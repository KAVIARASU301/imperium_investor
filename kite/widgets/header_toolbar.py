"""Production-grade dark trading terminal header toolbar.

Institutional Dark Trading Terminal UI with modern UI typography for all visible
text and numbers. Monospace is reserved only for raw logs / debug text.
"""

import logging
from typing import Any, Dict, List, Union

from PySide6.QtCore import QSize, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QFont, QFontMetrics, QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolBar,
    QWidget,
    QFrame,
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
_BLUE = "#00d4ff"

_TEXT = "#e8f0ff"
_TEXT_SYMBOL = "#b6c4d6"      # softer active symbol/account text
_TEXT_SOFT = "#a8bcd4"
_TEXT_MUTED = "#5a7090"
_TEXT_FAINT = "#2a3a50"
_SELECTION = "#1a2840"

_MONO = "'Consolas', 'JetBrains Mono', monospace"  # raw logs/debug only
_SANS = "'Inter', 'Segoe UI Variable', 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, Arial, sans-serif"
_NUM = _SANS
_UI_FONT_FAMILY = "Inter"


def _modern_font(point_size: int = 9, weight: QFont.Weight = QFont.Weight.Medium) -> QFont:
    """Return the preferred modern UI font with safe Qt fallback."""
    font = QFont(_UI_FONT_FAMILY)
    font.setPointSize(point_size)
    font.setWeight(weight)
    return font

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
        self._preferred_username = ""
        self._show_ticker_board = True
        self._ticker_symbols: List[str] = ["NIFTY", "BANKNIFTY", "INDIAVIX"]
        self._ticker_alias_map: Dict[str, str] = {
            "NIFTY": "NSE:NIFTY 50",
            "NIFTY50": "NSE:NIFTY 50",
            "BANKNIFTY": "NSE:NIFTY BANK",
            "NIFTYBANK": "NSE:NIFTY BANK",
            "INDIAVIX": "NSE:INDIA VIX",
            "VIX": "NSE:INDIA VIX",
        }
        self._ticker_snapshot: Dict[str, Dict[str, Any]] = {}
        self._ticker_token_to_symbol: Dict[int, str] = {}
        self._symbol_index = SymbolIndex()
        self.threadpool = QThreadPool()
        self._enable_account_polling = bool(enable_account_polling)

        self._init_ui()
        self._apply_styles()
        self._apply_explicit_fonts()
        if self._enable_account_polling:
            self._setup_timers()

    # ── UI construction ───────────────────────────────────────────────────────

    def _init_ui(self):
        self._create_symbol_search_section()
        self._create_center_spacer()
        self._create_ticker_board_section()
        self._create_account_section()

    def _create_symbol_search_section(self):
        search_group = QWidget()
        search_group.setObjectName("symbolSearchGroup")
        search_layout = QHBoxLayout(search_group)
        search_layout.setContentsMargins(6, 2, 6, 2)
        search_layout.setSpacing(5)

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

        self.search_input = EnhancedSearchInput()
        self.search_input.setPlaceholderText("Symbol / company…")
        self.search_input.setObjectName("enhancedSymbolSearch")
        self.search_input.setFixedHeight(_CONTROL_H)
        self.search_input.setFont(_modern_font(10, QFont.Weight.DemiBold))
        self.search_input.setMinimumWidth(126)
        self.search_input.setMaximumWidth(176)

        # Fast symbol commit path used by the app search/index system.
        self.search_input.symbol_selected.connect(self._on_symbol_committed)
        search_layout.addWidget(self.search_input)

        self.positions_button = self._make_icon_button(
            object_name="positionsActionButton",
            icon_name="portfolio.svg",
            required=True,
            tooltip="Open positions",
        )
        self.positions_button.clicked.connect(self.positions_requested.emit)
        search_layout.addWidget(self.positions_button)

        self.alerts_button = self._make_icon_button(
            object_name="alertActionButton",
            icon_name="alert.svg",
            required=True,
            tooltip="Open alert manager",
        )
        self.alerts_button.clicked.connect(self.alert_manager_requested.emit)
        search_layout.addWidget(self.alerts_button)

        self.alerts_badge = NotificationBadge()
        search_layout.addWidget(self.alerts_badge)

        self.addWidget(search_group)

    def _create_center_spacer(self):
        spacer = QWidget()
        spacer.setObjectName("centerSpacer")
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer)


    def _create_ticker_board_section(self):
        self.ticker_board_widget = QFrame()
        self.ticker_board_widget.setObjectName("tickerBoardWidget")
        ticker_layout = QHBoxLayout(self.ticker_board_widget)
        ticker_layout.setContentsMargins(6, 2, 6, 2)
        ticker_layout.setSpacing(4)

        self.ticker_board_label = QLabel("---")
        self.ticker_board_label.setObjectName("tickerBoardText")
        self.ticker_board_label.setFont(_modern_font(8, QFont.Weight.Bold))
        self.ticker_board_label.setTextFormat(Qt.TextFormat.RichText)
        self.ticker_board_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.ticker_board_label.setMinimumWidth(0)
        self.ticker_board_label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        ticker_layout.addWidget(self.ticker_board_label)

        self.addWidget(self.ticker_board_widget)
        self._refresh_ticker_board_display()

    def _create_alert_section(self):
        """Legacy placeholder kept for API compatibility."""
        return

    def _create_trading_actions_section(self):
        """Legacy placeholder kept for API compatibility."""
        return

    def _create_account_section(self):
        self._add_section_gap(8)

        self.account_info_widget = QWidget()
        self.account_info_widget.setObjectName("accountInfoWidget")
        account_layout = QHBoxLayout(self.account_info_widget)
        account_layout.setContentsMargins(6, 2, 6, 2)
        account_layout.setSpacing(6)

        self.order_history_button = self._make_icon_button(
            object_name="orderHistoryActionButton",
            icon_name="order_history.svg",
            required=True,
            tooltip="Open order history",
        )
        self.order_history_button.clicked.connect(self.order_history_requested.emit)
        account_layout.addWidget(self.order_history_button)

        self.pending_orders_button = self._make_icon_button(
            object_name="pendingOrdersActionButton",
            icon_name="pending.svg",
            required=True,
            tooltip="Open pending orders",
        )
        self.pending_orders_button.clicked.connect(self.pending_orders_requested.emit)
        account_layout.addWidget(self.pending_orders_button)

        self.settings_button = self._make_icon_button(
            object_name="settingsActionButton",
            icon_name="gear_setting.svg",
            required=True,
            tooltip="Open settings",
        )
        self.settings_button.clicked.connect(self.color_settings_requested.emit)
        account_layout.addWidget(self.settings_button)

        self.user_id_label = QLabel(self._account_info.get("user_id", "N/A"))
        self.user_id_label.setObjectName("userIdLabel")
        self.user_id_label.setFont(_modern_font(9, QFont.Weight.Bold))
        account_layout.addWidget(self.user_id_label)

        self.account_separator = self._create_separator_dot()
        account_layout.addWidget(self.account_separator)

        self.balance_label = QLabel("₹0")
        self.balance_label.setObjectName("balanceLabel")
        self.balance_label.setFont(_modern_font(10, QFont.Weight.Bold))
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
        button.setFont(_modern_font(9, QFont.Weight.Bold))
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
        button.setFont(_modern_font(9, QFont.Weight.Bold))
        return button

    @staticmethod
    def _create_separator_dot() -> QLabel:
        dot = QLabel("•")
        dot.setObjectName("separatorDot")
        dot.setFont(_modern_font(8, QFont.Weight.Black))
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
        self._preferred_username = str(theme.get("preferred_username", "")).strip()
        self._show_ticker_board = bool(theme.get("show_ticker_board", True))
        raw_tickers = theme.get("ticker_board_symbols", ["NIFTY", "BANKNIFTY", "INDIAVIX"])
        if isinstance(raw_tickers, str):
            raw_tickers = [raw_tickers]
        cleaned = [str(sym).strip().upper() for sym in raw_tickers if str(sym).strip()]
        self._ticker_symbols = cleaned[:5] if cleaned else ["NIFTY", "BANKNIFTY", "INDIAVIX"]
        self._update_account_display()
        self._update_account_display_visibility()
        self._refresh_ticker_board_display()



    def _adjust_ticker_board_width(self, symbols: List[str]) -> None:
        """Dynamically size ticker board to content with per-symbol jitter headroom."""
        if not symbols:
            self.ticker_board_widget.setMinimumWidth(0)
            return

        metrics = QFontMetrics(self.ticker_board_label.font())
        divider = " │ "
        divider_width = metrics.horizontalAdvance(divider)

        extra_digits_headroom = metrics.horizontalAdvance("88")
        per_symbol_padding = 20

        base_width = 0
        for symbol in symbols:
            snap = self._ticker_snapshot.get(symbol.upper(), {})
            if isinstance(snap.get("price"), (int, float)):
                price_text = f"{float(snap['price']):,.2f}"
            else:
                price_text = "--"

            if isinstance(snap.get("change_pct"), (int, float)):
                change_pct = float(snap["change_pct"])
                sign = "+" if change_pct > 0 else ""
                change_text = f"{sign}{change_pct:.2f}%"
            else:
                change_text = "--%"

            segment_text = f"{symbol} {price_text} {change_text}"
            base_width += metrics.horizontalAdvance(segment_text) + per_symbol_padding + extra_digits_headroom

        board_width = base_width + max(0, len(symbols) - 1) * divider_width
        self.ticker_board_widget.setMinimumWidth(max(240, board_width))

    def _refresh_ticker_board_display(self) -> None:
        symbols = self._ticker_symbols[:5]
        if symbols:
            divider = f" <span style='color:{_TEXT_FAINT};'>│</span> "
            joined = divider.join(self._format_ticker_pill(symbol) for symbol in symbols)
            self.ticker_board_label.setText(joined)
        else:
            self.ticker_board_label.setText("---")
        self._adjust_ticker_board_width(symbols)
        self.ticker_board_widget.setVisible(self._show_ticker_board and len(symbols) > 0)

    def _format_ticker_pill(self, symbol: str) -> str:
        snap = self._ticker_snapshot.get(symbol.upper(), {})
        price = snap.get("price")
        chg = snap.get("change_pct")
        if isinstance(price, (int, float)):
            price_text = f"{float(price):,.2f}"
        else:
            price_text = "--"
        if isinstance(chg, (int, float)):
            chg_val = float(chg)
            sign = "+" if chg_val > 0 else ""
            chg_color = _BULL if chg_val > 0 else (_BEAR if chg_val < 0 else _TEXT_SOFT)
            chg_text = f"{sign}{chg_val:.2f}%"
        else:
            chg_color = _TEXT_MUTED
            chg_text = "--%"
        return (
            f"<span style='color:{_TEXT_SOFT}; font-size:8px; font-weight:700;'>{symbol}</span> "
            f"<span style='color:{_TEXT}; font-size:9px; font-weight:800;'>{price_text}</span> "
            f"<span style='color:{chg_color}; font-size:11px; font-weight:900;'>{chg_text}</span>"
        )

    def configure_ticker_ws_tokens(self, instrument_map: Dict[str, Dict[str, Any]]) -> List[int]:
        """Resolve ticker board symbols to instrument tokens for WS subscriptions."""
        resolved: Dict[int, str] = {}
        if not isinstance(instrument_map, dict):
            self._ticker_token_to_symbol = {}
            return []

        for display_symbol in self._ticker_symbols[:5]:
            token = self._find_instrument_token_for_symbol(display_symbol, instrument_map)
            if token is not None:
                resolved[int(token)] = display_symbol.upper()

        self._ticker_token_to_symbol = resolved
        return list(resolved.keys())

    def _find_instrument_token_for_symbol(self, symbol: str, instrument_map: Dict[str, Dict[str, Any]]) -> int | None:
        normalized = str(symbol or '').strip().upper().replace(' ', '')
        candidates = {normalized}

        alias = self._resolve_ticker_instrument(symbol)
        alias_tail = alias.split(':', 1)[-1].strip().upper().replace(' ', '')
        if alias_tail:
            candidates.add(alias_tail)

        for inst in instrument_map.values():
            keys = {
                str(inst.get('tradingsymbol', '')).strip().upper().replace(' ', ''),
                str(inst.get('name', '')).strip().upper().replace(' ', ''),
            }
            if keys & candidates:
                token = inst.get('instrument_token')
                try:
                    return int(token)
                except (TypeError, ValueError):
                    return None
        return None

    def ingest_ws_ticks(self, ticks: List[Dict[str, Any]]) -> None:
        """Update ticker board snapshots from websocket ticks."""
        if not ticks or not self._ticker_token_to_symbol:
            return

        updated = False
        for tick in ticks:
            token = tick.get('instrument_token')
            if token is None:
                continue
            try:
                display_symbol = self._ticker_token_to_symbol.get(int(token))
            except (TypeError, ValueError):
                continue
            if not display_symbol:
                continue

            price = tick.get('last_price')
            ohlc = tick.get('ohlc') if isinstance(tick.get('ohlc'), dict) else {}
            prev_close = ohlc.get('close')
            change_pct = None
            try:
                if price is not None and prev_close not in (None, 0):
                    change_pct = ((float(price) - float(prev_close)) / float(prev_close)) * 100.0
            except (TypeError, ValueError, ZeroDivisionError):
                change_pct = None

            existing = self._ticker_snapshot.get(display_symbol, {})
            self._ticker_snapshot[display_symbol] = {
                'price': price if price is not None else existing.get('price'),
                'change_pct': change_pct if change_pct is not None else existing.get('change_pct'),
            }
            updated = True

        if updated:
            self._refresh_ticker_board_display()

    def _resolve_ticker_instrument(self, symbol: str) -> str:
        key = str(symbol or "").strip().upper()
        if key in self._ticker_alias_map:
            return self._ticker_alias_map[key]
        return key if ":" in key else f"NSE:{key}"

    @Slot(object)
    def _handle_ticker_board_update(self, payload: Dict[str, Dict[str, Any]]) -> None:
        if payload:
            self._ticker_snapshot.update(payload)
        self._refresh_ticker_board_display()

    @Slot(tuple)
    def _handle_ticker_board_error(self, _error: tuple) -> None:
        logger.debug("Ticker board refresh failed", exc_info=False)
        self._refresh_ticker_board_display()

    def update_performance_metrics(self, performance_data: Dict[str, Any]) -> None:
        daily_pnl = performance_data.get("daily_pnl", 0)
        if daily_pnl > 0:
            state = "profit"
        elif daily_pnl < 0:
            state = "loss"
        else:
            state = "flat"
        self.info_button.setProperty("pnlState", state)
        self.info_button.style().unpolish(self.info_button)
        self.info_button.style().polish(self.info_button)
        self.info_button.update()

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
        profile_user_id = str(self._account_info.get("user_id", "DEMO")).strip() or "DEMO"
        display_name = self._preferred_username if self._preferred_username else profile_user_id
        self.user_id_label.setText(display_name)
        balance = self._account_info.get("available_balance", 0.0)
        self.balance_label.setText(self._format_account_balance(balance))
        self._update_account_display_visibility()

    def _update_account_display_visibility(self) -> None:
        show_name = bool(self._show_account_name)
        show_balance = bool(self._show_account_balance)
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

    def _apply_explicit_fonts(self) -> None:
        """Force modern UI typography on visible toolbar widgets.

        Stylesheets cover most cases, but explicit QFont assignment keeps the
        toolbar visually consistent even when child widgets or platform themes
        try to fall back to default/monospace fonts.
        """
        modern_small = _modern_font(9, QFont.Weight.Bold)
        modern_text = _modern_font(10, QFont.Weight.Bold)
        modern_number = _modern_font(10, QFont.Weight.DemiBold)

        for widget in (
            self.buy_button,
            self.sell_button,
            self.info_button,
            self.positions_button,
            self.alerts_button,
        ):
            widget.setFont(modern_small)

        self.search_input.setFont(_modern_font(10, QFont.Weight.DemiBold))
        self.alerts_badge.setFont(_modern_font(9, QFont.Weight.Black))
        self.user_id_label.setFont(modern_small)
        self.balance_label.setFont(modern_number)
        self.account_separator.setFont(_modern_font(8, QFont.Weight.Black))

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
        QWidget#accountInfoWidget,
        QFrame#tickerBoardWidget {{
            background-color: {_BG_PANEL};
            border: 1px solid {_BG_BORDER};
            border-radius: 2px;
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
            font-weight: 600;
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
        QPushButton#infoActionButton[pnlState="profit"] {{
            border-left: 3px solid {_BULL};
        }}
        QPushButton#infoActionButton[pnlState="loss"] {{
            border-left: 3px solid {_BEAR};
        }}

        QPushButton#positionsActionButton {{
            color: {_BLUE};
            border-color: rgba(0, 212, 255, 0.34);
        }}
        QPushButton#positionsActionButton:hover {{
            background-color: rgba(0, 212, 255, 0.12);
            border-color: {_BLUE};
        }}
        QPushButton#positionsActionButton:pressed {{
            background-color: rgba(0, 212, 255, 0.20);
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



        QLabel#tickerBoardText {{
            background: transparent;
            color: {_TEXT_SOFT};
            border: none;
            padding: 1px 2px;
            font-family: {_SANS};
        }}

        QLabel#notificationBadge {{
            background-color: {_BEAR};
            color: white;
            border: 1px solid rgba(255, 255, 255, 0.10);
            border-radius: 2px;
            font-family: {_NUM};
            font-size: 9px;
            font-weight: 800;
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
            font-weight: 800;
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
