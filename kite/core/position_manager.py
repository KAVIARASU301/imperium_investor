# kite/core/position_manager.py
"""
PositionManager — WebSocket-driven, replaces naive 1s REST API polling.

Original problem:
  self.order_check_timer.start(1000)  # Poll Kite REST every second
  → Unnecessary API load, latency, rate-limit risk

Fix:
  KiteTicker emits ORDER_UPDATE postbacks via the WebSocket channel.
  We listen to that. REST polling is the fallback ONLY when:
    - WebSocket isn't available (e.g. paper trading mode)
    - An order hasn't been confirmed after 60s (safety net)

Architecture:
  PositionManager.on_ws_order_update(order_dict)  ← called by MarketDataWorker
  PositionManager.start_tracking_order(...)        ← called when order placed
  _safety_poll_timer fires every 5s for tracked orders, with WS timeout at 15s
"""

import logging
import re
from dataclasses import dataclass
from typing import Dict, Optional, Set, List
from datetime import datetime, timedelta

from PySide6.QtCore import QObject, Signal, QTimer, Slot, QThreadPool

from kite.widgets.status_bar import status as global_status
from kite.utils.worker import Worker

logger = logging.getLogger(__name__)


@dataclass
class Position:
    symbol: str
    quantity: int
    avg_price: float
    token: int
    ltp: float  = 0.0
    pnl:  float = 0.0
    product: str = "MIS"
    prev_close: float = 0.0
    is_partial_building: bool = False


@dataclass
class TrackedOrder:
    order_id: str
    order_data: dict
    placed_at: datetime
    ws_confirmed: bool = False      # True once WS postback received
    rest_confirmed: bool = False    # True once REST fallback confirms
    partial_fill_seen: bool = False  # True after a partial fill update is logged
    last_partial_filled_qty: int = 0  # Last cumulative fill quantity persisted

    @property
    def is_partial(self) -> bool:
        """Return True while an open tracked order has a partial fill."""
        return self.partial_fill_seen and self.last_partial_filled_qty > 0


class PositionManager(QObject):
    """
    Manages positions and order lifecycle.

    Jobs:
      1. Fetch positions from Kite on demand
      2. Track order status via WebSocket postbacks (primary)
      3. REST polling fallback if WS hasn't confirmed within 60s
      4. Emit notifications on completion/rejection
      5. Integrate with ChartLinesManager for position lines
    """

    positions_updated  = Signal(list)   # emits List[Position]
    partial_fill_symbols_updated = Signal(object)  # emits Set[str] with open partial fills
    show_notification  = Signal(str, str)

    # ── Timing config ──
    SAFETY_POLL_INTERVAL_MS = 5_000     # REST poll interval (fallback only)
    WS_TIMEOUT_SECONDS      = 15        # after this, REST polling kicks in
    ORDER_EXPIRY_MINUTES    = 10        # forget orders older than this

    def __init__(self, trader, main_window=None, trade_logger=None):
        super().__init__()
        self.trader      = trader
        self.main_window = main_window
        self.trade_logger = trade_logger

        self._tracked: Dict[str, TrackedOrder] = {}
        self._confirmed: Set[str] = set()    # order IDs already handled
        self._ws_available = False           # toggled by _on_ws_connected
        self._thread_pool = QThreadPool.globalInstance()
        self._positions_fetch_inflight = False
        self._orders_fetch_inflight = False
        self._broker_api_degraded_until: Optional[datetime] = None
        self._last_broker_api_notice_at: Optional[datetime] = None

        # Safety fallback timer — only polls when needed
        self._safety_timer = QTimer(self)
        self._safety_timer.timeout.connect(self._safety_poll)
        # Starts lazily; stopped when no tracked orders need fallback

        # Position refresh timer — 30s, event-driven primarily
        self._pos_refresh_timer = QTimer(self)
        self._pos_refresh_timer.timeout.connect(
            lambda: self.fetch_positions_from_kite("periodic")
        )
        self._pos_refresh_timer.start(30_000)

    # ─────────────────────────────────────────────────────────────────────────
    # WS AVAILABILITY HOOKS
    # Called by MarketDataWorker signals in main_window._connect_signals()
    # ─────────────────────────────────────────────────────────────────────────

    @Slot()
    def on_ws_connected(self):
        """Called when KiteTicker WebSocket connects."""
        self._ws_available = True
        logger.info("PositionManager: WebSocket available — order tracking via WS postbacks")

    @Slot()
    def on_ws_disconnected(self):
        """Called when WebSocket disconnects."""
        self._ws_available = False
        logger.warning("PositionManager: WebSocket lost — switching to REST polling fallback")

    # ─────────────────────────────────────────────────────────────────────────
    # PRIMARY PATH: WebSocket order updates
    # ─────────────────────────────────────────────────────────────────────────

    @Slot(dict)
    def on_ws_order_update(self, order_dict: dict):
        """
        Called by:
          - KiteTicker on_order_update (live) via MarketDataWorker.order_update
          - BasePaperTrader.order_update (paper) via integrate_paper_trading

        Both modes use this single pipeline.
        """
        order_id = order_dict.get("order_id") or order_dict.get("id")
        if not order_id:
            return

        if order_id in self._confirmed:
            return  # already handled — idempotent

        tracked = self._tracked.get(order_id)
        if not tracked:
            # Not one of ours (e.g. GTT, bracket leg) — ignore silently
            return

        tracked.ws_confirmed = True
        order_status = order_dict.get("status", "").upper()

        logger.info(f"Order update: {order_id} → {order_status}")

        if order_status in ("COMPLETE", "FILLED"):
            self._handle_completion(order_id, order_dict, order_status)

        elif order_status in ("REJECTED", "CANCELLED", "CANCELED"):
            self._handle_failure(order_id, order_dict, order_status)

        elif order_status == "OPEN":
            # ── PARTIAL FILL DETECTION ──
            # Kite sends status=OPEN while an order is live in the market.
            # filled_quantity > 0 means we have a partial fill — alert the trader.
            filled_qty = int(order_dict.get("filled_quantity") or 0)
            pending_qty = int(order_dict.get("pending_quantity") or 0)

            if filled_qty > 0 and pending_qty > 0:
                symbol = order_dict.get("tradingsymbol", "")
                avg_fill = float(order_dict.get("average_price") or 0)
                tx_type = order_dict.get("transaction_type", "")

                # Draw/update entry line immediately on partial fills so the user
                # sees the in-progress position without waiting for COMPLETE.
                if (
                    self.main_window
                    and hasattr(self.main_window, "chart_lines_manager")
                    and symbol
                    and avg_fill > 0
                ):
                    newly_filled_qty = max(0, filled_qty - tracked.last_partial_filled_qty)
                    if newly_filled_qty > 0:
                        self.main_window.chart_lines_manager.add_position_line(
                            symbol=symbol,
                            order_type=tx_type,
                            quantity=newly_filled_qty,
                            avg_price=avg_fill,
                        )
                        logger.info(
                            "[PARTIAL] Chart line updated immediately: %s %s +%s @ ₹%.2f",
                            symbol,
                            tx_type,
                            newly_filled_qty,
                            avg_fill,
                        )

                self.show_notification.emit(
                    f"⚠ Partial fill: {tx_type} {filled_qty}/{filled_qty + pending_qty} "
                    f"{symbol} @ ₹{avg_fill:.2f} — {pending_qty} pending",
                    "warning",
                )
                if filled_qty > tracked.last_partial_filled_qty:
                    if self.trade_logger:
                        self.trade_logger.log_partial_fill(
                            order_id=order_id,
                            filled_qty=filled_qty,
                            avg_price=avg_fill,
                            pending_qty=pending_qty,
                        )
                    tracked.partial_fill_seen = True
                    tracked.last_partial_filled_qty = filled_qty
                    self._emit_partial_fill_symbols()
                logger.info(
                    f"[PARTIAL] {symbol}: {filled_qty} filled, {pending_qty} pending @ ₹{avg_fill:.2f}"
                )
            # else: OPEN with 0 filled = order just accepted (normal), no notification needed

        # PENDING / TRIGGER PENDING / AMO REQ etc → wait for next update

    # ─────────────────────────────────────────────────────────────────────────
    # FALLBACK PATH: REST polling (only when WS not available or timed out)
    # ─────────────────────────────────────────────────────────────────────────

    def _safety_poll(self):
        """
        Polls Kite REST /orders for orders that haven't been WS-confirmed
        within WS_TIMEOUT_SECONDS. Runs every 5s, not 1s.
        """
        if not self._tracked:
            self._safety_timer.stop()
            logger.debug("Safety poll timer stopped — no tracked orders")
            return

        needs_polling = [
            (oid, t) for oid, t in self._tracked.items()
            if not t.ws_confirmed and oid not in self._confirmed
            and (datetime.now() - t.placed_at).total_seconds() > self.WS_TIMEOUT_SECONDS
        ]

        if not needs_polling:
            # Nothing needs polling — stop timer to avoid wasted API calls
            self._safety_timer.stop()
            logger.debug("Safety poll timer stopped — no pending unconfirmed orders")
            return

        if self._orders_fetch_inflight:
            logger.debug("Safety poll skipped — previous /orders request still running")
            return

        self._orders_fetch_inflight = True
        worker = Worker(self.trader.orders)
        worker.signals.result.connect(
            lambda kite_orders: self._handle_safety_poll_orders_result(kite_orders, needs_polling)
        )
        worker.signals.error.connect(
            lambda err: logger.error(f"Safety poll REST call failed: {err[1]}")
        )
        worker.signals.finished.connect(self._on_safety_poll_finished)
        self._thread_pool.start(worker)

    def _ensure_safety_timer(self):
        """Start the safety timer only if it's not already running."""
        if not self._safety_timer.isActive() and self._tracked:
            self._safety_timer.start(self.SAFETY_POLL_INTERVAL_MS)
            logger.debug("Safety poll timer started")

    # ─────────────────────────────────────────────────────────────────────────
    # ORDER TRACKING
    # ─────────────────────────────────────────────────────────────────────────

    def start_tracking_order(self, order_id: str, order_data: dict):
        """
        Begin tracking a newly placed order.
        Primary tracking via WS postback; safety REST poll as fallback.
        """
        tracked = TrackedOrder(
            order_id=order_id,
            order_data=order_data,
            placed_at=datetime.now(),
        )
        self._tracked[order_id] = tracked

        symbol   = order_data.get("tradingsymbol", "")
        qty      = order_data.get("quantity", 0)
        price    = order_data.get("price", 0)
        tx_type  = order_data.get("transaction_type", "")

        global_status.show_order_update(
            {
                "status": "ROUTED",
                "tradingsymbol": symbol,
                "quantity": qty,
                "price": price,
                "transaction_type": tx_type,
                "order_type": order_data.get("order_type", "MKT"),
            }
        )

        if not self._ws_available:
            logger.info(f"Tracking order {order_id} via REST fallback (WS not available)")
        else:
            logger.info(f"Tracking order {order_id} via WebSocket postback")

        # Always schedule a WS-timeout backstop and arm fallback polling now
        QTimer.singleShot(
            self.WS_TIMEOUT_SECONDS * 1000,
            lambda: self._ws_timeout_check(order_id)
        )
        self._emit_partial_fill_symbols()
        self._ensure_safety_timer()


    def partial_fill_symbols(self) -> Set[str]:
        """Return symbols with an open tracked order that has partially filled."""
        return {
            tracked.order_data.get("tradingsymbol", "")
            for tracked in self._tracked.values()
            if tracked.is_partial and tracked.order_data.get("tradingsymbol", "")
        }

    def _emit_partial_fill_symbols(self):
        """Notify UI consumers which symbols are still building from partial fills."""
        self.partial_fill_symbols_updated.emit(self.partial_fill_symbols())

    def _ws_timeout_check(self, order_id: str):
        """If WS hasn't confirmed this order in time, switch to REST polling."""
        if order_id in self._confirmed:
            return  # Already done
        tracked = self._tracked.get(order_id)
        if tracked and not tracked.ws_confirmed:
            logger.warning(
                f"Order {order_id} not confirmed via WS within {self.WS_TIMEOUT_SECONDS}s "
                "— enabling REST fallback"
            )

    # ─────────────────────────────────────────────────────────────────────────
    # COMPLETION HANDLERS
    # ─────────────────────────────────────────────────────────────────────────

    def _handle_completion(self, order_id: str, order_dict: dict, status: str):
        if order_id in self._confirmed:
            return
        self._confirmed.add(order_id)

        symbol = order_dict.get("tradingsymbol", "")
        filled_qty = int(order_dict.get("filled_quantity") or order_dict.get("quantity", 0))
        avg_price = float(order_dict.get("average_price") or 0)
        tx_type = order_dict.get("transaction_type", "").upper()

        # Original tracked order carries our _is_exit_order flag
        tracked = self._tracked.get(order_id)
        is_exit = (
            (tracked and tracked.order_data.get("_is_exit_order"))
            or tx_type == "SELL"   # fallback: SELL = reducing/closing long
        )

        self.show_notification.emit(
            f"✅ Filled: {tx_type} {filled_qty} {symbol} @ ₹{avg_price:.2f}",
            "success",
        )

        # ── Chart line management ──
        if self.main_window and hasattr(self.main_window, "chart_lines_manager"):
            clm = self.main_window.chart_lines_manager

            if is_exit:
                # EXIT — remove position line so chart stays clean
                if filled_qty and avg_price:
                    clm.remove_position_line(symbol)
                    logger.info(f"[EXIT COMPLETE] Removed chart line for {symbol}")
            else:
                # ENTRY — add / update position line
                if filled_qty and avg_price:
                    clm.add_position_line(
                        symbol=symbol,
                        order_type=tx_type,
                        quantity=filled_qty,
                        avg_price=avg_price,
                    )
                    logger.info(
                        f"[ENTRY COMPLETE] Chart line added: {symbol} "
                        f"{tx_type} {filled_qty} @ ₹{avg_price:.2f}"
                    )

        if self.trade_logger:
            pending_qty = int(order_dict.get("pending_quantity") or 0)
            if tracked and tracked.partial_fill_seen:
                self.trade_logger.log_partial_fill(
                    order_id=order_id,
                    filled_qty=filled_qty,
                    avg_price=avg_price,
                    pending_qty=pending_qty,
                )
                tracked.last_partial_filled_qty = filled_qty
            self.trade_logger.log_order_update({
                "order_id": order_id,
                "status": status,
                "status_message": order_dict.get("status_message", ""),
                "average_price": avg_price,
                "filled_quantity": filled_qty,
                "pending_quantity": pending_qty,
                "cancelled_quantity": int(order_dict.get("cancelled_quantity") or 0),
            })

        # Refresh positions
        self.fetch_positions_from_kite("order_completed")
        self._tracked.pop(order_id, None)
        self._emit_partial_fill_symbols()
        self._stop_safety_timer_if_idle()
        logger.info(
            f"✅ Order complete: {order_id} | {tx_type} {filled_qty} {symbol} @ ₹{avg_price:.2f}"
        )

    def _handle_failure(self, order_id: str, order_dict: dict, status: str):
        if order_id in self._confirmed:
            return
        self._confirmed.add(order_id)

        symbol = order_dict.get("tradingsymbol", "?")
        reason = (
            order_dict.get("status_message")
            or order_dict.get("reject_reason")
            or "Unknown reason"
        )
        tx_type = order_dict.get("transaction_type", "")

        if str(status).upper() in {"CANCELLED", "CANCELED"}:
            self.show_notification.emit(f"⚠ Order cancelled: {symbol}", "warning")
        else:
            self.show_notification.emit(f"❌ Order rejected: {symbol} — {reason}", "error")

        if self.trade_logger:
            self.trade_logger.log_order_update({
                "order_id": order_id,
                "status": status,
                "status_message": reason,
                "average_price": float(order_dict.get("average_price") or 0.0),
                "filled_quantity": int(order_dict.get("filled_quantity") or 0),
                "pending_quantity": int(order_dict.get("pending_quantity") or 0),
                "cancelled_quantity": int(order_dict.get("cancelled_quantity") or 0),
            })

        self._tracked.pop(order_id, None)
        self._emit_partial_fill_symbols()
        self._stop_safety_timer_if_idle()
        logger.warning(
            f"⚠️ Order {status}: {order_id} | {tx_type} {symbol} | Reason: {reason}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # POSITION FETCH
    # ─────────────────────────────────────────────────────────────────────────

    def fetch_positions_from_kite(self, reason: str = "manual"):
        """Fetch and emit current positions."""
        if self._positions_fetch_inflight:
            logger.debug(f"Skipping position fetch ({reason}) — previous request still running")
            return

        logger.debug(f"Queueing background position fetch — reason: {reason}")
        self._positions_fetch_inflight = True
        worker = Worker(self._fetch_positions_payload, log_exceptions=False)
        worker.signals.result.connect(self._handle_positions_result)
        worker.signals.error.connect(
            lambda err: self._handle_positions_error(err[1])
        )
        worker.signals.finished.connect(self._on_positions_fetch_finished)
        self._thread_pool.start(worker)

    def _fetch_positions_payload(self):
        """Fetch positions plus holdings so CNC carry holdings can appear in positions UI."""
        payload = {
            "positions": self.trader.positions(),
            "holdings": []
        }
        try:
            if hasattr(self.trader, "holdings"):
                payload["holdings"] = self.trader.holdings() or []
        except Exception as exc:
            logger.warning(f"Holdings fetch failed while loading positions UI: {exc}")
        return payload

    @Slot(object)
    def _handle_positions_result(self, payload):
        positions: List[Position] = []
        kite_positions = payload.get("positions", {}) if isinstance(payload, dict) else (payload or {})
        holdings = payload.get("holdings", []) if isinstance(payload, dict) else []

        for pos_data in (kite_positions or {}).get("net", []):
            quantity = int(pos_data.get("quantity") or 0)
            if quantity == 0:
                continue

            product = str(pos_data.get("product") or pos_data.get("product_type") or "MIS").upper()
            # Kite can report same-day CNC exits as negative quantity (greyed out in Kite UI).
            # These are not active short positions and should not appear in active positions table.
            if product == "CNC" and quantity < 0:
                continue

            pos = Position(
                symbol   = pos_data.get("tradingsymbol", ""),
                quantity = quantity,
                avg_price= float(pos_data.get("average_price") or 0.0),
                token    = int(pos_data.get("instrument_token") or 0),
                ltp      = float(pos_data.get("last_price") or 0.0),
                product  = product,
            )
            for tracked in self._tracked.values():
                t_sym = tracked.order_data.get("tradingsymbol", "")
                if t_sym == pos.symbol and tracked.is_partial:
                    pos.is_partial_building = True
                    break
            pos.pnl = (pos.ltp - pos.avg_price) * pos.quantity
            positions.append(pos)

        existing_symbols = {p.symbol for p in positions}
        for hold in holdings or []:
            settled_qty = int(hold.get("quantity") or 0)
            t1_qty = int(hold.get("t1_quantity") or 0)
            qty = settled_qty + t1_qty
            symbol = hold.get("tradingsymbol", "")
            if qty <= 0 or not symbol or symbol in existing_symbols:
                continue

            avg_price = float(hold.get("average_price") or 0.0)
            last_price = float(hold.get("last_price") or hold.get("ltp") or avg_price or 0.0)
            pos = Position(
                symbol=symbol,
                quantity=qty,
                avg_price=avg_price,
                token=int(hold.get("instrument_token") or 0),
                ltp=last_price,
                product=str(hold.get("product") or "CNC"),
            )
            pos.pnl = (pos.ltp - pos.avg_price) * pos.quantity
            positions.append(pos)

        self.positions_updated.emit(positions)
        self._emit_partial_fill_symbols()
        logger.debug(f"Emitted {len(positions)} positions")

        if self.main_window and hasattr(self.main_window, "chart_lines_manager"):
            self.main_window.chart_lines_manager.sync_position_lines(positions)

    def _handle_positions_error(self, error):
        if self._is_transient_broker_gateway_error(error):
            now = datetime.utcnow()
            self._broker_api_degraded_until = now + timedelta(minutes=10)
            logger.warning("Broker API temporarily unavailable while fetching positions: %s", error)
            if (
                self._last_broker_api_notice_at is None
                or (now - self._last_broker_api_notice_at) >= timedelta(minutes=3)
            ):
                self._last_broker_api_notice_at = now
                self.show_notification.emit(
                    "Broker maintenance window detected. Positions/margins will auto-retry.",
                    "info",
                )
            return

        logger.error(f"Failed to fetch positions: {error}")
        self.show_notification.emit("Position fetch failed. Please retry.", "error")

    @staticmethod
    def _is_transient_broker_gateway_error(error: object) -> bool:
        text = str(error or "")
        normalized = text.lower()
        if "unknown content-type" in normalized and "text/html" in normalized:
            return True
        if "bad gateway" in normalized or "gateway timeout" in normalized:
            return True
        if re.search(r"\b50[234]\b", normalized):
            return True
        return False

    @Slot()
    def _on_positions_fetch_finished(self):
        self._positions_fetch_inflight = False

    @Slot()
    def _on_safety_poll_finished(self):
        self._orders_fetch_inflight = False
        self._purge_expired()

    def _handle_safety_poll_orders_result(self, kite_orders, needs_polling):
        for oid, tracked in needs_polling:
            kite_order = next(
                (o for o in (kite_orders or []) if o.get("order_id") == oid),
                None,
            )
            if not kite_order:
                continue

            status = kite_order.get("status", "").upper()
            filled = int(kite_order.get("filled_quantity") or 0)
            pending = int(kite_order.get("pending_quantity") or 0)
            tracked.rest_confirmed = True

            if status in ("COMPLETE", "FILLED"):
                self._handle_completion(oid, kite_order, status)
            elif status in ("REJECTED", "CANCELLED", "CANCELED"):
                self._handle_failure(oid, kite_order, status)
            elif status == "OPEN" and filled > 0:
                # Partial fill detected via REST fallback. Re-route through the
                # WebSocket update path so partial handling stays unified.
                self.on_ws_order_update(kite_order)

    # ─────────────────────────────────────────────────────────────────────────
    # CHART LINE HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def update_position_line(self, symbol: str, total_quantity: int,
                              avg_price: float, order_type: str):
        """Update chart position line when position size changes."""
        if not self.main_window or not hasattr(self.main_window, "chart_lines_manager"):
            return
        clm = self.main_window.chart_lines_manager
        clm.remove_position_line(symbol)
        if total_quantity != 0:
            clm.add_position_line(
                symbol=symbol,
                order_type=order_type,
                quantity=abs(total_quantity),
                avg_price=avg_price,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # CLEANUP
    # ─────────────────────────────────────────────────────────────────────────

    def _purge_expired(self):
        """Remove orders that are older than ORDER_EXPIRY_MINUTES."""
        cutoff = datetime.now() - timedelta(minutes=self.ORDER_EXPIRY_MINUTES)
        expired = [
            oid for oid, t in self._tracked.items()
            if t.placed_at < cutoff and oid not in self._confirmed
        ]
        for oid in expired:
            logger.warning(f"Purging expired unconfirmed order: {oid}")
            del self._tracked[oid]
        if expired:
            self._emit_partial_fill_symbols()
        self._stop_safety_timer_if_idle()

    def _stop_safety_timer_if_idle(self):
        if not self._tracked and self._safety_timer.isActive():
            self._safety_timer.stop()
            logger.debug("Safety poll timer stopped — no tracked orders remain")

    def stop_tracking(self):
        """Graceful shutdown — stop all timers."""
        self._safety_timer.stop()
        self._pos_refresh_timer.stop()
        self._tracked.clear()
        self._emit_partial_fill_symbols()
        logger.info("PositionManager stopped")
