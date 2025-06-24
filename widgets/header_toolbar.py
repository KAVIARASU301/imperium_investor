# header_toolbar.py (Refactored)

import logging
from datetime import datetime
from typing import List, Dict, Any, Union

from PySide6.QtWidgets import (
    QToolBar, QLineEdit, QCompleter, QWidget, QLabel, QSizePolicy, QPushButton,
    QHBoxLayout, QFrame
)
from PySide6.QtCore import Signal, QStringListModel, Qt, QTimer
from PySide6.QtGui import QPainter, QColor, QFont, QPen
from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)


class NotificationBadge(QLabel):
    """Animated notification badge for buttons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.count = 0
        self.setFixedSize(18, 18)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setObjectName("notificationBadge")
        self.hide()

    def set_count(self, count: int):
        """Set badge count and visibility."""
        self.count = count
        if count > 0:
            display_text = str(count) if count < 100 else "99+"
            self.setText(display_text)
            self.show()
        else:
            self.hide()
        self.update()

    def paintEvent(self, event):
        """Custom paint event for circular badge."""
        if not self.isVisible():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#ff4444"))
        painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Arial", 7, QFont.Weight.Bold))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


class HeaderToolbar(QToolBar):
    """
    Refactored compact, modern toolbar with alert management and trading features.
    """
    symbol_selected = Signal(str)
    add_alert_requested = Signal()
    alert_manager_requested = Signal()
    order_history_requested = Signal()
    performance_dashboard_requested = Signal()
    market_depth_requested = Signal(str)
    timeframe_changed = Signal(str)
    buy_order_requested = Signal(str)  # Signal for buy order with symbol
    sell_order_requested = Signal(str)  # Signal for sell order with symbol

    def __init__(self, trader: Union[KiteConnect, Any], parent=None):
        super().__init__(parent)
        self.setMovable(False)
        self.setObjectName("enhancedHeaderToolbar")
        self.trader = trader
        self._instrument_map: Dict[str, Dict] = {}
        self._account_info = {'available_balance': 0.0, 'used_margin': 0.0, 'pnl': 0.0}

        self._init_ui()
        self._apply_styles()
        self._setup_timers()

    def _init_ui(self):
        """Initializes the enhanced UI components of the toolbar."""
        self._create_symbol_search_section()
        self._create_center_spacer()
        self._create_market_status_section()
        self._create_alert_section()
        self._create_trading_actions_section()
        self._create_account_section()

    def _create_symbol_search_section(self):
        """Creates an enhanced symbol search section with buy/sell buttons."""
        symbol_label = QLabel("SYMBOL:")
        symbol_label.setObjectName("symbolLabel")
        self.addWidget(symbol_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search symbols (e.g., RELIANCE)")
        self.search_input.setObjectName("enhancedSymbolSearch")
        self.search_input.returnPressed.connect(self._on_search_enter)
        self.addWidget(self.search_input)

        # Buy and Sell buttons
        self.buy_button = QPushButton("BUY")
        self.buy_button.setObjectName("buyButton")
        self.buy_button.setToolTip("Place Buy Order for Current Symbol")
        self.buy_button.setFixedSize(45, 24)
        self.buy_button.clicked.connect(self._on_buy_clicked)
        self.addWidget(self.buy_button)

        self.sell_button = QPushButton("SELL")
        self.sell_button.setObjectName("sellButton")
        self.sell_button.setToolTip("Place Sell Order for Current Symbol")
        self.sell_button.setFixedSize(45, 24)
        self.sell_button.clicked.connect(self._on_sell_clicked)
        self.addWidget(self.sell_button)

        self.completer = QCompleter(self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.search_input.setCompleter(self.completer)
        self.completer.activated.connect(self._on_search_enter)

    def _create_center_spacer(self):
        spacer = QWidget()
        spacer.setObjectName("centerSpacer")
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer)

    def _create_market_status_section(self):
        market_widget = QWidget()
        market_widget.setObjectName("marketStatusWidget")
        market_layout = QHBoxLayout(market_widget)
        market_layout.setContentsMargins(6, 2, 6, 2)
        market_layout.setSpacing(4)

        self.market_status_label = QLabel("●")
        self.market_status_label.setObjectName("marketStatusIndicator")
        market_layout.addWidget(self.market_status_label)

        self.market_text_label = QLabel("Market")
        self.market_text_label.setObjectName("marketStatusText")
        market_layout.addWidget(self.market_text_label)

        self.addWidget(market_widget)

    def _create_alert_section(self):
        """Creates an enhanced alert management section with consistent styling."""
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setObjectName("sectionSeparator")
        self.addWidget(separator)

        # Alert container widget with consistent dark styling
        alert_widget = QWidget()
        alert_widget.setObjectName("alertActionWidget")

        alert_layout = QHBoxLayout(alert_widget)
        alert_layout.setContentsMargins(4, 2, 4, 2)
        alert_layout.setSpacing(2)

        # Quick Alert Button with badge
        quick_alert_container = QWidget()
        quick_alert_container.setFixedSize(60, 24)
        self.quick_alert_button = QPushButton("Alert", quick_alert_container)
        self.quick_alert_button.setObjectName("alertActionButton")
        self.quick_alert_button.setToolTip("Quick Alert (Ctrl+A)")
        self.quick_alert_button.clicked.connect(self.add_alert_requested.emit)
        self.quick_alert_button.setGeometry(0, 0, 60, 24)
        self.active_badge = NotificationBadge(quick_alert_container)
        self.active_badge.move(46, -2)
        alert_layout.addWidget(quick_alert_container)

        # Alert Manager Button with badge (combines manager and history)
        manager_container = QWidget()
        manager_container.setFixedSize(85, 24)
        self.alert_manager_button = QPushButton("Alert Manager", manager_container)
        self.alert_manager_button.setObjectName("alertActionButton")
        self.alert_manager_button.setToolTip("Alert Manager & History (Ctrl+Shift+A)")
        self.alert_manager_button.clicked.connect(self.alert_manager_requested.emit)
        self.alert_manager_button.setGeometry(0, 0, 85, 24)
        self.triggered_badge = NotificationBadge(manager_container)
        self.triggered_badge.move(71, -2)
        alert_layout.addWidget(manager_container)

        self.addWidget(alert_widget)

    def _create_trading_actions_section(self):
        """Creates trading actions section with Order History and Performance Dashboard."""
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setObjectName("sectionSeparator")
        self.addWidget(separator)

        # Trading actions container widget
        actions_widget = QWidget()
        actions_widget.setObjectName("tradingActionWidget")

        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(4, 2, 4, 2)
        actions_layout.setSpacing(2)

        # Order History Button
        self.order_history_btn = QPushButton("Order History")
        self.order_history_btn.setObjectName("tradingActionButton")
        self.order_history_btn.setToolTip("View Order History")
        self.order_history_btn.clicked.connect(self.order_history_requested.emit)
        self.order_history_btn.setFixedSize(95, 24)
        actions_layout.addWidget(self.order_history_btn)

        # Performance Dashboard Button
        self.performance_btn = QPushButton("Performance")
        self.performance_btn.setObjectName("tradingActionButton")
        self.performance_btn.setToolTip("Performance Dashboard")
        self.performance_btn.clicked.connect(self.performance_dashboard_requested.emit)
        self.performance_btn.setFixedSize(85, 24)
        actions_layout.addWidget(self.performance_btn)

        self.addWidget(actions_widget)

    def _create_account_section(self):
        """Creates the account information display section."""
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setObjectName("sectionSeparator")
        self.addWidget(separator)

        self.account_info_widget = QWidget()
        self.account_info_widget.setObjectName("accountInfoWidget")
        account_layout = QHBoxLayout(self.account_info_widget)
        account_layout.setContentsMargins(8, 2, 8, 2)
        account_layout.setSpacing(8)

        self.user_id_label = QLabel("KE6286")
        self.user_id_label.setObjectName("userIdLabel")
        account_layout.addWidget(self.user_id_label)
        account_layout.addWidget(self._create_separator_dot())

        self.balance_label = QLabel("₹0")
        self.balance_label.setObjectName("balanceLabel")
        account_layout.addWidget(self.balance_label)

        self.addWidget(self.account_info_widget)

    @staticmethod
    def _create_separator_dot() -> QLabel:
        dot = QLabel("•")
        dot.setObjectName("separatorDot")
        return dot

    def _setup_timers(self):
        QTimer.singleShot(1000, self._refresh_account_info)
        self.account_timer = QTimer(self)
        self.account_timer.timeout.connect(self._refresh_account_info)
        self.account_timer.start(30000)

        self.market_timer = QTimer(self)
        self.market_timer.timeout.connect(self._update_market_status)
        self.market_timer.start(60000)

    def _update_market_status(self):
        """Updates market status indicator."""
        current_time = datetime.now().time()
        market_open = datetime.strptime("09:15", "%H:%M").time() <= current_time <= datetime.strptime("15:30",
                                                                                                      "%H:%M").time()
        self.show_connection_status(market_open)

    def _refresh_account_info(self):
        """Enhanced account information refresh."""
        try:
            actual_trader = self._get_actual_trader()
            if not actual_trader:
                self._show_demo_mode()
                return

            profile = actual_trader.profile()
            margins = actual_trader.margins()
            equity_margins = margins.get('equity', {})

            self._account_info = {
                'user_id': profile.get('user_id', 'N/A'),
                'available_balance': equity_margins.get('available', {}).get('live_balance', 0.0),
                'used_margin': equity_margins.get('utilised', {}).get('total', 0.0)
            }
            self._update_account_display()

        except Exception as e:
            logger.error(f"Failed to refresh account info: {e}")
            self._show_error_state()

    def _update_account_display(self):
        """Updates the UI labels for account information."""
        self.user_id_label.setText(self._account_info.get('user_id', 'N/A'))
        self._format_and_set_balance(self._account_info.get('available_balance', 0.0))

    def _get_actual_trader(self):
        """Gets the actual KiteConnect client."""
        if hasattr(self.trader, 'profile') and hasattr(self.trader, 'margins'):
            return self.trader
        return None

    def _show_demo_mode(self):
        """Shows demo mode when no trading client is available."""
        self.user_id_label.setText("DEMO")
        self.balance_label.setText("₹--")

    def _show_error_state(self):
        """Shows error state briefly."""
        self.user_id_label.setText("ERROR")
        self.balance_label.setText("₹--")

    def _format_indian_currency(self, amount: float) -> str:
        """Format currency in Indian numbering system (lakhs, crores)."""
        if amount == 0:
            return "₹0"

        # Convert to string and handle negative numbers
        is_negative = amount < 0
        amount = abs(amount)
        amount_str = f"{amount:.0f}"

        # Indian number formatting: first 3 digits, then groups of 2
        if len(amount_str) <= 3:
            formatted = amount_str
        else:
            # First 3 digits from right
            last_three = amount_str[-3:]
            remaining = amount_str[:-3]

            # Add commas every 2 digits for the remaining part
            formatted_remaining = ""
            for i, digit in enumerate(reversed(remaining)):
                if i > 0 and i % 2 == 0:
                    formatted_remaining = "," + formatted_remaining
                formatted_remaining = digit + formatted_remaining

            formatted = formatted_remaining + "," + last_three

        prefix = "-₹" if is_negative else "₹"
        return prefix + formatted

    def _format_and_set_balance(self, balance: float):
        self.balance_label.setText(self._format_indian_currency(balance))

    def set_instrument_data(self, instruments: List[Dict[str, Any]]):
        symbols = [inst['tradingsymbol'] for inst in instruments if 'tradingsymbol' in inst]
        self._instrument_map = {inst['tradingsymbol']: inst for inst in instruments if 'tradingsymbol' in inst}
        model = QStringListModel(symbols)
        self.completer.setModel(model)

    def update_alert_counts(self, active_count: int, triggered_today: int):
        self.active_badge.set_count(active_count)
        self.triggered_badge.set_count(triggered_today)

    def set_current_symbol(self, symbol: str):
        """Set the current symbol in the search input."""
        self.search_input.setText(symbol)

    def get_current_symbol(self) -> str:
        """Get the current symbol from the search input."""
        return self.search_input.text().upper().strip()

    def _on_search_enter(self, text=""):
        symbol = (text or self.search_input.text()).upper().strip()
        if symbol and symbol in self._instrument_map:
            self.symbol_selected.emit(symbol)
        elif symbol:
            logger.warning(f"Invalid symbol entered: {symbol}")

    def _on_buy_clicked(self):
        """Handle buy button click."""
        symbol = self.search_input.text().upper().strip()
        if symbol and symbol in self._instrument_map:
            self.buy_order_requested.emit(symbol)
        else:
            # Try to get current symbol from chart if input is empty
            if not symbol:
                logger.warning("No symbol entered. Please enter a symbol in the search field.")
            else:
                logger.warning(f"Invalid symbol '{symbol}' entered for buy order")

    def _on_sell_clicked(self):
        """Handle sell button click."""
        symbol = self.search_input.text().upper().strip()
        if symbol and symbol in self._instrument_map:
            self.sell_order_requested.emit(symbol)
        else:
            # Try to get current symbol from chart if input is empty
            if not symbol:
                logger.warning("No symbol entered. Please enter a symbol in the search field.")
            else:
                logger.warning(f"Invalid symbol '{symbol}' entered for sell order")

    def _apply_styles(self):
        self.setStyleSheet("""
            QToolBar#enhancedHeaderToolbar {
                background-color: #1a1a1a;
                border-bottom: 3px solid #404040;
                padding: 2px 8px;
                spacing: 8px;
                max-height: 40px;
            }
            #centerSpacer {
                background-color: transparent;
            }
            #symbolLabel { 
                background-color: #1a1a1a; 
                color: #ffffff; 
                font-size: 12px; 
                font-weight: 900; 
                text-transform: uppercase;
                letter-spacing: 1px;
                padding-right: 8px;
            }
            #enhancedSymbolSearch {
                background-color: #000000;
                border: 1px solid #333333; 
                color: #ffffff; 
                padding: 6px 12px; 
                border-radius: 3px;
                font-size: 10px; 
                font-weight: 500;
                min-width: 180px; 
                max-width: 220px; 
                max-height: 24px;
            }
            #enhancedSymbolSearch:focus { 
                border: 1px solid #00d4ff; 
                color: #00d4ff;
            }
            #buyButton {
                background-color: #000000;
                color: white;
                border: 1px solid #333333;
                padding: 6px 8px;
                border-radius: 3px;
                font-size: 10px;
                font-weight: 600;
                text-transform: uppercase;
            }
            #buyButton:hover {
                background-color: #1a5928;
                border: 1px solid #4aff4a;
                color: #4aff4a;
            }
            #sellButton {
                background-color: #000000;
                color: white;
                border: 1px solid #333333;
                padding: 6px 8px;
                border-radius: 3px;
                font-size: 10px;
                font-weight: 600;
                text-transform: uppercase;
            }
            #sellButton:hover {
                background-color: #5a1a1a;
                border: 1px solid #ff4444;
                color: #ff4444;
            }
            #marketStatusWidget { 
                background-color: #1a1a1a; 
                border-radius: 4px; 
                border: 1px solid #333; 
            }
            #marketOpen { 
                background-color: #1a1a1a;
                color: #00ff00; 
            } 
            #marketClosed { 
                background-color: #1a1a1a;
                color: #ff4444; 
            }
            #marketStatusText { 
                background-color: #1a1a1a;
                color: #cccccc; 
                font-size: 10px; 
                font-weight: 600; 
            }
            #alertActionWidget, #tradingActionWidget {
                background-color: #1a1a1a;
            }
            #alertActionButton, #tradingActionButton {
                background-color: #000000;
                color: white;
                border: 1px solid #333333;
                padding: 6px 8px;
                border-radius: 3px;
                font-size: 10px;
                font-weight: 500;
            }
            #alertActionButton:hover, #tradingActionButton:hover {
                background-color: #1a1a1a;
                border: 1px solid #00d4ff;
                color: #00d4ff;
            }
            #accountInfoWidget {
                background-color: #1a1a1a;
                border: 1px solid #333333; 
                border-radius: 6px; 
                padding: 4px 8px;
            }
            #userIdLabel { 
                background-color: #1a1a1a;
                color: #00d4ff; 
                font-size: 11px; 
                font-weight: 700; 
            }
            #balanceLabel { 
                background-color: #1a1a1a;
                color: #4aff4a; 
                font-size: 11px; 
                font-weight: 600; 
            }
            #separatorDot { 
                background-color: #1a1a1a;
                color: #666666; 
                font-size: 8px; 
            }
            #sectionSeparator { 
                background-color: #404040; 
                max-width: 1px; 
                margin: 4px 2px; 
            }
        """)

    def show_connection_status(self, connected: bool):
        """Shows connection status in the market status area."""
        if connected:
            self.market_text_label.setText("Connected")
            self.market_status_label.setObjectName("marketOpen")
        else:
            self.market_text_label.setText("Disconnected")
            self.market_status_label.setObjectName("marketClosed")

        self.market_status_label.style().polish(self.market_status_label)

    def closeEvent(self, event):
        """Clean up timers when closing."""
        self.account_timer.stop()
        self.market_timer.stop()
        super().closeEvent(event)