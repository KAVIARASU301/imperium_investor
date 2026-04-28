# kite/widgets/positions_table.py
"""
Institutional-grade Positions Table — TC2000 / Bloomberg terminal style.

Upgrades over previous version:
  • LTP column (live comparison vs avg entry)
  • % Change (today's move for each stock)
  • Open P&L in absolute ₹
  • % of Account (position weight / concentration)
  • Cell flash animation on tick update (green up-tick, red down-tick)
  • Delta arrow indicator (▲ / ▼) next to LTP
  • Throttled redraws at ≈5 fps — numbers remain readable
  • Click-to-sort on every column header
  • Pinned aggregation footer (Total P&L, Day P&L, Total Exposure)
  • Right-click context menu (Close, Half-exit, Chart)
  • Double-click row → opens chart
  • Remove explicit "X" button — actions live in context menu
  • Color: muted teal / red, tabular numbers, zero noise
"""

import logging
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from functools import partial

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QHeaderView, QAbstractItemView, QFrame, QMenu, QTableWidget,
    QTableWidgetItem, QSizePolicy, QGraphicsOpacityEffect
)
from PySide6.QtCore import (
    Qt, Signal, Slot, QTimer, QPropertyAnimation, QEasingCurve,
    QSequentialAnimationGroup, Property, QRect
)
from PySide6.QtGui import QColor, QFont, QBrush, QCursor, QAction, QFontMetrics

logger = logging.getLogger(__name__)

# ─── Palette ──────────────────────────────────────────────────────────────────
_BG_BASE    = "#03060c"
_BG_ALT     = "#070b12"
_BG_HEADER  = "#0b1019"
_BG_FOOTER  = "#080d15"
_BG_SEL     = "#1a3350"
_BG_HOVER   = "#0f1a28"
_BORDER     = "#1a2536"
_T0         = "#d8e4f0"   # primary text
_T1         = "#8ea3bc"   # secondary text
_T2         = "#506070"   # muted
_GREEN      = "#26a69a"   # profit / up-tick
_RED        = "#ef5350"   # loss / down-tick
_AMBER      = "#f59e0b"   # neutral / warn
_BLUE       = "#4a9eff"   # accent
_FLASH_UP   = "#1a3d2a"   # cell flash bg up
_FLASH_DN   = "#3d1a1a"   # cell flash bg down
_MONO       = "JetBrains Mono, Consolas, Courier New, monospace"
_SANS       = "Segoe UI, Helvetica Neue, Arial, sans-serif"

# Column indices
COL_SYMBOL  = 0
COL_QTY     = 1
COL_AVG     = 2
COL_LTP     = 3
COL_DAY_CHG = 4
COL_OPEN_PNL= 5
COL_WEIGHT  = 6

HEADERS = ["Symbol", "Qty", "Avg Entry", "LTP", "Day %", "Open P&L", "% Acct"]

# Throttle: refresh table visuals at 250 ms intervals (≈4 fps) to keep numbers readable
_REFRESH_INTERVAL_MS = 250

# Flash duration in ms
_FLASH_DURATION_MS   = 350


@dataclass
class Position:
    """Single live position."""
    symbol:       str
    quantity:     int
    avg_price:    float
    token:        int
    ltp:          float  = 0.0
    pnl:          float  = 0.0
    product:      str    = "MIS"
    # Day change (from previous close to current LTP)
    prev_close:   float  = 0.0
    # Tick direction: +1 up, -1 down, 0 neutral
    tick_dir:     int    = 0
    # Previous LTP (for tick direction detection)
    _prev_ltp:    float  = field(default=0.0, repr=False)


class _FlashCell:
    """Tracks a single cell's flash state."""
    __slots__ = ("row", "col", "direction", "remaining_ms")

    def __init__(self, row: int, col: int, direction: int):
        self.row = row
        self.col = col
        self.direction = direction   # +1 or -1
        self.remaining_ms = _FLASH_DURATION_MS


class PositionsTable(QWidget):
    """
    Institutional Positions Table.

    Signals:
        exit_position_requested(str)           — symbol, full exit
        exit_half_position_requested(str)      — symbol, partial exit
        symbol_selected(str)                   — click → chart
        subscribe_to_market_data(list[int])    — token list
    """

    exit_position_requested      = Signal(str)
    exit_half_position_requested = Signal(str)
    symbol_selected              = Signal(str)
    subscribe_to_market_data     = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)

        # ── State ──────────────────────────────────────────────────────────
        self.positions_data:  Dict[str, Position]  = {}
        self.symbol_to_row:   Dict[str, int]       = {}
        self._token_to_symbol: Dict[int, str]      = {}
        self._subscribed_tokens: set               = set()
        self._account_equity: float                = 1_000_000.0  # default until updated

        # Sort state
        self._sort_col:   int  = COL_SYMBOL
        self._sort_asc:   bool = True

        # Cell flash queue
        self._flashes: List[_FlashCell] = []

        # Pending tick updates (batched)
        self._pending_ticks: Dict[int, float] = {}

        self._color_theme = {
            "enable_table_directional_colors": True,
            "tables": {
                "positive": _GREEN,
                "negative": _RED,
                "neutral": _T1,
                "volume": "#45d4ff",
            },
        }

        self._setup_ui()
        self._apply_styles()

        # Throttled redraw timer
        self._redraw_timer = QTimer(self)
        self._redraw_timer.timeout.connect(self._flush_pending_ticks)
        self._redraw_timer.start(_REFRESH_INTERVAL_MS)

        # Flash decay timer (runs faster to smooth animation)
        self._flash_timer = QTimer(self)
        self._flash_timer.timeout.connect(self._decay_flashes)
        self._flash_timer.start(50)  # 20 fps flash decay

    # ══════════════════════════════════════════════════════════════════════════
    # UI CONSTRUCTION
    # ══════════════════════════════════════════════════════════════════════════

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Table ──────────────────────────────────────────────────────────
        self.table = QTableWidget()
        self._configure_table()
        main_layout.addWidget(self.table, 1)

        # ── Footer ─────────────────────────────────────────────────────────
        main_layout.addWidget(self._build_footer())

        # Signals
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)

    def _configure_table(self):
        self.table.setColumnCount(len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)

        # Behaviour
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setSortingEnabled(False)   # manual sort

        # Row height
        self.table.verticalHeader().setDefaultSectionSize(24)

        # Column widths
        hdr = self.table.horizontalHeader()
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hdr.setSectionResizeMode(COL_SYMBOL, QHeaderView.ResizeMode.Stretch)
        for col in (COL_QTY, COL_AVG, COL_LTP, COL_DAY_CHG, COL_OPEN_PNL, COL_WEIGHT):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setMinimumSectionSize(54)
        hdr.setHighlightSections(False)
        hdr.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        # Bold mono header font
        hdr_font = QFont(_MONO)
        hdr_font.setPixelSize(10)
        hdr_font.setBold(True)
        hdr.setFont(hdr_font)

    def _build_footer(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("positionsFooter")
        frame.setFixedHeight(26)

        lay = QHBoxLayout(frame)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(20)

        def _metric(label: str) -> tuple:
            lbl = QLabel(label)
            lbl.setObjectName("footerLabel")
            val = QLabel("–")
            val.setObjectName("footerValue")
            lay.addWidget(lbl)
            lay.addWidget(val)
            return val

        self._footer_open_pnl  = _metric("Open P&L:")
        self._footer_exposure  = _metric("Exposure:")
        self._footer_positions = _metric("Positions:")
        lay.addStretch()
        return frame

    # ══════════════════════════════════════════════════════════════════════════
    # POSITIONS POPULATION
    # ══════════════════════════════════════════════════════════════════════════

    @Slot(list)
    def update_positions(self, positions_list: List[Position]):
        """Receive fresh positions list from PositionManager."""
        self.positions_data  = {p.symbol: p for p in positions_list}
        self.symbol_to_row   = {}
        self._token_to_symbol = {}

        self.table.setRowCount(len(positions_list))

        sorted_positions = self._sort_positions(positions_list)
        for row, pos in enumerate(sorted_positions):
            self.symbol_to_row[pos.symbol] = row
            if pos.token > 0:
                self._token_to_symbol[pos.token] = pos.symbol
            self._populate_row(row, pos)

        self._subscribe_tokens(positions_list)
        self._update_footer()

    def _populate_row(self, row: int, pos: Position):
        for col in range(len(HEADERS)):
            item = QTableWidgetItem()
            self.table.setItem(row, col, item)
        self._refresh_row(row, pos)

    def _refresh_row(self, row: int, pos: Position):
        """Write all cell values for a position row."""
        if row >= self.table.rowCount():
            return

        pnl      = (pos.ltp - pos.avg_price) * pos.quantity
        pos.pnl  = pnl
        is_long  = pos.quantity > 0
        qty_sign = "+" if is_long else "−"

        # Day change %
        day_chg_pct = 0.0
        prev_close = float(getattr(pos, "prev_close", 0.0) or 0.0)
        if prev_close > 0 and pos.ltp > 0:
            day_chg_pct = (pos.ltp - prev_close) / prev_close * 100

        # Weight %
        investment = abs(pos.quantity) * pos.avg_price
        weight_pct = (investment / self._account_equity * 100) if self._account_equity > 0 else 0.0

        # Tick direction delta label
        tick_arrow = "▲" if pos.tick_dir > 0 else ("▼" if pos.tick_dir < 0 else " ")
        tick_col   = _GREEN if pos.tick_dir > 0 else (_RED if pos.tick_dir < 0 else _T2)

        cells = [
            (COL_SYMBOL,  pos.symbol,                            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,  _T0),
            (COL_QTY,     f"{qty_sign}{abs(pos.quantity):,}",    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, _GREEN if is_long else _RED),
            (COL_AVG,     f"₹{pos.avg_price:,.2f}",              Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, _T1),
            (COL_LTP,     f"{tick_arrow} ₹{pos.ltp:,.2f}",       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, tick_col),
            (COL_DAY_CHG, f"{day_chg_pct:+.2f}%",               Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, _GREEN if day_chg_pct >= 0 else _RED),
            (COL_OPEN_PNL,f"{'+'if pnl>=0 else ''}₹{pnl:,.2f}", Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, _GREEN if pnl >= 0 else _RED),
            (COL_WEIGHT,  f"{weight_pct:.1f}%",                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                          _AMBER if weight_pct > 20 else _T1),
        ]

        mono_font = QFont(_MONO)
        mono_font.setPixelSize(11)
        mono_font.setBold(False)

        sym_font = QFont(_MONO)
        sym_font.setPixelSize(11)
        sym_font.setBold(True)

        for col, text, align, color in cells:
            item = self.table.item(row, col)
            if not item:
                continue
            item.setText(text)
            item.setTextAlignment(align)
            item.setForeground(QColor(color))
            item.setFont(sym_font if col == COL_SYMBOL else mono_font)
            item.setData(Qt.ItemDataRole.UserRole, self._sort_key(col, pos))

        # P&L tint background on open P&L cell
        pnl_item = self.table.item(row, COL_OPEN_PNL)
        if pnl_item:
            if pnl > 0:
                pnl_item.setBackground(QBrush(QColor(18, 55, 34, 160)))
            elif pnl < 0:
                pnl_item.setBackground(QBrush(QColor(70, 20, 20, 160)))
            else:
                pnl_item.setBackground(QBrush(QColor(20, 28, 42, 120)))

        # Weight warning tint
        w_item = self.table.item(row, COL_WEIGHT)
        if w_item:
            if weight_pct > 30:
                w_item.setBackground(QBrush(QColor(80, 50, 0, 120)))
            else:
                w_item.setBackground(QBrush(QColor(0, 0, 0, 0)))

    @staticmethod
    def _sort_key(col: int, pos: Position):
        pnl = (pos.ltp - pos.avg_price) * pos.quantity
        prev_close = float(getattr(pos, "prev_close", 0.0) or 0.0)
        day_chg = (pos.ltp - prev_close) / prev_close * 100 if prev_close > 0 else 0
        mapping = {
            COL_SYMBOL:   pos.symbol,
            COL_QTY:      pos.quantity,
            COL_AVG:      pos.avg_price,
            COL_LTP:      pos.ltp,
            COL_DAY_CHG:  day_chg,
            COL_OPEN_PNL: pnl,
            COL_WEIGHT:   abs(pos.quantity) * pos.avg_price,
        }
        return mapping.get(col, 0)

    # ══════════════════════════════════════════════════════════════════════════
    # LIVE TICK UPDATE — throttled + cell flash
    # ══════════════════════════════════════════════════════════════════════════

    @Slot(int, float)
    def update_market_data(self, token: int, ltp: float):
        """O(1) tick path — batch into pending dict, flush at 4fps."""
        self._pending_ticks[token] = ltp

    def _flush_pending_ticks(self):
        """Called every 250ms — apply batched ticks and redraw affected rows."""
        if not self._pending_ticks:
            return

        for token, ltp in self._pending_ticks.items():
            symbol = self._token_to_symbol.get(token)
            if not symbol:
                continue
            pos = self.positions_data.get(symbol)
            if not pos:
                continue

            prev_ltp  = pos.ltp
            pos._prev_ltp = prev_ltp
            pos.ltp   = ltp
            pos.pnl   = (ltp - pos.avg_price) * pos.quantity

            # Tick direction
            if ltp > prev_ltp:
                pos.tick_dir = 1
            elif ltp < prev_ltp:
                pos.tick_dir = -1

            row = self.symbol_to_row.get(symbol)
            if row is not None:
                self._refresh_row(row, pos)
                # Queue flash on LTP cell
                if prev_ltp > 0 and ltp != prev_ltp:
                    direction = 1 if ltp > prev_ltp else -1
                    self._flashes.append(_FlashCell(row, COL_LTP, direction))

        self._pending_ticks.clear()
        self._update_footer()

    # ── Cell Flash ─────────────────────────────────────────────────────────

    def _decay_flashes(self):
        """Decay flash backgrounds every 50ms."""
        if not self._flashes:
            return

        surviving = []
        for flash in self._flashes:
            flash.remaining_ms -= 50
            item = self.table.item(flash.row, flash.col)
            if item:
                ratio = max(0.0, flash.remaining_ms / _FLASH_DURATION_MS)
                if ratio > 0:
                    if flash.direction > 0:
                        # up-tick: green flash
                        r = int(18 + (0 - 18) * (1 - ratio))
                        g = int(80 + (55 - 80) * (1 - ratio))
                        b = int(40 + (34 - 40) * (1 - ratio))
                    else:
                        # down-tick: red flash
                        r = int(120 + (70 - 120) * (1 - ratio))
                        g = int(20 + (20 - 20) * (1 - ratio))
                        b = int(20 + (20 - 20) * (1 - ratio))
                    item.setBackground(QBrush(QColor(r, g, b, 200)))
                    surviving.append(flash)
                else:
                    # Restore PnL tint
                    pos_sym = None
                    for sym, row in self.symbol_to_row.items():
                        if row == flash.row:
                            pos_sym = sym
                            break
                    if pos_sym:
                        pos = self.positions_data.get(pos_sym)
                        if pos:
                            self._refresh_row(flash.row, pos)

        self._flashes = surviving

    # ══════════════════════════════════════════════════════════════════════════
    # SORTING
    # ══════════════════════════════════════════════════════════════════════════

    def _on_header_clicked(self, logical_idx: int):
        if self._sort_col == logical_idx:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = logical_idx
            self._sort_asc = logical_idx == COL_SYMBOL   # alpha asc by default for symbol
        self._re_sort()

    def _re_sort(self):
        positions = list(self.positions_data.values())
        sorted_list = self._sort_positions(positions)
        self.symbol_to_row = {}
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(sorted_list))
        for row, pos in enumerate(sorted_list):
            self.symbol_to_row[pos.symbol] = row
            self._populate_row(row, pos)
        self._update_footer()

    def _sort_positions(self, positions: List[Position]) -> List[Position]:
        def key_fn(pos: Position):
            return self._sort_key(self._sort_col, pos)
        return sorted(positions, key=key_fn, reverse=not self._sort_asc)

    # ══════════════════════════════════════════════════════════════════════════
    # FOOTER
    # ══════════════════════════════════════════════════════════════════════════

    def _update_footer(self):
        if not self.positions_data:
            for w in (self._footer_open_pnl, self._footer_exposure, self._footer_positions):
                w.setText("–")
            return

        total_pnl  = sum(p.pnl for p in self.positions_data.values())
        exposure   = sum(abs(p.quantity) * p.avg_price for p in self.positions_data.values())
        n          = len(self.positions_data)

        pnl_color  = _GREEN if total_pnl >= 0 else _RED
        sign       = "+" if total_pnl >= 0 else ""

        self._footer_open_pnl.setText(f"{sign}₹{total_pnl:,.2f}")
        self._footer_open_pnl.setStyleSheet(
            f"color:{pnl_color};font-family:'{_MONO}';font-size:11px;font-weight:700;background:transparent;"
        )
        self._footer_exposure.setText(f"₹{exposure:,.0f}")
        self._footer_positions.setText(str(n))

    # ══════════════════════════════════════════════════════════════════════════
    # EVENTS
    # ══════════════════════════════════════════════════════════════════════════

    def _on_cell_clicked(self, row: int, col: int):
        symbol = self._symbol_at_row(row)
        if symbol:
            self.symbol_selected.emit(symbol)

    def _on_cell_double_clicked(self, row: int, col: int):
        """Double-click also triggers chart — same as single click, more explicit."""
        self._on_cell_clicked(row, col)

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        symbol = self._symbol_at_row(row)
        position = self.positions_data.get(symbol) if symbol else None
        if not symbol or not position:
            return

        menu = QMenu(self)
        menu.setObjectName("posContextMenu")

        pnl_text = f"{'+'if position.pnl>=0 else ''}₹{position.pnl:,.2f}"
        info = menu.addAction(f"  {symbol}  ·  {pnl_text}")
        info.setEnabled(False)
        menu.addSeparator()

        chart_action = menu.addAction("📈  Open Chart")
        chart_action.triggered.connect(lambda: self.symbol_selected.emit(symbol))

        menu.addSeparator()

        close_action = menu.addAction("✕  Close Position (Market)")
        close_action.triggered.connect(lambda: self.exit_position_requested.emit(symbol))

        half_action = menu.addAction("½  Exit Half Position")
        half_action.triggered.connect(lambda: self.exit_half_position_requested.emit(symbol))

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _symbol_at_row(self, row: int) -> Optional[str]:
        for sym, r in self.symbol_to_row.items():
            if r == row:
                return sym
        return None

    # ══════════════════════════════════════════════════════════════════════════
    # SUBSCRIPTIONS
    # ══════════════════════════════════════════════════════════════════════════

    def _subscribe_tokens(self, positions: List[Position]):
        tokens = [p.token for p in positions if p.token > 0]
        new_tokens = [t for t in tokens if t not in self._subscribed_tokens]
        if new_tokens:
            self.subscribe_to_market_data.emit(new_tokens)
            self._subscribed_tokens.update(new_tokens)

    # ══════════════════════════════════════════════════════════════════════════
    # PUBLIC HELPERS
    # ══════════════════════════════════════════════════════════════════════════

    def set_account_equity(self, equity: float):
        """Feed account balance so % weight is calculated correctly."""
        self._account_equity = max(1.0, equity)

    def apply_color_theme(self, theme: Dict):
        self._color_theme = theme or self._color_theme
        for sym, row in self.symbol_to_row.items():
            pos = self.positions_data.get(sym)
            if pos:
                self._refresh_row(row, pos)
        self._update_footer()

    def get_total_pnl(self) -> float:
        return sum(p.pnl for p in self.positions_data.values())

    def has_positions(self) -> bool:
        return bool(self.positions_data)

    def clear_positions(self):
        self.positions_data.clear()
        self.symbol_to_row.clear()
        self._token_to_symbol.clear()
        self._subscribed_tokens.clear()
        self._flashes.clear()
        self._pending_ticks.clear()
        self.table.setRowCount(0)
        self._update_footer()

    # ══════════════════════════════════════════════════════════════════════════
    # STYLES
    # ══════════════════════════════════════════════════════════════════════════

    def _apply_styles(self):
        self.setStyleSheet(f"""
            /* ── Widget shell ──────────────────────────────────────────── */
            QWidget {{
                background:{_BG_BASE};
                color:{_T0};
                font-family:'{_SANS}';
                font-size:12px;
            }}

            /* ── Main table ────────────────────────────────────────────── */
            QTableWidget {{
                background:{_BG_BASE};
                alternate-background-color:{_BG_ALT};
                gridline-color:transparent;
                border:1px solid {_BORDER};
                border-radius:0px;
                outline:none;
                show-decoration-selected:0;
                selection-background-color:transparent;
            }}

            QTableWidget::item {{
                padding:1px 8px;
                border-bottom:1px solid {_BORDER};
                background:transparent;
            }}

            QTableWidget::item:selected {{
                background:{_BG_SEL} !important;
                color:{_T0};
            }}

            QTableWidget::item:focus {{
                background:{_BG_SEL} !important;
                outline:none;
            }}

            QTableWidget::item:alternate {{
                background:{_BG_ALT};
            }}

            QTableWidget::item:alternate:selected {{
                background:{_BG_SEL} !important;
            }}

            /* ── Header ────────────────────────────────────────────────── */
            QHeaderView {{
                background:{_BG_HEADER};
                border:none;
            }}

            QHeaderView::section {{
                background:{_BG_HEADER};
                color:{_BLUE};
                padding:3px 8px;
                border:none;
                border-bottom:1px solid {_BORDER};
                border-right:1px solid {_BORDER};
                font-family:'{_MONO}';
                font-size:10px;
                font-weight:700;
                letter-spacing:0.8px;
            }}

            QHeaderView::section:last {{
                border-right:none;
            }}

            QHeaderView::section:hover {{
                background:#111d2c;
                color:{_T0};
            }}

            QHeaderView::down-arrow,
            QHeaderView::up-arrow {{
                width:0px;
                height:0px;
            }}

            /* ── Footer ────────────────────────────────────────────────── */
            #positionsFooter {{
                background:{_BG_FOOTER};
                border-top:1px solid {_BORDER};
            }}

            #footerLabel {{
                color:{_T2};
                font-family:'{_MONO}';
                font-size:10px;
                font-weight:700;
                letter-spacing:0.8px;
                background:transparent;
            }}

            #footerValue {{
                color:{_T1};
                font-family:'{_MONO}';
                font-size:11px;
                font-weight:600;
                background:transparent;
                min-width:80px;
            }}

            /* ── Context menu ───────────────────────────────────────────── */
            QMenu#posContextMenu {{
                background:#0c121e;
                border:1px solid {_BORDER};
                border-radius:4px;
                padding:4px 0;
                font-family:'{_SANS}';
                font-size:12px;
                color:{_T0};
            }}
            QMenu#posContextMenu::item {{
                padding:7px 18px;
            }}
            QMenu#posContextMenu::item:selected {{
                background:#1a2840;
                color:{_T0};
            }}
            QMenu#posContextMenu::item:disabled {{
                color:{_T2};
            }}
            QMenu#posContextMenu::separator {{
                height:1px;
                background:{_BORDER};
                margin:3px 0;
            }}

            /* ── Scrollbars ─────────────────────────────────────────────── */
            QScrollBar:vertical {{
                background:transparent;
                width:5px;
                border:none;
            }}
            QScrollBar::handle:vertical {{
                background:#2a3850;
                border-radius:2px;
                min-height:20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background:#4a6888;
            }}
            QScrollBar:horizontal {{
                background:transparent;
                height:5px;
                border:none;
            }}
            QScrollBar::handle:horizontal {{
                background:#2a3850;
                border-radius:2px;
            }}
            QScrollBar::add-line,QScrollBar::sub-line {{
                border:none;background:none;width:0px;height:0px;
            }}
        """)
