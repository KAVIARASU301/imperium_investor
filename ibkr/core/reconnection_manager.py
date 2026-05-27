# kite/core/reconnection_manager.py
"""
ReconnectionManager — coordinates service restart after network recovery.

Restart order (matches dependency graph):
  1. MarketDataWorker WebSocket  (KiteTicker)
  2. InstrumentLoader cache refresh  (token map may be stale after long outage)
  3. PositionManager re-fetch        (positions/P&L may have changed while offline)
  4. Chart live-data re-subscription (chart must re-subscribe to current symbol)
  5. Watchlist + scanner re-subscription

Uses exponential back-off with jitter — same as Zerodha's own API client.
"""

import logging
import random
import time
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot, QTimer

logger = logging.getLogger(__name__)

# Back-off config
_INITIAL_DELAY_MS  = 1_000    # 1s first retry
_MAX_DELAY_MS      = 60_000   # cap at 60s
_BACKOFF_FACTOR    = 2.0
_JITTER_RANGE      = 0.25     # ±25%


def _jittered_delay(attempt: int) -> int:
    base = min(_INITIAL_DELAY_MS * (_BACKOFF_FACTOR ** attempt), _MAX_DELAY_MS)
    jitter = base * _JITTER_RANGE * (random.random() * 2 - 1)
    return max(_INITIAL_DELAY_MS, int(base + jitter))


class ReconnectionManager(QObject):
    """
    Wired into the main window.  Call attach(main_window) after construction.
    """

    reconnection_started  = Signal()
    reconnection_complete = Signal()
    reconnection_failed   = Signal(str)     # reason

    def __init__(self, parent=None):
        super().__init__(parent)
        self._main_window = None
        self._attempt = 0
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.timeout.connect(self._do_reconnect)
        self._reconnecting = False

    def attach(self, main_window) -> None:
        self._main_window = main_window
        mdw = getattr(main_window, "market_data_worker", None)
        if mdw and hasattr(mdw, "set_external_reconnection_manager"):
            mdw.set_external_reconnection_manager(True)

    # ── Public slots called by NetworkMonitor ──────────────────────────────

    @Slot()
    def on_network_offline(self) -> None:
        """Called the moment the network drops."""
        logger.warning("ReconnectionManager: network offline — stopping WS")
        self._retry_timer.stop()
        self._reconnecting = False
        mw = self._main_window
        if not mw:
            return
        # Stop the WS gracefully so it doesn't hammer reconnects internally
        mdw = getattr(mw, "market_data_worker", None)
        if mdw:
            try:
                mdw._shutdown_requested = True   # prevent internal reconnect loop
                mdw.stop()
            except Exception as e:
                logger.warning("WS stop on offline failed: %s", e)

    @Slot()
    def on_network_online(self) -> None:
        """Called when the network comes back."""
        logger.info("ReconnectionManager: network online — scheduling reconnect")
        self._attempt = 0
        self._reconnecting = True
        self.reconnection_started.emit()
        self._schedule_retry()

    # ── Internal ──────────────────────────────────────────────────────────

    def _schedule_retry(self):
        delay = _jittered_delay(self._attempt)
        logger.info("Reconnect attempt %d in %dms", self._attempt + 1, delay)
        self._retry_timer.start(delay)

    @Slot()
    def _do_reconnect(self):
        if not self._reconnecting:
            return
        mw = self._main_window
        if not mw:
            return

        logger.info("Reconnecting services (attempt %d)…", self._attempt + 1)

        try:
            # ── 1. Restart WebSocket ──────────────────────────────────────
            self._restart_websocket(mw)

            # ── 2. Refresh positions (may have changed while offline) ─────
            pm = getattr(mw, "position_manager", None)
            if pm:
                QTimer.singleShot(2_000, lambda: pm.fetch_positions_from_kite("reconnect"))

            # ── 3. Reload chart for current symbol ────────────────────────
            QTimer.singleShot(3_000, lambda: self._reload_chart(mw))

            # ── 4. Rebuild full subscription universe ─────────────────────
            QTimer.singleShot(4_000, lambda: self._rebuild_subscriptions(mw))

            # ── 5. Account margin refresh ─────────────────────────────────
            am = getattr(mw, "account_manager", None)
            if am:
                QTimer.singleShot(5_000, lambda: am.refresh_margins(force=True))

            self._reconnecting = False
            self.reconnection_complete.emit()
            logger.info("Reconnection sequence complete")

        except Exception as e:
            logger.error("Reconnect attempt %d failed: %s", self._attempt + 1, e)
            self._attempt += 1
            if self._attempt < 10:
                self._schedule_retry()
            else:
                self._reconnecting = False
                self.reconnection_failed.emit(str(e))

    def _restart_websocket(self, mw) -> None:
        mdw = getattr(mw, "market_data_worker", None)
        if not mdw:
            return
        # Full reset: clear the shutdown flag, recreate KiteTicker
        mdw._shutdown_requested = False
        mdw.is_running = False
        mdw.subscribed_tokens.clear()
        mdw.start()
        logger.info("MarketDataWorker restarted")

    def _reload_chart(self, mw) -> None:
        chart = getattr(mw, "candlestick_chart", None)
        if not chart:
            return
        symbol = getattr(chart, "current_symbol", None)
        interval = getattr(chart, "current_interval", "day")
        if symbol:
            try:
                # Clear the cache for this symbol so fresh data loads
                dc = getattr(chart, "data_cache", None)
                if dc:
                    dc.invalidate(symbol, interval)
                chart.on_search(symbol)
                logger.info("Chart reloaded for %s [%s] after reconnect", symbol, interval)
            except Exception as e:
                logger.error("Chart reload failed: %s", e)

    def _rebuild_subscriptions(self, mw) -> None:
        rebuild = getattr(mw, "_rebuild_subscription_universe", None)
        if callable(rebuild):
            rebuild()
            logger.info("Subscription universe rebuilt after reconnect")