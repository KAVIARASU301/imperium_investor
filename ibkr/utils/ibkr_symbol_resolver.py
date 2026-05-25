"""On-demand symbol resolution for IBKR using reqMatchingSymbols."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QObject, QTimer

logger = logging.getLogger(__name__)


class IBKRSymbolResolver(QObject):
    """Resolves IBKR symbols on demand."""

    def __init__(self, ib_client: Any, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.ib_client = ib_client
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._pending_query: Optional[str] = None

    def search(self, query: str, callback: Callable[[list], None]) -> None:
        if not query or not query.strip():
            callback([])
            return

        cache_key = query.strip().upper()
        if cache_key in self._cache:
            callback([self._cache[cache_key]])
            return

        self._pending_query = cache_key
        QTimer.singleShot(0, lambda: self._execute_search(callback))

    def stop(self) -> None:
        """Stop any in-flight search request."""
        self._pending_query = None

    def _execute_search(self, callback: Callable[[list], None]) -> None:
        query = self._pending_query
        self._pending_query = None
        if not query:
            callback([])
            return

        try:
            # Run on a worker thread so IBKR/TWS timeouts do not block UI responsiveness.
            import threading

            result_holder: list[list[Any]] = []
            done = threading.Event()

            def _worker() -> None:
                try:
                    contracts = self.ib_client.reqMatchingSymbols(query)
                    result_holder.append(contracts or [])
                except Exception as exc:
                    logger.debug("Symbol search failed for '%s': %s", query, exc)
                    result_holder.append([])
                finally:
                    done.set()

            worker = threading.Thread(target=_worker, daemon=True)
            worker.start()
            done.wait(timeout=8.0)

            contracts = result_holder[0] if result_holder else []
            results = []
            for cd in (contracts or []):
                contract = getattr(cd, "contract", None)
                if not contract or contract.secType != "STK":
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
            self._on_results(results, callback)
        except Exception as exc:
            logger.error("IBKR symbol search failed for '%s': %s", query, exc)
            callback([])

    def _on_results(self, results: list, callback: Callable[[list], None]) -> None:
        for inst in results:
            symbol = str(inst.get("tradingsymbol", "")).strip().upper()
            if symbol:
                self._cache[symbol] = inst
        callback(results)
