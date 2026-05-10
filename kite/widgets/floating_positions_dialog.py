# kite/widgets/floating_positions_dialog.py
"""
FloatingPositionsDialog — TC2000-style floating positions monitor.

Design language:
  • OLED-black foundation (#000000) with deep charcoal panels
  • Monospace numerics only (Consolas / JetBrains Mono) — columns never shift
  • Teal-green (#00d4a8) / crimson (#ff4d6a) for P&L — never raw green/red
  • Heat-map row tinting: profit rows get a subtle teal glow; loss rows get crimson
  • Flash animation on LTP change (50 ms decay)
  • Compact 24 px row height — maximum data density
  • Frameless, draggable, always-on-top with optional pin toggle
  • Live P&L footer ribbon with total exposure bar
  • Right-click context menu: chart, exit full, exit half
  • Keyboard: Space = next position symbol in chart; Del = exit dialog focus

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
    # Backgrounds
    BG0     = "#000000"   # OLED black — outermost shell
    BG1     = "#080c12"   # dialog body
    BG2     = "#0d1219"   # table rows
    BG3     = "#111926"   # header / footer
    BORDER  = "#1a2535"
    BORDER2 = "#243040"

    # Signals
    BULL    = "#00d4a8"   # teal-green (TC2000 signature)
    BULL_DIM= "#1a7a62"
    BULL_BG = "#0a2520"
    BEAR    = "#ff4d6a"   # warm crimson — NOT pure red
    BEAR_DIM= "#7a2030"
    BEAR_BG = "#200a10"
    FLAT    = "#7a94b0"

    # Text
    T0      = "#e8f0ff"   # primary values
    T1      = "#a8bcd4"   # secondary labels
    T2      = "#5a7090"   # muted / axes
    T3      = "#2a3a50"   # disabled

    # Accent
    CYAN    = "#00d4ff"
    AMBER   = "#f59e0b"
    BLUE    = "#3b82f6"

    # Flash
    FLASH_UP = "#1f6a42"
    FLASH_DN = "#6a1f2a"

_MONO = "\"Consolas\", \"JetBrains Mono\", \"Courier New\", monospace"
_SANS = "\"-apple-system\", \"Segoe UI\", Roboto, sans-serif"


# ─────────────────────────────────────────────────────────────────────────────
#  COLUMN CONFIG
# ─────────────────────────────────────────────────────────────────────────────

_COLS = [
    ("Symbol",  "symbol",   110, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
    ("Qty",     "quantity",  56, Qt.AlignmentFlag.AlignCenter),
    ("Avg",     "avg_price", 76, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
    ("LTP",     "ltp",       76, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
    ("P&L",     "pnl",       90, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
    ("Chg%",    "chg_pct",   64, Qt.AlignmentFlag.AlignCenter),
]

_COL_IDX = {name: i for i, (name, *_) in enumerate(_COLS)}

_FLASH_DURATION = 400   # ms
_REDRAW_INTERVAL = 200  # ms  (~5 fps — human-readable)


_FLOATING_POS_STATE_KEY = "floating_positions_dialog"
_DEFAULT_DIALOG_SIZE = QSize(560, 360)

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
    """Full-width gradient bar: red left → amber mid → teal right."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(3)
        self._pct = 0.5

    def set_pct(self, v: float):
        self._pct = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.width(), self.height()
        # track
        p.fillRect(0, 0, w, h, QColor(_C.BG2))
        # gradient fill
        g = QLinearGradient(0, 0, w, 0)
        g.setColorAt(0.0, QColor(_C.BEAR))
        g.setColorAt(0.5, QColor(_C.AMBER))
        g.setColorAt(1.0, QColor(_C.BULL))
        fill_w = int(w * self._pct)
        p.fillRect(0, 0, fill_w, h, QBrush(g))
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
        self.setMinimumSize(400, 180)
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
        bar.setFixedHeight(30)
        bar.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))

        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 0, 6, 0)
        h.setSpacing(6)

        # Dot indicator + title
        self._dot = QLabel("●")
        self._dot.setObjectName("dotIndicator")
        self._dot.setFixedWidth(10)

        title = QLabel("POSITIONS")
        title.setObjectName("barTitle")

        h.addWidget(self._dot)
        h.addWidget(title)
        h.addStretch()

        # Right controls
        self._pin_btn = QToolButton()
        self._pin_btn.setObjectName("barBtn")
        self._pin_btn.setText("📌")
        self._pin_btn.setToolTip("Toggle always-on-top")
        self._pin_btn.setFixedSize(22, 22)
        self._pin_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._pin_btn.clicked.connect(self._toggle_pin)

        min_btn = QToolButton()
        min_btn.setObjectName("barBtn")
        min_btn.setText("—")
        min_btn.setFixedSize(22, 22)
        min_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        min_btn.clicked.connect(self.showMinimized)

        close_btn = QToolButton()
        close_btn.setObjectName("closeBtn")
        close_btn.setText("✕")
        close_btn.setFixedSize(22, 22)
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
        t.verticalHeader().setDefaultSectionSize(24)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setShowGrid(False)
        t.setAlternatingRowColors(False)
        t.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        t.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        t.setSortingEnabled(False)

        t.customContextMenuRequested.connect(self._ctx_menu)
        t.cellDoubleClicked.connect(self._on_double_click)
        t.cellClicked.connect(self._on_cell_click)
        return t

    def _build_footer(self) -> QFrame:
        f = QFrame()
        f.setObjectName("footer")
        f.setFixedHeight(36)

        h = QHBoxLayout(f)
        h.setContentsMargins(10, 0, 10, 0)
        h.setSpacing(16)

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
        bw_lay.setContentsMargins(0, 16, 0, 16)
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
        }
        cfg.save_window_state(state)

    def _restore_window_state(self):
        saved = self._load_state()
        width = int(saved.get("width", _DEFAULT_DIALOG_SIZE.width()))
        height = int(saved.get("height", _DEFAULT_DIALOG_SIZE.height()))
        self.resize(max(self.minimumWidth(), width), max(self.minimumHeight(), height))

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
        self._dot.setStyleSheet(
            f"color: {'#00d4a8' if new_data else '#2a3a50'}; background: transparent;"
        )

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

    def _write_row(self, row: int, pos: _PosRow):
        if row >= self.table.rowCount():
            return

        is_long  = pos.quantity > 0
        pnl_pos  = pos.pnl > 0
        pnl_neg  = pos.pnl < 0
        pnl_col  = _C.BULL if pnl_pos else (_C.BEAR if pnl_neg else _C.FLAT)
        qty_col  = _C.BULL if is_long else _C.BEAR
        row_bg   = QColor(_C.BULL_BG) if pnl_pos else (QColor(_C.BEAR_BG) if pnl_neg else QColor(_C.BG2))

        qty_sign = "+" if is_long else "−"
        chg_sign = "+" if pos.chg_pct >= 0 else ""
        pnl_sign = "+" if pos.pnl >= 0 else ""

        values = [
            (pos.symbol,                                       _C.T0,   True),
            (f"{qty_sign}{abs(pos.quantity)}",                 qty_col, False),
            (f"{pos.avg_price:,.2f}",                          _C.T1,   False),
            (f"{pos.ltp:,.2f}" if pos.ltp > 0 else "—",       _C.T0,   False),
            (f"{pnl_sign}{pos.pnl:,.2f}",                     pnl_col, True),
            (f"{chg_sign}{pos.chg_pct:.2f}%",                 pnl_col, False),
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

            font = QFont("Consolas, JetBrains Mono, Courier New")
            font.setPointSize(9)
            font.setBold(bold)
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
                    if flash.direction > 0:
                        r = int(10 + (30 - 10) * (1 - ratio))
                        g = int(80 * ratio)
                        b = int(60 * ratio)
                    else:
                        r = int(100 * ratio)
                        g = 10
                        b = int(10 + (40 - 10) * (1 - ratio))
                    item.setBackground(QBrush(QColor(r, g, b, 220)))
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
            f"color: {pnl_col}; font-family: {_MONO}; font-size: 12px;"
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

        menu = QMenu(self)
        menu.setObjectName("posCtxMenu")

        chart_act = menu.addAction("📈  Open Chart")
        chart_act.triggered.connect(lambda: self.symbol_chart_requested.emit(sym))
        menu.addSeparator()

        exit_act = menu.addAction("✕  Exit Full Position")
        exit_act.triggered.connect(lambda: self.exit_position_requested.emit(sym))

        half_act = menu.addAction("½  Exit Half")
        half_act.triggered.connect(lambda: self.exit_half_position_requested.emit(sym))

        menu.exec(self.table.viewport().mapToGlobal(pos))

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

            /* ── Title bar ─────────────────────────────────────── */
            QFrame#titleBar {{
                background: {_C.BG3};
                border-bottom: 1px solid {_C.BORDER};
                border-radius: 0px;
            }}
            QLabel#dotIndicator {{
                font-size: 8px;
                color: {_C.BULL};
                background: transparent;
                letter-spacing: 0px;
            }}
            QLabel#barTitle {{
                color: {_C.T1};
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 2px;
                background: transparent;
            }}
            QLabel#countBadge {{
                color: {_C.CYAN};
                background: rgba(0,212,255,0.10);
                border: 1px solid rgba(0,212,255,0.20);
                border-radius: 2px;
                font-family: {_MONO};
                font-size: 9px;
                font-weight: 700;
                padding: 0 5px;
                min-width: 18px;
            }}
            QToolButton#barBtn {{
                background: transparent;
                color: {_C.T2};
                border: none;
                font-size: 11px;
                border-radius: 2px;
            }}
            QToolButton#barBtn:hover {{
                background: rgba(255,255,255,0.07);
                color: {_C.T0};
            }}
            QToolButton#barBtn[active="true"] {{
                color: {_C.CYAN};
            }}
            QToolButton#closeBtn {{
                background: transparent;
                color: {_C.T2};
                border: none;
                font-size: 11px;
                border-radius: 2px;
            }}
            QToolButton#closeBtn:hover {{
                background: rgba(255,77,106,0.15);
                color: {_C.BEAR};
            }}

            /* ── Table ─────────────────────────────────────────── */
            QTableWidget#posTable {{
                background: {_C.BG1};
                alternate-background-color: {_C.BG2};
                gridline-color: transparent;
                border: none;
                outline: none;
                selection-background-color: transparent;
                font-family: {_MONO};
                font-size: 12px;
            }}
            QTableWidget#posTable::item {{
                padding: 0 6px;
                border-bottom: 1px solid {_C.BG3};
            }}
            QTableWidget#posTable::item:selected {{
                background-color: #1a2840;
                color: {_C.T0};
            }}
            QTableWidget#posTable::item:hover {{
                background-color: #141c28;
            }}
            QHeaderView::section {{
                background: {_C.BG3};
                color: {_C.T2};
                font-family: {_SANS};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 1.2px;
                text-transform: uppercase;
                border: none;
                border-bottom: 1px solid {_C.BORDER};
                padding: 0 6px;
            }}
            QHeaderView::section:first {{
                padding-left: 10px;
            }}

            /* ── Footer ─────────────────────────────────────────── */
            QFrame#footer {{
                background: transparent;
                border-top: 1px solid {_C.BORDER};
            }}
            QLabel#footerLabel {{
                color: {_C.T2};
                font-family: {_SANS};
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 1px;
                background: transparent;
            }}
            QLabel[objectName^="footerVal"] {{
                color: {_C.T1};
                font-family: {_MONO};
                font-size: 11px;
                font-weight: 700;
                background: transparent;
            }}

            /* ── Context menu ────────────────────────────────────── */
            QMenu#posCtxMenu {{
                background: #0c121e;
                border: 1px solid {_C.BORDER};
                border-radius: 4px;
                padding: 4px 0;
                font-family: {_SANS};
                font-size: 12px;
                color: {_C.T0};
            }}
            QMenu#posCtxMenu::item {{
                padding: 6px 16px;
            }}
            QMenu#posCtxMenu::item:selected {{
                background: #1a2840;
                color: {_C.T0};
            }}
            QMenu#posCtxMenu::separator {{
                height: 1px;
                background: {_C.BORDER};
                margin: 3px 10px;
            }}

            /* Scrollbar */
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
                height: 0; border: none;
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
