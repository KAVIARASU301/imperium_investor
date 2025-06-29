# src/utils/data_models.py

"""Data models for Swing Trading application - Stocks Only"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class Contract:
    """Represents a stock contract (simplified for swing trading)"""
    symbol: str  # Base symbol (e.g., "RELIANCE")
    tradingsymbol: str  # Full trading symbol (e.g., "RELIANCE")
    instrument_token: int  # Unique token for market data
    lot_size: int = 1  # Always 1 for stocks

    # Stock-specific fields (no options complexity)
    strike: float = 0  # Not applicable for stocks
    option_type: str = ""  # Not applicable for stocks
    expiry: Optional[date] = None  # Not applicable for stocks

    # Market data fields
    ltp: float = 0.0
    volume: int = 0
    bid: float = 0.0
    ask: float = 0.0

    # Additional stock info
    exchange: str = "NSE"
    segment: str = "EQ"
    instrument_type: str = "EQ"


@dataclass
class Position:
    """Represents an open stock position"""
    symbol: str  # Base symbol
    tradingsymbol: str  # Full trading symbol
    quantity: int  # Position quantity (positive for long, negative for short)
    average_price: float  # Average entry price
    ltp: float  # Last traded price
    pnl: float  # Current unrealized P&L
    contract: Contract  # Associated contract
    order_id: Optional[str]  # Associated order ID
    exchange: str = "NSE"  # Exchange (NSE/BSE)
    product: str = "MIS"  # Product type (MIS/NRML/CNC)

    # Enhanced fields for swing trading
    stop_loss_price: Optional[float] = field(default=None)
    target_price: Optional[float] = field(default=None)
    stop_loss_order_id: Optional[str] = field(default=None)
    target_order_id: Optional[str] = field(default=None)

    # Position metrics
    entry_time: Optional[str] = field(default=None)
    position_type: str = field(default="LONG")  # LONG/SHORT

    def update_pnl(self, new_ltp: float):
        """Recalculate P&L based on updated LTP"""
        self.ltp = new_ltp
        self.pnl = (new_ltp - self.average_price) * self.quantity

    @property
    def investment(self) -> float:
        """Calculate total investment (always positive)"""
        return abs(self.quantity * self.average_price)

    @property
    def market_value(self) -> float:
        """Calculate current market value (always positive)"""
        return abs(self.quantity * self.ltp)

    @property
    def pnl_percent(self) -> float:
        """Calculate P&L percentage"""
        if self.average_price == 0:
            return 0.0
        return ((self.ltp - self.average_price) / self.average_price) * 100

    @property
    def is_profitable(self) -> bool:
        """Check if position is currently profitable"""
        return self.pnl > 0

    @property
    def is_long(self) -> bool:
        """Check if position is long"""
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        """Check if position is short"""
        return self.quantity < 0


@dataclass
class OrderData:
    """Represents order data for swing trading"""
    tradingsymbol: str
    transaction_type: str  # BUY/SELL
    quantity: int
    order_type: str  # MARKET/LIMIT/SL/SL-M
    product: str  # MIS/NRML/CNC
    exchange: str = "NSE"
    variety: str = "regular"
    validity: str = "DAY"

    # Price fields
    price: Optional[float] = None  # Limit price
    trigger_price: Optional[float] = None  # Stop loss trigger price
    disclosed_quantity: int = 0

    # Additional fields
    tag: str = ""
    order_source: str = "manual"

    def to_dict(self) -> dict:
        """Convert to dictionary for API calls"""
        order_dict = {
            "tradingsymbol": self.tradingsymbol,
            "transaction_type": self.transaction_type,
            "quantity": self.quantity,
            "order_type": self.order_type,
            "product": self.product,
            "exchange": self.exchange,
            "variety": self.variety,
            "validity": self.validity,
            "disclosed_quantity": self.disclosed_quantity,
            "tag": self.tag
        }

        # Add price fields if present
        if self.price is not None:
            order_dict["price"] = self.price
        if self.trigger_price is not None:
            order_dict["trigger_price"] = self.trigger_price

        return order_dict


@dataclass
class WatchlistItem:
    """Represents an item in a watchlist"""
    symbol: str
    tradingsymbol: str
    instrument_token: int
    exchange: str = "NSE"
    ltp: float = 0.0
    change: float = 0.0
    change_percent: float = 0.0
    volume: int = 0

    # Additional fields for analysis
    notes: str = ""
    alert_price: Optional[float] = None
    is_favorite: bool = False

    @property
    def is_gaining(self) -> bool:
        """Check if stock is gaining"""
        return self.change > 0

    @property
    def is_losing(self) -> bool:
        """Check if stock is losing"""
        return self.change < 0


@dataclass
class TradeRecord:
    """Represents a completed trade record"""
    symbol: str
    entry_price: float
    exit_price: float
    quantity: int
    entry_time: str
    exit_time: str
    pnl: float
    product: str
    exchange: str

    # Trade analysis
    trade_type: str = "LONG"  # LONG/SHORT
    hold_duration: Optional[str] = None
    max_profit: float = 0.0
    max_loss: float = 0.0

    @property
    def is_winning_trade(self) -> bool:
        """Check if trade was profitable"""
        return self.pnl > 0

    @property
    def return_percent(self) -> float:
        """Calculate return percentage"""
        if self.entry_price == 0:
            return 0.0
        return (self.pnl / (self.quantity * self.entry_price)) * 100


@dataclass
class RiskMetrics:
    """Risk management metrics"""
    position_count: int = 0
    total_exposure: float = 0.0
    max_position_size: float = 0.0
    portfolio_concentration: float = 0.0
    total_unrealized_pnl: float = 0.0
    total_realized_pnl: float = 0.0
    max_drawdown: float = 0.0

    # Risk limits
    max_positions: int = 20
    max_concentration_percent: float = 25.0
    max_daily_loss: float = 50000.0
    max_position_risk: float = 10000.0

    def is_within_limits(self) -> bool:
        """Check if current metrics are within risk limits"""
        return (
                self.position_count <= self.max_positions and
                self.portfolio_concentration <= self.max_concentration_percent and
                abs(self.total_realized_pnl) <= self.max_daily_loss
        )

    def get_risk_alerts(self) -> list:
        """Get list of current risk alerts"""
        alerts = []

        if self.position_count > self.max_positions:
            alerts.append(f"Position limit exceeded: {self.position_count}/{self.max_positions}")

        if self.portfolio_concentration > self.max_concentration_percent:
            alerts.append(f"High concentration: {self.portfolio_concentration:.1f}%")

        if abs(self.total_realized_pnl) > self.max_daily_loss:
            alerts.append(f"Daily loss limit exceeded: ₹{abs(self.total_realized_pnl):,.2f}")

        return alerts


@dataclass
class PerformanceMetrics:
    """Trading performance metrics"""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    max_profit: float = 0.0
    max_loss: float = 0.0

    # Calculated metrics
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0

    # Streak tracking
    current_streak: int = 0
    max_winning_streak: int = 0
    max_losing_streak: int = 0

    def calculate_metrics(self):
        """Calculate derived performance metrics"""
        if self.total_trades > 0:
            self.win_rate = (self.winning_trades / self.total_trades) * 100

        # Additional calculations would be done here
        # based on trade history data

    @property
    def is_profitable(self) -> bool:
        """Check if overall trading is profitable"""
        return self.total_pnl > 0

    @property
    def average_trade(self) -> float:
        """Calculate average trade P&L"""
        if self.total_trades == 0:
            return 0.0
        return self.total_pnl / self.total_trades


# Utility functions for data models

def create_stock_contract(symbol: str, instrument_token: int, exchange: str = "NSE") -> Contract:
    """Create a simple stock contract"""
    return Contract(
        symbol=symbol,
        tradingsymbol=symbol,
        instrument_token=instrument_token,
        lot_size=1,
        exchange=exchange,
        segment="EQ",
        instrument_type="EQ"
    )


def create_market_order(symbol: str, transaction_type: str, quantity: int,
                        product: str = "MIS", exchange: str = "NSE") -> OrderData:
    """Create a market order"""
    return OrderData(
        tradingsymbol=symbol,
        transaction_type=transaction_type,
        quantity=quantity,
        order_type="MARKET",
        product=product,
        exchange=exchange
    )


def create_limit_order(symbol: str, transaction_type: str, quantity: int,
                       price: float, product: str = "MIS", exchange: str = "NSE") -> OrderData:
    """Create a limit order"""
    return OrderData(
        tradingsymbol=symbol,
        transaction_type=transaction_type,
        quantity=quantity,
        order_type="LIMIT",
        product=product,
        exchange=exchange,
        price=price
    )


def create_stop_loss_order(symbol: str, transaction_type: str, quantity: int,
                           trigger_price: float, product: str = "MIS",
                           exchange: str = "NSE") -> OrderData:
    """Create a stop loss order"""
    return OrderData(
        tradingsymbol=symbol,
        transaction_type=transaction_type,
        quantity=quantity,
        order_type="SL-M",
        product=product,
        exchange=exchange,
        trigger_price=trigger_price
    )