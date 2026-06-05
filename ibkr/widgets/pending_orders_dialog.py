import inspect
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal, QPoint
from PySide6.QtGui import QColor, QCursor, QFont, QBrush
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
    QFrame,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox,
    QFormLayout,
    QAbstractButton,
    QLineEdit,
    QApplication,
    QMenu,
    QAbstractItemView,
)

from ibkr.utils.ibkr_price import first_positive_ibkr_price, safe_ibkr_price
from ibkr.utils.market_time import market_strftime
from ibkr.widgets.status_bar import show_error, show_info, show_order_cancelled

logger = logging.getLogger(__name__)

PENDING_STATUSES = {
    "OPEN",
    "SUBMITTED",
    "PRESUBMITTED",
    "PENDING",
    "PENDING_SUBMIT",
    "PENDINGSUBMIT",
    "API_PENDING",
    "APIPENDING",
    "PENDING_EXECUTION",
    "CANCEL_PENDING",
    "PARTIAL",
    "TRIGGER PENDING",
    "VALIDATION PENDING",
    "PUT ORDER REQ RECEIVED",
    "MODIFY VALIDATION PENDING",
    "MODIFY PENDING",
    "AMO REQ RECEIVED",
}


def _normalize_status(status: Any) -> str:
    text = str(status or "UNKNOWN").replace(" ", "").replace("_", "").upper()
    return {
        "SUBMITTED": "OPEN",
        "PRESUBMITTED": "OPEN",
        "PENDINGSUBMIT": "PENDING",
        "APIPENDING": "PENDING",
        "PENDINGCANCEL": "CANCEL_PENDING",
        "APICANCELLED": "CANCELLED",
        "FILLED": "COMPLETE",
        "INACTIVE": "REJECTED",
    }.get(text, str(status or "UNKNOWN").replace(" ", "_").upper())


def _normalize_order_type(order_type: Any) -> str:
    text = str(order_type or "").replace(" ", "_").replace("-", "_").upper()
    return {
        "MKT": "MARKET",
        "MARKET": "MARKET",
        "LMT": "LIMIT",
        "LIMIT": "LIMIT",
        "STP": "SL-M",
        "STOP": "SL-M",
        "STP_LMT": "SL",
        "STOP_LIMIT": "SL",
    }.get(text, text or "MARKET")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    return safe_ibkr_price(value, default)


def _normalize_order_row(order: Dict[str, Any]) -> Dict[str, Any]:
    """Make Kite-style, IBKRTradingClient, and wrapper rows render consistently."""
    row = dict(order or {})
    raw_status = row.get("raw_status") or row.get("status")
    row["status"] = _normalize_status(raw_status)
    row["order_type"] = _normalize_order_type(row.get("order_type"))

    quantity = _safe_int(row.get("quantity") or row.get("total_quantity") or row.get("totalQuantity"))
    filled = _safe_int(row.get("filled_quantity") or row.get("filled") or row.get("filledQuantity"))
    pending = _safe_int(row.get("pending_quantity") or row.get("remaining") or row.get("remainingQuantity"))
    if quantity <= 0 and (filled or pending):
        quantity = filled + pending
    if pending <= 0 and quantity > filled and row["status"] in PENDING_STATUSES:
        pending = quantity - filled

    row["quantity"] = quantity
    row["filled_quantity"] = filled
    row["pending_quantity"] = pending
    row["tradingsymbol"] = str(row.get("tradingsymbol") or row.get("symbol") or "").upper()
    row["transaction_type"] = str(row.get("transaction_type") or row.get("action") or "").upper()
    row["price"] = first_positive_ibkr_price(row.get("price"), row.get("limit_price"), row.get("lmtPrice"))
    row["trigger_price"] = first_positive_ibkr_price(row.get("trigger_price"), row.get("stop_price"), row.get("auxPrice"))
    row["order_timestamp"] = str(row.get("order_timestamp") or row.get("timestamp") or row.get("time") or "")
    row["variety"] = row.get("variety") or "regular"
    return row


def _convert_raw_ibkr_trade(trade: Any) -> Dict[str, Any]:
    order = getattr(trade, "order", None)
    status = getattr(trade, "orderStatus", None)
    contract = getattr(trade, "contract", None)
    total_qty = _safe_int(getattr(order, "totalQuantity", 0))
    filled = _safe_int(getattr(status, "filled", 0))
    remaining = _safe_int(getattr(status, "remaining", max(total_qty - filled, 0)))
    return _normalize_order_row({
        "order_id": str(getattr(order, "orderId", "") or getattr(order, "permId", "")),
        "perm_id": str(getattr(order, "permId", "") or ""),
        "tradingsymbol": getattr(contract, "symbol", ""),
        "exchange": getattr(contract, "exchange", "SMART") if contract else "SMART",
        "transaction_type": getattr(order, "action", ""),
        "order_type": getattr(order, "orderType", ""),
        "quantity": total_qty,
        "filled_quantity": filled,
        "pending_quantity": remaining,
        "price": safe_ibkr_price(getattr(order, "lmtPrice", 0.0), 0.0),
        "trigger_price": safe_ibkr_price(getattr(order, "auxPrice", 0.0), 0.0),
        "status": getattr(status, "status", "UNKNOWN") if status else "UNKNOWN",
        "average_price": getattr(status, "avgFillPrice", 0.0) if status else 0.0,
        "order_timestamp": market_strftime("%Y-%m-%d %H:%M:%S"),
        "_ibkr_trade": trade,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Institutional Dark Trading Terminal UI tokens
# ─────────────────────────────────────────────────────────────────────────────
_BG0 = "#050709"
_BG1 = "#0a0d12"
_BG2 = "#0f1318"
_BG3 = "#141920"
_BG4 = "#1a2030"
_BGTB = "#070a0f"

_BULL = "#00d4a8"
_BEAR = "#ff4d6a"
_AMBER = "#f59e0b"
_CYAN = "#00d4ff"
_BLUE = "#3b82f6"

_T0 = "#e8f0ff"
_T1 = "#a8bcd4"
_T2 = "#5a7090"
_T3 = "#2a3a50"
_T_SYMBOL = "#b6c4d6"
_SEL = "#1a2840"

_SANS = "'Inter', 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif"
_MONO = "'Consolas', 'JetBrains Mono', 'Courier New', monospace"
_ROW_H = 24
_HEADER_H = 23


def _font(mono: bool = False, size: int = 9, bold: bool = False) -> QFont:
    """Create a compact UI/number font without leaking styling into backend logic."""
    f = QFont("Consolas" if mono else "Segoe UI")
    f.setPointSize(size)
    f.setBold(bold)
    return f


def _table_item(
    text: str,
    *,
    color: str = _T1,
    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft,
    mono: bool = False,
    bold: bool = False,
    tooltip: str = "",
) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setForeground(QBrush(QColor(color)))
    item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
    item.setFont(_font(mono=mono, size=9, bold=bold))
    if tooltip:
        item.setToolTip(tooltip)
    return item


class EditPendingOrderDialog(QDialog):
    """Small editor for pending order modifications with strict validation."""

    def __init__(self, order: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.order = order
        self.setWindowTitle(f"Edit Pending Order • {order.get('tradingsymbol', '')}")
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumSize(430, 338)
        self.resize(460, 360)

        self._drag_active = False
        self._drag_offset = QPoint()

        self._setup_ui()
        self._apply_styles()

        self.order_type_input.currentTextChanged.connect(self._sync_field_states)
        self._sync_field_states()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        self._shell = QFrame()
        self._shell.setObjectName("editShell")
        root.addWidget(self._shell)

        shell_layout = QVBoxLayout(self._shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        shell_layout.addWidget(self._build_title_bar())

        body = QFrame()
        body.setObjectName("editBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(10, 10, 10, 8)
        body_layout.setSpacing(8)

        meta = QLabel("MODIFY PENDING ORDER")
        meta.setObjectName("sectionLabel")
        body_layout.addWidget(meta)

        form_panel = QFrame()
        form_panel.setObjectName("formPanel")
        form = QFormLayout(form_panel)
        form.setContentsMargins(10, 8, 10, 10)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.quantity_input = QSpinBox()
        self.quantity_input.setObjectName("terminalSpin")
        self.quantity_input.setRange(1, 10_000_000)
        self.quantity_input.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.quantity_input.setValue(int(self.order.get("quantity") or 1))
        form.addRow(self._field_label("QUANTITY"), self.quantity_input)

        self.order_type_input = QComboBox()
        self.order_type_input.setObjectName("terminalCombo")
        self.order_type_input.addItems(["MARKET", "LIMIT", "SL", "SL-M"])
        current_type = (self.order.get("order_type") or "MARKET").upper()
        idx = self.order_type_input.findText(current_type)
        if idx >= 0:
            self.order_type_input.setCurrentIndex(idx)
        form.addRow(self._field_label("ORDER TYPE"), self.order_type_input)

        self.price_input = QDoubleSpinBox()
        self.price_input.setObjectName("terminalSpin")
        self.price_input.setDecimals(2)
        self.price_input.setRange(0.0, 10_000_000.0)
        self.price_input.setSingleStep(0.05)
        self.price_input.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.price_input.setValue(float(self.order.get("price") or 0.0))
        form.addRow(self._field_label("LIMIT PRICE"), self.price_input)

        self.trigger_input = QDoubleSpinBox()
        self.trigger_input.setObjectName("terminalSpin")
        self.trigger_input.setDecimals(2)
        self.trigger_input.setRange(0.0, 10_000_000.0)
        self.trigger_input.setSingleStep(0.05)
        self.trigger_input.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.trigger_input.setValue(float(self.order.get("trigger_price") or 0.0))
        form.addRow(self._field_label("TRIGGER PRICE"), self.trigger_input)

        self.validity_input = QComboBox()
        self.validity_input.setObjectName("terminalCombo")
        self.validity_input.addItems(["DAY", "IOC"])
        current_validity = (self.order.get("validity") or "DAY").upper()
        idx = self.validity_input.findText(current_validity)
        if idx >= 0:
            self.validity_input.setCurrentIndex(idx)
        form.addRow(self._field_label("VALIDITY"), self.validity_input)

        body_layout.addWidget(form_panel)
        body_layout.addStretch()
        shell_layout.addWidget(body, 1)
        shell_layout.addWidget(self._build_footer())

    def _build_title_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("editTitleBar")
        bar.setFixedHeight(30)
        bar.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 0, 6, 0)
        layout.setSpacing(8)

        badge = QLabel("EDIT ORDER")
        badge.setObjectName("dialogBadge")

        symbol = QLabel(str(self.order.get("tradingsymbol", "") or "—").upper())
        symbol.setObjectName("symbolTitle")

        order_id = QLabel(str(self.order.get("order_id", "") or ""))
        order_id.setObjectName("orderIdTitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self.reject)

        layout.addWidget(badge)
        layout.addWidget(symbol)
        layout.addWidget(order_id)
        layout.addStretch()
        layout.addWidget(close_btn)

        bar.mousePressEvent = self._tb_press
        bar.mouseMoveEvent = self._tb_move
        bar.mouseReleaseEvent = self._tb_release
        return bar

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("editFooter")
        footer.setFixedHeight(42)

        controls = QHBoxLayout(footer)
        controls.setContentsMargins(10, 6, 10, 6)
        controls.setSpacing(8)

        hint = QLabel("Modify only editable IBKR pending-order fields")
        hint.setObjectName("footerHint")

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setObjectName("secondaryBtn")
        cancel_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel_btn.setFixedHeight(26)
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("SAVE CHANGES")
        save_btn.setObjectName("primaryBtn")
        save_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save_btn.setFixedHeight(26)
        save_btn.clicked.connect(self._on_save)

        controls.addWidget(hint)
        controls.addStretch()
        controls.addWidget(cancel_btn)
        controls.addWidget(save_btn)
        return footer

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    def _tb_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _tb_move(self, event):
        if self._drag_active and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def _tb_release(self, _event):
        self._drag_active = False

    def _apply_styles(self):
        self.setStyleSheet(f"""
        QDialog {{
            background: {_BG0};
            color: {_T1};
            font-family: {_SANS};
        }}
        QFrame#editShell {{
            background: {_BG1};
            border: 1px solid {_BG4};
            border-radius: 2px;
        }}
        QFrame#editTitleBar,
        QFrame#editFooter {{
            background: {_BGTB};
        }}
        QFrame#editTitleBar {{
            border-bottom: 1px solid {_BG4};
        }}
        QFrame#editFooter {{
            border-top: 1px solid {_BG4};
        }}
        QFrame#editBody {{
            background: {_BG1};
        }}
        QFrame#formPanel {{
            background: {_BG2};
            border: 1px solid {_BG4};
            border-radius: 2px;
        }}
        QLabel#dialogBadge {{
            color: {_AMBER};
            font-family: {_SANS};
            font-size: 10px;
            font-weight: 900;
            letter-spacing: 1.2px;
            background: transparent;
        }}
        QLabel#symbolTitle {{
            color: {_T_SYMBOL};
            font-family: {_SANS};
            font-size: 12px;
            font-weight: 800;
            letter-spacing: 0.4px;
            background: transparent;
        }}
        QLabel#orderIdTitle {{
            color: {_T2};
            font-family: {_MONO};
            font-size: 9px;
            font-weight: 700;
            background: transparent;
        }}
        QLabel#sectionLabel,
        QLabel#fieldLabel {{
            color: {_T2};
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 900;
            letter-spacing: 1px;
            background: transparent;
        }}
        QLabel#footerHint {{
            color: {_T2};
            font-family: {_SANS};
            font-size: 10px;
            font-weight: 600;
            background: transparent;
        }}
        QComboBox#terminalCombo,
        QSpinBox#terminalSpin,
        QDoubleSpinBox#terminalSpin {{
            background: {_BG1};
            color: {_T0};
            border: 1px solid {_BG4};
            border-radius: 2px;
            font-family: {_MONO};
            font-size: 11px;
            font-weight: 700;
            padding: 4px 8px;
            min-height: 20px;
            selection-background-color: {_SEL};
        }}
        QComboBox#terminalCombo:hover,
        QSpinBox#terminalSpin:hover,
        QDoubleSpinBox#terminalSpin:hover {{
            background: {_BG3};
            border-color: {_T2};
        }}
        QComboBox#terminalCombo:focus,
        QSpinBox#terminalSpin:focus,
        QDoubleSpinBox#terminalSpin:focus {{
            border: 1px solid {_CYAN};
            background: {_BG3};
        }}
        QComboBox#terminalCombo:disabled,
        QSpinBox#terminalSpin:disabled,
        QDoubleSpinBox#terminalSpin:disabled {{
            color: {_T3};
            background: {_BG2};
            border-color: {_BG4};
        }}
        QComboBox#terminalCombo::drop-down {{
            border: none;
            width: 18px;
        }}
        QComboBox#terminalCombo::down-arrow {{
            image: none;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 5px solid {_T2};
            margin-right: 5px;
        }}
        QComboBox#terminalCombo QAbstractItemView {{
            background: {_BG1};
            color: {_T0};
            border: 1px solid {_BG4};
            selection-background-color: {_SEL};
            selection-color: {_T0};
            outline: none;
        }}
        QPushButton#closeBtn {{
            background: transparent;
            color: {_T2};
            border: none;
            border-radius: 2px;
            font-size: 12px;
            font-weight: 800;
        }}
        QPushButton#closeBtn:hover {{
            background: rgba(255,77,106,0.15);
            color: {_BEAR};
        }}
        QPushButton#primaryBtn,
        QPushButton#secondaryBtn {{
            border-radius: 2px;
            font-family: {_SANS};
            font-size: 10px;
            font-weight: 900;
            letter-spacing: 0.7px;
            padding: 0 14px;
            min-width: 84px;
        }}
        QPushButton#primaryBtn {{
            background: rgba(0,212,168,0.12);
            color: {_BULL};
            border: 1px solid rgba(0,212,168,0.35);
        }}
        QPushButton#primaryBtn:hover {{
            background: rgba(0,212,168,0.18);
            border-color: {_BULL};
        }}
        QPushButton#secondaryBtn {{
            background: {_BG2};
            color: {_T1};
            border: 1px solid {_BG4};
        }}
        QPushButton#secondaryBtn:hover {{
            background: {_BG3};
            color: {_T0};
        }}
        """)

    def _sync_field_states(self):
        order_type = self.order_type_input.currentText().upper()
        self.price_input.setEnabled(order_type in {"LIMIT", "SL"})
        self.trigger_input.setEnabled(order_type in {"SL", "SL-M"})

        if order_type in {"MARKET", "SL-M"}:
            self.price_input.setValue(0.0)
        if order_type in {"MARKET", "LIMIT"}:
            self.trigger_input.setValue(0.0)

    def _on_save(self):
        payload = self.get_payload()
        if payload is None:
            return
        self.accept()

    def get_payload(self) -> Optional[Dict[str, Any]]:
        order_type = self.order_type_input.currentText().upper()
        quantity = int(self.quantity_input.value())
        price = float(self.price_input.value())
        trigger_price = float(self.trigger_input.value())

        if order_type in {"LIMIT", "SL"} and price <= 0:
            show_error("Limit/SL orders require a positive price.")
            return None
        if order_type in {"SL", "SL-M"} and trigger_price <= 0:
            show_error("SL/SL-M orders require a positive trigger price.")
            return None

        return {
            "quantity": quantity,
            "order_type": order_type,
            "price": price if order_type in {"LIMIT", "SL"} else None,
            "trigger_price": trigger_price if order_type in {"SL", "SL-M"} else None,
            "validity": self.validity_input.currentText().upper(),
        }


class PendingOrdersDialog(QDialog):
    """Live pending orders monitor for cancel/modify workflows."""

    pending_orders_updated = Signal(int)

    def __init__(self, trader, instrument_map: Optional[Dict[str, Dict[str, Any]]] = None, parent=None):
        super().__init__(parent)
        self.trader = trader
        self.instrument_map = instrument_map or {}
        self._orders: List[Dict[str, Any]] = []
        self._orders_by_id: Dict[str, Dict[str, Any]] = {}

        self.setWindowTitle("PENDING ORDERS")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumSize(900, 560)
        self.resize(1000, 660)
        self._drag_active = False
        self._drag_offset = QPoint()

        self._setup_ui()
        self._connect_signals()

        self.auto_refresh_timer = QTimer(self)
        self.auto_refresh_timer.timeout.connect(self.refresh_orders)

        self.refresh_orders()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        self._shell = QFrame()
        self._shell.setObjectName("pendingShell")
        root.addWidget(self._shell)

        shell_layout = QVBoxLayout(self._shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        title_bar = QFrame()
        title_bar.setObjectName("titleBar")
        title_bar.setFixedHeight(34)
        title_bar.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))

        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(10, 0, 6, 0)
        title_layout.setSpacing(8)

        self.title_label = QLabel("PENDING ORDERS")
        self.title_label.setObjectName("dialogTitle")

        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setObjectName("toolBtn")
        self.refresh_btn.setToolTip("Refresh pending orders")
        self.refresh_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.refresh_btn.setFixedSize(24, 22)

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setToolTip("Close")
        self.close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.close_btn.setFixedSize(24, 22)

        title_layout.addWidget(self.title_label)
        title_layout.addStretch()
        title_layout.addWidget(self.refresh_btn)
        title_layout.addWidget(self.close_btn)

        title_bar.mousePressEvent = self._tb_press
        title_bar.mouseMoveEvent = self._tb_move
        title_bar.mouseReleaseEvent = self._tb_release

        shell_layout.addWidget(title_bar)

        body = QFrame()
        body.setObjectName("body")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(10, 8, 10, 8)
        body_layout.setSpacing(8)

        header = QFrame()
        header.setObjectName("summaryStrip")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 4, 8, 4)
        header_layout.setSpacing(8)

        self.status_label = QLabel("Loading pending orders…")
        self.status_label.setObjectName("statusNeutral")
        header_layout.addWidget(self.status_label)
        header_layout.addStretch()

        self.count_label = QLabel("0 pending")
        self.count_label.setObjectName("countLabel")
        header_layout.addWidget(self.count_label)

        body_layout.addWidget(header)

        self.table = QTableWidget()
        self.table.setObjectName("pendingTable")
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels([
            "Time",
            "Order ID",
            "Symbol",
            "Type",
            "Qty",
            "Filled",
            "Pending",
            "Price",
            "Trigger",
            "Status",
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self.table.verticalHeader().setMinimumSectionSize(_ROW_H)
        self.table.setWordWrap(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setCornerButtonEnabled(False)

        header_view = self.table.horizontalHeader()
        header_view.setFixedHeight(_HEADER_H)
        header_view.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header_view.setHighlightSections(False)
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setMinimumSectionSize(36)

        body_layout.addWidget(self.table, 1)
        shell_layout.addWidget(body, 1)

        footer = QFrame()
        footer.setObjectName("footer")
        footer.setFixedHeight(38)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(10, 0, 10, 0)
        footer_layout.setSpacing(8)

        self.footer_status_label = QLabel("⚠ PENDING")
        self.footer_status_label.setObjectName("statusWarning")

        self.cancel_btn = QPushButton("CANCEL SELECTED")
        self.cancel_btn.setObjectName("destructiveBtn")
        self.cancel_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.cancel_btn.setFixedHeight(26)

        self.edit_btn = QPushButton("EDIT SELECTED")
        self.edit_btn.setObjectName("secondaryBtn")
        self.edit_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.edit_btn.setFixedHeight(26)

        footer_layout.addWidget(self.footer_status_label)
        footer_layout.addStretch()
        footer_layout.addWidget(self.edit_btn)
        footer_layout.addWidget(self.cancel_btn)

        shell_layout.addWidget(footer)

        self._apply_styles()
        self._set_action_state(False)

    def _apply_styles(self):
        self.setStyleSheet(f"""
        QDialog {{
            background: {_BG0};
            color: {_T1};
            font-family: {_SANS};
        }}
        QFrame#pendingShell {{
            background: {_BG1};
            border: 1px solid {_BG4};
            border-radius: 2px;
        }}
        QFrame#titleBar,
        QFrame#footer {{
            background: {_BGTB};
        }}
        QFrame#titleBar {{
            border-bottom: 1px solid {_BG4};
        }}
        QFrame#footer {{
            border-top: 1px solid {_BG4};
        }}
        QFrame#body {{
            background: {_BG1};
        }}
        QFrame#summaryStrip {{
            background: {_BG2};
            border: 1px solid {_BG4};
            border-radius: 2px;
        }}
        QLabel {{
            color: {_T1};
            background: transparent;
            font-family: {_SANS};
        }}
        QLabel#categoryBadge {{
            color: {_AMBER};
            font-size: 9px;
            font-weight: 900;
            letter-spacing: 1.1px;
        }}
        QLabel#dialogTitle {{
            color: {_T_SYMBOL};
            font-size: 12px;
            font-weight: 800;
            letter-spacing: 0.8px;
        }}
        QLabel#countLabel {{
            color: {_CYAN};
            background: rgba(0,212,255,0.08);
            border: 1px solid rgba(0,212,255,0.24);
            border-radius: 2px;
            padding: 2px 7px;
            font-family: {_MONO};
            font-size: 10px;
            font-weight: 800;
        }}
        QLabel#statusNeutral {{
            color: {_T2};
            font-size: 10px;
            font-weight: 700;
        }}
        QLabel#statusWarning {{
            color: {_AMBER};
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 0.8px;
        }}
        QPushButton#toolBtn,
        QPushButton#closeBtn {{
            border: 1px solid transparent;
            border-radius: 2px;
            background: transparent;
            color: {_T2};
            font-family: {_SANS};
            font-size: 12px;
            font-weight: 900;
        }}
        QPushButton#toolBtn:hover {{
            background: rgba(0,212,255,0.09);
            color: {_CYAN};
            border-color: rgba(0,212,255,0.25);
        }}
        QPushButton#closeBtn:hover {{
            background: rgba(255,77,106,0.15);
            color: {_BEAR};
            border-color: rgba(255,77,106,0.30);
        }}
        QPushButton#secondaryBtn,
        QPushButton#destructiveBtn {{
            border-radius: 2px;
            font-family: {_SANS};
            font-size: 10px;
            font-weight: 900;
            letter-spacing: 0.7px;
            padding: 0 14px;
            min-width: 104px;
        }}
        QPushButton#secondaryBtn {{
            background: {_BG2};
            color: {_T1};
            border: 1px solid {_BG4};
        }}
        QPushButton#secondaryBtn:hover {{
            background: {_BG3};
            color: {_CYAN};
            border-color: rgba(0,212,255,0.26);
        }}
        QPushButton#destructiveBtn {{
            background: rgba(255,77,106,0.08);
            color: {_BEAR};
            border: 1px solid rgba(255,77,106,0.25);
        }}
        QPushButton#destructiveBtn:hover {{
            background: rgba(255,77,106,0.15);
            border-color: {_BEAR};
        }}
        QPushButton#secondaryBtn:disabled,
        QPushButton#destructiveBtn:disabled {{
            background: {_BG2};
            color: {_T3};
            border-color: {_BG4};
        }}
        QTableWidget#pendingTable {{
            background: {_BG1};
            alternate-background-color: {_BG2};
            gridline-color: transparent;
            border: 1px solid {_BG4};
            border-radius: 2px;
            outline: none;
            color: {_T1};
            selection-background-color: {_SEL};
            selection-color: {_T0};
            font-family: {_MONO};
            font-size: 11px;
        }}
        QTableWidget#pendingTable::item {{
            padding: 0 6px;
            border-bottom: 1px solid {_BG3};
            background: transparent;
        }}
        QTableWidget#pendingTable::item:selected {{
            background: {_SEL};
            color: {_T0};
        }}
        QTableWidget#pendingTable::item:hover {{
            background: {_BG3};
        }}
        QHeaderView::section {{
            background: {_BG2};
            color: {_T2};
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 900;
            letter-spacing: 1px;
            text-transform: uppercase;
            border: none;
            border-bottom: 1px solid {_BG4};
            padding: 0 6px;
            min-height: {_HEADER_H}px;
            max-height: {_HEADER_H}px;
        }}
        QHeaderView::section:hover {{
            color: {_T1};
            background: {_BG3};
        }}
        QMenu {{
            background: {_BG1};
            color: {_T1};
            border: 1px solid {_BG4};
            border-radius: 2px;
            font-family: {_SANS};
            font-size: 11px;
            padding: 3px 0;
        }}
        QMenu::item {{
            padding: 5px 14px;
            background: transparent;
        }}
        QMenu::item:selected {{
            background: {_SEL};
            color: {_T0};
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 4px;
            border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {_BG4};
            border-radius: 2px;
            min-height: 18px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {_T2};
        }}
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {{
            height: 0;
            border: none;
        }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 4px;
            border: none;
        }}
        QScrollBar::handle:horizontal {{
            background: {_BG4};
            border-radius: 2px;
            min-width: 18px;
        }}
        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal {{
            width: 0;
            border: none;
        }}
        """)

    def _connect_signals(self):
        self.refresh_btn.clicked.connect(self.refresh_orders)
        self.cancel_btn.clicked.connect(self.cancel_selected_order)
        self.edit_btn.clicked.connect(self.edit_selected_order)
        self.close_btn.clicked.connect(self.close)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

    def _set_action_state(self, enabled: bool):
        self.cancel_btn.setEnabled(enabled)
        self.edit_btn.setEnabled(enabled)

    def _on_selection_changed(self):
        self._set_action_state(self.selected_order() is not None)

    def selected_order(self) -> Optional[Dict[str, Any]]:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return None

        row = selected[0].row()
        order_id_item = self.table.item(row, 1)
        if order_id_item is None:
            return None

        row_order = order_id_item.data(Qt.ItemDataRole.UserRole)
        if isinstance(row_order, dict):
            return row_order

        order_id = (order_id_item.text() or "").strip()
        return self._orders_by_id.get(order_id)

    def refresh_orders(self):
        if not self.isVisible() and self._orders:
            return

        self.status_label.setText("Syncing with IBKR...")
        try:
            orders = self._fetch_orders_from_api()
            self._handle_orders_result(orders)
        except Exception as exc:
            self._handle_orders_error(exc)

    def _fetch_orders_from_api(self) -> List[Dict[str, Any]]:
        """Pull the latest pending/order list from the active IBKR client surface."""
        if hasattr(self.trader, "orders") and callable(self.trader.orders):
            orders = self.trader.orders()
        elif hasattr(self.trader, "get_orders") and callable(self.trader.get_orders):
            orders = self.trader.get_orders()
        elif hasattr(getattr(self.trader, "client", None), "trades"):
            orders = [_convert_raw_ibkr_trade(trade) for trade in (self.trader.client.trades() or [])]
        elif hasattr(getattr(self.trader, "ib", None), "trades"):
            orders = [_convert_raw_ibkr_trade(trade) for trade in (self.trader.ib.trades() or [])]
        else:
            raise RuntimeError("Active IBKR client does not expose an orders API")

        return [_normalize_order_row(order) for order in (orders or []) if isinstance(order, dict)]

    def _handle_orders_result(self, all_orders):
        pending = [order for order in (all_orders or []) if (order.get("status", "").upper() in PENDING_STATUSES)]

        pending_sorted = sorted(pending, key=lambda o: o.get("order_timestamp", ""), reverse=True)
        self._orders = pending_sorted
        self._orders_by_id = {
            str(order.get("order_id", "")): order
            for order in pending_sorted
            if order.get("order_id")
        }
        self._render_table()

        self.count_label.setText(f"{len(self._orders)} pending")
        now = market_strftime("%H:%M:%S")
        self.status_label.setText(f"Synced with IBKR at {now}")
        self.pending_orders_updated.emit(len(self._orders))

    def _handle_orders_error(self, exc):
        logger.error("Failed to refresh pending orders: %s", exc, exc_info=True)
        self.status_label.setText("Failed to load pending orders")
        show_error(f"Pending orders refresh failed: {exc}")

    def _render_table(self):
        selected_order_id = self._selected_order_id()

        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        for order in self._orders:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._populate_row(row, order)

        self.table.setSortingEnabled(True)
        self._restore_selection(selected_order_id)
        self._set_action_state(self.selected_order() is not None)

    def _selected_order_id(self) -> Optional[str]:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return None

        row = selected[0].row()
        order_id_item = self.table.item(row, 1)
        if order_id_item is None:
            return None

        order_id = (order_id_item.text() or "").strip()
        return order_id or None

    def _restore_selection(self, order_id: Optional[str]) -> None:
        if not order_id:
            return

        for row in range(self.table.rowCount()):
            order_id_item = self.table.item(row, 1)
            if order_id_item is None:
                continue
            if (order_id_item.text() or "").strip() == order_id:
                self.table.selectRow(row)
                return

    def _populate_row(self, row: int, order: Dict[str, Any]):
        ts = str(order.get("order_timestamp") or order.get("exchange_timestamp") or "-")
        ts_display = ts
        if ts and ts != "-":
            try:
                ts_display = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").strftime("%H:%M:%S")
            except Exception:
                ts_display = ts[:19]

        time_item = _table_item(ts_display, color=_T2, mono=True, tooltip=ts)
        self.table.setItem(row, 0, time_item)

        order_id = str(order.get("order_id", ""))
        order_id_item = _table_item(order_id, color=_T2, mono=True, tooltip=f"Order ID: {order_id}")
        order_id_item.setData(Qt.ItemDataRole.UserRole, order)
        self.table.setItem(row, 1, order_id_item)

        symbol_item = _table_item(
            str(order.get("tradingsymbol", "")).upper(),
            color=_T_SYMBOL,
            bold=True,
        )
        self.table.setItem(row, 2, symbol_item)

        side = str(order.get("transaction_type", "")).upper()
        side_color = _BULL if side == "BUY" else _BEAR if side == "SELL" else _T1
        side_item = _table_item(side, color=side_color, align=Qt.AlignmentFlag.AlignCenter, bold=True)
        self.table.setItem(row, 3, side_item)

        filled = int(order.get("filled_quantity") or 0)
        pending = int(order.get("pending_quantity") or 0)
        total = filled + pending
        is_partial = filled > 0 and pending > 0

        if is_partial:
            qty_item = _table_item(
                f"{filled}/{total}",
                color=_AMBER,
                align=Qt.AlignmentFlag.AlignRight,
                mono=True,
                bold=True,
                tooltip=f"Partial fill: {filled} of {total} shares filled",
            )
        else:
            qty_item = _table_item(
                str(total),
                color=_T1,
                align=Qt.AlignmentFlag.AlignRight,
                mono=True,
            )

        self.table.setItem(row, 4, qty_item)
        self.table.setItem(row, 5, _table_item(str(filled), color=_T2, align=Qt.AlignmentFlag.AlignRight, mono=True))
        self.table.setItem(row, 6, _table_item(str(pending), color=_AMBER, align=Qt.AlignmentFlag.AlignRight, mono=True))

        price = float(order.get("price") or 0.0)
        trigger = float(order.get("trigger_price") or 0.0)
        self.table.setItem(
            row,
            7,
            _table_item(
                "MKT" if price <= 0 else f"${price:.2f}",
                color=_T1 if price > 0 else _CYAN,
                align=Qt.AlignmentFlag.AlignRight,
                mono=True,
            ),
        )
        self.table.setItem(
            row,
            8,
            _table_item(
                "—" if trigger <= 0 else f"${trigger:.2f}",
                color=_T3 if trigger <= 0 else _AMBER,
                align=Qt.AlignmentFlag.AlignRight,
                mono=True,
            ),
        )

        if is_partial:
            status_text = "PARTIAL"
            status_color = _AMBER
        else:
            status_text = str(order.get("status", "")).upper()
            status_color = _AMBER if status_text in PENDING_STATUSES else _T2

        status_item = _table_item(
            status_text,
            color=status_color,
            align=Qt.AlignmentFlag.AlignCenter,
            bold=True,
        )
        self.table.setItem(row, 9, status_item)

        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            if item:
                item.setBackground(QBrush(QColor(_BG1 if row % 2 == 0 else _BG2)))
                self.table.setRowHeight(row, _ROW_H)

    def _show_context_menu(self, pos):
        row = self.table.indexAt(pos).row()
        if row >= 0:
            self.table.selectRow(row)

        order = self.selected_order()
        if not order:
            return

        filled = int(order.get("filled_quantity") or 0)
        pending = int(order.get("pending_quantity") or 0)

        menu = QMenu(self)
        menu.setObjectName("pendingOrdersMenu")
        if filled > 0 and pending > 0:
            cancel_rest = menu.addAction(
                f"✕  CANCEL REMAINING {pending} SHARES"
            )
            cancel_rest.triggered.connect(
                lambda: self._cancel_remaining(order)
            )
            menu.addSeparator()

        modify_act = menu.addAction("✎  MODIFY ORDER")
        modify_act.triggered.connect(self.edit_selected_order)
        menu.exec(self.table.viewport().mapToGlobal(pos))


    def _call_cancel_order(self, order: Dict[str, Any]):
        order_id = order.get("order_id")
        cancel_order = getattr(self.trader, "cancel_order", None)
        if callable(cancel_order):
            params = inspect.signature(cancel_order).parameters
            if "variety" in params:
                return cancel_order(variety=order.get("variety") or "regular", order_id=order_id)
            return cancel_order(order_id)

        raw_trade = order.get("_ibkr_trade")
        client = getattr(self.trader, "client", None) or getattr(self.trader, "ib", None)
        if raw_trade is not None and hasattr(client, "cancelOrder"):
            return client.cancelOrder(raw_trade.order)
        raise RuntimeError("Active IBKR client does not expose cancel_order")

    def _call_modify_order(self, order: Dict[str, Any], payload: Dict[str, Any]):
        order_id = order.get("order_id")
        modify_order = getattr(self.trader, "modify_order", None)
        kwargs = {
            "quantity": payload.get("quantity"),
            "price": payload.get("price"),
            "order_type": payload.get("order_type"),
            "trigger_price": payload.get("trigger_price"),
            "validity": payload.get("validity"),
        }
        if callable(modify_order):
            params = inspect.signature(modify_order).parameters
            if "variety" in params:
                return modify_order(variety=order.get("variety") or "regular", order_id=order_id, **kwargs)
            return modify_order(order_id, **kwargs)
        raise RuntimeError("Active IBKR client does not expose modify_order")

    def _cancel_remaining(self, order):
        """Cancel the unfilled portion of a partially-filled order."""
        order_id = order.get("order_id")
        symbol = order.get("tradingsymbol", "")
        pending = int(order.get("pending_quantity") or 0)

        try:
            self._call_cancel_order(order)
            show_info(f"Cancelled remaining {pending} shares of {symbol}")
            logger.info("Cancelled remaining quantity for partially-filled order %s", order_id)
            self.refresh_orders()
        except Exception as exc:
            logger.error("Cancel remaining failed for %s: %s", order_id, exc, exc_info=True)
            show_error(f"Cancel failed: {exc}")

    def cancel_selected_order(self):
        order = self.selected_order()
        if not order:
            show_error("Select an order to cancel")
            return

        order_id = order.get("order_id")
        symbol = order.get("tradingsymbol", "")
        try:
            self._call_cancel_order(order)
            show_order_cancelled(symbol)
            show_info(f"Cancelled order {order_id}")
            logger.info("Cancelled pending order %s", order_id)
            self.refresh_orders()
        except Exception as exc:
            logger.error("Cancel order failed for %s: %s", order_id, exc, exc_info=True)
            show_error(f"Cancel failed for {order_id}: {exc}")

    def edit_selected_order(self):
        order = self.selected_order()
        if not order:
            show_error("Select an order to edit")
            return

        order_id = order.get("order_id")
        edit_dialog = EditPendingOrderDialog(order, parent=self)

        if edit_dialog.exec() != QDialog.DialogCode.Accepted:
            return

        payload = edit_dialog.get_payload()
        if payload is None:
            return

        try:
            self._call_modify_order(order, payload)
            show_info(f"Modified order {order_id}")
            logger.info("Modified pending order %s", order_id)
            self.refresh_orders()
        except Exception as exc:
            logger.error("Modify order failed for %s: %s", order_id, exc, exc_info=True)
            show_error(f"Modify failed for {order_id}: {exc}")

    def closeEvent(self, event):
        self.auto_refresh_timer.stop()
        super().closeEvent(event)

    def hideEvent(self, event):
        self.auto_refresh_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.auto_refresh_timer.start(7000)
        self.refresh_orders()
        if self.parent():
            parent_geo = self.parent().frameGeometry()
            center = parent_geo.center()
            self.move(center - self.rect().center())

    def _tb_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _tb_move(self, event):
        if self._drag_active and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def _tb_release(self, _event):
        self._drag_active = False

    def mousePressEvent(self, event):
        w = self.childAt(event.position().toPoint())
        while w:
            if isinstance(w, (QAbstractButton, QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox, QTableWidget)):
                return super().mousePressEvent(event)
            w = w.parentWidget()
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_active and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_active = False
        super().mouseReleaseEvent(event)
