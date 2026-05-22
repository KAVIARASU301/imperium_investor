from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


_BG0 = "#050709"
_BG1 = "#0a0d12"
_BG2 = "#0f1318"
_BG4 = "#1a2030"
_BGTB = "#070a0f"
_AMBER = "#f59e0b"
_CYAN = "#00d4ff"
_T0 = "#e8f0ff"
_T1 = "#a8bcd4"
_T2 = "#5a7090"
_UI_FONT = "'Inter', 'Aptos', 'Segoe UI', 'Roboto', 'Noto Sans', sans-serif"


class ReconnectingOverlay(QWidget):
    """Modal full-screen overlay used to block interaction during reconnect/restart."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("reconnectingOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.WaitCursor)
        self.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        container = QFrame(self)
        container.setObjectName("reconnectBox")
        container.setMinimumWidth(380)
        container.setMaximumWidth(440)
        container.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        shell = QVBoxLayout(container)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        header = QWidget(container)
        header.setObjectName("reconnectHeader")
        header.setFixedHeight(28)

        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 12, 0)
        header_layout.setSpacing(8)

        state_dot = QFrame(header)
        state_dot.setObjectName("reconnectStateDot")
        state_dot.setFixedSize(7, 7)

        status = QLabel("RECONNECTING", header)
        status.setObjectName("reconnectStatusLabel")
        status.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        header_meta = QLabel("SESSION LINK", header)
        header_meta.setObjectName("reconnectHeaderMeta")
        header_meta.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)

        header_layout.addWidget(state_dot)
        header_layout.addWidget(status)
        header_layout.addStretch(1)
        header_layout.addWidget(header_meta)

        body = QWidget(container)
        body.setObjectName("reconnectBody")

        inner = QVBoxLayout(body)
        inner.setContentsMargins(18, 16, 18, 14)
        inner.setSpacing(8)

        self.title_label = QLabel("Connection interrupted", body)
        self.title_label.setObjectName("reconnectTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.hint_label = QLabel("Restoring trading session", body)
        self.hint_label.setObjectName("reconnectHint")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.progress = QProgressBar(body)
        self.progress.setObjectName("reconnectProgress")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        inner.addWidget(self.title_label)
        inner.addWidget(self.hint_label)
        inner.addSpacing(2)
        inner.addWidget(self.progress)

        footer = QWidget(container)
        footer.setObjectName("reconnectFooter")
        footer.setFixedHeight(26)

        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(12, 0, 12, 0)
        footer_layout.setSpacing(8)

        footer_label = QLabel("AUTO RETRY ACTIVE", footer)
        footer_label.setObjectName("reconnectFooterText")
        footer_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        footer_accent = QLabel("LOCKED", footer)
        footer_accent.setObjectName("reconnectFooterAccent")
        footer_accent.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)

        footer_layout.addWidget(footer_label)
        footer_layout.addStretch(1)
        footer_layout.addWidget(footer_accent)

        shell.addWidget(header)
        shell.addWidget(body)
        shell.addWidget(footer)

        layout.addWidget(container, alignment=Qt.AlignmentFlag.AlignCenter)

        self.setStyleSheet(
            f"""
            QWidget#reconnectingOverlay {{
                background: rgba(5, 7, 9, 202);
            }}

            QFrame#reconnectBox {{
                background: {_BG1};
                border: 1px solid {_BG4};
                border-radius: 2px;
            }}

            QWidget#reconnectHeader {{
                background: {_BGTB};
                border-bottom: 1px solid {_BG4};
            }}

            QFrame#reconnectStateDot {{
                background: {_AMBER};
                border: 1px solid {_AMBER};
                border-radius: 1px;
            }}

            QLabel#reconnectStatusLabel {{
                background: transparent;
                color: {_AMBER};
                font-family: {_UI_FONT};
                font-size: 10px;
                font-weight: 800;
            }}

            QLabel#reconnectHeaderMeta {{
                background: transparent;
                color: {_T2};
                font-family: {_UI_FONT};
                font-size: 9px;
                font-weight: 700;
            }}

            QWidget#reconnectBody {{
                background: {_BG2};
            }}

            QLabel#reconnectTitle {{
                background: transparent;
                color: {_T0};
                font-family: {_UI_FONT};
                font-size: 13px;
                font-weight: 700;
            }}

            QLabel#reconnectHint {{
                background: transparent;
                color: {_T1};
                font-family: {_UI_FONT};
                font-size: 11px;
                font-weight: 500;
            }}

            QProgressBar#reconnectProgress {{
                background: {_BG0};
                border: 1px solid {_BG4};
                border-radius: 2px;
                min-height: 5px;
                max-height: 5px;
            }}

            QProgressBar#reconnectProgress::chunk {{
                background: {_AMBER};
                border-radius: 1px;
                margin: 1px;
            }}

            QWidget#reconnectFooter {{
                background: {_BGTB};
                border-top: 1px solid {_BG4};
            }}

            QLabel#reconnectFooterText {{
                background: transparent;
                color: {_T2};
                font-family: {_UI_FONT};
                font-size: 9px;
                font-weight: 700;
            }}

            QLabel#reconnectFooterAccent {{
                background: transparent;
                color: {_CYAN};
                font-family: {_UI_FONT};
                font-size: 9px;
                font-weight: 800;
            }}
            """
        )

    def set_message(self, title=None, hint=None):
        """Optionally update overlay copy without changing external show/hide hooks."""
        if title is not None:
            self.title_label.setText(title)
        if hint is not None:
            self.hint_label.setText(hint)

    def show_overlay(self):
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.show()

    def hide_overlay(self):
        self.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
