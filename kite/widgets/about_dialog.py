from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class AboutDialog(QDialog):
    """Dedicated about dialog for qullamaggie."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(
            parent,
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setObjectName("aboutDialog")
        self.setModal(False)
        self.setWindowTitle("About qullamaggie")
        self.setMinimumSize(560, 420)
        self.resize(640, 470)

        self._drag_active = False
        self._drag_offset = None

        self._build_ui()
        self._apply_styles()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        shell = QFrame()
        shell.setObjectName("aboutShell")
        root.addWidget(shell)

        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("aboutHeader")
        header.setFixedHeight(36)
        header.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 0, 8, 0)

        title = QLabel("ABOUT QULLAMAGGIE")
        title.setObjectName("aboutTitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("aboutClose")
        close_btn.setFixedSize(24, 22)
        close_btn.clicked.connect(self.close)

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(close_btn)

        header.mousePressEvent = self._drag_press
        header.mouseMoveEvent = self._drag_move
        header.mouseReleaseEvent = self._drag_release

        body = QFrame()
        body.setObjectName("aboutBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 14, 16, 14)
        body_layout.setSpacing(10)

        body_html = QLabel(
            """
            <h2>qullamaggie</h2>
            <p>A desktop swing-trading command center for scanning Indian equity markets,
            reviewing charts, managing watchlists, and monitoring positions from one focused workspace.</p>

            <h3>What this workspace includes</h3>
            <ul>
                <li><b>Market scanner:</b> Chartink and Finviz workflows for finding setups quickly.</li>
                <li><b>Interactive charts:</b> candlesticks, indicators, drawings, and persisted chart notes.</li>
                <li><b>Watchlists:</b> tabbed symbol lists with quick chart access and stock details.</li>
                <li><b>Trading tools:</b> order entry, pending orders, order history, and P&amp;L views.</li>
                <li><b>Risk visibility:</b> live positions, floating panels, alerts, and app health indicators.</li>
            </ul>

            <h3>Broker and data context</h3>
            <p>This build is wired for Kite/Zerodha market access with paper-trading support
            for safer workflow validation before live execution.</p>

            <h3>Important note</h3>
            <p>qullamaggie is a decision-support tool, not financial advice.
            Always verify market data, order details, risk, and broker confirmations
            before placing or modifying trades.</p>
            """
        )
        body_html.setObjectName("aboutContent")
        body_html.setTextFormat(Qt.TextFormat.RichText)
        body_html.setWordWrap(True)
        body_html.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        footer = QFrame()
        footer.setObjectName("aboutFooter")
        footer.setFixedHeight(32)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(12, 0, 12, 0)

        footer_hint = QLabel("Read-only product summary and safety notice.")
        footer_hint.setObjectName("aboutHint")

        close_footer_btn = QPushButton("CLOSE")
        close_footer_btn.setObjectName("aboutFooterClose")
        close_footer_btn.setFixedHeight(22)
        close_footer_btn.clicked.connect(self.close)

        footer_layout.addWidget(footer_hint)
        footer_layout.addStretch()
        footer_layout.addWidget(close_footer_btn)

        body_layout.addWidget(body_html)
        shell_layout.addWidget(header)
        shell_layout.addWidget(body, 1)
        shell_layout.addWidget(footer)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QDialog#aboutDialog { background: #050709; color: #E8F0FF; }
            QFrame#aboutShell { background: #0A0D12; border: 1px solid #1A2030; border-radius: 2px; }
            QFrame#aboutHeader { background: #050709; border-bottom: 1px solid #1A2030; }
            QLabel#aboutTitle { color: #F59E0B; font-size: 11px; font-weight: 800; letter-spacing: 1px; }
            QPushButton#aboutClose { background: transparent; color: #5A7090; border: 1px solid transparent; border-radius: 2px; }
            QPushButton#aboutClose:hover { color: #FF4D6A; background: rgba(255,77,106,0.11); border-color: rgba(255,77,106,0.26); }
            QFrame#aboutBody { background: #0F1318; }
            QLabel#aboutContent { color: #A8BCD4; font-size: 11px; background: transparent; }
            QLabel#aboutContent h2 { color: #E8F0FF; }
            QLabel#aboutContent h3 { color: #00D4FF; }
            QFrame#aboutFooter { background: #050709; border-top: 1px solid #1A2030; }
            QLabel#aboutHint { color: #5A7090; font-size: 10px; }
            QPushButton#aboutFooterClose { background: #141920; color: #A8BCD4; border: 1px solid #1A2030; border-radius: 2px; padding: 0 10px; font-weight: 700; }
            QPushButton#aboutFooterClose:hover { background: #1A2030; color: #E8F0FF; }
            """
        )

    def _drag_press(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _drag_move(self, event: QMouseEvent) -> None:
        if self._drag_active and event.buttons() & Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def _drag_release(self, event: QMouseEvent) -> None:
        self._drag_active = False
        event.accept()


def show_about_dialog(parent: QWidget | None = None) -> AboutDialog:
    dialog = AboutDialog(parent)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog