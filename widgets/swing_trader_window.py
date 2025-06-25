import logging
import os
import json
from typing import List, Dict, Union, Any

from PySide6.QtCore import Qt, QUrl, QByteArray, QTimer, Slot
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtWidgets import QMainWindow, QSplitter, QMessageBox, QWidget, QVBoxLayout, QHBoxLayout, \
    QPushButton, QLabel
from PySide6.QtGui import QMouseEvent, QKeySequence, QShortcut

from widgets.menu_bar import create_main_menu
from tables.scanner_table import ChartinkScannerTable
from tables.positions_table import PositionsTable
from tables.watchlist_table import TabbedWatchlistWidget
from widgets.canvas_candlestick_chart import CandlestickChart as ChartWindow
from widgets.header_toolbar import HeaderToolbar

from dialogs.order_dialog import OrderDialog
from dialogs.settings_dialog import SettingsDialog
from dialogs.order_history_dialog import OrderHistoryDialog
from dialogs.pnl_history_dialog import PnlHistoryDialog
from dialogs.performance_dialog import PerformanceDialog

from dialogs.alert_management_system import AlertSystemManager

from utils.advanced_order_manager import AdvancedOrderManager
from utils.risk_management import (
    AdvancedRiskManager, PositionMonitor, TradeAnalyzer, TradingRules
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
    The main frameless window for the Swing Trader application with a professional dark theme.
    Includes advanced order management, risk management, and an enhanced alert system.
    """

    # ==============================================================================
    # INITIALIZATION AND SETUP
    # ==============================================================================

    def __init__(self, trader: Union[KiteConnect, PaperTradingManager], real_kite_client: KiteConnect, api_key: str,
                 access_token: str):
        super().__init__()

        # --- Core Application Components ---
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

        if isinstance(self.trader, PaperTradingManager):
            self.trader.set_trade_logger(self.trade_logger)

        # --- Window Dragging Variables ---
        self._drag_pos = None
        self._is_maximized = False

        # --- Advanced Components ---
        self.order_manager = None
        self.risk_manager = None
        self.position_monitor = None
        self.trade_analyzer = None
        self.trading_rules = None

        # --- Alert System & Sounds ---
        self.alert_system = None
        self.alert_sound = None
        self.success_sound = None
        self.error_sound = None
        self.order_placed_sound = None

        # --- Setup Sequence ---
        self._setup_frameless_window()
        self._setup_ui()
        self._setup_menu_bar()
        self._init_sounds()
        self._init_alert_system()
        self._init_background_workers()
        self._init_advanced_components()
        self._connect_signals()
        self._setup_watchlist_shortcuts()
        self._apply_dark_theme()
        self.restore_window_state()
        logger.info("Swing Trader Window Initialized Successfully.")

        self._startup_complete = False
        QTimer.singleShot(20000, self._mark_startup_complete)  #

    def _setup_frameless_window(self):
        """Setup frameless window with custom title bar."""
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumSize(1200, 700)

    def _setup_ui(self):
        """Initializes and arranges all UI widgets in a frameless container."""
        main_container = QWidget()
        main_container.setObjectName("mainContainer")
        self.setCentralWidget(main_container)

        main_layout = QVBoxLayout(main_container)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.title_bar = self._create_custom_title_bar()
        main_layout.addWidget(self.title_bar)

        self.header_toolbar = HeaderToolbar(self.trader, self)
        main_layout.addWidget(self.header_toolbar)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.main_splitter, 1)  # Add a stretch factor

        self.chartink_scanner = ChartinkScannerTable()
        self.candlestick_chart = ChartWindow(self.real_kite_client)
        self.watchlist = TabbedWatchlistWidget()
        self.positions_table = PositionsTable(parent=self)

        right_panel_splitter = QSplitter(Qt.Orientation.Vertical)
        right_panel_splitter.addWidget(self.watchlist)
        right_panel_splitter.addWidget(self.positions_table)
        right_panel_splitter.setStretchFactor(0, 3)
        right_panel_splitter.setStretchFactor(1, 2)

        self.main_splitter.addWidget(self.chartink_scanner)
        self.main_splitter.addWidget(self.candlestick_chart)
        self.main_splitter.addWidget(right_panel_splitter)
        self.main_splitter.setSizes([250, 800, 250])

    def _create_custom_title_bar(self) -> QWidget:
        """Creates a custom title bar for the frameless window."""
        title_bar = QWidget()
        title_bar.setObjectName("customTitleBar")
        title_bar.setFixedHeight(28)

        layout = QHBoxLayout(title_bar)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(4)

        title_label = QLabel("Kristjan Qullamaggie")
        title_label.setObjectName("appTitle")
        layout.addWidget(title_label)

        mode_label = QLabel(f"[{self.trading_mode.upper()}]")
        mode_label.setObjectName("tradingModeLabel")
        layout.addWidget(mode_label)

        layout.addStretch()

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

        title_bar.mousePressEvent = self._title_bar_mouse_press
        title_bar.mouseMoveEvent = self._title_bar_mouse_move
        title_bar.mouseDoubleClickEvent = self._title_bar_double_click
        return title_bar

    def _setup_menu_bar(self):
        """Creates a hidden menu bar accessible via shortcuts."""
        menubar, menu_actions = create_main_menu(self)
        menubar.setVisible(False)
        self.setMenuBar(menubar)

        menu_actions["refresh"].triggered.connect(self.position_manager.fetch_positions_and_orders)
        menu_actions["settings"].triggered.connect(self._show_settings_dialog)
        menu_actions["order_history"].triggered.connect(self._show_order_history_dialog)
        menu_actions["pnl_calendar"].triggered.connect(self._show_pnl_history_dialog)
        menu_actions["performance"].triggered.connect(self._show_performance_dialog)
        menu_actions["exit"].triggered.connect(self.close)

    def _init_sounds(self):
        """Initializes all sound effects for the application."""
        def create_sound(file_name):
            sound_effect = QSoundEffect(self)
            sound_file = os.path.join("assets", file_name)
            if os.path.exists(sound_file):
                sound_effect.setSource(QUrl.fromLocalFile(sound_file))
                sound_effect.setVolume(1.0)
                logger.info(f"Sound loaded: {file_name}")
                return sound_effect
            logger.warning(f"Sound file not found: {sound_file}")
            return None

        self.alert_sound = create_sound("alert.mp3")
        self.success_sound = create_sound("success.mp3")
        self.error_sound = create_sound("error.mp3")
        self.order_placed_sound = create_sound("placed.mp3")

    def _init_alert_system(self):
        """Initializes the advanced alert management system."""
        try:
            self.alert_system = AlertSystemManager(self)
            self.alert_system.alert_sound_requested.connect(self._play_alert_sound)
            logger.info("Advanced alert system initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize alert system: {e}")
            self.alert_system = None

    def _init_background_workers(self):
        """Initializes and starts background threads for data fetching."""
        # Create a QTimer to delay chart initialization until instruments are loaded
        self.chart_init_timer = QTimer()
        self.chart_init_timer.setSingleShot(True)
        self.chart_init_timer.timeout.connect(self._initialize_chart_after_instruments)

        self.instrument_loader = InstrumentLoader(self.real_kite_client)
        self.instrument_loader.instruments_loaded.connect(self._on_instruments_loaded)
        self.instrument_loader.error_occurred.connect(lambda e: logger.error(f"Critical error loading instruments: {e}"))
        self.instrument_loader.start()

        self.market_data_worker = MarketDataWorker(self.api_key, self.access_token)
        self.market_data_worker.data_received.connect(self._on_market_data)
        self.market_data_worker.connection_established.connect(self._on_websocket_connect)
        self.market_data_worker.start()

    def _init_advanced_components(self):
        """Initializes advanced order and risk management components."""
        try:
            self.order_manager = AdvancedOrderManager(self.trader, self.config_manager)
            self.risk_manager = AdvancedRiskManager(self.config_manager)
            self.position_monitor = PositionMonitor(self.risk_manager)
            self.trade_analyzer = TradeAnalyzer()
            self.trading_rules = TradingRules()
            logger.info("Advanced trading components initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize advanced components: {e}")
            self.order_manager = None
            self.risk_manager = None

    # ==============================================================================
    # SIGNAL CONNECTIONS
    # ==============================================================================

    def _connect_signals(self):
        """Connects all signals for seamless integration between components."""
        logger.info("Connecting component signals...")

        # --- Position Manager -> UI ---
        self.position_manager.positions_updated.connect(self.positions_table.update_positions)
        self.position_manager.refresh_completed.connect(self._on_positions_refresh_completed)
        self.position_manager.api_error_occurred.connect(self._on_api_error)
        # self.position_manager.position_closed.connect(self._on_position_closed)
        # self.position_manager.position_opened.connect(self._on_position_opened)
        self.position_manager.pnl_updated.connect(self._on_pnl_updated)
        self.position_manager.risk_alert.connect(self._on_risk_alert)
        self.position_manager.performance_update.connect(self._on_performance_update)

        # --- Positions Table -> Main Window ---
        self.positions_table.exit_position_requested.connect(self._handle_exit_position_request)
        self.positions_table.symbol_selected.connect(self.candlestick_chart.on_search)
        self.positions_table.subscribe_tokens_requested.connect(self._subscribe_to_tokens)
        self.positions_table.position_details_requested.connect(self._show_position_details)
        if self.alert_system:
            self.positions_table.add_alert_requested.connect(self._create_alert_from_position)

        # --- Chart -> Main Window & Header ---
        self.candlestick_chart.order_button_clicked.connect(self._show_advanced_order_dialog)
        self.candlestick_chart.symbol_loaded.connect(self.header_toolbar.set_current_symbol)
        if self.alert_system:
            self.candlestick_chart.alert_creation_requested.connect(self.alert_system.create_alert_from_chart)
            self.candlestick_chart.order_dialog_requested.connect(self._handle_chart_order_request)

        # --- Scanner & Watchlist -> Chart & Main Window ---
        self.chartink_scanner.symbol_selected.connect(self.candlestick_chart.on_search)
        self.watchlist.symbol_selected.connect(self.candlestick_chart.on_search)
        self.watchlist.subscribe_tokens_requested.connect(self._subscribe_to_tokens)
        self.watchlist.place_order_requested.connect(self._show_advanced_order_dialog_from_dict)
        self.watchlist.watchlist_changed.connect(self._on_watchlist_changed)
        self.watchlist.advanced_buy_order_requested.connect(self._show_advanced_buy_order)
        self.watchlist.advanced_sell_order_requested.connect(self._show_advanced_sell_order)
        self.watchlist.bracket_order_requested.connect(self._show_bracket_order)

        # --- Header Toolbar -> Main Window & Components ---
        self.header_toolbar.symbol_selected.connect(self.candlestick_chart.on_search)
        self.header_toolbar.buy_order_requested.connect(self._on_header_buy_order)
        self.header_toolbar.sell_order_requested.connect(self._on_header_sell_order)

        # --- Alert System Connections ---
        if self.alert_system:
            self.header_toolbar.add_alert_requested.connect(self.alert_system.show_quick_alert_dialog)
            self.header_toolbar.alert_manager_requested.connect(self.alert_system.show_alert_manager)
            if hasattr(self.header_toolbar, 'alert_logs_requested'):
                self.header_toolbar.alert_logs_requested.connect(self._show_alert_history)
        else:
            self.header_toolbar.add_alert_requested.connect(self._alert_system_unavailable)
            self.header_toolbar.alert_manager_requested.connect(self._alert_system_unavailable)
            logger.warning("Alert system unavailable; connected fallback handlers.")

        # --- Alert Update Timer ---
        self.alert_update_timer = QTimer(self)
        self.alert_update_timer.timeout.connect(self._update_alert_badges)
        self.alert_update_timer.start(30000)

        self._connect_advanced_signals()
        logger.info("All component signals connected successfully.")

    def _connect_advanced_signals(self):
        """Connects signals for advanced order and risk management."""
        if self.order_manager:
            self.order_manager.order_placed.connect(self._on_order_placed)
            self.order_manager.order_executed.connect(self._on_order_executed)
            self.order_manager.order_cancelled.connect(self._on_order_cancelled)
            self.order_manager.order_rejected.connect(self._on_order_rejected)
            self.order_manager.bracket_order_completed.connect(self._on_bracket_completed)
            self.order_manager.oco_triggered.connect(self._on_oco_triggered)
        if self.risk_manager:
            # self.risk_manager.risk_limit_exceeded.connect(self._handle_risk_alert)
            # self.risk_manager.position_limit_reached.connect(self._handle_position_limit_alert)
            pass
    # ==============================================================================
    # WINDOW MANAGEMENT & STATE
    # ==============================================================================

    def _title_bar_mouse_press(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _title_bar_mouse_move(self, event: QMouseEvent):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos:
            if not self.isMaximized():
                self.move(event.globalPosition().toPoint() - self._drag_pos)

    def _title_bar_double_click(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximize()

    def _toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
            self.max_btn.setText("□")
            self._is_maximized = False
        else:
            self.showMaximized()
            self.max_btn.setText("❐")
            self._is_maximized = True

    def closeEvent(self, event):
        logger.info("Close event triggered. Saving state and stopping workers...")
        self.save_window_state()
        if self.order_manager and hasattr(self.order_manager, 'stop'):
            self.order_manager.stop()
        if self.alert_system:
            self.alert_system.stop_engine()
        self.alert_update_timer.stop()
        if self.market_data_worker:
            self.market_data_worker.stop()
        if self.instrument_loader and self.instrument_loader.isRunning():
            self.instrument_loader.quit()
            self.instrument_loader.wait(2000)
        # Clean up timers
        if hasattr(self, 'chart_init_timer'):
            self.chart_init_timer.stop()
        logger.info("Application shut down gracefully.")
        event.accept()

    def save_window_state(self):
        try:
            state = {
                'geometry': self.saveGeometry().toBase64().data().decode('utf-8'),
                'splitter': self.main_splitter.saveState().toBase64().data().decode('utf-8'),
                'is_maximized': self.isMaximized()
            }
            self.config_manager.save_window_state(state)
            logger.info("Window state saved.")
        except Exception as e:
            logger.error(f"Failed to save window state: {e}", exc_info=True)

    def restore_window_state(self):
        try:
            state = self.config_manager.load_window_state()
            if state and state.get('geometry'):
                self.restoreGeometry(QByteArray.fromBase64(state['geometry'].encode('utf-8')))
                self.main_splitter.restoreState(QByteArray.fromBase64(state['splitter'].encode('utf-8')))
                if state.get('is_maximized', False):
                    self.showMaximized()
                    self.max_btn.setText("❐")
                logger.info("Window state restored.")
            else:
                self.showMaximized()
                self.max_btn.setText("❐")
        except Exception as e:
            logger.error(f"Failed to restore window state: {e}", exc_info=True)
            self.showMaximized()

    # ==============================================================================
    # CORE EVENT HANDLERS (SLOTS)
    # ==============================================================================

    @Slot(list)
    def _on_instruments_loaded(self, instruments: List[Dict]):
        logger.info(f"Successfully loaded {len(instruments)} instruments.")
        self.instrument_list = instruments
        self.instrument_map = {inst['tradingsymbol']: inst for inst in instruments if 'tradingsymbol' in inst}

        self.header_toolbar.set_instrument_data(instruments)
        self.candlestick_chart.set_instrument_list(instruments)
        self.position_manager.set_instrument_data(instruments)
        self.watchlist.set_instrument_map(self.instrument_map)

        if isinstance(self.trader, PaperTradingManager):
            self.trader.set_instrument_data(instruments)
        if self.alert_system:
            self.alert_system.set_instrument_map(self.instrument_map)

        self._on_watchlist_changed()



        # Set instruments in chart widget
        if hasattr(self, 'chart_widget') and self.chart_widget:
            self.chart_widget.set_instrument_list(instruments)

        # Set instruments in watchlist
        if hasattr(self, 'watchlist_widget') and self.watchlist_widget:
            self.watchlist_widget.set_instrument_list(instruments)

        # Delay chart initialization to ensure UI is ready
        self.chart_init_timer.start(1000)  # 1 second delay

        logger.info(f"Loaded {len(instruments)} instruments successfully.")

    def _initialize_chart_after_instruments(self):
        """Initialize chart auto-loading after instruments are ready."""
        try:
            if hasattr(self, 'chart_widget') and self.chart_widget:
                # The chart widget will automatically attempt to load the last symbol
                # since we already called set_instrument_list above
                logger.info("Chart auto-loading initiated")
            else:
                logger.warning("Chart widget not available for auto-loading")
        except Exception as e:
            logger.error(f"Error in chart auto-loading: {e}")

    # Add this method to handle cases where you want to manually load a symbol
    def load_symbol_on_chart(self, symbol: str):
        """Manually load a symbol on the chart (bypass auto-loading)."""
        try:
            if hasattr(self, 'chart_widget') and self.chart_widget:
                self.chart_widget.disable_auto_load()  # Disable auto-loading
                self.chart_widget.on_search(symbol)
                self.chart_widget.enable_auto_load()  # Re-enable for future
            else:
                logger.warning("Chart widget not available")
        except Exception as e:
            logger.error(f"Error loading symbol on chart: {e}")

    @Slot(list)
    def _on_market_data(self, ticks: List[Dict]):
        if not ticks:
            return
        try:
            self.position_manager.update_pnl_from_market_data(ticks)
            if isinstance(self.trader, PaperTradingManager):
                self.trader.update_market_data(ticks)
            self.watchlist.update_data(ticks)
            if self.alert_system:
                self.alert_system.update_market_data(ticks)
            if self.candlestick_chart:
                self.candlestick_chart.update_live_data(ticks)
            if self.risk_manager:
                positions = self.position_manager.get_all_positions()
                daily_pnl = sum(pos.pnl for pos in positions)
                self.risk_manager.update_daily_pnl(daily_pnl)
        except Exception as e:
            logger.error(f"Error processing market data: {e}")

    @Slot()
    def _on_websocket_connect(self):
        logger.info("WebSocket connected. Subscribing to all required tokens.")
        self._on_watchlist_changed()

    @Slot()
    def _on_watchlist_changed(self):
        logger.info("Watchlist changed - updating subscriptions")
        all_tokens = set()
        all_tokens.update(self.watchlist.get_all_tokens())
        all_tokens.update(self.positions_table.get_all_tokens())
        all_tokens.update(self._get_alert_tokens())

        if self.market_data_worker and all_tokens:
            self.market_data_worker.set_instruments(list(all_tokens))
            logger.info(f"Updated subscription to {len(all_tokens)} tokens.")

    @Slot(list)
    def _subscribe_to_tokens(self, tokens: List[int]):
        if not self.market_data_worker or not tokens:
            return
        try:
            current_tokens = set(getattr(self.market_data_worker, 'subscribed_tokens', []))
            new_tokens = current_tokens.union(set(tokens))
            self.market_data_worker.set_instruments(list(new_tokens))
            logger.info(f"Added {len(tokens)} new tokens to subscription (total: {len(new_tokens)})")
        except Exception as e:
            logger.error(f"Failed to subscribe to tokens: {e}")

    @Slot()
    def _on_positions_refresh_completed(self):
        logger.debug("Position refresh completed.")
        if self.risk_manager:
            positions = self.position_manager.get_all_positions()
            self.risk_manager.update_positions(positions)

    @Slot(str)
    def _on_api_error(self, error_message: str):
        """Handle API errors from position manager - log only, no dialogs during startup."""
        logger.error(f"Position Manager API Error: {error_message}")

        if hasattr(self, '_startup_complete') and self._startup_complete:
            critical_errors = ['Authentication failed', 'Network error', 'Invalid API key']
            is_critical = any(critical in error_message for critical in critical_errors)

            if is_critical:
                self._show_order_notification(f"Critical API Error: {error_message}", "error")
            else:
                self._update_status_message(f"API Warning: {error_message}")
        print(f"API Error: {error_message}")

    def _mark_startup_complete(self):
        """Mark that startup sequence is complete and enable notifications."""
        self._startup_complete = True
        self._connect_position_notifications()
        logger.info("Application startup completed successfully. Position notifications enabled.")

    def _connect_position_notifications(self):
        """Connect position notification signals after startup is complete."""
        logger.info("Connecting position notification signals...")
        self.position_manager.position_closed.connect(self._on_position_closed)
        self.position_manager.position_opened.connect(self._on_position_opened)

    # ==============================================================================
    # ADVANCED COMPONENT & POSITION EVENT HANDLERS (SLOTS)
    # ==============================================================================

    @Slot(dict)
    def _on_order_placed(self, order_data):
        symbol = order_data.get('tradingsymbol', '')
        order_id = order_data.get('order_id', '')
        message = f"Order placed for {symbol}. ID: {order_id}"
        self._show_order_notification(message, "info", sound_type='placed')
        logger.info(message)
        QTimer.singleShot(1500, self._refresh_positions_table)

    @Slot(dict)
    def _on_order_executed(self, order_data):
        symbol = order_data.get('tradingsymbol', '')
        trans_type = order_data.get('transaction_type', '')
        qty = order_data.get('quantity', 0)
        message = f"EXECUTED: {trans_type} {qty} {symbol}"
        self._show_order_notification(message, "success")
        self.trade_logger.log_order_update(order_data)
        self._refresh_positions_table()

    @Slot(dict)
    def _on_order_cancelled(self, order_data):
        symbol = order_data.get('tradingsymbol', '')
        self._show_order_notification(f"Order cancelled for {symbol}", "info")

    @Slot(dict, str)
    def _on_order_rejected(self, order_data, reason):
        symbol = order_data.get('tradingsymbol', '')
        message = f"REJECTED: Order for {symbol}. Reason: {reason}"
        self._show_order_notification(message, "error")
        order_data['status'] = 'REJECTED'
        order_data['status_message'] = reason
        self.trade_logger.log_order_update(order_data)

    @Slot(dict)
    def _on_bracket_completed(self, bracket_data):
        symbol = bracket_data.get('parent_order', {}).get('tradingsymbol', '')
        self._show_order_notification(f"Bracket order completed for {symbol}", "success")

    @Slot(dict, dict)
    def _on_oco_triggered(self, triggered_order, cancelled_order):
        symbol = triggered_order.get('tradingsymbol', '')
        self._show_order_notification(f"OCO order triggered for {symbol}", "info")

    @Slot(dict)
    def _on_position_closed(self, closure_data: dict):
        if not getattr(self, '_startup_complete', False):
            return
        symbol = closure_data.get('tradingsymbol', '')
        pnl = closure_data.get('pnl', 0.0)
        message = f"Position closed: {symbol} | P&L: ₹{pnl:,.2f}"
        notification_type = "success" if pnl >= 0 else "info"
        self._show_order_notification(message, notification_type)
        logger.info(f"Position closed notification: {symbol}, P&L: ₹{pnl:,.2f}")

    @Slot(dict)
    def _on_position_opened(self, position_data: dict):
        if not getattr(self, '_startup_complete', False):
            return
        symbol = position_data.get('tradingsymbol', '')
        quantity = position_data.get('quantity', 0)
        message = f"New position: {symbol} | Qty: {quantity}"
        self._show_order_notification(message, "info")
        logger.info(f"New position notification: {symbol}, Qty: {quantity}")

    @Slot(float, float)
    def _on_pnl_updated(self, unrealized_pnl: float, realized_pnl: float):
        if hasattr(self.header_toolbar, 'update_pnl_display'):
            self.header_toolbar.update_pnl_display(unrealized_pnl, realized_pnl)

    @Slot(str, float)
    def _on_risk_alert(self, message: str, risk_value: float):
        self._show_order_notification(f"RISK ALERT: {message}", "error", sound_type='alert')
        logger.warning(f"Risk Alert: {message} (Value: {risk_value})")

    @Slot(dict)
    def _on_performance_update(self, performance_data: dict):
        if hasattr(self.header_toolbar, 'update_performance_metrics'):
            self.header_toolbar.update_performance_metrics(performance_data)

    # ==============================================================================
    # ORDER DIALOG AND PLACEMENT
    # ==============================================================================

    @Slot(str, float)
    def _show_advanced_order_dialog(self, symbol: str, ltp_from_chart: float = 0.0):
        ltp = ltp_from_chart if ltp_from_chart > 0.0 else self._get_fresh_ltp(symbol)
        if ltp == 0.0:
            self._show_order_notification(f"Could not fetch LTP for {symbol}.", "error")
            return
        if symbol not in self.instrument_map:
            self._show_order_notification(f"Symbol {symbol} not found.", "error")
            return

        default_qty = self.config_manager.load_settings().get('default_quantity', 1)
        order_details = {'tradingsymbol': symbol, 'ltp': ltp, 'transaction_type': 'BUY', 'quantity': default_qty}

        dialog = OrderDialog(self, symbol, ltp, order_details)
        dialog.order_placed.connect(self._handle_order_placement)
        dialog.bracket_order_placed.connect(self._handle_bracket_order_placement)
        dialog.show()

    def _show_advanced_order_dialog_from_dict(self, order_data: Dict[str, Any]):
        symbol = order_data.get('tradingsymbol')
        if symbol:
            ltp = self._get_fresh_ltp(symbol)
            dialog = OrderDialog(self, symbol, ltp, order_data)
            dialog.order_placed.connect(self._handle_order_placement)
            dialog.bracket_order_placed.connect(self._handle_bracket_order_placement)
            dialog.show()

    def _on_header_buy_order(self, symbol: str):
        self._show_advanced_order_dialog(symbol)

    def _on_header_sell_order(self, symbol: str):
        ltp = self._get_fresh_ltp(symbol)
        if ltp == 0.0:
            self._show_order_notification(f"Could not fetch LTP for {symbol}.", "error")
            return

        default_qty = self.config_manager.load_settings().get('default_quantity', 1)
        order_details = {'tradingsymbol': symbol, 'ltp': ltp, 'transaction_type': 'SELL', 'quantity': default_qty}

        dialog = OrderDialog(self, symbol, ltp, order_details)
        dialog.order_placed.connect(self._handle_order_placement)
        dialog.bracket_order_placed.connect(self._handle_bracket_order_placement)
        dialog.show()

    @Slot(dict)
    def _handle_exit_position_request(self, position_data: dict):
        symbol = position_data.get('tradingsymbol', '')
        quantity = position_data.get('quantity', 0)
        if not symbol or quantity == 0:
            logger.warning("Invalid position data for exit request.")
            return

        transaction_type = "SELL" if quantity > 0 else "BUY"
        ltp = self._get_fresh_ltp(symbol)
        exit_order = {
            "tradingsymbol": symbol, "transaction_type": transaction_type, "quantity": abs(quantity),
            "order_type": "MARKET", "product": position_data.get("product", "NRML"), "ltp": ltp
        }

        dialog = OrderDialog(self, symbol, ltp, exit_order)
        dialog.order_placed.connect(self._handle_order_placement)
        dialog.show()


    @Slot(dict)
    def _handle_order_placement(self, order_data: Dict[str, Any]):
        try:
            logger.info(f"Received order request: {order_data}")
            if not self._validate_order_data(order_data):
                return

            if self.risk_manager:
                is_valid, msg = self.risk_manager.validate_order(order_data)
                if not is_valid:
                    self._show_order_notification(f"Risk validation failed: {msg}", "error")
                    return

            if self.order_manager:
                self.order_manager.place_order(order_data)
            else:
                logger.warning("Order manager not available. Cannot place order.")
                self._show_order_notification("Order placement system is offline.", "error")

        except Exception as e:
            error_msg = f"Order placement failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            self._show_order_notification(error_msg, "error")

    @Slot(dict)
    def _handle_bracket_order_placement(self, bracket_order_data: Dict[str, Any]):
        try:
            logger.info(f"Placing bracket order: {bracket_order_data}")
            if self.order_manager:
                self.order_manager.place_bracket_order(bracket_order_data)
            else:
                self._show_order_notification("Order manager not available.", "error")
        except Exception as e:
            error_msg = f"Bracket order failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            self._show_order_notification(error_msg, "error")

    # ==============================================================================
    # RISK MANAGEMENT HANDLERS
    # ==============================================================================

    def _handle_risk_alert(self, message: str, risk_value: float):
        """Handle risk alerts with logging only - no annoying popups."""
        logger.warning(f"RISK ALERT: {message} | Value: {risk_value}")

    def _handle_position_limit_alert(self, message: str, position_count: int):
        """Handle position limit alerts with logging only - no annoying popups."""
        logger.warning(f"POSITION ALERT: {message} | Count: {position_count}")

    # ==============================================================================
    # UI NOTIFICATION & REFRESH
    # ==============================================================================

    def _update_status_message(self, message: str):
        """Update status without showing the dialog."""
        if hasattr(self.header_toolbar, 'set_status_message'):
            self.header_toolbar.set_status_message(message)
        # Also log it
        logger.info(f"Status: {message}")

    # Alternative approach - modify _show_order_notification to be less intrusive during startup:
    def _show_order_notification(self, message: str, notification_type: str = "info", sound_type: str = None,
                                 silent_during_startup: bool = True):
        """Show notification with option to suppress during startup."""

        # Check if we should suppress during startup
        if silent_during_startup and not getattr(self, '_startup_complete', True):
            logger.info(f"Suppressed startup notification: {message}")
            return

        # Play sound regardless
        if sound_type is None:
            sound_type = notification_type

        if sound_type == "success" and self.success_sound:
            self.success_sound.play()
        elif sound_type == "error" and self.error_sound:
            self.error_sound.play()
        elif sound_type == "placed" and self.order_placed_sound:
            self.order_placed_sound.play()
        elif sound_type == "alert" and self.alert_sound:
            self.alert_sound.play()

        # Rest of the existing notification code...
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Notification")
        msg_box.setText(message)

        if notification_type == "success":
            msg_box.setIcon(QMessageBox.Icon.Information)
            msg_box.setStyleSheet("QMessageBox { background-color: #0a0a0a; color: #00b894; }")
        elif notification_type == "error":
            msg_box.setIcon(QMessageBox.Icon.Critical)
            msg_box.setStyleSheet("QMessageBox { background-color: #0a0a0a; color: #d63031; }")
        else:  # info
            msg_box.setIcon(QMessageBox.Icon.Information)
            msg_box.setStyleSheet("QMessageBox { background-color: #0a0a0a; color: #6a9cff; }")

        if notification_type != "error":
            QTimer.singleShot(4000, msg_box.accept)

        msg_box.exec()
    def _refresh_positions_table(self):
        logger.debug("Requesting position and order refresh...")
        self.position_manager.fetch_positions_and_orders()

    # ==============================================================================
    # ALERT SYSTEM METHODS
    # ==============================================================================

    @Slot()
    def _play_alert_sound(self):
        if self.alert_sound:
            self.alert_sound.play()

    @Slot()
    def _update_alert_badges(self):
        if self.alert_system and hasattr(self.header_toolbar, 'update_alert_counts'):
            try:
                active, triggered = self.alert_system.get_notification_counts()
                self.header_toolbar.update_alert_counts(active, triggered)
            except Exception as e:
                logger.debug(f"Error updating alert badges: {e}")

    def _get_alert_tokens(self) -> List[int]:
        return self.alert_system.get_active_alert_tokens() if self.alert_system else []

    @Slot(str, float)
    def _create_alert_from_position(self, symbol: str, price: float):
        if self.alert_system:
            self.alert_system.create_alert_from_chart(symbol, price)
        else:
            self._alert_system_unavailable()

    def _alert_system_unavailable(self):
        self._show_order_notification("Alert system is unavailable. Please check logs.", "error")

    @Slot(str)
    def _handle_chart_order_request(self, order_data_json: str):
        try:
            order_data = json.loads(order_data_json)
            symbol, price = order_data.get('symbol', ''), order_data.get('price', 0.0)
            if symbol and price > 0:
                self._show_advanced_order_dialog(symbol, price)
        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"Error processing chart order request: {e}")

    # ==============================================================================
    # DIALOG SHOW METHODS
    # ==============================================================================

    def _show_settings_dialog(self):
        dialog = SettingsDialog(self)
        dialog.exec()

    def _show_order_history_dialog(self):
        orders = self.order_manager.get_completed_orders() if self.order_manager else []
        dialog = OrderHistoryDialog(self)
        dialog.update_orders(orders)
        dialog.exec()

    def _show_pnl_history_dialog(self):
        dialog = PnlHistoryDialog(self.trading_mode, self)
        dialog.exec()

    def _show_performance_dialog(self):
        metrics = self.trade_logger.calculate_performance_metrics()
        dialog = PerformanceDialog(self)
        dialog.update_metrics(metrics)
        dialog.exec()

    def _show_alert_history(self):
        if self.alert_system:
            self.alert_system.show_alert_manager(history_tab=True)
        else:
            self._alert_system_unavailable()

    @Slot(str)
    def _show_position_details(self, symbol: str):
        position = self.position_manager.get_position_by_symbol(symbol)
        if not position:
            self._show_order_notification(f"Position not found for {symbol}", "error")
            return
        details = f"""<b>Position Details: {symbol}</b><br>
                    Quantity: {position.quantity}<br>
                    Avg Price: ₹{position.average_price:.2f}<br>
                    LTP: ₹{position.ltp:.2f}<br>
                    P&L: ₹{position.pnl:,.2f}<br>
                    Invested: ₹{getattr(position, 'investment', 0):,.2f}<br>
                    Product: {position.product}"""
        QMessageBox.information(self, f"Details - {symbol}", details)

    # ==============================================================================
    # CONTEXT MENU HANDLERS
    # ==============================================================================

    def _show_advanced_buy_order(self, symbol: str):
        self._show_advanced_order_dialog(symbol)

    def _show_advanced_sell_order(self, symbol: str):
        ltp = self._get_fresh_ltp(symbol)
        order_details = {'transaction_type': 'SELL'}
        dialog = OrderDialog(self, symbol, ltp, order_details)
        dialog.order_placed.connect(self._handle_order_placement)
        dialog.show()

    def _show_bracket_order(self, symbol: str):
        ltp = self._get_fresh_ltp(symbol)
        dialog = OrderDialog(self, symbol, ltp)
        if hasattr(dialog, 'tab_widget'):
            dialog.tab_widget.setCurrentIndex(1)  # Switch to bracket tab
        dialog.bracket_order_placed.connect(self._handle_bracket_order_placement)
        dialog.show()

    # ==============================================================================
    # UTILITY & HELPER METHODS
    # ==============================================================================

    def _get_fresh_ltp(self, symbol: str) -> float:
        ltp = 0.0
        # Check all watchlist tables for the symbol
        for table in self.watchlist._tables.values():
            if hasattr(table, '_watchlist_data') and symbol in table._watchlist_data:
                ltp = table._watchlist_data[symbol].get('ltp', 0.0)
                if ltp > 0: return ltp

        if symbol in self.instrument_map:
            ltp = self.instrument_map[symbol].get('last_price', 0)
            if ltp > 0: return ltp

        try:
            if self.real_kite_client:
                exchange = self.instrument_map.get(symbol, {}).get('exchange', 'NSE')
                quote = self.real_kite_client.quote([f"{exchange}:{symbol}"])
                ltp = quote[f"{exchange}:{symbol}"].get('last_price', 0)
                return ltp
        except Exception as e:
            logger.warning(f"Failed to fetch backup LTP for {symbol} via API: {e}")
        return ltp

    def _validate_order_data(self, order_data: Dict[str, Any]) -> bool:
        required = ['tradingsymbol', 'transaction_type', 'quantity', 'order_type']
        for field in required:
            if field not in order_data:
                self._show_order_notification(f"Missing order field: {field}", "error")
                return False
        if not isinstance(order_data.get('quantity'), (int, float)) or order_data['quantity'] <= 0:
            self._show_order_notification("Quantity must be a positive number.", "error")
            return False
        if order_data['tradingsymbol'] not in self.instrument_map:
            self._show_order_notification(f"Symbol {order_data['tradingsymbol']} not found", "error")
            return False
        return True

    def _setup_watchlist_shortcuts(self):
        """Setup watchlist shortcuts and global navigation shortcuts."""
        # Existing watchlist shortcuts
        shortcut_map = {"Ctrl+Shift+1": "Breakouts", "Ctrl+Shift+2": "EP", "Ctrl+Shift+3": "Parabolic"}
        for key, category in shortcut_map.items():
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(lambda cat=category: self._add_symbol_to_watchlist_from_chart(cat))

        #Global navigation shortcuts
        self._setup_global_shortcuts()

        logger.info("Watchlist shortcuts and global navigation initialized.")
    def _add_symbol_to_watchlist_from_chart(self, category: str):
        current_symbol = getattr(self.candlestick_chart, 'current_symbol', None)
        if not current_symbol:
            self._show_order_notification("No symbol is displayed on the chart.", "info")
            return
        if self.watchlist.add_symbol(current_symbol, category):
            self._show_order_notification(f"Added '{current_symbol}' to '{category}' watchlist.", "success")
        else:
            self._show_order_notification(f"'{current_symbol}' may already be in '{category}'.", "info")

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            #mainContainer { background-color: #0a0a0a; border: 1px solid #1a1a1a; }
            #customTitleBar { background-color: #0a0a0a; border-bottom: 1px solid #202020; }
            #appTitle { color: #a0c0ff; font-size: 12px; font-weight: 600; }
            #tradingModeLabel { color: #64ffda; font-size: 10px; font-weight: 500; }
            #titleBarButton { background-color: transparent; color: #b0b0b0; border: none; font-size: 14px; font-weight: bold; border-radius: 2px; }
            #titleBarButton:hover { background-color: #2a2a2a; color: #ffffff; }
            #closeTitleBarButton { background-color: transparent; color: #b0b0b0; border: none; font-size: 12px; font-weight: bold; border-radius: 2px; }
            #closeTitleBarButton:hover { background-color: #e81123; color: #ffffff; }
            QMainWindow, QWidget { background-color: #0a0a0a; color: #e0e0e0; font-family: "Segoe UI", Arial, sans-serif; }
            QSplitter::handle { background-color: #1a1a1a; }
            QSplitter::handle:horizontal { width: 1px; }
            QSplitter::handle:vertical { height: 1px; }
            QSplitter::handle:hover { background-color: #6a9cff; }
            QScrollBar:vertical { background-color: #151515; width: 12px; border: none; }
            QScrollBar::handle:vertical { background-color: #3a3a3a; border-radius: 6px; min-height: 20px; }
            QScrollBar::handle:vertical:hover { background-color: #5a5a5a; }
            QScrollBar:horizontal { background-color: #151515; height: 12px; border: none; }
            QScrollBar::handle:horizontal { background-color: #3a3a3a; border-radius: 6px; min-width: 20px; }
            QScrollBar::handle:horizontal:hover { background-color: #5a5a5a; }
            QDialog { background-color: #121212; border: 1px solid #282828; }
            QMessageBox { background-color: #121212; }
            QMessageBox QPushButton { background-color: #2a2a2a; color: #e0e0e0; border: 1px solid #3a3a3a; padding: 6px 12px; border-radius: 3px; min-width: 60px; }
            QMessageBox QPushButton:hover { background-color: #3a3a3a; }
        """)

    # Add this to swing_trader_window.py after the _setup_watchlist_shortcuts method

    def _setup_global_shortcuts(self):
        """Setup global shortcuts that work based on focused widget context."""
        from PySide6.QtGui import QShortcut, QKeySequence

        # Global spacebar shortcut for symbol navigation
        self.spacebar_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self.spacebar_shortcut.activated.connect(self._handle_global_spacebar)

        # Global Shift+Spacebar for reverse navigation
        self.shift_spacebar_shortcut = QShortcut(QKeySequence("Shift+Space"), self)
        self.shift_spacebar_shortcut.activated.connect(self._handle_global_shift_spacebar)

        logger.info("Global navigation shortcuts initialized")

    def _handle_global_spacebar(self):
        """Handle spacebar press based on currently focused widget."""
        focused_widget = self.focusWidget()

        # Check if scanner table has focus
        if self._is_scanner_focused(focused_widget):
            if hasattr(self.chartink_scanner, '_next_symbol'):
                self.chartink_scanner._next_symbol()
                return

        # Check if any watchlist table has focus
        watchlist_table = self._get_focused_watchlist_table(focused_widget)
        if watchlist_table:
            self._navigate_watchlist_symbols(watchlist_table, direction='next')
            return

        # Check if positions table has focus
        if self._is_positions_focused(focused_widget):
            self._navigate_position_symbols(direction='next')
            return

        # Fallback to scanner if no specific table is focused
        if hasattr(self.chartink_scanner, '_next_symbol'):
            self.chartink_scanner._next_symbol()

    def _handle_global_shift_spacebar(self):
        """Handle Shift+spacebar press based on currently focused widget."""
        focused_widget = self.focusWidget()

        # Check if scanner table has focus
        if self._is_scanner_focused(focused_widget):
            if hasattr(self.chartink_scanner, '_previous_symbol'):
                self.chartink_scanner._previous_symbol()
                return

        # Check if any watchlist table has focus
        watchlist_table = self._get_focused_watchlist_table(focused_widget)
        if watchlist_table:
            self._navigate_watchlist_symbols(watchlist_table, direction='previous')
            return

        # Check if positions table has focus
        if self._is_positions_focused(focused_widget):
            self._navigate_position_symbols(direction='previous')
            return

        # Fallback to scanner if no specific table is focused
        if hasattr(self.chartink_scanner, '_previous_symbol'):
            self.chartink_scanner._previous_symbol()

    def _is_scanner_focused(self, widget) -> bool:
        """Check if scanner table or its children have focus."""
        if not widget:
            return False

        # Walk up the parent hierarchy to check if we're in scanner
        current = widget
        while current:
            if current == self.chartink_scanner:
                return True
            if hasattr(current, 'objectName') and 'scanner' in current.objectName().lower():
                return True
            current = current.parent()
        return False

    def _get_focused_watchlist_table(self, widget):
        """Get the specific watchlist table that has focus."""
        if not widget:
            return None

        # Walk up the parent hierarchy to check if we're in watchlist
        current = widget
        while current:
            if current == self.watchlist:
                # Now find which specific table has focus
                for category, table in self.watchlist._tables.items():
                    if table == widget or self._is_child_of_widget(widget, table):
                        return table
                return None
            current = current.parent()
        return None

    def _is_positions_focused(self, widget) -> bool:
        """Check if positions table or its children have focus."""
        if not widget:
            return False

        # Walk up the parent hierarchy to check if we're in positions
        current = widget
        while current:
            if current == self.positions_table:
                return True
            if hasattr(current, 'table') and current.table == widget:
                return True
            current = current.parent()
        return False

    def _is_child_of_widget(self, child, parent) -> bool:
        """Check if child widget is a descendant of parent widget."""
        if not child or not parent:
            return False

        current = child
        while current:
            if current == parent:
                return True
            current = current.parent()
        return False

    def _navigate_watchlist_symbols(self, table, direction='next'):
        """Navigate symbols in a specific watchlist table."""
        if not table or not hasattr(table, '_watchlist_symbols'):
            return

        symbols = list(table._watchlist_symbols)
        if not symbols:
            return

        # Get current selection or start from beginning
        current_row = table.currentRow()
        if current_row == -1:
            current_row = 0

        # Calculate next row
        if direction == 'next':
            next_row = (current_row + 1) % len(symbols)
        else:  # previous
            next_row = (current_row - 1) % len(symbols)

        # Select the row and emit symbol
        table.selectRow(next_row)
        table.setCurrentCell(next_row, 0)

        # Get symbol and emit selection
        try:
            symbol_item = table.item(next_row, 0)
            if symbol_item:
                symbol = symbol_item.text()
                if symbol and symbol != 'N/A':
                    table.symbol_selected.emit(symbol)
                    logger.debug(f"Watchlist navigation: Selected {symbol} at row {next_row}")
        except Exception as e:
            logger.warning(f"Error navigating watchlist symbols: {e}")

    def _navigate_position_symbols(self, direction='next'):
        """Navigate symbols in positions table."""
        if not hasattr(self.positions_table, 'table'):
            return

        table = self.positions_table.table
        row_count = table.rowCount()
        if row_count == 0:
            return

        # Get current selection or start from beginning
        current_row = table.currentRow()
        if current_row == -1:
            current_row = 0

        # Calculate next row
        if direction == 'next':
            next_row = (current_row + 1) % row_count
        else:  # previous
            next_row = (current_row - 1) % row_count

        # Select the row and emit symbol
        table.selectRow(next_row)
        table.setCurrentCell(next_row, 0)

        # Get symbol and emit selection
        try:
            symbol_item = table.item(next_row, 0)
            if symbol_item:
                symbol = symbol_item.text()
                if symbol and symbol != 'N/A':
                    self.positions_table.symbol_selected.emit(symbol)
                    logger.debug(f"Positions navigation: Selected {symbol} at row {next_row}")
        except Exception as e:
            logger.warning(f"Error navigating position symbols: {e}")

