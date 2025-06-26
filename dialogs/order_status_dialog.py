import logging
import sys
from typing import Dict, Any
from datetime import datetime
from enum import Enum

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QApplication, QProgressBar, QGraphicsOpacityEffect
)
from PySide6.QtCore import ( Qt, Signal, QTimer,QPoint )
from PySide6.QtCore import QPropertyAnimation, QParallelAnimationGroup, QEasingCurve, QByteArray

logger = logging.getLogger(__name__)


class OrderStatusType(Enum):
    """Order status types with corresponding colors and actions."""
    PENDING = ("PENDING", "#fdcb6e", "Order is pending execution")
    PARTIAL = ("PARTIAL", "#e17055", "Order is partially filled")
    COMPLETE = ("COMPLETE", "#00b894", "Order completed successfully")
    REJECTED = ("REJECTED", "#d63031", "Order was rejected")
    CANCELLED = ("CANCELLED", "#636e72", "Order was cancelled")


class OrderStatusDialog(QWidget):
    """
    Advanced order status dialog that monitors order execution in real-time.
    Appears as a slide-up notification from bottom-right corner and provides
    live updates with options to modify or cancel pending/partial orders.
    """

    # Signals for integration with main window
    cancel_requested = Signal(str)  # order_id
    modify_requested = Signal(dict)  # order_data
    order_completed = Signal(dict)  # order_data when fully filled
    refresh_positions_requested = Signal()  # when order completes
    close_dialog = Signal()  # when dialog should be closed

    def __init__(self, order_data: Dict[str, Any], parent=None, main_window=None):
        super().__init__(parent)
        self.order_id = order_data.get('order_id')
        self.parent_window = main_window if main_window else parent
        self._last_status = None
        self._is_closed = False
        self._status_sources = set()

        # Core data
        self.order_data = order_data.copy()
        self.order_id = order_data.get("order_id", "")
        self.symbol = order_data.get("tradingsymbol", "N/A")
        self.initial_quantity = order_data.get("quantity", 0)

        # Status tracking
        self.current_status = self._determine_status_type()
        self.last_update_time = datetime.now()
        self.is_closing = False

        # Animation objects
        self.slide_animation = None
        self.fade_animation = None
        self.pulse_animation = None
        self.opacity_effect = None

        # Update timer for real-time monitoring
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self._refresh_order_status)

        # Auto-close timer for completed orders
        self.auto_close_timer = QTimer()
        self.auto_close_timer.timeout.connect(self._auto_close)
        self.auto_close_timer.setSingleShot(True)

        self._setup_ui()
        self._apply_advanced_styles()
        self._position_dialog()
        self._start_monitoring()
        self.show()
        self._animate_entrance()

    def _setup_ui(self):
        """Setup the complete UI layout with modern design."""
        # Window configuration
        self.setWindowFlags(
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(380, 160)

        # Main container with rounded corners
        self.container = QFrame(self)
        self.container.setObjectName("mainContainer")
        self.container.setGeometry(0, 0, 380, 160)

        # Main layout
        main_layout = QVBoxLayout(self.container)
        main_layout.setContentsMargins(20, 16, 20, 16)
        main_layout.setSpacing(8)

        # Header section
        self._create_header_section(main_layout)

        # Progress section
        self._create_progress_section(main_layout)

        # Order details section
        self._create_details_section(main_layout)

        # Action buttons section
        self._create_action_section(main_layout)

        # Status indicator (pulsing dot)
        self._create_status_indicator()

    def _create_header_section(self, layout):
        """Create the header with symbol and status."""
        header_layout = QHBoxLayout()
        header_layout.setSpacing(12)

        # Symbol label
        self.symbol_label = QLabel(self.symbol)
        self.symbol_label.setObjectName("symbolLabel")
        header_layout.addWidget(self.symbol_label)

        header_layout.addStretch()

        # Status badge
        self.status_label = QLabel(self.current_status.value[0])
        self.status_label.setObjectName("statusBadge")
        header_layout.addWidget(self.status_label)

        # Close button
        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("closeButton")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.clicked.connect(self._close_dialog)
        header_layout.addWidget(self.close_btn)

        layout.addLayout(header_layout)

    def _create_progress_section(self, layout):
        """Create progress bar showing fill percentage."""
        progress_layout = QVBoxLayout()
        progress_layout.setSpacing(4)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("fillProgress")
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMaximum(100)

        filled_qty = self.order_data.get("filled_quantity", 0)
        total_qty = self.order_data.get("quantity", 1)
        progress_value = int((filled_qty / total_qty) * 100) if total_qty > 0 else 0
        self.progress_bar.setValue(progress_value)

        progress_layout.addWidget(self.progress_bar)

        # Progress text
        self.progress_text = QLabel(f"Filled: {filled_qty}/{total_qty} ({progress_value}%)")
        self.progress_text.setObjectName("progressText")
        progress_layout.addWidget(self.progress_text)

        layout.addLayout(progress_layout)

    def _create_details_section(self, layout):
        """Create order details display."""
        details_layout = QHBoxLayout()
        details_layout.setSpacing(16)

        # Transaction type and quantity
        trans_type = self.order_data.get("transaction_type", "").upper()
        quantity = self.order_data.get("quantity", 0)
        self.transaction_label = QLabel(f"{trans_type} {quantity}")
        self.transaction_label.setObjectName("transactionLabel")
        details_layout.addWidget(self.transaction_label)

        # Price information
        price = self.order_data.get("price", 0.0)
        order_type = self.order_data.get("order_type", "MARKET")
        price_text = f"@ ₹{price:,.2f}" if order_type != "MARKET" else "@ MARKET"
        self.price_label = QLabel(price_text)
        self.price_label.setObjectName("priceLabel")
        details_layout.addWidget(self.price_label)

        details_layout.addStretch()

        # Time elapsed
        self.time_label = QLabel("Just now")
        self.time_label.setObjectName("timeLabel")
        details_layout.addWidget(self.time_label)

        layout.addLayout(details_layout)

    def _create_action_section(self, layout):
        """Create action buttons based on order status."""
        action_layout = QHBoxLayout()
        action_layout.setSpacing(8)

        action_layout.addStretch()

        # Modify button (only for pending/partial orders)
        self.modify_btn = QPushButton("MODIFY")
        self.modify_btn.setObjectName("modifyButton")
        self.modify_btn.clicked.connect(self._on_modify_clicked)
        action_layout.addWidget(self.modify_btn)

        # Cancel button (only for pending/partial orders)
        self.cancel_btn = QPushButton("CANCEL")
        self.cancel_btn.setObjectName("cancelButton")
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        action_layout.addWidget(self.cancel_btn)

        layout.addLayout(action_layout)

        # Update button visibility based on status
        self._update_action_buttons()

    def _create_status_indicator(self):
        """Create animated status indicator dot."""
        self.status_dot = QLabel(self.container)
        self.status_dot.setFixedSize(12, 12)
        self.status_dot.setObjectName("statusDot")
        self.status_dot.move(self.width() - 20, 8)

        # Opacity effect for pulsing animation
        self.opacity_effect = QGraphicsOpacityEffect()
        self.status_dot.setGraphicsEffect(self.opacity_effect)

    def _determine_status_type(self) -> OrderStatusType:
        """Determine the current status type based on order data."""
        status = self.order_data.get("status", "").upper()
        filled_qty = self.order_data.get("filled_quantity", 0)
        total_qty = self.order_data.get("quantity", 0)

        if status in ["COMPLETE", "EXECUTED"]:
            return OrderStatusType.COMPLETE
        elif status in ["CANCELLED"]:
            return OrderStatusType.CANCELLED
        elif status in ["REJECTED"]:
            return OrderStatusType.REJECTED
        elif filled_qty > 0 and filled_qty < total_qty:
            return OrderStatusType.PARTIAL
        else:
            return OrderStatusType.PENDING

    def _position_dialog(self):
        """Position dialog in bottom-right corner of screen."""
        if not self.parent():
            # Get primary screen geometry
            screen = QApplication.primaryScreen().availableGeometry()
            x = screen.width() - self.width() - 20
            y = screen.height() - self.height() - 20
        else:
            # Position relative to parent window
            parent_rect = self.parent().geometry()
            x = parent_rect.right() - self.width() - 20
            y = parent_rect.bottom() - self.height() - 20

        # Start position (below screen for slide-up animation)
        self.setGeometry(x, y + self.height(), self.width(), self.height())
        self.target_position = QPoint(x, y)

    def _animate_entrance(self):
        """Animate dialog sliding up from bottom."""
        self.slide_animation = QPropertyAnimation(self, QByteArray(b"pos"))
        self.slide_animation.setDuration(400)
        self.slide_animation.setStartValue(self.pos())
        self.slide_animation.setEndValue(self.target_position)
        self.slide_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Fade in animation
        self.fade_animation = QPropertyAnimation(self, QByteArray(b"windowOpacity"))
        self.fade_animation.setDuration(400)
        self.fade_animation.setStartValue(0.0)
        self.fade_animation.setEndValue(1.0)

        # Start animations together
        self.entrance_group = QParallelAnimationGroup()
        self.entrance_group.addAnimation(self.slide_animation)
        self.entrance_group.addAnimation(self.fade_animation)
        self.entrance_group.finished.connect(self._start_pulse_animation)
        self.entrance_group.start()

    def _start_pulse_animation(self):
        """Start pulsing animation for status indicator."""
        if self.current_status in [OrderStatusType.PENDING, OrderStatusType.PARTIAL]:
            self.pulse_animation = QPropertyAnimation(self.opacity_effect, QByteArray(b"opacity"))
            self.pulse_animation.setDuration(1500)
            self.pulse_animation.setStartValue(1.0)
            self.pulse_animation.setEndValue(0.3)
            self.pulse_animation.setLoopCount(-1)  # Infinite loop
            self.pulse_animation.start()

    def _start_monitoring(self):
        """Start real-time order status monitoring."""
        # Update every 500ms for active orders
        if self.current_status in [OrderStatusType.PENDING, OrderStatusType.PARTIAL]:
            self.update_timer.start(500)

        logger.info(f"Started monitoring order {self.order_id} with status {self.current_status.value[0]}")

    def _refresh_order_status(self):
        """Refreshes order status directly from the main window."""
        try:
            updated_data = None
            if hasattr(self, 'parent_window') and self.parent_window and hasattr(self.parent_window,
                                                                                 'get_order_status'):
                updated_data = self.parent_window.get_order_status(self.order_id)
            else:
                logger.warning(
                    f"Could not refresh order {self.order_id}: main_window or get_order_status method not found.")
                # Stop the timer if we can't refresh, to prevent repeated errors.
                self.update_timer.stop()
                return

            if updated_data:
                self._update_order_data(updated_data)

            # Update time elapsed
            elapsed = datetime.now() - self.last_update_time
            if elapsed.total_seconds() < 60:
                time_text = f"{int(elapsed.total_seconds())}s ago"
            else:
                time_text = f"{int(elapsed.total_seconds() // 60)}m ago"

            if hasattr(self, 'time_label'):
                self.time_label.setText(time_text)

        except Exception as e:
            logger.error(f"Error refreshing order status for {self.order_id}: {e}", exc_info=True)
            self.update_timer.stop()  # Stop on error to prevent crash loops

    def _update_order_data(self, new_data: Dict[str, Any]):
        """Update order data and UI elements."""
        old_status = self.current_status
        self.order_data.update(new_data)
        self.current_status = self._determine_status_type()

        # Update progress
        filled_qty = self.order_data.get("filled_quantity", 0)
        total_qty = self.order_data.get("quantity", 1)
        progress_value = int((filled_qty / total_qty) * 100) if total_qty > 0 else 0

        self.progress_bar.setValue(progress_value)
        self.progress_text.setText(f"Filled: {filled_qty}/{total_qty} ({progress_value}%)")

        # Update status if changed
        if old_status != self.current_status:
            self._handle_status_change(old_status, self.current_status)

        self.last_update_time = datetime.now()

    def _handle_status_change(self, old_status: OrderStatusType, new_status: OrderStatusType):
        """Handle order status changes - updated to prevent toast conflicts."""
        logger.info(f"Order {self.order_id} status changed: {old_status.value[0]} -> {new_status.value[0]}")

        # Update status label
        self.status_label.setText(new_status.value[0])

        # Update styling
        self._update_status_styling()

        # Handle specific status changes
        if new_status == OrderStatusType.COMPLETE:
            # For completion, hide immediately and let main window show toast
            self._handle_order_completion_and_hide()
        elif new_status == OrderStatusType.CANCELLED:
            self._handle_order_cancellation()
        elif new_status == OrderStatusType.REJECTED:
            self._handle_order_rejection()

        # Update action buttons
        self._update_action_buttons()

        # Stop/start monitoring as needed
        if new_status in [OrderStatusType.COMPLETE, OrderStatusType.CANCELLED, OrderStatusType.REJECTED]:
            self.update_timer.stop()
            if self.pulse_animation:
                self.pulse_animation.stop()

            # Auto-close for non-complete statuses
            if new_status != OrderStatusType.COMPLETE and not self.is_closing:
                self.auto_close_timer.start(3000)

    def _handle_order_completion_and_hide(self):
        """Handle order completion with immediate hide to allow main window toast."""
        logger.info(f"Order {self.order_id} completed - hiding dialog for main window toast")

        if self.is_closing:
            return

        try:
            self.update_timer.stop()
            if self.pulse_animation:
                self.pulse_animation.stop()

            self.is_closing = True
            self._is_closed = True

            # Emit completion signal so the main window can take over
            self.order_completed.emit(self.order_data)
            self.refresh_positions_requested.emit()

            # Hide immediately and schedule for deletion
            self.hide()
            self.deleteLater()

            if hasattr(self, 'parent_window') and self.parent_window and hasattr(self.parent_window,
                                                                                 'order_status_dialog'):
                if self.parent_window.order_status_dialog is self:
                    self.parent_window.order_status_dialog = None

            logger.info(f"Order status dialog for {self.order_id} has been hidden and scheduled for deletion.")

        except Exception as e:
            logger.error(f"Error hiding order status dialog for completion: {e}", exc_info=True)

    def _handle_order_completion(self):
        """Legacy method - now redirects to new completion handler."""
        self._handle_order_completion_and_hide()

    def _handle_order_cancellation(self):
        """Handle order cancellation."""
        logger.info(f"Order {self.order_id} was cancelled")
        self.progress_text.setText("Order was cancelled")

    def _handle_order_rejection(self):
        """Handle order rejection."""
        logger.info(f"Order {self.order_id} was rejected")
        reason = self.order_data.get("status_message", "Unknown reason")
        self.progress_text.setText(f"Order rejected: {reason}")

    def _update_action_buttons(self):
        """Update action button visibility based on current status."""
        show_actions = self.current_status in [OrderStatusType.PENDING, OrderStatusType.PARTIAL]
        self.modify_btn.setVisible(show_actions)
        self.cancel_btn.setVisible(show_actions)

    def _update_status_styling(self):
        """Update styling based on current status."""
        status_color = self.current_status.value[1]

        # Update status badge color
        self.status_label.setStyleSheet(f"""
            #statusBadge {{
                background-color: {status_color};
                color: white;
                font-weight: bold;
                font-size: 10px;
                padding: 4px 8px;
                border-radius: 8px;
            }}
        """)

        # Update progress bar color
        self.progress_bar.setStyleSheet(f"""
            #fillProgress {{
                border: 1px solid #3a3a5a;
                border-radius: 4px;
                background-color: #2a2a4a;
            }}
            #fillProgress::chunk {{
                background-color: {status_color};
                border-radius: 3px;
            }}
        """)

    def _on_modify_clicked(self):
        """Handle modify button click - cancel existing order and open order dialog."""
        logger.info(f"Modify requested for order {self.order_id}")

        # Disable buttons to prevent multiple clicks
        self.modify_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.modify_btn.setText("CANCELLING...")

        # Signal to cancel the existing order and open modify dialog
        self.modify_requested.emit(self.order_data)

    def _on_cancel_clicked(self):
        """Handle cancel button click."""
        logger.info(f"Cancel requested for order {self.order_id}")
        self.cancel_requested.emit(self.order_id)

    def _close_dialog(self):
        """Close dialog and mark as closed."""
        self._is_closed = True
        self.close()

    def _auto_close(self):
        """Auto-close dialog for completed orders."""
        self._close_dialog()

    def update_from_external(self, new_data: Dict[str, Any]):
        """Update dialog from external order data (WebSocket or API)."""
        if self.is_closing or self._is_closed:
            return

        try:
            # Store old status before update
            old_status = self.current_status

            # Update order data
            self.order_data.update(new_data)
            new_status = self._determine_status_type()  # Fix: Define new_status properly
            self.current_status = new_status

            # Check if we should hide immediately for completion
            if new_status == OrderStatusType.COMPLETE:
                self._handle_order_completion_and_hide()
                return

            # Update progress for non-completion updates
            filled_qty = self.order_data.get("filled_quantity", 0)
            total_qty = self.order_data.get("quantity", 1)
            progress_value = int((filled_qty / total_qty) * 100) if total_qty > 0 else 0

            self.progress_bar.setValue(progress_value)
            self.progress_text.setText(f"Filled: {filled_qty}/{total_qty} ({progress_value}%)")

            # Update status if changed
            if old_status != self.current_status:
                self._handle_status_change(old_status, self.current_status)

            self.last_update_time = datetime.now()

        except Exception as e:
            logger.error(f"Error updating order status dialog: {e}")

    def _apply_advanced_styles(self):
        """Apply modern, advanced styling to the dialog."""
        self.setStyleSheet("""
            /* Main Container */
            #mainContainer {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #2c2c54, stop:1 #24243e);
                border: 1px solid #3a3a6b;
                border-radius: 12px;
                border-top: 2px solid #4a4a7a;
            }

            /* Symbol Label */
            #symbolLabel {
                color: #ffffff;
                font-size: 16px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }

            /* Status Badge */
            #statusBadge {
                background-color: #fdcb6e;
                color: #2d3436;
                font-weight: bold;
                font-size: 10px;
                padding: 4px 8px;
                border-radius: 8px;
            }

            /* Close Button */
            #closeButton {
                background-color: transparent;
                color: #ddd;
                font-size: 16px;
                font-weight: bold;
                border: none;
                border-radius: 12px;
            }
            #closeButton:hover {
                background-color: #d63031;
                color: white;
            }

            /* Progress Bar */
            #fillProgress {
                border: 1px solid #3a3a5a;
                border-radius: 4px;
                background-color: #2a2a4a;
                height: 8px;
            }
            #fillProgress::chunk {
                background-color: #fdcb6e;
                border-radius: 3px;
            }

            /* Progress Text */
            #progressText {
                color: #b2bec3;
                font-size: 11px;
                font-weight: 500;
            }

            /* Transaction and Price Labels */
            #transactionLabel {
                color: #e0e0e0;
                font-size: 13px;
                font-weight: 600;
            }
            #priceLabel {
                color: #74b9ff;
                font-size: 13px;
                font-weight: 500;
            }
            #timeLabel {
                color: #636e72;
                font-size: 11px;
                font-style: italic;
            }

            /* Action Buttons */
            QPushButton {
                font-family: "Segoe UI";
                font-weight: 600;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 11px;
                border: none;
                letter-spacing: 0.5px;
            }

            #modifyButton {
                background-color: #4a4a6a;
                color: #e0e0e0;
            }
            #modifyButton:hover {
                background-color: #5a5a7a;
            }

            #cancelButton {
                background-color: #d63031;
                color: #ffffff;
            }
            #cancelButton:hover {
                background-color: #e17055;
            }

            /* Status Indicator Dot */
            #statusDot {
                background-color: #fdcb6e;
                border-radius: 6px;
                border: 2px solid #3a3a6b;
            }
        """)


def create_order_status_dialog(main_window, order_data: Dict[str, Any]) -> 'OrderStatusDialog':
    """
    Enhanced factory function that ensures proper main window reference.

    Args:
        main_window: Reference to the main application window
        order_data: Order data dictionary with order details

    Returns:
        OrderStatusDialog: Configured dialog instance with proper main window reference
    """
    # Pass main_window explicitly to ensure proper reference
    dialog = OrderStatusDialog(order_data, parent=main_window, main_window=main_window)

    # Connect signals to main window methods
    if hasattr(main_window, '_handle_order_cancellation'):
        dialog.cancel_requested.connect(main_window._handle_order_cancellation)

    if hasattr(main_window, '_handle_order_modification'):
        dialog.modify_requested.connect(main_window._handle_order_modification)

    if hasattr(main_window, '_refresh_positions_table'):
        dialog.refresh_positions_requested.connect(main_window._refresh_positions_table)

    if hasattr(main_window, '_on_order_completed'):
        dialog.order_completed.connect(main_window._on_order_completed)

    logger.info(f"Created order status dialog for order {order_data.get('order_id', 'N/A')}")
    return dialog