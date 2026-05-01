# kite/widgets/positions_table.py
"""
Institutional-grade Positions Table — Ultra-Compact Mode.

Upgrades:
  • Strictly 4 columns: Symbol, Qty, Avg, PnL
  • Stripped heavy monospace fonts; matches native app UI (Sans-serif)
  • Compact headers and cell paddings to minimize horizontal space
  • Zero visual noise (no currency symbols inline)
  • Flash animations and quick sorting retained
"""

import logging
from typing import List, Dict, Optional
from dataclasses import dataclass, field

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QHeaderView, QAbstractItemView, QFrame, QMenu, QTableWidget,
    QTableWidgetItem
)
from PySide6.QtCore import (
    Qt, Signal, Slot, QTimer
)
from PySide6.QtGui import QColor, QFont, QBrush, QCursor

logger = logging.getLogger(__name__)

# ─── Palette ──────────────────────────────────────────────────────────────────
_BG_BASE = "#0f1318"
_BG_ALT = "#0f1318"
_BG_HEADER = "#0b1019"
_BG_FOOTER = "#080d15"
_BG_SEL = "#1a2840"
_BG_HOVER = "#141920"
_BORDER = "#1a2030"
_T0 = "#d8e4f0"  # primary text
_T1 = "#8ea3bc"  # secondary text
_T2 = "#506070"  # muted
_GREEN = "#26a69a"  # profit / up-tick
_RED = "#ef5350"  # loss / down-tick
_APP_FONT_FAMILY = "Segoe UI"
_OPEN_PROFIT = "#00d4a8"
_OPEN_PROFIT_TINT = "#0a2520"
_OPEN_LOSS = "#ff4d6a"
_OPEN_LOSS_TINT = "#200a10"
_OPEN_FLAT = "#7a94b0"

# Column indices
COL_SYMBOL = 0
COL_QTY = 1
COL_AVG = 2
COL_OPEN_PNL = 3

HEADERS = ["Symbol", "Qty", "Avg", "P&L"]

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

    def __init__(self, parent=None):
        super().__init__(parent)

        self.positions_data: Dict[str, Position] = {}
        self.symbol_to_row: Dict[str, int] = {}
        self._token_to_symbol: Dict[int, str] = {}
        self._subscribed_tokens: set = set()

        self._tick_dirs: Dict[str, int] = {}
        self._prev_ltps: Dict[str, float] = {}

        self._sort_col: int = COL_SYMBOL
        self._sort_asc: bool = True
        self._flashes: List[_FlashCell] = []
        self._pending_ticks: Dict[int, float] = {}

        self._color_theme: Dict = {
            "enable_table_directional_colors": False,
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

        main_layout.addWidget(self._build_footer())

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
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setSortingEnabled(False)

        self.table.verticalHeader().setDefaultSectionSize(22)  # Tighter rows

        hdr = self.table.horizontalHeader()
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        hdr.setSectionResizeMode(COL_SYMBOL, QHeaderView.ResizeMode.Stretch)
        for col in (COL_QTY, COL_AVG, COL_OPEN_PNL):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        hdr.setMinimumSectionSize(35)  # Reduced for compactness
        hdr.setHighlightSections(False)
        hdr.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def _build_footer(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("positionsFooter")
        frame.setFixedHeight(24)

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

        self._footer_open_pnl = _metric("Open P&L:")
        self._footer_exposure = _metric("Exposure:")
        lay.addStretch()
        return frame

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
        is_long = pos.quantity > 0
        qty_sign = "+" if is_long else "−"

        tick_dir = self._tick_dirs.get(pos.symbol, 0)
        table_colors = self._color_theme.get("tables", {})
        directional_colors_enabled = bool(self._color_theme.get("enable_table_directional_colors", False))
        profit_color = table_colors.get("positive", _GREEN)
        loss_color = table_colors.get("negative", _RED)
        neutral_color = table_colors.get("neutral", _T2)

        pnl_color = self._open_pnl_text_color(pnl)

        # Notice: No ₹ symbols to save horizontal space
        cells = [
            (COL_SYMBOL, pos.symbol, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, pnl_color),
            (COL_QTY, f"{qty_sign}{abs(pos.quantity)}", Qt.AlignmentFlag.AlignCenter, profit_color if is_long else loss_color),
            (COL_AVG, f"{pos.avg_price:,.2f}", Qt.AlignmentFlag.AlignCenter, pnl_color),
            (COL_OPEN_PNL, f"{'+' if pnl >= 0 else ''}{pnl:,.2f}", Qt.AlignmentFlag.AlignCenter, pnl_color),
        ]

        # Use UI native fonts instead of Monospace
        base_font = QFont(_APP_FONT_FAMILY, 9)

        sym_font = QFont(base_font)
        sym_font.setBold(True)

        for col, text, align, color in cells:
            item = self.table.item(row, col)
            if not item: continue
            item.setText(text)
            item.setTextAlignment(align)
            item.setForeground(QColor(color))
            item.setFont(sym_font if col == COL_SYMBOL else base_font)
            item.setData(Qt.ItemDataRole.UserRole, self._sort_key(col, pos))

        self._apply_open_pnl_row_style(row, pnl)

    def _open_pnl_text_color(self, pnl: float) -> str:
        if pnl > 0:
            return _OPEN_PROFIT
        if pnl < 0:
            return _OPEN_LOSS
        return _OPEN_FLAT

    def _apply_open_pnl_row_style(self, row: int, pnl: float) -> None:
        if pnl > 0:
            fg = QColor(_OPEN_PROFIT)
            bg = QColor(_OPEN_PROFIT_TINT)
        elif pnl < 0:
            fg = QColor(_OPEN_LOSS)
            bg = QColor(_OPEN_LOSS_TINT)
        else:
            fg = QColor(_OPEN_FLAT)
            bg = QColor(Qt.transparent)

        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            if item:
                item.setForeground(QBrush(fg))
                item.setBackground(QBrush(bg))

    def _sort_key(self, col: int, pos: Position):
        pnl = (pos.ltp - pos.avg_price) * pos.quantity
        mapping = {
            COL_SYMBOL: pos.symbol,
            COL_QTY: pos.quantity,
            COL_AVG: pos.avg_price,
            COL_OPEN_PNL: pnl,
        }
        return mapping.get(col, 0)

    # ══════════════════════════════════════════════════════════════════════════
    # LIVE TICK UPDATE
    # ══════════════════════════════════════════════════════════════════════════

    @Slot(int, float)
    def update_market_data(self, token: int, ltp: float):
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
                        r = int(18 + (0 - 18) * (1 - ratio))
                        g = int(80 + (55 - 80) * (1 - ratio))
                        b = int(40 + (34 - 40) * (1 - ratio))
                    else:
                        r = int(120 + (70 - 120) * (1 - ratio))
                        g = int(20 + (20 - 20) * (1 - ratio))
                        b = int(20 + (20 - 20) * (1 - ratio))
                    item.setBackground(QBrush(QColor(r, g, b, 200)))
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
            return

        total_pnl = sum(p.pnl for p in self.positions_data.values())
        exposure = sum(abs(p.quantity) * p.avg_price for p in self.positions_data.values())

        pnl_color = _GREEN if total_pnl >= 0 else _RED
        self._footer_open_pnl.setText(f"{'+' if total_pnl >= 0 else ''}{total_pnl:,.0f}")
        self._footer_open_pnl.setStyleSheet(f"color:{pnl_color}; font-weight:bold;")
        self._footer_exposure.setText(f"{exposure:,.0f}")

    def _on_cell_clicked(self, row: int, col: int):
        if symbol := self._symbol_at_row(row):
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

        chart_act = menu.addAction("📈  Open Chart")
        chart_act.triggered.connect(lambda: self.symbol_selected.emit(symbol))
        menu.addSeparator()

        close_act = menu.addAction("✕  Close Position")
        close_act.triggered.connect(lambda: self.exit_position_requested.emit(symbol))

        half_act = menu.addAction("½  Exit Half")
        half_act.triggered.connect(lambda: self.exit_half_position_requested.emit(symbol))

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _symbol_at_row(self, row: int) -> Optional[str]:
        return next((s for s, r in self.symbol_to_row.items() if r == row), None)

    def _subscribe_tokens(self, positions: List[Position]):
        tokens = [p.token for p in positions if p.token > 0]
        new_tokens = [t for t in tokens if t not in self._subscribed_tokens]
        if new_tokens:
            self.subscribe_to_market_data.emit(new_tokens)
            self._subscribed_tokens.update(new_tokens)


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
        self.setStyleSheet(f"""
            QWidget {{
                background:{_BG_BASE};
                color:{_T0};
                font-family:'{_APP_FONT_FAMILY}';
                font-size:9pt;
            }}
            QTableWidget {{
                background:{_BG_BASE};
                alternate-background-color:{_BG_ALT};
                gridline-color:transparent;
                border:1px solid {_BORDER};
                border-radius:0px;
                outline:none;
                show-decoration-selected:0;
            }}
            QTableWidget::item {{
                padding:1px 6px; /* Tighter cell padding */
                border-bottom:1px solid {_BORDER};
                font-family:"JetBrains Mono","Consolas",monospace;
            }}
            QTableWidget::item:selected {{
                background:{_BG_SEL} !important;
                color:{_T0};
            }}
            QTableWidget::item:hover {{
                background:{_BG_HOVER};
            }}
            QTableWidget::item:focus {{
                background:{_BG_SEL} !important;
                color:{_T0};
            }}
            QTableWidget::item:alternate {{
                background:{_BG_ALT};
            }}
            QHeaderView::section {{
                background:{_BG_HEADER};
                color:{_T1};
                padding:2px 6px; /* Compact header */
                border:none;
                border-bottom:1px solid {_BORDER};
                border-right:1px solid {_BORDER};
                font-family:'{_APP_FONT_FAMILY}';
                font-size:9pt;
                font-weight:600;
            }}
            QHeaderView::section:last {{ border-right:none; }}
            QHeaderView::section:hover {{ color:{_T0}; }}
            QHeaderView::down-arrow, QHeaderView::up-arrow {{ width:0px; height:0px; }}
            #positionsFooter {{
                background:{_BG_FOOTER};
                border-top:1px solid {_BORDER};
            }}
            #footerLabel {{
                color:{_T2};
                font-family:'{_APP_FONT_FAMILY}';
                font-size:9pt;
            }}
            #footerValue {{
                color:{_T1};
                font-family:'{_APP_FONT_FAMILY}';
                font-size:9pt;
            }}
            QMenu#posContextMenu {{
                background:#0c121e;
                border:1px solid {_BORDER};
                border-radius:4px;
            }}
            QMenu#posContextMenu::item {{
                padding:6px 16px;
                color:{_T0};
            }}
            QMenu#posContextMenu::item:selected {{ background:#1a2840; }}
            QScrollBar:vertical {{ background:transparent; width:4px; }}
            QScrollBar::handle:vertical {{ background:#2a3850; border-radius:2px; }}
            QScrollBar:horizontal {{ background:transparent; height:4px; }}
            QScrollBar::handle:horizontal {{ background:#2a3850; border-radius:2px; }}
            QScrollBar::add-line,QScrollBar::sub-line {{ border:none; background:none; width:0; height:0; }}
        """)

    # ══════════════════════════════════════════════════════════════════════════
    # PUBLIC HELPERS
    # ══════════════════════════════════════════════════════════════════════════

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
