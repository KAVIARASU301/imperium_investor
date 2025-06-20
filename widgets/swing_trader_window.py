import logging
import os
import json
import shutil
from datetime import datetime
from typing import List, Dict, Union, Any

from PySide6.QtCore import Qt, QUrl, QByteArray, QTimer, Slot, QPoint
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtWidgets import QMainWindow, QSplitter, QMessageBox, QDialog, QWidget, QVBoxLayout, QHBoxLayout, \
    QPushButton, QLabel, QMenu
from PySide6.QtGui import QMouseEvent, QAction

from widgets.menu_bar import create_main_menu
from tables.chartink_scanner_table import ChartinkScannerTable
from tables.open_positions_table import OpenPositionsTable
from tables.watchlist_table import TabbedWatchlistWidget
from widgets.canvas_candlestick_chart import CandlestickChart as ChartWindow
from widgets.header_toolbar import HeaderToolbar

# Import both order dialogs - keep the old one for compatibility, use new one by default
from dialogs.order_dialog import OrderConfirmationDialog
from dialogs.order_dialog import OrderDialog  # New advanced order dialog

from dialogs.settings_dialog import SettingsDialog
from dialogs.stock_alert_dialog import StockAlertDialog
from dialogs.alert_logs_dialog import AlertLogsDialog
from dialogs.order_history_dialog import OrderHistoryDialog
from dialogs.pnl_history_dialog import PnlHistoryDialog
from dialogs.performance_dialog import PerformanceDialog

# Import advanced components
from utils.advanced_order_manager import AdvancedOrderManager, setup_advanced_order_manager
from utils.risk_management import (
    AdvancedRiskManager, PositionMonitor, TradeAnalyzer, TradingRules,
    integrate_risk_management
)

from utils.market_data_worker import MarketDataWorker
from utils.paper_trading_manager import PaperTradingManager
from utils.position_manager import PositionManager
from utils.config_manager import ConfigManager
from utils.instrument_loader import InstrumentLoader
from utils.theme_manager import ThemeManager
from utils.trade_logger import TradeLogger
from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)


class SwingTraderWindow(QMainWindow):
    """
    The main frameless window for the Swing Trader application with professional dark theme.
    Now includes advanced order management, risk management, and enhanced trading features.
    """

    def __init__(self, trader: Union[KiteConnect, PaperTradingManager], real_kite_client: KiteConnect, api_key: str,
                 access_token: str):
        super().__init__()

        # Core Application Components
        self.trader = trader
        self.real_kite_client = real_kite_client
        self.api_key = api_key
        self.access_token = access_token
        self.config_manager = ConfigManager()
        self.theme_manager = ThemeManager(self)
        self.trading_mode = 'paper' if isinstance(trader, PaperTradingManager) else 'live'
        self.trade_logger = TradeLogger(mode=self.trading_mode)
        self.position_manager = PositionManager(self.trader, self.trade_logger)
        self.instrument_list: List[Dict] = []
        self.instrument_map: Dict[str, Dict] = {}

        # Set trade logger in paper trading manager
        if isinstance(self.trader, PaperTradingManager):
            self.trader.set_trade_logger(self.trade_logger)

        # Window dragging variables
        self._drag_pos = None
        self._is_maximized = False

        # Advanced components - Initialize after basic setup
        self.order_manager = None
        self.risk_manager = None
        self.position_monitor = None
        self.trade_analyzer = None
        self.trading_rules = None

        # Setup frameless window
        self._setup_frameless_window()

        # UI Initialization
        self._setup_ui()
        self._setup_menu_bar()
        self._connect_signals()
        self._init_alert_system()
        self._init_background_workers()

        # Initialize advanced components
        self._init_advanced_components()

        self._apply_dark_theme()
        self.restore_window_state()

    def _init_advanced_components(self):
        """Initialize advanced order management and risk management components."""
        try:
            # Setup advanced order manager
            self.order_manager = AdvancedOrderManager(self.trader, self.config_manager)

            # Setup risk management
            self.risk_manager = AdvancedRiskManager(self.config_manager)
            self.position_monitor = PositionMonitor(self.risk_manager)
            self.trade_analyzer = TradeAnalyzer()
            self.trading_rules = TradingRules()

            # Connect signals for advanced components
            self._connect_advanced_signals()

            logger.info("Advanced trading components initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize advanced components: {e}")
            # Fallback to basic functionality
            self.order_manager = None
            self.risk_manager = None

    def _connect_advanced_signals(self):
        """Connect signals for advanced order management and risk management."""
        if self.order_manager:
            # Order manager signals
            self.order_manager.order_placed.connect(self._on_order_placed)
            self.order_manager.order_executed.connect(self._on_order_executed)
            self.order_manager.order_cancelled.connect(self._on_order_cancelled)
            self.order_manager.order_rejected.connect(self._on_order_rejected)
            self.order_manager.bracket_order_completed.connect(self._on_bracket_completed)
            self.order_manager.oco_triggered.connect(self._on_oco_triggered)

        if self.risk_manager:
            # Risk manager signals
            self.risk_manager.risk_limit_exceeded.connect(self._handle_risk_alert)
            self.risk_manager.position_limit_reached.connect(self._handle_position_limit_alert)

    def _setup_frameless_window(self):
        """Setup frameless window with custom title bar."""
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumSize(1200, 700)

    def _setup_ui(self):
        """Initializes and arranges all UI widgets in frameless container."""
        # Main container widget
        main_container = QWidget()
        main_container.setObjectName("mainContainer")
        self.setCentralWidget(main_container)

        # Main layout with zero margins
        main_layout = QVBoxLayout(main_container)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Custom title bar
        self.title_bar = self._create_custom_title_bar()
        main_layout.addWidget(self.title_bar)

        # Compact header toolbar
        self.header_toolbar = HeaderToolbar(self, self)
        main_layout.addWidget(self.header_toolbar)

        # Main content splitter
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.main_splitter)

        # Create widgets
        self.chartink_scanner = ChartinkScannerTable()
        self.candlestick_chart = ChartWindow(self.real_kite_client)
        self.watchlist = TabbedWatchlistWidget()
        self.positions_table = OpenPositionsTable()

        # Layout: Scanner | Chart | Watchlist + Positions (stacked)
        self.main_splitter.addWidget(self.chartink_scanner)
        self.main_splitter.addWidget(self.candlestick_chart)

        # Right panel: Watchlist on top, Positions on bottom
        right_panel_splitter = QSplitter(Qt.Orientation.Vertical)
        right_panel_splitter.addWidget(self.watchlist)
        right_panel_splitter.addWidget(self.positions_table)
        right_panel_splitter.setSizes([500, 200])

        self.main_splitter.addWidget(right_panel_splitter)
        self.main_splitter.setSizes([350, 800, 300])

    def _create_custom_title_bar(self) -> QWidget:
        """Creates a custom title bar for the frameless window."""
        title_bar = QWidget()
        title_bar.setObjectName("customTitleBar")
        title_bar.setFixedHeight(28)  # Compact title bar

        layout = QHBoxLayout(title_bar)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(4)

        # App title
        title_label = QLabel("Swing Trader Pro")
        title_label.setObjectName("appTitle")
        layout.addWidget(title_label)

        # Trading mode indicator
        mode_label = QLabel(f"[{self.trading_mode.upper()}]")
        mode_label.setObjectName("tradingModeLabel")
        layout.addWidget(mode_label)

        layout.addStretch()

        # Window controls
        min_btn = QPushButton("−")
        min_btn.setObjectName("titleBarButton")
        min_btn.setFixedSize(24, 24)
        min_btn.clicked.connect(self.showMinimized)
        layout.addWidget(min_btn)

        self.max_btn = QPushButton("□")
        self.max_btn.setObjectName("titleBarButton")
        self.max_btn.setFixedSize(24, 24)
        self.max_btn.clicked.connect(self._toggle_maximize)
        layout.addWidget(self.max_btn)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeTitleBarButton")
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

        # Enable dragging on title bar
        title_bar.mousePressEvent = self._title_bar_mouse_press
        title_bar.mouseMoveEvent = self._title_bar_mouse_move
        title_bar.mouseDoubleClickEvent = self._title_bar_double_click

        return title_bar

    def _title_bar_mouse_press(self, event: QMouseEvent):
        """Handle title bar mouse press for window dragging."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _title_bar_mouse_move(self, event: QMouseEvent):
        """Handle title bar mouse move for window dragging."""
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos:
            if not self._is_maximized:
                self.move(event.globalPosition().toPoint() - self._drag_pos)

    def _title_bar_double_click(self, event: QMouseEvent):
        """Handle title bar double click to maximize/restore."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximize()

    def _toggle_maximize(self):
        """Toggle between maximized and normal window state."""
        if self._is_maximized:
            self.showNormal()
            self.max_btn.setText("□")
            self._is_maximized = False
        else:
            self.showMaximized()
            self.max_btn.setText("❐")
            self._is_maximized = True

    def _setup_menu_bar(self):
        """Creates a hidden menu bar (accessible via shortcuts)."""
        # We'll keep the menu functionality but hide the visual menu bar
        menubar, menu_actions = create_main_menu(self)
        menubar.setVisible(False)  # Hide the menu bar
        self.setMenuBar(menubar)

        menu_actions["refresh"].triggered.connect(self.position_manager.fetch_positions_and_orders)
        menu_actions["settings"].triggered.connect(self._show_settings_dialog)
        menu_actions["order_history"].triggered.connect(self._show_order_history_dialog)
        menu_actions["pnl_calendar"].triggered.connect(self._show_pnl_history_dialog)
        menu_actions["performance"].triggered.connect(self._show_performance_dialog)
        menu_actions["exit"].triggered.connect(self.close)

    def _connect_signals(self):
        """Central place to connect all signals and slots across the application."""
        self.header_toolbar.symbol_selected.connect(self.candlestick_chart.on_search)
        self.header_toolbar.add_alert_requested.connect(self._show_add_alert_dialog)
        self.header_toolbar.alert_logs_requested.connect(self._show_alert_logs_dialog)

        # Connect the order_button_clicked signal from candlestick_chart
        self.candlestick_chart.order_button_clicked.connect(self._show_advanced_order_dialog)

        # Symbol selection connections
        self.chartink_scanner.symbol_selected.connect(self.candlestick_chart.on_search)
        self.watchlist.symbol_selected.connect(self.candlestick_chart.on_search)
        self.positions_table.symbol_selected.connect(self.candlestick_chart.on_search)

        # Position manager connections
        self.position_manager.positions_updated.connect(self.positions_table.update_positions)

        # Positions table connections
        self.positions_table.exit_position_requested.connect(self._on_exit_position_requested)
        self.positions_table.subscribe_tokens_requested.connect(self._subscribe_to_tokens)

        # Watchlist connections - Updated to use advanced order dialog
        self.watchlist.subscribe_tokens_requested.connect(self._subscribe_to_tokens)
        self.watchlist.place_order_requested.connect(self._show_advanced_order_dialog)
        self.watchlist.watchlist_changed.connect(self._on_websocket_connect)

        # Chartink scanner connections
        self.chartink_scanner.subscribe_tokens_requested.connect(self._subscribe_to_tokens)

    def _init_background_workers(self):
        """Initializes and starts background threads for data fetching."""
        self.instrument_loader = InstrumentLoader(self.real_kite_client)
        self.instrument_loader.instruments_loaded.connect(self._on_instruments_loaded)
        self.instrument_loader.error_occurred.connect(
            lambda e: logger.error(f"Critical error loading instruments: {e}")
        )
        self.instrument_loader.start()

        self.market_data_worker = MarketDataWorker(self.api_key, self.access_token)
        self.market_data_worker.data_received.connect(self._on_market_data)
        self.market_data_worker.connection_established.connect(self._on_websocket_connect)
        self.market_data_worker.start()

    def _init_alert_system(self):
        """Loads alerts from file and sets up the alert sound."""
        self.alerts = self._load_json("user_data/alerts.json", [])
        self.triggered_alerts = self._load_json("user_data/alert_history.json", [])
        self.alert_sound = QSoundEffect(self)
        sound_file = os.path.join("icons", "notify.wav")
        if os.path.exists(sound_file):
            self.alert_sound.setSource(QUrl.fromLocalFile(sound_file))
            self.alert_sound.setVolume(0.7)
        else:
            logger.warning(f"Alert sound file not found at {sound_file}")

    # ======================
    # ADVANCED ORDER MANAGEMENT
    # ======================

    @Slot(str, float)
    def _show_advanced_order_dialog(self, symbol: str, ltp_from_chart: float = 0.0):
        """Enhanced order dialog with advanced features, now accepting LTP from chart."""
        # Use LTP from chart if provided and valid, otherwise fetch fresh LTP
        ltp = ltp_from_chart if ltp_from_chart > 0.0 else self._get_fresh_ltp(symbol)

        if ltp == 0.0:
            QMessageBox.warning(self, "LTP Not Available", f"Could not fetch LTP for {symbol}.")
            return

        # Check if symbol exists in instrument map
        if symbol not in self.instrument_map:
            QMessageBox.warning(self, "Symbol Not Found", f"Symbol {symbol} not found in instrument database.")
            return

        order_details = {
            'tradingsymbol': symbol,
            'ltp': ltp,
            # Default to BUY, user can change in dialog
            'transaction_type': 'BUY',
            # Default quantity, can be overridden by user settings or dialog default
            'quantity': self.config_manager.load_settings().get('default_quantity', 1)
        }

        # Create and show advanced order dialog
        dialog = OrderDialog(self, symbol, ltp, order_details)

        # Connect signals
        dialog.order_placed.connect(self._handle_order_placement)
        dialog.bracket_order_placed.connect(self._handle_bracket_order_placement)

        # Show dialog
        dialog.show()

    def _get_fresh_ltp(self, symbol: str) -> float:
        """Get the most recent LTP for a symbol from multiple sources."""
        ltp = 0.0

        # Try watchlist data first
        if hasattr(self, 'watchlist') and hasattr(self.watchlist, '_tables'):
            for table in self.watchlist._tables.values():
                if hasattr(table, 'get_watchlist_data'):
                    watchlist_data = table.get_watchlist_data()
                    if symbol in watchlist_data:
                        ltp = watchlist_data[symbol].get('ltp', 0)
                        if ltp > 0:
                            logger.debug(f"LTP for {symbol} from watchlist: {ltp}")
                            break

        # Try scanner data
        if not ltp and hasattr(self, 'chartink_scanner') and hasattr(self.chartink_scanner, '_symbol_data'):
            scanner_data = self.chartink_scanner._symbol_data.get(symbol, {})
            ltp = scanner_data.get('ltp', 0)
            if ltp > 0:
                logger.debug(f"LTP for {symbol} from scanner: {ltp}")

        # Try instrument map
        if not ltp and symbol in self.instrument_map:
            ltp = self.instrument_map[symbol].get('last_price', 0)
            if ltp > 0:
                logger.debug(f"LTP for {symbol} from instrument map: {ltp}")

        # Fallback to API quote (only if we have permission)
        if not ltp and self.real_kite_client:
            try:
                instrument_info = self.instrument_map.get(symbol, {})
                token = instrument_info.get('instrument_token')
                exchange = instrument_info.get('exchange', 'NSE')

                if token:
                    # Use the correct quote method format
                    quote_key = f"{exchange}:{symbol}"
                    quote = self.real_kite_client.quote([quote_key])
                    if quote_key in quote:
                        ltp = quote[quote_key].get('last_price', 0)
                        if ltp > 0:
                            logger.debug(f"LTP for {symbol} from Kite API: {ltp}")
            except Exception as e:
                logger.warning(f"Failed to fetch LTP for {symbol} via Kite API: {e}")

        return ltp

    def _handle_order_placement(self, order_data: Dict[str, Any]):
        """Handle regular order placement with enhanced error handling."""
        try:
            logger.info(f"Placing order: {order_data}")

            # Validate order data with risk management
            if self.risk_manager:
                is_valid, error_msg = self.risk_manager.validate_order(order_data)
                if not is_valid:
                    self._show_order_notification(f"Order validation failed: {error_msg}", "error")
                    return
            else:
                # Basic validation fallback
                if not self._validate_order_data(order_data):
                    return

            # Get instrument details for exchange information
            instrument_info = self.instrument_map.get(order_data['tradingsymbol'], {})

            # Prepare complete order data with required fields
            complete_order_data = {
                "variety": "regular",  # Add default variety
                "exchange": instrument_info.get('exchange', 'NSE'),  # Get exchange from instrument map
                "tradingsymbol": order_data['tradingsymbol'],
                "transaction_type": order_data['transaction_type'],
                "quantity": order_data['quantity'],
                "order_type": order_data['order_type'],
                "product": order_data.get('product', 'MIS'),
                "validity": order_data.get('validity', 'DAY')
            }

            # Add price for limit/SL orders
            if order_data['order_type'] in ["LIMIT", "SL"]:
                complete_order_data["price"] = order_data.get('price', 0)

            # Add trigger price for SL orders
            if order_data['order_type'] in ["SL", "SL-M"]:
                complete_order_data["trigger_price"] = order_data.get('trigger_price', 0)

            # Use advanced order manager if available
            if self.order_manager:
                order_id = self.order_manager.place_order(complete_order_data)
                if order_id:
                    self._show_order_notification(f"Order placed successfully: {order_id}", "success")
                    self._refresh_positions_table()
            else:
                # Fallback to direct trader with all required parameters
                self._place_order_direct(complete_order_data)

            if order_id:
                # Log order placement immediately
                self.trade_logger.log_order_placement(complete_order_data, order_id)
                self._show_order_notification(f"Order placed: {order_id}", "success")
                self._refresh_positions_table()

        except Exception as e:
            error_msg = f"Order placement failed: {str(e)}"
            logger.error(error_msg)
            self._show_order_notification(error_msg, "error")

    def _handle_bracket_order_placement(self, bracket_order_data: Dict[str, Any]):
        """Handle bracket order placement."""
        try:
            logger.info(f"Placing bracket order: {bracket_order_data}")

            if self.order_manager:
                order_id = self.order_manager.place_bracket_order(bracket_order_data)
                if order_id:
                    self._show_order_notification(f"Bracket order placed: {order_id}", "success")
            else:
                # Fallback simulation
                self._simulate_bracket_order(bracket_order_data)

        except Exception as e:
            error_msg = f"Bracket order failed: {str(e)}"
            logger.error(error_msg)
            self._show_order_notification(error_msg, "error")

    def _place_order_direct(self, order_data: Dict[str, Any]):
        """Direct order placement fallback with proper parameter handling."""
        try:
            if self.trading_mode == 'paper':
                # Paper trading - pass all required parameters
                order_id = self.trader.place_order(
                    variety=order_data.get('variety', 'regular'),
                    exchange=order_data.get('exchange', 'NSE'),
                    tradingsymbol=order_data['tradingsymbol'],
                    transaction_type=order_data['transaction_type'],
                    quantity=order_data['quantity'],
                    product=order_data.get('product', 'MIS'),
                    order_type=order_data.get('order_type', 'MARKET'),
                    price=order_data.get('price'),
                    trigger_price=order_data.get('trigger_price'),
                    validity=order_data.get('validity', 'DAY')
                )
            else:
                # Live trading - use KiteConnect parameters
                order_id = self.real_kite_client.place_order(
                    variety=order_data.get('variety', 'regular'),
                    exchange=order_data.get('exchange', 'NSE'),
                    tradingsymbol=order_data['tradingsymbol'],
                    transaction_type=order_data['transaction_type'],
                    quantity=order_data['quantity'],
                    product=order_data.get('product', 'MIS'),
                    order_type=order_data.get('order_type', 'MARKET'),
                    price=order_data.get('price'),
                    trigger_price=order_data.get('trigger_price'),
                    validity=order_data.get('validity', 'DAY')
                )

            if order_id:
                self.trade_logger.log_order_placement(order_data, order_id)
                self._show_order_notification(f"Order placed: {order_id}", "success")
                self._refresh_positions_table()

        except Exception as e:
            logger.error(f"Direct order placement failed: {e}")
            raise

    def _simulate_bracket_order(self, bracket_order_data: Dict[str, Any]):
        """Simulate bracket order for paper trading."""
        # Extract data
        symbol = bracket_order_data['tradingsymbol']
        quantity = bracket_order_data['quantity']
        entry_price = bracket_order_data['price']
        squareoff = bracket_order_data['squareoff']
        stoploss = bracket_order_data['stoploss']
        transaction_type = bracket_order_data['transaction_type']

        # Calculate target and SL prices
        if transaction_type == "BUY":
            target_price = entry_price + squareoff
            sl_price = entry_price - stoploss
        else:
            target_price = entry_price - squareoff
            sl_price = entry_price + stoploss

        # Place entry order
        entry_order = {
            "tradingsymbol": symbol,
            "transaction_type": transaction_type,
            "quantity": quantity,
            "order_type": "LIMIT",
            "price": entry_price,
            "product": "MIS",
            "validity": "DAY",
            "tag": "BRACKET_ENTRY"
        }

        try:
            entry_id = self.trader.place_order(**entry_order)
            logger.info(f"Bracket order entry placed: {entry_id}")
            self._show_order_notification(f"Bracket order placed: {entry_id}", "success")

        except Exception as e:
            logger.error(f"Bracket order simulation failed: {e}")
            raise

    # ======================
    # ORDER MANAGEMENT EVENT HANDLERS
    # ======================

    def _on_order_placed(self, order_data):
        """Handle order placed event."""
        symbol = order_data.get('tradingsymbol', '')
        logger.info(f"Order placed for {symbol}")

    def _on_order_executed(self, order_data):
        """Handle order execution event."""
        symbol = order_data.get('tradingsymbol', '')
        transaction_type = order_data.get('transaction_type', '')
        quantity = order_data.get('quantity', 0)

        message = f"{transaction_type} {quantity} {symbol} executed"
        self._show_order_notification(message, "success")

        self.trade_logger.log_order_update(order_data)

        # Refresh positions table
        self._refresh_positions_table()

    def _on_order_cancelled(self, order_data):
        """Handle order cancellation event."""
        symbol = order_data.get('tradingsymbol', '')
        self._show_order_notification(f"Order cancelled for {symbol}", "info")

    def _on_order_rejected(self, order_data, reason):
        """Handle order rejection with logging."""
        symbol = order_data.get('tradingsymbol', '')
        self._show_order_notification(f"Order rejected for {symbol}: {reason}", "error")

        # Update order status
        order_data['status'] = 'REJECTED'
        order_data['status_message'] = reason
        self.trade_logger.log_order_update(order_data)

    def _on_bracket_completed(self, bracket_data):
        """Handle bracket order completion."""
        parent_order = bracket_data.get('parent_order', {})
        symbol = parent_order.get('tradingsymbol', '')
        self._show_order_notification(f"Bracket order completed for {symbol}", "success")

    def _on_oco_triggered(self, triggered_order, cancelled_order):
        """Handle OCO order trigger."""
        symbol = triggered_order.get('tradingsymbol', '')
        self._show_order_notification(f"OCO order triggered for {symbol}", "info")

    # ======================
    # RISK MANAGEMENT
    # ======================

    def _handle_risk_alert(self, message: str, risk_value: float):
        """Handle risk limit exceeded alerts."""
        logger.warning(f"Risk Alert: {message}")
        self._show_order_notification(message, "error")

    def _handle_position_limit_alert(self, message: str, position_count: int):
        """Handle position limit alerts."""
        logger.warning(f"Position Alert: {message}")
        self._show_order_notification(message, "error")

    def _validate_order_data(self, order_data: Dict[str, Any]) -> bool:
        """Enhanced order validation."""
        required_fields = ['tradingsymbol', 'transaction_type', 'quantity', 'order_type']

        # Check required fields
        for field in required_fields:
            if field not in order_data:
                self._show_order_notification(f"Missing required field: {field}", "error")
                return False

        # Validate quantity
        if order_data['quantity'] <= 0:
            self._show_order_notification("Quantity must be greater than 0", "error")
            return False

        # Validate symbol exists in instrument map
        if order_data['tradingsymbol'] not in self.instrument_map:
            self._show_order_notification(f"Symbol {order_data['tradingsymbol']} not found", "error")
            return False

        # Validate price for limit orders
        if order_data['order_type'] in ['LIMIT', 'SL'] and not order_data.get('price'):
            self._show_order_notification("Price is required for limit/SL orders", "error")
            return False

        # Validate trigger price for SL orders
        if order_data['order_type'] in ['SL', 'SL-M'] and not order_data.get('trigger_price'):
            self._show_order_notification("Trigger price is required for SL/SL-M orders", "error")
            return False

        return True

    # ======================
    # EXISTING ORDER DIALOG (FALLBACK)
    # ======================

    def _show_order_dialog(self, order_details: Dict[str, Any]):
        """Shows the basic order confirmation dialog (fallback)."""
        symbol = order_details['tradingsymbol']
        ltp = self._get_fresh_ltp(symbol)

        order_details['ltp'] = ltp
        order_details.setdefault('price', ltp)
        order_details.setdefault('quantity', self.config_manager.load_settings().get('default_quantity', 1))
        order_details['estimated_cost'] = order_details.get('price', ltp) * order_details['quantity']

        dialog = OrderConfirmationDialog(self, order_details)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                final_order = dialog.order_details
                self.trader.place_order(
                    variety=self.trader.VARIETY_REGULAR,
                    exchange=self.instrument_map.get(final_order['tradingsymbol'], {}).get('exchange', 'NSE'),
                    tradingsymbol=final_order['tradingsymbol'],
                    transaction_type=final_order['transaction_type'],
                    quantity=final_order['quantity'],
                    product=final_order.get('product', 'NRML'),
                    order_type=final_order.get('order_type', 'MARKET'),
                    price=final_order.get('price')
                )
                logger.info(f"Order placed for {final_order['tradingsymbol']}")
                QTimer.singleShot(2000, self.position_manager.fetch_positions_and_orders)
            except Exception as e:
                logger.error(f"Failed to place order: {e}", exc_info=True)
                QMessageBox.critical(self, "Order Placement Failed", str(e))

    # ======================
    # NOTIFICATION SYSTEM
    # ======================

    def _show_order_notification(self, message: str, notification_type: str = "info"):
        """Show order notification with appropriate styling."""
        from PySide6.QtCore import QTimer

        # Create custom notification widget or use message box
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Order Status")
        msg_box.setText(message)

        # Set icon based on type
        if notification_type == "success":
            msg_box.setIcon(QMessageBox.Icon.Information)
            msg_box.setStyleSheet("""
                QMessageBox {
                    background-color: #0a0a0a;
                    color: #00b894;
                }
                QPushButton {
                    background-color: #00b894;
                    color: white;
                    padding: 6px 12px;
                    border-radius: 4px;
                }
            """)
        elif notification_type == "error":
            msg_box.setIcon(QMessageBox.Icon.Critical)
            msg_box.setStyleSheet("""
                QMessageBox {
                    background-color: #0a0a0a;
                    color: #d63031;
                }
                QPushButton {
                    background-color: #d63031;
                    color: white;
                    padding: 6px 12px;
                    border-radius: 4px;
                }
            """)
        else:
            msg_box.setIcon(QMessageBox.Icon.Information)

        # Auto-close after 3 seconds for success messages
        if notification_type == "success":
            QTimer.singleShot(3000, msg_box.accept)

        msg_box.exec()

    def _refresh_positions_table(self):
        """Refresh positions table after order placement."""
        if hasattr(self, 'positions_table'):
            try:
                # Get fresh positions data
                if self.trading_mode == 'paper':
                    positions = self.trader.positions()
                else:
                    positions = self.real_kite_client.positions()

                # Update the table
                self.positions_table.update_positions(positions)

                # Update risk manager if available
                if self.risk_manager:
                    self.risk_manager.update_positions(positions)

            except Exception as e:
                logger.error(f"Failed to refresh positions: {e}")

    # ======================
    # EXISTING METHODS (UNCHANGED)
    # ======================

    @Slot(list)
    def _on_instruments_loaded(self, instruments: List[Dict]):
        """Handles the fully loaded list of instruments."""
        logger.info(f"Successfully loaded {len(instruments)} instruments.")
        self.instrument_list = instruments
        self.instrument_map = {
            inst['tradingsymbol']: inst for inst in instruments if 'tradingsymbol' in inst
        }

        # Distribute instrument data to all components
        self.header_toolbar.set_instrument_data(instruments)
        self.candlestick_chart.set_instrument_list(instruments)
        self.position_manager.set_instrument_data(instruments)
        self.watchlist.set_instrument_map(self.instrument_map)
        self.chartink_scanner.set_instrument_map(self.instrument_map)
        self.chartink_scanner.set_kite_client(self.real_kite_client)

        if isinstance(self.trader, PaperTradingManager):
            self.trader.set_instrument_data(instruments)

        self._on_websocket_connect()

    @Slot(list)
    def _on_market_data(self, ticks: List[Dict]):
        """Distributes live market data ticks to all interested components."""
        self.position_manager.update_pnl_from_market_data(ticks)
        self.watchlist.update_data(ticks)
        self.chartink_scanner.update_data(ticks)
        self._check_alerts(ticks)

        # Update risk manager with latest market data
        if self.risk_manager:
            # Calculate daily P&L from positions
            try:
                if self.trading_mode == 'paper':
                    positions = self.trader.positions()
                else:
                    positions = self.real_kite_client.positions()

                daily_pnl = sum(pos.get('pnl', 0) for pos in positions.get('day', []))
                self.risk_manager.update_daily_pnl(daily_pnl)

            except Exception as e:
                logger.debug(f"Could not update daily P&L: {e}")

    @Slot()
    def _on_websocket_connect(self):
        """Consolidates all subscription requests and sends them to the worker."""
        logger.info("WebSocket connected/changed. Subscribing to all required tokens.")
        all_tokens = set()
        all_tokens.update(self.positions_table.get_all_tokens())
        all_tokens.update(self.watchlist.get_all_tokens())
        all_tokens.update(self.chartink_scanner.get_all_tokens())
        all_tokens.update(self._get_alert_tokens())

        if all_tokens:
            self.market_data_worker.set_instruments(all_tokens)
            logger.info(f"Subscribed to {len(all_tokens)} instrument tokens")

    @Slot(list)
    def _subscribe_to_tokens(self, tokens: List[int]):
        """Adds a list of instrument tokens to the WebSocket subscription."""
        if self.market_data_worker and tokens:
            current_tokens = getattr(self.market_data_worker, 'subscribed_tokens', set())
            new_tokens = current_tokens.union(set(tokens))
            self.market_data_worker.set_instruments(new_tokens)
            logger.info(f"Added {len(tokens)} new tokens to subscription")

    @Slot(dict)
    def _on_exit_position_requested(self, position_data: Dict[str, Any]):
        """Handles the request to exit a position using advanced order dialog."""
        symbol = position_data.get('tradingsymbol')
        if not symbol:
            logger.warning("Exit requested for position with no symbol.")
            return

        quantity = abs(position_data.get('quantity', 0))
        if quantity == 0:
            logger.warning("Exit requested for position with zero quantity.")
            return

        # Get current LTP for the position
        ltp = self._get_fresh_ltp(symbol)

        # Create exit order data
        exit_order = {
            "tradingsymbol": symbol,
            "quantity": quantity,
            "transaction_type": "SELL" if position_data.get('quantity', 0) > 0 else "BUY",
            "order_type": "MARKET",
            "product": position_data.get("product", "NRML"),
            "ltp": ltp
        }

        # Show order dialog with pre-filled exit details
        dialog = OrderDialog(self, symbol, ltp, exit_order)

        # Set the correct transaction type in the dialog
        dialog.toggle_switch.set_buy_mode(exit_order['transaction_type'] == 'BUY')
        dialog.quantity_spinbox.setValue(quantity)

        dialog.order_placed.connect(self._handle_order_placement)
        dialog.show()

    # Dialog methods
    def _show_settings_dialog(self):
        dialog = SettingsDialog(self)
        dialog.exec()

    def _show_order_history_dialog(self):
        if self.order_manager:
            # Show advanced order history
            orders = self.order_manager.get_completed_orders(days=30)
        else:
            # Fallback to trade logger
            orders = self.trade_logger.get_all_orders(limit=100)

        dialog = OrderHistoryDialog(self)
        dialog.update_orders(orders)
        dialog.exec()

    def _show_pnl_history_dialog(self):
        dialog = PnlHistoryDialog(self.trading_mode, self)
        dialog.exec()

    def _show_performance_dialog(self):
        """Enhanced performance metrics."""
        # Use enhanced trade logger for comprehensive metrics
        metrics = self.trade_logger.calculate_performance_metrics(days=30)

        dialog = PerformanceDialog(self)
        dialog.update_metrics(metrics)
        dialog.exec()

    def _show_add_alert_dialog(self):
        dialog = StockAlertDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            alert_data = dialog.get_data()
            alert_data['triggered'] = False
            self.alerts.append(alert_data)
            self._save_json("user_data/alerts.json", self.alerts)
            self._subscribe_to_tokens(self._get_alert_tokens())

    def _show_alert_logs_dialog(self):
        self.header_toolbar.set_alert_active(False)
        dialog = AlertLogsDialog(self.triggered_alerts, self)
        dialog.exec()

    # ======================
    # ENHANCED CONTEXT MENUS
    # ======================

    def _show_advanced_buy_order(self, symbol: str):
        """Show advanced buy order dialog."""
        order_details = {
            "tradingsymbol": symbol,
            "transaction_type": "BUY",
            "quantity": 1
        }
        self._show_advanced_order_dialog(order_details['tradingsymbol'], 0.0) # Pass 0.0 for LTP

    def _show_advanced_sell_order(self, symbol: str):
        """Show advanced sell order dialog."""
        order_details = {
            "tradingsymbol": symbol,
            "transaction_type": "SELL",
            "quantity": 1
        }
        self._show_advanced_order_dialog(order_details['tradingsymbol'], 0.0) # Pass 0.0 for LTP

    def _show_bracket_order(self, symbol: str):
        """Show bracket order dialog."""
        ltp = self._get_fresh_ltp(symbol)
        dialog = OrderDialog(self, symbol, ltp)

        # Switch to bracket order tab
        dialog.tab_widget.setCurrentIndex(1)

        dialog.bracket_order_placed.connect(self._handle_bracket_order_placement)
        dialog.show()

    # ======================
    # ALERT SYSTEM METHODS
    # ======================

    def _get_alert_tokens(self) -> List[int]:
        """Returns a list of tokens for all active, untriggered alerts."""
        active_alerts = [a for a in self.alerts if not a.get('triggered')]
        return [
            self.instrument_map[alert['symbol']]['instrument_token']
            for alert in active_alerts
            if alert.get('symbol') in self.instrument_map
        ]

    def _check_alerts(self, ticks: List[Dict]):
        """Checks incoming ticks against active alerts."""
        if not self.instrument_map:
            return

        an_alert_was_triggered = False
        for tick in ticks:
            token = tick['instrument_token']
            ltp = tick.get('last_price')
            if ltp is None:
                continue

            for alert in self.alerts:
                if alert.get('triggered'):
                    continue

                alert_token = self.instrument_map.get(alert['symbol'], {}).get('instrument_token')
                if alert_token == token:
                    price_threshold = float(alert['price'])
                    is_above = ltp >= price_threshold
                    is_below = ltp <= price_threshold

                    if (alert['condition'].startswith("Crosses Above") and is_above) or \
                            (alert['condition'].startswith("Crosses Below") and is_below):
                        alert['triggered'] = True
                        self._trigger_alert_actions(alert, ltp)
                        an_alert_was_triggered = True

        if an_alert_was_triggered:
            self._save_json("user_data/alerts.json", self.alerts)

    def _trigger_alert_actions(self, alert_data: Dict, trigger_price: float):
        """Handles all actions for a triggered alert."""
        self.alert_sound.play()
        self.header_toolbar.set_alert_active(True)

        triggered_entry = {
            "symbol": alert_data['symbol'],
            "price": trigger_price,
            "note": alert_data.get('note', ''),
            "condition": alert_data['condition'].replace("Crosses", "Crossed"),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self.triggered_alerts.append(triggered_entry)
        self._save_json("user_data/alert_history.json", self.triggered_alerts, backup=True)
        logger.info(f"Alert Triggered: {triggered_entry}")

    # ======================
    # THEME APPLICATION
    # ======================

    def _apply_dark_theme(self):
        """Applies professional dark theme to the frameless application."""
        self.setStyleSheet("""
            /* Main Container */
            #mainContainer {
                background-color: #0a0a0a;
                border: 1px solid #1a1a1a;
            }

            /* Custom Title Bar */
            #customTitleBar {
                background-color: #0a0a0a;
                border-bottom: 1px solid #202020;
            }

            #appTitle {
                color: #a0c0ff;
                font-size: 12px;
                font-weight: 600;
                font-family: "Segoe UI", Arial, sans-serif;
            }

            #tradingModeLabel {
                color: #64ffda;
                font-size: 10px;
                font-weight: 500;
                font-family: "Segoe UI", Arial, sans-serif;
            }

            /* Title Bar Buttons */
            #titleBarButton {
                background-color: transparent;
                color: #b0b0b0;
                border: none;
                font-size: 14px;
                font-weight: bold;
                border-radius: 2px;
            }

            #titleBarButton:hover {
                background-color: #2a2a2a;
                color: #ffffff;
            }

            #closeTitleBarButton {
                background-color: transparent;
                color: #b0b0b0;
                border: none;
                font-size: 12px;
                font-weight: bold;
                border-radius: 2px;
            }

            #closeTitleBarButton:hover {
                background-color: #e81123;
                color: #ffffff;
            }

            /* Main Window */
            QMainWindow {
                background-color: #0a0a0a;
                color: #e0e0e0;
            }

            /* Splitters */
            QSplitter {
                background-color: #0a0a0a;
                border: none;
            }

            QSplitter::handle {
                background-color: #1a1a1a;
                border: none;
            }

            QSplitter::handle:horizontal {
                width: 1px;
                margin: 0px;
                background-color: #202020;
            }

            QSplitter::handle:vertical {
                height: 1px;
                margin: 0px;
                background-color: #202020;
            }

            QSplitter::handle:hover {
                background-color: #6a9cff;
            }

            /* Remove status bar completely */
            QStatusBar {
                display: none;
            }

            /* Ensure all child widgets inherit the dark theme */
            QWidget {
                background-color: #0a0a0a;
                color: #e0e0e0;
                font-family: "Segoe UI", Arial, sans-serif;
            }

            /* Scrollbars */
            QScrollBar:vertical {
                background-color: #151515;
                width: 12px;
                border: none;
            }

            QScrollBar::handle:vertical {
                background-color: #3a3a3a;
                border-radius: 6px;
                min-height: 20px;
            }

            QScrollBar::handle:vertical:hover {
                background-color: #5a5a5a;
            }

            QScrollBar:horizontal {
                background-color: #151515;
                height: 12px;
                border: none;
            }

            QScrollBar::handle:horizontal {
                background-color: #3a3a3a;
                border-radius: 6px;
                min-width: 20px;
            }

            QScrollBar::handle:horizontal:hover {
                background-color: #5a5a5a;
            }

            QScrollBar::add-line, QScrollBar::sub-line {
                border: none;
                background: none;
            }

            /* Dialog styling */
            QDialog {
                background-color: #0a0a0a;
                color: #e0e0e0;
                border: 1px solid #202020;
            }

            /* Message boxes */
            QMessageBox {
                background-color: #0a0a0a;
                color: #e0e0e0;
            }

            QMessageBox QPushButton {
                background-color: #2a2a2a;
                color: #e0e0e0;
                border: 1px solid #3a3a3a;
                padding: 6px 12px;
                border-radius: 3px;
                min-width: 60px;
            }

            QMessageBox QPushButton:hover {
                background-color: #3a3a3a;
            }
        """)

    # ======================
    # UTILITY METHODS
    # ======================

    def _load_json(self, file_path, default=None):
        try:
            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Could not load JSON from {file_path}: {e}")
        return default if default is not None else []

    def _save_json(self, file_path, data, backup=False):
        try:
            dir_name = os.path.dirname(file_path)
            if not os.path.exists(dir_name):
                os.makedirs(dir_name)
            if backup and os.path.exists(file_path):
                shutil.copy(file_path, file_path.replace(".json", "_backup.json"))
            with open(file_path, 'w') as f:
                json.dump(data, f, indent=4)
        except IOError as e:
            logger.error(f"Could not save JSON to {file_path}: {e}")

    # ======================
    # WINDOW STATE MANAGEMENT
    # ======================

    def closeEvent(self, event):
        """Saves window state and stops background workers before closing."""
        logger.info("Close event triggered. Saving state and stopping workers...")
        self.save_window_state()

        # Stop advanced components
        if self.order_manager:
            self.order_manager.order_monitor_timer.stop()

        if hasattr(self.chartink_scanner, '_update_timer'):
            self.chartink_scanner._update_timer.stop()

        if self.market_data_worker:
            self.market_data_worker.stop()
        if self.instrument_loader and self.instrument_loader.isRunning():
            self.instrument_loader.quit()
            self.instrument_loader.wait(2000)

        logger.info("Application shut down gracefully.")
        event.accept()

    def save_window_state(self):
        """Saves window geometry and splitter states."""
        try:
            state = {
                'geometry': self.saveGeometry().toBase64().data().decode('utf-8'),
                'state': self.saveState().toBase64().data().decode('utf-8'),
                'splitter': self.main_splitter.saveState().toBase64().data().decode('utf-8'),
                'is_maximized': self._is_maximized
            }
            self.config_manager.save_window_state(state)
            logger.info("Window state saved.")
        except Exception as e:
            logger.error(f"Failed to save window state: {e}", exc_info=True)

    def restore_window_state(self):
        """Restores window geometry and splitter states from the last session."""
        try:
            state = self.config_manager.load_window_state()
            if state and state.get('geometry'):
                self.restoreGeometry(QByteArray.fromBase64(state['geometry'].encode('utf-8')))
                self.restoreState(QByteArray.fromBase64(state['state'].encode('utf-8')))
                self.main_splitter.restoreState(QByteArray.fromBase64(state['splitter'].encode('utf-8')))

                # Restore maximized state
                if state.get('is_maximized', False):
                    self._toggle_maximize()

                logger.info("Window state restored.")
            else:
                self.showMaximized()
                self._is_maximized = True
                self.max_btn.setText("❐")
        except Exception as e:
            logger.error(f"Failed to restore window state: {e}", exc_info=True)
            self.showMaximized()
            self._is_maximized = True
            self.max_btn.setText("❐")