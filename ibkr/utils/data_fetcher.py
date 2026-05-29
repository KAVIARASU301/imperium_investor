# ibkr/utils/data_fetcher.py
"""Data fetcher for IBKR using ib_insync"""

import logging
from typing import Dict, List, Any, Optional
from datetime import timedelta
from ibkr.utils.market_time import market_now
from ib_insync import Contract, Stock, Option, Future, Forex, MarketOrder, LimitOrder, util as ib_util
import pandas as pd

logger = logging.getLogger(__name__)


class IBKRDataFetcher:
    """Handles all data fetching operations for IBKR"""

    def __init__(self, ib_client):
        self.ib = ib_client
        self._subscribed_contracts = {}

    def create_stock_contract(self, symbol: str, exchange: str = "SMART") -> Contract:
        """Create a stock contract"""
        return Stock(symbol, exchange, "USD")

    def create_option_contract(self, symbol: str, expiry: str, strike: float,
                               right: str, exchange: str = "SMART") -> Contract:
        """Create an option contract"""
        return Option(symbol, expiry, strike, right, exchange)

    async def get_quote(self, symbol: str) -> Dict[str, Any]:
        """Get real-time quote for a symbol"""
        try:
            contract = self.create_stock_contract(symbol)
            ticker = self.ib.reqMktData(contract)

            # Wait for data
            await self.ib.sleep(0.5)

            return {
                'symbol': symbol,
                'last_price': ticker.last if ticker.last else ticker.close,
                'bid': ticker.bid,
                'ask': ticker.ask,
                'volume': ticker.volume,
                'open': ticker.open,
                'high': ticker.high,
                'low': ticker.low,
                'close': ticker.close,
                'timestamp': market_now()
            }
        except Exception as e:
            logger.error(f"Error fetching quote for {symbol}: {e}")
            return {}

    async def get_historical_data(self, symbol: str, duration: str = "1 D",
                                  bar_size: str = "5 mins") -> List[Dict[str, Any]]:
        """Get historical data for a symbol"""
        try:
            contract = self.create_stock_contract(symbol)

            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime='',
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow='TRADES',
                useRTH=True,
                formatDate=1
            )

            df = ib_util.df(bars)
            if df.empty:
                return []

            # Keep only payload columns and vectorize datetime normalization.
            payload_columns = ['date', 'open', 'high', 'low', 'close', 'volume']
            df = df.reindex(columns=payload_columns)
            df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')
            df['date'] = df['date'].fillna('')

            return df.to_dict('records')

        except Exception as e:
            logger.error(f"Error fetching historical data for {symbol}: {e}")
            return []

    def get_positions(self) -> List[Dict[str, Any]]:
        """Get current positions"""
        try:
            positions = self.ib.positions()
            return [{
                'symbol': pos.contract.symbol,
                'quantity': pos.position,
                'average_price': pos.avgCost,
                'current_price': 0,  # Will be updated with market data
                'pnl': 0,
                'exchange': pos.contract.exchange,
                'contract': pos.contract
            } for pos in positions]
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return []

    def get_orders(self) -> List[Dict[str, Any]]:
        """Get open orders"""
        try:
            trades = self.ib.openTrades()
            return [{
                'order_id': trade.order.orderId,
                'symbol': trade.contract.symbol,
                'quantity': trade.order.totalQuantity,
                'order_type': trade.order.orderType,
                'limit_price': trade.order.lmtPrice if hasattr(trade.order, 'lmtPrice') else None,
                'status': trade.orderStatus.status,
                'filled': trade.orderStatus.filled,
                'remaining': trade.orderStatus.remaining,
                'action': trade.order.action
            } for trade in trades]
        except Exception as e:
            logger.error(f"Error fetching orders: {e}")
            return []

    async def get_current_price(self, symbol: str) -> float:
        """Gets the last traded price for a symbol."""
        try:
            contract = Stock(symbol, 'SMART', 'USD')
            self.ib.reqMktData(contract, '', False, False)
            await self.ib.sleep(1) # Allow time for data to arrive
            ticker = self.ib.ticker(contract)
            return ticker.last
        except Exception as e:
            logger.error(f"Error fetching current price for {symbol}: {e}")
            return 0.0