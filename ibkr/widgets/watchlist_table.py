# Enhanced watchlist_table.py - FIXED width consistency with position table
import logging
import json
import os
from typing import List, Dict
from functools import partial

from PySide6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QPushButton, QVBoxLayout, QWidget,
    QHeaderView, QAbstractItemView, QMenu, QTabWidget
)
from PySide6.QtCore import Qt, Signal, Slot, QPoint, QTimer
from PySide6.QtGui import QColor, QCursor, QAction, QResizeEvent, QFontMetrics

logger = logging.getLogger(__name__)

# Separate files for each watchlist category
WATCHLIST_FILES = {
    "Breakouts": "user_data/watchlist_breakouts.json",
    "EP": "user_data/watchlist_episodic.json",
    "Parabolic": "user_data/watchlist_parabolic.json"
}


class TradingTable(QTableWidget):
    """
    Enhanced trading table with FIXED data persistence and real-time updates
    """
    symbol_selected = Signal(str)
    place_order_requested = Signal(dict)
    advanced_buy_order_requested = Signal(str)
    advanced_sell_order_requested = Signal(str)
    bracket_order_requested = Signal(str)

    def __init__(self, category: str, parent=None):
        super().__init__(parent)
        self.category = category
        self._instrument_map: Dict[str, Dict] = {}
        self._watchlist_data: Dict[str, Dict] = {}
        self._symbol_to_row: Dict[str, int] = {}
        self._data_update_timer = QTimer()
        self._dirty_symbols = set()

        # Initialize empty watchlist data
        self._watchlist_symbols = set()  # Track symbols separately
        self._last_widget_width = 0  # Reset to force update

        self._configure_table()
        self._connect_signals()
        self._setup_data_refresh()

    def _configure_table(self):
        """FIXED table configuration with proper column sizing matching scanner."""
        self.setColumnCount(5)
        self.setHorizontalHeaderLabels(["Symbol", "LTP", "Vol", "Chg %", ""])

        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setVisible(True)

        # Set header style for better visibility
        header = self.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)

        # Table behavior
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Enable sorting
        self.setSortingEnabled(True)
        header.sectionClicked.connect(self._on_header_clicked)
        header.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        # FIXED: Column sizing EXACTLY matching scanner table - Symbol stretches, others fixed
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)  # Symbol
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)  # LTP - fixed width
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)  # Volume - fixed width
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)  # Chg % - fixed width
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)  # Remove button - fixed width

        # FIXED: Set compact fixed widths for right-side columns
        self._adjust_symbol_column_width()
        self.setColumnWidth(1, 70)  # LTP - enough for "0000.00"
        self.setColumnWidth(2, 70)  # Volume - enough for "999 K" or "9.9 L"
        self.setColumnWidth(3, 60)  # Change % - enough for "+00.00%"
        self.setColumnWidth(4, 24)  # Remove button - minimal

        # Row height for compact appearance
        self.verticalHeader().setDefaultSectionSize(28)

        # Initialize sorting state
        self._sort_column = -1
        self._sort_order = Qt.SortOrder.AscendingOrder


    def _adjust_symbol_column_width(self):
        """Keep symbol column compact using ~70% of the longest visible symbol length."""
        metrics = QFontMetrics(self.font())
        longest_symbol_len = 0

        for row in range(self.rowCount()):
            symbol_item = self.item(row, 0)
            if not symbol_item:
                continue
            longest_symbol_len = max(longest_symbol_len, len(symbol_item.text().strip()))

        target_chars = max(4, int(round(longest_symbol_len * 0.7))) if longest_symbol_len > 0 else 6
        compact_width = metrics.horizontalAdvance("W" * target_chars) + 18
        header_width = metrics.horizontalAdvance("Symbol") + 20
        max_compact_width = metrics.horizontalAdvance("W" * 10) + 22
        symbol_width = min(max(compact_width, header_width), max_compact_width)

        self.setColumnWidth(0, symbol_width)

    def _connect_signals(self):
        """Connect table signals."""
        self.cellClicked.connect(self._on_cell_clicked)
        self.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_enhanced_context_menu)

        # Connect focus events to clear selection (from position table)
        self.focusOutEvent = self._on_table_focus_out

    def _on_cell_double_clicked(self, row: int, column: int):
        """Handle cell double-click for symbol details."""
        if column != 4 and row < self.rowCount():
            try:
                symbol = self.item(row, 0).text()
                if symbol and symbol != 'N/A':
                    # Emit signal for detailed view or advanced chart
                    logger.info(f"Double-clicked on {symbol} - opening detailed view")
                    self.symbol_selected.emit(symbol)  # Can be enhanced later for different action
            except (AttributeError, TypeError):
                logger.warning(f"Could not get symbol from double-clicked row {row}.")

    def _on_table_focus_out(self, event):
        """Clear selection when table loses focus (from position table)."""
        try:
            self.clearSelection()
            # Call the original focusOutEvent if it exists
            if hasattr(QTableWidget, 'focusOutEvent'):
                QTableWidget.focusOutEvent(self, event)
        except Exception as e:
            logger.debug(f"Error clearing selection on focus out: {e}")

    def _on_header_clicked(self, logical_index: int):
        """Handle header clicks for sorting."""
        if logical_index == 4:  # Don't sort on remove button column
            return

        # Toggle sort order if clicking the same column
        if self._sort_column == logical_index:
            self._sort_order = (Qt.SortOrder.DescendingOrder
                                if self._sort_order == Qt.SortOrder.AscendingOrder
                                else Qt.SortOrder.AscendingOrder)
        else:
            self._sort_column = logical_index
            # Default sort order based on column
            if logical_index == 3:  # Change % column - default to descending (the best performers first)
                self._sort_order = Qt.SortOrder.DescendingOrder
            else:
                self._sort_order = Qt.SortOrder.AscendingOrder

        self._sort_table_by_column(logical_index, self._sort_order)

        # Update header visual indicator
        self._update_header_sort_indicator()

    def _sort_table_by_column(self, column: int, order: Qt.SortOrder):
        """Sort table by specified column with proper data type handling."""
        if not self._watchlist_symbols:
            return

        # Create a list of (symbol, sort_value) tuples
        sort_data = []

        for symbol in self._watchlist_symbols:
            if symbol not in self._watchlist_data:
                continue

            data = self._watchlist_data[symbol]

            if column == 0:  # Symbol
                sort_value = symbol
            elif column == 1:  # LTP
                sort_value = data.get('ltp', 0.0)
            elif column == 2:  # Volume
                sort_value = data.get('volume', 0)
            elif column == 3:  # Change %
                sort_value = data.get('change_pct', 0.0)
            else:
                sort_value = symbol

            sort_data.append((symbol, sort_value))

        # Sort the data
        reverse = (order == Qt.SortOrder.DescendingOrder)

        # Custom sorting for volume to handle different scales
        if column == 2:  # Volume column
            sort_data.sort(key=lambda x: x[1] if isinstance(x[1], (int, float)) else 0, reverse=reverse)
        else:
            sort_data.sort(key=lambda x: x[1] if x[1] is not None else (float('-inf') if reverse else float('inf')),
                           reverse=reverse)

        # Get sorted symbol list
        sorted_symbols = [item[0] for item in sort_data]

        # Temporarily disable sorting to prevent recursion
        self.setSortingEnabled(False)

        # Repopulate table with sorted data
        self.setRowCount(len(sorted_symbols))
        self._symbol_to_row.clear()

        for row, symbol in enumerate(sorted_symbols):
            self._symbol_to_row[symbol] = row
            self._populate_row(row, symbol)

        # Re-enable sorting
        self.setSortingEnabled(True)

        self._adjust_symbol_column_width()

        logger.debug(f"Sorted {self.category} watchlist by column {column} ({'DESC' if reverse else 'ASC'})")

    def _update_header_sort_indicator(self):
        """Update header to show sort indicator."""
        header = self.horizontalHeader()

        # Clear all sort indicators first
        for i in range(self.columnCount() - 1):  # Exclude remove button column
            header.setSortIndicator(i, Qt.SortOrder.AscendingOrder)
            header.setSortIndicatorShown(False)

        # Set sort indicator for current column
        if self._sort_column >= 0:
            header.setSortIndicator(self._sort_column, self._sort_order)
            header.setSortIndicatorShown(True)

    def _setup_data_refresh(self):
        """Setup periodic data refresh for better responsiveness"""
        self._ui_flush_timer = QTimer(self)
        self._ui_flush_timer.timeout.connect(self._flush_pending_ui_updates)
        self._ui_flush_timer.start(225)
        self._data_update_timer.timeout.connect(self._refresh_display)
        self._data_update_timer.start(1000)  # Refresh every second

    def set_instrument_map(self, instrument_map: Dict[str, Dict]):
        """Enhanced instrument map setting with proper data initialization"""
        logger.info(f"Setting instrument map for {self.category} with {len(instrument_map)} instruments")
        self._instrument_map = instrument_map

        # Re-initialize watchlist data for existing symbols
        self._initialize_watchlist_data()
        self._populate_full_table()

    def _initialize_watchlist_data(self):
        """Initialize watchlist data from an instrument map for existing symbols"""
        for symbol in list(self._watchlist_symbols):
            if symbol in self._instrument_map:
                instrument = self._instrument_map[symbol]

                # Get OHLC data properly
                ohlc_data = instrument.get('ohlc', {})
                if isinstance(ohlc_data, dict):
                    prev_close = ohlc_data.get('close', 0.0)
                    day_high = ohlc_data.get('high', 0.0)
                    day_low = ohlc_data.get('low', 0.0)
                    day_open = ohlc_data.get('open', 0.0)
                else:
                    prev_close = 0.0
                    day_high = 0.0
                    day_low = 0.0
                    day_open = 0.0

                # Create comprehensive data structure
                self._watchlist_data[symbol] = {
                    "tradingsymbol": symbol,
                    "instrument_token": instrument.get('instrument_token'),
                    "exchange": instrument.get('exchange', 'NSE'),
                    "segment": instrument.get('segment', 'NSE'),
                    "last_price": instrument.get('last_price', 0.0),
                    "volume": instrument.get('volume', 0),  # This should be a day volume
                    "volume_traded": instrument.get('volume_traded', 0),  # Alternative field
                    "ohlc": ohlc_data,
                    "ltp": instrument.get('last_price', 0.0),  # Current LTP
                    "change_pct": 0.0,
                    "change": 0.0,  # Absolute change
                    "day_high": day_high,
                    "day_low": day_low,
                    "day_open": day_open,
                    "prev_close": prev_close,
                }

                # Calculate initial change percentage
                self._calculate_change_percentage(symbol)

                logger.debug(f"Initialized data for {symbol}: LTP={self._watchlist_data[symbol]['ltp']}, "
                             f"Volume={self._watchlist_data[symbol]['volume']}, "
                             f"PrevClose={prev_close}")
            else:
                logger.warning(f"Symbol {symbol} not found in instrument map")

    def _calculate_change_percentage(self, symbol: str):
        """Calculate change percentage for a symbol"""
        if symbol not in self._watchlist_data:
            return

        data = self._watchlist_data[symbol]
        ltp = data.get('ltp', 0.0)
        prev_close = data.get('prev_close', 0.0)

        if prev_close > 0 and ltp > 0:
            change = ltp - prev_close
            change_pct = (change / prev_close) * 100
            data['change_pct'] = change_pct
            data['change'] = change
            logger.debug(f"Calculated change for {symbol}: {change_pct:.2f}% (LTP: {ltp}, PrevClose: {prev_close})")
        else:
            data['change_pct'] = 0.0
            data['change'] = 0.0
            if prev_close <= 0:
                logger.warning(f"Invalid prev_close for {symbol}: {prev_close}")

    def update_data(self, ticks: List[Dict]):
        """FIXED data update from WebSocket ticks with proper field handling"""
        updated_symbols = set()

        for tick in ticks:
            token = tick.get('instrument_token')
            if not token:
                continue

            # Find symbol by token
            symbol_found = None
            for symbol, data in self._watchlist_data.items():
                if data.get('instrument_token') == token:
                    symbol_found = symbol
                    break

            if not symbol_found:
                continue

            # Update data from tick
            data = self._watchlist_data[symbol_found]

            # Debug: Log the full tick structure occasionally
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Processing tick for {symbol_found}: {tick}")

            # Update LTP
            if 'last_price' in tick and tick['last_price'] is not None:
                new_ltp = float(tick['last_price'])
                data['ltp'] = new_ltp
                data['last_price'] = new_ltp

            # FIXED: Update volume - try multiple possible field names
            volume_updated = False
            for vol_field in ['volume', 'volume_traded', 'day_volume']:
                if vol_field in tick and tick[vol_field] is not None:
                    try:
                        new_volume = int(tick[vol_field])
                        if new_volume > 0:  # Only update if we get a positive volume
                            data['volume'] = new_volume
                            volume_updated = True
                            logger.debug(f"Updated volume for {symbol_found} from field '{vol_field}': {new_volume}")
                            break
                    except (ValueError, TypeError):
                        logger.warning(f"Invalid volume value in field '{vol_field}': {tick[vol_field]}")

            if not volume_updated and 'volume' in tick:
                logger.debug(f"Volume not updated for {symbol_found}, tick volume field: {tick.get('volume')}")

            # FIXED: Update OHLC if available - handle different data structures
            if 'ohlc' in tick and tick['ohlc'] is not None:
                tick_ohlc = tick['ohlc']
                if isinstance(tick_ohlc, dict):
                    # Update high
                    if 'high' in tick_ohlc and tick_ohlc['high'] is not None:
                        new_high = float(tick_ohlc['high'])
                        data['day_high'] = max(data.get('day_high', 0.0), new_high)

                    # Update low
                    if 'low' in tick_ohlc and tick_ohlc['low'] is not None:
                        new_low = float(tick_ohlc['low'])
                        if new_low > 0:  # Only update if we have a valid low
                            if data.get('day_low', 0.0) == 0.0:
                                data['day_low'] = new_low
                            else:
                                data['day_low'] = min(data['day_low'], new_low)

                    # Update open (usually doesn't change during the day)
                    if 'open' in tick_ohlc and tick_ohlc['open'] is not None:
                        data['day_open'] = float(tick_ohlc['open'])

                    # CRITICAL: Update previous close for change calculation
                    if 'close' in tick_ohlc and tick_ohlc['close'] is not None:
                        data['prev_close'] = float(tick_ohlc['close'])

            # Alternative: If change fields are directly in tick data
            if 'change' in tick and tick['change'] is not None:
                data['change'] = float(tick['change'])

            if 'net_change' in tick and tick['net_change'] is not None:
                data['change'] = float(tick['net_change'])

            # Alternative: If change percentage is directly provided
            if 'change_percent' in tick and tick['change_percent'] is not None:
                data['change_pct'] = float(tick['change_percent'])
            elif 'net_change_percent' in tick and tick['net_change_percent'] is not None:
                data['change_pct'] = float(tick['net_change_percent'])
            else:
                # Recalculate change percentage
                self._calculate_change_percentage(symbol_found)

            updated_symbols.add(symbol_found)

            logger.debug(
                f"Updated {symbol_found}: LTP={data['ltp']:.2f}, Vol={data['volume']}, "
                f"Chg={data['change_pct']:.2f}%, PrevClose={data['prev_close']:.2f}")

        # Queue for throttled repaint.
        self._dirty_symbols.update(updated_symbols)

    def _flush_pending_ui_updates(self):
        """Flush queued watchlist row updates at ~4-5 FPS."""
        if not self._dirty_symbols:
            return

        dirty_symbols = tuple(self._dirty_symbols)
        self._dirty_symbols.clear()
        for symbol in dirty_symbols:
            if symbol in self._symbol_to_row and symbol in self._watchlist_data:
                row = self._symbol_to_row[symbol]
                self._update_row_data(row, self._watchlist_data[symbol])

    def add_symbol(self, symbol: str) -> bool:
        """Enhanced symbol addition with proper data initialization"""
        if not symbol or symbol in self._watchlist_symbols:
            logger.warning(f"Symbol '{symbol}' already exists in {self.category} or is invalid")
            return False

        if symbol not in self._instrument_map:
            logger.warning(f"Symbol '{symbol}' not found in instrument map")
            return False

        # Add to a symbol set
        self._watchlist_symbols.add(symbol)

        # Initialize data
        instrument = self._instrument_map[symbol]

        # Get OHLC data properly
        ohlc_data = instrument.get('ohlc', {})
        if isinstance(ohlc_data, dict):
            prev_close = ohlc_data.get('close', 0.0)
            day_high = ohlc_data.get('high', 0.0)
            day_low = ohlc_data.get('low', 0.0)
            day_open = ohlc_data.get('open', 0.0)
        else:
            prev_close = 0.0
            day_high = 0.0
            day_low = 0.0
            day_open = 0.0

        self._watchlist_data[symbol] = {
            "tradingsymbol": symbol,
            "instrument_token": instrument.get('instrument_token'),
            "exchange": instrument.get('exchange', 'NSE'),
            "segment": instrument.get('segment', 'NSE'),
            "last_price": instrument.get('last_price', 0.0),
            "volume": instrument.get('volume', 0),
            "volume_traded": instrument.get('volume_traded', 0),
            "ohlc": ohlc_data,
            "ltp": instrument.get('last_price', 0.0),
            "change_pct": 0.0,
            "change": 0.0,
            "day_high": day_high,
            "day_low": day_low,
            "day_open": day_open,
            "prev_close": prev_close,
        }

        # Calculate initial change percentage
        self._calculate_change_percentage(symbol)

        # Repopulate table
        self._populate_full_table()

        logger.info(f"Added {symbol} to {self.category} watchlist with LTP: {self._watchlist_data[symbol]['ltp']}, "
                    f"Volume: {self._watchlist_data[symbol]['volume']}")
        return True

    def remove_symbol(self, symbol: str) -> bool:
        """Enhanced symbol removal"""
        if symbol in self._watchlist_symbols:
            self._watchlist_symbols.remove(symbol)
            if symbol in self._watchlist_data:
                del self._watchlist_data[symbol]
            self._populate_full_table()
            logger.info(f"Removed {symbol} from {self.category} watchlist")
            return True
        return False

    def _populate_full_table(self):
        """Enhanced table population with proper data handling"""
        self.setRowCount(0)
        self._symbol_to_row.clear()

        if not self._watchlist_symbols:
            return

        sorted_symbols = sorted(self._watchlist_symbols)
        self.setRowCount(len(sorted_symbols))

        for row, symbol in enumerate(sorted_symbols):
            self._symbol_to_row[symbol] = row
            self._populate_row(row, symbol)

        self._adjust_symbol_column_width()

    def _populate_row(self, row: int, symbol: str):
        """Enhanced row population with proper data"""
        # Create items for all columns
        for i in range(4):
            self.setItem(row, i, QTableWidgetItem())

        # Add remove button
        self.setCellWidget(row, 4, self._create_remove_button(row))

        # Update with current data
        if symbol in self._watchlist_data:
            self._update_row_data(row, self._watchlist_data[symbol])
        else:
            # Fallback display
            self.item(row, 0).setText(symbol)
            self.item(row, 1).setText("0.00")
            self.item(row, 2).setText("0")
            self.item(row, 3).setText("0.00%")

    def _update_row_data(self, row: int, data: Dict):
        """FIXED row data update with proper formatting and sorting preservation"""
        if row >= self.rowCount():
            return

        # Ensure items exist
        for col_idx in range(4):
            if not self.item(row, col_idx):
                self.setItem(row, col_idx, QTableWidgetItem())

        # Get data with defaults
        tradingsymbol = data.get('tradingsymbol', 'N/A')
        ltp = data.get('ltp', 0.0)
        volume = data.get('volume', 0)
        change_pct = data.get('change_pct', 0.0)

        # Set text values
        self.item(row, 0).setText(tradingsymbol)
        self.item(row, 1).setText(f"{ltp:.2f}" if ltp > 0 else "0.00")

        # Format volume with K/M notation
        if volume >= 1_000_000:
            volume_text = f"{volume / 1_000_000:.1f}M"
        elif volume >= 1_000:
            volume_text = f"{volume / 1_000:.1f}K"
        else:
            volume_text = str(volume)
        self.item(row, 2).setText(volume_text)

        # Format change percentage
        if change_pct != 0:
            self.item(row, 3).setText(f"{change_pct:+.1f}%")
        else:
            self.item(row, 3).setText("0.0%")

        # Set data for proper sorting
        self.item(row, 0).setData(Qt.ItemDataRole.UserRole, tradingsymbol)
        self.item(row, 1).setData(Qt.ItemDataRole.UserRole, ltp)
        self.item(row, 2).setData(Qt.ItemDataRole.UserRole, volume)
        self.item(row, 3).setData(Qt.ItemDataRole.UserRole, change_pct)

        # Apply colors
        profit_color = QColor(60, 179, 113)
        loss_color = QColor(220, 20, 60)
        neutral_color = QColor(169, 169, 169)
        color = profit_color if change_pct > 0 else (loss_color if change_pct < 0 else neutral_color)

        self.item(row, 1).setForeground(color)
        self.item(row, 3).setForeground(color)
        self.item(row, 2).setForeground(neutral_color)

        # Set alignments
        self.item(row, 0).setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.item(row, 1).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.item(row, 2).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.item(row, 3).setTextAlignment(Qt.AlignmentFlag.AlignCenter)

    def _refresh_display(self):
        """Periodic refresh of display data"""
        for symbol, row in self._symbol_to_row.items():
            if symbol in self._watchlist_data:
                self._update_row_data(row, self._watchlist_data[symbol])

    def _create_remove_button(self, row) -> QPushButton:
        """Creates a minimal 'x' button to remove a symbol."""
        remove_btn = QPushButton("×")
        remove_btn.setObjectName("removeButton")
        # Use Qt method for cursor instead of CSS
        remove_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        remove_btn.setFixedSize(16, 16)
        remove_btn.clicked.connect(partial(self._remove_symbol_at_row, row))
        return remove_btn

    def _remove_symbol_at_row(self, row: int):
        """Enhanced symbol removal by row"""
        if 0 <= row < self.rowCount():
            symbols_list = sorted(self._watchlist_symbols)
            if row < len(symbols_list):
                symbol_to_remove = symbols_list[row]
                self.remove_symbol(symbol_to_remove)

    def _on_cell_clicked(self, row, column):
        """Handles clicks on a cell to select the symbol for charting."""
        if column != 4 and row < self.rowCount():
            try:
                symbol = self.item(row, 0).text()
                if symbol and symbol != 'N/A':
                    self.symbol_selected.emit(symbol)
            except (AttributeError, TypeError):
                logger.warning(f"Could not get symbol from clicked row {row}.")

    def _show_enhanced_context_menu(self, pos: QPoint):
        """Enhanced context menu with advanced order options."""
        row = self.rowAt(pos.y())
        if row < 0:
            return

        try:
            symbol = self.item(row, 0).text()
            if not symbol or symbol == 'N/A':
                return
        except (AttributeError, TypeError):
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

        # Quick orders section
        quick_label = QAction("Quick Orders", self)
        quick_label.setEnabled(False)
        menu.addAction(quick_label)

        quick_buy = QAction("Quick Buy", self)
        quick_buy.triggered.connect(lambda: self._request_trade(symbol, "BUY"))
        menu.addAction(quick_buy)

        quick_sell = QAction("Quick Sell", self)
        quick_sell.triggered.connect(lambda: self._request_trade(symbol, "SELL"))
        menu.addAction(quick_sell)

        menu.addSeparator()

        # Advanced orders section
        advanced_label = QAction("Advanced Orders", self)
        advanced_label.setEnabled(False)
        menu.addAction(advanced_label)

        advanced_buy = QAction("Advanced Buy Order", self)
        advanced_buy.triggered.connect(lambda: self.advanced_buy_order_requested.emit(symbol))
        menu.addAction(advanced_buy)

        advanced_sell = QAction("Advanced Sell Order", self)
        advanced_sell.triggered.connect(lambda: self.advanced_sell_order_requested.emit(symbol))
        menu.addAction(advanced_sell)

        bracket_order = QAction("Bracket Order", self)
        bracket_order.triggered.connect(lambda: self.bracket_order_requested.emit(symbol))
        menu.addAction(bracket_order)

        menu.addSeparator()

        # Analysis section
        analysis_label = QAction("Analysis", self)
        analysis_label.setEnabled(False)
        menu.addAction(analysis_label)

        view_chart = QAction("View Chart", self)
        view_chart.triggered.connect(lambda: self.symbol_selected.emit(symbol))
        menu.addAction(view_chart)

        menu.addSeparator()

        # Sorting section
        sorting_label = QAction("Sorting Options", self)
        sorting_label.setEnabled(False)
        menu.addAction(sorting_label)

        sort_symbol = QAction("Sort by Symbol", self)
        sort_symbol.triggered.connect(lambda: self._sort_table_by_column(0, Qt.SortOrder.AscendingOrder))
        menu.addAction(sort_symbol)

        sort_ltp = QAction("Sort by LTP", self)
        sort_ltp.triggered.connect(lambda: self._sort_table_by_column(1, Qt.SortOrder.DescendingOrder))
        menu.addAction(sort_ltp)

        sort_volume = QAction("Sort by Volume", self)
        sort_volume.triggered.connect(lambda: self._sort_table_by_column(2,Qt.SortOrder.DescendingOrder))
        menu.addAction(sort_volume)

        sort_change_desc = QAction("Sort by Change % ↓ (Best First)", self)
        sort_change_desc.triggered.connect(lambda: self._sort_table_by_column(3, Qt.SortOrder.DescendingOrder))
        menu.addAction(sort_change_desc)

        sort_change_asc = QAction("Sort by Change % ↑ (Worst First)", self)
        sort_change_asc.triggered.connect(lambda: self._sort_table_by_column(3, Qt.SortOrder.AscendingOrder))
        menu.addAction(sort_change_asc)

        # Debug action to show current data
        menu.addSeparator()
        debug_action = QAction("Debug: Show Data", self)
        debug_action.triggered.connect(lambda: self._show_debug_data(symbol))
        menu.addAction(debug_action)

        # Refresh data action
        refresh_action = QAction("Refresh Data", self)
        refresh_action.triggered.connect(lambda: self._refresh_symbol_data(symbol))
        menu.addAction(refresh_action)

        menu.exec(self.viewport().mapToGlobal(pos))

    def _show_debug_data(self, symbol: str):
        """Debug function to show current data for a symbol"""
        if symbol in self._watchlist_data:
            data = self._watchlist_data[symbol]
            logger.info(f"Debug data for {symbol}: {data}")
            print(f"Debug data for {symbol}:")
            for key, value in data.items():
                print(f"  {key}: {value}")

    def _refresh_symbol_data(self, symbol: str):
        """Refresh data for a specific symbol"""
        if symbol in self._instrument_map and symbol in self._watchlist_data:
            instrument = self._instrument_map[symbol]
            self._watchlist_data[symbol].update({
                'ltp': instrument.get('last_price', 0.0),
                'volume': instrument.get('volume', 0),
                'day_high': instrument.get('ohlc', {}).get('high', 0.0),
                'day_low': instrument.get('ohlc', {}).get('low', 0.0),
            })
            self._calculate_change_percentage(symbol)

            if symbol in self._symbol_to_row:
                row = self._symbol_to_row[symbol]
                self._update_row_data(row, self._watchlist_data[symbol])

    def _request_trade(self, symbol: str, transaction_type: str):
        """Emits a signal to open the basic order dialog."""
        order_details = {
            "tradingsymbol": symbol,
            "transaction_type": transaction_type,
        }
        self.place_order_requested.emit(order_details)

    def get_all_tokens(self) -> List[int]:
        """Returns a list of all instrument tokens currently in this watchlist."""
        tokens = []
        for data in self._watchlist_data.values():
            token = data.get('instrument_token')
            if token:
                tokens.append(token)
        return tokens

    def get_watchlist_data(self) -> Dict[str, Dict]:
        """Returns the current watchlist data for saving."""
        return self._watchlist_data.copy()

    def get_symbol_list(self) -> List[str]:
        """Returns list of symbols for persistence"""
        return list(self._watchlist_symbols)

    def load_watchlist_data(self, symbols: List[str]):
        """Load watchlist from a list of symbols"""
        self._watchlist_symbols = set(symbols) if symbols else set()
        self._watchlist_data.clear()

        # Initialize data if instrument map is available
        if self._instrument_map:
            self._initialize_watchlist_data()
            self._populate_full_table()

        logger.info(f"Loaded {len(self._watchlist_symbols)} symbols for {self.category}")


class TabbedWatchlistWidget(QWidget):
    """
    Enhanced tabbed watchlist widget with dynamic width calculation.
    """
    symbol_selected = Signal(str)
    subscribe_tokens_requested = Signal(list)
    place_order_requested = Signal(dict)
    advanced_buy_order_requested = Signal(str)
    advanced_sell_order_requested = Signal(str)
    bracket_order_requested = Signal(str)
    watchlist_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._instrument_map: Dict[str, Dict] = {}
        self._tables: Dict[str, TradingTable] = {}
        self._setup_ui()
        self._apply_styles()
        self._load_all_watchlists()

    def _setup_ui(self):
        """Sets up the main UI layout with tabs."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.setMinimumWidth(350)

        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("tradingTabs")

        # Initialize tab width tracking variables
        self._last_widget_width = 0
        self._last_calculated_tab_width = 0
        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._update_tab_widths)

        tab_bar = self.tab_widget.tabBar()
        tab_bar.setUsesScrollButtons(False)
        tab_bar.setExpanding(False)  # Set to False for manual control
        tab_bar.setDrawBase(False)  # Prevents visual glitches

        categories = ["Breakouts", "EP", "Parabolic"]
        for category in categories:
            table = TradingTable(category)
            self._tables[category] = table

            table.symbol_selected.connect(self.symbol_selected.emit)
            table.place_order_requested.connect(self.place_order_requested.emit)
            table.advanced_buy_order_requested.connect(self.advanced_buy_order_requested.emit)
            table.advanced_sell_order_requested.connect(self.advanced_sell_order_requested.emit)
            table.bracket_order_requested.connect(self.bracket_order_requested.emit)

            self.tab_widget.addTab(table, category.upper())

        layout.addWidget(self.tab_widget)

    def _update_tab_widths(self):
        """Enhanced tab width calculation with glitch prevention."""
        if not hasattr(self, 'tab_widget') or not self.isVisible():
            return

        tab_bar = self.tab_widget.tabBar()
        tab_count = tab_bar.count()

        if tab_count == 0:
            return

        # Get current widget width
        current_width = self.width()

        # Prevent updates if width hasn't changed significantly (prevents glitches)
        if abs(current_width - self._last_widget_width) < 10:
            return

        self._last_widget_width = current_width

        # Calculate available width for tabs
        # Account for tab bar margins, borders, and container padding
        tab_bar_margins = 4  # Total left and right margins
        tab_borders = (tab_count - 1) * 1  # 1px border between tabs
        container_padding = 2  # Container padding
        scrollbar_width = 15  # Reserve space for potential scrollbar

        # Calculate usable width
        usable_width = current_width - tab_bar_margins - tab_borders - container_padding - scrollbar_width

        # Ensure minimum tab width
        min_tab_width = 50
        max_usable_width = max(usable_width, tab_count * min_tab_width)

        # Calculate equal tab width
        calculated_tab_width = max_usable_width // tab_count

        # Only update if the change is significant (prevents constant updates)
        if abs(calculated_tab_width - self._last_calculated_tab_width) > 8:
            self._last_calculated_tab_width = calculated_tab_width
            self._apply_dynamic_tab_styles(calculated_tab_width)

            logger.debug(f"Updated tab width: {calculated_tab_width}px for widget width: {current_width}px")

    def _apply_dynamic_tab_styles(self, tab_width: int):
        """Apply styles with dynamic tab width, preventing visual glitches."""
        # Use exact pixel values to prevent rounding issues
        tab_width_px = f"{tab_width}px"

        # Create stylesheet with fixed tab widths
        dynamic_stylesheet = f"""
            /* Main Widget */
            TabbedWatchlistWidget {{
                background-color: #0a0a0a;
                color: #e0e0e0;
                font-family: "Segoe UI", Arial, sans-serif;
                font-size: 13px;
            }}

            /* Tab Widget Styling */
            QTabWidget#tradingTabs {{
                background-color: #0a0a0a;
                border: none;
            }}

            QTabWidget#tradingTabs::pane {{
                border: 1px solid #202020;
                background-color: #0a0a0a;
                border-radius: 0px;
                border-top: none;
            }}

            QTabWidget#tradingTabs::tab-bar {{
                alignment: left;
            }}

            /* Completely hide scroll buttons */
            QTabBar::scroller {{
                width: 0px;
                height: 0px;
            }}

            QTabBar QToolButton {{
                width: 0px;
                height: 0px;
                border: none;
                background: transparent;
            }}

            /* Dynamic Tab Styling - Equal Width Distribution */
            QTabBar::tab {{
                background-color: #1a1a1a;
                color: #8892b0;
                padding: 6px 2px;
                margin: 0px;
                border: 1px solid #202020;
                border-bottom: none;
                border-right: 1px solid #202020;
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 0.5px;
                text-align: center;
                width: {tab_width_px};
                min-width: {tab_width_px};
                max-width: {tab_width_px};
            }}

            QTabBar::tab:last {{
                border-right: 1px solid #202020;
            }}

            QTabBar::tab:selected {{
                background-color: #0a0a0a;
                color: #6a9cff;
                border-bottom: 2px solid #6a9cff;
                width: {tab_width_px};
                min-width: {tab_width_px};
                max-width: {tab_width_px};
            }}

            QTabBar::tab:hover:!selected {{
                background-color: #2a2a2a;
                color: #ccd6f6;
                width: {tab_width_px};
                min-width: {tab_width_px};
                max-width: {tab_width_px};
            }}

            /* Table Styling - EXACT match to scanner table */
            TradingTable {{
                background-color: #0a0a0a;
                border: none;
                gridline-color: #2a2a2a;
                selection-background-color: #1e3a5f;
                alternate-background-color: #0f0f0f;
                outline: none;
                show-decoration-selected: 0;
                font-size: 12px;
                border-radius: 0px;
            }}

            TradingTable::item {{
                padding: 5px 8px;
                border-bottom: 1px solid #1a1a1a;
                background-color: transparent;
                font-size: 12px;
            }}

            TradingTable::item:selected {{
                background-color: #1e3a5f !important;
                outline: none;
                border: none;
                color: #ffffff;
                font-weight: 600;
            }}

            TradingTable::item:focus {{
                background-color: #1e3a5f !important;
                outline: none;
                border: none;
            }}

            TradingTable::item:hover {{
                background-color: transparent;
            }}

            TradingTable::item:alternate {{
                background-color: #0f0f0f;
            }}

            TradingTable::item:alternate:selected {{
                background-color: #1e3a5f !important;
                color: #ffffff;
                font-weight: 600;
            }}

            /* Header Styling - EXACT match to scanner table */
            QHeaderView::section {{
                background-color: #1a1a1a;
                color: #a0c0ff;
                padding: 3px 10px;
                border: none;
                border-bottom: 1px solid #303030;
                border-right: 1px solid #101010;
                font-weight: 600;
                font-size: 11px;
            }}

            QHeaderView::section:last {{
                border-right: none;
            }}

            QHeaderView::section:hover {{
                background-color: #2a2a2a;
                color: #ccd6f6;
            }}

            QHeaderView::down-arrow {{
                color: #6a9cff;
                width: 8px;
                height: 8px;
                subcontrol-position: center right;
                subcontrol-origin: margin;
                margin-right: 2px;
            }}

            QHeaderView::up-arrow {{
                color: #6a9cff;
                width: 8px;
                height: 8px;
                subcontrol-position: center right;
                subcontrol-origin: margin;
                margin-right: 2px;
            }}

            /* Remove Button Styling */
            QPushButton#removeButton {{
                background-color: transparent;
                color: #cc4444;
                border: none;
                font-weight: bold;
                font-size: 12px;
                border-radius: 8px;
                padding: 0px;
                margin: 0px;
            }}

            QPushButton#removeButton:hover {{
                color: #ff6666;
                background-color: #2a1f1f;
            }}

            /* Enhanced Scrollbars */
            QScrollBar:vertical {{
                background-color: #0a0a0a;
                width: 8px;
                border: none;
                margin: 0px;
            }}

            QScrollBar::handle:vertical {{
                background-color: #424242;
                border-radius: 4px;
                min-height: 20px;
                margin: 2px;
            }}

            QScrollBar::handle:vertical:hover {{
                background-color: #616161;
            }}

            QScrollBar:horizontal {{
                background-color: #0a0a0a;
                height: 8px;
                border: none;
                margin: 0px;
            }}

            QScrollBar::handle:horizontal {{
                background-color: #424242;
                border-radius: 4px;
                min-width: 20px;
                margin: 2px;
            }}

            QScrollBar::handle:horizontal:hover {{
                background-color: #616161;
            }}

            QScrollBar::add-line, QScrollBar::sub-line {{
                border: none;
                background: none;
                width: 0px;
                height: 0px;
                margin: 0px;
            }}
        """

        # Apply the stylesheet in a thread-safe manner
        self.setStyleSheet(dynamic_stylesheet)

    def resizeEvent(self, event: QResizeEvent):
        """Enhanced resize event handling with debouncing to prevent glitches."""
        super().resizeEvent(event)

        # Stop any pending resize updates
        if hasattr(self, '_resize_timer'):
            self._resize_timer.stop()

        # Start timer with longer delay to debounce rapid resize events
        self._resize_timer.start(100)  # 100 ms delay for smoother resizing

    def showEvent(self, event):
        """Initialize tab widths when widget is first shown."""
        super().showEvent(event)

        # Use QTimer.singleShot to ensure the widget is fully rendered
        QTimer.singleShot(50, self._initial_tab_width_setup)

    def _initial_tab_width_setup(self):
        """Initial setup of tab widths after widget is fully rendered."""
        if self.isVisible() and self.width() > 0:
            self._update_tab_widths()

    def _apply_styles(self):
        """Initial style application - basic styles without tab widths."""
        # Tab widths will be set dynamically by _apply_dynamic_tab_styles()
        # This ensures the widget has basic styling before dynamic updates
        basic_stylesheet = """
            TabbedWatchlistWidget {
                background-color: #0a0a0a;
                color: #e0e0e0;
                font-family: "Segoe UI", Arial, sans-serif;
                font-size: 13px;
            }
        """
        self.setStyleSheet(basic_stylesheet)

    # Additional helper method for debugging tab width issues
    def _debug_tab_dimensions(self):
        """Debug method to log current tab dimensions."""
        if not hasattr(self, 'tab_widget'):
            return

        tab_bar = self.tab_widget.tabBar()
        widget_width = self.width()
        tab_count = tab_bar.count()

        logger.debug(f"Widget width: {widget_width}, Tab count: {tab_count}")

        for i in range(tab_count):
            tab_rect = tab_bar.tabRect(i)
            logger.debug(f"Tab {i} rect: {tab_rect.width()}x{tab_rect.height()}")

    # Method to force tab width recalculation (useful for external calls)
    def force_update_tab_widths(self):
        """Force an immediate update of tab widths."""
        self._update_tab_widths()

    def set_instrument_map(self, instrument_map: Dict[str, Dict]):
        """Enhanced instrument map setting with proper propagation"""
        logger.info(f"Setting instrument map with {len(instrument_map)} instruments")
        self._instrument_map = instrument_map

        # Propagate to all tables
        for table in self._tables.values():
            table.set_instrument_map(instrument_map)

        # Request token subscription for all existing symbols
        self._subscribe_all_tokens()

    def _subscribe_all_tokens(self):
        """Subscribe to all tokens across all watchlists"""
        all_tokens = self.get_all_tokens()
        if all_tokens:
            self.subscribe_tokens_requested.emit(all_tokens)
            logger.info(f"Subscribed to {len(all_tokens)} tokens across all watchlists")

    @Slot(list)
    def update_data(self, ticks: List[Dict]):
        """Enhanced data update with logging"""
        if ticks:
            # Log first tick for debugging
            if logger.isEnabledFor(logging.DEBUG) and len(ticks) > 0:
                logger.debug(f"Received {len(ticks)} ticks. First tick structure: {ticks[0]}")

            # Distribute to all tables
            for table in self._tables.values():
                table.update_data(ticks)

    def add_symbol(self, symbol: str, category: str = None) -> bool:
        """Enhanced symbol addition with proper persistence"""
        if category is None:
            current_index = self.tab_widget.currentIndex()
            category = list(self._tables.keys())[current_index]

        if category in self._tables:
            success = self._tables[category].add_symbol(symbol)
            if success:
                # Save immediately
                self._save_watchlist(category)

                # Subscribe to token
                if symbol in self._instrument_map:
                    token = self._instrument_map[symbol].get('instrument_token')
                    if token:
                        self.subscribe_tokens_requested.emit([token])

                self.watchlist_changed.emit()
                logger.info(f"Successfully added {symbol} to {category}")
                return True
            else:
                logger.warning(f"Failed to add {symbol} to {category}")
        return False

    def get_current_category(self) -> str:
        """Returns the currently selected category."""
        current_index = self.tab_widget.currentIndex()
        return list(self._tables.keys())[current_index]

    def get_all_tokens(self) -> List[int]:
        """Returns a list of all instrument tokens from all watchlists."""
        all_tokens = []
        for table in self._tables.values():
            all_tokens.extend(table.get_all_tokens())
        return list(set(all_tokens))  # Remove duplicates

    def _load_all_watchlists(self):
        """Enhanced watchlist loading with better error handling"""
        for category, filepath in WATCHLIST_FILES.items():
            if os.path.exists(filepath):
                try:
                    with open(filepath, 'r') as f:
                        symbols = json.load(f)

                    if isinstance(symbols, list):
                        self._tables[category].load_watchlist_data(symbols)
                        logger.info(f"Loaded {len(symbols)} symbols for {category} watchlist")
                    else:
                        logger.warning(f"Invalid format in {filepath}, expected list of symbols")

                except (json.JSONDecodeError, IOError) as e:
                    logger.error(f"Failed to load {category} watchlist: {e}")
            else:
                logger.info(f"No existing watchlist file for {category}")

    def _save_watchlist(self, category: str):
        """Enhanced watchlist saving"""
        if category not in WATCHLIST_FILES or category not in self._tables:
            return

        filepath = WATCHLIST_FILES[category]
        try:
            # Ensure directory exists
            dir_name = os.path.dirname(filepath)
            if dir_name and not os.path.exists(dir_name):
                os.makedirs(dir_name)

            # Get symbol list
            symbols = self._tables[category].get_symbol_list()

            # Save to file
            with open(filepath, 'w') as f:
                json.dump(symbols, f, indent=4)

            logger.info(f"Saved {category} watchlist with {len(symbols)} symbols to {filepath}")

        except IOError as e:
            logger.error(f"Failed to save {category} watchlist: {e}")

    def closeEvent(self, event):
        """Enhanced close event with proper cleanup - REMOVED geometry saving"""
        # Save all watchlists
        for category in self._tables.keys():
            self._save_watchlist(category)

        # Stop timers
        for table in self._tables.values():
            if hasattr(table, '_data_update_timer'):
                table._data_update_timer.stop()

        super().closeEvent(event)
