"""Qt worker for real-time IBKR market data aggregation."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from ibkr.core.contract_manager import ContractManager
from ibkr.utils.data_converter import normalize_ticker


class IBKRMarketDataWorker(QObject):
    tick_received = Signal(dict)
    error = Signal(str)

    def __init__(self, ib: Any):
        super().__init__()
        self.ib = ib
        self.contracts = ContractManager(ib)
        self.ib.pendingTickersEvent += self._on_pending_tickers

    @Slot(str)
    def subscribe_symbol(self, symbol: str) -> None:
        try:
            contract = self.contracts.resolve_stock(symbol)
            self.ib.reqMktData(contract, "", False, False)
        except Exception as exc:
            self.error.emit(str(exc))

    def _on_pending_tickers(self, tickers) -> None:
        for ticker in tickers:
            self.tick_received.emit(normalize_ticker(ticker))
