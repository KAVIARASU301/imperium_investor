import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
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
    QMessageBox,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox,
    QFormLayout,
)

from kite.widgets.status_bar import show_error, show_info, show_order_cancelled

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
            QMessageBox.warning(self, "Invalid Price", "Limit/SL orders require a positive price.")
            return None
        if order_type in {"SL", "SL-M"} and trigger_price <= 0:
            QMessageBox.warning(self, "Invalid Trigger", "SL/SL-M orders require a positive trigger price.")
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

        self.setWindowTitle("Pending Orders")
        self.setMinimumSize(980, 560)

        self._setup_ui()
        self._connect_signals()

        self.auto_refresh_timer = QTimer(self)
        self.auto_refresh_timer.timeout.connect(self.refresh_orders)
        self.auto_refresh_timer.start(7000)

        self.refresh_orders()

    def _setup_ui(self):
        root = QVBoxLayout(self)

        header = QHBoxLayout()
        self.status_label = QLabel("Loading pending orders…")
        self.status_label.setObjectName("statusLabel")
        header.addWidget(self.status_label)
        header.addStretch()

        self.count_label = QLabel("0 pending")
        header.addWidget(self.count_label)
        root.addLayout(header)

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

        root.addWidget(self.table)

        footer = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.cancel_btn = QPushButton("Cancel Selected")
        self.edit_btn = QPushButton("Edit Selected")
        self.close_btn = QPushButton("Close")

        footer.addWidget(self.refresh_btn)
        footer.addStretch()
        footer.addWidget(self.edit_btn)
        footer.addWidget(self.cancel_btn)
        footer.addWidget(self.close_btn)
        root.addLayout(footer)

        self._set_action_state(False)

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
        try:
            all_orders = self.trader.orders() or []
            pending = [order for order in all_orders if (order.get("status", "").upper() in PENDING_STATUSES)]

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

        except Exception as exc:
            logger.error("Failed to refresh pending orders: %s", exc, exc_info=True)
            self.status_label.setText("Failed to load pending orders")
            show_error(f"Pending orders refresh failed: {exc}")

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

        confirm = QMessageBox.question(
            self,
            "Cancel Order",
            f"Cancel pending order {order_id} ({symbol})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

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
