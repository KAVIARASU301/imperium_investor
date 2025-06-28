# widgets/order_dialog.py - Improved version with better alignment, darker theme, and compact height

import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget, QFrame,
    QComboBox, QCheckBox, QButtonGroup, QRadioButton, QMessageBox,
    QTabWidget, QSpinBox, QDoubleSpinBox, QGridLayout, QGroupBox, QFormLayout
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
        self.setFixedSize(90, 18)  # Smaller size
        self._is_buy = True

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # Background - sharp square edges
        rect = self.rect()
        painter.setPen(QPen(QColor("#1a1a1a"), 1))

        if self._is_buy:
            painter.setBrush(QBrush(QColor("#0d1a0d")))  # Darker green
        else:
            painter.setBrush(QBrush(QColor("#1a0d0d")))  # Darker red

        painter.drawRect(rect)

        # Button - sharp square edges
        button_width = 42
        button_height = 14
        button_y = 2

        if self._is_buy:
            button_x = 2
            color = QColor("#2d5a2d")
        else:
            button_x = self.width() - button_width - 2
            color = QColor("#5a2d2d")

        painter.setBrush(QBrush(color))
        painter.setPen(QPen(QColor("#0a0a0a"), 1))
        painter.drawRect(button_x, button_y, button_width, button_height)

        # Text
        painter.setPen(QColor("#ffffff"))
        font = QFont("Arial", 7, QFont.Weight.Bold)
        painter.setFont(font)

        if self._is_buy:
            painter.drawText(button_x, button_y, button_width, button_height, Qt.AlignmentFlag.AlignCenter, "BUY")
            painter.setPen(QColor("#666666"))
            painter.drawText(button_x + button_width + 2, 0, self.width() - button_x - button_width - 4, self.height(),
                             Qt.AlignmentFlag.AlignCenter, "SELL")
        else:
            painter.drawText(button_x, button_y, button_width, button_height, Qt.AlignmentFlag.AlignCenter, "SELL")
            painter.setPen(QColor("#666666"))
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
    """Compact order dialog with improved alignment, darker theme, and reduced height."""

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
        self.setFixedSize(350, 380)  # Reduced height from 480 to 380
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
        """Build the compact UI with better vertical alignment."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header
        header_frame = self._create_header()
        main_layout.addWidget(header_frame)

        # Content with form layout for better alignment
        content_frame = self._create_content()
        main_layout.addWidget(content_frame)

        # Buttons
        button_frame = self._create_buttons()
        main_layout.addWidget(button_frame)

    def _create_header(self) -> QFrame:
        """Create compact header."""
        header = QFrame()
        header.setFixedHeight(35)  # Reduced from 40 to 35
        header.setObjectName("orderDialogHeader")

        layout = QHBoxLayout(header)
        layout.setContentsMargins(12, 6, 6, 6)

        # Symbol and LTP
        symbol_label = QLabel(f"{self.symbol}")
        symbol_label.setObjectName("symbolLabel")

        self.ltp_label = QLabel(f"₹{self.ltp:.2f}")
        self.ltp_label.setObjectName("ltpLabel")

        # Close button
        close_btn = QPushButton("×")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(18, 18)  # Smaller close button
        close_btn.clicked.connect(self.reject)

        layout.addWidget(symbol_label)
        layout.addWidget(self.ltp_label)
        layout.addStretch()
        layout.addWidget(close_btn)

        return header

    def _create_content(self) -> QFrame:
        """Create main content area with form layout for better alignment."""
        content = QFrame()
        content.setObjectName("orderDialogContent")

        layout = QVBoxLayout(content)
        layout.setContentsMargins(15, 15, 15, 15)  # Increased margins
        layout.setSpacing(12)  # Increased spacing

        # Create form layout for better alignment
        form_layout = QFormLayout()
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form_layout.setVerticalSpacing(12)  # Increased vertical spacing
        form_layout.setHorizontalSpacing(12)

        # Buy/Sell Toggle
        self.buy_sell_toggle = CompactToggleSwitch()
        self.buy_sell_toggle.set_buy_mode(self._is_buy)
        form_layout.addRow("Transaction:", self.buy_sell_toggle)

        # Quantity
        self.quantity_spin = QSpinBox()
        self.quantity_spin.setRange(1, 10000)
        self.quantity_spin.setValue(self.order_details.get('quantity', 1))
        self.quantity_spin.setFixedWidth(140)  # Increased width
        form_layout.addRow("Quantity:", self.quantity_spin)

        # Order Type
        self.order_type_combo = QComboBox()
        self.order_type_combo.addItems(["MARKET", "LIMIT"])
        self.order_type_combo.setCurrentText(self._order_type)
        self.order_type_combo.setFixedWidth(140)  # Increased width
        form_layout.addRow("Order Type:", self.order_type_combo)

        # Price (for LIMIT orders)
        self.price_spin = QDoubleSpinBox()
        self.price_spin.setRange(0.05, 99999.95)
        self.price_spin.setDecimals(2)
        self.price_spin.setSingleStep(0.05)
        self.price_spin.setValue(self.ltp)
        self.price_spin.setEnabled(self._order_type == "LIMIT")
        self.price_spin.setFixedWidth(140)  # Increased width
        form_layout.addRow("Price:", self.price_spin)

        # Product Type
        self.product_combo = QComboBox()
        self.product_combo.addItems(["MIS", "CNC", "NRML"])
        self.product_combo.setCurrentText(self._product_type)
        self.product_combo.setFixedWidth(140)  # Increased width
        form_layout.addRow("Product:", self.product_combo)

        # Total Margin Required (Price * Quantity)
        self.total_margin_label = QLabel("₹0.00")
        self.total_margin_label.setObjectName("totalMarginLabel")
        self.total_margin_label.setFixedWidth(140)  # Increased width
        form_layout.addRow("Total Margin:", self.total_margin_label)

        layout.addLayout(form_layout)
        return content

    def _create_buttons(self) -> QFrame:
        """Create button area."""
        button_frame = QFrame()
        button_frame.setFixedHeight(50)  # Reduced from 60 to 50
        button_frame.setObjectName("orderDialogButtons")

        layout = QHBoxLayout(button_frame)
        layout.setContentsMargins(15, 12, 15, 12)  # Increased margins

        # Cancel button
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cancelButton")
        cancel_btn.setFixedHeight(32)  # Increased height
        cancel_btn.clicked.connect(self.reject)

        # Place Order button
        self.place_btn = QPushButton("Place Order")
        self.place_btn.setObjectName("placeOrderButton")
        self.place_btn.setFixedHeight(32)  # Increased height
        self.place_btn.clicked.connect(self._place_order)

        layout.addStretch()
        layout.addWidget(cancel_btn)
        layout.addWidget(self.place_btn)

        return button_frame

    def _connect_signals(self):
        """Connect internal signals."""
        self.buy_sell_toggle.toggled.connect(self._on_transaction_type_changed)
        self.order_type_combo.currentTextChanged.connect(self._on_order_type_changed)

        # Connect signals for real-time margin calculation
        self.quantity_spin.valueChanged.connect(self._update_total_margin)
        self.price_spin.valueChanged.connect(self._update_total_margin)
        self.order_type_combo.currentTextChanged.connect(self._update_total_margin)

    def _populate_initial_data(self):
        """Populate dialog with initial data."""
        # Set transaction type from order details
        if 'transaction_type' in self.order_details:
            self._is_buy = self.order_details['transaction_type'] == 'BUY'
            self.buy_sell_toggle.set_buy_mode(self._is_buy)

        # Calculate initial total margin
        self._update_total_margin()

    def _on_transaction_type_changed(self, is_buy: bool):
        """Handle transaction type change."""
        self._is_buy = is_buy

    def _on_order_type_changed(self, order_type: str):
        """Handle order type change."""
        self._order_type = order_type
        self.price_spin.setEnabled(order_type == "LIMIT")
        self._update_total_margin()

    def _update_total_margin(self):
        """Update total margin calculation in real-time."""
        try:
            quantity = self.quantity_spin.value()

            # Get price based on order type
            if self.order_type_combo.currentText() == "LIMIT":
                price = self.price_spin.value()
            else:
                price = self.ltp  # Use LTP for MARKET orders

            total_margin = price * quantity
            self.total_margin_label.setText(f"₹{total_margin:,.2f}")

        except (ValueError, AttributeError):
            self.total_margin_label.setText("₹0.00")

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
    # DIALOG STYLING - DARKER THEME WITH BETTER ALIGNMENT
    # ============================================================================

    def _apply_compact_styles(self):
        """Apply darker, more compact styling with better alignment."""
        self.setStyleSheet("""
            /* Main Dialog - Much Darker */
            QDialog {
                background-color: #0a0a0a;
                border: 1px solid #1a1a1a;
            }

            /* Header - Much Darker */
            #orderDialogHeader {
                background-color: #0f0f0f;
                border-bottom: 1px solid #1a1a1a;
            }

            #symbolLabel {
                font-size: 14px;  /* Increased from 13px */
                font-weight: bold;
                color: #ffffff;
            }

            #ltpLabel {
                font-size: 12px;  /* Increased from 11px */
                color: #4CAF50;
                font-weight: bold;
            }

            #closeButton {
                background-color: #d32f2f;
                border: none;
                color: white;
                font-size: 13px;  /* Increased from 12px */
                font-weight: bold;
                border-radius: 1px;
            }

            #closeButton:hover {
                background-color: #b71c1c;
            }

            /* Content - Much Darker */
            #orderDialogContent {
                background-color: #0a0a0a;
            }

            QLabel {
                color: #e0e0e0;
                font-size: 11px;  /* Increased from 10px */
                font-weight: 500;
                min-width: 80px;  /* Increased from 70px */
                padding-right: 6px;
            }

            /* Input Fields - Larger and Better Sized */
            QSpinBox, QDoubleSpinBox, QComboBox {
                background-color: #0d0d0d;
                border: 1px solid #2a2a2a;
                color: #ffffff;
                padding: 6px 8px;  /* Increased padding */
                font-size: 11px;   /* Increased from 10px */
                min-height: 22px;  /* Increased from 18px */
                max-height: 26px;  /* Increased from 22px */
                border-radius: 2px;
            }

            QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
                border: 1px solid #4CAF50;
                background-color: #111111;
            }

            QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {
                border: 1px solid #404040;
                background-color: #0f0f0f;
            }

            /* ComboBox dropdown */
            QComboBox::drop-down {
                border: none;
                width: 24px;  /* Increased from 20px */
                background-color: #1a1a1a;
            }

            QComboBox::down-arrow {
                image: none;
                border: 3px solid #666666;  /* Increased from 2px */
                border-top: none;
                border-left: 3px solid transparent;
                border-right: 3px solid transparent;
                width: 0;
                height: 0;
            }

            QComboBox QAbstractItemView {
                background-color: #0d0d0d;
                border: 1px solid #2a2a2a;
                selection-background-color: #2a2a2a;
                color: #ffffff;
                font-size: 11px;  /* Added font size for dropdown */
            }

            /* Buttons - Much Darker */
            #orderDialogButtons {
                background-color: #0f0f0f;
                border-top: 1px solid #1a1a1a;
            }

            #cancelButton {
                background-color: #333333;
                border: 1px solid #404040;
                color: #e0e0e0;
                padding: 8px 16px;  /* Increased padding */
                font-size: 11px;    /* Increased from 10px */
                min-width: 70px;    /* Increased from 60px */
                border-radius: 2px;
            }

            #cancelButton:hover {
                background-color: #404040;
                border-color: #505050;
            }

            #placeOrderButton {
                background-color: #2d5a2d;
                border: 1px solid #4CAF50;
                color: #ffffff;
                padding: 8px 16px;  /* Increased padding */
                font-size: 11px;    /* Increased from 10px */
                font-weight: bold;
                min-width: 90px;    /* Increased from 80px */
                border-radius: 2px;
            }

            #placeOrderButton:hover {
                background-color: #4CAF50;
                border-color: #66BB6A;
            }

            #placeOrderButton:pressed {
                background-color: #1a4a1a;
                border-color: #2d5a2d;
            }

            /* Total Margin Label */
            #totalMarginLabel {
                color: #64ffda;
                font-size: 12px;  /* Increased from 11px */
                font-weight: bold;
                background-color: #0d1a1a;
                padding: 6px 8px;  /* Increased padding */
                border: 1px solid #2a4a4a;
                border-radius: 2px;
                min-height: 22px;  /* Increased from 18px */
            }

            /* Form Layout Styling */
            QFormLayout {
                spacing: 8px;  /* Increased from 6px */
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