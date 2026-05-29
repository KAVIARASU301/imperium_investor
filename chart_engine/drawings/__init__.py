# chart_engine/drawings/__init__.py
#
# Manages persistent storage of chart drawings, zoom level, and global settings.
# Saves one JSON file per symbol in a user_data directory (interval-independent).
# Also handles global chart settings (candle colors, watermark, etc.)
# and the last-viewed symbol so the chart restores state on reopen.

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)

# ─── Default values ──────────────────────────────────────────────────────────

_DEFAULT_GLOBAL_SETTINGS: Dict[str, Any] = {
    "candle_width": 7,
    "candle_spacing": 3,
    "default_visible_candles": 100,
    "right_buffer_candles": 20,
    "up_candle_color": "#00c896",
    "down_candle_color": "#e84060",
    "up_volume_color": "#00c896",
    "down_volume_color": "#e84060",
    "watermark_enabled": True,
    "watermark_color": "#ffffff",
    "watermark_opacity": 0.28,
    "watermark_position": "bottom_center",
    "watermark_font_size": 50,
    "watermark_description_opacity": 0.13,
    "watermark_description_font_size": 25,
    "indicator_scale_labels_enabled": False,
    "crosshair_snap_enabled": False,
    "show_time_slider": True,
    "show_premarket_candles": True,
    "tool_selection_mode": "single_use",
    "toolbar_symbol_display": "description",
    "show_watermark_description": True,
    "price_scale_currency": "",
    "history_days_by_interval": {
        "minute": 5,
        "3minute": 10,
        "5minute": 10,
        "10minute": 10,
        "15minute": 10,
        "30minute": 30,
        "60minute": 50,
        "day": 100,
        "week": 1000,
        "month": 2000,
    },
    "indicator_visibility": {},
}

_DEFAULT_DRAWINGS: Dict[str, list] = {
    "lines": [],
    "rectangles": [],
    "notes": [],
    "horizontal_lines": [],
    "horizontal_rays": [],
    "arrow_lines": [],
    "fibonacci": [],
}

# All indicators OFF by default — user explicitly enables what they want.
# volume is the only exception (on by default so the chart isn't empty).
_DEFAULT_INDICATOR_VISIBILITY: Dict[str, bool] = {
    "ema10": False,
    "ema20": False,
    "ema50": False,
    "ema200": False,
    "bjTrend": False,
    "vwap": False,
    "atrTrendReversal": False,
    "volume": True,
    "cvd": False,
    "rsi": False,
}


def _validate_drawings(drawings: Any) -> Dict[str, list]:
    """Ensure the drawings dict is structurally valid."""
    if not isinstance(drawings, dict):
        return dict(_DEFAULT_DRAWINGS)
    result = {}
    for key in _DEFAULT_DRAWINGS:
        val = drawings.get(key, [])
        result[key] = val if isinstance(val, list) else []
    return result


def _validate_indicator_visibility(visibility: Any) -> Dict[str, bool]:
    """
    Validate indicator visibility payload.
    Returns saved values as-is; only fills in MISSING keys with defaults.
    Never overrides an explicit False that the user saved.
    """
    # Start with defaults (all False except volume)
    result = dict(_DEFAULT_INDICATOR_VISIBILITY)
    if not isinstance(visibility, dict):
        return result
    # Overwrite defaults only for keys that exist in the saved payload
    for key in _DEFAULT_INDICATOR_VISIBILITY:
        if key in visibility:
            result[key] = bool(visibility[key])
    # Also preserve any extra keys not in defaults (future indicators)
    for key, val in visibility.items():
        if key not in result:
            result[key] = bool(val)
    return result


class DrawingStorage:
    """
    Persistent store for per-symbol chart state (drawings + zoom) and
    global chart settings shared across all symbols.
    """

    def __init__(self, storage_dir: str = "kite/user_data/chart_drawings"):
        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)
        self.global_settings_file = os.path.join(storage_dir, "global_chart_settings.json")

    # ─── Per-symbol state ─────────────────────────────────────────────────────

    def save_state(self, symbol: str, interval: str, state: Dict[str, Any]) -> None:
        """Persist drawings + view state for a symbol (interval-independent)."""
        if not isinstance(state, dict):
            logger.warning("save_state: invalid state type %s for %s", type(state), symbol)
            return
        if "drawings" in state:
            state["drawings"] = _validate_drawings(state["drawings"])
        else:
            state["drawings"] = dict(_DEFAULT_DRAWINGS)
        state["indicator_visibility"] = _validate_indicator_visibility(
            state.get("indicator_visibility", {})
        )

        filepath = self._state_path(symbol, interval)
        try:
            with open(filepath, "w") as f:
                json.dump(state, f, indent=2)
            logger.info(
                "Saved state for %s (%s) — %d drawings",
                symbol, interval, self._count_drawings(state["drawings"]),
            )
        except Exception as exc:
            logger.error("Failed to save state for %s: %s", symbol, exc)

    def load_state(self, symbol: str, interval: str) -> Dict[str, Any]:
        """Load drawings + view state for a symbol (interval-independent)."""
        filepath = self._state_path(symbol, interval)
        legacy_filepath = self._legacy_state_path(symbol, interval)
        source_path = filepath
        if not os.path.exists(source_path):
            source_path = legacy_filepath
        if not source_path or not os.path.exists(source_path):
            return self._default_state()
        try:
            with open(source_path, "r") as f:
                state = json.load(f)
            if not isinstance(state, dict):
                return self._default_state()
            state["drawings"] = _validate_drawings(state.get("drawings", {}))
            state.setdefault("visible_candle_count", 100)
            state["indicator_visibility"] = _validate_indicator_visibility(
                state.get("indicator_visibility", {})
            )
            logger.info(
                "Loaded state for %s (%s) — %d drawings",
                symbol, interval, self._count_drawings(state["drawings"]),
            )
            if source_path == legacy_filepath:
                self.save_state(symbol, interval, state)
            return state
        except Exception as exc:
            logger.error("Failed to load state for %s: %s", symbol, exc)
            return self._default_state()

    def _default_state(self) -> Dict[str, Any]:
        return {
            "drawings": dict(_DEFAULT_DRAWINGS),
            "visible_candle_count": 100,
            "indicator_visibility": dict(_DEFAULT_INDICATOR_VISIBILITY),
        }

    def _state_path(self, symbol: str, interval: str) -> str:
        del interval
        safe = symbol.replace("/", "_").replace(":", "_")
        return os.path.join(self.storage_dir, f"{safe}_state.json")

    def _legacy_state_path(self, symbol: str, interval: str) -> str:
        safe = symbol.replace("/", "_").replace(":", "_")
        return os.path.join(self.storage_dir, f"{safe}_{interval}_state.json")

    def _count_drawings(self, drawings: Dict[str, list]) -> int:
        if not isinstance(drawings, dict):
            return 0
        return sum(len(v) for v in drawings.values() if isinstance(v, list))

    # ─── Global settings ──────────────────────────────────────────────────────

    def save_global_settings(self, settings: Dict[str, Any]) -> None:
        payload = dict(settings or {})
        if "indicator_visibility" in payload:
            payload["indicator_visibility"] = _validate_indicator_visibility(
                payload.get("indicator_visibility")
            )
        try:
            with open(self.global_settings_file, "w") as f:
                json.dump(payload, f, indent=2)
            logger.info("Saved global chart settings.")
        except Exception as exc:
            logger.error("Failed to save global settings: %s", exc)

    def load_global_settings(self) -> Dict[str, Any]:
        if not os.path.exists(self.global_settings_file):
            return dict(_DEFAULT_GLOBAL_SETTINGS)
        try:
            with open(self.global_settings_file, "r") as f:
                settings = json.load(f)
            result = dict(_DEFAULT_GLOBAL_SETTINGS)
            result.update(settings)
            result["indicator_visibility"] = _validate_indicator_visibility(
                result.get("indicator_visibility")
            )
            logger.info("Loaded global chart settings.")
            return result
        except Exception as exc:
            logger.error("Failed to load global settings: %s", exc)
            return dict(_DEFAULT_GLOBAL_SETTINGS)

    def save_global_indicator_visibility(self, visibility: Dict[str, Any]) -> None:
        """Persist indicator visibility once and reuse for all symbols."""
        settings = self.load_global_settings()
        settings["indicator_visibility"] = _validate_indicator_visibility(visibility)
        self.save_global_settings(settings)

    def load_global_indicator_visibility(self) -> Dict[str, bool]:
        """Load shared indicator visibility used across all chart symbols."""
        settings = self.load_global_settings()
        return _validate_indicator_visibility(settings.get("indicator_visibility", {}))

    # ─── Last-viewed symbol ───────────────────────────────────────────────────

    def save_last_viewed_symbol(self, symbol: str, interval: str) -> None:
        try:
            path = os.path.join(self.storage_dir, "last_viewed_symbol.json")
            with open(path, "w") as f:
                json.dump(
                    {"symbol": symbol, "interval": interval, "timestamp": datetime.now().isoformat()},
                    f, indent=2,
                )
        except Exception as exc:
            logger.error("Failed to save last viewed symbol: %s", exc)

    def load_last_viewed_symbol(self) -> Dict[str, str]:
        path = os.path.join(self.storage_dir, "last_viewed_symbol.json")
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r") as f:
                data = json.load(f)
            if isinstance(data, dict) and "symbol" in data and "interval" in data:
                return {"symbol": data["symbol"], "interval": data["interval"]}
        except Exception as exc:
            logger.error("Failed to load last viewed symbol: %s", exc)
        return {}

    def clear_last_viewed_symbol(self) -> None:
        path = os.path.join(self.storage_dir, "last_viewed_symbol.json")
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as exc:
            logger.error("Failed to clear last viewed symbol: %s", exc)
