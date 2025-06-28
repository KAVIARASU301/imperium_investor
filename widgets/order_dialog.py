# widgets/order_dialog.py - Fixed version with complete error handling

import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget, QFrame,
    QComboBox, QCheckBox, QButtonGroup, QRadioButton, QMessageBox,
    QTabWidget, QSpinBox, QDoubleSpinBox, QGridLayout, QGroupBox
)
from PySide6.QtCore import Qt, Signal, QRect, QTimer
from PySide6.QtGui import QMouseEvent, QShowEvent, QPainter, QFont, QPen, QColor, QBrush
from typing import Dict, Any, Optional
from decimal import Decimal, ROUND_HALF_UP

# Import the status bar functions for error display
from widgets.status_bar import show_error, show_info

logger = logging.getLogger(__name__)


class CompactToggleSwitch(QWidget):
    """Simple compact toggle switch for Buy/Sell selection."""

    toggled = Signal(bool)  # True for Buy, False for Sell

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(100, 20)  # Match close button height
        self._is_buy = True

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)  # Sharp edges

        # Background - sharp square edges
        rect = self.rect()
        painter.setPen(QPen(QColor("#3a3a3a"), 1))

        if self._is_buy:
            painter.setBrush(QBrush(QColor("#1a3d1a")))  # Dark green
        else:
            painter.setBrush(QBrush(QColor("#3d1a1a")))  # Dark red

        painter.drawRect(rect)  # Square edges instead of rounded

        # Button - sharp square edges
        button_width = 46
        button_height = 16
        button_y = 2

        if self._is_buy:
            button_x = 2
            color = QColor("#4CAF50")
        else:
            button_x = self.width() - button_width - 2
            color = QColor("#F44336")

        painter.setBrush(QBrush(color))
        painter.setPen(QPen(QColor("#222222"), 1))
        painter.drawRect(button_x, button_y, button_width, button_height)  # Square edges

        # Text
        painter.setPen(QColor("#ffffff"))
        font = QFont("Arial", 8, QFont.Weight.Bold)
        painter.setFont(font)

        if self._is_buy:
            painter.drawText(button_x, button_y, button_width, button_height, Qt.AlignmentFlag.AlignCenter, "BUY")
            painter.setPen(QColor("#888888"))
            painter.drawText(button_x + button_width + 3, 0, self.width() - button_x - button_width - 6, self.height(),
                             Qt.AlignmentFlag.AlignCenter, "SELL")
        else:
            painter.drawText(button_x, button_y, button_width, button_height, Qt.AlignmentFlag.AlignCenter, "SELL")
            painter.setPen(QColor("#888888"))
            painter.drawText(2, 0, button_x - 2, self.height(), Qt.AlignmentFlag.AlignCenter, "BUY")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_buy = not self._is_buy
            self.toggled.emit(self._is_buy)
            self.update()

    def is_buy_mode(self):
        return self._is_buy

    def set_buy_mode(self, is_buy: bool):
        if self._is_buy != is_buy:
            self._is_buy = is_buy
            self.update()


class CompactOrderDialog(QDialog):
    """Compact order dialog with complete error handling and 2010-2015 style UI."""

    ltp_update_requested = Signal(str)
    order_placed = Signal(dict)
    bracket_order_placed = Signal(dict)

    def __init__(self, parent: QWidget, symbol: str, ltp: float = 0.0, order_details: Optional[Dict[str, Any]] = None):
        super().__init__(parent)
        self.symbol = symbol
        self.ltp = ltp
        self.order_details = order_details or {}
        self._drag_pos = None

        # Initialize order parameters
        self._is_buy = order_details.get('transaction_type', 'BUY') == 'BUY'
        self._order_type = "MARKET"
        self._product_type = "MIS"
        self._validity = "DAY"

        self._setup_dialog()
        self._setup_ui()
        self._apply_compact_styles()
        self._connect_signals()
        self._populate_initial_data()

    def _setup_dialog(self):
        """Configure dialog properties."""
        self.setWindowTitle(f"Order - {self.symbol}")
        self.setModal(True)
        self.setFixedSize(380, 480)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)

    def showEvent(self, event: QShowEvent):
        """Center dialog on parent."""
        super().showEvent(event)
        if self.parent():
            parent_rect = self.parent().geometry()
            x = parent_rect.center().x() - self.width() // 2
            y = parent_rect.center().y() - self.height() // 2
            self.move(x, y)

    def _setup_ui(self):
        """Build the compact UI."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header
        header_frame = self._create_header()
        main_layout.addWidget(header_frame)

        # Content
        content_frame = self._create_content()
        main_layout.addWidget(content_frame)

        # Buttons
        button_frame = self._create_buttons()
        main_layout.addWidget(button_frame)

    def _create_header(self) -> QFrame:
        """Create compact header."""
        header = QFrame()
        header.setFixedHeight(40)
        header.setObjectName("orderDialogHeader")

        layout = QHBoxLayout(header)
        layout.setContentsMargins(15, 8, 8, 8)

        # Symbol and LTP
        symbol_label = QLabel(f"{self.symbol}")
        symbol_label.setObjectName("symbolLabel")

        self.ltp_label = QLabel(f"₹{self.ltp:.2f}")
        self.ltp_label.setObjectName("ltpLabel")

        # Close button
        close_btn = QPushButton("×")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(20, 20)
        close_btn.clicked.connect(self.reject)

        layout.addWidget(symbol_label)
        layout.addWidget(self.ltp_label)
        layout.addStretch()
        layout.addWidget(close_btn)

        return header

    def _create_content(self) -> QFrame:
        """Create main content area."""
        content = QFrame()
        content.setObjectName("orderDialogContent")

        layout = QVBoxLayout(content)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        # Buy/Sell Toggle
        toggle_layout = QHBoxLayout()
        toggle_layout.addWidget(QLabel("Transaction:"))
        self.buy_sell_toggle = CompactToggleSwitch()
        self.buy_sell_toggle.set_buy_mode(self._is_buy)
        toggle_layout.addWidget(self.buy_sell_toggle)
        toggle_layout.addStretch()
        layout.addLayout(toggle_layout)

        # Quantity
        qty_layout = QHBoxLayout()
        qty_layout.addWidget(QLabel("Quantity:"))
        self.quantity_spin = QSpinBox()
        self.quantity_spin.setRange(1, 10000)
        self.quantity_spin.setValue(self.order_details.get('quantity', 1))
        qty_layout.addWidget(self.quantity_spin)
        qty_layout.addStretch()
        layout.addLayout(qty_layout)

        # Order Type
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Order Type:"))
        self.order_type_combo = QComboBox()
        self.order_type_combo.addItems(["MARKET", "LIMIT"])
        self.order_type_combo.setCurrentText(self._order_type)
        type_layout.addWidget(self.order_type_combo)
        type_layout.addStretch()
        layout.addLayout(type_layout)

        # Price (for LIMIT orders)
        price_layout = QHBoxLayout()
        price_layout.addWidget(QLabel("Price:"))
        self.price_spin = QDoubleSpinBox()
        self.price_spin.setRange(0.05, 99999.95)
        self.price_spin.setDecimals(2)
        self.price_spin.setSingleStep(0.05)
        self.price_spin.setValue(self.ltp)
        self.price_spin.setEnabled(self._order_type == "LIMIT")
        price_layout.addWidget(self.price_spin)
        price_layout.addStretch()
        layout.addLayout(price_layout)

        # Product Type
        product_layout = QHBoxLayout()
        product_layout.addWidget(QLabel("Product:"))
        self.product_combo = QComboBox()
        self.product_combo.addItems(["MIS", "CNC", "NRML"])
        self.product_combo.setCurrentText(self._product_type)
        product_layout.addWidget(self.product_combo)
        product_layout.addStretch()
        layout.addLayout(product_layout)

        return content

    def _create_buttons(self) -> QFrame:
        """Create button area."""
        button_frame = QFrame()
        button_frame.setFixedHeight(60)
        button_frame.setObjectName("orderDialogButtons")

        layout = QHBoxLayout(button_frame)
        layout.setContentsMargins(15, 15, 15, 15)

        # Cancel button
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cancelButton")
        cancel_btn.clicked.connect(self.reject)

        # Place Order button
        self.place_btn = QPushButton("Place Order")
        self.place_btn.setObjectName("placeOrderButton")
        self.place_btn.clicked.connect(self._place_order)

        layout.addStretch()
        layout.addWidget(cancel_btn)
        layout.addWidget(self.place_btn)

        return button_frame

    def _connect_signals(self):
        """Connect internal signals."""
        self.buy_sell_toggle.toggled.connect(self._on_transaction_type_changed)
        self.order_type_combo.currentTextChanged.connect(self._on_order_type_changed)

    def _populate_initial_data(self):
        """Populate dialog with initial data."""
        # Set transaction type from order details
        if 'transaction_type' in self.order_details:
            self._is_buy = self.order_details['transaction_type'] == 'BUY'
            self.buy_sell_toggle.set_buy_mode(self._is_buy)

    def _on_transaction_type_changed(self, is_buy: bool):
        """Handle transaction type change."""
        self._is_buy = is_buy

    def _on_order_type_changed(self, order_type: str):
        """Handle order type change."""
        self._order_type = order_type
        self.price_spin.setEnabled(order_type == "LIMIT")

    def _place_order(self):
        """
        OPTIMIZED: Place order with minimal UI operations and proper error handling
        """
        try:
            # Validate inputs - FAST validation only
            if not self._quick_validate():
                return

            # Get order data - MINIMAL processing
            order_data = self._get_order_data_fast()

            # Emit signal IMMEDIATELY - no processing delays
            self.order_placed.emit(order_data)

            # Close dialog IMMEDIATELY
            self.accept()

            logger.info(f"Order dialog completed: {order_data.get('tradingsymbol')}")

        except Exception as e:
            logger.error(f"Order dialog error: {e}")
            self.show_error(str(e))

    def _quick_validate(self) -> bool:
        """Fast validation without heavy operations"""
        # Only essential validations
        if not self.symbol.strip():
            self.show_error("Symbol required")
            return False

        try:
            qty = int(self.quantity_spin.value())
            if qty <= 0:
                self.show_error("Quantity must be positive")
                return False
        except ValueError:
            self.show_error("Invalid quantity")
            return False

        # Validate price for LIMIT orders
        if self.order_type_combo.currentText() == "LIMIT":
            try:
                price = float(self.price_spin.value())
                if price <= 0:
                    self.show_error("Price must be positive for LIMIT orders")
                    return False
            except ValueError:
                self.show_error("Invalid price")
                return False

        return True

    def _get_order_data_fast(self) -> dict:
        """Get order data with minimal processing"""
        return {
            'tradingsymbol': self.symbol.strip().upper(),
            'transaction_type': 'BUY' if self.buy_sell_toggle.is_buy_mode() else 'SELL',
            'quantity': self.quantity_spin.value(),
            'order_type': self.order_type_combo.currentText(),
            'product': self.product_combo.currentText(),
            'price': self.price_spin.value() if self.order_type_combo.currentText() == 'LIMIT' else None,
            'variety': 'regular',
            'exchange': 'NSE',
            'validity': 'DAY'
        }

    # ============================================================================
    # ERROR HANDLING METHODS
    # ============================================================================

    def show_error(self, message: str):
        """Show error message using status bar (non-blocking)"""
        show_error(message)
        logger.error(f"Order Dialog Error: {message}")

    def show_info(self, message: str):
        """Show info message using status bar (non-blocking)"""
        show_info(message)
        logger.info(f"Order Dialog Info: {message}")

    def show_warning(self, message: str):
        """Show warning message using status bar (non-blocking)"""
        show_error(f"Warning: {message}")  # Use error color for warnings
        logger.warning(f"Order Dialog Warning: {message}")

    def show_success(self, message: str):
        """Show success message using status bar (non-blocking)"""
        show_info(message)
        logger.info(f"Order Dialog Success: {message}")

    # ============================================================================
    # DIALOG STYLING
    # ============================================================================

    def _apply_compact_styles(self):
        """Apply compact 2010-2015 style."""
        self.setStyleSheet("""
            /* Main Dialog */
            QDialog {
                background-color: #2d2d2d;
                border: 1px solid #555555;
            }

            /* Header */
            #orderDialogHeader {
                background-color: #3d3d3d;
                border-bottom: 1px solid #555555;
            }

            #symbolLabel {
                font-size: 14px;
                font-weight: bold;
                color: #ffffff;
            }

            #ltpLabel {
                font-size: 12px;
                color: #4CAF50;
                font-weight: bold;
            }

            #closeButton {
                background-color: #f44336;
                border: none;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border-radius: 2px;
            }

            #closeButton:hover {
                background-color: #d32f2f;
            }

            /* Content */
            #orderDialogContent {
                background-color: #2d2d2d;
            }

            QLabel {
                color: #ffffff;
                font-size: 11px;
            }

            QSpinBox, QDoubleSpinBox, QComboBox {
                background-color: #1a1a1a;
                border: 1px solid #555555;
                color: #ffffff;
                padding: 4px;
                font-size: 11px;
                min-width: 80px;
            }

            QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
                border: 1px solid #4CAF50;
            }

            /* Buttons */
            #orderDialogButtons {
                background-color: #3d3d3d;
                border-top: 1px solid #555555;
            }

            #cancelButton {
                background-color: #666666;
                border: 1px solid #555555;
                color: #ffffff;
                padding: 8px 16px;
                font-size: 11px;
                min-width: 70px;
            }

            #cancelButton:hover {
                background-color: #777777;
            }

            #placeOrderButton {
                background-color: #4CAF50;
                border: 1px solid #45a049;
                color: #ffffff;
                padding: 8px 16px;
                font-size: 11px;
                font-weight: bold;
                min-width: 90px;
            }

            #placeOrderButton:hover {
                background-color: #45a049;
            }

            #placeOrderButton:pressed {
                background-color: #3d8b40;
            }
        """)

    # ============================================================================
    # WINDOW DRAGGING
    # ============================================================================

    def mousePressEvent(self, event: QMouseEvent):
        """Handle mouse press for dragging."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        """Handle mouse move for dragging."""
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        """Handle mouse release."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = None
            event.accept()


# Alias for backward compatibility
OrderDialog = CompactOrderDialog