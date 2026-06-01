# ==============================================================================
#  MAIN WINDOW
# ==============================================================================

import ast
import logging
import os
import sys
import json
import re
import time
from collections import deque
from datetime import datetime, timedelta
from typing import List, Dict, Union, Any, Optional

from PySide6.QtCore import Qt, QByteArray, QTimer, Slot, Signal, QEvent, QProcess, QSize
from PySide6.QtWidgets import QMainWindow, QSplitter, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, \
    QPushButton, QLabel, QApplication, QMessageBox, QMenuBar, QSizePolicy, QDialog, QLineEdit, QGraphicsDropShadowEffect, QToolButton
from PySide6.QtGui import QMouseEvent, QKeySequence, QKeyEvent, QAction, QColor, QIcon

from ibkr.widgets.scanner_table import FinvizScannerTable
from ibkr.widgets.positions_table import PositionsTable
from ibkr.widgets.watchlist_table import TabbedWatchlistWidget
from chart_engine import CandlestickChart as ChartWindow
from chart_engine.core.data_loader import KiteDataFetcher
from chart_engine.core.ibkr_data_fetcher import IBKRDataFetcher
from ibkr.widgets.header_toolbar import HeaderToolbar
from ibkr.widgets.settings_dialog import ColorSettingsDialog
from ibkr.widgets.stock_info_dialog import show_stock_info
from ibkr.widgets.about_dialog import show_about_dialog
from ibkr.widgets.keyboard_shortcuts import show_keyboard_shortcuts_dialog
from ibkr.widgets.keyboard_shortcuts import setup_keyboard_shortcuts

from ibkr.widgets.order_dialog import OrderDialog
from ibkr.widgets.order_history_dialog import OrderHistoryDialog
from ibkr.widgets.pending_orders_dialog import PendingOrdersDialog
from ibkr.widgets.performance_dialog import PerformanceDialog
from ibkr.widgets.pnl_history_dialog import PnlHistoryDialog
from ibkr.widgets.floating_positions_dialog import FloatingPositionsDialog
from ibkr.widgets.floating_watchlist_dialog import attach_floating_watchlist
from ibkr.widgets.reconnecting_overlay import ReconnectingOverlay
from ibkr.widgets.sectors_industries_dialog import show_sectors_industries_dialog
from ibkr.core.alert_management_system import AlertSystemManager
from ibkr.core.chart_lines_manager import ChartLinesManager
from ibkr.core.data_cache import MarketAwareDataCache
from ibkr.core.account_manager import AccountManager
from ibkr.utils.ibkr_symbol_resolver import IBKRSymbolResolver
from ibkr.utils.market_time import market_now, market_session_label, market_strftime
from utils.resource_path import resource_path

from ibkr.core.position_manager import PositionManager
from ibkr.core.stop_loss_manager import StopLossManager
from ibkr.core.shutdown_manager import CleanShutdownMixin

from ibkr.core.market_data_worker import MarketDataWorker
from ibkr.utils.paper_trading_manager import (
    PaperTradingManager,
    PaperTradingMixin,
    integrate_paper_trading,
)
from ibkr.utils.config_manager import ConfigManager
from ibkr.core.trade_logger import TradeLogger
try:
    from ib_insync import IB
except Exception:
    IB = None

from ibkr.widgets.status_bar import (
    StatusBar,
    show_error, show_info, show_order_failed,
    show_order_completed, show_order_rejected, show_order_cancelled,
    status  # Global status manager
)
from ibkr.utils.sounds import play_error
from ibkr.utils.color_system import get_color_theme_manager


logger = logging.getLogger(__name__)

# Scanner panel sizing: keep the left scanner lane compact and stable.
# This prevents the scanner table/header from pushing the main splitter handle
# to the right on startup or after scan results refresh.
_SCANNER_PANEL_MIN_WIDTH = 80
_SCANNER_PANEL_DEFAULT_WIDTH = 260
_SCANNER_PANEL_MAX_WIDTH = 360
_RIGHT_PANEL_MIN_WIDTH = 220
_RIGHT_PANEL_DEFAULT_WIDTH = 320



class QullamaggieWindow(CleanShutdownMixin, PaperTradingMixin, QMainWindow):
    """
    SIMPLIFIED Main Window with subtle bottom status bar:
    - Simple Position Manager (only works when tracking orders)
    - Bottom app status bar for market/API indicators
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

    def __init__(self, trader: Union[IB, PaperTradingManager], real_kite_client: IB,
                 api_key: str, access_token: str):
        super().__init__()

        # --- Core Application Components ---
        self.trader = trader
        self.real_kite_client = real_kite_client
        self.api_key = api_key
        self.access_token = access_token
        self.config_manager = ConfigManager()
        self.app_settings = self.config_manager.load_settings()
        self.color_theme_manager = get_color_theme_manager()
        theme_dual_mode = bool(self.color_theme_manager.get_theme().get('dual_chart_mode', False))
        self.dual_chart_mode_enabled = bool(self.app_settings.get('dual_chart_mode', theme_dual_mode))
        paper_trader = self._get_paper_trading_manager()
        self.trading_mode = 'paper' if paper_trader else 'live'
        self.trade_logger = TradeLogger(
            mode=self.trading_mode,
        )
        self.chart_drawings_dir = os.path.join(
            "ibkr", "user_data", f"chart_drawings_{self.trading_mode}"
        )

        # SIMPLIFIED MANAGERS - NO NOTIFICATION SYSTEM
        self.position_manager = PositionManager(self.trader, main_window=self, trade_logger=self.trade_logger)

        self.sl_manager = StopLossManager(
            trader=self.trader,
            position_manager=self.position_manager,
            parent=self,
        )

        # Wire notifications through the same toast system
        self.sl_manager.show_notification.connect(
            self._show_position_manager_notification
        )
        self.sl_manager.sl_set.connect(self._on_stop_loss_set)
        self.sl_manager.sl_updated.connect(self._on_stop_loss_set)
        self.sl_manager.sl_cancelled.connect(self._on_stop_loss_cancelled)
        self.sl_manager.sl_triggered.connect(self._on_stop_loss_cancelled)

        # Auto-cancel ghost SLs when positions change
        self.position_manager.positions_updated.connect(
            self.sl_manager.sync_with_positions
        )

        self.chart_lines_manager = ChartLinesManager(self)

        self.instrument_list: List[Dict] = []
        self.instrument_map: Dict[str, Dict] = {}
        self._subscribed_tokens = set()
        self._ibkr_symbol_resolver: Optional[IBKRSymbolResolver] = None

        if paper_trader:
            paper_trader.set_trade_logger(self.trade_logger)
            paper_trader.set_main_window(self)
            integrate_paper_trading(self, paper_trader)

        self.setWindowTitle("qullamaggie")

        # --- Window Dragging Variables ---
        self._drag_pos = None
        self._is_maximized = False
        self.order_history_dialog = None
        self.pending_orders_dialog = None
        self.performance_dialog = None
        self.pnl_history_dialog = None
        self.floating_positions_dialog = None
        self._target_prices: Dict[str, float] = {}
        self.floating_watchlist_dialog = None
        self._last_spacebar_context = None
        self._start_maximized = True
        self._tick_buffer_by_token: Dict[Any, Dict] = {}
        self._tick_buffer_without_token = deque()
        self._chart_tick_queue = deque()
        self._tick_flush_timer = QTimer(self)
        self._tick_flush_timer.setInterval(30)
        self._tick_flush_timer.timeout.connect(self._flush_market_data_ticks)
        self._tick_flush_timer.start()
        self._pending_contract_preload_symbols = set()
        self._preloaded_hover_symbols = set()
        self._contract_preload_timer = QTimer(self)
        self._contract_preload_timer.setSingleShot(True)
        self._contract_preload_timer.setInterval(250)
        self._contract_preload_timer.timeout.connect(self._flush_contract_preload_queue)
        self._subscription_rebuild_timer = QTimer(self)
        self._subscription_rebuild_timer.setSingleShot(True)
        self._subscription_rebuild_timer.setInterval(300)
        self._subscription_rebuild_timer.timeout.connect(self._rebuild_subscription_universe)
        self._pending_fresh_restart = False
        self._charts_revealed = False
        self._status_day_realized_total = 0.0
        self._reconnect_overlay = ReconnectingOverlay(self)

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
        self._apply_startup_dual_chart_timeframes()

        # DEFER network monitoring — don't start probing during UI construction
        QTimer.singleShot(3000, self._init_network_resilience)

        logger.info("Simplified qullamaggie Window with Status Bar Initialized Successfully.")

        # Start position manager after a delay
        QTimer.singleShot(2000, self._initialize_position_system)

    def show_initial_window_state(self):
        """Show window once using restored/default startup mode to reduce startup flicker."""
        # Keep chart panes visible so the center lane stays stable during startup.
        # The prewarmed black HTML avoids white flash while preserving layout.
        self._set_chart_panes_visible(True)

        if self._start_maximized:
            self.showMaximized()
            self.max_btn.setText("❐")
            self._is_maximized = True
        else:
            self.show()
            self.max_btn.setText("□")
            self._is_maximized = False

    def _set_chart_panes_visible(self, visible: bool):
        """Toggle chart pane visibility while preserving dual/single chart layout intent."""
        if hasattr(self, 'candlestick_chart') and self.candlestick_chart is not None:
            self.candlestick_chart.setVisible(visible)
        if hasattr(self, 'candlestick_chart_secondary') and self.candlestick_chart_secondary is not None:
            self.candlestick_chart_secondary.setVisible(visible and self.dual_chart_mode_enabled)

    @Slot(str)
    def _reveal_chart_panes_on_first_symbol(self, symbol: str):
        """Reveal chart panes once the first symbol is fully loaded into the chart."""
        if self._charts_revealed:
            return
        if not (symbol or '').strip():
            return

        self._charts_revealed = True
        self._set_chart_panes_visible(True)
        logger.info("Chart panes revealed after initial symbol render: %s", symbol)

    def _apply_startup_dual_chart_timeframes(self):
        """Set default timeframe intervals without triggering loads (no symbol yet)."""
        try:
            # Just set the toolbar state — don't trigger a load
            self.candlestick_chart.toolbar.set_timeframe("day")
            self.candlestick_chart.current_interval = "day"
            if self.dual_chart_mode_enabled:
                self.candlestick_chart_secondary.toolbar.set_timeframe("60minute")
                self.candlestick_chart_secondary.current_interval = "60minute"
                logger.info("Applied startup dual-chart timeframes: left=day, right=60minute")
            else:
                logger.info("Applied startup single-chart timeframe: primary=day")
        except Exception as e:
            logger.warning(f"Failed to apply startup chart timeframes: {e}")


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
        self.menuBar().setVisible(False)

    def _setup_ui(self):
        """Setup UI with simplified layout"""
        main_container = QWidget()
        main_container.setObjectName("mainContainer")
        self.setCentralWidget(main_container)

        main_layout = QVBoxLayout(main_container)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.menu_bar = self._create_menu_bar()
        self.top_bar = self._create_top_bar()
        main_layout.addWidget(self.top_bar)

        # Header toolbar dedicated to trading actions
        # Use the raw Kite client for live data, but keep the paper trader for paper mode
        toolbar_client = self.trader if self.trading_mode == 'paper' else self.real_kite_client
        self.header_toolbar = HeaderToolbar(toolbar_client, self, enable_account_polling=False)
        if self.real_kite_client and hasattr(self.real_kite_client, "reqMatchingSymbols"):
            self._ibkr_symbol_resolver = IBKRSymbolResolver(self.real_kite_client, parent=self)
            self.header_toolbar.set_ibkr_search_provider(self._ibkr_symbol_resolver.search)

        self.account_manager = AccountManager(toolbar_client, parent=self)
        self.account_manager.margins_updated.connect(self.header_toolbar._handle_account_info_update)
        self.account_manager.margins_updated.connect(self._on_account_info_updated)
        self.account_manager.refresh_margins(force=True)
        self._account_refresh_timer = QTimer(self)
        self._account_refresh_timer.timeout.connect(self.account_manager.refresh_if_stale)
        self._account_refresh_timer.start(10_000)
        self.header_toolbar.color_settings_requested.connect(self._open_color_settings_dialog)
        main_layout.addWidget(self.header_toolbar)

        # Create the main splitter
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.main_splitter, 1)
        self._is_adjusting_splitter = False

        # Create components
        self.finviz_scanner = FinvizScannerTable()
        chart_data_fetcher = self._create_chart_data_fetcher()
        self.chart_data_fetcher = chart_data_fetcher
        self.candlestick_chart = ChartWindow(
            chart_data_fetcher,
            storage_dir=self.chart_drawings_dir,
        )
        self.candlestick_chart_secondary = ChartWindow(
            chart_data_fetcher,
            storage_dir=self.chart_drawings_dir,
        )
        shared_chart_cache = MarketAwareDataCache(parent=self)
        self.candlestick_chart.data_cache = shared_chart_cache
        self.candlestick_chart_secondary.data_cache = shared_chart_cache
        # Backward-compat for force-refresh path still using `_cache`.
        if not hasattr(shared_chart_cache, '_cache'):
            shared_chart_cache._cache = shared_chart_cache._store
        self.watchlist = TabbedWatchlistWidget()
        self.watchlist.set_quote_client(self.real_kite_client)
        self.positions_table = PositionsTable(parent=self)
        self.positions_panel = QWidget()
        self.positions_panel.setObjectName("positionsPanelContainer")
        positions_panel_layout = QVBoxLayout(self.positions_panel)
        positions_panel_layout.setContentsMargins(0, 0, 0, 0)
        positions_panel_layout.setSpacing(0)
        self.positions_title_bar = QWidget()
        self.positions_title_bar.setObjectName("positionsPanelTitleBar")
        positions_title_layout = QHBoxLayout(self.positions_title_bar)
        positions_title_layout.setContentsMargins(8, 0, 6, 0)
        positions_title_layout.setSpacing(6)
        self.positions_title = QLabel("Positions")
        self.positions_title.setObjectName("positionsPanelTitle")
        self.positions_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.positions_title.setContentsMargins(0, 2, 0, 2)
        positions_title_layout.addWidget(self.positions_title, 1)
        self.open_positions_table_button = QToolButton()
        self.open_positions_table_button.setObjectName("openPositionsTableButton")
        self.open_positions_table_button.setToolTip("Open floating positions table")
        self.open_positions_table_button.setAccessibleName("Open floating positions table")
        self.open_positions_table_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_positions_table_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.open_positions_table_button.setAutoRaise(True)
        self.open_positions_table_button.setIcon(QIcon(resource_path("assets/icons/positions_table_open_arrow.svg")))
        self.open_positions_table_button.setIconSize(QSize(14, 14))
        self.open_positions_table_button.setFixedSize(22, 20)
        self.open_positions_table_button.clicked.connect(self._show_floating_positions_dialog)
        positions_title_layout.addWidget(self.open_positions_table_button, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        positions_panel_layout.addWidget(self.positions_title_bar)
        positions_panel_layout.addWidget(self.positions_table, 1)
        self.finviz_scanner.setObjectName("scannerPanel")
        self.candlestick_chart.setObjectName("primaryChartPanel")
        self.candlestick_chart_secondary.setObjectName("secondaryChartPanel")
        self.watchlist.setObjectName("watchlistPanel")
        self.positions_table.setObjectName("positionsPanel")

        # Build the persistent bottom status bar before applying the startup
        # theme so it exists alongside the other theme-aware widgets. The bar is
        # added to the layout after the main splitter is assembled below.
        self.app_status_bar = StatusBar(self)
        self.app_status_bar.setObjectName("bottomAppStatusBar")
        alignment = self.color_theme_manager.get_theme().get("status_bar_alignment", "left")
        self.app_status_bar.set_elements_alignment(alignment)
        self.app_status_bar.set_metrics_alignment(
            bool(self.color_theme_manager.get_theme().get("status_bar_metrics_right", True))
        )
        self.positions_table.footer_metrics_changed.connect(self._on_positions_footer_metrics_changed)

        initial_theme = self.color_theme_manager.get_theme()
        self.header_toolbar.apply_color_theme(initial_theme)
        self.finviz_scanner.apply_color_theme(initial_theme)
        self.finviz_scanner.set_live_ticks_enabled(
            bool(initial_theme.get("scanner_live_ticks", True))
        )
        self.watchlist.apply_color_theme(initial_theme)
        self.positions_table.apply_color_theme(initial_theme)
        self.app_status_bar.apply_color_theme(initial_theme)
        self.positions_table.set_footer_metrics_visible(False)
        self.candlestick_chart.apply_color_theme(initial_theme)
        self.candlestick_chart_secondary.apply_color_theme(initial_theme)

        # Create right panel splitter
        right_panel_splitter = QSplitter(Qt.Orientation.Vertical)
        right_panel_splitter.setObjectName("rightPanelSplitter")
        right_panel_splitter.addWidget(self.watchlist)
        right_panel_splitter.addWidget(self.positions_panel)

        # Configure splitters
        right_panel_splitter.setStretchFactor(0, 3)
        right_panel_splitter.setStretchFactor(1, 2)
        right_panel_splitter.setChildrenCollapsible(False)
        right_panel_splitter.setHandleWidth(1)

        self.watchlist.setMinimumHeight(150)
        self.positions_table.setMinimumHeight(100)

        # Keep side panels compact while preserving readability.
        self.finviz_scanner.setMinimumWidth(_SCANNER_PANEL_MIN_WIDTH)
        self.finviz_scanner.setMaximumWidth(_SCANNER_PANEL_MAX_WIDTH)
        right_panel_splitter.setMinimumWidth(_RIGHT_PANEL_MIN_WIDTH)
        self.candlestick_chart.setMinimumWidth(460)
        self.candlestick_chart_secondary.setMinimumWidth(460)

        # Add to the main splitter
        self.main_splitter.addWidget(self.finviz_scanner)
        self.main_splitter.addWidget(self.candlestick_chart)
        self.main_splitter.addWidget(self.candlestick_chart_secondary)
        self.main_splitter.addWidget(right_panel_splitter)

        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(1)
        # Side panels should not grow when the window grows; give the extra
        # space to charts only. This keeps the scanner splitter handle stable.
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 4)
        self.main_splitter.setStretchFactor(2, 4)
        self.main_splitter.setStretchFactor(3, 0)
        self.main_splitter.splitterMoved.connect(self._on_main_splitter_moved)
        self.main_splitter.splitterMoved.connect(self._queue_window_state_save)

        self.right_panel_splitter = right_panel_splitter
        self._apply_chart_mode_layout()

        # Bottom status bar (quiet app-level health indicators)
        main_layout.addWidget(self.app_status_bar)
        status.initialize(self.app_status_bar)
        self._setup_status_indicators()
        self.right_panel_splitter.splitterMoved.connect(self._queue_window_state_save)
        self._window_state_save_timer = QTimer(self)
        self._window_state_save_timer.setSingleShot(True)
        self._window_state_save_timer.setInterval(400)
        self._window_state_save_timer.timeout.connect(self.save_window_state)
        self._pending_main_splitter_sizes = None
        self._pending_right_splitter_sizes = None
        self._saved_watchlist_panel_width = None
        self._saved_scanner_panel_width = None
        self._apply_intelligent_main_splitter_layout()
        self._apply_panel_elevation()
        QTimer.singleShot(0, self._prewarm_webengine)
        QTimer.singleShot(1000, self._schedule_visible_contract_preload)


    def _prewarm_webengine(self):
        """Load a blank page to initialize Chromium before real chart data arrives."""
        if self.candlestick_chart.chart_view is None:
            self.candlestick_chart._create_chart_view()
            self.candlestick_chart.chart_view.setHtml(
                "<html><body style='background:#050709;margin:0'></body></html>"
            )

    def _apply_subtle_shadow(self, widget: QWidget, blur_radius: float = 14.0, y_offset: float = 1.0) -> None:
        """Apply subtle edge elevation so panels feel grouped without harsh framing."""
        if widget is None:
            return
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(blur_radius)
        effect.setOffset(0, y_offset)
        effect.setColor(QColor(0, 0, 0, 95))
        widget.setGraphicsEffect(effect)

    def _apply_panel_elevation(self) -> None:
        """Give primary widgets a gentle, cohesive elevation."""
        self._apply_subtle_shadow(self.finviz_scanner, blur_radius=12.0, y_offset=0.0)
        self._apply_subtle_shadow(self.watchlist, blur_radius=12.0, y_offset=0.0)
        self._apply_subtle_shadow(self.positions_table, blur_radius=12.0, y_offset=0.0)
        self._apply_subtle_shadow(self.app_status_bar, blur_radius=16.0, y_offset=-1.0)

    def _create_menu_bar(self) -> QMenuBar:
        """Create a compact menu bar with flat action lists for quick access."""
        menu_bar = QMenuBar()
        menu_bar.setObjectName("mainMenuBar")
        # Keep the menu rendered inside our custom title bar.
        # Without this, some desktop environments may move it to a system/global
        # menubar, making it appear missing from the app window.
        menu_bar.setNativeMenuBar(False)

        file_menu = menu_bar.addMenu("File")
        file_menu.addAction("Exit", self.close)

        view_menu = menu_bar.addMenu("View")
        self.scanner_action = QAction("Scanner", self)
        self.scanner_action.setCheckable(True)
        self.scanner_action.setChecked(True)
        self.scanner_action.toggled.connect(self._set_scanner_visible)
        view_menu.addAction(self.scanner_action)

        self.watchlist_action = QAction("Watchlist", self)
        self.watchlist_action.setCheckable(True)
        self.watchlist_action.setChecked(True)
        self.watchlist_action.toggled.connect(self._set_watchlist_visible)
        view_menu.addAction(self.watchlist_action)

        self.positions_action = QAction("Positions", self)
        self.positions_action.setCheckable(True)
        self.positions_action.setChecked(True)
        self.positions_action.toggled.connect(self._set_positions_visible)
        view_menu.addAction(self.positions_action)

        self.dual_chart_action = QAction("Dual Chart Mode", self)
        self.dual_chart_action.setCheckable(True)
        self.dual_chart_action.setChecked(self.dual_chart_mode_enabled)
        self.dual_chart_action.toggled.connect(self._set_dual_chart_mode)
        view_menu.addAction(self.dual_chart_action)

        tools_menu = menu_bar.addMenu("Tools")
        open_order_action = tools_menu.addAction("Open Order Dialog", self._show_order_dialog)
        open_order_action.setShortcut(QKeySequence("F3"))
        open_order_action.setShortcutVisibleInContextMenu(True)
        pending_orders_action = tools_menu.addAction("Pending Orders", self._show_pending_orders_dialog)
        pending_orders_action.setShortcut(QKeySequence("Shift+N"))
        pending_orders_action.setShortcutVisibleInContextMenu(True)
        tools_menu.addSeparator()

        floating_positions_action = tools_menu.addAction("Floating Positions", self._show_floating_positions_dialog)
        floating_positions_action.setShortcut(QKeySequence("Ctrl+P"))
        floating_positions_action.setShortcutVisibleInContextMenu(True)

        stock_info_action = tools_menu.addAction("Stock Info", self._show_stock_info_for_active_symbol)
        stock_info_action.setShortcuts([QKeySequence("Ctrl+I"), QKeySequence("Shift+I")])
        stock_info_action.setShortcutVisibleInContextMenu(True)

        scans_list_action = tools_menu.addAction("Scans List", self._show_scans_list_dialog)
        scans_list_action.setShortcut(QKeySequence("Shift+S"))
        scans_list_action.setShortcutVisibleInContextMenu(True)

        floating_watchlist_action = tools_menu.addAction("Floating Watchlist", self._show_floating_watchlist_dialog)
        floating_watchlist_action.setShortcut(QKeySequence("Shift+W"))
        floating_watchlist_action.setShortcutVisibleInContextMenu(True)
        tools_menu.addSeparator()

        order_history_action = tools_menu.addAction("Order History", self._show_order_history_dialog)
        order_history_action.setShortcut(QKeySequence("Ctrl+H"))
        order_history_action.setShortcutVisibleInContextMenu(True)
        pnl_history_action = tools_menu.addAction("P&L History", self._show_pnl_history_dialog)
        pnl_history_action.setShortcut(QKeySequence("Shift+L"))
        pnl_history_action.setShortcutVisibleInContextMenu(True)
        performance_action = tools_menu.addAction("Performance", self._show_performance_dialog)
        performance_action.setShortcut(QKeySequence("Ctrl+D"))
        performance_action.setShortcutVisibleInContextMenu(True)
        tools_menu.addSeparator()

        settings_action = tools_menu.addAction("Settings", self._open_color_settings_dialog)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.setShortcutVisibleInContextMenu(True)

        about_menu = menu_bar.addMenu("About")
        about_menu.addAction("Keyboard Shortcuts", lambda: show_keyboard_shortcuts_dialog(self))
        about_menu.addAction("Sectors & Industries", lambda: show_sectors_industries_dialog(self))
        about_menu.addSeparator()
        about_menu.addAction("About qullamaggie", lambda: show_about_dialog(self))

        return menu_bar

    def _setup_status_indicators(self) -> None:
        """Drive subtle bottom-bar operational indicators."""
        self._market_status_timer = QTimer(self)
        self._market_status_timer.timeout.connect(self._refresh_market_status)
        self._market_status_timer.start(1_000)
        self._refresh_market_status()

    def _refresh_market_status(self) -> None:
        """Update bottom status bar from the US stock-market clock (America/New_York)."""
        now = market_now()
        status.set_market_indicator(market_session_label(now))
        if hasattr(self, "app_status_bar"):
            self.app_status_bar.set_market_clock(now.strftime("%H:%M:%S ET"))

    def _clamp_scanner_panel_width(self, width: Any = None) -> int:
        """Return a safe, compact scanner width for persisted/splitter values."""
        try:
            value = int(width) if width not in (None, "") else _SCANNER_PANEL_DEFAULT_WIDTH
        except (TypeError, ValueError):
            value = _SCANNER_PANEL_DEFAULT_WIDTH
        return max(_SCANNER_PANEL_MIN_WIDTH, min(value, _SCANNER_PANEL_MAX_WIDTH))

    def _clamp_right_panel_width(self, width: Any = None) -> int:
        """Return a safe right-side width while allowing it to be wider than scanner."""
        splitter_width = self.main_splitter.size().width() if hasattr(self, "main_splitter") else 0
        max_width = max(_RIGHT_PANEL_MIN_WIDTH, int(splitter_width * 0.34)) if splitter_width > 0 else 520
        try:
            value = int(width) if width not in (None, "") else _RIGHT_PANEL_DEFAULT_WIDTH
        except (TypeError, ValueError):
            value = _RIGHT_PANEL_DEFAULT_WIDTH
        return max(_RIGHT_PANEL_MIN_WIDTH, min(value, max_width))

    def _sanitize_main_splitter_sizes(self, raw_sizes=None) -> List[int]:
        """Clamp restored/saved splitter sizes so side panels cannot drift outward."""
        sizes = list(raw_sizes or self.main_splitter.sizes())
        if len(sizes) != 4:
            sizes = [_SCANNER_PANEL_DEFAULT_WIDTH, 900, 0, _RIGHT_PANEL_DEFAULT_WIDTH]

        try:
            sizes = [max(0, int(v)) for v in sizes]
        except (TypeError, ValueError):
            sizes = [_SCANNER_PANEL_DEFAULT_WIDTH, 900, 0, _RIGHT_PANEL_DEFAULT_WIDTH]

        splitter_width = self.main_splitter.size().width() if hasattr(self, "main_splitter") else 0
        total = max(1, splitter_width, sum(sizes))

        left_visible = bool(getattr(self, "finviz_scanner", None) and self.finviz_scanner.isVisible())
        right_visible = bool(getattr(self, "right_panel_splitter", None) and self.right_panel_splitter.isVisible())

        left = self._clamp_scanner_panel_width(sizes[0]) if left_visible else 0
        right = self._clamp_right_panel_width(sizes[3]) if right_visible else 0

        # Always protect the chart lane first. If side panels would squeeze the
        # center, reduce the right panel before touching the scanner width.
        center_floor = 520 if not self.dual_chart_mode_enabled else 920
        if left + right + center_floor > total:
            deficit = (left + right + center_floor) - total
            right_reduction = min(max(0, right - _RIGHT_PANEL_MIN_WIDTH), deficit)
            right -= right_reduction
            deficit -= right_reduction
            if deficit > 0:
                left = max(_SCANNER_PANEL_MIN_WIDTH if left_visible else 0, left - deficit)

        chart_total = max(0, total - left - right)
        if self.dual_chart_mode_enabled:
            old_primary = max(1, sizes[1])
            old_secondary = max(1, sizes[2])
            primary_ratio = old_primary / max(1, old_primary + old_secondary)
            primary = int(round(chart_total * primary_ratio))
            secondary = chart_total - primary
        else:
            primary = chart_total
            secondary = 0

        return [int(left), int(primary), int(secondary), int(right)]

    def _apply_intelligent_main_splitter_layout(self, preferred_sizes=None):
        """Keep scanner/watchlist compact and protect chart space during resize/drag."""
        if self._is_adjusting_splitter:
            return
        if self.main_splitter.size().width() <= 0:
            return

        current_sizes = self.main_splitter.sizes()
        sizes = self._sanitize_main_splitter_sizes(preferred_sizes or current_sizes)
        if len(current_sizes) == 4 and [int(v) for v in current_sizes] == sizes:
            return

        self._is_adjusting_splitter = True
        try:
            self.main_splitter.setSizes(sizes)
        finally:
            self._is_adjusting_splitter = False

    def _set_scanner_visible(self, visible: bool):
        if visible:
            self.finviz_scanner.setMinimumWidth(_SCANNER_PANEL_MIN_WIDTH)
            self.finviz_scanner.setMaximumWidth(_SCANNER_PANEL_MAX_WIDTH)
        self.finviz_scanner.setVisible(visible)

        sizes = self.main_splitter.sizes()
        if len(sizes) == 4:
            if visible:
                sizes[0] = self._clamp_scanner_panel_width(self._saved_scanner_panel_width)
            else:
                if sizes[0] > 0:
                    self._saved_scanner_panel_width = self._clamp_scanner_panel_width(sizes[0])
                sizes[0] = 0
            self.main_splitter.setSizes(self._sanitize_main_splitter_sizes(sizes))

        self._apply_intelligent_main_splitter_layout()
        # Rebuild immediately so scanner tokens subscribe/unsubscribe exactly
        # when the user toggles visibility from View → Scanner.
        self._rebuild_subscription_universe()
        if visible:
            self._schedule_visible_contract_preload()
        self._queue_window_state_save()

    def _set_watchlist_visible(self, visible: bool):
        self.watchlist.setVisible(visible)
        self._sync_right_panel_visibility()
        # Rebuild immediately so watchlist tokens subscribe/unsubscribe exactly
        # when the user toggles visibility from View → Watchlist.
        self._rebuild_subscription_universe()
        if visible:
            self._schedule_visible_contract_preload()
        self._queue_window_state_save()

    def _set_positions_visible(self, visible: bool):
        # Hide/show the full positions pane (title + table) from View → Positions.
        self.positions_panel.setVisible(visible)
        if not visible:
            self.app_status_bar.set_positions_metrics(False)
        self._sync_right_panel_visibility()
        self._queue_window_state_save()

    def _sync_right_panel_visibility(self):
        watchlist_visible = self.watchlist_action.isChecked()
        positions_visible = self.positions_action.isChecked()

        right_visible = watchlist_visible or positions_visible
        self.right_panel_splitter.setVisible(right_visible)

        if right_visible:
            if self._saved_watchlist_panel_width and self.watchlist_action.isChecked():
                sizes = self.main_splitter.sizes()
                if len(sizes) == 4:
                    total = max(1, sum(sizes))
                    left = sizes[0]
                    right = max(220, int(self._saved_watchlist_panel_width))
                    chart_total = max(520, total - left - right)
                    right = max(220, total - left - chart_total)
                    primary = chart_total if not self.dual_chart_mode_enabled else max(520, chart_total // 2)
                    secondary = 0 if not self.dual_chart_mode_enabled else max(520, chart_total - primary)
                    self.main_splitter.setSizes([left, primary, secondary, right])
            # Recover splitter sizes after both right-side panes were hidden.
            pane_sizes = self.right_panel_splitter.sizes()
            if len(pane_sizes) == 2:
                if watchlist_visible and positions_visible:
                    if pane_sizes[0] == 0 and pane_sizes[1] == 0:
                        self.right_panel_splitter.setSizes([_RIGHT_PANEL_DEFAULT_WIDTH, 220])
                elif watchlist_visible:
                    self.right_panel_splitter.setSizes([1, 0])
                elif positions_visible:
                    self.right_panel_splitter.setSizes([0, 1])

        self._apply_intelligent_main_splitter_layout()

    def _on_main_splitter_moved(self, _pos: int, _index: int):
        """Persist user splitter drags, but keep side panes inside safe limits."""
        sizes = self.main_splitter.sizes()
        if len(sizes) == 4:
            if self.finviz_scanner.isVisible():
                self._saved_scanner_panel_width = self._clamp_scanner_panel_width(sizes[0])
            if self.right_panel_splitter.isVisible():
                self._saved_watchlist_panel_width = self._clamp_right_panel_width(sizes[3])
        self._apply_intelligent_main_splitter_layout()

    def _queue_window_state_save(self, *_args):
        """Debounce frequent splitter drags and persist layout shortly after movement."""
        if hasattr(self, '_window_state_save_timer'):
            self._window_state_save_timer.start()

    def resizeEvent(self, event):
        """Re-balance pane widths when the window geometry changes."""
        super().resizeEvent(event)
        self._update_title_bar_compact_state()
        if hasattr(self, 'main_splitter'):
            self._apply_intelligent_main_splitter_layout()

    def showEvent(self, event):
        """Recompute title/menu geometry once the window is visible."""
        super().showEvent(event)
        self._update_title_bar_compact_state()
        self._apply_pending_splitter_sizes()

    def _apply_pending_splitter_sizes(self):
        """Apply restored splitter sizes once widgets have a real on-screen size."""
        try:
            if self._pending_main_splitter_sizes:
                self.main_splitter.setSizes(self._sanitize_main_splitter_sizes(self._pending_main_splitter_sizes))
                self._pending_main_splitter_sizes = None

            if hasattr(self, 'right_panel_splitter') and self._pending_right_splitter_sizes:
                self.right_panel_splitter.setSizes(self._pending_right_splitter_sizes)
                self._pending_right_splitter_sizes = None

            self._apply_intelligent_main_splitter_layout()
        except Exception as e:
            logger.warning(f"Failed applying pending splitter sizes: {e}")

    def _create_top_bar(self) -> QWidget:
        """Create a top bar with centered app title/mode and anchored menu/window controls."""
        top_bar = QWidget()
        top_bar.setObjectName("customTitleBar")
        top_bar.setFixedHeight(28)

        root_layout = QGridLayout(top_bar)
        root_layout.setContentsMargins(7, 0, 4, 0)
        root_layout.setHorizontalSpacing(6)
        root_layout.setVerticalSpacing(0)

        self.menu_container = QWidget()
        self.menu_container.setObjectName("menuContainer")
        self.menu_container.setMinimumWidth(210)
        self.menu_container.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
        menu_layout = QHBoxLayout(self.menu_container)
        menu_layout.setContentsMargins(0, 0, 0, 0)
        menu_layout.setSpacing(0)
        self.menu_bar.setFixedHeight(24)
        self.menu_bar.setMinimumWidth(200)
        self.menu_bar.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
        if hasattr(self.menu_bar, "setSizeAdjustPolicy"):
            self.menu_bar.setSizeAdjustPolicy(QMenuBar.SizeAdjustPolicy.AdjustToContents)
        menu_layout.addWidget(self.menu_bar)

        self.title_container = QWidget()
        title_layout = QHBoxLayout(self.title_container)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(4)

        self.title_label = QLabel("Qullamaggie")
        self.title_label.setObjectName("appTitle")
        title_layout.addWidget(self.title_label)

        self.mode_label = QLabel(f"[{self.trading_mode.upper()}]")
        self.mode_label.setObjectName("tradingModeLabel")
        title_layout.addWidget(self.mode_label)

        self.window_controls = QWidget()
        controls_layout = QHBoxLayout(self.window_controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(4)

        min_btn = QPushButton("−")
        min_btn.setObjectName("titleBarButton")
        min_btn.setFixedSize(24, 22)
        min_btn.clicked.connect(self.showMinimized)
        controls_layout.addWidget(min_btn)

        self.max_btn = QPushButton("□")
        self.max_btn.setObjectName("titleBarButton")
        self.max_btn.setFixedSize(24, 22)
        self.max_btn.clicked.connect(self._toggle_maximize)
        controls_layout.addWidget(self.max_btn)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeTitleBarButton")
        close_btn.setFixedSize(24, 22)
        close_btn.clicked.connect(self.close)
        controls_layout.addWidget(close_btn)

        root_layout.addWidget(self.menu_container, 0, 0, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        root_layout.addWidget(self.title_container, 0, 1, alignment=Qt.AlignmentFlag.AlignCenter)
        root_layout.addWidget(self.window_controls, 0, 2, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        root_layout.setColumnMinimumWidth(0, 210)
        root_layout.setColumnMinimumWidth(2, 92)
        root_layout.setColumnStretch(0, 1)
        root_layout.setColumnStretch(1, 0)
        root_layout.setColumnStretch(2, 1)

        self._drag_widgets = [top_bar, self.title_container, self.title_label, self.mode_label]
        for widget in self._drag_widgets:
            widget.installEventFilter(self)

        top_bar.mousePressEvent = self._title_bar_mouse_press
        top_bar.mouseMoveEvent = self._title_bar_mouse_move
        top_bar.mouseReleaseEvent = self._title_bar_mouse_release
        top_bar.mouseDoubleClickEvent = self._title_bar_double_click
        self._update_title_bar_compact_state()
        return top_bar

    def _update_title_bar_compact_state(self):
        """Keep menu labels visible and collapse non-critical title labels on narrow windows."""
        if not hasattr(self, "title_label") or not hasattr(self, "mode_label"):
            return

        compact = self.width() < 1280
        self.title_label.setVisible(not compact)
        self.mode_label.setVisible(not compact)

    def eventFilter(self, obj, event):
        if obj in getattr(self, '_drag_widgets', []):
            if event.type() == QEvent.Type.MouseButtonPress:
                self._title_bar_mouse_press(event)
                return True
            if event.type() == QEvent.Type.MouseMove:
                self._title_bar_mouse_move(event)
                return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._title_bar_mouse_release(event)
                return True
            if event.type() == QEvent.Type.MouseButtonDblClick:
                self._title_bar_double_click(event)
                return True
        return super().eventFilter(obj, event)

    @Slot(dict)
    def _on_color_theme_changed(self, theme: Dict[str, Any]):
        self.header_toolbar.apply_color_theme(theme)
        self.finviz_scanner.apply_color_theme(theme)
        self.watchlist.apply_color_theme(theme)
        self.positions_table.apply_color_theme(theme)
        self.app_status_bar.apply_color_theme(theme)
        if self.floating_watchlist_dialog is not None:
            self.floating_watchlist_dialog.apply_color_theme(theme)
        self.candlestick_chart.apply_color_theme(theme)
        self.candlestick_chart_secondary.apply_color_theme(theme)
        self.finviz_scanner.set_live_ticks_enabled(
            bool(theme.get("scanner_live_ticks", True))
        )
        # Apply scanner live-tick subscription changes immediately after
        # Settings dialog updates, without waiting for symbol/navigation events.
        self._rebuild_subscription_universe()
        alignment = str(theme.get("status_bar_alignment", "left"))
        self.app_status_bar.set_elements_alignment(alignment)
        self.app_status_bar.set_metrics_alignment(bool(theme.get("status_bar_metrics_right", True)))

    @Slot(dict)
    def _on_positions_footer_metrics_changed(self, payload: Dict[str, Any]):
        has_data = bool(payload.get("has_data", False)) and self.positions_action.isChecked()
        open_pnl = float(payload.get("open_pnl", 0.0) or 0.0)
        live_mtm = float(payload.get("day_unrealized", open_pnl) or 0.0)
        self.app_status_bar.set_positions_metrics(
            has_data=has_data,
            open_pnl=open_pnl,
            exposure=float(payload.get("exposure", 0.0) or 0.0),
            day_unrealized=live_mtm,
            day_realized=float(getattr(self, "_status_day_realized_total", 0.0) or 0.0),
        )

    @Slot(object)
    def _on_day_pnl_updated(self, pnl_data: Any):
        """Fast-path status metrics update using broker-reported day P&L aggregates."""
        unrealized = 0.0
        realized = float(getattr(self, "_status_day_realized_total", 0.0) or 0.0)
        if isinstance(pnl_data, dict):
            unrealized = float(pnl_data.get("unrealized", 0.0) or 0.0)
            for realized_key in ("realized", "realised", "day_realized", "day_realised"):
                if realized_key in pnl_data:
                    realized = float(pnl_data.get(realized_key, 0.0) or 0.0)
                    self._status_day_realized_total = realized
                    break
        elif isinstance(pnl_data, (int, float)):
            # PositionManager can emit a float aggregate for unrealized day P&L.
            unrealized = float(pnl_data)

        if not self.positions_action.isChecked():
            return

        self.app_status_bar.set_positions_metrics(
            has_data=True,
            day_unrealized=unrealized,
            day_realized=realized,
            exposure=getattr(self.app_status_bar, "_last_exposure", 0.0) or 0.0,
        )

    def _open_color_settings_dialog(self):
        dialog = ColorSettingsDialog(self.color_theme_manager.get_theme(), self)
        if dialog.exec():
            updated_theme = dialog.get_theme()
            self.color_theme_manager.update_theme(updated_theme)
            self._set_dual_chart_mode(bool(updated_theme.get("dual_chart_mode", False)))
            if hasattr(self, "dual_chart_action"):
                self.dual_chart_action.blockSignals(True)
                self.dual_chart_action.setChecked(self.dual_chart_mode_enabled)
                self.dual_chart_action.blockSignals(False)


    def _init_alert_system(self):
        try:
            self.alert_system = AlertSystemManager(self)
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

        # IBKR instruments should be fetched on-demand (not preloaded from Kite).
        self.instrument_loader = None
        self._initialize_ibkr_instruments()

        # Only start market data worker if actually connected.
        if self.real_kite_client and self.real_kite_client.isConnected():
            self.market_data_worker = MarketDataWorker(self.real_kite_client)
            self.market_data_worker.data_received.connect(self._enqueue_market_data)
            self.market_data_worker.connection_established.connect(self._on_websocket_connect)
            self.market_data_worker.market_data_type_changed.connect(self._on_market_data_type_changed)
            self._connect_position_worker_signals()
            self.market_data_worker.start()
        else:
            logger.warning("IB client not connected — market data worker not started")
            self.market_data_worker = None
            QTimer.singleShot(3000, self._retry_start_market_data_worker)

    def _retry_start_market_data_worker(self):
        if self.real_kite_client and self.real_kite_client.isConnected():
            self.market_data_worker = MarketDataWorker(self.real_kite_client)
            self.market_data_worker.data_received.connect(self._enqueue_market_data)
            self.market_data_worker.connection_established.connect(self._on_websocket_connect)
            self.market_data_worker.market_data_type_changed.connect(self._on_market_data_type_changed)
            self._connect_position_worker_signals()
            self.market_data_worker.start()
        else:
            logger.error("IB still not connected after retry")

    def _connect_position_worker_signals(self):
        """Wire IBKR worker position/order events into the table reconciliation path."""
        worker = getattr(self, "market_data_worker", None)
        if not worker or getattr(worker, "_positions_sync_connected", False):
            return
        try:
            worker.order_update.connect(self.position_manager.on_ws_order_update)
        except Exception:
            logger.debug("IBKR order update signal was already connected", exc_info=True)
        if hasattr(worker, "position_update"):
            try:
                worker.position_update.connect(self.position_manager.on_ws_position_update)
            except Exception:
                logger.debug("IBKR position update signal was already connected", exc_info=True)
        try:
            worker.connection_established.connect(self.position_manager.on_ws_connected)
            worker.connection_closed.connect(self.position_manager.on_ws_disconnected)
        except Exception:
            logger.debug("IBKR connection signals were already connected", exc_info=True)
        worker._positions_sync_connected = True



    def _initialize_ibkr_instruments(self):
        from ibkr.utils.ibkr_instrument_loader import IBKRInstrumentLoader
        from ibkr.utils.ibkr_symbol_resolver import IBKRSymbolResolver

        # Create live search resolver
        self.ibkr_symbol_resolver = IBKRSymbolResolver(self.real_kite_client, parent=self)

        # Wire search bar to use live IBKR search
        self.header_toolbar.set_live_search_callback(self._ibkr_live_search)

        # Load seed instruments
        self.ibkr_instrument_loader = IBKRInstrumentLoader(self.real_kite_client)
        self.ibkr_instrument_loader.instruments_loaded.connect(self._on_instruments_loaded)
        self.ibkr_instrument_loader.progress_update.connect(
            lambda msg: logger.info("IBKR loader: %s", msg)
        )
        self.ibkr_instrument_loader.start()

    def _ibkr_live_search(self, query: str, callback) -> None:
        """Called by search bar for every keystroke — hits IBKR API live."""
        query = (query or "").strip().upper()
        if not query:
            callback([])
            return

        # Check local instrument map first (instant).  This map is populated
        # from the symbol-info database at startup, so it is the authoritative
        # source for prefix suggestions even if the live IBKR lookup is slow or
        # returns an empty prefix response.
        local_matches = [
            inst for sym, inst in self.instrument_map.items()
            if str(sym or "").upper().startswith(query)
        ][:20]
        if local_matches:
            callback(local_matches)

        # Also search IBKR for anything not in local map.  The completion
        # callback receives local + live rows, never live rows alone, so a later
        # empty live response cannot clear the local suggestions from the UI.
        self.ibkr_symbol_resolver.search(
            query,
            lambda results, local=local_matches: self._on_ibkr_search_results(
                results, query, callback, local
            )
        )

    def _on_ibkr_search_results(
        self,
        results: list,
        query: str,
        callback,
        local_matches: list | None = None,
    ) -> None:
        """Merge live IBKR search results into instrument map and call back."""
        merged = []
        seen = set()

        def add(inst):
            sym = str(inst.get("tradingsymbol") or inst.get("symbol") or "").strip().upper()
            if not sym or sym in seen:
                return
            normalized = {**inst, "tradingsymbol": sym, "symbol": sym}
            seen.add(sym)
            merged.append(normalized)
            if sym not in self.instrument_map:
                self.instrument_map[sym] = normalized

        for inst in local_matches or []:
            add(inst)
        for inst in results or []:
            add(inst)

        callback(merged[:20])

    @Slot()
    def _on_websocket_connect(self):
        """WebSocket connection handler"""
        logger.info("WebSocket connected. Setting up subscriptions.")
        status.set_api_indicator("CONNECTED")

        if (hasattr(self, 'candlestick_chart') and
                hasattr(self.candlestick_chart, 'current_instrument_token') and
                self.candlestick_chart.current_instrument_token):
            try:
                self.market_data_worker.add_instruments([self.candlestick_chart.current_instrument_token])
                logger.info(f"Subscribed to chart token: {self.candlestick_chart.current_instrument_token}")
            except Exception as e:
                logger.error(f"Failed to subscribe to chart: {e}")
        self._schedule_subscription_rebuild()

    @Slot(str, bool)
    def _on_market_data_type_changed(self, data_type: str, live: bool):
        """Reflect IBKR live/delayed market-data mode in persistent UI status."""
        label = "LIVE" if live else "DELAYED"
        if hasattr(self, "app_status_bar"):
            self.app_status_bar.set_data_status(label, live)
        if hasattr(self, "header_toolbar"):
            self.header_toolbar.set_data_status(label, live)
        status.set_data_indicator(label, live)
        if not live:
            logger.warning("IBKR market data is delayed (%s); live subscription is unavailable.", data_type)
        else:
            logger.info("IBKR market data is live (%s).", data_type)

    def _connect_chart_signals(self):
        """Connect chart signals"""
        if self.candlestick_chart:
            # One-shot: reveal chart panes after first successful load
            def _reveal_charts(symbol):
                self.candlestick_chart.setVisible(True)
                if self.dual_chart_mode_enabled:
                    self.candlestick_chart_secondary.setVisible(True)
                # Disconnect so it only fires once
                try:
                    self.candlestick_chart.symbol_loaded.disconnect(_reveal_charts)
                except RuntimeError:
                    pass

            self.candlestick_chart.symbol_loaded.connect(_reveal_charts)
            self.candlestick_chart.symbol_loaded.connect(self._on_chart_symbol_changed)
            self.candlestick_chart.data_request_for_symbol.connect(self._ensure_chart_subscription)
            if self.candlestick_chart_secondary:
                self.candlestick_chart_secondary.data_request_for_symbol.connect(self._ensure_chart_subscription)
            # FIX #9: redraw alert lines whenever the chart switches symbol
            if self.alert_system:
                # Restore alert lines only after chart JS is fully initialized
                self.candlestick_chart.chart_bridge_ready.connect(
                    lambda: QTimer.singleShot(200, self._restore_alert_lines)
                )
                # Also restore on each symbol load (handles interval switches too)
                self.candlestick_chart.symbol_loaded.connect(
                    self.alert_system.refresh_alert_lines_for_symbol
                )
                if getattr(self, 'candlestick_chart_secondary', None):
                    self.candlestick_chart_secondary.symbol_loaded.connect(
                        self.alert_system.refresh_alert_lines_for_symbol
                    )
                # ALERT DRAG SYNC: chart line drag → alert manager price update
                if hasattr(self.candlestick_chart, 'alert_price_updated'):
                    self.candlestick_chart.alert_price_updated.connect(
                        self.alert_system.update_alert_price_from_chart
                    )
                if hasattr(self.candlestick_chart, 'alert_line_deleted'):
                    self.candlestick_chart.alert_line_deleted.connect(
                        self._on_alert_line_deleted_from_chart
                    )
                if getattr(self, 'candlestick_chart_secondary', None):
                    if hasattr(self.candlestick_chart_secondary, 'alert_price_updated'):
                        self.candlestick_chart_secondary.alert_price_updated.connect(
                            self.alert_system.update_alert_price_from_chart
                        )
                    if hasattr(self.candlestick_chart_secondary, 'alert_line_deleted'):
                        self.candlestick_chart_secondary.alert_line_deleted.connect(
                            self._on_alert_line_deleted_from_chart
                        )
            if hasattr(self.candlestick_chart, 'stop_loss_price_updated'):
                self.candlestick_chart.stop_loss_price_updated.connect(
                    self._on_stop_loss_line_moved_from_chart
                )
            if hasattr(self.candlestick_chart, 'stop_loss_line_deleted'):
                self.candlestick_chart.stop_loss_line_deleted.connect(
                    self._on_stop_loss_line_deleted_from_chart
                )
            if hasattr(self.candlestick_chart, 'target_price_updated'):
                self.candlestick_chart.target_price_updated.connect(
                    self._on_target_line_moved_from_chart
                )
            if hasattr(self.candlestick_chart, 'target_line_deleted'):
                self.candlestick_chart.target_line_deleted.connect(
                    self._on_target_line_deleted_from_chart
                )

    def _restore_alert_lines(self) -> None:
        """Redraw all active alert lines after chart is confirmed ready."""
        if self.alert_system:
            current_symbols = {
                getattr(self.candlestick_chart, 'current_symbol', ''),
                getattr(getattr(self, 'candlestick_chart_secondary', None), 'current_symbol', ''),
            }
            for symbol in current_symbols:
                if symbol:
                    self.alert_system.refresh_alert_lines_for_symbol(symbol)

    @Slot(str)
    def _on_alert_line_deleted_from_chart(self, payload: str) -> None:
        """Delete matching alert when its alert line is removed from the chart."""
        if not self.alert_system:
            return
        try:
            data = json.loads(payload or "{}")
            symbol = str(data.get("symbol", "")).strip().upper()
            price = float(data.get("price", 0.0))
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.error(f"Invalid alert_line_deleted payload: {exc}")
            return

        if not symbol or price <= 0:
            return

        tolerance = 0.5
        match = next(
            (
                alert for alert in self.alert_system.store.active()
                if alert.symbol == symbol and abs(float(alert.target_value) - price) <= tolerance
            ),
            None,
        )
        if not match:
            logger.info(f"No active alert matched deleted chart line for {symbol} @ {price:.2f}")
            return

        self.alert_system.remove_alert(match.id)
        show_info(f"Alert deleted: {symbol} @ ₹{price:.2f}")

    def _find_stop_loss_record_for_chart_line(self, symbol: str, price: float):
        """Find the active stop-loss record matching a dragged/deleted chart line."""
        if not getattr(self, "sl_manager", None):
            return None
        tolerance = 0.5
        matches = [
            rec for rec in self.sl_manager.get_all_active()
            if str(rec.symbol).upper() == symbol and abs(float(rec.sl_price) - price) <= tolerance
        ]
        if matches:
            return matches[0]

        # Fallback for the common case of only one active SL per symbol.
        symbol_matches = [
            rec for rec in self.sl_manager.get_all_active()
            if str(rec.symbol).upper() == symbol
        ]
        return symbol_matches[0] if len(symbol_matches) == 1 else None

    @Slot(str)
    def _on_stop_loss_line_moved_from_chart(self, payload: str) -> None:
        """Modify the StopLossManager record when a chart SL line is dragged."""
        try:
            data = json.loads(payload or "{}")
            symbol = str(data.get("symbol", "")).strip().upper()
            old_price = float(data.get("old_price", 0.0))
            new_price = float(data.get("new_price", 0.0))
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.error(f"Invalid stop_loss_price_updated payload: {exc}")
            return

        if not symbol or old_price <= 0 or new_price <= 0:
            return

        rec = self._find_stop_loss_record_for_chart_line(symbol, old_price)
        if not rec:
            logger.info("No active stop-loss matched moved chart line for %s @ %.2f", symbol, old_price)
            return

        if self.sl_manager.modify_stop_loss(symbol, new_price, rec.product):
            self.chart_lines_manager.add_stop_loss_line(symbol, float(new_price), rec.position_id)
            self._refresh_floating_positions_sl_values(symbol)
            show_info(f"Stop-loss updated: {symbol} @ ${new_price:.2f}")
            return

        # Validation failed (for example, dragged beyond entry). Restore the persisted SL line.
        try:
            self.chart_lines_manager.add_stop_loss_line(symbol, float(rec.sl_price), rec.position_id)
        except Exception as exc:
            logger.error(f"Failed to restore stop-loss line for {symbol}: {exc}")

    @Slot(str)
    def _on_stop_loss_line_deleted_from_chart(self, payload: str) -> None:
        """Cancel the matching stop-loss when its chart line is deleted."""
        try:
            data = json.loads(payload or "{}")
            symbol = str(data.get("symbol", "")).strip().upper()
            price = float(data.get("price", 0.0))
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.error(f"Invalid stop_loss_line_deleted payload: {exc}")
            return

        if not symbol or price <= 0:
            return

        rec = self._find_stop_loss_record_for_chart_line(symbol, price)
        if not rec:
            logger.info("No active stop-loss matched deleted chart line for %s @ %.2f", symbol, price)
            return

        if self.sl_manager.cancel_stop_loss(symbol, rec.product):
            self.chart_lines_manager.remove_stop_loss_line(symbol, rec.position_id)
            self._refresh_floating_positions_sl_values(symbol)
            show_info(f"Stop-loss removed: {symbol}")

    def _refresh_floating_positions_sl_values(self, symbol: str = "") -> None:
        """Refresh the floating positions SL column after SL-only changes."""
        dialog = getattr(self, "floating_positions_dialog", None)
        if dialog is not None and hasattr(dialog, "refresh_stop_loss_values"):
            try:
                dialog.refresh_stop_loss_values(symbol)
                return
            except Exception as exc:
                logger.error(f"Failed to refresh floating positions SL cells: {exc}")

        self._update_floating_positions_dialog(
            getattr(self.positions_table, 'positions_data', {}).values()
        )

    @Slot(str)
    def _on_target_line_moved_from_chart(self, payload: str) -> None:
        try:
            data = json.loads(payload or "{}")
            symbol = str(data.get("symbol", "")).strip().upper()
            new_price = float(data.get("new_price", 0.0))
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.error(f"Invalid target_price_updated payload: {exc}")
            return
        if not symbol or new_price <= 0:
            return
        self._target_prices[symbol] = new_price
        self.chart_lines_manager.add_target_line(symbol, new_price)
        dialog = getattr(self, "floating_positions_dialog", None)
        if dialog is not None and hasattr(dialog, "set_target_value"):
            dialog.set_target_value(symbol, new_price)

    @Slot(str)
    def _on_target_line_deleted_from_chart(self, payload: str) -> None:
        try:
            data = json.loads(payload or "{}")
            symbol = str(data.get("symbol", "")).strip().upper()
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.error(f"Invalid target_line_deleted payload: {exc}")
            return
        if not symbol:
            return
        self._target_prices.pop(symbol, None)
        self.chart_lines_manager.remove_target_line(symbol)
        dialog = getattr(self, "floating_positions_dialog", None)
        if dialog is not None and hasattr(dialog, "clear_target_value"):
            dialog.clear_target_value(symbol)

    @Slot(str)
    def _on_chart_symbol_changed(self, symbol: str):
        """Handle chart symbol changes and warm IBKR caches early."""
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return

        self._queue_contract_preload([symbol])
        self._prefetch_chart_data_background(symbol)

        self._chart_tick_queue.clear()
        logger.info(f"Chart symbol changed to: {symbol}")
        try:
            item = self._build_subscription_item(symbol)
            if item is not None and self.market_data_worker and self.market_data_worker.is_connected():
                self.market_data_worker.add_instruments([item])
                self.market_data_worker.request_snapshots([item])
                logger.info(f"Added chart symbol {symbol} to subscription")
            self._schedule_subscription_rebuild()
        except Exception as e:
            logger.error(f"Failed to subscribe to chart symbol {symbol}: {e}")

    @Slot(str)
    def _ensure_chart_subscription(self, symbol: str):
        """Ensure chart symbol is subscribed."""
        clean_symbol = str(symbol or "").strip().upper()
        if not clean_symbol or not self.market_data_worker:
            return

        instrument = self.instrument_map.get(clean_symbol, {}) or {}
        token = instrument.get('instrument_token')
        item: Any
        if token:
            item = {
                "instrument_token": token,
                "conId": token,
                "tradingsymbol": clean_symbol,
                "symbol": clean_symbol,
                "exchange": instrument.get("exchange") or "SMART",
                "currency": instrument.get("currency") or "USD",
            }
        else:
            # IBKR can qualify raw symbols on demand; this keeps chart LTP live
            # even before a conId has landed in instrument_map.
            item = clean_symbol

        try:
            self.market_data_worker.add_instruments([item])
            self.market_data_worker.request_snapshots([item])
            if token:
                self._subscribed_tokens.add(token)
            logger.info(f"Ensured subscription for chart symbol {clean_symbol}")
            self._schedule_subscription_rebuild()
        except Exception as e:
            logger.error(f"Failed to ensure chart subscription for {clean_symbol}: {e}")


    def _refresh_header_ticker_ws_subscriptions(self) -> None:
        """Subscribe market-data items needed by the header ticker board."""
        if not hasattr(self, "header_toolbar"):
            return
        try:
            if hasattr(self.header_toolbar, "configure_ticker_ws_subscriptions"):
                items = self.header_toolbar.configure_ticker_ws_subscriptions(getattr(self, "instrument_map", {}) or {})
            else:
                items = self.header_toolbar.configure_ticker_ws_tokens(getattr(self, "instrument_map", {}) or {})
            if items and self.market_data_worker:
                self.market_data_worker.add_instruments(items)
                self.market_data_worker.request_snapshots(items)
        except Exception as exc:
            logger.error(f"Failed to configure header ticker subscriptions: {exc}")

    def _schedule_visible_contract_preload(self) -> None:
        """Queue visible scanner/watchlist contracts for async cache warming."""
        symbols = []
        symbols.extend(self._get_visible_watchlist_symbols())
        symbols.extend(self._get_visible_scanner_symbols())
        self._queue_contract_preload(symbols)

    def _bind_watchlist_contract_preload_tracking(self) -> None:
        """Track active watchlist tab/scroll changes so visible rows stay warm."""
        watchlist = getattr(self, "watchlist", None)
        if watchlist is None:
            return

        dropdown = getattr(watchlist, "_dropdown", None)
        if dropdown is not None and not getattr(dropdown, "_contract_preload_bound", False):
            dropdown.currentIndexChanged.connect(lambda _idx: self._schedule_visible_contract_preload())
            dropdown._contract_preload_bound = True

        for table in getattr(watchlist, "_tables", {}).values():
            scrollbar = table.verticalScrollBar() if hasattr(table, "verticalScrollBar") else None
            if scrollbar is not None and not getattr(scrollbar, "_contract_preload_bound", False):
                scrollbar.valueChanged.connect(lambda _value: self._schedule_visible_contract_preload())
                scrollbar._contract_preload_bound = True

    def _bind_watchlist_hover_preload_tracking(self) -> None:
        """Warm contracts when the user moves selection through watchlist rows."""
        watchlist = getattr(self, "watchlist", None)
        if watchlist is None:
            return

        for table in getattr(watchlist, "_tables", {}).values():
            if getattr(table, "_hover_contract_preload_bound", False):
                continue
            table.currentCellChanged.connect(
                lambda row, *_args, table=table: self._on_watchlist_row_hovered(row, table)
            )
            table._hover_contract_preload_bound = True

    def _on_symbol_hovered(self, symbol: str) -> None:
        """Pre-qualify IBKR contract when user hovers over scanner/watchlist rows."""
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return

        preloaded = getattr(self, "_preloaded_hover_symbols", None)
        if preloaded is None:
            self._preloaded_hover_symbols = set()
            preloaded = self._preloaded_hover_symbols
        if symbol in preloaded:
            return

        preloaded.add(symbol)
        self._queue_contract_preload([symbol])

    def _on_watchlist_row_hovered(self, row: int, table=None) -> None:
        if row < 0:
            return
        if table is None:
            table_getter = getattr(getattr(self, "watchlist", None), "_current_table", None)
            table = table_getter() if callable(table_getter) else None
        if table is None:
            return

        symbol_getter = getattr(table, "_symbol_at_row", None)
        symbol = symbol_getter(row) if callable(symbol_getter) else None
        if symbol:
            self._on_symbol_hovered(symbol)

    def _queue_contract_preload(self, symbols: List[str]) -> None:
        clean_symbols = {str(symbol or "").strip().upper() for symbol in symbols or []}
        clean_symbols.discard("")
        if not clean_symbols:
            return

        pending = getattr(self, "_pending_contract_preload_symbols", None)
        if pending is None:
            self._pending_contract_preload_symbols = set()
            pending = self._pending_contract_preload_symbols
        pending.update(clean_symbols)

        timer = getattr(self, "_contract_preload_timer", None)
        if timer is not None:
            timer.start()
        else:
            self._flush_contract_preload_queue()

    def _prefetch_chart_data_background(self, symbol: str) -> None:
        """Start warming chart dependencies before the foreground chart load blocks."""
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return

        fetcher = getattr(self, "chart_data_fetcher", None)
        preload = getattr(fetcher, "preload_contracts", None)
        if callable(preload):
            try:
                # IBKRDataFetcher schedules this on the dedicated history executor,
                # so callers can fire-and-forget from the UI thread.
                preload([symbol])
            except Exception as exc:
                logger.debug("Failed to prefetch chart data for %s: %s", symbol, exc)

    def _flush_contract_preload_queue(self) -> None:
        pending = getattr(self, "_pending_contract_preload_symbols", set())
        if not pending:
            return
        symbols = sorted(pending)
        pending.clear()

        fetcher = getattr(self, "chart_data_fetcher", None)
        preload = getattr(fetcher, "preload_contracts", None)
        if not callable(preload):
            return
        try:
            preload(symbols)
        except Exception as exc:
            logger.debug("Failed to queue IBKR contract preload for %s: %s", symbols, exc)

    def _get_visible_scanner_symbols(self) -> List[str]:
        scanner = getattr(self, "finviz_scanner", None)
        if scanner is None or not scanner.isVisible():
            return []
        getter = getattr(scanner, "get_visible_symbols", None)
        if callable(getter):
            try:
                return [str(symbol).strip().upper() for symbol in getter() if str(symbol).strip()]
            except Exception as exc:
                logger.debug("Could not collect visible scanner symbols for preload: %s", exc)
        return []

    def _get_visible_watchlist_symbols(self, buffer: int = 5) -> List[str]:
        watchlist = getattr(self, "watchlist", None)
        if watchlist is None or not watchlist.isVisible():
            return []

        table_getter = getattr(watchlist, "_current_table", None)
        table = table_getter() if callable(table_getter) else None
        if table is None:
            return []

        try:
            row_count = table.rowCount()
            if row_count <= 0:
                return []

            viewport = table.viewport() if hasattr(table, "viewport") else None
            if viewport is not None and viewport.height() > 0:
                top_row = table.rowAt(0)
                bottom_row = table.rowAt(viewport.height() - 1)
                if top_row < 0:
                    top_row = 0
                if bottom_row < 0:
                    bottom_row = row_count - 1
                first = max(0, top_row - buffer)
                last = min(row_count - 1, bottom_row + buffer)
            else:
                first, last = 0, row_count - 1

            symbols = []
            has_symbol_lookup = hasattr(table, "_symbol_at_row")
            for row in range(first, last + 1):
                if has_symbol_lookup:
                    symbol = table._symbol_at_row(row)
                    if not symbol:
                        continue  # grouped header row
                else:
                    item = table.item(row, 1) if hasattr(table, "item") else None
                    symbol = item.text().strip() if item else None
                symbol = str(symbol or "").strip().upper()
                if symbol and symbol != "N/A" and not symbol.startswith("─"):
                    symbols.append(symbol)
            return symbols
        except Exception as exc:
            logger.debug("Could not collect visible watchlist symbols for preload: %s", exc)
            return []

    def _preload_nearby_table_symbols(self, table, center_row: int, radius: int = 3) -> None:
        if table is None:
            return
        try:
            row_count = table.rowCount()
            symbols = []
            has_symbol_lookup = hasattr(table, "_symbol_at_row")
            for row in range(max(0, center_row - radius), min(row_count - 1, center_row + radius) + 1):
                if has_symbol_lookup:
                    symbol = table._symbol_at_row(row)
                    if not symbol:
                        continue
                else:
                    item = table.item(row, 1) if hasattr(table, "item") else None
                    symbol = item.text().strip() if item else None
                if symbol:
                    symbols.append(symbol)
            self._queue_contract_preload(symbols)
        except Exception as exc:
            logger.debug("Could not queue nearby table contract preload: %s", exc)

    def _preload_nearby_scanner_symbols(self, direction: str = "next", radius: int = 3) -> None:
        scanner = getattr(self, "finviz_scanner", None)
        if scanner is None:
            return
        getter = getattr(scanner, "get_current_symbols", None)
        if not callable(getter):
            return
        try:
            symbols = getter() or []
            if not symbols:
                return
            current = int(getattr(scanner, "_current_symbol_index", 0) or 0)
            step = -1 if direction == "previous" else 1
            center = (current + step) % len(symbols)
            nearby = [symbols[(center + offset) % len(symbols)] for offset in range(-radius, radius + 1)]
            self._queue_contract_preload(nearby)
        except Exception as exc:
            logger.debug("Could not queue nearby scanner contract preload: %s", exc)

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

    @Slot(str, str)
    def _show_position_manager_notification(self, message: str, level: str):
        """Surface PositionManager lifecycle events as toast notifications."""
        status.show_notification(message, level)

    def _init_network_resilience(self):
        """
        Wire up network monitoring and automatic reconnection.
        Must be called after market_data_worker and position_manager exist.
        """
        from ibkr.core.network_monitor import NetworkMonitor
        from ibkr.core.reconnection_manager import ReconnectionManager

        self.network_monitor = NetworkMonitor(self)
        self.reconnection_manager = ReconnectionManager(self)
        self.reconnection_manager.attach(self)

        # Network monitor → reconnection manager
        self.network_monitor.went_offline.connect(self.reconnection_manager.on_network_offline)
        self.network_monitor.came_online.connect(self.reconnection_manager.on_network_online)

        # Network monitor → UI indicators
        self.network_monitor.went_offline.connect(self._on_network_offline_ui)
        self.network_monitor.came_online.connect(self._on_network_online_ui)

        # Reconnection manager → UI
        self.reconnection_manager.reconnection_started.connect(
            lambda: status.show_notification("Reconnecting…", "warn", 2500))
        self.reconnection_manager.reconnection_started.connect(self._show_reconnect_overlay)
        self.reconnection_manager.reconnection_complete.connect(
            lambda: status.show_notification("Back online", "success", 2200))
        self.reconnection_manager.reconnection_complete.connect(self._hide_reconnect_overlay)
        self.reconnection_manager.reconnection_failed.connect(
            lambda r: status.show_notification(f"Reconnect failed: {r}", "error", 8000))
        self.reconnection_manager.reconnection_failed.connect(lambda _r: self._hide_reconnect_overlay())

        self.network_monitor.start()
        logger.info("Network resilience layer initialized")


    @Slot()
    def _show_reconnect_overlay(self):
        if hasattr(self, "_reconnect_overlay") and self._reconnect_overlay:
            self._reconnect_overlay.show_overlay()

    @Slot()
    def _hide_reconnect_overlay(self):
        if hasattr(self, "_reconnect_overlay") and self._reconnect_overlay:
            self._reconnect_overlay.hide_overlay()

    @Slot()
    def _on_network_offline_ui(self):
        """Immediate UI feedback when network drops."""
        status.show_notification("Offline", "warn", 2500)
        self._show_reconnect_overlay()
        self._pending_fresh_restart = True

        if hasattr(self, "app_status_bar"):
            self.app_status_bar.set_api_status("OFFLINE")

    @Slot()
    def _on_network_online_ui(self):
        """UI feedback when network returns."""
        # Toast is shown by reconnection_manager.reconnection_started
        if hasattr(self, "app_status_bar"):
            self.app_status_bar.set_api_status("CONNECTED")
        if self._pending_fresh_restart:
            self._pending_fresh_restart = False
            # Skip in-process reconnect orchestration and relaunch fresh session.
            if hasattr(self, "reconnection_manager") and self.reconnection_manager:
                self.reconnection_manager._retry_timer.stop()
                self.reconnection_manager._reconnecting = False
            QTimer.singleShot(600, self._restart_app_with_saved_session)

    def _restart_app_with_saved_session(self):
        """Fully restart app process and resume using persisted Kite session."""
        try:
            status.show_notification("Network restored. Restarting fresh session…", "warn", 2200)
            argv = [arg for arg in sys.argv if arg != "--resume-kite-session"]
            program = sys.executable
            arguments = [*argv, "--resume-kite-session"]

            detached_ok = QProcess.startDetached(program, arguments)
            if not detached_ok:
                raise RuntimeError("startDetached returned False")

            QApplication.instance().quit()
        except Exception as exc:
            logger.error(f"Failed to restart app after network recovery: {exc}", exc_info=True)
            status.show_notification("Auto-restart failed. Please reopen app.", "error", 6000)

    # ==============================================================================
    # SIMPLIFIED SIGNAL CONNECTIONS
    # ==============================================================================

    def _connect_signals(self):
        """Connect signals with simplified architecture"""
        logger.info("Connecting component signals...")

        # SIMPLIFIED: Position Manager → Positions Table (direct connection)
        self.position_manager.positions_updated.connect(self.positions_table.update_positions)
        self.position_manager.partial_fill_symbols_updated.connect(
            self.positions_table.mark_partial_symbols
        )
        self.position_manager.positions_updated.connect(self._schedule_subscription_rebuild)
        self.position_manager.positions_updated.connect(self._update_floating_positions_dialog)
        self.position_manager.day_pnl_updated.connect(self._on_day_pnl_updated)
        self.position_manager.show_notification.connect(self._show_position_manager_notification)
        self._connect_position_worker_signals()
        self.position_manager.start_live_sync(interval_seconds=5)
        # Position manager notifications route through the Qt signal so WS callbacks stay visible/audible.

        # SIMPLIFIED: Positions Table → Main Window
        self.positions_table.exit_position_requested.connect(self._handle_exit_position_request)
        self.positions_table.exit_half_position_requested.connect(self._handle_exit_half_position_request)
        self.positions_table.symbol_selected.connect(self.candlestick_chart.on_search)
        self.positions_table.symbol_selected.connect(self.candlestick_chart_secondary.on_search)
        self.positions_table.subscribe_to_market_data.connect(self._subscribe_to_tokens)

        # Chart → Main Window & Header
        self.candlestick_chart.order_button_clicked.connect(self._show_order_dialog)
        self.candlestick_chart_secondary.order_button_clicked.connect(self._show_order_dialog)
        self.candlestick_chart.order_dialog_requested.connect(self._show_order_dialog_from_chart_context)
        self.candlestick_chart_secondary.order_dialog_requested.connect(self._show_order_dialog_from_chart_context)
        self.candlestick_chart.symbol_loaded.connect(self.header_toolbar.set_current_symbol)
        self.candlestick_chart.symbol_loaded.connect(self._reveal_chart_panes_on_first_symbol)
        self.candlestick_chart_secondary.symbol_loaded.connect(self._reveal_chart_panes_on_first_symbol)
        self.candlestick_chart.drawings_updated.connect(self._sync_drawings_to_secondary_chart)
        self.candlestick_chart_secondary.drawings_updated.connect(self._sync_drawings_to_primary_chart)
        self.candlestick_chart.indicator_configs_updated.connect(
            lambda configs: self._sync_indicator_configs_between_charts(self.candlestick_chart, configs)
        )
        self.candlestick_chart_secondary.indicator_configs_updated.connect(
            lambda configs: self._sync_indicator_configs_between_charts(self.candlestick_chart_secondary, configs)
        )

        if self.alert_system:
            self.candlestick_chart.alert_creation_requested.connect(self.alert_system.create_alert_from_chart)
            self.candlestick_chart_secondary.alert_creation_requested.connect(self.alert_system.create_alert_from_chart)

        # Scanner & Watchlist → Chart
        # Scanner selections route through _on_scanner_symbol_selected so IBKR
        # qualification/subscription and chart loading happen once.  Directly
        # wiring scanner rows to both charts as well caused duplicate loader
        # churn and extra IBKR historical requests on every row change.
        self.finviz_scanner.symbol_selected.connect(self._on_scanner_symbol_selected)
        # Re-evaluate subscription universe whenever scan results refresh or user scrolls
        self.finviz_scanner.scan_results_changed.connect(self._schedule_subscription_rebuild)
        self.finviz_scanner.scan_results_changed.connect(self._schedule_visible_contract_preload)
        self.finviz_scanner.visible_rows_changed.connect(self._schedule_subscription_rebuild)
        self.finviz_scanner.visible_rows_changed.connect(self._schedule_visible_contract_preload)
        self.finviz_scanner.symbol_hovered.connect(self._on_symbol_hovered)
        self.watchlist.symbol_selected.connect(self.candlestick_chart.on_search)
        self.watchlist.symbol_selected.connect(self.candlestick_chart_secondary.on_search)
        self.watchlist.subscribe_tokens_requested.connect(self._subscribe_to_tokens)
        self.watchlist.place_order_requested.connect(self._show_order_dialog_from_dict)
        self.watchlist.watchlist_changed.connect(self._schedule_subscription_rebuild)
        self.watchlist.watchlist_changed.connect(self._schedule_visible_contract_preload)
        self.watchlist.watchlist_changed.connect(self._bind_watchlist_contract_preload_tracking)
        self.watchlist.watchlist_changed.connect(self._bind_watchlist_hover_preload_tracking)
        self.watchlist.watchlist_changed.connect(self._sync_floating_watchlist_dialog)
        self.watchlist.watchlist_changed.connect(self._bind_spacebar_context_tracking)
        self._bind_watchlist_contract_preload_tracking()
        self._bind_watchlist_hover_preload_tracking()
        self._bind_spacebar_context_tracking()

        # Header Toolbar → Main Window
        self.header_toolbar.symbol_selected.connect(self.candlestick_chart.on_search)
        self.header_toolbar.symbol_selected.connect(self.candlestick_chart_secondary.on_search)
        self.header_toolbar.buy_order_requested.connect(self._on_header_buy_order)
        self.header_toolbar.sell_order_requested.connect(self._on_header_sell_order)
        self.header_toolbar.order_history_requested.connect(self._show_order_history_dialog)
        self.header_toolbar.pending_orders_requested.connect(self._show_pending_orders_dialog)
        self.header_toolbar.performance_dashboard_requested.connect(self._show_performance_dialog)
        self.header_toolbar.positions_requested.connect(self._show_floating_positions_dialog)
        self.header_toolbar.stock_info_requested.connect(self._show_stock_info_dialog)

        # Alert System
        if self.alert_system:
            self.header_toolbar.alert_manager_requested.connect(lambda: self.alert_system.show_alert_manager(self))
        else:
            self.header_toolbar.alert_manager_requested.connect(self._alert_system_unavailable)

        # Alert update timer
        self.alert_update_timer = QTimer(self)
        self.alert_update_timer.timeout.connect(self._update_alert_badges)
        self.alert_update_timer.start(30000)


    @Slot(str, str)
    def _sync_drawings_to_secondary_chart(self, symbol: str, drawings_json: str):
        self._sync_chart_drawings(self.candlestick_chart_secondary, symbol, drawings_json)

    @Slot(str, str)
    def _sync_drawings_to_primary_chart(self, symbol: str, drawings_json: str):
        self._sync_chart_drawings(self.candlestick_chart, symbol, drawings_json)

    def _sync_chart_drawings(self, target_chart, symbol: str, drawings_json: str):
        if not self.dual_chart_mode_enabled or target_chart is None:
            return
        if getattr(target_chart, "current_symbol", "") != symbol:
            return
        try:
            target_chart.set_drawings(json.loads(drawings_json))
        except Exception as exc:
            logger.error("Failed to sync drawings for %s: %s", symbol, exc)

    def _sync_indicator_configs_between_charts(self, source_chart, configs: list[dict]) -> None:
        """Keep both chart instances aligned when indicator manager applies changes."""
        if source_chart is self.candlestick_chart:
            target_chart = self.candlestick_chart_secondary
        elif source_chart is self.candlestick_chart_secondary:
            target_chart = self.candlestick_chart
        else:
            return

        if target_chart is None:
            return

        target_chart.apply_indicator_configs(configs, reload_current_symbol=bool(target_chart.current_symbol))


    def _set_dual_chart_mode(self, enabled: bool):
        self.dual_chart_mode_enabled = bool(enabled)
        self._apply_chart_mode_layout()
        settings = self.config_manager.load_settings()
        settings['dual_chart_mode'] = self.dual_chart_mode_enabled
        self.config_manager.save_settings(settings)

    def _apply_chart_mode_layout(self):
        if not hasattr(self, 'candlestick_chart_secondary'):
            return
        self.candlestick_chart_secondary.setVisible(self.dual_chart_mode_enabled)
        if self.dual_chart_mode_enabled:
            self.main_splitter.setSizes([220, 700, 700, 320])
        else:
            self.main_splitter.setSizes([220, 1100, 0, 320])

    # ==============================================================================
    # WINDOW MANAGEMENT & EVENTS
    # ==============================================================================

    def _title_bar_mouse_press(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _title_bar_mouse_move(self, event: QMouseEvent):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            if not self.isMaximized():
                self.move(event.globalPosition().toPoint() - self._drag_pos)

    def _title_bar_mouse_release(self, _event: QMouseEvent):
        self._drag_pos = None

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

    def _on_instruments_loaded(self, payload: Dict[str, Any]):
        """Handle pre-processed instrument payload emitted by InstrumentLoader."""
        instruments = payload.get("instruments", [])
        self.instrument_list = instruments
        self.instrument_map = payload.get("instrument_map", {})
        self._token_to_symbol = payload.get("token_to_symbol", {})

        logger.info(f"Successfully loaded {len(instruments)} instruments.")

        self.header_toolbar.set_instrument_data(
            instruments,
            instrument_map=self.instrument_map,
            symbol_index=payload.get("symbol_index"),
        )
        self.candlestick_chart.set_instrument_list(instruments, instrument_map=self.instrument_map)
        self.candlestick_chart_secondary.set_instrument_list(instruments, instrument_map=self.instrument_map)
        self.watchlist.set_instrument_map(self.instrument_map)
        self.finviz_scanner.set_instrument_map(self.instrument_map)

        paper_trader = self._get_paper_trading_manager()
        if paper_trader:
            paper_trader.set_instrument_map(self.instrument_map)
            logger.info("Paper trader instrument map updated")
        if self.alert_system:
            self.alert_system.set_instrument_map(self.instrument_map)

        # Fetch positions after instruments are loaded
        QTimer.singleShot(1000, lambda: self.position_manager.fetch_positions_from_kite("instruments_loaded"))

        self._schedule_subscription_rebuild()
        self._refresh_header_ticker_ws_subscriptions()
        self.chart_init_timer.start(1000)

        # Trigger chart restore now that tokens are resolvable
        # (chart_widget deferred its own restore if token was missing)
        for chart in [self.candlestick_chart, self.candlestick_chart_secondary]:
            if chart.current_symbol and not chart.current_instrument_token:
                inst = self.instrument_map.get(chart.current_symbol, {})
                token = int(inst.get("instrument_token") or 0)
                if token:
                    chart.current_instrument_token = token
                    chart._load_chart_data()

        logger.info("Instruments loaded successfully.")

    @Slot(str)
    def _on_instruments_load_failed(self, error: str):
        """Handle instrument load errors without crashing the app."""
        logger.error(f"Instrument load failed: {error}")
        show_error(f"Instrument load failed: {error[:80]}")
        status.set_api_indicator("DEGRADED")

    def _show_stock_info_dialog(self, symbol: str) -> None:
        selected_symbol = (symbol or "").strip().upper()
        if not selected_symbol:
            selected_symbol = self.header_toolbar.get_current_symbol()
        if not selected_symbol:
            show_info("Select a symbol to open stock info")
            return

        try:
            show_stock_info(selected_symbol, parent=self)
        except Exception as exc:
            logger.error("Failed to open stock info dialog for %s: %s", selected_symbol, exc)
            show_error("Failed to open stock info dialog")

    def _show_scans_list_dialog(self) -> None:
        """Open the scanner's simple scans list dialog."""
        scanner = getattr(self, "finviz_scanner", None)
        if scanner is None or not hasattr(scanner, "show_scans_list_dialog"):
            show_info("Scanner is not ready")
            return
        scanner.show_scans_list_dialog()

    def _initialize_chart_after_instruments(self):
        """Initialize chart after instruments are ready.

        IBKR can restore the last symbol before the seed instrument list and
        contract IDs are available. In that case the chart has a symbol, but no
        rendered candles yet. Do not treat that as success; resolve the conId
        when possible and force one clean reload.
        """
        try:
            logger.info("Chart auto-loading initiated")
            active_symbol = (getattr(self.candlestick_chart, "current_symbol", "") or "").strip().upper()
            if active_symbol:
                state_value = str(getattr(getattr(self.candlestick_chart, "current_state", None), "value", ""))
                last_df = getattr(self.candlestick_chart, "last_df", None)
                has_rendered_data = (state_value == "loaded" and last_df is not None and not last_df.empty)

                if has_rendered_data:
                    logger.info("Chart already has active rendered symbol: %s", active_symbol)
                    return

                instrument = self.instrument_map.get(active_symbol, {}) if getattr(self, "instrument_map", None) else {}
                token = int(instrument.get("instrument_token") or 0) if isinstance(instrument, dict) else 0
                exchange = instrument.get("exchange") if isinstance(instrument, dict) else None
                if token:
                    self.candlestick_chart.current_instrument_token = token

                logger.info(
                    "Chart has active symbol %s but no rendered data; retrying historical load (conId=%s)",
                    active_symbol, token or "symbol-lookup",
                )
                self.candlestick_chart.load_symbol(
                    active_symbol,
                    exchange,
                    token,
                    getattr(self.candlestick_chart, "current_interval", "day"),
                    force_refresh=True,
                )
                return

            symbol_to_load = ""

            # 1) Prefer current watchlist symbol when available.
            if hasattr(self, "watchlist_widget") and self.watchlist_widget:
                get_symbol = getattr(self.watchlist_widget, "get_current_symbol", None)
                if callable(get_symbol):
                    symbol_to_load = (get_symbol() or "").strip().upper()

            # 2) Fall back to first scanner row (keeps startup aligned with scanner output).
            if not symbol_to_load and hasattr(self, "finviz_scanner") and self.finviz_scanner:
                table = getattr(self.finviz_scanner, "table", None)
                if table and table.rowCount() > 0:
                    cell = table.item(0, 0)
                    if cell:
                        symbol_to_load = (cell.text() or "").strip().upper()

            # 3) Last fallback: first instrument in map.
            if not symbol_to_load and self.instrument_map:
                symbol_to_load = next(iter(self.instrument_map.keys()), "").strip().upper()

            if symbol_to_load:
                logger.info("Auto-loading startup chart symbol: %s", symbol_to_load)
                self._on_scanner_symbol_selected(symbol_to_load)
            else:
                logger.warning("Chart auto-load skipped: no startup symbol available")
        except Exception as e:
            logger.error(f"Error in chart auto-loading: {e}")

    @Slot(list)
    def _enqueue_market_data(self, ticks: List[Dict]):
        """Ultra-light slot for raw websocket ticks; split chart ticks before coalescing."""
        if not ticks:
            return

        for tick in ticks:
            token = tick.get("instrument_token")

            # Feed every incoming tick to the chart queue and let each chart do
            # its own symbol/token filter.  IBKR can stream ticks before the GUI
            # has resolved the chart conId, or with token-only contracts whose
            # symbol alias is filled slightly later; pre-filtering here can drop
            # the exact live tick the active chart needs.  The coalesced path
            # below is still used for watchlists, scanners, positions, alerts,
            # and paper orders.
            self._chart_tick_queue.append(tick)

            if token is None:
                self._tick_buffer_without_token.append(tick)
            else:
                self._tick_buffer_by_token[token] = tick


    @Slot()
    def _flush_market_data_ticks(self):
        # Flush chart ticks first — highest priority, no batching delay
        if self._chart_tick_queue:
            chart_ticks = list(self._chart_tick_queue)
            self._chart_tick_queue.clear()
            for tick in chart_ticks:
                self.candlestick_chart.update_live_data(tick)
                self.candlestick_chart_secondary.update_live_data(tick)

        # Flush the coalesced buffer for everything else (watchlist, positions, scanner)
        if not self._tick_buffer_by_token and not self._tick_buffer_without_token:
            return

        ticks = list(self._tick_buffer_by_token.values())
        self._tick_buffer_by_token.clear()
        while self._tick_buffer_without_token:
            ticks.append(self._tick_buffer_without_token.popleft())

        self._on_market_data(ticks)

    def _on_market_data(self, ticks: List[Dict]):
        """
        Hot path — called on each aggregated flush.
        Keep this MINIMAL: no filtering, no allocation, no logging.
        Each component does its own O(1) lookup.
        """
        if not ticks:
            return

        if hasattr(self, "header_toolbar"):
            self.header_toolbar.ingest_ws_ticks(ticks)

        # 1. Watchlist and scanner — direct dispatch (O(1) per tick per component)
        self.watchlist.update_data(ticks)
        if self.floating_watchlist_dialog and self.floating_watchlist_dialog.isVisible():
            self.floating_watchlist_dialog.update_data(ticks)
        scanner_token_map = getattr(self.finviz_scanner, "_token_to_symbol", None)
        if scanner_token_map:
            scanner_ticks = []
            for tick in ticks:
                token = tick.get("instrument_token")
                if token is None:
                    continue
                if token in scanner_token_map:
                    scanner_ticks.append(tick)
                    continue
                try:
                    if int(token) in scanner_token_map:
                        scanner_ticks.append(tick)
                except (TypeError, ValueError):
                    continue
            if scanner_ticks:
                self.finviz_scanner.update_data(scanner_ticks)

        # 2. Positions — pass raw ticks; table does O(1) token→row lookup
        for tick in ticks:
            token = tick.get("instrument_token")
            ltp = tick.get("last_price")
            if token is not None and ltp is not None:
                self.positions_table.update_market_data(int(token), float(ltp))
                if self.floating_positions_dialog and self.floating_positions_dialog.isVisible():
                    self.floating_positions_dialog.update_market_data(int(token), float(ltp))

        # 3. Paper trader (only in paper mode, lightweight)
        paper_trader = self._get_paper_trading_manager()
        if paper_trader:
            paper_trader.update_market_data(ticks)

        # 4. Alert engine
        if self.alert_system:
            self.alert_system.update_market_data(ticks)

        if hasattr(self, "sl_manager"):
            self.sl_manager.on_ticks(ticks)

    @Slot(str)
    def _on_scanner_symbol_selected(self, symbol: str):
        """Pre-qualify scanner-selected contracts and start chart loading."""
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return

        # Start IBKR contract qualification before the chart history request needs it.
        self._queue_contract_preload([symbol])

        # Kick the history executor immediately so contract/data warm-up races the UI load.
        self._prefetch_chart_data_background(symbol)

        instrument = self.instrument_map.get(symbol)
        if instrument and instrument.get("instrument_token"):
            token = instrument["instrument_token"]
            self._subscribe_to_tokens([self._build_subscription_item(symbol, token=token) or token])

        self.candlestick_chart.on_search(symbol)
        self.candlestick_chart_secondary.on_search(symbol)

    def _filter_ticks_by_exchange_preference(self, ticks: List[Dict]) -> List[Dict]:
        """Filter ticks to prefer NSE over BSE for same symbols"""
        if not hasattr(self, 'instrument_map'):
            return ticks

        # Group ticks by symbol
        symbol_ticks = {}
        token_to_symbol = {}

        passthrough_ticks = []

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
            else:
                # Keep unresolved ticks so token-based consumers (watchlist/positions)
                # continue to update even if symbol resolution fails.
                passthrough_ticks.append(tick)

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

        filtered_ticks.extend(passthrough_ticks)
        logger.debug(f"Filtered {len(ticks)} ticks to {len(filtered_ticks)} (NSE preference applied)")
        return filtered_ticks

    def _resolve_symbol_from_token(self, token: int) -> Optional[str]:
        """Resolve trading symbol from instrument token with NSE preference"""
        normalized_token = self._normalize_token(token)
        if normalized_token is None:
            return None

        if hasattr(self, '_token_to_symbol') and normalized_token in self._token_to_symbol:
            return self._token_to_symbol[normalized_token]

        if not hasattr(self, 'instrument_map'):
            return None

        # Look for token in an instrument map
        nse_symbol = None
        bse_symbol = None
        other_symbol = None

        for symbol, instrument in self.instrument_map.items():
            instrument_token = self._normalize_token(instrument.get('instrument_token'))
            if instrument_token == normalized_token:
                exchange = instrument.get('exchange', '')
                if exchange == 'NSE':
                    nse_symbol = symbol
                elif exchange == 'BSE':
                    bse_symbol = symbol
                else:
                    other_symbol = symbol

        # Return in preference order
        return nse_symbol or bse_symbol or other_symbol

    @staticmethod
    def _normalize_token(token) -> Optional[int]:
        """Normalize instrument token values for reliable comparisons."""
        try:
            return int(token)
        except (TypeError, ValueError):
            return None

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
    def _schedule_subscription_rebuild(self):
        """Debounce expensive subscription-universe rebuilds."""
        if not hasattr(self, '_subscription_rebuild_timer'):
            return

        self._subscription_rebuild_timer.start()

    @Slot()
    def _on_watchlist_changed(self):
        """Backward-compatible entrypoint for subscription rebuild triggers."""
        self._schedule_subscription_rebuild()

    def _safe_int_token(self, value: Any) -> int:
        try:
            token = int(float(value or 0))
            return token if token > 0 else 0
        except (TypeError, ValueError):
            return 0

    def _token_from_subscription_item(self, item: Any) -> int:
        if isinstance(item, dict):
            return self._safe_int_token(item.get("instrument_token") or item.get("conId") or item.get("conid"))
        return self._safe_int_token(item)

    def _subscription_item_key(self, item: Any) -> str:
        if isinstance(item, dict):
            token = self._token_from_subscription_item(item)
            if token:
                return f"T:{token}"
            symbol = str(item.get("tradingsymbol") or item.get("symbol") or item.get("name") or "").strip().upper()
            return f"S:{symbol}" if symbol else ""
        token = self._token_from_subscription_item(item)
        if token:
            return f"T:{token}"
        symbol = str(item or "").strip().upper()
        return f"S:{symbol}" if symbol else ""

    def _build_subscription_item(self, symbol: str = "", token: Any = None) -> Optional[Any]:
        """Build a market-data subscription item that IBKR can qualify.

        IBKR widgets can have only a symbol before conId qualification (for
        example scanner rows or newly added watchlist symbols). Passing a raw
        symbol keeps those widgets live instead of silently skipping them.
        """
        clean_symbol = str(symbol or "").strip().upper()
        instrument = self.instrument_map.get(clean_symbol, {}) if clean_symbol and getattr(self, "instrument_map", None) else {}
        resolved_token = self._safe_int_token(token) or self._safe_int_token(
            instrument.get("instrument_token") or instrument.get("conId") or instrument.get("conid")
        )

        if resolved_token:
            return {
                "instrument_token": resolved_token,
                "conId": resolved_token,
                "tradingsymbol": clean_symbol or instrument.get("tradingsymbol") or instrument.get("symbol") or "",
                "symbol": clean_symbol or instrument.get("symbol") or instrument.get("tradingsymbol") or "",
                "exchange": instrument.get("exchange") or "SMART",
                "currency": instrument.get("currency") or "USD",
            }

        if clean_symbol:
            return clean_symbol
        return None

    def _get_pending_paper_order_subscription_items(self) -> List[Any]:
        """Return market-data subscription items for active pending paper orders."""
        paper_trader = self._get_paper_trading_manager()
        if not paper_trader:
            return []

        items: List[Any] = []
        try:
            for order in paper_trader.orders() or []:
                if str(order.get("status", "")).upper() != "PENDING_EXECUTION":
                    continue
                symbol = str(order.get("tradingsymbol") or order.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                item = self._build_subscription_item(symbol)
                if item is not None:
                    items.append(item)
        except Exception as exc:
            logger.warning(f"Failed to collect pending paper-order subscriptions: {exc}")
            return []

        return items

    def _get_scanner_visible_subscription_items(self) -> List[Any]:
        """Return subscription items for visible scanner rows, including raw symbols without conIds."""
        if not hasattr(self, 'finviz_scanner'):
            return []
        symbols = []
        if hasattr(self.finviz_scanner, "get_visible_symbols"):
            symbols = self.finviz_scanner.get_visible_symbols()
        items: List[Any] = []
        for symbol in symbols:
            item = self._build_subscription_item(symbol)
            if item is not None:
                items.append(item)
        return items

    @Slot()
    def _rebuild_subscription_universe(self):
        """Handle watchlist and UI changes with position-priority subscriptions."""
        logger.info("Watchlist changed - updating subscriptions")
        all_tokens = set()
        all_instruments: List[Any] = []
        seen_instruments: set[str] = set()

        def add_subscription_item(item: Any) -> None:
            key = self._subscription_item_key(item)
            if not key or key in seen_instruments:
                return
            seen_instruments.add(key)
            all_instruments.append(item)
            token = self._token_from_subscription_item(item)
            if token:
                all_tokens.add(token)

        def add_symbol(symbol: str, token: Any = None) -> None:
            item = self._build_subscription_item(symbol, token=token)
            if item is not None:
                add_subscription_item(item)

        # Priority 0: Pending paper-order symbols (must stay subscribed so
        # trigger/limit orders continue evaluating even when chart focus changes).
        paper_pending_items = self._get_pending_paper_order_subscription_items()
        for item in paper_pending_items:
            add_subscription_item(item)
        if paper_pending_items:
            logger.info(f"Added {len(paper_pending_items)} pending paper-order symbols")

        # Priority 1: Position tokens
        if hasattr(self, 'positions_table') and self.positions_table.positions_data:
            position_count = 0
            for pos in self.positions_table.positions_data.values():
                add_symbol(getattr(pos, "symbol", ""), token=getattr(pos, "token", 0))
                position_count += 1
            logger.info(f"Added {position_count} position symbols")

        # Priority 2: Chart token
        for chart in (getattr(self, 'candlestick_chart', None), getattr(self, 'candlestick_chart_secondary', None)):
            if chart:
                chart_symbol = getattr(chart, 'current_symbol', '')
                chart_token = getattr(chart, 'current_instrument_token', None)
                if chart_symbol or chart_token:
                    add_symbol(chart_symbol, token=chart_token)
                    logger.info(f"Added chart subscription: {chart_symbol or chart_token}")

        # Priority 3: Watchlist tokens (always include)
        # NOTE:
        # Keep watchlist symbols subscribed even when the watchlist pane is hidden.
        # Multiple consumers (watchlist/floating watchlist, order dialog LTP,
        # alerts, scanner interactions, etc.) read from shared live ticks; tying
        # subscriptions to widget visibility can cause intermittent "one panel
        # updates, another stalls" behavior for overlapping symbols.
        watchlist_tokens = []
        if hasattr(self.watchlist, "get_all_watchlist_tokens"):
            watchlist_tokens = self.watchlist.get_all_watchlist_tokens()
        else:
            watchlist_tokens = self.watchlist.get_all_tokens()

        if hasattr(self.watchlist, "get_all_watchlist_subscription_items"):
            watchlist_items = self.watchlist.get_all_watchlist_subscription_items()
            for item in watchlist_items:
                add_subscription_item(item)
        else:
            for token in watchlist_tokens:
                add_subscription_item(token)
        logger.info(f"Added {len(watchlist_tokens)} watchlist tokens")

        # Priority 4: Scanner-visible symbols
        theme = self.color_theme_manager.get_theme()
        if theme.get("scanner_live_ticks", True):
            scanner_items = self._get_scanner_visible_subscription_items()
            for item in scanner_items:
                add_subscription_item(item)
            logger.info(f"Added {len(scanner_items)} scanner visible symbols")

        # Priority 5: Alert tokens
        alert_tokens = self._get_alert_tokens()
        for token in alert_tokens:
            add_subscription_item(token)

        # Priority 6: Header ticker board symbols
        # Keep these in the core subscription universe so they are not dropped
        # when set_instruments() replaces the websocket subscriptions.  IBKR may
        # only know a symbol before conId qualification, so accept rich items or
        # raw symbols instead of token-only subscriptions.
        if hasattr(self, "header_toolbar"):
            try:
                if hasattr(self.header_toolbar, "configure_ticker_ws_subscriptions"):
                    header_items = self.header_toolbar.configure_ticker_ws_subscriptions(getattr(self, "instrument_map", {}) or {})
                else:
                    header_items = self.header_toolbar.configure_ticker_ws_tokens(getattr(self, "instrument_map", {}) or {})
                for item in header_items:
                    add_subscription_item(item)
                logger.info(f"Added {len(header_items)} header ticker subscriptions")
            except Exception as exc:
                logger.error(f"Failed to resolve header ticker subscriptions: {exc}")

        # Subscribe to all tokens/symbols (or clear when empty).  IBKR watchlist
        # rows can be raw symbols before conId qualification, so pass rich
        # instrument dicts/strings for the watchlist and bare tokens for the
        # other consumers.
        if self.market_data_worker:
            token_keys = {str(token) for token in all_tokens}
            self.market_data_worker.set_instruments(all_instruments)
            self.market_data_worker.request_snapshots(all_instruments)
            self._subscribed_tokens = set(all_tokens)
            logger.info(
                f"Updated subscription universe to {len(all_instruments)} instruments "
                f"({len(token_keys)} token-backed)"
            )

    def _get_scanner_visible_tokens(self) -> List[int]:
        """
        Return instrument tokens for rows VISIBLE in the scanner viewport.
        Includes a ±5 row scroll buffer (handled inside get_visible_tokens).
        Never subscribes the full scan result set — only what the trader sees.
        """
        if not hasattr(self, 'finviz_scanner'):
            return []
        return self.finviz_scanner.get_visible_tokens()

    def _get_pending_paper_order_tokens(self) -> List[int]:
        """Return instrument tokens for active pending paper orders."""
        paper_trader = self._get_paper_trading_manager()
        if not paper_trader:
            return []

        tokens = set()
        try:
            for order in paper_trader.orders() or []:
                if str(order.get("status", "")).upper() != "PENDING_EXECUTION":
                    continue
                symbol = str(order.get("tradingsymbol", "")).strip().upper()
                if not symbol:
                    continue
                instrument = self.instrument_map.get(symbol) or {}
                token = instrument.get("instrument_token")
                if token:
                    tokens.add(int(token))
        except Exception as exc:
            logger.warning(f"Failed to collect pending paper-order tokens: {exc}")
            return []

        return list(tokens)

    @Slot(list)
    def _subscribe_to_tokens(self, tokens: List[Any]):
        """Subscribe widgets to IBKR market data by token, symbol, or rich item."""
        if not tokens:
            return

        instruments: List[Any] = []
        new_token_count = 0
        seen: set[str] = set()
        for token in tokens:
            item = token if isinstance(token, (dict, str)) else self._build_subscription_item(token=token)
            if item is None:
                continue
            key = self._subscription_item_key(item)
            if not key or key in seen:
                continue
            seen.add(key)
            token_value = self._token_from_subscription_item(item)
            if token_value and token_value in self._subscribed_tokens:
                continue
            instruments.append(item)
            if token_value:
                new_token_count += 1

        if not instruments:
            return

        try:
            if self.market_data_worker and hasattr(self.market_data_worker, 'add_instruments'):
                self.market_data_worker.add_instruments(instruments)
                self.market_data_worker.request_snapshots(instruments)
                self._subscribed_tokens.update(
                    token for token in (self._token_from_subscription_item(item) for item in instruments) if token
                )
                logger.info(
                    f"Added {len(instruments)} widget market-data subscriptions "
                    f"({new_token_count} token-backed)"
                )
        except Exception as e:
            logger.error(f"Failed to subscribe to tokens: {e}")


    @Slot(dict)
    def _on_account_info_updated(self, account_info: Dict[str, Any]) -> None:
        self._latest_account_info = account_info or {}

    def _build_order_details_with_account(self, base_order_details: Dict[str, Any]) -> Dict[str, Any]:
        order_details = dict(base_order_details or {})
        if hasattr(self, "account_manager"):
            self.account_manager.refresh_if_stale()
            order_details["available_margin"] = self.account_manager.get_cached_balance()
        return order_details

    # ==============================================================================
    # SIMPLIFIED ORDER HANDLING WITH STATUS BAR
    # ==============================================================================

    def _instrument_for_order(self, symbol: str) -> Dict[str, Any]:
        """Return an order-ready instrument, with an IBKR on-demand fallback."""
        instrument = dict(getattr(self, "instrument_map", {}).get(symbol, {}) or {})
        if instrument:
            return instrument

        # IBKR mode can chart/order raw US symbols after TWS qualifies them.
        # Header buttons should not fail just because the static startup symbol
        # file did not contain the symbol selected via live search.
        if self.trading_mode == "ibkr" or hasattr(getattr(self, "real_kite_client", None), "reqContractDetails"):
            instrument = {
                "tradingsymbol": symbol,
                "symbol": symbol,
                "name": symbol,
                "exchange": "SMART",
                "primaryExch": "",
                "instrument_token": 0,
                "conId": 0,
                "segment": "STK",
                "secType": "STK",
                "currency": "USD",
                "instrument_type": "EQ",
            }
            self.instrument_map[symbol] = instrument
            if hasattr(self, "header_toolbar"):
                self.header_toolbar._instrument_map[symbol] = instrument
            return instrument

        return {}

    def _remember_order_dialog(self, dialog: OrderDialog) -> None:
        """Keep modeless order tickets alive until Qt destroys them."""
        dialogs = getattr(self, "_active_order_dialogs", None)
        if dialogs is None:
            dialogs = []
            self._active_order_dialogs = dialogs
        dialogs.append(dialog)
        dialog.destroyed.connect(lambda *_: dialogs.remove(dialog) if dialog in dialogs else None)

    @Slot(str, float)
    def _show_order_dialog(self, symbol: str = "", ltp_from_chart: float = 0.0, side: str = "BUY"):
        """Show a BUY/SELL order dialog without crashing the Qt slot on errors."""
        symbol = (symbol or "").strip().upper()
        side = "SELL" if str(side or "").upper() == "SELL" else "BUY"
        if not symbol:
            symbol = self._get_active_symbol_for_shortcuts()
        if not symbol:
            show_info("Select a symbol on chart before placing an order")
            return

        try:
            instrument = self._instrument_for_order(symbol)
            if not instrument:
                show_error(f"Symbol {symbol} not found")
                return

            ltp = ltp_from_chart if ltp_from_chart > 0.0 else self._get_fresh_ltp(symbol)
            if ltp <= 0.0:
                show_error(f"Could not fetch LTP for {symbol}")
                return

            default_qty = self.config_manager.load_settings().get('default_quantity', 1)
            order_details = {
                'tradingsymbol': symbol,
                'ltp': ltp,
                'transaction_type': side,
                'quantity': default_qty,
                'order_type': 'LIMIT',
                'price': ltp,
            }
            order_details = self._build_order_details_with_account(order_details)

            dialog = OrderDialog(self, symbol, ltp, order_details, instrument=instrument, ltp_fetcher=self._get_fresh_ltp)
            dialog.order_placed.connect(self._handle_order_placement)
            self._remember_order_dialog(dialog)
            dialog.show()
        except Exception as exc:
            logger.exception("Failed to open %s order dialog for %s", side, symbol)
            show_error(f"Could not open {side} order ticket for {symbol}: {exc}")

    def _show_order_dialog_from_dict(self, order_data: Dict[str, Any]):
        """Show order dialog from watchlist"""
        symbol = order_data.get('tradingsymbol')
        if symbol:
            ltp = self._get_fresh_ltp(symbol)
            instrument = self.instrument_map.get(symbol, {})
            dialog = OrderDialog(self, symbol, ltp, self._build_order_details_with_account(order_data), instrument=instrument, ltp_fetcher=self._get_fresh_ltp)
            dialog.order_placed.connect(self._handle_order_placement)
            dialog.show()

    @Slot(str)
    def _show_order_dialog_from_chart_context(self, order_json: str):
        """Open order dialog as LIMIT BUY at the exact chart level user clicked."""
        try:
            payload = json.loads(order_json or "{}")
        except Exception:
            payload = {}

        symbol = str(payload.get("symbol") or "").strip().upper()
        level_price = float(payload.get("price") or 0.0)
        ltp_hint = float(payload.get("ltp") or 0.0)

        if not symbol:
            symbol = self._get_active_symbol_for_shortcuts()
        if not symbol:
            show_info("Select a symbol on chart before placing an order")
            return
        if symbol not in self.instrument_map:
            show_error(f"Symbol {symbol} not found")
            return

        ltp = ltp_hint if ltp_hint > 0 else self._get_fresh_ltp(symbol)
        if ltp <= 0:
            show_error(f"Could not fetch LTP for {symbol}")
            return

        default_qty = self.config_manager.load_settings().get('default_quantity', 1)
        order_details = {
            "tradingsymbol": symbol,
            "ltp": ltp,
            "transaction_type": "BUY",
            "quantity": default_qty,
            "order_type": "LIMIT",
            "price": level_price if level_price > 0 else ltp,
        }
        order_details = self._build_order_details_with_account(order_details)

        instrument = self.instrument_map.get(symbol, {})
        dialog = OrderDialog(
            self,
            symbol,
            ltp,
            order_details,
            instrument=instrument,
            ltp_fetcher=self._get_fresh_ltp,
        )
        target_price = float(order_details.get("price") or 0.0)
        if target_price > 0:
            dialog._otype_seg.set_current("LIMIT")
            dialog._price_spin.setValue(round(target_price, 2))
            dialog._refresh_fields_visibility()
            dialog._update_summary()
        dialog.order_placed.connect(self._handle_order_placement)
        dialog.show()

    def _on_header_buy_order(self, symbol: str):
        """Handle buy order from header."""
        self._show_order_dialog(symbol, side="BUY")

    def _on_header_sell_order(self, symbol: str):
        """Handle sell order from header."""
        self._show_order_dialog(symbol, side="SELL")

    def _resolve_position_product(self, symbol: str, fallback: str = "STK") -> str:
        """Resolve product from latest broker positions with a safe fallback."""
        position = self.positions_table.get_position_by_symbol(symbol)
        if position and getattr(position, "product", None):
            return position.product
        try:
            for pos_data in (self.trader.positions() or {}).get("net", []):
                if pos_data.get("tradingsymbol") == symbol and int(pos_data.get("quantity", 0)) != 0:
                    return pos_data.get("product") or pos_data.get("product_type") or fallback
        except Exception:
            pass
        return fallback


    def _resolve_position_order_type(self, symbol: str, quantity: int, fallback: str = "MARKET") -> str:
        """Resolve preferred exit order type from the most recent matching entry order."""
        try:
            entry_tx = "BUY" if quantity > 0 else "SELL"
            orders = self.trader.orders() or []
            for od in reversed(orders):
                if (
                    str(od.get("tradingsymbol", "")) == symbol
                    and str(od.get("transaction_type", "")).upper() == entry_tx
                    and str(od.get("status", "")).upper() == "COMPLETE"
                ):
                    return str(od.get("order_type") or fallback).upper()
        except Exception:
            pass
        return fallback

    @Slot(str)
    def _handle_exit_position_request(self, symbol: str):
        """Handle position exit request from positions table."""
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
            "order_type": self._resolve_position_order_type(symbol, position.quantity, "MARKET"),
            "product": self._resolve_position_product(symbol, position.product),
            "ltp": ltp,
        }

        instrument = self.instrument_map.get(symbol, {})
        dialog = OrderDialog(self, symbol, ltp, self._build_order_details_with_account(exit_order), instrument=instrument, ltp_fetcher=self._get_fresh_ltp)
        dialog.order_placed.connect(self._handle_exit_order_placement)
        dialog.show()

    @Slot(str)
    def _handle_exit_half_position_request(self, symbol: str):
        """Handle half position exit request from positions widgets."""
        position = self.positions_table.get_position_by_symbol(symbol)
        if not position:
            show_error(f"Position not found: {symbol}")
            return

        total_qty = abs(int(position.quantity))
        half_qty = max(1, total_qty // 2)
        transaction_type = "SELL" if position.quantity > 0 else "BUY"
        ltp = self._get_fresh_ltp(symbol)

        exit_order = {
            "tradingsymbol": symbol,
            "transaction_type": transaction_type,
            "quantity": half_qty,
            "order_type": self._resolve_position_order_type(symbol, position.quantity, "MARKET"),
            "product": self._resolve_position_product(symbol, position.product),
            "ltp": ltp,
        }

        instrument = self.instrument_map.get(symbol, {})
        dialog = OrderDialog(self, symbol, ltp, self._build_order_details_with_account(exit_order), instrument=instrument, ltp_fetcher=self._get_fresh_ltp)
        dialog.order_placed.connect(self._handle_exit_order_placement)
        dialog.show()


    @staticmethod
    def _compact_broker_error(error: Exception) -> str:
        """Extract a concise, user-facing broker error for toast notifications."""
        raw = str(error or "").strip()
        if not raw:
            return "Unknown broker error"

        message = raw

        # Parse JSON-like broker payloads first: {'message': '...', 'error_type': '...'}
        parsed_payload: Any = None
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed_payload = parser(raw)
                if isinstance(parsed_payload, dict):
                    break
            except (json.JSONDecodeError, ValueError, SyntaxError, TypeError):
                continue

        if isinstance(parsed_payload, dict):
            payload_message = parsed_payload.get("message")
            if isinstance(payload_message, str) and payload_message.strip():
                message = payload_message.strip()
        else:
            # Fallback extraction when payload parsing fails.
            msg_match = re.search(r"['\"]message['\"]\s*:\s*['\"](.+?)['\"](?:,|})", raw)
            message = msg_match.group(1) if msg_match else raw

        # Remove markdown links and collapse whitespace.
        message = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", message)
        message = re.sub(r"\s+", " ", message).strip()

        # Common broker preambles/noise.
        message = re.sub(r"^Relay/Kite error HTTP \d+\s*:\s*", "", message, flags=re.IGNORECASE)
        message = re.sub(r"^RMS:Rule:\s*", "", message, flags=re.IGNORECASE)

        # Normalize capitalization for all-caps messages.
        if message.isupper():
            message = message.capitalize()
        elif message and message[0].islower():
            message = message[0].upper() + message[1:]

        if len(message) > 280:
            message = message[:277].rstrip() + "..."

        return message

    def _handle_order_placement(self, order_data: Dict[str, Any]):
        """
        Entry order placement handler.
        Called via OrderDialog.order_placed signal for BUY (and short-SELL) entries.

        Flow:
          1. Validate
          2. Submit to broker (live or paper)
          3. On success → status bar + start tracking
          4. On failure → status bar with reason (no popup)
        """
        try:
            logger.info(f"[ENTRY] Placing order: {order_data}")

            if not self._validate_order_data(order_data):
                show_error("Order validation failed — check qty/price")
                return

            symbol = order_data.get("tradingsymbol", "")
            tx_type = order_data.get("transaction_type", "BUY")
            qty = order_data.get("quantity", 0)
            self._ensure_symbol_subscription_for_order(symbol)
            status.set_message(
                f"Submitting {tx_type} {qty} {symbol}…", 3000, level="action"
            )

            order_response = self.trader.place_order(**order_data)
            order_id, broker_order = self._normalize_order_response(order_response)

            if order_id:
                order_data.update(broker_order)
                order_data["order_id"] = order_id
                order_data["status"] = str(broker_order.get("status") or "ROUTED").upper()

                status.notify("submitted", symbol)
                self.position_manager.start_tracking_order(order_id, order_data)
                self.position_manager.fetch_positions_from_kite("entry_order_submitted")
                self.account_manager.refresh_margins(force=True)
                QTimer.singleShot(2000, lambda: self.account_manager.refresh_margins(force=True))
                self._log_order_placement_immediate(order_data, order_id)
                logger.info(f"[ENTRY] Order accepted by broker: {broker_order or order_id}")
            else:
                reason = broker_order.get("error") or broker_order.get("status_message") or "no order ID returned"
                show_order_failed(f"{symbol} — {reason}")
                logger.warning(f"[ENTRY] Broker returned no order_id for {symbol}: {broker_order or order_response}")

        except Exception as e:
            symbol = order_data.get("tradingsymbol", "?")
            compact_error = self._compact_broker_error(e)
            status.notify("rejected", symbol, compact_error)
            logger.error(f"[ENTRY] Order placement exception: {e}", exc_info=True)

    def _handle_exit_order_placement(self, order_data: Dict[str, Any]):
        """
        Exit / position-close handler.
        Functionally identical to entry but:
          - Uses exit-specific status messages
          - On COMPLETE: position line removed from chart (not added)
          - Logged with [EXIT] prefix for easy filtering

        Called via OrderDialog.order_placed signal for exit dialogs.
        """
        try:
            logger.info(f"[EXIT] Closing position: {order_data}")

            if not self._validate_order_data(order_data):
                show_error("Exit validation failed — check qty/price")
                return

            symbol = order_data.get("tradingsymbol", "")
            tx_type = order_data.get("transaction_type", "SELL")
            qty = order_data.get("quantity", 0)
            self._ensure_symbol_subscription_for_order(symbol)

            status.set_message(
                f"Submitting exit {tx_type} {qty} {symbol}…", 3000, level="action"
            )

            order_response = self.trader.place_order(**order_data)
            order_id, broker_order = self._normalize_order_response(order_response)

            if order_id:
                order_data.update(broker_order)
                order_data["order_id"] = order_id
                order_data["status"] = str(broker_order.get("status") or "ROUTED").upper()
                order_data["_is_exit_order"] = True

                status.notify("submitted", symbol)
                self.position_manager.start_tracking_order(order_id, order_data)
                self.position_manager.fetch_positions_from_kite("exit_order_submitted")
                self.account_manager.refresh_margins(force=True)
                QTimer.singleShot(2000, lambda: self.account_manager.refresh_margins(force=True))
                self._log_order_placement_immediate(order_data, order_id)
                logger.info(f"[EXIT] Exit order accepted: {broker_order or order_id}")
            else:
                reason = broker_order.get("error") or broker_order.get("status_message") or "no order ID returned"
                show_order_failed(f"{symbol} exit — {reason}")
                logger.warning(f"[EXIT] Broker returned no order_id for exit {symbol}: {broker_order or order_response}")

        except Exception as e:
            symbol = order_data.get("tradingsymbol", "?")
            compact_error = self._compact_broker_error(e)
            status.notify("rejected", symbol, compact_error)
            logger.error(f"[EXIT] Exit placement exception: {e}", exc_info=True)

    @staticmethod
    def _normalize_order_response(order_response: Any) -> tuple[str, Dict[str, Any]]:
        """Return a scalar order id plus broker metadata from broker-specific responses."""
        if isinstance(order_response, dict):
            data = dict(order_response)
            status_text = str(data.get("status") or "").upper()
            failed_statuses = {"REJECTED", "FAILED", "CANCELLED", "CANCELED", "INACTIVE"}
            if data.get("error") or data.get("accepted") is False or status_text in failed_statuses:
                if not data.get("error"):
                    data["error"] = data.get("status_message") or f"Order {status_text.lower() or 'failed'}"
                return "", data
            raw_order_id = data.get("order_id") or data.get("orderId") or data.get("id")
            return str(raw_order_id).strip() if raw_order_id is not None else "", data

        if order_response is None:
            return "", {}

        return str(order_response).strip(), {}

    def _ensure_symbol_subscription_for_order(self, symbol: str) -> None:
        """Subscribe order symbol immediately so paper/live flows get fresh ticks."""
        symbol = (symbol or "").strip().upper()
        if not symbol or symbol not in self.instrument_map:
            return

        token = self.instrument_map[symbol].get("instrument_token")
        if not token:
            return

        self._subscribe_to_tokens([int(token)])
        self._schedule_subscription_rebuild()
        logger.info(f"Ensured pre-order subscription for {symbol} ({token})")

    def _log_order_placement_immediate(self, order_data: Dict[str, Any], order_id: str):
        """
        Log order placement immediately with no delays or timers
        """
        try:
            if hasattr(self, 'trade_logger') and self.trade_logger:
                order_data = dict(order_data or {})
                order_data["order_source"] = self._resolve_order_source()
                # This is now fully async and won't block the UI
                self.trade_logger.log_order_placement(order_data, order_id)
                logger.info(f"Order queued for logging: {order_id}")
        except Exception as log_error:
            # Even if logging fails, don't block the UI
            logger.error(f"Failed to queue order for logging: {log_error}")

    def _resolve_order_source(self) -> str:
        return "manual"

    # ==============================================================================
    # DIALOG SHOW METHODS
    # ==============================================================================

    def _show_order_history_dialog(self):
        """Show order history dialog"""
        try:
            if self.order_history_dialog is None:
                self.order_history_dialog = OrderHistoryDialog(
                    trade_logger=self.trade_logger,
                    parent=self
                )
                self.order_history_dialog.refresh_requested.connect(self._refresh_order_history)
                self.order_history_dialog.export_requested.connect(self._export_order_history)
            else:
                self.order_history_dialog.refresh_orders()

            self.order_history_dialog.show()
            self.order_history_dialog.raise_()
            self.order_history_dialog.activateWindow()
            logger.info("Order history dialog opened")
        except Exception as e:
            logger.error(f"Failed to show order history dialog: {e}")
            show_error("Failed to open order history")

    def _show_pending_orders_dialog(self):
        """Show pending orders dialog wired to live/paper Kite order APIs."""
        try:
            if self.pending_orders_dialog is None or not self.pending_orders_dialog.isVisible():
                self.pending_orders_dialog = PendingOrdersDialog(
                    trader=self.trader,
                    instrument_map=self.instrument_map,
                    parent=self,
                )
            else:
                self.pending_orders_dialog.refresh_orders()

            self.pending_orders_dialog.show()
            self.pending_orders_dialog.raise_()
            self.pending_orders_dialog.activateWindow()
            logger.info("Pending orders dialog opened")
        except Exception as e:
            logger.error(f"Failed to show pending orders dialog: {e}")
            show_error("Failed to open pending orders")

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

    def _show_pnl_history_dialog(self):
        """Show P&L history dialog."""
        try:
            if self.pnl_history_dialog is None or not self.pnl_history_dialog.isVisible():
                self.pnl_history_dialog = PnlHistoryDialog(
                    trade_logger=self.trade_logger,
                    parent=self,
                )
            else:
                self.pnl_history_dialog._populate_calendar()

            self.pnl_history_dialog.show()
            self.pnl_history_dialog.raise_()
            self.pnl_history_dialog.activateWindow()
            logger.info("P&L history dialog opened")
        except Exception as e:
            logger.error(f"Failed to show P&L history dialog: {e}")
            show_error("Failed to open P&L history")

    def _show_floating_positions_dialog(self):
        """Show floating positions dialog."""
        try:
            if self.floating_positions_dialog is None:
                self.floating_positions_dialog = FloatingPositionsDialog(parent=self)
                self.floating_positions_dialog.symbol_chart_requested.connect(self.candlestick_chart.on_search)
                self.floating_positions_dialog.symbol_chart_requested.connect(self.candlestick_chart_secondary.on_search)
                self.floating_positions_dialog.symbol_chart_requested.connect(self.header_toolbar.set_current_symbol)
                self.floating_positions_dialog.symbol_chart_requested.connect(self._ensure_chart_subscription)
                self.floating_positions_dialog.exit_position_requested.connect(self._handle_exit_position_request)
                self.floating_positions_dialog.exit_half_position_requested.connect(self._handle_exit_half_position_request)
                self.floating_positions_dialog.subscribe_to_market_data.connect(self._subscribe_to_tokens)

            self._update_floating_positions_dialog(getattr(self.positions_table, 'positions_data', {}).values())
            self._schedule_subscription_rebuild()
            self.floating_positions_dialog.show()
            self.floating_positions_dialog.raise_()
            self.floating_positions_dialog.activateWindow()
            logger.info("Floating positions dialog opened")
        except Exception as e:
            logger.error(f"Failed to show floating positions dialog: {e}")
            show_error("Failed to open floating positions")

    def _show_floating_watchlist_dialog(self):
        """Show floating watchlist dialog that shares embedded watchlist data."""
        try:
            if self.floating_watchlist_dialog is None:
                self.floating_watchlist_dialog = attach_floating_watchlist(self)
                self.floating_watchlist_dialog.apply_color_theme(self.color_theme_manager.get_theme())
                self.floating_watchlist_dialog.symbol_chart_requested.connect(self.candlestick_chart_secondary.on_search)
                self.floating_watchlist_dialog.symbol_chart_requested.connect(self.header_toolbar.set_current_symbol)
                self.floating_watchlist_dialog.symbol_chart_requested.connect(self._ensure_chart_subscription)
                self.floating_watchlist_dialog.table.cellClicked.connect(
                    lambda _r, _c: self._set_last_spacebar_context("floating_watchlist")
                )
            self._sync_floating_watchlist_dialog()
            self._schedule_subscription_rebuild()
            self.floating_watchlist_dialog.show()
            self.floating_watchlist_dialog.raise_()
            self.floating_watchlist_dialog.activateWindow()
            logger.info("Floating watchlist dialog opened")
        except Exception as e:
            logger.error(f"Failed to show floating watchlist dialog: {e}")
            show_error("Failed to open floating watchlist")

    def _sync_floating_watchlist_dialog(self):
        """Push latest embedded watchlist symbols/data/token map into floating dialog."""
        if self.floating_watchlist_dialog is None:
            return
        try:
            meta = []
            for entry in self.watchlist._config.all():
                wl_id = entry.get("id")
                table = self.watchlist._tables.get(wl_id)
                if not wl_id or table is None:
                    continue

                symbols = table.get_symbol_list()
                data = {sym: dict(table._watchlist_data.get(sym, {})) for sym in symbols}
                meta.append({
                    "id": wl_id,
                    "name": entry.get("name", wl_id),
                    "symbols": symbols,
                    "data": data,
                    "instrument_map": self.instrument_map,
                })
                self.floating_watchlist_dialog._token_to_symbol[wl_id] = dict(table._token_to_symbol)

            self.floating_watchlist_dialog.set_watchlists(meta)
        except Exception as e:
            logger.error(f"Failed to sync floating watchlist dialog: {e}")

    def _update_floating_positions_dialog(self, positions):
        """Sync latest positions into floating positions dialog if initialized."""
        if self.floating_positions_dialog is None:
            return
        try:
            self.floating_positions_dialog.update_positions(list(positions))
        except Exception as e:
            logger.error(f"Failed to update floating positions dialog: {e}")

    def _on_stop_loss_set(self, symbol: str, sl_price: float) -> None:
        """Draw/update stop-loss line immediately after SL set/modify/trailing updates."""
        try:
            rec = self._find_stop_loss_record_for_chart_line(symbol.upper(), float(sl_price))
            if rec:
                self.chart_lines_manager.add_stop_loss_line(symbol, float(sl_price), rec.position_id)
            else:
                # Fallback: preserve existing behavior when record resolution is ambiguous.
                self.chart_lines_manager.add_stop_loss_line(symbol, float(sl_price))
            self._refresh_floating_positions_sl_values(symbol)
        except Exception as e:
            logger.error(f"Failed to draw stop-loss line for {symbol}: {e}")

    def _on_stop_loss_cancelled(self, symbol: str, *_args) -> None:
        """Remove stop-loss line when SL is cancelled/triggered."""
        try:
            self.chart_lines_manager.remove_stop_loss_line(symbol)
            # If another SL remains active for the same symbol (e.g., different product),
            # redraw it so one cancellation doesn't wipe unrelated SL lifecycle visuals.
            remaining = [
                rec for rec in self.sl_manager.get_all_active()
                if str(rec.symbol).upper() == str(symbol).upper()
            ]
            for rec in remaining:
                self.chart_lines_manager.add_stop_loss_line(symbol, float(rec.sl_price), rec.position_id)
            self._refresh_floating_positions_sl_values(symbol)
        except Exception as e:
            logger.error(f"Failed to remove stop-loss line for {symbol}: {e}")

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
            exports_dir = os.path.join(home, ".qullamaggie", "exports")
            os.makedirs(exports_dir, exist_ok=True)

            # Generate filename with timestamp
            timestamp = market_strftime("%Y%m%d_%H%M%S")
            filename = f"order_history_export_{timestamp}.json"
            filepath = os.path.join(exports_dir, filename)

            # Add metadata
            export_data.update({
                'export_source': 'qullamaggie_order_history',
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
        if self.alert_system:
            alert = self.alert_system.store.get(alert_id)
            if alert:
                status.notify("alert", alert.symbol, f"Alert triggered at ₹{alert.target_value}")
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

        # Check active charts before the blocking broker fallback.  The chart
        # order button uses this same value, so header BUY/SELL should be able
        # to open the ticket for the currently displayed symbol even when the
        # static instrument map has no last_price yet.
        for chart in (getattr(self, "candlestick_chart", None), getattr(self, "candlestick_chart_secondary", None)):
            chart_symbol = str(getattr(chart, "current_symbol", "") or "").strip().upper()
            if chart_symbol == symbol:
                try:
                    ltp = float(getattr(chart, "current_ltp", 0.0) or 0.0)
                except (TypeError, ValueError):
                    ltp = 0.0
                if ltp > 0:
                    return ltp

        # Check instrument map (now NSE-preferred)
        if symbol in self.instrument_map:
            ltp = self.instrument_map[symbol].get('last_price', 0)
            if ltp > 0:
                return ltp

        # Fallback to API: support both Kite-style quote() and IBKR reqMktData().
        try:
            if self.real_kite_client:
                if hasattr(self.real_kite_client, "quote"):
                    exchange = self.instrument_map.get(symbol, {}).get('exchange', 'NSE')
                    quote = self.real_kite_client.quote([f"{exchange}:{symbol}"])
                    ltp = quote[f"{exchange}:{symbol}"].get('last_price', 0)
                    return ltp

                if hasattr(self.real_kite_client, "reqMktData") and hasattr(self.real_kite_client, "reqContractDetails"):
                    from ib_insync import Stock
                    details = self.real_kite_client.reqContractDetails(Stock(symbol, "SMART", "USD"))
                    if details:
                        contract = details[0].contract
                        ticker = self.real_kite_client.reqMktData(contract, '', False, False)
                        time.sleep(0.2)
                        return float((ticker.last or ticker.close or 0.0) if ticker else 0.0)
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

    def _create_chart_data_fetcher(self):
        """Create the correct chart data fetcher for the active broker client."""
        client = self.real_kite_client
        if hasattr(client, "reqHistoricalData"):
            return IBKRDataFetcher(client)
        return KiteDataFetcher(client)

    def _setup_watchlist_shortcuts(self):
        """Setup keyboard shortcuts"""
        self._watchlist_shortcuts = setup_keyboard_shortcuts(self)
        logger.info("Keyboard shortcuts initialized")

    def _watchlist_metadata_for_symbol(self, symbol: str) -> Dict[str, Any]:
        """Return scanner metadata so watchlist can group symbols by industry/theme."""
        clean_symbol = str(symbol or "").strip().upper()
        if not clean_symbol:
            return {}

        metadata: Dict[str, Any] = {"symbol": clean_symbol}
        scanner = getattr(self, "finviz_scanner", None)
        getter = getattr(scanner, "get_symbol_data", None)
        if callable(getter):
            try:
                scanner_data = getter(clean_symbol) or {}
                if isinstance(scanner_data, dict):
                    metadata.update({
                        key: value
                        for key, value in scanner_data.items()
                        if key in {
                            "symbol", "name", "company", "company_name", "sector",
                            "industry", "theme", "group", "country", "market_cap",
                            "scan_name", "source_scan", "scan_tag"
                        } and value not in (None, "")
                    })
            except Exception:
                logger.debug("Could not read scanner metadata for %s", clean_symbol, exc_info=True)

        group = (
            metadata.get("group")
            or metadata.get("theme")
            or metadata.get("industry")
            or metadata.get("scan_name")
            or metadata.get("source_scan")
        )
        if group:
            metadata["group"] = str(group).strip()
        return metadata

    def _add_symbol_to_watchlist_from_chart_index(self, index: int):
        """Add current chart symbol to watchlist by zero-based index."""
        current_symbol = getattr(self.candlestick_chart, 'current_symbol', None)
        if not current_symbol:
            status.show_info("No symbol on chart")
            return

        watchlist_name = self.watchlist.get_watchlist_name_by_index(index)
        if not watchlist_name:
            status.show_info(f"Watchlist slot {index + 1} is empty")
            return

        metadata = self._watchlist_metadata_for_symbol(current_symbol)
        if self.watchlist.add_symbol_to_watchlist_index(
            current_symbol,
            index,
            metadata=metadata,
            category=metadata.get("group"),
        ):
            group = metadata.get("group") or "Ungrouped"
            status.show_info(f"Added {current_symbol} to {watchlist_name} / {group}")
        else:
            status.show_info(f"{current_symbol} already in {watchlist_name}")

    def _toggle_symbol_in_active_watchlist_from_chart(self):
        """Toggle current chart symbol in the active watchlist (remove if present, otherwise add)."""
        current_symbol = getattr(self.candlestick_chart, 'current_symbol', None)
        if not current_symbol:
            status.show_info("No symbol on chart")
            return

        active_name = self.watchlist.get_active_watchlist_name()
        active_table = getattr(self.watchlist, "_current_table", lambda: None)()
        symbols = []
        target_row_after_remove = None
        symbol_in_active_watchlist = False
        if active_table and hasattr(active_table, "get_symbol_list"):
            symbols = active_table.get_symbol_list()
            symbol_in_active_watchlist = current_symbol in symbols
            if symbol_in_active_watchlist:
                removed_index = symbols.index(current_symbol)
                target_row_after_remove = max(removed_index - 1, 0)

        if symbol_in_active_watchlist:
            if self.watchlist.remove_symbol_from_active_watchlist(current_symbol):
                if active_table and target_row_after_remove is not None and active_table.rowCount() > 0:
                    target_row_after_remove = min(target_row_after_remove, active_table.rowCount() - 1)
                    active_table.selectRow(target_row_after_remove)
                    active_table.setCurrentCell(target_row_after_remove, 1)
                status.show_info(f"Removed {current_symbol} from {active_name or 'active watchlist'}")
                return
            status.show_info(f"Could not remove {current_symbol} from {active_name or 'active watchlist'}")
            return

        metadata = self._watchlist_metadata_for_symbol(current_symbol)
        if self.watchlist.add_symbol_to_active_watchlist(
            current_symbol,
            metadata=metadata,
            category=metadata.get("group"),
        ):
            group = metadata.get("group") or "Ungrouped"
            status.show_info(f"Added {current_symbol} to {active_name or 'active watchlist'} / {group}")
        else:
            status.show_info(f"Could not add {current_symbol} to {active_name or 'active watchlist'}")

    def _get_active_symbol_for_shortcuts(self) -> str:
        symbol = (getattr(self.candlestick_chart, "current_symbol", "") or "").strip().upper()
        if not symbol:
            symbol = (self.header_toolbar.get_current_symbol() or "").strip().upper()
        return symbol

    def _focus_order_quantity_input(self):
        active_modal = QApplication.activeModalWidget()
        if isinstance(active_modal, QDialog):
            qty_input = active_modal.findChild(QLineEdit, "qt_spinbox_lineedit")
            if qty_input:
                qty_input.setFocus(Qt.FocusReason.ShortcutFocusReason)
                qty_input.selectAll()

    def _open_order_ticket_for_side(self, side: str):
        symbol = self._get_active_symbol_for_shortcuts()
        if not symbol:
            show_info("Select a symbol on chart before placing an order")
            return
        if side.upper() == "SELL":
            self._on_header_sell_order(symbol)
        else:
            self._show_order_dialog(symbol)
        QTimer.singleShot(0, self._focus_order_quantity_input)
        status.show_info(f"{side.upper()} TICKET: {symbol}")

    def _on_buy_shortcut(self):
        self._open_order_ticket_for_side("BUY")

    def _on_sell_shortcut(self):
        self._open_order_ticket_for_side("SELL")

    def _on_order_entry_shortcut(self):
        self._open_order_ticket_for_side("BUY")

    def _toggle_floating_positions_shortcut(self):
        if self.floating_positions_dialog and self.floating_positions_dialog.isVisible():
            self.floating_positions_dialog.close()
        else:
            self._show_floating_positions_dialog()

    def _show_stock_info_for_active_symbol(self):
        self._show_stock_info_dialog(self._get_active_symbol_for_shortcuts())

    def _handle_escape_shortcut(self):
        active_modal = QApplication.activeModalWidget()
        if isinstance(active_modal, QDialog):
            active_modal.close()
            return
        focused_widget = QApplication.focusWidget()
        if focused_widget == self.header_toolbar.search_input:
            self.header_toolbar.search_input.clearFocus()
            return

    def _handle_global_spacebar(self):
        """Handle spacebar press based on focused widget"""
        focused_widget = self.focusWidget()
        context = self._resolve_spacebar_context(focused_widget)

        floating_watchlist = self._get_focused_floating_watchlist(focused_widget)
        active_floating_watchlist = self._get_active_floating_watchlist()
        if active_floating_watchlist is not None:
            floating_watchlist = active_floating_watchlist
            context = "floating_watchlist"
        if context == "floating_watchlist" and floating_watchlist is None:
            dlg = getattr(self, "floating_watchlist_dialog", None)
            if dlg and dlg.isVisible():
                floating_watchlist = dlg
        if floating_watchlist:
            self._set_last_spacebar_context("floating_watchlist")
            self._navigate_floating_watchlist_symbols(floating_watchlist, direction='next')
            return

        floating_positions = self._get_focused_floating_positions(focused_widget)
        active_floating_positions = self._get_active_floating_positions()
        if active_floating_positions is not None:
            floating_positions = active_floating_positions
            context = "floating_positions"
        if context == "floating_positions" and floating_positions is None:
            dlg = getattr(self, "floating_positions_dialog", None)
            if dlg and dlg.isVisible():
                floating_positions = dlg
        if floating_positions:
            self._set_last_spacebar_context("floating_positions")
            self._navigate_floating_positions_symbols(floating_positions, direction='next')
            return

        # Check scanner focus — evaluated before watchlist so chart interaction
        # cannot steal the "scanner" context away mid-session.
        if context == "scanner" or self._is_scanner_focused(focused_widget):
            self._set_last_spacebar_context("scanner")
            if hasattr(self.finviz_scanner, '_next_symbol'):
                self._preload_nearby_scanner_symbols(direction='next')
                self.finviz_scanner._next_symbol()
                return

        # Check watchlist focus — only resolve the table when context is "watchlist".
        # Previously, _get_focused_watchlist_table() returned the active tab's table
        # via its fallback branch even when the scanner was the intended context,
        # causing spacebar to jump to the watchlist after any chart interaction.
        watchlist_table = None
        if context == "watchlist":
            watchlist_table = self._get_focused_watchlist_table(focused_widget)
            if watchlist_table is None:
                watchlist_table = self._get_last_selected_watchlist_table()
        if watchlist_table:
            self._set_last_spacebar_context("watchlist")
            self._navigate_watchlist_symbols(watchlist_table, direction='next')
            return

        # Check positions focus
        if self._is_positions_focused(focused_widget):
            self._set_last_spacebar_context("positions")
            self._navigate_position_symbols(direction='next')
            return

        logger.debug("Spacebar ignored: no focused scanner/watchlist/positions context")

    def _handle_global_shift_spacebar(self):
        """Handle Shift+spacebar press based on focused widget"""
        focused_widget = self.focusWidget()
        context = self._resolve_spacebar_context(focused_widget)

        floating_watchlist = self._get_focused_floating_watchlist(focused_widget)
        active_floating_watchlist = self._get_active_floating_watchlist()
        if active_floating_watchlist is not None:
            floating_watchlist = active_floating_watchlist
            context = "floating_watchlist"
        if context == "floating_watchlist" and floating_watchlist is None:
            dlg = getattr(self, "floating_watchlist_dialog", None)
            if dlg and dlg.isVisible():
                floating_watchlist = dlg
        if floating_watchlist:
            self._set_last_spacebar_context("floating_watchlist")
            self._navigate_floating_watchlist_symbols(floating_watchlist, direction='previous')
            return

        floating_positions = self._get_focused_floating_positions(focused_widget)
        active_floating_positions = self._get_active_floating_positions()
        if active_floating_positions is not None:
            floating_positions = active_floating_positions
            context = "floating_positions"
        if context == "floating_positions" and floating_positions is None:
            dlg = getattr(self, "floating_positions_dialog", None)
            if dlg and dlg.isVisible():
                floating_positions = dlg
        if floating_positions:
            self._set_last_spacebar_context("floating_positions")
            self._navigate_floating_positions_symbols(floating_positions, direction='previous')
            return

        if context == "scanner" or self._is_scanner_focused(focused_widget):
            self._set_last_spacebar_context("scanner")
            if hasattr(self.finviz_scanner, '_previous_symbol'):
                self._preload_nearby_scanner_symbols(direction='previous')
                self.finviz_scanner._previous_symbol()
                return

        watchlist_table = None
        if context == "watchlist":
            watchlist_table = self._get_focused_watchlist_table(focused_widget)
            if watchlist_table is None:
                watchlist_table = self._get_last_selected_watchlist_table()
        if watchlist_table:
            self._set_last_spacebar_context("watchlist")
            self._navigate_watchlist_symbols(watchlist_table, direction='previous')
            return

        if self._is_positions_focused(focused_widget):
            self._set_last_spacebar_context("positions")
            self._navigate_position_symbols(direction='previous')
            return

        logger.debug("Shift+Space ignored: no focused scanner/watchlist/positions context")

    def _set_last_spacebar_context(self, context: str):
        """Remember where the latest user mouse selection came from."""
        self._last_spacebar_context = context
        self._clear_non_active_table_selections(context)

    def _clear_non_active_table_selections(self, active_context: str):
        """Keep row highlight visible only on the active spacebar navigation table."""
        try:
            scanner_table = getattr(self.finviz_scanner, "table", None)
            if scanner_table is not None and active_context != "scanner":
                scanner_table.clearSelection()

            if active_context != "watchlist":
                for table in getattr(self.watchlist, "_tables", {}).values():
                    table.clearSelection()

            positions_table = getattr(self.positions_table, "table", None)
            if positions_table is not None and active_context != "positions":
                positions_table.clearSelection()
        except Exception as e:
            logger.debug(f"Failed to clear non-active table selections: {e}")

    def _resolve_spacebar_context(self, focused_widget):
        """Resolve navigation context without letting chart focus reset table choice.

        Focus inside a table always wins.  When focus moves to the chart after a
        symbol load, keep using the last manually selected table instead of
        falling back to whichever watchlist tab still has a current row.
        """
        if self._get_focused_floating_watchlist(focused_widget):
            return "floating_watchlist"
        if self._get_focused_floating_positions(focused_widget):
            return "floating_positions"
        if self._is_scanner_focused(focused_widget):
            return "scanner"
        if self._is_watchlist_focused(focused_widget):
            return "watchlist"
        return self._last_spacebar_context

    def _is_watchlist_focused(self, widget) -> bool:
        """Return True only when focus is actually within the docked watchlist."""
        if not widget:
            return False
        current = widget
        while current:
            if current == self.watchlist:
                return True
            current = current.parent()
        return False

    def _get_last_selected_watchlist_table(self):
        """Return watchlist table that currently has a row selected."""
        for table in self.watchlist._tables.values():
            if table.currentRow() != -1:
                return table
        return None

    def _bind_spacebar_context_tracking(self):
        """Bind mouse-selection tracking for scanner/watchlist tables."""
        try:
            if hasattr(self.finviz_scanner, "table"):
                scanner_table = self.finviz_scanner.table
                if not getattr(scanner_table, "_spacebar_context_bound", False):
                    scanner_table.cellClicked.connect(
                        lambda _r, _c: self._set_last_spacebar_context("scanner")
                    )
                    scanner_table._spacebar_context_bound = True
            for table in self.watchlist._tables.values():
                if not getattr(table, "_spacebar_context_bound", False):
                    table.cellClicked.connect(
                        lambda _r, _c: self._set_last_spacebar_context("watchlist")
                    )
                    table._spacebar_context_bound = True
        except Exception as e:
            logger.debug(f"Failed to bind spacebar context tracking: {e}")

    def _get_focused_floating_watchlist(self, widget):
        """Return floating watchlist dialog when focus is inside it."""
        dlg = getattr(self, "floating_watchlist_dialog", None)
        if not dlg or not dlg.isVisible() or not widget:
            return None

        current = widget
        while current:
            if current == dlg:
                return dlg
            current = current.parent()
        return None

    def _get_active_floating_watchlist(self):
        """Return floating watchlist when it is the active top-level window."""
        dlg = getattr(self, "floating_watchlist_dialog", None)
        if dlg and dlg.isVisible() and dlg.isActiveWindow():
            return dlg
        return None

    def _navigate_floating_watchlist_symbols(self, dialog, direction='next'):
        """Navigate symbols in floating watchlist dialog."""
        try:
            table = getattr(dialog, "table", None)
            if table is None:
                return

            count = table.rowCount()
            if count <= 0:
                return

            row = table.currentRow()
            if row < 0:
                row = 0
            elif direction == 'next':
                row = (row + 1) % count
            else:
                row = (row - 1) % count

            dialog._nav_idx = row
            dialog._select_row(row)
        except Exception as e:
            logger.warning(f"Error navigating floating watchlist symbols: {e}")


    def _get_focused_floating_positions(self, widget):
        """Return floating positions dialog when focus is inside it."""
        dlg = getattr(self, "floating_positions_dialog", None)
        if not dlg or not dlg.isVisible() or not widget:
            return None

        current = widget
        while current:
            if current == dlg:
                return dlg
            current = current.parent()
        return None

    def _get_active_floating_positions(self):
        """Return floating positions when it is the active top-level window."""
        dlg = getattr(self, "floating_positions_dialog", None)
        if dlg and dlg.isVisible() and dlg.isActiveWindow():
            return dlg
        return None

    def _navigate_floating_positions_symbols(self, dialog, direction='next'):
        """Navigate symbols in floating positions dialog."""
        try:
            table = getattr(dialog, "table", None)
            if table is None:
                return

            count = table.rowCount()
            if count <= 0:
                return

            row = table.currentRow()
            if row < 0:
                row = 0
            elif direction == 'next':
                row = (row + 1) % count
            else:
                row = (row - 1) % count

            dialog._nav_idx = row
            table.selectRow(row)
            sym = dialog._symbol_at_row(row)
            if sym:
                dialog.symbol_chart_requested.emit(sym)
        except Exception as e:
            logger.warning(f"Error navigating floating positions symbols: {e}")

    def _is_scanner_focused(self, widget) -> bool:
        """Check if the scanner has focus"""
        if not widget:
            return False
        current = widget
        while current:
            if current == self.finviz_scanner:
                return True
            if hasattr(current, 'objectName') and 'scanner' in current.objectName().lower():
                return True
            current = current.parent()
        return False

    def _get_focused_watchlist_table(self, widget):
        """Get focused watchlist table.

        If focus moved away from the watchlist (e.g., chart steals focus after
        symbol load), continue using the active watchlist tab as long as it has
        an existing row selection so spacebar navigation remains sequential.
        """
        if widget:
            current = widget
            while current:
                if current == self.watchlist:
                    for table in self.watchlist._tables.values():
                        if table == widget or self._is_child_of_widget(widget, table):
                            return table
                    break
                current = current.parent()

        active_table = getattr(self.watchlist, '_current_table', None)
        if callable(active_table):
            table = active_table()
            if table and table.currentRow() != -1:
                return table

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
        """Navigate symbols in watchlist table, skipping grouped industry header rows."""
        if not table:
            return

        row_count = table.rowCount()
        if row_count == 0:
            return

        current_row = table.currentRow()
        symbol_col = 1
        step = -1 if direction == 'previous' else 1
        if current_row < 0:
            current_row = row_count if direction == 'previous' else -1

        try:
            for offset in range(1, row_count + 1):
                next_row = (current_row + (step * offset)) % row_count
                symbol = None
                if hasattr(table, '_symbol_at_row'):
                    symbol = table._symbol_at_row(next_row)
                if not symbol and not hasattr(table, '_symbol_at_row'):
                    item = table.item(next_row, symbol_col)
                    symbol = item.text().strip() if item else None

                if symbol and symbol != 'N/A' and not symbol.startswith('─'):
                    table.selectRow(next_row)
                    table.setCurrentCell(next_row, symbol_col)
                    self._preload_nearby_table_symbols(table, next_row)
                    table.symbol_selected.emit(symbol)
                    logger.debug(f"Watchlist navigation: Selected {symbol}")
                    return
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
            symbol = None
            if hasattr(self.positions_table, '_symbol_at_row'):
                symbol = self.positions_table._symbol_at_row(next_row)
            if not symbol:
                symbol_item = table.item(next_row, 0)
                symbol = symbol_item.text().strip() if symbol_item else None

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
            existing_state = self.config_manager.load_window_state()
            if not isinstance(existing_state, dict):
                existing_state = {}

            splitter_sizes = self._sanitize_main_splitter_sizes(self.main_splitter.sizes())
            scanner_width = (
                splitter_sizes[0]
                if self.finviz_scanner.isVisible()
                else self._clamp_scanner_panel_width(self._saved_scanner_panel_width)
            )
            watchlist_width = (
                splitter_sizes[3]
                if self.right_panel_splitter.isVisible()
                else self._clamp_right_panel_width(self._saved_watchlist_panel_width)
            )

            state = {
                'geometry': self.saveGeometry().toBase64().data().decode('utf-8'),
                'main_splitter': self.main_splitter.saveState().toBase64().data().decode('utf-8'),
                'main_splitter_sizes': splitter_sizes,
                'is_maximized': self.isMaximized(),
                'scanner_visible': self.finviz_scanner.isVisible(),
                'watchlist_visible': self.watchlist_action.isChecked(),
                'positions_visible': self.positions_action.isChecked(),
                'scanner_panel_width': scanner_width,
                'watchlist_panel_width': watchlist_width
            }

            if hasattr(self, 'right_panel_splitter'):
                state['right_panel_splitter'] = self.right_panel_splitter.saveState().toBase64().data().decode('utf-8')
                state['right_panel_splitter_sizes'] = self.right_panel_splitter.sizes()

            existing_state.update(state)
            self.config_manager.save_window_state(existing_state)
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
                        self.main_splitter.setSizes([_SCANNER_PANEL_DEFAULT_WIDTH, 900, 0, _RIGHT_PANEL_DEFAULT_WIDTH])
                else:
                    self.main_splitter.setSizes([_SCANNER_PANEL_DEFAULT_WIDTH, 900, 0, _RIGHT_PANEL_DEFAULT_WIDTH])
                if state.get('main_splitter_sizes'):
                    self._pending_main_splitter_sizes = state['main_splitter_sizes']

                if hasattr(self, 'right_panel_splitter') and 'right_panel_splitter' in state:
                    try:
                        self.right_panel_splitter.restoreState(
                            QByteArray.fromBase64(state['right_panel_splitter'].encode('utf-8')))
                    except Exception as e:
                        logger.warning(f"Failed to restore right panel splitter state: {e}")
                        self.right_panel_splitter.setSizes([_RIGHT_PANEL_DEFAULT_WIDTH, 220])
                elif hasattr(self, 'right_panel_splitter'):
                    self.right_panel_splitter.setSizes([_RIGHT_PANEL_DEFAULT_WIDTH, 220])
                if hasattr(self, 'right_panel_splitter') and state.get('right_panel_splitter_sizes'):
                    self._pending_right_splitter_sizes = state['right_panel_splitter_sizes']

                self._saved_watchlist_panel_width = self._clamp_right_panel_width(state.get('watchlist_panel_width'))
                self._saved_scanner_panel_width = self._clamp_scanner_panel_width(state.get('scanner_panel_width'))

                scanner_visible = state.get('scanner_visible', True)
                watchlist_visible = state.get('watchlist_visible', True)
                positions_visible = state.get('positions_visible', True)

                self.scanner_action.setChecked(scanner_visible)
                self.watchlist_action.setChecked(watchlist_visible)
                self.positions_action.setChecked(positions_visible)

                self._start_maximized = bool(state.get('is_maximized', False))

                QTimer.singleShot(0, self._apply_pending_splitter_sizes)
                logger.info("Window state restored")
            else:
                # Default state
                self._start_maximized = True
                self.main_splitter.setSizes([_SCANNER_PANEL_DEFAULT_WIDTH, 900, 0, _RIGHT_PANEL_DEFAULT_WIDTH])
                if hasattr(self, 'right_panel_splitter'):
                    self.right_panel_splitter.setSizes([_RIGHT_PANEL_DEFAULT_WIDTH, 220])
                self._apply_intelligent_main_splitter_layout()

        except Exception as e:
            logger.error(f"Failed to restore window state: {e}")
            # Safe fallback
            self._start_maximized = True
            self.main_splitter.setSizes([_SCANNER_PANEL_DEFAULT_WIDTH, 900, 0, _RIGHT_PANEL_DEFAULT_WIDTH])
            if hasattr(self, 'right_panel_splitter'):
                self.right_panel_splitter.setSizes([_RIGHT_PANEL_DEFAULT_WIDTH, 220])
            self._apply_intelligent_main_splitter_layout()


    # ==============================================================================
    # DARK THEME STYLING
    # ==============================================================================

    def _apply_dark_theme(self):
        """Apply dark theme with splitter styling"""
        self.setStyleSheet("""
            #mainContainer {
                background-color: #050709;
                border: 1px solid #151d2b;
            }

            #customTitleBar {
                background-color: #050709;
                border-bottom: 1px solid #151d2b;
            }

            #menuContainer {
                background: transparent;
                border: none;
            }

            QMenuBar#mainMenuBar {
                background-color: transparent;
                color: #a8bcd4;
                border: none;
                padding: 0px;
                spacing: 1px;
                font-family: "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", "Noto Sans", sans-serif;
                font-size: 10px;
                font-weight: 500;
            }

            QMenuBar#mainMenuBar::item {
                color: #a8bcd4;
                background: transparent;
                padding: 3px 8px;
                margin: 0px 1px;
                border: 1px solid transparent;
                border-radius: 2px;
            }

            QMenuBar#mainMenuBar::item:selected {
                color: #e8f0ff;
                background: #0f1318;
                border: 1px solid #25344a;
            }

            QMenuBar#mainMenuBar::item:pressed {
                color: #e8f0ff;
                background: #1a2840;
                border: 1px solid #2a3a50;
            }

            QMenuBar#mainMenuBar::item:disabled {
                color: #2a3a50;
            }

            QMenu {
                background-color: #0a0d12;
                color: #a8bcd4;
                border: 1px solid #1a2030;
                border-radius: 2px;
                padding: 4px 0px;
                font-family: "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", "Noto Sans", sans-serif;
                font-size: 10px;
                font-weight: 500;
            }

            QMenu::item {
                color: #a8bcd4;
                background: transparent;
                padding: 5px 28px 5px 22px;
                margin: 1px 4px;
                min-height: 18px;
                border: 1px solid transparent;
                border-radius: 2px;
            }

            QMenu::item:selected {
                color: #e8f0ff;
                background-color: #1a2840;
                border: 1px solid #25344a;
            }

            QMenu::item:pressed {
                color: #e8f0ff;
                background-color: #111722;
            }

            QMenu::item:checked {
                color: #a8bcd4;
                font-weight: 500;
            }

            QMenu::item:disabled {
                color: #2a3a50;
                background: transparent;
            }

            QMenu::separator {
                height: 1px;
                background: #1a2030;
                margin: 4px 8px;
            }

            QMenu::indicator {
                width: 11px;
                height: 11px;
                left: 7px;
                border: 1px solid #263247;
                border-radius: 2px;
                background: #050709;
            }

            QMenu::indicator:checked {
                background: #f59e0b;
                border: 1px solid #f59e0b;
            }

            #appTitle {
                color: #cbd5e1;
                font-family: "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", "Noto Sans", sans-serif;
                font-size: 11px;
                font-weight: 600;
                letter-spacing: 0.25px;
                background: transparent;
            }

            #tradingModeLabel {
                color: #8292a8;
                font-family: "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", "Noto Sans", sans-serif;
                font-size: 9px;
                font-weight: 500;
                letter-spacing: 0.4px;
                background: transparent;
            }

            #titleBarButton {
                background-color: transparent;
                color: #8292a8;
                border: 1px solid transparent;
                font-family: "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", sans-serif;
                font-size: 13px;
                font-weight: 500;
                border-radius: 2px;
                padding: 0px;
            }

            #titleBarButton:hover {
                background-color: #0f1318;
                color: #e8f0ff;
                border: 1px solid #25344a;
            }

            #titleBarButton:pressed {
                background-color: #1a2840;
                color: #e8f0ff;
            }

            #closeTitleBarButton {
                background-color: transparent;
                color: #8292a8;
                border: 1px solid transparent;
                font-family: "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", sans-serif;
                font-size: 12px;
                font-weight: 600;
                border-radius: 2px;
                padding: 0px;
            }

            #closeTitleBarButton:hover {
                background-color: rgba(255,77,106,0.16);
                color: #ff4d6a;
                border: 1px solid rgba(255,77,106,0.36);
            }

            #closeTitleBarButton:pressed {
                background-color: rgba(255,77,106,0.26);
                color: #ffffff;
            }

            QMainWindow, QWidget {
                background-color: #050709;
                color: #e8f0ff;
                font-family: "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", "Noto Sans", sans-serif;
            }

            #scannerPanel, #watchlistPanel, #positionsPanel {
                background-color: #0e1117;
                border: 1px solid #1f2530;
            }

            #positionsPanelContainer {
                background-color: #0e1117;
                border: 1px solid #1f2530;
            }

            #positionsPanelTitleBar {
                background-color: #121826;
                border: none;
                border-top: 1px solid #2a3345;
                border-right: 1px solid #2a3345;
                border-bottom: 1px solid #2a3345;
                min-height: 24px;
            }

            #positionsPanelTitle {
                background-color: transparent;
                color: #d7deef;
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 0.35px;
                border: none;
                padding: 2px 0;
            }

            QToolButton#openPositionsTableButton {
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
                padding: 2px;
            }

            QToolButton#openPositionsTableButton:hover {
                background-color: #1a2840;
                border-color: #2a71c8;
            }

            QToolButton#openPositionsTableButton:pressed {
                background-color: #101d30;
                border-color: #3584e4;
            }

            #primaryChartPanel, #secondaryChartPanel {
                background-color: #0c1016;
                border: 1px solid #202634;
            }

            /* Ultra-thin splitter styling */
            QSplitter { 
                background-color: #0a0a0a;
            }

            QSplitter::handle { 
                background-color: transparent;
                border: none;
                margin: 0px;
            }

            QSplitter::handle:horizontal { 
                width: 1px; 
                background-color: transparent;
                border: none;
            }

            QSplitter::handle:vertical { 
                height: 1px; 
                background-color: transparent;
                border: none;
            }

            QSplitter::handle:hover { 
                background-color: rgba(106, 156, 255, 0.28); 
            }

            QSplitter::handle:pressed {
                background-color: rgba(106, 156, 255, 0.45);
            }

            QSplitter#rightPanelSplitter::handle:vertical {
                background-color: transparent;
                height: 1px;
            }

            QSplitter#rightPanelSplitter::handle:vertical:hover {
                background-color: rgba(106, 156, 255, 0.28);
            }

            /* Ultra-thin scrollbars */
            QScrollBar:vertical { 
                background-color: transparent; 
                width: 4px; 
                border: none; 
                margin: 0px;
            }

            QScrollBar::handle:vertical { 
                background-color: rgba(140, 140, 140, 0.45); 
                border-radius: 2px; 
                min-height: 18px; 
                margin: 0px;
            }

            QScrollBar::handle:vertical:hover { 
                background-color: rgba(170, 170, 170, 0.7); 
            }

            QScrollBar:horizontal { 
                background-color: transparent; 
                height: 4px; 
                border: none; 
                margin: 0px;
            }

            QScrollBar::handle:horizontal { 
                background-color: rgba(140, 140, 140, 0.45); 
                border-radius: 2px; 
                min-width: 18px; 
                margin: 0px;
            }

            QScrollBar::handle:horizontal:hover { 
                background-color: rgba(170, 170, 170, 0.7); 
            }

            QScrollBar::add-line, QScrollBar::sub-line {
                border: none;
                background: none;
                width: 0px;
                height: 0px;
            }

            QDialog { 
                background-color: #121212; 
                border: 1px solid #3f4e66; 
                outline: 1px solid #0a0f18;
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

            #bottomAppStatusBar {
                background-color: #0f131b;
                border-top: 1px solid #2a3342;
                border-bottom: 1px solid #0a0c10;
                min-height: 22px;
                max-height: 24px;
            }

            #statusLabel {
                color: #8a8a8a;
                font-size: 10px;
                background: transparent;
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

        # Auto-focus logic for symbol typing from anywhere on the chart.
        if self._is_symbol_char_key(event) and not self._is_input_focused():
            if not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier |
                                         Qt.KeyboardModifier.AltModifier |
                                         Qt.KeyboardModifier.MetaModifier)):
                # Seed the symbol input with the initiating key without leaving
                # that character selected; otherwise the next keystroke replaces it.
                self.header_toolbar.search_input.start_symbol_entry(event.text())
                return

        # Call parent implementation for all other keys
        super().keyPressEvent(event)

    def _is_symbol_char_key(self, key_event):
        """Check if the pressed key is symbol-search compatible text (A-Z, 0-9)."""
        key = key_event.key()
        is_letter = Qt.Key.Key_A <= key <= Qt.Key.Key_Z
        is_number = Qt.Key.Key_0 <= key <= Qt.Key.Key_9
        return is_letter or is_number

    def _is_input_focused(self):
        """Check if any input field is currently focused."""
        focused_widget = QApplication.focusWidget()

        if focused_widget is None:
            return False

        # Check if the focused widget is an input field
        from PySide6.QtWidgets import QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox

        input_types = (QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox)
        return isinstance(focused_widget, input_types)