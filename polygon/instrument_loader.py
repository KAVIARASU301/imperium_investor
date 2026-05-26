"""Polygon instrument loader.

Builds a US-equity symbol index using Polygon reference tickers endpoint and
emits the same payload contract used by existing instrument loaders.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from PySide6.QtCore import QThread, Signal

from polygon.client import PolygonRESTClient

logger = logging.getLogger(__name__)


class PolygonInstrumentLoader(QThread):
    instruments_loaded = Signal(dict)
    progress_update = Signal(str)

    def __init__(self, polygon_client: PolygonRESTClient, page_limit: int = 500):
        super().__init__()
        self._client = polygon_client
        self._page_limit = max(100, min(int(page_limit), 1000))
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        try:
            self.progress_update.emit("Loading Polygon reference tickers...")
            rows = self._client.search_tickers("", limit=self._page_limit)
            # search endpoint with empty query may return empty; fallback to direct request
            if not rows:
                payload = self._client._request(
                    f"{self._client.BASE_URL}/v3/reference/tickers",
                    params={"market": "stocks", "active": "true", "limit": self._page_limit, "sort": "ticker", "order": "asc"},
                )
                rows = payload.get("results") if isinstance(payload, dict) else []

            instruments: List[Dict[str, Any]] = []
            instrument_map: Dict[str, Dict[str, Any]] = {}
            token_to_symbol: Dict[str, str] = {}

            for item in rows or []:
                if self._stop_requested:
                    return
                symbol = str(item.get("ticker") or "").strip().upper()
                if not symbol:
                    continue
                instrument = {
                    "tradingsymbol": symbol,
                    "name": item.get("name") or symbol,
                    "exchange": item.get("primary_exchange") or "SMART",
                    "instrument_token": symbol,
                    "segment": "STK",
                    "currency": item.get("currency_name") or "USD",
                    "instrument_type": item.get("type") or "EQ",
                    "lot_size": 1,
                }
                instrument_map[symbol] = instrument
                instruments.append(instrument)
                token_to_symbol[symbol] = symbol

            self.instruments_loaded.emit(
                {
                    "instruments": instruments,
                    "instrument_map": instrument_map,
                    "token_to_symbol": token_to_symbol,
                    "symbol_index": None,
                }
            )
            self.progress_update.emit(f"Loaded {len(instruments)} Polygon symbols")
        except Exception as exc:
            logger.error("Polygon instrument loader failed: %s", exc, exc_info=True)
            self.progress_update.emit(f"Polygon loader failed: {exc}")
