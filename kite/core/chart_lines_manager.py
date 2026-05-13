# kite/core/chart_lines_manager.py
"""
ChartLinesManager — FIXED VERSION

Bugs fixed:
  1. Lines now written to ALL existing interval files for a symbol, not just
     the currently visible one.  This ensures alerts/positions appear regardless
     of which timeframe the trader opens.
  2. _refresh_chart() now waits for chart LOADED state before injecting drawings.
  3. Added _has_existing_position_drawings() to properly dedup position lines.
  4. Startup restoration uses a QTimer that retries until the chart is ready.
  5. add_alert_line / add_position_line both write across all intervals.
"""

import json
import os
import logging
import time
from datetime import datetime
from typing import Dict, Any, Optional, List
from PySide6.QtCore import QObject, Signal, Slot, QTimer

logger = logging.getLogger(__name__)


_recent_draws: dict[str, float] = {}
_DRAW_COOLDOWN_SECONDS = 1.0


def _is_recently_drawn(key: str) -> bool:
    ts = _recent_draws.get(key)
    if ts is None:
        return False
    if (time.time() - ts) > _DRAW_COOLDOWN_SECONDS:
        _recent_draws.pop(key, None)
        return False
    return True


def _mark_drawn(key: str) -> None:
    _recent_draws[key] = time.time()


class ChartLinesManager(QObject):
    """
    Manages alert lines and position lines in the chart.
    Compatible with existing drawing system:
        kite/user_data/chart_drawings/SYMBOL_<interval>_state.json
    """

    chart_refresh_requested = Signal()

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.drawings_dir = "kite/user_data/chart_drawings"
        os.makedirs(self.drawings_dir, exist_ok=True)
        _recent_draws.clear()

    def _get_trading_mode(self) -> str:
        """Return active trading mode ('live' or 'paper')."""
        mode = getattr(self.main_window, "trading_mode", "live")
        mode = str(mode).lower()
        return "paper" if mode == "paper" else "live"

    # ─────────────────────────────────────────────────────────────────────────
    # FILE PATH HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _get_current_interval(self) -> str:
        """Return the interval currently shown in the chart widget."""
        if hasattr(self.main_window, 'candlestick_chart'):
            chart = self.main_window.candlestick_chart
            if hasattr(chart, 'current_interval') and chart.current_interval:
                return chart.current_interval
        return "day"

    def _get_symbol_file_path(self, symbol: str, interval: str = None) -> str:
        """Return the state file path for a symbol/interval pair."""
        if interval is None:
            interval = self._get_current_interval()
        safe_symbol = symbol.replace("/", "_").replace(":", "_")
        return os.path.join(self.drawings_dir, f"{safe_symbol}_{interval}_state.json")

    def _get_all_interval_file_paths(self, symbol: str) -> List[str]:
        """
        Return paths for ALL timeframe state files that already exist for a symbol.
        If none exist yet, return the current-interval path so we create at least one.
        """
        safe_symbol = symbol.replace("/", "_").replace(":", "_")
        existing = [
            os.path.join(self.drawings_dir, fname)
            for fname in os.listdir(self.drawings_dir)
            if fname.startswith(safe_symbol + "_") and fname.endswith("_state.json")
        ]
        if not existing:
            # No files yet — use current interval so we create one
            existing = [self._get_symbol_file_path(symbol)]
        return existing

    # ─────────────────────────────────────────────────────────────────────────
    # STATE I/O
    # ─────────────────────────────────────────────────────────────────────────

    def _load_symbol_drawings(self, symbol: str, interval: str = None) -> Dict:
        """Load existing drawings for a symbol/interval or return default structure."""
        file_path = self._get_symbol_file_path(symbol, interval)

        if os.path.exists(file_path):
            try:
                with open(file_path, 'r') as f:
                    data = json.load(f)
                if "drawings" in data:
                    return data
                else:
                    return {"drawings": data, "visible_candle_count": 100}
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Error loading drawings for {symbol}: {e}")

        return {
            "drawings": {
                "lines": [],
                "rectangles": [],
                "notes": [],
                "horizontal_lines": [],
                "horizontal_rays": [],
                "arrow_lines": [],
                "fibonacci": [],
            },
            "visible_candle_count": 100,
        }

    def _save_symbol_drawings(self, symbol: str, state: Dict, interval: str = None) -> bool:
        """Save drawings to a single symbol/interval file."""
        try:
            file_path = self._get_symbol_file_path(symbol, interval)
            if not isinstance(state, dict):
                return False

            if "drawings" not in state:
                state["drawings"] = {
                    "lines": [], "rectangles": [], "notes": [],
                    "horizontal_lines": [], "horizontal_rays": [],
                    "arrow_lines": [], "fibonacci": [],
                }

            drawings = state["drawings"]
            if not isinstance(drawings, dict):
                return False

            for draw_type in ["lines", "rectangles", "notes", "horizontal_lines",
                               "horizontal_rays", "arrow_lines", "fibonacci"]:
                if draw_type not in drawings:
                    drawings[draw_type] = []
                elif not isinstance(drawings[draw_type], list):
                    drawings[draw_type] = []

            with open(file_path, 'w') as f:
                json.dump(state, f, indent=2)

            return True
        except Exception as e:
            logger.error(f"Error saving drawings for {symbol}: {e}")
            return False

    def _save_to_all_intervals(self, symbol: str,
                                apply_fn,
                                current_state: Dict = None) -> bool:
        """
        Apply apply_fn(drawings_dict) to every existing interval file for symbol.

        apply_fn receives the 'drawings' dict and modifies it in-place.
        Returns True if at least one file was written successfully.
        """
        paths = self._get_all_interval_file_paths(symbol)
        any_success = False

        for path in paths:
            interval = self._extract_interval_from_path(path, symbol)

            # Load existing state for this interval
            if os.path.exists(path):
                try:
                    with open(path, 'r') as f:
                        state = json.load(f)
                    if "drawings" not in state:
                        state = {"drawings": state, "visible_candle_count": 100}
                except Exception:
                    state = self._empty_state()
            else:
                # Use the already-modified current state for the current interval
                state = current_state if current_state is not None else self._empty_state()

            # Ensure drawings structure is intact
            drawings = state.setdefault("drawings", {})
            for key in ["lines", "rectangles", "notes", "horizontal_lines",
                        "horizontal_rays", "arrow_lines", "fibonacci"]:
                drawings.setdefault(key, [])

            apply_fn(drawings)

            if self._save_symbol_drawings(symbol, state, interval):
                any_success = True

        return any_success

    @staticmethod
    def _extract_interval_from_path(path: str, symbol: str) -> str:
        """Extract interval string from a state file path."""
        fname = os.path.basename(path)
        safe_symbol = symbol.replace("/", "_").replace(":", "_")
        prefix = safe_symbol + "_"
        suffix = "_state.json"
        if fname.startswith(prefix) and fname.endswith(suffix):
            return fname[len(prefix): -len(suffix)]
        return "day"

    @staticmethod
    def _empty_state() -> Dict:
        return {
            "drawings": {
                "lines": [], "rectangles": [], "notes": [],
                "horizontal_lines": [], "horizontal_rays": [],
                "arrow_lines": [], "fibonacci": [],
            },
            "visible_candle_count": 100,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # LINE FACTORIES
    # ─────────────────────────────────────────────────────────────────────────

    def _create_horizontal_ray_line(self, price: float, color: str,
                                    start_time: float, text: str,
                                    metadata: Optional[Dict[str, Any]] = None) -> Dict:
        """Create a horizontal ray line structure matching the DrawingEngine format."""
        current_time = datetime.now().timestamp() * 1000
        ten_days_ago = (datetime.now().timestamp() - (10 * 24 * 60 * 60)) * 1000

        line = {
            "id": current_time + (price * 1000),
            "type": "horizontal_ray",
            "startTime": ten_days_ago,
            "startPrice": price,
            "color": color,
            "lineWidth": 1,
            "timestamp": current_time,
        }
        if metadata:
            line.update(metadata)
        return line

    # ─────────────────────────────────────────────────────────────────────────
    # ALERT LINE MANAGEMENT
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _session_line_key(symbol: str, price: float) -> str:
        return f"{symbol}_{price:.2f}"

    # ─────────────────────────────────────────────────────────────────────────

    def add_alert_line(self, symbol: str, price: float, intent: str = "", interval: str = None) -> bool:
        """Add an alert line for a symbol/timeframe file."""
        try:
            line_key = self._session_line_key(symbol, price)
            if _is_recently_drawn(line_key):
                logger.debug(f"Write coalescing: alert line recently processed for {symbol} at {price:.2f}")
                return True

            # Load current-interval state to check for duplicates
            current_state = self._load_symbol_drawings(symbol)
            drawings = current_state["drawings"]

            if self._has_existing_alert_drawings(drawings, price):
                _mark_drawn(line_key)
                logger.debug(f"Alert line already exists for {symbol} at {price:.2f}")
                return True

            new_line = self._create_horizontal_ray_line(
                price=price, color="#FFD700", start_time=0, text="",
                metadata={
                    "lineCategory": "alert",
                    "intent": intent,
                    "tradingMode": self._get_trading_mode(),
                },
            )

            def _apply(d):
                self._remove_existing_alert_drawings(d, price)
                d["horizontal_rays"].append(new_line)

            success = self._save_to_all_intervals(symbol, _apply,
                                                  current_state=current_state)
            if success:
                _mark_drawn(line_key)
                self._refresh_chart()
                logger.info(f"Alert line drawn: {symbol} @ {price:.2f} (all intervals)")
            return success

        except Exception as e:
            logger.error(f"Error adding alert line: {e}")
            return False

    def _has_existing_alert_drawings(self, drawings: Dict, price: float) -> bool:
        mode = self._get_trading_mode()
        return any(
            ray.get("type") == "horizontal_ray" and
            abs(ray.get("startPrice", 0) - price) < 0.01 and
            (ray.get("lineCategory") == "alert" or ray.get("color") == "#FFD700") and
            str(ray.get("tradingMode", "live")).lower() == mode
            for ray in drawings.get("horizontal_rays", [])
        )

    def _remove_existing_alert_drawings(self, drawings: Dict, price: float) -> None:
        mode = self._get_trading_mode()
        drawings["horizontal_rays"] = [
            ray for ray in drawings.get("horizontal_rays", [])
            if not (
                ray.get("type") == "horizontal_ray" and
                abs(ray.get("startPrice", 0) - price) < 0.01 and
                (ray.get("lineCategory") == "alert" or ray.get("color") == "#FFD700") and
                str(ray.get("tradingMode", "live")).lower() == mode
            )
        ]
        # Remove legacy note-based alert labels
        drawings["notes"] = [
            note for note in drawings.get("notes", [])
            if not (
                note.get("type") == "note" and
                abs(note.get("price", 0) - price - 0.5) < 0.01 and
                "Alert @" in note.get("text", "") and
                note.get("color") == "#FFD700"
            )
        ]

    def remove_alert_line(self, symbol: str, price: float, interval: str = None) -> bool:
        """Remove alert line for a symbol/timeframe file."""
        try:
            state = self._load_symbol_drawings(symbol, interval)
            self._remove_existing_alert_drawings(state["drawings"], price)
            success = self._save_symbol_drawings(symbol, state, interval)
            if success:
                line_key = self._session_line_key(symbol, price)
                _recent_draws.pop(line_key, None)
                self._refresh_chart()
                logger.info(f"Removed alert line for {symbol} at {price:.2f}")
            return success
        except Exception as e:
            logger.error(f"Error removing alert line: {e}")
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # POSITION LINE MANAGEMENT
    # ─────────────────────────────────────────────────────────────────────────

    def add_position_line(self, symbol: str, order_type: str, quantity: int,
                          avg_price: float, timestamp: float = None) -> bool:
        """Add or update a position line across ALL interval files."""
        try:
            # Read current state to get existing position info
            current_state = self._load_symbol_drawings(symbol)
            existing_position = self._get_existing_position_info(current_state["drawings"])

            total_quantity = quantity
            final_avg_price = avg_price
            final_order_type = order_type

            if existing_position:
                existing_qty = existing_position['quantity']
                existing_price = existing_position['avg_price']
                existing_type = existing_position['order_type']

                if order_type.upper() == existing_type.upper():
                    total_quantity = existing_qty + quantity
                    total_cost = (existing_qty * existing_price) + (quantity * avg_price)
                    final_avg_price = total_cost / total_quantity if total_quantity != 0 else avg_price
                    final_order_type = order_type
                else:
                    if order_type.upper() == "BUY":
                        total_quantity = quantity - existing_qty
                    else:
                        total_quantity = existing_qty - quantity

                    if total_quantity > 0:
                        final_order_type = "BUY"
                        final_avg_price = avg_price if order_type.upper() == "BUY" else existing_price
                    elif total_quantity < 0:
                        final_order_type = "SELL"
                        final_avg_price = avg_price if order_type.upper() == "SELL" else existing_price
                        total_quantity = abs(total_quantity)
                    else:
                        # Position fully closed
                        return self.remove_position_line(symbol)

            if total_quantity == 0:
                return self.remove_position_line(symbol)

            color = "#00FF00" if final_order_type.upper() == "BUY" else "#FF0000"
            normalized_order_type = "BUY" if final_order_type.upper() == "BUY" else "SELL"

            new_line = self._create_horizontal_ray_line(
                price=final_avg_price, color=color, start_time=0, text="",
                metadata={
                    "lineCategory": "position",
                    "quantity": int(total_quantity),
                    "orderType": normalized_order_type,
                    "avgPrice": float(final_avg_price),
                    "tradingMode": self._get_trading_mode(),
                },
            )

            def _apply(d):
                self._remove_existing_position_lines(d)
                d["horizontal_rays"].append(new_line)

            success = self._save_to_all_intervals(symbol, _apply, current_state=current_state)
            if success:
                self._refresh_chart()
                logger.info(
                    f"Updated position line for {symbol}: "
                    f"{normalized_order_type} {total_quantity} @ {final_avg_price:.2f}"
                )
            return success

        except Exception as e:
            logger.error(f"Error adding position line: {e}")
            return False

    def remove_position_line(self, symbol: str) -> bool:
        """Remove all position lines across ALL interval files for the symbol."""
        try:
            def _apply(d):
                self._remove_existing_position_lines(d)

            success = self._save_to_all_intervals(symbol, _apply)
            if success:
                self._refresh_chart()
                logger.info(f"Removed position line for {symbol}")
            return success
        except Exception as e:
            logger.error(f"Error removing position line: {e}")
            return False

    def add_stop_loss_line(self, symbol: str, sl_price: float) -> bool:
        """Add or update a stop-loss line across all interval files for a symbol."""
        try:
            mode = self._get_trading_mode()
            new_line = self._create_horizontal_ray_line(
                price=float(sl_price),
                color="#FF4D4F",
                start_time=0,
                text="",
                metadata={
                    "lineCategory": "stop_loss",
                    "slPrice": float(sl_price),
                    "tradingMode": mode,
                },
            )

            def _apply(d):
                d["horizontal_rays"] = [
                    ray for ray in d.get("horizontal_rays", [])
                    if not (
                        ray.get("type") == "horizontal_ray" and
                        (ray.get("lineCategory") == "stop_loss" or ray.get("color") == "#FF4D4F") and
                        str(ray.get("tradingMode", "live")).lower() == mode
                    )
                ]
                d["horizontal_rays"].append(new_line)

            success = self._save_to_all_intervals(symbol, _apply)
            if success:
                self._refresh_chart()
                logger.info("Updated stop-loss line for %s @ %.2f", symbol, sl_price)
            return success
        except Exception as e:
            logger.error(f"Error adding stop-loss line: {e}")
            return False

    def remove_stop_loss_line(self, symbol: str) -> bool:
        """Remove stop-loss line(s) across all interval files for a symbol."""
        try:
            mode = self._get_trading_mode()

            def _apply(d):
                d["horizontal_rays"] = [
                    ray for ray in d.get("horizontal_rays", [])
                    if not (
                        ray.get("type") == "horizontal_ray" and
                        (ray.get("lineCategory") == "stop_loss" or ray.get("color") == "#FF4D4F") and
                        str(ray.get("tradingMode", "live")).lower() == mode
                    )
                ]

            success = self._save_to_all_intervals(symbol, _apply)
            if success:
                self._refresh_chart()
                logger.info("Removed stop-loss line for %s", symbol)
            return success
        except Exception as e:
            logger.error(f"Error removing stop-loss line: {e}")
            return False

    def sync_position_lines(self, positions: List[Any]) -> None:
        """
        Ensure chart position lines exactly match the latest positions table.
        Adds/updates lines for active positions and removes stale lines for symbols
        no longer present.
        """
        try:
            active_symbols = set()

            for pos in positions or []:
                symbol = getattr(pos, "symbol", "")
                quantity = int(getattr(pos, "quantity", 0) or 0)
                avg_price = float(getattr(pos, "avg_price", 0) or 0)
                if not symbol or quantity == 0 or avg_price <= 0:
                    continue

                active_symbols.add(symbol)
                order_type = "BUY" if quantity > 0 else "SELL"
                self.add_position_line(
                    symbol=symbol,
                    order_type=order_type,
                    quantity=abs(quantity),
                    avg_price=avg_price,
                )

            for fname in os.listdir(self.drawings_dir):
                if not fname.endswith("_state.json"):
                    continue
                if "_day_state.json" not in fname:
                    continue

                symbol = fname.replace("_day_state.json", "").replace("_", "/")
                if symbol in active_symbols:
                    continue

                state = self._load_symbol_drawings(symbol, "day")
                if self._has_existing_position_drawings(state.get("drawings", {})):
                    self.remove_position_line(symbol)

        except Exception as e:
            logger.error(f"Error syncing position lines with positions table: {e}")

    def _get_existing_position_info(self, drawings: Dict) -> Optional[Dict]:
        try:
            mode = self._get_trading_mode()
            for ray in drawings.get("horizontal_rays", []):
                if ray.get("type") != "horizontal_ray" or ray.get("lineCategory") != "position":
                    continue
                if str(ray.get("tradingMode", "live")).lower() != mode:
                    continue
                quantity = ray.get("quantity")
                order_type = ray.get("orderType")
                avg_price = ray.get("avgPrice", ray.get("startPrice"))
                if quantity is None or not order_type:
                    continue
                return {
                    'quantity': int(quantity),
                    'avg_price': float(avg_price),
                    'order_type': str(order_type).upper(),
                }

            # Fallback: legacy color-based detection
            for ray in drawings.get("horizontal_rays", []):
                if (ray.get("type") == "horizontal_ray" and
                        ray.get("color") in ["#00FF00", "#FF0000"] and
                        str(ray.get("tradingMode", "live")).lower() == mode):
                    color = ray.get("color")
                    order_type = "BUY" if color == "#00FF00" else "SELL"
                    price = ray.get("startPrice", 0)
                    if price > 0:
                        return {'quantity': 1, 'avg_price': float(price), 'order_type': order_type}

            return None
        except Exception as e:
            logger.error(f"Error getting existing position info: {e}")
            return None

    def _has_existing_position_drawings(self, drawings: Dict) -> bool:
        """Return True when the current trading mode has any position drawings."""
        mode = self._get_trading_mode()
        for ray in drawings.get("horizontal_rays", []):
            if ray.get("type") != "horizontal_ray":
                continue
            is_position = (
                ray.get("lineCategory") == "position"
                or ray.get("color") in ["#00FF00", "#FF0000"]
            )
            if is_position and str(ray.get("tradingMode", "live")).lower() == mode:
                return True
        return False

    def _remove_existing_position_lines(self, drawings: Dict) -> None:
        mode = self._get_trading_mode()
        drawings["horizontal_rays"] = [
            ray for ray in drawings.get("horizontal_rays", [])
            if not (
                ray.get("type") == "horizontal_ray" and
                (ray.get("lineCategory") == "position" or
                 ray.get("color") in ["#00FF00", "#FF0000"]) and
                str(ray.get("tradingMode", "live")).lower() == mode
            )
        ]
        drawings["notes"] = [
            note for note in drawings.get("notes", [])
            if not (
                note.get("type") == "note" and
                any(kw in note.get("text", "") for kw in ["Bought", "Shorted"]) and
                note.get("color") in ["#00FF00", "#FF0000"]
            )
        ]


    def _filter_drawings_for_mode(self, drawings: Dict[str, Any]) -> Dict[str, Any]:
        """Return a copy of drawings that only includes active-mode alert/position lines."""
        mode = self._get_trading_mode()
        out = dict(drawings)
        rays = list(drawings.get("horizontal_rays", []))

        def _keep(ray: Dict[str, Any]) -> bool:
            category = ray.get("lineCategory")
            is_alert = category == "alert" or ray.get("color") == "#FFD700"
            is_position = category == "position" or ray.get("color") in ["#00FF00", "#FF0000"]
            is_stop_loss = category == "stop_loss" or ray.get("color") == "#FF4D4F"
            if not (is_alert or is_position or is_stop_loss):
                return True
            return str(ray.get("tradingMode", "live")).lower() == mode

        out["horizontal_rays"] = [r for r in rays if _keep(r)]
        return out

    # ─────────────────────────────────────────────────────────────────────────
    # CHART REFRESH  (waits for chart LOADED state before injecting)
    # ─────────────────────────────────────────────────────────────────────────

    def _refresh_chart(self, retry_count: int = 0) -> None:
        """
        Push updated drawings into the live chart.
        Retries up to 10 times (1 second total) if the chart is not ready yet.
        """
        try:
            if not hasattr(self.main_window, 'candlestick_chart'):
                return

            chart = self.main_window.candlestick_chart
            symbol = getattr(chart, 'current_symbol', None)
            if not symbol:
                return

            # Check that the chart is in LOADED state
            from chart_engine.core.chart_widget import ChartState
            if getattr(chart, 'current_state', None) != ChartState.LOADED:
                if retry_count < 10:
                    QTimer.singleShot(
                        100,
                        lambda: self._refresh_chart(retry_count + 1)
                    )
                    logger.debug(f"Chart not ready, retry {retry_count + 1}/10")
                return

            state = self._load_symbol_drawings(symbol)
            drawings = state.get("drawings", {})
            filtered_drawings = self._filter_drawings_for_mode(drawings)

            if hasattr(chart, 'set_drawings'):
                chart.set_drawings(filtered_drawings)
                logger.debug(f"Chart drawings refreshed for {symbol}")
            elif hasattr(chart, 'chart_view') and chart.chart_view:
                js_code = (
                    "if(window.chart && window.chart.updateDrawings)"
                    f"window.chart.updateDrawings({json.dumps(filtered_drawings)});"
                )
                chart.chart_view.page().runJavaScript(js_code)
                logger.debug(f"Chart drawings injected via JS for {symbol}")

        except Exception as e:
            logger.error(f"Error refreshing chart: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # CHART SYMBOL SYNC
    # ─────────────────────────────────────────────────────────────────────────

    def load_symbol_with_fresh_drawings(self, symbol: str) -> None:
        """Called by main_window when chart switches symbol."""
        self._refresh_chart()

    def sync_lines_with_chart_symbol(self, symbol: str) -> None:
        """Sync alert and position lines when chart symbol changes."""
        try:
            self.load_symbol_with_fresh_drawings(symbol)
            logger.info(f"Synced lines for chart symbol: {symbol}")
        except Exception as e:
            logger.error(f"Error syncing lines for symbol {symbol}: {e}")

    def cleanup_all_triggered_alerts(self) -> None:
        """Remove all alert lines from all symbol state files."""
        try:
            for filename in os.listdir(self.drawings_dir):
                if not filename.endswith("_state.json"):
                    continue
                filepath = os.path.join(self.drawings_dir, filename)
                try:
                    with open(filepath, 'r') as f:
                        state = json.load(f)
                    if "drawings" not in state:
                        state = {"drawings": state}
                    drawings = state["drawings"]

                    original_count = (
                        len(drawings.get("horizontal_rays", [])) +
                        len(drawings.get("notes", []))
                    )

                    drawings["horizontal_rays"] = [
                        ray for ray in drawings.get("horizontal_rays", [])
                        if ray.get("color") != "#FFD700"
                    ]
                    drawings["notes"] = [
                        note for note in drawings.get("notes", [])
                        if not ("Alert @" in note.get("text", "") and note.get("color") == "#FFD700")
                    ]

                    new_count = (
                        len(drawings.get("horizontal_rays", [])) +
                        len(drawings.get("notes", []))
                    )
                    if new_count < original_count:
                        with open(filepath, 'w') as f:
                            json.dump(state, f, indent=2)
                        logger.info(f"Cleaned {original_count - new_count} alerts from {filename}")
                except Exception as e:
                    logger.error(f"Error cleaning alerts from {filename}: {e}")
        except Exception as e:
            logger.error(f"Error during alert cleanup: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # SLOTS (for signal connections)
    # ─────────────────────────────────────────────────────────────────────────

    @Slot(str, float, str)
    def on_alert_created(self, symbol: str, price: float, intent: str = "") -> None:
        self.add_alert_line(symbol, price, intent)

    @Slot(str, float)
    def on_alert_triggered_or_deleted(self, symbol: str, price: float) -> None:
        self.remove_alert_line(symbol, price)

    @Slot(str, str, int, float)
    def on_position_created(self, symbol: str, order_type: str, quantity: int,
                             avg_price: float) -> None:
        self.add_position_line(symbol, order_type, quantity, avg_price)

    @Slot(str)
    def on_position_closed(self, symbol: str) -> None:
        self.remove_position_line(symbol)
