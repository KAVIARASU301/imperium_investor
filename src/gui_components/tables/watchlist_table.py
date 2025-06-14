from PySide6.QtWidgets import QWidget, QVBoxLayout, QTableWidget, QHeaderView, QTableWidgetItem
from PySide6.QtCore import Signal


class WatchlistTable(QWidget):
    symbol_selected = Signal(int, str)

    # --- MODIFIED: Updated __init__ to accept trader and a name ---
    def __init__(self, trader, name="Watchlist", parent=None):
        super().__init__(parent)
        self.trader = trader  # Store the trader object
        self.name = name
        self.symbols = []
        self._init_ui()
        self._connect_signals()
        self.add_mock_data()  # For demonstration

    def _init_ui(self):
        layout = QVBoxLayout(self)
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels([self.name, "LTP", "Change %"])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.table)

    def _connect_signals(self):
        self.table.cellClicked.connect(self.on_cell_clicked)

    def add_mock_data(self):
        # This should be replaced with real watchlist loading/updating logic
        mock_symbols = ["INFY", "TCS", "HDFCBANK"]
        for symbol in mock_symbols:
            self.add_symbol(symbol)

    def add_symbol(self, symbol):
        if symbol not in self.symbols:
            self.symbols.append(symbol)
            row_position = self.table.rowCount()
            self.table.insertRow(row_position)
            self.table.setItem(row_position, 0, QTableWidgetItem(symbol))
            # You would use the trader object to fetch LTP and calculate change
            self.table.setItem(row_position, 1, QTableWidgetItem("0.00"))  # Placeholder
            self.table.setItem(row_position, 2, QTableWidgetItem("0.00%"))  # Placeholder

    def on_cell_clicked(self, row, column):
        symbol_item = self.table.item(row, 0)
        if symbol_item:
            self.symbol_selected.emit(row, symbol_item.text())