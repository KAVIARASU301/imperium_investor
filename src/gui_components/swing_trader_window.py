import os
import json
import shutil
import logging
from datetime import datetime
from tvDatafeed import TvDatafeed, Interval

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
        self.theme_manager = ThemeManager(self)

        self._init_alerts()
        self._init_ui()
        self._load_instruments()

    def _init_alerts(self):
        """Initializes the alert system."""
        self.alerts = self._load_json(ALERT_FILE, [])
        self.triggered_alerts = self._load_json(HISTORY_FILE, [])

        try:
            username = os.getenv("TV_USERNAME")
            password = os.getenv("TV_PASSWORD")
            self.tv = TvDatafeed(username=username, password=password)
        except Exception as e:
            logging.error(f"Failed to initialize TvDatafeed: {e}")
            self.tv = None

        self.alert_timer = QTimer(self)
        self.alert_timer.timeout.connect(self.check_alerts)
        self.alert_timer.start(30000)  # Check every 30 seconds

        # Sound effect for alerts
        sound_file = "icons/notify_sound.wav"  # Assuming sound is in icons folder
        self.alert_sound = QSoundEffect()
        if os.path.exists(sound_file):
            self.alert_sound.setSource(QUrl.fromLocalFile(sound_file))
            self.alert_sound.setVolume(0.8)

    def _init_ui(self):
        """Initializes the user interface."""
        # --- Header Toolbar ---
        self.header_toolbar = HeaderToolbar()
        self.addToolBar(self.header_toolbar)
        self.header_toolbar.theme_switched.connect(self.theme_manager.set_theme)
        self.header_toolbar.symbol_selected.connect(self.on_symbol_selected)
        self.header_toolbar.add_alert_requested.connect(self.show_add_alert_dialog)
        self.header_toolbar.alert_logs_requested.connect(self.show_alert_logs_dialog)

        # --- Main Layout ---
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(main_splitter)

        # --- Left Panel ---
        left_panel_splitter = QSplitter(Qt.Orientation.Vertical)

        # --- FIXED LINE ---
        self.chartink_scanner = ChartinkScannerTable()

        self.positions_table = PositionsTable(config_manager=self.config_manager)
        left_panel_splitter.addWidget(self.chartink_scanner)
        left_panel_splitter.addWidget(self.positions_table)
        left_panel_splitter.setSizes([400, 200])

        # --- Center Panel ---
        self.candlestick_chart = ChartWindow()

        # --- Right Panel (with stacked watchlists) ---
        right_panel_splitter = QSplitter(Qt.Orientation.Vertical)
        self.watchlist1 = WatchlistTable()
        self.watchlist2 = WatchlistTable()
        self.watchlist3 = WatchlistTable()
        right_panel_splitter.addWidget(self.watchlist1)
        right_panel_splitter.addWidget(self.watchlist2)
        right_panel_splitter.addWidget(self.watchlist3)

        # Add panels to main splitter
        main_splitter.addWidget(left_panel_splitter)
        main_splitter.addWidget(self.candlestick_chart)
        main_splitter.addWidget(right_panel_splitter)
        main_splitter.setSizes([350, 900, 350])

        style = "QSplitter::handle:horizontal{width:2px} QSplitter::handle:vertical{height:2px}"
        self.setStyleSheet(style)

    def check_alerts(self):
        """Checks all pending alerts against live market data."""
        if not self.tv:
            return

        active_alerts = [a for a in self.alerts if not a.get('triggered')]
        for alert in active_alerts:
            try:
                exchange, symbol_name = alert['symbol'].split(":")
                data = self.tv.get_hist(symbol=symbol_name, exchange=exchange, interval=Interval.in_1_minute, n_bars=1)
                if data is None or data.empty:
                    continue

                latest_price = data['close'].iloc[-1]
                price_threshold = float(alert['price'])
                condition = alert['condition']

                if (condition == "Crosses Above / Current Above" and latest_price >= price_threshold) or \
                        (condition == "Crosses Below / Current Below" and latest_price <= price_threshold):
                    alert['triggered'] = True
                    self.trigger_alert(alert, latest_price)

            except Exception as e:
                logging.error(f"Error checking alert for {alert['symbol']}: {e}")

        self._save_json(ALERT_FILE, self.alerts)

    def trigger_alert(self, alert_data, trigger_price):
        """Handles the logic for a triggered alert."""
        self.alert_sound.play()
        self.header_toolbar.set_alert_active(True)

        past_condition = alert_data['condition'].replace("Crosses", "Crossed").replace("/ Current", "")

        triggered_entry = {
            "symbol": alert_data['symbol'],
            "price": trigger_price,
            "note": alert_data['note'],
            "condition": past_condition,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self.triggered_alerts.append(triggered_entry)
        self._save_json(HISTORY_FILE, self.triggered_alerts, backup=True)
        print(f"Alert Triggered: {alert_data['symbol']} {past_condition} {trigger_price}")

    def show_add_alert_dialog(self):
        """Shows the dialog to add a new stock alert."""
        dialog = StockAlertDialog(self)
        if dialog.exec():
            alert_data = dialog.get_data()
            alert_data['triggered'] = False
            self.alerts.append(alert_data)
            self._save_json(ALERT_FILE, self.alerts)
            print(f"Alert added: {alert_data}")

    def show_alert_logs_dialog(self):
        """Shows the dialog with the history of triggered alerts."""
        self.header_toolbar.set_alert_active(False)  # Deactivate highlight
        dialog = AlertLogsDialog(self.triggered_alerts, self)
        dialog.exec()

    def _load_json(self, file_path, default=None):
        if default is None:
            default = {}
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    return default
        return default

    def _save_json(self, file_path, data, backup=False):
        if backup and os.path.exists(file_path):
            shutil.copy(file_path, file_path.replace(".json", "_backup.json"))

        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2)

    def _load_instruments(self):  # Placeholder
        pass

    def on_symbol_selected(self, symbol, instrument_token):  # Placeholder
        pass