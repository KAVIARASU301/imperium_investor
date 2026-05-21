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
        self.setMinimumSize(450, 420)  # Slightly increased height for double-line notes

        container = QWidget(self)
        container.setObjectName("alertDialogContainer")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 15, 20, 15)  # Reduced bottom margin
        layout.setSpacing(12)  # Reduced spacing

        header_layout = QHBoxLayout()
        title = QLabel("Create Price Alert")
        title.setObjectName("dialogTitle")
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(24, 24)  # Smaller close button
        close_btn.clicked.connect(self.reject)
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(close_btn)
        layout.addLayout(header_layout)

        form_layout = QFormLayout()
        form_layout.setSpacing(8)  # Reduced spacing between form fields
        form_layout.setVerticalSpacing(8)
        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        # Compact input fields
        self.symbol_input = QLineEdit(self.symbol)
        self.symbol_input.setObjectName("alertInputCompact")
        self.symbol_input.setMaximumHeight(32)  # Compact height

        self.price_input = QLineEdit(f"{self.price:.2f}")
        self.price_input.setObjectName("alertInputCompact")
        self.price_input.setMaximumHeight(32)  # Compact height

        self.condition_combo = QComboBox()
        self.condition_combo.setObjectName("alertComboCompact")
        self.condition_combo.setMaximumHeight(32)  # Compact height
        self.condition_combo.addItems([c.value for c in AlertCondition])

        self.intent_combo = QComboBox()
        self.intent_combo.setObjectName("alertComboCompact")
        self.intent_combo.setMaximumHeight(32)  # Compact height
        self.intent_combo.addItems([i.value for i in AlertIntent])

        self.validity_combo = QComboBox()
        self.validity_combo.setObjectName("alertComboCompact")
        self.validity_combo.setMaximumHeight(32)  # Compact height
        self.validity_combo.addItems(["1 Day", "3 Days", "1 Week", "2 Weeks", "1 Month"])
        self.validity_combo.setCurrentText("1 Week")

        # Double-line notes field - using QTextEdit instead of QLineEdit
        from PySide6.QtWidgets import QTextEdit
        self.note_input = QTextEdit(self.note_str)
        self.note_input.setObjectName("alertNotesInput")
        self.note_input.setMaximumHeight(60)  # Double line height
        self.note_input.setMinimumHeight(60)
        self.note_input.setPlaceholderText("Optional notes about this alert...")

        form_layout.addRow("Symbol:", self.symbol_input)
        form_layout.addRow("Alert Price:", self.price_input)
        form_layout.addRow("Condition:", self.condition_combo)
        form_layout.addRow("Intent:", self.intent_combo)
        form_layout.addRow("Validity:", self.validity_combo)
        form_layout.addRow("Notes:", self.note_input)
        layout.addLayout(form_layout)

        # Compact button layout
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)  # Reduced spacing between buttons
        button_layout.addStretch()

        # Compact buttons
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cancelButtonCompact")
        cancel_btn.setFixedSize(70, 30)  # Compact button size
        cancel_btn.clicked.connect(self.reject)

        create_btn = QPushButton("Create")  # Shortened text
        create_btn.setObjectName("createButtonCompact")
        create_btn.setFixedSize(70, 30)  # Compact button size
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
                note=self.note_input.toPlainText().strip(),  # Changed to toPlainText() for QTextEdit
                validity_days=validity_days,
                created_time=datetime.now(),
                expiry_time=datetime.now() + timedelta(days=validity_days)
            )
            self.alert_created.emit(alert)
            self.accept()
        except (ValueError, KeyError) as e:
            QMessageBox.warning(self, "Invalid Input", f"Please check your inputs: {e}")

    def _apply_styles(self):
        self.setStyleSheet("""
            * {
                font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
            }
            QWidget#alertDialogContainer {
                background-color: #12141A;
                border: 1px solid #222630;
                border-radius: 2px;
            }
            QLabel {
                color: #A0A6B5;
                font-size: 12px;
                font-weight: 500;
            }
            QLabel#dialogTitle {
                color: #FFFFFF;
                font-size: 14px;
                font-weight: 600;
                letter-spacing: 0.5px;
            }
            QLineEdit#alertInputCompact, QComboBox#alertComboCompact, QTextEdit#alertNotesInput {
                background-color: #1B1E26;
                color: #FFFFFF;
                border: 1px solid #2A2F3A;
                border-radius: 2px;
                padding: 5px 8px;
                font-size: 12px;
            }
            QLineEdit#alertInputCompact:focus, QComboBox#alertComboCompact:focus, QTextEdit#alertNotesInput:focus {
                border: 1px solid #00E676;
                background-color: #12141A;
            }
            QComboBox#alertComboCompact QAbstractItemView {
                background-color: #1B1E26;
                color: #FFFFFF;
                selection-background-color: #2A2F3A;
                selection-color: #FFFFFF;
                border: 1px solid #2A2F3A;
                outline: 0;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #7B8496;
                margin-right: 4px;
            }
            QPushButton {
                font-weight: 600;
                font-size: 12px;
                border-radius: 2px;
            }
            QPushButton#createButtonCompact {
                background-color: rgba(0, 230, 118, 0.1);
                color: #00E676;
                border: 1px solid transparent;
                padding: 6px 12px;
            }
            QPushButton#createButtonCompact:hover {
                background-color: rgba(0, 230, 118, 0.15);
                border: 1px solid rgba(0, 230, 118, 0.3);
            }
            QPushButton#cancelButtonCompact {
                background-color: transparent;
                color: #7B8496;
                border: 1px solid #2A2F3A;
                padding: 6px 12px;
            }
            QPushButton#cancelButtonCompact:hover {
                background-color: #1B1E26;
                color: #FFFFFF;
            }
            QPushButton#closeButton {
                background: transparent;
                color: #7B8496;
                border: none;
                font-size: 16px;
            }
            QPushButton#closeButton:hover {
                color: #FF4444;
            }
        """)


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
        self._last_prices: Dict[str, float] = {}
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


    def _check_engine_health(self):
        """Watchdog function to monitor engine health."""
        if self._running:
            time_since_heartbeat = time.time() - self._last_heartbeat
            if time_since_heartbeat > 30:  # No heartbeat for 30 seconds
                logger.warning(f"AlertEngine appears frozen (no heartbeat for {time_since_heartbeat:.1f}s)")
                self.engine_error.emit("Engine appears to be frozen")

    def _check_alerts(self):
        """Core alert checking logic using stateful crossover detection."""
        with QMutexLocker(self._mutex):
            if not self._alerts:
                return

            now = datetime.now()
            triggered_alerts, expired_alerts = [], []

            for alert in self._alerts[:]:  # Copy list to avoid modification during iteration
                try:
                    if alert.expiry_time <= now:
                        expired_alerts.append(alert)
                        continue

                    current_price = self._market_data.get(alert.symbol)
                    if current_price is None:
                        continue

                    prev_price = self._last_prices.get(alert.symbol, current_price)
                    condition_met = False

                    if alert.condition == AlertCondition.PRICE_IS_ABOVE:
                        # Fire immediately once price touches/crosses the alert level.
                        # No candle close confirmation is required.
                        condition_met = current_price >= alert.price
                    elif alert.condition == AlertCondition.PRICE_IS_BELOW:
                        # Fire immediately once price touches/crosses the alert level.
                        # No candle close confirmation is required.
                        condition_met = current_price <= alert.price

                    self._last_prices[alert.symbol] = current_price

                    if condition_met:
                        alert.triggered = True
                        alert.triggered_time = now
                        alert.triggered_price = current_price
                        triggered_alerts.append(alert)
                        logger.info(
                            f"Alert triggered: {alert.symbol} crossed {alert.price:.2f} at {current_price:.2f}"
                        )

                except Exception as e:
                    logger.error(f"Error checking alert {alert.id}: {e}")
                    continue

            for alert in expired_alerts + triggered_alerts:
                if alert in self._alerts:
                    self._alerts.remove(alert)

        for alert in expired_alerts:
            self.alert_expired.emit(alert)

        for alert in triggered_alerts:
            self.alert_triggered.emit(alert, alert.triggered_price)

    def stop(self):
        """Gracefully stop the engine with proper cleanup."""
        logger.info("Stopping AlertEngine...")

        # Set the running flag to False first
        self._running = False

        # Stop the watchdog timer
        if hasattr(self, '_watchdog_timer') and self._watchdog_timer:
            self._watchdog_timer.stop()
            self._watchdog_timer.deleteLater()

        # Wake up the thread if it's sleeping
        if self.isRunning():
            # Request interruption
            self.requestInterruption()

        logger.info("AlertEngine stop requested")

    def run(self):
        """Main engine loop with proper interruption handling."""
        self._running = True
        self._error_count = 0
        logger.info("AlertEngine thread started successfully")

        while self._running and not self.isInterruptionRequested():
            try:
                self._check_alerts()
                self._last_heartbeat = time.time()
                self._error_count = 0  # Reset error count on successful iteration

                # Use msleep with smaller intervals to allow faster interruption
                for _ in range(self._check_interval_ms // 100):  # Break into 100ms chunks
                    if not self._running or self.isInterruptionRequested():
                        break
                    self.msleep(100)

            except Exception as e:
                self._error_count += 1
                logger.error(f"Error in AlertEngine main loop (#{self._error_count}): {e}")

                if self._error_count >= self._max_errors:
                    logger.critical("AlertEngine exceeded maximum error count, stopping")
                    self.engine_error.emit(f"Engine failure after {self._max_errors} errors")
                    break

                # Brief pause before retry (also interruptible)
                for _ in range(50):  # 5 seconds in 100ms chunks
                    if not self._running or self.isInterruptionRequested():
                        break
                    self.msleep(100)

        logger.info("AlertEngine thread stopped")

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
        self.setStyleSheet("""
            * {
                font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
            }
            QWidget#mainContainer {
                background-color: #12141A;
                border: 1px solid #222630;
                border-radius: 2px;
            }
            QLabel#dialogTitle {
                color: #FFFFFF;
                font-size: 15px;
                font-weight: 600;
                letter-spacing: 0.5px;
            }
            QPushButton#closeButton {
                background: transparent;
                color: #7B8496;
                border: none;
                font-size: 16px;
            }
            QPushButton#closeButton:hover { color: #FF4444; }
            QLabel#statusLabel {
                color: #7B8496;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 8px;
                background: transparent;
                border: 1px solid #2A2F3A;
                border-radius: 2px;
            }
            QTableWidget#alertTable {
                background-color: transparent;
                color: #D1D4DC;
                border: none;
                gridline-color: #1E222B;
                font-size: 12px;
                outline: none;
            }
            QTableWidget#alertTable::item {
                padding: 2px 6px;
                border-bottom: 1px solid #1A1D24;
            }
            QTableWidget#alertTable::item:selected {
                background-color: #1B1E26;
                color: #FFFFFF;
            }
            QHeaderView::section {
                background-color: #12141A;
                color: #7B8496;
                border: none;
                border-bottom: 1px solid #222630;
                font-size: 10px;
                font-weight: 700;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                padding: 4px 6px;
                text-align: left;
            }
            QPushButton#deleteButton {
                background-color: transparent;
                color: #FF4444;
                border: none;
                font-weight: 600;
                font-size: 11px;
            }
            QPushButton#deleteButton:hover {
                color: #FF6666;
                background-color: rgba(255, 68, 68, 0.1);
                border-radius: 2px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #2A2F3A;
                border-radius: 2px;
                background: #1B1E26;
            }
            QCheckBox::indicator:checked {
                background: #00E676;
                border: 1px solid #00E676;
            }
            QTabWidget::pane {
                border: none;
                border-top: 1px solid #222630;
            }
            QTabBar::tab {
                background: transparent;
                color: #7B8496;
                padding: 8px 16px;
                border: none;
                font-size: 12px;
                font-weight: 600;
            }
            QTabBar::tab:selected {
                background: transparent;
                color: #FFFFFF;
                border-bottom: 2px solid #00E676;
            }
            QTabBar::tab:hover:!selected {
                color: #D1D4DC;
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

    # Refactored AlertSystemManager methods for chart lines integration
    # Add these methods to your AlertSystemManager class

    @Slot(str)
    def create_alert_from_chart(self, alert_json: str):
        """Create alert from chart context menu with chart line integration."""
        try:
            if not self._initialized:
                logger.warning("Alert system not initialized, cannot create alert from chart")
                return

            # Parse the JSON data from chart
            alert_data = json.loads(alert_json)
            symbol = alert_data.get('symbol', '')
            price = alert_data.get('price', 0.0)
            intent = alert_data.get('intent', '')
            note = alert_data.get('note', '')
            current_ltp = alert_data.get('current_ltp', 0.0)

            if not symbol or not price:
                logger.error("Invalid alert data from chart: missing symbol or price")
                return

            logger.info(f"Creating alert from chart: {symbol} at {price:.2f}")

            # Show the creation dialog
            self._show_creation_dialog(
                symbol=symbol,
                price=price,
                intent=intent,
                note=note,
                current_ltp=current_ltp
            )

        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to parse alert data from chart: {e}")
        except Exception as e:
            logger.error(f"Error creating alert from chart: {e}")

    def _add_alert(self, alert: Alert):
        """Add a new alert to the system with chart line integration."""
        try:
            # Add alert to the system
            self.all_alerts.append(alert)
            logger.info(f"Added alert: {alert.symbol} at {alert.price:.2f} with intent: {alert.intent.value}")

            # Add chart line for the alert
            if hasattr(self.main_window, 'chart_lines_manager'):
                intent_text = alert.intent.value if alert.intent != AlertIntent.BREAKOUT_WATCH else ""
                success = self.main_window.chart_lines_manager.add_alert_line(
                    symbol=alert.symbol,
                    price=alert.price,
                    intent=intent_text
                )
                if success:
                    logger.info(f"Alert line added to chart for {alert.symbol}")
                else:
                    logger.warning(f"Failed to add alert line to chart for {alert.symbol}")
            else:
                logger.warning("Chart lines manager not available, alert created without chart line")

            # Save and refresh all components
            self._save_and_refresh_all()

        except Exception as e:
            logger.error(f"Error adding alert: {e}")

    def _delete_alert(self, alert_to_delete: Alert):
        """Delete an alert from the system with chart line removal."""
        try:
            if alert_to_delete not in self.all_alerts:
                logger.warning(f"Alert not found in system: {alert_to_delete.symbol} at {alert_to_delete.price}")
                return

            # Remove chart line first
            if hasattr(self.main_window, 'chart_lines_manager'):
                success = self.main_window.chart_lines_manager.remove_alert_line(
                    symbol=alert_to_delete.symbol,
                    price=alert_to_delete.price
                )
                if success:
                    logger.info(f"Alert line removed from chart for {alert_to_delete.symbol}")
                else:
                    logger.warning(f"Failed to remove alert line from chart for {alert_to_delete.symbol}")
            else:
                logger.warning("Chart lines manager not available, alert deleted without removing chart line")

            # Remove from alerts list
            self.all_alerts.remove(alert_to_delete)
            logger.info(f"Deleted alert: {alert_to_delete.symbol} at {alert_to_delete.price:.2f}")

            # Save and refresh all components
            self._save_and_refresh_all()

        except Exception as e:
            logger.error(f"Error deleting alert: {e}")

    @Slot(object, float)
    def _on_alert_triggered(self, alert: Alert, trigger_price: float):
        """Handle triggered alert with chart line removal."""
        try:
            # Play alert sound
            play_alert()
            self.alert_sound_requested.emit()

            # Update alert status
            alert.triggered = True
            alert.triggered_time = datetime.now()
            alert.triggered_price = trigger_price

            logger.info(f"Alert Triggered: {alert.symbol} at {trigger_price:.2f}")

            # Remove chart line for triggered alert
            if hasattr(self.main_window, 'chart_lines_manager'):
                success = self.main_window.chart_lines_manager.remove_alert_line(
                    symbol=alert.symbol,
                    price=alert.price
                )
                if success:
                    logger.info(f"Triggered alert line removed from chart for {alert.symbol}")
                else:
                    logger.warning(f"Failed to remove triggered alert line from chart for {alert.symbol}")
            else:
                logger.warning("Chart lines manager not available, triggered alert line not removed")

            # Save and refresh all components
            self._save_and_refresh_all()

            # Switch to triggered alerts tab if dialog is open
            if self.alert_manager_dialog and self.alert_manager_dialog.isVisible():
                self.alert_manager_dialog.tab_widget.setCurrentIndex(1)

        except Exception as e:
            logger.error(f"Error handling triggered alert: {e}")

    @Slot(object)
    def _on_alert_expired(self, alert: Alert):
        """Handle expired alert with chart line removal."""
        try:
            if alert not in self.all_alerts:
                logger.warning(f"Expired alert not found in system: {alert.symbol}")
                return

            # Remove chart line for expired alert
            if hasattr(self.main_window, 'chart_lines_manager'):
                success = self.main_window.chart_lines_manager.remove_alert_line(
                    symbol=alert.symbol,
                    price=alert.price
                )
                if success:
                    logger.info(f"Expired alert line removed from chart for {alert.symbol}")
                else:
                    logger.warning(f"Failed to remove expired alert line from chart for {alert.symbol}")
            else:
                logger.warning("Chart lines manager not available, expired alert line not removed")

            # Remove from alerts list
            self.all_alerts.remove(alert)
            logger.info(f"Alert expired and removed: {alert.symbol} at {alert.price:.2f}")

            # Save and refresh all components
            self._save_and_refresh_all()

        except Exception as e:
            logger.error(f"Error handling expired alert: {e}")

    def delete_alert_by_symbol_and_price(self, symbol: str, price: float):
        """Public method to delete alert by symbol and price (useful for external calls)."""
        try:
            # Find the alert
            alert_to_delete = None
            for alert in self.all_alerts:
                if alert.symbol == symbol and abs(alert.price - price) < 0.01:
                    alert_to_delete = alert
                    break

            if alert_to_delete:
                self._delete_alert(alert_to_delete)
                return True
            else:
                logger.warning(f"No alert found for {symbol} at {price:.2f}")
                return False

        except Exception as e:
            logger.error(f"Error deleting alert by symbol and price: {e}")
            return False

    def get_alerts_for_symbol(self, symbol: str) -> List[Alert]:
        """Get all alerts for a specific symbol."""
        try:
            return [alert for alert in self.all_alerts if alert.symbol == symbol]
        except Exception as e:
            logger.error(f"Error getting alerts for symbol {symbol}: {e}")
            return []

    def remove_all_alert_lines_for_symbol(self, symbol: str):
        """Remove all alert lines for a symbol from chart (useful when switching symbols)."""
        try:
            if hasattr(self.main_window, 'chart_lines_manager'):
                # Get all alerts for this symbol
                symbol_alerts = self.get_alerts_for_symbol(symbol)

                # Remove each alert line
                for alert in symbol_alerts:
                    if not alert.triggered:  # Only remove active alert lines
                        self.main_window.chart_lines_manager.remove_alert_line(
                            symbol=alert.symbol,
                            price=alert.price
                        )

                logger.info(f"Removed all alert lines for symbol: {symbol}")

        except Exception as e:
            logger.error(f"Error removing alert lines for symbol {symbol}: {e}")

    def refresh_alert_lines_for_symbol(self, symbol: str):
        """Refresh all alert lines for a symbol on chart (useful when loading new symbol)."""
        try:
            if hasattr(self.main_window, 'chart_lines_manager'):
                # Get all active alerts for this symbol
                symbol_alerts = [alert for alert in self.all_alerts
                                 if alert.symbol == symbol and not alert.triggered]

                # Add lines for each active alert
                for alert in symbol_alerts:
                    intent_text = alert.intent.value if alert.intent != AlertIntent.BREAKOUT_WATCH else ""
                    self.main_window.chart_lines_manager.add_alert_line(
                        symbol=alert.symbol,
                        price=alert.price,
                        intent=intent_text
                    )

                logger.info(f"Refreshed {len(symbol_alerts)} alert lines for symbol: {symbol}")

        except Exception as e:
            logger.error(f"Error refreshing alert lines for symbol {symbol}: {e}")

    # Method to handle bulk alert operations (useful for cleanup)
    def cleanup_triggered_alert_lines(self):
        """Remove all chart lines for triggered alerts (cleanup method)."""
        try:
            if hasattr(self.main_window, 'chart_lines_manager'):
                triggered_alerts = [alert for alert in self.all_alerts if alert.triggered]

                for alert in triggered_alerts:
                    self.main_window.chart_lines_manager.remove_alert_line(
                        symbol=alert.symbol,
                        price=alert.price
                    )

                logger.info(f"Cleaned up {len(triggered_alerts)} triggered alert lines")

        except Exception as e:
            logger.error(f"Error cleaning up triggered alert lines: {e}")

    # Method to synchronize chart lines with alert system (useful for recovery)
    def sync_alert_lines_with_system(self, symbol: str = None):
        """Synchronize chart lines with alert system state."""
        try:
            if not hasattr(self.main_window, 'chart_lines_manager'):
                logger.warning("Chart lines manager not available for sync")
                return

            if symbol:
                # Sync for specific symbol
                active_alerts = [alert for alert in self.all_alerts
                                 if alert.symbol == symbol and not alert.triggered]

                # Remove all existing alert lines for this symbol first
                self.remove_all_alert_lines_for_symbol(symbol)

                # Add lines for active alerts
                for alert in active_alerts:
                    intent_text = alert.intent.value if alert.intent != AlertIntent.BREAKOUT_WATCH else ""
                    self.main_window.chart_lines_manager.add_alert_line(
                        symbol=alert.symbol,
                        price=alert.price,
                        intent=intent_text
                    )

                logger.info(f"Synced alert lines for symbol {symbol}: {len(active_alerts)} lines")
            else:
                # Sync for all symbols (heavy operation, use sparingly)
                active_alerts = [alert for alert in self.all_alerts if not alert.triggered]
                symbols = list(set(alert.symbol for alert in active_alerts))

                for sym in symbols:
                    self.refresh_alert_lines_for_symbol(sym)

                logger.info(f"Synced alert lines for {len(symbols)} symbols")

        except Exception as e:
            logger.error(f"Error syncing alert lines: {e}")

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
            today = datetime.now().date()
            triggered = len([
                a for a in self.all_alerts
                if a.triggered and a.triggered_time and a.triggered_time.date() == today
            ])
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
        """Switch selected symbol on every chart panel."""
        try:
            for chart_attr in ("candlestick_chart", "candlestick_chart_secondary"):
                chart = getattr(self.main_window, chart_attr, None)
                if chart is not None and hasattr(chart, "on_search"):
                    chart.on_search(symbol)
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

    def stop_engine(self):
        """Stop the alert engine and save the final state with improved cleanup."""
        try:
            logger.info("Stopping alert engine and saving alerts...")

            # Stop auto-save timer first
            if hasattr(self, 'auto_save_timer'):
                self.auto_save_timer.stop()

            # Save final state
            self._save_alerts()

            # Stop engine with timeout
            if hasattr(self, 'alert_engine') and self.alert_engine:
                self.alert_engine.stop()

                # Give it time to stop gracefully
                if self.alert_engine.isRunning():
                    logger.info("Waiting for alert engine to stop...")
                    if not self.alert_engine.wait(5000):  # Wait 5 seconds
                        logger.warning("Alert engine didn't stop gracefully, terminating...")
                        self.alert_engine.terminate()
                        # Wait for termination
                        self.alert_engine.wait(2000)

                # Clean up the engine reference
                self.alert_engine = None

            self.engine_status_changed.emit("stopped")
            logger.info("Alert engine stopped successfully")

        except Exception as e:
            logger.error(f"Error stopping alert engine: {e}")
            # Ensure the engine reference is cleared even on error
            if hasattr(self, 'alert_engine'):
                self.alert_engine = None
