import json
import os
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from PySide6.QtCore import QObject, Signal, Slot

logger = logging.getLogger(__name__)


class ChartLinesManager(QObject):
    """
    Manages alert lines and position lines in the chart
    Compatible with existing drawing system: user_data/chart_drawings/SYMBOL_state.json
    """

    # Signals to communicate with chart
    chart_refresh_requested = Signal()

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.drawings_dir = "user_data/chart_drawings"
        os.makedirs(self.drawings_dir, exist_ok=True)

    def _get_trading_mode(self) -> str:
        """Return the active trading mode used to scope chart-managed lines."""
        mode = getattr(self.main_window, "trading_mode", "live")
        mode = str(mode or "live").lower()
        return "paper" if mode == "paper" else "live"

    def _get_symbol_file_path(self, symbol: str, interval: str = None) -> str:
        """Get the path to the symbol's drawings JSON file (shared across intervals)."""
        safe_symbol = symbol.replace("/", "_").replace(":", "_")
        return os.path.join(self.drawings_dir, f"{safe_symbol}_state.json")

    def _load_symbol_drawings(self, symbol: str, interval: str = None) -> Dict:
        """Load existing drawings for a symbol or create new structure"""
        file_path = self._get_symbol_file_path(symbol, interval)

        if os.path.exists(file_path):
            try:
                with open(file_path, 'r') as f:
                    data = json.load(f)

                # Handle both old and new format
                if "drawings" in data:
                    return data  # New format with state
                else:
                    # Old format - just drawings
                    return {
                        "drawings": data,
                        "visible_candle_count": 100
                    }
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Error loading drawings for {symbol}: {e}")

        # Default structure matching existing system
        return {
            "drawings": {
                "lines": [],
                "rectangles": [],
                "notes": [],
                "horizontal_lines": [],
                "horizontal_rays": [],
                "arrow_lines": []
            },
            "visible_candle_count": 100
        }

    def _save_symbol_drawings(self, symbol: str, state: Dict, interval: str = None) -> bool:
        """Save drawings to symbol's JSON file"""
        try:
            file_path = self._get_symbol_file_path(symbol, interval)

            # Ensure the state structure is valid
            if not isinstance(state, dict):
                logger.error(f"Invalid state type for {symbol}: {type(state)}")
                return False

            # Ensure drawings structure exists
            if "drawings" not in state:
                state["drawings"] = {
                    "lines": [],
                    "rectangles": [],
                    "notes": [],
                    "horizontal_lines": [],
                    "horizontal_rays": [],
                    "arrow_lines": []
                }

            drawings = state["drawings"]
            if not isinstance(drawings, dict):
                logger.error(f"Invalid drawings type for {symbol}: {type(drawings)}")
                return False

            # Ensure all required drawing types exist
            for draw_type in ["lines", "rectangles", "notes", "horizontal_lines", "horizontal_rays", "arrow_lines"]:
                if draw_type not in drawings:
                    drawings[draw_type] = []
                elif not isinstance(drawings[draw_type], list):
                    drawings[draw_type] = []

            # Save with proper formatting
            with open(file_path, 'w') as f:
                json.dump(state, f, indent=2)

            logger.info(f"Saved drawings state for {symbol}")
            return True

        except Exception as e:
            logger.error(f"Error saving drawings for {symbol}: {e}")
            return False

    def _save_to_all_intervals(self, symbol: str, apply_fn, current_state: Dict = None) -> bool:
        """Apply a drawing mutation and save it.

        IBKR drawings are stored in one symbol-level state file shared by all
        intervals, but the alert manager uses the Kite-compatible helper name
        when restoring alert lines.  Keeping this wrapper here preserves that
        interface and avoids chart refresh failures during startup/symbol loads.
        """
        state = current_state if current_state is not None else self._load_symbol_drawings(symbol)
        if not isinstance(state, dict):
            state = self._load_symbol_drawings(symbol)
        drawings = state.setdefault("drawings", {})
        for draw_type in ["lines", "rectangles", "notes", "horizontal_lines", "horizontal_rays", "arrow_lines"]:
            value = drawings.get(draw_type)
            if not isinstance(value, list):
                drawings[draw_type] = []
        apply_fn(drawings)
        return self._save_symbol_drawings(symbol, state)

    def _create_horizontal_ray_line(self, price: float, color: str, start_time: float,
                                    text: str, metadata: Optional[Dict[str, Any]] = None) -> Dict:
        """Create a horizontal ray line structure matching existing format"""
        current_time = datetime.now().timestamp() * 1000  # JavaScript timestamp format
        # Set start time to 10 days ago so text appears fully on chart
        ten_days_ago = (datetime.now().timestamp() - (10 * 24 * 60 * 60)) * 1000

        line = {
            "id": current_time + (price * 1000),  # Unique ID
            "type": "horizontal_ray",
            "startTime": ten_days_ago,  # Start 10 days ago
            "startPrice": price,
            "color": color,
            "lineWidth": 1,
            "timestamp": current_time
        }
        if metadata:
            line.update(metadata)
        return line

    def _create_text_note(self, price: float, start_time: float, text: str,
                          color: str = "#FFFFFF") -> Dict:
        """Create a text note structure matching existing format"""
        current_time = datetime.now().timestamp() * 1000  # JavaScript timestamp format
        # Set start time to 10 days ago so text appears fully on chart
        ten_days_ago = (datetime.now().timestamp() - (10 * 24 * 60 * 60)) * 1000

        return {
            "id": current_time + (price * 1000) + 1,  # Unique ID (slightly different from line)
            "type": "note",
            "time": ten_days_ago,  # Start 10 days ago
            "price": price + 0.5,  # Position text slightly above the line
            "text": text,
            "color": color,
            "size": 12,
            "timestamp": current_time
        }

    # Alert Line Management
    def add_alert_line(self, symbol: str, price: float, intent: str = "") -> bool:
        """Add an alert line (line only, no text label)."""
        try:
            state = self._load_symbol_drawings(symbol)
            drawings = state["drawings"]

            # Prevent duplicate alert lines for the same price from accumulating.
            if self._has_existing_alert_drawings(drawings, price):
                logger.debug(f"Alert line already exists for {symbol} at {price:.2f}; skipping redraw")
                return True
            self._remove_existing_alert_drawings(drawings, price)

            # Create horizontal ray line (yellow color) - starts 10 days ago
            line = self._create_horizontal_ray_line(
                price=price,
                color="#FFD700",  # Yellow
                start_time=0,  # Not used anymore, calculated inside function
                text="",
                metadata={
                    "lineCategory": "alert",
                    "intent": intent,
                    "tradingMode": self._get_trading_mode(),
                }
            )

            # Add only the line to drawings (no note text)
            state["drawings"]["horizontal_rays"].append(line)

            success = self._save_symbol_drawings(symbol, state)
            if success:
                self._refresh_chart(symbol)
                logger.info(f"Added alert line for {symbol} at {price:.2f}")

            return success

        except Exception as e:
            logger.error(f"Error adding alert line: {e}")
            return False

    def _has_existing_alert_drawings(self, drawings: Dict, price: float) -> bool:
        """Return True if an alert line already exists for a price."""
        has_ray = any(
            ray.get("type") == "horizontal_ray" and
            abs(ray.get("startPrice", 0) - price) < 0.01 and
            (ray.get("lineCategory") == "alert" or ray.get("color") == "#FFD700")
            for ray in drawings.get("horizontal_rays", [])
        )
        has_legacy_note = any(
            note.get("type") == "note" and
            abs(note.get("price", 0) - price - 0.5) < 0.01 and
            "Alert @" in note.get("text", "") and
            note.get("color") == "#FFD700"
            for note in drawings.get("notes", [])
        )
        return has_ray or has_legacy_note

    def _remove_existing_alert_drawings(self, drawings: Dict, price: float) -> None:
        """Remove existing alert drawings for a price from in-memory drawings."""
        drawings["horizontal_rays"] = [
            ray for ray in drawings["horizontal_rays"]
            if not (
                ray.get("type") == "horizontal_ray" and
                abs(ray.get("startPrice", 0) - price) < 0.01 and
                (ray.get("lineCategory") == "alert" or ray.get("color") == "#FFD700")
            )
        ]

        # Remove legacy note-based alert labels, if present
        drawings["notes"] = [
            note for note in drawings["notes"]
            if not (
                note.get("type") == "note" and
                abs(note.get("price", 0) - price - 0.5) < 0.01 and
                "Alert @" in note.get("text", "") and
                note.get("color") == "#FFD700"
            )
        ]

    def remove_alert_line(self, symbol: str, price: float) -> bool:
        """Remove alert line by price (with tolerance for floating point comparison)"""
        try:
            state = self._load_symbol_drawings(symbol)
            drawings = state["drawings"]

            original_ray_count = len(drawings["horizontal_rays"])
            original_note_count = len(drawings["notes"])
            self._remove_existing_alert_drawings(drawings, price)

            removed_items = (original_ray_count - len(drawings["horizontal_rays"])) + \
                            (original_note_count - len(drawings["notes"]))

            if removed_items > 0:
                success = self._save_symbol_drawings(symbol, state)
                if success:
                    self._refresh_chart(symbol)
                    logger.info(f"Removed {removed_items} alert line items for {symbol} at {price:.2f}")
                return success
            else:
                logger.debug(f"No alert line found for {symbol} at {price:.2f}")
                return True  # Not an error if line doesn't exist

        except Exception as e:
            logger.error(f"Error removing alert line: {e}")
            return False

    # Position Line Management
    def add_position_line(self, symbol: str, order_type: str, quantity: int,
                          avg_price: float, timestamp: float = None) -> bool:
        """Add or update a position line (line only, no text label)."""
        try:
            state = self._load_symbol_drawings(symbol)

            # Get existing position information before removing lines
            existing_position = self._get_existing_position_info(state["drawings"])

            # Remove any existing position lines first
            self._remove_existing_position_lines(state["drawings"])

            # Calculate total position
            total_quantity = quantity  # Start with current order quantity
            final_avg_price = avg_price  # Start with current order price
            final_order_type = order_type

            if existing_position:
                existing_qty = existing_position['quantity']
                existing_price = existing_position['avg_price']
                existing_type = existing_position['order_type']

                # Calculate total quantity and average price
                if order_type.upper() == existing_type.upper():
                    # Same direction - add quantities and calculate weighted average
                    total_quantity = existing_qty + quantity
                    total_cost = (existing_qty * existing_price) + (quantity * avg_price)
                    final_avg_price = total_cost / total_quantity if total_quantity != 0 else avg_price
                    final_order_type = order_type
                else:
                    # Opposite direction - net the quantities
                    if order_type.upper() == "BUY":
                        total_quantity = quantity - existing_qty  # New buy minus existing short
                    else:
                        total_quantity = existing_qty - quantity  # Existing long minus new sell

                    # Determine final order type and price
                    if total_quantity > 0:
                        final_order_type = "BUY"
                        if order_type.upper() == "BUY":
                            final_avg_price = avg_price
                        else:
                            final_order_type = existing_type
                            final_avg_price = existing_price
                    elif total_quantity < 0:
                        final_order_type = "SELL"
                        final_avg_price = avg_price if order_type.upper() == "SELL" else existing_price
                        total_quantity = abs(total_quantity)
                    else:
                        # Position is fully closed, don't add any line
                        success = self._save_symbol_drawings(symbol, state)
                        if success:
                            self._refresh_chart(symbol)
                            logger.info(f"Position fully closed for {symbol}, no line added")
                        return success

            # Don't create line if total quantity is 0
            if total_quantity == 0:
                success = self._save_symbol_drawings(symbol, state)
                if success:
                    self._refresh_chart(symbol)
                    logger.info(f"Position closed for {symbol}, line removed")
                return success

            if final_order_type.upper() == "BUY":
                color = "#00FF00"  # Green
                normalized_order_type = "BUY"
            else:
                color = "#FF0000"  # Red
                normalized_order_type = "SELL"

            line = self._create_horizontal_ray_line(
                price=final_avg_price,
                color=color,
                start_time=0,
                text="",
                metadata={
                    "lineCategory": "position",
                    "quantity": int(total_quantity),
                    "orderType": normalized_order_type,
                    "avgPrice": float(final_avg_price),
                    "tradingMode": self._get_trading_mode(),
                }
            )

            state["drawings"]["horizontal_rays"].append(line)

            success = self._save_symbol_drawings(symbol, state)
            if success:
                self._refresh_chart(symbol)
                logger.info(
                    f"Updated position line for {symbol}: {normalized_order_type} "
                    f"{total_quantity} @ {final_avg_price:.2f}"
                )

            return success

        except Exception as e:
            logger.error(f"Error adding position line: {e}")
            return False

    def remove_position_line(self, symbol: str) -> bool:
        """Remove all position lines for a symbol"""
        try:
            state = self._load_symbol_drawings(symbol)
            drawings = state["drawings"]

            # Count items before removal
            original_ray_count = len(drawings["horizontal_rays"])
            original_note_count = len(drawings["notes"])

            # Remove position lines (green or red colored rays/notes)
            self._remove_existing_position_lines(drawings)

            removed_items = (original_ray_count - len(drawings["horizontal_rays"])) + \
                            (original_note_count - len(drawings["notes"]))

            if removed_items > 0:
                success = self._save_symbol_drawings(symbol, state)
                if success:
                    self._refresh_chart(symbol)
                    logger.info(f"Removed {removed_items} position line items for {symbol}")
                return success
            else:
                logger.debug(f"No position lines found for {symbol}")
                return True

        except Exception as e:
            logger.error(f"Error removing position line: {e}")
            return False

    def _get_existing_position_info(self, drawings: Dict) -> Optional[Dict]:
        """Extract existing position information from current drawings."""
        try:
            # Prefer metadata stored on position rays (new format).
            for ray in drawings.get("horizontal_rays", []):
                if ray.get("type") != "horizontal_ray" or ray.get("lineCategory") != "position":
                    continue

                quantity = ray.get("quantity")
                order_type = ray.get("orderType")
                avg_price = ray.get("avgPrice", ray.get("startPrice"))
                if quantity is None or not order_type:
                    continue

                return {
                    'quantity': int(quantity),
                    'avg_price': float(avg_price),
                    'order_type': str(order_type).upper()
                }

            # Backward-compatible fallback: parse legacy notes.
            for ray in drawings.get("horizontal_rays", []):
                if (ray.get("type") == "horizontal_ray" and
                        ray.get("color") in ["#00FF00", "#FF0000"]):

                    for note in drawings.get("notes", []):
                        if (note.get("type") == "note" and
                                note.get("color") == ray.get("color") and
                                any(keyword in note.get("text", "") for keyword in ["Bought", "Shorted"])):

                            text = note.get("text", "")
                            try:
                                if "Bought" in text:
                                    parts = text.replace("Bought ", "").split(" @")
                                    quantity = int(parts[0])
                                    price = float(parts[1])
                                    return {
                                        'quantity': quantity,
                                        'avg_price': price,
                                        'order_type': 'BUY'
                                    }
                                if "Shorted" in text:
                                    parts = text.replace("Shorted ", "").split(" @")
                                    quantity = int(parts[0])
                                    price = float(parts[1])
                                    return {
                                        'quantity': quantity,
                                        'avg_price': price,
                                        'order_type': 'SELL'
                                    }
                            except (ValueError, IndexError) as e:
                                logger.warning(f"Could not parse position text: {text}, error: {e}")
                                continue

            return None

        except Exception as e:
            logger.error(f"Error getting existing position info: {e}")
            return None

    def _remove_existing_position_lines(self, drawings: Dict):
        """Helper to remove existing position lines from drawings."""
        drawings["horizontal_rays"] = [
            ray for ray in drawings["horizontal_rays"]
            if not (
                ray.get("type") == "horizontal_ray" and
                (ray.get("lineCategory") == "position" or ray.get("color") in ["#00FF00", "#FF0000"])
            )
        ]

        # Remove legacy position notes if any old state files still contain them.
        drawings["notes"] = [
            note for note in drawings["notes"]
            if not (note.get("type") == "note" and
                    any(keyword in note.get("text", "") for keyword in ["Bought", "Shorted"]) and
                    note.get("color") in ["#00FF00", "#FF0000"])
        ]

    def _refresh_chart(self, symbol: Optional[str] = None):
        """Signal the chart to refresh and reload drawings for a symbol."""
        try:
            self.chart_refresh_requested.emit()

            # Directly refresh chart if available and symbol is currently visible.
            if hasattr(self.main_window, 'candlestick_chart'):
                chart = self.main_window.candlestick_chart
                target_symbol = symbol or getattr(chart, 'current_symbol', None)

                if target_symbol and getattr(chart, 'current_symbol', None) != target_symbol:
                    logger.debug(
                        "Skipping live chart refresh for %s because active chart symbol is %s",
                        target_symbol,
                        getattr(chart, 'current_symbol', None),
                    )
                    return

                # Try different methods to refresh the chart
                if hasattr(chart, 'load_symbol_drawings'):
                    chart.load_symbol_drawings(target_symbol, 'day')
                elif hasattr(chart, '_load_chart_data'):
                    chart._load_chart_data(force_refresh=True)
                elif hasattr(chart, 'reload_drawings'):
                    chart.reload_drawings()

                logger.debug("Chart refresh requested for %s", target_symbol)

        except Exception as e:
            logger.error(f"Error refreshing chart: {e}")

    def load_symbol_with_fresh_drawings(self, symbol: str):
        """Load symbol and refresh its drawings"""
        try:
            file_path = self._get_symbol_file_path(symbol)

            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    state = json.load(f)

                # Update chart with fresh drawings
                if hasattr(self.main_window, 'candlestick_chart'):
                    chart = self.main_window.candlestick_chart

                    # Try to update the chart's drawings
                    if hasattr(chart, 'run_js_function'):
                        chart.run_js_function("updateDrawings", [state.get("drawings", {})])
                    elif hasattr(chart, 'chart_view'):
                        js_code = f"if (window.chart && window.chart.updateDrawings) window.chart.updateDrawings({json.dumps(state.get('drawings', {}))});"
                        chart.chart_view.page().runJavaScript(js_code)

                logger.info(f"Loaded fresh drawings for {symbol}")
        except Exception as e:
            logger.error(f"Error loading fresh drawings for {symbol}: {e}")

    # Public interface methods for other components
    @Slot(str, float, str)
    def on_alert_created(self, symbol: str, price: float, intent: str = ""):
        """Slot to handle alert creation"""
        self.add_alert_line(symbol, price, intent)

    @Slot(str, float)
    def on_alert_triggered_or_deleted(self, symbol: str, price: float):
        """Slot to handle alert trigger or deletion"""
        self.remove_alert_line(symbol, price)

    @Slot(str, str, int, float)
    def on_position_created(self, symbol: str, order_type: str, quantity: int, avg_price: float):
        """Slot to handle position creation"""
        self.add_position_line(symbol, order_type, quantity, avg_price)

    @Slot(str)
    def on_position_closed(self, symbol: str):
        """Slot to handle position closure"""
        self.remove_position_line(symbol)

    def sync_lines_with_chart_symbol(self, symbol: str):
        """Sync alert and position lines when chart symbol changes"""
        try:
            # This method can be called when the chart loads a new symbol
            # to ensure all lines are properly displayed
            self.load_symbol_with_fresh_drawings(symbol)
            logger.info(f"Synced lines for chart symbol: {symbol}")
        except Exception as e:
            logger.error(f"Error syncing lines for symbol {symbol}: {e}")

    def cleanup_all_triggered_alerts(self):
        """Remove all alert lines from all symbol files (cleanup utility)"""
        try:
            for filename in os.listdir(self.drawings_dir):
                if filename.endswith("_state.json"):
                    symbol = filename.replace("_state.json", "").replace("_", "/")

                    try:
                        state = self._load_symbol_drawings(symbol)
                        drawings = state["drawings"]

                        # Remove all alert lines (yellow)
                        original_count = len(drawings["horizontal_rays"]) + len(drawings["notes"])

                        drawings["horizontal_rays"] = [
                            ray for ray in drawings["horizontal_rays"]
                            if ray.get("color") != "#FFD700"
                        ]

                        drawings["notes"] = [
                            note for note in drawings["notes"]
                            if not ("Alert @" in note.get("text", "") and note.get("color") == "#FFD700")
                        ]

                        new_count = len(drawings["horizontal_rays"]) + len(drawings["notes"])
                        removed = original_count - new_count

                        if removed > 0:
                            self._save_symbol_drawings(symbol, state)
                            logger.info(f"Cleaned up {removed} alert lines for {symbol}")

                    except Exception as e:
                        logger.error(f"Error cleaning up alerts for {symbol}: {e}")

        except Exception as e:
            logger.error(f"Error during alert cleanup: {e}")
