"""On-demand symbol resolution for IBKR using reqContractDetails."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QTimer

logger = logging.getLogger(__name__)


class IBKRSymbolResolver(QObject):
    """Resolves IBKR symbols on demand via reqContractDetails pattern search."""

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

        # Return cache hit immediately (exact match)
        cached = self._cache.get(cache_key)
        if cached:
            callback([cached])
            return

        # Return all cached entries that start with the query prefix
        prefix_hits = [v for k, v in self._cache.items() if k.startswith(cache_key)]
        if prefix_hits:
            callback(prefix_hits)
            return

        self._pending_query = cache_key
        QTimer.singleShot(0, lambda: self._execute_search(callback))

    def stop(self) -> None:
        self._pending_query = None

    def _execute_search(self, callback: Callable[[list], None]) -> None:
        query = self._pending_query
        self._pending_query = None
        if not query:
            callback([])
            return

        import threading

        result_holder: list[list] = []
        done = threading.Event()

        def _worker() -> None:
            try:
                results = self._fetch_symbols(query)
                result_holder.append(results)
            except Exception as exc:
                logger.debug("Symbol search failed for '%s': %s", query, exc)
                result_holder.append([])
            finally:
                done.set()

        threading.Thread(target=_worker, daemon=True, name=f"IBKRSearch-{query}").start()
        done.wait(timeout=8.0)

        results = result_holder[0] if result_holder else []
        self._on_results(results, callback)

    def _fetch_symbols(self, query: str) -> List[Dict[str, Any]]:
        """
        Fetch matching symbols from TWS.

        reqMatchingSymbols became async-only in ib_insync ≥ 0.9.86.
        We use reqContractDetails with a Stock pattern instead, which is
        reliably synchronous and works identically for equity searches.
        """
        from ib_insync import Stock

        results = []

        try:
            # Build a wildcard-style contract: TWS matches on symbol prefix.
            contract = Stock(query, "SMART", "USD")
            details_list = self.ib_client.reqContractDetails(contract)

            for details in (details_list or []):
                c = details.contract
                if not c or c.secType != "STK":
                    continue
                results.append({
                    "tradingsymbol": c.symbol,
                    "name": getattr(details, "longName", "") or c.symbol,
                    "exchange": c.primaryExch or c.exchange or "SMART",
                    "instrument_token": c.conId,
                    "segment": c.secType,
                    "currency": c.currency or "USD",
                    "instrument_type": "EQ",
                })

            if results:
                return results

        except Exception as exc:
            logger.debug("reqContractDetails search failed for '%s': %s", query, exc)

        # Fallback: build a minimal entry so the search bar can still
        # load the chart even if TWS contract details are unavailable.
        return [{
            "tradingsymbol": query,
            "name": query,
            "exchange": "SMART",
            "instrument_token": 0,
            "segment": "STK",
            "currency": "USD",
            "instrument_type": "EQ",
        }]

    def _on_results(self, results: list, callback: Callable[[list], None]) -> None:
        for inst in results:
            symbol = str(inst.get("tradingsymbol", "")).strip().upper()
            if symbol and symbol != "0":
                self._cache[symbol] = inst
        callback(results)