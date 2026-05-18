# kite/widgets/order_dialog.py
"""
OrderDialog — Institutional-grade, pure PySide6.

Design:  Institutional Dark Trading Terminal UI  ·  sharp  ·  compact  ·  data-dense
Perf:    Native Qt paint  ·  no WebEngine  ·  no IPC  ·  sub-ms response
API:     100% backward-compatible with old OrderDialog (same Signal, same __init__)

Features
────────
  • Live LTP flash (green/red) via QPropertyAnimation
  • Level II market depth  ─  5 bid × 5 ask with proportional bar fills
  • OFI (Order Flow Imbalance) institutional pressure meter
  • Circuit-limit compact solid progress bar
  • Real-time margin / charges calculator
  • 2-stage confirm guard (click → review → confirm)
  • SL / SL-M trigger price (auto show/hide)
  • Bracket Order (BO) with target, stoploss, trailing-SL
  • AMO & GTT toggles
  • Drag support on a custom sharp terminal window

Public API
──────────
    dialog = OrderDialog(parent, symbol, ltp, order_details, instrument)
    dialog.order_placed.connect(your_slot)   # Signal(dict) — Kite kwargs
    dialog.update_tick(ltp, bid, ask, depth) # call from your WebSocket feed
    dialog.exec()
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple
from decimal import Decimal, ROUND_HALF_UP

from PySide6.QtCore import (
    Qt, Signal, Slot, QPoint, QPropertyAnimation, QEasingCurve,
    QByteArray, Property, QTimer, QRect, QSize
)
from PySide6.QtGui import (
    QColor, QFont, QPainter, QPen, QBrush,
    QPalette, QFontDatabase, QCursor, QKeyEvent
)
from PySide6.QtWidgets import (
    QApplication, QDialog, QWidget, QFrame, QLabel, QPushButton,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QGroupBox,
    QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QSizePolicy
)

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  PALETTE  (single source of truth — tweak here only)
# ─────────────────────────────────────────────────────────────────────────────
class P:
    BG0      = "#050709"   # deepest shell / outside edge
    BG1      = "#0a0d12"   # dialog body
    BG2      = "#0f1318"   # panel / selected surface
    BG3      = "#141920"   # control surface
    BG4      = "#1a2030"   # raised/hover surface
    BORDER   = "#1a2030"
    BORDER2  = "#2a3a50"
    T0       = "#e8f0ff"   # primary text
    T1       = "#a8bcd4"   # secondary text
    T2       = "#5a7090"   # muted labels
    T3       = "#2a3a50"   # disabled / deep muted
    BUY      = "#00d4a8"
    SELL     = "#ff4d6a"
    AMBER    = "#f59e0b"
    CYAN     = "#00d4ff"
    BLUE     = "#00d4ff"
    FLASH_UP = "#00d4a8"
    FLASH_DN = "#ff4d6a"
    SYMBOL   = "#b6c4d6"   # softened symbol text — less distracting than pure white

# Typography rule:
# - Use modern UI fonts for interface text and market numbers.
# - Keep monospace reserved only for raw logs, code, IDs, and technical debug text.
FONT_UI   = "Inter"
FONT_NUM  = "Inter"
FONT_MONO = "Consolas"
FONT_FALL = "'Segoe UI Variable', 'Segoe UI', Arial, sans-serif"

# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
VALID_ORDER_TYPES = ["MARKET", "LIMIT", "SL", "SL-M"]
VALID_PRODUCTS    = ["CNC", "MIS"]
VALID_VARIETIES   = ["regular", "bo", "co"]
VALID_EXCHANGES   = ["NSE", "BSE", "NFO", "MCX", "BFO", "CDS"]
VALID_VALIDITY    = ["DAY", "IOC"]

BROKERAGE_INTRADAY = 0.0003
STT_EQUITY_INTRADAY_SELL = 0.00025
STT_EQUITY_DELIVERY      = 0.001

PRODUCT_MARGIN = {"MIS": 0.20, "NRML": 0.12, "CNC": 1.0}


# ─────────────────────────────────────────────────────────────────────────────
#  SMALL REUSABLE WIDGETS
# ─────────────────────────────────────────────────────────────────────────────

class _Label(QLabel):
    """Pre-styled label."""
    def __init__(self, text="", color=P.T1, size=10, bold=False, parent=None):
        super().__init__(text, parent)
        w = "700" if bold else "500"
        self.setStyleSheet(
            f"color:{color};font-family:'{FONT_UI}',{FONT_FALL};"
            f"font-size:{size}px;font-weight:{w};background:transparent;"
        )


class _SegButton(QPushButton):
    """Segmented selector button — active/inactive state."""
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self._refresh()
        self.toggled.connect(lambda _: self._refresh())

    def _refresh(self):
        active = self.isChecked()
        border_c = P.CYAN if active else P.BORDER
        bg      = P.BG2  if active else P.BG3
        color   = P.T0   if active else P.T2
        self.setStyleSheet(f"""
            QPushButton {{
                background:{bg}; color:{color};
                border:1px solid {border_c}; border-radius:2px;
                font-family:'{FONT_UI}',{FONT_FALL}; font-size:9px;
                font-weight:800; letter-spacing:0.5px;
                padding:4px 3px;
            }}
            QPushButton:hover {{ background:{P.BG2}; color:{P.T0}; }}
        """)


class _SegGroup(QWidget):
    """
    Mutually-exclusive segmented button bar.
    currentChanged(str) emitted on change.
    """
    currentChanged = Signal(str)

    def __init__(self, options: List[str], default: str = "", parent=None):
        super().__init__(parent)
        self._btns: Dict[str, _SegButton] = {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        for opt in options:
            b = _SegButton(opt, self)
            b.clicked.connect(lambda _, o=opt: self._select(o))
            lay.addWidget(b)
            self._btns[opt] = b
        self._select(default or options[0])

    def _select(self, val: str):
        if val not in self._btns:
            return

        for k, b in self._btns.items():
            should_check = (k == val)
            if b.isChecked() != should_check:
                b.setChecked(should_check)
            # Keep visual state deterministic even if checked state is unchanged.
            b._refresh()

        self.currentChanged.emit(val)

    def current(self) -> str:
        for k, b in self._btns.items():
            if b.isChecked():
                return k
        return ""

    def set_current(self, val: str):
        self._select(val)


class _DropdownField(QWidget):
    """
    Styled dropdown selector with SegGroup-compatible API.
    """
    currentChanged = Signal(str)

    def __init__(self, options: List[str], default: str = "", parent=None):
        super().__init__(parent)
        self._combo = QComboBox(self)
        self._combo.addItems(options)
        self._combo.currentTextChanged.connect(self.currentChanged.emit)
        self._combo.setStyleSheet(f"""
            QComboBox {{
                background:{P.BG3}; color:{P.T0};
                border:1px solid {P.BORDER2}; border-radius:2px;
                font-family:'{FONT_UI}',{FONT_FALL};
                font-size:11px; font-weight:700;
                letter-spacing:0.4px;
                padding:5px 28px 5px 8px;
                min-height:20px;
            }}
            QComboBox:hover {{
                background:{P.BG2};
                border:1px solid {P.CYAN};
            }}
            QComboBox:focus {{
                border:1px solid {P.CYAN};
                background:{P.BG2};
            }}
            QComboBox::drop-down {{
                width:20px;
                border:none;
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
                padding:2px;
            }}
        """)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._combo)

        self.set_current(default or options[0])

    def current(self) -> str:
        return self._combo.currentText()

    def set_current(self, val: str):
        idx = self._combo.findText(val)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)


class _NumInput(QDoubleSpinBox):
    """Institutional-styled price/quantity input."""
    def __init__(self, decimals=2, step=0.05, lo=0.0, hi=9_999_999.0, parent=None):
        super().__init__(parent)
        self.setDecimals(decimals)
        self.setSingleStep(step)
        self.setRange(lo, hi)
        self.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setStyleSheet(f"""
            QDoubleSpinBox {{
                background:{P.BG3}; color:{P.T0};
                border:1px solid {P.BORDER2}; border-radius:2px;
                font-family:'{FONT_NUM}',{FONT_FALL};
                font-size:13px; font-weight:650;
                padding:4px 7px;
            }}
            QDoubleSpinBox:focus {{
                border:1px solid {P.CYAN};
                background:{P.BG2};
            }}
        """)


class _IntInput(QSpinBox):
    """Institutional-styled integer (quantity) input."""
    def __init__(self, lo=1, hi=10_000_000, step=1, parent=None):
        super().__init__(parent)
        self.setRange(lo, hi)
        self.setSingleStep(step)
        self.setButtonSymbols(QSpinBox.NoButtons)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setStyleSheet(f"""
            QSpinBox {{
                background:{P.BG3}; color:{P.T0};
                border:1px solid {P.BORDER2}; border-radius:2px;
                font-family:'{FONT_NUM}',{FONT_FALL};
                font-size:13px; font-weight:650;
                padding:4px 7px;
            }}
            QSpinBox:focus {{
                border:1px solid {P.CYAN};
                background:{P.BG2};
            }}
        """)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        line_edit = self.lineEdit()
        if line_edit is not None:
            line_edit.setCursorPosition(len(line_edit.text()))


class _StepButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setFixedSize(24, 28)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setStyleSheet(f"""
            QPushButton {{
                background:{P.BG3}; color:{P.T1};
                border:1px solid {P.BORDER2}; border-radius:2px;
                font-size:14px; font-weight:500;
            }}
            QPushButton:hover {{ background:{P.BG2}; color:{P.T0}; }}
            QPushButton:pressed {{ background:{P.BG0}; }}
        """)


class _SmallBtn(QPushButton):
    def __init__(self, text, color=P.CYAN, parent=None):
        super().__init__(text, parent)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setFixedHeight(28)
        self.setStyleSheet(f"""
            QPushButton {{
                background:rgba(0,212,255,0.08); color:{color};
                border:1px solid {color}; border-radius:2px;
                font-family:'{FONT_UI}',{FONT_FALL};
                font-size:9px; font-weight:700; letter-spacing:0.8px;
                padding:0 8px;
            }}
            QPushButton:hover {{ background:rgba(0,212,255,0.16); }}
        """)


class _Toggle(QCheckBox):
    """Compact terminal checkbox toggle."""
    def __init__(self, label="", parent=None):
        super().__init__(label, parent)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setStyleSheet(f"""
            QCheckBox {{
                color:{P.T1}; spacing:6px;
                font-family:'{FONT_UI}',{FONT_FALL};
                font-size:10px; font-weight:800; letter-spacing:0.8px;
                background:transparent;
            }}
            QCheckBox:hover {{ color:{P.T0}; }}
            QCheckBox::indicator {{
                width:14px; height:14px;
                border-radius:2px;
                background:{P.BG3};
                border:1px solid {P.BORDER2};
            }}
            QCheckBox::indicator:checked {{
                background:{P.CYAN};
                border:1px solid {P.CYAN};
            }}
        """)


# ─────────────────────────────────────────────────────────────────────────────
#  LTP FLASH WIDGET  —  animates color from flash → normal
# ─────────────────────────────────────────────────────────────────────────────

class _LTPLabel(QLabel):
    """LTP with animated green/red flash on tick change."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = QColor(P.T0)
        self._anim  = QPropertyAnimation(self, b"_flash_color", self)
        self._anim.setDuration(500)
        self._anim.setEasingCurve(QEasingCurve.OutQuad)
        self._base_style = (
            f"font-family:'{FONT_NUM}',{FONT_FALL};"
            f"font-size:21px;font-weight:750;background:transparent;"
        )
        self._apply_color()

    def flash(self, direction: str):
        start = QColor(P.FLASH_UP if direction == "up" else P.FLASH_DN)
        end   = QColor(P.T0)
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.start()

    def _get_flash_color(self) -> QColor:
        return self._color

    def _set_flash_color(self, c: QColor):
        self._color = c
        self._apply_color()

    _flash_color = Property(QColor, _get_flash_color, _set_flash_color)

    def _apply_color(self):
        self.setStyleSheet(self._base_style + f"color:{self._color.name()};")


# ─────────────────────────────────────────────────────────────────────────────
#  DEPTH BAR WIDGET  (custom paint — proportional fill behind numbers)
# ─────────────────────────────────────────────────────────────────────────────

class _DepthRow(QWidget):
    """One row: [orders | qty | bid_price] [ask_price | qty | orders]"""

    def __init__(self, is_buy=True, parent=None):
        super().__init__(parent)
        self.is_buy  = is_buy
        self.pct     = 0.0
        self.price   = 0.0
        self.qty     = 0
        self.orders  = 0
        self._color  = QColor(P.BUY if is_buy else P.SELL)
        self.setFixedHeight(24)

        self._l_orders = _Label("", P.T2, 9)
        self._l_qty    = _Label("", P.BUY if is_buy else P.SELL, 11, bold=True)
        self._l_price  = _Label("", P.BUY if is_buy else P.SELL, 11, bold=True)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(0)

        if is_buy:
            lay.addWidget(self._l_orders)
            lay.addStretch()
            lay.addWidget(self._l_qty)
            lay.addSpacing(12)
            lay.addWidget(self._l_price)
        else:
            lay.addWidget(self._l_price)
            lay.addSpacing(12)
            lay.addWidget(self._l_qty)
            lay.addStretch()
            lay.addWidget(self._l_orders)

    def update_data(self, price: float, qty: int, orders: int, pct: float):
        self.price  = price
        self.qty    = qty
        self.orders = orders
        self.pct    = min(1.0, max(0.0, pct))
        self._l_orders.setText(str(orders))
        self._l_qty.setText(f"{qty:,}")
        self._l_price.setText(f"₹{price:,.2f}")
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.pct <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w = int(self.width() * self.pct)
        c = QColor(P.BUY if self.is_buy else P.SELL)
        c.setAlpha(18)
        r = QRect(self.width() - w if self.is_buy else 0, 0, w, self.height())
        p.fillRect(r, c)
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
#  OFI PRESSURE BAR  (custom paint)
# ─────────────────────────────────────────────────────────────────────────────

class _PressureBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(6)
        self._bid_pct = 0.5

    def set_pct(self, bid_pct: float):
        self._bid_pct = max(0.0, min(1.0, bid_pct))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w = self.width()
        h = self.height()
        bw = int(w * self._bid_pct)
        # buy side
        p.fillRect(QRect(0, 0, bw, h), QColor(P.BUY))
        # sell side
        p.fillRect(QRect(bw, 0, w - bw, h), QColor(P.SELL))
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
#  CIRCUIT BAR  (compact solid progress bar)
# ─────────────────────────────────────────────────────────────────────────────

class _CircuitBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(5)
        self._pct = 0.5

    def set_pct(self, pct: float):
        self._pct = max(0.0, min(1.0, pct))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.fillRect(self.rect(), QColor(P.BG3))
        w = int(self.width() * self._pct)
        if w > 0:
            fill = P.SELL if self._pct < 0.35 else P.AMBER if self._pct < 0.68 else P.BUY
            p.fillRect(QRect(0, 0, w, self.height()), QColor(fill))
        p.setPen(QPen(QColor(P.BORDER), 1))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class OrderDialog(QDialog):
    """
    Institutional order dialog — pure PySide6, zero-latency.

    Signals
    ───────
    order_placed(dict)  — emitted with Kite place_order() kwargs on confirm
    """

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
        self.setWindowTitle("Order Dialog")
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setMinimumWidth(480)
        self.resize(480, 540)

        self.symbol     = symbol.strip().upper()
        self.ltp        = max(0.0, float(ltp))
        self.instrument = instrument or {}
        self._ltp_fetcher = ltp_fetcher
        od              = order_details or {}

        self._exchange     = self._infer_exchange(od, instrument)
        requested_product = str(od.get("product", "CNC")).upper()
        self._product_type = requested_product if requested_product in VALID_PRODUCTS else "CNC"
        self._order_type   = od.get("order_type", "LIMIT")
        self._variety      = od.get("variety", "regular")
        self._is_buy       = od.get("transaction_type", "BUY").upper() == "BUY"
        self._lot_size     = int(self.instrument.get("lot_size") or 1)
        self._tick_size    = float(self.instrument.get("tick_size") or 0.05)
        self._default_qty  = int(od.get("quantity") or self._lot_size)
        if self._default_qty <= 0:
            self._default_qty = self._lot_size
        if self._default_qty % self._lot_size != 0:
            self._default_qty = max(self._lot_size, (self._default_qty // self._lot_size) * self._lot_size)
        self._circuit_low  = float(self.instrument.get("lower_circuit_limit") or ltp * 0.90)
        self._circuit_high = float(self.instrument.get("upper_circuit_limit") or ltp * 1.10)
        self._avail_margin = float(od.get("available_margin") or 0.0)

        self._drag_active = False
        self._drag_offset = QPoint()
        self._confirm_stage = 0   # 0=idle  1=awaiting confirm

        # Depth data (5 levels each)
        self._depth_buy : List[Dict]  = []
        self._depth_sell: List[Dict]  = []
        self._prev_ltp  = self.ltp

        self._setup_ui()
        self._apply_global_styles()
        self._connect_signals()
        self._refresh_fields_visibility()
        self._refresh_confirm_btn()
        self._update_summary()
        self._sync_dialog_height()

    # ─────────────────────────────────────────────────────────────────────────
    #  DEFAULTS INFERENCE
    # ─────────────────────────────────────────────────────────────────────────

    def _infer_exchange(self, od: Dict, instr: Optional[Dict]) -> str:
        if od.get("exchange"):   return od["exchange"].upper()
        if instr and instr.get("exchange"): return instr["exchange"].upper()
        return "NSE"

    def _infer_product(self, instr: Optional[Dict]) -> str:
        # CNC is the default in this dialog; NRML is intentionally excluded from UI options.
        return "CNC"

    # ─────────────────────────────────────────────────────────────────────────
    #  UI CONSTRUCTION
    # ─────────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._container = QFrame()
        self._container.setObjectName("dialogContainer")
        outer.addWidget(self._container)

        root = QVBoxLayout(self._container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._build_form_panel())

    # ── HEADER ───────────────────────────────────────────────────────────────

    def _build_header(self) -> QFrame:
        f = QFrame()
        f.setObjectName("header")
        f.setFixedHeight(38)
        f.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))

        h = QHBoxLayout(f)
        h.setContentsMargins(10, 0, 6, 0)
        h.setSpacing(8)

        ticket = _Label("ORDER TICKET", P.AMBER, 9, bold=True)
        ticket.setObjectName("ticketBadge")
        h.addWidget(ticket)

        self._sym_label = _Label(self.symbol or "—", P.SYMBOL, 15, bold=True)
        self._sym_label.setObjectName("symbolTitle")
        self._sym_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        h.addWidget(self._sym_label)

        exch = _Label(self._exchange, P.T2, 9, bold=True)
        exch.setObjectName("exchangePill")
        h.addWidget(exch)

        h.addStretch()

        ltp_key = _Label("LTP", P.T2, 9, bold=True)
        h.addWidget(ltp_key)
        self._ltp_label = _LTPLabel()
        self._ltp_label.setText(f"₹{self.ltp:,.2f}" if self.ltp > 0 else "₹—")
        h.addWidget(self._ltp_label)

        self._close_btn = QPushButton("✕")
        self._close_btn.setObjectName("orderCloseButton")
        self._close_btn.setFixedSize(24, 22)
        self._close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._close_btn.clicked.connect(self.reject)
        h.addWidget(self._close_btn)

        return f

    # ── FORM PANEL ───────────────────────────────────────────────────────────

    def _build_form_panel(self) -> QFrame:
        f = QFrame()
        f.setObjectName("formPanel")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        # BUY / SELL
        self._side_group = _SegGroup(["BUY", "SELL"],
                                     "BUY" if self._is_buy else "SELL")
        self._side_group.setFixedHeight(30)
        # Override with big bold buttons
        for key, btn in self._side_group._btns.items():
            btn.setFixedHeight(30)
            btn.setStyleSheet(self._side_style(key, key == ("BUY" if self._is_buy else "SELL")))
        self._side_group.currentChanged.connect(self._on_side_changed)
        lay.addWidget(self._side_group)

        # PRODUCT / VALIDITY / ORDER TYPE (compact grid to save vertical space)
        self._product_seg = _DropdownField(VALID_PRODUCTS, self._product_type)
        self._product_seg.currentChanged.connect(self._update_summary)

        self._validity_seg = _DropdownField(VALID_VALIDITY, "DAY")

        self._otype_seg = _DropdownField(VALID_ORDER_TYPES, self._order_type)
        self._otype_seg.currentChanged.connect(self._refresh_fields_visibility)

        top_grid = QGridLayout()
        top_grid.setContentsMargins(0, 0, 0, 0)
        top_grid.setHorizontalSpacing(8)
        top_grid.setVerticalSpacing(4)
        top_grid.addWidget(self._labeled_block("PRODUCT", self._product_seg), 0, 0)
        top_grid.addWidget(self._labeled_block("ORDER TYPE", self._otype_seg), 0, 1)
        top_grid.addWidget(self._labeled_block("VALIDITY", self._validity_seg), 0, 2)
        top_grid.setColumnStretch(0, 1)
        top_grid.setColumnStretch(1, 2)
        top_grid.setColumnStretch(2, 1)
        lay.addLayout(top_grid)

        qty_block = QWidget()
        qty_block.setObjectName("qtyBlock")
        qty_block_lay = QVBoxLayout(qty_block)
        qty_block_lay.setContentsMargins(0, 0, 0, 0)
        qty_block_lay.setSpacing(3)

        # QUANTITY
        qty_hdr = QWidget()
        qty_hdr.setObjectName("qtyHeader")
        qh = QHBoxLayout(qty_hdr)
        qh.setContentsMargins(0, 0, 0, 0)
        qh.addWidget(self._section_label("QUANTITY"))
        qh.addStretch()
        qh.addWidget(_Label(f"LOT: {self._lot_size}", P.CYAN, 9))
        qty_block_lay.addWidget(qty_hdr)

        qty_row = QHBoxLayout()
        qty_row.setSpacing(3)
        self._qty_minus = _StepButton("−")
        self._qty_plus  = _StepButton("+")
        self._qty_spin  = _IntInput(1, 10_000_000, self._lot_size)
        self._qty_spin.setValue(self._default_qty)
        qty_row.addWidget(self._qty_minus)
        qty_row.addWidget(self._qty_spin, 1)
        qty_row.addWidget(self._qty_plus)
        qty_row.addWidget(_Label("SHR", P.T2, 9))
        qty_block_lay.addLayout(qty_row)

        price_block = QWidget()
        price_block.setObjectName("priceBlock")
        price_block_lay = QVBoxLayout(price_block)
        price_block_lay.setContentsMargins(0, 0, 0, 0)
        price_block_lay.setSpacing(3)

        # PRICE
        self._price_hdr = QWidget()
        self._price_hdr.setObjectName("priceHeader")
        ph = QHBoxLayout(self._price_hdr)
        ph.setContentsMargins(0, 0, 0, 0)
        ph.addWidget(self._section_label("PRICE"))
        ph.addStretch()
        ph.addWidget(_Label(f"TICK ₹{self._tick_size}", P.T2, 9))
        price_block_lay.addWidget(self._price_hdr)

        self._price_row_w = QWidget()
        self._price_row_w.setObjectName("priceRow")
        pr = QHBoxLayout(self._price_row_w)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.setSpacing(3)
        self._price_minus = _StepButton("−")
        self._price_plus  = _StepButton("+")
        self._price_spin  = _NumInput(2, self._tick_size, 0.05, 999_999.95)
        self._price_spin.setValue(self.ltp if self.ltp > 0 else 1.0)
        self._ltp_btn = _SmallBtn("LTP")
        pr.addWidget(self._price_minus)
        pr.addWidget(self._price_spin, 1)
        pr.addWidget(self._price_plus)
        pr.addWidget(self._ltp_btn)
        price_block_lay.addWidget(self._price_row_w)

        field_grid = QGridLayout()
        field_grid.setContentsMargins(0, 0, 0, 0)
        field_grid.setHorizontalSpacing(8)
        field_grid.setVerticalSpacing(4)
        field_grid.addWidget(qty_block, 0, 0)
        field_grid.addWidget(price_block, 0, 1)
        field_grid.setColumnStretch(0, 1)
        field_grid.setColumnStretch(1, 1)
        lay.addLayout(field_grid)

        # TRIGGER PRICE
        self._trig_hdr = _Label("TRIGGER PRICE", P.T2, 9, bold=True)
        self._trig_hdr.setStyleSheet(
            f"color:{P.T2};font-family:'{FONT_UI}',{FONT_FALL};"
            f"font-size:9px;font-weight:700;letter-spacing:0.8px;background:transparent;margin-top:2px;"
        )
        lay.addWidget(self._trig_hdr)

        self._trig_row_w = QWidget()
        self._trig_row_w.setObjectName("triggerRow")
        tr = QHBoxLayout(self._trig_row_w)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.setSpacing(3)
        self._trig_spin = _NumInput(2, self._tick_size, 0.05, 999_999.95)
        self._trig_spin.setValue(self.ltp * 0.98 if self.ltp > 0 else 1.0)
        tr.addWidget(self._trig_spin, 1)
        tr.addWidget(_Label("₹", P.T2, 10))
        lay.addWidget(self._trig_row_w)

        # TOGGLES (AMO  GTT  VARIETY)
        tog_row = QHBoxLayout()
        tog_row.setContentsMargins(0, 2, 0, 0)
        tog_row.setSpacing(10)
        self._amo_chk = _Toggle("AMO")
        self._gtt_chk = _Toggle("GTT")
        self._bo_chk  = _Toggle("BO")
        tog_row.addWidget(self._amo_chk)
        tog_row.addWidget(self._gtt_chk)
        tog_row.addWidget(self._bo_chk)
        tog_row.addStretch()
        lay.addLayout(tog_row)

        # BO SECTION
        self._bo_section = self._build_bo_section()
        lay.addWidget(self._bo_section)

        # SUMMARY BOX
        lay.addWidget(self._build_summary_box())

        # CIRCUIT BAR
        lay.addWidget(self._build_circuit_row())

        lay.addStretch()

        # SUBMIT BUTTON
        self._submit_btn = QPushButton("▲  PLACE BUY ORDER")
        self._submit_btn.setFixedHeight(30)
        self._submit_btn.setCursor(QCursor(Qt.PointingHandCursor))
        lay.addWidget(self._submit_btn)

        self._confirm_hint = _Label("Click again to confirm  ·  ESC to cancel", P.T2, 9)
        self._confirm_hint.setAlignment(Qt.AlignCenter)
        self._confirm_hint.setVisible(False)
        lay.addWidget(self._confirm_hint)

        return f

    def _labeled_block(self, label: str, widget: QWidget) -> QWidget:
        block = QWidget()
        block.setObjectName("fieldBlock")
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(self._section_label(label))
        layout.addWidget(widget)
        return block

    def _build_bo_section(self) -> QGroupBox:
        g = QGroupBox("BRACKET ORDER")
        g.setVisible(False)
        g.setStyleSheet(
            f"QGroupBox{{color:{P.AMBER};border:1px solid {P.BORDER};"
            f"border-radius:2px;margin-top:6px;padding-top:4px;"
            f"font-family:'{FONT_UI}',{FONT_FALL};font-size:9px;font-weight:800;letter-spacing:0.8px;}}"
            f"QGroupBox::title{{subcontrol-origin:margin;left:8px;padding:0 4px;}}"
        )
        form = QFormLayout(g)
        form.setVerticalSpacing(6)
        form.setHorizontalSpacing(8)

        self._target_spin = _NumInput(2, self._tick_size, 0.05, 999_999.95)
        self._target_spin.setValue(self.ltp * 1.02 if self.ltp > 0 else 1.0)
        self._sl_spin = _NumInput(2, self._tick_size, 0.05, 999_999.95)
        self._sl_spin.setValue(self.ltp * 0.98 if self.ltp > 0 else 1.0)
        self._trailing_chk = QCheckBox("Trailing SL")
        self._trailing_chk.setStyleSheet(
            f"QCheckBox{{"
            f"color:{P.T1};font-family:'{FONT_UI}',{FONT_FALL};font-size:10px;"
            f"spacing:6px;background:transparent;}}"
            f"QCheckBox::indicator{{"
            f"width:14px;height:14px;border-radius:2px;border:1px solid {P.BORDER2};"
            f"background:{P.BG3};}}"
            f"QCheckBox::indicator:checked{{"
            f"background:{P.CYAN};border:1px solid {P.CYAN};}}"
        )

        form.addRow(_Label("Target:", P.T1, 10), self._target_spin)
        form.addRow(_Label("Stop-Loss:", P.T1, 10), self._sl_spin)
        form.addRow("", self._trailing_chk)
        return g

    def _build_summary_box(self) -> QFrame:
        f = QFrame()
        f.setObjectName("summaryBox")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(5)

        def row(key: str):
            r = QHBoxLayout()
            r.setContentsMargins(0, 0, 0, 0)
            k = _Label(key, P.T2, 9)
            k.setFixedWidth(110)
            v = _Label("—", P.T0, 12, bold=True)
            r.addWidget(k)
            r.addStretch()
            r.addWidget(v)
            lay.addLayout(r)
            return v

        self._ov_val    = row("ORDER VALUE")
        self._mreq_val  = row("MARGIN REQ.")
        self._mavail_val = row("AVAILABLE")

        self._margin_warn = _Label("", P.SELL, 10)
        self._margin_warn.setStyleSheet(
            f"color:{P.SELL};background:rgba(255,77,109,0.07);"
            f"border:1px solid rgba(255,77,109,0.2);border-radius:2px;"
            f"padding:3px 7px;font-family:'{FONT_UI}',{FONT_FALL};font-size:10px;"
        )
        self._margin_warn.setVisible(False)
        lay.addWidget(self._margin_warn)
        return f

    def _build_circuit_row(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 2, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(_Label("CIRCUIT", P.T2, 9))
        self._circ_low_lbl = _Label(f"▼{self._circuit_low:,.0f}", P.SELL, 10, bold=True)
        lay.addWidget(self._circ_low_lbl)
        self._circuit_bar = _CircuitBar()
        lay.addWidget(self._circuit_bar, 1)
        self._circ_high_lbl = _Label(f"▲{self._circuit_high:,.0f}", P.BUY, 10, bold=True)
        lay.addWidget(self._circ_high_lbl)
        self._update_circuit()
        return w

    # ── DEPTH PANEL ──────────────────────────────────────────────────────────

    def _build_depth_panel(self) -> QFrame:
        f = QFrame()
        f.setObjectName("depthPanel")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(0)

        # Title
        title_row = QHBoxLayout()
        title_row.addWidget(_Label("MARKET DEPTH", P.T0, 10, bold=True))
        title_row.addSpacing(8)
        title_row.addWidget(_Label("LEVEL II", P.T2, 9))
        title_row.addStretch()
        lay.addLayout(title_row)
        lay.addSpacing(6)

        # Column headers
        hdr = QWidget()
        hdr.setFixedHeight(20)
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(4, 0, 4, 0)
        hdr_lay.setSpacing(0)
        for t in ["ORDERS", "QTY", "BID ₹"]:
            lbl = _Label(t, P.T2, 9)
            hdr_lay.addWidget(lbl)
            if t != "BID ₹": hdr_lay.addStretch()
        # separator
        hdr_lay.addWidget(_sep_v())
        for t in ["ASK ₹", "QTY", "ORDERS"]:
            if t != "ASK ₹": hdr_lay.addStretch()
            lbl = _Label(t, P.T2, 9)
            hdr_lay.addWidget(lbl)
        lay.addWidget(hdr)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{P.BORDER};background:{P.BORDER};border:none;max-height:1px;")
        lay.addWidget(sep)

        # 5 depth rows
        self._buy_rows : List[_DepthRow] = []
        self._sell_rows: List[_DepthRow] = []

        for i in range(5):
            row_w = QWidget()
            row_w.setFixedHeight(24)
            row_lay = QHBoxLayout(row_w)
            row_lay.setContentsMargins(0, 0, 0, 0)
            row_lay.setSpacing(0)

            br = _DepthRow(is_buy=True)
            sr = _DepthRow(is_buy=False)
            row_lay.addWidget(br, 1)
            row_lay.addWidget(_sep_v())
            row_lay.addWidget(sr, 1)
            self._buy_rows.append(br)
            self._sell_rows.append(sr)
            lay.addWidget(row_w)

            if i < 4:
                sep2 = QFrame()
                sep2.setFrameShape(QFrame.HLine)
                sep2.setStyleSheet(f"color:{P.BORDER};background:{P.BORDER};border:none;max-height:1px;")
                lay.addWidget(sep2)

        # Totals row
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.HLine)
        sep3.setStyleSheet(f"color:{P.BORDER};background:{P.BORDER};border:none;max-height:1px;margin-top:1px;")
        lay.addWidget(sep3)

        tot_w = QWidget()
        tot_w.setFixedHeight(26)
        tot_lay = QHBoxLayout(tot_w)
        tot_lay.setContentsMargins(4, 0, 4, 0)
        tot_lay.setSpacing(0)
        self._total_bid_lbl = _Label("0", P.BUY, 11, bold=True)
        self._total_ask_lbl = _Label("0", P.SELL, 11, bold=True)
        tot_lay.addWidget(_Label("TOTAL BID", P.T2, 9))
        tot_lay.addStretch()
        tot_lay.addWidget(self._total_bid_lbl)
        tot_lay.addWidget(_sep_v())
        tot_lay.addWidget(self._total_ask_lbl)
        tot_lay.addStretch()
        tot_lay.addWidget(_Label("TOTAL ASK", P.T2, 9))
        lay.addWidget(tot_w)

        lay.addSpacing(10)

        # OFI SECTION
        ofi_title = QHBoxLayout()
        ofi_title.addWidget(_Label("BID PRESSURE", P.T2, 9))
        ofi_title.addStretch()
        ofi_title.addWidget(_Label("ORDER FLOW IMBALANCE", P.AMBER, 9, bold=True))
        ofi_title.addStretch()
        ofi_title.addWidget(_Label("ASK PRESSURE", P.T2, 9))
        lay.addLayout(ofi_title)
        lay.addSpacing(5)

        self._pressure_bar = _PressureBar()
        lay.addWidget(self._pressure_bar)
        lay.addSpacing(8)

        ofi_row = QHBoxLayout()
        self._ofi_key   = _Label("OFI", P.T2, 9)
        self._ofi_val   = _Label("+0.0%", P.BUY, 15, bold=True)
        self._ofi_desc  = _Label("NEUTRAL BUY FLOW", P.T2, 10)
        self._ofi_ratio = _Label("", P.T2, 9)
        ofi_row.addWidget(self._ofi_key)
        ofi_row.addSpacing(8)
        ofi_row.addWidget(self._ofi_val)
        ofi_row.addSpacing(8)
        ofi_row.addWidget(self._ofi_desc)
        ofi_row.addStretch()
        ofi_row.addWidget(self._ofi_ratio)
        lay.addLayout(ofi_row)

        lay.addStretch()
        return f

    # ─────────────────────────────────────────────────────────────────────────
    #  STYLES
    # ─────────────────────────────────────────────────────────────────────────

    def _apply_global_styles(self):
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
                font-weight:800;
                letter-spacing:0.9px;
                background:transparent;
            }}
            QLabel#symbolTitle {{
                color:{P.SYMBOL};
                font-family:'{FONT_UI}',{FONT_FALL};
                font-size:15px;
                font-weight:800;
                letter-spacing:0.4px;
                background:transparent;
            }}
            QLabel#exchangePill {{
                color:{P.CYAN};
                background:rgba(0,212,255,0.07);
                border:1px solid rgba(0,212,255,0.20);
                border-radius:2px;
                padding:2px 6px;
                font-family:'{FONT_UI}',{FONT_FALL};
                font-size:9px;
                font-weight:800;
                letter-spacing:0.8px;
            }}
            QPushButton#orderCloseButton {{
                background:transparent;
                color:{P.T2};
                border:1px solid transparent;
                border-radius:2px;
                font-family:'{FONT_UI}',{FONT_FALL};
                font-size:12px;
                font-weight:800;
            }}
            QPushButton#orderCloseButton:hover {{
                background:rgba(255,77,106,0.14);
                color:{P.SELL};
                border:1px solid rgba(255,77,106,0.35);
            }}
            QFrame#formPanel {{
                background:{P.BG1};
            }}
            QFrame#depthPanel {{
                background:{P.BG1};
            }}
            QFrame#summaryBox {{
                background:{P.BG0};
                border:1px solid {P.BORDER};
                border-radius:2px;
            }}
            QGroupBox {{
                color:{P.AMBER};
                font-family:'{FONT_UI}',{FONT_FALL};
                font-size:9px; font-weight:800; letter-spacing:0.8px;
                border:1px solid {P.BORDER}; border-radius:2px;
                margin-top:6px; padding-top:6px;
                background:{P.BG1};
            }}
            QGroupBox::title {{
                subcontrol-origin:margin; left:8px; padding:0 4px;
                background:{P.BG1};
            }}
            QLabel, QCheckBox {{
                background:transparent;
            }}
            QWidget#fieldBlock,
            QWidget#qtyBlock,
            QWidget#qtyHeader,
            QWidget#priceBlock,
            QWidget#priceHeader,
            QWidget#priceRow,
            QWidget#triggerRow {{
                background:transparent;
            }}
        """)
        self._refresh_submit_style()

    def _section_label(self, text: str) -> QLabel:
        lbl = _Label(text, P.T2, 9, bold=True)
        lbl.setStyleSheet(
            f"color:{P.T2};font-family:'{FONT_UI}',{FONT_FALL};"
            f"font-size:9px;font-weight:700;letter-spacing:0.5px;background:transparent;margin-top:2px;"
        )
        return lbl

    def _side_style(self, side: str, active: bool) -> str:
        color = P.BUY if side == "BUY" else P.SELL
        if not active:
            return (
                f"QPushButton{{background:{P.BG3};color:{P.T1};"
                f"border:1px solid {P.BORDER2};border-radius:2px;font-family:'{FONT_UI}',{FONT_FALL};"
                f"font-size:12px;font-weight:800;letter-spacing:0.6px;padding:0 8px;}}"
                f"QPushButton:hover{{background:{P.BG4};color:{P.T0};border-color:{color};}}"
            )
        text_color = "#04110d" if side == "BUY" else "#ffffff"
        return (
            f"QPushButton{{background:{color};color:{text_color};"
            f"border:1px solid {color};border-radius:2px;font-family:'{FONT_UI}',{FONT_FALL};"
            f"font-size:12px;font-weight:900;letter-spacing:0.8px;padding:0 8px;}}"
            f"QPushButton:hover{{border:1px solid {P.T0};}}"
        )

    def _refresh_submit_style(self):
        side = self._side_group.current() if hasattr(self, "_side_group") else "BUY"
        side_color = P.BUY if side == "BUY" else P.SELL
        text_color = "#03110d" if side == "BUY" else "#ffffff"
        border_color = P.AMBER if self._confirm_stage == 1 else side_color
        label_weight = "900" if self._confirm_stage == 1 else "800"
        self._submit_btn.setStyleSheet(f"""
            QPushButton {{
                background:{side_color};
                color:{text_color};
                border:1px solid {border_color};
                border-radius:2px;
                font-family:'{FONT_UI}',{FONT_FALL};
                font-size:12px;
                font-weight:{label_weight};
                letter-spacing:0.7px;
            }}
            QPushButton:hover {{
                border:1px solid {P.T0};
            }}
            QPushButton:pressed {{
                background:{P.BG4};
                color:{side_color};
                border:1px solid {side_color};
            }}
        """)

    # ─────────────────────────────────────────────────────────────────────────
    #  SIGNAL CONNECTIONS
    # ─────────────────────────────────────────────────────────────────────────

    def _connect_signals(self):
        # Side toggle — restyle buttons
        self._side_group.currentChanged.connect(self._on_side_changed)

        # Field visibility
        self._otype_seg.currentChanged.connect(self._refresh_fields_visibility)

        # BO toggle
        self._bo_chk.toggled.connect(self._toggle_bo)
        self._bo_chk.toggled.connect(lambda _: self._refresh_fields_visibility())

        # Summary recalc
        self._qty_spin.valueChanged.connect(self._update_summary)
        self._price_spin.valueChanged.connect(self._update_summary)
        self._product_seg.currentChanged.connect(self._update_summary)
        self._price_spin.editingFinished.connect(lambda: self._snap_price_field(self._price_spin))
        self._trig_spin.editingFinished.connect(lambda: self._snap_price_field(self._trig_spin))
        self._target_spin.editingFinished.connect(lambda: self._snap_price_field(self._target_spin))
        self._sl_spin.editingFinished.connect(lambda: self._snap_price_field(self._sl_spin))

        # Step buttons
        self._qty_minus.clicked.connect(lambda: self._qty_spin.setValue(
            max(1, self._qty_spin.value() - self._lot_size)))
        self._qty_plus.clicked.connect(lambda: self._qty_spin.setValue(
            self._qty_spin.value() + self._lot_size))
        self._price_minus.clicked.connect(lambda: self._price_spin.setValue(
            max(0.05, round(self._price_spin.value() - self._tick_size, 2))))
        self._price_plus.clicked.connect(lambda: self._price_spin.setValue(
            round(self._price_spin.value() + self._tick_size, 2)))

        # LTP fill
        self._ltp_btn.clicked.connect(self._on_ltp_clicked)

        # Submit
        self._submit_btn.clicked.connect(self._handle_submit)

    # ─────────────────────────────────────────────────────────────────────────
    #  SLOT HANDLERS
    # ─────────────────────────────────────────────────────────────────────────


    def _on_ltp_clicked(self):
        """Fetch latest LTP (if fetcher available) and fill it into price."""
        latest_ltp = self.ltp
        if callable(self._ltp_fetcher) and self.symbol:
            try:
                fetched_ltp = float(self._ltp_fetcher(self.symbol) or 0.0)
                if fetched_ltp > 0:
                    latest_ltp = fetched_ltp
            except Exception as e:
                log.warning(f"[OrderDialog] LTP fetch failed for {self.symbol}: {e}")

        if latest_ltp > 0:
            previous = self.ltp
            self.ltp = latest_ltp
            self._update_ltp_header(previous)
            self._price_spin.setValue(self._round_to_tick(latest_ltp))
            self._update_circuit()
            self._update_summary()

    def _update_ltp_header(self, previous: Optional[float] = None) -> None:
        if not hasattr(self, "_ltp_label"):
            return
        self._ltp_label.setText(f"₹{self.ltp:,.2f}" if self.ltp > 0 else "₹—")
        if previous is not None and self.ltp > 0 and abs(self.ltp - previous) > 1e-9:
            self._ltp_label.flash("up" if self.ltp > previous else "down")

    def _round_to_tick(self, value: float) -> float:
        tick = Decimal(str(self._tick_size if self._tick_size > 0 else 0.05))
        price = Decimal(str(max(0.0, float(value))))
        rounded = (price / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick
        return float(rounded.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def _snap_price_field(self, field: _NumInput):
        snapped = max(0.05, self._round_to_tick(field.value()))
        if abs(field.value() - snapped) > 1e-9:
            field.setValue(snapped)

    def _on_side_changed(self, side: str):
        for k, btn in self._side_group._btns.items():
            btn.setStyleSheet(self._side_style(k, k == side))
        self._confirm_stage = 0
        self._refresh_confirm_btn()
        self._refresh_submit_style()

    def _toggle_bo(self, checked: bool):
        self._bo_section.setVisible(checked)
        if checked:
            self._otype_seg.set_current("LIMIT")

    def _refresh_fields_visibility(self):
        ot = self._otype_seg.current()
        bo = self._bo_chk.isChecked()
        show_price  = ot in ("LIMIT", "SL") and not bo
        show_trig   = ot in ("SL", "SL-M")

        self._price_hdr.setVisible(show_price)
        self._price_row_w.setVisible(show_price)
        self._trig_hdr.setVisible(show_trig)
        self._trig_row_w.setVisible(show_trig)
        self._sync_dialog_height()

    def _sync_dialog_height(self):
        """
        Keep dialog height tightly fit to visible content while allowing growth
        when optional sections (e.g. BO) are shown.
        """
        self.layout().activate()
        self._container.layout().activate()
        target_h = self.sizeHint().height()
        max_h = int(QApplication.primaryScreen().availableGeometry().height() * 0.92)
        self.setMaximumHeight(max_h)
        self.resize(self.width(), min(target_h, max_h))

    def _refresh_confirm_btn(self):
        side = self._side_group.current()
        if self._confirm_stage == 0:
            self._submit_btn.setText(f"{side}")
            self._confirm_hint.setVisible(False)
        else:
            qty = self._qty_spin.value()
            p   = self._price_spin.value()
            ot  = self._otype_seg.current()
            price_str = f"₹{p:,.2f}" if ot in ("LIMIT","SL") else "MKT"
            self._submit_btn.setText(f"Confirm {side} · {qty} @ {price_str}")
            self._confirm_hint.setVisible(True)

    def _update_summary(self):
        qty     = self._qty_spin.value()
        ot      = self._otype_seg.current()
        product = self._product_seg.current()
        price   = self._price_spin.value() if ot in ("LIMIT","SL") else self.ltp
        if price <= 0: price = self.ltp

        order_val   = qty * price
        margin_pct  = PRODUCT_MARGIN.get(product, 1.0)
        margin_req  = order_val * margin_pct
        margin_ok   = (self._avail_margin <= 0) or (self._avail_margin >= margin_req)

        self._ov_val.setText(f"₹{order_val:,.2f}")
        self._mreq_val.setText(f"₹{margin_req:,.2f}")
        self._ov_val.setStyleSheet(
            f"color:{P.T0};font-family:'{FONT_NUM}',{FONT_FALL};font-size:12px;font-weight:750;background:transparent;"
        )
        self._mreq_val.setStyleSheet(
            f"color:{P.T0 if margin_ok else P.SELL};font-family:'{FONT_NUM}',{FONT_FALL};font-size:12px;font-weight:750;background:transparent;"
        )

        if self._avail_margin > 0:
            avail_c = P.BUY if margin_ok else P.SELL
            self._mavail_val.setText(f"₹{self._avail_margin:,.2f}")
            self._mavail_val.setStyleSheet(
                f"color:{avail_c};font-family:'{FONT_NUM}',{FONT_FALL};font-size:12px;font-weight:750;background:transparent;"
            )
            if not margin_ok:
                shortfall = margin_req - self._avail_margin
                self._margin_warn.setText(f"⚠  INSUFFICIENT — SHORTFALL ₹{shortfall:,.2f}")
                self._margin_warn.setVisible(True)
            else:
                self._margin_warn.setVisible(False)

    def _update_circuit(self):
        if self._circuit_high > self._circuit_low:
            pct = (self.ltp - self._circuit_low) / (self._circuit_high - self._circuit_low)
            self._circuit_bar.set_pct(pct)

    def _handle_submit(self):
        if self._confirm_stage == 0:
            self._confirm_stage = 1
            self._refresh_confirm_btn()
            self._refresh_submit_style()
        else:
            if not self._quick_validate():
                return
            order_data = self._build_order_data()
            self.order_placed.emit(order_data)
            log.info(
                f"[OrderDialog] {order_data['transaction_type']} "
                f"{order_data['quantity']} {order_data['tradingsymbol']} "
                f"[{order_data['variety']}/{order_data['order_type']}]"
            )
            self.accept()

    def showEvent(self, event):
        super().showEvent(event)
        # Keep the order ticket above pinned floating panels (for example,
        # FloatingPositionsDialog uses WindowStaysOnTopHint).  A modal dialog
        # can still be obscured by another always-on-top tool window unless it
        # is explicitly raised and activated after the window manager maps it.
        self.raise_()
        self.activateWindow()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Escape:
            if self._confirm_stage == 1:
                self._confirm_stage = 0
                self._refresh_confirm_btn()
                self._refresh_submit_style()
            else:
                self.reject()
        else:
            super().keyPressEvent(event)

    # ─────────────────────────────────────────────────────────────────────────
    #  PUBLIC API  — call from your WebSocket tick handler
    # ─────────────────────────────────────────────────────────────────────────

    @Slot(float, float, float, list)
    def update_tick(
        self,
        ltp: float,
        bid: float = 0.0,
        ask: float = 0.0,
        depth: Optional[List[Dict]] = None,
    ):
        """
        Push live market data into the dialog.
        Call this from your Kite WebSocket on_ticks callback.

        depth format (list of 10 dicts, first 5 = buy, last 5 = sell):
            [{"price": 2847.20, "quantity": 342, "orders": 8}, ...]
        """
        previous_ltp = self.ltp
        self.ltp = max(0.0, float(ltp))
        self._update_ltp_header(previous_ltp)
        self._prev_ltp = self.ltp
        self._update_circuit()

        # Depth
        if depth and len(depth) >= 10:
            buy_side  = depth[:5]
            sell_side = depth[5:]
            self._depth_buy  = buy_side
            self._depth_sell = sell_side
            self._refresh_depth(buy_side, sell_side)

        self._update_summary()

    def _refresh_depth(self, buy_side: List[Dict], sell_side: List[Dict]):
        all_qty = [d.get("quantity", 0) for d in buy_side + sell_side]
        max_qty = max(all_qty) if all_qty else 1

        total_bid = total_ask = 0

        for i, (b, s) in enumerate(zip(buy_side, sell_side)):
            bq = b.get("quantity", 0)
            sq = s.get("quantity", 0)
            total_bid += bq
            total_ask += sq
            self._buy_rows[i].update_data(
                b.get("price", 0), bq, b.get("orders", 0), bq / max_qty)
            self._sell_rows[i].update_data(
                s.get("price", 0), sq, s.get("orders", 0), sq / max_qty)

        self._total_bid_lbl.setText(f"{total_bid:,}")
        self._total_ask_lbl.setText(f"{total_ask:,}")

        # OFI
        total = total_bid + total_ask
        if total > 0:
            bid_pct = total_bid / total
            self._pressure_bar.set_pct(bid_pct)
            ofi     = ((total_bid - total_ask) / total) * 100
            ofi_pos = ofi >= 0
            self._ofi_val.setText(f"{'+' if ofi_pos else ''}{ofi:.1f}%")
            self._ofi_val.setStyleSheet(
                f"color:{P.BUY if ofi_pos else P.SELL};"
                f"font-family:'{FONT_NUM}',{FONT_FALL};"
                f"font-size:15px;font-weight:750;background:transparent;"
            )
            strength = "STRONG" if abs(ofi) > 15 else "MODERATE" if abs(ofi) > 5 else "NEUTRAL"
            flow_col = P.BUY if ofi_pos else P.SELL
            self._ofi_desc.setText(f"{strength} {'BUY' if ofi_pos else 'SELL'} FLOW")
            self._ofi_desc.setStyleSheet(
                f"color:{flow_col};font-family:'{FONT_UI}',{FONT_FALL};"
                f"font-size:10px;background:transparent;"
            )
            self._ofi_ratio.setText(f"{bid_pct*100:.1f}% bid · {(1-bid_pct)*100:.1f}% ask")

    # ─────────────────────────────────────────────────────────────────────────
    #  VALIDATION & ORDER BUILDING
    # ─────────────────────────────────────────────────────────────────────────

    def _quick_validate(self) -> bool:
        from kite.widgets.status_bar import show_error  # your existing utility
        if not self.symbol:
            show_error("Symbol is required"); return False
        if self._exchange not in VALID_EXCHANGES:
            show_error(f"Unsupported exchange: {self._exchange}"); return False
        if self._qty_spin.value() <= 0:
            show_error("Quantity must be positive"); return False
        if self._qty_spin.value() % max(1, self._lot_size) != 0:
            show_error(f"Quantity must be a multiple of lot size ({self._lot_size})"); return False
        ot = self._otype_seg.current()
        side = self._side_group.current()
        if ot in ("LIMIT", "SL") and self._price_spin.value() <= 0:
            show_error("Price must be > 0 for LIMIT/SL"); return False
        if ot in ("SL", "SL-M") and self._trig_spin.value() <= 0:
            show_error("Trigger price required for SL orders"); return False
        if ot == "SL":
            p = self._price_spin.value()
            t = self._trig_spin.value()
            if side == "BUY" and p < t:
                show_error("For BUY SL, limit price must be >= trigger price"); return False
            if side == "SELL" and p > t:
                show_error("For SELL SL, limit price must be <= trigger price"); return False
        if self._bo_chk.isChecked():
            if self._target_spin.value() <= 0:
                show_error("Target price required for BO"); return False
            if self._sl_spin.value() <= 0:
                show_error("Stoploss required for BO"); return False
        if ot in ("LIMIT", "SL"):
            self._snap_price_field(self._price_spin)
        if ot in ("SL", "SL-M"):
            self._snap_price_field(self._trig_spin)
        if self._bo_chk.isChecked():
            self._snap_price_field(self._target_spin)
            self._snap_price_field(self._sl_spin)
        return True

    def _build_order_data(self) -> Dict[str, Any]:
        ot     = self._otype_seg.current()
        side   = self._side_group.current()
        bo     = self._bo_chk.isChecked()
        variety = "bo" if bo else "regular"

        data: Dict[str, Any] = {
            "tradingsymbol":    self.symbol,
            "exchange":         self._exchange,
            "transaction_type": side,
            "quantity":         self._qty_spin.value(),
            "order_type":       ot,
            "product":          self._product_seg.current(),
            "variety":          variety,
            "validity":         self._validity_seg.current(),
            "price":            self._price_spin.value() if ot in ("LIMIT", "SL") else 0,
            "trigger_price":    self._trig_spin.value() if ot in ("SL", "SL-M") else 0,
            "tag":              "terminal",
        }
        if bo:
            ref_price = data["price"] or self.ltp
            data["squareoff"]         = abs(self._target_spin.value() - ref_price)
            data["stoploss"]          = abs(ref_price - self._sl_spin.value())
            data["trailing_stoploss"] = self._trailing_chk.isChecked()
        return data

    # ─────────────────────────────────────────────────────────────────────────
    #  DRAG SUPPORT
    # ─────────────────────────────────────────────────────────────────────────

    def _is_interactive(self, widget) -> bool:
        from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QAbstractButton
        while widget:
            if isinstance(widget, (QAbstractSpinBox, QComboBox, QAbstractButton)):
                return True
            widget = widget.parentWidget()
        return False

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self._is_interactive(self.childAt(event.pos())):
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_active and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_active = False
        super().mouseReleaseEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _sep_v() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.VLine)
    f.setFixedWidth(1)
    f.setStyleSheet(f"background:{P.BORDER};border:none;")
    return f
