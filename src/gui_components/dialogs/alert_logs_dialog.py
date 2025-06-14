from PySide6.QtWidgets import (QDialog, QVBoxLayout, QTableWidget,
                               QTableWidgetItem, QHeaderView)
from PySide6.QtGui import QColor, QFont
from PySide6.QtCore import Qt
from datetime import datetime


class AlertLogsDialog(QDialog):
    """A dialog to display the history of triggered alerts."""

    def __init__(self, triggered_alerts, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Triggered Alert History")
        self.setMinimumSize(800, 500)

        self.layout = QVBoxLayout(self)
        self.triggered_table = QTableWidget(0, 5)
        self.triggered_table.setHorizontalHeaderLabels(["Time", "Date", "Symbol", "Condition", "Trigger Price", "Note"])
        self.triggered_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.triggered_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.triggered_table.setAlternatingRowColors(True)

        self.layout.addWidget(self.triggered_table)
        self.last_trigger_date = ""
        self._populate_table(triggered_alerts)

    def _populate_table(self, alerts_history):
        """Fills the table with triggered alert data."""
        self.triggered_table.setRowCount(0)

        # Group by date, assuming newest alerts come last
        for alert in reversed(alerts_history):
            try:
                dt = datetime.strptime(alert['time'], "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                continue

            date_str, weekday_str, time_str = dt.strftime("%Y-%m-%d"), dt.strftime("%A"), dt.strftime("%H:%M:%S")

            if self.last_trigger_date != date_str:
                self._add_date_header(date_str, weekday_str)
                self.last_trigger_date = date_str

            self._add_alert_row(time_str, date_str, alert)

    def _add_date_header(self, date_str, weekday_str):
        """Adds a non-selectable date header row to the table."""
        header_row = self.triggered_table.rowCount()
        self.triggered_table.insertRow(header_row)
        header_item = QTableWidgetItem(f"{date_str} ({weekday_str})")
        header_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        header_item.setBackground(QColor("#36454F"))
        header_item.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.triggered_table.setItem(header_row, 0, header_item)
        self.triggered_table.setSpan(header_row, 0, 1, 6)

    def _add_alert_row(self, time_str, date_str, alert):
        """Adds a row with alert details to the table."""
        row = self.triggered_table.rowCount()
        self.triggered_table.insertRow(row)
        self.triggered_table.setItem(row, 0, QTableWidgetItem(time_str))
        self.triggered_table.setItem(row, 1, QTableWidgetItem(date_str))
        self.triggered_table.setItem(row, 2, QTableWidgetItem(alert['symbol']))
        self.triggered_table.setItem(row, 3, QTableWidgetItem(alert.get('condition', 'N/A')))
        self.triggered_table.setItem(row, 4, QTableWidgetItem(f"{alert['price']:.2f}"))
        self.triggered_table.setItem(row, 5, QTableWidgetItem(alert.get('note', '')))