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
  AlertManagementDialog — UI in kite/widgets/alert_management_dialog.py
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
from kite.utils.sounds import play_alert
from kite.widgets.notifications import ToastNotification
from kite.core import chart_lines_manager as clm_module

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
    _last_trigger_epoch: float = field(default=0.0, repr=False, compare=False)
    _last_trigger_day:   str   = field(default="", repr=False, compare=False)

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
        self._last_save_at = 0.0
        self._dirty = False

    def all(self) -> List[Alert]:
        with QMutexLocker(self._mutex):
            return list(self._alerts.values())

    def active(self) -> List[Alert]:
        return [a for a in self.all() if a.status == AlertStatus.ACTIVE.value]

    def get(self, alert_id: str) -> Optional[Alert]:
        with QMutexLocker(self._mutex):
            return self._alerts.get(alert_id)

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
        self._save(force=True)

    def _save(self, force: bool = False):
        now = time.time()
        # Coalesce high-frequency writes from the engine loop.
        if not force and (now - self._last_save_at) < 0.5:
            self._dirty = True
            return
        try:
            payload = json.dumps([a.to_dict() for a in self._alerts.values()])
            with sqlite3.connect(self._path, timeout=5.0) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("UPDATE alerts_store SET payload_json = ?, updated_at = ? WHERE id = 1",
                             (payload, datetime.now().isoformat()))
                conn.commit()
            self._last_save_at = now
            if self._dirty:
                self._dirty = False
                self._save(force=True)
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
        self._repeat_cooldown_seconds = 10

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

    @Slot(dict)
    def set_token_symbol_map(self, token_to_symbol: Dict[int, str]) -> None:
        """Replace token→symbol cache with a prebuilt map from manager."""
        with QMutexLocker(self._mutex):
            self._token_to_symbol = dict(token_to_symbol)

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
        now = datetime.now()
        now_epoch = time.time()
        # Prevent alert storms for repeat alerts on noisy ticks.
        if alert.repeat and (now_epoch - alert._last_trigger_epoch) < self._repeat_cooldown_seconds:
            return
        # Time-based alerts should only fire once per day.
        if alert.condition == AlertCondition.TIME_BASED.value:
            day_key = now.strftime("%Y-%m-%d")
            if alert._last_trigger_day == day_key:
                return
            alert._last_trigger_day = day_key

        if not alert.repeat:
            alert.status = AlertStatus.TRIGGERED.value
        alert.triggered_at = now.isoformat()
        alert._last_trigger_epoch = now_epoch
        alert._trigger_count += 1
        self._store.update(alert)
        self.alert_triggered.emit(alert.id)
        logger.info(f"🔔 Alert triggered: {alert.symbol} — {alert.condition} @ {alert.target_value}")


# ─────────────────────────────────────────────────────────────────────────────
# ALERT SYSTEM MANAGER
# ─────────────────────────────────────────────────────────────────────────────

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


    # ──────────────────────────────────────────────────────────────
    # MARKET DATA
    # ──────────────────────────────────────────────────────────────

    def update_market_data(self, ticks: List[Dict]) -> None:
        """Pass live ticks from main window's _on_market_data slot."""
        if not ticks:
            return
        self._market_data_received.emit(ticks)

    # ──────────────────────────────────────────────────────────────
    # ALERT CRUD  (all chart-line side-effects live here)
    # ──────────────────────────────────────────────────────────────

    def add_alert(self, alert: Alert) -> None:
        """Add alert to store AND draw the corresponding chart line."""
        validation_error = self._validate_alert(alert)
        if validation_error:
            logger.warning(f"Rejected alert {alert.id}: {validation_error}")
            return

        duplicate = next(
            (
                a for a in self.store.active()
                if a.symbol == alert.symbol
                and a.condition == alert.condition
                and abs(float(a.target_value) - float(alert.target_value)) < 0.001
            ),
            None,
        )
        if duplicate:
            logger.info(
                f"Skipping duplicate alert for {alert.symbol}: {alert.condition} @ {alert.target_value}"
            )
            return

        self.store.add(alert)
        logger.info(f"Alert added: {alert.symbol} {alert.condition} @ {alert.target_value}")
        # FIX #1: draw chart line immediately after saving
        self._add_chart_line(alert)
        # FIX #7: subscribe alert symbol to WS so engine gets price ticks
        self._ensure_alert_symbol_subscribed(alert.symbol)
        # Also re-run restore logic so re-armed/reloaded alerts redraw mid-session too.
        self._restore_chart_lines_on_startup()
        # Refresh open dialog if visible
        self._refresh_dialog_if_open()

    def remove_alert(self, alert_id: str) -> None:
        """Remove alert from store AND erase the corresponding chart line."""
        # FIX #2: look up alert *before* removing so we know the price/symbol
        alert = next((a for a in self.store.all() if a.id == alert_id), None)
        if alert:
            self._remove_chart_line(alert)
            self._discard_chart_line_cache(alert.symbol, alert.target_value)
        self.store.remove(alert_id)
        self._refresh_dialog_if_open()


    def get_alert_for_price(self, symbol: str, price: float, tolerance: float = 0.5) -> Optional[Alert]:
        """Return active alert matching symbol and price within tolerance."""
        target_symbol = str(symbol or "").strip().upper()
        for alert in self.store.active():
            if str(alert.symbol).strip().upper() != target_symbol:
                continue
            if abs(float(alert.target_value) - float(price)) <= float(tolerance):
                return alert
        return None

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
        alert = self.store.get(alert_id)
        if not alert:
            return

        # CRITICAL FIX: remove the chart line immediately at trigger time.
        # Do NOT wait for the user to acknowledge. A triggered alert's line
        # is no longer meaningful — the price has already passed the level.
        self._remove_chart_line(alert)

        # Clear session draw cache so the line can be re-added if re-armed
        self._discard_chart_line_cache(alert.symbol, alert.target_value)

        title = f"Alert: {alert.symbol}"
        message = (f"{alert.condition} @ ₹{alert.target_value:,.2f} "
                   f"— {alert.intent}")
        ToastNotification(title, message, "warn", 6000).show_toast()

        # Re-arm if configured as repeat (line will be redrawn by engine
        # on next tick if the condition still holds)
        if alert.repeat:
            alert.status = AlertStatus.ACTIVE.value
            alert.triggered_at = datetime.now().isoformat()
            self.store.update(alert)
        # else leave as TRIGGERED — do not re-draw line

        QTimer.singleShot(0, play_alert)
        self.alert_triggered.emit(alert_id)
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
        from kite.widgets.alert_management_dialog import AlertManagementDialog

        self._dialog = AlertManagementDialog(self, parent or self.parent())
        self._dialog.show()

    def show_quick_alert_dialog(self, parent=None) -> None:
        self.show_dialog(parent=parent)

    def show_alert_manager(self, parent=None) -> None:
        self.show_dialog(parent=parent)

    def _resolve_chart_price_condition(self, symbol: str, target_value: float, requested: str = "") -> str:
        """
        Resolve chart-created price alert direction using live LTP.

        Behavior:
        - Explicit crossing aliases are honored directly.
        - For generic / missing condition values, infer crossing direction from
          alert line position relative to current LTP:
            * target above LTP -> Price Crossed Up
            * target below LTP -> Price Crossed Down
        - If LTP is unavailable, fallback to non-crossing level conditions to
          avoid choosing an incorrect crossing direction.
        """
        requested_key = str(requested or "").strip().lower()
        explicit_map = {
            "crosses_above": AlertCondition.PRICE_CROSSED_UP.value,
            "crosses_below": AlertCondition.PRICE_CROSSED_DOWN.value,
            "price_above": AlertCondition.PRICE_IS_ABOVE.value,
            "price_below": AlertCondition.PRICE_IS_BELOW.value,
            "above": AlertCondition.PRICE_IS_ABOVE.value,
            "below": AlertCondition.PRICE_IS_BELOW.value,
        }

        if requested_key in ("crosses_above", "crosses_below"):
            return explicit_map[requested_key]

        ltp = self._get_current_ltp(symbol)
        if ltp > 0:
            if target_value >= ltp:
                return AlertCondition.PRICE_CROSSED_UP.value
            return AlertCondition.PRICE_CROSSED_DOWN.value

        return explicit_map.get(requested_key, AlertCondition.PRICE_IS_ABOVE.value)

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

        intent_map = {
            "buy_entry":     AlertIntent.BUY_ENTRY.value,
            "sell_entry":    AlertIntent.SELL_ENTRY.value,
            "profit_target": AlertIntent.PROFIT_TARGET.value,
            "stop_loss":     AlertIntent.STOP_LOSS.value,
            "breakout":      AlertIntent.BREAKOUT.value,
            "support":       AlertIntent.SUPPORT.value,
            "info":          AlertIntent.INFO.value,
        }

        condition = self._resolve_chart_price_condition(
            symbol=symbol,
            target_value=target_value,
            requested=str(data.get("condition", "")),
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

    def audit_chart_lines(self, symbol: str) -> None:
        """
        Remove any alert lines in the chart that have no corresponding
        active alert in the store. Prevents orphans from crashes/deletes.
        """
        clm = self._clm()
        if not clm or not symbol:
            return

        active_prices = {
            float(a.target_value)
            for a in self.store.active()
            if a.symbol == symbol
        }

        state = clm._load_symbol_drawings(symbol)
        drawings = state.get("drawings", {})
        rays = drawings.get("horizontal_rays", [])
        mode = clm._get_trading_mode()

        orphans = []
        for ray in rays:
            if ray.get("lineCategory") != "alert":
                continue
            if str(ray.get("tradingMode", "live")).lower() != mode:
                continue
            ray_price = float(ray.get("startPrice", 0))
            has_owner = any(abs(ray_price - p) < 0.01 for p in active_prices)
            if not has_owner:
                orphans.append(ray_price)

        for price in orphans:
            logger.info(f"Removing orphan alert line: {symbol} @ {price:.2f}")
            clm.remove_alert_line(symbol=symbol, price=price)

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
        # Preserve condition family; only adjust directional variant.
        ltp = self._get_current_ltp(symbol)
        is_level_condition = target.condition in (
            AlertCondition.PRICE_IS_ABOVE.value,
            AlertCondition.PRICE_IS_BELOW.value,
        )
        is_cross_condition = target.condition in (
            AlertCondition.PRICE_CROSSED_UP.value,
            AlertCondition.PRICE_CROSSED_DOWN.value,
        )
        if ltp > 0:
            if is_level_condition:
                target.condition = (
                    AlertCondition.PRICE_IS_ABOVE.value
                    if new_price > ltp
                    else AlertCondition.PRICE_IS_BELOW.value
                )
            elif is_cross_condition:
                target.condition = (
                    AlertCondition.PRICE_CROSSED_UP.value
                    if new_price > ltp
                    else AlertCondition.PRICE_CROSSED_DOWN.value
                )
        # Non-price conditions remain unchanged.

        target.target_value = new_price
        target.note = (
            f"[Moved] Alert at ₹{new_price:.2f} "
            f"({'above' if new_price > ltp else 'below'} LTP ₹{ltp:.2f})"
            if ltp > 0
            else f"[Moved] Alert at ₹{new_price:.2f}"
        )

        # ── 4. Persist ──
        self.store.update(target)
        self._discard_chart_line_cache(symbol, old_price)
        # Ensure the newly moved line is not skipped by stale session/coalescing caches.
        self._discard_chart_line_cache(symbol, new_price)

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


    @staticmethod
    def _chart_line_key(symbol: str, price: float) -> str:
        return f"{symbol}_{price:.2f}"

    def _discard_chart_line_cache(self, symbol: str, price: float) -> None:
        """Best-effort cleanup for chart-line session/coalescing caches."""
        line_key = self._chart_line_key(symbol, price)
        session_lines = getattr(clm_module, "_lines_drawn_this_session", None)
        if session_lines is not None:
            session_lines.discard(line_key)
        recent_draws = getattr(clm_module, "_recent_draws", None)
        if recent_draws is not None:
            recent_draws.pop(line_key, None)

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
                line_key = self._chart_line_key(alert.symbol, alert.target_value)
                session_lines = getattr(clm_module, "_lines_drawn_this_session", set())
                if line_key in session_lines:
                    return
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
        drawn = 0

        for alert in active:
            try:
                # Check EVERY existing interval file for this symbol.
                paths = self._get_all_interval_file_paths(clm, alert.symbol)
                needs_draw = True
                for path in paths:
                    interval = self._extract_interval_from_path(clm, path, alert.symbol)
                    state = clm._load_symbol_drawings(alert.symbol, interval)
                    if clm._has_existing_alert_drawings(
                        state.get("drawings", {}),
                        alert.target_value,
                    ):
                        needs_draw = False
                        break

                if needs_draw:
                    self._add_chart_line(alert)
                    drawn += 1
            except Exception as e:
                logger.error(f"sync_chart_lines: error for {symbol}: {e}")

        # Run orphan audit while we're here.
        self.audit_chart_lines(symbol)

        if drawn:
            logger.info(f"Synced {drawn} alert line(s) for {symbol}")

    # FIX #8: deferred startup scan writes persisted lines for active alerts
    def _restore_chart_lines_on_startup(self) -> None:
        """
        Called only after chart bridge is confirmed ready.
        Writes lines to all interval JSON files for every active alert.
        The chart itself will render from the JSON on next symbol load.
        """
        clm = self._clm()
        if not clm:
            return

        active = self.store.active()
        if not active:
            return

        by_symbol: Dict[str, list] = {}
        for alert in active:
            by_symbol.setdefault(alert.symbol, []).append(alert)

        for symbol, alerts in by_symbol.items():
            for alert in alerts:
                def _apply(drawings, a=alert):
                    if not clm._has_existing_alert_drawings(drawings, a.target_value):
                        new_line = clm._create_horizontal_ray_line(
                            price=a.target_value,
                            color="#FFD700",
                            start_time=0,
                            text="",
                            metadata={
                                "lineCategory": "alert",
                                "intent": a.intent,
                                "tradingMode": clm._get_trading_mode(),
                            },
                        )
                        drawings["horizontal_rays"].append(new_line)

                clm._save_to_all_intervals(symbol, _apply)

        current = getattr(
            getattr(self.parent(), 'candlestick_chart', None),
            'current_symbol', '',
        )
        if current:
            clm._refresh_chart()

        logger.info(f"Restored lines for {len(active)} active alert(s) across all timeframes")


    def _get_all_interval_file_paths(self, clm, symbol: str) -> List[str]:
        """Return all existing chart state paths, with a fallback for older CLM objects."""
        method = getattr(clm, "_get_all_interval_file_paths", None)
        if callable(method):
            return method(symbol)

        safe_symbol = symbol.replace("/", "_").replace(":", "_")
        drawings_dir = getattr(clm, "drawings_dir", "kite/user_data/chart_drawings")
        try:
            paths = [
                os.path.join(drawings_dir, fname)
                for fname in os.listdir(drawings_dir)
                if fname.startswith(safe_symbol + "_") and fname.endswith("_state.json")
            ]
        except OSError:
            paths = []

        if paths:
            return paths

        path_method = getattr(clm, "_get_symbol_file_path", None)
        if callable(path_method):
            return [path_method(symbol)]
        return [os.path.join(drawings_dir, f"{safe_symbol}_{self._current_chart_interval()}_state.json")]

    def _extract_interval_from_path(self, clm, path: str, symbol: str) -> str:
        """Extract timeframe from a state path, with a fallback for older CLM objects."""
        method = getattr(clm, "_extract_interval_from_path", None)
        if callable(method):
            return method(path, symbol)

        fname = os.path.basename(path)
        safe_symbol = symbol.replace("/", "_").replace(":", "_")
        prefix = safe_symbol + "_"
        suffix = "_state.json"
        if fname.startswith(prefix) and fname.endswith(suffix):
            return fname[len(prefix): -len(suffix)]
        return self._current_chart_interval()

    def purge_all_alert_lines_from_chart_files(self) -> int:
        """
        Nuclear option: remove every alert line from every drawing JSON file.
        Then redraw only lines that have a live active alert in the store.
        Returns the count of orphan lines removed.
        """
        clm = self._clm()
        if not clm:
            return 0

        removed = 0
        drawings_dir = clm.drawings_dir

        for fname in os.listdir(drawings_dir):
            if not fname.endswith("_state.json"):
                continue
            fpath = os.path.join(drawings_dir, fname)
            try:
                with open(fpath, "r") as f:
                    state = json.load(f)
                if "drawings" not in state:
                    continue
                rays = state["drawings"].get("horizontal_rays", [])
                before = len(rays)
                state["drawings"]["horizontal_rays"] = [
                    r for r in rays
                    if r.get("lineCategory") != "alert"
                ]
                after = len(state["drawings"]["horizontal_rays"])
                if after < before:
                    with open(fpath, "w") as f:
                        json.dump(state, f, indent=2)
                    removed += before - after
            except Exception as e:
                logger.error(f"purge_all_alert_lines: error in {fname}: {e}")

        self._restore_chart_lines_on_startup()
        logger.info(f"Purged {removed} orphan alert lines; redrawn from store")
        return removed

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
        """Build token→symbol cache once and push it to AlertEngine thread."""
        token_to_symbol: Dict[int, str] = {}
        for symbol, meta in (instrument_map or {}).items():
            token = int((meta or {}).get("instrument_token", 0) or 0)
            if token > 0:
                token_to_symbol[token] = str(symbol).strip().upper()

        self._token_to_symbol = token_to_symbol
        self.engine.set_token_symbol_map(token_to_symbol)

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
                self._dialog.refresh_tables(force=True)
            except TypeError:
                self._dialog.refresh_tables()
            except Exception:
                pass

    def _validate_alert(self, alert: Alert) -> Optional[str]:
        if not alert.symbol or not str(alert.symbol).strip():
            return "symbol is required"

        try:
            target = float(alert.target_value)
        except (TypeError, ValueError):
            return "target value must be numeric"

        if alert.condition != AlertCondition.TIME_BASED.value and target <= 0:
            return "target value must be > 0"

        if alert.condition in (AlertCondition.RSI_ABOVE.value, AlertCondition.RSI_BELOW.value):
            if target < 0 or target > 100:
                return "RSI alerts require target in range 0..100"

        if alert.condition == AlertCondition.TIME_BASED.value:
            hhmm = int(target)
            hour = hhmm // 100
            minute = hhmm % 100
            if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                return "time-based alerts require HHMM format (e.g. 915, 1530)"

        return None

    def stop_engine(self) -> None:
        self._request_engine_stop.emit()
        self._engine_thread.quit()
        self._engine_thread.wait(3_000)
        self.engine_status_changed.emit("stopped")
        logger.info("AlertSystemManager stopped")
