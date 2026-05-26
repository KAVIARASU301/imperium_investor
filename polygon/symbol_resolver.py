"""Async Polygon ticker symbol resolver compatible with IBKR search callback contract."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

from PySide6.QtCore import QObject

from polygon.client import PolygonRESTClient

logger = logging.getLogger(__name__)


class PolygonSymbolResolver(QObject):
    """Search resolver with the same callback signature as IBKRSymbolResolver.search."""

    def __init__(self, polygon_client: PolygonRESTClient, parent: QObject | None = None):
        super().__init__(parent)
        self._client = polygon_client

    def search(self, query: str, callback: Callable[[List[Dict[str, Any]]], None]) -> None:
        """Resolve `query` and invoke callback with app-compatible result records."""
        q = (query or "").strip()
        if not q:
            callback([])
            return

        try:
            raw = self._client.search_tickers(q, limit=30)
            normalized = []
            for item in raw:
                symbol = str(item.get("ticker") or "").strip().upper()
                if not symbol:
                    continue
                normalized.append(
                    {
                        "tradingsymbol": symbol,
                        "name": item.get("name") or symbol,
                        "exchange": item.get("primary_exchange") or "SMART",
                        "instrument_type": item.get("type") or "STK",
                        "currency": item.get("currency_name") or "USD",
                    }
                )
            callback(normalized)
        except Exception as exc:
            logger.warning("Polygon symbol search failed for '%s': %s", q, exc)
            callback([])
