# kite/widgets/status_bar.py

import logging
import re
from typing import Optional

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

# Import our new professional popups
from kite.widgets.notifications import ToastNotification
from kite.utils.sounds import play_alert, play_entry_exit, play_error

logger = logging.getLogger(__name__)


class StatusBar(QWidget):
    """
    Production-grade bottom status ribbon.

    Purpose:
    - Visually closes the application at the bottom edge.
    - Keeps only persistent system vitals here.
    - Routes temporary messages to toast notifications.
    """

    HEIGHT = 26
    EDGE_HEIGHT = 1
    CONTENT_HEIGHT = HEIGHT - EDGE_HEIGHT

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("bottomStatusBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self._layout: Optional[QHBoxLayout] = None
        self._status_alignment = "left"
        self._metrics_on_right = True

        self._build_ui()
        self._apply_styles()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.top_edge = QFrame(self)
        self.top_edge.setObjectName("statusTopEdge")
        self.top_edge.setFixedHeight(self.EDGE_HEIGHT)
        self.top_edge.setFrameShape(QFrame.NoFrame)

        self.content = QWidget(self)
        self.content.setObjectName("statusContent")
        self.content.setAttribute(Qt.WA_StyledBackground, True)
        self.content.setFixedHeight(self.CONTENT_HEIGHT)
        self.content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QHBoxLayout(self.content)
        self._layout = layout
        layout.setContentsMargins(12, 0, 12, 1)
        layout.setSpacing(12)

        root_layout.addWidget(self.top_edge)
        root_layout.addWidget(self.content)

        self.market_label = QLabel("MARKET: --", self.content)
        self.api_label = QLabel('API <span style="color:#6f7a8c;">●</span>', self.content)
        self.open_pnl_label = QLabel("OPEN P&L: --", self.content)
        self.exposure_label = QLabel("EXPOSURE: --", self.content)

        self.group_separator = QFrame(self.content)
        self.group_separator.setObjectName("statusGroupSeparator")
        self.group_separator.setFixedSize(1, 13)
        self.group_separator.setFrameShape(QFrame.NoFrame)

        for label in (
            self.market_label,
            self.api_label,
            self.open_pnl_label,
            self.exposure_label,
        ):
            label.setObjectName("statusLabel")
            label.setTextFormat(Qt.RichText)
            label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            label.setMinimumHeight(self.CONTENT_HEIGHT - 2)
            label.setContentsMargins(0, 0, 0, 0)

        self._rebuild_layout()

    def set_elements_alignment(self, alignment: str) -> None:
        desired = "right" if str(alignment).lower() == "right" else "left"
        if not self._layout:
            self._status_alignment = desired
            return
        if desired == self._status_alignment:
            self._rebuild_layout()
            return
        self._status_alignment = desired
        self._rebuild_layout()

    def set_metrics_alignment(self, on_right: bool) -> None:
        self._metrics_on_right = bool(on_right)
        self._rebuild_layout()

    def _clear_layout(self) -> None:
        if not self._layout:
            return
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(self.content)

    def _add_status_widgets(self, widgets: tuple[QWidget, ...]) -> None:
        if not self._layout:
            return
        for widget in widgets:
            self._layout.addWidget(widget)

    def _rebuild_layout(self) -> None:
        if not self._layout:
            return

        self._clear_layout()

        base_labels = (self.market_label, self.api_label)
        metric_labels = (self.open_pnl_label, self.exposure_label)

        if self._metrics_on_right:
            left_group = base_labels
            right_group = metric_labels
        else:
            left_group = metric_labels
            right_group = base_labels

        if self._status_alignment == "right":
            self._layout.addStretch(1)
            self._add_status_widgets((*left_group, self.group_separator, *right_group))
            return

        self._add_status_widgets(left_group)
        self._layout.addStretch(1)
        self._layout.addWidget(self.group_separator)
        self._add_status_widgets(right_group)

    def set_positions_metrics(self, has_data: bool, open_pnl: float = 0.0, exposure: float = 0.0) -> None:
        if not has_data:
            self.open_pnl_label.setText("OPEN P&L: --")
            self.exposure_label.setText("EXPOSURE: --")
            return

        pnl_color = "#48c78e" if open_pnl >= 0 else "#e65a6a"
        sign = "+" if open_pnl >= 0 else ""
        self.open_pnl_label.setText(
            f'OPEN P&L: <span style="color:{pnl_color}; font-weight:700;">{sign}{open_pnl:,.0f}</span>'
        )
        self.exposure_label.setText(
            f'EXPOSURE: <span style="color:#aab4c3; font-weight:650;">{exposure:,.0f}</span>'
        )

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget#bottomStatusBar {
                background-color: #080d14;
                border: none;
                border-radius: 0px;
            }

            QFrame#statusTopEdge {
                background-color: #2a3442;
                border: none;
            }

            QWidget#statusContent {
                background-color: #0c121b;
                border: none;
                border-bottom: 1px solid #05080d;
                border-radius: 0px;
            }

            QFrame#statusGroupSeparator {
                background-color: #263141;
                border: none;
            }

            QLabel#statusLabel {
                background: transparent;
                border: none;
                color: #7e899a;
                font-family: "Inter", "Segoe UI", "Roboto", "Noto Sans", sans-serif;
                font-size: 10px;
                font-weight: 600;
                padding: 0px;
                margin: 0px;
            }
            """
        )

    def set_market_status(self, text: str) -> None:
        status = (text or "--").upper()
        market_color_map = {
            "OPEN": "#48c78e",
            "CLOSED": "#5f6b7a",
        }
        color = market_color_map.get(status, "#7e899a")
        self.market_label.setText(f'MARKET: <span style="color:{color}; font-weight:700;">{status}</span>')

    def set_api_status(self, text: str) -> None:
        status = (text or "--").upper()
        dot_color_map = {
            "CONNECTED": "#48c78e",
            "ERROR": "#e65a6a",
            "DISCONNECTED": "#d4a84b",
        }
        dot_color = dot_color_map.get(status, "#6f7a8c")
        self.api_label.setText(f'API <span style="color:{dot_color}; font-size:11px;">●</span>')

    def set_message(self, text: str) -> None:
        # Status bar is reserved for persistent system vitals.
        # Temporary messages should go through GlobalStatusManager toasts.
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
            self._post("FILLED", f"{symbol} {detail}".strip(), "success", 3000, sub_kind="filled")
            play_entry_exit()
            return
        if event == "rejected":
            self._post("REJECTED", f"{symbol} — {detail}".strip(" —"), "error", 4000, sub_kind="rejected")
            play_error()
            return
        if event == "cancelled":
            self._post("CANCELLED", symbol, "warn", 3000)
            return
        if event == "partial":
            self._post("PARTIAL FILL", f"{symbol} {detail}".strip(), "warn", 6000, sub_kind="partial_fill")
            play_alert()
            return

    def show_order_completed(self, symbol: str = "", pnl: str = "") -> None:
        msg = f"{symbol}" if symbol else "UNKNOWN"
        if pnl:
            msg = f"{msg} | PNL: {pnl}"
        self._post("FILLED", msg, "success", 3000, sub_kind="filled")

    def show_order_failed(self, reason: str = "") -> None:
        translated = self._translate_message(reason or "Unknown reason")
        detail = translated.split(":", 1)[1].strip() if ":" in translated else translated
        self._post("REJECTED", detail, "error", 3000, sub_kind="rejected")

    def show_order_rejected(self, reason: str = "") -> None:
        translated = self._translate_message(reason or "Unknown reason")
        detail = translated.split(":", 1)[1].strip() if ":" in translated else translated
        self._post("REJECTED", detail, "error", 3000, sub_kind="rejected")

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
            self._post("FILLED", f"{direction_sign}{qty} {symbol} @ {price}", "success", 3000, sub_kind="filled")
            return

        if raw_status == "REJECTED":
            reason = str(
                order_dict.get("status_message")
                or order_dict.get("reject_reason")
                or "Unknown Reason"
            )
            clean_reason = self._translate_message(reason)
            short_reason = (clean_reason[:50] + "...") if len(clean_reason) > 50 else clean_reason
            self._post("REJECTED", f"{symbol} [{short_reason}]", "error", 3000, sub_kind="rejected")
            return

        if raw_status in {"CANCELLED", "CANCELED"}:
            self._post("CANCELED", f"{direction_sign}{qty} {symbol}", "warn", 3000)
            return

        # Ignore any remaining non-terminal statuses to prevent toast spam.
        return

    def show_position_update(self, symbol: str, pnl: str) -> None:
        pass

    def show_error(self, message: str) -> None:
        translated = self._translate_message(message)
        sub_kind = "network" if "NETWORK" in translated else ("rate_limit" if "RATE" in translated else "rejected")
        self._post("ERROR", translated, "error", 6000, sub_kind=sub_kind)

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

    def set_ready(self) -> None:
        # We removed the "Ready" label, so this method safely does nothing.
        pass

    def clear_status(self) -> None:
        pass

    def show_notification(self, message: str, level: str = "info", timeout: int = 4000) -> None:
        """Show an explicit component notification without lifecycle text filtering."""
        normalized_level = (level or "info").lower()
        kind_map = {
            "error": "error",
            "danger": "error",
            "warning": "warn",
            "warn": "warn",
            "success": "success",
            "info": "info",
            "action": "info",
        }
        kind = kind_map.get(normalized_level, "info")
        display_message = self._translate_message(message) if kind == "error" else (message or "")

        text_upper = display_message.upper()
        is_network_notice = any(token in text_upper for token in ("NETWORK", "OFFLINE", "ONLINE", "RECONNECT"))
        is_api_notice = self._is_api_related_message(text_upper)

        if is_network_notice:
            title = "Network"
            sub_kind = "network"
            if timeout <= 0:
                timeout = 2500
        elif is_api_notice:
            title_map = {
                "error": "API Error",
                "warn": "API Warning",
                "success": "API Status",
                "info": "API Update",
            }
            title = title_map.get(kind, "API Update")
            sub_kind = "api"
        else:
            title_map = {
                "error": "Order Alert",
                "warn": "Order Update",
                "success": "Order Filled",
                "info": "Notification",
            }
            title = title_map.get(kind, "Notification")
            sub_kind = None
            if kind == "success" and ("FILL" in text_upper or "COMPLETE" in text_upper):
                sub_kind = "filled"
            elif kind == "error":
                sub_kind = "rejected"
            elif "PARTIAL" in text_upper:
                sub_kind = "partial_fill"

        self._post(title, display_message, kind, timeout, sub_kind=sub_kind)
        self._play_notification_sound(kind)

    @staticmethod
    def _play_notification_sound(kind: str) -> None:
        try:
            if kind == "success":
                play_entry_exit()
            elif kind == "error":
                play_error()
            elif kind == "warn":
                play_alert()
        except Exception as e:
            logger.debug(f"Notification sound failed: {e}")

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

    @staticmethod
    def _is_api_related_message(text_upper: str) -> bool:
        """Detect broker/API notices so toasts use API-specific titles."""
        api_tokens = (
            " API",
            "BROKER",
            "RATE LIMIT",
            "AUTH",
            "TOKEN",
            "SESSION",
            "WEBSOCKET",
            "CONNECTION",
            "CONNECTIVITY",
        )
        return any(token in text_upper for token in api_tokens)

    @staticmethod
    def _resolve_border_color(sub_kind: str | None) -> str | None:
        border_by_sub_kind = {
            "rejected": "#e05555",     # red
            "filled": "#4ec994",       # teal
            "network": "#d4a84b",      # amber
            "api": "#57a5ff",          # blue
            "rate_limit": "#b081ff",   # purple
            "partial_fill": "#f59e0b", # amber
        }
        if not sub_kind:
            return None
        return border_by_sub_kind.get(str(sub_kind).lower())

    def _post(
        self,
        title: str,
        message: str,
        kind: str,
        ttl: int,
        sub_kind: str | None = None,
    ) -> None:
        """Creates and displays the floating popup."""
        try:
            adaptive_ttl = self._resolve_toast_ttl(message, kind, ttl)
            border_color = self._resolve_border_color(sub_kind)
            toast = ToastNotification(
                title,
                message,
                kind,
                adaptive_ttl,
                border_color=border_color,
            )
            toast.show_toast()
        except Exception as e:
            logger.error(f"Failed to show popup: {e}")

    @staticmethod
    def _resolve_toast_ttl(message: str, kind: str, fallback_ttl: int) -> int:
        text = (message or "").strip()
        msg_len = len(text)

        if kind == "error" and msg_len > 150:
            return 8000
        if msg_len < 60:
            return 3000
        if msg_len <= 150:
            return 5000
        return 8000 if kind == "error" else max(fallback_ttl, 5000)

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