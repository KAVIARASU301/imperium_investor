"""
Advanced Alert Management System for Trading Terminal - IMPROVED VERSION
=======================================================================

This system provides a comprehensive alert management interface with:
- A three-tab workflow: Active -> Triggered -> History.
- An acknowledgement system for handling triggered alerts.
- A streamlined UI with compact, sharp-edged styling.
- A self-contained AlertCreationDialog.
- FAILPROOF background alert monitoring that starts immediately.
- Automatic startup initialization and persistent saving.
- Enhanced error handling and recovery mechanisms.
- Real-time market data integration.
"""
import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QLabel, QPushButton, QWidget, QLineEdit, QComboBox,
    QMessageBox, QCheckBox, QAbstractItemView, QFormLayout
)
from PySide6.QtGui import QMouseEvent
from PySide6.QtCore import Qt, Signal, QThread, QMutex, QMutexLocker, Slot, QObject, QTimer
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import json
import os
import time
from dataclasses import dataclass, asdict
from enum import Enum
from utils.sounds import play_alert

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

        container = QWidget(self)
        container.setObjectName("alertDialogContainer")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 15, 20, 20)
        layout.setSpacing(15)

        header_layout = QHBoxLayout()
        title = QLabel("Create Price Alert")
        title.setObjectName("dialogTitle")
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.clicked.connect(self.reject)
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(close_btn)
        layout.addLayout(header_layout)

        form_layout = QFormLayout()
        form_layout.setSpacing(12)
        self.symbol_input = QLineEdit(self.symbol)
        self.symbol_input.setObjectName("alertInput")
        self.price_input = QLineEdit(f"{self.price:.2f}")
        self.price_input.setObjectName("alertInput")
        self.condition_combo = QComboBox()
        self.condition_combo.setObjectName("alertCombo")
        self.condition_combo.addItems([c.value for c in AlertCondition])
        self.intent_combo = QComboBox()
        self.intent_combo.setObjectName("alertCombo")
        self.intent_combo.addItems([i.value for i in AlertIntent])
        self.validity_combo = QComboBox()
        self.validity_combo.setObjectName("alertCombo")
        self.validity_combo.addItems(["1 Day", "3 Days", "1 Week", "2 Weeks", "1 Month"])
        self.validity_combo.setCurrentText("1 Week")
        self.note_input = QLineEdit(self.note_str)
        self.note_input.setObjectName("alertInput")

        form_layout.addRow("Symbol:", self.symbol_input)
        form_layout.addRow("Alert Price:", self.price_input)
        form_layout.addRow("Condition:", self.condition_combo)
        form_layout.addRow("Intent:", self.intent_combo)
        form_layout.addRow("Validity:", self.validity_combo)
        form_layout.addRow("Note:", self.note_input)
        layout.addLayout(form_layout)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cancelButton")
        cancel_btn.clicked.connect(self.reject)
        create_btn = QPushButton("Create Alert")
        create_btn.setObjectName("createButton")
        create_btn.clicked.connect(self._create_alert)
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(create_btn)
        layout.addLayout(button_layout)

    def _prefill_intelligent_defaults(self):
        if self.price > self.current_ltp:
            self.condition_combo.setCurrentText(AlertCondition.PRICE_IS_ABOVE.value)
        else:
            self.condition_combo.setCurrentText(AlertCondition.PRICE_IS_BELOW.value)

        intent_map = {
            'buy_entry': AlertIntent.BUY_ENTRY,
            'sell_entry': AlertIntent.SELL_ENTRY,
            'profit_target': AlertIntent.PROFIT_TARGET,
            'stop_loss': AlertIntent.STOP_LOSS,
            'resistance': AlertIntent.BREAKOUT_WATCH,
            'support': AlertIntent.SUPPORT_WATCH
        }
        if self.intent_str in intent_map:
            self.intent_combo.setCurrentText(intent_map[self.intent_str].value)

    def _create_alert(self):
        try:
            symbol = self.symbol_input.text().strip().upper()
            price = float(self.price_input.text().strip())
            validity_days = {
                "1 Day": 1, "3 Days": 3, "1 Week": 7,
                "2 Weeks": 14, "1 Month": 30
            }.get(self.validity_combo.currentText(), 7)

            alert = Alert(
                id=f"{symbol}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
                symbol=symbol,
                price=price,
                condition=AlertCondition(self.condition_combo.currentText()),
                intent=AlertIntent(self.intent_combo.currentText()),
                note=self.note_input.text().strip(),
                validity_days=validity_days,
                created_time=datetime.now(),
                expiry_time=datetime.now() + timedelta(days=validity_days)
            )
            self.alert_created.emit(alert)
            self.accept()
        except (ValueError, KeyError) as e:
            QMessageBox.warning(self, "Invalid Input", f"Please check your inputs: {e}")

    def _apply_styles(self):
        """Apply enhanced dark theme styling for creation dialog."""
        self.setStyleSheet("""
            /* Dialog Container */
            QWidget#alertDialogContainer {
                background-color: #000000;
                border: 2px solid #1a1a1a;
                border-radius: 12px;
            }
            
            /* Dialog Title */
            QLabel#dialogTitle {
                color: #ffffff;
                font-size: 18px;
                font-weight: 700;
                padding: 4px;
                background-color: transparent;
            }
            
            /* Close Button */
            QPushButton#closeButton {
                background-color: transparent;
                border: none;
                color: #666;
                font-size: 18px;
                font-weight: bold;
                padding: 4px 8px;
                border-radius: 4px;
            }
            QPushButton#closeButton:hover {
                color: #ff4757;
                background-color: #1a1a1a;
            }
            
            /* Input Fields */
            QLineEdit#alertInput {
                background-color: #0a0a0a;
                border: 2px solid #1a1a1a;
                border-radius: 6px;
                padding: 10px 12px;
                color: #ffffff;
                font-size: 13px;
                font-weight: 500;
                selection-background-color: #4a9eff;
            }
            QLineEdit#alertInput:focus {
                border-color: #4a9eff;
                background-color: #0f0f0f;
            }
            QLineEdit#alertInput:hover {
                border-color: #2a2a2a;
            }
            
            /* Combo Boxes */
            QComboBox#alertCombo {
                background-color: #0a0a0a;
                border: 2px solid #1a1a1a;
                border-radius: 6px;
                padding: 10px 12px;
                color: #ffffff;
                font-size: 13px;
                font-weight: 500;
                min-width: 120px;
            }
            QComboBox#alertCombo:focus {
                border-color: #4a9eff;
                background-color: #0f0f0f;
            }
            QComboBox#alertCombo:hover {
                border-color: #2a2a2a;
            }
            QComboBox#alertCombo::drop-down {
                border: none;
                width: 30px;
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
            }
            QComboBox#alertCombo::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 6px solid #666;
                width: 0px;
                height: 0px;
            }
            QComboBox#alertCombo QAbstractItemView {
                background-color: #0a0a0a;
                border: 2px solid #1a1a1a;
                border-radius: 6px;
                color: #ffffff;
                selection-background-color: #4a9eff;
                selection-color: #ffffff;
                padding: 4px;
            }
            QComboBox#alertCombo QAbstractItemView::item {
                padding: 8px 12px;
                border-radius: 4px;
                margin: 1px;
            }
            QComboBox#alertCombo QAbstractItemView::item:hover {
                background-color: #1a1a1a;
            }
            
            /* Form Labels */
            QFormLayout QLabel {
                color: #cccccc;
                font-size: 13px;
                font-weight: 600;
                padding: 4px 0px;
            }
            
            /* Create Button */
            QPushButton#createButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #4CAF50, stop:1 #45a049);
                color: white;
                border: none;
                border-radius: 8px;
                padding: 12px 24px;
                font-weight: 700;
                font-size: 14px;
                letter-spacing: 0.5px;
            }
            QPushButton#createButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #5CBF60, stop:1 #4CAF50);
            }
            QPushButton#createButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #45a049, stop:1 #3d8b40);
            }
            
            /* Cancel Button */
            QPushButton#cancelButton {
                background-color: #2a2a2a;
                color: #cccccc;
                border: 2px solid #1a1a1a;
                border-radius: 8px;
                padding: 12px 24px;
                font-weight: 600;
                font-size: 14px;
            }
            QPushButton#cancelButton:hover {
                background-color: #3a3a3a;
                border-color: #2a2a2a;
                color: #ffffff;
            }
            QPushButton#cancelButton:pressed {
                background-color: #1a1a1a;
            }
        """)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None
        event.accept()


# --- IMPROVED Alert Engine with Failproof Design ---
class AlertEngine(QThread):
    """Background thread for reliable alert processing with failproof mechanisms."""
    alert_triggered = Signal(object, float)
    alert_expired = Signal(object)
    engine_error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._alerts: List[Alert] = []
        self._market_data: Dict[str, float] = {}
        self._check_interval_ms = 1000  # 1 second for responsiveness
        self._running = False
        self._mutex = QMutex()
        self._last_heartbeat = time.time()
        self._error_count = 0
        self._max_errors = 5

        # Watchdog timer for health monitoring
        self._watchdog_timer = QTimer()
        self._watchdog_timer.timeout.connect(self._check_engine_health)
        self._watchdog_timer.start(10000)  # Check every 10 seconds

    def update_alerts(self, alerts: List[Alert]):
        """Thread-safe alert list update."""
        try:
            with QMutexLocker(self._mutex):
                # Only keep active, non-expired alerts
                now = datetime.now()
                self._alerts = [a for a in alerts if not a.triggered and a.expiry_time > now]
                logger.debug(f"AlertEngine updated with {len(self._alerts)} active alerts")
        except Exception as e:
            logger.error(f"Error updating alerts in engine: {e}")
            self.engine_error.emit(f"Failed to update alerts: {e}")

    def update_market_data(self, market_data_ticks: List[Dict]):
        """Thread-safe market data update."""
        try:
            with QMutexLocker(self._mutex):
                for tick in market_data_ticks:
                    if 'symbol' in tick and 'price' in tick:
                        self._market_data[tick['symbol']] = tick['price']
                logger.debug(f"AlertEngine updated market data for {len(market_data_ticks)} symbols")
        except Exception as e:
            logger.error(f"Error updating market data in engine: {e}")

    def run(self):
        """Main engine loop with error recovery."""
        self._running = True
        self._error_count = 0
        logger.info("AlertEngine thread started successfully")

        while self._running:
            try:
                self._check_alerts()
                self._last_heartbeat = time.time()
                self._error_count = 0  # Reset error count on successful iteration
                self.msleep(self._check_interval_ms)
            except Exception as e:
                self._error_count += 1
                logger.error(f"Error in AlertEngine main loop (#{self._error_count}): {e}")

                if self._error_count >= self._max_errors:
                    logger.critical("AlertEngine exceeded maximum error count, stopping")
                    self.engine_error.emit(f"Engine failure after {self._max_errors} errors")
                    break

                # Brief pause before retry
                self.msleep(5000)

        logger.info("AlertEngine thread stopped")

    def stop(self):
        """Gracefully stop the engine."""
        logger.info("Stopping AlertEngine...")
        self._running = False
        self._watchdog_timer.stop()

    def _check_engine_health(self):
        """Watchdog function to monitor engine health."""
        if self._running:
            time_since_heartbeat = time.time() - self._last_heartbeat
            if time_since_heartbeat > 30:  # No heartbeat for 30 seconds
                logger.warning(f"AlertEngine appears frozen (no heartbeat for {time_since_heartbeat:.1f}s)")
                self.engine_error.emit("Engine appears to be frozen")

    def _check_alerts(self):
        """Core alert checking logic with enhanced error handling."""
        with QMutexLocker(self._mutex):
            if not self._alerts:
                return

            now = datetime.now()
            triggered_alerts, expired_alerts = [], []

            for alert in self._alerts[:]:  # Copy list to avoid modification during iteration
                try:
                    # Check expiry
                    if alert.expiry_time <= now:
                        expired_alerts.append(alert)
                        continue

                    # Check price condition
                    current_price = self._market_data.get(alert.symbol)
                    if current_price is None:
                        continue

                    condition_met = False
                    if alert.condition == AlertCondition.PRICE_IS_ABOVE and current_price >= alert.price:
                        condition_met = True
                    elif alert.condition == AlertCondition.PRICE_IS_BELOW and current_price <= alert.price:
                        condition_met = True

                    if condition_met:
                        alert.triggered = True
                        alert.triggered_time = now
                        alert.triggered_price = current_price
                        triggered_alerts.append(alert)
                        logger.info(f"Alert triggered: {alert.symbol} at {current_price:.2f}")

                except Exception as e:
                    logger.error(f"Error checking alert {alert.id}: {e}")
                    continue

            # Remove processed alerts from an active list
            for alert in expired_alerts + triggered_alerts:
                if alert in self._alerts:
                    self._alerts.remove(alert)

            # Emit signals outside mutex lock

        # Emit signals for expired alerts
        for alert in expired_alerts:
            self.alert_expired.emit(alert)

        # Emit signals for triggered alerts
        for alert in triggered_alerts:
            self.alert_triggered.emit(alert, alert.triggered_price)


# --- Main Alert Management Dialog ---
class AdvancedAlertManager(QDialog):
    """Main alert management window with enhanced UI and functionality."""
    symbol_selected = Signal(str)

    def __init__(self, alert_system_manager, parent=None):
        super().__init__(parent)
        self.manager = alert_system_manager
        self.instrument_map = self.manager.instrument_map
        self.all_alerts = self.manager.all_alerts
        self._drag_pos = None

        self._setup_ui()
        self._apply_styles()
        self._refresh_all_tables()

    def _setup_ui(self):
        self.setWindowTitle("Alert Manager")
        self.setMinimumSize(900, 700)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        container = QWidget(self)
        container.setObjectName("mainContainer")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(15, 10, 15, 15)
        layout.setSpacing(10)

        layout.addLayout(self._create_header())

        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("alertTabs")

        self.active_table = self._create_table(["Symbol", "Alert Price", "Current LTP", "Condition", "Note", "Expires", ""])
        self.triggered_table = self._create_table(["", "Trigger Time", "Symbol", "Alert Price", "Trigger Price", "Note"])
        self.history_table = self._create_table(["", "Trigger Time", "Symbol", "Alert Price", "Trigger Price", "Note"], sorting=True)

        self.tab_widget.addTab(self.active_table, "Active Alerts")
        self.tab_widget.addTab(self.triggered_table, "Triggered Alerts")
        self.tab_widget.addTab(self.history_table, "Alert History")

        layout.addWidget(self.tab_widget)
        layout.addLayout(self._create_status_bar())

    def _create_header(self) -> QHBoxLayout:
        header_layout = QHBoxLayout()
        title = QLabel("Alert Manager")
        title.setObjectName("dialogTitle")
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.clicked.connect(self.close)
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignTop)
        return header_layout

    def _create_table(self, headers: List[str], sorting=False) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setObjectName("alertTable")
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)  # Make unselectable
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(False)  # Disable alternating row colors
        table.setShowGrid(True)  # Enable grid lines
        table.cellDoubleClicked.connect(self._on_symbol_double_clicked)
        table.setSortingEnabled(sorting)

        # Set a consistent compact row height
        table.verticalHeader().setDefaultSectionSize(24)  # Compact height
        table.verticalHeader().setVisible(False)  # Hide row numbers

        # Set header height to match
        table.horizontalHeader().setFixedHeight(24)  # Same as row height

        # Set specific column widths
        if len(headers) > 0:
            # Configure column widths based on a table type
            if "Delete" in headers or "" in headers:
                # For tables with delete button - make delete column very narrow
                delete_col_index = len(headers) - 1
                table.horizontalHeader().setSectionResizeMode(delete_col_index, QHeaderView.ResizeMode.Fixed)
                table.setColumnWidth(delete_col_index, 30)  # Very narrow delete column

                # Make note column stretch to use available space
                note_col_index = -1
                for i, header in enumerate(headers):
                    if "Note" in header:
                        note_col_index = i
                        break
                if note_col_index != -1:
                    table.horizontalHeader().setSectionResizeMode(note_col_index, QHeaderView.ResizeMode.Stretch)

        return table

    def _create_status_bar(self) -> QHBoxLayout:
        status_layout = QHBoxLayout()
        self.active_count_label = QLabel("Active: 0")
        self.triggered_count_label = QLabel("Triggered: 0")
        self.active_count_label.setObjectName("statusLabel")
        self.triggered_count_label.setObjectName("statusLabel")
        status_layout.addWidget(self.active_count_label)
        status_layout.addWidget(self.triggered_count_label)
        status_layout.addStretch()
        return status_layout

    @Slot(int, int)
    def _on_symbol_double_clicked(self, row: int, column: int):
        """Handle double click on table cells to select symbol."""
        # Note: column parameter not used as we search for Symbol column by header text
        table = self.sender()
        if not isinstance(table, QTableWidget):
            return

        symbol_col_index = -1
        for i in range(table.columnCount()):
            header_item = table.horizontalHeaderItem(i)
            if header_item and header_item.text() == "Symbol":
                symbol_col_index = i
                break

        if symbol_col_index != -1:
            symbol_item = table.item(row, symbol_col_index)
            if symbol_item:
                self.symbol_selected.emit(symbol_item.text())

    def _refresh_all_tables(self):
        """Refresh all tables with current alert data."""
        active = [a for a in self.all_alerts if not a.triggered]
        triggered = [a for a in self.all_alerts if a.triggered and not a.acknowledged]
        history = [a for a in self.all_alerts if a.triggered and a.acknowledged]

        self._populate_active_table(active)
        self._populate_triggered_table(triggered)
        self._populate_history_table(history)
        self._update_status(len(active), len(triggered))

    def _request_delete_alert(self, alert_to_delete: Alert):
        """Request confirmation before deleting an alert."""
        reply = QMessageBox.question(
            self, 'Confirm Deletion',
            f"Are you sure you want to delete the alert for {alert_to_delete.symbol}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.manager._delete_alert(alert_to_delete)

    def _populate_active_table(self, alerts: List[Alert]):
        """Populate the active alerts table."""
        self.active_table.setRowCount(0)
        for alert in sorted(alerts, key=lambda a: a.created_time, reverse=True):
            row = self.active_table.rowCount()
            self.active_table.insertRow(row)

            self.active_table.setItem(row, 0, QTableWidgetItem(alert.symbol))
            self.active_table.setItem(row, 1, QTableWidgetItem(f"{alert.price:.2f}"))
            self.active_table.setItem(row, 2, QTableWidgetItem("--"))
            self.active_table.setItem(row, 3, QTableWidgetItem(alert.condition.value))
            self.active_table.setItem(row, 4, QTableWidgetItem(alert.note))

            expires_item = QTableWidgetItem(alert.expiry_time.strftime("%d-%b %H:%M"))
            expires_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.active_table.setItem(row, 5, expires_item)

            # Create compact delete button with normal delete icon
            delete_btn = QPushButton("×")  # Normal delete symbol
            delete_btn.setObjectName("deleteButton")
            delete_btn.setFixedSize(20, 20)  # Small fixed size
            delete_btn.clicked.connect(lambda ch, a=alert: self._request_delete_alert(a))

            # Center the button in the cell
            widget = QWidget()
            layout = QHBoxLayout(widget)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(delete_btn)
            layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.active_table.setCellWidget(row, 6, widget)

        # Set column resize modes - Note column stretches, delete column fixed
        self.active_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)  # Note column
        self.active_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)  # Delete column
        self.active_table.setColumnWidth(6, 30)  # Very narrow delete column

    def _populate_triggered_table(self, alerts: List[Alert]):
        """Populate the triggered alerts table."""
        self.triggered_table.setRowCount(0)
        for alert in sorted(alerts, key=lambda a: a.triggered_time, reverse=True):
            row = self.triggered_table.rowCount()
            self.triggered_table.insertRow(row)

            # Create centered checkbox
            chk = QCheckBox()
            chk.stateChanged.connect(lambda state, a=alert: self.manager._on_alert_acknowledged(state, a))
            widget = QWidget()
            layout = QHBoxLayout(widget)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(chk)
            layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.triggered_table.setCellWidget(row, 0, widget)

            self.triggered_table.setItem(row, 1, QTableWidgetItem(alert.triggered_time.strftime("%H:%M:%S")))
            self.triggered_table.setItem(row, 2, QTableWidgetItem(alert.symbol))
            self.triggered_table.setItem(row, 3, QTableWidgetItem(f"{alert.price:.2f}"))
            self.triggered_table.setItem(row, 4, QTableWidgetItem(f"{alert.triggered_price:.2f}"))
            self.triggered_table.setItem(row, 5, QTableWidgetItem(alert.note))

        # Set column resize modes
        self.triggered_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)  # Checkbox column
        self.triggered_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)  # Note column
        self.triggered_table.setColumnWidth(0, 30)  # Narrow checkbox column

    def _populate_history_table(self, alerts: List[Alert]):
        """Populate the alert history table."""
        self.history_table.setSortingEnabled(False)
        self.history_table.setRowCount(0)

        for alert in sorted(alerts, key=lambda a: a.triggered_time, reverse=True):
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)

            # Create centered checkbox
            chk = QCheckBox()
            chk.setChecked(True)
            chk.stateChanged.connect(lambda state, a=alert: self.manager._on_alert_acknowledged(state, a))
            widget = QWidget()
            layout = QHBoxLayout(widget)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(chk)
            layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.history_table.setCellWidget(row, 0, widget)

            self.history_table.setItem(row, 1, QTableWidgetItem(alert.triggered_time.strftime("%Y-%m-%d %H:%M")))
            self.history_table.setItem(row, 2, QTableWidgetItem(alert.symbol))
            self.history_table.setItem(row, 3, QTableWidgetItem(f"{alert.price:.2f}"))
            self.history_table.setItem(row, 4, QTableWidgetItem(f"{alert.triggered_price:.2f}"))
            self.history_table.setItem(row, 5, QTableWidgetItem(alert.note))

        self.history_table.setSortingEnabled(True)
        # Set column resize modes
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)  # Checkbox column
        self.history_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)  # Note column
        self.history_table.setColumnWidth(0, 30)  # Narrow checkbox column

    def _update_status(self, active_count, triggered_count):
        """Update status bar with current counts."""
        self.active_count_label.setText(f"Active: {active_count}")
        self.triggered_count_label.setText(f"Triggered: {triggered_count}")
        self.tab_widget.setTabText(0, f"Active Alerts ({active_count})")
        self.tab_widget.setTabText(1, f"Triggered Alerts ({triggered_count})")

    def _apply_styles(self):
        """Apply enhanced dark theme styling with professional appearance."""
        self.setStyleSheet("""
            /* Main Container */
            QWidget#mainContainer {
                background-color: #0a0a0a;
                border: 1px solid #1a1a1a;
                border-radius: 8px;
            }
            
            /* Dialog Title */
            QLabel#dialogTitle {
                color: #ffffff;
                font-size: 18px;
                font-weight: 700;
                padding: 8px;
                background-color: transparent;
            }
            
            /* Close Button */
            QPushButton#closeButton {
                background-color: transparent;
                border: none;
                color: #666;
                font-size: 20px;
                font-weight: bold;
                padding: 4px 8px;
                border-radius: 4px;
            }
            QPushButton#closeButton:hover {
                color: #ff4757;
                background-color: #1a1a1a;
            }
            
            /* Status Labels */
            QLabel#statusLabel {
                color: #4a9eff;
                font-size: 12px;
                font-weight: 600;
                padding: 6px 12px;
                background-color: #0f0f0f;
                border: 1px solid #1a1a1a;
                border-radius: 4px;
                margin: 2px;
            }
            
            /* Tab Widget */
            QTabWidget#alertTabs {
                background-color: transparent;
                border: none;
            }
            QTabWidget#alertTabs::pane {
                border: 1px solid #1a1a1a;
                border-radius: 6px;
                background-color: #000000;
                margin-top: -1px;
            }
            QTabWidget#alertTabs::tab-bar {
                alignment: left;
            }
            
            /* Compact Tab Bar */
            QTabBar::tab {
                background: #0a0a0a;
                color: #888;
                padding: 4px 16px;  /* Compact padding */
                font-weight: 600;
                font-size: 12px;
                border: 1px solid #1a1a1a;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 2px;
                min-width: 100px;
                height: 24px;  /* Same height as table rows */
            }
            QTabBar::tab:selected {
                background: #000000;
                color: #ffffff;
                border-color: #1a1a1a;
                border-bottom: 1px solid #000000;
            }
            QTabBar::tab:hover:!selected {
                background: #111111;
                color: #ccc;
            }
            
            /* Table Widget - Dark Professional Theme */
            QTableWidget#alertTable {
                background-color: #000000;
                border: none;
                gridline-color: #1a1a1a;
                color: #e0e0e0;
                selection-background-color: transparent;  /* Disable selection background */
                selection-color: #e0e0e0;
                font-size: 11px;
                font-weight: 500;
                outline: none;
            }
            
            /* Compact Table Headers */
            QHeaderView::section {
                background-color: #151515;  /* Slight gray background */
                color: #4a9eff;
                padding: 4px 6px;  /* Compact padding */
                border: none;
                border-bottom: 2px solid #1a1a1a;
                border-right: 1px solid #1a1a1a;
                font-size: 11px;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                height: 24px;  /* Same height as rows and tabs */
            }
            QHeaderView::section:last {
                border-right: none;
            }
            QHeaderView::section:hover {
                background-color: #1a1a1a;  /* Slightly lighter on hover */
            }
            
            /* Table Items with subtle hover */
            QTableWidget#alertTable::item {
                padding: 4px 6px;  /* Compact padding */
                border-bottom: 1px solid #1a1a1a;
                border-right: 1px solid #0f0f0f;
                height: 24px;  /* Consistent height */
            }
            QTableWidget#alertTable::item:selected {
                background-color: transparent;  /* No selection */
                color: #e0e0e0;
            }
            
            /* Very subtle row hover effect */
            QTableWidget#alertTable::item:hover {
                background-color: rgba(74, 158, 255, 0.03);  /* Very light blue tint */
            }
            
            /* Remove alternating row colors */
            QTableWidget#alertTable::item:alternate {
                background-color: #000000;
            }
            
            /* Compact Delete Button */
            QPushButton#deleteButton {
                background-color: transparent;
                color: #ff6b6b;
                border: none;
                border-radius: 2px;
                font-size: 14px;
                font-weight: normal;
                padding: 0px;
                margin: 0px;
                width: 20px;
                height: 20px;
                text-align: center;
            }
            QPushButton#deleteButton:hover {
                background-color: rgba(255, 107, 107, 0.1);
                color: #ff4757;
            }
            QPushButton#deleteButton:pressed {
                background-color: rgba(255, 107, 107, 0.2);
            }
            
            /* Checkboxes */
            QCheckBox {
                margin-left: 8px;
                spacing: 4px;
            }
            
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 2px solid #333;
                border-radius: 3px;
                background-color: #000000;
            }
            
            QCheckBox::indicator:checked {
                background-color: #ff4444;
                border-color: #ff4444;
                background-image: url("data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTIiIGhlaWdodD0iMTIiIHZpZXdCb3g9IjAgMCAxMiAxMiIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTIgNkw1IDlMMTAgMyIgc3Ryb2tlPSIjZmY0NDQ0IiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCIvPgo8L3N2Zz4=");
                background-repeat: no-repeat;
                background-position: center;
            }
            
            QCheckBox::indicator:hover {
                border-color: #4a9eff;
            }

            
            /* Scrollbars */
            QScrollBar:vertical {
                background-color: #0a0a0a;
                width: 12px;
                border: none;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background-color: #333;
                border-radius: 6px;
                min-height: 20px;
                margin: 2px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #444;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar:horizontal {
                background-color: #0a0a0a;
                height: 12px;
                border: none;
                border-radius: 6px;
            }
            QScrollBar::handle:horizontal {
                background-color: #333;
                border-radius: 6px;
                min-width: 20px;
                margin: 2px;
            }
            QScrollBar::handle:horizontal:hover {
                background-color: #444;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
            }
        """)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None
        event.accept()


# --- ENHANCED Alert System Controller ---
class AlertSystemManager(QObject):
    """Enhanced alert system controller with failproof initialization and operation."""
    alert_sound_requested = Signal()
    alerts_changed = Signal()
    engine_status_changed = Signal(str)  # "running", "stopped", "error"

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.instrument_map: Dict[str, Dict] = {}
        self.alert_manager_dialog: Optional[AdvancedAlertManager] = None
        self.alert_engine: Optional[AlertEngine] = None
        self.all_alerts: List[Alert] = []

        # Auto-save timer to ensure data persistence
        self.auto_save_timer = QTimer()
        self.auto_save_timer.timeout.connect(self._save_alerts)
        self.auto_save_timer.start(30000)  # Auto-save every 30 seconds

        # Initialization flag
        self._initialized = False

        # Initialize immediately
        QTimer.singleShot(100, self._initialize_system)

    def _initialize_system(self):
        """Initialize the alert system immediately on startup."""
        try:
            logger.info("Initializing AlertSystemManager...")

            # Create user_data directory
            os.makedirs("user_data", exist_ok=True)

            # Load existing alerts
            self._load_alerts()

            # Get instrument map from the main window if available
            if hasattr(self.main_window, 'instrument_map'):
                self.instrument_map = self.main_window.instrument_map

            # Start the alert engine immediately
            self._start_alert_engine()

            # Fetch initial market data if possible
            self._fetch_initial_market_data()

            self._initialized = True
            logger.info("AlertSystemManager initialized successfully")

        except Exception as e:
            logger.error(f"Error initializing AlertSystemManager: {e}", exc_info=True)
            # Retry initialization after a delay
            QTimer.singleShot(5000, self._initialize_system)

    def _start_alert_engine(self):
        """Start the alert engine with error handling."""
        try:
            if self.alert_engine is not None:
                self.alert_engine.stop()
                self.alert_engine.wait(3000)

            self.alert_engine = AlertEngine(self)
            self.alert_engine.alert_triggered.connect(self._on_alert_triggered)
            self.alert_engine.alert_expired.connect(self._on_alert_expired)
            self.alert_engine.engine_error.connect(self._on_engine_error)

            # Update engine with current alerts
            self.alert_engine.update_alerts(self.all_alerts)

            # Start the engine
            self.alert_engine.start()

            self.engine_status_changed.emit("running")
            logger.info("AlertEngine started successfully")

        except Exception as e:
            logger.error(f"Error starting AlertEngine: {e}", exc_info=True)
            self.engine_status_changed.emit("error")
            # Retry after delay
            QTimer.singleShot(10000, self._start_alert_engine)

    def _fetch_initial_market_data(self):
        """Fetch initial market data for active alerts."""
        try:
            active_symbols = list(set(a.symbol for a in self.all_alerts if not a.triggered))
            if not active_symbols:
                return

            logger.info(f"Fetching initial LTPs for {len(active_symbols)} symbols...")

            if hasattr(self.main_window, 'data_fetcher'):
                quotes = self.main_window.data_fetcher.get_quotes(active_symbols)
                ticks = []
                for symbol, data in quotes.items():
                    if 'last_price' in data:
                        ticks.append({'symbol': symbol, 'price': data['last_price']})

                if ticks and self.alert_engine:
                    self.alert_engine.update_market_data(ticks)
                    logger.info(f"Initial market data updated for {len(ticks)} symbols")

        except Exception as e:
            logger.error(f"Error fetching initial market data: {e}")

    def _save_and_refresh_all(self):
        """Save alerts and refresh all UI components."""
        try:
            self._save_alerts()

            if self.alert_manager_dialog and self.alert_manager_dialog.isVisible():
                self.alert_manager_dialog._refresh_all_tables()

            if self.alert_engine:
                self.alert_engine.update_alerts(self.all_alerts)

            self.alerts_changed.emit()

        except Exception as e:
            logger.error(f"Error in save and refresh: {e}")

    def _add_alert(self, alert: Alert):
        """Add a new alert to the system."""
        try:
            self.all_alerts.append(alert)
            logger.info(f"Added alert: {alert.symbol} at {alert.price:.2f}")
            self._save_and_refresh_all()
        except Exception as e:
            logger.error(f"Error adding alert: {e}")

    def _delete_alert(self, alert_to_delete: Alert):
        """Delete an alert from the system."""
        try:
            if alert_to_delete in self.all_alerts:
                self.all_alerts.remove(alert_to_delete)
                logger.info(f"Deleted alert: {alert_to_delete.symbol}")
                self._save_and_refresh_all()
        except Exception as e:
            logger.error(f"Error deleting alert: {e}")

    @Slot(int, object)
    def _on_alert_acknowledged(self, state, alert: Alert):
        """Handle alert acknowledgment."""
        try:
            alert.acknowledged = (state == Qt.CheckState.Checked.value)
            logger.info(f"Alert acknowledged: {alert.symbol} - {alert.acknowledged}")
            self._save_and_refresh_all()
        except Exception as e:
            logger.error(f"Error acknowledging alert: {e}")

    @Slot(object, float)
    def _on_alert_triggered(self, alert: Alert, trigger_price: float):
        """Handle triggered alert."""
        try:
            play_alert()
            self.alert_sound_requested.emit()
            logger.info(f"Alert Triggered: {alert.symbol} at {trigger_price:.2f}")

            self._save_and_refresh_all()

            # Switch to triggered alerts tab if the dialog is open
            if self.alert_manager_dialog and self.alert_manager_dialog.isVisible():
                self.alert_manager_dialog.tab_widget.setCurrentIndex(1)

        except Exception as e:
            logger.error(f"Error handling triggered alert: {e}")

    @Slot(object)
    def _on_alert_expired(self, alert: Alert):
        """Handle expired alert."""
        try:
            if alert in self.all_alerts:
                self.all_alerts.remove(alert)
                logger.info(f"Alert expired and removed: {alert.symbol}")
                self._save_and_refresh_all()
        except Exception as e:
            logger.error(f"Error handling expired alert: {e}")

    @Slot(str)
    def _on_engine_error(self, error_message: str):
        """Handle alert engine errors."""
        logger.error(f"AlertEngine error: {error_message}")
        self.engine_status_changed.emit("error")

        # Attempt to restart the engine
        QTimer.singleShot(5000, self._start_alert_engine)

    def show_alert_manager(self, history_tab=False):
        """Show the alert manager dialog."""
        try:
            if not self._initialized:
                QMessageBox.information(
                    self.main_window,
                    "Alert System",
                    "Alert system is still initializing. Please try again in a moment."
                )
                return

            if self.alert_manager_dialog is None or not self.alert_manager_dialog.isVisible():
                self.alert_manager_dialog = AdvancedAlertManager(self, parent=self.main_window)
                self.alert_manager_dialog.symbol_selected.connect(self._switch_chart_symbol)

            if history_tab:
                self.alert_manager_dialog.tab_widget.setCurrentIndex(2)

            self.alert_manager_dialog.show()
            self.alert_manager_dialog.raise_()
            self.alert_manager_dialog.activateWindow()

        except Exception as e:
            logger.error(f"Error showing alert manager: {e}")

    def show_quick_alert_dialog(self):
        """Show quick alert creation dialog."""
        try:
            if not self._initialized:
                QMessageBox.information(
                    self.main_window,
                    "Alert System",
                    "Alert system is still initializing. Please try again in a moment."
                )
                return

            current_symbol = getattr(self.main_window.candlestick_chart, 'current_symbol', '')
            if not current_symbol:
                QMessageBox.information(
                    self.main_window,
                    "No Symbol",
                    "Please select a symbol on the chart first."
                )
                return

            ltp = self.main_window._get_fresh_ltp(current_symbol)
            self._show_creation_dialog(symbol=current_symbol, price=ltp, current_ltp=ltp)

        except Exception as e:
            logger.error(f"Error showing quick alert dialog: {e}")

    @Slot(str)
    def create_alert_from_chart(self, alert_json: str):
        """Create alert from chart context menu."""
        try:
            if not self._initialized:
                return

            data = json.loads(alert_json)
            self._show_creation_dialog(
                symbol=data.get('symbol', ''),
                price=data.get('price', 0.0),
                intent=data.get('intent', ''),
                note=data.get('note', ''),
                current_ltp=data.get('current_ltp', 0.0)
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to parse alert data from chart: {e}")

    def _show_creation_dialog(self, symbol, price, intent="", note="", current_ltp=0.0):
        """Show the alert creation dialog."""
        try:
            if not symbol or not price:
                return

            dialog = AlertCreationDialog(
                parent=self.main_window,
                symbol=symbol,
                price=price,
                intent=intent,
                note=note,
                current_ltp=current_ltp
            )
            dialog.alert_created.connect(self._add_alert)
            dialog.exec()

        except Exception as e:
            logger.error(f"Error showing creation dialog: {e}")

    def update_market_data(self, ticks: List[Dict]):
        """Update market data from the main window."""
        try:
            if not self._initialized or not self.alert_engine:
                return

            # Convert instrument tokens to symbols
            symbol_price_map = {}
            for tick in ticks:
                instrument_token = tick.get('instrument_token')
                last_price = tick.get('last_price')

                if instrument_token and last_price is not None:
                    symbol = next(
                        (s for s, d in self.instrument_map.items()
                         if d.get('instrument_token') == instrument_token),
                        None
                    )
                    if symbol:
                        symbol_price_map[symbol] = last_price

            # Update alert engine
            market_data_for_engine = [
                {'symbol': s, 'price': p}
                for s, p in symbol_price_map.items()
            ]

            if market_data_for_engine:
                self.alert_engine.update_market_data(market_data_for_engine)

            # Update UI if visible
            if (self.alert_manager_dialog and
                self.alert_manager_dialog.isVisible() and
                self.alert_manager_dialog.tab_widget.currentIndex() == 0):

                self._update_active_table_ltps(symbol_price_map)

        except Exception as e:
            logger.debug(f"Error updating market data: {e}")

    def _update_active_table_ltps(self, symbol_price_map: Dict[str, float]):
        """Update LTP column in active alerts table."""
        try:
            table = self.alert_manager_dialog.active_table
            for row in range(table.rowCount()):
                symbol_item = table.item(row, 0)
                if symbol_item and symbol_item.text() in symbol_price_map:
                    ltp = symbol_price_map[symbol_item.text()]
                    ltp_item = table.item(row, 2)
                    if ltp_item:
                        ltp_item.setText(f"{ltp:.2f}")
        except Exception as e:
            logger.debug(f"Error updating LTPs in table: {e}")

    def set_instrument_map(self, instrument_map: Dict):
        """Set the instrument mapping."""
        try:
            self.instrument_map = instrument_map
            logger.info(f"Instrument map updated with {len(instrument_map)} instruments")
        except Exception as e:
            logger.error(f"Error setting instrument map: {e}")

    def get_notification_counts(self) -> tuple[int, int]:
        """Get counts for badge notifications."""
        try:
            active = len([a for a in self.all_alerts if not a.triggered])
            triggered = len([a for a in self.all_alerts if a.triggered and not a.acknowledged])
            return active, triggered
        except Exception as e:
            logger.error(f"Error getting notification counts: {e}")
            return 0, 0

    def get_active_alert_tokens(self) -> List[int]:
        """Get instrument tokens for active alerts."""
        try:
            tokens = []
            for alert in self.all_alerts:
                if not alert.triggered and alert.symbol in self.instrument_map:
                    token = self.instrument_map[alert.symbol].get('instrument_token')
                    if token:
                        tokens.append(token)
            return list(set(tokens))
        except Exception as e:
            logger.error(f"Error getting active alert tokens: {e}")
            return []

    def stop_engine(self):
        """Stop the alert engine and save the final state."""
        try:
            logger.info("Stopping alert engine and saving alerts...")

            # Stop auto-save timer
            self.auto_save_timer.stop()

            # Save final state
            self._save_alerts()

            # Stop engine
            if self.alert_engine:
                self.alert_engine.stop()
                self.alert_engine.wait(5000)  # Wait up to 5 seconds

            self.engine_status_changed.emit("stopped")
            logger.info("Alert engine stopped successfully")

        except Exception as e:
            logger.error(f"Error stopping alert engine: {e}")

    def _get_current_positions(self) -> Dict:
        """Get current trading positions."""
        try:
            if hasattr(self.main_window, 'position_manager'):
                pos_mgr = self.main_window.position_manager
                if hasattr(pos_mgr, 'get_all_positions'):
                    return {
                        p.tradingsymbol: {'quantity': p.quantity}
                        for p in pos_mgr.get_all_positions()
                    }
        except Exception as e:
            logger.error(f"Error getting current positions: {e}")
        return {}

    def _switch_chart_symbol(self, symbol: str):
        """Switch to symbol on chart."""
        try:
            if hasattr(self.main_window, 'candlestick_chart'):
                self.main_window.candlestick_chart.on_search(symbol)
        except Exception as e:
            logger.error(f"Error switching chart symbol: {e}")

    def _load_alerts(self):
        """Load alerts from persistent storage."""
        try:
            alerts_file = "user_data/all_alerts.json"
            if os.path.exists(alerts_file):
                with open(alerts_file, 'r') as f:
                    alert_data = json.load(f)
                    self.all_alerts = [Alert.from_dict(d) for d in alert_data]
                logger.info(f"Loaded {len(self.all_alerts)} alerts from file")
            else:
                self.all_alerts = []
                logger.info("No existing alerts file found, starting fresh")
        except Exception as e:
            logger.error(f"Error loading alerts: {e}")
            self.all_alerts = []

    def _save_alerts(self):
        """Save alerts to persistent storage."""
        try:
            os.makedirs("user_data", exist_ok=True)
            alerts_file = "user_data/all_alerts.json"

            with open(alerts_file, 'w') as f:
                json.dump([a.to_dict() for a in self.all_alerts], f, indent=2)

            logger.debug(f"Successfully saved {len(self.all_alerts)} alerts")
        except Exception as e:
            logger.error(f"Error saving alerts: {e}")

    def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status for debugging."""
        try:
            return {
                'initialized': self._initialized,
                'engine_running': self.alert_engine.isRunning() if self.alert_engine else False,
                'total_alerts': len(self.all_alerts),
                'active_alerts': len([a for a in self.all_alerts if not a.triggered]),
                'triggered_alerts': len([a for a in self.all_alerts if a.triggered and not a.acknowledged]),
                'instrument_count': len(self.instrument_map),
                'dialog_open': self.alert_manager_dialog.isVisible() if self.alert_manager_dialog else False
            }
        except Exception as e:
            logger.error(f"Error getting system status: {e}")
            return {'error': str(e)}


# === INTEGRATION INSTRUCTIONS ===
"""
INTEGRATION STEPS FOR MAIN WINDOW:

1. Replace the existing alert system initialization in main_window.py:

   def _init_alert_system(self):
       try:
           self.alert_system = AlertSystemManager(self)
           self.alert_system.alert_sound_requested.connect(self._play_alert_sound)
           self.alert_system.engine_status_changed.connect(self._on_alert_engine_status)
           logger.info("Advanced alert system initialized successfully.")
       except Exception as e:
           logger.error(f"Failed to initialize alert system: {e}")
           self.alert_system = None

2. Add this method to handle engine status changes:

   @Slot(str)
   def _on_alert_engine_status(self, status: str):
       if status == "error":
           logger.warning("Alert engine encountered an error")
       elif status == "running":
           logger.info("Alert engine is running normally")

3. Ensure market data is passed to alert system in _on_market_data:

   if self.alert_system:
       self.alert_system.update_market_data(ticks)

4. Add cleanup in closeEvent:

   def closeEvent(self, event):
       if self.alert_system:
           self.alert_system.stop_engine()
       # ... rest of cleanup code

5. The system will now:
   - Start automatically on application startup
   - Run in a separate thread for failproof operation
   - Auto-save every 30 seconds + on all changes
   - Recover from errors automatically
   - Provide comprehensive logging and status monitoring
"""