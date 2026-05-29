# ibkr/utils/paper_trading_manager.py
"""
Paper trading manager for IBKR integration.
Simulates trading without real money for testing and development.
"""

import logging
import json
import random
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from pathlib import Path

from PySide6.QtCore import QObject, Signal, QTimer
from ibkr.utils.market_time import market_isoformat, market_now_naive

logger = logging.getLogger(__name__)


@dataclass
class PaperPosition:
    """Represents a paper trading position"""
    symbol: str
    quantity: int
    average_price: float
    current_price: float
    exchange: str = "SMART"
    currency: str = "USD"
    entry_time: str = ""

    @property
    def market_value(self) -> float:
        return self.quantity * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.average_price) * self.quantity

    @property
    def unrealized_pnl_percent(self) -> float:
        if self.average_price == 0:
            return 0
        return (self.unrealized_pnl / (self.average_price * abs(self.quantity))) * 100


@dataclass
class PaperOrder:
    """Represents a paper trading order"""
    order_id: str
    symbol: str
    action: str  # BUY or SELL
    quantity: int
    order_type: str  # MKT, LMT, STP
    status: str  # SUBMITTED, FILLED, CANCELLED
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    filled_quantity: int = 0
    average_fill_price: float = 0.0
    order_time: str = ""
    fill_time: Optional[str] = None
    exchange: str = "SMART"
    currency: str = "USD"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class IBKRPaperTradingManager(QObject):
    """
    Paper trading manager that simulates IBKR trading functionality.
    Provides realistic order execution simulation with configurable parameters.
    """

    # Signals for UI updates
    order_filled = Signal(dict)
    position_updated = Signal(dict)
    account_updated = Signal(dict)

    def __init__(self, initial_balance: float = 1000000.0):
        super().__init__()
        self.initial_balance = initial_balance
        self.current_balance = initial_balance
        self.positions: Dict[str, PaperPosition] = {}
        self.orders: Dict[str, PaperOrder] = {}
        self.order_counter = 1
        self.commissions = 1.0  # $1 per trade
        self.slippage = 0.01  # 1 cent slippage simulation

        # Market data simulation
        self.market_data: Dict[str, Dict[str, float]] = {}
        self.price_volatility = 0.02  # 2% random price movement

        # Configuration
        self.data_file = Path("paper_trading_data.json")
        self.auto_fill_delay = 100  # ms delay for market orders

        # Timer for order processing
        self.order_timer = QTimer()
        self.order_timer.timeout.connect(self._process_pending_orders)
        self.order_timer.start(1000)  # Check every second

        # Timer for price updates
        self.price_timer = QTimer()
        self.price_timer.timeout.connect(self._update_market_prices)
        self.price_timer.start(5000)  # Update every 5 seconds

        self._load_data()

    def _load_data(self):
        """Load saved paper trading data"""
        try:
            if self.data_file.exists():
                with open(self.data_file, 'r') as f:
                    data = json.load(f)

                self.current_balance = data.get('balance', self.initial_balance)

                # Load positions
                for pos_data in data.get('positions', []):
                    pos = PaperPosition(**pos_data)
                    self.positions[pos.symbol] = pos

                # Load orders
                for order_data in data.get('orders', []):
                    order = PaperOrder(**order_data)
                    self.orders[order.order_id] = order

                self.order_counter = data.get('order_counter', 1)

                logger.info("Loaded paper trading data")
        except Exception as e:
            logger.error(f"Error loading paper trading data: {e}")

    def _save_data(self):
        """Save paper trading data"""
        try:
            data = {
                'balance': self.current_balance,
                'positions': [asdict(pos) for pos in self.positions.values()],
                'orders': [order.to_dict() for order in self.orders.values()],
                'order_counter': self.order_counter,
                'last_updated': market_isoformat()
            }

            with open(self.data_file, 'w') as f:
                json.dump(data, f, indent=2)

        except Exception as e:
            logger.error(f"Error saving paper trading data: {e}")

    def _get_current_price(self, symbol: str) -> float:
        """Get current market price for symbol (simulated)"""
        if symbol in self.market_data:
            return self.market_data[symbol]['price']

        # Simulate initial price for new symbols
        base_price = random.uniform(10, 500)  # Random price between $10-$500
        self.market_data[symbol] = {
            'price': base_price,
            'last_update': market_isoformat()
        }
        return base_price

    def _update_market_prices(self):
        """Simulate market price movements"""
        for symbol in self.market_data:
            current_price = self.market_data[symbol]['price']

            # Random price movement
            change = random.uniform(-self.price_volatility, self.price_volatility)
            new_price = current_price * (1 + change)
            new_price = max(0.01, new_price)  # Minimum price $0.01

            self.market_data[symbol]['price'] = new_price
            self.market_data[symbol]['last_update'] = market_isoformat()

        # Update position current prices
        for position in self.positions.values():
            position.current_price = self._get_current_price(position.symbol)

    def _process_pending_orders(self):
        """Process pending orders and simulate fills"""
        for order in list(self.orders.values()):
            if order.status == "SUBMITTED":
                self._try_fill_order(order)

    def _try_fill_order(self, order: PaperOrder):
        """Attempt to fill an order based on current market conditions"""
        current_price = self._get_current_price(order.symbol)
        should_fill = False
        fill_price = current_price

        if order.order_type == "MKT":
            # Market orders fill immediately
            should_fill = True
            # Add slippage
            if order.action == "BUY":
                fill_price += self.slippage
            else:
                fill_price -= self.slippage

        elif order.order_type == "LMT" and order.limit_price:
            # Limit orders fill when price reaches limit
            if order.action == "BUY" and current_price <= order.limit_price:
                should_fill = True
                fill_price = order.limit_price
            elif order.action == "SELL" and current_price >= order.limit_price:
                should_fill = True
                fill_price = order.limit_price

        elif order.order_type == "STP" and order.stop_price:
            # Stop orders become market orders when triggered
            if order.action == "BUY" and current_price >= order.stop_price:
                should_fill = True
                fill_price = current_price + self.slippage
            elif order.action == "SELL" and current_price <= order.stop_price:
                should_fill = True
                fill_price = current_price - self.slippage

        if should_fill:
            self._fill_order(order, fill_price)

    def _fill_order(self, order: PaperOrder, fill_price: float):
        """Execute order fill"""
        try:
            # Calculate total cost including commission
            total_cost = order.quantity * fill_price + self.commissions

            # Check if we have sufficient funds for buy orders
            if order.action == "BUY" and total_cost > self.current_balance:
                order.status = "REJECTED"
                logger.warning(f"Order {order.order_id} rejected: Insufficient funds")
                return

            # Update order
            order.status = "FILLED"
            order.filled_quantity = order.quantity
            order.average_fill_price = fill_price
            order.fill_time = market_isoformat()

            # Update positions
            self._update_position(order.symbol, order.action, order.quantity, fill_price)

            # Update account balance
            if order.action == "BUY":
                self.current_balance -= total_cost
            else:
                self.current_balance += (order.quantity * fill_price) - self.commissions

            # Emit signals
            self.order_filled.emit(order.to_dict())

            if order.symbol in self.positions:
                self.position_updated.emit(asdict(self.positions[order.symbol]))

            self.account_updated.emit({
                'balance': self.current_balance,
                'total_pnl': self.get_total_pnl()
            })

            self._save_data()
            logger.info(f"Order {order.order_id} filled at ${fill_price:.2f}")

        except Exception as e:
            logger.error(f"Error filling order {order.order_id}: {e}")
            order.status = "REJECTED"

    def _update_position(self, symbol: str, action: str, quantity: int, price: float):
        """Update position after order fill"""
        if symbol not in self.positions:
            # New position
            if action == "BUY":
                self.positions[symbol] = PaperPosition(
                    symbol=symbol,
                    quantity=quantity,
                    average_price=price,
                    current_price=price,
                    entry_time=market_isoformat()
                )
            else:
                # Short position
                self.positions[symbol] = PaperPosition(
                    symbol=symbol,
                    quantity=-quantity,
                    average_price=price,
                    current_price=price,
                    entry_time=market_isoformat()
                )
        else:
            # Existing position
            position = self.positions[symbol]

            if action == "BUY":
                if position.quantity < 0:
                    # Covering short
                    if quantity >= abs(position.quantity):
                        # Complete cover + new long
                        remaining = quantity - abs(position.quantity)
                        if remaining > 0:
                            position.quantity = remaining
                            position.average_price = price
                        else:
                            del self.positions[symbol]
                    else:
                        # Partial cover
                        position.quantity += quantity
                else:
                    # Adding to long position
                    total_cost = (position.quantity * position.average_price) + (quantity * price)
                    position.quantity += quantity
                    position.average_price = total_cost / position.quantity
            else:  # SELL
                if position.quantity > 0:
                    # Selling long
                    if quantity >= position.quantity:
                        # Complete sale + new short
                        remaining = quantity - position.quantity
                        if remaining > 0:
                            position.quantity = -remaining
                            position.average_price = price
                        else:
                            del self.positions[symbol]
                    else:
                        # Partial sale
                        position.quantity -= quantity
                else:
                    # Adding to short position
                    total_cost = (abs(position.quantity) * position.average_price) + (quantity * price)
                    position.quantity -= quantity
                    position.average_price = total_cost / abs(position.quantity)

    # Public API methods

    def place_order(self, symbol: str, action: str, quantity: int,
                    order_type: str = "MKT", price: Optional[float] = None,
                    **kwargs) -> Dict[str, Any]:
        """Place a paper trading order"""
        try:
            order_id = f"PAPER_{self.order_counter:06d}"
            self.order_counter += 1

            order = PaperOrder(
                order_id=order_id,
                symbol=symbol.upper(),
                action=action.upper(),
                quantity=quantity,
                order_type=order_type.upper(),
                status="SUBMITTED",
                limit_price=price if order_type.upper() == "LMT" else None,
                stop_price=price if order_type.upper() == "STP" else None,
                order_time=market_isoformat()
            )

            self.orders[order_id] = order

            # Market orders fill immediately
            if order_type.upper() == "MKT":
                QTimer.singleShot(self.auto_fill_delay, lambda: self._try_fill_order(order))

            return {
                'order_id': order_id,
                'status': 'SUBMITTED',
                'symbol': symbol,
                'quantity': quantity,
                'order_type': order_type,
                'action': action,
                'price': price,
                'timestamp': order.order_time
            }

        except Exception as e:
            logger.error(f"Error placing paper order: {e}")
            return {'error': str(e)}

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel a pending order"""
        try:
            if order_id in self.orders:
                order = self.orders[order_id]
                if order.status == "SUBMITTED":
                    order.status = "CANCELLED"
                    self._save_data()
                    return {'status': 'CANCELLED', 'order_id': order_id}
                else:
                    return {'error': f'Order {order_id} cannot be cancelled (status: {order.status})'}
            else:
                return {'error': f'Order {order_id} not found'}
        except Exception as e:
            return {'error': str(e)}

    def get_positions(self) -> List[Dict[str, Any]]:
        """Get current positions"""
        positions = []
        for position in self.positions.values():
            # Update current price
            position.current_price = self._get_current_price(position.symbol)

            positions.append({
                'tradingsymbol': position.symbol,
                'exchange': position.exchange,
                'quantity': position.quantity,
                'average_price': position.average_price,
                'current_price': position.current_price,
                'pnl': position.unrealized_pnl,
                'pnl_percent': position.unrealized_pnl_percent,
                'market_value': position.market_value,
                'product': 'PAPER',
                'entry_time': position.entry_time
            })

        return positions

    def get_orders(self) -> List[Dict[str, Any]]:
        """Get order history"""
        orders = []
        for order in self.orders.values():
            orders.append({
                'order_id': order.order_id,
                'tradingsymbol': order.symbol,
                'exchange': order.exchange,
                'quantity': order.quantity,
                'filled_quantity': order.filled_quantity,
                'price': order.limit_price or order.stop_price or 0,
                'average_price': order.average_fill_price,
                'status': order.status,
                'order_type': order.order_type,
                'transaction_type': order.action,
                'order_timestamp': order.order_time,
                'fill_timestamp': order.fill_time,
                'product': 'PAPER'
            })

        return orders

    def get_profile(self) -> Dict[str, Any]:
        """Get paper trading account profile"""
        return {
            'user_name': 'Paper Trader',
            'broker': 'IBKR Paper Trading',
            'trading_mode': 'paper',
            'initial_balance': self.initial_balance,
            'current_balance': self.current_balance,
            'total_pnl': self.get_total_pnl(),
            'total_positions': len(self.positions),
            'total_orders': len(self.orders)
        }

    def get_total_pnl(self) -> float:
        """Calculate total unrealized P&L"""
        total_pnl = 0
        for position in self.positions.values():
            position.current_price = self._get_current_price(position.symbol)
            total_pnl += position.unrealized_pnl
        return total_pnl

    def get_account_summary(self) -> Dict[str, Any]:
        """Get detailed account summary"""
        total_pnl = self.get_total_pnl()
        total_market_value = sum(abs(pos.market_value) for pos in self.positions.values())

        return {
            'TotalCashValue': {'value': str(self.current_balance), 'currency': 'USD'},
            'NetLiquidation': {'value': str(self.current_balance + total_pnl), 'currency': 'USD'},
            'UnrealizedPnL': {'value': str(total_pnl), 'currency': 'USD'},
            'GrossPositionValue': {'value': str(total_market_value), 'currency': 'USD'},
            'BuyingPower': {'value': str(self.current_balance * 4), 'currency': 'USD'},  # 4:1 margin
            'InitialMarginReq': {'value': str(total_market_value * 0.25), 'currency': 'USD'},
            'MaintMarginReq': {'value': str(total_market_value * 0.25), 'currency': 'USD'},
        }

    def reset_account(self):
        """Reset paper trading account to initial state"""
        self.current_balance = self.initial_balance
        self.positions.clear()
        self.orders.clear()
        self.order_counter = 1
        self.market_data.clear()

        # Delete saved data file
        if self.data_file.exists():
            self.data_file.unlink()

        logger.info("Paper trading account reset")

    def is_connected(self) -> bool:
        """Paper trading is always 'connected'"""
        return True

    def disconnect(self):
        """Stop paper trading timers"""
        if self.order_timer:
            self.order_timer.stop()
        if self.price_timer:
            self.price_timer.stop()
        self._save_data()

    def set_market_price(self, symbol: str, price: float):
        """Manually set market price for a symbol (for testing)"""
        self.market_data[symbol] = {
            'price': price,
            'last_update': market_isoformat()
        }

    def get_market_data(self, symbol: str) -> Dict[str, Any]:
        """Get current market data for symbol"""
        price = self._get_current_price(symbol)

        # Simulate bid/ask spread
        spread = price * 0.001  # 0.1% spread
        bid = price - spread / 2
        ask = price + spread / 2

        return {
            'symbol': symbol,
            'last_price': price,  # Use IBKR format
            'last': price,  # Backward compatibility
            'bid': bid,
            'ask': ask,
            'volume': random.randint(10000, 1000000),
            'open': price * random.uniform(0.98, 1.02),  # Simulate open price
            'high': price * random.uniform(1.0, 1.03),  # Simulate high
            'low': price * random.uniform(0.97, 1.0),  # Simulate low
            'close': price * random.uniform(0.99, 1.01),  # Simulate previous close
            'timestamp': market_isoformat()
        }

    def get_historical_data(self, symbol: str, duration: str = "1 D",
                            bar_size: str = "5 mins") -> List[Dict[str, Any]]:
        """Generate simulated historical data"""
        current_price = self._get_current_price(symbol)
        bars = []

        # Generate 78 bars for 1 day of 5-minute data (6.5 hours * 12)
        num_bars = 78 if duration == "1 D" else 100

        for i in range(num_bars):
            # Random walk for price simulation
            change = random.uniform(-0.02, 0.02)  # ±2% per bar
            open_price = current_price
            close_price = open_price * (1 + change)
            high_price = max(open_price, close_price) * random.uniform(1.0, 1.01)
            low_price = min(open_price, close_price) * random.uniform(0.99, 1.0)

            bars.append({
                'date': (market_now_naive() - timedelta(minutes=5 * (num_bars - i))).isoformat(),
                'open': round(open_price, 2),
                'high': round(high_price, 2),
                'low': round(low_price, 2),
                'close': round(close_price, 2),
                'volume': random.randint(1000, 10000)
            })

            current_price = close_price

        return bars


# Backward-compatibility exports expected by main_window.
PaperTradingManager = IBKRPaperTradingManager


class PaperTradingMixin:
    """Compatibility mixin for windows integrating paper trading callbacks."""


def integrate_paper_trading(window: Any, paper_trader: IBKRPaperTradingManager) -> None:
    """Wire paper-trading hooks into the main window when available."""
    if not window or not paper_trader:
        return

    # Keep integration intentionally defensive: connect only if signals/slots exist.
    try:
        if hasattr(window, "_on_paper_order_filled"):
            paper_trader.order_filled.connect(window._on_paper_order_filled)
        if hasattr(window, "_on_paper_position_updated"):
            paper_trader.position_updated.connect(window._on_paper_position_updated)
        if hasattr(window, "_on_paper_account_updated"):
            paper_trader.account_updated.connect(window._on_paper_account_updated)
    except Exception as exc:
        logger.warning("Paper trading UI integration skipped: %s", exc)


__all__ = [
    "PaperPosition",
    "PaperOrder",
    "IBKRPaperTradingManager",
    "PaperTradingManager",
    "PaperTradingMixin",
    "integrate_paper_trading",
]
