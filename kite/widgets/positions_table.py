# kite/widgets/positions_table.py
"""
Institutional-grade Positions Table — Ultra-Compact Mode.

Upgrades:
  • Strictly 3 columns: Symbol, Qty, PnL
  • Modern UI number typography for Qty, %Chg, footer P&L and Exposure
  • Compact symbol typography matched with watchlist/scanner tables
  • Compact gridless headers and cell paddings to minimize horizontal space
  • Zero visual noise (no currency symbols inline)
  • Flash animations and quick sorting retained
"""

import json
import logging
import os
from typing import List, Dict, Optional, Set
from dataclasses import dataclass

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QHeaderView, QAbstractItemView, QFrame, QMenu, QTableWidget,
    QTableWidgetItem
)
from PySide6.QtCore import (
    Qt, Signal, Slot, QTimer, QSignalBlocker, QEvent
)
from PySide6.QtGui import QColor, QFont, QBrush, QCursor

logger = logging.getLogger(__name__)

# ─── Institutional Dark Trading Terminal UI Tokens ─────────────────────────────
# Softer terminal palette: still dark/compact, but less neon and easier to read.
_BG_APP = "#05070a"
_BG_BASE = "#0a0d11"
_BG_ALT = "#0e1217"
_BG_HEADER = "#0c1015"
_BG_FOOTER = "#070a0e"
_BG_SEL = "#182436"
_BG_HOVER = "#121821"
_BORDER = "#202838"

_T0 = "#d9e2ee"   # primary text — readable, not pure white
_SYMBOL_TEXT = "#afbdcc"  # calm symbol text
_T1 = "#9eb0c2"   # secondary text
_T2 = "#6f8194"   # muted labels / metadata
_T3 = "#3b4758"   # disabled

_GREEN = "#6fcfb8"   # success / profit / buy-side — softened
_RED = "#ee7580"     # danger / loss / sell-side — softened
_AMBER = "#d6a34d"   # warning / active — softened
_CYAN = "#67c7d8"    # info / utility — softened

_SANS = "'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', -apple-system, BlinkMacSystemFont, Arial, sans-serif"
_NUM = "'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', -apple-system, BlinkMacSystemFont, Arial, sans-serif"
_MONO = "'Consolas', 'JetBrains Mono', monospace"

_SYMBOL_FONT_FAMILIES = ["Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", "Noto Sans", "Arial"]
_UI_FONT_FAMILIES = ["Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", "Noto Sans", "Arial"]
_NUM_FONT_FAMILIES = ["Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", "Noto Sans", "Arial"]

_APP_FONT_FAMILY = _UI_FONT_FAMILIES[0]
_NUM_FONT = _NUM_FONT_FAMILIES[0]

_OPEN_PROFIT = _GREEN
_OPEN_PROFIT_TINT = "#102720"
_OPEN_LOSS = _RED
_OPEN_LOSS_TINT = "#291217"
_OPEN_FLAT = "#7f90a3"

_ROW_H = 22
_HEADER_H = 21
_FOOTER_H = 24


def _apply_font_fallbacks(font: QFont, families: List[str]) -> QFont:
    """Apply real Qt font fallbacks; QFont('Inter, Segoe UI') is not a fallback stack."""
    if hasattr(font, "setFamilies"):
        font.setFamilies(families)
    return font


def _ui_font(point_size: int = 9, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Quiet modern UI font. Monospace stays reserved for raw logs/code only."""
    f = QFont(_APP_FONT_FAMILY)
    _apply_font_fallbacks(f, _UI_FONT_FAMILIES)
    f.setStyleHint(QFont.StyleHint.SansSerif)
    f.setPointSize(point_size)
    f.setWeight(weight)
    f.setKerning(True)
    return f


def _symbol_font(pixel_size: int = 10, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Compact symbol/ticker font matching watchlist and scanner tables."""
    f = QFont(_SYMBOL_FONT_FAMILIES[0])
    _apply_font_fallbacks(f, _SYMBOL_FONT_FAMILIES)
    f.setStyleHint(QFont.StyleHint.SansSerif)
    f.setPixelSize(pixel_size)  # Match QSS px sizing; avoids oversized 9pt rendering.
    f.setWeight(weight)
    f.setKerning(True)
    try:
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 103.0)
    except Exception:
        pass
    return f


# Column indices
COL_FLAG = 0
COL_SYMBOL = 1
COL_QTY = 2
COL_OPEN_PNL = 3

HEADERS = ["", "Symbol", "Qty", "%Chg"]

_FLAG_CYCLE = [None, "green"]
_FLAG_DISPLAY = {
    None: ("", _T2),
    "green": ("⚑", _GREEN),
}
_FLAG_TOOLTIP = {
    None: "Click to flag",
    "green": "Flagged — click to remove",
}
_FLAGS_FILE = os.path.join(os.path.expanduser("~"), ".qullamaggie", "watchlist_flags.json")


class _FlagStore:
    def __init__(self):
        self._flags: Dict[str, Optional[str]] = {}
        self._load()

    def get(self, symbol: str) -> Optional[str]:
        return self._flags.get(symbol.upper())

    def cycle(self, symbol: str) -> Optional[str]:
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

    def _load(self):
        try:
            if os.path.exists(_FLAGS_FILE):
                with open(_FLAGS_FILE, "r") as f:
                    self._flags = json.load(f)
        except Exception as e:
            logger.error(f"Positions flag store load failed: {e}")

    def _save(self):
        try:
            os.makedirs(os.path.dirname(_FLAGS_FILE), exist_ok=True)
            with open(_FLAGS_FILE, "w") as f:
                json.dump(self._flags, f, indent=2)
        except Exception as e:
            logger.error(f"Positions flag store save failed: {e}")


_flag_store = _FlagStore()

# Throttle: refresh table visuals at 250 ms intervals (≈4 fps)
_REFRESH_INTERVAL_MS = 250
_FLASH_DURATION_MS = 350


@dataclass
class Position:
    """Single live position."""
    symbol: str
    quantity: int
    avg_price: float
    token: int
    ltp: float = 0.0
    pnl: float = 0.0
    product: str = "MIS"
    prev_close: float = 0.0
    is_partial_building: bool = False

    @classmethod
    def from_kite_position(cls, pos_data: Dict) -> "Position":
        return cls(
            symbol=pos_data.get("tradingsymbol", ""),
            quantity=int(pos_data.get("quantity", 0) or 0),
            avg_price=float(pos_data.get("average_price", 0) or 0),
            token=int(pos_data.get("instrument_token", 0) or 0),
            ltp=float(pos_data.get("last_price", 0) or 0),
            product=pos_data.get("product") or pos_data.get("product_type") or "MIS",
        )


class _FlashCell:
    """Tracks a single cell's flash state."""
    __slots__ = ("row", "col", "direction", "remaining_ms")

    def __init__(self, row: int, col: int, direction: int):
        self.row = row
        self.col = col
        self.direction = direction  # +1 or -1
        self.remaining_ms = _FLASH_DURATION_MS


class PositionsTable(QWidget):
    """Compact Institutional Positions Table."""

    exit_position_requested = Signal(str)
    exit_half_position_requested = Signal(str)
    symbol_selected = Signal(str)
    subscribe_to_market_data = Signal(list)
    footer_metrics_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.positions_data: Dict[str, Position] = {}
        self.symbol_to_row: Dict[str, int] = {}
        self._token_to_symbol: Dict[int, str] = {}
        self._subscribed_tokens: set = set()
        self._partial_fill_symbols: Set[str] = set()

        self._tick_dirs: Dict[str, int] = {}
        self._prev_ltps: Dict[str, float] = {}

        self._sort_col: int = COL_SYMBOL
        self._sort_asc: bool = True
        self._flashes: List[_FlashCell] = []
        self._pending_ticks: Dict[int, float] = {}

        self._color_theme: Dict = {
            "enable_table_directional_colors": False,
            "show_table_vertical_lines": False,
            "tables": {
                "positive": _GREEN,
                "negative": _RED,
                "neutral": _T2,
            },
        }

        self._setup_ui()
        self._apply_styles()

        self._redraw_timer = QTimer(self)
        self._redraw_timer.timeout.connect(self._flush_pending_ticks)
        self._redraw_timer.start(_REFRESH_INTERVAL_MS)

        self._flash_timer = QTimer(self)
        self._flash_timer.timeout.connect(self._decay_flashes)
        self._flash_timer.start(50)

        # ══════════════════════════════════════════════════════════════════════════

    # UI CONSTRUCTION
    # ══════════════════════════════════════════════════════════════════════════

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.table = QTableWidget()
        self._configure_table()
        main_layout.addWidget(self.table, 1)

        self._footer_frame = self._build_footer()
        main_layout.addWidget(self._footer_frame)

        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)

    def _configure_table(self):
        self.table.setColumnCount(len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)

        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(bool(self._color_theme.get("show_table_vertical_lines", False)))
        self.table.setAlternatingRowColors(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setSortingEnabled(False)

        self.table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self.table.verticalHeader().setMinimumSectionSize(_ROW_H)
        self.table.setWordWrap(False)

        hdr = self.table.horizontalHeader()
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.setFixedHeight(_HEADER_H)
        hdr.setSectionResizeMode(COL_FLAG, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(COL_FLAG, 20)
        hdr.setSectionResizeMode(COL_SYMBOL, QHeaderView.ResizeMode.Stretch)
        for col in (COL_QTY, COL_OPEN_PNL):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)

        self._apply_dynamic_column_widths()

        hdr.setMinimumSectionSize(20)
        hdr.setHighlightSections(False)
        hdr.setFont(_ui_font(8, QFont.Weight.Medium))
        hdr.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self.table.viewport().installEventFilter(self)


    def _apply_dynamic_column_widths(self) -> None:
        """Size Qty and %Chg to remain visually aligned with watchlist numeric columns."""
        flag_w = 20
        min_symbol_w = 96
        min_data_w = 62
        max_data_w = 120

        viewport_w = max(self.table.viewport().width(), 0)
        available_for_data = max(0, viewport_w - flag_w - min_symbol_w)
        data_w = max(min_data_w, min(max_data_w, available_for_data // 2))

        self.table.setColumnWidth(COL_QTY, data_w)
        self.table.setColumnWidth(COL_OPEN_PNL, data_w)

    def _build_footer(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("positionsFooter")
        frame.setFixedHeight(_FOOTER_H)

        lay = QHBoxLayout(frame)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(15)

        def _metric(label: str) -> tuple:
            lbl = QLabel(label)
            lbl.setObjectName("footerLabel")
            val = QLabel("–")
            val.setObjectName("footerValue")
            lay.addWidget(lbl)
            lay.addWidget(val)
            return val

        self._footer_open_pnl = _metric("OPEN P&L")
        self._footer_exposure = _metric("EXPOSURE")
        lay.addStretch()
        return frame

    def resizeEvent(self, event):
        super().resizeEvent(event)
        with QSignalBlocker(self.table.horizontalHeader()):
            self.table.setColumnWidth(COL_FLAG, 20)
            self._apply_dynamic_column_widths()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_columns_after_layout)

    def eventFilter(self, watched, event):
        if watched is self.table.viewport() and event.type() == QEvent.Type.Resize:
            self._fit_columns_after_layout()
        return super().eventFilter(watched, event)

    def _fit_columns_after_layout(self) -> None:
        """Re-apply fixed column widths after Qt settles initial layout geometry."""
        with QSignalBlocker(self.table.horizontalHeader()):
            self.table.setColumnWidth(COL_FLAG, 20)
            self._apply_dynamic_column_widths()

    # ══════════════════════════════════════════════════════════════════════════
    # POSITIONS POPULATION
    # ══════════════════════════════════════════════════════════════════════════

    @Slot(list)
    def update_positions(self, positions_list: List[Position]):
        self.positions_data = {p.symbol: p for p in positions_list}
        self.symbol_to_row = {}
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
            self.table.setItem(row, col, QTableWidgetItem())
        self._refresh_row(row, pos)

    def _refresh_row(self, row: int, pos: Position):
        if row >= self.table.rowCount():
            return

        pnl = (pos.ltp - pos.avg_price) * pos.quantity
        pos.pnl = pnl
        entry_change_pct = self._entry_change_pct(pos)
        is_long = pos.quantity > 0
        qty_sign = "+" if is_long else "−"

        table_colors = self._color_theme.get("tables", {})
        profit_color = table_colors.get("positive", _GREEN)
        loss_color = table_colors.get("negative", _RED)

        change_color = self._open_pnl_text_color(entry_change_pct)

        symbol_font = _symbol_font(10, QFont.Weight.Normal)
        number_font = self._number_font(False, 9)
        strong_number_font = self._number_font(True, 9)
        base_bg = QBrush(QColor(_BG_BASE if row % 2 == 0 else _BG_ALT))

        # Notice: No ₹ symbols to save horizontal space
        symbol_text = f"⚡ {pos.symbol}" if pos.symbol in self._partial_fill_symbols else pos.symbol
        cells = [
            (COL_SYMBOL, symbol_text, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, _SYMBOL_TEXT, symbol_font),
            (COL_FLAG, "", Qt.AlignmentFlag.AlignCenter, _T2, symbol_font),
            (COL_QTY, f"{qty_sign}{abs(pos.quantity)}", Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, profit_color if is_long else loss_color, number_font),
            (COL_OPEN_PNL, f"{entry_change_pct:+.2f}%", Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, change_color, strong_number_font),
        ]

        for col, text, align, color, font in cells:
            item = self.table.item(row, col)
            if not item:
                continue
            item.setText(text)
            item.setTextAlignment(align)
            item.setForeground(QColor(color))
            item.setFont(font)

            if col != COL_OPEN_PNL:
                item.setBackground(base_bg)

            item.setData(Qt.ItemDataRole.UserRole, self._sort_key(col, pos))
            if col == COL_SYMBOL:
                if pos.symbol in self._partial_fill_symbols:
                    item.setToolTip("Position building — partial fill in progress")
                else:
                    item.setToolTip("")

        self._paint_flag_cell(row, pos.symbol)
        self._apply_open_pnl_row_style(row, entry_change_pct)


    @Slot(object)
    def mark_partial_symbols(self, symbols):
        """Mark position rows whose entry order is still partially filling."""
        partial_symbols = {str(symbol) for symbol in (symbols or set()) if symbol}
        if partial_symbols == self._partial_fill_symbols:
            return

        affected_symbols = self._partial_fill_symbols | partial_symbols
        self._partial_fill_symbols = partial_symbols

        for symbol in affected_symbols:
            row = self.symbol_to_row.get(symbol)
            pos = self.positions_data.get(symbol)
            if row is not None and pos:
                self._refresh_row(row, pos)


    @staticmethod
    def _entry_change_pct(pos: Position) -> float:
        if not pos.avg_price:
            return 0.0
        return ((pos.ltp - pos.avg_price) / pos.avg_price) * 100.0

    def _open_pnl_text_color(self, pnl: float) -> str:
        if pnl > 0:
            return _OPEN_PROFIT
        if pnl < 0:
            return _OPEN_LOSS
        return _OPEN_FLAT

    def _apply_open_pnl_row_style(self, row: int, pnl: float) -> None:
        if pnl > 0:
            fg = QColor(_OPEN_PROFIT)
            bg = QBrush(QColor(111, 207, 184, 18))
        elif pnl < 0:
            fg = QColor(_OPEN_LOSS)
            bg = QBrush(QColor(238, 117, 128, 18))
        else:
            fg = QColor(_OPEN_FLAT)
            bg = QBrush(QColor(_BG_BASE if row % 2 == 0 else _BG_ALT))

        pnl_item = self.table.item(row, COL_OPEN_PNL)
        if pnl_item:
            pnl_item.setForeground(QBrush(fg))
            pnl_item.setBackground(bg)

    def _sort_key(self, col: int, pos: Position):
        pnl = (pos.ltp - pos.avg_price) * pos.quantity
        mapping = {
            COL_SYMBOL: pos.symbol,
            COL_QTY: pos.quantity,
            COL_OPEN_PNL: self._entry_change_pct(pos),
        }
        return mapping.get(col, 0)

    # ══════════════════════════════════════════════════════════════════════════
    # LIVE TICK UPDATE
    # ══════════════════════════════════════════════════════════════════════════

    @Slot(int, float)
    def update_market_data(self, token: int, ltp: float):
        try:
            token = int(token)
            ltp = float(ltp)
        except (TypeError, ValueError):
            return
        if token <= 0 or ltp <= 0:
            return
        self._pending_ticks[token] = ltp

    def _flush_pending_ticks(self):
        if not self._pending_ticks:
            return

        for token, ltp in self._pending_ticks.items():
            symbol = self._token_to_symbol.get(token)
            if not symbol: continue
            pos = self.positions_data.get(symbol)
            if not pos: continue

            prev_ltp = self._prev_ltps.get(symbol, pos.ltp)
            self._prev_ltps[symbol] = ltp

            pos.ltp = ltp
            pos.pnl = (ltp - pos.avg_price) * pos.quantity

            if ltp > prev_ltp:
                self._tick_dirs[symbol] = 1
            elif ltp < prev_ltp:
                self._tick_dirs[symbol] = -1
            else:
                self._tick_dirs[symbol] = 0

            row = self.symbol_to_row.get(symbol)
            if row is not None:
                self._refresh_row(row, pos)
                if prev_ltp > 0 and ltp != prev_ltp:
                    direction = 1 if ltp > prev_ltp else -1
                    self._flashes.append(_FlashCell(row, COL_OPEN_PNL, direction))

        self._pending_ticks.clear()
        self._update_footer()

    def _decay_flashes(self):
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
                        r = int(24 + (16 - 24) * (1 - ratio))
                        g = int(74 + (44 - 74) * (1 - ratio))
                        b = int(55 + (42 - 55) * (1 - ratio))
                    else:
                        r = int(92 + (52 - 92) * (1 - ratio))
                        g = int(38 + (30 - 38) * (1 - ratio))
                        b = int(44 + (35 - 44) * (1 - ratio))
                    item.setBackground(QBrush(QColor(r, g, b)))
                    surviving.append(flash)
                else:
                    pos_sym = next((s for s, r in self.symbol_to_row.items() if r == flash.row), None)
                    if pos_sym and (pos := self.positions_data.get(pos_sym)):
                        self._refresh_row(flash.row, pos)

        self._flashes = surviving

    # ══════════════════════════════════════════════════════════════════════════
    # SORTING & EVENTS
    # ══════════════════════════════════════════════════════════════════════════

    def _on_header_clicked(self, logical_idx: int):
        if self._sort_col == logical_idx:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = logical_idx
            self._sort_asc = logical_idx == COL_SYMBOL
        self._re_sort()

    def _re_sort(self):
        positions = list(self.positions_data.values())
        sorted_list = self._sort_positions(positions)
        self.symbol_to_row.clear()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(sorted_list))
        for row, pos in enumerate(sorted_list):
            self.symbol_to_row[pos.symbol] = row
            self._populate_row(row, pos)
        self._update_footer()

    def _sort_positions(self, positions: List[Position]) -> List[Position]:
        return sorted(positions, key=lambda p: self._sort_key(self._sort_col, p), reverse=not self._sort_asc)

    def _update_footer(self):
        if not self.positions_data:
            self._footer_open_pnl.setText("–")
            self._footer_exposure.setText("–")
            self.footer_metrics_changed.emit(
                {"has_data": False, "open_pnl": 0.0, "exposure": 0.0}
            )
            return

        total_pnl = sum(p.pnl for p in self.positions_data.values())
        exposure = sum(abs(p.quantity) * p.avg_price for p in self.positions_data.values())

        pnl_color = _GREEN if total_pnl >= 0 else _RED
        self._footer_open_pnl.setText(f"{'+' if total_pnl >= 0 else ''}{total_pnl:,.0f}")
        self._footer_open_pnl.setStyleSheet(
            f"color:{pnl_color}; font-family:{_NUM}; font-size:11px; font-weight:500; background:transparent;"
        )
        self._footer_exposure.setText(f"{exposure:,.0f}")
        self._footer_exposure.setStyleSheet(
            f"color:{_T1}; font-family:{_NUM}; font-size:11px; font-weight:500; background:transparent;"
        )
        self.footer_metrics_changed.emit(
            {"has_data": True, "open_pnl": total_pnl, "exposure": exposure}
        )

    def set_footer_metrics_visible(self, visible: bool) -> None:
        self._footer_frame.setVisible(bool(visible))

    def _on_cell_clicked(self, row: int, col: int):
        symbol = self._symbol_at_row(row)
        if not symbol:
            return
        if col == COL_FLAG:
            _flag_store.cycle(symbol)
            self._paint_flag_cell(row, symbol)
            return
        self.symbol_selected.emit(symbol)

    def _on_cell_double_clicked(self, row: int, col: int):
        self._on_cell_clicked(row, col)

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0: return
        symbol = self._symbol_at_row(row)
        if not symbol: return

        menu = QMenu(self)
        menu.setObjectName("posContextMenu")

        flag_state = _flag_store.get(symbol)
        next_states = {None: "⚑  Add Flag", "green": "⚑  Remove Flag"}
        flag_act = menu.addAction(next_states.get(flag_state, "⚑  Toggle Flag"))
        flag_act.triggered.connect(lambda: self._cycle_flag(row, symbol))
        menu.addSeparator()

        chart_act = menu.addAction("Open Chart")
        chart_act.triggered.connect(lambda: self.symbol_selected.emit(symbol))
        menu.addSeparator()

        close_act = menu.addAction("Close Position")
        close_act.triggered.connect(lambda: self.exit_position_requested.emit(symbol))

        half_act = menu.addAction("Exit Half")
        half_act.triggered.connect(lambda: self.exit_half_position_requested.emit(symbol))

        menu.exec(self.table.viewport().mapToGlobal(pos))


    def _paint_flag_cell(self, row: int, symbol: str):
        state = _flag_store.get(symbol)
        glyph, color = _FLAG_DISPLAY[state]
        item = self.table.item(row, COL_FLAG)
        if not item:
            item = QTableWidgetItem()
            self.table.setItem(row, COL_FLAG, item)
        item.setText(glyph)
        item.setForeground(QColor(color))
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setFont(_ui_font(9, QFont.Weight.Normal))
        item.setToolTip(_FLAG_TOOLTIP[state])

    def _cycle_flag(self, row: int, symbol: str):
        _flag_store.cycle(symbol)
        self._paint_flag_cell(row, symbol)

    @staticmethod
    def _number_font(bold: bool = False, point_size: int = 9) -> QFont:
        """Modern UI number font for market values; monospace is reserved for raw logs/code."""
        f = QFont(_NUM_FONT)
        _apply_font_fallbacks(f, _NUM_FONT_FAMILIES)
        f.setStyleHint(QFont.StyleHint.SansSerif)
        f.setPointSize(point_size)
        f.setWeight(QFont.Weight.Medium if bold else QFont.Weight.Normal)
        f.setKerning(True)
        return f

    def _symbol_at_row(self, row: int) -> Optional[str]:
        return next((s for s, r in self.symbol_to_row.items() if r == row), None)

    def _subscribe_tokens(self, positions: List[Position]):
        tokens = {int(p.token) for p in positions if int(p.token) > 0}
        if not tokens:
            self._subscribed_tokens.clear()
            return

        # Always re-emit full position tokens when the set changes so positions
        # recover quickly after reconnect/app reopen websocket resets.
        if tokens != self._subscribed_tokens:
            self.subscribe_to_market_data.emit(sorted(tokens))
            self._subscribed_tokens = set(tokens)
            return

        # Safety net: if the set is unchanged but we still have stale/zero LTPs,
        # request the same subscriptions again to force immediate refresh.
        stale_symbols = [p.symbol for p in positions if p.token > 0 and p.ltp <= 0]
        if stale_symbols:
            self.subscribe_to_market_data.emit(sorted(tokens))


    def get_position_by_symbol(self, symbol: str) -> Optional[Position]:
        """Return the latest position snapshot for a symbol, if present."""
        if not symbol:
            return None
        return self.positions_data.get(symbol)

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
        show_vertical_lines = bool(self._color_theme.get("show_table_vertical_lines", False))
        gridline_color = "rgba(111,129,148,0.28)" if show_vertical_lines else "transparent"

        self.setStyleSheet(f"""
            QWidget {{
                background-color: {_BG_APP};
                color: {_T0};
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 400;
            }}

            QTableWidget {{
                background-color: {_BG_BASE};
                alternate-background-color: {_BG_ALT};
                border: none;
                gridline-color: {gridline_color};
                selection-background-color: {_BG_SEL};
                selection-color: {_T0};
                color: {_T0};
                outline: none;
                show-decoration-selected: 0;
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 400;
                border-radius: 0px;
            }}

            QTableWidget::item {{
                padding: 0 5px;
                border-bottom: 1px solid {_BG_HOVER};
                background-color: transparent;
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 400;
            }}

            QTableWidget::item:selected {{
                background-color: {_BG_SEL} !important;
                color: {_T0};
                outline: none;
            }}

            QTableWidget::item:focus {{
                background-color: {_BG_SEL} !important;
                outline: none;
            }}

            QTableWidget::item:hover {{
                background-color: {_BG_HOVER};
            }}

            QTableWidget::item:alternate {{
                background-color: {_BG_ALT};
            }}

            QTableWidget::item:alternate:selected {{
                background-color: {_BG_SEL} !important;
                color: {_T0};
            }}

            QHeaderView::section {{
                background-color: {_BG_HEADER};
                color: {_T2};
                padding: 0 5px;
                border: none;
                border-bottom: 1px solid {_BORDER};
                font-family: {_SANS};
                font-weight: 500;
                font-size: 8px;
                letter-spacing: 1px;
                text-transform: uppercase;
                min-height: {_HEADER_H}px;
                max-height: {_HEADER_H}px;
            }}

            QHeaderView::section:hover {{
                background-color: {_BG_HOVER};
                color: {_T1};
            }}

            QHeaderView {{
                background-color: {_BG_HEADER};
                border: none;
            }}

            QHeaderView::down-arrow,
            QHeaderView::up-arrow {{
                width: 0px;
                height: 0px;
            }}

            #positionsFooter {{
                background-color: {_BG_FOOTER};
                border-top: 1px solid {_BORDER};
            }}

            #footerLabel {{
                color: {_T2};
                font-family: {_SANS};
                font-size: 9px;
                font-weight: 600;
                letter-spacing: 0.6px;
                background-color: transparent;
            }}

            #footerValue {{
                color: {_T1};
                font-family: {_NUM};
                font-size: 11px;
                font-weight: 500;
                background-color: transparent;
            }}

            QMenu#posContextMenu {{
                background: {_BG_BASE};
                border: 1px solid {_BORDER};
                border-radius: 2px;
                color: {_T0};
                font-family: {_SANS};
                font-size: 10px;
                padding: 4px 0;
            }}

            QMenu#posContextMenu::item {{
                padding: 5px 14px;
                color: {_T0};
            }}

            QMenu#posContextMenu::item:selected {{
                background: {_BG_SEL};
                color: {_T0};
            }}

            QMenu#posContextMenu::separator {{
                height: 1px;
                background: {_BORDER};
                margin: 3px 8px;
            }}

            QScrollBar:vertical {{
                background: transparent;
                width: 4px;
                border: none;
                margin: 0;
            }}

            QScrollBar::handle:vertical {{
                background: {_BORDER};
                border-radius: 2px;
                min-height: 18px;
            }}

            QScrollBar::handle:vertical:hover {{
                background: {_T2};
            }}

            QScrollBar:horizontal {{
                background: transparent;
                height: 4px;
                border: none;
                margin: 0;
            }}

            QScrollBar::handle:horizontal {{
                background: {_BORDER};
                border-radius: 2px;
                min-width: 18px;
            }}

            QScrollBar::handle:horizontal:hover {{
                background: {_T2};
            }}

            QScrollBar::add-line,
            QScrollBar::sub-line {{
                border: none;
                background: none;
                width: 0px;
                height: 0px;
                margin: 0px;
            }}
        """)

    # ══════════════════════════════════════════════════════════════════════════
    # PUBLIC HELPERS
    # ══════════════════════════════════════════════════════════════════════════

    def apply_color_theme(self, theme: Dict):
        self._color_theme = theme or self._color_theme
        self.table.setShowGrid(bool(self._color_theme.get("show_table_vertical_lines", False)))
        self._apply_styles()
        for sym, row in self.symbol_to_row.items():
            pos = self.positions_data.get(sym)
            if pos:
                self._refresh_row(row, pos)
        self._update_footer()

    def get_total_pnl(self) -> float:
        return sum(p.pnl for p in self.positions_data.values())

    def has_positions(self) -> bool:
        return bool(self.positions_data)


    def get_position_by_symbol(self, symbol: str) -> Optional[Position]:
        """Return the latest position snapshot for a symbol, if present."""
        if not symbol:
            return None
        return self.positions_data.get(symbol)

    def clear_positions(self):
        self.positions_data.clear()
        self.symbol_to_row.clear()
        self._token_to_symbol.clear()
        self._subscribed_tokens.clear()
        self._flashes.clear()
        self._pending_ticks.clear()
        self.table.setRowCount(0)
        self._update_footer()