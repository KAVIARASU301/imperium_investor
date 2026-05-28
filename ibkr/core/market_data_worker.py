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
import queue
import threading
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from PySide6.QtCore import QThread, Signal
from ib_insync import IB, Contract, Stock, Ticker

logger = logging.getLogger(__name__)


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

    def __init__(self, ib_client: IB):
        super().__init__()
        self.ib = ib_client
        self._is_running = True
        self._commands: "queue.Queue[Tuple[str, list]]" = queue.Queue()
        self._subscribed_contracts: Dict[str, Contract] = {}
        self._symbol_to_key: Dict[str, str] = {}
        self._contract_cache: Dict[str, Contract] = {}
        self._latest_ticks: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # QThread lifecycle
    # ------------------------------------------------------------------
    def run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        if not self.ib or not self.ib.isConnected():
            self.connection_error.emit("IB client is not connected.")
            asyncio.set_event_loop(None)
            loop.close()
            return

        logger.info("IBKR MarketDataWorker started")
        try:
            # Prefer real-time streaming data when the account has live market
            # data permissions. Closed-market LTP is handled separately by
            # snapshot requests, which can use frozen/close fields from IBKR.
            self.ib.reqMarketDataType(1)
        except Exception:
            logger.debug("Could not request live IBKR market data type", exc_info=True)
        self.connection_established.emit()
        self.ib.pendingTickersEvent += self._on_pending_tickers
        try:
            self.ib.orderStatusEvent += self._on_order_status
        except Exception:
            logger.debug("Could not attach IBKR orderStatusEvent", exc_info=True)

        try:
            while self._is_running and self.ib.isConnected():
                self._drain_commands(max_commands=25)
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
                self.ib.orderStatusEvent -= self._on_order_status
            except Exception:
                pass
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

        for chunk_start in range(0, len(contracts_to_qualify), 25):
            chunk = contracts_to_qualify[chunk_start:chunk_start + 25]
            aliases = original_keys[chunk_start:chunk_start + 25]
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

            for contract, alias in zip(qualified, aliases):
                self._subscribe_contract(contract, alias_key=alias)

    def _subscribe_contract(self, contract: Contract, alias_key: str = "") -> None:
        key = self._contract_key(contract) or alias_key
        symbol = (getattr(contract, "symbol", "") or alias_key).strip().upper()
        if not key:
            return
        with self._lock:
            if key in self._subscribed_contracts:
                return
        try:
            self.ib.reqMktData(contract, "", False, False)
            with self._lock:
                self._subscribed_contracts[key] = contract
                self._contract_cache[key] = contract
                if alias_key:
                    self._contract_cache[alias_key] = contract
                if symbol:
                    self._symbol_to_key[symbol] = key
                    self._contract_cache[symbol] = contract
            logger.info("Subscribed IBKR market data: %s", symbol or key)
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
                    self.ib.reqMarketDataType(2)
                except Exception:
                    logger.debug("Could not request IBKR frozen snapshot data type", exc_info=True)
                ticker = self.ib.reqMktData(contract, "", True, False)
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
                    self.ib.reqMarketDataType(1)
                except Exception:
                    logger.debug("Could not restore IBKR live market data type", exc_info=True)
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
            self._latest_ticks.clear()
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

        market_price = _clean_float(ticker.marketPrice() if hasattr(ticker, "marketPrice") else 0.0, 0.0)
        last_price = _positive_price(market_price, getattr(ticker, "last", 0.0), getattr(ticker, "close", 0.0), getattr(ticker, "bid", 0.0), getattr(ticker, "ask", 0.0))
        if last_price <= 0:
            return None

        prev_close = _positive_price(
            getattr(ticker, "prevClose", 0.0),
            getattr(ticker, "close", 0.0),
        )
        change_pct = ((last_price - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0
        tick = {
            "symbol": symbol,
            "tradingsymbol": symbol,
            "instrument_token": con_id or key,
            "conId": con_id or 0,
            "last_price": last_price,
            "volume": int(_clean_float(getattr(ticker, "volume", 0.0), 0.0)),
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
