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

    def __init__(self_self, maxsize: int = 100, ttl: int = 300):
        self_self._cache = TTLCache(maxsize=maxsize, ttl=ttl)
        self_self._lock = threading.RLock()

    def get(self_self, key: str) -> Optional[pd.DataFrame]:
        with self_self._lock:
            return self_self._cache.get(key)

    def set(self_self, key: str, value: pd.DataFrame) -> None:
        with self_self._lock:
            self_self._cache[key] = value.copy()

    def clear(self_self) -> None:
        with self_self._lock:
            self_self._cache.clear()


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

            # Convert data types - FIXED: was missing df[col]
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

    # Signal to emit when the Order button in the chart's toolbar is clicked
    order_button_clicked = Signal(str, float)  # Emits symbol and LTP

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
        self.current_ltp: float = 0.0  # Store current LTP for order button
        self.current_instrument_token: int = 0  # Added to easily compare with incoming ticks

        # UI components
        self.chart_view: Optional[QWebEngineView] = None
        self.timeframe_buttons: Dict[str, QPushButton] = {}
        self.order_btn: Optional[QPushButton] = None  # Reference to the new order button

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
        toolbar.setObjectName("chartToolbar")
        toolbar.setFixedHeight(35)

        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(10, 2, 10, 2)
        layout.setSpacing(6)

        # Symbol info
        self.symbol_info_label = QLabel("No Symbol Selected")
        self.symbol_info_label.setObjectName("symbolInfoLabel")
        font = QFont()
        font.setBold(True)
        self.symbol_info_label.setFont(font)
        layout.addWidget(self.symbol_info_label)

        layout.addStretch()

        # Order button
        self.order_btn = QPushButton("Order")
        self.order_btn.setObjectName("orderButton")
        self.order_btn.setToolTip("Place an order for the current symbol")
        self.order_btn.setFixedSize(60, 28)
        self.order_btn.clicked.connect(self._on_order_button_clicked)  # Connect to new slot
        layout.addWidget(self.order_btn)

        # Chart controls
        self.auto_scale_btn = QPushButton("Auto Scale")
        self.auto_scale_btn.setObjectName("controlButton")
        self.auto_scale_btn.setToolTip("Auto scale to fit all data")
        self.auto_scale_btn.clicked.connect(self._auto_scale_chart)
        layout.addWidget(self.auto_scale_btn)

        # Refresh button
        self.refresh_button = QPushButton("⟳ Refresh")
        self.refresh_button.setObjectName("refreshButton")
        self.refresh_button.setFixedSize(80, 28)
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
            btn.setFixedSize(40, 28)
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
                'widget_index': 2,
                'buttons_enabled': True
            },
            ChartState.LOADING: {
                'widget_index': 0,
                'buttons_enabled': False
            },
            ChartState.ERROR: {
                'widget_index': 1,
                'buttons_enabled': True
            },
            ChartState.LOADED: {
                'widget_index': 2,
                'buttons_enabled': True
            }
        }

        config = state_configs.get(state, state_configs[ChartState.IDLE])

        # Update widget visibility
        if self.stacked_widget.currentIndex() != config['widget_index']:
            self.stacked_widget.setCurrentIndex(config['widget_index'])

        # Update button states
        for btn in self.timeframe_buttons.values():
            btn.setEnabled(config['buttons_enabled'])
        self.refresh_button.setEnabled(config['buttons_enabled'])
        self.auto_scale_btn.setEnabled(config['buttons_enabled'])
        if self.order_btn:  # Enable/disable order button based on state
            self.order_btn.setEnabled(config['buttons_enabled'] and self.current_symbol != "")

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
        self.current_instrument_token = self.instrument_map[symbol]['instrument_token']  # Set instrument token
        self._set_state(ChartState.IDLE)  # Reset state to ensure order button is updated

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
            background-color: #0a0a0a;
            font-family: 'Arial', sans-serif;
            overflow: hidden;
        }}
        #chartContainer {{
            width: 100vw;
            height: 100vh;
            position: relative;
        }}
        #mainCanvas {{
            background-color: #0a0a0a;
            cursor: crosshair;
        }}
        #info {{
            position: absolute;
            top: 5px;
            left: 5px;
            color: #e0e0e0;
            font-size: 13px;
            background-color: rgba(0, 0, 0, 0.0);
            padding: 0px;
            border-radius: 0px;
            pointer-events: none;
        }}
    </style>
</head>
<body>
    <div id="chartContainer">
        <canvas id="mainCanvas"></canvas>
        <div id="info">
            <div id="priceInfo">Loading...</div>
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
                this.padding = {{ top: 30, right: 70, bottom: 25, left: 5 }};
                this.chartArea = {{}};
                this.volumeArea = {{}};

                // Chart state
                this.minPrice = 0;
                this.maxPrice = 0;
                this.minVolume = 0;
                this.maxVolume = 0;
                this.fixedCandleWidth = 4;
                this.fixedCandleSpacing = 2;
                this.candleWidth = this.fixedCandleWidth;
                this.candleSpacing = this.fixedCandleSpacing;
                this.visibleCandleCount = 100;

                // Interaction state
                this.isDragging = false;
                this.lastMouseX = 0;
                this.lastMouseY = 0;
                this.isAutoScale = true;

                // Colors
                this.colors = {{
                    upCandle: '#26a69a',
                    downCandle: '#ef5350',
                    grid: '#151515',
                    text: '#e0e0e0',
                    volume: '#555',
                    volumeUp: 'rgba(38, 166, 154, 0.3)',
                    volumeDown: 'rgba(239, 83, 80, 0.3)',
                    background: '#0a0a0a',
                    crosshair: 'rgba(160, 192, 255, 0.4)'
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

                const totalCandleSpace = this.fixedCandleWidth + this.fixedCandleSpacing;
                this.visibleCandleCount = Math.floor(this.chartArea.width / totalCandleSpace);
                this.visibleCandleCount = Math.min(this.visibleCandleCount, this.data.length);

                this.candleWidth = this.fixedCandleWidth;
                this.candleSpacing = this.fixedCandleSpacing;

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

                        const totalCandleSpace = this.fixedCandleWidth + this.fixedCandleSpacing;
                        const candleDelta = Math.round(deltaX / totalCandleSpace);

                        if (candleDelta !== 0) {{
                            const newStart = Math.max(0, Math.min(this.data.length - this.visibleCandleCount, this.viewPortStart - candleDelta));
                            this.viewPortStart = newStart;
                            this.viewPortEnd = Math.min(this.data.length - 1, this.viewPortStart + this.visibleCandleCount - 1);
                        }}

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
                    this.crosshairX = null;
                    this.crosshairY = null;
                    document.getElementById('priceInfo').textContent = 'Hover over candle for details';
                    this.draw();
                }});

                this.canvas.addEventListener('wheel', (e) => {{
                    e.preventDefault();

                    if (e.shiftKey) {{
                        return;
                    }} else {{
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

                document.addEventListener('keydown', (e) => {{
                    if (e.ctrlKey && e.key === 'a') {{
                        e.preventDefault();
                        this.autoScale();
                    }}
                }});
            }}

            updateCrosshair(e) {{
                const rect = this.canvas.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;

                const candleIndex = this.xToCandle(x);

                if (candleIndex >= this.viewPortStart && candleIndex <= this.viewPortEnd && candleIndex < this.data.length) {{
                    const candle = this.data[candleIndex];
                    const date = new Date(candle.time);
                    const dateStr = this.formatTimeLabel(date, '{self.current_interval}');
                    const change = candle.close - candle.open;
                    const changePercent = ((change / candle.open) * 100).toFixed(2);
                    const changeStr = change >= 0 ? `+₹${{change.toFixed(2)}} (+${{changePercent}}%)` : `₹${{change.toFixed(2)}} (${{changePercent}}%)`;

                    const info = `O: ₹${{candle.open.toFixed(2)}} H: ₹${{candle.high.toFixed(2)}} L: ₹${{candle.low.toFixed(2)}} C: ₹${{candle.close.toFixed(2)}} | Vol: ${{this.formatVolume(this.volumeData[candleIndex].value)}} | ${{changeStr}}`;
                    document.getElementById('priceInfo').textContent = info;

                    this.crosshairX = x;
                    this.crosshairY = y;
                    this.draw();
                }} else {{
                    this.crosshairX = null;
                    this.crosshairY = null;
                    document.getElementById('priceInfo').textContent = 'Hover over candle for details';
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

            draw() {{
                this.ctx.fillStyle = this.colors.background;
                this.ctx.fillRect(0, 0, this.width, this.height);

                this.drawCandlesticks();
                this.drawVolume();
                this.drawAxes();
                this.drawCrosshair();
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
                const priceStep = (this.maxPrice - this.minPrice) / 6;
                for (let i = 0; i <= 6; i++) {{
                    const price = this.minPrice + (priceStep * i);
                    const y = this.priceToY(price);
                    const text = '₹' + price.toFixed(2);

                    this.ctx.fillStyle = this.colors.text;
                    this.ctx.fillText(text, this.chartArea.x + this.chartArea.width + 4, y + 4);
                }}

                // Volume axis (right side of volume area)
                const volumeStep = this.maxVolume / 3;
                for (let i = 0; i <= 3; i++) {{
                    const volume = volumeStep * i;
                    const y = this.volumeToY(volume);
                    const text = this.formatVolume(volume);

                    this.ctx.fillStyle = this.colors.text;
                    this.ctx.fillText(text, this.volumeArea.x + this.volumeArea.width + 4, y + 4);
                }}

                // Time axis (bottom)
                const visibleCandles = this.viewPortEnd - this.viewPortStart + 1;
                const timeStep = Math.max(1, Math.floor(visibleCandles / 5));

                this.ctx.textAlign = 'center';
                for (let i = this.viewPortStart; i <= this.viewPortEnd; i += timeStep) {{
                    if (i >= this.data.length) break;

                    const x = this.candleToX(i) + this.candleWidth / 2;
                    const date = new Date(this.data[i].time);
                    const text = this.getTimeAxisLabel(date, '{self.current_interval}');

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

            // NEW: Method to update chart with live tick data
            updateLiveTick(tickData) {{
                if (this.data.length === 0) return;

                const lastCandle = this.data[this.data.length - 1];
                const lastCandleTime = new Date(lastCandle.time);
                const tickTime = new Date(tickData.time);

                let updated = false;

                // Simple logic for minute interval for demonstration
                // For other intervals, this logic would need to be more complex
                // based on the interval duration.
                if (this.currentInterval === 'minute' || this.currentInterval === '5minute' || this.currentInterval === '15minute') {{
                    // Check if tick falls within the current last candle's interval
                    // This is a simplified check. A proper implementation would check if
                    // tickTime is within the current candle's open-to-close interval.
                    // For live data, if the current tick is for the same minute as the last candle, update it.
                    // Otherwise, create a new candle.
                    const lastCandleMinute = new Date(lastCandle.time).getMinutes();
                    const tickMinute = new Date(tickData.time).getMinutes();
                    const lastCandleHour = new Date(lastCandle.time).getHours();
                    const tickHour = new Date(tickData.time).getHours();

                    if (lastCandleMinute === tickMinute && lastCandleHour === tickHour) {{
                        // Update existing last candle
                        lastCandle.close = tickData.close;
                        lastCandle.high = Math.max(lastCandle.high, tickData.close);
                        lastCandle.low = Math.min(lastCandle.low, tickData.close);
                        // For volume, you'd add to existing volume, not just set it
                        this.volumeData[this.volumeData.length - 1].value += tickData.volume; // Assuming tickData has volume too
                        updated = true;
                    }} else {{
                        // New candle, append it
                        this.data.push(tickData);
                        this.volumeData.push({{ time: tickData.time, value: tickData.volume }});
                        updated = true;
                    }}
                }} else if (this.currentInterval === 'day') {{
                    // For daily chart, typically you only update the current day's candle
                    // and ticks come in much faster than a day interval.
                    // So, you'd only update the last candle (today's candle)
                    const lastCandleDate = new Date(lastCandle.time).toDateString();
                    const tickDate = new Date(tickData.time).toDateString();

                    if (lastCandleDate === tickDate) {{
                        lastCandle.close = tickData.close;
                        lastCandle.high = Math.max(lastCandle.high, tickData.close);
                        lastCandle.low = Math.min(lastCandle.low, tickData.close);
                        this.volumeData[this.volumeData.length - 1].value += tickData.volume;
                        updated = true;
                    }} else if (tickTime > lastCandleTime) {{
                        // New day, append new candle
                        this.data.push(tickData);
                        this.volumeData.push({{ time: tickData.time, value: tickData.volume }});
                        updated = true;
                    }}
                }}

                if (updated) {{
                    this.calculateBounds(); // Recalculate bounds to adjust for new high/low prices or new candles
                    this.draw(); // Redraw the chart
                }}
            }}
        }}

        // Initialize chart
        const candlestickData = {candlestick_json};
        const volumeData = {volume_json};
        const currentInterval = '{self.current_interval}'; // Pass current interval to JS

        if (candlestickData.length > 0) {{
            const chart = new EnhancedCandlestickChart('mainCanvas', candlestickData, volumeData);
            chart.currentInterval = currentInterval; // Set the interval
            window.chart = chart; // Make chart accessible globally for Python calls
            window.autoScale = () => chart.autoScale();
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

    def _update_symbol_info(self, df: pd.DataFrame):
        """Update symbol information display"""
        try:
            if df.empty:
                return

            latest = df.iloc[-1]
            symbol = self.current_symbol

            # Set current_ltp
            self.current_ltp = float(latest['close']) if 'close' in latest else 0.0

            # Format price and change
            current_price = self.current_ltp
            if len(df) > 1:
                prev_price = df.iloc[-2]['close']
                change = current_price - prev_price
                change_pct = (change / prev_price) * 100
                change_str = f"{change:+.2f} ({change_pct:+.2f}%)"
            else:
                change_str = "N/A"

            # Update labels
            info_text = f"{symbol} • ₹{current_price:.2f}"
            self.symbol_info_label.setText(info_text)
            self.symbol_info_label.setToolTip(f"Change: {change_str}")

        except Exception as e:
            logger.error(f"Error updating symbol info: {e}")

    @Slot(list)
    def update_live_data(self, ticks: List[Dict]):
        """
        Receives live market data ticks and updates the chart.
        This method will be called from SwingTraderWindow's _on_market_data.
        """
        if not self.chart_view or self.current_state != ChartState.LOADED or self.last_df.empty:
            return

        for tick in ticks:
            # Check if the tick is for the currently displayed symbol
            if tick.get('instrument_token') == self.current_instrument_token:
                # Update current_ltp for the order button
                self.current_ltp = float(tick.get('last_price', 0.0))

                # Prepare tick data for JavaScript
                # Note: 'volume' might not be in every tick, especially for LTP-only ticks.
                # You might need to derive it or handle its absence.
                # For demonstration, we'll assume it exists or can be 0.
                tick_time = datetime.fromtimestamp(
                    tick.get('exchange_timestamp', datetime.now()).timestamp())  # Use exchange timestamp if available
                tick_data_for_js = {
                    'time': int(tick_time.timestamp() * 1000),
                    'open': float(tick.get('last_price', 0.0)),  # Simplified: treat LTP as open for live update
                    'high': float(tick.get('last_price', 0.0)),
                    'low': float(tick.get('last_price', 0.0)),
                    'close': float(tick.get('last_price', 0.0)),
                    'volume': float(tick.get('volume', 0.0))  # Volume might require aggregation
                }

                # Update the last candle in DataFrame as well (for consistency with historical data)
                # This is a simplified approach. A robust solution would involve
                # recreating/updating the last candle based on the current interval.
                if not self.last_df.empty:
                    last_row = self.last_df.iloc[-1]
                    # Check if the tick is for the same candle (based on interval)
                    # This logic needs to be precise for each interval.
                    # For a simple live price update, we just update the 'close' of the last candle.
                    # For full candle updates, you'd need to compare timestamps based on interval.

                    # Let's simplify and just pass the latest price to JS for instant update
                    # and let JS handle the candle aggregation/drawing.
                    # This assumes the JS chart has the logic to update its internal data structure.

                    js_update_code = f"if (window.chart) window.chart.updateLiveTick({json.dumps(tick_data_for_js)});"
                    self.chart_view.page().runJavaScript(js_update_code)

                    # Update the current LTP and symbol info label
                    self._update_symbol_info_from_tick(tick)

    def _update_symbol_info_from_tick(self, tick: Dict):
        """Update symbol information display based on a single tick."""
        if 'last_price' in tick:
            current_price = float(tick['last_price'])
            self.current_ltp = current_price

            # Get previous close for percentage change
            change_str = "N/A"
            if not self.last_df.empty and len(self.last_df) > 1:
                prev_price = self.last_df.iloc[-2]['close']  # Or use today's open for daily change
                if prev_price != 0:
                    change = current_price - prev_price
                    change_pct = (change / prev_price) * 100
                    change_str = f"{change:+.2f} ({change_pct:+.2f}%)"

            info_text = f"{self.current_symbol} • ₹{current_price:.2f}"
            self.symbol_info_label.setText(info_text)
            self.symbol_info_label.setToolTip(f"Change: {change_str}")

    @Slot()  # New slot for the order button
    def _on_order_button_clicked(self):
        """Emits the current symbol and LTP for placing an order."""
        if self.current_symbol and self.current_ltp > 0:
            logger.info(f"Order button clicked for {self.current_symbol} with LTP {self.current_ltp}")
            self.order_button_clicked.emit(self.current_symbol, self.current_ltp)
        elif self.current_symbol:
            QMessageBox.warning(self, "No LTP", f"LTP not available for {self.current_symbol}. Please try again later.")
        else:
            QMessageBox.warning(self, "No Symbol Selected", "Please select a symbol first to place an order.")

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
                background-color: #0a0a0a;
                color: #e0e0e0;
                font-family: "Segoe UI", Arial, sans-serif;
            }

            /* Toolbar */
            QFrame#chartToolbar {
                background-color: #1a1a1a;
                border: 1px solid #202020;
                border-radius: 0px;
                border-bottom: 1px solid #303030;
            }

            /* Symbol info label */
            #symbolInfoLabel {
                color: #a0c0ff;
                font-size: 13px;
                font-weight: 600;
            }

            /* Control buttons */
            #controlButton {
                background-color: #2a2a2a;
                color: #e0e0e0;
                border: 1px solid #303030;
                border-radius: 3px;
                font-size: 11px;
                font-weight: 500;
                padding: 4px 8px;
            }

            #controlButton:hover {
                background-color: #3a3a3a;
                border-color: #505050;
            }

            #controlButton:pressed {
                background-color: #1a1a1a;
                border-color: #404040;
            }

            /* Order Button */
            #orderButton {
                background-color: #6a9cff; /* Blue for order */
                color: #ffffff;
                border: 1px solid #6a9cff;
                border-radius: 3px;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 8px;
            }

            #orderButton:hover {
                background-color: #5a8cef;
                border-color: #5a8cef;
            }

            #orderButton:pressed {
                background-color: #4a7cdf;
                border-color: #4a7cdf;
            }

            #orderButton:disabled {
                background-color: #050505;
                color: #606060;
                border-color: #202020;
            }

            /* Timeframe buttons */
            #timeframeButton {
                background-color: #2a2a2a;
                color: #e0e0e0;
                border: 1px solid #303030;
                border-radius: 3px;
                font-size: 11px;
                font-weight: 500;
                padding: 4px 8px;
            }

            #timeframeButton:hover {
                background-color: #3a3a3a;
                color: #ffffff;
                border-color: #505050;
            }

            #timeframeButton:checked {
                background-color: #6a9cff;
                color: #ffffff;
                border-color: #6a9cff;
            }

            #timeframeButton:disabled {
                background-color: #050505;
                color: #606060;
                border-color: #202020;
            }

            #refreshButton {
                background-color: #2e8b57;
                color: #ffffff;
                border: 1px solid #2e8b57;
                border-radius: 3px;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 10px;
            }

            #refreshButton:hover {
                background-color: #246b43;
            }

            #refreshButton:pressed {
                background-color: #1e5a37;
            }

            #retryButton {
                background-color: #cc4444;
                color: #ffffff;
                border: 1px solid #cc4444;
                border-radius: 3px;
                font-size: 11px;
                font-weight: 600;
                padding: 8px 16px;
            }

            #retryButton:hover {
                background-color: #e04f5e;
            }

            /* Labels */
            #loadingLabel, #errorLabel {
                color: #a0c0ff;
                font-size: 14px;
                font-weight: bold;
                font-family: "Segoe UI", Arial, sans-serif;
            }

            #errorLabel {
                color: #f85149;
            }

            /* Progress bar */
            QProgressBar {
                background-color: #1a1a1a;
                border: none;
                border-radius: 1px;
                text-align: center;
                color: transparent;
            }

            QProgressBar::chunk {
                background-color: #6a9cff;
                border-radius: 1px;
            }

            /* Stacked widget */
            QStackedWidget {
                background-color: #0a0a0a;
                border: 1px solid #202020;
                border-radius: 0px;
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