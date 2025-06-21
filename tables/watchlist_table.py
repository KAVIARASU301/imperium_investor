# Enhanced watchlist_table.py - TC2000 style dynamic watchlist
import logging
import json
import os
from typing import List, Dict, Optional
from functools import partial

from PySide6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QPushButton, QVBoxLayout, QWidget,
    QHeaderView, QAbstractItemView, QMenu, QTabWidget
)
from PySide6.QtCore import Qt, Signal, Slot, QPoint, QSettings, QTimer
from PySide6.QtGui import QColor, QCursor, QAction, QFont

logger = logging.getLogger(__name__)

# Separate files for each watchlist category
WATCHLIST_FILES = {
    "Breakouts": "user_data/watchlist_breakouts.json",
    "EP": "user_data/watchlist_episodic.json",
    "Parabolic": "user_data/watchlist_parabolic.json"
}

# Settings for remembering UI state
SETTINGS_KEY_WIDTH = "watchlist/widget_width"


class TradingTable(QTableWidget):
    """
    Enhanced trading table with proper data persistence and real-time updates
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

        # Initialize empty watchlist data
        self._watchlist_symbols = set()  # Track symbols separately

        self._configure_table()
        self._connect_signals()
        self._setup_data_refresh()

    def _configure_table(self):
        """Configures the table to achieve a compact, TC2000-like appearance."""
        self.setColumnCount(5)
        self.setHorizontalHeaderLabels(["Symbol", "LTP", "Vol", "Chg %", ""])

        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setVisible(True)

        # Set header style for better visibility
        header = self.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)

        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)

        # Column sizing
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)  # Symbol
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # LTP
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Volume
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Chg %
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)  # Remove button
        self.setColumnWidth(4, 24)  # Minimal width for X button

        # Row height for compact appearance
        self.verticalHeader().setDefaultSectionSize(28)

    def _connect_signals(self):
        """Connect table signals."""
        self.cellClicked.connect(self._on_cell_clicked)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_enhanced_context_menu)

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
        """Initialize watchlist data from instrument map for existing symbols"""
        for symbol in list(self._watchlist_symbols):
            if symbol in self._instrument_map:
                instrument = self._instrument_map[symbol]

                # Create comprehensive data structure
                self._watchlist_data[symbol] = {
                    "tradingsymbol": symbol,
                    "instrument_token": instrument.get('instrument_token'),
                    "exchange": instrument.get('exchange', 'NSE'),
                    "segment": instrument.get('segment', 'NSE'),
                    "last_price": instrument.get('last_price', 0.0),
                    "volume": instrument.get('volume', 0),
                    "ohlc": instrument.get('ohlc', {}),
                    "ltp": instrument.get('last_price', 0.0),  # Current LTP
                    "change_pct": 0.0,
                    "day_high": instrument.get('ohlc', {}).get('high', 0.0),
                    "day_low": instrument.get('ohlc', {}).get('low', 0.0),
                    "prev_close": instrument.get('ohlc', {}).get('close', 0.0),
                }

                # Calculate initial change percentage
                self._calculate_change_percentage(symbol)

                logger.debug(f"Initialized data for {symbol}: LTP={self._watchlist_data[symbol]['ltp']}")
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
            change_pct = ((ltp - prev_close) / prev_close) * 100
            data['change_pct'] = change_pct
        else:
            data['change_pct'] = 0.0

    def update_data(self, ticks: List[Dict]):
        """Enhanced data update from WebSocket ticks"""
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

            # Update LTP and volume
            if 'last_price' in tick and tick['last_price'] is not None:
                data['ltp'] = float(tick['last_price'])
                data['last_price'] = float(tick['last_price'])

            if 'volume' in tick and tick['volume'] is not None:
                data['volume'] = int(tick['volume'])

            # Update OHLC if available
            if 'ohlc' in tick and isinstance(tick['ohlc'], dict):
                tick_ohlc = tick['ohlc']
                data['day_high'] = max(data.get('day_high', 0.0), tick_ohlc.get('high', 0.0))

                day_low = tick_ohlc.get('low', 0.0)
                if day_low > 0:  # Only update if we have a valid low
                    if data.get('day_low', 0.0) == 0.0:
                        data['day_low'] = day_low
                    else:
                        data['day_low'] = min(data['day_low'], day_low)

            # Recalculate change percentage
            self._calculate_change_percentage(symbol_found)
            updated_symbols.add(symbol_found)

            logger.debug(
                f"Updated {symbol_found}: LTP={data['ltp']}, Vol={data['volume']}, Chg={data['change_pct']:.2f}%")

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

        # Add to symbol set
        self._watchlist_symbols.add(symbol)

        # Initialize data
        instrument = self._instrument_map[symbol]
        self._watchlist_data[symbol] = {
            "tradingsymbol": symbol,
            "instrument_token": instrument.get('instrument_token'),
            "exchange": instrument.get('exchange', 'NSE'),
            "segment": instrument.get('segment', 'NSE'),
            "last_price": instrument.get('last_price', 0.0),
            "volume": instrument.get('volume', 0),
            "ohlc": instrument.get('ohlc', {}),
            "ltp": instrument.get('last_price', 0.0),
            "change_pct": 0.0,
            "day_high": instrument.get('ohlc', {}).get('high', 0.0),
            "day_low": instrument.get('ohlc', {}).get('low', 0.0),
            "prev_close": instrument.get('ohlc', {}).get('close', 0.0),
        }

        # Calculate initial change percentage
        self._calculate_change_percentage(symbol)

        # Repopulate table
        self._populate_full_table()

        logger.info(f"Added {symbol} to {self.category} watchlist with LTP: {self._watchlist_data[symbol]['ltp']}")
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
        """Enhanced row data update with proper formatting"""
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

        # Format volume
        if volume >= 1000000:
            volume_text = f"{volume / 1000000:.1f}M"
        elif volume >= 1000:
            volume_text = f"{volume / 1000:.0f}K"
        else:
            volume_text = str(volume)
        self.item(row, 2).setText(volume_text)

        # Format change percentage
        self.item(row, 3).setText(f"{change_pct:+.2f}%" if change_pct != 0 else "0.00%")

        # Apply colors
        profit_color = QColor(60, 179, 113)  # Medium Sea Green
        loss_color = QColor(220, 20, 60)  # Crimson
        neutral_color = QColor(169, 169, 169)  # DarkGray

        color = profit_color if change_pct > 0 else (loss_color if change_pct < 0 else neutral_color)

        # Apply color to LTP and Change %
        self.item(row, 1).setForeground(color)
        self.item(row, 3).setForeground(color)
        self.item(row, 2).setForeground(neutral_color)

        # Set alignments
        self.item(row, 0).setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.item(row, 1).setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        self.item(row, 2).setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        self.item(row, 3).setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

    def _refresh_display(self):
        """Periodic refresh of display data"""
        for symbol, row in self._symbol_to_row.items():
            if symbol in self._watchlist_data:
                self._update_row_data(row, self._watchlist_data[symbol])

    def _create_remove_button(self, row) -> QPushButton:
        """Creates a minimal 'x' button to remove a symbol."""
        remove_btn = QPushButton("×")
        remove_btn.setObjectName("removeButton")
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

        # Refresh data action
        menu.addSeparator()
        refresh_action = QAction("Refresh Data", self)
        refresh_action.triggered.connect(lambda: self._refresh_symbol_data(symbol))
        menu.addAction(refresh_action)

        menu.exec(self.viewport().mapToGlobal(pos))

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
        """Load watchlist from list of symbols"""
        self._watchlist_symbols = set(symbols) if symbols else set()
        self._watchlist_data.clear()

        # Initialize data if instrument map is available
        if self._instrument_map:
            self._initialize_watchlist_data()
            self._populate_full_table()

        logger.info(f"Loaded {len(self._watchlist_symbols)} symbols for {self.category}")


class TabbedWatchlistWidget(QWidget):
    """
    Enhanced tabbed watchlist widget with proper persistence and real-time updates
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
        self._settings = QSettings("SwingTrader", "WatchlistWidget")

        self._setup_ui()
        self._apply_styles()
        self._load_all_watchlists()
        self._restore_geometry()

    def _setup_ui(self):
        """Sets up the main UI layout with tabs."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Create tab widget
        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("tradingTabs")

        # Disable tab scrolling
        tab_bar = self.tab_widget.tabBar()
        tab_bar.setUsesScrollButtons(False)
        tab_bar.setExpanding(True)

        # Create tables for each category
        categories = ["Breakouts", "EP", "Parabolic"]
        for category in categories:
            table = TradingTable(category)
            self._tables[category] = table

            # Connect all signals
            table.symbol_selected.connect(self.symbol_selected.emit)
            table.place_order_requested.connect(self.place_order_requested.emit)
            table.advanced_buy_order_requested.connect(self.advanced_buy_order_requested.emit)
            table.advanced_sell_order_requested.connect(self.advanced_sell_order_requested.emit)
            table.bracket_order_requested.connect(self.bracket_order_requested.emit)

            self.tab_widget.addTab(table, category.upper())

        layout.addWidget(self.tab_widget)
        self.tab_widget.show()
        self._set_equal_tab_widths()

    def _set_equal_tab_widths(self):
        """Sets equal width for all tabs based on the widget width."""
        if hasattr(self, 'tab_widget'):
            tab_bar = self.tab_widget.tabBar()
            total_width = self.width()
            tab_count = tab_bar.count()
            if tab_count > 0:
                margins_and_borders = (tab_count - 1) * 1 + 4
                available_width = total_width - margins_and_borders
                tab_width = available_width // tab_count
                self._apply_tab_width_style(tab_width)

    def _apply_styles(self):
        """Applies TC2000-inspired styling to the tabbed watchlist."""
        self.setStyleSheet("""
            TabbedWatchlistWidget {
                background-color: #0a0a0a;
                color: #e0e0e0;
                font-family: "Segoe UI", Arial, sans-serif;
                font-size: 13px;
                border: 1px solid #202020;
            }
        """)

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

            # Get symbols list
            symbols = self._tables[category].get_symbol_list()

            # Save to file
            with open(filepath, 'w') as f:
                json.dump(symbols, f, indent=4)

            logger.info(f"Saved {category} watchlist with {len(symbols)} symbols to {filepath}")

        except IOError as e:
            logger.error(f"Failed to save {category} watchlist: {e}")

    def _restore_geometry(self):
        """Restores the widget width from saved settings."""
        saved_width = self._settings.value(SETTINGS_KEY_WIDTH, 300, type=int)
        self.setFixedWidth(saved_width)

    def _save_geometry(self):
        """Saves the current widget width to settings."""
        self._settings.setValue(SETTINGS_KEY_WIDTH, self.width())

    def _apply_tab_width_style(self, tab_width: int):
        """Enhanced styling with proper tab width"""
        tab_width_exact = f"{tab_width}px"

        self.setStyleSheet(f"""
            /* Main Widget */
            TabbedWatchlistWidget {{
                background-color: #0a0a0a;
                color: #e0e0e0;
                font-family: "Segoe UI", Arial, sans-serif;
                font-size: 13px;
                border: 1px solid #202020;
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
            }}

            QTabWidget#tradingTabs::tab-bar {{
                alignment: left;
            }}

            /* Hide tab scroll buttons */
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

            /* Fixed Tab Bar Styling */
            QTabBar::tab {{
                background-color: #1a1a1a;
                color: #8892b0;
                padding: 6px 0px;
                margin: 0px;
                border: 1px solid #202020;
                border-bottom: none;
                border-right: 1px solid #202020;
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 0.5px;
                text-align: center;
                width: {tab_width_exact};
                min-width: {tab_width_exact};
                max-width: {tab_width_exact};
            }}

            QTabBar::tab:last {{
                border-right: 1px solid #202020;
            }}

            QTabBar::tab:selected {{
                background-color: #0a0a0a;
                color: #6a9cff;
                border-bottom: 2px solid #6a9cff;
                width: {tab_width_exact};
                min-width: {tab_width_exact};
                max-width: {tab_width_exact};
            }}

            QTabBar::tab:hover:!selected {{
                background-color: #2a2a2a;
                color: #ccd6f6;
                width: {tab_width_exact};
                min-width: {tab_width_exact};
                max-width: {tab_width_exact};
            }}

            /* Table Styling */
            TradingTable {{
                border: 1px solid #202020;
                gridline-color: #151515;
                font-size: 12px;
                background-color: #0d0d0d;
                selection-background-color: rgba(74, 122, 191, 0.2);
                selection-color: #ffffff;
                border-radius: 0px;
            }}

            TradingTable::item {{
                padding: 5px 8px;
                border-bottom: 1px solid #1a1a1a;
                background-color: transparent;
                color: #e0e0e0;
            }}

            TradingTable::item:selected {{
                background-color: rgba(74, 122, 191, 0.2);
                color: #ffffff;
                font-weight: 600;
            }}

            TradingTable::item:alternate {{
                background-color: #121212;
            }}

            /* Header Styling */
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
            }}

            QPushButton#removeButton:hover {{
                color: #ff6666;
                background-color: #2a1f1f;
            }}

            /* Scrollbar Styling - Invisible */
            QScrollBar:vertical {{
                width: 0px;
            }}
            QScrollBar::handle:vertical {{
                width: 0px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QScrollBar:horizontal {{
                height: 0px;
            }}
            QScrollBar::handle:horizontal {{
                height: 0px;
            }}
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {{
                width: 0px;
            }}
        """)

    def resizeEvent(self, event):
        """Override to save geometry and update tab widths when widget is resized."""
        super().resizeEvent(event)
        self._save_geometry()
        self._set_equal_tab_widths()

    def showEvent(self, event):
        """Override to set tab widths when widget is first shown."""
        super().showEvent(event)
        self._set_equal_tab_widths()

    def closeEvent(self, event):
        """Enhanced close event with proper cleanup"""
        # Save all watchlists
        for category in self._tables.keys():
            self._save_watchlist(category)

        # Stop timers
        for table in self._tables.values():
            if hasattr(table, '_data_update_timer'):
                table._data_update_timer.stop()

        self._save_geometry()
        super().closeEvent(event)