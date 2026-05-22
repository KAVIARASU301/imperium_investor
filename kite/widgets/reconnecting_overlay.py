from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QProgressBar


class ReconnectingOverlay(QWidget):
    """Modal full-screen overlay used to block interaction during reconnect/restart."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("reconnectingOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        container = QWidget(self)
        container.setObjectName("reconnectBox")
        inner = QVBoxLayout(container)
        inner.setContentsMargins(28, 24, 28, 24)
        inner.setSpacing(12)

        title = QLabel("Connection lost — reconnecting…", container)
        title.setObjectName("reconnectTitle")

        spinner = QProgressBar(container)
        spinner.setRange(0, 0)
        spinner.setTextVisible(False)
        spinner.setFixedWidth(280)

        hint = QLabel("Please wait while we restore your trading session.", container)
        hint.setObjectName("reconnectHint")

        inner.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        inner.addWidget(spinner, alignment=Qt.AlignmentFlag.AlignCenter)
        inner.addWidget(hint, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(container, alignment=Qt.AlignmentFlag.AlignCenter)

        self.setStyleSheet(
            """
            QWidget#reconnectingOverlay {
                background: rgba(0, 0, 0, 160);
            }
            QWidget#reconnectBox {
                background: #111827;
                border: 1px solid #334155;
                border-radius: 10px;
            }
            QLabel#reconnectTitle {
                color: #e2e8f0;
                font-size: 16px;
                font-weight: 600;
            }
            QLabel#reconnectHint {
                color: #94a3b8;
                font-size: 12px;
            }
            """
        )

    def show_overlay(self):
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()

    def hide_overlay(self):
        self.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
