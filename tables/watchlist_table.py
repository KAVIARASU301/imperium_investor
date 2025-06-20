# Refactored file: swing_trader/swing_trader-2a35ae38ebac01e2a2096f450e74f084a599031f/tables/watchlist_table.py
import logging
import json
import os
from typing import List, Dict
from functools import partial

from PySide6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QPushButton, QVBoxLayout, QWidget,
    QHeaderView, QAbstractItemView, QMenu, QTabWidget
)
from PySide6.QtCore import Qt, Signal, Slot, QPoint, QSettings
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
    Individual table widget for each trading strategy category.
    Maintains the compact TC2000-like appearance with enhanced context menus.
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

        self._configure_table()
        self._connect_signals()

    def _configure_table(self):
        """Configures the table to achieve a compact, TC2000-like appearance."""
        self.setColumnCount(5)
        self.setHorizontalHeaderLabels(["Symbol", "LTP", "Volume", "Chg %", ""])

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

    def set_instrument_map(self, instrument_map: Dict[str, Dict]):
        """Receives the master instrument map for data lookups."""
        self._instrument_map = instrument_map
        self._populate_full_table()

    def update_data(self, ticks: List[Dict]):
        """Updates LTP and change from WebSocket ticks."""
        for tick in ticks:
            token = tick.get('instrument_token')
            ltp = tick.get('last_price')
            volume = tick.get('volume', 0)

            for symbol, data in self._watchlist_data.items():
                if data.get('instrument_token') == token and ltp is not None:
                    data['ltp'] = ltp
                    data['volume'] = volume

                    # Use 'ohlc.close' from instrument map as base for change_pct if available,
                    # otherwise fallback to current ltp. This 'ohlc.close' is typically prev day's close.
                    instrument_info = self._instrument_map.get(symbol, {})
                    prev_close = instrument_info.get('ohlc', {}).get('close', ltp)

                    if prev_close > 0:
                        change_pct = ((ltp - prev_close) / prev_close) * 100
                        data['change_pct'] = change_pct
                    else:
                        data['change_pct'] = 0.0  # Handle division by zero or no prev_close

                    # Update day high/low from tick if present
                    if 'ohlc' in tick and isinstance(tick['ohlc'], dict):
                        data['day_high'] = max(data.get('day_high', 0.0), tick['ohlc'].get('high', 0.0))
                        data['day_low'] = min(data.get('day_low', float('inf')), tick['ohlc'].get('low', float('inf')))
                        # Ensure inf is converted to 0 if no low is available initially
                        if data['day_low'] == float('inf'):
                            data['day_low'] = 0.0

                    if symbol in self._symbol_to_row:
                        row = self._symbol_to_row[symbol]
                        self._update_row_data(row, data)
                    break

    def add_symbol(self, symbol: str):
        """Adds a new symbol to this category's watchlist."""
        if not symbol or symbol in self._watchlist_data or symbol not in self._instrument_map:
            logger.warning(
                f"Could not add '{symbol}' to {self.category}. It may already exist or is not a valid symbol.")
            return False

        instrument = self._instrument_map[symbol]
        self._watchlist_data[symbol] = {
            "tradingsymbol": symbol,
            "instrument_token": instrument.get('instrument_token'),
            "close_price": instrument.get('ohlc', {}).get('close', 0.0),  # Prev day close
            "ltp": instrument.get('last_price', 0.0),
            "volume": instrument.get('volume', 0),
            "change_pct": 0.0,
            "day_high": instrument.get('ohlc', {}).get('high', 0.0),
            "day_low": instrument.get('ohlc', {}).get('low', 0.0),
        }
        # Calculate initial change_pct
        if self._watchlist_data[symbol]['close_price'] > 0 and self._watchlist_data[symbol]['ltp'] > 0:
            self._watchlist_data[symbol]['change_pct'] = (
                                                                 (self._watchlist_data[symbol]['ltp'] -
                                                                  self._watchlist_data[symbol]['close_price']) /
                                                                 self._watchlist_data[symbol]['close_price']
                                                         ) * 100

        self._populate_full_table()
        logger.info(f"Added {symbol} to {self.category} watchlist.")
        return True

    def remove_symbol(self, symbol: str):
        """Removes a symbol from this category's watchlist."""
        if symbol in self._watchlist_data:
            del self._watchlist_data[symbol]
            self._populate_full_table()
            logger.info(f"Removed {symbol} from {self.category} watchlist.")
            return True
        return False

    def _populate_full_table(self):
        """Clears and repopulates the entire table."""
        self.setRowCount(0)
        self._symbol_to_row.clear()

        sorted_symbols = sorted(self._watchlist_data.keys())
        self.setRowCount(len(sorted_symbols))

        for row, symbol in enumerate(sorted_symbols):
            # Always try to update/fill data from instrument_map if available
            if symbol in self._instrument_map:
                instrument = self._instrument_map[symbol]

                # Retrieve existing data or create a new dict
                current_data = self._watchlist_data.get(symbol, {"tradingsymbol": symbol})

                # Update with details from instrument_map
                current_data["instrument_token"] = instrument.get('instrument_token')
                current_data["ltp"] = instrument.get('last_price', 0.0)
                current_data["volume"] = instrument.get('volume', 0)

                ohlc = instrument.get('ohlc', {})
                current_data["close_price"] = ohlc.get('close', 0.0)  # Previous day's closing
                current_data["day_high"] = ohlc.get('high', 0.0)
                current_data["day_low"] = ohlc.get('low', 0.0)

                # Calculate initial change_pct if possible
                if current_data["close_price"] > 0:
                    current_data["change_pct"] = (
                                                         (current_data["ltp"] - current_data["close_price"]) /
                                                         current_data["close_price"]
                                                 ) * 100
                else:
                    current_data["change_pct"] = 0.0

                self._watchlist_data[symbol] = current_data  # Update the master data

            self._symbol_to_row[symbol] = row
            self._populate_row(row, symbol)

    def _populate_row(self, row: int, symbol: str):
        """Populates a single row with data and widgets."""
        data = self._watchlist_data[symbol]

        for i in range(4):
            self.setItem(row, i, QTableWidgetItem())
        self.setCellWidget(row, 4, self._create_remove_button(row))

        self._update_row_data(row, data)

    def _update_row_data(self, row: int, data: Dict):
        """Updates the text and color for a single row."""
        # Ensure items exist before setting text and alignment
        for col_idx in range(self.columnCount()):
            if not self.item(row, col_idx):
                self.setItem(row, col_idx, QTableWidgetItem())

        # Safely get data, defaulting to 'N/A' or 0.0 if key is missing
        tradingsymbol = data.get('tradingsymbol', 'N/A')
        ltp = data.get('ltp', 0.0)
        volume = data.get('volume', 0)
        change_pct = data.get('change_pct', 0.0)
        day_high = data.get('day_high', 0.0)
        day_low = data.get('day_low', 0.0)

        self.item(row, 0).setText(tradingsymbol)
        self.item(row, 1).setText(f"{ltp:.2f}")

        # Format volume in K/M format for readability
        if volume >= 1000000:
            volume_text = f"{volume / 1000000:.1f}M"
        elif volume >= 1000:
            volume_text = f"{volume / 1000:.0f}K"
        else:
            volume_text = str(volume)
        self.item(row, 2).setText(volume_text)

        self.item(row, 3).setText(f"{change_pct:+.2f}%")  # Changed to + sign for positive changes

        # TC2000-style colors
        profit_color = QColor(60, 179, 113)  # Medium Sea Green
        loss_color = QColor(220, 20, 60)  # Crimson
        neutral_color = QColor(169, 169, 169)  # DarkGray

        color = profit_color if change_pct > 0 else (loss_color if change_pct < 0 else neutral_color)

        # Apply color to LTP and Change %
        self.item(row, 1).setForeground(color)
        self.item(row, 3).setForeground(color)  # Color code %Chg

        # Volume stays neutral colored
        self.item(row, 2).setForeground(neutral_color)

        # Alignments
        self.item(row, 0).setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.item(row, 1).setTextAlignment(
            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)  # Center align LTP
        self.item(row, 2).setTextAlignment(
            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)  # Center align Volume
        self.item(row, 3).setTextAlignment(
            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)  # Center align Chg %

    def _create_remove_button(self, row) -> QPushButton:
        """Creates a minimal 'x' button to remove a symbol."""
        remove_btn = QPushButton("×")
        remove_btn.setObjectName("removeButton")
        remove_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        remove_btn.setFixedSize(16, 16)
        remove_btn.clicked.connect(partial(self._remove_symbol_at_row, row))
        return remove_btn

    def _remove_symbol_at_row(self, row: int):
        """Removes a symbol from the watchlist efficiently."""
        if 0 <= row < self.rowCount():
            symbol_to_remove = list(self._watchlist_data.keys())[row]
            self.remove_symbol(symbol_to_remove)

    def _on_cell_clicked(self, row, column):
        """Handles clicks on a cell to select the symbol for charting."""
        if column != 4 and row < self.rowCount():
            try:
                symbol = self.item(row, 0).text()
                self.symbol_selected.emit(symbol)
            except AttributeError:
                logger.warning(f"Could not get symbol from clicked row {row}.")

    def _show_enhanced_context_menu(self, pos: QPoint):
        """Enhanced context menu with advanced order options."""
        row = self.rowAt(pos.y())
        if row < 0:
            return

        try:
            symbol = self.item(row, 0).text()
            if not symbol:
                return
        except AttributeError:
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

        # Bracket order
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

        # Add to other watchlists
        menu.addSeparator()
        watchlist_label = QAction("Add to Watchlist", self)
        watchlist_label.setEnabled(False)
        menu.addAction(watchlist_label)

        # Get parent widget to access other watchlist categories
        parent_widget = self.parent()
        if hasattr(parent_widget, 'parent') and hasattr(parent_widget.parent(), '_tables'):
            main_widget = parent_widget.parent()
            for category, table in main_widget._tables.items():
                if category != self.category:  # Don't show current category
                    add_action = QAction(f"Add to {category}", self)
                    add_action.triggered.connect(lambda checked, cat=category: main_widget.add_symbol(symbol, cat))
                    menu.addAction(add_action)

        menu.exec(self.viewport().mapToGlobal(pos))

    def _request_trade(self, symbol: str, transaction_type: str):
        """Emits a signal to open the basic order dialog."""
        order_details = {
            "tradingsymbol": symbol,
            "transaction_type": transaction_type,
        }
        self.place_order_requested.emit(order_details)

    def get_all_tokens(self) -> List[int]:
        """Returns a list of all instrument tokens currently in this watchlist."""
        return [
            data['instrument_token']
            for data in self._watchlist_data.values()
            if data and data.get('instrument_token')
        ]

    def get_watchlist_data(self) -> Dict[str, Dict]:
        """Returns the current watchlist data for saving."""
        return self._watchlist_data.copy()

    def load_watchlist_data(self, data: Dict[str, Dict]):
        """Loads watchlist data from saved state."""
        self._watchlist_data = data
        self._populate_full_table()


class TabbedWatchlistWidget(QWidget):
    """
    Main tabbed watchlist widget that contains three trading strategy categories.
    Maintains TC2000-style appearance with professional tabs and enhanced functionality.
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

        # Create tab widget directly without header
        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("tradingTabs")

        # Disable tab scrolling and ensure all tabs are always visible
        tab_bar = self.tab_widget.tabBar()
        tab_bar.setUsesScrollButtons(False)
        tab_bar.setExpanding(True)

        # Create tables for each category
        categories = ["Breakouts", "EP", "Parabolic"]
        for category in categories:
            table = TradingTable(category)
            self._tables[category] = table

            # Connect signals - Enhanced with new advanced order signals
            table.symbol_selected.connect(self.symbol_selected.emit)
            table.place_order_requested.connect(self.place_order_requested.emit)
            table.advanced_buy_order_requested.connect(self.advanced_buy_order_requested.emit)
            table.advanced_sell_order_requested.connect(self.advanced_sell_order_requested.emit)
            table.bracket_order_requested.connect(self.bracket_order_requested.emit)

            self.tab_widget.addTab(table, category.upper())

        layout.addWidget(self.tab_widget)

        # Set equal tab widths after the widget is shown
        self.tab_widget.show()
        self._set_equal_tab_widths()

    def _set_equal_tab_widths(self):
        """Sets equal width for all tabs based on the widget width."""
        if hasattr(self, 'tab_widget'):
            # Get the tab bar
            tab_bar = self.tab_widget.tabBar()

            # Calculate equal width for all tabs accounting for borders and margins
            total_width = self.width()
            tab_count = tab_bar.count()
            if tab_count > 0:
                # Account for tab margins (1px between tabs) and borders
                margins_and_borders = (tab_count - 1) * 1 + 4  # 1px margin between tabs + 4px for widget borders
                available_width = total_width - margins_and_borders
                tab_width = available_width // tab_count

                # Apply the calculated width via stylesheet
                self._apply_tab_width_style(tab_width)

    def _apply_styles(self):
        """Applies TC2000-inspired styling to the tabbed watchlist."""
        # Initial styling - will be overridden by _apply_tab_width_style
        # This method is called once during __init__ and then _apply_tab_width_style handles the full stylesheet.
        # We need to ensure that the initial stylesheet applies the base dark background and default text colors.
        self.setStyleSheet("""
            TabbedWatchlistWidget {
                background-color: #0a0a0a; /* Deep black background */
                color: #e0e0e0; /* Light gray text */
                font-family: "Segoe UI", Arial, sans-serif; /* Professional font */
                font-size: 13px;
                border: 1px solid #202020; /* Subtle border for the main widget */
            }
        """)

    def set_instrument_map(self, instrument_map: Dict[str, Dict]):
        """Receives the master instrument map for data lookups."""
        self._instrument_map = instrument_map
        for table in self._tables.values():
            table.set_instrument_map(instrument_map)

    @Slot(list)
    def update_data(self, ticks: List[Dict]):
        """Public slot to update LTP and change from WebSocket ticks."""
        for table in self._tables.values():
            table.update_data(ticks)

    def add_symbol(self, symbol: str, category: str = None):
        """Adds a new symbol to the specified category or current tab."""
        if category is None:
            current_index = self.tab_widget.currentIndex()
            category = list(self._tables.keys())[current_index]

        if category in self._tables:
            success = self._tables[category].add_symbol(symbol)
            if success:
                self._save_watchlist(category)

                # Get tokens for subscription
                instrument = self._instrument_map.get(symbol, {})
                token = instrument.get('instrument_token')
                if token:
                    self.subscribe_tokens_requested.emit([token])

                self.watchlist_changed.emit()
                return True
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
        return all_tokens

    def _load_all_watchlists(self):
        """Loads all watchlists from their respective JSON files."""
        for category, filepath in WATCHLIST_FILES.items():
            if os.path.exists(filepath):
                try:
                    with open(filepath, 'r') as f:
                        symbols = json.load(f)

                    # Convert list of symbols to dict format
                    data = {}
                    for symbol in symbols:
                        data[symbol] = {}  # Initialize with empty dict

                    self._tables[category].load_watchlist_data(data)
                    logger.info(f"Loaded {len(symbols)} symbols for {category} watchlist.")

                except (json.JSONDecodeError, IOError) as e:
                    logger.error(f"Failed to load {category} watchlist: {e}")

    def _save_watchlist(self, category: str):
        """Saves the specified watchlist to its JSON file."""
        if category not in WATCHLIST_FILES:
            return

        filepath = WATCHLIST_FILES[category]
        try:
            dir_name = os.path.dirname(filepath)
            if dir_name and not os.path.exists(dir_name):
                os.makedirs(dir_name)

            watchlist_data = self._tables[category].get_watchlist_data()
            symbols = list(watchlist_data.keys())

            with open(filepath, 'w') as f:
                json.dump(symbols, f, indent=4)

            logger.info(f"Saved {category} watchlist with {len(symbols)} symbols.")

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
        """Applies dynamic tab width to the stylesheet, combining with table styles."""
        # Calculate exact positioning to eliminate any shifting
        tab_width_exact = f"{tab_width}px"

        # Combined stylesheet
        self.setStyleSheet(f"""
            /* Main Widget */
            TabbedWatchlistWidget {{
                background-color: #0a0a0a; /* Deep black background */
                color: #e0e0e0; /* Light gray text */
                font-family: "Segoe UI", Arial, sans-serif; /* Professional font */
                font-size: 13px;
                border: 1px solid #202020; /* Subtle border for the main widget */
            }}

            /* Tab Widget Styling - KEPT INTACT AS REQUESTED */
            QTabWidget#tradingTabs {{
                background-color: #0a0a0a; /* Match main background */
                border: none;
            }}

            QTabWidget#tradingTabs::pane {{
                border: 1px solid #202020; /* Darker border for pane */
                background-color: #0a0a0a; /* Match main background */
                border-radius: 0px;
            }}

            QTabWidget#tradingTabs::tab-bar {{
                alignment: left;
            }}

            /* Hide tab scroll buttons completely */
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

            QTabBar QToolButton::right-arrow,
            QTabBar QToolButton::left-arrow {{
                width: 0px;
                height: 0px;
                background: transparent;
            }}

            /* Fixed Tab Bar Styling - No movement */
            QTabBar::tab {{
                background-color: #1a1a1a; /* Darker tab background */
                color: #8892b0;
                padding: 6px 0px; /* Reduced vertical padding for tabs */
                margin: 0px;
                border: 1px solid #202020; /* Darker border for tabs */
                border-bottom: none;
                border-right: 1px solid #202020;
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 0.5px;
                text-align: center;
                width: {tab_width_exact};
                min-width: {tab_width_exact};
                max-width: {tab_width_exact};
                position: fixed;
            }}

            QTabBar::tab:last {{
                border-right: 1px solid #202020;
            }}

            QTabBar::tab:selected {{
                background-color: #0a0a0a; /* Match main background when selected */
                color: #6a9cff; /* Professional blue for selected tab text */
                border-bottom: 2px solid #6a9cff;
                width: {tab_width_exact};
                min-width: {tab_width_exact};
                max-width: {tab_width_exact};
            }}

            QTabBar::tab:hover:!selected {{
                background-color: #2a2a2a; /* Darker hover for non-selected tabs */
                color: #ccd6f6;
                width: {tab_width_exact};
                min-width: {tab_width_exact};
                max-width: {tab_width_exact};
            }}

            /* Table Styling - Applied from previous task */
            TradingTable {{
                border: 1px solid #202020; /* Subtle dark border for the table */
                gridline-color: #151515; /* Almost invisible grid lines */
                font-size: 12px;
                background-color: #0d0d0d; /* Deep black table background */
                selection-background-color: rgba(74, 122, 191, 0.2); /* Softer blue selection with transparency */
                selection-color: #ffffff;
                border-radius: 0px; /* No rounding */
            }}
            TradingTable::item {{
                padding: 5px 8px; /* Consistent padding */
                border-bottom: 1px solid #1a1a1a; /* Thin row separator */
                background-color: transparent;
                color: #e0e0e0;
            }}
            TradingTable::item:selected {{
                background-color: rgba(74, 122, 191, 0.2); /* Softer blue selection with transparency */
                color: #ffffff;
                font-weight: 600;
            }}
            TradingTable::item:alternate {{
                background-color: #121212; /* Very dark alternate row */
            }}

            /* Header Styling - Applied from previous task, further reduced padding */
            QHeaderView::section {{
                background-color: #1a1a1a; /* Header background */
                color: #a0c0ff; /* Header text color */
                padding: 3px 10px; /* Further reduced header padding */
                border: none;
                border-bottom: 1px solid #303030; /* Clear header bottom border */
                border-right: 1px solid #101010; /* Dark vertical header separators */
                font-weight: 600;
                font-size: 11px;
            }}
            QHeaderView::section:last {{
                border-right: none;
            }}
            QHeaderView::section:hover {{
                background-color: #2a2a2a; /* Subtle hover for headers */
            }}

            /* Remove Button Styling */
            QPushButton#removeButton {{
                background-color: transparent;
                color: #cc4444; /* Red color for 'X' */
                border: none;
                font-weight: bold;
                font-size: 12px;
                border-radius: 8px;
                padding: 0px;
            }}

            QPushButton#removeButton:hover {{
                color: #ff6666; /* Lighter red on hover */
                background-color: #2a1f1f;
            }}

            /* Context Menu - Applied from previous task */
            QMenu {{
                background-color: #1a1a1a;
                color: #e0e0e0;
                border: 1px solid #303030;
                padding: 3px 0px; /* Reduced padding */
                font-size: 11px;
            }}
            QMenu::item {{
                padding: 5px 15px; /* Reduced padding */
                background-color: transparent;
            }}
            QMenu::item:selected {{
                background-color: rgba(74, 122, 191, 0.2); /* Softer blue for context menu selection */
                color: #ffffff;
            }}

            /* Scrollbar Styling - Invisible */
            QScrollBar:vertical {{
                width: 0px; /* Make invisible */
            }}
            QScrollBar::handle:vertical {{
                width: 0px; /* Make invisible */
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0px; /* Make invisible */
            }}
            QScrollBar:horizontal {{
                height: 0px; /* Make invisible */
            }}
            QScrollBar::handle:horizontal {{
                height: 0px; /* Make invisible */
            }}
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {{
                width: 0px; /* Make invisible */
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
        """Override to save geometry when widget is closed."""
        self._save_geometry()
        super().closeEvent(event)
