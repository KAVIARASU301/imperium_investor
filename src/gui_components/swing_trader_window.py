import os
import json
import shutil
import logging
from datetime import datetime
from typing import List, Dict, Union

from PySide6.QtWidgets import QMainWindow, QSplitter, QMessageBox, QDialog
from PySide6.QtCore import Qt, QUrl, QByteArray, QTimer, Slot
from PySide6.QtMultimedia import QSoundEffect

from src.gui_components.positions_table import PositionsTable
from src.gui_components.widgets.candlestick_chart_widget import ChartWindow
from src.gui_components.tables.chartink_scanner_table import ChartinkScannerTable
from src.gui_components.tables.watchlist_table import WatchlistTable
from src.gui_components.widgets.header_toolbar import HeaderToolbar
from src.gui_components.dialogs.stock_alert_dialog import StockAlertDialog
from src.gui_components.dialogs.alert_logs_dialog import AlertLogsDialog
from src.gui_components.menu_bar import create_enhanced_menu_bar
from src.utils.config_manager import ConfigManager
from src.utils.instrument_loader import InstrumentLoader
from src.utils.theme_manager import ThemeManager
from src.market_data_worker import MarketDataWorker
from src.position_manager import PositionManager
from src.paper_trading_manager import PaperTradingManager
from src.utils.trade_logger import TradeLogger
from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)

ALERT_FILE = "user_data/alerts.json"
HISTORY_FILE = "user_data/alert_history.json"


class SwingTraderWindow(QMainWindow):
    """
    Main window for the Swing Trader application, refactored for robustness
    and better component integration.
    """

    def __init__(self, trader: Union[KiteConnect, PaperTradingManager], real_kite_client: KiteConnect, api_key: str,
                 access_token: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Swing Trader Pro")
        self.setMinimumSize(1200, 700)

        # --- Core Components ---
        self.trader = trader
        self.real_kite_client = real_kite_client
        self.api_key = api_key
        self.access_token = access_token
        self.config_manager = ConfigManager()
        self.theme_manager = ThemeManager(self)

        # --- Data & State Management ---
        self.instrument_list: List[Dict] = []
        self.instrument_map: Dict[str, int] = {}  # Map symbol to token
        self.trading_mode = 'paper' if isinstance(trader, PaperTradingManager) else 'live'
        self.trade_logger = TradeLogger(mode=self.trading_mode)
        self.position_manager = PositionManager(self.trader, self.trade_logger)

        # --- UI Initialization ---
        self._init_alert_system()
        self._setup_ui()
        self._setup_menu_bar()
        self._connect_signals()
        self._init_background_workers()

        self.restore_window_state()
        self.statusBar().showMessage("Loading instruments...")

        # Trigger initial position load after a short delay
        QTimer.singleShot(1000, self.position_manager.refresh_from_api)

    def _setup_ui(self):
        """Initializes and arranges all UI widgets."""
        self.header_toolbar = HeaderToolbar(kite_client=self.real_kite_client)
        self.addToolBar(self.header_toolbar)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setHandleWidth(2)
        self.setCentralWidget(self.main_splitter)

        # --- Create Main Widgets ---
        self.chartink_scanner = ChartinkScannerTable()
        self.positions_table = PositionsTable(trader=self.trader, config_manager=self.config_manager)
        self.candlestick_chart = ChartWindow(kite_client=self.real_kite_client)
        self.watchlist1 = WatchlistTable(trader=self.trader, name="Watchlist 1")
        self.watchlist2 = WatchlistTable(trader=self.trader, name="Watchlist 2")
        self.watchlist3 = WatchlistTable(trader=self.trader, name="Watchlist 3")

        # --- Assemble Layout ---
        left_panel_splitter = QSplitter(Qt.Orientation.Vertical)
        left_panel_splitter.addWidget(self.chartink_scanner)
        left_panel_splitter.addWidget(self.positions_table)
        left_panel_splitter.setSizes([450, 250])

        right_panel_splitter = QSplitter(Qt.Orientation.Vertical)
        right_panel_splitter.addWidget(self.watchlist1)
        right_panel_splitter.addWidget(self.watchlist2)
        right_panel_splitter.addWidget(self.watchlist3)

        self.main_splitter.addWidget(left_panel_splitter)
        self.main_splitter.addWidget(self.candlestick_chart)
        self.main_splitter.addWidget(right_panel_splitter)
        self.main_splitter.setSizes([350, 900, 350])

        style = "QSplitter::handle { background-color: #2A3140; } QSplitter::handle:horizontal{width:2px} QSplitter::handle:vertical{height:2px}"
        self.setStyleSheet(style)

    def _setup_menu_bar(self):
        """Creates the main menu bar and connects its actions."""
        # This assumes menu_bar.py provides a similar structure
        menubar, menu_actions = create_enhanced_menu_bar(self)
        self.setMenuBar(menubar)

        # Connect actions - adapt names from your options app's menu bar
        menu_actions.get('refresh', {}).triggered.connect(self.position_manager.refresh_from_api)
        menu_actions.get('exit', {}).triggered.connect(self.close)
        # Add connections for other dialogs like settings, history, etc.

    def _connect_signals(self):
        """Central place to connect all signals and slots."""
        # --- Header & Theme ---
        self.header_toolbar.theme_switched.connect(self.theme_manager.set_theme)
        self.header_toolbar.symbol_selected.connect(self._on_symbol_selected)
        self.header_toolbar.add_alert_requested.connect(self._show_add_alert_dialog)
        self.header_toolbar.alert_logs_requested.connect(self._show_alert_logs_dialog)

        # --- Chart Interactions ---
        self.chartink_scanner.table.cellClicked.connect(self._on_scanner_symbol_selected)
        self.watchlist1.symbol_selected.connect(self.candlestick_chart.on_search)
        self.watchlist2.symbol_selected.connect(self.candlestick_chart.on_search)
        self.watchlist3.symbol_selected.connect(self.candlestick_chart.on_search)

        # --- Position Manager -> Positions Table ---
        self.position_manager.positions_updated.connect(
            self.positions_table.load_initial_positions)  # Use initial load to redraw

        # --- Positions Table -> Main Window / Workers ---
        self.positions_table.exit_requested.connect(self._on_exit_position_requested)
        # Connect the table's request for subscriptions to the worker
        self.positions_table.subscribe_symbols_requested.connect(self._add_subscriptions)

    def _init_background_workers(self):
        """Initializes and starts background threads."""
        # --- Instrument Loader ---
        self.instrument_loader = InstrumentLoader(self.real_kite_client)
        self.instrument_loader.instruments_loaded.connect(self._on_instruments_loaded)
        self.instrument_loader.error_occurred.connect(lambda e: logger.error(f"Instrument loading failed: {e}"))
        self.instrument_loader.start()

        # --- Market Data WebSocket ---
        self.market_data_worker = MarketDataWorker(self.api_key, self.access_token)
        self.market_data_worker.data_received.connect(self._on_market_data)
        self.market_data_worker.start()

    def _init_alert_system(self):
        """Loads alerts and sets up the sound effect."""
        self.alerts = self._load_json(ALERT_FILE, [])
        self.triggered_alerts = self._load_json(HISTORY_FILE, [])
        self.alert_sound = QSoundEffect()
        sound_file = os.path.join("icons", "notify_sound.wav")
        if os.path.exists(sound_file):
            self.alert_sound.setSource(QUrl.fromLocalFile(sound_file))
            self.alert_sound.setVolume(0.8)

    # --- SLOTS FOR HANDLING SIGNALS ---

    @Slot(list)
    def _on_instruments_loaded(self, instruments: List[Dict]):
        """Handles the loaded instrument list."""
        logger.info(f"Successfully loaded {len(instruments)} instruments.")
        self.instrument_list = instruments
        self.header_toolbar.set_instrument_data(instruments)

        # Create a map for quick token lookups for alerts
        self.instrument_map = {
            instrument['tradingsymbol']: instrument['instrument_token']
            for instrument in instruments if 'tradingsymbol' in instrument and 'instrument_token' in instrument
        }
        logger.info("Instrument map created for alerts.")
        self.candlestick_chart.set_instrument_list(instruments)

        self.statusBar().showMessage("Instruments loaded.", 3000)
        self._subscribe_to_alert_tokens()  # Initial subscription

    @Slot(list)
    def _on_market_data(self, ticks: List[Dict]):
        """Distributes live data ticks to relevant components."""
        self.positions_table.on_tick(ticks)
        self._check_alerts(ticks)
        # Forward to watchlists if they need live updates
        self.watchlist1.on_tick(ticks)
        self.watchlist2.on_tick(ticks)
        self.watchlist3.on_tick(ticks)

    @Slot(str, int)
    def _on_symbol_selected(self, symbol: str, instrument_token: int):
        """Handles symbol selection from the main header search."""
        self.candlestick_chart.on_search(symbol=symbol)

    @Slot(int, int)
    def _on_scanner_symbol_selected(self, row: int, column: int):
        """Handles symbol selection from the Chartink scanner table."""
        symbol_item = self.chartink_scanner.table.item(row, 0)
        if symbol_item:
            self.candlestick_chart.on_search(symbol=symbol_item.text())

    @Slot(dict)
    def _on_exit_position_requested(self, position_data: Dict):
        """Handles exit request from the positions table."""
        # Implement the exit logic, possibly showing a confirmation dialog first
        symbol = position_data.get('tradingsymbol')
        pnl = position_data.get('pnl', 0.0)

        reply = QMessageBox.question(
            self, "Confirm Exit", f"Exit position in {symbol}?\nCurrent P&L: ₹{pnl:,.2f}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            logger.info(f"User confirmed exit for {symbol}")
            # Add logic here to place the exit order via self.trader
            # self.trader.place_order(...)
            # After placing, refresh positions
            # QTimer.singleShot(1000, self.position_manager.refresh_from_api)

    @Slot(list)
    def _add_subscriptions(self, tokens: List[int]):
        """Adds a list of instrument tokens to the WebSocket subscription."""
        if self.market_data_worker:
            self.market_data_worker.add_instruments(tokens)

    # --- ALERT MANAGEMENT ---

    def _check_alerts(self, ticks: List[Dict]):
        """Checks incoming ticks against active alerts."""
        if not self.instrument_map: return

        for tick in ticks:
            token = tick['instrument_token']
            ltp = tick['last_price']

            for alert in self.alerts:
                if alert.get('triggered'): continue

                symbol_name = alert['symbol'].split(':')[-1]
                alert_token = self.instrument_map.get(symbol_name)

                if alert_token == token:
                    price_threshold = float(alert['price'])
                    if ((alert['condition'] == "Crosses Above / Current Above" and ltp >= price_threshold) or
                            (alert['condition'] == "Crosses Below / Current Below" and ltp <= price_threshold)):
                        alert['triggered'] = True
                        self._trigger_alert(alert, ltp)

        # Save any changes to alert statuses
        self._save_json(ALERT_FILE, self.alerts)

    def _trigger_alert(self, alert_data: Dict, trigger_price: float):
        """Handles a triggered alert."""
        self.alert_sound.play()
        self.header_toolbar.set_alert_active(True)

        past_condition = alert_data['condition'].replace("Crosses", "Crossed").replace("/ Current", "")
        triggered_entry = {
            "symbol": alert_data['symbol'], "price": trigger_price, "note": alert_data['note'],
            "condition": past_condition, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self.triggered_alerts.append(triggered_entry)
        self._save_json(HISTORY_FILE, self.triggered_alerts, backup=True)
        logger.info(f"Alert Triggered: {alert_data['symbol']} {past_condition} {trigger_price}")

    def _subscribe_to_alert_tokens(self):
        """Subscribes to tokens for all active alerts."""
        if not self.instrument_map or not self.market_data_worker: return

        active_alerts = [a for a in self.alerts if not a.get('triggered')]
        tokens_to_subscribe = {
            self.instrument_map[alert['symbol'].split(':')[-1]]
            for alert in active_alerts if alert['symbol'].split(':')[-1] in self.instrument_map
        }

        if tokens_to_subscribe:
            self.market_data_worker.add_instruments(list(tokens_to_subscribe))
            logger.info(f"Subscribed to {len(tokens_to_subscribe)} tokens for alerts.")

    # --- DIALOG MANAGEMENT ---

    def _show_add_alert_dialog(self):
        dialog = StockAlertDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            alert_data = dialog.get_data()
            alert_data['triggered'] = False
            self.alerts.append(alert_data)
            self._save_json(ALERT_FILE, self.alerts)
            self._subscribe_to_alert_tokens()  # Subscribe to new alert

    def _show_alert_logs_dialog(self):
        self.header_toolbar.set_alert_active(False)
        dialog = AlertLogsDialog(self.triggered_alerts, self)
        dialog.exec()

    # --- HELPERS AND FILE I/O ---

    def _load_json(self, file_path, default=None):
        if default is None: default = {}
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return default
        return default

    def _save_json(self, file_path, data, backup=False):
        if backup and os.path.exists(file_path):
            shutil.copy(file_path, file_path.replace(".json", "_backup.json"))
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w') as f: json.dump(data, f, indent=4)

    # --- WINDOW STATE & CLOSE EVENT ---

    def closeEvent(self, event):
        """Handles the window close event."""
        logger.info("Close event triggered. Stopping workers...")
        if self.market_data_worker: self.market_data_worker.stop()
        if self.instrument_loader and self.instrument_loader.isRunning():
            self.instrument_loader.quit()
            self.instrument_loader.wait(2000)

        self.save_window_state()
        logger.info("Application shutting down.")
        event.accept()

    def save_window_state(self):
        """Saves window geometry and splitter states."""
        try:
            state = {
                'geometry': self.saveGeometry().toBase64().data().decode('utf-8'),
                'state': self.saveState().toBase64().data().decode('utf-8'),
                'splitter': self.main_splitter.saveState().toBase64().data().decode('utf-8')
            }
            self.config_manager.save_window_state(state)
            logger.info("Window state saved.")
        except Exception as e:
            logger.error(f"Failed to save window state: {e}")

    def restore_window_state(self):
        """Restores window geometry and splitter states."""
        try:
            state = self.config_manager.load_window_state()
            if state:
                if state.get('geometry'): self.restoreGeometry(QByteArray.fromBase64(state['geometry'].encode('utf-8')))
                if state.get('state'): self.restoreState(QByteArray.fromBase64(state['state'].encode('utf-8')))
                if state.get('splitter'): self.main_splitter.restoreState(
                    QByteArray.fromBase64(state['splitter'].encode('utf-8')))
                logger.info("Window state restored.")
            else:
                self.setWindowState(Qt.WindowMaximized)
        except Exception as e:
            logger.error(f"Failed to restore window state: {e}")
            self.setWindowState(Qt.WindowMaximized)
