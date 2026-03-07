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
#   chartBridge.notify_order_dialog_requested(json)  — right-click → place order
#   chartBridge.notify_zoom_changed(count)           — user scrolled / zoomed

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
    alert_creation_requested = Signal(str)  # alert JSON
    order_dialog_requested = Signal(str)    # order JSON
    text_note_requested = Signal(str)       # mouse-pos JSON
    text_note_edit_requested = Signal(str)  # note JSON

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
    def notify_order_dialog_requested(self, order_json: str) -> None:
        if self._guard("notify_order_dialog_requested", order_json):
            return
        if self._valid_json(order_json, "order_dialog_requested"):
            self.order_dialog_requested.emit(order_json)

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
