import copy
import json
import os
from typing import Dict, Any

from PySide6.QtCore import QObject, Signal


DEFAULT_COLOR_THEME: Dict[str, Any] = {
    "link_all_sections": True,
    "enable_table_directional_colors": False,
    "enable_volume_strength_indicator": False,
    "candles": {
        "up": "#26a69a",
        "down": "#ef5350",
    },
    "volume": {
        "up": "#26a69a",
        "down": "#ef5350",
    },
    "tables": {
        "positive": "#26a69a",
        "negative": "#ef5350",
        "neutral": "#a9a9a9",
        "volume": "#45d4ff",
    },
}


class ColorThemeManager(QObject):
    theme_changed = Signal(dict)

    def __init__(self, storage_path: str = None):
        super().__init__()
        self.storage_path = storage_path or os.path.join(os.path.expanduser("~/.swing_trader"), "color_theme.json")
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        self._theme = copy.deepcopy(DEFAULT_COLOR_THEME)
        self.load_theme()

    def load_theme(self) -> Dict[str, Any]:
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r") as f:
                    loaded = json.load(f)
                self._theme = self._merge_with_default(loaded)
            except Exception:
                self._theme = copy.deepcopy(DEFAULT_COLOR_THEME)
        self._normalize_linked_sections(self._theme)
        return self.get_theme()

    def save_theme(self) -> None:
        with open(self.storage_path, "w") as f:
            json.dump(self._theme, f, indent=2)

    def get_theme(self) -> Dict[str, Any]:
        return copy.deepcopy(self._theme)

    def update_theme(self, new_theme: Dict[str, Any]) -> None:
        self._theme = self._merge_with_default(new_theme)
        self._normalize_linked_sections(self._theme)
        self.save_theme()
        self.theme_changed.emit(self.get_theme())

    def _merge_with_default(self, custom: Dict[str, Any]) -> Dict[str, Any]:
        merged = copy.deepcopy(DEFAULT_COLOR_THEME)
        if not isinstance(custom, dict):
            return merged

        merged["link_all_sections"] = bool(custom.get("link_all_sections", merged["link_all_sections"]))
        merged["enable_table_directional_colors"] = bool(
            custom.get("enable_table_directional_colors", merged["enable_table_directional_colors"])
        )
        merged["enable_volume_strength_indicator"] = bool(
            custom.get("enable_volume_strength_indicator", merged["enable_volume_strength_indicator"])
        )

        for section in ("candles", "volume", "tables"):
            section_data = custom.get(section, {})
            if isinstance(section_data, dict):
                for key in merged[section].keys():
                    value = section_data.get(key)
                    if isinstance(value, str) and value.startswith("#"):
                        merged[section][key] = value
        return merged

    def _normalize_linked_sections(self, theme: Dict[str, Any]) -> None:
        if theme.get("link_all_sections"):
            theme["volume"]["up"] = theme["candles"]["up"]
            theme["volume"]["down"] = theme["candles"]["down"]
            theme["tables"]["positive"] = theme["candles"]["up"]
            theme["tables"]["negative"] = theme["candles"]["down"]


_theme_manager: ColorThemeManager = None


def get_color_theme_manager() -> ColorThemeManager:
    global _theme_manager
    if _theme_manager is None:
        _theme_manager = ColorThemeManager()
    return _theme_manager
