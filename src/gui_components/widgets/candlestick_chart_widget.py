
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any

import pandas as pd
from lightweight_charts.widgets import QtChart
from PySide6.QtCore import Signal, Slot, QThread, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QMessageBox, QStackedWidget, QLabel
from kiteconnect import KiteConnect

from src.utils.data_fetcher import DataFetcher

logger = logging.getLogger(__name__)


class ChartDataLoaderThread(QThread):
    data_loaded = Signal(pd.DataFrame)
    load_error = Signal(str)

    def __init__(self, data_fetcher: DataFetcher, instrument_token: int, symbol: str):
        super().__init__()
        self.data_fetcher = data_fetcher
        self.instrument_token = instrument_token
        self.symbol = symbol

    def run(self):
        try:
            to_date = datetime.now().date()
            from_date = to_date - timedelta(days=730)

            logger.info(f"Fetching historical data for {self.symbol} (Token: {self.instrument_token})")
            historical_data = self.data_fetcher.fetch_historical_data(
                instrument_token=self.instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval="day"
            )

            if historical_data:
                df = pd.DataFrame(historical_data)
                df['date'] = pd.to_datetime(df['date'])
                df.drop_duplicates(subset='date', inplace=True)
                df.rename(columns={'date': 'time'}, inplace=True)
                df.sort_values('time', inplace=True)

                df['open'] = df['open'].astype(float)
                df['high'] = df['high'].astype(float)
                df['low'] = df['low'].astype(float)
                df['close'] = df['close'].astype(float)
                df['volume'] = df['volume'].astype(int)
                df['symbol'] = self.symbol

                self.data_loaded.emit(df)
            else:
                self.load_error.emit(f"No historical data was returned for {self.symbol}.")
        except Exception as e:
            logger.error(f"Error in ChartDataLoaderThread for {self.symbol}: {e}", exc_info=True)
            self.load_error.emit(f"An API error occurred: {e}")


class ChartWindow(QWidget):
    def __init__(self, kite_client: KiteConnect, parent=None):
        super().__init__(parent)
        self.data_fetcher = DataFetcher(kite_client)
        self.instrument_map: Dict[str, Dict[str, Any]] = {}
        self.data_loader_thread: Optional[ChartDataLoaderThread] = None
        self.current_chart_widget: Optional[QWidget] = None

        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        self.stacked_widget = QStackedWidget()
        main_layout.addWidget(self.stacked_widget)

        self.initial_message_label = QLabel("Select a stock to display its chart.")
        self.initial_message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.initial_message_label.setObjectName("messageLabel")
        self.stacked_widget.addWidget(self.initial_message_label)

        self.loading_message_label = QLabel("Loading chart...")
        self.loading_message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_message_label.setObjectName("messageLabel")
        self.stacked_widget.addWidget(self.loading_message_label)

        self.stacked_widget.setCurrentWidget(self.initial_message_label)

    def set_instrument_list(self, instruments: List[Dict[str, Any]]):
        self.instrument_map = {
            inst['tradingsymbol']: inst for inst in instruments if 'tradingsymbol' in inst
        }
        logger.info("Chart widget received the instrument list.")

    @Slot(str)
    def on_search(self, symbol: Optional[str] = None):
        if not symbol:
            return
        instrument_details = self.instrument_map.get(symbol)
        if not instrument_details or 'instrument_token' not in instrument_details:
            self.on_load_error(f"Could not find instrument details for '{symbol}'.")
            return

        self.loading_message_label.setText(f"Loading chart for {symbol}...")
        self.stacked_widget.setCurrentWidget(self.loading_message_label)

        if self.data_loader_thread and self.data_loader_thread.isRunning():
            self.data_loader_thread.terminate()

        instrument_token = instrument_details['instrument_token']
        self.data_loader_thread = ChartDataLoaderThread(self.data_fetcher, instrument_token, symbol)
        self.data_loader_thread.data_loaded.connect(self.on_data_loaded)
        self.data_loader_thread.load_error.connect(self.on_load_error)
        self.data_loader_thread.start()

    @Slot(pd.DataFrame)
    def on_data_loaded(self, df: pd.DataFrame):
        if df.empty:
            self.on_load_error("Received empty data frame.")
            return

        try:
            chart = QtChart(toolbox=True)
            chart.legend(visible=True)
            chart.topbar.textbox('symbol', df['symbol'].iloc[0])
            chart.set(df)

            chart_container = QWidget()
            layout = QVBoxLayout(chart_container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(chart.get_webview())

            if self.current_chart_widget:
                self.stacked_widget.removeWidget(self.current_chart_widget)
                self.current_chart_widget.deleteLater()

            self.stacked_widget.addWidget(chart_container)
            self.stacked_widget.setCurrentWidget(chart_container)
            self.current_chart_widget = chart_container

            logger.info(f"Chart for {df['symbol'].iloc[0]} loaded successfully.")

        except Exception as e:
            self.on_load_error(f"Failed to render chart: {e}")
            logger.error(f"Error creating chart widget: {e}", exc_info=True)

    @Slot(str)
    def on_load_error(self, error_message: str):
        logger.error(f"Chart loading error: {error_message}")
        QMessageBox.warning(self, "Chart Error", error_message)
        self.stacked_widget.setCurrentWidget(self.initial_message_label)

    def _apply_styles(self):
        self.setStyleSheet("""
            #messageLabel {
                color: #8a8a9e;
                font-size: 16px;
                font-family: "Segoe UI";
            }
        """)
