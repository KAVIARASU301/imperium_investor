# ibkr/core/data_cache.py
"""
MarketAwareDataCache — thread-safe chart cache tuned for IBKR / US equities.

Why this version is faster and safer for IBKR mode:
  • Uses America/New_York market time instead of IST.
  • Keeps daily/weekly/monthly bars across calendar rollovers until TTL expires.
  • Flushes only intraday bars at the US market open.
  • Uses a monotonic access counter so max-size eviction is true LRU.
  • Keeps a tiny lock-free hot cache for repeated chart renders.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, time, date, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import pandas as pd
from PySide6.QtCore import QObject, QTimer

logger = logging.getLogger(__name__)

try:
    _MARKET_TZ = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - very old Python/tzdata fallback
    _MARKET_TZ = timezone(timedelta(hours=-5))

MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

# Short intraday TTLs keep live charts fresh; longer swing-timeframe TTLs avoid
# repeated IBKR historical requests, which are pacing-sensitive.
INTERVAL_TTL: Dict[str, int] = {
    "minute": 30,
    "3minute": 60,
    "5minute": 90,
    "10minute": 180,
    "15minute": 240,
    "30minute": 420,
    "60minute": 600,
    "day": 12 * 60 * 60,
    "week": 24 * 60 * 60,
    "month": 24 * 60 * 60,
}
DEFAULT_TTL = 300
_INTRADAY_INTERVALS = {"minute", "3minute", "5minute", "10minute", "15minute", "30minute", "60minute"}


def _now_market() -> datetime:
    return datetime.now(tz=_MARKET_TZ)


def _today_market() -> date:
    return _now_market().date()


def _time_market() -> time:
    return _now_market().time()


# Backward-compatible aliases used by a few older call-sites/tests.  They now
# deliberately mean “market timezone”, not India time, because this file lives
# under ibkr/core in the IBKR build.
def _now_ist() -> datetime:  # pragma: no cover - compatibility alias
    return _now_market()


def _today_ist() -> date:  # pragma: no cover - compatibility alias
    return _today_market()


def _time_ist() -> time:  # pragma: no cover - compatibility alias
    return _time_market()


def _is_intraday(interval: str) -> bool:
    return str(interval or "day") in _INTRADAY_INTERVALS


def _is_market_hours() -> bool:
    now = _now_market()
    return now.weekday() < 5 and MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _ttl_for(interval: str) -> int:
    return INTERVAL_TTL.get(str(interval or "day"), DEFAULT_TTL)


@dataclass
class CacheEntry:
    data: pd.DataFrame
    interval: str = "day"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_market_date: date = field(default_factory=_today_market)
    last_access_seq: int = 0

    def is_stale(self, ttl: int) -> bool:
        now = datetime.now(self.created_at.tzinfo or timezone.utc)
        return (now - self.created_at).total_seconds() >= ttl

    def is_previous_intraday_session(self) -> bool:
        return _is_intraday(self.interval) and self.created_market_date < _today_market()

    def is_premarket_intraday(self) -> bool:
        if not _is_intraday(self.interval):
            return False
        if self.created_market_date != _today_market():
            return False
        created_market_time = self.created_at.astimezone(_MARKET_TZ).time()
        return created_market_time < MARKET_OPEN


class MarketAwareDataCache(QObject):
    """Small LRU dataframe cache for chart historical requests."""

    def __init__(self, maxsize: int = 300, parent=None):
        super().__init__(parent)
        self._store: Dict[str, CacheEntry] = {}
        self._lock = threading.RLock()
        self._maxsize = max(1, int(maxsize))
        self._last_flush_date: Optional[date] = None
        self._access_seq = 0
        # Hot path for repeated renders of the same recently viewed symbols.
        # Values are shallow copies from the backing cache so callers avoid the
        # lock and copy overhead on immediate repeat lookups.
        self._hot_cache: "OrderedDict[str, pd.DataFrame]" = OrderedDict()
        self._hot_cache_max = 5

        self._open_flush_timer = QTimer(self)
        self._open_flush_timer.timeout.connect(self._check_market_open_flush)
        self._open_flush_timer.start(60_000)

        logger.info("MarketAwareDataCache initialised for IBKR/US market time")

    def get(self, key: str) -> Optional[pd.DataFrame]:
        if key in self._hot_cache:
            self._hot_cache.move_to_end(key)
            return self._hot_cache[key]

        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None

            if entry.is_previous_intraday_session():
                self._store.pop(key, None)
                self._hot_cache.pop(key, None)
                logger.debug("Cache miss previous US intraday session: %s", key)
                return None

            if entry.is_premarket_intraday() and _is_market_hours():
                self._store.pop(key, None)
                self._hot_cache.pop(key, None)
                logger.debug("Cache miss premarket intraday after open: %s", key)
                return None

            ttl = _ttl_for(entry.interval)
            if entry.is_stale(ttl):
                self._store.pop(key, None)
                self._hot_cache.pop(key, None)
                logger.debug("Cache miss TTL expired (%ss): %s", ttl, key)
                return None

            self._access_seq += 1
            entry.last_access_seq = self._access_seq
            df = entry.data.copy(deep=False)

        self._hot_cache[key] = df
        self._hot_cache.move_to_end(key)
        if len(self._hot_cache) > self._hot_cache_max:
            self._hot_cache.popitem(last=False)
        return df

    def set(self, key: str, data: pd.DataFrame, interval: str = "day") -> None:
        if data is None or data.empty:
            return

        self._hot_cache.pop(key, None)
        with self._lock:
            if len(self._store) >= self._maxsize and key not in self._store:
                self._evict_one()
            self._access_seq += 1
            self._store[key] = CacheEntry(
                data=data.copy(deep=False),
                interval=str(interval or "day"),
                created_at=datetime.now(timezone.utc),
                created_market_date=_today_market(),
                last_access_seq=self._access_seq,
            )

    def invalidate(self, symbol: str, interval: Optional[str] = None) -> int:
        symbol = str(symbol or "").strip().upper()
        if interval:
            for key in (f"{symbol}_{interval}", f"{symbol}:{interval}"):
                self._hot_cache.pop(key, None)
        else:
            prefixes = (f"{symbol}_", f"{symbol}:")
            hot_keys = [key for key in self._hot_cache if key.upper().startswith(prefixes)]
            for key in hot_keys:
                self._hot_cache.pop(key, None)

        with self._lock:
            if interval:
                candidates = [f"{symbol}_{interval}", f"{symbol}:{interval}"]
                removed = 0
                for key in candidates:
                    removed += 1 if self._store.pop(key, None) is not None else 0
                return removed

            prefixes = (f"{symbol}_", f"{symbol}:")
            keys = [k for k in self._store if k.upper().startswith(prefixes)]
            for key in keys:
                self._store.pop(key, None)
            return len(keys)

    def clear(self) -> None:
        self._hot_cache.clear()
        with self._lock:
            count = len(self._store)
            self._store.clear()
        logger.info("Chart data cache cleared (%d entries)", count)

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "entries": len(self._store),
                "capacity": self._maxsize,
                "hot_entries": len(self._hot_cache),
                "hot_capacity": self._hot_cache_max,
                "keys": list(self._store.keys()),
                "hot_keys": list(self._hot_cache.keys()),
                "market_tz": str(_MARKET_TZ),
                "market_now": _now_market().isoformat(),
                "market_date": str(_today_market()),
            }

    def _check_market_open_flush(self) -> None:
        today = _today_market()
        if self._last_flush_date == today:
            return
        now = _now_market()
        if now.weekday() < 5 and now.time() >= MARKET_OPEN:
            self._flush_intraday()
            self._last_flush_date = today
            logger.info("US market-open cache flush complete; intraday entries cleared")

    def _flush_intraday(self) -> None:
        with self._lock:
            keys = [key for key, entry in self._store.items() if _is_intraday(entry.interval)]
            for key in keys:
                self._store.pop(key, None)
                self._hot_cache.pop(key, None)
        if keys:
            logger.debug("Flushed %d intraday cache entries", len(keys))

    def _evict_one(self) -> None:
        if not self._store:
            return
        victim = min(self._store, key=lambda k: self._store[k].last_access_seq)
        self._store.pop(victim, None)
        self._hot_cache.pop(victim, None)
        logger.debug("Cache evicted LRU entry: %s", victim)

    @property
    def _cache(self):  # compatibility with older chart refresh code
        return self._store
