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

    Design intent:
    - Acts as a hard visual end-cap for the application.
    - Keeps only persistent system vitals in the bar.
    - Leaves transient text/messages to toast notifications.
    """

    HEIGHT = 24
    EDGE_HEIGHT = 1
    CONTENT_HEIGHT = HEIGHT - EDGE_HEIGHT

    # AMOLED dark terminal tokens — matched with scanner/watchlist/positions.
    COLOR_BG_OUTER = "#050709"
    COLOR_BG_CONTENT = "#070A0F"
    COLOR_TOP_EDGE = "#1A2030"
    COLOR_BOTTOM_EDGE = "#050709"
    COLOR_SEPARATOR = "#1A2030"
    COLOR_TEXT_MUTED = "#5A7090"
    COLOR_TEXT_STRONG = "#A8BCD4"
    COLOR_GREEN = "#00D4A8"
    COLOR_RED = "#FF4D6A"
    COLOR_AMBER = "#F59E0B"
    COLOR_CLOSED = "#2A3A50"
    COLOR_BLUE = "#00D4FF"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("bottomStatusBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
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
        self.top_edge.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.content = QWidget(self)
        self.content.setObjectName("statusContent")
        self.content.setAttribute(Qt.WA_StyledBackground, True)
        self.content.setFixedHeight(self.CONTENT_HEIGHT)
        self.content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QHBoxLayout(self.content)
        self._layout = layout
        layout.setContentsMargins(8, 0, 8, 1)
        layout.setSpacing(8)

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
        self.group_separator.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        min_widths = {
            self.market_label: 82,
            self.api_label: 44,
            self.open_pnl_label: 116,
            self.exposure_label: 112,
        }

        for label in min_widths:
            label.setObjectName("statusLabel")
            label.setTextFormat(Qt.RichText)
            label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            label.setMinimumHeight(self.CONTENT_HEIGHT - 2)
            label.setMinimumWidth(min_widths[label])
            label.setContentsMargins(0, 0, 0, 0)
            label.setTextInteractionFlags(Qt.NoTextInteraction)

        self._rebuild_layout()

    def set_elements_alignment(self, alignment: str) -> None:
        desired = "right" if str(alignment).lower() == "right" else "left"
        if desired == self._status_alignment and self._layout:
            return
        self._status_alignment = desired
        self._rebuild_layout()

    def set_metrics_alignment(self, on_right: bool) -> None:
        desired = bool(on_right)
        if desired == self._metrics_on_right and self._layout:
            return
        self._metrics_on_right = desired
        self._rebuild_layout()

    @staticmethod
    def _set_label_text(label: QLabel, text: str) -> None:
        """Avoid unnecessary QLabel repaints when repeated status events arrive."""
        if label.text() != text:
            label.setText(text)

    @staticmethod
    def _format_number(value: float) -> str:
        try:
            return f"{float(value):,.0f}"
        except (TypeError, ValueError):
            return "--"

    @staticmethod
    def _is_non_negative(value: float) -> bool:
        try:
            return float(value or 0.0) >= 0
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _normalize_status(text: str) -> str:
        return str(text or "--").strip().upper() or "--"

    def _clear_layout(self) -> None:
        if not self._layout:
            return
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(self.content)
            del item

    def _add_status_widgets(self, widgets: tuple[QWidget, ...]) -> None:
        if not self._layout:
            return
        for widget in widgets:
            self._layout.addWidget(widget, 0, Qt.AlignVCenter)

    def _rebuild_layout(self) -> None:
        if not self._layout:
            return

        self.content.setUpdatesEnabled(False)
        try:
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
            self._layout.addWidget(self.group_separator, 0, Qt.AlignVCenter)
            self._add_status_widgets(right_group)
        finally:
            self.content.setUpdatesEnabled(True)

    def set_positions_metrics(self, has_data: bool, open_pnl: float = 0.0, exposure: float = 0.0) -> None:
        if not has_data:
            self._set_label_text(self.open_pnl_label, "OPEN P&L: --")
            self._set_label_text(self.exposure_label, "EXPOSURE: --")
            return

        pnl_value = self._format_number(open_pnl)
        exposure_value = self._format_number(exposure)
        pnl_positive = self._is_non_negative(open_pnl)
        pnl_color = self.COLOR_GREEN if pnl_positive else self.COLOR_RED
        sign = "+" if pnl_positive else ""

        self._set_label_text(
            self.open_pnl_label,
            f'OPEN P&L: <span style="color:{pnl_color}; font-weight:700;">{sign}{pnl_value}</span>',
        )
        self._set_label_text(
            self.exposure_label,
            f'EXPOSURE: <span style="color:{self.COLOR_TEXT_STRONG}; font-weight:650;">{exposure_value}</span>',
        )

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            f"""
            QWidget#bottomStatusBar {{
                background-color: {self.COLOR_BG_OUTER};
                border: none;
                border-radius: 0px;
            }}

            QFrame#statusTopEdge {{
                background-color: {self.COLOR_TOP_EDGE};
                border: none;
            }}

            QWidget#statusContent {{
                background-color: {self.COLOR_BG_CONTENT};
                border: none;
                border-top: 1px solid rgba(26, 32, 48, 0.80);
                border-bottom: 1px solid {self.COLOR_BOTTOM_EDGE};
                border-radius: 0px;
            }}

            QFrame#statusGroupSeparator {{
                background-color: {self.COLOR_SEPARATOR};
                border: none;
            }}

            QLabel#statusLabel {{
                background: transparent;
                border: none;
                color: {self.COLOR_TEXT_MUTED};
                font-family: "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", "Noto Sans", sans-serif;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 0.45px;
                padding: 0px;
                margin: 0px;
            }}
            """
        )

    def set_market_status(self, text: str) -> None:
        status = self._normalize_status(text)
        market_color_map = {
            "OPEN": self.COLOR_GREEN,
            "LIVE": self.COLOR_GREEN,
            "CLOSED": self.COLOR_CLOSED,
            "PREOPEN": self.COLOR_AMBER,
            "PRE-OPEN": self.COLOR_AMBER,
            "HALTED": self.COLOR_RED,
        }
        color = market_color_map.get(status, self.COLOR_TEXT_MUTED)
        self._set_label_text(
            self.market_label,
            f'MARKET: <span style="color:{color}; font-weight:700;">{status}</span>',
        )

    def set_api_status(self, text: str) -> None:
        status = self._normalize_status(text)
        dot_color_map = {
            "CONNECTED": self.COLOR_GREEN,
            "ONLINE": self.COLOR_GREEN,
            "ERROR": self.COLOR_RED,
            "FAILED": self.COLOR_RED,
            "DISCONNECTED": self.COLOR_AMBER,
            "RECONNECTING": self.COLOR_AMBER,
            "CONNECTING": self.COLOR_BLUE,
        }
        dot_color = dot_color_map.get(status, "#6f7a8c")
        self._set_label_text(
            self.api_label,
            f'API <span style="color:{dot_color}; font-size:10px;">●</span>',
        )

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

        normalized_event = str(event or "").strip().lower()
        symbol = str(symbol or "").strip().upper()
        detail = str(detail or "").strip()

        if normalized_event == "submitted":
            play_order_placed()
            return
        if normalized_event == "filled":
            self._post("FILLED", f"{symbol} {detail}".strip(), "success", 3000, sub_kind="filled")
            play_entry_exit()
            return
        if normalized_event == "rejected":
            self._post("REJECTED", f"{symbol} — {detail}".strip(" —"), "error", 4000, sub_kind="rejected")
            play_error()
            return
        if normalized_event in {"cancelled", "canceled"}:
            self._post("CANCELED", symbol or "UNKNOWN", "warn", 3000)
            return
        if normalized_event == "partial":
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

    @staticmethod
    def _safe_int(value) -> int:
        try:
            return int(float(value or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _format_order_price(value) -> str:
        if value in (None, "", "MKT"):
            return "MKT"
        try:
            number = float(value)
            return f"{number:.2f}" if number % 1 else f"{number:.0f}"
        except (TypeError, ValueError):
            return str(value)

    def show_order_update(self, order_dict: dict) -> None:
        """
        Gatekeeper for order notifications.
        Emits only terminal/actionable updates in concise trading lexicon.
        """
        raw_status = str(order_dict.get("status", "")).upper().strip()
        symbol = str(order_dict.get("tradingsymbol") or "UNKNOWN").upper()
        qty = self._safe_int(order_dict.get("filled_quantity") or order_dict.get("quantity") or 0)
        price = self._format_order_price(order_dict.get("average_price") or order_dict.get("price") or "MKT")
        side = str(order_dict.get("transaction_type") or "BUY").upper()

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
        text = str(message or "").upper()
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
        kind = "success" if str(status_text or "").upper() == "CONNECTED" else "warn"
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
        normalized_level = str(level or "info").lower()
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
        display_message = self._translate_message(message) if kind == "error" else str(message or "")

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
        kind = level_map.get(str(level or "info").lower(), "info")
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
            "API ",
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
                str(title or "Notification"),
                str(message or ""),
                kind,
                adaptive_ttl,
                border_color=border_color,
            )
            toast.show_toast()
        except Exception as e:
            logger.error(f"Failed to show popup: {e}")

    @staticmethod
    def _resolve_toast_ttl(message: str, kind: str, fallback_ttl: int) -> int:
        text = str(message or "").strip()
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
        cleaned = re.sub(r"\s+", " ", str(message or "").strip())
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