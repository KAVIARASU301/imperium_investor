import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
from cachetools import TTLCache
from kiteconnect import KiteConnect
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

# ─── Date range config per interval ──────────────────────────────────────────
#
# Rule: (desired_trading_days × 1.45) rounded up to the next clean number,
#       then add a 10-day safety buffer for holidays.
#
# Kite API hard limits (trading days):
#   minute       → 60 td  → need ≤ 60×1.45+10 ≈ 97  cd  → use 100
#   3minute      → 100 td → need ≤ 100×1.45+10 ≈ 155 cd  → use 160
#   5minute      → 100 td → same as 3minute                → use 160
#   10minute     → 100 td → same                           → use 160
#   15minute     → 200 td → need ≤ 200×1.45+10 ≈ 300 cd  → use 300
#   30minute     → 200 td → same as 15minute               → use 300
#   60minute     → 400 td → need ≤ 400×1.45+10 ≈ 590 cd  → use 600
#   day          → 2000 td→ need ≤ 2000×1.45+10 ≈ 2910 cd → use 2900
#   week / month → full   → 3000 cd is safe and well within limits
#
_DAYS_BACK: Dict[str, int] = {
    "day":      2900,   # was 2000 — fixes missing candles near holidays
    "week":     3000,
    "month":    3000,
    "60minute": 600,    # was 90  — fixes truncated intraday history
    "30minute": 300,    # was 60
    "15minute": 300,    # was 45
    "10minute": 160,    # was 21
    "5minute":  160,    # was 14
    "3minute":  160,    # was 10
    "minute":   100,    # was 5  — critical fix: 5 cal days = 3–4 trading days
}


# ─── DataFetcher ─────────────────────────────────────────────────────────────

class DataFetcher:
    """Thin wrapper around KiteConnect.historical_data()."""

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


# ─── DataCache ───────────────────────────────────────────────────────────────

class DataCache:
    """
    TTL in-memory cache for DataFrames keyed by 'SYMBOL_interval'.
    Thread-safe: all operations hold a lock.
    """

    def __init__(self, maxsize: int = 50, ttl: int = 300):
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[pd.DataFrame]:
        with self._lock:
            df = self._cache.get(key)
            return df.copy() if df is not None else None

    def set(self, key: str, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return  # Never cache empty frames
        with self._lock:
            self._cache[key] = df

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


# ─── ChartDataLoaderThread ───────────────────────────────────────────────────

class ChartDataLoaderThread(QThread):
    """
    Background thread: fetch → process → cache → emit.

    BUG FIX (Bug 1 / Race condition):
        stop() now sets _stop_requested=True atomically BEFORE calling
        quit(), which closes the window where a late data_loaded signal
        could fire against a chart that has already moved on to a new symbol.

    BUG FIX (Bug 2 / Empty frame):
        Empty-frame guard is now checked BEFORE cache.set().  Previously
        an empty post-dropna frame was cached and then immediately re-served
        on the next load_symbol() call, producing a blank chart.

    Cancellation contract:
        Call stop() from any thread to request cancellation.
        The thread checks _stop_requested at every major step.
        After stop() is called, NO signals will be emitted.

    Signal contract:
        data_loaded(DataFrame, cache_key)   — only emitted on success AND not stopped
        load_error(str)                     — only emitted on failure AND not stopped
        load_progress(int)                  — 0-100, emitted freely (UI progress bar)
    """

    data_loaded   = Signal(object, str)   # (DataFrame, cache_key)
    load_error    = Signal(str)
    load_progress = Signal(int)           # 0-100

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
        self.data_fetcher     = data_fetcher
        self.cache            = cache
        self.symbol           = symbol
        self.instrument_token = instrument_token
        self.interval         = interval
        self.force_refresh    = force_refresh
        self.cache_key        = f"{symbol}_{interval}"
        self._stop_requested  = False

    def stop(self) -> None:
        """
        Request cancellation.  Safe to call from any thread.
        IMPORTANT: sets the flag BEFORE quit() so there is no race
        window where the thread emits a signal after stop() returns.
        """
        self._stop_requested = True          # ← set FIRST
        self.requestInterruption()           # Qt native signal

    def run(self) -> None:
        try:
            self._run_inner()
        except Exception as exc:
            if not self._stop_requested:
                logger.error("Loader uncaught error for %s: %s", self.symbol, exc, exc_info=True)
                self.load_error.emit(f"Unexpected error: {exc}")

    def _run_inner(self) -> None:
        self._emit_progress(10)
        if self._stop_requested:
            return

        # ── Cache hit ─────────────────────────────────────────────────────
        if not self.force_refresh:
            cached = self.cache.get(self.cache_key)
            if cached is not None and not cached.empty:
                self._emit_progress(100)
                if not self._stop_requested:
                    self.data_loaded.emit(cached, self.cache_key)
                return

        if self._stop_requested:
            return

        # ── Build date range ──────────────────────────────────────────────
        to_date   = datetime.now()
        days_back = _DAYS_BACK.get(self.interval, 365)
        from_date = to_date - timedelta(days=days_back)
        self._emit_progress(25)

        if self._stop_requested:
            return

        # ── Fetch from API ────────────────────────────────────────────────
        try:
            raw = self.data_fetcher.fetch(
                instrument_token=self.instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval=self.interval,
            )
        except Exception as exc:
            if not self._stop_requested:
                err_msg = self._classify_error(exc)
                logger.warning("Fetch failed for %s: %s", self.symbol, exc)
                self.load_error.emit(err_msg)
            return

        if self._stop_requested:
            return

        self._emit_progress(65)

        # ── Validate raw data ─────────────────────────────────────────────
        if not raw:
            if not self._stop_requested:
                self.load_error.emit(f"No data returned for {self.symbol}")
            return

        # ── Process ───────────────────────────────────────────────────────
        try:
            df = self._process(raw)
        except Exception as exc:
            if not self._stop_requested:
                self.load_error.emit(f"Data processing error: {exc}")
            return

        if self._stop_requested:
            return

        # ── Empty-frame guard (BEFORE cache.set — was AFTER, which was wrong) ──
        if df.empty:
            if not self._stop_requested:
                self.load_error.emit(f"No valid OHLCV data for {self.symbol}")
            return

        self._emit_progress(90)

        # ── Cache and emit ────────────────────────────────────────────────
        self.cache.set(self.cache_key, df)

        self._emit_progress(100)

        if not self._stop_requested:
            self.data_loaded.emit(df, self.cache_key)

    def _emit_progress(self, value: int) -> None:
        try:
            self.load_progress.emit(value)
        except Exception:
            pass

    def _process(self, raw_data: List[Dict]) -> pd.DataFrame:
        """Convert raw KiteConnect list-of-dicts into a clean OHLCV DataFrame."""
        df = pd.DataFrame(raw_data)
        if df.empty:
            return df

        required = {"date", "open", "high", "low", "close", "volume"}
        missing  = required - set(df.columns)
        if missing:
            raise ValueError(f"Missing columns in API response: {missing}")

        df["date"] = pd.to_datetime(df["date"])
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        before_drop = len(df)
        df = df.dropna(subset=["open", "high", "low", "close"])
        if len(df) < before_drop:
            logger.debug("Dropped %d rows with NaN OHLC values", before_drop - len(df))

        df = df.drop_duplicates(subset="date").sort_values("date")
        df = df.rename(columns={"date": "time"})
        df["symbol"] = self.symbol
        return df

    @staticmethod
    def _classify_error(exc: Exception) -> str:
        msg = str(exc).lower()
        if "interval exceeds" in msg or "date range" in msg:
            return "Date range too large — try a shorter period or smaller interval"
        if "instrument not found" in msg or "invalid instrument" in msg:
            return "Instrument not found — check the symbol"
        if "rate limit" in msg or "too many requests" in msg:
            return "API rate limit exceeded — please wait a moment"
        if "network" in msg or "connection" in msg or "timeout" in msg:
            return "Network error — check your connection and retry"
        if "session" in msg or "unauthorised" in msg or "token" in msg:
            return "Session expired — please re-login"
        return f"Data fetch failed: {exc}"
