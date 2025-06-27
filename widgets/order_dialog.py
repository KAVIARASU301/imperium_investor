import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget, QFrame,
    QComboBox, QCheckBox, QButtonGroup, QRadioButton,
    QTabWidget, QSpinBox, QDoubleSpinBox, QGridLayout, QMessageBox, QGroupBox
)
from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QMouseEvent, QShowEvent, QPainter, QFont, QPen, QColor, QBrush
from typing import Dict, Any, Optional
from decimal import Decimal, ROUND_HALF_UP

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
    """Compact order dialog with 2010-2015 style UI."""
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
        header.setObjectName("headerFrame")
        header.setFixedHeight(50)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(15)

        # Symbol and LTP
        symbol_layout = QVBoxLayout()
        symbol_layout.setSpacing(2)

        self.symbol_label = QLabel(self.symbol)
        self.symbol_label.setObjectName("symbolLabel")

        self.ltp_label = QLabel(f"₹{self.ltp:,.2f}")
        self.ltp_label.setObjectName("ltpLabel")

        symbol_layout.addWidget(self.symbol_label)
        symbol_layout.addWidget(self.ltp_label)

        # Toggle switch
        self.toggle_switch = CompactToggleSwitch()
        self.toggle_switch.set_buy_mode(self._is_buy)

        # Close button
        close_btn = QPushButton("×")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(24, 20)
        close_btn.clicked.connect(self.reject)

        layout.addLayout(symbol_layout)
        layout.addStretch()
        layout.addWidget(self.toggle_switch)
        layout.addWidget(close_btn)

        return header

    def _create_content(self) -> QFrame:
        """Create content area with tabs."""
        content = QFrame()
        content.setObjectName("contentFrame")

        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Tabs
        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("compactTabs")

        # Regular Order Tab
        self.tab_widget.addTab(self._create_regular_tab(), "Regular")

        # Bracket Order Tab
        self.tab_widget.addTab(self._create_bracket_tab(), "Bracket")

        layout.addWidget(self.tab_widget)

        return content

    def _create_regular_tab(self) -> QWidget:
        """Create regular order tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)

        # Order Type Group
        type_group = QGroupBox("Order Type")
        type_layout = QHBoxLayout(type_group)
        type_layout.setSpacing(10)

        self.order_type_group = QButtonGroup()
        order_types = ["MARKET", "LIMIT", "SL", "SL-M"]

        for i, order_type in enumerate(order_types):
            radio = QRadioButton(order_type)
            if order_type == "MARKET":
                radio.setChecked(True)
            self.order_type_group.addButton(radio, i)
            type_layout.addWidget(radio)

        layout.addWidget(type_group)

        # Order Details Group
        details_group = QGroupBox("Order Details")
        details_layout = QGridLayout(details_group)
        details_layout.setSpacing(8)

        # Row 0: Quantity and Product
        details_layout.addWidget(QLabel("Quantity:"), 0, 0)
        self.quantity_spinbox = QSpinBox()
        self.quantity_spinbox.setRange(1, 999999)
        self.quantity_spinbox.setValue(1)
        details_layout.addWidget(self.quantity_spinbox, 0, 1)

        details_layout.addWidget(QLabel("Product:"), 0, 2)
        self.product_combo = QComboBox()
        self.product_combo.addItems(["MIS", "NRML"])
        details_layout.addWidget(self.product_combo, 0, 3)

        # Row 1: Price and Validity
        details_layout.addWidget(QLabel("Price:"), 1, 0)
        self.price_spinbox = QDoubleSpinBox()
        self.price_spinbox.setRange(0.01, 999999.99)
        self.price_spinbox.setDecimals(2)
        self.price_spinbox.setValue(self.ltp)
        self.price_spinbox.setEnabled(False)
        details_layout.addWidget(self.price_spinbox, 1, 1)

        details_layout.addWidget(QLabel("Validity:"), 1, 2)
        self.validity_combo = QComboBox()
        self.validity_combo.addItems(["DAY", "IOC"])
        details_layout.addWidget(self.validity_combo, 1, 3)

        # Row 2: Trigger Price
        details_layout.addWidget(QLabel("Trigger:"), 2, 0)
        self.trigger_price_spinbox = QDoubleSpinBox()
        self.trigger_price_spinbox.setRange(0.01, 999999.99)
        self.trigger_price_spinbox.setDecimals(2)
        self.trigger_price_spinbox.setValue(self.ltp)
        self.trigger_price_spinbox.setEnabled(False)
        details_layout.addWidget(self.trigger_price_spinbox, 2, 1, 1, 3)

        layout.addWidget(details_group)

        # SL/Target Group
        sl_target_group = QGroupBox("Stop Loss & Target")
        sl_target_layout = QVBoxLayout(sl_target_group)
        sl_target_layout.setSpacing(8)

        # Stop Loss
        sl_layout = QHBoxLayout()
        self.enable_sl_checkbox = QCheckBox("Stop Loss")
        sl_layout.addWidget(self.enable_sl_checkbox)

        self.sl_type_combo = QComboBox()
        self.sl_type_combo.addItems(["%", "₹", "PTS"])
        self.sl_type_combo.setEnabled(False)
        self.sl_type_combo.setFixedWidth(50)
        sl_layout.addWidget(self.sl_type_combo)

        self.sl_value_spinbox = QDoubleSpinBox()
        self.sl_value_spinbox.setRange(0.01, 9999.99)
        self.sl_value_spinbox.setDecimals(2)
        self.sl_value_spinbox.setValue(2.0)
        self.sl_value_spinbox.setEnabled(False)
        self.sl_value_spinbox.setFixedWidth(70)
        sl_layout.addWidget(self.sl_value_spinbox)

        self.sl_price_label = QLabel("₹0.00")
        self.sl_price_label.setObjectName("priceLabel")
        sl_layout.addWidget(self.sl_price_label)
        sl_layout.addStretch()

        sl_target_layout.addLayout(sl_layout)

        # Target
        target_layout = QHBoxLayout()
        self.enable_target_checkbox = QCheckBox("Target")
        target_layout.addWidget(self.enable_target_checkbox)

        self.target_type_combo = QComboBox()
        self.target_type_combo.addItems(["%", "₹", "PTS"])
        self.target_type_combo.setEnabled(False)
        self.target_type_combo.setFixedWidth(50)
        target_layout.addWidget(self.target_type_combo)

        self.target_value_spinbox = QDoubleSpinBox()
        self.target_value_spinbox.setRange(0.01, 9999.99)
        self.target_value_spinbox.setDecimals(2)
        self.target_value_spinbox.setValue(3.0)
        self.target_value_spinbox.setEnabled(False)
        self.target_value_spinbox.setFixedWidth(70)
        target_layout.addWidget(self.target_value_spinbox)

        self.target_price_label = QLabel("₹0.00")
        self.target_price_label.setObjectName("priceLabel")
        target_layout.addWidget(self.target_price_label)
        target_layout.addStretch()

        sl_target_layout.addLayout(target_layout)
        layout.addWidget(sl_target_group)

        # Total Investment
        self.total_investment_label = QLabel("Margin Required: ₹0.00")
        self.total_investment_label.setObjectName("totalLabel")
        layout.addWidget(self.total_investment_label)

        layout.addStretch()
        return widget

    def _create_bracket_tab(self) -> QWidget:
        """Create bracket order tab."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)

        # Entry Order Group
        entry_group = QGroupBox("Entry Order")
        entry_layout = QGridLayout(entry_group)
        entry_layout.setSpacing(8)

        entry_layout.addWidget(QLabel("Quantity:"), 0, 0)
        self.bracket_quantity_spinbox = QSpinBox()
        self.bracket_quantity_spinbox.setRange(1, 999999)
        self.bracket_quantity_spinbox.setValue(1)
        entry_layout.addWidget(self.bracket_quantity_spinbox, 0, 1)

        entry_layout.addWidget(QLabel("Price:"), 0, 2)
        self.bracket_price_spinbox = QDoubleSpinBox()
        self.bracket_price_spinbox.setRange(0.01, 999999.99)
        self.bracket_price_spinbox.setDecimals(2)
        self.bracket_price_spinbox.setValue(self.ltp)
        entry_layout.addWidget(self.bracket_price_spinbox, 0, 3)

        layout.addWidget(entry_group)

        # Stop Loss Group
        sl_group = QGroupBox("Stop Loss")
        sl_layout = QHBoxLayout(sl_group)
        sl_layout.setSpacing(8)

        self.bracket_sl_type_combo = QComboBox()
        self.bracket_sl_type_combo.addItems(["%", "₹", "PTS"])
        self.bracket_sl_type_combo.setFixedWidth(50)
        sl_layout.addWidget(self.bracket_sl_type_combo)

        self.bracket_sl_value_spinbox = QDoubleSpinBox()
        self.bracket_sl_value_spinbox.setRange(0.01, 9999.99)
        self.bracket_sl_value_spinbox.setDecimals(2)
        self.bracket_sl_value_spinbox.setValue(2.0)
        self.bracket_sl_value_spinbox.setFixedWidth(80)
        sl_layout.addWidget(self.bracket_sl_value_spinbox)

        self.bracket_sl_price_label = QLabel("₹0.00")
        self.bracket_sl_price_label.setObjectName("priceLabel")
        sl_layout.addWidget(self.bracket_sl_price_label)
        sl_layout.addStretch()

        layout.addWidget(sl_group)

        # Target Group
        target_group = QGroupBox("Target")
        target_layout = QHBoxLayout(target_group)
        target_layout.setSpacing(8)

        self.bracket_target_type_combo = QComboBox()
        self.bracket_target_type_combo.addItems(["%", "₹", "PTS"])
        self.bracket_target_type_combo.setFixedWidth(50)
        target_layout.addWidget(self.bracket_target_type_combo)

        self.bracket_target_value_spinbox = QDoubleSpinBox()
        self.bracket_target_value_spinbox.setRange(0.01, 9999.99)
        self.bracket_target_value_spinbox.setDecimals(2)
        self.bracket_target_value_spinbox.setValue(3.0)
        self.bracket_target_value_spinbox.setFixedWidth(80)
        target_layout.addWidget(self.bracket_target_value_spinbox)

        self.bracket_target_price_label = QLabel("₹0.00")
        self.bracket_target_price_label.setObjectName("priceLabel")
        target_layout.addWidget(self.bracket_target_price_label)
        target_layout.addStretch()

        layout.addWidget(target_group)

        # Total Investment
        self.bracket_total_investment_label = QLabel("Margin Required: ₹0.00")
        self.bracket_total_investment_label.setObjectName("totalLabel")
        layout.addWidget(self.bracket_total_investment_label)

        layout.addStretch()
        return widget

    def _create_buttons(self) -> QFrame:
        """Create action buttons."""
        button_frame = QFrame()
        button_frame.setObjectName("buttonFrame")
        button_frame.setFixedHeight(45)

        layout = QHBoxLayout(button_frame)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        # Cancel button
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cancelButton")
        cancel_btn.clicked.connect(self.reject)

        # Update LTP button
        update_ltp_btn = QPushButton("Update LTP")
        update_ltp_btn.setObjectName("updateButton")
        update_ltp_btn.clicked.connect(self._update_ltp)

        # Place Order button
        self.place_order_btn = QPushButton("PLACE ORDER")
        self.place_order_btn.clicked.connect(self._place_order)

        layout.addWidget(cancel_btn)
        layout.addWidget(update_ltp_btn)
        layout.addStretch()
        layout.addWidget(self.place_order_btn)

        return button_frame

    def _apply_compact_styles(self):
        """Apply 2010-2015 style theme."""
        self.setStyleSheet("""
            /* Main Dialog */
            QDialog {
                background: #1e1e1e;
                border: 2px solid #3a3a3a;
                border-radius: 5px;
            }

            /* Header */
            #headerFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                    stop:0 #2a2a2a, stop:1 #1e1e1e);
                border-bottom: 1px solid #3a3a3a;
            }

            #symbolLabel {
                color: #ffffff;
                font-size: 16px;
                font-weight: bold;
                font-family: Arial, sans-serif;
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            }

            #ltpLabel {
                color: #cccccc;
                font-size: 12px;
                font-family: Arial, sans-serif;
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            }

            #closeButton {
                background: #444444;
                border: 1px solid #5a5a5a;
                border-radius: 3px;
                color: #ffffff;
                font-size: 14px;
                font-weight: bold;
                min-width: 22px;
                max-width: 24px;
                padding: 2px;
            }

            #closeButton:hover {
                background: #5a5a5a;
            }

            /* Content Frame */
            #contentFrame {
                background: #1e1e1e;
            }

            /* Tabs */
            QTabWidget#compactTabs::pane {
                border: 1px solid #3a3a3a;
                background: #1e1e1e;
                border-radius: 3px;
            }

            QTabWidget#compactTabs QTabBar::tab {
                background: #2a2a2a;
                color: #cccccc;
                padding: 6px 12px;
                margin-right: 1px;
                border: 1px solid #3a3a3a;
                border-bottom: none;
                border-top-left-radius: 3px;
                border-top-right-radius: 3px;
                font-size: 11px;
                font-weight: bold;
            }

            QTabWidget#compactTabs QTabBar::tab:selected {
                background: #1e1e1e;
                color: #ffffff;
                border-bottom: 1px solid #1e1e1e;
            }

            QTabWidget#compactTabs QTabBar::tab:hover:!selected {
                background: #333333;
            }

            /* Group Boxes */
            QGroupBox {
                color: #ffffff;
                font-size: 11px;
                font-weight: bold;
                border: 1px solid #3a3a3a;
                border-radius: 3px;
                margin-top: 8px;
                padding-top: 5px;
                background: transparent;
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
                background: #1e1e1e;
                color: #ffffff;
                border: none;
            }

            /* Labels */
            QLabel {
                color: #cccccc;
                font-size: 11px;
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            }

            #priceLabel {
                color: #4CAF50;
                font-weight: bold;
                font-size: 11px;
                background: transparent;
                border: none;
                padding: 2px;
            }

            #totalLabel {
                color: #ffffff;
                font-weight: bold;
                font-size: 12px;
                padding: 5px;
                background: #1a1a1a;
                border: 1px solid #3a3a3a;
                border-radius: 3px;
            }

            /* Input Fields */
            QSpinBox, QDoubleSpinBox, QComboBox {
                background: #000000;
                border: 1px solid #3a3a3a;
                border-radius: 3px;
                color: #ffffff;
                padding: 3px;
                font-size: 11px;
                height: 20px;
            }

            QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
                border: 2px solid #666666;
            }

            QSpinBox:disabled, QDoubleSpinBox:disabled {
                background: #1a1a1a;
                color: #666666;
            }

            QSpinBox::up-button, QDoubleSpinBox::up-button,
            QSpinBox::down-button, QDoubleSpinBox::down-button {
                background: #3a3a3a;
                border: none;
                width: 16px;
            }

            QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
            QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
                background: #4a4a4a;
            }

            QComboBox::drop-down {
                border: none;
                width: 20px;
            }

            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 4px solid #cccccc;
            }

            QComboBox QAbstractItemView {
                background: #000000;
                border: 1px solid #3a3a3a;
                color: #ffffff;
                selection-background-color: #333333;
            }

            /* Radio Buttons */
            QRadioButton {
                color: #cccccc;
                font-size: 11px;
                spacing: 5px;
            }

            QRadioButton::indicator {
                width: 12px;
                height: 12px;
                border: 2px solid #4a4a4a;
                border-radius: 6px;
                background: #1a1a1a;
            }

            QRadioButton::indicator:checked {
                background: #666666;
                border: 2px solid #666666;
            }

            /* Checkboxes */
            QCheckBox {
                color: #cccccc;
                font-size: 11px;
                spacing: 5px;
            }

            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 2px solid #4a4a4a;
                border-radius: 2px;
                background: #1a1a1a;
            }

            QCheckBox::indicator:checked {
                background: #666666;
                border: 2px solid #666666;
            }

            /* Buttons */
            #buttonFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                    stop:0 #1e1e1e, stop:1 #1a1a1a);
                border-top: 1px solid #3a3a3a;
            }

            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                    stop:0 #454545, stop:1 #383838);
                border: 1px solid #5a5a5a;
                border-radius: 4px;
                color: #cccccc;
                font-size: 11px;
                font-weight: bold;
                padding: 6px 12px;
                min-width: 60px;
            }

            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                    stop:0 #2a2a2a, stop:1 #3a3a3a);
            }

            #cancelButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                    stop:0 #454545, stop:1 #383838);
                border: 1px solid #5a5a5a;
                color: #cccccc;
            }

            #cancelButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                    stop:0 #F44336, stop:1 #D32F2F);
                border: 1px solid #F44336;
                color: #ffffff;
            }

            #updateButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                    stop:0 #454545, stop:1 #383838);
                border: 1px solid #5a5a5a;
                color: #cccccc;
            }

            #updateButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                    stop:0 #2196F3, stop:1 #1976D2);
                border: 1px solid #2196F3;
                color: #ffffff;
            }
        """)

    def paintEvent(self, event):
        """Simple paint event for basic border."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Simple border
        rect = self.rect()
        painter.setPen(QPen(QColor("#3a3a3a"), 2))
        painter.setBrush(QBrush(QColor("#1e1e1e")))
        painter.drawRoundedRect(rect, 5, 5)

    # Include all the signal handling and calculation methods
    def _connect_signals(self):
        """Connect all signal handlers."""
        self.toggle_switch.toggled.connect(self._on_transaction_type_changed)
        self.order_type_group.buttonToggled.connect(self._on_order_type_changed)
        self.enable_sl_checkbox.toggled.connect(self._on_sl_enabled_changed)
        self.enable_target_checkbox.toggled.connect(self._on_target_enabled_changed)

        # SL and Target value changes
        self.sl_type_combo.currentTextChanged.connect(self._update_sl_calculation)
        self.sl_value_spinbox.valueChanged.connect(self._update_sl_calculation)
        self.target_type_combo.currentTextChanged.connect(self._update_target_calculation)
        self.target_value_spinbox.valueChanged.connect(self._update_target_calculation)

        # Regular order calculations
        self.quantity_spinbox.valueChanged.connect(self._update_regular_total_investment)
        self.price_spinbox.valueChanged.connect(self._update_regular_total_investment)
        self.order_type_group.buttonToggled.connect(self._update_regular_total_investment)

        # Bracket order calculations
        self.bracket_quantity_spinbox.valueChanged.connect(self._update_bracket_total_investment)
        self.bracket_price_spinbox.valueChanged.connect(self._update_bracket_total_investment)
        self.bracket_price_spinbox.valueChanged.connect(self._update_bracket_calculations)
        self.bracket_sl_type_combo.currentTextChanged.connect(self._update_bracket_calculations)
        self.bracket_sl_value_spinbox.valueChanged.connect(self._update_bracket_calculations)
        self.bracket_target_type_combo.currentTextChanged.connect(self._update_bracket_calculations)
        self.bracket_target_value_spinbox.valueChanged.connect(self._update_bracket_calculations)

    def _populate_initial_data(self):
        """Populate form with initial data."""
        if self.order_details:
            self._is_buy = self.order_details.get('transaction_type', 'BUY') == 'BUY'
            self.toggle_switch.set_buy_mode(self._is_buy)
            if 'quantity' in self.order_details:
                self.quantity_spinbox.setValue(self.order_details['quantity'])
        self._update_ui_state()
        self._update_bracket_calculations()
        self._update_regular_total_investment()
        self._update_bracket_total_investment()
        # Ensure colors are set correctly on initialization
        self._update_colors_for_mode()

    def _update_ui_state(self):
        """Update UI elements based on current selections."""
        action = "BUY" if self.toggle_switch.is_buy_mode() else "SELL"
        self.place_order_btn.setText(f"{action}")
        self._update_colors_for_mode()

    def _update_colors_for_mode(self):
        """Update colors based on buy/sell mode."""
        if self._is_buy:
            # Buy mode - Green colors
            accent_color = "#4CAF50"
            dark_accent = "#388E3C"
            light_accent = "#66BB6A"

            # Update place button for buy mode
            self.place_order_btn.setStyleSheet(f"""
                QPushButton {{
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                        stop:0 #454545, stop:1 #383838);
                    border: 1px solid #5a5a5a;
                    border-radius: 4px;
                    color: #cccccc;
                    font-size: 11px;
                    font-weight: bold;
                    padding: 6px 12px;
                    min-width: 100px;
                }}
                QPushButton:hover {{
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                        stop:0 {accent_color}, stop:1 {dark_accent});
                    border: 1px solid {accent_color};
                    color: #ffffff;
                }}
                QPushButton:pressed {{
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                        stop:0 {dark_accent}, stop:1 {accent_color});
                }}
            """)

            # Update focus colors for input fields
            input_focus_style = f"""
                QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
                    border: 2px solid {accent_color};
                }}
                QRadioButton::indicator:checked {{
                    background: {accent_color};
                    border: 2px solid {accent_color};
                }}
                QCheckBox::indicator:checked {{
                    background: {accent_color};
                    border: 2px solid {accent_color};
                }}
            """

        else:
            # Sell mode - Red colors
            accent_color = "#F44336"
            dark_accent = "#D32F2F"
            light_accent = "#EF5350"

            # Update place button for sell mode
            self.place_order_btn.setStyleSheet(f"""
                QPushButton {{
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                        stop:0 #454545, stop:1 #383838);
                    border: 1px solid #5a5a5a;
                    border-radius: 4px;
                    color: #cccccc;
                    font-size: 11px;
                    font-weight: bold;
                    padding: 6px 12px;
                    min-width: 100px;
                }}
                QPushButton:hover {{
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                        stop:0 {accent_color}, stop:1 {dark_accent});
                    border: 1px solid {accent_color};
                    color: #ffffff;
                }}
                QPushButton:pressed {{
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                        stop:0 {dark_accent}, stop:1 {accent_color});
                }}
            """)

            # Update focus colors for input fields
            input_focus_style = f"""
                QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
                    border: 2px solid {accent_color};
                }}
                QRadioButton::indicator:checked {{
                    background: {accent_color};
                    border: 2px solid {accent_color};
                }}
                QCheckBox::indicator:checked {{
                    background: {accent_color};
                    border: 2px solid {accent_color};
                }}
            """

        # Apply the dynamic styles
        current_style = self.styleSheet()
        # Remove old dynamic styles if they exist
        if "/* DYNAMIC_STYLES_START */" in current_style:
            base_style = current_style.split("/* DYNAMIC_STYLES_START */")[0]
        else:
            base_style = current_style

        # Add new dynamic styles
        new_style = base_style + f"""
            /* DYNAMIC_STYLES_START */
            {input_focus_style}

            #priceLabel {{
                color: {accent_color};
            }}

            #totalLabel {{
                color: #ffffff;
            }}
            /* DYNAMIC_STYLES_END */
        """

        self.setStyleSheet(new_style)

    def _on_transaction_type_changed(self, is_buy: bool):
        """Handle buy/sell toggle change."""
        self._is_buy = is_buy
        self._update_ui_state()
        self._update_sl_calculation()
        self._update_target_calculation()
        self._update_bracket_calculations()
        self._update_regular_total_investment()
        self._update_bracket_total_investment()

    def _on_order_type_changed(self, button, checked):
        """Handle order type change."""
        if not checked:
            return
        order_type = button.text()
        self._order_type = order_type
        price_required = order_type in ["LIMIT", "SL"]
        trigger_required = order_type in ["SL", "SL-M"]
        self.price_spinbox.setEnabled(price_required)
        self.trigger_price_spinbox.setEnabled(trigger_required)

        # Set defaults
        if price_required and self.price_spinbox.value() == 0:
            self.price_spinbox.setValue(self.ltp)
        if trigger_required and self.trigger_price_spinbox.value() == 0:
            if self.toggle_switch.is_buy_mode():
                self.trigger_price_spinbox.setValue(self.ltp * 1.01)
            else:
                self.trigger_price_spinbox.setValue(self.ltp * 0.99)
        self._update_regular_total_investment()

    def _on_sl_enabled_changed(self, enabled: bool):
        """Handle stop loss enable/disable."""
        self.sl_type_combo.setEnabled(enabled)
        self.sl_value_spinbox.setEnabled(enabled)
        self._update_sl_calculation()

    def _on_target_enabled_changed(self, enabled: bool):
        """Handle target enable/disable."""
        self.target_type_combo.setEnabled(enabled)
        self.target_value_spinbox.setEnabled(enabled)
        self._update_target_calculation()

    def _update_sl_calculation(self):
        """Calculate and display stop loss price."""
        if not self.enable_sl_checkbox.isChecked():
            self.sl_price_label.setText("₹0.00")
            return

        sl_type = self.sl_type_combo.currentText()
        sl_value = self.sl_value_spinbox.value()
        entry_price = self.price_spinbox.value() if self._order_type in ["LIMIT", "SL"] else self.ltp

        if sl_type == "%":
            if self._is_buy:
                sl_price = entry_price * (1 - sl_value / 100)
            else:
                sl_price = entry_price * (1 + sl_value / 100)
        elif sl_type == "₹":
            sl_price = sl_value
        else:  # PTS
            if self._is_buy:
                sl_price = entry_price - sl_value
            else:
                sl_price = entry_price + sl_value

        self.sl_price_label.setText(f"₹{sl_price:.2f}")

    def _update_target_calculation(self):
        """Calculate and display target price."""
        if not self.enable_target_checkbox.isChecked():
            self.target_price_label.setText("₹0.00")
            return

        target_type = self.target_type_combo.currentText()
        target_value = self.target_value_spinbox.value()
        entry_price = self.price_spinbox.value() if self._order_type in ["LIMIT", "SL"] else self.ltp

        if target_type == "%":
            if self._is_buy:
                target_price = entry_price * (1 + target_value / 100)
            else:
                target_price = entry_price * (1 - target_value / 100)
        elif target_type == "₹":
            target_price = target_value
        else:  # PTS
            if self._is_buy:
                target_price = entry_price + target_value
            else:
                target_price = entry_price - target_value

        self.target_price_label.setText(f"₹{target_price:.2f}")

    def _update_bracket_calculations(self):
        """Update bracket order calculations."""
        entry_price = self.bracket_price_spinbox.value()

        # SL calculation
        sl_type = self.bracket_sl_type_combo.currentText()
        sl_value = self.bracket_sl_value_spinbox.value()

        if sl_type == "%":
            if self._is_buy:
                sl_price = entry_price * (1 - sl_value / 100)
            else:
                sl_price = entry_price * (1 + sl_value / 100)
        elif sl_type == "₹":
            sl_price = sl_value
        else:  # PTS
            if self._is_buy:
                sl_price = entry_price - sl_value
            else:
                sl_price = entry_price + sl_value

        self.bracket_sl_price_label.setText(f"₹{sl_price:.2f}")

        # Target calculation
        target_type = self.bracket_target_type_combo.currentText()
        target_value = self.bracket_target_value_spinbox.value()

        if target_type == "%":
            if self._is_buy:
                target_price = entry_price * (1 + target_value / 100)
            else:
                target_price = entry_price * (1 - target_value / 100)
        elif target_type == "₹":
            target_price = target_value
        else:  # PTS
            if self._is_buy:
                target_price = entry_price + target_value
            else:
                target_price = entry_price - target_value

        self.bracket_target_price_label.setText(f"₹{target_price:.2f}")

    def _update_bracket_total_investment(self):
        """Calculate and update the total investment for bracket order."""
        quantity = self.bracket_quantity_spinbox.value()
        price = self.bracket_price_spinbox.value()
        total_investment = quantity * price
        self.bracket_total_investment_label.setText(f"Margin Required: ₹{total_investment:,.2f}")

    def _update_regular_total_investment(self):
        """Calculate and update the total investment."""
        quantity = self.quantity_spinbox.value()
        price = self.ltp if self._order_type == "MARKET" else self.price_spinbox.value()
        total_investment = quantity * price
        self.total_investment_label.setText(f"Margin Required: ₹{total_investment:,.2f}")

    def _update_ltp(self):
        """Request LTP update."""
        logger.info(f"LTP update requested for {self.symbol}")
        if hasattr(self.parent(), '_get_fresh_ltp'):
            new_ltp = self.parent()._get_fresh_ltp(self.symbol)
            if new_ltp is not None and new_ltp > 0:
                self.update_ltp(new_ltp)
            else:
                QMessageBox.warning(self, "Update Failed", "Could not fetch the latest price.")
        else:
            logger.error("Parent window does not have the '_get_fresh_ltp' method.")
            QMessageBox.critical(self, "Error", "LTP update functionality is not available.")

    def update_ltp(self, new_ltp: float):
        """Update LTP and recalculate dependent values."""
        self.ltp = new_ltp
        self.ltp_label.setText(f"₹{self.ltp:,.2f}")
        if self._order_type == "MARKET":
            self.price_spinbox.setValue(new_ltp)
            self.trigger_price_spinbox.setValue(new_ltp)
        self.bracket_price_spinbox.setValue(new_ltp)
        self._update_sl_calculation()
        self._update_target_calculation()
        self._update_bracket_calculations()
        self._update_regular_total_investment()
        self._update_bracket_total_investment()

    def _place_order(self):
        """Process order placement."""
        current_tab = self.tab_widget.currentIndex()
        if current_tab == 0:
            self._place_regular_order()
        elif current_tab == 1:
            self._place_bracket_order()

    def _place_regular_order(self):
        """Place regular order."""
        try:
            selected_button = self.order_type_group.checkedButton()
            if not selected_button:
                QMessageBox.warning(self, "Error", "Select order type")
                return

            order_type = selected_button.text()
            order_data = {
                "variety": "regular",
                "exchange": "NSE",
                "tradingsymbol": self.symbol,
                "transaction_type": "BUY" if self._is_buy else "SELL",
                "quantity": self.quantity_spinbox.value(),
                "order_type": order_type,
                "product": self.product_combo.currentText(),
                "validity": self.validity_combo.currentText()
            }

            if order_type in ["LIMIT", "SL"]:
                order_data["price"] = f"{self.price_spinbox.value():.2f}"
            if order_type in ["SL", "SL-M"]:
                order_data["trigger_price"] = f"{self.trigger_price_spinbox.value():.2f}"

            # Place the main order
            orders_to_place = [order_data]

            # Add stop loss order if enabled
            if self.enable_sl_checkbox.isChecked():
                sl_price = self._calculate_sl_price()
                if sl_price > 0:
                    sl_order = {
                        "tradingsymbol": self.symbol,
                        "transaction_type": "SELL" if self._is_buy else "BUY",
                        "quantity": order_data["quantity"],
                        "order_type": "SL-M",
                        "trigger_price": f"{sl_price:.2f}",
                        "product": order_data["product"],
                        "validity": order_data["validity"],
                        "tag": "SL"
                    }
                    orders_to_place.append(sl_order)

            # Add target order if enabled
            if self.enable_target_checkbox.isChecked():
                target_price = self._calculate_target_price()
                if target_price > 0:
                    target_order = {
                        "tradingsymbol": self.symbol,
                        "transaction_type": "SELL" if self._is_buy else "BUY",
                        "quantity": order_data["quantity"],
                        "order_type": "LIMIT",
                        "price": f"{target_price:.2f}",
                        "product": order_data["product"],
                        "validity": order_data["validity"],
                        "tag": "TARGET"
                    }
                    orders_to_place.append(target_order)

            # Emit all orders
            for order in orders_to_place:
                logger.info(f"Emitting order: {order}")
                self.order_placed.emit(order)

            self.accept()

        except Exception as e:
            logger.error(f"Error placing order: {e}")
            QMessageBox.critical(self, "Error", f"Failed: {str(e)}")

    def _calculate_sl_price(self):
        """Calculate stop loss price based on current settings."""
        sl_type = self.sl_type_combo.currentText()
        sl_value = self.sl_value_spinbox.value()
        entry_price = self.price_spinbox.value() if self._order_type in ["LIMIT", "SL"] else self.ltp

        if sl_type == "%":
            if self._is_buy:
                return entry_price * (1 - sl_value / 100)
            else:
                return entry_price * (1 + sl_value / 100)
        elif sl_type == "₹":
            return sl_value
        else:  # PTS
            if self._is_buy:
                return entry_price - sl_value
            else:
                return entry_price + sl_value

    def _calculate_target_price(self):
        """Calculate target price based on current settings."""
        target_type = self.target_type_combo.currentText()
        target_value = self.target_value_spinbox.value()
        entry_price = self.price_spinbox.value() if self._order_type in ["LIMIT", "SL"] else self.ltp

        if target_type == "%":
            if self._is_buy:
                return entry_price * (1 + target_value / 100)
            else:
                return entry_price * (1 - target_value / 100)
        elif target_type == "₹":
            return target_value
        else:  # PTS
            if self._is_buy:
                return entry_price + target_value
            else:
                return entry_price - target_value

    def _place_bracket_order(self):
        """Place bracket order."""
        try:
            entry_price = self.bracket_price_spinbox.value()
            quantity = self.bracket_quantity_spinbox.value()

            # Calculate SL and Target
            sl_price = self._calculate_bracket_sl_price()
            target_price = self._calculate_bracket_target_price()

            bracket_order = {
                "tradingsymbol": self.symbol,
                "transaction_type": "BUY" if self._is_buy else "SELL",
                "quantity": quantity,
                "order_type": "LIMIT",
                "price": f"{entry_price:.2f}",
                "product": "MIS",
                "validity": "DAY",
                "squareoff": abs(target_price - entry_price),
                "stoploss": abs(entry_price - sl_price),
                "variety": "bo"
            }

            logger.info(f"Placing bracket order: {bracket_order}")
            self.bracket_order_placed.emit(bracket_order)
            self.accept()

        except Exception as e:
            logger.error(f"Error placing bracket order: {e}")
            QMessageBox.critical(self, "Error", f"Failed: {str(e)}")

    def _calculate_bracket_sl_price(self):
        """Calculate bracket order stop loss price."""
        entry_price = self.bracket_price_spinbox.value()
        sl_type = self.bracket_sl_type_combo.currentText()
        sl_value = self.bracket_sl_value_spinbox.value()

        if sl_type == "%":
            if self._is_buy:
                return entry_price * (1 - sl_value / 100)
            else:
                return entry_price * (1 + sl_value / 100)
        elif sl_type == "₹":
            return sl_value
        else:  # PTS
            if self._is_buy:
                return entry_price - sl_value
            else:
                return entry_price + sl_value

    def _calculate_bracket_target_price(self):
        """Calculate bracket order target price."""
        entry_price = self.bracket_price_spinbox.value()
        target_type = self.bracket_target_type_combo.currentText()
        target_value = self.bracket_target_value_spinbox.value()

        if target_type == "%":
            if self._is_buy:
                return entry_price * (1 + target_value / 100)
            else:
                return entry_price * (1 - target_value / 100)
        elif target_type == "₹":
            return target_value
        else:  # PTS
            if self._is_buy:
                return entry_price + target_value
            else:
                return entry_price - target_value

    # Dragging functionality
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