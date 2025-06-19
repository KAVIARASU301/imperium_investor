import logging
import os
import json
import shutil
from datetime import datetime
from typing import List, Dict, Union, Any

from PySide6.QtCore import Qt, QUrl, QByteArray, QTimer, Slot, QPoint
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtWidgets import QMainWindow, QSplitter, QMessageBox, QDialog, QWidget, QVBoxLayout, QHBoxLayout, \
    QPushButton, QLabel
from PySide6.QtGui import QMouseEvent

from widgets.menu_bar import create_main_menu
from tables.chartink_scanner_table import ChartinkScannerTable
from tables.open_positions_table import OpenPositionsTable
from tables.watchlist_table import TabbedWatchlistWidget
from widgets.canvas_candlestick_chart import CandlestickChart as ChartWindow
from widgets.header_toolbar import HeaderToolbar
from dialogs.order_confirmation_dialog import OrderConfirmationDialog
from dialogs.settings_dialog import SettingsDialog
from dialogs.stock_alert_dialog import StockAlertDialog
from dialogs.alert_logs_dialog import AlertLogsDialog
from dialogs.order_history_dialog import OrderHistoryDialog
from dialogs.pnl_history_dialog import PnlHistoryDialog
from dialogs.performance_dialog import PerformanceDialog
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

        # Window dragging variables
        self._drag_pos = None
        self._is_maximized = False

        # Setup frameless window
        self._setup_frameless_window()

        # UI Initialization
        self._setup_ui()
        self._setup_menu_bar()
        self._connect_signals()
        self._init_alert_system()
        self._init_background_workers()
        self._apply_dark_theme()

        self.restore_window_state()

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

        # Symbol selection connections
        self.chartink_scanner.symbol_selected.connect(self.candlestick_chart.on_search)
        self.watchlist.symbol_selected.connect(self.candlestick_chart.on_search)
        self.positions_table.symbol_selected.connect(self.candlestick_chart.on_search)

        # Position manager connections
        self.position_manager.positions_updated.connect(self.positions_table.update_positions)

        # Positions table connections
        self.positions_table.exit_position_requested.connect(self._on_exit_position_requested)
        self.positions_table.subscribe_tokens_requested.connect(self._subscribe_to_tokens)

        # Watchlist connections
        self.watchlist.subscribe_tokens_requested.connect(self._subscribe_to_tokens)
        self.watchlist.place_order_requested.connect(self._show_order_dialog)
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
        """Handles the request to exit a position."""
        symbol = position_data.get('tradingsymbol')
        if not symbol:
            logger.warning("Exit requested for position with no symbol.")
            return

        quantity = abs(position_data.get('quantity', 0))

        exit_order = {
            "tradingsymbol": symbol,
            "quantity": quantity,
            "transaction_type": "SELL" if position_data.get('quantity', 0) > 0 else "BUY",
            "order_type": "MARKET",
            "product": position_data.get("product", "NRML")
        }
        self._show_order_dialog(exit_order)

    def _show_order_dialog(self, order_details: Dict[str, Any]):
        """Shows the order confirmation dialog with enhanced LTP fetching."""
        symbol = order_details['tradingsymbol']
        ltp = 0

        # Try multiple sources for LTP
        if hasattr(self.watchlist, '_watchlist_data'):
            for table in getattr(self.watchlist, '_tables', {}).values():
                if hasattr(table, 'get_watchlist_data'):
                    watchlist_data = table.get_watchlist_data()
                    if symbol in watchlist_data:
                        ltp = watchlist_data[symbol].get('ltp', 0)
                        break

        if not ltp and hasattr(self.chartink_scanner, '_symbol_data'):
            scanner_data = self.chartink_scanner._symbol_data.get(symbol, {})
            ltp = scanner_data.get('ltp', 0)

        if not ltp and symbol in self.instrument_map:
            ltp = self.instrument_map[symbol].get('last_price', 0)

        if not ltp and self.real_kite_client:
            try:
                token = self.instrument_map.get(symbol, {}).get('instrument_token')
                if token:
                    quote = self.real_kite_client.quote([token])
                    if str(token) in quote:
                        ltp = quote[str(token)].get('last_price', 0)
            except Exception as e:
                logger.warning(f"Failed to fetch LTP for {symbol}: {e}")

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

    # Dialog methods
    def _show_settings_dialog(self):
        dialog = SettingsDialog(self)
        dialog.exec()

    def _show_order_history_dialog(self):
        orders = self.trade_logger.get_all_trades()
        dialog = OrderHistoryDialog(self)
        dialog.update_orders(orders)
        dialog.exec()

    def _show_pnl_history_dialog(self):
        dialog = PnlHistoryDialog(self.trading_mode, self)
        dialog.exec()

    def _show_performance_dialog(self):
        if hasattr(self.trade_logger, 'calculate_performance_metrics'):
            metrics = self.trade_logger.calculate_performance_metrics()
        else:
            metrics = {}
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

    # Alert system methods
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

    # Utility methods
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

    # Window state methods
    def closeEvent(self, event):
        """Saves window state and stops background workers before closing."""
        logger.info("Close event triggered. Saving state and stopping workers...")
        self.save_window_state()

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


