import logging
import sys
from typing import Dict, Any

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QApplication
)
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, QByteArray, QTimer

logger = logging.getLogger(__name__)


class OrderStatusWidget(QWidget):
    """
    A small, non-modal "toast" widget that displays the status of a single
    pending order. It appears in the corner of the screen and provides
    actions to modify or cancel the order.
    """
    cancel_requested = Signal(str)  # Emits order_id
    modify_requested = Signal(dict)  # Emits full order data for modification

    def __init__(self, order_data: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.order_data = order_data
        self.order_id = order_data.get("order_id")
        self.animation = None

        self._setup_ui()
        self._apply_styles()
        self.show()
        self.animate_in()

    def _setup_ui(self):
        """Initializes the UI components and layout."""
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(300, 120)

        container = QFrame(self)
        container.setObjectName("mainContainer")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(15, 12, 15, 12)
        layout.setSpacing(5)

        # Top Row: Symbol and Status
        top_layout = QHBoxLayout()
        symbol = self.order_data.get('tradingsymbol', 'N/A')
        symbol_label = QLabel(symbol)
        symbol_label.setObjectName("symbolLabel")

        status = self.order_data.get('status', 'N/A').replace("_", " ").title()
        status_label = QLabel(status)
        status_label.setObjectName("statusLabel")

        top_layout.addWidget(symbol_label)
        top_layout.addStretch()
        top_layout.addWidget(status_label)
        layout.addLayout(top_layout)

        # Separator Line
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("divider")
        layout.addWidget(line)

        # Info Row: Type, Quantity, Price
        info_text = (
            f"{self.order_data.get('transaction_type', '')} "
            f"{self.order_data.get('quantity', 0)} @ "
            f"₹{self.order_data.get('price', 0.0):,.2f}"
        )
        info_label = QLabel(info_text)
        info_label.setObjectName("infoLabel")
        layout.addWidget(info_label)

        layout.addStretch()

        # Action Buttons
        button_layout = QHBoxLayout()
        self.modify_btn = QPushButton("MODIFY")
        self.modify_btn.setObjectName("secondaryButton")
        self.modify_btn.clicked.connect(lambda: self.modify_requested.emit(self.order_data))

        self.cancel_btn = QPushButton("CANCEL")
        self.cancel_btn.setObjectName("dangerButton")
        self.cancel_btn.clicked.connect(lambda: self.cancel_requested.emit(self.order_id))

        button_layout.addStretch()
        button_layout.addWidget(self.modify_btn)
        button_layout.addWidget(self.cancel_btn)
        layout.addLayout(button_layout)

    def animate_in(self):
        """Fades the widget in when it first appears."""
        self.animation = QPropertyAnimation(self, QByteArray(b"windowOpacity"))
        self.animation.setDuration(300)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.Type.InQuad)
        self.animation.start()

    def close_widget(self):
        """Fades the widget out before closing it."""
        self.animation = QPropertyAnimation(self, QByteArray(b"windowOpacity"))
        self.animation.setDuration(300)
        self.animation.setStartValue(1.0)
        self.animation.setEndValue(0.0)
        self.animation.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.animation.finished.connect(self.close)
        self.animation.start()

    def _apply_styles(self):
        """Applies a consistent, modern dark theme stylesheet."""
        self.setStyleSheet("""
            #mainContainer {
                background-color: #2a2a4a;
                border: 1px solid #3a3a5a;
                border-radius: 8px;
            }
            #symbolLabel {
                color: #e0e0e0; font-size: 14px; font-weight: 600;
            }
            #statusLabel {
                color: #fdcb6e; font-size: 11px; font-weight: bold;
                text-transform: uppercase;
            }
            #infoLabel { color: #b2bec3; font-size: 12px; }
            #divider { border: 1px solid #3a3a5a; }

            QPushButton {
                font-family: "Segoe UI"; font-weight: bold; border-radius: 6px; 
                padding: 7px 14px; font-size: 11px; border: none;
            }
            #secondaryButton { background-color: #4a4a6a; color: #e0e0e0; }
            #secondaryButton:hover { background-color: #5a5a7a; }
            #dangerButton { background-color: #d63031; color: #ffffff; }
            #dangerButton:hover { background-color: #e17055; }
        """)


# --- Example Usage ---
def usage():
    """Demonstrates how to create and use the OrderStatusWidget."""
    app = QApplication(sys.argv)

    sample_order = {
        "order_id": "ORD12345",
        "tradingsymbol": "RELIANCE",
        "status": "OPEN",
        "transaction_type": "BUY",
        "quantity": 50,
        "price": 2850.50,
    }

    widget = OrderStatusWidget(sample_order)

    # Example of connecting signals
    widget.cancel_requested.connect(lambda oid: print(f"Cancel requested for Order ID: {oid}"))
    widget.modify_requested.connect(lambda odata: print(f"Modify requested for Order: {odata}"))

    # Position the widget in the bottom-right corner of the screen
    screen_geometry = app.primaryScreen().availableGeometry()
    x = screen_geometry.width() - widget.width() - 20
    y = screen_geometry.height() - widget.height() - 40  # Position above taskbar
    widget.move(x, y)

    # For demonstration, close the widget after 5 seconds
    QTimer.singleShot(5000, widget.close_widget)

    sys.exit(app.exec())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    usage()
