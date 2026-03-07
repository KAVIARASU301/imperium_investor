# chart_engine/drawings/drawing_storage.py
#
# Manages persistent storage of chart drawings, zoom level, and global settings.
# Saves one JSON file per (symbol, interval) pair in a user_data directory.
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
    "candle_width": 3,
    "candle_spacing": 3,
    "default_visible_candles": 100,
    "up_candle_color": "#26a69a",
    "down_candle_color": "#ef5350",
    "up_volume_color": "#26a69a",
    "down_volume_color": "#ef5350",
    "watermark_enabled": True,
    "watermark_color": "#ffffff",
    "watermark_opacity": 0.08,
    "watermark_position": "mid_center",
    "watermark_font_size": 0,
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


def _validate_drawings(drawings: Any) -> Dict[str, list]:
    """Ensure the drawings dict is structurally valid."""
    if not isinstance(drawings, dict):
        return dict(_DEFAULT_DRAWINGS)
    result = {}
    for key in _DEFAULT_DRAWINGS:
        val = drawings.get(key, [])
        result[key] = val if isinstance(val, list) else []
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
        """Persist drawings + view state for a (symbol, interval) pair."""
        if not isinstance(state, dict):
            logger.warning("save_state: invalid state type %s for %s", type(state), symbol)
            return
        # Normalise drawings structure before saving
        if "drawings" in state:
            state["drawings"] = _validate_drawings(state["drawings"])
        else:
            state["drawings"] = dict(_DEFAULT_DRAWINGS)

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
        """Load drawings + view state for a (symbol, interval) pair."""
        filepath = self._state_path(symbol, interval)
        if not os.path.exists(filepath):
            return self._default_state()
        try:
            with open(filepath, "r") as f:
                state = json.load(f)
            if not isinstance(state, dict):
                return self._default_state()
            state["drawings"] = _validate_drawings(state.get("drawings", {}))
            state.setdefault("visible_candle_count", 100)
            logger.info(
                "Loaded state for %s (%s) — %d drawings",
                symbol, interval, self._count_drawings(state["drawings"]),
            )
            return state
        except Exception as exc:
            logger.error("Failed to load state for %s: %s", symbol, exc)
            return self._default_state()

    def _default_state(self) -> Dict[str, Any]:
        return {"drawings": dict(_DEFAULT_DRAWINGS), "visible_candle_count": 100}

    def _state_path(self, symbol: str, interval: str) -> str:
        safe = symbol.replace("/", "_").replace(":", "_")
        return os.path.join(self.storage_dir, f"{safe}_{interval}_state.json")

    def _count_drawings(self, drawings: Dict[str, list]) -> int:
        if not isinstance(drawings, dict):
            return 0
        return sum(len(v) for v in drawings.values() if isinstance(v, list))

    # ─── Global settings ──────────────────────────────────────────────────────

    def save_global_settings(self, settings: Dict[str, Any]) -> None:
        try:
            with open(self.global_settings_file, "w") as f:
                json.dump(settings, f, indent=2)
            logger.info("Saved global chart settings.")
        except Exception as exc:
            logger.error("Failed to save global settings: %s", exc)

    def load_global_settings(self) -> Dict[str, Any]:
        if not os.path.exists(self.global_settings_file):
            return dict(_DEFAULT_GLOBAL_SETTINGS)
        try:
            with open(self.global_settings_file, "r") as f:
                settings = json.load(f)
            # Fill any missing keys with defaults (backward compatibility)
            result = dict(_DEFAULT_GLOBAL_SETTINGS)
            result.update(settings)
            logger.info("Loaded global chart settings.")
            return result
        except Exception as exc:
            logger.error("Failed to load global settings: %s", exc)
            return dict(_DEFAULT_GLOBAL_SETTINGS)

    # ─── Last-viewed symbol ───────────────────────────────────────────────────

    def save_last_viewed_symbol(self, symbol: str, interval: str) -> None:
        try:
            path = os.path.join(self.storage_dir, "last_viewed_symbol.json")
            with open(path, "w") as f:
                json.dump(
                    {"symbol": symbol, "interval": interval, "timestamp": datetime.now().isoformat()},
                    f,
                    indent=2,
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
