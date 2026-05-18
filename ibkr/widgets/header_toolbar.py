# header_toolbar.py (Updated with Status Bar)

import logging
from typing import List, Dict, Any, Union

from PySide6.QtWidgets import (
    QToolBar, QLineEdit, QCompleter, QWidget, QLabel, QSizePolicy, QPushButton,
    QHBoxLayout
)
from PySide6.QtCore import Signal, Qt, QTimer, QEvent, QModelIndex, QSize
from PySide6.QtGui import QIcon, QKeyEvent, QStandardItemModel, QStandardItem
from kiteconnect import KiteConnect

from app_paths import get_asset_path
# Import the simple status bar
from widgets.status_bar import StatusBar, status

logger = logging.getLogger(__name__)
DEFAULT_PAPER_BALANCE = 1_000_000.0


class NotificationBadge(QLabel):
    """Sharp, layout-friendly alert count badge."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.count = 0
        self.setFixedSize(18, 18)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setObjectName("notificationBadge")
        self.setContentsMargins(0, 0, 0, 0)
        self.hide()

    def update_count(self, count: int):
        self.count = count
        if count > 0:
            self.setText(str(count) if count < 100 else "99+")
            self.show()
        else:
            self.hide()

    def set_count(self, count: int):
        """Backward compatible alias."""
        self.update_count(count)


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
        self.search_input = SymbolSearchInput()
        self.search_input.setPlaceholderText("Search symbol, company, exchange")
        self.search_input.setObjectName("enhancedSymbolSearch")
        self.search_input.textEdited.connect(self._on_search_text_edited)
        self.search_input.returnPressed.connect(self._on_search_enter)

        self.addWidget(self.search_input)

        # Buy and Sell buttons
        self.buy_button = QPushButton()
        self.buy_button.setObjectName("buyButton")
        self.buy_button.setFixedSize(24, 20)
        self.buy_button.setIconSize(QSize(10, 10))
        buy_icon_path = get_asset_path("icons", "plus.svg", required=True)
        if buy_icon_path is not None:
            self.buy_button.setIcon(QIcon(str(buy_icon_path)))
        self.buy_button.clicked.connect(self._on_buy_clicked)
        self.addWidget(self.buy_button)

        self.sell_button = QPushButton()
        self.sell_button.setObjectName("sellButton")
        self.sell_button.setFixedSize(24, 20)
        self.sell_button.setIconSize(QSize(10, 10))
        sell_icon_path = get_asset_path("icons", "minus.svg", required=True)
        if sell_icon_path is not None:
            self.sell_button.setIcon(QIcon(str(sell_icon_path)))
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
        self.completer.activated[QModelIndex].connect(self._on_completer_activated)


    def _create_status_bar_section(self):
        """NEW: Creates the LED-style status bar section."""
        self._add_section_gap()

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
        self._add_section_gap()

        alert_widget = QWidget()
        alert_widget.setObjectName("alertActionWidget")

        alert_layout = QHBoxLayout(alert_widget)
        alert_layout.setContentsMargins(0, 0, 0, 0)
        alert_layout.setSpacing(4)

        self.alerts_button = QPushButton("")
        self.alerts_button.setObjectName("alertActionButton")
        self.alerts_button.setIconSize(QSize(14, 14))
        alert_icon_path = get_asset_path("icons", "alert.svg", required=True)
        if alert_icon_path is not None:
            self.alerts_button.setIcon(QIcon(str(alert_icon_path)))
        self.alerts_button.clicked.connect(self.alert_manager_requested.emit)
        self.alerts_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.alerts_button.setFixedSize(24, 20)
        alert_layout.addWidget(self.alerts_button)

        self.alerts_badge = NotificationBadge()
        alert_layout.addWidget(self.alerts_badge)

        self.addWidget(alert_widget)

    def _create_trading_actions_section(self):
        """Creates trading actions section."""
        self._add_section_gap()

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

        self.addWidget(actions_widget)

    def _create_account_section(self):
        """Creates simplified account information display."""
        self._add_section_gap()

        self.account_info_widget = QWidget()
        self.account_info_widget.setObjectName("accountInfoWidget")
        account_layout = QHBoxLayout(self.account_info_widget)
        account_layout.setContentsMargins(6, 1, 6, 1)
        account_layout.setSpacing(6)

        self.user_id_label = QLabel("KE6286")
        self.user_id_label.setObjectName("userIdLabel")
        account_layout.addWidget(self.user_id_label)
        account_layout.addWidget(self._create_separator_dot())

        self.balance_pill = QWidget()
        self.balance_pill.setObjectName("balancePill")
        balance_layout = QHBoxLayout(self.balance_pill)
        balance_layout.setContentsMargins(0, 0, 0, 0)
        balance_layout.setSpacing(0)

        self.balance_currency_label = QLabel("₹")
        self.balance_currency_label.setObjectName("balanceCurrencyLabel")
        balance_layout.addWidget(self.balance_currency_label)

        self.balance_label = QLabel("0")
        self.balance_label.setObjectName("balanceValueLabel")
        balance_layout.addWidget(self.balance_label)

        account_layout.addWidget(self.balance_pill)

        self.addWidget(self.account_info_widget)

    @staticmethod
    def _create_separator_dot() -> QLabel:
        dot = QLabel("•")
        dot.setObjectName("separatorDot")
        return dot

    def _add_section_gap(self, width: int = 10) -> None:
        gap = QWidget()
        gap.setObjectName("sectionGap")
        gap.setFixedWidth(width)
        self.addWidget(gap)

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
            return "0"

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

        prefix = "-" if is_negative else ""
        return prefix + formatted

    def set_instrument_data(self, instruments: List[Dict[str, Any]]):
        """Set instrument data for symbol search."""
        self._instrument_map = {inst['tradingsymbol']: inst for inst in instruments if 'tradingsymbol' in inst}
        model = QStandardItemModel(self)

        for inst in instruments:
            symbol = inst.get("tradingsymbol")
            if not symbol:
                continue

            name = inst.get("name") or ""
            exchange = inst.get("exchange") or ""
            display_text = f"{symbol} — {name} ({exchange})" if name else f"{symbol} ({exchange})" if exchange else symbol

            item = QStandardItem(display_text)
            item.setData(symbol, Qt.ItemDataRole.UserRole)
            model.appendRow(item)

        self.completer.setModel(model)

    def update_alert_counts(self, active_count: int, triggered_today: int):
        """Update alert badge counts."""
        self.alerts_badge.update_count(triggered_today)

    def set_current_symbol(self, symbol: str):
        """Set the current symbol in the search input."""
        normalized_symbol = symbol.upper().strip()
        self.search_input.setText(normalized_symbol)
        self.search_input.set_committed_symbol(normalized_symbol)
        self.search_input.arm_replace_on_next_input()

    def get_current_symbol(self) -> str:
        """Get the current symbol from the search input."""
        return self.search_input.text().upper().strip()

    def _on_search_enter(self, text=""):
        """Handle symbol search."""
        symbol = (text or self.search_input.text()).upper().strip()
        if symbol and symbol in self._instrument_map:
            self.search_input.setText(symbol)
            self.search_input.set_committed_symbol(symbol)
            self.search_input.arm_replace_on_next_input()
            self.symbol_selected.emit(symbol)
        elif symbol:
            logger.warning(f"Invalid symbol entered: {symbol}")

    def _on_search_text_edited(self, text: str):
        """Show live matches as the user types."""
        if text.strip():
            self.completer.complete()

    def _on_completer_activated(self, index: QModelIndex):
        """Convert completion selection into canonical symbol text."""
        symbol = index.data(Qt.ItemDataRole.UserRole) or index.data(Qt.ItemDataRole.DisplayRole)
        if isinstance(symbol, str):
            self._on_search_enter(symbol)

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
                background-color: #0a0d12;
                border-bottom: 1px solid #1a2030;
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
                background-color: #0f1318;
                border: 1px solid transparent; 
                color: #ffffff; 
                padding: 3px 8px; 
                border-radius: 0px;
                font-size: 11px; 
                font-weight: 500;
                min-width: 84px; 
                max-width: 100px; 
                max-height: 20px;
            }
            #enhancedSymbolSearch:focus { 
                border: 1px solid #2f2f2f; 
                color: #ffffff;
            }
            #buyButton {
                background-color: #000000;
                color: white;
                border: 1px solid #333333;
                padding: 3px 6px;
                border-radius: 0px;
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
                border-radius: 0px;
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
                border-radius: 0px; 
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
                font-size: 9px; 
                font-weight: 600; 
            }
            #marketStatusWidget { 
                background-color: #1a1a1a; 
                border-radius: 0px; 
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
                font-size: 9px; 
                font-weight: 600; 
            }
            #sectionGap {
                background: transparent;
            }
            #notificationBadge {
                background-color: #E53935;
                color: #FFFFFF;
                border: none;
                border-radius: 2px;
                font-size: 10px;
                font-weight: 700;
                font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
            }
            #alertActionWidget, #tradingActionWidget {
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid #2f2f2f;
                border-radius: 0px;
            }
            #alertActionButton, #tradingActionButton {
                background-color: transparent;
                color: #cfcfcf;
                border: 1px solid #3a3a3a;
                padding: 3px 6px;
                border-radius: 0px;
                font-size: 9px;
                font-weight: 500;
            }
            #alertActionButton {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(255, 173, 51, 0.18), stop:1 rgba(255, 92, 92, 0.12));
                color: #ffe0b5;
                border: 1px solid #85541f;
                padding: 3px 8px;
                font-weight: 700;
            }
            #alertActionButton:hover, #tradingActionButton:hover {
                background-color: #232323;
                border: 1px solid #575757;
                color: #ffffff;
            }
            #alertActionButton:hover {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(255, 189, 89, 0.26), stop:1 rgba(255, 108, 108, 0.20));
                border: 1px solid #aa6b25;
            }
            #alertActionButton:pressed {
                background-color: rgba(255, 154, 61, 0.20);
                border: 1px solid #c67d2b;
            }
            #accountInfoWidget {
                background-color: rgba(255, 255, 255, 0.03);
                border: none;
                border-radius: 0px;
                padding: 2px 6px;
            }
            #userIdLabel { 
                background-color: rgba(0, 212, 255, 0.10);
                color: #7ee9ff;
                border: none;
                padding: 3px 8px;
                border-radius: 4px;
                font-size: 9px;
                font-weight: 600;
                letter-spacing: 0.4px;
            }
            #balancePill {
                background-color: transparent;
                border: 1px solid #276a55;
                border-radius: 0px;
            }
            #balanceCurrencyLabel {
                background-color: #1f9d73;
                color: #03170f;
                border: none;
                padding: 3px 7px;
                font-size: 10px;
                font-weight: 900;
                font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
            }
            #balanceValueLabel {
                background-color: #0f2a21;
                color: #a6ffd8;
                border: none;
                padding: 3px 10px 3px 9px;
                font-size: 10px;
                font-weight: 800;
                font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
                letter-spacing: 0.3px;
            }
            #separatorDot { 
                background-color: transparent;
                color: #666666; 
                font-size: 8px; 
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
    """Enhanced QLineEdit tuned for high-speed symbol lookup."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._select_all_on_mouse_release = False
        self._replace_on_next_input = False
        self._committed_symbol = ""
        self.textEdited.connect(self._normalize_user_text)

    def arm_replace_on_next_input(self):
        """Enable one-shot replace mode so next typed character overwrites current symbol."""
        self._replace_on_next_input = True

    def set_committed_symbol(self, symbol: str):
        """Remember the most recently confirmed symbol for escape-to-revert."""
        self._committed_symbol = (symbol or "").upper().strip()

    def _normalize_user_text(self, text: str):
        """Keep symbol inputs consistently uppercase."""
        normalized = text.upper()
        if normalized != text:
            cursor_pos = self.cursorPosition()
            self.blockSignals(True)
            self.setText(normalized)
            self.setCursorPosition(cursor_pos)
            self.blockSignals(False)

    def keyPressEvent(self, event):
        """Override to support TradingView-like replace and navigation behaviour."""
        key = event.key()
        text_value = event.text()

        if key == Qt.Key.Key_Escape:
            if self._committed_symbol:
                self.setText(self._committed_symbol)
                self.selectAll()
            event.accept()
            return

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
            self._replace_on_next_input = True
            super().keyPressEvent(event)
            return

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

        is_text_input = bool(text_value and text_value.isprintable() and not event.modifiers())
        if is_text_input and self._replace_on_next_input:
            self.clear()
            self._replace_on_next_input = False

        # For all other keys, use default handling
        super().keyPressEvent(event)
        if is_text_input:
            self._replace_on_next_input = False

    def focusInEvent(self, event):
        """Handle focus in event."""
        super().focusInEvent(event)
        self.selectAll()
        self._select_all_on_mouse_release = True
        # Optionally show completer when focused if there's text
        if self.text().strip() and self.completer():
            self.completer().complete()

    def mousePressEvent(self, event):
        """Single click should allow immediate overwrite of existing symbol text."""
        super().mousePressEvent(event)
        if self._select_all_on_mouse_release:
            self.selectAll()
            self._select_all_on_mouse_release = False

    def focusOutEvent(self, event):
        """Handle focus out event."""
        super().focusOutEvent(event)
        self._select_all_on_mouse_release = False
        # Hide completer when focus is lost
        if self.completer() and self.completer().popup().isVisible():
            self.completer().popup().hide()
