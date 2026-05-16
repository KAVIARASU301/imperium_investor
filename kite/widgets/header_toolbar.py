"""Production-grade dark trading terminal header toolbar.

Institutional Dark Trading Terminal UI with modern UI typography for all visible
text and numbers. Monospace is reserved only for raw logs / debug text.
"""

import logging
from typing import Any, Dict, List, Union

from PySide6.QtCore import QSize, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolBar,
    QWidget,
)
from kiteconnect import KiteConnect

from app_paths import get_asset_path
from kite.utils.worker import Worker
from kite.widgets.search_bar import EnhancedSearchInput, SymbolIndex

logger = logging.getLogger(__name__)
DEFAULT_PAPER_BALANCE = 1_000_000.0

# ── Institutional Dark Trading Terminal palette ──────────────────────────────
_BG_APP = "#050709"
_BG_WINDOW = "#0a0d12"
_BG_PANEL = "#0f1318"
_BG_SECTION = "#141920"
_BG_BORDER = "#1a2030"
_BG_BORDER_HI = "#26354a"

_BULL = "#00d4a8"
_BEAR = "#ff4d6a"
_AMBER = "#f59e0b"
_CYAN = "#00d4ff"
_BLUE = "#3b82f6"

_TEXT = "#e8f0ff"
_TEXT_SYMBOL = "#b6c4d6"      # softer active symbol/account text
_TEXT_SOFT = "#a8bcd4"
_TEXT_MUTED = "#5a7090"
_TEXT_FAINT = "#2a3a50"
_SELECTION = "#1a2840"

_MONO = "'Consolas', 'JetBrains Mono', monospace"  # raw logs/debug only
_SANS = "'Inter', 'Segoe UI', sans-serif"
_NUM = "'Inter', 'Segoe UI Variable', 'Segoe UI', sans-serif"
_NUM_FONT = "Inter"

_TOOLBAR_H = 34
_CONTROL_H = 24
_ICON_BTN_W = 26


# ── Data helpers ─────────────────────────────────────────────────────────────

def _extract_available_balance_from_data(
    trader: Any,
    profile: Dict[str, Any],
    margins: Dict[str, Any],
) -> float:
    equity = margins.get("equity", {})
    available = equity.get("available", {})
    for val in [
        available.get("live_balance"),
        available.get("cash"),
        equity.get("net"),
        profile.get("current_balance"),
        profile.get("balance"),
        getattr(trader, "balance", None),
        getattr(trader, "initial_balance", DEFAULT_PAPER_BALANCE),
    ]:
        try:
            if val is not None:
                return float(val)
        except (TypeError, ValueError):
            pass
    return DEFAULT_PAPER_BALANCE


class NotificationBadge(QLabel):
    """Sharp, compact alert count badge."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.count = 0
        self.setFixedSize(18, 18)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setObjectName("notificationBadge")
        self.setContentsMargins(0, 0, 0, 0)
        self.hide()

    def update_count(self, count: int):
        self.count = count
        if count > 0:
            self.setText(str(count) if count < 100 else "99+")
            self.show()
        else:
            self.hide()

    def set_count(self, count: int):
        """Backward-compatible alias."""
        self.update_count(count)


class HeaderToolbar(QToolBar):
    """
    Compact institutional-style toolbar for the trading terminal.

    Public API, signals, slots, manager calls, and account polling behavior are
    intentionally preserved for compatibility with the rest of the app.
    """

    symbol_selected = Signal(str)
    add_alert_requested = Signal()
    alert_manager_requested = Signal()
    order_history_requested = Signal()
    pending_orders_requested = Signal()
    performance_dashboard_requested = Signal()
    market_depth_requested = Signal(str)
    timeframe_changed = Signal(str)
    buy_order_requested = Signal(str)
    sell_order_requested = Signal(str)
    color_settings_requested = Signal()
    positions_requested = Signal()
    stock_info_requested = Signal(str)
    account_refresh_requested = Signal()

    def __init__(
        self,
        trader: Union[KiteConnect, Any],
        parent=None,
        enable_account_polling: bool = True,
    ):
        super().__init__(parent)
        self.setMovable(False)
        self.setFloatable(False)
        self.setIconSize(QSize(14, 14))
        self.setObjectName("enhancedHeaderToolbar")

        self.trader = trader
        self._instrument_map: Dict[str, Dict] = {}
        self._recent_symbols: List[str] = []
        self._account_info = {
            "available_balance": DEFAULT_PAPER_BALANCE,
            "user_id": "N/A",
        }
        self._show_account_name = True
        self._show_account_balance = True
        self._symbol_index = SymbolIndex()
        self.threadpool = QThreadPool()
        self._enable_account_polling = bool(enable_account_polling)

        self._init_ui()
        self._apply_styles()
        if self._enable_account_polling:
            self._setup_timers()

    # ── UI construction ───────────────────────────────────────────────────────

    def _init_ui(self):
        self._create_symbol_search_section()
        self._create_center_spacer()
        self._create_alert_section()
        self._create_trading_actions_section()
        self._create_account_section()

    def _create_symbol_search_section(self):
        search_group = QWidget()
        search_group.setObjectName("symbolSearchGroup")
        search_layout = QHBoxLayout(search_group)
        search_layout.setContentsMargins(6, 2, 6, 2)
        search_layout.setSpacing(5)

        symbol_label = QLabel("SYMBOL")
        symbol_label.setObjectName("symbolLabel")
        search_layout.addWidget(symbol_label)

        self.search_input = EnhancedSearchInput()
        self.search_input.setPlaceholderText("Symbol / company…")
        self.search_input.setObjectName("enhancedSymbolSearch")
        self.search_input.setFixedHeight(_CONTROL_H)
        self.search_input.setMinimumWidth(126)
        self.search_input.setMaximumWidth(176)

        # Fast symbol commit path used by the app search/index system.
        self.search_input.symbol_selected.connect(self._on_symbol_committed)
        search_layout.addWidget(self.search_input)

        self.buy_button = self._make_icon_button(
            object_name="buyButton",
            icon_name="plus.svg",
            required=True,
            tooltip="Buy selected symbol",
        )
        self.buy_button.clicked.connect(self._on_buy_clicked)
        search_layout.addWidget(self.buy_button)

        self.sell_button = self._make_icon_button(
            object_name="sellButton",
            icon_name="minus.svg",
            required=True,
            tooltip="Sell selected symbol",
        )
        self.sell_button.clicked.connect(self._on_sell_clicked)
        search_layout.addWidget(self.sell_button)

        self.info_button = self._make_icon_button(
            object_name="infoActionButton",
            icon_name="info.svg",
            required=True,
            tooltip="Open stock information",
        )
        self.info_button.clicked.connect(self._on_info_clicked)
        search_layout.addWidget(self.info_button)

        self.positions_button = self._make_icon_button(
            object_name="positionsActionButton",
            icon_name="portfolio.svg",
            required=True,
            tooltip="Open positions",
        )
        self.positions_button.clicked.connect(self.positions_requested.emit)
        search_layout.addWidget(self.positions_button)

        self.addWidget(search_group)

    def _create_center_spacer(self):
        spacer = QWidget()
        spacer.setObjectName("centerSpacer")
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer)

    def _create_alert_section(self):
        self._add_section_gap(8)

        alert_widget = QWidget()
        alert_widget.setObjectName("alertActionWidget")
        alert_layout = QHBoxLayout(alert_widget)
        alert_layout.setContentsMargins(4, 2, 4, 2)
        alert_layout.setSpacing(3)

        self.alerts_button = self._make_icon_button(
            object_name="alertActionButton",
            icon_name="alert.svg",
            required=True,
            tooltip="Open alert manager",
        )
        self.alerts_button.clicked.connect(self.alert_manager_requested.emit)
        alert_layout.addWidget(self.alerts_button)

        self.alerts_badge = NotificationBadge()
        alert_layout.addWidget(self.alerts_badge)

        self.addWidget(alert_widget)

    def _create_trading_actions_section(self):
        self._add_section_gap(8)

        actions_widget = QWidget()
        actions_widget.setObjectName("tradingActionWidget")
        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(5, 2, 5, 2)
        actions_layout.setSpacing(3)

        self.order_history_btn = self._make_text_button("Order History")
        self.order_history_btn.clicked.connect(self.order_history_requested.emit)
        actions_layout.addWidget(self.order_history_btn)

        self.pending_orders_btn = self._make_text_button("Pending")
        self.pending_orders_btn.clicked.connect(self.pending_orders_requested.emit)
        actions_layout.addWidget(self.pending_orders_btn)

        self.performance_btn = self._make_text_button("Performance")
        self.performance_btn.setProperty("pnlState", "flat")
        self.performance_btn.clicked.connect(self.performance_dashboard_requested.emit)
        actions_layout.addWidget(self.performance_btn)

        self.color_settings_btn = self._make_text_button("Settings")
        self.color_settings_btn.clicked.connect(self.color_settings_requested.emit)
        actions_layout.addWidget(self.color_settings_btn)

        self.addWidget(actions_widget)

    def _create_account_section(self):
        self._add_section_gap(8)

        self.account_info_widget = QWidget()
        self.account_info_widget.setObjectName("accountInfoWidget")
        account_layout = QHBoxLayout(self.account_info_widget)
        account_layout.setContentsMargins(6, 2, 6, 2)
        account_layout.setSpacing(6)

        self.profile_avatar_label = QLabel()
        self.profile_avatar_label.setObjectName("profileAvatarLabel")
        self.profile_avatar_label.setFixedSize(14, 14)
        avatar_icon_path = get_asset_path("icons", "profile_avatar.svg", required=False)
        if avatar_icon_path is not None:
            self.profile_avatar_label.setPixmap(QIcon(str(avatar_icon_path)).pixmap(14, 14))
        account_layout.addWidget(self.profile_avatar_label)

        self.user_id_label = QLabel("KE6286")
        self.user_id_label.setObjectName("userIdLabel")
        account_layout.addWidget(self.user_id_label)

        self.account_separator = self._create_separator_dot()
        account_layout.addWidget(self.account_separator)

        self.balance_label = QLabel("₹0")
        self.balance_label.setObjectName("balanceLabel")
        account_layout.addWidget(self.balance_label)

        self.addWidget(self.account_info_widget)

    def _make_icon_button(
        self,
        object_name: str,
        icon_name: str,
        required: bool,
        tooltip: str,
    ) -> QPushButton:
        button = QPushButton()
        button.setObjectName(object_name)
        button.setFixedSize(_ICON_BTN_W, _CONTROL_H)
        button.setIconSize(QSize(12, 12))
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        icon_path = get_asset_path("icons", icon_name, required=required)
        if icon_path is not None:
            button.setIcon(QIcon(str(icon_path)))
        return button

    @staticmethod
    def _make_text_button(text: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("tradingActionButton")
        button.setFixedHeight(_CONTROL_H)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    @staticmethod
    def _create_separator_dot() -> QLabel:
        dot = QLabel("•")
        dot.setObjectName("separatorDot")
        return dot

    def _add_section_gap(self, width: int = 10) -> None:
        gap = QWidget()
        gap.setObjectName("sectionGap")
        gap.setFixedWidth(width)
        self.addWidget(gap)

    # ── Timers ────────────────────────────────────────────────────────────────

    def _setup_timers(self):
        QTimer.singleShot(1000, self._trigger_account_refresh)
        self.account_timer = QTimer(self)
        self.account_timer.timeout.connect(self._trigger_account_refresh)
        self.account_timer.start(30_000)

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _on_symbol_committed(self, symbol: str, inst: Dict) -> None:
        """Called when user selects from dropdown or presses Enter."""
        self._remember_recent_symbol(symbol)
        self.symbol_selected.emit(symbol)

    def _on_buy_clicked(self):
        sym = self.search_input.text().upper().strip()
        if sym and sym in self._instrument_map:
            self.buy_order_requested.emit(sym)

    def _on_sell_clicked(self):
        sym = self.search_input.text().upper().strip()
        if sym and sym in self._instrument_map:
            self.sell_order_requested.emit(sym)

    def _on_info_clicked(self):
        sym = self.search_input.text().upper().strip()
        if sym:
            self.stock_info_requested.emit(sym)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_instrument_data(
        self,
        instruments: List[Dict[str, Any]],
        instrument_map: Dict[str, Dict[str, Any]] | None = None,
        symbol_index: SymbolIndex | None = None,
    ) -> None:
        """Set instrument data, optionally with pre-built map/index from worker thread."""
        self._instrument_map = instrument_map or {
            inst["tradingsymbol"]: inst
            for inst in instruments
            if "tradingsymbol" in inst
        }
        if symbol_index is not None:
            self._symbol_index = symbol_index
        else:
            self._symbol_index.build(instruments)
        self.search_input.set_symbol_index(self._symbol_index)
        logger.info("Search index ready: %s instruments", len(self._instrument_map))

    def update_alert_counts(self, active_count: int, triggered_today: int) -> None:
        self.alerts_badge.update_count(triggered_today)

    def set_current_symbol(self, symbol: str) -> None:
        normalized = symbol.upper().strip()
        self.search_input.setText(normalized)
        self.search_input.set_committed_symbol(normalized)
        self.search_input.arm_replace_on_next_input()

    def get_current_symbol(self) -> str:
        return self.search_input.text().upper().strip()

    def update_balance(self, balance: float) -> None:
        self._account_info["available_balance"] = float(balance)
        if not self._account_info.get("user_id"):
            self._account_info["user_id"] = "DEMO"
        self._update_account_display()

    def apply_color_theme(self, theme: Dict[str, Any]) -> None:
        theme = theme or {}
        self._show_account_name = bool(theme.get("show_account_name", True))
        self._show_account_balance = bool(theme.get("show_account_balance", True))
        self._update_account_display_visibility()

    def update_performance_metrics(self, performance_data: Dict[str, Any]) -> None:
        daily_pnl = performance_data.get("daily_pnl", 0)
        if daily_pnl > 0:
            state = "profit"
        elif daily_pnl < 0:
            state = "loss"
        else:
            state = "flat"
        self.performance_btn.setProperty("pnlState", state)
        self.performance_btn.style().unpolish(self.performance_btn)
        self.performance_btn.style().polish(self.performance_btn)
        self.performance_btn.update()

    def set_watchlist_symbols(self, symbols: List[str]) -> None:
        pass  # index handles this now

    # ── Account helpers ───────────────────────────────────────────────────────

    def _refresh_account_info(self):
        self._trigger_account_refresh()

    def _trigger_account_refresh(self) -> None:
        if not self.trader:
            return
        worker = Worker(self._fetch_account_info_sync)
        worker.signals.result.connect(self._handle_account_info_update)
        worker.signals.error.connect(self._handle_account_info_error)
        self.threadpool.start(worker)

    def _fetch_account_info_sync(self) -> Dict[str, Any]:
        profile = self._get_profile_data()
        margins = self._get_margins_data()
        return {
            "user_id": profile.get("user_id", profile.get("user_name", "DEMO")),
            "available_balance": self._extract_available_balance(profile, margins),
        }

    @Slot(object)
    def _handle_account_info_update(self, account_info: Dict[str, Any]) -> None:
        self._account_info = account_info or {
            "user_id": "DEMO",
            "available_balance": DEFAULT_PAPER_BALANCE,
        }
        self._update_account_display()

    @Slot(tuple)
    def _handle_account_info_error(self, _error: tuple) -> None:
        self._account_info = {
            "user_id": "DEMO",
            "available_balance": DEFAULT_PAPER_BALANCE,
        }
        self._update_account_display()

    def _get_profile_data(self) -> Dict[str, Any]:
        for fn_name in ("profile", "get_profile"):
            fn = getattr(self.trader, fn_name, None)
            if callable(fn):
                try:
                    return fn() or {}
                except Exception as exc:
                    logger.warning("Unable to fetch account profile from broker: %s", exc)
                    return {}
        return {}

    def _get_margins_data(self) -> Dict[str, Any]:
        fn = getattr(self.trader, "margins", None)
        if not callable(fn):
            return {}
        try:
            return fn() or {}
        except Exception as exc:
            logger.warning(
                "Unable to fetch account margins from broker (using cached/default balance): %s",
                exc,
            )
            return {}

    def _extract_available_balance(self, profile, margins) -> float:
        return _extract_available_balance_from_data(self.trader, profile, margins)

    def _update_account_display(self):
        self.user_id_label.setText(self._account_info.get("user_id", "DEMO"))
        balance = self._account_info.get("available_balance", 0.0)
        self.balance_label.setText(self._format_account_balance(balance))
        self._update_account_display_visibility()

    def _update_account_display_visibility(self) -> None:
        show_name = bool(self._show_account_name)
        show_balance = bool(self._show_account_balance)
        self.profile_avatar_label.setVisible(show_name)
        self.user_id_label.setVisible(show_name)
        self.balance_label.setVisible(show_balance)
        self.account_separator.setVisible(show_name and show_balance)
        self.account_info_widget.setVisible(show_name or show_balance)

    @staticmethod
    def _format_account_balance(amount: float) -> str:
        if amount == 0:
            return "₹0"
        neg = amount < 0
        amount = abs(amount)
        s = f"{amount:.0f}"
        if len(s) <= 3:
            fmt = s
        else:
            last3 = s[-3:]
            rest = s[:-3]
            chunks = ""
            for i, d in enumerate(reversed(rest)):
                if i and i % 2 == 0:
                    chunks = "," + chunks
                chunks = d + chunks
            fmt = chunks + "," + last3
        return ("-₹" if neg else "₹") + fmt

    def _remember_recent_symbol(self, symbol: str):
        normalized = symbol.upper().strip()
        updated = [normalized] + [s for s in self._recent_symbols if s != normalized]
        self._recent_symbols = updated[:10]

    # ── Styles ────────────────────────────────────────────────────────────────

    def _apply_styles(self):
        self.setStyleSheet(f"""
        QToolBar#enhancedHeaderToolbar {{
            background-color: {_BG_WINDOW};
            border: none;
            border-bottom: 1px solid {_BG_BORDER};
            padding: 2px 6px;
            spacing: 0px;
            min-height: {_TOOLBAR_H}px;
            max-height: {_TOOLBAR_H}px;
            font-family: {_SANS};
        }}

        QWidget#centerSpacer,
        QWidget#sectionGap {{
            background: transparent;
        }}

        QWidget#symbolSearchGroup,
        QWidget#tradingActionWidget,
        QWidget#accountInfoWidget,
        QWidget#alertActionWidget {{
            background-color: {_BG_PANEL};
            border: 1px solid {_BG_BORDER};
            border-radius: 2px;
        }}

        QLabel#symbolLabel {{
            background: transparent;
            color: {_TEXT_MUTED};
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 900;
            letter-spacing: 1.1px;
            padding: 0 2px 0 0;
        }}

        #enhancedSymbolSearch {{
            background-color: {_BG_WINDOW};
            color: {_TEXT_SYMBOL};
            border: 1px solid {_BG_BORDER_HI};
            border-radius: 2px;
            padding: 2px 8px;
            selection-background-color: {_SELECTION};
            selection-color: {_TEXT};
            font-family: {_SANS};
            font-size: 11px;
            font-weight: 650;
        }}
        #enhancedSymbolSearch:hover {{
            border-color: {_TEXT_FAINT};
            background-color: {_BG_SECTION};
        }}
        #enhancedSymbolSearch:focus {{
            border: 1px solid {_CYAN};
            background-color: {_BG_APP};
            color: {_TEXT_SYMBOL};
        }}

        QPushButton {{
            outline: none;
            border-radius: 2px;
            font-family: {_SANS};
        }}

        QPushButton#buyButton,
        QPushButton#sellButton,
        QPushButton#infoActionButton,
        QPushButton#positionsActionButton,
        QPushButton#alertActionButton {{
            background-color: rgba(255, 255, 255, 0.035);
            border: 1px solid {_BG_BORDER_HI};
            padding: 2px;
        }}

        QPushButton#buyButton {{
            color: {_BULL};
            border-color: rgba(0, 212, 168, 0.38);
        }}
        QPushButton#buyButton:hover {{
            background-color: rgba(0, 212, 168, 0.14);
            border-color: {_BULL};
        }}
        QPushButton#buyButton:pressed {{
            background-color: rgba(0, 212, 168, 0.22);
        }}

        QPushButton#sellButton {{
            color: {_BEAR};
            border-color: rgba(255, 77, 106, 0.38);
        }}
        QPushButton#sellButton:hover {{
            background-color: rgba(255, 77, 106, 0.14);
            border-color: {_BEAR};
        }}
        QPushButton#sellButton:pressed {{
            background-color: rgba(255, 77, 106, 0.22);
        }}

        QPushButton#infoActionButton {{
            color: {_CYAN};
            border-color: rgba(0, 212, 255, 0.34);
        }}
        QPushButton#infoActionButton:hover {{
            background-color: rgba(0, 212, 255, 0.12);
            border-color: {_CYAN};
        }}
        QPushButton#infoActionButton:pressed {{
            background-color: rgba(0, 212, 255, 0.20);
        }}

        QPushButton#positionsActionButton {{
            color: {_BLUE};
            border-color: rgba(59, 130, 246, 0.36);
        }}
        QPushButton#positionsActionButton:hover {{
            background-color: rgba(59, 130, 246, 0.13);
            border-color: {_BLUE};
        }}
        QPushButton#positionsActionButton:pressed {{
            background-color: rgba(59, 130, 246, 0.20);
        }}

        QPushButton#alertActionButton {{
            color: {_AMBER};
            border-color: rgba(245, 158, 11, 0.40);
        }}
        QPushButton#alertActionButton:hover {{
            background-color: rgba(245, 158, 11, 0.14);
            border-color: {_AMBER};
        }}
        QPushButton#alertActionButton:pressed {{
            background-color: rgba(245, 158, 11, 0.22);
        }}

        QLabel#notificationBadge {{
            background-color: {_BEAR};
            color: white;
            border: 1px solid rgba(255, 255, 255, 0.10);
            border-radius: 2px;
            font-family: {_NUM};
            font-size: 9px;
            font-weight: 900;
            padding: 0px;
        }}

        QPushButton#tradingActionButton {{
            background-color: transparent;
            color: {_TEXT_MUTED};
            border: 1px solid transparent;
            border-radius: 2px;
            padding: 2px 8px;
            font-family: {_SANS};
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 0.25px;
        }}
        QPushButton#tradingActionButton:hover {{
            background-color: rgba(0, 212, 255, 0.09);
            border-color: rgba(0, 212, 255, 0.25);
            color: {_TEXT_SOFT};
        }}
        QPushButton#tradingActionButton:pressed {{
            background-color: rgba(0, 212, 255, 0.15);
            color: {_TEXT};
        }}
        QPushButton#tradingActionButton[pnlState="profit"] {{
            color: {_BULL};
            border-left: 2px solid {_BULL};
            padding-left: 7px;
        }}
        QPushButton#tradingActionButton[pnlState="loss"] {{
            color: {_BEAR};
            border-left: 2px solid {_BEAR};
            padding-left: 7px;
        }}
        QPushButton#tradingActionButton[pnlState="flat"] {{
            color: {_TEXT_MUTED};
            border-left: 2px solid transparent;
            padding-left: 7px;
        }}

        QLabel#profileAvatarLabel {{
            background: transparent;
        }}
        QLabel#userIdLabel {{
            background-color: rgba(0, 212, 255, 0.075);
            color: {_CYAN};
            border: 1px solid rgba(0, 212, 255, 0.16);
            border-radius: 2px;
            padding: 2px 7px;
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 800;
            letter-spacing: 0.45px;
        }}
        QLabel#balanceLabel {{
            background-color: rgba(0, 212, 168, 0.075);
            color: {_BULL};
            border: 1px solid rgba(0, 212, 168, 0.16);
            border-radius: 2px;
            padding: 2px 8px;
            font-family: {_NUM};
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 0.15px;
        }}
        QLabel#separatorDot {{
            background: transparent;
            color: {_TEXT_FAINT};
            font-size: 8px;
            font-weight: 900;
        }}

        QToolTip {{
            background-color: {_BG_PANEL};
            color: {_TEXT_SOFT};
            border: 1px solid {_BG_BORDER_HI};
            border-radius: 2px;
            padding: 4px 6px;
            font-family: {_SANS};
            font-size: 10px;
        }}
        """)

    def closeEvent(self, event):
        if hasattr(self, "account_timer"):
            self.account_timer.stop()
        if hasattr(self, "_account_polling_thread"):
            self._account_polling_thread.quit()
            self._account_polling_thread.wait(2000)
        super().closeEvent(event)