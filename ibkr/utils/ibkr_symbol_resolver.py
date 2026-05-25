"""On-demand symbol resolution for IBKR using reqMatchingSymbols."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QObject, QThread, Signal

logger = logging.getLogger(__name__)


class IBKRSymbolSearchWorker(QThread):
    """Background thread for IBKR symbol search."""

    results_ready = Signal(list)
    search_failed = Signal(str)

    def __init__(self, ib_client: Any, query: str):
        super().__init__()
        self.ib_client = ib_client
        self.query = query.strip().upper()

    def run(self) -> None:
        try:
            contracts = self.ib_client.reqMatchingSymbols(self.query)
            results = []
            for cd in contracts:
                contract = getattr(cd, "contract", None)
                if not contract:
                    continue
                if contract.secType != "STK":
                    continue
                details = getattr(cd, "contractDetails", None)
                results.append(
                    {
                        "tradingsymbol": contract.symbol,
                        "name": getattr(details, "longName", "") if details else "",
                        "exchange": contract.primaryExch or contract.exchange or "SMART",
                        "instrument_token": contract.conId,
                        "segment": contract.secType,
                        "currency": contract.currency,
                        "instrument_type": "EQ",
                    }
                )
            self.results_ready.emit(results)
        except Exception as exc:
            logger.error("IBKR symbol search failed for '%s': %s", self.query, exc)
            self.search_failed.emit(str(exc))


class IBKRSymbolResolver(QObject):
    """Resolves IBKR symbols on demand."""

    def __init__(self, ib_client: Any):
        super().__init__()
        self.ib_client = ib_client
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._search_worker: Optional[IBKRSymbolSearchWorker] = None

    def search(self, query: str, callback: Callable[[list], None]) -> None:
        if not query or not query.strip():
            callback([])
            return

        cache_key = query.strip().upper()
        if cache_key in self._cache:
            callback([self._cache[cache_key]])
            return

        if self._search_worker and self._search_worker.isRunning():
            self._search_worker.requestInterruption()
            self._search_worker.quit()
            self._search_worker.wait(200)

        worker = IBKRSymbolSearchWorker(self.ib_client, query)
        worker.results_ready.connect(lambda results: self._on_results(results, callback))
        worker.search_failed.connect(lambda _err: callback([]))
        self._search_worker = worker
        worker.start()

    def _on_results(self, results: list, callback: Callable[[list], None]) -> None:
        for inst in results:
            symbol = str(inst.get("tradingsymbol", "")).strip().upper()
            if symbol:
                self._cache[symbol] = inst
        callback(results)

