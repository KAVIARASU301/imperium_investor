import logging
import sys
from typing import Dict, Any, Optional, List, cast
from datetime import datetime
from enum import Enum

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QApplication, QGraphicsOpacityEffect, QStackedWidget, QGraphicsEffect,
    QGraphicsDropShadowEffect
)
from PySide6.QtCore import (Qt, Signal, QTimer, QRect)
from PySide6.QtCore import QPropertyAnimation, QParallelAnimationGroup, QEasingCurve, QPoint, QByteArray
from PySide6.QtGui import QFont, QPixmap, QPainter, QColor, QLinearGradient

logger = logging.getLogger(__name__)


class NotificationType(Enum):
    """Notification types with corresponding styles and behavior."""
    SUCCESS = ("success", "#00b894", "✓", 4000, True)
    ERROR = ("error", "#d63031", "✗", 8000, False)  # Stay longer for errors
    INFO = ("info", "#74b9ff", "ℹ", 4000, True)
    WARNING = ("warning", "#fdcb6e", "⚠", 5000, True)
    ORDER_PLACED = ("order_placed", "#6c5ce7", "📝", 4000, True)
    ORDER_EXECUTED = ("order_executed", "#00b894", "✅", 4000, True)
    ORDER_CANCELLED = ("order_cancelled", "#636e72", "❌", 3000, True)
    ORDER_REJECTED = ("order_rejected", "#d63031", "🚫", 6000, False)
    PARTIAL_FILL = ("partial_fill", "#e17055", "📊", 5000, True)
    POSITION_UPDATE = ("position_update", "#00cec9", "💰", 3000, True)
    ALERT = ("alert", "#fd79a8", "🔔", 6000, False)
    SYSTEM = ("system", "#a29bfe", "⚙", 4000, True)

    def __init__(self, type_name, color, icon, duration, auto_close):
        self.type_name = type_name
        self.color = color
        self.icon = icon
        self.duration = duration
        self.auto_close = auto_close


class NotificationDialog(QWidget):
    """
    Premium notification widget with dark theme and excellent readability.
    Features elegant animations, rich visual design, and professional appearance.
    """

    notification_clicked = Signal(dict)  # Emits notification data when clicked
    notification_closed = Signal(str)  # Emits notification ID when closed

    def __init__(self, message: str, notification_type: NotificationType,
                 notification_id: str = None, action_data: Dict[str, Any] = None, parent=None):
        super().__init__(parent)

        # Core properties
        self.message = message
        self.notification_type = notification_type
        self.notification_id = notification_id or f"notif_{int(datetime.now().timestamp() * 1000)}"
        self.action_data = action_data or {}
        self.created_at = datetime.now()

        # Animation properties
        self.slide_animation = None
        self.fade_animation = None
        self.pulse_animation = None
        self.is_closing = False

        # Auto-close timer
        self.auto_close_timer = QTimer()
        self.auto_close_timer.timeout.connect(self._auto_close)
        self.auto_close_timer.setSingleShot(True)

        # Hover timer for pause/resume
        self.hover_timer = QTimer()
        self.hover_timer.timeout.connect(self._resume_auto_close)
        self.hover_timer.setSingleShot(True)

        self._setup_ui()
        self._apply_premium_styles()
        self._add_drop_shadow()
        self._position_notification()
        self._start_entrance_animation()
        self._start_auto_close_timer()

    def _setup_ui(self):
        """Setup the premium notification UI with enhanced visual elements."""
        # Window configuration
        self.setWindowFlags(
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(320, 65)  # More compact size
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Main container with rounded corners
        self.container = QFrame(self)
        self.container.setObjectName("notificationContainer")
        self.container.setGeometry(0, 0, 320, 65)
        self.container.mousePressEvent = self._on_notification_clicked

        # Main layout
        main_layout = QHBoxLayout(self.container)
        main_layout.setContentsMargins(12, 8, 12, 8)  # Reduced padding
        main_layout.setSpacing(10)  # Reduced spacing

        # Icon section with enhanced styling
        self.icon_container = QFrame()
        self.icon_container.setObjectName("iconContainer")
        self.icon_container.setFixedSize(26, 26)  # Smaller icon container

        icon_layout = QVBoxLayout(self.icon_container)
        icon_layout.setContentsMargins(0, 0, 0, 0)

        self.icon_label = QLabel(self.notification_type.icon)
        self.icon_label.setObjectName("notificationIcon")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setFont(QFont("Segoe UI Emoji", 12, QFont.Weight.Bold))  # Smaller icon
        icon_layout.addWidget(self.icon_label)

        main_layout.addWidget(self.icon_container)

        # Content section
        content_layout = QVBoxLayout()
        content_layout.setSpacing(2)  # Minimal spacing
        content_layout.setContentsMargins(0, 0, 0, 0)

        # Message text with premium typography
        self.message_label = QLabel(self.message)
        self.message_label.setObjectName("notificationMessage")
        self.message_label.setWordWrap(True)
        self.message_label.setFont(QFont("Segoe UI", 11, QFont.Weight.Medium))  # Smaller text
        content_layout.addWidget(self.message_label)

        # Timestamp with subtle styling
        time_str = self.created_at.strftime("%H:%M:%S")
        self.time_label = QLabel(time_str)
        self.time_label.setObjectName("notificationTime")
        self.time_label.setFont(QFont("Segoe UI", 8, QFont.Weight.Normal))  # Smaller timestamp
        content_layout.addWidget(self.time_label)

        main_layout.addLayout(content_layout, 1)

        # Enhanced close button
        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("notificationClose")
        self.close_btn.setFixedSize(20, 20)  # Smaller close button
        self.close_btn.setFont(QFont("Arial", 12, QFont.Weight.Bold))  # Smaller font
        self.close_btn.clicked.connect(self._close_notification)
        main_layout.addWidget(self.close_btn)

        # Premium progress bar with glow effect
        self.progress_bar = QFrame(self.container)
        self.progress_bar.setObjectName("progressBar")
        self.progress_bar.setFixedHeight(2)  # Thinner progress bar
        self.progress_bar.setGeometry(2, 61, 316, 2)  # Adjusted for border

        # Remove accent line since we now have full border
        # self.accent_line = QFrame(self.container)
        # self.accent_line.setObjectName("accentLine")
        # self.accent_line.setFixedWidth(3)  # Thinner accent line
        # self.accent_line.setGeometry(0, 0, 3, 65)

        # Install event filters for hover detection
        self.container.enterEvent = self._on_mouse_enter
        self.container.leaveEvent = self._on_mouse_leave

    def _add_drop_shadow(self):
        """Add premium drop shadow effect."""
        try:
            shadow = QGraphicsDropShadowEffect()
            shadow.setBlurRadius(15)
            shadow.setXOffset(0)
            shadow.setYOffset(3)
            shadow.setColor(QColor(0, 0, 0, 60))
            self.container.setGraphicsEffect(shadow)
        except Exception as e:
            logger.debug(f"Could not apply drop shadow: {e}")

    def _position_notification(self):
        """Position notification in bottom-right corner."""
        if not self.parent():
            # Get primary screen geometry
            screen = QApplication.primaryScreen().availableGeometry()
            x = screen.width() - self.width() - 24
            y = screen.height() - self.height() - 24
        else:
            # Position relative to a parent window
            parent_rect = self.parent().geometry()
            x = parent_rect.right() - self.width() - 24
            y = parent_rect.bottom() - self.height() - 24

        # Start position (off-screen to the right)
        self.setGeometry(x + self.width(), y, self.width(), self.height())
        self.target_position = QPoint(x, y)

    def _start_entrance_animation(self):
        """Animate notification with smooth slide and scale effect."""
        self.slide_animation = QPropertyAnimation(self, QByteArray(b"pos"))
        self.slide_animation.setDuration(400)
        self.slide_animation.setStartValue(self.pos())
        self.slide_animation.setEndValue(self.target_position)
        self.slide_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Fade in animation
        self.fade_animation = QPropertyAnimation(self, QByteArray(b"windowOpacity"))
        self.fade_animation.setDuration(400)
        self.fade_animation.setStartValue(0.0)
        self.fade_animation.setEndValue(0.95)

        # Start animations together
        self.entrance_group = QParallelAnimationGroup()
        self.entrance_group.addAnimation(self.slide_animation)
        self.entrance_group.addAnimation(self.fade_animation)
        self.entrance_group.start()

    def _start_auto_close_timer(self):
        """Start auto-close timer if enabled."""
        if self.notification_type.auto_close:
            self.auto_close_timer.start(self.notification_type.duration)
            self._start_progress_animation()

    def _start_progress_animation(self):
        """Animate progress bar with smooth countdown."""
        self.progress_animation = QPropertyAnimation(self.progress_bar, QByteArray(b"geometry"))
        self.progress_animation.setDuration(self.notification_type.duration)
        start_rect = QRect(2, 61, 316, 2)  # Updated for border adjustment
        end_rect = QRect(2, 61, 0, 2)
        self.progress_animation.setStartValue(start_rect)
        self.progress_animation.setEndValue(end_rect)
        self.progress_animation.setEasingCurve(QEasingCurve.Type.Linear)
        self.progress_animation.start()

    def _on_mouse_enter(self, event):
        """Enhanced hover effect with smooth transitions."""
        if self.auto_close_timer.isActive():
            self.auto_close_timer.stop()
            if hasattr(self, 'progress_animation') and self.progress_animation:
                self.progress_animation.pause()

        # Create enhanced hover effect without graphics effects to avoid edge issues
        try:
            # Just change the container style directly for cleaner hover
            self.container.setStyleSheet(f"""
                #notificationContainer {{
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 rgb(25, 25, 25),
                        stop:0.3 {self._hex_to_rgba(self.notification_type.color, 0.08)},
                        stop:0.7 {self._hex_to_rgba(self.notification_type.color, 0.06)},
                        stop:1 rgb(18, 18, 18));
                    border: 2px solid {self.notification_type.color};
                    border-radius: 8px;
                }}
            """)
        except Exception as e:
            logger.debug(f"Error in mouse enter effect: {e}")

    def _on_mouse_leave(self, event):
        """Remove hover effects and resume auto-close."""
        try:
            # Reset to original styling
            self._apply_premium_styles()

            if self.notification_type.auto_close and not self.is_closing:
                remaining_time = self.notification_type.duration // 4
                self.hover_timer.start(remaining_time)
        except Exception as e:
            logger.debug(f"Error in mouse leave effect: {e}")

    def _hex_to_rgba(self, hex_color, alpha):
        """Convert hex color to rgba for various alpha levels"""
        hex_color = hex_color.lstrip('#')
        if len(hex_color) == 6:
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
            return f"rgba({r}, {g}, {b}, {alpha})"
        return hex_color

    def _resume_auto_close(self):
        """Resume auto-close after hover."""
        if not self.is_closing and self.notification_type.auto_close:
            self._auto_close()

    def _on_notification_clicked(self, event):
        """Handle notification click with ripple effect."""
        if self.action_data:
            self.notification_clicked.emit({
                'notification_id': self.notification_id,
                'type': self.notification_type.type_name,
                'message': self.message,
                'action_data': self.action_data
            })
        self._close_notification()

    def _auto_close(self):
        """Auto-close notification after timeout."""
        if not self.is_closing:
            self._close_notification()

    def _close_notification(self):
        """Close notification with elegant exit animation."""
        if self.is_closing:
            return

        self.is_closing = True
        self.auto_close_timer.stop()
        if hasattr(self, 'progress_animation'):
            self.progress_animation.stop()

        # Slide out animation
        self.close_slide_animation = QPropertyAnimation(self, QByteArray(b"pos"))
        self.close_slide_animation.setDuration(300)
        self.close_slide_animation.setStartValue(self.pos())
        end_pos = QPoint(self.pos().x() + self.width(), self.pos().y())
        self.close_slide_animation.setEndValue(end_pos)
        self.close_slide_animation.setEasingCurve(QEasingCurve.Type.InCubic)

        # Fade out animation
        self.close_fade_animation = QPropertyAnimation(self, QByteArray(b"windowOpacity"))
        self.close_fade_animation.setDuration(300)
        self.close_fade_animation.setStartValue(0.95)
        self.close_fade_animation.setEndValue(0.0)

        # Close after animation
        self.close_group = QParallelAnimationGroup()
        self.close_group.addAnimation(self.close_slide_animation)
        self.close_group.addAnimation(self.close_fade_animation)
        self.close_group.finished.connect(self._on_close_finished)
        self.close_group.start()

    def _on_close_finished(self):
        """Handle cleanup after close animation."""
        self.notification_closed.emit(self.notification_id)
        self.close()

    def _apply_premium_styles(self):
        """Apply premium dark theme styling with excellent readability."""
        color = self.notification_type.color

        # Convert hex color to rgba for various alpha levels
        def hex_to_rgba(hex_color, alpha):
            hex_color = hex_color.lstrip('#')
            if len(hex_color) == 6:
                r = int(hex_color[0:2], 16)
                g = int(hex_color[2:4], 16)
                b = int(hex_color[4:6], 16)
                return f"rgba({r}, {g}, {b}, {alpha})"
            return hex_color

        # Rich color palette
        primary_color = color
        bg_color = "rgba(18, 18, 18, 0.98)"  # Almost solid dark background
        border_color = hex_to_rgba(color, 0.4)
        icon_bg = hex_to_rgba(color, 0.2)
        accent_color = hex_to_rgba(color, 0.8)

        self.setStyleSheet(f"""
            /* Main Container - Premium Dark Theme */
            #notificationContainer {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgb(18, 18, 18),
                    stop:0.3 {hex_to_rgba(color, 0.05)},
                    stop:0.7 {hex_to_rgba(color, 0.03)},
                    stop:1 rgb(12, 12, 12));
                border: 2px solid {primary_color};
                border-radius: 8px;
            }}

            /* Icon Container */
            #iconContainer {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {hex_to_rgba(color, 0.15)},
                    stop:1 rgb(25, 25, 25));
                border: 1px solid {primary_color};
                border-radius: 6px;
            }}

            /* Icon Styling */
            #notificationIcon {{
                color: {primary_color};
                background: transparent;
            }}

            /* Message Text - High Contrast */
            #notificationMessage {{
                color: #ffffff;
                background: transparent;
                font-weight: 500;
                line-height: 1.2;
                margin: 0;
                padding: 0;
                max-height: 32px;
            }}

            /* Time Label - Subtle but Readable */
            #notificationTime {{
                color: #b0b0b0;
                background: transparent;
                font-weight: 400;
                margin: 0;
                padding: 0;
                max-height: 12px;
            }}

            /* Enhanced Close Button */
            #notificationClose {{
                background: rgb(45, 45, 45);
                color: #cccccc;
                border: 1px solid rgb(70, 70, 70);
                border-radius: 4px;
                font-weight: bold;
            }}
            #notificationClose:hover {{
                background: rgb(200, 50, 50);
                color: #ffffff;
                border: 1px solid rgb(220, 70, 70);
            }}
            #notificationClose:pressed {{
                background: rgb(160, 30, 30);
                border: 1px solid rgb(180, 50, 50);
            }}

            /* Premium Progress Bar */
            #progressBar {{
                background: {primary_color};
                border: none;
                border-radius: 0px;
            }}

            /* Accent Line - REMOVED since we now have full border */
        """)


class NotificationManager:
    """
    Enhanced notification manager with premium visual effects and smart positioning.
    """

    def __init__(self, parent_window=None):
        self.parent_window = parent_window
        self.active_notifications: List[NotificationDialog] = []
        self.notification_spacing = 75  # Adjusted spacing for compact notifications
        self.max_notifications = 4  # Reduced for better UX

        # Sound effects (if available)
        self.sounds = {}
        self._setup_sounds()

    def _setup_sounds(self):
        """Setup enhanced sound effects for different notification types."""
        try:
            from PySide6.QtMultimedia import QSoundEffect
            from PySide6.QtCore import QUrl
            import os

            # Initialize sounds if files exist
            sound_files = {
                NotificationType.SUCCESS: "sounds/success.wav",
                NotificationType.ERROR: "sounds/error.wav",
                NotificationType.ORDER_PLACED: "sounds/order_placed.wav",
                NotificationType.ORDER_EXECUTED: "sounds/order_executed.wav",
                NotificationType.ALERT: "sounds/alert.wav"
            }

            for notif_type, file_path in sound_files.items():
                try:
                    if os.path.exists(file_path):
                        sound = QSoundEffect()
                        sound.setSource(QUrl.fromLocalFile(file_path))
                        sound.setVolume(0.4)  # Slightly higher volume for premium feel
                        self.sounds[notif_type] = sound
                    else:
                        logger.debug(f"Sound file not found: {file_path}")
                except Exception as e:
                    logger.debug(f"Could not load sound {file_path}: {e}")

        except ImportError:
            logger.debug("QtMultimedia not available for sounds")

    def show_notification(self, message: str, notification_type: NotificationType,
                          action_data: Dict[str, Any] = None, silent: bool = False) -> str:
        """
        Show a premium notification with enhanced visual effects.

        Args:
            message: Notification message text
            notification_type: Type of notification (determines styling and behavior)
            action_data: Optional data for click actions
            silent: If True, don't play sound

        Returns:
            notification_id: Unique ID of the created notification
        """
        try:
            # Clean up closed notifications
            self._cleanup_closed_notifications()

            # Remove oldest notification if at max capacity
            if len(self.active_notifications) >= self.max_notifications:
                oldest = self.active_notifications[0]
                oldest._close_notification()

            # Create new premium notification
            notification = NotificationDialog(
                message=message,
                notification_type=notification_type,
                action_data=action_data,
                parent=self.parent_window
            )

            # Position notification accounting for existing ones
            self._position_notification(notification)

            # Connect signals
            notification.notification_closed.connect(self._on_notification_closed)
            if action_data:
                notification.notification_clicked.connect(self._on_notification_clicked)

            # Add to active list
            self.active_notifications.append(notification)

            # Show notification
            notification.show()

            # Play enhanced sound
            if not silent and notification_type in self.sounds:
                self.sounds[notification_type].play()

            logger.debug(f"Showed premium notification: {notification.notification_id}")
            return notification.notification_id

        except Exception as e:
            logger.error(f"Error showing notification: {e}")
            return ""

    def _position_notification(self, notification: NotificationDialog):
        """Position notification with premium spacing."""
        base_y = notification.target_position.y()

        # Move up for each existing notification with enhanced spacing
        offset = len(self.active_notifications) * self.notification_spacing
        new_y = base_y - offset

        notification.target_position = QPoint(notification.target_position.x(), new_y)

        # Also adjust the starting position for slide animation
        start_pos = notification.pos()
        notification.setGeometry(start_pos.x(), new_y, notification.width(), notification.height())

    def _cleanup_closed_notifications(self):
        """Remove closed notifications from active list."""
        self.active_notifications = [n for n in self.active_notifications if not n.is_closing]

    def _on_notification_closed(self, notification_id: str):
        """Handle notification close with smooth repositioning."""
        try:
            # Remove from active list
            self.active_notifications = [
                n for n in self.active_notifications
                if n.notification_id != notification_id
            ]

            # Smoothly reposition remaining notifications
            self._reposition_notifications()

        except Exception as e:
            logger.error(f"Error handling notification close: {e}")

    def _reposition_notifications(self):
        """Smoothly reposition all active notifications with enhanced animations."""
        try:
            for i, notification in enumerate(self.active_notifications):
                if not notification.is_closing:
                    # Calculate new position
                    base_y = notification.target_position.y() + (
                            len(self.active_notifications) - 1 - i
                    ) * self.notification_spacing
                    new_pos = QPoint(notification.target_position.x(), base_y)

                    # Animate to new position with smooth easing
                    animation = QPropertyAnimation(notification, QByteArray(b"pos"))
                    animation.setDuration(300)
                    animation.setStartValue(notification.pos())
                    animation.setEndValue(new_pos)
                    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
                    animation.start()

        except Exception as e:
            logger.error(f"Error repositioning notifications: {e}")

    def _on_notification_clicked(self, notification_data: Dict[str, Any]):
        """Handle notification click events with enhanced functionality."""
        try:
            action_data = notification_data.get('action_data', {})
            action_type = action_data.get('action_type')

            if action_type == 'show_order_history':
                if hasattr(self.parent_window, '_show_order_history'):
                    self.parent_window._show_order_history()

            elif action_type == 'show_positions':
                if hasattr(self.parent_window, 'positions_table'):
                    self.parent_window.positions_table.setFocus()

            elif action_type == 'open_order_dialog':
                symbol = action_data.get('symbol')
                if symbol and hasattr(self.parent_window, '_show_advanced_order_dialog'):
                    self.parent_window._show_advanced_order_dialog(symbol)

            logger.debug(f"Handled notification click: {action_type}")

        except Exception as e:
            logger.error(f"Error handling notification click: {e}")

    def clear_all_notifications(self):
        """Close all active notifications with staggered animation."""
        notifications_to_close = self.active_notifications[:]
        for i, notification in enumerate(notifications_to_close):
            # Stagger the closing animations for visual appeal
            QTimer.singleShot(i * 50, notification._close_notification)

    # Convenience methods for common notification types
    def show_success(self, message: str, action_data: Dict[str, Any] = None):
        return self.show_notification(message, NotificationType.SUCCESS, action_data)

    def show_error(self, message: str, action_data: Dict[str, Any] = None):
        return self.show_notification(message, NotificationType.ERROR, action_data)

    def show_info(self, message: str, action_data: Dict[str, Any] = None):
        return self.show_notification(message, NotificationType.INFO, action_data)

    def show_warning(self, message: str, action_data: Dict[str, Any] = None):
        return self.show_notification(message, NotificationType.WARNING, action_data)

    def show_order_placed(self, message: str, action_data: Dict[str, Any] = None):
        return self.show_notification(message, NotificationType.ORDER_PLACED, action_data)

    def show_order_executed(self, message: str, action_data: Dict[str, Any] = None):
        return self.show_notification(message, NotificationType.ORDER_EXECUTED, action_data)

    def show_order_cancelled(self, message: str, action_data: Dict[str, Any] = None):
        return self.show_notification(message, NotificationType.ORDER_CANCELLED, action_data)

    def show_order_rejected(self, message: str, action_data: Dict[str, Any] = None):
        return self.show_notification(message, NotificationType.ORDER_REJECTED, action_data)

    def show_partial_fill(self, message: str, action_data: Dict[str, Any] = None):
        return self.show_notification(message, NotificationType.PARTIAL_FILL, action_data)


# Integration function for main window
def setup_notification_system(main_window) -> NotificationManager:
    """
    Setup premium notification system for the main window.

    Args:
        main_window: Reference to main application window

    Returns:
        NotificationManager: Configured premium notification manager
    """
    notification_manager = NotificationManager(main_window)

    # Store reference in main window
    main_window.notification_manager = notification_manager

    logger.info("Premium notification system initialized")
    return notification_manager


# Example usage and testing
if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Create premium notification manager
    manager = NotificationManager()


    # Test different notification types with premium styling
    def test_notifications():
        manager.show_order_placed("Order placed: BUY 100 RELIANCE @ ₹2,850.50")

        QTimer.singleShot(1000, lambda: manager.show_partial_fill(
            "Partial fill: 50/100 RELIANCE executed",
            {'action_type': 'show_order_history'}
        ))

        QTimer.singleShot(2000, lambda: manager.show_order_executed(
            "Order executed: BUY 100 RELIANCE @ ₹2,848.75"
        ))

        QTimer.singleShot(3000, lambda: manager.show_success(
            "Position updated: +100 RELIANCE shares"
        ))

        QTimer.singleShot(4000, lambda: manager.show_error(
            "Order rejected: Insufficient margin available"
        ))

        QTimer.singleShot(5000, lambda: manager.show_warning(
            "Market volatility detected - Review your positions"
        ))

        QTimer.singleShot(6000, lambda: manager.show_info(
            "Daily P&L: ₹15,750 (+2.3%)"
        ))


    # Start premium notification test
    QTimer.singleShot(500, test_notifications)

    sys.exit(app.exec())