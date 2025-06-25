# swing_trader/widgets/canvas_candlestick_chart.py

import logging
import json
import os
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Dict, Optional, Any

import pandas as pd
from PySide6.QtCore import Signal, Slot, QThread, Qt, QTimer, QObject
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                               QStackedWidget, QLabel, QPushButton, QProgressBar,
                               QFrame, QMessageBox, QColorDialog, QDialog,
                               QFormLayout, QSpinBox, QComboBox, QMenu,
                               QTextEdit, QDialogButtonBox)
from PySide6.QtGui import QFont, QKeySequence, QShortcut, QColor, QAction
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


class TextNoteDialog(QDialog):
    """Custom dialog for entering text notes on the chart."""

    def __init__(self, parent=None, text="", color="#FFFFFF", size=12):  # Modify signature
        super().__init__(parent)
        self.setWindowTitle("Add / Edit Text Note")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setMinimumSize(300, 150)

        self.text = text
        self.color = color
        self.size = size

        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.text_edit = QTextEdit()
        self.text_edit.setText(self.text)
        layout.addWidget(self.text_edit)

        options_layout = QHBoxLayout()
        self.color_button = QPushButton("Color")
        self.color_button.clicked.connect(self._choose_color)
        self.color_button.setStyleSheet(f"background-color: {self.color};")
        options_layout.addWidget(self.color_button)

        self.size_spinbox = QSpinBox()
        self.size_spinbox.setRange(8, 24)
        self.size_spinbox.setValue(self.size)
        self.size_spinbox.setSuffix("px")
        options_layout.addWidget(self.size_spinbox)
        layout.addLayout(options_layout)

        buttons = QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        button_box = QDialogButtonBox(buttons)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _choose_color(self):
        color = QColorDialog.getColor(QColor(self.color), self, "Choose Text Color")
        if color.isValid():
            self.color = color.name()
            self.color_button.setStyleSheet(f"background-color: {self.color};")

    def accept(self):
        self.text = self.text_edit.toPlainText()
        self.size = self.size_spinbox.value()
        super().accept()

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #2c2c2c;
                border: 1px solid #444;
            }
            QTextEdit {
                background-color: #333;
                color: #f0f0f0;
                border: 1px solid #555;
            }
            QPushButton, QSpinBox {
                background-color: #383838;
                color: #f0f0f0;
                border: 1px solid #505050;
                padding: 5px;
            }
        """)


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
                    state["drawings"] = {"lines": [], "rectangles": [], "notes": [], "horizontal_lines": []}
                else:
                    # Ensure all required drawing types exist
                    for draw_type in ["lines", "rectangles", "notes", "horizontal_lines"]:
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

                # Ensure drawing structure is valid
                if "drawings" not in state:
                    state["drawings"] = {"lines": [], "rectangles": [], "notes": [], "horizontal_lines": []}
                elif not isinstance(state["drawings"], dict):
                    state["drawings"] = {"lines": [], "rectangles": [], "notes": [], "horizontal_lines": []}
                else:
                    # Ensure all required drawing types exist
                    for draw_type in ["lines", "rectangles", "notes", "horizontal_lines"]:
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
            "drawings": {"lines": [], "rectangles": [], "notes": [], "horizontal_lines": []},
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
            # Default settings if a file doesn't exist
            return {
                "candle_width": 3,
                "candle_spacing": 3,
                "default_visible_candles": 100,
                "up_candle_color": "#26a69a",
                "down_candle_color": "#ef5350"
            }
        except Exception as e:
            logger.error(f"Failed to load global chart settings: {e}")
            # Return default settings on error
            return {
                "candle_width": 3,
                "candle_spacing": 3,
                "default_visible_candles": 100,
                "up_candle_color": "#26a69a",
                "down_candle_color": "#ef5350"
            }

    def save_last_viewed_symbol(self, symbol: str, interval: str):
        """Save the last viewed symbol and interval"""
        try:
            last_viewed_file = os.path.join(self.storage_dir, "last_viewed_symbol.json")
            last_viewed_data = {
                "symbol": symbol,
                "interval": interval,
                "timestamp": datetime.now().isoformat()
            }

            with open(last_viewed_file, 'w') as f:
                json.dump(last_viewed_data, f, indent=2)

            logger.info(f"Saved last viewed symbol: {symbol} ({interval})")
        except Exception as e:
            logger.error(f"Failed to save last viewed symbol: {e}")

    def load_last_viewed_symbol(self) -> Dict[str, str]:
        """Load the last viewed symbol and interval"""
        try:
            last_viewed_file = os.path.join(self.storage_dir, "last_viewed_symbol.json")

            if os.path.exists(last_viewed_file):
                with open(last_viewed_file, 'r') as f:
                    data = json.load(f)

                # Return symbol and interval, or empty dict if invalid
                if isinstance(data, dict) and "symbol" in data and "interval" in data:
                    logger.info(f"Loaded last viewed symbol: {data['symbol']} ({data['interval']})")
                    return {
                        "symbol": data["symbol"],
                        "interval": data["interval"]
                    }

            logger.info("No last viewed symbol found")
            return {}
        except Exception as e:
            logger.error(f"Failed to load last viewed symbol: {e}")
            return {}

    def clear_last_viewed_symbol(self):
        """Clear the last viewed symbol (useful for logout/reset)"""
        try:
            last_viewed_file = os.path.join(self.storage_dir, "last_viewed_symbol.json")
            if os.path.exists(last_viewed_file):
                os.remove(last_viewed_file)
            logger.info("Cleared last viewed symbol")
        except Exception as e:
            logger.error(f"Failed to clear last viewed symbol: {e}")

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
            if self.interval == 'week':
                days_back = 365 * 5  # 5 years for weekly
            elif self.interval == 'month':
                days_back = 365 * 20  # 20 years for monthly
            else:
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


class ChartBridge(QObject):
    """
    Bridge to allow JavaScript in QWebEngineView to communicate with Python.
    """
    drawings_changed = Signal(str)
    visible_candle_count_changed = Signal(int)
    chart_ready = Signal()
    webChannelInitialized = False
    alert_creation_requested = Signal(str)
    order_dialog_requested = Signal(str)
    text_note_requested = Signal(str)
    text_note_edit_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

    @Slot(str)
    def notify_drawings_changed(self, drawings_json: str):
        """Receives drawing data as a JSON string from JavaScript."""
        try:
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
        self.chart_ready.emit()

    @Slot(str)
    def create_alert_from_chart(self, alert_json: str):
        """Receives alert creation request as JSON from JS"""
        logger.info(f"Received alert creation request from chart: {alert_json}")
        self.alert_creation_requested.emit(alert_json)

    @Slot(str)
    def show_order_dialog_from_chart(self, order_data_json: str):
        """Receives request to show order dialog from JS"""
        logger.info(f"Received order dialog request from chart: {order_data_json}")
        self.order_dialog_requested.emit(order_data_json)

    @Slot(str)
    def request_text_note_dialog(self, mouse_pos_json):
        self.text_note_requested.emit(mouse_pos_json)


class CandlestickChart(QWidget):
    """Professional candlestick chart with fixed drawing persistence"""
    symbol_loaded = Signal(str)  # Emits when a new symbol is loaded
    order_button_clicked = Signal(str, float)
    alert_creation_requested = Signal(str)
    order_dialog_requested = Signal(str)
    data_request_for_symbol = Signal(str)

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
        self.current_visible_candle_count: int = self.global_chart_settings["default_visible_candles"]

        # Chart rendering properties
        self._current_candle_width: int = self.global_chart_settings["candle_width"]
        self._current_candle_spacing: int = self.global_chart_settings["candle_spacing"]
        self._current_up_color: str = self.global_chart_settings["up_candle_color"]
        self._current_down_color: str = self.global_chart_settings["down_candle_color"]

        # EMA data storage
        self.ema_data = {'ema10': [], 'ema20': [], 'ema50': []}
        self.current_adr: Dict[str, float] = {"value": 0.0, "percent": 0.0}
        self.percentage_changes: Dict[str, float] = {}

        # Drawing state
        self.current_drawing_color = "#FF0000"
        self.current_line_width = 1

        # QtWebChannel setup
        self.chart_bridge = ChartBridge(parent=self)
        self.chart_bridge.drawings_changed.connect(self._on_drawings_changed_from_js)
        self.chart_bridge.visible_candle_count_changed.connect(self._on_zoom_changed_from_js)
        self.chart_bridge.chart_ready.connect(self._on_js_chart_fully_ready)
        self.chart_bridge.alert_creation_requested.connect(self._on_alert_creation_requested)
        self.chart_bridge.order_dialog_requested.connect(self._on_order_dialog_requested)
        self.chart_bridge.text_note_requested.connect(self._open_text_note_dialog)
        self.chart_bridge.text_note_edit_requested.connect(self._open_text_note_dialog_for_edit)

        # UI components
        self.chart_view: Optional[QWebEngineView] = None
        self.channel: Optional[QWebChannel] = None
        self.timeframe_dropdown: Optional[QComboBox] = None
        self.drawing_tools_button: Optional[QPushButton] = None
        self.auto_scale_btn: Optional[QPushButton] = None
        self.refresh_button: Optional[QPushButton] = None
        self.settings_btn: Optional[QPushButton] = None
        self.order_btn: Optional[QPushButton] = None
        self.color_btn: Optional[QPushButton] = None
        self.line_width_btn: Optional[QPushButton] = None
        self.save_drawings_btn: Optional[QPushButton] = None
        self.clear_drawings_btn: Optional[QPushButton] = None

        self.should_auto_load_last_symbol = True

        self.current_position_info: Optional[Dict] = None
        self.active_alerts: List[Dict] = []

        self._setup_ui()
        self._apply_styles()
        self._setup_keyboard_shortcuts()

        QTimer.singleShot(100, self._initialize_chart)

    @Slot()
    def _on_js_chart_fully_ready(self):
        logger.info("JavaScript chart object reported ready via chart_ready signal.")
        self._apply_saved_drawings_and_zoom()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.combined_toolbar = QFrame()
        self.combined_toolbar.setObjectName("chartToolbar")
        self.combined_toolbar.setFixedHeight(40)

        toolbar_layout = QHBoxLayout(self.combined_toolbar)
        toolbar_layout.setContentsMargins(10, 5, 10, 5)
        toolbar_layout.setSpacing(2)

        self.symbol_info_label = QLabel("No Symbol Selected")
        self.symbol_info_label.setObjectName("symbolInfoLabel")

        font = QFont()
        font.setBold(True)
        self.symbol_info_label.setFont(font)

        # Make background transparent
        self.symbol_info_label.setStyleSheet("""
            QLabel#symbolInfoLabel {
                background-color: transparent;
            }
        """)

        toolbar_layout.addWidget(self.symbol_info_label)
        toolbar_layout.addStretch()

        # Timeframe Dropdown
        self.timeframe_dropdown = QComboBox()
        self.timeframe_dropdown.setObjectName("timeframeDropdown")
        # self.timeframe_dropdown.setFixedWidth(55)
        self.timeframe_dropdown.setFixedHeight(30)
        self.timeframe_dropdown.setStyleSheet("""
            QComboBox::drop-down {
                width: 0px;
                border: none;
            }
            QComboBox {
                padding-right: 0px;
                padding-left: 2px;


            }
        """)
        timeframes = [
            ("1 Min", "minute"), ("3 Min", "3minute"), ("5 Min", "5minute"),
            ("15 Min", "15minute"), ("30 Min", "30minute"), ("1 Hr", "60minute"),
            ("1 Day", "day"), ("1 W", "week"), ("1 M", "month")
        ]
        self.timeframe_dropdown.view().setMinimumWidth(80)

        for display, interval in timeframes:
            self.timeframe_dropdown.addItem(display, interval)
        self.timeframe_dropdown.setCurrentText("1 Day")
        self.timeframe_dropdown.activated.connect(self._on_timeframe_selected)
        toolbar_layout.addWidget(self.timeframe_dropdown)

        # After self.color_btn
        self.measure_tool_btn = QPushButton("📏")
        self.measure_tool_btn.setObjectName("controlButton")
        self.measure_tool_btn.setFixedSize(30, 30)
        self.measure_tool_btn.setToolTip("Measuring Tool (Ctrl+M)")
        self.measure_tool_btn.setCheckable(True)
        self.measure_tool_btn.clicked.connect(self._toggle_measure_tool)
        toolbar_layout.addWidget(self.measure_tool_btn)

        # Drawing Tools Button
        self.drawing_tools_button = QPushButton("Drawing Tools")
        self.drawing_tools_button.setObjectName("drawingToolsButton")
        self.drawing_tools_button.setFixedSize(110, 30)
        self.drawing_tools_button.setToolTip("Drawing Tools")
        drawing_menu = QMenu(self)
        drawing_menu.setObjectName("drawingMenu")
        drawing_tools = [
            ("/", "line", "Trend Line"),
            ("-", "horizontal_line", "Horizontal Line"),
            ("→", "horizontal_ray", "Horizontal Ray"),
            ("➚", "arrow_line", "Arrow Line"),
            ("T", "note", "Text Note"),
            ("□", "rectangle", "Rectangle")
        ]
        for icon, tool_id, tooltip in drawing_tools:
            action = QAction(icon, self)
            action.setData(tool_id)
            action.setToolTip(tooltip)
            action.triggered.connect(lambda checked=False, t=tool_id: self._toggle_drawing_tool(t, True))
            drawing_menu.addAction(action)

        self.drawing_tools_button.setMenu(drawing_menu)
        toolbar_layout.addWidget(self.drawing_tools_button)

        # Color picker button
        self.color_btn = QPushButton("🎨")
        self.color_btn.setObjectName("controlButton")
        self.color_btn.setFixedSize(30, 30)
        self.color_btn.setToolTip("Change drawing color")
        self.color_btn.clicked.connect(self._choose_drawing_color)
        toolbar_layout.addWidget(self.color_btn)

        # Other buttons
        self.order_btn = QPushButton("Order")
        self.order_btn.setObjectName("orderButton")
        self.order_btn.setFixedSize(70, 30)
        self.order_btn.clicked.connect(self._on_order_button_clicked)
        toolbar_layout.addWidget(self.order_btn)

        self.auto_scale_btn = QPushButton("A")
        self.auto_scale_btn.setObjectName("controlButton")
        self.auto_scale_btn.setFixedSize(30, 30)
        self.auto_scale_btn.setToolTip("Auto Scale (Ctrl+A)")
        self.auto_scale_btn.clicked.connect(self._auto_scale_chart)
        toolbar_layout.addWidget(self.auto_scale_btn)

        self.refresh_button = QPushButton("⟳")
        self.refresh_button.setObjectName("refreshButton")
        self.refresh_button.setFixedSize(30, 30)
        self.refresh_button.setToolTip("Refresh Data (F5)")
        self.refresh_button.clicked.connect(self._force_refresh)
        toolbar_layout.addWidget(self.refresh_button)

        self.settings_btn = QPushButton("⚙️")
        self.settings_btn.setObjectName("controlButton")
        self.settings_btn.setFixedSize(30, 30)
        self.settings_btn.setToolTip("Chart Settings")
        self.settings_btn.clicked.connect(self._open_settings_dialog)
        toolbar_layout.addWidget(self.settings_btn)

        main_layout.addWidget(self.combined_toolbar)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumHeight(3)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        main_layout.addWidget(self.progress_bar)

        self.stacked_widget = QStackedWidget()
        main_layout.addWidget(self.stacked_widget)

        self.loading_widget = self._create_loading_widget()
        self.stacked_widget.addWidget(self.loading_widget)

        self.error_widget = self._create_error_widget()
        self.stacked_widget.addWidget(self.error_widget)

        self.chart_container = QWidget()
        self.chart_layout = QVBoxLayout(self.chart_container)
        self.chart_layout.setContentsMargins(0, 0, 0, 0)
        self.stacked_widget.addWidget(self.chart_container)

        self._set_state(ChartState.IDLE)

    def _on_timeframe_selected(self, index):
        interval = self.timeframe_dropdown.itemData(index)
        self._change_timeframe(interval)

    @Slot(str)
    def _open_text_note_dialog_for_edit(self, note_json: str):
        """Opens a dialog to edit an existing text note."""
        note_data = json.loads(note_json)
        dialog = TextNoteDialog(self, text=note_data.get('text'), color=note_data.get('color'),
                                size=note_data.get('size'))
        if dialog.exec():
            # Update the note data with new values but keep the original ID and position
            note_data['text'] = dialog.text
            note_data['color'] = dialog.color
            note_data['size'] = dialog.size
            if self.chart_view:
                js_code = f"if (window.chart) window.chart.updateTextNote({json.dumps(note_data)});"
                self.chart_view.page().runJavaScript(js_code)

    def _toggle_measure_tool(self, checked: bool):
        # Deactivate other drawing tools when measure tool is activated
        if checked:
            self._toggle_drawing_tool(None, False)
        if self.chart_view and self.current_state == ChartState.LOADED:
            js_code = f"if (window.chart) window.chart.setDrawingTool('measure', {str(checked).lower()});"
            self.chart_view.page().runJavaScript(js_code)

    def _toggle_drawing_tool(self, tool_id: str, checked: bool):
        if self.chart_view and self.current_state == ChartState.LOADED:
            js_code = f"""
            if (window.chart) {{
                window.chart.setDrawingTool('{tool_id}', {str(checked).lower()},
                    '{self.current_drawing_color}', {self.current_line_width});
            }}"""
            self.chart_view.page().runJavaScript(js_code)

    def _choose_drawing_color(self):
        color = QColorDialog.getColor(QColor(self.current_drawing_color), self, "Choose Drawing Color")
        if color.isValid():
            self.current_drawing_color = color.name()
            # The color button icon could be updated here if desired
            if self.chart_view:
                js_code = f"if (window.chart) window.chart.updateDrawingStyle('{self.current_drawing_color}', {self.current_line_width});"
                self.chart_view.page().runJavaScript(js_code)

    def _save_drawings(self):
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
        })();"""

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
                if current_state.get("drawings") == drawings_data: return
                current_state["drawings"] = drawings_data
                self.drawing_storage.save_state(self.current_symbol, self.current_interval, current_state)
            except (json.JSONDecodeError, Exception) as e:
                logger.error(f"Error saving drawings from JS callback: {e}")

    @Slot(int)
    def _on_zoom_changed_from_js(self, visible_candle_count: int):
        if self.current_symbol and self.current_state == ChartState.LOADED:
            self.current_visible_candle_count = visible_candle_count
            try:
                current_state = self.drawing_storage.load_state(self.current_symbol, self.current_interval)
                current_state["visible_candle_count"] = visible_candle_count
                self.drawing_storage.save_state(self.current_symbol, self.current_interval, current_state)
            except Exception as e:
                logger.error(f"Error saving zoom from JS callback: {e}")

    def _clear_drawings(self):
        if self.chart_view:
            js_code = "if (window.chart) window.chart.clearAllDrawings();"
            self.chart_view.page().runJavaScript(js_code)

    def _create_loading_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label = QLabel("Loading chart data...")
        self.loading_label.setObjectName("loadingLabel")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.loading_label)
        return widget

    def _create_error_widget(self) -> QWidget:
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
        QShortcut(QKeySequence("F5"), self).activated.connect(self._force_refresh)
        QShortcut(QKeySequence("Ctrl+A"), self).activated.connect(self._auto_scale_chart)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._save_drawings)
        QShortcut(QKeySequence("Delete"), self).activated.connect(self._delete_selected_drawing)
        QShortcut(QKeySequence("Ctrl+M"), self).activated.connect(self.measure_tool_btn.toggle)

    @Slot(object)
    def update_position_data(self, position_info: Optional[Dict]):
        """Receives position data (e.g., from a position manager) and updates the chart."""
        self.current_position_info = None
        if self.current_symbol and position_info and position_info.get('tradingsymbol') == self.current_symbol:
            self.current_position_info = position_info

        if self.chart_view and self.current_state == ChartState.LOADED:
            js_code = f"if (window.chart) window.chart.updatePositionLine({json.dumps(self.current_position_info)});"
            self.chart_view.page().runJavaScript(js_code)

    @Slot(list)
    def update_alert_data(self, alerts: List[Dict]):
        """Receives active alert data for the current symbol and updates the chart."""
        self.active_alerts = alerts
        if self.chart_view and self.current_state == ChartState.LOADED:
            js_code = f"if (window.chart) window.chart.updateAlertLines({json.dumps(self.active_alerts)});"
            self.chart_view.page().runJavaScript(js_code)

    @Slot()
    def _delete_selected_drawing(self):
        if self.chart_view and self.current_state == ChartState.LOADED:
            js_code = "if (window.chart) window.chart.deleteSelectedDrawing();"
            self.chart_view.page().runJavaScript(js_code)
            logger.info("Requested deletion of selected drawing from JS.")
        else:
            logger.warning("Cannot delete drawing: chart not loaded.")

    def _initialize_chart(self):
        self._create_chart_view()

    def _create_chart_view(self):
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
        self.current_state = state
        configs = {
            ChartState.IDLE: {'widget_index': 2, 'buttons_enabled': True},
            ChartState.LOADING: {'widget_index': 0, 'buttons_enabled': False},
            ChartState.ERROR: {'widget_index': 1, 'buttons_enabled': True},
            ChartState.LOADED: {'widget_index': 2, 'buttons_enabled': True}
        }
        config = configs.get(state, configs[ChartState.IDLE])
        if self.stacked_widget.currentIndex() != config['widget_index']:
            self.stacked_widget.setCurrentIndex(config['widget_index'])

        self.timeframe_dropdown.setEnabled(config['buttons_enabled'])
        self.drawing_tools_button.setEnabled(config['buttons_enabled'] and self.current_symbol != "")

        if self.color_btn: self.color_btn.setEnabled(config['buttons_enabled'] and self.current_symbol != "")
        if self.refresh_button: self.refresh_button.setEnabled(config['buttons_enabled'])
        if self.auto_scale_btn: self.auto_scale_btn.setEnabled(config['buttons_enabled'])
        if self.settings_btn: self.settings_btn.setEnabled(config['buttons_enabled'])
        if self.order_btn: self.order_btn.setEnabled(config['buttons_enabled'] and self.current_symbol != "")

    def set_instrument_list(self, instruments: List[Dict[str, Any]]):
        """Set the instrument list and attempt to autoload last viewed symbol"""
        try:
            self.instrument_map = {inst['tradingsymbol']: inst for inst in instruments if
                                   all(k in inst for k in ['tradingsymbol', 'instrument_token'])}
            logger.info(f"Loaded {len(self.instrument_map)} instruments")

            # Attempt to autoload last viewed symbol if enabled
            if self.should_auto_load_last_symbol:
                self._attempt_auto_load_last_symbol()

        except Exception as e:
            logger.error(f"Error setting instrument list: {e}")

    def _set_timeframe_dropdown(self, interval: str):
        """Set the timeframe dropdown to the specified interval"""
        try:
            for i in range(self.timeframe_dropdown.count()):
                if self.timeframe_dropdown.itemData(i) == interval:
                    self.timeframe_dropdown.setCurrentIndex(i)
                    self.current_interval = interval
                    logger.info(f"Set timeframe to: {interval}")
                    break
        except Exception as e:
            logger.error(f"Error setting timeframe dropdown: {e}")

    @Slot(str)
    def on_search(self, symbol: Optional[str] = None):
        """Enhanced on_search method that saves the symbol"""
        if not symbol or symbol not in self.instrument_map:
            if symbol:
                self._show_error(f"Symbol '{symbol}' not found")
            return

        # Save current state if switching symbols
        if self.current_symbol and self.chart_view:
            self._save_current_state_sync()

        self._stop_current_operations()
        if self.chart_view:
            self.chart_view.page().runJavaScript("if (window.chart) window.chart.setDrawingTool(null, false);")

        self.current_symbol = symbol
        self.current_instrument_token = self.instrument_map[symbol]['instrument_token']
        saved_state = self.drawing_storage.load_state(self.current_symbol, self.current_interval)
        self.current_visible_candle_count = saved_state.get("visible_candle_count", self.global_chart_settings[
            "default_visible_candles"])
        self._set_state(ChartState.IDLE)
        self._load_chart_data()
        self.symbol_loaded.emit(symbol)

        # Save this as the last viewed symbol
        self.drawing_storage.save_last_viewed_symbol(symbol, self.current_interval)

    def _change_timeframe(self, interval: str):
        """Enhanced timeframe change that also saves the interval"""
        if self.current_interval == interval or not self.current_symbol:
            return

        if self.current_symbol and self.chart_view:
            self._save_current_state_sync()

        self.current_interval = interval
        saved_state = self.drawing_storage.load_state(self.current_symbol, self.current_interval)
        self.current_visible_candle_count = saved_state.get("visible_candle_count",
                                                            self.global_chart_settings["default_visible_candles"])
        self._load_chart_data()

        # Save the updated interval as well
        if self.current_symbol:
            self.drawing_storage.save_last_viewed_symbol(self.current_symbol, self.current_interval)

    def disable_auto_load(self):
        """Disable autoloading of last symbol (useful for programmatic control)"""
        self.should_auto_load_last_symbol = False

    def enable_auto_load(self):
        """Enable auto-loading of last symbol"""
        self.should_auto_load_last_symbol = True

    def _save_current_state_sync(self):
        if not self.chart_view or not self.current_symbol: return
        js_code = """
        (function() {
            if (window.chart && window.chart.getAllDrawings && window.chart.getVisibleCandleCount) {
                return {
                    drawings: window.chart.getAllDrawings(),
                    visible_candle_count: window.chart.getVisibleCandleCount()
                };
            }
            return null;
        })();"""

        def sync_save_callback(state_data):
            if state_data and self.current_symbol:
                self.drawing_storage.save_state(self.current_symbol, self.current_interval, state_data)
                logger.info(f"Sync save completed for {self.current_symbol}")

        self.chart_view.page().runJavaScript(js_code, sync_save_callback)

    def _load_chart_data(self, force_refresh: bool = False):
        if not self.current_symbol or self.current_symbol not in self.instrument_map: return
        if force_refresh:
            cache_key = f"{self.current_symbol}_{self.current_interval}"
            self.data_cache._cache.pop(cache_key, None)
        self._stop_current_operations()
        self._set_state(ChartState.LOADING)
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        instrument = self.instrument_map[self.current_symbol]
        instrument_token = instrument['instrument_token']
        self.data_loader_thread = ChartDataLoaderThread(self.data_fetcher, instrument_token, self.current_symbol,
                                                        self.current_interval, self.data_cache)
        self.data_loader_thread.data_loaded.connect(self._on_data_loaded)
        self.data_loader_thread.load_error.connect(self._on_load_error)
        self.data_loader_thread.load_progress.connect(self._on_load_progress)
        self.data_loader_thread.finished.connect(self._on_thread_finished)
        self.data_loader_thread.start()

    # Add this to your CandlestickChart class for better user feedback

    def _update_symbol_info_with_status(self, status_text: str = ""):
        """Update the symbol info label with optional status text."""
        try:
            if self.current_symbol:
                base_text = f"{self.current_symbol}"
                if self.current_ltp > 0:
                    base_text += f" • ₹{self.current_ltp:.2f}"

                if status_text:
                    base_text += f" • {status_text}"

                self.symbol_info_label.setText(base_text)
            else:
                self.symbol_info_label.setText(status_text or "No Symbol Selected")
        except Exception as e:
            logger.error(f"Error updating symbol info: {e}")

    def _attempt_auto_load_last_symbol(self):
        """Enhanced autoload with status feedback"""
        try:
            # Only autoload if no symbol is currently loaded
            if self.current_symbol:
                logger.info("Symbol already loaded, skipping auto-load")
                return

            # Show loading status
            self._update_symbol_info_with_status("Loading last viewed symbol...")

            # Load last viewed symbol info
            last_viewed = self.drawing_storage.load_last_viewed_symbol()
            if not last_viewed:
                logger.info("No last viewed symbol to auto-load")
                self._update_symbol_info_with_status("No previous symbol found")
                QTimer.singleShot(3000, lambda: self._update_symbol_info_with_status())
                return

            symbol = last_viewed.get("symbol")
            interval = last_viewed.get("interval", "day")

            # Validate that the symbol exists in our instrument map
            if symbol and symbol in self.instrument_map:
                logger.info(f"Auto-loading last viewed symbol: {symbol} ({interval})")
                self._update_symbol_info_with_status(f"Restoring {symbol}...")

                # Set the timeframe dropdown
                self._set_timeframe_dropdown(interval)

                # Load the symbol (this will trigger the chart loading)
                QTimer.singleShot(500, lambda: self.on_search(symbol))
            else:
                if symbol:
                    logger.warning(f"Last viewed symbol '{symbol}' not found in instrument list")
                    self._update_symbol_info_with_status(f"Symbol '{symbol}' not available")
                else:
                    logger.info("No valid last viewed symbol found")
                    self._update_symbol_info_with_status("No valid previous symbol")

                # Clear the status message after a delay
                QTimer.singleShot(3000, lambda: self._update_symbol_info_with_status())

        except Exception as e:
            logger.error(f"Error auto-loading last symbol: {e}")
            self._update_symbol_info_with_status("Failed to restore previous symbol")
            QTimer.singleShot(3000, lambda: self._update_symbol_info_with_status())

    # Add to your _on_data_loaded method
    @Slot(pd.DataFrame, str)
    def _on_data_loaded(self, df: pd.DataFrame, cache_key: str):
        try:
            if df.empty:
                self._show_error("No data available")
                return
            self.last_df = df.copy()
            self._calculate_metrics(self.last_df)
            self._render_chart(df)
            self._update_symbol_info(df)  # This will clear any status messages
            self._set_state(ChartState.LOADED)
            self.data_request_for_symbol.emit(self.current_symbol)
            logger.info(f"Chart loaded: {self.current_symbol} ({len(df)} candles)")
        except Exception as e:
            logger.error(f"Error processing loaded data: {e}")
            self._show_error(f"Failed to render chart: {str(e)}")

    def _calculate_metrics(self, df: pd.DataFrame):
        if 'close' not in df.columns or df.empty:
            self.ema_data = {'ema10': [], 'ema20': [], 'ema50': []}
            self.current_adr = {"value": 0.0, "percent": 0.0}
            self.percentage_changes = {}
            return
        df['time_ms'] = df['time'].apply(lambda x: int(x.timestamp() * 1000))
        df['ema10'] = df['close'].ewm(span=10, adjust=False).mean()
        df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['daily_range'] = df['high'] - df['low']
        adr_period = 14
        if len(df) >= adr_period:
            current_adr_value = df['daily_range'].iloc[-adr_period:].mean()
            last_close = df['close'].iloc[-1] if not df.empty else 0
            current_adr_percent = (current_adr_value / last_close) * 100 if last_close != 0 else 0
            self.current_adr = {"value": float(current_adr_value), "percent": float(current_adr_percent)}
        else:
            self.current_adr = {"value": 0.0, "percent": 0.0}
        self.percentage_changes = {}
        last_close_price = df['close'].iloc[-1] if not df.empty else 0
        periods = {"Weekly": 5, "Monthly": 22, "3M": 66, "6M": 132, "1Y": 252}
        for label, days_back in periods.items():
            if len(df) > days_back:
                past_close_price = df['close'].iloc[-1 - days_back]
                change_percent = ((
                                          last_close_price - past_close_price) / past_close_price) * 100 if past_close_price != 0 else 0
                self.percentage_changes[label] = float(change_percent)
            else:
                self.percentage_changes[label] = 0.0
        self.ema_data['ema10'] = \
            df[['time_ms', 'ema10']].dropna().rename(columns={'time_ms': 'time', 'ema10': 'value'}).to_dict(
                orient='records')
        self.ema_data['ema20'] = \
            df[['time_ms', 'ema20']].dropna().rename(columns={'time_ms': 'time', 'ema20': 'value'}).to_dict(
                orient='records')
        self.ema_data['ema50'] = \
            df[['time_ms', 'ema50']].dropna().rename(columns={'time_ms': 'time', 'ema50': 'value'}).to_dict(
                orient='records')
        logger.debug(
            f"Calculated EMAs, ADR ({self.current_adr['value']:.2f}, {self.current_adr['percent']:.2f}%) and percentage changes.")

    @Slot()
    def _apply_saved_drawings_and_zoom(self):
        try:
            saved_state = self.drawing_storage.load_state(self.current_symbol, self.current_interval)
            drawings = saved_state.get("drawings", {"lines": [], "rectangles": [], "notes": [], "horizontal_lines": []})
            initial_zoom = saved_state.get("visible_candle_count",
                                           self.global_chart_settings["default_visible_candles"])
            if self.current_state == ChartState.LOADED and self.current_symbol and self.chart_view and self.chart_bridge.webChannelInitialized:
                logger.info(
                    f"Applied {self.drawing_storage._count_drawings(drawings)} saved drawings and set zoom to {initial_zoom} for {self.current_symbol}")
            else:
                logger.warning(
                    f"Skipping _apply_saved_drawings_and_zoom: Chart not fully ready or no symbol. State: {self.current_state}, Symbol: {self.current_symbol}, JS Bridge ready: {getattr(self.chart_bridge, 'webChannelInitialized', False)}")
        except Exception as e:
            logger.error(f"Error applying saved drawings and zoom: {e}")

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
            self.data_loader_thread.quit()
            self.data_loader_thread.wait(3000)
            if self.data_loader_thread.isRunning():
                logger.warning("ChartDataLoaderThread is still running after wait, terminating forcefully.")
                self.data_loader_thread.terminate()
            self.data_loader_thread.deleteLater()
            self.data_loader_thread = None

    @Slot(object)
    def update_live_data(self, live_data: Any):
        if self.current_state != ChartState.LOADED or not self.current_symbol:
            return

        if isinstance(live_data, list):
            for item in live_data:
                self._process_single_live_data_item(item)
        elif isinstance(live_data, dict):
            self._process_single_live_data_item(live_data)
        else:
            logger.error(f"Received malformed live_data (not a dict or list of dicts): {live_data}")

    def _process_single_live_data_item(self, data_item: Dict[str, Any]):
        trading_symbol = data_item.get('tradingsymbol')
        last_price = data_item.get('last_price')
        instrument_token = data_item.get('instrument_token')

        logger.debug(f"TICK DEBUG: symbol={trading_symbol}, ltp={last_price}, token={instrument_token}")
        logger.debug(
            f"CHART STATE: current_symbol={self.current_symbol}, current_token={self.current_instrument_token}")
        logger.debug(
            f"CHART READY: state={self.current_state}, bridge_ready={getattr(self.chart_bridge, 'webChannelInitialized', False)}")

        # Enhanced matching logic - check both symbol and token
        symbol_matches = trading_symbol == self.current_symbol
        token_matches = instrument_token == self.current_instrument_token

        # Debug logging
        logger.debug(
            f"Tick: {trading_symbol}={last_price}, token={instrument_token}, current_token={self.current_instrument_token}")

        if trading_symbol and last_price is not None and (symbol_matches or token_matches):
            self.current_ltp = float(last_price)
            self._update_symbol_info_live(self.current_ltp)

            # Enhanced chart readiness check
            if (self.chart_view and
                    self.current_state == ChartState.LOADED and
                    self.last_df is not None and
                    not self.last_df.empty and
                    getattr(self.chart_bridge, 'webChannelInitialized', False)):

                try:
                    last_candle_time = self.last_df['time'].iloc[-1]
                    now = datetime.now()
                    new_candle = False

                    # More robust interval checking
                    if self.current_interval == "minute":
                        new_candle = now.minute != last_candle_time.minute
                    elif self.current_interval == "3minute":
                        new_candle = (now.hour * 60 + now.minute) // 3 != (
                                    last_candle_time.hour * 60 + last_candle_time.minute) // 3
                    elif self.current_interval == "5minute":
                        new_candle = (now.hour * 60 + now.minute) // 5 != (
                                    last_candle_time.hour * 60 + last_candle_time.minute) // 5
                    elif self.current_interval == "15minute":
                        new_candle = (now.hour * 60 + now.minute) // 15 != (
                                    last_candle_time.hour * 60 + last_candle_time.minute) // 15
                    elif self.current_interval == "30minute":
                        new_candle = (now.hour * 60 + now.minute) // 30 != (
                                    last_candle_time.hour * 60 + last_candle_time.minute) // 30
                    elif self.current_interval == "60minute":
                        new_candle = now.hour != last_candle_time.hour
                    elif self.current_interval == "day":
                        new_candle = now.date() != last_candle_time.date()
                    elif self.current_interval == "week":
                        new_candle = now.isocalendar()[1] != last_candle_time.isocalendar()[1]
                    elif self.current_interval == "month":
                        new_candle = now.month != last_candle_time.month or now.year != last_candle_time.year

                    if new_candle:
                        new_candle_data = {
                            'time': int(now.timestamp() * 1000),
                            'open': last_price,
                            'high': last_price,
                            'low': last_price,
                            'close': last_price,
                            'volume': 0
                        }
                        js_code = f"if (window.chart && window.chart.addNewCandle) window.chart.addNewCandle({json.dumps(new_candle_data)});"
                        logger.debug(f"Adding new candle for {trading_symbol}: {last_price}")
                    else:
                        js_code = f"if (window.chart && window.chart.updateLivePrice) window.chart.updateLivePrice({self.current_ltp});"
                        logger.debug(f"Updating live price for {trading_symbol}: {last_price}")

                    self.chart_view.page().runJavaScript(js_code)

                except Exception as e:
                    logger.error(f"Error processing live data for {trading_symbol}: {e}")
                    # Fallback to simple price update
                    js_code = f"if (window.chart && window.chart.updateLivePrice) window.chart.updateLivePrice({self.current_ltp});"
                    self.chart_view.page().runJavaScript(js_code)
            else:
                logger.debug(f"Chart not ready for updates: view={bool(self.chart_view)}, state={self.current_state}, "
                             f"bridge_ready={getattr(self.chart_bridge, 'webChannelInitialized', False)}, "
                             f"has_data={self.last_df is not None and not self.last_df.empty if self.last_df is not None else False}")
        else:
            if trading_symbol == self.current_symbol:
                logger.debug(
                    f"Skipping tick for {trading_symbol}: last_price={last_price}, token_match={token_matches}")

    def _update_symbol_info_live(self, ltp: float):
        try:
            self.symbol_info_label.setText(f"{self.current_symbol} • ₹{ltp:.2f}")
        except Exception as e:
            logger.error(f"Error updating live symbol info: {e}")

    @Slot(str)
    def _open_text_note_dialog(self, mouse_pos_json: str):
        mouse_pos = json.loads(mouse_pos_json)
        dialog = TextNoteDialog(self)
        if dialog.exec():
            note = {
                "text": dialog.text,
                "color": dialog.color,
                "size": dialog.size,
                "x": mouse_pos['x'],
                "y": mouse_pos['y']
            }
            if self.chart_view:
                js_code = f"if (window.chart) window.chart.addTextNoteFromDialog({json.dumps(note)});"
                self.chart_view.page().runJavaScript(js_code)

    def _render_chart(self, df: pd.DataFrame):
        try:
            if not self.chart_view: self._create_chart_view()
            candlestick_data, volume_data = [], []
            for _, row in df.iterrows():
                timestamp = int(row['time'].timestamp() * 1000)
                candlestick_data.append(
                    {'time': timestamp, 'open': float(row['open']), 'high': float(row['high']),
                     'low': float(row['low']),
                     'close': float(row['close'])})
                volume_data.append({'time': timestamp, 'value': float(row['volume'])})
            saved_state = self.drawing_storage.load_state(self.current_symbol, self.current_interval)
            initial_drawings_json = json.dumps(
                saved_state.get("drawings", {"lines": [], "rectangles": [], "notes": [], "horizontal_lines": []}))
            initial_zoom = self.global_chart_settings["default_visible_candles"]
            html_content = self._create_fixed_chart_html(candlestick_data, volume_data, initial_zoom,
                                                         self._current_candle_width, self._current_candle_spacing,
                                                         self._current_up_color, self._current_down_color,
                                                         self.ema_data, self.current_adr, self.percentage_changes,
                                                         self.current_interval, self.current_symbol,
                                                         initial_drawings_json)
            self.chart_view.setHtml(html_content)
            logger.info(f"Chart rendered successfully for {self.current_symbol}")
        except Exception as e:
            logger.error(f"Chart rendering error: {e}")
            self._show_error(f"Failed to render chart: {str(e)}")

    def _create_fixed_chart_html(self, candlestick_data, volume_data,
                                 initial_visible_candle_count, initial_candle_width,
                                 initial_candle_spacing, up_candle_color, down_candle_color,
                                 ema_data: Dict[str, List[Dict]],
                                 current_adr: Dict[str, float],
                                 percentage_changes: Dict[str, float],
                                 current_interval: str,
                                 current_symbol: str,
                                 initial_drawings_json: str):
        candlestick_json = json.dumps(candlestick_data)
        volume_json = json.dumps(volume_data)
        ema_json = json.dumps(ema_data)
        adr_json = json.dumps(current_adr)
        percentage_changes_json = json.dumps(percentage_changes)
        current_interval_js = json.dumps(current_interval)
        current_symbol_js = json.dumps(current_symbol)
        safe_initial_drawings = json.dumps(
            json.loads(initial_drawings_json)) if isinstance(initial_drawings_json, str) else json.dumps(
            initial_drawings_json)
        try:
            json.loads(safe_initial_drawings)
        except (json.JSONDecodeError, TypeError):
            safe_initial_drawings = json.dumps({"lines": [], "rectangles": [], "notes": [], "horizontal_lines": []})
        qwebchannel_script_src = "qrc:///qtwebchannel/qwebchannel.js"

        html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Professional Trading Chart</title>
        <style>
            body {{ margin: 0; padding: 0; background-color: #0a0a0a; font-family: 'Segoe UI', sans-serif; overflow: hidden; }}
            #chartContainer {{ width: 100vw; height: 100vh; position: relative; }}
            #mainCanvas {{ background-color: #0a0a0a; cursor: crosshair; width: 100%; height: calc(100% - 15px); position: absolute; top: 0; left: 0; }}
            #info {{ position: absolute; top: 5px; left: 5px; color: #e0e0e0; font-size: 12px; pointer-events: none; z-index: 5; }}
            #metricsInfo {{ font-weight: bold; margin-bottom: 5px; color: #e0e0e0; }}
            #priceInfo {{ color: #00bfff; font-weight: bold; }}
            #timeSlider {{ position: absolute; bottom: 0; left: 0; width: 100%; height: 15px; background-color: #1a1a1a; border-top: 1px solid #333; display: flex; align-items: center; justify-content: center; overflow: hidden; user-select: none; z-index: 10; }}
            #sliderTrack {{ position: relative; height: 3px; background-color: #333; border-radius: 1.5px; width: calc(100% - 20px); margin: 0 10px; }}
            #sliderThumb {{ position: absolute; width: 50px; height: 10px; background-color: #0066cc; border: 1px solid #0080ff; border-radius: 2px; cursor: grab; display: flex; align-items: center; justify-content: center; color: transparent; font-size: 0; z-index: 12; }}
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
                            emaData, initialADR, percentageChanges, currentInterval, currentSymbol, initialDrawingsJson) {{
                    this.canvas = document.getElementById(canvasId);
                    this.ctx = this.canvas.getContext('2d');
                    this.data = data || [];
                    this.volumeData = volumeData || [];
                    this.width = 0; this.height = 0;
                    this.padding = {{ top: 30, right: 80, bottom: 30, left: 10 }};
                    this.rightBufferCandles = 5;
                    this.minPrice = 0; this.maxPrice = 0;
                    this.minVolume = 0; this.maxVolume = 0;
                    this.candleWidth = initialCandleWidth || 4;
                    this.candleSpacing = initialCandleSpacing || 2;
                    this.visibleCandleCount = initialVisibleCandleCount || 100;
                    this.viewPortEnd = Math.max(0, this.data.length - 1 + this.rightBufferCandles);
                    this.viewPortStart = Math.max(0, this.viewPortEnd - this.visibleCandleCount);
                    this.currentTool = null; this.isDrawing = false;
                    this.startPoint = null; this.endPoint = null;
                    this.drawingColor = '#FFD700'; this.lineWidth = 2;
                    this.drawings = this.initializeDrawings(initialDrawingsJson);
                    this.selectedDrawingId = null; this.activeContextMenu = null;
                    this.isDragging = false; this.lastMouseX = 0; this.lastMouseY = 0;
                    this.crosshairX = null; this.crosshairY = null;
                    this.livePrice = null; this.isUserZooming = false;
                    this.colors = {{ upCandle: upCandleColor || '#26a69a', downCandle: downCandleColor || '#ef5350', grid: '#1a1a1a', text: '#e0e0e0', volume: '#555', volumeUp: 'rgba(38, 166, 154, 0.3)', volumeDown: 'rgba(239, 83, 80, 0.3)', background: '#0a0a0a', crosshair: 'rgba(160, 192, 255, 0.4)', livePrice: '#00BFFF' }};
                    this.emaData = emaData || {{}};
                    this.currentADR = initialADR || {{}};
                    this.percentageChanges = percentageChanges || {{}};
                    this.currentInterval = currentInterval || 'day';
                    this.currentSymbol = currentSymbol || '';
                    this.isSliderDragging = false; this.sliderLastX = 0;
                    this.chartBridge = null; this.webChannelInitialized = false;
                    this.isLoadingState = false; this.notificationQueue = [];
                    this.notificationTimer = null;
                    this.positionInfo = null;
                    this.activeAlerts = [];
                    this.init();
                }}

                initializeDrawings(initialDrawingsJson) {{
                    const defaultDrawings = {{ lines: [], rectangles: [], notes: [], horizontal_lines: [], horizontal_rays: [], arrow_lines: [] }};
                    if (!initialDrawingsJson) return defaultDrawings;
                    try {{
                        let drawings = (typeof initialDrawingsJson === 'string') ? JSON.parse(initialDrawingsJson) : initialDrawingsJson;
                        if (drawings && typeof drawings === 'object') {{
                            return {{
                                lines: Array.isArray(drawings.lines) ? drawings.lines : [],
                                rectangles: Array.isArray(drawings.rectangles) ? drawings.rectangles : [],
                                notes: Array.isArray(drawings.notes) ? drawings.notes : [],
                                horizontal_lines: Array.isArray(drawings.horizontal_lines) ? drawings.horizontal_lines : [],
                                horizontal_rays: Array.isArray(drawings.horizontal_rays) ? drawings.horizontal_rays : [],
                                arrow_lines: Array.isArray(drawings.arrow_lines) ? drawings.arrow_lines : []
                            }};
                        }}
                    }} catch (error) {{ console.error('Error parsing initial drawings:', error); }}
                    return defaultDrawings;
                }}

                async init() {{
                    try {{
                        this.setupCanvas(); this.setupSlider(); this.calculateBounds();
                        this.setupEventListeners(); this.setupWebChannel();
                        this.draw(); this.updateSlider();
                        this.displayLatestCandleDetails(); this.updateMetricsDisplay();
                        console.log('Chart initialized with', this.data.length, 'candles');
                    }} catch (error) {{ console.error('Error initializing chart:', error); }}
                }}

                setupWebChannel() {{
                    const initWebChannel = () => {{
                        try {{
                            if (typeof QWebChannel !== 'undefined' && window.qt && window.qt.webChannelTransport) {{
                                new QWebChannel(qt.webChannelTransport, (channel) => {{
                                    if (channel.objects && channel.objects.chartBridge) {{
                                        this.chartBridge = channel.objects.chartBridge;
                                        this.webChannelInitialized = true;
                                        console.log("QWebChannel ChartBridge loaded.");
                                        setTimeout(() => {{
                                            try {{
                                                if (this.chartBridge && typeof this.chartBridge.set_web_channel_initialized === 'function') {{
                                                    this.chartBridge.set_web_channel_initialized();
                                                }}
                                            }} catch (e) {{ console.warn("Error calling set_web_channel_initialized:", e); }}
                                        }}, 100);
                                        this.processNotificationQueue();
                                    }} else {{ setTimeout(initWebChannel, 200); }}
                                }});
                            }} else {{ setTimeout(initWebChannel, 100); }}
                        }} catch (error) {{ console.error("Error setting up WebChannel:", error); setTimeout(initWebChannel, 500); }}
                    }};
                    initWebChannel();
                    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initWebChannel);
                    else initWebChannel();
                }}

                queueNotification(type, data) {{
                    this.notificationQueue.push({{ type, data, timestamp: Date.now() }});
                    if (this.notificationTimer) clearTimeout(this.notificationTimer);
                    this.notificationTimer = setTimeout(() => this.processNotificationQueue(), 100);
                }}

                processNotificationQueue() {{
                    if (!this.webChannelInitialized || this.isLoadingState || this.notificationQueue.length === 0) return;
                    const latestNotifications = new Map();
                    this.notificationQueue.forEach(n => latestNotifications.set(n.type, n));
                    latestNotifications.forEach((notification, type) => {{
                        try {{
                            if (type === 'drawings' && this.chartBridge && typeof this.chartBridge.notify_drawings_changed === 'function') {{
                                setTimeout(() => {{
                                    try {{ this.chartBridge.notify_drawings_changed(JSON.stringify(notification.data)); }}
                                    catch (e) {{ console.warn("Error in drawings callback:", e); }}
                                }}, 50);
                            }} else if (type === 'zoom' && this.chartBridge && typeof this.chartBridge.notify_visible_candle_count_changed === 'function') {{
                                if (this.isUserZooming) {{
                                    if (this.updateGlobalSettings) this.updateGlobalSettings(notification.data);
                                    setTimeout(() => {{
                                        try {{ this.chartBridge.notify_visible_candle_count_changed(notification.data); }}
                                        catch (e) {{ console.warn("Error in zoom callback:", e); }}
                                    }}, 50);
                                }}
                            }}
                        }} catch (error) {{ console.error(`Error processing ${{type}} notification:`, error); }}
                    }});
                    this.notificationQueue = [];
                }}

                notifyDrawingsChange() {{ if (!this.isLoadingState) this.queueNotification('drawings', this.drawings); }}
                notifyZoomChange() {{ if (!this.isLoadingState && this.isUserZooming) this.queueNotification('zoom', this.visibleCandleCount); }}

                loadDrawings(drawingsData) {{
                    if (this.isLoadingState) return;
                    try {{
                        this.isLoadingState = true; this.isUserZooming = false;
                        if (drawingsData && typeof drawingsData === 'object') {{
                            this.drawings.lines = Array.isArray(drawingsData.lines) ? drawingsData.lines : [];
                            this.drawings.rectangles = Array.isArray(drawingsData.rectangles) ? drawingsData.rectangles : [];
                            this.drawings.notes = Array.isArray(drawingsData.notes) ? drawingsData.notes : [];
                            this.drawings.horizontal_lines = Array.isArray(drawingsData.horizontal_lines) ? drawingsData.horizontal_lines : [];
                            this.draw(); console.log("Drawings loaded:", this.drawings);
                        }}
                    }} catch (error) {{ console.error("Error loading drawings:", error); }}
                    finally {{ setTimeout(() => {{ this.isLoadingState = false; }}, 100); }}
                }}

                setupCanvas() {{ this.resizeCanvas(); window.addEventListener('resize', () => this.resizeCanvas()); }}

                resizeCanvas() {{
                    const container = this.canvas.parentElement;
                    this.width = container.clientWidth; this.height = container.clientHeight;
                    this.canvas.width = this.width * window.devicePixelRatio; this.canvas.height = this.height * window.devicePixelRatio;
                    this.canvas.style.width = this.width + 'px'; this.canvas.style.height = this.height + 'px';
                    this.ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
                    this.padding = {{ top: 10, right: 65, bottom: 25, left: 5 }};
                    const sliderHeight = 15, spacing = 10, volRatio = 0.15;
                    const plotHeight = this.height - this.padding.top - this.padding.bottom - sliderHeight;
                    this.chartArea = {{ x: this.padding.left, y: this.padding.top, width: this.width - this.padding.left - this.padding.right, height: Math.max(50, plotHeight * (1 - volRatio) - spacing) }};
                    this.volumeArea = {{ x: this.padding.left, y: this.chartArea.y + this.chartArea.height + spacing, width: this.chartArea.width, height: Math.max(10, plotHeight * volRatio) }};
                    this.calculateBounds(); this.draw(); setTimeout(() => this.updateSlider(), 100);
                }}

                setupEventListeners() {{
                    this.canvas.addEventListener('mousedown', (e) => this.handleMouseDown(e));
                    this.canvas.addEventListener('mousemove', (e) => this.handleMouseMove(e));
                    this.canvas.addEventListener('mouseup', (e) => this.handleMouseUp(e));
                    this.canvas.addEventListener('mouseleave', (e) => this.handleMouseLeave(e));
                    this.canvas.addEventListener('wheel', (e) => this.handleWheel(e));
                    this.canvas.addEventListener('dblclick', (e) => this.handleDoubleClick(e));
                    this.canvas.addEventListener('contextmenu', (e) => this.handleRightClick(e));
                    document.addEventListener('keydown', (e) => this.handleKeyDown(e));
                }}

                handleKeyDown(e) {{
                    if (e.key === 'Escape') {{
                        if (this.isDrawing) {{
                            this.isDrawing = false;
                            this.startPoint = null;
                            this.endPoint = null;
                            this.draw();
                        }} else if (this.currentTool) {{
                            this.setDrawingTool(null, false);
                        }}
                    }}
                }}

                handleMouseDown(e) {{
                    const mousePos = this.getMousePosition(e);
                    if (this.currentTool) this.startDrawing(mousePos);
                    else if (e.button === 0) {{
                        const clickedDrawingId = this.getDrawingAtPoint(mousePos);
                        if (clickedDrawingId) {{ this.selectedDrawingId = clickedDrawingId; }}
                        else {{
                            this.selectedDrawingId = null; this.isDragging = true;
                            this.lastMouseX = e.clientX; this.lastMouseY = e.clientY;
                            this.canvas.style.cursor = 'grabbing';
                        }}
                        this.draw();
                    }}
                }}

                handleMouseMove(e) {{
                    if (this.isDragging && !this.currentTool) {{ this.handleChartDrag(e); this.draw(); return; }}
                    if (this.isDrawing && this.startPoint) {{ this.endPoint = this.getMousePosition(e); this.draw(); this.drawTemporaryDrawing(); return; }}
                    this.updateCrosshair(e);
                }}

                handleMouseUp(e) {{
                    if (this.isDrawing && this.startPoint && this.endPoint) this.finishDrawing();
                    else if (this.isDragging) {{ this.isDragging = false; this.canvas.style.cursor = this.currentTool ? 'crosshair' : 'default'; this.draw(); }}
                }}

                handleMouseLeave(e) {{
                    this.isDragging = false; this.isDrawing = false;
                    this.crosshairX = null; this.crosshairY = null;
                    this.displayLatestCandleDetails(); this.updateMetricsDisplay();
                    this.canvas.style.cursor = this.currentTool ? 'crosshair' : 'default';
                    this.draw();
                }}

                handleDoubleClick(e) {{
                    const mousePos = this.getMousePosition(e);
                    const clickedNoteId = this.getDrawingAtPoint(mousePos, 'note');
                    if (clickedNoteId) {{
                        const note = this.drawings.notes.find(n => n.id === clickedNoteId);
                        if (note && this.chartBridge && this.chartBridge.request_text_note_edit_dialog) {{
                            this.chartBridge.request_text_note_edit_dialog(JSON.stringify(note));
                        }}
                        return;
                    }}
                }}

                startDrawing(mousePos) {{
                    if (this.currentTool === 'note') {{
                        if (this.chartBridge && this.chartBridge.request_text_note_dialog) {{
                            this.chartBridge.request_text_note_dialog(JSON.stringify(mousePos));
                        }}
                        return;
                    }}
                    this.isDrawing = true;
                    this.startPoint = {{ x: mousePos.x, y: mousePos.y, time: this.xToTime(mousePos.x), price: this.yToPrice(mousePos.y) }};
                    this.endPoint = this.startPoint;
                }}

                finishDrawing() {{
                    if (!this.startPoint || !this.endPoint) return;

                    let drawing;
                    const commonProps = {{ 
                        id: Date.now() + Math.random(), 
                        color: this.drawingColor, 
                        lineWidth: this.lineWidth, 
                        timestamp: Date.now() 
                    }};

                    if (this.currentTool === 'horizontal_line') {{
                        drawing = {{ ...commonProps, type: 'horizontal_line', price: this.startPoint.price }};
                        this.drawings.horizontal_lines.push(drawing);
                    }} else if (this.currentTool === 'horizontal_ray') {{
                        drawing = {{ ...commonProps, type: 'horizontal_ray', startTime: this.startPoint.time, startPrice: this.startPoint.price }};
                        this.drawings.horizontal_rays.push(drawing);
                    }} else {{
                        const endPointData = {{ endTime: this.xToTime(this.endPoint.x), endPrice: this.yToPrice(this.endPoint.y) }};
                        const startPointData = {{ startTime: this.startPoint.time, startPrice: this.startPoint.price }};
                        drawing = {{ ...commonProps, ...startPointData, ...endPointData }};

                        if (this.currentTool === 'line') {{
                            drawing.type = 'line';
                            this.drawings.lines.push(drawing);
                        }} else if (this.currentTool === 'rectangle') {{
                            drawing.type = 'rectangle';
                            this.drawings.rectangles.push(drawing);
                        }} else if (this.currentTool === 'arrow_line') {{
                            drawing.type = 'arrow_line';
                            this.drawings.arrow_lines.push(drawing);
                        }}
                    }}

                    this.isDrawing = false;
                    this.startPoint = null;
                    this.endPoint = null;
                    if(this.currentTool !== 'measure') {{
                         this.setDrawingTool(null, false);
                    }}
                    this.draw();
                    this.notifyDrawingsChange();
                }}

                getMousePosition(e) {{ const rect = this.canvas.getBoundingClientRect(); return {{ x: e.clientX - rect.left, y: e.clientY - rect.top }}; }}

                calculateBounds() {{
                    if (this.data.length === 0) return;
                    const visibleData = this.data.slice(this.viewPortStart, Math.min(this.data.length, this.viewPortEnd + 1));
                    if (visibleData.length === 0) {{ this.minPrice = 0; this.maxPrice = 0; this.minVolume = 0; this.maxVolume = 0; return; }}
                    this.minPrice = Math.min(...visibleData.map(d => d.low));
                    this.maxPrice = Math.max(...visibleData.map(d => d.high));
                    Object.values(this.emaData).forEach(emaList => {{
                        emaList.forEach(item => {{
                            const itemTime = item.time, firstVisibleTime = this.data[this.viewPortStart]?.time, lastVisibleTime = this.data[Math.min(this.data.length - 1, this.viewPortEnd)]?.time;
                            if (firstVisibleTime !== undefined && lastVisibleTime !== undefined && itemTime >= firstVisibleTime && itemTime <= lastVisibleTime) {{
                                this.minPrice = Math.min(this.minPrice, item.value); this.maxPrice = Math.max(this.maxPrice, item.value);
                            }}
                        }});
                    }});
                    const priceRange = this.maxPrice - this.minPrice;
                    if (priceRange === 0) {{ this.minPrice -= 1; this.maxPrice += 1; }}
                    else {{ this.minPrice -= priceRange * 0.05; this.maxPrice += priceRange * 0.05; }}
                    if (this.livePrice !== null) {{ this.minPrice = Math.min(this.minPrice, this.livePrice); this.maxPrice = Math.max(this.maxPrice, this.livePrice); }}
                    this.minVolume = 0;
                    this.maxVolume = Math.max(...this.volumeData.slice(this.viewPortStart, Math.min(this.volumeData.length, this.viewPortEnd + 1)).map(d => d.value));
                    if (this.maxVolume === 0) this.maxVolume = 1;
                }}

                draw() {{
                    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
                    this.ctx.fillStyle = this.colors.background; this.ctx.fillRect(0, 0, this.width, this.height);
                    if (this.data.length === 0) {{
                        this.ctx.fillStyle = this.colors.text; this.ctx.font = '16px Arial'; this.ctx.textAlign = 'center';
                        this.ctx.fillText('No data available', this.width / 2, this.height / 2); return;
                    }}
                    this.drawGrid(); this.drawVolume(); this.drawEMABands();
                    this.drawCandlesticks(); this.drawAxes(); this.drawAllDrawings();
                    this.drawPriceNotes();
                    this.drawCrosshair(); this.drawCurrentPriceRay();
                }}

                drawGrid() {{
                    this.ctx.strokeStyle = this.colors.grid; this.ctx.lineWidth = 1;
                    const priceRange = this.maxPrice - this.minPrice; if (priceRange <= 0) return;
                    const priceStep = priceRange / 8;
                    for (let i = 0; i <= 8; i++) {{
                        const price = this.minPrice + (priceStep * i), y = this.priceToY(price);
                        this.ctx.beginPath(); this.ctx.moveTo(this.chartArea.x, y); this.ctx.lineTo(this.chartArea.x + this.chartArea.width, y); this.ctx.stroke();
                    }}
                }}

                drawCandlesticks() {{
                    const visibleCandles = this.viewPortEnd - this.viewPortStart + 1; if (visibleCandles <= 0) return;
                    const candleSpace = this.chartArea.width / visibleCandles;
                    this.candleWidth = Math.max(1, candleSpace - this.candleSpacing);
                    for (let i = this.viewPortStart; i < this.data.length && i <= this.viewPortEnd; i++) {{
                        if (i < 0) continue;
                        const candle = this.data[i], x = this.candleToX(i);
                        const openY = this.priceToY(candle.open), closeY = this.priceToY(candle.close), highY = this.priceToY(candle.high), lowY = this.priceToY(candle.low);
                        const isUp = candle.close >= candle.open;
                        this.ctx.fillStyle = isUp ? this.colors.upCandle : this.colors.downCandle; this.ctx.strokeStyle = this.ctx.fillStyle; this.ctx.lineWidth = 1;
                        this.ctx.beginPath(); this.ctx.moveTo(x + this.candleWidth / 2, highY); this.ctx.lineTo(x + this.candleWidth / 2, lowY); this.ctx.stroke();
                        const bodyHeight = Math.abs(closeY - openY);
                        if (bodyHeight < 1) {{ this.ctx.beginPath(); this.ctx.moveTo(x, openY); this.ctx.lineTo(x + this.candleWidth, openY); this.ctx.stroke(); }}
                        else {{ this.ctx.fillRect(x, Math.min(openY, closeY), this.candleWidth, bodyHeight); }}
                    }}
                }}

                drawVolume() {{
                    const visibleCandles = this.viewPortEnd - this.viewPortStart + 1; if (visibleCandles <= 0) return;
                    for (let i = this.viewPortStart; i < this.volumeData.length && i <= this.viewPortEnd; i++) {{
                        if (i < 0) continue;
                        const volume = this.volumeData[i], candle = this.data[i], x = this.candleToX(i);
                        const height = (this.maxVolume > 0) ? (volume.value / this.maxVolume) * this.volumeArea.height : 0;
                        const isUp = candle.close >= candle.open;
                        this.ctx.fillStyle = isUp ? this.colors.volumeUp : this.colors.volumeDown;
                        this.ctx.fillRect(x, this.volumeArea.y + this.volumeArea.height - height, this.candleWidth, height);
                    }}
                }}

                drawEMABands() {{
                    this.ctx.setLineDash([]);
                    const emaColors = {{ 'ema10': '#2962ff', 'ema20': '#9c27b0', 'ema50': '#f06204' }};
                    for (const emaKey in this.emaData) {{
                        const emaList = this.emaData[emaKey]; if (emaList.length === 0) continue;
                        this.ctx.strokeStyle = emaColors[emaKey] || '#FFFFFF'; this.ctx.lineWidth = 1.5;
                        this.ctx.beginPath(); let firstPoint = true;
                        for (let i = 0; i < emaList.length; i++) {{
                            const item = emaList[i], x = this.timeToX(item.time), y = this.priceToY(item.value);
                            if (x >= this.chartArea.x && x <= (this.chartArea.x + this.chartArea.width) && y >= this.chartArea.y && y <= (this.chartArea.y + this.chartArea.height)) {{
                                if (firstPoint) {{ this.ctx.moveTo(x, y); firstPoint = false; }}
                                else {{ this.ctx.lineTo(x, y); }}
                            }} else if (!firstPoint) break;
                        }}
                        this.ctx.stroke();
                    }}
                }}

                drawAxes() {{
                    this.ctx.fillStyle = this.colors.text; this.ctx.font = '11px monospace'; this.ctx.textAlign = 'left';
                    const priceRange = this.maxPrice - this.minPrice; if (priceRange <= 0) return;
                    const priceStep = priceRange / 8;
                    for (let i = 0; i <= 8; i++) {{
                        const price = this.minPrice + (priceStep * i), y = this.priceToY(price);
                        this.ctx.fillText('₹' + price.toFixed(2), this.chartArea.x + this.chartArea.width + 4, y + 4);
                    }}
                    this.ctx.fillText('Vol', this.volumeArea.x + this.volumeArea.width + 4, this.volumeArea.y + 12);
                    this.ctx.fillText(this.formatVolume(this.maxVolume), this.volumeArea.x + this.volumeArea.width + 4, this.volumeArea.y + 24);
                    const visibleCandles = this.viewPortEnd - this.viewPortStart + 1;
                    const timeStep = Math.max(1, Math.floor(visibleCandles / 6)); this.ctx.textAlign = 'center';
                    for (let i = this.viewPortStart; i < this.data.length; i += timeStep) {{
                        if (i < 0) continue;
                        const x = this.candleToX(i) + this.candleWidth / 2, date = new Date(this.data[i].time);
                        this.ctx.fillText(this.formatTimeLabel(date), x, this.volumeArea.y + this.volumeArea.height + 20);
                    }}
                    this.ctx.textAlign = 'left';
                }}

                drawAllDrawings() {{
                    this.drawings.lines.forEach(line => {{
                        if (line.type !== 'line') return;
                        const startX = this.timeToX(line.startTime), startY = this.priceToY(line.startPrice), endX = this.timeToX(line.endTime), endY = this.priceToY(line.endPrice);
                        if (this.isLineVisible(startX, startY, endX, endY)) {{
                            this.ctx.strokeStyle = line.color; this.ctx.lineWidth = line.lineWidth; this.ctx.setLineDash([]);
                            if (this.selectedDrawingId === line.id) {{ this.ctx.strokeStyle = '#FFFF00'; this.ctx.lineWidth = line.lineWidth + 2; }}
                            this.ctx.beginPath(); this.ctx.moveTo(startX, startY); this.ctx.lineTo(endX, endY); this.ctx.stroke();
                        }}
                    }});
                    this.drawings.rectangles.forEach(rect => {{
                        const startX = this.timeToX(rect.startTime), startY = this.priceToY(rect.startPrice), endX = this.timeToX(rect.endTime), endY = this.priceToY(rect.endPrice);
                        const x = Math.min(startX, endX), y = Math.min(startY, endY), width = Math.abs(endX - startX), height = Math.abs(endY - startY);
                        if (this.isRectVisible(x, y, width, height)) {{
                            this.ctx.strokeStyle = rect.color; this.ctx.lineWidth = rect.lineWidth; this.ctx.setLineDash([]);
                            this.ctx.fillStyle = rect.color + '20';
                            if (this.selectedDrawingId === rect.id) {{ this.ctx.strokeStyle = '#FFFF00'; this.ctx.lineWidth = rect.lineWidth + 2; this.ctx.fillStyle = this.ctx.strokeStyle + '30'; }}
                            this.ctx.fillRect(x, y, width, height); this.ctx.strokeRect(x, y, width, height);
                        }}
                    }});
                    this.drawings.notes.forEach(note => {{
                        if (note.type !== 'note') return;
                        const x = this.timeToX(note.time), y = this.priceToY(note.price);
                        if (this.isPointVisible(x, y)) {{
                            this.ctx.font = `bold ${{note.size || 12}}px Arial`; const textMetrics = this.ctx.measureText(note.text);
                            this.ctx.fillStyle = 'rgba(0, 0, 0, 0.8)'; if (this.selectedDrawingId === note.id) this.ctx.fillStyle = 'rgba(255, 255, 0, 0.8)';
                            this.ctx.fillRect(x - 2, y - (note.size || 12) - 2, textMetrics.width + 4, (note.size || 12) + 4);
                            this.ctx.fillStyle = note.color || '#FFFFFF'; if (this.selectedDrawingId === note.id) this.ctx.fillStyle = '#000000';
                            this.ctx.fillText(note.text, x, y);
                        }}
                    }});
                    this.drawings.horizontal_lines.forEach(line => {{
                        const y = this.priceToY(line.price);
                        if (y >= this.chartArea.y && y <= this.chartArea.y + this.chartArea.height) {{
                            this.ctx.strokeStyle = line.color; this.ctx.lineWidth = line.lineWidth; this.ctx.setLineDash([]);
                             if (this.selectedDrawingId === line.id) {{ this.ctx.strokeStyle = '#FFFF00'; this.ctx.lineWidth = line.lineWidth + 2; }}
                            this.ctx.beginPath(); this.ctx.moveTo(this.chartArea.x, y); this.ctx.lineTo(this.chartArea.x + this.chartArea.width, y); this.ctx.stroke();
                        }}
                    }});
                    this.drawings.horizontal_rays.forEach(ray => {{
                        const startX = this.timeToX(ray.startTime);
                        const startY = this.priceToY(ray.startPrice);

                        if (this.isPointVisible(startX, startY)) {{
                            this.ctx.strokeStyle = this.selectedDrawingId === ray.id ? '#FFFF00' : ray.color;
                            this.ctx.lineWidth = this.selectedDrawingId === ray.id ? ray.lineWidth + 1 : ray.lineWidth;
                            this.ctx.beginPath();
                            this.ctx.moveTo(startX, startY);
                            this.ctx.lineTo(this.chartArea.x + this.chartArea.width, startY);
                            this.ctx.stroke();
                        }}
                    }});

                    this.drawings.arrow_lines.forEach(line => {{
                        const startX = this.timeToX(line.startTime);
                        const startY = this.priceToY(line.startPrice);
                        const endX = this.timeToX(line.endTime);
                        const endY = this.priceToY(line.endPrice);

                        if (this.isLineVisible(startX, startY, endX, endY)) {{
                            this.ctx.strokeStyle = this.selectedDrawingId === line.id ? '#FFFF00' : line.color;
                            this.ctx.lineWidth = this.selectedDrawingId === line.id ? line.lineWidth + 1 : line.lineWidth;
                            this.ctx.beginPath();
                            this.ctx.moveTo(startX, startY);
                            this.ctx.lineTo(endX, endY);
                            this.ctx.stroke();
                            this.drawArrowhead(this.ctx, startX, startY, endX, endY, 10);
                        }}
                    }});
                }}

                drawTemporaryDrawing() {{
                    if (!this.isDrawing || !this.startPoint || !this.endPoint) return;
                    this.ctx.strokeStyle = this.drawingColor; this.ctx.lineWidth = this.lineWidth; this.ctx.setLineDash([3, 3]);
                    if (this.currentTool === 'line') {{ this.ctx.beginPath(); this.ctx.moveTo(this.startPoint.x, this.startPoint.y); this.ctx.lineTo(this.endPoint.x, this.endPoint.y); this.ctx.stroke(); }}
                    else if (this.currentTool === 'rectangle') {{ const width = this.endPoint.x - this.startPoint.x, height = this.endPoint.y - this.startPoint.y; this.ctx.strokeRect(this.startPoint.x, this.startPoint.y, width, height); }}
                    else if (this.currentTool === 'horizontal_line') {{ this.ctx.beginPath(); this.ctx.moveTo(this.chartArea.x, this.startPoint.y); this.ctx.lineTo(this.chartArea.x + this.chartArea.width, this.startPoint.y); this.ctx.stroke(); }}
                    else if (this.currentTool === 'arrow_line') {{
                        this.ctx.beginPath();
                        this.ctx.moveTo(this.startPoint.x, this.startPoint.y);
                        this.ctx.lineTo(this.endPoint.x, this.endPoint.y);
                        this.ctx.stroke();
                        this.drawArrowhead(this.ctx, this.startPoint.x, this.startPoint.y, this.endPoint.x, this.endPoint.y, 10);
                    }}
                    else if (this.currentTool === 'horizontal_ray') {{
                        this.ctx.beginPath();
                        this.ctx.moveTo(this.startPoint.x, this.startPoint.y);
                        this.ctx.lineTo(this.chartArea.x + this.chartArea.width, this.startPoint.y);
                        this.ctx.stroke();
                    }}
                    else if (this.currentTool === 'measure') {{
                        this.ctx.beginPath();
                        this.ctx.moveTo(this.startPoint.x, this.startPoint.y);
                        this.ctx.lineTo(this.endPoint.x, this.endPoint.y);
                        this.ctx.stroke();

                        const price1 = this.yToPrice(this.startPoint.y);
                        const price2 = this.yToPrice(this.endPoint.y);
                        const priceChange = price2 - price1;
                        const pctChange = (priceChange / price1) * 100;

                        const index1 = this.xToCandle(this.startPoint.x);
                        const index2 = this.xToCandle(this.endPoint.x);
                        const barCount = index2 - index1;

                        const time1 = this.data[index1] ? this.data[index1].time : 0;
                        const time2 = this.data[index2] ? this.data[index2].time : 0;
                        const timeDiff = Math.abs(time2 - time1);
                        const days = Math.floor(timeDiff / (1000 * 60 * 60 * 24));

                        const infoText = [
                            `₹${{priceChange.toFixed(2)}} (${{pctChange.toFixed(2)}}%)`,
                            `${{barCount}} bars, ${{days}} days`
                        ];

                        this.ctx.font = '12px sans-serif';
                        const textWidth = Math.max(this.ctx.measureText(infoText[0]).width, this.ctx.measureText(infoText[1]).width);
                        const boxX = this.endPoint.x + 10;
                        const boxY = this.endPoint.y;
                        const boxW = textWidth + 20;
                        const boxH = 45;

                        this.ctx.fillStyle = 'rgba(40, 40, 40, 0.8)';
                        this.ctx.strokeStyle = this.drawingColor;
                        this.ctx.lineWidth = 1;
                        this.ctx.setLineDash([]);
                        this.ctx.fillRect(boxX, boxY, boxW, boxH);
                        this.ctx.strokeRect(boxX, boxY, boxW, boxH);

                        this.ctx.fillStyle = '#FFFFFF';
                        this.ctx.fillText(infoText[0], boxX + 10, boxY + 20);
                        this.ctx.fillText(infoText[1], boxX + 10, boxY + 38);
                    }}

                    this.ctx.setLineDash([]);
                }}

                drawArrowhead(ctx, fromX, fromY, toX, toY, radius) {{
                    const angle = Math.atan2(toY - fromY, toX - fromX);
                    ctx.save();
                    ctx.beginPath();
                    ctx.translate(toX, toY);
                    ctx.rotate(angle);
                    ctx.moveTo(0, 0);
                    ctx.lineTo(-radius, radius / 2);
                    ctx.moveTo(0, 0);
                    ctx.lineTo(-radius, -radius / 2);
                    ctx.stroke();
                    ctx.restore();
                }}

                drawCrosshair() {{
                    if (this.currentTool === null && this.crosshairX !== null && this.crosshairY !== null) {{
                        this.ctx.strokeStyle = this.colors.crosshair; this.ctx.lineWidth = 1; this.ctx.setLineDash([5, 5]);
                        this.ctx.beginPath(); this.ctx.moveTo(this.crosshairX, this.chartArea.y); this.ctx.lineTo(this.crosshairX, this.volumeArea.y + this.volumeArea.height); this.ctx.stroke();
                        if (this.crosshairY >= this.chartArea.y && this.crosshairY <= this.chartArea.y + this.chartArea.height) {{
                            this.ctx.beginPath(); this.ctx.moveTo(this.chartArea.x, this.crosshairY); this.ctx.lineTo(this.chartArea.x + this.chartArea.width, this.crosshairY); this.ctx.stroke();
                            const priceText = '₹' + this.yToPrice(this.crosshairY).toFixed(2);
                            this.ctx.font = 'bold 12px monospace'; const textMetrics = this.ctx.measureText(priceText);
                            const rectX = this.chartArea.x + this.chartArea.width, rectY = this.crosshairY - 8, rectWidth = textMetrics.width + 10, rectHeight = 16;
                            this.ctx.fillStyle = this.colors.crosshair; this.ctx.fillRect(rectX, rectY, rectWidth, rectHeight);
                            this.ctx.fillStyle = 'white'; this.ctx.fillText(priceText, rectX + 5, this.crosshairY + 4);
                        }}
                        this.ctx.setLineDash([]);
                    }}
                }}

                drawCurrentPriceRay() {{
                    if (this.livePrice !== null && this.currentTool === null) {{
                        const y = this.priceToY(this.livePrice);
                        const lastCandleIndex = this.data.length - 1; let rayStartX = this.chartArea.x;
                        if (lastCandleIndex >= this.viewPortStart && lastCandleIndex <= this.viewPortEnd) rayStartX = this.candleToX(lastCandleIndex) + this.candleWidth / 2;
                        else if (lastCandleIndex > this.viewPortEnd) rayStartX = this.chartArea.x + this.chartArea.width;
                        this.ctx.strokeStyle = this.colors.livePrice; this.ctx.lineWidth = 2; this.ctx.setLineDash([2, 2]);
                        this.ctx.beginPath(); this.ctx.moveTo(rayStartX, y); this.ctx.lineTo(this.chartArea.x + this.chartArea.width, y); this.ctx.stroke(); this.ctx.setLineDash([]);
                        const priceText = '₹' + this.livePrice.toFixed(2); this.ctx.font = 'bold 12px monospace';
                        const textMetrics = this.ctx.measureText(priceText);
                        const rectX = this.chartArea.x + this.chartArea.width, rectY = y - 8, rectWidth = textMetrics.width + 10, rectHeight = 16;
                        this.ctx.fillStyle = this.colors.livePrice; this.ctx.fillRect(rectX, rectY, rectWidth, rectHeight);
                        this.ctx.fillStyle = 'white'; this.ctx.fillText(priceText, rectX + 5, y + 4);
                    }}
                }}

                drawPriceNotes() {{
                    this.drawings.notes.forEach(note => {{
                        if (note.type === 'price_note' && note.price >= this.minPrice && note.price <= this.maxPrice) {{
                            const y = this.priceToY(note.price), x = this.timeToX(note.time);
                            this.ctx.save(); this.ctx.font = 'bold 12px sans-serif'; this.ctx.textAlign = 'left';
                            const textWidth = this.ctx.measureText(note.text).width, padding = 4;
                            this.ctx.fillStyle = 'rgba(0,0,0,0.7)'; this.ctx.fillRect(x, y - 12 - padding, textWidth + padding * 2, 12 + padding * 2);
                            this.ctx.fillStyle = note.color; this.ctx.fillText(note.text, x + padding, y - padding);
                            this.ctx.restore();
                        }}
                    }});
                }}

                handleChartDrag(e) {{
                    const deltaX = e.clientX - this.lastMouseX, deltaY = e.clientY - this.lastMouseY;
                    const visibleCandles = this.viewPortEnd - this.viewPortStart + 1;
                    const pixelsPerCandle = this.chartArea.width / visibleCandles;
                    const candleShift = -Math.round(deltaX / pixelsPerCandle);
                    let newStart = Math.max(0, Math.min(this.viewPortStart + candleShift, this.data.length + this.rightBufferCandles - visibleCandles));
                    if (this.viewPortStart !== newStart) {{ this.viewPortStart = newStart; this.viewPortEnd = this.viewPortStart + visibleCandles - 1; }}
                    const pricePerPixel = (this.maxPrice - this.minPrice) / this.chartArea.height;
                    const priceDelta = -deltaY * pricePerPixel;
                    this.minPrice += priceDelta; this.maxPrice += priceDelta;
                    this.lastMouseX = e.clientX; this.lastMouseY = e.clientY;
                    this.calculateBounds(); this.updateSlider();
                }}

                handleWheel(e) {{
                    e.preventDefault();
                    const mouseY = e.clientY - this.canvas.getBoundingClientRect().top, mouseX = e.clientX - this.canvas.getBoundingClientRect().left;
                    let zoomChanged = false; this.isUserZooming = true;
                    if (e.ctrlKey || e.metaKey) {{
                        const zoomFactor = e.deltaY > 0 ? 1.1 : 0.9;
                        if (mouseY >= this.chartArea.y && mouseY <= this.chartArea.y + this.chartArea.height) {{
                            const priceAtMouse = this.yToPrice(mouseY), currentRange = this.maxPrice - this.minPrice, newRange = currentRange * zoomFactor;
                            this.minPrice = priceAtMouse - (newRange * ((priceAtMouse - this.minPrice) / currentRange));
                            this.maxPrice = priceAtMouse + (newRange * ((this.maxPrice - priceAtMouse) / currentRange));
                        }}
                    }} else if (e.shiftKey) {{
                        const panAmount = (e.deltaY > 0 ? 1 : -1) * (this.maxPrice - this.minPrice) * 0.05;
                        this.minPrice += panAmount; this.maxPrice += panAmount;
                    }} else {{
                        const zoomFactor = e.deltaY > 0 ? 1.1 : 0.9, currentCount = this.visibleCandleCount;
                        let newCount = Math.round(currentCount * zoomFactor);
                        newCount = Math.max(20, Math.min(this.data.length + this.rightBufferCandles, newCount));
                        if (newCount !== currentCount) {{
                            const dataCandleIndex = this.xToCandle(mouseX);
                            let newStart = Math.round(dataCandleIndex - (newCount * ((mouseX - this.chartArea.x) / this.chartArea.width)));
                            newStart = Math.max(0, Math.min(newStart, this.data.length + this.rightBufferCandles - newCount));
                            this.viewPortStart = newStart; this.viewPortEnd = this.viewPortStart + newCount - 1;
                            this.visibleCandleCount = newCount; zoomChanged = true;
                        }}
                    }}
                    this.calculateBounds(); this.draw(); this.updateSlider();
                    if (zoomChanged) {{ setTimeout(() => {{ this.notifyZoomChange(); this.isUserZooming = false; }}, 200); }}
                    else this.isUserZooming = false;
                }}

                handleRightClick(e) {{
                    e.preventDefault();
                    const rect = this.canvas.getBoundingClientRect();
                    const priceAtMouse = this.yToPrice(e.clientY - rect.top);
                    this.showChartContextMenu(e.clientX, e.clientY, priceAtMouse, this.currentSymbol || 'SYMBOL');
                }}

                showChartContextMenu(clientX, clientY, priceLevel, symbol) {{
                    this.removeExistingContextMenu();
                    const menu = document.createElement('div');
                    menu.style.cssText = `position: fixed; left: ${{clientX}}px; top: ${{clientY}}px; background-color: #1a1a1a; border: 1px solid #404040; border-radius: 6px; padding: 8px 0; z-index: 10000; box-shadow: 0 4px 12px rgba(0,0,0,0.5); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 12px; color: #e0e0e0; min-width: 180px; user-select: none;`;
                    const ltp = this.livePrice || (this.data.length > 0 ? this.data[this.data.length - 1].close : priceLevel);
                    const isAbove = priceLevel > ltp;
                    const diff = Math.abs(priceLevel - ltp), diffPercent = ((diff / ltp) * 100).toFixed(2);
                    const menuItems = [
                        {{ text: `Set Alert at ₹${{priceLevel.toFixed(2)}}`, icon: '🔔', action: () => this.createAlert(symbol, priceLevel), subtitle: `${{isAbove ? 'Above' : 'Below'}} LTP by ${{diffPercent}}%`, highlight: true }},
                        {{ divider: true }},
                        {{ text: isAbove ? 'Buy Entry Alert' : 'Short Entry Alert', icon: isAbove ? '📈' : '📉', action: () => this.createAlert(symbol, priceLevel, isAbove ? 'buy_entry' : 'sell_entry'), subtitle: isAbove ? 'Breakout signal' : 'Breakdown signal' }},
                        {{ text: isAbove ? 'Resistance Watch' : 'Support Watch', icon: '👁️', action: () => this.createAlert(symbol, priceLevel, isAbove ? 'resistance' : 'support'), subtitle: isAbove ? 'Monitor resistance' : 'Monitor support' }},
                        {{ divider: true }},
                        {{ text: 'Place Order', icon: '💰', action: () => this.placeOrderAtPrice(symbol, priceLevel), subtitle: 'Quick order entry' }},
                        {{ text: 'Add Drawing Tool', icon: '✏️', submenu: [
                            {{ text: 'Horizontal Line', action: () => this.addHorizontalLine(priceLevel) }},
                            {{ text: 'Support Line', action: () => this.addSupportLine(priceLevel) }},
                            {{ text: 'Resistance Line', action: () => this.addResistanceLine(priceLevel) }},
                            {{ text: 'Price Note', action: () => this.addPriceNote(priceLevel) }}
                        ]}}
                    ];
                    menuItems.forEach(item => {{
                        if (item.divider) {{ const d = document.createElement('div'); d.style.cssText = 'height: 1px; background-color: #404040; margin: 4px 0;'; menu.appendChild(d); return; }}
                        const mi = document.createElement('div');
                        mi.style.cssText = `padding: 8px 16px; cursor: pointer; display: flex; align-items: center; transition: background-color 0.2s ease; ${{item.highlight ? 'background-color: rgba(106, 156, 255, 0.1);' : ''}}`;
                        mi.innerHTML = `<span style="margin-right: 8px; font-size: 14px;">${{item.icon}}</span><div style="flex: 1;"><div style="font-weight: ${{item.highlight ? '600' : '500'}}; color: ${{item.highlight ? '#a0c0ff' : '#e0e0e0'}};">${{item.text}}</div>${{item.subtitle ? `<div style="font-size: 10px; color: #8a8a9e; margin-top: 2px;">${{item.subtitle}}</div>` : ''}}</div>${{item.submenu ? '<span style="margin-left: 8px; color: #8a8a9e;">▶</span>' : ''}}`;
                        mi.addEventListener('mouseenter', () => mi.style.backgroundColor = item.highlight ? 'rgba(106, 156, 255, 0.2)' : '#2a2a2a');
                        mi.addEventListener('mouseleave', () => mi.style.backgroundColor = item.highlight ? 'rgba(106, 156, 255, 0.1)' : 'transparent');
                        if (item.action) mi.addEventListener('click', (e) => {{ e.stopPropagation(); item.action(); this.removeExistingContextMenu(); }});
                        menu.appendChild(mi);
                    }});
                    document.body.appendChild(menu);
                    const menuRect = menu.getBoundingClientRect();
                    if (menuRect.right > window.innerWidth) menu.style.left = `${{clientX - menuRect.width}}px`;
                    if (menuRect.bottom > window.innerHeight) menu.style.top = `${{clientY - menuRect.height}}px`;
                    const closeHandler = (e) => {{ if (!menu.contains(e.target)) {{ this.removeExistingContextMenu(); document.removeEventListener('click', closeHandler); }} }};
                    setTimeout(() => document.addEventListener('click', closeHandler), 100);
                    this.activeContextMenu = menu;
                }}

                removeExistingContextMenu() {{
                    if (this.activeContextMenu) {{ document.body.removeChild(this.activeContextMenu); this.activeContextMenu = null; }}
                    document.querySelectorAll('.chart-context-menu').forEach(m => m.parentNode && m.parentNode.removeChild(m));
                }}

                createAlert(symbol, price, intent = 'auto') {{
                    const currentLTP = this.livePrice || (this.data.length > 0 ? this.data[this.data.length - 1].close : price);
                    const isAboveLTP = price > currentLTP;
                    if (intent === 'auto') {{
                        const hasLong = this.checkHasLongPosition ? this.checkHasLongPosition(symbol) : false;
                        const hasShort = this.checkHasShortPosition ? this.checkHasShortPosition(symbol) : false;
                        if (isAboveLTP) intent = hasLong ? 'profit_target' : (hasShort ? 'stop_loss' : 'buy_entry');
                        else intent = hasLong ? 'stop_loss' : (hasShort ? 'profit_target' : 'sell_entry');
                    }}
                    const alertData = {{ symbol, price, condition: isAboveLTP ? 'crosses_above' : 'crosses_below', intent, current_ltp: currentLTP, note: this.generateAlertNote(symbol, price, currentLTP, intent) }};
                    if (this.chartBridge) this.chartBridge.show_order_dialog_from_chart(JSON.stringify(orderData));
                }}

                addHorizontalLine(priceLevel) {{ this.drawings.horizontal_lines.push({{ id: Date.now() + Math.random(), type: 'horizontal_line', price: priceLevel, color: '#FFD700', lineWidth: 2, style: 'solid', label: `₹${{priceLevel.toFixed(2)}}` }}); this.draw(); this.notifyDrawingsChange(); }}
                addSupportLine(priceLevel) {{ this.drawings.horizontal_lines.push({{ id: Date.now() + Math.random(), type: 'horizontal_line', price: priceLevel, color: '#4CAF50', lineWidth: 2, style: 'solid', label: `Support: ₹${{priceLevel.toFixed(2)}}` }}); this.draw(); this.notifyDrawingsChange(); }}
                addResistanceLine(priceLevel) {{ this.drawings.horizontal_lines.push({{ id: Date.now() + Math.random(), type: 'horizontal_line', price: priceLevel, color: '#f44336', lineWidth: 2, style: 'solid', label: `Resistance: ₹${{priceLevel.toFixed(2)}}` }}); this.draw(); this.notifyDrawingsChange(); }}
                addPriceNote(priceLevel) {{
                    const note = {{ id: Date.now() + Math.random(), type: 'price_note', time: this.xToTime(this.chartArea.width * 0.9), price: priceLevel, text: `₹${{priceLevel.toFixed(2)}}`, color: '#a0c0ff' }};
                    this.drawings.notes.push(note); this.draw(); this.notifyDrawingsChange();
                }}

                updateMetricsDisplay() {{
                    const el = document.getElementById('metricsInfo'); if (!el) return;
                    let adrText = `ADR: ${{this.currentADR && this.currentADR.value > 0 ? `₹${{this.currentADR.value.toFixed(2)}} (${{this.currentADR.percent.toFixed(2)}}%)` : 'N/A'}}`;
                    let changes = ["Weekly", "Monthly", "3M", "6M", "1Y"].map(p => {{
                        if (this.percentageChanges.hasOwnProperty(p)) {{
                            const change = this.percentageChanges[p], color = change >= 0 ? '#00b894' : '#d63031';
                            return `<span style="color: ${{color}};">${{p}}: ${{change.toFixed(2)}}%</span>`;
                        }} return `<span style="color: #e0e0e0;">${{p}}: N/A</span>`;
                    }});
                    el.innerHTML = `${{adrText}} | ${{changes.join(' | ')}}`;
                }}

                displayLatestCandleDetails() {{
                    const el = document.getElementById('priceInfo'); if (!el) return;
                    if (this.crosshairX !== null) return;
                    if (this.data.length > 0) {{
                        const candle = this.data[this.data.length - 1];
                        const dateStr = new Date(candle.time).toLocaleDateString('en-GB', {{ day: '2-digit', month: 'short', year: 'numeric' }});
                        const change = candle.close - candle.open, percent = (candle.open !== 0) ? ((change / candle.open) * 100).toFixed(2) : '0.00';
                        const changeStr = change >= 0 ? `+₹${{change.toFixed(2)}} (+${{percent}}%)` : `₹${{change.toFixed(2)}} (${{percent}}%)`;
                        el.textContent = `${{dateStr}} | O:₹${{candle.open.toFixed(2)}} H:₹${{candle.high.toFixed(2)}} L:₹${{candle.low.toFixed(2)}} C:₹${{candle.close.toFixed(2)}} | ${{changeStr}}`;
                    }} else el.textContent = 'No data available';
                }}

                updateCrosshair(e) {{
                    const rect = this.canvas.getBoundingClientRect(), x = e.clientX - rect.left, y = e.clientY - rect.top;
                    if (x < this.chartArea.x || x > this.chartArea.x + this.chartArea.width || y < this.chartArea.y || y > this.volumeArea.y + this.volumeArea.height) {{
                        this.crosshairX = null; this.crosshairY = null; this.displayLatestCandleDetails();
                        this.updateMetricsDisplay(); this.canvas.style.cursor = this.currentTool ? 'crosshair' : 'default';
                        this.draw(); return;
                    }}
                    const candleIndex = this.xToCandle(x);
                    if (candleIndex >= 0 && candleIndex < this.data.length) {{
                        const candle = this.data[candleIndex];
                        const change = candle.close - candle.open, percent = (candle.open !== 0) ? ((change / candle.open) * 100).toFixed(2) : '0.00';
                        const changeStr = change >= 0 ? `+₹${{change.toFixed(2)}} (+${{percent}}%)` : `₹${{change.toFixed(2)}} (${{percent}}%)`;
                        const dateStr = this.formatTimeLabel(new Date(candle.time));
                        const el = document.getElementById('priceInfo');
                        if (el) el.textContent = `${{dateStr}} | O:₹${{candle.open.toFixed(2)}} H:₹${{candle.high.toFixed(2)}} L:₹${{candle.low.toFixed(2)}} C:₹${{candle.close.toFixed(2)}} | ${{changeStr}}`;
                        this.crosshairX = x; this.crosshairY = y; this.draw();
                    }} else {{
                        this.crosshairX = null; this.crosshairY = null; this.displayLatestCandleDetails(); this.updateMetricsDisplay(); this.draw();
                    }}
                }}

                priceToY(price) {{ const ratio = (price - this.minPrice) / (this.maxPrice - this.minPrice); return this.chartArea.y + this.chartArea.height - (ratio * this.chartArea.height); }}
                yToPrice(y) {{ const ratio = (this.chartArea.y + this.chartArea.height - y) / this.chartArea.height; return this.minPrice + (ratio * (this.maxPrice - this.minPrice)); }}

                timeToX(time) {{
                    let candleIndex = this.data.findIndex(d => d.time >= time);
                    if (candleIndex === -1) {{
                        const lastIndex = this.data.length - 1; if (lastIndex < 0) return this.chartArea.x;
                        const xOfLast = this.candleToX(lastIndex) + this.candleWidth;
                        return Math.min(xOfLast + (this.chartArea.width * (this.rightBufferCandles / this.visibleCandleCount)), this.chartArea.x + this.chartArea.width);
                    }}
                    if (candleIndex === 0 && time < this.data[0].time) return this.chartArea.x;
                    return this.candleToX(candleIndex);
                }}

                xToTime(x) {{
                    const candleIndex = this.xToCandle(x);
                    if (candleIndex >= 0 && candleIndex < this.data.length) return this.data[candleIndex].time;
                    const lastTime = this.data.length > 0 ? this.data[this.data.length - 1].time : Date.now();
                    const firstTime = this.data.length > 0 ? this.data[0].time : Date.now();
                    if (candleIndex >= this.data.length) {{
                        const avgTime = (lastTime - firstTime) / Math.max(1, this.data.length - 1);
                        return lastTime + (avgTime * (candleIndex - (this.data.length - 1)));
                    }}
                    return firstTime;
                }}

                candleToX(index) {{ const visibleCandles = this.viewPortEnd - this.viewPortStart + 1; const candleSpace = this.chartArea.width / visibleCandles; return this.chartArea.x + ((index - this.viewPortStart) * candleSpace); }}
                xToCandle(x) {{ const relativeX = x - this.chartArea.x, visibleCandles = this.viewPortEnd - this.viewPortStart + 1, candleSpace = this.chartArea.width / visibleCandles; if (candleSpace <= 0) return -1; return this.viewPortStart + Math.floor(relativeX / candleSpace); }}
                isLineVisible(x1, y1, x2, y2) {{ const chart = this.chartArea; return !((x1 < chart.x && x2 < chart.x) || (x1 > chart.x + chart.width && x2 > chart.x + chart.width) || (y1 < chart.y && y2 < chart.y) || (y1 > chart.y + chart.height && y2 > chart.y + chart.height)); }}
                isRectVisible(x, y, w, h) {{ const chart = this.chartArea; return x + w >= chart.x && x <= chart.x + chart.width && y + h >= chart.y && y <= chart.y + chart.height; }}
                isPointVisible(x, y) {{ const chart = this.chartArea; return x >= chart.x && x <= chart.x + chart.width && y >= chart.y && y <= chart.y + chart.height; }}
                formatTimeLabel(date) {{
                    const now = new Date(), daysDiff = Math.floor((now - date) / 86400000), isSameDay = date.toDateString() === now.toDateString();
                    if (this.currentInterval.includes('minute')) {{
                        const time = date.toLocaleTimeString('en-GB', {{ hour: '2-digit', minute: '2-digit' }});
                        return isSameDay ? time : date.toLocaleDateString('en-GB', {{ day: '2-digit', month: 'short' }}) + ' ' + time;
                    }}
                    return date.toLocaleDateString('en-GB', {{ day: '2-digit', month: 'short', year: daysDiff > 365 ? 'numeric' : undefined }});
                }}
                formatVolume(vol) {{ if (vol >= 1e7) return (vol / 1e7).toFixed(1) + 'Cr'; if (vol >= 1e5) return (vol / 1e5).toFixed(1) + 'L'; if (vol >= 1e3) return (vol / 1e3).toFixed(1) + 'K'; return vol.toFixed(0); }}

                getDrawingAtPoint(mousePos, specificType = null) {{
                    const tol = 5;
                    if (!specificType || specificType === 'line') {{
                        for (const line of this.drawings.lines) {{
                            const sx = this.timeToX(line.startTime), sy = this.priceToY(line.startPrice), ex = this.timeToX(line.endTime), ey = this.priceToY(line.endPrice);
                            if (this.isPointNearLine(mousePos.x, mousePos.y, sx, sy, ex, ey, tol)) return line.id;
                        }}
                    }}
                    if (!specificType || specificType === 'horizontal_line') {{
                        for (const line of this.drawings.horizontal_lines) {{
                           if (Math.abs(mousePos.y - this.priceToY(line.price)) <= tol) return line.id;
                        }}
                    }}
                    if (!specificType || specificType === 'horizontal_ray') {{
                        for (const ray of this.drawings.horizontal_rays) {{
                            const startX = this.timeToX(ray.startTime);
                            const y = this.priceToY(ray.startPrice);
                            // Check if click is on the ray line (from start point to right edge)
                            if (Math.abs(mousePos.y - y) <= tol && mousePos.x >= startX - tol && mousePos.x <= this.chartArea.x + this.chartArea.width + tol) {{
                                return ray.id;
                            }}
                        }}
                    }}
                    if (!specificType || specificType === 'arrow_line') {{
                        for (const arrow of this.drawings.arrow_lines) {{
                            const sx = this.timeToX(arrow.startTime), sy = this.priceToY(arrow.startPrice), ex = this.timeToX(arrow.endTime), ey = this.priceToY(arrow.endPrice);
                            if (this.isPointNearLine(mousePos.x, mousePos.y, sx, sy, ex, ey, tol)) return arrow.id;
                        }}
                    }}
                    if (!specificType || specificType === 'rectangle') {{
                        for (const rect of this.drawings.rectangles) {{
                            const sx = this.timeToX(rect.startTime), sy = this.priceToY(rect.startPrice), ex = this.timeToX(rect.endTime), ey = this.priceToY(rect.endPrice);
                            const x = Math.min(sx, ex), y = Math.min(sy, ey), w = Math.abs(ex - sx), h = Math.abs(ey - sy);
                            if (mousePos.x >= x - tol && mousePos.x <= x + w + tol && mousePos.y >= y - tol && mousePos.y <= y + h + tol) return rect.id;
                        }}
                    }}
                    if (!specificType || specificType === 'note') {{
                        for (const note of this.drawings.notes) {{ 
                            const x = this.timeToX(note.time), y = this.priceToY(note.price); 
                            if (Math.abs(mousePos.x - x) <= tol && Math.abs(mousePos.y - y) <= tol) return note.id; 
                        }}
                    }}
                    return null;
                }}

                isPointNearLine(px, py, x1, y1, x2, y2, tol) {{
                    const dx = x2 - x1, dy = y2 - y1; const lenSq = dx * dx + dy * dy;
                    const t = lenSq === 0 ? -1 : Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / lenSq));
                    const projX = x1 + t * dx, projY = y1 + t * dy;
                    return (px - projX) * (px - projX) + (py - projY) * (py - projY) < tol * tol;
                }}

                setupSlider() {{
                    const setup = () => {{
                        this.slider = document.getElementById('timeSlider'); this.sliderTrack = document.getElementById('sliderTrack'); this.sliderThumb = document.getElementById('sliderThumb');
                        if (!this.slider || !this.sliderThumb || !this.sliderTrack) {{ setTimeout(setup, 100); return; }}
                        this.sliderThumb.addEventListener('mousedown', (e) => this.handleSliderMouseDown(e)); document.addEventListener('mousemove', (e) => this.handleSliderMouseMove(e));
                        document.addEventListener('mouseup', (e) => this.handleSliderMouseUp(e)); this.sliderTrack.addEventListener('click', (e) => this.handleSliderClick(e));
                        this.slider.addEventListener('wheel', (e) => this.handleSliderWheel(e));
                    }}; setup();
                }}
                handleSliderMouseDown(e) {{ if (e.target === this.sliderThumb) {{ e.preventDefault(); this.isSliderDragging = true; this.sliderLastX = e.clientX; this.sliderThumb.style.cursor = 'grabbing'; }} }}
                handleSliderMouseMove(e) {{
                    if (!this.isSliderDragging) return; e.preventDefault();
                    const deltaX = e.clientX - this.sliderLastX; this.sliderLastX = e.clientX;
                    const totalSpots = this.data.length + this.rightBufferCandles, movableRange = totalSpots - this.visibleCandleCount; if (movableRange <= 0) return;
                    const pxPerSpot = (this.sliderTrack.clientWidth - this.sliderThumb.clientWidth) / movableRange, deltaSpot = Math.round(deltaX / pxPerSpot);
                    let newStart = this.viewPortStart - deltaSpot; newStart = Math.max(0, Math.min(newStart, movableRange));
                    if (this.viewPortStart !== newStart) {{ this.viewPortStart = newStart; this.viewPortEnd = this.viewPortStart + this.visibleCandleCount - 1; this.calculateBounds(); this.draw(); this.updateSlider(); }}
                }}
                handleSliderMouseUp(e) {{ this.isSliderDragging = false; this.sliderThumb.style.cursor = 'grab'; }}
                handleSliderClick(e) {{
                    if (e.target === this.sliderThumb) return;
                    const rect = this.sliderTrack.getBoundingClientRect(), clickX = e.clientX - rect.left;
                    const totalSpots = this.data.length + this.rightBufferCandles, movableRange = totalSpots - this.visibleCandleCount; if (movableRange <= 0) return;
                    let newStart = Math.round((clickX / this.sliderTrack.clientWidth) * movableRange);
                    newStart = Math.max(0, Math.min(newStart, movableRange));
                    if (this.viewPortStart !== newStart) {{ this.viewPortStart = newStart; this.viewPortEnd = this.viewPortStart + this.visibleCandleCount - 1; this.calculateBounds(); this.draw(); this.updateSlider(); }}
                }}
                handleSliderWheel(e) {{
                    e.preventDefault(); this.isUserZooming = true;
                    const scroll = e.deltaY > 0 ? 5 : -5, totalSpots = this.data.length + this.rightBufferCandles, movableRange = totalSpots - this.visibleCandleCount; if (movableRange <= 0) {{ this.isUserZooming = false; return; }}
                    let newStart = Math.max(0, Math.min(this.viewPortStart + scroll, movableRange));
                    if (this.viewPortStart !== newStart) {{ this.viewPortStart = newStart; this.viewPortEnd = this.viewPortStart + this.visibleCandleCount - 1; this.calculateBounds(); this.draw(); this.updateSlider(); }}
                    setTimeout(() => this.isUserZooming = false, 100);
                }}
                updateSlider() {{
                    if (!this.slider || !this.sliderThumb || !this.sliderTrack) {{ setTimeout(() => this.updateSlider(), 50); return; }}
                    const totalSpots = this.data.length + this.rightBufferCandles;
                    if (totalSpots <= this.visibleCandleCount) {{ this.slider.style.display = 'none'; return; }}
                    this.slider.style.display = 'flex';
                    const trackWidth = this.sliderTrack.clientWidth, thumbWidth = Math.max(20, (this.visibleCandleCount / totalSpots) * trackWidth);
                    const maxPos = trackWidth - thumbWidth, ratio = this.viewPortStart / (totalSpots - this.visibleCandleCount);
                    this.sliderThumb.style.width = thumbWidth + 'px'; this.sliderThumb.style.left = Math.max(0, Math.min(maxPos, ratio * maxPos)) + 'px';
                }}

                updatePositionLine(info) {{ this.positionInfo = info; this.draw(); }}
                updateAlertLines(alerts) {{ this.activeAlerts = alerts; this.draw(); }}
                updateTextNote(noteData) {{
                    const noteIndex = this.drawings.notes.findIndex(n => n.id === noteData.id);
                    if (noteIndex > -1) {{
                        this.drawings.notes[noteIndex] = {{ ...this.drawings.notes[noteIndex], ...noteData }};
                        this.draw();
                        this.notifyDrawingsChange();
                    }}
                }}

                addTextNoteFromDialog(noteData) {{
                    if (noteData.text) {{
                        const note = {{
                            id: Date.now() + Math.random(),
                            type: 'note',
                            time: this.xToTime(noteData.x),
                            price: this.yToPrice(noteData.y),
                            text: noteData.text,
                            color: noteData.color,
                            size: noteData.size,
                            timestamp: Date.now()
                        }};
                        this.drawings.notes.push(note);
                        this.draw();
                        this.notifyDrawingsChange();
                    }}
                }}

                checkHasLongPosition(symbol) {{ return false; }}
                checkHasShortPosition(symbol) {{ return false; }}

                generateAlertNote(symbol, alertPrice, currentLTP, intent) {{
                    const diff = alertPrice - currentLTP;
                    const percent = ((Math.abs(diff) / currentLTP) * 100).toFixed(2);
                    const dir = diff > 0 ? 'above' : 'below';
                    const messages = {{
                        'buy_entry': `Buy signal for ${{symbol}} on break ${{dir}} ₹${{alertPrice.toFixed(2)}}`,
                        'sell_entry': `Short signal for ${{symbol}} on break ${{dir}} ₹${{alertPrice.toFixed(2)}}`,
                        'profit_target': `Profit target for ${{symbol}} at ₹${{alertPrice.toFixed(2)}}`,
                        'stop_loss': `Stop loss for ${{symbol}} at ₹${{alertPrice.toFixed(2)}}`,
                        'resistance': `Resistance watch for ${{symbol}} at ₹${{alertPrice.toFixed(2)}}`,
                        'support': `Support watch for ${{symbol}} at ₹${{alertPrice.toFixed(2)}}`
                    }};
                    return messages[intent] || `Alert for ${{symbol}} at ₹${{alertPrice.toFixed(2)}}`;
                }}

                showAlertCreatedNotification(symbol, price, intent) {{
                    const el = document.createElement('div');
                    el.style.cssText = `position: fixed; top: 20px; right: 20px; background-color: #4CAF50; color: white; padding: 12px 20px; border-radius: 6px; z-index: 10001; font-family: -apple-system, sans-serif; font-size: 13px; font-weight: 500; box-shadow: 0 4px 12px rgba(0,0,0,0.3); animation: slideInRight 0.3s ease-out;`;
                    el.innerHTML = `<div style="display: flex; align-items: center;"><span style="margin-right: 8px; font-size: 16px;">🔔</span><div><div>Alert Created!</div><div style="font-size: 11px; opacity: 0.9; margin-top: 2px;">${{symbol}} at ₹${{price.toFixed(2)}} (${{intent.replace('_', ' ')}})</div></div></div>`;
                    if (!document.querySelector('#alert-animations')) {{
                        const style = document.createElement('style');
                        style.id = 'alert-animations';
                        style.textContent = `@keyframes slideInRight{{from{{transform:translateX(100%);opacity:0}}to{{transform:translateX(0);opacity:1}}}}@keyframes slideOutRight{{from{{transform:translateX(0);opacity:1}}to{{transform:translateX(100%);opacity:0}}}}`;
                        document.head.appendChild(style);
                    }}
                    document.body.appendChild(el);
                    setTimeout(() => {{
                        el.style.animation = 'slideOutRight 0.3s ease-in';
                        setTimeout(() => el.parentNode && el.parentNode.removeChild(el), 300);
                    }}, 3000);
                }}

                placeOrderAtPrice(symbol, price) {{
                    const orderData = {{
                        symbol: symbol,
                        price: price,
                        ltp: this.livePrice || (this.data.length > 0 ? this.data[this.data.length - 1].close : price)
                    }};
                    if (this.chartBridge && this.chartBridge.show_order_dialog_from_chart) {{
                        this.chartBridge.show_order_dialog_from_chart(JSON.stringify(orderData));
                    }}
                }}

                createAlert(symbol, price, intent = 'auto') {{
                    const currentLTP = this.livePrice || (this.data.length > 0 ? this.data[this.data.length - 1].close : price);
                    const isAboveLTP = price > currentLTP;
                    if (intent === 'auto') {{
                        const hasLong = this.checkHasLongPosition ? this.checkHasLongPosition(symbol) : false;
                        const hasShort = this.checkHasShortPosition ? this.checkHasShortPosition(symbol) : false;
                        if (isAboveLTP) {{
                            intent = hasLong ? 'profit_target' : (hasShort ? 'stop_loss' : 'buy_entry');
                        }} else {{
                            intent = hasLong ? 'stop_loss' : (hasShort ? 'profit_target' : 'sell_entry');
                        }}
                    }}
                    const alertData = {{
                        symbol: symbol,
                        price: price,
                        condition: isAboveLTP ? 'crosses_above' : 'crosses_below',
                        intent: intent,
                        current_ltp: currentLTP,
                        note: this.generateAlertNote(symbol, price, currentLTP, intent)
                    }};
                    if (this.chartBridge && this.chartBridge.create_alert_from_chart) {{
                        this.chartBridge.create_alert_from_chart(JSON.stringify(alertData));
                    }}
                    this.showAlertCreatedNotification(symbol, price, intent);
                }}

                createAlert(symbol, price, intent = 'auto') {{
                    const currentLTP = this.livePrice || (this.data.length > 0 ? this.data[this.data.length - 1].close : price);
                    const isAboveLTP = price > currentLTP;
                    if (intent === 'auto') {{
                        const hasLong = this.checkHasLongPosition ? this.checkHasLongPosition(symbol) : false;
                        const hasShort = this.checkHasShortPosition ? this.checkHasShortPosition(symbol) : false;
                        if (isAboveLTP) {{
                            intent = hasLong ? 'profit_target' : (hasShort ? 'stop_loss' : 'buy_entry');
                        }} else {{
                            intent = hasLong ? 'stop_loss' : (hasShort ? 'profit_target' : 'sell_entry');
                        }}
                    }}
                    const alertData = {{
                        symbol: symbol,
                        price: price,
                        condition: isAboveLTP ? 'crosses_above' : 'crosses_below',
                        intent: intent,
                        current_ltp: currentLTP,
                        note: this.generateAlertNote(symbol, price, currentLTP, intent)
                    }};
                    if (this.chartBridge && this.chartBridge.create_alert_from_chart) {{
                        this.chartBridge.create_alert_from_chart(JSON.stringify(alertData));
                    }}
                    this.showAlertCreatedNotification(symbol, price, intent);
                }}

                setDrawingTool(tool, enabled, color, width) {{ 
                    this.isDragging = false; 
                    this.canvas.style.cursor = 'crosshair'; 
                    if (enabled) {{ 
                        this.currentTool = tool; 
                        this.drawingColor = color || this.drawingColor; 
                        this.lineWidth = width || this.lineWidth; 
                    }} else {{ 
                        this.currentTool = null; 
                        this.canvas.style.cursor = 'default'; 
                        this.isDrawing = false; 
                        this.startPoint = null; 
                        this.endPoint = null; 
                    }} 
                    this.draw(); 
                }}
                updateDrawingStyle(color, width) {{ this.drawingColor = color || this.drawingColor; this.lineWidth = width || this.lineWidth; }}
                setVisibleCandleCount(count) {{ 
                    let newCount = Math.max(20, Math.min(this.data.length + this.rightBufferCandles, count)); 
                    if (this.visibleCandleCount === newCount) return; 
                    this.isUserZooming = false; 
                    this.visibleCandleCount = newCount; 
                    this.viewPortEnd = Math.min(this.data.length - 1 + this.rightBufferCandles, this.viewPortStart + this.visibleCandleCount - 1); 
                    this.viewPortStart = Math.max(0, this.viewPortEnd - this.visibleCandleCount + 1); 
                    this.viewPortEnd = this.viewPortStart + this.visibleCandleCount - 1; 
                    this.calculateBounds(); 
                    this.draw(); 
                    this.updateSlider(); 
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
                    const lastCandle = this.data[this.data.length - 1]; 
                    if (lastCandle) {{ 
                        lastCandle.close = newPrice; 
                        lastCandle.high = Math.max(lastCandle.high, newPrice); 
                        lastCandle.low = Math.min(lastCandle.low, newPrice); 
                    }} 
                    this.calculateBounds(); 
                    this.draw(); 
                }}
                addNewCandle(candle) {{ if(candle) {{ this.data.push(candle); this.calculateBounds(); this.draw(); }} }}
                clearAllDrawings() {{ this.drawings = {{ lines: [], rectangles: [], notes: [], horizontal_lines: [] }}; this.draw(); this.notifyDrawingsChange(); }}
                deleteSelectedDrawing() {{ 
                    if (this.selectedDrawingId) {{ 
                        let deleted = false; 
                        for (const type in this.drawings) {{ 
                            const len = this.drawings[type].length; 
                            this.drawings[type] = this.drawings[type].filter(d => d.id !== this.selectedDrawingId); 
                            if (this.drawings[type].length < len) {{ 
                                deleted = true; 
                                break; 
                            }} 
                        }} 
                        if (deleted) {{ 
                            this.selectedDrawingId = null; 
                            this.draw(); 
                            this.notifyDrawingsChange(); 
                        }} 
                    }} 
                }}
                autoScale() {{ this.calculateBounds(); this.draw(); this.updateSlider(); }}
                getVisibleCandleCount() {{ return this.visibleCandleCount; }}
                getAllDrawings() {{ return this.drawings; }}
            }}

            window.globalChartSettings = window.globalChartSettings || {{ visibleCandleCount: {initial_visible_candle_count}, candleWidth: {initial_candle_width}, candleSpacing: {initial_candle_spacing} }};
            const candlestickData = {candlestick_json}, volumeData = {volume_json}, emaData = {ema_json}, initialADR = {adr_json}, percentageChanges = {percentage_changes_json};
            const upCandleColor = '{up_candle_color}', downCandleColor = '{down_candle_color}';
            const currentInterval = {current_interval_js}, currentSymbol = {current_symbol_js};
            const initialDrawingsJson = `{safe_initial_drawings}`;
            let chartInitialized = false;

            function initChart() {{
                if (chartInitialized) return; chartInitialized = true;
                try {{
                    const chart = new FixedTradingChart('mainCanvas', candlestickData, volumeData, window.globalChartSettings.visibleCandleCount, window.globalChartSettings.candleWidth, window.globalChartSettings.candleSpacing, upCandleColor, downCandleColor, emaData, initialADR, percentageChanges, currentInterval, currentSymbol, initialDrawingsJson);
                    window.chart = chart; window.autoScale = () => chart.autoScale();
                    chart.updateGlobalSettings = function(count) {{ window.globalChartSettings.visibleCandleCount = count; }};
                }} catch (error) {{
                    console.error('Error initializing chart:', error);
                    document.getElementById('priceInfo').textContent = 'Error: ' + error.message;
                }}
            }}
            document.addEventListener('DOMContentLoaded', initChart);
            if (document.readyState === 'interactive' || document.readyState === 'complete') initChart();
            setTimeout(initChart, 100);
        </script>
    </body>
    </html>
            """
        return html

    def _auto_scale_chart(self):
        if self.chart_view: self.chart_view.page().runJavaScript("if (window.autoScale) window.autoScale();")

    def _open_settings_dialog(self):
        current_settings = {"candle_width": self._current_candle_width,
                            "candle_spacing": self._current_candle_spacing,
                            "default_visible_candles": self.current_visible_candle_count,
                            "up_candle_color": self._current_up_color, "down_candle_color": self._current_down_color}
        dialog = ChartSettingsDialog(current_settings, self)
        dialog.settings_changed.connect(self._apply_chart_settings)
        dialog.exec()

    @Slot(dict)
    def _apply_chart_settings(self, new_settings: Dict[str, Any]):
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
                    candleWidth: {self._current_candle_width}, candleSpacing: {self._current_candle_spacing},
                    upCandleColor: '{self._current_up_color}', downCandleColor: '{self._current_down_color}'
                }});
                window.chart.setVisibleCandleCount({self.current_visible_candle_count});
                window.chart.autoScale();
            }}"""
            self.chart_view.page().runJavaScript(js_code)
            logger.info("Applied new chart settings and auto-scaled.")

    def _update_symbol_info(self, df: pd.DataFrame):
        try:
            if df.empty: return
            latest = df.iloc[-1]
            self.current_ltp = float(latest.get('close', 0.0))
            change_str = "N/A"
            if len(df) > 1:
                change = self.current_ltp - df.iloc[-2]['close']
                change_pct = (change / df.iloc[-2]['close']) * 100 if df.iloc[-2]['close'] != 0 else 0
                change_str = f"{change:+.2f} ({change_pct:+.2f}%)"
            self.symbol_info_label.setText(f"{self.current_symbol} • ₹{self.current_ltp:.2f}")
            self.symbol_info_label.setToolTip(f"Change: {change_str}")
        except Exception as e:
            logger.error(f"Error updating symbol info from DataFrame: {e}")

    @Slot()
    def _on_order_button_clicked(self):
        if self.current_symbol and self.current_ltp > 0:
            self.order_button_clicked.emit(self.current_symbol, self.current_ltp)
        else:
            QMessageBox.warning(self, "No Symbol", "Please select a symbol first.")

    @Slot(str)
    def _on_alert_creation_requested(self, alert_json: str):
        """
        Handles alert creation request from the chart.
        This method NO LONGER shows a popup. It just forwards the signal.
        """
        logger.info(f"CandlestickChart: Relaying alert creation request: {alert_json}")
        # Simply emit the signal for the main window's controller (AlertSystemManager) to handle.
        self.alert_creation_requested.emit(alert_json)

    @Slot(str)
    def _on_order_dialog_requested(self, order_data_json: str):
        """
        Handles the request to show an order dialog from the chart.
        This method NO LONGER shows a popup. It just forwards the signal.
        """
        logger.info(f"CandlestickChart: Relaying order dialog request: {order_data_json}")
        # Simply emit the signal for the main window's controller to handle.
        self.order_dialog_requested.emit(order_data_json)

    def _force_refresh(self):
        if self.current_symbol: self._load_chart_data(force_refresh=True)

    def _retry_load(self):
        if self.current_symbol: self._load_chart_data()

    def _stop_current_operations(self):
        if self.data_loader_thread and self.data_loader_thread.isRunning():
            self.data_loader_thread.stop()
            self.data_loader_thread.quit()
            self.data_loader_thread.wait(3000)
            if self.data_loader_thread.isRunning():
                logger.warning("Terminating hung data loader thread.")
                self.data_loader_thread.terminate()
            self.data_loader_thread.deleteLater()
            self.data_loader_thread = None

    def _show_error(self, message: str):
        self.error_label.setText(f"Error: {message}")
        self._set_state(ChartState.ERROR)

    def _apply_styles(self):
        self.setStyleSheet("""
            QFrame#chartToolbar { background-color: #1a1a1a; border-bottom: 1px solid #404040; }
            #symbolFullNameLabel { color: #E0E0E0; font-size: 13px; font-weight: bold; padding-left: 5px; }
            QComboBox#chartDropdown {
                background-color: #000000; color: white; border: 1px solid #333333;
                padding: 4px 6px; border-radius: 3px; font-size: 11px; font-weight: 500;
            }
            QComboBox#chartDropdown:hover { border: 1px solid #00d4ff; color: #00d4ff; }
            QComboBox#chartDropdown::drop-down { border: none; }
            QComboBox QAbstractItemView { background-color: #2a2a2a; color: #e0e0e0; border: 1px solid #505050; }
            #chartControlButton, #chartOrderButton {
                background-color: #000000; color: white; border: 1px solid #333333;
                padding: 4px 6px; border-radius: 3px; font-size: 11px; font-weight: 500;
            }
            #chartControlButton:hover, #chartOrderButton:hover {
                background-color: #1a1a1a; border: 1px solid #00d4ff; color: #00d4ff;
            }
            #chartControlButton:checked { background-color: #0066cc; border: 1px solid #0080ff; }
            QFrame#drawingToolsFrame { border: 1px solid #404040; border-radius: 4px; }
            #drawingToolsFrame > QPushButton { border: none; border-radius: 0; padding: 4px; font-size: 14px; }
            #drawingToolsFrame > QPushButton:checked { background-color: #0066cc; }
            QMenu { background-color: #2a2a2a; color: #e0e0e0; border: 1px solid #505050; }
            QMenu::item:selected { background-color: #0066cc; }
        """)

    def closeEvent(self, event):
        """Enhanced close event that saves last viewed symbol"""
        try:
            if self.current_symbol and self.chart_view:
                self._save_current_state_sync()
                # Save the last viewed symbol on close
                self.drawing_storage.save_last_viewed_symbol(self.current_symbol, self.current_interval)

            self._stop_current_operations()
            self.data_cache.clear()
            if self.channel:
                self.channel.deleteLater()
                self.channel = None
            logger.info("Candlestick chart widget closed and state saved.")
        except Exception as e:
            logger.error(f"Error during close: {e}")
        super().closeEvent(event)