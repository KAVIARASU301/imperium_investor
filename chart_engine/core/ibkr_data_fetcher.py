from __future__ import annotations

import asyncio
import concurrent.futures
import itertools
import logging
import os
import threading
import time
from collections import OrderedDict
from datetime import date as date_cls
from datetime import datetime, time as dt_time, timezone
from typing import Any, Dict, List, Optional, Tuple

from chart_engine.core.broker_protocol import BarData, BrokerCapabilities, BrokerDataFetcher

logger = logging.getLogger(__name__)

_CLIENT_ID_LOCK = threading.Lock()
_CLIENT_ID_COUNTER = itertools.count(1)

# Pacing and Caching
_RECENT_REQUEST_LOCK = threading.Lock()
_RECENT_REQUESTS: "OrderedDict[str, float]" = OrderedDict()
_PACING_DELAY_S = 1.0

# Shared Async Connection State
_SHARED_HISTORY_IB: Optional[Any] = None
_SHARED_HISTORY_LOCK_ASYNC: Optional[asyncio.Lock] = None


# ─── Native Asyncio Daemon Thread for True IBKR Concurrency ──────────────────
# Upgraded from a sync queue to an asyncio event loop. This allows multiple
# charts (Dual Chart mode) to fetch data concurrently on a single IB connection
# without hitting TWS pacing blocks or freezing the UI.

class HistoryExecutor(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="IBKR-History-Async-Thread")
        self.loop = asyncio.new_event_loop()
        self._started = threading.Event()
        self.start()

    def run(self):
        asyncio.set_event_loop(self.loop)
        self._started.set()
        self.loop.run_forever()

    def submit_async(self, coro) -> concurrent.futures.Future:
        """Submit an async coroutine to the background loop safely."""
        self._started.wait()
        return asyncio.run_coroutine_threadsafe(coro, self.loop)


_HISTORY_EXECUTOR: Optional[HistoryExecutor] = None
_HISTORY_EXECUTOR_LOCK = threading.Lock()


def _get_history_executor() -> HistoryExecutor:
    global _HISTORY_EXECUTOR
    with _HISTORY_EXECUTOR_LOCK:
        if _HISTORY_EXECUTOR is None:
            _HISTORY_EXECUTOR = HistoryExecutor()
        return _HISTORY_EXECUTOR


# ──────────────────────────────────────────────────────────────────────────────


def _ensure_event_loop() -> None:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("Current event loop is closed")
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def _next_history_client_id(base: int) -> int:
    with _CLIENT_ID_LOCK:
        n = next(_CLIENT_ID_COUNTER)
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

        if not self._dedicated_history_connection:
            _ensure_event_loop()
            # If not dedicated, run the async method synchronously using the main IB instance
            return self._ib.run(
                self._fetch_with_ib_async(self._ib, symbol, instrument_token, from_date, to_date, interval)
            )

        # Dispatch the async pipeline to the persistent daemon loop
        executor = _get_history_executor()
        future = executor.submit_async(
            self._fetch_async(symbol, instrument_token, from_date, to_date, interval)
        )
        return future.result()

    async def _fetch_async(
            self,
            symbol: str,
            instrument_token: Any,
            from_date: datetime,
            to_date: datetime,
            interval: str,
    ) -> List[BarData]:
        ib = await self._get_or_create_history_connection_async()
        return await self._fetch_with_ib_async(
            ib=ib,
            symbol=symbol,
            instrument_token=instrument_token,
            from_date=from_date,
            to_date=to_date,
            interval=interval,
        )

    async def _fetch_with_ib_async(
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
            contract = Contract()
            contract.conId = con_id
            contract.symbol = symbol
            contract.secType = "STK"
            contract.exchange = "SMART"
            contract.currency = "USD"
        elif contract is None:
            stock = Stock(symbol, "SMART", "USD")
            try:
                qualified = await ib.qualifyContractsAsync(stock)
                if not qualified:
                    raise ValueError(f"Could not qualify contract for {symbol}")
                contract = qualified[0]
                qualified_once = True
                self._cache_contract(cache_key, contract)
            except Exception as e:
                raise ValueError(f"Could not qualify contract for {symbol}: {e}") from e

        logger.info(
            "Requesting IBKR historical data: %s conId=%s bar=%s duration=%s end=%s",
            symbol, getattr(contract, "conId", con_id) or con_id or "symbol-lookup",
            bar_size, duration_str, end_dt_str or "latest",
        )

        try:
            bars = await self._request_historical_bars_async(
                ib=ib, contract=contract, end_dt_str=end_dt_str,
                duration_str=duration_str, bar_size=bar_size,
            )
        except Exception:
            if con_id > 0 and not qualified_once:
                contract = await self._qualify_by_con_id_async(ib, symbol, con_id)
                self._cache_contract(cache_key, contract)
                bars = await self._request_historical_bars_async(
                    ib=ib, contract=contract, end_dt_str=end_dt_str,
                    duration_str=duration_str, bar_size=bar_size,
                )
            else:
                raise

        if not bars and con_id > 0 and not qualified_once:
            contract = await self._qualify_by_con_id_async(ib, symbol, con_id)
            self._cache_contract(cache_key, contract)
            bars = await self._request_historical_bars_async(
                ib=ib, contract=contract, end_dt_str=end_dt_str,
                duration_str=duration_str, bar_size=bar_size,
            )

        if not bars:
            raise ValueError(f"No data returned for {symbol} [{bar_size}]")

        if getattr(contract, "conId", 0):
            self._cache_contract(cache_key, contract)
            self._cache_contract(self._contract_cache_key(symbol, int(getattr(contract, "conId", 0))), contract)

        return [self._bar_to_bardata(b) for b in bars]

    async def _request_historical_bars_async(
            self, ib, contract, end_dt_str: str, duration_str: str, bar_size: str, max_retries: int = 3
    ):
        pacing_key = f"{getattr(contract, 'conId', '')}_{bar_size}_{duration_str}_{end_dt_str}"

        # 1. Pacing Delay (Applied ONLY to identical symbols/timeframes to prevent TWS soft-bans)
        with _RECENT_REQUEST_LOCK:
            last_time = _RECENT_REQUESTS.get(pacing_key)
            now = time.monotonic()
            wait = _PACING_DELAY_S - (now - last_time) if (last_time and (now - last_time) < _PACING_DELAY_S) else 0.0

        if wait > 0:
            logger.info("TWS pacing: sleeping %.1fs before duplicate request %s", wait, pacing_key)
            await asyncio.sleep(wait)

        request_kwargs = dict(
            endDateTime=end_dt_str, durationStr=duration_str, barSizeSetting=bar_size,
            whatToShow=self._what_to_show, useRTH=self._use_rth, formatDate=1, keepUpToDate=False,
        )

        last_bars = []
        for attempt in range(1, max_retries + 1):
            with _RECENT_REQUEST_LOCK:
                _RECENT_REQUESTS[pacing_key] = time.monotonic()
                if len(_RECENT_REQUESTS) > 200:
                    _RECENT_REQUESTS.popitem(last=False)

            try:
                # 2. Asynchronous TWS Request (No Semaphore! Allows Parallel Chart Loading)
                coro = ib.reqHistoricalDataAsync(contract, **request_kwargs)
                bars = await asyncio.wait_for(coro, timeout=60.0)
            except asyncio.TimeoutError:
                logger.warning("reqHistoricalDataAsync timed out on attempt %d/%d", attempt, max_retries)
                bars = []
            except Exception as exc:
                logger.warning("reqHistoricalDataAsync raised on attempt %d/%d: %s", attempt, max_retries, exc)
                bars = []

            last_bars = bars or []
            if last_bars:
                return last_bars

            if attempt < max_retries:
                back_off = 5.0 * (2 ** (attempt - 1))
                logger.warning("Empty bars for %s on attempt %d/%d; retrying in %.0fs...",
                               getattr(contract, "symbol", "?"), attempt, max_retries, back_off)
                await asyncio.sleep(back_off)

        return last_bars

    async def _qualify_by_con_id_async(self, ib, symbol: str, con_id: int):
        from ib_insync import Contract
        contract = Contract()
        contract.conId = int(con_id)
        contract.symbol = symbol
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        qualified = await ib.qualifyContractsAsync(contract)
        if not qualified:
            raise ValueError(f"Could not qualify contract for {symbol}/{con_id}")
        return qualified[0]

    async def _get_or_create_history_connection_async(self):
        """Return a single, lazily-created shared async IB connection."""
        global _SHARED_HISTORY_IB, _SHARED_HISTORY_LOCK_ASYNC
        if _SHARED_HISTORY_LOCK_ASYNC is None:
            _SHARED_HISTORY_LOCK_ASYNC = asyncio.Lock()

        async with _SHARED_HISTORY_LOCK_ASYNC:
            if _SHARED_HISTORY_IB is not None:
                try:
                    if _SHARED_HISTORY_IB.isConnected():
                        return _SHARED_HISTORY_IB
                except Exception:
                    pass
            _SHARED_HISTORY_IB = await self._connect_dedicated_ib_async()
            return _SHARED_HISTORY_IB

    async def _connect_dedicated_ib_async(self):
        from ib_insync import IB
        last_error = None
        for host, port in self._connection_candidates():
            ib = IB()
            client_id = _next_history_client_id(self._history_client_id_base)
            try:
                await ib.connectAsync(host=host, port=int(port), clientId=client_id, timeout=self._connect_timeout)
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

    def close_history_connections(self) -> None:
        pass

    def _connection_candidates(self) -> List[Tuple[str, int]]:
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

        client = getattr(self._ib, "client", None)
        for obj in (client, getattr(client, "conn", None), self._ib):
            if obj is None: continue
            h = getattr(obj, "host", None) or getattr(obj, "_host", None)
            p = getattr(obj, "port", None) or getattr(obj, "_port", None)
            if h and p: add(h, p)

        for host in ("127.0.0.1", "localhost", "::1"):
            for port in (7496, 4001, 7497, 4002):
                add(host, port)
        return candidates

    def resolve_instrument(self, symbol: str):
        if not self._dedicated_history_connection:
            from ib_insync import Stock
            _ensure_event_loop()
            try:
                details = self._ib.reqContractDetails(Stock(symbol, "SMART", "USD"))
                if details: return details[0].contract
            except Exception as exc:
                logger.error("resolve_instrument failed for %s: %s", symbol, exc)
            return None

        executor = _get_history_executor()
        future = executor.submit_async(self._resolve_instrument_in_dedicated_thread_async(symbol))
        return future.result()

    async def _resolve_instrument_in_dedicated_thread_async(self, symbol: str):
        from ib_insync import Stock
        try:
            ib = await self._get_or_create_history_connection_async()
            coro = ib.reqContractDetailsAsync(Stock(symbol, "SMART", "USD"))
            details = await asyncio.wait_for(coro, timeout=10.0)
            if details:
                return details[0].contract
        except Exception as exc:
            logger.error("resolve_instrument in dedicated thread failed for %s: %s", symbol, exc)
        return None

    @staticmethod
    def _coerce_con_id(instrument_token: Any) -> int:
        try:
            return int(instrument_token) if instrument_token else 0
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _contract_cache_key(symbol: str, con_id: int) -> str:
        return f"conid:{con_id}" if con_id > 0 else f"symbol:{str(symbol or '').strip().upper()}"

    def _get_cached_contract(self, key: str):
        with self._contract_cache_lock: return self._contract_cache.get(key)

    def _cache_contract(self, key: str, contract) -> None:
        if not key or contract is None: return
        with self._contract_cache_lock: self._contract_cache[key] = contract

    @staticmethod
    def _bar_to_bardata(bar) -> BarData:
        raw_date = bar.date
        if isinstance(raw_date, str):
            fmt = "%Y%m%d %H:%M:%S" if " " in raw_date else "%Y%m%d"
            dt = datetime.strptime(raw_date, fmt).replace(tzinfo=timezone.utc)
        elif isinstance(raw_date, datetime):
            dt = raw_date.replace(tzinfo=timezone.utc) if raw_date.tzinfo is None else raw_date
        elif isinstance(raw_date, date_cls):
            dt = datetime.combine(raw_date, dt_time.min, tzinfo=timezone.utc)
        else:
            dt = raw_date

        return BarData(
            time=dt, open=float(bar.open), high=float(bar.high), low=float(bar.low),
            close=float(bar.close), volume=float(getattr(bar, "volume", 0) or 0)
        )

    @staticmethod
    def _format_end_datetime(to_date: datetime) -> str:
        end_utc = to_date.replace(tzinfo=timezone.utc) if to_date.tzinfo is None else to_date.astimezone(timezone.utc)
        return "" if end_utc >= datetime.now(timezone.utc) else end_utc.strftime("%Y%m%d %H:%M:%S UTC")

    @staticmethod
    def _compute_duration(from_date: datetime, to_date: datetime, bar_size: str) -> str:
        days = max(1, (to_date - from_date).days + 1)
        if bar_size in ("1 week", "1 month"):
            return f"{max(1, min(10, (days // 365) + 1))} Y"
        return f"{min(days, 365)} D" if bar_size == "1 day" else f"{min(days, 30)} D"