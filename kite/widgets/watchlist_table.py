# Enhanced watchlist_table.py - FIXED width consistency with position table
import logging
import json
import os
from typing import List, Dict, Optional
from functools import partial

from PySide6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QPushButton, QVBoxLayout, QWidget,
    QHeaderView, QAbstractItemView, QMenu, QComboBox, QStackedWidget
)
from PySide6.QtCore import Qt, Signal, Slot, QPoint, QTimer
from PySide6.QtGui import QColor, QCursor, QAction, QFont, QBrush

logger = logging.getLogger(__name__)

# Separate files for each watchlist category
WATCHLIST_FILES = {
    "Breakouts": "kite/user_data/watchlist_breakouts.json",
    "EP": "kite/user_data/watchlist_episodic.json",
    "Parabolic": "kite/user_data/watchlist_parabolic.json"
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
    watchlist_symbols_changed = Signal()

    def __init__(self, category: str, parent=None):
        super().__init__(parent)
        self.category = category
        self._instrument_map: Dict[str, Dict] = {}
        self._watchlist_data: Dict[str, Dict] = {}
        self._symbol_to_row: Dict[str, int] = {}
        self._token_to_symbol: Dict[int, str] = {}   # O(1) reverse map
        self._data_update_timer = QTimer()

        # Initialize empty watchlist data
        self._watchlist_symbols = set()  # Track symbols separately
        self._last_widget_width = 0  # Reset to force update

        self._color_theme = {
            "enable_table_directional_colors": False,
            "enable_volume_strength_indicator": False,
            "tables": {"positive": "#26a69a", "negative": "#ef5350", "neutral": "#a9a9a9", "volume": "#45d4ff"}
        }

        self._configure_table()
        self._connect_signals()
        self._setup_data_refresh()

    def _configure_table(self):
        """TC2000 style compact table configuration."""
        self.setColumnCount(5)
        self.setHorizontalHeaderLabels(["Symbol", "LTP", "Vol", "Chg %", ""])

        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setVisible(True)

        # THE FIX: Native Qt sizing for ultimate density
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)

        # ADD THIS: Ensure the table doesn't think it's wider than the widget
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header.setStretchLastSection(False)
        self.setColumnWidth(4, 25)  # Give the X button slightly more breathing room

        # Prevent columns from disappearing entirely if crushed
        header.setMinimumSectionSize(35)

        # Table behavior
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setShowGrid(True)
        self.setAlternatingRowColors(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        # Ensure the horizontal scrollbar never appears and takes up space
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Enable sorting
        self.setSortingEnabled(True)
        header.sectionClicked.connect(self._on_header_clicked)
        header.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        # Ultra-compact row heights (TC2000 style)
        self.verticalHeader().setDefaultSectionSize(22)

        header_font = QFont("Segoe UI", 9)
        header_font.setBold(True)
        header.setFont(header_font)

        # Initialize sorting state
        self._sort_column = -1
        self._sort_order = Qt.SortOrder.AscendingOrder

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

        self._rebuild_token_map()

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


    def _rebuild_token_map(self):
        """Build a token -> symbol reverse map for O(1) tick lookups."""
        self._token_to_symbol = {}
        for symbol, data in self._watchlist_data.items():
            token = data.get('instrument_token')
            if token is not None:
                try:
                    self._token_to_symbol[int(token)] = symbol
                except (TypeError, ValueError):
                    pass
        logger.debug(f"[{self.category}] token map rebuilt - {len(self._token_to_symbol)} entries")

    @staticmethod
    def _normalize_token(token) -> Optional[int]:
        """Normalize incoming instrument tokens for robust comparisons."""
        try:
            return int(token)
        except (TypeError, ValueError):
            return None

    def update_data(self, ticks: List[Dict]):
        """FIXED data update from WebSocket ticks with proper field handling"""
        updated_symbols = set()

        for tick in ticks:
            token = self._normalize_token(tick.get('instrument_token'))
            if token is None:
                continue

            # O(1) reverse-map lookup - replaces the O(n) linear scan
            symbol_found = self._token_to_symbol.get(token)
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

        # Update display for changed symbols
        for symbol in updated_symbols:
            if symbol in self._symbol_to_row:
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
        self._rebuild_token_map()

        # Repopulate table
        self._populate_full_table()

        logger.info(f"Added {symbol} to {self.category} watchlist with LTP: {self._watchlist_data[symbol]['ltp']}, "
                    f"Volume: {self._watchlist_data[symbol]['volume']}")
        self.watchlist_symbols_changed.emit()
        return True

    def remove_symbol(self, symbol: str) -> bool:
        """Enhanced symbol removal"""
        if symbol in self._watchlist_symbols:
            self._watchlist_symbols.remove(symbol)
            if symbol in self._watchlist_data:
                del self._watchlist_data[symbol]
            self._rebuild_token_map()
            self._populate_full_table()
            logger.info(f"Removed {symbol} from {self.category} watchlist")
            self.watchlist_symbols_changed.emit()
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
            self.item(row, 3).setText("0.00")

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
        self.item(row, 0).setToolTip(f"Open chart for {tradingsymbol}")
        self.item(row, 1).setText(f"{ltp:,.2f}" if ltp > 0 else "0.00")

        # Format volume with K/M notation
        if volume >= 1_000_000:
            volume_text = f"{volume / 1_000_000:.1f}M"
        elif volume >= 1_000:
            volume_text = f"{volume / 1_000:.1f}K"
        else:
            volume_text = str(volume)

        show_volume_strength = bool(self._color_theme.get("enable_volume_strength_indicator", False))
        if show_volume_strength:
            if volume >= 5_000_000:
                strength = "3pt"
            elif volume >= 1_000_000:
                strength = "2pt"
            elif volume >= 250_000:
                strength = "1pt"
            else:
                strength = "0pt"
            self.item(row, 2).setText(f"{strength} {volume_text}")
        else:
            self.item(row, 2).setText(volume_text)
        self.item(row, 2).setToolTip(f"Reported volume: {volume:,.0f}")

        # Format change percentage
        self.item(row, 3).setText(f"{change_pct:+.2f}" if abs(change_pct) > 0.01 else "0.00")

        # Set data for proper sorting
        self.item(row, 0).setData(Qt.ItemDataRole.UserRole, tradingsymbol)
        self.item(row, 1).setData(Qt.ItemDataRole.UserRole, ltp)
        self.item(row, 2).setData(Qt.ItemDataRole.UserRole, volume)
        self.item(row, 3).setData(Qt.ItemDataRole.UserRole, change_pct)

        # Apply colors
        table_colors = self._color_theme.get("tables", {})
        directional_colors_enabled = bool(self._color_theme.get("enable_table_directional_colors", False))
        profit_color = QColor(table_colors.get("positive", "#26a69a"))
        loss_color = QColor(table_colors.get("negative", "#ef5350"))
        neutral_color = QColor(table_colors.get("neutral", "#a9a9a9"))
        color = neutral_color
        if directional_colors_enabled:
            color = profit_color if change_pct > 0 else (loss_color if change_pct < 0 else neutral_color)

        self.item(row, 1).setForeground(color)
        self.item(row, 3).setForeground(color)
        self.item(row, 2).setForeground(QColor(table_colors.get("volume", "#45d4ff")))

        if directional_colors_enabled and change_pct > 0:
            self.item(row, 3).setBackground(QBrush(QColor(18, 55, 34, 140)))
        elif directional_colors_enabled and change_pct < 0:
            self.item(row, 3).setBackground(QBrush(QColor(70, 20, 20, 140)))
        else:
            self.item(row, 3).setBackground(QBrush(QColor(35, 35, 35, 100)))

        # Set alignments
        self.item(row, 0).setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.item(row, 1).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.item(row, 2).setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.item(row, 3).setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        symbol_font = self.item(row, 0).font()
        symbol_font.setBold(True)
        self.item(row, 0).setFont(symbol_font)

    def apply_color_theme(self, theme: Dict):
        self._color_theme = theme or self._color_theme
        for symbol, row in self._symbol_to_row.items():
            if symbol in self._watchlist_data:
                self._update_row_data(row, self._watchlist_data[symbol])

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
                background-color: #0b1019;
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
                background-color: #6ec8ff;
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
    Watchlist widget with a category dropdown and stacked tables.
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

    def apply_color_theme(self, theme: Dict):
        for table in self._tables.values():
            table.apply_color_theme(theme)

    def _setup_ui(self):
        """Sets up the main UI layout with dropdown category selector."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.setMinimumWidth(350)

        self.category_dropdown = QComboBox()
        self.category_dropdown.setObjectName("watchlistCategoryDropdown")
        self.table_stack = QStackedWidget()
        self.table_stack.setObjectName("watchlistTableStack")
        self._categories: List[str] = ["Breakouts", "EP", "Parabolic"]

        for category in self._categories:
            table = TradingTable(category)
            self._tables[category] = table

            table.symbol_selected.connect(self.symbol_selected.emit)
            table.place_order_requested.connect(self.place_order_requested.emit)
            table.advanced_buy_order_requested.connect(self.advanced_buy_order_requested.emit)
            table.advanced_sell_order_requested.connect(self.advanced_sell_order_requested.emit)
            table.bracket_order_requested.connect(self.bracket_order_requested.emit)
            table.watchlist_symbols_changed.connect(
                lambda c=category: self._handle_watchlist_symbols_changed(c)
            )

            self.category_dropdown.addItem(category.upper(), category)
            self.table_stack.addWidget(table)

        self.category_dropdown.currentIndexChanged.connect(self.table_stack.setCurrentIndex)
        self.table_stack.currentChanged.connect(self.category_dropdown.setCurrentIndex)

        layout.addWidget(self.category_dropdown)
        layout.addWidget(self.table_stack)

    def _handle_watchlist_symbols_changed(self, category: str):
        """Persist symbol list changes immediately for the given category."""
        self._save_watchlist(category)
        self.watchlist_changed.emit()

    def _apply_dynamic_styles(self):
        """Apply styles for dropdown and table stack."""
        dynamic_stylesheet = """
            /* Main Widget */
            TabbedWatchlistWidget {
                background-color: #05070b;
                color: #e0e0e0;
                font-family: "Segoe UI", Arial, sans-serif;
                font-size: 13px;
            }

            QComboBox#watchlistCategoryDropdown {
                background-color: #05070b;
                border: 1px solid #1a2536;
                color: #7fd4ff;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 8px;
                margin: 0px;
                min-height: 24px;
            }

            QComboBox#watchlistCategoryDropdown:hover {
                background-color: #0b1019;
                color: #dbe9ff;
            }

            QComboBox#watchlistCategoryDropdown::drop-down {
                border: none;
                width: 20px;
            }

            QComboBox#watchlistCategoryDropdown::down-arrow {
                width: 10px;
                height: 10px;
            }

            QComboBox#watchlistCategoryDropdown QAbstractItemView {
                background-color: #05070b;
                color: #dbe9ff;
                selection-background-color: #234b73;
                border: 1px solid #1a2536;
                outline: none;
            }

            QStackedWidget#watchlistTableStack {
                border: 1px solid #202020;
                border-top: none;
                background-color: #05070b;
            }

            /* Table Styling - EXACT match to scanner table */
            TradingTable {
                background-color: #05070b;
                border: 1px solid #1a2536;
                gridline-color: #162131;
                selection-background-color: #234b73;
                alternate-background-color: #070b12;
                outline: none;
                show-decoration-selected: 0;
                font-size: 12px;
                border-radius: 0px;
            }

            TradingTable::item {
                padding: 1px 5px;
                border-bottom: 1px solid #101926;
                background-color: transparent;
                font-size: 12px;
            }

            TradingTable::item:selected {
                background-color: #234b73 !important;
                outline: none;
                border: none;
                color: #ffffff;
                font-weight: 600;
            }

            TradingTable::item:focus {
                background-color: #234b73 !important;
                outline: none;
                border: none;
            }

            TradingTable::item:hover {
                background-color: transparent;
            }

            TradingTable::item:alternate {
                background-color: #070b12;
            }

            TradingTable::item:alternate:selected {
                background-color: #234b73 !important;
                color: #ffffff;
                font-weight: 600;
            }

            /* Header Styling - EXACT match to scanner table */
            QHeaderView::section {
                background-color: #0b1019;
                color: #7fd4ff;
                padding: 2px 5px;
                border: none;
                border-bottom: 1px solid #24344c;
                border-right: 1px solid #121c2b;
                font-weight: 600;
                font-size: 11px;
            }
            QHeaderView {
                background-color: #0b1019;
                border: none;
                margin: 0px;
            }
            QHeaderView::section:last {
                border-right: none;
            }

            QHeaderView::section:hover {
                background-color: #16253a;
                color: #dbe9ff;
            }

            QHeaderView::down-arrow {
                color: #6ec8ff;
                width: 8px;
                height: 8px;
                subcontrol-position: center right;
                subcontrol-origin: margin;
                margin-right: 2px;
            }

            QHeaderView::up-arrow {
                color: #6ec8ff;
                width: 8px;
                height: 8px;
                subcontrol-position: center right;
                subcontrol-origin: margin;
                margin-right: 2px;
            }

            /* Remove Button Styling */
            QPushButton#removeButton {
                background-color: transparent;
                color: #cc4444;
                border: none;
                font-weight: bold;
                font-size: 12px;
                border-radius: 8px;
                padding: 0px;
                margin: 0px;
            }

            QPushButton#removeButton:hover {
                color: #ff6666;
                background-color: #2a1f1f;
            }

            /* Enhanced Scrollbars */
            QScrollBar:vertical {
                background-color: #05070b;
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
                background-color: #05070b;
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
        """

        self.setStyleSheet(dynamic_stylesheet)

    def _apply_styles(self):
        """Apply full watchlist widget styles."""
        self._apply_dynamic_styles()

    # Method to force tab width recalculation (useful for external calls)
    def force_update_tab_widths(self):
        """Kept for backward compatibility. Styles are static with dropdown layout."""
        self._apply_dynamic_styles()

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
            current_index = self.table_stack.currentIndex()
            category = self._categories[current_index]

        if category in self._tables:
            success = self._tables[category].add_symbol(symbol)
            if success:
                # Subscribe to token
                if symbol in self._instrument_map:
                    token = self._instrument_map[symbol].get('instrument_token')
                    if token:
                        self.subscribe_tokens_requested.emit([token])
                logger.info(f"Successfully added {symbol} to {category}")
                return True
            else:
                logger.warning(f"Failed to add {symbol} to {category}")
        return False

    def get_current_category(self) -> str:
        """Returns the currently selected category."""
        current_index = self.table_stack.currentIndex()
        return self._categories[current_index]

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
