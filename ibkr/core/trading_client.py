# ibkr/core/trading_client.py
"""IBKR TradingClient with Kite-compatible surface methods.

Main integration fixes:
  • Exposes positions() and orders() so existing MainWindow/PositionManager do not
    accidentally receive raw ib_insync objects.
  • Caches qualified contracts to avoid repeated reqContractDetails calls.
  • Keeps place_order() returning a rich dict, while updated MainWindow accepts
    both this dict and older broker order-id strings.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Signal, QTimer

from ibkr.utils.account_display import extract_account_display_name
from ibkr.utils.market_time import market_isoformat

try:
    from ib_insync import IB, Contract, Stock, MarketOrder, LimitOrder, StopOrder, StopLimitOrder, Trade, Position, Ticker
    IBKR_AVAILABLE = True
except Exception:  # pragma: no cover
    IB = Contract = Stock = MarketOrder = LimitOrder = StopOrder = StopLimitOrder = Trade = Position = Ticker = None
    IBKR_AVAILABLE = False

try:
    from login_setup.broker_modes import TradingMode
except Exception:  # pragma: no cover
    class TradingMode:
        PAPER = "paper"
        LIVE = "live"

logger = logging.getLogger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _first_price(*values: Any) -> float:
    for value in values:
        number = _safe_float(value, 0.0)
        if number > 0:
            return number
    return 0.0


def _mode_value(mode: Any) -> str:
    return str(getattr(mode, "value", mode) or "paper")


IBKR_FAILURE_STATUSES = {"REJECTED", "FAILED", "INACTIVE", "CANCELLED", "API_CANCELLED", "APICANCELLED"}
IBKR_PENDING_STATUSES = {"PENDING", "PENDING_SUBMIT", "PENDINGSUBMIT", "API_PENDING", "APIPENDING"}
IBKR_OPEN_STATUSES = {"OPEN", "SUBMITTED", "PRESUBMITTED"}
IBKR_SUCCESS_STATUSES = {"COMPLETE", "FILLED"}


def _normalize_status(status: Any) -> str:
    text = str(status or "UNKNOWN").replace(" ", "").replace("_", "").upper()
    mapping = {
        "FILLED": "COMPLETE",
        "SUBMITTED": "OPEN",
        "PRESUBMITTED": "OPEN",
        "PENDINGSUBMIT": "PENDING",
        "APIPENDING": "PENDING",
        "PENDINGCANCEL": "CANCEL_PENDING",
        "APICANCELLED": "CANCELLED",
        "CANCELLED": "CANCELLED",
        "INACTIVE": "REJECTED",
    }
    return mapping.get(text, text)


def _is_failure_status(status: Any) -> bool:
    return _normalize_status(status) in IBKR_FAILURE_STATUSES


def _is_pending_or_open_status(status: Any) -> bool:
    return _normalize_status(status) in (IBKR_PENDING_STATUSES | IBKR_OPEN_STATUSES | {"CANCEL_PENDING"})


def _normalize_order_type(order_type: Any) -> str:
    text = str(order_type or "MKT").replace(" ", "_").replace("-", "_").upper()
    return {"MARKET": "MARKET", "MKT": "MARKET", "LIMIT": "LIMIT", "LMT": "LIMIT", "STP": "STOP", "STOP": "STOP", "SLM": "STOP", "SL_M": "STOP", "STP_LMT": "STOP_LIMIT", "SL": "STOP_LIMIT"}.get(text, text)


def _convert_position(pos: Any) -> Dict[str, Any]:
    contract = getattr(pos, "contract", None)
    symbol = str(getattr(contract, "symbol", "") or "").upper()
    qty = int(_safe_float(getattr(pos, "position", 0), 0.0))
    avg = _safe_float(getattr(pos, "avgCost", 0.0), 0.0)
    con_id = int(getattr(contract, "conId", 0) or 0) if contract is not None else 0
    return {
        "tradingsymbol": symbol,
        "symbol": symbol,
        "exchange": getattr(contract, "primaryExchange", "") or getattr(contract, "exchange", "SMART") if contract else "SMART",
        "instrument_token": con_id,
        "conId": con_id,
        "quantity": qty,
        "average_price": avg,
        "avg_price": avg,
        "product": getattr(contract, "secType", "STK") if contract else "STK",
        "last_price": 0.0,
        "pnl": 0.0,
        "currency": "USD",
    }


def _extract_trade_message(trade: Any) -> str:
    messages: List[str] = []
    for attr in ("advancedError", "advancedErrorOverride"):
        value = getattr(trade, attr, None)
        if value:
            messages.append(str(value))

    for entry in list(getattr(trade, "log", []) or [])[-5:]:
        message = getattr(entry, "message", None)
        if message:
            messages.append(str(message))
        error_code = getattr(entry, "errorCode", None)
        if error_code not in (None, 0, "0"):
            messages.append(f"IBKR error {error_code}")

    # Preserve order while removing duplicates/blank fragments.
    seen = set()
    unique: List[str] = []
    for message in messages:
        message = " ".join(str(message).split())
        if message and message not in seen:
            unique.append(message)
            seen.add(message)
    return "; ".join(unique)


def _extract_order_identity(trade: Any) -> Tuple[str, str]:
    order = getattr(trade, "order", None)
    order_id = str(getattr(order, "orderId", "") or "").strip() if order else ""
    perm_id = str(getattr(order, "permId", "") or "").strip() if order else ""
    return order_id, perm_id


def _convert_trade(trade: Any) -> Dict[str, Any]:
    order = getattr(trade, "order", None)
    status = getattr(trade, "orderStatus", None)
    contract = getattr(trade, "contract", None)
    symbol = str(getattr(contract, "symbol", "") or "").upper()
    order_type = _normalize_order_type(getattr(order, "orderType", ""))
    price = _first_price(getattr(order, "lmtPrice", 0.0), getattr(status, "avgFillPrice", 0.0))
    trigger_price = _safe_float(getattr(order, "auxPrice", 0.0), 0.0)
    quantity = int(_safe_float(getattr(order, "totalQuantity", 0), 0.0))
    filled_quantity = int(_safe_float(getattr(status, "filled", 0), 0.0)) if status else 0
    remaining_quantity = int(_safe_float(getattr(status, "remaining", max(quantity - filled_quantity, 0)), 0.0)) if status else max(quantity - filled_quantity, 0)
    return {
        "order_id": str(getattr(order, "orderId", "") or getattr(order, "permId", "")),
        "perm_id": str(getattr(order, "permId", "") or ""),
        "tradingsymbol": symbol,
        "symbol": symbol,
        "exchange": getattr(contract, "exchange", "SMART") if contract else "SMART",
        "instrument_token": int(getattr(contract, "conId", 0) or 0) if contract else 0,
        "transaction_type": str(getattr(order, "action", "") or "").upper(),
        "order_type": order_type,
        "quantity": quantity,
        "price": price,
        "trigger_price": trigger_price,
        "status": _normalize_status(getattr(status, "status", "UNKNOWN") if status else "UNKNOWN"),
        "raw_status": str(getattr(status, "status", "UNKNOWN") if status else "UNKNOWN"),
        "status_message": _extract_trade_message(trade),
        "filled_quantity": filled_quantity,
        "pending_quantity": remaining_quantity,
        "average_price": _safe_float(getattr(status, "avgFillPrice", 0.0), 0.0) if status else 0.0,
        "timestamp": market_isoformat(),
        "product": "IBKR",
    }


def _convert_ticker(ticker: Any) -> Dict[str, Any]:
    contract = getattr(ticker, "contract", None)
    symbol = str(getattr(contract, "symbol", "") or "").upper()
    con_id = int(getattr(contract, "conId", 0) or 0) if contract else 0
    # IBKR architecture: streaming reqMktData() fields are only used as
    # last/close prices for LTP-style UI updates; bid/ask/marketPrice must not
    # feed chart candles or scanner/watchlist prices.
    last_price = _first_price(
        getattr(ticker, "last", 0.0),
        getattr(ticker, "delayedLast", 0.0),
        getattr(ticker, "close", 0.0),
        getattr(ticker, "delayedClose", 0.0),
    )

    return {
        "tradingsymbol": symbol,
        "symbol": symbol,
        "instrument_token": con_id,
        "last_price": last_price,
        "volume": int(_safe_float(getattr(ticker, "volume", 0) or getattr(ticker, "delayedVolume", 0), 0.0)),
        "bid": _safe_float(getattr(ticker, "bid", 0.0) or getattr(ticker, "delayedBid", 0.0), 0.0),
        "ask": _safe_float(getattr(ticker, "ask", 0.0) or getattr(ticker, "delayedAsk", 0.0), 0.0),
        "ohlc": {
            "open": _safe_float(getattr(ticker, "open", 0.0) or getattr(ticker, "delayedOpen", 0.0), 0.0),
            "high": _safe_float(getattr(ticker, "high", 0.0) or getattr(ticker, "delayedHigh", 0.0), 0.0),
            "low": _safe_float(getattr(ticker, "low", 0.0) or getattr(ticker, "delayedLow", 0.0), 0.0),
            "close": _safe_float(getattr(ticker, "close", 0.0) or getattr(ticker, "delayedClose", 0.0), 0.0),
        },
    }

class IBKRTradingClient(QObject):
    order_status_updated = Signal(dict)
    position_updated = Signal(dict)
    market_data_updated = Signal(dict)
    account_updated = Signal(dict)
    connection_status_changed = Signal(bool)
    error_occurred = Signal(str)

    def __init__(self, ib_client: Any, trading_mode: Any = None):
        super().__init__()
        self.ib = ib_client
        self.trading_mode = trading_mode or getattr(TradingMode, "PAPER", "paper")
        self._connected = bool(self.ib and self.ib.isConnected())
        self._account_info: Dict[str, Dict[str, Any]] = {}
        self._positions: Dict[str, Dict[str, Any]] = {}
        self._orders: Dict[str, Dict[str, Any]] = {}
        self._contract_cache: Dict[str, Any] = {}
        self._market_data_subscriptions: Dict[str, Dict[str, Any]] = {}
        self._subscribed_symbols: set[str] = set()
        self._recent_order_errors: Dict[str, str] = {}
        self._events_attached = False

        self._setup_event_handlers()
        self.heartbeat_timer = QTimer(self)
        self.heartbeat_timer.timeout.connect(self._check_connection)
        self.heartbeat_timer.start(30_000)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _setup_event_handlers(self) -> None:
        if not self.ib or self._events_attached:
            return
        try:
            self.ib.orderStatusEvent += self._on_order_status
            self.ib.positionEvent += self._on_position_update
            self.ib.accountValueEvent += self._on_account_update
            self.ib.pendingTickersEvent += self._on_market_data_update
            self.ib.disconnectedEvent += self._on_disconnected
            if hasattr(self.ib, "errorEvent"):
                self.ib.errorEvent += self._on_ib_error
            self._events_attached = True
        except Exception as exc:
            logger.warning("Failed to attach IBKR event handlers: %s", exc)

    def _detach_event_handlers(self) -> None:
        if not self.ib or not self._events_attached:
            return
        for event_name, handler in (
            ("orderStatusEvent", self._on_order_status),
            ("positionEvent", self._on_position_update),
            ("accountValueEvent", self._on_account_update),
            ("pendingTickersEvent", self._on_market_data_update),
            ("disconnectedEvent", self._on_disconnected),
            ("errorEvent", self._on_ib_error),
        ):
            try:
                event = getattr(self.ib, event_name)
                event -= handler
            except Exception:
                pass
        self._events_attached = False

    def _on_order_status(self, trade: Any) -> None:
        data = _convert_trade(trade)
        if data.get("order_id"):
            self._orders[str(data["order_id"])] = data
        self.order_status_updated.emit(data)

    def _on_position_update(self, position: Any) -> None:
        data = _convert_position(position)
        if data.get("symbol"):
            self._positions[data["symbol"]] = data
        self.position_updated.emit(data)

    def _on_account_update(self, account_value: Any) -> None:
        tag = getattr(account_value, "tag", "")
        if tag:
            self._account_info[tag] = {
                "value": getattr(account_value, "value", ""),
                "currency": getattr(account_value, "currency", "USD"),
            }
            self.account_updated.emit(self._account_info)

    def _on_market_data_update(self, tickers: List[Any]) -> None:
        for ticker in tickers or []:
            data = _convert_ticker(ticker)
            if data.get("last_price", 0) > 0:
                self.market_data_updated.emit(data)

    def _on_disconnected(self) -> None:
        self._connected = False
        self.connection_status_changed.emit(False)

    def _on_ib_error(self, req_id: Any = None, error_code: Any = None, error_string: Any = None, contract: Any = None) -> None:
        message = " ".join(str(error_string or "").split())
        if not message:
            return

        key = str(req_id or "").strip()
        if key and key != "-1":
            self._recent_order_errors[key] = message

        symbol = str(getattr(contract, "symbol", "") or "").upper() if contract is not None else ""
        full_message = f"{symbol}: {message}" if symbol else message
        logger.warning("IBKR error %s for request %s: %s", error_code, req_id, full_message)
        self.error_occurred.emit(full_message)

    def _check_connection(self) -> None:
        connected = bool(self.ib and self.ib.isConnected())
        if connected != self._connected:
            self._connected = connected
            self.connection_status_changed.emit(connected)

    # ------------------------------------------------------------------
    # Kite-compatible broker surface
    # ------------------------------------------------------------------
    def get_profile(self) -> Dict[str, Any]:
        try:
            accounts = self.ib.managedAccounts() if self.ib else []
            summary = self.get_account_summary()
            profile = {
                "broker": "Interactive Brokers",
                "trading_mode": _mode_value(self.trading_mode),
                "accounts": accounts,
                "account_summary": summary,
                "connection_status": self.is_connected(),
            }
            profile["user_id"] = extract_account_display_name(self, profile) or "Unknown"
            profile["user_name"] = profile["user_id"]
            return profile
        except Exception as exc:
            logger.error("Error getting IBKR profile: %s", exc)
            return {"error": str(exc)}

    def get_positions(self) -> List[Dict[str, Any]]:
        try:
            positions = self.ib.positions() if self.ib else []
            rows = [_convert_position(pos) for pos in positions if _safe_float(getattr(pos, "position", 0), 0) != 0]
            self._positions = {row["symbol"]: row for row in rows if row.get("symbol")}
            return rows
        except Exception as exc:
            logger.error("Error getting IBKR positions: %s", exc)
            return list(self._positions.values())

    def positions(self) -> List[Dict[str, Any]]:
        return self.get_positions()

    def get_orders(self) -> List[Dict[str, Any]]:
        try:
            trades = self.ib.trades() if self.ib else []
            rows = [_convert_trade(trade) for trade in trades]
            for row in rows:
                if row.get("order_id"):
                    self._orders[str(row["order_id"])] = row
            return rows
        except Exception as exc:
            logger.error("Error getting IBKR orders: %s", exc)
            return list(self._orders.values())

    def orders(self) -> List[Dict[str, Any]]:
        return self.get_orders()

    def place_order(self, **kwargs) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        try:
            if not self.is_connected():
                return self._order_failure_response(kwargs, "IBKR client is not connected")

            params = self._prepare_order_params(kwargs)
            symbol = params["symbol"]
            quantity = int(params["quantity"])
            if not symbol or quantity <= 0:
                return self._order_failure_response(params or kwargs, "Invalid symbol or quantity")

            contract = self._resolve_stock_contract(symbol, params.get("exchange", "SMART"), params.get("currency", "USD"))
            if contract is None:
                return self._order_failure_response(params, f"Unable to resolve IBKR contract for {symbol}")

            order_type = params["order_type"]
            action = params["action"]
            if order_type == "MARKET":
                order = MarketOrder(action, quantity)
            elif order_type == "LIMIT":
                limit_price = _safe_float(params.get("limit_price"), 0.0)
                if limit_price <= 0:
                    return self._order_failure_response(params, "Limit price required for limit orders")
                order = LimitOrder(action, quantity, limit_price)
            elif order_type == "STOP":
                stop_price = _safe_float(params.get("stop_price"), 0.0)
                if stop_price <= 0:
                    return self._order_failure_response(params, "Stop price required for stop orders")
                order = StopOrder(action, quantity, stop_price)
            elif order_type == "STOP_LIMIT":
                limit_price = _safe_float(params.get("limit_price"), 0.0)
                stop_price = _safe_float(params.get("stop_price"), 0.0)
                if limit_price <= 0 or stop_price <= 0:
                    return self._order_failure_response(params, "Both stop and limit price are required for stop-limit orders")
                order = StopLimitOrder(action, quantity, limit_price, stop_price)
            else:
                return self._order_failure_response(params, f"Unsupported order type: {order_type}")

            order.tif = params.get("time_in_force", "DAY")
            order.outsideRth = bool(params.get("outside_rth", False))

            trade = self.ib.placeOrder(contract, order)
            if not trade:
                return self._order_failure_response(params, "IBKR did not return a Trade object")

            self._pump_order_updates(trade)
            result = self._build_order_result(trade, params, contract)
            order_id = str(result.get("order_id") or "").strip()
            if order_id:
                error_message = self._recent_order_errors.pop(order_id, "")
                if error_message and not result.get("status_message"):
                    result["status_message"] = error_message

            status_text = str(result.get("status") or "UNKNOWN").upper()
            status_message = str(result.get("status_message") or "").strip()

            if _is_failure_status(status_text):
                reason = status_message or f"IBKR marked order {status_text.lower()}"
                result.update({"accepted": False, "error": reason})
                logger.warning("IBKR order rejected/failed for %s: %s", symbol, reason)
            elif _is_pending_or_open_status(status_text) or status_text in IBKR_SUCCESS_STATUSES:
                result["accepted"] = True
            else:
                # Some IBKR responses have an order id before a status arrives. Treat
                # that as accepted-but-pending so the normal polling/event flow can
                # resolve it instead of showing a false failure to the user.
                result.update({"accepted": bool(order_id), "status": "PENDING" if order_id else status_text})
                if not order_id:
                    result.update({"accepted": False, "error": "IBKR did not provide an order id yet"})

            if order_id:
                self._orders[order_id] = result
                self.order_status_updated.emit(result)
            return result
        except Exception as exc:
            logger.error("Error placing IBKR order: %s", exc, exc_info=True)
            return self._order_failure_response(params or kwargs, self._compact_exception(exc))

    def cancel_order(self, order_id: Any = None, **kwargs) -> Dict[str, Any]:
        order_id = order_id if order_id is not None else kwargs.get("order_id")
        try:
            oid = int(order_id)
            for trade in self.ib.trades():
                if int(getattr(trade.order, "orderId", 0) or 0) == oid:
                    self.ib.cancelOrder(trade.order)
                    return {"status": "CANCELLED", "order_id": str(order_id)}
            return {"error": f"Order {order_id} not found"}
        except Exception as exc:
            logger.error("Error cancelling IBKR order %s: %s", order_id, exc)
            return {"error": str(exc)}

    def modify_order(self, order_id: Any = None, **kwargs) -> Dict[str, Any]:
        order_id = order_id if order_id is not None else kwargs.get("order_id")
        try:
            oid = int(order_id)
            for trade in self.ib.trades():
                if int(getattr(trade.order, "orderId", 0) or 0) == oid:
                    quantity = kwargs.get("quantity")
                    price = kwargs.get("price")
                    trigger_price = kwargs.get("trigger_price")
                    order_type = kwargs.get("order_type")
                    validity = kwargs.get("validity")

                    if quantity is not None:
                        trade.order.totalQuantity = int(quantity)
                    if order_type:
                        normalized_type = _normalize_order_type(order_type)
                        trade.order.orderType = {
                            "MARKET": "MKT",
                            "LIMIT": "LMT",
                            "STOP": "STP",
                            "STOP_LIMIT": "STP LMT",
                            "SL": "STP LMT",
                            "SL-M": "STP",
                        }.get(normalized_type, str(order_type).upper())
                    if price is not None and hasattr(trade.order, "lmtPrice"):
                        trade.order.lmtPrice = float(price)
                    if trigger_price is not None and hasattr(trade.order, "auxPrice"):
                        trade.order.auxPrice = float(trigger_price)
                    if validity:
                        trade.order.tif = str(validity).upper()

                    self.ib.placeOrder(trade.contract, trade.order)
                    return {"status": "MODIFIED", "order_id": str(order_id)}
            return {"error": f"Order {order_id} not found"}
        except Exception as exc:
            logger.error("Error modifying IBKR order %s: %s", order_id, exc)
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------
    def get_instruments(self) -> List[Dict[str, Any]]:
        # Fast seed only; live search resolves conIds on demand.
        return [
            {"tradingsymbol": s, "symbol": s, "name": n, "exchange": e, "instrument_token": 0, "currency": "USD"}
            for s, n, e in [
                ("AAPL", "Apple Inc.", "NASDAQ"), ("MSFT", "Microsoft Corporation", "NASDAQ"),
                ("NVDA", "NVIDIA Corporation", "NASDAQ"), ("AMZN", "Amazon.com Inc.", "NASDAQ"),
                ("GOOGL", "Alphabet Inc.", "NASDAQ"), ("TSLA", "Tesla Inc.", "NASDAQ"),
                ("SPY", "SPDR S&P 500 ETF", "ARCA"), ("QQQ", "Invesco QQQ Trust", "NASDAQ"),
            ]
        ]

    def get_ltp(self, symbol: str) -> float:
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return 0.0
        try:
            contract = self._resolve_stock_contract(symbol)
            if contract is None:
                return 0.0
            ticker = self.ib.ticker(contract)
            price = _convert_ticker(ticker).get("last_price", 0.0) if ticker else 0.0
            if price > 0:
                return float(price)
            tickers = self.ib.reqTickers(contract)
            if tickers:
                return float(_convert_ticker(tickers[0]).get("last_price", 0.0) or 0.0)
        except Exception as exc:
            logger.warning("IBKR LTP fetch failed for %s: %s", symbol, exc)
        return 0.0

    def get_historical_data(self, symbol: str, duration: str = "1 Y", bar_size: str = "1 day") -> List[Dict[str, Any]]:
        try:
            contract = self._resolve_stock_contract(symbol)
            if contract is None:
                return []
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
                keepUpToDate=False,
            )
            return [
                {
                    "date": getattr(bar, "date", None),
                    "open": getattr(bar, "open", 0),
                    "high": getattr(bar, "high", 0),
                    "low": getattr(bar, "low", 0),
                    "close": getattr(bar, "close", 0),
                    "volume": getattr(bar, "volume", 0),
                }
                for bar in (bars or [])
            ]
        except Exception as exc:
            logger.error("Error getting historical data for %s: %s", symbol, exc)
            return []

    def subscribe_market_data(self, symbols: List[str]) -> None:
        for symbol in symbols or []:
            symbol = str(symbol or "").strip().upper()
            if not symbol or symbol in self._subscribed_symbols:
                continue
            try:
                contract = self._resolve_stock_contract(symbol)
                if contract is None:
                    continue
                ticker = self.ib.reqMktData(contract, "", False, False)
                self._market_data_subscriptions[symbol] = {"contract": contract, "ticker": ticker}
                self._subscribed_symbols.add(symbol)
            except Exception as exc:
                logger.error("Error subscribing market data for %s: %s", symbol, exc)

    def unsubscribe_market_data(self, symbols: List[str]) -> None:
        for symbol in symbols or []:
            symbol = str(symbol or "").strip().upper()
            sub = self._market_data_subscriptions.pop(symbol, None)
            if not sub:
                continue
            try:
                self.ib.cancelMktData(sub["contract"])
            except Exception:
                pass
            self._subscribed_symbols.discard(symbol)

    def search_contracts(self, pattern: str) -> List[Dict[str, Any]]:
        pattern = str(pattern or "").strip().upper()
        if not pattern:
            return []
        try:
            matches = self.ib.reqMatchingSymbols(pattern)
        except Exception as exc:
            logger.error("Error searching IBKR contracts for %s: %s", pattern, exc)
            return []

        results: List[Dict[str, Any]] = []
        for match in matches or []:
            contract = getattr(match, "contract", match)
            symbol = str(getattr(contract, "symbol", "") or "").upper()
            if not symbol:
                continue
            con_id = int(getattr(contract, "conId", 0) or 0)
            if con_id:
                self._contract_cache[str(con_id)] = contract
                self._contract_cache[symbol] = contract
            results.append({
                "tradingsymbol": symbol,
                "symbol": symbol,
                "name": getattr(match, "longName", "") or getattr(contract, "localSymbol", ""),
                "exchange": getattr(contract, "primaryExchange", "") or getattr(contract, "exchange", "SMART"),
                "instrument_token": con_id,
                "conId": con_id,
                "currency": getattr(contract, "currency", "USD"),
                "secType": getattr(contract, "secType", "STK"),
            })
        return results

    def get_account_summary(self) -> Dict[str, Any]:
        try:
            summary = self.ib.accountSummary() if self.ib else []
            return {item.tag: {"value": item.value, "currency": item.currency} for item in summary}
        except Exception as exc:
            logger.error("Error getting IBKR account summary: %s", exc)
            return dict(self._account_info)

    def is_connected(self) -> bool:
        return bool(self.ib and self.ib.isConnected())

    def disconnect(self) -> None:
        try:
            self.heartbeat_timer.stop()
            self._detach_event_handlers()
            for symbol in list(self._subscribed_symbols):
                self.unsubscribe_market_data([symbol])
            if self.ib and self.ib.isConnected():
                self.ib.disconnect()
            self._connected = False
        except Exception as exc:
            logger.error("Error disconnecting IBKR client: %s", exc)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _pump_order_updates(self, trade: Any, attempts: int = 5, timeout: float = 0.1) -> None:
        for _ in range(max(1, attempts)):
            status = _normalize_status(getattr(getattr(trade, "orderStatus", None), "status", ""))
            order_id, _ = _extract_order_identity(trade)
            if status in {"OPEN", "PENDING", "COMPLETE", "REJECTED", "CANCELLED"} and order_id:
                return
            try:
                self.ib.waitOnUpdate(timeout=timeout)
            except Exception as exc:
                logger.debug("IBKR waitOnUpdate during order placement failed: %s", exc)
                return

    def _build_order_result(self, trade: Any, params: Dict[str, Any], contract: Any) -> Dict[str, Any]:
        symbol = params.get("symbol", "")
        quantity = int(params.get("quantity") or 0)
        action = params.get("action", "BUY")
        order_type = params.get("order_type", "MARKET")
        result = _convert_trade(trade)
        order_id, perm_id = _extract_order_identity(trade)
        result.update({
            "order_id": str(result.get("order_id") or order_id or perm_id),
            "perm_id": str(result.get("perm_id") or perm_id),
            "symbol": symbol,
            "tradingsymbol": symbol,
            "quantity": quantity,
            "transaction_type": action,
            "order_type": order_type,
            "exchange": getattr(contract, "exchange", "SMART"),
            "product": "IBKR",
            "timestamp": market_isoformat(),
        })
        return result

    def _order_failure_response(self, source: Dict[str, Any], message: str) -> Dict[str, Any]:
        params = source if isinstance(source, dict) else {}
        symbol = str(params.get("symbol") or params.get("tradingsymbol") or "").strip().upper()
        response = {
            "accepted": False,
            "error": str(message or "IBKR order placement failed"),
            "status": "REJECTED",
            "status_message": str(message or "IBKR order placement failed"),
            "symbol": symbol,
            "tradingsymbol": symbol,
            "quantity": int(_safe_float(params.get("quantity") or params.get("qty") or 0, 0.0)),
            "transaction_type": str(params.get("action") or params.get("transaction_type") or "").upper(),
            "order_type": _normalize_order_type(params.get("order_type") or params.get("orderType") or "MARKET"),
            "product": "IBKR",
            "timestamp": market_isoformat(),
        }
        return response

    @staticmethod
    def _compact_exception(exc: Exception) -> str:
        text = " ".join(str(exc or "").split())
        return text or exc.__class__.__name__

    def _prepare_order_params(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        symbol = str(kwargs.get("symbol") or kwargs.get("tradingsymbol") or "").strip().upper()
        action = str(kwargs.get("action") or kwargs.get("transaction_type") or "BUY").strip().upper()
        quantity = int(float(kwargs.get("quantity") or kwargs.get("qty") or 0))
        raw_type = kwargs.get("order_type") or kwargs.get("orderType") or "MARKET"
        order_type = _normalize_order_type(raw_type)
        return {
            "symbol": symbol,
            "action": "SELL" if action == "SELL" else "BUY",
            "quantity": quantity,
            "order_type": order_type,
            "limit_price": kwargs.get("limit_price") or kwargs.get("price"),
            "stop_price": kwargs.get("stop_price") or kwargs.get("trigger_price") or kwargs.get("triggerPrice"),
            "exchange": kwargs.get("exchange") or "SMART",
            "currency": kwargs.get("currency") or "USD",
            "time_in_force": kwargs.get("time_in_force") or kwargs.get("validity") or "DAY",
            "outside_rth": bool(kwargs.get("outside_rth") or kwargs.get("outsideRth") or False),
        }

    def _resolve_stock_contract(self, symbol: str, exchange: str = "SMART", currency: str = "USD") -> Optional[Any]:
        symbol = str(symbol or "").strip().upper()
        if not symbol or Stock is None:
            return None
        if symbol in self._contract_cache:
            return self._contract_cache[symbol]
        exchange = "SMART" if exchange in {"", "NASDAQ", "NYSE", "ARCA", "AMEX", "BATS"} else exchange
        contract = Stock(symbol, exchange or "SMART", currency or "USD")
        try:
            qualified = self.ib.qualifyContracts(contract)
            resolved = qualified[0] if qualified else contract
        except Exception as exc:
            logger.warning("IBKR contract qualification failed for %s: %s", symbol, exc)
            resolved = contract
        self._contract_cache[symbol] = resolved
        con_id = int(getattr(resolved, "conId", 0) or 0)
        if con_id:
            self._contract_cache[str(con_id)] = resolved
        return resolved

    def __getattr__(self, name: str):
        if self.ib and hasattr(self.ib, name):
            return getattr(self.ib, name)
        raise AttributeError(f"{self.__class__.__name__!r} object has no attribute {name!r}")
