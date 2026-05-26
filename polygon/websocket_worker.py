"""Polygon market data worker with MarketDataWorker-compatible signals.

Implements a lightweight polling feed using Polygon snapshots so the UI can
switch to Polygon data without changing downstream consumers.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Set

from PySide6.QtCore import QMutex, QMutexLocker, QThread, Signal

from polygon.client import PolygonRESTClient

logger = logging.getLogger(__name__)


class PolygonWebSocketWorker(QThread):
    data_received = Signal(list)
    connection_established = Signal()
    connection_closed = Signal()
    connection_error = Signal(str)

    def __init__(self, polygon_client: PolygonRESTClient, poll_interval_s: float = 1.0):
        super().__init__()
        self._client = polygon_client
        self._poll_interval_s = max(0.3, float(poll_interval_s))
        self._running = False
        self._symbols: Set[str] = set()
        self._mutex = QMutex()

    def set_symbols(self, symbols: List[str]) -> None:
        normalized = {str(s or "").strip().upper() for s in symbols if str(s or "").strip()}
        with QMutexLocker(self._mutex):
            self._symbols = normalized

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        self._running = True
        self.connection_established.emit()
        logger.info("PolygonWebSocketWorker started")
        while self._running:
            try:
                with QMutexLocker(self._mutex):
                    symbols = list(self._symbols)
                ticks = self._fetch_ticks(symbols)
                if ticks:
                    self.data_received.emit(ticks)
            except Exception as exc:
                logger.warning("Polygon worker poll failed: %s", exc)
                self.connection_error.emit(str(exc))
            time.sleep(self._poll_interval_s)

        self.connection_closed.emit()
        logger.info("PolygonWebSocketWorker stopped")

    def _fetch_ticks(self, symbols: List[str]) -> List[Dict]:
        ticks: List[Dict] = []
        for sym in symbols:
            snap = self._client.get_snapshot(sym)
            if not snap:
                continue
            day = snap.get("day") or {}
            prev = snap.get("prevDay") or {}
            last_trade = snap.get("lastTrade") or {}
            price = float(last_trade.get("p") or day.get("c") or prev.get("c") or 0.0)
            tick = {
                "tradingsymbol": sym,
                "exchange": "SMART",
                "instrument_token": sym,
                "last_price": price,
                "volume_traded": int(day.get("v") or 0),
                "ohlc": {
                    "open": float(day.get("o") or 0.0),
                    "high": float(day.get("h") or 0.0),
                    "low": float(day.get("l") or 0.0),
                    "close": float(prev.get("c") or day.get("c") or 0.0),
                },
            }
            ticks.append(tick)
        return ticks
