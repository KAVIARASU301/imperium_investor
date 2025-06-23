import logging
import os
import json
import shutil
from typing import List, Dict, Union, Any

from PySide6.QtCore import Qt, QUrl, QByteArray, QTimer, Slot
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtWidgets import QMainWindow, QSplitter, QMessageBox, QDialog, QWidget, QVBoxLayout, QHBoxLayout, \
    QPushButton, QLabel
from PySide6.QtGui import QMouseEvent, QKeySequence, QShortcut

from widgets.menu_bar import create_main_menu
from tables.chartink_scanner_table import ChartinkScannerTable
from tables.positions_table import PositionsTable
from tables.watchlist_table import TabbedWatchlistWidget
from widgets.canvas_candlestick_chart import CandlestickChart as ChartWindow
from widgets.header_toolbar import HeaderToolbar

from dialogs.order_dialog import OrderDialog
from dialogs.settings_dialog import SettingsDialog
from dialogs.order_history_dialog import OrderHistoryDialog
from dialogs.pnl_history_dialog import PnlHistoryDialog
from dialogs.performance_dialog import PerformanceDialog

# Import the new advanced alert system
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
    The main frameless window for the Swing Trader application with professional dark theme.
    Now includes advanced order management, risk management, and enhanced alert system.
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

        # Advanced Alert System - Initialize early
        self.alert_system = None
        self.alert_sound = None

        # Setup frameless window
        self._setup_frameless_window()

        # UI Initialization
        self._setup_ui()
        self._setup_menu_bar()

        # Initialize alert system before other components
        self._init_alert_system()

        # Initialize background workers
        self._init_background_workers()

        # Connect signals after alert system is ready
        self._connect_signals()

        # Initialize advanced components
        self._init_advanced_components()
        self._setup_watchlist_shortcuts()

        self._apply_dark_theme()
        self.restore_window_state()
        logger.info("Swing Trader Window Initialized Successfully.")

        # Call this method after initialization to debug:
        # Add this to the end of __init__ method temporarily:
        QTimer.singleShot(2000, self.debug_alert_buttons)  # Debug after 2 seconds

    # Add this debugging method to test the alert buttons:
    def debug_alert_buttons(self):
        """Debug method to test alert button functionality."""
        logger.info("=== ALERT BUTTON DEBUG ===")

        # Test header toolbar
        if hasattr(self, 'header_toolbar'):
            logger.info("✅ Header toolbar exists")

            # Test alert buttons
            if hasattr(self.header_toolbar, 'quick_alert_button'):
                logger.info("✅ Quick alert button exists")

                # Test manual click
                try:
                    if self.alert_system:
                        self.alert_system.show_quick_alert_dialog()
                        logger.info("✅ Manual alert dialog call successful")
                    else:
                        logger.error("❌ Alert system is None")
                except Exception as e:
                    logger.error(f"❌ Error calling alert dialog: {e}")
            else:
                logger.error("❌ Quick alert button missing")

            if hasattr(self.header_toolbar, 'alert_manager_button'):
                logger.info("✅ Alert manager button exists")
            else:
                logger.error("❌ Alert manager button missing")
        else:
            logger.error("❌ Header toolbar missing")

        # Test alert system
        if self.alert_system:
            logger.info("✅ Alert system exists")

            # Test methods
            if hasattr(self.alert_system, 'show_quick_alert_dialog'):
                logger.info("✅ show_quick_alert_dialog method exists")
            else:
                logger.error("❌ show_quick_alert_dialog method missing")

            if hasattr(self.alert_system, 'show_alert_manager'):
                logger.info("✅ show_alert_manager method exists")
            else:
                logger.error("❌ show_alert_manager method missing")
        else:
            logger.error("❌ Alert system missing")

        logger.info("=== END DEBUG ===")

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
        self.header_toolbar = HeaderToolbar(self.trader, self)
        main_layout.addWidget(self.header_toolbar)

        # Main content splitter
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.main_splitter)

        # Create widgets
        self.chartink_scanner = ChartinkScannerTable()
        self.candlestick_chart = ChartWindow(self.real_kite_client)
        self.watchlist = TabbedWatchlistWidget()
        self.positions_table = PositionsTable(parent=self)

        # Right panel: Watchlist on top (60%), Positions on bottom (40%)
        right_panel_splitter = QSplitter(Qt.Orientation.Vertical)
        right_panel_splitter.addWidget(self.watchlist)
        right_panel_splitter.addWidget(self.positions_table)
        right_panel_splitter.setStretchFactor(0, 3)  # index 0 -> watchlist
        right_panel_splitter.setStretchFactor(1, 2)  # index 1 -> positions

        # Layout: Scanner | Chart | Watchlist + Positions (stacked)
        self.main_splitter.addWidget(self.chartink_scanner)
        self.main_splitter.addWidget(self.candlestick_chart)
        self.main_splitter.addWidget(right_panel_splitter)
        self.main_splitter.setSizes([200, 800, 330])

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

    # Enhanced error handling in _init_alert_system:
    def _init_alert_system(self):
        """Initialize the advanced alert management system."""
        try:
            # Initialize the alert system manager
            self.alert_system = AlertSystemManager(self)
            logger.info("Alert system manager created successfully")

            # Initialize alert sound
            self.alert_sound = QSoundEffect(self)
            sound_file = os.path.join("assets", "alert.mp3")
            if os.path.exists(sound_file):
                self.alert_sound.setSource(QUrl.fromLocalFile(sound_file))
                self.alert_sound.setVolume(1.0)
                logger.info("Alert sound loaded successfully")
            else:
                logger.warning(f"Alert sound file not found at {sound_file}")

            # Connect alert system signals
            if hasattr(self.alert_system, 'alert_sound_requested'):
                self.alert_system.alert_sound_requested.connect(self._play_alert_sound)
                logger.info("Alert sound signal connected")
            else:
                logger.warning("Alert system missing alert_sound_requested signal")

            logger.info("Advanced alert system initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize alert system: {e}")
            self.alert_system = None
            # Don't let this stop the app from loading
            return

    def _connect_signals(self):
        """Connect all signals for seamless integration between components - ENHANCED."""
        logger.info("Connecting enhanced component signals...")

        # === ENHANCED POSITION MANAGER CONNECTIONS ===
        # Core position data flow: Enhanced PositionManager -> Enhanced PositionsTable
        self.position_manager.positions_updated.connect(self.positions_table.update_positions)
        self.position_manager.refresh_completed.connect(self._on_positions_refresh_completed)
        self.position_manager.api_error_occurred.connect(self._on_api_error)

        # ENHANCED: Connect new signals from enhanced position manager
        self.position_manager.position_closed.connect(self._on_position_closed)
        self.position_manager.position_opened.connect(self._on_position_opened)
        self.position_manager.pnl_updated.connect(self._on_pnl_updated)
        self.position_manager.risk_alert.connect(self._on_risk_alert)
        self.position_manager.performance_update.connect(self._on_performance_update)

        # === ENHANCED POSITIONS TABLE CONNECTIONS ===
        # User interactions from enhanced positions table
        self.positions_table.exit_position_requested.connect(self._handle_exit_position_request)
        self.positions_table.symbol_selected.connect(self.candlestick_chart.on_search)
        self.positions_table.subscribe_tokens_requested.connect(self._subscribe_to_tokens)

        # ENHANCED: Connect new signals from enhanced positions table
        self.positions_table.exit_all_positions_requested.connect(self._handle_exit_all_positions)
        self.positions_table.position_details_requested.connect(self._show_position_details)
        self.positions_table.add_alert_requested.connect(self._create_alert_from_position)

        # === CHART CONNECTIONS ===
        # Chart interactions and order placement
        self.candlestick_chart.order_button_clicked.connect(self._show_advanced_order_dialog)

        # === SCANNER AND WATCHLIST CONNECTIONS ===
        # Symbol selection from various sources
        self.chartink_scanner.symbol_selected.connect(self.candlestick_chart.on_search)
        self.chartink_scanner.subscribe_tokens_requested.connect(self._subscribe_to_tokens)

        self.watchlist.symbol_selected.connect(self.candlestick_chart.on_search)
        self.watchlist.subscribe_tokens_requested.connect(self._subscribe_to_tokens)
        self.watchlist.place_order_requested.connect(self._show_advanced_order_dialog_from_dict)
        self.watchlist.watchlist_changed.connect(self._on_watchlist_changed)

        # Advanced order types from watchlist
        self.watchlist.advanced_buy_order_requested.connect(self._show_advanced_buy_order)
        self.watchlist.advanced_sell_order_requested.connect(self._show_advanced_sell_order)
        self.watchlist.bracket_order_requested.connect(self._show_bracket_order)

        # === HEADER TOOLBAR CONNECTIONS ===
        self.header_toolbar.symbol_selected.connect(self.candlestick_chart.on_search)

        # Alert system connections - with better error handling
        try:
            if self.alert_system:
                self.header_toolbar.add_alert_requested.connect(self.alert_system.show_quick_alert_dialog)
                self.header_toolbar.alert_manager_requested.connect(self.alert_system.show_alert_manager)

                # Connect alert logs if the signal exists
                if hasattr(self.header_toolbar, 'alert_logs_requested'):
                    self.header_toolbar.alert_logs_requested.connect(self._show_alert_history)

                logger.info("Alert system signals connected successfully")
            else:
                # Provide fallback handlers for when alert system is not available
                self.header_toolbar.add_alert_requested.connect(self._alert_system_unavailable)
                self.header_toolbar.alert_manager_requested.connect(self._alert_system_unavailable)
                logger.warning("Alert system not available - connected fallback handlers")

        except Exception as e:
            logger.error(f"Failed to connect alert signals: {e}")
            # Connect fallback handlers
            self.header_toolbar.add_alert_requested.connect(self._alert_system_unavailable)
            self.header_toolbar.alert_manager_requested.connect(self._alert_system_unavailable)

        # Chart alert connections
        if self.alert_system and hasattr(self.candlestick_chart, 'alert_creation_requested'):
            self.candlestick_chart.alert_creation_requested.connect(self.alert_system.create_alert_from_chart)

        if self.alert_system and hasattr(self.candlestick_chart, 'order_dialog_requested'):
            self.candlestick_chart.order_dialog_requested.connect(self._handle_chart_order_request)

        # Update alert badges periodically
        self.alert_update_timer = QTimer(self)
        self.alert_update_timer.timeout.connect(self._update_alert_badges)
        self.alert_update_timer.start(30000)  # Update every 30 seconds

        logger.info("All enhanced component signals connected successfully.")

    @Slot(dict)
    def _on_position_closed(self, closure_data: dict):
        """Handle position closure notification from enhanced position manager."""
        try:
            symbol = closure_data.get('tradingsymbol', '')
            pnl = closure_data.get('pnl', 0.0)

            # Show notification
            message = f"Position closed: {symbol} | P&L: ₹{pnl:,.2f}"
            notification_type = "success" if pnl >= 0 else "info"
            self._show_order_notification(message, notification_type)

            # Play sound for significant P&L
            if abs(pnl) > 1000 and self.alert_sound:  # More than ₹1000
                self.alert_sound.play()

            logger.info(f"Position closed notification: {symbol}, P&L: ₹{pnl:,.2f}")

        except Exception as e:
            logger.error(f"Error handling position closure: {e}")

    @Slot(dict)
    def _on_position_opened(self, position_data: dict):
        """Handle new position notification from enhanced position manager."""
        try:
            symbol = position_data.get('tradingsymbol', '')
            quantity = position_data.get('quantity', 0)

            message = f"New position: {symbol} | Qty: {quantity}"
            self._show_order_notification(message, "info")

            logger.info(f"New position notification: {symbol}, Qty: {quantity}")

        except Exception as e:
            logger.error(f"Error handling new position: {e}")

    @Slot(float, float)
    def _on_pnl_updated(self, unrealized_pnl: float, realized_pnl: float):
        """Handle P&L updates from enhanced position manager."""
        try:
            # Update header toolbar with P&L if it has that functionality
            if hasattr(self.header_toolbar, 'update_pnl_display'):
                self.header_toolbar.update_pnl_display(unrealized_pnl, realized_pnl)

            # Log significant P&L changes
            total_pnl = unrealized_pnl + realized_pnl
            if total_pnl != 0:
                logger.debug(f"P&L Update - Unrealized: ₹{unrealized_pnl:,.2f}, Realized: ₹{realized_pnl:,.2f}")

        except Exception as e:
            logger.error(f"Error handling P&L update: {e}")

    @Slot(str, float)
    def _on_risk_alert(self, message: str, risk_value: float):
        """Handle risk alerts from enhanced position manager."""
        try:
            # Show prominent risk alert
            self._show_order_notification(f"RISK ALERT: {message}", "error")

            # Play alert sound
            if self.alert_sound:
                self.alert_sound.play()

            logger.warning(f"Risk Alert: {message} (Value: {risk_value})")

        except Exception as e:
            logger.error(f"Error handling risk alert: {e}")

    @Slot(dict)
    def _on_performance_update(self, performance_data: dict):
        """Handle performance updates from enhanced position manager."""
        try:
            # Update any performance displays in the UI
            if hasattr(self.header_toolbar, 'update_performance_metrics'):
                self.header_toolbar.update_performance_metrics(performance_data)

            # Log performance milestones
            win_rate = performance_data.get('win_rate', 0)
            total_trades = performance_data.get('total_trades', 0)

            if total_trades > 0 and total_trades % 10 == 0:  # Every 10 trades
                logger.info(f"Performance Update - Trades: {total_trades}, Win Rate: {win_rate:.1f}%")

        except Exception as e:
            logger.error(f"Error handling performance update: {e}")

    @Slot()
    def _handle_exit_all_positions(self):
        """Handle exit all positions request from enhanced positions table."""
        try:
            # Show confirmation dialog
            reply = QMessageBox.question(
                self,
                "Exit All Positions",
                "Are you sure you want to exit ALL open positions?\n\nThis action cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes:
                positions = self.position_manager.get_all_positions()

                for position in positions:
                    try:
                        # Create exit order for each position
                        transaction_type = "SELL" if position.quantity > 0 else "BUY"
                        exit_quantity = abs(position.quantity)

                        exit_order = {
                            "tradingsymbol": position.tradingsymbol,
                            "transaction_type": transaction_type,
                            "quantity": exit_quantity,
                            "order_type": "MARKET",
                            "product": position.product
                        }

                        # Place exit order
                        self._handle_order_placement(exit_order)

                    except Exception as e:
                        logger.error(f"Error exiting position {position.tradingsymbol}: {e}")

                self._show_order_notification(f"Exit orders placed for {len(positions)} positions", "info")
                logger.info(f"Exit all positions requested for {len(positions)} positions")

        except Exception as e:
            logger.error(f"Error handling exit all positions: {e}")

    @Slot(str)
    def _show_position_details(self, symbol: str):
        """Show detailed position information."""
        try:
            position = self.position_manager.get_position_by_symbol(symbol)
            if not position:
                self._show_order_notification(f"Position not found for {symbol}", "error")
                return

            # Create detailed position info dialog
            details = f"""
                        Position Details for {symbol}:
                    
                        Quantity: {position.quantity}
                        Average Price: ₹{position.average_price:.2f}
                        Current Price: ₹{position.ltp:.2f}
                        P&L: ₹{position.pnl:,.2f}
                        P&L %: {getattr(position, 'pnl_percent', 0):.2f}%
                        Investment: ₹{getattr(position, 'investment', 0):,.2f}
                        Market Value: ₹{getattr(position, 'market_value', 0):,.2f}
                        Product: {position.product}
                        Exchange: {position.exchange}
                        Last Updated: {getattr(position, 'last_updated', 'Unknown')}
                """

            QMessageBox.information(self, f"Position Details - {symbol}", details.strip())

        except Exception as e:
            logger.error(f"Error showing position details for {symbol}: {e}")

    @Slot(str, float)
    def _create_alert_from_position(self, symbol: str, price: float):
        """Create price alert from position context menu."""
        try:
            if self.alert_system:
                self.alert_system.create_alert_from_chart(symbol, price)
            else:
                self._alert_system_unavailable()

        except Exception as e:
            logger.error(f"Error creating alert for {symbol}: {e}")

    def _alert_system_unavailable(self):
        """Fallback handler when alert system is not available."""
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(
            self,
            "Alert System Unavailable",
            "The alert system is currently unavailable. Please check the logs for initialization errors."
        )

    def _show_alert_history(self):
        """Show alert history dialog."""
        if self.alert_system:
            try:
                # Show the main alert manager with history tab selected
                self.alert_system.show_alert_manager()
                if hasattr(self.alert_system.alert_manager_dialog, 'tab_widget'):
                    self.alert_system.alert_manager_dialog.tab_widget.setCurrentIndex(2)  # History tab
            except Exception as e:
                logger.error(f"Error showing alert history: {e}")
                self._alert_system_unavailable()
        else:
            self._alert_system_unavailable()

    #Enhanced debugging - add this method for testing:
    def test_alert_connections(self):
        """Test method to verify alert connections - call this after initialization."""
        logger.info("Testing alert system connections...")

        # Test if alert system exists
        if not self.alert_system:
            logger.error("❌ Alert system is None")
            return False

        # Test if header toolbar exists and has the right signals
        if not hasattr(self.header_toolbar, 'add_alert_requested'):
            logger.error("❌ Header toolbar missing add_alert_requested signal")
            return False

        # Test if alert system has the right methods
        if not hasattr(self.alert_system, 'show_quick_alert_dialog'):
            logger.error("❌ Alert system missing show_quick_alert_dialog method")
            return False

        if not hasattr(self.alert_system, 'show_alert_manager'):
            logger.error("❌ Alert system missing show_alert_manager method")
            return False

        logger.info("✅ All alert system connections verified")
        return True

    def _init_background_workers(self):
        """Initializes and starts background threads for data fetching."""
        # Initialize instrument loader
        self.instrument_loader = InstrumentLoader(self.real_kite_client)
        self.instrument_loader.instruments_loaded.connect(self._on_instruments_loaded)
        self.instrument_loader.error_occurred.connect(
            lambda e: logger.error(f"Critical error loading instruments: {e}")
        )
        self.instrument_loader.start()

        # Initialize market data worker
        self.market_data_worker = MarketDataWorker(self.api_key, self.access_token)
        self.market_data_worker.data_received.connect(self._on_market_data)
        self.market_data_worker.connection_established.connect(self._on_websocket_connect)
        self.market_data_worker.start()

    # ======================
    # ALERT SYSTEM METHODS
    # ======================

    @Slot()
    def _play_alert_sound(self):
        """Play alert notification sound."""
        if self.alert_sound:
            self.alert_sound.play()

    @Slot()
    def _update_alert_badges(self):
        """Update alert notification badges in header toolbar."""
        if self.alert_system and hasattr(self.header_toolbar, 'update_alert_counts'):
            try:
                active_count, triggered_today = self.alert_system.get_notification_counts()
                self.header_toolbar.update_alert_counts(active_count, triggered_today)
            except Exception as e:
                logger.debug(f"Error updating alert badges: {e}")

    @Slot(str)
    def _handle_chart_order_request(self, order_data_json: str):
        """Handle order dialog request from chart."""
        try:
            order_data = json.loads(order_data_json)
            symbol = order_data.get('symbol', '')
            price = order_data.get('price', 0.0)

            if symbol and price > 0:
                self._show_advanced_order_dialog(symbol, price)
            else:
                logger.warning(f"Invalid order request data: {order_data}")

        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"Error processing chart order request: {e}")

    def _get_alert_tokens(self) -> List[int]:
        """Get all instrument tokens needed for active alerts."""
        if not self.alert_system:
            return []

        try:
            return self.alert_system.get_active_alert_tokens()
        except Exception as e:
            logger.debug(f"Error getting alert tokens: {e}")
            return []

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
        """Enhanced LTP fetching with better fallback logic."""
        ltp = 0.0

        # Try watchlist data first (most up-to-date)
        if hasattr(self.watchlist, '_tables'):
            for table in self.watchlist._tables.values():
                if hasattr(table, '_watchlist_data') and symbol in table._watchlist_data:
                    ltp = table._watchlist_data[symbol].get('ltp', 0.0)
                    if ltp > 0:
                        logger.debug(f"LTP for {symbol} from watchlist: {ltp}")
                        return ltp

        # Try scanner data
        if hasattr(self.chartink_scanner, '_symbol_data'):
            scanner_data = self.chartink_scanner._symbol_data.get(symbol, {})
            ltp = scanner_data.get('ltp', 0)
            if ltp > 0:
                logger.debug(f"LTP for {symbol} from scanner: {ltp}")
                return ltp

        # Try instrument map
        if symbol in self.instrument_map:
            ltp = self.instrument_map[symbol].get('last_price', 0)
            if ltp > 0:
                logger.debug(f"LTP for {symbol} from instrument map: {ltp}")
                return ltp

        # Fallback to API quote (only if we have permission and real client)
        if not ltp and self.real_kite_client:
            try:
                instrument_info = self.instrument_map.get(symbol, {})
                token = instrument_info.get('instrument_token')
                exchange = instrument_info.get('exchange', 'NSE')

                if token:
                    quote_key = f"{exchange}:{symbol}"
                    quote = self.real_kite_client.quote([quote_key])
                    if quote_key in quote:
                        ltp = quote[quote_key].get('last_price', 0)
                        if ltp > 0:
                            logger.debug(f"LTP for {symbol} from Kite API: {ltp}")
                            return ltp
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
                order_id = self._place_order_direct(complete_order_data)

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
                return order_id

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

        dialog = OrderDialog(self, order_details)
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

                # Update alert system with new positions
                if self.alert_system:
                    self.alert_system.update_positions(positions)

            except Exception as e:
                logger.error(f"Failed to refresh positions: {e}")

    @Slot()
    def _on_positions_refresh_completed(self):
        """Handle completion of position refresh from API."""
        logger.debug("Position refresh completed.")

        # Update risk manager with latest positions
        if self.risk_manager:
            positions = self.position_manager.get_all_positions()
            self.risk_manager.update_positions(positions)

    @Slot(str)
    def _on_api_error(self, error_message: str):
        """Handle API errors from position manager."""
        logger.error(f"Position Manager API Error: {error_message}")
        self._show_order_notification(f"API Error: {error_message}", "error")

    @Slot(dict)
    def _handle_exit_position_request(self, position_data: dict):
        """Handle exit position request with enhanced validation."""
        try:
            symbol = position_data.get('tradingsymbol', '')
            quantity = position_data.get('quantity', 0)

            if not symbol or quantity == 0:
                logger.warning("Invalid position data for exit request.")
                return

            # Determine transaction type for exit
            transaction_type = "SELL" if quantity > 0 else "BUY"
            exit_quantity = abs(quantity)

            # Show enhanced order dialog for position exit
            self._show_exit_order_dialog(symbol, exit_quantity, transaction_type)

        except Exception as e:
            logger.error(f"Error handling exit position request: {e}")

    def _show_exit_order_dialog(self, symbol: str, quantity: int, transaction_type: str):
        """Show enhanced order dialog specifically for exiting positions."""
        try:
            ltp = self._get_fresh_ltp(symbol)
            dialog = OrderDialog(self, symbol, ltp)

            # Pre-fill with exit details
            exit_order = {
                "tradingsymbol": symbol,
                "transaction_type": transaction_type,
                "quantity": quantity,
                "order_type": "MARKET",  # Default to market order for quick exit
                "product": "NRML"  # Default product
            }

            # Set the order details in dialog if it has that method
            if hasattr(dialog, 'set_order_details'):
                dialog.set_order_details(**exit_order)

            dialog.order_placed.connect(self._handle_order_placement)
            dialog.show()

        except Exception as e:
            logger.error(f"Error showing exit order dialog: {e}")

    # ======================
    # EXISTING METHODS (ENHANCED WITH ALERT INTEGRATION)
    # ======================

    @Slot(list)
    def _on_instruments_loaded(self, instruments: List[Dict]):
        """Enhanced instrument loading with alert system integration."""
        logger.info(f"Successfully loaded {len(instruments)} instruments.")
        self.instrument_list = instruments
        self.instrument_map = {
            inst['tradingsymbol']: inst for inst in instruments if 'tradingsymbol' in inst
        }

        # Distribute instrument data to all components
        self.header_toolbar.set_instrument_data(instruments)
        self.candlestick_chart.set_instrument_list(instruments)
        self.position_manager.set_instrument_data(instruments)

        # Enhanced watchlist instrument map setting
        self.watchlist.set_instrument_map(self.instrument_map)

        # Set for other components
        self.chartink_scanner.set_instrument_map(self.instrument_map)
        self.chartink_scanner.set_kite_client(self.real_kite_client)

        # Set for paper trading manager
        if isinstance(self.trader, PaperTradingManager):
            self.trader.set_instrument_data(instruments)

        # Update alert system with instrument map
        if self.alert_system:
            self.alert_system.set_instrument_map(self.instrument_map)

        # Trigger initial subscription after everything is set up
        self._on_watchlist_changed()

    @Slot(list)
    def _on_market_data(self, ticks: List[Dict]):
        """Enhanced market data distribution with perfect integration."""
        if not ticks:
            return

        try:
            # Update enhanced position manager (this will automatically update the positions table)
            self.position_manager.update_pnl_from_market_data(ticks)

            if isinstance(self.trader, PaperTradingManager):
                self.trader.update_market_data(ticks)
                logger.debug(f"Paper trading manager updated with {len(ticks)} ticks")

            # Update watchlist with enhanced logging
            self.watchlist.update_data(ticks)

            # Update scanner
            self.chartink_scanner.update_data(ticks)

            # Update alert system with live market data
            if self.alert_system:
                self.alert_system.update_market_data(ticks)

            # Pass ticks to the candlestick chart for live updates
            if self.candlestick_chart and ticks:
                self.candlestick_chart.update_live_data(ticks)

            # Update risk manager with latest market data
            if self.risk_manager:
                try:
                    positions = self.position_manager.get_all_positions()
                    daily_pnl = sum(pos.pnl for pos in positions)
                    self.risk_manager.update_daily_pnl(daily_pnl)

                except Exception as e:
                    logger.debug(f"Could not update daily P&L: {e}")

        except Exception as e:
            logger.error(f"Error processing market data: {e}")

    @Slot()
    def _on_watchlist_changed(self):
        """Enhanced watchlist change handler with alert token management."""
        logger.info("Watchlist changed - updating subscriptions")

        # Get all tokens from all components
        all_tokens = set()

        # Add watchlist tokens
        watchlist_tokens = self.watchlist.get_all_tokens()
        all_tokens.update(watchlist_tokens)

        # Add position tokens
        if hasattr(self.positions_table, 'get_all_tokens'):
            all_tokens.update(self.positions_table.get_all_tokens())

        # Add scanner tokens
        if hasattr(self.chartink_scanner, 'get_all_tokens'):
            all_tokens.update(self.chartink_scanner.get_all_tokens())

        # Add alert tokens
        alert_tokens = self._get_alert_tokens()
        all_tokens.update(alert_tokens)

        # Update market data worker subscription
        if self.market_data_worker and all_tokens:
            self.market_data_worker.set_instruments(list(all_tokens))
            logger.info(
                f"Updated subscription to {len(all_tokens)} tokens (including {len(alert_tokens)} alert tokens)")

    @Slot()
    def _on_websocket_connect(self):
        """Consolidates all subscription requests including alerts."""
        logger.info("WebSocket connected/changed. Subscribing to all required tokens.")
        all_tokens = set()

        all_tokens.update(self.positions_table.get_all_tokens())
        all_tokens.update(self.watchlist.get_all_tokens())
        all_tokens.update(self.chartink_scanner.get_all_tokens())

        # Include alert tokens
        alert_tokens = self._get_alert_tokens()
        all_tokens.update(alert_tokens)

        if all_tokens:
            # Convert to list before passing to set_instruments
            self.market_data_worker.set_instruments(list(all_tokens))
            logger.info(f"Subscribed to {len(all_tokens)} instrument tokens (including {len(alert_tokens)} for alerts)")

    @Slot(list)
    def _subscribe_to_tokens(self, tokens: List[int]):
        """Enhanced token subscription with alert token integration."""
        if not self.market_data_worker or not tokens:
            return

        try:
            # Get current subscribed tokens and ensure it's a set
            current_tokens = getattr(self.market_data_worker, 'subscribed_tokens', set())

            # Ensure current_tokens is a set (handle case where it might be a list)
            if isinstance(current_tokens, list):
                current_tokens = set(current_tokens)
            elif not isinstance(current_tokens, set):
                current_tokens = set()

            # Add new tokens
            new_tokens = current_tokens.union(set(tokens))

            # Also include alert tokens
            alert_tokens = self._get_alert_tokens()
            new_tokens.update(alert_tokens)

            # Update subscription - convert to list
            self.market_data_worker.set_instruments(list(new_tokens))
            logger.info(
                f"Added {len(tokens)} new tokens to subscription (total: {len(new_tokens)}, alerts: {len(alert_tokens)})")

        except Exception as e:
            logger.error(f"Failed to subscribe to tokens: {e}")

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

    def _show_advanced_order_dialog_from_dict(self, order_data: Dict[str, Any]):
        """Show advanced order dialog from watchlist context menu."""
        symbol = order_data.get('tradingsymbol', '')
        transaction_type = order_data.get('transaction_type', 'BUY')

        if symbol:
            # Get fresh LTP
            ltp = self._get_fresh_ltp(symbol)

            # Create enhanced order dialog with pre-filled data
            dialog = OrderDialog(self, symbol, ltp, order_data)

            # Set the transaction type in the dialog
            if hasattr(dialog, 'toggle_switch'):
                dialog.toggle_switch.set_buy_mode(transaction_type == 'BUY')

            # Connect signals
            dialog.order_placed.connect(self._handle_order_placement)
            dialog.bracket_order_placed.connect(self._handle_bracket_order_placement)

            dialog.show()


    def _on_order_placed_from_dialog(self, order_data: dict):
        """Handle order placed from dialog."""
        pass
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
        self._show_advanced_order_dialog(order_details['tradingsymbol'], 0.0)  # Pass 0.0 for LTP

    def _show_advanced_sell_order(self, symbol: str):
        """Show advanced sell order dialog."""
        order_details = {
            "tradingsymbol": symbol,
            "transaction_type": "SELL",
            "quantity": 1
        }
        self._show_advanced_order_dialog(order_details['tradingsymbol'], 0.0)  # Pass 0.0 for LTP

    def _show_bracket_order(self, symbol: str):
        """Show bracket order dialog."""
        ltp = self._get_fresh_ltp(symbol)
        dialog = OrderDialog(self, symbol, ltp)

        # Switch to bracket order tab
        dialog.tab_widget.setCurrentIndex(1)

        dialog.bracket_order_placed.connect(self._handle_bracket_order_placement)
        dialog.show()

    # ======================
    # WATCHLIST SHORTCUTS
    # ======================

    def _setup_watchlist_shortcuts(self):
        """Sets up keyboard shortcuts for adding symbols to watchlists."""
        shortcut_map = {
            "Ctrl+Shift+1": "Breakouts",
            "Ctrl+Shift+2": "EP",
            "Ctrl+Shift+3": "Parabolic"
        }

        for key_sequence, category in shortcut_map.items():
            shortcut = QShortcut(QKeySequence(key_sequence), self)
            shortcut.activated.connect(lambda cat=category: self._add_symbol_to_watchlist_from_chart(cat))

        logger.info("Watchlist shortcuts initialized.")

    def _add_symbol_to_watchlist_from_chart(self, category: str):
        """Enhanced symbol addition from chart with better validation."""
        current_symbol = getattr(self.candlestick_chart, 'current_symbol', None)

        if not current_symbol:
            self._show_order_notification("No symbol is currently displayed on the chart.", "info")
            return

        # Check if the symbol is valid and exists in our instrument map
        if current_symbol not in self.instrument_map:
            self._show_order_notification(
                f"Cannot add '{current_symbol}'. Not a valid or recognized trading symbol.", "error"
            )
            return

        # Add to watchlist
        success = self.watchlist.add_symbol(current_symbol, category)

        if success:
            self._show_order_notification(
                f"Added '{current_symbol}' to '{category}' watchlist.", "success"
            )
        else:
            self._show_order_notification(
                f"Failed to add '{current_symbol}' to '{category}' watchlist (may already exist).", "info"
            )

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
        try:
            logger.info("Close event triggered. Saving state and stopping workers...")
            self.save_window_state()

            # Stop advanced components
            if self.order_manager:
                self.order_manager.order_monitor_timer.stop()

            if hasattr(self.chartink_scanner, '_update_timer'):
                self.chartink_scanner._update_timer.stop()

            # Stop alert system
            if self.alert_system:
                try:
                    self.alert_system.stop_engine()
                except Exception as e:
                    logger.error(f"Error stopping alert system: {e}")

            # Stop alert update timer
            if hasattr(self, 'alert_update_timer'):
                self.alert_update_timer.stop()

            # Stop market data worker
            if self.market_data_worker:
                self.market_data_worker.data_received.disconnect()
                self.market_data_worker.connection_established.disconnect()
                self.market_data_worker.stop()

            # Stop instrument loader
            if self.instrument_loader and self.instrument_loader.isRunning():
                self.instrument_loader.quit()
                self.instrument_loader.wait(2000)

            logger.info("Application shut down gracefully.")
            event.accept()

        except Exception as e:
            logger.error(f"Error during application shutdown: {e}")
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

    # Additional helper methods for better integration:

    def enhance_market_data_worker_integration(market_data_worker):
        """
        Enhance the market data worker to better track subscribed tokens
        Add this to your market_data_worker.py file or modify accordingly
        """
        if not hasattr(market_data_worker, 'subscribed_tokens'):
            market_data_worker.subscribed_tokens = set()

        # Override or enhance the set_instruments method
        original_set_instruments = getattr(market_data_worker, 'set_instruments', None)

        def enhanced_set_instruments(tokens):
            """Enhanced set_instruments with better token tracking"""
            if isinstance(tokens, (list, set)):
                market_data_worker.subscribed_tokens = set(tokens)
                logger.info(f"Market data worker subscribed to {len(tokens)} tokens")

                if original_set_instruments:
                    return original_set_instruments(list(tokens))
            else:
                logger.warning("Invalid tokens provided to set_instruments")

        market_data_worker.set_instruments = enhanced_set_instruments

    # Data validation utilities
    def validate_watchlist_data(data: Dict[str, Any]) -> bool:
        """Validate watchlist data structure"""
        required_fields = ['tradingsymbol', 'instrument_token']

        for field in required_fields:
            if field not in data:
                logger.warning(f"Missing required field {field} in watchlist data")
                return False

        return True

    def format_volume_display(volume: int) -> str:
        """Format volume for consistent display across the application"""
        if volume >= 1000000:
            return f"{volume / 1000000:.1f}M"
        elif volume >= 1000:
            return f"{volume / 1000:.0f}K"
        else:
            return str(volume)


    def calculate_change_percentage(current_price: float, previous_close: float) -> float:
        """Calculate percentage change with proper error handling"""
        if previous_close <= 0 or current_price <= 0:
            return 0.0

        return ((current_price - previous_close) / previous_close) * 100