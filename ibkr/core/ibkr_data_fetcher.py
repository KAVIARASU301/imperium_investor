"""IBKR historical data fetcher for chart loading workflows."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from ib_insync import Stock

logger = logging.getLogger(__name__)


class IBKRDataFetcher:
    """Fetch and normalize historical OHLCV bars from IBKR."""

    _INTERVAL_TO_BAR_SIZE = {
        "1minute": "1 min",
        "minute": "1 min",
        "3minute": "3 mins",
        "5minute": "5 mins",
        "10minute": "10 mins",
        "15minute": "15 mins",
        "30minute": "30 mins",
        "60minute": "1 hour",
        "day": "1 day",
        "week": "1 week",
        "month": "1 month",
    }

    _INTERVAL_TO_DURATION = {
        "1minute": "2 D",
        "minute": "2 D",
        "3minute": "5 D",
        "5minute": "10 D",
        "10minute": "20 D",
        "15minute": "30 D",
        "30minute": "30 D",
        "60minute": "5 D",
        "day": "2 Y",
        "week": "5 Y",
        "month": "10 Y",
    }

    def __init__(self, ib: Any, exchange: str = "SMART", currency: str = "USD") -> None:
        self.ib = ib
        self.exchange = exchange
        self.currency = currency
        self._contract_cache: dict[str, Any] = {}

    def _bar_size_for_interval(self, interval: str) -> str:
        return self._INTERVAL_TO_BAR_SIZE.get(interval, "1 day")

    def _duration_for_interval(self, interval: str) -> str:
        return self._INTERVAL_TO_DURATION.get(interval, "2 Y")

    def _qualified_contract_for_symbol(self, symbol: str):
        key = symbol.strip().upper()
        contract = self._contract_cache.get(key)
        if contract is not None:
            return contract

        contract = Stock(key, self.exchange, self.currency)
        qualified = self.ib.qualifyContracts(contract)
        if not qualified:
            raise ValueError(f"Unable to qualify contract for {key}")

        self._contract_cache[key] = qualified[0]
        return qualified[0]

    def fetch_ohlcv(self, symbol: str, interval: str = "day") -> list[dict[str, Any]]:
        """Synchronously fetch IBKR historical bars and return chart-engine friendly rows."""
        try:
            contract = self._qualified_contract_for_symbol(symbol)
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=self._duration_for_interval(interval),
                barSizeSetting=self._bar_size_for_interval(interval),
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )

            normalized: list[dict[str, Any]] = []
            for bar in bars:
                dt = bar.date if isinstance(bar.date, datetime) else datetime.fromisoformat(str(bar.date))
                normalized.append(
                    {
                        "date": dt.strftime("%Y-%m-%d %H:%M:%S"),
                        "open": float(bar.open),
                        "high": float(bar.high),
                        "low": float(bar.low),
                        "close": float(bar.close),
                        "volume": int(bar.volume or 0),
                    }
                )

            return normalized
        except Exception as exc:  # noqa: BLE001
            logger.exception("IBKRDataFetcher.fetch_ohlcv failed for %s/%s: %s", symbol, interval, exc)
            return []

    def get_optimal_date_range(self, interval: str) -> tuple[datetime, datetime]:
        """Compatibility helper for callers expecting date range methods."""
        end = datetime.utcnow()
        days = {
            "day": 730,
            "week": 3650,
            "month": 3650,
            "60minute": 5,
        }.get(interval, 30)
        return end - timedelta(days=days), end
