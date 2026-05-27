"""Qt worker for real-time IBKR market data aggregation."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ibkr.core.contract_manager import ContractManager

logger = logging.getLogger(__name__)


class IBKRMarketDataWorker(QObject):
    data_received = Signal(list)
    tick_received = Signal(dict)
    connection_established = Signal()
    connection_closed = Signal()
    error = Signal(str)

    def __init__(self, ib: Any):
        super().__init__()
        self.ib = ib
        self.contracts = ContractManager(ib)
        self._subscriptions: dict[str, Any] = {}
        self._tick_buffer: dict[int, dict[str, Any]] = {}
        self._flush_timer = QTimer(self)
        self._flush_timer.timeout.connect(self._flush)
        self._flush_timer.start(225)
        self.ib.pendingTickersEvent += self._on_pending_tickers
        self.connection_established.emit()

    @Slot(str)
    def subscribe_symbol(self, symbol: str) -> None:
        try:
            key = symbol.strip().upper()
            if not key or key in self._subscriptions:
                return

            contract = self.contracts.resolve_stock(key)
            self.ib.reqMktData(contract, "", False, False)
            self._subscriptions[key] = contract
        except Exception as exc:
            self.error.emit(str(exc))

    @Slot(str)
    def unsubscribe_symbol(self, symbol: str) -> None:
        key = symbol.strip().upper()
        contract = self._subscriptions.pop(key, None)
        if not contract:
            return
        try:
            self.ib.cancelMktData(contract)
            con_id = int(getattr(contract, "conId", 0) or 0)
            if con_id:
                self._tick_buffer.pop(con_id, None)
        except Exception as exc:
            self.error.emit(str(exc))

    def stop(self) -> None:
        for symbol in list(self._subscriptions.keys()):
            self.unsubscribe_symbol(symbol)
        self._flush_timer.stop()
        self.connection_closed.emit()

    def _on_pending_tickers(self, tickers) -> None:
        for ticker in tickers:
            contract = getattr(ticker, "contract", None)
            con_id = int(getattr(contract, "conId", 0) or 0)
            if not con_id:
                continue

            symbol = str(getattr(contract, "symbol", "") or "")
            tick = {
                "instrument_token": con_id,
                "tradingsymbol": symbol,
                "symbol": symbol,
                "exchange": getattr(contract, "exchange", "SMART"),
                "last_price": float(getattr(ticker, "last", 0) or getattr(ticker, "close", 0) or 0),
                "volume": int(getattr(ticker, "volume", 0) or 0),
                "ohlc": {
                    "open": float(getattr(ticker, "open", 0) or 0),
                    "high": float(getattr(ticker, "high", 0) or 0),
                    "low": float(getattr(ticker, "low", 0) or 0),
                    "close": float(getattr(ticker, "close", 0) or 0),
                },
                "bid": float(getattr(ticker, "bid", 0) or 0),
                "ask": float(getattr(ticker, "ask", 0) or 0),
                "last": getattr(ticker, "last", None),
                "close": getattr(ticker, "close", None),
            }
            self._tick_buffer[con_id] = tick

    def _flush(self) -> None:
        if not self._tick_buffer:
            return
        ticks = list(self._tick_buffer.values())
        self._tick_buffer.clear()
        self.data_received.emit(ticks)
        for tick in ticks:
            self.tick_received.emit(tick)
