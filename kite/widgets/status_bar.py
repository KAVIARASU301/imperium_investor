# kite/widgets/status_bar.py
"""
Production-grade status bar for Swing Trader.

Design principles:
  - Single text label, no animations, no flashing, no LEDs
  - Light grey baseline; semantic colour only for ERRORS (red) and
    CONFIRMED executions (green) — the two events that demand attention
  - Auto-clears back to "Ready" after a configurable timeout
  - Thread-safe: any thread can post via the convenience functions;
    colour and text are queued onto the Qt main thread via signal
  - GlobalStatusManager is a singleton; call status.initialize(widget)
    once from the main window, then use the module-level helpers anywhere
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal, Qt
from PySide6.QtWidgets import QLabel

logger = logging.getLogger(__name__)

# ─── Colour palette ───────────────────────────────────────────────────────────
# Keep it subtle.  Only errors and fills deviate from the default grey.
_COLOUR_DEFAULT = "#888888"   # resting / informational
_COLOUR_ERROR   = "#e05555"   # errors, rejections, failures
_COLOUR_SUCCESS = "#4ec994"   # order filled / completed
_COLOUR_WARN    = "#d4a84b"   # partial fills, degraded state


# ─── StatusBar widget ─────────────────────────────────────────────────────────

class StatusBar(QLabel):
    """
    Minimal single-line status label.

    Usage:
        bar = StatusBar()
        bar.post("Order placed — RELIANCE 100 @ ₹2,847", kind="info", ttl=4000)
    """

    _post_signal = Signal(str, str, int)   # message, kind, ttl_ms

    _KIND_COLOUR: dict[str, str] = {
        "info":    _COLOUR_DEFAULT,
        "success": _COLOUR_SUCCESS,
        "error":   _COLOUR_ERROR,
        "warn":    _COLOUR_WARN,
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusBar")
        self.setFixedHeight(22)
        self.setMinimumWidth(320)
        self.setMaximumWidth(640)
        self.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.timeout.connect(self._reset)

        # Cross-thread posting
        self._post_signal.connect(self._apply, Qt.ConnectionType.QueuedConnection)

        self._apply("Ready", "info", 0)
        self._base_style()

    # ── Public ────────────────────────────────────────────────────────────

    def post(self, message: str, kind: str = "info", ttl: int = 4000) -> None:
        """
        Display *message*.

        kind : "info" | "success" | "error" | "warn"
        ttl  : milliseconds before auto-reset to "Ready".
                Pass 0 to keep the message until the next post.
        """
        self._post_signal.emit(message, kind, ttl)

    def set_ready(self) -> None:
        self.post("Ready", "info", 0)

    # ── Internal ──────────────────────────────────────────────────────────

    def _apply(self, message: str, kind: str, ttl: int) -> None:
        self._clear_timer.stop()
        colour = self._KIND_COLOUR.get(kind, _COLOUR_DEFAULT)
        self.setText(message)
        self.setStyleSheet(
            f"QLabel#statusBar {{"
            f"  color: {colour};"
            f"  background: transparent;"
            f"  font-family: 'Segoe UI', 'Helvetica Neue', sans-serif;"
            f"  font-size: 11px;"
            f"  font-weight: 500;"
            f"  letter-spacing: 0.1px;"
            f"  padding: 0 8px;"
            f"  border: none;"
            f"}}"
        )
        if ttl > 0:
            self._clear_timer.start(ttl)

    def _reset(self) -> None:
        self._apply("Ready", "info", 0)

    def _base_style(self) -> None:
        self._apply("Ready", "info", 0)


# ─── GlobalStatusManager ──────────────────────────────────────────────────────

class GlobalStatusManager(QObject):
    """
    Singleton façade over StatusBar.

    Call ``initialize(bar)`` once from the main window.
    Thereafter, use the module-level helper functions from anywhere.
    """

    _instance: Optional["GlobalStatusManager"] = None

    def __new__(cls) -> "GlobalStatusManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # Guard against double-init (singleton pattern)
        if getattr(self, "_ready", False):
            return
        super().__init__()
        self._bar: Optional[StatusBar] = None
        self._ready = True

    def initialize(self, bar: StatusBar) -> None:
        self._bar = bar
        logger.debug("GlobalStatusManager attached to StatusBar")

    def is_initialized(self) -> bool:
        return self._bar is not None

    # ── High-level helpers (these are the only entry points you need) ──────

    def show_order_placed(self, symbol: str = "") -> None:
        msg = f"Order placed — {symbol}" if symbol else "Order placed"
        self._post(msg, "info", 3000)

    def show_order_completed(self, symbol: str = "", pnl: str = "") -> None:
        parts = ["Executed", symbol, pnl]
        msg = "  ·  ".join(p for p in parts if p)
        self._post(msg, "success", 5000)

    def show_order_failed(self, reason: str = "") -> None:
        msg = f"Order failed — {reason}" if reason else "Order failed"
        self._post(msg, "error", 6000)

    def show_order_rejected(self, reason: str = "") -> None:
        msg = f"Rejected — {reason}" if reason else "Order rejected"
        self._post(msg, "error", 6000)

    def show_order_cancelled(self, symbol: str = "") -> None:
        msg = f"Cancelled — {symbol}" if symbol else "Order cancelled"
        self._post(msg, "warn", 3000)

    def show_position_update(self, symbol: str, pnl: str) -> None:
        self._post(f"{symbol}  ·  {pnl}", "info", 4000)

    def show_error(self, message: str) -> None:
        self._post(message, "error", 6000)

    def show_info(self, message: str) -> None:
        self._post(message, "info", 3000)

    def show_market_status(self, status_text: str) -> None:
        self._post(f"Market — {status_text}", "info", 0)

    def show_api_status(self, status_text: str) -> None:
        kind = "success" if status_text.upper() == "CONNECTED" else "warn"
        self._post(f"API — {status_text}", kind, 0)

    def set_ready(self) -> None:
        if self._bar:
            self._bar.set_ready()

    def clear_status(self) -> None:
        self.set_ready()

    # Backward-compatible generic setter used by a few call-sites
    def set_message(self, message: str, timeout: int = 3000, level: str = "info") -> None:
        level_map = {"error": "error", "danger": "error",
                     "warning": "warn", "warn": "warn",
                     "success": "success", "action": "info"}
        kind = level_map.get(level.lower(), "info")
        self._post(message, kind, timeout)

    # ── Internal ──────────────────────────────────────────────────────────

    def _post(self, message: str, kind: str, ttl: int) -> None:
        if self._bar:
            self._bar.post(message, kind, ttl)
        else:
            logger.debug("StatusBar not initialised — dropped: %s", message)


# ─── Module-level singleton + convenience functions ───────────────────────────

status = GlobalStatusManager()


def show_order_placed(symbol: str = "") -> None:
    status.show_order_placed(symbol)

def show_order_completed(symbol: str = "", pnl: str = "") -> None:
    status.show_order_completed(symbol, pnl)

def show_order_failed(reason: str = "") -> None:
    status.show_order_failed(reason)

def show_order_rejected(reason: str = "") -> None:
    status.show_order_rejected(reason)

def show_order_cancelled(symbol: str = "") -> None:
    status.show_order_cancelled(symbol)

def show_position_update(symbol: str, pnl: str) -> None:
    status.show_position_update(symbol, pnl)

def show_error(message: str) -> None:
    status.show_error(message)

def show_info(message: str) -> None:
    status.show_info(message)

def show_market_status(status_text: str) -> None:
    status.show_market_status(status_text)

def show_api_status(status_text: str) -> None:
    status.show_api_status(status_text)

def set_ready() -> None:
    status.set_ready()

def clear_status() -> None:
    status.clear_status()