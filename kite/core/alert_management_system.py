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
import sqlite3
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
    _last_state:         bool  = field(default=False, repr=False, compare=False)

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
    """Thread-safe in-memory store with SQLite persistence and JSON migration."""

    def __init__(self):
        self._alerts: Dict[str, Alert] = {}
        self._mutex  = QMutex()
        app_dir = os.path.join(os.path.expanduser("~"), ".qullamaggie")
        os.makedirs(app_dir, exist_ok=True)
        self._path = os.path.join(app_dir, "alerts.db")
        self._legacy_json_path = os.path.join(app_dir, "alerts.json")
        self._init_db()
        self._migrate_from_legacy_json_if_needed()
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
            payload = json.dumps([a.to_dict() for a in self._alerts.values()])
            with sqlite3.connect(self._path, timeout=5.0) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("UPDATE alerts_store SET payload_json = ?, updated_at = ? WHERE id = 1",
                             (payload, datetime.now().isoformat()))
                conn.commit()
        except Exception as e:
            logger.error(f"AlertStore save failed: {e}")

    def _load(self):
        try:
            with sqlite3.connect(self._path, timeout=5.0) as conn:
                row = conn.execute("SELECT payload_json FROM alerts_store WHERE id = 1").fetchone()
            if not row or not row[0]:
                return
            data = json.loads(row[0])
            for d in data:
                try:
                    a = Alert.from_dict(d)
                    if a.repeat and a.status == AlertStatus.TRIGGERED.value:
                        a.status = AlertStatus.ACTIVE.value  # re-arm
                        self._alerts[a.id] = a
                    # Load all user-visible states so Triggered/History survive app restarts.
                    elif a.status in (
                        AlertStatus.ACTIVE.value,
                        AlertStatus.DISABLED.value,
                        AlertStatus.TRIGGERED.value,
                        AlertStatus.EXPIRED.value,
                    ):
                        self._alerts[a.id] = a
                except Exception as e:
                    logger.warning(f"Skipping corrupt alert: {e}")
            logger.info(f"Loaded {len(self._alerts)} alerts from disk")
        except Exception as e:
            logger.error(f"AlertStore load failed: {e}")

    def _init_db(self) -> None:
        try:
            with sqlite3.connect(self._path, timeout=5.0) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS alerts_store (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        payload_json TEXT NOT NULL DEFAULT '[]',
                        updated_at TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO alerts_store (id, payload_json, updated_at)
                    VALUES (1, '[]', ?)
                    ON CONFLICT(id) DO NOTHING
                    """,
                    (datetime.now().isoformat(),),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"AlertStore DB init failed: {e}")

    def _migrate_from_legacy_json_if_needed(self) -> None:
        if not os.path.exists(self._legacy_json_path):
            return
        try:
            with sqlite3.connect(self._path, timeout=5.0) as conn:
                row = conn.execute("SELECT payload_json FROM alerts_store WHERE id = 1").fetchone()
                has_db_data = bool(row and row[0] and row[0] != "[]")
            if has_db_data:
                return

            with open(self._legacy_json_path, "r") as f:
                legacy_data = json.load(f)
            if not isinstance(legacy_data, list):
                logger.warning("Legacy alerts.json format invalid; skipping migration")
                return

            payload = json.dumps(legacy_data)
            with sqlite3.connect(self._path, timeout=5.0) as conn:
                conn.execute(
                    "UPDATE alerts_store SET payload_json = ?, updated_at = ? WHERE id = 1",
                    (payload, datetime.now().isoformat()),
                )
                conn.commit()
            logger.info(f"Migrated {len(legacy_data)} alert(s) from alerts.json to alerts.db")
        except Exception as e:
            logger.error(f"AlertStore legacy migration failed: {e}")


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
        self._market_data_by_token: Dict[int, Dict] = {}  # token → tick dict
        self._token_to_symbol: Dict[int, str] = {}  # token → symbol
        self._price_history: Dict[str, List[float]] = {}  # symbol → last N prices
        self._volume_history: Dict[str, List[float]] = {}  # symbol → last N volume prints
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

    @Slot(list)
    def update_market_data(self, ticks: List[Dict]) -> None:
        """Update internal tick cache using symbol and/or instrument token."""
        with QMutexLocker(self._mutex):
            for tick in ticks:
                raw_token = tick.get("instrument_token")
                token = int(raw_token) if raw_token is not None else 0
                sym = str(tick.get("tradingsymbol") or "").strip().upper()

                # Resolve symbol from existing token map when tick lacks tradingsymbol.
                if not sym and token > 0:
                    sym = self._token_to_symbol.get(token, "")

                # Keep token↔symbol map up to date.
                if sym and token > 0:
                    self._token_to_symbol[token] = sym

                if not sym:
                    continue

                if token > 0:
                    self._market_data_by_token[token] = tick
                self._market_data[sym] = tick
                ltp = float(tick.get("last_price", 0))
                if ltp > 0:
                    hist = self._price_history.setdefault(sym, [])
                    hist.append(ltp)
                    if len(hist) > 100:
                        hist.pop(0)

                vol = float(tick.get("volume", 0))
                if vol > 0:
                    vhist = self._volume_history.setdefault(sym, [])
                    vhist.append(vol)
                    if len(vhist) > 100:
                        vhist.pop(0)
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
                if alert.repeat:
                    if triggered and not alert._last_state:
                        self._fire(alert)
                    alert._last_state = triggered
                    self._store.update(alert)
                elif triggered:
                    self._fire(alert)
            except Exception as e:
                logger.debug(f"Alert check error [{alert.id}]: {e}")

    def _check(self, alert: Alert) -> bool:
        sym = alert.symbol
        with QMutexLocker(self._mutex):
            tick    = self._market_data.get(sym, {})
            hist    = list(self._price_history.get(sym, []))
            vol_hist = list(self._volume_history.get(sym, []))
            day_open = self._day_open.get(sym, 0.0)

        ltp     = float(tick.get("last_price", 0))
        volume  = float(tick.get("volume", 0))
        cond    = alert.condition
        target  = float(alert.target_value)

        if ltp <= 0 and cond != AlertCondition.TIME_BASED.value:
            return False

        # ── Price conditions ──
        if cond == AlertCondition.PRICE_IS_ABOVE.value:
            # Level-based alert: trigger as soon as LTP is at/above target.
            # This does NOT wait for candle/day close.
            return ltp >= target

        if cond == AlertCondition.PRICE_IS_BELOW.value:
            # Level-based alert: trigger as soon as LTP is at/below target.
            # This does NOT wait for candle/day close.
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
            avg_vol = 0.0
            if len(vol_hist) >= 5:
                avg_vol = sum(vol_hist[:-1]) / max(1, len(vol_hist) - 1)
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
        if not alert.repeat:
            alert.status = AlertStatus.TRIGGERED.value
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
            * {
                font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
            }
            QFrame#alertContainer {
                background-color: #12141A;
                border: 1px solid #222630;
                border-radius: 2px;
            }
            QLabel {
                color: #A0A6B5;
                font-size: 12px;
                font-weight: 500;
            }
            QLabel#alertTitle {
                color: #FFFFFF;
                font-size: 14px;
                font-weight: 600;
                letter-spacing: 0.5px;
            }
            /* Flat Inputs & Dropdowns */
            QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {
                background-color: #1B1E26;
                color: #FFFFFF;
                border: 1px solid #2A2F3A;
                border-radius: 2px;
                padding: 5px 8px;
                font-size: 12px;
            }
            QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {
                border: 1px solid #00E676;
                background-color: #12141A;
            }
            QComboBox QAbstractItemView {
                background-color: #1B1E26;
                color: #FFFFFF;
                selection-background-color: #2A2F3A;
                selection-color: #FFFFFF;
                border: 1px solid #2A2F3A;
                outline: 0;
            }
            QDoubleSpinBox QLineEdit {
                background-color: #1B1E26;
                color: #FFFFFF;
                selection-background-color: #2A2F3A;
                selection-color: #FFFFFF;
                border: none;
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
            /* Action Buttons */
            QPushButton {
                font-weight: 600;
                font-size: 12px;
                border-radius: 2px;
            }
            QPushButton#confirmButton {
                background-color: rgba(0, 230, 118, 0.1);
                color: #00E676;
                border: 1px solid transparent;
                padding: 6px 12px;
            }
            QPushButton#confirmButton:hover {
                background-color: rgba(0, 230, 118, 0.15);
                border: 1px solid rgba(0, 230, 118, 0.3);
            }
            QPushButton#cancelButton {
                background-color: transparent;
                color: #7B8496;
                border: 1px solid #2A2F3A;
                padding: 6px 12px;
            }
            QPushButton#cancelButton:hover {
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
            QCheckBox {
                color: #A0A6B5;
                font-size: 12px;
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
        """)


class AlertSystemManager(QObject):
    """
    Top-level coordinator.  Main window holds one instance.

    Usage:
        self.alert_system = AlertSystemManager(self)   # parent = main_window
        self.alert_system.alert_triggered.connect(self._on_alert_triggered)
        # Feed ticks:
        self.alert_system.update_market_data(ticks)
        # Open the dialog:
        self.alert_system.show_dialog()
    """

    alert_triggered      = Signal(str)   # alert_id
    alert_sound_requested = Signal()
    engine_status_changed = Signal(str)
    _request_engine_stop  = Signal()
    _market_data_received = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.store  = AlertStore()
        self.engine = AlertEngine(self.store)
        self._dialog = None
        self._token_to_symbol: Dict[int, str] = {}

        # Engine runs in its own thread
        self._engine_thread = QThread(self)
        self._engine_thread.setObjectName("AlertEngineThread")
        self.engine.moveToThread(self._engine_thread)
        self._engine_thread.started.connect(self.engine.start_engine)
        self._market_data_received.connect(self.engine.update_market_data, Qt.QueuedConnection)
        self._engine_thread.start()

        self.engine.alert_triggered.connect(self._on_engine_alert_triggered)
        self._request_engine_stop.connect(self.engine.stop_engine)
        self.engine_status_changed.emit("running")
        logger.info("AlertSystemManager ready")

        # FIX #8: Restore chart lines for alerts loaded from disk.
        # Use a short delay so the chart and chart_lines_manager are fully
        # initialised before we try to draw.
        QTimer.singleShot(2000, self._restore_chart_lines_on_startup)

    # ──────────────────────────────────────────────────────────────
    # MARKET DATA
    # ──────────────────────────────────────────────────────────────

    def update_market_data(self, ticks: List[Dict]) -> None:
        """Pass live ticks from main window's _on_market_data slot."""
        if not ticks:
            return

        # Normalize Kite ticks so AlertEngine can always resolve symbols.
        # KiteTicker ticks are token-keyed and usually omit tradingsymbol.
        parent = self.parent()
        if parent:
            imap = getattr(parent, "instrument_map", {}) or {}
            if imap:
                # Lazy/refresh cache from symbol->instrument map.
                if not self._token_to_symbol:
                    for sym, meta in imap.items():
                        token = int(meta.get("instrument_token", 0) or 0)
                        if token > 0:
                            self._token_to_symbol[token] = str(sym).upper()

                enriched_ticks: List[Dict] = []
                for tick in ticks:
                    token = int(tick.get("instrument_token", 0) or 0)
                    sym = str(tick.get("tradingsymbol") or "").strip().upper()
                    if token > 0 and sym:
                        self._token_to_symbol[token] = sym
                    if not sym and token > 0:
                        sym = self._token_to_symbol.get(token, "")
                    if sym:
                        enriched = dict(tick)
                        enriched["tradingsymbol"] = sym
                        enriched_ticks.append(enriched)
                    else:
                        enriched_ticks.append(tick)
                self._market_data_received.emit(enriched_ticks)
                return

        self._market_data_received.emit(ticks)

    # ──────────────────────────────────────────────────────────────
    # ALERT CRUD  (all chart-line side-effects live here)
    # ──────────────────────────────────────────────────────────────

    def add_alert(self, alert: Alert) -> None:
        """Add alert to store AND draw the corresponding chart line."""
        self.store.add(alert)
        logger.info(f"Alert added: {alert.symbol} {alert.condition} @ {alert.target_value}")
        # FIX #1: draw chart line immediately after saving
        self._add_chart_line(alert)
        # FIX #7: subscribe alert symbol to WS so engine gets price ticks
        self._ensure_alert_symbol_subscribed(alert.symbol)
        # Refresh open dialog if visible
        self._refresh_dialog_if_open()

    def remove_alert(self, alert_id: str) -> None:
        """Remove alert from store AND erase the corresponding chart line."""
        # FIX #2: look up alert *before* removing so we know the price/symbol
        alert = next((a for a in self.store.all() if a.id == alert_id), None)
        self.store.remove(alert_id)
        if alert:
            self._remove_chart_line(alert)
        self._refresh_dialog_if_open()


    def acknowledge_triggered_alert(self, alert_id: str) -> None:
        """Move a triggered alert to history and remove its chart line."""
        alert = next((a for a in self.store.all() if a.id == alert_id), None)
        if not alert:
            return

        if alert.status == AlertStatus.TRIGGERED.value:
            self._remove_chart_line(alert)

        alert.status = AlertStatus.EXPIRED.value
        self.store.update(alert)
        self._refresh_dialog_if_open()

    # ──────────────────────────────────────────────────────────────
    # ENGINE CALLBACK
    # ──────────────────────────────────────────────────────────────

    @Slot(str)
    def _on_engine_alert_triggered(self, alert_id: str) -> None:
        """Keep triggered alert lines visible until user acknowledges the alert."""
        self.alert_triggered.emit(alert_id)
        self.alert_sound_requested.emit()
        # Refresh open dialog so it moves the row to Triggered tab
        self._refresh_dialog_if_open()

    # ──────────────────────────────────────────────────────────────
    # DIALOG
    # ──────────────────────────────────────────────────────────────

    def show_dialog(self, parent=None) -> None:
        """Open the alert management dialog."""
        if self._dialog and self._dialog.isVisible():
            self._dialog.raise_()
            return
        # FIX #4 / #10: pass *self* (AlertSystemManager) not self.store so the
        # dialog can call add_alert / remove_alert with full chart integration.
        self._dialog = AlertManagementDialog(self, parent or self.parent())
        self._dialog.show()

    def show_quick_alert_dialog(self, parent=None) -> None:
        self.show_dialog(parent=parent)

    def show_alert_manager(self, parent=None) -> None:
        self.show_dialog(parent=parent)

    # ──────────────────────────────────────────────────────────────
    # CHART → ALERT  (called from chart bridge signal)
    # ──────────────────────────────────────────────────────────────

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
            "above": AlertCondition.PRICE_IS_ABOVE.value,
            "below": AlertCondition.PRICE_IS_BELOW.value,
            "price_above": AlertCondition.PRICE_IS_ABOVE.value,
            "price_below": AlertCondition.PRICE_IS_BELOW.value,
            "crosses_above": AlertCondition.PRICE_CROSSED_UP.value,
            "crosses_below": AlertCondition.PRICE_CROSSED_DOWN.value,
        }
        intent_map = {
            "buy_entry":     AlertIntent.BUY_ENTRY.value,
            "sell_entry":    AlertIntent.SELL_ENTRY.value,
            "profit_target": AlertIntent.PROFIT_TARGET.value,
            "stop_loss":     AlertIntent.STOP_LOSS.value,
            "breakout":      AlertIntent.BREAKOUT.value,
            "support":       AlertIntent.SUPPORT.value,
            "info":          AlertIntent.INFO.value,
        }

        condition = condition_map.get(
            str(data.get("condition", "")).lower(),
            AlertCondition.PRICE_IS_ABOVE.value
        )
        intent = intent_map.get(
            str(data.get("intent", "")).lower(),
            AlertIntent.INFO.value
        )

        alert = Alert(
            id=f"alert_{uuid.uuid4().hex[:8]}",
            symbol=symbol,
            condition=condition,
            intent=intent,
            target_value=target_value,
            note=str(data.get("note", "")).strip(),
        )
        # add_alert() now also draws the chart line (FIX #1)
        self.add_alert(alert)
        logger.info(f"Alert created from chart: {symbol} {condition} @ {target_value}")

    # ──────────────────────────────────────────────────────────────
    # ALERT LINE DRAG  (chart → Python price sync)
    # ──────────────────────────────────────────────────────────────

    @Slot(str)
    def update_alert_price_from_chart(self, payload: str) -> None:
        """
        Called when user drags an alert line to a new price on the chart.
        Payload (JSON): {"symbol": str, "old_price": float, "new_price": float}

        Institutional contract:
          1. Locate the alert by (symbol, old_price) with a small tolerance.
          2. Remove the old chart line at old_price.
          3. Update target_value, condition, note on the alert object.
          4. Persist the alert store.
          5. Re-draw the chart line at new_price.
          6. Refresh the alert dialog if open.
        """
        try:
            data = json.loads(payload)
            symbol = str(data.get("symbol", "")).strip().upper()
            old_price = float(data.get("old_price", 0.0))
            new_price = float(data.get("new_price", 0.0))
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.error(f"update_alert_price_from_chart: bad payload — {e}")
            return

        if not symbol or old_price <= 0 or new_price <= 0:
            logger.warning("update_alert_price_from_chart: invalid data, skipping")
            return

        if abs(new_price - old_price) < 0.001:
            return  # no meaningful change

        self._update_alert_price(symbol, old_price, new_price)

    def _update_alert_price(self, symbol: str, old_price: float, new_price: float) -> None:
        """
        Core mutation: find alert, update price, persist, sync chart line.
        Tolerance for float comparison: 0.5 (matches chart_lines_manager).
        """
        # ── 1. Find the alert ──
        tolerance = 0.5
        target = next(
            (
                a
                for a in self.store.active()
                if a.symbol == symbol and abs(a.target_value - old_price) <= tolerance
            ),
            None,
        )

        if target is None:
            logger.warning(
                f"_update_alert_price: no active alert for {symbol} @ {old_price:.2f}"
                f" (tolerance={tolerance})"
            )
            return

        logger.info(f"Updating alert price: {symbol} {old_price:.2f} → {new_price:.2f}")

        # ── 2. Remove old chart line ──
        self._remove_chart_line(target)

        # ── 3. Mutate the alert in-place ──
        # Re-evaluate condition based on current LTP
        ltp = self._get_current_ltp(symbol)
        if ltp > 0:
            if new_price > ltp:
                target.condition = AlertCondition.PRICE_CROSSED_UP.value
            else:
                target.condition = AlertCondition.PRICE_CROSSED_DOWN.value
        # else keep old condition; we don't want to silently corrupt it

        target.target_value = new_price
        target.note = (
            f"[Moved] Alert at ₹{new_price:.2f} "
            f"({'above' if new_price > ltp else 'below'} LTP ₹{ltp:.2f})"
            if ltp > 0
            else f"[Moved] Alert at ₹{new_price:.2f}"
        )

        # ── 4. Persist ──
        self.store.update(target)

        # ── 5. Re-draw chart line at new price ──
        self._add_chart_line(target)

        # ── 6. Refresh dialog ──
        self._refresh_dialog_if_open()

        logger.info(f"Alert price updated successfully: {symbol} → ₹{new_price:.2f}")

    def _get_current_ltp(self, symbol: str) -> float:
        """
        Best-effort LTP retrieval. Checks the parent main_window's chart LTP
        first (fastest), then watchlist, then falls back to 0.0.
        """
        try:
            parent = self.parent()
            # Chart widget is the most up-to-date source
            if parent and hasattr(parent, 'candlestick_chart'):
                chart = parent.candlestick_chart
                if getattr(chart, 'current_symbol', '') == symbol:
                    ltp = getattr(chart, 'current_ltp', 0.0)
                    if ltp > 0:
                        return float(ltp)
            # Watchlist fallback
            if parent and hasattr(parent, 'watchlist_manager'):
                wm = parent.watchlist_manager
                if hasattr(wm, 'get_ltp'):
                    ltp = wm.get_ltp(symbol)
                    if ltp and ltp > 0:
                        return float(ltp)
        except Exception as e:
            logger.debug(f"_get_current_ltp fallback for {symbol}: {e}")
        return 0.0

    # ──────────────────────────────────────────────────────────────
    # CHART-LINE HELPERS
    # ──────────────────────────────────────────────────────────────

    def _current_chart_interval(self) -> str:
        """Best-effort current chart timeframe (Kite interval string)."""
        try:
            p = self.parent()
            if p and hasattr(p, 'candlestick_chart'):
                iv = getattr(p.candlestick_chart, 'current_interval', None)
                if iv:
                    return str(iv)
        except Exception:
            pass
        return "day"

    def _clm(self):
        """Return chart_lines_manager from the parent main window, or None."""
        p = self.parent()
        return getattr(p, 'chart_lines_manager', None) if p else None

    def _add_chart_line(self, alert: Alert) -> None:
        """Draw a yellow horizontal ray on the chart for this alert."""
        clm = self._clm()
        if clm:
            try:
                clm.add_alert_line(
                    symbol=alert.symbol,
                    price=alert.target_value,
                    intent=alert.intent,
                    interval=self._current_chart_interval(),
                )
                logger.debug(f"Chart line added for {alert.symbol} @ {alert.target_value}")
            except Exception as e:
                logger.error(f"Failed to add chart line for {alert.symbol}: {e}")

    def _remove_chart_line(self, alert: Alert) -> None:
        """Erase the yellow horizontal ray from the chart for this alert."""
        clm = self._clm()
        if clm:
            try:
                clm.remove_alert_line(
                    symbol=alert.symbol,
                    price=alert.target_value,
                    interval=self._current_chart_interval(),
                )
                logger.debug(f"Chart line removed for {alert.symbol} @ {alert.target_value}")
            except Exception as e:
                logger.error(f"Failed to remove chart line for {alert.symbol}: {e}")

    # FIX #9: called by main_window when the chart switches symbol
    @Slot(str)
    def sync_chart_lines_for_symbol(self, symbol: str) -> None:
        """
        Draw all active alert lines for *symbol* onto the chart.
        Called whenever the chart changes symbol so lines are always visible.
        """
        clm = self._clm()
        if not clm or not symbol:
            return
        active = [a for a in self.store.active() if a.symbol == symbol]
        for alert in active:
            try:
                clm.add_alert_line(
                    symbol=alert.symbol,
                    price=alert.target_value,
                    intent=alert.intent,
                    interval=self._current_chart_interval(),
                )
            except Exception as e:
                logger.error(f"sync_chart_lines: error for {symbol}: {e}")
        if active:
            logger.info(f"Synced {len(active)} alert line(s) for {symbol}")

    # FIX #8: restore chart lines for all active alerts loaded from disk
    def _restore_chart_lines_on_startup(self) -> None:
        """Draw chart lines for every active alert persisted to disk."""
        clm = self._clm()
        if not clm:
            logger.warning("_restore_chart_lines_on_startup: chart_lines_manager not ready yet")
            return
        active = self.store.active()
        for alert in active:
            try:
                clm.add_alert_line(
                    symbol=alert.symbol,
                    price=alert.target_value,
                    intent=alert.intent,
                    interval=self._current_chart_interval(),
                )
            except Exception as e:
                logger.error(f"Startup restore: failed for {alert.symbol}: {e}")
        logger.info(f"Restored chart lines for {len(active)} active alert(s) on startup")

    # ──────────────────────────────────────────────────────────────
    # WS SUBSCRIPTION  (FIX #7)
    # ──────────────────────────────────────────────────────────────

    def _ensure_alert_symbol_subscribed(self, symbol: str) -> None:
        """Subscribe an alert symbol to the live WS feed if not already."""
        try:
            p = self.parent()
            if not p:
                return
            imap = getattr(p, 'instrument_map', {})
            inst = imap.get(symbol)
            if not inst:
                return
            token = inst.get('instrument_token')
            if not token:
                return
            worker = getattr(p, 'market_data_worker', None)
            if worker and worker.is_connected():
                worker.add_instruments([token])
                # Keep alert token in the subscription universe across watchlist refreshes.
                if hasattr(p, "_subscribed_tokens") and isinstance(p._subscribed_tokens, set):
                    p._subscribed_tokens.add(token)
                logger.info(f"Alert subscription: added token {token} for {symbol}")
        except Exception as e:
            logger.error(f"Failed to subscribe alert symbol {symbol}: {e}")

    def get_active_alert_tokens(self) -> List[int]:
        """
        FIX #7 (was always returning []).
        Return instrument tokens for all active-alert symbols so they get
        subscribed to the live WS feed in _on_watchlist_changed.
        """
        p = self.parent()
        if not p:
            return []
        imap = getattr(p, 'instrument_map', {})
        tokens: set = set()
        for alert in self.store.active():
            inst = imap.get(alert.symbol)
            if inst:
                token = inst.get('instrument_token')
                if token:
                    tokens.add(token)
        return list(tokens)

    # ──────────────────────────────────────────────────────────────
    # MISC COMPAT
    # ──────────────────────────────────────────────────────────────

    def set_instrument_map(self, instrument_map: Dict[str, Dict]) -> None:
        """Compatibility shim — instrument_map lives on the main window."""
        pass   # No-op; we access it via self.parent().instrument_map

    def get_notification_counts(self) -> tuple:
        alerts = self.store.all()
        active = sum(1 for a in alerts if a.status == AlertStatus.ACTIVE.value)
        today = datetime.now().date()
        triggered = sum(
            1
            for a in alerts
            if (
                a.status == AlertStatus.TRIGGERED.value
                and a.triggered_at
                and datetime.fromisoformat(a.triggered_at).date() == today
            )
        )
        return active, triggered

    def _refresh_dialog_if_open(self) -> None:
        """Refresh the open dialog's tables, if it exists and is visible."""
        if self._dialog and self._dialog.isVisible():
            try:
                self._dialog.refresh_tables()
            except Exception:
                pass

    def stop_engine(self) -> None:
        self._request_engine_stop.emit()
        self._engine_thread.quit()
        self._engine_thread.wait(3_000)
        self.engine_status_changed.emit("stopped")
        logger.info("AlertSystemManager stopped")


class AlertManagementDialog(QDialog):
    """Three-tab dialog: Active | Triggered | History."""

    def __init__(self, manager: "AlertSystemManager", parent=None):
        super().__init__(parent)
        # FIX #4 / #10: Accept the full manager (not just store) so that
        # add / delete operations go through chart-line integration.
        self.manager = manager
        self.store   = manager.store   # kept for read-only queries

        self.setWindowTitle("Alert Manager")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(850, 500)
        self.setMinimumSize(700, 400)

        self._drag_pos = None
        self._build_ui()
        self._apply_styles()

        # Refresh every 3 seconds
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_tables)
        self._refresh_timer.start(3_000)
        self.refresh_tables()
        self._wire_symbol_navigation()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        container = QFrame()
        container.setObjectName("alertMgmtContainer")
        container.mousePressEvent   = self._mouse_press
        container.mouseMoveEvent    = self._mouse_move
        container.mouseReleaseEvent = self._mouse_release
        outer.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(8)

        # Header row
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
        self.active_table    = self._make_table(
            ["Symbol", "Condition", "Target", "Created", "Action"])
        self.triggered_table = self._make_table(
            ["Symbol", "Condition", "Target", "Triggered At", "Action"])
        self.history_table   = self._make_table(
            ["Symbol", "Condition", "Target", "Triggered At", "Count"])

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
        t.verticalHeader().setDefaultSectionSize(34)
        t.verticalHeader().setMinimumSectionSize(34)
        t.setAlternatingRowColors(True)
        t.setShowGrid(False)
        header = t.horizontalHeader()
        for col, name in enumerate(headers):
            h_item = t.horizontalHeaderItem(col)
            if not h_item:
                continue
            if name == "Symbol":
                h_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            elif name in ("Condition", "Target", "Created", "Triggered At"):
                h_item.setTextAlignment(Qt.AlignCenter)
            elif name == "Count":
                h_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignRight)
            else:
                h_item.setTextAlignment(Qt.AlignCenter)
        if "Action" in headers:
            action_col = headers.index("Action")
            header.setSectionResizeMode(action_col, QHeaderView.ResizeMode.Fixed)
            t.setColumnWidth(action_col, 124)
        return t

    def _wire_symbol_navigation(self) -> None:
        """Open selected alert symbol in chart for faster alert triage."""
        for table in (self.active_table, self.triggered_table, self.history_table):
            table.itemSelectionChanged.connect(
                lambda t=table: self._open_selected_symbol_in_chart(t)
            )

    def _open_selected_symbol_in_chart(self, table: QTableWidget) -> None:
        selected_items = table.selectedItems()
        if not selected_items:
            return

        row = selected_items[0].row()
        symbol_item = table.item(row, 0)
        if not symbol_item:
            return

        symbol = (symbol_item.text() or "").strip().upper()
        if not symbol:
            return

        chart = getattr(self.parent(), "candlestick_chart", None)
        if chart and hasattr(chart, "on_search"):
            chart.on_search(symbol)

    @staticmethod
    def _fmt_indian_datetime(dt_text: Optional[str]) -> str:
        """Format ISO datetime to readable Indian-style date/time."""
        if not dt_text:
            return ""
        try:
            clean_text = dt_text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_text)
            return dt.strftime("%d-%m-%Y %I:%M %p")
        except Exception:
            return str(dt_text)[:16]

    @staticmethod
    def _set_row_alignment(table: QTableWidget, row: int):
        """Keep cell alignments consistent with header expectations."""
        left_item = table.item(row, 0)
        if left_item:
            left_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        for col in (1, 2, 3):
            item = table.item(row, col)
            if item:
                item.setTextAlignment(Qt.AlignCenter)

    def refresh_tables(self):
        alerts    = self.store.all()
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
            t.setItem(row, 3, QTableWidgetItem(self._fmt_indian_datetime(a.created_at)))

            del_btn = QPushButton("Delete")
            del_btn.setObjectName("deleteButton")
            del_btn.setMinimumWidth(74)
            # FIX #5 / #6: route through manager so chart line is also removed
            del_btn.clicked.connect(lambda _, aid=a.id: self._delete_alert(aid))
            t.setCellWidget(row, 4, del_btn)
            self._set_row_alignment(t, row)

    def _populate_triggered(self, alerts: List[Alert]):
        t = self.triggered_table
        t.setRowCount(len(alerts))
        for row, a in enumerate(alerts):
            t.setItem(row, 0, QTableWidgetItem(a.symbol))
            t.setItem(row, 1, QTableWidgetItem(a.condition))
            t.setItem(row, 2, QTableWidgetItem(f"{a.target_value:.2f}"))
            t.setItem(row, 3, QTableWidgetItem(self._fmt_indian_datetime(a.triggered_at)))

            ack_btn = QPushButton("Ack")
            ack_btn.setObjectName("ackButton")
            ack_btn.setMinimumWidth(74)
            ack_btn.clicked.connect(lambda _, aid=a.id: self._ack_alert(aid))
            t.setCellWidget(row, 4, ack_btn)
            self._set_row_alignment(t, row)

    def _populate_history(self, alerts: List[Alert]):
        t = self.history_table
        t.setRowCount(len(alerts))
        for row, a in enumerate(alerts):
            t.setItem(row, 0, QTableWidgetItem(a.symbol))
            t.setItem(row, 1, QTableWidgetItem(a.condition))
            t.setItem(row, 2, QTableWidgetItem(f"{a.target_value:.2f}"))
            t.setItem(row, 3, QTableWidgetItem(self._fmt_indian_datetime(a.triggered_at)))
            t.setItem(row, 4, QTableWidgetItem(str(a._trigger_count)))
            self._set_row_alignment(t, row)

    def _add_new(self):
        dlg = AlertCreationDialog(parent=self)
        # FIX #5: route through manager.add_alert() so chart line is drawn
        dlg.alert_created.connect(self.manager.add_alert)
        dlg.alert_created.connect(lambda _: self.refresh_tables())
        dlg.exec()

    def _delete_alert(self, alert_id: str):
        # FIX #6: route through manager.remove_alert() so chart line is removed
        self.manager.remove_alert(alert_id)
        self.refresh_tables()

    def _ack_alert(self, alert_id: str):
        """Acknowledge triggered alert — move it to expired/history."""
        self.manager.acknowledge_triggered_alert(alert_id)
        self.refresh_tables()

    # ── drag support ──
    def _mouse_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _mouse_move(self, event):
        if event.buttons() & Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def _mouse_release(self, event):
        self._drag_pos = None

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #121212;
                color: #e0e0e0;
                font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
            }
            QFrame#alertMgmtContainer {
                background-color: #121212;
                border: 1px solid #222630;
                border-radius: 2px;
            }
            QLabel#mgmtTitle {
                color: #e0e0e0;
                font-size: 15px;
                font-weight: 600;
                letter-spacing: 0.5px;
            }
            QTableWidget {
                background-color: #0f1318;
                border: 1px solid #1a2030;
                gridline-color: transparent;
                font-size: 13px;
                alternate-background-color: #0f1318;
                selection-background-color: #1a2840;
            }
            QTableWidget::item {
                padding: 4px 8px;
                border-bottom: 1px solid #1a2030;
            }
            QTableWidget::item:hover {
                background-color: #141920;
            }
            QTableWidget::item:selected {
                background-color: #1a2840;
            }
            QHeaderView::section {
                background-color: #1B1E26;
                color: #7b8496;
                font-weight: 600;
                letter-spacing: 0.5px;
                text-transform: uppercase;
                border: none;
                padding: 6px 8px;
                font-size: 11px;
                text-align: left;
            }
            QPushButton {
                background-color: rgba(255, 255, 255, 0.05);
                color: #e0e0e0;
                border-radius: 2px;
                padding: 6px 14px;
                font-weight: bold;
                border: 1px solid transparent;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
            }
            QPushButton#addButton,
            QPushButton#ackButton {
                background-color: rgba(0, 230, 118, 0.1);
                color: #00E676;
            }
            QPushButton#addButton:hover,
            QPushButton#ackButton:hover {
                background-color: rgba(0, 230, 118, 0.15);
            }
            QPushButton#deleteButton {
                background-color: rgba(239, 83, 80, 0.1);
                color: #ef5350;
                padding: 4px 10px;
            }
            QPushButton#deleteButton:hover {
                background-color: rgba(239, 83, 80, 0.15);
            }
            QPushButton#closeButton {
                background: transparent;
                color: #7B8496;
                border: none;
                font-size: 16px;
                padding: 0;
            }
            QPushButton#closeButton:hover { color: #FF4444; }
            QLineEdit, QComboBox, QDoubleSpinBox {
                background-color: #1B1E26;
                border: 1px solid transparent;
                border-radius: 2px;
                padding: 5px 8px;
                color: #ffffff;
            }
            QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {
                border: 1px solid #3b4252;
                background-color: #15181e;
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
