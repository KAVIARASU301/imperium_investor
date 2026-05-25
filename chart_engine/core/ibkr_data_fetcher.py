from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from chart_engine.core.broker_protocol import BarData, BrokerCapabilities, BrokerDataFetcher

logger = logging.getLogger(__name__)

IBKR_INTERVAL_MAP: Dict[str, str] = {
    "1min": "1 min", "minute": "1 min", "3min": "3 mins", "3minute": "3 mins",
    "5min": "5 mins", "5minute": "5 mins", "10min": "10 mins", "10minute": "10 mins",
    "15min": "15 mins", "15minute": "15 mins", "30min": "30 mins", "30minute": "30 mins",
    "60min": "1 hour", "60minute": "1 hour", "1h": "1 hour", "1d": "1 day", "day": "1 day",
    "1w": "1 week", "week": "1 week", "1M": "1 month", "month": "1 month",
}


class IBKRDataFetcher(BrokerDataFetcher):
    def __init__(self, ib_client, what_to_show: str = "TRADES", use_rth: bool = True):
        self._ib = ib_client
        self._what_to_show = what_to_show
        self._use_rth = use_rth

    @property
    def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            name="ibkr",
            exchange_tz="America/New_York",
            currency="USD",
            supports_options=True,
            supports_greeks=True,
            supports_level2=True,
        )

    def fetch(self, symbol: str, instrument_token: Any, from_date: datetime, to_date: datetime, interval: str) -> List[BarData]:
        from ib_insync import Contract, Stock

        bar_size = IBKR_INTERVAL_MAP.get(interval, "1 day")
        duration_str = self._compute_duration(from_date, to_date, bar_size)
        end_dt_str = to_date.strftime("%Y%m%d %H:%M:%S UTC")

        # Build contract — prefer conId if we have it, fall back to symbol lookup
        if instrument_token and int(instrument_token) > 0:
            contract = Contract()
            contract.conId = int(instrument_token)
            contract.exchange = "SMART"
        else:
            # Qualify by symbol
            contract = Stock(symbol, "SMART", "USD")
            qualified = self._ib.qualifyContracts(contract)
            if not qualified:
                raise ValueError(f"Could not qualify contract for {symbol}")
            contract = qualified[0]

        bars = self._ib.reqHistoricalData(
            contract,
            endDateTime=end_dt_str,
            durationStr=duration_str,
            barSizeSetting=bar_size,
            whatToShow=self._what_to_show,
            useRTH=self._use_rth,
            formatDate=1,
            keepUpToDate=False,
        )
        if not bars:
            raise ValueError(f"No data returned for {symbol} [{bar_size}]")

        return [self._bar_to_bardata(b) for b in bars]

    def resolve_instrument(self, symbol: str):
        from ib_insync import Stock

        try:
            details = self._ib.reqContractDetails(Stock(symbol, "SMART", "USD"))
            if details:
                return details[0].contract
        except Exception as exc:
            logger.error("IBKR resolve_instrument failed for %s: %s", symbol, exc)
        return None

    @staticmethod
    def _bar_to_bardata(bar) -> BarData:
        raw_date = bar.date
        if isinstance(raw_date, str):
            dt = datetime.strptime(raw_date, "%Y%m%d").replace(tzinfo=timezone.utc)
        elif hasattr(raw_date, "tzinfo") and raw_date.tzinfo is None:
            dt = raw_date.replace(tzinfo=timezone.utc)
        else:
            dt = raw_date

        return BarData(
            time=dt,
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=float(getattr(bar, "volume", 0) or 0),
        )

    @staticmethod
    def _compute_duration(from_date: datetime, to_date: datetime, bar_size: str) -> str:
        days = max(1, (to_date - from_date).days + 1)
        if bar_size in ("1 week", "1 month"):
            return f"{min(max(1, (days // 365) + 1), 10)} Y"
        if bar_size == "1 day":
            return "1 Y"
        return f"{min(days, 30)} D"
