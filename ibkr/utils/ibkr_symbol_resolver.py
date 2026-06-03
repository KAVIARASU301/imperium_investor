"""On-demand symbol resolution for IBKR using reqContractDetails.

This module exposes two compatible APIs:
- IBKRSymbolResolver.search(query, callback) for live search bars.
- IBKRSymbolSearchWorker for older main_window code paths that expect a QThread.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ibkr.core.ibkr_contract_db import IBKRContractDatabase

logger = logging.getLogger(__name__)


def _db_row_to_symbol_result(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    symbol = str(row.get("symbol") or "").strip().upper()
    if not symbol:
        return None
    con_id = int(row.get("con_id") or 0)
    return {
        "tradingsymbol": symbol,
        "symbol": symbol,
        "name": row.get("company_name") or row.get("description") or symbol,
        "exchange": row.get("primary_exchange") or row.get("exchange") or "SMART",
        "primaryExch": row.get("primary_exchange") or "",
        "instrument_token": con_id,
        "conId": con_id,
        "segment": row.get("sec_type") or "STK",
        "secType": row.get("sec_type") or "STK",
        "currency": row.get("currency") or "USD",
        "instrument_type": "EQ",
    }


def _normalize_symbol_result(details: Any, fallback_query: str) -> Optional[Dict[str, Any]]:
    """Convert an ib_insync ContractDetails object into the app instrument shape."""
    contract = getattr(details, "contract", details)
    if not contract:
        return None

    sec_type = str(getattr(contract, "secType", "") or "STK").upper()
    if sec_type and sec_type != "STK":
        return None

    symbol = str(getattr(contract, "symbol", "") or fallback_query).strip().upper()
    if not symbol:
        return None

    con_id = int(getattr(contract, "conId", 0) or 0)
    primary_exch = (
        getattr(contract, "primaryExchange", "")
        or getattr(contract, "primaryExch", "")
        or getattr(details, "primaryExchange", "")
        or ""
    )
    exchange = primary_exch or getattr(contract, "exchange", "") or "SMART"
    currency = getattr(contract, "currency", "") or "USD"
    long_name = getattr(details, "longName", "") or symbol

    return {
        "tradingsymbol": symbol,
        "symbol": symbol,
        "name": long_name,
        "exchange": exchange,
        "primaryExch": primary_exch,
        "instrument_token": con_id,
        "conId": con_id,
        "segment": sec_type or "STK",
        "secType": sec_type or "STK",
        "currency": currency,
        "instrument_type": "EQ",
    }


def _fetch_ibkr_symbols(ib_client: Any, query: str) -> List[Dict[str, Any]]:
    """Fetch matching US stock symbols from TWS/IB Gateway."""
    query = (query or "").strip().upper()
    if not query:
        return []

    from ib_insync import Stock

    results: List[Dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    contract_db = IBKRContractDatabase()

    try:
        db_rows = contract_db.search_symbols(query, limit=30)
        fresh_db_hits = []
        for row in db_rows:
            symbol = str(row.get("symbol") or "").strip().upper()
            if row.get("con_id") and symbol and not contract_db.is_stale(symbol):
                item = _db_row_to_symbol_result(row)
                if item:
                    fresh_db_hits.append(item)
        if fresh_db_hits:
            logger.info("Loaded %d IBKR symbol search result(s) for %s from DB", len(fresh_db_hits), query)
            return fresh_db_hits[:30]
    except Exception as exc:
        logger.warning("IBKR contract DB search failed for %s; falling back to IBKR: %s", query, exc)

    try:
        # Prefix-style lookup. SMART keeps routing generic while TWS resolves
        # the primary exchange/conId in ContractDetails.
        logger.info("Qualifying IBKR symbol search for %s from IBKR", query)
        contract = Stock(query, "SMART", "USD")
        details_list = ib_client.reqContractDetails(contract)

        for details in details_list or []:
            item = _normalize_symbol_result(details, query)
            if not item:
                continue
            key = (item["tradingsymbol"], int(item.get("conId") or 0))
            if key in seen:
                continue
            seen.add(key)
            results.append(item)
            try:
                contract_db.save_contract(item["tradingsymbol"], getattr(details, "contract", details), details)
            except Exception as save_exc:
                logger.warning("IBKR contract DB save failed for %s: %s", item["tradingsymbol"], save_exc)

        if results:
            # Prefer exact symbol match first, then shorter/common symbols.
            results.sort(
                key=lambda item: (
                    str(item.get("tradingsymbol", "")).upper() != query,
                    len(str(item.get("tradingsymbol", ""))),
                    str(item.get("tradingsymbol", "")),
                )
            )
            return results[:30]

    except Exception as exc:
        logger.debug("reqContractDetails search failed for '%s': %s", query, exc)

    # Fallback: build a minimal entry so chart loading can still proceed;
    # IBKRDataFetcher can resolve the raw symbol later.
    return [{
        "tradingsymbol": query,
        "symbol": query,
        "name": query,
        "exchange": "SMART",
        "primaryExch": "",
        "instrument_token": 0,
        "conId": 0,
        "segment": "STK",
        "secType": "STK",
        "currency": "USD",
        "instrument_type": "EQ",
    }]


class IBKRSymbolSearchWorker(QThread):
    """Backward-compatible QThread worker used by older main_window code."""

    results_ready = Signal(list)
    search_failed = Signal(str)

    def __init__(self, ib_client: Any, query: str, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.ib_client = ib_client
        self.query = (query or "").strip().upper()

    def run(self) -> None:  # type: ignore[override]
        try:
            if not self.query:
                self.results_ready.emit([])
                return
            self.results_ready.emit(_fetch_ibkr_symbols(self.ib_client, self.query))
        except Exception as exc:
            logger.exception("IBKR symbol worker failed for '%s'", self.query)
            self.search_failed.emit(str(exc))


class IBKRSymbolResolver(QObject):
    """Resolves IBKR symbols on demand via reqContractDetails pattern search."""

    def __init__(self, ib_client: Any, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.ib_client = ib_client
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._active_workers: list[IBKRSymbolSearchWorker] = []
        self._callbacks: Dict[IBKRSymbolSearchWorker, Callable[[list], None]] = {}

    def search(self, query: str, callback: Callable[[list], None]) -> None:
        query = (query or "").strip().upper()
        if not query:
            callback([])
            return

        cached = self._cache.get(query)
        if cached:
            callback([cached])
            return

        prefix_hits = [v for k, v in self._cache.items() if k.startswith(query)]
        if prefix_hits:
            callback(prefix_hits[:20])
            return

        worker = IBKRSymbolSearchWorker(self.ib_client, query, parent=self)
        self._active_workers.append(worker)
        self._callbacks[worker] = callback
        worker.results_ready.connect(self._on_worker_results)
        worker.search_failed.connect(self._on_worker_failed)
        worker.finished.connect(lambda w=worker: self._cleanup_worker(w))
        worker.start()

    def stop(self) -> None:
        for worker in list(self._active_workers):
            self._callbacks.pop(worker, None)
            worker.requestInterruption()
        self._active_workers.clear()

    def _fetch_symbols(self, query: str) -> List[Dict[str, Any]]:
        """Compatibility helper for any direct callers/tests."""
        return _fetch_ibkr_symbols(self.ib_client, query)

    @Slot(list)
    def _on_worker_results(self, results: list) -> None:
        worker = self.sender()
        callback = self._callbacks.pop(worker, None) if isinstance(worker, IBKRSymbolSearchWorker) else None
        self._cache_results(results)
        if callback:
            callback(results)

    @Slot(str)
    def _on_worker_failed(self, message: str) -> None:
        worker = self.sender()
        callback = self._callbacks.pop(worker, None) if isinstance(worker, IBKRSymbolSearchWorker) else None
        logger.debug("IBKR symbol search failed: %s", message)
        if callback:
            callback([])

    def _cleanup_worker(self, worker: IBKRSymbolSearchWorker) -> None:
        self._callbacks.pop(worker, None)
        if worker in self._active_workers:
            self._active_workers.remove(worker)
        worker.deleteLater()

    def _cache_results(self, results: list) -> None:
        for inst in results or []:
            symbol = str(inst.get("tradingsymbol") or inst.get("symbol") or "").strip().upper()
            if symbol and symbol != "0":
                self._cache[symbol] = inst