import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal, QThreadPool, QPoint
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
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
)

from kite.widgets.status_bar import show_error, show_info, show_order_cancelled
from kite.utils.worker import Worker

logger = logging.getLogger(__name__)

PENDING_STATUSES = {
    "OPEN",
    "PENDING",
    "PENDING_EXECUTION",
    "TRIGGER PENDING",
    "VALIDATION PENDING",
    "PUT ORDER REQ RECEIVED",
    "MODIFY VALIDATION PENDING",
    "MODIFY PENDING",
    "AMO REQ RECEIVED",
}


class EditPendingOrderDialog(QDialog):
    """Small editor for pending order modifications with strict validation."""

    def __init__(self, order: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.order = order
        self.setWindowTitle(f"Edit Pending Order • {order.get('tradingsymbol', '')}")
        self.setModal(True)
        self.setMinimumWidth(380)

        root = QVBoxLayout(self)

        form = QFormLayout()

        self.quantity_input = QSpinBox()
        self.quantity_input.setRange(1, 10_000_000)
        self.quantity_input.setValue(int(order.get("quantity") or 1))
        form.addRow("Quantity", self.quantity_input)

        self.order_type_input = QComboBox()
        self.order_type_input.addItems(["MARKET", "LIMIT", "SL", "SL-M"])
        current_type = (order.get("order_type") or "MARKET").upper()
        idx = self.order_type_input.findText(current_type)
        if idx >= 0:
            self.order_type_input.setCurrentIndex(idx)
        form.addRow("Order Type", self.order_type_input)

        self.price_input = QDoubleSpinBox()
        self.price_input.setDecimals(2)
        self.price_input.setRange(0.0, 10_000_000.0)
        self.price_input.setSingleStep(0.05)
        self.price_input.setValue(float(order.get("price") or 0.0))
        form.addRow("Limit Price", self.price_input)

        self.trigger_input = QDoubleSpinBox()
        self.trigger_input.setDecimals(2)
        self.trigger_input.setRange(0.0, 10_000_000.0)
        self.trigger_input.setSingleStep(0.05)
        self.trigger_input.setValue(float(order.get("trigger_price") or 0.0))
        form.addRow("Trigger Price", self.trigger_input)

        self.validity_input = QComboBox()
        self.validity_input.addItems(["DAY", "IOC"])
        current_validity = (order.get("validity") or "DAY").upper()
        idx = self.validity_input.findText(current_validity)
        if idx >= 0:
            self.validity_input.setCurrentIndex(idx)
        form.addRow("Validity", self.validity_input)

        root.addLayout(form)

        controls = QHBoxLayout()
        controls.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        controls.addWidget(cancel_btn)

        save_btn = QPushButton("Save Changes")
        save_btn.clicked.connect(self._on_save)
        controls.addWidget(save_btn)
        root.addLayout(controls)

        self.order_type_input.currentTextChanged.connect(self._sync_field_states)
        self._sync_field_states()

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
        self._thread_pool = QThreadPool.globalInstance()
        self._refresh_inflight = False

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
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        title_bar = QWidget()
        title_bar.setFixedHeight(36)
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(12, 0, 8, 0)
        title_layout.setSpacing(8)
        badge = QLabel("ORDERS")
        badge.setObjectName("categoryBadge")
        self.title_label = QLabel("PENDING ORDERS")
        self.refresh_btn = QPushButton("↺")
        self.refresh_btn.setObjectName("toolBtn")
        self.refresh_btn.setToolTip("Refresh pending orders")
        self.refresh_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setToolTip("Close")
        self.close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        title_layout.addWidget(badge)
        title_layout.addWidget(self.title_label)
        title_layout.addStretch()
        title_layout.addWidget(self.refresh_btn)
        title_layout.addWidget(self.close_btn)
        root.addWidget(title_bar)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 16, 16, 16)
        body_layout.setSpacing(12)

        header = QHBoxLayout()
        self.status_label = QLabel("Loading pending orders…")
        self.status_label.setObjectName("statusNeutral")
        header.addWidget(self.status_label)
        header.addStretch()

        self.count_label = QLabel("0 pending")
        self.count_label.setObjectName("countLabel")
        header.addWidget(self.count_label)
        body_layout.addLayout(header)

        self.table = QTableWidget()
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
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)

        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        body_layout.addWidget(self.table)
        root.addWidget(body)

        footer = QWidget()
        footer.setFixedHeight(40)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 0, 16, 0)
        footer_layout.setSpacing(8)
        self.footer_status_label = QLabel("⚠ PENDING")
        self.footer_status_label.setObjectName("statusWarning")
        self.cancel_btn = QPushButton("CANCEL SELECTED")
        self.cancel_btn.setObjectName("destructiveBtn")
        self.edit_btn = QPushButton("EDIT SELECTED")
        self.edit_btn.setObjectName("secondaryBtn")
        footer_layout.addWidget(self.footer_status_label)
        footer_layout.addStretch()
        footer_layout.addWidget(self.edit_btn)
        footer_layout.addWidget(self.cancel_btn)
        root.addWidget(footer)

        self._apply_styles()
        self._set_action_state(False)


    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog { background: #0a0d12; border: 1px solid #1a2030; border-radius: 1px; }
            QWidget { color: #a8bcd4; font-family: 'Inter', 'Segoe UI', sans-serif; }
            QLabel#categoryBadge { color: #00d4ff; font-size: 9px; font-weight: 700; letter-spacing: 1px; }
            QLabel { color: #a8bcd4; }
            QLabel#countLabel { color: #e8f0ff; font-family: 'Consolas', 'JetBrains Mono', monospace; font-weight: 700; }
            QPushButton#toolBtn, QPushButton#closeBtn { min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; border: none; border-radius: 2px; background: transparent; color: #5a7090; font-size: 14px; font-weight: 700; }
            QPushButton#toolBtn:hover { background: #141920; color: #e8f0ff; }
            QPushButton#closeBtn:hover { background: rgba(255, 77, 106, 0.15); color: #ff4d6a; }
            QPushButton#secondaryBtn { background: #0f1318; color: #a8bcd4; border: 1px solid #1a2030; border-radius: 1px; font-size: 11px; font-weight: 700; padding: 0 16px; min-height: 28px; }
            QPushButton#secondaryBtn:hover { background: #141920; color: #e8f0ff; }
            QPushButton#destructiveBtn { background: rgba(255, 77, 106, 0.08); color: #ff4d6a; border: 1px solid rgba(255, 77, 106, 0.25); border-radius: 1px; font-size: 11px; font-weight: 800; padding: 0 16px; min-height: 28px; }
            QPushButton#destructiveBtn:hover { background: rgba(255, 77, 106, 0.15); border-color: #ff4d6a; }
            QTableWidget { background: #0f1318; gridline-color: #1a2030; border: 1px solid #1a2030; color: #e8f0ff; font-family: 'Consolas', 'JetBrains Mono', monospace; font-size: 12px; selection-background-color: #1a2840; }
            QHeaderView::section { background: #070a0f; color: #5a7090; font-size: 9px; font-weight: 800; letter-spacing: 1px; border: none; border-right: 1px solid #1a2030; border-bottom: 1px solid #1a2030; min-height: 26px; }
            QLabel#statusNeutral { color: #5a7090; font-size: 10px; font-weight: 600; }
            QLabel#statusWarning { color: #f59e0b; font-size: 10px; font-weight: 700; }
        """)

    def _connect_signals(self):
        self.refresh_btn.clicked.connect(self.refresh_orders)
        self.cancel_btn.clicked.connect(self.cancel_selected_order)
        self.edit_btn.clicked.connect(self.edit_selected_order)
        self.close_btn.clicked.connect(self.close)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

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
        return self._orders_by_id.get(order_id_item.text())

    def refresh_orders(self):
        if not self.isVisible():
            return

        if self._refresh_inflight:
            logger.debug("Pending order refresh skipped — previous broker request still running")
            return

        self._refresh_inflight = True
        self.status_label.setText("Syncing with Kite...")
        worker = Worker(self.trader.orders)
        worker.signals.result.connect(self._handle_orders_result)
        worker.signals.error.connect(lambda err: self._handle_orders_error(err[1]))
        worker.signals.finished.connect(self._on_refresh_finished)
        self._thread_pool.start(worker)

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
        now = datetime.now().strftime("%H:%M:%S")
        self.status_label.setText(f"Synced with Kite at {now}")
        self.pending_orders_updated.emit(len(self._orders))

    def _handle_orders_error(self, exc):
        logger.error("Failed to refresh pending orders: %s", exc, exc_info=True)
        self.status_label.setText("Failed to load pending orders")
        show_error(f"Pending orders refresh failed: {exc}")

    def _on_refresh_finished(self):
        self._refresh_inflight = False

    def _render_table(self):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        for order in self._orders:
            row = self.table.rowCount()
            self.table.insertRow(row)

            ts = order.get("order_timestamp") or order.get("exchange_timestamp") or "-"
            self.table.setItem(row, 0, QTableWidgetItem(str(ts)))
            self.table.setItem(row, 1, QTableWidgetItem(str(order.get("order_id", ""))))
            self.table.setItem(row, 2, QTableWidgetItem(str(order.get("tradingsymbol", ""))))
            self.table.setItem(row, 3, QTableWidgetItem(str(order.get("transaction_type", ""))))
            self.table.setItem(row, 4, QTableWidgetItem(str(order.get("quantity", 0))))
            self.table.setItem(row, 5, QTableWidgetItem(str(order.get("filled_quantity", 0))))
            self.table.setItem(row, 6, QTableWidgetItem(str(order.get("pending_quantity", 0))))

            price = float(order.get("price") or 0.0)
            trigger = float(order.get("trigger_price") or 0.0)
            self.table.setItem(row, 7, QTableWidgetItem("Market" if price <= 0 else f"₹{price:.2f}"))
            self.table.setItem(row, 8, QTableWidgetItem("-" if trigger <= 0 else f"₹{trigger:.2f}"))
            self.table.setItem(row, 9, QTableWidgetItem(str(order.get("status", ""))))

        self.table.setSortingEnabled(True)
        self._set_action_state(self.selected_order() is not None)

    def cancel_selected_order(self):
        order = self.selected_order()
        if not order:
            show_error("Select an order to cancel")
            return

        order_id = order.get("order_id")
        symbol = order.get("tradingsymbol", "")
        variety = order.get("variety") or "regular"


        try:
            self.trader.cancel_order(variety=variety, order_id=order_id)
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
        variety = order.get("variety") or "regular"
        edit_dialog = EditPendingOrderDialog(order, parent=self)

        if edit_dialog.exec() != QDialog.DialogCode.Accepted:
            return

        payload = edit_dialog.get_payload()
        if payload is None:
            return

        try:
            self.trader.modify_order(
                variety=variety,
                order_id=order_id,
                quantity=payload.get("quantity"),
                price=payload.get("price"),
                order_type=payload.get("order_type"),
                trigger_price=payload.get("trigger_price"),
                validity=payload.get("validity"),
            )
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
        if self.parent():
            parent_geo = self.parent().frameGeometry()
            center = parent_geo.center()
            self.move(center - self.rect().center())

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
