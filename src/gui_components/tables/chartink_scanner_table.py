from PySide6.QtWidgets import QTableWidget, QHeaderView, QTableWidgetItem

class ChartinkScannerTable(QTableWidget):
    """A table to display Chartink scanner results, using PySide6."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(4)  # Example column count
        self.setHorizontalHeaderLabels(["Symbol", "Price", "Volume", "Scan"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # Placeholder data
        self._add_placeholder_data()

    def _add_placeholder_data(self):
        """Adds some dummy data for visualization."""
        for i in range(15):
            row_position = self.rowCount()
            self.insertRow(row_position)
            self.setItem(row_position, 0, QTableWidgetItem(f"SYMBOL{i+1}"))
            self.setItem(row_position, 1, QTableWidgetItem(f"{100 + i*5}"))
            self.setItem(row_position, 2, QTableWidgetItem(f"{10000 * i}"))
            self.setItem(row_position, 3, QTableWidgetItem("Bullish Scan"))