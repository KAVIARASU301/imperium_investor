import logging
from typing import List, Dict, Optional
from datetime import datetime
from dataclasses import asdict
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
    Optimized positions table with efficient updates and no scrollbar.
    Features clean design and smooth user experience.
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
        self._sort_column = 3  # Default sort by P&L (now column 3)
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
        logger.info("Positions Table initialized.")

    def _setup_ui(self):
        """Setup the main UI layout with enhanced features."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header with refresh indicator only
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

        # Connect focus events to clear selection
        self.table.focusOutEvent = self._on_table_focus_out

    def _create_header(self) -> QFrame:
        """Create minimal header without status indicator."""
        header_frame = QFrame()
        header_frame.setObjectName("positionsHeader")
        header_frame.setFixedHeight(0)  # Remove header completely
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(0, 0, 0, 0)

        return header_frame

    def _configure_table(self):
        """Configure table with optimized column layout."""
        # Reduced to 5 columns: Symbol, Qty, Avg, P&L, Exit
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            "Symbol", "Qty", "Avg", "P&L", ""
        ])

        # Table behavior
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        # Clear selection when focus is lost
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        header = self.table.horizontalHeader()

        # Column sizing to match watchlist table layout
        # Symbol column - stretch to fill available space (like watchlist)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

        # Qty column - resize to contents (like LTP in watchlist)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)

        # Avg column - resize to contents (like Vol in watchlist)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        # P&L column - resize to contents (like Chg % in watchlist)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        # Exit button column - fixed width (same as watchlist remove button)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, 24)  # Minimal width for X button

        # Row height for compact appearance (matching watchlist exactly)
        self.table.verticalHeader().setDefaultSectionSize(28)

        # Header click for sorting
        header.sectionClicked.connect(self._on_header_clicked)

    def _create_enhanced_footer(self) -> QFrame:
        """Create enhanced footer with detailed summary."""
        footer_frame = QFrame()
        footer_frame.setObjectName("positionsFooter")
        footer_frame.setFixedHeight(42)
        footer_layout = QVBoxLayout(footer_frame)
        footer_layout.setContentsMargins(12, 4, 12, 4)
        footer_layout.setSpacing(2)

        # Top row: Total P&L - simplified layout
        top_row = QHBoxLayout()

        total_pnl_container = QLabel()
        total_pnl_container.setObjectName("footerMetric")

        self.total_pnl_label = QLabel("Total P&L: ₹0.00")
        self.total_pnl_label.setObjectName("footerMetric")

        top_row.addWidget(self.total_pnl_label)
        top_row.addStretch()

        # Bottom row: Additional metrics with separators
        bottom_row = QHBoxLayout()

        self.investment_label = QLabel("Investment: ₹0")
        self.investment_label.setObjectName("footerMetric")

        separator1 = QLabel(" | ")
        separator1.setObjectName("footerMetric")

        self.returns_label = QLabel("Returns: 0.00%")
        self.returns_label.setObjectName("footerMetric")

        separator2 = QLabel(" | ")
        separator2.setObjectName("footerMetric")

        self.last_update_label = QLabel("Updated: Never")
        self.last_update_label.setObjectName("footerMetric")

        bottom_row.addWidget(self.investment_label)
        bottom_row.addWidget(separator1)
        bottom_row.addWidget(self.returns_label)
        bottom_row.addWidget(separator2)
        bottom_row.addWidget(self.last_update_label)
        bottom_row.addStretch()

        footer_layout.addLayout(top_row)
        footer_layout.addLayout(bottom_row)

        return footer_frame

    @Slot(list)
    def update_positions(self, positions: List[Position]):
        """
        Efficient position update that preserves scroll position and only updates changed data.
        """
        try:
            start_time = datetime.now()

            # Store current scroll position
            v_scrollbar = self.table.verticalScrollBar()
            h_scrollbar = self.table.horizontalScrollBar()
            v_scroll_pos = v_scrollbar.value()
            h_scroll_pos = h_scrollbar.value()

            # Create new cache for positions
            new_cache = {pos.tradingsymbol: pos for pos in positions}

            # Calculate totals
            total_pnl = 0.0
            total_investment = 0.0

            # Sort positions by the current sort criteria
            sorted_positions = self._sort_positions(positions)

            # Update table efficiently
            self._update_table_efficiently(sorted_positions, new_cache)

            # Calculate totals from sorted positions
            for pos in sorted_positions:
                total_pnl += pos.pnl
                total_investment += abs(pos.quantity * pos.average_price)

            # Update cache
            self._positions_cache = new_cache

            # Update summary information
            self._update_summary(total_pnl, total_investment, len(positions))

            # Restore scroll position
            QTimer.singleShot(0, lambda: self._restore_scroll_position(v_scroll_pos, h_scroll_pos))

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



    def _restore_scroll_position(self, v_pos: int, h_pos: int):
        """Restore scroll position after table update."""
        try:
            self.table.verticalScrollBar().setValue(v_pos)
            self.table.horizontalScrollBar().setValue(h_pos)
        except Exception as e:
            logger.debug(f"Could not restore scroll position: {e}")

    def _sort_positions(self, positions: List[Position]) -> List[Position]:
        """Sort positions based on current sort criteria."""
        try:
            if self._sort_column == 0:  # Symbol
                key_func = lambda p: p.tradingsymbol
            elif self._sort_column == 1:  # Quantity
                key_func = lambda p: p.quantity
            elif self._sort_column == 2:  # Average Price
                key_func = lambda p: p.average_price
            elif self._sort_column == 3:  # P&L
                key_func = lambda p: p.pnl
            else:
                return positions

            reverse = self._sort_order == Qt.SortOrder.DescendingOrder
            return sorted(positions, key=key_func, reverse=reverse)

        except Exception as e:
            logger.error(f"Error sorting positions: {e}")
            return positions


    def _set_row_tooltips(self, row: int, pos: Position):
        """Set informative tooltips for table cells."""
        try:
            investment = abs(pos.quantity * pos.average_price)
            current_value = abs(pos.quantity * pos.ltp)
            pnl_percent = ((pos.ltp - pos.average_price) / pos.average_price * 100) if pos.average_price != 0 else 0

            tooltip_base = f"""Symbol: {pos.tradingsymbol}
Product: {pos.product}
Exchange: {pos.exchange}
LTP: ₹{pos.ltp:.2f}
Investment: ₹{investment:,.2f}
Current Value: ₹{current_value:,.2f}
P&L%: {pnl_percent:+.2f}%"""

            for col in range(4):
                self.table.item(row, col).setToolTip(tooltip_base.strip())

        except Exception as e:
            logger.error(f"Error setting tooltips for row {row}: {e}")

    def _create_exit_button(self, row: int) -> QPushButton:
        """Create enhanced exit button with watchlist-style cross button."""
        exit_btn = QPushButton("×")
        exit_btn.setObjectName("exitButton")
        exit_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        exit_btn.setFixedSize(16, 16)  # Match watchlist remove button size
        exit_btn.setToolTip("Exit this position")
        exit_btn.clicked.connect(lambda: self._on_exit_clicked(row))
        return exit_btn

    def _update_summary(self, total_pnl: float, total_investment: float, position_count: int):
        """Update summary labels with current data."""
        try:
            # Update total P&L with simple inline styling
            profit_color = "#26a69a"
            loss_color = "#ef5350"
            color = profit_color if total_pnl >= 0 else loss_color

            self.total_pnl_label.setText(f"Total P&L: ₹{total_pnl:,.2f}")
            self.total_pnl_label.setStyleSheet(
                f"color: {color}; background-color: transparent; border: none; margin: 0px; padding: 0px; font-size: 12px; font-weight: normal;")

            # Update metrics with simple text
            self.investment_label.setText(f"Investment: ₹{total_investment:,.0f}")
            returns_percent = (total_pnl / total_investment * 100) if total_investment > 0 else 0
            self.returns_label.setText(f"Returns: {returns_percent:+.2f}%")
            self.last_update_label.setText(f"Updated: {self._last_update_time.strftime('%H:%M:%S')}")

        except Exception as e:
            logger.error(f"Error updating summary: {e}")

    def _check_data_freshness(self):
        """Check if data is getting stale and update indicator."""
        try:
            # Data freshness check without visual indicator
            time_since_update = (datetime.now() - self._last_update_time).total_seconds()

            if time_since_update > 60:
                logger.debug("Position data may be stale (>60s)")
            elif time_since_update > 30:
                logger.debug("Position data is getting old (>30s)")

        except Exception as e:
            logger.error(f"Error checking data freshness: {e}")

    # === EVENT HANDLERS ===

    @Slot(int)
    def _on_header_clicked(self, logical_index: int):
        """Handle header clicks for sorting."""
        if logical_index == 4:  # Don't sort by action column
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
                position = self._positions_cache[symbol]
                # Convert Position dataclass to dictionary
                position_data = asdict(position)
                self.exit_position_requested.emit(position_data)
                logger.info(f"Exit position requested for {symbol}")
            else:
                logger.warning(f"Could not find position data for symbol {symbol}")
        except Exception as e:
            logger.error(f"Error handling exit click for row {row}: {e}")

    @Slot(int, int)
    def _on_table_focus_out(self, event):
        """Clear selection when table loses focus (matching positions table)."""
        try:
            self.table.clearSelection()
            # Call the original focusOutEvent if it exists
            if hasattr(QTableWidget, 'focusOutEvent'):
                QTableWidget.focusOutEvent(self.table, event)
        except Exception as e:
            logger.debug(f"Error clearing selection on focus out: {e}")

    def _on_cell_clicked(self, row: int, column: int):
        """Handle cell click for chart updates."""
        if column == 4:  # Don't trigger on exit button column
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
        if column == 4:
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
            exit_action.triggered.connect(lambda: self.exit_position_requested.emit(asdict(pos)))
            menu.addAction(exit_action)

            menu.exec(self.table.mapToGlobal(position))

        except Exception as e:
            logger.error(f"Error showing context menu: {e}")


    # === UTILITY METHODS ===

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
            /* Main Container with thick top border separator */
            QWidget {
                background-color: #0a0a0a;
                color: #e0e0e0;
                font-family: "Segoe UI", "Arial", sans-serif;
                font-size: 11px;
                border-top: 3px solid #404040;
            }

            /* Header */
            #positionsHeader {
                background-color: #1a1a1a;
                border-bottom: 1px solid #2a2a2a;
            }

            /* Table */
            QTableWidget {
                background-color: #0a0a0a;
                border: none;
                gridline-color: #2a2a2a;
                selection-background-color: #1e3a5f;
                alternate-background-color: #0f0f0f;
                outline: none;
                show-decoration-selected: 0;
            }

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
            }

            QTableWidget::item {
                padding: 5px 8px;
                border-bottom: 1px solid #1a1a1a;
                background-color: transparent;
                color: #e0e0e0;
                font-size: 12px;
            }

            QTableWidget::item:selected {
                background-color: #1e3a5f;
                outline: none;
                border: none;
            }

            QTableWidget::item:focus {
                background-color: #1e3a5f;
                outline: none;
                border: none;
            }

            QTableWidget::item:hover {
                background-color: transparent;
            }

            /* Exit Button - Matching Watchlist Style Exactly */
            #exitButton {
                background-color: transparent;
                color: #cc4444;
                border: none;
                font-weight: bold;
                font-size: 12px;
                border-radius: 8px;
                padding: 0px;
                margin: 0px;
            }

            #exitButton:hover {
                color: #ff6666;
                background-color: #2a1f1f;
            }

            /* Footer */
            #positionsFooter {
                background-color: #000000;
                border-top: 1px solid #2a2a2a;
            }

            #footerMetric {
                color: #a0a0a0;
                font-size: 9px;
                font-weight: normal;
                background-color: transparent;
                border: none;
                margin: 0px;
                padding: 0px;
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

    @Slot(list)
    def update_positions(self, positions: List[Position]):
        """FIXED: Enhanced position update with better change detection and forced refresh"""
        try:
            if not hasattr(self, '_update_counter'):
                self._update_counter = 0
            self._update_counter += 1

            start_time = datetime.now()
            logger.debug(f"Position table update #{self._update_counter}: {len(positions)} positions")

            # Store current scroll position
            v_scrollbar = self.table.verticalScrollBar()
            h_scrollbar = self.table.horizontalScrollBar()
            v_scroll_pos = v_scrollbar.value()
            h_scroll_pos = h_scrollbar.value()

            # Create new cache for positions
            new_cache = {pos.tradingsymbol: pos for pos in positions}

            # Calculate totals
            total_pnl = 0.0
            total_investment = 0.0

            # Sort positions by the current sort criteria
            sorted_positions = self._sort_positions(positions)

            # CRITICAL: Always update all rows if we haven't updated in a while
            force_full_update = (
                    self._update_counter % 10 == 0 or  # Every 10th update
                    len(new_cache) != len(self._positions_cache) or  # Position count changed
                    not hasattr(self, '_last_full_update') or
                    (datetime.now() - getattr(self, '_last_full_update', datetime.min)).seconds > 30  # 30 seconds
            )

            if force_full_update:
                logger.debug("Performing full table update")
                self._last_full_update = datetime.now()

            # Update table efficiently or fully
            self._update_table_efficiently(sorted_positions, new_cache, force_full_update)

            # Calculate totals from sorted positions
            for pos in sorted_positions:
                total_pnl += pos.pnl
                total_investment += abs(pos.quantity * pos.average_price)

            # Update cache
            self._positions_cache = new_cache

            # Update summary information
            self._update_summary(total_pnl, total_investment, len(positions))

            # Restore scroll position
            QTimer.singleShot(0, lambda: self._restore_scroll_position(v_scroll_pos, h_scroll_pos))

            # Update performance metrics
            self._last_update_time = datetime.now()
            update_time = (datetime.now() - start_time).total_seconds() * 1000

            # Request token subscription for market data (critical for live updates)
            if positions:
                tokens = self.get_all_tokens()
                if tokens:
                    self.subscribe_tokens_requested.emit(tokens)
                    logger.debug(f"Requested subscription for {len(tokens)} position tokens")

            if self._update_counter % 5 == 0:  # Log every 5th update
                logger.info(
                    f"Position table update #{self._update_counter} completed in {update_time:.1f}ms for {len(positions)} positions")

        except Exception as e:
            logger.error(f"Error updating positions: {e}", exc_info=True)

    def _update_table_efficiently(self, sorted_positions: List[Position], new_cache: Dict[str, Position],
                                  force_full_update: bool = False):
        """FIXED: Enhanced table update with forced refresh option"""
        current_rows = self.table.rowCount()
        needed_rows = len(sorted_positions)

        # Add or remove rows as needed
        if needed_rows > current_rows:
            for _ in range(needed_rows - current_rows):
                self.table.insertRow(self.table.rowCount())
        elif needed_rows < current_rows:
            for _ in range(current_rows - needed_rows):
                self.table.removeRow(self.table.rowCount() - 1)

        # Update each row
        for row, pos in enumerate(sorted_positions):
            old_pos = self._positions_cache.get(pos.tradingsymbol)

            # Force update if requested or if this is a significant change
            if force_full_update:
                self._populate_row(row, pos, old_pos)
            else:
                self._update_row_if_needed(row, pos, old_pos)

    def _update_row_if_needed(self, row: int, pos: Position, old_pos: Optional[Position]):
        """FIXED: Enhanced change detection with more sensitive thresholds"""
        try:
            needs_update = (
                    old_pos is None or
                    old_pos.quantity != pos.quantity or
                    abs(old_pos.average_price - pos.average_price) > 0.01 or
                    abs(old_pos.pnl - pos.pnl) > 0.01 or  # Even 1 paisa change
                    abs(getattr(old_pos, 'ltp', 0) - getattr(pos, 'ltp', 0)) > 0.01  # LTP change
            )

            if needs_update:
                self._populate_row(row, pos, old_pos)
                if old_pos and abs(old_pos.pnl - pos.pnl) > 0.01:
                    logger.debug(f"Updated {pos.tradingsymbol}: P&L {old_pos.pnl:.2f} → {pos.pnl:.2f}")
        except Exception as e:
            logger.error(f"Error checking row update for row {row}: {e}")
            # On error, force update the row
            self._populate_row(row, pos, old_pos)

    def _populate_row(self, row: int, pos: Position, old_pos: Optional[Position] = None):
        """FIXED: Enhanced row population with better error handling"""
        try:
            # Ensure we have valid data
            if not hasattr(pos, 'tradingsymbol') or not pos.tradingsymbol:
                logger.warning(f"Invalid position data for row {row}")
                return

            # Create items for 4 data columns
            items = [
                QTableWidgetItem(str(pos.tradingsymbol)),
                QTableWidgetItem(str(getattr(pos, 'quantity', 0))),
                QTableWidgetItem(f"{getattr(pos, 'average_price', 0.0):.2f}"),
                QTableWidgetItem(f"{getattr(pos, 'pnl', 0.0):,.2f}")
            ]

            # Set alignments
            items[0].setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            for i in range(1, 4):
                items[i].setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

            # Apply colors
            self._apply_row_colors(items, pos, old_pos)

            # Set items in table (with error handling)
            for col, item in enumerate(items):
                try:
                    self.table.setItem(row, col, item)
                except Exception as item_error:
                    logger.error(f"Error setting item at row {row}, col {col}: {item_error}")

            # Add exit button (only if it doesn't exist)
            if not self.table.cellWidget(row, 4):
                try:
                    self.table.setCellWidget(row, 4, self._create_exit_button(row))
                except Exception as button_error:
                    logger.error(f"Error creating exit button for row {row}: {button_error}")

            # Set tooltips
            self._set_row_tooltips(row, pos)

        except Exception as e:
            logger.error(f"Error populating row {row}: {e}")

    def _apply_row_colors(self, items: List[QTableWidgetItem], pos: Position, old_pos: Optional[Position]):
        """FIXED: Enhanced color application with change detection"""
        try:
            # Color scheme
            profit_color = QColor(60, 179, 113)  # Medium Sea Green
            loss_color = QColor(220, 20, 60)  # Crimson
            neutral_color = QColor(169, 169, 169)  # DarkGray
            change_color = QColor("#ffeb3b")  # Yellow for changes

            # P&L coloring
            pnl = getattr(pos, 'pnl', 0.0)
            if pnl > 0:
                pnl_color = profit_color
            elif pnl < 0:
                pnl_color = loss_color
            else:
                pnl_color = neutral_color

            items[3].setForeground(pnl_color)  # P&L column

            # Neutral colors for other columns
            items[1].setForeground(neutral_color)  # Quantity
            items[2].setForeground(neutral_color)  # Average Price

            # Change indicators (highlight recent changes)
            if old_pos and abs(getattr(pos, 'pnl', 0) - getattr(old_pos, 'pnl', 0)) > 0.01:
                items[3].setBackground(change_color.lighter(180))
                # Remove highlight after a delay
                QTimer.singleShot(2000, lambda: self._remove_change_highlight(items[3]))

        except Exception as e:
            logger.error(f"Error applying row colors: {e}")

    def _remove_change_highlight(self, item: QTableWidgetItem):
        """Remove change highlight from table item"""
        try:
            if item:
                item.setBackground(QColor())  # Reset to default background
        except Exception as e:
            logger.debug(f"Error removing highlight: {e}")

    def get_all_tokens(self) -> List[int]:
        """FIXED: Enhanced token retrieval for position subscriptions"""
        try:
            tokens = []
            for pos in self._positions_cache.values():
                token = None

                # Method 1: Direct instrument token
                if hasattr(pos, 'instrument_token') and pos.instrument_token:
                    token = pos.instrument_token

                # Method 2: Contract token
                elif hasattr(pos, 'contract'):
                    if isinstance(pos.contract, dict):
                        token = pos.contract.get('instrument_token')
                    elif hasattr(pos.contract, 'instrument_token'):
                        token = pos.contract.instrument_token

                # Method 3: Lookup from parent's instrument map
                if not token and hasattr(pos, 'tradingsymbol'):
                    main_window = self.parent()
                    while main_window and not hasattr(main_window, 'instrument_map'):
                        main_window = main_window.parent()

                    if main_window and hasattr(main_window, 'instrument_map'):
                        instrument_map = main_window.instrument_map
                        if pos.tradingsymbol in instrument_map:
                            instrument = instrument_map[pos.tradingsymbol]
                            token = instrument.get('instrument_token')

                if token and token > 0:
                    tokens.append(token)
                    logger.debug(f"Position token: {pos.tradingsymbol} -> {token}")

            logger.info(f"Positions table returning {len(tokens)} tokens for subscription")
            return tokens
        except Exception as e:
            logger.error(f"Error getting position tokens: {e}")
            return []

    def force_refresh_display(self):
        """Force refresh the entire table display"""
        try:
            logger.info("🔄 Force refreshing positions table display")

            # Get current positions from cache
            if self._positions_cache:
                positions_list = list(self._positions_cache.values())

                # Mark for full update
                self._last_full_update = datetime.min

                # Trigger update
                self.update_positions(positions_list)

                logger.info(f"✅ Force refreshed {len(positions_list)} positions")
            else:
                logger.warning("No positions in cache to refresh")

        except Exception as e:
            logger.error(f"Error in force refresh: {e}")

    def debug_table_state(self):
        """Debug method to check table state"""
        try:
            logger.info("=== POSITIONS TABLE DEBUG ===")
            logger.info(f"Cached positions: {len(self._positions_cache)}")
            logger.info(f"Table rows: {self.table.rowCount()}")
            logger.info(f"Update counter: {getattr(self, '_update_counter', 0)}")
            logger.info(f"Last update: {getattr(self, '_last_update_time', 'Never')}")

            # Check each cached position
            for symbol, pos in self._positions_cache.items():
                ltp = getattr(pos, 'ltp', 0)
                pnl = getattr(pos, 'pnl', 0)
                last_update = getattr(pos, '_last_ltp_update', 'Never')
                logger.info(f"  {symbol}: LTP={ltp}, P&L={pnl}, Update={last_update}")

            logger.info("=== DEBUG END ===")

        except Exception as e:
            logger.error(f"Error in table debug: {e}")

    # Add this method to test table updates manually
    def test_table_updates(self):
        """Test method to verify table is updating correctly"""
        try:
            logger.info("🧪 Testing table updates...")

            # Debug current state
            self.debug_table_state()

            # Force a refresh
            self.force_refresh_display()

            logger.info("✅ Table test complete")

        except Exception as e:
            logger.error(f"Error in table test: {e}")