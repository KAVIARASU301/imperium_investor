import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import pandas as pd
from lightweight_charts import Chart
from PySide6.QtCore import Signal, Slot, QThread, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QMessageBox, QStackedWidget, QLabel

from src.utils.data_fetcher import DataFetcher

logger = logging.getLogger(__name__)


class DataLoaderThread(QThread):
    """A dedicated thread to fetch chart data without freezing the UI."""
    data_loaded = Signal(pd.DataFrame)
    load_error = Signal(str)

    def __init__(self, data_fetcher: DataFetcher, instrument_token: int, symbol: str):
        super().__init__()
        self.data_fetcher = data_fetcher
        self.instrument_token = instrument_token
        self.symbol = symbol

    def run(self):
        """Fetches data in the background."""
        try:
            to_date = datetime.now().date()
            from_date = to_date - timedelta(days=730)  # Approx. 2 years
            historical_data = self.data_fetcher.fetch_historical_data(
                self.instrument_token, from_date, to_date, "day"
            )

            if historical_data:
                df = pd.DataFrame(historical_data)
                # Ensure correct data types for the library
                df['time'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
                df['open'] = df['open'].astype(float)
                df['high'] = df['high'].astype(float)
                df['low'] = df['low'].astype(float)
                df['close'] = df['close'].astype(float)
                df['volume'] = df['volume'].astype(int)
                df['symbol'] = self.symbol
                self.data_loaded.emit(df)
            else:
                self.load_error.emit(f"No historical data returned for {self.symbol}.")
        except Exception as e:
            logger.error(f"Error in DataLoaderThread for {self.symbol}: {e}")
            self.load_error.emit(f"API Error: {e}")


class ChartWindow(QWidget):
    """A robust widget for displaying candlestick charts using a QStackedWidget."""

    def __init__(self, parent=None, kite_client=None):
        super().__init__(parent)
        # --- Dependencies ---
        self.data_fetcher = DataFetcher(kite_client)

        # --- State ---
        self.instrument_map: Dict[str, int] = {}
        self.data_loader_thread: Optional[DataLoaderThread] = None
        self.current_chart_widget: Optional[QWidget] = None  # Hold reference to the current chart widget

        # --- UI Components ---
        self.stacked_widget = QStackedWidget()
        self.message_label = QLabel("Select a symbol to display chart.")
        self._setup_ui()

    def _setup_ui(self):
        """Initializes the UI, including the stacked layout for different states."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.stacked_widget)

        # Configure the message label (this is the widget's default state)
        self.message_label.setAlignment(Qt.AlignCenter)
        self.message_label.setStyleSheet("color: #888; font-size: 16px;")

        # Page 0: Initial message. This ensures the widget is never blank.
        self.stacked_widget.addWidget(self.message_label)
        self.stacked_widget.setCurrentWidget(self.message_label)

    def set_instrument_list(self, instruments: List[Dict]):
        """Receives the full instrument list from the main window for fast lookups."""
        self.instrument_map = {
            instrument['tradingsymbol']: instrument['instrument_token']
            for instrument in instruments
            if 'tradingsymbol' in instrument and 'instrument_token' in instrument
        }
        logger.info("ChartWindow has received the instrument list.")

    @Slot(str)
    def on_search(self, symbol: Optional[str] = None):
        """Handles the request to display a new chart by symbol."""
        if not symbol:
            logger.warning("Chart search triggered with no symbol.")
            return

        logger.info(f"Chart: Received symbol '{symbol}'")
        instrument_token = self.instrument_map.get(symbol)

        if not instrument_token:
            self.on_load_error(f"Could not find instrument token for '{symbol}'.")
            return

        # Show a "Loading..." message to the user
        self.message_label.setText(f"Loading chart for {symbol}...")
        self.stacked_widget.setCurrentWidget(self.message_label)

        if self.data_loader_thread and self.data_loader_thread.isRunning():
            self.data_loader_thread.terminate()

        self.data_loader_thread = DataLoaderThread(self.data_fetcher, instrument_token, symbol)
        self.data_loader_thread.data_loaded.connect(self.on_data_loaded)
        self.data_loader_thread.load_error.connect(self.on_load_error)
        self.data_loader_thread.start()

    @Slot(pd.DataFrame)
    def on_data_loaded(self, df: pd.DataFrame):
        """Creates a new chart widget with the loaded data and displays it."""
        if df.empty:
            self.on_load_error("Received empty data frame.")
            return

        # Create a new self-contained widget for the chart
        new_chart_widget = QWidget()
        chart_layout = QVBoxLayout(new_chart_widget)
        chart_layout.setContentsMargins(0, 0, 0, 0)

        chart = Chart(parent=new_chart_widget, toolbox=True, inner_width=0.99, inner_height=0.99)
        chart_layout.addWidget(chart)

        chart.legend(visible=True)
        chart.topbar.textbox('symbol', df['symbol'].iloc[0])

        chart.set(df)
        chart.load()

        # If an old chart widget exists, remove it from the stack and delete it
        if self.current_chart_widget:
            self.stacked_widget.removeWidget(self.current_chart_widget)
            self.current_chart_widget.deleteLater()

        # Add the new widget to the stack, make it the current one to show,
        # and store a reference to it for the next cleanup.
        self.stacked_widget.addWidget(new_chart_widget)
        self.stacked_widget.setCurrentWidget(new_chart_widget)
        self.current_chart_widget = new_chart_widget

        logger.info(f"Chart for {df['symbol'].iloc[0]} loaded successfully.")

    @Slot(str)
    def on_load_error(self, error_message: str):
        """Displays an error message on the message label and also in a popup."""
        logger.error(f"Chart loading error: {error_message}")
        self.message_label.setText(f"Failed to load chart.\n{error_message}")
        self.stacked_widget.setCurrentWidget(self.message_label)
        QMessageBox.warning(self, "Chart Error", error_message)

