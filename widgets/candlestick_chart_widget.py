import logging
import json
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Tuple
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum

import pandas as pd
import numpy as np
from lightweight_charts.widgets import QtChart
from PySide6.QtCore import (Signal, Slot, QThread, Qt, QTimer, QMutex,
                            QPropertyAnimation, QEasingCurve, QParallelAnimationGroup)
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QMessageBox,
                               QStackedWidget, QLabel, QPushButton, QProgressBar,
                               QFrame, QToolTip, QSizePolicy, QComboBox, QCheckBox,
                               QSpinBox, QGroupBox, QSplitter)
from PySide6.QtGui import QPalette, QFont, QPixmap, QPainter, QColor
from kiteconnect import KiteConnect
from cachetools import TTLCache
import threading

from utils.data_fetcher import DataFetcher

logger = logging.getLogger(__name__)


class ChartState(Enum):
    IDLE = "idle"
    LOADING = "loading"
    ERROR = "error"
    LOADED = "loaded"


@dataclass
class ChartConfig:
    """Chart configuration settings"""
    symbol: str = ""
    interval: str = "day"
    show_volume: bool = True
    show_sma: bool = True
    sma_periods: List[int] = None
    theme: str = "dark"
    auto_refresh: bool = False
    refresh_interval: int = 30  # seconds

    def __post_init__(self):
        if self.sma_periods is None:
            self.sma_periods = [10, 21, 51]


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


class ChartDataLoaderThread(QThread):
    """Enhanced data loader with better error handling and caching"""
    data_loaded = Signal(pd.DataFrame, str)  # DataFrame, cache_key
    load_error = Signal(str)
    load_progress = Signal(int)  # Progress percentage

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

            # Check cache first
            cached_data = self.cache.get(cache_key)
            if cached_data is not None and not self._stop_requested:
                logger.info(f"Using cached data for {self.symbol}")
                self.load_progress.emit(100)
                self.data_loaded.emit(cached_data, cache_key)
                return

            if self._stop_requested:
                return

            self.load_progress.emit(10)

            to_date = datetime.now().date()

            # Optimized date ranges
            date_ranges = {
                "day": 730,  # 2 years
                "60minute": 120,  # 4 months
                "30minute": 60,  # 2 months
                "15minute": 30,  # 1 month
                "10minute": 21,  # 3 weeks
                "5minute": 14,  # 2 weeks
                "3minute": 10,  # 10 days
                "minute": 5  # 5 days
            }

            days_back = date_ranges.get(self.interval, 365)
            from_date = to_date - timedelta(days=days_back)

            if self._stop_requested:
                return

            self.load_progress.emit(30)

            logger.info(f"Fetching data: {self.symbol} @ {self.interval} ({from_date} to {to_date})")

            historical_data = self.data_fetcher.fetch_historical_data(
                instrument_token=self.instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval=self.interval
            )

            if self._stop_requested:
                return

            self.load_progress.emit(60)

            if not historical_data:
                self.load_error.emit(f"No data available for {self.symbol}")
                return

            df = self._process_data(historical_data)

            if df.empty:
                self.load_error.emit(f"No valid data for {self.symbol}")
                return

            if self._stop_requested:
                return

            self.load_progress.emit(90)

            # Cache the processed data
            self.cache.set(cache_key, df)

            self.load_progress.emit(100)
            self.data_loaded.emit(df, cache_key)

        except Exception as e:
            if not self._stop_requested:
                logger.error(f"Data loading error for {self.symbol}: {e}", exc_info=True)
                self.load_error.emit(f"Failed to load data: {str(e)}")

    def _process_data(self, raw_data: List[Dict]) -> pd.DataFrame:
        """Process raw data with validation and optimization"""
        try:
            df = pd.DataFrame(raw_data)

            if df.empty:
                return df

            # Data validation and cleaning
            required_columns = ['date', 'open', 'high', 'low', 'close', 'volume']
            missing_cols = [col for col in required_columns if col not in df.columns]
            if missing_cols:
                raise ValueError(f"Missing required columns: {missing_cols}")

            # Convert and validate data types
            df['date'] = pd.to_datetime(df['date'])
            numeric_cols = ['open', 'high', 'low', 'close', 'volume']

            for col in numeric_cols:
                df[col] = pd.to_numeric(df[col], errors='coerce')

            # Remove invalid rows
            df = df.dropna(subset=numeric_cols)

            # Validate OHLC data integrity
            invalid_ohlc = (
                    (df['high'] < df['low']) |
                    (df['high'] < df['open']) |
                    (df['high'] < df['close']) |
                    (df['low'] > df['open']) |
                    (df['low'] > df['close'])
            )

            if invalid_ohlc.any():
                logger.warning(f"Removing {invalid_ohlc.sum()} invalid OHLC rows")
                df = df[~invalid_ohlc]

            # Remove duplicates and sort
            df = df.drop_duplicates(subset='date').sort_values('date')

            # Rename for chart compatibility
            df = df.rename(columns={'date': 'time'})
            df['symbol'] = self.symbol

            # Optimize data types for memory efficiency
            df['volume'] = df['volume'].astype('int32')
            for col in ['open', 'high', 'low', 'close']:
                df[col] = df[col].astype('float32')

            return df

        except Exception as e:
            logger.error(f"Data processing error: {e}")
            raise


class TechnicalIndicators:
    """Technical analysis indicators"""

    @staticmethod
    def sma(data: pd.Series, period: int) -> pd.Series:
        """Simple Moving Average"""
        return data.rolling(window=period, min_periods=1).mean()

    @staticmethod
    def ema(data: pd.Series, period: int) -> pd.Series:
        """Exponential Moving Average"""
        return data.ewm(span=period, adjust=False).mean()

    @staticmethod
    def bollinger_bands(data: pd.Series, period: int = 20, std_dev: float = 2) -> Tuple[
        pd.Series, pd.Series, pd.Series]:
        """Bollinger Bands"""
        sma = TechnicalIndicators.sma(data, period)
        std = data.rolling(window=period).std()
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        return upper, sma, lower

    @staticmethod
    def rsi(data: pd.Series, period: int = 14) -> pd.Series:
        """Relative Strength Index"""
        delta = data.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))


class ChartControlPanel(QWidget):
    """Control panel for chart settings"""
    settings_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = ChartConfig()
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(5)
        layout.setContentsMargins(5, 5, 5, 5)

        # Indicators Group
        indicators_group = QGroupBox("Technical Indicators")
        indicators_layout = QVBoxLayout(indicators_group)

        self.show_sma_checkbox = QCheckBox("Show SMA Lines")
        self.show_sma_checkbox.setChecked(True)
        indicators_layout.addWidget(self.show_sma_checkbox)

        # SMA Periods
        sma_layout = QHBoxLayout()
        sma_layout.addWidget(QLabel("SMA Periods:"))

        self.sma_period_inputs = []
        for period in [10, 21, 51]:
            spinbox = QSpinBox()
            spinbox.setRange(1, 200)
            spinbox.setValue(period)
            spinbox.setMaximumWidth(60)
            self.sma_period_inputs.append(spinbox)
            sma_layout.addWidget(spinbox)

        indicators_layout.addLayout(sma_layout)
        layout.addWidget(indicators_group)

        # Display Group
        display_group = QGroupBox("Display Options")
        display_layout = QVBoxLayout(display_group)

        self.show_volume_checkbox = QCheckBox("Show Volume")
        self.show_volume_checkbox.setChecked(True)
        display_layout.addWidget(self.show_volume_checkbox)

        self.auto_refresh_checkbox = QCheckBox("Auto Refresh")
        display_layout.addWidget(self.auto_refresh_checkbox)

        refresh_layout = QHBoxLayout()
        refresh_layout.addWidget(QLabel("Interval (s):"))
        self.refresh_interval_spinbox = QSpinBox()
        self.refresh_interval_spinbox.setRange(5, 300)
        self.refresh_interval_spinbox.setValue(30)
        self.refresh_interval_spinbox.setMaximumWidth(80)
        refresh_layout.addWidget(self.refresh_interval_spinbox)
        display_layout.addLayout(refresh_layout)

        layout.addWidget(display_group)
        layout.addStretch()

    def _connect_signals(self):
        self.show_sma_checkbox.toggled.connect(self._emit_settings_changed)
        self.show_volume_checkbox.toggled.connect(self._emit_settings_changed)
        self.auto_refresh_checkbox.toggled.connect(self._emit_settings_changed)
        self.refresh_interval_spinbox.valueChanged.connect(self._emit_settings_changed)

        for spinbox in self.sma_period_inputs:
            spinbox.valueChanged.connect(self._emit_settings_changed)

    def _emit_settings_changed(self):
        settings = {
            'show_sma': self.show_sma_checkbox.isChecked(),
            'show_volume': self.show_volume_checkbox.isChecked(),
            'auto_refresh': self.auto_refresh_checkbox.isChecked(),
            'refresh_interval': self.refresh_interval_spinbox.value(),
            'sma_periods': [spinbox.value() for spinbox in self.sma_period_inputs]
        }
        self.settings_changed.emit(settings)


class EnhancedChartWindow(QWidget):
    """Production-level candlestick chart widget"""

    def __init__(self, kite_client: KiteConnect, parent=None):
        super().__init__(parent)

        # Core components
        self.data_fetcher = DataFetcher(kite_client)
        self.data_cache = DataCache(maxsize=50, ttl=300)  # 5-minute cache
        self.config = ChartConfig()

        # State management
        self.instrument_map: Dict[str, Dict[str, Any]] = {}
        self.current_state = ChartState.IDLE
        self.data_loader_thread: Optional[ChartDataLoaderThread] = None
        self.last_df: Optional[pd.DataFrame] = None

        # UI components
        self.chart: Optional[QtChart] = None
        self.sma_lines = []
        self.timeframe_buttons: Dict[str, QPushButton] = {}

        # Timers and animations
        self.auto_refresh_timer = QTimer()
        self.auto_refresh_timer.timeout.connect(self._auto_refresh)

        # Thread safety
        self.thread_mutex = QMutex()

        # Configuration
        self.chart_state_file = os.path.expanduser("~/.swing_trader/enhanced_chart_state.json")
        os.makedirs(os.path.dirname(self.chart_state_file), exist_ok=True)

        self._setup_ui()
        self._apply_enhanced_styles()
        self._setup_keyboard_shortcuts()

        # Delayed initialization
        QTimer.singleShot(500, self._initialize_chart)

    def _setup_ui(self):
        """Setup the enhanced UI layout"""
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Create splitter for resizable panels
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # Chart area
        chart_widget = QWidget()
        chart_layout = QVBoxLayout(chart_widget)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_layout.setSpacing(0)

        # Enhanced toolbar
        self.toolbar = self._create_enhanced_toolbar()
        chart_layout.addWidget(self.toolbar)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumHeight(3)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        chart_layout.addWidget(self.progress_bar)

        # Chart container with stacked widget
        self.stacked_widget = QStackedWidget()
        chart_layout.addWidget(self.stacked_widget)

        # Loading screen
        self.loading_widget = self._create_loading_widget()
        self.stacked_widget.addWidget(self.loading_widget)

        # Error screen
        self.error_widget = self._create_error_widget()
        self.stacked_widget.addWidget(self.error_widget)

        # Chart container
        self.chart_container = QWidget()
        self.chart_layout = QVBoxLayout(self.chart_container)
        self.chart_layout.setContentsMargins(0, 0, 0, 0)
        self.stacked_widget.addWidget(self.chart_container)

        # Control panel
        self.control_panel = ChartControlPanel()
        self.control_panel.setMaximumWidth(250)
        self.control_panel.settings_changed.connect(self._on_settings_changed)

        # Add to splitter
        splitter.addWidget(chart_widget)
        splitter.addWidget(self.control_panel)
        splitter.setStretchFactor(0, 1)  # Chart takes most space
        splitter.setStretchFactor(1, 0)  # Control panel fixed

        # Set initial state
        self._set_state(ChartState.IDLE)

    def _create_enhanced_toolbar(self) -> QWidget:
        """Create enhanced toolbar with more controls"""
        toolbar = QFrame()
        toolbar.setFrameStyle(QFrame.Shape.Box)
        toolbar.setFixedHeight(45)

        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(8)

        # Symbol info
        self.symbol_info_label = QLabel("No Symbol Selected")
        self.symbol_info_label.setObjectName("symbolInfoLabel")
        font = QFont()
        font.setBold(True)
        self.symbol_info_label.setFont(font)
        layout.addWidget(self.symbol_info_label)

        # Status indicator
        self.status_indicator = QLabel("●")
        self.status_indicator.setObjectName("statusIndicator")
        layout.addWidget(self.status_indicator)

        layout.addStretch()

        # Refresh button
        self.refresh_button = QPushButton("⟳")
        self.refresh_button.setObjectName("refreshButton")
        self.refresh_button.setFixedSize(30, 30)
        self.refresh_button.setToolTip("Refresh Data")
        self.refresh_button.clicked.connect(self._force_refresh)
        layout.addWidget(self.refresh_button)

        # Timeframe buttons
        timeframes = [
            ("1D", "day", "Daily"),
            ("4H", "240minute", "4 Hours"),
            ("1H", "60minute", "1 Hour"),
            ("15m", "15minute", "15 Minutes"),
            ("5m", "5minute", "5 Minutes")
        ]

        for display, interval, tooltip in timeframes:
            btn = QPushButton(display)
            btn.setObjectName("timeframeButton")
            btn.setCheckable(True)
            btn.setFixedSize(40, 30)
            btn.setToolTip(tooltip)
            btn.clicked.connect(lambda checked, i=interval: self._change_timeframe(i))
            self.timeframe_buttons[interval] = btn
            layout.addWidget(btn)

        # Set default
        self.timeframe_buttons["day"].setChecked(True)

        return toolbar

    def _create_loading_widget(self) -> QWidget:
        """Create animated loading widget"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.loading_label = QLabel("Loading chart data...")
        self.loading_label.setObjectName("loadingLabel")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self.loading_label)
        return widget

    def _create_error_widget(self) -> QWidget:
        """Create error display widget"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.error_label = QLabel("Failed to load chart data")
        self.error_label.setObjectName("errorLabel")
        self.error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.retry_button = QPushButton("Retry")
        self.retry_button.setObjectName("retryButton")
        self.retry_button.clicked.connect(self._retry_load)
        self.retry_button.setFixedWidth(100)

        layout.addWidget(self.error_label)
        layout.addWidget(self.retry_button, 0, Qt.AlignmentFlag.AlignCenter)

        return widget

    def _setup_keyboard_shortcuts(self):
        """Setup keyboard shortcuts for better UX"""
        from PySide6.QtGui import QShortcut, QKeySequence

        # Refresh: Ctrl+R or F5
        refresh_shortcut = QShortcut(QKeySequence.StandardKey.Refresh, self)
        refresh_shortcut.activated.connect(self._force_refresh)

        f5_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F5), self)
        f5_shortcut.activated.connect(self._force_refresh)

    def _initialize_chart(self):
        """Initialize chart after UI is ready"""
        if not self.chart:
            self._create_new_chart()
        self._load_saved_state()

    def _create_new_chart(self):
        """Create a new chart instance with optimized settings"""
        try:
            # Clear existing chart
            if self.chart and self.chart.get_webview():
                old_webview = self.chart.get_webview()
                self.chart_layout.removeWidget(old_webview)
                old_webview.deleteLater()

            # Create new chart with performance optimizations
            self.chart = QtChart(
                toolbox=False,
            )

            # Configure chart appearance
            self.chart.legend(visible=True, font_size=11)
            self.chart.grid(vert_enabled=True, horz_enabled=True,
                            color='rgba(64, 64, 80, 0.3)')

            # ======================= FIX START =========================
            # Enhanced crosshair - remove style parameters to use default solid line
            self.chart.crosshair(
                vert_color='rgba(200, 200, 220, 0.6)',
                horz_color='rgba(200, 200, 220, 0.6)'
            )
            # ======================= FIX END ===========================

            # Add to layout
            self.chart_layout.addWidget(self.chart.get_webview())
            self.sma_lines.clear()

        except Exception as e:
            logger.error(f"Failed to create chart: {e}")
            self._set_state(ChartState.ERROR)

    def _set_state(self, state: ChartState):
        """Update UI state with visual feedback"""
        self.current_state = state

        state_configs = {
            ChartState.IDLE: {
                'status_color': '#888888',
                'status_text': 'Ready',
                'widget_index': 2,  # Chart container
                'buttons_enabled': True
            },
            ChartState.LOADING: {
                'status_color': '#4a9eff',
                'status_text': 'Loading',
                'widget_index': 0,  # Loading widget
                'buttons_enabled': False
            },
            ChartState.ERROR: {
                'status_color': '#ff4a4a',
                'status_text': 'Error',
                'widget_index': 1,  # Error widget
                'buttons_enabled': True
            },
            ChartState.LOADED: {
                'status_color': '#4aff4a',
                'status_text': 'Live',
                'widget_index': 2,  # Chart container
                'buttons_enabled': True
            }
        }

        config = state_configs.get(state, state_configs[ChartState.IDLE])

        # Update status indicator
        self.status_indicator.setStyleSheet(f"color: {config['status_color']};")
        self.status_indicator.setToolTip(config['status_text'])

        # Update widget visibility
        self.stacked_widget.setCurrentIndex(config['widget_index'])

        # Update button states
        for btn in self.timeframe_buttons.values():
            btn.setEnabled(config['buttons_enabled'])
        self.refresh_button.setEnabled(config['buttons_enabled'])

    def set_instrument_list(self, instruments: List[Dict[str, Any]]):
        """Set available instruments with validation"""
        try:
            self.instrument_map = {}
            for inst in instruments:
                if all(key in inst for key in ['tradingsymbol', 'instrument_token']):
                    self.instrument_map[inst['tradingsymbol']] = inst

            logger.info(f"Loaded {len(self.instrument_map)} instruments")

        except Exception as e:
            logger.error(f"Error setting instrument list: {e}")

    @Slot(str)
    def on_search(self, symbol: Optional[str] = None):
        """Handle symbol search with enhanced validation"""
        if not symbol or symbol not in self.instrument_map:
            if symbol:
                self._show_error(f"Symbol '{symbol}' not found")
            return

        # Stop any running operations
        self._stop_current_operations()

        # Update configuration
        self.config.symbol = symbol
        self._save_state()

        # Load new data
        self._load_chart_data()

    def _load_chart_data(self, force_refresh: bool = False):
        """Load chart data with enhanced error handling"""
        if not self.config.symbol or self.config.symbol not in self.instrument_map:
            return

        # Clear cache if force refresh
        if force_refresh:
            cache_key = f"{self.config.symbol}_{self.config.interval}"
            self.data_cache._cache.pop(cache_key, None)

        # Stop existing thread
        self._stop_current_operations()

        # Update UI state
        self._set_state(ChartState.LOADING)
        self.progress_bar.show()
        self.progress_bar.setValue(0)

        # Get instrument details
        instrument = self.instrument_map[self.config.symbol]
        instrument_token = instrument['instrument_token']

        # Start loading thread
        self.data_loader_thread = ChartDataLoaderThread(
            self.data_fetcher, instrument_token,
            self.config.symbol, self.config.interval, self.data_cache
        )

        # Connect signals
        self.data_loader_thread.data_loaded.connect(self._on_data_loaded)
        self.data_loader_thread.load_error.connect(self._on_load_error)
        self.data_loader_thread.load_progress.connect(self._on_load_progress)
        self.data_loader_thread.finished.connect(self._on_thread_finished)

        self.data_loader_thread.start()

    @Slot(pd.DataFrame, str)
    def _on_data_loaded(self, df: pd.DataFrame, cache_key: str):
        """Handle successful data loading"""
        try:
            if df.empty:
                self._show_error("No data available")
                return

            self.last_df = df.copy()
            self._render_chart(df)

            # Update UI
            self._update_symbol_info(df)
            self._set_state(ChartState.LOADED)

            # Setup auto-refresh if enabled
            self._setup_auto_refresh()

            logger.info(f"Chart loaded: {self.config.symbol} ({len(df)} candles)")

        except Exception as e:
            logger.error(f"Error processing loaded data: {e}")
            self._show_error(f"Failed to render chart: {str(e)}")

    @Slot(str)
    def _on_load_error(self, error_message: str):
        """Handle data loading errors"""
        logger.error(f"Data loading failed: {error_message}")
        self._show_error(error_message)

    @Slot(int)
    def _on_load_progress(self, progress: int):
        """Update loading progress"""
        self.progress_bar.setValue(progress)

    def _on_thread_finished(self):
        """Clean up after thread completion"""
        self.progress_bar.hide()
        if self.data_loader_thread:
            self.data_loader_thread.deleteLater()
            self.data_loader_thread = None

    def _render_chart(self, df: pd.DataFrame):
        """Render chart with technical indicators"""
        try:
            # Create new chart if needed
            if not self.chart:
                self._create_new_chart()

            # Prepare data
            chart_df = df.copy()

            # Add technical indicators
            self._add_technical_indicators(chart_df)

            # Clear existing lines
            self.sma_lines.clear()

            # Recreate chart for consistency
            self._create_new_chart()

            # Add SMA lines first (behind candlesticks)
            if self.config.show_sma:
                self._add_sma_lines(chart_df)

            # Add main candlestick data
            self.chart.set(chart_df[['time', 'open', 'high', 'low', 'close', 'volume']])

            logger.info(f"Chart rendered successfully for {self.config.symbol}")

        except Exception as e:
            logger.error(f"Chart rendering error: {e}")
            raise

    def _add_technical_indicators(self, df: pd.DataFrame):
        """Add technical indicators to dataframe"""
        try:
            # Simple Moving Averages
            for period in self.config.sma_periods:
                if len(df) >= period:
                    df[f'sma{period}'] = TechnicalIndicators.sma(df['close'], period)

            # Additional indicators can be added here
            # df['rsi'] = TechnicalIndicators.rsi(df['close'])

        except Exception as e:
            logger.error(f"Error adding technical indicators: {e}")

    def _add_sma_lines(self, df: pd.DataFrame):
        """Add SMA lines to chart with different colors"""
        colors = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#FFB347', '#DDA0DD']

        for i, period in enumerate(self.config.sma_periods):
            sma_col = f'sma{period}'
            if sma_col in df.columns and df[sma_col].notna().sum() > 0:
                color = colors[i % len(colors)]
                try:
                    line = self.chart.create_line(
                        name=f'SMA {period}',
                        color=color,
                        width=1,
                        price_line=False,
                        price_label=False
                    )

                    # ======================= FIX START =========================
                    # Create a new DataFrame with the correct column names 'time' and 'value'
                    sma_data = df[['time', sma_col]].copy().dropna()
                    sma_data.rename(columns={sma_col: 'value'}, inplace=True)
                    # ======================= FIX END ===========================

                    if not sma_data.empty:
                        line.set(sma_data)
                        self.sma_lines.append(line)

                except Exception as e:
                    logger.warning(f"Failed to add SMA {period} line: {e}")

    def _update_symbol_info(self, df: pd.DataFrame):
        """Update symbol information display"""
        try:
            if df.empty:
                return

            latest = df.iloc[-1]
            symbol = self.config.symbol
            interval_name = self._get_interval_display_name(self.config.interval)

            # Format price and change
            current_price = latest['close']
            if len(df) > 1:
                prev_price = df.iloc[-2]['close']
                change = current_price - prev_price
                change_pct = (change / prev_price) * 100

                change_str = f"{change:+.2f} ({change_pct:+.2f}%)"
                color = "#4aff4a" if change >= 0 else "#ff4a4a"
            else:
                change_str = "N/A"
                color = "#888888"

            # Update labels
            info_text = f"{symbol} • {interval_name} • ₹{current_price:.2f}"
            self.symbol_info_label.setText(info_text)
            self.symbol_info_label.setToolTip(f"Change: {change_str}")

        except Exception as e:
            logger.error(f"Error updating symbol info: {e}")

    def _get_interval_display_name(self, interval: str) -> str:
        """Convert interval to display name"""
        interval_map = {
            "day": "1D",
            "240minute": "4H",
            "60minute": "1H",
            "30minute": "30m",
            "15minute": "15m",
            "10minute": "10m",
            "5minute": "5m",
            "3minute": "3m",
            "minute": "1m"
        }
        return interval_map.get(interval, interval.upper())

    def _change_timeframe(self, interval: str):
        """Change chart timeframe with smooth transition"""
        if self.config.interval == interval or not self.config.symbol:
            return

        # Update button states
        for btn_interval, btn in self.timeframe_buttons.items():
            btn.setChecked(btn_interval == interval)

        # Update config and reload
        self.config.interval = interval
        self._save_state()
        self._load_chart_data()

    def _force_refresh(self):
        """Force refresh current chart data"""
        if self.config.symbol:
            self._load_chart_data(force_refresh=True)

    def _retry_load(self):
        """Retry loading chart data after error"""
        if self.config.symbol:
            self._load_chart_data()

    def _auto_refresh(self):
        """Auto refresh chart data"""
        if self.config.auto_refresh and self.config.symbol:
            self._load_chart_data(force_refresh=True)

    def _setup_auto_refresh(self):
        """Setup auto refresh timer"""
        self.auto_refresh_timer.stop()

        if self.config.auto_refresh:
            interval_ms = self.config.refresh_interval * 1000
            self.auto_refresh_timer.start(interval_ms)

    @Slot(dict)
    def _on_settings_changed(self, settings: dict):
        """Handle settings changes from control panel"""
        try:
            # Update configuration
            self.config.show_sma = settings.get('show_sma', True)
            self.config.show_volume = settings.get('show_volume', True)
            self.config.auto_refresh = settings.get('auto_refresh', False)
            self.config.refresh_interval = settings.get('refresh_interval', 30)
            self.config.sma_periods = settings.get('sma_periods', [10, 21, 51])

            # Setup auto refresh
            self._setup_auto_refresh()

            # Re-render chart if data is available
            if self.last_df is not None and not self.last_df.empty:
                self._render_chart(self.last_df)

            # Save settings
            self._save_state()

        except Exception as e:
            logger.error(f"Error handling settings change: {e}")

    def _stop_current_operations(self):
        """Stop all current operations"""
        # Stop data loading thread
        if self.data_loader_thread and self.data_loader_thread.isRunning():
            self.data_loader_thread.stop()
            self.data_loader_thread.terminate()
            self.data_loader_thread.wait(3000)  # Wait up to 3 seconds

        # Stop auto refresh
        self.auto_refresh_timer.stop()

    def _show_error(self, message: str):
        """Show error state with message"""
        self.error_label.setText(f"Error: {message}")
        self._set_state(ChartState.ERROR)

        # Show tooltip for detailed error
        QToolTip.showText(self.mapToGlobal(self.rect().center()), message)

    def _save_state(self):
        """Save current chart state"""
        try:
            state = {
                'symbol': self.config.symbol,
                'interval': self.config.interval,
                'show_sma': self.config.show_sma,
                'show_volume': self.config.show_volume,
                'auto_refresh': self.config.auto_refresh,
                'refresh_interval': self.config.refresh_interval,
                'sma_periods': self.config.sma_periods,
                'timestamp': datetime.now().isoformat()
            }

            with open(self.chart_state_file, 'w') as f:
                json.dump(state, f, indent=2)

        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def _load_saved_state(self):
        """Load saved chart state"""
        try:
            if not os.path.exists(self.chart_state_file):
                self._load_default_symbol()
                return

            with open(self.chart_state_file, 'r') as f:
                state = json.load(f)

            # Restore configuration
            self.config.symbol = state.get('symbol', '')
            self.config.interval = state.get('interval', 'day')
            self.config.show_sma = state.get('show_sma', True)
            self.config.show_volume = state.get('show_volume', True)
            self.config.auto_refresh = state.get('auto_refresh', False)
            self.config.refresh_interval = state.get('refresh_interval', 30)
            self.config.sma_periods = state.get('sma_periods', [10, 21, 51])

            # Update UI controls
            self._update_controls_from_config()

            # Load chart if symbol exists
            if self.config.symbol and self.config.symbol in self.instrument_map:
                self._load_chart_data()
            else:
                self._load_default_symbol()

        except Exception as e:
            logger.error(f"Failed to load saved state: {e}")
            self._load_default_symbol()

    def _update_controls_from_config(self):
        """Update UI controls from configuration"""
        try:
            # Update timeframe buttons
            for interval, btn in self.timeframe_buttons.items():
                btn.setChecked(interval == self.config.interval)

            # Update control panel
            self.control_panel.show_sma_checkbox.setChecked(self.config.show_sma)
            self.control_panel.show_volume_checkbox.setChecked(self.config.show_volume)
            self.control_panel.auto_refresh_checkbox.setChecked(self.config.auto_refresh)
            self.control_panel.refresh_interval_spinbox.setValue(self.config.refresh_interval)

            # Update SMA period inputs
            for i, period in enumerate(self.config.sma_periods):
                if i < len(self.control_panel.sma_period_inputs):
                    self.control_panel.sma_period_inputs[i].setValue(period)

        except Exception as e:
            logger.error(f"Error updating controls: {e}")

    def _load_default_symbol(self):
        """Load default symbol when no saved state"""
        if self.instrument_map:
            # Try to find a popular symbol first
            popular_symbols = ['NIFTY 50', 'BANKNIFTY', 'RELIANCE', 'TCS', 'INFY']

            for symbol in popular_symbols:
                if symbol in self.instrument_map:
                    self.config.symbol = symbol
                    self._load_chart_data()
                    return

            # Fall back to first available symbol
            self.config.symbol = next(iter(self.instrument_map.keys()))
            self._load_chart_data()
        else:
            self.symbol_info_label.setText("No instruments available")
            self._set_state(ChartState.ERROR)

    def _apply_enhanced_styles(self):
        """Apply enhanced styling for better visual appeal"""
        self.setStyleSheet("""
            /* Main widget */
            EnhancedChartWindow {
                background-color: #0d1117;
                color: #e6edf3;
            }

            /* Toolbar */
            QFrame {
                background-color: #161b22;
                border: 1px solid #30363d;
                border-radius: 6px;
            }

            /* Symbol info label */
            #symbolInfoLabel {
                color: #f0f6fc;
                font-size: 14px;
                font-weight: bold;
                font-family: "Segoe UI", system-ui;
                padding: 4px 8px;
            }

            /* Status indicator */
            #statusIndicator {
                font-size: 16px;
                font-weight: bold;
            }

            /* Buttons */
            #timeframeButton {
                background-color: #21262d;
                color: #7d8590;
                border: 1px solid #30363d;
                border-radius: 6px;
                font-size: 11px;
                font-weight: 600;
                padding: 2px 4px;
            }

            #timeframeButton:hover {
                background-color: #30363d;
                color: #f0f6fc;
                border-color: #58a6ff;
            }

            #timeframeButton:checked {
                background-color: #0969da;
                color: #ffffff;
                border-color: #0969da;
            }

            #timeframeButton:disabled {
                background-color: #161b22;
                color: #484f58;
                border-color: #21262d;
            }

            #refreshButton {
                background-color: #238636;
                color: #ffffff;
                border: 1px solid #238636;
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
            }

            #refreshButton:hover {
                background-color: #2ea043;
            }

            #refreshButton:pressed {
                background-color: #1a7f37;
            }

            #retryButton {
                background-color: #da3633;
                color: #ffffff;
                border: 1px solid #da3633;
                border-radius: 6px;
                font-size: 12px;
                font-weight: bold;
                padding: 8px 16px;
            }

            #retryButton:hover {
                background-color: #f85149;
            }

            /* Labels */
            #loadingLabel {
                color: #58a6ff;
                font-size: 16px;
                font-weight: bold;
                font-family: "Segoe UI", system-ui;
            }

            #errorLabel {
                color: #f85149;
                font-size: 16px;
                font-weight: bold;
                font-family: "Segoe UI", system-ui;
            }

            /* Progress bar */
            QProgressBar {
                background-color: #21262d;
                border: none;
                border-radius: 2px;
            }

            QProgressBar::chunk {
                background-color: #58a6ff;
                border-radius: 2px;
            }

            /* Control panel */
            ChartControlPanel {
                background-color: #161b22;
                border-left: 1px solid #30363d;
            }

            QGroupBox {
                color: #f0f6fc;
                font-weight: bold;
                border: 1px solid #30363d;
                border-radius: 6px;
                margin: 4px 0px;
                padding-top: 8px;
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px 0 4px;
            }

            QCheckBox {
                color: #e6edf3;
                font-size: 12px;
            }

            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #30363d;
                border-radius: 3px;
                background-color: #0d1117;
            }

            QCheckBox::indicator:checked {
                background-color: #0969da;
                border-color: #0969da;
            }

            QSpinBox {
                background-color: #0d1117;
                color: #e6edf3;
                border: 1px solid #30363d;
                border-radius: 6px;
                padding: 4px;
                font-size: 12px;
            }

            QSpinBox:focus {
                border-color: #58a6ff;
            }

            QLabel {
                color: #e6edf3;
                font-size: 12px;
            }

            /* Splitter */
            QSplitter::handle {
                background-color: #30363d;
                width: 2px;
            }

            QSplitter::handle:hover {
                background-color: #58a6ff;
            }

            /* Stacked widget */
            QStackedWidget {
                background-color: #0d1117;
                border: 1px solid #30363d;
                border-radius: 6px;
            }
        """)

    def closeEvent(self, event):
        """Handle widget close event"""
        try:
            # Stop all operations
            self._stop_current_operations()

            # Save current state
            self._save_state()

            # Clear cache
            self.data_cache.clear()

            logger.info("Enhanced chart widget closed successfully")

        except Exception as e:
            logger.error(f"Error during close: {e}")

        super().closeEvent(event)

    def showEvent(self, event):
        """Handle widget show event"""
        super().showEvent(event)

        # Ensure chart is properly initialized when shown
        if not self.chart:
            QTimer.singleShot(100, self._initialize_chart)

    def resizeEvent(self, event):
        """Handle resize event for responsive layout"""
        super().resizeEvent(event)

        # Hide control panel on small screens
        if self.width() < 800:
            self.control_panel.hide()
        else:
            self.control_panel.show()


# Alias for backward compatibility
ChartWindow = EnhancedChartWindow