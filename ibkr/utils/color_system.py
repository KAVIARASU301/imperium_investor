import copy
import json
import os
from typing import Dict, Any

from PySide6.QtCore import QObject, Signal


BROKER_MODE = "ibkr"
IBKR_TICKER_DEFAULTS = ["SPY", "QQQ"]
KITE_TICKER_DEFAULTS = ["NIFTY", "SENSEX"]
TICKER_DEFAULTS_BY_MODE = {
    "ibkr": IBKR_TICKER_DEFAULTS,
    "kite": KITE_TICKER_DEFAULTS,
}


def _clean_ticker_symbols(value: Any, fallback: list[str]) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if isinstance(value, list):
        cleaned = [str(sym).strip().upper() for sym in value if str(sym).strip()]
        if cleaned:
            return cleaned[:5]
    return list(fallback)


def _ticker_symbols_match_defaults(symbols: list[str], defaults: list[str]) -> bool:
    return [str(sym).strip().upper() for sym in symbols] == defaults


DEFAULT_COLOR_THEME: Dict[str, Any] = {
    "link_all_sections": True,
    "enable_table_directional_colors": True,
    "enable_volume_strength_indicator": False,
    "show_table_vertical_lines": False,
    "show_scanner_volume_column": False,
    "show_watchlist_volume_column": False,
    "scanner_live_ticks": False,
    "status_bar_alignment": "left",
    "status_bar_metrics_right": True,
    "show_account_name": False,
    "show_account_balance": True,
    "preferred_username": "",
    "show_app_title": True,
    "app_title_text": "Swing Trader",
    "dual_chart_mode": True,
    "show_ticker_board": True,
    "ticker_board_symbols": ["SPY", "QQQ"],
    "ticker_board_symbols_by_mode": {
        "ibkr": IBKR_TICKER_DEFAULTS,
        "kite": KITE_TICKER_DEFAULTS,
    },
    "global": {
        "positive": "#00d4a8",
        "negative": "#ff4d6a",
    },
    "candles": {
        "up": "#00C896",
        "down": "#E84060",
    },
    "volume": {
        "up": "#00C896",
        "down": "#E84060",
    },
    "tables": {
        "positive": "#00d4a8",
        "negative": "#ff4d6a",
        "neutral": "#7a94b0",
        "volume": "#00d4ff",
    },
}


class ColorThemeManager(QObject):
    theme_changed = Signal(dict)

    def __init__(self, storage_path: str = None):
        super().__init__()
        self.storage_path = storage_path or os.path.join(os.path.expanduser("~/.qullamaggie"), "color_theme.json")
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
        if isinstance(new_theme, dict) and "ticker_board_symbols" in new_theme:
            new_theme = copy.deepcopy(new_theme)
            symbols_by_mode = new_theme.get("ticker_board_symbols_by_mode")
            if not isinstance(symbols_by_mode, dict):
                symbols_by_mode = copy.deepcopy(self._theme.get("ticker_board_symbols_by_mode", {}))
            symbols_by_mode[BROKER_MODE] = new_theme.get("ticker_board_symbols")
            new_theme["ticker_board_symbols_by_mode"] = symbols_by_mode

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
        merged["show_table_vertical_lines"] = bool(
            custom.get("show_table_vertical_lines", merged["show_table_vertical_lines"])
        )
        merged["show_scanner_volume_column"] = bool(
            custom.get("show_scanner_volume_column", merged["show_scanner_volume_column"])
        )
        merged["show_watchlist_volume_column"] = bool(
            custom.get("show_watchlist_volume_column", merged["show_watchlist_volume_column"])
        )
        merged["scanner_live_ticks"] = bool(custom.get("scanner_live_ticks", merged["scanner_live_ticks"]))
        alignment = str(custom.get("status_bar_alignment", merged["status_bar_alignment"]))
        merged["status_bar_alignment"] = "right" if alignment.lower() == "right" else "left"
        merged["status_bar_metrics_right"] = bool(
            custom.get("status_bar_metrics_right", merged["status_bar_metrics_right"])
        )
        merged["show_account_name"] = bool(custom.get("show_account_name", merged["show_account_name"]))
        merged["show_account_balance"] = bool(custom.get("show_account_balance", merged["show_account_balance"]))
        merged["preferred_username"] = str(custom.get("preferred_username", merged["preferred_username"])).strip()
        merged["show_app_title"] = bool(custom.get("show_app_title", merged["show_app_title"]))
        app_title_text = str(custom.get("app_title_text", merged["app_title_text"])).strip()
        merged["app_title_text"] = app_title_text or DEFAULT_COLOR_THEME["app_title_text"]
        merged["dual_chart_mode"] = bool(custom.get("dual_chart_mode", merged["dual_chart_mode"]))
        merged["show_ticker_board"] = bool(custom.get("show_ticker_board", merged["show_ticker_board"]))

        ticker_symbols_by_mode = copy.deepcopy(merged["ticker_board_symbols_by_mode"])
        custom_symbols_by_mode = custom.get("ticker_board_symbols_by_mode")
        if isinstance(custom_symbols_by_mode, dict):
            for broker_mode, defaults in TICKER_DEFAULTS_BY_MODE.items():
                ticker_symbols_by_mode[broker_mode] = _clean_ticker_symbols(
                    custom_symbols_by_mode.get(broker_mode),
                    defaults,
                )

        active_defaults = TICKER_DEFAULTS_BY_MODE[BROKER_MODE]
        active_symbols = _clean_ticker_symbols(ticker_symbols_by_mode.get(BROKER_MODE), active_defaults)
        # One legacy key used to be shared by both broker modes.  Import it only
        # when no broker-aware map has been saved yet, and do not import a value
        # that exactly matches the other broker's defaults.
        legacy_symbols = _clean_ticker_symbols(custom.get("ticker_board_symbols"), active_defaults)
        other_modes = [mode for mode in TICKER_DEFAULTS_BY_MODE if mode != BROKER_MODE]
        if not isinstance(custom_symbols_by_mode, dict):
            active_symbols = legacy_symbols
            if any(
                _ticker_symbols_match_defaults(legacy_symbols, TICKER_DEFAULTS_BY_MODE[mode])
                for mode in other_modes
            ):
                active_symbols = list(active_defaults)

        ticker_symbols_by_mode[BROKER_MODE] = active_symbols
        merged["ticker_board_symbols_by_mode"] = ticker_symbols_by_mode
        merged["ticker_board_symbols"] = active_symbols

        global_data = custom.get("global")
        if isinstance(global_data, dict) and global_data:
            for key in merged["global"].keys():
                value = global_data.get(key)
                if isinstance(value, str) and value.startswith("#"):
                    merged["global"][key] = value
        else:
            # Backward compatibility for themes saved before universal color codes.
            for section_name, positive_key, negative_key in (
                ("tables", "positive", "negative"),
                ("candles", "up", "down"),
            ):
                section_data = custom.get(section_name, {})
                if not isinstance(section_data, dict):
                    continue
                pos = section_data.get(positive_key)
                neg = section_data.get(negative_key)
                if isinstance(pos, str) and pos.startswith("#"):
                    merged["global"]["positive"] = pos
                if isinstance(neg, str) and neg.startswith("#"):
                    merged["global"]["negative"] = neg
                break

        for section in ("candles", "volume", "tables"):
            section_data = custom.get(section, {})
            if isinstance(section_data, dict):
                for key in merged[section].keys():
                    value = section_data.get(key)
                    if isinstance(value, str) and value.startswith("#"):
                        merged[section][key] = value
        return merged

    def _normalize_linked_sections(self, theme: Dict[str, Any]) -> None:
        # The terminal settings expose one universal positive/negative pair.
        # Always fan that pair out to every directional surface so candles,
        # volume bars, chart metrics, and tables stay in sync and persist as
        # a single user preference.
        theme["candles"]["up"] = theme["global"]["positive"]
        theme["candles"]["down"] = theme["global"]["negative"]
        theme["volume"]["up"] = theme["global"]["positive"]
        theme["volume"]["down"] = theme["global"]["negative"]
        theme["tables"]["positive"] = theme["global"]["positive"]
        theme["tables"]["negative"] = theme["global"]["negative"]


_theme_manager: ColorThemeManager = None


def get_color_theme_manager() -> ColorThemeManager:
    global _theme_manager
    if _theme_manager is None:
        _theme_manager = ColorThemeManager()
    return _theme_manager