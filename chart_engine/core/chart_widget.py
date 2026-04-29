# chart_engine/core/chart_widget.py
#
# CandlestickChart — the main QWidget you drop into any layout.
#
# Responsibilities:
#   - Own and wire all sub-modules (toolbar, bridge, loader, storage, renderer)
#   - Manage chart lifecycle: load symbol → render → live updates
#   - Relay signals in/out (symbol_loaded, order_button_clicked, alert_creation_requested)
#   - Save/restore per-symbol state (drawings + zoom) on symbol change
#
# It is intentionally thin: all real work lives in the sub-modules.

import json
import logging
from typing import Any, Dict, Optional

import pandas as pd
from kiteconnect import KiteConnect
from PySide6.QtCore import QTimer, Signal, Slot, Qt
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QColorDialog, QFrame, QLabel, QMessageBox,
    QProgressBar, QStackedWidget, QVBoxLayout, QWidget,
)
from PySide6.QtGui import QColor, QShortcut, QKeySequence

from chart_engine.core.chart_bridge import ChartBridge
from chart_engine.core.data_loader import ChartDataLoaderThread, DataCache, DataFetcher
from chart_engine.core.metrics import calculate_metrics
from chart_engine.drawings import DrawingStorage
from chart_engine.renderer.html_builder import ChartHtmlConfig, build_chart_html
from chart_engine.settings.chart_settings_dialog import ChartSettingsDialog
from chart_engine.settings.text_note_dialog import TextNoteDialog
from chart_engine.toolbar.chart_toolbar import ChartToolbar

logger = logging.getLogger(__name__)

DEFAULT_INDICATOR_VISIBILITY = {
    "ema10": False,
    "ema20": False,
    "ema50": False,
    "ema200": False,
    "atrTrendReversal": False,
    "vwap": False,
    "volume": True,   # volume bars on by default
    "cvd": False,
    "rsi": False,
}

# ChartState is used internally to manage the stacked-widget visibility.
from enum import Enum

class ChartState(Enum):
    IDLE    = "idle"
    LOADING = "loading"
    ERROR   = "error"
    LOADED  = "loaded"


class CandlestickChart(QWidget):
    """
    Institutional-grade candlestick chart widget.

    Drop-in replacement for the monolithic canvas_candlestick_chart.py.
    Works with Kite today; IBKR requires only swapping the DataFetcher.

    Signals:
        symbol_loaded(str)                     — after a symbol renders successfully
        order_button_clicked(str, float)       — (symbol, ltp) when Order btn clicked
        alert_creation_requested(str)          — alert JSON from chart right-click
        order_dialog_requested(str)            — order JSON from chart right-click
        data_request_for_symbol(str)           — after load, requests live-data sub
    """

    symbol_loaded              = Signal(str)
    order_button_clicked       = Signal(str, float)
    alert_creation_requested   = Signal(str)
    alert_price_updated        = Signal(str)   # {symbol, old_price, new_price} — alert drag
    order_dialog_requested     = Signal(str)
    data_request_for_symbol    = Signal(str)

    def __init__(
        self,
        kite_client: KiteConnect,
        instrument_loader=None,
        storage_dir: str = "kite/user_data/chart_drawings",
        parent=None,
    ):
        super().__init__(parent)
        self.kite_client       = kite_client
        self.instrument_loader = instrument_loader

        # ── State ──
        self.current_symbol:          str   = ""
        self.current_interval:        str   = "day"
        self.current_instrument_token: int  = 0
        self.current_ltp:             float = 0.0
        self.current_state = ChartState.IDLE
        self.last_df:    Optional[pd.DataFrame] = None
        self._active_load_key: Optional[str] = None
        self.instrument_map: Dict[str, Dict[str, Any]] = {}

        # ── Drawing style ──
        self.current_drawing_color = "#FFD700"
        self.current_line_width    = 1.5
        self.current_drawing_tool  = ""

        # ── Sub-modules ──
        self.drawing_storage = DrawingStorage(storage_dir)
        self.global_chart_settings = self.drawing_storage.load_global_settings()

        self._current_up_color          = self.global_chart_settings.get("up_candle_color",   "#00d4a8")
        self._current_down_color        = self.global_chart_settings.get("down_candle_color", "#ff4d6a")
        self._current_volume_up_color   = self.global_chart_settings.get("up_volume_color",   "#00d4a8")
        self._current_volume_down_color = self.global_chart_settings.get("down_volume_color", "#ff4d6a")
        self._current_candle_width      = self.global_chart_settings.get("candle_width",   3)
        self._current_candle_spacing    = self.global_chart_settings.get("candle_spacing", 3)
        self._watermark_enabled         = self.global_chart_settings.get("watermark_enabled",  True)
        self._show_watermark_description = self.global_chart_settings.get("show_watermark_description", True)
        self._watermark_color           = self.global_chart_settings.get("watermark_color",    "#ffffff")
        self._watermark_opacity         = self.global_chart_settings.get("watermark_opacity",  0.06)
        self._watermark_position        = self.global_chart_settings.get("watermark_position", "mid_center")
        self._watermark_font_size       = self.global_chart_settings.get("watermark_font_size", 0)
        self._indicator_scale_labels_enabled = self.global_chart_settings.get("indicator_scale_labels_enabled", False)
        self._crosshair_snap_enabled    = self.global_chart_settings.get("crosshair_snap_enabled", True)
        self._tool_selection_mode       = self.global_chart_settings.get("tool_selection_mode", "single_use")
        self._toolbar_symbol_display    = self.global_chart_settings.get("toolbar_symbol_display", "symbol")
        self.current_visible_candle_count = self.global_chart_settings.get("default_visible_candles", 100)
        self._indicator_visibility = self.drawing_storage.load_global_indicator_visibility()
        self._current_watermark_description = ""

        self.data_fetcher = DataFetcher(kite_client)
        self.data_cache   = DataCache()
        self.data_loader_thread: Optional[ChartDataLoaderThread] = None

        # ── WebEngine ──
        self.chart_view:   Optional[QWebEngineView] = None
        self.chart_bridge: Optional[ChartBridge]    = None
        self.channel:      Optional[QWebChannel]    = None

        # ── Build UI ──
        self._build_ui()
        self._setup_shortcuts()
        self._apply_styles()

        # ── Restore last symbol ──
        last = self.drawing_storage.load_last_viewed_symbol()
        if last.get("symbol"):
            QTimer.singleShot(200, lambda: self.load_symbol(
                last["symbol"], None, 0, last.get("interval", "day")
            ))

    # ═══════════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════════════════════

    def load_symbol(
        self,
        symbol: str,
        exchange: Optional[str],
        instrument_token: int,
        interval: Optional[str] = None,
        force_refresh: bool = False,
    ) -> None:
        """Load a new symbol (or reload current). Safe to call from any thread."""
        if not symbol:
            return
        self.current_symbol           = symbol
        self._current_watermark_description = self._resolve_symbol_description(symbol)
        token = int(instrument_token or 0)
        if not token:
            instrument = self.instrument_map.get(symbol, {})
            token = int(instrument.get("instrument_token") or 0)
        self.current_instrument_token = token
        if interval:
            self.current_interval = interval
            self.toolbar.set_timeframe(interval)
        self._load_chart_data(force_refresh=force_refresh)

    def update_live_data(self, live_data: Any) -> None:
        """Called by the market-data worker with a tick dict or list of ticks."""
        if self.current_state != ChartState.LOADED or not self.current_symbol:
            return
        if isinstance(live_data, list):
            for item in live_data:
                self._process_tick(item)
        elif isinstance(live_data, dict):
            self._process_tick(live_data)

    def set_drawings(self, drawings: Dict[str, Any]) -> None:
        """Inject a complete drawings dictionary directly into the live JavaScript chart."""
        if self.chart_view and self.current_state == ChartState.LOADED:
            payload = json.dumps(drawings)
            js_code = (
                "(function applyDrawingsWhenReady(drawings, attempt){"
                "attempt = attempt || 0;"
                "if(window.chart && window.chart.updateDrawings){"
                "window.chart.updateDrawings(drawings);"
                "return;"
                "}"
                "if(attempt < 25){"
                "setTimeout(function(){applyDrawingsWhenReady(drawings, attempt + 1);}, 100);"
                "}"
                "})(" + payload + ", 0);"
            )
            self._js(js_code)

    def apply_color_theme(self, theme: Dict[str, Any]) -> None:
        candles = theme.get("candles", {})
        volume  = theme.get("volume",  {})
        self._current_up_color          = candles.get("up",   self._current_up_color)
        self._current_down_color        = candles.get("down", self._current_down_color)
        self._current_volume_up_color   = volume.get("up",    self._current_up_color)
        self._current_volume_down_color = volume.get("down",  self._current_down_color)
        self._js("if(window.chart) window.chart.setChartSettings({"
                 f"upCandleColor:'{self._current_up_color}',"
                 f"downCandleColor:'{self._current_down_color}',"
                 f"upVolumeColor:'{self._current_volume_up_color}',"
                 f"downVolumeColor:'{self._current_volume_down_color}'"
                 "});")

    def set_instrument_list(self, instruments: Any) -> None:
        """Compatibility API used by `kite.core.main_window`.

        Stores a tradingsymbol → instrument payload map that is later used by
        `on_search` to resolve symbols emitted by watchlist/scanner widgets.
        """
        if not instruments:
            self.instrument_map = {}
            return

        instrument_map: Dict[str, Dict[str, Any]] = {}
        for instrument in instruments:
            if not isinstance(instrument, dict):
                continue
            symbol = str(instrument.get("tradingsymbol", "")).strip().upper()
            token = instrument.get("instrument_token")
            if symbol and token:
                instrument_map[symbol] = instrument

        self.instrument_map = instrument_map

        # If startup attempted to restore a symbol before instruments were ready,
        # retry once we can resolve a valid token.
        if self.current_symbol and not self.current_instrument_token:
            current = self.instrument_map.get(self.current_symbol, {})
            token = int(current.get("instrument_token") or 0)
            if token:
                logger.info("Resolved token for restored symbol %s after instrument load", self.current_symbol)
                self.current_instrument_token = token
                self._load_chart_data()

    @Slot(str)
    def on_search(self, symbol: Optional[str] = None) -> None:
        """Compatibility API used by UI symbol-selection signals."""
        resolved_symbol = self._resolve_symbol(symbol)
        if not resolved_symbol:
            if symbol:
                self._show_error(f"Symbol '{symbol}' not found")
            return

        if self.current_symbol and self.chart_view and resolved_symbol != self.current_symbol:
            self._save_current_state_sync()

        instrument = self.instrument_map.get(resolved_symbol, {})
        token = int(instrument.get("instrument_token") or 0)
        exchange = instrument.get("exchange")
        self.load_symbol(resolved_symbol, exchange, token)
        self.set_watermark(
            resolved_symbol,
            self._resolve_symbol_description(resolved_symbol),
            self._show_watermark_description,
        )

        self.drawing_storage.save_last_viewed_symbol(resolved_symbol, self.current_interval)

    def _resolve_symbol(self, symbol: Optional[str]) -> Optional[str]:
        if not symbol:
            return None

        value = str(symbol).strip().upper()
        if value in self.instrument_map:
            return value

        candidates = [value]
        if ":" in value:
            candidates.append(value.split(":", 1)[1])

        if value.endswith("-EQ"):
            candidates.append(value[:-3])
        else:
            candidates.append(f"{value}-EQ")

        for candidate in candidates:
            if candidate in self.instrument_map:
                return candidate
        return None

    def _resolve_symbol_description(self, symbol: str) -> str:
        instrument = self.instrument_map.get(str(symbol or "").strip().upper(), {})
        return str(instrument.get("name", "") or "").strip()

    # ═══════════════════════════════════════════════════════════════════════
    # BUILD UI
    # ═══════════════════════════════════════════════════════════════════════

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Toolbar ──
        self.toolbar = ChartToolbar(self)
        self.toolbar.apply_toolbar_preferences(self.global_chart_settings.get("toolbar_preferences", {}))
        self._wire_toolbar()
        main_layout.addWidget(self.toolbar)

        # ── Thin progress bar ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumHeight(3)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        main_layout.addWidget(self.progress_bar)

        # ── Stacked widget: loading / error / chart ──
        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack)

        self.stack.addWidget(self._make_loading_widget())
        self.stack.addWidget(self._make_error_widget())

        self.chart_container = QWidget()
        self.chart_layout    = QVBoxLayout(self.chart_container)
        self.chart_layout.setContentsMargins(0, 0, 0, 0)
        self.stack.addWidget(self.chart_container)

        self._set_state(ChartState.IDLE)

    def _wire_toolbar(self) -> None:
        tb = self.toolbar

        # Timeframe dropdown
        if tb.timeframe_dropdown:
            tb.timeframe_dropdown.currentIndexChanged.connect(self._on_timeframe_selected)

        # Drawing tools
        for tool_id, action in tb._drawing_actions.items():
            action.triggered.connect(
                lambda _checked=False, tid=tool_id: self._activate_drawing_tool(tid)
            )
        tb.get_clear_action().triggered.connect(self._clear_active_tool)
        tb.measure_btn.toggled.connect(self._toggle_measure_tool)

        # Indicator multi-select dropdown actions
        for key, action in tb.indicator_actions.items():
            action.toggled.connect(lambda checked, k=key: self._toggle_indicator(k, checked))

        # Action buttons
        tb.color_btn.clicked.connect(self._choose_drawing_color)
        tb.clear_drawings_btn.clicked.connect(
            lambda: self._js("if(window.chart) window.chart.clearAllDrawings();")
        )
        tb.snapshot_btn.clicked.connect(self._save_drawings)
        tb.autoscale_btn.clicked.connect(self._auto_scale)
        tb.refresh_btn.clicked.connect(self._force_refresh)
        tb.settings_btn.clicked.connect(self._open_settings_dialog)
        tb.order_btn.clicked.connect(self._on_order_btn_clicked)
        tb.toolbar_preferences_changed.connect(self._on_toolbar_preferences_changed)

    def _make_loading_widget(self) -> QWidget:
        w = QWidget()
        from PySide6.QtWidgets import QVBoxLayout
        lay = QVBoxLayout(w)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label = QLabel("Loading chart…")
        self.loading_label.setObjectName("loadingLabel")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.loading_label)
        return w

    def _make_error_widget(self) -> QWidget:
        from PySide6.QtWidgets import QVBoxLayout, QPushButton
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.error_label = QLabel("Failed to load chart")
        self.error_label.setObjectName("errorLabel")
        self.error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        retry_btn = QPushButton("Retry")
        retry_btn.setObjectName("retryButton")
        retry_btn.clicked.connect(self._retry_load)
        lay.addWidget(self.error_label)
        lay.addWidget(retry_btn)
        return w

    @Slot(int)
    def _on_timeframe_selected(self, index: int) -> None:
        if not self.toolbar.timeframe_dropdown:
            return
        interval = self.toolbar.timeframe_dropdown.itemData(index)
        if interval:
            self._change_timeframe(interval)

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+A"), self).activated.connect(self._auto_scale)
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self._save_drawings)
        QShortcut(QKeySequence("F5"),     self).activated.connect(self._force_refresh)

    # ═══════════════════════════════════════════════════════════════════════
    # CHART LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════════

    def _load_chart_data(self, force_refresh: bool = False) -> None:
        if not self.current_symbol:
            return

        token = self.current_instrument_token
        if not token and self.instrument_loader:
            try:
                token = self.instrument_loader.get_token(self.current_symbol)
                self.current_instrument_token = token
            except Exception as exc:
                logger.error("Instrument lookup failed: %s", exc)

        if not token:
            if not self.instrument_map:
                logger.info(
                    "Deferring chart load for %s until instruments are available",
                    self.current_symbol,
                )
                self.progress_bar.hide()
                self._set_state(ChartState.IDLE)
                return

            self._show_error(f"Instrument token unavailable for {self.current_symbol}")
            return

        self._stop_loader()
        self._set_state(ChartState.LOADING)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.loading_label.setText(f"Loading {self.current_symbol}…")

        load_key = f"{self.current_symbol}_{self.current_interval}"
        self._active_load_key = load_key

        self.data_loader_thread = ChartDataLoaderThread(
            data_fetcher=self.data_fetcher,
            cache=self.data_cache,
            symbol=self.current_symbol,
            instrument_token=token,
            interval=self.current_interval,
            force_refresh=force_refresh,
        )
        self.data_loader_thread.data_loaded.connect(
            lambda df, key: self._on_data_loaded(df, key)
        )
        self.data_loader_thread.load_error.connect(
            lambda msg: self._on_load_error(msg, load_key)
        )
        self.data_loader_thread.load_progress.connect(self.progress_bar.setValue)
        self.data_loader_thread.finished.connect(self._on_thread_finished)
        self.data_loader_thread.start()

    @Slot(object, str)
    def _on_data_loaded(self, df: pd.DataFrame, key: str) -> None:
        if key != self._active_load_key:
            logger.debug("Discarding stale data for %s", key)
            return
        self.last_df = df
        metrics = calculate_metrics(df)

        # Build render data
        candles, volumes = [], []
        for _, row in df.iterrows():
            ts = int(row["time"].timestamp() * 1000)
            candles.append({"time": ts, "open": float(row["open"]), "high": float(row["high"]),
                            "low": float(row["low"]), "close": float(row["close"]),
                            "volume": float(row["volume"])})
            volumes.append({"time": ts, "value": float(row["volume"])})

        saved_state = self.drawing_storage.load_state(self.current_symbol, self.current_interval)
        initial_zoom = saved_state.get("visible_candle_count",
                                       self.current_visible_candle_count)
        # Indicators are global, not per-symbol. Load once and apply everywhere.
        initial_indicator_visibility = self.drawing_storage.load_global_indicator_visibility()
        self._indicator_visibility = initial_indicator_visibility
        self._apply_indicator_toolbar_state(initial_indicator_visibility)
        drawings_json = json.dumps(saved_state.get("drawings", {}))

        cfg = ChartHtmlConfig(
            candlestick_data       = candles,
            volume_data            = volumes,
            ema_data               = metrics.ema_data,
            adr                    = metrics.adr,
            pct_changes            = metrics.pct_changes,
            interval               = self.current_interval,
            symbol                 = self.current_symbol,
            initial_drawings_json  = drawings_json,
            watermark_description  = self._current_watermark_description,
            show_watermark_description = self._show_watermark_description,
            visible_candle_count   = initial_zoom,
            candle_width           = self._current_candle_width,
            candle_spacing         = self._current_candle_spacing,
            up_candle_color        = self._current_up_color,
            down_candle_color      = self._current_down_color,
            up_volume_color        = self._current_volume_up_color,
            down_volume_color      = self._current_volume_down_color,
            watermark_enabled      = self._watermark_enabled,
            watermark_color        = self._watermark_color,
            watermark_opacity      = self._watermark_opacity,
            watermark_position     = self._watermark_position,
            watermark_font_size    = self._watermark_font_size,
            indicator_scale_labels_enabled = self._indicator_scale_labels_enabled,
            crosshair_snap_enabled = self._crosshair_snap_enabled,
            tool_selection_mode    = self._tool_selection_mode,
            initial_indicator_visibility = initial_indicator_visibility,
        )

        self._render_html(cfg)
        self._update_symbol_info(df)
        self._set_state(ChartState.LOADED)
        self.symbol_loaded.emit(self.current_symbol)
        self.data_request_for_symbol.emit(self.current_symbol)
        logger.info("Chart loaded: %s (%d candles)", self.current_symbol, len(df))

    def _render_html(self, cfg: ChartHtmlConfig) -> None:
        if not self.chart_view:
            self._create_chart_view()
        html = build_chart_html(cfg)
        self.chart_view.setHtml(html)

    def _create_chart_view(self) -> None:
        # Tear down previous view
        if self.chart_view:
            self.chart_layout.removeWidget(self.chart_view)
            self.chart_view.deleteLater()
            self.chart_view = None
        if self.channel:
            self.channel.deleteLater()
            self.channel = None

        self.chart_bridge = ChartBridge(self)
        self.chart_view   = QWebEngineView(self.chart_container)
        self.channel      = QWebChannel(self.chart_view.page())
        self.channel.registerObject("chartBridge", self.chart_bridge)
        self.chart_view.page().setWebChannel(self.channel)

        # Wire bridge signals
        self.chart_bridge.chart_ready.connect(self._on_chart_ready)
        self.chart_bridge.drawings_changed.connect(self._on_drawings_changed)
        self.chart_bridge.visible_candle_count_changed.connect(self._on_zoom_changed)
        self.chart_bridge.text_note_requested.connect(self._open_text_note_dialog)
        self.chart_bridge.text_note_edit_requested.connect(self._open_text_note_edit_dialog)
        self.chart_bridge.drawing_tool_cleared.connect(self._clear_active_tool_ui)
        self.chart_bridge.alert_creation_requested.connect(self.alert_creation_requested)
        self.chart_bridge.alert_price_updated.connect(self._on_alert_price_updated)
        self.chart_bridge.order_dialog_requested.connect(self.order_dialog_requested)

        self.chart_layout.addWidget(self.chart_view)

    # ── Live updates ──────────────────────────────────────────────────────

    def _process_tick(self, tick: Dict[str, Any]) -> None:
        sym   = tick.get("tradingsymbol")
        price = tick.get("last_price")
        token = tick.get("instrument_token")
        if price is None:
            return
        sym_match   = sym   == self.current_symbol
        token_match = token == self.current_instrument_token if token and self.current_instrument_token else False
        if not (sym_match or token_match):
            return

        self.current_ltp = float(price)
        self._refresh_toolbar_symbol_text()

        if self.chart_view and self.current_state == ChartState.LOADED:
            self._js(f"if(window.chart) window.chart.updateLivePrice({price});")

    # ── Bridge callbacks ──────────────────────────────────────────────────

    @Slot()
    def _on_chart_ready(self) -> None:
        logger.debug("Chart JS ready for %s", self.current_symbol)
        self.set_watermark(
            self.current_symbol,
            self._current_watermark_description,
            self._show_watermark_description,
        )

    def set_watermark(self, symbol: str, description: str = "", show_description: bool = False) -> None:
        """Push watermark symbol/description state into the JS renderer."""
        payload = json.dumps({
            "symbol": str(symbol or ""),
            "description": str(description or ""),
            "showDescription": bool(show_description),
        })
        self._js(
            "if(window.chart){"
            f"const wm={payload}; window.chart.setWatermark(wm.symbol, wm.description, wm.showDescription);"
            "}"
        )

    @Slot(str)
    def _on_drawings_changed(self, drawings_json: str) -> None:
        if not (self.current_symbol and self.current_state == ChartState.LOADED):
            return
        try:
            drawings_data = json.loads(drawings_json)
            state = self.drawing_storage.load_state(self.current_symbol, self.current_interval)
            if state.get("drawings") == drawings_data:
                return
            state["drawings"] = drawings_data
            self.drawing_storage.save_state(self.current_symbol, self.current_interval, state)
        except Exception as exc:
            logger.error("_on_drawings_changed error: %s", exc)

    @Slot(int)
    def _on_zoom_changed(self, count: int) -> None:
        self.current_visible_candle_count = count
        if self.current_symbol and self.current_state == ChartState.LOADED:
            state = self.drawing_storage.load_state(self.current_symbol, self.current_interval)
            state["visible_candle_count"] = count
            self.drawing_storage.save_state(self.current_symbol, self.current_interval, state)

    @Slot(str)
    def _on_alert_price_updated(self, payload: str) -> None:
        """
        Relay alert drag event from bridge outward.
        main_window connects this → alert_system.update_alert_price_from_chart.
        """
        logger.info(f"CandlestickChart: alert price updated from chart: {payload}")
        self.alert_price_updated.emit(payload)

    # ── Drawing tool methods ──────────────────────────────────────────────

    def _activate_drawing_tool(self, tool_id: str) -> None:
        self.current_drawing_tool = tool_id
        self.toolbar.measure_btn.setChecked(False)
        self.toolbar.set_draw_btn_active(tool_id)
        self._js(f"if(window.chart) window.chart.setDrawingTool('{tool_id}', true, "
                 f"'{self.current_drawing_color}', {self.current_line_width});")

    def _clear_active_tool(self) -> None:
        self.current_drawing_tool = ""
        self.toolbar.measure_btn.setChecked(False)
        self.toolbar.reset_draw_btn()
        self._js("if(window.chart) window.chart.setDrawingTool('', false);")

    def _clear_active_tool_ui(self) -> None:
        self.current_drawing_tool = ""
        self.toolbar.measure_btn.setChecked(False)
        self.toolbar.reset_draw_btn()

    def _toggle_measure_tool(self, checked: bool) -> None:
        if checked:
            self.current_drawing_tool = "measure"
            self.toolbar.reset_draw_btn()
            self._js("if(window.chart) window.chart.setDrawingTool('measure', true);")
        else:
            if self.current_drawing_tool == "measure":
                self.current_drawing_tool = ""
            self._js("if(window.chart) window.chart.setDrawingTool('', false);")

    def _choose_drawing_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.current_drawing_color), self)
        if color.isValid():
            self.current_drawing_color = color.name()
            self.toolbar.set_drawing_color(self.current_drawing_color)
            self._js(f"if(window.chart) window.chart.updateDrawingStyle('{self.current_drawing_color}', {self.current_line_width});")

    @Slot(dict)
    def _on_toolbar_preferences_changed(self, prefs: Dict[str, Any]) -> None:
        self._save_global_settings_patch({"toolbar_preferences": dict(prefs or {})})

    def _toggle_indicator(self, key: str, visible: bool) -> None:
        self._indicator_visibility[key] = visible
        self._js(f"if(window.chart) window.chart.setIndicatorVisibility('{key}', {str(visible).lower()});")
        self.drawing_storage.save_global_indicator_visibility(self._indicator_visibility)

    def _save_drawings(self) -> None:
        if not (self.chart_view and self.current_symbol):
            return

        symbol = self.current_symbol
        interval = self.current_interval

        def _cb(state_data):
            self._persist_state_snapshot(symbol, interval, state_data)

        self.chart_view.page().runJavaScript(
            "(function(){ if(window.chart) return {"
            "  drawings: window.chart.getAllDrawings(),"
            "  visible_candle_count: window.chart.getVisibleCandleCount(),"
            "  indicator_visibility: window.chart.getIndicatorVisibility()"
            "}; return null; })()", _cb
        )

    # ── Text note dialogs ─────────────────────────────────────────────────

    @Slot(str)
    def _open_text_note_dialog(self, mouse_pos_json: str) -> None:
        pos = json.loads(mouse_pos_json)
        dlg = TextNoteDialog(self)
        if dlg.exec():
            note = {"text": dlg.text, "color": dlg.color, "size": dlg.size,
                    "x": pos["x"], "y": pos["y"]}
            self._js(f"if(window.chart) window.chart.addTextNoteFromDialog({json.dumps(note)});")

    @Slot(str)
    def _open_text_note_edit_dialog(self, note_json: str) -> None:
        note = json.loads(note_json)
        dlg  = TextNoteDialog(self, text=note.get("text",""), color=note.get("color","#FFD700"), size=note.get("size",12))
        if dlg.exec():
            note["text"]  = dlg.text
            note["color"] = dlg.color
            note["size"]  = dlg.size
            self._js(f"if(window.chart) window.chart.updateTextNote({json.dumps(note)});")

    # ── Settings dialog ───────────────────────────────────────────────────

    def _open_settings_dialog(self) -> None:
        current = {
            "candle_width":           self._current_candle_width,
            "candle_spacing":         self._current_candle_spacing,
            "default_visible_candles": self.current_visible_candle_count,
            "up_candle_color":        self._current_up_color,
            "down_candle_color":      self._current_down_color,
            "up_volume_color":        self._current_volume_up_color,
            "down_volume_color":      self._current_volume_down_color,
            "watermark_enabled":      self._watermark_enabled,
            "show_watermark_description": self._show_watermark_description,
            "watermark_color":        self._watermark_color,
            "watermark_opacity":      self._watermark_opacity,
            "watermark_position":     self._watermark_position,
            "watermark_font_size":    self._watermark_font_size,
            "indicator_scale_labels_enabled": self._indicator_scale_labels_enabled,
            "crosshair_snap_enabled": self._crosshair_snap_enabled,
            "tool_selection_mode": self._tool_selection_mode,
            "toolbar_symbol_display": self._toolbar_symbol_display,
        }
        dlg = ChartSettingsDialog(current, self)
        dlg.settings_changed.connect(self._apply_chart_settings)
        dlg.exec()

    @Slot(dict)
    def _apply_chart_settings(self, s: Dict[str, Any]) -> None:
        self._current_candle_width       = s["candle_width"]
        self._current_candle_spacing     = s["candle_spacing"]
        self.current_visible_candle_count = s["default_visible_candles"]
        self._current_up_color           = s["up_candle_color"]
        self._current_down_color         = s["down_candle_color"]
        self._current_volume_up_color    = s.get("up_volume_color",   self._current_up_color)
        self._current_volume_down_color  = s.get("down_volume_color", self._current_down_color)
        self._watermark_enabled          = s.get("watermark_enabled",  self._watermark_enabled)
        self._show_watermark_description = s.get("show_watermark_description", self._show_watermark_description)
        self._watermark_color            = s.get("watermark_color",    self._watermark_color)
        self._watermark_opacity          = s.get("watermark_opacity",  self._watermark_opacity)
        self._watermark_position         = s.get("watermark_position", self._watermark_position)
        self._watermark_font_size        = int(s.get("watermark_font_size", self._watermark_font_size))
        self._indicator_scale_labels_enabled = s.get("indicator_scale_labels_enabled", self._indicator_scale_labels_enabled)
        self._crosshair_snap_enabled     = s.get("crosshair_snap_enabled", self._crosshair_snap_enabled)
        self._tool_selection_mode        = s.get("tool_selection_mode", self._tool_selection_mode)
        self._toolbar_symbol_display     = s.get("toolbar_symbol_display", self._toolbar_symbol_display)
        self._save_global_settings_patch(s)
        self._refresh_toolbar_symbol_text()

        if self.chart_view and self.current_state == ChartState.LOADED:
            payload = json.dumps({
                "candleWidth":      self._current_candle_width,
                "candleSpacing":    self._current_candle_spacing,
                "upCandleColor":    self._current_up_color,
                "downCandleColor":  self._current_down_color,
                "upVolumeColor":    self._current_volume_up_color,
                "downVolumeColor":  self._current_volume_down_color,
                "watermarkEnabled": self._watermark_enabled,
                "showWatermarkDescription": self._show_watermark_description,
                "watermarkColor":   self._watermark_color,
                "watermarkOpacity": self._watermark_opacity,
                "watermarkPosition":self._watermark_position,
                "watermarkFontSize":self._watermark_font_size,
                "indicatorScaleLabelsEnabled": self._indicator_scale_labels_enabled,
                "crosshairSnapEnabled": self._crosshair_snap_enabled,
                "toolSelectionMode": self._tool_selection_mode,
            })
            self._js(f"if(window.chart){{ window.chart.setChartSettings({payload});"
                     f"window.chart.setVisibleCandleCount({self.current_visible_candle_count});"
                     "window.chart.autoScale(); }}")
            self.set_watermark(
                self.current_symbol,
                self._current_watermark_description,
                self._show_watermark_description,
            )

    def _save_global_settings_patch(self, patch: Dict[str, Any]) -> None:
        settings = self.drawing_storage.load_global_settings()
        settings.update(dict(patch or {}))
        settings["indicator_visibility"] = dict(self._indicator_visibility or {})
        settings.setdefault("toolbar_preferences", self.toolbar.get_toolbar_preferences())
        self.drawing_storage.save_global_settings(settings)

    # ── Misc actions ──────────────────────────────────────────────────────

    def _change_timeframe(self, interval: str) -> None:
        if interval and interval != self.current_interval:
            if self.current_symbol:
                self._save_current_state_sync()
            self.current_interval = interval
            if self.current_symbol:
                self.drawing_storage.save_last_viewed_symbol(self.current_symbol, self.current_interval)
                self._load_chart_data()

    def _auto_scale(self) -> None:
        self._js("if(window.autoScale) window.autoScale();")

    def _force_refresh(self) -> None:
        if self.current_symbol:
            self._save_current_state_sync()
            self._load_chart_data(force_refresh=True)

    def _retry_load(self) -> None:
        if self.current_symbol:
            self._load_chart_data()

    def _on_order_btn_clicked(self) -> None:
        if self.current_symbol and self.current_ltp > 0:
            self.order_button_clicked.emit(self.current_symbol, self.current_ltp)
        else:
            QMessageBox.warning(self, "No Symbol", "Please load a symbol first.")

    # ── Symbol info label ─────────────────────────────────────────────────

    def _update_symbol_info(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        last_close = float(df.iloc[-1]["close"])
        self.current_ltp = last_close
        self._refresh_toolbar_symbol_text()

    def _refresh_toolbar_symbol_text(self) -> None:
        if self._toolbar_symbol_display == "description" and self._current_watermark_description:
            self.toolbar.set_symbol_text(self._current_watermark_description)
            return
        self.toolbar.set_symbol_text(self.current_symbol)

    # ── State management ──────────────────────────────────────────────────

    def _set_state(self, state: ChartState) -> None:
        self.current_state = state
        idx = {ChartState.IDLE: 0, ChartState.LOADING: 0,
               ChartState.ERROR: 1, ChartState.LOADED: 2}.get(state, 0)
        self.stack.setCurrentIndex(idx)

    def _show_error(self, msg: str) -> None:
        self.error_label.setText(f"Error: {msg}")
        self._set_state(ChartState.ERROR)

    @Slot(str)
    def _on_load_error(self, msg: str, key: Optional[str] = None) -> None:
        if key and key != self._active_load_key:
            return
        self._show_error(msg)

    def _on_thread_finished(self) -> None:
        self.progress_bar.hide()
        if self.data_loader_thread:
            self.data_loader_thread.quit()
            self.data_loader_thread.wait(3000)
            if self.data_loader_thread.isRunning():
                self.data_loader_thread.terminate()
            self.data_loader_thread.deleteLater()
            self.data_loader_thread = None

    # ── Save state helper ─────────────────────────────────────────────────

    def _save_current_state_sync(self) -> None:
        """Best-effort synchronous state save (used before symbol/interval switch)."""
        if not (self.chart_view and self.current_symbol):
            return

        symbol = self.current_symbol
        interval = self.current_interval

        def _cb(data):
            self._persist_state_snapshot(symbol, interval, data)

        self.chart_view.page().runJavaScript(
            "(function(){ if(!window.chart) return null;"
            "return { drawings: window.chart.getAllDrawings(),"
            "         visible_candle_count: window.chart.getVisibleCandleCount(),"
            "         indicator_visibility: window.chart.getIndicatorVisibility() }; })()", _cb
        )

    def _persist_state_snapshot(self, symbol: str, interval: str, snapshot: Any) -> None:
        """Persist chart snapshot for a specific symbol/interval without cross-symbol bleed."""
        if not symbol or not isinstance(snapshot, dict):
            return

        state = self.drawing_storage.load_state(symbol, interval)
        if "drawings" in snapshot:
            state["drawings"] = snapshot["drawings"]
        if "visible_candle_count" in snapshot:
            state["visible_candle_count"] = snapshot["visible_candle_count"]
        if "indicator_visibility" in snapshot and isinstance(snapshot["indicator_visibility"], dict):
            self._indicator_visibility = {
                **DEFAULT_INDICATOR_VISIBILITY,
                **snapshot["indicator_visibility"],
            }
            self.drawing_storage.save_global_indicator_visibility(self._indicator_visibility)

        self.drawing_storage.save_state(symbol, interval, state)

    def _apply_indicator_toolbar_state(self, visibility: Dict[str, bool]) -> None:
        for key, action in self.toolbar.indicator_actions.items():
            # Default False — never light up an indicator the user hasn't enabled
            target = bool(visibility.get(key, False))
            if action.isChecked() == target:
                continue
            action.blockSignals(True)
            action.setChecked(target)
            action.blockSignals(False)

    # ── Thread cleanup ────────────────────────────────────────────────────

    def _stop_loader(self) -> None:
        if self.data_loader_thread and self.data_loader_thread.isRunning():
            self.data_loader_thread.stop()
            self.data_loader_thread.quit()
            self.data_loader_thread.wait(3000)
            if self.data_loader_thread.isRunning():
                self.data_loader_thread.terminate()
            self.data_loader_thread.deleteLater()
            self.data_loader_thread = None

    # ── JS helper ─────────────────────────────────────────────────────────

    def _js(self, code: str) -> None:
        if self.chart_view:
            try:
                self.chart_view.page().runJavaScript(code)
            except Exception as exc:
                logger.debug("_js error: %s", exc)

    # ── Close event ───────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        try:
            if self.current_symbol and self.chart_view:
                self._save_current_state_sync()
                self.drawing_storage.save_last_viewed_symbol(self.current_symbol, self.current_interval)
            self._stop_loader()
            self.data_cache.clear()
            if self.channel:
                self.channel.deleteLater()
                self.channel = None
        except Exception as exc:
            logger.error("closeEvent error: %s", exc)
        super().closeEvent(event)

    # ═══════════════════════════════════════════════════════════════════════
    # STYLES
    # ═══════════════════════════════════════════════════════════════════════

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            QWidget { background-color: #0b0f18; }

            QLabel#loadingLabel {
                color: #4a6090;
                font-size: 13px;
                font-weight: 500;
            }
            QLabel#errorLabel {
                color: #a04040;
                font-size: 13px;
            }
            QPushButton#retryButton {
                background-color: #1a2535;
                color: #7090c0;
                border: 1px solid #2a3a55;
                padding: 5px 14px;
                border-radius: 3px;
                font-size: 12px;
            }
            QPushButton#retryButton:hover { border-color: #4070b0; color: #a0c0e0; }

            QProgressBar {
                background-color: #0e1420;
                border: none;
            }
            QProgressBar::chunk { background-color: #1d5aa0; }
        """)
