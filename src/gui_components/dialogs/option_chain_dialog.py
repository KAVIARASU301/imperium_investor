import logging
from typing import Dict, Optional, List
from datetime import datetime
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QWidget, QTableWidget, QHeaderView,
                               QTableWidgetItem, QAbstractItemView, QLabel, QComboBox,
                               QHBoxLayout, QFrame, QPushButton)
from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QColor, QFont

from src.utils.bs_greeks import calculate_greeks
from datetime import datetime

from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)

INDEX_SYMBOL_MAP = {
    'NIFTY': 'NIFTY 50',
    'BANKNIFTY': 'NIFTY BANK',
    'FINNIFTY': 'NIFTY FIN SERVICE',
    'MIDCPNIFTY': 'NIFTY MID SELECT'
}


def _format_large_number(n: float) -> str:
    """Formats a large number into a compact string with K, L, or Cr suffixes."""
    sign = "+" if n > 0 else ""
    if abs(n) >= 1_00_00_000:
        return f"{sign}{n / 1_00_00_000:.2f}Cr"
    elif abs(n) >= 1_00_000:
        return f"{sign}{n / 1_00_000:.2f}L"
    elif abs(n) >= 1_000:
        return f"{sign}{n / 1_000:.1f}K"
    return f"{n:+,}" if n != 0 else "0"


class OptionChainDialog(QDialog):
    """A premium, live Option Chain dialog with a consistent dark theme."""

    def __init__(self, real_kite_client: KiteConnect, instrument_data: Dict, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.kite = real_kite_client
        self.instrument_data = instrument_data
        self.contracts_data: Dict[float, Dict[str, dict]] = {}
        self.underlying_instrument = ""
        self.underlying_ltp = 0.0
        self._drag_pos = None
        self._is_initialized = False

        self._setup_window()
        self._setup_ui()
        self._connect_signals()
        self._apply_styles()

        # Create the timer but do not start it here
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._fetch_market_data)

    def showEvent(self, event):
        """Override showEvent to initialize data fetching and timers only when the dialog is shown."""
        if not self._is_initialized:
            logger.info("Option Chain dialog opened. Initializing data fetch...")
            # Populate controls and fetch initial data
            self._populate_controls()
            # Now, start the recurring updates
            self.update_timer.start(2000)
            self._is_initialized = True
        super().showEvent(event)

    def closeEvent(self, event):
        """Override closeEvent to ensure timers are stopped cleanly."""
        logger.info("Closing Option Chain dialog. Stopping update timer.")
        self.update_timer.stop()
        super().closeEvent(event)

    def _setup_window(self):
        """Configure window properties for a frameless, modern design."""
        self.setWindowTitle("Live Option Chain")
        self.resize(1400, 800)
        self.setModal(False)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog | Qt.WindowMaximizeButtonHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def _setup_ui(self):
        """Initialize UI components with the new premium layout."""
        container = QWidget(self)
        container.setObjectName("mainContainer")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(15, 10, 15, 15)
        container_layout.setSpacing(10)

        container_layout.addLayout(self._create_header())
        container_layout.addWidget(self._create_toolbar())
        self.chain_widget = OptionChainWidget(self)
        container_layout.addWidget(self.chain_widget, 1)

    def _create_header(self):
        """Creates a custom title bar with window controls."""
        header_layout = QHBoxLayout()
        title = QLabel("Live Option Chain")
        title.setObjectName("dialogTitle")

        self.maximize_btn = QPushButton("🗖")
        self.maximize_btn.setObjectName("windowControlButton")
        self.maximize_btn.setFixedSize(28, 28)

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeButton")
        self.close_btn.setFixedSize(28, 28)

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(self.maximize_btn)
        header_layout.addWidget(self.close_btn)
        return header_layout

    def _create_toolbar(self) -> QWidget:
        """Creates the top toolbar for symbol and expiry selection."""
        toolbar = QWidget()
        toolbar.setObjectName("toolbar")
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(15)

        layout.addWidget(QLabel("Symbol:"))
        self.symbol_combo = QComboBox()
        layout.addWidget(self.symbol_combo)

        layout.addWidget(QLabel("Expiry:"))
        self.expiry_combo = QComboBox()
        layout.addWidget(self.expiry_combo)

        layout.addStretch()

        self.ltp_label = QLabel("LTP: 0.00")
        self.ltp_label.setObjectName("ltpLabel")
        layout.addWidget(self.ltp_label)

        return toolbar

    def _apply_styles(self):
        """Applies the application's rich and premium dark theme."""
        self.setStyleSheet("""
            #mainContainer {
                background-color: #161A25;
                border: 1px solid #3A4458;
                border-radius: 12px;
                font-family: "Segoe UI", sans-serif;
            }
            #dialogTitle { color: #FFFFFF; font-size: 16px; font-weight: 600; }
            #windowControlButton, #closeButton {
                background-color: transparent; border: none; color: #8A9BA8;
                font-size: 16px; font-weight: bold;
            }
            #windowControlButton:hover, #closeButton:hover { color: #FFFFFF; }
            #toolbar { background-color: #212635; border-radius: 8px; }
            QLabel { color: #A9B1C3; font-weight: 600; }
            #ltpLabel { color: #FFFFFF; }
            QComboBox {
                background-color: #2A3140; color: #E0E0E0;
                border: 1px solid #3A4458; border-radius: 6px;
                padding: 6px 10px;
            }
            QComboBox:focus { border-color: #29C7C9; }
            QComboBox::drop-down { border: none; }
        """)

    def _connect_signals(self):
        self.symbol_combo.currentTextChanged.connect(self._on_symbol_change)
        self.expiry_combo.currentTextChanged.connect(self._fetch_and_build_chain)
        self.maximize_btn.clicked.connect(self._toggle_maximize)
        self.close_btn.clicked.connect(self.close)

    def _toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _populate_controls(self):
        if self.instrument_data:
            self.symbol_combo.addItems(sorted(self.instrument_data.keys()))
            if "NIFTY" in self.instrument_data:
                self.symbol_combo.setCurrentText("NIFTY")
        self._on_symbol_change()

    def _on_symbol_change(self):
        symbol = self.symbol_combo.currentText()
        if not symbol: return
        self.underlying_instrument = f"NSE:{INDEX_SYMBOL_MAP.get(symbol, symbol)}"
        self.expiry_combo.blockSignals(True)
        self.expiry_combo.clear()
        if symbol_data := self.instrument_data.get(symbol):
            expiries = [exp.strftime('%d-%b-%Y') for exp in symbol_data.get('expiries', [])]
            self.expiry_combo.addItems(expiries)
        self.expiry_combo.blockSignals(False)
        self._fetch_and_build_chain()

    def _fetch_and_build_chain(self):
        symbol = self.symbol_combo.currentText()
        expiry_str = self.expiry_combo.currentText()
        if not symbol or not expiry_str: return
        self.contracts_data = {}
        expiry_date = datetime.strptime(expiry_str, '%d-%b-%Y').date()
        if symbol_data := self.instrument_data.get(symbol):
            for inst in symbol_data.get('instruments', []):
                if inst.get('expiry') == expiry_date:
                    strike = inst.get('strike')
                    opt_type = inst.get('instrument_type')
                    if strike not in self.contracts_data: self.contracts_data[strike] = {}
                    self.contracts_data[strike][opt_type] = inst
        self._fetch_market_data(is_initial_load=True)

    def _fetch_market_data(self, is_initial_load=False):
        tokens_to_fetch = [self.underlying_instrument]
        for strike_map in self.contracts_data.values():
            for contract in strike_map.values():
                tokens_to_fetch.append(f"NFO:{contract['tradingsymbol']}")
        if not tokens_to_fetch:
            return
        try:
            market_data = self.kite.quote(tokens_to_fetch)
            if self.underlying_instrument in market_data:
                self.underlying_ltp = market_data[self.underlying_instrument].get('last_price', 0.0)
                self.ltp_label.setText(f"LTP: ₹{self.underlying_ltp:,.2f}")

            expiry_str = self.expiry_combo.currentText()
            expiry_date = datetime.strptime(expiry_str, '%d-%b-%Y').date() if expiry_str else None

            self.chain_widget.update_chain(self.contracts_data, market_data, self.underlying_ltp, expiry_date)

            if is_initial_load:
                QTimer.singleShot(150, self.chain_widget.center_on_atm)
        except Exception as e:
            logger.error(f"Failed to fetch option chain market data: {e}")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()


class OptionChainWidget(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.atm_strike = 0
        self.underlying_ltp = 0.0
        self.expiry_date = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget()
        self._setup_table()
        layout.addWidget(self.table)
        self._apply_styles()

    def _setup_table(self):
        self.table.setColumnCount(19)
        headers = [
            "OI", "OI C", "OI C%", "LTP", "IV", "D", "T", "V", "G",
            "Strike",
            "G", "V", "T", "D", "IV", "LTP", "OI C%", "OI C", "OI"
        ]
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(9, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(9, 120)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)

    def update_chain(self, contracts_data: Dict, market_data: Dict, underlying_ltp: float, expiry_date):
        self.table.setUpdatesEnabled(False)
        if not underlying_ltp:
            self.table.setUpdatesEnabled(True)
            return
        self.underlying_ltp = underlying_ltp
        self.expiry_date = expiry_date
        self.table.setRowCount(0)

        strikes = sorted(list(contracts_data.keys()))
        if not strikes:
            self.table.setUpdatesEnabled(True)
            return

        if len(strikes) > 1:
            strike_step = strikes[1] - strikes[0]
            self.atm_strike = round(underlying_ltp / strike_step) * strike_step
        else:
            self.atm_strike = strikes[0] if strikes else 0

        for strike in strikes:
            row_pos = self.table.rowCount()
            self.table.insertRow(row_pos)
            self.table.setRowHeight(row_pos, 38)

            is_atm_strike = strike == self.atm_strike

            strike_item = self._create_item(f"{strike:,.0f}", is_strike=True, is_atm=is_atm_strike)
            self.table.setItem(row_pos, 9, strike_item)

            if call_contract := contracts_data[strike].get('CE'):
                is_itm = strike < underlying_ltp
                self._populate_side(row_pos, 'call', call_contract, market_data, is_itm, is_atm_strike)
            if put_contract := contracts_data[strike].get('PE'):
                is_itm = strike > underlying_ltp
                self._populate_side(row_pos, 'put', put_contract, market_data, is_itm, is_atm_strike)
        self.table.setUpdatesEnabled(True)

    def _populate_side(self, row, side, contract, market_data, is_itm, is_atm):
        quote_key = f"NFO:{contract.get('tradingsymbol')}"
        data = market_data.get(quote_key, {})
        ltp = data.get('last_price', 0)

        greeks = calculate_greeks(
            spot_price=self.underlying_ltp,
            strike_price=contract['strike'],
            expiry_date=self.expiry_date,
            option_price=ltp,
            is_call=(side == 'call')
        )
        iv = greeks['iv']
        delta = greeks['delta']
        theta = greeks['theta']
        gamma = greeks['gamma']
        vega = greeks['vega']

        oi = data.get('oi', 0)
        prev_day_oi = data.get('oi_day_high', 0) if data.get('oi_day_high', 0) > 0 else oi
        oi_change = oi - prev_day_oi
        oi_change_pct = (oi_change / prev_day_oi * 100) if prev_day_oi > 0 else 0

        if side == 'call':
            columns = [
                (_format_large_number(oi).replace('+', ''), {}),
                (_format_large_number(oi_change), {'oi_change': oi_change}),
                (f"{oi_change_pct:+.1f}%", {'oi_change': oi_change}),
                (f"{ltp:.2f}", {}), (f"{iv:.1f}%", {}), (f"{delta:.2f}", {}),
                (f"{theta:.2f}", {}), (f"{vega:.2f}", {}), (f"{gamma:.4f}", {})
            ]
            start_col = 0
        else:  # 'put' side
            columns = [
                (f"{gamma:.4f}", {}), (f"{vega:.2f}", {}), (f"{theta:.2f}", {}),
                (f"{delta:.2f}", {}), (f"{iv:.1f}%", {}), (f"{ltp:.2f}", {}),
                (f"{oi_change_pct:+.1f}%", {'oi_change': oi_change}),
                (_format_large_number(oi_change), {'oi_change': oi_change}),
                (_format_large_number(oi).replace('+', ''), {})
            ]
            start_col = 10

        for i, (text, style) in enumerate(columns):
            item = self._create_item(text, is_itm, is_atm=is_atm, side=side, **style)
            self.table.setItem(row, start_col + i, item)

    def _create_item(self, text, is_itm=False, is_strike=False, is_atm=False, oi_change=None, side=None):
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)

        # Precedence of coloring: Strike > ITM > ATM Row
        if is_strike:
            if is_atm:
                item.setBackground(QColor("#FFD700"))  # Bright Gold for ATM Strike
                item.setForeground(QColor("#000000"))  # Black text for readability
            else:
                item.setBackground(QColor("#212635"))  # Default strike BG
                item.setForeground(QColor("#FFFFFF"))
        elif is_itm:
            # ITM color takes precedence over the base ATM row highlight
            color_map = {'call': "#29C7C9", 'put': "#F85149"}
            itm_bg = QColor(color_map.get(side, "#FFFFFF"))
            itm_bg.setAlpha(35)
            item.setBackground(itm_bg)
        elif is_atm:
            # A subtle highlight for non-ITM cells in the ATM row
            atm_bg = QColor("#FFD700")
            atm_bg.setAlpha(15)
            item.setBackground(atm_bg)

        # OI Change color logic is applied to text, independent of background
        if oi_change is not None:
            if oi_change > 0:
                item.setForeground(QColor("#29C7C9"))
            elif oi_change < 0:
                item.setForeground(QColor("#F85149"))

        return item

    def center_on_atm(self):
        for row in range(self.table.rowCount()):
            strike_item = self.table.item(row, 9)
            if strike_item and float(strike_item.text().replace(",", "")) == self.atm_strike:
                self.table.scrollToItem(strike_item, QAbstractItemView.ScrollHint.PositionAtCenter)
                return

    def _apply_styles(self):
        self.setStyleSheet("""
            QTableWidget {
                background-color: #161A25; color: #A9B1C3; gridline-color: #2A3140;
                border: 1px solid #2A3140; border-radius: 8px; font-size: 13px; font-family: "Segoe UI";
            }
            QHeaderView::section {
                background-color: #212635; color: #A9B1C3; padding: 10px 6px;
                border: none; border-bottom: 1px solid #3A4458;
                font-weight: bold; font-size: 11px; text-transform: uppercase;
            }
            QTableWidget::item {
                padding: 8px 6px;
                border-bottom: 1px solid #2A3140; border-right: 1px solid #2A3140;
            }
            QTableWidget::item:selected {
                background-color: #3A4458; color: #FFFFFF;
            }
        """)