import logging
import json
import os
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Dict, Optional, Any

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from PySide6.QtCore import (Signal, Slot, QThread, Qt, QTimer, QMutex, QObject)
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                               QStackedWidget, QLabel, QPushButton, QProgressBar,
                               QFrame, QToolTip)
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel
from kiteconnect import KiteConnect
from cachetools import TTLCache
import threading

# Assuming DataFetcher is in a utils directory
# from utils.data_fetcher import DataFetcher

# Placeholder for DataFetcher if not available
class DataFetcher:
    def __init__(self, kite_client):
        self.kite_client = kite_client
    def fetch_historical_data(self, instrument_token, from_date, to_date, interval):
        return self.kite_client.historical_data(instrument_token, from_date, to_date, interval)


logger = logging.getLogger(__name__)


class ChartState(Enum):
    IDLE = "idle"
    LOADING = "loading"
    ERROR = "error"
    LOADED = "loaded"


class ChartConfig:
    """Chart configuration settings"""
    def __init__(self):
        self.symbol: str = ""
        self.interval: str = "day"
        self.show_volume: bool = True
        self.show_sma: bool = True
        self.sma_periods: List[int] = [10, 21, 51]
        self.theme: str = "dark"
        self.auto_refresh: bool = False
        self.refresh_interval: int = 30  # seconds


class DataCache:
    """Thread-safe data cache with TTL"""
    def __init__(self, maxsize: int = 100, ttl: int = 300):
        self._cache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[pd.DataFrame]:
        with self._lock:
            return self._cache.get(key)

    def set(self, key: str, value: pd.DataFrame) -> None:
        with self._lock:
            self._cache[key] = value.copy()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


class WebBridge(QObject):
    """Bridge for communication between Python and JavaScript in QWebEngineView."""
    layout_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.last_layout = {}

    @Slot(dict)
    def on_relayout(self, layout_data: dict):
        """Called by JavaScript when the user pans or zooms the chart."""
        self.last_layout['xaxis.range'] = layout_data.get('xaxis.range')
        self.last_layout['yaxis.range'] = layout_data.get('yaxis.range')
        self.layout_changed.emit(self.last_layout)


class ChartDataLoaderThread(QThread):
    """Enhanced data loader with better error handling and caching"""
    data_loaded = Signal(pd.DataFrame, str)
    load_error = Signal(str)
    load_progress = Signal(int)

    def __init__(self, data_fetcher: DataFetcher, instrument_token: int,
                 symbol: str, interval: str, cache: DataCache):
        super().__init__()
        self.data_fetcher = data_fetcher
        self.instrument_token = instrument_token
        self.symbol = symbol
        self.interval = interval
        self.cache = cache
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            cache_key = f"{self.symbol}_{self.interval}"
            cached_data = self.cache.get(cache_key)
            if cached_data is not None and not self._stop_requested:
                logger.info(f"Using cached data for {self.symbol}")
                self.load_progress.emit(100)
                self.data_loaded.emit(cached_data, cache_key)
                return

            if self._stop_requested: return
            self.load_progress.emit(10)
            to_date = datetime.now().date()
            date_ranges = {
                "day": 730, "60minute": 120, "30minute": 60, "15minute": 30,
                "10minute": 21, "5minute": 14, "3minute": 10, "minute": 5
            }
            days_back = date_ranges.get(self.interval, 365)
            from_date = to_date - timedelta(days=days_back)

            if self._stop_requested: return
            self.load_progress.emit(30)
            historical_data = self.data_fetcher.fetch_historical_data(
                instrument_token=self.instrument_token, from_date=from_date,
                to_date=to_date, interval=self.interval
            )

            if self._stop_requested: return
            self.load_progress.emit(60)
            if not historical_data:
                self.load_error.emit(f"No data available for {self.symbol}")
                return

            df = self._process_data(historical_data)
            if df.empty:
                self.load_error.emit(f"No valid data for {self.symbol}")
                return

            if self._stop_requested: return
            self.load_progress.emit(90)
            self.cache.set(cache_key, df)
            self.load_progress.emit(100)
            self.data_loaded.emit(df, cache_key)
        except Exception as e:
            if not self._stop_requested:
                logger.error(f"Data loading error for {self.symbol}: {e}", exc_info=True)
                self.load_error.emit(f"Failed to load data: {str(e)}")

    def _process_data(self, raw_data: List[Dict]) -> pd.DataFrame:
        try:
            df = pd.DataFrame(raw_data)
            if df.empty: return df

            required_columns = ['date', 'open', 'high', 'low', 'close', 'volume']
            if any(col not in df.columns for col in required_columns):
                raise ValueError("Missing required columns")

            df['date'] = pd.to_datetime(df['date'])
            numeric_cols = ['open', 'high', 'low', 'close', 'volume']
            for col in numeric_cols:
                df[col] = pd.to_numeric(df[col], errors='coerce')

            df = df.dropna(subset=numeric_cols)
            invalid_ohlc = (df['high'] < df['low']) | (df['high'] < df['open']) | \
                           (df['high'] < df['close']) | (df['low'] > df['open']) | \
                           (df['low'] > df['close'])
            if invalid_ohlc.any():
                logger.warning(f"Removing {invalid_ohlc.sum()} invalid OHLC rows")
                df = df[~invalid_ohlc]

            df = df.drop_duplicates(subset='date').sort_values('date')
            df = df.rename(columns={'date': 'time'})
            df['symbol'] = self.symbol
            df['volume'] = df['volume'].astype('int32')
            for col in ['open', 'high', 'low', 'close']:
                df[col] = df[col].astype('float32')
            return df
        except Exception as e:
            logger.error(f"Data processing error: {e}")
            raise


class TechnicalIndicators:
    @staticmethod
    def sma(data: pd.Series, period: int) -> pd.Series:
        return data.rolling(window=period, min_periods=1).mean()


class ChartWindow(QWidget):
    """Production-level candlestick chart widget using Plotly and PySide6"""

    def __init__(self, kite_client: KiteConnect, parent=None):
        super().__init__(parent)
        self.data_fetcher = DataFetcher(kite_client)
        self.data_cache = DataCache(maxsize=50, ttl=300)
        self.config = ChartConfig()
        self.instrument_map: Dict[str, Dict[str, Any]] = {}
        self.current_state = ChartState.IDLE
        self.data_loader_thread: Optional[ChartDataLoaderThread] = None
        self.last_df: Optional[pd.DataFrame] = None
        self.chart_initialized = False
        self.bridge = WebBridge(self)
        self.channel = QWebChannel(self)
        self.channel.registerObject("bridge", self.bridge)
        self._setup_plotly_template()
        self.timeframe_buttons: Dict[str, QPushButton] = {}
        self.auto_refresh_timer = QTimer()
        self.auto_refresh_timer.timeout.connect(self._auto_refresh)
        self.chart_state_file = os.path.expanduser("~/.swing_trader/plotly_chart_state.json")
        os.makedirs(os.path.dirname(self.chart_state_file), exist_ok=True)
        self._setup_ui()
        self._apply_enhanced_styles()
        self._setup_keyboard_shortcuts()
        QTimer.singleShot(500, self._initialize_chart)

    def _setup_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        chart_widget = QWidget()
        chart_layout = QVBoxLayout(chart_widget)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_layout.setSpacing(0)
        self.toolbar = self._create_enhanced_toolbar()
        chart_layout.addWidget(self.toolbar)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumHeight(3)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        chart_layout.addWidget(self.progress_bar)
        self.stacked_widget = QStackedWidget()
        chart_layout.addWidget(self.stacked_widget)
        self.loading_widget = self._create_loading_widget()
        self.stacked_widget.addWidget(self.loading_widget)
        self.error_widget = self._create_error_widget()
        self.stacked_widget.addWidget(self.error_widget)
        self.chart_container = QWidget()
        self.chart_layout = QVBoxLayout(self.chart_container)
        self.chart_layout.setContentsMargins(0, 0, 0, 0)
        self.chart_view = QWebEngineView()
        self.chart_view.page().setWebChannel(self.channel)
        self.chart_layout.addWidget(self.chart_view)
        self.stacked_widget.addWidget(self.chart_container)
        main_layout.addWidget(chart_widget)
        self._set_state(ChartState.IDLE)

    def _setup_plotly_template(self):
        pio.templates["custom_dark"] = go.layout.Template(
            layout=go.Layout(
                font={"color": "#e6edf3"}, paper_bgcolor="#0d1117", plot_bgcolor="#161b22",
                xaxis={"gridcolor": "#30363d", "linecolor": "#30363d", "zerolinecolor": "#30363d"},
                yaxis={"gridcolor": "#30363d", "linecolor": "#30363d", "zerolinecolor": "#30363d"},
                legend={"bgcolor": "rgba(0,0,0,0)", "x": 0.01, "y": 0.99},
                margin={"t": 30, "b": 30, "l": 30, "r": 30},
            )
        )
        pio.templates.default = "custom_dark"

    def _create_enhanced_toolbar(self) -> QWidget:
        toolbar = QFrame()
        toolbar.setFrameStyle(QFrame.Shape.Box)
        toolbar.setFixedHeight(45)
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(8)
        self.symbol_info_label = QLabel("No Symbol Selected")
        self.symbol_info_label.setObjectName("symbolInfoLabel")
        font = QFont()
        font.setBold(True)
        self.symbol_info_label.setFont(font)
        layout.addWidget(self.symbol_info_label)
        self.status_indicator = QLabel("●")
        self.status_indicator.setObjectName("statusIndicator")
        layout.addWidget(self.status_indicator)
        layout.addStretch()
        self.refresh_button = QPushButton("⟳")
        self.refresh_button.setObjectName("refreshButton")
        self.refresh_button.setFixedSize(30, 30)
        self.refresh_button.setToolTip("Refresh Data")
        self.refresh_button.clicked.connect(self._force_refresh)
        layout.addWidget(self.refresh_button)
        timeframes = [("1D", "day", "Daily"), ("1H", "60minute", "1 Hour"),
                      ("15m", "15minute", "15 Minutes"), ("5m", "5minute", "5 Minutes")]
        for display, interval, tooltip in timeframes:
            btn = QPushButton(display)
            btn.setObjectName("timeframeButton")
            btn.setCheckable(True)
            btn.setFixedSize(40, 30)
            btn.setToolTip(tooltip)
            btn.clicked.connect(lambda checked, i=interval: self._change_timeframe(i))
            self.timeframe_buttons[interval] = btn
            layout.addWidget(btn)
        self.timeframe_buttons["day"].setChecked(True)
        return toolbar

    def _create_loading_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label = QLabel("Loading chart data...")
        self.loading_label.setObjectName("loadingLabel")
        layout.addWidget(self.loading_label)
        return widget

    def _create_error_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_label = QLabel("Failed to load chart data")
        self.error_label.setObjectName("errorLabel")
        self.retry_button = QPushButton("Retry")
        self.retry_button.setObjectName("retryButton")
        self.retry_button.clicked.connect(self._retry_load)
        layout.addWidget(self.error_label)
        layout.addWidget(self.retry_button, 0, Qt.AlignmentFlag.AlignCenter)
        return widget

    def _setup_keyboard_shortcuts(self):
        refresh_shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Refresh), self)
        refresh_shortcut.activated.connect(self._force_refresh)
        f5_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F5), self)
        f5_shortcut.activated.connect(self._force_refresh)

    def _initialize_chart(self):
        self._load_saved_state()

    def _set_state(self, state: ChartState):
        self.current_state = state
        state_configs = {
            ChartState.IDLE: {'color': '#888888', 'text': 'Ready', 'widget': 2, 'enabled': True},
            ChartState.LOADING: {'color': '#4a9eff', 'text': 'Loading', 'widget': 0, 'enabled': False},
            ChartState.ERROR: {'color': '#ff4a4a', 'text': 'Error', 'widget': 1, 'enabled': True},
            ChartState.LOADED: {'color': '#4aff4a', 'text': 'Live', 'widget': 2, 'enabled': True}
        }
        config = state_configs.get(state, state_configs[ChartState.IDLE])
        self.status_indicator.setStyleSheet(f"color: {config['color']};")
        self.status_indicator.setToolTip(config['text'])
        if self.stacked_widget.currentIndex() != config['widget']:
            self.stacked_widget.setCurrentIndex(config['widget'])
        for btn in self.timeframe_buttons.values():
            btn.setEnabled(config['enabled'])
        self.refresh_button.setEnabled(config['enabled'])

    def set_instrument_list(self, instruments: List[Dict[str, Any]]):
        try:
            self.instrument_map = {inst['tradingsymbol']: inst for inst in instruments
                                   if all(key in inst for key in ['tradingsymbol', 'instrument_token'])}
            logger.info(f"Loaded {len(self.instrument_map)} instruments")
        except Exception as e:
            logger.error(f"Error setting instrument list: {e}")

    @Slot(str)
    def on_search(self, symbol: Optional[str] = None):
        if not symbol or symbol not in self.instrument_map:
            if symbol: self._show_error(f"Symbol '{symbol}' not found")
            return
        self._stop_current_operations()
        self.chart_initialized = False
        self.config.symbol = symbol
        self._save_state()
        self._load_chart_data()

    def _load_chart_data(self, force_refresh: bool = False):
        if not self.config.symbol or self.config.symbol not in self.instrument_map: return
        if force_refresh: self.data_cache.clear()
        self._stop_current_operations()
        self._set_state(ChartState.LOADING)
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        instrument = self.instrument_map[self.config.symbol]
        self.data_loader_thread = ChartDataLoaderThread(
            self.data_fetcher, instrument['instrument_token'], self.config.symbol,
            self.config.interval, self.data_cache
        )
        self.data_loader_thread.data_loaded.connect(self._on_data_loaded)
        self.data_loader_thread.load_error.connect(self._on_load_error)
        self.data_loader_thread.load_progress.connect(self._on_load_progress)
        self.data_loader_thread.finished.connect(self._on_thread_finished)
        self.data_loader_thread.start()

    @Slot(pd.DataFrame, str)
    def _on_data_loaded(self, df: pd.DataFrame, cache_key: str):
        try:
            if df.empty:
                self._show_error("No data available")
                return
            self.last_df = df.copy()
            self._render_chart_plotly(df)
            self._update_symbol_info(df)
            self._set_state(ChartState.LOADED)
            self._setup_auto_refresh()
        except Exception as e:
            logger.error(f"Error processing loaded data: {e}", exc_info=True)
            self._show_error(f"Failed to render chart: {str(e)}")

    @Slot(str)
    def _on_load_error(self, error_message: str):
        logger.error(f"Data loading failed: {error_message}")
        self._show_error(error_message)

    @Slot(int)
    def _on_load_progress(self, progress: int):
        self.progress_bar.setValue(progress)

    def _on_thread_finished(self):
        self.progress_bar.hide()
        if self.data_loader_thread:
            self.data_loader_thread.deleteLater()
            self.data_loader_thread = None

    def _render_chart_plotly(self, df: pd.DataFrame):
        try:
            chart_df = df.copy()
            self._add_technical_indicators(chart_df)
            traces = []
            traces.append(go.Candlestick(x=chart_df['time'], open=chart_df['open'], high=chart_df['high'],
                                     low=chart_df['low'], close=chart_df['close'], name='Candles',
                                     increasing=dict(line=dict(color='#238636')),
                                     decreasing=dict(line=dict(color='#da3633'))))
            if self.config.show_volume:
                bar_colors = ['#238636' if row['close'] >= row['open'] else '#da3633'
                              for _, row in chart_df.iterrows()]
                traces.append(go.Bar(x=chart_df['time'], y=chart_df['volume'], name='Volume',
                                     marker=dict(color=bar_colors), yaxis='y2'))
            if self.config.show_sma:
                colors = ['#FF6B6B', '#4ECDC4', '#45B7D1']
                for i, period in enumerate(self.config.sma_periods):
                    sma_col = f'sma{period}'
                    if sma_col in chart_df.columns:
                        traces.append(go.Scatter(x=chart_df['time'], y=chart_df[sma_col], mode='lines',
                                                 name=f'SMA {period}', line={'color': colors[i % len(colors)], 'width': 1}))
            layout = go.Layout(
                yaxis={'domain': [0.3, 1.0], 'fixedrange': False},
                yaxis2={'domain': [0, 0.25], 'showticklabels': False},
                xaxis={'fixedrange': False, 'rangeslider': {'visible': False},
                       'rangebreaks': [dict(bounds=["sat", "sun"], pattern="day of week")]},
                showlegend=True, dragmode='pan')
            fig = go.Figure(data=traces, layout=layout)
            fig.update_layout(template="custom_dark")

            if not self.chart_initialized:
                self._initialize_new_chart(fig)
                self.chart_initialized = True
            else:
                if self.bridge.last_layout.get('xaxis.range'):
                    fig.layout.xaxis.range = self.bridge.last_layout['xaxis.range']
                if self.bridge.last_layout.get('yaxis.range'):
                    fig.layout.yaxis.range = self.bridge.last_layout['yaxis.range']
                self._update_chart_data(fig)
            logger.info("Plotly chart rendered/updated successfully.")
        except Exception as e:
            logger.error(f"Plotly chart rendering error: {e}", exc_info=True)
            self._show_error("Could not render chart.")

    def _initialize_new_chart(self, fig: go.Figure):
        # --- FIX: Correctly index the x-axis data for initial range ---
        x_data = fig.data[0].x
        if len(x_data) > 80:
            fig.layout.xaxis.range = [x_data[-80], x_data[-1]]

        raw_html = f"""
        <!DOCTYPE html><html><head><meta charset="utf-8" />
        <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
        <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
        <style>body {{ margin: 0; background-color: #0d1117; }}</style></head>
        <body><div id="chartdiv" style="width:100vw; height:100vh;"></div>
        <script>
            document.addEventListener("DOMContentLoaded", function(event) {{
                var chartDiv = document.getElementById('chartdiv');
                var fig = {pio.to_json(fig)};
                Plotly.newPlot('chartdiv', fig.data, fig.layout, {{
                    scrollZoom: true, displaylogo: false, responsive: true
                }});
                new QWebChannel(qt.webChannelTransport, function (channel) {{
                    window.bridge = channel.objects.bridge;
                    chartDiv.on('plotly_relayout', function(eventdata) {{
                        bridge.on_relayout(chartDiv.layout);
                    }});
                }});
            }});
        </script></body></html>"""
        self.chart_view.setHtml(raw_html)

    def _update_chart_data(self, fig: go.Figure):
        fig_json = pio.to_json(fig)
        js_code = f"Plotly.react('chartdiv', {fig_json}.data, {fig_json}.layout);"
        self.chart_view.page().runJavaScript(js_code)

    def _add_technical_indicators(self, df: pd.DataFrame):
        try:
            if self.config.show_sma:
                for period in self.config.sma_periods:
                    if len(df) >= period:
                        df[f'sma{period}'] = TechnicalIndicators.sma(df['close'], period)
        except Exception as e:
            logger.error(f"Error adding technical indicators: {e}")

    def _update_symbol_info(self, df: pd.DataFrame):
        try:
            if df.empty: return
            latest = df.iloc[-1]
            symbol = self.config.symbol
            interval_name = self._get_interval_display_name(self.config.interval)
            current_price = latest['close']
            change_str = "N/A"
            if len(df) > 1:
                prev_price = df.iloc[-2]['close']
                change = current_price - prev_price
                change_pct = (change / prev_price) * 100
                change_str = f"{change:+.2f} ({change_pct:+.2f}%)"
            info_text = f"{symbol} • {interval_name} • ₹{current_price:.2f}"
            self.symbol_info_label.setText(info_text)
            self.symbol_info_label.setToolTip(f"Change: {change_str}")
        except Exception as e:
            logger.error(f"Error updating symbol info: {e}")

    def _get_interval_display_name(self, interval: str) -> str:
        return {"day": "1D", "60minute": "1H", "15minute": "15m", "5minute": "5m"}.get(interval, interval.upper())

    def _change_timeframe(self, interval: str):
        if self.config.interval == interval or not self.config.symbol: return
        for btn_interval, btn in self.timeframe_buttons.items():
            btn.setChecked(btn_interval == interval)
        self.config.interval = interval
        self._save_state()
        self._load_chart_data()

    def _force_refresh(self):
        if self.config.symbol: self._load_chart_data(force_refresh=True)

    def _retry_load(self):
        if self.config.symbol: self._load_chart_data()

    def _auto_refresh(self):
        if self.config.auto_refresh and self.config.symbol: self._load_chart_data(force_refresh=True)

    def _setup_auto_refresh(self):
        self.auto_refresh_timer.stop()
        if self.config.auto_refresh:
            self.auto_refresh_timer.start(self.config.refresh_interval * 1000)

    def _stop_current_operations(self):
        if self.data_loader_thread and self.data_loader_thread.isRunning():
            self.data_loader_thread.stop()
            self.data_loader_thread.wait(3000)
        self.auto_refresh_timer.stop()

    def _show_error(self, message: str):
        self.error_label.setText(f"Error: {message}")
        self._set_state(ChartState.ERROR)
        QToolTip.showText(self.mapToGlobal(self.rect().center()), message)

    def _save_state(self):
        try:
            state = {k: getattr(self.config, k) for k in self.config.__dict__}
            state['timestamp'] = datetime.now().isoformat()
            with open(self.chart_state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def _load_saved_state(self):
        try:
            if not os.path.exists(self.chart_state_file):
                self._load_default_symbol()
                return
            with open(self.chart_state_file, 'r') as f:
                state = json.load(f)
            for key, value in state.items():
                if hasattr(self.config, key): setattr(self.config, key, value)
            self._update_controls_from_config()
            if self.config.symbol and self.config.symbol in self.instrument_map:
                self._load_chart_data()
            else:
                self._load_default_symbol()
        except Exception as e:
            logger.error(f"Failed to load saved state: {e}", exc_info=True)
            self._load_default_symbol()

    def _update_controls_from_config(self):
        try:
            for interval, btn in self.timeframe_buttons.items():
                btn.setChecked(interval == self.config.interval)
        except Exception as e:
            logger.error(f"Error updating controls: {e}")

    def _load_default_symbol(self):
        if not self.instrument_map:
            self.symbol_info_label.setText("No instruments available")
            self._set_state(ChartState.ERROR)
            return
        popular_symbols = ['NIFTY 50', 'BANKNIFTY', 'RELIANCE', 'TCS', 'INFY']
        for symbol in popular_symbols:
            if symbol in self.instrument_map:
                self.config.symbol = symbol
                self._load_chart_data()
                return
        if self.instrument_map:
            self.config.symbol = next(iter(self.instrument_map.keys()))
            self._load_chart_data()

    def _apply_enhanced_styles(self):
        self.setStyleSheet("""
            ChartWindow { background-color: #0d1117; color: #e6edf3; }
            QFrame { background-color: #161b22; border: 1px solid #30363d; border-radius: 6px; }
            #symbolInfoLabel { color: #f0f6fc; font-size: 14px; font-weight: bold; font-family: "Segoe UI", system-ui; }
            #statusIndicator { font-size: 16px; font-weight: bold; }
            #timeframeButton { background-color: #21262d; color: #7d8590; border: 1px solid #30363d; border-radius: 6px; font-weight: 600; }
            #timeframeButton:hover { background-color: #30363d; color: #f0f6fc; border-color: #58a6ff; }
            #timeframeButton:checked { background-color: #0969da; color: #ffffff; border-color: #0969da; }
            #timeframeButton:disabled { background-color: #161b22; color: #484f58; border-color: #21262d; }
            #refreshButton { background-color: #238636; color: #ffffff; border: 1px solid #238636; border-radius: 6px; font-weight: bold; }
            #refreshButton:hover { background-color: #2ea043; }
            #retryButton { background-color: #da3633; color: #ffffff; border: 1px solid #da3633; border-radius: 6px; padding: 8px 16px; }
            #retryButton:hover { background-color: #f85149; }
            #loadingLabel, #errorLabel { font-size: 16px; font-weight: bold; font-family: "Segoe UI", system-ui; }
            #loadingLabel { color: #58a6ff; }
            #errorLabel { color: #f85149; }
            QProgressBar { background-color: #21262d; border: none; }
            QProgressBar::chunk { background-color: #58a6ff; }
            QStackedWidget > QWidget { background-color: #0d1117; }
        """)

    def closeEvent(self, event):
        self._stop_current_operations()
        self._save_state()
        self.data_cache.clear()
        logger.info("Plotly chart widget closed successfully")
        super().closeEvent(event)