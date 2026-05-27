# ibkr/utils/data_converter.py
"""
Data format converter for IBKR to ensure consistency with the application's expected data format.
Handles conversion between IBKR-specific data structures and unified application format.
"""

import logging
from typing import Dict, List, Any, Union, Optional
from datetime import datetime
import math

logger = logging.getLogger(__name__)


class IBKRDataConverter:
    """
    Converter class to standardize IBKR data formats for use in the application.
    Handles NaN values, type conversions, and field mappings.
    """

    @staticmethod
    def is_valid_number(value: Any) -> bool:
        """Check if a value is a valid number (not NaN or None)"""
        if value is None:
            return False
        try:
            return not (math.isnan(float(value)) or math.isinf(float(value)))
        except (ValueError, TypeError):
            return False

    @staticmethod
    def safe_float(value: Any, default: float = 0.0) -> float:
        """Safely convert value to float, handling NaN and None"""
        if IBKRDataConverter.is_valid_number(value):
            return float(value)
        return default

    @staticmethod
    def safe_int(value: Any, default: int = 0) -> int:
        """Safely convert value to int, handling NaN and None"""
        if IBKRDataConverter.is_valid_number(value):
            return int(float(value))
        return default

    @staticmethod
    def convert_ticker_data(ticker) -> Dict[str, Any]:
        """
        Convert IBKR Ticker object to standardized market data format.

        Args:
            ticker: IBKR Ticker object

        Returns:
            Dict with standardized market data fields
        """
        try:
            data = {
                'symbol': ticker.contract.symbol if ticker.contract else '',
                'last_price': IBKRDataConverter.safe_float(ticker.last),
                'last': IBKRDataConverter.safe_float(ticker.last),  # Backward compatibility
                'bid': IBKRDataConverter.safe_float(ticker.bid),
                'ask': IBKRDataConverter.safe_float(ticker.ask),
                'volume': IBKRDataConverter.safe_int(ticker.volume),
                'open': IBKRDataConverter.safe_float(ticker.open),
                'high': IBKRDataConverter.safe_float(ticker.high),
                'low': IBKRDataConverter.safe_float(ticker.low),
                'close': IBKRDataConverter.safe_float(ticker.close),
                'exchange': ticker.contract.exchange if ticker.contract else 'SMART',
                'currency': getattr(ticker.contract, 'currency', 'USD') if ticker.contract else 'USD',
                'timestamp': datetime.now().isoformat(),
                # Additional IBKR-specific fields
                'bid_size': IBKRDataConverter.safe_int(ticker.bidSize),
                'ask_size': IBKRDataConverter.safe_int(ticker.askSize),
                'last_size': IBKRDataConverter.safe_int(ticker.lastSize),
            }

            # Calculate additional fields
            if data['last_price'] > 0 and data['open'] > 0:
                change = data['last_price'] - data['open']
                data['change'] = change
                data['change_percent'] = (change / data['open']) * 100
            else:
                data['change'] = 0.0
                data['change_percent'] = 0.0

            return data

        except Exception as e:
            logger.error(f"Error converting ticker data: {e}")
            return {
                'symbol': '',
                'last_price': 0.0,
                'last': 0.0,
                'bid': 0.0,
                'ask': 0.0,
                'volume': 0,
                'timestamp': datetime.now().isoformat()
            }

    @staticmethod
    def convert_position_data(position) -> Dict[str, Any]:
        """
        Convert IBKR Position object to standardized position format.

        Args:
            position: IBKR Position object

        Returns:
            Dict with standardized position fields
        """
        try:
            avg_cost = IBKRDataConverter.safe_float(position.avgCost)
            quantity = IBKRDataConverter.safe_int(position.position)

            data = {
                'tradingsymbol': position.contract.symbol,
                'exchange': position.contract.exchange,
                'quantity': quantity,
                'average_price': avg_cost,
                'current_price': 0.0,  # Will be updated with market data
                'pnl': IBKRDataConverter.safe_float(position.unrealizedPNL),
                'realized_pnl': IBKRDataConverter.safe_float(getattr(position, 'realizedPNL', 0)),
                'market_value': abs(quantity * avg_cost) if avg_cost > 0 else 0.0,
                'product': 'IBKR',
                'contract_type': position.contract.secType,
                'currency': getattr(position.contract, 'currency', 'USD'),
                # Additional fields for compatibility
                'instrument_token': position.contract.conId if hasattr(position.contract, 'conId') else 0,
                'multiplier': IBKRDataConverter.safe_int(getattr(position.contract, 'multiplier', 1)),
            }

            return data

        except Exception as e:
            logger.error(f"Error converting position data: {e}")
            return {
                'tradingsymbol': '',
                'exchange': 'SMART',
                'quantity': 0,
                'average_price': 0.0,
                'current_price': 0.0,
                'pnl': 0.0,
                'product': 'IBKR'
            }

    @staticmethod
    def convert_order_data(trade) -> Dict[str, Any]:
        """
        Convert IBKR Trade object to standardized order format.

        Args:
            trade: IBKR Trade object

        Returns:
            Dict with standardized order fields
        """
        try:
            order = trade.order
            status = trade.orderStatus
            contract = trade.contract

            data = {
                'order_id': str(order.orderId),
                'tradingsymbol': contract.symbol,
                'exchange': contract.exchange,
                'quantity': IBKRDataConverter.safe_int(order.totalQuantity),
                'filled_quantity': IBKRDataConverter.safe_int(status.filled),
                'remaining_quantity': IBKRDataConverter.safe_int(status.remaining),
                'price': IBKRDataConverter.safe_float(getattr(order, 'lmtPrice', None)) or \
                         IBKRDataConverter.safe_float(getattr(order, 'auxPrice', None)) or 0.0,
                'average_price': IBKRDataConverter.safe_float(status.avgFillPrice),
                'status': status.status,
                'order_type': order.orderType,
                'transaction_type': order.action,  # BUY/SELL
                'product': 'IBKR',
                'validity': getattr(order, 'tif', 'DAY'),
                'tag': getattr(order, 'orderRef', ''),
                'order_timestamp': trade.log[0].time.isoformat() if trade.log else datetime.now().isoformat(),
                'exchange_timestamp': status.lastFillTime.isoformat() if hasattr(status,
                                                                                 'lastFillTime') and status.lastFillTime else '',
                # Additional IBKR-specific fields
                'contract_type': contract.secType,
                'currency': getattr(contract, 'currency', 'USD'),
                'outside_rth': getattr(order, 'outsideRth', False),
                'client_id': getattr(order, 'clientId', 0),
                'perm_id': getattr(order, 'permId', 0),
                # Commission data if available
                'commission': IBKRDataConverter.safe_float(getattr(trade, 'commission', 0)),
                'commission_currency': getattr(trade, 'commissionCurrency', 'USD') if hasattr(trade,
                                                                                              'commissionCurrency') else 'USD'
            }

            # Map IBKR status to standard status
            data['status'] = IBKRDataConverter.normalize_order_status(data['status'])

            return data

        except Exception as e:
            logger.error(f"Error converting order data: {e}")
            return {
                'order_id': '0',
                'tradingsymbol': '',
                'exchange': 'SMART',
                'quantity': 0,
                'filled_quantity': 0,
                'price': 0.0,
                'average_price': 0.0,
                'status': 'UNKNOWN',
                'order_type': 'MKT',
                'transaction_type': 'BUY',
                'product': 'IBKR'
            }

    @staticmethod
    def convert_historical_data(bars) -> List[Dict[str, Any]]:
        """
        Convert IBKR historical bars to standardized format.

        Args:
            bars: List of IBKR BarData objects

        Returns:
            List of dicts with standardized historical data
        """
        historical_data = []

        try:
            for bar in bars:
                bar_data = {
                    'date': bar.date.isoformat() if hasattr(bar.date, 'isoformat') else str(bar.date),
                    'datetime': bar.date.isoformat() if hasattr(bar.date, 'isoformat') else str(bar.date),
                    'open': IBKRDataConverter.safe_float(bar.open),
                    'high': IBKRDataConverter.safe_float(bar.high),
                    'low': IBKRDataConverter.safe_float(bar.low),
                    'close': IBKRDataConverter.safe_float(bar.close),
                    'volume': IBKRDataConverter.safe_int(bar.volume),
                    # Additional fields
                    'average': IBKRDataConverter.safe_float(getattr(bar, 'average', 0)),
                    'bar_count': IBKRDataConverter.safe_int(getattr(bar, 'barCount', 0))
                }
                historical_data.append(bar_data)

        except Exception as e:
            logger.error(f"Error converting historical data: {e}")

        return historical_data

    @staticmethod
    def convert_account_data(account_values) -> Dict[str, Any]:
        """
        Convert IBKR account values to standardized format.

        Args:
            account_values: List of IBKR AccountValue objects

        Returns:
            Dict with standardized account data
        """
        account_data = {}

        try:
            for item in account_values:
                key = item.tag
                value = IBKRDataConverter.safe_float(item.value) if item.value.replace('.', '').replace('-',
                                                                                                        '').isdigit() else item.value
                currency = getattr(item, 'currency', 'USD')

                account_data[key] = {
                    'value': value,
                    'currency': currency
                }

            # Add computed fields for compatibility
            if 'NetLiquidation' in account_data:
                account_data['equity'] = account_data['NetLiquidation']

            if 'TotalCashValue' in account_data:
                account_data['funds'] = account_data['TotalCashValue']

            if 'BuyingPower' in account_data:
                account_data['margins'] = {
                    'available': account_data['BuyingPower'],
                    'utilised': account_data.get('InitMarginReq', {'value': 0, 'currency': 'USD'})
                }

        except Exception as e:
            logger.error(f"Error converting account data: {e}")

        return account_data

    @staticmethod
    def normalize_order_status(ibkr_status: str) -> str:
        """
        Normalize IBKR order status to standard application status.

        Args:
            ibkr_status: IBKR order status string

        Returns:
            Normalized status string
        """
        status_mapping = {
            'Submitted': 'OPEN',
            'PreSubmitted': 'TRIGGER PENDING',
            'PendingSubmit': 'PENDING',
            'PendingCancel': 'CANCEL PENDING',
            'Cancelled': 'CANCELLED',
            'Filled': 'COMPLETE',
            'Inactive': 'REJECTED',
            'ApiCancelled': 'CANCELLED',
            'ApiPending': 'PENDING'
        }

        return status_mapping.get(ibkr_status, ibkr_status)

    @staticmethod
    def normalize_order_type(ibkr_order_type: str) -> str:
        """
        Normalize IBKR order type to standard application format.

        Args:
            ibkr_order_type: IBKR order type string

        Returns:
            Normalized order type string
        """
        type_mapping = {
            'MKT': 'MARKET',
            'LMT': 'LIMIT',
            'STP': 'SL',
            'STP LMT': 'SL-M',
            'TRAIL': 'SL',
            'MOC': 'MARKET',
            'LOC': 'LIMIT'
        }

        return type_mapping.get(ibkr_order_type, ibkr_order_type)

    @staticmethod
    def prepare_order_for_ibkr(order_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert standard order parameters to IBKR format.

        Args:
            order_params: Standard order parameters

        Returns:
            IBKR-formatted order parameters
        """
        normalized_type = order_params.get('order_type', 'MKT').upper().strip()
        order_type_aliases = {
            'MARKET': 'MKT',
            'MKT': 'MKT',
            'LIMIT': 'LMT',
            'LMT': 'LMT',
            'STOP': 'STP',
            'SL': 'STP',
            'STP': 'STP',
            'STOP_LIMIT': 'STP LMT',
            'STOP-LIMIT': 'STP LMT',
            'STOP LIMIT': 'STP LMT',
            'SL-M': 'STP LMT',
            'STP LMT': 'STP LMT'
        }

        ibkr_params = {
            'symbol': order_params.get('symbol', order_params.get('tradingsymbol', '')),
            'action': order_params.get('action', order_params.get('transaction_type', 'BUY')).upper(),
            'quantity': IBKRDataConverter.safe_int(order_params.get('quantity', 0)),
            'order_type': order_type_aliases.get(normalized_type, normalized_type),
            'exchange': order_params.get('exchange', 'SMART'),
            'currency': order_params.get('currency', 'USD'),
            'outside_rth': order_params.get('outside_rth', False)
        }

        # Handle price fields
        if 'price' in order_params:
            price = IBKRDataConverter.safe_float(order_params['price'])
            if ibkr_params['order_type'] in ['LMT', 'STP LMT']:
                ibkr_params['limit_price'] = price
            elif ibkr_params['order_type'] in ['STP']:
                ibkr_params['stop_price'] = price
        if 'limit_price' in order_params:
            ibkr_params['limit_price'] = IBKRDataConverter.safe_float(order_params['limit_price'])
        if 'stop_price' in order_params:
            ibkr_params['stop_price'] = IBKRDataConverter.safe_float(order_params['stop_price'])

        # Handle time in force
        tif_mapping = {
            'DAY': 'DAY',
            'IOC': 'IOC',
            'GTC': 'GTC',
            'FOK': 'FOK'
        }

        tif = order_params.get('validity', order_params.get('time_in_force', 'DAY'))
        ibkr_params['time_in_force'] = tif_mapping.get(tif, 'DAY')

        return ibkr_params

    @staticmethod
    def create_contract_from_symbol(symbol: str, sec_type: str = 'STK',
                                    exchange: str = 'SMART', currency: str = 'USD') -> Dict[str, Any]:
        """
        Create contract parameters for IBKR from symbol information.

        Args:
            symbol: Trading symbol
            sec_type: Security type (STK, OPT, FUT, etc.)
            exchange: Exchange
            currency: Currency

        Returns:
            Contract parameters dict
        """
        return {
            'symbol': symbol.upper(),
            'secType': sec_type,
            'exchange': exchange,
            'currency': currency
        }

    @staticmethod
    def format_currency(amount: float, currency: str = 'USD') -> str:
        """
        Format currency amount according to currency type.

        Args:
            amount: Amount to format
            currency: Currency code

        Returns:
            Formatted currency string
        """
        symbols = {
        'USD': '$',
        'EUR': '€',
        'GBP': '£',
        'JPY': '¥',
        'CAD': 'C',
        'AUD': 'A',
        }

        symbol = symbols.get(currency, currency + ' ')

        if currency == 'JPY':
            return f"{symbol}{amount:,.0f}"
        else:
            return f"{symbol}{amount:,.2f}"


# Convenience functions for common conversions
def convert_ibkr_ticker(ticker) -> Dict[str, Any]:
    """Convenience function to convert IBKR ticker data"""
    return IBKRDataConverter.convert_ticker_data(ticker)


def convert_ibkr_position(position) -> Dict[str, Any]:
    """Convenience function to convert IBKR position data"""
    return IBKRDataConverter.convert_position_data(position)


def convert_ibkr_order(trade) -> Dict[str, Any]:
    """Convenience function to convert IBKR order data"""
    return IBKRDataConverter.convert_order_data(trade)


def convert_ibkr_historical(bars) -> List[Dict[str, Any]]:
    """Convenience function to convert IBKR historical data"""
    return IBKRDataConverter.convert_historical_data(bars)


def prepare_order_for_ibkr(order_params: Dict[str, Any]) -> Dict[str, Any]:
    """Convenience function to prepare order for IBKR"""
    return IBKRDataConverter.prepare_order_for_ibkr(order_params)


# Export all conversion functions
__all__ = [
    'IBKRDataConverter',
    'convert_ibkr_ticker',
    'convert_ibkr_position',
    'convert_ibkr_order',
    'convert_ibkr_historical',
    'prepare_order_for_ibkr'
]
