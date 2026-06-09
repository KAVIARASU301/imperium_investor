# ibkr/core/trading_client.py
"""IBKR TradingClient with Kite-compatible surface methods.

Main integration fixes:
  • Exposes positions() and orders() so existing MainWindow/PositionManager do not
    accidentally receive raw ib_insync objects.
  • Caches qualified contracts to avoid repeated reqContractDetails calls.
  • Keeps place_order() returning a rich dict, while updated MainWindow accepts
    both this dict and older broker order-id strings.
  • ✅ NEW: Subscribes to tradeStatusEvent for real-time order updates
  • ✅ NEW: Polls local ib_insync caches without cross-thread broker requests
  • ✅ NEW: Properly tracks order cancellation state
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from PySide6.QtCore import QObject, Signal, QTimer

from ibkr.utils.account_balance import IBKR_SUMMARY_TAGS, ibkr_summary_tag_matches
from ibkr.utils.account_display import extract_account_display_name
from ibkr.utils.ibkr_price import first_positive_ibkr_price, safe_ibkr_price
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


def _asyncio_loop_is_running() -> bool:
    """Return True when synchronous ib_insync helpers would re-enter a live loop."""
    try:
        return asyncio.get_running_loop().is_running()
    except RuntimeError:
        return False


def _ensure_thread_event_loop() -> None:
    """Ensure current thread has an asyncio event loop for ib_insync sync wrappers."""
    try:
        asyncio.get_running_loop()
        return
    except RuntimeError:
        pass
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _first_price(*values: Any) -> float:
    return first_positive_ibkr_price(*values)


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


def _is_terminal_status(status: Any) -> bool:
    return _normalize_status(status) in (IBKR_FAILURE_STATUSES | IBKR_SUCCESS_STATUSES)


def _merge_order_snapshot(existing: Optional[Dict[str, Any]], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a fresh IBKR order row without regressing execution state.

    IBKR can deliver execution/fill events before the corresponding Trade
    ``orderStatus`` object is updated.  Later polling of ``ib.trades()`` may
    therefore return the same order as Submitted with zero fills.  Keep the
    best-known terminal/fill information so UI refreshes do not move an already
    executed order back to pending.
    """
    if not existing:
        return dict(incoming or {})

    merged = {**existing, **(incoming or {})}
    existing_status = _normalize_status(existing.get("status") or existing.get("raw_status"))
    incoming_status = _normalize_status((incoming or {}).get("status") or (incoming or {}).get("raw_status"))
    existing_filled = int(_safe_float(existing.get("filled_quantity"), 0.0))
    incoming_filled = int(_safe_float((incoming or {}).get("filled_quantity"), 0.0))
    quantity = int(_safe_float(merged.get("quantity"), 0.0))

    best_filled = max(existing_filled, incoming_filled)
    if best_filled > 0:
        merged["filled_quantity"] = best_filled
        if quantity > 0:
            merged["pending_quantity"] = max(quantity - best_filled, 0)

    existing_avg = _safe_float(existing.get("average_price"), 0.0)
    incoming_avg = _safe_float((incoming or {}).get("average_price"), 0.0)
    if existing_avg > 0 and incoming_avg <= 0:
        merged["average_price"] = existing_avg
    if _safe_float(existing.get("price"), 0.0) > 0 and _safe_float((incoming or {}).get("price"), 0.0) <= 0:
        merged["price"] = existing.get("price")

    stale_incoming = (
        _is_terminal_status(existing_status)
        and not _is_terminal_status(incoming_status)
        and incoming_filled <= existing_filled
    )
    if stale_incoming:
        merged["status"] = existing.get("status", existing_status)
        merged["raw_status"] = existing.get("raw_status", existing_status)
        merged["pending_quantity"] = existing.get("pending_quantity", merged.get("pending_quantity", 0))
        merged["filled_quantity"] = existing_filled
        if existing_avg > 0:
            merged["average_price"] = existing_avg
        if existing.get("status_message") and not (incoming or {}).get("status_message"):
            merged["status_message"] = existing.get("status_message")
    elif quantity > 0 and best_filled >= quantity:
        merged["status"] = "COMPLETE"
        merged["raw_status"] = "Filled"
        merged["pending_quantity"] = 0

    return merged


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
    avg = _safe_float(getattr(pos, "avgCost", getattr(pos, "averageCost", 0.0)), 0.0)
    con_id = int(getattr(contract, "conId", 0) or 0) if contract is not None else 0
    last_price = _safe_float(getattr(pos, "marketPrice", 0.0), 0.0)
    unrealized = _safe_float(getattr(pos, "unrealizedPNL", getattr(pos, "unrealizedPnL", 0.0)), 0.0)
    realized = _safe_float(getattr(pos, "realizedPNL", getattr(pos, "realizedPnL", 0.0)), 0.0)
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
        "last_price": last_price,
        "pnl": unrealized,
        "unrealized_pnl": unrealized,
        "realized_pnl": realized,
        "currency": getattr(contract, "currency", "USD") if contract else "USD",
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
    trigger_price = safe_ibkr_price(getattr(order, "auxPrice", 0.0), 0.0)
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


def _apply_execution_to_order_row(data: Dict[str, Any], fill: Any = None, *, execution: Any = None) -> Dict[str, Any]:
    """Overlay an IBKR execution report on an app order row.

    Official TWS API execution reports are the source that TWS uses for its
    Trades tab.  They can arrive, or be requested, even when the in-memory
    ``Trade.orderStatus`` snapshot still says Submitted/PendingSubmit.
    """
    execution = execution if execution is not None else getattr(fill, "execution", None)
    if execution is None:
        return data

    quantity = int(_safe_float(data.get("quantity"), 0.0))
    exec_order_id = str(getattr(execution, "orderId", "") or "").strip()
    exec_perm_id = str(getattr(execution, "permId", "") or "").strip()
    exec_id = str(getattr(execution, "execId", "") or "").strip()
    cum_qty = int(_safe_float(getattr(execution, "cumQty", 0), 0.0))
    shares = int(_safe_float(getattr(execution, "shares", 0), 0.0))
    previous_filled = int(_safe_float(data.get("filled_quantity"), 0.0))
    filled_quantity = max(previous_filled, cum_qty, shares)
    avg_price = _first_price(
        getattr(execution, "avgPrice", 0.0),
        getattr(execution, "price", 0.0),
        data.get("average_price"),
    )

    if exec_order_id and not data.get("order_id"):
        data["order_id"] = exec_order_id
    if exec_perm_id and not data.get("perm_id"):
        data["perm_id"] = exec_perm_id
    if exec_id:
        data["exec_id"] = exec_id
    if filled_quantity > 0:
        data["filled_quantity"] = filled_quantity
        data["pending_quantity"] = (
            max(quantity - filled_quantity, 0)
            if quantity > 0
            else int(data.get("pending_quantity") or 0)
        )
        data["average_price"] = avg_price
        if avg_price > 0 and not data.get("price"):
            data["price"] = avg_price
        if quantity <= 0 or filled_quantity >= quantity:
            data["status"] = "COMPLETE"
            data["raw_status"] = "Filled"
            data["pending_quantity"] = 0
        elif _normalize_status(data.get("status")) in {"UNKNOWN", "PENDING", "OPEN"}:
            data["status"] = "OPEN"
            data["raw_status"] = "PartiallyFilled"
    return data


def _convert_trade_with_fill(trade: Any, fill: Any = None) -> Dict[str, Any]:
    """Convert an IBKR trade plus execution fill into the app order schema.

    IBKR/TWS can report fast market-order executions via ``execDetailsEvent`` or
    a Trade ``fillEvent`` before the corresponding ``orderStatusEvent`` updates
    the local ``Trade.orderStatus`` object.  Relying only on orderStatus then
    leaves the UI tracking an order as OPEN/PENDING even though TWS has already
    executed it.  This helper overlays execution fields on top of the normal
    trade conversion so every execution signal can advance the app lifecycle.
    """
    return _apply_execution_to_order_row(_convert_trade(trade), fill)


def _convert_execution_fill(fill: Any) -> Dict[str, Any]:
    """Convert a broker execution report (TWS Trades tab row) to app schema."""
    execution = getattr(fill, "execution", fill)
    contract = getattr(fill, "contract", None)
    symbol = str(getattr(contract, "symbol", "") or "").upper()
    shares = int(_safe_float(getattr(execution, "shares", 0), 0.0))
    cum_qty = int(_safe_float(getattr(execution, "cumQty", 0), 0.0))
    filled_quantity = max(cum_qty, shares)
    avg_price = _first_price(getattr(execution, "avgPrice", 0.0), getattr(execution, "price", 0.0))
    order_id = str(getattr(execution, "orderId", "") or "").strip()
    perm_id = str(getattr(execution, "permId", "") or "").strip()
    side = str(getattr(execution, "side", "") or "").upper()
    action = {"BOT": "BUY", "BOUGHT": "BUY", "SLD": "SELL", "SOLD": "SELL"}.get(side, side)
    row = {
        "order_id": order_id or perm_id,
        "perm_id": perm_id,
        "tradingsymbol": symbol,
        "symbol": symbol,
        "exchange": getattr(contract, "exchange", "SMART") if contract else getattr(execution, "exchange", "SMART"),
        "instrument_token": int(getattr(contract, "conId", 0) or 0) if contract else 0,
        "transaction_type": action,
        "order_type": "",
        "quantity": filled_quantity,
        "price": avg_price,
        "trigger_price": 0.0,
        "status": "COMPLETE" if filled_quantity > 0 else "UNKNOWN",
        "raw_status": "Filled" if filled_quantity > 0 else "Execution",
        "status_message": "",
        "filled_quantity": filled_quantity,
        "pending_quantity": 0,
        "average_price": avg_price,
        "timestamp": str(getattr(execution, "time", "") or market_isoformat()),
        "product": "IBKR",
    }
    return _apply_execution_to_order_row(row, execution=execution)


def _apply_commission_report_to_order_row(data: Dict[str, Any], report: Any = None) -> Dict[str, Any]:
    """Overlay IBKR commissionReport details on an app order row."""
    if report is None:
        return data
    exec_id = str(getattr(report, "execId", "") or "").strip()
    if exec_id:
        data["exec_id"] = exec_id
    data["commission"] = _safe_float(getattr(report, "commission", data.get("commission", 0.0)), 0.0)
    data["commission_currency"] = str(getattr(report, "currency", data.get("commission_currency", "")) or "")
    data["realized_pnl"] = _safe_float(getattr(report, "realizedPNL", data.get("realized_pnl", 0.0)), 0.0)
    data["yield"] = _safe_float(getattr(report, "yield_", getattr(report, "yield", data.get("yield", 0.0))), 0.0)
    redemption_date = getattr(report, "yieldRedemptionDate", None)
    if redemption_date not in (None, ""):
        data["yield_redemption_date"] = redemption_date
    return data


def _execution_id(fill: Any = None, *, execution: Any = None) -> str:
    execution = execution if execution is not None else getattr(fill, "execution", fill)
    return str(getattr(execution, "execId", "") or "").strip()


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
        self._ib_events_subscribed = False
        self._pending_cancellation: Dict[str, bool] = {}  # Track orders waiting for cancellation confirmation
        self._execution_order_keys: Dict[str, str] = {}
        self._connection_snapshot_requested = False

        self._subscribe_ib_events()

        # Do not issue blocking IBKR snapshot requests during application startup.
        # The login dialog hands us an already-connected ib_insync object; making
        # synchronous req* calls before Qt's event loop is running can leave the
        # app looking like it crashed with no fresh log lines.  The live event
        # subscriptions above plus the local-cache poll below keep startup safe
        # while still surfacing order changes as IBKR publishes them.
        logger.info("IBKR trading client startup snapshots deferred; using local cache/event sync")

        # Fetch fresh broker orders immediately and periodically
        self._order_poll_timer = QTimer(self)
        self._order_poll_timer.timeout.connect(self._dispatch_order_poll)
        self._order_poll_timer.start(3_000)  # Reduced to 3s for faster sync

    def _subscribe_ib_events(self) -> None:
        """Subscribe to ib_insync real-time order/fill callbacks. Called once."""
        if getattr(self, "_ib_events_subscribed", False) or not self.ib:
            return
        try:
            # Core order events
            self.ib.orderStatusEvent += self._on_ib_order_status
            self.ib.execDetailsEvent += self._on_ib_exec_details
            if hasattr(self.ib, "commissionReportEvent"):
                self.ib.commissionReportEvent += self._on_ib_commission_report
            self.ib.newOrderEvent += self._on_ib_order_event
            self.ib.openOrderEvent += self._on_ib_order_event
            if hasattr(self.ib, "errorEvent"):
                self.ib.errorEvent += self._on_ib_error

            # ✅ NEW: Subscribe to trade status event for real-time updates
            if hasattr(self.ib, "tradeStatusEvent"):
                self.ib.tradeStatusEvent += self._on_ib_trade_status
                logger.info("Subscribed to IBKR tradeStatusEvent")

            # Position updates
            if hasattr(self.ib, "positionEvent"):
                self.ib.positionEvent += self._on_ib_position_event
            if hasattr(self.ib, "updatePortfolioEvent"):
                self.ib.updatePortfolioEvent += self._on_ib_position_event

            self._ib_events_subscribed = True
            logger.info("Subscribed to IBKR real-time order events")
        except Exception as exc:
            logger.warning("Could not subscribe to IBKR order events: %s", exc)

    def _on_ib_order_status(self, trade: Any) -> None:
        """Fires on every order status change: Submitted, Filled, Cancelled, etc."""
        try:
            self._process_trade_update(trade)
        except Exception as exc:
            logger.debug("IBKR orderStatus event error: %s", exc)

    def _on_ib_exec_details(self, trade: Any, fill: Any) -> None:
        """Fires when a fill/execution report arrives from TWS."""
        try:
            if trade is not None:
                self._process_trade_update(trade, fill)
            else:
                self._process_execution_fill(fill)
        except Exception as exc:
            logger.debug("IBKR execDetails event error: %s", exc)

    def _on_ib_commission_report(self, trade: Any, fill: Any, report: Any) -> None:
        """Fires when IBKR publishes final commission/cost details for a fill."""
        try:
            order_id = ""
            if trade is not None:
                self._process_trade_update(trade, fill)
                row = _convert_trade_with_fill(trade, fill) if fill else _convert_trade(trade)
                order_id = str(row.get("order_id") or "").strip()
            if not order_id and fill is not None:
                order_id = self._process_execution_fill(fill)
            if not order_id:
                exec_id = str(getattr(report, "execId", "") or "").strip()
                order_id = getattr(self, "_execution_order_keys", {}).get(exec_id, "")
            if order_id and order_id in self._orders:
                row = _apply_commission_report_to_order_row(dict(self._orders[order_id]), report)
                self._orders[order_id] = row
                self.order_status_updated.emit(dict(row))
        except Exception as exc:
            logger.debug("IBKR commissionReport event error: %s", exc)

    def _on_ib_error(self, req_id: Any, error_code: Any, error_string: Any, contract: Any = None) -> None:
        """Log and surface IBKR API errors, including order rejections/cancels."""
        message = f"IBKR error {error_code}: {error_string}"
        if contract is not None:
            symbol = getattr(contract, "symbol", "")
            if symbol:
                message = f"{message} ({symbol})"
        logger.warning(message)
        try:
            self.error_occurred.emit(message)
        except Exception:
            pass

        order_id = str(req_id or "").strip()
        if order_id and order_id in getattr(self, "_orders", {}):
            row = dict(self._orders[order_id])
            row["status_message"] = message
            code_text = str(error_code or "")
            if code_text in {"201", "202"}:
                row["status"] = "REJECTED" if code_text == "201" else "CANCELLED"
                row["raw_status"] = row["status"]
            self._orders[order_id] = row
            self.order_status_updated.emit(dict(row))

    def _on_ib_order_event(self, trade: Any) -> None:
        """Fires for new-order acknowledgments and open-order updates."""
        try:
            self._process_trade_update(trade)
        except Exception as exc:
            logger.debug("IBKR order event error: %s", exc)

    def _on_ib_trade_status(self, trade: Any) -> None:
        """✅ NEW: Fires on trade status changes from TWS."""
        try:
            logger.debug("IBKR tradeStatusEvent received for order %s", getattr(trade.order, 'orderId', 'unknown') if trade.order else 'unknown')
            self._process_trade_update(trade)
        except Exception as exc:
            logger.debug("IBKR tradeStatus event error: %s", exc)

    def _on_ib_position_event(self, position: Any) -> None:
        """Fires when IBKR publishes a position/portfolio row update."""
        try:
            row = _convert_position(position)
            symbol = row.get("symbol")
            if symbol:
                if row.get("quantity"):
                    self._positions[symbol] = row
                else:
                    self._positions.pop(symbol, None)
            self.position_updated.emit(row)
        except Exception as exc:
            logger.debug("IBKR position event error: %s", exc)

    def _process_trade_update(self, trade: Any, fill: Any = None) -> None:
        """Convert ib_insync Trade → app dict, merge, and emit signal."""
        row = _convert_trade_with_fill(trade, fill) if fill else _convert_trade(trade)
        order_id = str(row.get("order_id") or "").strip()
        if not order_id:
            return
        exec_id = str(row.get("exec_id") or _execution_id(fill) or "").strip()
        if exec_id:
            if not hasattr(self, "_execution_order_keys"):
                self._execution_order_keys = {}
            self._execution_order_keys[exec_id] = order_id

        # ✅ Check if this was a pending cancellation
        if order_id in self._pending_cancellation:
            status = _normalize_status(row.get("status") or row.get("raw_status"))
            if status == "CANCELLED":
                self._pending_cancellation.pop(order_id, None)
                logger.info("Order %s successfully cancelled", order_id)

        existing = self._orders.get(order_id)
        merged = _merge_order_snapshot(existing, row)
        self._orders[order_id] = merged
        self.order_status_updated.emit(dict(merged))

    def _request_connection_order_snapshots(self) -> None:
        """Request one-time IBKR order/execution snapshots after connecting.

        ``orderStatus`` is not guaranteed for every transition, especially fast
        market fills.  On startup/reconnect, ask TWS/Gateway for open orders,
        all open API orders, today's executions, and completed-order records so
        the local cache can recover fills that occurred before our callbacks
        were attached.
        """
        if getattr(self, "_connection_snapshot_requested", False) or not self.is_connected():
            return
        self._connection_snapshot_requested = True
        snapshot_calls = (
            ("reqOpenOrders", ()),
            ("reqAllOpenOrders", ()),
            ("reqExecutions", ()),
            ("reqCompletedOrders", (False,)),
        )
        if _asyncio_loop_is_running():
            asyncio.create_task(self._request_connection_order_snapshots_async(snapshot_calls))
            return

        for method_name, args in snapshot_calls:
            method = getattr(self.ib, method_name, None)
            if not callable(method):
                continue
            try:
                result = method(*args)
                self._process_snapshot_result(method_name, result)
                logger.debug("Requested IBKR connection snapshot via %s", method_name)
            except Exception as exc:
                logger.warning("IBKR %s snapshot request failed: %s", method_name, exc)

    async def _request_connection_order_snapshots_async(self, snapshot_calls: Tuple[Tuple[str, Tuple[Any, ...]], ...]) -> None:
        """Run startup snapshots without blocking an already-running asyncio loop."""
        for method_name, args in snapshot_calls:
            async_method = getattr(self.ib, f"{method_name}Async", None)
            method = async_method if callable(async_method) else getattr(self.ib, method_name, None)
            if not callable(method):
                continue
            try:
                result = method(*args)
                if hasattr(result, "__await__"):
                    result = await result
                self._process_snapshot_result(method_name, result)
                logger.debug("Requested IBKR connection snapshot via %s", method_name)
            except Exception as exc:
                logger.warning("IBKR %s snapshot request failed: %s", method_name, exc)

    def _process_snapshot_result(self, method_name: str, result: Any) -> None:
        """Merge rows returned by one-time startup snapshot requests."""
        if result is None:
            return
        if method_name == "reqExecutions":
            for fill in list(result or []):
                self._process_execution_fill(fill)
            return

        for trade in list(result or []):
            self._process_trade_update(trade)

    def _process_execution_fill(self, fill: Any) -> str:
        """Merge an execution-only fill and emit the normalized order update."""
        row = _convert_execution_fill(fill)
        order_id = str(row.get("order_id") or "").strip()
        if not order_id:
            return ""
        exec_id = str(row.get("exec_id") or _execution_id(fill) or "").strip()
        if exec_id:
            if not hasattr(self, "_execution_order_keys"):
                self._execution_order_keys = {}
            self._execution_order_keys[exec_id] = order_id
        existing = self._orders.get(order_id)
        merged = _merge_order_snapshot(existing, row)
        self._orders[order_id] = merged
        self.order_status_updated.emit(dict(merged))
        return order_id

    # ------------------------------------------------------------------
    # Active order polling from the local ib_insync cache
    # ------------------------------------------------------------------
    def _dispatch_order_poll(self) -> None:
        if not self.is_connected():
            return
        try:
            fresh_orders = self._fetch_fresh_orders_from_broker()
            self._on_orders_polled(fresh_orders)
        except Exception as exc:
            logger.warning("Order poll failed: %s", exc)

    def _fetch_fresh_orders_from_broker(self) -> List[Dict[str, Any]]:
        """Read fresh order state from ib_insync's local trade cache only.

        ib_insync owns an asyncio socket/event loop that must not be driven from
        QThreadPool workers.  Avoid proactive broker requests such as
        reqAllOpenOrders() or reqExecutions() here; the subscribed live events
        keep ``ib.trades()`` and each trade's fills up to date.
        """
        try:
            if not self.ib:
                return []

            fresh_orders: Dict[str, Dict[str, Any]] = {}
            cache_sources = []
            if hasattr(self.ib, "trades"):
                cache_sources.extend(list(self.ib.trades() or []))

            # ``openTrades()`` is also an ib_insync local cache accessor.  On
            # master-client connections TWS/Gateway can publish open-order rows
            # into this active-order cache before they are visible through the
            # broader completed-trade cache.  Read both local caches so the
            # pending-orders UI lists broker-accepted orders immediately without
            # issuing a network request such as reqOpenOrders().
            if hasattr(self.ib, "openTrades"):
                cache_sources.extend(list(self.ib.openTrades() or []))

            seen_trade_keys: Set[Tuple[str, str]] = set()
            for trade in cache_sources:
                order_id, perm_id = _extract_order_identity(trade)
                trade_key = (order_id, perm_id)
                if trade_key in seen_trade_keys:
                    continue
                seen_trade_keys.add(trade_key)

                row = _convert_trade(trade)
                fills = list(getattr(trade, "fills", []) or [])
                for fill in fills:
                    row = _apply_execution_to_order_row(row, fill)

                order_id = str(row.get("order_id") or "").strip()
                if order_id:
                    fresh_orders[order_id] = row

            logger.debug("Synced %d orders from local IBKR trade caches", len(fresh_orders))
            return list(fresh_orders.values())
        except Exception as exc:
            logger.warning("Fresh order fetch failed: %s", exc)
            return list(self._orders.values())

    def _on_orders_polled(self, fresh_orders: List[Dict[str, Any]]) -> None:
        """Process fresh orders from broker polling."""
        for row in fresh_orders or []:
            order_id = str(row.get("order_id") or "").strip()
            if not order_id:
                continue
            existing = self._orders.get(order_id)
            merged = _merge_order_snapshot(existing, row)
            if merged != existing:
                self._orders[order_id] = merged
                self.order_status_updated.emit(merged)

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
        _ensure_thread_event_loop()
        try:
            if not self.ib:
                return list(self._positions.values())

            # Use ib_insync's synchronized local caches here instead of issuing
            # reqPositions().  Position/portfolio events call back into the app
            # as each row changes; forcing a new broker snapshot from that path
            # causes a feedback loop of reqPositions -> positionEvent ->
            # PositionManager refresh -> reqPositions that can exhaust memory.
            portfolio_rows = []
            if hasattr(self.ib, "portfolio"):
                portfolio_rows = list(self.ib.portfolio() or [])

            rows_by_symbol: Dict[str, Dict[str, Any]] = {}
            for item in portfolio_rows:
                row = _convert_position(item)
                if row.get("symbol") and _safe_float(row.get("quantity"), 0.0) != 0:
                    rows_by_symbol[row["symbol"]] = row

            if hasattr(self.ib, "positions"):
                for pos in list(self.ib.positions() or []):
                    row = _convert_position(pos)
                    symbol = row.get("symbol")
                    if not symbol or _safe_float(row.get("quantity"), 0.0) == 0:
                        continue
                    existing = rows_by_symbol.get(symbol, {})
                    merged = {**row, **existing}
                    for price_key in ("last_price", "pnl", "unrealized_pnl", "realized_pnl"):
                        if _safe_float(merged.get(price_key), 0.0) == 0 and _safe_float(row.get(price_key), 0.0) != 0:
                            merged[price_key] = row.get(price_key)
                    rows_by_symbol[symbol] = merged

            # Always replace the cache, even when IBKR reports no open rows.
            # Otherwise a just-closed position can remain stuck in the table until
            # the app restarts because an empty broker snapshot would keep the old
            # non-empty cache alive.
            self._positions = rows_by_symbol
            return list(self._positions.values())
        except Exception as exc:
            logger.error("Error getting IBKR positions: %s", exc)
            return list(self._positions.values())

    def positions(self) -> List[Dict[str, Any]]:
        return self.get_positions()

    def get_orders(self) -> List[Dict[str, Any]]:
        """Return cached session orders refreshed from fresh broker data."""
        try:
            rows = self._fetch_fresh_orders_from_broker()
            for row in rows:
                order_id = str(row.get("order_id") or "").strip()
                if order_id:
                    self._orders[order_id] = _merge_order_snapshot(self._orders.get(order_id), row)
            return list(self._orders.values())
        except Exception as exc:
            logger.error("get_orders failed: %s", exc)
            return list(self._orders.values())

    def orders(self) -> List[Dict[str, Any]]:
        return self.get_orders()

    def get_margins(self) -> Dict[str, Any]:
        """Return available funds in a Kite-compatible margins shape.

        IBKRTradingClient.get_account_summary() returns::

            {tag: {"value": "<str>", "currency": "USD"}, ...}

        This method normalises those tags into the flat dict that
        AccountManager / extract_available_balance_from_data expect.
        """
        try:
            summary = self.get_account_summary()
            available = 0.0
            net_liq = 0.0
            buying_power = 0.0

            for tag, entry in (summary or {}).items():
                try:
                    value = float(
                        entry.get("value", 0.0) if isinstance(entry, dict) else (entry or 0.0)
                    )
                except (TypeError, ValueError):
                    value = 0.0

                if ibkr_summary_tag_matches(tag, "AvailableFunds") and value > 0:
                    available = value
                elif ibkr_summary_tag_matches(tag, "BuyingPower") and value > 0:
                    buying_power = value
                elif ibkr_summary_tag_matches(tag, "NetLiquidation") and value > 0:
                    net_liq = value

            # Use AvailableFunds as the primary "available to invest" figure.
            # Fall back to BuyingPower, then NetLiquidation.
            balance = available or buying_power or net_liq

            return {
                "available_funds": balance,
                "available_balance": balance,
                "net_liquidation": net_liq,
                "buying_power": buying_power,
                "equity": net_liq,
                "currency": "USD",
            }
        except Exception as exc:
            logger.error("Error building IBKR margins dict: %s", exc)
            return {}

    def margins(self) -> Dict[str, Any]:
        """Kite-compatible alias so AccountManager._get_margins_data() finds this method."""
        return self.get_margins()

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

            contract = self._resolve_stock_contract(
                symbol,
                params.get("exchange", "SMART"),
                params.get("currency", "USD"),
                con_id=params.get("con_id", 0),
                primary_exchange=params.get("primary_exchange", ""),
                allow_qualification=False,
            )
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

            # Build initial result from synchronous trade object state.
            # Real status updates arrive via orderStatusEvent / execDetailsEvent.
            result = self._build_order_result(trade, params, contract)
            order_id = str(result.get("order_id") or "").strip()

            if order_id:
                self._orders[order_id] = result
                result["accepted"] = True
                self.order_status_updated.emit(dict(result))
            else:
                result.update({"accepted": False, "error": "No order ID returned from IBKR"})

            return result
        except Exception as exc:
            logger.error("Error placing IBKR order: %s", exc, exc_info=True)
            return self._order_failure_response(params or kwargs, self._compact_exception(exc))

    def cancel_order(self, order_id: Any = None, **kwargs) -> Dict[str, Any]:
        """✅ IMPROVED: Track cancellation status properly"""
        order_id = order_id if order_id is not None else kwargs.get("order_id")
        try:
            oid = int(order_id)
            for trade in self.ib.trades():
                if int(getattr(trade.order, "orderId", 0) or 0) == oid:
                    # Mark as pending cancellation so we wait for broker confirmation
                    self._pending_cancellation[str(oid)] = True
                    self.ib.cancelOrder(trade.order)

                    # Return with CANCEL_PENDING status
                    return {
                        "status": "CANCEL_PENDING",
                        "order_id": str(order_id),
                        "message": "Cancellation request sent to broker"
                    }
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

    def _normalise_account_summary_rows(self, rows: Any) -> Dict[str, Dict[str, Any]]:
        return {
            item.tag: {"value": item.value, "currency": item.currency}
            for item in (rows or [])
            if getattr(item, "tag", None)
        }

    def get_account_summary(self) -> Dict[str, Any]:
        try:
            if not self.ib:
                return dict(self._account_info)

            # ib_insync's synchronous helpers call asyncio.get_event_loop() in
            # the current thread. Account refreshes run from Qt thread-pool
            # workers (shown in logs as Dummy-*), which do not have a default
            # loop on modern Python versions. Create one before calling sync
            # account helpers so ib_insync does not create an un-awaited coroutine
            # and then fail with "There is no current event loop".
            if _asyncio_loop_is_running():
                logger.debug("Using cached IBKR account summary inside active asyncio loop")
                return dict(self._account_info)
            _ensure_thread_event_loop()

            try:
                account_info = self._normalise_account_summary_rows(self.ib.accountSummary())
            except Exception as exc:
                logger.warning("Unable to read cached IBKR accountSummary: %s", exc)
                account_info = {}

            if not account_info and hasattr(self.ib, "reqAccountSummary"):
                for args in (("All", IBKR_SUMMARY_TAGS), ("", IBKR_SUMMARY_TAGS), ()):  # pragma: no branch
                    try:
                        account_info = self._normalise_account_summary_rows(
                            self.ib.reqAccountSummary(*args)
                        )
                    except TypeError:
                        continue
                    except Exception as exc:
                        logger.warning("Unable to request fresh IBKR account summary: %s", exc)
                        break
                    if account_info:
                        break

            if not account_info and hasattr(self.ib, "accountValues"):
                try:
                    account_info = self._normalise_account_summary_rows(self.ib.accountValues())
                except Exception as exc:
                    logger.warning("Unable to read IBKR account values: %s", exc)
                    account_info = {}

            if account_info:
                self._account_info = account_info
            return dict(self._account_info)
        except Exception as exc:
            logger.error("Error getting IBKR account summary: %s", exc)
            return dict(self._account_info)

    def is_connected(self) -> bool:
        connected = bool(self.ib and self.ib.isConnected())
        if connected and not getattr(self, "_connected", False):
            self._on_reconnected()
        self._connected = connected
        return connected

    def _on_reconnected(self) -> None:
        self._ib_events_subscribed = False
        self._subscribe_ib_events()
        self.connection_status_changed.emit(True)

    def disconnect(self) -> None:
        try:
            self._order_poll_timer.stop()
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

    def _position_contract_metadata(self, symbol: str) -> Dict[str, Any]:
        """Return cached IBKR contract metadata for a held position.

        Position exits frequently originate from the chart/header where the order
        ticket may only know the symbol.  Reusing the portfolio/position conId
        avoids a synchronous reqContractDetails round trip on the GUI thread just
        before placeOrder().
        """
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return {}

        positions_cache = getattr(self, "_positions", None)
        if positions_cache is None:
            positions_cache = {}
            self._positions = positions_cache

        cached_position = dict(positions_cache.get(symbol, {}) or {})
        if cached_position:
            return cached_position

        if not self.ib:
            return {}

        rows: List[Dict[str, Any]] = []
        for source_name in ("portfolio", "positions"):
            source = getattr(self.ib, source_name, None)
            if not callable(source):
                continue
            try:
                rows.extend(_convert_position(row) for row in list(source() or []))
            except Exception:
                logger.debug("Unable to inspect IBKR %s cache for %s", source_name, symbol, exc_info=True)

        for row in rows:
            if str(row.get("symbol") or row.get("tradingsymbol") or "").upper() == symbol:
                if _safe_float(row.get("quantity"), 0.0) != 0:
                    self._positions[symbol] = row
                return row
        return {}

    def _prepare_order_params(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        symbol = str(kwargs.get("symbol") or kwargs.get("tradingsymbol") or "").strip().upper()
        action = str(kwargs.get("action") or kwargs.get("transaction_type") or "BUY").strip().upper()
        quantity = int(float(kwargs.get("quantity") or kwargs.get("qty") or 0))
        raw_type = kwargs.get("order_type") or kwargs.get("orderType") or "MARKET"
        order_type = _normalize_order_type(raw_type)
        position_meta = self._position_contract_metadata(symbol)
        con_id = int(_safe_float(
            kwargs.get("con_id")
            or kwargs.get("conId")
            or kwargs.get("instrument_token")
            or position_meta.get("conId")
            or position_meta.get("instrument_token")
            or 0,
            0.0,
        ))
        return {
            "symbol": symbol,
            "action": "SELL" if action == "SELL" else "BUY",
            "quantity": quantity,
            "order_type": order_type,
            "limit_price": kwargs.get("limit_price") or kwargs.get("price"),
            "stop_price": kwargs.get("stop_price") or kwargs.get("trigger_price") or kwargs.get("triggerPrice"),
            "exchange": kwargs.get("exchange") or position_meta.get("exchange") or "SMART",
            "currency": kwargs.get("currency") or position_meta.get("currency") or "USD",
            "con_id": con_id,
            "primary_exchange": str(
                kwargs.get("primary_exchange")
                or kwargs.get("primaryExchange")
                or kwargs.get("primaryExch")
                or position_meta.get("primary_exchange")
                or position_meta.get("primaryExchange")
                or position_meta.get("exchange")
                or ""
            ).strip().upper(),
            "time_in_force": kwargs.get("time_in_force") or kwargs.get("validity") or "DAY",
            "outside_rth": bool(kwargs.get("outside_rth") or kwargs.get("outsideRth") or False),
        }

    def _resolve_stock_contract(
        self,
        symbol: str,
        exchange: str = "SMART",
        currency: str = "USD",
        con_id: int = 0,
        primary_exchange: str = "",
        allow_qualification: bool = True,
    ) -> Optional[Any]:
        symbol = str(symbol or "").strip().upper()
        con_id = int(_safe_float(con_id, 0.0))
        cache_key = str(con_id) if con_id > 0 else symbol
        if not symbol or Stock is None:
            return None
        if cache_key in self._contract_cache:
            return self._contract_cache[cache_key]
        if symbol in self._contract_cache:
            return self._contract_cache[symbol]

        exchange = "SMART" if exchange in {"", "NASDAQ", "NYSE", "ARCA", "AMEX", "BATS"} else exchange
        primary_exchange = str(primary_exchange or "").strip().upper()

        if con_id > 0 and Contract is not None:
            resolved = Contract(
                secType="STK",
                conId=con_id,
                symbol=symbol,
                exchange=exchange or "SMART",
                currency=currency or "USD",
                primaryExchange=primary_exchange,
            )
        else:
            contract = Stock(symbol, exchange or "SMART", currency or "USD", primaryExchange=primary_exchange)
            if not allow_qualification or _asyncio_loop_is_running():
                # Order placement can be invoked from the Qt/ib_insync loop.
                # Calling qualifyContracts there blocks and raises
                # "This event loop is already running", leaving the UI frozen
                # until shutdown. IBKR can still accept a symbol-only stock
                # contract, while any available conId path above remains fully
                # qualified and non-blocking.
                reason = (
                    "order path requested non-blocking resolution"
                    if not allow_qualification
                    else "an event loop is already running"
                )
                logger.info(
                    "Skipping synchronous IBKR contract qualification for %s because %s",
                    symbol,
                    reason,
                )
                resolved = contract
            else:
                try:
                    qualified = self.ib.qualifyContracts(contract)
                    resolved = qualified[0] if qualified else contract
                except Exception as exc:
                    logger.warning("IBKR contract qualification failed for %s: %s", symbol, exc)
                    resolved = contract

        self._contract_cache[symbol] = resolved
        resolved_con_id = int(getattr(resolved, "conId", 0) or 0)
        if resolved_con_id:
            self._contract_cache[str(resolved_con_id)] = resolved
        return resolved

    def __getattr__(self, name: str):
        if self.ib and hasattr(self.ib, name):
            return getattr(self.ib, name)
        raise AttributeError(f"{self.__class__.__name__!r} object has no attribute {name!r}")
