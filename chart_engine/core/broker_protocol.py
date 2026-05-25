from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional


@dataclass
class BarData:
    """Single OHLCV bar in canonical chart-engine format."""
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class BrokerCapabilities:
    name: str
    exchange_tz: str
    currency: str
    supports_options: bool = False
    supports_greeks: bool = False
    supports_level2: bool = False


class BrokerDataFetcher(ABC):
    @property
    @abstractmethod
    def capabilities(self) -> BrokerCapabilities:
        ...

    @abstractmethod
    def fetch(
        self,
        symbol: str,
        instrument_token: Any,
        from_date: datetime,
        to_date: datetime,
        interval: str,
    ) -> List[BarData]:
        ...

    @abstractmethod
    def resolve_instrument(self, symbol: str) -> Optional[Any]:
        ...
