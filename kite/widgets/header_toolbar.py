import logging
from typing import List, Dict, Any, Union

from PySide6.QtWidgets import (
    QToolBar, QWidget, QLabel, QSizePolicy, QPushButton,
    QHBoxLayout
)
from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtGui import QPainter, QColor, QFont
from kiteconnect import KiteConnect

from kite.widgets.search_bar import EnhancedSearchInput, SymbolIndex
logger = logging.getLogger(__name__)
DEFAULT_PAPER_BALANCE = 1_000_000.0


class NotificationBadge(QLabel):
    """Animated notification badge for buttons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.count = 0
        self.setFixedSize(15, 15)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setObjectName("notificationBadge")
        self.hide()
        self.setContentsMargins(0, 0, 0, 0)

    def set_count(self, count: int):
        self.count = count
        if count > 0:
            self.setText(str(count) if count < 100 else "99+")
            self.show()
        else:
            self.hide()
        self.update()

    def paintEvent(self, event):
        if not self.isVisible():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#f80404"))
        badge_font = QFont()
        badge_font.setPointSize(8)
        badge_font.setWeight(QFont.Weight.Bold)
        painter.setFont(badge_font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


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

    def __init__(self, trader: Union[KiteConnect, Any], parent=None):
        super().__init__(parent)
        self.setMovable(False)
        self.setObjectName("enhancedHeaderToolbar")
        self.trader = trader
        self._instrument_map: Dict[str, Dict] = {}
        self._recent_symbols: List[str] = []
        self._account_info = {"available_balance": DEFAULT_PAPER_BALANCE, "user_id": "N/A"}
        self._symbol_index = SymbolIndex()

        self._init_ui()
        self._apply_styles()
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

        self.buy_button = QPushButton("BUY")
        self.buy_button.setObjectName("buyButton")
        self.buy_button.setFixedSize(42, 20)
        self.buy_button.clicked.connect(self._on_buy_clicked)
        self.addWidget(self.buy_button)

        self.sell_button = QPushButton("SELL")
        self.sell_button.setObjectName("sellButton")
        self.sell_button.setFixedSize(42, 20)
        self.sell_button.clicked.connect(self._on_sell_clicked)
        self.addWidget(self.sell_button)

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
        alert_layout.setContentsMargins(4, 2, 4, 2)
        alert_layout.setSpacing(2)

        alerts_container = QWidget()
        alerts_container.setFixedSize(64, 20)
        self.alerts_button = QPushButton("Alerts", alerts_container)
        self.alerts_button.setObjectName("alertActionButton")
        self.alerts_button.clicked.connect(self.alert_manager_requested.emit)
        self.alerts_button.setGeometry(0, 0, 64, 20)
        self.alerts_badge = NotificationBadge(alerts_container)
        self.alerts_badge.move(50, -3)
        alert_layout.addWidget(alerts_container)
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
        self.order_history_btn.setFixedSize(84, 20)
        actions_layout.addWidget(self.order_history_btn)

        self.pending_orders_btn = QPushButton("Pending")
        self.pending_orders_btn.setObjectName("tradingActionButton")
        self.pending_orders_btn.clicked.connect(self.pending_orders_requested.emit)
        self.pending_orders_btn.setFixedSize(62, 20)
        actions_layout.addWidget(self.pending_orders_btn)

        self.performance_btn = QPushButton("Performance")
        self.performance_btn.setObjectName("tradingActionButton")
        self.performance_btn.clicked.connect(self.performance_dashboard_requested.emit)
        self.performance_btn.setFixedSize(76, 20)
        actions_layout.addWidget(self.performance_btn)

        self.color_settings_btn = QPushButton("Settings")
        self.color_settings_btn.setObjectName("tradingActionButton")
        self.color_settings_btn.clicked.connect(self.color_settings_requested.emit)
        self.color_settings_btn.setFixedSize(62, 20)
        actions_layout.addWidget(self.color_settings_btn)

        self.addWidget(actions_widget)

    def _create_account_section(self):
        self._add_section_gap()

        self.account_info_widget = QWidget()
        self.account_info_widget.setObjectName("accountInfoWidget")
        account_layout = QHBoxLayout(self.account_info_widget)
        account_layout.setContentsMargins(6, 1, 6, 1)
        account_layout.setSpacing(6)

        self.user_id_label = QLabel("KE6286")
        self.user_id_label.setObjectName("userIdLabel")
        account_layout.addWidget(self.user_id_label)
        account_layout.addWidget(self._create_separator_dot())

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
        QTimer.singleShot(1000, self._refresh_account_info)
        self.account_timer = QTimer(self)
        self.account_timer.timeout.connect(self._refresh_account_info)
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

    # ── Public API ────────────────────────────────────────────────────────────

    def set_instrument_data(self, instruments: List[Dict[str, Any]]) -> None:
        """Build the fast index and update internal map. O(n) once."""
        self._instrument_map = {
            inst["tradingsymbol"]: inst
            for inst in instruments
            if "tradingsymbol" in inst
        }
        # Build index in-place (fast even for 100k instruments)
        self._symbol_index.build(instruments)
        # Hand the index to the search input — it uses it from now on
        self.search_input.set_symbol_index(self._symbol_index)
        logger.info(f"Search index built: {len(self._instrument_map)} instruments")

    def update_alert_counts(self, active_count: int, triggered_today: int) -> None:
        self.alerts_badge.set_count(active_count + triggered_today)

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
        try:
            profile = self._get_profile_data()
            margins = self._get_margins_data()
            self._account_info = {
                "user_id": profile.get("user_id", profile.get("user_name", "DEMO")),
                "available_balance": self._extract_available_balance(profile, margins),
            }
            self._update_account_display()
        except Exception:
            self._account_info = {"user_id": "DEMO", "available_balance": DEFAULT_PAPER_BALANCE}
            self._update_account_display()

    def _get_profile_data(self) -> Dict[str, Any]:
        for fn_name in ("profile", "get_profile"):
            fn = getattr(self.trader, fn_name, None)
            if callable(fn):
                return fn() or {}
        return {}

    def _get_margins_data(self) -> Dict[str, Any]:
        fn = getattr(self.trader, "margins", None)
        return fn() if callable(fn) else {}

    def _extract_available_balance(self, profile, margins) -> float:
        equity = margins.get("equity", {})
        available = equity.get("available", {})
        for val in [
            available.get("live_balance"),
            available.get("cash"),
            equity.get("net"),
            profile.get("current_balance"),
            profile.get("balance"),
            getattr(self.trader, "balance", None),
            getattr(self.trader, "initial_balance", DEFAULT_PAPER_BALANCE),
        ]:
            try:
                if val is not None:
                    return float(val)
            except (TypeError, ValueError):
                pass
        return DEFAULT_PAPER_BALANCE

    def _update_account_display(self):
        self.user_id_label.setText(self._account_info.get("user_id", "DEMO"))
        balance = self._account_info.get("available_balance", 0.0)
        self.balance_label.setText(self._format_indian_currency(balance))

    @staticmethod
    def _format_indian_currency(amount: float) -> str:
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
                background-color: #1a1a1a;
                border-bottom: 2px solid #404040;
                padding: 1px 6px;
                spacing: 6px;
                min-height: 28px;
                max-height: 30px;
            }
            #centerSpacer { background-color: transparent; }
            #symbolLabel {
                background-color: #1a1a1a; color: #ffffff;
                font-size: 11px; font-weight: 900;
                text-transform: uppercase; letter-spacing: 1px;
                padding-right: 6px;
            }
            #enhancedSymbolSearch {
                background-color: #111111;
                border: 1px solid transparent; color: #ffffff;
                padding: 3px 8px; border-radius: 6px;
                font-size: 11px; font-weight: 500;
                min-width: 84px; max-width: 100px; max-height: 20px;
            }
            #enhancedSymbolSearch:focus {
                border: 1px solid #2f2f2f;
                color: #ffffff;
            }
            #buyButton {
                background-color: rgba(74, 255, 74, 0.10);
                color: #4aff4a;
                border: none;
                padding: 3px 8px;
                border-radius: 10px;
                font-size: 9px;
                font-weight: 700;
            }
            #buyButton:hover {
                background-color: rgba(74, 255, 74, 0.18);
                border: 1px solid rgba(74, 255, 74, 0.45);
                color: #7dff7d;
            }
            #sellButton {
                background-color: rgba(255, 68, 68, 0.10);
                color: #ff6666;
                border: none;
                padding: 3px 8px;
                border-radius: 10px;
                font-size: 9px;
                font-weight: 700;
            }
            #sellButton:hover {
                background-color: rgba(255, 68, 68, 0.18);
                border: 1px solid rgba(255, 68, 68, 0.45);
                color: #ff8b8b;
            }
            #sectionGap { background: transparent; }
            #alertActionWidget, #tradingActionWidget {
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid #2f2f2f;
                border-radius: 6px;
            }
            #alertActionButton, #tradingActionButton {
                background-color: rgba(0, 212, 255, 0.10);
                color: #7ee9ff;
                border: none;
                padding: 3px 8px;
                border-radius: 10px;
                font-size: 9px;
                font-weight: 600;
            }
            #alertActionButton:hover, #tradingActionButton:hover {
                background-color: rgba(0, 212, 255, 0.18);
                border: 1px solid rgba(0, 212, 255, 0.45);
                color: #b7f4ff;
            }
            #accountInfoWidget {
                background-color: rgba(255, 255, 255, 0.03); border: 1px solid #333333;
                border-radius: 5px; padding: 2px 6px;
            }
            #userIdLabel { background-color:#1a1a1a; color:#00d4ff; font-size:10px; font-weight:700; }
            #balanceLabel { background-color:#1a1a1a; color:#4aff4a; font-size:10px; font-weight:600; }
            #separatorDot { background-color:transparent; color:#666666; font-size:8px; }
        """)

    def closeEvent(self, event):
        if hasattr(self, "account_timer"):
            self.account_timer.stop()
        super().closeEvent(event)
