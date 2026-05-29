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
from math import ceil
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from chart_engine.core.broker_protocol import BarData, BrokerCapabilities, BrokerDataFetcher

logger = logging.getLogger(__name__)

_CLIENT_ID_LOCK = threading.Lock()
_CLIENT_ID_COUNTER = itertools.count(1)

# Pacing and Caching
_RECENT_REQUEST_LOCK = threading.Lock()
_RECENT_REQUESTS: "OrderedDict[str, float]" = OrderedDict()
_PACING_DELAY_S = 0.2
_INFLIGHT_REQUEST_LOCK = threading.Lock()
_INFLIGHT_REQUESTS: Dict[str, Tuple[asyncio.AbstractEventLoop, asyncio.Task]] = {}

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


_NY_TZ = ZoneInfo("America/New_York")
_US_RTH_OPEN_MINUTES = 9 * 60 + 30
_US_RTH_CLOSE_MINUTES = 16 * 60

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

        # Keep HMDS warm from startup so first/next symbol transitions do not
        # pay the dedicated history-socket cold-start cost.
        self.prewarm_connection()

    def preload_contracts(self, symbols: list[str]) -> Optional[concurrent.futures.Future]:
        """Warm the IBKR contract cache for the supplied symbols without blocking callers."""
        clean_symbols: List[str] = []
        seen = set()
        for raw_symbol in symbols or []:
            symbol = str(raw_symbol or "").strip().upper()
            if not symbol or symbol in seen:
                continue
            if self._get_cached_contract(self._contract_cache_key(symbol, 0)) is not None:
                continue
            seen.add(symbol)
            clean_symbols.append(symbol)

        if not clean_symbols:
            return None

        executor = _get_history_executor()
        return executor.submit_async(self._preload_contracts_async(clean_symbols))

    async def _preload_contracts_async(
            self,
            symbols: List[str],
            batch_size: int = 12,
            batch_delay: float = 0.35,
    ) -> None:
        """Qualify/cache contracts in small paced batches on the history loop."""
        from ib_insync import Stock

        try:
            ib = await self._get_or_create_history_connection_async()
        except Exception as exc:
            logger.debug("IBKR contract preload skipped; history connection unavailable: %s", exc)
            return

        warmed = 0
        for start in range(0, len(symbols), max(1, int(batch_size))):
            batch = [
                symbol
                for symbol in symbols[start:start + batch_size]
                if self._get_cached_contract(self._contract_cache_key(symbol, 0)) is None
            ]
            if not batch:
                continue

            contracts = [Stock(symbol, "SMART", "USD") for symbol in batch]
            try:
                qualified = await asyncio.wait_for(
                    ib.qualifyContractsAsync(*contracts),
                    timeout=max(8.0, 1.5 * len(batch)),
                )
            except Exception as exc:
                logger.debug("IBKR batched contract preload failed for %s: %s", batch, exc)
                qualified = []

            qualified_by_symbol = {
                str(getattr(contract, "symbol", "") or "").strip().upper(): contract
                for contract in qualified or []
            }

            for symbol in batch:
                contract = qualified_by_symbol.get(symbol)
                if contract is None:
                    contract = await self._preload_contract_details_fallback_async(ib, symbol)
                if contract is None:
                    continue
                self._cache_contract_aliases(
                    symbol,
                    contract,
                    preferred_key=self._contract_cache_key(symbol, 0),
                )
                warmed += 1

            if start + batch_size < len(symbols):
                await asyncio.sleep(batch_delay)

        if warmed:
            logger.debug("Preloaded %d IBKR contracts", warmed)

    async def _preload_contract_details_fallback_async(self, ib, symbol: str):
        """Fallback single-symbol contract-details lookup used when batch qualification misses."""
        from ib_insync import Stock

        try:
            details = await asyncio.wait_for(
                ib.reqContractDetailsAsync(Stock(symbol, "SMART", "USD")),
                timeout=8.0,
            )
            if details:
                return details[0].contract
        except Exception as exc:
            logger.debug("IBKR contract-details preload failed for %s: %s", symbol, exc)
        finally:
            await asyncio.sleep(0.05)
        return None

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
        logger.info(
            "Chart data source=IBKR symbol=%s interval=%s from=%s to=%s dedicated_history=%s",
            symbol, interval, from_date.isoformat(), to_date.isoformat(), self._dedicated_history_connection,
        )

        if not self._dedicated_history_connection:
            _ensure_event_loop()
            # If not dedicated, run the async method synchronously using the main IB instance
            bars = self._ib.run(
                self._fetch_with_ib_async(self._ib, symbol, instrument_token, from_date, to_date, interval)
            )
            logger.info("Chart data fetch complete source=IBKR symbol=%s bars=%d", symbol, len(bars or []))
            return bars

        # Dispatch the async pipeline to the persistent daemon loop
        executor = _get_history_executor()
        future = executor.submit_async(
            self._fetch_async(symbol, instrument_token, from_date, to_date, interval)
        )
        bars = future.result()
        logger.info("Chart data fetch complete source=IBKR symbol=%s bars=%d", symbol, len(bars or []))
        return bars

    def prewarm_connection(self) -> None:
        """Forces the background thread to establish the TWS connection immediately."""
        if not self._dedicated_history_connection:
            return
        logger.info("Pre-warming dedicated IBKR history connection...")
        executor = _get_history_executor()
        executor.submit_async(self._get_or_create_history_connection_async())

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
        bar_size = IBKR_INTERVAL_MAP.get(interval, "1 day")
        aggregate_rth_hourly = str(interval or "").strip().lower() in {"60minute", "60min", "1h"}
        request_bar_size = "30 mins" if aggregate_rth_hourly else bar_size
        duration_str = self._compute_duration(from_date, to_date, request_bar_size)
        end_dt_str = self._format_end_datetime(to_date)

        con_id = self._coerce_con_id(instrument_token)
        cache_key = self._contract_cache_key(symbol, con_id)
        symbol_cache_key = self._contract_cache_key(symbol, 0)
        # Fast path: cached contracts are already usable for historical data, so
        # avoid spending another round-trip re-qualifying them on repeated chart
        # loads.  Only qualify when the contract is absent from both aliases.
        contract = self._get_cached_contract(cache_key) or self._get_cached_contract(symbol_cache_key)

        if contract is None:
            if con_id > 0:
                contract = await self._qualify_by_con_id_async(ib, symbol, con_id)
            else:
                contract = await self._qualify_by_symbol_async(ib, symbol)
            self._cache_contract_aliases(symbol, contract, preferred_key=cache_key)

        try:
            bars = await self._request_historical_bars_async(
                ib=ib, contract=contract, end_dt_str=end_dt_str,
                duration_str=duration_str, bar_size=request_bar_size, request_symbol=symbol,
            )
        except Exception as exc:
            logger.error("Historical request failed for %s: %s", symbol, exc)
            raise

        if not bars:
            raise ValueError(f"No data returned for {symbol} [{request_bar_size}]")

        self._cache_contract_aliases(symbol, contract, preferred_key=cache_key)
        bars = self._filter_bars_to_window(bars, from_date, to_date, request_bar_size)
        if aggregate_rth_hourly:
            return self._aggregate_rth_hourly_bars(bars)
        return [self._bar_to_bardata(b) for b in bars]

    @classmethod
    def _aggregate_rth_hourly_bars(cls, bars) -> List[BarData]:
        """Build IBKR 1H candles on US regular-session boundaries.

        IBKR's native ``1 hour`` historical bars are not guaranteed to align to
        the US equity open.  Build the chart's 1H view from smaller bars so each
        session starts at 09:30 ET, advances in one-hour buckets, and keeps the
        final 15:30-16:00 ET bucket as a 30-minute candle.
        """
        buckets: "OrderedDict[datetime, Dict[str, float]]" = OrderedDict()

        for bar in bars or []:
            raw_dt = cls._parse_bar_datetime(getattr(bar, "date", None))
            if not isinstance(raw_dt, datetime):
                continue

            if raw_dt.tzinfo is not None:
                bar_dt = raw_dt.astimezone(_NY_TZ).replace(tzinfo=None)
            else:
                bar_dt = raw_dt

            minutes = bar_dt.hour * 60 + bar_dt.minute
            if minutes < _US_RTH_OPEN_MINUTES or minutes >= _US_RTH_CLOSE_MINUTES:
                continue

            bucket_offset = ((minutes - _US_RTH_OPEN_MINUTES) // 60) * 60
            bucket_minutes = _US_RTH_OPEN_MINUTES + bucket_offset
            bucket_start = datetime.combine(
                bar_dt.date(),
                dt_time(bucket_minutes // 60, bucket_minutes % 60),
            )

            volume = float(getattr(bar, "volume", 0) or 0)
            high = float(getattr(bar, "high"))
            low = float(getattr(bar, "low"))
            close = float(getattr(bar, "close"))

            bucket = buckets.get(bucket_start)
            if bucket is None:
                buckets[bucket_start] = {
                    "open": float(getattr(bar, "open")),
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                }
            else:
                bucket["high"] = max(bucket["high"], high)
                bucket["low"] = min(bucket["low"], low)
                bucket["close"] = close
                bucket["volume"] += volume

        return [
            BarData(
                time=start,
                open=values["open"],
                high=values["high"],
                low=values["low"],
                close=values["close"],
                volume=values["volume"],
            )
            for start, values in buckets.items()
        ]

    async def _request_historical_bars_async(
            self,
            ib,
            contract,
            end_dt_str: str,
            duration_str: str,
            bar_size: str,
            max_retries: int = 4,
            request_symbol: str = "",
    ):
        pacing_key = f"{getattr(contract, 'conId', '')}_{bar_size}_{duration_str}_{end_dt_str}"
        loop = asyncio.get_running_loop()

        with _INFLIGHT_REQUEST_LOCK:
            existing = _INFLIGHT_REQUESTS.get(pacing_key)
            if existing is not None:
                existing_loop, existing_task = existing
                if existing_loop is loop and not existing_task.done():
                    logger.debug("Joining in-flight IBKR historical request: %s", pacing_key)
                    task = existing_task
                else:
                    _INFLIGHT_REQUESTS.pop(pacing_key, None)
                    task = None
            else:
                task = None

            if task is None:
                task = loop.create_task(
                    self._execute_historical_request_async(
                        ib=ib,
                        contract=contract,
                        end_dt_str=end_dt_str,
                        duration_str=duration_str,
                        bar_size=bar_size,
                        max_retries=max_retries,
                        pacing_key=pacing_key,
                        request_symbol=request_symbol,
                    )
                )
                _INFLIGHT_REQUESTS[pacing_key] = (loop, task)

        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                with _INFLIGHT_REQUEST_LOCK:
                    current = _INFLIGHT_REQUESTS.get(pacing_key)
                    if current is not None and current[1] is task:
                        _INFLIGHT_REQUESTS.pop(pacing_key, None)

    async def _execute_historical_request_async(
            self,
            ib,
            contract,
            end_dt_str: str,
            duration_str: str,
            bar_size: str,
            max_retries: int,
            pacing_key: str,
            request_symbol: str,
    ):
        logger.info(
            "Requesting IBKR historical data: %s conId=%s bar=%s duration=%s end=%s",
            request_symbol or getattr(contract, "symbol", "?"),
            getattr(contract, "conId", None) or "symbol-lookup",
            bar_size,
            duration_str,
            end_dt_str or "latest",
        )

        # 1. Pacing Delay (Applied ONLY to identical symbols/timeframes to prevent TWS soft-bans)
        with _RECENT_REQUEST_LOCK:
            last_time = _RECENT_REQUESTS.get(pacing_key)
            now = time.monotonic()
            wait = _PACING_DELAY_S - (now - last_time) if (last_time and (now - last_time) < _PACING_DELAY_S) else 0.0

        if wait > 0:
            logger.info("TWS pacing: sleeping %.1fs before duplicate request %s", wait, pacing_key)
            await asyncio.sleep(wait)

        # Intraday IBKR charts should include extended-hours bars so premarket
        # moves are visible; higher timeframes keep the configured RTH policy.
        use_rth = self._use_rth if bar_size in {"1 day", "1 week", "1 month"} else False
        request_kwargs = dict(
            endDateTime=end_dt_str, durationStr=duration_str, barSizeSetting=bar_size,
            whatToShow=self._what_to_show, useRTH=use_rth, formatDate=1, keepUpToDate=False,
        )

        last_bars = []
        # Aggressive timeout schedule: 8s, 12s, 20s.
        timeout_schedule = (8.0, 12.0, 20.0)
        for attempt in range(1, max_retries + 1):
            with _RECENT_REQUEST_LOCK:
                _RECENT_REQUESTS[pacing_key] = time.monotonic()
                if len(_RECENT_REQUESTS) > 200:
                    _RECENT_REQUESTS.popitem(last=False)

            timeout = timeout_schedule[min(attempt - 1, len(timeout_schedule) - 1)]
            try:
                # 2. Asynchronous TWS Request (No Semaphore! Allows Parallel Chart Loading)
                coro = ib.reqHistoricalDataAsync(contract, **request_kwargs)
                bars = await asyncio.wait_for(coro, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    "reqHistoricalDataAsync timed out after %.0fs on attempt %d/%d for %s",
                    timeout,
                    attempt,
                    max_retries,
                    request_symbol,
                )
                bars = []
            except Exception as exc:
                logger.warning("reqHistoricalDataAsync error attempt %d/%d: %s", attempt, max_retries, exc)
                bars = []

            last_bars = bars or []
            if last_bars:
                return last_bars

            if attempt < max_retries:
                backoff = (0.5, 1.0, 2.0)[min(attempt - 1, 2)]
                logger.warning("Empty bars attempt %d/%d, retrying in %.1fs", attempt, max_retries, backoff)
                await asyncio.sleep(backoff)

        return last_bars

    async def _qualify_by_symbol_async(self, ib, symbol: str):
        from ib_insync import Stock
        stock = Stock(symbol, "SMART", "USD")
        try:
            qualified = await ib.qualifyContractsAsync(stock)
            if not qualified:
                raise ValueError(f"Could not qualify contract for {symbol}")
            return qualified[0]
        except Exception as e:
            raise ValueError(f"Could not qualify contract for {symbol}: {e}") from e

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
        """Close dedicated HMDS connection and stop background async executor."""
        if not self._dedicated_history_connection:
            return
        _shutdown_history_resources()

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

        # IBKR mode is intentionally live-TWS/Gateway first: resolve/qualify and
        # historical candles come from the local API endpoint before streaming
        # reqMktData() is used for LTP-style updates.
        add(os.environ.get("IBKR_HOST", "127.0.0.1"), os.environ.get("IBKR_PORT", "7496"))

        client = getattr(self._ib, "client", None)
        for obj in (client, getattr(client, "conn", None), self._ib):
            if obj is None: continue
            h = getattr(obj, "host", None) or getattr(obj, "_host", None)
            p = getattr(obj, "port", None) or getattr(obj, "_port", None)
            if h and p: add(h, p)

        add("localhost", 7496)
        add("::1", 7496)
        return candidates

    def resolve_instrument(self, symbol: str):
        symbol_cache_key = self._contract_cache_key(symbol, 0)
        cached_contract = self._get_cached_contract(symbol_cache_key)
        if cached_contract is not None:
            return cached_contract

        if not self._dedicated_history_connection:
            from ib_insync import Stock
            _ensure_event_loop()
            try:
                details = self._ib.reqContractDetails(Stock(symbol, "SMART", "USD"))
                if details:
                    contract = details[0].contract
                    self._cache_contract_aliases(symbol, contract, preferred_key=symbol_cache_key)
                    return contract
            except Exception as exc:
                logger.error("resolve_instrument failed for %s: %s", symbol, exc)
            return None

        executor = _get_history_executor()
        future = executor.submit_async(self._resolve_instrument_in_dedicated_thread_async(symbol))
        return future.result()

    async def _resolve_instrument_in_dedicated_thread_async(self, symbol: str):
        from ib_insync import Stock
        symbol_cache_key = self._contract_cache_key(symbol, 0)
        cached_contract = self._get_cached_contract(symbol_cache_key)
        if cached_contract is not None:
            return cached_contract

        try:
            ib = await self._get_or_create_history_connection_async()
            coro = ib.reqContractDetailsAsync(Stock(symbol, "SMART", "USD"))
            details = await asyncio.wait_for(coro, timeout=10.0)
            if details:
                contract = details[0].contract
                self._cache_contract_aliases(symbol, contract, preferred_key=symbol_cache_key)
                return contract
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

    def _cache_contract_aliases(self, symbol: str, contract, preferred_key: Optional[str] = None) -> None:
        if contract is None:
            return
        if preferred_key:
            self._cache_contract(preferred_key, contract)
        con_id = int(getattr(contract, "conId", 0) or 0)
        self._cache_contract(self._contract_cache_key(symbol, 0), contract)
        if con_id > 0:
            self._cache_contract(self._contract_cache_key(symbol, con_id), contract)

    @staticmethod
    def _parse_bar_datetime(raw_date):
        if isinstance(raw_date, str):
            # IBKR daily bars are exchange *calendar* dates (YYYYMMDD), not
            # UTC instants.  Treat date-only values as timezone-naive calendar
            # keys so the loader does not convert UTC midnight to the previous
            # America/New_York date.  Intraday strings include a time component
            # and remain timestamp-like values for normal exchange conversion.
            fmt = "%Y%m%d %H:%M:%S" if " " in raw_date else "%Y%m%d"
            return datetime.strptime(raw_date, fmt)
        if isinstance(raw_date, datetime):
            return raw_date
        if isinstance(raw_date, date_cls):
            return datetime.combine(raw_date, dt_time.min)
        return raw_date

    @classmethod
    def _filter_bars_to_window(cls, bars, from_date: datetime, to_date: datetime, bar_size: str):
        # Year-based IBKR duration strings are required for >365-day requests,
        # but they round up (for example 600 calendar days -> 2 Y).  Trim the
        # over-fetched higher-timeframe bars back to the requested chart window
        # so the UI keeps honoring its days-back settings.
        if bar_size not in {"1 day", "1 week", "1 month"}:
            return bars

        start_date = from_date.date() if isinstance(from_date, datetime) else from_date
        end_date = to_date.date() if isinstance(to_date, datetime) else to_date
        filtered = []
        for bar in bars or []:
            parsed = cls._parse_bar_datetime(getattr(bar, "date", None))
            bar_date = parsed.date() if isinstance(parsed, datetime) else parsed
            if bar_date is None or start_date <= bar_date <= end_date:
                filtered.append(bar)
        return filtered

    @staticmethod
    def _bar_to_bardata(bar) -> BarData:
        dt = IBKRDataFetcher._parse_bar_datetime(bar.date)

        return BarData(
            time=dt, open=float(bar.open), high=float(bar.high), low=float(bar.low),
            close=float(bar.close), volume=float(getattr(bar, "volume", 0) or 0)
        )

    @staticmethod
    def _format_end_datetime(to_date: datetime) -> str:
        end_utc = to_date.replace(tzinfo=timezone.utc) if to_date.tzinfo is None else to_date.astimezone(timezone.utc)
        now_utc = datetime.now(timezone.utc)
        return "" if end_utc.date() >= now_utc.date() else end_utc.strftime("%Y%m%d %H:%M:%S UTC")

    @staticmethod
    def _compute_duration(from_date: datetime, to_date: datetime, bar_size: str) -> str:
        total_seconds = max(0.0, (to_date - from_date).total_seconds())
        days = max(1, int(ceil(total_seconds / 86400.0)))
        if bar_size in {"1 day", "1 week", "1 month"} and days > 365:
            return f"{max(1, int(ceil(days / 365.0)))} Y"
        return f"{days} D"


def _shutdown_history_resources() -> None:
    """Best-effort teardown for shared history IB connection and loop thread."""
    global _SHARED_HISTORY_IB, _HISTORY_EXECUTOR

    with _HISTORY_EXECUTOR_LOCK:
        executor = _HISTORY_EXECUTOR
        if executor is None:
            _SHARED_HISTORY_IB = None
            return

        try:
            async def _close_async():
                global _SHARED_HISTORY_IB
                ib = _SHARED_HISTORY_IB
                if ib is not None:
                    try:
                        if ib.isConnected():
                            ib.disconnect()
                    except Exception:
                        pass
                _SHARED_HISTORY_IB = None

            fut = executor.submit_async(_close_async())
            fut.result(timeout=5.0)
        except Exception as exc:
            logger.warning("Failed to close shared IBKR history connection cleanly: %s", exc)
            _SHARED_HISTORY_IB = None

        try:
            executor.loop.call_soon_threadsafe(executor.loop.stop)
            executor.join(timeout=2.0)
            if not executor.loop.is_closed():
                executor.loop.close()
        except Exception as exc:
            logger.warning("Failed to stop IBKR history executor loop: %s", exc)
        finally:
            _HISTORY_EXECUTOR = None
