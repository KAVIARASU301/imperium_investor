"""Helpers for normalizing IBKR price fields.

ib_insync/IBKR uses a very large floating-point sentinel for unset order
prices (for example ``Order.auxPrice`` on a plain limit order).  Treat those
sentinels as missing values before rendering or reusing prices.
"""

from __future__ import annotations

import math
from typing import Any

# IBKR's unset double sentinel is approximately sys.float_info.max.  Use a
# lower threshold so stringified/rounded variants are also caught.
IBKR_UNSET_PRICE_THRESHOLD = 1e100


def is_ibkr_unset_price(value: Any) -> bool:
    """Return True when *value* is IBKR's unset/invalid price sentinel."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return not math.isfinite(number) or abs(number) >= IBKR_UNSET_PRICE_THRESHOLD


def safe_ibkr_price(value: Any, default: float = 0.0) -> float:
    """Convert an IBKR price-like value while mapping unset sentinels to default."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number) or abs(number) >= IBKR_UNSET_PRICE_THRESHOLD:
        return default
    return number


def first_positive_ibkr_price(*values: Any) -> float:
    """Return the first positive, non-sentinel IBKR price from *values*."""
    for value in values:
        number = safe_ibkr_price(value, 0.0)
        if number > 0:
            return number
    return 0.0
