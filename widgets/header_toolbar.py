import logging
from typing import List, Dict, Any, Optional, Union

from PySide6.QtWidgets import (
    QToolBar, QLineEdit, QCompleter, QWidget, QLabel, QSizePolicy, QPushButton
)
from PySide6.QtCore import Signal, QStringListModel, Qt, QTimer
from PySide6.QtGui import QIcon
from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)


class HeaderToolbar(QToolBar):
    """
    A compact, modern toolbar for the main application window.
    Features symbol search, alert management, and real-time account information.
    """
    symbol_selected = Signal(str)
    add_alert_requested = Signal()
    alert_logs_requested = Signal()

    def __init__(self, trader: Union[KiteConnect, Any], parent=None):
        super().__init__(parent)
        self.setMovable(False)
        self.setObjectName("compactHeaderToolbar")
        self.trader = trader
        self._instrument_map: Dict[str, Dict] = {}

        # Account info cache
        self._account_info = {
            'user_name': 'Loading...',
            'available_balance': 0.0,
            'used_margin': 0.0
        }

        self._init_ui()
        self._apply_styles()

        # Set up account refresh with a slight delay to ensure everything is initialized
        self._setup_account_refresh()

    def _init_ui(self):
        """Initializes the compact UI components of the toolbar."""
        # --- Left Section: Symbol Search ---
        symbol_label = QLabel("Symbol:")
        symbol_label.setObjectName("compactLabel")
        self.addWidget(symbol_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("e.g., RELIANCE")
        self.search_input.setObjectName("compactSymbolSearch")
        self.search_input.returnPressed.connect(self._on_search_enter)
        self.addWidget(self.search_input)

        self.completer = QCompleter(self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.search_input.setCompleter(self.completer)
        self.completer.activated.connect(self._on_search_enter)

        # --- Center Spacer ---
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer)

        # --- Right Section: Account Info ---
        self.account_info_widget = self._create_account_info_widget()
        self.addWidget(self.account_info_widget)

        # Small spacer
        small_spacer = QWidget()
        small_spacer.setFixedWidth(12)
        self.addWidget(small_spacer)

        # --- Alert Management Buttons ---
        self.alert_button = QPushButton()
        self.alert_button.setIcon(QIcon("icons/bell.svg"))
        self.alert_button.setObjectName("compactIconButton")
        self.alert_button.setToolTip("Set Price Alert")
        self.alert_button.clicked.connect(self.add_alert_requested)
        self.addWidget(self.alert_button)

        self.alert_logs_button = QPushButton()
        self.alert_logs_button.setIcon(QIcon("icons/checklist.svg"))
        self.alert_logs_button.setObjectName("compactIconButton")
        self.alert_logs_button.setToolTip("View Alert History")
        self.alert_logs_button.clicked.connect(self.alert_logs_requested)
        self.addWidget(self.alert_logs_button)

    def _create_account_info_widget(self) -> QWidget:
        """Creates a compact account information display widget."""
        container = QWidget()
        container.setObjectName("accountInfoContainer")

        # We'll use a simple horizontal layout with labels
        from PySide6.QtWidgets import QHBoxLayout
        layout = QHBoxLayout(container)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(6)

        # User name
        self.user_label = QLabel("Loading...")
        self.user_label.setObjectName("userNameLabel")
        layout.addWidget(self.user_label)

        # Separator
        separator1 = QLabel("•")
        separator1.setObjectName("separatorLabel")
        layout.addWidget(separator1)

        # Available balance
        self.balance_label = QLabel("₹0")
        self.balance_label.setObjectName("balanceLabel")
        layout.addWidget(self.balance_label)

        # Separator
        separator2 = QLabel("•")
        separator2.setObjectName("separatorLabel")
        layout.addWidget(separator2)

        # Used margin
        self.margin_label = QLabel("Used: ₹0")
        self.margin_label.setObjectName("marginLabel")
        layout.addWidget(self.margin_label)

        return container

    def _setup_account_refresh(self):
        """Sets up periodic refresh of account information."""
        # Initial load
        QTimer.singleShot(1000, self._refresh_account_info)

        # Periodic refresh every 30 seconds
        self.account_timer = QTimer(self)
        self.account_timer.timeout.connect(self._refresh_account_info)
        self.account_timer.start(30000)  # 30 seconds

    def _refresh_account_info(self):
        """Fetches and updates account information from the Kite API."""
        try:
            # Handle different trader types
            actual_trader = self._get_actual_trader()
            if not actual_trader:
                self._show_demo_mode()
                return

            # Get user profile
            profile = actual_trader.profile()
            user_name = profile.get('user_name', profile.get('user_id', 'Unknown'))

            # Get margin information
            margins = actual_trader.margins()
            equity_margins = margins.get('equity', {})

            available_balance = equity_margins.get('available', {}).get('live_balance', 0.0)
            used_margin = equity_margins.get('utilised', {}).get('total', 0.0)

            # Update cached info
            self._account_info = {
                'user_name': user_name,
                'available_balance': available_balance,
                'used_margin': used_margin
            }

            # Update UI
            self._update_account_display()

        except Exception as e:
            logger.error(f"Failed to refresh account info: {e}")
            # Check if this is paper trading mode
            if self._is_paper_trading():
                self._show_paper_trading_mode()
            else:
                # Show error state briefly, then retry
                self.user_label.setText("Error")
                self.balance_label.setText("₹--")
                self.margin_label.setText("Used: ₹--")

    def _get_actual_trader(self):
        """Gets the actual trading client from various possible sources."""
        # If trader has a 'trader' attribute (like SwingTraderWindow)
        if hasattr(self.trader, 'trader'):
            return self.trader.trader

        # If trader has a 'real_kite_client' attribute
        if hasattr(self.trader, 'real_kite_client'):
            return self.trader.real_kite_client

        # If trader itself has the required methods
        if hasattr(self.trader, 'profile') and hasattr(self.trader, 'margins'):
            return self.trader

        return None

    def _is_paper_trading(self):
        """Checks if we're in paper trading mode."""
        if hasattr(self.trader, 'trading_mode'):
            return self.trader.trading_mode == 'paper'

        # Check if trader is a PaperTradingManager
        trader_class_name = self.trader.__class__.__name__
        return 'Paper' in trader_class_name or 'paper' in trader_class_name.lower()

    def _show_paper_trading_mode(self):
        """Shows paper trading account information."""
        self.user_label.setText("Paper Trader")

        # Get paper trading balance if available
        if hasattr(self.trader, 'balance'):
            balance = self.trader.balance
            if balance >= 1e5:
                balance_text = f"₹{balance / 1e5:.1f}L"
            elif balance >= 1e3:
                balance_text = f"₹{balance / 1e3:.1f}K"
            else:
                balance_text = f"₹{balance:.0f}"
            self.balance_label.setText(balance_text)
        else:
            self.balance_label.setText("₹100K")  # Default paper trading balance

        self.margin_label.setText("Used: ₹0")

    def _show_demo_mode(self):
        """Shows demo mode when no trading client is available."""
        self.user_label.setText("Demo Mode")
        self.balance_label.setText("₹100K")
        self.margin_label.setText("Used: ₹0")

    def _update_account_display(self):
        """Updates the account information display with current data."""
        info = self._account_info

        # Update user name (truncate if too long)
        user_name = info['user_name']
        if len(user_name) > 12:
            user_name = user_name[:12] + "..."
        self.user_label.setText(user_name)

        # Update available balance
        balance = info['available_balance']
        if balance >= 1e5:  # 1 lakh or more
            balance_text = f"₹{balance / 1e5:.1f}L"
        elif balance >= 1e3:  # 1 thousand or more
            balance_text = f"₹{balance / 1e3:.1f}K"
        else:
            balance_text = f"₹{balance:.0f}"
        self.balance_label.setText(balance_text)

        # Update used margin
        used = info['used_margin']
        if used >= 1e5:  # 1 lakh or more
            used_text = f"Used: ₹{used / 1e5:.1f}L"
        elif used >= 1e3:  # 1 thousand or more
            used_text = f"Used: ₹{used / 1e3:.1f}K"
        else:
            used_text = f"Used: ₹{used:.0f}"
        self.margin_label.setText(used_text)

    def set_instrument_data(self, instruments: List[Dict[str, Any]]):
        """Receives the master list of instruments to populate the search completer."""
        symbols = [inst['tradingsymbol'] for inst in instruments if 'tradingsymbol' in inst]
        self._instrument_map = {inst['tradingsymbol']: inst for inst in instruments if 'tradingsymbol' in inst}

        model = QStringListModel(symbols)
        self.completer.setModel(model)
        logger.info("Compact header toolbar search completer has been populated.")

    def set_alert_active(self, active: bool):
        """Changes the alert bell icon to indicate one or more triggered alerts."""
        icon_path = "icons/bell_color.svg" if active else "icons/bell.svg"
        self.alert_button.setIcon(QIcon(icon_path))

    def _on_search_enter(self, text=""):
        """Handles symbol selection from the search bar to display its chart."""
        symbol = (text or self.search_input.text()).upper().strip()
        if not symbol:
            return

        if symbol in self._instrument_map:
            self.symbol_selected.emit(symbol)
            logger.info(f"Symbol '{symbol}' selected for charting.")
            self.search_input.clear()
        else:
            logger.warning(f"Invalid symbol entered for charting: {symbol}")

    def _apply_styles(self):
        """Applies a compact, professional dark theme stylesheet."""
        self.setStyleSheet("""
            QToolBar#compactHeaderToolbar {
                background-color: #0a0a0a; /* Deep black background */
                border-bottom: 1px solid #202020; /* Subtle dark border */
                padding: 2px 8px; /* Reduced padding for compactness */
                spacing: 6px; /* Tighter spacing */
                max-height: 32px; /* Compact height */
                min-height: 32px; /* Fixed height */
            }

            /* Compact labels */
            #compactLabel {
                color: #a0c0ff; /* Light blue */
                font-size: 11px; /* Smaller font */
                font-weight: 500;
                padding: 0px 2px;
            }

            /* Compact symbol search */
            #compactSymbolSearch {
                background-color: #1a1a1a; /* Dark input background */
                border: 1px solid #303030; /* Subtle border */
                color: #e0e0e0; /* Light text */
                padding: 4px 8px; /* Compact padding */
                border-radius: 3px; /* Minimal rounding */
                font-size: 11px; /* Smaller font */
                min-width: 120px; /* Compact width */
                max-width: 160px; /* Max width limit */
                max-height: 24px; /* Compact height */
            }

            #compactSymbolSearch:focus {
                border: 1px solid #6a9cff; /* Professional blue focus */
                background-color: #202020; /* Slightly lighter when focused */
            }

            /* Account info container */
            #accountInfoContainer {
                background-color: #151515; /* Slightly different from main bg */
                border: 1px solid #252525; /* Subtle border */
                border-radius: 3px;
                max-height: 24px; /* Compact height */
            }

            /* Account info labels */
            #userNameLabel {
                color: #ffffff; /* White for username */
                font-size: 11px;
                font-weight: 600;
            }

            #balanceLabel {
                color: #4aff4a; /* Green for available balance */
                font-size: 11px;
                font-weight: 600;
            }

            #marginLabel {
                color: #ffa500; /* Orange for used margin */
                font-size: 10px;
                font-weight: 500;
            }

            #separatorLabel {
                color: #606060; /* Gray separator dots */
                font-size: 10px;
            }

            /* Compact icon buttons */
            #compactIconButton {
                background-color: transparent;
                border: none;
                padding: 4px; /* Minimal padding */
                margin: 0px 1px; /* Tight margins */
                border-radius: 3px;
                max-width: 24px; /* Compact size */
                max-height: 24px;
                min-width: 24px;
                min-height: 24px;
            }

            #compactIconButton:hover {
                background-color: #2a2a2a; /* Subtle hover */
            }

            #compactIconButton:pressed {
                background-color: #3a3a3a; /* Pressed state */
            }

            /* Ensure QCompleter dropdown follows the same theme */
            QAbstractItemView {
                background-color: #1a1a1a;
                color: #e0e0e0;
                border: 1px solid #303030;
                selection-background-color: #6a9cff;
                font-size: 11px;
            }
        """)

    def refresh_account_info_now(self):
        """Public method to manually trigger account info refresh."""
        self._refresh_account_info()

    def closeEvent(self, event):
        """Clean up timers when closing."""
        if hasattr(self, 'account_timer'):
            self.account_timer.stop()
        super().closeEvent(event)