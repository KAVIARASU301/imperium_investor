from __future__ import annotations

import asyncio
import itertools
import logging
import os
import threading
import time
from collections import OrderedDict
from datetime import date as date_cls
from datetime import datetime, time as dt_time, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from chart_engine.core.broker_protocol import BarData, BrokerCapabilities, BrokerDataFetcher

logger = logging.getLogger(__name__)


_CLIENT_ID_LOCK = threading.Lock()
_CLIENT_ID_COUNTER = itertools.count(1)
_HISTORY_SEMAPHORE = threading.BoundedSemaphore(1)
_RECENT_REQUEST_LOCK = threading.Lock()
_RECENT_REQUESTS: "OrderedDict[str, float]" = OrderedDict()
_PACING_DELAY_S = 1.0
_SHARED_HISTORY_IB: Optional[Any] = None
_SHARED_HISTORY_LOCK = threading.Lock()


def _ensure_event_loop() -> None:
    """Ensure current thread has an active asyncio event loop for ib_insync sync wrappers."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("Current event loop is closed")
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def _next_history_client_id(base: int) -> int:
    """Return a unique TWS clientId for short-lived chart history sessions."""
    with _CLIENT_ID_LOCK:
        n = next(_CLIENT_ID_COUNTER)
    # Keep it deterministic but away from the main app clientId, usually 1.
    return int(base) + (os.getpid() % 1000) + (n % 500)


IBKR_INTERVAL_MAP: Dict[str, str] = {
    "1min": "1 min", "minute": "1 min",
    "3min": "3 mins", "3minute": "3 mins",
    "5min": "5 mins", "5minute": "5 mins",
    "10min": "10 mins", "10minute": "10 mins",
    "15min": "15 mins", "15minute": "15 mins",
    "30min": "30 mins", "30minute": "30 mins",
    "60min": "1 hour", "60minute": "1 hour", "1h": "1 hour",
    "1d": "1 day", "day": "1 day",
    "1w": "1 week", "week": "1 week",
    "1M": "1 month", "month": "1 month",
}


class IBKRDataFetcher(BrokerDataFetcher):
    """
    IBKR historical data adapter for the chart engine.

    Important: the chart loader runs in a QThread. Reusing the already-connected
    app-wide IB instance inside that worker thread can stall ib_insync requests
    because the socket/asyncio ownership belongs to another thread. Therefore,
    by default, historical chart requests use a short-lived dedicated IB
    connection created and destroyed inside the loader thread itself. This is
    the same threading pattern as the standalone check_connection.py script.
    """

    def __init__(
        self,
        ib_client,
        what_to_show: str = "TRADES",
        use_rth: bool = True,
        dedicated_history_connection: bool = True,
        history_client_id_base: int = 9000,
        connect_timeout: float = 8.0,
    ):
        self._ib = ib_client
        self._what_to_show = what_to_show
        self._use_rth = use_rth
        self._dedicated_history_connection = bool(dedicated_history_connection)
        self._history_client_id_base = int(history_client_id_base)
        self._connect_timeout = float(connect_timeout)
        self._contract_cache: Dict[str, Any] = {}
        self._contract_cache_lock = threading.Lock()
        self._last_history_endpoint: Optional[Tuple[str, int]] = None

    @property
    def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            name="ibkr",
            exchange_tz="America/New_York",
            currency="USD",
            supports_options=True,
            supports_greeks=True,
            supports_level2=True,
        )

    def fetch(
        self,
        symbol: str,
        instrument_token: Any,
        from_date: datetime,
        to_date: datetime,
        interval: str,
    ) -> List[BarData]:
        _ensure_event_loop()

        ib = self._ib
        if self._dedicated_history_connection:
            ib = self._get_or_create_history_connection()

        return self._fetch_with_ib(
            ib=ib,
            symbol=symbol,
            instrument_token=instrument_token,
            from_date=from_date,
            to_date=to_date,
            interval=interval,
        )

    def _fetch_with_ib(
        self,
        ib,
        symbol: str,
        instrument_token: Any,
        from_date: datetime,
        to_date: datetime,
        interval: str,
    ) -> List[BarData]:
        from ib_insync import Contract, Stock

        bar_size = IBKR_INTERVAL_MAP.get(interval, "1 day")
        duration_str = self._compute_duration(from_date, to_date, bar_size)
        end_dt_str = self._format_end_datetime(to_date)

        con_id = self._coerce_con_id(instrument_token)
        cache_key = self._contract_cache_key(symbol, con_id)
        contract = self._get_cached_contract(cache_key)
        qualified_once = contract is not None

        if contract is None and con_id > 0:
            # With a valid conId, TWS can usually serve history without an extra
            # qualifyContracts() round-trip. If it fails, we qualify and retry once.
            contract = Contract()
            contract.conId = con_id
            contract.symbol = symbol
            contract.secType = "STK"
            contract.exchange = "SMART"
            contract.currency = "USD"
        elif contract is None:
            stock = Stock(symbol, "SMART", "USD")
            try:
                qualified = ib.qualifyContracts(stock)
                if not qualified:
                    raise ValueError(f"Could not qualify contract for {symbol}")
                contract = qualified[0]
                qualified_once = True
                self._cache_contract(cache_key, contract)
            except Exception as e:
                raise ValueError(f"Could not qualify contract for {symbol}: {e}") from e

        logger.info(
            "Requesting IBKR historical data: %s conId=%s bar=%s duration=%s end=%s",
            symbol,
            getattr(contract, "conId", con_id) or con_id or "symbol-lookup",
            bar_size,
            duration_str,
            end_dt_str or "latest",
        )

        try:
            bars = self._request_historical_bars(
                ib=ib,
                contract=contract,
                end_dt_str=end_dt_str,
                duration_str=duration_str,
                bar_size=bar_size,
            )
        except Exception:
            if con_id > 0 and not qualified_once:
                logger.debug("IBKR history failed with conId-only contract; qualifying %s and retrying", symbol)
                contract = self._qualify_by_con_id(ib, symbol, con_id)
                self._cache_contract(cache_key, contract)
                bars = self._request_historical_bars(
                    ib=ib,
                    contract=contract,
                    end_dt_str=end_dt_str,
                    duration_str=duration_str,
                    bar_size=bar_size,
                )
            else:
                raise

        if not bars and con_id > 0 and not qualified_once:
            contract = self._qualify_by_con_id(ib, symbol, con_id)
            self._cache_contract(cache_key, contract)
            bars = self._request_historical_bars(
                ib=ib,
                contract=contract,
                end_dt_str=end_dt_str,
                duration_str=duration_str,
                bar_size=bar_size,
            )

        if not bars:
            raise ValueError(f"No data returned for {symbol} [{bar_size}]")

        if getattr(contract, "conId", 0):
            self._cache_contract(cache_key, contract)
            self._cache_contract(self._contract_cache_key(symbol, int(getattr(contract, "conId", 0))), contract)

        logger.info("Received %d IBKR bars for %s", len(bars), symbol)
        return [self._bar_to_bardata(b) for b in bars]

    def _request_historical_bars(
            self,
            ib,
            contract,
            end_dt_str: str,
            duration_str: str,
            bar_size: str,
            max_retries: int = 3,
    ):
        """
        Serialize and pace TWS historical requests, with automatic retry on
        empty/timeout responses.

        TWS paces identical requests and the HMDS data farm can take up to
        ~15 s to warm up after login.  Three attempts with exponential back-off
        handles both race conditions without blocking the UI thread for too long.
        """
        pacing_key = f"{getattr(contract, 'conId', '')}_{bar_size}_{duration_str}_{end_dt_str}"

        with _RECENT_REQUEST_LOCK:
            last_time = _RECENT_REQUESTS.get(pacing_key)
            now = time.monotonic()
            if last_time and (now - last_time) < _PACING_DELAY_S:
                wait = _PACING_DELAY_S - (now - last_time)
            else:
                wait = 0.0

        if wait > 0:
            logger.info("TWS pacing: sleeping %.1fs before duplicate request %s", wait, pacing_key)
            time.sleep(wait)

        request_kwargs = dict(
            endDateTime=end_dt_str,
            durationStr=duration_str,
            barSizeSetting=bar_size,
            whatToShow=self._what_to_show,
            useRTH=self._use_rth,
            formatDate=1,
            keepUpToDate=False,
        )

        last_bars = []
        for attempt in range(1, max_retries + 1):
            with _HISTORY_SEMAPHORE:
                with _RECENT_REQUEST_LOCK:
                    _RECENT_REQUESTS[pacing_key] = time.monotonic()
                    if len(_RECENT_REQUESTS) > 200:
                        _RECENT_REQUESTS.popitem(last=False)

                try:
                    bars = ib.reqHistoricalData(contract, timeout=60, **request_kwargs)
                except TypeError:
                    # Older ib_insync builds don't accept timeout as a kwarg.
                    bars = ib.reqHistoricalData(contract, **request_kwargs)
                except Exception as exc:
                    logger.warning(
                        "reqHistoricalData raised on attempt %d/%d: %s",
                        attempt, max_retries, exc,
                    )
                    bars = []

            last_bars = bars or []

            if last_bars:
                return last_bars

            # Empty result — could be HMDS farm warming up or TWS pacing.
            # Back-off: 5s, 10s, 20s …
            if attempt < max_retries:
                back_off = 5.0 * (2 ** (attempt - 1))
                logger.warning(
                    "Empty bars for %s [%s] on attempt %d/%d; "
                    "retrying in %.0fs (HMDS may still be warming up)…",
                    getattr(contract, "symbol", "?"),
                    bar_size,
                    attempt,
                    max_retries,
                    back_off,
                )
                time.sleep(back_off)

        return last_bars

    @staticmethod
    def _coerce_con_id(instrument_token: Any) -> int:
        try:
            return int(instrument_token) if instrument_token else 0
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _contract_cache_key(symbol: str, con_id: int) -> str:
        if con_id > 0:
            return f"conid:{con_id}"
        return f"symbol:{str(symbol or '').strip().upper()}"

    def _get_cached_contract(self, key: str):
        with self._contract_cache_lock:
            return self._contract_cache.get(key)

    def _cache_contract(self, key: str, contract) -> None:
        if not key or contract is None:
            return
        with self._contract_cache_lock:
            self._contract_cache[key] = contract

    def _qualify_by_con_id(self, ib, symbol: str, con_id: int):
        from ib_insync import Contract

        contract = Contract()
        contract.conId = int(con_id)
        contract.symbol = symbol
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise ValueError(f"Could not qualify contract for {symbol}/{con_id}")
        return qualified[0]


    def _get_or_create_history_connection(self):
        """Return a single, lazily-created shared history IB connection."""
        global _SHARED_HISTORY_IB
        with _SHARED_HISTORY_LOCK:
            if _SHARED_HISTORY_IB is not None:
                try:
                    if _SHARED_HISTORY_IB.isConnected():
                        return _SHARED_HISTORY_IB
                except Exception:
                    pass
            _SHARED_HISTORY_IB = self._connect_dedicated_ib()
            return _SHARED_HISTORY_IB

    def close_history_connections(self) -> None:
        global _SHARED_HISTORY_IB
        with _SHARED_HISTORY_LOCK:
            if _SHARED_HISTORY_IB is not None:
                try:
                    _SHARED_HISTORY_IB.disconnect()
                except Exception:
                    pass
                _SHARED_HISTORY_IB = None

    def _connect_dedicated_ib(self):
        """Create a new IB connection owned by the current loader thread."""
        from ib_insync import IB

        last_error: Optional[Exception] = None
        for host, port in self._connection_candidates():
            ib = IB()
            client_id = _next_history_client_id(self._history_client_id_base)
            try:
                ib.connect(host=host, port=int(port), clientId=client_id, timeout=self._connect_timeout)
                if ib.isConnected():
                    self._last_history_endpoint = (str(host), int(port))
                    logger.info("IBKR chart history connected on %s:%s clientId=%s", host, port, client_id)
                    return ib
            except Exception as exc:
                last_error = exc
                try:
                    ib.disconnect()
                except Exception:
                    pass

        raise ConnectionError(f"Could not open dedicated IBKR history connection: {last_error}")

    def _connection_candidates(self) -> List[Tuple[str, int]]:
        """Prefer the already-connected client's host/port, then common TWS/Gateway ports."""
        candidates: List[Tuple[str, int]] = []

        def add(host: Any, port: Any) -> None:
            try:
                host_s, port_i = str(host or "").strip(), int(port)
            except Exception:
                return
            if host_s and port_i and (host_s, port_i) not in candidates:
                candidates.append((host_s, port_i))

        if self._last_history_endpoint:
            add(*self._last_history_endpoint)

        # Try to mirror the main app's active connection first.
        client = getattr(self._ib, "client", None)
        for obj in (client, getattr(client, "conn", None), self._ib):
            if obj is None:
                continue
            h = getattr(obj, "host", None) or getattr(obj, "_host", None)
            p = getattr(obj, "port", None) or getattr(obj, "_port", None)
            if h and p:
                add(h, p)

        # Fallbacks ordered by likelihood: live TWS, live Gateway, paper TWS, paper Gateway.
        for host in ("127.0.0.1", "localhost", "::1"):
            for port in (7496, 4001, 7497, 4002):
                add(host, port)
        return candidates

    def resolve_instrument(self, symbol: str):
        from ib_insync import Stock
        try:
            details = self._ib.reqContractDetails(Stock(symbol, "SMART", "USD"))
            if details:
                return details[0].contract
        except Exception as exc:
            logger.error("resolve_instrument failed for %s: %s", symbol, exc)
        return None

    @staticmethod
    def _bar_to_bardata(bar) -> BarData:
        raw_date = bar.date
        if isinstance(raw_date, str):
            # "YYYYMMDD" or "YYYYMMDD HH:MM:SS"
            fmt = "%Y%m%d %H:%M:%S" if " " in raw_date else "%Y%m%d"
            dt = datetime.strptime(raw_date, fmt).replace(tzinfo=timezone.utc)
        elif isinstance(raw_date, datetime):
            dt = raw_date.replace(tzinfo=timezone.utc) if raw_date.tzinfo is None else raw_date
        elif isinstance(raw_date, date_cls):
            dt = datetime.combine(raw_date, dt_time.min, tzinfo=timezone.utc)
        else:
            dt = raw_date

        return BarData(
            time=dt,
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=float(getattr(bar, "volume", 0) or 0),
        )

    @staticmethod
    def _format_end_datetime(to_date: datetime) -> str:
        """
        Return a TWS-compatible endDateTime.

        If the requested end is now/future, use an empty string. This matches the
        working check_connection.py behavior and avoids accidentally formatting a
        New York local timestamp as literal UTC.
        """
        if to_date.tzinfo is None:
            end_utc = to_date.replace(tzinfo=timezone.utc)
        else:
            end_utc = to_date.astimezone(timezone.utc)

        if end_utc >= datetime.now(timezone.utc):
            return ""
        return end_utc.strftime("%Y%m%d %H:%M:%S UTC")

    @staticmethod
    def _compute_duration(from_date: datetime, to_date: datetime, bar_size: str) -> str:
        """Return a TWS-compatible durationStr."""
        days = max(1, (to_date - from_date).days + 1)
        if bar_size in ("1 week", "1 month"):
            years = max(1, min(10, (days // 365) + 1))
            return f"{years} Y"
        if bar_size == "1 day":
            return f"{min(days, 365)} D"
        # Intraday: cap at 30 days.
        return f"{min(days, 30)} D"
