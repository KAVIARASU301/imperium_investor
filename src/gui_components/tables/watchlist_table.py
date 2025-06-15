import logging
import json
import os
from typing import List, Dict, Any
from PySide6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QPushButton, QVBoxLayout, QWidget,
    QHeaderView, QAbstractItemView, QMenu
)
from PySide6.QtCore import Qt, Signal, Slot, QPoint
from PySide6.QtGui import QColor, QCursor, QAction

logger = logging.getLogger(__name__)
WATCHLIST_FILE = "user_data/watchlist.json"


class WatchlistTable(QWidget):
    """
    A compact, real-time watchlist widget inspired by professional trading terminals.
    It displays a persistent list of stocks with live price updates and allows
    for quick actions like charting, trading, and list management.
    """
    symbol_selected = Signal(str)
    subscribe_tokens_requested = Signal(list)
    place_order_requested = Signal(dict)
    watchlist_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._instrument_map: Dict[str, Dict] = {}
        self._watchlist_data: Dict[str, Dict] = {}  # Cache for symbol data (LTP, close, etc.)
        self._symbol_to_row: Dict[str, int] = {}

        self._setup_ui()
        self._apply_styles()
        self._load_watchlist()

    def _setup_ui(self):
        """Sets up the main UI layout and table."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget()
        self._configure_table()
        layout.addWidget(self.table)

        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

    def _configure_table(self):
        """Configures the table to achieve a compact, TC2000-like appearance."""
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Symbol", "LTP", "Chg", "Chg %", ""])

        # Hide headers for a cleaner look
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setVisible(False)

        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)

        # Set column resize modes
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)  # Symbol
        for i in range(1, 4):  # LTP, Chg, Chg %
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)  # Remove button
        self.table.setColumnWidth(4, 25)

    def set_instrument_map(self, instrument_map: Dict[str, Dict]):
        """Receives the master instrument map for data lookups."""
        self._instrument_map = instrument_map
        # Now that we have the instrument map, we can fully populate the watchlist
        self._rebuild_table()

    @Slot(list)
    def update_data(self, ticks: List[Dict]):
        """Public slot to update LTP and change from WebSocket ticks."""
        for tick in ticks:
            token = tick.get('instrument_token')
            ltp = tick.get('last_price')

            # Find symbol corresponding to the token
            for symbol, data in self._watchlist_data.items():
                if data.get('instrument_token') == token:
                    if ltp is not None:
                        # Update cache
                        data['ltp'] = ltp
                        change = ltp - data.get('close_price', ltp)
                        change_pct = (change / data.get('close_price', 1)) * 100
                        data['change'] = change
                        data['change_pct'] = change_pct

                        # Update UI
                        if symbol in self._symbol_to_row:
                            row = self._symbol_to_row[symbol]
                            self._update_row_data(row, data)
                    break

    def add_symbol(self, symbol: str):
        """Adds a new symbol to the watchlist."""
        if symbol and symbol not in self._watchlist_data and symbol in self._instrument_map:
            instrument = self._instrument_map[symbol]
            self._watchlist_data[symbol] = {
                "tradingsymbol": symbol,
                "instrument_token": instrument.get('instrument_token'),
                "close_price": instrument.get('ohlc', {}).get('close', 0.0),
                "ltp": instrument.get('last_price', 0.0),
                "change": 0.0,
                "change_pct": 0.0,
            }
            self._rebuild_table()
            self._save_watchlist()
            self.subscribe_tokens_requested.emit([instrument.get('instrument_token')])
            self.watchlist_changed.emit()
            logger.info(f"Added {symbol} to watchlist.")
        else:
            logger.warning(f"Could not add '{symbol}'. It may already exist or is not a valid symbol.")

    def _remove_symbol(self, row: int):
        """Removes a symbol from the watchlist."""
        try:
            symbol_to_remove = self.table.item(row, 0).text()
            if symbol_to_remove in self._watchlist_data:
                del self._watchlist_data[symbol_to_remove]
                self._rebuild_table()
                self._save_watchlist()
                self.watchlist_changed.emit()
                logger.info(f"Removed {symbol_to_remove} from watchlist.")
        except AttributeError:
            logger.error(f"Failed to remove symbol at row {row}.")

    def _rebuild_table(self):
        """Clears and repopulates the entire table from the internal cache."""
        self.table.setRowCount(0)
        self._symbol_to_row.clear()

        sorted_symbols = sorted(self._watchlist_data.keys())
        for symbol in sorted_symbols:
            data = self._watchlist_data[symbol]
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._symbol_to_row[symbol] = row

            # Create items
            self.table.setItem(row, 0, QTableWidgetItem())  # Symbol
            self.table.setItem(row, 1, QTableWidgetItem())  # LTP
            self.table.setItem(row, 2, QTableWidgetItem())  # Chg
            self.table.setItem(row, 3, QTableWidgetItem())  # Chg %
            self.table.setCellWidget(row, 4, self._create_remove_button(row))

            # Populate data
            self._update_row_data(row, data)

    def _update_row_data(self, row: int, data: Dict):
        """Updates the text and color for a single row."""
        self.table.item(row, 0).setText(data['tradingsymbol'])
        self.table.item(row, 1).setText(f"{data.get('ltp', 0.0):.2f}")
        self.table.item(row, 2).setText(f"{data.get('change', 0.0):.2f}")
        self.table.item(row, 3).setText(f"{data.get('change_pct', 0.0):.2f}%")

        profit_color = QColor("#00b894")
        loss_color = QColor("#d63031")
        neutral_color = QColor("#b2bec3")

        change = data.get('change', 0.0)
        color = profit_color if change > 0 else (loss_color if change < 0 else neutral_color)

        for col in range(1, 4):  # LTP, Chg, Chg %
            self.table.item(row, col).setForeground(color)
            self.table.item(row, col).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def _create_remove_button(self, row: int) -> QPushButton:
        """Creates the 'x' button to remove a symbol."""
        remove_btn = QPushButton("✕", objectName="removeButton")
        remove_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        remove_btn.clicked.connect(lambda: self._remove_symbol(row))
        return remove_btn

    def _on_cell_clicked(self, row, column):
        """Handles clicks on a cell to select the symbol for charting."""
        if column != 4:  # Ignore clicks on the remove button column
            try:
                symbol = self.table.item(row, 0).text()
                self.symbol_selected.emit(symbol)
            except AttributeError:
                logger.warning(f"Could not get symbol from clicked row {row}.")

    def _show_context_menu(self, pos: QPoint):
        """Shows a right-click context menu for trading actions."""
        row = self.table.rowAt(pos.y())
        if row < 0: return

        symbol = self.table.item(row, 0).text()
        menu = QMenu(self)

        buy_action = QAction("Buy", self)
        buy_action.triggered.connect(lambda: self._request_trade(symbol, "BUY"))
        menu.addAction(buy_action)

        sell_action = QAction("Sell", self)
        sell_action.triggered.connect(lambda: self._request_trade(symbol, "SELL"))
        menu.addAction(sell_action)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _request_trade(self, symbol: str, transaction_type: str):
        """Emits a signal to open the order dialog."""
        order_details = {
            "tradingsymbol": symbol,
            "transaction_type": transaction_type,
        }
        self.place_order_requested.emit(order_details)

    def _load_watchlist(self):
        """Loads the list of symbols from the JSON file."""
        if os.path.exists(WATCHLIST_FILE):
            try:
                with open(WATCHLIST_FILE, 'r') as f:
                    symbols = json.load(f)
                    for symbol in symbols:
                        # Add symbol to a temporary list; full data added when instrument map is set
                        self._watchlist_data[symbol] = {}
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to load watchlist: {e}")
        self._rebuild_table()

    def _save_watchlist(self):
        """Saves the current list of symbols to the JSON file."""
        try:
            dir_name = os.path.dirname(WATCHLIST_FILE)
            if not os.path.exists(dir_name):
                os.makedirs(dir_name)
            with open(WATCHLIST_FILE, 'w') as f:
                json.dump(list(self._watchlist_data.keys()), f, indent=4)
        except IOError as e:
            logger.error(f"Failed to save watchlist: {e}")

    def get_all_tokens(self) -> List[int]:
        """Returns a list of all instrument tokens currently in the watchlist."""
        return [
            data['instrument_token']
            for data in self._watchlist_data.values()
            if 'instrument_token' in data and data['instrument_token']
        ]

    def _apply_styles(self):
        """Applies a minimalist, TC2000-inspired stylesheet."""
        self.setStyleSheet("""
            QTableWidget {
                background-color: #1c1c2e;
                color: #e0e0e0;
                border: none;
                gridline-style: none;
                font-size: 14px;
            }
            QTableWidget::item {
                padding: 6px 8px;
                border: none; /* No cell borders */
            }
            QTableWidget::item:selected {
                background-color: #3a3a5a;
            }
            #removeButton {
                background-color: transparent;
                color: #4a4a6a;
                border: none;
                font-weight: bold;
                font-size: 16px;
            }
            #removeButton:hover {
                color: #d63031;
            }
        """)

