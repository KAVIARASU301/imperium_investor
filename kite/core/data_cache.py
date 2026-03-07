# kite/core/data_cache.py
"""
MarketAwareDataCache — Replaces the naive TTL-only DataCache.

Problems with old TTLCache(maxsize=100, ttl=300):
  1. A 5-minute candle fetched at 9:14 AM (before open) stays valid until 9:19 AM.
     The first live bar at 9:15 AM is completely missed.
  2. Same 5-min TTL used for BOTH minute-level data (stale in seconds)
     AND daily data (fine to cache for hours).
  3. Cache survives midnight — you get yesterday's EOD prices at next session open.
  4. No awareness of market holidays or weekends.

Fix:
  - Interval-appropriate TTLs
  - Automatic invalidation at market open (9:15 AM IST)
  - Date-stamp check — any entry from a previous calendar date is always stale
  - Thread-safe via RLock (same as original)
"""

import logging
import threading
from datetime import datetime, time, date, timedelta
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass, field

import pandas as pd
from PySide6.QtCore import QTimer, QObject

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# MARKET SCHEDULE (IST)
# ─────────────────────────────────────────────────────────────────────────────

MARKET_OPEN  = time(9, 15)   # NSE equity market opens
MARKET_CLOSE = time(15, 30)  # NSE equity market closes

# Interval → max cache TTL in seconds
# Intraday data is short-lived; daily/weekly data can be cached longer.
INTERVAL_TTL: Dict[str, int] = {
    "minute":    60,      #  1-min bars → 60s TTL
    "3minute":   180,
    "5minute":   300,
    "10minute":  600,
    "15minute":  900,
    "30minute":  1_800,
    "60minute":  3_600,
    "day":       3_600,   # Daily bars → 1h (updated once/day anyway)
    "week":      86_400,  # Weekly bars → 24h
    "month":     86_400,
}

DEFAULT_TTL = 300  # fallback if interval not found


# ─────────────────────────────────────────────────────────────────────────────
# CACHE ENTRY
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CacheEntry:
    data: pd.DataFrame
    created_at: datetime = field(default_factory=datetime.now)
    created_date: date = field(default_factory=date.today)
    interval: str = "day"

    def is_stale(self, ttl: int) -> bool:
        elapsed = (datetime.now() - self.created_at).total_seconds()
        return elapsed >= ttl

    def is_from_previous_session(self) -> bool:
        """True if this entry was created on a different calendar date."""
        return self.created_date < date.today()

    def is_pre_market_data(self) -> bool:
        """
        True if data was fetched before today's market open.
        Intraday entries fetched before 9:15 AM IST may be missing today's bars.
        """
        now = datetime.now()
        # Only relevant for intraday intervals on today's date
        if self.created_date != date.today():
            return False
        if _is_intraday(self.interval):
            return self.created_at.time() < MARKET_OPEN
        return False


def _is_intraday(interval: str) -> bool:
    return interval not in ("day", "week", "month")


def _is_market_hours() -> bool:
    now = datetime.now().time()
    return MARKET_OPEN <= now <= MARKET_CLOSE


def _ttl_for(interval: str) -> int:
    return INTERVAL_TTL.get(interval, DEFAULT_TTL)


# ─────────────────────────────────────────────────────────────────────────────
# CACHE
# ─────────────────────────────────────────────────────────────────────────────

class MarketAwareDataCache(QObject):
    """
    Thread-safe chart data cache that understands Indian market hours.

    Key behaviours:
      • Different TTLs per timeframe (minute bars expire quickly, daily bars linger).
      • Any entry from a previous calendar date is immediately stale.
      • Intraday entries fetched before 9:15 AM are stale once market opens.
      • Entire cache is flushed at market open each trading day.
      • get() / set() / invalidate() / clear() API (same as original DataCache).
    """

    def __init__(self, maxsize: int = 150, parent=None):
        super().__init__(parent)
        self._store: Dict[str, CacheEntry] = {}
        self._lock = threading.RLock()
        self._maxsize = maxsize
        self._last_flush_date: Optional[date] = None

        # Schedule flush at market open
        self._open_flush_timer = QTimer(self)
        self._open_flush_timer.timeout.connect(self._check_market_open_flush)
        self._open_flush_timer.start(60_000)  # check every minute

        logger.info("MarketAwareDataCache initialised")

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

            # Stale checks (order matters — cheapest first)
            if entry.is_from_previous_session():
                logger.debug(f"Cache miss (prev session): {key}")
                del self._store[key]
                return None

            if entry.is_pre_market_data() and _is_market_hours():
                logger.debug(f"Cache miss (pre-market data, market now open): {key}")
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
            # Evict LRU if at capacity
            if len(self._store) >= self._maxsize and key not in self._store:
                self._evict_one()

            self._store[key] = CacheEntry(
                data=data.copy(),
                created_at=datetime.now(),
                created_date=date.today(),
                interval=interval,
            )
            logger.debug(f"Cache set: {key} ({len(data)} rows, interval={interval})")

    def invalidate(self, symbol: str, interval: Optional[str] = None) -> int:
        """
        Invalidate all cache entries for a symbol.
        If interval is given, only that interval is removed.
        Returns number of entries removed.
        """
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
        """Flush the entire cache."""
        with self._lock:
            count = len(self._store)
            self._store.clear()
        logger.info(f"Cache cleared ({count} entries)")

    def stats(self) -> Dict[str, Any]:
        """Return cache statistics for debugging."""
        with self._lock:
            return {
                "entries":   len(self._store),
                "capacity":  self._maxsize,
                "keys":      list(self._store.keys()),
            }

    # ──────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────

    def _check_market_open_flush(self) -> None:
        """
        Called every minute. On the first tick at/after 9:15 AM IST on a new
        trading date, flush all intraday cache entries so fresh bars are fetched.
        """
        today = date.today()
        now   = datetime.now().time()

        # Only act once per trading day
        if self._last_flush_date == today:
            return

        # Is it a weekday (Mon–Fri) and past 9:15?
        if datetime.today().weekday() < 5 and now >= MARKET_OPEN:
            self._flush_intraday()
            self._last_flush_date = today
            logger.info("Market-open flush complete — all intraday cache entries cleared")

    def _flush_intraday(self) -> None:
        """Remove all intraday cache entries (minute → 60min)."""
        with self._lock:
            to_del = [
                k for k, v in self._store.items()
                if _is_intraday(v.interval)
            ]
            for k in to_del:
                del self._store[k]
        logger.debug(f"Flushed {len(to_del)} intraday cache entries")

    def _evict_one(self) -> None:
        """Evict the oldest entry (simple LRU approximation)."""
        if not self._store:
            return
        oldest_key = min(self._store, key=lambda k: self._store[k].created_at)
        del self._store[oldest_key]
        logger.debug(f"Cache evicted (LRU): {oldest_key}")
