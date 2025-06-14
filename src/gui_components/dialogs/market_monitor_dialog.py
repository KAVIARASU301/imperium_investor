# src/gui_components/dialogs/market_monitor_dialog.py
import logging
from datetime import datetime, timedelta
from typing import Dict, List
import pandas as pd
# --- ADD QByteArray IMPORT ---
from PySide6.QtCore import Qt, QByteArray
# --- END ADD ---
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QWidget,
                               QLabel, QLineEdit, QPushButton, QMessageBox, QComboBox)
from kiteconnect import KiteConnect

from src.gui_components.widgets.market_chart_widget import MarketChartWidget
from src.market_data_worker import MarketDataWorker
from src.utils.config_manager import ConfigManager
from src.utils.cpr_calculator import CPRCalculator

logger = logging.getLogger(__name__)


class MarketMonitorDialog(QDialog):
    # The __init__ method is already correct from the previous step, no changes needed here.
    def __init__(self, real_kite_client: KiteConnect, market_data_worker: MarketDataWorker,
                 instrument_data: dict, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self.kite = real_kite_client
        self.market_data_worker = market_data_worker
        self.instrument_data = instrument_data
        self.config_manager = config_manager
        self.charts: List[MarketChartWidget] = []
        self.token_to_chart_map: Dict[int, MarketChartWidget] = {}
        # --- ADDED: To store the loaded sets ---
        self.symbol_sets: List[Dict] = []

        self._setup_window()
        self._setup_ui()
        self._apply_styles()
        self._restore_state()

        # --- ADDED: Connect signals and load data ---
        self._connect_signals()
        self._load_and_populate_sets()
        # --- END ADDED ---

    # ... other methods ...

    # --- FIX 1: Update _restore_state to use the new method ---
    def _restore_state(self):
        """Restores the dialog's last saved size and position from the config."""
        try:
            # Use the new, correct method and decode the data
            saved_geometry_str = self.config_manager.load_dialog_state('market_monitor')
            if saved_geometry_str:
                self.restoreGeometry(QByteArray.fromBase64(saved_geometry_str.encode('utf-8')))
                logger.info("Market Monitor dialog state restored.")
        except Exception as e:
            logger.error(f"Could not restore Market Monitor dialog state: {e}")

    # --- FIX 2: Update closeEvent to use the new method ---
    def closeEvent(self, event):
        """Saves dialog state and ensures all subscriptions are cancelled."""
        try:
            # Get geometry as QByteArray, encode it, and save it using the new method
            geometry_bytes = self.saveGeometry()
            self.config_manager.save_dialog_state('market_monitor', geometry_bytes.toBase64().data().decode('utf-8'))
            logger.info("Market Monitor dialog state saved.")
        except Exception as e:
            logger.error(f"Failed to save Market Monitor dialog state: {e}")

        self._unsubscribe_all()
        logger.info("Market Monitor closed, unsubscribed from its tokens.")
        super().closeEvent(event)

    # ... (The rest of the file remains the same)
    def _setup_window(self):
        self.setWindowTitle("Market Monitor")
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.setMinimumSize(960, 640)

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)
        main_layout.addWidget(self._create_control_panel())
        main_layout.addLayout(self._create_chart_grid())

    def _create_control_panel(self) -> QWidget:
        panel = QWidget()
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # --- REPLACED old layout with new controls ---
        layout.addWidget(QLabel("Symbol Set:"))
        self.set_selector_combo = QComboBox()
        layout.addWidget(self.set_selector_combo)

        self.symbols_entry = QLineEdit()
        layout.addWidget(self.symbols_entry, 1)

        self.save_set_button = QPushButton("Save Set")
        layout.addWidget(self.save_set_button)

        self.load_button = QPushButton("Load Charts")
        layout.addWidget(self.load_button)
        # --- END REPLACEMENT ---
        return panel

    def _create_chart_grid(self) -> QGridLayout:
        grid_layout = QGridLayout()
        grid_layout.setSpacing(15)
        positions = [(i, j) for i in range(2) for j in range(2)]
        for i, j in positions:
            chart_widget = MarketChartWidget(self)
            grid_layout.addWidget(chart_widget, i, j)
            self.charts.append(chart_widget)
        return grid_layout

    def _load_charts_data(self):
        self._unsubscribe_all()
        self.token_to_chart_map.clear()

        symbols_text = self.symbols_entry.text().strip().upper()
        if not symbols_text:
            QMessageBox.warning(self, "Input Required", "Please enter at least one symbol.")
            return

        symbols = [s.strip() for s in symbols_text.split(',') if s.strip()]
        self.load_button.setText("Loading...")
        self.load_button.setEnabled(False)

        tokens_to_subscribe = set()
        for i, chart in enumerate(self.charts):
            if i < len(symbols):
                symbol = symbols[i]
                token = self._get_instrument_token(symbol)
                if token:
                    self.token_to_chart_map[token] = chart
                    tokens_to_subscribe.add(token)
                    self._fetch_and_plot_initial(chart, symbol, token)
                else:
                    chart.show_message("INVALID SYMBOL", f"Could not find token for '{symbol}'")
            else:
                chart.show_message("EMPTY", "Awaiting symbol selection")

        if tokens_to_subscribe:
            self._subscribe_to(tokens_to_subscribe)

        self.load_button.setText("Load Charts")
        self.load_button.setEnabled(True)

    def _on_ticks_received(self, ticks: List[Dict]):
        for tick in ticks:
            token = tick.get('instrument_token')
            chart = self.token_to_chart_map.get(token)
            if chart:
                chart.add_tick(tick)

    def _fetch_and_plot_initial(self, chart: MarketChartWidget, symbol: str, token: int):
        try:
            to_date = datetime.now().date()
            from_date = to_date - timedelta(days=5)
            hist_data = self.kite.historical_data(token, from_date, to_date, "minute")

            if not hist_data:
                raise ValueError("No historical data returned from API.")

            df = pd.DataFrame(hist_data)
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)

            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)

            unique_dates = sorted(pd.Series(df.index.date).unique())
            if len(unique_dates) < 2:
                logger.warning(f"Not enough historical data for {symbol} to create two-day chart.")
                chart.show_message(f"[{symbol}]", "Insufficient data for two-day view.")
                return

            today_date = unique_dates[-1]
            prev_day_date = unique_dates[-2]

            today_df = df[df.index.date == today_date]
            prev_day_df = df[df.index.date == prev_day_date]

            cpr_levels = CPRCalculator.get_previous_day_cpr(df)

            chart.plot_two_day_data(symbol, prev_day_df, today_df, cpr_levels)

        except Exception as e:
            logger.error(f"Failed to fetch or plot initial data for {symbol}: {e}", exc_info=True)
            chart.show_message(f"[{symbol}] DATA ERROR", "Could not load historical data.")

    def _get_instrument_token(self, symbol: str) -> int | None:
        index_token_map = {
            'SENSEX': 265, 'BANKEX': 259, 'NIFTY': 256265, 'NIFTY 50': 256265,
            'BANKNIFTY': 260105, 'NIFTY BANK': 260105, 'FINNIFTY': 257801,
            'NIFTY FIN SERVICE': 257801, 'MIDCPNIFTY': 260841, 'NIFTY MID SELECT': 260841,
        }
        return index_token_map.get(symbol.upper())

    def _subscribe_to(self, tokens: set):
        if self.market_data_worker:
            current_subs = self.market_data_worker.subscribed_tokens
            self.market_data_worker.set_instruments(current_subs.union(tokens))
            logger.info(f"Market Monitor subscribed to: {tokens}")

    def _unsubscribe_all(self):
        if self.market_data_worker and self.token_to_chart_map:
            tokens_to_remove = set(self.token_to_chart_map.keys())
            current_subs = self.market_data_worker.subscribed_tokens
            self.market_data_worker.set_instruments(current_subs - tokens_to_remove)
            logger.info(f"Market Monitor unsubscribed from: {tokens_to_remove}")

    def _connect_signals(self):
        self.load_button.clicked.connect(self._load_charts_data)
        self.save_set_button.clicked.connect(self._save_current_set)
        self.set_selector_combo.currentIndexChanged.connect(self._on_set_selected)
        self.market_data_worker.data_received.connect(self._on_ticks_received)

    def _load_and_populate_sets(self):
        """Loads sets from config and populates the dropdown."""
        self.symbol_sets = self.config_manager.load_market_monitor_sets()
        self.set_selector_combo.clear()
        for symbol_set in self.symbol_sets:
            self.set_selector_combo.addItem(symbol_set.get("name"))

    def _on_set_selected(self, index: int):
        """When a set is chosen, populate the symbol entry field."""
        if 0 <= index < len(self.symbol_sets):
            selected_set = self.symbol_sets[index]
            self.symbols_entry.setText(selected_set.get("symbols", ""))

    def _save_current_set(self):
        """Saves the symbols in the text box to the selected set."""
        current_index = self.set_selector_combo.currentIndex()
        if current_index < 0: return

        new_symbols = self.symbols_entry.text().strip().upper()
        self.symbol_sets[current_index]["symbols"] = new_symbols

        self.config_manager.save_market_monitor_sets(self.symbol_sets)
        QMessageBox.information(self, "Success", f"Set '{self.symbol_sets[current_index]['name']}' saved.")

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #161A25;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QLabel {
                color: #A9B1C3;
                font-size: 13px;
                font-weight: bold;
            }
            QLineEdit {
                background-color: #212635;
                border: 1px solid #3A4458;
                color: #E0E0E0;
                padding: 8px;
                border-radius: 6px;
                font-size: 14px;
            }
            QLineEdit:focus {
                border: 1px solid #29C7C9;
            }
            QPushButton {
                background-color: #29C7C9;
                color: #161A25;
                font-weight: bold;
                font-size: 13px;
                padding: 8px 18px;
                border-radius: 6px;
                border: none;
            }
            QPushButton:hover {
                background-color: #32E0E3;
            }
            QPushButton:pressed {
                background-color: #25B2B4;
            }
        """)

