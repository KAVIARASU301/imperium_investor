import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget, QFrame,
    QComboBox, QCheckBox, QButtonGroup, QRadioButton,
    QTabWidget, QSpinBox, QDoubleSpinBox, QGridLayout, QMessageBox
)
from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QMouseEvent, QShowEvent, QPainter, QPainterPath, QFont, QPen, QLinearGradient, QColor, QBrush, \
    QFontMetrics
from typing import Dict, Any, Optional
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger(__name__)


class ToggleSwitch(QWidget):
    """Modern toggle switch for Buy/Sell selection with proper color scheme."""
    toggled = Signal(bool)  # True for Buy, False for Sell

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(120, 32)
        self._is_buy = True

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background track with square edges
        track_rect = self.rect()
        painter.setPen(Qt.PenStyle.NoPen)

        if self._is_buy:
            painter.setBrush(QColor("#0a3d0a"))  # Darker green background
        else:
            painter.setBrush(QColor("#3d0a0a"))  # Dark red background

        painter.drawRect(track_rect)  # Square edges

        # Sliding button with square edges
        button_width = 56
        button_height = 24
        button_y = (self.height() - button_height) // 2

        if self._is_buy:
            button_x = 4
            painter.setBrush(QColor("#00b894"))  # Professional green
        else:
            button_x = self.width() - button_width - 4
            painter.setBrush(QColor("#d63031"))  # Professional red

        painter.drawRect(button_x, button_y, button_width, button_height)  # Square edges

        # Text
        painter.setPen(QColor("#ffffff"))
        font = QFont("Consolas", 10, QFont.Weight.Bold)
        painter.setFont(font)

        if self._is_buy:
            text_rect = QRect(button_x, button_y, button_width, button_height)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, "BUY")
            # SELL text on the right
            painter.setPen(QColor("#666666"))
            text_rect = QRect(button_x + button_width + 4, 0, self.width() - button_x - button_width - 8, self.height())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, "SELL")
        else:
            text_rect = QRect(button_x, button_y, button_width, button_height)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, "SELL")
            # BUY text on the left
            painter.setPen(QColor("#666666"))
            text_rect = QRect(4, 0, button_x - 4, self.height())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, "BUY")

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
    """Enhanced order window with improved UI and proper color scheme."""
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
        self._apply_styles()
        self._connect_signals()
        self._populate_initial_data()

    def _setup_dialog(self):
        """Configure dialog properties."""
        self.setWindowTitle(f"{self.symbol}")
        self.setModal(True)
        self.setFixedSize(420, 520)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def showEvent(self, event: QShowEvent):
        """Center dialog on parent."""
        super().showEvent(event)
        if self.parent():
            parent_rect = self.parent().geometry()
            x = parent_rect.center().x() - self.width() // 2
            y = parent_rect.center().y() - self.height() // 2
            self.move(x, y)

    def _setup_ui(self):
        """Build the enhanced UI."""
        # Main container with gradient border
        self.container = QWidget(self)
        self.container.setObjectName("mainContainer")
        self.container.setGeometry(0, 0, self.width(), self.height())

        # Install event filter on container for dragging
        self.container.installEventFilter(self)

        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(12, 12, 12, 12)
        container_layout.setSpacing(10)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.container)

        # Header with symbol, LTP and toggle
        container_layout.addLayout(self._create_header())

        # Order tabs
        container_layout.addWidget(self. _create_order_tabs())

        # Action buttons
        container_layout.addLayout(self._create_action_buttons())

    def _create_header(self) -> QHBoxLayout:
        """Create enhanced header."""
        layout = QHBoxLayout()
        layout.setSpacing(12)

        # Symbol and LTP
        symbol_layout = QVBoxLayout()
        symbol_layout.setSpacing(2)

        self.symbol_label = QLabel(self.symbol)
        self.symbol_label.setObjectName("symbolLabel")

        self.ltp_label = QLabel(f"₹{self.ltp:,.2f}")
        self.ltp_label.setObjectName("ltpLabel")

        symbol_layout.addWidget(self.symbol_label)
        symbol_layout.addWidget(self.ltp_label)

        # Buy/Sell toggle
        self.toggle_switch = ToggleSwitch()
        self.toggle_switch.set_buy_mode(self._is_buy)

        # Close button
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.reject)

        layout.addLayout(symbol_layout)
        layout.addStretch()
        layout.addWidget(self.toggle_switch)
        layout.addWidget(close_btn)

        return layout

    def _create_order_tabs(self) -> QTabWidget:
        """Create enhanced tabbed interface."""
        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("orderTabs")

        # Regular Order Tab
        self.tab_widget.addTab(self._create_regular_order_tab(), "REGULAR")

        # Bracket Order Tab
        self.tab_widget.addTab(self._create_bracket_order_tab(), "BRACKET")

        return self.tab_widget

    def _create_regular_order_tab(self) -> QWidget:
        """Create regular order form."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)
        layout.setContentsMargins(8, 8, 8, 8)

        # Order Type - Horizontal layout
        order_type_layout = QHBoxLayout()
        order_type_layout.setSpacing(8)

        self.order_type_group = QButtonGroup()
        order_types = ["MARKET", "LIMIT", "SL", "SL-M"]

        for i, order_type in enumerate(order_types):
            radio = QRadioButton(order_type)
            radio.setObjectName("orderTypeRadio")
            if order_type == "MARKET":
                radio.setChecked(True)
            self.order_type_group.addButton(radio, i)
            order_type_layout.addWidget(radio)

        layout.addLayout(order_type_layout)

        # Main form grid
        grid_layout = QGridLayout()
        grid_layout.setVerticalSpacing(10)
        grid_layout.setHorizontalSpacing(12)

        # Row 0: Quantity and Product
        grid_layout.addWidget(QLabel("QTY"), 0, 0)
        self.quantity_spinbox = QSpinBox()
        self.quantity_spinbox.setRange(1, 999999)
        self.quantity_spinbox.setValue(1)
        self.quantity_spinbox.setObjectName("quantityInput")
        grid_layout.addWidget(self.quantity_spinbox, 0, 1)

        grid_layout.addWidget(QLabel("PRODUCT"), 0, 2)
        self.product_combo = QComboBox()
        self.product_combo.addItems(["MIS", "NRML"])
        self.product_combo.setObjectName("productCombo")
        grid_layout.addWidget(self.product_combo, 0, 3)

        # Row 1: Price and Validity
        grid_layout.addWidget(QLabel("PRICE"), 1, 0)
        self.price_spinbox = QDoubleSpinBox()
        self.price_spinbox.setRange(0.01, 999999.99)
        self.price_spinbox.setDecimals(2)
        self.price_spinbox.setValue(self.ltp)
        self.price_spinbox.setEnabled(False)
        self.price_spinbox.setObjectName("priceInput")
        grid_layout.addWidget(self.price_spinbox, 1, 1)

        grid_layout.addWidget(QLabel("VALIDITY"), 1, 2)
        self.validity_combo = QComboBox()
        self.validity_combo.addItems(["DAY", "IOC"])
        self.validity_combo.setObjectName("validityCombo")
        grid_layout.addWidget(self.validity_combo, 1, 3)

        # Row 2: Trigger Price
        grid_layout.addWidget(QLabel("TRIGGER"), 2, 0)
        self.trigger_price_spinbox = QDoubleSpinBox()
        self.trigger_price_spinbox.setRange(0.01, 999999.99)
        self.trigger_price_spinbox.setDecimals(2)
        self.trigger_price_spinbox.setValue(self.ltp)
        self.trigger_price_spinbox.setEnabled(False)
        self.trigger_price_spinbox.setObjectName("triggerPriceInput")
        grid_layout.addWidget(self.trigger_price_spinbox, 2, 1, 1, 3)

        layout.addLayout(grid_layout)

        # SL and Target section
        sl_target_frame = QFrame()
        sl_target_frame.setObjectName("slTargetFrame")
        sl_target_layout = QVBoxLayout(sl_target_frame)
        sl_target_layout.setSpacing(12)

        # Use QFontMetrics to get uniform checkbox width
        font_metrics = QFontMetrics(widget.font())
        uniform_label_width = font_metrics.horizontalAdvance("STOP LOSS") + 20

        # Stop Loss - using QGridLayout
        sl_grid = QGridLayout()
        sl_grid.setSpacing(8)

        self.enable_sl_checkbox = QCheckBox("STOP LOSS")
        self.enable_sl_checkbox.setObjectName("enableSLCheckbox")
        self.enable_sl_checkbox.setFixedWidth(uniform_label_width)
        sl_grid.addWidget(self.enable_sl_checkbox, 0, 0)

        self.sl_type_combo = QComboBox()
        self.sl_type_combo.addItems(["%", "₹", "PTS"])
        self.sl_type_combo.setEnabled(False)
        self.sl_type_combo.setFixedWidth(60)
        sl_grid.addWidget(self.sl_type_combo, 0, 1)

        self.sl_value_spinbox = QDoubleSpinBox()
        self.sl_value_spinbox.setRange(0.01, 9999.99)
        self.sl_value_spinbox.setDecimals(2)
        self.sl_value_spinbox.setValue(2.0)
        self.sl_value_spinbox.setEnabled(False)
        self.sl_value_spinbox.setFixedWidth(80)
        sl_grid.addWidget(self.sl_value_spinbox, 0, 2)

        self.sl_price_label = QLabel("₹0.00")
        self.sl_price_label.setObjectName("calcPrice")
        sl_grid.addWidget(self.sl_price_label, 0, 3)

        sl_grid.setColumnStretch(4, 1)
        sl_target_layout.addLayout(sl_grid)

        # Target - using same column layout
        target_grid = QGridLayout()
        target_grid.setSpacing(8)

        self.enable_target_checkbox = QCheckBox("TARGET")
        self.enable_target_checkbox.setObjectName("enableTargetCheckbox")
        self.enable_target_checkbox.setFixedWidth(uniform_label_width)
        target_grid.addWidget(self.enable_target_checkbox, 0, 0)

        self.target_type_combo = QComboBox()
        self.target_type_combo.addItems(["%", "₹", "PTS"])
        self.target_type_combo.setEnabled(False)
        self.target_type_combo.setFixedWidth(60)
        target_grid.addWidget(self.target_type_combo, 0, 1)

        self.target_value_spinbox = QDoubleSpinBox()
        self.target_value_spinbox.setRange(0.01, 9999.99)
        self.target_value_spinbox.setDecimals(2)
        self.target_value_spinbox.setValue(3.0)
        self.target_value_spinbox.setEnabled(False)
        self.target_value_spinbox.setFixedWidth(80)
        target_grid.addWidget(self.target_value_spinbox, 0, 2)

        self.target_price_label = QLabel("₹0.00")
        self.target_price_label.setObjectName("calcPrice")
        target_grid.addWidget(self.target_price_label, 0, 3)

        target_grid.setColumnStretch(4, 1)
        sl_target_layout.addLayout(target_grid)

        layout.addWidget(sl_target_frame)
        layout.addStretch()

        # Total Investment Label
        self.total_investment_label = QLabel("Total Investment: ₹0.00")
        self.total_investment_label.setObjectName("totalInvestmentLabel")
        layout.addWidget(self.total_investment_label)


        return widget

    def _create_bracket_order_tab(self) -> QWidget:
        """Create bracket order form."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 6, 12, 6)

        # Entry section
        entry_frame = QFrame()
        entry_frame.setObjectName("entryFrame")
        entry_layout = QVBoxLayout(entry_frame)
        entry_layout.setSpacing(8)

        entry_label = QLabel("ENTRY ORDER")
        entry_label.setObjectName("sectionLabel")
        entry_layout.addWidget(entry_label)

        entry_grid = QGridLayout()
        entry_grid.setSpacing(6)

        entry_grid.addWidget(QLabel("QTY"), 0, 0)
        self.bracket_quantity_spinbox = QSpinBox()
        self.bracket_quantity_spinbox.setRange(1, 999999)
        self.bracket_quantity_spinbox.setValue(1)
        entry_grid.addWidget(self.bracket_quantity_spinbox, 0, 1)

        entry_grid.addWidget(QLabel("PRICE"), 0, 2)
        self.bracket_price_spinbox = QDoubleSpinBox()
        self.bracket_price_spinbox.setRange(0.01, 999999.99)
        self.bracket_price_spinbox.setDecimals(2)
        self.bracket_price_spinbox.setValue(self.ltp)
        entry_grid.addWidget(self.bracket_price_spinbox, 0, 3)

        entry_layout.addLayout(entry_grid)
        layout.addWidget(entry_frame)

        # SL section
        sl_frame = QFrame()
        sl_frame.setObjectName("slFrame")
        sl_layout = QVBoxLayout(sl_frame)
        sl_layout.setSpacing(8)

        sl_label = QLabel("STOP LOSS")
        sl_label.setObjectName("sectionLabel")
        sl_layout.addWidget(sl_label)

        sl_grid = QGridLayout()
        sl_grid.setSpacing(6)

        sl_grid.addWidget(QLabel("TYPE"), 0, 0)
        self.bracket_sl_type_combo = QComboBox()
        self.bracket_sl_type_combo.addItems(["%", "₹", "PTS"])
        self.bracket_sl_type_combo.setFixedWidth(80)
        sl_grid.addWidget(self.bracket_sl_type_combo, 0, 1)

        sl_grid.addWidget(QLabel("VALUE"), 0, 2)
        self.bracket_sl_value_spinbox = QDoubleSpinBox()
        self.bracket_sl_value_spinbox.setRange(0.01, 9999.99)
        self.bracket_sl_value_spinbox.setDecimals(2)
        self.bracket_sl_value_spinbox.setValue(2.0)
        self.bracket_sl_value_spinbox.setFixedWidth(100)
        sl_grid.addWidget(self.bracket_sl_value_spinbox, 0, 3)

        self.bracket_sl_price_label = QLabel("₹0.00")
        self.bracket_sl_price_label.setObjectName("calcPriceLarge")
        sl_grid.addWidget(self.bracket_sl_price_label, 1, 0, 1, 4)

        sl_layout.addLayout(sl_grid)
        layout.addWidget(sl_frame)

        # Target section
        target_frame = QFrame()
        target_frame.setObjectName("targetFrame")
        target_layout = QVBoxLayout(target_frame)
        target_layout.setSpacing(8)

        target_label = QLabel("TARGET")
        target_label.setObjectName("sectionLabel")
        target_layout.addWidget(target_label)

        target_grid = QGridLayout()
        target_grid.setSpacing(6)

        target_grid.addWidget(QLabel("TYPE"), 0, 0)
        self.bracket_target_type_combo = QComboBox()
        self.bracket_target_type_combo.addItems(["%", "₹", "PTS"])
        self.bracket_target_type_combo.setFixedWidth(80)
        target_grid.addWidget(self.bracket_target_type_combo, 0, 1)

        target_grid.addWidget(QLabel("VALUE"), 0, 2)
        self.bracket_target_value_spinbox = QDoubleSpinBox()
        self.bracket_target_value_spinbox.setRange(0.01, 9999.99)
        self.bracket_target_value_spinbox.setDecimals(2)
        self.bracket_target_value_spinbox.setValue(3.0)
        self.bracket_target_value_spinbox.setFixedWidth(100)
        target_grid.addWidget(self.bracket_target_value_spinbox, 0, 3)

        self.bracket_target_price_label = QLabel("₹0.00")
        self.bracket_target_price_label.setObjectName("calcPriceLarge")
        target_grid.addWidget(self.bracket_target_price_label, 1, 0, 1, 4)

        target_layout.addLayout(target_grid)
        layout.addWidget(target_frame)

        layout.addStretch()

        # Total Investment Label for Bracket Order
        self.bracket_total_investment_label = QLabel("Total Investment: ₹0.00")
        self.bracket_total_investment_label.setObjectName("totalInvestmentLabel")
        layout.addWidget(self.bracket_total_investment_label)

        return widget

    def _create_action_buttons(self) -> QHBoxLayout:
        """Create action buttons with proper colors."""
        layout = QHBoxLayout()
        layout.setSpacing(8)

        # Cancel button
        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.setFixedHeight(32)
        cancel_btn.clicked.connect(self.reject)

        # Update LTP button
        update_ltp_btn = QPushButton("UPDATE LTP")
        update_ltp_btn.setObjectName("secondaryButton")
        update_ltp_btn.setFixedHeight(32)
        update_ltp_btn.clicked.connect(self._update_ltp)

        # Place Order button
        self.place_order_btn = QPushButton("PLACE ORDER")
        self.place_order_btn.setObjectName("primaryButton")
        self.place_order_btn.setFixedHeight(32)
        self.place_order_btn.clicked.connect(self._place_order)

        layout.addWidget(cancel_btn)
        layout.addWidget(update_ltp_btn)
        layout.addStretch()
        layout.addWidget(self.place_order_btn)

        return layout

    def _connect_signals(self):
        """Connect all signal handlers."""
        # Toggle switch
        self.toggle_switch.toggled.connect(self._on_transaction_type_changed)

        # Order type radio buttons
        self.order_type_group.buttonToggled.connect(self._on_order_type_changed)

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

        # Total Investment calculations for Regular Order
        self.quantity_spinbox.valueChanged.connect(self._update_regular_total_investment)
        self.price_spinbox.valueChanged.connect(self._update_regular_total_investment)
        self.order_type_group.buttonToggled.connect(self._update_regular_total_investment) # Re-evaluate on order type change

        # Total Investment calculations for Bracket Order
        self.bracket_quantity_spinbox.valueChanged.connect(self._update_bracket_total_investment)
        self.bracket_price_spinbox.valueChanged.connect(self._update_bracket_total_investment)


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
        self._update_bracket_calculations()
        self._update_regular_total_investment()
        self._update_bracket_total_investment()

    def _update_ui_state(self):
        """Update UI elements based on current selections."""
        # Update button text and color based on buy/sell
        action = "BUY" if self.toggle_switch.is_buy_mode() else "SELL"
        self.place_order_btn.setText(f"{action}")

        # Update button style based on transaction type
        if self._is_buy:
            self.place_order_btn.setObjectName("primaryButtonBuy")
            # Set the focus/border color for spinboxes and comboboxes to green
            self.setStyleSheet(self.styleSheet() + """
                QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
                    border: 1px solid #00b894; /* Professional green */
                }
                #orderTypeRadio::indicator:checked, QCheckBox::indicator:checked {
                    background: #00b894; /* Professional green */
                    border: 2px solid #00b894; /* Professional green */
                }
                #calcPrice, #calcPriceLarge {
                    color: #00b894; /* Professional green */
                }
                #totalInvestmentLabel {
                    color: #00b894; /* Professional green */
                }
            """)
        else:
            self.place_order_btn.setObjectName("primaryButtonSell")
            # Set the focus/border color for spinboxes and comboboxes to red
            self.setStyleSheet(self.styleSheet() + """
                QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
                    border: 1px solid #d63031; /* Professional red */
                }
                #orderTypeRadio::indicator:checked, QCheckBox::indicator:checked {
                    background: #d63031; /* Professional red */
                    border: 2px solid #d63031; /* Professional red */
                }
                #calcPrice, #calcPriceLarge {
                    color: #d63031; /* Professional red */
                }
                #totalInvestmentLabel {
                    color: #d63031; /* Professional red */
                }
            """)

        # Force style refresh for all relevant widgets
        self.place_order_btn.setStyle(self.place_order_btn.style())
        self.quantity_spinbox.setStyle(self.quantity_spinbox.style())
        self.product_combo.setStyle(self.product_combo.style())
        self.price_spinbox.setStyle(self.price_spinbox.style())
        self.validity_combo.setStyle(self.validity_combo.style())
        self.trigger_price_spinbox.setStyle(self.trigger_price_spinbox.style())
        self.enable_sl_checkbox.setStyle(self.enable_sl_checkbox.style())
        self.enable_target_checkbox.setStyle(self.enable_target_checkbox.style())
        self.sl_type_combo.setStyle(self.sl_type_combo.style())
        self.sl_value_spinbox.setStyle(self.sl_value_spinbox.style())
        self.target_type_combo.setStyle(self.target_type_combo.style())
        self.target_value_spinbox.setStyle(self.target_value_spinbox.style())
        self.bracket_quantity_spinbox.setStyle(self.bracket_quantity_spinbox.style())
        self.bracket_price_spinbox.setStyle(self.bracket_price_spinbox.style())
        self.bracket_sl_type_combo.setStyle(self.bracket_sl_type_combo.style())
        self.bracket_sl_value_spinbox.setStyle(self.bracket_sl_value_spinbox.style())
        self.bracket_target_type_combo.setStyle(self.bracket_target_type_combo.style())
        self.bracket_target_value_spinbox.setStyle(self.bracket_target_value_spinbox.style())

        # Update border color
        self._update_border_color()

    def _update_border_color(self):
        """Update window border with gradient effect."""
        self.update()  # Trigger paintEvent

    def paintEvent(self, event):
        """Custom paint event for gradient border."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Create rounded rectangle path
        path = QPainterPath()
        rect = self.rect()
        path.addRoundedRect(rect, 8, 8)

        # Create gradient for border
        gradient = QLinearGradient(0, 0, rect.width(), rect.height())

        if self._is_buy:
            # Green gradient for buy
            gradient.setColorAt(0, QColor("#00b894"))
            gradient.setColorAt(0.5, QColor("#00a085"))
            gradient.setColorAt(1, QColor("#009376"))
        else:
            # Red gradient for sell
            gradient.setColorAt(0, QColor("#d63031"))
            gradient.setColorAt(0.5, QColor("#c7282a"))
            gradient.setColorAt(1, QColor("#b82023"))

        # Draw gradient border
        pen = QPen(QBrush(gradient), 2)
        painter.setPen(pen)
        painter.drawPath(path)

        # Fill background
        painter.fillPath(path, QColor("#0a0a0a"))

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

        # Enable/disable fields
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
        self._update_bracket_total_investment()


    def _update_regular_total_investment(self):
        """Calculate and update the total investment for the regular order tab."""
        quantity = self.quantity_spinbox.value()
        order_type = self._order_type

        if order_type == "MARKET":
            price = self.ltp
        else: # LIMIT, SL, SL-M
            price = self.price_spinbox.value()

        total_investment = quantity * price
        self.total_investment_label.setText(f"Total Investment: ₹{total_investment:,.2f}")


    def _update_bracket_total_investment(self):
        """Calculate and update the total investment for the bracket order tab."""
        quantity = self.bracket_quantity_spinbox.value()
        price = self.bracket_price_spinbox.value()
        total_investment = quantity * price
        self.bracket_total_investment_label.setText(f"Total Investment: ₹{total_investment:,.2f}")

    def _update_ltp(self):
        """
        Request a fresh LTP update by directly calling the parent window's method.
        This is the restored direct communication pattern.
        """
        logger.info(f"LTP update requested for {self.symbol}")

        if hasattr(self.parent(), '_get_fresh_ltp'):
            # The parent window has the method, so we can call it.
            new_ltp = self.parent()._get_fresh_ltp(self.symbol)

            if new_ltp is not None and new_ltp > 0:
                self.update_ltp(new_ltp)  # Call the dialog's own updater
            else:
                QMessageBox.warning(self, "Update Failed", "Could not fetch the latest price.")
        else:
            # This is a fallback in case the parent doesn't have the method.
            logger.error("Parent window does not have the '_get_fresh_ltp' method.")
            QMessageBox.critical(self, "Error", "LTP update functionality is not available.")
        # ---------------------------------------------------------------------

    def update_ltp(self, new_ltp: float):
        """Update LTP and recalculate dependent values."""
        self.ltp = new_ltp
        self.ltp_label.setText(f"₹{self.ltp:,.2f}")

        # Only update prices if market order or not manually edited
        if self._order_type == "MARKET":
            self.price_spinbox.setValue(new_ltp)
            self.trigger_price_spinbox.setValue(new_ltp)

        self.bracket_price_spinbox.setValue(new_ltp)

        # Recalculate all dependent values
        self._update_sl_calculation()
        self._update_target_calculation()
        self._update_bracket_calculations()
        self._update_regular_total_investment()
        self._update_bracket_total_investment()

    def _place_order(self):
        """Process order placement."""
        current_tab = self.tab_widget.currentIndex()

        if current_tab == 0:  # Regular Order
            self._place_regular_order()
        elif current_tab == 1:  # Bracket Order
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
                "variety": "regular",  # ADD THIS
                "exchange": "NSE",  # ADD THIS
                "tradingsymbol": self.symbol,
                "transaction_type": "BUY" if self._is_buy else "SELL",
                "quantity": self.quantity_spinbox.value(),
                "order_type": order_type,
                "product": self.product_combo.currentText(),
                "validity": self.validity_combo.currentText()
            }

            if order_type in ["LIMIT", "SL"]:
                price_value = self.price_spinbox.value()
                # Format to a string with exactly two decimal places
                order_data["price"] = f"{price_value:.2f}"

            if order_type in ["SL", "SL-M"]:
                trigger_price_value = self.trigger_price_spinbox.value()
                # Format to a string with exactly two decimal places
                order_data["trigger_price"] = f"{trigger_price_value:.2f}"

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
                        "trigger_price": sl_price,
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
                        "price": target_price,
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

    def _apply_styles(self):
        """Apply enhanced dark theme with proper color scheme."""
        self.setStyleSheet("""
            /* Labels */
            #symbolLabel {
                color: #ffffff;
                font-size: 18px;
                font-weight: bold;
                font-family: "Consolas", monospace;
            }

            #ltpLabel {
                color: #888888;
                font-size: 14px;
                font-family: "Consolas", monospace;
            }

            /* These colors will be dynamically set by _update_ui_state */
            #calcPrice {
                color: #00ff00; /* Default, will be overridden */
                font-size: 11px;
                font-weight: bold;
            }

            #calcPriceLarge {
                color: #00ff00; /* Default, will be overridden */
                font-size: 14px;
                font-weight: bold;
                padding-top: 4px;
            }

            #totalInvestmentLabel {
                color: #00ff00; /* Default, will be overridden */
                font-size: 14px;
                font-weight: bold;
                padding-top: 8px;
                qproperty-alignment: AlignRight; /* Align text to the right */
            }

            #sectionLabel {
                color: #ffff00;
                font-size: 11px;
                margin-bottom: 2px;
                font-weight: bold;
                font-family: "Consolas", monospace;
                letter-spacing: 1px;
            }

            /* Close Button */
            #closeButton {
                background: transparent;
                border: none;
                color: #888888;
                font-size: 18px;
                font-weight: normal;
                font-family: Arial, sans-serif;
            }
            #closeButton:hover {
                color: #ffffff;
            }

            /* Frames */
            #slTargetFrame, #entryFrame, #slFrame, #targetFrame {
                background: #0a0a0a;
                border: 1px solid #222222;
                padding: 4px;
                margin: 2px 0px;
            }

            /* Tabs */
            QTabWidget::pane {
                border: 1px solid #222222;
                background: #000000;
                border-radius: 0px;
            }

            QTabBar::tab {
                background: #111111;
                color: #666666;
                padding: 6px 16px;
                margin-right: 2px;
                border: 1px solid #222222;
                border-bottom: none;
                font-size: 11px;
                font-weight: bold;
            }

            QTabBar::tab:selected {
                background: #000000;
                color: #ffffff;
                border-color: #444444;
            }

            /* Radio Buttons */
            #orderTypeRadio {
                color: #ffffff;
                font-size: 11px;
                spacing: 5px;
            }

            #orderTypeRadio::indicator {
                width: 10px;
                height: 10px;
                border: 2px solid #444444;
                background: #000000;
            }

            /* This color will be dynamically set by _update_ui_state */
            #orderTypeRadio::indicator:checked {
                background: #00ff00; /* Default, will be overridden */
                border: 2px solid #00ff00; /* Default, will be overridden */
            }

            /* Input Fields - SpinBoxes and ComboBoxes */
            QSpinBox, QDoubleSpinBox, QComboBox {
                background: #111111;
                border: 1px solid #333333;
                color: #ffffff;
                padding: 2px;
                padding-left: 6px; /* Shift text a bit to the left */
                font-size: 14px;
                font-family: "Consolas", monospace;
                min-height: 26px; /* Increased height for uniformity */
                max-height: 26px; /* Increased height for uniformity */
            }
            /* This color will be dynamically set by _update_ui_state */
            QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
                border: 1px solid #00ff00; /* Default, will be overridden */
            }

            QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
                background: #000000;
                color: #444444;
                border: 1px solid #222222;
            }

            QSpinBox::up-button, QDoubleSpinBox::up-button,
            QSpinBox::down-button, QDoubleSpinBox::down-button {
                background: #222222;
                border: none;
                width: 16px; /* Slightly wider buttons for better click area */
            }

            QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
            QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
                background: #333333;
            }

            QComboBox::drop-down {
                border: none;
                width: 20px; /* Adjusted for better appearance with increased height */
            }

            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid #666666;
            }

            QComboBox QAbstractItemView {
                background: #111111;
                border: 1px solid #333333;
                color: #ffffff;
                selection-background-color: #222222;
            }

            /* Checkboxes */
            QCheckBox {
                color: #ffffff;
                font-size: 11px;
                font-weight: bold;
                spacing: 5px;
            }

            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 2px solid #444444;
                background: #000000;
            }

            /* This color will be dynamically set by _update_ui_state */
            QCheckBox::indicator:checked {
                background: #00ff00; /* Default, will be overridden */
                border: 2px solid #00ff00; /* Default, will be overridden */
            }

            QCheckBox:disabled {
                color: #444444;
            }

            /* Labels */
            QLabel {
                color: #888888;
                font-size: 11px;
                font-family: "Consolas", monospace;
            }

            /* Buttons */
            QPushButton {
                font-weight: bold;
                border: 1px solid #333333;
                padding: 8px 16px;
                font-size: 11px;
                font-family: "Consolas", monospace;
                text-transform: uppercase;
            }

            #secondaryButton {
                background: #111111;
                color: #888888;
            }

            #secondaryButton:hover {
                background: #222222;
                color: #ffffff;
                border: 1px solid #444444;
            }

            /* Primary Button (Place Order) - default styles are here, overridden by specific buy/sell below */
            #primaryButton {
                background: #111111;
                color: #00ff00;
                border: 1px solid #00ff00;
            }

            #primaryButton:hover {
                background: #00ff00;
                color: #000000;
            }

            /* Specific styles for Buy button */
            #primaryButtonBuy {
                background: #0a3d0a;
                color: #00b894;
                border: 1px solid #00b894;
            }

            #primaryButtonBuy:hover {
                background: #00b894;
                color: #000000;
            }

            /* Specific styles for Sell button */
            #primaryButtonSell {
                background: #3d0a0a;
                color: #d63031;
                border: 1px solid #d63031;
            }

            #primaryButtonSell:hover {
                background: #d63031;
                color: #ffffff;
            }

            /* Scrollbar */
            QScrollBar:vertical {
                background: #000000;
                width: 8px;
                border: none;
            }

            QScrollBar::handle:vertical {
                background: #333333;
                min-height: 20px;
            }

            QScrollBar::handle:vertical:hover {
                background: #444444;
            }

            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: none;
                height: 0px;
            }
        """)

    # Window dragging - make entire dialog draggable
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            # Check if click is not on any interactive widget
            widget = self.childAt(event.position().toPoint())
            if widget and not isinstance(widget,
                                         (QPushButton, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QRadioButton,
                                          ToggleSwitch)):
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()
            elif widget is None:  # Clicked on empty space
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()
            else:
                super().mousePressEvent(event)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)


    def eventFilter(self, obj, event):
        """Event filter to handle dragging from container widget."""
        if obj == self.container:
            if event.type() == event.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                # Check if click is not on any interactive widget
                widget = self.childAt(event.globalPosition().toPoint() - self.pos())
                if widget == self.container or (widget and not isinstance(widget, (QPushButton, QComboBox, QSpinBox,
                                                                                   QDoubleSpinBox, QCheckBox,
                                                                                   QRadioButton, ToggleSwitch,
                                                                                   QTabWidget))):
                    self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                    return True
            elif event.type() == event.Type.MouseMove and event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
                self.move(event.globalPosition().toPoint() - self._drag_pos)
                return True
            elif event.type() == event.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self._drag_pos = None
                return True
        return super().eventFilter(obj, event)

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
                "price": entry_price,
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