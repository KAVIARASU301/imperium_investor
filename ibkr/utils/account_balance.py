"""Helpers for extracting displayable account balances from broker data."""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

DEFAULT_PAPER_BALANCE = 1_000_000.0

_IBKR_AVAILABLE_BALANCE_TAGS = (
    "AvailableFunds",
    "FullAvailableFunds",
    "ExcessLiquidity",
    "FullExcessLiquidity",
    "CashBalance",
    "TotalCashValue",
    "SettledCash",
    "BuyingPower",
    "NetLiquidation",
)

_FLAT_AVAILABLE_BALANCE_KEYS = (
    "available_balance",
    "available_funds",
    "availableFunds",
    "buying_power",
    "buyingPower",
    "net_liquidation",
    "netLiquidation",
    "cash",
    "cash_balance",
    "cashBalance",
)

_KITE_AVAILABLE_BALANCE_PATHS = (
    ("equity", "available", "live_balance"),
    ("equity", "available", "cash"),
    ("equity", "net"),
)

_IBKR_SUMMARY_TAGS = ",".join(_IBKR_AVAILABLE_BALANCE_TAGS)


def _finite_float(value: Any) -> Optional[float]:
    """Return ``value`` as a finite float, or ``None`` when it is unavailable."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _nested_get(data: Mapping[str, Any], path: Iterable[str]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _ibkr_summary_value(item: Any) -> Optional[float]:
    """Extract the numeric value from one IBKR account summary row/value."""
    if isinstance(item, Mapping):
        for key in ("value", "amount"):
            number = _finite_float(item.get(key))
            if number is not None:
                return number
    return _finite_float(item)


def _extract_flat_available_balance(*containers: Mapping[str, Any]) -> Optional[float]:
    """Return a broker-normalized flat cash/buying-power value if present."""
    for container in containers:
        if not isinstance(container, Mapping):
            continue
        for key in _FLAT_AVAILABLE_BALANCE_KEYS:
            number = _finite_float(container.get(key))
            if number is not None:
                return number
    return None


def _iter_cached_ibkr_account_summary_sources(
    profile: Mapping[str, Any],
    margins: Mapping[str, Any],
) -> List[Any]:
    sources: List[Any] = []
    for container in (profile, margins):
        if isinstance(container, Mapping):
            for key in ("account_summary", "accountSummary", "summary"):
                summary = container.get(key)
                if summary:
                    sources.append(summary)
    return sources


def _call_first_available(method: Any, argument_sets: Sequence[tuple[Any, ...]]) -> Any:
    for args in argument_sets:
        try:
            return method(*args)
        except TypeError:
            continue
    return None


def _iter_live_ibkr_account_summary_sources(trader: Any) -> List[Any]:
    sources: List[Any] = []

    for attr_name, argument_sets in (
        ("get_account_summary", ((),)),
        ("accountSummary", ((),)),
        ("accountValues", ((),)),
        ("reqAccountSummary", (("", _IBKR_SUMMARY_TAGS), ("All", _IBKR_SUMMARY_TAGS), ())),
        ("account_summary", ((),)),
    ):
        attr = getattr(trader, attr_name, None)
        if not callable(attr):
            continue
        try:
            summary = _call_first_available(attr, argument_sets)
        except Exception as exc:
            logger.warning("Unable to fetch IBKR account summary from %s: %s", attr_name, exc)
            continue
        if summary:
            sources.append(summary)
            break
    return sources


def _extract_from_ibkr_summary(summary: Any) -> Optional[float]:
    """Return IBKR available funds from dict or ib_insync AccountValue rows."""
    if isinstance(summary, Mapping):
        for tag in _IBKR_AVAILABLE_BALANCE_TAGS:
            if tag in summary:
                number = _ibkr_summary_value(summary.get(tag))
                if number is not None:
                    return number
        return None

    rows = (
        list(summary or [])
        if isinstance(summary, Iterable) and not isinstance(summary, (str, bytes))
        else []
    )
    by_tag: Dict[str, List[Any]] = {}
    for row in rows:
        tag = str(getattr(row, "tag", "") or "").strip()
        if tag:
            by_tag.setdefault(tag, []).append(row)
    for tag in _IBKR_AVAILABLE_BALANCE_TAGS:
        for row in by_tag.get(tag, []):
            number = _ibkr_summary_value(getattr(row, "value", None))
            if number is not None:
                return number
    return None


def extract_available_balance_from_data(
    trader: Any,
    profile: Dict[str, Any],
    margins: Dict[str, Any],
    *,
    default: float = DEFAULT_PAPER_BALANCE,
) -> float:
    """Extract the best available account balance from Kite/Paper/IBKR data.

    The IBKR header toolbar receives profile data whose real cash availability
    lives in ``account_summary`` (for example ``AvailableFunds``).  If those tags
    are ignored, the display falls through to the paper-trading default of
    1,000,000.  Prefer explicit broker summary and margin values before demo
    defaults.
    """
    profile = profile or {}
    margins = margins or {}

    for path in _KITE_AVAILABLE_BALANCE_PATHS:
        number = _finite_float(_nested_get(margins, path))
        if number is not None:
            return number

    number = _extract_flat_available_balance(margins, profile)
    if number is not None:
        return number

    for summary in _iter_cached_ibkr_account_summary_sources(profile, margins):
        number = _extract_from_ibkr_summary(summary)
        if number is not None:
            return number

    for summary in _iter_live_ibkr_account_summary_sources(trader):
        number = _extract_from_ibkr_summary(summary)
        if number is not None:
            return number

    for val in (
        profile.get("current_balance"),
        profile.get("balance"),
        getattr(trader, "current_balance", None),
        getattr(trader, "balance", None),
        getattr(trader, "initial_balance", default),
    ):
        number = _finite_float(val)
        if number is not None:
            return number

    return float(default)
