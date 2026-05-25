# ==============================================================================
# 1. SIMPLIFIED POSITION MANAGER - ONLY 4 RESPONSIBILITIES
# ==============================================================================

import logging
from PySide6.QtCore import QObject, Signal, QTimer
from dataclasses import dataclass
from ibkr.widgets.status_bar import (
    show_order_completed, show_order_failed, show_error, show_info
)
from ibkr.utils.sounds import play_entry_exit

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
    partial_fill_symbols_updated = Signal(object)  # emits Set[str]
    day_pnl_updated = Signal(float)
    show_notification = Signal(str, str)  # message, type

    def __init__(self, trader, main_window=None, trade_logger=None):
        super().__init__()
        self.trader = trader
        self.main_window = main_window  # Add this line
        self.trade_logger = trade_logger
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
            self.partial_fill_symbols_updated.emit(set())
            self.day_pnl_updated.emit(sum(p.pnl for p in simple_positions))
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
        """Handle when order completes/fails with chart line integration"""
        symbol = kite_order.get('tradingsymbol', '')
        quantity = kite_order.get('quantity', 0)
        tx_type = kite_order.get('transaction_type', '').upper()
        tracked_order = self.tracking_orders.get(order_id, {})
        is_exit = bool(tracked_order.get("_is_exit_order")) or tx_type == "SELL"

        try:
            if status in ['COMPLETE', 'FILLED']:
                # Show order completed notification
                show_order_completed(symbol, "")
                play_entry_exit()

                # Update chart position line
                if self.main_window and hasattr(self.main_window, 'chart_lines_manager'):
                    filled_quantity = kite_order.get('filled_quantity', kite_order.get('quantity', 0))
                    avg_price = kite_order.get('average_price', 0)

                    if filled_quantity and avg_price:
                        if is_exit:
                            success = self.main_window.chart_lines_manager.remove_position_line(symbol)
                            if success:
                                logger.info(f"Position line removed for exit {symbol}")
                            else:
                                logger.warning(f"Failed to remove position line for exit {symbol}")
                        else:
                            success = self.main_window.chart_lines_manager.add_position_line(
                                symbol=symbol,
                                order_type=tx_type,
                                quantity=filled_quantity,
                                avg_price=avg_price
                            )
                            if success:
                                logger.info(f"Position line added to chart for {symbol}")
                            else:
                                logger.warning(f"Failed to add position line to chart for {symbol}")
                    else:
                        logger.warning(
                            f"Invalid order data for chart line: quantity={filled_quantity}, price={avg_price}")
                else:
                    logger.debug("Chart lines manager not available, position line not added")

            elif status in ['REJECTED', 'CANCELLED', 'FAILED']:
                show_order_failed(f"Order {status.lower()}")

        except Exception as e:
            logger.error(f"Error in order completion handling: {e}")

    def update_position_line(self, symbol: str, total_quantity: int, avg_price: float, order_type: str):
        """Update position line when position changes (e.g., partial fills, position averaging)"""
        try:
            if not self.main_window or not hasattr(self.main_window, 'chart_lines_manager'):
                logger.debug("Chart lines manager not available for position line update")
                return

            # Remove existing position line first
            remove_success = self.main_window.chart_lines_manager.remove_position_line(symbol)

            # Add new line with updated info if position still exists
            if total_quantity != 0:
                add_success = self.main_window.chart_lines_manager.add_position_line(
                    symbol=symbol,
                    order_type=order_type,
                    quantity=abs(total_quantity),
                    avg_price=avg_price
                )
                if add_success:
                    logger.info(f"Updated position line for {symbol}: {total_quantity} @ {avg_price:.2f}")
                else:
                    logger.warning(f"Failed to update position line for {symbol}")
            else:
                logger.info(f"Position closed, line removed for {symbol}")

        except Exception as e:
            logger.error(f"Error updating position line for {symbol}: {e}")

    def remove_position_line_for_symbol(self, symbol: str):
        """Remove position line for a symbol (when position is fully closed)"""
        try:
            if self.main_window and hasattr(self.main_window, 'chart_lines_manager'):
                success = self.main_window.chart_lines_manager.remove_position_line(symbol)
                if success:
                    logger.info(f"Position line removed for {symbol}")
                else:
                    logger.warning(f"Failed to remove position line for {symbol}")
            else:
                logger.debug("Chart lines manager not available for position line removal")
        except Exception as e:
            logger.error(f"Error removing position line for {symbol}: {e}")

    # Add method to handle position updates from external sources (like position table)
    def on_position_closed_externally(self, symbol: str):
        """Handle position closure from external sources (e.g., manual close, web platform)"""
        try:
            self.remove_position_line_for_symbol(symbol)
            logger.info(f"External position closure handled for {symbol}")
        except Exception as e:
            logger.error(f"Error handling external position closure for {symbol}: {e}")

    # ===========================================================================
    # JOB 4: SAFETY REFRESH (OPTIONAL)
    # ===========================================================================

    def start_safety_refresh(self, interval_minutes=5):
        """Optional: Refresh positions every X minutes for safety"""
        safety_timer = QTimer()
        safety_timer.timeout.connect(lambda: self.fetch_positions_from_kite("safety_refresh"))
        safety_timer.start(interval_minutes * 60 * 1000)
        logger.info(f"Started safety refresh every {interval_minutes} minutes")

    # ===========================================================================
    # Compatibility no-op handlers used by MainWindow signal wiring
    # ===========================================================================

    def on_ws_order_update(self, *_args, **_kwargs):
        """Handle websocket order updates (compatibility no-op)."""
        return

    def on_ws_connected(self, *_args, **_kwargs):
        """Handle websocket connected (compatibility no-op)."""
        return

    def on_ws_disconnected(self, *_args, **_kwargs):
        """Handle websocket disconnected (compatibility no-op)."""
        return
