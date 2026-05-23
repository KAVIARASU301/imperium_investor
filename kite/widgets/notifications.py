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
    """Compact non-blocking toast notification for the terminal UI."""

    # Track active toasts to stack them
    _active_toasts = []

    # Institutional Dark Trading Terminal tokens
    BG_WINDOW = "#0A0D12"
    BG_PANEL = "#0F1318"
    BG_SECTION = "#141920"
    BG_BORDER = "#1A2030"
    TEXT_PRIMARY = "#E8F0FF"
    TEXT_SECONDARY = "#A8BCD4"
    TEXT_MUTED = "#5A7090"
    ACCENT_SUCCESS = "#00D4A8"
    ACCENT_ERROR = "#FF4D6A"
    ACCENT_WARNING = "#F59E0B"
    ACCENT_INFO = "#00D4FF"
    UI_FONT = "'Inter', 'Aptos', 'Segoe UI', 'Roboto', 'Noto Sans', sans-serif"

    KIND_ALIASES = {
        "warning": "warn",
        "danger": "error",
        "critical": "error",
        "failed": "error",
        "failure": "error",
        "ok": "success",
        "done": "success",
        "notice": "info",
    }

    KIND_TITLES = {
        "success": "CONFIRMED",
        "error": "ACTION REQUIRED",
        "warn": "ATTENTION",
        "info": "NOTICE",
    }

    KIND_THEME = {
        "success": {"accent": ACCENT_SUCCESS},
        "error": {"accent": ACCENT_ERROR},
        "warn": {"accent": ACCENT_WARNING},
        "info": {"accent": ACCENT_INFO},
    }

    # Padding and sizing
    TOAST_MIN_WIDTH = 260
    TOAST_MAX_WIDTH = 430
    TOAST_TEXT_MAX_WIDTH = 350
    TOAST_MIN_HEIGHT = 42
    DETAILS_CHAR_THRESHOLD = 140
    COLLAPSED_PREVIEW_CHARS = 118
    STACK_SPACING = 8
    MARGIN = 18
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

        self.kind = self._normalize_kind(kind)
        self.theme = self._build_theme(self.kind, border_color)
        self._closing = False

        self.setWindowFlags(
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        display_title = self._format_title(title, self.kind)
        display_message = self._format_message(message)

        self._setup_ui(display_title, display_message)
        self.setMinimumHeight(self.TOAST_MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        self._update_dynamic_size()

        # Keep the toast visually lifted without the heavy card-shadow look.
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(8)
        shadow.setColor(QColor(0, 0, 0, 110))
        shadow.setOffset(0, 2)
        self.setGraphicsEffect(shadow)

        # Short, functional motion only.
        self.animation = QPropertyAnimation(self, b"pos")
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.setDuration(120)
        self.animation.finished.connect(self._on_animation_finished)

        # Auto-close timer
        if duration > 0:
            self.timer = QTimer(self)
            self.timer.setSingleShot(True)
            self.timer.timeout.connect(self.fade_out)
            self.timer.start(duration)

    @classmethod
    def _normalize_kind(cls, kind: str) -> str:
        normalized = (kind or "info").strip().lower()
        normalized = cls.KIND_ALIASES.get(normalized, normalized)
        return normalized if normalized in cls.KIND_THEME else "info"

    @classmethod
    def _build_theme(cls, kind: str, border_color: str | None = None) -> dict[str, str]:
        accent = border_color or cls.KIND_THEME.get(kind, cls.KIND_THEME["info"])["accent"]
        return {
            "bg": cls.BG_WINDOW,
            "panel": cls.BG_PANEL,
            "section": cls.BG_SECTION,
            "border": cls.BG_BORDER,
            "accent": accent,
            "text": cls.TEXT_PRIMARY,
            "secondary": cls.TEXT_SECONDARY,
            "muted": cls.TEXT_MUTED,
        }

    @classmethod
    def _format_title(cls, title: str, kind: str) -> str:
        cleaned = " ".join((title or "").replace("_", " ").split()).strip(" .:-")
        if not cleaned:
            cleaned = cls.KIND_TITLES.get(kind, cls.KIND_TITLES["info"])
        return cleaned.upper()

    @staticmethod
    def _format_message(message: str) -> str:
        if not message:
            return "Update received."
        lines = [line.strip() for line in str(message).splitlines()]
        return "\n".join(line for line in lines if line).strip() or "Update received."

    def _setup_ui(self, title: str, message: str):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 10, 9)
        layout.setSpacing(10)

        # Left state indicator. Accent is state-driven, not decorative.
        self.indicator = QWidget()
        self.indicator.setFixedWidth(3)
        self.indicator.setStyleSheet(
            f"background-color: {self.theme['accent']}; border-radius: 1px;"
        )
        layout.addWidget(self.indicator)

        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(4)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.title_label.setStyleSheet(
            f"""
            QLabel {{
                color: {self.theme['accent']};
                background: transparent;
                font-family: {self.UI_FONT};
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 1.1px;
            }}
            """
        )
        header_layout.addWidget(self.title_label, stretch=1)

        self.close_button = QPushButton("×")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setToolTip("Dismiss notification")
        self.close_button.setAccessibleName("Dismiss notification")
        self.close_button.setFixedSize(20, 20)
        self.close_button.setStyleSheet(
            f"""
            QPushButton {{
                color: {self.theme['muted']};
                background: transparent;
                border: 1px solid transparent;
                border-radius: 2px;
                font-family: {self.UI_FONT};
                font-size: 14px;
                font-weight: 600;
                padding: 0px;
            }}
            QPushButton:hover {{
                color: {self.ACCENT_ERROR};
                background: rgba(255, 77, 106, 0.10);
                border: 1px solid rgba(255, 77, 106, 0.35);
            }}
            QPushButton:pressed {{
                background: rgba(255, 77, 106, 0.18);
            }}
            """
        )
        self.close_button.clicked.connect(self.fade_out)
        header_layout.addWidget(self.close_button, alignment=Qt.AlignmentFlag.AlignTop)
        content_layout.addLayout(header_layout)

        self._full_message = message or ""
        self._details_expanded = False
        self._has_details_toggle = self._should_enable_details_toggle(self._full_message)

        initial_message = (
            self._build_collapsed_preview(self._full_message)
            if self._has_details_toggle
            else self._full_message
        )
        self.message_label = QLabel(initial_message)
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.message_label.setStyleSheet(
            f"""
            QLabel {{
                color: {self.theme['text']};
                background: transparent;
                font-family: {self.UI_FONT};
                font-size: 11px;
                font-weight: 500;
                line-height: 15px;
            }}
            """
        )
        self.message_label.setWordWrap(True)
        self.message_label.setMaximumWidth(self.TOAST_TEXT_MAX_WIDTH)
        self.message_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.message_label.adjustSize()
        content_layout.addWidget(self.message_label)

        self.details_button = None
        if self._has_details_toggle:
            self.details_button = QPushButton("SHOW DETAILS")
            self.details_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.details_button.setToolTip("Expand notification details")
            self.details_button.setAccessibleName("Show notification details")
            self.details_button.setStyleSheet(
                f"""
                QPushButton {{
                    color: {self.ACCENT_INFO};
                    background: transparent;
                    border: none;
                    border-radius: 2px;
                    font-family: {self.UI_FONT};
                    font-size: 9px;
                    font-weight: 800;
                    letter-spacing: 0.9px;
                    padding: 1px 0px;
                    text-align: left;
                }}
                QPushButton:hover {{
                    color: {self.TEXT_PRIMARY};
                }}
                QPushButton:pressed {{
                    color: {self.TEXT_SECONDARY};
                }}
                """
            )
            self.details_button.setFlat(True)
            self.details_button.clicked.connect(self._toggle_details)
            content_layout.addWidget(self.details_button, alignment=Qt.AlignmentFlag.AlignLeft)

        layout.addLayout(content_layout, stretch=1)
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
                self.details_button.setText("HIDE DETAILS")
                self.details_button.setToolTip("Collapse notification details")
                self.details_button.setAccessibleName("Hide notification details")
        else:
            self.message_label.setText(self._build_collapsed_preview(self._full_message))
            if self.details_button:
                self.details_button.setText("SHOW DETAILS")
                self.details_button.setToolTip("Expand notification details")
                self.details_button.setAccessibleName("Show notification details")

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
        details_width = self._measure_text_width(self.details_button)

        text_width = max(title_width + 28, message_width, details_width)
        chrome_width = 12 + 3 + 10 + 10
        toast_width = max(self.TOAST_MIN_WIDTH, min(self.TOAST_MAX_WIDTH, chrome_width + text_width))

        available_text_width = max(1, min(self.TOAST_TEXT_MAX_WIDTH, toast_width - chrome_width - 28))
        self.setFixedWidth(toast_width)
        self.message_label.setMaximumWidth(available_text_width)
        self.title_label.setMaximumWidth(available_text_width)
        self.adjustSize()

    def paintEvent(self, event):
        """Draw a sharp matte terminal surface with a thin border."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(0.5, 0.5, self.width() - 1, self.height() - 1, 2, 2)
        painter.fillPath(path, QColor(self.theme["bg"]))
        painter.setPen(QColor(self.theme["border"]))
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
                toast.animation.setDuration(120)
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

        self.animation.setDuration(120)
        self.animation.setStartValue(QPoint(start_x, target_y))
        self.animation.setEndValue(QPoint(end_x, target_y))
        self.animation.start()

        ToastNotification._active_toasts.append(self)

    def fade_out(self):
        """Animates out and cleans up."""
        if self._closing:
            return

        if hasattr(self, "timer") and self.timer.isActive():
            self.timer.stop()

        if self.animation.state() == QAbstractAnimation.State.Running:
            self.animation.stop()

        self._closing = True
        self.animation.setDuration(140)
        self.animation.setStartValue(self.pos())
        self.animation.setEndValue(QPoint(self.pos().x() + self.width() + self.MARGIN, self.pos().y()))
        self.animation.start()

        if self in ToastNotification._active_toasts:
            ToastNotification._active_toasts.remove(self)

    def _on_animation_finished(self):
        if self._closing:
            self._finalize_close()

    def _finalize_close(self):
        """Close this toast and then reflow the remaining stack."""
        self.close()
        ToastNotification._restack_visible_toasts()
        self.deleteLater()