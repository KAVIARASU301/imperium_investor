import logging
import sys

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QApplication
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, QByteArray, QTimer

logger = logging.getLogger(__name__)


class OrderStatusWidget(QWidget):
    """
    Small, non-modal widgets that display the status of a single pending order.
    It appears in the corner of the screen and provides modify/cancel actions.
    """
    cancel_requested = Signal(str)  # Emits order_id
    modify_requested = Signal(dict)  # Emits order data

    def __init__(self, order_data: dict, parent=None):
        super().__init__(parent)
        self.order_data = order_data
        self.order_id = order_data.get("order_id")
        self.animation = None

        self._setup_ui()
        self._apply_styles()
        self.show()
        self.animate_in()

    def _setup_ui(self):
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedSize(280, 110)

        container = QFrame(self)
        container.setObjectName("mainContainer")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(5)

        # Top row: Symbol and Status
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

        # Info row: Type, Qty, Price
        info_text = (
            f"{self.order_data.get('transaction_type', '')} "
            f"{self.order_data.get('quantity', 0)} @ "
            f"₹{self.order_data.get('price', 0.0):.2f}"
        )
        info_label = QLabel(info_text)
        info_label.setObjectName("infoLabel")
        layout.addWidget(info_label)

        layout.addStretch()

        # Action buttons
        button_layout = QHBoxLayout()
        self.modify_btn = QPushButton("EDIT")
        self.modify_btn.setObjectName("modifyButton")
        self.modify_btn.clicked.connect(lambda: self.modify_requested.emit(self.order_data))

        self.cancel_btn = QPushButton("CANCEL")
        self.cancel_btn.setObjectName("cancelButton")
        self.cancel_btn.clicked.connect(lambda: self.cancel_requested.emit(self.order_id))

        button_layout.addWidget(self.modify_btn)
        button_layout.addWidget(self.cancel_btn)
        layout.addLayout(button_layout)

    def animate_in(self):
        self.animation = QPropertyAnimation(self, QByteArray(b"windowOpacity"))
        self.animation.setDuration(300)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.Type.InQuad)
        self.animation.start()

    def close_widget(self):
        self.animation = QPropertyAnimation(self, QByteArray(b"windowOpacity"))
        self.animation.setDuration(300)
        self.animation.setStartValue(1.0)
        self.animation.setEndValue(0.0)
        self.animation.setEasingCurve(QEasingCurve.Type.OutQuad)
        self.animation.finished.connect(self.close)
        self.animation.start()

    def _apply_styles(self):
        self.setStyleSheet("""
            #mainContainer {
                background-color: #1c1c1c;
                border: 1px solid #333;
                border-radius: 8px;
            }
            #symbolLabel {
                color: #e0e0e0; font-size: 13px; font-weight: bold;
            }
            #statusLabel {
                color: #ffb86c; font-size: 10px; font-weight: bold;
                text-transform: uppercase;
            }
            #infoLabel { color: #a0a0a0; font-size: 11px; }

            QPushButton {
                font-family: "Segoe UI"; font-weight: bold; border-radius: 5px; 
                padding: 6px 12px; font-size: 10px; border: none;
            }
            #modifyButton { background-color: #444; color: #e0e0e0; }
            #modifyButton:hover { background-color: #555; }
            #cancelButton { background-color: #ff3860; color: #1c1c1c; }
            #cancelButton:hover { background-color: #ff5070; }
        """)

#---------------------------------------
def usage():
    """
    Usage function to test the OrderStatusWidget.
    It creates a QApplication and displays a sample OrderStatusWidget.
    """
    app = QApplication(sys.argv)

    # Sample order data
    sample_order = {
        "order_id": "ORD12345",
        "tradingsymbol": "INFY",
        "status": "pending_validation",
        "transaction_type": "BUY",
        "quantity": 100,
        "price": 1500.75,
    }

    widget = OrderStatusWidget(sample_order)

    # Connect signals to a simple print function for demonstration
    widget.cancel_requested.connect(lambda order_id: print(f"Cancel requested for Order ID: {order_id}"))
    widget.modify_requested.connect(lambda order_data: print(f"Modify requested for Order: {order_data}"))

    # Position the widget in the bottom-right corner
    screen_geometry = app.primaryScreen().availableGeometry()
    x = screen_geometry.width() - widget.width() - 20
    y = screen_geometry.height() - widget.height() - 20
    widget.move(x, y)

    # Optional: Close the widget after some time for demonstration
    QTimer.singleShot(5000, widget.close_widget)

    sys.exit(app.exec())

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    usage()