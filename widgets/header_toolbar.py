import logging
from typing import List, Dict, Any, Optional, Union
from datetime import datetime

from PySide6.QtWidgets import (
    QToolBar, QLineEdit, QCompleter, QWidget, QLabel, QSizePolicy, QPushButton,
    QHBoxLayout, QVBoxLayout, QFrame, QProgressBar, QComboBox
)
from PySide6.QtCore import Signal, QStringListModel, Qt, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor
from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)


class HeaderToolbar(QToolBar):
    """
    Enhanced compact, modern toolbar for the swing trading terminal.
    Features symbol search, alert management, real-time account information,
    market status, P&L tracking, and quick actions.
    """
    symbol_selected = Signal(str)
    add_alert_requested = Signal()
    alert_logs_requested = Signal()
    watchlist_requested = Signal()
    portfolio_requested = Signal()
    orders_requested = Signal()
    market_depth_requested = Signal(str)  # For quick market depth access
    timeframe_changed = Signal(str)  # For quick timeframe switching

    def __init__(self, trader: Union[KiteConnect, Any], parent=None):
        super().__init__(parent)
        self.setMovable(False)
        self.setObjectName("enhancedHeaderToolbar")
        self.trader = trader
        self._instrument_map: Dict[str, Dict] = {}

        # Account info cache with enhanced data
        self._account_info = {
            'user_id': 'Loading...',
            'user_name': 'Loading...',
            'available_balance': 0.0,
            'used_margin': 0.0
        }

        # Market status
        self._market_status = {
            'nse': 'unknown',
            'bse': 'unknown',
            'last_update': None
        }

        # Alert counters
        self._alert_counts = {
            'active': 0,
            'triggered_today': 0
        }

        self._init_ui()
        self._apply_styles()
        self._setup_timers()

    def _init_ui(self):
        """Initializes the enhanced UI components of the toolbar."""
        # --- Left Section: Symbol Search ---
        self._create_symbol_search_section()

        # --- Center Section: Quick Actions & Market Status ---
        self._create_center_spacer()
        self._create_market_status_section()
        self._create_quick_actions_section()

        # --- Right Section: Account Info & Alerts ---
        self._create_account_section()
        self._create_alert_section()

    def _create_brand_section(self):
        """Creates a small brand/logo section."""
        brand_widget = QWidget()
        brand_widget.setObjectName("brandSection")
        brand_layout = QHBoxLayout(brand_widget)
        brand_layout.setContentsMargins(8, 2, 8, 2)
        brand_layout.setSpacing(4)

        # App icon/logo (you can replace with your logo)
        logo_label = QLabel("📈")
        logo_label.setObjectName("logoLabel")
        brand_layout.addWidget(logo_label)

        # App name
        app_name = QLabel("SwingTrader")
        app_name.setObjectName("appNameLabel")
        brand_layout.addWidget(app_name)

        self.addWidget(brand_widget)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setObjectName("sectionSeparator")
        self.addWidget(separator)

    def _create_symbol_search_section(self):
        """Creates enhanced symbol search section."""
        # Symbol search label
        symbol_label = QLabel("Symbol:")
        symbol_label.setObjectName("sectionLabel")
        self.addWidget(symbol_label)

        # Symbol search input with enhanced features
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search symbols (e.g., RELIANCE, NIFTY)")
        self.search_input.setObjectName("enhancedSymbolSearch")
        self.search_input.returnPressed.connect(self._on_search_enter)
        self.addWidget(self.search_input)

        # Completer setup
        self.completer = QCompleter(self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.search_input.setCompleter(self.completer)
        self.completer.activated.connect(self._on_search_enter)

    def _create_center_spacer(self):
        """Creates expanding spacer."""
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer)

    def _create_market_status_section(self):
        """Creates market status indicator."""
        market_widget = QWidget()
        market_widget.setObjectName("marketStatusWidget")
        market_layout = QHBoxLayout(market_widget)
        market_layout.setContentsMargins(6, 2, 6, 2)
        market_layout.setSpacing(4)

        # Market status indicator
        self.market_status_label = QLabel("●")
        self.market_status_label.setObjectName("marketStatusIndicator")
        market_layout.addWidget(self.market_status_label)

        self.market_text_label = QLabel("Market")
        self.market_text_label.setObjectName("marketStatusText")
        market_layout.addWidget(self.market_text_label)

        self.addWidget(market_widget)

        # Update market status
        self._update_market_status()

    def _create_quick_actions_section(self):
        """Creates quick action buttons."""
        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setObjectName("sectionSeparator")
        self.addWidget(separator)

        # Quick actions container
        actions_widget = QWidget()
        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(4, 2, 4, 2)
        actions_layout.setSpacing(2)

        # Watchlist button
        self.watchlist_btn = self._create_action_button("📋", "Watchlist", self.watchlist_requested.emit)
        actions_layout.addWidget(self.watchlist_btn)

        # Portfolio button
        self.portfolio_btn = self._create_action_button("💼", "Portfolio", self.portfolio_requested.emit)
        actions_layout.addWidget(self.portfolio_btn)

        # Orders button
        self.orders_btn = self._create_action_button("📝", "Orders", self.orders_requested.emit)
        actions_layout.addWidget(self.orders_btn)

        self.addWidget(actions_widget)

    def _create_action_button(self, icon_text: str, tooltip: str, callback) -> QPushButton:
        """Creates a standardized action button."""
        btn = QPushButton(icon_text)
        btn.setObjectName("quickActionButton")
        btn.setToolTip(tooltip)
        btn.clicked.connect(callback)
        btn.setFixedSize(28, 24)
        return btn

    def _create_account_section(self):
        """Creates horizontal account information display."""
        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setObjectName("sectionSeparator")
        self.addWidget(separator)

        # Account info container - single horizontal row
        account_widget = QWidget()
        account_widget.setObjectName("accountInfoWidget")
        account_layout = QHBoxLayout(account_widget)
        account_layout.setContentsMargins(8, 2, 8, 2)
        account_layout.setSpacing(8)

        # User ID (shows username like KE6286)
        self.user_id_label = QLabel("KE6286")
        self.user_id_label.setObjectName("userIdLabel")
        account_layout.addWidget(self.user_id_label)

        # Separator dot
        account_layout.addWidget(self._create_separator_dot())

        # Available balance
        self.balance_label = QLabel("₹0")
        self.balance_label.setObjectName("balanceLabel")
        account_layout.addWidget(self.balance_label)

        # Separator dot
        account_layout.addWidget(self._create_separator_dot())

        # Used margin
        self.margin_label = QLabel("Used: ₹0")
        self.margin_label.setObjectName("marginLabel")
        account_layout.addWidget(self.margin_label)

        self.addWidget(account_widget)

    def _create_separator_dot(self) -> QLabel:
        """Creates a separator dot."""
        dot = QLabel("•")
        dot.setObjectName("separatorDot")
        return dot

    def _create_alert_section(self):
        """Creates enhanced alert management section."""
        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setObjectName("sectionSeparator")
        self.addWidget(separator)

        # Alert section container
        alert_widget = QWidget()
        alert_layout = QHBoxLayout(alert_widget)
        alert_layout.setContentsMargins(6, 2, 6, 2)
        alert_layout.setSpacing(4)

        # Alert button with badge
        alert_container = QWidget()
        alert_container_layout = QVBoxLayout(alert_container)
        alert_container_layout.setContentsMargins(0, 0, 0, 0)
        alert_container_layout.setSpacing(0)

        self.alert_button = QPushButton("🔔")
        self.alert_button.setObjectName("alertButton")
        self.alert_button.setToolTip("Set Price Alert")
        self.alert_button.clicked.connect(self.add_alert_requested)
        self.alert_button.setFixedSize(32, 20)

        # Alert badge
        self.alert_badge = QLabel("0")
        self.alert_badge.setObjectName("alertBadge")
        self.alert_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.alert_badge.hide()

        alert_container_layout.addWidget(self.alert_button)
        alert_container_layout.addWidget(self.alert_badge)
        alert_layout.addWidget(alert_container)

        # Alert label
        alert_label = QLabel("Alert")
        alert_label.setObjectName("actionLabel")
        alert_layout.addWidget(alert_label)

        # Small spacer
        spacer_widget = QWidget()
        spacer_widget.setFixedWidth(8)
        alert_layout.addWidget(spacer_widget)

        # Alert logs button
        logs_container = QWidget()
        logs_container_layout = QVBoxLayout(logs_container)
        logs_container_layout.setContentsMargins(0, 0, 0, 0)
        logs_container_layout.setSpacing(0)

        self.alert_logs_button = QPushButton("📋")
        self.alert_logs_button.setObjectName("alertLogsButton")
        self.alert_logs_button.setToolTip("View Alert History")
        self.alert_logs_button.clicked.connect(self.alert_logs_requested)
        self.alert_logs_button.setFixedSize(32, 20)

        # Logs badge
        self.logs_badge = QLabel("0")
        self.logs_badge.setObjectName("logsBadge")
        self.logs_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logs_badge.hide()

        logs_container_layout.addWidget(self.alert_logs_button)
        logs_container_layout.addWidget(self.logs_badge)
        alert_layout.addWidget(logs_container)

        # Alert logs label
        logs_label = QLabel("Logs")
        logs_label.setObjectName("actionLabel")
        alert_layout.addWidget(logs_label)

        self.addWidget(alert_widget)

    def _setup_timers(self):
        """Sets up periodic updates."""
        # Account refresh timer
        QTimer.singleShot(1000, self._refresh_account_info)
        self.account_timer = QTimer(self)
        self.account_timer.timeout.connect(self._refresh_account_info)
        self.account_timer.start(30000)  # 30 seconds

        # Market status timer
        self.market_timer = QTimer(self)
        self.market_timer.timeout.connect(self._update_market_status)
        self.market_timer.start(60000)  # 1 minute

    def _update_market_status(self):
        """Updates market status indicator."""
        current_time = datetime.now().time()

        # Market timings (simplified)
        market_open = current_time >= datetime.strptime("09:15", "%H:%M").time()
        market_close = current_time <= datetime.strptime("15:30", "%H:%M").time()
        is_market_open = market_open and market_close

        if is_market_open:
            self.market_status_label.setText("●")
            self.market_status_label.setObjectName("marketOpen")
            self.market_text_label.setText("OPEN")
        else:
            self.market_status_label.setText("●")
            self.market_status_label.setObjectName("marketClosed")
            self.market_text_label.setText("CLOSED")

        # Refresh styles
        self.market_status_label.style().polish(self.market_status_label)

    def _refresh_account_info(self):
        """Enhanced account information refresh."""
        try:
            actual_trader = self._get_actual_trader()
            if not actual_trader:
                self._show_demo_mode()
                return

            # Get user profile
            profile = actual_trader.profile()
            user_id = profile.get('user_id', 'Unknown')
            user_name = profile.get('user_name', profile.get('user_id', 'Unknown'))

            # Get margin information
            margins = actual_trader.margins()
            equity_margins = margins.get('equity', {})

            available_balance = equity_margins.get('available', {}).get('live_balance', 0.0)
            used_margin = equity_margins.get('utilised', {}).get('total', 0.0)

            # Update cached info
            self._account_info = {
                'user_id': user_id,
                'user_name': user_name,
                'available_balance': available_balance,
                'used_margin': used_margin
            }

            self._update_account_display()

        except Exception as e:
            logger.error(f"Failed to refresh account info: {e}")
            if self._is_paper_trading():
                self._show_paper_trading_mode()
            else:
                self._show_error_state()

    def _get_actual_trader(self):
        """Gets the actual trading client from various possible sources."""
        if hasattr(self.trader, 'trader'):
            return self.trader.trader
        if hasattr(self.trader, 'real_kite_client'):
            return self.trader.real_kite_client
        if hasattr(self.trader, 'profile') and hasattr(self.trader, 'margins'):
            return self.trader
        return None

    def _is_paper_trading(self):
        """Checks if we're in paper trading mode."""
        if hasattr(self.trader, 'trading_mode'):
            return self.trader.trading_mode == 'paper'
        trader_class_name = self.trader.__class__.__name__
        return 'Paper' in trader_class_name or 'paper' in trader_class_name.lower()

    def _show_paper_trading_mode(self):
        """Shows paper trading account information."""
        self.user_id_label.setText("PAPER")
        balance = getattr(self.trader, 'balance', 100000)
        self._format_and_set_balance(balance)
        self.margin_label.setText("Used: ₹0")
        self.pnl_label.setText("P&L: ₹0")

    def _show_demo_mode(self):
        """Shows demo mode when no trading client is available."""
        self.user_id_label.setText("DEMO")
        self.balance_label.setText("₹1L")
        self.margin_label.setText("Used: ₹0")
        self.pnl_label.setText("P&L: ₹0")

    def _show_error_state(self):
        """Shows error state briefly."""
        self.user_id_label.setText("ERROR")
        self.balance_label.setText("₹--")
        self.margin_label.setText("Used: ₹--")
        self.pnl_label.setText("P&L: ₹--")

    def _update_account_display(self):
        """Updates the account information display with current data."""
        info = self._account_info

        # Update user ID (show first 8 chars if too long)
        user_id = info['user_id']
        if len(user_id) > 8:
            user_id = user_id[:8] + "..."
        self.user_id_label.setText(user_id)

        # Update available balance
        self._format_and_set_balance(info['available_balance'])

        # Update used margin
        self._format_and_set_margin(info['used_margin'])

    def _format_and_set_balance(self, balance: float):
        """Formats and sets balance with appropriate units."""
        if balance >= 1e7:  # 1 crore or more
            self.balance_label.setText(f"₹{balance / 1e7:.1f}Cr")
        elif balance >= 1e5:  # 1 lakh or more
            self.balance_label.setText(f"₹{balance / 1e5:.1f}L")
        elif balance >= 1e3:  # 1 thousand or more
            self.balance_label.setText(f"₹{balance / 1e3:.1f}K")
        else:
            self.balance_label.setText(f"₹{balance:.0f}")

    def _format_and_set_margin(self, margin: float):
        """Formats and sets margin with appropriate units."""
        if margin >= 1e5:  # 1 lakh or more
            self.margin_label.setText(f"Used: ₹{margin / 1e5:.1f}L")
        elif margin >= 1e3:  # 1 thousand or more
            self.margin_label.setText(f"Used: ₹{margin / 1e3:.1f}K")
        else:
            self.margin_label.setText(f"Used: ₹{margin:.0f}")

    # Public API methods
    def set_instrument_data(self, instruments: List[Dict[str, Any]]):
        """Receives the master list of instruments to populate the search completer."""
        symbols = [inst['tradingsymbol'] for inst in instruments if 'tradingsymbol' in inst]
        self._instrument_map = {inst['tradingsymbol']: inst for inst in instruments if 'tradingsymbol' in inst}

        model = QStringListModel(symbols)
        self.completer.setModel(model)
        logger.info("Enhanced header toolbar search completer has been populated.")

    def set_alert_active(self, active: bool):
        """Changes the alert bell icon to indicate triggered alerts."""
        if active:
            self.alert_button.setText("🔔")
            self.alert_button.setObjectName("alertButtonActive")
        else:
            self.alert_button.setText("🔔")
            self.alert_button.setObjectName("alertButton")

        # Refresh styles
        self.alert_button.style().polish(self.alert_button)

    def update_alert_counts(self, active_count: int, triggered_today: int):
        """Updates alert count badges."""
        # Update active alerts badge
        if active_count > 0:
            self.alert_badge.setText(str(active_count))
            self.alert_badge.show()
        else:
            self.alert_badge.hide()

        # Update triggered alerts badge
        if triggered_today > 0:
            self.logs_badge.setText(str(triggered_today))
            self.logs_badge.show()
        else:
            self.logs_badge.hide()

    def set_current_symbol(self, symbol: str):
        """Updates the search input to show current symbol."""
        self.search_input.setText(symbol)

    def _on_search_enter(self, text=""):
        """Handles symbol selection from the search bar."""
        symbol = (text or self.search_input.text()).upper().strip()
        if not symbol:
            return

        if symbol in self._instrument_map:
            self.symbol_selected.emit(symbol)
            logger.info(f"Symbol '{symbol}' selected for charting.")
            # Don't clear the input, keep it for reference
        else:
            logger.warning(f"Invalid symbol entered for charting: {symbol}")

    def _apply_styles(self):
        """Applies enhanced professional dark theme stylesheet."""
        self.setStyleSheet("""
            QToolBar#enhancedHeaderToolbar {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #0e0e0e, stop:1 #1a1a1a);
                border-bottom: 2px solid #2a2a2a;
                padding: 2px 8px;
                spacing: 8px;
                max-height: 40px;
                min-height: 40px;
            }

            /* Brand Section */
            #brandSection {
                background-color: rgba(26, 26, 26, 0.8);
                border-radius: 4px;
                border: 1px solid #333;
            }

            #logoLabel {
                font-size: 14px;
                color: #00d4ff;
            }

            #appNameLabel {
                color: #ffffff;
                font-size: 11px;
                font-weight: 600;
                letter-spacing: 0.5px;
            }

            /* Section separators */
            #sectionSeparator {
                color: #404040;
                background-color: #404040;
                max-width: 1px;
                margin: 4px 2px;
            }

            /* Labels */
            #sectionLabel {
                color: #a0c0ff;
                font-size: 11px;
                font-weight: 500;
                padding: 0px 4px;
            }

            #actionLabel {
                color: #cccccc;
                font-size: 9px;
                font-weight: 500;
                padding: 0px 2px;
            }

            /* Enhanced symbol search - subtle focus */
            #enhancedSymbolSearch {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1e1e1e, stop:1 #2a2a2a);
                border: 1px solid #404040;
                color: #ffffff;
                padding: 6px 12px;
                border-radius: 6px;
                font-size: 11px;
                font-weight: 500;
                min-width: 180px;
                max-width: 220px;
                max-height: 28px;
            }

            #enhancedSymbolSearch:focus {
                border: 1px solid #6a9cff;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #252525, stop:1 #1e1e1e);
            }

            /* Timeframe combo */
            #timeframeCombo {
                background-color: #1e1e1e;
                border: 1px solid #404040;
                color: #ffffff;
                padding: 4px 8px;
                border-radius: 4px;
                font-size: 10px;
                font-weight: 600;
                min-width: 40px;
                max-height: 28px;
            }

            #timeframeCombo:hover {
                border: 1px solid #00d4ff;
            }

            #timeframeCombo QAbstractItemView {
                background-color: #1e1e1e;
                color: #ffffff;
                border: 1px solid #404040;
                selection-background-color: #00d4ff;
            }

            /* Market status */
            #marketStatusWidget {
                background-color: rgba(30, 30, 30, 0.8);
                border-radius: 4px;
                border: 1px solid #333;
            }

            #marketOpen {
                color: #00ff00;
                font-size: 12px;
            }

            #marketClosed {
                color: #ff4444;
                font-size: 12px;
            }

            #marketStatusText {
                color: #cccccc;
                font-size: 10px;
                font-weight: 600;
            }

            /* Quick action buttons */
            #quickActionButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #2a2a2a, stop:1 #1e1e1e);
                border: 1px solid #404040;
                color: #ffffff;
                border-radius: 4px;
                font-size: 12px;
                font-weight: 500;
                padding: 2px;
            }

            #quickActionButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #404040, stop:1 #2a2a2a);
                border: 1px solid #00d4ff;
            }

            #quickActionButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1e1e1e, stop:1 #404040);
            }

            /* Account info */
            #accountInfoWidget {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1a1a1a, stop:1 #0e0e0e);
                border: 1px solid #333333;
                border-radius: 6px;
                padding: 2px;
            }

            #userIdLabel {
                color: #00d4ff;
                font-size: 11px;
                font-weight: 700;
                font-family: 'Consolas', 'Monaco', monospace;
            }

            #balanceLabel {
                color: #4aff4a;
                font-size: 11px;
                font-weight: 600;
            }

            #marginLabel {
                color: #ffaa00;
                font-size: 10px;
                font-weight: 500;
            }

            #separatorDot {
                color: #666666;
                font-size: 8px;
            }

            /* Alert buttons */
            #alertButton, #alertLogsButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #2a2a2a, stop:1 #1e1e1e);
                border: 1px solid #404040;
                color: #ffffff;
                border-radius: 4px;
                font-size: 14px;
                padding: 2px;
            }

            #alertButton:hover, #alertLogsButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #404040, stop:1 #2a2a2a);
                border: 1px solid #00d4ff;
            }

            #alertButtonActive {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ff6600, stop:1 #cc4400);
                border: 1px solid #ff8800;
                color: #ffffff;
                border-radius: 4px;
                font-size: 14px;
                padding: 2px;
            }

            /* Alert badges */
            #alertBadge, #logsBadge {
                background-color: #ff4444;
                color: #ffffff;
                font-size: 8px;
                font-weight: 700;
                border-radius: 6px;
                min-width: 12px;
                max-width: 20px;
                max-height: 12px;
                padding: 1px 2px;
                margin: 0px;
            }

            /* Completer dropdown */
            QAbstractItemView {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1a1a1a, stop:1 #0e0e0e);
                color: #ffffff;
                border: 2px solid #404040;
                border-radius: 4px;
                selection-background-color: #00d4ff;
                selection-color: #000000;
                font-size: 11px;
                padding: 2px;
            }

            QAbstractItemView::item {
                padding: 4px 8px;
                border-bottom: 1px solid #2a2a2a;
            }

            QAbstractItemView::item:hover {
                background-color: #333333;
            }

            QAbstractItemView::item:selected {
                background-color: #00d4ff;
                color: #000000;
            }

            /* Scrollbars for completer */
            QScrollBar:vertical {
                background-color: #1a1a1a;
                width: 12px;
                border-radius: 6px;
            }

            QScrollBar::handle:vertical {
                background-color: #404040;
                border-radius: 6px;
                min-height: 20px;
            }

            QScrollBar::handle:vertical:hover {
                background-color: #00d4ff;
            }
        """)

    def refresh_account_info_now(self):
        """Public method to manually trigger account info refresh."""
        self._refresh_account_info()

    def get_current_timeframe(self) -> str:
        """Returns the currently selected timeframe."""
        return "15m"  # Default since timeframe selector is removed

    def set_timeframe(self, timeframe: str):
        """Sets the timeframe programmatically."""
        # This method remains for compatibility but does nothing
        # since timeframe selector was removed
        pass

    def show_connection_status(self, connected: bool):
        """Shows connection status in the market status area."""
        if connected:
            self.market_text_label.setText("CONNECTED")
            self.market_status_label.setObjectName("marketOpen")
        else:
            self.market_text_label.setText("DISCONNECTED")
            self.market_status_label.setObjectName("marketClosed")

        self.market_status_label.style().polish(self.market_status_label)

    def add_custom_indicator(self, name: str, value: str, color: str = "#cccccc"):
        """Adds a custom indicator to the toolbar (for advanced features)."""
        # This could be used to add things like VIX, NIFTY levels, etc.
        indicator_label = QLabel(f"{name}: {value}")
        indicator_label.setStyleSheet(f"color: {color}; font-size: 10px; font-weight: 500;")

        # Insert before the account section
        account_index = self.layout().indexOf(self.account_info_widget)
        if account_index > 0:
            self.insertWidget(account_index, indicator_label)

    def closeEvent(self, event):
        """Clean up timers when closing."""
        if hasattr(self, 'account_timer'):
            self.account_timer.stop()
        if hasattr(self, 'market_timer'):
            self.market_timer.stop()
        super().closeEvent(event)