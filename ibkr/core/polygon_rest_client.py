"""Polygon.io REST client for historical aggregates.

Phase 1 foundation from the IBKR hybrid architecture plan:
- Centralized auth via API key
- Interval mapping for chart engine timeframes
- Retry with exponential backoff + jitter for rate limits/transient failures
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


class PolygonRESTClient:
    """Small Polygon REST wrapper focused on aggregate bars."""

    BASE_URL = "https://api.polygon.io"

    INTERVAL_MAP: Dict[str, Tuple[int, str]] = {
        "1min": (1, "minute"),
        "minute": (1, "minute"),
        "3min": (3, "minute"),
        "3minute": (3, "minute"),
        "5min": (5, "minute"),
        "5minute": (5, "minute"),
        "10min": (10, "minute"),
        "10minute": (10, "minute"),
        "15min": (15, "minute"),
        "15minute": (15, "minute"),
        "30min": (30, "minute"),
        "30minute": (30, "minute"),
        "60min": (60, "minute"),
        "60minute": (60, "minute"),
        "1h": (60, "minute"),
        "1d": (1, "day"),
        "day": (1, "day"),
        "1w": (1, "week"),
        "week": (1, "week"),
        "1m": (1, "month"),
        "month": (1, "month"),
    }

    def __init__(self, api_key: str, timeout_s: float = 10.0):
        if not api_key or not api_key.strip():
            raise ValueError("Polygon API key is required")
        self.api_key = api_key.strip()
        self.timeout_s = float(timeout_s)
        self.session = requests.Session()

    def get_agg_bars(
        self,
        symbol: str,
        from_date: datetime,
        to_date: datetime,
        interval: str,
        adjusted: bool = True,
        sort: str = "asc",
        limit: int = 50000,
        max_retries: int = 4,
    ) -> List[Dict[str, Any]]:
        multiplier, timespan = self._map_interval(interval)
        start = from_date.strftime("%Y-%m-%d")
        end = to_date.strftime("%Y-%m-%d")

        url = (
            f"{self.BASE_URL}/v2/aggs/ticker/{symbol.upper()}"
            f"/range/{multiplier}/{timespan}/{start}/{end}"
        )
        params = {
            "adjusted": str(bool(adjusted)).lower(),
            "sort": sort,
            "limit": int(limit),
            "apiKey": self.api_key,
        }

        payload = self._request_with_retry(url, params=params, max_retries=max_retries)
        return payload.get("results", []) if isinstance(payload, dict) else []

    def _map_interval(self, interval: str) -> Tuple[int, str]:
        mapped = self.INTERVAL_MAP.get((interval or "").strip())
        if not mapped:
            raise ValueError(f"Unsupported interval: {interval}")
        return mapped

    def _request_with_retry(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        max_retries: int = 4,
    ) -> Dict[str, Any]:
        last_exc: Optional[Exception] = None

        for attempt in range(max_retries):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout_s)

                if resp.status_code == 429:
                    raise requests.HTTPError("429 rate-limited", response=resp)

                resp.raise_for_status()
                return resp.json()

            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                last_exc = exc
                is_retryable_http = (
                    isinstance(exc, requests.HTTPError)
                    and getattr(exc, "response", None) is not None
                    and exc.response.status_code in {429, 500, 502, 503, 504}
                )
                is_retryable = isinstance(exc, (requests.Timeout, requests.ConnectionError)) or is_retryable_http

                if (not is_retryable) or attempt >= max_retries - 1:
                    break

                backoff_s = (2 ** attempt) * 0.5
                jitter_s = random.uniform(0, 0.25)
                sleep_s = backoff_s + jitter_s
                logger.warning(
                    "Polygon request retry %s/%s in %.2fs for %s (%s)",
                    attempt + 1,
                    max_retries,
                    sleep_s,
                    url,
                    exc,
                )
                time.sleep(sleep_s)

        raise RuntimeError(f"Polygon request failed after {max_retries} attempts: {url}") from last_exc
