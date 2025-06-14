import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import pandas as pd
from lightweight_charts import Chart
from PySide6.QtCore import QObject, Signal, Slot, QThread
from PySide6.QtWidgets import QWidget, QVBoxLayout, QMessageBox

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
            from_date = to_date - timedelta(days=730)  # Approx. 2 years of data
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
                df['symbol'] = self.symbol  # Tag the data with the symbol
                self.data_loaded.emit(df)
            else:
                self.load_error.emit(f"No historical data returned for {self.symbol}.")
        except Exception as e:
            logger.error(f"Error in DataLoaderThread for {self.symbol}: {e}")
            self.load_error.emit(str(e))


class ChartWindow(QWidget):
    """A widget for displaying candlestick charts using lightweight-charts."""

    def __init__(self, parent=None, kite_client=None):
        super().__init__(parent)
        self.kite_client = kite_client
        self.data_fetcher = DataFetcher(self.kite_client)

        # --- FIX: Hold the full instrument list for efficient lookups ---
        self.instrument_list: List[Dict] = []
        self.instrument_map: Dict[str, int] = {}

        # --- FIX: Chart object and the layout that contains it ---
        self.chart: Optional[Chart] = None
        self.chart_container_layout: Optional[QVBoxLayout] = None
        self.data_loader_thread: Optional[DataLoaderThread] = None

        self._init_ui()

    def _init_ui(self):
        """Initializes the main layout of the chart widget."""
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        # The chart will be added dynamically to this layout.

    def set_instrument_list(self, instruments: List[Dict]):
        """Receives the full instrument list from the main window for fast lookups."""
        self.instrument_list = instruments
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
            self.on_load_error(f"Could not find instrument token for {symbol}. Instrument list might be out of date.")
            return

        # --- FIX: Use a dedicated thread to fetch data to prevent UI freezes ---
        if self.data_loader_thread and self.data_loader_thread.isRunning():
            self.data_loader_thread.terminate()

        self.data_loader_thread = DataLoaderThread(self.data_fetcher, instrument_token, symbol)
        self.data_loader_thread.data_loaded.connect(self.on_data_loaded)
        self.data_loader_thread.load_error.connect(self.on_load_error)
        self.data_loader_thread.start()

    @Slot(pd.DataFrame)
    def on_data_loaded(self, df: pd.DataFrame):
        """
        --- FIX: Completely rebuilds the chart to ensure it renders correctly. ---
        This is the most critical part of the fix.
        """
        if df.empty:
            self.on_load_error("Received empty data frame.")
            return

        # 1. Clear the old chart and its container layout completely.
        if self.chart_container_layout is not None:
            while self.chart_container_layout.count():
                item = self.chart_container_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            self.main_layout.removeItem(self.chart_container_layout)
            self.chart_container_layout.deleteLater()
            self.chart = None
            self.chart_container_layout = None

        # 2. Create a new container layout and a new chart instance.
        self.chart_container_layout = QVBoxLayout()
        self.chart_container_layout.setContentsMargins(0, 0, 0, 0)

        self.chart = Chart(parent=self, toolbox=True)
        self.chart.legend(visible=True)
        self.chart.topbar.textbox('symbol', df['symbol'].iloc[0])

        # 3. Add the new chart to the new layout, and add that to the main layout.
        self.chart_container_layout.addWidget(self.chart)
        self.main_layout.addLayout(self.chart_container_layout)

        # 4. Set the data and render the chart.
        self.chart.set(df)
        self.chart.load()

        logger.info(f"Chart for {df['symbol'].iloc[0]} loaded successfully.")

    @Slot(str)
    def on_load_error(self, error_message: str):
        """Displays an error message in a popup dialog."""
        QMessageBox.warning(self, "Chart Error", error_message)
        logger.error(f"Chart loading error: {error_message}")
