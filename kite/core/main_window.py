# ==============================================================================
#  MAIN WINDOW
# ==============================================================================

import logging
import os
import json
from datetime import datetime
from typing import List, Dict, Union, Any, Optional

from PySide6.QtCore import Qt, QByteArray, QTimer, Slot, Signal, QEvent
from PySide6.QtWidgets import QMainWindow, QSplitter, QWidget, QVBoxLayout, QHBoxLayout, \
    QPushButton, QLabel, QApplication
from PySide6.QtGui import QMouseEvent, QKeySequence, QShortcut, QKeyEvent

from kite.widgets.scanner_table import ChartinkScannerTable
from kite.widgets.positions_table import PositionsTable
from kite.widgets.watchlist_table import TabbedWatchlistWidget
from chart_engine import CandlestickChart as ChartWindow
from kite.widgets.header_toolbar import HeaderToolbar
from kite.widgets.color_settings_dialog import ColorSettingsDialog

from kite.widgets.order_dialog import OrderDialog
from kite.widgets.order_history_dialog import OrderHistoryDialog
from kite.widgets.performance_dialog import PerformanceDialog
from kite.core.alert_management_system import AlertSystemManager
from kite.core.chart_lines_manager import ChartLinesManager
from kite.core.data_cache import MarketAwareDataCache

from kite.core.position_manager import PositionManager
from kite.core.shutdown_manager import CleanShutdownMixin

from kite.core.market_data_worker import MarketDataWorker
from kite.utils.paper_trading_manager import (
    PaperTradingManager,
    PaperTradingMixin,
    integrate_paper_trading,
)
from kite.utils.config_manager import ConfigManager
from kite.core.instrument_loader import InstrumentLoader
from kite.core.trade_logger import TradeLogger
from kiteconnect import KiteConnect

from kite.widgets.status_bar import (
    show_error, show_info, show_order_placed, show_order_failed,
    show_order_completed, show_order_rejected, show_order_cancelled,
    status  # Global status manager
)
from kite.utils.sounds import play_alert, play_error
from kite.utils.color_system import get_color_theme_manager


logger = logging.getLogger(__name__)


class SwingTraderWindow(CleanShutdownMixin, PaperTradingMixin, QMainWindow):
    """
    SIMPLIFIED Main Window with LED-style status bar instead of popup notifications:
    - Simple Position Manager (only works when tracking orders)
    - LED Status Bar in header toolbar (no popup distractions)
    - Self-Managing Positions Table (local PnL calculation)
    - Event-driven updates (no continuous polling)
    """
    trade_completed = Signal()

    def _get_paper_trading_manager(self) -> Optional[PaperTradingManager]:
        """Return the underlying paper trading manager, even when wrapped."""
        if isinstance(self.trader, PaperTradingManager):
            return self.trader

        wrapped_client = getattr(self.trader, 'client', None)
        if isinstance(wrapped_client, PaperTradingManager):
            return wrapped_client

        return None

    # ==============================================================================
    # INITIALIZATION AND SETUP
    # ==============================================================================

    def __init__(self, trader: Union[KiteConnect, PaperTradingManager], real_kite_client: KiteConnect,
                 api_key: str, access_token: str):
        super().__init__()

        # --- Core Application Components ---
        self.trader = trader
        self.real_kite_client = real_kite_client
        self.api_key = api_key
        self.access_token = access_token
        self.config_manager = ConfigManager()
        self.color_theme_manager = get_color_theme_manager()
        paper_trader = self._get_paper_trading_manager()
        self.trading_mode = 'paper' if paper_trader else 'live'
        self.trade_logger = TradeLogger(
            broker="kite",
            mode=self.trading_mode,
        )

        # SIMPLIFIED MANAGERS - NO NOTIFICATION SYSTEM
        self.position_manager = PositionManager(self.trader, main_window=self)

        self.chart_lines_manager = ChartLinesManager(self)
        self.chart_lines_manager.chart_refresh_requested.connect(
            self._refresh_chart_drawings
        )

        self.instrument_list: List[Dict] = []
        self.instrument_map: Dict[str, Dict] = {}
        self._subscribed_tokens = set()

        if paper_trader:
            paper_trader.set_trade_logger(self.trade_logger)
            paper_trader.set_main_window(self)
            integrate_paper_trading(self, paper_trader)

        # --- Window Dragging Variables ---
        self._drag_pos = None
        self._is_maximized = False
        self.order_history_dialog = None
        self.performance_dialog = None

        # --- Setup Sequence ---
        self._setup_frameless_window()
        self._setup_ui()
        self._init_alert_system()
        self._init_background_workers()
        self._connect_signals()
        self.color_theme_manager.theme_changed.connect(self._on_color_theme_changed)
        self._connect_chart_signals()
        self._setup_watchlist_shortcuts()

        self._apply_dark_theme()
        self.restore_window_state()

        logger.info("Simplified Swing Trader Window with Status Bar Initialized Successfully.")

        # Start position manager after a delay
        QTimer.singleShot(2000, self._initialize_position_system)


    def _initialize_position_system(self):
        """Initialize a position system after the main parts are ready"""
        try:
            # Fetch initial positions on startup
            self.position_manager.fetch_positions_from_kite("app_startup")
            status.set_ready()  # Set status bar to ready
            logger.info("Position system initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize position system: {e}")
            show_error("Failed to initialize positions")

    def _setup_frameless_window(self):
        """Setup frameless window with custom title bar."""
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumSize(1200, 700)

    def _setup_ui(self):
        """Setup UI with simplified layout"""
        main_container = QWidget()
        main_container.setObjectName("mainContainer")
        self.setCentralWidget(main_container)

        main_layout = QVBoxLayout(main_container)
        main_layout.setContentsMargins(0, 0, 0, 5)
        main_layout.setSpacing(0)

        self.title_bar = self._create_custom_title_bar()
        main_layout.addWidget(self.title_bar)

        # HEADER TOOLBAR WITH STATUS BAR INTEGRATION
        self.header_toolbar = HeaderToolbar(self.trader, self)
        self.header_toolbar.color_settings_requested.connect(self._open_color_settings_dialog)
        main_layout.addWidget(self.header_toolbar)

        # Initialize global status manager with header toolbar's status bar
        if hasattr(self.header_toolbar, 'status_bar'):
            status.initialize(self.header_toolbar.status_bar)
            logger.info("Status bar integrated with header toolbar")

        # Create the main splitter
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.main_splitter, 1)

        # Create components
        self.chartink_scanner = ChartinkScannerTable()
        self.candlestick_chart = ChartWindow(self.real_kite_client)
        self.candlestick_chart.data_cache = MarketAwareDataCache(parent=self.candlestick_chart)
        # Backward-compat for force-refresh path still using `_cache`.
        if not hasattr(self.candlestick_chart.data_cache, '_cache'):
            self.candlestick_chart.data_cache._cache = self.candlestick_chart.data_cache._store
        self.watchlist = TabbedWatchlistWidget()
        self.positions_table = PositionsTable(parent=self)

        initial_theme = self.color_theme_manager.get_theme()
        self.chartink_scanner.apply_color_theme(initial_theme)
        self.watchlist.apply_color_theme(initial_theme)
        self.positions_table.apply_color_theme(initial_theme)
        self.candlestick_chart.apply_color_theme(initial_theme)

        # Create right panel splitter
        right_panel_splitter = QSplitter(Qt.Orientation.Vertical)
        right_panel_splitter.setObjectName("rightPanelSplitter")
        right_panel_splitter.addWidget(self.watchlist)
        right_panel_splitter.addWidget(self.positions_table)

        # Configure splitters
        right_panel_splitter.setStretchFactor(0, 3)
        right_panel_splitter.setStretchFactor(1, 2)
        right_panel_splitter.setChildrenCollapsible(False)
        right_panel_splitter.setHandleWidth(4)

        self.watchlist.setMinimumHeight(150)
        self.positions_table.setMinimumHeight(100)

        # Add to the main splitter
        self.main_splitter.addWidget(self.chartink_scanner)
        self.main_splitter.addWidget(self.candlestick_chart)
        self.main_splitter.addWidget(right_panel_splitter)

        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(4)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setSizes([250, 600, 300])

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

    @Slot(dict)
    def _on_color_theme_changed(self, theme: Dict[str, Any]):
        self.chartink_scanner.apply_color_theme(theme)
        self.watchlist.apply_color_theme(theme)
        self.positions_table.apply_color_theme(theme)
        self.candlestick_chart.apply_color_theme(theme)

    def _open_color_settings_dialog(self):
        dialog = ColorSettingsDialog(self.color_theme_manager.get_theme(), self)
        if dialog.exec():
            self.color_theme_manager.update_theme(dialog.get_theme())

    def _init_alert_system(self):
        try:
            self.alert_system = AlertSystemManager(self)
            self.alert_system.alert_sound_requested.connect(lambda: play_alert())
            self.alert_system.engine_status_changed.connect(self._on_alert_engine_status)
            self.alert_system.alert_triggered.connect(self._on_alert_triggered)
            logger.info("Alert system initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize alert system: {e}")
            self.alert_system = None

    @Slot(str)
    def _on_alert_engine_status(self, status: str):
        """Handle alert engine status changes"""
        if status == "error":
            logger.warning("Alert engine encountered an error")
            play_error()  # SIMPLE
        elif status == "running":
            logger.info("Alert engine is running normally")

    def _init_background_workers(self):
        """Initialize background workers"""
        self.chart_init_timer = QTimer()
        self.chart_init_timer.setSingleShot(True)
        self.chart_init_timer.timeout.connect(self._initialize_chart_after_instruments)

        self.instrument_loader = InstrumentLoader(self.real_kite_client)
        self.instrument_loader.instruments_loaded.connect(self._on_instruments_loaded)
        self.instrument_loader.error_occurred.connect(
            lambda e: logger.error(f"Critical error loading instruments: {e}"))
        self.instrument_loader.start()

        self.market_data_worker = MarketDataWorker(self.api_key, self.access_token)
        self.market_data_worker.data_received.connect(self._on_market_data)
        self.market_data_worker.connection_established.connect(self._on_websocket_connect)
        self.market_data_worker.start()

    @Slot()
    def _on_websocket_connect(self):
        """WebSocket connection handler"""
        logger.info("WebSocket connected. Setting up subscriptions.")
        status.show_api_status("CONNECTED")

        if (hasattr(self, 'candlestick_chart') and
                hasattr(self.candlestick_chart, 'current_instrument_token') and
                self.candlestick_chart.current_instrument_token):
            try:
                self.market_data_worker.add_instruments([self.candlestick_chart.current_instrument_token])
                logger.info(f"Subscribed to chart token: {self.candlestick_chart.current_instrument_token}")
            except Exception as e:
                logger.error(f"Failed to subscribe to chart: {e}")
        self._on_watchlist_changed()

    def _connect_chart_signals(self):
        """Connect chart signals"""
        if self.candlestick_chart:
            self.candlestick_chart.symbol_loaded.connect(self._on_chart_symbol_changed)
            self.candlestick_chart.data_request_for_symbol.connect(self._ensure_chart_subscription)
            # FIX #9: redraw alert lines whenever the chart switches symbol
            if self.alert_system:
                self.candlestick_chart.symbol_loaded.connect(
                    self.alert_system.sync_chart_lines_for_symbol
                )

    @Slot(str)
    def _on_chart_symbol_changed(self, symbol: str):
        """Handle chart symbol changes"""
        logger.info(f"Chart symbol changed to: {symbol}")
        if symbol in self.instrument_map:
            token = self.instrument_map[symbol]['instrument_token']
            try:
                if self.market_data_worker and self.market_data_worker.is_connected():
                    self.market_data_worker.add_instruments([token])
                    logger.info(f"Added chart symbol {symbol} to subscription")
                QTimer.singleShot(100, self._on_watchlist_changed)
            except Exception as e:
                logger.error(f"Failed to subscribe to chart symbol {symbol}: {e}")

    @Slot(str)
    def _ensure_chart_subscription(self, symbol: str):
        """Ensure chart symbol is subscribed"""
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

    def _refresh_chart_drawings(self):
        """Refresh chart drawings when lines are updated"""
        try:
            if hasattr(self, 'candlestick_chart'):
                # Force chart to reload current symbol's drawings
                current_symbol = getattr(self.candlestick_chart, 'current_symbol', '')
                if current_symbol:
                    self.chart_lines_manager.load_symbol_with_fresh_drawings(current_symbol)
        except Exception as e:
            logger.error(f"Error refreshing chart drawings: {e}")

    # ==============================================================================
    # SIMPLIFIED SIGNAL CONNECTIONS
    # ==============================================================================

    def _connect_signals(self):
        """Connect signals with simplified architecture"""
        logger.info("Connecting component signals...")

        # SIMPLIFIED: Position Manager → Positions Table (direct connection)
        self.position_manager.positions_updated.connect(self.positions_table.update_positions)
        if hasattr(self, 'market_data_worker') and self.market_data_worker:
            self.market_data_worker.order_update.connect(self.position_manager.on_ws_order_update)
            self.market_data_worker.connection_established.connect(self.position_manager.on_ws_connected)
            self.market_data_worker.connection_closed.connect(self.position_manager.on_ws_disconnected)
        # NO MORE NOTIFICATION SIGNALS - Position manager uses global status directly

        # SIMPLIFIED: Positions Table → Main Window
        self.positions_table.exit_position_requested.connect(self._handle_exit_position_request)
        self.positions_table.symbol_selected.connect(self.candlestick_chart.on_search)
        self.positions_table.subscribe_to_market_data.connect(self._subscribe_to_tokens)

        # Chart → Main Window & Header
        self.candlestick_chart.order_button_clicked.connect(self._show_order_dialog)
        self.candlestick_chart.symbol_loaded.connect(self.header_toolbar.set_current_symbol)
        if self.alert_system:
            self.candlestick_chart.alert_creation_requested.connect(self.alert_system.create_alert_from_chart)

        # Scanner & Watchlist → Chart
        self.chartink_scanner.symbol_selected.connect(self.candlestick_chart.on_search)
        self.watchlist.symbol_selected.connect(self.candlestick_chart.on_search)
        self.watchlist.subscribe_tokens_requested.connect(self._subscribe_to_tokens)
        self.watchlist.place_order_requested.connect(self._show_order_dialog_from_dict)
        self.watchlist.watchlist_changed.connect(self._on_watchlist_changed)

        # Header Toolbar → Main Window
        self.header_toolbar.symbol_selected.connect(self.candlestick_chart.on_search)
        self.header_toolbar.buy_order_requested.connect(self._on_header_buy_order)
        self.header_toolbar.sell_order_requested.connect(self._on_header_sell_order)
        self.header_toolbar.order_history_requested.connect(self._show_order_history_dialog)
        self.header_toolbar.performance_dashboard_requested.connect(self._show_performance_dialog)

        # Alert System
        if self.alert_system:
            self.header_toolbar.alert_manager_requested.connect(lambda: self.alert_system.show_alert_manager(self))
        else:
            self.header_toolbar.alert_manager_requested.connect(self._alert_system_unavailable)

        # Alert update timer
        self.alert_update_timer = QTimer(self)
        self.alert_update_timer.timeout.connect(self._update_alert_badges)
        self.alert_update_timer.start(30000)

    # ==============================================================================
    # WINDOW MANAGEMENT & EVENTS
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


    # ==============================================================================
    # CORE EVENT HANDLERS (SIMPLIFIED)
    # ==============================================================================

    def _on_instruments_loaded(self, instruments: List[Dict]):
        """Handle instrument loading with NSE preference"""
        logger.info(f"Successfully loaded {len(instruments)} instruments.")
        self.instrument_list = instruments

        # BUILD INSTRUMENT MAP WITH NSE PREFERENCE
        self.instrument_map = self._build_instrument_map_with_nse_preference(instruments)

        # Set instrument data in components
        self.header_toolbar.set_instrument_data(instruments)
        self.candlestick_chart.set_instrument_list(instruments)
        self.watchlist.set_instrument_map(self.instrument_map)

        paper_trader = self._get_paper_trading_manager()
        if paper_trader:
            paper_trader.set_instrument_map(self.instrument_map)
            logger.info("Paper trader instrument map updated")
        if self.alert_system:
            self.alert_system.set_instrument_map(self.instrument_map)

        # Fetch positions after instruments are loaded
        QTimer.singleShot(1000, lambda: self.position_manager.fetch_positions_from_kite("instruments_loaded"))

        self._on_watchlist_changed()
        self.chart_init_timer.start(1000)
        logger.info("Instruments loaded successfully.")

    def _build_instrument_map_with_nse_preference(self, instruments: List[Dict]) -> Dict[str, Dict]:
        """Build instrument map prioritizing NSE over BSE for same symbols"""
        instrument_map = {}

        # Sort instruments to process NSE first, then BSE, then others
        def exchange_priority(inst):
            exchange = inst.get('exchange', '')
            if exchange == 'NSE':
                return 0  # Highest priority
            elif exchange == 'BSE':
                return 1  # Second priority
            else:
                return 2  # Lowest priority

        sorted_instruments = sorted(instruments, key=exchange_priority)

        # Build map - NSE will be processed first and won't be overwritten
        nse_count = 0
        bse_count = 0
        bse_overridden = 0

        for inst in sorted_instruments:
            symbol = inst.get('tradingsymbol')
            exchange = inst.get('exchange', '')

            if symbol:
                if symbol not in instrument_map:
                    # First time seeing this symbol
                    instrument_map[symbol] = inst
                    if exchange == 'NSE':
                        nse_count += 1
                    elif exchange == 'BSE':
                        bse_count += 1
                else:
                    # Symbol already exists - this means BSE is trying to override NSE
                    existing_exchange = instrument_map[symbol].get('exchange', '')
                    if existing_exchange == 'NSE' and exchange == 'BSE':
                        bse_overridden += 1
                        logger.debug(f"Kept NSE version of {symbol} (ignored BSE)")
                    # Don't overwrite - keep the NSE version

        logger.info(f"Built instrument map: {nse_count} NSE symbols, {bse_count} BSE-only symbols")
        logger.info(f"BSE duplicates ignored: {bse_overridden}")

        return instrument_map

    def _initialize_chart_after_instruments(self):
        """Initialize chart after instruments are ready"""
        try:
            logger.info("Chart auto-loading initiated")
        except Exception as e:
            logger.error(f"Error in chart auto-loading: {e}")

    @Slot(list)
    def _on_market_data(self, ticks: List[Dict]):
        """Handle market data updates with NSE preference and improved filtering"""
        if not ticks:
            return

        try:
            # Filter and prioritize ticks by exchange preference
            filtered_ticks = self._filter_ticks_by_exchange_preference(ticks)

            # Update chart with filtered data
            self._update_chart_data(filtered_ticks)

            # Update positions table with market data (using filtered ticks)
            self._update_positions_market_data(filtered_ticks)

            # Update other components with filtered data
            paper_trader = self._get_paper_trading_manager()
            if paper_trader:
                paper_trader.update_market_data(filtered_ticks)

            self.watchlist.update_data(filtered_ticks)

            if self.alert_system:
                self.alert_system.update_market_data(filtered_ticks)

        except Exception as e:
            logger.error(f"Error processing market data: {e}")

    def _filter_ticks_by_exchange_preference(self, ticks: List[Dict]) -> List[Dict]:
        """Filter ticks to prefer NSE over BSE for same symbols"""
        if not hasattr(self, 'instrument_map'):
            return ticks

        # Group ticks by symbol
        symbol_ticks = {}
        token_to_symbol = {}

        for tick in ticks:
            # Get symbol from tick or resolve from token
            symbol = tick.get('tradingsymbol')
            token = tick.get('instrument_token')

            if not symbol and token:
                # Try to resolve symbol from token
                symbol = self._resolve_symbol_from_token(token)
                if symbol:
                    tick['tradingsymbol'] = symbol

            if symbol:
                if symbol not in symbol_ticks:
                    symbol_ticks[symbol] = []
                symbol_ticks[symbol].append(tick)

                if token:
                    token_to_symbol[token] = symbol

        # Filter to prefer NSE over BSE for each symbol
        filtered_ticks = []

        for symbol, tick_list in symbol_ticks.items():
            if len(tick_list) == 1:
                # Only one tick for this symbol, use it
                filtered_ticks.extend(tick_list)
            else:
                # Multiple ticks for same symbol, prefer NSE
                nse_tick = None
                bse_tick = None
                other_ticks = []

                for tick in tick_list:
                    exchange = self._get_exchange_for_tick(tick, symbol)
                    if exchange == 'NSE':
                        nse_tick = tick
                    elif exchange == 'BSE':
                        bse_tick = tick
                    else:
                        other_ticks.append(tick)

                # Prefer NSE, fallback to BSE, then others
                if nse_tick:
                    filtered_ticks.append(nse_tick)
                    logger.debug(f"Using NSE tick for {symbol}")
                elif bse_tick:
                    filtered_ticks.append(bse_tick)
                    logger.debug(f"Using BSE tick for {symbol} (NSE not available)")
                else:
                    filtered_ticks.extend(other_ticks)

        logger.debug(f"Filtered {len(ticks)} ticks to {len(filtered_ticks)} (NSE preference applied)")
        return filtered_ticks

    def _resolve_symbol_from_token(self, token: int) -> Optional[str]:
        """Resolve trading symbol from instrument token with NSE preference"""
        if not hasattr(self, 'instrument_map'):
            return None

        # Look for token in an instrument map
        nse_symbol = None
        bse_symbol = None
        other_symbol = None

        for symbol, instrument in self.instrument_map.items():
            if instrument.get('instrument_token') == token:
                exchange = instrument.get('exchange', '')
                if exchange == 'NSE':
                    nse_symbol = symbol
                elif exchange == 'BSE':
                    bse_symbol = symbol
                else:
                    other_symbol = symbol

        # Return in preference order
        return nse_symbol or bse_symbol or other_symbol

    def _get_exchange_for_tick(self, tick: Dict, symbol: str) -> str:
        """Get exchange for a tick, with lookup in an instrument map if needed"""
        # First check if tick has exchange info
        if 'exchange' in tick:
            return tick['exchange']

        # Look up in an instrument map
        if hasattr(self, 'instrument_map') and symbol in self.instrument_map:
            return self.instrument_map[symbol].get('exchange', 'NSE')

        # Default to NSE
        return 'NSE'

    def _update_chart_data(self, ticks: List[Dict]):
        """Update chart with filtered market data"""
        current_chart_symbol = getattr(self.candlestick_chart, 'current_symbol', None)
        current_chart_token = getattr(self.candlestick_chart, 'current_instrument_token', None)

        if not self.candlestick_chart or not current_chart_symbol:
            return

        chart_ticks = []
        for tick in ticks:
            tick_symbol = tick.get('tradingsymbol')
            tick_token = tick.get('instrument_token')

            # Direct symbol match
            symbol_matches = tick_symbol == current_chart_symbol

            # Token match (if available)
            token_matches = (tick_token == current_chart_token) if tick_token and current_chart_token else False

            if symbol_matches or token_matches:
                chart_ticks.append(tick)

        if chart_ticks:
            logger.debug(f"Sending {len(chart_ticks)} filtered ticks to chart for {current_chart_symbol}")
            self.candlestick_chart.update_live_data(chart_ticks)

    def _update_positions_market_data(self, ticks: List[Dict]):
        """Update positions table with filtered market data"""
        for tick in ticks:
            token = tick.get('instrument_token')
            ltp = tick.get('last_price', 0)
            if token and ltp > 0:
                self.positions_table.update_market_data(token, ltp)

    # Additional helper method for monitoring exchange usage
    def _log_exchange_statistics(self, ticks: List[Dict]):
        """Log statistics about exchange usage in ticks (for debugging)"""
        if not logger.isEnabledFor(logging.DEBUG):
            return

        exchange_counts = {'NSE': 0, 'BSE': 0, 'OTHER': 0}

        for tick in ticks:
            symbol = tick.get('tradingsymbol')
            if symbol and hasattr(self, 'instrument_map'):
                exchange = self.instrument_map.get(symbol, {}).get('exchange', 'OTHER')
                if exchange in exchange_counts:
                    exchange_counts[exchange] += 1
                else:
                    exchange_counts['OTHER'] += 1

        if any(exchange_counts.values()):
            logger.debug(f"Market data exchange distribution: {exchange_counts}")

    @Slot()
    def _on_watchlist_changed(self):
        """Handle watchlist changes with position priority"""
        logger.info("Watchlist changed - updating subscriptions")
        all_tokens = set()

        # Priority 1: Position tokens
        if hasattr(self, 'positions_table') and self.positions_table.positions_data:
            position_tokens = [pos.token for pos in self.positions_table.positions_data.values() if pos.token > 0]
            all_tokens.update(position_tokens)
            logger.info(f"Added {len(position_tokens)} position tokens")

        # Priority 2: Chart token
        if (hasattr(self, 'candlestick_chart') and
                hasattr(self.candlestick_chart, 'current_instrument_token') and
                self.candlestick_chart.current_instrument_token):
            all_tokens.add(self.candlestick_chart.current_instrument_token)
            logger.info(f"Added chart token: {self.candlestick_chart.current_instrument_token}")

        # Priority 3: Watchlist tokens
        watchlist_tokens = self.watchlist.get_all_tokens()
        all_tokens.update(watchlist_tokens)
        logger.info(f"Added {len(watchlist_tokens)} watchlist tokens")

        # Priority 4: Alert tokens
        alert_tokens = self._get_alert_tokens()
        all_tokens.update(alert_tokens)

        # Subscribe to all tokens
        if self.market_data_worker and all_tokens:
            self.market_data_worker.set_instruments(list(all_tokens))
            logger.info(f"Updated subscription to {len(all_tokens)} tokens")

    @Slot(list)
    def _subscribe_to_tokens(self, tokens: List[int]):
        """Subscribe to market data tokens"""
        if not tokens:
            return

        new_tokens = [token for token in tokens if token not in self._subscribed_tokens]
        if not new_tokens:
            return

        try:
            if self.market_data_worker and hasattr(self.market_data_worker, 'add_instruments'):
                self.market_data_worker.add_instruments(new_tokens)
                self._subscribed_tokens.update(new_tokens)
                logger.info(f"Added {len(new_tokens)} new tokens to subscription")
        except Exception as e:
            logger.error(f"Failed to subscribe to tokens: {e}")

    # ==============================================================================
    # SIMPLIFIED ORDER HANDLING WITH STATUS BAR
    # ==============================================================================

    @Slot(str, float)
    def _show_order_dialog(self, symbol: str, ltp_from_chart: float = 0.0):
        """Show order dialog - simplified"""
        ltp = ltp_from_chart if ltp_from_chart > 0.0 else self._get_fresh_ltp(symbol)
        if ltp == 0.0:
            show_error(f"Could not fetch LTP for {symbol}")
            return
        if symbol not in self.instrument_map:
            show_error(f"Symbol {symbol} not found")
            return

        default_qty = self.config_manager.load_settings().get('default_quantity', 1)
        order_details = {'tradingsymbol': symbol, 'ltp': ltp, 'transaction_type': 'BUY', 'quantity': default_qty}

        instrument = self.instrument_map.get(symbol, {})
        dialog = OrderDialog(self, symbol, ltp, order_details, instrument=instrument)
        dialog.order_placed.connect(self._handle_order_placement)
        dialog.show()

    def _show_order_dialog_from_dict(self, order_data: Dict[str, Any]):
        """Show order dialog from watchlist"""
        symbol = order_data.get('tradingsymbol')
        if symbol:
            ltp = self._get_fresh_ltp(symbol)
            instrument = self.instrument_map.get(symbol, {})
            dialog = OrderDialog(self, symbol, ltp, order_data, instrument=instrument)
            dialog.order_placed.connect(self._handle_order_placement)
            dialog.show()

    def _on_header_buy_order(self, symbol: str):
        """Handle buy order from header"""
        self._show_order_dialog(symbol)

    def _on_header_sell_order(self, symbol: str):
        """Handle sell order from header"""
        ltp = self._get_fresh_ltp(symbol)
        if ltp == 0.0:
            show_error(f"Could not fetch LTP for {symbol}")
            return

        default_qty = self.config_manager.load_settings().get('default_quantity', 1)
        order_details = {'tradingsymbol': symbol, 'ltp': ltp, 'transaction_type': 'SELL', 'quantity': default_qty}

        instrument = self.instrument_map.get(symbol, {})
        dialog = OrderDialog(self, symbol, ltp, order_details, instrument=instrument)
        dialog.order_placed.connect(self._handle_order_placement)
        dialog.show()

    @Slot(str)
    def _handle_exit_position_request(self, symbol: str):
        """Handle position exit request - simplified"""
        position = self.positions_table.get_position_by_symbol(symbol)
        if not position:
            show_error(f"Position not found: {symbol}")
            return

        transaction_type = "SELL" if position.quantity > 0 else "BUY"
        ltp = self._get_fresh_ltp(symbol)

        exit_order = {
            "tradingsymbol": symbol,
            "transaction_type": transaction_type,
            "quantity": abs(position.quantity),
            "order_type": "MARKET",
            "product": position.product,
            "ltp": ltp
        }

        instrument = self.instrument_map.get(symbol, {})
        dialog = OrderDialog(self, symbol, ltp, exit_order, instrument=instrument)
        dialog.order_placed.connect(self._handle_order_placement)
        dialog.show()

    def _handle_order_placement(self, order_data: Dict[str, Any]):
        """CLEAN order placement handler - sounds via status bar only"""
        try:
            logger.info(f"Placing order: {order_data}")

            if not self._validate_order_data(order_data):
                show_error("Order validation failed")  # Sound plays automatically
                return

            symbol = order_data.get('tradingsymbol', '')

            # ONLY status call - sound plays automatically
            show_order_placed(symbol)

            if hasattr(self.trader, 'place_order'):
                order_id = self.trader.place_order(**order_data)
            else:
                logger.error("No order placement method available")
                show_error("Order placement system offline")  # Sound plays automatically
                return

            if order_id:
                order_data['order_id'] = order_id
                order_data['status'] = 'PENDING'
                self.position_manager.start_tracking_order(order_id, order_data)
                self._log_order_placement_immediate(order_data, order_id)
                logger.info(f"Order placed and tracking started: {order_id}")
            else:
                show_order_failed("No order ID returned")  # Sound plays automatically

        except Exception as e:
            error_msg = f"Order placement failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            show_order_failed(str(e))

    def _log_order_placement_immediate(self, order_data: Dict[str, Any], order_id: str):
        """
        Log order placement immediately with no delays or timers
        """
        try:
            if hasattr(self, 'trade_logger') and self.trade_logger:
                # This is now fully async and won't block the UI
                self.trade_logger.log_order_placement(order_data, order_id)
                logger.info(f"Order queued for logging: {order_id}")
        except Exception as log_error:
            # Even if logging fails, don't block the UI
            logger.error(f"Failed to queue order for logging: {log_error}")

    # ==============================================================================
    # DIALOG SHOW METHODS
    # ==============================================================================

    def _show_order_history_dialog(self):
        """Show order history dialog"""
        try:
            if self.order_history_dialog is None or not self.order_history_dialog.isVisible():
                self.order_history_dialog = OrderHistoryDialog(
                    trade_logger=self.trade_logger,
                    parent=self
                )
                self.order_history_dialog.refresh_requested.connect(self._refresh_order_history)
                self.order_history_dialog.export_requested.connect(self._export_order_history)

            self.order_history_dialog.show()
            self.order_history_dialog.raise_()
            self.order_history_dialog.activateWindow()
            logger.info("Order history dialog opened")
        except Exception as e:
            logger.error(f"Failed to show order history dialog: {e}")
            show_error("Failed to open order history")

    def _show_performance_dialog(self):
        """Show performance dialog"""
        try:
            if self.performance_dialog is None or not self.performance_dialog.isVisible():
                self.performance_dialog = PerformanceDialog(
                    trade_logger=self.trade_logger,
                    parent=self
                )
                self.trade_completed.connect(self.performance_dialog.refresh_data)

            self.performance_dialog.refresh_data()
            self.performance_dialog.show()
            self.performance_dialog.raise_()
            self.performance_dialog.activateWindow()
            logger.info("Performance dashboard opened")
        except Exception as e:
            logger.error(f"Failed to show performance dashboard: {e}")
            show_error("Failed to open performance dashboard")

    def _refresh_order_history(self):
        """Handle order history refresh request"""
        try:
            if self.order_history_dialog and self.order_history_dialog.isVisible():
                self.order_history_dialog.refresh_orders()
                status.show_info("Order history refreshed")
                logger.info("Order history manually refreshed")
        except Exception as e:
            logger.error(f"Failed to refresh order history: {e}")
            show_error("Failed to refresh order history")

    def _export_order_history(self, export_data: dict):
        """Handle order history export request"""
        try:
            # Create exports directory
            home = os.path.expanduser("~")
            exports_dir = os.path.join(home, ".swing_trader", "exports")
            os.makedirs(exports_dir, exist_ok=True)

            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"order_history_export_{timestamp}.json"
            filepath = os.path.join(exports_dir, filename)

            # Add metadata
            export_data.update({
                'export_source': 'swing_trader_order_history',
                'trading_mode': self.trading_mode,
                'export_timestamp': timestamp
            })

            # Export to JSON file
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False, default=str)

            status.show_info(f"Exported: {filename}")
            logger.info(f"Order history exported to: {filepath}")

        except Exception as e:
            logger.error(f"Failed to export order history: {e}")
            show_error("Export failed")

    # ==============================================================================
    # ALERT SYSTEM METHODS
    # ==============================================================================



    @Slot(str)
    def _on_alert_triggered(self, alert_id: str):
        """Handle alert trigger events from alert engine."""
        logger.info(f"Alert triggered: {alert_id}")
        self._update_alert_badges()

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

    def _alert_system_unavailable(self):
        show_error("Alert system unavailable")

    # ==============================================================================
    # UTILITY & HELPER METHODS
    # ==============================================================================

    def _get_fresh_ltp(self, symbol: str) -> float:
        """Get fresh LTP for symbol with NSE preference"""
        ltp = 0.0

        # Check watchlist tables
        for table in self.watchlist._tables.values():
            if hasattr(table, '_watchlist_data') and symbol in table._watchlist_data:
                ltp = table._watchlist_data[symbol].get('ltp', 0.0)
                if ltp > 0:
                    return ltp

        # Check instrument map (now NSE-preferred)
        if symbol in self.instrument_map:
            ltp = self.instrument_map[symbol].get('last_price', 0)
            if ltp > 0:
                return ltp

        # Fallback to API with NSE preference
        try:
            if self.real_kite_client:
                # Use the exchange from our NSE-preferred instrument map
                exchange = self.instrument_map.get(symbol, {}).get('exchange', 'NSE')
                quote = self.real_kite_client.quote([f"{exchange}:{symbol}"])
                ltp = quote[f"{exchange}:{symbol}"].get('last_price', 0)
                return ltp
        except Exception as e:
            logger.warning(f"Failed to fetch LTP for {symbol} via API: {e}")

        return ltp

    def _validate_order_data(self, order_data: Dict[str, Any]) -> bool:
        """Validate order data"""
        required = ['tradingsymbol', 'transaction_type', 'quantity', 'order_type']
        for field in required:
            if field not in order_data:
                show_error(f"Missing field: {field}")
                return False

        if not isinstance(order_data.get('quantity'), (int, float)) or order_data['quantity'] <= 0:
            show_error("Invalid quantity")
            return False

        if order_data['tradingsymbol'] not in self.instrument_map:
            show_error(f"Symbol not found: {order_data['tradingsymbol']}")
            return False

        return True

    def _setup_watchlist_shortcuts(self):
        """Setup keyboard shortcuts"""
        # Watchlist shortcuts
        shortcut_map = {"Ctrl+Shift+1": "Breakouts", "Ctrl+Shift+2": "EP", "Ctrl+Shift+3": "Parabolic"}
        for key, category in shortcut_map.items():
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(lambda cat=category: self._add_symbol_to_watchlist_from_chart(cat))

        # Order history shortcut (Ctrl+H)
        order_history_shortcut = QShortcut(QKeySequence("Ctrl+H"), self)
        order_history_shortcut.activated.connect(self._show_order_history_dialog)

        # Performance dashboard shortcut (Ctrl+P)
        performance_shortcut = QShortcut(QKeySequence("Ctrl+P"), self)
        performance_shortcut.activated.connect(self._show_performance_dialog)

        # Global navigation shortcuts
        self._setup_global_shortcuts()
        logger.info("Keyboard shortcuts initialized")

    def _add_symbol_to_watchlist_from_chart(self, category: str):
        """Add current chart symbol to watchlist"""
        current_symbol = getattr(self.candlestick_chart, 'current_symbol', None)
        if not current_symbol:
            status.show_info("No symbol on chart")
            return

        if self.watchlist.add_symbol(current_symbol, category):
            status.show_info(f"Added {current_symbol} to {category}")
        else:
            status.show_info(f"{current_symbol} already in {category}")

    def _setup_global_shortcuts(self):
        """Setup global navigation shortcuts"""
        from PySide6.QtGui import QShortcut, QKeySequence

        # Global spacebar shortcut for symbol navigation
        self.spacebar_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self.spacebar_shortcut.activated.connect(self._handle_global_spacebar)

        # Global Shift+Spacebar for reverse navigation
        self.shift_spacebar_shortcut = QShortcut(QKeySequence("Shift+Space"), self)
        self.shift_spacebar_shortcut.activated.connect(self._handle_global_shift_spacebar)

        logger.info("Global navigation shortcuts initialized")

    def _handle_global_spacebar(self):
        """Handle spacebar press based on focused widget"""
        focused_widget = self.focusWidget()

        # Check scanner focus
        if self._is_scanner_focused(focused_widget):
            if hasattr(self.chartink_scanner, '_next_symbol'):
                self.chartink_scanner._next_symbol()
                return

        # Check watchlist focus
        watchlist_table = self._get_focused_watchlist_table(focused_widget)
        if watchlist_table:
            self._navigate_watchlist_symbols(watchlist_table, direction='next')
            return

        # Check positions focus
        if self._is_positions_focused(focused_widget):
            self._navigate_position_symbols(direction='next')
            return

        # Fallback to scanner
        if hasattr(self.chartink_scanner, '_next_symbol'):
            self.chartink_scanner._next_symbol()

    def _handle_global_shift_spacebar(self):
        """Handle Shift+spacebar press based on focused widget"""
        focused_widget = self.focusWidget()

        if self._is_scanner_focused(focused_widget):
            if hasattr(self.chartink_scanner, '_previous_symbol'):
                self.chartink_scanner._previous_symbol()
                return

        watchlist_table = self._get_focused_watchlist_table(focused_widget)
        if watchlist_table:
            self._navigate_watchlist_symbols(watchlist_table, direction='previous')
            return

        if self._is_positions_focused(focused_widget):
            self._navigate_position_symbols(direction='previous')
            return

        if hasattr(self.chartink_scanner, '_previous_symbol'):
            self.chartink_scanner._previous_symbol()

    def _is_scanner_focused(self, widget) -> bool:
        """Check if the scanner has focus"""
        if not widget:
            return False
        current = widget
        while current:
            if current == self.chartink_scanner:
                return True
            if hasattr(current, 'objectName') and 'scanner' in current.objectName().lower():
                return True
            current = current.parent()
        return False

    def _get_focused_watchlist_table(self, widget):
        """Get focused watchlist table"""
        if not widget:
            return None
        current = widget
        while current:
            if current == self.watchlist:
                for category, table in self.watchlist._tables.items():
                    if table == widget or self._is_child_of_widget(widget, table):
                        return table
                return None
            current = current.parent()
        return None

    def _is_positions_focused(self, widget) -> bool:
        """Check if the position table has focus"""
        if not widget:
            return False
        current = widget
        while current:
            if current == self.positions_table:
                return True
            if hasattr(current, 'table') and current.table == widget:
                return True
            current = current.parent()
        return False

    def _is_child_of_widget(self, child, parent) -> bool:
        """Check if child is a descendant of parent"""
        if not child or not parent:
            return False
        current = child
        while current:
            if current == parent:
                return True
            current = current.parent()
        return False

    def _navigate_watchlist_symbols(self, table, direction='next'):
        """Navigate symbols in watchlist table"""
        if not table or not hasattr(table, '_watchlist_symbols'):
            return

        symbols = list(table._watchlist_symbols)
        if not symbols:
            return

        current_row = table.currentRow()
        if current_row == -1:
            current_row = 0

        if direction == 'next':
            next_row = (current_row + 1) % len(symbols)
        else:
            next_row = (current_row - 1) % len(symbols)

        table.selectRow(next_row)
        table.setCurrentCell(next_row, 0)

        try:
            symbol_item = table.item(next_row, 0)
            if symbol_item:
                symbol = symbol_item.text()
                if symbol and symbol != 'N/A':
                    table.symbol_selected.emit(symbol)
                    logger.debug(f"Watchlist navigation: Selected {symbol}")
        except Exception as e:
            logger.warning(f"Error navigating watchlist symbols: {e}")

    def _navigate_position_symbols(self, direction='next'):
        """Navigate symbols in positions table"""
        if not hasattr(self.positions_table, 'table'):
            return

        table = self.positions_table.table
        row_count = table.rowCount()
        if row_count == 0:
            return

        current_row = table.currentRow()
        if current_row == -1:
            current_row = 0

        if direction == 'next':
            next_row = (current_row + 1) % row_count
        else:
            next_row = (current_row - 1) % row_count

        table.selectRow(next_row)
        table.setCurrentCell(next_row, 0)

        try:
            symbol_item = table.item(next_row, 0)
            if symbol_item:
                symbol = symbol_item.text()
                if symbol and symbol != 'N/A':
                    self.positions_table.symbol_selected.emit(symbol)
                    logger.debug(f"Positions navigation: Selected {symbol}")
        except Exception as e:
            logger.warning(f"Error navigating position symbols: {e}")

    # ==============================================================================
    # WINDOW STATE MANAGEMENT
    # ==============================================================================

    def save_window_state(self):
        """Save window state"""
        try:
            state = {
                'geometry': self.saveGeometry().toBase64().data().decode('utf-8'),
                'main_splitter': self.main_splitter.saveState().toBase64().data().decode('utf-8'),
                'is_maximized': self.isMaximized()
            }

            if hasattr(self, 'right_panel_splitter'):
                state['right_panel_splitter'] = self.right_panel_splitter.saveState().toBase64().data().decode('utf-8')

            self.config_manager.save_window_state(state)
            logger.info("Window state saved")
        except Exception as e:
            logger.error(f"Failed to save window state: {e}")

    def restore_window_state(self):
        """Restore window state"""
        try:
            state = self.config_manager.load_window_state()
            if state and state.get('geometry'):
                self.restoreGeometry(QByteArray.fromBase64(state['geometry'].encode('utf-8')))

                if 'main_splitter' in state:
                    try:
                        self.main_splitter.restoreState(QByteArray.fromBase64(state['main_splitter'].encode('utf-8')))
                    except Exception as e:
                        logger.warning(f"Failed to restore main splitter state: {e}")
                        self.main_splitter.setSizes([250, 600, 300])
                else:
                    self.main_splitter.setSizes([250, 600, 300])

                if hasattr(self, 'right_panel_splitter') and 'right_panel_splitter' in state:
                    try:
                        self.right_panel_splitter.restoreState(
                            QByteArray.fromBase64(state['right_panel_splitter'].encode('utf-8')))
                    except Exception as e:
                        logger.warning(f"Failed to restore right panel splitter state: {e}")
                        self.right_panel_splitter.setSizes([300, 200])
                elif hasattr(self, 'right_panel_splitter'):
                    self.right_panel_splitter.setSizes([300, 200])

                if state.get('is_maximized', False):
                    self.showMaximized()
                    self.max_btn.setText("❐")

                logger.info("Window state restored")
            else:
                # Default state
                self.showMaximized()
                self.max_btn.setText("❐")
                self.main_splitter.setSizes([250, 600, 300])
                if hasattr(self, 'right_panel_splitter'):
                    self.right_panel_splitter.setSizes([300, 200])

        except Exception as e:
            logger.error(f"Failed to restore window state: {e}")
            # Safe fallback
            self.showMaximized()
            self.main_splitter.setSizes([250, 600, 300])
            if hasattr(self, 'right_panel_splitter'):
                self.right_panel_splitter.setSizes([300, 200])


    # ==============================================================================
    # DARK THEME STYLING
    # ==============================================================================

    def _apply_dark_theme(self):
        """Apply dark theme with splitter styling"""
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

            /* Splitter styling for easy dragging */
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

    def keyPressEvent(self, event):
        """Override keyPressEvent for main window key handling."""

        # Check if symbol input is focused - if so, don't interfere with arrow keys
        focused_widget = QApplication.focusWidget()
        if (focused_widget == self.header_toolbar.search_input and
                event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Return, Qt.Key.Key_Enter,
                                Qt.Key.Key_Escape)):
            # Let the HeaderToolbar's eventFilter handle these keys
            super().keyPressEvent(event)
            return

        # Auto-focus logic for letter keys (existing code)
        if self._is_letter_key(event) and not self._is_input_focused():
            if not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier |
                                         Qt.KeyboardModifier.AltModifier |
                                         Qt.KeyboardModifier.MetaModifier)):
                # Clear and focus the symbol input
                self.header_toolbar.search_input.clear()
                self.header_toolbar.search_input.setFocus()

                # Send the key to the input field
                self.header_toolbar.search_input.setText(event.text())
                return

        # Call parent implementation for all other keys
        super().keyPressEvent(event)

    def _is_letter_key(self, key_event):
        """Check if the pressed key is a letter (a-z, A-Z)."""
        key = key_event.key()
        return (Qt.Key.Key_A <= key <= Qt.Key.Key_Z)

    def _is_input_focused(self):
        """Check if any input field is currently focused."""
        focused_widget = QApplication.focusWidget()

        if focused_widget is None:
            return False

        # Check if the focused widget is an input field
        from PySide6.QtWidgets import QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox

        input_types = (QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox)
        return isinstance(focused_widget, input_types)
