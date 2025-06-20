import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget, QFrame,
    QLineEdit, QComboBox, QCheckBox, QButtonGroup, QRadioButton, QGroupBox,
    QTabWidget, QSpinBox, QDoubleSpinBox, QGridLayout, QFormLayout, QScrollArea, QMessageBox
)
from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve, QRect
from PySide6.QtGui import QMouseEvent, QShowEvent, QPainter, QPainterPath, QFont
from typing import Dict, Any, Optional
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger(__name__)


class ToggleSwitch(QWidget):
    """Custom toggle switch for Buy/Sell selection with smooth animation."""
    toggled = Signal(bool)  # True for Buy, False for Sell

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(100, 36)
        self._is_buy = True
        self._animation = QPropertyAnimation(self, b"geometry")
        self._animation.setDuration(200)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background
        bg_color = "#0d3b0d" if self._is_buy else "#3b0d0d"
        painter.fillRect(self.rect(), bg_color)

        # Toggle circle
        circle_x = 6 if self._is_buy else 54
        circle_color = "#00b894" if self._is_buy else "#d63031"
        painter.setBrush(circle_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(circle_x, 6, 24, 24)

        # Text
        painter.setPen("#ffffff")
        font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        painter.setFont(font)

        if self._is_buy:
            painter.drawText(34, 22, "BUY")
        else:
            painter.drawText(12, 22, "SELL")

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


class OrderDialog(QDialog):
    """
    Professional-grade order window with comprehensive trading features:
    - Buy/Sell toggle switch
    - Market/Limit/SL/SL-M order types
    - Stop Loss and Target (percentage, price, points)
    - OCO (One-Cancels-Other) orders
    - Bracket orders
    - Product type selection (MIS/NRML)
    - Validity options
    - Risk calculation
    - Advanced order management
    """

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
        self._apply_styles()
        self._connect_signals()
        self._populate_initial_data()

    def _setup_dialog(self):
        """Configure dialog properties."""
        self.setWindowTitle(f"Order Entry - {self.symbol}")
        self.setModal(True)
        self.setMinimumSize(480, 750)
        self.setMaximumSize(480, 900)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def showEvent(self, event: QShowEvent):
        """Center dialog on parent."""
        super().showEvent(event)
        if self.parent():
            parent_geometry = self.parent().geometry()
            self.move(parent_geometry.center() - self.rect().center())

    def _setup_ui(self):
        """Build the complete UI."""
        # Main container with frosted black background
        container = QWidget(self)
        container.setObjectName("mainContainer")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(20, 15, 20, 20)
        container_layout.setSpacing(12)

        # Allow dragging
        container.mousePressEvent = self.mousePressEvent
        container.mouseMoveEvent = self.mouseMoveEvent
        container.mouseReleaseEvent = self.mouseReleaseEvent

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        # Title bar with toggle
        container_layout.addLayout(self._create_title_bar())

        # Symbol and LTP section
        container_layout.addWidget(self._create_symbol_section())

        # Separator
        container_layout.addWidget(QFrame(frameShape=QFrame.Shape.HLine, objectName="divider"))

        # Main order form in tabs
        container_layout.addWidget(self._create_order_tabs())

        # Risk summary
        container_layout.addWidget(self._create_risk_summary())

        # Action buttons
        container_layout.addLayout(self._create_action_buttons())

    def _create_title_bar(self) -> QHBoxLayout:
        """Create title bar with buy/sell toggle."""
        layout = QHBoxLayout()

        title = QLabel("Order Entry")
        title.setObjectName("dialogTitle")

        # Buy/Sell toggle switch
        self.toggle_switch = ToggleSwitch()
        self.toggle_switch.set_buy_mode(self._is_buy)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.clicked.connect(self.reject)

        layout.addWidget(title)
        layout.addStretch()
        layout.addWidget(self.toggle_switch)
        layout.addWidget(close_btn)

        return layout

    def _create_symbol_section(self) -> QWidget:
        """Create symbol display with LTP."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.symbol_label = QLabel(self.symbol)
        self.symbol_label.setObjectName("symbolLabel")

        self.ltp_label = QLabel(f"LTP: ₹{self.ltp:,.2f}")
        self.ltp_label.setObjectName("ltpLabel")

        layout.addWidget(self.symbol_label)
        layout.addWidget(self.ltp_label)

        return widget

    def _create_order_tabs(self) -> QTabWidget:
        """Create tabbed interface for different order types."""
        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("orderTabs")

        # Regular Order Tab
        self.tab_widget.addTab(self._create_regular_order_tab(), "Regular")

        # Bracket Order Tab
        self.tab_widget.addTab(self._create_bracket_order_tab(), "Bracket")

        # OCO Order Tab
        self.tab_widget.addTab(self._create_oco_order_tab(), "OCO")

        return self.tab_widget

    def _create_regular_order_tab(self) -> QWidget:
        """Create regular order form."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(15)

        # Order Type Selection
        order_type_group = QGroupBox("Order Type")
        order_type_layout = QHBoxLayout(order_type_group)

        self.order_type_group = QButtonGroup()
        order_types = ["MARKET", "LIMIT", "SL", "SL-M"]

        for i, order_type in enumerate(order_types):
            radio = QRadioButton(order_type)
            radio.setObjectName("orderTypeRadio")
            if order_type == "MARKET":
                radio.setChecked(True)
            self.order_type_group.addButton(radio, i)
            order_type_layout.addWidget(radio)

        layout.addWidget(order_type_group)

        # Price and Quantity Section
        form_layout = QFormLayout()

        # Quantity
        self.quantity_spinbox = QSpinBox()
        self.quantity_spinbox.setRange(1, 999999)
        self.quantity_spinbox.setValue(1)
        self.quantity_spinbox.setObjectName("quantityInput")
        form_layout.addRow("Quantity:", self.quantity_spinbox)

        # Price (for limit orders)
        self.price_spinbox = QDoubleSpinBox()
        self.price_spinbox.setRange(0.01, 999999.99)
        self.price_spinbox.setDecimals(2)
        self.price_spinbox.setValue(self.ltp)
        self.price_spinbox.setEnabled(False)
        self.price_spinbox.setObjectName("priceInput")
        form_layout.addRow("Price:", self.price_spinbox)

        # Trigger Price (for SL orders)
        self.trigger_price_spinbox = QDoubleSpinBox()
        self.trigger_price_spinbox.setRange(0.01, 999999.99)
        self.trigger_price_spinbox.setDecimals(2)
        self.trigger_price_spinbox.setValue(self.ltp)
        self.trigger_price_spinbox.setEnabled(False)
        self.trigger_price_spinbox.setObjectName("triggerPriceInput")
        form_layout.addRow("Trigger Price:", self.trigger_price_spinbox)

        # Product Type
        self.product_combo = QComboBox()
        self.product_combo.addItems(["MIS", "NRML"])
        self.product_combo.setObjectName("productCombo")
        form_layout.addRow("Product:", self.product_combo)

        # Validity
        self.validity_combo = QComboBox()
        self.validity_combo.addItems(["DAY", "IOC"])
        self.validity_combo.setObjectName("validityCombo")
        form_layout.addRow("Validity:", self.validity_combo)

        layout.addLayout(form_layout)

        # Advanced Options
        advanced_group = QGroupBox("Advanced Options")
        advanced_layout = QVBoxLayout(advanced_group)

        # Stop Loss Section
        sl_frame = QFrame()
        sl_layout = QVBoxLayout(sl_frame)

        self.enable_sl_checkbox = QCheckBox("Enable Stop Loss")
        self.enable_sl_checkbox.setObjectName("enableSLCheckbox")
        sl_layout.addWidget(self.enable_sl_checkbox)

        sl_options_layout = QHBoxLayout()

        self.sl_type_combo = QComboBox()
        self.sl_type_combo.addItems(["Percentage", "Price", "Points"])
        self.sl_type_combo.setEnabled(False)
        sl_options_layout.addWidget(self.sl_type_combo)

        self.sl_value_spinbox = QDoubleSpinBox()
        self.sl_value_spinbox.setRange(0.01, 9999.99)
        self.sl_value_spinbox.setDecimals(2)
        self.sl_value_spinbox.setValue(2.0)
        self.sl_value_spinbox.setEnabled(False)
        sl_options_layout.addWidget(self.sl_value_spinbox)

        sl_layout.addLayout(sl_options_layout)
        advanced_layout.addWidget(sl_frame)

        # Target Section
        target_frame = QFrame()
        target_layout = QVBoxLayout(target_frame)

        self.enable_target_checkbox = QCheckBox("Enable Target")
        self.enable_target_checkbox.setObjectName("enableTargetCheckbox")
        target_layout.addWidget(self.enable_target_checkbox)

        target_options_layout = QHBoxLayout()

        self.target_type_combo = QComboBox()
        self.target_type_combo.addItems(["Percentage", "Price", "Points"])
        self.target_type_combo.setEnabled(False)
        target_options_layout.addWidget(self.target_type_combo)

        self.target_value_spinbox = QDoubleSpinBox()
        self.target_value_spinbox.setRange(0.01, 9999.99)
        self.target_value_spinbox.setDecimals(2)
        self.target_value_spinbox.setValue(3.0)
        self.target_value_spinbox.setEnabled(False)
        target_options_layout.addWidget(self.target_value_spinbox)

        target_layout.addLayout(target_options_layout)
        advanced_layout.addWidget(target_frame)

        layout.addWidget(advanced_group)

        return widget

    def _create_bracket_order_tab(self) -> QWidget:
        """Create bracket order form."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(15)

        info_label = QLabel("Bracket orders combine entry, stop loss, and target in one order")
        info_label.setObjectName("infoLabel")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Entry Order
        entry_group = QGroupBox("Entry Order")
        entry_layout = QFormLayout(entry_group)

        self.bracket_quantity_spinbox = QSpinBox()
        self.bracket_quantity_spinbox.setRange(1, 999999)
        self.bracket_quantity_spinbox.setValue(1)
        entry_layout.addRow("Quantity:", self.bracket_quantity_spinbox)

        self.bracket_price_spinbox = QDoubleSpinBox()
        self.bracket_price_spinbox.setRange(0.01, 999999.99)
        self.bracket_price_spinbox.setDecimals(2)
        self.bracket_price_spinbox.setValue(self.ltp)
        entry_layout.addRow("Entry Price:", self.bracket_price_spinbox)

        layout.addWidget(entry_group)

        # Stop Loss
        sl_group = QGroupBox("Stop Loss")
        sl_layout = QGridLayout(sl_group)

        sl_layout.addWidget(QLabel("Type:"), 0, 0)
        self.bracket_sl_type_combo = QComboBox()
        self.bracket_sl_type_combo.addItems(["Percentage", "Price", "Points"])
        sl_layout.addWidget(self.bracket_sl_type_combo, 0, 1)

        sl_layout.addWidget(QLabel("Value:"), 1, 0)
        self.bracket_sl_value_spinbox = QDoubleSpinBox()
        self.bracket_sl_value_spinbox.setRange(0.01, 9999.99)
        self.bracket_sl_value_spinbox.setDecimals(2)
        self.bracket_sl_value_spinbox.setValue(2.0)
        sl_layout.addWidget(self.bracket_sl_value_spinbox, 1, 1)

        self.bracket_sl_price_label = QLabel("SL Price: ₹0.00")
        self.bracket_sl_price_label.setObjectName("calculatedPrice")
        sl_layout.addWidget(self.bracket_sl_price_label, 2, 0, 1, 2)

        layout.addWidget(sl_group)

        # Target
        target_group = QGroupBox("Target")
        target_layout = QGridLayout(target_group)

        target_layout.addWidget(QLabel("Type:"), 0, 0)
        self.bracket_target_type_combo = QComboBox()
        self.bracket_target_type_combo.addItems(["Percentage", "Price", "Points"])
        target_layout.addWidget(self.bracket_target_type_combo, 0, 1)

        target_layout.addWidget(QLabel("Value:"), 1, 0)
        self.bracket_target_value_spinbox = QDoubleSpinBox()
        self.bracket_target_value_spinbox.setRange(0.01, 9999.99)
        self.bracket_target_value_spinbox.setDecimals(2)
        self.bracket_target_value_spinbox.setValue(3.0)
        target_layout.addWidget(self.bracket_target_value_spinbox, 1, 1)

        self.bracket_target_price_label = QLabel("Target Price: ₹0.00")
        self.bracket_target_price_label.setObjectName("calculatedPrice")
        target_layout.addWidget(self.bracket_target_price_label, 2, 0, 1, 2)

        layout.addWidget(target_group)

        return widget

    def _create_oco_order_tab(self) -> QWidget:
        """Create OCO (One-Cancels-Other) order form."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(15)

        info_label = QLabel("OCO orders place two orders simultaneously - when one executes, the other is cancelled")
        info_label.setObjectName("infoLabel")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Common settings
        common_group = QGroupBox("Common Settings")
        common_layout = QFormLayout(common_group)

        self.oco_quantity_spinbox = QSpinBox()
        self.oco_quantity_spinbox.setRange(1, 999999)
        self.oco_quantity_spinbox.setValue(1)
        common_layout.addRow("Quantity:", self.oco_quantity_spinbox)

        layout.addWidget(common_group)

        # Order 1 (Higher Price)
        order1_group = QGroupBox("Order 1 (Buy Stop / Sell Limit)")
        order1_layout = QFormLayout(order1_group)

        self.oco_price1_spinbox = QDoubleSpinBox()
        self.oco_price1_spinbox.setRange(0.01, 999999.99)
        self.oco_price1_spinbox.setDecimals(2)
        self.oco_price1_spinbox.setValue(self.ltp * 1.02)
        order1_layout.addRow("Price 1:", self.oco_price1_spinbox)

        layout.addWidget(order1_group)

        # Order 2 (Lower Price)
        order2_group = QGroupBox("Order 2 (Buy Limit / Sell Stop)")
        order2_layout = QFormLayout(order2_group)

        self.oco_price2_spinbox = QDoubleSpinBox()
        self.oco_price2_spinbox.setRange(0.01, 999999.99)
        self.oco_price2_spinbox.setDecimals(2)
        self.oco_price2_spinbox.setValue(self.ltp * 0.98)
        order2_layout.addRow("Price 2:", self.oco_price2_spinbox)

        layout.addWidget(order2_group)

        return widget

    def _create_risk_summary(self) -> QWidget:
        """Create risk calculation summary."""
        widget = QWidget(objectName="riskSummary")
        layout = QVBoxLayout(widget)
        layout.setSpacing(8)

        title = QLabel("Risk Summary")
        title.setObjectName("riskTitle")
        layout.addWidget(title)

        # Risk metrics
        metrics_layout = QGridLayout()

        self.investment_label = QLabel("Investment: ₹0.00")
        self.investment_label.setObjectName("riskValue")
        metrics_layout.addWidget(self.investment_label, 0, 0)

        self.max_loss_label = QLabel("Max Loss: ₹0.00")
        self.max_loss_label.setObjectName("riskLoss")
        metrics_layout.addWidget(self.max_loss_label, 0, 1)

        self.max_profit_label = QLabel("Max Profit: ₹0.00")
        self.max_profit_label.setObjectName("riskProfit")
        metrics_layout.addWidget(self.max_profit_label, 1, 0)

        self.risk_reward_label = QLabel("Risk:Reward = 1:0")
        self.risk_reward_label.setObjectName("riskRatio")
        metrics_layout.addWidget(self.risk_reward_label, 1, 1)

        layout.addLayout(metrics_layout)

        return widget

    def _create_action_buttons(self) -> QHBoxLayout:
        """Create action buttons."""
        layout = QHBoxLayout()
        layout.setSpacing(10)

        # Cancel button
        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.clicked.connect(self.reject)

        # Modify LTP button
        modify_ltp_btn = QPushButton("UPDATE LTP")
        modify_ltp_btn.setObjectName("secondaryButton")
        modify_ltp_btn.clicked.connect(self._update_ltp)

        # Place Order button
        self.place_order_btn = QPushButton("PLACE ORDER")
        self.place_order_btn.setObjectName("primaryButton")
        self.place_order_btn.clicked.connect(self._place_order)

        layout.addWidget(cancel_btn)
        layout.addWidget(modify_ltp_btn)
        layout.addStretch()
        layout.addWidget(self.place_order_btn)

        return layout

    def _connect_signals(self):
        """Connect all signal handlers."""
        # Toggle switch
        self.toggle_switch.toggled.connect(self._on_transaction_type_changed)

        # Order type radio buttons
        self.order_type_group.buttonToggled.connect(self._on_order_type_changed)

        # Price and quantity changes
        self.quantity_spinbox.valueChanged.connect(self._update_risk_summary)
        self.price_spinbox.valueChanged.connect(self._update_risk_summary)

        # Stop loss and target checkboxes
        self.enable_sl_checkbox.toggled.connect(self._on_sl_enabled_changed)
        self.enable_target_checkbox.toggled.connect(self._on_target_enabled_changed)

        # SL and Target value changes
        self.sl_type_combo.currentTextChanged.connect(self._update_sl_calculation)
        self.sl_value_spinbox.valueChanged.connect(self._update_sl_calculation)
        self.target_type_combo.currentTextChanged.connect(self._update_target_calculation)
        self.target_value_spinbox.valueChanged.connect(self._update_target_calculation)

        # Bracket order calculations
        self.bracket_price_spinbox.valueChanged.connect(self._update_bracket_calculations)
        self.bracket_sl_type_combo.currentTextChanged.connect(self._update_bracket_calculations)
        self.bracket_sl_value_spinbox.valueChanged.connect(self._update_bracket_calculations)
        self.bracket_target_type_combo.currentTextChanged.connect(self._update_bracket_calculations)
        self.bracket_target_value_spinbox.valueChanged.connect(self._update_bracket_calculations)

    def _populate_initial_data(self):
        """Populate form with initial data."""
        if self.order_details:
            # Set transaction type
            self._is_buy = self.order_details.get('transaction_type', 'BUY') == 'BUY'
            self.toggle_switch.set_buy_mode(self._is_buy)

            # Set quantity
            if 'quantity' in self.order_details:
                self.quantity_spinbox.setValue(self.order_details['quantity'])

        # Update UI state
        self._update_ui_state()
        self._update_risk_summary()

    def _update_ui_state(self):
        """Update UI elements based on current selections."""
        # Update button text based on buy/sell
        action = "BUY" if self.toggle_switch.is_buy_mode() else "SELL"
        self.place_order_btn.setText(f"PLACE {action} ORDER")

        # Update colors
        if self.toggle_switch.is_buy_mode():
            self.place_order_btn.setObjectName("primaryButtonBuy")
        else:
            self.place_order_btn.setObjectName("primaryButtonSell")

        self.place_order_btn.style().unpolish(self.place_order_btn)
        self.place_order_btn.style().polish(self.place_order_btn)

    def _on_transaction_type_changed(self, is_buy: bool):
        """Handle buy/sell toggle change."""
        self._is_buy = is_buy
        self._update_ui_state()
        self._update_risk_summary()

    def _on_order_type_changed(self, button, checked):
        """Handle order type change with proper field enabling."""
        if not checked:
            return

        order_type = button.text()
        self._order_type = order_type

        # Enable/disable price fields based on order type
        price_required = order_type in ["LIMIT", "SL"]
        trigger_required = order_type in ["SL", "SL-M"]

        self.price_spinbox.setEnabled(price_required)
        self.trigger_price_spinbox.setEnabled(trigger_required)

        # Set default values
        if price_required and self.price_spinbox.value() == 0:
            self.price_spinbox.setValue(self.ltp)

        if trigger_required and self.trigger_price_spinbox.value() == 0:
            # Set reasonable default trigger prices
            if self.toggle_switch.is_buy_mode():
                self.trigger_price_spinbox.setValue(self.ltp * 1.01)  # 1% above for buy SL
            else:
                self.trigger_price_spinbox.setValue(self.ltp * 0.99)  # 1% below for sell SL

        self._update_risk_summary()

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
        """Calculate and display stop loss price with validation."""
        try:
            if not self.enable_sl_checkbox.isChecked():
                self._sl_price = 0
                return

            sl_type = self.sl_type_combo.currentText()
            sl_value = self.sl_value_spinbox.value()

            # Get entry price
            if self._order_type in ["LIMIT", "SL"]:
                entry_price = self.price_spinbox.value()
            else:
                entry_price = self.ltp

            if entry_price <= 0:
                entry_price = self.ltp

            # Calculate SL price based on type
            if sl_type == "Percentage":
                if self.toggle_switch.is_buy_mode():
                    sl_price = entry_price * (1 - sl_value / 100)
                else:
                    sl_price = entry_price * (1 + sl_value / 100)
            elif sl_type == "Price":
                sl_price = sl_value
            else:  # Points
                if self.toggle_switch.is_buy_mode():
                    sl_price = entry_price - sl_value
                else:
                    sl_price = entry_price + sl_value

            # Validate SL price
            if sl_price <= 0:
                sl_price = entry_price * 0.95 if self.toggle_switch.is_buy_mode() else entry_price * 1.05

            self._sl_price = sl_price
            self._update_risk_summary()

        except Exception as e:
            logger.error(f"Error calculating SL price: {e}")
            self._sl_price = 0

    def _update_target_calculation(self):
        """Calculate and display target price with validation."""
        try:
            if not self.enable_target_checkbox.isChecked():
                self._target_price = 0
                return

            target_type = self.target_type_combo.currentText()
            target_value = self.target_value_spinbox.value()

            # Get entry price
            if self._order_type in ["LIMIT", "SL"]:
                entry_price = self.price_spinbox.value()
            else:
                entry_price = self.ltp

            if entry_price <= 0:
                entry_price = self.ltp

            # Calculate target price based on type
            if target_type == "Percentage":
                if self.toggle_switch.is_buy_mode():
                    target_price = entry_price * (1 + target_value / 100)
                else:
                    target_price = entry_price * (1 - target_value / 100)
            elif target_type == "Price":
                target_price = target_value
            else:  # Points
                if self.toggle_switch.is_buy_mode():
                    target_price = entry_price + target_value
                else:
                    target_price = entry_price - target_value

            # Validate target price
            if target_price <= 0:
                target_price = entry_price * 1.05 if self.toggle_switch.is_buy_mode() else entry_price * 0.95

            self._target_price = target_price
            self._update_risk_summary()

        except Exception as e:
            logger.error(f"Error calculating target price: {e}")
            self._target_price = 0

    def _update_bracket_calculations(self):
        """Update bracket order SL and target price calculations."""
        entry_price = self.bracket_price_spinbox.value()

        # Calculate SL price
        sl_type = self.bracket_sl_type_combo.currentText()
        sl_value = self.bracket_sl_value_spinbox.value()

        if sl_type == "Percentage":
            if self._is_buy:
                sl_price = entry_price * (1 - sl_value / 100)
            else:
                sl_price = entry_price * (1 + sl_value / 100)
        elif sl_type == "Price":
            sl_price = sl_value
        else:  # Points
            if self._is_buy:
                sl_price = entry_price - sl_value
            else:
                sl_price = entry_price + sl_value

        self.bracket_sl_price_label.setText(f"SL Price: ₹{max(0.01, sl_price):,.2f}")

        # Calculate target price
        target_type = self.bracket_target_type_combo.currentText()
        target_value = self.bracket_target_value_spinbox.value()

        if target_type == "Percentage":
            if self._is_buy:
                target_price = entry_price * (1 + target_value / 100)
            else:
                target_price = entry_price * (1 - target_value / 100)
        elif target_type == "Price":
            target_price = target_value
        else:  # Points
            if self._is_buy:
                target_price = entry_price + target_value
            else:
                target_price = entry_price - target_value

        self.bracket_target_price_label.setText(f"Target Price: ₹{max(0.01, target_price):,.2f}")

    def _update_risk_summary(self):
        """Update risk calculation display with better error handling."""
        try:
            quantity = self.quantity_spinbox.value()

            if self._order_type in ["LIMIT", "SL"]:
                entry_price = self.price_spinbox.value()
            else:
                entry_price = self.ltp

            if entry_price <= 0:
                entry_price = self.ltp

            investment = quantity * entry_price
            self.investment_label.setText(f"Investment: ₹{investment:,.2f}")

            max_loss = 0
            max_profit = 0

            # Calculate max loss (if SL is enabled)
            if hasattr(self, '_sl_price') and self.enable_sl_checkbox.isChecked() and self._sl_price > 0:
                if self.toggle_switch.is_buy_mode():
                    max_loss = (entry_price - self._sl_price) * quantity
                else:
                    max_loss = (self._sl_price - entry_price) * quantity

            # Calculate max profit (if Target is enabled)
            if hasattr(self, '_target_price') and self.enable_target_checkbox.isChecked() and self._target_price > 0:
                if self.toggle_switch.is_buy_mode():
                    max_profit = (self._target_price - entry_price) * quantity
                else:
                    max_profit = (entry_price - self._target_price) * quantity

            self.max_loss_label.setText(f"Max Loss: ₹{max_loss:,.2f}")
            self.max_profit_label.setText(f"Max Profit: ₹{max_profit:,.2f}")

            # Risk-Reward ratio
            if max_loss > 0 and max_profit > 0:
                ratio = max_profit / max_loss
                self.risk_reward_label.setText(f"Risk:Reward = 1:{ratio:.2f}")
            else:
                self.risk_reward_label.setText("Risk:Reward = N/A")

        except Exception as e:
            logger.error(f"Error updating risk summary: {e}")
            # Set default values on error
            self.investment_label.setText("Investment: ₹0.00")
            self.max_loss_label.setText("Max Loss: ₹0.00")
            self.max_profit_label.setText("Max Profit: ₹0.00")
            self.risk_reward_label.setText("Risk:Reward = N/A")

    def _update_ltp(self):
        """Request LTP update from parent."""
        # This would typically emit a signal to request fresh LTP
        # For now, we'll just log the request
        logger.info(f"LTP update requested for {self.symbol}")

    def _place_order(self):
        """Process order placement based on current tab."""
        current_tab = self.tab_widget.currentIndex()

        if current_tab == 0:  # Regular Order
            self._place_regular_order()
        elif current_tab == 1:  # Bracket Order
            self._place_bracket_order()
        elif current_tab == 2:  # OCO Order
            self._place_oco_order()

    def _place_regular_order(self):
        """Place a regular order with proper validation."""
        try:
            # Get selected order type
            selected_button = self.order_type_group.checkedButton()
            if not selected_button:
                QMessageBox.warning(self, "Order Type Required", "Please select an order type.")
                return

            order_type = selected_button.text()

            # Build base order data
            order_data = {
                "tradingsymbol": self.symbol,
                "transaction_type": "BUY" if self.toggle_switch.is_buy_mode() else "SELL",
                "quantity": self.quantity_spinbox.value(),
                "order_type": order_type,
                "product": self.product_combo.currentText(),
                "validity": self.validity_combo.currentText()
            }

            # Add price for limit orders
            if order_type in ["LIMIT", "SL"]:
                price = self.price_spinbox.value()
                if price <= 0:
                    QMessageBox.warning(self, "Invalid Price", "Please enter a valid price for limit orders.")
                    return
                order_data["price"] = price

            # Add trigger price for SL orders
            if order_type in ["SL", "SL-M"]:
                trigger_price = self.trigger_price_spinbox.value()
                if trigger_price <= 0:
                    QMessageBox.warning(self, "Invalid Trigger Price",
                                        "Please enter a valid trigger price for SL orders.")
                    return
                order_data["trigger_price"] = trigger_price

            # Validate price relationships for SL orders
            if order_type in ["SL", "SL-M"]:
                price = order_data.get("price", self.ltp)
                trigger_price = order_data["trigger_price"]

                if order_data["transaction_type"] == "BUY":
                    # For buy SL: trigger should be above current LTP
                    if trigger_price <= self.ltp:
                        QMessageBox.warning(self, "Invalid SL Setup",
                                            "For buy stop loss, trigger price should be above current LTP.")
                        return
                else:
                    # For sell SL: trigger should be below current LTP
                    if trigger_price >= self.ltp:
                        QMessageBox.warning(self, "Invalid SL Setup",
                                            "For sell stop loss, trigger price should be below current LTP.")
                        return

            # Place the main order
            orders_to_place = [order_data]

            # Add stop loss order if enabled
            if self.enable_sl_checkbox.isChecked() and hasattr(self, '_sl_price'):
                sl_order = {
                    "tradingsymbol": self.symbol,
                    "transaction_type": "SELL" if order_data["transaction_type"] == "BUY" else "BUY",
                    "quantity": order_data["quantity"],
                    "order_type": "SL-M",
                    "trigger_price": self._sl_price,
                    "product": order_data["product"],
                    "validity": order_data["validity"],
                    "tag": "SL"
                }
                orders_to_place.append(sl_order)

            # Add target order if enabled
            if self.enable_target_checkbox.isChecked() and hasattr(self, '_target_price'):
                target_order = {
                    "tradingsymbol": self.symbol,
                    "transaction_type": "SELL" if order_data["transaction_type"] == "BUY" else "BUY",
                    "quantity": order_data["quantity"],
                    "order_type": "LIMIT",
                    "price": self._target_price,
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
            logger.error(f"Error placing regular order: {e}")
            QMessageBox.critical(self, "Order Error", f"Failed to place order: {str(e)}")

    def _place_bracket_order(self):
        """Place a bracket order with validation."""
        try:
            entry_price = self.bracket_price_spinbox.value()
            quantity = self.bracket_quantity_spinbox.value()

            if entry_price <= 0 or quantity <= 0:
                QMessageBox.warning(self, "Invalid Input", "Please enter valid price and quantity.")
                return

            # Calculate SL and Target prices
            sl_type = self.bracket_sl_type_combo.currentText()
            sl_value = self.bracket_sl_value_spinbox.value()
            target_type = self.bracket_target_type_combo.currentText()
            target_value = self.bracket_target_value_spinbox.value()

            is_buy = self.toggle_switch.is_buy_mode()

            # Calculate SL price
            if sl_type == "Percentage":
                sl_price = entry_price * (1 - sl_value / 100) if is_buy else entry_price * (1 + sl_value / 100)
            elif sl_type == "Price":
                sl_price = sl_value
            else:  # Points
                sl_price = entry_price - sl_value if is_buy else entry_price + sl_value

            # Calculate target price
            if target_type == "Percentage":
                target_price = entry_price * (1 + target_value / 100) if is_buy else entry_price * (
                            1 - target_value / 100)
            elif target_type == "Price":
                target_price = target_value
            else:  # Points
                target_price = entry_price + target_value if is_buy else entry_price - target_value

            # Validate prices
            if sl_price <= 0 or target_price <= 0:
                QMessageBox.warning(self, "Invalid Prices", "Calculated SL or target price is invalid.")
                return

            # Validate price relationships
            if is_buy:
                if sl_price >= entry_price:
                    QMessageBox.warning(self, "Invalid SL", "Stop loss should be below entry price for buy orders.")
                    return
                if target_price <= entry_price:
                    QMessageBox.warning(self, "Invalid Target", "Target should be above entry price for buy orders.")
                    return
            else:
                if sl_price <= entry_price:
                    QMessageBox.warning(self, "Invalid SL", "Stop loss should be above entry price for sell orders.")
                    return
                if target_price >= entry_price:
                    QMessageBox.warning(self, "Invalid Target", "Target should be below entry price for sell orders.")
                    return

            # Create bracket order data
            bracket_order = {
                "tradingsymbol": self.symbol,
                "transaction_type": "BUY" if is_buy else "SELL",
                "quantity": quantity,
                "order_type": "LIMIT",
                "price": entry_price,
                "product": "MIS",  # Bracket orders are typically intraday
                "validity": "DAY",
                "squareoff": abs(target_price - entry_price),
                "stoploss": abs(entry_price - sl_price),
                "variety": "bo"  # Bracket Order
            }

            logger.info(f"Placing bracket order: {bracket_order}")
            self.bracket_order_placed.emit(bracket_order)
            self.accept()

        except Exception as e:
            logger.error(f"Error placing bracket order: {e}")
            QMessageBox.critical(self, "Bracket Order Error", f"Failed to place bracket order: {str(e)}")

    def _place_oco_order(self):
        """Place OCO (One-Cancels-Other) orders with validation."""
        try:
            quantity = self.oco_quantity_spinbox.value()
            price1 = self.oco_price1_spinbox.value()
            price2 = self.oco_price2_spinbox.value()

            if quantity <= 0 or price1 <= 0 or price2 <= 0:
                QMessageBox.warning(self, "Invalid Input", "Please enter valid quantity and prices.")
                return

            if price1 == price2:
                QMessageBox.warning(self, "Invalid Prices", "OCO prices must be different.")
                return

            is_buy = self.toggle_switch.is_buy_mode()

            # Determine order types based on current price and target prices
            def get_order_type_and_trigger(target_price, is_buy_order):
                if is_buy_order:
                    if target_price > self.ltp:
                        return "SL", target_price, target_price
                    else:
                        return "LIMIT", target_price, None
                else:
                    if target_price < self.ltp:
                        return "SL", target_price, target_price
                    else:
                        return "LIMIT", target_price, None

            # Create first order
            order1_type, order1_price, order1_trigger = get_order_type_and_trigger(price1, is_buy)
            order1 = {
                "tradingsymbol": self.symbol,
                "transaction_type": "BUY" if is_buy else "SELL",
                "quantity": quantity,
                "order_type": order1_type,
                "price": order1_price,
                "product": "MIS",
                "validity": "DAY",
                "tag": "OCO1"
            }

            if order1_trigger:
                order1["trigger_price"] = order1_trigger

            # Create second order
            order2_type, order2_price, order2_trigger = get_order_type_and_trigger(price2, is_buy)
            order2 = {
                "tradingsymbol": self.symbol,
                "transaction_type": "BUY" if is_buy else "SELL",
                "quantity": quantity,
                "order_type": order2_type,
                "price": order2_price,
                "product": "MIS",
                "validity": "DAY",
                "tag": "OCO2"
            }

            if order2_trigger:
                order2["trigger_price"] = order2_trigger

            # Emit both orders
            logger.info(f"Placing OCO order 1: {order1}")
            logger.info(f"Placing OCO order 2: {order2}")

            self.order_placed.emit(order1)
            self.order_placed.emit(order2)

            self.accept()

        except Exception as e:
            logger.error(f"Error placing OCO orders: {e}")
            QMessageBox.critical(self, "OCO Order Error", f"Failed to place OCO orders: {str(e)}")

    def update_ltp(self, new_ltp: float):
        """Update LTP and recalculate dependent values."""
        self.ltp = new_ltp
        self.ltp_label.setText(f"LTP: ₹{self.ltp:,.2f}")

        # Update default prices
        if self._order_type == "MARKET":
            self.price_spinbox.setValue(new_ltp)
            self.trigger_price_spinbox.setValue(new_ltp)

        self.bracket_price_spinbox.setValue(new_ltp)
        self.oco_price1_spinbox.setValue(new_ltp * 1.02)
        self.oco_price2_spinbox.setValue(new_ltp * 0.98)

        self._update_risk_summary()
        self._update_bracket_calculations()

    def _apply_styles(self):
        """Apply professional dark theme styling."""
        self.setStyleSheet("""
            /* Main Container */
            #mainContainer {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #0d0d0d, stop:1 #050505);
                border: 1px solid #1a1a1a;
                border-radius: 12px;
                font-family: "Segoe UI", sans-serif;
            }

            /* Title and Labels */
            #dialogTitle {
                color: #ffffff;
                font-size: 16px;
                font-weight: 600;
                padding: 0px;
            }

            #symbolLabel {
                color: #ffffff;
                font-size: 24px;
                font-weight: 300;
                margin: 0px;
            }

            #ltpLabel {
                color: #8a8a9e;
                font-size: 14px;
                font-weight: 500;
                margin: 0px;
            }

            #infoLabel {
                color: #6a9cff;
                font-size: 12px;
                font-style: italic;
                padding: 8px;
                background-color: rgba(106, 156, 255, 0.1);
                border-radius: 4px;
                border-left: 3px solid #6a9cff;
            }

            #calculatedPrice {
                color: #00b894;
                font-size: 12px;
                font-weight: 600;
                margin-top: 4px;
            }

            /* Close Button */
            #closeButton {
                background: transparent;
                border: none;
                color: #8a8a9e;
                font-size: 16px;
                font-weight: bold;
                padding: 4px 8px;
                border-radius: 4px;
            }
            #closeButton:hover {
                color: #d63031;
                background-color: rgba(214, 48, 49, 0.1);
            }

            /* Divider */
            #divider {
                border: none;
                background-color: #1a1a1a;
                height: 1px;
                margin: 8px 0px;
            }

            /* Tabs */
            QTabWidget::pane {
                border: 1px solid #2a2a2a;
                border-radius: 6px;
                background-color: #0f0f0f;
                padding: 8px;
            }

            QTabBar::tab {
                background-color: #1a1a1a;
                color: #8a8a9e;
                padding: 8px 16px;
                margin-right: 2px;
                border-radius: 6px 6px 0px 0px;
                font-weight: 500;
                font-size: 12px;
            }

            QTabBar::tab:selected {
                background-color: #2a2a2a;
                color: #ffffff;
            }

            QTabBar::tab:hover:!selected {
                background-color: #202020;
                color: #e0e0e0;
            }

            /* Group Boxes */
            QGroupBox {
                color: #a0c0ff;
                font-weight: 600;
                font-size: 12px;
                border: 1px solid #2a2a2a;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 8px;
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0px 4px;
            }

            /* Radio Buttons */
            #orderTypeRadio {
                color: #e0e0e0;
                font-size: 11px;
                padding: 4px;
            }

            #orderTypeRadio::indicator {
                width: 14px;
                height: 14px;
                border-radius: 7px;
                border: 2px solid #4a4a4a;
                background-color: #1a1a1a;
            }

            #orderTypeRadio::indicator:checked {
                background-color: #6a9cff;
                border: 2px solid #6a9cff;
            }

            #orderTypeRadio::indicator:hover {
                border: 2px solid #8ab4ff;
            }

            /* Input Fields */
            QSpinBox, QDoubleSpinBox {
                background-color: #1a1a1a;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                padding: 6px 8px;
                color: #ffffff;
                font-size: 12px;
                selection-background-color: #6a9cff;
            }

            QSpinBox:focus, QDoubleSpinBox:focus {
                border: 1px solid #6a9cff;
                background-color: #1f1f1f;
            }

            QSpinBox:disabled, QDoubleSpinBox:disabled {
                background-color: #0f0f0f;
                color: #4a4a4a;
                border: 1px solid #2a2a2a;
            }

            QSpinBox::up-button, QDoubleSpinBox::up-button {
                border: none;
                border-radius: 2px;
                background-color: #2a2a2a;
                width: 16px;
            }

            QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover {
                background-color: #3a3a3a;
            }

            QSpinBox::down-button, QDoubleSpinBox::down-button {
                border: none;
                border-radius: 2px;
                background-color: #2a2a2a;
                width: 16px;
            }

            QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
                background-color: #3a3a3a;
            }

            /* Combo Boxes */
            QComboBox {
                background-color: #1a1a1a;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                padding: 6px 8px;
                color: #ffffff;
                font-size: 12px;
                min-width: 80px;
            }

            QComboBox:focus {
                border: 1px solid #6a9cff;
                background-color: #1f1f1f;
            }

            QComboBox:disabled {
                background-color: #0f0f0f;
                color: #4a4a4a;
                border: 1px solid #2a2a2a;
            }

            QComboBox::drop-down {
                border: none;
                width: 20px;
            }

            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 6px solid #8a8a9e;
                margin-right: 6px;
            }

            QComboBox QAbstractItemView {
                background-color: #1a1a1a;
                border: 1px solid #3a3a3a;
                color: #ffffff;
                selection-background-color: #6a9cff;
                selection-color: #ffffff;
            }

            /* Checkboxes */
            QCheckBox {
                color: #e0e0e0;
                font-size: 12px;
                font-weight: 500;
            }

            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 2px solid #4a4a4a;
                border-radius: 3px;
                background-color: #1a1a1a;
            }

            QCheckBox::indicator:checked {
                background-color: #6a9cff;
                border: 2px solid #6a9cff;
                image: url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTIiIGhlaWdodD0iMTIiIHZpZXdCb3g9IjAgMCAxMiAxMiIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTEwIDNMNC41IDguNUwyIDYiIHN0cm9rZT0id2hpdGUiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIi8+Cjwvc3ZnPgo=);
            }

            QCheckBox::indicator:hover {
                border: 2px solid #8ab4ff;
            }

            /* Risk Summary */
            #riskSummary {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1a1a2e, stop:1 #16213e);
                border: 1px solid #2a2a4a;
                border-radius: 8px;
                padding: 12px;
                margin: 8px 0px;
            }

            #riskTitle {
                color: #a0c0ff;
                font-size: 14px;
                font-weight: 600;
                margin-bottom: 8px;
            }

            #riskValue {
                color: #e0e0e0;
                font-size: 12px;
                font-weight: 500;
            }

            #riskLoss {
                color: #ff6b6b;
                font-size: 12px;
                font-weight: 600;
            }

            #riskProfit {
                color: #51cf66;
                font-size: 12px;
                font-weight: 600;
            }

            #riskRatio {
                color: #ffd93d;
                font-size: 12px;
                font-weight: 600;
            }

            /* Buttons */
            QPushButton {
                font-weight: 600;
                border-radius: 6px;
                padding: 10px 16px;
                border: none;
                font-size: 12px;
                text-transform: uppercase;
            }

            #secondaryButton {
                background-color: #2a2a2a;
                color: #e0e0e0;
                border: 1px solid #3a3a3a;
            }

            #secondaryButton:hover {
                background-color: #3a3a3a;
                color: #ffffff;
            }

            #secondaryButton:pressed {
                background-color: #1a1a1a;
            }

            #primaryButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #6a9cff, stop:1 #4a7cdf);
                color: #ffffff;
                font-weight: 700;
            }

            #primaryButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #7babff, stop:1 #5b8dff);
            }

            #primaryButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #5a8cef, stop:1 #3a6ccf);
            }

            #primaryButtonBuy {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #00b894, stop:1 #00a085);
                color: #ffffff;
                font-weight: 700;
            }

            #primaryButtonBuy:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #00d2a2, stop:1 #00b894);
            }

            #primaryButtonSell {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #d63031, stop:1 #c92a2a);
                color: #ffffff;
                font-weight: 700;
            }

            #primaryButtonSell:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #e74c3c, stop:1 #d63031);
            }

            /* Enable/disable states for checkboxes */
            #enableSLCheckbox, #enableTargetCheckbox {
                color: #ffd93d;
                font-weight: 600;
            }

            /* Form labels */
            QLabel {
                color: #a0a0a0;
                font-size: 11px;
                font-weight: 500;
            }

            /* Scrollbar styling for potential scroll areas */
            QScrollBar:vertical {
                background-color: #1a1a1a;
                width: 12px;
                border: none;
                border-radius: 6px;
            }

            QScrollBar::handle:vertical {
                background-color: #3a3a3a;
                border-radius: 6px;
                min-height: 20px;
                margin: 2px;
            }

            QScrollBar::handle:vertical:hover {
                background-color: #4a4a4a;
            }

            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: none;
                height: 0px;
            }
        """)

    # Window dragging functionality
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None
        event.accept()

OrderConfirmationDialog = OrderDialog