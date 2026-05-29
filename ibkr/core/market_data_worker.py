# ibkr/core/market_data_worker.py
"""Thread-safe IBKR market-data worker.

The previous version executed subscribe/unsubscribe calls directly from the Qt
main thread whenever UI widgets changed. With ib_insync that is slow and risky.
This worker now queues commands and processes all IBKR calls inside the worker
loop, while the UI receives only normal Qt signals.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from PySide6.QtCore import QThread, Signal
from ib_insync import IB, Contract, Stock, Ticker

logger = logging.getLogger(__name__)

_MARKET_DATA_TYPE_NAMES = {
    1: "live",
    2: "frozen",
    3: "delayed",
    4: "delayed-frozen",
}
_SUBSCRIPTION_ERROR_CODES = {354, 10089, 10090, 10186, 10197}
_DELAYED_NOTICE_CODES = {10167, 10168}
_LIVE_RETRY_SECONDS = 300.0


def _configured_market_data_type() -> int:
    raw = os.environ.get("IBKR_MARKET_DATA_TYPE", "1").strip().lower()
    aliases = {
        "live": 1,
        "realtime": 1,
        "real-time": 1,
        "frozen": 2,
        "delayed": 3,
        "delay": 3,
        "delayed-frozen": 4,
        "delayed_frozen": 4,
    }
    if raw in aliases:
        return aliases[raw]
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Unknown IBKR_MARKET_DATA_TYPE=%r; using live market data type", raw)
        return 1
    if value not in _MARKET_DATA_TYPE_NAMES:
        logger.warning("Unsupported IBKR_MARKET_DATA_TYPE=%r; using live market data type", raw)
        return 1
    return value


def _delayed_fallback_enabled() -> bool:
    # Default to a delayed fallback because many IBKR accounts do not have live
    # market-data entitlements, while TWS can still stream delayed quotes.
    raw = os.environ.get("IBKR_MARKET_DATA_FALLBACK_DELAYED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _generic_tick_list() -> str:
    # Keep the default request identical to the standalone streaming probe: plain
    # top-of-book market data with no generic tick add-ons.  Some IBKR accounts
    # can stream bid/ask/last data but reject optional generic ticks such as 233
    # (RTVolume), which prevents the GUI from receiving pendingTickersEvent even
    # though tools/ibkr_market_data_probe.py succeeds. Operators that know their
    # entitlements include optional streams can still opt in via the environment.
    return os.environ.get("IBKR_GENERIC_TICKS", "").strip()


def _clean_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _positive_price(*values: Any) -> float:
    for value in values:
        number = _clean_float(value, 0.0)
        if number > 0:
            return number
    return 0.0


class MarketDataWorker(QThread):
    data_received = Signal(list)
    connection_error = Signal(str)
    connection_established = Signal()
    connection_closed = Signal()
    order_update = Signal(dict)
    position_update = Signal(dict)
    market_data_type_changed = Signal(str, bool)

    def __init__(self, ib_client: IB):
        super().__init__()
        self._source_ib = ib_client
        self.ib = ib_client
        self._owns_ib_connection = False
        (
            self._connection_host,
            self._connection_port,
            self._connection_client_id,
        ) = self._connection_params_from_client(ib_client)
        self._is_running = True
        self._commands: "queue.Queue[Tuple[str, list]]" = queue.Queue()
        self._subscribed_contracts: Dict[str, Contract] = {}
        self._symbol_to_key: Dict[str, str] = {}
        self._key_to_symbol: Dict[str, str] = {}
        self._contract_cache: Dict[str, Contract] = {}
        self._latest_ticks: Dict[str, Dict[str, Any]] = {}
        self._req_id_to_key: Dict[int, str] = {}
        self._preferred_market_data_type = _configured_market_data_type()
        self._market_data_type = self._preferred_market_data_type
        self._allow_delayed_fallback = _delayed_fallback_enabled()
        self._generic_tick_list = _generic_tick_list()
        self._tried_without_generic_ticks = not bool(self._generic_tick_list)
        self._tried_delayed_fallback = self._market_data_type == 3
        self._last_live_retry_monotonic = 0.0
        self._lock = threading.RLock()

    def _connection_params_from_client(self, ib_client: IB) -> Tuple[str, int, int]:
        """Extract TWS/Gateway endpoint details from the authenticated IB client.

        The market-data worker opens its own IBKR API session.  It must connect
        to the same TWS/Gateway endpoint as login; the IBKR architecture defaults
        to 127.0.0.1:7496 so secondary sessions do not guess a paper endpoint,
        fall back to the shared cross-thread IB object, and stall watchlist ticks.
        """
        client = getattr(ib_client, "client", None)

        host = str(
            getattr(ib_client, "_qullamaggie_host", "")
            or getattr(client, "host", "")
            or os.environ.get("IBKR_HOST", "127.0.0.1")
        )
        try:
            port = int(
                getattr(ib_client, "_qullamaggie_port", 0)
                or getattr(client, "port", 0)
                or os.environ.get("IBKR_PORT", "7496")
            )
        except (TypeError, ValueError):
            port = 7496
        configured = os.environ.get("IBKR_MARKET_DATA_CLIENT_ID", "101").strip()
        try:
            return host, port, int(configured)
        except ValueError:
            logger.warning(
                "Invalid IBKR_MARKET_DATA_CLIENT_ID=%r; using fixed market-data client id 101",
                configured,
            )
            return host, port, 101

    def _ensure_worker_connection(self) -> bool:
        """Use a thread-owned IB connection for streaming market data.

        ib_insync sockets and asyncio events are tied to the thread/event loop
        where the IB object is driven.  The login client is created elsewhere,
        so the market-data worker opens a lightweight, read-only API session on
        the same TWS/Gateway endpoint instead of driving that shared object from
        this QThread.
        """
        if self._owns_ib_connection and self.ib and self.ib.isConnected():
            return True

        host = self._connection_host
        port = self._connection_port
        preferred_client_id = int(self._connection_client_id)

        last_error: Optional[Exception] = None
        for attempt in range(3):
            dedicated = IB()
            try:
                dedicated.connect(host, port, clientId=preferred_client_id, timeout=8, readonly=True)
                if dedicated.isConnected():
                    self.ib = dedicated
                    self._owns_ib_connection = True
                    logger.info(
                        "Dedicated IBKR market-data connection established on %s:%s clientId=%s",
                        host,
                        port,
                        preferred_client_id,
                    )
                    return True
                last_error = RuntimeError("IBKR market-data connection did not report connected")
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Retry %d/3 for IBKR market-data connection on %s:%s clientId=%s: %s",
                    attempt + 1,
                    host,
                    port,
                    preferred_client_id,
                    exc,
                )
            try:
                dedicated.disconnect()
            except Exception:
                pass
            if attempt < 2:
                time.sleep(1)

        # Fall back only if the shared client is still connected. This keeps the
        # app usable in unusual setups while the warning points at the safer fix.
        if self._source_ib and self._source_ib.isConnected():
            self.ib = self._source_ib
            self._owns_ib_connection = False
            logger.warning(
                "Using shared IBKR client for market data because dedicated connection failed: %s",
                last_error,
            )
            return True
        logger.error("Unable to establish IBKR market-data connection: %s", last_error)
        return False

    # ------------------------------------------------------------------
    # QThread lifecycle
    # ------------------------------------------------------------------
    def run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        if not self._ensure_worker_connection():
            self.connection_error.emit("IB market-data client is not connected.")
            asyncio.set_event_loop(None)
            loop.close()
            return

        logger.info("IBKR MarketDataWorker started")
        try:
            # Prefer real-time streaming data when the account has live market
            # data permissions. Closed-market LTP is handled separately by
            # snapshot requests, which can use frozen/close fields from IBKR.
            self.ib.reqMarketDataType(self._market_data_type)
            self._emit_market_data_type()
            logger.info(
                "Requested IBKR %s market data type (%s); delayed fallback=%s; generic ticks=%r",
                _MARKET_DATA_TYPE_NAMES.get(self._market_data_type, str(self._market_data_type)),
                self._market_data_type,
                self._allow_delayed_fallback,
                self._generic_tick_list,
            )
        except Exception:
            logger.debug("Could not request configured IBKR market data type", exc_info=True)
        self.connection_established.emit()
        self.ib.pendingTickersEvent += self._on_pending_tickers
        try:
            self.ib.errorEvent += self._on_ib_error
        except Exception:
            logger.debug("Could not attach IBKR errorEvent", exc_info=True)
        try:
            self.ib.orderStatusEvent += self._on_order_status
        except Exception:
            logger.debug("Could not attach IBKR orderStatusEvent", exc_info=True)
        try:
            self.ib.positionEvent += self._on_position_update
        except Exception:
            logger.debug("Could not attach IBKR positionEvent", exc_info=True)

        try:
            while self._is_running and self.ib.isConnected():
                self._drain_commands(max_commands=25)
                self._maybe_retry_live_market_data()
                self.ib.waitOnUpdate(timeout=0.05)
        except Exception as exc:
            logger.error("MarketDataWorker loop error: %s", exc, exc_info=True)
            self.connection_error.emit(str(exc))
        finally:
            self._cancel_all_subscriptions()
            try:
                self.ib.pendingTickersEvent -= self._on_pending_tickers
            except Exception:
                pass
            try:
                self.ib.errorEvent -= self._on_ib_error
            except Exception:
                pass
            try:
                self.ib.orderStatusEvent -= self._on_order_status
            except Exception:
                pass
            try:
                self.ib.positionEvent -= self._on_position_update
            except Exception:
                pass
            if self._owns_ib_connection and self.ib and self.ib.isConnected():
                try:
                    self.ib.disconnect()
                except Exception:
                    logger.debug("Failed to disconnect dedicated IBKR market-data client", exc_info=True)
            asyncio.set_event_loop(None)
            loop.close()
            self.connection_closed.emit()
            logger.info("IBKR MarketDataWorker stopped")

    def stop(self) -> None:
        logger.info("Stopping IBKR MarketDataWorker…")
        self._is_running = False
        self._commands.put(("stop", []))
        self.quit()
        if not self.wait(3000):
            logger.warning("MarketDataWorker did not stop in 3s — terminating")
            self.terminate()
            self.wait(1000)

    # ------------------------------------------------------------------
    # Public API called from UI/main thread. These never call IB directly.
    # ------------------------------------------------------------------
    def add_instruments(self, instruments: Iterable[Any]) -> None:
        items = list(instruments or [])
        if items:
            self._commands.put(("add", items))

    def subscribe_to_contracts(self, contracts: List[Contract]) -> None:
        self.add_instruments(contracts)

    def unsubscribe_from_contracts(self, contracts: List[Contract]) -> None:
        items = list(contracts or [])
        if items:
            self._commands.put(("remove", items))

    def set_instruments(self, instruments: Iterable[Any]) -> None:
        # The old implementation cancelled everything and resubscribed everything.
        # This command performs a diff in the worker thread instead.
        self._commands.put(("set", list(instruments or [])))

    def request_snapshots(self, instruments: Iterable[Any]) -> None:
        """Request one-shot quote snapshots without blocking the UI thread.

        Used by the watchlist when markets are closed or immediately after a
        symbol is added, so LTP/%Chg populate even before a live tick arrives.
        """
        items = list(instruments or [])
        if items:
            self._commands.put(("snapshot", items))

    def is_connected(self) -> bool:
        return bool(self.ib and self.ib.isConnected())

    def get_subscription_info(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "subscribed_tokens": [int(k) for k in self._subscribed_contracts if str(k).isdigit()],
                "subscribed_symbols": sorted(self._symbol_to_key.keys()),
                "count": len(self._subscribed_contracts),
            }

    def get_last_price(self, symbol_or_token: Any) -> float:
        key = str(symbol_or_token or "").strip().upper()
        if not key:
            return 0.0
        with self._lock:
            canonical = self._symbol_to_key.get(key, key)
            tick = self._latest_ticks.get(canonical) or self._latest_ticks.get(key)
            return _clean_float((tick or {}).get("last_price"), 0.0)

    # ------------------------------------------------------------------
    # Command processing inside worker thread
    # ------------------------------------------------------------------
    def _drain_commands(self, max_commands: int = 25) -> None:
        for _ in range(max_commands):
            try:
                command, items = self._commands.get_nowait()
            except queue.Empty:
                return

            if command == "stop":
                self._is_running = False
                return
            if command == "add":
                self._subscribe_items(items)
            elif command == "remove":
                self._unsubscribe_items(items)
            elif command == "set":
                self._set_items(items)
            elif command == "snapshot":
                self._snapshot_items(items)

    def _set_items(self, items: List[Any]) -> None:
        desired_keys: Set[str] = set()
        for item in items:
            key = self._key_from_item(item)
            if key:
                desired_keys.add(self._symbol_to_key.get(key, key))

        with self._lock:
            current_keys = set(self._subscribed_contracts.keys())

        remove_keys = current_keys - desired_keys
        add_items = [item for item in items if self._symbol_to_key.get(self._key_from_item(item), self._key_from_item(item)) not in current_keys]

        if remove_keys:
            self._unsubscribe_keys(remove_keys)
        if add_items:
            self._subscribe_items(add_items)

    def _subscribe_items(self, items: List[Any]) -> None:
        contracts_to_qualify: List[Contract] = []
        original_keys: List[str] = []
        original_symbols: List[str] = []

        for item in items:
            key = self._key_from_item(item)
            if not key:
                continue
            with self._lock:
                if key in self._subscribed_contracts or self._symbol_to_key.get(key) in self._subscribed_contracts:
                    continue
                cached = self._contract_cache.get(key)
            if cached is not None:
                self._subscribe_contract(cached, alias_key=key)
                continue
            contract = self._contract_from_item(item)
            if contract is None:
                continue
            contracts_to_qualify.append(contract)
            original_keys.append(key)
            original_symbols.append(
                self._symbol_from_item(item)
                or (getattr(contract, "symbol", "") or "").strip().upper()
            )

        for chunk_start in range(0, len(contracts_to_qualify), 25):
            chunk = contracts_to_qualify[chunk_start:chunk_start + 25]
            aliases = original_keys[chunk_start:chunk_start + 25]
            alias_symbols = original_symbols[chunk_start:chunk_start + 25]
            try:
                qualified = self.ib.qualifyContracts(*chunk)
            except Exception as exc:
                logger.warning("Batch contract qualification failed, falling back per contract: %s", exc)
                qualified = []
                for contract in chunk:
                    try:
                        one = self.ib.qualifyContracts(contract)
                        qualified.append(one[0] if one else contract)
                    except Exception as inner:
                        logger.error("Contract qualification failed for %s: %s", contract, inner)
                        qualified.append(contract)

            # ib_insync returns only successfully qualified contracts. If one is
            # missing, fall back to the submitted contract so subscription still
            # has a chance to work with symbol/conId.
            if len(qualified) != len(chunk):
                by_symbol = {getattr(c, "symbol", ""): c for c in qualified}
                by_conid = {str(getattr(c, "conId", 0) or ""): c for c in qualified}
                qualified = [by_conid.get(str(getattr(c, "conId", 0) or "")) or by_symbol.get(getattr(c, "symbol", "")) or c for c in chunk]

            for contract, alias, alias_symbol in zip(qualified, aliases, alias_symbols):
                self._subscribe_contract(contract, alias_key=alias, alias_symbol=alias_symbol)


    def _request_market_data(self, contract: Contract, *, snapshot: bool) -> Ticker:
        generic_ticks = self._generic_tick_list
        try:
            return self.ib.reqMktData(contract, generic_ticks, snapshot, False)
        except Exception:
            if not generic_ticks:
                raise
            logger.warning(
                "IBKR reqMktData failed with generic ticks %r for %s; disabling optional generic ticks and retrying",
                generic_ticks,
                getattr(contract, "symbol", contract),
                exc_info=True,
            )
            self._generic_tick_list = ""
            self._tried_without_generic_ticks = True
            return self.ib.reqMktData(contract, "", snapshot, False)

    def _subscribe_contract(self, contract: Contract, alias_key: str = "", alias_symbol: str = "") -> None:
        key = self._contract_key(contract) or alias_key
        symbol = (getattr(contract, "symbol", "") or alias_symbol or alias_key).strip().upper()
        if not key:
            return
        with self._lock:
            if key in self._subscribed_contracts:
                return
        try:
            ticker = self._request_market_data(contract, snapshot=False)
            req_id = self._ticker_req_id(ticker)
            with self._lock:
                if req_id is not None:
                    self._req_id_to_key[req_id] = key
                self._subscribed_contracts[key] = contract
                self._contract_cache[key] = contract
                if alias_key:
                    self._contract_cache[alias_key] = contract
                if symbol:
                    self._symbol_to_key[symbol] = key
                    self._key_to_symbol[key] = symbol
                    self._contract_cache[symbol] = contract
            logger.info(
                "Subscribed IBKR market data: %s key=%s type=%s",
                symbol or key,
                key,
                _MARKET_DATA_TYPE_NAMES.get(self._market_data_type, self._market_data_type),
            )
        except Exception as exc:
            logger.error("IBKR market data subscribe failed for %s: %s", symbol or key, exc)

    def _snapshot_items(self, items: List[Any]) -> None:
        """Fetch one-shot LTP/close snapshots in the worker thread."""
        ticks: List[Dict[str, Any]] = []
        for item in items[:75]:  # protect against accidental huge snapshot bursts
            key = self._key_from_item(item)
            if not key:
                continue
            with self._lock:
                cached_contract = self._contract_cache.get(key) or self._contract_cache.get(self._symbol_to_key.get(key, ""))
            contract = cached_contract or self._contract_from_item(item)
            if contract is None:
                continue
            try:
                if cached_contract is None:
                    qualified = self.ib.qualifyContracts(contract)
                    if qualified:
                        contract = qualified[0]
                try:
                    snapshot_type = 4 if self._market_data_type in {3, 4} else 2
                    self.ib.reqMarketDataType(snapshot_type)
                except Exception:
                    logger.debug("Could not request IBKR frozen snapshot data type", exc_info=True)
                ticker = self._request_market_data(contract, snapshot=True)
                # Snapshot delivery is asynchronous; wait briefly inside the
                # worker only. The UI remains responsive.
                for _ in range(5):
                    self.ib.waitOnUpdate(timeout=0.15)
                    tick = self._tick_from_ticker(ticker)
                    if tick:
                        ticks.append(tick)
                        with self._lock:
                            t_key = str(tick.get("instrument_token") or key)
                            self._latest_ticks[t_key] = tick
                            if tick.get("symbol"):
                                self._latest_ticks[str(tick.get("symbol")).upper()] = tick
                        break
            except Exception as exc:
                logger.debug("IBKR snapshot failed for %s: %s", key, exc)
            finally:
                try:
                    self.ib.reqMarketDataType(self._market_data_type)
                except Exception:
                    logger.debug("Could not restore configured IBKR market data type", exc_info=True)
        if ticks:
            self.data_received.emit(ticks)

    def _unsubscribe_items(self, items: List[Any]) -> None:
        keys = {self._symbol_to_key.get(self._key_from_item(item), self._key_from_item(item)) for item in items}
        self._unsubscribe_keys({k for k in keys if k})

    def _unsubscribe_keys(self, keys: Set[str]) -> None:
        for key in list(keys):
            with self._lock:
                contract = self._subscribed_contracts.pop(key, None)
                symbols = [s for s, k in self._symbol_to_key.items() if k == key]
                for symbol in symbols:
                    self._symbol_to_key.pop(symbol, None)
                self._key_to_symbol.pop(key, None)
                self._latest_ticks.pop(key, None)
            if contract is None:
                continue
            try:
                self.ib.cancelMktData(contract)
                logger.info("Unsubscribed IBKR market data: %s", getattr(contract, "symbol", key))
            except Exception as exc:
                logger.error("IBKR unsubscribe failed for %s: %s", key, exc)

    def _cancel_all_subscriptions(self) -> None:
        with self._lock:
            contracts = list(self._subscribed_contracts.items())
            self._subscribed_contracts.clear()
            self._symbol_to_key.clear()
            self._key_to_symbol.clear()
            self._latest_ticks.clear()
            self._req_id_to_key.clear()
        for key, contract in contracts:
            try:
                self.ib.cancelMktData(contract)
            except Exception:
                logger.debug("Failed to cancel subscription %s during shutdown", key, exc_info=True)

    # ------------------------------------------------------------------
    # Contract/tick helpers
    # ------------------------------------------------------------------
    def _key_from_item(self, item: Any) -> str:
        if isinstance(item, Contract):
            return self._contract_key(item)
        if isinstance(item, dict):
            token = item.get("instrument_token") or item.get("conId") or item.get("conid")
            if token not in (None, "", 0, "0"):
                return str(token)
            return str(item.get("tradingsymbol") or item.get("symbol") or item.get("name") or "").strip().upper()
        if isinstance(item, int):
            return str(item)
        if isinstance(item, str):
            return item.strip().upper()
        return ""

    def _contract_key(self, contract: Contract) -> str:
        con_id = int(getattr(contract, "conId", 0) or 0)
        if con_id:
            return str(con_id)
        return (getattr(contract, "symbol", "") or "").strip().upper()

    def _symbol_from_item(self, item: Any) -> str:
        if isinstance(item, Contract):
            return (getattr(item, "symbol", "") or "").strip().upper()
        if isinstance(item, dict):
            return str(item.get("tradingsymbol") or item.get("symbol") or item.get("name") or "").strip().upper()
        if isinstance(item, str) and not item.strip().isdigit():
            return item.strip().upper()
        return ""

    def _ticker_req_id(self, ticker: Any) -> Optional[int]:
        for attr in ("tickerId", "reqId"):
            value = getattr(ticker, attr, None)
            try:
                if value is not None:
                    return int(value)
            except Exception:
                continue
        return None

    def _emit_market_data_type(self) -> None:
        type_name = _MARKET_DATA_TYPE_NAMES.get(self._market_data_type, str(self._market_data_type))
        self.market_data_type_changed.emit(type_name, self._market_data_type in {1, 2})

    def _maybe_retry_live_market_data(self) -> None:
        """Periodically probe live data after delayed fallback is active.

        IBKR does not push a separate event when the user adds live market-data
        entitlements while the application is running.  If the preferred mode is
        live but we fell back to delayed, periodically resubscribe in live mode;
        a subscription error will immediately fall back to delayed again.
        """
        if (
            not self._allow_delayed_fallback
            or self._preferred_market_data_type != 1
            or self._market_data_type != 3
        ):
            return
        with self._lock:
            has_subscriptions = bool(self._subscribed_contracts)
        if not has_subscriptions:
            return
        now = time.monotonic()
        if now - self._last_live_retry_monotonic < _LIVE_RETRY_SECONDS:
            return
        self._last_live_retry_monotonic = now
        self._tried_delayed_fallback = False
        self._switch_market_data_type(
            1,
            "periodic live-market-data retry after delayed fallback",
            resubscribe=True,
        )

    def _switch_market_data_type(self, market_data_type: int, reason: str, resubscribe: bool = False) -> None:
        if market_data_type == self._market_data_type:
            return
        old_type = self._market_data_type
        try:
            self.ib.reqMarketDataType(market_data_type)
            self._market_data_type = market_data_type
            if market_data_type == 3:
                self._last_live_retry_monotonic = time.monotonic()
            self._emit_market_data_type()
            logger.warning(
                "Switched IBKR market data type from %s (%s) to %s (%s): %s",
                _MARKET_DATA_TYPE_NAMES.get(old_type, old_type),
                old_type,
                _MARKET_DATA_TYPE_NAMES.get(market_data_type, market_data_type),
                market_data_type,
                reason,
            )
        except Exception as exc:
            logger.error("Failed to switch IBKR market data type to %s: %s", market_data_type, exc)
            return

        if not resubscribe:
            return

        with self._lock:
            subscriptions = list(self._subscribed_contracts.items())
            self._req_id_to_key.clear()
        for key, contract in subscriptions:
            try:
                self.ib.cancelMktData(contract)
            except Exception:
                logger.debug("Failed to cancel %s before delayed resubscribe", key, exc_info=True)
            try:
                ticker = self._request_market_data(contract, snapshot=False)
                req_id = self._ticker_req_id(ticker)
                if req_id is not None:
                    with self._lock:
                        self._req_id_to_key[req_id] = key
                logger.info("Resubscribed IBKR market data after type switch: %s", getattr(contract, "symbol", key))
            except Exception as exc:
                logger.error("Failed to resubscribe %s after market data type switch: %s", key, exc)


    def _disable_generic_ticks_and_resubscribe(self, reason: str) -> bool:
        """Retry existing streams without optional generic ticks before changing data type.

        The probe script intentionally requests an empty generic tick list.  If
        the GUI has been configured with optional add-ons (for example RTVolume
        233) and IBKR rejects them asynchronously, keeping the optional list on
        every resubscription can make all widgets look stalled even though plain
        streaming works.
        """
        if not self._generic_tick_list or self._tried_without_generic_ticks:
            return False

        disabled_ticks = self._generic_tick_list
        self._generic_tick_list = ""
        self._tried_without_generic_ticks = True
        logger.warning(
            "Disabling IBKR optional generic ticks %r and resubscribing market data: %s",
            disabled_ticks,
            reason,
        )

        with self._lock:
            subscriptions = list(self._subscribed_contracts.items())
            self._req_id_to_key.clear()

        for key, contract in subscriptions:
            try:
                self.ib.cancelMktData(contract)
            except Exception:
                logger.debug("Failed to cancel %s before generic-tick retry", key, exc_info=True)
            try:
                ticker = self._request_market_data(contract, snapshot=False)
                req_id = self._ticker_req_id(ticker)
                if req_id is not None:
                    with self._lock:
                        self._req_id_to_key[req_id] = key
                logger.info("Resubscribed IBKR market data without generic ticks: %s", getattr(contract, "symbol", key))
            except Exception as exc:
                logger.error("Failed to resubscribe %s without generic ticks: %s", key, exc)
        return True

    def _contract_from_item(self, item: Any) -> Optional[Contract]:
        if isinstance(item, Contract):
            return item

        token = None
        symbol = ""
        exchange = "SMART"
        currency = "USD"

        if isinstance(item, dict):
            token = item.get("instrument_token") or item.get("conId") or item.get("conid")
            symbol = str(item.get("tradingsymbol") or item.get("symbol") or item.get("name") or "").strip().upper()
            exchange = item.get("exchange") or "SMART"
            # Avoid direct ECN exchange for routing unless caller explicitly uses SMART.
            if exchange in {"NASDAQ", "NYSE", "ARCA", "AMEX", "BATS"}:
                exchange = "SMART"
            currency = item.get("currency") or "USD"
        elif isinstance(item, int):
            token = item
        elif isinstance(item, str):
            text = item.strip().upper()
            if not text:
                return None
            token = int(text) if text.isdigit() else None
            symbol = "" if text.isdigit() else text

        try:
            if token not in (None, "", 0, "0"):
                contract = Contract()
                contract.conId = int(token)
                contract.secType = "STK"
                contract.exchange = exchange
                contract.currency = currency
                if symbol:
                    contract.symbol = symbol
                return contract
        except Exception:
            pass

        if symbol:
            return Stock(symbol, "SMART", currency)
        return None

    def _tick_from_ticker(self, ticker: Ticker) -> Optional[Dict[str, Any]]:
        contract = getattr(ticker, "contract", None)
        if not contract:
            return None
        symbol = (getattr(contract, "symbol", "") or "").strip().upper()
        con_id = int(getattr(contract, "conId", 0) or 0)
        key = str(con_id) if con_id else symbol
        if not key:
            return None
        if not symbol:
            with self._lock:
                symbol = self._key_to_symbol.get(key, "")

        # reqMktData delivers Level-1 fields.  Per the IBKR data architecture,
        # stream ticks may feed only LTP-style fields: last/delayedLast first,
        # then close/delayedClose as a non-trading-session fallback.  Never let
        # bid/ask/marketPrice update chart candles, watchlist prices, or scanner
        # prices.
        last_price = _positive_price(
            getattr(ticker, "last", 0.0),
            getattr(ticker, "delayedLast", 0.0),
        )
        latest_trade_time = None
        latest_trade_size = _positive_price(
            getattr(ticker, "lastSize", 0.0),
            getattr(ticker, "delayedLastSize", 0.0),
        )

        # ib_insync keeps recent low-level TickData in ticker.ticks.  Only tick
        # types 4/68 are Last/DelayedLast prices; bid/ask ticks must not become
        # chart LTP updates.
        for tick_data in reversed(list(getattr(ticker, "ticks", []) or [])):
            tick_type = int(getattr(tick_data, "tickType", -1) or -1)
            if tick_type not in {4, 68}:
                continue
            tick_price = _clean_float(getattr(tick_data, "price", 0.0), 0.0)
            if tick_price > 0:
                last_price = tick_price
                latest_trade_time = getattr(tick_data, "time", None)
                latest_trade_size = _clean_float(getattr(tick_data, "size", 0.0), latest_trade_size)
                break

        if last_price <= 0:
            last_price = _positive_price(
                getattr(ticker, "close", 0.0),
                getattr(ticker, "delayedClose", 0.0),
            )
        if last_price <= 0:
            return None

        prev_close = _positive_price(
            getattr(ticker, "prevClose", 0.0),
            getattr(ticker, "close", 0.0),
            getattr(ticker, "delayedClose", 0.0),
        )
        change_pct = ((last_price - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0
        ticker_time = latest_trade_time or getattr(ticker, "time", None)
        if isinstance(ticker_time, datetime) and ticker_time.tzinfo is None:
            ticker_time = ticker_time.replace(tzinfo=timezone.utc)

        tick = {
            "symbol": symbol,
            "tradingsymbol": symbol,
            "instrument_token": con_id or key,
            "conId": con_id or 0,
            "last_price": last_price,
            "timestamp": ticker_time,
            "exchange_timestamp": ticker_time,
            "volume": int(_clean_float(getattr(ticker, "volume", 0.0), 0.0)),
            "last_size": latest_trade_size,
            "tick_count": len(list(getattr(ticker, "ticks", []) or [])),
            "close": prev_close,
            "prev_close": prev_close,
            "change_percent": change_pct,
            "open": _clean_float(getattr(ticker, "open", 0.0), 0.0),
            "high": _clean_float(getattr(ticker, "high", 0.0), 0.0),
            "low": _clean_float(getattr(ticker, "low", 0.0), 0.0),
            "bid": _clean_float(getattr(ticker, "bid", 0.0), 0.0),
            "ask": _clean_float(getattr(ticker, "ask", 0.0), 0.0),
            "ohlc": {
                "open": _clean_float(getattr(ticker, "open", 0.0), 0.0),
                "high": _clean_float(getattr(ticker, "high", 0.0), 0.0),
                "low": _clean_float(getattr(ticker, "low", 0.0), 0.0),
                "close": prev_close,
            },
        }
        return tick

    def _on_pending_tickers(self, tickers: List[Ticker]) -> None:
        ticks_data: List[Dict[str, Any]] = []
        for ticker in tickers:
            tick = self._tick_from_ticker(ticker)
            if not tick:
                continue
            symbol = str(tick.get("symbol") or "").upper()
            key = str(tick.get("instrument_token") or symbol)
            with self._lock:
                self._latest_ticks[key] = tick
                if symbol:
                    self._latest_ticks[symbol] = tick
                    self._symbol_to_key.setdefault(symbol, key)
            ticks_data.append(tick)

        if ticks_data:
            self.data_received.emit(ticks_data)

    def _on_ib_error(self, req_id: int, error_code: int, error_string: str, contract: Any = None) -> None:
        symbol = (getattr(contract, "symbol", "") or "").strip().upper() if contract is not None else ""
        with self._lock:
            key = self._req_id_to_key.get(int(req_id)) if req_id not in (None, -1) else None
        label = symbol or key or f"reqId={req_id}"

        if error_code in _DELAYED_NOTICE_CODES:
            logger.warning("IBKR market data notice for %s: %s (%s)", label, error_string, error_code)
            if (
                self._allow_delayed_fallback
                and "delayed" in str(error_string or "").lower()
                and self._market_data_type not in {3, 4}
            ):
                self._market_data_type = 3
                self._tried_delayed_fallback = True
                self._last_live_retry_monotonic = time.monotonic()
                self._emit_market_data_type()
            return

        if error_code in _SUBSCRIPTION_ERROR_CODES:
            logger.error(
                "IBKR market data subscription problem for %s: %s (%s). "
                "This usually means the account lacks live market-data permissions for the exchange, "
                "or delayed market data is not enabled in TWS/Gateway API settings.",
                label,
                error_string,
                error_code,
            )
            if self._disable_generic_ticks_and_resubscribe(
                f"IBKR returned market-data subscription error {error_code} for {label}"
            ):
                return
            if self._allow_delayed_fallback and self._market_data_type != 3 and not self._tried_delayed_fallback:
                self._tried_delayed_fallback = True
                self._switch_market_data_type(
                    3,
                    f"IBKR returned market-data subscription error {error_code} for {label}",
                    resubscribe=True,
                )
            return

        if 10000 <= int(error_code) < 11000 or int(error_code) in {200, 300, 321, 322}:
            logger.warning("IBKR API error for %s: %s (%s)", label, error_string, error_code)

    def _on_order_status(self, trade: Any) -> None:
        try:
            order = getattr(trade, "order", None)
            status = getattr(trade, "orderStatus", None)
            contract = getattr(trade, "contract", None)
            self.order_update.emit({
                "order_id": str(getattr(order, "orderId", "") or getattr(order, "permId", "")),
                "tradingsymbol": getattr(contract, "symbol", ""),
                "symbol": getattr(contract, "symbol", ""),
                "transaction_type": getattr(order, "action", ""),
                "quantity": int(_clean_float(getattr(order, "totalQuantity", 0), 0.0)),
                "status": str(getattr(status, "status", "UNKNOWN") or "UNKNOWN").upper(),
                "filled_quantity": int(_clean_float(getattr(status, "filled", 0), 0.0)),
                "average_price": _clean_float(getattr(status, "avgFillPrice", 0.0), 0.0),
            })
        except Exception:
            logger.debug("Failed to emit order status update", exc_info=True)

    def _on_position_update(self, position: Any) -> None:
        try:
            contract = getattr(position, "contract", None)
            self.position_update.emit({
                "tradingsymbol": getattr(contract, "symbol", ""),
                "symbol": getattr(contract, "symbol", ""),
                "instrument_token": int(getattr(contract, "conId", 0) or 0),
                "conId": int(getattr(contract, "conId", 0) or 0),
                "quantity": int(_clean_float(getattr(position, "position", 0), 0.0)),
                "average_price": _clean_float(getattr(position, "avgCost", 0.0), 0.0),
                "avg_price": _clean_float(getattr(position, "avgCost", 0.0), 0.0),
                "product": getattr(contract, "secType", "IBKR") if contract is not None else "IBKR",
            })
        except Exception:
            logger.debug("Failed to emit position update", exc_info=True)
