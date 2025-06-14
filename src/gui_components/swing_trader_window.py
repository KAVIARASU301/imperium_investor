import os
import json
import shutil
import logging
from datetime import datetime

from PySide6.QtWidgets import QMainWindow, QSplitter
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtMultimedia import QSoundEffect

from src.gui_components.positions_table import PositionsTable
from src.gui_components.widgets.candlestick_chart_widget import ChartWindow
from src.gui_components.tables.chartink_scanner_table import ChartinkScannerTable
from src.gui_components.tables.watchlist_table import WatchlistTable
from src.gui_components.widgets.header_toolbar import HeaderToolbar
from src.gui_components.dialogs.stock_alert_dialog import StockAlertDialog
from src.gui_components.dialogs.alert_logs_dialog import AlertLogsDialog
from src.utils.config_manager import ConfigManager
from src.utils.instrument_loader import InstrumentLoader
from src.utils.theme_manager import ThemeManager

ALERT_FILE = "user_data/alerts.json"
HISTORY_FILE = "user_data/alert_history.json"


class SwingTraderWindow(QMainWindow):
    """Main window for the Swing Trader tool, using PySide6."""

    def __init__(self, trader, real_kite_client, api_key, access_token, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Swing Trader")
        self.setGeometry(150, 150, 1600, 900)

        self.trader = trader
        self.real_kite_client = real_kite_client
        self.api_key = api_key
        self.access_token = access_token
        self.config_manager = ConfigManager()
        self.theme_manager = ThemeManager(self.window())

        # --- NEW: To store a mapping of tradingsymbol to instrument_token ---
        self.instrument_map = {}

        self._init_alerts()
        self._init_ui()
        self._load_instruments()
        self._connect_signals()

    def _init_alerts(self):
        """Initializes the alert system."""
        self.alerts = self._load_json(ALERT_FILE, [])
        self.triggered_alerts = self._load_json(HISTORY_FILE, [])

        # --- REMOVED: TvDatafeed initialization ---
        # No longer needed, we will use the kite_client

        self.alert_timer = QTimer(self)
        self.alert_timer.timeout.connect(self.check_alerts)
        self.alert_timer.start(5000)  # Check every 5 seconds

        sound_file = "icons/notify_sound.wav"
        self.alert_sound = QSoundEffect()
        if os.path.exists(sound_file):
            self.alert_sound.setSource(QUrl.fromLocalFile(sound_file))
            self.alert_sound.setVolume(0.8)

    def _init_ui(self):
        # (This method remains the same as the previous refactoring)
        self.header_toolbar = HeaderToolbar(kite_client=self.real_kite_client)
        self.addToolBar(self.header_toolbar)
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(main_splitter)
        left_panel_splitter = QSplitter(Qt.Orientation.Vertical)
        self.chartink_scanner = ChartinkScannerTable()
        self.positions_table = PositionsTable(trader=self.trader, config_manager=self.config_manager)
        left_panel_splitter.addWidget(self.chartink_scanner)
        left_panel_splitter.addWidget(self.positions_table)
        left_panel_splitter.setSizes([400, 200])
        self.candlestick_chart = ChartWindow(kite_client=self.real_kite_client)
        right_panel_splitter = QSplitter(Qt.Orientation.Vertical)
        self.watchlist1 = WatchlistTable(trader=self.trader, name="Watchlist 1")
        self.watchlist2 = WatchlistTable(trader=self.trader, name="Watchlist 2")
        self.watchlist3 = WatchlistTable(trader=self.trader, name="Watchlist 3")
        right_panel_splitter.addWidget(self.watchlist1)
        right_panel_splitter.addWidget(self.watchlist2)
        right_panel_splitter.addWidget(self.watchlist3)
        main_splitter.addWidget(left_panel_splitter)
        main_splitter.addWidget(self.candlestick_chart)
        main_splitter.addWidget(right_panel_splitter)
        main_splitter.setSizes([350, 900, 350])
        style = "QSplitter::handle:horizontal{width:2px} QSplitter::handle:vertical{height:2px}"
        self.setStyleSheet(style)

    def _connect_signals(self):
        # (This method remains the same)
        self.header_toolbar.theme_switched.connect(self.theme_manager.set_theme)
        self.header_toolbar.symbol_selected.connect(self.on_symbol_selected)
        self.header_toolbar.add_alert_requested.connect(self.show_add_alert_dialog)
        self.header_toolbar.alert_logs_requested.connect(self.show_alert_logs_dialog)
        self.watchlist1.symbol_selected.connect(self.candlestick_chart.on_search)
        self.watchlist2.symbol_selected.connect(self.candlestick_chart.on_search)
        self.watchlist3.symbol_selected.connect(self.candlestick_chart.on_search)
        self.chartink_scanner.table.cellClicked.connect(self.on_scanner_symbol_selected)

    # --- NEW: Method to create instrument map and pass data to header ---
    def _on_instruments_loaded(self, instruments):
        """Callback for when instruments are loaded."""
        self.header_toolbar.set_instrument_data(instruments)
        # Create a map for faster lookups in check_alerts
        self.instrument_map = {
            instrument['tradingsymbol']: instrument['instrument_token']
            for instrument in instruments
        }
        logging.info("Instrument map created for alerts.")

    # --- REFACTORED: `check_alerts` now uses the kite_client ---
    def check_alerts(self):
        """Checks all pending alerts against live market data using Kite client."""
        if not self.real_kite_client or not self.instrument_map:
            return

        active_alerts = [a for a in self.alerts if not a.get('triggered')]
        if not active_alerts:
            return

        instrument_tokens = []
        alerts_by_token = {}
        for alert in active_alerts:
            try:
                # The alert symbol format is "EXCHANGE:TRADINGSYMBOL"
                symbol_name = alert['symbol'].split(':')[-1]
                if symbol_name in self.instrument_map:
                    token = self.instrument_map[symbol_name]
                    if token not in alerts_by_token:
                        instrument_tokens.append(token)
                        alerts_by_token[token] = []
                    alerts_by_token[token].append(alert)
                else:
                    logging.warning(f"Symbol {symbol_name} not found in instrument map. Cannot check alert.")
            except Exception as e:
                logging.error(f"Error processing alert for {alert['symbol']}: {e}")

        if not instrument_tokens:
            return

        try:
            ltp_data = self.real_kite_client.ltp(instrument_tokens)

            for token, alerts in alerts_by_token.items():
                # The keys in the ltp_data dictionary are the instrument tokens
                ltp_info = ltp_data.get(str(token))
                if ltp_info:
                    latest_price = ltp_info['last_price']
                    for alert in alerts:
                        price_threshold = float(alert['price'])
                        condition = alert['condition']

                        is_triggered = (
                                                   condition == "Crosses Above / Current Above" and latest_price >= price_threshold) or \
                                       (
                                                   condition == "Crosses Below / Current Below" and latest_price <= price_threshold)

                        if is_triggered and not alert.get('triggered'):
                            alert['triggered'] = True
                            self.trigger_alert(alert, latest_price)
                else:
                    logging.warning(f"LTP data not found for token: {token}")

        except Exception as e:
            logging.error(f"Error fetching LTP for alerts: {e}")

        self._save_json(ALERT_FILE, self.alerts)

    def trigger_alert(self, alert_data, trigger_price):
        # (This method remains the same)
        self.alert_sound.play()
        self.header_toolbar.set_alert_active(True)
        past_condition = alert_data['condition'].replace("Crosses", "Crossed").replace("/ Current", "")
        triggered_entry = {
            "symbol": alert_data['symbol'], "price": trigger_price, "note": alert_data['note'],
            "condition": past_condition, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self.triggered_alerts.append(triggered_entry)
        self._save_json(HISTORY_FILE, self.triggered_alerts, backup=True)
        logging.info(f"Alert Triggered: {alert_data['symbol']} {past_condition} {trigger_price}")

    def show_add_alert_dialog(self):
        # (This method remains the same)
        dialog = StockAlertDialog(self)
        if dialog.exec():
            alert_data = dialog.get_data()
            alert_data['triggered'] = False
            self.alerts.append(alert_data)
            self._save_json(ALERT_FILE, self.alerts)

    def show_alert_logs_dialog(self):
        # (This method remains the same)
        self.header_toolbar.set_alert_active(False)
        dialog = AlertLogsDialog(self.triggered_alerts, self)
        dialog.exec()

    def _load_json(self, file_path, default=None):
        # (This method remains the same)
        if default is None: default = {}
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    return default
        return default

    def _save_json(self, file_path, data, backup=False):
        # (This method remains the same)
        if backup and os.path.exists(file_path):
            shutil.copy(file_path, file_path.replace(".json", "_backup.json"))
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2)

    def _load_instruments(self):
        """Loads trading instruments using the InstrumentLoader."""
        self.instrument_loader = InstrumentLoader(self.real_kite_client)
        # --- MODIFIED: Connect to the new method ---
        self.instrument_loader.instruments_loaded.connect(self._on_instruments_loaded)
        self.instrument_loader.error_occurred.connect(lambda e: logging.error(f"Instrument loading failed: {e}"))
        self.instrument_loader.start()

    def on_symbol_selected(self, symbol, instrument_token):
        # (This method remains the same)
        self.candlestick_chart.on_search(None, symbol)

    def on_scanner_symbol_selected(self, row, column):
        """Handle symbol selection from the Chartink scanner."""
        symbol_item = self.chartink_scanner.table.item(row, 0)
        if symbol_item:
            self.candlestick_chart.on_search(None, symbol_item.text())