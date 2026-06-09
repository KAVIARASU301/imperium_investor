"""Helpers for extracting displayable account balances from broker data."""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

DEFAULT_PAPER_BALANCE = 1_000_000.0

IBKR_AVAILABLE_BALANCE_TAGS = (
    "TotalCashValue",
    "TotalCashBalance",
    "CashBalance",
    "AvailableFunds",
    "FullAvailableFunds",
    "ExcessLiquidity",
    "FullExcessLiquidity",
    "SettledCash",
    "BuyingPower",
    "NetLiquidation",
)

_FLAT_AVAILABLE_BALANCE_KEYS = (
    "available_balance",
    "total_cash_value",
    "totalCashValue",
    "total_cash_balance",
    "totalCashBalance",
    "cash",
    "cash_balance",
    "cashBalance",
    "available_funds",
    "availableFunds",
    "buying_power",
    "buyingPower",
    "net_liquidation",
    "netLiquidation",
)

_KITE_AVAILABLE_BALANCE_PATHS = (
    ("equity", "available", "live_balance"),
    ("equity", "available", "cash"),
    ("equity", "net"),
)

IBKR_SUMMARY_TAGS = ",".join(IBKR_AVAILABLE_BALANCE_TAGS)
_IBKR_AVAILABLE_BALANCE_TAGS = IBKR_AVAILABLE_BALANCE_TAGS
_IBKR_SUMMARY_TAGS = IBKR_SUMMARY_TAGS


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


def ibkr_summary_tag_matches(actual_tag: Any, expected_tag: str) -> bool:
    """Return True for exact IBKR summary tags and segment-suffixed variants.

    IBKR can publish multi-segment account values such as ``AvailableFunds-S``
    for the securities segment. Treat those as the corresponding base tag while
    avoiding loose prefix matches like ``FullAvailableFunds``.
    """
    tag = str(actual_tag or "").strip()
    return tag == expected_tag or tag.startswith(f"{expected_tag}-")


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
        ("reqAccountSummary", (("All", _IBKR_SUMMARY_TAGS), ("", _IBKR_SUMMARY_TAGS), ())),
        ("accountValues", ((),)),
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
    """Return IBKR display balance from dict or ib_insync AccountValue rows."""
    if isinstance(summary, Mapping):
        for tag in _IBKR_AVAILABLE_BALANCE_TAGS:
            for actual_tag, entry in summary.items():
                if not ibkr_summary_tag_matches(actual_tag, tag):
                    continue
                number = _ibkr_summary_value(entry)
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
        for actual_tag, tagged_rows in by_tag.items():
            if not ibkr_summary_tag_matches(actual_tag, tag):
                continue
            for row in tagged_rows:
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

    The IBKR header toolbar displays the same account balance IBKR publishes as
    ``TotalCashValue`` in account summary/API logs.  Prefer explicit broker
    summary and margin cash values before demo defaults.
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
