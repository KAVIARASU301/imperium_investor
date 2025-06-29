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
    Handles JSON file operations for the 'day' timeframe only
    """

    # Signals to communicate with chart
    chart_refresh_requested = Signal()

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.drawings_dir = "user_data/drawings"
        os.makedirs(self.drawings_dir, exist_ok=True)

    def _get_symbol_file_path(self, symbol: str) -> str:
        """Get the path to the symbol's drawings JSON file for day timeframe"""
        safe_symbol = symbol.replace("/", "_").replace(":", "_")
        return os.path.join(self.drawings_dir, f"{safe_symbol}_day.json")

    def _load_symbol_drawings(self, symbol: str) -> Dict:
        """Load existing drawings for a symbol or create new structure"""
        file_path = self._get_symbol_file_path(symbol)

        if os.path.exists(file_path):
            try:
                with open(file_path, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Error loading drawings for {symbol}: {e}")

        # Default structure
        return {
            "lines": [],
            "rectangles": [],
            "notes": [],
            "horizontal_lines": [],
            "horizontal_rays": [],
            "arrow_lines": []
        }

    def _save_symbol_drawings(self, symbol: str, drawings: Dict) -> bool:
        """Save drawings to symbol's JSON file"""
        try:
            file_path = self._get_symbol_file_path(symbol)
            with open(file_path, 'w') as f:
                json.dump(drawings, f, indent=2)
            return True
        except Exception as e:
            logger.error(f"Error saving drawings for {symbol}: {e}")
            return False

    def _create_horizontal_ray_line(self, price: float, color: str, start_time: str,
                                    text: str, text_color: str = None) -> Dict:
        """Create a horizontal ray line structure"""
        return {
            "id": f"line_{datetime.now().timestamp()}_{price}",
            "type": "horizontal_ray",
            "startTime": start_time,
            "startPrice": price,
            "color": color,
            "width": 1,
            "timestamp": datetime.now().isoformat()
        }

    def _create_text_note(self, price: float, start_time: str, text: str,
                          color: str = "#FFFFFF") -> Dict:
        """Create a text note structure"""
        return {
            "id": f"note_{datetime.now().timestamp()}_{price}",
            "type": "note",
            "time": start_time,
            "price": price + 0.5,  # Position text slightly above the line
            "text": text,
            "color": color,
            "size": 12,
            "timestamp": datetime.now().isoformat()
        }

    # Alert Line Management
    def add_alert_line(self, symbol: str, price: float, intent: str = "") -> bool:
        """Add an alert line with text"""
        try:
            drawings = self._load_symbol_drawings(symbol)
            current_time = datetime.now().isoformat()

            # Create alert text
            alert_text = f"Alert @ {price:.2f}"
            if intent and intent != "auto":
                alert_text += f" ({intent})"

            # Create horizontal ray line (yellow color)
            line = self._create_horizontal_ray_line(
                price=price,
                color="#FFD700",  # Yellow
                start_time=current_time,
                text=alert_text
            )

            # Create text note above the line
            note = self._create_text_note(
                price=price,
                start_time=current_time,
                text=alert_text,
                color="#FFD700"
            )

            # Add to drawings
            drawings["horizontal_rays"].append(line)
            drawings["notes"].append(note)

            # Save and refresh
            success = self._save_symbol_drawings(symbol, drawings)
            if success:
                self._refresh_chart()
                logger.info(f"Added alert line for {symbol} at {price:.2f}")

            return success

        except Exception as e:
            logger.error(f"Error adding alert line: {e}")
            return False

    def remove_alert_line(self, symbol: str, price: float) -> bool:
        """Remove alert line by price"""
        try:
            drawings = self._load_symbol_drawings(symbol)

            # Remove horizontal rays matching the price
            original_ray_count = len(drawings["horizontal_rays"])
            drawings["horizontal_rays"] = [
                ray for ray in drawings["horizontal_rays"]
                if not (ray.get("type") == "horizontal_ray" and
                        abs(ray.get("startPrice", 0) - price) < 0.01 and
                        ray.get("color") == "#FFD700")
            ]

            # Remove corresponding notes
            original_note_count = len(drawings["notes"])
            drawings["notes"] = [
                note for note in drawings["notes"]
                if not (note.get("type") == "note" and
                        abs(note.get("price", 0) - price - 0.5) < 0.01 and
                        "Alert @" in note.get("text", ""))
            ]

            removed_items = (original_ray_count - len(drawings["horizontal_rays"])) + \
                            (original_note_count - len(drawings["notes"]))

            if removed_items > 0:
                success = self._save_symbol_drawings(symbol, drawings)
                if success:
                    self._refresh_chart()
                    logger.info(f"Removed alert line for {symbol} at {price:.2f}")
                return success
            else:
                logger.info(f"No alert line found for {symbol} at {price:.2f}")
                return True  # Not an error if line doesn't exist

        except Exception as e:
            logger.error(f"Error removing alert line: {e}")
            return False

    # Position Line Management
    def add_position_line(self, symbol: str, order_type: str, quantity: int,
                          avg_price: float, timestamp: str = None) -> bool:
        """Add a position line with text"""
        try:
            drawings = self._load_symbol_drawings(symbol)

            # Remove any existing position lines first
            self._remove_existing_position_lines(drawings)

            current_time = timestamp or datetime.now().isoformat()

            # Determine colors and text based on order type
            if order_type.upper() == "BUY":
                color = "#00FF00"  # Green
                text = f"Bought {quantity} @{avg_price:.2f}"
            else:  # SELL/SHORT
                color = "#FF0000"  # Red
                text = f"Shorted {quantity} @{avg_price:.2f}"

            # Create horizontal ray line
            line = self._create_horizontal_ray_line(
                price=avg_price,
                color=color,
                start_time=current_time,
                text=text
            )

            # Create text note above the line
            note = self._create_text_note(
                price=avg_price,
                start_time=current_time,
                text=text,
                color=color
            )

            # Add to drawings
            drawings["horizontal_rays"].append(line)
            drawings["notes"].append(note)

            # Save and refresh
            success = self._save_symbol_drawings(symbol, drawings)
            if success:
                self._refresh_chart()
                logger.info(f"Added position line for {symbol}: {text}")

            return success

        except Exception as e:
            logger.error(f"Error adding position line: {e}")
            return False

    def remove_position_line(self, symbol: str) -> bool:
        """Remove all position lines for a symbol"""
        try:
            drawings = self._load_symbol_drawings(symbol)

            # Count items before removal
            original_ray_count = len(drawings["horizontal_rays"])
            original_note_count = len(drawings["notes"])

            # Remove position lines (green or red colored rays/notes)
            self._remove_existing_position_lines(drawings)

            removed_items = (original_ray_count - len(drawings["horizontal_rays"])) + \
                            (original_note_count - len(drawings["notes"]))

            if removed_items > 0:
                success = self._save_symbol_drawings(symbol, drawings)
                if success:
                    self._refresh_chart()
                    logger.info(f"Removed position lines for {symbol}")
                return success
            else:
                logger.info(f"No position lines found for {symbol}")
                return True

        except Exception as e:
            logger.error(f"Error removing position line: {e}")
            return False

    def _remove_existing_position_lines(self, drawings: Dict):
        """Helper to remove existing position lines from drawings"""
        # Remove position rays (green or red colored)
        drawings["horizontal_rays"] = [
            ray for ray in drawings["horizontal_rays"]
            if not (ray.get("type") == "horizontal_ray" and
                    ray.get("color") in ["#00FF00", "#FF0000"])
        ]

        # Remove position notes (containing "Bought" or "Shorted")
        drawings["notes"] = [
            note for note in drawings["notes"]
            if not (note.get("type") == "note" and
                    any(keyword in note.get("text", "") for keyword in ["Bought", "Shorted"]))
        ]

    def _refresh_chart(self):
        """Signal the chart to refresh"""
        try:
            self.chart_refresh_requested.emit()
            # Also directly refresh if chart is available
            if hasattr(self.main_window, 'candlestick_chart'):
                chart = self.main_window.candlestick_chart
                if hasattr(chart, 'reload_drawings'):
                    chart.reload_drawings()
        except Exception as e:
            logger.error(f"Error refreshing chart: {e}")

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