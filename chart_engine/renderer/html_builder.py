"""
chart_engine/renderer/html_builder.py  — updated to embed DrawingEngine v2.

Embeds drawing_engine.js and drawing_engine_integration.patch.js before chart.js so FixedTradingChart can initialize DrawingEngine directly.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from utils.resource_path import resource_path

logger = logging.getLogger(__name__)

_CHART_JS_PATH          = resource_path("chart_engine/renderer/chart.js")
_DRAWING_ENGINE_PATH    = resource_path("chart_engine/renderer/drawing_engine.js")
_INTEGRATION_PATCH_PATH = resource_path("chart_engine/renderer/drawing_engine_integration.patch.js")

_JS_SOURCE_CACHE: Dict[str, str] = {}


def _read_js_source(path: str, label: str) -> str:
    """Read embedded JS once per process; first chart render should not re-hit disk."""
    cached = _JS_SOURCE_CACHE.get(path)
    if cached is not None:
        return cached
    try:
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
    except FileNotFoundError:
        logger.error("%s not found at %s", label, path)
        source = f"console.error('{label} missing');"
    _JS_SOURCE_CACHE[path] = source
    return source


# ─── Config dataclass ─────────────────────────────────────────────────────────

@dataclass
class ChartHtmlConfig:
    candlestick_data:        List[Dict]
    volume_data:             List[Dict]
    ema_data:                Dict[str, List[Dict]]
    adr:                     Dict[str, float]
    pct_changes:             Dict[str, float]
    interval:                str
    symbol:                  str
    initial_drawings_json:   str
    watermark_description:   str   = ""
    show_watermark_description: bool = False
    visible_candle_count:    int   = 100
    candle_width:            int   = 3
    candle_spacing:          int   = 3
    right_buffer_candles:    int   = 20
    viewport_right_offset:  float | None = None
    up_candle_color:         str   = "#00c896"
    down_candle_color:       str   = "#e84060"
    up_volume_color:         str   = "#00c896"
    down_volume_color:       str   = "#e84060"
    watermark_enabled:       bool  = True
    watermark_color:         str   = "#ffffff"
    watermark_opacity:       float = 0.28
    watermark_position:      str   = "bottom_center"
    watermark_font_size:     int   = 50
    watermark_description_opacity: float = 0.13
    watermark_description_font_size: int = 25
    indicator_scale_labels_enabled: bool = False
    crosshair_snap_enabled:  bool  = False
    show_time_slider:        bool  = True
    tool_selection_mode:     str   = "single_use"
    chart_type:              str   = "candle"
    initial_indicator_visibility:   Dict[str, bool] = field(default_factory=dict)
    info_visibility:        Dict[str, bool] = field(default_factory=dict)
    price_scale_currency:   str   = ""
    moving_average_configs:  List[Dict] = field(default_factory=list)
    broker_name:             str  = ""
    show_premarket_candles: bool = True
    show_postmarket_candles: bool = True
    qwebchannel_src:         str   = "qrc:///qtwebchannel/qwebchannel.js"


# ─── Builder ──────────────────────────────────────────────────────────────────

def build_chart_html(cfg: ChartHtmlConfig) -> str:
    """
    Produce the complete HTML.  Drawing engine is embedded inline so
    QWebEngineView.setHtml() can load everything without external file access.
    """
    # ── Validate drawings JSON ──
    try:
        drawings_obj = json.loads(cfg.initial_drawings_json)
        safe_drawings = json.dumps(drawings_obj)
    except (json.JSONDecodeError, TypeError):
        safe_drawings = json.dumps({
            "lines": [], "rectangles": [], "notes": [],
            "horizontal_lines": [], "horizontal_rays": [],
            "arrow_lines": [], "fibonacci": []
        })

    # ── Read JS files ──
    drawing_engine_js    = _read_js_source(_DRAWING_ENGINE_PATH,    "drawing_engine.js")
    integration_patch_js = _read_js_source(_INTEGRATION_PATCH_PATH, "drawing_engine_integration.patch.js")
    chart_js             = _read_js_source(_CHART_JS_PATH,           "chart.js")

    # ── Data injection ──
    data_obj: Dict[str, Any] = {
        "canvasId":                  "mainCanvas",
        "candlestickData":           cfg.candlestick_data,
        "volumeData":                cfg.volume_data,
        "emaData":                   cfg.ema_data,
        "initialADR":                cfg.adr,
        "percentageChanges":         cfg.pct_changes,
        "currentInterval":           cfg.interval,
        "currentSymbol":             cfg.symbol,
        "watermarkDescription":      cfg.watermark_description,
        "showWatermarkDescription":  bool(cfg.show_watermark_description),
        "initialDrawingsJson":       safe_drawings,
        "initialVisibleCandleCount": cfg.visible_candle_count,
        "initialCandleWidth":        cfg.candle_width,
        "initialCandleSpacing":      cfg.candle_spacing,
        "rightBufferCandles":        int(max(0, cfg.right_buffer_candles)),
        "viewportRightOffset":       cfg.viewport_right_offset,
        "upCandleColor":             cfg.up_candle_color,
        "downCandleColor":           cfg.down_candle_color,
        "upVolumeColor":             cfg.up_volume_color,
        "downVolumeColor":           cfg.down_volume_color,
        "themePositiveColor":        cfg.up_candle_color,
        "themeNegativeColor":        cfg.down_candle_color,
        "watermarkEnabled":          cfg.watermark_enabled,
        "watermarkColor":            cfg.watermark_color,
        "watermarkOpacity":          float(max(0.0, min(1.0, cfg.watermark_opacity))),
        "watermarkPosition":         cfg.watermark_position,
        "watermarkFontSize":         int(max(0, cfg.watermark_font_size)),
        "watermarkDescriptionOpacity": float(max(0.0, min(1.0, cfg.watermark_description_opacity))),
        "watermarkDescriptionFontSize": int(max(0, cfg.watermark_description_font_size)),
        "indicatorScaleLabelsEnabled": bool(cfg.indicator_scale_labels_enabled),
        "crosshairSnapEnabled":      bool(cfg.crosshair_snap_enabled),
        "showTimeSlider":           bool(cfg.show_time_slider),
        "toolSelectionMode":         cfg.tool_selection_mode,
        "chartType":                 cfg.chart_type,
        "initialIndicatorVisibility": cfg.initial_indicator_visibility,
        "infoVisibility":            cfg.info_visibility,
        "priceScaleCurrency":        (cfg.price_scale_currency or "").upper(),
        "movingAverageConfigs":       cfg.moving_average_configs,
        "brokerName":                 (cfg.broker_name or "").lower(),
        "showPremarketCandles":       bool(cfg.show_premarket_candles),
        "showPostmarketCandles":      bool(cfg.show_postmarket_candles),
    }

    data_json = json.dumps(data_obj)

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{cfg.symbol} — Trading Chart</title>
    <style>
        *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: #0b0f18;
            font-family: "Inter", "Segoe UI", "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
            -webkit-font-smoothing: antialiased;
            text-rendering: geometricPrecision;
            color: #c8d0e0;
            overflow: hidden;
        }}
        #chartContainer {{
            width: 100vw; height: 100vh; position: relative;
            user-select: none; -webkit-user-select: none;
        }}
        #mainCanvas {{
            position: absolute; top: 0; left: 0;
            width: 100%; height: calc(100% - 14px);
            min-width: 200px; min-height: 200px;
            cursor: crosshair; display: block;
        }}
        #info {{
            position: absolute; top: 8px; left: 10px;
            color: #d4def2; font-size: 12px;
            pointer-events: none; z-index: 5;
            line-height: 1.45;
            font-family: "Inter", "Segoe UI", "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
            font-weight: 600; letter-spacing: 0.2px;
            -webkit-font-smoothing: antialiased;
            text-rendering: optimizeLegibility;
            user-select: none; -webkit-user-select: none;
        }}
        #metricsInfo {{ font-size: 12px; color: #d1dcf2; font-weight: 600; white-space: nowrap; }}
        #metricsInfo .info-row {{ margin-bottom: 2px; }}
        #timeSlider {{
            position: absolute; bottom: 0; left: 0;
            width: 100%; height: 14px;
            background: #080c14; border-top: 1px solid #161e2e;
            display: flex; align-items: center; overflow: hidden;
            user-select: none; z-index: 10;
        }}
        #sliderTrack {{
            position: relative; height: 2px; background: #1e2840;
            border-radius: 999px; width: calc(100% - 16px); margin: 0 8px;
        }}
        #sliderThumb {{
            position: absolute; width: 60px; height: 4px;
            background: linear-gradient(90deg, #2a4070, #3a60a8);
            border: 1px solid rgba(80,120,180,0.5);
            border-radius: 999px; cursor: grab; z-index: 12;
        }}
        #sliderThumb:active {{ cursor: grabbing; }}
    </style>
</head>
<body>
    <div id="chartContainer">
        <canvas id="mainCanvas"></canvas>
        <div id="info"><div id="metricsInfo"></div></div>
        <div id="timeSlider">
            <div id="sliderTrack"><div id="sliderThumb"></div></div>
        </div>
    </div>

    <script src="{cfg.qwebchannel_src}"></script>

    <script>
        window.__CHART_DATA__ = {data_json};
        window.__chartInitialized = false;
    </script>

    <!-- Drawing Engine v2 (must come before chart.js) -->
    <script>
        {drawing_engine_js}
    </script>

    <!-- Integration patch (adapter functions) -->
    <script>
        {integration_patch_js}
    </script>

    <!-- Main chart engine -->
    <script>
        {chart_js}
    </script>

</body>
</html>"""

    return html