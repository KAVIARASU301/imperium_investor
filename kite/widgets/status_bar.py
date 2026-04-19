# kite/widgets/status_bar.py (Replacement)

import logging
from typing import Optional
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QLabel, QWidget, QHBoxLayout

# Import our new professional popups
from kite.widgets.notifications import ToastNotification

logger = logging.getLogger(__name__)


class StatusBar(QWidget):
    """
    Lightweight status bar widget kept for compatibility with existing toolbar code.

    The old LED status bar was replaced by toast notifications, but some modules
    still instantiate `StatusBar` and pass it into the global status manager.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("compactStatusBar")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(8)

        self.market_label = QLabel("Market: --")
        self.api_label = QLabel("API: --")
        self.message_label = QLabel("Ready")

        for label in (self.market_label, self.api_label, self.message_label):
            label.setObjectName("statusLabel")
            layout.addWidget(label)

    def set_market_status(self, text: str) -> None:
        self.market_label.setText(f"Market: {text}")

    def set_api_status(self, text: str) -> None:
        self.api_label.setText(f"API: {text}")

    def set_message(self, text: str) -> None:
        self.message_label.setText(text)

class GlobalStatusManager(QObject):
    """
    Singleton manager that routes status updates to professional Toast Popups.
    Acts as a drop-in replacement for the old LED status bar manager.
    """
    _instance: Optional["GlobalStatusManager"] = None

    def __new__(cls) -> "GlobalStatusManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def initialize(self, dummy_bar=None) -> None:
        # Kept for backwards compatibility so main_window.py doesn't crash
        # when it calls status.initialize(self.header_toolbar.status_bar)
        logger.debug("GlobalStatusManager initialized with Popups")

    def is_initialized(self) -> bool:
        return True

    # ── High-level helpers ──────────────────────────────────────────────────

    def show_order_placed(self, symbol: str = "") -> None:
        title = "Order Submitted"
        msg = f"Your order for {symbol} has been sent to the exchange." if symbol else "Order sent."
        self._post(title, msg, "info", 4000)

    def show_order_completed(self, symbol: str = "", pnl: str = "") -> None:
        title = "Order Filled"
        parts = [f"Executed: {symbol}"]
        if pnl:
            parts.append(f"PnL: {pnl}")
        self._post(title, " | ".join(parts), "success", 5000)

    def show_order_failed(self, reason: str = "") -> None:
        self._post("Order Failed", reason or "The order could not be placed.", "error", 6000)

    def show_order_rejected(self, reason: str = "") -> None:
        self._post("Order Rejected", reason or "The exchange rejected the order.", "error", 6000)

    def show_order_cancelled(self, symbol: str = "") -> None:
        msg = f"Order for {symbol} was cancelled." if symbol else "Order cancelled."
        self._post("Order Cancelled", msg, "warn", 4000)

    def show_position_update(self, symbol: str, pnl: str) -> None:
        # For rapid position updates, you might want to suppress popups to avoid spam
        # or route this to a smaller, quieter UI element if desired.
        pass

    def show_error(self, message: str) -> None:
        self._post("Error", message, "error", 6000)

    def show_info(self, message: str) -> None:
        self._post("System Info", message, "info", 4000)

    def show_market_status(self, status_text: str) -> None:
        self._post("Market Status", status_text, "info", 4000)

    def show_api_status(self, status_text: str) -> None:
        kind = "success" if status_text.upper() == "CONNECTED" else "warn"
        self._post("API Status", status_text, kind, 4000)

    def set_ready(self) -> None:
        pass

    def clear_status(self) -> None:
        pass

    def set_message(self, message: str, timeout: int = 3000, level: str = "info") -> None:
        level_map = {"error": "error", "danger": "error",
                     "warning": "warn", "warn": "warn",
                     "success": "success", "action": "info"}
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

def show_order_placed(symbol: str = ""): status.show_order_placed(symbol)
def show_order_completed(symbol: str = "", pnl: str = ""): status.show_order_completed(symbol, pnl)
def show_order_failed(reason: str = ""): status.show_order_failed(reason)
def show_order_rejected(reason: str = ""): status.show_order_rejected(reason)
def show_order_cancelled(symbol: str = ""): status.show_order_cancelled(symbol)
def show_position_update(symbol: str, pnl: str): status.show_position_update(symbol, pnl)
def show_error(message: str): status.show_error(message)
def show_info(message: str): status.show_info(message)
def show_market_status(status_text: str): status.show_market_status(status_text)
def show_api_status(status_text: str): status.show_api_status(status_text)
def set_ready(): status.set_ready()
def clear_status(): status.clear_status()
