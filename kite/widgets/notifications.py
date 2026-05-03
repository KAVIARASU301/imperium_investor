# kite/widgets/notifications.py
import logging
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QAbstractAnimation
from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
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
    TOAST_MIN_WIDTH = 220
    TOAST_MAX_WIDTH = 420
    TOAST_TEXT_MAX_WIDTH = 340
    TOAST_MIN_HEIGHT = 0
    DETAILS_CHAR_THRESHOLD = 140
    COLLAPSED_PREVIEW_CHARS = 120
    STACK_SPACING = 10
    MARGIN = 20
    BOTTOM_SAFE_GAP = 18

    def __init__(
        self,
        title: str,
        message: str,
        kind: str = "info",
        duration: int = 3000,
        parent=None,
        border_color: str | None = None,
    ):
        super().__init__(parent)

        # Determine colors based on kind
        self.theme = {
            "success": {"bg": "#1e2b24", "border": "#4ec994", "text": "#e0e0e0"},
            "error": {"bg": "#2b1e1e", "border": "#e05555", "text": "#e0e0e0"},
            "warn": {"bg": "#2b251e", "border": "#d4a84b", "text": "#e0e0e0"},
            "info": {"bg": "#1e1e1e", "border": "#6a9cff", "text": "#e0e0e0"},
        }.get(kind, {"bg": "#1e1e1e", "border": "#888888", "text": "#e0e0e0"})
        if border_color:
            self.theme["border"] = border_color

        self.setWindowFlags(
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._setup_ui(title, message)
        self.setMinimumHeight(self.TOAST_MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        self._update_dynamic_size()

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
        self.title_label = None
        if title:
            self.title_label = QLabel(title)
            self.title_label.setStyleSheet(f"color: {self.theme['border']}; font-weight: bold; font-size: 12px;")
            text_layout.addWidget(self.title_label)

        self._full_message = message or ""
        self._details_expanded = False
        self._has_details_toggle = self._should_enable_details_toggle(self._full_message)

        # Message
        initial_message = self._build_collapsed_preview(self._full_message) if self._has_details_toggle else self._full_message
        self.message_label = QLabel(initial_message)
        self.message_label.setStyleSheet(f"color: {self.theme['text']}; font-size: 11px;")
        self.message_label.setWordWrap(True)
        self.message_label.setMaximumWidth(self.TOAST_TEXT_MAX_WIDTH)
        self.message_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.message_label.adjustSize()
        text_layout.addWidget(self.message_label)

        self.details_button = None
        if self._has_details_toggle:
            self.details_button = QPushButton("▾ Details")
            self.details_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.details_button.setStyleSheet(
                "QPushButton { color: #9cbcff; background: transparent; border: none; font-size: 10px; padding: 0; text-align: left; }"
                "QPushButton:hover { color: #c9dbff; text-decoration: underline; }"
            )
            self.details_button.setFlat(True)
            self.details_button.clicked.connect(self._toggle_details)
            text_layout.addWidget(self.details_button, alignment=Qt.AlignmentFlag.AlignLeft)

        layout.addLayout(text_layout)

        self.close_button = QPushButton("×")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setFixedSize(16, 16)
        self.close_button.setStyleSheet(
            "QPushButton { color: #a8a8a8; background: transparent; border: none; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { color: #ffffff; }"
        )
        self.close_button.clicked.connect(self.fade_out)
        layout.addWidget(self.close_button, alignment=Qt.AlignmentFlag.AlignTop)

        self.adjustSize()

    def _should_enable_details_toggle(self, message: str) -> bool:
        if not message:
            return False
        return len(message) > self.DETAILS_CHAR_THRESHOLD or "\n" in message

    def _build_collapsed_preview(self, message: str) -> str:
        one_line = " ".join(message.split())
        if len(one_line) <= self.COLLAPSED_PREVIEW_CHARS:
            return one_line
        return f"{one_line[: self.COLLAPSED_PREVIEW_CHARS - 1].rstrip()}…"

    def _toggle_details(self):
        self._details_expanded = not self._details_expanded
        if self._details_expanded:
            self.message_label.setText(self._full_message)
            if self.details_button:
                self.details_button.setText("▴ Hide details")
        else:
            self.message_label.setText(self._build_collapsed_preview(self._full_message))
            if self.details_button:
                self.details_button.setText("▾ Details")

        self._update_dynamic_size()
        ToastNotification._restack_visible_toasts()

    @staticmethod
    def _measure_text_width(label: QLabel) -> int:
        if not label or not label.text():
            return 0
        metrics = label.fontMetrics()
        return max(metrics.horizontalAdvance(line) for line in label.text().splitlines() or [label.text()])

    def _update_dynamic_size(self):
        title_width = self._measure_text_width(self.title_label)
        message_width = self._measure_text_width(self.message_label)

        text_width = max(title_width, message_width)
        chrome_width = 15 + 4 + 10 + 10 + 16 + 8 + 15
        toast_width = max(self.TOAST_MIN_WIDTH, min(self.TOAST_MAX_WIDTH, chrome_width + text_width))

        self.setFixedWidth(toast_width)
        self.message_label.setMaximumWidth(max(1, min(self.TOAST_TEXT_MAX_WIDTH, toast_width - chrome_width)))
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

    @classmethod
    def _restack_visible_toasts(cls):
        """Reflow active toasts upward so no gaps remain after dismissals."""
        screen = QApplication.primaryScreen().availableGeometry()
        cls._active_toasts = [t for t in cls._active_toasts if t.isVisible()]

        y_offset = cls.MARGIN
        for toast in cls._active_toasts:
            target_y = screen.height() - toast.height() - cls.BOTTOM_SAFE_GAP - y_offset
            if toast.pos().y() != target_y:
                if toast.animation.state() == QAbstractAnimation.State.Running:
                    toast.animation.stop()
                toast.animation.setDuration(150)
                toast.animation.setStartValue(toast.pos())
                toast.animation.setEndValue(QPoint(toast.pos().x(), target_y))
                toast.animation.start()
            y_offset += toast.height() + cls.STACK_SPACING

    def show_toast(self):
        """Calculates position, handles stacking, and animates in."""
        screen = QApplication.primaryScreen().availableGeometry()

        # Ensure layout-derived size/height is finalized before stacking math.
        self.adjustSize()

        # Clean up dead toasts from the stack tracking
        ToastNotification._active_toasts = [t for t in ToastNotification._active_toasts if t.isVisible()]

        # Calculate Y position based on total stack height of visible toasts
        stack_height = sum(t.height() + self.STACK_SPACING for t in ToastNotification._active_toasts)
        y_offset = self.MARGIN + stack_height

        start_x = screen.width()
        end_x = screen.width() - self.width() - self.MARGIN
        target_y = screen.height() - self.height() - self.BOTTOM_SAFE_GAP - y_offset

        self.setGeometry(start_x, target_y, self.width(), self.height())
        self.show()

        self.animation.setStartValue(QPoint(start_x, target_y))
        self.animation.setEndValue(QPoint(end_x, target_y))
        self.animation.start()

        ToastNotification._active_toasts.append(self)

    def fade_out(self):
        """Animates out and cleans up."""
        if hasattr(self, "timer") and self.timer.isActive():
            self.timer.stop()

        if self.animation.state() == QPropertyAnimation.State.Running:
            return

        self.animation.setDuration(200)
        self.animation.setStartValue(self.pos())
        self.animation.setEndValue(QPoint(self.pos().x() + self.width() + self.MARGIN, self.pos().y()))
        self.animation.finished.connect(self._finalize_close)
        self.animation.start()

        if self in ToastNotification._active_toasts:
            ToastNotification._active_toasts.remove(self)

    def _finalize_close(self):
        """Close this toast and then reflow the remaining stack."""
        self.close()
        ToastNotification._restack_visible_toasts()
