# kite/widgets/floating_positions_dialog.py
"""
FloatingPositionsDialog — compact institutional dark positions monitor.

Design language:
  • Matte dark trading-terminal shell with layered panels and 1 px separators
  • Compact table-first layout with 21 px rows and muted uppercase headers
  • Modern UI number typography for price / quantity / P&L columns
  • Monospace reserved only for raw logs, IDs, code, and debug text
  • Purposeful color semantics: green profit/buy, red loss/sell, amber warning/SL,
    cyan utility/pinned/live state, muted blue-gray labels
  • Frameless, draggable, always-on-top with pin toggle and minimal controls
  • Live P&L footer with a centered exposure pressure bar
  • Right-click context menu: chart, stop-loss, exit full, exit half
  • Keyboard: Space / Up / Down cycles symbols into chart

Public API:
    dialog = FloatingPositionsDialog(parent=main_window)
    dialog.show()

    # Feed ticks (same interface as PositionsTable):
    dialog.update_market_data(token: int, ltp: float)

    # Feed positions (same interface as PositionsTable):
    dialog.update_positions(positions: List[Position])

Signals:
    symbol_chart_requested(str)        — open symbol in chart
    exit_position_requested(str)       — full exit for symbol
    exit_half_position_requested(str)  — half exit for symbol
    subscribe_to_market_data(list)     — list[int] of tokens to subscribe
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import (
    Qt, Signal, Slot, QTimer, QPoint, QSize, QPropertyAnimation,
    QEasingCurve, QRect, Property, QByteArray
)
from PySide6.QtGui import (
    QColor, QFont, QBrush, QPainter, QPen, QLinearGradient,
    QCursor, QMouseEvent, QKeyEvent, QPainterPath
)
from PySide6.QtWidgets import (
    QDialog, QWidget, QFrame, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMenu, QSizeGrip,
    QSizePolicy, QToolButton, QApplication, QGraphicsDropShadowEffect
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  DESIGN TOKENS  (single source — mirrors consistency_rules.md)
# ─────────────────────────────────────────────────────────────────────────────

class _C:
    # Matte terminal layers
    BG0      = "#050709"   # outer app/window shell
    BG1      = "#0a0d12"   # dialog body
    BG2      = "#0f1318"   # table base rows
    BG3      = "#141920"   # row hover / footer surface
    BG4      = "#070a0f"   # title bar / hard chrome
    BORDER   = "#1a2030"   # primary separator
    BORDER2  = "#243040"   # active separator / grip / scrollbar
    SELECT   = "#1a2840"   # selected row

    # Market semantics
    BULL     = "#00d4a8"
    BULL_DIM = "#14745f"
    BULL_BG  = "#08231d"
    BEAR     = "#ff4d6a"
    BEAR_DIM = "#7a2030"
    BEAR_BG  = "#230a12"
    FLAT     = "#7a94b0"

    # Text
    T0       = "#e8f0ff"
    SYMBOL   = "#b6c4d6"   # softened symbol column text
    T1       = "#a8bcd4"
    T2       = "#5a7090"
    T3       = "#2a3a50"

    # Accents
    CYAN     = "#00d4ff"
    AMBER    = "#f59e0b"
    BLUE     = "#00d4ff"

    # Flash fills
    FLASH_UP = "#103d32"
    FLASH_DN = "#42111c"

_MONO = "\"Consolas\", \"JetBrains Mono\", \"Courier New\", monospace"  # technical/debug only
_SANS = "\"Inter\", \"Segoe UI\", -apple-system, Roboto, sans-serif"
_NUM = "\"Inter\", \"Segoe UI Variable\", \"Segoe UI\", -apple-system, Roboto, sans-serif"
_NUM_FONT = "Inter"


# ─────────────────────────────────────────────────────────────────────────────
#  COLUMN CONFIG
# ─────────────────────────────────────────────────────────────────────────────

_COLS = [
    ("Symbol",  "symbol",   116, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
    ("Qty",     "quantity",  52, Qt.AlignmentFlag.AlignCenter),
    ("Avg",     "avg_price", 74, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
    ("LTP",     "ltp",       74, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
    ("P&L",     "pnl",       88, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
    ("SL",      "sl",        78, Qt.AlignmentFlag.AlignCenter),
]

_COL_IDX = {name: i for i, (name, *_) in enumerate(_COLS)}

_FLASH_DURATION = 400   # ms
_REDRAW_INTERVAL = 200  # ms  (~5 fps — human-readable)
_ROW_HEIGHT = 21


_FLOATING_POS_STATE_KEY = "floating_positions_dialog"
_DEFAULT_DIALOG_SIZE = QSize(600, 326)

# ─────────────────────────────────────────────────────────────────────────────
#  POSITION DATA CLASS  (same shape as positions_table.py)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _PosRow:
    symbol:    str
    quantity:  int
    avg_price: float
    token:     int
    ltp:       float = 0.0
    pnl:       float = 0.0
    product:   str   = "MIS"

    @property
    def chg_pct(self) -> float:
        if self.avg_price <= 0:
            return 0.0
        return (self.ltp - self.avg_price) / self.avg_price * 100

    def refresh_pnl(self):
        self.pnl = (self.ltp - self.avg_price) * self.quantity


# ─────────────────────────────────────────────────────────────────────────────
#  MINI EXPOSURE BAR  (custom painted)
# ─────────────────────────────────────────────────────────────────────────────

class _ExposureBar(QWidget):
    """Centered exposure pressure bar: red left, green right, neutral midpoint."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(4)
        self.setMinimumWidth(120)
        self._pct = 0.5

    def set_pct(self, v: float):
        self._pct = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.width(), self.height()
        mid = w // 2
        p.fillRect(0, 0, w, h, QColor(_C.BG2))
        p.fillRect(mid, 0, 1, h, QColor(_C.BORDER2))

        end = int(w * self._pct)
        if end > mid:
            p.fillRect(mid, 0, end - mid, h, QColor(_C.BULL))
        elif end < mid:
            p.fillRect(end, 0, mid - end, h, QColor(_C.BEAR))
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
#  FLASH CELL TRACKER
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _Flash:
    row: int
    col: int
    direction: int      # +1 up  /  -1 down
    remaining: int = _FLASH_DURATION


# ─────────────────────────────────────────────────────────────────────────────
#  RESIZE GRIP (transparent, bottom-right corner)
# ─────────────────────────────────────────────────────────────────────────────

class _ResizeGrip(QWidget):
    """Minimal 12×12 px drag handle drawn with two diagonal lines."""

    SIZE = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
        self._dragging = False
        self._start_pos = QPoint()
        self._start_geo = QRect()

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
            self._dragging = True
            self._start_pos = e.globalPosition().toPoint()
            self._start_geo = self.window().geometry()

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._dragging:
            delta = e.globalPosition().toPoint() - self._start_pos
            geo = self._start_geo
            new_w = max(380, geo.width()  + delta.x())
            new_h = max(200, geo.height() + delta.y())
            self.window().setGeometry(geo.x(), geo.y(), new_w, new_h)

    def mouseReleaseEvent(self, _):
        self._dragging = False


# ─────────────────────────────────────────────────────────────────────────────
#  FLOATING POSITIONS DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class FloatingPositionsDialog(QDialog):
    """TC2000-style always-on-top floating positions monitor."""

    # ── Public signals ──────────────────────────────────────────────────────
    symbol_chart_requested      = Signal(str)
    exit_position_requested     = Signal(str)
    exit_half_position_requested = Signal(str)
    subscribe_to_market_data    = Signal(list)

    def __init__(self, parent=None):
        flags = (
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumSize(420, 190)
        self.resize(_DEFAULT_DIALOG_SIZE)

        # ── State ────────────────────────────────────────────────────────────
        self._positions: Dict[str, _PosRow] = {}       # symbol → row
        self._sym_to_row: Dict[str, int] = {}          # symbol → table row index
        self._tok_to_sym: Dict[int, str] = {}          # token → symbol
        self._pending_ticks: Dict[int, float] = {}     # token → ltp (buffered)
        self._flashes: List[_Flash] = []
        self._prev_ltps: Dict[str, float] = {}
        self._pinned = True                            # always-on-top toggle
        self._dragging = False
        self._drag_offset = QPoint()
        self._subscribed: set = set()
        self._nav_idx = 0                              # keyboard nav index

        # ── Build ────────────────────────────────────────────────────────────
        self._build_ui()
        self._apply_styles()

        # ── Timers ───────────────────────────────────────────────────────────
        self._redraw_timer = QTimer(self)
        self._redraw_timer.timeout.connect(self._flush_pending_ticks)
        self._redraw_timer.start(_REDRAW_INTERVAL)

        self._flash_timer = QTimer(self)
        self._flash_timer.timeout.connect(self._decay_flashes)
        self._flash_timer.start(40)     # ~25 fps flash decay

        self._restore_window_state()

    # ═══════════════════════════════════════════════════════════════════════
    # UI CONSTRUCTION
    # ═══════════════════════════════════════════════════════════════════════

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        # ── Title bar ────────────────────────────────────────────────────────
        root.addWidget(self._build_title_bar())

        # ── Table ────────────────────────────────────────────────────────────
        self.table = self._build_table()
        root.addWidget(self.table, 1)

        # ── Footer ───────────────────────────────────────────────────────────
        root.addWidget(self._build_footer())

        # ── Resize grip ──────────────────────────────────────────────────────
        grip = _ResizeGrip(self)
        grip.move(self.width() - _ResizeGrip.SIZE, self.height() - _ResizeGrip.SIZE)
        self._grip = grip

    def _build_title_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("titleBar")
        bar.setFixedHeight(28)
        bar.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))

        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 0, 5, 0)
        h.setSpacing(6)

        # Dot indicator + title
        self._dot = QLabel("●")
        self._dot.setObjectName("dotIndicator")
        self._dot.setFixedWidth(10)

        title = QLabel("POSITIONS")
        title.setObjectName("barTitle")

        self._count_badge = QLabel("0")
        self._count_badge.setObjectName("countBadge")
        self._count_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._count_badge.setFixedHeight(16)

        h.addWidget(self._dot)
        h.addWidget(title)
        h.addWidget(self._count_badge)
        h.addStretch()

        # Right controls
        self._pin_btn = QToolButton()
        self._pin_btn.setObjectName("barBtn")
        self._pin_btn.setText("PIN")
        self._pin_btn.setToolTip("Toggle always-on-top")
        self._pin_btn.setFixedSize(30, 20)
        self._pin_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._pin_btn.clicked.connect(self._toggle_pin)
        self._pin_btn.setProperty("active", True)

        min_btn = QToolButton()
        min_btn.setObjectName("barBtn")
        min_btn.setText("—")
        min_btn.setFixedSize(22, 20)
        min_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        min_btn.clicked.connect(self.showMinimized)

        close_btn = QToolButton()
        close_btn.setObjectName("closeBtn")
        close_btn.setText("✕")
        close_btn.setFixedSize(22, 20)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self.hide)

        h.addWidget(self._pin_btn)
        h.addWidget(min_btn)
        h.addWidget(close_btn)

        # Drag via title bar
        bar.mousePressEvent   = self._tb_press
        bar.mouseMoveEvent    = self._tb_move
        bar.mouseReleaseEvent = self._tb_release

        return bar

    def _build_table(self) -> QTableWidget:
        t = QTableWidget(0, len(_COLS))
        t.setObjectName("posTable")
        t.setHorizontalHeaderLabels([name for name, *_ in _COLS])

        hdr = t.horizontalHeader()
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.setHighlightSections(False)
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)

        for i, (_, _, width, _) in enumerate(_COLS):
            t.setColumnWidth(i, width)
        # Symbol column stretches
        t.horizontalHeader().setSectionResizeMode(
            _COL_IDX["Symbol"], QHeaderView.ResizeMode.Stretch)

        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT)
        t.verticalHeader().setMinimumSectionSize(_ROW_HEIGHT)
        t.setWordWrap(False)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setShowGrid(False)
        t.setAlternatingRowColors(True)
        t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        t.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        t.setSortingEnabled(False)

        t.customContextMenuRequested.connect(self._ctx_menu)
        t.cellDoubleClicked.connect(self._on_double_click)
        t.cellClicked.connect(self._on_cell_click)
        t.focusOutEvent = self._on_table_focus_out
        return t

    def _on_table_focus_out(self, event):
        """Clear row highlight when focus moves away (e.g., clicking empty chart area)."""
        if self.table:
            self.table.clearSelection()
        QTableWidget.focusOutEvent(self.table, event)

    def _build_footer(self) -> QFrame:
        f = QFrame()
        f.setObjectName("footer")
        f.setFixedHeight(28)

        h = QHBoxLayout(f)
        h.setContentsMargins(8, 0, 8, 0)
        h.setSpacing(8)

        def _metric(label: str, key: str) -> QLabel:
            lbl = QLabel(label)
            lbl.setObjectName("footerLabel")
            val = QLabel("—")
            val.setObjectName(f"footerVal_{key}")
            h.addWidget(lbl)
            h.addWidget(val)
            return val

        self._total_pnl_lbl  = _metric("P&L", "pnl")
        self._exposure_lbl   = _metric("EXP", "exp")
        self._pos_count_lbl  = _metric("POS", "cnt")

        h.addStretch()

        self._exposure_bar = _ExposureBar()
        # Exposure bar sits inside footer; we embed it in a small wrapper
        bar_wrap = QWidget()
        bw_lay = QVBoxLayout(bar_wrap)
        bw_lay.setContentsMargins(0, 12, 0, 12)
        bw_lay.addWidget(self._exposure_bar)
        h.addWidget(bar_wrap)

        return f

    # ═══════════════════════════════════════════════════════════════════════
    # TITLE BAR DRAG
    # ═══════════════════════════════════════════════════════════════════════

    def _tb_press(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _tb_move(self, e: QMouseEvent):
        if self._dragging and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_offset)

    def _tb_release(self, _):
        self._dragging = False

    def _toggle_pin(self):
        self._pinned = not self._pinned
        self._pin_btn.setProperty("active", self._pinned)
        self._pin_btn.style().unpolish(self._pin_btn)
        self._pin_btn.style().polish(self._pin_btn)
        flags = self.windowFlags()
        if self._pinned:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    def _load_state(self) -> Dict:
        parent = self.parent()
        cfg = getattr(parent, "config_manager", None)
        if not cfg:
            return {}
        state = cfg.load_window_state() or {}
        if not isinstance(state, dict):
            return {}
        return state.get(_FLOATING_POS_STATE_KEY, {}) or {}

    def _save_window_state(self):
        parent = self.parent()
        cfg = getattr(parent, "config_manager", None)
        if not cfg:
            return
        state = cfg.load_window_state() or {}
        if not isinstance(state, dict):
            state = {}
        state[_FLOATING_POS_STATE_KEY] = {
            "width": self.width(),
            "height": self.height(),
            "x": self.x(),
            "y": self.y(),
        }
        cfg.save_window_state(state)

    def _restore_window_state(self):
        saved = self._load_state()
        width = int(saved.get("width", _DEFAULT_DIALOG_SIZE.width()))
        height = int(saved.get("height", _DEFAULT_DIALOG_SIZE.height()))
        self.resize(max(self.minimumWidth(), width), max(self.minimumHeight(), height))
        if "x" in saved and "y" in saved:
            self.move(int(saved["x"]), int(saved["y"]))

    def hideEvent(self, event):
        self._save_window_state()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._save_window_state()
        super().closeEvent(event)

    # ═══════════════════════════════════════════════════════════════════════
    # RESIZE
    # ═══════════════════════════════════════════════════════════════════════

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if hasattr(self, '_grip'):
            self._grip.move(
                self.width()  - _ResizeGrip.SIZE,
                self.height() - _ResizeGrip.SIZE
            )
        self._save_window_state()

    def moveEvent(self, ev):
        super().moveEvent(ev)
        self._save_window_state()

    # ═══════════════════════════════════════════════════════════════════════
    # PUBLIC: POSITION FEED
    # ═══════════════════════════════════════════════════════════════════════

    @Slot(list)
    def update_positions(self, positions) -> None:
        """
        Accept list of Position objects (same interface as PositionsTable).
        Each item needs: symbol, quantity, avg_price, token, ltp, pnl, product.
        """
        new_data: Dict[str, _PosRow] = {}
        for pos in positions:
            sym = getattr(pos, "symbol", "")
            qty = int(getattr(pos, "quantity", 0) or 0)
            if not sym or qty == 0:
                continue
            row = _PosRow(
                symbol    = sym,
                quantity  = qty,
                avg_price = float(getattr(pos, "avg_price", 0) or 0),
                token     = int(getattr(pos, "token", 0) or 0),
                ltp       = float(getattr(pos, "ltp", 0) or 0),
                pnl       = float(getattr(pos, "pnl", 0) or 0),
                product   = str(getattr(pos, "product", "MIS") or "MIS"),
            )
            row.refresh_pnl()
            new_data[sym] = row

        self._positions = new_data
        self._sym_to_row = {}
        self._tok_to_sym = {}

        self.table.setRowCount(len(new_data))
        sorted_syms = sorted(new_data.keys())
        for r, sym in enumerate(sorted_syms):
            self._sym_to_row[sym] = r
            tok = new_data[sym].token
            if tok:
                self._tok_to_sym[tok] = sym
            self._write_row(r, new_data[sym])

        self._subscribe_new_tokens()
        self._update_footer()
        if hasattr(self, "_count_badge"):
            self._count_badge.setText(str(len(new_data)))
            self._count_badge.setProperty("active", bool(new_data))
            self._count_badge.style().unpolish(self._count_badge)
            self._count_badge.style().polish(self._count_badge)
        self._dot.setStyleSheet(
            f"color: {_C.BULL if new_data else _C.T3}; background: transparent;"
        )

    @Slot(str)
    def refresh_stop_loss_values(self, symbol: str = "") -> None:
        """Repaint SL cells after a stop-loss-only change such as chart-line drag."""
        target = str(symbol or "").strip().upper()
        symbols = [
            sym for sym in self._positions.keys()
            if not target or str(sym).strip().upper() == target
        ]
        for sym in symbols:
            pos = self._positions.get(sym)
            row = self._sym_to_row.get(sym)
            if pos is None or row is None:
                continue
            self._write_row(row, pos)
        self._update_footer()

    # ═══════════════════════════════════════════════════════════════════════
    # PUBLIC: TICK FEED
    # ═══════════════════════════════════════════════════════════════════════

    @Slot(int, float)
    def update_market_data(self, token: int, ltp: float) -> None:
        """Buffer tick; flushed by redraw timer (~5 fps)."""
        if not self.isVisible():
            return

        self._pending_ticks[token] = ltp

    # ═══════════════════════════════════════════════════════════════════════
    # INTERNAL: TICK FLUSH
    # ═══════════════════════════════════════════════════════════════════════

    def _flush_pending_ticks(self):
        if not self._pending_ticks:
            return
        for token, ltp in self._pending_ticks.items():
            sym = self._tok_to_sym.get(token)
            if not sym:
                continue
            pos = self._positions.get(sym)
            if not pos:
                continue
            prev = self._prev_ltps.get(sym, pos.ltp)
            self._prev_ltps[sym] = ltp
            pos.ltp = ltp
            pos.refresh_pnl()
            row = self._sym_to_row.get(sym)
            if row is not None:
                direction = 1 if ltp > prev else (-1 if ltp < prev else 0)
                self._write_row(row, pos)
                if direction and prev > 0:
                    self._flashes.append(_Flash(row, _COL_IDX["LTP"], direction))
                    self._flashes.append(_Flash(row, _COL_IDX["P&L"], direction))
        self._pending_ticks.clear()
        self._update_footer()

    # ═══════════════════════════════════════════════════════════════════════
    # INTERNAL: ROW RENDERING
    # ═══════════════════════════════════════════════════════════════════════

    def _get_sl_display(self, pos: _PosRow) -> tuple[str, str]:
        """Returns (text, color) for the SL column."""
        sl_mgr = self._get_sl_manager()
        if not sl_mgr:
            return "—", "#2a3a50"

        rec = sl_mgr.get_sl_for(pos.symbol, pos.product)
        if not rec:
            return "—", "#2a3a50"

        if pos.ltp > 0:
            dist_pct = abs(pos.ltp - rec.sl_price) / pos.ltp * 100
            color = "#f59e0b" if dist_pct < 1.0 else "#5a7090"
        else:
            color = "#5a7090"

        trail_mark = "⟳ " if rec.trailing_sl else ""
        return f"{trail_mark}₹{rec.sl_price:.2f}", color

    def _write_row(self, row: int, pos: _PosRow):
        if row >= self.table.rowCount():
            return

        is_long  = pos.quantity > 0
        pnl_pos  = pos.pnl > 0
        pnl_neg  = pos.pnl < 0
        pnl_col  = _C.BULL if pnl_pos else (_C.BEAR if pnl_neg else _C.FLAT)
        qty_col  = _C.BULL if is_long else _C.BEAR
        if pnl_pos:
            row_bg = QColor(_C.BULL_BG)
        elif pnl_neg:
            row_bg = QColor(_C.BEAR_BG)
        else:
            row_bg = QColor(_C.BG2 if row % 2 == 0 else _C.BG1)

        qty_sign = "+" if is_long else "−"
        pnl_sign = "+" if pos.pnl >= 0 else ""

        sl_text, sl_color = self._get_sl_display(pos)
        values = [
            (pos.symbol,                                       _C.SYMBOL, True),
            (f"{qty_sign}{abs(pos.quantity)}",                 qty_col, False),
            (f"{pos.avg_price:,.2f}",                          _C.T1,   False),
            (f"{pos.ltp:,.2f}" if pos.ltp > 0 else "—",       _C.T0,   False),
            (f"{pnl_sign}{pos.pnl:,.2f}",                     pnl_col, True),
            (sl_text,                                           sl_color, False),
        ]

        align_map = [a for _, _, _, a in _COLS]

        for col, (text, color, bold) in enumerate(values):
            item = self.table.item(row, col)
            if item is None:
                item = QTableWidgetItem()
                self.table.setItem(row, col, item)

            item.setText(text)
            item.setForeground(QBrush(QColor(color)))
            item.setBackground(QBrush(row_bg))
            item.setTextAlignment(align_map[col])

            # Modern UI typography for all visible text/numbers; monospace is
            # reserved for raw logs, IDs, code, and debug text only.
            font = QFont(_NUM_FONT)
            font.setStyleHint(QFont.StyleHint.SansSerif)
            font.setPointSize(8 if col == _COL_IDX["Symbol"] else 9)
            if col == _COL_IDX["Symbol"]:
                font.setWeight(QFont.Weight.DemiBold)
            elif bold:
                font.setWeight(QFont.Weight.DemiBold)
            else:
                font.setWeight(QFont.Weight.Medium)
            font.setKerning(True)
            item.setFont(font)

    # ═══════════════════════════════════════════════════════════════════════
    # INTERNAL: FLASH DECAY
    # ═══════════════════════════════════════════════════════════════════════

    def _decay_flashes(self):
        if not self._flashes:
            return
        surviving = []
        for flash in self._flashes:
            flash.remaining -= 40
            item = self.table.item(flash.row, flash.col)
            if item:
                ratio = max(0.0, flash.remaining / _FLASH_DURATION)
                if ratio > 0:
                    base = QColor(_C.FLASH_UP if flash.direction > 0 else _C.FLASH_DN)
                    base.setAlpha(int(90 + 120 * ratio))
                    item.setBackground(QBrush(base))
                    surviving.append(flash)
                else:
                    # Restore row background
                    sym = None
                    for s, ri in self._sym_to_row.items():
                        if ri == flash.row:
                            sym = s
                            break
                    if sym and sym in self._positions:
                        self._write_row(flash.row, self._positions[sym])
        self._flashes = surviving

    # ═══════════════════════════════════════════════════════════════════════
    # INTERNAL: FOOTER
    # ═══════════════════════════════════════════════════════════════════════

    def _update_footer(self):
        if not self._positions:
            self._total_pnl_lbl.setText("—")
            self._exposure_lbl.setText("—")
            self._pos_count_lbl.setText("0")
            self._exposure_bar.set_pct(0.5)
            return

        total_pnl  = sum(p.pnl  for p in self._positions.values())
        exposure   = sum(abs(p.quantity) * p.avg_price for p in self._positions.values())
        count      = len(self._positions)

        pnl_col = _C.BULL if total_pnl >= 0 else _C.BEAR
        sign    = "+" if total_pnl >= 0 else ""

        self._total_pnl_lbl.setText(f"{sign}{total_pnl:,.0f}")
        self._total_pnl_lbl.setStyleSheet(
            f"color: {pnl_col}; font-family: {_NUM}; font-size: 12px;"
            f" font-weight: 700; background: transparent;"
        )
        self._exposure_lbl.setText(f"₹{exposure:,.0f}")
        self._pos_count_lbl.setText(str(count))

        # Exposure bar: position of pnl between worst and best possible
        total_invested = max(exposure, 1)
        bar_pct = 0.5 + (total_pnl / total_invested) * 0.5
        self._exposure_bar.set_pct(bar_pct)

    # ═══════════════════════════════════════════════════════════════════════
    # INTERNAL: WS SUBSCRIPTIONS
    # ═══════════════════════════════════════════════════════════════════════

    def _subscribe_new_tokens(self):
        new_toks = [
            tok for tok in self._tok_to_sym
            if tok not in self._subscribed and tok > 0
        ]
        if new_toks:
            self.subscribe_to_market_data.emit(new_toks)
            self._subscribed.update(new_toks)

    # ═══════════════════════════════════════════════════════════════════════
    # TABLE EVENTS
    # ═══════════════════════════════════════════════════════════════════════

    def _symbol_at_row(self, row: int) -> Optional[str]:
        return next((s for s, r in self._sym_to_row.items() if r == row), None)

    def _on_cell_click(self, row: int, _col: int):
        sym = self._symbol_at_row(row)
        if sym:
            self.symbol_chart_requested.emit(sym)
            self._nav_idx = row

    def _on_double_click(self, row: int, _col: int):
        sym = self._symbol_at_row(row)
        if sym:
            self.symbol_chart_requested.emit(sym)

    def _ctx_menu(self, pos: QPoint):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        sym = self._symbol_at_row(row)
        if not sym:
            return

        position = self._positions.get(sym)
        if not position:
            return

        menu = QMenu(self)
        menu.setObjectName("posCtxMenu")

        chart_act = menu.addAction("📈  Open Chart")
        chart_act.triggered.connect(lambda: self.symbol_chart_requested.emit(sym))
        menu.addSeparator()

        # ── Stop-Loss section ────────────────────────────────────────────────
        sl_mgr = self._get_sl_manager()
        existing_sl = sl_mgr.get_sl_for(sym, position.product) if sl_mgr else None

        if existing_sl:
            sl_price = existing_sl.sl_price
            dist_pct = existing_sl.distance_pct
            sl_lbl = (
                f"⚙  Modify SL @ ₹{sl_price:.2f} "
                f"({existing_sl.sl_quantity.lower()}, {dist_pct:.1f}% away)"
            )
            sl_act = menu.addAction(sl_lbl)
            sl_act.triggered.connect(
                lambda: self._open_sl_dialog(sym, position, existing_sl.sl_price)
            )
            remove_sl_act = menu.addAction("✕  Remove Stop-Loss")
            remove_sl_act.triggered.connect(
                lambda: sl_mgr.cancel_stop_loss(sym, position.product)
            )
        else:
            set_sl_act = menu.addAction("🛡  Set Stop-Loss…")
            set_sl_act.triggered.connect(
                lambda: self._open_sl_dialog(sym, position, None)
            )

        menu.addSeparator()

        exit_act = menu.addAction("✕  Exit Full Position")
        exit_act.triggered.connect(lambda: self.exit_position_requested.emit(sym))

        # Show count dynamically
        half_qty = max(1, abs(position.quantity) // 2)
        half_act = menu.addAction(f"½  Exit Half ({half_qty} shares)")
        half_act.triggered.connect(lambda: self.exit_half_position_requested.emit(sym))

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _open_sl_dialog(self, symbol: str, position, current_sl_price: Optional[float]):
        """Open the SL configuration dialog."""
        from kite.widgets.stop_loss_dialog import StopLossDialog
        sl_mgr = self._get_sl_manager()
        if not sl_mgr:
            return

        ltp = position.ltp if position.ltp > 0 else position.avg_price

        dlg = StopLossDialog(
            symbol=symbol,
            ltp=ltp,
            avg_price=position.avg_price,
            quantity=position.quantity,
            product=position.product,
            current_sl=current_sl_price,
            parent=self,
        )

        def _on_sl_confirmed(sym, sl_price, sl_qty_type, custom_qty,
                             order_type, trailing, trail_pct):
            sl_mgr.set_stop_loss(
                symbol=sym,
                sl_price=sl_price,
                quantity=position.quantity,
                avg_price=position.avg_price,
                product=position.product,
                sl_quantity=sl_qty_type,
                custom_qty=custom_qty,
                sl_type=order_type,
                trailing=trailing,
                trail_pct=trail_pct,
            )

        def _on_sl_remove(sym):
            sl_mgr.cancel_stop_loss(sym, position.product)

        dlg.sl_confirmed.connect(_on_sl_confirmed)
        dlg.sl_cancelled_by_user.connect(_on_sl_remove)
        dlg.exec()

    def _get_sl_manager(self):
        """Return the StopLossManager from the main window, if available."""
        parent = self.parent()
        if not parent:
            return None
        return getattr(parent, "sl_manager", None) or getattr(parent, "stop_loss_manager", None)

    # ═══════════════════════════════════════════════════════════════════════
    # KEYBOARD NAV
    # ═══════════════════════════════════════════════════════════════════════

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        count = self.table.rowCount()
        if count == 0:
            super().keyPressEvent(event)
            return

        if key == Qt.Key.Key_Space:
            self._nav_idx = (self._nav_idx + 1) % count
            self.table.selectRow(self._nav_idx)
            sym = self._symbol_at_row(self._nav_idx)
            if sym:
                self.symbol_chart_requested.emit(sym)
            return

        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = -1 if key == Qt.Key.Key_Up else 1
            self._nav_idx = (self._nav_idx + step) % count
            self.table.selectRow(self._nav_idx)
            sym = self._symbol_at_row(self._nav_idx)
            if sym:
                self.symbol_chart_requested.emit(sym)
            return

        super().keyPressEvent(event)

    # ═══════════════════════════════════════════════════════════════════════
    # STYLESHEET
    # ═══════════════════════════════════════════════════════════════════════

    def _apply_styles(self):
        self.setStyleSheet(f"""
            /* Dialog shell */
            FloatingPositionsDialog {{
                background: {_C.BG0};
                border: 1px solid {_C.BORDER};
                border-radius: 2px;
            }}

            /* Title bar */
            QFrame#titleBar {{
                background: {_C.BG4};
                border-bottom: 1px solid {_C.BORDER};
                border-radius: 0px;
            }}
            QLabel#dotIndicator {{
                color: {_C.BULL};
                background: transparent;
                font-family: {_SANS};
                font-size: 8px;
                font-weight: 900;
            }}
            QLabel#barTitle {{
                color: {_C.T1};
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 1.6px;
                background: transparent;
            }}
            QLabel#countBadge {{
                color: {_C.T2};
                background: rgba(255,255,255,0.03);
                border: 1px solid {_C.BORDER};
                border-radius: 2px;
                font-family: {_NUM};
                font-size: 9px;
                font-weight: 800;
                padding: 0 5px;
                min-width: 18px;
            }}
            QLabel#countBadge[active="true"] {{
                color: {_C.CYAN};
                border-color: rgba(0,212,255,0.35);
                background: rgba(0,212,255,0.08);
            }}
            QToolButton#barBtn {{
                background: transparent;
                color: {_C.T2};
                border: 1px solid transparent;
                border-radius: 2px;
                font-family: {_SANS};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 0.4px;
            }}
            QToolButton#barBtn:hover {{
                background: rgba(255,255,255,0.06);
                border-color: {_C.BORDER};
                color: {_C.T0};
            }}
            QToolButton#barBtn[active="true"] {{
                color: {_C.CYAN};
                border-color: rgba(0,212,255,0.24);
                background: rgba(0,212,255,0.06);
            }}
            QToolButton#closeBtn {{
                background: transparent;
                color: {_C.T2};
                border: 1px solid transparent;
                border-radius: 2px;
                font-size: 11px;
                font-weight: 800;
            }}
            QToolButton#closeBtn:hover {{
                background: rgba(255,77,106,0.14);
                border-color: rgba(255,77,106,0.28);
                color: {_C.BEAR};
            }}

            /* Compact position table */
            QTableWidget#posTable {{
                background: {_C.BG1};
                alternate-background-color: {_C.BG2};
                gridline-color: transparent;
                border: none;
                outline: none;
                selection-background-color: {_C.SELECT};
                selection-color: {_C.T0};
                font-family: {_NUM};
                font-size: 11px;
                color: {_C.T0};
            }}
            QTableWidget#posTable::item {{
                padding: 0 5px;
                border-bottom: 1px solid {_C.BG3};
            }}
            QTableWidget#posTable::item:selected {{
                background: {_C.SELECT};
                color: {_C.T0};
            }}
            QTableWidget#posTable::item:hover {{
                background: {_C.BG3};
            }}
            QHeaderView::section {{
                background: {_C.BG2};
                color: {_C.T2};
                font-family: {_SANS};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 1.1px;
                text-transform: uppercase;
                border: none;
                border-bottom: 1px solid {_C.BORDER};
                padding: 0 5px;
                min-height: 19px;
            }}

            /* Footer */
            QFrame#footer {{
                background: {_C.BG4};
                border-top: 1px solid {_C.BORDER};
            }}
            QLabel#footerLabel {{
                color: {_C.T2};
                font-family: {_SANS};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 0.9px;
                background: transparent;
            }}
            QLabel[objectName^="footerVal"] {{
                color: {_C.T1};
                font-family: {_NUM};
                font-size: 10px;
                font-weight: 800;
                background: transparent;
            }}

            /* Context menu */
            QMenu#posCtxMenu {{
                background: {_C.BG1};
                border: 1px solid {_C.BORDER};
                border-radius: 2px;
                padding: 3px 0;
                font-family: {_SANS};
                font-size: 11px;
                color: {_C.T0};
            }}
            QMenu#posCtxMenu::item {{
                padding: 5px 14px;
                background: transparent;
            }}
            QMenu#posCtxMenu::item:selected {{
                background: {_C.SELECT};
                color: {_C.T0};
            }}
            QMenu#posCtxMenu::separator {{
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
                min-height: 18px;
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
                min-width: 18px;
            }}
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {{
                width: 0;
                border: none;
            }}
        """)


# ─────────────────────────────────────────────────────────────────────────────
#  INTEGRATION HELPER — call from QullamaggieWindow
# ─────────────────────────────────────────────────────────────────────────────

def attach_floating_positions(main_window) -> FloatingPositionsDialog:
    """
    Create and wire a FloatingPositionsDialog into an existing QullamaggieWindow.

    Call once after __init__:
        self.floating_positions = attach_floating_positions(self)

    Wires:
      • position_manager.positions_updated → dialog.update_positions
      • positions_table.update_market_data → dialog.update_market_data  (via proxy)
      • dialog.symbol_chart_requested      → candlestick_chart.on_search
      • dialog.exit_position_requested     → main_window._handle_exit_position_request
      • dialog.subscribe_to_market_data    → main_window._subscribe_to_tokens

    The dialog is NOT shown automatically — caller chooses when to show it.
    """
    dlg = FloatingPositionsDialog(parent=main_window)

    # Position data feed
    if hasattr(main_window, 'position_manager'):
        main_window.position_manager.positions_updated.connect(dlg.update_positions)

    # Symbol → chart
    if hasattr(main_window, 'candlestick_chart'):
        dlg.symbol_chart_requested.connect(main_window.candlestick_chart.on_search)

    # Exit handlers
    if hasattr(main_window, '_handle_exit_position_request'):
        dlg.exit_position_requested.connect(main_window._handle_exit_position_request)
    if hasattr(main_window, '_handle_exit_position_request'):
        dlg.exit_half_position_requested.connect(
            lambda sym: main_window._handle_exit_position_request(sym)
        )

    # WS subscription
    if hasattr(main_window, '_subscribe_to_tokens'):
        dlg.subscribe_to_market_data.connect(main_window._subscribe_to_tokens)

    return dlg