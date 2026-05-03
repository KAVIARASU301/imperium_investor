# ==============================================================================
#  MAIN WINDOW
# ==============================================================================

import ast
import logging
import os
import json
import re
from datetime import datetime, timedelta
from typing import List, Dict, Union, Any, Optional

from PySide6.QtCore import Qt, QByteArray, QTimer, Slot, Signal, QEvent
from PySide6.QtWidgets import QMainWindow, QSplitter, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, \
    QPushButton, QLabel, QApplication, QMessageBox, QMenuBar, QSizePolicy
from PySide6.QtGui import QMouseEvent, QKeySequence, QShortcut, QKeyEvent, QAction

from kite.widgets.scanner_table import ChartinkScannerTable
from kite.widgets.positions_table import PositionsTable
from kite.widgets.watchlist_table import TabbedWatchlistWidget
from chart_engine import CandlestickChart as ChartWindow
from kite.widgets.header_toolbar import HeaderToolbar
from kite.widgets.color_settings_dialog import ColorSettingsDialog

from kite.widgets.order_dialog import OrderDialog
from kite.widgets.order_history_dialog import OrderHistoryDialog
from kite.widgets.pending_orders_dialog import PendingOrdersDialog
from kite.widgets.performance_dialog import PerformanceDialog
from kite.widgets.pnl_history_dialog import PnlHistoryDialog
from kite.widgets.floating_positions_dialog import FloatingPositionsDialog
from kite.widgets.floating_watchlist_dialog import attach_floating_watchlist
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
    StatusBar,
    show_error, show_info, show_order_failed,
    show_order_completed, show_order_rejected, show_order_cancelled,
    status  # Global status manager
)
from kite.utils.sounds import play_alert, play_error
from kite.utils.color_system import get_color_theme_manager


logger = logging.getLogger(__name__)


class QullamaggieWindow(CleanShutdownMixin, PaperTradingMixin, QMainWindow):
    """
    SIMPLIFIED Main Window with subtle bottom status bar:
    - Simple Position Manager (only works when tracking orders)
    - Bottom app status bar for market/API/heartbeat indicators
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
        self.position_manager = PositionManager(self.trader, main_window=self, trade_logger=self.trade_logger)

        self.chart_lines_manager = ChartLinesManager(self)

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
        self.pending_orders_dialog = None
        self.performance_dialog = None
        self.pnl_history_dialog = None
        self.floating_positions_dialog = None
        self.floating_watchlist_dialog = None
        self._last_spacebar_context = None

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

        logger.info("Simplified Qullamaggie Window with Status Bar Initialized Successfully.")

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
        self.header_toolbar = HeaderToolbar(toolbar_client, self)
        self.header_toolbar.color_settings_requested.connect(self._open_color_settings_dialog)
        main_layout.addWidget(self.header_toolbar)

        # Create the main splitter
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.main_splitter, 1)
        self._is_adjusting_splitter = False

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
        right_panel_splitter.setHandleWidth(1)

        self.watchlist.setMinimumHeight(150)
        self.positions_table.setMinimumHeight(100)

        # Keep side panels compact while preserving readability.
        self.chartink_scanner.setMinimumWidth(220)
        right_panel_splitter.setMinimumWidth(280)
        self.candlestick_chart.setMinimumWidth(520)

        # Add to the main splitter
        self.main_splitter.addWidget(self.chartink_scanner)
        self.main_splitter.addWidget(self.candlestick_chart)
        self.main_splitter.addWidget(right_panel_splitter)

        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(1)
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 5)
        self.main_splitter.setStretchFactor(2, 2)
        self.main_splitter.setSizes([220, 900, 320])
        self.main_splitter.splitterMoved.connect(self._on_main_splitter_moved)
        self.main_splitter.splitterMoved.connect(self._queue_window_state_save)

        self.right_panel_splitter = right_panel_splitter

        # Bottom status bar (quiet app-level health indicators)
        self.app_status_bar = StatusBar(self)
        self.app_status_bar.setObjectName("bottomAppStatusBar")
        alignment = self.color_theme_manager.get_theme().get("status_bar_alignment", "left")
        self.app_status_bar.set_elements_alignment(alignment)
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
        self._apply_intelligent_main_splitter_layout()

    def _create_menu_bar(self) -> QMenuBar:
        """Create a classic top-level menu bar for quick access to key actions."""
        menu_bar = QMenuBar()
        menu_bar.setObjectName("mainMenuBar")
        # Keep the menu rendered inside our custom title bar.
        # Without this, some desktop environments may move it to a system/global
        # menubar, making it appear missing from the app window.
        menu_bar.setNativeMenuBar(False)

        file_menu = menu_bar.addMenu("File")
        file_menu.addAction("Order History", self._show_order_history_dialog)
        file_menu.addAction("P&L History", self._show_pnl_history_dialog)
        file_menu.addAction("Pending Orders", self._show_pending_orders_dialog)
        file_menu.addAction("Performance", self._show_performance_dialog)
        file_menu.addAction("Floating Positions", self._show_floating_positions_dialog)
        file_menu.addAction("Floating Watchlist", self._show_floating_watchlist_dialog)
        file_menu.addSeparator()
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

        tools_menu = menu_bar.addMenu("Tools")
        tools_menu.addAction("Color Settings", self._open_color_settings_dialog)
        tools_menu.addAction("Open Order Dialog", self._show_order_dialog)
        tools_menu.addSeparator()
        tools_menu.addAction("Relay Server Settings", self._show_relay_settings_dialog)

        about_menu = menu_bar.addMenu("About")
        about_menu.addAction("About Qullamaggie", self._show_about_dialog)

        return menu_bar

    def _setup_status_indicators(self) -> None:
        """Drive subtle bottom-bar operational indicators."""
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.timeout.connect(status.pulse_heartbeat)
        self._heartbeat_timer.start(1000)

        self._market_status_timer = QTimer(self)
        self._market_status_timer.timeout.connect(self._refresh_market_status)
        self._market_status_timer.start(60_000)
        self._refresh_market_status()

    def _refresh_market_status(self) -> None:
        """Update bottom status bar with NSE session status based on IST."""
        now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
        is_weekend = now_ist.weekday() >= 5
        time_tuple = (now_ist.hour, now_ist.minute)
        is_open_time = (9, 15) <= time_tuple <= (15, 30)
        market_status = "OPEN" if (not is_weekend and is_open_time) else "CLOSED"
        status.set_market_indicator(market_status)

    def _show_about_dialog(self):
        """Display application summary information."""
        QMessageBox.information(
            self,
            "About Qullamaggie",
            (
                "Qullamaggie\n"
                "\n"
                "A desktop swing-trading workspace with scanner, chart, watchlist, "
                "and position monitoring tools."
            )
        )

    def _apply_intelligent_main_splitter_layout(self, preferred_sizes=None):
        """Keep scanner/watchlist compact and protect chart space during resize/drag."""
        if self._is_adjusting_splitter:
            return

        splitter_width = self.main_splitter.size().width()
        if splitter_width <= 0:
            return

        sizes = preferred_sizes or self.main_splitter.sizes()
        if len(sizes) != 3:
            return

        left, center, right = sizes
        total = max(1, left + center + right)

        left_visible = self.chartink_scanner.isVisible()
        right_visible = self.right_panel_splitter.isVisible()

        left_min = 200 if left_visible else 0
        right_min = 280 if right_visible else 0
        center_min = max(520, int(splitter_width * 0.45))

        left_max = int(splitter_width * 0.3) if left_visible else 0
        right_max = int(splitter_width * 0.34) if right_visible else 0

        # Start from user ratio if available, then clamp side columns.
        left = max(left_min, min(left, left_max))
        right = max(right_min, min(right, right_max))

        if left + right >= total:
            center = center_min
            remainder = max(0, total - center)
            left = max(left_min, int(remainder * 0.42))
            right = max(right_min, remainder - left)
        else:
            center = total - left - right

        # Guarantee minimum chart width by borrowing proportionally from side panels.
        if center < center_min:
            deficit = center_min - center
            left_spare = max(0, left - left_min)
            right_spare = max(0, right - right_min)
            spare = left_spare + right_spare

            if spare > 0:
                take_left = min(left_spare, int(round(deficit * (left_spare / spare))))
                take_right = min(right_spare, deficit - take_left)
                leftover = deficit - (take_left + take_right)
                if leftover > 0 and left_spare - take_left > 0:
                    extra = min(left_spare - take_left, leftover)
                    take_left += extra
                    leftover -= extra
                if leftover > 0 and right_spare - take_right > 0:
                    take_right += min(right_spare - take_right, leftover)

                left -= take_left
                right -= take_right
                center = total - left - right

        # Final sanity pass.
        left = max(left_min, left)
        right = max(right_min, right)
        center = max(center_min, total - left - right)

        if left + center + right != total:
            center = max(center_min, total - left - right)

        self._is_adjusting_splitter = True
        try:
            self.main_splitter.setSizes([left, center, right])
        finally:
            self._is_adjusting_splitter = False

    def _set_scanner_visible(self, visible: bool):
        self.chartink_scanner.setVisible(visible)
        self._apply_intelligent_main_splitter_layout()
        self._queue_window_state_save()

    def _set_watchlist_visible(self, visible: bool):
        self.watchlist.setVisible(visible)
        self._sync_right_panel_visibility()
        self._queue_window_state_save()

    def _set_positions_visible(self, visible: bool):
        self.positions_table.setVisible(visible)
        self._sync_right_panel_visibility()
        self._queue_window_state_save()

    def _sync_right_panel_visibility(self):
        watchlist_visible = self.watchlist_action.isChecked()
        positions_visible = self.positions_action.isChecked()

        right_visible = watchlist_visible or positions_visible
        self.right_panel_splitter.setVisible(right_visible)

        if right_visible:
            # Recover splitter sizes after both right-side panes were hidden.
            pane_sizes = self.right_panel_splitter.sizes()
            if len(pane_sizes) == 2:
                if watchlist_visible and positions_visible:
                    if pane_sizes[0] == 0 and pane_sizes[1] == 0:
                        self.right_panel_splitter.setSizes([320, 220])
                elif watchlist_visible:
                    self.right_panel_splitter.setSizes([1, 0])
                elif positions_visible:
                    self.right_panel_splitter.setSizes([0, 1])

        self._apply_intelligent_main_splitter_layout()

    def _on_main_splitter_moved(self, _pos: int, _index: int):
        """Prevent one pane from taking all width when dragging splitter handles."""
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
                self.main_splitter.setSizes(self._pending_main_splitter_sizes)
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
        top_bar.setFixedHeight(30)

        root_layout = QGridLayout(top_bar)
        root_layout.setContentsMargins(8, 0, 4, 0)
        root_layout.setHorizontalSpacing(8)
        root_layout.setVerticalSpacing(0)

        self.menu_container = QWidget()
        menu_layout = QHBoxLayout(self.menu_container)
        menu_layout.setContentsMargins(0, 0, 0, 0)
        menu_layout.setSpacing(0)
        self.menu_bar.setFixedHeight(24)
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
        min_btn.setFixedSize(24, 24)
        min_btn.clicked.connect(self.showMinimized)
        controls_layout.addWidget(min_btn)

        self.max_btn = QPushButton("□")
        self.max_btn.setObjectName("titleBarButton")
        self.max_btn.setFixedSize(24, 24)
        self.max_btn.clicked.connect(self._toggle_maximize)
        controls_layout.addWidget(self.max_btn)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeTitleBarButton")
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.close)
        controls_layout.addWidget(close_btn)

        root_layout.addWidget(self.menu_container, 0, 0, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        root_layout.addWidget(self.title_container, 0, 1, alignment=Qt.AlignmentFlag.AlignCenter)
        root_layout.addWidget(self.window_controls, 0, 2, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
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
        self.chartink_scanner.apply_color_theme(theme)
        self.watchlist.apply_color_theme(theme)
        self.positions_table.apply_color_theme(theme)
        self.candlestick_chart.apply_color_theme(theme)
        alignment = str(theme.get("status_bar_alignment", "left"))
        self.app_status_bar.set_elements_alignment(alignment)

    def _open_color_settings_dialog(self):
        dialog = ColorSettingsDialog(self.color_theme_manager.get_theme(), self)
        if dialog.exec():
            self.color_theme_manager.update_theme(dialog.get_theme())

    def _show_relay_settings_dialog(self):
        """Open relay settings and hot-reload an active RelayOrderRouter."""
        from kite.core.relay_order_router import _HMACSigner
        from kite.widgets.relay_settings_widget import RelaySettingsDialog
        from login_setup.token_manager import EnhancedTokenManager

        dialog = RelaySettingsDialog(token_manager=EnhancedTokenManager(), parent=self)

        def _resolve_active_relay_router():
            if hasattr(self.trader, "_cfg"):
                return self.trader
            wrapped_client = getattr(self.trader, "client", None)
            if wrapped_client and hasattr(wrapped_client, "_cfg"):
                return wrapped_client
            return None

        def on_config_saved(new_cfg):
            router = _resolve_active_relay_router()
            if not router:
                status.show_info("Relay config saved. It will be applied on next login/session.")
                return

            if new_cfg:
                router._cfg = new_cfg
                if hasattr(router, "_signer"):
                    router._signer = _HMACSigner(new_cfg.secret)
                try:
                    router.check_health()
                    status.show_info(f"Relay updated and connected: {new_cfg.url}")
                except Exception as e:
                    show_error(f"Relay saved but health check failed: {e}")
            else:
                if hasattr(router, "_cfg") and router._cfg:
                    router._cfg.enabled = False
                status.show_info("Relay routing disabled. Orders will route directly.")

        dialog.config_saved.connect(on_config_saved)
        dialog.exec()

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
        status.set_api_indicator("CONNECTED")

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
                # ALERT DRAG SYNC: chart line drag → alert manager price update
                if hasattr(self.candlestick_chart, 'alert_price_updated'):
                    self.candlestick_chart.alert_price_updated.connect(
                        self.alert_system.update_alert_price_from_chart
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
        self.position_manager.positions_updated.connect(self._update_floating_positions_dialog)
        if hasattr(self, 'market_data_worker') and self.market_data_worker:
            self.market_data_worker.order_update.connect(self.position_manager.on_ws_order_update)
            self.market_data_worker.connection_established.connect(self.position_manager.on_ws_connected)
            self.market_data_worker.connection_closed.connect(self.position_manager.on_ws_disconnected)
        # NO MORE NOTIFICATION SIGNALS - Position manager uses global status directly

        # SIMPLIFIED: Positions Table → Main Window
        self.positions_table.exit_position_requested.connect(self._handle_exit_position_request)
        self.positions_table.exit_half_position_requested.connect(self._handle_exit_half_position_request)
        self.positions_table.symbol_selected.connect(self.candlestick_chart.on_search)
        self.positions_table.subscribe_to_market_data.connect(self._subscribe_to_tokens)

        # Chart → Main Window & Header
        self.candlestick_chart.order_button_clicked.connect(self._show_order_dialog)
        self.candlestick_chart.symbol_loaded.connect(self.header_toolbar.set_current_symbol)
        if self.alert_system:
            self.candlestick_chart.alert_creation_requested.connect(self.alert_system.create_alert_from_chart)

        # Scanner & Watchlist → Chart
        self.chartink_scanner.symbol_selected.connect(self.candlestick_chart.on_search)
        self.chartink_scanner.symbol_selected.connect(self._on_scanner_symbol_selected)
        # Re-evaluate subscription universe whenever scan results refresh or user scrolls
        self.chartink_scanner.scan_results_changed.connect(self._on_watchlist_changed)
        self.chartink_scanner.visible_rows_changed.connect(self._on_watchlist_changed)
        self.watchlist.symbol_selected.connect(self.candlestick_chart.on_search)
        self.watchlist.subscribe_tokens_requested.connect(self._subscribe_to_tokens)
        self.watchlist.place_order_requested.connect(self._show_order_dialog_from_dict)
        self.watchlist.watchlist_changed.connect(self._on_watchlist_changed)
        self.watchlist.watchlist_changed.connect(self._sync_floating_watchlist_dialog)
        self.watchlist.watchlist_changed.connect(self._bind_spacebar_context_tracking)
        self._bind_spacebar_context_tracking()

        # Header Toolbar → Main Window
        self.header_toolbar.symbol_selected.connect(self.candlestick_chart.on_search)
        self.header_toolbar.buy_order_requested.connect(self._on_header_buy_order)
        self.header_toolbar.sell_order_requested.connect(self._on_header_sell_order)
        self.header_toolbar.order_history_requested.connect(self._show_order_history_dialog)
        self.header_toolbar.pending_orders_requested.connect(self._show_pending_orders_dialog)
        self.header_toolbar.performance_dashboard_requested.connect(self._show_performance_dialog)
        self.header_toolbar.positions_requested.connect(self._show_floating_positions_dialog)

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

    def _on_instruments_loaded(self, instruments: List[Dict]):
        """Handle instrument loading with NSE preference"""
        logger.info(f"Successfully loaded {len(instruments)} instruments.")
        self.instrument_list = instruments

        # BUILD INSTRUMENT MAP WITH NSE PREFERENCE
        self.instrument_map = self._build_instrument_map_with_nse_preference(instruments)
        self._token_to_symbol = {
            int(inst.get('instrument_token')): symbol
            for symbol, inst in self.instrument_map.items()
            if inst.get('instrument_token') is not None
        }

        # Set instrument data in components
        self.header_toolbar.set_instrument_data(instruments)
        self.candlestick_chart.set_instrument_list(instruments)
        self.watchlist.set_instrument_map(self.instrument_map)
        self.chartink_scanner.set_instrument_map(self.instrument_map)

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
        """
        Hot path — called on every WebSocket tick batch.
        Keep this MINIMAL: no filtering, no allocation, no logging.
        Each component does its own O(1) lookup.
        """
        if not ticks:
            return

        # 1. Watchlist and scanner — direct dispatch (O(1) per tick per component)
        self.watchlist.update_data(ticks)
        if self.floating_watchlist_dialog and self.floating_watchlist_dialog.isVisible():
            self.floating_watchlist_dialog.update_data(ticks)
        self.chartink_scanner.update_data(ticks)

        # 2. Positions — pass raw ticks; table does O(1) token→row lookup
        for tick in ticks:
            token = tick.get("instrument_token")
            ltp = tick.get("last_price")
            if token is not None and ltp is not None:
                self.positions_table.update_market_data(int(token), float(ltp))
                if self.floating_positions_dialog and self.floating_positions_dialog.isVisible():
                    self.floating_positions_dialog.update_market_data(int(token), float(ltp))

        # 3. Chart — only if token matches current symbol (cheap check)
        chart_token = getattr(self.candlestick_chart, "current_instrument_token", None)
        if chart_token:
            for tick in ticks:
                if tick.get("instrument_token") == chart_token:
                    self.candlestick_chart.update_live_data(tick)
                    break  # only one match possible

        # 4. Paper trader (only in paper mode, lightweight)
        paper_trader = self._get_paper_trading_manager()
        if paper_trader:
            paper_trader.update_market_data(ticks)

        # 5. Alert engine
        if self.alert_system:
            self.alert_system.update_market_data(ticks)

    @Slot(str)
    def _on_scanner_symbol_selected(self, symbol: str):
        """Ensure scanner-selected symbols are subscribed immediately."""
        token = self.instrument_map.get(symbol, {}).get('instrument_token')
        if token:
            self._subscribe_to_tokens([token])

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

        # Priority 4: Scanner-visible symbols
        scanner_tokens = self._get_scanner_visible_tokens()
        all_tokens.update(scanner_tokens)
        logger.info(f"Added {len(scanner_tokens)} scanner tokens")

        # Priority 5: Alert tokens
        alert_tokens = self._get_alert_tokens()
        all_tokens.update(alert_tokens)

        # Subscribe to all tokens (or clear when empty)
        if self.market_data_worker:
            self.market_data_worker.set_instruments(list(all_tokens))
            self._subscribed_tokens = set(all_tokens)
            logger.info(f"Updated subscription universe to {len(all_tokens)} tokens")

    def _get_scanner_visible_tokens(self) -> List[int]:
        """
        Return instrument tokens for rows VISIBLE in the scanner viewport.
        Includes a ±5 row scroll buffer (handled inside get_visible_tokens).
        Never subscribes the full scan result set — only what the trader sees.
        """
        if not hasattr(self, 'chartink_scanner'):
            return []
        return self.chartink_scanner.get_visible_tokens()

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
        dialog = OrderDialog(self, symbol, ltp, order_details, instrument=instrument, ltp_fetcher=self._get_fresh_ltp)
        dialog.order_placed.connect(self._handle_order_placement)
        dialog.show()

    def _show_order_dialog_from_dict(self, order_data: Dict[str, Any]):
        """Show order dialog from watchlist"""
        symbol = order_data.get('tradingsymbol')
        if symbol:
            ltp = self._get_fresh_ltp(symbol)
            instrument = self.instrument_map.get(symbol, {})
            dialog = OrderDialog(self, symbol, ltp, order_data, instrument=instrument, ltp_fetcher=self._get_fresh_ltp)
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
        dialog = OrderDialog(self, symbol, ltp, order_details, instrument=instrument, ltp_fetcher=self._get_fresh_ltp)
        dialog.order_placed.connect(self._handle_order_placement)
        dialog.show()

    def _resolve_position_product(self, symbol: str, fallback: str = "MIS") -> str:
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
            "order_type": "MARKET",
            "product": self._resolve_position_product(symbol, position.product),
            "ltp": ltp,
        }

        instrument = self.instrument_map.get(symbol, {})
        dialog = OrderDialog(self, symbol, ltp, exit_order, instrument=instrument, ltp_fetcher=self._get_fresh_ltp)
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
            "order_type": "MARKET",
            "product": self._resolve_position_product(symbol, position.product),
            "ltp": ltp,
        }

        instrument = self.instrument_map.get(symbol, {})
        dialog = OrderDialog(self, symbol, ltp, exit_order, instrument=instrument, ltp_fetcher=self._get_fresh_ltp)
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
            status.set_message(
                f"Submitting {tx_type} {qty} {symbol}…", 3000, level="action"
            )

            order_id = self.trader.place_order(**order_data)

            if order_id:
                order_data["order_id"] = order_id
                order_data["status"] = "ROUTED"

                status.notify("submitted", symbol)
                self.position_manager.start_tracking_order(order_id, order_data)
                self.position_manager.fetch_positions_from_kite("entry_order_submitted")
                self._log_order_placement_immediate(order_data, order_id)
                logger.info(f"[ENTRY] Order accepted by broker: {order_id}")
            else:
                show_order_failed(f"{symbol} — no order ID returned (possible rejection)")
                logger.warning(f"[ENTRY] Broker returned no order_id for {symbol}")

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

            status.set_message(
                f"Submitting exit {tx_type} {qty} {symbol}…", 3000, level="action"
            )

            order_id = self.trader.place_order(**order_data)

            if order_id:
                order_data["order_id"] = order_id
                order_data["status"] = "ROUTED"
                order_data["_is_exit_order"] = True

                status.notify("submitted", symbol)
                self.position_manager.start_tracking_order(order_id, order_data)
                self.position_manager.fetch_positions_from_kite("exit_order_submitted")
                self._log_order_placement_immediate(order_data, order_id)
                logger.info(f"[EXIT] Exit order accepted: {order_id}")
            else:
                show_order_failed(f"{symbol} exit — no order ID returned")
                logger.warning(f"[EXIT] Broker returned no order_id for exit {symbol}")

        except Exception as e:
            symbol = order_data.get("tradingsymbol", "?")
            compact_error = self._compact_broker_error(e)
            status.notify("rejected", symbol, compact_error)
            logger.error(f"[EXIT] Exit placement exception: {e}", exc_info=True)

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
                self.floating_positions_dialog.exit_position_requested.connect(self._handle_exit_position_request)
                self.floating_positions_dialog.exit_half_position_requested.connect(self._handle_exit_half_position_request)
                self.floating_positions_dialog.subscribe_to_market_data.connect(self._subscribe_to_tokens)

            self._update_floating_positions_dialog(getattr(self.positions_table, 'positions_data', {}).values())
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
                self.floating_watchlist_dialog.table.cellClicked.connect(
                    lambda _r, _c: self._set_last_spacebar_context("floating_watchlist")
                )
            self._sync_floating_watchlist_dialog()
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
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
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

        # Check scanner focus
        if context == "scanner" or self._is_scanner_focused(focused_widget):
            self._set_last_spacebar_context("scanner")
            if hasattr(self.chartink_scanner, '_next_symbol'):
                self.chartink_scanner._next_symbol()
                return

        # Check watchlist focus
        watchlist_table = self._get_focused_watchlist_table(focused_widget)
        if context == "watchlist" and watchlist_table is None:
            watchlist_table = self._get_last_selected_watchlist_table()
        if watchlist_table:
            self._set_last_spacebar_context("watchlist")
            self._navigate_watchlist_symbols(watchlist_table, direction='next')
            return

        # Check positions focus
        if self._is_positions_focused(focused_widget):
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

        if context == "scanner" or self._is_scanner_focused(focused_widget):
            self._set_last_spacebar_context("scanner")
            if hasattr(self.chartink_scanner, '_previous_symbol'):
                self.chartink_scanner._previous_symbol()
                return

        watchlist_table = self._get_focused_watchlist_table(focused_widget)
        if context == "watchlist" and watchlist_table is None:
            watchlist_table = self._get_last_selected_watchlist_table()
        if watchlist_table:
            self._set_last_spacebar_context("watchlist")
            self._navigate_watchlist_symbols(watchlist_table, direction='previous')
            return

        if self._is_positions_focused(focused_widget):
            self._navigate_position_symbols(direction='previous')
            return

        logger.debug("Shift+Space ignored: no focused scanner/watchlist/positions context")

    def _set_last_spacebar_context(self, context: str):
        """Remember where the latest user mouse selection came from."""
        self._last_spacebar_context = context

    def _resolve_spacebar_context(self, focused_widget):
        """Resolve active navigation context from focus first, then last selection source."""
        if self._get_focused_floating_watchlist(focused_widget):
            return "floating_watchlist"
        if self._is_scanner_focused(focused_widget):
            return "scanner"
        if self._get_focused_watchlist_table(focused_widget):
            return "watchlist"
        return self._last_spacebar_context

    def _get_last_selected_watchlist_table(self):
        """Return watchlist table that currently has a row selected."""
        for table in self.watchlist._tables.values():
            if table.currentRow() != -1:
                return table
        return None

    def _bind_spacebar_context_tracking(self):
        """Bind mouse-selection tracking for scanner/watchlist tables."""
        try:
            if hasattr(self.chartink_scanner, "table"):
                scanner_table = self.chartink_scanner.table
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
        """Navigate symbols in watchlist table."""
        if not table:
            return

        row_count = table.rowCount()
        if row_count == 0:
            return

        current_row = table.currentRow()
        if current_row < 0:
            next_row = 0
        elif direction == 'previous':
            next_row = (current_row - 1) % row_count
        else:
            next_row = (current_row + 1) % row_count

        symbol_col = 1
        table.selectRow(next_row)
        table.setCurrentCell(next_row, symbol_col)

        try:
            symbol = None
            if hasattr(table, '_symbol_at_row'):
                symbol = table._symbol_at_row(next_row)
            if not symbol:
                item = table.item(next_row, symbol_col)
                symbol = item.text().strip() if item else None

            if symbol and symbol != 'N/A' and not symbol.startswith('─'):
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
                'main_splitter_sizes': self.main_splitter.sizes(),
                'is_maximized': self.isMaximized(),
                'scanner_visible': self.chartink_scanner.isVisible(),
                'watchlist_visible': self.watchlist_action.isChecked(),
                'positions_visible': self.positions_action.isChecked()
            }

            if hasattr(self, 'right_panel_splitter'):
                state['right_panel_splitter'] = self.right_panel_splitter.saveState().toBase64().data().decode('utf-8')
                state['right_panel_splitter_sizes'] = self.right_panel_splitter.sizes()

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
                        self.main_splitter.setSizes([220, 900, 320])
                else:
                    self.main_splitter.setSizes([220, 900, 320])
                if state.get('main_splitter_sizes'):
                    self._pending_main_splitter_sizes = state['main_splitter_sizes']

                if hasattr(self, 'right_panel_splitter') and 'right_panel_splitter' in state:
                    try:
                        self.right_panel_splitter.restoreState(
                            QByteArray.fromBase64(state['right_panel_splitter'].encode('utf-8')))
                    except Exception as e:
                        logger.warning(f"Failed to restore right panel splitter state: {e}")
                        self.right_panel_splitter.setSizes([320, 220])
                elif hasattr(self, 'right_panel_splitter'):
                    self.right_panel_splitter.setSizes([320, 220])
                if hasattr(self, 'right_panel_splitter') and state.get('right_panel_splitter_sizes'):
                    self._pending_right_splitter_sizes = state['right_panel_splitter_sizes']

                scanner_visible = state.get('scanner_visible', True)
                watchlist_visible = state.get('watchlist_visible', True)
                positions_visible = state.get('positions_visible', True)

                self.scanner_action.setChecked(scanner_visible)
                self.watchlist_action.setChecked(watchlist_visible)
                self.positions_action.setChecked(positions_visible)

                if state.get('is_maximized', False):
                    self.showMaximized()
                    self.max_btn.setText("❐")

                QTimer.singleShot(0, self._apply_pending_splitter_sizes)
                logger.info("Window state restored")
            else:
                # Default state
                self.showMaximized()
                self.max_btn.setText("❐")
                self.main_splitter.setSizes([220, 900, 320])
                if hasattr(self, 'right_panel_splitter'):
                    self.right_panel_splitter.setSizes([320, 220])
                self._apply_intelligent_main_splitter_layout()

        except Exception as e:
            logger.error(f"Failed to restore window state: {e}")
            # Safe fallback
            self.showMaximized()
            self.main_splitter.setSizes([220, 900, 320])
            if hasattr(self, 'right_panel_splitter'):
                self.right_panel_splitter.setSizes([320, 220])
            self._apply_intelligent_main_splitter_layout()


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

            #mainMenuBar {
                background-color: transparent;
                color: #d6d6d6;
                border: none;
                padding: 0px;
            }

            #mainMenuBar::item {
                background: transparent;
                padding: 3px 6px;
                margin: 0px 1px;
            }

            #mainMenuBar::item:selected {
                background: #2a2a2a;
                border-radius: 2px;
            }

            #mainMenuBar::item:pressed {
                background: #1f1f1f;
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

            #bottomAppStatusBar {
                background-color: #0f0f0f;
                border-top: 1px solid #1f1f1f;
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
                # Clear and focus the symbol input
                self.header_toolbar.search_input.clear()
                self.header_toolbar.search_input.setFocus()

                # Send the key to the input field
                self.header_toolbar.search_input.setText(event.text())
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
