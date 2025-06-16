import logging
from typing import List, Dict, Any
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QTableWidget, QTableWidgetItem, QHeaderView
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor

logger = logging.getLogger(__name__)


class OrderHistoryTable(QTableWidget):
    """
    A styled table widget optimized for displaying a list of historical stock orders.
    """

    def __init__(self):
        super().__init__()
        # Add a "Type" column for BUY/SELL
        self.setColumnCount(7)
        self.setHorizontalHeaderLabels([
            "Timestamp", "Symbol", "Type", "Qty", "Avg. Price", "Status", "Order ID"
        ])
        self._setup_table_styles()

    def _setup_table_styles(self):
        """Configures the table's appearance and behavior."""
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)

        header = self.horizontalHeader()
        header.setStretchLastSection(False)
        # Set resize modes for columns for a clean layout
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive) # Timestamp
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)      # Symbol
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)      # Order ID

    def update_orders(self, orders: List[Dict]):
        """Populates the table with a list of order dictionaries."""
        self.setRowCount(0)
        # Sort orders by timestamp descending to show the latest first
        sorted_orders = sorted(orders, key=lambda x: x.get("order_timestamp", ""), reverse=True)
        for order in sorted_orders:
            row_position = self.rowCount()
            self.insertRow(row_position)
            self._populate_row(row_position, order)

    def _populate_row(self, row: int, order: Dict[str, Any]):
        """Populates a single row with data from an order dictionary."""
        # Column 0: Timestamp
        timestamp = order.get("order_timestamp", "")
        self.setItem(row, 0, QTableWidgetItem(timestamp))

        # Column 1: Symbol
        self.setItem(row, 1, QTableWidgetItem(order.get("tradingsymbol", "")))

        # Column 2: Transaction Type (BUY/SELL)
        trans_type = order.get("transaction_type", "N/A").upper()
        type_item = QTableWidgetItem(trans_type)
        if "BUY" in trans_type:
            type_item.setForeground(QColor("#00b894"))  # Green for BUY
        elif "SELL" in trans_type:
            type_item.setForeground(QColor("#d63031"))  # Red for SELL
        self.setItem(row, 2, type_item)

        # Column 3: Quantity
        self.setItem(row, 3, QTableWidgetItem(str(order.get("quantity", 0))))

        # Column 4: Average Price
        price = order.get('average_price', 0.0)
        self.setItem(row, 4, QTableWidgetItem(f"{price:.2f}"))

        # Column 5: Status
        status = order.get("status", "").upper()
        status_item = QTableWidgetItem(status)
        if "COMPLETE" in status:
            status_item.setForeground(QColor("#0984e3")) # Blue for COMPLETE
        elif "CANCELLED" in status:
            status_item.setForeground(QColor("#b2bec3")) # Grey for CANCELLED
        else: # OPEN, PENDING, etc.
            status_item.setForeground(QColor("#fdcb6e")) # Yellow for others
        self.setItem(row, 5, status_item)

        # Column 6: Order ID
        self.setItem(row, 6, QTableWidgetItem(order.get("order_id", "")))

        # Center align all text items for a clean look
        for col in range(self.columnCount()):
            item = self.item(row, col)
            if item:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)


class OrderHistoryDialog(QDialog):
    """
    A modern, frameless dialog to display historical order data for the session.
    """
    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos = None
        self._setup_window()
        self._setup_ui()
        self._apply_styles()

    def _setup_window(self):
        """Initializes window properties."""
        self.setWindowTitle("Order History")
        self.setMinimumSize(850, 600)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def _setup_ui(self):
        """Builds the main layout and widgets."""
        container = QWidget(self)
        container.setObjectName("mainContainer")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(20, 10, 20, 20)
        container_layout.setSpacing(15)

        # Allow dragging the window
        container.mousePressEvent = self.mousePressEvent
        container.mouseMoveEvent = self.mouseMoveEvent
        container.mouseReleaseEvent = self.mouseReleaseEvent

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        container_layout.addLayout(self._create_header())
        self.orders_table = OrderHistoryTable()
        container_layout.addWidget(self.orders_table, 1) # Give table stretch factor
        container_layout.addLayout(self._create_footer())

    def _create_header(self) -> QHBoxLayout:
        """Creates the dialog's header with title and close button."""
        header_layout = QHBoxLayout()
        title_group_layout = QVBoxLayout()
        title_group_layout.setSpacing(2)

        title = QLabel("Order History")
        title.setObjectName("dialogTitle")

        note_label = QLabel("Displays all completed and cancelled orders for the session.")
        note_label.setObjectName("noteLabel")

        title_group_layout.addWidget(title)
        title_group_layout.addWidget(note_label)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.clicked.connect(self.close)

        header_layout.addLayout(title_group_layout)
        header_layout.addStretch()
        header_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignTop)
        return header_layout

    def _create_footer(self) -> QHBoxLayout:
        """Creates the dialog's footer with trade count and refresh button."""
        footer_layout = QHBoxLayout()
        footer_layout.setContentsMargins(0, 5, 0, 0)

        self.trade_count_label = QLabel("0 TRADES")
        self.trade_count_label.setObjectName("footerLabel")

        refresh_button = QPushButton("REFRESH")
        refresh_button.setObjectName("secondaryButton")
        refresh_button.clicked.connect(self.refresh_requested.emit)

        footer_layout.addWidget(self.trade_count_label)
        footer_layout.addStretch()
        footer_layout.addWidget(refresh_button)
        return footer_layout

    def update_orders(self, orders: List[Dict]):
        """Public method to update the dialog with a new list of orders."""
        self.orders_table.update_orders(orders)
        count = len(orders)
        self.trade_count_label.setText(f"{count} ORDER{'S' if count != 1 else ''} RECORDED")

    def _apply_styles(self):
        """Applies a modern, dark theme stylesheet to the dialog."""
        self.setStyleSheet("""
            #mainContainer {
                background-color: #1c1c2e;
                border: 1px solid #3a3a5a;
                border-radius: 12px;
                font-family: "Segoe UI", sans-serif;
            }
            #dialogTitle { color: #e0e0e0; font-size: 18px; font-weight: 600; }
            #noteLabel { color: #8a8a9e; font-size: 12px; }
            #footerLabel { color: #8a8a9e; font-size: 11px; font-weight: bold; }
            #closeButton {
                background-color: transparent; border: none; color: #8a8a9e;
                font-size: 16px; font-weight: bold;
            }
            #closeButton:hover { color: #d63031; }

            QTableWidget {
                background-color: transparent;
                border: 1px solid #2a2a4a;
                border-radius: 6px;
                gridline-color: #2a2a4a;
                color: #b2bec3;
            }
            QTableWidget::item {
                border-bottom: 1px solid #2a2a4a;
                padding: 8px;
            }
            QTableWidget::item:selected {
                background-color: #3a3a5a;
                color: #ffffff;
            }

            QHeaderView::section {
                background-color: #1c1c2e;
                color: #8a8a9e;
                padding: 10px 5px;
                border: none;
                border-bottom: 1px solid #3a3a5a;
                font-weight: bold;
                font-size: 11px;
                text-transform: uppercase;
            }
            #secondaryButton {
                background-color: #3a3a5a; color: #e0e0e0;
                font-size: 12px; font-weight: bold;
                border-radius: 6px; padding: 9px 18px; border: none;
            }
            #secondaryButton:hover { background-color: #4a4a6a; }
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

