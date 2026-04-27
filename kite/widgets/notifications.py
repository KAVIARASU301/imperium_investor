# kite/widgets/notifications.py
import logging
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QRect, QPoint
from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QApplication,
    QGraphicsDropShadowEffect,
    QSizePolicy,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath

logger = logging.getLogger(__name__)


class ToastNotification(QWidget):
    """A professional, non-blocking toast notification that fades in and stacks."""

    # Track active toasts to stack them
    _active_toasts = []

    # Padding and sizing
    TOAST_WIDTH = 300
    TOAST_MIN_HEIGHT = 50
    STACK_SPACING = 10
    MARGIN = 20

    def __init__(self, title: str, message: str, kind: str = "info", duration: int = 3000, parent=None):
        super().__init__(parent)

        # Determine colors based on kind
        self.theme = {
            "success": {"bg": "#1e2b24", "border": "#4ec994", "text": "#e0e0e0"},
            "error": {"bg": "#2b1e1e", "border": "#e05555", "text": "#e0e0e0"},
            "warn": {"bg": "#2b251e", "border": "#d4a84b", "text": "#e0e0e0"},
            "info": {"bg": "#1e1e1e", "border": "#6a9cff", "text": "#e0e0e0"},
        }.get(kind, {"bg": "#1e1e1e", "border": "#888888", "text": "#e0e0e0"})

        self.setWindowFlags(
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._setup_ui(title, message)
        self.setFixedWidth(self.TOAST_WIDTH)
        self.setMinimumHeight(self.TOAST_MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.MinimumExpanding)
        self.adjustSize()

        # Shadow effect for professional depth
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

        # Animation setup
        self.animation = QPropertyAnimation(self, b"pos")
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.setDuration(150)

        # Auto-close timer
        if duration > 0:
            self.timer = QTimer(self)
            self.timer.setSingleShot(True)
            self.timer.timeout.connect(self.fade_out)
            self.timer.start(duration)

    def _setup_ui(self, title: str, message: str):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)

        # Left colored indicator bar
        self.indicator = QWidget()
        self.indicator.setFixedWidth(4)
        self.indicator.setStyleSheet(f"background-color: {self.theme['border']}; border-radius: 2px;")
        layout.addWidget(self.indicator)

        # Text layout
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        text_layout.setContentsMargins(10, 0, 0, 0)

        # Title
        if title:
            title_label = QLabel(title)
            title_label.setStyleSheet(f"color: {self.theme['border']}; font-weight: bold; font-size: 12px;")
            text_layout.addWidget(title_label)

        # Message
        self.message_label = QLabel(message)
        self.message_label.setStyleSheet(f"color: {self.theme['text']}; font-size: 11px;")
        self.message_label.setWordWrap(True)
        self.message_label.adjustSize()
        text_layout.addWidget(self.message_label)

        text_layout.addStretch()
        layout.addLayout(text_layout)

        self.adjustSize()

    def paintEvent(self, event):
        """Draw rounded dark background."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 6, 6)
        painter.fillPath(path, QColor(self.theme["bg"]))
        painter.setPen(QColor(40, 40, 40))  # Subtle border
        painter.drawPath(path)

    def show_toast(self):
        """Calculates position, handles stacking, and animates in."""
        screen = QApplication.primaryScreen().availableGeometry()

        # Clean up dead toasts from the stack tracking
        ToastNotification._active_toasts = [t for t in ToastNotification._active_toasts if t.isVisible()]

        # Calculate Y position based on total stack height of visible toasts
        stack_height = sum(t.height() + self.STACK_SPACING for t in ToastNotification._active_toasts)
        y_offset = self.MARGIN + stack_height

        start_x = screen.width()
        end_x = screen.width() - self.TOAST_WIDTH - self.MARGIN
        target_y = screen.height() - self.height() - y_offset

        self.setGeometry(start_x, target_y, self.TOAST_WIDTH, self.height())
        self.show()

        self.animation.setStartValue(QPoint(start_x, target_y))
        self.animation.setEndValue(QPoint(end_x, target_y))
        self.animation.start()

        ToastNotification._active_toasts.append(self)

    def fade_out(self):
        """Animates out and cleans up."""
        self.animation.setDuration(200)
        self.animation.setStartValue(self.pos())
        self.animation.setEndValue(QPoint(self.pos().x() + self.TOAST_WIDTH + self.MARGIN, self.pos().y()))
        self.animation.finished.connect(self.close)
        self.animation.start()

        if self in ToastNotification._active_toasts:
            ToastNotification._active_toasts.remove(self)
