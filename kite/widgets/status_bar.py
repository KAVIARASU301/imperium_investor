# kite/widgets/status_bar.py

import logging
import re
from typing import Optional

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

# Import our new professional popups
from kite.widgets.notifications import ToastNotification

logger = logging.getLogger(__name__)


class StatusBar(QWidget):
    """
    Ultra-compact, production-ready bottom ribbon.
    Sharp edges, no rounded corners, strictly for core system vitals.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("bottomStatusBar")
        # Lock to a strict, thin ribbon size
        self.setFixedHeight(20)
        self._layout: Optional[QHBoxLayout] = None
        self._status_alignment = "left"
        self._build_ui()
        self._apply_styles()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        self._layout = layout
        # 0 vertical margin makes it sit flush against the bottom edge
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(16)

        # Uppercase for a stronger, institutional feel
        self.market_label = QLabel("MARKET: --")
        self.api_label = QLabel("API: --")
        self.heartbeat_label = QLabel("HEARTBEAT: ●")
        self._heartbeat_pulse_on = False

        # Add indicators to layout
        for label in (self.market_label, self.api_label, self.heartbeat_label):
            label.setObjectName("statusLabel")
            label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            layout.addWidget(label)

        layout.addStretch(1)


    def set_elements_alignment(self, alignment: str) -> None:
        desired = "right" if str(alignment).lower() == "right" else "left"
        if not self._layout or desired == self._status_alignment:
            self._status_alignment = desired
            return

        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.setParent(None)

        if desired == "right":
            self._layout.addStretch(1)

        for label in (self.market_label, self.api_label, self.heartbeat_label):
            self._layout.addWidget(label)

        if desired == "left":
            self._layout.addStretch(1)

        self._status_alignment = desired

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            #bottomStatusBar {
                background-color: #0d0f14; /* Deep, solid background */
                border-top: 1px solid #222630; /* Sharp top separator line */
                border-bottom: none;
                border-left: none;
                border-right: none;
                border-radius: 0px; /* Force sharp edges */
            }
            #statusLabel {
                color: #7b8496; /* Subdued gray text so it doesn't distract */
                font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 0.35px; /* Slight tracking for readability */
            }
        """
        )

    def set_market_status(self, text: str) -> None:
        status = (text or "--").upper()
        market_color_map = {
            "OPEN": "#00d4a8",
            "CLOSED": "#3a4d60",
        }
        color = market_color_map.get(status, "#7b8496")
        self.market_label.setText(f'MARKET: <span style="color:{color};">{status}</span>')

    def set_api_status(self, text: str) -> None:
        status = (text or "--").upper()
        dot_color_map = {
            "CONNECTED": "#00d4a8",
            "ERROR": "#ff4d6a",
        }
        dot_color = dot_color_map.get(status, "#7b8496")
        self.api_label.setText(f'API: {status} <span style="color:{dot_color};">●</span>')

    def set_heartbeat(self, text: str) -> None:
        pulse_color = "#00d4a8" if self._heartbeat_pulse_on else "#2a3a50"
        self.heartbeat_label.setText(f'HEARTBEAT: <span style="color:{pulse_color};">{text}</span>')

    def set_message(self, text: str) -> None:
        # Dummy method to prevent crashes since we removed message_label
        pass


class GlobalStatusManager(QObject):
    """
    Singleton manager that routes status updates to professional Toast Popups.
    """

    _instance: Optional["GlobalStatusManager"] = None

    def __new__(cls) -> "GlobalStatusManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def initialize(self, dummy_bar=None) -> None:
        self._status_bar = dummy_bar
        if self._status_bar:
            self._status_bar.set_market_status("--")
            self._status_bar.set_api_status("--")
            self._status_bar.set_heartbeat("●")
        logger.debug("GlobalStatusManager initialized")

    def is_initialized(self) -> bool:
        return True

    # ── High-level helpers ──────────────────────────────────────────────────

    def show_order_placed(self, symbol: str = "") -> None:
        """Deprecated for order flows; kept for backwards compatibility."""
        return

    def notify(self, event: str, symbol: str = "", detail: str = "") -> None:
        """
        Single entry point for order lifecycle notifications.
        Handles toast and sound atomically.

        event: 'submitted' | 'filled' | 'rejected' | 'cancelled' | 'partial'
        """
        from kite.utils.sounds import play_alert, play_entry_exit, play_error, play_order_placed

        if event == "submitted":
            play_order_placed()
            return
        if event == "filled":
            self._post("FILLED", f"{symbol} {detail}".strip(), "success", 3000)
            play_entry_exit()
            return
        if event == "rejected":
            self._post("REJECTED", f"{symbol} — {detail}".strip(" —"), "error", 4000)
            play_error()
            return
        if event == "cancelled":
            self._post("CANCELLED", symbol, "warn", 3000)
            return
        if event == "partial":
            self._post("PARTIAL", f"{symbol} {detail}".strip(), "warn", 3000)
            play_alert()
            return

    def show_order_completed(self, symbol: str = "", pnl: str = "") -> None:
        msg = f"{symbol}" if symbol else "UNKNOWN"
        if pnl:
            msg = f"{msg} | PNL: {pnl}"
        self._post("FILLED", msg, "success", 3000)

    def show_order_failed(self, reason: str = "") -> None:
        translated = self._translate_message(reason or "Unknown reason")
        detail = translated.split(":", 1)[1].strip() if ":" in translated else translated
        self._post("REJECTED", detail, "error", 3000)

    def show_order_rejected(self, reason: str = "") -> None:
        translated = self._translate_message(reason or "Unknown reason")
        detail = translated.split(":", 1)[1].strip() if ":" in translated else translated
        self._post("REJECTED", detail, "error", 3000)

    def show_order_cancelled(self, symbol: str = "") -> None:
        msg = f"{symbol}" if symbol else "UNKNOWN"
        self._post("CANCELED", msg, "warn", 3000)

    def show_order_update(self, order_dict: dict) -> None:
        """
        Gatekeeper for order notifications.
        Emits only terminal/actionable updates in concise trading lexicon.
        """
        raw_status = str(order_dict.get("status", "")).upper().strip()
        symbol = str(order_dict.get("tradingsymbol") or "UNKNOWN").upper()
        qty = int(order_dict.get("filled_quantity") or order_dict.get("quantity") or 0)
        price = order_dict.get("average_price") or order_dict.get("price") or "MKT"
        side = str(order_dict.get("transaction_type") or "BUY").upper()
        order_type = str(order_dict.get("order_type") or "MKT").upper()

        if raw_status in {"ROUTED", "PUT ORDER REQ RECEIVED"}:
            # Keep routed acknowledgements silent visually but provide subtle
            # audible feedback so submission does not feel unresponsive.
            try:
                from kite.utils.sounds import play_order_placed
                play_order_placed()
            except Exception:
                pass
            return

        ignored_states = {
            "UPDATE",
            "VALIDATION PENDING",
            "PUT ORDER REQ RECEIVED",
            "MODIFY VALIDATION PENDING",
            "MODIFY PENDING",
            "OPEN",
            "PENDING",
            "SUBMITTED",
            "TRIGGER PENDING",
            "AMO REQ RECEIVED",
            "AMO SUBMITTED",
            "MODIFIED",
        }
        if raw_status in ignored_states:
            return

        direction_sign = "+" if side == "BUY" else "-"

        if raw_status in {"COMPLETE", "FILLED"}:
            self._post("FILLED", f"{direction_sign}{qty} {symbol} @ {price}", "success", 3000)
            return

        if raw_status == "REJECTED":
            reason = str(
                order_dict.get("status_message")
                or order_dict.get("reject_reason")
                or "Unknown Reason"
            )
            clean_reason = self._translate_message(reason)
            short_reason = (clean_reason[:50] + "...") if len(clean_reason) > 50 else clean_reason
            self._post("REJECTED", f"{symbol} [{short_reason}]", "error", 3000)
            return

        if raw_status in {"CANCELLED", "CANCELED"}:
            self._post("CANCELED", f"{direction_sign}{qty} {symbol}", "warn", 3000)
            return

        # Ignore any remaining non-terminal statuses to prevent toast spam.
        return

    def show_position_update(self, symbol: str, pnl: str) -> None:
        pass

    def show_error(self, message: str) -> None:
        self._post("ERROR", self._translate_message(message), "error", 6000)

    @staticmethod
    def _is_order_lifecycle_text(message: str) -> bool:
        text = (message or "").upper()
        order_tokens = (
            "ORDER", "ROUTED", "FILLED", "REJECTED", "CANCELED", "CANCELLED",
            "SUBMITTING", "ENTRY", "EXIT", "QTY", "MKT", "LMT"
        )
        return any(token in text for token in order_tokens)

    def show_info(self, message: str) -> None:
        if self._is_order_lifecycle_text(message):
            return
        self._post("System Info", message, "info", 4000)

    def show_market_status(self, status_text: str) -> None:
        self.set_market_indicator(status_text)
        self._post("Market Status", status_text, "info", 4000)

    def show_api_status(self, status_text: str) -> None:
        self.set_api_indicator(status_text)
        kind = "success" if status_text.upper() == "CONNECTED" else "warn"
        self._post("API Status", status_text, kind, 4000)

    def set_market_indicator(self, status_text: str) -> None:
        if getattr(self, "_status_bar", None):
            self._status_bar.set_market_status(status_text)

    def set_api_indicator(self, status_text: str) -> None:
        if getattr(self, "_status_bar", None):
            self._status_bar.set_api_status(status_text)

    def pulse_heartbeat(self) -> None:
        if getattr(self, "_status_bar", None):
            self._status_bar._heartbeat_pulse_on = not self._status_bar._heartbeat_pulse_on
            self._status_bar.set_heartbeat("●")

    def set_ready(self) -> None:
        # We removed the "Ready" label, so this method safely does nothing.
        pass

    def clear_status(self) -> None:
        pass

    def set_message(self, message: str, timeout: int = 3000, level: str = "info") -> None:
        # Route generic string messages purely to toasts
        level_map = {
            "error": "error",
            "danger": "error",
            "warning": "warn",
            "warn": "warn",
            "success": "success",
            "action": "info",
        }
        kind = level_map.get(level.lower(), "info")
        if self._is_order_lifecycle_text(message):
            return
        if kind == "error":
            message = self._translate_message(message)
        self._post("Notification", message, kind, timeout)

    # ── Internal ──────────────────────────────────────────────────────────

    def _post(self, title: str, message: str, kind: str, ttl: int) -> None:
        """Creates and displays the floating popup."""
        try:
            toast = ToastNotification(title, message, kind, ttl)
            toast.show_toast()
        except Exception as e:
            logger.error(f"Failed to show popup: {e}")

    @staticmethod
    def _translate_message(message: str) -> str:
        """Translate noisy broker/API failures into compact trader-facing alerts."""
        cleaned = re.sub(r"\s+", " ", (message or "").strip())
        if not cleaned:
            return "Unknown error"

        msg_lower = cleaned.lower()

        if "market is closed" in msg_lower or "markets are closed" in msg_lower or "after market" in msg_lower or "amo" in msg_lower:
            return "REJECTED: MARKET CLOSED"

        if "insufficient" in msg_lower and "margin" in msg_lower:
            return "REJECTED: INSUFFICIENT FUNDS"
        if "available cash" in msg_lower or "buying power" in msg_lower:
            return "REJECTED: INSUFFICIENT BUYING POWER"

        if "trigger price" in msg_lower:
            return "REJECTED: INVALID TRIGGER PRICE"
        if "limit price" in msg_lower:
            return "REJECTED: INVALID LIMIT PRICE"
        if any(key in msg_lower for key in ("circuit breaker", "upper circuit", "lower circuit")):
            return "REJECTED: CIRCUIT LIMIT"
        if "rms" in msg_lower and "blocked" in msg_lower:
            return "REJECTED: RMS RULE VIOLATION"

        if "timeout" in msg_lower:
            return "ERROR: NETWORK TIMEOUT"
        if "502" in msg_lower or "bad gateway" in msg_lower:
            return "ERROR: BROKER API DOWN"

        if len(cleaned) > 65:
            return (cleaned[:62] + "...").upper()

        return cleaned.upper()


# ─── Module-level singleton + convenience functions ───────────────────────────

status = GlobalStatusManager()


def show_order_placed(symbol: str = ""):
    status.show_order_placed(symbol)


def show_order_completed(symbol: str = "", pnl: str = ""):
    status.show_order_completed(symbol, pnl)


def show_order_failed(reason: str = ""):
    status.show_order_failed(reason)


def show_order_rejected(reason: str = ""):
    status.show_order_rejected(reason)


def show_order_cancelled(symbol: str = ""):
    status.show_order_cancelled(symbol)


def show_position_update(symbol: str, pnl: str):
    status.show_position_update(symbol, pnl)


def show_error(message: str):
    status.show_error(message)


def show_info(message: str):
    status.show_info(message)


def show_market_status(status_text: str):
    status.show_market_status(status_text)


def show_api_status(status_text: str):
    status.show_api_status(status_text)


def set_ready():
    status.set_ready()


def clear_status():
    status.clear_status()
