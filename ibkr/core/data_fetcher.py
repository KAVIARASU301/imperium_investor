# ibkr/core/data_fetcher.py
"""Broker-aware historical data fetcher.

This keeps the old Kite-style methods, but also supports IBKR/ib_insync through
`fetch_ohlcv()`, the method used by the chart engine.  The IBKR path caches
qualified contracts so chart reloads do not repeatedly hit reqContractDetails.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone, time, date
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

try:
    from ib_insync import Contract, Stock
except Exception:  # pragma: no cover - optional at import time
    Contract = None
    Stock = None

try:
    _NY_TZ = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _NY_TZ = timezone(timedelta(hours=-5))

_MARKET_OPEN_ET = time(9, 30)


def _today_et() -> date:
    return datetime.now(tz=_NY_TZ).date()


def _effective_to_date(interval: str) -> date:
    """Stable chart end-date for US equities."""
    now_et = datetime.now(tz=_NY_TZ)
    if str(interval or "day") in {"day", "week", "month"} and now_et.time() < _MARKET_OPEN_ET:
        return now_et.date() - timedelta(days=1)
    return now_et.date()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        f = float(value)
        return f if math.isfinite(f) else default
    except Exception:
        return default


class DataFetcher:
    """Historical data helper for Kite-compatible clients and IBKR clients."""

    def __init__(self, client: Any):
        self.client = client
        self._contract_cache: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public chart-engine interface
    # ------------------------------------------------------------------
    def fetch_ohlcv(self, symbol: str, instrument_token: Any, interval: str, from_date, to_date) -> List[Dict[str, Any]]:
        if self._is_ibkr_client():
            return self._fetch_ibkr_ohlcv(symbol, instrument_token, interval, from_date, to_date)
        records = self.fetch_historical_data(instrument_token, from_date, to_date, interval)
        return [self._normalise_kite_record(r) for r in records]

    # ------------------------------------------------------------------
    # Backward-compatible Kite-style API
    # ------------------------------------------------------------------
    def fetch_historical_data(self, instrument_token: Any, from_date, to_date, interval: str, symbol: str = "") -> List[Any]:
        try:
            if self._is_ibkr_client():
                return self._fetch_ibkr_ohlcv(symbol, instrument_token, interval, from_date, to_date)

            logger.info("Fetching historical data token=%s interval=%s range=%s..%s", instrument_token, interval, from_date, to_date)
            records = self.client.historical_data(instrument_token, from_date, to_date, interval)
            logger.info("Fetched %d historical records for token=%s", len(records), instrument_token)
            return records
        except Exception as exc:
            self._log_fetch_error(exc, instrument_token, interval)
            return []

    def get_optimal_date_range(self, interval: str, max_days: Optional[int] = None) -> Tuple[date, date]:
        to_date = _effective_to_date(interval)
        if max_days is not None:
            days = int(max_days)
        elif interval == "day":
            days = 730
        elif interval in {"week", "month"}:
            days = 3650
        elif interval in {"60minute", "30minute", "15minute"}:
            days = 30
        elif interval in {"10minute", "5minute", "3minute", "minute"}:
            days = 5
        else:
            days = 365
        return to_date - timedelta(days=days), to_date

    def fetch_historical_data_with_retry(self, instrument_token: Any, from_date, to_date, interval: str, max_retries: int = 3, symbol: str = "") -> List[Any]:
        start = from_date
        for attempt in range(max(1, int(max_retries))):
            records = self.fetch_historical_data(instrument_token, start, to_date, interval, symbol=symbol)
            if records:
                return records
            if attempt < max_retries - 1:
                days = max((to_date - start).days // 2, 2)
                start = to_date - timedelta(days=days)
                logger.warning("Retrying historical fetch with reduced range: %s days", days)
        return []

    # ------------------------------------------------------------------
    # IBKR internals
    # ------------------------------------------------------------------
    def _is_ibkr_client(self) -> bool:
        return bool(self.client and hasattr(self.client, "reqHistoricalData"))

    def _fetch_ibkr_ohlcv(self, symbol: str, instrument_token: Any, interval: str, from_date, to_date) -> List[Dict[str, Any]]:
        contract = self._resolve_ibkr_contract(symbol, instrument_token)
        if contract is None:
            logger.warning("IBKR historical fetch skipped; unresolved contract symbol=%s token=%s", symbol, instrument_token)
            return []

        duration = self._ibkr_duration(from_date, to_date, interval)
        bar_size = self._ibkr_bar_size(interval)
        end_dt = self._ibkr_end_datetime(to_date)

        try:
            bars = self.client.reqHistoricalData(
                contract,
                endDateTime=end_dt,
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
                keepUpToDate=False,
            )
        except Exception as exc:
            self._log_fetch_error(exc, instrument_token or symbol, interval)
            return []

        return [
            {
                "date": getattr(bar, "date", None),
                "open": _safe_float(getattr(bar, "open", 0.0)),
                "high": _safe_float(getattr(bar, "high", 0.0)),
                "low": _safe_float(getattr(bar, "low", 0.0)),
                "close": _safe_float(getattr(bar, "close", 0.0)),
                "volume": int(_safe_float(getattr(bar, "volume", 0), 0.0)),
            }
            for bar in (bars or [])
        ]

    def _resolve_ibkr_contract(self, symbol: str, instrument_token: Any):
        if Contract is None or Stock is None:
            return None

        key = str(instrument_token or symbol or "").strip().upper()
        if not key:
            return None
        if key in self._contract_cache:
            return self._contract_cache[key]

        contract = None
        try:
            if instrument_token not in (None, "", 0, "0"):
                contract = Contract()
                contract.conId = int(instrument_token)
                contract.secType = "STK"
                contract.exchange = "SMART"
                contract.currency = "USD"
                if symbol:
                    contract.symbol = str(symbol).strip().upper()
            elif symbol:
                contract = Stock(str(symbol).strip().upper(), "SMART", "USD")
        except Exception:
            contract = None

        if contract is None:
            return None

        try:
            qualified = self.client.qualifyContracts(contract)
            resolved = qualified[0] if qualified else contract
        except Exception as exc:
            logger.warning("IBKR contract qualification failed for %s: %s", key, exc)
            resolved = contract

        self._contract_cache[key] = resolved
        if symbol:
            self._contract_cache[str(symbol).strip().upper()] = resolved
        if getattr(resolved, "conId", 0):
            self._contract_cache[str(resolved.conId)] = resolved
        return resolved

    @staticmethod
    def _ibkr_bar_size(interval: str) -> str:
        return {
            "minute": "1 min",
            "3minute": "3 mins",
            "5minute": "5 mins",
            "10minute": "10 mins",
            "15minute": "15 mins",
            "30minute": "30 mins",
            "60minute": "1 hour",
            "day": "1 day",
            "week": "1 week",
            "month": "1 month",
        }.get(str(interval or "day"), "1 day")

    @staticmethod
    def _ibkr_duration(from_date, to_date, interval: str) -> str:
        try:
            days = max(1, int((to_date - from_date).days) + 1)
        except Exception:
            days = 365 if interval == "day" else 5

        interval = str(interval or "day")
        if interval in {"minute", "3minute", "5minute", "10minute"}:
            return f"{min(days, 5)} D"
        if interval in {"15minute", "30minute", "60minute"}:
            return f"{min(days, 30)} D"
        if interval == "week":
            return f"{min(max(days, 365), 3650)} D"
        if interval == "month":
            return f"{min(max(days, 365), 3650)} D"
        return f"{min(max(days, 30), 730)} D"

    @staticmethod
    def _ibkr_end_datetime(to_date) -> str:
        if isinstance(to_date, datetime):
            dt = to_date.astimezone(_NY_TZ) if to_date.tzinfo else to_date.replace(tzinfo=_NY_TZ)
        else:
            dt = datetime.combine(to_date, time(16, 0), tzinfo=_NY_TZ)
        return dt.strftime("%Y%m%d %H:%M:%S US/Eastern")

    @staticmethod
    def _normalise_kite_record(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "date": record.get("date"),
            "open": record.get("open", 0),
            "high": record.get("high", 0),
            "low": record.get("low", 0),
            "close": record.get("close", 0),
            "volume": record.get("volume", 0),
        }

    @staticmethod
    def _log_fetch_error(exc: Exception, token: Any, interval: str) -> None:
        msg = str(exc).lower()
        if "pacing" in msg or "rate" in msg or "too many" in msg:
            logger.error("Historical data pacing/rate limit hit for %s %s: %s", token, interval, exc)
        elif "interval exceeds max limit" in msg:
            logger.error("Date range too large for %s data: %s", interval, exc)
        elif "not found" in msg or "no security definition" in msg:
            logger.error("Instrument/contract not found for %s: %s", token, exc)
        else:
            logger.error("Historical data fetch failed for %s %s: %s", token, interval, exc)


# Names commonly imported by older modules.
KiteDataFetcher = DataFetcher
IBKRDataFetcher = DataFetcher
