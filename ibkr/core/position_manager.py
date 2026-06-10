# ibkr/core/position_manager.py
"""Broker-neutral position/order lifecycle manager for IBKR/Kite-style clients."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set

from PySide6.QtCore import QObject, Signal, Slot, QTimer

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
    day_unrealized: float = 0.0
    day_realized: float = 0.0
    product: str = "STK"
    stop_loss_price: float = 0.0
    stop_loss_position_id: str = ""
    stop_loss_type: str = ""
    stop_loss_quantity: str = ""
    trailing_stop_loss: bool = False


class PositionManager(QObject):
    positions_updated = Signal(list)
    partial_fill_symbols_updated = Signal(object)
    day_pnl_updated = Signal(object)
    position_sync_status_changed = Signal(bool, str)
    show_notification = Signal(str, str)

    def __init__(self, trader: Any, main_window=None, trade_logger=None):
        super().__init__(main_window)
        self.trader = trader
        self.main_window = main_window
        self.trade_logger = trade_logger
        self.tracking_orders: Dict[str, Dict[str, Any]] = {}
        self.safety_timer: Optional[QTimer] = None
        self.live_sync_timer: Optional[QTimer] = None
        self.pending_order_sync_timer: Optional[QTimer] = None
        self._external_pending_order_count = 0
        self._pending_sync_interval_ms = 1200
        self._position_sync_active = False
        self._pending_position_refresh_reason = "broker_position_update"
        self._last_position_log_signature: Optional[tuple] = None
        self._position_refresh_timer = QTimer(self)
        self._position_refresh_timer.setSingleShot(True)
        self._position_refresh_timer.timeout.connect(self._flush_scheduled_position_refresh)
        
        # ✅ NEW: Connect to trader signals for real-time updates from IBKR API
        self._connect_trader_signals()

    def _connect_trader_signals(self) -> None:
        """✅ NEW: Subscribe to trader's real-time update signals from IBKR events."""
        try:
            if hasattr(self.trader, "order_status_updated"):
                self.trader.order_status_updated.connect(self.on_ws_order_update)
                logger.info("✅ Connected to trader.order_status_updated signal - orders will sync immediately")
            
            if hasattr(self.trader, "position_updated"):
                self.trader.position_updated.connect(self.on_ws_position_update)
                logger.info("✅ Connected to trader.position_updated signal - positions will sync immediately")
            
            if hasattr(self.trader, "connection_status_changed"):
                self.trader.connection_status_changed.connect(self.on_ws_connected)
                logger.info("✅ Connected to trader.connection_status_changed signal")
        except Exception as exc:
            logger.warning("Could not connect trader signals: %s", exc)

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------
    def fetch_positions_from_broker(self, reason: str = "manual") -> None:
        try:
            noisy_reasons = {
                "safety_refresh",
                "ibkr_live_sync",
                "ibkr_order_update",
                "ibkr_position_event",
                "order_accepted_fast_poll",
                "order_accepted_confirm_poll",
                "pending_order_sync",
            }
            log_method = logger.debug if reason in noisy_reasons else logger.info
            log_method("Fetching positions from broker - Reason: %s", reason)
            raw_payload = self._broker_positions()
            raw_positions = self._extract_position_rows(raw_payload)

            positions: List[Position] = []
            for row in raw_positions:
                position = self._normalize_position(row)
                if position.quantity != 0:
                    positions.append(position)

            day_pnl = self._build_day_pnl_snapshot(raw_payload, positions)

            self.positions_updated.emit(positions)
            self.partial_fill_symbols_updated.emit(set())
            self.day_pnl_updated.emit(day_pnl)
            position_signature = tuple(
                sorted(
                    (pos.symbol, int(pos.quantity), round(float(pos.avg_price), 4), int(pos.token or 0))
                    for pos in positions
                )
            )
            summary_args = (
                len(positions),
                day_pnl.get("unrealized", 0.0),
                day_pnl.get("realized", 0.0),
            )
            if position_signature != self._last_position_log_signature:
                logger.info(
                    "Position table changed: %d open positions | open MTM %.2f | realized %.2f",
                    *summary_args,
                )
                self._last_position_log_signature = position_signature
            else:
                logger.debug(
                    "Position table refreshed: %d open positions | open MTM %.2f | realized %.2f",
                    *summary_args,
                )
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

        status = str(normalized.get("status") or "UNKNOWN").upper()
        if status in {"COMPLETE", "FILLED", "CANCELLED", "REJECTED", "FAILED", "INACTIVE"}:
            # IBKR can fill market orders inside placeOrder() before MainWindow has
            # registered the order for tracking. Process that returned snapshot
            # immediately so the UI does not remain on the initial Pending row.
            self.on_ws_order_update({**self.tracking_orders[order_id], **normalized})
            return

        symbol = normalized.get("tradingsymbol", "")
        quantity = normalized.get("quantity", 0)
        price = normalized.get("price", 0.0)
        tx_type = normalized.get("transaction_type", "")
        price_text = f" @ {price:.2f}" if price else ""
        self.show_notification.emit(f"⏳ {tx_type} {quantity} {symbol}{price_text} - Pending", "pending")
        self._ensure_pending_order_sync_running("order_accepted")

        # Kick IBKR reconciliation immediately after order acceptance.  This
        # reads IBKR's current order/position API cache now, then retries shortly
        # after to catch fills/position rows that arrive milliseconds later.
        self._schedule_position_refresh("order_accepted", delay_ms=0)
        QTimer.singleShot(
            250,
            lambda oid=order_id: self._refresh_tracked_order_from_broker(oid, "order_accepted_fast_poll"),
        )
        QTimer.singleShot(
            1000,
            lambda oid=order_id: self._refresh_tracked_order_from_broker(oid, "order_accepted_confirm_poll"),
        )

    def _refresh_tracked_order_from_broker(self, order_id: str, reason: str = "tracked_order_poll") -> None:
        """Read IBKR's latest order snapshot and refresh positions for a tracked order."""
        try:
            rows = []
            if hasattr(self.trader, "get_orders"):
                rows = self.trader.get_orders() or []
            elif hasattr(self.trader, "orders"):
                rows = self.trader.orders() or []

            broker_order = next(
                (
                    row
                    for row in rows
                    if str((row or {}).get("order_id") or (row or {}).get("id") or "").strip()
                    == str(order_id)
                ),
                None,
            )
            if broker_order:
                self.on_ws_order_update(broker_order)
            else:
                self._schedule_position_refresh(reason, delay_ms=0)
        except Exception as exc:
            logger.debug("IBKR tracked order refresh failed for %s: %s", order_id, exc, exc_info=True)
            self._schedule_position_refresh(reason, delay_ms=0)

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
                self._schedule_position_refresh("fill_immediate", delay_ms=0)
                QTimer.singleShot(2000, lambda: self.fetch_positions_from_broker("fill_confirm"))
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

    @classmethod
    def _first_float(cls, data: Any, *keys: str, default=None):
        value = cls._field(data, *keys, default=None)
        if value is None:
            return default
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _extract_day_position_rows(payload: Any) -> List[Any]:
        """Rows used for booked/realized P&L; includes closed intraday rows."""
        if isinstance(payload, dict):
            rows = payload.get("day")
            if rows is None:
                rows = payload.get("positions")
            if rows is None:
                rows = payload.get("net")
            if isinstance(rows, list):
                return rows
            if isinstance(rows, tuple):
                return list(rows)
            return []
        if isinstance(payload, list):
            return payload
        if isinstance(payload, tuple):
            return list(payload)
        return []

    def _build_day_pnl_snapshot(self, raw_payload: Any, positions: List[Position]) -> Dict[str, float]:
        """Return clean status-bar P&L buckets.

        MTM/unrealized is only the current open position P&L. Realized is the
        booked P&L for today's trades, including symbols already closed and no
        longer present in the open positions table.  Avoid broker/account
        realized fields in live mode because they can be lifetime aggregates.
        """
        open_unrealized = sum(float(getattr(p, "day_unrealized", p.pnl) or 0.0) for p in positions)

        trader_realized = None
        try:
            if self._is_paper_trader() and hasattr(self.trader, "get_realized_pnl"):
                trader_realized = float(self.trader.get_realized_pnl() or 0.0)
        except Exception:
            trader_realized = None

        top_level_realized = self._first_float(
            raw_payload,
            "day_realised", "day_realized",
            default=None,
        )
        if trader_realized is not None:
            realized = trader_realized
        elif top_level_realized is not None:
            realized = top_level_realized
        else:
            realized = 0.0
            for row in self._extract_day_position_rows(raw_payload):
                row_realized = self._first_float(
                    row,
                    "day_realised", "day_realized",
                    default=None,
                )
                if row_realized is not None:
                    realized += row_realized

        return {
            "open_pnl": open_unrealized,
            "unrealized": open_unrealized,
            "realized": realized,
        }


    def _is_paper_trader(self) -> bool:
        """Return whether the current trader is a paper/simulated broker."""
        trader = self.trader
        hints = (
            trader.__class__.__name__,
            trader.__class__.__module__,
            str(getattr(trader, "broker", "")),
            str(getattr(trader, "broker_type", "")),
            str(getattr(trader, "trading_mode", "")),
        )
        return any("paper" in str(hint).lower() for hint in hints)

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

        product = str(self._field(pos_data, "product", "product_type", "secType", default="STK") or "STK")
        pnl_default = (ltp - avg_price) * quantity if ltp and avg_price and quantity else 0.0

        open_unrealized = self._first_float(
            pos_data,
            "unrealised", "unrealized", "unrealised_pnl", "unrealized_pnl",
            "unrealizedPNL", "unrealizedPnL", "day_unrealized",
            default=None,
        )
        if open_unrealized is None:
            broker_pnl = self._first_float(pos_data, "pnl", "m2m", "dailyPnL", default=None)
            open_unrealized = pnl_default if pnl_default else float(broker_pnl or 0.0)

        day_realized = self._first_float(
            pos_data,
            "realised", "realized", "realised_pnl", "realized_pnl",
            "realizedPNL", "realizedPnL", "day_realized",
            default=0.0,
        )

        return Position(
            symbol=symbol,
            quantity=quantity,
            avg_price=avg_price,
            token=token,
            ltp=ltp,
            pnl=open_unrealized,
            day_unrealized=open_unrealized,
            day_realized=day_realized,
            product=product,
        )

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
            "CANCEL_PENDING": "CANCEL_PENDING",
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
        self.tracking_orders.clear()
        self._update_pending_order_sync_state("tracking_stopped")

    def set_pending_order_dialog_count(self, pending_count: int) -> None:
        """Keep position reconciliation alive while the pending-orders dialog has rows.

        IBKR updates can arrive in separate order and position cache bursts.  When
        a pending order is visible, continuously reading the same synchronized
        position cache used at startup gives the positions table a reliable
        reconciliation path until all pending orders leave the dialog.
        """
        try:
            self._external_pending_order_count = max(0, int(pending_count or 0))
        except (TypeError, ValueError):
            self._external_pending_order_count = 0
        self._update_pending_order_sync_state("pending_dialog")

    def _has_pending_order_sync_work(self) -> bool:
        return bool(self.tracking_orders) or self._external_pending_order_count > 0

    def _ensure_pending_order_sync_running(self, reason: str = "pending_order") -> None:
        if self.pending_order_sync_timer is None:
            self.pending_order_sync_timer = QTimer(self)
            self.pending_order_sync_timer.timeout.connect(self._poll_positions_while_orders_pending)
        if not self.pending_order_sync_timer.isActive():
            self.pending_order_sync_timer.start(self._pending_sync_interval_ms)
            logger.info("Started IBKR pending-order position sync (%s)", reason)
        self._set_position_sync_status(True, "Syncing positions")

    def _update_pending_order_sync_state(self, reason: str = "pending_order_state") -> None:
        if self._has_pending_order_sync_work():
            self._ensure_pending_order_sync_running(reason)
            return
        if self.pending_order_sync_timer and self.pending_order_sync_timer.isActive():
            self.pending_order_sync_timer.stop()
            logger.info("Stopped IBKR pending-order position sync (%s)", reason)
        self._set_position_sync_status(False, "Positions synced")

    def _set_position_sync_status(self, active: bool, message: str) -> None:
        if self._position_sync_active == bool(active) and not active:
            return
        self._position_sync_active = bool(active)
        self.position_sync_status_changed.emit(bool(active), message)

    def _poll_positions_while_orders_pending(self) -> None:
        if not self._has_pending_order_sync_work():
            self._update_pending_order_sync_state("no_pending_orders")
            return

        for order_id in list(self.tracking_orders.keys()):
            self._refresh_tracked_order_from_broker(order_id, "pending_order_sync")
        self.fetch_positions_from_broker("pending_order_sync")
        self._set_position_sync_status(True, "Syncing positions")

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
        """Start legacy polling only when explicitly enabled by callers/config.

        Normal production flow is event-driven via IBKR order/position/connection
        signals.  Use start_safety_refresh() for low-frequency reconciliation.
        """
        interval = max(60, int(interval_seconds))
        if self.live_sync_timer is None:
            self.live_sync_timer = QTimer(self)
            self.live_sync_timer.timeout.connect(lambda: self.fetch_positions_from_broker("ibkr_live_sync"))
        self.live_sync_timer.start(interval * 1000)
        logger.warning("Legacy IBKR position polling enabled at %d-second interval", interval)

    def stop_live_sync(self) -> None:
        if self.live_sync_timer and self.live_sync_timer.isActive():
            self.live_sync_timer.stop()

    # ------------------------------------------------------------------
    # Real-time order update signal handlers
    # ------------------------------------------------------------------
    @Slot(object)
    def on_ws_order_update(self, update: Any = None, *_args, **_kwargs) -> None:
        if not update:
            return

        order = self._normalize_order(update) if not isinstance(update, dict) else update
        order_id = str(order.get("order_id") or "").strip()
        if not order_id:
            return

        status = str(order.get("status") or "UNKNOWN").upper()
        symbol = str(order.get("tradingsymbol") or "").upper()

        if order_id in self.tracking_orders:
            self.tracking_orders[order_id].update(order)

        terminal = status in {"COMPLETE", "FILLED", "CANCELLED", "REJECTED", "FAILED", "INACTIVE"}
        if terminal and order_id in self.tracking_orders:
            self._handle_order_completion(order_id, order, status)
            self.tracking_orders.pop(order_id, None)
            self._schedule_position_refresh("order_terminal", delay_ms=300)
            self._update_pending_order_sync_state("order_terminal")
        elif terminal:
            self._schedule_position_refresh("ibkr_order_update", delay_ms=0)
        elif status in {"OPEN", "PENDING"}:
            self._ensure_pending_order_sync_running("order_open")
            filled = int(order.get("filled_quantity") or 0)
            qty = int(order.get("quantity") or 0)
            if filled > 0 and filled < qty:
                self.partial_fill_symbols_updated.emit({symbol})

    def on_ws_position_update(self, update: Any = None, *_args, **_kwargs) -> None:
        # IBKR position/updatePortfolio events arrive as bursts, one row at a
        # time. Coalesce them before reading the already-synchronized local
        # ib_insync cache so the UI updates once per burst and we avoid a
        # request/event/request feedback loop.
        self._schedule_position_refresh("ibkr_position_event", delay_ms=250)

    def on_ws_connected(self, *_args, **_kwargs) -> None:
        self.fetch_positions_from_broker("ibkr_connected")

    def on_ws_disconnected(self, *_args, **_kwargs) -> None:
        return
