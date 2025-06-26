import logging
import os
import json
from datetime import datetime
from typing import List, Dict, Union, Any, Optional

from PySide6.QtCore import Qt, QUrl, QByteArray, QTimer, Slot, Signal
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtWidgets import QMainWindow, QSplitter, QMessageBox, QWidget, QVBoxLayout, QHBoxLayout, \
    QPushButton, QLabel
from PySide6.QtGui import QMouseEvent, QKeySequence, QShortcut

from tables.scanner_table import ChartinkScannerTable
from tables.positions_table import PositionsTable
from tables.watchlist_table import TabbedWatchlistWidget
from widgets.canvas_candlestick_chart import CandlestickChart as ChartWindow
from widgets.header_toolbar import HeaderToolbar

from dialogs.order_dialog import OrderDialog
from dialogs.order_history_dialog import OrderHistoryDialog
from dialogs.performance_dialog import PerformanceDialog
from dialogs.order_status_dialog import create_order_status_dialog
from dialogs.alert_management_system import AlertSystemManager
from dialogs.notification_dialog import NotificationType, setup_notification_system

from utils.advanced_order_manager import AdvancedOrderManager
from utils.market_data_worker import MarketDataWorker
from utils.paper_trading_manager import PaperTradingManager
from utils.position_manager import PositionManager
from utils.config_manager import ConfigManager
from utils.instrument_loader import InstrumentLoader
from utils.trade_logger import TradeLogger
from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)


class SwingTraderWindow(QMainWindow):
    """
    The main frameless window for the Swing Trader application with a professional dark theme.
    Includes advanced order management and an enhanced alert system.
    """
    trade_completed = Signal()

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
        self.trading_mode = 'paper' if isinstance(trader, PaperTradingManager) else 'live'
        self.trade_logger = TradeLogger(mode=self.trading_mode)
        self.position_manager = PositionManager(self.trader, self.trade_logger)
        self.position_manager.set_main_window_reference(self)

        self.instrument_list: List[Dict] = []
        self.instrument_map: Dict[str, Dict] = {}
        self._subscribed_tokens = set()
        self.notification_manager = setup_notification_system(self)

        if isinstance(self.trader, PaperTradingManager):
            self.trader.set_trade_logger(self.trade_logger)

        # --- Window Dragging Variables ---
        self._drag_pos = None
        self._is_maximized = False
        self.order_history_dialog = None
        self.performance_dialog = None

        # --- Advanced Components ---
        self.order_manager = None
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
        self._init_sounds()
        self._init_alert_system()
        self._init_background_workers()
        self._init_advanced_components()
        self._connect_signals()
        self._connect_chart_signals()

        self._setup_watchlist_shortcuts()
        self._apply_dark_theme()
        self.restore_window_state()
        logger.info("Swing Trader Window Initialized Successfully.")

        self._startup_complete = False
        QTimer.singleShot(20000, self._mark_startup_complete)

    def _setup_frameless_window(self):
        """Setup frameless window with custom title bar."""
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumSize(1200, 700)

    def _setup_ui(self):
        """Initializes and arranges all UI widgets in a frameless container with FIXED splitter behavior."""
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

        # FIXED: Create main splitter with proper configuration
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.main_splitter, 1)

        # Create components
        self.chartink_scanner = ChartinkScannerTable()
        self.candlestick_chart = ChartWindow(self.real_kite_client)
        self.watchlist = TabbedWatchlistWidget()
        self.positions_table = PositionsTable(parent=self)

        # FIXED: Create right panel splitter with stable configuration
        right_panel_splitter = QSplitter(Qt.Orientation.Vertical)
        right_panel_splitter.setObjectName("rightPanelSplitter")

        # CRITICAL: Add widgets in correct order
        right_panel_splitter.addWidget(self.watchlist)
        right_panel_splitter.addWidget(self.positions_table)

        # FIXED: Configure right panel splitter behavior
        right_panel_splitter.setStretchFactor(0, 3)  # Watchlist gets 60%
        right_panel_splitter.setStretchFactor(1, 2)  # Positions gets 40%
        right_panel_splitter.setChildrenCollapsible(False)
        right_panel_splitter.setHandleWidth(4)

        # CRITICAL: Set minimum sizes to prevent unwanted resizing
        self.watchlist.setMinimumHeight(150)
        self.positions_table.setMinimumHeight(100)

        # Add all widgets to main splitter
        self.main_splitter.addWidget(self.chartink_scanner)
        self.main_splitter.addWidget(self.candlestick_chart)
        self.main_splitter.addWidget(right_panel_splitter)

        # FIXED: Configure main splitter behavior
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(4)

        # CRITICAL: Set minimum sizes for main panels
        self.chartink_scanner.setMinimumWidth(200)
        self.candlestick_chart.setMinimumWidth(400)
        right_panel_splitter.setMinimumWidth(250)

        # FIXED: Set proper stretch factors for main splitter
        self.main_splitter.setStretchFactor(0, 0)  # Scanner: fixed size
        self.main_splitter.setStretchFactor(1, 1)  # Chart: stretches with window
        self.main_splitter.setStretchFactor(2, 0)  # Right panel: fixed size

        # Set initial sizes AFTER configuration
        self.main_splitter.setSizes([250, 600, 300])

        # Store reference for state saving
        self.right_panel_splitter = right_panel_splitter

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

    def _init_sounds(self):
        """Initializes all sound effects for the application."""

        def create_sound(file_name):
            sound_file = os.path.join("assets", file_name)
            if not os.path.exists(sound_file):
                logger.warning(f"Sound file not found: {sound_file}. Please ensure the 'assets' directory is present.")
                return None

            sound_effect = QSoundEffect(self)
            sound_effect.setSource(QUrl.fromLocalFile(sound_file))
            sound_effect.setVolume(1.0)  # You can make this configurable
            logger.info(f"Sound loaded: {sound_file}")
            return sound_effect

        self.alert_sound = create_sound("alert.mp3")
        self.success_sound = create_sound("success.mp3")
        self.error_sound = create_sound("error.mp3")
        self.order_placed_sound = create_sound("placed.mp3")

    def _init_alert_system(self):
        try:
            self.alert_system = AlertSystemManager(self)
            self.alert_system.alert_sound_requested.connect(self._play_alert_sound)
            self.alert_system.engine_status_changed.connect(self._on_alert_engine_status)
            logger.info("Advanced alert system initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize alert system: {e}")
            self.alert_system = None

    @Slot(str)
    def _on_alert_engine_status(self, status: str):
        if status == "error":
            logger.warning("Alert engine encountered an error")
        elif status == "running":
            logger.info("Alert engine is running normally")

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

        # Enhanced market data worker with chart-specific handling
        self.market_data_worker = MarketDataWorker(self.api_key, self.access_token)
        self.market_data_worker.data_received.connect(self._on_market_data)
        self.market_data_worker.connection_established.connect(self._on_websocket_connect)
        self.market_data_worker.start()

    @Slot()
    def _on_websocket_connect(self):
        """Enhanced WebSocket connection handler with immediate chart subscription"""
        logger.info("WebSocket connected. Setting up enhanced subscriptions.")

        # Immediate chart subscription if available
        if (hasattr(self, 'candlestick_chart') and
                hasattr(self.candlestick_chart, 'current_instrument_token') and
                self.candlestick_chart.current_instrument_token):

            try:
                self.market_data_worker.add_instruments([self.candlestick_chart.current_instrument_token])
                logger.info(
                    f"Immediately subscribed to chart token on connection: {self.candlestick_chart.current_instrument_token}")
            except Exception as e:
                logger.error(f"Failed to immediately subscribe to chart on connection: {e}")

        # Then handle other subscriptions
        self._on_watchlist_changed()

    def _connect_chart_signals(self):
        """Connect chart-specific signals for live updates"""
        if self.candlestick_chart:
            # Connect symbol change to subscription update
            self.candlestick_chart.symbol_loaded.connect(self._on_chart_symbol_changed)

            # Connect chart data requests
            self.candlestick_chart.data_request_for_symbol.connect(self._ensure_chart_subscription)

    @Slot(str)
    def _on_chart_symbol_changed(self, symbol: str):
        """Handle chart symbol changes with immediate subscription"""
        logger.info(f"Chart symbol changed to: {symbol}")

        if symbol in self.instrument_map:
            token = self.instrument_map[symbol]['instrument_token']
            try:
                # Add to subscription immediately
                if self.market_data_worker and self.market_data_worker.is_connected():
                    self.market_data_worker.add_instruments([token])
                    logger.info(f"Added chart symbol {symbol} (token: {token}) to subscription")
                else:
                    logger.warning("Market data worker not connected, will subscribe when connected")

                # Update all subscriptions to include chart
                QTimer.singleShot(100, self._on_watchlist_changed)

            except Exception as e:
                logger.error(f"Failed to subscribe to chart symbol {symbol}: {e}")

    @Slot(str)
    def _ensure_chart_subscription(self, symbol: str):
        """Ensure chart symbol is subscribed to market data"""
        if symbol in self.instrument_map:
            token = self.instrument_map[symbol]['instrument_token']
            try:
                if self.market_data_worker:
                    current_info = self.market_data_worker.get_subscription_info()
                    if token not in current_info.get('subscribed_tokens', []):
                        self.market_data_worker.add_instruments([token])
                        logger.info(f"Ensured subscription for chart symbol {symbol}")
            except Exception as e:
                logger.error(f"Failed to ensure chart subscription for {symbol}: {e}")



    def _update_chart_with_ticks(self, ticks: List[Dict]):
        """Dedicated method for chart tick processing"""
        if not self.candlestick_chart:
            return

        current_symbol = getattr(self.candlestick_chart, 'current_symbol', None)
        current_token = getattr(self.candlestick_chart, 'current_instrument_token', None)

        if not current_symbol or not current_token:
            return

        # Find relevant ticks for chart
        chart_ticks = []
        for tick in ticks:
            tick_token = tick.get('instrument_token')
            tick_symbol = tick.get('tradingsymbol')

            # Direct token match (most reliable)
            if tick_token == current_token:
                # Ensure tradingsymbol is set for chart processing
                if not tick_symbol:
                    tick['tradingsymbol'] = current_symbol
                chart_ticks.append(tick)
                continue

            # Symbol match as backup
            if tick_symbol == current_symbol:
                chart_ticks.append(tick)

        # Update chart immediately if we have relevant ticks
        if chart_ticks:
            try:
                self.candlestick_chart.update_live_data(chart_ticks)
                logger.debug(f"Updated chart with {len(chart_ticks)} ticks for {current_symbol}")
            except Exception as e:
                logger.error(f"Error updating chart with ticks: {e}")

    def _init_advanced_components(self):
        """Initializes advanced order management components."""
        try:
            self.order_manager = AdvancedOrderManager(self.trader, self.config_manager)

            # CRITICAL: Set the main window reference in position manager
            if hasattr(self, 'position_manager'):
                self.position_manager.set_main_window_reference(self)
                logger.info("Set main window reference in position manager")

            logger.info("Advanced trading components initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize advanced components: {e}")
            self.order_manager = None

    # ==============================================================================
    # SIGNAL CONNECTIONS
    # ==============================================================================

    def _connect_signals(self):
        """Connects all signals for seamless integration between components."""
        logger.info("Connecting component signals...")

        # --- Position Manager -> UI ---
        self.position_manager.positions_updated.connect(self.positions_table.update_positions)
        self.position_manager.api_error_occurred.connect(self._on_api_error)
        self.position_manager.position_closed.connect(self._on_position_closed)
        self.position_manager.position_opened.connect(self._on_position_opened)
        self.position_manager.pnl_updated.connect(self._on_pnl_updated)
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
        self.header_toolbar.order_history_requested.connect(self._show_order_history_dialog)
        self.header_toolbar.performance_dashboard_requested.connect(self._show_performance_dialog)

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
        """Connects signals for advanced order management."""
        if self.order_manager:
            self.order_manager.order_placed.connect(self._on_order_placed)
            self.order_manager.order_executed.connect(self._on_order_executed)
            self.order_manager.order_cancelled.connect(self._on_order_cancelled)
            self.order_manager.order_rejected.connect(self._on_order_rejected)
            self.order_manager.bracket_order_completed.connect(self._on_bracket_completed)
            self.order_manager.oco_triggered.connect(self._on_oco_triggered)

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
        if self.instrument_loader and self.instrument_loader.isRunning():
            self.instrument_loader.quit()
            self.instrument_loader.wait(2000)
        if self.order_history_dialog and self.order_history_dialog.isVisible():
            self.order_history_dialog.close()
        if hasattr(self, 'chart_init_timer'):
            self.chart_init_timer.stop()
        if self.performance_dialog and self.performance_dialog.isVisible():
            self.performance_dialog.close()
        if self.market_data_worker:
            self.market_data_worker.stop()

        logger.info("Application shut down gracefully.")
        super().closeEvent(event)


    # ==============================================================================
    # CORE EVENT HANDLERS (SLOTS)
    # ==============================================================================

    def _on_instruments_loaded(self, instruments: List[Dict]):
        """Enhanced instrument loading with position manager integration."""
        logger.info(f"Successfully loaded {len(instruments)} instruments.")
        self.instrument_list = instruments
        self.instrument_map = {inst['tradingsymbol']: inst for inst in instruments if 'tradingsymbol' in inst}

        # Set instrument data in all components
        self.header_toolbar.set_instrument_data(instruments)
        self.candlestick_chart.set_instrument_list(instruments)

        # CRITICAL: Set instrument data in position manager
        if hasattr(self, 'position_manager'):
            self.position_manager.set_instrument_data(instruments)

        self.watchlist.set_instrument_map(self.instrument_map)

        if isinstance(self.trader, PaperTradingManager):
            self.trader.set_instrument_data(instruments)
        if self.alert_system:
            self.alert_system.set_instrument_map(self.instrument_map)

        # Trigger position refresh after instrument data is set
        QTimer.singleShot(1000, self._refresh_positions_table)

        self._on_watchlist_changed()
        self.chart_init_timer.start(1000)
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
            # Debug: Log incoming ticks for chart symbol
            current_chart_symbol = getattr(self.candlestick_chart, 'current_symbol', None)
            current_chart_token = getattr(self.candlestick_chart, 'current_instrument_token', None)

            # Enhanced chart update with better token-to-symbol mapping
            if self.candlestick_chart and current_chart_symbol:
                chart_ticks = []

                for tick in ticks:
                    tick_symbol = tick.get('tradingsymbol')
                    tick_token = tick.get('instrument_token')

                    # Enhanced matching logic
                    symbol_matches = tick_symbol == current_chart_symbol
                    token_matches = (tick_token == current_chart_token) if tick_token and current_chart_token else False

                    # Also check instrument map for symbol resolution
                    if not symbol_matches and tick_token and hasattr(self, 'instrument_map'):
                        for symbol, instrument in self.instrument_map.items():
                            if instrument.get('instrument_token') == tick_token and symbol == current_chart_symbol:
                                # Add tradingsymbol to tick for better matching
                                tick['tradingsymbol'] = symbol
                                symbol_matches = True
                                break

                    if symbol_matches or token_matches:
                        chart_ticks.append(tick)

                if chart_ticks:
                    logger.debug(f"Sending {len(chart_ticks)} ticks to chart for {current_chart_symbol}")
                    # Force immediate update
                    self.candlestick_chart.update_live_data(chart_ticks)
                elif current_chart_token:
                    # Debug: Check if we're receiving data for chart token
                    chart_token_ticks = [t for t in ticks if t.get('instrument_token') == current_chart_token]
                    if chart_token_ticks:
                        logger.warning(
                            f"Received {len(chart_token_ticks)} ticks for chart token {current_chart_token} but no symbol match")

            # Continue with other updates
            self.position_manager.update_pnl_from_market_data(ticks)
            if isinstance(self.trader, PaperTradingManager):
                self.trader.update_market_data(ticks)
            self.watchlist.update_data(ticks)

            if self.alert_system:
                self.alert_system.update_market_data(ticks)


        except Exception as e:
            logger.error(f"Error processing market data: {e}")

    @Slot()
    def _on_watchlist_changed(self):
        """FIXED: Enhanced watchlist change handler with position token priority"""
        logger.info("Watchlist changed - updating subscriptions with position priority")
        all_tokens = set()

        # PRIORITY 1: Position tokens (most critical)
        if hasattr(self, 'position_manager') and self.position_manager and self.position_manager._positions:
            position_tokens = []
            for symbol, position in self.position_manager._positions.items():
                token = None

                if hasattr(position, 'instrument_token') and position.instrument_token:
                    token = position.instrument_token
                elif symbol in self.instrument_map:
                    token = self.instrument_map[symbol].get('instrument_token')

                if token and token > 0:
                    position_tokens.append(token)
                    all_tokens.add(token)

            if position_tokens:
                logger.info(f"🎯 PRIORITY: Added {len(position_tokens)} position tokens")

        # PRIORITY 2: Chart token (if available)
        if (hasattr(self, 'candlestick_chart') and
                hasattr(self.candlestick_chart, 'current_instrument_token') and
                self.candlestick_chart.current_instrument_token):
            all_tokens.add(self.candlestick_chart.current_instrument_token)
            logger.info(f"📊 Added chart token: {self.candlestick_chart.current_instrument_token}")

        # PRIORITY 3: Watchlist tokens
        watchlist_tokens = self.watchlist.get_all_tokens()
        all_tokens.update(watchlist_tokens)
        logger.info(f"👁 Added {len(watchlist_tokens)} watchlist tokens")

        # PRIORITY 4: Alert tokens
        alert_tokens = self._get_alert_tokens()
        all_tokens.update(alert_tokens)

        # Subscribe to all tokens
        if self.market_data_worker and all_tokens:
            self.market_data_worker.set_instruments(list(all_tokens))
            logger.info(
                f"🚀 Updated subscription to {len(all_tokens)} tokens (positions: {len(position_tokens) if 'position_tokens' in locals() else 0})")


    @Slot(list)
    def _subscribe_to_tokens(self, tokens: List[int]):
        """Subscribe to market data tokens with duplicate prevention."""
        if not tokens:
            return

        # Filter out already subscribed tokens - CORRECTED
        new_tokens = [token for token in tokens if token not in self._subscribed_tokens]

        if not new_tokens:
            return  # No new tokens to subscribe

        try:
            if self.market_data_worker and hasattr(self.market_data_worker, 'add_instruments'):
                # Use the more robust add_instruments method from your worker
                self.market_data_worker.add_instruments(new_tokens)
                # Update the local set of subscribed tokens
                self._subscribed_tokens.update(new_tokens)
                logger.info(
                    f"Added {len(new_tokens)} new tokens to subscription (total: {len(self._subscribed_tokens)})")
            else:
                logger.warning("Market data worker not available for subscription")
        except Exception as e:
            logger.error(f"Failed to subscribe to tokens: {e}")


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
        QTimer.singleShot(1000, self._refresh_positions_table)
        QTimer.singleShot(2000, self._update_performance_metrics_in_header)
        # Refresh dashboard if open
        if self.performance_dialog and self.performance_dialog.isVisible():
            QTimer.singleShot(1500, self.performance_dialog.refresh_data)

    @Slot(dict)
    def _on_order_executed(self, order_data):
        """Legacy order executed handler - now just logs."""
        try:
            order_id = order_data.get('order_id', '')
            symbol = order_data.get('tradingsymbol', '')

            # Check if this was already processed
            if order_data.get('update_source') == 'status_dialog':
                logger.debug(f"Order {order_id} already processed by status dialog, skipping")
                return

            # Mark source and delegate to completion handler
            order_data['update_source'] = 'order_manager'
            self._on_order_completed(order_data)

        except Exception as e:
            logger.error(f"Error in legacy order executed handler: {e}")

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
        """Enhanced position closure handler."""
        if not getattr(self, '_startup_complete', False):
            return

        self._show_position_update_notification(closure_data)
        self._refresh_positions_table()

        symbol = closure_data.get('tradingsymbol', '')
        pnl = closure_data.get('pnl', 0.0)
        logger.info(f"Position closed notification: {symbol}, P&L: ₹{pnl:,.2f}")

    @Slot(dict)
    def _on_position_opened(self, position_data: dict):
        """Enhanced position opening handler."""
        if not getattr(self, '_startup_complete', False):
            return

        self._show_position_update_notification(position_data)
        self._refresh_positions_table()

        symbol = position_data.get('tradingsymbol', '')
        quantity = position_data.get('quantity', 0)
        logger.info(f"Position opened: {symbol}, Qty: {quantity}")

    @Slot(float, float)
    def _on_pnl_updated(self, unrealized_pnl: float, realized_pnl: float):
        if hasattr(self.header_toolbar, 'update_pnl_display'):
            self.header_toolbar.update_pnl_display(unrealized_pnl, realized_pnl)



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
        """Enhanced order placement handler with fixed notification logic."""
        try:
            logger.info(f"Received order request: {order_data}")
            if not self._validate_order_data(order_data):
                return

            # Place order via order manager
            if self.order_manager:
                order_id = self.order_manager.place_order(order_data)
            else:
                # Direct placement if no order manager
                if hasattr(self.trader, 'place_order'):
                    order_id = self.trader.place_order(**order_data)
                else:
                    logger.error("No order placement method available")
                    self._show_order_notification("Order placement system is offline.", "error")
                    return

            if order_id:
                # Update order_data with the returned order_id
                order_data['order_id'] = order_id
                order_data['status'] = 'PLACED'  # Initial status

                # Log order placement FIRST (before notifications)
                if hasattr(self, 'trade_logger'):
                    try:
                        self.trade_logger.log_order_placement(order_data, order_id)
                        logger.info(f"Order logged successfully: {order_id}")
                    except Exception as log_error:
                        logger.error(f"Failed to log order: {log_error}")
                        # Continue despite logging error

                # Show SUCCESS notification (fix: was showing "failed" before)
                self._show_order_placed_notification(order_data)

                # Show order status dialog for monitoring
                QTimer.singleShot(500, lambda: self.show_order_status_dialog(order_data))

                # Refresh order history if dialog is open
                if (hasattr(self, 'order_history_dialog') and
                        self.order_history_dialog and
                        self.order_history_dialog.isVisible()):
                    QTimer.singleShot(1000, self.order_history_dialog.refresh_orders)

                logger.info(f"Order placed successfully: {order_id}")
            else:
                # Only show failure if order_id is None/False
                self._show_order_notification("Order placement failed - no order ID returned", "error")

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
    # UI NOTIFICATION & REFRESH
    # ==============================================================================

    def _update_status_message(self, message: str):
        """Update status without showing the dialog."""
        if hasattr(self.header_toolbar, 'set_status_message'):
            self.header_toolbar.set_status_message(message)
        # Also log it
        logger.info(f"Status: {message}")


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

    def _show_order_history_dialog(self):
        """
        Show the order history dialog with trade logger integration.
        This method replaces or enhances your existing _show_order_history_dialog method.
        """
        try:
            # Create dialog if it doesn't exist or was closed
            if self.order_history_dialog is None or not self.order_history_dialog.isVisible():
                self.order_history_dialog = OrderHistoryDialog(
                    trade_logger=self.trade_logger,
                    parent=self
                )

                # Connect dialog signals
                self.order_history_dialog.refresh_requested.connect(self._refresh_order_history)
                self.order_history_dialog.export_requested.connect(self._export_order_history)

            # Show the dialog
            self.order_history_dialog.show()
            self.order_history_dialog.raise_()
            self.order_history_dialog.activateWindow()

            logger.info("Order history dialog opened")

        except Exception as e:
            logger.error(f"Failed to show order history dialog: {e}")
            self._show_order_notification("Failed to open order history", "error")

    def _show_performance_dialog(self):
        try:
            if self.performance_dialog is None or not self.performance_dialog.isVisible():
                self.performance_dialog = PerformanceDialog(
                    trade_logger=self.trade_logger,
                    parent=self
                )
                # Connect the signal for automatic updates
                self.trade_completed.connect(self.performance_dialog.refresh_data)  # <--- ADD THIS LINE

            self.performance_dialog.refresh_data()  # Initial refresh
            self.performance_dialog.show()
            self.performance_dialog.raise_()
            self.performance_dialog.activateWindow()
            logger.info("Performance dashboard opened")
        except Exception as e:
            logger.error(f"Failed to show performance dashboard: {e}", exc_info=True)

    def _refresh_performance_data(self):
        """Handle performance data refresh request."""
        try:
            if self.performance_dialog and self.performance_dialog.isVisible():
                self.performance_dialog.refresh_data()
                self._show_order_notification("Performance data refreshed", "info", silent_during_startup=False)
                logger.info("Performance data manually refreshed")
        except Exception as e:
            logger.error(f"Failed to refresh performance data: {e}")
            self._show_order_notification("Failed to refresh performance data", "error")

    def _export_performance_report(self, export_data: dict):
        """
        Handle performance report export request.

        Args:
            export_data: Dictionary containing performance metrics and analysis
        """
        try:
            # Create exports directory
            home = os.path.expanduser("~")
            exports_dir = os.path.join(home, ".swing_trader", "exports")
            os.makedirs(exports_dir, exist_ok=True)

            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"performance_report_{timestamp}.json"
            filepath = os.path.join(exports_dir, filename)

            # Add additional metadata
            export_data.update({
                'export_source': 'swing_trader_performance_dashboard',
                'trading_mode': self.trading_mode,
                'app_version': getattr(self, 'app_version', '1.0.0'),
                'user_id': getattr(self.header_toolbar, 'user_id', 'unknown'),
                'account_balance': getattr(self.header_toolbar, '_account_info', {}).get('available_balance', 0)
            })

            # Export to JSON file
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False, default=str)

            # Also create CSV summary for easy analysis
            csv_filename = f"performance_summary_{timestamp}.csv"
            csv_filepath = os.path.join(exports_dir, csv_filename)
            self._create_performance_csv(export_data, csv_filepath)

            # Show success notification
            self._show_order_notification(
                f"Performance report exported to {filename}",
                "success",
                silent_during_startup=False
            )

            logger.info(f"Performance report exported to: {filepath}")

            # Optionally open the exports folder
            if hasattr(self, '_open_exports_folder'):
                self._open_exports_folder(exports_dir)

        except Exception as e:
            logger.error(f"Failed to export performance report: {e}")
            self._show_order_notification("Failed to export performance report", "error")

    def _setup_performance_tracking(self):
        """
        Set up performance tracking and periodic updates.
        Call this during application initialization.
        """
        try:
            # Performance update timer (every 5 minutes when market is open)
            self.performance_update_timer = QTimer(self)
            self.performance_update_timer.timeout.connect(self._update_performance_metrics_in_header)
            self.performance_update_timer.start(300000)  # 5 minutes

            # Initial performance metrics load
            QTimer.singleShot(5000, self._update_performance_metrics_in_header)

            logger.info("Performance tracking initialized")

        except Exception as e:
            logger.error(f"Failed to setup performance tracking: {e}")
    def _create_performance_csv(self, export_data: dict, filepath: str):
        """Create a CSV summary of performance data for easy analysis."""
        try:
            import csv

            metrics = export_data.get('metrics', {})

            with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)

                # Header
                writer.writerow(['Performance Report Summary'])
                writer.writerow(['Generated:', datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
                writer.writerow(['Period:', export_data.get('period', 'Unknown')])
                writer.writerow(['Trading Mode:', export_data.get('trading_mode', 'Unknown')])
                writer.writerow([])  # Empty row

                # Key metrics
                writer.writerow(['Metric', 'Value'])
                writer.writerow(['Total P&L', f"₹{metrics.get('total_pnl', 0):,.2f}"])
                writer.writerow(['Win Rate', f"{metrics.get('win_rate', 0):.1f}%"])
                writer.writerow(['Total Trades', metrics.get('total_trades', 0)])
                writer.writerow(['Winning Trades', metrics.get('winning_trades', 0)])
                writer.writerow(['Losing Trades', metrics.get('losing_trades', 0)])
                writer.writerow(['Average Win', f"₹{metrics.get('average_win', 0):,.2f}"])
                writer.writerow(['Average Loss', f"₹{metrics.get('average_loss', 0):,.2f}"])
                writer.writerow(['Profit Factor', f"{metrics.get('profit_factor', 0):.2f}"])
                writer.writerow(['Largest Win', f"₹{metrics.get('largest_win', 0):,.2f}"])
                writer.writerow(['Largest Loss', f"₹{metrics.get('largest_loss', 0):,.2f}"])
                writer.writerow(['Max Consecutive Wins', metrics.get('max_consecutive_wins', 0)])
                writer.writerow(['Max Consecutive Losses', metrics.get('max_consecutive_losses', 0)])

            logger.info(f"Performance CSV summary created: {filepath}")

        except Exception as e:
            logger.error(f"Failed to create performance CSV: {e}")

    def _update_performance_metrics_in_header(self):
        """Update performance metrics display in the header toolbar if available."""
        try:
            if hasattr(self.header_toolbar, 'update_performance_metrics'):
                # Get latest metrics
                metrics = self.trade_logger.calculate_performance_metrics(30)  # Last 30 days

                # Create summary for header display
                performance_summary = {
                    'daily_pnl': metrics.get('total_pnl', 0),
                    'win_rate': metrics.get('win_rate', 0),
                    'total_trades': metrics.get('total_trades', 0),
                    'profit_factor': metrics.get('profit_factor', 0)
                }

                self.header_toolbar.update_performance_metrics(performance_summary)

        except Exception as e:
            logger.error(f"Failed to update header performance metrics: {e}")

    def _refresh_order_history(self):
        """Handle order history refresh request."""
        try:
            if self.order_history_dialog and self.order_history_dialog.isVisible():
                self.order_history_dialog.refresh_orders()
                self._show_order_notification("Order history refreshed", "info", silent_during_startup=False)
                logger.info("Order history manually refreshed")
        except Exception as e:
            logger.error(f"Failed to refresh order history: {e}")
            self._show_order_notification("Failed to refresh order history", "error")

    def _export_order_history(self, export_data: dict):
        """
        Handle order history export request.

        Args:
            export_data: Dictionary containing filters, statistics, orders, and metadata
        """
        try:
            # Create exports directory
            home = os.path.expanduser("~")
            exports_dir = os.path.join(home, ".swing_trader", "exports")
            os.makedirs(exports_dir, exist_ok=True)

            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"order_history_export_{timestamp}.json"
            filepath = os.path.join(exports_dir, filename)

            # Add additional metadata
            export_data.update({
                'export_source': 'swing_trader_order_history',
                'trading_mode': self.trading_mode,
                'app_version': getattr(self, 'app_version', '1.0.0'),
                'user_id': getattr(self.header_toolbar, 'user_id', 'unknown')
            })

            # Export to JSON file
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False, default=str)

            # Show success notification
            self._show_order_notification(
                f"Order history exported to {filename}",
                "success",
                silent_during_startup=False
            )

            logger.info(f"Order history exported to: {filepath}")

            # Optionally open the exports folder
            if hasattr(self, '_open_exports_folder'):
                self._open_exports_folder(exports_dir)

        except Exception as e:
            logger.error(f"Failed to export order history: {e}")
            self._show_order_notification("Failed to export order history", "error")

    def _open_exports_folder(self, folder_path: str):
        """Open the exports folder in the system file manager."""
        try:
            import subprocess
            import platform

            system = platform.system()
            if system == "Windows":
                subprocess.Popen(f'explorer "{folder_path}"')
            elif system == "Darwin":  # macOS
                subprocess.Popen(["open", folder_path])
            else:  # Linux and others
                subprocess.Popen(["xdg-open", folder_path])

        except Exception as e:
            logger.warning(f"Could not open exports folder: {e}")


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

        # Order history shortcut (Ctrl+H)
        order_history_shortcut = QShortcut(QKeySequence("Ctrl+H"), self)
        order_history_shortcut.activated.connect(self._show_order_history_dialog)

        logger.info("Order history keyboard shortcut (Ctrl+H) registered")

        # Performance dashboard shortcut (Ctrl+P)
        performance_shortcut = QShortcut(QKeySequence("Ctrl+P"), self)
        performance_shortcut.activated.connect(self._show_performance_dialog)

        logger.info("Performance dashboard keyboard shortcut (Ctrl+P) registered")

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



    def _handle_order_modification(self, order_data: Dict[str, Any]):
        """
        Handle order modification - cancel existing order and open order dialog with pre-populated data.

        Args:
            order_data: Current order data to be modified
        """
        try:
            order_id = order_data.get('order_id')
            symbol = order_data.get('tradingsymbol')

            logger.info(f"Starting modification workflow for order {order_id}")

            # Step 1: Cancel the existing order
            if self.order_manager and hasattr(self.order_manager, 'cancel_order'):
                cancelled = self.order_manager.cancel_order(order_id)
                if not cancelled:
                    self._show_order_notification(f"Failed to cancel order {order_id}", "error")
                    return
            elif hasattr(self, 'trader') and hasattr(self.trader, 'cancel_order'):
                # Direct trader cancellation for paper trading
                try:
                    self.trader.cancel_order("regular", order_id)
                except Exception as e:
                    self._show_order_notification(f"Failed to cancel order: {str(e)}", "error")
                    return
            else:
                self._show_order_notification("Order cancellation not available", "error")
                return

            # Step 2: Close the status dialog
            if hasattr(self, 'order_status_dialog') and self.order_status_dialog:
                self.order_status_dialog._close_dialog()
                self.order_status_dialog = None

            # Step 3: Get fresh LTP for the symbol
            ltp = self._get_fresh_ltp(symbol)
            if ltp == 0.0:
                ltp = order_data.get('price', 0.0)  # Fallback to original price

            # Step 4: Prepare order details for pre-population
            order_details = {
                'tradingsymbol': symbol,
                'transaction_type': order_data.get('transaction_type', 'BUY'),
                'quantity': order_data.get('quantity', 1),
                'order_type': order_data.get('order_type', 'LIMIT'),
                'price': order_data.get('price', ltp),
                'trigger_price': order_data.get('trigger_price', 0.0),
                'product': order_data.get('product', 'MIS'),
                'validity': order_data.get('validity', 'DAY'),
                'ltp': ltp,
                # Preserve any special order attributes
                'stop_loss_price': order_data.get('stop_loss_price'),
                'target_price': order_data.get('target_price'),
                'tag': order_data.get('tag', ''),
                # Mark as modification
                'is_modification': True,
                'original_order_id': order_id
            }

            # Step 5: Open the order dialog with pre-populated data
            from dialogs.order_dialog import OrderDialog
            dialog = OrderDialog(self, symbol, ltp, order_details)
            dialog.order_placed.connect(self._handle_order_placement)
            if hasattr(dialog, 'bracket_order_placed'):
                dialog.bracket_order_placed.connect(self._handle_bracket_order_placement)
            dialog.show()

            # Step 6: Show confirmation
            self._show_order_notification(
                f"Order {order_id} cancelled. Modify and place new order.",
                "info"
            )

            logger.info(f"Order modification dialog opened for {symbol}")

        except Exception as e:
            error_msg = f"Error during order modification: {str(e)}"
            logger.error(error_msg, exc_info=True)
            self._show_order_notification(error_msg, "error")

    def _handle_order_cancellation(self, order_id: str):
        """
        Handle direct order cancellation from status dialog.

        Args:
            order_id: ID of the order to cancel
        """
        try:
            logger.info(f"Cancelling order {order_id}")

            # Cancel through order manager
            if self.order_manager and hasattr(self.order_manager, 'cancel_order'):
                cancelled = self.order_manager.cancel_order(order_id)
                if cancelled:
                    self._show_order_notification(f"Order {order_id} cancelled successfully", "success")
                else:
                    self._show_order_notification(f"Failed to cancel order {order_id}", "error")
            elif hasattr(self, 'trader') and hasattr(self.trader, 'cancel_order'):
                # Direct trader cancellation
                self.trader.cancel_order("regular", order_id)
                self._show_order_notification(f"Order {order_id} cancelled successfully", "success")
            else:
                self._show_order_notification("Order cancellation not available", "error")
                return

            # Close the status dialog
            if hasattr(self, 'order_status_dialog') and self.order_status_dialog:
                self.order_status_dialog._close_dialog()
                self.order_status_dialog = None

        except Exception as e:
            error_msg = f"Error cancelling order {order_id}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            self._show_order_notification(error_msg, "error")

    @Slot(dict)
    def _on_order_completed(self, order_data: Dict[str, Any]):
        """Single handler for order completion - called from status dialog."""
        try:
            symbol = order_data.get('tradingsymbol', '')
            filled_quantity = order_data.get('filled_quantity', 0)
            avg_price = order_data.get('average_price', 0)
            transaction_type = order_data.get('transaction_type', '')
            order_id = order_data.get('order_id', '')

            logger.info(f"Processing order completion: {order_id}")

            # Mark as processed to prevent duplicate handling
            order_data['update_source'] = 'status_dialog'
            order_data['status'] = 'COMPLETE'

            # Log order update (with source marking)
            try:
                self.trade_logger.log_order_update(order_data)
            except Exception as log_error:
                logger.error(f"Failed to log order completion: {log_error}")

            # Show success notification
            message = f"✓ Order completed: {transaction_type} {filled_quantity} {symbol} @ ₹{avg_price:.2f}"
            self._show_order_notification(message, "success")

            # CRITICAL: Force fetch positions from Kite after order completion
            self._force_refresh_positions_from_kite()

            # Emit trade completion signal
            self.trade_completed.emit()

            # Update performance metrics
            QTimer.singleShot(2000, self._update_performance_metrics_in_header)

            # Refresh performance dialog if open
            if self.performance_dialog and self.performance_dialog.isVisible():
                QTimer.singleShot(1500, self.performance_dialog.refresh_data)

            logger.info(f"Order completion processed successfully: {order_id}")

        except Exception as e:
            logger.error(f"Error handling order completion: {e}")

    def _force_refresh_positions_from_kite(self):
        """Force refresh positions from Kite API after order execution."""
        try:
            logger.info("Force refreshing positions from Kite after order completion")

            if hasattr(self, 'position_manager'):
                # Use a timer to allow order to settle in Kite's system
                QTimer.singleShot(1000, self._refresh_positions_from_api)
                QTimer.singleShot(3000, self._refresh_positions_from_api)  # Second try
            else:
                logger.warning("Position manager not available for refresh")

        except Exception as e:
            logger.error(f"Error forcing position refresh: {e}")

    def _refresh_positions_from_api(self):
        """Refresh positions directly from API."""
        try:
            if hasattr(self, 'position_manager'):
                self.position_manager.fetch_positions_and_orders()
                logger.info("Positions refreshed from Kite API")

            # Also refresh the positions table UI
            if hasattr(self, 'positions_table'):
                QTimer.singleShot(500, self.positions_table.update)

        except Exception as e:
            logger.error(f"Error refreshing positions from API: {e}")

    def show_order_status_dialog(self, order_data: Dict[str, Any]):
        """
        Show order status dialog for monitoring order execution.
        Call this method after placing an order to monitor its status.

        Args:
            order_data: Order data with order_id and other details
        """
        try:
            # Close any existing status dialog
            if hasattr(self, 'order_status_dialog') and self.order_status_dialog:
                self.order_status_dialog._close_dialog()

            # Create new status dialog
            self.order_status_dialog = create_order_status_dialog(self, order_data)

            logger.info(f"Order status dialog shown for order {order_data.get('order_id')}")

        except Exception as e:
            logger.error(f"Error showing order status dialog: {e}")

    def _handle_order_update(self, order_data: Dict[str, Any]):
        """Enhanced order update handler with new notifications."""
        try:
            # Log the order update
            if hasattr(self, 'trade_logger') and self.trade_logger:
                self.trade_logger.log_order_update(order_data)

            # Extract order details
            order_id = order_data.get('order_id', '')
            status = order_data.get('status', '').upper()
            symbol = order_data.get('tradingsymbol', '')

            logger.info(f"Processing order update: {order_id} -> {status}")

            # Update order status dialog if exists
            if hasattr(self, 'order_status_dialog') and self.order_status_dialog:
                if self.order_status_dialog.order_id == order_id:
                    self.order_status_dialog.update_from_external(order_data)

            # Handle specific status changes with enhanced notifications
            if status == 'COMPLETE':
                self._show_order_executed_notification(order_data)
                self._refresh_positions_table()

            elif status == 'CANCELLED':
                self._show_order_cancelled_notification(order_data)

            elif status == 'REJECTED':
                reason = order_data.get('status_message', 'Unknown reason')
                self._show_order_rejected_notification(order_data, reason)

            elif status in ['PARTIAL']:
                self._show_partial_fill_notification(order_data)
                # Ensure status dialog is shown for partial fills
                if not hasattr(self, 'order_status_dialog') or not self.order_status_dialog:
                    self.show_order_status_dialog(order_data)

            elif status in ['OPEN', 'PENDING_EXECUTION']:
                # Ensure status dialog is shown for pending orders
                if not hasattr(self, 'order_status_dialog') or not self.order_status_dialog:
                    self.show_order_status_dialog(order_data)

            # Refresh UI components
            self._refresh_ui_after_order_update(order_data)

            logger.info(f"Order update processed successfully: {order_id} -> {status}")

        except Exception as e:
            error_msg = f"Failed to handle order update for {order_data.get('order_id', 'unknown')}: {e}"
            logger.error(error_msg, exc_info=True)
            self._show_order_notification(error_msg, "error")

    def _handle_order_completion(self, order_data: Dict[str, Any]):
        """Handle completed order updates."""
        try:
            symbol = order_data.get('tradingsymbol', '')
            filled_quantity = order_data.get('filled_quantity', 0)
            avg_price = order_data.get('average_price', 0)
            transaction_type = order_data.get('transaction_type', '')

            # Show success notification
            message = f"✓ Order completed: {transaction_type} {filled_quantity} {symbol} @ ₹{avg_price:.2f}"
            self._show_order_notification(message, "success")

            # Refresh positions table immediately
            self._refresh_positions_table()

            # Update P&L displays
            if hasattr(self, 'header_toolbar') and hasattr(self.header_toolbar, 'refresh_pnl'):
                QTimer.singleShot(1000, self.header_toolbar.refresh_pnl)

            logger.info(f"Order completion handled: {symbol} - {filled_quantity} @ ₹{avg_price}")

        except Exception as e:
            logger.error(f"Error handling order completion: {e}")

    def _handle_order_cancellation_update(self, order_data: Dict[str, Any]):
        """Handle order cancellation updates."""
        try:
            symbol = order_data.get('tradingsymbol', '')
            order_id = order_data.get('order_id', '')

            # Show cancellation notification
            message = f"Order cancelled: {symbol} ({order_id[:8]}...)"
            self._show_order_notification(message, "info")

            # Close status dialog if it's for this order
            if (hasattr(self, 'order_status_dialog') and
                    self.order_status_dialog and
                    self.order_status_dialog.order_id == order_id):
                # Let the dialog handle its own closure after showing cancellation status
                pass

            logger.info(f"Order cancellation handled: {order_id}")

        except Exception as e:
            logger.error(f"Error handling order cancellation: {e}")

    def _handle_order_rejection_update(self, order_data: Dict[str, Any]):
        """Handle order rejection updates."""
        try:
            symbol = order_data.get('tradingsymbol', '')
            order_id = order_data.get('order_id', '')
            reason = order_data.get('status_message', 'Unknown reason')

            # Show rejection notification with reason
            message = f"Order rejected: {symbol} - {reason}"
            self._show_order_notification(message, "error")

            logger.warning(f"Order rejection handled: {order_id} - {reason}")

        except Exception as e:
            logger.error(f"Error handling order rejection: {e}")

    def _handle_partial_order_update(self, order_data: Dict[str, Any]):
        """Handle partial fill updates."""
        try:
            symbol = order_data.get('tradingsymbol', '')
            filled_quantity = order_data.get('filled_quantity', 0)
            total_quantity = order_data.get('quantity', 0)

            # Calculate fill percentage
            fill_percentage = (filled_quantity / total_quantity * 100) if total_quantity > 0 else 0

            # Show partial fill notification
            message = f"Partial fill: {filled_quantity}/{total_quantity} {symbol} ({fill_percentage:.0f}%)"
            self._show_order_notification(message, "info")

            # Ensure status dialog is shown for partial fills
            if not hasattr(self, 'order_status_dialog') or not self.order_status_dialog:
                self.show_order_status_dialog(order_data)

            # Refresh positions if there's any fill
            if filled_quantity > 0:
                self._refresh_positions_table()

            logger.info(f"Partial order update handled: {symbol} - {filled_quantity}/{total_quantity}")

        except Exception as e:
            logger.error(f"Error handling partial order update: {e}")

    def _show_order_status_if_needed(self, order_data: Dict[str, Any]):
        """Show order status dialog if order is pending and dialog not already shown."""
        try:
            status = order_data.get('status', '').upper()

            # Only show for pending orders that might need user action
            if status in ['OPEN', 'PENDING_EXECUTION', 'TRIGGER_PENDING']:
                # Check if dialog already exists for this order
                if (not hasattr(self, 'order_status_dialog') or
                        not self.order_status_dialog or
                        self.order_status_dialog.order_id != order_data.get('order_id')):
                    self.show_order_status_dialog(order_data)

        except Exception as e:
            logger.error(f"Error showing order status dialog: {e}")

    def _refresh_ui_after_order_update(self, order_data: Dict[str, Any]):
        """Refresh UI components after order update."""
        try:
            status = order_data.get('status', '').upper()

            # Refresh order history if dialog is open
            if (hasattr(self, 'order_history_dialog') and
                    self.order_history_dialog and
                    self.order_history_dialog.isVisible()):
                QTimer.singleShot(500, self.order_history_dialog.refresh_orders)

            # Refresh positions for executed orders
            if status in ['COMPLETE', 'PARTIAL']:
                QTimer.singleShot(1000, self._refresh_positions_table)

            # Update watchlist if the symbol is being watched
            symbol = order_data.get('tradingsymbol', '')
            if (hasattr(self, 'watchlist') and
                    symbol and
                    hasattr(self.watchlist, 'update_symbol_data')):
                QTimer.singleShot(500, lambda: self.watchlist.update_symbol_data(symbol))

        except Exception as e:
            logger.error(f"Error refreshing UI after order update: {e}")

    def get_order_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Enhanced method to get current order status for real-time updates.
        This method is called by OrderStatusDialog for live updates.
        """
        try:
            # Method 1: Try advanced order manager
            if (hasattr(self, 'order_manager') and
                    self.order_manager and
                    hasattr(self.order_manager, 'get_order_status')):
                status = self.order_manager.get_order_status(order_id)
                if status:
                    return status

            # Method 2: Try trader orders list
            if hasattr(self, 'trader') and hasattr(self.trader, 'orders'):
                try:
                    orders = self.trader.orders()
                    for order in orders:
                        if order.get('order_id') == order_id:
                            return order
                except Exception as e:
                    logger.debug(f"Could not get orders from trader: {e}")

            # Method 3: Try trade logger
            if (hasattr(self, 'trade_logger') and
                    self.trade_logger and
                    hasattr(self.trade_logger, 'get_order')):
                try:
                    return self.trade_logger.get_order(order_id)
                except Exception as e:
                    logger.debug(f"Could not get order from trade logger: {e}")

            # Method 4: Try paper trading manager
            if (hasattr(self, 'paper_trading_manager') and
                    self.paper_trading_manager and
                    hasattr(self.paper_trading_manager, 'get_order')):
                try:
                    return self.paper_trading_manager.get_order(order_id)
                except Exception as e:
                    logger.debug(f"Could not get order from paper trading: {e}")

            logger.debug(f"Could not find order status for {order_id}")
            return None

        except Exception as e:
            logger.error(f"Error getting order status for {order_id}: {e}")
            return None

    # Additional helper method for order placement integration
    def _handle_order_placement_with_status_dialog(self, order_data: Dict[str, Any]):
        """
        Enhanced order placement handler that automatically shows status dialog.
        Use this instead of or in addition to your existing _handle_order_placement.
        """
        try:
            # Call existing order placement logic
            self._handle_order_placement(order_data)

            # Get the order ID from the placement result
            order_id = order_data.get('order_id')

            if order_id:

                QTimer.singleShot(500, lambda: self.show_order_status_dialog(order_data))

                logger.info(f"Order placed with status monitoring: {order_id}")

        except Exception as e:
            logger.error(f"Error in order placement with status dialog: {e}")

    def _show_order_notification(self, message: str, notification_type: str = "info",
                                 sound_type: str = None, silent_during_startup: bool = True,
                                 action_data: Dict[str, Any] = None):
        """
        Enhanced notification system with sleek toast notifications.
        Replaces the old popup-based notification system.

        Args:
            message: Notification message text
            notification_type: Type of notification for styling
            sound_type: Override sound type (deprecated - auto-determined now)
            silent_during_startup: Whether to suppress notifications during startup
            action_data: Optional data for clickable notifications
        """
        try:
            # Check if we should suppress during startup
            if silent_during_startup and not getattr(self, '_startup_complete', True):
                logger.info(f"Suppressed startup notification: {message}")
                return

            # Map old notification types to new system
            type_mapping = {
                "success": NotificationType.SUCCESS,
                "error": NotificationType.ERROR,
                "info": NotificationType.INFO,
                "warning": NotificationType.WARNING,
                "order_placed": NotificationType.ORDER_PLACED,
                "order_executed": NotificationType.ORDER_EXECUTED,
                "order_cancelled": NotificationType.ORDER_CANCELLED,
                "order_rejected": NotificationType.ORDER_REJECTED,
                "partial_fill": NotificationType.PARTIAL_FILL,
                "position_update": NotificationType.POSITION_UPDATE,
                "alert": NotificationType.ALERT,
                "system": NotificationType.SYSTEM
            }

            # Get notification type
            notif_type = type_mapping.get(notification_type, NotificationType.INFO)

            # Show notification through manager
            notification_id = self.notification_manager.show_notification(
                message=message,
                notification_type=notif_type,
                action_data=action_data,
                silent=False  # Let the manager handle sound based on notification type
            )

            logger.debug(f"Showed notification: {notification_id} - {message}")

        except Exception as e:
            logger.error(f"Error showing notification: {e}")
            # Fallback to console log if notification system fails
            print(f"NOTIFICATION: {message}")

    # Enhanced notification methods for specific order events
    def _show_order_placed_notification(self, order_data: Dict[str, Any]):
        """Show order placed notification with action data."""
        symbol = order_data.get('tradingsymbol', '')
        transaction_type = order_data.get('transaction_type', '')
        quantity = order_data.get('quantity', 0)
        price = order_data.get('price', 0)
        order_id = order_data.get('order_id', '')

        # Ensure we show SUCCESS message for placed orders
        message = f"✓ Order placed: {transaction_type} {quantity} {symbol} @ ₹{price:,.2f}"

        action_data = {
            'action_type': 'show_order_history',
            'order_id': order_id,
            'symbol': symbol
        }

        # Use "success" type for order placement
        self._show_order_notification(message, "success", action_data=action_data)

    def _show_order_executed_notification(self, order_data: Dict[str, Any]):
        """Show order executed notification with position link."""
        symbol = order_data.get('tradingsymbol', '')
        transaction_type = order_data.get('transaction_type', '')
        filled_quantity = order_data.get('filled_quantity', 0)
        avg_price = order_data.get('average_price', 0)

        message = f"✓ Executed: {transaction_type} {filled_quantity} {symbol} @ ₹{avg_price:,.2f}"

        action_data = {
            'action_type': 'show_positions',
            'symbol': symbol
        }

        self._show_order_notification(message, "order_executed", action_data=action_data)

    def _show_order_cancelled_notification(self, order_data: Dict[str, Any]):
        """Show order cancelled notification."""
        symbol = order_data.get('tradingsymbol', '')
        order_id = order_data.get('order_id', '')

        message = f"Order cancelled: {symbol} ({order_id[:8]}...)"

        self._show_order_notification(message, "order_cancelled")

    def _show_order_rejected_notification(self, order_data: Dict[str, Any], reason: str = ""):
        """Show order rejected notification with reason."""
        symbol = order_data.get('tradingsymbol', '')
        order_id = order_data.get('order_id', '')

        if reason:
            message = f"Order rejected: {symbol} - {reason}"
        else:
            message = f"Order rejected: {symbol} ({order_id[:8]}...)"

        action_data = {
            'action_type': 'open_order_dialog',
            'symbol': symbol,
            'retry_order': True
        }

        self._show_order_notification(message, "order_rejected", action_data=action_data)

    def _show_partial_fill_notification(self, order_data: Dict[str, Any]):
        """Show partial fill notification with progress."""
        symbol = order_data.get('tradingsymbol', '')
        filled_quantity = order_data.get('filled_quantity', 0)
        total_quantity = order_data.get('quantity', 0)
        order_id = order_data.get('order_id', '')

        fill_percentage = (filled_quantity / total_quantity * 100) if total_quantity > 0 else 0
        message = f"Partial fill: {filled_quantity}/{total_quantity} {symbol} ({fill_percentage:.0f}%)"

        action_data = {
            'action_type': 'show_order_history',
            'order_id': order_id,
            'symbol': symbol
        }

        self._show_order_notification(message, "partial_fill", action_data=action_data)

    def _show_position_update_notification(self, position_data: Dict[str, Any]):
        """Show position update notification."""
        symbol = position_data.get('tradingsymbol', '')
        quantity = position_data.get('quantity', 0)
        pnl = position_data.get('pnl', 0)

        if quantity > 0:
            message = f"Position: +{quantity} {symbol} | P&L: ₹{pnl:,.2f}"
        elif quantity < 0:
            message = f"Position: {quantity} {symbol} | P&L: ₹{pnl:,.2f}"
        else:
            message = f"Position closed: {symbol} | P&L: ₹{pnl:,.2f}"

        action_data = {
            'action_type': 'show_positions',
            'symbol': symbol
        }

        self._show_order_notification(message, "position_update", action_data=action_data)


    def _show_system_notification(self, message: str):
        """Show system status notification."""
        self._show_order_notification(message, "system")

    # Connection status notifications
    def _on_market_data_connected(self):
        """Handle market data connection."""
        self._show_system_notification("Market data connected ✓")

    def _on_market_data_disconnected(self):
        """Handle market data disconnection."""
        self._show_system_notification("Market data disconnected - Reconnecting..."
                                       "")

    def _on_api_error(self, error_msg: str):
        """Handle API errors."""
        self._show_order_notification(f"API Error: {error_msg}", "error")

    # Utility method to clear all notifications
    def clear_all_notifications(self):
        """Clear all active notifications."""
        if hasattr(self, 'notification_manager'):
            self.notification_manager.clear_all_notifications()

    # Method to show notifications for alerts
    def _show_alert_notification(self, alert_data: Dict[str, Any]):
        """Show notification for price alerts."""
        symbol = alert_data.get('symbol', '')
        price = alert_data.get('current_price', 0)
        condition = alert_data.get('condition', '')

        message = f"🔔 Alert: {symbol} {condition} ₹{price:,.2f}"

        action_data = {
            'action_type': 'open_order_dialog',
            'symbol': symbol,
            'alert_triggered': True
        }

        self._show_order_notification(message, "alert", action_data=action_data)



    def debug_position_market_data_subscription(self):
        """Debug method to check position token subscription status"""
        try:
            logger.info("=== POSITION SUBSCRIPTION DEBUG ===")

            if not hasattr(self, 'position_manager') or not self.position_manager._positions:
                logger.warning("No positions to debug")
                return

            # Check position tokens
            position_tokens = []
            for symbol, position in self.position_manager._positions.items():
                token = getattr(position, 'instrument_token', 0)
                position_tokens.append((symbol, token))
                logger.info(f"Position: {symbol} -> Token: {token}")

            # Check subscription status
            if hasattr(self, 'market_data_worker') and self.market_data_worker:
                worker_info = self.market_data_worker.get_subscription_info()
                subscribed_tokens = worker_info.get('subscribed_tokens', [])

                logger.info(f"Total subscribed tokens: {len(subscribed_tokens)}")

                for symbol, token in position_tokens:
                    is_subscribed = token in subscribed_tokens
                    status = "✅ SUBSCRIBED" if is_subscribed else "❌ NOT SUBSCRIBED"
                    logger.info(f"  {symbol} (token: {token}): {status}")

            logger.info("=== DEBUG END ===")

        except Exception as e:
            logger.error(f"Error in subscription debug: {e}")

    def force_position_subscription_refresh(self):
        """Force refresh of position token subscriptions"""
        try:
            logger.info("🔄 Force refreshing position subscriptions...")

            # First refresh positions
            if hasattr(self, 'position_manager') and self.position_manager:
                self.position_manager.fetch_positions_and_orders(force_api_call=True)

            # Wait a bit then refresh subscriptions
            QTimer.singleShot(2000, self._force_subscription_update)

        except Exception as e:
            logger.error(f"Error in force subscription refresh: {e}")

    def _force_subscription_update(self):
        """Internal method to force subscription update"""
        try:
            # Trigger watchlist change to include all tokens
            self._on_watchlist_changed()

            # Debug subscription status
            QTimer.singleShot(1000, self.debug_position_market_data_subscription)

        except Exception as e:
            logger.error(f"Error in force subscription update: {e}")


    def _refresh_positions_table(self):
        """Enhanced position table refresh with subscription check"""
        logger.debug("Requesting position and order refresh...")

        # Force refresh positions
        if hasattr(self, 'position_manager'):
            self.position_manager.fetch_positions_and_orders()

            # CRITICAL: Ensure tokens are subscribed after refresh
            QTimer.singleShot(2000, self._ensure_position_tokens_subscribed)

        # Also refresh the positions table data immediately
        if hasattr(self, 'positions_table'):
            QTimer.singleShot(100, self.positions_table.update)

    def _ensure_position_tokens_subscribed(self):
        """Ensure all position tokens are subscribed after position refresh"""
        try:
            if hasattr(self, 'position_manager') and self.position_manager._positions:
                # Trigger a subscription update
                self._on_watchlist_changed()
                logger.info("✅ Ensured position tokens are subscribed after refresh")
        except Exception as e:
            logger.error(f"Error ensuring position token subscription: {e}")

    def save_window_state(self):
        """Enhanced window state saving with both splitters."""
        try:
            state = {
                'geometry': self.saveGeometry().toBase64().data().decode('utf-8'),
                'main_splitter': self.main_splitter.saveState().toBase64().data().decode('utf-8'),
                'is_maximized': self.isMaximized()
            }

            # Save right panel splitter state
            if hasattr(self, 'right_panel_splitter'):
                state['right_panel_splitter'] = self.right_panel_splitter.saveState().toBase64().data().decode('utf-8')

            self.config_manager.save_window_state(state)
            logger.info("Window state saved with stable splitter configuration.")
        except Exception as e:
            logger.error(f"Failed to save window state: {e}", exc_info=True)

    def restore_window_state(self):
        """Enhanced window state restoration with stable splitter behavior."""
        try:
            state = self.config_manager.load_window_state()
            if state and state.get('geometry'):
                self.restoreGeometry(QByteArray.fromBase64(state['geometry'].encode('utf-8')))

                # Restore main splitter with validation
                if 'main_splitter' in state:
                    try:
                        self.main_splitter.restoreState(QByteArray.fromBase64(state['main_splitter'].encode('utf-8')))
                    except Exception as e:
                        logger.warning(f"Failed to restore main splitter state: {e}")
                        self.main_splitter.setSizes([250, 600, 300])
                else:
                    self.main_splitter.setSizes([250, 600, 300])

                # Restore right panel splitter with validation
                if hasattr(self, 'right_panel_splitter') and 'right_panel_splitter' in state:
                    try:
                        self.right_panel_splitter.restoreState(
                            QByteArray.fromBase64(state['right_panel_splitter'].encode('utf-8')))
                    except Exception as e:
                        logger.warning(f"Failed to restore right panel splitter state: {e}")
                        # Use proportional default sizes
                        total_height = 500  # Safe default
                        self.right_panel_splitter.setSizes([300, 200])
                elif hasattr(self, 'right_panel_splitter'):
                    self.right_panel_splitter.setSizes([300, 200])

                if state.get('is_maximized', False):
                    self.showMaximized()
                    self.max_btn.setText("❐")

                logger.info("Window state restored with stable splitter behavior.")
            else:
                # Default state with safe sizes
                self.showMaximized()
                self.max_btn.setText("❐")
                self.main_splitter.setSizes([250, 600, 300])
                if hasattr(self, 'right_panel_splitter'):
                    self.right_panel_splitter.setSizes([300, 200])

        except Exception as e:
            logger.error(f"Failed to restore window state: {e}", exc_info=True)
            # Safe fallback
            self.showMaximized()
            self.main_splitter.setSizes([250, 600, 300])
            if hasattr(self, 'right_panel_splitter'):
                self.right_panel_splitter.setSizes([300, 200])

    def _apply_dark_theme(self):
        """Enhanced dark theme with FIXED splitter styling for proper resizing."""
        self.setStyleSheet("""
            #mainContainer { 
                background-color: #0a0a0a; 
                border: 1px solid #1a1a1a; 
            }

            #customTitleBar { 
                background-color: #0a0a0a; 
                border-bottom: 1px solid #202020; 
            }

            #appTitle { 
                color: #a0c0ff; 
                font-size: 12px; 
                font-weight: 600; 
            }

            #tradingModeLabel { 
                color: #64ffda; 
                font-size: 10px; 
                font-weight: 500; 
            }

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

            QMainWindow, QWidget { 
                background-color: #0a0a0a; 
                color: #e0e0e0; 
                font-family: "Segoe UI", Arial, sans-serif; 
            }

            /* FIXED: Properly styled splitter handles for easy dragging */
            QSplitter { 
                background-color: #0a0a0a;
            }

            QSplitter::handle { 
                background-color: #1a1a1a;
                border: none;
                margin: 0px;
            }

            QSplitter::handle:horizontal { 
                width: 4px; 
                background-color: #1a1a1a;
                border-left: 1px solid #0a0a0a;
                border-right: 1px solid #0a0a0a;
            }

            QSplitter::handle:vertical { 
                height: 4px; 
                background-color: #1a1a1a;
                border-top: 1px solid #0a0a0a;
                border-bottom: 1px solid #0a0a0a;
            }

            QSplitter::handle:hover { 
                background-color: #6a9cff; 
            }

            QSplitter::handle:pressed {
                background-color: #5a8be0;
            }

            /* FIXED: Special styling for right panel splitter */
            QSplitter#rightPanelSplitter::handle:vertical {
                background-color: #2a2a2a;
                height: 4px;
            }

            QSplitter#rightPanelSplitter::handle:vertical:hover {
                background-color: #6a9cff;
            }

            /* Enhanced Scrollbars */
            QScrollBar:vertical { 
                background-color: #151515; 
                width: 8px; 
                border: none; 
                margin: 0px;
            }

            QScrollBar::handle:vertical { 
                background-color: #424242; 
                border-radius: 4px; 
                min-height: 20px; 
                margin: 2px;
            }

            QScrollBar::handle:vertical:hover { 
                background-color: #616161; 
            }

            QScrollBar:horizontal { 
                background-color: #151515; 
                height: 8px; 
                border: none; 
                margin: 0px;
            }

            QScrollBar::handle:horizontal { 
                background-color: #424242; 
                border-radius: 4px; 
                min-width: 20px; 
                margin: 2px;
            }

            QScrollBar::handle:horizontal:hover { 
                background-color: #616161; 
            }

            QScrollBar::add-line, QScrollBar::sub-line {
                border: none;
                background: none;
                width: 0px;
                height: 0px;
            }

            QDialog { 
                background-color: #121212; 
                border: 1px solid #282828; 
            }

            QMessageBox { 
                background-color: #121212; 
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