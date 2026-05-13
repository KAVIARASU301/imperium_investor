# kite/core/data_cache.py
"""
MarketAwareDataCache — Replaces the naive TTL-only DataCache.

KEY FIX: All date comparisons now use IST (Asia/Kolkata = UTC+5:30) instead of
the system's local time or UTC. On Linux servers, `date.today()` returns UTC,
which causes cache entries created at e.g. 14:00 IST on May 13 to be wrongly
treated as "today" after 00:00 IST May 14 (when UTC is still May 13 18:30).

This also prevents:
  - Missing previous-day daily candle after midnight IST
  - Cache not flushing at market open (09:15 IST) correctly
  - Pre-market data served as current after market opens
"""

import logging
import threading
from datetime import datetime, time, date, timedelta, timezone
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass, field

import pandas as pd
from PySide6.QtCore import QTimer, QObject

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# IST HELPERS  (single source of truth for timezone-aware comparisons)
# ─────────────────────────────────────────────────────────────────────────────

_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist() -> datetime:
    """Return current datetime in IST."""
    return datetime.now(tz=_IST)


def _today_ist() -> date:
    """Return today's date in IST, not UTC or system local time."""
    return _now_ist().date()


def _time_ist() -> time:
    """Return current time-of-day in IST."""
    return _now_ist().time()


# ─────────────────────────────────────────────────────────────────────────────
# MARKET SCHEDULE (IST)
# ─────────────────────────────────────────────────────────────────────────────

MARKET_OPEN  = time(9, 15)    # NSE equity market opens
MARKET_CLOSE = time(15, 30)   # NSE equity market closes

# Interval → max cache TTL in seconds
INTERVAL_TTL: Dict[str, int] = {
    "minute":    60,
    "3minute":   180,
    "5minute":   300,
    "10minute":  600,
    "15minute":  900,
    "30minute":  1_800,
    "60minute":  3_600,
    "day":       3_600,   # Daily bars → 1h
    "week":      86_400,  # Weekly bars → 24h
    "month":     86_400,
}

DEFAULT_TTL = 300


# ─────────────────────────────────────────────────────────────────────────────
# CACHE ENTRY
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CacheEntry:
    data: pd.DataFrame
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # FIXED: store as IST date, not UTC date
    created_date: date = field(default_factory=_today_ist)
    interval: str = "day"

    def is_stale(self, ttl: int) -> bool:
        now = datetime.now(self.created_at.tzinfo) if self.created_at.tzinfo is not None else datetime.now()
        elapsed = (now - self.created_at).total_seconds()
        return elapsed >= ttl

    def is_from_previous_session(self) -> bool:
        """
        True if this entry was created on a different IST calendar date.

        FIXED: Uses IST date, not UTC. Before this fix, on Linux systems
        date.today() returned UTC, so between 00:00 and 05:30 IST, entries
        from the previous IST day were NOT detected as stale (UTC date
        hadn't changed yet).
        """
        return self.created_date < _today_ist()

    def is_pre_market_data(self) -> bool:
        """
        True if data was fetched before today's market open (09:15 IST).
        Intraday entries fetched before 09:15 IST may be missing today's bars.

        FIXED: Compares times in IST, not system local time.
        """
        today_ist = _today_ist()
        if self.created_date != today_ist:
            return False
        if _is_intraday(self.interval):
            # created_at may be naive (system local). Convert via UTC round-trip.
            try:
                # Prefer timezone-aware path
                if self.created_at.tzinfo is not None:
                    created_ist_time = self.created_at.astimezone(_IST).time()
                else:
                    # Naive datetime: assume it's UTC (Linux default), shift to IST
                    created_utc = self.created_at.replace(tzinfo=timezone.utc)
                    created_ist_time = created_utc.astimezone(_IST).time()
            except Exception:
                # Last resort fallback: add IST offset to naive datetime
                created_ist_time = (self.created_at + timedelta(hours=5, minutes=30)).time()
            return created_ist_time < MARKET_OPEN
        return False


def _is_intraday(interval: str) -> bool:
    return interval not in ("day", "week", "month")


def _is_market_hours() -> bool:
    """Check if NSE market is currently open (IST-aware)."""
    now_ist = _time_ist()
    return MARKET_OPEN <= now_ist <= MARKET_CLOSE


def _ttl_for(interval: str) -> int:
    return INTERVAL_TTL.get(interval, DEFAULT_TTL)


# ─────────────────────────────────────────────────────────────────────────────
# CACHE
# ─────────────────────────────────────────────────────────────────────────────

class MarketAwareDataCache(QObject):
    """
    Thread-safe chart data cache that understands Indian market hours (IST).

    Key behaviours:
      • Different TTLs per timeframe.
      • Any entry from a previous IST calendar date is immediately stale.
      • Intraday entries fetched before 09:15 IST are stale once market opens.
      • Entire intraday cache is flushed at market open each trading day.
      • All date/time comparisons use IST, never UTC or system local time.
    """

    def __init__(self, maxsize: int = 150, parent=None):
        super().__init__(parent)
        self._store: Dict[str, CacheEntry] = {}
        self._lock = threading.RLock()
        self._maxsize = maxsize
        self._last_flush_date: Optional[date] = None

        # Schedule flush at market open — checked every minute
        self._open_flush_timer = QTimer(self)
        self._open_flush_timer.timeout.connect(self._check_market_open_flush)
        self._open_flush_timer.start(60_000)

        logger.info("MarketAwareDataCache initialised (IST-aware)")

    # ──────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[pd.DataFrame]:
        """
        Return cached DataFrame or None if missing/stale.
        key format: "{symbol}_{interval}"
        """
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None

            interval = entry.interval
            ttl      = _ttl_for(interval)

            # Stale checks (cheapest first)
            if entry.is_from_previous_session():
                logger.debug(f"Cache miss (prev IST session): {key}")
                del self._store[key]
                return None

            if entry.is_pre_market_data() and _is_market_hours():
                logger.debug(f"Cache miss (pre-market data, market now open IST): {key}")
                del self._store[key]
                return None

            if entry.is_stale(ttl):
                logger.debug(f"Cache miss (TTL expired, {ttl}s): {key}")
                del self._store[key]
                return None

            logger.debug(f"Cache hit: {key}")
            return entry.data.copy()

    def set(self, key: str, data: pd.DataFrame, interval: str = "day") -> None:
        """Cache a DataFrame. key format: "{symbol}_{interval}"."""
        if data is None or data.empty:
            return

        with self._lock:
            if len(self._store) >= self._maxsize and key not in self._store:
                self._evict_one()

            self._store[key] = CacheEntry(
                data=data.copy(),
                created_at=datetime.now(timezone.utc),
                created_date=_today_ist(),   # FIXED: IST date, not UTC
                interval=interval,
            )
            logger.debug(f"Cache set: {key} ({len(data)} rows, interval={interval}, "
                         f"ist_date={_today_ist()})")

    def invalidate(self, symbol: str, interval: Optional[str] = None) -> int:
        with self._lock:
            if interval:
                key = f"{symbol}_{interval}"
                removed = 1 if self._store.pop(key, None) is not None else 0
            else:
                prefix  = f"{symbol}_"
                to_del  = [k for k in self._store if k.startswith(prefix)]
                for k in to_del:
                    del self._store[k]
                removed = len(to_del)

        if removed:
            logger.debug(f"Cache invalidated {removed} entries for {symbol}")
        return removed

    def clear(self) -> None:
        with self._lock:
            count = len(self._store)
            self._store.clear()
        logger.info(f"Cache cleared ({count} entries)")

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "entries":        len(self._store),
                "capacity":       self._maxsize,
                "keys":           list(self._store.keys()),
                "current_ist":    str(_now_ist()),
                "today_ist":      str(_today_ist()),
            }

    # ──────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────

    def _check_market_open_flush(self) -> None:
        """
        Called every minute. On the first tick at/after 09:15 IST on a new
        trading date, flush all intraday cache entries so fresh bars are fetched.

        FIXED: Uses IST date and time, not system local or UTC.
        """
        today_ist   = _today_ist()
        now_ist_time = _time_ist()

        # Only act once per trading day
        if self._last_flush_date == today_ist:
            return

        # IST weekday (Mon=0 ... Fri=4) and past 09:15 IST
        weekday = _now_ist().weekday()
        if weekday < 5 and now_ist_time >= MARKET_OPEN:
            self._flush_intraday()
            self._last_flush_date = today_ist
            logger.info(
                f"Market-open flush complete (IST date={today_ist}, "
                f"IST time={now_ist_time}) — all intraday cache entries cleared"
            )

    def _flush_intraday(self) -> None:
        with self._lock:
            to_del = [
                k for k, v in self._store.items()
                if _is_intraday(v.interval)
            ]
            for k in to_del:
                del self._store[k]
        logger.debug(f"Flushed {len(to_del)} intraday cache entries")

    def _evict_one(self) -> None:
        if not self._store:
            return
        oldest_key = min(self._store, key=lambda k: self._store[k].created_at)
        del self._store[oldest_key]
        logger.debug(f"Cache evicted (LRU): {oldest_key}")