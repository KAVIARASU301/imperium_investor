import logging
from datetime import datetime
from typing import List, Dict, Any, Union

from PySide6.QtWidgets import (
    QToolBar, QLineEdit, QCompleter, QWidget, QLabel, QSizePolicy, QPushButton,
    QHBoxLayout, QVBoxLayout, QFrame
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

        # Draw background circle
        painter.setBrush(QColor("#ff4444"))
        painter.setPen(QPen(QColor("#ffffff"), 1))
        # Adjust rect to be within the widget bounds
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))

        # Draw text
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Arial", 7, QFont.Weight.Bold))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


class HeaderToolbar(QToolBar):
    """
    Enhanced compact, modern toolbar with advanced alert management features.
    """
    symbol_selected = Signal(str)
    add_alert_requested = Signal()  # For quick alert
    alert_manager_requested = Signal()  # For the full manager dialog
    alert_logs_requested = Signal()  # For the history tab/dialog
    watchlist_requested = Signal()
    portfolio_requested = Signal()
    orders_requested = Signal()
    market_depth_requested = Signal(str)
    timeframe_changed = Signal(str)

    def __init__(self, trader: Union[KiteConnect, Any], parent=None):
        super().__init__(parent)
        self.setMovable(False)
        self.setObjectName("enhancedHeaderToolbar")
        self.trader = trader
        self._instrument_map: Dict[str, Dict] = {}

        self._account_info = {'available_balance': 0.0, 'used_margin': 0.0}
        self._market_status = {'nse': 'unknown', 'bse': 'unknown'}

        self._init_ui()
        self._apply_styles()
        self._setup_timers()

    def _init_ui(self):
        """Initializes the enhanced UI components of the toolbar."""
        self._create_symbol_search_section()
        self._create_center_spacer()
        self._create_market_status_section()
        self._create_quick_actions_section()
        self._create_account_section()
        self._create_alert_section()  # Updated alert section

    def _create_symbol_search_section(self):
        """Creates enhanced symbol search section."""
        symbol_label = QLabel("Symbol:")
        symbol_label.setObjectName("sectionLabel")
        self.addWidget(symbol_label)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search symbols (e.g., RELIANCE)")
        self.search_input.setObjectName("enhancedSymbolSearch")
        self.search_input.returnPressed.connect(self._on_search_enter)
        self.addWidget(self.search_input)

        self.completer = QCompleter(self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.search_input.setCompleter(self.completer)
        self.completer.activated.connect(self._on_search_enter)

    def _create_center_spacer(self):
        spacer = QWidget()
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
        self._update_market_status()

    def _create_quick_actions_section(self):
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setObjectName("sectionSeparator")
        self.addWidget(separator)

        actions_widget = QWidget()
        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(4, 2, 4, 2)
        actions_layout.setSpacing(2)

        self.watchlist_btn = self._create_action_button("📋", "Watchlist", self.watchlist_requested.emit)
        actions_layout.addWidget(self.watchlist_btn)
        self.portfolio_btn = self._create_action_button("💼", "Portfolio", self.portfolio_requested.emit)
        actions_layout.addWidget(self.portfolio_btn)
        self.orders_btn = self._create_action_button("📝", "Orders", self.orders_requested.emit)
        actions_layout.addWidget(self.orders_btn)

        self.addWidget(actions_widget)

    def _create_action_button(self, icon_text: str, tooltip: str, callback) -> QPushButton:
        btn = QPushButton(icon_text)
        btn.setObjectName("quickActionButton")
        btn.setToolTip(tooltip)
        btn.clicked.connect(callback)
        btn.setFixedSize(28, 24)
        return btn

    def _create_account_section(self):
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setObjectName("sectionSeparator")
        self.addWidget(separator)

        account_widget = QWidget()
        account_widget.setObjectName("accountInfoWidget")
        account_layout = QHBoxLayout(account_widget)
        account_layout.setContentsMargins(8, 2, 8, 2)
        account_layout.setSpacing(8)

        self.user_id_label = QLabel("KE6286")
        self.user_id_label.setObjectName("userIdLabel")
        account_layout.addWidget(self.user_id_label)
        account_layout.addWidget(self._create_separator_dot())

        self.balance_label = QLabel("₹0")
        self.balance_label.setObjectName("balanceLabel")
        account_layout.addWidget(self.balance_label)
        account_layout.addWidget(self._create_separator_dot())

        self.margin_label = QLabel("Used: ₹0")
        self.margin_label.setObjectName("marginLabel")
        account_layout.addWidget(self.margin_label)

        self.addWidget(account_widget)

    def _create_separator_dot(self) -> QLabel:
        dot = QLabel("•")
        dot.setObjectName("separatorDot")
        return dot

    def _create_alert_section(self):
        """Creates enhanced alert management section with badges."""
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setObjectName("sectionSeparator")
        self.addWidget(separator)

        alert_widget = QWidget()
        alert_layout = QHBoxLayout(alert_widget)
        alert_layout.setContentsMargins(8, 0, 8, 0)
        alert_layout.setSpacing(8)

        # Quick Alert Button with Badge
        quick_alert_container = QWidget()
        quick_alert_container.setFixedSize(36, 28)
        self.quick_alert_button = QPushButton("🔔", quick_alert_container)
        self.quick_alert_button.setObjectName("quickAlertButton")
        self.quick_alert_button.setToolTip("Quick Alert (Ctrl+A)")
        self.quick_alert_button.clicked.connect(self.add_alert_requested.emit)
        self.quick_alert_button.setGeometry(0, 2, 34, 24)
        self.active_badge = NotificationBadge(quick_alert_container)
        self.active_badge.move(20, 0)
        alert_layout.addWidget(quick_alert_container)

        # Alert Manager Button
        self.alert_manager_button = self._create_action_button("⚙️", "Alert Manager (Ctrl+Shift+A)",
                                                               self.alert_manager_requested.emit)
        alert_layout.addWidget(self.alert_manager_button)

        # Alert History Button with Badge
        history_container = QWidget()
        history_container.setFixedSize(36, 28)
        self.alert_logs_button = QPushButton("📋", history_container)
        self.alert_logs_button.setObjectName("alertHistoryButton")
        self.alert_logs_button.setToolTip("Alert History")
        self.alert_logs_button.clicked.connect(self.alert_logs_requested.emit)
        self.alert_logs_button.setGeometry(0, 2, 34, 24)
        self.triggered_badge = NotificationBadge(history_container)
        self.triggered_badge.move(20, 0)
        alert_layout.addWidget(history_container)

        alert_label = QLabel("Alerts")
        alert_label.setObjectName("sectionLabel")
        alert_layout.addWidget(alert_label)

        self.addWidget(alert_widget)

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

    def set_instrument_data(self, instruments: List[Dict[str, Any]]):
        symbols = [inst['tradingsymbol'] for inst in instruments if 'tradingsymbol' in inst]
        self._instrument_map = {inst['tradingsymbol']: inst for inst in instruments if 'tradingsymbol' in inst}
        model = QStringListModel(symbols)
        self.completer.setModel(model)
        logger.info("Header toolbar search completer populated.")

    def update_alert_counts(self, active_count: int, triggered_today: int):
        """Updates alert count badges."""
        self.active_badge.set_count(active_count)
        self.triggered_badge.set_count(triggered_today)

    def set_current_symbol(self, symbol: str):
        self.search_input.setText(symbol)

    def _on_search_enter(self, text=""):
        symbol = (text or self.search_input.text()).upper().strip()
        if symbol and symbol in self._instrument_map:
            self.symbol_selected.emit(symbol)
        elif symbol:
            logger.warning(f"Invalid symbol entered: {symbol}")

    def _apply_styles(self):
        self.setStyleSheet("""
            QToolBar#enhancedHeaderToolbar {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0e0e0e, stop:1 #1a1a1a);
                border-bottom: 2px solid #2a2a2a; padding: 2px 8px; spacing: 8px; max-height: 40px;
            }
            #sectionLabel, #actionLabel { color: #a0c0ff; font-size: 11px; font-weight: 500; }
            #enhancedSymbolSearch {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1e1e1e, stop:1 #2a2a2a);
                border: 1px solid #404040; color: #ffffff; padding: 6px 12px; border-radius: 6px;
                font-size: 11px; min-width: 180px; max-width: 220px; max-height: 28px;
            }
            #enhancedSymbolSearch:focus { border: 1px solid #6a9cff; }
            #marketStatusWidget { background-color: rgba(30, 30, 30, 0.8); border-radius: 4px; border: 1px solid #333; }
            #marketOpen { color: #00ff00; } #marketClosed { color: #ff4444; }
            #marketStatusText { color: #cccccc; font-size: 10px; font-weight: 600; }
            #quickActionButton, #quickAlertButton, #alertManagerButton, #alertHistoryButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2a2a2a, stop:1 #1e1e1e);
                border: 1px solid #404040; color: #ffffff; border-radius: 4px; font-size: 12px; padding: 2px;
            }
            #quickActionButton:hover, #quickAlertButton:hover, #alertManagerButton:hover, #alertHistoryButton:hover {
                border: 1px solid #00d4ff;
            }
            #accountInfoWidget {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a1a1a, stop:1 #0e0e0e);
                border: 1px solid #333333; border-radius: 6px; padding: 2px;
            }
            #userIdLabel { color: #00d4ff; font-size: 11px; font-weight: 700; }
            #balanceLabel { color: #4aff4a; font-size: 11px; font-weight: 600; }
            #marginLabel { color: #ffaa00; font-size: 10px; font-weight: 500; }
            #separatorDot { color: #666666; font-size: 8px; }
            #sectionSeparator { background-color: #404040; max-width: 1px; margin: 4px 2px; }
            #notificationBadge {
                background-color: transparent; /* Handled by paintEvent */
                color: white; font-size: 8px; font-weight: 700;
                border: none;
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