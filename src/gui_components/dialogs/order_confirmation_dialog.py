import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget, QFrame
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent, QShowEvent
from typing import Dict, Any

logger = logging.getLogger(__name__)


class OrderConfirmationDialog(QDialog):
    """
    A sleek, modern dialog for confirming a stock trade.
    It displays all relevant order details like symbol, quantity, price,
    and estimated cost in a clear and concise manner.
    """
    # Signal to request a refresh of the LTP before confirming
    refresh_requested = Signal(str)

    def __init__(self, parent: QWidget, order_details: Dict[str, Any]):
        super().__init__(parent)
        self.order_details = order_details
        self._drag_pos = None

        self._setup_dialog()
        self._setup_ui()
        self._apply_styles()

    def _setup_dialog(self):
        self.setWindowTitle("Confirm Order")
        self.setModal(True)
        self.setMinimumSize(360, 380)
        # Use a frameless window for a custom UI
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def showEvent(self, event: QShowEvent):
        """Overrides the show event to center the dialog on its parent."""
        super().showEvent(event)
        if self.parent():
            parent_geometry = self.parent().geometry()
            self.move(parent_geometry.center() - self.rect().center())

    def _setup_ui(self):
        """Builds the main layout and components of the dialog."""
        container = QWidget(self)
        container.setObjectName("mainContainer")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(20, 15, 20, 20)
        container_layout.setSpacing(15)

        # Allow dragging the window
        container.mousePressEvent = self.mousePressEvent
        container.mouseMoveEvent = self.mouseMoveEvent
        container.mouseReleaseEvent = self.mouseReleaseEvent

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        # Add UI components
        container_layout.addLayout(self._create_title_bar())
        container_layout.addWidget(self._create_instrument_details_widget())

        container_layout.addWidget(QFrame(self, frameShape=QFrame.Shape.HLine, objectName="divider"))

        container_layout.addLayout(self._create_order_summary_layout())

        container_layout.addStretch()

        container_layout.addWidget(self._create_cost_summary_widget())

        container_layout.addSpacing(10)
        container_layout.addLayout(self._create_action_buttons())

    def update_order_details(self, new_order_details: dict):
        """Refreshes the dialog with updated price information."""
        self.order_details = new_order_details
        self._repopulate_ui()
        logger.info("Order confirmation dialog refreshed with latest prices.")

    def _repopulate_ui(self):
        """Updates all labels with new data."""
        self.symbol_label.setText(self.order_details.get('tradingsymbol', 'N/A'))

        transaction_type = self.order_details.get("transaction_type", "BUY").upper()
        self.transaction_type_label.setText(transaction_type)
        self.transaction_type_label.setObjectName(f"{transaction_type.lower()}Tag")

        order_type = self.order_details.get("order_type", "MARKET").upper()
        price = self.order_details.get('price', 0.0)
        ltp = self.order_details.get('ltp', 0.0)

        self.price_value_label.setText(f"₹ {price if order_type == 'LIMIT' else ltp:,.2f}")
        self.order_type_value_label.setText(order_type)
        self.quantity_value_label.setText(str(self.order_details.get('quantity', 0)))

        estimated_cost = self.order_details.get('estimated_cost', 0.0)
        self.cost_value_label.setText(f"₹ {estimated_cost:,.2f}")
        self.cost_title_label.setText("ESTIMATED COST" if transaction_type == "BUY" else "ESTIMATED CREDIT")

    def _create_title_bar(self) -> QHBoxLayout:
        """Creates the custom title bar with a close button."""
        layout = QHBoxLayout()
        title = QLabel("Confirm Order")
        title.setObjectName("dialogTitle")
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.clicked.connect(self.reject)
        layout.addWidget(title)
        layout.addStretch()
        layout.addWidget(close_btn)
        return layout

    def _create_instrument_details_widget(self) -> QWidget:
        """Creates the top section with the stock symbol and transaction type."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self.symbol_label = QLabel()
        self.symbol_label.setObjectName("symbolLabel")

        self.transaction_type_label = QLabel()

        layout.addWidget(self.symbol_label)
        layout.addWidget(self.transaction_type_label, alignment=Qt.AlignmentFlag.AlignLeft)
        return widget

    def _create_order_summary_layout(self) -> QHBoxLayout:
        """Creates the section displaying Quantity, Price, and Order Type."""
        layout = QHBoxLayout()
        layout.setSpacing(20)

        self.quantity_value_label = QLabel()
        self.price_value_label = QLabel()
        self.order_type_value_label = QLabel()

        layout.addWidget(self._create_summary_item("QUANTITY", self.quantity_value_label))
        layout.addWidget(self._create_summary_item("PRICE", self.price_value_label))
        layout.addWidget(self._create_summary_item("ORDER TYPE", self.order_type_value_label))
        return layout

    def _create_summary_item(self, title: str, value_label: QLabel) -> QWidget:
        """Helper to create a single item (e.g., Quantity) for the summary."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        title_label = QLabel(title)
        title_label.setObjectName("summaryTitle")

        value_label.setObjectName("summaryValue")

        layout.addWidget(title_label)
        layout.addWidget(value_label)
        layout.addStretch()
        return widget

    def _create_cost_summary_widget(self) -> QWidget:
        """Creates the final estimated cost section."""
        widget = QWidget(objectName="summaryBox")
        layout = QVBoxLayout(widget)
        layout.setSpacing(0)

        self.cost_value_label = QLabel(objectName="costValue")
        self.cost_title_label = QLabel(objectName="costTitle")

        layout.addWidget(self.cost_value_label, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.cost_title_label, alignment=Qt.AlignmentFlag.AlignCenter)
        return widget

    def _create_action_buttons(self) -> QHBoxLayout:
        """Creates the Confirm, Cancel, and Refresh buttons."""
        layout = QHBoxLayout()
        layout.setSpacing(10)

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.clicked.connect(self.reject)

        refresh_btn = QPushButton("REFRESH")
        refresh_btn.setObjectName("secondaryButton")
        refresh_btn.clicked.connect(lambda: self.refresh_requested.emit(self.order_details.get('tradingsymbol')))

        confirm_btn = QPushButton("CONFIRM")
        confirm_btn.setObjectName("primaryButton")
        confirm_btn.clicked.connect(self.accept)

        layout.addWidget(cancel_btn)
        layout.addWidget(refresh_btn)
        layout.addStretch()
        layout.addWidget(confirm_btn)
        return layout

    def _apply_styles(self):
        """Applies a modern, dark stylesheet to the dialog."""
        self.setStyleSheet("""
            #mainContainer { background-color: #1c1c2e; border: 1px solid #3a3a5a; border-radius: 12px; }
            #dialogTitle { color: #e0e0e0; font-size: 16px; font-weight: 600; }
            #closeButton { background: transparent; border: none; color: #8a8a9e; font-size: 16px; font-weight: bold; }
            #closeButton:hover { color: #ffffff; }
            #divider { border: 1px solid #2a2a4a; }

            #symbolLabel { color: #ffffff; font-size: 28px; font-weight: 300; }
            #buyTag, #sellTag { font-size: 10px; font-weight: bold; border-radius: 4px; padding: 4px 10px; }
            #buyTag { background-color: #00b894; color: #ffffff; }
            #sellTag { background-color: #d63031; color: #ffffff; }

            #summaryTitle { color: #8a8a9e; font-size: 11px; font-weight: bold; text-transform: uppercase; }
            #summaryValue { color: #e0e0e0; font-size: 16px; font-weight: 500; }

            #summaryBox { background-color: #2a2a4a; border-radius: 8px; padding: 12px; }
            #costTitle { font-size: 11px; color: #8a8a9e; font-weight: bold; text-transform: uppercase; }
            #costValue { font-size: 32px; font-weight: 300; color: #ffffff; padding-bottom: 2px; }

            QPushButton { font-weight: bold; border-radius: 6px; padding: 11px 18px; border: none; font-size: 13px; }
            #secondaryButton { background-color: #3a3a5a; color: #e0e0e0; }
            #secondaryButton:hover { background-color: #4a4a6a; }
            #primaryButton { background-color: #00b894; color: #ffffff; }
            #primaryButton:hover { background-color: #00d2a2; }
        """)

    # --- Window Dragging ---
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.LeftButton and self._drag_pos:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None
        event.accept()

