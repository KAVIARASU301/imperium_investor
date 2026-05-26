# chart_engine/core/chart_bridge.py
#
# QObject exposed to JavaScript via QWebChannel.
# JavaScript calls Python slots; Python emits Qt signals that the chart widget
# connects to.
#
# Message contract (JS → Python):
#   chartBridge.set_web_channel_initialized()       — JS tells us it's ready
#   chartBridge.notify_drawings_changed(json)        — drawing was added/moved/deleted
#   chartBridge.notify_text_note_requested(json)     — user clicked "add note" tool
#   chartBridge.notify_text_note_edit_requested(json)— user double-clicked a note
#   chartBridge.notify_alert_creation_requested(json)— right-click → set alert
#   chartBridge.notify_alert_price_updated(json)     — alert line dragged to new price
#   chartBridge.notify_order_dialog_requested(json)  — right-click → place order
#   chartBridge.notify_zoom_changed(count)           — user scrolled / zoomed
#   chartBridge.notify_drawing_tool_cleared()        — active tool was canceled/consumed

import json
import logging
from typing import Any, List, Tuple

from PySide6.QtCore import QObject, Signal, Slot

logger = logging.getLogger(__name__)


class ChartBridge(QObject):
    """
    Bidirectional bridge between the PySide6 widget layer and the JS canvas chart.
    Handles queuing of calls that arrive before the WebChannel is fully ready.
    """

    # ── Outgoing signals (consumed by CandlestickChart) ──────────────────────
    chart_ready = Signal()
    drawings_changed = Signal(str)          # drawings JSON
    visible_candle_count_changed = Signal(int)
    zoom_preferences_changed = Signal(int, int, int)  # visible count, candle width, candle spacing
    alert_creation_requested = Signal(str)  # alert JSON
    alert_price_updated = Signal(str)       # {symbol, old_price, new_price} — alert drag
    alert_line_deleted = Signal(str)        # {symbol, price} — alert line deleted
    stop_loss_price_updated = Signal(str)   # {symbol, old_price, new_price} — SL drag
    stop_loss_line_deleted = Signal(str)    # {symbol, price} — SL line deleted
    target_price_updated = Signal(str)      # {symbol, old_price, new_price} — target drag
    target_line_deleted = Signal(str)       # {symbol, price} — target line deleted
    order_dialog_requested = Signal(str)    # order JSON
    text_note_requested = Signal(str)       # mouse-pos JSON
    text_note_edit_requested = Signal(str)  # note JSON
    drawing_tool_cleared = Signal()
    timeframe_step_requested = Signal(int)  # +1 for up, -1 for down
    older_data_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.webChannelInitialized = False
        self._pending: List[Tuple[str, Any]] = []

    # ─── JS → Python slots ───────────────────────────────────────────────────

    @Slot()
    def set_web_channel_initialized(self) -> None:
        """Called by JS once QWebChannel transport is set up."""
        self.webChannelInitialized = True
        logger.debug("ChartBridge: WebChannel initialized.")

        # Drain any calls that arrived before the channel was ready
        for method_name, args in self._pending:
            try:
                getattr(self, method_name)(args)
            except Exception as exc:
                logger.error("ChartBridge pending flush error (%s): %s", method_name, exc)
        self._pending.clear()

        self.chart_ready.emit()

    @Slot(str)
    def notify_drawings_changed(self, drawings_json: str) -> None:
        if self._guard("notify_drawings_changed", drawings_json):
            return
        if self._valid_json(drawings_json, "drawings"):
            self.drawings_changed.emit(drawings_json)

    @Slot(int)
    def notify_zoom_changed(self, count: int) -> None:
        if self.webChannelInitialized:
            self.visible_candle_count_changed.emit(count)

    @Slot(int, int, int)
    def notify_zoom_preferences_changed(self, count: int, candle_width: int, candle_spacing: int) -> None:
        if self.webChannelInitialized:
            self.zoom_preferences_changed.emit(count, candle_width, candle_spacing)

    @Slot(str)
    def notify_text_note_requested(self, mouse_pos_json: str) -> None:
        if self._guard("notify_text_note_requested", mouse_pos_json):
            return
        if self._valid_json(mouse_pos_json, "text_note_requested"):
            self.text_note_requested.emit(mouse_pos_json)

    @Slot(str)
    def notify_text_note_edit_requested(self, note_json: str) -> None:
        if self._guard("notify_text_note_edit_requested", note_json):
            return
        if self._valid_json(note_json, "text_note_edit_requested"):
            self.text_note_edit_requested.emit(note_json)

    @Slot(str)
    def notify_alert_creation_requested(self, alert_json: str) -> None:
        if self._guard("notify_alert_creation_requested", alert_json):
            return
        if self._valid_json(alert_json, "alert_creation_requested"):
            self.alert_creation_requested.emit(alert_json)

    @Slot(str)
    def notify_alert_price_updated(self, payload: str) -> None:
        """
        Called by JS when an alert line (lineCategory==='alert') is dragged
        to a new Y position. Payload: {"symbol", "old_price", "new_price"}
        """
        if not self.webChannelInitialized:
            self._pending.append(("notify_alert_price_updated", payload))
            return
        try:
            json.loads(payload)
            self.alert_price_updated.emit(payload)
            logger.info(f"ChartBridge: alert price updated from chart: {payload}")
        except json.JSONDecodeError as e:
            logger.error(f"ChartBridge: invalid alert_price_updated JSON: {e}")
        except Exception as e:
            logger.error(f"ChartBridge: error in notify_alert_price_updated: {e}")

    @Slot(str)
    def notify_alert_line_deleted(self, payload: str) -> None:
        if self._guard("notify_alert_line_deleted", payload):
            return
        if self._valid_json(payload, "alert_line_deleted"):
            self.alert_line_deleted.emit(payload)

    @Slot(str)
    def notify_stop_loss_price_updated(self, payload: str) -> None:
        """Called by JS when a stop-loss line is dragged to a new price."""
        if not self.webChannelInitialized:
            self._pending.append(("notify_stop_loss_price_updated", payload))
            return
        if self._valid_json(payload, "stop_loss_price_updated"):
            self.stop_loss_price_updated.emit(payload)
            logger.info(f"ChartBridge: stop-loss price updated from chart: {payload}")

    @Slot(str)
    def notify_stop_loss_line_deleted(self, payload: str) -> None:
        if self._guard("notify_stop_loss_line_deleted", payload):
            return
        if self._valid_json(payload, "stop_loss_line_deleted"):
            self.stop_loss_line_deleted.emit(payload)

    @Slot(str)
    def notify_target_price_updated(self, payload: str) -> None:
        if self._guard("notify_target_price_updated", payload):
            return
        if self._valid_json(payload, "target_price_updated"):
            self.target_price_updated.emit(payload)

    @Slot(str)
    def notify_target_line_deleted(self, payload: str) -> None:
        if self._guard("notify_target_line_deleted", payload):
            return
        if self._valid_json(payload, "target_line_deleted"):
            self.target_line_deleted.emit(payload)

    @Slot(str)
    def notify_order_dialog_requested(self, order_json: str) -> None:
        if self._guard("notify_order_dialog_requested", order_json):
            return
        if self._valid_json(order_json, "order_dialog_requested"):
            self.order_dialog_requested.emit(order_json)

    @Slot()
    def notify_drawing_tool_cleared(self) -> None:
        if self.webChannelInitialized:
            self.drawing_tool_cleared.emit()

    @Slot(int)
    def notify_timeframe_step_requested(self, direction: int) -> None:
        if self.webChannelInitialized and direction in (-1, 1):
            self.timeframe_step_requested.emit(direction)

    @Slot()
    def notify_older_data_requested(self) -> None:
        if self.webChannelInitialized:
            self.older_data_requested.emit()

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _guard(self, method: str, args: Any) -> bool:
        """Queue the call if channel not ready. Returns True if queued."""
        if not self.webChannelInitialized:
            self._pending.append((method, args))
            return True
        return False

    def _valid_json(self, text: str, context: str) -> bool:
        try:
            json.loads(text)
            return True
        except json.JSONDecodeError as exc:
            logger.error("ChartBridge invalid JSON [%s]: %s", context, exc)
            return False
