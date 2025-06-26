# utils/simple_order_tracker.py - NEW FILE
import logging
from typing import Dict, Any
from datetime import datetime
from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger(__name__)


class SimpleOrderTracker(QObject):
    """
    Simple order tracker that replaces the complex dialog system.
    Tracks orders and emits signals for UI updates without managing dialogs.
    """

    # Signals for main window integration
    order_status_changed = Signal(dict)  # order_data
    order_completed = Signal(dict)  # order_data
    order_cancelled = Signal(dict)  # order_data
    order_rejected = Signal(dict, str)  # order_data, reason
    partial_fill_updated = Signal(dict)  # order_data

    def __init__(self, main_window, update_interval: int = 2000):
        super().__init__()
        self.main_window = main_window
        self.tracked_orders: Dict[str, Dict[str, Any]] = {}
        self.last_status: Dict[str, str] = {}

        # Update timer
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._update_all_orders)
        self.update_timer.start(update_interval)

        logger.info(f"Simple order tracker initialized with {update_interval}ms interval")

    def track_order(self, order_data: Dict[str, Any]) -> bool:
        """
        Start tracking an order.

        Args:
            order_data: Order data with order_id and status

        Returns:
            bool: True if tracking started successfully
        """
        try:
            order_id = order_data.get('order_id')
            if not order_id:
                logger.warning("Cannot track order without order_id")
                return False

            # Store order data and initial status
            self.tracked_orders[order_id] = order_data.copy()
            self.last_status[order_id] = order_data.get('status', 'UNKNOWN').upper()

            logger.info(f"Started tracking order {order_id}")
            return True

        except Exception as e:
            logger.error(f"Error starting order tracking: {e}")
            return False

    def stop_tracking(self, order_id: str) -> bool:
        """
        Stop tracking an order.

        Args:
            order_id: Order ID to stop tracking

        Returns:
            bool: True if stopped successfully
        """
        try:
            if order_id in self.tracked_orders:
                del self.tracked_orders[order_id]

            if order_id in self.last_status:
                del self.last_status[order_id]

            logger.info(f"Stopped tracking order {order_id}")
            return True

        except Exception as e:
            logger.error(f"Error stopping order tracking: {e}")
            return False

    def get_tracked_orders(self) -> Dict[str, Dict[str, Any]]:
        """Get all currently tracked orders."""
        return self.tracked_orders.copy()

    def get_active_order_count(self) -> int:
        """Get count of active (non-terminal) orders."""
        terminal_statuses = {'COMPLETE', 'CANCELLED', 'REJECTED'}
        active_count = 0

        for order_data in self.tracked_orders.values():
            status = order_data.get('status', '').upper()
            if status not in terminal_statuses:
                active_count += 1

        return active_count

    def _update_all_orders(self):
        """Update all tracked orders from the main window."""
        if not self.tracked_orders:
            return

        try:
            # Get list of order IDs to avoid dictionary size change during iteration
            order_ids = list(self.tracked_orders.keys())

            for order_id in order_ids:
                self._update_single_order(order_id)

        except Exception as e:
            logger.error(f"Error in bulk order update: {e}")

    def _update_single_order(self, order_id: str):
        """Update a single tracked order."""
        try:
            # Get updated order data from main window
            if (hasattr(self.main_window, 'get_order_status') and
                    callable(self.main_window.get_order_status)):

                updated_data = self.main_window.get_order_status(order_id)
                if updated_data:
                    self._process_order_update(order_id, updated_data)
                else:
                    # If we can't get updated data, check if order is too old
                    self._check_stale_order(order_id)
            else:
                logger.warning("Main window does not have get_order_status method")

        except Exception as e:
            logger.debug(f"Error updating order {order_id}: {e}")

    def _process_order_update(self, order_id: str, updated_data: Dict[str, Any]):
        """Process an order update and emit appropriate signals."""
        try:
            # Update stored order data
            old_data = self.tracked_orders.get(order_id, {})
            self.tracked_orders[order_id] = updated_data

            # Check for status changes
            old_status = self.last_status.get(order_id, '').upper()
            new_status = updated_data.get('status', '').upper()

            if old_status != new_status:
                self.last_status[order_id] = new_status
                self._handle_status_change(order_id, old_status, new_status, updated_data)

            # Always emit general status update
            self.order_status_changed.emit(updated_data)

        except Exception as e:
            logger.error(f"Error processing order update for {order_id}: {e}")

    def _handle_status_change(self, order_id: str, old_status: str,
                              new_status: str, order_data: Dict[str, Any]):
        """Handle order status changes with specific signals."""
        try:
            logger.info(f"Order {order_id} status: {old_status} → {new_status}")

            if new_status == 'COMPLETE':
                self.order_completed.emit(order_data)
                # Stop tracking completed orders after a delay
                QTimer.singleShot(5000, lambda: self.stop_tracking(order_id))

            elif new_status == 'CANCELLED':
                self.order_cancelled.emit(order_data)
                # Stop tracking cancelled orders after a delay
                QTimer.singleShot(3000, lambda: self.stop_tracking(order_id))

            elif new_status == 'REJECTED':
                reason = order_data.get('status_message', 'Unknown reason')
                self.order_rejected.emit(order_data, reason)
                # Stop tracking rejected orders after a delay
                QTimer.singleShot(5000, lambda: self.stop_tracking(order_id))

            elif new_status == 'PARTIAL':
                self.partial_fill_updated.emit(order_data)

        except Exception as e:
            logger.error(f"Error handling status change for {order_id}: {e}")

    def _check_stale_order(self, order_id: str):
        """Check if an order is stale and should be removed from tracking."""
        try:
            order_data = self.tracked_orders.get(order_id, {})
            if not order_data:
                return

            # Check if order is older than 30 minutes with no updates
            created_time = order_data.get('order_timestamp')
            if isinstance(created_time, str):
                try:
                    created_dt = datetime.fromisoformat(created_time.replace('Z', '+00:00'))
                    age_minutes = (datetime.now() - created_dt).total_seconds() / 60

                    if age_minutes > 30:
                        logger.info(f"Removing stale order from tracking: {order_id}")
                        self.stop_tracking(order_id)

                except ValueError:
                    # Invalid timestamp format, remove after 10 update cycles
                    pass

        except Exception as e:
            logger.debug(f"Error checking stale order {order_id}: {e}")

    def cleanup_terminal_orders(self):
        """Clean up orders in terminal states."""
        try:
            terminal_statuses = {'COMPLETE', 'CANCELLED', 'REJECTED'}
            terminal_orders = []

            for order_id, order_data in self.tracked_orders.items():
                status = order_data.get('status', '').upper()
                if status in terminal_statuses:
                    terminal_orders.append(order_id)

            for order_id in terminal_orders:
                self.stop_tracking(order_id)

            if terminal_orders:
                logger.info(f"Cleaned up {len(terminal_orders)} terminal orders")

        except Exception as e:
            logger.error(f"Error cleaning up terminal orders: {e}")

    def stop(self):
        """Stop the order tracker."""
        try:
            self.update_timer.stop()
            self.tracked_orders.clear()
            self.last_status.clear()
            logger.info("Order tracker stopped")

        except Exception as e:
            logger.error(f"Error stopping order tracker: {e}")


# Integration helper for main window
def setup_simple_order_tracker(main_window) -> SimpleOrderTracker:
    """
    Setup simple order tracker for the main window.

    Args:
        main_window: Reference to main application window

    Returns:
        SimpleOrderTracker: Configured order tracker
    """
    tracker = SimpleOrderTracker(main_window)

    # Connect tracker signals to main window methods
    tracker.order_completed.connect(main_window._handle_order_completion_notification)
    tracker.order_cancelled.connect(main_window._handle_order_cancellation_notification)
    tracker.order_rejected.connect(main_window._handle_order_rejection_notification)
    tracker.partial_fill_updated.connect(main_window._show_enhanced_partial_fill_notification)

    # Store reference in main window
    main_window.order_tracker = tracker

    logger.info("Simple order tracker setup completed")
    return tracker