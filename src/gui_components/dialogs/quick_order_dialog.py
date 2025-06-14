import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QDoubleSpinBox, QPushButton, QWidget, QFrame,
    QRadioButton
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent

from src.utils.pricing_utils import calculate_smart_limit_price
from src.utils.data_models import Contract

logger = logging.getLogger(__name__)


class QuickOrderDialog(QDialog):
    """
    A premium, compact quick order dialog with real-time price refresh
    and functionality to be pre-filled for modifying existing orders.
    """
    order_placed = Signal(dict)
    refresh_requested = Signal(str)

    def __init__(self, parent, contract: Contract, default_lots: int):
        super().__init__(parent)
        self.contract = contract
        self._drag_pos = None

        self._setup_window(parent)
        self._setup_ui(default_lots)
        self._apply_styles()
        self.show()
        self.activateWindow()

    def _setup_window(self, parent):
        self.setWindowTitle("Quick Order")
        self.setModal(False)
        self.setMinimumSize(340, 400)
        self.setMaximumWidth(680)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)

        # 👇 Position near the mouse cursor
        from PySide6.QtGui import QCursor
        cursor_pos = QCursor.pos()  # global position
        x_offset = 20
        y_offset = 10

        # Prevent it from going off the screen
        screen = self.screen().availableGeometry()
        dialog_size = self.sizeHint()

        x = min(cursor_pos.x() + x_offset, screen.right() - dialog_size.width())
        y = min(cursor_pos.y() + y_offset, screen.bottom() - dialog_size.height())

        self.move(x, y)

    def _setup_ui(self, default_lots):
        container = QWidget(self)
        container.setObjectName("mainContainer")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(20, 10, 20, 20)
        container_layout.setSpacing(15)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        container_layout.addLayout(self._create_title_bar())
        container_layout.addSpacing(5)
        self._create_instrument_info(container_layout)
        self._create_form_controls(container_layout, default_lots)
        self._create_action_buttons(container_layout)

    def _create_title_bar(self):
        title_layout = QHBoxLayout()
        title = QLabel("Quick Order")
        title.setObjectName("dialogTitle")
        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeButton")
        self.close_btn.setFixedSize(35, 35)
        self.close_btn.clicked.connect(self.reject)
        title_layout.addWidget(title)
        title_layout.addStretch()
        title_layout.addWidget(self.close_btn)
        return title_layout

    def _create_instrument_info(self, parent_layout):
        self.symbol_label = QLabel(self.contract.tradingsymbol)
        self.symbol_label.setObjectName("symbolLabel")
        parent_layout.addWidget(self.symbol_label)

        self.info_label = QLabel(f"Strike: ₹{int(self.contract.strike):,}  |  LTP: ₹{self.contract.ltp:.2f}")
        self.info_label.setObjectName("infoLabel")
        parent_layout.addWidget(self.info_label)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setObjectName("divider")
        parent_layout.addWidget(divider)

    def _create_form_controls(self, parent_layout, default_lots):
        form_layout = QGridLayout()
        form_layout.setHorizontalSpacing(15)
        form_layout.setVerticalSpacing(12)

        self.buy_radio = QRadioButton("BUY")
        self.buy_radio.setObjectName("buyRadio")
        self.buy_radio.setChecked(True)
        self.sell_radio = QRadioButton("SELL")
        self.sell_radio.setObjectName("sellRadio")

        transaction_layout = QHBoxLayout()
        transaction_layout.addWidget(self.buy_radio)
        transaction_layout.addSpacing(20)
        transaction_layout.addWidget(self.sell_radio)
        transaction_layout.addStretch()
        form_layout.addLayout(transaction_layout, 0, 0, 1, 2)

        form_layout.addWidget(QLabel("Lots"), 1, 0)
        self.lots_spinbox = QDoubleSpinBox()
        self.lots_spinbox.setRange(1, 1000)
        self.lots_spinbox.setDecimals(0)
        self.lots_spinbox.setValue(default_lots)
        form_layout.addWidget(self.lots_spinbox, 1, 1)

        form_layout.addWidget(QLabel("Limit Price"), 2, 0)
        self.price_spinbox = QDoubleSpinBox()
        self.price_spinbox.setRange(0.05, 50000.0)
        self.price_spinbox.setDecimals(2)
        self.price_spinbox.setSingleStep(0.05)
        self.price_spinbox.setValue(calculate_smart_limit_price(self.contract))
        self.price_spinbox.setButtonSymbols(QDoubleSpinBox.NoButtons)
        form_layout.addWidget(self.price_spinbox, 2, 1)

        self.total_value_label = QLabel()
        self.total_value_label.setObjectName("totalValueLabel")
        self.total_value_label.setAlignment(Qt.AlignCenter)
        form_layout.addWidget(self.total_value_label, 3, 0, 1, 2)
        parent_layout.addLayout(form_layout)
        parent_layout.addStretch()

        self.lots_spinbox.valueChanged.connect(self._update_summary)
        self.price_spinbox.valueChanged.connect(self._update_summary)
        self._update_summary()

    def _create_action_buttons(self, parent_layout):
        action_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh Price")
        self.refresh_btn.setObjectName("refreshButton")
        self.refresh_btn.clicked.connect(lambda: self.refresh_requested.emit(self.contract.tradingsymbol))

        self.place_order_btn = QPushButton("PLACE ORDER")
        self.place_order_btn.setObjectName("confirmButton")
        self.place_order_btn.clicked.connect(self._accept_dialog)

        action_layout.addWidget(self.refresh_btn)
        action_layout.addWidget(self.place_order_btn)
        parent_layout.addLayout(action_layout)

    def _accept_dialog(self):
        """
        Gathers order parameters, closes the dialog immediately, and then
        emits the signal for the main window to process the order.
        """
        from kiteconnect import KiteConnect
        order_parameters = {
            'contract': self.contract,
            'quantity': int(self.lots_spinbox.value()) * self.contract.lot_size,
            'price': self.price_spinbox.value(),
            'order_type': 'LIMIT',
            'transaction_type': KiteConnect.TRANSACTION_TYPE_BUY if self.buy_radio.isChecked() else KiteConnect.TRANSACTION_TYPE_SELL,
        }
        # FIX: Close the dialog *before* emitting the signal
        self.accept()
        # Now emit the signal for the main window to handle the order execution
        self.order_placed.emit(order_parameters)

    def _apply_styles(self):
        self.setStyleSheet("""
            #mainContainer {
                background-color: #161A25;
                border: 1px solid #3A4458;
                border-radius: 12px;
                font-family: "Segoe UI", sans-serif;
            }
            #dialogTitle { color: #E0E0E0; font-size: 14px; font-weight: 600; }
            #closeButton {
                background-color: transparent; border: none; color: #8A9BA8;
                font-size: 16px; font-weight: bold;
            }
            #closeButton:hover, #navButton:hover { background-color: #3A4458; color: #f52a20; }

            #symbolLabel { color: #FFFFFF; font-size: 24px; font-weight: 300; }
            #infoLabel { color: #A9B1C3; font-size: 12px; }
            #divider { background-color: #2A3140; height: 1px; border: none; }
            QLabel { color: #A9B1C3; font-size: 13px; }

            QRadioButton { spacing: 8px; color: #A9B1C3; font-weight: bold; }
            QRadioButton::indicator {
                width: 18px; height: 18px; border-radius: 9px;
                background-color: #2A3140; border: 1px solid #3A4458;
            }
            #buyRadio::indicator:checked { background-color: #29C7C9; border-color: #29C7C9; }
            #sellRadio::indicator:checked { background-color: #F85149; border-color: #F85149; }

            QDoubleSpinBox {
                background-color: #212635; border: 1px solid #3A4458;
                color: #E0E0E0; font-size: 14px; padding: 10px; border-radius: 6px;
            }
            QDoubleSpinBox:focus { border: 1px solid #29C7C9; }

            #totalValueLabel {
                color: #FFFFFF; font-size: 18px; font-weight: 500; padding: 12px;
                background-color: #212635; border-radius: 8px;
            }
            QPushButton {
                font-weight: bold; border-radius: 6px; padding: 12px; font-size: 14px;
            }
            #secondaryButton {
                background-color: #3A4458; color: #E0E0E0; border: none;
            }
            #secondaryButton:hover { background-color: #4A5568; }

            #primaryButton { border: none; }
            #primaryButton[transaction_type="BUY"] { background-color: #29C7C9; color: #161A25; }
            #primaryButton[transaction_type="BUY"]:hover { background-color: #32E0E3; }
            #primaryButton[transaction_type="SELL"] { background-color: #F85149; color: #161A25; }
            #primaryButton[transaction_type="SELL"]:hover { background-color: #FA6B64; }
        """)

    def _update_summary(self):
        qty = self.lots_spinbox.value() * self.contract.lot_size
        price = self.price_spinbox.value()
        total_value = qty * price
        self.total_value_label.setText(f"Est. Value: ₹{total_value:,.2f}")

    def update_contract_data(self, new_contract: Contract):
        self.contract = new_contract
        self.info_label.setText(f"Strike: ₹{int(self.contract.strike):,}  |  LTP: ₹{self.contract.ltp:.2f}")
        self.price_spinbox.setValue(calculate_smart_limit_price(self.contract))
        self._update_summary()
        logger.info(f"Quick Order Dialog refreshed for {self.contract.tradingsymbol}")

    def populate_from_order(self, order_data: dict):
        if order_data.get('transaction_type') == 'SELL':
            self.sell_radio.setChecked(True)
        else:
            self.buy_radio.setChecked(True)

        quantity = order_data.get('quantity', 0)
        lot_size = self.contract.lot_size if self.contract and self.contract.lot_size > 0 else 1
        lots = int(quantity / lot_size)
        self.lots_spinbox.setValue(lots)

        price = order_data.get('price', self.contract.ltp)
        self.price_spinbox.setValue(price)
        logger.info(f"Dialog populated for modifying order {order_data.get('order_id')}")

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None
        event.accept()