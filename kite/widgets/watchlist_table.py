# kite/widgets/watchlist_table.py
"""
Institutional Watchlist — TC2000-grade, production-ready.

Features
────────
  • Unlimited user-named watchlists (create / rename / delete)
  • ⚑ Flag column (20 px) — 2 states: none ↔ green
    Flags are per-symbol and persist globally across all watchlists.
  • Heat-map % change coloring (gradient magnitude, not binary red/green)
  • Full TC2000 color system (consistency_rules palette, zero deviation)
  • Monospace numerics — columns never shift during live updates
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

from PySide6.QtCore import Qt, Signal, Slot, QPoint, QTimer, QSize
from PySide6.QtGui import (
    QColor, QFont, QBrush, QCursor, QAction, QFontMetrics, QMouseEvent, QIcon
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QPushButton, QToolButton, QComboBox, QStackedWidget, QMenu,
    QDialog, QLineEdit, QDialogButtonBox, QMessageBox, QApplication
)
from app_paths import get_asset_path

logger = logging.getLogger(__name__)

CHART_TOOLBAR_HEIGHT = 32
CHART_TOOLBAR_CONTROL_HEIGHT = 22


# ─────────────────────────────────────────────────────────────────────────────
#  DESIGN TOKENS  (strict consistency_rules palette — zero deviation)
# ─────────────────────────────────────────────────────────────────────────────

class _C:
    BG0 = "#050709"  # app shell
    BG1 = "#0a0d12"  # primary panels
    BG2 = "#0f1318"  # table rows / input bg
    BG3 = "#141920"  # hover / selected elevated
    BG4 = "#1a2030"  # borders / subtle dividers

    BULL = "#00d4a8"  # teal-green — TC2000 signature
    BULL_DIM = "#1a7a62"
    BULL_BG = "rgba(0,212,168,0.07)"

    BEAR = "#ff4d6a"  # warm crimson
    BEAR_DIM = "#7a2030"
    BEAR_BG = "rgba(255,77,106,0.07)"

    NEUTRAL = "#7a94b0"
    NEU_DIM = "#3a4d60"

    T0 = "#e8f0ff"  # primary — prices, symbols
    T1 = "#a8bcd4"  # secondary — headers, labels
    T2 = "#5a7090"  # tertiary — muted metadata
    T3 = "#2a3a50"  # disabled / placeholder

    CYAN = "#00d4ff"  # selected / focus rings
    AMBER = "#f59e0b"  # alerts / warnings
    BLUE = "#3b82f6"  # informational
    SEL = "#1a2840"  # selected row

    # Flag color (single-state)
    FLAG_GREEN = "#00d4a8"

    # Heat-map change % bands
    @staticmethod
    def change_color(pct: float) -> Tuple[str, str]:
        """Return (fg_color, bg_rgba) for a % change value."""
        if pct >= 3.0:
            return "#00d4a8", "rgba(0,212,168,0.12)"
        if pct >= 1.0:
            return "#22c4a0", "rgba(34,196,160,0.07)"
        if pct >= -0.5:
            return "#7a94b0", ""
        if pct >= -1.0:
            return "#e87060", "rgba(232,112,96,0.07)"
        return "#ff4d6a", "rgba(255,77,106,0.12)"


_MONO = "Consolas, 'JetBrains Mono', 'Courier New', monospace"
_SANS = "'Segoe UI', -apple-system, Roboto, Arial, sans-serif"

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

_APP_DIR = os.path.join(os.path.expanduser("~"), ".qullamaggie")
_DATA_DIR = "kite/user_data"
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
                font-family: {_MONO};
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 1.2px;
                background: transparent;
            }}
            QFrame#nameDialogBody {{ background: {_C.BG1}; }}
            QLabel#fieldLabel {{
                color: {_C.T2};
                font-family: {_SANS};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 1px;
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
                background: rgba(255,77,106,0.15);
                color: {_C.BEAR};
            }}
            QPushButton#secondaryButton, QPushButton#infoButton {{
                border-radius: 2px;
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 800;
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
                background: rgba(0,212,255,0.08);
                color: {_C.CYAN};
                border: 1px solid rgba(0,212,255,0.28);
            }}
            QPushButton#infoButton:hover {{
                background: rgba(0,212,255,0.16);
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
                font-family: {_MONO};
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 1.2px;
                background: transparent;
            }}
            QFrame#nameDialogBody {{ background: {_C.BG1}; }}
            QLabel#fieldLabel {{
                color: {_C.T2};
                font-family: {_SANS};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 1px;
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
                background: rgba(255,77,106,0.15);
                color: {_C.BEAR};
            }}
            QPushButton#secondaryButton, QPushButton#confirmButton {{
                border-radius: 2px;
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 800;
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
                background: rgba(0,212,168,0.10);
                color: {_C.BULL};
                border: 1px solid rgba(0,212,168,0.35);
            }}
            QPushButton#confirmButton:hover {{
                background: rgba(0,212,168,0.18);
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
    All numerics in monospace. Heat-map on %Chg.
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

        self._color_theme: Dict = {}
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
        hdr.setFixedHeight(21)

        # Flag col — fixed tight
        hdr.setSectionResizeMode(_COL_FLAG, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(_COL_FLAG, 20)

        # Symbol — stretches
        hdr.setSectionResizeMode(_COL_SYMBOL, QHeaderView.ResizeMode.Stretch)

        # Data cols — fit content
        for col in (_COL_LTP, _COL_VOL, _COL_CHG):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(21)
        self.verticalHeader().setMinimumSectionSize(21)

        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSortingEnabled(False)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setWordWrap(False)

        hdr.sectionClicked.connect(self._on_header_click)
        hdr.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setColumnHidden(_COL_VOL, not bool(self._color_theme.get("show_watchlist_volume_column", True)))

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
        self.setColumnHidden(_COL_VOL, not bool(self._color_theme.get("show_watchlist_volume_column", True)))
        for sym, row in self._symbol_to_row.items():
            data = self._watchlist_data.get(sym)
            if data:
                self._update_row(row, data)

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
                self.item(row, _COL_SYMBOL).setText(sym)

    def _update_row(self, row: int, data: Dict):
        if row >= self.rowCount():
            return

        sym = data.get("tradingsymbol", "")
        ltp = data.get("ltp", 0.0)
        vol = data.get("volume", 0)
        chg = data.get("change_pct", 0.0)

        # ── Flag ──
        self._paint_flag_cell(row, sym)

        symbol_font = QFont("Segoe UI")
        symbol_font.setPointSize(9)
        symbol_font.setBold(True)
        value_font = self._mono_font(False)
        value_font.setPointSize(9)
        strong_value_font = self._mono_font(True)
        strong_value_font.setPointSize(9)

        # ── Symbol ──
        sym_item = self.item(row, _COL_SYMBOL)
        if sym_item:
            sym_item.setText(sym)
            sym_item.setForeground(QColor(_C.T0))
            sym_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            sym_item.setFont(symbol_font)

        # ── LTP ──
        ltp_text = f"{ltp:.2f}" if ltp > 0 else "—"
        ltp_item = self.item(row, _COL_LTP)
        if ltp_item:
            ltp_item.setText(ltp_text)
            ltp_item.setForeground(QColor(_C.T0))
            ltp_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            ltp_item.setFont(value_font)

        # ── Volume ──
        vol_text = self._fmt_volume(vol)
        vol_item = self.item(row, _COL_VOL)
        if vol_item:
            vol_item.setText(vol_text)
            vol_item.setForeground(QColor(_C.T2))
            vol_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            vol_item.setToolTip(f"Volume: {vol:,}")
            vol_item.setFont(value_font)

        # ── Chg% with heat-map ──
        chg_text = f"{chg:+.2f}" if abs(chg) > 0.005 else "0.00"
        fg, bg_rgba = _C.change_color(chg)
        chg_item = self.item(row, _COL_CHG)
        if chg_item:
            chg_item.setText(chg_text)
            chg_item.setForeground(QColor(fg))
            chg_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
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
        f.setPointSize(9)
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
    def _mono_font(bold: bool = False) -> QFont:
        f = QFont("Consolas")
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPointSize(10)
        f.setBold(bold)
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
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(_COL_FLAG, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(_COL_FLAG, 20)


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
        self._tables: Dict[str, TradingTable] = {}  # id → table
        self._config = _WatchlistConfig()

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

        # Static label
        lbl = QLabel("WATCHLIST")
        lbl.setObjectName("wlLabel")
        lbl.setFixedWidth(72)
        h.addWidget(lbl)

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

    def add_symbol(self, symbol: str, category: str = None) -> bool:
        table = self._current_table()
        if not table:
            return False
        return table.add_symbol(symbol)

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

        return table.add_symbol(symbol)

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
        dropdown_icon_path = get_asset_path("icons", "dropdown-arrow.svg", required=False)
        dropdown_icon_url = dropdown_icon_path.as_posix() if dropdown_icon_path is not None else ""
        stylesheet = """
            /* ── Widget shell ─────────────────────────────────────── */
            TabbedWatchlistWidget {
                background: #050709;
                color: #e8f0ff;
                font-family: "Segoe UI", -apple-system, Roboto, Arial, sans-serif;
                font-size: 11px;
            }

            /* ── Header bar ───────────────────────────────────────── */
            QFrame#wlHeader {
                background: #070a0f;
                border-bottom: 1px solid #1a2030;
                min-height: 32px;
                max-height: 32px;
                padding: 0;
            }

            QLabel#wlLabel {
                color: #f59e0b;
                font-family: "Consolas", "JetBrains Mono", monospace;
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 1.4px;
                background: transparent;
            }

            /* ── Dropdown ─────────────────────────────────────────── */
            QComboBox#wlDropdown {
                background: #0f1318;
                color: #e8f0ff;
                border: 1px solid #1a2030;
                border-radius: 2px;
                min-height: 22px;
                max-height: 22px;
                padding: 0 22px 0 7px;
                font-family: "Segoe UI", -apple-system, Roboto, Arial, sans-serif;
                font-size: 10px;
                font-weight: 700;
            }
            QComboBox#wlDropdown:hover {
                background: #141920;
                border-color: #243040;
            }
            QComboBox#wlDropdown:focus {
                border-color: #00d4ff;
                outline: none;
            }
            QComboBox#wlDropdown::drop-down {
                border: none;
                width: 18px;
                background: transparent;
            }
            QComboBox#wlDropdown::down-arrow {
                image: url("__DROPDOWN_ICON_URL__");
                width: 10px;
                height: 10px;
                margin-right: 4px;
            }
            QComboBox#wlDropdown QAbstractItemView {
                background: #0a0d12;
                border: 1px solid #1a2030;
                border-radius: 2px;
                color: #e8f0ff;
                selection-background-color: #1a2840;
                selection-color: #e8f0ff;
                padding: 2px;
                outline: none;
                font-size: 10px;
            }
            QComboBox#wlDropdown QAbstractItemView::item {
                padding: 4px 7px;
                border: none;
                min-height: 18px;
            }
            QComboBox#wlDropdown QAbstractItemView::item:hover {
                background: #141920;
            }

            /* ── Add / Menu buttons ───────────────────────────────── */
            QToolButton#wlAddBtn, QToolButton#wlMenuBtn {
                background: #0f1318;
                color: #00d4ff;
                min-height: 22px;
                max-height: 22px;
                font-size: 12px;
                font-weight: 800;
                border-radius: 2px;
                border: 1px solid #1a2030;
                padding: 0;
            }
            QToolButton#wlAddBtn:hover, QToolButton#wlMenuBtn:hover {
                background: rgba(0,212,255,0.10);
                border-color: rgba(0,212,255,0.35);
                color: #b7f4ff;
            }
            QToolButton#wlAddBtn:pressed, QToolButton#wlMenuBtn:pressed {
                background: #050709;
                border-color: #00d4ff;
            }

            /* ── Table ────────────────────────────────────────────── */
            TradingTable {
                background: #0a0d12;
                alternate-background-color: #0f1318;
                border: none;
                gridline-color: transparent;
                selection-background-color: #1a2840;
                color: #e8f0ff;
                outline: none;
                show-decoration-selected: 0;
                font-size: 11px;
                border-radius: 0;
            }

            TradingTable::item {
                padding: 0 5px;
                border-bottom: 1px solid #141920;
                background: transparent;
                font-size: 11px;
                font-family: "Consolas", "JetBrains Mono", monospace;
            }

            TradingTable::item:selected {
                background: #1a2840 !important;
                color: #e8f0ff;
                outline: none;
            }

            TradingTable::item:focus {
                background: #1a2840 !important;
                outline: none;
            }

            TradingTable::item:hover {
                background: #141920;
            }

            TradingTable::item:alternate {
                background: #0f1318;
            }

            TradingTable::item:alternate:selected {
                background: #1a2840 !important;
                color: #e8f0ff;
            }

            /* ── Table header ─────────────────────────────────────── */
            QHeaderView::section {
                background: #0f1318;
                color: #5a7090;
                padding: 0 5px;
                border: none;
                border-bottom: 1px solid #1a2030;
                font-family: "Segoe UI", -apple-system, Roboto, Arial, sans-serif;
                font-weight: 800;
                font-size: 8px;
                letter-spacing: 1px;
                text-transform: uppercase;
            }
            QHeaderView::section:hover {
                background: #141920;
                color: #a8bcd4;
            }
            QHeaderView {
                background: #0f1318;
                border: none;
            }

            /* ── Context menu ─────────────────────────────────────── */
            QMenu#wlCtxMenu, QMenu#wlOptionsMenu {
                background: #0a0d12;
                border: 1px solid #1a2030;
                border-radius: 2px;
                color: #e8f0ff;
                font-family: "Segoe UI", -apple-system, Roboto, Arial, sans-serif;
                font-size: 11px;
                padding: 4px 0;
            }
            QMenu#wlCtxMenu::item, QMenu#wlOptionsMenu::item {
                padding: 5px 16px;
            }
            QMenu#wlCtxMenu::item:selected,
            QMenu#wlOptionsMenu::item:selected {
                background: #1a2840;
                color: #e8f0ff;
            }
            QMenu#wlCtxMenu::separator, QMenu#wlOptionsMenu::separator {
                height: 1px;
                background: #1a2030;
                margin: 3px 8px;
            }

            /* ── Stack ────────────────────────────────────────────── */
            QStackedWidget#wlStack {
                background: #0a0d12;
                border: none;
            }

            /* ── Scrollbars ───────────────────────────────────────── */
            QScrollBar:vertical {
                background: transparent;
                width: 4px;
                border: none;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #243040;
                border-radius: 2px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: #5a7090;
            }
            QScrollBar:horizontal {
                background: transparent;
                height: 4px;
                border: none;
                margin: 0;
            }
            QScrollBar::handle:horizontal {
                background: #243040;
                border-radius: 2px;
                min-width: 20px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #5a7090;
            }
            QScrollBar::add-line, QScrollBar::sub-line {
                border: none;
                background: none;
                width: 0;
                height: 0;
                margin: 0;
            }
        """
        self.setStyleSheet(stylesheet.replace("__DROPDOWN_ICON_URL__", dropdown_icon_url))
