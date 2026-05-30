# ibkr/widgets/order_dialog.py
"""
OrderDialog — IBKR-first USA swing trading order ticket.

Goals
─────
- Keep the existing application API stable:
    dialog = OrderDialog(parent, symbol, ltp, order_details, instrument, ltp_fetcher)
    dialog.order_placed.connect(main_window._handle_order_placement)
- Emit a dictionary that still satisfies the current MainWindow validation
  (`tradingsymbol`, `transaction_type`, `quantity`, `order_type`) while also
  carrying IBKR/ib_insync style aliases (`action`, `totalQuantity`, `orderType`,
  `lmtPrice`, `auxPrice`, `tif`, `outsideRth`, `secType`, `currency`).
- Remove India/Kite-specific concepts from the UI: MIS/CNC/NRML, AMO, GTT,
  BO, circuit limits, STT estimates.
- Default to US swing-trading behavior: SMART routing, USD stock contract,
  whole-share quantity, LIMIT order, GTC/DAY time-in-force, RTH-only by default.

This file is intentionally self-contained and pure PySide6.
"""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import (
    Qt,
    Signal,
    Slot,
    QPoint,
    QPropertyAnimation,
    QEasingCurve,
    Property,
    QTimer,
)
from PySide6.QtGui import QColor, QCursor, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ibkr.utils.ibkr_price import safe_ibkr_price

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Theme
# ─────────────────────────────────────────────────────────────────────────────

class P:
    BG0 = "#050709"       # deepest shell
    BG1 = "#0a0d12"       # body
    BG2 = "#0f1318"       # panels
    BG3 = "#141920"       # input surface
    BG4 = "#1a2030"       # hover/raised surface
    BORDER = "#1a2030"
    BORDER2 = "#2a3a50"
    T0 = "#e8f0ff"
    T1 = "#a8bcd4"
    T2 = "#5a7090"
    T3 = "#344458"
    BUY = "#00d4a8"
    SELL = "#ff4d6a"
    AMBER = "#f59e0b"
    CYAN = "#00d4ff"
    BLUE = "#7aa2ff"
    WARN_BG = "rgba(245,158,11,0.08)"
    SELL_BG = "rgba(255,77,106,0.10)"
    BUY_BG = "rgba(0,212,168,0.10)"


FONT_UI = "Inter"
FONT_NUM = "Inter"
FONT_FALL = "'Segoe UI Variable', 'Segoe UI', Arial, sans-serif"


UI_ORDER_TYPES = ["MARKET", "LIMIT", "STOP", "STOP LIMIT"]
IBKR_ORDER_TYPES = {
    "MARKET": "MKT",
    "MKT": "MKT",
    "LIMIT": "LMT",
    "LMT": "LMT",
    "STOP": "STP",
    "STP": "STP",
    "STOP LIMIT": "STP LMT",
    "STOP-LIMIT": "STP LMT",
    "STP LMT": "STP LMT",
    # Backward-compatible names from the older dialog:
    "SL-M": "STP",
    "SL": "STP LMT",
}
UI_FROM_ORDER_TYPE = {
    "MKT": "MARKET",
    "MARKET": "MARKET",
    "LMT": "LIMIT",
    "LIMIT": "LIMIT",
    "STP": "STOP",
    "STOP": "STOP",
    "SL-M": "STOP",
    "STP LMT": "STOP LIMIT",
    "STOP LIMIT": "STOP LIMIT",
    "SL": "STOP LIMIT",
}
EXCHANGES = ["SMART", "NASDAQ", "NYSE", "ARCA", "AMEX", "IEX", "ISLAND", "BATS"]
VALIDITY = ["DAY", "GTC", "IOC"]
CURRENCIES = ["USD"]
ROUTES = ["AUTO", "ADAPTIVE", "DARK/SMART"]
DEFAULT_TICK = 0.01


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _as_float(value: Any, default: float = 0.0) -> float:
    return safe_ibkr_price(value, default)


def _as_int(value: Any, default: int = 1) -> int:
    try:
        val = int(float(value))
        return val if val > 0 else default
    except Exception:
        return default


def _first_value(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return default


def _normalize_upper(value: Any, default: str = "") -> str:
    text = str(value or default or "").strip().upper()
    return text or default


def _tick_decimals(tick_size: float) -> int:
    try:
        d = Decimal(str(tick_size)).normalize()
        decimals = max(0, -d.as_tuple().exponent)
        return max(2, min(6, decimals))
    except Exception:
        return 2


def _snap_to_tick(value: float, tick_size: float) -> float:
    if value <= 0 or tick_size <= 0:
        return max(0.0, float(value))
    try:
        v = Decimal(str(value))
        t = Decimal(str(tick_size))
        ticks = (v / t).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        snapped = ticks * t
        return float(snapped)
    except (InvalidOperation, ValueError):
        return round(value, _tick_decimals(tick_size))


def _show_error(message: str) -> None:
    try:
        from ibkr.widgets.status_bar import show_error
        show_error(message)
    except Exception:
        log.warning("OrderDialog validation error: %s", message)


# ─────────────────────────────────────────────────────────────────────────────
# Small widgets
# ─────────────────────────────────────────────────────────────────────────────

class _Label(QLabel):
    def __init__(self, text: str = "", color: str = P.T1, size: int = 10, bold: bool = False, parent=None):
        super().__init__(text, parent)
        weight = "750" if bold else "500"
        self.setStyleSheet(
            f"color:{color};font-family:'{FONT_UI}',{FONT_FALL};"
            f"font-size:{size}px;font-weight:{weight};background:transparent;"
        )


class _Value(QLabel):
    def __init__(self, text: str = "—", color: str = P.T0, size: int = 12, parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setStyleSheet(
            f"color:{color};font-family:'{FONT_NUM}',{FONT_FALL};"
            f"font-size:{size}px;font-weight:750;background:transparent;"
        )


class _SegButton(QPushButton):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setMinimumHeight(28)
        self._accent = P.CYAN
        self.toggled.connect(lambda _: self._refresh())
        self._refresh()

    def set_accent(self, color: str) -> None:
        self._accent = color
        self._refresh()

    def _refresh(self) -> None:
        active = self.isChecked()
        bg = P.BG2 if active else P.BG3
        fg = P.T0 if active else P.T2
        border = self._accent if active else P.BORDER2
        self.setStyleSheet(f"""
            QPushButton {{
                background:{bg};
                color:{fg};
                border:1px solid {border};
                border-radius:2px;
                font-family:'{FONT_UI}',{FONT_FALL};
                font-size:9px;
                font-weight:800;
                letter-spacing:0.6px;
                padding:4px 6px;
            }}
            QPushButton:hover {{
                background:{P.BG4};
                color:{P.T0};
                border-color:{self._accent};
            }}
        """)


class _SegGroup(QWidget):
    currentChanged = Signal(str)

    def __init__(self, options: List[str], default: str = "", parent=None):
        super().__init__(parent)
        self._btns: Dict[str, _SegButton] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for opt in options:
            btn = _SegButton(opt, self)
            btn.clicked.connect(lambda _=False, value=opt: self._select(value))
            layout.addWidget(btn, 1)
            self._btns[opt] = btn
        self._select(default if default in self._btns else options[0])

    def _select(self, value: str) -> None:
        if value not in self._btns:
            return
        for key, btn in self._btns.items():
            btn.blockSignals(True)
            btn.setChecked(key == value)
            btn.blockSignals(False)
            btn._refresh()
        self.currentChanged.emit(value)

    def current(self) -> str:
        for key, btn in self._btns.items():
            if btn.isChecked():
                return key
        return ""

    def set_current(self, value: str) -> None:
        self._select(value)

    def set_button_accent(self, key: str, color: str) -> None:
        if key in self._btns:
            self._btns[key].set_accent(color)


class _DropdownField(QWidget):
    currentChanged = Signal(str)

    def __init__(self, options: List[str], default: str = "", parent=None):
        super().__init__(parent)
        self._combo = QComboBox(self)
        self._combo.addItems(options)
        self._combo.currentTextChanged.connect(self.currentChanged.emit)
        self._combo.setStyleSheet(f"""
            QComboBox {{
                background:{P.BG3};
                color:{P.T0};
                border:1px solid {P.BORDER2};
                border-radius:2px;
                font-family:'{FONT_UI}',{FONT_FALL};
                font-size:11px;
                font-weight:750;
                letter-spacing:0.3px;
                min-height:20px;
                padding:5px 24px 5px 8px;
            }}
            QComboBox:hover, QComboBox:focus {{
                background:{P.BG2};
                border:1px solid {P.CYAN};
            }}
            QComboBox::drop-down {{
                border:none;
                width:20px;
                background:transparent;
            }}
            QComboBox::down-arrow {{
                image:none;
                width:0px;
                height:0px;
                border-left:5px solid transparent;
                border-right:5px solid transparent;
                border-top:6px solid {P.T1};
                margin-right:6px;
            }}
            QComboBox QAbstractItemView {{
                background:{P.BG1};
                color:{P.T0};
                border:1px solid {P.BORDER2};
                selection-background-color:{P.BG2};
                selection-color:{P.T0};
                outline:none;
            }}
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._combo)
        self.set_current(default or options[0])

    def current(self) -> str:
        return self._combo.currentText()

    def set_current(self, value: str) -> None:
        idx = self._combo.findText(str(value))
        if idx >= 0:
            self._combo.setCurrentIndex(idx)

    def add_missing_and_select(self, value: str) -> None:
        value = str(value or "").strip().upper()
        if not value:
            return
        if self._combo.findText(value) < 0:
            self._combo.addItem(value)
        self.set_current(value)


class _TextInput(QLineEdit):
    def __init__(self, text: str = "", placeholder: str = "", parent=None):
        super().__init__(text, parent)
        self.setPlaceholderText(placeholder)
        self.setStyleSheet(f"""
            QLineEdit {{
                background:{P.BG3};
                color:{P.T0};
                border:1px solid {P.BORDER2};
                border-radius:2px;
                font-family:'{FONT_UI}',{FONT_FALL};
                font-size:11px;
                font-weight:650;
                padding:5px 8px;
                min-height:20px;
            }}
            QLineEdit:focus {{
                background:{P.BG2};
                border-color:{P.CYAN};
            }}
            QLineEdit::placeholder {{ color:{P.T3}; }}
        """)


class _IntInput(QSpinBox):
    def __init__(self, lo: int = 1, hi: int = 10_000_000, step: int = 1, parent=None):
        super().__init__(parent)
        self.setRange(lo, hi)
        self.setSingleStep(max(1, step))
        self.setButtonSymbols(QSpinBox.NoButtons)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setStyleSheet(f"""
            QSpinBox {{
                background:{P.BG3};
                color:{P.T0};
                border:1px solid {P.BORDER2};
                border-radius:2px;
                font-family:'{FONT_NUM}',{FONT_FALL};
                font-size:15px;
                font-weight:750;
                padding:4px 8px;
                min-height:24px;
            }}
            QSpinBox:focus {{
                border:1px solid {P.CYAN};
                background:{P.BG2};
            }}
        """)

    def mousePressEvent(self, event):  # Keep shortcut focus behavior pleasant.
        super().mousePressEvent(event)
        edit = self.lineEdit()
        if edit is not None:
            edit.setCursorPosition(len(edit.text()))


class _NumInput(QDoubleSpinBox):
    def __init__(self, decimals: int = 2, step: float = 0.01, lo: float = 0.0, hi: float = 9_999_999.0, parent=None):
        super().__init__(parent)
        self.setDecimals(decimals)
        self.setSingleStep(step)
        self.setRange(lo, hi)
        self.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setPrefix("$")
        self.setStyleSheet(f"""
            QDoubleSpinBox {{
                background:{P.BG3};
                color:{P.T0};
                border:1px solid {P.BORDER2};
                border-radius:2px;
                font-family:'{FONT_NUM}',{FONT_FALL};
                font-size:14px;
                font-weight:750;
                padding:4px 8px;
                min-height:24px;
            }}
            QDoubleSpinBox:focus {{
                border:1px solid {P.CYAN};
                background:{P.BG2};
            }}
        """)


class _StepButton(QPushButton):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setFixedSize(25, 30)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setStyleSheet(f"""
            QPushButton {{
                background:{P.BG3};
                color:{P.T1};
                border:1px solid {P.BORDER2};
                border-radius:2px;
                font-size:15px;
                font-weight:650;
            }}
            QPushButton:hover {{ background:{P.BG2}; color:{P.T0}; border-color:{P.CYAN}; }}
            QPushButton:pressed {{ background:{P.BG0}; }}
        """)


class _Toggle(QCheckBox):
    def __init__(self, label: str, parent=None):
        super().__init__(label, parent)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setStyleSheet(f"""
            QCheckBox {{
                color:{P.T1};
                spacing:6px;
                font-family:'{FONT_UI}',{FONT_FALL};
                font-size:10px;
                font-weight:800;
                letter-spacing:0.6px;
                background:transparent;
            }}
            QCheckBox:hover {{ color:{P.T0}; }}
            QCheckBox::indicator {{
                width:14px;
                height:14px;
                border-radius:2px;
                background:{P.BG3};
                border:1px solid {P.BORDER2};
            }}
            QCheckBox::indicator:checked {{
                background:{P.CYAN};
                border:1px solid {P.CYAN};
            }}
        """)


class _LTPLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = QColor(P.T0)
        self._anim = QPropertyAnimation(self, b"_flash_color", self)
        self._anim.setDuration(420)
        self._anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._base = (
            f"font-family:'{FONT_NUM}',{FONT_FALL};"
            f"font-size:21px;font-weight:800;background:transparent;"
        )
        self._apply_color()

    def flash(self, direction: str) -> None:
        self._anim.stop()
        self._anim.setStartValue(QColor(P.BUY if direction == "up" else P.SELL))
        self._anim.setEndValue(QColor(P.T0))
        self._anim.start()

    def _get_flash_color(self) -> QColor:
        return self._color

    def _set_flash_color(self, color: QColor) -> None:
        self._color = color
        self._apply_color()

    _flash_color = Property(QColor, _get_flash_color, _set_flash_color)

    def _apply_color(self) -> None:
        self.setStyleSheet(self._base + f"color:{self._color.name()};")


# ─────────────────────────────────────────────────────────────────────────────
# Main dialog
# ─────────────────────────────────────────────────────────────────────────────

class OrderDialog(QDialog):
    """IBKR-first order dialog for US stock swing trading."""

    order_placed = Signal(dict)

    def __init__(
        self,
        parent=None,
        symbol: str = "",
        ltp: float = 0.0,
        order_details: Optional[Dict[str, Any]] = None,
        instrument: Optional[Dict[str, Any]] = None,
        ltp_fetcher: Optional[Callable[[str], float]] = None,
    ):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setModal(True)
        self.setWindowTitle("IBKR Order Ticket")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumWidth(500)
        self.resize(500, 500)

        self.symbol = str(symbol or "").strip().upper()
        self.instrument = instrument or {}
        self._ltp_fetcher = ltp_fetcher
        self.ltp = max(0.0, _as_float(ltp, 0.0))
        self._prev_ltp = self.ltp
        self._order_details = dict(order_details or {})

        self._exchange = self._infer_exchange(self._order_details, self.instrument)
        self._primary_exchange = self._infer_primary_exchange(self._order_details, self.instrument)
        self._currency = self._infer_currency(self._order_details, self.instrument)
        self._sec_type = _normalize_upper(_first_value(
            self._order_details.get("secType"),
            self._order_details.get("sec_type"),
            self.instrument.get("secType"),
            self.instrument.get("sec_type"),
            default="STK",
        ), "STK")
        self._tick_size = self._infer_tick_size(self.instrument)
        self._price_decimals = _tick_decimals(self._tick_size)
        self._confirm_stage = 0
        self._drag_active = False
        self._drag_offset = QPoint()
        self._bid = _as_float(self._order_details.get("bid"), 0.0)
        self._ask = _as_float(self._order_details.get("ask"), 0.0)
        self._depth_buy: List[Dict[str, Any]] = []
        self._depth_sell: List[Dict[str, Any]] = []

        self._setup_ui()
        self._apply_global_styles()
        self._seed_defaults()
        self._connect_signals()
        self._refresh_fields_visibility()
        self._update_quote_strip()
        self._update_summary()
        self._refresh_confirm_btn()
        self._sync_dialog_height()

    # ──────────────────────────────────────────────────────────────────────
    # Inference
    # ──────────────────────────────────────────────────────────────────────

    def _infer_exchange(self, od: Dict[str, Any], instr: Dict[str, Any]) -> str:
        value = _normalize_upper(_first_value(
            od.get("exchange"),
            od.get("route"),
            instr.get("exchange"),
            instr.get("valid_exchange"),
            default="SMART",
        ), "SMART")
        return value if value in EXCHANGES else "SMART"

    def _infer_primary_exchange(self, od: Dict[str, Any], instr: Dict[str, Any]) -> str:
        value = _normalize_upper(_first_value(
            od.get("primaryExchange"),
            od.get("primary_exchange"),
            instr.get("primaryExchange"),
            instr.get("primary_exchange"),
            instr.get("listing_exchange"),
            default="",
        ), "")
        return value

    def _infer_currency(self, od: Dict[str, Any], instr: Dict[str, Any]) -> str:
        value = _normalize_upper(_first_value(od.get("currency"), instr.get("currency"), default="USD"), "USD")
        return value if value in CURRENCIES else "USD"

    def _infer_tick_size(self, instr: Dict[str, Any]) -> float:
        tick = _as_float(_first_value(
            instr.get("minTick"),
            instr.get("min_tick"),
            instr.get("tick_size"),
            default=DEFAULT_TICK,
        ), DEFAULT_TICK)
        if tick <= 0:
            tick = DEFAULT_TICK
        return tick

    def _initial_side(self) -> str:
        side = _normalize_upper(_first_value(
            self._order_details.get("action"),
            self._order_details.get("transaction_type"),
            default="BUY",
        ), "BUY")
        return "SELL" if side == "SELL" else "BUY"

    def _initial_order_type(self) -> str:
        raw = _normalize_upper(_first_value(
            self._order_details.get("orderType"),
            self._order_details.get("order_type"),
            default="LIMIT",
        ), "LIMIT")
        return UI_FROM_ORDER_TYPE.get(raw, "LIMIT")

    def _initial_limit_price(self) -> float:
        value = _as_float(_first_value(
            self._order_details.get("lmtPrice"),
            self._order_details.get("limit_price"),
            self._order_details.get("price"),
            default=0.0,
        ), 0.0)
        if value <= 0 and self.ltp > 0:
            value = self.ltp
        return _snap_to_tick(value, self._tick_size)

    def _initial_stop_price(self) -> float:
        value = _as_float(_first_value(
            self._order_details.get("auxPrice"),
            self._order_details.get("stop_price"),
            self._order_details.get("trigger_price"),
            default=0.0,
        ), 0.0)
        return _snap_to_tick(value, self._tick_size)

    # ──────────────────────────────────────────────────────────────────────
    # UI
    # ──────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)

        self._container = QFrame(self)
        self._container.setObjectName("dialogContainer")
        outer.addWidget(self._container)

        root = QVBoxLayout(self._container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_body(), 1)

    def _build_header(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("header")
        frame.setFixedHeight(40)
        frame.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))

        layout = QHBoxLayout(frame)
        layout.setContentsMargins(10, 0, 6, 0)
        layout.setSpacing(8)

        self._sym_label = _Label(self.symbol or "—", P.T0, 15, True)
        self._sym_label.setObjectName("symbolTitle")
        layout.addWidget(self._sym_label)

        self._contract_label = _Label("STK · SMART · USD", P.CYAN, 9, True)
        self._contract_label.setObjectName("contractLabel")
        layout.addWidget(self._contract_label)

        layout.addStretch()

        layout.addWidget(_Label("LTP", P.T2, 9, True))
        self._ltp_label = _LTPLabel(self)
        self._ltp_label.setText(self._format_price(self.ltp) if self.ltp > 0 else "$—")
        layout.addWidget(self._ltp_label)

        self._close_btn = QPushButton("✕", self)
        self._close_btn.setObjectName("orderCloseButton")
        self._close_btn.setFixedSize(24, 22)
        self._close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._close_btn.clicked.connect(self.reject)
        layout.addWidget(self._close_btn)
        return frame

    def _build_body(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("body")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 8, 10, 9)
        layout.setSpacing(7)

        self._side_group = _SegGroup(["BUY", "SELL"], self._initial_side(), self)
        self._side_group.setFixedHeight(32)
        self._side_group.set_button_accent("BUY", P.BUY)
        self._side_group.set_button_accent("SELL", P.SELL)
        layout.addWidget(self._side_group)

        layout.addWidget(self._build_route_grid())
        layout.addWidget(self._build_quantity_block())
        layout.addWidget(self._build_price_block())
        layout.addWidget(self._build_execution_options())
        layout.addWidget(self._build_quote_strip())
        layout.addWidget(self._build_summary_box())
        layout.addWidget(self._build_action_row())
        return frame

    def _build_route_grid(self) -> QWidget:
        wrap = QWidget(self)
        wrap.setObjectName("routeGrid")
        grid = QGridLayout(wrap)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(5)

        self._otype_seg = _DropdownField(UI_ORDER_TYPES, self._initial_order_type(), self)
        self._tif_seg = _DropdownField(VALIDITY, _normalize_upper(_first_value(
            self._order_details.get("tif"), self._order_details.get("validity"), default="GTC"
        ), "GTC"), self)
        self._exchange_seg = _DropdownField(EXCHANGES, self._exchange, self)
        self._route_seg = _DropdownField(ROUTES, "AUTO", self)
        self._primary_exchange_input = _TextInput(self._primary_exchange, "optional", self)
        self._account_input = _TextInput(str(self._order_details.get("account") or ""), "optional", self)

        grid.addWidget(self._labeled_block("ORDER TYPE", self._otype_seg), 0, 0)
        grid.addWidget(self._labeled_block("TIF", self._tif_seg), 0, 1)
        grid.addWidget(self._labeled_block("EXCHANGE", self._exchange_seg), 0, 2)
        grid.addWidget(self._labeled_block("ALGO / ROUTE", self._route_seg), 1, 0)
        grid.addWidget(self._labeled_block("PRIMARY EXCH", self._primary_exchange_input), 1, 1)
        grid.addWidget(self._labeled_block("ACCOUNT", self._account_input), 1, 2)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        return wrap

    def _build_quantity_block(self) -> QWidget:
        wrap = QWidget(self)
        wrap.setObjectName("quantityBlock")
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self._section_label("QUANTITY"))
        header.addStretch()
        header.addWidget(_Label("WHOLE SHARES", P.CYAN, 9, True))
        layout.addLayout(header)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self._qty_minus = _StepButton("−", self)
        self._qty_plus = _StepButton("+", self)
        self._qty_spin = _IntInput(1, 10_000_000, 1, self)
        self._qty_spin.setValue(_as_int(self._order_details.get("quantity"), 1))
        row.addWidget(self._qty_minus)
        row.addWidget(self._qty_spin, 1)
        row.addWidget(self._qty_plus)
        row.addWidget(_Label("SHR", P.T2, 9, True))
        layout.addLayout(row)
        return wrap

    def _build_price_block(self) -> QWidget:
        wrap = QWidget(self)
        wrap.setObjectName("priceBlock")
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self._section_label("PRICE"))
        header.addStretch()
        header.addWidget(_Label(f"TICK: ${self._tick_size:g}", P.CYAN, 9, True))
        layout.addLayout(header)

        self._limit_row = QWidget(self)
        self._limit_row.setObjectName("limitRow")
        limit_layout = QHBoxLayout(self._limit_row)
        limit_layout.setContentsMargins(0, 0, 0, 0)
        limit_layout.setSpacing(6)
        limit_layout.addWidget(_Label("LIMIT", P.T2, 9, True))
        self._price_spin = _NumInput(self._price_decimals, self._tick_size, 0.0, 9_999_999.0, self)
        self._price_spin.setValue(self._initial_limit_price())
        self._snap_limit_btn = _MiniAction("SNAP", self)
        self._mid_btn = _MiniAction("MID", self)
        self._bid_btn = _MiniAction("BID", self)
        self._ask_btn = _MiniAction("ASK", self)
        limit_layout.addWidget(self._price_spin, 1)
        limit_layout.addWidget(self._snap_limit_btn)
        limit_layout.addWidget(self._bid_btn)
        limit_layout.addWidget(self._mid_btn)
        limit_layout.addWidget(self._ask_btn)
        layout.addWidget(self._limit_row)

        self._stop_row = QWidget(self)
        self._stop_row.setObjectName("stopRow")
        stop_layout = QHBoxLayout(self._stop_row)
        stop_layout.setContentsMargins(0, 0, 0, 0)
        stop_layout.setSpacing(6)
        stop_layout.addWidget(_Label("STOP", P.T2, 9, True))
        self._stop_spin = _NumInput(self._price_decimals, self._tick_size, 0.0, 9_999_999.0, self)
        self._stop_spin.setValue(self._initial_stop_price())
        self._snap_stop_btn = _MiniAction("SNAP", self)
        stop_layout.addWidget(self._stop_spin, 1)
        stop_layout.addWidget(self._snap_stop_btn)
        layout.addWidget(self._stop_row)

        # Backward-compatible alias used by older chart-context code.
        self._trig_spin = self._stop_spin
        return wrap

    def _build_execution_options(self) -> QWidget:
        wrap = QWidget(self)
        wrap.setObjectName("executionOptions")
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._outside_rth_chk = _Toggle("ALLOW OUTSIDE RTH", self)
        self._outside_rth_chk.setChecked(bool(_first_value(
            self._order_details.get("outsideRth"), self._order_details.get("outside_rth"), default=False
        )))
        self._transmit_chk = _Toggle("TRANSMIT", self)
        self._transmit_chk.setChecked(bool(_first_value(self._order_details.get("transmit"), default=True)))
        self._transmit_chk.setToolTip("Unchecked sends a held/non-transmitted order only if your broker session supports it.")

        layout.addWidget(self._outside_rth_chk)
        layout.addWidget(self._transmit_chk)
        layout.addStretch()
        return wrap

    def _build_quote_strip(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("quoteStrip")
        grid = QGridLayout(frame)
        grid.setContentsMargins(8, 6, 8, 6)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)

        self._bid_val = _Value("—", P.BUY, 12, self)
        self._ask_val = _Value("—", P.SELL, 12, self)
        self._spread_val = _Value("—", P.AMBER, 12, self)
        self._mid_val = _Value("—", P.CYAN, 12, self)

        grid.addWidget(_Label("BID", P.T2, 9, True), 0, 0)
        grid.addWidget(_Label("ASK", P.T2, 9, True), 0, 1)
        grid.addWidget(_Label("SPREAD", P.T2, 9, True), 0, 2)
        grid.addWidget(_Label("MID", P.T2, 9, True), 0, 3)
        grid.addWidget(self._bid_val, 1, 0)
        grid.addWidget(self._ask_val, 1, 1)
        grid.addWidget(self._spread_val, 1, 2)
        grid.addWidget(self._mid_val, 1, 3)
        for col in range(4):
            grid.setColumnStretch(col, 1)
        return frame

    def _build_summary_box(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("summaryBox")
        grid = QGridLayout(frame)
        grid.setContentsMargins(8, 6, 8, 7)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)

        self._est_price_val = _Value("—", P.T0, 12, self)
        self._ov_val = _Value("—", P.T0, 12, self)
        self._risk_val = _Value("—", P.AMBER, 12, self)
        self._route_val = _Value("—", P.CYAN, 12, self)
        self._warning_label = _Label("", P.AMBER, 9, True, self)
        self._warning_label.setObjectName("warningLabel")
        self._warning_label.setWordWrap(True)

        grid.addWidget(_Label("EST PRICE", P.T2, 9, True), 0, 0)
        grid.addWidget(_Label("ORDER VALUE", P.T2, 9, True), 0, 1)
        grid.addWidget(_Label("1R RISK", P.T2, 9, True), 0, 2)
        grid.addWidget(_Label("ROUTE", P.T2, 9, True), 0, 3)
        grid.addWidget(self._est_price_val, 1, 0)
        grid.addWidget(self._ov_val, 1, 1)
        grid.addWidget(self._risk_val, 1, 2)
        grid.addWidget(self._route_val, 1, 3)
        grid.addWidget(self._warning_label, 2, 0, 1, 4)
        for col in range(4):
            grid.setColumnStretch(col, 1)
        return frame

    def _build_action_row(self) -> QWidget:
        wrap = QWidget(self)
        wrap.setObjectName("actionRow")
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._confirm_hint = _Label("REVIEW MODE — press confirm again to transmit the ticket", P.AMBER, 9, True, self)
        self._confirm_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._confirm_hint.setVisible(False)
        layout.addWidget(self._confirm_hint)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        self._cancel_btn = QPushButton("CANCEL", self)
        self._cancel_btn.setObjectName("cancelButton")
        self._cancel_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._submit_btn = QPushButton("BUY", self)
        self._submit_btn.setObjectName("submitButton")
        self._submit_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        row.addWidget(self._cancel_btn, 1)
        row.addWidget(self._submit_btn, 2)
        layout.addLayout(row)
        return wrap

    def _labeled_block(self, label: str, widget: QWidget) -> QWidget:
        wrap = QWidget(self)
        wrap.setObjectName("fieldBlock")
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(self._section_label(label))
        layout.addWidget(widget)
        return wrap

    def _section_label(self, text: str) -> QLabel:
        label = _Label(text, P.T2, 9, True, self)
        label.setStyleSheet(
            f"color:{P.T2};font-family:'{FONT_UI}',{FONT_FALL};"
            f"font-size:9px;font-weight:800;letter-spacing:0.65px;background:transparent;"
        )
        return label

    # ──────────────────────────────────────────────────────────────────────
    # Styling
    # ──────────────────────────────────────────────────────────────────────

    def _apply_global_styles(self) -> None:
        self.setStyleSheet(f"QDialog{{background:{P.BG0};}}")
        self._container.setStyleSheet(f"""
            QFrame#dialogContainer {{
                background:{P.BG1};
                border:1px solid {P.BORDER};
                border-radius:2px;
            }}
            QFrame#header {{
                background:{P.BG0};
                border-bottom:1px solid {P.BORDER};
            }}
            QLabel#ticketBadge {{
                color:{P.AMBER};
                font-family:'{FONT_UI}',{FONT_FALL};
                font-size:9px;
                font-weight:850;
                letter-spacing:0.9px;
                background:transparent;
            }}
            QLabel#symbolTitle {{
                color:{P.T0};
                font-family:'{FONT_UI}',{FONT_FALL};
                font-size:15px;
                font-weight:850;
                letter-spacing:0.45px;
                background:transparent;
            }}
            QLabel#contractLabel {{
                color:{P.CYAN};
                background:transparent;
                font-family:'{FONT_UI}',{FONT_FALL};
                font-size:9px;
                font-weight:800;
                letter-spacing:0.75px;
            }}
            QPushButton#orderCloseButton {{
                background:transparent;
                color:{P.T2};
                border:1px solid transparent;
                border-radius:2px;
                font-family:'{FONT_UI}',{FONT_FALL};
                font-size:12px;
                font-weight:850;
            }}
            QPushButton#orderCloseButton:hover {{
                background:rgba(255,77,106,0.14);
                color:{P.SELL};
                border:1px solid rgba(255,77,106,0.35);
            }}
            QFrame#body {{ background:{P.BG1}; }}
            QWidget#routeGrid,
            QWidget#quantityBlock,
            QWidget#priceBlock,
            QWidget#executionOptions,
            QWidget#fieldBlock,
            QWidget#limitRow,
            QWidget#stopRow,
            QWidget#actionRow {{ background:transparent; }}
            QFrame#quoteStrip,
            QFrame#summaryBox {{
                background:{P.BG0};
                border:1px solid {P.BORDER};
                border-radius:2px;
            }}
            QLabel#warningLabel {{
                color:{P.AMBER};
                background:transparent;
                padding-top:2px;
            }}
            QPushButton#cancelButton {{
                background:{P.BG3};
                color:{P.T1};
                border:1px solid {P.BORDER2};
                border-radius:2px;
                font-family:'{FONT_UI}',{FONT_FALL};
                font-size:10px;
                font-weight:850;
                letter-spacing:0.8px;
                min-height:32px;
            }}
            QPushButton#cancelButton:hover {{
                background:{P.BG4};
                color:{P.T0};
                border-color:{P.T2};
            }}
        """)
        self._refresh_submit_style()

    def _refresh_submit_style(self) -> None:
        side = self._side_group.current() if hasattr(self, "_side_group") else "BUY"
        color = P.BUY if side == "BUY" else P.SELL
        bg = P.BUY_BG if side == "BUY" else P.SELL_BG
        self._submit_btn.setStyleSheet(f"""
            QPushButton#submitButton {{
                background:{bg};
                color:{color};
                border:1px solid {color};
                border-radius:2px;
                font-family:'{FONT_UI}',{FONT_FALL};
                font-size:11px;
                font-weight:900;
                letter-spacing:0.85px;
                min-height:32px;
            }}
            QPushButton#submitButton:hover {{ background:rgba(255,255,255,0.06); }}
            QPushButton#submitButton:pressed {{ background:{P.BG0}; }}
        """)

    # ──────────────────────────────────────────────────────────────────────
    # Defaults and signals
    # ──────────────────────────────────────────────────────────────────────

    def _seed_defaults(self) -> None:
        self._exchange_seg.add_missing_and_select(self._exchange)
        self._contract_label.setText(self._contract_text())

    def _connect_signals(self) -> None:
        self._side_group.currentChanged.connect(self._on_side_changed)
        self._otype_seg.currentChanged.connect(self._on_order_type_changed)
        self._tif_seg.currentChanged.connect(self._on_order_option_changed)
        self._exchange_seg.currentChanged.connect(self._on_order_option_changed)
        self._route_seg.currentChanged.connect(self._on_order_option_changed)
        self._primary_exchange_input.textChanged.connect(lambda *_: self._on_order_option_changed())
        self._account_input.textChanged.connect(lambda *_: self._on_order_option_changed())
        self._outside_rth_chk.toggled.connect(self._on_order_option_changed)
        self._transmit_chk.toggled.connect(self._on_order_option_changed)

        self._qty_spin.valueChanged.connect(self._on_value_changed)
        self._price_spin.valueChanged.connect(self._on_value_changed)
        self._stop_spin.valueChanged.connect(self._on_value_changed)

        self._qty_minus.clicked.connect(lambda: self._qty_spin.setValue(max(1, self._qty_spin.value() - 1)))
        self._qty_plus.clicked.connect(lambda: self._qty_spin.setValue(self._qty_spin.value() + 1))
        self._snap_limit_btn.clicked.connect(lambda: self._snap_price_field(self._price_spin))
        self._snap_stop_btn.clicked.connect(lambda: self._snap_price_field(self._stop_spin))
        self._bid_btn.clicked.connect(lambda: self._set_limit_from_quote("bid"))
        self._ask_btn.clicked.connect(lambda: self._set_limit_from_quote("ask"))
        self._mid_btn.clicked.connect(lambda: self._set_limit_from_quote("mid"))

        self._cancel_btn.clicked.connect(self.reject)
        self._submit_btn.clicked.connect(self._handle_submit)

    def _on_side_changed(self, _side: str) -> None:
        self._confirm_stage = 0
        self._refresh_submit_style()
        self._refresh_confirm_btn()
        self._update_summary()

    def _on_order_type_changed(self, _order_type: str) -> None:
        self._confirm_stage = 0
        self._refresh_fields_visibility()
        self._refresh_confirm_btn()
        self._update_summary()
        self._sync_dialog_height()

    def _on_order_option_changed(self, *_args) -> None:
        self._confirm_stage = 0
        self._contract_label.setText(self._contract_text())
        self._refresh_confirm_btn()
        self._update_summary()

    def _on_value_changed(self, *_args) -> None:
        self._confirm_stage = 0
        self._refresh_confirm_btn()
        self._update_summary()

    # ──────────────────────────────────────────────────────────────────────
    # Public update API
    # ──────────────────────────────────────────────────────────────────────

    @Slot(float, float, float, list)
    def update_tick(
        self,
        ltp: float,
        bid: float = 0.0,
        ask: float = 0.0,
        depth: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        previous = self.ltp
        self.ltp = max(0.0, _as_float(ltp, self.ltp))
        self._bid = max(0.0, _as_float(bid, self._bid))
        self._ask = max(0.0, _as_float(ask, self._ask))
        if depth:
            self._depth_buy = list(depth[:5])
            self._depth_sell = list(depth[5:10]) if len(depth) >= 10 else []
        self._update_ltp_header(previous)
        self._update_quote_strip()
        self._update_summary()

    # ──────────────────────────────────────────────────────────────────────
    # UI refresh
    # ──────────────────────────────────────────────────────────────────────

    def _refresh_fields_visibility(self) -> None:
        order_type = self._otype_seg.current()
        show_limit = order_type in ("LIMIT", "STOP LIMIT")
        show_stop = order_type in ("STOP", "STOP LIMIT")
        self._limit_row.setVisible(show_limit)
        self._stop_row.setVisible(show_stop)
        self._bid_btn.setVisible(show_limit)
        self._ask_btn.setVisible(show_limit)
        self._mid_btn.setVisible(show_limit)
        self._snap_limit_btn.setVisible(show_limit)
        self._snap_stop_btn.setVisible(show_stop)
        self._update_summary()

    def _sync_dialog_height(self) -> None:
        self.layout().activate()
        self._container.layout().activate()
        target_h = self.sizeHint().height()
        screen = QApplication.primaryScreen()
        max_h = int(screen.availableGeometry().height() * 0.92) if screen else target_h
        self.setMaximumHeight(max_h)
        self.resize(self.width(), min(target_h, max_h))

    def _refresh_confirm_btn(self) -> None:
        side = self._side_group.current()
        qty = self._qty_spin.value()
        order_type = self._otype_seg.current()
        price_text = self._display_order_price_text()
        if self._confirm_stage == 0:
            self._submit_btn.setText(f"{side} · {order_type}")
            self._confirm_hint.setVisible(False)
        else:
            self._submit_btn.setText(f"CONFIRM {side} · {qty} @ {price_text}")
            self._confirm_hint.setVisible(True)

    def _update_ltp_header(self, previous: float) -> None:
        self._ltp_label.setText(self._format_price(self.ltp) if self.ltp > 0 else "$—")
        if previous > 0 and self.ltp != previous:
            self._ltp_label.flash("up" if self.ltp > previous else "down")
        self._prev_ltp = self.ltp

    def _update_quote_strip(self) -> None:
        self._bid_val.setText(self._format_price(self._bid) if self._bid > 0 else "—")
        self._ask_val.setText(self._format_price(self._ask) if self._ask > 0 else "—")
        spread = self._spread()
        mid = self._mid()
        self._spread_val.setText(self._format_price(spread) if spread > 0 else "—")
        self._mid_val.setText(self._format_price(mid) if mid > 0 else "—")

    def _update_summary(self) -> None:
        price = self._effective_price()
        qty = int(self._qty_spin.value())
        value = qty * price if price > 0 else 0.0
        risk = self._risk_value(price, qty)
        route_text = self._route_text()

        self._est_price_val.setText(self._format_price(price) if price > 0 else "—")
        self._ov_val.setText(f"${value:,.2f}" if value > 0 else "—")
        self._risk_val.setText(f"${risk:,.2f}" if risk > 0 else "—")
        self._route_val.setText(route_text)

        warnings = self._warnings()
        self._warning_label.setText("  ·  ".join(warnings))
        self._warning_label.setVisible(bool(warnings))
        self._refresh_confirm_btn()

    def _warnings(self) -> List[str]:
        warnings: List[str] = []
        order_type = self._otype_seg.current()
        side = self._side_group.current()
        tif = self._tif_seg.current()

        if order_type == "MARKET":
            warnings.append("Market orders can slip; LIMIT is usually safer for swing entries")
        if tif == "GTC":
            warnings.append("GTC stays live until filled/cancelled by broker rules")
        if self._outside_rth_chk.isChecked():
            warnings.append("Outside-RTH liquidity can be thin and spreads can widen")
        if order_type in ("STOP", "STOP LIMIT"):
            stop = self._stop_spin.value()
            ref = self.ltp or self._price_spin.value()
            if stop > 0 and ref > 0:
                if side == "BUY" and stop <= ref:
                    warnings.append("BUY stop is usually above current price")
                if side == "SELL" and stop >= ref:
                    warnings.append("SELL stop is usually below current price")
        if order_type == "STOP LIMIT":
            warnings.append("Stop-limit may not fill if price gaps through the limit")
        return warnings

    # ──────────────────────────────────────────────────────────────────────
    # Calculations
    # ──────────────────────────────────────────────────────────────────────

    def _format_price(self, value: float) -> str:
        return f"${value:,.{self._price_decimals}f}"

    def _spread(self) -> float:
        if self._bid > 0 and self._ask > 0 and self._ask >= self._bid:
            return self._ask - self._bid
        return 0.0

    def _mid(self) -> float:
        if self._bid > 0 and self._ask > 0 and self._ask >= self._bid:
            return _snap_to_tick((self._bid + self._ask) / 2.0, self._tick_size)
        return 0.0

    def _effective_price(self) -> float:
        order_type = self._otype_seg.current()
        if order_type in ("LIMIT", "STOP LIMIT"):
            return float(self._price_spin.value())
        if order_type == "STOP":
            return float(self._stop_spin.value()) or self.ltp
        return self.ltp or self._mid() or self._ask or self._bid

    def _display_order_price_text(self) -> str:
        order_type = self._otype_seg.current()
        if order_type == "MARKET":
            return "MKT"
        if order_type == "STOP":
            return f"STP {self._format_price(self._stop_spin.value())}"
        if order_type == "STOP LIMIT":
            return f"STP {self._format_price(self._stop_spin.value())} / LMT {self._format_price(self._price_spin.value())}"
        return self._format_price(self._price_spin.value())

    def _risk_value(self, price: float, qty: int) -> float:
        order_type = self._otype_seg.current()
        if order_type not in ("STOP", "STOP LIMIT"):
            return 0.0
        stop = self._stop_spin.value()
        if price <= 0 or stop <= 0:
            return 0.0
        return abs(price - stop) * qty

    def _contract_text(self) -> str:
        primary = self._primary_exchange_input.text().strip().upper() if hasattr(self, "_primary_exchange_input") else self._primary_exchange
        exchange = self._exchange_seg.current() if hasattr(self, "_exchange_seg") else self._exchange
        currency = self._currency
        bits = [self._sec_type, exchange, currency]
        if primary:
            bits.append(f"PRIMARY {primary}")
        return " · ".join(bits)

    def _route_text(self) -> str:
        route = self._route_seg.current()
        exchange = self._exchange_seg.current()
        tif = self._tif_seg.current()
        if route == "AUTO":
            return f"{exchange}/{tif}"
        return f"{exchange}/{route}/{tif}"

    def _snap_price_field(self, field: QDoubleSpinBox) -> None:
        field.setValue(_snap_to_tick(float(field.value()), self._tick_size))
        self._update_summary()

    def _set_limit_from_quote(self, source: str) -> None:
        value = 0.0
        if source == "bid":
            value = self._bid
        elif source == "ask":
            value = self._ask
        elif source == "mid":
            value = self._mid()
        if value <= 0:
            value = self.ltp
        if value > 0:
            self._price_spin.setValue(_snap_to_tick(value, self._tick_size))

    # ──────────────────────────────────────────────────────────────────────
    # Submit and validation
    # ──────────────────────────────────────────────────────────────────────

    def _quick_validate(self) -> bool:
        if not self.symbol:
            _show_error("Symbol is required")
            return False
        if self._sec_type != "STK":
            _show_error("This ticket is configured for US stocks only")
            return False
        if self._qty_spin.value() <= 0:
            _show_error("Quantity must be positive")
            return False

        order_type = self._otype_seg.current()
        side = self._side_group.current()
        limit_price = float(self._price_spin.value())
        stop_price = float(self._stop_spin.value())

        if order_type in ("LIMIT", "STOP LIMIT") and limit_price <= 0:
            _show_error("Limit price is required")
            return False
        if order_type in ("STOP", "STOP LIMIT") and stop_price <= 0:
            _show_error("Stop price is required")
            return False
        if order_type == "STOP LIMIT":
            if side == "BUY" and limit_price < stop_price:
                _show_error("For BUY stop-limit, limit price should be >= stop price")
                return False
            if side == "SELL" and limit_price > stop_price:
                _show_error("For SELL stop-limit, limit price should be <= stop price")
                return False

        if order_type in ("LIMIT", "STOP LIMIT"):
            self._snap_price_field(self._price_spin)
        if order_type in ("STOP", "STOP LIMIT"):
            self._snap_price_field(self._stop_spin)
        return True

    def _handle_submit(self) -> None:
        if self._confirm_stage == 0:
            self._confirm_stage = 1
            self._refresh_confirm_btn()
            return
        if not self._quick_validate():
            return
        order_data = self._build_order_data()
        self.order_placed.emit(order_data)
        log.info(
            "[OrderDialog] %s %s %s [%s/%s]",
            order_data.get("transaction_type"),
            order_data.get("quantity"),
            order_data.get("tradingsymbol"),
            order_data.get("exchange"),
            order_data.get("order_type"),
        )
        self.accept()

    def _build_order_data(self) -> Dict[str, Any]:
        ui_order_type = self._otype_seg.current()
        ibkr_order_type = IBKR_ORDER_TYPES.get(ui_order_type, "LMT")
        side = self._side_group.current()
        qty = int(self._qty_spin.value())
        exchange = self._exchange_seg.current()
        currency = self._currency
        tif = self._tif_seg.current()
        route = self._route_seg.current()
        primary_exchange = self._primary_exchange_input.text().strip().upper()
        account = self._account_input.text().strip()
        outside_rth = bool(self._outside_rth_chk.isChecked())
        transmit = bool(self._transmit_chk.isChecked())

        data: Dict[str, Any] = {
            # Existing app/MainWindow compatibility:
            "tradingsymbol": self.symbol,
            "transaction_type": side,
            "quantity": qty,
            "order_type": ibkr_order_type,
            "exchange": exchange,
            "product": "IBKR",
            "variety": "regular",
            "validity": tif,
            "price": 0.0,
            "trigger_price": 0.0,

            # IBKR/ib_insync-friendly aliases:
            "symbol": self.symbol,
            "action": side,
            "totalQuantity": qty,
            "orderType": ibkr_order_type,
            "tif": tif,
            "outsideRth": outside_rth,
            "outside_rth": outside_rth,
            "transmit": transmit,
            "secType": self._sec_type,
            "sec_type": self._sec_type,
            "currency": currency,
            "primaryExchange": primary_exchange,
            "primary_exchange": primary_exchange,
            "route": route,
            "orderRef": f"QULL-SWING-{self.symbol}",
            "tag": "terminal-ibkr-swing",
        }

        if account:
            data["account"] = account

        if ibkr_order_type in ("LMT", "STP LMT"):
            limit_price = _snap_to_tick(float(self._price_spin.value()), self._tick_size)
            data.update({
                "price": limit_price,
                "limit_price": limit_price,
                "lmtPrice": limit_price,
            })
        if ibkr_order_type in ("STP", "STP LMT"):
            stop_price = _snap_to_tick(float(self._stop_spin.value()), self._tick_size)
            data.update({
                "trigger_price": stop_price,
                "stop_price": stop_price,
                "auxPrice": stop_price,
            })

        if route == "ADAPTIVE":
            data["algoStrategy"] = "Adaptive"
            data["algoParams"] = {"adaptivePriority": "Normal"}
        elif route == "DARK/SMART":
            # Kept as a lightweight hint for your broker wrapper; the safest default
            # still routes through SMART unless the wrapper implements this hint.
            data["smartComboRoutingParams"] = {"NonGuaranteed": "1"}

        return data

    # ──────────────────────────────────────────────────────────────────────
    # Window behavior
    # ──────────────────────────────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(0, self._focus_quantity)

    def _focus_quantity(self) -> None:
        edit = self._qty_spin.findChild(QLineEdit, "qt_spinbox_lineedit")
        if edit:
            edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
            edit.selectAll()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self._confirm_stage == 1:
                self._confirm_stage = 0
                self._refresh_confirm_btn()
            else:
                self.reject()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._handle_submit()
            return
        super().keyPressEvent(event)

    def _is_interactive(self, widget) -> bool:
        while widget:
            if isinstance(widget, (QAbstractSpinBox, QComboBox, QAbstractButton, QLineEdit)):
                return True
            widget = widget.parentWidget()
        return False

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._is_interactive(self.childAt(event.pos())):
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_active and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = False
        super().mouseReleaseEvent(event)


class _MiniAction(QPushButton):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFixedHeight(30)
        self.setMinimumWidth(42)
        self.setStyleSheet(f"""
            QPushButton {{
                background:{P.BG3};
                color:{P.T1};
                border:1px solid {P.BORDER2};
                border-radius:2px;
                font-family:'{FONT_UI}',{FONT_FALL};
                font-size:9px;
                font-weight:850;
                letter-spacing:0.6px;
                padding:0 6px;
            }}
            QPushButton:hover {{
                background:{P.BG2};
                color:{P.CYAN};
                border-color:{P.CYAN};
            }}
        """)