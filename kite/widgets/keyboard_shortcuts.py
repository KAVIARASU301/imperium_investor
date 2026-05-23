from __future__ import annotations

from typing import Callable, Iterable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCursor, QFont, QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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
            ("Ctrl + D", "Open performance dashboard", "BLUE"),
            ("Ctrl + P", "Toggle floating positions", "BLUE"),
            ("Shift + P", "Toggle floating positions", "BLUE"),
            ("Ctrl + I", "Open stock information", "CYAN"),
            ("Shift + I", "Open stock information", "CYAN"),
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
    shortcuts.append(_bind_shortcut(main_window, "Ctrl+D", main_window._show_performance_dialog))
    shortcuts.append(_bind_shortcut(main_window, "F1", main_window._on_buy_shortcut))
    shortcuts.append(_bind_shortcut(main_window, "Shift+B", main_window._on_buy_shortcut))
    shortcuts.append(_bind_shortcut(main_window, "F2", main_window._on_sell_shortcut))
    shortcuts.append(_bind_shortcut(main_window, "Shift+S", main_window._on_sell_shortcut))
    shortcuts.append(_bind_shortcut(main_window, "F3", main_window._on_order_entry_shortcut))
    shortcuts.append(_bind_shortcut(main_window, "Shift+O", main_window._on_order_entry_shortcut))
    shortcuts.append(_bind_shortcut(main_window, "Ctrl+P", main_window._toggle_floating_positions_shortcut))
    shortcuts.append(_bind_shortcut(main_window, "Shift+P", main_window._toggle_floating_positions_shortcut))
    shortcuts.append(_bind_shortcut(main_window, "Ctrl+I", main_window._show_stock_info_for_active_symbol))
    shortcuts.append(_bind_shortcut(main_window, "Shift+I", main_window._show_stock_info_for_active_symbol))
    shortcuts.append(_bind_shortcut(main_window, Qt.Key.Key_Space, main_window._handle_global_spacebar))
    shortcuts.append(_bind_shortcut(main_window, "Shift+Space", main_window._handle_global_shift_spacebar))
    shortcuts.append(_bind_shortcut(main_window, "Esc", main_window._handle_escape_shortcut))
    return shortcuts


# ─────────────────────────────────────────────────────────────────────────────
#  Optional Keyboard Shortcuts Dialog
# ─────────────────────────────────────────────────────────────────────────────

class _ShortcutRow(QFrame):
    """Single compact shortcut row with a key badge and plain-English action text."""

    def __init__(self, keys: str, action: str, tone: str = "CYAN", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("shortcutRow")
        self.setFixedHeight(30)

        row = QHBoxLayout(self)
        row.setContentsMargins(7, 0, 8, 0)
        row.setSpacing(8)

        self.key_badge = QLabel(keys)
        self.key_badge.setObjectName("keyBadge")
        self.key_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.key_badge.setMinimumWidth(132)
        self.key_badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.key_badge.setProperty("tone", tone)

        self.action_label = QLabel(action)
        self.action_label.setObjectName("shortcutAction")
        self.action_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        row.addWidget(self.key_badge)
        row.addWidget(self.action_label, 1)


class _ShortcutSection(QFrame):
    """Section card for one shortcut group."""

    def __init__(self, title: str, entries: Iterable[ShortcutEntry], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("shortcutSection")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        title_bar = QFrame()
        title_bar.setObjectName("sectionTitleBar")
        title_bar.setFixedHeight(27)
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(8, 0, 8, 0)
        title_layout.setSpacing(8)

        accent = QFrame()
        accent.setObjectName("sectionAccent")
        accent.setFixedSize(3, 13)

        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")

        title_layout.addWidget(accent)
        title_layout.addWidget(title_label)
        title_layout.addStretch()

        layout.addWidget(title_bar)

        for keys, action, tone in entries:
            layout.addWidget(_ShortcutRow(keys, action, tone, self))


class KeyboardShortcutsDialog(QDialog):
    """
    Compact AMOLED shortcuts reference panel.

    This is intentionally UI-only. Shortcut registration remains handled by
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
        self.setMinimumSize(520, 420)
        self.resize(560, 520)

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
        body_layout.setContentsMargins(8, 8, 8, 8)
        body_layout.setSpacing(8)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("shortcutScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        content.setObjectName("shortcutContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)

        for title, entries in SHORTCUT_SECTIONS:
            content_layout.addWidget(_ShortcutSection(title, entries, content))

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

        subtitle = QLabel("TERMINAL CONTROL MAP")
        subtitle.setObjectName("dialogSubtitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(24, 22)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self.close)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addStretch()
        layout.addWidget(close_btn)

        header.mousePressEvent = self._drag_press
        header.mouseMoveEvent = self._drag_move
        header.mouseReleaseEvent = self._drag_release
        return header

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("shortcutFooter")
        footer.setFixedHeight(27)

        layout = QHBoxLayout(footer)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(8)

        hint = QLabel("Tip: shortcuts follow the active chart, watchlist, scanner, or floating panel focus.")
        hint.setObjectName("footerHint")

        close_btn = QPushButton("CLOSE")
        close_btn.setObjectName("footerCloseButton")
        close_btn.setFixedHeight(21)
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
                font-weight: 800;
                letter-spacing: 1.0px;
            }}

            QLabel#dialogSubtitle {{
                color: {P.TEXT_FAINT};
                background: transparent;
                font-family: {FONT_UI};
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 0.8px;
                padding-left: 4px;
            }}

            QPushButton#closeButton {{
                background: transparent;
                color: {P.TEXT_MUTED};
                border: 1px solid transparent;
                border-radius: 2px;
                font-family: {FONT_UI};
                font-size: 12px;
                font-weight: 800;
            }}

            QPushButton#closeButton:hover {{
                background: rgba(255,77,106,0.11);
                color: {P.RED};
                border-color: rgba(255,77,106,0.26);
            }}

            QFrame#shortcutBody {{
                background: {P.BG1};
            }}

            QWidget#shortcutContent {{
                background: {P.BG1};
            }}

            QScrollArea#shortcutScroll {{
                background: {P.BG1};
                border: none;
            }}

            QFrame#shortcutSection {{
                background: {P.BG2};
                border: 1px solid {P.BG4};
                border-radius: 2px;
            }}

            QFrame#sectionTitleBar {{
                background: {P.BG0};
                border-bottom: 1px solid {P.BG4};
            }}

            QFrame#sectionAccent {{
                background: {P.AMBER};
                border: none;
            }}

            QLabel#sectionTitle {{
                color: {P.TEXT_SOFT};
                background: transparent;
                font-family: {FONT_UI};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 1.0px;
            }}

            QFrame#shortcutRow {{
                background: transparent;
                border-bottom: 1px solid rgba(26,32,48,0.72);
            }}

            QFrame#shortcutRow:hover {{
                background: {P.BG3};
            }}

            QLabel#keyBadge {{
                background: {P.BG0};
                color: {P.TEXT_SOFT};
                border: 1px solid {P.BORDER_HI};
                border-radius: 2px;
                font-family: {FONT_NUM};
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 0.35px;
                padding: 2px 7px;
            }}

            QLabel#keyBadge[tone="GREEN"] {{
                color: {P.GREEN};
                border-color: rgba(0,212,168,0.26);
                background: rgba(0,212,168,0.045);
            }}

            QLabel#keyBadge[tone="RED"] {{
                color: {P.RED};
                border-color: rgba(255,77,106,0.26);
                background: rgba(255,77,106,0.045);
            }}

            QLabel#keyBadge[tone="AMBER"] {{
                color: {P.AMBER};
                border-color: rgba(245,158,11,0.26);
                background: rgba(245,158,11,0.045);
            }}

            QLabel#keyBadge[tone="CYAN"] {{
                color: {P.CYAN};
                border-color: rgba(0,212,255,0.22);
                background: rgba(0,212,255,0.04);
            }}

            QLabel#keyBadge[tone="BLUE"] {{
                color: {P.BLUE};
                border-color: rgba(127,166,216,0.24);
                background: rgba(127,166,216,0.045);
            }}

            QLabel#shortcutAction {{
                color: {P.TEXT_SOFT};
                background: transparent;
                font-family: {FONT_UI};
                font-size: 10px;
                font-weight: 600;
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
                font-weight: 600;
            }}

            QPushButton#footerCloseButton {{
                background: {P.BG2};
                color: {P.TEXT_SOFT};
                border: 1px solid {P.BG4};
                border-radius: 2px;
                font-family: {FONT_UI};
                font-size: 9px;
                font-weight: 800;
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
    """Show a compact AMOLED shortcuts reference dialog and return it."""
    dialog = KeyboardShortcutsDialog(parent)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog