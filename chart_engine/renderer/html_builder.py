# chart_engine/renderer/html_builder.py
#
# Builds the complete HTML string that QWebEngineView loads.
# Reads chart.js from the same directory and embeds it into the HTML.
# All data is injected as a single window.__CHART_DATA__ object so the JS
# chart class never needs f-string escaping — cleaner and safer.
#
# Public function:
#   build_chart_html(cfg: ChartHtmlConfig) -> str

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_CHART_JS_PATH = os.path.join(os.path.dirname(__file__), "chart.js")

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
    visible_candle_count:    int  = 100
    candle_width:            int  = 3
    candle_spacing:          int  = 3
    up_candle_color:         str  = "#26a69a"
    down_candle_color:       str  = "#ef5350"
    up_volume_color:         str  = "#26a69a"
    down_volume_color:       str  = "#ef5350"
    watermark_enabled:       bool = True
    watermark_color:         str  = "#ffffff"
    watermark_opacity:       float = 0.06
    watermark_position:      str  = "mid_center"
    watermark_font_size:     int  = 0
    indicator_scale_labels_enabled: bool = False
    initial_indicator_visibility: Dict[str, bool] = field(default_factory=dict)
    # ^^^ This is a FALLBACK only for brand-new installs (empty localStorage).
    #     chart.js always prefers its localStorage state over this value.
    #     Your Python ChartBridge should implement:
    #
    #       @Slot(str)
    #       def notify_indicator_visibility_changed(self, json_str: str):
    #           self._indicator_visibility = json.loads(json_str)
    #
    #     and pass self._indicator_visibility as initial_indicator_visibility
    #     on every build_chart_html() call.  The JS localStorage is the primary
    #     persistence store; Python persistence is a secondary safety net.
    qwebchannel_src:         str  = "qrc:///qtwebchannel/qwebchannel.js"


# ─── Builder ──────────────────────────────────────────────────────────────────

def build_chart_html(cfg: ChartHtmlConfig) -> str:
    """
    Produce the complete HTML for the chart.
    Embeds chart.js inline so QWebEngineView.setHtml() can load it without
    needing a base URL or external file access.
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

    # ── Read chart.js ──
    try:
        with open(_CHART_JS_PATH, "r", encoding="utf-8") as f:
            chart_js = f.read()
    except FileNotFoundError:
        logger.error("chart.js not found at %s", _CHART_JS_PATH)
        chart_js = "console.error('chart.js missing');"

    # ── Build data injection block ──
    # This is the ONLY place we touch Python ↔ JS data.
    # The JS class only reads from window.__CHART_DATA__ — no f-string escaping.
    data_obj: Dict[str, Any] = {
        "canvasId":                  "mainCanvas",
        "candlestickData":           cfg.candlestick_data,
        "volumeData":                cfg.volume_data,
        "emaData":                   cfg.ema_data,
        "initialADR":                cfg.adr,
        "percentageChanges":         cfg.pct_changes,
        "currentInterval":           cfg.interval,
        "currentSymbol":             cfg.symbol,
        "initialDrawingsJson":       safe_drawings,
        "initialVisibleCandleCount": cfg.visible_candle_count,
        "initialCandleWidth":        cfg.candle_width,
        "initialCandleSpacing":      cfg.candle_spacing,
        "upCandleColor":             cfg.up_candle_color,
        "downCandleColor":           cfg.down_candle_color,
        "upVolumeColor":             cfg.up_volume_color,
        "downVolumeColor":           cfg.down_volume_color,
        "watermarkEnabled":          cfg.watermark_enabled,
        "watermarkColor":            cfg.watermark_color,
        "watermarkOpacity":          float(max(0.0, min(1.0, cfg.watermark_opacity))),
        "watermarkPosition":         cfg.watermark_position,
        "watermarkFontSize":         int(max(0, cfg.watermark_font_size)),
        "indicatorScaleLabelsEnabled": bool(cfg.indicator_scale_labels_enabled),
        "initialIndicatorVisibility": cfg.initial_indicator_visibility,
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
            font-family: "Segoe UI", "Helvetica Neue", sans-serif;
            color: #c8d0e0;
            overflow: hidden;
        }}

        #chartContainer {{
            width: 100vw;
            height: 100vh;
            position: relative;
        }}

        #mainCanvas {{
            position: absolute;
            top: 0; left: 0;
            width: 100%;
            height: calc(100% - 14px);
            cursor: crosshair;
            display: block;
        }}

        /* ── OHLC / metrics overlay ── */
        #info {{
            position: absolute;
            top: 8px; left: 10px;
            color: #d4def2;
            font-size: 12px;
            pointer-events: none;
            z-index: 5;
            line-height: 1.45;
            font-family: "Inter", "Segoe UI", "Roboto", "Helvetica Neue", Arial, sans-serif;
            font-weight: 600;
            letter-spacing: 0.2px;
            text-shadow: 0 1px 0 rgba(0, 0, 0, 0.25);
        }}
        #metricsInfo {{
            font-size: 12px;
            color: #d1dcf2;
            font-weight: 600;
            font-family: "Inter", "Segoe UI", "Roboto", "Helvetica Neue", Arial, sans-serif;
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
        }}
        #metricsInfo .info-row {{
            margin-bottom: 2px;
        }}
        #metricsInfo .info-row:last-child {{
            margin-bottom: 0;
        }}

        /* ── Time slider ── */
        #timeSlider {{
            position: absolute;
            bottom: 0; left: 0;
            width: 100%; height: 14px;
            background: #080c14;
            border-top: 1px solid #161e2e;
            display: flex;
            align-items: center;
            overflow: hidden;
            user-select: none;
            z-index: 10;
        }}
        #sliderTrack {{
            position: relative;
            height: 2px;
            background: #1e2840;
            border-radius: 999px;
            width: calc(100% - 16px);
            margin: 0 8px;
        }}
        #sliderThumb {{
            position: absolute;
            width: 60px; height: 4px;
            background: linear-gradient(90deg, #2a4070, #3a60a8);
            border: 1px solid rgba(80,120,180,0.5);
            border-radius: 999px;
            cursor: grab;
            z-index: 12;
            box-shadow: 0 0 4px rgba(60,100,180,0.3);
        }}
        #sliderThumb:active {{ cursor: grabbing; }}
    </style>
</head>
<body>
    <div id="chartContainer">
        <canvas id="mainCanvas"></canvas>
        <div id="info">
            <div id="metricsInfo"></div>
        </div>
        <div id="timeSlider">
            <div id="sliderTrack">
                <div id="sliderThumb"></div>
            </div>
        </div>
    </div>

    <script src="{cfg.qwebchannel_src}"></script>

    <script>
        // ── Inject chart data ───────────────────────────────────────────────
        window.__CHART_DATA__ = {data_json};
        window.__chartInitialized = false;
    </script>

    <script>
        // ── Chart engine ────────────────────────────────────────────────────
        {chart_js}
    </script>
</body>
</html>"""

    return html
