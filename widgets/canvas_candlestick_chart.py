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

    def __init__(self, parent=None):
        super().__init__(parent)

    @Slot(str)
    def notify_drawings_changed(self, drawings_json: str):
        """Receives drawing data as a JSON string from JavaScript."""
        try:
            # Validate JSON before emitting
            json.loads(drawings_json)  # This will raise exception if invalid
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

        # Drawing state
        self.current_drawing_color = "#FFD700"
        self.current_line_width = 2

        # QtWebChannel setup
        self.chart_bridge = ChartBridge()
        self.chart_bridge.drawings_changed.connect(self._on_drawings_changed_from_js)
        self.chart_bridge.visible_candle_count_changed.connect(self._on_zoom_changed_from_js)

        # UI components
        self.chart_view: Optional[QWebEngineView] = None
        self.channel: Optional[QWebChannel] = None  # Declare channel here
        self.timeframe_buttons: Dict[str, QPushButton] = {}
        self.drawing_buttons: Dict[str, QPushButton] = {}
        # References for specific toolbar buttons, set in _setup_ui
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

    def _setup_ui(self):
        """Setup the main UI layout"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Combined toolbar (formerly main_toolbar + drawing_toolbar)
        self.combined_toolbar = QFrame()
        self.combined_toolbar.setObjectName("chartToolbar")  # Reusing style object name
        self.combined_toolbar.setFixedHeight(40)  # Fixed height for the single line

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

        toolbar_layout.addStretch()  # Pushes elements to the right

        # Order button
        self.order_btn = QPushButton("Order")
        self.order_btn.setObjectName("orderButton")
        self.order_btn.setFixedSize(70, 30)
        self.order_btn.clicked.connect(self._on_order_button_clicked)
        toolbar_layout.addWidget(self.order_btn)

        # Auto Scale button (now "A" and compact)
        self.auto_scale_btn = QPushButton("A")
        self.auto_scale_btn.setObjectName("controlButton")
        self.auto_scale_btn.setFixedSize(30, 30)  # Very compact
        self.auto_scale_btn.setToolTip("Auto Scale (Ctrl+A)")
        self.auto_scale_btn.clicked.connect(self._auto_scale_chart)
        toolbar_layout.addWidget(self.auto_scale_btn)

        # Refresh button
        self.refresh_button = QPushButton("⟳")
        self.refresh_button.setObjectName("refreshButton")
        self.refresh_button.setFixedSize(30, 30)  # Very compact
        self.refresh_button.setToolTip("Refresh Data (F5)")
        self.refresh_button.clicked.connect(self._force_refresh)
        toolbar_layout.addWidget(self.refresh_button)

        # Settings button
        self.settings_btn = QPushButton("⚙️")
        self.settings_btn.setObjectName("controlButton")
        self.settings_btn.setFixedSize(30, 30)  # Very compact
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
            btn.setFixedSize(40, 30)  # Slightly larger for text
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
            btn.setFixedSize(30, 28)  # Very compact button size
            btn.setToolTip(tooltip)
            if tool_id == "color_picker":
                btn.clicked.connect(self._choose_drawing_color)
                btn.setCheckable(False)  # Not a toggle tool
                self.color_btn = btn  # Keep reference
            elif tool_id == "line_width":
                btn.clicked.connect(self._toggle_line_width)
                btn.setCheckable(False)  # Not a toggle tool
                self.line_width_btn = btn  # Keep reference
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
                self.drawing_buttons[tool_id] = btn  # Only store actual drawing tools here
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

    # Removed _create_toolbar and _create_drawing_toolbar as their content is now in _setup_ui

    def _toggle_drawing_tool(self, tool_id: str, checked: bool):
        """Toggle drawing tool mode"""
        if self.chart_view and self.current_state == ChartState.LOADED:
            # Deactivate other tools when one is selected
            if checked:
                for other_tool, btn in self.drawing_buttons.items():
                    if other_tool != tool_id:
                        btn.setChecked(False)

            # Send command to JavaScript
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

            # Update active tool color
            if self.chart_view:
                js_code = f"if (window.chart) window.chart.updateDrawingStyle('{self.current_drawing_color}', {self.current_line_width});"
                self.chart_view.page().runJavaScript(js_code)

    def _toggle_line_width(self):
        """Toggle between different line widths"""
        widths = [1, 2, 3, 4]
        current_index = widths.index(self.current_line_width) if self.current_line_width in widths else 0
        self.current_line_width = widths[(current_index + 1) % len(widths)]

        # Update button appearance
        width_symbols = {1: "─", 2: "━", 3: "▬", 4: "█"}
        self.line_width_btn.setText(width_symbols.get(self.current_line_width, "─"))

        # Update active tool width
        if self.chart_view:
            js_code = f"if (window.chart) window.chart.updateDrawingStyle('{self.current_drawing_color}', {self.current_line_width});"
            self.chart_view.page().runJavaScript(js_code)

    def _save_drawings(self):
        """Manually trigger a save of current drawings and zoom level to storage."""
        if not self.chart_view or not self.current_symbol:
            logger.warning("Attempted manual save without a chart view or current symbol.")
            return

        # Request data from JS chart to ensure it's the latest
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

                # Avoid saving if drawings haven't changed
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
            self.current_visible_candle_count = visible_candle_count  # Update Python's internal state
            try:
                current_state = self.drawing_storage.load_state(self.current_symbol, self.current_interval)
                current_state["visible_candle_count"] = visible_candle_count
                self.drawing_storage.save_state(self.current_symbol, self.current_interval, current_state)
                # logger.debug(f"Auto-saved zoom level for {self.current_symbol}: {visible_candle_count}") # Can be very chatty
            except Exception as e:
                logger.error(f"Error saving zoom from JS callback: {e}")

    def _clear_drawings(self):
        """Clear all drawings from chart and storage"""
        if self.chart_view:
            js_code = "if (window.chart) window.chart.clearAllDrawings();"
            self.chart_view.page().runJavaScript(js_code)

            # Auto-save will be triggered by JS clearAllDrawings calling notify_drawings_changed

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

        clear_shortcut = QShortcut(QKeySequence("Delete"), self)
        clear_shortcut.activated.connect(self._clear_drawings)

    def _activate_drawing_tool_shortcut(self, tool_id: str):
        """Activate drawing tool via keyboard shortcut"""
        if tool_id in self.drawing_buttons:
            btn = self.drawing_buttons[tool_id]
            current_state = btn.isChecked()
            btn.setChecked(not current_state)
            self._toggle_drawing_tool(tool_id, not current_state)

    def _initialize_chart(self):
        """Initialize chart after UI is ready"""
        self._create_chart_view()

    def _create_chart_view(self):
        """Create the web engine view for the chart with proper WebChannel setup"""
        try:
            # Clear existing chart
            if self.chart_view:
                self.chart_layout.removeWidget(self.chart_view)
                self.chart_view.deleteLater()
                self.chart_view = None

            # Re-initialize channel explicitly to avoid issues on multiple calls if not careful
            if self.channel:
                self.channel.deleteLater()
                self.channel = None

            # Create new chart view
            self.chart_view = QWebEngineView()
            self.chart_layout.addWidget(self.chart_view)

            # Set up QWebChannel to allow JS to call Python methods
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

        # Update widget visibility
        if self.stacked_widget.currentIndex() != config['widget_index']:
            self.stacked_widget.setCurrentIndex(config['widget_index'])

        # Update button states
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

            # Load saved drawings and set initial zoom level after chart is rendered
            QTimer.singleShot(200, self._apply_saved_drawings_and_zoom)

            logger.info(f"Chart loaded: {self.current_symbol} ({len(df)} candles)")

        except Exception as e:
            logger.error(f"Error processing loaded data: {e}")
            self._show_error(f"Failed to render chart: {str(e)}")

    def _apply_saved_drawings_and_zoom(self):
        """Apply saved drawings and zoom after chart is fully loaded"""
        try:
            saved_state = self.drawing_storage.load_state(self.current_symbol, self.current_interval)
            drawings = saved_state.get("drawings", {"lines": [], "rectangles": [], "notes": []})
            initial_zoom = saved_state.get("visible_candle_count",
                                           self.global_chart_settings["default_visible_candles"])

            if self.chart_view and self.current_symbol:
                drawings_json = json.dumps(drawings)
                js_code = f"""
                if (window.chart) {{
                    console.log("Applying saved state for {self.current_symbol}");
                    window.chart.loadDrawings({drawings_json});
                    window.chart.setVisibleCandleCount({initial_zoom});
                    window.chart.setChartSettings({{
                        candleWidth: {self._current_candle_width},
                        candleSpacing: {self._current_candle_spacing},
                        upCandleColor: '{self._current_up_color}',
                        downCandleColor: '{self._current_down_color}'
                    }});
                    window.chart.autoScale();
                    window.chart.displayLatestCandleDetails();
                    console.log("Saved state applied successfully");
                }} else {{
                    console.error("Chart object not available for applying saved state.");
                }}
                """
                self.chart_view.page().runJavaScript(js_code)
                logger.info(
                    f"Applied {self.drawing_storage._count_drawings(drawings)} saved drawings and set zoom to {initial_zoom} for {self.current_symbol}")
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

            html_content = self._create_fixed_chart_html(
                candlestick_data,
                volume_data,
                self.current_visible_candle_count,
                self._current_candle_width,
                self._current_candle_spacing,
                self._current_up_color,
                self._current_down_color
            )

            self.chart_view.setHtml(html_content)
            logger.info(f"Chart rendered successfully for {self.current_symbol}")

        except Exception as e:
            logger.error(f"Chart rendering error: {e}")
            self._show_error("Failed to render chart")

    def _create_fixed_chart_html(self, candlestick_data, volume_data,
                                 initial_visible_candle_count, initial_candle_width,
                                 initial_candle_spacing, up_candle_color, down_candle_color):
        """Create HTML content with fixed drawing persistence and proper coordinate handling"""

        candlestick_json = json.dumps(candlestick_data)
        volume_json = json.dumps(volume_data)

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
                            initialCandleWidth, initialCandleSpacing, upCandleColor, downCandleColor) {{
                    this.canvas = document.getElementById(canvasId);
                    this.ctx = this.canvas.getContext('2d');
                    this.data = data;
                    this.volumeData = volumeData;
                    this.width = 0;
                    this.height = 0;
                    this.padding = {{ top: 30, right: 80, bottom: 30, left: 10 }};
                    this.rightBufferCandles = 5;

                    // Price and volume bounds
                    this.minPrice = 0;
                    this.maxPrice = 0;
                    this.minVolume = 0;
                    this.maxVolume = 0;

                    // Chart settings
                    this.candleWidth = initialCandleWidth || 4;
                    this.candleSpacing = initialCandleSpacing || 2;
                    this.visibleCandleCount = initialVisibleCandleCount || 100;

                    // Viewport
                    this.viewPortEnd = this.data.length - 1 + this.rightBufferCandles;
                    this.viewPortStart = Math.max(0, this.viewPortEnd - this.visibleCandleCount);

                    // Drawing tools
                    this.currentTool = null;
                    this.isDrawing = false;
                    this.startPoint = null;
                    this.endPoint = null;
                    this.drawingColor = '#FFD700';
                    this.lineWidth = 2;

                    this.drawings = {{
                        lines: [],
                        rectangles: [],
                        notes: []
                    }};

                    // Interaction state
                    this.isDragging = false;
                    this.lastMouseX = 0;
                    this.lastMouseY = 0;
                    this.crosshairX = null;
                    this.crosshairY = null;
                    this.livePrice = null;

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

                    // Slider state
                    this.isSliderDragging = false;
                    this.sliderLastX = 0;

                    // WebChannel state - FIXED TO PREVENT RECURSION
                    this.chartBridge = null;
                    this.webChannelInitialized = false;
                    this.isLoadingState = false; // Prevent recursive state loading
                    this.notificationQueue = []; // Queue notifications during state loading
                    this.notificationTimer = null; // Debounce notifications

                    this.init();
                }}

                async init() {{
                    try {{
                        this.setupCanvas();
                        this.setupSlider();
                        this.calculateBounds();
                        this.setupEventListeners();
                        this.setupWebChannel();

                        // Initial render
                        this.draw();
                        this.updateSlider();
                        this.displayLatestCandleDetails();

                        console.log('Chart initialized successfully');
                    }} catch (error) {{
                        console.error('Error initializing chart:', error);
                    }}
                }}

                setupWebChannel() {{
                    const initWebChannel = () => {{
                        try {{
                            if (typeof QWebChannel !== 'undefined' && window.qt && window.qt.webChannelTransport) {{
                                new QWebChannel(qt.webChannelTransport, (channel) => {{
                                    this.chartBridge = channel.objects.chartBridge;
                                    this.webChannelInitialized = true;
                                    console.log("QWebChannel ChartBridge loaded successfully");

                                    // Process queued notifications
                                    this.processNotificationQueue();
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
                    }}
                }}

                // FIXED: Debounced notification system to prevent recursion
                queueNotification(type, data) {{
                    this.notificationQueue.push({{ type, data, timestamp: Date.now() }});

                    if (this.notificationTimer) {{
                        clearTimeout(this.notificationTimer);
                    }}

                    this.notificationTimer = setTimeout(() => {{
                        this.processNotificationQueue();
                    }}, 100); // 100ms debounce
                }}

                processNotificationQueue() {{
                    if (!this.webChannelInitialized || this.isLoadingState || this.notificationQueue.length === 0) {{
                        return;
                    }}

                    // Process only the latest notification of each type
                    const latestNotifications = new Map();
                    this.notificationQueue.forEach(notification => {{
                        latestNotifications.set(notification.type, notification);
                    }});

                    latestNotifications.forEach((notification, type) => {{
                        try {{
                            if (type === 'drawings' && this.chartBridge) {{
                                this.chartBridge.notify_drawings_changed(JSON.stringify(notification.data));
                            }} else if (type === 'zoom' && this.chartBridge) {{
                                this.chartBridge.notify_visible_candle_count_changed(notification.data);
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
                    if (!this.isLoadingState) {{
                        this.queueNotification('zoom', this.visibleCandleCount);
                    }}
                }}

                // FIXED: Prevent recursive state loading
                loadDrawings(drawingsData) {{
                    if (this.isLoadingState) {{
                        console.warn('Already loading state, skipping...');
                        return;
                    }}

                    try {{
                        this.isLoadingState = true;

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
                        this.isLoadingState = false;
                    }}
                }}

                clearAllDrawings() {{
                    this.drawings = {{ lines: [], rectangles: [], notes: [] }};
                    this.draw();
                    this.notifyDrawingsChange();
                }}

                // Chart interaction methods
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

                    this.visibleCandleCount = newCount;
                    this.viewPortEnd = Math.min(this.data.length - 1 + this.rightBufferCandles, this.viewPortStart + this.visibleCandleCount - 1);
                    this.viewPortStart = this.viewPortEnd - this.visibleCandleCount + 1;
                    this.viewPortStart = Math.max(0, this.viewPortStart);
                    this.viewPortEnd = this.viewPortStart + this.visibleCandleCount - 1;

                    this.calculateBounds();
                    this.draw();
                    this.updateSlider();
                    this.notifyZoomChange();
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

                getVisibleCandleCount() {{
                    return this.visibleCandleCount;
                }}

                getAllDrawings() {{
                    return this.drawings;
                }}

                autoScale() {{
                    this.calculateBounds();
                    this.draw();
                    this.updateSlider();
                }}

                // Slider setup and event handlers
                setupSlider() {{
                    const setupSliderElements = () => {{
                        this.slider = document.getElementById('timeSlider');
                        this.sliderTrack = document.getElementById('sliderTrack');
                        this.sliderThumb = document.getElementById('sliderThumb');

                        if (!this.slider || !this.sliderThumb || !this.sliderTrack) {{
                            setTimeout(setupSliderElements, 100);
                            return;
                        }}

                        // Event listeners
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
                    e.preventDefault();
                    this.isSliderDragging = true;
                    this.sliderLastX = e.clientX;
                    this.sliderThumb.style.cursor = 'grabbing';
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
                    const scrollAmount = e.deltaY > 0 ? 5 : -5;

                    const totalCandleSpots = this.data.length + this.rightBufferCandles;
                    const totalMovableRange = totalCandleSpots - this.visibleCandleCount;
                    if (totalMovableRange <= 0) return;

                    let newViewPortStart = Math.max(0, Math.min(this.viewPortStart + scrollAmount, totalMovableRange));

                    if (this.viewPortStart !== newViewPortStart) {{
                        this.viewPortStart = newViewPortStart;
                        this.viewPortEnd = this.viewPortStart + this.visibleCandleCount - 1;
                        this.calculateBounds();
                        this.draw();
                        this.updateSlider();
                    }}
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
                        this.isDragging = true;
                        this.lastMouseX = e.clientX;
                        this.lastMouseY = e.clientY;
                        this.canvas.style.cursor = 'grabbing';
                    }}
                }}

                handleMouseMove(e) {{
                    const mousePos = this.getMousePosition(e);

                    if (this.isDrawing && this.startPoint) {{
                        this.endPoint = mousePos;
                        this.draw();
                        this.drawTemporaryDrawing();
                    }} else if (this.isDragging) {{
                        this.handleChartDrag(e);
                        this.draw();
                    }} else {{
                        this.updateCrosshair(e);
                    }}
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
                    this.canvas.style.cursor = this.currentTool ? 'crosshair' : 'default';
                    this.draw();
                }}

                handleDoubleClick(e) {{
                    if (this.currentTool === 'note') {{
                        const mousePos = this.getMousePosition(e);
                        this.addTextNote(mousePos);
                    }}
                }}

                handleWheel(e) {{
                    e.preventDefault();

                    const chartMouseY = e.clientY - this.canvas.getBoundingClientRect().top;
                    const chartMouseX = e.clientX - this.canvas.getBoundingClientRect().left;
                    let zoomChanged = false;

                    if (e.ctrlKey || e.metaKey) {{
                        // Price zoom
                        const zoomFactor = e.deltaY > 0 ? 1.1 : 0.9;

                        if (chartMouseY >= this.chartArea.y && chartMouseY <= this.chartArea.y + this.chartArea.height) {{
                            const priceAtMouse = this.yToPrice(chartMouseY);
                            const currentRange = this.maxPrice - this.minPrice;
                            const newRange = currentRange * zoomFactor;

                            this.minPrice = priceAtMouse - (newRange * ((priceAtMouse - this.minPrice) / currentRange));
                            this.maxPrice = priceAtMouse + (newRange * ((this.maxPrice - priceAtMouse) / currentRange));
                        }}
                    }} else if (e.shiftKey) {{
                        // Price pan
                        const priceRange = this.maxPrice - this.minPrice;
                        const panAmount = (e.deltaY > 0 ? 1 : -1) * priceRange * 0.05;
                        this.minPrice += panAmount;
                        this.maxPrice += panAmount;
                    }} else {{
                        // Time zoom
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
                        this.notifyZoomChange();
                    }}
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
                    }} else if (this.currentTool === 'note') {{
                        this.drawings.notes.push(drawing);
                    }}

                    this.isDrawing = false;
                    this.startPoint = null;
                    this.endPoint = null;
                    this.draw();
                    this.notifyDrawingsChange();
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

                // Chart drag handler
                handleChartDrag(e) {{
                    const deltaX = e.clientX - this.lastMouseX;
                    const deltaY = e.clientY - this.lastMouseY;

                    // Time pan
                    const visibleCandles = this.viewPortEnd - this.viewPortStart + 1;
                    const totalCandleSlots = this.data.length + this.rightBufferCandles;
                    const pixelsPerCandleSlot = this.chartArea.width / visibleCandles;
                    const candleShift = -Math.round(deltaX / pixelsPerCandleSlot);

                    let newViewPortStart = Math.max(0, Math.min(this.viewPortStart + candleShift, totalCandleSlots - visibleCandles));

                    if (this.viewPortStart !== newViewPortStart) {{
                        this.viewPortStart = newViewPortStart;
                        this.viewPortEnd = this.viewPortStart + visibleCandles - 1;
                    }}

                    // Price pan
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

                // Main drawing method
                draw() {{
                    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
                    this.ctx.fillStyle = this.colors.background;
                    this.ctx.fillRect(0, 0, this.width, this.height);

                    this.drawGrid();
                    this.drawCandlesticks();
                    this.drawVolume();
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

                    // Horizontal grid lines (price)
                    const priceStep = priceRange / 10;
                    for (let i = 0; i <= 10; i++) {{
                        const price = this.minPrice + (priceStep * i);
                        const y = this.priceToY(price);

                        this.ctx.beginPath();
                        this.ctx.moveTo(this.chartArea.x, y);
                        this.ctx.lineTo(this.chartArea.x + this.chartArea.width, y);
                        this.ctx.stroke();
                    }}

                    // Vertical grid lines (time)
                    const visibleCandlesIncludingBuffer = this.viewPortEnd - this.viewPortStart + 1;
                    const timeStep = Math.max(1, Math.floor(visibleCandlesIncludingBuffer / 6));

                    for (let i = this.viewPortStart; i < this.data.length; i += timeStep) {{
                        if (i < 0) continue;

                        const x = this.candleToX(i) + this.candleWidth / 2;

                        this.ctx.beginPath();
                        this.ctx.moveTo(x, this.chartArea.y);
                        this.ctx.lineTo(x, this.chartArea.y + this.chartArea.height);
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
                            // Doji candle
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

                drawAxes() {{
                    this.ctx.fillStyle = this.colors.text;
                    this.ctx.font = '11px monospace';
                    this.ctx.textAlign = 'left';

                    // Price labels
                    const priceRange = this.maxPrice - this.minPrice;
                    if (priceRange > 0) {{
                        const priceStep = priceRange / 8;
                        for (let i = 0; i <= 8; i++) {{
                            const price = this.minPrice + (priceStep * i);
                            const y = this.priceToY(price);
                            this.ctx.fillText('₹' + price.toFixed(2), this.chartArea.x + this.chartArea.width + 4, y + 4);
                        }}
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
                            this.ctx.fillRect(x, y, width, height);
                            this.ctx.strokeRect(x, y, width, height);
                        }}
                    }});

                    // Draw notes
                    this.drawings.notes.forEach(note => {{
                        const x = this.timeToX(note.time);
                        const y = this.priceToY(note.price);

                        if (this.isPointVisible(x, y)) {{
                            this.ctx.font = '12px Arial';
                            const textMetrics = this.ctx.measureText(note.text);

                            this.ctx.fillStyle = 'rgba(0, 0, 0, 0.8)';
                            this.ctx.fillRect(x - 2, y - 16, textMetrics.width + 4, 18);

                            this.ctx.fillStyle = note.color;
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
                    }} else if (this.currentTool === 'measure') {{
                        // Draw L-shaped measurement tool
                        this.ctx.beginPath();
                        this.ctx.moveTo(this.startPoint.x, this.startPoint.y);
                        this.ctx.lineTo(this.startPoint.x, this.endPoint.y);
                        this.ctx.stroke();

                        this.ctx.beginPath();
                        this.ctx.moveTo(this.startPoint.x, this.endPoint.y);
                        this.ctx.lineTo(this.endPoint.x, this.endPoint.y);
                        this.ctx.stroke();

                        // Display measurement info
                        const priceDiff = this.yToPrice(this.startPoint.y) - this.yToPrice(this.endPoint.y);
                        const startPrice = this.yToPrice(this.startPoint.y);
                        const priceChangePercent = (startPrice !== 0) ? ((priceDiff / startPrice) * 100) : 0;
                        const timeDiff = this.xToTime(this.endPoint.x) - this.xToTime(this.startPoint.x);
                        const daysDiff = Math.round(Math.abs(timeDiff) / (24 * 60 * 60 * 1000));

                        this.ctx.fillStyle = '#00BFFF';
                        this.ctx.font = '12px Arial';
                        const measureText = `₹${{Math.abs(priceDiff).toFixed(2)}} (${{priceChangePercent.toFixed(2)}}%) | ${{daysDiff}}d`;
                        this.ctx.fillText(measureText, this.endPoint.x + 5, this.endPoint.y - 5);
                    }}

                    this.ctx.setLineDash([]);
                }}

                drawCrosshair() {{
                    if (this.currentTool === null && this.crosshairX !== null && this.crosshairY !== null) {{
                        this.ctx.strokeStyle = this.colors.crosshair;
                        this.ctx.lineWidth = 1;
                        this.ctx.setLineDash([5, 5]);

                        // Vertical line
                        this.ctx.beginPath();
                        this.ctx.moveTo(this.crosshairX, this.chartArea.y);
                        this.ctx.lineTo(this.crosshairX, this.volumeArea.y + this.volumeArea.height);
                        this.ctx.stroke();

                        // Horizontal line
                        if (this.crosshairY >= this.chartArea.y && this.crosshairY <= this.chartArea.y + this.chartArea.height) {{
                            this.ctx.beginPath();
                            this.ctx.moveTo(this.chartArea.x, this.crosshairY);
                            this.ctx.lineTo(this.chartArea.x + this.chartArea.width, this.crosshairY);
                            this.ctx.stroke();
                        }}

                        this.ctx.setLineDash([]);
                    }}
                }}

                drawCurrentPriceRay() {{
                    if (this.livePrice !== null && this.currentTool === null && this.crosshairX === null) {{
                        const y = this.priceToY(this.livePrice);
                        const lastCandleIndex = this.data.length - 1;
                        let rayStartX = this.chartArea.x;

                        if (lastCandleIndex >= this.viewPortStart && lastCandleIndex <= this.viewPortEnd) {{
                            rayStartX = this.candleToX(lastCandleIndex) + this.candleWidth / 2;
                        }} else if (lastCandleIndex < this.viewPortStart) {{
                            rayStartX = this.chartArea.x;
                        }}

                        this.ctx.strokeStyle = this.colors.livePrice;
                        this.ctx.lineWidth = 2;
                        this.ctx.setLineDash([2, 2]);
                        this.ctx.beginPath();
                        this.ctx.moveTo(rayStartX, y);
                        this.ctx.lineTo(this.chartArea.x + this.chartArea.width, y);
                        this.ctx.stroke();
                        this.ctx.setLineDash([]);

                        // Price label
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

                // Display methods
                displayLatestCandleDetails() {{
                    const priceInfoEl = document.getElementById('priceInfo');
                    if (!priceInfoEl) return;

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
                        this.draw();
                        return;
                    }}

                    const candleIndex = this.xToCandle(x);
                    const priceInfoEl = document.getElementById('priceInfo');

                    if (candleIndex >= 0 && candleIndex < this.data.length) {{
                        const candle = this.data[candleIndex];
                        const date = new Date(candle.time);
                        const dateStr = date.toLocaleDateString('en-GB', {{ day: '2-digit', month: 'short', year: 'numeric' }});
                        const change = candle.close - candle.open;
                        const changePercent = (candle.open !== 0) ? ((change / candle.open) * 100).toFixed(2) : '0.00';
                        const changeStr = change >= 0 ? `+₹${{change.toFixed(2)}} (+${{changePercent}}%)` : `₹${{change.toFixed(2)}} (${{changePercent}}%)`;

                        const info = `${{dateStr}} | O: ₹${{candle.open.toFixed(2)}} H: ₹${{candle.high.toFixed(2)}} L: ₹${{candle.low.toFixed(2)}} C: ₹${{candle.close.toFixed(2)}} | ${{changeStr}}`;
                        if (priceInfoEl) priceInfoEl.textContent = info;

                        this.crosshairX = x;
                        this.crosshairY = y;
                        this.draw();
                    }} else {{
                        this.crosshairX = null;
                        this.crosshairY = null;
                        this.displayLatestCandleDetails();
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

                // Visibility checking methods
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

                // Formatting methods
                formatTimeLabel(date) {{
                    const now = new Date();
                    const daysDiff = Math.floor((now - date) / (1000 * 60 * 60 * 24));

                    if (daysDiff < 7) {{
                        return date.toLocaleDateString('en-GB', {{ weekday: 'short' }});
                    }} else if (daysDiff < 30) {{
                        return date.toLocaleDateString('en-GB', {{ day: '2-digit', month: 'short' }});
                    }} else {{
                        return date.toLocaleDateString('en-GB', {{ month: 'short', year: '2-digit' }});
                    }}
                }}

                formatVolume(volume) {{
                    if (volume >= 1e7) return (volume / 1e7).toFixed(1) + 'Cr';
                    if (volume >= 1e5) return (volume / 1e5).toFixed(1) + 'L';
                    if (volume >= 1e3) return (volume / 1e3).toFixed(1) + 'K';
                    return volume.toFixed(0);
                }}
            }}

            // Initialize chart
            const candlestickData = {candlestick_json};
            const volumeData = {volume_json};
            const initialVisibleCandleCount = {initial_visible_candle_count};
            const initialCandleWidth = {initial_candle_width};
            const initialCandleSpacing = {initial_candle_spacing};
            const upCandleColor = '{up_candle_color}';
            const downCandleColor = '{down_candle_color}';

            let chartInitialized = false;

            function initChart() {{
                if (chartInitialized) return;
                chartInitialized = true;

                if (candlestickData.length > 0) {{
                    const chart = new FixedTradingChart(
                        'mainCanvas',
                        candlestickData,
                        volumeData,
                        initialVisibleCandleCount,
                        initialCandleWidth,
                        initialCandleSpacing,
                        upCandleColor,
                        downCandleColor
                    );

                    // Expose chart methods to global scope
                    window.chart = chart;
                    window.autoScale = () => chart.autoScale();

                    console.log('Chart initialized successfully');
                }} else {{
                    document.getElementById('priceInfo').textContent = 'No data available';
                    console.warn('No candlestick data to initialize chart.');
                }}
            }}

            // Multiple initialization strategies to ensure chart loads
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
                font-size: 16px; /* For better emoji visibility */
                padding: 2px 5px; /* Compact padding */
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

            # Clean up WebChannel
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


    # Mock KiteConnect for testing
    class MockKiteConnect:
        def historical_data(self, instrument_token, from_date, to_date, interval):
            import random
            data = []
            price = 2800

            num_candles = 500
            for i in range(num_candles):
                current_date = from_date + timedelta(days=i)
                if current_date.weekday() >= 5:
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
                price = close

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
    market_data_timer.setInterval(100)
    mock_ltp = 0.0


    def simulate_live_data():
        global mock_ltp
        if chart_widget.current_symbol and chart_widget.current_instrument_token:
            if mock_ltp == 0.0 and chart_widget.current_ltp != 0.0:
                mock_ltp = chart_widget.current_ltp
            elif mock_ltp != 0.0:
                change = random.uniform(-1.0, 1.0)
                mock_ltp += change
                mock_ltp = max(1.0, mock_ltp)

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