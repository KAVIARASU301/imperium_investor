import logging
from typing import List, Dict, Any, Union

from PySide6.QtWidgets import (
    QToolBar, QWidget, QLabel, QSizePolicy, QPushButton,
    QHBoxLayout
)
from PySide6.QtCore import Signal, Qt, QThreadPool, QTimer, Slot, QSize
from PySide6.QtGui import QIcon
from kiteconnect import KiteConnect

from app_paths import get_asset_path
from kite.utils.worker import Worker
from kite.widgets.search_bar import EnhancedSearchInput, SymbolIndex
logger = logging.getLogger(__name__)
DEFAULT_PAPER_BALANCE = 1_000_000.0


def _extract_available_balance_from_data(trader: Any, profile: Dict[str, Any], margins: Dict[str, Any]) -> float:
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
    """Sharp, layout-friendly alert count badge."""

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
        """Backward compatible alias."""
        self.update_count(count)


class HeaderToolbar(QToolBar):
    """
    Compact, modern toolbar.

    Signals
    ───────
    symbol_selected(str)           — a valid symbol was committed by the user
    buy_order_requested(str)
    sell_order_requested(str)
    alert_manager_requested()
    order_history_requested()
    pending_orders_requested()
    performance_dashboard_requested()
    color_settings_requested()
    """

    symbol_selected                  = Signal(str)
    add_alert_requested              = Signal()
    alert_manager_requested          = Signal()
    order_history_requested          = Signal()
    pending_orders_requested         = Signal()
    performance_dashboard_requested  = Signal()
    market_depth_requested           = Signal(str)
    timeframe_changed                = Signal(str)
    buy_order_requested              = Signal(str)
    sell_order_requested             = Signal(str)
    color_settings_requested         = Signal()
    positions_requested              = Signal()
    stock_info_requested             = Signal(str)
    account_refresh_requested        = Signal()

    def __init__(self, trader: Union[KiteConnect, Any], parent=None, enable_account_polling: bool = True):
        super().__init__(parent)
        self.setMovable(False)
        self.setObjectName("enhancedHeaderToolbar")
        self.trader = trader
        self._instrument_map: Dict[str, Dict] = {}
        self._recent_symbols: List[str] = []
        self._account_info = {"available_balance": DEFAULT_PAPER_BALANCE, "user_id": "N/A"}
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
        symbol_label = QLabel("SYMBOL:")
        symbol_label.setObjectName("symbolLabel")
        self.addWidget(symbol_label)

        self.search_input = EnhancedSearchInput()
        self.search_input.setPlaceholderText("Symbol / company…")
        self.search_input.setObjectName("enhancedSymbolSearch")

        # ── Wire the NEW fast signals ──────────────────────────────────────
        self.search_input.symbol_selected.connect(self._on_symbol_committed)

        # Backward-compat: some callers may still listen to debouncedTextChanged
        # (e.g. alert dialogs) — keep it connected but don't drive search from it
        # (the new index handles search internally).

        self.addWidget(self.search_input)

        self.buy_button = QPushButton()
        self.buy_button.setObjectName("buyButton")
        self.buy_button.setFixedSize(24, 22)
        self.buy_button.setIconSize(QSize(10, 10))
        buy_icon_path = get_asset_path("icons", "plus.svg", required=True)
        if buy_icon_path is not None:
            self.buy_button.setIcon(QIcon(str(buy_icon_path)))
        self.buy_button.clicked.connect(self._on_buy_clicked)
        self.addWidget(self.buy_button)

        self.sell_button = QPushButton()
        self.sell_button.setObjectName("sellButton")
        self.sell_button.setFixedSize(24, 22)
        self.sell_button.setIconSize(QSize(10, 10))
        sell_icon_path = get_asset_path("icons", "minus.svg", required=True)
        if sell_icon_path is not None:
            self.sell_button.setIcon(QIcon(str(sell_icon_path)))
        self.sell_button.clicked.connect(self._on_sell_clicked)
        self.addWidget(self.sell_button)

        self._add_section_gap(4)

        self.info_button = QPushButton()
        self.info_button.setObjectName("infoActionButton")
        self.info_button.setFixedSize(24, 22)
        self.info_button.setIconSize(QSize(12, 12))
        info_icon_path = get_asset_path("icons", "info.svg", required=True)
        if info_icon_path is not None:
            self.info_button.setIcon(QIcon(str(info_icon_path)))
        self.info_button.clicked.connect(self._on_info_clicked)
        self.addWidget(self.info_button)

        self.positions_button = QPushButton()
        self.positions_button.setObjectName("positionsActionButton")
        self.positions_button.setFixedSize(24, 22)
        self.positions_button.setIconSize(QSize(12, 12))
        positions_icon_path = get_asset_path("icons", "portfolio.svg", required=True)
        if positions_icon_path is not None:
            self.positions_button.setIcon(QIcon(str(positions_icon_path)))
        self.positions_button.clicked.connect(self.positions_requested.emit)
        self.addWidget(self.positions_button)

    def _create_center_spacer(self):
        spacer = QWidget()
        spacer.setObjectName("centerSpacer")
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer)

    def _create_alert_section(self):
        self._add_section_gap()

        alert_widget = QWidget()
        alert_widget.setObjectName("alertActionWidget")
        alert_layout = QHBoxLayout(alert_widget)
        alert_layout.setContentsMargins(0, 0, 0, 0)
        alert_layout.setSpacing(2)

        self.alerts_button = QPushButton(" ALERTS")
        self.alerts_button.setObjectName("alertActionButton")
        self.alerts_button.setIconSize(QSize(14, 14))
        alert_icon_path = get_asset_path("icons", "alert.svg", required=True)
        if alert_icon_path is not None:
            self.alerts_button.setIcon(QIcon(str(alert_icon_path)))
        self.alerts_button.clicked.connect(self.alert_manager_requested.emit)
        self.alerts_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.alerts_button.setFixedSize(54, 22)
        alert_layout.addWidget(self.alerts_button)

        self.alerts_badge = NotificationBadge()
        alert_layout.addWidget(self.alerts_badge)

        self.addWidget(alert_widget)

    def _create_trading_actions_section(self):
        self._add_section_gap()

        actions_widget = QWidget()
        actions_widget.setObjectName("tradingActionWidget")
        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(4, 2, 4, 2)
        actions_layout.setSpacing(2)

        self.order_history_btn = QPushButton("Order History")
        self.order_history_btn.setObjectName("tradingActionButton")
        self.order_history_btn.clicked.connect(self.order_history_requested.emit)
        self.order_history_btn.setFixedHeight(22)
        actions_layout.addWidget(self.order_history_btn)

        self.pending_orders_btn = QPushButton("Pending")
        self.pending_orders_btn.setObjectName("tradingActionButton")
        self.pending_orders_btn.clicked.connect(self.pending_orders_requested.emit)
        self.pending_orders_btn.setFixedHeight(22)
        actions_layout.addWidget(self.pending_orders_btn)

        self.performance_btn = QPushButton("Performance")
        self.performance_btn.setObjectName("tradingActionButton")
        self.performance_btn.clicked.connect(self.performance_dashboard_requested.emit)
        self.performance_btn.setFixedHeight(22)
        actions_layout.addWidget(self.performance_btn)

        self.color_settings_btn = QPushButton("Settings")
        self.color_settings_btn.setObjectName("tradingActionButton")
        self.color_settings_btn.clicked.connect(self.color_settings_requested.emit)
        self.color_settings_btn.setFixedHeight(22)
        actions_layout.addWidget(self.color_settings_btn)

        self.addWidget(actions_widget)

    def _create_account_section(self):
        self._add_section_gap()

        self.account_info_widget = QWidget()
        self.account_info_widget.setObjectName("accountInfoWidget")
        account_layout = QHBoxLayout(self.account_info_widget)
        account_layout.setContentsMargins(6, 1, 6, 1)
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
        logger.info(f"Search index ready: {len(self._instrument_map)} instruments")

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
            self.performance_btn.setStyleSheet(
                self.performance_btn.styleSheet() + "border-left:3px solid #00b894;"
            )
        elif daily_pnl < 0:
            self.performance_btn.setStyleSheet(
                self.performance_btn.styleSheet() + "border-left:3px solid #d63031;"
            )

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
        self._account_info = account_info or {"user_id": "DEMO", "available_balance": DEFAULT_PAPER_BALANCE}
        self._update_account_display()

    @Slot(tuple)
    def _handle_account_info_error(self, _error: tuple) -> None:
        self._account_info = {"user_id": "DEMO", "available_balance": DEFAULT_PAPER_BALANCE}
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
        self.setStyleSheet("""
            QToolBar#enhancedHeaderToolbar {
                background-color: #0a0d12;
                border-bottom: 1px solid #1a2030;
                padding: 2px 6px;
                spacing: 6px;
                min-height: 32px;
                max-height: 34px;
            }
            #centerSpacer { background-color: transparent; }
            #symbolLabel {
                background-color: #0a0d12; color: #e8f0ff;
                font-size: 11px; font-weight: 900;
                text-transform: uppercase; letter-spacing: 1px;
                padding-right: 6px;
            }
            #enhancedSymbolSearch {
                background-color: #0f1318;
                border: 1px solid #1a2030; color: #e8f0ff;
                padding: 3px 8px; border-radius: 0px;
                font-size: 11px; font-weight: 500;
                min-width: 84px; max-width: 100px; max-height: 22px;
            }
            #enhancedSymbolSearch:focus {
                border: 1px solid #1a2030;
                color: #e8f0ff;
            }
            #buyButton {
                background-color: #0f1318;
                color: #00d4a8;
                border: 1px solid #1a2030;
                padding: 3px 8px;
                border-radius: 0px;
                font-size: 9px;
                font-weight: 700;
            }
            #buyButton:hover {
                background-color: #141920;
                border: 1px solid #22c4a0;
                color: #22c4a0;
            }
            #sellButton {
                background-color: #0f1318;
                color: #ff4d6a;
                border: 1px solid #1a2030;
                padding: 3px 8px;
                border-radius: 0px;
                font-size: 9px;
                font-weight: 700;
            }
            #sellButton:hover {
                background-color: #141920;
                border: 1px solid #ff4d6a;
                color: #ff6b82;
            }
            #sectionGap { background: transparent; }
            #alertActionWidget {
                background-color: transparent;
                border: none;
            }
            #alertActionButton {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(255, 173, 51, 0.14), stop:1 rgba(255, 92, 92, 0.10));
                color: #ffdca8;
                border: 1px solid #7a4d1a;
                border-radius: 3px;
                padding: 2px 8px;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.6px;
                text-align: left;
            }
            #alertActionButton:hover {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(255, 189, 89, 0.22), stop:1 rgba(255, 108, 108, 0.18));
                color: #fff2dd;
                border: 1px solid #a86923;
            }
            #alertActionButton:pressed {
                background-color: rgba(255, 154, 61, 0.20);
                border: 1px solid #c67d2b;
            }
            #notificationBadge {
                background-color: #E53935;
                border: none;
                color: #FFFFFF;
                border-radius: 2px;
                font-size: 10px;
                font-weight: 700;
                font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
            }
            #tradingActionWidget {
                background-color: rgba(255, 255, 255, 0.02);
                border: 1px solid #222630;
                border-radius: 4px;
            }
            #tradingActionButton {
                background-color: rgba(0, 212, 255, 0.05);
                color: #7b8496;
                border: 1px solid transparent;
                padding: 3px 8px;
                border-radius: 2px;
                font-size: 10px;
                font-weight: 600;
            }
            #tradingActionButton:hover {
                background-color: rgba(0, 212, 255, 0.15);
                border: 1px solid rgba(0, 212, 255, 0.35);
                color: #b7f4ff;
            }
            #infoActionButton {
                background-color: rgba(0, 212, 255, 0.05);
                border: 1px solid transparent;
                border-radius: 0px;
                padding: 0px;
            }
            #infoActionButton:hover {
                background-color: rgba(255, 215, 0, 0.16);
                border: 1px solid #f1c40f;
                border-radius: 0px;
            }
            #positionsActionButton {
                background-color: rgba(0, 212, 255, 0.05);
                border: 1px solid transparent;
                border-radius: 0px;
                padding: 0px;
            }
            #positionsActionButton:hover {
                background-color: rgba(139, 94, 60, 0.18);
                border: 1px solid #8b5e3c;
                border-radius: 0px;
            }
            #accountInfoWidget {
                background-color: rgba(255, 255, 255, 0.03);
                border: none;
                border-radius: 4px;
                padding: 2px 6px;
            }
            #userIdLabel {
                background-color: rgba(0, 212, 255, 0.10);
                color: #7ee9ff;
                border: none;
                padding: 3px 8px;
                border-radius: 4px;
                font-size: 9px;
                font-weight: 600;
                letter-spacing: 0.4px;
            }
            #balanceLabel {
                background-color: rgba(0, 255, 170, 0.10);
                color: #76ffcd;
                border: none;
                padding: 3px 9px;
                border-radius: 4px;
                font-size: 10px;
                font-weight: 800;
                font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
                letter-spacing: 0.6px;
            }
            #separatorDot { background-color:transparent; color:#666666; font-size:8px; }
        """)

    def closeEvent(self, event):
        if hasattr(self, "account_timer"):
            self.account_timer.stop()
        if hasattr(self, "_account_polling_thread"):
            self._account_polling_thread.quit()
            self._account_polling_thread.wait(2000)
        super().closeEvent(event)
