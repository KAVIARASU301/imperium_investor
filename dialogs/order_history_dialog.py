import logging
from typing import List, Dict, Any
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame
)
from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QColor, QMouseEvent

logger = logging.getLogger(__name__)


class OrderHistoryTable(QTableWidget):
    """
    A styled table widget optimized for displaying a list of historical stock orders.
    This class is now mainly responsible for table structure and data population,
    with styling handled by the parent dialog.
    """

    def __init__(self):
        super().__init__()
        # Add a "Type" column for BUY/SELL
        self.setColumnCount(7)
        self.setHorizontalHeaderLabels([
            "Timestamp", "Symbol", "Type", "Qty", "Avg. Price", "Status", "Order ID"
        ])
        self._setup_table_behavior()

    def _setup_table_behavior(self):
        """Configures the table's appearance and behavior."""
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False) # Hide grid lines as per theme

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
        # Set object name for styling based on type in CSS
        type_item.setObjectName(f"{trans_type.lower()}Tag")
        self.setItem(row, 2, type_item)

        # Column 3: Quantity
        self.setItem(row, 3, QTableWidgetItem(str(order.get("quantity", 0))))

        # Column 4: Average Price
        price = order.get('average_price', 0.0)
        self.setItem(row, 4, QTableWidgetItem(f"{price:.2f}"))

        # Column 5: Status
        status = order.get("status", "").upper()
        status_item = QTableWidgetItem(status)
        # Set object name for styling based on status in CSS
        status_item.setObjectName(f"{status.lower()}Status")
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
    A modern, frameless dialog to display historical order data for the session,
    matching the solid black theme of the swing trading app.
    """
    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos = None  # For window dragging
        self._setup_window()
        self._setup_ui()
        self._apply_styles()

    def _setup_window(self):
        """Initializes window properties for a frameless, translucent dialog."""
        self.setWindowTitle("Order History")
        self.setMinimumSize(850, 600)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def _setup_ui(self):
        """Builds the main layout and widgets of the dialog."""
        container = QWidget(self)
        container.setObjectName("mainContainer")

        # Enable dragging the window from anywhere in the container
        container.mousePressEvent = self._handle_mouse_press
        container.mouseMoveEvent = self._handle_mouse_move
        container.mouseReleaseEvent = self._handle_mouse_release

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(20, 15, 20, 20)
        container_layout.setSpacing(15)

        container_layout.addLayout(self._create_header())
        self.orders_table = OrderHistoryTable()
        container_layout.addWidget(self.orders_table, 1) # Give table stretch factor
        container_layout.addLayout(self._create_footer())

    def _create_header(self) -> QHBoxLayout:
        """Creates the dialog's header with a title, a brief note, and a close button."""
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

        self.trade_count_label = QLabel("0 ORDERS RECORDED")
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
        """Applies a modern, dark theme stylesheet to the dialog and its components."""
        self.setStyleSheet("""
            QWidget#mainContainer {
                background-color: #0a0a0a; /* Deep black background */
                border: 1px solid #202020; /* Subtle dark border */
                border-radius: 8px; /* Soft edges */
                font-family: "Segoe UI", sans-serif;
            }

            QLabel#dialogTitle {
                color: #ffffff;
                font-size: 18px;
                font-weight: 600;
            }

            QLabel#noteLabel {
                color: #8a8a9e;
                font-size: 12px;
            }

            QLabel#footerLabel {
                color: #8a8a9e;
                font-size: 11px;
                font-weight: bold;
            }

            QPushButton#closeButton {
                background-color: transparent;
                border: none;
                color: #8a8a9e;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton#closeButton:hover {
                color: #d63031; /* Red on hover for close button */
            }

            QTableWidget {
                background-color: #0d0d0d; /* Very dark table background */
                border: 1px solid #202020; /* Subtle dark border for the table */
                gridline-color: #151515; /* Almost invisible grid lines */
                font-size: 12px;
                color: #e0e0e0; /* Light text for table content */
                selection-background-color: rgba(74, 122, 191, 0.2); /* Softer blue selection with transparency */
                selection-color: #ffffff;
                border-radius: 4px; /* Slight rounding for the table */
            }

            QTableWidget::item {
                padding: 6px 8px; /* Consistent padding for table items */
                border-bottom: 1px solid #1a1a1a; /* Thin row separator */
            }
            QTableWidget::item:selected {
                background-color: rgba(74, 122, 191, 0.2); /* Softer blue selection with transparency */
                color: #ffffff;
                font-weight: 600;
            }
            QTableWidget::item:alternate {
                background-color: #121212; /* Very dark alternate row */
            }

            QHeaderView::section {
                background-color: #1a1a1a; /* Header background */
                color: #a0c0ff; /* Header text color */
                padding: 8px 10px; /* Padding for header sections */
                border: none;
                border-bottom: 1px solid #303030; /* Clear header bottom border */
                border-right: 1px solid #101010; /* Dark vertical header separators */
                font-weight: 600;
                font-size: 11px;
                text-transform: uppercase;
            }
            QHeaderView::section:last {
                border-right: none;
            }
            QHeaderView::section:hover {
                background-color: #2a2a2a; /* Subtle hover for headers */
            }

            /* Transaction Type Tag Colors */
            QTableWidgetItem[objectName="buyTag"] {
                color: #00b894; /* Green for BUY */
            }
            QTableWidgetItem[objectName="sellTag"] {
                color: #d63031; /* Red for SELL */
            }

            /* Status Colors */
            QTableWidgetItem[objectName="completeStatus"] {
                color: #0984e3; /* Blue for COMPLETE */
            }
            QTableWidgetItem[objectName="cancelledStatus"] {
                color: #b2bec3; /* Grey for CANCELLED */
            }
            QTableWidgetItem[objectName="openStatus"],
            QTableWidgetItem[objectName="pending_executionStatus"],
            QTableWidgetItem[objectName="trigger pendingStatus"] {
                color: #fdcb6e; /* Yellow for OPEN/PENDING */
            }

            QPushButton {
                font-weight: bold;
                border-radius: 6px;
                padding: 9px 18px;
                border: none;
                font-size: 12px;
            }
            QPushButton#secondaryButton {
                background-color: #3a3a5a;
                color: #e0e0e0;
            }
            QPushButton#secondaryButton:hover {
                background-color: #4a4a6a;
            }
        """)

    # --- Window Dragging Methods ---
    def _handle_mouse_press(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _handle_mouse_move(self, event: QMouseEvent):
        if event.buttons() & Qt.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def _handle_mouse_release(self, event: QMouseEvent):
        self._drag_pos = None
        event.accept()