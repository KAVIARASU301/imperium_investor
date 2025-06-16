# Copyright (C) 2023 The Qt Company Ltd.
# SPDX-License-Identifier: LicenseRef-Qt-Commercial OR BSD-3-Clause

"""
A refactored and optimized Candlestick Chart Widget using native PySide6.QtCharts.
This version replaces the web-based lightweight-charts library with a native
implementation, applying performance best practices where applicable.
"""

import logging
import json
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any

import pandas as pd
from PySide6.QtCharts import (QChart, QChartView, QCandlestickSeries, QLineSeries,
                              QValueAxis, QDateTimeAxis, QCandlestickSet)
from PySide6.QtCore import (Signal, Slot, QThread, Qt, QTimer, QDateTime)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QMessageBox,
                               QStackedWidget, QLabel, QPushButton)
from kiteconnect import KiteConnect


# Assuming data_fetcher.py exists in a 'utils' subfolder
# from utils.data_fetcher import DataFetcher
# Mock DataFetcher for standalone execution
class DataFetcher:
    def __init__(self, client):
        self._client = client

    def fetch_historical_data(self, **kwargs):
        # This should contain the actual API call logic
        # Returning mock data for demonstration purposes
        print(f"Fetching mock data with args: {kwargs}")
        end_date = kwargs.get('to_date', datetime.now().date())
        days = (end_date - kwargs.get('from_date')).days
        data = []
        current_price = 100
        for i in range(days):
            date = kwargs.get('from_date') + timedelta(days=i)
            open_price = current_price + pd.np.random.randn()
            high = open_price + pd.np.random.uniform(0, 5)
            low = open_price - pd.np.random.uniform(0, 5)
            close = low + pd.np.random.uniform(1, 4)
            volume = pd.np.random.randint(100000, 5000000)
            data.append({
                'date': date,
                'open': open_price,
                'high': high,
                'low': low,
                'close': close,
                'volume': volume
            })
            current_price = close
        return data


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class ChartDataLoaderThread(QThread):
    """Handles fetching chart data in a separate thread to keep the UI responsive."""
    data_loaded = Signal(pd.DataFrame)
    load_error = Signal(str)

    def __init__(self, data_fetcher: DataFetcher, instrument_token: int, symbol: str, interval: str):
        super().__init__()
        self.data_fetcher = data_fetcher
        self.instrument_token = instrument_token
        self.symbol = symbol
        self.interval = interval
        self.is_running = True

    def run(self):
        try:
            # Determine date range based on interval to manage data volume
            to_date = datetime.now().date()
            if self.interval == "day":
                from_date = to_date - timedelta(days=730)
            elif self.interval == "60minute":
                from_date = to_date - timedelta(days=150)
            elif self.interval == "15minute":
                from_date = to_date - timedelta(days=60)
            elif self.interval == "5minute":
                from_date = to_date - timedelta(days=30)
            else:
                from_date = to_date - timedelta(days=365)  # Default

            logger.info(f"Fetching data for {self.symbol} ({self.interval}) from {from_date} to {to_date}")

            historical_data = self.data_fetcher.fetch_historical_data(
                instrument_token=self.instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval=self.interval
            )

            if not self.is_running: return  # Check if stopped while fetching

            if historical_data:
                df = pd.DataFrame(historical_data)
                if df.empty:
                    self.load_error.emit(f"No data for {self.symbol} in the date range.")
                    return

                # --- Data Preparation ---
                # This is a critical step for native QtCharts
                df['time'] = pd.to_datetime(df['date'])
                df.drop_duplicates(subset='time', inplace=True)
                df.sort_values('time', inplace=True)
                for col in ['open', 'high', 'low', 'close']: df[col] = df[col].astype(float)
                df['volume'] = df['volume'].astype(int)
                df['symbol'] = self.symbol
                # Convert datetime to milliseconds since epoch for QDateTimeAxis
                df['timestamp'] = df['time'].apply(lambda x: x.timestamp() * 1000)

                logger.info(f"Processed {len(df)} records for {self.symbol}")
                self.data_loaded.emit(df)
            else:
                self.load_error.emit(f"No historical data returned for {self.symbol}.")
        except Exception as e:
            logger.error(f"Error in ChartDataLoaderThread for {self.symbol}: {e}", exc_info=True)
            self.load_error.emit(f"An API error occurred: {e}")

    def stop(self):
        self.is_running = False


class ChartWindow(QWidget):
    """
    Main chart widget using native QtCharts for rendering.
    """

    def __init__(self, kite_client: Optional[KiteConnect] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.data_fetcher = DataFetcher(kite_client)
        self.instrument_map: Dict[str, Dict[str, Any]] = self._load_mock_instruments()
        self.data_loader_thread: Optional[ChartDataLoaderThread] = None
        self.last_df: Optional[pd.DataFrame] = None
        self.current_symbol: Optional[str] = None
        self.current_interval: str = "day"

        self.timeframe_buttons: Dict[str, QPushButton] = {}
        self.chart_state_file = os.path.expanduser("~/.swing_trader/chart_state_native.json")
        os.makedirs(os.path.dirname(self.chart_state_file), exist_ok=True)

        self._setup_ui()
        self._apply_styles()

        QTimer.singleShot(500, self._load_saved_chart_state)

    def _load_mock_instruments(self) -> Dict[str, Dict[str, Any]]:
        """Provides mock data if a live connection isn't available."""
        return {
            "RELIANCE": {"tradingsymbol": "RELIANCE", "instrument_token": 738561},
            "TCS": {"tradingsymbol": "TCS", "instrument_token": 2953217},
            "HDFCBANK": {"tradingsymbol": "HDFCBANK", "instrument_token": 341249},
        }

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Main container to hold chart and loading message
        self.stacked_widget = QStackedWidget()
        main_layout.addWidget(self.stacked_widget)

        # Create chart container with top bar
        self._create_chart_container()

        # Create loading message label
        self.loading_label = QLabel("Loading chart data...")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label.setObjectName("loadingLabel")
        self.stacked_widget.addWidget(self.loading_label)

        self.stacked_widget.setCurrentWidget(self.chart_container)

    def _create_chart_container(self):
        """Creates the main view for the chart and its controls."""
        self.chart_container = QWidget()
        container_layout = QVBoxLayout(self.chart_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # Custom top bar for timeframe buttons
        self.custom_topbar = self._create_timeframe_buttons()
        container_layout.addWidget(self.custom_topbar)

        # --- Native QtChart Implementation ---
        self.chart = QChart()
        self.chart_view = QChartView(self.chart)
        self.chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)  # Good for aesthetics
        # self.chart_view.setRenderHint(QPainter.RenderHint.NoAntialiasing) # Better performance

        # --- Advanced Method: Disable animations for performance ---
        self.chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)

        # Set a dark theme
        self.chart.setTheme(QChart.ChartTheme.ChartThemeDark)
        self.chart.setBackgroundBrush(QColor("#1c1c2e"))
        self.chart.legend().setLabelColor(QColor("white"))
        self.chart.legend().setVisible(True)

        container_layout.addWidget(self.chart_view)
        self.stacked_widget.addWidget(self.chart_container)

    def _create_timeframe_buttons(self) -> QWidget:
        """Creates the custom top bar with timeframe selection buttons."""
        # This UI part is largely the same as the original
        button_widget = QWidget()
        button_widget.setFixedHeight(35)
        button_layout = QHBoxLayout(button_widget)
        button_layout.setContentsMargins(8, 3, 8, 3)
        button_layout.setSpacing(3)

        self.symbol_label = QLabel("Loading...")
        self.symbol_label.setObjectName("symbolLabel")
        button_layout.addWidget(self.symbol_label)

        self.loading_indicator = QLabel("Loading...")
        self.loading_indicator.setObjectName("loadingIndicator")
        self.loading_indicator.hide()
        button_layout.addWidget(self.loading_indicator)

        button_layout.addStretch()

        timeframes = [("1D", "day"), ("1H", "60minute"), ("15m", "15minute"), ("5m", "5minute")]
        for display_text, interval in timeframes:
            btn = QPushButton(display_text)
            btn.setObjectName("timeframeButton")
            btn.setCheckable(True)
            btn.setFixedSize(32, 26)
            btn.clicked.connect(lambda checked, i=interval: self._change_timeframe(i))
            self.timeframe_buttons[interval] = btn
            button_layout.addWidget(btn)

        self.timeframe_buttons["day"].setChecked(True)
        return button_widget

    @Slot(str)
    def on_search(self, symbol: Optional[str] = None):
        """Public slot to load a new symbol into the chart."""
        if not symbol or symbol not in self.instrument_map:
            self.on_load_error(f"Could not find instrument details for '{symbol}'.")
            return

        self.current_symbol = symbol
        is_initial_load = self.last_df is None or self.last_df.empty
        self._load_data(interval=self.current_interval, show_loading_screen=is_initial_load)
        self._save_chart_state()

    def _load_data(self, interval: str, show_loading_screen: bool = True):
        """Manages the data loading process, including UI state changes."""
        if not self.current_symbol or self.current_symbol not in self.instrument_map:
            return

        instrument_token = self.instrument_map[self.current_symbol]['instrument_token']

        if self.data_loader_thread and self.data_loader_thread.isRunning():
            self.data_loader_thread.stop()
            self.data_loader_thread.quit()
            self.data_loader_thread.wait()

        self.loading_indicator.show()
        self.symbol_label.hide()
        if show_loading_screen:
            self.stacked_widget.setCurrentWidget(self.loading_label)

        for btn in self.timeframe_buttons.values(): btn.setEnabled(False)

        self.data_loader_thread = ChartDataLoaderThread(
            self.data_fetcher, instrument_token, self.current_symbol, interval)
        self.data_loader_thread.data_loaded.connect(self.on_data_loaded)
        self.data_loader_thread.load_error.connect(self.on_load_error)
        self.data_loader_thread.start()

    @Slot(pd.DataFrame)
    def on_data_loaded(self, df: pd.DataFrame):
        """This slot receives the data and populates the native QtChart."""
        if df.empty:
            self.on_load_error("Received empty data frame.")
            return

        try:
            self.last_df = df
            self._update_ui_post_load(success=True)

            # --- Chart Rendering Logic for Native QtChart ---
            self.chart.removeAllSeries()
            for axis in self.chart.axes(): self.chart.removeAxis(axis)

            # 1. Create Candlestick Series
            candlestick_series = QCandlestickSeries()
            candlestick_series.setName(self.current_symbol)
            candlestick_series.setIncreasingColor(QColor("#4ECDC4"))  # Green
            candlestick_series.setDecreasingColor(QColor("#FF6B6B"))  # Red

            # Populate with data
            for index, row in df.iterrows():
                # QCandlestickSet requires timestamp_ms, open, high, low, close
                c_set = QCandlestickSet(row['timestamp'], row['open'], row['high'], row['low'], row['close'])
                candlestick_series.append(c_set)

            self.chart.addSeries(candlestick_series)

            # 2. Create SMA Line Series
            data_len = len(df)
            if data_len >= 10: self._add_sma_line(df, 10, '#45B7D1', candlestick_series)
            if data_len >= 21: self._add_sma_line(df, 21, '#FFA07A', candlestick_series)
            if data_len >= 51: self._add_sma_line(df, 51, '#9370DB', candlestick_series)

            # 3. Create and Configure Axes
            axis_x = QDateTimeAxis()
            axis_x.setFormat("dd MMM yyyy")
            axis_x.setTitleText("Date")
            axis_x.setLabelsColor(QColor("white"))
            axis_x.setTitleBrush(QColor("white"))

            axis_y = QValueAxis()
            axis_y.setTitleText("Price")
            axis_y.setLabelsColor(QColor("white"))
            axis_y.setTitleBrush(QColor("white"))

            self.chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
            self.chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)

            candlestick_series.attachAxis(axis_x)
            candlestick_series.attachAxis(axis_y)

            # Re-attach axes for any line series added
            for series in self.chart.series():
                if isinstance(series, QLineSeries):
                    series.attachAxis(axis_x)
                    series.attachAxis(axis_y)

            self.chart.legend().markers(candlestick_series)[0].setVisible(False)
            self.stacked_widget.setCurrentWidget(self.chart_container)

            logger.info(f"Native chart for {self.current_symbol} rendered successfully.")

        except Exception as e:
            self.on_load_error(f"Failed to render native chart: {e}")
            logger.error(f"Error in on_data_loaded: {e}", exc_info=True)

    def _add_sma_line(self, df, period, color, main_series):
        """Helper to calculate and add an SMA line series to the chart."""
        sma_col = f'sma{period}'
        df[sma_col] = df['close'].rolling(window=period).mean()

        line_series = QLineSeries()
        line_series.setName(f"SMA {period}")
        pen = QPen(QColor(color))
        pen.setWidth(1)
        line_series.setPen(pen)

        # Note: OpenGL acceleration is only for QLineSeries and QScatterSeries.
        # It's disabled here to allow mixing with QCandlestickSeries without issues.
        # line_series.setUseOpenGL(True)

        for index, row in df.dropna(subset=[sma_col]).iterrows():
            line_series.append(row['timestamp'], row[sma_col])

        self.chart.addSeries(line_series)

    def _update_ui_post_load(self, success: bool):
        """Consolidates UI updates after a data load attempt."""
        self.loading_indicator.hide()
        self.symbol_label.show()
        for btn in self.timeframe_buttons.values(): btn.setEnabled(True)

        if success:
            interval_display = self._get_interval_display_name(self.current_interval)
            self.symbol_label.setText(f"{self.current_symbol} - {interval_display}")
        else:
            self.symbol_label.setText("Error Loading Chart")

    def _change_timeframe(self, interval: str):
        if self.current_interval == interval:
            return

        for btn_interval, btn in self.timeframe_buttons.items():
            btn.setChecked(btn_interval == interval)

        self.current_interval = interval
        if self.current_symbol:
            self._load_data(interval=interval, show_loading_screen=False)
            self._save_chart_state()

    @Slot(str)
    def on_load_error(self, error_message: str):
        logger.error(f"Chart loading error: {error_message}")
        self._update_ui_post_load(success=False)
        QMessageBox.warning(self, "Chart Error", error_message)
        self.stacked_widget.setCurrentWidget(self.chart_container)

    def _save_chart_state(self):
        if not self.current_symbol: return
        try:
            state = {'symbol': self.current_symbol, 'interval': self.current_interval}
            with open(self.chart_state_file, 'w') as f:
                json.dump(state, f)
            logger.info(f"Chart state saved: {state}")
        except Exception as e:
            logger.error(f"Failed to save chart state: {e}")

    def _load_saved_chart_state(self):
        try:
            if not os.path.exists(self.chart_state_file):
                self._load_default_symbol()
                return
            with open(self.chart_state_file, 'r') as f:
                state = json.load(f)

            saved_symbol = state.get('symbol')
            if saved_symbol and saved_symbol in self.instrument_map:
                logger.info(f"Loading saved state: {state}")
                self.current_interval = state.get('interval', 'day')
                self.timeframe_buttons[self.current_interval].setChecked(True)
                self.on_search(saved_symbol)
            else:
                self._load_default_symbol()
        except Exception as e:
            logger.error(f"Failed to load chart state: {e}")
            self._load_default_symbol()

    def _load_default_symbol(self):
        if self.instrument_map:
            default_symbol = next(iter(self.instrument_map.keys()))
            logger.info(f"Loading default symbol: {default_symbol}")
            self.on_search(default_symbol)
        else:
            self.symbol_label.setText("No instruments found")

    def _get_interval_display_name(self, interval: str) -> str:
        return {"day": "Daily", "60minute": "1H", "15minute": "15m", "5minute": "5m"}.get(interval, interval)

    def closeEvent(self, event):
        self._save_chart_state()
        if self.data_loader_thread and self.data_loader_thread.isRunning():
            self.data_loader_thread.stop()
            self.data_loader_thread.quit()
            self.data_loader_thread.wait()
        super().closeEvent(event)

    def _apply_styles(self):
        # Styles remain largely the same as they target standard QWidgets
        self.setStyleSheet("""
            QWidget { background-color: #1c1c2e; color: #e0e0e0; }
            #loadingLabel, #loadingIndicator {
                color: #4a9eff; font-size: 16px; font-weight: bold;
            }
            #symbolLabel {
                color: #ffffff; font-size: 13px; font-weight: bold;
            }
            #timeframeButton {
                background-color: #2d2d44; color: #8a8a9e; border: 1px solid #3d3d54;
                border-radius: 3px; font-size: 10px; font-weight: bold;
            }
            #timeframeButton:hover { background-color: #3d3d54; color: #ffffff; }
            #timeframeButton:checked {
                background-color: #4a9eff; color: #ffffff; border-color: #4a9eff;
            }
            #timeframeButton:disabled {
                background-color: #1a1a2a; color: #555566; border-color: #2a2a3a;
            }
        """)


if __name__ == '__main__':
    import sys
    from PySide6.QtWidgets import QApplication, QMainWindow

    app = QApplication(sys.argv)
    window = QMainWindow()
    # Pass None for KiteConnect client for mock data usage
    chart_widget = ChartWindow(kite_client=None)

    # Add a search bar for testing the on_search slot
    from PySide6.QtWidgets import QLineEdit

    central_widget = QWidget()
    layout = QVBoxLayout(central_widget)
    search_bar = QLineEdit()
    search_bar.setPlaceholderText("Enter symbol (e.g., RELIANCE, TCS) and press Enter")
    search_bar.returnPressed.connect(lambda: chart_widget.on_search(search_bar.text().upper()))

    layout.addWidget(search_bar)
    layout.addWidget(chart_widget)

    window.setCentralWidget(central_widget)
    window.resize(1200, 800)
    window.setWindowTitle("Advanced Native Candlestick Chart")
    window.show()
    sys.exit(app.exec())
