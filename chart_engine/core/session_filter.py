from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

INTRADAY_INTERVALS = {"minute", "3minute", "5minute", "10minute", "15minute", "30minute", "60minute"}
US_PREMARKET_OPEN_MINUTES = 4 * 60
US_RTH_OPEN_MINUTES = 9 * 60 + 30


def is_intraday_interval(interval: Any) -> bool:
    return str(interval or "").strip().lower() in INTRADAY_INTERVALS


def filter_ibkr_premarket_candles(
    df: pd.DataFrame,
    *,
    show_premarket_candles: bool,
    broker_name: Any,
    interval: Any,
) -> pd.DataFrame:
    """Return IBKR intraday chart data with premarket bars removed when disabled.

    IBKR historical intraday bars can arrive either as exchange-local naive
    datetimes or as timezone-aware instants.  Naive values are already the
    chart's America/New_York wall-clock values after the loader normalisation;
    aware values are converted to America/New_York before session filtering.
    """
    if (
        df is None
        or df.empty
        or bool(show_premarket_candles)
        or str(broker_name or "").strip().lower() != "ibkr"
        or not is_intraday_interval(interval)
        or "time" not in df.columns
    ):
        return df

    times = pd.to_datetime(df["time"], errors="coerce")
    if times.empty:
        return df

    try:
        if getattr(times.dt, "tz", None) is not None:
            exchange_times = times.dt.tz_convert(ZoneInfo("America/New_York")).dt.tz_localize(None)
        else:
            exchange_times = times
    except Exception as exc:
        logger.warning("Unable to filter IBKR premarket candles: %s", exc)
        return df

    minutes = exchange_times.dt.hour * 60 + exchange_times.dt.minute
    is_premarket = (minutes >= US_PREMARKET_OPEN_MINUTES) & (minutes < US_RTH_OPEN_MINUTES)
    keep_mask = times.notna() & ~is_premarket
    return df.loc[keep_mask].copy()
