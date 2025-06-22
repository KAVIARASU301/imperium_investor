"""
Advanced Alert Management System for TC2000-style Trading Terminal
==============================================================

This system provides a comprehensive alert management interface with:
- Three-tab design: Set Alerts, Active Alerts, Triggered History
- Intelligent alert creation from chart right-click
- Position-aware alert suggestions
- Configurable alert checking intervals
- Notification badges and sound alerts
- Professional dark theme matching TC2000 aesthetics
"""
import logging

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QLabel, QPushButton, QFrame, QWidget, QLineEdit, QComboBox,
    QCheckBox, QSpinBox, QTextEdit, QSplitter, QGroupBox, QFormLayout,
    QMessageBox, QMenu, QAbstractItemView, QProgressBar, QSlider
)
from PySide6.QtGui import QColor, QFont, QMouseEvent, QAction, QPalette, QIcon
from PySide6.QtCore import Qt, Signal, QTimer, QThread, QMutex, QMutexLocker, Slot, QObject
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import json
import os
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)

class AlertCondition(Enum):
    """Alert condition types."""
    CROSSES_ABOVE = "Crosses Above"
    CROSSES_BELOW = "Crosses Below"
    CURRENT_ABOVE = "Current Above"
    CURRENT_BELOW = "Current Below"


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
    """Enhanced alert data structure."""
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

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
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
        """Create from dictionary."""
        return cls(
            id=data['id'],
            symbol=data['symbol'],
            price=data['price'],
            condition=AlertCondition(data['condition']),
            intent=AlertIntent(data['intent']),
            note=data['note'],
            validity_days=data['validity_days'],
            created_time=datetime.fromisoformat(data['created_time']),
            expiry_time=datetime.fromisoformat(data['expiry_time']),
            triggered=data.get('triggered', False),
            triggered_time=datetime.fromisoformat(data['triggered_time']) if data.get('triggered_time') else None,
            triggered_price=data.get('triggered_price')
        )


class AlertEngine(QThread):
    """Background thread for alert processing with configurable intervals."""

    alert_triggered = Signal(object, float)  # alert, trigger_price
    alert_expired = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.alerts: List[Alert] = []
        self.market_data: Dict[str, float] = {}  # symbol -> current_price
        self.check_interval = 5000  # Default 5 seconds
        self.running = False
        self.mutex = QMutex()

    def set_check_interval(self, seconds: int):
        """Set alert checking interval in seconds."""
        self.check_interval = max(1, seconds) * 1000  # Convert to milliseconds

    def update_alerts(self, alerts: List[Alert]):
        """Thread-safe alert update."""
        with QMutexLocker(self.mutex):
            self.alerts = [alert for alert in alerts if not alert.triggered and alert.expiry_time > datetime.now()]

    def update_market_data(self, symbol: str, price: float):
        """Thread-safe market data update."""
        with QMutexLocker(self.mutex):
            self.market_data[symbol] = price

    def run(self):
        """Main alert checking loop."""
        self.running = True
        timer = QTimer()
        timer.timeout.connect(self._check_alerts)
        timer.start(self.check_interval)

        while self.running:
            self.msleep(100)

    def stop(self):
        """Stop the alert engine."""
        self.running = False
        self.quit()
        self.wait()

    def _check_alerts(self):
        """Check alerts against current market data."""
        with QMutexLocker(self.mutex):
            current_time = datetime.now()

            for alert in self.alerts[:]:  # Create copy for iteration
                # Check expiry
                if alert.expiry_time <= current_time:
                    self.alert_expired.emit(alert)
                    self.alerts.remove(alert)
                    continue

                # Check trigger condition
                current_price = self.market_data.get(alert.symbol)
                if current_price is None:
                    continue

                triggered = False
                if alert.condition == AlertCondition.CROSSES_ABOVE and current_price >= alert.price:
                    triggered = True
                elif alert.condition == AlertCondition.CROSSES_BELOW and current_price <= alert.price:
                    triggered = True
                elif alert.condition == AlertCondition.CURRENT_ABOVE and current_price > alert.price:
                    triggered = True
                elif alert.condition == AlertCondition.CURRENT_BELOW and current_price < alert.price:
                    triggered = True

                if triggered:
                    alert.triggered = True
                    alert.triggered_time = current_time
                    alert.triggered_price = current_price
                    self.alert_triggered.emit(alert, current_price)
                    self.alerts.remove(alert)


class AlertCreationDialog(QDialog):
    """Enhanced alert creation dialog with intelligent prefilling."""

    alert_created = Signal(Alert)

    def __init__(self, parent=None, symbol: str = "", price: float = 0.0,
                 current_positions: Dict[str, Any] = None):
        super().__init__(parent)
        self.symbol = symbol.upper()
        self.price = price
        self.current_positions = current_positions or {}
        self._setup_ui()
        self._apply_styles()
        self._prefill_intelligent_defaults()

    def _setup_ui(self):
        """Setup the dialog UI."""
        self.setWindowTitle("Create Price Alert")
        self.setModal(True)
        self.setMinimumSize(500, 400)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Main container
        container = QWidget(self)
        container.setObjectName("alertDialogContainer")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 15, 20, 20)
        layout.setSpacing(15)

        # Header
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

        # Form layout
        form_layout = QFormLayout()
        form_layout.setSpacing(12)

        # Symbol input
        self.symbol_input = QLineEdit(self.symbol)
        self.symbol_input.setObjectName("alertInput")
        self.symbol_input.textChanged.connect(lambda text: self.symbol_input.setText(text.upper()))
        form_layout.addRow("Symbol:", self.symbol_input)

        # Price input
        self.price_input = QLineEdit(str(self.price) if self.price > 0 else "")
        self.price_input.setObjectName("alertInput")
        form_layout.addRow("Alert Price:", self.price_input)

        # Condition combo
        self.condition_combo = QComboBox()
        self.condition_combo.setObjectName("alertCombo")
        self.condition_combo.addItems([condition.value for condition in AlertCondition])
        form_layout.addRow("Condition:", self.condition_combo)

        # Intent combo (intelligent suggestions)
        self.intent_combo = QComboBox()
        self.intent_combo.setObjectName("alertCombo")
        self.intent_combo.addItems([intent.value for intent in AlertIntent])
        form_layout.addRow("Intent:", self.intent_combo)

        # Validity
        self.validity_combo = QComboBox()
        self.validity_combo.setObjectName("alertCombo")
        validity_options = ["1 Day", "3 Days", "1 Week", "2 Weeks", "1 Month", "3 Months", "6 Months"]
        self.validity_combo.addItems(validity_options)
        self.validity_combo.setCurrentText("1 Week")
        form_layout.addRow("Validity:", self.validity_combo)

        # Note
        self.note_input = QTextEdit()
        self.note_input.setObjectName("alertNote")
        self.note_input.setMaximumHeight(80)
        form_layout.addRow("Note:", self.note_input)

        layout.addLayout(form_layout)

        # Buttons
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
        """Intelligently prefill form based on context."""
        if not self.symbol or self.price <= 0:
            return

        # Get current LTP (this would come from market data)
        current_ltp = self.price  # In real implementation, fetch current LTP

        # Check if user has positions in this symbol
        has_position = self.symbol in self.current_positions
        position_type = None
        if has_position:
            position_type = self.current_positions[self.symbol].get('transaction_type', '')

        # Intelligent condition and intent selection
        if self.price > current_ltp:
            # Alert price is above current price
            if has_position and position_type == 'BUY':
                # User has long position, likely setting profit target
                self.condition_combo.setCurrentText(AlertCondition.CROSSES_ABOVE.value)
                self.intent_combo.setCurrentText(AlertIntent.PROFIT_TARGET.value)
                self.note_input.setPlainText(f"Profit target for long position in {self.symbol}")
            elif has_position and position_type == 'SELL':
                # User has short position, likely setting stop loss
                self.condition_combo.setCurrentText(AlertCondition.CROSSES_ABOVE.value)
                self.intent_combo.setCurrentText(AlertIntent.STOP_LOSS.value)
                self.note_input.setPlainText(f"Stop loss for short position in {self.symbol}")
            else:
                # No position, likely breakout watch or buy entry
                self.condition_combo.setCurrentText(AlertCondition.CROSSES_ABOVE.value)
                self.intent_combo.setCurrentText(AlertIntent.BUY_ENTRY.value)
                self.note_input.setPlainText(f"Buy entry signal for {self.symbol} above {self.price}")
        else:
            # Alert price is below current price
            if has_position and position_type == 'BUY':
                # User has long position, likely setting stop loss
                self.condition_combo.setCurrentText(AlertCondition.CROSSES_BELOW.value)
                self.intent_combo.setCurrentText(AlertIntent.STOP_LOSS.value)
                self.note_input.setPlainText(f"Stop loss for long position in {self.symbol}")
            elif has_position and position_type == 'SELL':
                # User has short position, likely setting profit target
                self.condition_combo.setCurrentText(AlertCondition.CROSSES_BELOW.value)
                self.intent_combo.setCurrentText(AlertIntent.PROFIT_TARGET.value)
                self.note_input.setPlainText(f"Profit target for short position in {self.symbol}")
            else:
                # No position, likely support watch or short entry
                self.condition_combo.setCurrentText(AlertCondition.CROSSES_BELOW.value)
                self.intent_combo.setCurrentText(AlertIntent.SELL_ENTRY.value)
                self.note_input.setPlainText(f"Short entry signal for {self.symbol} below {self.price}")

    def _create_alert(self):
        """Create and emit the new alert."""
        try:
            symbol = self.symbol_input.text().strip().upper()
            price = float(self.price_input.text().strip())
            condition = AlertCondition(self.condition_combo.currentText())
            intent = AlertIntent(self.intent_combo.currentText())
            note = self.note_input.toPlainText().strip()

            # Parse validity
            validity_text = self.validity_combo.currentText()
            validity_days = {
                "1 Day": 1, "3 Days": 3, "1 Week": 7, "2 Weeks": 14,
                "1 Month": 30, "3 Months": 90, "6 Months": 180
            }.get(validity_text, 7)

            # Create alert
            alert = Alert(
                id=f"{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                symbol=symbol,
                price=price,
                condition=condition,
                intent=intent,
                note=note,
                validity_days=validity_days,
                created_time=datetime.now(),
                expiry_time=datetime.now() + timedelta(days=validity_days)
            )

            self.alert_created.emit(alert)
            self.accept()

        except ValueError as e:
            QMessageBox.warning(self, "Invalid Input", f"Please check your inputs: {e}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create alert: {e}")

    def _apply_styles(self):
        """Apply dark theme styles."""
        self.setStyleSheet("""
            QWidget#alertDialogContainer {
                background-color: #0a0a0a;
                border: 1px solid #202020;
                border-radius: 8px;
            }
            
            QLabel#dialogTitle {
                color: #ffffff;
                font-size: 18px;
                font-weight: 600;
            }
            
            QPushButton#closeButton {
                background-color: transparent;
                border: none;
                color: #8a8a9e;
                font-size: 16px;
                font-weight: bold;
                padding: 5px;
            }
            QPushButton#closeButton:hover {
                color: #d63031;
            }
            
            QLineEdit#alertInput, QComboBox#alertCombo {
                background-color: #1a1a1a;
                border: 1px solid #303030;
                border-radius: 4px;
                padding: 8px;
                color: #e0e0e0;
                font-size: 12px;
            }
            QLineEdit#alertInput:focus, QComboBox#alertCombo:focus {
                border-color: #6a9cff;
            }
            
            QTextEdit#alertNote {
                background-color: #1a1a1a;
                border: 1px solid #303030;
                border-radius: 4px;
                padding: 8px;
                color: #e0e0e0;
                font-size: 12px;
            }
            
            QPushButton#createButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 20px;
                font-weight: bold;
            }
            QPushButton#createButton:hover {
                background-color: #45a049;
            }
            
            QPushButton#cancelButton {
                background-color: #555;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 20px;
            }
            QPushButton#cancelButton:hover {
                background-color: #666;
            }
        """)


class AdvancedAlertManager(QDialog):
    """Main alert management window with three-tab interface."""

    symbol_selected = Signal(str)  # Signal to switch chart to symbol
    alert_sound_requested = Signal()  # Signal to play alert sound

    def __init__(self, parent=None, instrument_map: Dict = None, positions: Dict = None):
        super().__init__(parent)
        self.instrument_map = instrument_map or {}
        self.current_positions = positions or {}
        self.alerts: List[Alert] = []
        self.triggered_alerts: List[Alert] = []

        # Alert engine
        self.alert_engine = AlertEngine(self)
        self.alert_engine.alert_triggered.connect(self._on_alert_triggered)
        self.alert_engine.alert_expired.connect(self._on_alert_expired)

        self._setup_ui()
        self._apply_styles()
        self._load_alerts()
        self._start_alert_engine()

        # Drag functionality
        self._drag_pos = None

    def _setup_ui(self):
        """Setup the main UI with three tabs."""
        self.setWindowTitle("Alert Manager")
        self.setMinimumSize(1200, 800)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Main container
        container = QWidget(self)
        container.setObjectName("mainContainer")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 15, 20, 20)
        layout.setSpacing(15)

        # Header with controls
        header_layout = self._create_header()
        layout.addLayout(header_layout)

        # Tab widget
        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("alertTabs")

        # Tab 1: Create/Set Alerts
        self.create_tab = self._create_set_alerts_tab()
        self.tab_widget.addTab(self.create_tab, "Set Alert")

        # Tab 2: Active Alerts
        self.active_tab = self._create_active_alerts_tab()
        self.tab_widget.addTab(self.active_tab, "Active Alerts")

        # Tab 3: Triggered History
        self.history_tab = self._create_history_tab()
        self.tab_widget.addTab(self.history_tab, "Alert History")

        layout.addWidget(self.tab_widget)

        # Status bar
        status_layout = self._create_status_bar()
        layout.addLayout(status_layout)

    def _create_header(self) -> QHBoxLayout:
        """Create header with title and controls."""
        header_layout = QHBoxLayout()

        # Title section
        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)

        title = QLabel("Alert Manager")
        title.setObjectName("dialogTitle")

        subtitle = QLabel("Manage price alerts and notifications")
        subtitle.setObjectName("subtitle")

        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)

        # Controls section
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(10)

        # Alert check interval
        interval_label = QLabel("Check Interval:")
        interval_label.setObjectName("controlLabel")

        self.interval_spin = QSpinBox()
        self.interval_spin.setObjectName("intervalSpin")
        self.interval_spin.setRange(1, 300)  # 1 second to 5 minutes
        self.interval_spin.setValue(5)
        self.interval_spin.setSuffix(" sec")
        self.interval_spin.valueChanged.connect(self._update_check_interval)

        # API pressure indicator
        self.api_pressure_label = QLabel("API Load: Low")
        self.api_pressure_label.setObjectName("apiPressure")

        controls_layout.addWidget(interval_label)
        controls_layout.addWidget(self.interval_spin)
        controls_layout.addWidget(self.api_pressure_label)
        controls_layout.addStretch()

        # Close button
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.clicked.connect(self.close)

        header_layout.addLayout(title_layout)
        header_layout.addStretch()
        header_layout.addLayout(controls_layout)
        header_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignTop)

        return header_layout

    def _create_set_alerts_tab(self) -> QWidget:
        """Create the alert creation tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)

        # Quick create section
        quick_group = QGroupBox("Quick Alert Creation")
        quick_group.setObjectName("alertGroup")
        quick_layout = QFormLayout(quick_group)

        self.quick_symbol = QLineEdit()
        self.quick_symbol.setObjectName("alertInput")
        self.quick_symbol.setPlaceholderText("e.g., NSE:RELIANCE")

        self.quick_price = QLineEdit()
        self.quick_price.setObjectName("alertInput")
        self.quick_price.setPlaceholderText("Alert price")

        quick_create_btn = QPushButton("Create Alert")
        quick_create_btn.setObjectName("createButton")
        quick_create_btn.clicked.connect(self._quick_create_alert)

        quick_layout.addRow("Symbol:", self.quick_symbol)
        quick_layout.addRow("Price:", self.quick_price)
        quick_layout.addRow("", quick_create_btn)

        layout.addWidget(quick_group)

        # Bulk operations section
        bulk_group = QGroupBox("Bulk Operations")
        bulk_group.setObjectName("alertGroup")
        bulk_layout = QHBoxLayout(bulk_group)

        import_btn = QPushButton("Import Alerts")
        import_btn.setObjectName("actionButton")

        export_btn = QPushButton("Export Alerts")
        export_btn.setObjectName("actionButton")

        clear_expired_btn = QPushButton("Clear Expired")
        clear_expired_btn.setObjectName("actionButton")
        clear_expired_btn.clicked.connect(self._clear_expired_alerts)

        bulk_layout.addWidget(import_btn)
        bulk_layout.addWidget(export_btn)
        bulk_layout.addWidget(clear_expired_btn)
        bulk_layout.addStretch()

        layout.addWidget(bulk_group)
        layout.addStretch()

        return tab

    def _create_active_alerts_tab(self) -> QWidget:
        """Create the active alerts tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)

        # Active alerts table
        self.active_table = QTableWidget(0, 8)
        self.active_table.setObjectName("alertTable")
        self.active_table.setHorizontalHeaderLabels([
            "Symbol", "Price", "Current", "Condition", "Intent", "Note", "Expires", "Actions"
        ])

        # Configure table
        header = self.active_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)  # Symbol
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)  # Price
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)  # Current
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)  # Condition
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)  # Intent
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)  # Note
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)  # Expires
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)  # Actions

        self.active_table.setColumnWidth(0, 120)  # Symbol
        self.active_table.setColumnWidth(1, 80)   # Price
        self.active_table.setColumnWidth(2, 80)   # Current
        self.active_table.setColumnWidth(6, 120)  # Expires
        self.active_table.setColumnWidth(7, 100)  # Actions

        self.active_table.setAlternatingRowColors(True)
        self.active_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.active_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.active_table.cellDoubleClicked.connect(self._on_symbol_double_clicked)

        layout.addWidget(self.active_table)

        return tab

    def _create_history_tab(self) -> QWidget:
        """Create the triggered alerts history tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)

        # History controls
        controls_layout = QHBoxLayout()

        # Date filter
        date_label = QLabel("Show:")
        self.history_filter = QComboBox()
        self.history_filter.setObjectName("alertCombo")
        self.history_filter.addItems(["Today", "This Week", "This Month", "All Time"])
        self.history_filter.currentTextChanged.connect(self._filter_history)

        controls_layout.addWidget(date_label)
        controls_layout.addWidget(self.history_filter)
        controls_layout.addStretch()

        # Clear history button
        clear_history_btn = QPushButton("Clear History")
        clear_history_btn.setObjectName("actionButton")
        clear_history_btn.clicked.connect(self._clear_history)

        controls_layout.addWidget(clear_history_btn)
        layout.addLayout(controls_layout)

        # History table
        self.history_table = QTableWidget(0, 7)
        self.history_table.setObjectName("alertTable")
        self.history_table.setHorizontalHeaderLabels([
            "Triggered Time", "Symbol", "Alert Price", "Trigger Price", "Condition", "Intent", "Note"
        ])

        # Configure history table
        hist_header = self.history_table.horizontalHeader()
        hist_header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        self.history_table.setAlternatingRowColors(True)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.cellDoubleClicked.connect(self._on_symbol_double_clicked)

        layout.addWidget(self.history_table)

        return tab

    def _create_status_bar(self) -> QHBoxLayout:
        """Create status bar with statistics."""
        status_layout = QHBoxLayout()

        self.active_count_label = QLabel("Active: 0")
        self.active_count_label.setObjectName("statusLabel")

        self.triggered_today_label = QLabel("Triggered Today: 0")
        self.triggered_today_label.setObjectName("statusLabel")

        self.total_alerts_label = QLabel("Total Alerts: 0")
        self.total_alerts_label.setObjectName("statusLabel")

        status_layout.addWidget(self.active_count_label)
        status_layout.addWidget(self.triggered_today_label)
        status_layout.addWidget(self.total_alerts_label)
        status_layout.addStretch()

        return status_layout

    def _quick_create_alert(self):
        """Quick alert creation from the form."""
        symbol = self.quick_symbol.text().strip().upper()
        try:
            price = float(self.quick_price.text().strip())
        except ValueError:
            QMessageBox.warning(self, "Invalid Price", "Please enter a valid price.")
            return

        if not symbol or price <= 0:
            QMessageBox.warning(self, "Invalid Input", "Please enter symbol and price.")
            return

        # Open detailed creation dialog
        dialog = AlertCreationDialog(self, symbol, price, self.current_positions)
        dialog.alert_created.connect(self._add_alert)
        dialog.exec()

    def _add_alert(self, alert: Alert):
        """Add a new alert to the system."""
        self.alerts.append(alert)
        self._save_alerts()
        self._refresh_active_table()
        self._update_status()
        self._update_alert_engine()

        # Clear quick form
        self.quick_symbol.clear()
        self.quick_price.clear()

    def _clear_expired_alerts(self):
        """Remove expired alerts."""
        current_time = datetime.now()
        before_count = len(self.alerts)
        self.alerts = [alert for alert in self.alerts if alert.expiry_time > current_time]
        removed_count = before_count - len(self.alerts)

        if removed_count > 0:
            self._save_alerts()
            self._refresh_active_table()
            self._update_status()
            self._update_alert_engine()
            QMessageBox.information(self, "Cleanup Complete", f"Removed {removed_count} expired alerts.")
        else:
            QMessageBox.information(self, "No Action Needed", "No expired alerts found.")

    def _on_symbol_double_clicked(self, row: int, column: int):
        """Handle double-click on symbol to switch chart."""
        table = self.sender()
        try:
            symbol_item = table.item(row, 0) if table == self.history_table else table.item(row, 0)
            if symbol_item:
                symbol = symbol_item.text()
                self.symbol_selected.emit(symbol)
        except Exception as e:
            print(f"Error selecting symbol: {e}")

    def _filter_history(self, filter_text: str):
        """Filter history table based on date range."""
        self._refresh_history_table()

    def _clear_history(self):
        """Clear triggered alerts history."""
        reply = QMessageBox.question(
            self, "Clear History",
            "Are you sure you want to clear all triggered alert history?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.triggered_alerts.clear()
            self._save_alerts()
            self._refresh_history_table()
            self._update_status()

    def _refresh_active_table(self):
        """Refresh the active alerts table."""
        self.active_table.setRowCount(0)

        for alert in self.alerts:
            if alert.triggered:
                continue

            row = self.active_table.rowCount()
            self.active_table.insertRow(row)

            # Symbol (clickable)
            symbol_item = QTableWidgetItem(alert.symbol)
            symbol_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.active_table.setItem(row, 0, symbol_item)

            # Alert Price
            price_item = QTableWidgetItem(f"{alert.price:.2f}")
            price_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.active_table.setItem(row, 1, price_item)

            # Current Price (would be updated from market data)
            current_item = QTableWidgetItem("--")
            current_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.active_table.setItem(row, 2, current_item)

            # Condition
            condition_item = QTableWidgetItem(alert.condition.value)
            self.active_table.setItem(row, 3, condition_item)

            # Intent
            intent_item = QTableWidgetItem(alert.intent.value)
            self.active_table.setItem(row, 4, intent_item)

            # Note
            note_item = QTableWidgetItem(alert.note[:50] + "..." if len(alert.note) > 50 else alert.note)
            self.active_table.setItem(row, 5, note_item)

            # Expires
            expires_text = alert.expiry_time.strftime("%Y-%m-%d %H:%M")
            expires_item = QTableWidgetItem(expires_text)
            expires_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.active_table.setItem(row, 6, expires_item)

            # Actions (Delete button)
            delete_btn = QPushButton("Delete")
            delete_btn.setObjectName("deleteButton")
            delete_btn.clicked.connect(lambda checked, a=alert: self._delete_alert(a))
            self.active_table.setCellWidget(row, 7, delete_btn)

    def _refresh_history_table(self):
        """Refresh the history table with filtering."""
        self.history_table.setRowCount(0)

        # Apply date filter
        filter_text = self.history_filter.currentText()
        now = datetime.now()

        if filter_text == "Today":
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif filter_text == "This Week":
            start_date = now - timedelta(days=now.weekday())
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        elif filter_text == "This Month":
            start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:  # All Time
            start_date = datetime.min

        filtered_alerts = [
            alert for alert in self.triggered_alerts
            if alert.triggered_time and alert.triggered_time >= start_date
        ]

        # Sort by triggered time (newest first)
        filtered_alerts.sort(key=lambda x: x.triggered_time, reverse=True)

        for alert in filtered_alerts:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)

            # Triggered Time
            time_text = alert.triggered_time.strftime("%Y-%m-%d %H:%M:%S")
            time_item = QTableWidgetItem(time_text)
            time_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.history_table.setItem(row, 0, time_item)

            # Symbol (clickable)
            symbol_item = QTableWidgetItem(alert.symbol)
            symbol_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.history_table.setItem(row, 1, symbol_item)

            # Alert Price
            alert_price_item = QTableWidgetItem(f"{alert.price:.2f}")
            alert_price_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.history_table.setItem(row, 2, alert_price_item)

            # Trigger Price
            trigger_price_item = QTableWidgetItem(f"{alert.triggered_price:.2f}" if alert.triggered_price else "--")
            trigger_price_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.history_table.setItem(row, 3, trigger_price_item)

            # Condition
            condition_item = QTableWidgetItem(alert.condition.value)
            self.history_table.setItem(row, 4, condition_item)

            # Intent
            intent_item = QTableWidgetItem(alert.intent.value)
            self.history_table.setItem(row, 5, intent_item)

            # Note
            note_item = QTableWidgetItem(alert.note)
            self.history_table.setItem(row, 6, note_item)

    def _delete_alert(self, alert: Alert):
        """Delete an alert."""
        reply = QMessageBox.question(
            self, "Delete Alert",
            f"Delete alert for {alert.symbol} at {alert.price}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.alerts.remove(alert)
            self._save_alerts()
            self._refresh_active_table()
            self._update_status()
            self._update_alert_engine()

    def _update_status(self):
        """Update status bar labels."""
        active_count = len([alert for alert in self.alerts if not alert.triggered])

        today = datetime.now().date()
        triggered_today = len([
            alert for alert in self.triggered_alerts
            if alert.triggered_time and alert.triggered_time.date() == today
        ])

        total_alerts = len(self.alerts) + len(self.triggered_alerts)

        self.active_count_label.setText(f"Active: {active_count}")
        self.triggered_today_label.setText(f"Triggered Today: {triggered_today}")
        self.total_alerts_label.setText(f"Total Alerts: {total_alerts}")

        # Update API pressure indicator
        if active_count > 50:
            self.api_pressure_label.setText("API Load: High")
            self.api_pressure_label.setStyleSheet("color: #ff6b6b;")
        elif active_count > 20:
            self.api_pressure_label.setText("API Load: Medium")
            self.api_pressure_label.setStyleSheet("color: #ffa500;")
        else:
            self.api_pressure_label.setText("API Load: Low")
            self.api_pressure_label.setStyleSheet("color: #4CAF50;")

    def _update_check_interval(self, seconds: int):
        """Update alert checking interval."""
        self.alert_engine.set_check_interval(seconds)

    def _start_alert_engine(self):
        """Start the alert checking engine."""
        self._update_alert_engine()
        self.alert_engine.start()

    def _update_alert_engine(self):
        """Update alert engine with current alerts."""
        active_alerts = [alert for alert in self.alerts if not alert.triggered]
        self.alert_engine.update_alerts(active_alerts)

    def _on_alert_triggered(self, alert: Alert, trigger_price: float):
        """Handle alert being triggered."""
        # Move to triggered alerts
        self.triggered_alerts.append(alert)
        if alert in self.alerts:
            self.alerts.remove(alert)

        # Save and refresh
        self._save_alerts()
        self._refresh_active_table()
        self._refresh_history_table()
        self._update_status()

        # Play sound and show notification
        self.alert_sound_requested.emit()

        # Show notification
        QMessageBox.information(
            self, "Alert Triggered",
            f"Alert triggered for {alert.symbol}!\n"
            f"Price: {trigger_price:.2f}\n"
            f"Intent: {alert.intent.value}"
        )

    def _on_alert_expired(self, alert: Alert):
        """Handle alert expiring."""
        if alert in self.alerts:
            self.alerts.remove(alert)
            self._save_alerts()
            self._refresh_active_table()
            self._update_status()

    def update_market_data(self, symbol: str, price: float):
        """Update market data for alert checking."""
        self.alert_engine.update_market_data(symbol, price)

        # Update current price in active table
        for row in range(self.active_table.rowCount()):
            symbol_item = self.active_table.item(row, 0)
            if symbol_item and symbol_item.text() == symbol:
                current_item = self.active_table.item(row, 2)
                if current_item:
                    current_item.setText(f"{price:.2f}")
                break

    def update_positions(self, positions: Dict):
        """Update current positions for intelligent alert creation."""
        self.current_positions = positions

    def create_alert_from_chart(self, symbol: str, price: float):
        """Create alert from chart right-click context menu."""
        dialog = AlertCreationDialog(self, symbol, price, self.current_positions)
        dialog.alert_created.connect(self._add_alert)
        dialog.exec()

    def get_notification_counts(self) -> tuple[int, int]:
        """Get counts for notification badges."""
        active_count = len([alert for alert in self.alerts if not alert.triggered])
        today = datetime.now().date()
        triggered_today = len([
            alert for alert in self.triggered_alerts
            if alert.triggered_time and alert.triggered_time.date() == today
        ])
        return active_count, triggered_today

    def _load_alerts(self):
        """Load alerts from JSON files."""
        try:
            # Load active alerts
            if os.path.exists("user_data/alerts.json"):
                with open("user_data/alerts.json", 'r') as f:
                    alerts_data = json.load(f)
                    self.alerts = [Alert.from_dict(data) for data in alerts_data]

            # Load triggered alerts
            if os.path.exists("user_data/alert_history.json"):
                with open("user_data/alert_history.json", 'r') as f:
                    history_data = json.load(f)
                    self.triggered_alerts = [Alert.from_dict(data) for data in history_data]

        except Exception as e:
            print(f"Error loading alerts: {e}")
            self.alerts = []
            self.triggered_alerts = []

        self._refresh_active_table()
        self._refresh_history_table()
        self._update_status()

    def _save_alerts(self):
        """Save alerts to JSON files."""
        try:
            # Ensure directory exists
            os.makedirs("user_data", exist_ok=True)

            # Save active alerts
            with open("user_data/alerts.json", 'w') as f:
                alerts_data = [alert.to_dict() for alert in self.alerts]
                json.dump(alerts_data, f, indent=2)

            # Save triggered alerts
            with open("user_data/alert_history.json", 'w') as f:
                history_data = [alert.to_dict() for alert in self.triggered_alerts]
                json.dump(history_data, f, indent=2)

        except Exception as e:
            print(f"Error saving alerts: {e}")

    def closeEvent(self, event):
        """Handle window close event."""
        self.alert_engine.stop()
        super().closeEvent(event)

    def _apply_styles(self):
        """Apply comprehensive dark theme styles."""
        self.setStyleSheet("""
            /* Main Container */
            QWidget#mainContainer {
                background-color: #0a0a0a;
                border: 1px solid #202020;
                border-radius: 8px;
            }
            
            /* Dialog Title */
            QLabel#dialogTitle {
                color: #ffffff;
                font-size: 22px;
                font-weight: 600;
            }
            
            QLabel#subtitle {
                color: #8a8a9e;
                font-size: 14px;
            }
            
            QLabel#controlLabel {
                color: #e0e0e0;
                font-size: 12px;
            }
            
            QLabel#statusLabel {
                color: #a0c0ff;
                font-size: 12px;
                font-weight: 500;
                padding: 4px 8px;
                border-radius: 3px;
                background-color: #1a1a1a;
            }
            
            /* Close Button */
            QPushButton#closeButton {
                background-color: transparent;
                border: none;
                color: #8a8a9e;
                font-size: 18px;
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
            }
            QPushButton#closeButton:hover {
                color: #d63031;
                background-color: #2a2a2a;
            }
            
            /* Tab Widget */
            QTabWidget#alertTabs {
                background-color: #0d0d0d;
                border: none;
            }
            
            QTabWidget#alertTabs::pane {
                border: 1px solid #303030;
                border-radius: 6px;
                background-color: #0d0d0d;
            }
            
            QTabWidget#alertTabs::tab-bar {
                alignment: left;
            }
            
            QTabBar::tab {
                background-color: #1a1a1a;
                color: #8a8a9e;
                padding: 12px 24px;
                margin-right: 2px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 500;
                font-size: 13px;
            }
            
            QTabBar::tab:selected {
                background-color: #0d0d0d;
                color: #a0c0ff;
                border-bottom: 2px solid #6a9cff;
            }
            
            QTabBar::tab:hover:!selected {
                background-color: #2a2a2a;
                color: #e0e0e0;
            }
            
            /* Group Boxes */
            QGroupBox#alertGroup {
                font-weight: 600;
                font-size: 14px;
                color: #e0e0e0;
                border: 1px solid #303030;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 8px;
            }
            
            QGroupBox#alertGroup::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 8px 0 8px;
                background-color: #0d0d0d;
            }
            
            /* Input Controls */
            QLineEdit#alertInput, QSpinBox#intervalSpin {
                background-color: #1a1a1a;
                border: 1px solid #303030;
                border-radius: 4px;
                padding: 8px 12px;
                color: #e0e0e0;
                font-size: 12px;
                selection-background-color: #6a9cff;
            }
            
            QLineEdit#alertInput:focus, QSpinBox#intervalSpin:focus {
                border-color: #6a9cff;
                outline: none;
            }
            
            QComboBox#alertCombo {
                background-color: #1a1a1a;
                border: 1px solid #303030;
                border-radius: 4px;
                padding: 8px 12px;
                color: #e0e0e0;
                font-size: 12px;
                min-width: 100px;
            }
            
            QComboBox#alertCombo:focus {
                border-color: #6a9cff;
            }
            
            QComboBox#alertCombo::drop-down {
                border: none;
                width: 30px;
            }
            
            QComboBox#alertCombo::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid #8a8a9e;
                margin-right: 8px;
            }
            
            QComboBox#alertCombo QAbstractItemView {
                background-color: #1a1a1a;
                border: 1px solid #303030;
                selection-background-color: #6a9cff;
                color: #e0e0e0;
                outline: none;
            }
            
            /* Tables */
            QTableWidget#alertTable {
                background-color: #0d0d0d;
                border: 1px solid #303030;
                gridline-color: #202020;
                font-size: 12px;
                color: #e0e0e0;
                selection-background-color: rgba(106, 156, 255, 0.3);
                selection-color: #ffffff;
                border-radius: 6px;
                show-decoration-selected: 1;
            }
            
            QTableWidget#alertTable::item {
                padding: 8px;
                border-bottom: 1px solid #1a1a1a;
            }
            
            QTableWidget#alertTable::item:selected {
                background-color: rgba(106, 156, 255, 0.3);
                color: #ffffff;
            }
            
            QTableWidget#alertTable::item:alternate {
                background-color: #121212;
            }
            
            QHeaderView::section {
                background-color: #1a1a1a;
                color: #a0c0ff;
                padding: 10px 8px;
                border: none;
                border-bottom: 2px solid #303030;
                border-right: 1px solid #202020;
                font-weight: 600;
                font-size: 11px;
                text-transform: uppercase;
            }
            
            QHeaderView::section:last {
                border-right: none;
            }
            
            QHeaderView::section:hover {
                background-color: #2a2a2a;
            }
            
            /* Buttons */
            QPushButton#createButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-weight: 600;
                font-size: 12px;
            }
            
            QPushButton#createButton:hover {
                background-color: #45a049;
            }
            
            QPushButton#createButton:pressed {
                background-color: #3d8b40;
            }
            
            QPushButton#actionButton {
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 500;
                font-size: 12px;
            }
            
            QPushButton#actionButton:hover {
                background-color: #1976D2;
            }
            
            QPushButton#deleteButton {
                background-color: #f44336;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: 500;
                font-size: 11px;
            }
            
            QPushButton#deleteButton:hover {
                background-color: #d32f2f;
            }
            
            /* Scrollbars */
            QScrollBar:vertical {
                background-color: #1a1a1a;
                width: 12px;
                border-radius: 6px;
            }
            
            QScrollBar::handle:vertical {
                background-color: #404040;
                border-radius: 6px;
                min-height: 20px;
            }
            
            QScrollBar::handle:vertical:hover {
                background-color: #505050;
            }
            
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                background: none;
                border: none;
            }
            
            QScrollBar:horizontal {
                background-color: #1a1a1a;
                height: 12px;
                border-radius: 6px;
            }
            
            QScrollBar::handle:horizontal {
                background-color: #404040;
                border-radius: 6px;
                min-width: 20px;
            }
            
            QScrollBar::handle:horizontal:hover {
                background-color: #505050;
            }
            
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                background: none;
                border: none;
            }
        """)

    # --- Window Dragging ---
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_pos = None
        event.accept()


# Integration helper for the main trading application
class AlertManagerIntegration:
    """Helper class to integrate alert manager with main trading window."""

    def __init__(self, main_window):
        self.main_window = main_window
        self.alert_manager = None

    def setup_integration(self):
        """Setup alert manager integration."""
        # Connect chart right-click to alert creation
        if hasattr(self.main_window, 'candlestick_chart'):
            # This would be connected to chart's right-click context menu
            # self.main_window.candlestick_chart.create_alert_requested.connect(self.create_alert_from_chart)
            pass

        # Connect market data updates
        if hasattr(self.main_window, 'market_data_worker'):
            # self.main_window.market_data_worker.data_received.connect(self.update_market_data)
            pass

    def show_alert_manager(self):
        """Show the alert manager window."""
        if self.alert_manager is None:
            instrument_map = getattr(self.main_window, 'instrument_map', {})
            positions = self._get_current_positions()

            self.alert_manager = AdvancedAlertManager(
                self.main_window,
                instrument_map=instrument_map,
                positions=positions
            )

            # Connect signals
            self.alert_manager.symbol_selected.connect(self._switch_chart_symbol)
            self.alert_manager.alert_sound_requested.connect(self._play_alert_sound)

        self.alert_manager.show()
        self.alert_manager.raise_()
        self.alert_manager.activateWindow()

    def create_alert_from_chart(self, symbol: str, price: float):
        """Create alert from chart context menu."""
        if self.alert_manager is None:
            self.show_alert_manager()

        self.alert_manager.create_alert_from_chart(symbol, price)

    def update_market_data(self, ticks: List[Dict]):
        """Update market data for alert checking."""
        if self.alert_manager:
            for tick in ticks:
                symbol = self._get_symbol_from_token(tick.get('instrument_token'))
                if symbol and 'last_price' in tick:
                    self.alert_manager.update_market_data(symbol, tick['last_price'])

    def get_notification_counts(self) -> tuple[int, int]:
        """Get notification badge counts."""
        if self.alert_manager:
            return self.alert_manager.get_notification_counts()
        return 0, 0

    def _get_current_positions(self) -> Dict:
        """Get current positions from main window."""
        if hasattr(self.main_window, 'positions_table'):
            # Extract positions data
            return {}  # Would return actual position data
        return {}

    def _switch_chart_symbol(self, symbol: str):
        """Switch chart to selected symbol."""
        if hasattr(self.main_window, 'candlestick_chart'):
            self.main_window.candlestick_chart.on_search(symbol)

    def _play_alert_sound(self):
        """Play alert sound."""
        if hasattr(self.main_window, 'alert_sound'):
            self.main_window.alert_sound.play()

    def _get_symbol_from_token(self, token: int) -> Optional[str]:
        """Get symbol from instrument token."""
        instrument_map = getattr(self.main_window, 'instrument_map', {})
        for symbol, data in instrument_map.items():
            if data.get('instrument_token') == token:
                return symbol
        return None


# Add this new class to your alert_management_system.py file

class AlertSystemManager(QObject):  # MUST inherit from QObject
    """
    Acts as the main controller to integrate the AdvancedAlertManager
    with the main application window.
    """
    alert_sound_requested = Signal()

    def __init__(self, main_window):
        super().__init__()  # CRITICAL: Must call QObject.__init__()
        self.main_window = main_window
        self.instrument_map = getattr(self.main_window, 'instrument_map', {})

        try:
            # Instantiate the main alert dialog
            self.alert_manager_dialog = AdvancedAlertManager(
                parent=self.main_window,
                instrument_map=self.instrument_map,
                positions=self._get_current_positions()
            )

            # Connect internal signals
            self.alert_manager_dialog.symbol_selected.connect(self._switch_chart_symbol)
            self.alert_manager_dialog.alert_sound_requested.connect(self.alert_sound_requested.emit)

            logger.info("AlertSystemManager initialized successfully")

        except Exception as e:
            logger.error(f"Error initializing AlertSystemManager: {e}")
            self.alert_manager_dialog = None

    def show_alert_manager(self):
        """Shows the main alert manager dialog."""
        if self.alert_manager_dialog:
            try:
                self.alert_manager_dialog.show()
                self.alert_manager_dialog.raise_()
                self.alert_manager_dialog.activateWindow()
                logger.info("Alert manager dialog shown successfully")
            except Exception as e:
                logger.error(f"Error showing alert manager: {e}")
        else:
            logger.error("Alert manager dialog not available")

    def show_quick_alert_dialog(self):
        """Shows the quick alert dialog for the currently charted symbol."""
        if not self.alert_manager_dialog:
            logger.error("Alert manager dialog not available")
            return

        try:
            current_symbol = getattr(self.main_window.candlestick_chart, 'current_symbol', '')
            if not current_symbol:
                QMessageBox.information(self.main_window, "No Symbol", "Please select a symbol on the chart first.")
                return

            ltp = self.main_window._get_fresh_ltp(current_symbol)
            self.alert_manager_dialog.create_alert_from_chart(current_symbol, ltp)
            logger.info(f"Quick alert dialog shown for {current_symbol}")
        except Exception as e:
            logger.error(f"Error showing quick alert dialog: {e}")

    @Slot(str)
    def create_alert_from_chart(self, alert_data_json: str):
        """Creates an alert based on data from the chart's context menu."""
        if not self.alert_manager_dialog:
            logger.error("Alert manager dialog not available")
            return

        try:
            alert_data = json.loads(alert_data_json)
            symbol = alert_data.get('symbol')
            price = float(alert_data.get('price', 0))
            if symbol and price > 0:
                self.alert_manager_dialog.create_alert_from_chart(symbol, price)
                logger.info(f"Alert created from chart for {symbol} at {price}")
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.error(f"Failed to create alert from chart data: {e}")

    def update_market_data(self, ticks: List[Dict]):
        """Forwards live market data to the alert engine."""
        if not self.alert_manager_dialog or not self.instrument_map:
            return

        try:
            for tick in ticks:
                token = tick.get('instrument_token')
                ltp = tick.get('last_price')

                # This reverse lookup can be slow; consider a token-to-symbol map for optimization
                symbol = next((s for s, d in self.instrument_map.items()
                               if d.get('instrument_token') == token), None)

                if symbol and ltp is not None:
                    self.alert_manager_dialog.update_market_data(symbol, ltp)
        except Exception as e:
            logger.error(f"Error updating market data for alerts: {e}")

    def update_positions(self, positions: Dict):
        """Forwards position updates to the alert manager."""
        if self.alert_manager_dialog:
            try:
                self.alert_manager_dialog.update_positions(positions)
            except Exception as e:
                logger.error(f"Error updating positions for alerts: {e}")

    def set_instrument_map(self, instrument_map: Dict):
        """Updates the instrument map used for lookups."""
        self.instrument_map = instrument_map
        if self.alert_manager_dialog:
            self.alert_manager_dialog.instrument_map = instrument_map

    def get_notification_counts(self) -> tuple[int, int]:
        """Gets notification counts for the header toolbar badges."""
        if self.alert_manager_dialog:
            try:
                return self.alert_manager_dialog.get_notification_counts()
            except Exception as e:
                logger.error(f"Error getting notification counts: {e}")
        return 0, 0

    def get_active_alert_tokens(self) -> List[int]:
        """Gets all instrument tokens needed for active alerts."""
        if not self.alert_manager_dialog:
            return []

        try:
            tokens = []
            active_alerts = [a for a in self.alert_manager_dialog.alerts if not a.triggered]
            for alert in active_alerts:
                if alert.symbol in self.instrument_map:
                    tokens.append(self.instrument_map[alert.symbol]['instrument_token'])
            return tokens
        except Exception as e:
            logger.error(f"Error getting active alert tokens: {e}")
            return []

    def stop_engine(self):
        """Stops the background alert engine thread."""
        if self.alert_manager_dialog:
            try:
                self.alert_manager_dialog.alert_engine.stop()
                logger.info("Alert engine stopped successfully")
            except Exception as e:
                logger.error(f"Error stopping alert engine: {e}")

    def _get_current_positions(self) -> Dict:
        """Helper to get current positions from the position manager with error handling."""
        try:
            if hasattr(self.main_window, 'position_manager'):
                position_manager = self.main_window.position_manager

                # Check if the method exists
                if hasattr(position_manager, 'get_positions_dict'):
                    return position_manager.get_positions_dict()
                elif hasattr(position_manager, 'get_all_positions'):
                    # Fallback: convert Position objects to dict format
                    positions = position_manager.get_all_positions()
                    positions_dict = {}
                    for pos in positions:
                        positions_dict[pos.tradingsymbol] = {
                            'tradingsymbol': pos.tradingsymbol,
                            'quantity': pos.quantity,
                            'average_price': pos.average_price,
                            'ltp': pos.ltp,
                            'pnl': pos.pnl,
                            'product': pos.product,
                            'exchange': pos.exchange,
                            'transaction_type': 'BUY' if pos.quantity > 0 else 'SELL'
                        }
                    return positions_dict
                else:
                    logger.warning("Position manager has no compatible position method")
                    return {}
            else:
                logger.warning("Main window has no position_manager attribute")
                return {}
        except Exception as e:
            logger.error(f"Error getting current positions: {e}")
            return {}

    def _switch_chart_symbol(self, symbol: str):
        """Switches the main chart to the specified symbol."""
        try:
            if hasattr(self.main_window, 'candlestick_chart'):
                self.main_window.candlestick_chart.on_search(symbol)
                logger.info(f"Switched chart to symbol: {symbol}")
        except Exception as e:
            logger.error(f"Error switching chart symbol: {e}")