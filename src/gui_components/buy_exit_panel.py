# src/gui_components/buy_exit_panel.py
import logging
from typing import List, Dict
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QSpinBox, QGroupBox, QRadioButton, QButtonGroup, QFrame, QAbstractSpinBox
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from kiteconnect import KiteConnect

from src.utils.data_models import OptionType, Contract

logger = logging.getLogger(__name__)


class ClickableLabel(QLabel):
    """A QLabel that emits a signal on double-click for toggling."""
    doubleClicked = Signal()

    def mouseDoubleClickEvent(self, event):
        self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)


class BuyExitPanel(QWidget):
    """
    A redesigned, compact, and unified panel for buying and exiting option positions,
    styled with a rich, metallic, and premium modern UI.
    """
    buy_clicked = Signal(dict)
    exit_clicked = Signal(OptionType)

    def __init__(self, kite_client: KiteConnect):
        super().__init__()
        self.kite = kite_client
        self.option_type = OptionType.CALL
        self.contracts_above = 0
        self.contracts_below = 0
        self.lot_size = 1
        self.lot_quantity = 50
        self.current_symbol = "NIFTY"
        self.expiry = ""
        self.atm_strike = 0.0
        self.strike_interval = 50.0
        self.strike_ladder_data = []
        self.radio_history = []

        self._setup_ui()
        self._apply_styles()
        self._update_ui_for_option_type()

    def _setup_ui(self):
        self.setObjectName("buyExitPanel")
        self.setFixedWidth(280)
        main_layout = QVBoxLayout(self)
        # FIX: Reduced margins and spacing for a more compact layout
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(10)

        self.title_label = ClickableLabel(self.option_type.name)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setCursor(QCursor(Qt.PointingHandCursor))
        self.title_label.setToolTip("Double-click to toggle between CALL and PUT")
        self.title_label.doubleClicked.connect(self.toggle_option_type)
        main_layout.addWidget(self.title_label)

        main_layout.addWidget(self._create_info_summary())
        main_layout.addWidget(self._create_strike_selection_group())

        main_layout.addStretch()
        main_layout.addLayout(self._create_action_buttons())

    def _create_strike_selection_group(self):
        group = QGroupBox("STRIKE SELECTION")
        group.setObjectName("selectionGroup")
        layout = QGridLayout(group)
        # FIX: Reduced spacing and margins
        layout.setSpacing(8)
        layout.setContentsMargins(10, 15, 10, 10)

        layout.addWidget(QLabel("Below ATM:"), 0, 0)
        self.below_spin = self._create_spinbox()
        layout.addWidget(self.below_spin, 0, 1)

        layout.addWidget(QLabel("Above ATM:"), 1, 0)
        self.above_spin = self._create_spinbox()
        layout.addWidget(self.above_spin, 1, 1)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setObjectName("divider")
        layout.addWidget(divider, 2, 0, 1, 2)

        layout.addWidget(QLabel("Skip Logic:"), 3, 0, 1, 2, Qt.AlignmentFlag.AlignCenter)
        radio_widget = self._create_radio_buttons()
        layout.addWidget(radio_widget, 4, 0, 1, 2, Qt.AlignmentFlag.AlignCenter)
        return group

    def _create_spinbox(self):
        spinbox = QSpinBox()
        spinbox.setRange(0, 10)
        spinbox.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spinbox.setAlignment(Qt.AlignCenter)
        spinbox.valueChanged.connect(self._update_margin)
        return spinbox

    def _create_radio_buttons(self):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 5, 0, 0)
        layout.setSpacing(15)

        self.radio_buttons = []
        self.radio_group = QButtonGroup()
        self.radio_group.setExclusive(False)

        for i in range(4):
            radio = QRadioButton(str(i))
            radio.toggled.connect(self._create_radio_handler(i))
            self.radio_group.addButton(radio)
            self.radio_buttons.append(radio)
            layout.addWidget(radio)

        self.radio_buttons[0].setChecked(True)
        return widget

    def _create_info_summary(self):
        info_frame = QFrame()
        info_frame.setObjectName("infoFrame")
        layout = QGridLayout(info_frame)
        # FIX: Reduced margins and spacing
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(5)

        strikes_title_label = QLabel("Total Strikes")
        lots_title_label = QLabel("Lots × Qty")

        layout.addWidget(strikes_title_label, 0, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lots_title_label, 0, 1, Qt.AlignmentFlag.AlignCenter)

        self.total_contracts_value_label = QLabel("0")
        self.total_contracts_value_label.setObjectName("infoValue")
        self.lot_info_label = QLabel("0 × 0")
        self.lot_info_label.setObjectName("infoValue")

        layout.addWidget(self.total_contracts_value_label, 1, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lot_info_label, 1, 1, Qt.AlignmentFlag.AlignCenter)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setObjectName("divider")
        layout.addWidget(divider, 2, 0, 1, 2)

        layout.addWidget(QLabel("Estimated Premium"), 3, 0, 1, 2, Qt.AlignmentFlag.AlignCenter)
        self.margin_label = QLabel("₹0")
        self.margin_label.setObjectName("marginValue")
        layout.addWidget(self.margin_label, 4, 0, 1, 2, Qt.AlignmentFlag.AlignCenter)

        return info_frame

    def _create_action_buttons(self):
        layout = QHBoxLayout()
        layout.setSpacing(10)

        self.buy_button = QPushButton("BUY")
        self.buy_button.setObjectName("primaryButton")
        self.buy_button.clicked.connect(self._on_buy_clicked)

        self.exit_button = QPushButton("EXIT")
        self.exit_button.setObjectName("dangerButton")
        self.exit_button.clicked.connect(lambda: self.exit_clicked.emit(self.option_type))

        layout.addWidget(self.exit_button)
        layout.addWidget(self.buy_button)
        return layout

    def _apply_styles(self):
        """Applies a premium, metallic, and modern dark theme stylesheet."""
        self.setStyleSheet("""
            #buyExitPanel {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                                  stop:0 #2A3140, stop:1 #161A25);
                border: 1px solid #3A4458;
                border-radius: 12px;
                font-family: "Segoe UI", sans-serif;
            }
            #panelTitleCall, #panelTitlePut {
                font-size: 22px; font-weight: 600; padding: 6px;
                border-radius: 8px;
                border: 1px solid transparent;
            }
            #panelTitleCall {
                background-color: rgba(41, 199, 201, 0.1); color: #29C7C9;
                border-color: rgba(41, 199, 201, 0.3);
            }
            #panelTitlePut {
                background-color: rgba(248, 81, 73, 0.1); color: #F85149;
                border-color: rgba(248, 81, 73, 0.3);
            }
            #selectionGroup {
                color: #A9B1C3; border: 1px solid #2A3140; border-radius: 8px;
                font-size: 11px; margin-top: 8px; padding-top: 12px; font-weight: bold;
            }
            #selectionGroup::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
            #infoFrame { background-color: rgba(13, 17, 23, 0.5); border-radius: 8px; }
            #divider { background-color: #3A4458; height: 1px; border: none; }
            QLabel { color: #A9B1C3; font-size: 12px; }
            #infoValue { color: #E0E0E0; font-size: 16px; font-weight: 600; }
            #marginValue { color: #FFFFFF; font-size: 24px; font-weight: 300; }
            QSpinBox {
                background-color: #212635; color: #E0E0E0; border: 1px solid #3A4458;
                border-radius: 6px; font-size: 14px; padding: 2px; font-weight: 600;
            }
            QSpinBox:focus { border-color: #29C7C9; }
            QRadioButton { color: #A9B1C3; spacing: 5px; font-weight: bold; }
            QRadioButton::indicator {
                width: 11px; height: 12px; border-radius: 6px;
                background-color: #2A3140; border: 0px solid #3A4458;
            }
            #callRadio::indicator:checked { background-color: #29C7C9; border-color: #29C7C9; }
            #putRadio::indicator:checked { background-color: #F85149; border-color: #F85149; }
            QPushButton {
                font-weight: bold; border-radius: 6px;
                padding: 10px; font-size: 14px; border: none;
            }
            #primaryButton { background-color: #29C7C9; color: #161A25; }
            #primaryButton:hover { background-color: #32E0E3; }
            #dangerButton { background-color: #F85149; color: #161A25; }
            #dangerButton:hover { background-color: #FA6B64; }
        """)

    # --- All backend and logic methods are preserved below ---

    def toggle_option_type(self):
        self.option_type = OptionType.PUT if self.option_type == OptionType.CALL else OptionType.CALL
        logger.info(f"Toggled panel to {self.option_type.name}")
        self._update_ui_for_option_type()

    def _update_ui_for_option_type(self):
        self.title_label.setText(self.option_type.name)
        if self.option_type == OptionType.CALL:
            self.title_label.setObjectName("panelTitleCall")
            for radio in self.radio_buttons:
                radio.setObjectName("callRadio")
        else:
            self.title_label.setObjectName("panelTitlePut")
            for radio in self.radio_buttons:
                radio.setObjectName("putRadio")
        self.style().unpolish(self.title_label)
        self.style().polish(self.title_label)
        for radio in self.radio_buttons:
            self.style().unpolish(radio)
            self.style().polish(radio)
        self._update_margin()

    def _on_buy_clicked(self):
        generated_strikes = self._generate_strikes_with_skip_logic()
        if not generated_strikes:
            logger.error("No strikes generated for order")
            return
        total_premium = sum(s['ltp'] * self.lot_size * self.lot_quantity for s in generated_strikes)
        order_details = {"symbol": self.current_symbol, "option_type": self.option_type, "expiry": self.expiry,
                         "contracts_above": self.contracts_above, "contracts_below": self.contracts_below,
                         "lot_size": self.lot_size, "strikes": generated_strikes,
                         "total_premium_estimate": total_premium}
        self.buy_clicked.emit(order_details)

    def _update_margin(self):
        self.contracts_above = self.above_spin.value()
        self.contracts_below = self.below_spin.value()
        strikes = self._generate_strikes_with_skip_logic()
        total_contracts = len(strikes)
        self.total_contracts_value_label.setText(str(total_contracts))
        total_premium = sum(s['ltp'] * self.lot_size * self.lot_quantity for s in strikes)
        self.margin_label.setText(f"₹{total_premium:,.0f}")

    def update_parameters(self, symbol: str, lot_size: int, lot_quantity: int, expiry: str):
        self.current_symbol = symbol
        self.lot_size = lot_size
        self.lot_quantity = lot_quantity
        self.expiry = expiry
        self.lot_info_label.setText(f"{lot_size} × {lot_quantity}")
        self._update_margin()

    def update_strike_ladder(self, atm_strike: float, interval: float, ladder_data: List[Dict]):
        self.atm_strike = atm_strike
        self.strike_interval = interval
        self.strike_ladder_data = ladder_data
        self._update_margin()

    def _create_radio_handler(self, index: int):
        def handler(checked: bool):
            if checked:
                self.radio_history.append(index)
                if len(self.radio_history) > 2:
                    self.radio_buttons[self.radio_history.pop(0)].setChecked(False)
            elif index in self.radio_history:
                self.radio_history.remove(index)
            self._update_margin()

        return handler

    def _get_skip_strategy(self):
        selected = {i for i, b in enumerate(self.radio_buttons) if b.isChecked()}
        if not selected: return 0, 0
        if len(selected) == 1: return list(selected)[0], 0
        if selected == {0, 2}: return 0, 1
        if selected == {0, 3}: return 0, 2
        if selected == {1, 2}: return 1, 0
        if selected == {1, 3}: return 1, 1
        return list(selected)[0], 0

    def _generate_strikes_with_skip_logic(self) -> List[Dict]:
        if not self.strike_ladder_data: return []
        atm_offset, skip_count = self._get_skip_strategy()
        try:
            atm_index = next((i for i, d in enumerate(self.strike_ladder_data) if d.get('strike') == self.atm_strike),
                             -1)
        except (ValueError, TypeError):
            atm_index = -1
        if atm_index < 0:
            logger.debug(f"Could not find ATM strike {self.atm_strike} in ladder data.")
            return []
        adj_atm_idx = atm_index + atm_offset
        indices = {adj_atm_idx}
        step = skip_count + 1
        for i in range(1, self.contracts_above + 1): indices.add(adj_atm_idx + (i * step))
        for i in range(1, self.contracts_below + 1): indices.add(adj_atm_idx - (i * step))
        strikes = []
        for idx in sorted(list(indices)):
            if 0 <= idx < len(self.strike_ladder_data):
                data = self.strike_ladder_data[idx]
                key_prefix = 'call' if self.option_type == OptionType.CALL else 'put'
                contract: Contract = data.get(f'{key_prefix}_contract')
                if contract:
                    strikes.append({"strike": data['strike'], "ltp": contract.ltp, "contract": contract})
        return strikes