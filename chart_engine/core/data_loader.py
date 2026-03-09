# chart_engine/core/data_loader.py
#
# Three responsibilities:
#   1. DataFetcher      — thin wrapper around KiteConnect historical_data()
#   2. DataCache        — TTL-based in-memory cache so we don't hammer the API
#   3. ChartDataLoaderThread — background QThread that fetches + processes data
#                              and emits signals when done or on error/progress
#
# The thread emits:
#   data_loaded(DataFrame, str)   — (processed df, cache_key)
#   load_error(str)               — human-readable error message
#   load_progress(int)            — 0–100 progress value

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
from cachetools import TTLCache
from kiteconnect import KiteConnect
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

# ─── Date range config per interval ───────────────────────────────────────────
# How many calendar days back to fetch for each timeframe.
_DAYS_BACK: Dict[str, int] = {
    # Daily charts are commonly used for multi-year backtests, so request a
    # much deeper window than the default 1-year lookback.
    "day": 2000,  # ~25 years
    "week": 2000,
    "month": 2000,
    "60minute": 90,
    "30minute": 60,
    "15minute": 45,
    "10minute": 21,
    "5minute": 14,
    "3minute": 10,
    "minute": 5,
}


# ─── DataFetcher ──────────────────────────────────────────────────────────────

class DataFetcher:
    """
    Thin wrapper around KiteConnect.historical_data().
    Decoupled so IBKR can swap in its own fetcher later.
    """

    def __init__(self, kite_client: KiteConnect):
        self.kite = kite_client

    def fetch(self, instrument_token: int, from_date, to_date, interval: str) -> List[Dict]:
        try:
            return self.kite.historical_data(
                instrument_token=instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval=interval,
            )
        except Exception as exc:
            logger.error("DataFetcher.fetch error: %s", exc)
            raise


# ─── DataCache ────────────────────────────────────────────────────────────────

class DataCache:
    """
    TTL in-memory cache for DataFrames keyed by 'SYMBOL_interval'.
    Default TTL = 5 minutes (300 s) with room for 50 entries.
    """

    def __init__(self, maxsize: int = 50, ttl: int = 300):
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock = __import__("threading").Lock()

    def get(self, key: str) -> Optional[pd.DataFrame]:
        with self._lock:
            return self._cache.get(key)

    def set(self, key: str, df: pd.DataFrame) -> None:
        with self._lock:
            self._cache[key] = df

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


# ─── ChartDataLoaderThread ────────────────────────────────────────────────────

class ChartDataLoaderThread(QThread):
    """
    Background thread: fetch → process → cache → emit.
    Cancelled cleanly via stop(); stale results are discarded by the caller.
    """

    data_loaded = Signal(object, str)   # (DataFrame, cache_key)
    load_error = Signal(str)            # error message
    load_progress = Signal(int)         # 0-100

    def __init__(
        self,
        data_fetcher: DataFetcher,
        cache: DataCache,
        symbol: str,
        instrument_token: int,
        interval: str,
        force_refresh: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.data_fetcher = data_fetcher
        self.cache = cache
        self.symbol = symbol
        self.instrument_token = instrument_token
        self.interval = interval
        self.force_refresh = force_refresh
        self._stop_requested = False
        self.cache_key = f"{symbol}_{interval}"

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        try:
            self.load_progress.emit(10)

            # ── Cache hit ──
            if not self.force_refresh:
                cached = self.cache.get(self.cache_key)
                if cached is not None and not cached.empty:
                    self.load_progress.emit(100)
                    self.data_loaded.emit(cached, self.cache_key)
                    return

            if self._stop_requested:
                return

            # ── Build date range ──
            to_date = datetime.now()
            days_back = _DAYS_BACK.get(self.interval, 365)
            from_date = to_date - timedelta(days=days_back)
            self.load_progress.emit(30)

            if self._stop_requested:
                return

            # ── Fetch raw data ──
            raw = self.data_fetcher.fetch(
                instrument_token=self.instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval=self.interval,
            )

            if self._stop_requested:
                return

            self.load_progress.emit(60)

            if not raw:
                self.load_error.emit(f"No data available for {self.symbol}")
                return

            # ── Process ──
            df = self._process(raw)
            if df.empty:
                self.load_error.emit(f"No valid data for {self.symbol}")
                return

            if self._stop_requested:
                return

            self.load_progress.emit(90)
            self.cache.set(self.cache_key, df)
            self.load_progress.emit(100)
            self.data_loaded.emit(df, self.cache_key)

        except Exception as exc:
            if not self._stop_requested:
                logger.error("Loader error for %s: %s", self.symbol, exc, exc_info=True)
                self.load_error.emit(f"Failed to load data: {exc}")

    def _process(self, raw_data: List[Dict]) -> pd.DataFrame:
        """Convert raw KiteConnect list-of-dicts into a clean DataFrame."""
        df = pd.DataFrame(raw_data)
        if df.empty:
            return df

        required = {"date", "open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Missing columns: {missing}")

        df["date"] = pd.to_datetime(df["date"])
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna()
        df = df.drop_duplicates(subset="date").sort_values("date")
        df = df.rename(columns={"date": "time"})
        df["symbol"] = self.symbol
        return df
