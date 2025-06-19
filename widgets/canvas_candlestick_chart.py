# Fixed canvas_candlestick_chart.py

import logging
import json
import os
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Dict, Optional, Any

import pandas as pd
from PySide6.QtCore import Signal, Slot, QThread, Qt, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                               QStackedWidget, QLabel, QPushButton, QProgressBar,
                               QFrame, QMessageBox)
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWebEngineWidgets import QWebEngineView
from kiteconnect import KiteConnect
from cachetools import TTLCache
import threading

logger = logging.getLogger(__name__)


class ChartState(Enum):
    IDLE = "idle"
    LOADING = "loading"
    ERROR = "error"
    LOADED = "loaded"


class DataFetcher:
    """Simple data fetcher for historical market data"""

    def __init__(self, kite_client: KiteConnect):
        self.kite_client = kite_client

    def fetch_historical_data(self, instrument_token: int, from_date, to_date, interval: str):
        """Fetch historical data from KiteConnect"""
        try:
            return self.kite_client.historical_data(
                instrument_token=instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval=interval
            )
        except Exception as e:
            logger.error(f"Error fetching data: {e}")
            raise


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
    """Background thread for loading chart data"""
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

            # Calculate date range
            to_date = datetime.now().date()
            date_ranges = {
                "day": 730, "60minute": 120, "30minute": 60, "15minute": 30,
                "10minute": 21, "5minute": 14, "3minute": 10, "minute": 5
            }
            days_back = date_ranges.get(self.interval, 365)
            from_date = to_date - timedelta(days=days_back)

            if self._stop_requested:
                return

            self.load_progress.emit(30)

            # Fetch data
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
            self.cache.set(cache_key, df)
            self.load_progress.emit(100)
            self.data_loaded.emit(df, cache_key)

        except Exception as e:
            if not self._stop_requested:
                logger.error(f"Data loading error for {self.symbol}: {e}", exc_info=True)
                self.load_error.emit(f"Failed to load data: {str(e)}")

    def _process_data(self, raw_data: List[Dict]) -> pd.DataFrame:
        """Process raw data with validation"""
        try:
            df = pd.DataFrame(raw_data)
            if df.empty:
                return df

            # Data validation
            required_columns = ['date', 'open', 'high', 'low', 'close', 'volume']
            if not all(col in df.columns for col in required_columns):
                raise ValueError("Missing required columns")

            # Convert data types
            df['date'] = pd.to_datetime(df['date'])
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')

            # Clean data
            df = df.dropna()
            df = df.drop_duplicates(subset='date').sort_values('date')
            df = df.rename(columns={'date': 'time'})
            df['symbol'] = self.symbol

            return df

        except Exception as e:
            logger.error(f"Data processing error: {e}")
            raise


class CandlestickChart(QWidget):
    """Enhanced candlestick chart with simplified initialization"""

    def __init__(self, kite_client: KiteConnect, parent=None):
        super().__init__(parent)

        # Core components
        self.data_fetcher = DataFetcher(kite_client)
        self.data_cache = DataCache(maxsize=50, ttl=300)

        # State management
        self.instrument_map: Dict[str, Dict[str, Any]] = {}
        self.current_state = ChartState.IDLE
        self.data_loader_thread: Optional[ChartDataLoaderThread] = None
        self.last_df: Optional[pd.DataFrame] = None
        self.current_symbol: str = ""
        self.current_interval: str = "day"

        # UI components
        self.chart_view: Optional[QWebEngineView] = None
        self.timeframe_buttons: Dict[str, QPushButton] = {}

        self._setup_ui()
        self._apply_styles()
        self._setup_keyboard_shortcuts()

        # Initialize chart after UI is ready
        QTimer.singleShot(100, self._initialize_chart)

    def _setup_ui(self):
        """Setup the main UI layout"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Toolbar
        self.toolbar = self._create_toolbar()
        main_layout.addWidget(self.toolbar)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumHeight(3)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        main_layout.addWidget(self.progress_bar)

        # Chart container with stacked widget
        self.stacked_widget = QStackedWidget()
        main_layout.addWidget(self.stacked_widget)

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

        # Set initial state
        self._set_state(ChartState.IDLE)

    def _create_toolbar(self) -> QWidget:
        """Create toolbar with controls"""
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

        # Chart controls
        self.auto_scale_btn = QPushButton("Auto Scale")
        self.auto_scale_btn.setObjectName("controlButton")
        self.auto_scale_btn.setToolTip("Auto scale to fit all data")
        self.auto_scale_btn.clicked.connect(self._auto_scale_chart)
        layout.addWidget(self.auto_scale_btn)

        self.reset_zoom_btn = QPushButton("Reset")
        self.reset_zoom_btn.setObjectName("controlButton")
        self.reset_zoom_btn.setToolTip("Reset zoom and pan")
        self.reset_zoom_btn.clicked.connect(self._reset_chart_view)
        layout.addWidget(self.reset_zoom_btn)

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
        """Create loading widget"""
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
        """Setup keyboard shortcuts"""
        refresh_shortcut = QShortcut(QKeySequence.StandardKey.Refresh, self)
        refresh_shortcut.activated.connect(self._force_refresh)

        f5_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F5), self)
        f5_shortcut.activated.connect(self._force_refresh)

        # Add keyboard shortcuts for chart controls
        auto_scale_shortcut = QShortcut(QKeySequence("Ctrl+A"), self)
        auto_scale_shortcut.activated.connect(self._auto_scale_chart)

        reset_shortcut = QShortcut(QKeySequence("Ctrl+R"), self)
        reset_shortcut.activated.connect(self._reset_chart_view)

    def _initialize_chart(self):
        """Initialize chart after UI is ready"""
        self._create_chart_view()

    def _create_chart_view(self):
        """Create the web engine view for the chart"""
        try:
            # Clear existing chart
            if self.chart_view:
                self.chart_layout.removeWidget(self.chart_view)
                self.chart_view.deleteLater()

            # Create new chart view
            self.chart_view = QWebEngineView()
            self.chart_layout.addWidget(self.chart_view)

        except Exception as e:
            logger.error(f"Failed to create chart view: {e}")
            self._set_state(ChartState.ERROR)

    def _set_state(self, state: ChartState):
        """Update UI state"""
        self.current_state = state

        state_configs = {
            ChartState.IDLE: {
                'status_color': '#888888',
                'status_text': 'Ready',
                'widget_index': 2,
                'buttons_enabled': True
            },
            ChartState.LOADING: {
                'status_color': '#4a9eff',
                'status_text': 'Loading',
                'widget_index': 0,
                'buttons_enabled': False
            },
            ChartState.ERROR: {
                'status_color': '#ff4a4a',
                'status_text': 'Error',
                'widget_index': 1,
                'buttons_enabled': True
            },
            ChartState.LOADED: {
                'status_color': '#4aff4a',
                'status_text': 'Live',
                'widget_index': 2,
                'buttons_enabled': True
            }
        }

        config = state_configs.get(state, state_configs[ChartState.IDLE])

        # Update status indicator
        self.status_indicator.setStyleSheet(f"color: {config['status_color']};")
        self.status_indicator.setToolTip(config['status_text'])

        # Update widget visibility
        if self.stacked_widget.currentIndex() != config['widget_index']:
            self.stacked_widget.setCurrentIndex(config['widget_index'])

        # Update button states
        for btn in self.timeframe_buttons.values():
            btn.setEnabled(config['buttons_enabled'])
        self.refresh_button.setEnabled(config['buttons_enabled'])
        self.auto_scale_btn.setEnabled(config['buttons_enabled'])
        self.reset_zoom_btn.setEnabled(config['buttons_enabled'])

    def set_instrument_list(self, instruments: List[Dict[str, Any]]):
        """Set available instruments"""
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
        """Handle symbol search"""
        if not symbol or symbol not in self.instrument_map:
            if symbol:
                self._show_error(f"Symbol '{symbol}' not found")
            return

        # Stop any running operations
        self._stop_current_operations()

        # Update configuration
        self.current_symbol = symbol

        # Load new data
        self._load_chart_data()

    def _load_chart_data(self, force_refresh: bool = False):
        """Load chart data"""
        if not self.current_symbol or self.current_symbol not in self.instrument_map:
            return

        # Clear cache if force refresh
        if force_refresh:
            cache_key = f"{self.current_symbol}_{self.current_interval}"
            self.data_cache._cache.pop(cache_key, None)

        # Stop existing thread
        self._stop_current_operations()

        # Update UI state
        self._set_state(ChartState.LOADING)
        self.progress_bar.show()
        self.progress_bar.setValue(0)

        # Get instrument details
        instrument = self.instrument_map[self.current_symbol]
        instrument_token = instrument['instrument_token']

        # Start loading thread
        self.data_loader_thread = ChartDataLoaderThread(
            self.data_fetcher, instrument_token,
            self.current_symbol, self.current_interval, self.data_cache
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

            logger.info(f"Chart loaded: {self.current_symbol} ({len(df)} candles)")

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
        """Render chart using HTML5 Canvas with enhanced features"""
        try:
            if not self.chart_view:
                self._create_chart_view()

            # Prepare candlestick data
            candlestick_data = []
            volume_data = []

            for _, row in df.iterrows():
                timestamp = int(row['time'].timestamp() * 1000)  # JavaScript uses milliseconds

                candlestick_data.append({
                    'time': timestamp,
                    'open': float(row['open']),
                    'high': float(row['high']),
                    'low': float(row['low']),
                    'close': float(row['close'])
                })

                volume_data.append({
                    'time': timestamp,
                    'value': float(row['volume'])
                })

            # Create HTML content
            html_content = self._create_enhanced_chart_html(candlestick_data, volume_data)

            # Load HTML into web view
            self.chart_view.setHtml(html_content)

            logger.info(f"Chart rendered successfully for {self.current_symbol}")

        except Exception as e:
            logger.error(f"Chart rendering error: {e}")
            self._show_error("Failed to render chart")

    def _create_enhanced_chart_html(self, candlestick_data, volume_data):
        """Create enhanced HTML content for the chart"""

        candlestick_json = json.dumps(candlestick_data)
        volume_json = json.dumps(volume_data)

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Enhanced Candlestick Chart</title>
    <style>
        body {{
            margin: 0;
            padding: 0;
            background-color: #131722;
            font-family: 'Arial', sans-serif;
            overflow: hidden;
        }}
        #chartContainer {{
            width: 100vw;
            height: 100vh;
            position: relative;
        }}
        #mainCanvas {{
            background-color: #131722;
            cursor: crosshair;
        }}
        #info {{
            position: absolute;
            top: 10px;
            left: 10px;
            color: #d1d4dc;
            font-size: 14px;
            background-color: rgba(0, 0, 0, 0.7);
            padding: 12px;
            border-radius: 6px;
            pointer-events: none;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.3);
        }}
        #controls {{
            position: absolute;
            top: 10px;
            right: 10px;
            color: #d1d4dc;
            font-size: 12px;
            background-color: rgba(0, 0, 0, 0.7);
            padding: 8px;
            border-radius: 4px;
            pointer-events: none;
        }}
    </style>
</head>
<body>
    <div id="chartContainer">
        <canvas id="mainCanvas"></canvas>
        <div id="info">
            <div id="symbolName">{self.current_symbol} - {self._get_interval_display_name(self.current_interval)}</div>
            <div id="priceInfo">Loading...</div>
        </div>
        <div id="controls">
            <div>Mouse: Pan | Wheel: Zoom | Shift+Wheel: Horizontal Zoom</div>
            <div>Ctrl+A: Auto Scale | Ctrl+R: Reset View</div>
        </div>
    </div>

    <script>
        class EnhancedCandlestickChart {{
            constructor(canvasId, data, volumeData) {{
                this.canvas = document.getElementById(canvasId);
                this.ctx = this.canvas.getContext('2d');
                this.data = data;
                this.volumeData = volumeData;
                this.width = 0;
                this.height = 0;
                this.padding = {{ top: 50, right: 100, bottom: 50, left: 0 }}; // Removed left padding
                this.chartArea = {{}};
                this.volumeArea = {{}};

                // Chart state - simplified defaults with fixed candle dimensions
                this.minPrice = 0;
                this.maxPrice = 0;
                this.minVolume = 0;
                this.maxVolume = 0;
                this.fixedCandleWidth = 4;  // Fixed candle width
                this.fixedCandleSpacing = 2;  // Fixed spacing between candles
                this.candleWidth = this.fixedCandleWidth;
                this.candleSpacing = this.fixedCandleSpacing;
                this.visibleCandleCount = 100;  // Will be calculated based on available space

                // Interaction state
                this.isDragging = false;
                this.lastMouseX = 0;
                this.lastMouseY = 0;
                this.isAutoScale = true;

                // Colors
                this.colors = {{
                    upCandle: '#26a69a',
                    downCandle: '#ef5350',
                    grid: '#2a2a3d',
                    text: '#d1d4dc',
                    volume: '#555',
                    volumeUp: '#26a69a40',
                    volumeDown: '#ef535040',
                    background: '#131722',
                    crosshair: '#ffffff40'
                }};

                this.setupCanvas();
                this.calculateBounds();
                this.setupEventListeners();
                this.draw();
            }}

            setupCanvas() {{
                this.resizeCanvas();
                window.addEventListener('resize', () => this.resizeCanvas());
            }}

            resizeCanvas() {{
                const container = this.canvas.parentElement;
                this.width = container.clientWidth;
                this.height = container.clientHeight;

                this.canvas.width = this.width * window.devicePixelRatio;
                this.canvas.height = this.height * window.devicePixelRatio;
                this.canvas.style.width = this.width + 'px';
                this.canvas.style.height = this.height + 'px';

                this.ctx.scale(window.devicePixelRatio, window.devicePixelRatio);

                // Better space allocation - 75% for main chart, 20% for volume, 5% for spacing
                const totalChartHeight = this.height - this.padding.top - this.padding.bottom;
                const volumeHeight = Math.floor(totalChartHeight * 0.2);
                const spacing = Math.floor(totalChartHeight * 0.02);
                const mainChartHeight = totalChartHeight - volumeHeight - spacing;

                this.chartArea = {{
                    x: this.padding.left,
                    y: this.padding.top,
                    width: this.width - this.padding.left - this.padding.right,
                    height: mainChartHeight
                }};

                this.volumeArea = {{
                    x: this.padding.left,
                    y: this.chartArea.y + this.chartArea.height + spacing,
                    width: this.chartArea.width,
                    height: volumeHeight
                }};

                this.calculateBounds();
                this.draw();
            }}

            calculateBounds() {{
                if (this.data.length === 0) return;

                // Calculate how many candles can fit in the available width with fixed dimensions
                const totalCandleSpace = this.fixedCandleWidth + this.fixedCandleSpacing;
                this.visibleCandleCount = Math.floor(this.chartArea.width / totalCandleSpace);

                // Ensure we don't try to show more candles than available
                this.visibleCandleCount = Math.min(this.visibleCandleCount, this.data.length);

                // Set actual candle dimensions
                this.candleWidth = this.fixedCandleWidth;
                this.candleSpacing = this.fixedCandleSpacing;

                // Calculate viewport to show latest candles
                this.viewPortEnd = this.data.length - 1;
                this.viewPortStart = Math.max(0, this.viewPortEnd - this.visibleCandleCount + 1);

                const visibleData = this.data.slice(this.viewPortStart, this.viewPortEnd + 1);

                if (this.isAutoScale || this.minPrice === 0 || this.maxPrice === 0) {{
                    this.minPrice = Math.min(...visibleData.map(d => d.low));
                    this.maxPrice = Math.max(...visibleData.map(d => d.high));

                    const priceRange = this.maxPrice - this.minPrice;
                    this.minPrice -= priceRange * 0.05;
                    this.maxPrice += priceRange * 0.05;
                }}

                this.minVolume = 0;
                this.maxVolume = Math.max(...this.volumeData.slice(this.viewPortStart, this.viewPortEnd + 1).map(d => d.value));
            }}

            setupEventListeners() {{
                this.canvas.addEventListener('mousedown', (e) => {{
                    this.isDragging = true;
                    this.lastMouseX = e.clientX;
                    this.lastMouseY = e.clientY;
                    this.canvas.style.cursor = 'grabbing';
                }});

                this.canvas.addEventListener('mousemove', (e) => {{
                    if (this.isDragging) {{
                        const deltaX = e.clientX - this.lastMouseX;
                        const deltaY = e.clientY - this.lastMouseY;

                        // Horizontal panning - move viewport based on mouse movement
                        const totalCandleSpace = this.fixedCandleWidth + this.fixedCandleSpacing;
                        const candleDelta = Math.round(deltaX / totalCandleSpace);

                        if (candleDelta !== 0) {{
                            // Calculate new viewport position
                            const newStart = Math.max(0, Math.min(this.data.length - this.visibleCandleCount, this.viewPortStart - candleDelta));
                            this.viewPortStart = newStart;
                            this.viewPortEnd = Math.min(this.data.length - 1, this.viewPortStart + this.visibleCandleCount - 1);
                        }}

                        // Vertical panning (price axis) - only if not in auto scale mode
                        if (!this.isAutoScale) {{
                            const priceRange = this.maxPrice - this.minPrice;
                            const pricePerPixel = priceRange / this.chartArea.height;
                            const priceDelta = deltaY * pricePerPixel;

                            this.minPrice += priceDelta;
                            this.maxPrice += priceDelta;
                        }}

                        this.lastMouseX = e.clientX;
                        this.lastMouseY = e.clientY;

                        this.calculateBounds();
                        this.draw();
                    }} else {{
                        this.updateCrosshair(e);
                    }}
                }});

                this.canvas.addEventListener('mouseup', () => {{
                    this.isDragging = false;
                    this.canvas.style.cursor = 'crosshair';
                }});

                this.canvas.addEventListener('mouseleave', () => {{
                    this.isDragging = false;
                    this.canvas.style.cursor = 'crosshair';
                }});

                this.canvas.addEventListener('wheel', (e) => {{
                    e.preventDefault();

                    if (e.shiftKey) {{
                        // Horizontal zoom - not applicable with fixed candle width
                        // Fixed candle width means we show as many as can fit
                        return; // Disabled horizontal zoom to maintain fixed candle size

                    }} else {{
                        // Vertical zoom
                        this.isAutoScale = false;
                        const zoomFactor = e.deltaY > 0 ? 1.1 : 0.9;
                        const mouseY = e.clientY - this.canvas.getBoundingClientRect().top;
                        const priceAtMouse = this.yToPrice(mouseY);

                        const currentRange = this.maxPrice - this.minPrice;
                        const newRange = currentRange * zoomFactor;

                        const ratio = (priceAtMouse - this.minPrice) / currentRange;
                        this.minPrice = priceAtMouse - (newRange * ratio);
                        this.maxPrice = priceAtMouse + (newRange * (1 - ratio));
                    }}

                    this.calculateBounds();
                    this.draw();
                }});

                // Keyboard shortcuts
                document.addEventListener('keydown', (e) => {{
                    if (e.ctrlKey && e.key === 'a') {{
                        e.preventDefault();
                        this.autoScale();
                    }} else if (e.ctrlKey && e.key === 'r') {{
                        e.preventDefault();
                        this.resetView();
                    }}
                }});
            }}

            updateCrosshair(e) {{
                const rect = this.canvas.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;

                // Find nearest candle
                const candleIndex = this.xToCandle(x);

                if (candleIndex >= this.viewPortStart && candleIndex <= this.viewPortEnd && candleIndex < this.data.length) {{
                    const candle = this.data[candleIndex];
                    const date = new Date(candle.time);
                    const dateStr = this.formatTimeLabel(date, '{self.current_interval}');
                    const change = candle.close - candle.open;
                    const changePercent = ((change / candle.open) * 100).toFixed(2);
                    const changeStr = change >= 0 ? `+₹${{change.toFixed(2)}} (+${{changePercent}}%)` : `₹${{change.toFixed(2)}} (${{changePercent}}%)`;

                    const info = `${{dateStr}} | O: ₹${{candle.open.toFixed(2)}} H: ₹${{candle.high.toFixed(2)}} L: ₹${{candle.low.toFixed(2)}} C: ₹${{candle.close.toFixed(2)}} | ${{changeStr}}`;
                    document.getElementById('priceInfo').textContent = info;

                    this.crosshairX = x;
                    this.crosshairY = y;
                    this.draw();
                }}
            }}

            formatTimeLabel(date, interval) {{
                switch(interval) {{
                    case 'day':
                        return date.toLocaleDateString('en-GB', {{ day: '2-digit', month: 'short', year: 'numeric' }});
                    case '60minute':
                    case '30minute':
                        return date.toLocaleDateString('en-GB', {{ day: '2-digit', month: 'short' }}) + ' ' + 
                               date.toLocaleTimeString('en-GB', {{ hour: '2-digit', minute: '2-digit' }});
                    case '15minute':
                    case '5minute':
                    case '3minute':
                    case 'minute':
                        return date.toLocaleDateString('en-GB', {{ day: '2-digit', month: 'short' }}) + ' ' + 
                               date.toLocaleTimeString('en-GB', {{ hour: '2-digit', minute: '2-digit' }});
                    default:
                        return date.toLocaleDateString('en-GB');
                }}
            }}

            autoScale() {{
                this.isAutoScale = true;
                this.calculateBounds();
                this.draw();
            }}

            resetView() {{
                this.isAutoScale = true;
                // Recalculate how many candles fit with current dimensions
                const totalCandleSpace = this.fixedCandleWidth + this.fixedCandleSpacing;
                this.visibleCandleCount = Math.floor(this.chartArea.width / totalCandleSpace);
                this.visibleCandleCount = Math.min(this.visibleCandleCount, this.data.length);
                this.viewPortStart = Math.max(0, this.data.length - this.visibleCandleCount);
                this.viewPortEnd = this.data.length - 1;
                this.calculateBounds();
                this.draw();
            }}

            draw() {{
                // Clear canvas
                this.ctx.fillStyle = this.colors.background;
                this.ctx.fillRect(0, 0, this.width, this.height);

                // Removed grid drawing for clean, solid black look
                this.drawCandlesticks();
                this.drawVolume();
                this.drawAxes();
                this.drawCrosshair();
            }}

            drawGrid() {{
                this.ctx.strokeStyle = this.colors.grid;
                this.ctx.lineWidth = 1;

                // Main chart horizontal grid lines
                const priceStep = (this.maxPrice - this.minPrice) / 8;
                for (let i = 0; i <= 8; i++) {{
                    const price = this.minPrice + (priceStep * i);
                    const y = this.priceToY(price);

                    this.ctx.beginPath();
                    this.ctx.moveTo(this.chartArea.x, y);
                    this.ctx.lineTo(this.chartArea.x + this.chartArea.width, y);
                    this.ctx.stroke();
                }}

                // Volume area horizontal grid lines
                const volumeStep = this.maxVolume / 4;
                for (let i = 0; i <= 4; i++) {{
                    const volume = volumeStep * i;
                    const y = this.volumeToY(volume);

                    this.ctx.beginPath();
                    this.ctx.moveTo(this.volumeArea.x, y);
                    this.ctx.lineTo(this.volumeArea.x + this.volumeArea.width, y);
                    this.ctx.stroke();
                }}

                // Vertical grid lines
                const visibleCandles = this.viewPortEnd - this.viewPortStart + 1;
                const timeStep = Math.max(1, Math.floor(visibleCandles / 8));

                for (let i = this.viewPortStart; i <= this.viewPortEnd; i += timeStep) {{
                    const x = this.candleToX(i);

                    this.ctx.beginPath();
                    this.ctx.moveTo(x, this.chartArea.y);
                    this.ctx.lineTo(x, this.volumeArea.y + this.volumeArea.height);
                    this.ctx.stroke();
                }}
            }}

            drawCandlesticks() {{
                for (let i = this.viewPortStart; i <= this.viewPortEnd; i++) {{
                    if (i >= this.data.length) break;

                    const candle = this.data[i];
                    const x = this.candleToX(i);

                    const openY = this.priceToY(candle.open);
                    const closeY = this.priceToY(candle.close);
                    const highY = this.priceToY(candle.high);
                    const lowY = this.priceToY(candle.low);

                    const isUp = candle.close >= candle.open;
                    this.ctx.fillStyle = isUp ? this.colors.upCandle : this.colors.downCandle;
                    this.ctx.strokeStyle = isUp ? this.colors.upCandle : this.colors.downCandle;
                    this.ctx.lineWidth = 1;

                    // Draw wick
                    this.ctx.beginPath();
                    this.ctx.moveTo(x + this.candleWidth / 2, highY);
                    this.ctx.lineTo(x + this.candleWidth / 2, lowY);
                    this.ctx.stroke();

                    // Draw body
                    const bodyHeight = Math.abs(closeY - openY);
                    const bodyY = Math.min(openY, closeY);

                    if (bodyHeight < 1) {{
                        // Doji - draw line
                        this.ctx.lineWidth = 1;
                        this.ctx.beginPath();
                        this.ctx.moveTo(x, openY);
                        this.ctx.lineTo(x + this.candleWidth, openY);
                        this.ctx.stroke();
                    }} else {{
                        this.ctx.fillRect(x, bodyY, this.candleWidth, bodyHeight);
                    }}
                }}
            }}

            drawVolume() {{
                for (let i = this.viewPortStart; i <= this.viewPortEnd; i++) {{
                    if (i >= this.volumeData.length) break;

                    const volume = this.volumeData[i];
                    const candle = this.data[i];
                    const x = this.candleToX(i);

                    const height = (volume.value / this.maxVolume) * this.volumeArea.height;
                    const isUp = candle.close >= candle.open;

                    this.ctx.fillStyle = isUp ? this.colors.volumeUp : this.colors.volumeDown;
                    this.ctx.fillRect(x, this.volumeArea.y + this.volumeArea.height - height, this.candleWidth, height);
                }}
            }}

            drawAxes() {{
                this.ctx.fillStyle = this.colors.text;
                this.ctx.font = '11px Arial';
                this.ctx.textAlign = 'left';

                // Price axis (right side)
                const priceStep = (this.maxPrice - this.minPrice) / 8;
                for (let i = 0; i <= 8; i++) {{
                    const price = this.minPrice + (priceStep * i);
                    const y = this.priceToY(price);
                    const text = '₹' + price.toFixed(2);

                    // Background for better readability
                    const textWidth = this.ctx.measureText(text).width;
                    this.ctx.fillStyle = 'rgba(19, 23, 34, 0.8)';
                    this.ctx.fillRect(this.chartArea.x + this.chartArea.width + 2, y - 8, textWidth + 4, 16);

                    this.ctx.fillStyle = this.colors.text;
                    this.ctx.fillText(text, this.chartArea.x + this.chartArea.width + 4, y + 4);
                }}

                // Volume axis (right side of volume area)
                const volumeStep = this.maxVolume / 4;
                for (let i = 0; i <= 4; i++) {{
                    const volume = volumeStep * i;
                    const y = this.volumeToY(volume);
                    const text = this.formatVolume(volume);

                    const textWidth = this.ctx.measureText(text).width;
                    this.ctx.fillStyle = 'rgba(19, 23, 34, 0.8)';
                    this.ctx.fillRect(this.volumeArea.x + this.volumeArea.width + 2, y - 8, textWidth + 4, 16);

                    this.ctx.fillStyle = this.colors.text;
                    this.ctx.fillText(text, this.volumeArea.x + this.volumeArea.width + 4, y + 4);
                }}

                // Time axis (bottom) - horizontal labels
                const visibleCandles = this.viewPortEnd - this.viewPortStart + 1;
                const timeStep = Math.max(1, Math.floor(visibleCandles / 6));

                this.ctx.textAlign = 'center';
                for (let i = this.viewPortStart; i <= this.viewPortEnd; i += timeStep) {{
                    if (i >= this.data.length) break;

                    const x = this.candleToX(i) + this.candleWidth / 2;
                    const date = new Date(this.data[i].time);
                    const text = this.getTimeAxisLabel(date, '{self.current_interval}');

                    // Background for better readability
                    const textWidth = this.ctx.measureText(text).width;
                    this.ctx.fillStyle = 'rgba(19, 23, 34, 0.8)';
                    this.ctx.fillRect(x - textWidth/2 - 2, this.volumeArea.y + this.volumeArea.height + 5, textWidth + 4, 16);

                    this.ctx.fillStyle = this.colors.text;
                    this.ctx.fillText(text, x, this.volumeArea.y + this.volumeArea.height + 17);
                }}

                this.ctx.textAlign = 'left';
            }}

            getTimeAxisLabel(date, interval) {{
                const now = new Date();
                const daysDiff = Math.floor((now - date) / (1000 * 60 * 60 * 24));

                switch(interval) {{
                    case 'day':
                        if (daysDiff < 7) {{
                            return date.toLocaleDateString('en-GB', {{ weekday: 'short' }});
                        }} else if (daysDiff < 30) {{
                            return date.toLocaleDateString('en-GB', {{ day: '2-digit', month: 'short' }});
                        }} else {{
                            return date.toLocaleDateString('en-GB', {{ month: 'short', year: '2-digit' }});
                        }}
                    case '60minute':
                    case '30minute':
                        return date.toLocaleDateString('en-GB', {{ day: '2-digit' }}) + '\\n' + 
                               date.toLocaleTimeString('en-GB', {{ hour: '2-digit', minute: '2-digit' }});
                    default:
                        return date.toLocaleDateString('en-GB', {{ day: '2-digit', month: 'short' }});
                }}
            }}

            formatVolume(volume) {{
                if (volume >= 1e7) {{
                    return (volume / 1e7).toFixed(1) + 'Cr';
                }} else if (volume >= 1e5) {{
                    return (volume / 1e5).toFixed(1) + 'L';
                }} else if (volume >= 1e3) {{
                    return (volume / 1e3).toFixed(1) + 'K';
                }} else {{
                    return volume.toFixed(0);
                }}
            }}

            drawCrosshair() {{
                if (this.crosshairX && this.crosshairY) {{
                    this.ctx.strokeStyle = this.colors.crosshair;
                    this.ctx.lineWidth = 1;
                    this.ctx.setLineDash([5, 5]);

                    // Vertical line
                    this.ctx.beginPath();
                    this.ctx.moveTo(this.crosshairX, this.chartArea.y);
                    this.ctx.lineTo(this.crosshairX, this.volumeArea.y + this.volumeArea.height);
                    this.ctx.stroke();

                    // Horizontal line (only in main chart area)
                    if (this.crosshairY >= this.chartArea.y && this.crosshairY <= this.chartArea.y + this.chartArea.height) {{
                        this.ctx.beginPath();
                        this.ctx.moveTo(this.chartArea.x, this.crosshairY);
                        this.ctx.lineTo(this.chartArea.x + this.chartArea.width, this.crosshairY);
                        this.ctx.stroke();
                    }}

                    this.ctx.setLineDash([]);
                }}
            }}

            // Helper methods
            priceToY(price) {{
                const ratio = (price - this.minPrice) / (this.maxPrice - this.minPrice);
                return this.chartArea.y + this.chartArea.height - (ratio * this.chartArea.height);
            }}

            yToPrice(y) {{
                const ratio = (this.chartArea.y + this.chartArea.height - y) / this.chartArea.height;
                return this.minPrice + (ratio * (this.maxPrice - this.minPrice));
            }}

            volumeToY(volume) {{
                const ratio = volume / this.maxVolume;
                return this.volumeArea.y + this.volumeArea.height - (ratio * this.volumeArea.height);
            }}

            candleToX(index) {{
                const relativeIndex = index - this.viewPortStart;
                const totalCandleSpace = this.fixedCandleWidth + this.fixedCandleSpacing;
                return this.chartArea.x + (relativeIndex * totalCandleSpace);
            }}

            xToCandle(x) {{
                const relativeX = x - this.chartArea.x;
                const totalCandleSpace = this.fixedCandleWidth + this.fixedCandleSpacing;
                const candleIndex = Math.floor(relativeX / totalCandleSpace);
                return this.viewPortStart + candleIndex;
            }}
        }}

        // Initialize chart
        const candlestickData = {candlestick_json};
        const volumeData = {volume_json};

        if (candlestickData.length > 0) {{
            const chart = new EnhancedCandlestickChart('mainCanvas', candlestickData, volumeData);

            // Expose chart methods to parent window
            window.autoScale = () => chart.autoScale();
            window.resetView = () => chart.resetView();
        }} else {{
            document.getElementById('priceInfo').textContent = 'No data available';
        }}
    </script>
</body>
</html>
        """

        return html

    def _auto_scale_chart(self):
        """Auto scale the chart to fit all visible data"""
        if self.chart_view:
            self.chart_view.page().runJavaScript("if (window.autoScale) window.autoScale();")

    def _reset_chart_view(self):
        """Reset chart view to default"""
        if self.chart_view:
            self.chart_view.page().runJavaScript("if (window.resetView) window.resetView();")

    def _update_symbol_info(self, df: pd.DataFrame):
        """Update symbol information display"""
        try:
            if df.empty:
                return

            latest = df.iloc[-1]
            symbol = self.current_symbol
            interval_name = self._get_interval_display_name(self.current_interval)

            # Format price and change
            current_price = latest['close']
            if len(df) > 1:
                prev_price = df.iloc[-2]['close']
                change = current_price - prev_price
                change_pct = (change / prev_price) * 100
                change_str = f"{change:+.2f} ({change_pct:+.2f}%)"
            else:
                change_str = "N/A"

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
            "60minute": "1H",
            "15minute": "15m",
            "5minute": "5m"
        }
        return interval_map.get(interval, interval.upper())

    def _change_timeframe(self, interval: str):
        """Change chart timeframe"""
        if self.current_interval == interval or not self.current_symbol:
            return

        # Update button states
        for btn_interval, btn in self.timeframe_buttons.items():
            btn.setChecked(btn_interval == interval)

        # Update config and reload
        self.current_interval = interval
        self._load_chart_data()

    def _force_refresh(self):
        """Force refresh current chart data"""
        if self.current_symbol:
            self._load_chart_data(force_refresh=True)

    def _retry_load(self):
        """Retry loading chart data after error"""
        if self.current_symbol:
            self._load_chart_data()

    def _stop_current_operations(self):
        """Stop all current operations"""
        # Stop data loading thread
        if self.data_loader_thread and self.data_loader_thread.isRunning():
            self.data_loader_thread.stop()
            self.data_loader_thread.wait(3000)

    def _show_error(self, message: str):
        """Show error state with message"""
        self.error_label.setText(f"Error: {message}")
        self._set_state(ChartState.ERROR)

    def _apply_styles(self):
        """Apply enhanced styling for better visual appeal"""
        self.setStyleSheet("""
            /* Main widget */
            CandlestickChart {
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

            /* Control buttons */
            #controlButton {
                background-color: #21262d;
                color: #7d8590;
                border: 1px solid #30363d;
                border-radius: 6px;
                font-size: 11px;
                font-weight: 600;
                padding: 6px 12px;
            }

            #controlButton:hover {
                background-color: #30363d;
                color: #f0f6fc;
                border-color: #58a6ff;
            }

            #controlButton:pressed {
                background-color: #0969da;
                color: #ffffff;
                border-color: #0969da;
            }

            /* Timeframe buttons */
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

            # Clear cache
            self.data_cache.clear()

            logger.info("Enhanced candlestick chart widget closed successfully")

        except Exception as e:
            logger.error(f"Error during close: {e}")

        super().closeEvent(event)


# Alias for backward compatibility
ChartWindow = CandlestickChart