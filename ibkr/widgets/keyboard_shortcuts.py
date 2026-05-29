from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCursor, QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


# ─────────────────────────────────────────────────────────────────────────────
#  AMOLED / Institutional Dark UI Tokens
# ─────────────────────────────────────────────────────────────────────────────

class P:
    BG0 = "#050709"       # app/dialog shell
    BG1 = "#0A0D12"       # main panel
    BG2 = "#0F1318"       # card surface
    BG3 = "#141920"       # hover / elevated
    BG4 = "#1A2030"       # borders
    BORDER_HI = "#26354A"

    TEXT = "#E8F0FF"
    TEXT_SOFT = "#A8BCD4"
    TEXT_MUTED = "#5A7090"
    TEXT_FAINT = "#2A3A50"

    AMBER = "#F59E0B"
    CYAN = "#00D4FF"
    GREEN = "#00D4A8"
    RED = "#FF4D6A"
    BLUE = "#7FA6D8"


FONT_UI = "'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', Arial, sans-serif"
FONT_NUM = "'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', Arial, sans-serif"


ShortcutEntry = tuple[str, str, str]
ShortcutSection = tuple[str, tuple[ShortcutEntry, ...]]


SHORTCUT_SECTIONS: tuple[ShortcutSection, ...] = (
    (
        "WATCHLIST",
        (
            ("Ctrl + Shift + 1…9", "Add active chart symbol to watchlist slot 1–9", "CYAN"),
            ("Ctrl + Shift + 0", "Toggle active chart symbol in current watchlist", "CYAN"),
        ),
    ),
    (
        "ORDERS",
        (
            ("F1", "Open buy order ticket", "GREEN"),
            ("Shift + B", "Open buy order ticket", "GREEN"),
            ("F2", "Open sell order ticket", "RED"),
            ("Shift + S", "Open sell order ticket", "RED"),
            ("F3", "Open order entry ticket", "AMBER"),
            ("Shift + O", "Open order entry ticket", "AMBER"),
        ),
    ),
    (
        "PANELS",
        (
            ("Ctrl + H", "Open order history", "BLUE"),
            ("Shift + N", "Open pending orders", "BLUE"),
            ("Shift + L", "Open P&L history", "BLUE"),
            ("Ctrl + D", "Open performance dashboard", "BLUE"),
            ("Ctrl + P", "Toggle floating positions", "BLUE"),
            ("Shift + P", "Toggle floating positions", "BLUE"),
            ("Shift + W", "Open floating watchlist", "BLUE"),
            ("Ctrl + I", "Open stock information", "CYAN"),
            ("Shift + I", "Open stock information", "CYAN"),
            ("Ctrl + ,", "Open settings", "CYAN"),
        ),
    ),
    (
        "NAVIGATION",
        (
            ("Space", "Move to next symbol", "AMBER"),
            ("Shift + Space", "Move to previous symbol", "AMBER"),
            ("Esc", "Close active overlay or cancel current action", "RED"),
        ),
    ),
)


def _bind_shortcut(parent: QWidget, sequence: str | Qt.Key, handler: Callable[[], None]) -> QShortcut:
    shortcut = QShortcut(QKeySequence(sequence), parent)
    shortcut.activated.connect(handler)
    return shortcut


def setup_keyboard_shortcuts(main_window: QWidget) -> list[QShortcut]:
    """Register all keyboard shortcuts and return references to keep them alive."""
    shortcuts: list[QShortcut] = []

    for num in range(1, 10):
        shortcuts.append(
            _bind_shortcut(
                main_window,
                f"Ctrl+Shift+{num}",
                lambda idx=num - 1: main_window._add_symbol_to_watchlist_from_chart_index(idx),
            )
        )

    shortcuts.append(
        _bind_shortcut(main_window, "Ctrl+Shift+0", main_window._toggle_symbol_in_active_watchlist_from_chart)
    )
    shortcuts.append(_bind_shortcut(main_window, "Ctrl+H", main_window._show_order_history_dialog))
    shortcuts.append(_bind_shortcut(main_window, "Shift+N", main_window._show_pending_orders_dialog))
    shortcuts.append(_bind_shortcut(main_window, "Shift+L", main_window._show_pnl_history_dialog))
    shortcuts.append(_bind_shortcut(main_window, "Ctrl+D", main_window._show_performance_dialog))
    shortcuts.append(_bind_shortcut(main_window, "F1", main_window._on_buy_shortcut))
    shortcuts.append(_bind_shortcut(main_window, "Shift+B", main_window._on_buy_shortcut))
    shortcuts.append(_bind_shortcut(main_window, "F2", main_window._on_sell_shortcut))
    shortcuts.append(_bind_shortcut(main_window, "Shift+S", main_window._on_sell_shortcut))
    shortcuts.append(_bind_shortcut(main_window, "F3", main_window._on_order_entry_shortcut))
    shortcuts.append(_bind_shortcut(main_window, "Shift+O", main_window._on_order_entry_shortcut))
    shortcuts.append(_bind_shortcut(main_window, "Ctrl+P", main_window._toggle_floating_positions_shortcut))
    shortcuts.append(_bind_shortcut(main_window, "Shift+P", main_window._toggle_floating_positions_shortcut))
    shortcuts.append(_bind_shortcut(main_window, "Shift+W", main_window._show_floating_watchlist_dialog))
    shortcuts.append(_bind_shortcut(main_window, "Ctrl+I", main_window._show_stock_info_for_active_symbol))
    shortcuts.append(_bind_shortcut(main_window, "Shift+I", main_window._show_stock_info_for_active_symbol))
    shortcuts.append(_bind_shortcut(main_window, "Ctrl+,", main_window._open_color_settings_dialog))
    shortcuts.append(_bind_shortcut(main_window, Qt.Key.Key_Space, main_window._handle_global_spacebar))
    shortcuts.append(_bind_shortcut(main_window, "Shift+Space", main_window._handle_global_shift_spacebar))
    shortcuts.append(_bind_shortcut(main_window, "Esc", main_window._handle_escape_shortcut))
    return shortcuts


# ─────────────────────────────────────────────────────────────────────────────
#  Optional Keyboard Shortcuts Dialog
# ─────────────────────────────────────────────────────────────────────────────

def _build_shortcuts_html() -> str:
    """Return a simple paragraph-style shortcuts reference."""
    groups: list[str] = []
    for section_title, entries in SHORTCUT_SECTIONS:
        rows = []
        for keys, action, _tone in entries:
            rows.append(
                f"<p><span class='keys'>{keys}</span> — {action}.</p>"
            )
        groups.append(
            f"<h3>{section_title.title()}</h3>"
            + "".join(rows)
        )

    return f"""
    <div class='doc'>
        <h2>Keyboard Shortcuts</h2>
        <p class='intro'>These shortcuts help you move faster inside the terminal without opening extra menus. They follow the active chart, selected symbol, watchlist, scanner, or floating panel depending on current focus.</p>
        {''.join(groups)}
        <p class='note'>Use <span class='keys'>Esc</span> to close overlays or cancel the current action when focus is inside the main workspace.</p>
    </div>
    """


class KeyboardShortcutsDialog(QDialog):
    """
    Simple AMOLED shortcuts reference dialog.

    This is UI-only. Shortcut registration remains handled by
    setup_keyboard_shortcuts(), preserving all existing main-window behavior.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(
            parent,
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setObjectName("keyboardShortcutsDialog")
        self.setModal(False)
        self.setWindowTitle("Keyboard Shortcuts")
        self.setMinimumSize(500, 360)
        self.resize(560, 440)

        self._drag_active = False
        self._drag_offset = QPoint()

        self._build_ui()
        self._apply_styles()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.shell = QFrame()
        self.shell.setObjectName("shortcutShell")
        outer.addWidget(self.shell)

        root = QVBoxLayout(self.shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        body = QFrame()
        body.setObjectName("shortcutBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 14, 16, 14)
        body_layout.setSpacing(0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("shortcutScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        content.setObjectName("shortcutContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.shortcut_text = QLabel(_build_shortcuts_html())
        self.shortcut_text.setObjectName("shortcutText")
        self.shortcut_text.setTextFormat(Qt.TextFormat.RichText)
        self.shortcut_text.setWordWrap(True)
        self.shortcut_text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.shortcut_text.setOpenExternalLinks(False)

        content_layout.addWidget(self.shortcut_text)
        content_layout.addStretch()
        self.scroll_area.setWidget(content)

        body_layout.addWidget(self.scroll_area)
        root.addWidget(body, 1)
        root.addWidget(self._build_footer())

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("shortcutHeader")
        header.setFixedHeight(32)
        header.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))

        layout = QHBoxLayout(header)
        layout.setContentsMargins(10, 0, 6, 0)
        layout.setSpacing(8)

        title = QLabel("KEYBOARD SHORTCUTS")
        title.setObjectName("dialogTitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(24, 22)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self.close)

        layout.addWidget(title)
        layout.addStretch()
        layout.addWidget(close_btn)

        header.mousePressEvent = self._drag_press
        header.mouseMoveEvent = self._drag_move
        header.mouseReleaseEvent = self._drag_release
        return header

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("shortcutFooter")
        footer.setFixedHeight(30)

        layout = QHBoxLayout(footer)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(8)

        hint = QLabel("Read-only shortcut reference.")
        hint.setObjectName("footerHint")

        close_btn = QPushButton("CLOSE")
        close_btn.setObjectName("footerCloseButton")
        close_btn.setFixedHeight(22)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self.close)

        layout.addWidget(hint, 1)
        layout.addWidget(close_btn)
        return footer

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QDialog#keyboardShortcutsDialog {{
                background: {P.BG0};
                color: {P.TEXT};
                font-family: {FONT_UI};
            }}

            QFrame#shortcutShell {{
                background: {P.BG1};
                border: 1px solid {P.BG4};
                border-radius: 2px;
            }}

            QFrame#shortcutHeader {{
                background: {P.BG0};
                border-bottom: 1px solid {P.BG4};
            }}

            QLabel#dialogTitle {{
                color: {P.AMBER};
                background: transparent;
                font-family: {FONT_UI};
                font-size: 11px;
                font-weight: 750;
                letter-spacing: 1.0px;
            }}

            QPushButton#closeButton {{
                background: transparent;
                color: {P.TEXT_MUTED};
                border: 1px solid transparent;
                border-radius: 2px;
                font-family: {FONT_UI};
                font-size: 12px;
                font-weight: 700;
            }}

            QPushButton#closeButton:hover {{
                background: rgba(255,77,106,0.11);
                color: {P.RED};
                border-color: rgba(255,77,106,0.26);
            }}

            QFrame#shortcutBody,
            QWidget#shortcutContent,
            QScrollArea#shortcutScroll {{
                background: {P.BG1};
                border: none;
            }}

            QLabel#shortcutText {{
                color: {P.TEXT_SOFT};
                background: transparent;
                font-family: {FONT_UI};
                font-size: 11px;
                font-weight: 500;
                line-height: 150%;
            }}

            QFrame#shortcutFooter {{
                background: {P.BG0};
                border-top: 1px solid {P.BG4};
            }}

            QLabel#footerHint {{
                color: {P.TEXT_MUTED};
                background: transparent;
                font-family: {FONT_UI};
                font-size: 9px;
                font-weight: 500;
            }}

            QPushButton#footerCloseButton {{
                background: transparent;
                color: {P.TEXT_SOFT};
                border: 1px solid {P.BG4};
                border-radius: 2px;
                font-family: {FONT_UI};
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 0.8px;
                padding: 0 10px;
            }}

            QPushButton#footerCloseButton:hover {{
                background: {P.BG3};
                color: {P.TEXT};
                border-color: {P.BORDER_HI};
            }}

            QScrollBar:vertical {{
                background: transparent;
                width: 4px;
                border: none;
                margin: 0px;
            }}

            QScrollBar::handle:vertical {{
                background: {P.BORDER_HI};
                border-radius: 2px;
                min-height: 20px;
            }}

            QScrollBar::handle:vertical:hover {{
                background: {P.TEXT_MUTED};
            }}

            QScrollBar:horizontal {{
                background: transparent;
                height: 4px;
                border: none;
                margin: 0px;
            }}

            QScrollBar::handle:horizontal {{
                background: {P.BORDER_HI};
                border-radius: 2px;
                min-width: 20px;
            }}

            QScrollBar::add-line,
            QScrollBar::sub-line {{
                width: 0px;
                height: 0px;
                border: none;
                background: none;
            }}
        """)

        self.shortcut_text.setText(f"""
        <style>
            .doc {{ color: {P.TEXT_SOFT}; font-family: {FONT_UI}; }}
            h2 {{ color: {P.TEXT}; font-size: 17px; margin: 0 0 8px 0; font-weight: 650; }}
            h3 {{ color: {P.AMBER}; font-size: 11px; margin: 16px 0 5px 0; font-weight: 750; letter-spacing: .7px; }}
            p {{ margin: 4px 0; line-height: 1.45; }}
            .intro {{ color: {P.TEXT_MUTED}; margin-bottom: 12px; }}
            .note {{ color: {P.TEXT_MUTED}; margin-top: 14px; }}
            .keys {{ color: {P.TEXT}; font-weight: 650; }}
        </style>
        {_build_shortcuts_html()}
        """)

    def _drag_press(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _drag_move(self, event: QMouseEvent) -> None:
        if self._drag_active and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def _drag_release(self, event: QMouseEvent) -> None:
        self._drag_active = False
        event.accept()


def show_keyboard_shortcuts_dialog(parent: QWidget | None = None) -> KeyboardShortcutsDialog:
    """Show a simple AMOLED shortcuts reference dialog and return it."""
    dialog = KeyboardShortcutsDialog(parent)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
