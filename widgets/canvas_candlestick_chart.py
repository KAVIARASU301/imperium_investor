import logging
import json
import os
from datetime import datetime, timedelta
from enum import Enum
from random import random
from typing import List, Dict, Optional, Any

import pandas as pd
from PySide6.QtCore import Signal, Slot, QThread, Qt, QTimer, QObject
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                               QStackedWidget, QLabel, QPushButton, QProgressBar,
                               QFrame, QMessageBox, QColorDialog, QDialog,
                               QFormLayout, QSpinBox)
from PySide6.QtGui import QFont, QKeySequence, QShortcut, QColor
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel
from kiteconnect import KiteConnect
from cachetools import TTLCache
import threading

logger = logging.getLogger(__name__)


class ChartState(Enum):
    IDLE = "idle"
    LOADING = "loading"
    ERROR = "error"
    LOADED = "loaded"


class DrawingStorage:
    """Manages saving and loading of chart drawings, view state, and global settings"""

    def __init__(self, storage_dir: str = "user_data/chart_drawings"):
        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)
        self.global_settings_file = os.path.join(self.storage_dir, "global_chart_settings.json")

    def save_state(self, symbol: str, interval: str, state: Dict[str, Any]):
        """Save drawings and view state for a symbol and timeframe"""
        try:
            # Ensure the state structure is valid
            if not isinstance(state, dict):
                logger.warning(f"Invalid state type for {symbol}: {type(state)}")
                return

            # Validate drawings structure
            if "drawings" in state:
                drawings = state["drawings"]
                if not isinstance(drawings, dict):
                    logger.warning(f"Invalid drawings type for {symbol}: {type(drawings)}")
                    state["drawings"] = {"lines": [], "rectangles": [], "notes": []}
                else:
                    # Ensure all required drawing types exist
                    for draw_type in ["lines", "rectangles", "notes"]:
                        if draw_type not in drawings:
                            drawings[draw_type] = []
                        elif not isinstance(drawings[draw_type], list):
                            drawings[draw_type] = []

            filename = f"{symbol}_{interval}_state.json"
            filepath = os.path.join(self.storage_dir, filename)

            with open(filepath, 'w') as f:
                json.dump(state, f, indent=2)

            logger.info(
                f"Saved state for {symbol} ({interval}) - drawings: {self._count_drawings(state.get('drawings', {}))}")
        except Exception as e:
            logger.error(f"Failed to save state for {symbol}: {e}")

    def load_state(self, symbol: str, interval: str) -> Dict[str, Any]:
        """Load drawings and view state for a symbol and timeframe"""
        try:
            filename = f"{symbol}_{interval}_state.json"
            filepath = os.path.join(self.storage_dir, filename)

            if os.path.exists(filepath):
                with open(filepath, 'r') as f:
                    state = json.load(f)

                # Validate loaded state
                if not isinstance(state, dict):
                    logger.warning(f"Invalid state file for {symbol}, using defaults")
                    return self._get_default_state()

                # Ensure drawings structure is valid
                if "drawings" not in state:
                    state["drawings"] = {"lines": [], "rectangles": [], "notes": []}
                elif not isinstance(state["drawings"], dict):
                    state["drawings"] = {"lines": [], "rectangles": [], "notes": []}
                else:
                    # Ensure all required drawing types exist
                    for draw_type in ["lines", "rectangles", "notes"]:
                        if draw_type not in state["drawings"]:
                            state["drawings"][draw_type] = []
                        elif not isinstance(state["drawings"][draw_type], list):
                            state["drawings"][draw_type] = []

                # Ensure visible_candle_count exists
                if "visible_candle_count" not in state:
                    state["visible_candle_count"] = 100

                logger.info(
                    f"Loaded state for {symbol} ({interval}) - drawings: {self._count_drawings(state['drawings'])}")
                return state

            # Return default empty state
            return self._get_default_state()
        except Exception as e:
            logger.error(f"Failed to load state for {symbol}: {e}")
            return self._get_default_state()

    def _get_default_state(self) -> Dict[str, Any]:
        """Get default empty state"""
        return {
            "drawings": {"lines": [], "rectangles": [], "notes": []},
            "visible_candle_count": 100
        }

    def _count_drawings(self, drawings_data):
        """Count total drawings"""
        if not drawings_data or not isinstance(drawings_data, dict):
            return 0
        return sum(len(v) for k, v in drawings_data.items() if isinstance(v, list))

    def save_global_settings(self, settings: Dict[str, Any]):
        """Save global chart settings"""
        try:
            with open(self.global_settings_file, 'w') as f:
                json.dump(settings, f, indent=2)
            logger.info("Saved global chart settings.")
        except Exception as e:
            logger.error(f"Failed to save global chart settings: {e}")

    def load_global_settings(self) -> Dict[str, Any]:
        """Load global chart settings"""
        try:
            if os.path.exists(self.global_settings_file):
                with open(self.global_settings_file, 'r') as f:
                    settings = json.load(f)
                logger.info("Loaded global chart settings.")
                return settings
            # Default settings if file doesn't exist
            return {
                "candle_width": 4,
                "candle_spacing": 2,
                "default_visible_candles": 100,
                "up_candle_color": "#26a69a",
                "down_candle_color": "#ef5350"
            }
        except Exception as e:
            logger.error(f"Failed to load global chart settings: {e}")
            # Return default settings on error
            return {
                "candle_width": 4,
                "candle_spacing": 2,
                "default_visible_candles": 100,
                "up_candle_color": "#26a69a",
                "down_candle_color": "#ef5350"
            }


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


class ChartSettingsDialog(QDialog):
    """Dialog for adjusting chart display settings"""
    settings_changed = Signal(dict)

    def __init__(self, current_settings: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chart Settings")
        self.setFixedSize(300, 300)
        self.current_settings = current_settings
        self.color_buttons: Dict[str, QPushButton] = {}

        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        layout = QFormLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        # Candle Width
        self.candle_width_spinbox = QSpinBox()
        self.candle_width_spinbox.setRange(1, 10)
        self.candle_width_spinbox.setValue(self.current_settings.get("candle_width", 4))
        layout.addRow("Candle Width:", self.candle_width_spinbox)

        # Candle Spacing
        self.candle_spacing_spinbox = QSpinBox()
        self.candle_spacing_spinbox.setRange(0, 5)
        self.candle_spacing_spinbox.setValue(self.current_settings.get("candle_spacing", 2))
        layout.addRow("Candle Spacing:", self.candle_spacing_spinbox)

        # Default Visible Candles (Zoom Level)
        self.default_visible_candles_spinbox = QSpinBox()
        self.default_visible_candles_spinbox.setRange(50, 500)
        self.default_visible_candles_spinbox.setSingleStep(10)
        self.default_visible_candles_spinbox.setValue(self.current_settings.get("default_visible_candles", 100))
        layout.addRow("Default Visible Candles:", self.default_visible_candles_spinbox)

        # Up Candle Color
        up_color_layout = QHBoxLayout()
        self.up_candle_color_button = QPushButton("")
        self.up_candle_color_button.setFixedSize(30, 20)
        self.up_candle_color_button.setStyleSheet(
            f"background-color: {self.current_settings.get('up_candle_color', '#26a69a')}; border: 1px solid #555;")
        self.up_candle_color_button.clicked.connect(lambda: self._choose_color('up_candle_color'))
        up_color_layout.addWidget(self.up_candle_color_button)
        up_color_layout.addStretch()
        layout.addRow("Up Candle Color:", up_color_layout)
        self.color_buttons['up_candle_color'] = self.up_candle_color_button

        # Down Candle Color
        down_color_layout = QHBoxLayout()
        self.down_candle_color_button = QPushButton("")
        self.down_candle_color_button.setFixedSize(30, 20)
        self.down_candle_color_button.setStyleSheet(
            f"background-color: {self.current_settings.get('down_candle_color', '#ef5350')}; border: 1px solid #555;")
        self.down_candle_color_button.clicked.connect(lambda: self._choose_color('down_candle_color'))
        down_color_layout.addWidget(self.down_candle_color_button)
        down_color_layout.addStretch()
        layout.addRow("Down Candle Color:", down_color_layout)
        self.color_buttons['down_candle_color'] = self.down_candle_color_button

        # Apply and Cancel Buttons
        button_layout = QHBoxLayout()
        self.apply_button = QPushButton("Apply")
        self.apply_button.clicked.connect(self._apply_settings)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        button_layout.addStretch()
        button_layout.addWidget(self.apply_button)
        button_layout.addWidget(self.cancel_button)
        layout.addRow(button_layout)

    def _choose_color(self, setting_key: str):
        initial_color = QColor(self.current_settings.get(setting_key))
        color = QColorDialog.getColor(initial_color, self, f"Select {setting_key.replace('_', ' ').title()}")
        if color.isValid():
            self.current_settings[setting_key] = color.name()
            self.color_buttons[setting_key].setStyleSheet(f"background-color: {color.name()}; border: 1px solid #555;")

    def _apply_settings(self):
        new_settings = {
            "candle_width": self.candle_width_spinbox.value(),
            "candle_spacing": self.candle_spacing_spinbox.value(),
            "default_visible_candles": self.default_visible_candles_spinbox.value(),
            "up_candle_color": self.current_settings["up_candle_color"],
            "down_candle_color": self.current_settings["down_candle_color"]
        }
        self.settings_changed.emit(new_settings)
        self.accept()

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e1e;
                color: #e0e0e0;
                border: 1px solid #333333;
            }
            QLabel {
                color: #e0e0e0;
                font-size: 12px;
            }
            QSpinBox {
                background-color: #2a2a2a;
                color: #e0e0e0;
                border: 1px solid #404040;
                border-radius: 4px;
                padding: 2px;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 16px;
                border-left: 1px solid #404040;
                background-color: #3a3a3a;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background-color: #4a4a4a;
            }
            QPushButton {
                background-color: #0066cc;
                color: white;
                border: 1px solid #0066cc;
                border-radius: 4px;
                padding: 5px 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0080ff;
            }
            QPushButton:pressed {
                background-color: #0050a0;
            }
            QPushButton#colorButton { /* Specific style for color buttons */
                border: 1px solid #555;
            }
            QPushButton#colorButton:hover {
                 border: 1px solid #888;
            }
        """)


# New ChartBridge class for QtWebChannel communication
class ChartBridge(QObject):
    """
    Bridge to allow JavaScript in QWebEngineView to communicate with Python.
    """
    drawings_changed = Signal(str)  # Emits JSON string of drawings
    visible_candle_count_changed = Signal(int)  # Emits updated visible candle count
    chart_ready = Signal() # Signal emitted when JS chart is fully initialized and window.chart is set.

    def __init__(self, parent=None):
        super().__init__(parent)
        self.webChannelInitialized = False # <--- ADDED THIS LINE

    @Slot(str)
    def notify_drawings_changed(self, drawings_json: str):
        """Receives drawing data as a JSON string from JavaScript."""
        try:
            # Validate JSON before emitting
            json.loads(drawings_json)
            self.drawings_changed.emit(drawings_json)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON received from JavaScript: {e}")
        except Exception as e:
            logger.error(f"Error in notify_drawings_changed: {e}")

    @Slot(int)
    def notify_visible_candle_count_changed(self, count: int):
        """Receives visible candle count from JavaScript."""
        try:
            if isinstance(count, (int, float)) and count > 0:
                self.visible_candle_count_changed.emit(int(count))
            else:
                logger.warning(f"Invalid candle count received: {count}")
        except Exception as e:
            logger.error(f"Error in notify_visible_candle_count_changed: {e}")

    @Slot()
    def set_web_channel_initialized(self):
        """Called by JavaScript to confirm WebChannel is fully set up."""
        self.webChannelInitialized = True
        logger.debug("Python ChartBridge.webChannelInitialized set to True and chart_ready emitted.")
        self.chart_ready.emit() # This emits the signal that CandlestickChart listens to


class CandlestickChart(QWidget):
    """Professional candlestick chart with fixed drawing persistence"""

    order_button_clicked = Signal(str, float)

    def __init__(self, kite_client: KiteConnect, parent=None):
        super().__init__(parent)

        # Core components
        self.data_fetcher = DataFetcher(kite_client)
        self.data_cache = DataCache(maxsize=50, ttl=300)
        self.drawing_storage = DrawingStorage()

        # Load global chart settings
        self.global_chart_settings = self.drawing_storage.load_global_settings()

        # State management
        self.instrument_map: Dict[str, Dict[str, Any]] = {}
        self.current_state = ChartState.IDLE
        self.data_loader_thread: Optional[ChartDataLoaderThread] = None
        self.last_df: Optional[pd.DataFrame] = None
        self.current_symbol: str = ""
        self.current_interval: str = "day"
        self.current_ltp: float = 0.0
        self.current_instrument_token: int = 0
        # Use default visible candles from settings
        self.current_visible_candle_count: int = self.global_chart_settings["default_visible_candles"]

        # Chart rendering properties (will be passed to JS)
        self._current_candle_width: int = self.global_chart_settings["candle_width"]
        self._current_candle_spacing: int = self.global_chart_settings["candle_spacing"]
        self._current_up_color: str = self.global_chart_settings["up_candle_color"]
        self._current_down_color: str = self.global_chart_settings["down_candle_color"]

        # SMA data storage
        self.sma_data = {
            'sma10': [],
            'sma20': [],
            'sma50': []
        }
        self.current_adr: Dict[str, float] = {"value": 0.0, "percent": 0.0} # Changed to dict for value and percent
        self.percentage_changes: Dict[str, float] = {} # Store percentage changes

        # Drawing state
        self.current_drawing_color = "#FFD700"
        self.current_line_width = 2

        # QtWebChannel setup
        # Important: Pass self as parent to ChartBridge so it can access _delete_selected_drawing
        self.chart_bridge = ChartBridge(parent=self)
        self.chart_bridge.drawings_changed.connect(self._on_drawings_changed_from_js)
        self.chart_bridge.visible_candle_count_changed.connect(self._on_zoom_changed_from_js)
        # We connect _apply_saved_drawings_and_zoom to chart_ready signal (emitted by set_web_channel_initialized)
        self.chart_bridge.chart_ready.connect(self._on_js_chart_fully_ready)


        # UI components
        self.chart_view: Optional[QWebEngineView] = None
        self.channel: Optional[QWebChannel] = None
        self.timeframe_buttons: Dict[str, QPushButton] = {}
        self.drawing_buttons: Dict[str, QPushButton] = {}
        self.auto_scale_btn: Optional[QPushButton] = None
        self.refresh_button: Optional[QPushButton] = None
        self.settings_btn: Optional[QPushButton] = None
        self.order_btn: Optional[QPushButton] = None
        self.color_btn: Optional[QPushButton] = None
        self.line_width_btn: Optional[QPushButton] = None
        self.save_drawings_btn: Optional[QPushButton] = None
        self.clear_drawings_btn: Optional[QPushButton] = None


        self._setup_ui()
        self._apply_styles()
        self._setup_keyboard_shortcuts()

        # Initialize chart after UI is ready
        QTimer.singleShot(100, self._initialize_chart)

    @Slot()
    def _on_js_chart_fully_ready(self):
        """This slot is now connected to chart_ready signal emitted by ChartBridge.set_web_channel_initialized."""
        logger.info("JavaScript chart object reported ready via chart_ready signal.")
        # Now it is safe to apply saved drawings and zoom
        self._apply_saved_drawings_and_zoom()


    def _setup_ui(self):
        """Setup the main UI layout"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Combined toolbar (formerly main_toolbar + drawing_toolbar)
        self.combined_toolbar = QFrame()
        self.combined_toolbar.setObjectName("chartToolbar")
        self.combined_toolbar.setFixedHeight(40)

        toolbar_layout = QHBoxLayout(self.combined_toolbar)
        toolbar_layout.setContentsMargins(10, 5, 10, 5)
        toolbar_layout.setSpacing(8)

        # Symbol info
        self.symbol_info_label = QLabel("No Symbol Selected")
        self.symbol_info_label.setObjectName("symbolInfoLabel")
        font = QFont()
        font.setBold(True)
        self.symbol_info_label.setFont(font)
        toolbar_layout.addWidget(self.symbol_info_label)

        toolbar_layout.addStretch()

        # Order button
        self.order_btn = QPushButton("Order")
        self.order_btn.setObjectName("orderButton")
        self.order_btn.setFixedSize(70, 30)
        self.order_btn.clicked.connect(self._on_order_button_clicked)
        toolbar_layout.addWidget(self.order_btn)

        # Auto Scale button (now "A" and compact)
        self.auto_scale_btn = QPushButton("A")
        self.auto_scale_btn.setObjectName("controlButton")
        self.auto_scale_btn.setFixedSize(30, 30)
        self.auto_scale_btn.setToolTip("Auto Scale (Ctrl+A)")
        self.auto_scale_btn.clicked.connect(self._auto_scale_chart)
        toolbar_layout.addWidget(self.auto_scale_btn)

        # Refresh button
        self.refresh_button = QPushButton("⟳")
        self.refresh_button.setObjectName("refreshButton")
        self.refresh_button.setFixedSize(30, 30)
        self.refresh_button.setToolTip("Refresh Data (F5)")
        self.refresh_button.clicked.connect(self._force_refresh)
        toolbar_layout.addWidget(self.refresh_button)

        # Settings button
        self.settings_btn = QPushButton("⚙️")
        self.settings_btn.setObjectName("controlButton")
        self.settings_btn.setFixedSize(30, 30)
        self.settings_btn.setToolTip("Chart Settings")
        self.settings_btn.clicked.connect(self._open_settings_dialog)
        toolbar_layout.addWidget(self.settings_btn)

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
            toolbar_layout.addWidget(btn)

        # Set default timeframe
        self.timeframe_buttons["day"].setChecked(True)

        # Separator for drawing tools
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        separator.setStyleSheet("color: #333333;")
        toolbar_layout.addWidget(separator)

        # Drawing tools - using text-based emojis and compact sizes
        tools = [
            ("\\", "line", "Trend Line"),
            ("📏", "measure", "Measuring Tool"),
            ("🔲", "rectangle", "Rectangle"),
            ("✍️", "note", "Text Note"),
            ("🎨", "color_picker", "Change drawing color"),
            ("━", "line_width", "Change line width"),
            ("💾", "save_drawings", "Save drawings (Ctrl+S)"),
            ("🗑", "clear_drawings", "Clear all drawings (Delete)"),
        ]

        for icon, tool_id, tooltip in tools:
            btn = QPushButton(icon)
            btn.setObjectName("drawingToolButton")
            btn.setCheckable(True)
            btn.setFixedSize(30, 28)
            btn.setToolTip(tooltip)
            if tool_id == "color_picker":
                btn.clicked.connect(self._choose_drawing_color)
                btn.setCheckable(False)
                self.color_btn = btn
            elif tool_id == "line_width":
                btn.clicked.connect(self._toggle_line_width)
                btn.setCheckable(False)
                self.line_width_btn = btn
            elif tool_id == "save_drawings":
                btn.clicked.connect(self._save_drawings)
                btn.setCheckable(False)
                self.save_drawings_btn = btn
            elif tool_id == "clear_drawings":
                btn.clicked.connect(self._clear_drawings)
                btn.setCheckable(False)
                self.clear_drawings_btn = btn
            else:
                btn.clicked.connect(lambda checked, t=tool_id: self._toggle_drawing_tool(t, checked))
                self.drawing_buttons[tool_id] = btn
            toolbar_layout.addWidget(btn)

        main_layout.addWidget(self.combined_toolbar)

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

    def _toggle_drawing_tool(self, tool_id: str, checked: bool):
        """Toggle drawing tool mode"""
        if self.chart_view and self.current_state == ChartState.LOADED:
            if checked:
                for other_tool, btn in self.drawing_buttons.items():
                    if other_tool != tool_id:
                        btn.setChecked(False)

            js_code = f"""
            if (window.chart) {{
                window.chart.setDrawingTool('{tool_id}', {str(checked).lower()},
                    '{self.current_drawing_color}', {self.current_line_width});
            }}
            """
            self.chart_view.page().runJavaScript(js_code)

    def _choose_drawing_color(self):
        """Open color picker for drawing tools"""
        color = QColorDialog.getColor(QColor(self.current_drawing_color), self, "Choose Drawing Color")
        if color.isValid():
            self.current_drawing_color = color.name()
            self.color_btn.setStyleSheet(f"background-color: {self.current_drawing_color};")

            if self.chart_view:
                js_code = f"if (window.chart) window.chart.updateDrawingStyle('{self.current_drawing_color}', {self.current_line_width});"
                self.chart_view.page().runJavaScript(js_code)

    def _toggle_line_width(self):
        """Toggle between different line widths"""
        widths = [1, 2, 3, 4]
        current_index = widths.index(self.current_line_width) if self.current_line_width in widths else 0
        self.current_line_width = widths[(current_index + 1) % len(widths)]

        width_symbols = {1: "─", 2: "━", 3: "▬", 4: "█"}
        self.line_width_btn.setText(width_symbols.get(self.current_line_width, "─"))

        if self.chart_view:
            js_code = f"if (window.chart) window.chart.updateDrawingStyle('{self.current_drawing_color}', {self.current_line_width});"
            self.chart_view.page().runJavaScript(js_code)

    def _save_drawings(self):
        """Manually trigger a save of current drawings and zoom level to storage."""
        if not self.chart_view or not self.current_symbol:
            logger.warning("Attempted manual save without a chart view or current symbol.")
            return

        js_code = """
        (function() {
            if (window.chart && window.chart.getAllDrawings && window.chart.getVisibleCandleCount) {
                return {
                    drawings: window.chart.getAllDrawings(),
                    visible_candle_count: window.chart.getVisibleCandleCount()
                };
            }
            return null;
        })();
        """

        def save_callback(state_data):
            if state_data and self.current_symbol:
                self.drawing_storage.save_state(self.current_symbol, self.current_interval, state_data)
                logger.info(
                    f"Manual save: {self.drawing_storage._count_drawings(state_data.get('drawings', {}))} drawings and zoom ({state_data.get('visible_candle_count')}) for {self.current_symbol}")
                original_text = self.save_drawings_btn.text()
                self.save_drawings_btn.setText("✓")
                QTimer.singleShot(1000, lambda: self.save_drawings_btn.setText(original_text))
            else:
                logger.warning("Manual save callback received no data or no current symbol.")

        self.chart_view.page().runJavaScript(js_code, save_callback)

    @Slot(str)
    def _on_drawings_changed_from_js(self, drawings_json: str):
        if self.current_symbol and self.current_state == ChartState.LOADED:
            try:
                drawings_data = json.loads(drawings_json)
                current_state = self.drawing_storage.load_state(self.current_symbol, self.current_interval)

                if current_state.get("drawings") == drawings_data:
                    return

                current_state["drawings"] = drawings_data
                self.drawing_storage.save_state(self.current_symbol, self.current_interval, current_state)
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding drawings JSON from JS: {e}")
            except Exception as e:
                logger.error(f"Error saving drawings from JS callback: {e}")

    @Slot(int)
    def _on_zoom_changed_from_js(self, visible_candle_count: int):
        """Slot to receive updated visible candle count from JavaScript via WebChannel and save it."""
        if self.current_symbol and self.current_state == ChartState.LOADED:
            self.current_visible_candle_count = visible_candle_count
            try:
                current_state = self.drawing_storage.load_state(self.current_symbol, self.current_interval)
                current_state["visible_candle_count"] = visible_candle_count
                self.drawing_storage.save_state(self.current_symbol, self.current_interval, current_state)
            except Exception as e:
                logger.error(f"Error saving zoom from JS callback: {e}")

    def _clear_drawings(self):
        """Clear all drawings from chart and storage"""
        if self.chart_view:
            js_code = "if (window.chart) window.chart.clearAllDrawings();"
            self.chart_view.page().runJavaScript(js_code)

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
        # Refresh shortcut
        refresh_shortcut = QShortcut(QKeySequence("F5"), self)
        refresh_shortcut.activated.connect(self._force_refresh)

        # Auto scale shortcut
        auto_scale_shortcut = QShortcut(QKeySequence("Ctrl+A"), self)
        auto_scale_shortcut.activated.connect(self._auto_scale_chart)

        # Drawing tool shortcuts
        shortcuts = {
            "L": "line",
            "M": "measure",
            "R": "rectangle",
            "T": "note"
        }

        for key, tool in shortcuts.items():
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(lambda t=tool: self._activate_drawing_tool_shortcut(t))

        # Save/Clear shortcuts
        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.activated.connect(self._save_drawings)

        # Delete selected drawing shortcut (NEW)
        delete_shortcut = QShortcut(QKeySequence("Delete"), self)
        delete_shortcut.activated.connect(self._delete_selected_drawing)

    def _activate_drawing_tool_shortcut(self, tool):
        """Handle drawing tool shortcut activation"""
        try:
            if not hasattr(self, 'current_drawing_tool'):
                self.current_drawing_tool = None

            # Toggle the tool if it's already active, otherwise activate it
            if self.current_drawing_tool == tool:
                # Deactivate current tool
                self.current_drawing_tool = None
                self._deactivate_all_drawing_tools()
            else:
                # Activate the new tool
                self.current_drawing_tool = tool
                self._activate_drawing_tool(tool)

        except Exception as e:
            logger.error(f"Error activating drawing tool shortcut for {tool}: {e}")

    def _activate_drawing_tool(self, tool):
        """Activate a specific drawing tool"""
        try:
            # Default drawing settings
            color = '#FFD700'  # Gold color
            line_width = 2

            # Deactivate all other tools first
            self._deactivate_all_drawing_tools()

            # Activate the selected tool
            if hasattr(self, 'web_view') and self.web_view:
                script = f"""
                if (window.chart && window.chart.setDrawingTool) {{
                    window.chart.setDrawingTool('{tool}', true, '{color}', {line_width});
                    console.log('Activated drawing tool: {tool}');
                }}
                """
                self.web_view.page().runJavaScript(script)

            logger.info(f"Activated drawing tool: {tool}")

        except Exception as e:
            logger.error(f"Error activating drawing tool {tool}: {e}")

    def _deactivate_all_drawing_tools(self):
        """Deactivate all drawing tools"""
        try:
            if hasattr(self, 'web_view') and self.web_view:
                script = """
                if (window.chart && window.chart.setDrawingTool) {
                    window.chart.setDrawingTool(null, false);
                    console.log('Deactivated all drawing tools');
                }
                """
                self.web_view.page().runJavaScript(script)

        except Exception as e:
            logger.error(f"Error deactivating drawing tools: {e}")

    @Slot() # NEW SLOT
    def _delete_selected_drawing(self):
        """Requests JavaScript chart to delete the currently selected drawing."""
        if self.chart_view and self.current_state == ChartState.LOADED:
            js_code = "if (window.chart) window.chart.deleteSelectedDrawing();"
            self.chart_view.page().runJavaScript(js_code)
            logger.info("Requested deletion of selected drawing from JS.")
        else:
            logger.warning("Cannot delete drawing: chart not loaded.")

    def _initialize_chart(self):
        """Initialize chart after UI is ready"""
        self._create_chart_view()

    def _create_chart_view(self):
        """Create the web engine view for the chart with proper WebChannel setup"""
        try:
            if self.chart_view:
                self.chart_layout.removeWidget(self.chart_view)
                self.chart_view.deleteLater()
                self.chart_view = None

            if self.channel:
                self.channel.deleteLater()
                self.channel = None

            self.chart_view = QWebEngineView()
            self.chart_layout.addWidget(self.chart_view)

            self.channel = QWebChannel(self.chart_view.page())
            self.channel.registerObject("chartBridge", self.chart_bridge)
            self.chart_view.page().setWebChannel(self.channel)

            logger.info("QWebChannel and ChartBridge exposed to JavaScript.")

        except Exception as e:
            logger.error(f"Failed to create chart view or setup WebChannel: {e}")
            self._set_state(ChartState.ERROR)

    def _set_state(self, state: ChartState):
        """Update UI state"""
        self.current_state = state

        state_configs = {
            ChartState.IDLE: {'widget_index': 2, 'buttons_enabled': True},
            ChartState.LOADING: {'widget_index': 0, 'buttons_enabled': False},
            ChartState.ERROR: {'widget_index': 1, 'buttons_enabled': True},
            ChartState.LOADED: {'widget_index': 2, 'buttons_enabled': True}
        }

        config = state_configs.get(state, state_configs[ChartState.IDLE])

        if self.stacked_widget.currentIndex() != config['widget_index']:
            self.stacked_widget.setCurrentIndex(config['widget_index'])

        for btn in self.timeframe_buttons.values():
            btn.setEnabled(config['buttons_enabled'])

        for btn_id, btn in self.drawing_buttons.items():
            btn.setEnabled(config['buttons_enabled'] and self.current_symbol != "")
            if state != ChartState.LOADED:
                btn.setChecked(False)

        if self.color_btn: self.color_btn.setEnabled(config['buttons_enabled'] and self.current_symbol != "")
        if self.line_width_btn: self.line_width_btn.setEnabled(config['buttons_enabled'] and self.current_symbol != "")
        if self.save_drawings_btn: self.save_drawings_btn.setEnabled(
            config['buttons_enabled'] and self.current_symbol != "")
        if self.clear_drawings_btn: self.clear_drawings_btn.setEnabled(
            config['buttons_enabled'] and self.current_symbol != "")

        if self.refresh_button: self.refresh_button.setEnabled(config['buttons_enabled'])
        if self.auto_scale_btn: self.auto_scale_btn.setEnabled(config['buttons_enabled'])
        if self.settings_btn: self.settings_btn.setEnabled(config['buttons_enabled'])

        if self.order_btn:
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

        # Before switching symbols, save current state
        if self.current_symbol and self.chart_view:
            self._save_current_state_sync()

        # Stop any running operations
        self._stop_current_operations()

        # Clear active drawing tools
        if self.chart_view:
            js_code = "if (window.chart) window.chart.setDrawingTool(null, false);"
            self.chart_view.page().runJavaScript(js_code)
            for btn in self.drawing_buttons.values():
                btn.setChecked(False)

        # Update configuration
        self.current_symbol = symbol
        self.current_instrument_token = self.instrument_map[symbol]['instrument_token']

        # Load saved state for the new symbol/interval
        saved_state = self.drawing_storage.load_state(self.current_symbol, self.current_interval)
        # Use the loaded 'visible_candle_count' for the new symbol
        self.current_visible_candle_count = saved_state.get("visible_candle_count",
                                                            self.global_chart_settings["default_visible_candles"])

        self._set_state(ChartState.IDLE)
        self._load_chart_data()

    def _save_current_state_sync(self):
        """Save current chart state synchronously before major context switches"""
        if not self.chart_view or not self.current_symbol:
            return

        # Request current state
        js_code = """
        (function() {
            if (window.chart && window.chart.getAllDrawings && window.chart.getVisibleCandleCount) {
                return {
                    drawings: window.chart.getAllDrawings(),
                    visible_candle_count: window.chart.getVisibleCandleCount()
                };
            }
            return null;
        })();
        """

        def sync_save_callback(state_data):
            if state_data and self.current_symbol:
                self.drawing_storage.save_state(self.current_symbol, self.current_interval, state_data)
                logger.info(f"Sync save completed for {self.current_symbol}")

        self.chart_view.page().runJavaScript(js_code, sync_save_callback)

    def _load_chart_data(self, force_refresh: bool = False):
        """Load chart data"""
        if not self.current_symbol or self.current_symbol not in self.instrument_map:
            return

        # Clear cache if force refresh
        if force_refresh:
            cache_key = f"{self.current_symbol}_{self.current_interval}"
            self.data_cache._cache.pop(cache_key, None)

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
            self._calculate_metrics(self.last_df) # Changed to _calculate_metrics
            self._render_chart(df)

            # Update UI
            self._update_symbol_info(df)
            self._set_state(ChartState.LOADED)

            logger.info(f"Chart loaded: {self.current_symbol} ({len(df)} candles)")

        except Exception as e:
            logger.error(f"Error processing loaded data: {e}")
            self._show_error(f"Failed to render chart: {str(e)}")

    def _calculate_metrics(self, df: pd.DataFrame):
        """Calculates SMAs, ADR, and various percentage changes."""
        if 'close' not in df.columns or df.empty:
            self.sma_data = {'sma10': [], 'sma20': [], 'sma50': []}
            self.current_adr = {"value": 0.0, "percent": 0.0}
            self.percentage_changes = {} # Initialize percentage changes
            return

        df['time_ms'] = df['time'].apply(lambda x: int(x.timestamp() * 1000))

        # Calculate SMAs
        df['sma10'] = df['close'].rolling(window=10).mean()
        df['sma20'] = df['close'].rolling(window=20).mean()
        df['sma50'] = df['close'].rolling(window=50).mean()

        # Calculate Daily Range (High - Low)
        df['daily_range'] = df['high'] - df['low']

        # Calculate Average Daily Range (ADR) over 14 periods, as typically used
        adr_period = 14
        if len(df) >= adr_period:
            current_adr_value = df['daily_range'].iloc[-adr_period:].mean()
            # Calculate ADR % based on the last close price
            last_close = df['close'].iloc[-1] if not df.empty else 0
            current_adr_percent = (current_adr_value / last_close) * 100 if last_close != 0 else 0
            self.current_adr = {"value": float(current_adr_value), "percent": float(current_adr_percent)}
        else:
            self.current_adr = {"value": 0.0, "percent": 0.0}

        # Calculate historical percentage changes
        self.percentage_changes = {}
        last_close_price = df['close'].iloc[-1] if not df.empty else 0

        # Define lookback periods in days (approximate for daily data)
        # Assuming df is daily data for simplicity in calculating 'days_ago'
        # For intraday data, this would need adjustment based on candles.
        periods = {
            "Weekly": 5,   # Approx 5 trading days for a week
            "Monthly": 22, # Approx 22 trading days for a month
            "3M": 66,      # Approx 3 * 22 trading days
            "6M": 132,     # Approx 6 * 22 trading days
            "1Y": 252      # Approx 12 * 21 trading days
        }

        for label, days_back in periods.items():
            if len(df) > days_back:
                past_close_price = df['close'].iloc[-1 - days_back]
                change_percent = ((last_close_price - past_close_price) / past_close_price) * 100 if past_close_price != 0 else 0
                self.percentage_changes[label] = float(change_percent)
            else:
                self.percentage_changes[label] = 0.0

        # Convert to list of dicts for JavaScript
        self.sma_data['sma10'] = df[['time_ms', 'sma10']].dropna().rename(columns={'time_ms': 'time', 'sma10': 'value'}).to_dict(orient='records')
        self.sma_data['sma20'] = df[['time_ms', 'sma20']].dropna().rename(columns={'time_ms': 'time', 'sma20': 'value'}).to_dict(orient='records')
        self.sma_data['sma50'] = df[['time_ms', 'sma50']].dropna().rename(columns={'time_ms': 'time', 'sma50': 'value'}).to_dict(orient='records')

        logger.debug(f"Calculated SMAs, ADR ({self.current_adr['value']:.2f}, {self.current_adr['percent']:.2f}%) and percentage changes.")


    @Slot() # Mark as slot as it's connected to a signal
    def _apply_saved_drawings_and_zoom(self):
        """Apply saved drawings and zoom after chart is fully loaded and JS bridge is ready."""
        try:
            saved_state = self.drawing_storage.load_state(self.current_symbol, self.current_interval)
            drawings = saved_state.get("drawings", {"lines": [], "rectangles": [], "notes": []})
            initial_zoom = saved_state.get("visible_candle_count",
                                           self.global_chart_settings["default_visible_candles"])

            # Ensure we only apply if chart is loaded and symbol is active
            if self.current_state == ChartState.LOADED and self.current_symbol and self.chart_view and self.chart_bridge.webChannelInitialized: # Corrected check
                # Now, the initial drawings and zoom are passed directly in _create_fixed_chart_html
                # This function is just for confirming and logging success
                logger.info(
                    f"Applied {self.drawing_storage._count_drawings(drawings)} saved drawings and set zoom to {initial_zoom} for {self.current_symbol}")
            else:
                logger.warning(f"Skipping _apply_saved_drawings_and_zoom: Chart not fully ready or no symbol. State: {self.current_state}, Symbol: {self.current_symbol}, JS Bridge ready: {getattr(self.chart_bridge, 'webChannelInitialized', False)}")
        except Exception as e:
            logger.error(f"Error applying saved drawings and zoom: {e}")

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
            self.data_loader_thread.quit()
            self.data_loader_thread.wait(3000)
            if self.data_loader_thread.isRunning():
                logger.warning("ChartDataLoaderThread is still running after wait, terminating forcefully.")
                self.data_loader_thread.terminate()
            self.data_loader_thread.deleteLater()
            self.data_loader_thread = None

    @Slot(object)
    def update_live_data(self, live_data: Any):
        """Update chart with live market data"""
        if isinstance(live_data, list):
            for item in live_data:
                if isinstance(item, dict):
                    self._process_single_live_data_item(item)
                else:
                    logger.warning(f"Skipping malformed live_data item (not a dict in list): {item}")
        elif isinstance(live_data, dict):
            self._process_single_live_data_item(live_data)
        else:
            logger.error(f"Received malformed live_data (not a dict or list of dicts): {live_data}")
            return

    def _process_single_live_data_item(self, data_item: Dict[str, Any]):
        """Helper to process a single live data dictionary."""
        if self.current_state == ChartState.LOADED and self.current_symbol:
            trading_symbol = data_item.get('tradingsymbol')
            last_price = data_item.get('last_price')
            instrument_token = data_item.get('instrument_token')

            if trading_symbol and last_price is not None and instrument_token == self.current_instrument_token:
                self.current_ltp = float(last_price)
                self._update_symbol_info_live(self.current_ltp)

                if self.chart_view:
                    js_code = f"""
                    if (window.chart) {{
                        window.chart.updateLivePrice({self.current_ltp});
                    }}
                    """
                    self.chart_view.page().runJavaScript(js_code)

    def _update_symbol_info_live(self, ltp: float):
        """Update symbol information display with live LTP"""
        try:
            symbol = self.current_symbol
            info_text = f"{symbol} • ₹{ltp:.2f}"
            self.symbol_info_label.setText(info_text)
        except Exception as e:
            logger.error(f"Error updating live symbol info: {e}")

    def _render_chart(self, df: pd.DataFrame):
        """Render chart using HTML5 Canvas with fixed drawing persistence"""
        try:
            if not self.chart_view:
                self._create_chart_view()

            # Prepare candlestick data
            candlestick_data = []
            volume_data = []

            for _, row in df.iterrows():
                timestamp = int(row['time'].timestamp() * 1000)

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

            # Retrieve saved drawings and zoom for current symbol/interval
            saved_state = self.drawing_storage.load_state(self.current_symbol, self.current_interval)
            initial_drawings_json = json.dumps(saved_state.get("drawings", {"lines": [], "rectangles": [], "notes": []}))
            initial_zoom = saved_state.get("visible_candle_count", self.global_chart_settings["default_visible_candles"])

            html_content = self._create_fixed_chart_html(
                candlestick_data,
                volume_data,
                initial_zoom, # Pass initial zoom directly to JS constructor
                self._current_candle_width,
                self._current_candle_spacing,
                self._current_up_color,
                self._current_down_color,
                self.sma_data,
                self.current_adr,
                self.percentage_changes,
                self.current_interval, # Pass current interval
                initial_drawings_json # Pass initial drawings directly to JS constructor
            )

            self.chart_view.setHtml(html_content)
            logger.info(f"Chart rendered successfully for {self.current_symbol}")

        except Exception as e:
            logger.error(f"Chart rendering error: {e}")
            self._show_error(f"Failed to render chart: {str(e)}")

    def _create_fixed_chart_html(self, candlestick_data, volume_data,
                                 initial_visible_candle_count, initial_candle_width,
                                 initial_candle_spacing, up_candle_color, down_candle_color,
                                 sma_data: Dict[str, List[Dict]],
                                 current_adr: Dict[str, float],
                                 percentage_changes: Dict[str, float],
                                 current_interval: str,
                                 initial_drawings_json: str):
        """Create HTML content with fixed drawing persistence and proper coordinate handling"""

        candlestick_json = json.dumps(candlestick_data)
        volume_json = json.dumps(volume_data)
        sma_json = json.dumps(sma_data)
        adr_json = json.dumps(current_adr)
        percentage_changes_json = json.dumps(percentage_changes)
        current_interval_js = json.dumps(current_interval)

        # Ensure initial_drawings_json is properly formatted
        if isinstance(initial_drawings_json, str):
            # If it's already a JSON string, use it as is
            safe_initial_drawings = initial_drawings_json
        else:
            # If it's an object, stringify it
            safe_initial_drawings = json.dumps(initial_drawings_json)

        # Validate the JSON string
        try:
            json.loads(safe_initial_drawings)
        except (json.JSONDecodeError, TypeError):
            # If invalid, provide a default empty structure
            safe_initial_drawings = json.dumps({"lines": [], "rectangles": [], "notes": []})

        qwebchannel_script_src = "qrc:///qtwebchannel/qwebchannel.js"

        html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Professional Trading Chart</title>
        <style>
            body {{
                margin: 0;
                padding: 0;
                background-color: #0a0a0a;
                font-family: 'Segoe UI', sans-serif;
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
                width: 100%;
                height: calc(100% - 15px);
                position: absolute;
                top: 0;
                left: 0;
            }}
            #info {{
                position: absolute;
                top: 5px;
                left: 5px;
                color: #e0e0e0;
                font-size: 12px;
                pointer-events: none;
                z-index: 5;
            }}
            #metricsInfo {{
                font-weight: bold;
                margin-bottom: 5px;
                color: #e0e0e0;
            }}
            #priceInfo {{
                color: #00bfff;
                font-weight: bold;
            }}
            #timeSlider {{
                position: absolute;
                bottom: 0;
                left: 0;
                width: 100%;
                height: 15px;
                background-color: #1a1a1a;
                border-top: 1px solid #333;
                display: flex;
                align-items: center;
                justify-content: center;
                overflow: hidden;
                user-select: none;
                z-index: 10;
            }}
            #sliderTrack {{
                position: relative;
                height: 3px;
                background-color: #333;
                border-radius: 1.5px;
                width: calc(100% - 20px);
                margin: 0 10px;
            }}
            #sliderThumb {{
                position: absolute;
                width: 50px;
                height: 10px;
                background-color: #0066cc;
                border: 1px solid #0080ff;
                border-radius: 2px;
                cursor: grab;
                display: flex;
                align-items: center;
                justify-content: center;
                color: transparent;
                font-size: 0;
                z-index: 12;
            }}
        </style>
    </head>
    <body>
        <div id="chartContainer">
            <canvas id="mainCanvas"></canvas>
            <div id="info">
                <div id="metricsInfo"></div>
                <div id="priceInfo"></div>
            </div>
            <div id="timeSlider">
                <div id="sliderTrack">
                    <div id="sliderThumb"></div>
                </div>
            </div>
        </div>

        <script src="{qwebchannel_script_src}"></script>
        <script>
            class FixedTradingChart {{
                constructor(canvasId, data, volumeData, initialVisibleCandleCount,
                            initialCandleWidth, initialCandleSpacing, upCandleColor, downCandleColor,
                            smaData, initialADR, percentageChanges, currentInterval, initialDrawingsJson) {{

                    // Basic setup
                    this.canvas = document.getElementById(canvasId);
                    this.ctx = this.canvas.getContext('2d');
                    this.data = data || [];
                    this.volumeData = volumeData || [];
                    this.width = 0;
                    this.height = 0;
                    this.padding = {{ top: 30, right: 80, bottom: 30, left: 10 }};
                    this.rightBufferCandles = 5;

                    // Price and volume bounds
                    this.minPrice = 0;
                    this.maxPrice = 0;
                    this.minVolume = 0;
                    this.maxVolume = 0;

                    // Chart display settings
                    this.candleWidth = initialCandleWidth || 4;
                    this.candleSpacing = initialCandleSpacing || 2;
                    this.visibleCandleCount = initialVisibleCandleCount || 100;

                    // Viewport settings
                    this.viewPortEnd = Math.max(0, this.data.length - 1 + this.rightBufferCandles);
                    this.viewPortStart = Math.max(0, this.viewPortEnd - this.visibleCandleCount);

                    // Drawing tools
                    this.currentTool = null;
                    this.isDrawing = false;
                    this.startPoint = null;
                    this.endPoint = null;
                    this.drawingColor = '#FFD700';
                    this.lineWidth = 2;

                    // Initialize drawings safely
                    this.drawings = this.initializeDrawings(initialDrawingsJson);
                    this.selectedDrawingId = null;

                    // Interaction state
                    this.isDragging = false;
                    this.lastMouseX = 0;
                    this.lastMouseY = 0;
                    this.crosshairX = null;
                    this.crosshairY = null;
                    this.livePrice = null;
                    this.isUserZooming = false; // Track user-initiated zoom changes

                    // Colors
                    this.colors = {{
                        upCandle: upCandleColor || '#26a69a',
                        downCandle: downCandleColor || '#ef5350',
                        grid: '#1a1a1a',
                        text: '#e0e0e0',
                        volume: '#555',
                        volumeUp: 'rgba(38, 166, 154, 0.3)',
                        volumeDown: 'rgba(239, 83, 80, 0.3)',
                        background: '#0a0a0a',
                        crosshair: 'rgba(160, 192, 255, 0.4)',
                        livePrice: '#00BFFF'
                    }};

                    // Market data
                    this.smaData = smaData || {{}};
                    this.currentADR = initialADR || {{}};
                    this.percentageChanges = percentageChanges || {{}};
                    this.currentInterval = currentInterval || 'day';

                    // Slider state
                    this.isSliderDragging = false;
                    this.sliderLastX = 0;

                    // WebChannel state
                    this.chartBridge = null;
                    this.webChannelInitialized = false;
                    this.isLoadingState = false;
                    this.notificationQueue = [];
                    this.notificationTimer = null;

                    this.init();
                }}

                initializeDrawings(initialDrawingsJson) {{
                    const defaultDrawings = {{ lines: [], rectangles: [], notes: [] }};

                    if (!initialDrawingsJson) {{
                        console.log('No initial drawings provided, using default');
                        return defaultDrawings;
                    }}

                    try {{
                        let drawings;
                        if (typeof initialDrawingsJson === 'string') {{
                            drawings = JSON.parse(initialDrawingsJson);
                        }} else {{
                            drawings = initialDrawingsJson;
                        }}

                        // Validate structure
                        if (drawings && typeof drawings === 'object') {{
                            return {{
                                lines: Array.isArray(drawings.lines) ? drawings.lines : [],
                                rectangles: Array.isArray(drawings.rectangles) ? drawings.rectangles : [],
                                notes: Array.isArray(drawings.notes) ? drawings.notes : []
                            }};
                        }}
                    }} catch (error) {{
                        console.error('Error parsing initial drawings:', error);
                    }}

                    console.log('Using default drawings structure');
                    return defaultDrawings;
                }}

                async init() {{
                    try {{
                        this.setupCanvas();
                        this.setupSlider();
                        this.calculateBounds();
                        this.setupEventListeners();
                        this.setupWebChannel();

                        this.draw();
                        this.updateSlider();
                        this.displayLatestCandleDetails();
                        this.updateMetricsDisplay();

                        console.log('Chart initialized successfully with', this.data.length, 'candles');
                    }} catch (error) {{
                        console.error('Error initializing chart:', error);
                    }}
                }}

                setupWebChannel() {{
                    const initWebChannel = () => {{
                        try {{
                            if (typeof QWebChannel !== 'undefined' && window.qt && window.qt.webChannelTransport) {{
                                new QWebChannel(qt.webChannelTransport, (channel) => {{
                                    if (channel.objects && channel.objects.chartBridge) {{
                                        this.chartBridge = channel.objects.chartBridge;
                                        this.webChannelInitialized = true;
                                        console.log("QWebChannel ChartBridge loaded successfully");

                                        // Use setTimeout to avoid callback timing issues
                                        setTimeout(() => {{
                                            try {{
                                                if (this.chartBridge && typeof this.chartBridge.set_web_channel_initialized === 'function') {{
                                                    this.chartBridge.set_web_channel_initialized();
                                                }}
                                            }} catch (callbackError) {{
                                                console.warn("Error calling set_web_channel_initialized:", callbackError);
                                            }}
                                        }}, 100);

                                        this.processNotificationQueue();
                                    }} else {{
                                        console.warn("ChartBridge not found in channel objects");
                                        setTimeout(initWebChannel, 200);
                                    }}
                                }});
                            }} else {{
                                setTimeout(initWebChannel, 100);
                            }}
                        }} catch (error) {{
                            console.error("Error setting up WebChannel:", error);
                            setTimeout(initWebChannel, 500);
                        }}
                    }};

                    initWebChannel();
                    if (document.readyState === 'loading') {{
                        document.addEventListener('DOMContentLoaded', initWebChannel);
                    }} else {{
                        initWebChannel();
                    }}
                }}

                queueNotification(type, data) {{
                    this.notificationQueue.push({{ type, data, timestamp: Date.now() }});

                    if (this.notificationTimer) {{
                        clearTimeout(this.notificationTimer);
                    }}

                    this.notificationTimer = setTimeout(() => {{
                        this.processNotificationQueue();
                    }}, 100);
                }}

                processNotificationQueue() {{
                    if (!this.webChannelInitialized || this.isLoadingState || this.notificationQueue.length === 0) {{
                        return;
                    }}

                    const latestNotifications = new Map();
                    this.notificationQueue.forEach(notification => {{
                        latestNotifications.set(notification.type, notification);
                    }});

                    latestNotifications.forEach((notification, type) => {{
                        try {{
                            if (type === 'drawings' && this.chartBridge && typeof this.chartBridge.notify_drawings_changed === 'function') {{
                                // Add delay to prevent callback conflicts
                                setTimeout(() => {{
                                    try {{
                                        this.chartBridge.notify_drawings_changed(JSON.stringify(notification.data));
                                    }} catch (callbackError) {{
                                        console.warn("Error in drawings callback:", callbackError);
                                    }}
                                }}, 50);
                            }} else if (type === 'zoom' && this.chartBridge && typeof this.chartBridge.notify_visible_candle_count_changed === 'function') {{
                                // Only notify zoom changes if they are significant (user-initiated)
                                if (this.isUserZooming) {{
                                    // Update global settings
                                    if (this.updateGlobalSettings) {{
                                        this.updateGlobalSettings(notification.data);
                                    }}

                                    setTimeout(() => {{
                                        try {{
                                            this.chartBridge.notify_visible_candle_count_changed(notification.data);
                                        }} catch (callbackError) {{
                                            console.warn("Error in zoom callback:", callbackError);
                                        }}
                                    }}, 50);
                                }}
                            }}
                        }} catch (error) {{
                            console.error(`Error processing ${{type}} notification:`, error);
                        }}
                    }});

                    this.notificationQueue = [];
                }}

                notifyDrawingsChange() {{
                    if (!this.isLoadingState) {{
                        this.queueNotification('drawings', this.drawings);
                    }}
                }}

                notifyZoomChange() {{
                    // Only notify if this is a user-initiated change and we're not loading state
                    if (!this.isLoadingState && this.isUserZooming) {{
                        this.queueNotification('zoom', this.visibleCandleCount);
                    }}
                }}

                loadDrawings(drawingsData) {{
                    if (this.isLoadingState) {{
                        console.warn('Already loading state, skipping...');
                        return;
                    }}

                    try {{
                        this.isLoadingState = true;
                        this.isUserZooming = false; // Ensure no zoom notifications during load

                        if (drawingsData && typeof drawingsData === 'object') {{
                            this.drawings.lines = Array.isArray(drawingsData.lines) ? drawingsData.lines : [];
                            this.drawings.rectangles = Array.isArray(drawingsData.rectangles) ? drawingsData.rectangles : [];
                            this.drawings.notes = Array.isArray(drawingsData.notes) ? drawingsData.notes : [];

                            this.draw();
                            console.log("Drawings loaded successfully:", this.drawings);
                        }} else {{
                            console.warn("Invalid drawings data provided for loadDrawings.");
                        }}
                    }} catch (error) {{
                        console.error("Error loading drawings:", error);
                    }} finally {{
                        // Reset loading state after a delay to ensure all operations complete
                        setTimeout(() => {{
                            this.isLoadingState = false;
                        }}, 100);
                    }}
                }}

                // Canvas setup and event handlers
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

                    const sliderHeight = this.slider ? this.slider.clientHeight : 15;
                    const spacingBetweenCharts = 10;
                    const totalAvailablePlottingHeight = this.height - this.padding.top - this.padding.bottom - sliderHeight;
                    const volumeChartRatio = 0.15;

                    this.chartArea = {{
                        x: this.padding.left,
                        y: this.padding.top,
                        width: this.width - this.padding.left - this.padding.right,
                        height: Math.max(50, totalAvailablePlottingHeight * (1 - volumeChartRatio) - spacingBetweenCharts)
                    }};

                    this.volumeArea = {{
                        x: this.padding.left,
                        y: this.chartArea.y + this.chartArea.height + spacingBetweenCharts,
                        width: this.chartArea.width,
                        height: Math.max(10, totalAvailablePlottingHeight * volumeChartRatio)
                    }};

                    this.calculateBounds();
                    this.draw();
                    setTimeout(() => this.updateSlider(), 100);
                }}

                setupEventListeners() {{
                    this.canvas.addEventListener('mousedown', (e) => this.handleMouseDown(e));
                    this.canvas.addEventListener('mousemove', (e) => this.handleMouseMove(e));
                    this.canvas.addEventListener('mouseup', (e) => this.handleMouseUp(e));
                    this.canvas.addEventListener('mouseleave', (e) => this.handleMouseLeave(e));
                    this.canvas.addEventListener('wheel', (e) => this.handleWheel(e));
                    this.canvas.addEventListener('dblclick', (e) => this.handleDoubleClick(e));
                }}

                handleMouseDown(e) {{
                    const mousePos = this.getMousePosition(e);

                    if (this.currentTool) {{
                        this.startDrawing(mousePos);
                    }} else if (e.button === 0) {{
                        const clickedDrawingId = this.getDrawingAtPoint(mousePos);
                        if (clickedDrawingId) {{
                            this.selectedDrawingId = clickedDrawingId;
                            this.draw();
                            console.log("Drawing selected:", clickedDrawingId);
                        }} else {{
                            this.selectedDrawingId = null;
                            this.isDragging = true;
                            this.lastMouseX = e.clientX;
                            this.lastMouseY = e.clientY;
                            this.canvas.style.cursor = 'grabbing';
                        }}
                        this.draw();
                    }}
                }}

                handleMouseMove(e) {{
                    if (this.isDragging && !this.currentTool) {{
                        this.handleChartDrag(e);
                        this.draw();
                        return;
                    }}

                    if (this.isDrawing && this.startPoint) {{
                        this.endPoint = this.getMousePosition(e);
                        this.draw();
                        this.drawTemporaryDrawing();
                        return;
                    }}

                    this.updateCrosshair(e);
                }}

                handleMouseUp(e) {{
                    if (this.isDrawing && this.startPoint && this.endPoint) {{
                        this.finishDrawing();
                    }} else if (this.isDragging) {{
                        this.isDragging = false;
                        this.canvas.style.cursor = this.currentTool ? 'crosshair' : 'default';
                        this.draw();
                    }}
                }}

                handleMouseLeave(e) {{
                    this.isDragging = false;
                    this.isDrawing = false;
                    this.crosshairX = null;
                    this.crosshairY = null;
                    this.displayLatestCandleDetails();
                    this.updateMetricsDisplay();
                    this.canvas.style.cursor = this.currentTool ? 'crosshair' : 'default';
                    this.draw();
                }}

                // Drawing methods
                startDrawing(mousePos) {{
                    this.isDrawing = true;
                    this.startPoint = {{
                        x: mousePos.x,
                        y: mousePos.y,
                        time: this.xToTime(mousePos.x),
                        price: this.yToPrice(mousePos.y)
                    }};
                    this.endPoint = null;
                }}

                finishDrawing() {{
                    if (!this.startPoint || !this.endPoint) return;

                    const drawing = {{
                        id: Date.now() + Math.random(),
                        type: this.currentTool,
                        startTime: this.startPoint.time,
                        startPrice: this.startPoint.price,
                        endTime: this.xToTime(this.endPoint.x),
                        endPrice: this.yToPrice(this.endPoint.y),
                        color: this.drawingColor,
                        lineWidth: this.lineWidth,
                        timestamp: Date.now()
                    }};

                    if (this.currentTool === 'line') {{
                        this.drawings.lines.push(drawing);
                    }} else if (this.currentTool === 'rectangle') {{
                        this.drawings.rectangles.push(drawing);
                    }}

                    this.isDrawing = false;
                    this.startPoint = null;
                    this.endPoint = null;
                    this.draw();
                    this.notifyDrawingsChange();
                }}

                // Utility methods
                getMousePosition(e) {{
                    const rect = this.canvas.getBoundingClientRect();
                    return {{
                        x: e.clientX - rect.left,
                        y: e.clientY - rect.top
                    }};
                }}

                calculateBounds() {{
                    if (this.data.length === 0) return;

                    const actualVisibleData = this.data.slice(this.viewPortStart, Math.min(this.data.length, this.viewPortEnd + 1));

                    if (actualVisibleData.length === 0) {{
                        this.minPrice = 0;
                        this.maxPrice = 0;
                        this.minVolume = 0;
                        this.maxVolume = 0;
                        return;
                    }}

                    this.minPrice = Math.min(...actualVisibleData.map(d => d.low));
                    this.maxPrice = Math.max(...actualVisibleData.map(d => d.high));

                    // Include SMA data in bounds calculation
                    Object.values(this.smaData).forEach(smaList => {{
                        smaList.forEach(item => {{
                            const itemTime = item.time;
                            const firstVisibleTime = this.data[this.viewPortStart]?.time;
                            const lastVisibleTime = this.data[Math.min(this.data.length - 1, this.viewPortEnd)]?.time;

                            if (firstVisibleTime !== undefined && lastVisibleTime !== undefined &&
                                itemTime >= firstVisibleTime && itemTime <= lastVisibleTime) {{
                                this.minPrice = Math.min(this.minPrice, item.value);
                                this.maxPrice = Math.max(this.maxPrice, item.value);
                            }}
                        }});
                    }});

                    const priceRange = this.maxPrice - this.minPrice;
                    if (priceRange === 0) {{
                        this.minPrice -= this.minPrice * 0.1 || 1;
                        this.maxPrice += this.maxPrice * 0.1 || 1;
                    }} else {{
                        this.minPrice -= priceRange * 0.05;
                        this.maxPrice += priceRange * 0.05;
                    }}

                    if (this.livePrice !== null) {{
                        this.minPrice = Math.min(this.minPrice, this.livePrice - (priceRange * 0.1));
                        this.maxPrice = Math.max(this.maxPrice, this.livePrice + (priceRange * 0.1));
                    }}

                    this.minVolume = 0;
                    this.maxVolume = Math.max(...this.volumeData.slice(this.viewPortStart, Math.min(this.volumeData.length, this.viewPortEnd + 1)).map(d => d.value));
                    if (this.maxVolume === 0) this.maxVolume = 1;
                }}

                draw() {{
                    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
                    this.ctx.fillStyle = this.colors.background;
                    this.ctx.fillRect(0, 0, this.width, this.height);

                    if (this.data.length === 0) {{
                        this.ctx.fillStyle = this.colors.text;
                        this.ctx.font = '16px Arial';
                        this.ctx.textAlign = 'center';
                        this.ctx.fillText('No data available', this.width / 2, this.height / 2);
                        return;
                    }}

                    this.drawGrid();
                    this.drawVolume();
                    this.drawSMABands();
                    this.drawCandlesticks();
                    this.drawAxes();
                    this.drawAllDrawings();
                    this.drawCrosshair();
                    this.drawCurrentPriceRay();
                }}

                drawGrid() {{
                    this.ctx.strokeStyle = this.colors.grid;
                    this.ctx.lineWidth = 1;

                    const priceRange = this.maxPrice - this.minPrice;
                    if (priceRange <= 0) return;

                    const priceStep = priceRange / 8;
                    for (let i = 0; i <= 8; i++) {{
                        const price = this.minPrice + (priceStep * i);
                        const y = this.priceToY(price);

                        this.ctx.beginPath();
                        this.ctx.moveTo(this.chartArea.x, y);
                        this.ctx.lineTo(this.chartArea.x + this.chartArea.width, y);
                        this.ctx.stroke();
                    }}
                }}

                drawCandlesticks() {{
                    const visibleCandlesIncludingBuffer = this.viewPortEnd - this.viewPortStart + 1;
                    if (visibleCandlesIncludingBuffer <= 0) return;

                    const totalWidth = this.chartArea.width;
                    const candleSpace = totalWidth / visibleCandlesIncludingBuffer;
                    this.candleWidth = Math.max(1, candleSpace - this.candleSpacing);

                    for (let i = this.viewPortStart; i < this.data.length && i <= this.viewPortEnd; i++) {{
                        if (i < 0) continue;

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
                        if (bodyHeight < 1) {{
                            this.ctx.beginPath();
                            this.ctx.moveTo(x, openY);
                            this.ctx.lineTo(x + this.candleWidth, openY);
                            this.ctx.stroke();
                        }} else {{
                            this.ctx.fillRect(x, Math.min(openY, closeY), this.candleWidth, bodyHeight);
                        }}
                    }}
                }}

                drawVolume() {{
                    const visibleCandlesIncludingBuffer = this.viewPortEnd - this.viewPortStart + 1;
                    if (visibleCandlesIncludingBuffer <= 0) return;

                    for (let i = this.viewPortStart; i < this.volumeData.length && i <= this.viewPortEnd; i++) {{
                        if (i < 0) continue;

                        const volume = this.volumeData[i];
                        const candle = this.data[i];
                        const x = this.candleToX(i);

                        const height = (this.maxVolume > 0) ? (volume.value / this.maxVolume) * this.volumeArea.height : 0;
                        const isUp = candle.close >= candle.open;

                        this.ctx.fillStyle = isUp ? this.colors.volumeUp : this.colors.volumeDown;
                        this.ctx.fillRect(x, this.volumeArea.y + this.volumeArea.height - height, this.candleWidth, height);
                    }}
                }}

                drawSMABands() {{
                    this.ctx.setLineDash([]);

                    const smaColors = {{
                        'sma10': '#FFD700',
                        'sma20': '#00BFFF',
                        'sma50': '#FF00FF'
                    }};

                    const smaLineWidth = 1.5;

                    for (const smaKey in this.smaData) {{
                        const smaList = this.smaData[smaKey];
                        if (smaList.length === 0) continue;

                        this.ctx.strokeStyle = smaColors[smaKey] || '#FFFFFF';
                        this.ctx.lineWidth = smaLineWidth;
                        this.ctx.beginPath();

                        let firstPoint = true;
                        for (let i = 0; i < smaList.length; i++) {{
                            const item = smaList[i];
                            const x = this.timeToX(item.time);
                            const y = this.priceToY(item.value);

                            if (x >= this.chartArea.x && x <= (this.chartArea.x + this.chartArea.width) &&
                                y >= this.chartArea.y && y <= (this.chartArea.y + this.chartArea.height)) {{
                                if (firstPoint) {{
                                    this.ctx.moveTo(x, y);
                                    firstPoint = false;
                                }} else {{
                                    this.ctx.lineTo(x, y);
                                }}
                            }} else if (!firstPoint) {{
                                break;
                            }}
                        }}
                        this.ctx.stroke();
                    }}
                }}

                drawAxes() {{
                    this.ctx.fillStyle = this.colors.text;
                    this.ctx.font = '11px monospace';
                    this.ctx.textAlign = 'left';

                    const priceRange = this.maxPrice - this.minPrice;
                    if (priceRange <= 0) return;

                    // Price labels
                    const priceStep = priceRange / 8;
                    for (let i = 0; i <= 8; i++) {{
                        const price = this.minPrice + (priceStep * i);
                        const y = this.priceToY(price);
                        this.ctx.fillText('₹' + price.toFixed(2), this.chartArea.x + this.chartArea.width + 4, y + 4);
                    }}

                    // Volume labels
                    this.ctx.fillText('Vol', this.volumeArea.x + this.volumeArea.width + 4, this.volumeArea.y + 12);
                    this.ctx.fillText(this.formatVolume(this.maxVolume), this.volumeArea.x + this.volumeArea.width + 4, this.volumeArea.y + 24);

                    // Time labels
                    const visibleCandlesIncludingBuffer = this.viewPortEnd - this.viewPortStart + 1;
                    const timeStep = Math.max(1, Math.floor(visibleCandlesIncludingBuffer / 6));
                    this.ctx.textAlign = 'center';

                    for (let i = this.viewPortStart; i < this.data.length; i += timeStep) {{
                        if (i < 0) continue;

                        const x = this.candleToX(i) + this.candleWidth / 2;
                        const date = new Date(this.data[i].time);
                        const text = this.formatTimeLabel(date);

                        this.ctx.fillText(text, x, this.volumeArea.y + this.volumeArea.height + 20);
                    }}
                    this.ctx.textAlign = 'left';
                }}

                drawAllDrawings() {{
                    // Draw lines
                    this.drawings.lines.forEach(line => {{
                        const startX = this.timeToX(line.startTime);
                        const startY = this.priceToY(line.startPrice);
                        const endX = this.timeToX(line.endTime);
                        const endY = this.priceToY(line.endPrice);

                        if (this.isLineVisible(startX, startY, endX, endY)) {{
                            this.ctx.strokeStyle = line.color;
                            this.ctx.lineWidth = line.lineWidth;
                            this.ctx.setLineDash([]);

                            if (this.selectedDrawingId === line.id) {{
                                this.ctx.strokeStyle = '#FFFF00';
                                this.ctx.lineWidth = line.lineWidth + 2;
                            }}

                            this.ctx.beginPath();
                            this.ctx.moveTo(startX, startY);
                            this.ctx.lineTo(endX, endY);
                            this.ctx.stroke();
                        }}
                    }});

                    // Draw rectangles
                    this.drawings.rectangles.forEach(rect => {{
                        const startX = this.timeToX(rect.startTime);
                        const startY = this.priceToY(rect.startPrice);
                        const endX = this.timeToX(rect.endTime);
                        const endY = this.priceToY(rect.endPrice);

                        const x = Math.min(startX, endX);
                        const y = Math.min(startY, endY);
                        const width = Math.abs(endX - startX);
                        const height = Math.abs(endY - startY);

                        if (this.isRectVisible(x, y, width, height)) {{
                            this.ctx.strokeStyle = rect.color;
                            this.ctx.lineWidth = rect.lineWidth;
                            this.ctx.setLineDash([]);

                            this.ctx.fillStyle = rect.color + '20';
                            if (this.selectedDrawingId === rect.id) {{
                                this.ctx.strokeStyle = '#FFFF00';
                                this.ctx.lineWidth = rect.lineWidth + 2;
                                this.ctx.fillStyle = this.ctx.strokeStyle + '30';
                            }}

                            this.ctx.fillRect(x, y, width, height);
                            this.ctx.strokeRect(x, y, width, height);
                        }}
                    }});

                    // Draw notes
                    this.drawings.notes.forEach(note => {{
                        const x = this.timeToX(note.time);
                        const y = this.priceToY(note.price);

                        if (this.isPointVisible(x, y)) {{
                            this.ctx.font = 'bold 12px Arial';
                            const textMetrics = this.ctx.measureText(note.text);

                            this.ctx.fillStyle = 'rgba(0, 0, 0, 0.8)';
                            if (this.selectedDrawingId === note.id) {{
                                this.ctx.fillStyle = 'rgba(255, 255, 0, 0.8)';
                            }}
                            this.ctx.fillRect(x - 2, y - 16, textMetrics.width + 4, 18);

                            this.ctx.fillStyle = note.color;
                            if (this.selectedDrawingId === note.id) {{
                                this.ctx.fillStyle = '#000000';
                            }}
                            this.ctx.fillText(note.text, x, y);
                        }}
                    }});
                }}

                drawTemporaryDrawing() {{
                    if (!this.isDrawing || !this.startPoint || !this.endPoint) return;

                    this.ctx.strokeStyle = this.drawingColor;
                    this.ctx.lineWidth = this.lineWidth;
                    this.ctx.setLineDash([3, 3]);

                    if (this.currentTool === 'line') {{
                        this.ctx.beginPath();
                        this.ctx.moveTo(this.startPoint.x, this.startPoint.y);
                        this.ctx.lineTo(this.endPoint.x, this.endPoint.y);
                        this.ctx.stroke();
                    }} else if (this.currentTool === 'rectangle') {{
                        const width = this.endPoint.x - this.startPoint.x;
                        const height = this.endPoint.y - this.startPoint.y;
                        this.ctx.strokeRect(this.startPoint.x, this.startPoint.y, width, height);
                    }}

                    this.ctx.setLineDash([]);
                }}

                drawCrosshair() {{
                    if (this.currentTool === null && this.crosshairX !== null && this.crosshairY !== null) {{
                        this.ctx.strokeStyle = this.colors.crosshair;
                        this.ctx.lineWidth = 1;
                        this.ctx.setLineDash([5, 5]);

                        this.ctx.beginPath();
                        this.ctx.moveTo(this.crosshairX, this.chartArea.y);
                        this.ctx.lineTo(this.crosshairX, this.volumeArea.y + this.volumeArea.height);
                        this.ctx.stroke();

                        if (this.crosshairY >= this.chartArea.y && this.crosshairY <= this.chartArea.y + this.chartArea.height) {{
                            this.ctx.beginPath();
                            this.ctx.moveTo(this.chartArea.x, this.crosshairY);
                            this.ctx.lineTo(this.chartArea.x + this.chartArea.width, this.crosshairY);
                            this.ctx.stroke();

                            const priceAtCrosshair = this.yToPrice(this.crosshairY);
                            const priceText = '₹' + priceAtCrosshair.toFixed(2);
                            this.ctx.font = 'bold 12px monospace';
                            const textMetrics = this.ctx.measureText(priceText);
                            const textWidth = textMetrics.width;
                            const textHeight = 12;

                            const rectX = this.chartArea.x + this.chartArea.width;
                            const rectY = this.crosshairY - textHeight / 2 - 2;
                            const rectWidth = textWidth + 10;
                            const rectHeight = textHeight + 4;

                            this.ctx.fillStyle = this.colors.crosshair;
                            this.ctx.fillRect(rectX, rectY, rectWidth, rectHeight);

                            this.ctx.fillStyle = 'white';
                            this.ctx.fillText(priceText, rectX + 5, this.crosshairY + textHeight / 2 - 2);
                        }}

                        this.ctx.setLineDash([]);
                    }}
                }}

                drawCurrentPriceRay() {{
                    if (this.livePrice !== null && this.currentTool === null) {{
                        const y = this.priceToY(this.livePrice);
                        const lastCandleIndex = this.data.length - 1;
                        let rayStartX = this.chartArea.x;

                        if (lastCandleIndex >= this.viewPortStart && lastCandleIndex <= this.viewPortEnd) {{
                            rayStartX = this.candleToX(lastCandleIndex) + this.candleWidth / 2;
                        }} else if (lastCandleIndex < this.viewPortStart) {{
                            rayStartX = this.chartArea.x;
                        }} else {{
                            rayStartX = this.chartArea.x + this.chartArea.width;
                        }}

                        this.ctx.strokeStyle = this.colors.livePrice;
                        this.ctx.lineWidth = 2;
                        this.ctx.setLineDash([2, 2]);
                        this.ctx.beginPath();
                        this.ctx.moveTo(rayStartX, y);
                        this.ctx.lineTo(this.chartArea.x + this.chartArea.width, y);
                        this.ctx.stroke();
                        this.ctx.setLineDash([]);

                        const priceText = '₹' + this.livePrice.toFixed(2);
                        this.ctx.font = 'bold 12px monospace';
                        const textMetrics = this.ctx.measureText(priceText);
                        const textWidth = textMetrics.width;
                        const textHeight = 12;

                        const rectX = this.chartArea.x + this.chartArea.width;
                        const rectY = y - textHeight / 2 - 2;
                        const rectWidth = textWidth + 10;
                        const rectHeight = textHeight + 4;

                        this.ctx.fillStyle = this.colors.livePrice;
                        this.ctx.fillRect(rectX, rectY, rectWidth, rectHeight);

                        this.ctx.fillStyle = 'white';
                        this.ctx.fillText(priceText, rectX + 5, y + textHeight / 2 - 2);
                    }}
                }}

                // Chart interaction methods
                handleChartDrag(e) {{
                    const deltaX = e.clientX - this.lastMouseX;
                    const deltaY = e.clientY - this.lastMouseY;

                    const visibleCandles = this.viewPortEnd - this.viewPortStart + 1;
                    const totalCandleSlots = this.data.length + this.rightBufferCandles;
                    const pixelsPerCandleSlot = this.chartArea.width / visibleCandles;
                    const candleShift = -Math.round(deltaX / pixelsPerCandleSlot);

                    let newViewPortStart = Math.max(0, Math.min(this.viewPortStart + candleShift, totalCandleSlots - visibleCandles));

                    if (this.viewPortStart !== newViewPortStart) {{
                        this.viewPortStart = newViewPortStart;
                        this.viewPortEnd = this.viewPortStart + visibleCandles - 1;
                    }}

                    const priceRange = this.maxPrice - this.minPrice;
                    const pricePerPixel = priceRange / this.chartArea.height;
                    const priceDelta = -deltaY * pricePerPixel;

                    this.minPrice += priceDelta;
                    this.maxPrice += priceDelta;

                    this.lastMouseX = e.clientX;
                    this.lastMouseY = e.clientY;

                    this.calculateBounds();
                    this.updateSlider();
                }}

                handleWheel(e) {{
                    e.preventDefault();

                    const chartMouseY = e.clientY - this.canvas.getBoundingClientRect().top;
                    const chartMouseX = e.clientX - this.canvas.getBoundingClientRect().left;
                    let zoomChanged = false;

                    // Mark as user-initiated zoom
                    this.isUserZooming = true;

                    if (e.ctrlKey || e.metaKey) {{
                        const zoomFactor = e.deltaY > 0 ? 1.1 : 0.9;

                        if (chartMouseY >= this.chartArea.y && chartMouseY <= this.chartArea.y + this.chartArea.height) {{
                            const priceAtMouse = this.yToPrice(chartMouseY);
                            const currentRange = this.maxPrice - this.minPrice;
                            const newRange = currentRange * zoomFactor;

                            this.minPrice = priceAtMouse - (newRange * ((priceAtMouse - this.minPrice) / currentRange));
                            this.maxPrice = priceAtMouse + (newRange * ((this.maxPrice - priceAtMouse) / currentRange));
                        }}
                    }} else if (e.shiftKey) {{
                        const priceRange = this.maxPrice - this.minPrice;
                        const panAmount = (e.deltaY > 0 ? 1 : -1) * priceRange * 0.05;
                        this.minPrice += panAmount;
                        this.maxPrice += panAmount;
                    }} else {{
                        const zoomFactor = e.deltaY > 0 ? 1.1 : 0.9;
                        const currentVisibleCount = this.visibleCandleCount;
                        const maxPossibleVisibleCount = this.data.length + this.rightBufferCandles;
                        let newVisibleCandleCount = Math.round(currentVisibleCount * zoomFactor);

                        newVisibleCandleCount = Math.max(20, Math.min(maxPossibleVisibleCount, newVisibleCandleCount));

                        if (newVisibleCandleCount !== currentVisibleCount) {{
                            const dataCandleIndex = this.xToCandle(chartMouseX);
                            let newViewPortStart = Math.round(dataCandleIndex - (newVisibleCandleCount * ((chartMouseX - this.chartArea.x) / this.chartArea.width)));

                            newViewPortStart = Math.max(0, Math.min(newViewPortStart, maxPossibleVisibleCount - newVisibleCandleCount));

                            this.viewPortStart = newViewPortStart;
                            this.viewPortEnd = this.viewPortStart + newVisibleCandleCount - 1;
                            this.visibleCandleCount = newVisibleCandleCount;
                            zoomChanged = true;
                        }}
                    }}

                    this.calculateBounds();
                    this.draw();
                    this.updateSlider();

                    if (zoomChanged) {{
                        // Delay the notification to allow zoom to settle
                        setTimeout(() => {{
                            this.notifyZoomChange();
                            this.isUserZooming = false;
                        }}, 200);
                    }} else {{
                        this.isUserZooming = false;
                    }}
                }}

                handleDoubleClick(e) {{
                    if (this.currentTool === 'note') {{
                        const mousePos = this.getMousePosition(e);
                        this.addTextNote(mousePos);
                    }}
                }}

                addTextNote(mousePos) {{
                    const text = prompt('Enter note text:');
                    if (text) {{
                        const note = {{
                            id: Date.now() + Math.random(),
                            type: 'note',
                            time: this.xToTime(mousePos.x),
                            price: this.yToPrice(mousePos.y),
                            text: text,
                            color: this.drawingColor,
                            timestamp: Date.now()
                        }};

                        this.drawings.notes.push(note);
                        this.draw();
                        this.notifyDrawingsChange();
                    }}
                }}

                // Display methods
                updateMetricsDisplay() {{
                    const metricsInfoEl = document.getElementById('metricsInfo');
                    if (!metricsInfoEl) return;

                    let adrText = '';
                    if (this.currentADR && this.currentADR.value > 0) {{
                        adrText = `ADR: ₹${{this.currentADR.value.toFixed(2)}} (${{this.currentADR.percent.toFixed(2)}}%)`;
                    }} else {{
                        adrText = 'ADR: N/A';
                    }}

                    let changesText = [];
                    const periods = ["Weekly", "Monthly", "3M", "6M", "1Y"];

                    periods.forEach(period => {{
                        if (this.percentageChanges.hasOwnProperty(period)) {{
                            const change = this.percentageChanges[period];
                            const color = change >= 0 ? 'color: #00b894;' : 'color: #d63031;';
                            changesText.push(`<span style="${{color}}">${{period}}: ${{change.toFixed(2)}}%</span>`);
                        }} else {{
                            changesText.push(`<span style="color: #e0e0e0;">${{period}}: N/A</span>`);
                        }}
                    }});

                    metricsInfoEl.innerHTML = `${{adrText}} | ${{changesText.join(' | ')}}`;
                }}

                displayLatestCandleDetails() {{
                    const priceInfoEl = document.getElementById('priceInfo');
                    if (!priceInfoEl) return;

                    if (this.crosshairX !== null) {{
                        return;
                    }}

                    if (this.data.length > 0) {{
                        const latestCandle = this.data[this.data.length - 1];
                        const date = new Date(latestCandle.time);
                        const dateStr = date.toLocaleDateString('en-GB', {{ day: '2-digit', month: 'short', year: 'numeric' }});
                        const change = latestCandle.close - latestCandle.open;
                        const changePercent = (latestCandle.open !== 0) ? ((change / latestCandle.open) * 100).toFixed(2) : '0.00';
                        const changeStr = change >= 0 ? `+₹${{change.toFixed(2)}} (+${{changePercent}}%)` : `₹${{change.toFixed(2)}} (${{changePercent}}%)`;

                        const info = `${{dateStr}} | O: ₹${{latestCandle.open.toFixed(2)}} H: ₹${{latestCandle.high.toFixed(2)}} L: ₹${{latestCandle.low.toFixed(2)}} C: ₹${{latestCandle.close.toFixed(2)}} | ${{changeStr}}`;
                        priceInfoEl.textContent = info;
                    }} else {{
                        priceInfoEl.textContent = 'No data available';
                    }}
                }}

                updateCrosshair(e) {{
                    const rect = this.canvas.getBoundingClientRect();
                    const x = e.clientX - rect.left;
                    const y = e.clientY - rect.top;

                    if (x < this.chartArea.x || x > this.chartArea.x + this.chartArea.width ||
                        y < this.chartArea.y || y > this.volumeArea.y + this.volumeArea.height) {{
                        this.crosshairX = null;
                        this.crosshairY = null;
                        this.displayLatestCandleDetails();
                        this.updateMetricsDisplay();
                        this.canvas.style.cursor = this.currentTool ? 'crosshair' : 'default';
                        this.draw();
                        return;
                    }}

                    const candleIndex = this.xToCandle(x);
                    const priceInfoEl = document.getElementById('priceInfo');

                    if (candleIndex >= 0 && candleIndex < this.data.length) {{
                        const candle = this.data[candleIndex];
                        const date = new Date(candle.time);

                        const change = candle.close - candle.open;
                        const changePercent = (candle.open !== 0) ? ((change / candle.open) * 100).toFixed(2) : '0.00';
                        const changeStr = change >= 0 ? `+₹${{change.toFixed(2)}} (+${{changePercent}}%)` : `₹${{change.toFixed(2)}} (${{changePercent}}%)`;

                        const dateTimeFormatted = this.formatTimeLabel(date);

                        const info = `${{dateTimeFormatted}} | O: ₹${{candle.open.toFixed(2)}} H: ₹${{candle.high.toFixed(2)}} L: ₹${{candle.low.toFixed(2)}} C: ₹${{candle.close.toFixed(2)}} | ${{changeStr}}`;
                        if (priceInfoEl) priceInfoEl.textContent = info;

                        this.crosshairX = x;
                        this.crosshairY = y;
                        this.draw();
                    }} else {{
                        this.crosshairX = null;
                        this.crosshairY = null;
                        this.displayLatestCandleDetails();
                        this.updateMetricsDisplay();
                        this.draw();
                    }}
                }}

                // Coordinate conversion methods
                priceToY(price) {{
                    const ratio = (price - this.minPrice) / (this.maxPrice - this.minPrice);
                    return this.chartArea.y + this.chartArea.height - (ratio * this.chartArea.height);
                }}

                yToPrice(y) {{
                    const ratio = (this.chartArea.y + this.chartArea.height - y) / this.chartArea.height;
                    return this.minPrice + (ratio * (this.maxPrice - this.minPrice));
                }}

                timeToX(time) {{
                    let candleIndex = -1;
                    for(let i = 0; i < this.data.length; i++) {{
                        if (this.data[i].time >= time) {{
                            candleIndex = i;
                            break;
                        }}
                    }}

                    if (candleIndex === -1) {{
                        const lastCandleIndex = this.data.length - 1;
                        if (lastCandleIndex < 0) return this.chartArea.x;
                        const xOfLastCandleEnd = this.candleToX(lastCandleIndex) + this.candleWidth;
                        return Math.min(xOfLastCandleEnd + (this.chartArea.width * (this.rightBufferCandles / this.visibleCandleCount)), this.chartArea.x + this.chartArea.width);
                    }}

                    if (candleIndex === 0 && time < this.data[0].time) {{
                        return this.chartArea.x;
                    }}

                    return this.candleToX(candleIndex);
                }}

                xToTime(x) {{
                    const candleIndex = this.xToCandle(x);
                    if (candleIndex >= 0 && candleIndex < this.data.length) {{
                        return this.data[candleIndex].time;
                    }}

                    const lastDataTime = this.data.length > 0 ? this.data[this.data.length - 1].time : Date.now();
                    const firstDataTime = this.data.length > 0 ? this.data[0].time : Date.now();

                    if (candleIndex >= this.data.length) {{
                        const avgTimePerCandle = (lastDataTime - firstDataTime) / Math.max(1, this.data.length - 1);
                        return lastDataTime + (avgTimePerCandle * (candleIndex - (this.data.length - 1)));
                    }}
                    return firstDataTime;
                }}

                candleToX(index) {{
                    const visibleCandlesOnScreen = this.viewPortEnd - this.viewPortStart + 1;
                    const candleSpace = this.chartArea.width / visibleCandlesOnScreen;
                    const relativeIndex = index - this.viewPortStart;
                    return this.chartArea.x + (relativeIndex * candleSpace);
                }}

                xToCandle(x) {{
                    const relativeX = x - this.chartArea.x;
                    const visibleCandlesOnScreen = this.viewPortEnd - this.viewPortStart + 1;
                    const candleSpace = this.chartArea.width / visibleCandlesOnScreen;
                    if (candleSpace <= 0) return -1;
                    const candleIndex = Math.floor(relativeX / candleSpace);
                    return this.viewPortStart + candleIndex;
                }}

                // Utility helper methods
                isLineVisible(x1, y1, x2, y2) {{
                    const chartLeft = this.chartArea.x;
                    const chartRight = this.chartArea.x + this.chartArea.width;
                    const chartTop = this.chartArea.y;
                    const chartBottom = this.chartArea.y + this.chartArea.height;

                    return !((x1 < chartLeft && x2 < chartLeft) ||
                             (x1 > chartRight && x2 > chartRight) ||
                             (y1 < chartTop && y2 < chartTop) ||
                             (y1 > chartBottom && y2 > chartBottom));
                }}

                isRectVisible(x, y, width, height) {{
                    const chartLeft = this.chartArea.x;
                    const chartRight = this.chartArea.x + this.chartArea.width;
                    const chartTop = this.chartArea.y;
                    const chartBottom = this.chartArea.y + this.chartArea.height;

                    return x + width >= chartLeft && x <= chartRight &&
                           y + height >= chartTop && y <= chartBottom;
                }}

                isPointVisible(x, y) {{
                    return x >= this.chartArea.x && x <= this.chartArea.x + this.chartArea.width &&
                           y >= this.chartArea.y && y <= this.chartArea.y + this.chartArea.height;
                }}

                formatTimeLabel(date) {{
                    const now = new Date();
                    const daysDiff = Math.floor((now - date) / (1000 * 60 * 60 * 24));
                    const isSameDay = date.toDateString() === now.toDateString();

                    if (this.currentInterval === 'minute' || this.currentInterval === '3minute' || 
                        this.currentInterval === '5minute' || this.currentInterval === '10minute' || 
                        this.currentInterval === '15minute' || this.currentInterval === '30minute' || 
                        this.currentInterval === '60minute') {{
                        const time = date.toLocaleTimeString('en-GB', {{ hour: '2-digit', minute: '2-digit' }});
                        if (isSameDay) {{
                            return time;
                        }} else {{
                            return date.toLocaleDateString('en-GB', {{ day: '2-digit', month: 'short' }}) + ' ' + time;
                        }}
                    }} else if (this.currentInterval === 'day') {{
                        if (daysDiff < 7) {{
                            return date.toLocaleDateString('en-GB', {{ weekday: 'short' }});
                        }} else if (daysDiff < 365) {{
                            return date.toLocaleDateString('en-GB', {{ day: '2-digit', month: 'short' }});
                        }} else {{
                            return date.toLocaleDateString('en-GB', {{ month: 'short', year: '2-digit' }});
                        }}
                    }}
                    return date.toLocaleDateString('en-GB', {{ day: '2-digit', month: 'short', year: '2-digit' }});
                }}

                formatVolume(volume) {{
                    if (volume >= 1e7) return (volume / 1e7).toFixed(1) + 'Cr';
                    if (volume >= 1e5) return (volume / 1e5).toFixed(1) + 'L';
                    if (volume >= 1e3) return (volume / 1e3).toFixed(1) + 'K';
                    return volume.toFixed(0);
                }}

                getDrawingAtPoint(mousePos) {{
                    // Check if any drawing is clicked for selection
                    const tolerance = 5;

                    // Check lines
                    for (const line of this.drawings.lines) {{
                        const startX = this.timeToX(line.startTime);
                        const startY = this.priceToY(line.startPrice);
                        const endX = this.timeToX(line.endTime);
                        const endY = this.priceToY(line.endPrice);

                        if (this.isPointNearLine(mousePos.x, mousePos.y, startX, startY, endX, endY, tolerance)) {{
                            return line.id;
                        }}
                    }}

                    // Check rectangles
                    for (const rect of this.drawings.rectangles) {{
                        const startX = this.timeToX(rect.startTime);
                        const startY = this.priceToY(rect.startPrice);
                        const endX = this.timeToX(rect.endTime);
                        const endY = this.priceToY(rect.endPrice);

                        const x = Math.min(startX, endX);
                        const y = Math.min(startY, endY);
                        const width = Math.abs(endX - startX);
                        const height = Math.abs(endY - startY);

                        if (mousePos.x >= x - tolerance && mousePos.x <= x + width + tolerance &&
                            mousePos.y >= y - tolerance && mousePos.y <= y + height + tolerance) {{
                            return rect.id;
                        }}
                    }}

                    // Check notes
                    for (const note of this.drawings.notes) {{
                        const x = this.timeToX(note.time);
                        const y = this.priceToY(note.price);

                        if (Math.abs(mousePos.x - x) <= tolerance && Math.abs(mousePos.y - y) <= tolerance) {{
                            return note.id;
                        }}
                    }}

                    return null;
                }}

                isPointNearLine(px, py, x1, y1, x2, y2, tolerance) {{
                    const A = px - x1;
                    const B = py - y1;
                    const C = x2 - x1;
                    const D = y2 - y1;

                    const dot = A * C + B * D;
                    const lenSq = C * C + D * D;
                    let param = -1;
                    if (lenSq !== 0) {{
                        param = dot / lenSq;
                    }}

                    let xx, yy;
                    if (param < 0) {{
                        xx = x1;
                        yy = y1;
                    }} else if (param > 1) {{
                        xx = x2;
                        yy = y2;
                    }} else {{
                        xx = x1 + param * C;
                        yy = y1 + param * D;
                    }}

                    const dx = px - xx;
                    const dy = py - yy;
                    return Math.sqrt(dx * dx + dy * dy) <= tolerance;
                }}

                // Slider methods
                setupSlider() {{
                    const setupSliderElements = () => {{
                        this.slider = document.getElementById('timeSlider');
                        this.sliderTrack = document.getElementById('sliderTrack');
                        this.sliderThumb = document.getElementById('sliderThumb');

                        if (!this.slider || !this.sliderThumb || !this.sliderTrack) {{
                            setTimeout(setupSliderElements, 100);
                            return;
                        }}

                        this.sliderThumb.addEventListener('mousedown', (e) => this.handleSliderMouseDown(e));
                        document.addEventListener('mousemove', (e) => this.handleSliderMouseMove(e));
                        document.addEventListener('mouseup', (e) => this.handleSliderMouseUp(e));
                        this.sliderTrack.addEventListener('click', (e) => this.handleSliderClick(e));
                        this.slider.addEventListener('wheel', (e) => this.handleSliderWheel(e));

                        console.log('Slider setup completed');
                    }};

                    setupSliderElements();
                }}

                handleSliderMouseDown(e) {{
                    if (e.target === this.sliderThumb) {{
                        e.preventDefault();
                        this.isSliderDragging = true;
                        this.sliderLastX = e.clientX;
                        this.sliderThumb.style.cursor = 'grabbing';
                    }}
                }}

                handleSliderMouseMove(e) {{
                    if (!this.isSliderDragging) return;

                    e.preventDefault();
                    const deltaX = e.clientX - this.sliderLastX;
                    this.sliderLastX = e.clientX;

                    const totalCandleSpots = this.data.length + this.rightBufferCandles;
                    const totalMovableRange = totalCandleSpots - this.visibleCandleCount;
                    if (totalMovableRange <= 0) return;

                    const pixelsPerCandleSpot = (this.sliderTrack.clientWidth - this.sliderThumb.clientWidth) / totalMovableRange;
                    const candleDelta = Math.round(deltaX / pixelsPerCandleSpot);

                    let newViewPortStart = this.viewPortStart - candleDelta;
                    newViewPortStart = Math.max(0, Math.min(newViewPortStart, totalCandleSpots - this.visibleCandleCount));

                    if (this.viewPortStart !== newViewPortStart) {{
                        this.viewPortStart = newViewPortStart;
                        this.viewPortEnd = this.viewPortStart + this.visibleCandleCount - 1;
                        this.calculateBounds();
                        this.draw();
                        this.updateSlider();
                    }}
                }}

                handleSliderMouseUp(e) {{
                    this.isSliderDragging = false;
                    this.sliderThumb.style.cursor = 'grab';
                }}

                handleSliderClick(e) {{
                    if (e.target === this.sliderThumb) return;

                    const rect = this.sliderTrack.getBoundingClientRect();
                    const clickX = e.clientX - rect.left;
                    const trackWidth = this.sliderTrack.clientWidth;

                    const totalCandleSpots = this.data.length + this.rightBufferCandles;
                    const totalMovableRange = totalCandleSpots - this.visibleCandleCount;
                    if (totalMovableRange <= 0) return;

                    const proportion = clickX / trackWidth;
                    const potentialStart = Math.round(proportion * totalMovableRange);
                    let newViewPortStart = Math.max(0, Math.min(potentialStart, totalMovableRange));

                    if (this.viewPortStart !== newViewPortStart) {{
                        this.viewPortStart = newViewPortStart;
                        this.viewPortEnd = this.viewPortStart + this.visibleCandleCount - 1;
                        this.calculateBounds();
                        this.draw();
                        this.updateSlider();
                    }}
                }}

                handleSliderWheel(e) {{
                    e.preventDefault();

                    // Mark as user-initiated zoom
                    this.isUserZooming = true;

                    const scrollAmount = e.deltaY > 0 ? 5 : -5;

                    const totalCandleSpots = this.data.length + this.rightBufferCandles;
                    const totalMovableRange = totalCandleSpots - this.visibleCandleCount;
                    if (totalMovableRange <= 0) {{
                        this.isUserZooming = false;
                        return;
                    }}

                    let newViewPortStart = Math.max(0, Math.min(this.viewPortStart + scrollAmount, totalMovableRange));

                    if (this.viewPortStart !== newViewPortStart) {{
                        this.viewPortStart = newViewPortStart;
                        this.viewPortEnd = this.viewPortStart + this.visibleCandleCount - 1;
                        this.calculateBounds();
                        this.draw();
                        this.updateSlider();
                    }}

                    // Reset user zooming flag after a delay
                    setTimeout(() => {{
                        this.isUserZooming = false;
                    }}, 100);
                }}

                updateSlider() {{
                    if (!this.slider || !this.sliderThumb || !this.sliderTrack) {{
                        setTimeout(() => this.updateSlider(), 50);
                        return;
                    }}

                    const totalCandles = this.data.length;
                    const totalCandleSpots = totalCandles + this.rightBufferCandles;

                    if (totalCandleSpots <= this.visibleCandleCount) {{
                        this.slider.style.display = 'none';
                        return;
                    }}

                    this.slider.style.display = 'flex';

                    const trackWidth = this.sliderTrack.clientWidth;
                    const visiblePercentage = this.visibleCandleCount / totalCandleSpots;
                    const newThumbWidth = Math.max(20, Math.round(visiblePercentage * trackWidth));
                    const maxThumbPosition = trackWidth - newThumbWidth;
                    const ratio = this.viewPortStart / (totalCandles + this.rightBufferCandles - this.visibleCandleCount);
                    const thumbPosition = ratio * maxThumbPosition;

                    this.sliderThumb.style.width = newThumbWidth + 'px';
                    this.sliderThumb.style.left = Math.max(0, Math.min(maxThumbPosition, thumbPosition)) + 'px';
                }}

                // Public API methods
                setDrawingTool(toolId, enabled, color, lineWidth) {{
                    this.isDragging = false;
                    this.canvas.style.cursor = 'crosshair';

                    if (enabled) {{
                        this.currentTool = toolId;
                        this.drawingColor = color || this.drawingColor;
                        this.lineWidth = lineWidth || this.lineWidth;
                    }} else {{
                        this.currentTool = null;
                        this.canvas.style.cursor = 'default';
                        this.isDrawing = false;
                        this.startPoint = null;
                        this.endPoint = null;
                    }}
                    this.draw();
                }}

                updateDrawingStyle(color, lineWidth) {{
                    this.drawingColor = color || this.drawingColor;
                    this.lineWidth = lineWidth || this.lineWidth;
                }}

                setVisibleCandleCount(count) {{
                    let newCount = Math.max(20, Math.min(this.data.length + this.rightBufferCandles, count));
                    if (this.visibleCandleCount === newCount) return;

                    // Don't mark as user zooming for programmatic changes
                    this.isUserZooming = false;

                    this.visibleCandleCount = newCount;
                    this.viewPortEnd = Math.min(this.data.length - 1 + this.rightBufferCandles, this.viewPortStart + this.visibleCandleCount - 1);
                    this.viewPortStart = this.viewPortEnd - this.visibleCandleCount + 1;
                    this.viewPortStart = Math.max(0, this.viewPortStart);
                    this.viewPortEnd = this.viewPortStart + this.visibleCandleCount - 1;

                    this.calculateBounds();
                    this.draw();
                    this.updateSlider();

                    // Don't notify for programmatic changes
                    // this.notifyZoomChange();
                }}

                setChartSettings(settings) {{
                    if (settings) {{
                        this.candleWidth = settings.candleWidth || this.candleWidth;
                        this.candleSpacing = settings.candleSpacing || this.candleSpacing;
                        this.colors.upCandle = settings.upCandleColor || this.colors.upCandle;
                        this.colors.downCandle = settings.downCandleColor || this.colors.downCandle;
                        this.calculateBounds();
                        this.draw();
                    }}
                }}

                updateLivePrice(newPrice) {{
                    if (this.data.length === 0 || typeof newPrice !== 'number') return;

                    this.livePrice = newPrice;
                    const lastActualCandle = this.data[this.data.length - 1];
                    if (lastActualCandle) {{
                        lastActualCandle.close = newPrice;
                        lastActualCandle.high = Math.max(lastActualCandle.high, newPrice);
                        lastActualCandle.low = Math.min(lastActualCandle.low, newPrice);
                    }}

                    this.calculateBounds();
                    this.draw();
                }}

                clearAllDrawings() {{
                    this.drawings = {{ lines: [], rectangles: [], notes: [] }};
                    this.draw();
                    this.notifyDrawingsChange();
                }}

                deleteSelectedDrawing() {{
                    if (this.selectedDrawingId) {{
                        let foundAndDeleted = false;
                        for (const type in this.drawings) {{
                            const initialLength = this.drawings[type].length;
                            this.drawings[type] = this.drawings[type].filter(d => d.id !== this.selectedDrawingId);
                            if (this.drawings[type].length < initialLength) {{
                                foundAndDeleted = true;
                                break;
                            }}
                        }}

                        if (foundAndDeleted) {{
                            this.selectedDrawingId = null;
                            this.draw();
                            this.notifyDrawingsChange();
                            console.log("Deleted selected drawing.");
                        }} else {{
                            console.warn("Selected drawing not found for deletion.");
                        }}
                    }} else {{
                        console.log("No drawing selected for deletion.");
                    }}
                }}

                autoScale() {{
                    this.calculateBounds();
                    this.draw();
                    this.updateSlider();
                }}

                getVisibleCandleCount() {{
                    return this.visibleCandleCount;
                }}

                getAllDrawings() {{
                    return this.drawings;
                }}
            }}

            // Global chart settings to maintain consistency across symbols
            window.globalChartSettings = window.globalChartSettings || {{
                visibleCandleCount: {initial_visible_candle_count},
                candleWidth: {initial_candle_width},
                candleSpacing: {initial_candle_spacing}
            }};

            // Initialize chart when DOM is ready
            const candlestickData = {candlestick_json};
            const volumeData = {volume_json};
            const smaData = {sma_json};
            const initialADR = {adr_json};
            const percentageChanges = {percentage_changes_json};
            const upCandleColor = '{up_candle_color}';
            const downCandleColor = '{down_candle_color}';
            const currentInterval = {current_interval_js};
            const initialDrawingsJson = `{safe_initial_drawings}`;

            let chartInitialized = false;

            function initChart() {{
                if (chartInitialized) return;
                chartInitialized = true;

                try {{
                    const chart = new FixedTradingChart(
                        'mainCanvas',
                        candlestickData,
                        volumeData,
                        window.globalChartSettings.visibleCandleCount, // Use global setting
                        window.globalChartSettings.candleWidth,
                        window.globalChartSettings.candleSpacing,
                        upCandleColor,
                        downCandleColor,
                        smaData,
                        initialADR,
                        percentageChanges,
                        currentInterval,
                        initialDrawingsJson
                    );

                    window.chart = chart;
                    window.autoScale = () => chart.autoScale();

                    // Update global settings when user zooms
                    chart.updateGlobalSettings = function(visibleCount) {{
                        window.globalChartSettings.visibleCandleCount = visibleCount;
                        console.log('Updated global visible candle count to:', visibleCount);
                    }};

                    console.log('Chart initialized successfully with', candlestickData.length, 'candles');
                }} catch (error) {{
                    console.error('Error initializing chart:', error);
                    document.getElementById('priceInfo').textContent = 'Error loading chart: ' + error.message;
                    document.getElementById('metricsInfo').textContent = 'Chart initialization failed';
                }}
            }}

            // Multiple initialization attempts to ensure chart loads
            document.addEventListener('DOMContentLoaded', initChart);
            if (document.readyState === 'interactive' || document.readyState === 'complete') {{
                initChart();
            }}
            setTimeout(initChart, 100);
        </script>
    </body>
    </html>
            """
        return html

    def _auto_scale_chart(self):
        """Auto scale the chart to fit all visible data"""
        if self.chart_view:
            self.chart_view.page().runJavaScript("if (window.autoScale) window.autoScale();")

    def _open_settings_dialog(self):
        """Open a dialog to configure chart settings."""
        current_settings = {
            "candle_width": self._current_candle_width,
            "candle_spacing": self._current_candle_spacing,
            "default_visible_candles": self.current_visible_candle_count,
            "up_candle_color": self._current_up_color,
            "down_candle_color": self._current_down_color
        }
        dialog = ChartSettingsDialog(current_settings, self)
        dialog.settings_changed.connect(self._apply_chart_settings)
        dialog.exec()

    @Slot(dict)
    def _apply_chart_settings(self, new_settings: Dict[str, Any]):
        """Apply new chart settings received from the settings dialog."""
        self._current_candle_width = new_settings["candle_width"]
        self._current_candle_spacing = new_settings["candle_spacing"]
        self.current_visible_candle_count = new_settings["default_visible_candles"]
        self._current_up_color = new_settings["up_candle_color"]
        self._current_down_color = new_settings["down_candle_color"]

        self.drawing_storage.save_global_settings(new_settings)

        if self.chart_view and self.current_state == ChartState.LOADED:
            js_code = f"""
            if (window.chart) {{
                window.chart.setChartSettings({{
                    candleWidth: {self._current_candle_width},
                    candleSpacing: {self._current_candle_spacing},
                    upCandleColor: '{self._current_up_color}',
                    downCandleColor: '{self._current_down_color}'
                }});
                window.chart.setVisibleCandleCount({self.current_visible_candle_count});
                window.chart.autoScale();
            }}
            """
            self.chart_view.page().runJavaScript(js_code)
            logger.info("Applied new chart settings and auto-scaled.")

    def _update_symbol_info(self, df: pd.DataFrame):
        """Update symbol information display based on dataframe"""
        try:
            if df.empty:
                return

            latest = df.iloc[-1]
            symbol = self.current_symbol

            self.current_ltp = float(latest['close']) if 'close' in latest else 0.0
            current_price = self.current_ltp

            if len(df) > 1:
                prev_price = df.iloc[-2]['close']
                change = current_price - prev_price
                change_pct = (change / prev_price) * 100 if prev_price != 0 else 0
                change_str = f"{change:+.2f} ({change_pct:+.2f}%)"
            else:
                change_str = "N/A"

            info_text = f"{symbol} • ₹{current_price:.2f}"
            self.symbol_info_label.setText(info_text)
            self.symbol_info_label.setToolTip(f"Change: {change_str}")

        except Exception as e:
            logger.error(f"Error updating symbol info from DataFrame: {e}")

    @Slot()
    def _on_order_button_clicked(self):
        """Emit signal for order placement"""
        if self.current_symbol and self.current_ltp > 0:
            self.order_button_clicked.emit(self.current_symbol, self.current_ltp)
        else:
            QMessageBox.warning(self, "No Symbol", "Please select a symbol first.")

    def _change_timeframe(self, interval: str):
        """Change chart timeframe"""
        if self.current_interval == interval or not self.current_symbol:
            return

        for btn_interval, btn in self.timeframe_buttons.items():
            btn.setChecked(btn_interval == interval)

        # Save current state before changing timeframe
        if self.current_symbol and self.chart_view:
            self._save_current_state_sync()

        # Clear active drawing tools
        for btn in self.drawing_buttons.values():
            btn.setChecked(False)

        self.current_interval = interval
        saved_state = self.drawing_storage.load_state(self.current_symbol, self.current_interval)
        self.current_visible_candle_count = saved_state.get("visible_candle_count",
                                                            self.global_chart_settings["default_visible_candles"])

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
        if self.data_loader_thread and self.data_loader_thread.isRunning():
            self.data_loader_thread.stop()
            self.data_loader_thread.quit()
            self.data_loader_thread.wait(3000)
            if self.data_loader_thread.isRunning():
                logger.warning("ChartDataLoaderThread is still running after wait, terminating forcefully.")
                self.data_loader_thread.terminate()
            self.data_loader_thread.deleteLater()
            self.data_loader_thread = None

    def _show_error(self, message: str):
        """Show error state with message"""
        self.error_label.setText(f"Error: {message}")
        self._set_state(ChartState.ERROR)

    def _apply_styles(self):
        """Apply professional dark theme styling"""
        self.setStyleSheet("""
            /* Main widget */
            CandlestickChart {
                background-color: #0a0a0a;
                color: #e0e0e0;
                font-family: "Segoe UI", "Consolas", monospace;
            }

            /* Toolbars */
            QFrame#chartToolbar {
                background-color: #1a1a1a;
                border-bottom: 1px solid #333333;
            }

            /* Symbol info */
            #symbolInfoLabel {
                color: #00bfff;
                font-size: 14px;
                font-weight: bold;
            }

            /* Control buttons */
            #controlButton {
                background-color: #2a2a2a;
                color: #e0e0e0;
                border: 1px solid #404040;
                border-radius: 4px;
                font-size: 11px;
                padding: 6px 6px; /* Reduced padding for more compactness */
            }

            #controlButton:hover {
                background-color: #3a3a3a;
                border-color: #555555;
            }

            #controlButton:pressed {
                background-color: #1a1a1a;
            }

            /* Order button */
            #orderButton {
                background-color: #0066cc;
                color: white;
                border: 1px solid #0066cc;
                border-radius: 4px;
                font-weight: bold;
                font-size: 11px;
                padding: 6px 12px; /* Standard padding */
            }

            #orderButton:hover {
                background-color: #0080ff;
            }

            #orderButton:disabled {
                background-color: #333333;
                color: #666666;
            }

            /* Timeframe buttons */
            #timeframeButton {
                background-color: #2a2a2a;
                color: #e0e0e0;
                border: 1px solid #404040;
                border-radius: 4px;
                font-size: 11px;
                padding: 6px;
            }

            #timeframeButton:hover {
                background-color: #3a3a3a;
            }

            #timeframeButton:checked {
                background-color: #0066cc;
                color: white;
                border-color: #0066cc;
            }

            /* Drawing tool buttons (emojis) */
            #drawingToolButton {
                background-color: #2a2a2a;
                color: #e0e0e0;
                border: 1px solid #404040;
                border-radius: 4px;
                font-size: 16px; /* For better emoji visibility */
                padding: 2px 5px; /* Compact padding */
            }

            #drawingToolButton:hover {
                background-color: #3a3a3a;
            }

            #drawingToolButton:checked {
                background-color: #FFD700;
                color: #000000;
                border-color: #FFD700;
                font-weight: bold;
            }

            /* Style buttons (color picker, line width) */
            #drawingStyleButton {
                background-color: #3a3a3a;
                color: #e0e0e0;
                border: 1px solid #555555;
                border-radius: 4px;
                font-size: 12px;
                padding: 2px 5px; /* Compact padding */
            }

            #drawingStyleButton:hover {
                background-color: #4a4a4a;
            }

            /* Save/Clear buttons (emojis) */
            #saveButton, #clearButton {
                background-color: #2a2a2a;
                color: #e0e0e0;
                border: 1px solid #404040;
                border-radius: 4px;
                font-size: 16px;
                padding: 2px 5px;
            }

            #saveButton:hover {
                background-color: #2e8b57;
            }

            #clearButton:hover {
                background-color: #cc4444;
            }

            /* Refresh button */
            #refreshButton {
                background-color: #2e8b57;
                color: white;
                border: 1px solid #2e8b57;
                border-radius: 4px;
                font-weight: bold;
            }

            #refreshButton:hover {
                background-color: #369665;
            }

            /* Labels */
            #loadingLabel, #errorLabel {
                color: #00bfff;
                font-size: 16px;
                font-weight: bold;
            }

            #errorLabel {
                color: #ff6b6b;
            }

            /* Retry button */
            #retryButton {
                background-color: #cc4444;
                color: white;
                border: 1px solid #cc4444;
                border-radius: 4px;
                font-weight: bold;
                padding: 8px 16px;
            }

            #retryButton:hover {
                background-color: #e55555;
            }

            /* Progress bar */
            QProgressBar {
                background-color: #1a1a1a;
                border: none;
                border-radius: 1px;
            }

            QProgressBar::chunk {
                background-color: #0066cc;
                border-radius: 1px;
            }

            /* Stacked widget */
            QStackedWidget {
                background-color: #0a0a0a;
                border: 1px solid #333333;
            }
        """)

    def closeEvent(self, event):
        """Handle widget close event"""
        try:
            if self.current_symbol and self.chart_view:
                logger.info("Attempting to save final chart state before closing.")
                self._save_current_state_sync()

            self._stop_current_operations()
            self.data_cache.clear()

            if self.channel:
                self.channel.deleteLater()
                self.channel = None

            logger.info("Candlestick chart widget closed successfully")

        except Exception as e:
            logger.error(f"Error during close: {e}")

        super().closeEvent(event)


# Export for backward compatibility
ChartWindow = CandlestickChart

if __name__ == "__main__":
    import sys
    from PySide6.QtWidgets import QApplication, QMainWindow
    from PySide6.QtCore import QTimer


    class MockKiteConnect:
        def historical_data(self, instrument_token, from_date, to_date, interval):
            import random
            data = []
            price = 2800

            num_candles = 500
            for i in range(num_candles):
                current_date = from_date + timedelta(days=i)
                if current_date.weekday() >= 5: # Skip weekends for daily data simulation
                    continue

                open_price = price + random.uniform(-5, 5)
                high = open_price + random.uniform(0, 20)
                low = open_price - random.uniform(0, 20)
                close = low + random.uniform(0, high - low)
                volume = random.randint(100000, 1000000)

                data.append({
                    'date': current_date,
                    'open': open_price,
                    'high': high,
                    'low': low,
                    'close': close,
                    'volume': volume
                })
                price = close + random.uniform(-2, 2) # Slight drift for price over time

            return data


    app = QApplication(sys.argv)

    main_window = QMainWindow()
    main_window.setWindowTitle("Professional Swing Trading Terminal")
    main_window.setGeometry(100, 100, 1400, 900)
    main_window.setStyleSheet("""
        QMainWindow {
            background-color: #0a0a0a;
            color: #e0e0e0;
        }
    """)

    mock_kite = MockKiteConnect()
    chart_widget = CandlestickChart(mock_kite)

    market_data_timer = QTimer()
    market_data_timer.setInterval(100) # Faster updates for testing
    mock_ltp = 0.0


    def simulate_live_data():
        global mock_ltp
        if chart_widget.current_symbol and chart_widget.current_instrument_token:
            if mock_ltp == 0.0 and chart_widget.current_ltp != 0.0:
                mock_ltp = chart_widget.current_ltp
            elif mock_ltp != 0.0:
                # Simulate price fluctuations around the last price
                change = random.uniform(-0.5, 0.5) # Smaller changes for live data
                mock_ltp += change
                mock_ltp = max(1.0, mock_ltp) # Ensure price doesn't go below 1

            if mock_ltp != 0.0:
                live_updates_list = [{
                    'tradingsymbol': chart_widget.current_symbol,
                    'instrument_token': chart_widget.current_instrument_token,
                    'last_price': mock_ltp,
                }]
                chart_widget.update_live_data(live_updates_list)
        else:
            mock_ltp = 0.0


    market_data_timer.timeout.connect(simulate_live_data)
    market_data_timer.start()

    instruments = [
        {'tradingsymbol': 'RELIANCE', 'instrument_token': 738561},
        {'tradingsymbol': 'TCS', 'instrument_token': 2953217},
        {'tradingsymbol': 'INFY', 'instrument_token': 408065},

    ]
    chart_widget.set_instrument_list(instruments)

    main_window.setCentralWidget(chart_widget)


    def load_default_symbol():
        chart_widget.on_search('RELIANCE')


    QTimer.singleShot(500, load_default_symbol)

    main_window.show()
    sys.exit(app.exec())