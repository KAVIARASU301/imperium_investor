"""Polygon REST client wrapper for auth validation, snapshots, and symbol search."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests


class PolygonRESTClient:
    BASE_URL = "https://api.polygon.io"

    def __init__(self, api_key: str, timeout_s: float = 10.0):
        if not api_key or not api_key.strip():
            raise ValueError("Polygon API key is required")
        self.api_key = api_key.strip()
        self.timeout_s = float(timeout_s)
        self.session = requests.Session()

    def get_snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        url = f"{self.BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers/{symbol.upper()}"
        payload = self._request(url)
        return payload.get("ticker") if isinstance(payload, dict) else None

    def search_tickers(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        payload = self._request(
            f"{self.BASE_URL}/v3/reference/tickers",
            params={
                "search": q,
                "market": "stocks",
                "active": "true",
                "sort": "ticker",
                "order": "asc",
                "limit": max(1, min(int(limit), 100)),
            },
        )
        results = payload.get("results") if isinstance(payload, dict) else None
        return results if isinstance(results, list) else []

    def validate_key(self) -> Dict[str, Any]:
        payload = self._request(
            f"{self.BASE_URL}/v3/reference/tickers",
            params={"market": "stocks", "limit": 1},
        )
        return {
            "ok": True,
            "request_id": payload.get("request_id") if isinstance(payload, dict) else None,
        }

    def _request(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        merged = {"apiKey": self.api_key}
        if params:
            merged.update(params)
        resp = self.session.get(url, params=merged, timeout=self.timeout_s)
        resp.raise_for_status()
        return resp.json()
