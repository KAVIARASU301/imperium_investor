import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from PySide6.QtCore import Qt, Signal, QDate, QTimer, QThreadPool
from PySide6.QtGui import QColor, QMouseEvent, QFont, QCursor, QBrush
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QLineEdit, QDateEdit, QCheckBox, QSplitter, QGroupBox, QGridLayout,
    QFormLayout, QAbstractButton, QAbstractSpinBox, QAbstractItemView, QFrame
)

from ibkr.utils.worker import Worker

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Institutional Dark Trading Terminal UI tokens
# ─────────────────────────────────────────────────────────────────────────────

_BG0 = "#050709"     # app shell
_BG1 = "#0a0d12"     # dialog/window background
_BG2 = "#0f1318"     # panels/table rows
_BG3 = "#141920"     # hover/inner section
_BG4 = "#1a2030"     # borders
_BGTB = "#070a0f"    # title/footer

_BULL = "#00d4a8"
_BEAR = "#ff4d6a"
_AMBER = "#f59e0b"
_CYAN = "#00d4ff"
_BLUE = "#3b82f6"

_T0 = "#e8f0ff"
_T1 = "#a8bcd4"
_T2 = "#5a7090"
_T3 = "#2a3a50"
_SYMBOL_SOFT = "#b6c4d6"
_SEL = "#1a2840"

_SANS = "'Inter', 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, Arial, sans-serif"
_MONO = "'Consolas', 'JetBrains Mono', monospace"

_ROW_H = 24
_HEADER_H = 23
_TITLE_H = 32
_FOOTER_H = 32


def _ui_font(point_size: int = 9, bold: bool = False) -> QFont:
    font = QFont("Segoe UI", point_size)
    font.setBold(bold)
    return font


def _mono_font(point_size: int = 9, bold: bool = False) -> QFont:
    font = QFont("Consolas", point_size)
    font.setBold(bold)
    return font


def _status_color(status: str) -> str:
    status = (status or "").upper()
    if status == "COMPLETE":
        return _BULL
    if status in ("CANCELLED", "REJECTED"):
        return _BEAR
    if status in ("OPEN", "PENDING_EXECUTION", "TRIGGER PENDING", "VALIDATION PENDING"):
        return _AMBER
    return _T2


def _format_currency(value: float, decimals: int = 2) -> str:
    try:
        value = float(value or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    if value <= 0:
        return "—"
    return f"₹{value:,.{decimals}f}"


class FilterWidget(QWidget):
    """Widget for filtering order history with a compact terminal layout."""

    filter_changed = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("filterWidget")
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        filter_group = QGroupBox("FILTERS")
        filter_group.setObjectName("filterGroup")

        form_layout = QFormLayout(filter_group)
        form_layout.setContentsMargins(9, 9, 9, 9)
        form_layout.setHorizontalSpacing(8)
        form_layout.setVerticalSpacing(7)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.symbol_filter = QLineEdit()
        self.symbol_filter.setObjectName("terminalInput")
        self.symbol_filter.setPlaceholderText("RELIANCE")
        self.symbol_filter.textChanged.connect(self.filter_changed.emit)
        form_layout.addRow(self._field_label("SYMBOL"), self.symbol_filter)

        self.status_filter = QComboBox()
        self.status_filter.setObjectName("terminalCombo")
        self.status_filter.addItems(["All", "COMPLETE", "CANCELLED", "OPEN", "PENDING_EXECUTION"])
        self.status_filter.currentTextChanged.connect(self.filter_changed.emit)
        form_layout.addRow(self._field_label("STATUS"), self.status_filter)

        self.type_filter = QComboBox()
        self.type_filter.setObjectName("terminalCombo")
        self.type_filter.addItems(["All", "BUY", "SELL"])
        self.type_filter.currentTextChanged.connect(self.filter_changed.emit)
        form_layout.addRow(self._field_label("TYPE"), self.type_filter)

        self.date_from = QDateEdit()
        self.date_from.setObjectName("terminalDate")
        self.date_from.setDate(QDate.currentDate().addDays(-30))
        self.date_from.setCalendarPopup(True)
        self.date_from.dateChanged.connect(self.filter_changed.emit)
        form_layout.addRow(self._field_label("FROM"), self.date_from)

        bottom_controls_layout = QHBoxLayout()
        bottom_controls_layout.setSpacing(8)
        bottom_controls_layout.setContentsMargins(0, 4, 0, 0)

        self.today_only = QCheckBox("TODAY ONLY")
        self.today_only.setObjectName("terminalCheck")
        self.today_only.stateChanged.connect(self.filter_changed.emit)

        clear_btn = QPushButton("CLEAR")
        clear_btn.setObjectName("clearButton")
        clear_btn.setFixedHeight(24)
        clear_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        clear_btn.clicked.connect(self._clear_filters)

        bottom_controls_layout.addWidget(self.today_only)
        bottom_controls_layout.addStretch()
        bottom_controls_layout.addWidget(clear_btn)

        form_layout.addRow(bottom_controls_layout)
        layout.addWidget(filter_group)

    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    def _clear_filters(self):
        """Clear all filters."""
        self.symbol_filter.clear()
        self.status_filter.setCurrentIndex(0)
        self.type_filter.setCurrentIndex(0)
        self.date_from.setDate(QDate.currentDate().addDays(-30))
        self.today_only.setChecked(False)
        self.filter_changed.emit()

    def get_filters(self) -> Dict[str, Any]:
        """Get current filter values."""
        return {
            'symbol': self.symbol_filter.text().strip().upper(),
            'status': self.status_filter.currentText() if self.status_filter.currentText() != "All" else None,
            'transaction_type': self.type_filter.currentText() if self.type_filter.currentText() != "All" else None,
            'date_from': self.date_from.date().toPython(),
            'today_only': self.today_only.isChecked()
        }


class OrderHistoryTable(QTableWidget):
    """Compact gridless table widget for displaying order history."""

    order_selected = Signal(dict)

    def __init__(self):
        super().__init__()
        self.setObjectName("ordersTable")
        self.setColumnCount(9)
        self.setHorizontalHeaderLabels([
            "Date", "Time", "Symbol", "Type", "Qty", "Price", "Avg Price", "Status", "Order ID"
        ])
        self._orders_data: List[Dict[str, Any]] = []
        self._visible_orders: List[Dict[str, Any]] = []
        self._setup_table()

    def _setup_table(self):
        """Configure table appearance and behavior."""
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(_ROW_H)
        self.verticalHeader().setMinimumSectionSize(_ROW_H)
        self.setShowGrid(False)
        self.setSortingEnabled(True)
        self.setWordWrap(False)
        self.setCornerButtonEnabled(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        header = self.horizontalHeader()
        header.setFixedHeight(_HEADER_H)
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(42)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)  # Symbol
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)  # Order ID

        self.itemSelectionChanged.connect(self._on_selection_changed)

    def update_orders(self, orders: List[Dict], filters: Dict[str, Any] = None):
        """Update table with filtered orders."""
        self._orders_data = orders or []
        filtered_orders = self._apply_filters(self._orders_data, filters) if filters else self._orders_data
        self._visible_orders = sorted(
            filtered_orders,
            key=lambda x: x.get("order_timestamp", ""),
            reverse=True,
        )

        self.setSortingEnabled(False)
        self.setRowCount(0)

        for order in self._visible_orders:
            row_position = self.rowCount()
            self.insertRow(row_position)
            self._populate_row(row_position, order)
            self.setRowHeight(row_position, _ROW_H)

        self.setSortingEnabled(True)
        self.resizeColumnsToContents()
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)

    def _apply_filters(self, orders: List[Dict], filters: Dict[str, Any]) -> List[Dict]:
        """Apply filters to orders list."""
        filtered = list(orders or [])

        if filters.get('today_only'):
            today = datetime.now().strftime('%Y-%m-%d')
            filtered = [o for o in filtered if str(o.get('order_timestamp', '')).startswith(today)]

        if filters.get('symbol'):
            symbol = filters['symbol']
            filtered = [o for o in filtered if symbol in str(o.get('tradingsymbol', '')).upper()]

        if filters.get('status'):
            status = filters['status']
            filtered = [o for o in filtered if str(o.get('status', '')).upper() == status]

        if filters.get('transaction_type'):
            trans_type = filters['transaction_type']
            filtered = [o for o in filtered if str(o.get('transaction_type', '')).upper() == trans_type]

        if filters.get('date_from'):
            date_from = filters['date_from']
            safe_filtered = []
            for order in filtered:
                stamp = str(order.get('order_timestamp', ''))
                try:
                    if datetime.strptime(stamp[:10], '%Y-%m-%d').date() >= date_from:
                        safe_filtered.append(order)
                except Exception:
                    # Preserve malformed/unexpected broker timestamps rather than crashing the UI.
                    safe_filtered.append(order)
            filtered = safe_filtered

        return filtered

    def _populate_row(self, row: int, order: Dict[str, Any]):
        """Populate a single row with order data."""
        timestamp = order.get("order_timestamp", "")
        formatted_date = "—"
        formatted_time = "—"
        if timestamp:
            try:
                dt = datetime.strptime(str(timestamp), '%Y-%m-%d %H:%M:%S')
                formatted_date = dt.strftime('%Y-%m-%d')
                formatted_time = dt.strftime('%H:%M:%S')
            except Exception:
                timestamp_text = str(timestamp)
                if ' ' in timestamp_text:
                    formatted_date, formatted_time = timestamp_text.split(' ', 1)
                else:
                    formatted_time = timestamp_text

        date_item = self._item(formatted_date, _T2, Qt.AlignmentFlag.AlignCenter, mono=True)
        date_item.setToolTip(str(timestamp))
        self.setItem(row, 0, date_item)

        time_item = self._item(formatted_time, _T2, Qt.AlignmentFlag.AlignCenter, mono=True)
        time_item.setToolTip(str(timestamp))
        self.setItem(row, 1, time_item)

        symbol_item = self._item(str(order.get("tradingsymbol", "") or "—").upper(), _SYMBOL_SOFT, bold=True)
        self.setItem(row, 2, symbol_item)

        trans_type = str(order.get("transaction_type", "N/A") or "N/A").upper()
        type_color = _BULL if trans_type == "BUY" else _BEAR if trans_type == "SELL" else _T2
        type_item = self._item(trans_type, type_color, Qt.AlignmentFlag.AlignCenter, bold=True)
        self.setItem(row, 3, type_item)

        qty_item = self._item(str(order.get("quantity", 0)), _T1, Qt.AlignmentFlag.AlignCenter, mono=True)
        self.setItem(row, 4, qty_item)

        price = order.get('price', 0.0) or 0.0
        price_text = f"₹{float(price):,.2f}" if price > 0 else "MKT"
        price_item = self._item(price_text, _T1, Qt.AlignmentFlag.AlignRight, mono=True)
        self.setItem(row, 5, price_item)

        avg_price = order.get('average_price', 0.0) or 0.0
        avg_text = f"₹{float(avg_price):,.2f}" if avg_price > 0 else "—"
        avg_item = self._item(avg_text, _T1, Qt.AlignmentFlag.AlignRight, mono=True)
        self.setItem(row, 6, avg_item)

        status = str(order.get("status", "") or "—").upper()
        status_item = self._item(status, _status_color(status), Qt.AlignmentFlag.AlignCenter, bold=True)
        self.setItem(row, 7, status_item)

        order_id = str(order.get("order_id", "") or "—")
        id_item = self._item(order_id, _T3 if order_id == "—" else _T2, mono=True)
        id_item.setToolTip(f"Order ID: {order_id}")
        self.setItem(row, 8, id_item)

    def _item(
        self,
        text: str,
        color: str = _T0,
        align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft,
        mono: bool = False,
        bold: bool = False,
    ) -> QTableWidgetItem:
        item = QTableWidgetItem(str(text))
        item.setForeground(QBrush(QColor(color)))
        item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        item.setFont(_mono_font(9, bold) if mono else _ui_font(9, bold))
        return item

    def _on_selection_changed(self):
        """Handle row selection change."""
        current_row = self.currentRow()
        if 0 <= current_row < len(self._visible_orders):
            self.order_selected.emit(self._visible_orders[current_row])

    def get_selected_order(self) -> Optional[Dict]:
        """Get currently selected order."""
        current_row = self.currentRow()
        if 0 <= current_row < len(self._visible_orders):
            return self._visible_orders[current_row]
        return None

    def get_displayed_orders(self) -> List[Dict]:
        """Return orders currently visible after filtering/sorting."""
        return list(self._visible_orders)


class OrderSummaryWidget(QWidget):
    """Widget displaying order statistics and summary."""

    def __init__(self):
        super().__init__()
        self.setObjectName("summaryWidget")
        self._stats = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        summary_group = QGroupBox("ORDER SUMMARY")
        summary_group.setObjectName("summaryGroup")
        summary_layout = QGridLayout(summary_group)
        summary_layout.setContentsMargins(9, 9, 9, 9)
        summary_layout.setHorizontalSpacing(10)
        summary_layout.setVerticalSpacing(7)

        self.total_orders_label = self._value_label("totalValue")
        self.completed_orders_label = self._value_label("completeValue")
        self.cancelled_orders_label = self._value_label("cancelValue")
        self.pending_orders_label = self._value_label("pendingValue")
        self.total_volume_label = self._value_label("volumeValue")
        self.avg_order_size_label = self._value_label("avgValue")

        rows = [
            ("TOTAL", self.total_orders_label),
            ("COMPLETE", self.completed_orders_label),
            ("CANCELLED", self.cancelled_orders_label),
            ("PENDING", self.pending_orders_label),
            ("VOLUME", self.total_volume_label),
            ("AVG SIZE", self.avg_order_size_label),
        ]

        for row, (label_text, value_label) in enumerate(rows):
            label = QLabel(label_text)
            label.setObjectName("summaryLabel")
            summary_layout.addWidget(label, row, 0)
            summary_layout.addWidget(value_label, row, 1, alignment=Qt.AlignmentFlag.AlignRight)

        layout.addWidget(summary_group)
        layout.addStretch()

    def _value_label(self, object_name: str) -> QLabel:
        label = QLabel("0")
        label.setObjectName(object_name)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return label

    def update_summary(self, orders: List[Dict]):
        """Update summary with order data."""
        if not orders:
            self._reset_summary()
            return

        total_orders = len(orders)
        completed = len([o for o in orders if o.get('status') == 'COMPLETE'])
        cancelled = len([o for o in orders if o.get('status') == 'CANCELLED'])
        pending = len([o for o in orders if o.get('status') in ['OPEN', 'PENDING_EXECUTION']])

        total_volume = sum(
            (o.get('filled_quantity', 0) or o.get('quantity', 0)) *
            (o.get('average_price', 0) or o.get('price', 0))
            for o in orders if o.get('status') == 'COMPLETE'
        )

        avg_order_size = total_volume / completed if completed > 0 else 0

        self.total_orders_label.setText(str(total_orders))
        self.completed_orders_label.setText(str(completed))
        self.cancelled_orders_label.setText(str(cancelled))
        self.pending_orders_label.setText(str(pending))
        self.total_volume_label.setText(f"₹{total_volume:,.0f}")
        self.avg_order_size_label.setText(f"₹{avg_order_size:,.0f}")

        self._stats = {
            'total_orders': total_orders,
            'completed_orders': completed,
            'cancelled_orders': cancelled,
            'pending_orders': pending,
            'total_volume': total_volume,
            'avg_order_size': avg_order_size
        }

    def _reset_summary(self):
        """Reset all summary labels."""
        self.total_orders_label.setText("0")
        self.completed_orders_label.setText("0")
        self.cancelled_orders_label.setText("0")
        self.pending_orders_label.setText("0")
        self.total_volume_label.setText("₹0")
        self.avg_order_size_label.setText("₹0")
        self._stats = {}

    def get_statistics(self) -> Dict:
        """Get current statistics."""
        return self._stats.copy()


class OrderHistoryDialog(QDialog):
    """
    Comprehensive order history dialog for qullamaggie.
    Displays orders from TradeLogger with filtering, summary, and export capabilities.
    """

    refresh_requested = Signal()
    export_requested = Signal(dict)  # Emits current filter settings

    def __init__(self, trade_logger, parent=None):
        super().__init__(parent)
        self.trade_logger = trade_logger
        self._drag_active = False
        self._drag_offset = None
        self._orders_data: List[Dict[str, Any]] = []
        self._thread_pool = QThreadPool.globalInstance()
        self._refresh_inflight = False

        self._setup_window()
        self._setup_ui()
        self._apply_styles()
        self._connect_signals()

        self.auto_refresh_timer = QTimer(self)
        self.auto_refresh_timer.timeout.connect(self._auto_refresh)
        self.auto_refresh_timer.start(30000)

        self._refresh_data()

    def _setup_window(self):
        """Initialize window properties."""
        self.setWindowTitle("Order History - qullamaggie")
        self.setMinimumSize(880, 520)
        self.resize(1000, 620)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)

    def _setup_ui(self):
        """Build the main UI layout."""
        container = QFrame(self)
        container.setObjectName("mainContainer")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        container_layout.addWidget(self._create_header())

        body_widget = QWidget()
        body_widget.setObjectName("bodyWidget")
        body_layout = QVBoxLayout(body_widget)
        body_layout.setContentsMargins(8, 8, 8, 8)
        body_layout.setSpacing(8)

        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        content_splitter.setObjectName("contentSplitter")

        left_panel = QWidget()
        left_panel.setObjectName("leftPanel")
        left_panel.setFixedWidth(268)
        left_panel_layout = QVBoxLayout(left_panel)
        left_panel_layout.setContentsMargins(0, 0, 6, 0)
        left_panel_layout.setSpacing(8)

        self.filter_widget = FilterWidget()
        left_panel_layout.addWidget(self.filter_widget)

        self.summary_widget = OrderSummaryWidget()
        left_panel_layout.addWidget(self.summary_widget)

        right_panel = QWidget()
        right_panel.setObjectName("rightPanel")
        right_panel_layout = QVBoxLayout(right_panel)
        right_panel_layout.setContentsMargins(6, 0, 0, 0)
        right_panel_layout.setSpacing(0)

        self.orders_table = OrderHistoryTable()
        right_panel_layout.addWidget(self.orders_table)

        content_splitter.addWidget(left_panel)
        content_splitter.addWidget(right_panel)
        content_splitter.setSizes([268, 900])

        body_layout.addWidget(content_splitter, 1)
        container_layout.addWidget(body_widget, 1)
        container_layout.addWidget(self._create_footer())

    def _create_header(self) -> QFrame:
        """Create dialog header."""
        header = QFrame()
        header.setObjectName("titleBar")
        header.setFixedHeight(_TITLE_H)
        header.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))

        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 0, 7, 0)
        header_layout.setSpacing(8)

        title = QLabel("ORDER HISTORY")
        title.setObjectName("dialogTitle")

        self.status_label = QLabel("LOADING")
        self.status_label.setObjectName("statusLabel")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeBtn")
        close_btn.clicked.connect(self.close)
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(self.status_label)
        header_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        header.mousePressEvent = self._tb_press
        header.mouseMoveEvent = self._tb_move
        header.mouseReleaseEvent = self._tb_release
        return header

    def _create_footer(self) -> QFrame:
        """Create dialog footer."""
        footer = QFrame()
        footer.setObjectName("footerBar")
        footer.setFixedHeight(_FOOTER_H)

        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(10, 0, 10, 0)
        footer_layout.setSpacing(6)

        self.count_label = QLabel("0 orders displayed")
        self.count_label.setObjectName("footerLabel")

        refresh_btn = QPushButton("REFRESH")
        refresh_btn.setObjectName("secondaryBtn")
        refresh_btn.setFixedHeight(24)
        refresh_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        refresh_btn.clicked.connect(self._refresh_data)

        export_btn = QPushButton("EXPORT")
        export_btn.setObjectName("secondaryBtn")
        export_btn.setFixedHeight(24)
        export_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        export_btn.clicked.connect(self._export_data)

        clear_btn = QPushButton("CLEAR FILTERS")
        clear_btn.setObjectName("secondaryBtn")
        clear_btn.setFixedHeight(24)
        clear_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        clear_btn.clicked.connect(self.filter_widget._clear_filters)

        footer_layout.addWidget(self.count_label)
        footer_layout.addStretch()
        footer_layout.addWidget(clear_btn)
        footer_layout.addWidget(export_btn)
        footer_layout.addWidget(refresh_btn)

        return footer

    def _connect_signals(self):
        """Connect internal signals."""
        self.filter_widget.filter_changed.connect(self._apply_filters)
        self.orders_table.order_selected.connect(self._on_order_selected)

    def _refresh_data(self):
        """Refresh order data from trade logger."""
        if self._refresh_inflight:
            logger.debug("Order history refresh skipped — previous load still running")
            return

        self._refresh_inflight = True
        self.status_label.setText("LOADING")
        self.status_label.setProperty("state", "loading")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

        worker = Worker(self.trade_logger.get_all_orders, 1000)
        worker.signals.result.connect(self._handle_refresh_result)
        worker.signals.error.connect(lambda err: self._handle_refresh_error(err[1]))
        worker.signals.finished.connect(self._on_refresh_finished)
        self._thread_pool.start(worker)

    def _handle_refresh_result(self, orders):
        self._orders_data = orders or []
        self._apply_filters()

        self.status_label.setText(f"UPDATED {datetime.now().strftime('%H:%M:%S')}")
        self.status_label.setProperty("state", "ok")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        logger.info("Loaded %s orders from trade logger", len(self._orders_data))

    def _handle_refresh_error(self, error):
        logger.error("Failed to refresh order data: %s", error)
        self.status_label.setText("LOAD ERROR")
        self.status_label.setProperty("state", "error")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _on_refresh_finished(self):
        self._refresh_inflight = False

    def _auto_refresh(self):
        """Auto-refresh data periodically."""
        if self.isVisible():
            self._refresh_data()

    def _apply_filters(self):
        """Apply current filters to the order data."""
        filters = self.filter_widget.get_filters()

        self.orders_table.update_orders(self._orders_data, filters)
        self.summary_widget.update_summary(self._orders_data)

        displayed_count = self.orders_table.rowCount()
        total_count = len(self._orders_data)

        if displayed_count == total_count:
            self.count_label.setText(f"{total_count} orders displayed")
        else:
            self.count_label.setText(f"{displayed_count} of {total_count} orders displayed")

    def _on_order_selected(self, order: Dict):
        """Handle order selection."""
        logger.info(
            "Selected order: %s - %s",
            order.get('order_id'),
            order.get('tradingsymbol'),
        )

    def _export_data(self):
        """Export current filtered data."""
        filters = self.filter_widget.get_filters()
        stats = self.summary_widget.get_statistics()

        export_data = {
            'filters': filters,
            'statistics': stats,
            'orders': self.orders_table.get_displayed_orders(),
            'export_timestamp': datetime.now().isoformat()
        }

        self.export_requested.emit(export_data)
        logger.info("Order data export requested")

    def _apply_styles(self):
        """Apply Institutional Dark Trading Terminal UI styles."""
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {_BG0};
                color: {_T1};
                font-family: {_SANS};
            }}

            QFrame#mainContainer {{
                background-color: {_BG1};
                border: 1px solid {_BG4};
                border-radius: 2px;
            }}

            QFrame#titleBar {{
                background-color: {_BGTB};
                border-bottom: 1px solid {_BG4};
            }}

            QWidget#bodyWidget {{
                background-color: {_BG1};
            }}

            QWidget#leftPanel,
            QWidget#rightPanel {{
                background: transparent;
            }}

            QLabel#categoryBadge {{
                color: {_AMBER};
                background: rgba(245,158,11,0.08);
                border: 1px solid rgba(245,158,11,0.25);
                border-radius: 2px;
                padding: 2px 7px;
                font-family: {_SANS};
                font-size: 9px;
                font-weight: 900;
                letter-spacing: 1.2px;
            }}

            QLabel#dialogTitle {{
                color: {_T1};
                font-family: {_SANS};
                font-size: 11px;
                font-weight: 900;
                letter-spacing: 1.1px;
                background: transparent;
            }}

            QLabel#statusLabel {{
                color: {_T2};
                background: {_BG2};
                border: 1px solid {_BG4};
                border-radius: 2px;
                padding: 2px 7px;
                font-family: {_MONO};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 0.5px;
            }}
            QLabel#statusLabel[state="loading"] {{
                color: {_AMBER};
                border-color: rgba(245,158,11,0.28);
                background: rgba(245,158,11,0.07);
            }}
            QLabel#statusLabel[state="ok"] {{
                color: {_BULL};
                border-color: rgba(0,212,168,0.25);
                background: rgba(0,212,168,0.06);
            }}
            QLabel#statusLabel[state="error"] {{
                color: {_BEAR};
                border-color: rgba(255,77,106,0.28);
                background: rgba(255,77,106,0.07);
            }}

            QLabel#footerLabel {{
                color: {_T2};
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 700;
                background: transparent;
            }}

            QFrame#footerBar {{
                background-color: {_BGTB};
                border-top: 1px solid {_BG4};
            }}

            QPushButton#closeBtn {{
                background: transparent;
                color: {_T2};
                border: 1px solid transparent;
                font-size: 12px;
                font-weight: 900;
                border-radius: 2px;
            }}
            QPushButton#closeBtn:hover {{
                background: rgba(255, 77, 106, 0.15);
                color: {_BEAR};
                border-color: rgba(255, 77, 106, 0.30);
            }}

            QPushButton#secondaryBtn,
            QPushButton#clearButton {{
                background: {_BG2};
                color: {_T1};
                border: 1px solid {_BG4};
                border-radius: 2px;
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 0.5px;
                padding: 0 10px;
            }}
            QPushButton#secondaryBtn:hover {{
                background: rgba(0, 212, 255, 0.08);
                color: {_CYAN};
                border-color: rgba(0, 212, 255, 0.28);
            }}
            QPushButton#secondaryBtn:pressed {{
                background: {_BG0};
                border-color: {_CYAN};
            }}
            QPushButton#clearButton {{
                color: {_BEAR};
                border-color: rgba(255,77,106,0.25);
                min-width: 60px;
            }}
            QPushButton#clearButton:hover {{
                background: rgba(255,77,106,0.12);
                border-color: {_BEAR};
                color: {_BEAR};
            }}

            QSplitter#contentSplitter {{
                background-color: transparent;
            }}
            QSplitter#contentSplitter::handle {{
                background-color: {_BG4};
                width: 1px;
            }}
            QSplitter#contentSplitter::handle:hover {{
                background-color: {_CYAN};
            }}

            QWidget#filterWidget,
            QWidget#summaryWidget {{
                background-color: {_BG2};
                border: 1px solid {_BG4};
                border-radius: 2px;
            }}

            QGroupBox {{
                color: {_AMBER};
                font-family: {_SANS};
                font-weight: 900;
                font-size: 9px;
                letter-spacing: 1.1px;
                border: 1px solid {_BG4};
                border-radius: 2px;
                margin-top: 8px;
                padding-top: 8px;
                background-color: transparent;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 5px;
                background-color: {_BG2};
            }}

            QLabel#fieldLabel,
            QLabel#summaryLabel {{
                color: {_T2};
                font-family: {_SANS};
                font-size: 9px;
                font-weight: 900;
                letter-spacing: 0.8px;
                background: transparent;
            }}

            QLabel#totalValue,
            QLabel#completeValue,
            QLabel#cancelValue,
            QLabel#pendingValue,
            QLabel#volumeValue,
            QLabel#avgValue {{
                color: {_T1};
                font-family: {_MONO};
                font-size: 11px;
                font-weight: 800;
                background: transparent;
            }}
            QLabel#completeValue {{ color: {_BULL}; }}
            QLabel#cancelValue {{ color: {_BEAR}; }}
            QLabel#pendingValue {{ color: {_AMBER}; }}
            QLabel#volumeValue,
            QLabel#avgValue {{ color: {_CYAN}; }}

            QLineEdit#terminalInput,
            QComboBox#terminalCombo,
            QDateEdit#terminalDate {{
                background-color: {_BG1};
                border: 1px solid {_BG4};
                color: {_T1};
                padding: 3px 7px;
                border-radius: 2px;
                font-family: {_SANS};
                font-size: 11px;
                font-weight: 600;
                min-height: 20px;
                selection-background-color: {_SEL};
                selection-color: {_T0};
            }}
            QLineEdit#terminalInput:hover,
            QComboBox#terminalCombo:hover,
            QDateEdit#terminalDate:hover {{
                background-color: {_BG3};
                border-color: {_T2};
            }}
            QLineEdit#terminalInput:focus,
            QComboBox#terminalCombo:focus,
            QDateEdit#terminalDate:focus {{
                border-color: {_CYAN};
                background-color: {_BG3};
                color: {_T0};
            }}
            QLineEdit#terminalInput::placeholder {{
                color: {_T3};
            }}

            QComboBox#terminalCombo::drop-down,
            QDateEdit#terminalDate::drop-down {{
                border: none;
                width: 18px;
                background: transparent;
            }}
            QComboBox#terminalCombo::down-arrow,
            QDateEdit#terminalDate::down-arrow {{
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid {_T2};
                margin-right: 5px;
            }}
            QComboBox#terminalCombo QAbstractItemView {{
                background-color: {_BG1};
                border: 1px solid {_BG4};
                selection-background-color: {_SEL};
                selection-color: {_T0};
                color: {_T1};
                outline: none;
                padding: 2px;
                font-family: {_SANS};
                font-size: 11px;
            }}

            QCheckBox#terminalCheck {{
                color: {_T1};
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 0.3px;
                spacing: 6px;
                background: transparent;
            }}
            QCheckBox#terminalCheck::indicator {{
                width: 13px;
                height: 13px;
                border: 1px solid {_BG4};
                border-radius: 2px;
                background-color: {_BG1};
            }}
            QCheckBox#terminalCheck::indicator:hover {{
                border-color: {_CYAN};
            }}
            QCheckBox#terminalCheck::indicator:checked {{
                background-color: {_CYAN};
                border-color: {_CYAN};
            }}

            QTableWidget#ordersTable {{
                background-color: {_BG1};
                alternate-background-color: {_BG2};
                border: 1px solid {_BG4};
                gridline-color: transparent;
                font-family: {_SANS};
                font-size: 11px;
                color: {_T1};
                selection-background-color: {_SEL};
                selection-color: {_T0};
                border-radius: 2px;
                outline: none;
            }}

            QTableWidget#ordersTable::item {{
                padding: 0 6px;
                border-bottom: 1px solid {_BG3};
                border-right: none;
                background: transparent;
            }}
            QTableWidget#ordersTable::item:selected {{
                background-color: {_SEL};
                color: {_T0};
            }}
            QTableWidget#ordersTable::item:hover {{
                background-color: {_BG3};
            }}
            QTableWidget#ordersTable::item:alternate {{
                background-color: {_BG2};
            }}
            QTableWidget#ordersTable::item:alternate:selected {{
                background-color: {_SEL};
                color: {_T0};
            }}

            QHeaderView::section {{
                background-color: {_BG2};
                color: {_T2};
                padding: 0 6px;
                border: none;
                border-bottom: 1px solid {_BG4};
                font-family: {_SANS};
                font-weight: 900;
                font-size: 8px;
                text-transform: uppercase;
                letter-spacing: 1px;
                min-height: {_HEADER_H}px;
                max-height: {_HEADER_H}px;
            }}
            QHeaderView::section:hover {{
                background-color: {_BG3};
                color: {_T1};
            }}
            QHeaderView {{
                background-color: {_BG2};
                border: none;
            }}

            QScrollBar:vertical {{
                background: transparent;
                width: 4px;
                border: none;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {_BG4};
                border-radius: 2px;
                min-height: 18px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {_T2};
            }}
            QScrollBar:horizontal {{
                background: transparent;
                height: 4px;
                border: none;
                margin: 0;
            }}
            QScrollBar::handle:horizontal {{
                background: {_BG4};
                border-radius: 2px;
                min-width: 18px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {_T2};
            }}
            QScrollBar::add-line,
            QScrollBar::sub-line {{
                border: none;
                background: none;
                width: 0;
                height: 0;
                margin: 0;
            }}

            QToolTip {{
                background-color: {_BG2};
                color: {_T1};
                border: 1px solid {_BG4};
                border-radius: 2px;
                padding: 4px 6px;
                font-family: {_SANS};
                font-size: 10px;
            }}
        """)

    def _tb_press(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _tb_move(self, event: QMouseEvent):
        if self._drag_active and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def _tb_release(self, _event: QMouseEvent):
        self._drag_active = False

    def mousePressEvent(self, event: QMouseEvent):
        w = self.childAt(event.pos())
        while w:
            if isinstance(w, (QAbstractButton, QAbstractSpinBox, QLineEdit, QComboBox, QTableWidget)):
                return super().mousePressEvent(event)
            w = w.parentWidget()
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_active and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_active = False
        super().mouseReleaseEvent(event)

    def showEvent(self, event):
        """Handle dialog show event."""
        super().showEvent(event)
        self._center_on_parent()
        self._refresh_data()

    def _center_on_parent(self):
        if self.parent():
            parent_geo = self.parent().frameGeometry()
            center = parent_geo.center()
            self.move(center - self.rect().center())
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.center() - self.rect().center())

    def closeEvent(self, event):
        """Handle dialog close event."""
        self.auto_refresh_timer.stop()
        super().closeEvent(event)

    # Public interface methods
    def refresh_orders(self):
        """Public method to refresh orders."""
        self._refresh_data()

    def get_current_filters(self) -> Dict[str, Any]:
        """Get current filter settings."""
        return self.filter_widget.get_filters()

    def set_symbol_filter(self, symbol: str):
        """Set symbol filter programmatically."""
        self.filter_widget.symbol_filter.setText(symbol)

    def get_displayed_orders(self) -> List[Dict]:
        """Get currently displayed orders."""
        return self.orders_table.get_displayed_orders()
