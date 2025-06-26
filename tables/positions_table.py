import logging
from typing import List, Dict, Optional
from datetime import datetime
from dataclasses import asdict
from functools import partial
from PySide6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QPushButton, QLabel, QVBoxLayout,
    QWidget, QHeaderView, QFrame, QHBoxLayout, QAbstractItemView, QMenu
)
from PySide6.QtCore import Qt, Signal, Slot, QTimer
from PySide6.QtGui import QColor, QCursor, QAction

from utils.data_models import Position

logger = logging.getLogger(__name__)


class PositionsTable(QWidget):
    """
    Enhanced positions table with consistent styling matching watchlist and scanner tables.
    Features clean design and smooth user experience with minimal footer.
    """
    # Signals for integration with main window
    exit_position_requested = Signal(dict)
    subscribe_tokens_requested = Signal(list)
    symbol_selected = Signal(str)
    position_details_requested = Signal(str)
    add_alert_requested = Signal(str, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._positions_cache: Dict[str, Position] = {}
        self._sort_column = 3  # Default sort by P&L
        self._sort_order = Qt.SortOrder.DescendingOrder

        # Performance tracking
        self._last_update_time = datetime.now()
        self._update_count = 0

        # Auto-refresh timer for stale data indication
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._check_data_freshness)
        self._refresh_timer.start(5000)  # Check every 5 seconds

        self._setup_ui()
        self._apply_consistent_styles()
        logger.info("Positions Table initialized with consistent styling.")

    def _setup_ui(self):
        """Setup the main UI layout with enhanced features."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Main table (no header needed)
        self.table = QTableWidget()
        self._configure_table()
        main_layout.addWidget(self.table, 1)

        # Simplified footer with summary information
        footer = self._create_minimal_footer()
        main_layout.addWidget(footer)

        # Connect table signals
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        # Connect focus events to clear selection
        self.table.focusOutEvent = self._on_table_focus_out

    def _configure_table(self):
        """Configure table with layout matching watchlist table exactly."""
        # FIXED: 5 columns to match watchlist layout
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            "Symbol", "Qty", "Avg", "P&L", ""
        ])

        # Table behavior - EXACTLY matching watchlist and scanner
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setVisible(True)

        # EXACT match to watchlist behavior
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)  # CRITICAL: No grid lines like watchlist
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        header = self.table.horizontalHeader()

        # Set header properties matching watchlist
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)

        # Column sizing EXACTLY matching watchlist table layout
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)  # Symbol - takes remaining space
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)  # Qty - fixed width
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)  # Avg - fixed width
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)  # P&L - fixed width
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)  # Exit button - fixed width

        # Set optimal fixed widths matching watchlist pattern
        self.table.setColumnWidth(1, 50)  # Qty - compact
        self.table.setColumnWidth(2, 70)  # Avg - enough for "0000.00"
        self.table.setColumnWidth(3, 80)  # P&L - enough for "+00,000.00"
        self.table.setColumnWidth(4, 24)  # Exit button - minimal

        # Row height matching watchlist exactly
        self.table.verticalHeader().setDefaultSectionSize(28)

        # Header click for sorting
        header.sectionClicked.connect(self._on_header_clicked)

        # Set cursor for header (matching watchlist)
        header.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def _create_minimal_footer(self) -> QFrame:
        """Create minimal footer with essential summary only."""
        footer_frame = QFrame()
        footer_frame.setObjectName("positionsFooter")
        footer_frame.setFixedHeight(28)  # Reduced height for minimal design

        footer_layout = QHBoxLayout(footer_frame)
        footer_layout.setContentsMargins(12, 4, 12, 4)
        footer_layout.setSpacing(12)

        # Total P&L (main metric)
        self.total_pnl_label = QLabel("Total P&L: ₹0.00")
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

        # Push everything to the left
        footer_layout.addStretch()

        return footer_frame

    @Slot(list)
    def update_positions(self, positions: List[Position]):
        """Efficient position update that preserves scroll position."""
        try:
            start_time = datetime.now()
            v_scrollbar = self.table.verticalScrollBar()
            h_scrollbar = self.table.horizontalScrollBar()
            v_scroll_pos = v_scrollbar.value()
            h_scroll_pos = h_scrollbar.value()

            new_cache = {pos.tradingsymbol: pos for pos in positions}
            sorted_positions = self._sort_positions(positions)
            self._update_table_efficiently(sorted_positions, new_cache)

            total_pnl = sum(pos.pnl for pos in sorted_positions)
            total_investment = sum(abs(pos.quantity * pos.average_price) for pos in sorted_positions)

            self._positions_cache = new_cache
            self._update_summary(total_pnl, total_investment, len(positions))
            QTimer.singleShot(0, lambda: self._restore_scroll_position(v_scroll_pos, h_scroll_pos))

            self._update_count += 1
            self._last_update_time = datetime.now()
            update_time = (datetime.now() - start_time).total_seconds() * 1000

            if positions:
                tokens = self.get_all_tokens()
                self.subscribe_tokens_requested.emit(tokens)

            logger.debug(f"Position update completed in {update_time:.1f}ms for {len(positions)} positions.")
        except Exception as e:
            logger.error(f"Error updating positions: {e}", exc_info=True)

    def _restore_scroll_position(self, v_pos: int, h_pos: int):
        """Restore scroll position after table update."""
        self.table.verticalScrollBar().setValue(v_pos)
        self.table.horizontalScrollBar().setValue(h_pos)

    def _sort_positions(self, positions: List[Position]) -> List[Position]:
        """Sort positions based on current sort criteria."""
        try:
            key_map = {0: 'tradingsymbol', 1: 'quantity', 2: 'average_price', 3: 'pnl'}
            sort_key = key_map.get(self._sort_column)
            if not sort_key:
                return positions

            reverse = self._sort_order == Qt.SortOrder.DescendingOrder
            return sorted(positions, key=lambda p: getattr(p, sort_key, 0), reverse=reverse)
        except Exception as e:
            logger.error(f"Error sorting positions: {e}")
            return positions

    def _update_table_efficiently(self, sorted_positions: List[Position], new_cache: Dict[str, Position]):
        """Efficiently update table rows."""
        self.table.setRowCount(len(sorted_positions))
        for row, pos in enumerate(sorted_positions):
            self._populate_row(row, pos)

    def _populate_row(self, row: int, pos: Position):
        """Populate a single row with position data and exit button."""
        try:
            # Create items for all columns (matching watchlist pattern)
            for i in range(4):  # First 4 columns get items
                self.table.setItem(row, i, QTableWidgetItem())

            # Add exit button in last column (matching watchlist remove button)
            self.table.setCellWidget(row, 4, self._create_exit_button(row))

            # Update with current data
            self._update_row_data(row, pos)

        except Exception as e:
            logger.error(f"Error populating row {row}: {e}")

    def _create_exit_button(self, row) -> QPushButton:
        """Creates a minimal 'x' button to exit position (matching watchlist style)."""
        exit_btn = QPushButton("×")
        exit_btn.setObjectName("exitButton")
        # Use Qt method for cursor instead of CSS (matching watchlist)
        exit_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        exit_btn.setFixedSize(16, 16)
        exit_btn.clicked.connect(partial(self._exit_position_at_row, row))
        return exit_btn

    def _update_row_data(self, row: int, pos: Position):
        """Update row data with proper formatting matching watchlist style."""
        if row >= self.table.rowCount():
            return

        # Ensure items exist
        for col_idx in range(4):
            if not self.table.item(row, col_idx):
                self.table.setItem(row, col_idx, QTableWidgetItem())

        # Set text values with formatting
        self.table.item(row, 0).setText(pos.tradingsymbol)
        self.table.item(row, 1).setText(str(pos.quantity))
        self.table.item(row, 2).setText(f"{pos.average_price:.2f}")
        self.table.item(row, 3).setText(f"{pos.pnl:,.2f}")

        # Apply colors (matching watchlist pattern)
        profit_color = QColor(60, 179, 113)  # Medium Sea Green
        loss_color = QColor(220, 20, 60)  # Crimson
        neutral_color = QColor(169, 169, 169)  # DarkGray

        pnl_color = profit_color if pos.pnl >= 0 else loss_color

        # Color the P&L column
        self.table.item(row, 3).setForeground(pnl_color)

        # Set alignments (matching watchlist exactly)
        self.table.item(row, 0).setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.item(row, 1).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.item(row, 2).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.item(row, 3).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # Set tooltips
        self._set_row_tooltips(row, pos)

    def _set_row_tooltips(self, row: int, pos: Position):
        """Set informative tooltips for table cells."""
        investment = abs(pos.quantity * pos.average_price)
        pnl_percent = ((pos.ltp - pos.average_price) / pos.average_price * 100) if pos.average_price != 0 else 0
        tooltip_text = f"""
            Symbol: {pos.tradingsymbol}
            Product: {pos.product}
            LTP: ₹{pos.ltp:.2f}
            Investment: ₹{investment:,.2f}
            P&L %: {pnl_percent:+.2f}%
                    """.strip()
        for col in range(4):  # Only first 4 columns have items
            if self.table.item(row, col):
                self.table.item(row, col).setToolTip(tooltip_text)

    def _update_summary(self, total_pnl: float, total_investment: float, position_count: int):
        """Update summary labels with current data."""
        profit_color = "#26a69a"
        loss_color = "#ef5350"
        color = profit_color if total_pnl >= 0 else loss_color

        # Update main P&L label with color
        self.total_pnl_label.setText(f"Total P&L: ₹{total_pnl:,.2f}")
        self.total_pnl_label.setStyleSheet(f"color: {color}; background-color: transparent; border: none;")

        # Update other metrics
        self.investment_label.setText(f"Investment: ₹{total_investment:,.0f}")
        returns_percent = (total_pnl / total_investment * 100) if total_investment > 0 else 0
        self.returns_label.setText(f"Returns: {returns_percent:+.2f}%")

    def _check_data_freshness(self):
        """Check if data is getting stale."""
        time_since_update = (datetime.now() - self._last_update_time).total_seconds()
        if time_since_update > 30:
            logger.debug("Position data might be stale.")

    @Slot(int)
    def _on_header_clicked(self, logical_index: int):
        """Handle header clicks for sorting."""
        if logical_index == 4:  # Don't sort on exit button column
            return

        if self._sort_column == logical_index:
            self._sort_order = Qt.SortOrder.AscendingOrder if self._sort_order == Qt.SortOrder.DescendingOrder else Qt.SortOrder.DescendingOrder
        else:
            self._sort_column = logical_index
            self._sort_order = Qt.SortOrder.DescendingOrder
        self.update_positions(list(self._positions_cache.values()))

    def _exit_position_at_row(self, row: int):
        """Exit position at specific row."""
        if 0 <= row < self.table.rowCount():
            try:
                symbol = self.table.item(row, 0).text()
                position = self._positions_cache.get(symbol)
                if position:
                    self.exit_position_requested.emit(asdict(position))
            except Exception as e:
                logger.error(f"Error exiting position at row {row}: {e}")

    @Slot(int)
    def _on_table_focus_out(self, event):
        """Clear selection when table loses focus."""
        try:
            self.table.clearSelection()
            # Call the original focusOutEvent if it exists
            if hasattr(QTableWidget, 'focusOutEvent'):
                QTableWidget.focusOutEvent(self.table, event)
        except Exception as e:
            logger.debug(f"Error clearing selection on focus out: {e}")

    def _on_cell_clicked(self, row: int, column: int):
        """Handle cell click for chart updates."""
        if column != 4 and row < self.table.rowCount():  # Don't handle exit button clicks
            try:
                symbol = self.table.item(row, 0).text()
                self.symbol_selected.emit(symbol)
            except Exception as e:
                logger.error(f"Error handling cell click: {e}")

    @Slot(int, int)
    def _on_cell_double_clicked(self, row: int, column: int):
        """Handle cell double-click for position details."""
        if column != 4:  # Don't handle exit button double-clicks
            try:
                symbol = self.table.item(row, 0).text()
                self.position_details_requested.emit(symbol)
            except Exception as e:
                logger.error(f"Error handling cell double-click: {e}")

    def _show_context_menu(self, position):
        """Show context menu for advanced actions."""
        item = self.table.itemAt(position)
        if not item:
            return
        row = item.row()
        symbol = self.table.item(row, 0).text()
        pos = self._positions_cache.get(symbol)
        if not pos:
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1a1a1a;
                color: #e0e0e0;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                padding: 4px;
                font-size: 12px;
            }
            QMenu::item {
                padding: 8px 16px;
                border-radius: 2px;
                margin: 1px;
            }
            QMenu::item:selected {
                background-color: #6a9cff;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background-color: #3a3a3a;
                margin: 4px 0px;
            }
        """)

        chart_action = QAction("View Chart", self)
        chart_action.triggered.connect(lambda: self.symbol_selected.emit(symbol))
        menu.addAction(chart_action)

        alert_action = QAction("Add Price Alert", self)
        alert_action.triggered.connect(lambda: self.add_alert_requested.emit(symbol, pos.ltp))
        menu.addAction(alert_action)

        menu.addSeparator()

        exit_action = QAction("Exit Position", self)
        exit_action.triggered.connect(lambda: self.exit_position_requested.emit(asdict(pos)))
        menu.addAction(exit_action)

        menu.exec(self.table.mapToGlobal(position))

    def get_all_tokens(self) -> List[int]:
        """Get instrument tokens for all current positions."""
        return [
            pos.contract.instrument_token
            for pos in self._positions_cache.values()
            if hasattr(pos, 'contract') and hasattr(pos.contract, 'instrument_token')
        ]

    def get_position_by_symbol(self, symbol: str) -> Optional[Position]:
        """Get position by symbol."""
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

    def _apply_consistent_styles(self):
        """Apply styling consistent with watchlist and scanner tables."""
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
                color: #e0e0e0;
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

            QHeaderView::down-arrow {
                color: #6a9cff;
                width: 8px;
                height: 8px;
                subcontrol-position: center right;
                subcontrol-origin: margin;
                margin-right: 2px;
            }

            QHeaderView::up-arrow {
                color: #6a9cff;
                width: 8px;
                height: 8px;
                subcontrol-position: center right;
                subcontrol-origin: margin;
                margin-right: 2px;
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