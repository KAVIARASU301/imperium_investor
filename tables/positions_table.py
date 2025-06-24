import logging
from typing import List, Dict, Optional
from datetime import datetime
from PySide6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QPushButton, QLabel, QVBoxLayout,
    QWidget, QHeaderView, QFrame, QHBoxLayout, QAbstractItemView, QMenu,
    QToolTip, QApplication
)
from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QAction

from utils.data_models import Position

logger = logging.getLogger(__name__)


class PositionsTable(QWidget):
    """
    Enhanced positions table with perfect integration to PositionManager.
    Features TC2000-style professional dark theme and advanced functionality.
    """
    # Signals for integration with main window
    exit_position_requested = Signal(dict)
    exit_all_positions_requested = Signal()
    subscribe_tokens_requested = Signal(list)
    symbol_selected = Signal(str)
    position_details_requested = Signal(str)
    add_alert_requested = Signal(str, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._positions_cache: Dict[str, Position] = {}
        self._sort_column = 4  # Default sort by P&L
        self._sort_order = Qt.SortOrder.DescendingOrder

        # Performance tracking
        self._last_update_time = datetime.now()
        self._update_count = 0

        # Auto-refresh timer for stale data indication
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._check_data_freshness)
        self._refresh_timer.start(5000)  # Check every 5 seconds

        self._setup_ui()
        self._apply_professional_styles()
        logger.info("Enhanced Open Positions Table initialized.")

    def _setup_ui(self):
        """Setup the main UI layout with enhanced features."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header with title and controls
        header = self._create_header()
        main_layout.addWidget(header)

        # Main table
        self.table = QTableWidget()
        self._configure_table()
        main_layout.addWidget(self.table, 1)

        # Footer with summary information
        footer = self._create_enhanced_footer()
        main_layout.addWidget(footer)

        # Connect table signals
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

    def _create_header(self) -> QFrame:
        """Create header with title and action buttons."""
        header_frame = QFrame(objectName="positionsHeader")
        header_frame.setFixedHeight(32)
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(12, 4, 12, 4)

        # Title
        title_label = QLabel("OPEN POSITIONS")
        title_label.setObjectName("positionsTitle")

        # Position count indicator
        self.position_count_label = QLabel("0")
        self.position_count_label.setObjectName("positionCount")

        # Refresh indicator
        self.refresh_indicator = QLabel("●")
        self.refresh_indicator.setObjectName("refreshIndicator")
        self.refresh_indicator.setToolTip("Data freshness indicator")

        # Exit all button
        self.exit_all_btn = QPushButton("Exit All")
        self.exit_all_btn.setObjectName("exitAllButton")
        self.exit_all_btn.clicked.connect(self._on_exit_all_clicked)
        self.exit_all_btn.setEnabled(False)

        header_layout.addWidget(title_label)
        header_layout.addWidget(self.position_count_label)
        header_layout.addStretch()
        header_layout.addWidget(self.refresh_indicator)
        header_layout.addWidget(self.exit_all_btn)

        return header_frame

    def _configure_table(self):
        """Configure table with enhanced features."""
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "Symbol", "Qty", "Avg", "LTP", "P&L", "P&L%", ""
        ])

        # Table behavior
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        header = self.table.horizontalHeader()

        # Symbol column — fixed but wider
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 60)  # adjust width as needed

        # Auto-size for data columns
        for i in range(1, 6):
            header.setSectionResizeMode(i, QHeaderView.ResizeToContents)

        # Exit button column — fixed size
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(6, 28)

        # Row height
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setStyleSheet("""
            QTableView::item {
                padding: 1px 2px;
            }
            QHeaderView::section {
                padding: 2px;
                margin: 0px;
            }
        """)

        # Header click for sorting
        header.sectionClicked.connect(self._on_header_clicked)

    def _create_enhanced_footer(self) -> QFrame:
        """Create enhanced footer with detailed summary."""
        footer_frame = QFrame(objectName="positionsFooter")
        footer_frame.setFixedHeight(42)
        footer_layout = QVBoxLayout(footer_frame)
        footer_layout.setContentsMargins(12, 4, 12, 4)
        footer_layout.setSpacing(2)

        # Top row: Total P&L
        top_row = QHBoxLayout()
        total_label = QLabel("TOTAL P&L")
        total_label.setObjectName("footerLabel")

        self.total_pnl_label = QLabel("₹0.00")
        self.total_pnl_label.setObjectName("totalPnlValue")

        top_row.addWidget(total_label)
        top_row.addStretch()
        top_row.addWidget(self.total_pnl_label)

        # Bottom row: Additional metrics
        bottom_row = QHBoxLayout()

        self.investment_label = QLabel("Investment: ₹0")
        self.investment_label.setObjectName("footerMetric")

        self.returns_label = QLabel("Returns: 0.00%")
        self.returns_label.setObjectName("footerMetric")

        self.last_update_label = QLabel("Updated: Never")
        self.last_update_label.setObjectName("footerMetric")

        bottom_row.addWidget(self.investment_label)
        bottom_row.addStretch()
        bottom_row.addWidget(self.returns_label)
        bottom_row.addStretch()
        bottom_row.addWidget(self.last_update_label)

        footer_layout.addLayout(top_row)
        footer_layout.addLayout(bottom_row)

        return footer_frame

    @Slot(list)
    def update_positions(self, positions: List[Position]):
        """
        Enhanced position update with performance tracking and animations.
        """
        try:
            start_time = datetime.now()

            # Clear existing data
            self.table.setRowCount(0)
            old_cache = self._positions_cache.copy()
            self._positions_cache.clear()

            # Calculate totals
            total_pnl = 0.0
            total_investment = 0.0

            # Sort positions by the current sort criteria
            sorted_positions = self._sort_positions(positions)

            # Populate table
            for pos in sorted_positions:
                self._positions_cache[pos.tradingsymbol] = pos
                row = self.table.rowCount()
                self.table.insertRow(row)
                self._populate_row(row, pos, old_cache.get(pos.tradingsymbol))

                total_pnl += pos.pnl
                total_investment += abs(pos.quantity * pos.average_price)

            # Update summary information
            self._update_summary(total_pnl, total_investment, len(positions))

            # Update performance metrics
            self._update_count += 1
            self._last_update_time = datetime.now()
            update_time = (datetime.now() - start_time).total_seconds() * 1000

            # Request token subscription for market data
            if positions:
                tokens = self.get_all_tokens()
                self.subscribe_tokens_requested.emit(tokens)

            logger.debug(f"Position update completed in {update_time:.1f}ms for {len(positions)} positions.")

        except Exception as e:
            logger.error(f"Error updating positions: {e}", exc_info=True)

    def _sort_positions(self, positions: List[Position]) -> List[Position]:
        """Sort positions based on current sort criteria."""
        try:
            if self._sort_column == 0:  # Symbol
                key_func = lambda p: p.tradingsymbol
            elif self._sort_column == 1:  # Quantity
                key_func = lambda p: p.quantity
            elif self._sort_column == 2:  # Average Price
                key_func = lambda p: p.average_price
            elif self._sort_column == 3:  # LTP
                key_func = lambda p: p.ltp
            elif self._sort_column == 4:  # P&L
                key_func = lambda p: p.pnl
            elif self._sort_column == 5:  # P&L%
                key_func = lambda p: ((p.ltp - p.average_price) / p.average_price * 100) if p.average_price != 0 else 0
            else:
                return positions

            reverse = self._sort_order == Qt.SortOrder.DescendingOrder
            return sorted(positions, key=key_func, reverse=reverse)

        except Exception as e:
            logger.error(f"Error sorting positions: {e}")
            return positions

    def _populate_row(self, row: int, pos: Position, old_pos: Optional[Position] = None):
        """Populate a single row with position data and change indicators."""
        try:
            # Calculate P&L percentage
            pnl_percent = 0.0
            if pos.average_price != 0:
                pnl_percent = ((pos.ltp - pos.average_price) / pos.average_price) * 100

            # Create items
            items = [
                QTableWidgetItem(pos.tradingsymbol),
                QTableWidgetItem(str(pos.quantity)),
                QTableWidgetItem(f"{pos.average_price:.2f}"),
                QTableWidgetItem(f"{pos.ltp:.2f}"),
                QTableWidgetItem(f"{pos.pnl:,.2f}"),
                QTableWidgetItem(f"{pnl_percent:+.2f}%")
            ]

            # Set alignments
            items[0].setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            for i in range(1, 6):
                items[i].setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

            # Apply colors
            self._apply_row_colors(items, pos, old_pos)

            # Set items in table
            for col, item in enumerate(items):
                self.table.setItem(row, col, item)

            # Add exit button
            self.table.setCellWidget(row, 6, self._create_exit_button(row))

            # Set tooltips
            self._set_row_tooltips(row, pos)

        except Exception as e:
            logger.error(f"Error populating row {row}: {e}")

    def _apply_row_colors(self, items: List[QTableWidgetItem], pos: Position, old_pos: Optional[Position]):
        """Apply colors to row items based on P&L and changes."""
        # Color scheme
        profit_color = QColor("#26a69a")  # Teal
        loss_color = QColor("#ef5350")  # Red
        neutral_color = QColor("#9e9e9e")  # Grey
        change_color = QColor("#ffeb3b")  # Yellow for changes

        # P&L coloring
        pnl_color = profit_color if pos.pnl >= 0 else loss_color
        items[3].setForeground(pnl_color)  # LTP
        items[4].setForeground(pnl_color)  # P&L
        items[5].setForeground(pnl_color)  # P&L%

        # Neutral colors
        items[1].setForeground(neutral_color)  # Quantity
        items[2].setForeground(neutral_color)  # Average Price

        # Change indicators
        if old_pos:
            if abs(pos.ltp - old_pos.ltp) > 0.01:  # LTP changed
                items[3].setBackground(change_color.lighter(180))
            if abs(pos.pnl - old_pos.pnl) > 0.01:  # P&L changed
                items[4].setBackground(change_color.lighter(180))

    def _set_row_tooltips(self, row: int, pos: Position):
        """Set informative tooltips for table cells."""
        try:
            investment = abs(pos.quantity * pos.average_price)
            tooltip_base = f"""
Symbol: {pos.tradingsymbol}
Product: {pos.product}
Exchange: {pos.exchange}
Investment: ₹{investment:,.2f}
Current Value: ₹{abs(pos.quantity * pos.ltp):,.2f}
"""

            for col in range(6):
                self.table.item(row, col).setToolTip(tooltip_base.strip())

        except Exception as e:
            logger.error(f"Error setting tooltips for row {row}: {e}")

    def _create_exit_button(self, row: int) -> QPushButton:
        """Create enhanced exit button with better styling."""
        exit_btn = QPushButton("✕")
        exit_btn.setObjectName("exitButton")
        exit_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        exit_btn.setFixedSize(20, 20)
        exit_btn.setToolTip("Exit this position")
        exit_btn.clicked.connect(lambda: self._on_exit_clicked(row))
        return exit_btn

    def _update_summary(self, total_pnl: float, total_investment: float, position_count: int):
        """Update summary labels with current data."""
        try:
            # Update total P&L
            profit_color = "#26a69a"
            loss_color = "#ef5350"
            color = profit_color if total_pnl >= 0 else loss_color

            self.total_pnl_label.setText(f"₹{total_pnl:,.2f}")
            self.total_pnl_label.setStyleSheet(f"color: {color}; font-weight: 600;")

            # Update position count
            self.position_count_label.setText(str(position_count))
            self.exit_all_btn.setEnabled(position_count > 0)

            # Update investment
            self.investment_label.setText(f"Investment: ₹{total_investment:,.0f}")

            # Update returns percentage
            returns_percent = (total_pnl / total_investment * 100) if total_investment > 0 else 0
            self.returns_label.setText(f"Returns: {returns_percent:+.2f}%")

            # Update last update time
            self.last_update_label.setText(f"Updated: {self._last_update_time.strftime('%H:%M:%S')}")

            # Update refresh indicator
            self.refresh_indicator.setStyleSheet("color: #26a69a;")  # Green for fresh data

        except Exception as e:
            logger.error(f"Error updating summary: {e}")

    def _check_data_freshness(self):
        """Check if data is getting stale and update indicator."""
        try:
            time_since_update = (datetime.now() - self._last_update_time).total_seconds()

            if time_since_update > 60:  # More than 1 minute
                self.refresh_indicator.setStyleSheet("color: #ef5350;")  # Red for stale
                self.refresh_indicator.setToolTip("Data may be stale")
            elif time_since_update > 30:  # More than 30 seconds
                self.refresh_indicator.setStyleSheet("color: #ffeb3b;")  # Yellow for warning
                self.refresh_indicator.setToolTip("Data is getting old")
            else:
                self.refresh_indicator.setStyleSheet("color: #26a69a;")  # Green for fresh
                self.refresh_indicator.setToolTip("Data is fresh")

        except Exception as e:
            logger.error(f"Error checking data freshness: {e}")

    # === EVENT HANDLERS ===

    @Slot(int)
    def _on_header_clicked(self, logical_index: int):
        """Handle header clicks for sorting."""
        if logical_index == 6:  # Don't sort by action column
            return

        if self._sort_column == logical_index:
            # Toggle sort order if same column
            self._sort_order = (Qt.SortOrder.AscendingOrder if self._sort_order == Qt.SortOrder.DescendingOrder
                                else Qt.SortOrder.DescendingOrder)
        else:
            # New column, default to descending for numerical columns
            self._sort_column = logical_index
            self._sort_order = Qt.SortOrder.DescendingOrder if logical_index > 0 else Qt.SortOrder.AscendingOrder

        # Re-trigger update to resort
        positions = list(self._positions_cache.values())
        self.update_positions(positions)

    @Slot(int)
    def _on_exit_clicked(self, row: int):
        """Handle exit button click."""
        try:
            symbol = self.table.item(row, 0).text()
            if symbol in self._positions_cache:
                position_data = self._positions_cache[symbol].to_dict()
                self.exit_position_requested.emit(position_data)
                logger.info(f"Exit position requested for {symbol}")
            else:
                logger.warning(f"Could not find position data for symbol {symbol}")
        except Exception as e:
            logger.error(f"Error handling exit click for row {row}: {e}")

    @Slot()
    def _on_exit_all_clicked(self):
        """Handle exit all positions button click."""
        try:
            if self._positions_cache:
                reply = QApplication.instance().exec()  # This would show a confirmation dialog
                self.exit_all_positions_requested.emit()
                logger.info("Exit all positions requested")
        except Exception as e:
            logger.error(f"Error handling exit all click: {e}")

    @Slot(int, int)
    def _on_cell_clicked(self, row: int, column: int):
        """Handle cell click for chart updates."""
        if column == 6:  # Don't trigger on exit button column
            return
        try:
            symbol = self.table.item(row, 0).text()
            self.symbol_selected.emit(symbol)
            logger.debug(f"Symbol selected from positions: {symbol}")
        except Exception as e:
            logger.error(f"Error handling cell click: {e}")

    @Slot(int, int)
    def _on_cell_double_clicked(self, row: int, column: int):
        """Handle cell double-click for position details."""
        if column == 6:
            return
        try:
            symbol = self.table.item(row, 0).text()
            self.position_details_requested.emit(symbol)
            logger.debug(f"Position details requested for {symbol}")
        except Exception as e:
            logger.error(f"Error handling cell double-click: {e}")

    def _show_context_menu(self, position):
        """Show context menu for advanced actions."""
        try:
            item = self.table.itemAt(position)
            if not item:
                return

            row = item.row()
            symbol = self.table.item(row, 0).text()
            pos = self._positions_cache.get(symbol)

            if not pos:
                return

            menu = QMenu(self)

            # Chart action
            chart_action = QAction("View Chart", self)
            chart_action.triggered.connect(lambda: self.symbol_selected.emit(symbol))
            menu.addAction(chart_action)

            # Alert action
            alert_action = QAction("Add Price Alert", self)
            alert_action.triggered.connect(lambda: self.add_alert_requested.emit(symbol, pos.ltp))
            menu.addAction(alert_action)

            menu.addSeparator()

            # Exit action
            exit_action = QAction("Exit Position", self)
            exit_action.triggered.connect(lambda: self.exit_position_requested.emit(pos.to_dict()))
            menu.addAction(exit_action)

            menu.exec(self.table.mapToGlobal(position))

        except Exception as e:
            logger.error(f"Error showing context menu: {e}")

    # === UTILITY METHODS ===

    def get_all_tokens(self) -> List[int]:
        """Get all instrument tokens for market data subscription."""
        try:
            return [
                pos.contract.instrument_token
                for pos in self._positions_cache.values()
                if pos.contract and pos.contract.instrument_token
            ]
        except Exception as e:
            logger.error(f"Error getting tokens: {e}")
            return []

    def get_position_by_symbol(self, symbol: str) -> Optional[Position]:
        """Get position object by symbol."""
        return self._positions_cache.get(symbol)

    def has_positions(self) -> bool:
        """Check if there are any open positions."""
        return len(self._positions_cache) > 0

    def get_position_count(self) -> int:
        """Get current position count."""
        return len(self._positions_cache)

    def get_total_pnl(self) -> float:
        """Get total unrealized P&L."""
        return sum(pos.pnl for pos in self._positions_cache.values())

    def _apply_professional_styles(self):
        """Apply TC2000-style professional dark theme."""
        self.setStyleSheet("""
            /* Main Container */
            QWidget {
                background-color: #0a0a0a;
                color: #e0e0e0;
                font-family: "Segoe UI", "Arial", sans-serif;
                font-size: 11px;
            }

            /* Header */
            #positionsHeader {
                background-color: #1a1a1a;
                border-bottom: 1px solid #2a2a2a;
            }

            #positionsTitle {
                color: #64b5f6;
                font-size: 12px;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }

            #positionCount {
                color: #26a69a;
                font-size: 11px;
                font-weight: 500;
                background-color: #1a2e2a;
                padding: 2px 6px;
                border-radius: 8px;
                margin-left: 8px;
            }

            #refreshIndicator {
                font-size: 16px;
                font-weight: bold;
            }

            #exitAllButton {
                background-color: #d32f2f;
                color: #ffffff;
                border: none;
                padding: 4px 12px;
                border-radius: 4px;
                font-size: 10px;
                font-weight: 600;
                text-transform: uppercase;
            }

            #exitAllButton:hover {
                background-color: #b71c1c;
            }

            #exitAllButton:disabled {
                background-color: #424242;
                color: #757575;
            }

            /* Table */
            QTableWidget {
                background-color: #0a0a0a;
                border: none;
                gridline-color: #1a1a1a;
                selection-background-color: #1e3a5f;
                alternate-background-color: #0f0f0f;
            }

            QHeaderView::section {
                background-color: #1a1a1a;
                color: #9e9e9e;
                padding: 6px 8px;
                border: none;
                border-bottom: 1px solid #2a2a2a;
                border-right: 1px solid #1a1a1a;
                font-weight: 600;
                font-size: 10px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }

            QHeaderView::section:hover {
                background-color: #2a2a2a;
                color: #e0e0e0;
            }

            QTableWidget::item {
                padding: 4px 6px;
                border-bottom: 1px solid #1a1a1a;
                background-color: transparent;
            }

            QTableWidget::item:selected {
                background-color: #1e3a5f;
                color: #ffffff;
            }

            QTableWidget::item:hover {
                background-color: #1a1a1a;
            }

            /* Exit Button */
            #exitButton {
                background-color: #424242;
                color: #e0e0e0;
                border: none;
                border-radius: 10px;
                font-weight: bold;
                font-size: 12px;
            }

            #exitButton:hover {
                background-color: #d32f2f;
                color: #ffffff;
            }

            /* Footer */
            #positionsFooter {
                background-color: #1a1a1a;
                border-top: 1px solid #2a2a2a;
            }

            #footerLabel {
                color: #9e9e9e;
                font-size: 10px;
                font-weight: 600;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }

            #totalPnlValue {
                font-size: 18px;
                font-weight: 700;
            }

            #footerMetric {
                color: #757575;
                font-size: 9px;
                font-weight: 500;
            }

            /* Scrollbars */
            QScrollBar:vertical {
                background-color: #0a0a0a;
                width: 8px;
                border: none;
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
            }
        """)

        logger.info("Professional dark theme applied to positions table.")