# kite/widgets/status_bar.py

import logging
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
        self._build_ui()
        self._apply_styles()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        # 0 vertical margin makes it sit flush against the bottom edge
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(16)

        # Uppercase for a stronger, institutional feel
        self.market_label = QLabel("MARKET: --")
        self.api_label = QLabel("API: --")
        self.heartbeat_label = QLabel("HEARTBEAT: --")

        # Add indicators to layout
        for label in (self.market_label, self.api_label, self.heartbeat_label):
            label.setObjectName("statusLabel")
            label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            layout.addWidget(label)

        layout.addStretch(1)

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
        self.market_label.setText(f"MARKET: {text.upper()}")

    def set_api_status(self, text: str) -> None:
        self.api_label.setText(f"API: {text.upper()}")

    def set_heartbeat(self, text: str) -> None:
        self.heartbeat_label.setText(f"HEARTBEAT: {text}")

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
        msg = f"{symbol} (MKT)" if symbol else "UNKNOWN (MKT)"
        self._post("ROUTED", msg, "info", 3000)

    def show_order_completed(self, symbol: str = "", pnl: str = "") -> None:
        msg = f"{symbol}" if symbol else "UNKNOWN"
        if pnl:
            msg = f"{msg} | PNL: {pnl}"
        self._post("FILLED", msg, "success", 3000)

    def show_order_failed(self, reason: str = "") -> None:
        self._post("REJECTED", reason or "Unknown reason", "error", 3000)

    def show_order_rejected(self, reason: str = "") -> None:
        self._post("REJECTED", reason or "Unknown reason", "error", 3000)

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

        ignored_states = {
            "UPDATE",
            "VALIDATION PENDING",
            "PUT ORDER REQ RECEIVED",
            "MODIFY VALIDATION PENDING",
            "MODIFY PENDING",
            "OPEN",
            "PENDING",
            "TRIGGER PENDING",
            "AMO REQ RECEIVED",
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
            short_reason = (reason[:30] + "...") if len(reason) > 30 else reason
            self._post("REJECTED", f"{symbol} [{short_reason}]", "error", 3000)
            return

        if raw_status in {"CANCELLED", "CANCELED"}:
            self._post("CANCELED", f"{direction_sign}{qty} {symbol}", "warn", 3000)
            return

        if raw_status in {"PUT ORDER REQ", "ROUTED"}:
            self._post("ROUTED", f"{direction_sign}{qty} {symbol} ({order_type})", "info", 3000)
            return

        self._post(raw_status or "ORDER UPDATE", f"{symbol} | QTY: {qty}", "info", 3000)

    def show_position_update(self, symbol: str, pnl: str) -> None:
        pass

    def show_error(self, message: str) -> None:
        self._post("Error", message, "error", 6000)

    def show_info(self, message: str) -> None:
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
            current = self._status_bar.heartbeat_label.text().replace("HEARTBEAT: ", "").strip()
            next_state = "○" if current == "●" else "●"
            self._status_bar.set_heartbeat(next_state)

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
        self._post("Notification", message, kind, timeout)

    # ── Internal ──────────────────────────────────────────────────────────

    def _post(self, title: str, message: str, kind: str, ttl: int) -> None:
        """Creates and displays the floating popup."""
        try:
            toast = ToastNotification(title, message, kind, ttl)
            toast.show_toast()
        except Exception as e:
            logger.error(f"Failed to show popup: {e}")


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
