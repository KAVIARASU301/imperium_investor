# kite/core/data_cache.py
"""
MarketAwareDataCache — IST-aware chart data cache.

KEY FIXES vs original:
  1. _effective_to_date is removed — the cache no longer needs to know about
     query boundaries. That logic lives in data_loader.py exclusively.
  2. is_pre_market_data() no longer evicts DAILY interval cache entries.
     A daily candle fetched at 23:00 IST is still valid at 00:01 IST the
     next morning — the IST calendar date has not changed. Only evict when
     created_date < _today_ist() (the IST date rolled over).
  3. The market-open flush only clears INTRADAY entries, never daily/weekly.
  4. All time comparisons use IST exclusively — never UTC, never local time.
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
    "day":       7_200,   # Daily bars → 2 hours (longer TTL, data rarely changes)
    "week":      86_400,  # Weekly bars → 24 hours
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
    # Store as IST date, not UTC date
    created_date: date = field(default_factory=_today_ist)
    interval: str = "day"

    def is_stale(self, ttl: int) -> bool:
        now = datetime.now(self.created_at.tzinfo) if self.created_at.tzinfo is not None else datetime.now()
        elapsed = (now - self.created_at).total_seconds()
        return elapsed >= ttl

    def is_from_previous_session(self) -> bool:
        """
        True if this entry was created on a different IST calendar date.

        Uses IST date exclusively. Between 00:00 and 05:30 IST, UTC date has
        already rolled over but IST hasn't — this prevents false positives.
        """
        return self.created_date < _today_ist()

    def is_pre_market_data(self) -> bool:
        """
        FIXED: Only evict intraday data fetched before today's market open.
        NEVER evict daily/weekly/monthly data based on time-of-day.

        Rationale: a daily candle for May 13 is valid at any time on May 13
        or May 14 (until the IST date rolls to May 15). The old code evicted
        daily cache entries between midnight and 9:15 IST, causing the previous
        day's candle to vanish every night.
        """
        # Daily/weekly/monthly data is never considered "pre-market stale".
        # It stays valid until the IST calendar date changes (handled by
        # is_from_previous_session()) or the TTL expires.
        if not _is_intraday(self.interval):
            return False

        # For intraday: data fetched before today's market open may be missing
        # today's bars entirely. Mark it stale once market opens.
        today_ist = _today_ist()
        if self.created_date != today_ist:
            return False  # Not today's data — let is_from_previous_session handle it

        try:
            if self.created_at.tzinfo is not None:
                created_ist_time = self.created_at.astimezone(_IST).time()
            else:
                # Naive datetime: assume UTC, shift to IST
                created_utc = self.created_at.replace(tzinfo=timezone.utc)
                created_ist_time = created_utc.astimezone(_IST).time()
        except Exception:
            created_ist_time = (self.created_at + timedelta(hours=5, minutes=30)).time()

        return created_ist_time < MARKET_OPEN


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
      • INTRADAY entries fetched before 09:15 IST are stale once market opens.
      • Daily/weekly/monthly entries are NEVER evicted due to time-of-day.
      • Entire intraday cache is flushed at market open each trading day.
      • All date/time comparisons use IST exclusively.
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

        logger.info("MarketAwareDataCache initialised (IST-aware, daily-bars exempt from pre-market eviction)")

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

            # Check 1: Previous IST session (cheapest check)
            if entry.is_from_previous_session():
                logger.debug(f"Cache miss (prev IST session): {key}")
                del self._store[key]
                return None

            # Check 2: Pre-market intraday data (only for intraday intervals)
            if entry.is_pre_market_data() and _is_market_hours():
                logger.debug(f"Cache miss (pre-market intraday, market now open IST): {key}")
                del self._store[key]
                return None

            # Check 3: TTL expired
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
                created_date=_today_ist(),   # IST date, not UTC
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
        trading date, flush ONLY intraday cache entries so fresh intraday bars
        are fetched. Daily/weekly/monthly entries are NEVER flushed here.

        Uses IST date and time exclusively.
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
                f"IST time={now_ist_time}) — intraday cache entries cleared "
                f"(daily/weekly/monthly entries preserved)"
            )

    def _flush_intraday(self) -> None:
        """Flush only intraday cache entries. Never touches daily/weekly/monthly."""
        with self._lock:
            to_del = [
                k for k, v in self._store.items()
                if _is_intraday(v.interval)
            ]
            for k in to_del:
                del self._store[k]
        if to_del:
            logger.debug(f"Flushed {len(to_del)} intraday cache entries (daily preserved)")

    def _evict_one(self) -> None:
        if not self._store:
            return
        oldest_key = min(self._store, key=lambda k: self._store[k].created_at)
        del self._store[oldest_key]
        logger.debug(f"Cache evicted (LRU): {oldest_key}")

    # Backward-compat: chart_widget.py may access ._cache or ._store
    @property
    def _cache(self):
        return self._store