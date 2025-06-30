# ibkr/utils/data_models.py

from dataclasses import dataclass, field
from typing import Optional

@dataclass
class StockContract:
    """Represents an IBKR stock contract."""
    symbol: str
    exchange: str = 'SMART'
    currency: str = 'USD'

    def to_ib_contract(self):
        """Converts to an ib_insync Contract object."""
        from ib_insync import Stock
        return Stock(self.symbol, self.exchange, self.currency)

@dataclass
class Position:
    """Represents an open stock position from IBKR."""
    symbol: str
    quantity: int
    average_price: float
    ltp: float = 0.0
    pnl: float = 0.0
    market_value: float = 0.0
    contract: StockContract = None # The associated contract

    def update_pnl(self, new_ltp: float):
        """Recalculates P&L based on updated LTP."""
        self.ltp = new_ltp
        self.market_value = self.quantity * new_ltp
        if self.quantity != 0:
            self.pnl = (new_ltp - self.average_price) * self.quantity

@dataclass
class OrderData:
    """Represents order data for placing an order with IBKR."""
    symbol: str
    quantity: int
    action: str  # 'BUY' or 'SELL'
    order_type: str  # 'MKT', 'LMT', etc.
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None

    def to_ib_order(self):
        """Converts to an ib_insync Order object."""
        from ib_insync import MarketOrder, LimitOrder, StopOrder

        if self.order_type == 'MKT':
            return MarketOrder(self.action, self.quantity)
        elif self.order_type == 'LMT':
            return LimitOrder(self.action, self.quantity, self.limit_price)
        elif self.order_type == 'STP':
            return StopOrder(self.action, self.quantity, self.stop_price)
        else:
            raise ValueError(f"Unsupported order type: {self.order_type}")