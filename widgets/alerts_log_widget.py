from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView
from PySide6.QtCore import QDateTime

class AlertsLogWidget(QTableWidget):
    """A widget to log and display alerts, using PySide6."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(2)
        self.setHorizontalHeaderLabels(["Time", "Alert"])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

    def add_alert(self, message: str):
        """Adds a new alert to the log."""
        row_position = self.rowCount()
        self.insertRow(row_position)
        time_str = QDateTime.currentDateTime().toString("HH:mm:ss")
        self.setItem(row_position, 0, QTableWidgetItem(time_str))
        self.setItem(row_position, 1, QTableWidgetItem(message))
        self.scrollToBottom()