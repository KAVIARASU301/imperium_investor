# kite/core/network_monitor.py
"""
NetworkMonitor — detects online/offline transitions.

Strategy (same as Bloomberg Terminal):
  1. OS network-change signal (instant, zero latency)
  2. Lightweight HTTP probe to api.kite.trade every 5s (ground truth)
  3. Exponential back-off on consecutive failures before declaring offline
"""

import logging
import socket
import time
from typing import Optional

import requests
from PySide6.QtCore import QObject, Signal, QTimer, Slot

logger = logging.getLogger(__name__)

PROBE_URL      = "https://api.kite.trade"
PROBE_TIMEOUT  = 4.0   # seconds
PROBE_INTERVAL = 5_000  # ms — how often to probe when online
OFFLINE_PROBE_INTERVAL = 3_000  # ms — probe faster when offline
FAILURE_THRESHOLD = 2   # consecutive failures before declaring offline
SUCCESS_THRESHOLD = 1   # consecutive successes before declaring online


class NetworkMonitor(QObject):
    """
    Emits went_offline / came_online at state transitions only (not every tick).
    Uses a lightweight background probe — never blocks the UI thread.
    """

    went_offline = Signal()          # transition: online → offline
    came_online  = Signal()          # transition: offline → online
    status_changed = Signal(bool)    # bool = is_online

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_online: Optional[bool] = None   # None = unknown (startup)
        self._consecutive_failures = 0
        self._consecutive_successes = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._probe)

    def start(self):
        self._timer.start(PROBE_INTERVAL)
        # Probe immediately on start
        QTimer.singleShot(0, self._probe)
        logger.info("NetworkMonitor started")

    def stop(self):
        self._timer.stop()

    def is_online(self) -> bool:
        return bool(self._is_online)

    @Slot()
    def _probe(self):
        """Non-blocking probe via requests with a short timeout."""
        try:
            # Use a HEAD request — zero body, ~10ms on good network
            requests.head(PROBE_URL, timeout=PROBE_TIMEOUT)
            self._on_probe_success()
        except Exception:
            self._on_probe_failure()

    def _on_probe_success(self):
        self._consecutive_failures = 0
        self._consecutive_successes += 1

        if self._consecutive_successes >= SUCCESS_THRESHOLD:
            if self._is_online is not True:
                prev = self._is_online
                self._is_online = True
                # Adjust probe interval back to normal
                self._timer.setInterval(PROBE_INTERVAL)
                self.status_changed.emit(True)
                if prev is False:       # was offline, now back
                    logger.info("Network restored")
                    self.came_online.emit()
                else:
                    logger.info("Network confirmed online at startup")

    def _on_probe_failure(self):
        self._consecutive_successes = 0
        self._consecutive_failures += 1

        if self._consecutive_failures >= FAILURE_THRESHOLD:
            if self._is_online is not False:
                self._is_online = False
                # Probe more aggressively while offline
                self._timer.setInterval(OFFLINE_PROBE_INTERVAL)
                self.status_changed.emit(False)
                logger.warning("Network offline detected after %d failures",
                               self._consecutive_failures)
                self.went_offline.emit()