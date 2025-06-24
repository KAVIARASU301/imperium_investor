"""
Advanced Alert Management System for Trading Terminal
======================================================

This system provides a comprehensive alert management interface with:
- A three-tab workflow: Active -> Triggered -> History.
- An acknowledgement system for handling triggered alerts.
- A streamlined UI with compact, sharp-edged styling.
- A self-contained AlertCreationDialog.
"""
import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QLabel, QPushButton, QWidget, QLineEdit, QComboBox,
    QMessageBox, QCheckBox, QAbstractItemView, QFormLayout
)
from PySide6.QtGui import QMouseEvent
from PySide6.QtCore import Qt, Signal, QThread, QMutex, QMutexLocker, Slot, QObject
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import json
import os
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class AlertCondition(Enum):
    """Simplified alert condition types."""
    PRICE_IS_ABOVE = "Price is Above"
    PRICE_IS_BELOW = "Price is Below"


class AlertIntent(Enum):
    """Intelligent alert intent detection."""
    BUY_ENTRY = "Buy Entry Signal"
    SELL_ENTRY = "Short Entry Signal"
    PROFIT_TARGET = "Profit Target"
    STOP_LOSS = "Stop Loss"
    BREAKOUT_WATCH = "Breakout Watch"
    SUPPORT_WATCH = "Support Watch"


@dataclass
class Alert:
    """Enhanced alert data structure with acknowledgement status."""
    id: str
    symbol: str
    price: float
    condition: AlertCondition
    intent: AlertIntent
    note: str
    validity_days: int
    created_time: datetime
    expiry_time: datetime
    triggered: bool = False
    triggered_time: Optional[datetime] = None
    triggered_price: Optional[float] = None
    acknowledged: bool = False

    def to_dict(self) -> Dict:
        data = asdict(self)
        data['condition'] = self.condition.value
        data['intent'] = self.intent.value
        data['created_time'] = self.created_time.isoformat()
        data['expiry_time'] = self.expiry_time.isoformat()
        if self.triggered_time:
            data['triggered_time'] = self.triggered_time.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict) -> 'Alert':
        acknowledged = data.get('acknowledged', False)
        if data.get('triggered') and not acknowledged:
            acknowledged = False
        return cls(
            id=data['id'], symbol=data['symbol'], price=data['price'],
            condition=AlertCondition(data['condition']), intent=AlertIntent(data['intent']),
            note=data['note'], validity_days=data['validity_days'],
            created_time=datetime.fromisoformat(data['created_time']),
            expiry_time=datetime.fromisoformat(data['expiry_time']),
            triggered=data.get('triggered', False),
            triggered_time=datetime.fromisoformat(data['triggered_time']) if data.get('triggered_time') else None,
            triggered_price=data.get('triggered_price'), acknowledged=acknowledged
        )

# --- Alert Creation Dialog ---
class AlertCreationDialog(QDialog):
    """A self-contained dialog for creating new alerts."""
    alert_created = Signal(Alert)

    def __init__(self, parent=None, symbol: str = "", price: float = 0.0, intent: str = "", note: str = "", current_ltp: float = 0.0):
        super().__init__(parent)
        self.symbol = symbol.upper()
        self.price = price
        self.intent_str = intent
        self.note_str = note
        self.current_ltp = current_ltp
        self._drag_pos = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self._setup_ui()
        self._apply_styles()
        self._prefill_intelligent_defaults()

    def _setup_ui(self):
        self.setWindowTitle("Create Price Alert")
        self.setModal(True)
        self.setMinimumSize(450, 380)

        container = QWidget(self); container.setObjectName("alertDialogContainer")
        main_layout = QVBoxLayout(self); main_layout.setContentsMargins(0, 0, 0, 0); main_layout.addWidget(container)
        layout = QVBoxLayout(container); layout.setContentsMargins(20, 15, 20, 20); layout.setSpacing(15)

        header_layout = QHBoxLayout()
        title = QLabel("Create Price Alert"); title.setObjectName("dialogTitle")
        close_btn = QPushButton("✕"); close_btn.setObjectName("closeButton"); close_btn.clicked.connect(self.reject)
        header_layout.addWidget(title); header_layout.addStretch(); header_layout.addWidget(close_btn)
        layout.addLayout(header_layout)

        form_layout = QFormLayout(); form_layout.setSpacing(12)
        self.symbol_input = QLineEdit(self.symbol); self.symbol_input.setObjectName("alertInput")
        self.price_input = QLineEdit(f"{self.price:.2f}"); self.price_input.setObjectName("alertInput")
        self.condition_combo = QComboBox(); self.condition_combo.setObjectName("alertCombo")
        self.condition_combo.addItems([c.value for c in AlertCondition])
        self.intent_combo = QComboBox(); self.intent_combo.setObjectName("alertCombo")
        self.intent_combo.addItems([i.value for i in AlertIntent])
        self.validity_combo = QComboBox(); self.validity_combo.setObjectName("alertCombo")
        self.validity_combo.addItems(["1 Day", "3 Days", "1 Week", "2 Weeks", "1 Month"]); self.validity_combo.setCurrentText("1 Week")
        self.note_input = QLineEdit(self.note_str); self.note_input.setObjectName("alertInput")

        form_layout.addRow("Symbol:", self.symbol_input)
        form_layout.addRow("Alert Price:", self.price_input)
        form_layout.addRow("Condition:", self.condition_combo)
        form_layout.addRow("Intent:", self.intent_combo)
        form_layout.addRow("Validity:", self.validity_combo)
        form_layout.addRow("Note:", self.note_input)
        layout.addLayout(form_layout)

        button_layout = QHBoxLayout(); button_layout.addStretch()
        cancel_btn = QPushButton("Cancel"); cancel_btn.setObjectName("cancelButton"); cancel_btn.clicked.connect(self.reject)
        create_btn = QPushButton("Create Alert"); create_btn.setObjectName("createButton"); create_btn.clicked.connect(self._create_alert)
        button_layout.addWidget(cancel_btn); button_layout.addWidget(create_btn)
        layout.addLayout(button_layout)

    def _prefill_intelligent_defaults(self):
        if self.price > self.current_ltp:
            self.condition_combo.setCurrentText(AlertCondition.PRICE_IS_ABOVE.value)
        else:
            self.condition_combo.setCurrentText(AlertCondition.PRICE_IS_BELOW.value)

        # Prefill intent if provided by chart context menu
        intent_map = {
            'buy_entry': AlertIntent.BUY_ENTRY, 'sell_entry': AlertIntent.SELL_ENTRY,
            'profit_target': AlertIntent.PROFIT_TARGET, 'stop_loss': AlertIntent.STOP_LOSS,
            'resistance': AlertIntent.BREAKOUT_WATCH, 'support': AlertIntent.SUPPORT_WATCH
        }
        if self.intent_str in intent_map:
            self.intent_combo.setCurrentText(intent_map[self.intent_str].value)

    def _create_alert(self):
        try:
            symbol = self.symbol_input.text().strip().upper()
            price = float(self.price_input.text().strip())
            validity_days = {"1 Day": 1, "3 Days": 3, "1 Week": 7, "2 Weeks": 14, "1 Month": 30}.get(self.validity_combo.currentText(), 7)
            alert = Alert(
                id=f"{symbol}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}", symbol=symbol, price=price,
                condition=AlertCondition(self.condition_combo.currentText()), intent=AlertIntent(self.intent_combo.currentText()),
                note=self.note_input.text().strip(), validity_days=validity_days,
                created_time=datetime.now(), expiry_time=datetime.now() + timedelta(days=validity_days)
            )
            self.alert_created.emit(alert)
            self.accept()
        except (ValueError, KeyError) as e:
            QMessageBox.warning(self, "Invalid Input", f"Please check your inputs: {e}")

    def _apply_styles(self): self.setStyleSheet("QWidget#alertDialogContainer { background-color: #0a0a0a; border: 1px solid #282828; border-radius: 8px; } QLabel#dialogTitle { color: #e0e0e0; font-size: 16px; font-weight: 600; } QPushButton#closeButton { background-color: transparent; border: none; color: #8a8a9e; font-size: 16px; } QPushButton#closeButton:hover { color: #d63031; } QLineEdit#alertInput, QComboBox#alertCombo { background-color: #1a1a1a; border: 1px solid #333; border-radius: 4px; padding: 8px; color: #e0e0e0; } QLineEdit#alertInput:focus, QComboBox#alertCombo:focus { border-color: #6a9cff; } QPushButton#createButton { background-color: #4CAF50; color: white; border: none; border-radius: 4px; padding: 10px 20px; font-weight: bold; } QPushButton#cancelButton { background-color: #555; color: white; border: none; border-radius: 4px; padding: 10px 20px; }")
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton: self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft(); event.accept()
    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.LeftButton and self._drag_pos: self.move(event.globalPosition().toPoint() - self._drag_pos); event.accept()
    def mouseReleaseEvent(self, event: QMouseEvent): self._drag_pos = None; event.accept()


# --- Main Alert System Components ---
class AlertEngine(QThread):
    """Background thread for reliable alert processing."""
    alert_triggered = Signal(object, float)
    alert_expired = Signal(object)
    def __init__(self, parent=None):
        super().__init__(parent)
        self._alerts: List[Alert] = []; self._market_data: Dict[str, float] = {}; self._check_interval_ms = 5000
        self._running = False; self._mutex = QMutex()
    def update_alerts(self, alerts: List[Alert]):
        with QMutexLocker(self._mutex): self._alerts = [a for a in alerts if not a.triggered and a.expiry_time > datetime.now()]
    def update_market_data(self, market_data_ticks: List[Dict]):
        with QMutexLocker(self._mutex):
            for tick in market_data_ticks: self._market_data[tick['symbol']] = tick['price']
    def run(self):
        self._running = True; logger.info("AlertEngine thread started.")
        while self._running: self._check_alerts(); self.msleep(self._check_interval_ms)
        logger.info("AlertEngine thread stopped.")
    def stop(self): self._running = False
    def _check_alerts(self):
        with QMutexLocker(self._mutex):
            if not self._alerts: return
            now = datetime.now(); triggered_alerts, expired_alerts = [], []
            for alert in self._alerts:
                if alert.expiry_time <= now: expired_alerts.append(alert); continue
                price = self._market_data.get(alert.symbol)
                if price is None: continue
                if (alert.condition == AlertCondition.PRICE_IS_ABOVE and price >= alert.price) or \
                   (alert.condition == AlertCondition.PRICE_IS_BELOW and price <= alert.price):
                    alert.triggered = True; alert.triggered_time = now; alert.triggered_price = price; triggered_alerts.append(alert)
            for a in expired_alerts: self._alerts.remove(a); self.alert_expired.emit(a)
            for a in triggered_alerts:
                if a in self._alerts: self._alerts.remove(a)
                self.alert_triggered.emit(a, a.triggered_price)

class AdvancedAlertManager(QDialog):
    """Main alert management window with a three-tab workflow."""
    symbol_selected = Signal(str)
    alert_sound_requested = Signal()
    def __init__(self, parent=None, instrument_map: Dict = None, positions: Dict = None):
        super().__init__(parent)
        self.instrument_map = instrument_map or {}; self.current_positions = positions or {}
        self.all_alerts: List[Alert] = []; self._drag_pos = None
        self.alert_engine = AlertEngine(self)
        self.alert_engine.alert_triggered.connect(self._on_alert_triggered)
        self.alert_engine.alert_expired.connect(self._on_alert_expired)
        self._setup_ui(); self._apply_styles(); self._load_alerts(); self.alert_engine.start()
    def _setup_ui(self):
        self.setWindowTitle("Alert Manager"); self.setMinimumSize(1100, 700)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog); self.setAttribute(Qt.WA_TranslucentBackground, True)
        container = QWidget(self); container.setObjectName("mainContainer")
        main_layout = QVBoxLayout(self); main_layout.setContentsMargins(0, 0, 0, 0); main_layout.addWidget(container)
        layout = QVBoxLayout(container); layout.setContentsMargins(15, 10, 15, 15); layout.setSpacing(10)
        layout.addLayout(self._create_header())
        self.tab_widget = QTabWidget(); self.tab_widget.setObjectName("alertTabs")
        self.active_table = self._create_table(["Symbol", "Alert Price", "Current LTP", "Condition", "Note", "Expires", ""])
        self.triggered_table = self._create_table(["", "Trigger Time", "Symbol", "Alert Price", "Trigger Price", "Note"])
        self.history_table = self._create_table(["", "Trigger Time", "Symbol", "Alert Price", "Trigger Price", "Note"], sorting=True)
        self.tab_widget.addTab(self.active_table, "Active Alerts")
        self.tab_widget.addTab(self.triggered_table, "Triggered Alerts")
        self.tab_widget.addTab(self.history_table, "Alert History")
        layout.addWidget(self.tab_widget)
        layout.addLayout(self._create_status_bar())
    def _create_header(self) -> QHBoxLayout:
        header_layout = QHBoxLayout(); title = QLabel("Alert Manager"); title.setObjectName("dialogTitle")
        close_btn = QPushButton("✕"); close_btn.setObjectName("closeButton"); close_btn.clicked.connect(self.close)
        header_layout.addWidget(title); header_layout.addStretch(); header_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignTop)
        return header_layout
    def _create_table(self, headers: List[str], sorting=False) -> QTableWidget:
        table = QTableWidget(0, len(headers)); table.setObjectName("alertTable")
        table.setHorizontalHeaderLabels(headers); table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True); table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers); table.setAlternatingRowColors(True)
        table.cellDoubleClicked.connect(self._on_symbol_double_clicked); table.setSortingEnabled(sorting)
        return table
    def _create_status_bar(self) -> QHBoxLayout:
        status_layout = QHBoxLayout(); self.active_count_label = QLabel("Active: 0"); self.triggered_count_label = QLabel("Triggered: 0")
        self.active_count_label.setObjectName("statusLabel"); self.triggered_count_label.setObjectName("statusLabel")
        status_layout.addWidget(self.active_count_label); status_layout.addWidget(self.triggered_count_label); status_layout.addStretch()
        return status_layout
    def _add_alert(self, alert: Alert): self.all_alerts.append(alert); self._save_and_refresh_all()
    @Slot(int, int)
    def _on_symbol_double_clicked(self, row: int, column: int):
        table = self.sender(); symbol_col_index = -1
        for i in range(table.columnCount()):
            if table.horizontalHeaderItem(i).text() == "Symbol": symbol_col_index = i; break
        if symbol_col_index != -1:
            symbol_item = table.item(row, symbol_col_index)
            if symbol_item: self.symbol_selected.emit(symbol_item.text())
    def _refresh_all_tables(self):
        active = [a for a in self.all_alerts if not a.triggered]
        triggered = [a for a in self.all_alerts if a.triggered and not a.acknowledged]
        history = [a for a in self.all_alerts if a.triggered and a.acknowledged]
        self._populate_active_table(active); self._populate_triggered_table(triggered); self._populate_history_table(history)
        self._update_status(len(active), len(triggered)); self.alert_engine.update_alerts(active)
    def _populate_active_table(self, alerts: List[Alert]):
        self.active_table.setRowCount(0)
        for alert in sorted(alerts, key=lambda a: a.created_time, reverse=True):
            row = self.active_table.rowCount(); self.active_table.insertRow(row)
            self.active_table.setItem(row, 0, QTableWidgetItem(alert.symbol)); self.active_table.setItem(row, 1, QTableWidgetItem(f"{alert.price:.2f}"))
            self.active_table.setItem(row, 2, QTableWidgetItem("--")); self.active_table.setItem(row, 3, QTableWidgetItem(alert.condition.value))
            self.active_table.setItem(row, 4, QTableWidgetItem(alert.note)); expires_item = QTableWidgetItem(alert.expiry_time.strftime("%d-%b %H:%M"))
            expires_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter); self.active_table.setItem(row, 5, expires_item)
            delete_btn = QPushButton("🗑️"); delete_btn.setObjectName("deleteButton"); delete_btn.setFixedSize(24, 24)
            delete_btn.clicked.connect(lambda ch, a=alert: self._delete_alert(a)); self.active_table.setCellWidget(row, 6, delete_btn)
        self.active_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
    def _populate_triggered_table(self, alerts: List[Alert]):
        self.triggered_table.setRowCount(0)
        for alert in sorted(alerts, key=lambda a: a.triggered_time, reverse=True):
            row = self.triggered_table.rowCount(); self.triggered_table.insertRow(row); chk = QCheckBox()
            chk.stateChanged.connect(lambda state, a=alert: self._on_alert_acknowledged(state, a)); self.triggered_table.setCellWidget(row, 0, chk)
            self.triggered_table.setItem(row, 1, QTableWidgetItem(alert.triggered_time.strftime("%H:%M:%S"))); self.triggered_table.setItem(row, 2, QTableWidgetItem(alert.symbol))
            self.triggered_table.setItem(row, 3, QTableWidgetItem(f"{alert.price:.2f}")); self.triggered_table.setItem(row, 4, QTableWidgetItem(f"{alert.triggered_price:.2f}"))
            self.triggered_table.setItem(row, 5, QTableWidgetItem(alert.note))
        self.triggered_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
    def _populate_history_table(self, alerts: List[Alert]):
        self.history_table.setSortingEnabled(False); self.history_table.setRowCount(0)
        for alert in sorted(alerts, key=lambda a: a.triggered_time, reverse=True):
            row = self.history_table.rowCount(); self.history_table.insertRow(row); chk = QCheckBox(); chk.setChecked(True)
            chk.stateChanged.connect(lambda state, a=alert: self._on_alert_acknowledged(state, a)); self.history_table.setCellWidget(row, 0, chk)
            self.history_table.setItem(row, 1, QTableWidgetItem(alert.triggered_time.strftime("%Y-%m-%d %H:%M"))); self.history_table.setItem(row, 2, QTableWidgetItem(alert.symbol))
            self.history_table.setItem(row, 3, QTableWidgetItem(f"{alert.price:.2f}")); self.history_table.setItem(row, 4, QTableWidgetItem(f"{alert.triggered_price:.2f}"))
            self.history_table.setItem(row, 5, QTableWidgetItem(alert.note))
        self.history_table.setSortingEnabled(True); self.history_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
    @Slot(int, object)
    def _on_alert_acknowledged(self, state, alert: Alert): alert.acknowledged = (state == Qt.Checked); self._save_and_refresh_all()
    def _delete_alert(self, alert_to_delete: Alert):
        if alert_to_delete in self.all_alerts: self.all_alerts.remove(alert_to_delete); self._save_and_refresh_all()
    def _update_status(self, active_count, triggered_count):
        self.active_count_label.setText(f"Active: {active_count}"); self.triggered_count_label.setText(f"Triggered: {triggered_count}")
        self.tab_widget.setTabText(0, f"Active Alerts ({active_count})"); self.tab_widget.setTabText(1, f"Triggered Alerts ({triggered_count})")
    @Slot(object, float)
    def _on_alert_triggered(self, alert: Alert, trigger_price: float):
        self.alert_sound_requested.emit(); logger.info(f"Alert Triggered for {alert.symbol} at {trigger_price:.2f}")
        self._save_and_refresh_all(); self.tab_widget.setCurrentIndex(1)
    @Slot(object)
    def _on_alert_expired(self, alert: Alert):
        if alert in self.all_alerts: self.all_alerts.remove(alert); logger.info(f"Alert for {alert.symbol} expired and removed."); self._save_and_refresh_all()
    def update_market_data(self, symbol: str, price: float):
        self.alert_engine.update_market_data([{'symbol': symbol, 'price': price}])
        if self.tab_widget.currentIndex() == 0:
            for row in range(self.active_table.rowCount()):
                if self.active_table.item(row, 0).text() == symbol: self.active_table.item(row, 2).setText(f"{price:.2f}"); break
    def _save_and_refresh_all(self): self._save_alerts(); self._refresh_all_tables()
    def _load_alerts(self):
        try:
            if os.path.exists("user_data/all_alerts.json"):
                with open("user_data/all_alerts.json", 'r') as f: self.all_alerts = [Alert.from_dict(d) for d in json.load(f)]
            logger.info(f"Loaded {len(self.all_alerts)} alerts from file.")
        except Exception as e: logger.error(f"Error loading alerts: {e}")
        self._refresh_all_tables()
    def _save_alerts(self):
        try:
            os.makedirs("user_data", exist_ok=True)
            with open("user_data/all_alerts.json", 'w') as f: json.dump([a.to_dict() for a in self.all_alerts], f, indent=2)
        except Exception as e: logger.error(f"Error saving alerts: {e}")
    def closeEvent(self, event): self.alert_engine.stop(); self.alert_engine.wait(2000); super().closeEvent(event)
    def _apply_styles(self): self.setStyleSheet("QWidget#mainContainer { background-color: #121212; border: 1px solid #333; } QLabel#dialogTitle { color: #e0e0e0; font-size: 16px; font-weight: 600; padding: 5px; } QPushButton#closeButton { background-color: transparent; border: none; color: #aaa; font-size: 18px; } QPushButton#closeButton:hover { color: #d63031; } QLabel#statusLabel { color: #a0c0ff; font-size: 11px; padding: 4px; } QTabWidget#alertTabs::pane { border: none; } QTabBar::tab { background: #121212; color: #aaa; padding: 8px 15px; font-weight: 500; font-size: 12px; border: 1px solid #121212; border-bottom-color: #333; } QTabBar::tab:selected { background: #1e1e1e; color: #e0e0e0; border-color: #333; border-bottom-color: #1e1e1e; } QTableWidget#alertTable { background-color: #1e1e1e; border: 1px solid #333; gridline-color: #282828; color: #ccc; selection-background-color: #3a3d4d; } QHeaderView::section { background-color: #282828; color: #a0c0ff; padding: 5px; border: none; font-size: 11px; font-weight: 600; } QPushButton#deleteButton { font-family: 'Segoe UI Symbol'; background-color: transparent; color: #ff6b6b; border: none; font-size: 14px; } QPushButton#deleteButton:hover { color: #ff4757; } QCheckBox { margin-left: 6px; }")
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton: self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft(); event.accept()
    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.LeftButton and self._drag_pos: self.move(event.globalPosition().toPoint() - self._drag_pos); event.accept()
    def mouseReleaseEvent(self, event: QMouseEvent): self._drag_pos = None; event.accept()

# --- Alert System Controller ---
class AlertSystemManager(QObject):
    """Acts as the main controller to integrate the AdvancedAlertManager."""
    alert_sound_requested = Signal()
    def __init__(self, main_window):
        super().__init__(); self.main_window = main_window; self.instrument_map = {}; self.alert_manager_dialog = None
    def initialize_manager(self):
        if self.alert_manager_dialog is None:
            try:
                self.instrument_map = getattr(self.main_window, 'instrument_map', {})
                self.alert_manager_dialog = AdvancedAlertManager(parent=self.main_window, instrument_map=self.instrument_map, positions=self._get_current_positions())
                self.alert_manager_dialog.symbol_selected.connect(self._switch_chart_symbol)
                self.alert_manager_dialog.alert_sound_requested.connect(self.alert_sound_requested.emit)
                logger.info("AlertSystemManager initialized successfully")
            except Exception as e: logger.error(f"Error initializing AdvancedAlertManager: {e}", exc_info=True); self.alert_manager_dialog = None
    def show_alert_manager(self, history_tab=False):
        self.initialize_manager()
        if self.alert_manager_dialog:
            if history_tab: self.alert_manager_dialog.tab_widget.setCurrentIndex(2) # History is now the 3rd tab
            self.alert_manager_dialog.show(); self.alert_manager_dialog.raise_(); self.alert_manager_dialog.activateWindow()
    def show_quick_alert_dialog(self):
        self.initialize_manager();
        if not self.alert_manager_dialog: return
        current_symbol = getattr(self.main_window.candlestick_chart, 'current_symbol', '')
        if not current_symbol: QMessageBox.information(self.main_window, "No Symbol", "Please select a symbol on the chart first."); return
        ltp = self.main_window._get_fresh_ltp(current_symbol)
        # Directly open the creation dialog via the manager
        self._show_creation_dialog(symbol=current_symbol, price=ltp, current_ltp=ltp)

    @Slot(str)
    def create_alert_from_chart(self, alert_json: str):
        """Receives a JSON string from the chart and opens the creation dialog."""
        self.initialize_manager()
        if not self.alert_manager_dialog: return
        try:
            data = json.loads(alert_json)
            self._show_creation_dialog(
                symbol=data.get('symbol'),
                price=data.get('price'),
                intent=data.get('intent'),
                note=data.get('note'),
                current_ltp=data.get('current_ltp')
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to parse alert data from chart: {e}")

    def _show_creation_dialog(self, symbol, price, intent="", note="", current_ltp=0.0):
        """Helper to create and show the alert creation dialog."""
        if not symbol or not price: return
        dialog = AlertCreationDialog(
            parent=self.alert_manager_dialog,
            symbol=symbol, price=price, intent=intent, note=note, current_ltp=current_ltp
        )
        dialog.alert_created.connect(self.alert_manager_dialog._add_alert)
        dialog.exec()

    def update_market_data(self, ticks: List[Dict]):
        if not self.alert_manager_dialog or not self.instrument_map: return
        for tick in ticks:
            token, ltp = tick.get('instrument_token'), tick.get('last_price')
            symbol = next((s for s, d in self.instrument_map.items() if d.get('instrument_token') == token), None)
            if symbol and ltp is not None: self.alert_manager_dialog.update_market_data(symbol, ltp)
    def update_positions(self, positions: Dict):
        if self.alert_manager_dialog: self.alert_manager_dialog.update_positions(positions)
    def set_instrument_map(self, instrument_map: Dict):
        self.instrument_map = instrument_map
        if self.alert_manager_dialog: self.alert_manager_dialog.instrument_map = instrument_map
    def get_notification_counts(self) -> tuple[int, int]:
        if not self.alert_manager_dialog: return (0, 0)
        active = len([a for a in self.alert_manager_dialog.all_alerts if not a.triggered])
        triggered = len([a for a in self.alert_manager_dialog.all_alerts if a.triggered and not a.acknowledged])
        return active, triggered
    def get_active_alert_tokens(self) -> List[int]:
        if not self.alert_manager_dialog: return []
        tokens = []
        for alert in self.alert_manager_dialog.all_alerts:
            if not alert.triggered and alert.symbol in self.instrument_map:
                tokens.append(self.instrument_map[alert.symbol]['instrument_token'])
        return list(set(tokens))
    def stop_engine(self):
        if self.alert_manager_dialog: self.alert_manager_dialog.close()
    def _get_current_positions(self) -> Dict:
        if hasattr(self.main_window, 'position_manager'):
            pos_mgr = self.main_window.position_manager
            if hasattr(pos_mgr, 'get_all_positions'):
                return {p.tradingsymbol: {'quantity': p.quantity} for p in pos_mgr.get_all_positions()}
        return {}
    def _switch_chart_symbol(self, symbol: str):
        if hasattr(self.main_window, 'candlestick_chart'):
            self.main_window.candlestick_chart.on_search(symbol)