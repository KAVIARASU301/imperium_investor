# ibkr/core/position_manager.py
"""Broker-neutral position/order lifecycle manager for IBKR/Kite-style clients."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set

from PySide6.QtCore import QObject, Signal, QTimer

from ibkr.utils.ibkr_price import first_positive_ibkr_price, safe_ibkr_price
from ibkr.utils.sounds import play_entry_exit
from ibkr.widgets.status_bar import show_order_completed, show_order_failed

logger = logging.getLogger(__name__)


@dataclass
class Position:
    symbol: str
    quantity: int
    avg_price: float
    token: int
    ltp: float = 0.0
    pnl: float = 0.0
    product: str = "IBKR"


class PositionManager(QObject):
    positions_updated = Signal(list)
    partial_fill_symbols_updated = Signal(object)
    day_pnl_updated = Signal(float)
    show_notification = Signal(str, str)

    def __init__(self, trader: Any, main_window=None, trade_logger=None):
        super().__init__(main_window)
        self.trader = trader
        self.main_window = main_window
        self.trade_logger = trade_logger
        self.tracking_orders: Dict[str, Dict[str, Any]] = {}
        self.order_check_timer = QTimer(self)
        self.order_check_timer.setInterval(1000)
        self.order_check_timer.timeout.connect(self._check_pending_orders)
        self.safety_timer: Optional[QTimer] = None
        self.live_sync_timer: Optional[QTimer] = None
        self._pending_position_refresh_reason = "broker_position_update"
        self._position_refresh_timer = QTimer(self)
        self._position_refresh_timer.setSingleShot(True)
        self._position_refresh_timer.timeout.connect(self._flush_scheduled_position_refresh)

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------
    def fetch_positions_from_broker(self, reason: str = "manual") -> None:
        try:
            logger.info("Fetching positions from broker - Reason: %s", reason)
            raw_payload = self._broker_positions()
            raw_positions = self._extract_position_rows(raw_payload)

            positions: List[Position] = []
            for row in raw_positions:
                position = self._normalize_position(row)
                if position.quantity != 0:
                    positions.append(position)

            self.positions_updated.emit(positions)
            self.partial_fill_symbols_updated.emit(set())
            self.day_pnl_updated.emit(sum(p.pnl for p in positions))
            logger.info("Sent %d positions to table", len(positions))
        except Exception as exc:
            logger.error("Failed to fetch positions: %s", exc, exc_info=True)
            self.show_notification.emit(f"Failed to fetch positions: {exc}", "error")

    def fetch_positions_from_kite(self, reason: str = "manual") -> None:
        self.fetch_positions_from_broker(reason)

    def _broker_positions(self) -> Any:
        if hasattr(self.trader, "get_positions"):
            return self.trader.get_positions()
        if hasattr(self.trader, "positions"):
            return self.trader.positions()
        return []

    @staticmethod
    def _extract_position_rows(payload: Any) -> List[Any]:
        if isinstance(payload, dict):
            return list(payload.get("net") or payload.get("positions") or [])
        if isinstance(payload, list):
            return payload
        if isinstance(payload, tuple):
            return list(payload)
        return []

    # ------------------------------------------------------------------
    # Order tracking
    # ------------------------------------------------------------------
    def start_tracking_order(self, order_id: Any, order_data: Dict[str, Any]) -> None:
        order_id = str(order_id or "").strip()
        if not order_id:
            logger.warning("Cannot track order without order_id: %s", order_data)
            return

        normalized = self._normalize_order({**(order_data or {}), "order_id": order_id})
        self.tracking_orders[order_id] = {**(order_data or {}), **normalized}

        symbol = normalized.get("tradingsymbol", "")
        quantity = normalized.get("quantity", 0)
        price = normalized.get("price", 0.0)
        tx_type = normalized.get("transaction_type", "")
        price_text = f" @ {price:.2f}" if price else ""
        self.show_notification.emit(f"⏳ {tx_type} {quantity} {symbol}{price_text} - Pending", "pending")

        if not self.order_check_timer.isActive():
            self.order_check_timer.start()

    def _check_pending_orders(self) -> None:
        if not self.tracking_orders:
            self.order_check_timer.stop()
            return

        try:
            broker_orders = self._broker_orders()
            normalized_orders = [self._normalize_order(order) for order in broker_orders]
            by_id = {str(order.get("order_id")): order for order in normalized_orders if order.get("order_id")}

            completed: List[str] = []
            for order_id in list(self.tracking_orders):
                broker_order = by_id.get(str(order_id))
                if not broker_order:
                    continue
                status = str(broker_order.get("status", "UNKNOWN")).upper()
                if status in {"COMPLETE", "FILLED", "CANCELLED", "REJECTED", "FAILED", "INACTIVE"}:
                    self._handle_order_completion(order_id, broker_order, status)
                    completed.append(order_id)

            for order_id in completed:
                self.tracking_orders.pop(order_id, None)

            if not self.tracking_orders:
                self.order_check_timer.stop()
        except Exception as exc:
            logger.error("Error checking order status: %s", exc, exc_info=True)

    def _broker_orders(self) -> List[Any]:
        if hasattr(self.trader, "get_orders"):
            return list(self.trader.get_orders() or [])
        if hasattr(self.trader, "orders"):
            return list(self.trader.orders() or [])
        return []

    def _handle_order_completion(self, order_id: str, broker_order: Dict[str, Any], status: str) -> None:
        symbol = broker_order.get("tradingsymbol") or broker_order.get("symbol") or ""
        tx_type = str(broker_order.get("transaction_type", "")).upper()
        tracked_order = self.tracking_orders.get(str(order_id), {})
        is_exit = bool(tracked_order.get("_is_exit_order")) or tx_type == "SELL"

        try:
            if status in {"COMPLETE", "FILLED"}:
                show_order_completed(symbol, "")
                play_entry_exit()
                self._sync_chart_position_line(symbol, tx_type, broker_order, is_exit)
                QTimer.singleShot(250, lambda: self.fetch_positions_from_broker("order_completed"))
            elif status in {"REJECTED", "CANCELLED", "FAILED", "INACTIVE"}:
                show_order_failed(f"Order {status.lower()}")
        except Exception as exc:
            logger.error("Error handling order completion: %s", exc, exc_info=True)

    def _sync_chart_position_line(self, symbol: str, tx_type: str, broker_order: Dict[str, Any], is_exit: bool) -> None:
        manager = getattr(self.main_window, "chart_lines_manager", None)
        if not manager or not symbol:
            return

        filled_quantity = int(broker_order.get("filled_quantity") or broker_order.get("quantity") or 0)
        avg_price = float(broker_order.get("average_price") or broker_order.get("price") or 0.0)
        if filled_quantity <= 0 or avg_price <= 0:
            return

        if is_exit:
            manager.remove_position_line(symbol)
            return

        manager.add_position_line(
            symbol=symbol,
            order_type=tx_type,
            quantity=filled_quantity,
            avg_price=avg_price,
        )

    # ------------------------------------------------------------------
    # Normalisation helpers support dicts and ib_insync objects.
    # ------------------------------------------------------------------
    @staticmethod
    def _field(data: Any, *keys: str, default=None):
        if data is None:
            return default
        if isinstance(data, dict):
            for key in keys:
                if key in data and data.get(key) is not None:
                    return data.get(key)
            return default
        for key in keys:
            if hasattr(data, key):
                value = getattr(data, key)
                if value is not None:
                    return value
        return default

    def _normalize_position(self, pos_data: Any) -> Position:
        contract = self._field(pos_data, "contract")
        symbol = str(self._field(pos_data, "tradingsymbol", "symbol", default="") or "").strip().upper()
        if not symbol and contract is not None:
            symbol = str(getattr(contract, "symbol", "") or "").strip().upper()

        quantity = int(float(self._field(pos_data, "quantity", "position", default=0) or 0))
        avg_price = float(self._field(pos_data, "average_price", "avg_price", "avgCost", default=0) or 0)
        token = int(float(self._field(pos_data, "instrument_token", "conid", "conId", default=0) or 0))
        if not token and contract is not None:
            token = int(getattr(contract, "conId", 0) or 0)

        ltp = float(self._field(pos_data, "last_price", "ltp", "market_price", "current_price", default=0) or 0)
        if ltp <= 0 and self.main_window and hasattr(self.main_window, "market_data_worker"):
            worker = getattr(self.main_window, "market_data_worker", None)
            if worker and hasattr(worker, "get_last_price"):
                ltp = float(worker.get_last_price(token or symbol) or 0.0)

        product = str(self._field(pos_data, "product", "product_type", "secType", default="IBKR") or "IBKR")
        pnl_default = (ltp - avg_price) * quantity if ltp and avg_price else 0.0
        pnl = float(self._field(pos_data, "pnl", "unrealized_pnl", "unrealizedPNL", default=pnl_default) or 0.0)
        return Position(symbol=symbol, quantity=quantity, avg_price=avg_price, token=token, ltp=ltp, pnl=pnl, product=product)

    def _normalize_order(self, order_data: Any) -> Dict[str, Any]:
        # ib_insync Trade support
        contract = self._field(order_data, "contract")
        order_obj = self._field(order_data, "order")
        status_obj = self._field(order_data, "orderStatus")
        if order_obj is not None or status_obj is not None:
            symbol = getattr(contract, "symbol", "") if contract is not None else ""
            return {
                "order_id": str(getattr(order_obj, "orderId", "") or getattr(order_obj, "permId", "") or ""),
                "tradingsymbol": str(symbol or "").upper(),
                "quantity": int(float(getattr(order_obj, "totalQuantity", 0) or 0)),
                "price": first_positive_ibkr_price(getattr(order_obj, "lmtPrice", 0), getattr(order_obj, "auxPrice", 0)),
                "transaction_type": str(getattr(order_obj, "action", "") or "").upper(),
                "status": self._normalize_status(getattr(status_obj, "status", "UNKNOWN") if status_obj else "UNKNOWN"),
                "filled_quantity": int(float(getattr(status_obj, "filled", 0) or 0)) if status_obj else 0,
                "average_price": float(getattr(status_obj, "avgFillPrice", 0) or 0) if status_obj else 0.0,
            }

        return {
            "order_id": str(self._field(order_data, "order_id", "id", "permId", default="") or ""),
            "tradingsymbol": str(self._field(order_data, "tradingsymbol", "symbol", default="") or "").upper(),
            "quantity": int(float(self._field(order_data, "quantity", "totalQuantity", "filled_quantity", default=0) or 0)),
            "price": safe_ibkr_price(self._field(order_data, "price", "limit_price", "lmtPrice", "avgFillPrice", default=0), 0.0),
            "transaction_type": str(self._field(order_data, "transaction_type", "action", "side", default="") or "").upper(),
            "status": self._normalize_status(self._field(order_data, "status", "orderStatus", default="UNKNOWN")),
            "filled_quantity": int(float(self._field(order_data, "filled_quantity", "filled", default=0) or 0)),
            "average_price": float(self._field(order_data, "average_price", "avg_fill_price", "avgFillPrice", default=0) or 0),
        }

    @staticmethod
    def _normalize_status(status: Any) -> str:
        text = str(status or "UNKNOWN").upper()
        mapping = {
            "FILLED": "FILLED",
            "COMPLETE": "COMPLETE",
            "SUBMITTED": "OPEN",
            "PRESUBMITTED": "OPEN",
            "PENDINGSUBMIT": "PENDING",
            "APIPENDING": "PENDING",
            "CANCELLED": "CANCELLED",
            "INACTIVE": "INACTIVE",
        }
        return mapping.get(text.replace(" ", ""), text)

    # ------------------------------------------------------------------
    # Chart-line utility methods used elsewhere
    # ------------------------------------------------------------------
    def update_position_line(self, symbol: str, total_quantity: int, avg_price: float, order_type: str) -> None:
        manager = getattr(self.main_window, "chart_lines_manager", None)
        if not manager:
            return
        try:
            manager.remove_position_line(symbol)
            if total_quantity:
                manager.add_position_line(symbol=symbol, order_type=order_type, quantity=abs(total_quantity), avg_price=avg_price)
        except Exception as exc:
            logger.error("Error updating position line for %s: %s", symbol, exc)

    def remove_position_line_for_symbol(self, symbol: str) -> None:
        manager = getattr(self.main_window, "chart_lines_manager", None)
        if manager:
            try:
                manager.remove_position_line(symbol)
            except Exception as exc:
                logger.error("Error removing position line for %s: %s", symbol, exc)

    def on_position_closed_externally(self, symbol: str) -> None:
        self.remove_position_line_for_symbol(symbol)

    def stop_tracking(self) -> None:
        if self.order_check_timer.isActive():
            self.order_check_timer.stop()
        self.tracking_orders.clear()

    def _schedule_position_refresh(self, reason: str = "broker_position_update", delay_ms: int = 350) -> None:
        """Debounce broker position refreshes caused by IBKR events/order fills."""
        self._pending_position_refresh_reason = reason
        if self._position_refresh_timer.isActive():
            return
        self._position_refresh_timer.start(max(0, int(delay_ms)))

    def _flush_scheduled_position_refresh(self) -> None:
        self.fetch_positions_from_broker(self._pending_position_refresh_reason)

    def start_safety_refresh(self, interval_minutes: int = 5) -> None:
        if self.safety_timer is None:
            self.safety_timer = QTimer(self)
            self.safety_timer.timeout.connect(lambda: self.fetch_positions_from_broker("safety_refresh"))
        self.safety_timer.start(max(1, int(interval_minutes)) * 60 * 1000)

    def start_live_sync(self, interval_seconds: int = 5) -> None:
        """Continuously reconcile UI positions with the broker's latest position snapshot."""
        if self.live_sync_timer is None:
            self.live_sync_timer = QTimer(self)
            self.live_sync_timer.timeout.connect(lambda: self.fetch_positions_from_broker("ibkr_live_sync"))
        self.live_sync_timer.start(max(1, int(interval_seconds)) * 1000)

    def stop_live_sync(self) -> None:
        if self.live_sync_timer and self.live_sync_timer.isActive():
            self.live_sync_timer.stop()

    # ------------------------------------------------------------------
    # Real-time order update signal handlers
    # ------------------------------------------------------------------
    def on_ws_order_update(self, update: Any = None, *_args, **_kwargs) -> None:
        if not update:
            return
        order = self._normalize_order(update)
        order_id = str(order.get("order_id", ""))
        status = str(order.get("status", "UNKNOWN")).upper()
        terminal_status = status in {"COMPLETE", "FILLED", "CANCELLED", "REJECTED", "FAILED", "INACTIVE"}

        if terminal_status:
            if order_id and order_id in self.tracking_orders:
                self._handle_order_completion(order_id, order, status)
                self.tracking_orders.pop(order_id, None)
                if not self.tracking_orders:
                    self.order_check_timer.stop()
            else:
                # Orders can fill outside this UI (TWS, mobile, bracket legs, or an
                # order that was submitted before this app started).  A terminal
                # IBKR order event must still reconcile both position tables.
                self._schedule_position_refresh("ibkr_order_update")

    def on_ws_position_update(self, update: Any = None, *_args, **_kwargs) -> None:
        # IBKR positionEvent is per-position and may carry zero quantity for a
        # closed symbol. Fetch the full broker snapshot so removed rows disappear
        # from both embedded and floating tables together.
        self._schedule_position_refresh("ibkr_position_event")

    def on_ws_connected(self, *_args, **_kwargs) -> None:
        self.fetch_positions_from_broker("ibkr_connected")

    def on_ws_disconnected(self, *_args, **_kwargs) -> None:
        return
