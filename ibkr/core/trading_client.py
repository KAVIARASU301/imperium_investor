# ibkr/core/trading_client.py
"""
Enhanced IBKR Trading Client with complete order management and market data functionality.
Now includes proper data format conversion for consistency with the application.
"""

import logging
import asyncio
from typing import Dict, List, Any, Optional, Union
from datetime import datetime, timedelta
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal, QTimer, QThread

try:
    from ib_insync import IB, Contract, Stock, Option, Future, Index, Forex
    from ib_insync import MarketOrder, LimitOrder, StopOrder, Order
    from ib_insync import Trade, Position, OrderStatus, Ticker

    IBKR_AVAILABLE = True
except ImportError:
    IBKR_AVAILABLE = False

from login_setup.broker_modes import BrokerMode, TradingMode
from login_setup.broker_factory import BrokerClientInterface

# Import the data converter
from ibkr.utils.data_converter import (
    IBKRDataConverter, convert_ibkr_ticker, convert_ibkr_position,
    convert_ibkr_order, convert_ibkr_historical, prepare_order_for_ibkr
)

logger = logging.getLogger(__name__)


@dataclass
class IBKROrderParams:
    """IBKR-specific order parameters"""
    symbol: str
    exchange: str = "SMART"
    currency: str = "USD"
    action: str = "BUY"  # BUY or SELL
    quantity: int = 100
    order_type: str = "MKT"  # MKT, LMT, STP, etc.
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: str = "DAY"  # DAY, GTC, IOC, etc.
    outside_rth: bool = False


class IBKRTradingClient(QObject, BrokerClientInterface):
    """
    Enhanced IBKR trading client with full order management and market data capabilities.
    Implements the BrokerClientInterface for seamless integration with your app.
    """

    # Qt Signals for real-time updates
    order_status_updated = Signal(dict)
    position_updated = Signal(dict)
    market_data_updated = Signal(dict)
    account_updated = Signal(dict)
    connection_status_changed = Signal(bool)
    error_occurred = Signal(str)

    def __init__(self, ib_client: IB, trading_mode: TradingMode = TradingMode.PAPER):
        super().__init__()
        self.ib = ib_client
        self.trading_mode = trading_mode
        self._connected = False
        self._account_info = {}
        self._positions = {}
        self._orders = {}
        self._subscribed_symbols = set()

        # Set up event handlers
        self._setup_event_handlers()

        # Connection monitoring timer
        self.heartbeat_timer = QTimer()
        self.heartbeat_timer.timeout.connect(self._check_connection)
        self.heartbeat_timer.start(30000)  # Check every 30 seconds

    def _setup_event_handlers(self):
        """Set up IB event handlers for real-time updates"""
        if not self.ib:
            return

        self.ib.orderStatusEvent += self._on_order_status
        self.ib.positionEvent += self._on_position_update
        self.ib.accountValueEvent += self._on_account_update
        self.ib.pendingTickersEvent += self._on_market_data_update
        self.ib.disconnectedEvent += self._on_disconnected

    def _on_order_status(self, order: Trade):
        """Handle order status updates"""
        order_data = {
            'order_id': order.order.orderId,
            'symbol': order.contract.symbol,
            'status': order.orderStatus.status,
            'filled': order.orderStatus.filled,
            'remaining': order.orderStatus.remaining,
            'avg_fill_price': order.orderStatus.avgFillPrice,
            'timestamp': datetime.now()
        }
        self._orders[order.order.orderId] = order_data
        self.order_status_updated.emit(order_data)

    def _on_position_update(self, position: Position):
        """Handle position updates"""
        position_data = {
            'symbol': position.contract.symbol,
            'exchange': position.contract.exchange,
            'quantity': position.position,
            'average_price': position.avgCost,
            'market_price': 0,  # Will be updated with market data
            'unrealized_pnl': 0,
            'realized_pnl': 0
        }
        self._positions[position.contract.symbol] = position_data
        self.position_updated.emit(position_data)

    def _on_account_update(self, account_value):
        """Handle account value updates"""
        self._account_info[account_value.tag] = {
            'value': account_value.value,
            'currency': account_value.currency
        }
        self.account_updated.emit(self._account_info)

    def _on_market_data_update(self, tickers: List[Ticker]):
        """Handle real-time market data updates"""
        for ticker in tickers:
            if ticker.contract and ticker.last is not None:
                # Use data converter for consistent format
                data = convert_ibkr_ticker(ticker)
                self.market_data_updated.emit(data)

    def _on_disconnected(self):
        """Handle disconnection"""
        self._connected = False
        self.connection_status_changed.emit(False)

    def _check_connection(self):
        """Check connection status"""
        if self.ib:
            connected = self.ib.isConnected()
            if connected != self._connected:
                self._connected = connected
                self.connection_status_changed.emit(connected)

    # BrokerClientInterface Implementation

    def get_profile(self) -> Dict[str, Any]:
        """Get account profile information"""
        try:
            accounts = self.ib.managedAccounts()
            account_summary = self.ib.accountSummary()

            profile = {
                'user_name': accounts[0] if accounts else 'Unknown',
                'broker': 'Interactive Brokers',
                'trading_mode': self.trading_mode.value,
                'accounts': accounts,
                'account_summary': {item.tag: item.value for item in account_summary},
                'connection_status': self._connected
            }
            return profile
        except Exception as e:
            logger.error(f"Error getting IBKR profile: {e}")
            return {'error': str(e)}

    def get_positions(self) -> List[Dict[str, Any]]:
        """Get current positions"""
        try:
            positions = self.ib.positions()
            position_list = []

            for pos in positions:
                if pos.position != 0:  # Only non-zero positions
                    # Use data converter for consistent format
                    position_data = convert_ibkr_position(pos)
                    position_list.append(position_data)

            return position_list
        except Exception as e:
            logger.error(f"Error getting IBKR positions: {e}")
            return []

    def place_order(self, **kwargs) -> Dict[str, Any]:
        """
        Place a trading order

        Args:
            symbol: Stock symbol
            action: BUY or SELL
            quantity: Number of shares
            order_type: MKT, LMT, STP, etc.
            price: Limit price (for limit orders)
            exchange: Exchange (default: SMART)
        """
        try:
            # Use data converter to prepare order parameters
            order_params = prepare_order_for_ibkr(kwargs)

            symbol = order_params['symbol']
            action = order_params['action']
            quantity = order_params['quantity']
            order_type = order_params['order_type']
            exchange = order_params['exchange']

            if not symbol or quantity <= 0:
                return {'error': 'Invalid symbol or quantity'}

            # Create contract
            contract = Stock(symbol, exchange, order_params['currency'])

            # Create order based on type
            if order_type in ['MARKET', 'MKT']:
                order = MarketOrder(action, quantity)
            elif order_type in ['LIMIT', 'LMT']:
                limit_price = order_params.get('limit_price')
                if not limit_price:
                    return {'error': 'Limit price required for limit orders'}
                order = LimitOrder(action, quantity, limit_price)
            elif order_type in ['STOP', 'STP']:
                stop_price = order_params.get('stop_price')
                if not stop_price:
                    return {'error': 'Stop price required for stop orders'}
                order = StopOrder(action, quantity, stop_price)
            else:
                return {'error': f'Unsupported order type: {order_type}'}

            # Set additional order properties
            order.tif = order_params.get('time_in_force', 'DAY')
            order.outsideRth = order_params.get('outside_rth', False)

            # Place the order
            trade = self.ib.placeOrder(contract, order)

            # Return order confirmation in standard format
            return {
                'order_id': str(trade.order.orderId),
                'status': 'OPEN',  # Normalize status
                'symbol': symbol,
                'quantity': quantity,
                'order_type': IBKRDataConverter.normalize_order_type(order_type),
                'transaction_type': action,
                'price': order_params.get('limit_price') or order_params.get('stop_price'),
                'timestamp': datetime.now().isoformat(),
                'exchange': exchange,
                'product': 'IBKR'
            }

        except Exception as e:
            logger.error(f"Error placing IBKR order: {e}")
            return {'error': str(e)}

    def get_orders(self) -> List[Dict[str, Any]]:
        """Get order history"""
        try:
            trades = self.ib.trades()
            orders = []

            for trade in trades:
                # Use data converter for consistent format
                order_data = convert_ibkr_order(trade)
                orders.append(order_data)

            return orders
        except Exception as e:
            logger.error(f"Error getting IBKR orders: {e}")
            return []

    def get_instruments(self) -> List[Dict[str, Any]]:
        """
        Get tradeable instruments (simplified - IBKR instruments are requested on-demand)
        """
        # For IBKR, instruments are typically requested dynamically
        # This is a placeholder that returns commonly traded US stocks
        popular_stocks = [
            {'tradingsymbol': 'AAPL', 'name': 'Apple Inc.', 'exchange': 'NASDAQ'},
            {'tradingsymbol': 'GOOGL', 'name': 'Alphabet Inc.', 'exchange': 'NASDAQ'},
            {'tradingsymbol': 'MSFT', 'name': 'Microsoft Corporation', 'exchange': 'NASDAQ'},
            {'tradingsymbol': 'AMZN', 'name': 'Amazon.com Inc.', 'exchange': 'NASDAQ'},
            {'tradingsymbol': 'TSLA', 'name': 'Tesla Inc.', 'exchange': 'NASDAQ'},
            {'tradingsymbol': 'SPY', 'name': 'SPDR S&P 500 ETF', 'exchange': 'NYSE'},
            {'tradingsymbol': 'QQQ', 'name': 'Invesco QQQ Trust', 'exchange': 'NASDAQ'},
        ]
        return popular_stocks

    def is_connected(self) -> bool:
        """Check if client is connected"""
        return self.ib.isConnected() if self.ib else False

    def disconnect(self):
        """Disconnect from IBKR"""
        try:
            if self.heartbeat_timer:
                self.heartbeat_timer.stop()

            if self.ib and self.ib.isConnected():
                self.ib.disconnect()

            self._connected = False
            logger.info("IBKR client disconnected")
        except Exception as e:
            logger.error(f"Error disconnecting IBKR client: {e}")

    # Additional IBKR-specific methods

    def subscribe_market_data(self, symbols: List[str]):
        """Subscribe to real-time market data for symbols"""
        try:
            for symbol in symbols:
                if symbol not in self._subscribed_symbols:
                    contract = Stock(symbol, 'SMART', 'USD')
                    self.ib.reqMktData(contract, '', False, False)
                    self._subscribed_symbols.add(symbol)
                    logger.info(f"Subscribed to market data for {symbol}")
        except Exception as e:
            logger.error(f"Error subscribing to market data: {e}")

    def unsubscribe_market_data(self, symbols: List[str]):
        """Unsubscribe from market data for symbols"""
        try:
            for symbol in symbols:
                if symbol in self._subscribed_symbols:
                    contract = Stock(symbol, 'SMART', 'USD')
                    self.ib.cancelMktData(contract)
                    self._subscribed_symbols.discard(symbol)
                    logger.info(f"Unsubscribed from market data for {symbol}")
        except Exception as e:
            logger.error(f"Error unsubscribing from market data: {e}")

    def get_historical_data(self, symbol: str, duration: str = "1 D",
                            bar_size: str = "5 mins") -> List[Dict[str, Any]]:
        """Get historical data for a symbol"""
        try:
            contract = Stock(symbol, 'SMART', 'USD')

            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime='',
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow='TRADES',
                useRTH=True,
                formatDate=1
            )

            # Use data converter for consistent format
            return convert_ibkr_historical(bars)

        except Exception as e:
            logger.error(f"Error getting historical data for {symbol}: {e}")
            return []

    def cancel_order(self, order_id: int) -> Dict[str, Any]:
        """Cancel an order"""
        try:
            # Find the trade with the given order ID
            for trade in self.ib.trades():
                if trade.order.orderId == order_id:
                    self.ib.cancelOrder(trade.order)
                    return {'status': 'CANCELLED', 'order_id': order_id}

            return {'error': f'Order {order_id} not found'}
        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
            return {'error': str(e)}

    def modify_order(self, order_id: int, **kwargs) -> Dict[str, Any]:
        """Modify an existing order"""
        try:
            # Find the trade with the given order ID
            for trade in self.ib.trades():
                if trade.order.orderId == order_id:
                    # Update order parameters
                    if 'quantity' in kwargs:
                        trade.order.totalQuantity = kwargs['quantity']
                    if 'price' in kwargs and hasattr(trade.order, 'lmtPrice'):
                        trade.order.lmtPrice = kwargs['price']

                    # Place modified order
                    self.ib.placeOrder(trade.contract, trade.order)
                    return {'status': 'MODIFIED', 'order_id': order_id}

            return {'error': f'Order {order_id} not found'}
        except Exception as e:
            logger.error(f"Error modifying order {order_id}: {e}")
            return {'error': str(e)}

    def get_account_summary(self) -> Dict[str, Any]:
        """Get detailed account summary"""
        try:
            summary = self.ib.accountSummary()
            account_data = {}

            for item in summary:
                account_data[item.tag] = {
                    'value': item.value,
                    'currency': item.currency
                }

            return account_data
        except Exception as e:
            logger.error(f"Error getting account summary: {e}")
            return {}

    def search_contracts(self, pattern: str) -> List[Dict[str, Any]]:
        """Search for contracts matching a pattern"""
        try:
            contracts = self.ib.reqMatchingSymbols(pattern)
            results = []

            for contract in contracts:
                results.append({
                    'symbol': contract.symbol,
                    'name': getattr(contract, 'longName', ''),
                    'exchange': contract.exchange,
                    'currency': getattr(contract, 'currency', 'USD'),
                    'sec_type': contract.secType
                })

            return results
        except Exception as e:
            logger.error(f"Error searching contracts for {pattern}: {e}")
            return []

    def get_market_depth(self, symbol: str) -> Dict[str, Any]:
        """Get Level 2 market data (market depth)"""
        try:
            contract = Stock(symbol, 'SMART', 'USD')
            self.ib.reqMktDepth(contract)

            # Wait for data
            self.ib.sleep(1)

            ticker = self.ib.ticker(contract)

            return {
                'symbol': symbol,
                'bid_depth': [{'price': dom.price, 'size': dom.size}
                              for dom in ticker.domBids] if ticker.domBids else [],
                'ask_depth': [{'price': dom.price, 'size': dom.size}
                              for dom in ticker.domAsks] if ticker.domAsks else []
            }
        except Exception as e:
            logger.error(f"Error getting market depth for {symbol}: {e}")
            return {}

    def __getattr__(self, name):
        """Delegate any unhandled attributes to the underlying IB client"""
        if self.ib and hasattr(self.ib, name):
            return getattr(self.ib, name)
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")