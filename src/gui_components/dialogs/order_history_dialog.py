# src/gui_components/dialogs/order_history_dialog.py
import logging
from typing import List, Dict
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QTableWidget, QTableWidgetItem, QHeaderView
)
from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import QColor, QFont
from datetime import datetime

logger = logging.getLogger(__name__)


class OrderHistoryTable(QTableWidget):
    """A premium, styled table for displaying order history."""

    def __init__(self):
        super().__init__()
        # Set column count to 6 to remove the "Type" column
        self.setColumnCount(6)
        self.setHorizontalHeaderLabels([
            "Timestamp", "Symbol", "Qty",
            "Avg. Price", "Status", "Order ID"
        ])
        self._setup_table_styles()

    def _setup_table_styles(self):
        """Configures the table's appearance and behavior."""
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setSelectionMode(QTableWidget.SingleSelection)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)

        header = self.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Symbol stretches
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)  # Order ID stretches

    def update_orders(self, orders: List[Dict]):
        """Populates the table with a list of order dictionaries."""
        self.setRowCount(0)
        for order in orders:
            row_position = self.rowCount()
            self.insertRow(row_position)
            self._populate_row(row_position, order)

    def _populate_row(self, row: int, order: Dict):
        """Populates a single row with order data, adjusted for column removal."""
        self.setItem(row, 0, QTableWidgetItem(order.get("timestamp", "")))
        self.setItem(row, 1, QTableWidgetItem(order.get("tradingsymbol", "")))

        # Column 2 is now "Qty"
        self.setItem(row, 2, QTableWidgetItem(str(order.get("quantity", 0))))

        # Column 3 is now "Avg. Price"
        price = order.get('average_price', 0.0)
        self.setItem(row, 3, QTableWidgetItem(f"{price:.2f}"))

        # Column 4 is now "Status"
        status_item = QTableWidgetItem(order.get("status", "").upper())
        status_color = "#29C7C9" if "COMPLETE" in status_item.text() else "#F39C12"
        status_item.setForeground(QColor(status_color))
        self.setItem(row, 4, status_item)

        # Column 5 is now "Order ID"
        self.setItem(row, 5, QTableWidgetItem(order.get("order_id", "")))

        # Center align all items for a cleaner look
        for col in range(self.columnCount()):
            item = self.item(row, col)
            if item:
                item.setTextAlignment(Qt.AlignCenter)


class OrderHistoryDialog(QDialog):
    """A premium dialog to display historical order data with a modern UI."""
    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos = None
        self._setup_window()
        self._setup_ui()
        self._apply_styles()

    def _setup_window(self):
        self.setWindowTitle("Order History")
        self.setMinimumSize(800, 600)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def _setup_ui(self):
        container = QWidget(self)
        container.setObjectName("mainContainer")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(20, 10, 20, 20)
        container_layout.setSpacing(15)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        container_layout.addLayout(self._create_header())
        self.orders_table = OrderHistoryTable()
        container_layout.addWidget(self.orders_table, 1)
        container_layout.addLayout(self._create_footer())

    def _create_header(self):
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)

        # Create a vertical layout for the title and the new note
        title_group_layout = QVBoxLayout()
        title_group_layout.setSpacing(2)

        title = QLabel("Order History")
        title.setObjectName("dialogTitle")

        # Add the new informational note
        note_label = QLabel("Showing completed trades only. For open trades, see Active Positions.")
        note_label.setObjectName("noteLabel")

        title_group_layout.addWidget(title)
        title_group_layout.addWidget(note_label)

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeButton")
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.clicked.connect(self.close)

        header_layout.addLayout(title_group_layout)
        header_layout.addStretch()
        header_layout.addWidget(self.close_btn)
        return header_layout

    def _create_footer(self):
        footer_layout = QHBoxLayout()
        footer_layout.setContentsMargins(0, 5, 0, 0)

        self.trade_count_label = QLabel("0 TRADES")
        self.trade_count_label.setObjectName("footerLabel")

        self.refresh_button = QPushButton("REFRESH")
        self.refresh_button.setObjectName("secondaryButton")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)

        footer_layout.addWidget(self.trade_count_label)
        footer_layout.addStretch()
        footer_layout.addWidget(self.refresh_button)
        return footer_layout

    def update_orders(self, orders: List[Dict]):
        """Public method to update the dialog with a list of orders."""
        self.orders_table.update_orders(orders)
        count = len(orders)
        self.trade_count_label.setText(f"{count} TRADE{'S' if count != 1 else ''} RECORDED")

    def _apply_styles(self):
        """Applies a premium, modern dark theme."""
        self.setStyleSheet("""
            #mainContainer {
                background-color: #161A25;
                border: 1px solid #3A4458;
                border-radius: 12px;
                font-family: "Segoe UI", sans-serif;
            }
            #dialogTitle {
                color: #FFFFFF;
                font-size: 16px;
                font-weight: bold;
            }
            #noteLabel {
                color: #8A9BA8;
                font-size: 11px;
                font-style: italic;
            }
            #footerLabel {
                color: #8A9BA8;
                font-size: 11px;
                font-weight: bold;
            }
            #closeButton {
                background-color: transparent; border: none; color: #8A9BA8;
                font-size: 16px; font-weight: bold;
            }
            #closeButton:hover { background-color: #3A4458; color: #E74C3C; }

            QTableWidget {
                background-color: transparent;
                border: none;
                gridline-color: #2A3140; /* Sets color for grid lines */
                color: #A9B1C3; 
            }
            QTableWidget::item {
                border-bottom: 1px solid #2A3140; 
                padding: 5px;
            }
            QTableWidget::item:alternate {
                background-color: #1E222F;
            }
            QTableWidget::item:selected {
                /* Style for the selected row */
                background-color: #3A4458;
                color: #FFFFFF;
            }

            QHeaderView::section {
                background-color: transparent; color: #8A9BA8;
                padding: 10px 5px; border: none;
                border-bottom: 2px solid #2A3140;
                font-weight: bold; font-size: 11px; text-transform: uppercase;
            }
            #secondaryButton {
                background-color: #212635; color: #A9B1C3; border: 1px solid #3A4458;
                font-size: 12px; font-weight: bold;
                border-radius: 6px; padding: 8px 16px;
            }
            #secondaryButton:hover { background-color: #3A4458; }
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()