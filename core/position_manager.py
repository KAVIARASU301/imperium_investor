# ==============================================================================
# 1. SIMPLIFIED POSITION MANAGER - ONLY 4 RESPONSIBILITIES
# ==============================================================================

import logging
from PySide6.QtCore import QObject, Signal, QTimer
from dataclasses import dataclass
from widgets.status_bar import (
    show_order_completed, show_order_failed, show_error, show_info
)

logger = logging.getLogger(__name__)


@dataclass
class Position:
    symbol: str
    quantity: int
    avg_price: float
    token: int
    ltp: float = 0.0
    pnl: float = 0.0
    product: str = "MIS"  # ADD THIS LINE - Default to MIS


class PositionManager(QObject):
    """
    SUPER SIMPLE Position Manager with only 4 jobs:
    1. Fetch positions from Kite when asked
    2. Track order status from pending to complete
    3. Send notifications based on order status
    4. Go dead after order completed
    """

    # Simple signals
    positions_updated = Signal(list)  # Send positions to table
    show_notification = Signal(str, str)  # message, type

    def __init__(self, trader):
        super().__init__()
        self.trader = trader
        self.tracking_orders = {}  # order_id -> order_data
        self.order_check_timer = QTimer()
        self.order_check_timer.timeout.connect(self._check_pending_orders)

    # ===========================================================================
    # JOB 1: FETCH POSITIONS FROM KITE (SIMPLE)
    # ===========================================================================

    def fetch_positions_from_kite(self, reason="manual"):
        """Dead simple position fetch - just get and send"""
        try:
            logger.info(f"Fetching positions from Kite - Reason: {reason}")

            # Get positions from Kite
            kite_positions = self.trader.positions()

            # Convert to simple format
            simple_positions = []
            for pos_data in kite_positions.get('net', []):
                if pos_data.get('quantity', 0) != 0:  # Only non-zero positions
                    position = Position(
                        symbol=pos_data.get('tradingsymbol', ''),
                        quantity=pos_data.get('quantity', 0),
                        avg_price=pos_data.get('average_price', 0),
                        token=pos_data.get('instrument_token', 0),
                        ltp=pos_data.get('last_price', 0),
                        product=pos_data.get('product', 'MIS')  # ADD THIS LINE - Extract product from Kite data
                    )
                    position.pnl = (position.ltp - position.avg_price) * position.quantity
                    simple_positions.append(position)

            # Send to positions table - ONE TIME
            self.positions_updated.emit(simple_positions)
            logger.info(f"✅ Sent {len(simple_positions)} positions to table")

        except Exception as e:
            logger.error(f"Failed to fetch positions: {e}")
            self.show_notification.emit(f"Failed to fetch positions: {e}", "error")

    # ===========================================================================
    # JOB 2: TRACK ORDER STATUS (SIMPLE POLLING)
    # ===========================================================================

    def start_tracking_order(self, order_id: str, order_data: dict):
        """
        OPTIMIZED: Start tracking an order - NO BLOCKING OPERATIONS
        """
        logger.info(f"🔄 Started tracking order: {order_id}")

        # Store order for tracking - IMMEDIATE
        self.tracking_orders[order_id] = order_data

        # Show notification - IMMEDIATE, NO DELAYS
        symbol = order_data.get('tradingsymbol', '')
        quantity = order_data.get('quantity', 0)
        price = order_data.get('price', 0)
        tx_type = order_data.get('transaction_type', '')

        self.show_notification.emit(
            f"⏳ {tx_type} {quantity} {symbol} @ ₹{price} - Pending",
            "pending"
        )

        # Start checking IMMEDIATELY - no delays
        if not self.order_check_timer.isActive():
            self.order_check_timer.start(1000)  # Check every 1 second

    def _check_pending_orders(self):
        """Check status of all pending orders"""
        if not self.tracking_orders:
            self.order_check_timer.stop()
            return

        try:
            # Get all orders from Kite
            kite_orders = self.trader.orders()

            completed_orders = []

            for order_id, order_data in self.tracking_orders.items():
                # Find this order in Kite response
                kite_order = next((o for o in kite_orders if o.get('order_id') == order_id), None)

                if kite_order:
                    status = kite_order.get('status', 'UNKNOWN')

                    if status in ['COMPLETE', 'CANCELLED', 'REJECTED']:
                        self._handle_order_completion(order_id, kite_order, status)
                        completed_orders.append(order_id)

            # Remove completed orders from tracking
            for order_id in completed_orders:
                del self.tracking_orders[order_id]

            # Stop timer if no more orders to track
            if not self.tracking_orders:
                self.order_check_timer.stop()
                logger.info("✅ All orders completed - Position manager going dead")

        except Exception as e:
            logger.error(f"Error checking order status: {e}")

    # ===========================================================================
    # JOB 3: HANDLE ORDER COMPLETION & NOTIFICATIONS
    # ===========================================================================

    def _handle_order_completion(self, order_id: str, kite_order: dict, status: str):
        """Handle when order completes/fails"""
        symbol = kite_order.get('tradingsymbol', '')
        quantity = kite_order.get('quantity', 0)
        tx_type = kite_order.get('transaction_type', '')

        try:
            if status in ['COMPLETE', 'FILLED']:
                show_order_completed(symbol, "")
            elif status in ['REJECTED', 'CANCELLED', 'FAILED']:
                show_order_failed(f"Order {status.lower()}")

            # Send appropriate notification
            if status == 'COMPLETE':
                avg_price = kite_order.get('average_price', 0)
                self.show_notification.emit(
                    f"✅ {tx_type} {quantity} {symbol} @ ₹{avg_price} - Completed",
                    "success"
                )

                # Refresh positions after completion
                QTimer.singleShot(2000, lambda: self.fetch_positions_from_kite("order_completed"))

            elif status == 'CANCELLED':
                self.show_notification.emit(
                    f"❌ {tx_type} {quantity} {symbol} - Cancelled",
                    "warning"
                )

            elif status == 'REJECTED':
                reject_reason = kite_order.get('status_message', 'Unknown reason')
                self.show_notification.emit(
                    f"🚫 {tx_type} {quantity} {symbol} - Rejected: {reject_reason}",
                    "error"
                )

            logger.info(f"Order {order_id} completed with status: {status}")
        except Exception as e:
            logger.error(f"Error handling order completion: {e}")
            show_error("Order completion error")

    # ===========================================================================
    # JOB 4: SAFETY REFRESH (OPTIONAL)
    # ===========================================================================

    def start_safety_refresh(self, interval_minutes=5):
        """Optional: Refresh positions every X minutes for safety"""
        safety_timer = QTimer()
        safety_timer.timeout.connect(lambda: self.fetch_positions_from_kite("safety_refresh"))
        safety_timer.start(interval_minutes * 60 * 1000)
        logger.info(f"Started safety refresh every {interval_minutes} minutes")