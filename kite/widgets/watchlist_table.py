# kite/widgets/watchlist_table.py
"""
Institutional Watchlist — TC2000-grade, production-ready.

Features
────────
  • Unlimited user-named watchlists (create / rename / delete)
  • ⚑ Flag column (20 px) — 2 states: none ↔ green
    Flags are per-symbol and persist globally across all watchlists.
  • Heat-map % change coloring (gradient magnitude, not binary red/green)
  • Calm institutional dark color system with subdued accents
  • Modern UI number typography — clean, sharp values during live updates
  • Throttled UI redraws (~4.4 fps) via dirty-symbol batching
  • WS-powered live ticks with token→symbol O(1) resolution
  • Context menu: chart, advanced buy/sell, bracket, remove
  • Keyboard: focus → Space navigates symbols into chart
  • Persistence:
      watchlist config  → ~/.qullamaggie/watchlist_config.json
      per-list symbols  → kite/user_data/watchlist_{id}.json
      flags             → ~/.qullamaggie/watchlist_flags.json
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from functools import partial
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal, Slot, QPoint, QTimer, QSize, QSignalBlocker
from PySide6.QtGui import (
    QColor, QFont, QBrush, QCursor, QAction, QFontMetrics, QMouseEvent, QIcon
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QPushButton, QToolButton, QComboBox, QStackedWidget, QMenu,
    QDialog, QLineEdit, QDialogButtonBox, QMessageBox, QApplication
)
from app_paths import get_asset_path, get_user_data_dir

logger = logging.getLogger(__name__)


def _prefer_text_antialias(font: QFont) -> QFont:
    """Prefer antialiased glyph rasterization for crisper HiDPI text."""
    try:
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    except Exception:
        pass
    return font

CHART_TOOLBAR_HEIGHT = 26
CHART_TOOLBAR_CONTROL_HEIGHT = 22


# ─────────────────────────────────────────────────────────────────────────────
#  DESIGN TOKENS  (calm institutional dark palette)
# ─────────────────────────────────────────────────────────────────────────────

class _C:
    # AMOLED Institutional Dark Trading Terminal palette.
    # Built from near-black layers, thin borders, compact contrast, and state-only accents.
    BG0 = "#050709"  # app shell / AMOLED black
    BG1 = "#0a0d12"  # main table body
    BG2 = "#0f1318"  # alternate row / panel layer
    BG3 = "#141920"  # hover / raised layer
    BG4 = "#1a2030"  # grid lines / borders
    BG5 = "#26354a"  # active border

    BULL = "#00d4a8"      # buy / success / positive
    BULL_DIM = "#078b72"
    BULL_BG = "rgba(0,212,168,0.055)"

    BEAR = "#ff4d6a"      # sell / risk / negative
    BEAR_DIM = "#a83a4e"
    BEAR_BG = "rgba(255,77,106,0.055)"

    NEUTRAL = "#8da2bd"
    NEU_DIM = "#5a7090"

    T0 = "#e8f0ff"        # primary text
    T_SYMBOL = "#d6e2f2"  # symbol text
    T1 = "#a8bcd4"        # secondary text
    T2 = "#5a7090"        # muted labels / headers
    T3 = "#2a3a50"        # disabled / placeholder

    CYAN = "#00d4ff"      # info / utility / focus
    AMBER = "#f59e0b"     # active / warning
    BLUE = "#3b82f6"      # informational
    SEL = "#1a2840"       # selected row

    GRID = "rgba(26,32,48,0.58)"
    ROW_LINE = "rgba(26,32,48,0.42)"

    # Flag color (single-state)
    FLAG_GREEN = "#00d4a8"

    @staticmethod
    def change_color(pct: float) -> Tuple[str, str]:
        """Return (fg_color, bg_rgba) for a % change value."""
        if pct >= 3.0:
            return "#00e6b8", "rgba(0,212,168,0.12)"
        if pct >= 1.0:
            return "#00d4a8", "rgba(0,212,168,0.075)"
        if pct >= -0.5:
            return "#8da2bd", ""
        if pct >= -1.0:
            return "#ff8a9a", "rgba(255,77,106,0.07)"
        return "#ff4d6a", "rgba(255,77,106,0.12)"


_MONO = "'JetBrains Mono', 'Consolas', 'Courier New', monospace"  # raw logs / IDs / code only
_SANS = "'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', Arial, sans-serif"
_SYMBOL = "'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', Arial, sans-serif"
_NUM = "'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', sans-serif"
_UI_FONT_FAMILIES = ["Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", "Noto Sans"]
_SYMBOL_FONT_FAMILIES = ["Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", "Noto Sans"]
_NUM_FONT_FAMILIES = ["Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", "Noto Sans"]
_UI_FONT = _UI_FONT_FAMILIES[0]
_NUM_FONT = _NUM_FONT_FAMILIES[0]
_ROW_H = 21

# ─────────────────────────────────────────────────────────────────────────────
#  FLAG STATES
# ─────────────────────────────────────────────────────────────────────────────

_FLAG_CYCLE = [None, "green"]

_FLAG_DISPLAY = {
    None: ("", _C.T3),
    "green": ("⚑", _C.FLAG_GREEN),
}

_FLAG_TOOLTIP = {
    None: "Click to flag",
    "green": "Flagged — click to remove",
}

# ─────────────────────────────────────────────────────────────────────────────
#  PERSISTENCE PATHS
# ─────────────────────────────────────────────────────────────────────────────

_APP_DIR = str(get_user_data_dir("kite", os.environ.get("QULLAMAGGIE_TRADING_MODE", "live")))
_DATA_DIR = os.path.join(_APP_DIR, "watchlists")
_CONFIG_FILE = os.path.join(_APP_DIR, "watchlist_config.json")
_FLAGS_FILE = os.path.join(_APP_DIR, "watchlist_flags.json")

os.makedirs(_APP_DIR, exist_ok=True)
os.makedirs(_DATA_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
#  FLAG STORE  (global, across all watchlists)
# ─────────────────────────────────────────────────────────────────────────────

class _FlagStore:
    """Thread-safe in-process flag state. Persisted to JSON."""

    def __init__(self):
        self._flags: Dict[str, Optional[str]] = {}
        self._load()

    def get(self, symbol: str) -> Optional[str]:
        return self._flags.get(symbol.upper())

    def cycle(self, symbol: str) -> Optional[str]:
        """Advance flag to next state. Return new state."""
        sym = symbol.upper()
        cur = self._flags.get(sym)
        idx = _FLAG_CYCLE.index(cur) if cur in _FLAG_CYCLE else 0
        nxt = _FLAG_CYCLE[(idx + 1) % len(_FLAG_CYCLE)]
        if nxt is None:
            self._flags.pop(sym, None)
        else:
            self._flags[sym] = nxt
        self._save()
        return nxt

    def all_flagged(self) -> Dict[str, str]:
        return dict(self._flags)

    def _load(self):
        try:
            if os.path.exists(_FLAGS_FILE):
                with open(_FLAGS_FILE, "r") as f:
                    self._flags = json.load(f)
        except Exception as e:
            logger.error(f"FlagStore load failed: {e}")

    def _save(self):
        try:
            with open(_FLAGS_FILE, "w") as f:
                json.dump(self._flags, f, indent=2)
        except Exception as e:
            logger.error(f"FlagStore save failed: {e}")


_flag_store = _FlagStore()  # module-level singleton


# ─────────────────────────────────────────────────────────────────────────────
#  WATCHLIST CONFIG  (names, order, ids)
# ─────────────────────────────────────────────────────────────────────────────

class _WatchlistConfig:
    """
    Manages the list of named watchlists.
    Each entry: {"id": str, "name": str}
    """

    _DEFAULT = [
        {"id": "breakouts", "name": "Breakouts"},
        {"id": "ep", "name": "EP"},
        {"id": "parabolic", "name": "Parabolic"},
    ]

    def __init__(self):
        self._lists: List[Dict] = []
        self._load()

    def all(self) -> List[Dict]:
        return list(self._lists)

    def add(self, name: str) -> Dict:
        entry = {"id": f"wl_{uuid.uuid4().hex[:8]}", "name": name.strip()}
        self._lists.append(entry)
        self._save()
        return entry

    def rename(self, wl_id: str, new_name: str) -> bool:
        for entry in self._lists:
            if entry["id"] == wl_id:
                entry["name"] = new_name.strip()
                self._save()
                return True
        return False

    def remove(self, wl_id: str) -> bool:
        before = len(self._lists)
        self._lists = [e for e in self._lists if e["id"] != wl_id]
        if len(self._lists) < before:
            self._save()
            # Also delete data file
            path = _data_path(wl_id)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
            return True
        return False

    def reorder(self, ids: List[str]) -> None:
        id_map = {e["id"]: e for e in self._lists}
        self._lists = [id_map[i] for i in ids if i in id_map]
        self._save()

    def _load(self):
        try:
            if os.path.exists(_CONFIG_FILE):
                with open(_CONFIG_FILE, "r") as f:
                    self._lists = json.load(f)
            else:
                self._lists = list(self._DEFAULT)
                self._save()
        except Exception as e:
            logger.error(f"WatchlistConfig load failed: {e}")
            self._lists = list(self._DEFAULT)

    def _save(self):
        try:
            with open(_CONFIG_FILE, "w") as f:
                json.dump(self._lists, f, indent=2)
        except Exception as e:
            logger.error(f"WatchlistConfig save failed: {e}")


def _data_path(wl_id: str) -> str:
    return os.path.join(_DATA_DIR, f"watchlist_{wl_id}.json")


def _load_symbols(wl_id: str) -> List[str]:
    path = _data_path(wl_id)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_symbols(wl_id: str, symbols: List[str]) -> None:
    try:
        with open(_data_path(wl_id), "w") as f:
            json.dump(symbols, f, indent=2)
    except Exception as e:
        logger.error(f"Save symbols failed for {wl_id}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  RENAME DIALOG
# ─────────────────────────────────────────────────────────────────────────────


class _RenameDialog(QDialog):
    """Compact terminal-style dialog for renaming a watchlist."""

    def __init__(self, current_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rename Watchlist")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setFixedSize(360, 164)
        self._drag_pos = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        container = QFrame()
        container.setObjectName("nameDialogContainer")
        root.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        title_bar = QFrame()
        title_bar.setObjectName("nameDialogTitleBar")
        title_bar.setFixedHeight(30)
        title_bar.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        title_row = QHBoxLayout(title_bar)
        title_row.setContentsMargins(10, 0, 6, 0)
        title_row.setSpacing(6)

        title = QLabel("RENAME WATCHLIST")
        title.setObjectName("nameDialogTitle")
        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setObjectName("nameDialogCloseBtn")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self.reject)

        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(close_btn)
        layout.addWidget(title_bar)

        body = QFrame()
        body.setObjectName("nameDialogBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(12, 10, 12, 12)
        body_layout.setSpacing(8)

        label = QLabel("WATCHLIST NAME")
        label.setObjectName("fieldLabel")
        self.input = QLineEdit(current_name)
        self.input.setObjectName("terminalInput")
        self.input.selectAll()

        btns = QHBoxLayout()
        btns.setContentsMargins(0, 2, 0, 0)
        btns.setSpacing(6)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("secondaryButton")
        ok = QPushButton("Rename")
        ok.setObjectName("infoButton")
        cancel.setFixedHeight(26)
        ok.setFixedHeight(26)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._accept)
        self.input.returnPressed.connect(self._accept)

        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(ok)

        body_layout.addWidget(label)
        body_layout.addWidget(self.input)
        body_layout.addLayout(btns)
        layout.addWidget(body, 1)

        title_bar.mousePressEvent = self._drag_press
        title_bar.mouseMoveEvent = self._drag_move
        title_bar.mouseReleaseEvent = self._drag_release

        self._apply_dialog_style()

    def _drag_press(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _drag_move(self, event: QMouseEvent):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def _drag_release(self, _event: QMouseEvent):
        self._drag_pos = None

    def _apply_dialog_style(self):
        self.setStyleSheet(f"""
            QFrame#nameDialogContainer {{
                background: {_C.BG1};
                border: 1px solid {_C.BG4};
                border-radius: 2px;
            }}
            QFrame#nameDialogTitleBar {{
                background: {_C.BG0};
                border-bottom: 1px solid {_C.BG4};
            }}
            QLabel#nameDialogTitle {{
                color: {_C.AMBER};
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 0.8px;
                background: transparent;
            }}
            QFrame#nameDialogBody {{ background: {_C.BG1}; }}
            QLabel#fieldLabel {{
                color: {_C.T2};
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 500;
                letter-spacing: 0.6px;
                background: transparent;
            }}
            QLineEdit#terminalInput {{
                background: {_C.BG2};
                color: {_C.T0};
                border: 1px solid {_C.BG4};
                border-radius: 2px;
                padding: 5px 8px;
                font-family: {_SANS};
                font-size: 12px;
                selection-background-color: {_C.SEL};
            }}
            QLineEdit#terminalInput:focus {{
                border-color: {_C.CYAN};
                background: {_C.BG3};
            }}
            QToolButton#nameDialogCloseBtn {{
                background: transparent;
                color: {_C.T2};
                border: none;
                border-radius: 2px;
                font-size: 11px;
            }}
            QToolButton#nameDialogCloseBtn:hover {{
                background: rgba(224,122,132,0.12);
                color: {_C.BEAR};
            }}
            QPushButton#secondaryButton, QPushButton#infoButton {{
                border-radius: 2px;
                font-family: {_SANS};
                font-size: 11px;
                font-weight: 500;
                padding: 0 12px;
                min-width: 70px;
            }}
            QPushButton#secondaryButton {{
                background: {_C.BG2};
                color: {_C.T1};
                border: 1px solid {_C.BG4};
            }}
            QPushButton#secondaryButton:hover {{
                background: {_C.BG3};
                color: {_C.T0};
            }}
            QPushButton#infoButton {{
                background: rgba(120,207,225,0.07);
                color: {_C.CYAN};
                border: 1px solid rgba(120,207,225,0.22);
            }}
            QPushButton#infoButton:hover {{
                background: rgba(120,207,225,0.12);
                border-color: {_C.CYAN};
            }}
        """)

    def _accept(self):
        if self.input.text().strip():
            self.accept()

    def name(self) -> str:
        return self.input.text().strip()

# ─────────────────────────────────────────────────────────────────────────────
#  ADD WATCHLIST DIALOG
# ─────────────────────────────────────────────────────────────────────────────


class _AddWatchlistDialog(QDialog):
    """Compact terminal-style dialog for creating a watchlist."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Watchlist")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setFixedSize(360, 164)
        self._drag_pos = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        container = QFrame()
        container.setObjectName("nameDialogContainer")
        root.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        title_bar = QFrame()
        title_bar.setObjectName("nameDialogTitleBar")
        title_bar.setFixedHeight(30)
        title_bar.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        title_row = QHBoxLayout(title_bar)
        title_row.setContentsMargins(10, 0, 6, 0)
        title_row.setSpacing(6)

        title = QLabel("NEW WATCHLIST")
        title.setObjectName("nameDialogTitle")
        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setObjectName("nameDialogCloseBtn")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self.reject)

        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(close_btn)
        layout.addWidget(title_bar)

        body = QFrame()
        body.setObjectName("nameDialogBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(12, 10, 12, 12)
        body_layout.setSpacing(8)

        label = QLabel("WATCHLIST NAME")
        label.setObjectName("fieldLabel")
        self.input = QLineEdit()
        self.input.setObjectName("terminalInput")
        self.input.setPlaceholderText("Momentum, Breakouts, Swing Setups…")

        btns = QHBoxLayout()
        btns.setContentsMargins(0, 2, 0, 0)
        btns.setSpacing(6)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("secondaryButton")
        ok = QPushButton("Create")
        ok.setObjectName("confirmButton")
        cancel.setFixedHeight(26)
        ok.setFixedHeight(26)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._accept)
        self.input.returnPressed.connect(self._accept)

        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(ok)

        body_layout.addWidget(label)
        body_layout.addWidget(self.input)
        body_layout.addLayout(btns)
        layout.addWidget(body, 1)

        title_bar.mousePressEvent = self._drag_press
        title_bar.mouseMoveEvent = self._drag_move
        title_bar.mouseReleaseEvent = self._drag_release

        self._apply_dialog_style()

    def _drag_press(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _drag_move(self, event: QMouseEvent):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def _drag_release(self, _event: QMouseEvent):
        self._drag_pos = None

    def _apply_dialog_style(self):
        self.setStyleSheet(f"""
            QFrame#nameDialogContainer {{
                background: {_C.BG1};
                border: 1px solid {_C.BG4};
                border-radius: 2px;
            }}
            QFrame#nameDialogTitleBar {{
                background: {_C.BG0};
                border-bottom: 1px solid {_C.BG4};
            }}
            QLabel#nameDialogTitle {{
                color: {_C.AMBER};
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 0.8px;
                background: transparent;
            }}
            QFrame#nameDialogBody {{ background: {_C.BG1}; }}
            QLabel#fieldLabel {{
                color: {_C.T2};
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 500;
                letter-spacing: 0.6px;
                background: transparent;
            }}
            QLineEdit#terminalInput {{
                background: {_C.BG2};
                color: {_C.T0};
                border: 1px solid {_C.BG4};
                border-radius: 2px;
                padding: 5px 8px;
                font-family: {_SANS};
                font-size: 12px;
                selection-background-color: {_C.SEL};
            }}
            QLineEdit#terminalInput:focus {{
                border-color: {_C.CYAN};
                background: {_C.BG3};
            }}
            QLineEdit#terminalInput::placeholder {{ color: {_C.T3}; }}
            QToolButton#nameDialogCloseBtn {{
                background: transparent;
                color: {_C.T2};
                border: none;
                border-radius: 2px;
                font-size: 11px;
            }}
            QToolButton#nameDialogCloseBtn:hover {{
                background: rgba(224,122,132,0.12);
                color: {_C.BEAR};
            }}
            QPushButton#secondaryButton, QPushButton#confirmButton {{
                border-radius: 2px;
                font-family: {_SANS};
                font-size: 11px;
                font-weight: 500;
                padding: 0 12px;
                min-width: 70px;
            }}
            QPushButton#secondaryButton {{
                background: {_C.BG2};
                color: {_C.T1};
                border: 1px solid {_C.BG4};
            }}
            QPushButton#secondaryButton:hover {{
                background: {_C.BG3};
                color: {_C.T0};
            }}
            QPushButton#confirmButton {{
                background: rgba(114,205,182,0.08);
                color: {_C.BULL};
                border: 1px solid rgba(114,205,182,0.24);
            }}
            QPushButton#confirmButton:hover {{
                background: rgba(114,205,182,0.12);
                border-color: {_C.BULL};
            }}
        """)

    def _accept(self):
        if self.input.text().strip():
            self.accept()

    def name(self) -> str:
        return self.input.text().strip()

# ─────────────────────────────────────────────────────────────────────────────
#  TRADING TABLE  (single watchlist pane)
# ─────────────────────────────────────────────────────────────────────────────

_COL_FLAG = 0
_COL_SYMBOL = 1
_COL_LTP = 2
_COL_VOL = 3
_COL_CHG = 4
_NUM_COLS = 5

_HEADERS = ["", "Symbol", "LTP", "Vol", "%Chg"]


class TradingTable(QTableWidget):
    """
    Single watchlist table.

    Columns: ⚑ | Symbol | LTP | Vol | %Chg

    Flag column (20 px): click to cycle flag state.
    Numeric values use modern UI number typography. Heat-map on %Chg.
    """

    symbol_selected = Signal(str)
    place_order_requested = Signal(dict)
    advanced_buy_order_requested = Signal(str)
    advanced_sell_order_requested = Signal(str)
    bracket_order_requested = Signal(str)
    watchlist_symbols_changed = Signal()

    def __init__(self, wl_id: str, parent=None):
        super().__init__(parent)
        self.wl_id = wl_id

        self._instrument_map: Dict[str, Dict] = {}
        self._watchlist_data: Dict[str, Dict] = {}
        self._symbol_to_row: Dict[str, int] = {}
        self._token_to_symbol: Dict[int, str] = {}
        self._symbols: List[str] = []  # ordered list
        self._dirty: set = set()

        self._color_theme: Dict = {"show_table_vertical_lines": True}
        self._sort_col: int = _COL_SYMBOL
        self._sort_asc: bool = True
        self._chg_sort_state = None  # None -> asc -> desc -> None
        self._last_tick_time: float = 0.0

        self._configure_table()
        self._connect_signals()

        # Throttled redraw
        self._flush_timer = QTimer(self)
        self._flush_timer.timeout.connect(self._flush_dirty)
        self._flush_timer.start(225)

        # Fallback refresh when no ticks arrive
        self._fallback_timer = QTimer(self)
        self._fallback_timer.timeout.connect(self._fallback_refresh)
        self._fallback_timer.start(5000)

    # ── Configuration ──────────────────────────────────────────────────────

    def _configure_table(self):
        self.setColumnCount(_NUM_COLS)
        self.setHorizontalHeaderLabels(_HEADERS)

        hdr = self.horizontalHeader()
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.setMinimumSectionSize(20)
        hdr.setStretchLastSection(False)
        hdr.setHighlightSections(False)
        hdr.setFixedHeight(20)

        # Flag col — fixed tight
        hdr.setSectionResizeMode(_COL_FLAG, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(_COL_FLAG, 20)

        # Symbol — stretches
        hdr.setSectionResizeMode(_COL_SYMBOL, QHeaderView.ResizeMode.Stretch)

        # Data cols — dynamically sized to keep right-side tables visually aligned
        for col in (_COL_LTP, _COL_VOL, _COL_CHG):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)

        self._apply_dynamic_column_widths()

        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(_ROW_H)
        self.verticalHeader().setMinimumSectionSize(_ROW_H)

        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setShowGrid(True)
        self.setAlternatingRowColors(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSortingEnabled(False)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # Keep a stable viewport width so dynamic column sizing stays aligned
        # with the positions table even when row count crosses scrollbar threshold.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setWordWrap(False)

        hdr.sectionClicked.connect(self._on_header_click)
        hdr.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setColumnHidden(_COL_VOL, not bool(self._color_theme.get("show_watchlist_volume_column", True)))
        self._apply_dynamic_column_widths()


    def _apply_dynamic_column_widths(self) -> None:
        """Keep numeric columns balanced and adaptive with available width."""
        flag_w = 20
        min_symbol_w = 96
        min_data_w = 62
        max_data_w = 120

        visible_data_cols = [_COL_LTP, _COL_CHG]
        if not self.isColumnHidden(_COL_VOL):
            visible_data_cols.insert(1, _COL_VOL)

        if not visible_data_cols:
            return

        viewport_w = max(self.viewport().width(), 0)
        available_for_data = max(0, viewport_w - flag_w - min_symbol_w)
        data_w = max(min_data_w, min(max_data_w, available_for_data // len(visible_data_cols)))

        for col in visible_data_cols:
            self.setColumnWidth(col, data_w)

    def _connect_signals(self):
        self.cellClicked.connect(self._on_cell_click)
        self.customContextMenuRequested.connect(self._show_ctx_menu)
        self.focusOutEvent = self._on_focus_out

    # ── Public API ─────────────────────────────────────────────────────────

    def set_instrument_map(self, instrument_map: Dict[str, Dict]) -> None:
        self._instrument_map = instrument_map
        self._init_data_for_existing()
        self._repopulate()

    def load_symbols(self, symbols: List[str]) -> None:
        self._symbols = [s for s in symbols if s]
        if self._instrument_map:
            self._init_data_for_existing()
        self._repopulate()

    def add_symbol(self, symbol: str) -> bool:
        if not symbol or symbol in self._symbols:
            return False
        if symbol not in self._instrument_map:
            return False
        self._symbols.append(symbol)
        self._init_symbol_data(symbol)
        self._repopulate()
        self.watchlist_symbols_changed.emit()
        return True

    def apply_symbol_snapshot(
        self,
        symbol: str,
        *,
        ltp: Optional[float] = None,
        prev_close: Optional[float] = None,
        volume: Optional[int] = None,
    ) -> None:
        """Apply a one-time quote snapshot for a symbol (useful when market is closed)."""
        data = self._watchlist_data.get(symbol)
        if not data:
            return

        if ltp is not None:
            try:
                data["ltp"] = float(ltp)
            except (TypeError, ValueError):
                pass

        if prev_close is not None:
            try:
                data["prev_close"] = float(prev_close)
            except (TypeError, ValueError):
                pass

        if volume is not None:
            try:
                parsed_volume = int(volume)
                if parsed_volume > 0:
                    data["volume"] = parsed_volume
            except (TypeError, ValueError):
                pass

        prev = float(data.get("prev_close", 0.0) or 0.0)
        cur = float(data.get("ltp", 0.0) or 0.0)
        data["change_pct"] = (cur - prev) / prev * 100 if prev > 0 and cur > 0 else 0.0

        if symbol in self._symbol_to_row:
            self._dirty.add(symbol)

    def remove_symbol(self, symbol: str) -> bool:
        if symbol not in self._symbols:
            return False
        self._symbols.remove(symbol)
        self._watchlist_data.pop(symbol, None)
        self._rebuild_token_map()
        self._repopulate()
        self.watchlist_symbols_changed.emit()
        return True

    def get_symbol_list(self) -> List[str]:
        symbols: List[str] = []
        for row in range(self.rowCount()):
            sym = self._symbol_at_row(row)
            if sym:
                symbols.append(sym)
        return symbols

    def get_all_tokens(self) -> List[int]:
        return list(self._token_to_symbol.keys())

    def apply_color_theme(self, theme: Dict) -> None:
        self._color_theme = theme
        self.setShowGrid(True)
        self.setColumnHidden(_COL_VOL, not bool(self._color_theme.get("show_watchlist_volume_column", True)))
        self._apply_dynamic_column_widths()
        for sym, row in self._symbol_to_row.items():
            data = self._watchlist_data.get(sym)
            if data:
                self._update_row(row, data)


    def _table_color(self, key: str, fallback: str) -> str:
        tables = self._color_theme.get("tables", {}) if isinstance(self._color_theme, dict) else {}
        color = tables.get(key, fallback)
        return color if isinstance(color, str) and color.startswith("#") else fallback

    def _change_colors(self, pct: float) -> Tuple[str, str]:
        positive = self._table_color("positive", _C.BULL)
        negative = self._table_color("negative", _C.BEAR)
        neutral = self._table_color("neutral", _C.NEUTRAL)
        if pct >= 3.0:
            return positive, self._rgba_for_color(positive, 0.12)
        if pct >= 1.0:
            return positive, self._rgba_for_color(positive, 0.075)
        if pct >= -0.5:
            return neutral, ""
        if pct >= -1.0:
            return negative, self._rgba_for_color(negative, 0.07)
        return negative, self._rgba_for_color(negative, 0.12)

    @staticmethod
    def _rgba_for_color(color_hex: str, alpha: float) -> str:
        color = QColor(color_hex)
        return f"rgba({color.red()},{color.green()},{color.blue()},{max(0.0, min(1.0, alpha))})"

    def update_data(self, ticks: List[Dict]) -> None:
        """Process WS ticks — O(1) per tick via pre-built token map."""
        import time
        if ticks:
            self._last_tick_time = time.monotonic()

        for tick in ticks:
            raw = tick.get("instrument_token")
            if raw is None:
                continue
            try:
                token = int(raw)
            except (TypeError, ValueError):
                continue

            sym = self._token_to_symbol.get(token)
            if not sym:
                continue

            data = self._watchlist_data[sym]
            ltp = tick.get("last_price")
            if ltp is not None:
                data["ltp"] = float(ltp)

            for vf in ("volume_traded", "volume"):
                vol = tick.get(vf)
                if vol is not None:
                    try:
                        v = int(vol)
                        if v > 0:
                            data["volume"] = v
                            break
                    except (TypeError, ValueError):
                        pass

            ohlc = tick.get("ohlc")
            if isinstance(ohlc, dict):
                close = ohlc.get("close")
                if close:
                    data["prev_close"] = float(close)

            prev = data.get("prev_close", 0.0)
            cur = data.get("ltp", 0.0)
            if prev > 0 and cur > 0:
                data["change_pct"] = (cur - prev) / prev * 100

            if sym in self._symbol_to_row:
                self._dirty.add(sym)

    # ── Internal: data ─────────────────────────────────────────────────────

    def _init_data_for_existing(self):
        for sym in self._symbols:
            if sym in self._instrument_map:
                self._init_symbol_data(sym)
        self._rebuild_token_map()

    def _init_symbol_data(self, symbol: str):
        inst = self._instrument_map.get(symbol, {})
        ohlc = inst.get("ohlc", {}) or {}
        prev = ohlc.get("close", 0.0) if isinstance(ohlc, dict) else 0.0
        ltp = inst.get("last_price", 0.0) or 0.0
        vol = inst.get("volume", 0) or 0
        chg = (ltp - prev) / prev * 100 if prev > 0 and ltp > 0 else 0.0

        self._watchlist_data[symbol] = {
            "tradingsymbol": symbol,
            "instrument_token": inst.get("instrument_token"),
            "exchange": inst.get("exchange", "NSE"),
            "ltp": ltp,
            "volume": vol,
            "prev_close": prev,
            "change_pct": chg,
        }

    def _rebuild_token_map(self):
        self._token_to_symbol = {}
        for sym, data in self._watchlist_data.items():
            tok = data.get("instrument_token")
            if tok is not None:
                try:
                    self._token_to_symbol[int(tok)] = sym
                except (TypeError, ValueError):
                    pass

    # ── Internal: rendering ────────────────────────────────────────────────

    def _repopulate(self):
        self.setRowCount(0)
        self._symbol_to_row.clear()

        for row, sym in enumerate(self._symbols):
            self._symbol_to_row[sym] = row
            self.insertRow(row)
            for col in range(_NUM_COLS):
                self.setItem(row, col, QTableWidgetItem())

            # Flag cell
            self._paint_flag_cell(row, sym)

            data = self._watchlist_data.get(sym)
            if data:
                self._update_row(row, data)
            else:
                sym_item = self.item(row, _COL_SYMBOL)
                if sym_item:
                    sym_item.setText(sym)
                    sym_item.setForeground(QColor(_C.T_SYMBOL))
                    sym_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                    sym_item.setFont(self._symbol_font())

    def _update_row(self, row: int, data: Dict):
        if row >= self.rowCount():
            return

        sym = data.get("tradingsymbol", "")
        ltp = data.get("ltp", 0.0)
        vol = data.get("volume", 0)
        chg = data.get("change_pct", 0.0)

        # ── Flag ──
        self._paint_flag_cell(row, sym)

        symbol_font = self._symbol_font()
        value_font = self._number_font(False)
        strong_value_font = self._number_font(True)

        # ── Symbol ──
        sym_item = self.item(row, _COL_SYMBOL)
        if sym_item:
            sym_item.setText(sym)
            sym_item.setForeground(QColor(_C.T_SYMBOL))
            sym_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            sym_item.setFont(symbol_font)

        # ── LTP ──
        ltp_text = f"{ltp:.2f}" if ltp > 0 else "—"
        ltp_item = self.item(row, _COL_LTP)
        if ltp_item:
            ltp_item.setText(ltp_text)
            ltp_item.setForeground(QColor(_C.T0))
            ltp_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            ltp_item.setFont(value_font)

        # ── Volume ──
        vol_text = self._fmt_volume(vol)
        vol_item = self.item(row, _COL_VOL)
        if vol_item:
            vol_item.setText(vol_text)
            vol_item.setForeground(QColor(_C.T2))
            vol_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            vol_item.setToolTip(f"Volume: {vol:,}")
            vol_item.setFont(value_font)

        # ── Chg% with heat-map ──
        chg_text = f"{chg:+.2f}" if abs(chg) > 0.005 else "0.00"
        fg, bg_rgba = self._change_colors(chg)
        chg_item = self.item(row, _COL_CHG)
        if chg_item:
            chg_item.setText(chg_text)
            chg_item.setForeground(QColor(fg))
            chg_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            chg_item.setFont(strong_value_font)
            if bg_rgba:
                r, g, b, a = self._parse_rgba(bg_rgba)
                chg_item.setBackground(QBrush(QColor(r, g, b, a)))
            else:
                chg_item.setBackground(QBrush(QColor(_C.BG2)))

        # ── LTP heat-map tint (subtle, for directional context) ──
        if ltp_item and abs(chg) > 0.005:
            ltp_item.setForeground(QColor(fg))

    def _paint_flag_cell(self, row: int, symbol: str):
        state = _flag_store.get(symbol)
        glyph, color = _FLAG_DISPLAY[state]
        item = self.item(row, _COL_FLAG)
        if not item:
            item = QTableWidgetItem()
            self.setItem(row, _COL_FLAG, item)
        item.setText(glyph)
        item.setForeground(QColor(color))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setToolTip(_FLAG_TOOLTIP[state])
        f = QFont()
        f.setPointSize(10)
        item.setFont(f)

    def _flush_dirty(self):
        if not self._dirty:
            return
        for sym in tuple(self._dirty):
            row = self._symbol_to_row.get(sym)
            data = self._watchlist_data.get(sym)
            if row is not None and data is not None:
                self._update_row(row, data)
        self._dirty.clear()

    def _fallback_refresh(self):
        import time
        if time.monotonic() - self._last_tick_time < 4.0:
            return
        for sym, row in self._symbol_to_row.items():
            data = self._watchlist_data.get(sym)
            if data:
                self._update_row(row, data)

    # ── Event handlers ─────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        """Spacebar moves selection down one row and opens selected symbol chart."""
        if event.key() == Qt.Key.Key_Space:
            row_count = self.rowCount()
            if row_count == 0:
                event.accept()
                return

            current_row = self.currentRow()
            next_row = 0 if current_row < 0 else (current_row + 1) % row_count

            self.selectRow(next_row)
            self.setCurrentCell(next_row, _COL_SYMBOL)

            sym = self._symbol_at_row(next_row)
            if sym and not sym.startswith("─"):
                self.symbol_selected.emit(sym)

            event.accept()
            return

        super().keyPressEvent(event)

    def _on_cell_click(self, row: int, col: int):
        if col == _COL_FLAG:
            sym = self._symbol_at_row(row)
            if sym:
                _flag_store.cycle(sym)
                self._paint_flag_cell(row, sym)
            return
        sym = self._symbol_at_row(row)
        if sym and not sym.startswith("─"):
            self.symbol_selected.emit(sym)

    def _on_focus_out(self, event):
        """Keep the watchlist selection visible when focus moves to the chart."""
        QTableWidget.focusOutEvent(self, event)

    def _on_header_click(self, col: int):
        if col == _COL_FLAG:
            return
        if col == _COL_CHG:
            if self._chg_sort_state is None:
                self._chg_sort_state = "asc"
                self._sort_col = _COL_CHG
                self._sort_asc = True
            elif self._chg_sort_state == "asc":
                self._chg_sort_state = "desc"
                self._sort_col = _COL_CHG
                self._sort_asc = False
            else:
                self._chg_sort_state = None
                self._sort_col = _COL_SYMBOL
                self._sort_asc = True
            self._sort_and_repopulate()
            return

        self._chg_sort_state = None
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = col == _COL_SYMBOL
        self._sort_and_repopulate()

    def _sort_and_repopulate(self):
        def _key(sym):
            d = self._watchlist_data.get(sym, {})
            if self._sort_col == _COL_SYMBOL: return sym
            if self._sort_col == _COL_LTP:    return d.get("ltp", 0.0)
            if self._sort_col == _COL_VOL:    return d.get("volume", 0)
            if self._sort_col == _COL_CHG:    return d.get("change_pct", 0.0)
            return sym

        self._symbols.sort(key=_key, reverse=not self._sort_asc)
        self._repopulate()

    def _show_ctx_menu(self, pos: QPoint):
        row = self.rowAt(pos.y())
        if row < 0:
            return
        sym = self._symbol_at_row(row)
        if not sym or sym.startswith("─"):
            return

        menu = QMenu(self)
        menu.setObjectName("wlCtxMenu")

        flag_state = _flag_store.get(sym)
        next_states = {None: "⚑  Add Flag", "green": "⚑  Remove Flag"}
        flag_act = menu.addAction(next_states.get(flag_state, "⚑  Toggle Flag"))
        flag_act.triggered.connect(lambda: self._cycle_flag(row, sym))
        menu.addSeparator()

        chart_act = menu.addAction("Open Chart")
        chart_act.triggered.connect(lambda: self.symbol_selected.emit(sym))

        menu.addSeparator()
        buy_act = menu.addAction("BUY")
        buy_act.triggered.connect(lambda: self.advanced_buy_order_requested.emit(sym))
        sell_act = menu.addAction("SELL")
        sell_act.triggered.connect(lambda: self.advanced_sell_order_requested.emit(sym))
        bo_act = menu.addAction("Bracket Order")
        bo_act.triggered.connect(lambda: self.bracket_order_requested.emit(sym))

        menu.addSeparator()
        rm_act = menu.addAction("✕  Remove")
        rm_act.triggered.connect(lambda: self.remove_symbol(sym))

        menu.exec(self.viewport().mapToGlobal(pos))

    def _cycle_flag(self, row: int, sym: str):
        _flag_store.cycle(sym)
        self._paint_flag_cell(row, sym)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _symbol_at_row(self, row: int) -> Optional[str]:
        for s, r in self._symbol_to_row.items():
            if r == row:
                return s
        return None

    @staticmethod
    def _font_from_families(
        families: List[str],
        point_size: int = 9,
        weight: QFont.Weight = QFont.Weight.Normal,
        letter_spacing: float = 100.0,
        pixel_size: Optional[int] = None,
    ) -> QFont:
        """Build a Qt font with real fallbacks and optional pixel sizing."""
        f = QFont()
        if hasattr(f, "setFamilies"):
            f.setFamilies(families)
        elif families:
            f.setFamily(families[0])

        f.setStyleHint(QFont.StyleHint.SansSerif)
        if pixel_size is not None:
            f.setPixelSize(pixel_size)
        else:
            f.setPointSize(point_size)
        f.setWeight(weight)
        f.setKerning(True)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, letter_spacing)
        return f

    @staticmethod
    def _ui_font() -> QFont:
        """Modern readable UI font; matched to the scanner table sizing."""
        return TradingTable._font_from_families(
            _UI_FONT_FAMILIES,
            point_size=9,
            weight=QFont.Weight.Normal,
        )

    @staticmethod
    def _symbol_font() -> QFont:
        """Compact symbol font. Pixel sizing prevents ticker text from growing too large."""
        return TradingTable._font_from_families(
            _SYMBOL_FONT_FAMILIES,
            pixel_size=10,
            weight=QFont.Weight.Normal,
            letter_spacing=103.0,
        )

    @staticmethod
    def _number_font(bold: bool = False) -> QFont:
        """Calm modern UI number font for prices, volume and percentage values."""
        return TradingTable._font_from_families(
            _NUM_FONT_FAMILIES,
            point_size=9,
            weight=QFont.Weight.Medium if bold else QFont.Weight.Normal,
        )

    @staticmethod
    def _mono_font(bold: bool = False) -> QFont:
        """Monospace reserved for raw logs, IDs, code and technical debug text."""
        f = QFont("Consolas")
        if hasattr(f, "setFamilies"):
            f.setFamilies(["Consolas", "JetBrains Mono", "Courier New"])
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPointSize(9)
        f.setWeight(QFont.Weight.Medium if bold else QFont.Weight.Normal)
        return f

    @staticmethod
    def _fmt_volume(vol: int) -> str:
        if vol >= 10_000_000: return f"{vol / 1_000_000:.0f}M"
        if vol >= 1_000_000:  return f"{vol / 1_000_000:.1f}M"
        if vol >= 1_000:      return f"{vol / 1_000:.0f}K"
        return str(vol) if vol > 0 else "—"

    @staticmethod
    def _parse_rgba(rgba: str) -> Tuple[int, int, int, int]:
        """Parse 'rgba(r,g,b,a)' → (r, g, b, a_0_255)."""
        try:
            inner = rgba[5:-1]
            parts = [p.strip() for p in inner.split(",")]
            r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
            a = int(float(parts[3]) * 255)
            return r, g, b, a
        except Exception:
            return 20, 20, 30, 80

    def resizeEvent(self, event):
        super().resizeEvent(event)
        with QSignalBlocker(self.horizontalHeader()):
            self.setColumnWidth(_COL_FLAG, 20)
            self._apply_dynamic_column_widths()


# ─────────────────────────────────────────────────────────────────────────────
#  TABBED WATCHLIST WIDGET  (main widget)
# ─────────────────────────────────────────────────────────────────────────────

class TabbedWatchlistWidget(QWidget):
    """
    Master watchlist widget.

    Exposes the same signals as the old widget so main_window wiring is unchanged.
    Adds: create / rename / delete watchlists via UI.
    """

    symbol_selected = Signal(str)
    subscribe_tokens_requested = Signal(list)
    place_order_requested = Signal(dict)
    advanced_buy_order_requested = Signal(str)
    advanced_sell_order_requested = Signal(str)
    bracket_order_requested = Signal(str)
    watchlist_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._instrument_map: Dict[str, Dict] = {}
        self._quote_client = None
        self._tables: Dict[str, TradingTable] = {}  # id → table
        self._config = _WatchlistConfig()
        self._color_theme: Dict = {"show_table_vertical_lines": True}

        self._setup_ui()
        self._apply_styles()
        self._build_from_config()

    # ── Construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        self._stack = QStackedWidget()
        self._stack.setObjectName("wlStack")
        root.addWidget(self._stack)

    def _build_header(self) -> QFrame:
        hdr = QFrame()
        hdr.setObjectName("wlHeader")
        hdr.setFixedHeight(CHART_TOOLBAR_HEIGHT)

        h = QHBoxLayout(hdr)
        h.setContentsMargins(6, 0, 4, 0)
        h.setSpacing(4)

        # Dropdown selector
        self._dropdown = QComboBox()
        self._dropdown.setObjectName("wlDropdown")
        self._dropdown.setFixedHeight(CHART_TOOLBAR_CONTROL_HEIGHT)
        self._dropdown.currentIndexChanged.connect(self._on_dropdown_change)
        self._dropdown.installEventFilter(self)
        h.addWidget(self._dropdown, 1)

        # Add watchlist button
        self._add_btn = QToolButton()
        self._add_btn.setObjectName("wlAddBtn")
        self._add_btn.setText("+")
        self._add_btn.setFixedSize(CHART_TOOLBAR_CONTROL_HEIGHT, CHART_TOOLBAR_CONTROL_HEIGHT)
        self._add_btn.setToolTip("Create new watchlist")
        self._add_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._add_btn.clicked.connect(self._create_watchlist)
        h.addWidget(self._add_btn)

        # Menu (rename / delete)
        self._menu_btn = QToolButton()
        self._menu_btn.setObjectName("wlMenuBtn")
        menu_icon_path = get_asset_path("icons", "gear_setting.svg", required=True)
        if menu_icon_path is not None:
            self._menu_btn.setIcon(QIcon(str(menu_icon_path)))
            self._menu_btn.setIconSize(QSize(12, 12))
        else:
            self._menu_btn.setText("⋯")
        self._menu_btn.setFixedSize(CHART_TOOLBAR_CONTROL_HEIGHT, CHART_TOOLBAR_CONTROL_HEIGHT)
        self._menu_btn.setToolTip("Watchlist options")
        self._menu_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._menu_btn.clicked.connect(self._show_list_menu)
        h.addWidget(self._menu_btn)

        return hdr

    def _build_from_config(self):
        """Build table widgets from persisted config."""
        entries = self._config.all()
        if not entries:
            # Safety: create a default list
            entry = self._config.add("Watchlist 1")
            entries = [entry]

        for entry in entries:
            self._add_table_for_entry(entry)

        self._rebuild_dropdown()
        if self._stack.count():
            self._stack.setCurrentIndex(0)

    def _add_table_for_entry(self, entry: Dict) -> TradingTable:
        wl_id = entry["id"]
        table = TradingTable(wl_id)
        self._tables[wl_id] = table
        self._stack.addWidget(table)

        # Load persisted symbols
        symbols = _load_symbols(wl_id)
        table.load_symbols(symbols)
        if self._instrument_map:
            table.set_instrument_map(self._instrument_map)

        # Wire signals
        table.symbol_selected.connect(self.symbol_selected.emit)
        table.place_order_requested.connect(self.place_order_requested.emit)
        table.advanced_buy_order_requested.connect(self.advanced_buy_order_requested.emit)
        table.advanced_sell_order_requested.connect(self.advanced_sell_order_requested.emit)
        table.bracket_order_requested.connect(self.bracket_order_requested.emit)
        table.watchlist_symbols_changed.connect(
            partial(self._on_symbols_changed, wl_id)
        )
        return table

    def _rebuild_dropdown(self):
        self._dropdown.blockSignals(True)
        current_id = self._current_wl_id()
        self._dropdown.clear()

        for entry in self._config.all():
            self._dropdown.addItem(entry["name"], entry["id"])

        # Restore selection
        if current_id:
            idx = self._dropdown.findData(current_id)
            if idx >= 0:
                self._dropdown.setCurrentIndex(idx)

        self._dropdown.blockSignals(False)

    # ── Public API (same interface as old widget) ───────────────────────────

    def set_instrument_map(self, instrument_map: Dict[str, Dict]) -> None:
        self._instrument_map = instrument_map
        for table in self._tables.values():
            table.set_instrument_map(instrument_map)
        self._subscribe_all_tokens()

    def set_quote_client(self, quote_client) -> None:
        """Set broker client used for one-shot quote snapshots on newly added symbols."""
        self._quote_client = quote_client

    def add_symbol(self, symbol: str, category: str = None) -> bool:
        table = self._current_table()
        if not table:
            return False
        added = table.add_symbol(symbol)
        if added:
            self._refresh_symbol_snapshot(table, symbol)
        return added

    def add_symbol_to_watchlist_index(self, symbol: str, index: int) -> bool:
        """Add symbol to watchlist at zero-based index."""
        entries = self._config.all()
        if index < 0 or index >= len(entries):
            return False

        wl_id = entries[index].get("id")
        if not wl_id:
            return False

        table = self._tables.get(wl_id)
        if not table:
            return False

        added = table.add_symbol(symbol)
        if added:
            self._refresh_symbol_snapshot(table, symbol)
        return added

    def get_watchlist_name_by_index(self, index: int) -> Optional[str]:
        """Return watchlist name at zero-based index."""
        entries = self._config.all()
        if index < 0 or index >= len(entries):
            return None
        return entries[index].get("name")

    def get_active_watchlist_name(self) -> Optional[str]:
        """Return currently active watchlist name."""
        return self._dropdown.currentText() or None

    def add_symbol_to_active_watchlist(self, symbol: str) -> bool:
        """Add symbol to currently active watchlist."""
        return self.add_symbol(symbol)

    def remove_symbol_from_active_watchlist(self, symbol: str) -> bool:
        """Remove symbol from currently active watchlist."""
        table = self._current_table()
        if not table:
            return False
        return table.remove_symbol(symbol)

    def _refresh_symbol_snapshot(self, table: TradingTable, symbol: str) -> None:
        """
        Refresh newly added symbol with a quote snapshot so LTP/%Chg are available
        immediately even when no live ticks are flowing (e.g., market closed).
        """
        client = self._quote_client
        if not client:
            return

        inst = self._instrument_map.get(symbol, {}) or {}
        exchange = str(inst.get("exchange") or "NSE")
        instrument = f"{exchange}:{symbol}"
        try:
            quote = client.quote([instrument]).get(instrument, {}) or {}
            ohlc = quote.get("ohlc") or {}
            table.apply_symbol_snapshot(
                symbol,
                ltp=quote.get("last_price"),
                prev_close=ohlc.get("close"),
                volume=quote.get("volume"),
            )
        except Exception:
            # Non-blocking best-effort update; live ticks / startup refresh will still populate.
            return

    def get_all_tokens(self) -> List[int]:
        """Return tokens from the currently selected watchlist only."""
        table = self._current_table()
        if not table:
            return []
        return list(set(table.get_all_tokens()))

    def get_all_watchlist_tokens(self) -> List[int]:
        """Return tokens from all watchlists (for diagnostics/utilities)."""
        tokens = []
        for table in self._tables.values():
            tokens.extend(table.get_all_tokens())
        return list(set(tokens))

    @Slot(list)
    def update_data(self, ticks: List[Dict]) -> None:
        for table in self._tables.values():
            table.update_data(ticks)

    def apply_color_theme(self, theme: Dict) -> None:
        self._color_theme = dict(theme or {})
        self._apply_styles()
        for table in self._tables.values():
            table.apply_color_theme(theme)

    # ── Watchlist management ────────────────────────────────────────────────

    def _create_watchlist(self):
        dlg = _AddWatchlistDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name = dlg.name()
            entry = self._config.add(name)
            self._add_table_for_entry(entry)
            self._rebuild_dropdown()
            # Switch to new watchlist
            idx = self._dropdown.findData(entry["id"])
            if idx >= 0:
                self._dropdown.setCurrentIndex(idx)

    def _rename_watchlist(self):
        wl_id = self._current_wl_id()
        if not wl_id:
            return
        current_name = self._dropdown.currentText()
        dlg = _RenameDialog(current_name, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._config.rename(wl_id, dlg.name())
            self._rebuild_dropdown()

    def _delete_watchlist(self):
        if self._dropdown.count() <= 1:
            QMessageBox.information(self, "Cannot Delete",
                                    "You must have at least one watchlist.")
            return
        wl_id = self._current_wl_id()
        name = self._dropdown.currentText()
        if not wl_id:
            return

        reply = QMessageBox.question(
            self, "Delete Watchlist",
            f"Delete '{name}' and all its symbols?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        table = self._tables.pop(wl_id, None)
        if table:
            self._stack.removeWidget(table)
            table.deleteLater()

        self._config.remove(wl_id)
        self._rebuild_dropdown()
        self.watchlist_changed.emit()

    def _show_list_menu(self):
        menu = QMenu(self)
        menu.setObjectName("wlOptionsMenu")
        rename_act = menu.addAction("✎  Rename Watchlist")
        rename_act.triggered.connect(self._rename_watchlist)
        menu.addSeparator()
        del_act = menu.addAction("✕  Delete Watchlist")
        del_act.triggered.connect(self._delete_watchlist)
        pos = self._menu_btn.mapToGlobal(
            QPoint(0, self._menu_btn.height() + 2)
        )
        menu.exec(pos)

    # ── Slots ──────────────────────────────────────────────────────────────

    def _on_dropdown_change(self, idx: int):
        wl_id = self._dropdown.itemData(idx)
        if not wl_id:
            return
        table = self._tables.get(wl_id)
        if table:
            self._stack.setCurrentWidget(table)
            self._subscribe_all_tokens()
        self.watchlist_changed.emit()

    def _on_symbols_changed(self, wl_id: str):
        table = self._tables.get(wl_id)
        if table:
            _save_symbols(wl_id, table.get_symbol_list())
            self._subscribe_all_tokens()
        self.watchlist_changed.emit()

    def _subscribe_all_tokens(self):
        tokens = self.get_all_tokens()
        if tokens:
            self.subscribe_tokens_requested.emit(tokens)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _current_table(self) -> Optional[TradingTable]:
        w = self._stack.currentWidget()
        return w if isinstance(w, TradingTable) else None

    def _current_wl_id(self) -> Optional[str]:
        return self._dropdown.currentData()

    def eventFilter(self, obj, event):
        """Right-click on dropdown → options menu."""
        if obj is self._dropdown:
            from PySide6.QtCore import QEvent
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.RightButton:
                    self._show_list_menu()
                    return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        for wl_id, table in self._tables.items():
            _save_symbols(wl_id, table.get_symbol_list())
        super().closeEvent(event)

    # ── Styles ─────────────────────────────────────────────────────────────

    def _apply_styles(self):
        """AMOLED dark terminal styling with visible low-contrast grid lines."""
        gridline_color = _C.GRID
        dropdown_icon_path = get_asset_path("icons", "dropdown-arrow.svg", required=False)
        dropdown_icon_url = dropdown_icon_path.as_posix() if dropdown_icon_path is not None else ""
        stylesheet = f"""
            /* ── Widget shell ─────────────────────────────────────── */
            TabbedWatchlistWidget {{
                background: {_C.BG0};
                color: {_C.T0};
                font-family: {_SANS};
                font-size: 10px;
            }}

            /* ── Compact AMOLED header bar ───────────────────────── */
            QFrame#wlHeader {{
                background: {_C.BG0};
                border-bottom: 1px solid {_C.BG4};
                min-height: {CHART_TOOLBAR_HEIGHT}px;
                max-height: {CHART_TOOLBAR_HEIGHT}px;
                padding: 0px;
            }}

            QLabel#wlLabel {{
                color: {_C.AMBER};
                font-family: {_SANS};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 0.8px;
                background: transparent;
            }}

            /* ── Dropdown ─────────────────────────────────────────── */
            QComboBox#wlDropdown {{
                background: {_C.BG1};
                color: {_C.T0};
                border: 1px solid {_C.BG4};
                border-radius: 2px;
                min-height: {CHART_TOOLBAR_CONTROL_HEIGHT}px;
                max-height: {CHART_TOOLBAR_CONTROL_HEIGHT}px;
                padding: 0px 20px 0px 7px;
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 650;
                selection-background-color: {_C.SEL};
                selection-color: {_C.T0};
            }}
            QComboBox#wlDropdown:hover {{
                background: {_C.BG2};
                border-color: {_C.BG5};
            }}
            QComboBox#wlDropdown:focus {{
                background: {_C.BG2};
                border-color: {_C.CYAN};
                outline: none;
            }}
            QComboBox#wlDropdown::drop-down {{
                border: none;
                width: 18px;
                background: transparent;
            }}
            QComboBox#wlDropdown::down-arrow {{
                image: url("__DROPDOWN_ICON_URL__");
                width: 10px;
                height: 10px;
                margin-right: 4px;
            }}
            QComboBox#wlDropdown QAbstractItemView {{
                background: {_C.BG1};
                border: 1px solid {_C.BG4};
                border-radius: 2px;
                color: {_C.T0};
                selection-background-color: {_C.SEL};
                selection-color: {_C.T0};
                padding: 2px;
                outline: none;
                font-family: {_SANS};
                font-size: 10px;
            }}
            QComboBox#wlDropdown QAbstractItemView::item {{
                padding: 3px 7px;
                border: none;
                min-height: 18px;
            }}
            QComboBox#wlDropdown QAbstractItemView::item:hover {{
                background: {_C.BG3};
            }}

            /* ── Add / Menu buttons ───────────────────────────────── */
            QToolButton#wlAddBtn,
            QToolButton#wlMenuBtn {{
                background: {_C.BG1};
                color: {_C.CYAN};
                min-height: {CHART_TOOLBAR_CONTROL_HEIGHT}px;
                max-height: {CHART_TOOLBAR_CONTROL_HEIGHT}px;
                font-size: 12px;
                font-weight: 800;
                border-radius: 2px;
                border: 1px solid {_C.BG4};
                padding: 0px;
            }}
            QToolButton#wlAddBtn:hover,
            QToolButton#wlMenuBtn:hover {{
                background: rgba(0,212,255,0.08);
                border-color: rgba(0,212,255,0.34);
                color: {_C.T0};
            }}
            QToolButton#wlAddBtn:pressed,
            QToolButton#wlMenuBtn:pressed {{
                background: {_C.BG3};
                border-color: {_C.CYAN};
            }}

            /* ── Table with visible grid lines ────────────────────── */
            TradingTable {{
                background: {_C.BG1};
                alternate-background-color: {_C.BG2};
                border: none;
                gridline-color: {gridline_color};
                selection-background-color: {_C.SEL};
                selection-color: {_C.T0};
                color: {_C.T0};
                outline: none;
                show-decoration-selected: 0;
                font-size: 10px;
                font-family: {_NUM};
                border-radius: 0px;
            }}

            TradingTable::item {{
                padding: 0px 5px;
                border-bottom: 1px solid {_C.ROW_LINE};
                background: transparent;
                font-size: 10px;
                font-family: {_NUM};
                font-weight: 500;
            }}

            TradingTable::item:selected {{
                background: {_C.SEL} !important;
                color: {_C.T0};
                font-weight: 600;
                outline: none;
            }}

            TradingTable::item:focus {{
                background: {_C.SEL} !important;
                color: {_C.T0};
                outline: none;
            }}

            TradingTable::item:hover {{
                background: {_C.BG3};
            }}

            TradingTable::item:alternate {{
                background: {_C.BG2};
            }}

            TradingTable::item:alternate:selected {{
                background: {_C.SEL} !important;
                color: {_C.T0};
            }}

            /* ── Table header ─────────────────────────────────────── */
            QHeaderView::section {{
                background: {_C.BG2};
                color: {_C.T2};
                padding: 0px 5px;
                border: none;
                border-right: 1px solid {_C.BG4};
                border-bottom: 1px solid {_C.BG4};
                font-family: {_SANS};
                font-weight: 800;
                font-size: 9px;
                letter-spacing: 0.8px;
                text-transform: uppercase;
                min-height: 19px;
            }}
            QHeaderView::section:hover {{
                background: {_C.BG3};
                color: {_C.T1};
            }}
            QHeaderView {{
                background: {_C.BG2};
                border: none;
            }}
            QTableCornerButton::section {{
                background: {_C.BG2};
                border: none;
                border-right: 1px solid {_C.BG4};
                border-bottom: 1px solid {_C.BG4};
            }}

            /* ── Context menu ─────────────────────────────────────── */
            QMenu#wlCtxMenu,
            QMenu#wlOptionsMenu {{
                background: {_C.BG1};
                border: 1px solid {_C.BG4};
                border-radius: 2px;
                color: {_C.T0};
                font-family: {_SANS};
                font-size: 10px;
                padding: 4px 0px;
            }}
            QMenu#wlCtxMenu::item,
            QMenu#wlOptionsMenu::item {{
                padding: 5px 16px;
            }}
            QMenu#wlCtxMenu::item:selected,
            QMenu#wlOptionsMenu::item:selected {{
                background: {_C.SEL};
                color: {_C.T0};
            }}
            QMenu#wlCtxMenu::separator,
            QMenu#wlOptionsMenu::separator {{
                height: 1px;
                background: {_C.BG4};
                margin: 3px 8px;
            }}

            /* ── Stack ────────────────────────────────────────────── */
            QStackedWidget#wlStack {{
                background: {_C.BG1};
                border: none;
            }}

            /* ── Scrollbars ───────────────────────────────────────── */
            QScrollBar:vertical {{
                background: transparent;
                width: 4px;
                border: none;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background: {_C.BG5};
                border-radius: 2px;
                min-height: 18px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {_C.T2};
            }}
            QScrollBar:horizontal {{
                background: transparent;
                height: 4px;
                border: none;
                margin: 0px;
            }}
            QScrollBar::handle:horizontal {{
                background: {_C.BG5};
                border-radius: 2px;
                min-width: 18px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {_C.T2};
            }}
            QScrollBar::add-line,
            QScrollBar::sub-line {{
                border: none;
                background: none;
                width: 0px;
                height: 0px;
                margin: 0px;
            }}

            QToolTip {{
                background-color: {_C.BG2};
                color: {_C.T1};
                border: 1px solid {_C.BG5};
                border-radius: 2px;
                padding: 4px 6px;
                font-family: {_SANS};
                font-size: 10px;
            }}
        """
        self.setStyleSheet(
            stylesheet
            .replace("__DROPDOWN_ICON_URL__", dropdown_icon_url)
        )