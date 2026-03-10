import logging
from typing import List, Dict, Optional
from dataclasses import dataclass
from functools import partial
from PySide6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QPushButton, QLabel, QVBoxLayout,
    QWidget, QHeaderView, QFrame, QHBoxLayout, QAbstractItemView, QMenu
)
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor, QCursor, QAction, QFontMetrics
from functools import partial

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Simple position data structure"""
    symbol: str
    quantity: int
    avg_price: float
    token: int
    ltp: float = 0.0
    pnl: float = 0.0
    product: str = "MIS"


class PositionsTable(QWidget):
    """
    SIMPLIFIED Positions Table - Self-Managing:
    1. Receives positions from manager ONCE
    2. Subscribes to market data ONCE
    3. Updates LTP and PnL locally
    4. NEVER recreates the table
    """

    # Simple signals
    exit_position_requested = Signal(str)  # Just symbol
    subscribe_to_market_data = Signal(list)  # Request tokens
    symbol_selected = Signal(str)  # For chart

    def __init__(self, parent=None):
        super().__init__(parent)

        # Simple data storage
        self.positions_data = {}  # symbol -> SimplePosition
        self.symbol_to_row = {}  # symbol -> row_number
        self.is_market_data_subscribed = False

        self._setup_ui()
        self._apply_consistent_styles()
        logger.info("Simple Positions Table initialized")

    def _setup_ui(self):
        """Setup UI once - never recreate"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Main table
        self.table = QTableWidget()
        self._configure_table()
        main_layout.addWidget(self.table, 1)

        # Simple footer
        footer = self._create_minimal_footer()
        main_layout.addWidget(footer)

        # Connect table signals
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

    def _configure_table(self):
        """Configure table once"""
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            "Symbol", "Qty", "Avg", "P&L", ""
        ])

        # Table behavior
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setVisible(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Column sizing
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)  # Symbol
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)  # Qty
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)  # Avg
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)  # P&L
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)  # Exit

        self._adjust_symbol_column_width()
        self.table.setColumnWidth(1, 50)  # Qty
        self.table.setColumnWidth(2, 70)  # Avg
        self.table.setColumnWidth(3, 80)  # P&L
        self.table.setColumnWidth(4, 24)  # Exit button

        self.table.verticalHeader().setDefaultSectionSize(28)


    def _adjust_symbol_column_width(self):
        """Keep symbol column compact using ~70% of the longest visible symbol length."""
        metrics = QFontMetrics(self.table.font())
        longest_symbol_len = 0

        for row in range(self.table.rowCount()):
            symbol_item = self.table.item(row, 0)
            if not symbol_item:
                continue
            longest_symbol_len = max(longest_symbol_len, len(symbol_item.text().strip()))

        target_chars = max(4, int(round(longest_symbol_len * 0.7))) if longest_symbol_len > 0 else 6
        compact_width = metrics.horizontalAdvance("W" * target_chars) + 18
        header_width = metrics.horizontalAdvance("Symbol") + 20
        max_compact_width = metrics.horizontalAdvance("W" * 10) + 22
        symbol_width = min(max(compact_width, header_width), max_compact_width)

        self.table.setColumnWidth(0, symbol_width)

    def _create_minimal_footer(self) -> QFrame:
        """Create minimal footer"""
        footer_frame = QFrame()
        footer_frame.setObjectName("positionsFooter")
        footer_frame.setFixedHeight(28)

        footer_layout = QHBoxLayout(footer_frame)
        footer_layout.setContentsMargins(12, 4, 12, 4)
        footer_layout.setSpacing(12)

        # Total P&L
        self.total_pnl_label = QLabel("P&L: ₹0.00")
        self.total_pnl_label.setObjectName("footerPrimaryMetric")
        footer_layout.addWidget(self.total_pnl_label)

        # Separator
        separator = QLabel("|")
        separator.setObjectName("footerSeparator")
        footer_layout.addWidget(separator)

        # Investment amount
        self.investment_label = QLabel("Investment: ₹0")
        self.investment_label.setObjectName("footerSecondaryMetric")
        footer_layout.addWidget(self.investment_label)

        # Separator
        separator2 = QLabel("|")
        separator2.setObjectName("footerSeparator")
        footer_layout.addWidget(separator2)

        # Returns percentage
        self.returns_label = QLabel("Returns: 0.00%")
        self.returns_label.setObjectName("footerSecondaryMetric")
        footer_layout.addWidget(self.returns_label)

        footer_layout.addStretch()
        return footer_frame

    # ===========================================================================
    # MAIN METHOD: RECEIVE POSITIONS FROM MANAGER (ONCE)
    # ===========================================================================

    @Slot(list)
    def update_positions(self, positions_list: List[Position]):
        """
        Receive positions from manager - populate table ONCE
        This is the ONLY method that recreates the table
        """
        logger.info(f"📊 Updating positions table with {len(positions_list)} positions")

        # Store positions data locally
        self.positions_data = {pos.symbol: pos for pos in positions_list}

        # Clear and populate table ONCE
        self.table.setRowCount(len(positions_list))
        self.symbol_to_row = {}

        for row, position in enumerate(positions_list):
            self.symbol_to_row[position.symbol] = row
            self._populate_row_once(row, position)

        # Subscribe to market data ONCE
        self._subscribe_to_market_data_once(positions_list)

        # Update summary
        self._update_summary()

        self._adjust_symbol_column_width()

        logger.info(f"✅ Positions table updated with {len(positions_list)} positions")

    def _populate_row_once(self, row: int, position: Position):
        """Populate a single row ONCE - never called again"""
        # Create items for first 4 columns
        for i in range(4):
            self.table.setItem(row, i, QTableWidgetItem())

        # Add exit button in last column
        exit_btn = self._create_exit_button(position.symbol)
        self.table.setCellWidget(row, 4, exit_btn)

        # Populate with initial data
        self._update_single_row_data(row, position)


    def _create_exit_button(self, symbol: str) -> QPushButton:
        """Create exit button for position"""
        exit_btn = QPushButton("×")
        exit_btn.setObjectName("exitButton")
        exit_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        exit_btn.setFixedSize(16, 16)
        exit_btn.clicked.connect(partial(self._emit_exit_position_signal, symbol))
        return exit_btn

    def _emit_exit_position_signal(self, symbol: str):
        """Emit exit position signal with proper symbol"""
        self.exit_position_requested.emit(symbol)

    def _subscribe_to_market_data_once(self, positions_list: List[Position]):
        """Subscribe to market data ONCE for all position tokens"""
        if not self.is_market_data_subscribed and positions_list:
            tokens = [pos.token for pos in positions_list if pos.token > 0]
            if tokens:
                self.subscribe_to_market_data.emit(tokens)
                self.is_market_data_subscribed = True
                logger.info(f"📡 Subscribed to market data for {len(tokens)} tokens")

    # ===========================================================================
    # LOCAL PNL CALCULATION: UPDATE ONLY AFFECTED ROWS
    # ===========================================================================

    @Slot(int, float)
    def update_market_data(self, token: int, ltp: float):
        """
        Update LTP and recalculate PnL locally
        ONLY UPDATES AFFECTED ROWS - NO TABLE RECREATION
        """
        for symbol, position in self.positions_data.items():
            if position.token == token:
                # Update position data locally
                position.ltp = ltp
                position.pnl = (ltp - position.avg_price) * position.quantity

                # Update ONLY this row - NO FULL TABLE REFRESH
                row = self.symbol_to_row.get(symbol)
                if row is not None:
                    self._update_single_row_data(row, position)

        # Update summary
        self._update_summary()

    def _update_single_row_data(self, row: int, position: Position):
        """Update data for a single row only"""
        if row >= self.table.rowCount():
            return

        # Update text values
        self.table.item(row, 0).setText(position.symbol)
        self.table.item(row, 1).setText(str(position.quantity))
        self.table.item(row, 2).setText(f"{position.avg_price:.2f}")
        self.table.item(row, 3).setText(f"{position.pnl:,.2f}")

        # Color code P&L
        pnl_item = self.table.item(row, 3)
        if position.pnl > 0:
            pnl_item.setForeground(QColor(60, 179, 113))  # Green
        elif position.pnl < 0:
            pnl_item.setForeground(QColor(220, 20, 60))  # Red
        else:
            pnl_item.setForeground(QColor(169, 169, 169))  # Gray

        # Set alignments
        self.table.item(row, 0).setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.item(row, 1).setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        self.table.item(row, 2).setTextAlignment(Qt.AlignmentFlag.AlignCenter  | Qt.AlignmentFlag.AlignVCenter)
        self.table.item(row, 3).setTextAlignment(Qt.AlignmentFlag.AlignCenter  | Qt.AlignmentFlag.AlignVCenter)

        # Set tooltip
        investment = abs(position.quantity * position.avg_price)
        pnl_percent = ((position.ltp - position.avg_price) / position.avg_price * 100) if position.avg_price != 0 else 0
        tooltip = f"Symbol: {position.symbol}\nLTP: ₹{position.ltp:.2f}\nInvestment: ₹{investment:,.2f}\nP&L %: {pnl_percent:+.2f}%"

        for col in range(4):
            if self.table.item(row, col):
                self.table.item(row, col).setToolTip(tooltip)

    def _update_summary(self):
        """Update summary footer"""
        if not self.positions_data:
            self.total_pnl_label.setText("P&L: ₹0.00")
            self.investment_label.setText("Investment: ₹0")
            self.returns_label.setText("Returns: 0.00%")
            return

        total_pnl = sum(pos.pnl for pos in self.positions_data.values())
        total_investment = sum(abs(pos.quantity * pos.avg_price) for pos in self.positions_data.values())

        # Update P&L with color
        color = "#26a69a" if total_pnl >= 0 else "#ef5350"
        self.total_pnl_label.setText(f"P&L: ₹{total_pnl:,.2f}")
        self.total_pnl_label.setStyleSheet(f"color: {color}; background-color: transparent; border: none;")

        # Update other metrics
        self.investment_label.setText(f"Investment: ₹{total_investment:,.0f}")
        returns_percent = (total_pnl / total_investment * 100) if total_investment > 0 else 0
        self.returns_label.setText(f"Returns: {returns_percent:+.2f}%")

    # ===========================================================================
    # SIMPLE EVENT HANDLERS
    # ===========================================================================

    def _on_cell_clicked(self, row: int, column: int):
        """Handle cell click for chart updates"""
        if column != 4 and row < self.table.rowCount():  # Don't handle exit button
            try:
                symbol = self.table.item(row, 0).text()
                self.symbol_selected.emit(symbol)
            except Exception as e:
                logger.error(f"Error handling cell click: {e}")

    def _show_context_menu(self, position):
        """Show simple context menu"""
        item = self.table.itemAt(position)
        if not item:
            return

        row = item.row()
        symbol = self.table.item(row, 0).text()
        pos = self.positions_data.get(symbol)
        if not pos:
            return

        menu = QMenu(self)
        # ... styling code ...

        # Simple menu options
        chart_action = QAction("View Chart", self)
        chart_action.triggered.connect(partial(self._emit_symbol_selected, symbol))
        menu.addAction(chart_action)

        menu.addSeparator()

        exit_action = QAction("Exit Position", self)
        exit_action.triggered.connect(partial(self._emit_exit_position_signal, symbol))
        menu.addAction(exit_action)

        menu.exec(self.table.mapToGlobal(position))

    def _emit_symbol_selected(self, symbol: str):
        """Emit symbol selected signal"""
        self.symbol_selected.emit(symbol)

    # ===========================================================================
    # UTILITY METHODS
    # ===========================================================================

    def get_position_by_symbol(self, symbol: str) -> Optional[Position]:
        """Get position by symbol"""
        return self.positions_data.get(symbol)

    def has_positions(self) -> bool:
        """Check if there are any open positions"""
        return len(self.positions_data) > 0

    def get_position_count(self) -> int:
        """Get current position count"""
        return len(self.positions_data)

    def get_total_pnl(self) -> float:
        """Get total unrealized P&L"""
        return sum(pos.pnl for pos in self.positions_data.values())

    def clear_positions(self):
        """Clear all positions - used when no positions"""
        self.positions_data.clear()
        self.symbol_to_row.clear()
        self.table.setRowCount(0)
        self.is_market_data_subscribed = False
        self._update_summary()
        logger.info("Positions table cleared")

    def _apply_consistent_styles(self):
        """Apply the same styling as before - keeping UI intact"""
        self.setStyleSheet("""
            /* Main Widget - matches watchlist and scanner */
            QWidget {
                background-color: #0a0a0a;
                color: #e0e0e0;
                font-family: "Segoe UI", Arial, sans-serif;
                font-size: 13px;
                border-top: 3px solid #404040;
            }

            /* Table Styling - EXACT match to watchlist and scanner */
            QTableWidget {
                background-color: #0a0a0a;
                border: none;
                gridline-color: #2a2a2a;
                selection-background-color: #1e3a5f;
                alternate-background-color: #0f0f0f;
                outline: none;
                show-decoration-selected: 0;
                font-size: 12px;
                border-radius: 0px;
            }

            QTableWidget::item {
                padding: 5px 8px;
                border-bottom: 1px solid #1a1a1a;
                background-color: transparent;
                font-size: 12px;
            }

            QTableWidget::item:selected {
                background-color: #1e3a5f !important;
                outline: none;
                border: none;
                color: #ffffff;
                font-weight: 600;
            }

            QTableWidget::item:focus {
                background-color: #1e3a5f !important;
                outline: none;
                border: none;
            }

            QTableWidget::item:hover {
                background-color: transparent;
            }

            QTableWidget::item:alternate {
                background-color: #0f0f0f;
            }

            QTableWidget::item:alternate:selected {
                background-color: #1e3a5f !important;
                color: #ffffff;
                font-weight: 600;
            }

            /* Header Styling - EXACT match to watchlist */
            QHeaderView::section {
                background-color: #1a1a1a;
                color: #a0c0ff;
                padding: 3px 10px;
                border: none;
                border-bottom: 1px solid #303030;
                border-right: 1px solid #101010;
                font-weight: 600;
                font-size: 11px;
            }

            QHeaderView::section:last {
                border-right: none;
            }

            QHeaderView::section:hover {
                background-color: #2a2a2a;
                color: #ccd6f6;
            }

            /* Exit Button - EXACT match to watchlist remove button */
            QPushButton#exitButton {
                background-color: transparent;
                color: #cc4444;
                border: none;
                font-weight: bold;
                font-size: 12px;
                border-radius: 8px;
                padding: 0px;
                margin: 0px;
            }

            QPushButton#exitButton:hover {
                color: #ff6666;
                background-color: #2a1f1f;
            }

            /* Minimal Footer Styling */
            #positionsFooter {
                background-color: #000000;
                border-top: 1px solid #2a2a2a;
            }

            #footerPrimaryMetric {
                color: #e0e0e0;
                font-size: 11px;
                font-weight: 600;
                background-color: transparent;
                border: none;
                margin: 0px;
                padding: 0px;
            }

            #footerSecondaryMetric {
                color: #a0a0a0;
                font-size: 10px;
                font-weight: normal;
                background-color: transparent;
                border: none;
                margin: 0px;
                padding: 0px;
            }

            #footerSeparator {
                color: #505050;
                font-size: 10px;
                background-color: transparent;
                border: none;
                margin: 0px;
                padding: 0px;
            }

            /* Enhanced Scrollbars - EXACT match to watchlist */
            QScrollBar:vertical {
                background-color: #0a0a0a;
                width: 8px;
                border: none;
                margin: 0px;
            }

            QScrollBar::handle:vertical {
                background-color: #424242;
                border-radius: 4px;
                min-height: 20px;
                margin: 2px;
            }

            QScrollBar::handle:vertical:hover {
                background-color: #616161;
            }

            QScrollBar:horizontal {
                background-color: #0a0a0a;
                height: 8px;
                border: none;
                margin: 0px;
            }

            QScrollBar::handle:horizontal {
                background-color: #424242;
                border-radius: 4px;
                min-width: 20px;
                margin: 2px;
            }

            QScrollBar::handle:horizontal:hover {
                background-color: #616161;
            }

            QScrollBar::add-line, QScrollBar::sub-line {
                border: none;
                background: none;
                width: 0px;
                height: 0px;
                margin: 0px;
            }
        """)

        logger.info("Consistent styling applied to positions table.")