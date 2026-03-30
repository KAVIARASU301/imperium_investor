# header_toolbar.py (Updated with Status Bar)

import logging
from typing import List, Dict, Any, Union

from PySide6.QtWidgets import (
    QToolBar, QLineEdit, QCompleter, QWidget, QLabel, QSizePolicy, QPushButton,
    QHBoxLayout, QFrame
)
from PySide6.QtCore import Signal, QStringListModel, Qt, QTimer, QEvent
from PySide6.QtGui import QPainter, QColor, QFont, QKeyEvent
from kiteconnect import KiteConnect

# Import the simple status bar
from kite.widgets.status_bar import StatusBar, status

logger = logging.getLogger(__name__)
DEFAULT_PAPER_BALANCE = 1_000_000.0


class NotificationBadge(QLabel):
    """Animated notification badge for buttons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.count = 0
        self.setFixedSize(15, 15)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setObjectName("notificationBadge")
        self.hide()
        self.setContentsMargins(0, 0, 0, 0)

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

        # Draw white number
        painter.setPen(QColor("#f80404"))
        painter.setFont(QFont("Arial", 8, QFont.Weight.Bold))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


class HeaderToolbar(QToolBar):
    """
    Compact, modern toolbar with status bar integration and trading features.
    """
    symbol_selected = Signal(str)
    add_alert_requested = Signal()
    alert_manager_requested = Signal()
    order_history_requested = Signal()
    performance_dashboard_requested = Signal()
    market_depth_requested = Signal(str)
    timeframe_changed = Signal(str)
    buy_order_requested = Signal(str)
    sell_order_requested = Signal(str)
    color_settings_requested = Signal()

    def __init__(self, trader: Union[KiteConnect, Any], parent=None):
        super().__init__(parent)
        self.setMovable(False)
        self.setObjectName("enhancedHeaderToolbar")
        self.trader = trader
        self._instrument_map: Dict[str, Dict] = {}
        self._account_info = {'available_balance': DEFAULT_PAPER_BALANCE, 'user_id': 'N/A'}

        self._init_ui()
        self._apply_styles()
        self._setup_timers()

    def _init_ui(self):
        """Initialize UI components with status bar integration."""
        self._create_symbol_search_section()
        self._create_status_bar_section()  # NEW: Status bar section
        self._create_center_spacer()
        self._create_alert_section()
        self._create_trading_actions_section()
        self._create_account_section()

    def _create_symbol_search_section(self):
        """Creates symbol search section with buy/sell buttons."""
        symbol_label = QLabel("SYMBOL:")
        symbol_label.setObjectName("symbolLabel")
        self.addWidget(symbol_label)

        # Use custom symbol input instead of regular QLineEdit
        self.search_input = SymbolSearchInput()  # Changed this line
        self.search_input.setPlaceholderText("Search symbols")
        self.search_input.setObjectName("enhancedSymbolSearch")
        self.search_input.returnPressed.connect(self._on_search_enter)

        self.addWidget(self.search_input)

        # Buy and Sell buttons
        self.buy_button = QPushButton("BUY")
        self.buy_button.setObjectName("buyButton")
        self.buy_button.setFixedSize(42, 20)
        self.buy_button.clicked.connect(self._on_buy_clicked)
        self.addWidget(self.buy_button)

        self.sell_button = QPushButton("SELL")
        self.sell_button.setObjectName("sellButton")
        self.sell_button.setFixedSize(42, 20)
        self.sell_button.clicked.connect(self._on_sell_clicked)
        self.addWidget(self.sell_button)

        # Setup completer with enhanced settings
        self.completer = QCompleter(self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setMaxVisibleItems(10)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)

        # Configure completer popup
        popup = self.completer.popup()
        popup.setStyleSheet("""
            QListView {
                background-color: #2b2b2b;
                color: #ffffff;
                border: 1px solid #555555;
                selection-background-color: #0078d4;
                font-size: 11px;
            }
            QListView::item {
                padding: 4px;
                border-bottom: 1px solid #3a3a3a;
            }
            QListView::item:hover {
                background-color: #404040;
            }
            QListView::item:selected {
                background-color: #0078d4;
            }
        """)

        self.search_input.setCompleter(self.completer)
        self.completer.activated.connect(self._on_search_enter)


    def _create_status_bar_section(self):
        """NEW: Creates the LED-style status bar section."""
        # Add small separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setObjectName("sectionSeparator")
        self.addWidget(separator)

        # Create status bar widget
        self.status_bar = StatusBar(self)
        self.addWidget(self.status_bar)

        # Initialize global status manager
        status.initialize(self.status_bar)
        logger.info("Status bar integrated into header toolbar")

    def _create_center_spacer(self):
        spacer = QWidget()
        spacer.setObjectName("centerSpacer")
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer)

    def _create_alert_section(self):
        """Creates alert management section."""
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setObjectName("sectionSeparator")
        self.addWidget(separator)

        alert_widget = QWidget()
        alert_widget.setObjectName("alertActionWidget")

        alert_layout = QHBoxLayout(alert_widget)
        alert_layout.setContentsMargins(4, 2, 4, 2)
        alert_layout.setSpacing(2)

        # Unified Alerts button with a single badge
        alerts_container = QWidget()
        alerts_container.setFixedSize(64, 20)
        self.alerts_button = QPushButton("Alerts", alerts_container)
        self.alerts_button.setObjectName("alertActionButton")
        self.alerts_button.clicked.connect(self.alert_manager_requested.emit)
        self.alerts_button.setGeometry(0, 0, 64, 20)
        self.alerts_badge = NotificationBadge(alerts_container)
        self.alerts_badge.move(50, -3)
        alert_layout.addWidget(alerts_container)

        self.addWidget(alert_widget)

    def _create_trading_actions_section(self):
        """Creates trading actions section."""
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setObjectName("sectionSeparator")
        self.addWidget(separator)

        actions_widget = QWidget()
        actions_widget.setObjectName("tradingActionWidget")

        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(4, 2, 4, 2)
        actions_layout.setSpacing(2)

        # Order History Button
        self.order_history_btn = QPushButton("Order History")
        self.order_history_btn.setObjectName("tradingActionButton")
        self.order_history_btn.clicked.connect(self.order_history_requested.emit)
        self.order_history_btn.setFixedSize(84, 20)
        actions_layout.addWidget(self.order_history_btn)

        # Performance Dashboard Button
        self.performance_btn = QPushButton("Performance")
        self.performance_btn.setObjectName("tradingActionButton")
        self.performance_btn.clicked.connect(self.performance_dashboard_requested.emit)
        self.performance_btn.setFixedSize(76, 20)
        actions_layout.addWidget(self.performance_btn)

        self.color_settings_btn = QPushButton("Settings")
        self.color_settings_btn.setObjectName("tradingActionButton")
        self.color_settings_btn.clicked.connect(self.color_settings_requested.emit)
        self.color_settings_btn.setFixedSize(62, 20)
        actions_layout.addWidget(self.color_settings_btn)

        self.addWidget(actions_widget)

    def _create_account_section(self):
        """Creates simplified account information display."""
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setObjectName("sectionSeparator")
        self.addWidget(separator)

        self.account_info_widget = QWidget()
        self.account_info_widget.setObjectName("accountInfoWidget")
        account_layout = QHBoxLayout(self.account_info_widget)
        account_layout.setContentsMargins(6, 1, 6, 1)
        account_layout.setSpacing(6)

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
        """Setup simplified timers."""
        # Simplified account refresh
        QTimer.singleShot(1000, self._refresh_account_info)
        self.account_timer = QTimer(self)
        self.account_timer.timeout.connect(self._refresh_account_info)
        self.account_timer.start(30000)

    def _refresh_account_info(self):
        """SIMPLIFIED account information refresh - no error handling complexity."""
        try:
            profile = self._get_profile_data()
            margins = self._get_margins_data()

            self._account_info = {
                'user_id': profile.get('user_id', profile.get('user_name', 'DEMO')),
                'available_balance': self._extract_available_balance(profile, margins)
            }

            self._update_account_display()

        except Exception as e:
            logger.debug(f"Using demo account info: {e}")
            self._account_info = {'user_id': 'DEMO', 'available_balance': DEFAULT_PAPER_BALANCE}
            self._update_account_display()

    def _get_profile_data(self) -> Dict[str, Any]:
        profile_fn = getattr(self.trader, 'profile', None)
        if callable(profile_fn):
            return profile_fn() or {}

        get_profile_fn = getattr(self.trader, 'get_profile', None)
        if callable(get_profile_fn):
            return get_profile_fn() or {}

        return {}

    def _get_margins_data(self) -> Dict[str, Any]:
        margins_fn = getattr(self.trader, 'margins', None)
        if callable(margins_fn):
            return margins_fn() or {}
        return {}

    def _extract_available_balance(self, profile: Dict[str, Any], margins: Dict[str, Any]) -> float:
        equity_margins = margins.get('equity', {})
        available = equity_margins.get('available', {})

        candidate_values = [
            available.get('live_balance'),
            available.get('cash'),
            equity_margins.get('net'),
            profile.get('current_balance'),
            profile.get('balance'),
            getattr(self.trader, 'balance', None),
            getattr(self.trader, 'current_balance', None),
            getattr(self.trader, 'initial_balance', DEFAULT_PAPER_BALANCE),
        ]

        for value in candidate_values:
            try:
                if value is not None:
                    return float(value)
            except (TypeError, ValueError):
                continue
        return DEFAULT_PAPER_BALANCE

    def update_balance(self, balance: float):
        """Direct balance update callback used by paper-trading signal wiring."""
        self._account_info['available_balance'] = float(balance)
        if self._account_info.get('user_id') in (None, '', 'N/A'):
            self._account_info['user_id'] = 'DEMO'
        self._update_account_display()

    def _update_account_display(self):
        """Updates the UI labels for account information."""
        self.user_id_label.setText(self._account_info.get('user_id', 'DEMO'))
        balance = self._account_info.get('available_balance', 0.0)
        self.balance_label.setText(self._format_indian_currency(balance))

    def _format_indian_currency(self, amount: float) -> str:
        """Format currency in Indian numbering system."""
        if amount == 0:
            return "₹0"

        is_negative = amount < 0
        amount = abs(amount)
        amount_str = f"{amount:.0f}"

        if len(amount_str) <= 3:
            formatted = amount_str
        else:
            last_three = amount_str[-3:]
            remaining = amount_str[:-3]

            formatted_remaining = ""
            for i, digit in enumerate(reversed(remaining)):
                if i > 0 and i % 2 == 0:
                    formatted_remaining = "," + formatted_remaining
                formatted_remaining = digit + formatted_remaining

            formatted = formatted_remaining + "," + last_three

        prefix = "-₹" if is_negative else "₹"
        return prefix + formatted

    def set_instrument_data(self, instruments: List[Dict[str, Any]]):
        """Set instrument data for symbol search."""
        symbols = [inst['tradingsymbol'] for inst in instruments if 'tradingsymbol' in inst]
        self._instrument_map = {inst['tradingsymbol']: inst for inst in instruments if 'tradingsymbol' in inst}
        model = QStringListModel(symbols)
        self.completer.setModel(model)

    def update_alert_counts(self, active_count: int, triggered_today: int):
        """Update alert badge counts."""
        self.alerts_badge.set_count(active_count + triggered_today)

    def set_current_symbol(self, symbol: str):
        """Set the current symbol in the search input."""
        self.search_input.setText(symbol)

    def get_current_symbol(self) -> str:
        """Get the current symbol from the search input."""
        return self.search_input.text().upper().strip()

    def _on_search_enter(self, text=""):
        """Handle symbol search."""
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
            logger.warning("No valid symbol entered for buy order")

    def _on_sell_clicked(self):
        """Handle sell button click."""
        symbol = self.search_input.text().upper().strip()
        if symbol and symbol in self._instrument_map:
            self.sell_order_requested.emit(symbol)
        else:
            logger.warning("No valid symbol entered for sell order")

    def update_performance_metrics(self, performance_data: Dict[str, Any]):
        """SIMPLIFIED performance metrics update - no tooltips."""
        try:
            daily_pnl = performance_data.get('daily_pnl', 0)

            # Simple color indicator on performance button
            if daily_pnl > 0:
                self.performance_btn.setStyleSheet(
                    self.performance_btn.styleSheet() +
                    "border-left: 3px solid #00b894;"
                )
            elif daily_pnl < 0:
                self.performance_btn.setStyleSheet(
                    self.performance_btn.styleSheet() +
                    "border-left: 3px solid #d63031;"
                )

            logger.debug(f"Header performance updated: P&L ₹{daily_pnl:,.2f}")

        except Exception as e:
            logger.error(f"Failed to update performance metrics: {e}")

    def _apply_styles(self):
        """Apply styles with status bar integration."""
        self.setStyleSheet("""
            QToolBar#enhancedHeaderToolbar {
                background-color: #1a1a1a;
                border-bottom: 2px solid #404040;
                padding: 1px 6px;
                spacing: 6px;
                min-height: 28px;
                max-height: 30px;
            }
            #centerSpacer {
                background-color: transparent;
            }
            #symbolLabel { 
                background-color: #1a1a1a; 
                color: #ffffff; 
                font-size: 11px; 
                font-weight: 900; 
                text-transform: uppercase;
                letter-spacing: 1px;
                padding-right: 6px;
            }
            #enhancedSymbolSearch {
                background-color: #000000;
                border: 1px solid #333333; 
                color: #ffffff; 
                padding: 3px 8px; 
                border-radius: 3px;
                font-size: 9px; 
                font-weight: 500;
                min-width: 84px; 
                max-width: 100px; 
                max-height: 20px;
            }
            #enhancedSymbolSearch:focus { 
                border: 1px solid #00d4ff; 
                color: #00d4ff;
            }
            #buyButton {
                background-color: #000000;
                color: white;
                border: 1px solid #333333;
                padding: 3px 6px;
                border-radius: 3px;
                font-size: 9px;
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
                padding: 3px 6px;
                border-radius: 3px;
                font-size: 9px;
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
                padding: 3px 6px;
                border-radius: 3px;
                font-size: 9px;
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
                border-radius: 5px; 
                padding: 2px 6px;
            }
            #userIdLabel { 
                background-color: #1a1a1a;
                color: #00d4ff; 
                font-size: 10px; 
                font-weight: 700; 
            }
            #balanceLabel { 
                background-color: #1a1a1a;
                color: #4aff4a; 
                font-size: 10px; 
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
                margin: 3px 1px; 
            }
        """)

    def closeEvent(self, event):
        """Clean up timers when closing."""
        if hasattr(self, 'account_timer'):
            self.account_timer.stop()
        super().closeEvent(event)




from PySide6.QtWidgets import QLineEdit
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent


# Update the SymbolSearchInput class in widgets/header_toolbar.py

class SymbolSearchInput(QLineEdit):
    """Custom QLineEdit with proper arrow key handling for completer."""

    def keyPressEvent(self, event):
        """Override to handle arrow keys properly with completer."""
        key = event.key()

        # Handle arrow keys specially
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            completer = self.completer()

            if completer:
                # If there's text and popup is not visible, show it
                if self.text().strip() and not completer.popup().isVisible():
                    completer.complete()

                # If popup is visible, let the default handling work
                if completer.popup().isVisible():
                    super().keyPressEvent(event)
                    return

            # If no completer or popup not visible, consume the event
            # This prevents the chart timeframe change
            event.accept()  # Explicitly accept the event
            return

        # For all other keys, use default handling
        super().keyPressEvent(event)

    def focusInEvent(self, event):
        """Handle focus in event."""
        super().focusInEvent(event)
        # Optionally show completer when focused if there's text
        if self.text().strip() and self.completer():
            self.completer().complete()

    def focusOutEvent(self, event):
        """Handle focus out event."""
        super().focusOutEvent(event)
        # Hide completer when focus is lost
        if self.completer() and self.completer().popup().isVisible():
            self.completer().popup().hide()
