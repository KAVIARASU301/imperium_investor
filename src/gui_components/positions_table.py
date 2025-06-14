import logging
from typing import List, Dict
from PySide6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QPushButton, QLabel, QVBoxLayout,
    QWidget, QHeaderView, QFrame, QHBoxLayout
)
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor, QCursor
from src.utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class PositionsTable(QWidget):
    """
    A widget to display open swing trading positions (day and holdings).
    It updates P&L in real-time based on WebSocket ticks.
    """
    exit_requested = Signal(dict)
    subscribe_symbols_requested = Signal(list)

    def __init__(self, trader, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self.trader = trader
        self.config_manager = config_manager
        self.table_name = "positions_table"

        # --- REFACTORED: Caches for positions and instrument tokens ---
        self._positions_cache: Dict[str, Dict] = {}
        self._token_map: Dict[int, str] = {}  # Maps instrument_token to symbol
        self._row_map: Dict[str, int] = {}  # Maps symbol to table row

        self._setup_ui()
        self._apply_styles()
        self.load_initial_positions()

    def _setup_ui(self):
        """Sets up the main UI layout and table."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.table = QTableWidget()
        self._configure_table()
        main_layout.addWidget(self.table, 1)

        main_layout.addWidget(self._create_footer())

    def _configure_table(self):
        """Configures the table headers and properties."""
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["Symbol", "Qty", "Avg", "LTP", "P&L", ""])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in [1, 2, 3, 4]:
            header.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        self.table.setColumnWidth(5, 45)

        self._load_column_widths()
        header.sectionResized.connect(self._save_column_widths)

    def _create_footer(self) -> QFrame:
        """Creates the footer widget with the total P&L label."""
        footer_frame = QFrame()
        footer_frame.setObjectName("footerFrame")
        footer_layout = QHBoxLayout(footer_frame)
        footer_layout.setContentsMargins(12, 0, 12, 0)
        footer_frame.setFixedHeight(30)

        footer_label = QLabel("TOTAL P&L")
        footer_label.setObjectName("footerLabel")

        self.total_pnl_label = QLabel("₹0.00")
        self.total_pnl_label.setObjectName("totalPnlValue")

        footer_layout.addWidget(footer_label)
        footer_layout.addStretch()
        footer_layout.addWidget(self.total_pnl_label)
        return footer_frame

    def load_initial_positions(self):
        """
        --- REFACTORED: Fetches both day positions and long-term holdings ---
        This method should be called once at startup.
        """
        try:
            day_positions = self.trader.positions().get('net', [])
            holdings = self.trader.holdings()

            # Combine and normalize holdings to match position structure
            normalized_holdings = []
            for h in holdings:
                # We only care about T1 and holdings qty, not stocks bought today
                if h.get('quantity', 0) > 0:
                    normalized_holdings.append({
                        'tradingsymbol': h['tradingsymbol'],
                        'instrument_token': h['instrument_token'],
                        'quantity': h['quantity'],
                        'average_price': h['average_price'],
                        'last_price': h['last_price'],
                        'pnl': h.get('pnl', 0.0),  # Kite holdings API provides PnL
                    })

            # Combine day positions and holdings, ensuring no duplicates
            combined_positions_map = {p['tradingsymbol']: p for p in day_positions}
            for h in normalized_holdings:
                if h['tradingsymbol'] not in combined_positions_map:
                    combined_positions_map[h['tradingsymbol']] = h

            self._update_table_from_data(list(combined_positions_map.values()))

            # Request WebSocket subscriptions for all open positions
            tokens_to_subscribe = [p['instrument_token'] for p in self._positions_cache.values()]
            self.subscribe_symbols_requested.emit(tokens_to_subscribe)

        except Exception as e:
            logger.error(f"Failed to fetch initial positions and holdings: {e}")

    @Slot(list)
    def on_tick(self, ticks: List[Dict]):
        """
        --- REFACTORED: Slot to handle live price updates from WebSocket ---
        Updates LTP and P&L for the relevant rows.
        """
        total_pnl = 0
        for symbol, pos_data in self._positions_cache.items():
            # Update P&L for all positions, even if no new tick
            total_pnl += pos_data.get('pnl', 0.0)

        for tick in ticks:
            token = tick.get('instrument_token')
            ltp = tick.get('last_price')

            if token and ltp and token in self._token_map:
                symbol = self._token_map[token]
                if symbol in self._positions_cache:
                    # Update cache
                    pos_data = self._positions_cache[symbol]
                    pos_data['last_price'] = ltp
                    pnl = (ltp - pos_data['average_price']) * pos_data['quantity']

                    # Deduct old PNL and add new PNL to total
                    total_pnl -= pos_data.get('pnl', 0.0)
                    total_pnl += pnl
                    pos_data['pnl'] = pnl

                    # Update table UI
                    row = self._row_map[symbol]
                    self._update_row_pnl(row, ltp, pnl)

        self._update_total_pnl(total_pnl)

    def _update_table_from_data(self, positions: List[Dict]):
        """Populates or updates the table with new position data."""
        self._positions_cache = {p['tradingsymbol']: p for p in positions if p.get('quantity', 0) != 0}
        self._token_map = {p['instrument_token']: p['tradingsymbol'] for p in self._positions_cache.values()}
        self._row_map.clear()
        self.table.setRowCount(0)  # Clear the table completely

        total_pnl = 0
        for symbol, pos_data in self._positions_cache.items():
            total_pnl += pos_data.get('pnl', 0)
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._populate_row(row, pos_data)
            self._row_map[symbol] = row

        self._update_total_pnl(total_pnl)

    def _populate_row(self, row: int, pos_data: Dict):
        """Fills a single row with data."""
        symbol_item = QTableWidgetItem(pos_data.get('tradingsymbol', ''))
        qty_item = QTableWidgetItem(str(pos_data.get('quantity', 0)))
        avg_item = QTableWidgetItem(f"{pos_data.get('average_price', 0.0):.2f}")
        ltp_item = QTableWidgetItem(f"{pos_data.get('last_price', 0.0):.2f}")
        pnl = pos_data.get('pnl', 0.0)
        pnl_item = QTableWidgetItem(f"{pnl:,.2f}")

        for item in [qty_item, avg_item, ltp_item, pnl_item]:
            item.setTextAlignment(Qt.AlignCenter)

        pnl_color = QColor("#29C7C9") if pnl >= 0 else QColor("#F85149")
        pnl_item.setForeground(pnl_color)

        self.table.setItem(row, 0, symbol_item)
        self.table.setItem(row, 1, qty_item)
        self.table.setItem(row, 2, avg_item)
        self.table.setItem(row, 3, ltp_item)
        self.table.setItem(row, 4, pnl_item)
        self.table.setCellWidget(row, 5, self._create_exit_button())

    def _update_row_pnl(self, row: int, ltp: float, pnl: float):
        """Updates only the LTP and P&L cells for efficiency."""
        self.table.item(row, 3).setText(f"{ltp:.2f}")
        pnl_item = self.table.item(row, 4)
        pnl_item.setText(f"{pnl:,.2f}")
        pnl_color = QColor("#29C7C9") if pnl >= 0 else QColor("#F85149")
        pnl_item.setForeground(pnl_color)

    def _on_exit_clicked(self):
        """Handles the click of an exit button."""
        button = self.sender()
        if not button: return

        # Find which row the clicked button belongs to
        for row in range(self.table.rowCount()):
            cell_widget = self.table.cellWidget(row, 5)
            if cell_widget and button in cell_widget.findChildren(QPushButton):
                try:
                    symbol = self.table.item(row, 0).text()
                    if symbol in self._positions_cache:
                        self.exit_requested.emit(self._positions_cache[symbol])
                except AttributeError:
                    logger.error(f"Could not find symbol for exit button in row {row}")
                return

    def _create_exit_button(self) -> QWidget:
        """Creates the exit button widget for the table."""
        container_widget = QWidget()
        layout = QHBoxLayout(container_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignCenter)
        exit_btn = QPushButton("✕")
        exit_btn.setObjectName("exitButton")
        exit_btn.setFixedSize(24, 24)
        exit_btn.setCursor(QCursor(Qt.PointingHandCursor))
        exit_btn.clicked.connect(self._on_exit_clicked)
        layout.addWidget(exit_btn)
        return container_widget

    def _update_total_pnl(self, total_pnl: float):
        """Updates the total P&L label in the footer."""
        color = "#29C7C9" if total_pnl >= 0 else "#F85149"
        self.total_pnl_label.setText(f"₹{total_pnl:,.2f}")
        self.total_pnl_label.setStyleSheet(f"color: {color};")

    def _apply_styles(self):
        """Applies the CSS stylesheet to the widget."""
        self.setStyleSheet("""
            QWidget { background-color: #161A25; color: #E0E0E0; font-family: "Segoe UI"; }
            QTableWidget { border: none; gridline-color: #2A3140; font-size: 13px; }
            QHeaderView::section { background-color: #212635; color: #A9B1C3; padding: 6px; border: none; border-bottom: 1px solid #3A4458; font-weight: bold; font-size: 11px; text-transform: uppercase; }
            QTableWidget::item { padding: 8px 4px; border-bottom: 1px solid #2A3140; }
            #exitButton { background-color: #3e232d; color: #ff3860; border-radius: 12px; font-weight: bold; font-size: 12px; border: 1px solid #555; }
            #exitButton:hover { background-color: #F85149; color: #FFFFFF; border: 1px solid #F85149; }
            #footerFrame { background-color: #212635; border-top: 1px solid #2A3140; }
            #footerLabel { color: #A9B1C3; font-size: 11px; font-weight: bold; text-transform: uppercase; }
            #totalPnlValue { font-size: 16px; font-weight: 600; }
        """)

    def _load_column_widths(self):
        """Loads saved column widths from the config file."""
        states = self.config_manager.load_table_column_states(self.table_name)
        if states and 'column_widths' in states:
            widths = states['column_widths']
            if len(widths) == self.table.columnCount():
                for i, width in enumerate(widths): self.table.setColumnWidth(i, width)

    def _save_column_widths(self, logical_index: int, old_size: int, new_size: int):
        """Saves column widths to the config file when they are resized."""
        if not self.isVisible(): return
        widths = [self.table.columnWidth(i) for i in range(self.table.columnCount())]
        self.config_manager.save_table_column_states(self.table_name, {'column_widths': widths})
