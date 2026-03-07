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
  _safety_poll_timer fires every 15s ONLY if ws_confirmed = False within 60s
"""

import logging
from dataclasses import dataclass
from typing import Dict, Optional, Set, List
from datetime import datetime, timedelta

from PySide6.QtCore import QObject, Signal, QTimer, Slot

from kite.widgets.status_bar import (
    show_order_completed, show_order_failed, show_error, show_info
)

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


@dataclass
class TrackedOrder:
    order_id: str
    order_data: dict
    placed_at: datetime
    ws_confirmed: bool = False      # True once WS postback received
    rest_confirmed: bool = False    # True once REST fallback confirms


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
    show_notification  = Signal(str, str)

    # ── Timing config ──
    SAFETY_POLL_INTERVAL_MS = 15_000    # REST poll interval (fallback only)
    WS_TIMEOUT_SECONDS      = 60        # after this, REST polling kicks in
    ORDER_EXPIRY_MINUTES    = 10        # forget orders older than this

    def __init__(self, trader, main_window=None):
        super().__init__()
        self.trader      = trader
        self.main_window = main_window

        self._tracked: Dict[str, TrackedOrder] = {}
        self._confirmed: Set[str] = set()    # order IDs already handled
        self._ws_available = False           # toggled by _on_ws_connected

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
        self._ensure_safety_timer()

    # ─────────────────────────────────────────────────────────────────────────
    # PRIMARY PATH: WebSocket order updates
    # ─────────────────────────────────────────────────────────────────────────

    @Slot(dict)
    def on_ws_order_update(self, order_dict: dict):
        """
        Called by KiteTicker's on_order_update callback (via MarketDataWorker).

        Kite pushes order postbacks over the same WebSocket channel.
        Format mirrors the REST /orders response.
        """
        order_id = order_dict.get("order_id") or order_dict.get("id")
        if not order_id:
            return

        if order_id in self._confirmed:
            return  # already handled

        tracked = self._tracked.get(order_id)
        if not tracked:
            # Not one of ours — ignore silently
            return

        tracked.ws_confirmed = True
        status = order_dict.get("status", "").upper()

        logger.info(f"WS order update: {order_id} → {status}")

        if status in ("COMPLETE", "FILLED"):
            self._handle_completion(order_id, order_dict, status)
        elif status in ("REJECTED", "CANCELLED"):
            self._handle_failure(order_id, order_dict, status)
        elif status in ("OPEN", "TRIGGER PENDING"):
            pass  # still in flight — wait for next update
        # PENDING / AMO REQ / UPDATE REQ etc — wait

    # ─────────────────────────────────────────────────────────────────────────
    # FALLBACK PATH: REST polling (only when WS not available or timed out)
    # ─────────────────────────────────────────────────────────────────────────

    def _safety_poll(self):
        """
        Polls Kite REST /orders for orders that haven't been WS-confirmed
        within WS_TIMEOUT_SECONDS. Runs every 15s, not 1s.
        """
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

        try:
            kite_orders = self.trader.orders()
            for oid, tracked in needs_polling:
                kite_order = next(
                    (o for o in kite_orders if o.get("order_id") == oid), None
                )
                if not kite_order:
                    continue

                status = kite_order.get("status", "").upper()
                tracked.rest_confirmed = True

                if status in ("COMPLETE", "FILLED"):
                    self._handle_completion(oid, kite_order, status)
                elif status in ("REJECTED", "CANCELLED"):
                    self._handle_failure(oid, kite_order, status)

        except Exception as e:
            logger.error(f"Safety poll REST call failed: {e}")

        # Clean up expired orders
        self._purge_expired()

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

        self.show_notification.emit(
            f"⏳ {tx_type} {qty} {symbol} @ ₹{price} — Pending",
            "pending"
        )

        if not self._ws_available:
            # Paper trading / WS not up — start safety poll immediately
            self._ensure_safety_timer()
            logger.info(f"Tracking order {order_id} via REST fallback (WS not available)")
        else:
            logger.info(f"Tracking order {order_id} via WebSocket postback")
            # Also schedule safety timer in case WS confirmation never arrives
            QTimer.singleShot(
                self.WS_TIMEOUT_SECONDS * 1000,
                lambda: self._ws_timeout_check(order_id)
            )

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
            self._ensure_safety_timer()

    # ─────────────────────────────────────────────────────────────────────────
    # COMPLETION HANDLERS
    # ─────────────────────────────────────────────────────────────────────────

    def _handle_completion(self, order_id: str, order_dict: dict, status: str):
        if order_id in self._confirmed:
            return
        self._confirmed.add(order_id)

        symbol         = order_dict.get("tradingsymbol", "")
        filled_qty     = int(order_dict.get("filled_quantity")
                             or order_dict.get("quantity", 0))
        avg_price      = float(order_dict.get("average_price", 0))
        tx_type        = order_dict.get("transaction_type", "")

        show_order_completed(symbol, "")

        # Add/update position line on chart
        if self.main_window and hasattr(self.main_window, "chart_lines_manager"):
            if filled_qty and avg_price:
                self.main_window.chart_lines_manager.add_position_line(
                    symbol=symbol,
                    order_type=tx_type,
                    quantity=filled_qty,
                    avg_price=avg_price,
                )

        # Refresh positions
        self.fetch_positions_from_kite("order_completed")
        del self._tracked[order_id]
        logger.info(f"✅ Order complete: {order_id} | {tx_type} {filled_qty} {symbol} @ ₹{avg_price}")

    def _handle_failure(self, order_id: str, order_dict: dict, status: str):
        if order_id in self._confirmed:
            return
        self._confirmed.add(order_id)

        show_order_failed(f"Order {status.lower()}")
        del self._tracked[order_id]
        logger.warning(f"⚠️ Order {status}: {order_id}")

    # ─────────────────────────────────────────────────────────────────────────
    # POSITION FETCH
    # ─────────────────────────────────────────────────────────────────────────

    def fetch_positions_from_kite(self, reason: str = "manual"):
        """Fetch and emit current positions."""
        try:
            logger.debug(f"Fetching positions — reason: {reason}")
            kite_positions = self.trader.positions()

            positions: List[Position] = []
            for pos_data in kite_positions.get("net", []):
                if pos_data.get("quantity", 0) == 0:
                    continue
                pos = Position(
                    symbol   = pos_data.get("tradingsymbol", ""),
                    quantity = pos_data.get("quantity", 0),
                    avg_price= pos_data.get("average_price", 0),
                    token    = pos_data.get("instrument_token", 0),
                    ltp      = pos_data.get("last_price", 0),
                    product  = pos_data.get("product", "MIS"),
                )
                pos.pnl = (pos.ltp - pos.avg_price) * pos.quantity
                positions.append(pos)

            self.positions_updated.emit(positions)
            logger.debug(f"Emitted {len(positions)} positions")

        except Exception as e:
            logger.error(f"Failed to fetch positions: {e}")
            self.show_notification.emit(f"Position fetch failed: {e}", "error")

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

    def stop_tracking(self):
        """Graceful shutdown — stop all timers."""
        self._safety_timer.stop()
        self._pos_refresh_timer.stop()
        self._tracked.clear()
        logger.info("PositionManager stopped")
