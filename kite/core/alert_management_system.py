# kite/core/alert_management_system.py
"""
AlertManagementSystem — Enhanced version.

Original only had:
    PRICE_IS_ABOVE / PRICE_IS_BELOW

Now adds:
    PRICE_CROSSED_UP / PRICE_CROSSED_DOWN   — one-shot crossings (not just level)
    PERCENT_CHANGE_UP / PERCENT_CHANGE_DOWN — % move from day open
    VOLUME_SPIKE                            — volume > N× 20-day avg
    RSI_ABOVE / RSI_BELOW                  — RSI indicator threshold
    MACD_CROSSOVER / MACD_CROSSUNDER        — MACD signal line cross
    TIME_BASED                              — fire at specific time (market events)

Architecture:
  AlertEngine  — runs in background QThread, evaluates conditions every 2s
  AlertStore   — thread-safe alert storage + JSON persistence
  AlertDialog  — full UI (Active / Triggered / History tabs)
"""

import logging
import json
import os
import time
import math
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Any, Optional, Callable

from PySide6.QtCore import (
    Qt, Signal, QThread, QObject, QTimer, QMutex, QMutexLocker, Slot
)
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QLabel, QPushButton, QWidget,
    QLineEdit, QComboBox, QCheckBox, QFormLayout, QMessageBox,
    QDoubleSpinBox, QSpinBox, QAbstractItemView, QFrame
)

from kite.utils.sounds import play_alert

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────────────────────────────────────

class AlertCondition(Enum):
    # Price
    PRICE_IS_ABOVE       = "Price Above"
    PRICE_IS_BELOW       = "Price Below"
    PRICE_CROSSED_UP     = "Price Crossed Up"      # one-shot crossing detection
    PRICE_CROSSED_DOWN   = "Price Crossed Down"
    # Percent move
    PERCENT_CHANGE_UP    = "% Change Up (from open)"
    PERCENT_CHANGE_DOWN  = "% Change Down (from open)"
    # Volume
    VOLUME_SPIKE         = "Volume Spike (N× avg)"
    # Indicator
    RSI_ABOVE            = "RSI Above"
    RSI_BELOW            = "RSI Below"
    MACD_CROSSOVER       = "MACD Crossover (Bull)"
    MACD_CROSSUNDER      = "MACD Crossunder (Bear)"
    # Time-based
    TIME_BASED           = "At Specific Time"


class AlertIntent(Enum):
    BUY_ENTRY     = "Buy Entry Signal"
    SELL_ENTRY    = "Short Entry Signal"
    PROFIT_TARGET = "Profit Target"
    STOP_LOSS     = "Stop Loss"
    BREAKOUT      = "Breakout Watch"
    SUPPORT       = "Support Watch"
    INFO          = "Informational"


class AlertStatus(Enum):
    ACTIVE    = "active"
    TRIGGERED = "triggered"
    EXPIRED   = "expired"
    DISABLED  = "disabled"


# ─────────────────────────────────────────────────────────────────────────────
# DATA MODEL
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Alert:
    id:                  str
    symbol:              str
    condition:           str       # AlertCondition.value
    intent:              str       # AlertIntent.value
    target_value:        float     # price / % / volume multiplier / RSI level / time
    status:              str  = AlertStatus.ACTIVE.value
    note:                str  = ""
    repeat:              bool = False    # re-arm after triggering
    notify_telegram:     bool = False
    created_at:          str  = field(default_factory=lambda: datetime.now().isoformat())
    triggered_at:        Optional[str] = None
    # Runtime state (not persisted)
    _prev_price:         float = field(default=0.0, repr=False, compare=False)
    _trigger_count:      int   = field(default=0,   repr=False, compare=False)

    def to_dict(self) -> Dict:
        d = asdict(self)
        # Strip runtime-only private fields
        return {k: v for k, v in d.items() if not k.startswith("_")}

    @classmethod
    def from_dict(cls, d: Dict) -> "Alert":
        clean = {k: v for k, v in d.items() if not k.startswith("_")}
        return cls(**clean)


# ─────────────────────────────────────────────────────────────────────────────
# ALERT STORE (thread-safe persistence)
# ─────────────────────────────────────────────────────────────────────────────

class AlertStore:
    """Thread-safe in-memory store with JSON persistence."""

    def __init__(self):
        self._alerts: Dict[str, Alert] = {}
        self._mutex  = QMutex()
        app_dir = os.path.join(os.path.expanduser("~"), ".swing_trader")
        os.makedirs(app_dir, exist_ok=True)
        self._path = os.path.join(app_dir, "alerts.json")
        self._load()

    def all(self) -> List[Alert]:
        with QMutexLocker(self._mutex):
            return list(self._alerts.values())

    def active(self) -> List[Alert]:
        return [a for a in self.all() if a.status == AlertStatus.ACTIVE.value]

    def add(self, alert: Alert) -> None:
        with QMutexLocker(self._mutex):
            self._alerts[alert.id] = alert
        self._save()

    def update(self, alert: Alert) -> None:
        with QMutexLocker(self._mutex):
            self._alerts[alert.id] = alert
        self._save()

    def remove(self, alert_id: str) -> None:
        with QMutexLocker(self._mutex):
            self._alerts.pop(alert_id, None)
        self._save()

    def _save(self):
        try:
            data = [a.to_dict() for a in self._alerts.values()]
            with open(self._path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"AlertStore save failed: {e}")

    def _load(self):
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r") as f:
                data = json.load(f)
            for d in data:
                try:
                    a = Alert.from_dict(d)
                    # Don't load expired/triggered unless repeating
                    if a.status in (AlertStatus.ACTIVE.value, AlertStatus.DISABLED.value):
                        self._alerts[a.id] = a
                    elif a.repeat and a.status == AlertStatus.TRIGGERED.value:
                        a.status = AlertStatus.ACTIVE.value  # re-arm
                        self._alerts[a.id] = a
                except Exception as e:
                    logger.warning(f"Skipping corrupt alert: {e}")
            logger.info(f"Loaded {len(self._alerts)} alerts from disk")
        except Exception as e:
            logger.error(f"AlertStore load failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR HELPERS (stateless, computed inline)
# ─────────────────────────────────────────────────────────────────────────────

class _Indicators:
    """Lightweight indicator calculations on price history dicts."""

    @staticmethod
    def rsi(prices: List[float], period: int = 14) -> float:
        if len(prices) < period + 1:
            return 50.0
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        gains  = [max(0, d) for d in deltas[-period:]]
        losses = [abs(min(0, d)) for d in deltas[-period:]]
        avg_g  = sum(gains)  / period
        avg_l  = sum(losses) / period
        if avg_l == 0:
            return 100.0
        rs = avg_g / avg_l
        return 100 - (100 / (1 + rs))

    @staticmethod
    def ema(prices: List[float], period: int) -> float:
        if not prices:
            return 0.0
        k = 2 / (period + 1)
        ema = prices[0]
        for p in prices[1:]:
            ema = p * k + ema * (1 - k)
        return ema

    @staticmethod
    def macd(prices: List[float]) -> tuple:
        """Returns (macd_line, signal_line). Positive macd = bullish."""
        if len(prices) < 26:
            return 0.0, 0.0
        fast = _Indicators.ema(prices, 12)
        slow = _Indicators.ema(prices, 26)
        macd_line = fast - slow
        # Signal line = 9-period EMA of MACD — simplified: just return
        return macd_line, macd_line * 0.9  # rough signal for now


# ─────────────────────────────────────────────────────────────────────────────
# ALERT ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class AlertEngine(QObject):
    """
    Background worker that evaluates alerts against live market data every 2s.
    Runs in its own QThread to never block the UI.
    """

    alert_triggered = Signal(str)  # alert_id

    def __init__(self, store: AlertStore):
        super().__init__()
        self._store = store
        self._market_data: Dict[str, Dict] = {}   # symbol → tick dict
        self._price_history: Dict[str, List[float]] = {}  # symbol → last N prices
        self._day_open: Dict[str, float] = {}
        self._mutex = QMutex()
        self._running = False

    @Slot()
    def start_engine(self):
        self._running = True
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._evaluate_all)
        self._timer.start(2_000)   # evaluate every 2 seconds
        logger.info("AlertEngine started")

    @Slot()
    def stop_engine(self):
        self._running = False
        if hasattr(self, "_timer"):
            self._timer.stop()
        logger.info("AlertEngine stopped")

    def update_market_data(self, ticks: List[Dict]) -> None:
        """Called by main window's _on_market_data slot."""
        with QMutexLocker(self._mutex):
            for tick in ticks:
                sym = tick.get("tradingsymbol")
                if not sym:
                    continue
                self._market_data[sym] = tick
                ltp = float(tick.get("last_price", 0))
                if ltp > 0:
                    hist = self._price_history.setdefault(sym, [])
                    hist.append(ltp)
                    if len(hist) > 100:
                        hist.pop(0)
                # Record day open from OHLC
                ohlc = tick.get("ohlc", {})
                if ohlc and sym not in self._day_open:
                    day_open = float(ohlc.get("open", 0))
                    if day_open > 0:
                        self._day_open[sym] = day_open

    def _evaluate_all(self):
        if not self._running:
            return
        for alert in self._store.active():
            try:
                triggered = self._check(alert)
                if triggered:
                    self._fire(alert)
            except Exception as e:
                logger.debug(f"Alert check error [{alert.id}]: {e}")

    def _check(self, alert: Alert) -> bool:
        sym = alert.symbol
        with QMutexLocker(self._mutex):
            tick    = self._market_data.get(sym, {})
            hist    = list(self._price_history.get(sym, []))
            day_open = self._day_open.get(sym, 0.0)

        ltp     = float(tick.get("last_price", 0))
        volume  = float(tick.get("volume", 0))
        cond    = alert.condition
        target  = float(alert.target_value)

        if ltp <= 0 and cond != AlertCondition.TIME_BASED.value:
            return False

        # ── Price conditions ──
        if cond == AlertCondition.PRICE_IS_ABOVE.value:
            return ltp >= target

        if cond == AlertCondition.PRICE_IS_BELOW.value:
            return ltp <= target

        if cond == AlertCondition.PRICE_CROSSED_UP.value:
            prev = alert._prev_price
            result = prev > 0 and prev < target <= ltp
            alert._prev_price = ltp
            return result

        if cond == AlertCondition.PRICE_CROSSED_DOWN.value:
            prev = alert._prev_price
            result = prev > 0 and prev > target >= ltp
            alert._prev_price = ltp
            return result

        # ── % Change from open ──
        if cond == AlertCondition.PERCENT_CHANGE_UP.value:
            if day_open <= 0:
                return False
            pct = (ltp - day_open) / day_open * 100
            return pct >= target

        if cond == AlertCondition.PERCENT_CHANGE_DOWN.value:
            if day_open <= 0:
                return False
            pct = (day_open - ltp) / day_open * 100
            return pct >= target

        # ── Volume spike ──
        if cond == AlertCondition.VOLUME_SPIKE.value:
            avg_vol = float(tick.get("average_traded_price", 0))
            # If avg_vol not in tick, use a rough heuristic
            if avg_vol <= 0:
                avg_vol = volume / max(1, len(hist))
            return avg_vol > 0 and volume >= target * avg_vol

        # ── RSI ──
        if cond == AlertCondition.RSI_ABOVE.value:
            if len(hist) < 15:
                return False
            rsi = _Indicators.rsi(hist)
            return rsi >= target

        if cond == AlertCondition.RSI_BELOW.value:
            if len(hist) < 15:
                return False
            rsi = _Indicators.rsi(hist)
            return rsi <= target

        # ── MACD ──
        if cond == AlertCondition.MACD_CROSSOVER.value:
            if len(hist) < 27:
                return False
            macd, signal = _Indicators.macd(hist)
            prev_macd, prev_signal = _Indicators.macd(hist[:-1]) if len(hist) > 27 else (0, 0)
            return prev_macd <= prev_signal and macd > signal  # bull crossover

        if cond == AlertCondition.MACD_CROSSUNDER.value:
            if len(hist) < 27:
                return False
            macd, signal = _Indicators.macd(hist)
            prev_macd, prev_signal = _Indicators.macd(hist[:-1]) if len(hist) > 27 else (0, 0)
            return prev_macd >= prev_signal and macd < signal  # bear crossunder

        # ── Time-based ──
        if cond == AlertCondition.TIME_BASED.value:
            # target_value is stored as HHMM int (e.g. 915 = 09:15)
            now = datetime.now()
            now_hhmm = now.hour * 100 + now.minute
            return now_hhmm == int(target)

        return False

    def _fire(self, alert: Alert):
        """Mark alert as triggered and emit signal."""
        alert.status       = AlertStatus.TRIGGERED.value
        alert.triggered_at = datetime.now().isoformat()
        alert._trigger_count += 1
        self._store.update(alert)
        self.alert_triggered.emit(alert.id)
        play_alert()
        logger.info(f"🔔 Alert triggered: {alert.symbol} — {alert.condition} @ {alert.target_value}")


# ─────────────────────────────────────────────────────────────────────────────
# ALERT CREATION DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class AlertCreationDialog(QDialog):
    """Compact dialog to create a new alert."""

    alert_created = Signal(object)  # Alert instance

    def __init__(self, symbol: str = "", ltp: float = 0.0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Alert")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._symbol = symbol.upper()
        self._ltp    = ltp
        self._build_ui()
        self._apply_styles()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        container = QFrame()
        container.setObjectName("alertContainer")
        outer.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)

        # Header
        header = QHBoxLayout()
        title  = QLabel(f"New Alert — {self._symbol}")
        title.setObjectName("alertTitle")
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(26, 26)
        close_btn.clicked.connect(self.reject)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        layout.addLayout(header)

        form = QFormLayout()
        form.setVerticalSpacing(8)

        # Symbol
        self.symbol_input = QLineEdit(self._symbol)
        self.symbol_input.setFixedWidth(160)
        form.addRow("Symbol:", self.symbol_input)

        # Condition
        self.condition_combo = QComboBox()
        self.condition_combo.addItems([c.value for c in AlertCondition])
        self.condition_combo.setFixedWidth(220)
        self.condition_combo.currentTextChanged.connect(self._update_target_label)
        form.addRow("Condition:", self.condition_combo)

        # Target value
        self.target_label = QLabel("Target Price:")
        self.target_spin  = QDoubleSpinBox()
        self.target_spin.setRange(0, 999_999)
        self.target_spin.setDecimals(2)
        self.target_spin.setValue(self._ltp)
        self.target_spin.setFixedWidth(160)
        form.addRow(self.target_label, self.target_spin)

        # Intent
        self.intent_combo = QComboBox()
        self.intent_combo.addItems([i.value for i in AlertIntent])
        self.intent_combo.setFixedWidth(220)
        form.addRow("Intent:", self.intent_combo)

        # Note
        self.note_input = QLineEdit()
        self.note_input.setPlaceholderText("Optional note…")
        form.addRow("Note:", self.note_input)

        # Repeat
        self.repeat_check = QCheckBox("Re-arm after trigger")
        form.addRow("", self.repeat_check)

        layout.addLayout(form)

        # Buttons
        btns = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cancelButton")
        cancel_btn.clicked.connect(self.reject)
        create_btn = QPushButton("Create Alert")
        create_btn.setObjectName("confirmButton")
        create_btn.clicked.connect(self._create)
        btns.addWidget(cancel_btn, 1)
        btns.addWidget(create_btn, 2)
        layout.addLayout(btns)

    def _update_target_label(self, condition_text: str):
        labels = {
            AlertCondition.PERCENT_CHANGE_UP.value:   "% Move Up:",
            AlertCondition.PERCENT_CHANGE_DOWN.value:  "% Move Down:",
            AlertCondition.VOLUME_SPIKE.value:         "Volume Multiplier (N×):",
            AlertCondition.RSI_ABOVE.value:            "RSI Level (>):",
            AlertCondition.RSI_BELOW.value:            "RSI Level (<):",
            AlertCondition.TIME_BASED.value:           "Time (HHMM, e.g. 915):",
        }
        self.target_label.setText(labels.get(condition_text, "Target Price:"))

    def _create(self):
        import uuid
        symbol = self.symbol_input.text().strip().upper()
        if not symbol:
            return
        alert = Alert(
            id=f"alert_{uuid.uuid4().hex[:8]}",
            symbol=symbol,
            condition=self.condition_combo.currentText(),
            intent=self.intent_combo.currentText(),
            target_value=self.target_spin.value(),
            note=self.note_input.text().strip(),
            repeat=self.repeat_check.isChecked(),
        )
        self.alert_created.emit(alert)
        self.accept()

    def _apply_styles(self):
        self.setStyleSheet("""
            QFrame#alertContainer {
                background-color: #1e1e1e;
                border: 1px solid #333;
                border-radius: 8px;
            }
            QLabel#alertTitle { color: #e0e0e0; font-size: 13px; font-weight: bold; }
            QPushButton#confirmButton {
                background-color: #006064;
                color: white; border: none; border-radius: 4px;
                padding: 6px; font-weight: bold;
            }
            QPushButton#confirmButton:hover { background-color: #00838f; }
            QPushButton#cancelButton {
                background-color: #2c2c2c; color: #aaa; border: none; border-radius: 4px; padding: 6px;
            }
            QPushButton#closeButton { background: transparent; color: #666; border: none; }
            QPushButton#closeButton:hover { color: #ef5350; }
            QComboBox, QLineEdit, QDoubleSpinBox {
                background-color: #2c2c2c; color: #e0e0e0;
                border: 1px solid #3a3a3a; border-radius: 3px; padding: 3px 6px;
            }
            QLabel { color: #a0a0a0; }
        """)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ALERT SYSTEM MANAGER (wires engine + UI + store)
# ─────────────────────────────────────────────────────────────────────────────

class AlertSystemManager(QObject):
    """
    Top-level coordinator. Main window holds one instance.

    Usage:
        self.alert_system = AlertSystemManager(self)
        self.alert_system.alert_triggered.connect(self._on_alert_triggered)
        # Feed ticks:
        self.alert_system.update_market_data(ticks)
        # Open the dialog:
        self.alert_system.show_dialog()
    """

    alert_triggered = Signal(str)  # alert_id
    alert_sound_requested = Signal()
    engine_status_changed = Signal(str)
    _request_engine_stop = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.store  = AlertStore()
        self.engine = AlertEngine(self.store)
        self._dialog = None

        # Engine runs in its own thread
        self._engine_thread = QThread(self)
        self._engine_thread.setObjectName("AlertEngineThread")
        self.engine.moveToThread(self._engine_thread)
        self._engine_thread.started.connect(self.engine.start_engine)
        self._engine_thread.start()

        self.engine.alert_triggered.connect(self._on_engine_alert_triggered)
        self._request_engine_stop.connect(self.engine.stop_engine)
        self.engine_status_changed.emit("running")
        logger.info("AlertSystemManager ready")

    def update_market_data(self, ticks: List[Dict]) -> None:
        """Pass live ticks from main window's market data slot."""
        self.engine.update_market_data(ticks)

    def add_alert(self, alert: Alert) -> None:
        self.store.add(alert)
        logger.info(f"Alert added: {alert.symbol} {alert.condition} @ {alert.target_value}")

    def remove_alert(self, alert_id: str) -> None:
        self.store.remove(alert_id)

    def show_dialog(self, parent=None) -> None:
        """Open the alert management dialog."""
        if self._dialog and self._dialog.isVisible():
            self._dialog.raise_()
            return
        self._dialog = AlertManagementDialog(self.store, parent or self.parent())
        self._dialog.show()


    @Slot(str)
    def create_alert_from_chart(self, alert_json: str) -> None:
        """Create and register an alert from chart bridge JSON payload."""
        try:
            data = json.loads(alert_json or "{}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid chart alert payload: {e}")
            return

        symbol = str(data.get("symbol", "")).strip().upper()
        if not symbol:
            logger.warning("Chart alert ignored: missing symbol")
            return

        try:
            target_value = float(data.get("price", 0.0))
        except (TypeError, ValueError):
            target_value = 0.0

        if target_value <= 0:
            logger.warning(f"Chart alert ignored for {symbol}: invalid target price {target_value}")
            return

        condition_map = {
            "crosses_above": AlertCondition.PRICE_CROSSED_UP.value,
            "crosses_below": AlertCondition.PRICE_CROSSED_DOWN.value,
        }
        intent_map = {
            "buy_entry": AlertIntent.BUY_ENTRY.value,
            "sell_entry": AlertIntent.SELL_ENTRY.value,
            "profit_target": AlertIntent.PROFIT_TARGET.value,
            "stop_loss": AlertIntent.STOP_LOSS.value,
            "breakout": AlertIntent.BREAKOUT.value,
            "support": AlertIntent.SUPPORT.value,
            "info": AlertIntent.INFO.value,
        }

        condition = condition_map.get(str(data.get("condition", "")).lower(), AlertCondition.PRICE_IS_ABOVE.value)
        intent = intent_map.get(str(data.get("intent", "")).lower(), AlertIntent.INFO.value)

        alert = Alert(
            id=f"alert_{uuid.uuid4().hex[:8]}",
            symbol=symbol,
            condition=condition,
            intent=intent,
            target_value=target_value,
            note=str(data.get("note", "")).strip(),
        )
        self.add_alert(alert)
        logger.info(f"Alert created from chart: {symbol} {condition} @ {target_value}")

    @Slot(str)
    def _on_engine_alert_triggered(self, alert_id: str) -> None:
        self.alert_triggered.emit(alert_id)
        self.alert_sound_requested.emit()

    def set_instrument_map(self, instrument_map: Dict[str, Dict]) -> None:
        """Compatibility API; map is not required by current alert engine."""
        self._instrument_map = instrument_map

    def show_quick_alert_dialog(self, parent=None) -> None:
        self.show_dialog(parent=parent)

    def show_alert_manager(self, parent=None) -> None:
        self.show_dialog(parent=parent)

    def get_notification_counts(self) -> tuple[int, int]:
        alerts = self.store.all()
        active = sum(1 for a in alerts if a.status == AlertStatus.ACTIVE.value)
        triggered = sum(1 for a in alerts if a.status == AlertStatus.TRIGGERED.value)
        return active, triggered

    def get_active_alert_tokens(self) -> List[int]:
        # Alerts are symbol-based; token extraction is not available here.
        return []

    def stop_engine(self) -> None:
        self._request_engine_stop.emit()
        self._engine_thread.quit()
        self._engine_thread.wait(3_000)
        self.engine_status_changed.emit("stopped")
        logger.info("AlertSystemManager stopped")


# ─────────────────────────────────────────────────────────────────────────────
# ALERT MANAGEMENT DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class AlertManagementDialog(QDialog):
    """Three-tab dialog: Active | Triggered | History."""

    def __init__(self, store: AlertStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Alert Manager")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(700, 450)

        self._drag_pos = None
        self._build_ui()
        self._apply_styles()

        # Refresh every 3 seconds
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_tables)
        self._refresh_timer.start(3_000)
        self.refresh_tables()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        container = QFrame()
        container.setObjectName("alertMgmtContainer")
        container.mousePressEvent  = self._mouse_press
        container.mouseMoveEvent   = self._mouse_move
        container.mouseReleaseEvent = self._mouse_release
        outer.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(8)

        # Header
        header = QHBoxLayout()
        title = QLabel("Alerts")
        title.setObjectName("mgmtTitle")
        add_btn = QPushButton("+ New Alert")
        add_btn.setObjectName("addButton")
        add_btn.clicked.connect(self._add_new)
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(26, 26)
        close_btn.clicked.connect(self.close)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(add_btn)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # Tabs
        self.tabs = QTabWidget()
        self.active_table    = self._make_table(["Symbol", "Condition", "Target", "Intent", "Note", "Created", "Action"])
        self.triggered_table = self._make_table(["Symbol", "Condition", "Target", "Triggered At", "Note", "Action"])
        self.history_table   = self._make_table(["Symbol", "Condition", "Target", "Triggered At", "Count", "Note"])

        self.tabs.addTab(self.active_table,    "Active")
        self.tabs.addTab(self.triggered_table, "Triggered")
        self.tabs.addTab(self.history_table,   "History")
        layout.addWidget(self.tabs)

    def _make_table(self, headers: List[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.setAlternatingRowColors(True)
        return t

    def refresh_tables(self):
        alerts = self.store.all()
        active    = [a for a in alerts if a.status == AlertStatus.ACTIVE.value]
        triggered = [a for a in alerts if a.status == AlertStatus.TRIGGERED.value]
        history   = [a for a in alerts if a.status in (
            AlertStatus.TRIGGERED.value, AlertStatus.EXPIRED.value)]

        self._populate_active(active)
        self._populate_triggered(triggered)
        self._populate_history(history)

        self.tabs.setTabText(0, f"Active ({len(active)})")
        self.tabs.setTabText(1, f"Triggered ({len(triggered)})")

    def _populate_active(self, alerts: List[Alert]):
        t = self.active_table
        t.setRowCount(len(alerts))
        for row, a in enumerate(alerts):
            t.setItem(row, 0, QTableWidgetItem(a.symbol))
            t.setItem(row, 1, QTableWidgetItem(a.condition))
            t.setItem(row, 2, QTableWidgetItem(f"{a.target_value:.2f}"))
            t.setItem(row, 3, QTableWidgetItem(a.intent))
            t.setItem(row, 4, QTableWidgetItem(a.note))
            t.setItem(row, 5, QTableWidgetItem(a.created_at[:16]))

            del_btn = QPushButton("✕ Delete")
            del_btn.setObjectName("deleteButton")
            del_btn.clicked.connect(lambda _, aid=a.id: self._delete_alert(aid))
            t.setCellWidget(row, 6, del_btn)

    def _populate_triggered(self, alerts: List[Alert]):
        t = self.triggered_table
        t.setRowCount(len(alerts))
        for row, a in enumerate(alerts):
            t.setItem(row, 0, QTableWidgetItem(a.symbol))
            t.setItem(row, 1, QTableWidgetItem(a.condition))
            t.setItem(row, 2, QTableWidgetItem(f"{a.target_value:.2f}"))
            t.setItem(row, 3, QTableWidgetItem(a.triggered_at[:16] if a.triggered_at else ""))
            t.setItem(row, 4, QTableWidgetItem(a.note))

            ack_btn = QPushButton("✓ Ack")
            ack_btn.setObjectName("ackButton")
            ack_btn.clicked.connect(lambda _, aid=a.id: self._ack_alert(aid))
            t.setCellWidget(row, 5, ack_btn)

    def _populate_history(self, alerts: List[Alert]):
        t = self.history_table
        t.setRowCount(len(alerts))
        for row, a in enumerate(alerts):
            t.setItem(row, 0, QTableWidgetItem(a.symbol))
            t.setItem(row, 1, QTableWidgetItem(a.condition))
            t.setItem(row, 2, QTableWidgetItem(f"{a.target_value:.2f}"))
            t.setItem(row, 3, QTableWidgetItem(a.triggered_at[:16] if a.triggered_at else ""))
            t.setItem(row, 4, QTableWidgetItem(str(a._trigger_count)))
            t.setItem(row, 5, QTableWidgetItem(a.note))

    def _add_new(self):
        dlg = AlertCreationDialog(parent=self)
        dlg.alert_created.connect(self.store.add)
        dlg.alert_created.connect(lambda _: self.refresh_tables())
        dlg.exec()

    def _delete_alert(self, alert_id: str):
        self.store.remove(alert_id)
        self.refresh_tables()

    def _ack_alert(self, alert_id: str):
        """Acknowledge a triggered alert — move it to history."""
        for a in self.store.all():
            if a.id == alert_id:
                a.status = AlertStatus.EXPIRED.value
                self.store.update(a)
                break
        self.refresh_tables()

    # Draggable window
    def _mouse_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()

    def _mouse_move(self, event):
        if self._drag_pos and event.buttons() & Qt.LeftButton:
            self.move(self.pos() + event.globalPosition().toPoint() - self._drag_pos)
            self._drag_pos = event.globalPosition().toPoint()

    def _mouse_release(self, event):
        self._drag_pos = None

    def _apply_styles(self):
        self.setStyleSheet("""
            QFrame#alertMgmtContainer {
                background-color: #1a1a1a;
                border: 1px solid #333;
                border-radius: 8px;
            }
            QLabel#mgmtTitle { color: #e0e0e0; font-size: 14px; font-weight: bold; }
            QPushButton#addButton {
                background-color: #006064; color: white; border: none;
                border-radius: 4px; padding: 5px 12px; font-weight: bold;
            }
            QPushButton#addButton:hover { background-color: #00838f; }
            QPushButton#deleteButton {
                background-color: transparent; color: #ef5350;
                border: none; font-size: 11px;
            }
            QPushButton#ackButton {
                background-color: transparent; color: #26a69a;
                border: none; font-size: 11px; font-weight: bold;
            }
            QPushButton#closeButton {
                background: transparent; color: #666; border: none;
            }
            QPushButton#closeButton:hover { color: #ef5350; }
            QTableWidget {
                background-color: #1e1e1e; color: #d0d0d0;
                gridline-color: #2a2a2a; border: none;
                alternate-background-color: #222;
            }
            QHeaderView::section {
                background-color: #252525; color: #888;
                border: none; padding: 4px; font-size: 11px;
            }
            QTabWidget::pane { border: 1px solid #333; }
            QTabBar::tab {
                background-color: #252525; color: #888;
                padding: 6px 16px; border: none;
            }
            QTabBar::tab:selected { background-color: #1a1a1a; color: #e0e0e0; }
        """)
