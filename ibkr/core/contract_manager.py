"""Contract translation layer for IBKR."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass
class ContractManager:
    ib: Any
    default_exchange: str = "SMART"
    default_currency: str = "USD"
    _cache: Dict[Tuple[str, str, str], Any] = field(default_factory=dict)

    def resolve_stock(self, symbol: str, exchange: Optional[str] = None, currency: Optional[str] = None) -> Any:
        from ib_insync import Stock

        ex = exchange or self.default_exchange
        curr = currency or self.default_currency
        key = (symbol.upper(), ex.upper(), curr.upper())
        if key in self._cache:
            return self._cache[key]

        contract = Stock(key[0], key[1], key[2])
        qualified = self.ib.qualifyContracts(contract)
        if not qualified:
            raise ValueError(f"Unable to qualify contract for {symbol}")

        self._cache[key] = qualified[0]
        return qualified[0]
