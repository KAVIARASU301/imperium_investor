import logging
import threading
from datetime import datetime, time as dt_time, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
from cachetools import TTLCache
from PySide6.QtCore import QThread, Signal
from zoneinfo import ZoneInfo

from chart_engine.core.broker_protocol import BarData, BrokerCapabilities, BrokerDataFetcher

logger = logging.getLogger(__name__)


# Canonical interval → Kite API interval string
KITE_INTERVAL_MAP: Dict[str, str] = {
    "1min": "minute", "3min": "3minute", "5min": "5minute",
    "10min": "10minute", "15min": "15minute", "30min": "30minute",
    "60min": "60minute", "1h": "60minute", "1d": "day",
    "1w": "week", "1M": "month",
    "minute": "minute", "day": "day", "week": "week", "month": "month",
}


# ─── Date range config per interval ──────────────────────────────────────────
#
# Kite historical API max lookback windows by interval.
# Keep requests at/under these ceilings so the chart does not fail with
# "interval exceeds max limit: <N> days".
DEFAULT_DAYS_BACK: Dict[str, int] = {
    "minute":     5,
    "3minute":   10,
    "5minute":   10,
    "10minute":  10,
    "15minute":  10,
    "30minute":  30,
    "60minute":  50,
    "day":      365,
    "week":    1000,
    "month":   2000,
}

IBKR_DYNAMIC_DAYS_BACK: Dict[str, int] = {
    "minute": 14,
    "3minute": 14,
    "5minute": 14,
    "10minute": 14,
    "15minute": 14,
    "60minute": 30,
    "day": 365,
}


def resolve_days_back(interval: str, overrides: Optional[Dict[str, int]] = None) -> int:
    base = int(DEFAULT_DAYS_BACK.get(interval, 365))
    if not isinstance(overrides, dict):
        return base
    raw = overrides.get(interval)
    try:
        return max(1, int(raw)) if raw is not None else base
    except (TypeError, ValueError):
        return base


# ─── KiteDataFetcher ─────────────────────────────────────────────────────────


class KiteDataFetcher(BrokerDataFetcher):
    """Wraps Kite historical_data() behind the broker protocol."""

    def __init__(self, kite_client):
        self._kite = kite_client

    @property
    def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            name="kite",
            exchange_tz="Asia/Kolkata",
            currency="INR",
        )

    def fetch(self, symbol, instrument_token, from_date, to_date, interval) -> List[BarData]:
        kite_interval = KITE_INTERVAL_MAP.get(interval, interval)
        fetch_iv = "day" if kite_interval in {"week", "month"} else kite_interval
        raw = self._kite.historical_data(
            instrument_token=int(instrument_token),
            from_date=from_date,
            to_date=to_date,
            interval=fetch_iv,
        )
        return [
            BarData(
                time=r["date"],
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                volume=float(r["volume"]),
            )
            for r in raw
        ]

    def resolve_instrument(self, symbol: str):
        return None


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
        data_fetcher: BrokerDataFetcher,
        cache: DataCache,
        symbol: str,
        instrument_token: Any,
        interval: str,
        force_refresh: bool = False,
        parent=None,
        days_back_overrides: Optional[Dict[str, int]] = None,
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
        self.days_back_overrides = dict(days_back_overrides or {})

    def stop(self) -> None:
        """
        Request cancellation. Safe to call from any thread.
        Sets the flag BEFORE quit() so there is no race window where the
        thread emits a signal after stop() returns.
        """
        self._stop_requested = True
        self.requestInterruption()

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

        # ── Build date range before cache lookup ──────────────────────────
        # The upper bound is part of the data identity.  Without this, a daily
        # cache created before/after the midnight or market-close boundary can
        # be reused for the wrong session and make the latest completed candle
        # appear/disappear until the TTL expires.
        caps = self.data_fetcher.capabilities
        exchange_tz = ZoneInfo(caps.exchange_tz)
        now_exchange = datetime.now(tz=exchange_tz)

        broker_name = str(getattr(caps, "name", "")).lower()
        to_date = self._effective_to_date(now_exchange, broker_name)
        days_back = resolve_days_back(self.interval, self.days_back_overrides)
        if broker_name == "ibkr":
            days_back = int(self.days_back_overrides.get(self.interval, IBKR_DYNAMIC_DAYS_BACK.get(self.interval, days_back)))
            # IBKR historical requests are duration-based from endDateTime.
            # Use a true rolling window ending at the current exchange timestamp
            # so "100 days" means now back 100 days and always includes today.
            from_date_dt = to_date - timedelta(days=days_back)
        else:
            from_date = to_date.date() - timedelta(days=days_back)
            from_date_dt = datetime.combine(from_date, dt_time.min, tzinfo=exchange_tz)
        scoped_cache_key = self._build_cache_key(to_date)

        # ── Cache hit ─────────────────────────────────────────────────────
        if not self.force_refresh:
            cached = self.cache.get(scoped_cache_key)
            if cached is not None and not cached.empty:
                self._emit_progress(100)
                if not self._stop_requested:
                    self.data_loaded.emit(cached, self.cache_key)
                return

        if self._stop_requested:
            return

        self._emit_progress(25)

        if self._stop_requested:
            return

        # ── Fetch from API ────────────────────────────────────────────────

        try:
            raw = self.data_fetcher.fetch(
                symbol=self.symbol,
                instrument_token=self.instrument_token,
                from_date=from_date_dt,
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
            df = self._process(raw, exchange_tz)
        except Exception as exc:
            if not self._stop_requested:
                self.load_error.emit(f"Data processing error: {exc}")
            return

        if self._stop_requested:
            return

        # ── Empty-frame guard (BEFORE cache.set) ──────────────────────────
        if df.empty:
            if not self._stop_requested:
                self.load_error.emit(f"No valid OHLCV data for {self.symbol}")
            return

        self._emit_progress(90)

        # ── Cache and emit ────────────────────────────────────────────────
        self.cache.set(scoped_cache_key, df)

        self._emit_progress(100)

        if not self._stop_requested:
            self.data_loaded.emit(df, self.cache_key)


    def _effective_to_date(self, now_exchange: datetime, broker_name: str = "") -> datetime:
        if broker_name == "ibkr":
            return now_exchange
        if self.interval == "day":
            return datetime.combine(now_exchange.date(), dt_time(23, 59, 59), tzinfo=now_exchange.tzinfo)
        if self.interval in {"week", "month"}:
            d = now_exchange.date()
            if now_exchange.time() < dt_time(16, 0):
                d = d - timedelta(days=1)
            return datetime.combine(d, dt_time(23, 59, 59), tzinfo=now_exchange.tzinfo)
        return now_exchange

    def _build_cache_key(self, to_date: datetime) -> str:
        if self.interval in {"day", "week", "month"}:
            return f"{self.cache_key}_{to_date.strftime('%Y%m%d')}"
        return self.cache_key

    def _emit_progress(self, value: int) -> None:
        try:
            self.load_progress.emit(value)
        except Exception:
            pass

    def _process(self, raw_data: List[BarData], exchange_tz) -> pd.DataFrame:
        """Convert List[BarData] into a clean OHLCV DataFrame."""
        records = [{"date": b.time, "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume} for b in raw_data]
        df = pd.DataFrame(records)
        if df.empty:
            return df

        required = {"date", "open", "high", "low", "close", "volume"}
        missing  = required - set(df.columns)
        if missing:
            raise ValueError(f"Missing columns in API response: {missing}")

        df["date"] = pd.to_datetime(df["date"])

        # ── EXCHANGE CALENDAR NORMALISATION ───────────────────────────────
        # TradingView-style daily/weekly/monthly bars are calendar bars. They
        # represent an exchange trading date, not a precise UTC instant. Kite
        # returns daily candles at NSE/IST midnight; if that timestamp is later
        # serialized as a normal datetime, host timezone conversion can shift the
        # visible date and make the most recent completed daily candle appear to
        # be missing.
        #
        # Normalize higher timeframes to timezone-naive exchange dates here, then
        # chart_widget serializes them as UTC-midnight calendar keys. Intraday
        # bars intentionally remain true timestamps so minute candles keep their
        # broker-provided session times.
        if self.interval in {"day", "week", "month"}:
            if df["date"].dt.tz is not None:
                df["date"] = df["date"].dt.tz_convert(exchange_tz).dt.tz_localize(None)
            df["date"] = df["date"].dt.normalize()
        elif df["date"].dt.tz is not None:
            # For intraday data, keep the actual instant but remove timezone info
            # after conversion to IST to match the renderer's existing convention.
            df["date"] = df["date"].dt.tz_convert(exchange_tz).dt.tz_localize(None)

        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        before_drop = len(df)
        df = df.dropna(subset=["open", "high", "low", "close"])
        if len(df) < before_drop:
            logger.debug("Dropped %d rows with NaN OHLC values", before_drop - len(df))

        df = df.drop_duplicates(subset="date").sort_values("date")

        if self.interval in {"week", "month"}:
            rule = "W-MON" if self.interval == "week" else "MS"
            df = (
                df.set_index("date")
                  .resample(rule)
                  .agg({
                      "open": "first",
                      "high": "max",
                      "low": "min",
                      "close": "last",
                      "volume": "sum",
                  })
                  .dropna(subset=["open", "high", "low", "close"])
                  .reset_index()
            )

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
