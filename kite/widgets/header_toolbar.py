"""Production-grade dark trading terminal header toolbar.

Institutional Dark Trading Terminal UI with modern UI typography for all visible
text and numbers. Monospace is reserved only for raw logs / debug text.

Ticker board redesign:
  - Individual TickerPill widgets with FIXED width — no layout reflow on ticks
  - Only QLabel.setText() is called on price updates → zero jitter
  - Width is computed once at pill construction and on symbol-set changes
  - TC2000-style inline tape: muted symbol / soft price / dominant colored %Chg
"""

import logging
from typing import Any, Dict, List, Optional, Union

from PySide6.QtCore import QSize, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QFont, QFontMetrics, QIcon, QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QVBoxLayout,
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


def _prefer_text_antialias(font: QFont) -> QFont:
    """Prefer antialiased glyph rasterization for crisper HiDPI text."""
    try:
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    except Exception:
        pass
    return font
DEFAULT_PAPER_BALANCE = 1_000_000.0

# ── Institutional Dark Trading Terminal palette ──────────────────────────────
# AMOLED-leaning shell: use true black for the toolbar surface and reserve
# slightly lifted layers only for controls, ticker pills, and account chips.
_BG_APP = "#000000"
_BG_WINDOW = "#050709"
_BG_PANEL = "#090d12"
_BG_SECTION = "#0e141b"
_BG_BORDER = "#151d2b"
_BG_BORDER_HI = "#25344a"

_BULL = "#00d4a8"
_BEAR = "#ff4d6a"
_AMBER = "#f59e0b"
_CYAN = "#00d4ff"
_BLUE = "#3b82f6"

_TEXT = "#e8f0ff"
_TEXT_SYMBOL = "#d7e2f2"
_TEXT_SOFT = "#a8bcd4"
_TEXT_MUTED = "#5a7090"
_TEXT_BUTTON = "#8f9caf"
_TEXT_FAINT = "#2a3a50"
_SELECTION = "#1a2840"

_MONO = "'JetBrains Mono', 'Consolas', monospace"
_SANS = "'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', sans-serif"
_NUM = _SANS
_UI_FONT_FAMILY = "Inter"


def _modern_font(point_size: int = 9, weight: QFont.Weight = QFont.Weight.Medium) -> QFont:
    """Return the preferred modern UI font with safe Qt fallback."""
    font = QFont(_UI_FONT_FAMILY)
    font.setPointSize(point_size)
    font.setWeight(weight)
    try:
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    except Exception:
        pass
    return _prefer_text_antialias(font)


_TOOLBAR_H = 32
_CONTROL_H = 24
_ICON_BTN_W = 28
_ACTION_BTN_H = 24
_ACTION_ICON = 16


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
        self.setFixedSize(16, 16)
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
        self.update_count(count)


# ─────────────────────────────────────────────────────────────────────────────
# TICKER PILL  — one fixed-width widget per symbol
# No layout changes on tick updates, only label text is mutated.
# ─────────────────────────────────────────────────────────────────────────────

class TickerPill(QFrame):
    """
    TC2000-style inline ticker item for a single symbol.

    Design rules:
      • no %Chg pill/badge/background
      • symbol muted, price secondary, %Chg dominant through color + size
      • thin vertical divider between ticker items
      • fixed width after construction so live ticks never shift the toolbar
    """

    _PILL_H = 22
    _PAD_L = 9
    _PAD_R = 9
    _GAP = 8
    _SYM_MIN_W = 48
    _PRICE_W = 72
    _CHG_W = 56

    def __init__(self, symbol: str, parent=None):
        super().__init__(parent)
        self._symbol = symbol.upper()
        self._bull_color = _BULL
        self._bear_color = _BEAR
        self._neutral_color = _TEXT_BUTTON

        self.setObjectName("tickerPill")
        self.setFixedHeight(self._PILL_H)
        self.setFrameShape(QFrame.Shape.NoFrame)

        inner = QHBoxLayout(self)
        inner.setContentsMargins(self._PAD_L, 0, self._PAD_R, 0)
        inner.setSpacing(self._GAP)

        self._sym_label = QLabel(self._symbol)
        self._sym_label.setObjectName("tickerPillSymbol")
        self._sym_label.setFont(_modern_font(7, QFont.Weight.Normal))
        self._sym_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._sym_label.setMinimumWidth(self._SYM_MIN_W)
        self._sym_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._price_label = QLabel("--")
        self._price_label.setObjectName("tickerPillPrice")
        self._price_label.setFont(_modern_font(8, QFont.Weight.Normal))
        self._price_label.setFixedWidth(self._PRICE_W)
        self._price_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._price_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._chg_label = QLabel("--%")
        self._chg_label.setObjectName("tickerPillChange")
        self._chg_label.setFont(_modern_font(9, QFont.Weight.Medium))
        self._chg_label.setFixedWidth(self._CHG_W)
        self._chg_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._chg_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        inner.addWidget(self._sym_label)
        inner.addWidget(self._price_label)
        inner.addWidget(self._chg_label)

        self._compute_fixed_width()

        self._last_state: Optional[str] = None
        self._apply_style(state=None)

    def update_data(self, price: Optional[float], change_pct: Optional[float]) -> None:
        """Update displayed values. Only setText() is called — zero layout impact."""
        if isinstance(price, (int, float)):
            self._price_label.setText(f"{float(price):,.2f}")
        else:
            self._price_label.setText("--")

        if isinstance(change_pct, (int, float)):
            chg = float(change_pct)
            sign = "+" if chg > 0 else ""
            self._chg_label.setText(f"{sign}{chg:.2f}%")
            new_state = "bull" if chg > 0 else ("bear" if chg < 0 else "flat")
        else:
            self._chg_label.setText("--%")
            new_state = None

        if new_state != self._last_state:
            self._last_state = new_state
            self._apply_style(state=new_state)

    def _compute_fixed_width(self) -> None:
        """Lock item width once, using rendered font metrics to prevent jitter."""
        sym_fm = QFontMetrics(self._sym_label.font())
        sym_w = max(self._SYM_MIN_W, sym_fm.horizontalAdvance(self._symbol))
        self._sym_label.setFixedWidth(sym_w)

        total = (
            self._PAD_L
            + sym_w
            + self._GAP
            + self._PRICE_W
            + self._GAP
            + self._CHG_W
            + self._PAD_R
        )
        self.setFixedWidth(total)

    def _apply_style(self, state: Optional[str]) -> None:
        """Apply inline market-tape styling. %Chg is text-only, never a badge."""
        if state == "bull":
            chg_color = self._bull_color
        elif state == "bear":
            chg_color = self._bear_color
        elif state == "flat":
            chg_color = self._neutral_color
        else:
            chg_color = _TEXT_MUTED

        self.setStyleSheet(f"""
            QFrame#tickerPill {{
                background: transparent;
                border: none;
                border-left: 1px solid {_BG_BORDER_HI};
                border-radius: 0px;
            }}
            QLabel#tickerPillSymbol {{
                color: {_TEXT_MUTED};
                background: transparent;
                font-size: 8px;
                font-weight: 500;
                letter-spacing: 0.45px;
                border: none;
                padding: 0px;
            }}
            QLabel#tickerPillPrice {{
                color: {_TEXT_SOFT};
                background: transparent;
                font-size: 9px;
                font-weight: 450;
                border: none;
                padding: 0px;
            }}
            QLabel#tickerPillChange {{
                color: {chg_color};
                background: transparent;
                border: none;
                border-radius: 0px;
                font-size: 10px;
                font-weight: 650;
                letter-spacing: 0.12px;
                padding: 0px;
            }}
        """)


# ─────────────────────────────────────────────────────────────────────────────
# TICKER BOARD  — container that holds all pills side by side
# ─────────────────────────────────────────────────────────────────────────────

class TickerBoard(QFrame):
    """
    Fixed-size horizontal strip containing one TickerPill per symbol.

    The board width = sum of pill widths + gaps.  It is set ONCE when the
    symbol list changes and never touched on tick updates.
    """

    _PILL_GAP = 0        # TC2000-style inline items separated by item borders

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("tickerBoard")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedHeight(_CONTROL_H)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(3, 0, 3, 0)
        self._layout.setSpacing(self._PILL_GAP)

        self._pills: Dict[str, TickerPill] = {}

        self.setStyleSheet(f"""
            QFrame#tickerBoard {{
                background: transparent;
                border: none;
                border-radius: 0px;
            }}
        """)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_symbols(self, symbols: List[str]) -> None:
        """Rebuild pills when the symbol list changes. Called infrequently."""
        # Clear existing pills
        for pill in self._pills.values():
            self._layout.removeWidget(pill)
            pill.deleteLater()
        self._pills.clear()

        if not symbols:
            self.setFixedWidth(0)
            self.hide()
            return

        for sym in symbols:
            pill = TickerPill(sym, self)
            self._layout.addWidget(pill)
            self._pills[sym.upper()] = pill

        # Lock board width to exactly fit all pills + gaps
        self._update_fixed_width()
        self.show()

    def update_ticker(self, symbol: str, price: Optional[float], change_pct: Optional[float]) -> None:
        """Update a single pill. ONLY setText is called — no geometry changes."""
        pill = self._pills.get(symbol.upper())
        if pill:
            pill.update_data(price, change_pct)

    def is_empty(self) -> bool:
        return len(self._pills) == 0

    # ── Private ───────────────────────────────────────────────────────────────

    def _update_fixed_width(self) -> None:
        if not self._pills:
            self.setFixedWidth(0)
            return
        n = len(self._pills)
        pill_total = sum(p.width() for p in self._pills.values())
        gap_total = self._PILL_GAP * max(0, n - 1)
        margins_total = 3 + 3   # left + right contentsMargins
        self.setFixedWidth(pill_total + gap_total + margins_total)


# ─────────────────────────────────────────────────────────────────────────────
# HEADER TOOLBAR
# ─────────────────────────────────────────────────────────────────────────────

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
        self.setIconSize(QSize(_ACTION_ICON, _ACTION_ICON))
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
        self._ticker_symbols: List[str] = ["NIFTY", "SENSEX"]
        self._ticker_alias_map: Dict[str, str] = {
            "NIFTY": "NSE:NIFTY 50",
            "NIFTY50": "NSE:NIFTY 50",
            "BANKNIFTY": "NSE:NIFTY BANK",
            "NIFTYBANK": "NSE:NIFTY BANK",
            "INDIAVIX": "NSE:INDIA VIX",
            "VIX": "NSE:INDIA VIX",
        }
        # Snapshot still kept for REST-based fallback queries
        self._ticker_snapshot: Dict[str, Dict[str, Any]] = {}
        self._ticker_token_to_symbol: Dict[int, str] = {}
        self._symbol_index = SymbolIndex()
        self.threadpool = QThreadPool()
        self._enable_account_polling = bool(enable_account_polling)
        self._last_info_pnl_state: str | None = None

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
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(0)

        self.buy_button = self._make_action_button(
            object_name="buyButton",
            icon_name="plus.svg",
            required=True,
            tooltip="Buy selected symbol",
            label="BUY",
        )
        self.buy_button.clicked.connect(self._on_buy_clicked)
        search_layout.addWidget(self.buy_button)
        search_layout.addWidget(self._create_vertical_divider())

        self.sell_button = self._make_action_button(
            object_name="sellButton",
            icon_name="minus.svg",
            required=True,
            tooltip="Sell selected symbol",
            label="SELL",
        )
        self.sell_button.clicked.connect(self._on_sell_clicked)
        search_layout.addWidget(self.sell_button)
        search_layout.addWidget(self._create_vertical_divider())

        self.info_button = self._make_action_button(
            object_name="infoActionButton",
            icon_name="info.svg",
            required=True,
            tooltip="Open stock information",
            label="INFO",
        )
        self.info_button.clicked.connect(self._on_info_clicked)
        search_layout.addWidget(self.info_button)
        search_layout.addWidget(self._create_vertical_divider())

        self.search_input = EnhancedSearchInput()
        self.search_input.setPlaceholderText("Symbol / company…")
        self.search_input.setObjectName("enhancedSymbolSearch")
        self.search_input.setFixedHeight(_CONTROL_H)
        self.search_input.setFont(_modern_font(9, QFont.Weight.Medium))
        self.search_input.setMinimumWidth(124)
        self.search_input.setMaximumWidth(174)
        self.search_input.symbol_selected.connect(self._on_symbol_committed)
        search_layout.addWidget(self.search_input)

        # ── Ticker board inline — right of symbol search ──────────────────
        # Keep this area permanently reserved. When the ticker board is disabled
        # from settings, only the board content is hidden; the slot width remains
        # locked so BUY/SELL/INFO/search never expand into this space.
        self._ticker_board_divider = self._create_vertical_divider()
        search_layout.addWidget(self._ticker_board_divider)

        self._ticker_board_slot = QWidget(search_group)
        self._ticker_board_slot.setObjectName("tickerBoardSlot")
        self._ticker_board_slot.setFixedHeight(_CONTROL_H)
        self._ticker_board_slot.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        ticker_slot_layout = QHBoxLayout(self._ticker_board_slot)
        ticker_slot_layout.setContentsMargins(0, 0, 0, 0)
        ticker_slot_layout.setSpacing(0)

        self._ticker_board = TickerBoard(self._ticker_board_slot)
        ticker_slot_layout.addWidget(self._ticker_board)
        search_layout.addWidget(self._ticker_board_slot)

        self._ticker_board_reserved_width = 0

        # Build initial pills immediately
        self._rebuild_ticker_pills(self._ticker_symbols)

        self.addWidget(search_group)

    def _create_center_spacer(self):
        spacer = QWidget()
        spacer.setObjectName("centerSpacer")
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer)

    # ── Ticker board (moved into search section) ──────────────────────────────

    def _create_ticker_board_section(self):
        """No-op — ticker board is now built inside _create_symbol_search_section."""
        pass

    # ── Account section ───────────────────────────────────────────────────────

    def _create_alert_section(self):
        """Legacy placeholder kept for API compatibility."""
        return

    def _create_trading_actions_section(self):
        """Legacy placeholder kept for API compatibility."""
        return

    def _create_account_section(self):
        self._add_section_gap(5)

        self.account_info_widget = QWidget()
        self.account_info_widget.setObjectName("accountInfoWidget")
        account_layout = QHBoxLayout(self.account_info_widget)
        account_layout.setContentsMargins(0, 0, 0, 0)
        account_layout.setSpacing(0)

        self.alerts_button = self._make_action_button(
            object_name="alertActionButton",
            icon_name="alert.svg",
            required=True,
            tooltip="Open alert manager",
            label="ALERTS",
        )
        self.alerts_button.clicked.connect(self.alert_manager_requested.emit)
        account_layout.addWidget(self.alerts_button)

        self.alerts_badge = NotificationBadge()
        account_layout.addWidget(self.alerts_badge)
        account_layout.addWidget(self._create_vertical_divider())

        self.positions_button = self._make_action_button(
            object_name="positionsActionButton",
            icon_name="portfolio.svg",
            required=True,
            tooltip="Open positions",
            label="POSITIONS",
        )
        self.positions_button.clicked.connect(self.positions_requested.emit)
        account_layout.addWidget(self.positions_button)
        account_layout.addWidget(self._create_vertical_divider())

        self.account_label_divider = self._create_vertical_divider()
        account_layout.addWidget(self.account_label_divider)

        # Kept as hidden compatibility actions so existing external code can
        # still reference/connect these buttons, but the visible header remains
        # focused on only the high-frequency controls.
        self.pending_orders_button = self._make_action_button(
            object_name="pendingOrdersActionButton",
            icon_name="pending.svg",
            required=True,
            tooltip="Open pending orders",
            label="PENDING",
        )
        self.pending_orders_button.clicked.connect(self.pending_orders_requested.emit)
        self.pending_orders_button.setParent(self)
        self.pending_orders_button.hide()

        self.settings_button = self._make_action_button(
            object_name="settingsActionButton",
            icon_name="gear_setting.svg",
            required=True,
            tooltip="Open settings",
            label="SETTINGS",
        )
        self.settings_button.clicked.connect(self.color_settings_requested.emit)
        self.settings_button.setParent(self)
        self.settings_button.hide()

        self.user_id_label = QLabel(self._account_info.get("user_id", "N/A"))
        self.user_id_label.setObjectName("userIdLabel")
        self.user_id_label.setFont(_modern_font(8, QFont.Weight.Medium))
        account_layout.addWidget(self.user_id_label)

        self.account_separator = self._create_separator_dot()
        account_layout.addWidget(self.account_separator)

        self.balance_label = QLabel("₹0")
        self.balance_label.setObjectName("balanceLabel")
        self.balance_label.setFont(_modern_font(9, QFont.Weight.DemiBold))
        account_layout.addWidget(self.balance_label)

        self.addWidget(self.account_info_widget)

    # ── Widget factories ──────────────────────────────────────────────────────

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
        button.setIconSize(QSize(_ACTION_ICON, _ACTION_ICON))
        button.setToolTip("")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFont(_modern_font(8, QFont.Weight.Normal))
        icon_path = get_asset_path("icons", icon_name, required=required)
        if icon_path is not None:
            button.setIcon(QIcon(str(icon_path)))
        return button

    def _make_action_button(
        self,
        object_name: str,
        icon_name: str,
        required: bool,
        tooltip: str,
        label: str,
    ) -> QPushButton:
        button = self._make_icon_button(object_name, icon_name, required, tooltip)
        button.setText(label)
        button.setFixedHeight(_ACTION_BTN_H)
        button.setIconSize(QSize(max(12, _ACTION_ICON - 2), max(12, _ACTION_ICON - 2)))
        button.setLayoutDirection(Qt.LayoutDirection.LeftToRight)

        # Lock compact button widths so the header does not breathe/reflow as
        # fonts are substituted on different systems.
        text_width = QFontMetrics(button.font()).horizontalAdvance(label)
        button.setFixedWidth(max(48, min(104, text_width + 38)))
        return button

    @staticmethod
    def _create_vertical_divider() -> QFrame:
        divider = QFrame()
        divider.setObjectName("toolbarDivider")
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFixedSize(1, 17)
        return divider

    @staticmethod
    def _make_text_button(text: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("tradingActionButton")
        button.setFixedHeight(_CONTROL_H)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFont(_modern_font(8, QFont.Weight.Normal))
        return button

    @staticmethod
    def _create_separator_dot() -> QLabel:
        dot = QLabel("•")
        dot.setObjectName("separatorDot")
        dot.setFont(_modern_font(8, QFont.Weight.Medium))
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
        raw_tickers = theme.get("ticker_board_symbols", ["NIFTY", "SENSEX"])
        if isinstance(raw_tickers, str):
            raw_tickers = [raw_tickers]
        cleaned = [str(sym).strip().upper() for sym in raw_tickers if str(sym).strip()]
        new_symbols = cleaned[:5] if cleaned else ["NIFTY", "SENSEX"]

        # Only rebuild pills when symbol list actually changes (avoids layout thrash)
        if new_symbols != self._ticker_symbols:
            self._ticker_symbols = new_symbols
            self._rebuild_ticker_pills(new_symbols)

        self._update_account_display()
        self._update_account_display_visibility()
        self._update_ticker_board_visibility()

    # ── Ticker board internals (jitter-free) ──────────────────────────────────

    def _rebuild_ticker_pills(self, symbols: List[str]) -> None:
        """
        Create a fresh set of TickerPill widgets for the given symbol list.
        Called only when the symbol list changes, NOT on every tick.
        """
        self._ticker_board.set_symbols(symbols)
        # Re-populate any snapshot data we already have
        for sym in symbols:
            snap = self._ticker_snapshot.get(sym.upper(), {})
            self._ticker_board.update_ticker(
                sym,
                price=snap.get("price"),
                change_pct=snap.get("change_pct"),
            )
        self._sync_ticker_board_slot_width()
        self._update_ticker_board_visibility()

    def _sync_ticker_board_slot_width(self) -> None:
        """Reserve ticker-board width even when the board is disabled/hidden."""
        if not hasattr(self, "_ticker_board") or not hasattr(self, "_ticker_board_slot"):
            return

        if not self._ticker_board.is_empty():
            self._ticker_board_reserved_width = max(0, self._ticker_board.width())

        reserved = max(0, getattr(self, "_ticker_board_reserved_width", 0))
        self._ticker_board_slot.setFixedWidth(reserved)

    def _update_ticker_board_visibility(self) -> None:
        visible = self._show_ticker_board and not self._ticker_board.is_empty()
        self._sync_ticker_board_slot_width()

        reserved = max(0, getattr(self, "_ticker_board_reserved_width", 0))
        has_reserved_space = reserved > 0

        # Slot remains visible when disabled to stop left controls from shifting.
        self._ticker_board_slot.setVisible(has_reserved_space)
        self._ticker_board_divider.setVisible(has_reserved_space)
        self._ticker_board.setVisible(visible)

    def ingest_ws_ticks(self, ticks: List[Dict[str, Any]]) -> None:
        """
        Process live WebSocket ticks for the ticker board.

        ONLY updates label text on each TickerPill — zero layout, zero resize,
        zero jitter. The width of each pill was fixed at construction time.
        """
        if not ticks or not self._ticker_token_to_symbol:
            return

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

            # Cache the latest snapshot for theme-rebuild hydration
            existing = self._ticker_snapshot.get(display_symbol, {})
            self._ticker_snapshot[display_symbol] = {
                'price': price if price is not None else existing.get('price'),
                'change_pct': change_pct if change_pct is not None else existing.get('change_pct'),
            }

            # Direct pill update — setText only, no geometry changes
            self._ticker_board.update_ticker(
                display_symbol,
                price=price if price is not None else existing.get('price'),
                change_pct=change_pct if change_pct is not None else existing.get('change_pct'),
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

    def _find_instrument_token_for_symbol(self, symbol: str, instrument_map: Dict[str, Dict[str, Any]]) -> Optional[int]:
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

    def _resolve_ticker_instrument(self, symbol: str) -> str:
        key = str(symbol or "").strip().upper()
        if key in self._ticker_alias_map:
            return self._ticker_alias_map[key]
        return key if ":" in key else f"NSE:{key}"

    # ── Legacy REST-based ticker update (fallback, rarely used) ──────────────

    @Slot(object)
    def _handle_ticker_board_update(self, payload: Dict[str, Dict[str, Any]]) -> None:
        if payload:
            self._ticker_snapshot.update(payload)
        # Re-hydrate pills from snapshot
        for sym, snap in payload.items():
            self._ticker_board.update_ticker(
                sym,
                price=snap.get("price"),
                change_pct=snap.get("change_pct"),
            )

    @Slot(tuple)
    def _handle_ticker_board_error(self, _error: tuple) -> None:
        logger.debug("Ticker board refresh failed", exc_info=False)

    # ── Misc public helpers ───────────────────────────────────────────────────

    def update_performance_metrics(self, performance_data: Dict[str, Any]) -> None:
        daily_pnl = performance_data.get("daily_pnl", 0)
        if daily_pnl > 0:
            state = "profit"
        elif daily_pnl < 0:
            state = "loss"
        else:
            state = "flat"

        # Avoid forcing full style repolish on every metrics tick.
        # Repainting only on state transitions prevents right-side toolbar flicker.
        if state == self._last_info_pnl_state:
            return

        self._last_info_pnl_state = state
        self.info_button.setProperty("pnlState", state)
        self.info_button.style().unpolish(self.info_button)
        self.info_button.style().polish(self.info_button)
        self.info_button.update()

    def set_watchlist_symbols(self, symbols: List[str]) -> None:
        pass  # search index handles this now

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
        show_account_text = show_name or show_balance
        self.user_id_label.setVisible(show_name)
        self.balance_label.setVisible(show_balance)
        self.account_separator.setVisible(show_name and show_balance)
        if hasattr(self, "account_label_divider"):
            self.account_label_divider.setVisible(show_account_text)
        # Keep the action area visible even when account name/balance chips are
        # disabled by theme settings, because this widget also hosts the core
        # toolbar actions: alerts, positions, and orders.
        self.account_info_widget.setVisible(True)

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
        """Force modern UI typography on visible toolbar widgets."""
        normal_small = _modern_font(8, QFont.Weight.Normal)
        account_text = _modern_font(8, QFont.Weight.Medium)
        balance_text = _modern_font(9, QFont.Weight.DemiBold)

        for widget in (
            self.buy_button,
            self.sell_button,
            self.info_button,
            self.positions_button,
            self.alerts_button,
            self.pending_orders_button,
            self.settings_button,
        ):
            widget.setFont(normal_small)

        self.search_input.setFont(_modern_font(9, QFont.Weight.Medium))
        self.alerts_badge.setFont(_modern_font(8, QFont.Weight.Bold))
        self.user_id_label.setFont(account_text)
        self.balance_label.setFont(balance_text)
        self.account_separator.setFont(_modern_font(8, QFont.Weight.Medium))

    # ── Styles ────────────────────────────────────────────────────────────────

    def _apply_styles(self):
        self.setStyleSheet(f"""
        QToolBar#enhancedHeaderToolbar {{
            background-color: {_BG_APP};
            border: none;
            border-bottom: 1px solid {_BG_BORDER};
            padding: 1px 6px;
            spacing: 0px;
            min-height: {_TOOLBAR_H}px;
            max-height: {_TOOLBAR_H}px;
            font-family: {_SANS};
        }}

        QWidget#centerSpacer,
        QWidget#sectionGap,
        QWidget#tickerBoardWrapper,
        QWidget#tickerBoardSlot,
        QWidget#symbolSearchGroup,
        QWidget#accountInfoWidget {{
            background: transparent;
            border: none;
        }}

        #enhancedSymbolSearch {{
            background-color: {_BG_WINDOW};
            color: {_TEXT_SYMBOL};
            border: 1px solid {_BG_BORDER};
            border-radius: 2px;
            padding: 1px 8px;
            selection-background-color: {_SELECTION};
            selection-color: {_TEXT};
            font-family: {_SANS};
            font-size: 10px;
            font-weight: 500;
        }}
        #enhancedSymbolSearch:hover {{
            border-color: {_BG_BORDER_HI};
            background-color: {_BG_PANEL};
        }}
        #enhancedSymbolSearch:focus {{
            border: 1px solid {_BG_BORDER_HI};
            background-color: {_BG_SECTION};
            color: {_TEXT};
        }}

        QPushButton {{
            outline: none;
            border-radius: 0px;
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 500;
            letter-spacing: 0.35px;
        }}

        QPushButton#buyButton,
        QPushButton#sellButton,
        QPushButton#infoActionButton,
        QPushButton#alertActionButton,
        QPushButton#positionsActionButton,
        QPushButton#orderHistoryActionButton,
        QPushButton#pendingOrdersActionButton,
        QPushButton#settingsActionButton {{
            background-color: transparent;
            border: none;
            border-radius: 0px;
            padding: 1px 7px;
            text-align: left;
            color: {_TEXT_BUTTON};
        }}

        QPushButton#buyButton:hover,
        QPushButton#sellButton:hover,
        QPushButton#infoActionButton:hover,
        QPushButton#alertActionButton:hover,
        QPushButton#positionsActionButton:hover,
        QPushButton#orderHistoryActionButton:hover,
        QPushButton#pendingOrdersActionButton:hover,
        QPushButton#settingsActionButton:hover {{
            background-color: rgba(143, 156, 175, 0.075);
            color: {_TEXT};
        }}

        QPushButton#buyButton:pressed,
        QPushButton#sellButton:pressed,
        QPushButton#infoActionButton:pressed,
        QPushButton#alertActionButton:pressed,
        QPushButton#positionsActionButton:pressed,
        QPushButton#orderHistoryActionButton:pressed,
        QPushButton#pendingOrdersActionButton:pressed,
        QPushButton#settingsActionButton:pressed {{
            background-color: rgba(26, 40, 64, 0.92);
            color: {_TEXT};
        }}
        QPushButton:disabled {{
            color: {_TEXT_FAINT};
            border: none;
            background-color: transparent;
        }}

        QLabel#notificationBadge {{
            background-color: {_AMBER};
            color: #120b00;
            border: 1px solid rgba(245, 158, 11, 0.45);
            border-radius: 2px;
            font-family: {_NUM};
            font-size: 8px;
            font-weight: 650;
            padding: 0px;
            margin-right: 4px;
        }}

        QFrame#toolbarDivider {{
            background-color: {_BG_BORDER_HI};
            border: none;
            margin-top: 4px;
            margin-bottom: 4px;
        }}

        QPushButton#tradingActionButton {{
            background-color: transparent;
            color: {_TEXT_BUTTON};
            border: none;
            border-radius: 0px;
            padding: 1px 7px;
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 500;
            letter-spacing: 0.35px;
        }}
        QPushButton#tradingActionButton:hover {{
            background-color: rgba(143, 156, 175, 0.075);
            color: {_TEXT};
        }}
        QPushButton#tradingActionButton:pressed {{
            background-color: {_SELECTION};
            color: {_TEXT};
        }}
        QPushButton#tradingActionButton[pnlState="profit"],
        QPushButton#tradingActionButton[pnlState="loss"],
        QPushButton#tradingActionButton[pnlState="flat"] {{
            color: {_TEXT_BUTTON};
            border: none;
            padding-left: 7px;
        }}

        QLabel#userIdLabel {{
            background-color: transparent;
            color: {_TEXT_SOFT};
            border: none;
            border-radius: 0px;
            padding: 1px 7px;
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 500;
        }}
        QLabel#balanceLabel {{
            background-color: transparent;
            color: {_BULL};
            border: none;
            border-radius: 0px;
            padding: 1px 7px;
            font-family: {_NUM};
            font-size: 10px;
            font-weight: 600;
        }}
        QLabel#separatorDot {{
            background: transparent;
            color: {_TEXT_FAINT};
            font-size: 8px;
            font-weight: 600;
            padding: 0px 1px;
        }}

        QToolTip {{
            background-color: {_BG_APP};
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
