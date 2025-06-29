# login_setup/broker_factory.py
"""
Broker factory for creating and managing broker-specific clients.
Provides unified interface for both Kite and IBKR brokers.
"""

import logging
import importlib
from typing import Union, Dict, Any, Optional, Type, List
from abc import ABC, abstractmethod

from login_setup.broker_modes import BrokerMode, TradingMode, get_broker_config
from login_setup.enhanced_token_manager import EnhancedTokenManager

logger = logging.getLogger(__name__)


class BrokerClientInterface(ABC):
    """Abstract interface that all broker clients should implement"""

    @abstractmethod
    def get_profile(self) -> Dict[str, Any]:
        """Get user profile information"""
        pass

    @abstractmethod
    def get_positions(self) -> list:
        """Get current positions"""
        pass

    @abstractmethod
    def place_order(self, **kwargs) -> Dict[str, Any]:
        """Place a trading order"""
        pass

    @abstractmethod
    def get_orders(self) -> list:
        """Get order history"""
        pass

    @abstractmethod
    def get_instruments(self) -> list:
        """Get tradeable instruments"""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if client is connected"""
        pass

    @abstractmethod
    def disconnect(self):
        """Disconnect from broker"""
        pass


class KiteClientWrapper(BrokerClientInterface):
    """Wrapper for KiteConnect client to implement common interface"""

    def __init__(self, kite_client):
        self.client = kite_client
        self._connected = True

    def get_profile(self) -> Dict[str, Any]:
        return self.client.profile()

    def get_positions(self) -> list:
        return self.client.positions()

    def place_order(self, **kwargs) -> Dict[str, Any]:
        return self.client.place_order(**kwargs)

    def get_orders(self) -> list:
        return self.client.orders()

    def get_instruments(self) -> list:
        return self.client.instruments()

    def is_connected(self) -> bool:
        try:
            self.client.profile()
            return True
        except:
            return False

    def disconnect(self):
        # Kite doesn't need explicit disconnection
        self._connected = False

    def __getattr__(self, name):
        """Delegate all other attributes to the underlying Kite client"""
        return getattr(self.client, name)


class IBKRClientWrapper(BrokerClientInterface):
    """Wrapper for IBKR client to implement common interface"""

    def __init__(self, ib_client):
        self.client = ib_client

    def get_profile(self) -> Dict[str, Any]:
        """Get account information as profile"""
        try:
            accounts = self.client.managedAccounts()
            account_summary = self.client.accountSummary()

            profile = {
                'user_name': accounts[0] if accounts else 'Unknown',
                'broker': 'Interactive Brokers',
                'accounts': accounts,
                'account_summary': {item.tag: item.value for item in account_summary}
            }
            return profile
        except Exception as e:
            logger.error(f"Error getting IBKR profile: {e}")
            return {}

    def get_positions(self) -> list:
        """Get current positions"""
        try:
            positions = self.client.positions()
            return [
                {
                    'tradingsymbol': pos.contract.symbol,
                    'exchange': pos.contract.exchange,
                    'quantity': pos.position,
                    'average_price': pos.avgCost,
                    'pnl': pos.unrealizedPNL,
                    'product': 'IBKR'
                }
                for pos in positions if pos.position != 0
            ]
        except Exception as e:
            logger.error(f"Error getting IBKR positions: {e}")
            return []

    def place_order(self, **kwargs) -> Dict[str, Any]:
        """Place order (simplified interface)"""
        try:
            # This would need proper order creation logic
            # For now, return a placeholder
            return {'order_id': 'IBKR_ORDER_ID', 'status': 'PENDING'}
        except Exception as e:
            logger.error(f"Error placing IBKR order: {e}")
            return {'error': str(e)}

    def get_orders(self) -> list:
        """Get order history"""
        try:
            trades = self.client.trades()
            return [
                {
                    'order_id': trade.contract.conId,
                    'tradingsymbol': trade.contract.symbol,
                    'exchange': trade.contract.exchange,
                    'quantity': trade.fill.shares,
                    'price': trade.fill.price,
                    'status': 'COMPLETE'
                }
                for trade in trades
            ]
        except Exception as e:
            logger.error(f"Error getting IBKR orders: {e}")
            return []

    def get_instruments(self) -> list:
        """Get tradeable instruments (placeholder)"""
        # IBKR instruments are requested on-demand
        return []

    def is_connected(self) -> bool:
        return self.client.isConnected()

    def disconnect(self):
        self.client.disconnect()

    def __getattr__(self, name):
        """Delegate all other attributes to the underlying IB client"""
        return getattr(self.client, name)


class PaperTradingClientWrapper(BrokerClientInterface):
    """Wrapper for paper trading clients"""

    def __init__(self, paper_client, broker_mode: BrokerMode):
        self.client = paper_client
        self.broker_mode = broker_mode

    def get_profile(self) -> Dict[str, Any]:
        if hasattr(self.client, 'get_profile'):
            return self.client.get_profile()
        return {'user_name': 'Paper Trader', 'broker': 'Paper Trading'}

    def get_positions(self) -> list:
        if hasattr(self.client, 'get_positions'):
            return self.client.get_positions()
        return []

    def place_order(self, **kwargs) -> Dict[str, Any]:
        if hasattr(self.client, 'place_order'):
            return self.client.place_order(**kwargs)
        return {'order_id': 'PAPER_ORDER', 'status': 'COMPLETE'}

    def get_orders(self) -> list:
        if hasattr(self.client, 'get_orders'):
            return self.client.get_orders()
        return []

    def get_instruments(self) -> list:
        if hasattr(self.client, 'get_instruments'):
            return self.client.get_instruments()
        return []

    def is_connected(self) -> bool:
        return True  # Paper trading is always "connected"

    def disconnect(self):
        pass  # No disconnection needed for paper trading

    def __getattr__(self, name):
        """Delegate to underlying paper client"""
        return getattr(self.client, name)


class BrokerFactory:
    """
    Factory class for creating and managing broker clients.
    Handles both live and paper trading clients for all supported brokers.
    """

    @staticmethod
    def create_client(broker_mode: BrokerMode,
                      trading_mode: TradingMode,
                      authentication_data: Dict[str, Any]) -> BrokerClientInterface:
        """
        Create a broker client based on mode and authentication data

        Args:
            broker_mode: Broker to use (India/America)
            trading_mode: Paper or live trading
            authentication_data: Authentication details from login manager

        Returns:
            BrokerClientInterface: Wrapped client implementing common interface
        """
        if broker_mode == BrokerMode.INDIA:
            return BrokerFactory._create_kite_client(trading_mode, authentication_data)
        elif broker_mode == BrokerMode.AMERICA:
            return BrokerFactory._create_ibkr_client(trading_mode, authentication_data)
        else:
            raise ValueError(f"Unsupported broker mode: {broker_mode}")

    @staticmethod
    def _create_kite_client(trading_mode: TradingMode,
                            auth_data: Dict[str, Any]) -> BrokerClientInterface:
        """Create Kite client (live or paper)"""
        if trading_mode == TradingMode.LIVE:
            # Create live Kite client
            try:
                from kiteconnect import KiteConnect

                api_key = auth_data.get('api_key')
                access_token = auth_data.get('access_token')

                if not api_key or not access_token:
                    raise ValueError("Missing API key or access token for Kite")

                kite_client = KiteConnect(api_key=api_key, access_token=access_token)
                return KiteClientWrapper(kite_client)

            except ImportError:
                raise ImportError("kiteconnect library not available")

        elif trading_mode == TradingMode.PAPER:
            # Create paper trading client for Kite
            try:
                # Try to import from kite module
                paper_module = importlib.import_module('kite.utils.paper_trading_manager')
                PaperTradingManager = getattr(paper_module, 'PaperTradingManager')

                paper_client = PaperTradingManager()
                return PaperTradingClientWrapper(paper_client, BrokerMode.INDIA)

            except (ImportError, AttributeError) as e:
                logger.error(f"Could not create Kite paper client: {e}")
                raise ImportError("Kite paper trading manager not available")

    @staticmethod
    def _create_ibkr_client(trading_mode: TradingMode,
                            auth_data: Dict[str, Any]) -> BrokerClientInterface:
        """Create IBKR client (live or paper)"""
        # For IBKR, both live and paper use the same client but connect to different ports
        try:
            ib_client = auth_data.get('ib_client')

            if not ib_client:
                raise ValueError("Missing IB client in authentication data")

            if trading_mode == TradingMode.PAPER:
                # Paper trading uses TWS paper account
                return IBKRClientWrapper(ib_client)
            else:
                # Live trading uses real account
                return IBKRClientWrapper(ib_client)

        except Exception as e:
            logger.error(f"Could not create IBKR client: {e}")
            raise

    @staticmethod
    def create_data_client(broker_mode: BrokerMode,
                           authentication_data: Dict[str, Any]) -> BrokerClientInterface:
        """
        Create a client specifically for data fetching (always uses live connection)

        This is useful when you need real market data even in paper trading mode.
        """
        if broker_mode == BrokerMode.INDIA:
            # For Kite, always create live client for data
            try:
                from kiteconnect import KiteConnect

                api_key = authentication_data.get('api_key')
                access_token = authentication_data.get('access_token')

                kite_client = KiteConnect(api_key=api_key, access_token=access_token)
                return KiteClientWrapper(kite_client)

            except Exception as e:
                logger.error(f"Could not create Kite data client: {e}")
                raise

        elif broker_mode == BrokerMode.AMERICA:
            # For IBKR, use the same client for data
            ib_client = authentication_data.get('ib_client')
            return IBKRClientWrapper(ib_client)

    @staticmethod
    def get_module_path(broker_mode: BrokerMode) -> str:
        """Get the module path for broker-specific implementations"""
        config = get_broker_config(broker_mode)
        return config.module_path

    @staticmethod
    def load_broker_main_window(broker_mode: BrokerMode) -> Type:
        """
        Dynamically load the main window class for the specified broker

        Returns:
            Type: The main window class for the broker
        """
        module_path = BrokerFactory.get_module_path(broker_mode)

        try:
            # Import the main window module
            main_window_module = importlib.import_module(f'{module_path}.core.main_window')

            # Get the main window class
            main_window_class = getattr(main_window_module, 'SwingTraderWindow')

            return main_window_class

        except (ImportError, AttributeError) as e:
            logger.error(f"Could not load main window for {broker_mode.value}: {e}")
            raise ImportError(f"Main window not available for {broker_mode.value}")

    @staticmethod
    def validate_authentication_data(broker_mode: BrokerMode,
                                     trading_mode: TradingMode,
                                     auth_data: Dict[str, Any]) -> bool:
        """
        Validate that authentication data contains required fields

        Args:
            broker_mode: Broker mode
            trading_mode: Trading mode
            auth_data: Authentication data to validate

        Returns:
            bool: True if valid, False otherwise
        """
        if broker_mode == BrokerMode.INDIA:
            required_fields = ['api_key', 'access_token']
            return all(field in auth_data and auth_data[field] for field in required_fields)
        elif broker_mode == BrokerMode.AMERICA:
            required_fields = ['ib_client']
            return all(field in auth_data and auth_data[field] for field in required_fields)

        return False

    @staticmethod
    def get_broker_specific_config(broker_mode: BrokerMode) -> Dict[str, Any]:
        """Get broker-specific configuration and constants"""
        try:
            module_path = BrokerFactory.get_module_path(broker_mode)
            constants_module = importlib.import_module(f'{module_path}.utils.constants')

            # Extract relevant constants
            config = {}

            # Common constants that most brokers should have
            common_attrs = [
                'DEFAULT_LOT_SIZES', 'STRIKE_STEP_RULES', 'COLORS',
                'EXCHANGE_PREFERENCE_ORDER', 'ORDER_TYPE_MARKET',
                'ORDER_TYPE_LIMIT', 'TRANSACTION_TYPE_BUY', 'TRANSACTION_TYPE_SELL'
            ]

            for attr in common_attrs:
                if hasattr(constants_module, attr):
                    config[attr] = getattr(constants_module, attr)

            return config

        except ImportError as e:
            logger.warning(f"Could not load constants for {broker_mode.value}: {e}")
            return {}

    @staticmethod
    def create_paper_trading_manager(broker_mode: BrokerMode) -> Any:
        """Create broker-specific paper trading manager"""
        try:
            module_path = BrokerFactory.get_module_path(broker_mode)
            paper_module = importlib.import_module(f'{module_path}.utils.paper_trading_manager')

            PaperTradingManager = getattr(paper_module, 'PaperTradingManager')
            return PaperTradingManager()

        except (ImportError, AttributeError) as e:
            logger.error(f"Could not create paper trading manager for {broker_mode.value}: {e}")
            raise ImportError(f"Paper trading manager not available for {broker_mode.value}")


class BrokerClientManager:
    """
    Manager class for handling multiple broker clients and sessions.
    Useful for applications that might need to switch between brokers.
    """

    def __init__(self):
        self.active_clients: Dict[BrokerMode, BrokerClientInterface] = {}
        self.token_manager = EnhancedTokenManager()

    def add_client(self, broker_mode: BrokerMode, client: BrokerClientInterface):
        """Add a broker client to the manager"""
        self.active_clients[broker_mode] = client
        logger.info(f"Added {broker_mode.value} client to manager")

    def get_client(self, broker_mode: BrokerMode) -> Optional[BrokerClientInterface]:
        """Get active client for specified broker"""
        return self.active_clients.get(broker_mode)

    def remove_client(self, broker_mode: BrokerMode):
        """Remove and disconnect client for specified broker"""
        if broker_mode in self.active_clients:
            client = self.active_clients[broker_mode]
            try:
                client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting {broker_mode.value} client: {e}")

            del self.active_clients[broker_mode]
            logger.info(f"Removed {broker_mode.value} client from manager")

    def get_active_brokers(self) -> List[BrokerMode]:
        """Get list of brokers with active clients"""
        return list(self.active_clients.keys())

    def disconnect_all(self):
        """Disconnect all active clients"""
        for broker_mode in list(self.active_clients.keys()):
            self.remove_client(broker_mode)

    def get_combined_positions(self) -> Dict[BrokerMode, list]:
        """Get positions from all active brokers"""
        positions = {}
        for broker_mode, client in self.active_clients.items():
            try:
                positions[broker_mode] = client.get_positions()
            except Exception as e:
                logger.error(f"Error getting positions from {broker_mode.value}: {e}")
                positions[broker_mode] = []

        return positions

    def check_all_connections(self) -> Dict[BrokerMode, bool]:
        """Check connection status of all clients"""
        status = {}
        for broker_mode, client in self.active_clients.items():
            try:
                status[broker_mode] = client.is_connected()
            except Exception as e:
                logger.error(f"Error checking {broker_mode.value} connection: {e}")
                status[broker_mode] = False

        return status


# Utility functions for common broker operations

def create_unified_client(broker_mode: BrokerMode,
                          trading_mode: TradingMode,
                          authentication_data: Dict[str, Any]) -> BrokerClientInterface:
    """
    Convenience function to create a unified broker client

    Args:
        broker_mode: Broker to use
        trading_mode: Paper or live trading
        authentication_data: Auth data from login manager

    Returns:
        BrokerClientInterface: Ready-to-use broker client
    """
    return BrokerFactory.create_client(broker_mode, trading_mode, authentication_data)


def validate_broker_requirements(broker_mode: BrokerMode) -> Dict[str, Any]:
    """
    Validate that all requirements for a broker are met

    Returns:
        Dict with validation results and suggestions
    """
    result = {
        'valid': False,
        'missing_requirements': [],
        'suggestions': []
    }

    if broker_mode == BrokerMode.INDIA:
        try:
            import kiteconnect
            result['kiteconnect_version'] = kiteconnect.__version__
        except ImportError:
            result['missing_requirements'].append('kiteconnect')
            result['suggestions'].append('Install kiteconnect: pip install kiteconnect')

    elif broker_mode == BrokerMode.AMERICA:
        try:
            import ib_insync
            result['ib_insync_version'] = ib_insync.__version__
        except ImportError:
            result['missing_requirements'].append('ib_insync')
            result['suggestions'].append('Install ib_insync: pip install ib_insync')

        # Check if TWS/Gateway might be available (this is a rough check)
        import socket
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            tws_available = sock.connect_ex(('127.0.0.1', 7497)) == 0
            sock.close()

            if not tws_available:
                result['suggestions'].append('Start TWS or IB Gateway')
                result['missing_requirements'].append('TWS/Gateway not running')
        except:
            pass

    result['valid'] = len(result['missing_requirements']) == 0
    return result


def get_broker_capabilities(broker_mode: BrokerMode) -> Dict[str, Any]:
    """Get capabilities and features supported by each broker"""
    capabilities = {
        BrokerMode.INDIA: {
            'markets': ['NSE', 'BSE', 'NFO', 'BFO', 'MCX'],
            'instruments': ['stocks', 'options', 'futures', 'commodities'],
            'order_types': ['MARKET', 'LIMIT', 'SL', 'SL-M'],
            'currency': 'INR',
            'real_time_data': True,
            'historical_data': True,
            'paper_trading': True,
            'auto_reconnect': False,
            'session_duration': '1_day'
        },
        BrokerMode.AMERICA: {
            'markets': ['NYSE', 'NASDAQ', 'ARCA', 'BATS'],
            'instruments': ['stocks', 'options', 'futures', 'forex', 'bonds'],
            'order_types': ['MKT', 'LMT', 'STP', 'STP LMT', 'TRAIL'],
            'currency': 'USD',
            'real_time_data': True,
            'historical_data': True,
            'paper_trading': True,
            'auto_reconnect': True,
            'session_duration': 'persistent'
        }
    }

    return capabilities.get(broker_mode, {})


def format_order_for_broker(broker_mode: BrokerMode,
                            order_params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert generic order parameters to broker-specific format

    Args:
        broker_mode: Target broker
        order_params: Generic order parameters
            {
                'symbol': str,
                'quantity': int,
                'side': 'BUY'|'SELL',
                'order_type': 'MARKET'|'LIMIT',
                'price': float (optional),
                'exchange': str (optional)
            }

    Returns:
        Dict: Broker-specific order parameters
    """
    if broker_mode == BrokerMode.INDIA:
        # Kite format
        kite_order = {
            'tradingsymbol': order_params['symbol'],
            'quantity': order_params['quantity'],
            'transaction_type': order_params['side'],
            'order_type': order_params['order_type'],
            'exchange': order_params.get('exchange', 'NSE'),
            'product': order_params.get('product', 'MIS')
        }

        if order_params.get('price'):
            kite_order['price'] = order_params['price']

        return kite_order

    elif broker_mode == BrokerMode.AMERICA:
        # IBKR format (simplified)
        ibkr_order = {
            'symbol': order_params['symbol'],
            'totalQuantity': order_params['quantity'],
            'action': order_params['side'],
            'orderType': order_params['order_type'].replace('MARKET', 'MKT').replace('LIMIT', 'LMT'),
            'exchange': order_params.get('exchange', 'SMART')
        }

        if order_params.get('price'):
            ibkr_order['lmtPrice'] = order_params['price']

        return ibkr_order

    return order_params


# Global instances
broker_factory = BrokerFactory()

# Export all public interfaces
__all__ = [
    'BrokerFactory',
    'BrokerClientInterface',
    'BrokerClientManager',
    'KiteClientWrapper',
    'IBKRClientWrapper',
    'PaperTradingClientWrapper',
    'create_unified_client',
    'validate_broker_requirements',
    'get_broker_capabilities',
    'broker_factory'
]