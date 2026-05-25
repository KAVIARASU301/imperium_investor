# kite/widgets/floating_watchlist_dialog.py
"""
FloatingWatchlistDialog — TC2000-style always-on-top floating watchlist.

Architecture
────────────
  • Shares EXACTLY the same underlying data as the embedded TabbedWatchlistWidget.
    The main window feeds live ticks to BOTH the embedded table and this dialog
    via a single _on_market_data → update_data() call chain.
  • Watchlist symbol/data is pushed in via update_watchlist_data(); the dialog
    is purely a view — it never owns the data.
  • Per-watchlist tab switching: the floating dialog mirrors whatever watchlist
    list the user selects inside it (independent selection from embedded widget).
  • Flag column (18 px) — shared _flag_store singleton.
  • Heat-map Chg% coloring (same bands as TradingTable).
  • Modern UI typography for symbols, LTP, volume, change %, counts and dropdowns.
  • Throttled redraws at ~4 fps (225 ms timer) to keep UI readable.
  • Frameless, draggable, always-on-top.
  • Resize grip (bottom-right corner).
  • Context menu: chart, buy, sell, bracket, remove, flag.
  • Keyboard: Space = next symbol → chart; ↑↓ = navigate.

Public API
──────────
    dialog = FloatingWatchlistDialog(parent=main_window)
    dialog.show()

    # Push available watchlists metadata (list of {id, name, symbols}):
    dialog.set_watchlists(watchlists_meta)

    # Feed live ticks (same dict list as main data path):
    dialog.update_data(ticks)

    # Notify when a watchlist's symbol list changed externally:
    dialog.refresh_watchlist(wl_id)

Signals
───────
    symbol_chart_requested(str)          — open symbol in chart
    advanced_buy_order_requested(str)
    advanced_sell_order_requested(str)
    bracket_order_requested(str)
    symbol_removed_from_watchlist(str, str)  — (wl_id, symbol) remove request
"""

from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import (
    Qt, Signal, Slot, QTimer, QPoint, QRect, QSize
)
from PySide6.QtGui import (
    QColor, QFont, QBrush, QCursor, QMouseEvent, QKeyEvent, QPainter, QPen
)
from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QToolButton,
    QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMenu,
    QComboBox, QDialog
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  DESIGN TOKENS  (strictly mirrors consistency_rules palette)
# ─────────────────────────────────────────────────────────────────────────────

class _C:
    # Matte dark terminal layers
    BG0    = "#050709"    # AMOLED outer shell
    BG1    = "#0a0d12"    # dialog/window body
    BG2    = "#0f1318"    # table alternate rows / panels
    BG3    = "#141920"    # hover / inner section
    BGTB   = "#070a0f"    # title bar / footer

    BORDER  = "#1a2030"
    BORDER2 = "#2a3a50"

    # Market semantics
    BULL     = "#00d4a8"
    BULL_DIM = "#3f917f"
    BULL_BG  = "#08211b"
    BEAR     = "#ff4d6a"
    BEAR_DIM = "#94424b"
    BEAR_BG  = "#250d13"
    FLAT     = "#7f90a3"

    # Text hierarchy
    T0 = "#e8f0ff"
    T1 = "#a8bcd4"
    T2 = "#5a7090"
    T3 = "#2a3a50"
    TSYM = "#c2ccd9"   # softened symbol text; avoids distracting bright white

    # Accents
    CYAN  = "#00d4ff"
    AMBER = "#f59e0b"
    BLUE  = "#6f8fc8"
    SEL   = "#1a2840"

    # Flag palette (4-state cycle)
    FLAG_GREEN = "#00d4a8"
    FLAG_AMBER = "#f59e0b"
    FLAG_RED   = "#ff4d6a"

    @staticmethod
    def change_color(pct: float) -> Tuple[str, str]:
        """Return (fg_color, bg_hex_or_empty) for a % change value."""
        if pct >= 3.0:
            return "#00d4a8", "#08251e"
        if pct >= 1.0:
            return "#2ad9b4", "#071c18"
        if pct >= -0.5:
            return "#7f90a3", ""
        if pct >= -1.0:
            return "#f0838f", "#201015"
        return "#ff4d6a", _C.BEAR_BG


_FONT_FAMILIES = ["Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", "Noto Sans"]
_MONO = "Consolas, 'JetBrains Mono', 'Courier New', monospace"  # reserved for raw logs / IDs / debug text
_SANS = "'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', sans-serif"
_NUM = "'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', sans-serif"
_UI_FONT = "Inter"
_NUM_FONT = "Inter"


def _apply_font_families(font: QFont) -> QFont:
    """Use the same real Qt font fallback order as the embedded tables."""
    if hasattr(font, "setFamilies"):
        font.setFamilies(_FONT_FAMILIES)
    return font

# ─────────────────────────────────────────────────────────────────────────────
#  FLAG STORE — reuse the module-level singleton from watchlist_table
# ─────────────────────────────────────────────────────────────────────────────

def _get_flag_store():
    """Lazily import and return the shared _flag_store singleton."""
    try:
        from ibkr.widgets.watchlist_table import _flag_store
        return _flag_store
    except Exception:
        return None


_FLAG_CYCLE = [None, "green", "amber", "red"]
_FLAG_DISPLAY = {
    None:    ("",  _C.T3),
    "green": ("⚑", _C.FLAG_GREEN),
    "amber": ("⚑", _C.FLAG_AMBER),
    "red":   ("⚑", _C.FLAG_RED),
}
_FLAG_TOOLTIP = {
    None:    "Click to flag",
    "green": "Watching — click to upgrade",
    "amber": "Interested — click to upgrade",
    "red":   "High priority — click to clear",
}

# ─────────────────────────────────────────────────────────────────────────────
#  COLUMN CONFIG
# ─────────────────────────────────────────────────────────────────────────────

_COL_FLAG   = 0
_COL_SYMBOL = 1
_COL_LTP    = 2
_COL_VOL    = 3
_COL_CHG    = 4
_NUM_COLS   = 5

_HEADERS = ["", "Symbol", "LTP", "Vol", "Chg%"]

_REDRAW_INTERVAL = 225   # ms — ~4.4 fps, human-readable under live data

# ─────────────────────────────────────────────────────────────────────────────
#  RESIZE GRIP (bottom-right corner)
# ─────────────────────────────────────────────────────────────────────────────

class _ResizeGrip(QWidget):
    """Minimal 12×12 px drag handle drawn with two diagonal lines."""

    SIZE = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
        self._dragging   = False
        self._start_pos  = QPoint()
        self._start_geo  = QRect()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(_C.BORDER2))
        pen.setWidth(1)
        p.setPen(pen)
        n = self.SIZE
        for i in range(2, n, 4):
            p.drawLine(i, n - 1, n - 1, i)
        p.end()

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging  = True
            self._start_pos = e.globalPosition().toPoint()
            self._start_geo = self.window().geometry()

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._dragging:
            delta  = e.globalPosition().toPoint() - self._start_pos
            geo    = self._start_geo
            new_w  = max(340, geo.width()  + delta.x())
            new_h  = max(200, geo.height() + delta.y())
            self.window().setGeometry(geo.x(), geo.y(), new_w, new_h)

    def mouseReleaseEvent(self, _):
        self._dragging = False


# ─────────────────────────────────────────────────────────────────────────────
#  FLOATING WATCHLIST DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class FloatingWatchlistDialog(QDialog):
    _STATE_KEY = "floating_watchlist_dialog_geometry"
    """
    Always-on-top floating watchlist that mirrors the embedded TabbedWatchlistWidget.

    It does NOT own the canonical symbol list — that lives in TabbedWatchlistWidget.
    Main window pushes data into it; this dialog just renders and navigates.
    """

    # ── Public signals ──────────────────────────────────────────────────────
    symbol_chart_requested          = Signal(str)
    advanced_buy_order_requested    = Signal(str)
    advanced_sell_order_requested   = Signal(str)
    bracket_order_requested         = Signal(str)
    symbol_removed_from_watchlist   = Signal(str, str)   # (wl_id, symbol)

    def __init__(self, parent=None):
        flags = (
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumSize(320, 220)
        self.resize(360, 500)

        # ── State ────────────────────────────────────────────────────────────
        # watchlists_meta: list of {"id": str, "name": str}
        self._watchlists_meta: List[Dict] = []
        # per-watchlist data: {wl_id: {symbol: {ltp, volume, prev_close, change_pct}}}
        self._watchlist_data: Dict[str, Dict[str, Dict]] = {}
        # per-watchlist symbols (ordered): {wl_id: [symbol, ...]}
        self._watchlist_symbols: Dict[str, List[str]] = {}
        # per-watchlist token→symbol maps: {wl_id: {token: symbol}}
        self._token_to_symbol: Dict[str, Dict[int, str]] = {}
        # symbol→row for the currently displayed watchlist
        self._symbol_to_row: Dict[str, int] = {}
        self._active_wl_id: Optional[str] = None

        # Pending tick buffer (token → ltp), flushed by redraw timer
        self._pending_ticks: Dict[int, float] = {}
        self._dirty_symbols: set = set()

        self._dragging = False
        self._drag_offset = QPoint()
        self._nav_idx  = 0
        self._sort_col = _COL_SYMBOL
        self._sort_asc = True
        self._selected_rows: set[int] = set()

        # ── Build ────────────────────────────────────────────────────────────
        self._build_ui()
        self._apply_styles()
        self._restore_geometry()

        # ── Timers ───────────────────────────────────────────────────────────
        self._redraw_timer = QTimer(self)
        self._redraw_timer.timeout.connect(self._flush_pending_ticks)
        self._redraw_timer.start(_REDRAW_INTERVAL)

    # ═══════════════════════════════════════════════════════════════════════
    # UI CONSTRUCTION
    # ═══════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        root.addWidget(self._build_title_bar())
        root.addWidget(self._build_watchlist_selector())
        self.table = self._build_table()
        root.addWidget(self.table, 1)
        root.addWidget(self._build_footer())

        # Resize grip
        grip = _ResizeGrip(self)
        grip.move(self.width() - _ResizeGrip.SIZE, self.height() - _ResizeGrip.SIZE)
        self._grip = grip

    def _build_title_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("floatWlTitleBar")
        bar.setFixedHeight(26)
        bar.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))

        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 0, 5, 0)
        h.setSpacing(5)

        self._dot = QLabel("●")
        self._dot.setObjectName("floatWlDot")
        self._dot.setFixedWidth(10)

        title = QLabel("WATCHLIST")
        title.setObjectName("floatWlTitle")

        self._count_badge = QLabel("0")
        self._count_badge.setObjectName("floatWlBadge")
        self._count_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._count_badge.setFixedHeight(16)

        h.addWidget(self._dot)
        h.addWidget(title)
        h.addWidget(self._count_badge)
        h.addStretch()

        min_btn = QToolButton()
        min_btn.setObjectName("floatWlBarBtn")
        min_btn.setText("—")
        min_btn.setFixedSize(20, 18)
        min_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        min_btn.clicked.connect(self.showMinimized)

        close_btn = QToolButton()
        close_btn.setObjectName("floatWlCloseBtn")
        close_btn.setText("✕")
        close_btn.setFixedSize(20, 18)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self.hide)

        h.addWidget(min_btn)
        h.addWidget(close_btn)

        bar.mousePressEvent   = self._tb_press
        bar.mouseMoveEvent    = self._tb_move
        bar.mouseReleaseEvent = self._tb_release
        return bar

    def _build_watchlist_selector(self) -> QFrame:
        """Compact bar with watchlist dropdown."""
        bar = QFrame()
        bar.setObjectName("floatWlSelectorBar")
        bar.setFixedHeight(24)

        h = QHBoxLayout(bar)
        h.setContentsMargins(7, 0, 7, 0)
        h.setSpacing(5)

        lbl = QLabel("LIST")
        lbl.setObjectName("floatWlSelectorLabel")
        lbl.setFixedWidth(30)
        h.addWidget(lbl)

        self._wl_dropdown = QComboBox()
        self._wl_dropdown.setObjectName("floatWlDropdown")
        self._wl_dropdown.setMinimumHeight(18)
        self._wl_dropdown.setMaximumHeight(18)
        self._wl_dropdown.currentIndexChanged.connect(self._on_dropdown_change)
        h.addWidget(self._wl_dropdown, 1)

        return bar

    def _build_table(self) -> QTableWidget:
        t = QTableWidget(0, _NUM_COLS)
        t.setObjectName("floatWlTable")
        t.setHorizontalHeaderLabels(_HEADERS)

        hdr = t.horizontalHeader()
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.setMinimumSectionSize(18)
        hdr.setHighlightSections(False)
        hdr.setFixedHeight(20)

        # Flag: fixed narrow
        hdr.setSectionResizeMode(_COL_FLAG,   QHeaderView.ResizeMode.Fixed)
        t.setColumnWidth(_COL_FLAG, 18)
        # Symbol: stretches
        hdr.setSectionResizeMode(_COL_SYMBOL, QHeaderView.ResizeMode.Stretch)
        # Data: fixed compact columns, visually aligned with embedded watchlist/positions.
        for col in (_COL_LTP, _COL_VOL, _COL_CHG):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        t.setColumnWidth(_COL_LTP, 70)
        t.setColumnWidth(_COL_VOL, 58)
        t.setColumnWidth(_COL_CHG, 64)

        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(21)
        t.verticalHeader().setMinimumSectionSize(21)

        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setShowGrid(True)
        t.setAlternatingRowColors(True)
        t.setCornerButtonEnabled(False)
        t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        t.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        t.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        t.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        t.setSortingEnabled(False)

        t.cellClicked.connect(self._on_cell_click)
        t.cellDoubleClicked.connect(self._on_cell_double_click)
        t.itemSelectionChanged.connect(self._on_selection_changed)
        t.customContextMenuRequested.connect(self._show_ctx_menu)
        t.horizontalHeader().sectionClicked.connect(self._on_header_click)

        return t

    def _build_footer(self) -> QFrame:
        f = QFrame()
        f.setObjectName("floatWlFooter")
        f.setFixedHeight(23)

        h = QHBoxLayout(f)
        h.setContentsMargins(8, 0, 8, 0)
        h.setSpacing(10)

        self._sym_count_lbl = QLabel("0 symbols")
        self._sym_count_lbl.setObjectName("floatWlFooterLabel")
        h.addWidget(self._sym_count_lbl)
        h.addStretch()

        # Keyboard hint
        hint = QLabel("Space: chart ↑↓: nav")
        hint.setObjectName("floatWlFooterHint")
        h.addWidget(hint)
        return f


    def _restore_geometry(self):
        cfg = getattr(self.parent(), "config_manager", None)
        if not cfg:
            return
        try:
            raw = cfg.load_dialog_state(self._STATE_KEY)
            if not raw:
                return
            data = json.loads(raw)
            width = int(data.get("w", self.width()))
            height = int(data.get("h", self.height()))
            self.resize(max(self.minimumWidth(), width), max(self.minimumHeight(), height))
            if "x" in data and "y" in data:
                self.move(int(data["x"]), int(data["y"]))
        except Exception as exc:
            logger.debug("Failed to restore floating watchlist geometry: %s", exc)

    def _save_geometry(self):
        cfg = getattr(self.parent(), "config_manager", None)
        if not cfg:
            return
        try:
            payload = json.dumps({
                "x": int(self.x()),
                "y": int(self.y()),
                "w": int(self.width()),
                "h": int(self.height()),
            })
            cfg.save_dialog_state(self._STATE_KEY, payload)
        except Exception as exc:
            logger.debug("Failed to save floating watchlist geometry: %s", exc)

    # ═══════════════════════════════════════════════════════════════════════
    # TITLE BAR DRAG
    # ═══════════════════════════════════════════════════════════════════════

    def _tb_press(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging    = True
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _tb_move(self, e: QMouseEvent):
        if self._dragging and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_offset)

    def _tb_release(self, _):
        self._dragging = False

    # ═══════════════════════════════════════════════════════════════════════
    # RESIZE
    # ═══════════════════════════════════════════════════════════════════════

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if hasattr(self, "_grip"):
            self._grip.move(
                self.width()  - _ResizeGrip.SIZE,
                self.height() - _ResizeGrip.SIZE,
            )

    def hideEvent(self, event):
        self._save_geometry()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._save_geometry()
        super().closeEvent(event)

    # ═══════════════════════════════════════════════════════════════════════
    # PUBLIC: DATA FEED
    # ═══════════════════════════════════════════════════════════════════════

    def set_watchlists(self, watchlists_meta: List[Dict]) -> None:
        """
        Call this whenever the list of available watchlists changes.
        watchlists_meta: [{"id": str, "name": str, "symbols": [...], "data": {...}}]
        Each entry may optionally carry "symbols" (list[str]) and "data" (dict).
        """
        self._watchlists_meta = watchlists_meta

        prev_id = self._active_wl_id

        self._wl_dropdown.blockSignals(True)
        self._wl_dropdown.clear()
        for wl in watchlists_meta:
            self._wl_dropdown.addItem(wl["name"], wl["id"])
            wl_id = wl["id"]
            # Absorb symbol list if provided
            if "symbols" in wl:
                self._watchlist_symbols[wl_id] = list(wl["symbols"])
            # Absorb data dict if provided
            if "data" in wl:
                self._watchlist_data.setdefault(wl_id, {}).update(wl["data"])
            # Build token map if instrument_map provided
            if "instrument_map" in wl:
                self._rebuild_token_map(wl_id, wl["instrument_map"])
        self._wl_dropdown.blockSignals(False)

        # Restore previous selection or default to first
        if prev_id:
            idx = self._wl_dropdown.findData(prev_id)
            if idx >= 0:
                self._wl_dropdown.setCurrentIndex(idx)
                self._active_wl_id = prev_id
                self._repopulate()
                return

        if self._wl_dropdown.count() > 0:
            self._wl_dropdown.setCurrentIndex(0)
            self._active_wl_id = self._wl_dropdown.itemData(0)
            self._repopulate()

    def push_watchlist_data(
        self,
        wl_id:      str,
        symbols:    List[str],
        data:       Dict[str, Dict],
        token_map:  Optional[Dict[int, str]] = None,
    ) -> None:
        """
        Push a full data snapshot for one watchlist.

        symbols:   ordered list of tradingsymbols
        data:      {symbol: {ltp, volume, prev_close, change_pct, ...}}
        token_map: {instrument_token: symbol}  — pass to keep tick routing current
        """
        self._watchlist_symbols[wl_id] = list(symbols)
        self._watchlist_data[wl_id]    = dict(data)
        if token_map:
            self._token_to_symbol[wl_id] = dict(token_map)
        if wl_id == self._active_wl_id:
            self._repopulate()

    def refresh_watchlist(self, wl_id: str) -> None:
        """Call after external symbol add/remove to refresh visible rows."""
        if wl_id == self._active_wl_id:
            self._repopulate()

    @Slot(list)
    def update_data(self, ticks: List[Dict]) -> None:
        """
        Receive the same tick list as the embedded watchlist.
        Buffers ticks; flushed at _REDRAW_INTERVAL.
        """
        if not self.isVisible():
            return

        if not ticks or not self._active_wl_id:
            return

        tok_map = self._token_to_symbol.get(self._active_wl_id, {})
        wl_data = self._watchlist_data.get(self._active_wl_id, {})

        for tick in ticks:
            raw = tick.get("instrument_token")
            if raw is None:
                continue
            token = int(raw)
            sym   = tok_map.get(token)
            if not sym or sym not in wl_data:
                continue

            rec = wl_data[sym]

            ltp = tick.get("last_price")
            if ltp is not None:
                rec["ltp"] = float(ltp)

            for vf in ("volume_traded", "volume"):
                vol = tick.get(vf)
                if vol is not None:
                    try:
                        v = int(vol)
                        if v > 0:
                            rec["volume"] = v
                            break
                    except (TypeError, ValueError):
                        pass

            ohlc = tick.get("ohlc")
            if isinstance(ohlc, dict):
                close = ohlc.get("close")
                if close:
                    rec["prev_close"] = float(close)

            prev  = rec.get("prev_close", 0.0)
            cur   = rec.get("ltp", 0.0)
            if prev > 0 and cur > 0:
                rec["change_pct"] = (cur - prev) / prev * 100

            if sym in self._symbol_to_row:
                self._dirty_symbols.add(sym)

    # ═══════════════════════════════════════════════════════════════════════
    # INTERNAL: RENDERING
    # ═══════════════════════════════════════════════════════════════════════

    def _on_dropdown_change(self, idx: int):
        wl_id = self._wl_dropdown.itemData(idx)
        if wl_id and wl_id != self._active_wl_id:
            self._active_wl_id = wl_id
            self._repopulate()

    def _repopulate(self):
        """Rebuild the table from scratch for the active watchlist."""
        wl_id   = self._active_wl_id
        symbols = self._watchlist_symbols.get(wl_id, [])
        data    = self._watchlist_data.get(wl_id, {})

        # Apply current sort
        sorted_syms = self._sort_symbols(symbols, data)

        self.table.setRowCount(len(sorted_syms))
        self._symbol_to_row = {}
        self._dirty_symbols.clear()

        for row, sym in enumerate(sorted_syms):
            self._symbol_to_row[sym] = row
            rec = data.get(sym, {})
            self._write_row(row, sym, rec)

        count = len(sorted_syms)
        self._sym_count_lbl.setText(f"{count} symbols")
        self._count_badge.setText(str(count) if count < 1000 else "999+")
        self._dot.setStyleSheet(
            f"color: {_C.BULL if sorted_syms else _C.T3}; background: transparent;"
        )

    @staticmethod
    def _ui_font(pixel_size: int = 10, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
        """Modern UI font for labels and compact utility glyphs."""
        f = _apply_font_families(QFont(_UI_FONT))
        f.setStyleHint(QFont.StyleHint.SansSerif)
        f.setPixelSize(pixel_size)
        f.setWeight(weight)
        f.setKerning(True)
        return f

    @staticmethod
    def _symbol_font(pixel_size: int = 10, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
        """Compact ticker font matching the embedded watchlist/scanner tables."""
        f = _apply_font_families(QFont(_UI_FONT))
        f.setStyleHint(QFont.StyleHint.SansSerif)
        f.setPixelSize(pixel_size)
        f.setWeight(weight)
        f.setKerning(True)
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 103)
        return f

    @staticmethod
    def _number_font(pixel_size: int = 10, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
        """Modern UI number font for LTP, volume and percentage values."""
        f = _apply_font_families(QFont(_NUM_FONT))
        f.setStyleHint(QFont.StyleHint.SansSerif)
        f.setPixelSize(pixel_size)
        f.setWeight(weight)
        f.setKerning(True)
        return f

    def _is_row_selected(self, row: int) -> bool:
        selection_model = self.table.selectionModel() if hasattr(self, "table") else None
        if not selection_model:
            return False
        return any(idx.row() == row for idx in selection_model.selectedRows())

    def _on_selection_changed(self) -> None:
        current_rows = {idx.row() for idx in self.table.selectionModel().selectedRows()}
        affected_rows = self._selected_rows | current_rows
        self._selected_rows = current_rows

        wl_id = self._active_wl_id
        data = self._watchlist_data.get(wl_id, {}) if wl_id else {}
        for row in affected_rows:
            sym = self._sym_at_row(row)
            if sym:
                self._write_row(row, sym, data.get(sym, {}))

    def _write_row(self, row: int, sym: str, rec: Dict):
        if row >= self.table.rowCount():
            return

        ltp       = rec.get("ltp", 0.0)
        volume    = rec.get("volume", 0)
        chg_pct   = rec.get("change_pct", 0.0)

        fg_chg, bg_chg = _C.change_color(chg_pct)
        row_bg = QColor(_C.BG1 if row % 2 == 0 else _C.BG2)
        selected = self._is_row_selected(row)
        cell_bg = QBrush() if selected else QBrush(row_bg)

        # Ensure items exist and keep all cells aligned to the compact row base.
        for col in range(_NUM_COLS):
            if not self.table.item(row, col):
                self.table.setItem(row, col, QTableWidgetItem())
            self.table.item(row, col).setBackground(cell_bg)

        # ── Flag ──
        self._paint_flag_cell(row, sym)

        # ── Symbol ──
        sym_item = self.table.item(row, _COL_SYMBOL)
        if sym_item:
            sym_item.setText(sym)
            sym_item.setForeground(QColor(_C.TSYM))
            sym_item.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            sym_item.setFont(self._symbol_font(10, QFont.Weight.Normal))

        # ── LTP ──
        ltp_item = self.table.item(row, _COL_LTP)
        if ltp_item:
            ltp_item.setText(f"{ltp:,.2f}" if ltp > 0 else "—")
            ltp_item.setForeground(QColor(fg_chg if abs(chg_pct) > 0.01 else _C.T0))
            ltp_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            ltp_item.setFont(self._number_font(10, QFont.Weight.Normal))

        # ── Volume ──
        vol_item = self.table.item(row, _COL_VOL)
        if vol_item:
            vol_item.setText(self._fmt_vol(volume))
            vol_item.setForeground(QColor(_C.T2))
            vol_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            vol_item.setToolTip(f"Volume: {volume:,}")
            vol_item.setFont(self._number_font(10, QFont.Weight.Normal))

        # ── Chg% ──
        chg_item = self.table.item(row, _COL_CHG)
        if chg_item:
            chg_item.setText(
                f"{chg_pct:+.2f}%" if abs(chg_pct) > 0.005 else "0.00%"
            )
            chg_item.setForeground(QColor(fg_chg))
            chg_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            chg_weight = QFont.Weight.DemiBold if abs(chg_pct) >= 1.0 else QFont.Weight.Medium
            chg_item.setFont(self._number_font(10, chg_weight))
            if selected:
                chg_item.setBackground(QBrush())
            elif bg_chg:
                chg_item.setBackground(QBrush(QColor(bg_chg)))
            else:
                chg_item.setBackground(QBrush(row_bg))

    def _paint_flag_cell(self, row: int, symbol: str):
        flag_store = _get_flag_store()
        state = flag_store.get(symbol) if flag_store else None
        glyph, color = _FLAG_DISPLAY[state]
        item = self.table.item(row, _COL_FLAG)
        if not item:
            item = QTableWidgetItem()
            self.table.setItem(row, _COL_FLAG, item)
        item.setText(glyph)
        item.setForeground(QColor(color))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setToolTip(_FLAG_TOOLTIP[state])
        item.setFont(self._ui_font(10, QFont.Weight.Normal))

    def _flush_pending_ticks(self):
        if not self._dirty_symbols:
            return

        wl_id = self._active_wl_id
        data  = self._watchlist_data.get(wl_id, {})

        for sym in tuple(self._dirty_symbols):
            row = self._symbol_to_row.get(sym)
            rec = data.get(sym)
            if row is not None and rec is not None:
                self._write_row(row, sym, rec)

        self._dirty_symbols.clear()

    # ═══════════════════════════════════════════════════════════════════════
    # SORTING
    # ═══════════════════════════════════════════════════════════════════════

    def _on_header_click(self, col: int):
        if col == _COL_FLAG:
            return
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = (col == _COL_SYMBOL)
        self._repopulate()

    def _sort_symbols(self, symbols: List[str], data: Dict) -> List[str]:
        def _key(sym):
            rec = data.get(sym, {})
            if self._sort_col == _COL_SYMBOL:   return sym
            if self._sort_col == _COL_LTP:      return rec.get("ltp", 0.0)
            if self._sort_col == _COL_VOL:      return rec.get("volume", 0)
            if self._sort_col == _COL_CHG:      return rec.get("change_pct", 0.0)
            return sym
        return sorted(symbols, key=_key, reverse=not self._sort_asc)

    # ═══════════════════════════════════════════════════════════════════════
    # TABLE EVENTS
    # ═══════════════════════════════════════════════════════════════════════

    def _sym_at_row(self, row: int) -> Optional[str]:
        return next((s for s, r in self._symbol_to_row.items() if r == row), None)

    def _on_cell_click(self, row: int, col: int):
        if col == _COL_FLAG:
            sym = self._sym_at_row(row)
            if sym:
                self._cycle_flag(row, sym)
            return
        sym = self._sym_at_row(row)
        if sym:
            self._nav_idx = row
            self.symbol_chart_requested.emit(sym)

    def _on_cell_double_click(self, row: int, col: int):
        sym = self._sym_at_row(row)
        if sym and col != _COL_FLAG:
            self.symbol_chart_requested.emit(sym)

    def _cycle_flag(self, row: int, sym: str):
        flag_store = _get_flag_store()
        if flag_store:
            flag_store.cycle(sym)
            self._paint_flag_cell(row, sym)

    def _show_ctx_menu(self, pos: QPoint):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        sym = self._sym_at_row(row)
        if not sym:
            return

        menu = QMenu(self)
        menu.setObjectName("floatWlCtxMenu")

        chart_act = menu.addAction("OPEN CHART")
        chart_act.triggered.connect(lambda: self.symbol_chart_requested.emit(sym))

        flag_store = _get_flag_store()
        state = flag_store.get(sym) if flag_store else None
        next_labels = {None: "FLAG GREEN", "green": "FLAG AMBER",
                       "amber": "FLAG RED", "red": "CLEAR FLAG"}
        flag_act = menu.addAction(next_labels.get(state, "FLAG"))
        flag_act.triggered.connect(lambda: self._cycle_flag(row, sym))

        menu.addSeparator()

        buy_act  = menu.addAction("BUY")
        sell_act = menu.addAction("SELL")
        bo_act   = menu.addAction("BRACKET ORDER")
        buy_act.triggered.connect(lambda: self.advanced_buy_order_requested.emit(sym))
        sell_act.triggered.connect(lambda: self.advanced_sell_order_requested.emit(sym))
        bo_act.triggered.connect(lambda: self.bracket_order_requested.emit(sym))

        menu.addSeparator()

        rm_act = menu.addAction("REMOVE FROM WATCHLIST")
        rm_act.triggered.connect(
            lambda: self.symbol_removed_from_watchlist.emit(
                self._active_wl_id or "", sym
            )
        )

        menu.exec(self.table.viewport().mapToGlobal(pos))

    # ═══════════════════════════════════════════════════════════════════════
    # KEYBOARD NAVIGATION
    # ═══════════════════════════════════════════════════════════════════════

    def keyPressEvent(self, event: QKeyEvent):
        key   = event.key()
        count = self.table.rowCount()
        if count == 0:
            super().keyPressEvent(event)
            return

        if key == Qt.Key.Key_Space:
            self._nav_idx = (self._nav_idx + 1) % count
            self._select_row(self._nav_idx)
            return

        if key == Qt.Key.Key_Up:
            self._nav_idx = (self._nav_idx - 1) % count
            self._select_row(self._nav_idx)
            return

        if key == Qt.Key.Key_Down:
            self._nav_idx = (self._nav_idx + 1) % count
            self._select_row(self._nav_idx)
            return

        super().keyPressEvent(event)

    def _select_row(self, row: int):
        self.table.selectRow(row)
        self.table.setCurrentCell(row, _COL_SYMBOL)
        sym = self._sym_at_row(row)
        if sym:
            self.symbol_chart_requested.emit(sym)

    # ═══════════════════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════════════════

    def _rebuild_token_map(self, wl_id: str, instrument_map: Dict[str, Dict]):
        """Build token→symbol map for a watchlist from an instrument_map snippet."""
        tok_map = {}
        for sym, inst in instrument_map.items():
            tok = inst.get("instrument_token")
            if tok is not None:
                try:
                    tok_map[int(tok)] = sym
                except (TypeError, ValueError):
                    pass
        self._token_to_symbol[wl_id] = tok_map

    @staticmethod
    def _fmt_vol(vol: int) -> str:
        if vol >= 10_000_000: return f"{vol / 1_000_000:.0f}M"
        if vol >= 1_000_000:  return f"{vol / 1_000_000:.1f}M"
        if vol >= 1_000:      return f"{vol / 1_000:.0f}K"
        return str(vol) if vol > 0 else "—"

    # ═══════════════════════════════════════════════════════════════════════
    # STYLESHEET
    # ═══════════════════════════════════════════════════════════════════════

    def _apply_styles(self):
        self.setStyleSheet(f"""
            /* Dialog shell */
            FloatingWatchlistDialog {{
                background: {_C.BG0};
                border: 1px solid {_C.BORDER};
                border-radius: 2px;
            }}

            /* Title bar */
            QFrame#floatWlTitleBar {{
                background: {_C.BGTB};
                border-bottom: 1px solid {_C.BORDER};
            }}
            QLabel#floatWlDot {{
                color: {_C.BULL};
                background: transparent;
                font-size: 7px;
            }}
            QLabel#floatWlTitle {{
                color: {_C.AMBER};
                font-family: {_SANS};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 1.2px;
                background: transparent;
            }}
            QLabel#floatWlBadge {{
                color: {_C.CYAN};
                background: rgba(0,212,255,0.08);
                border: 1px solid rgba(0,212,255,0.24);
                border-radius: 2px;
                font-family: {_NUM};
                font-size: 8px;
                font-weight: 800;
                padding: 0 5px;
                min-width: 18px;
            }}
            QToolButton#floatWlBarBtn {{
                background: transparent;
                color: {_C.T2};
                border: none;
                border-radius: 2px;
                font-size: 10px;
                padding: 0;
            }}
            QToolButton#floatWlBarBtn:hover {{
                background: rgba(255,255,255,0.07);
                color: {_C.T0};
            }}
            QToolButton#floatWlBarBtn[active="true"] {{
                color: {_C.CYAN};
            }}
            QToolButton#floatWlCloseBtn {{
                background: transparent;
                color: {_C.T2};
                border: none;
                border-radius: 2px;
                font-size: 10px;
                padding: 0;
            }}
            QToolButton#floatWlCloseBtn:hover {{
                background: rgba(255,77,106,0.15);
                color: {_C.BEAR};
            }}

            /* Watchlist selector */
            QFrame#floatWlSelectorBar {{
                background: {_C.BG1};
                border-bottom: 1px solid {_C.BORDER};
            }}
            QLabel#floatWlSelectorLabel {{
                color: {_C.T2};
                font-family: {_SANS};
                font-size: 8px;
                font-weight: 500;
                letter-spacing: 0.6px;
                background: transparent;
            }}
            QComboBox#floatWlDropdown {{
                background: {_C.BG2};
                color: {_C.T0};
                border: 1px solid {_C.BORDER};
                border-radius: 2px;
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 600;
                padding: 1px 22px 1px 7px;
                min-height: 18px;
                max-height: 20px;
            }}
            QComboBox#floatWlDropdown:hover {{
                border-color: {_C.BORDER2};
                background: {_C.BG3};
            }}
            QComboBox#floatWlDropdown:focus {{
                border-color: {_C.CYAN};
            }}
            QComboBox#floatWlDropdown::drop-down {{
                border: none;
                width: 18px;
                background: transparent;
            }}
            QComboBox#floatWlDropdown::down-arrow {{
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 4px solid {_C.T2};
                margin-right: 5px;
            }}
            QComboBox#floatWlDropdown QAbstractItemView {{
                background: {_C.BG1};
                border: 1px solid {_C.BORDER};
                color: {_C.T0};
                selection-background-color: {_C.SEL};
                selection-color: {_C.T0};
                outline: none;
                padding: 2px;
            }}

            /* Compact table */
            QTableWidget#floatWlTable {{
                background: {_C.BG1};
                alternate-background-color: {_C.BG2};
                gridline-color: rgba(26,32,48,0.65);
                border: none;
                outline: none;
                selection-background-color: {_C.SEL};
                selection-color: {_C.T0};
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 400;
            }}
            QTableWidget#floatWlTable::item {{
                padding: 0 5px;
                border-bottom: 1px solid rgba(26,32,48,0.55);
                background: transparent;
                font-family: {_NUM};
                font-size: 10px;
                font-weight: 400;
            }}
            QTableWidget#floatWlTable::item:selected {{
                background: {_C.SEL};
                color: {_C.T0};
            }}
            QTableWidget#floatWlTable::item:hover {{
                background: {_C.BG3};
            }}
            QHeaderView::section {{
                background: {_C.BG2};
                color: {_C.T2};
                font-family: {_SANS};
                font-size: 8px;
                font-weight: 500;
                letter-spacing: 0.6px;
                text-transform: uppercase;
                border: none;
                border-right: 1px solid rgba(26,32,48,0.55);
                border-bottom: 1px solid {_C.BORDER};
                padding: 0 5px;
            }}

            /* Footer */
            QFrame#floatWlFooter {{
                background: {_C.BGTB};
                border-top: 1px solid {_C.BORDER};
            }}
            QLabel#floatWlFooterLabel {{
                color: {_C.T2};
                font-family: {_SANS};
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 0.4px;
                background: transparent;
            }}
            QLabel#floatWlFooterHint {{
                color: {_C.T3};
                font-family: {_SANS};
                font-size: 8px;
                background: transparent;
                letter-spacing: 0.3px;
            }}

            /* Context menu */
            QMenu#floatWlCtxMenu {{
                background: {_C.BG1};
                border: 1px solid {_C.BORDER};
                border-radius: 2px;
                padding: 3px 0;
                font-family: {_SANS};
                font-size: 10px;
                color: {_C.T0};
            }}
            QMenu#floatWlCtxMenu::item {{
                padding: 5px 14px;
                background: transparent;
            }}
            QMenu#floatWlCtxMenu::item:selected {{
                background: {_C.SEL};
                color: {_C.T0};
            }}
            QMenu#floatWlCtxMenu::separator {{
                height: 1px;
                background: {_C.BORDER};
                margin: 3px 8px;
            }}

            /* Scrollbars */
            QScrollBar:vertical {{
                background: transparent;
                width: 4px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {_C.BORDER2};
                border-radius: 2px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {_C.T2};
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0;
                border: none;
            }}
            QScrollBar:horizontal {{
                background: transparent;
                height: 4px;
                border: none;
            }}
            QScrollBar::handle:horizontal {{
                background: {_C.BORDER2};
                border-radius: 2px;
                min-width: 20px;
            }}
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {{
                width: 0;
                border: none;
            }}
        """)


# ─────────────────────────────────────────────────────────────────────────────
#  INTEGRATION HELPER  — call from QullamaggieWindow.__init__
# ─────────────────────────────────────────────────────────────────────────────

def attach_floating_watchlist(main_window) -> FloatingWatchlistDialog:
    """
    Create and wire a FloatingWatchlistDialog into an existing QullamaggieWindow.

    Call once after __init__:
        self.floating_watchlist = attach_floating_watchlist(self)

    Wires:
      • market data ticks          → dialog.update_data (same path as embedded)
      • dialog.symbol_chart_requested    → candlestick_chart.on_search
      • dialog.advanced_buy_order_requested  → main_window._on_header_buy_order
      • dialog.advanced_sell_order_requested → main_window._on_header_sell_order
      • dialog.bracket_order_requested   → watchlist.bracket_order_requested relay
      • dialog.symbol_removed_from_watchlist → embedded watchlist remove

    Call dialog.push_watchlist_data() or dialog.set_watchlists() to push data.
    The dialog is NOT shown automatically — caller decides when to show it.

    Main window integration example (add to _show_floating_watchlist_dialog):

        def _show_floating_watchlist_dialog(self):
            if not self.floating_watchlist.isVisible():
                self._sync_floating_watchlist()
            self.floating_watchlist.show()
            self.floating_watchlist.raise_()

        def _sync_floating_watchlist(self):
            meta = []
            for entry in self.watchlist._config.all():
                wl_id = entry["id"]
                table = self.watchlist._tables.get(wl_id)
                if not table:
                    continue
                symbols = table.get_symbol_list()
                data = {}
                for sym in symbols:
                    rec = table._watchlist_data.get(sym, {})
                    data[sym] = dict(rec)
                meta.append({
                    "id":      wl_id,
                    "name":    entry["name"],
                    "symbols": symbols,
                    "data":    data,
                })
                # Feed token map
                self.floating_watchlist._token_to_symbol[wl_id] = dict(table._token_to_symbol)
            self.floating_watchlist.set_watchlists(meta)
    """
    dlg = FloatingWatchlistDialog(parent=main_window)

    # Symbol → chart
    if hasattr(main_window, "candlestick_chart"):
        dlg.symbol_chart_requested.connect(main_window.candlestick_chart.on_search)

    # Order actions
    if hasattr(main_window, "_on_header_buy_order"):
        dlg.advanced_buy_order_requested.connect(main_window._on_header_buy_order)
    if hasattr(main_window, "_on_header_sell_order"):
        dlg.advanced_sell_order_requested.connect(main_window._on_header_sell_order)

    # Bracket order — relay through embedded watchlist's signal
    if hasattr(main_window, "watchlist"):
        dlg.bracket_order_requested.connect(main_window.watchlist.bracket_order_requested)

    # Symbol remove — relay to embedded watchlist's remove API
    def _handle_remove(wl_id: str, sym: str):
        if hasattr(main_window, "watchlist"):
            table = main_window.watchlist._tables.get(wl_id)
            if table:
                table.remove_symbol(sym)
                # Refresh the floating dialog after removal
                dlg.refresh_watchlist(wl_id)

    dlg.symbol_removed_from_watchlist.connect(_handle_remove)

    return dlg