
from PySide6.QtWidgets import QTableWidget, QHeaderView, QTableWidgetItem

class WatchlistTable(QTableWidget):
    """A table to display a watchlist of stocks, using PySide6."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(["Symbol", "LTP", "% Change"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # Placeholder data
        self._add_placeholder_data()

    def _add_placeholder_data(self):
        """Adds some dummy data for visualization."""
        for i in range(20):
            row_position = self.rowCount()
            self.insertRow(row_position)
            self.setItem(row_position, 0, QTableWidgetItem(f"STOCK{i+1}"))
            self.setItem(row_position, 1, QTableWidgetItem(f"{50 + i*2}"))
            self.setItem(row_position, 2, QTableWidgetItem(f"{i*0.1:.2f}%"))
