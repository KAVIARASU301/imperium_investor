"""High-level IBKR trading client services."""

from __future__ import annotations

from typing import Any, Dict, Optional

from ibkr.core.contract_manager import ContractManager


class IBKRTradingClient:
    def __init__(self, ib: Any, default_exchange: str = "SMART", default_currency: str = "USD"):
        self.ib = ib
        self.contracts = ContractManager(ib, default_exchange=default_exchange, default_currency=default_currency)

    def place_order(
        self,
        symbol: str,
        quantity: float,
        side: str,
        order_type: str = "MKT",
        price: Optional[float] = None,
        exchange: Optional[str] = None,
        currency: Optional[str] = None,
    ) -> Dict[str, Any]:
        from ib_insync import LimitOrder, MarketOrder

        contract = self.contracts.resolve_stock(symbol, exchange=exchange, currency=currency)
        if order_type.upper() in {"LMT", "LIMIT"}:
            order = LimitOrder(side.upper(), quantity, price)
        else:
            order = MarketOrder(side.upper(), quantity)

        trade = self.ib.placeOrder(contract, order)
        return {
            "order_id": getattr(getattr(trade, "order", None), "orderId", None),
            "status": "SUBMITTED",
            "symbol": symbol,
        }
