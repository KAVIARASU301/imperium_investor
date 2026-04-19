# login_setup/broker_factory.py
"""
Enhanced broker factory with improved IBKR IPv6 support.
Provides unified interface for both Kite and IBKR brokers.
"""

import logging
import importlib
from typing import Union, Dict, Any, Optional, Type, List
from abc import ABC, abstractmethod

from login_setup.broker_modes import BrokerMode, TradingMode, get_broker_config, get_module_path
from login_setup.enhanced_token_manager import EnhancedTokenManager
from kite.core.relay_integration import build_relay_client

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
    """Enhanced wrapper for IBKR client with improved IPv6 support"""

    def __init__(self, ib_client, connection_info: Dict[str, Any] = None):
        self.client = ib_client
        self.connection_info = connection_info or {}
        self._last_check = None

    def get_profile(self) -> Dict[str, Any]:
        """Get account information as profile"""
        try:
            accounts = self.client.managedAccounts()
            account_summary = self.client.accountSummary()

            # Enhanced profile with connection info
            profile = {
                'user_name': accounts[0] if accounts else 'IBKR User',
                'broker': 'Interactive Brokers',
                'accounts': accounts,
                'account_summary': {item.tag: item.value for item in account_summary},
                'connection_info': {
                    'host': self.connection_info.get('host', 'unknown'),
                    'port': self.connection_info.get('port', 'unknown'),
                    'client_id': self.connection_info.get('client_id', 'unknown'),
                    'address_family': self.connection_info.get('address_family', 'unknown')
                }
            }
            return profile
        except Exception as e:
            logger.error(f"Error getting IBKR profile: {e}")
            return {
                'user_name': 'IBKR User',
                'broker': 'Interactive Brokers',
                'error': str(e)
            }

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
                    'product': 'IBKR',
                    'contract_details': {
                        'secType': pos.contract.secType,
                        'currency': pos.contract.currency,
                        'localSymbol': pos.contract.localSymbol
                    }
                }
                for pos in positions if pos.position != 0
            ]
        except Exception as e:
            logger.error(f"Error getting IBKR positions: {e}")
            return []

    def place_order(self, **kwargs) -> Dict[str, Any]:
        """Place order with enhanced error handling"""
        try:
            # Convert generic order parameters to IBKR format
            ibkr_order = self._convert_to_ibkr_order(kwargs)

            # Place the order
            trade = self.client.placeOrder(ibkr_order['contract'], ibkr_order['order'])

            return {
                'order_id': trade.order.orderId,
                'status': 'SUBMITTED',
                'order_details': ibkr_order
            }
        except Exception as e:
            logger.error(f"Error placing IBKR order: {e}")
            return {'error': str(e), 'status': 'FAILED'}

    def get_orders(self) -> list:
        """Get order history"""
        try:
            trades = self.client.trades()
            orders = []

            for trade in trades:
                order_data = {
                    'order_id': trade.order.orderId,
                    'tradingsymbol': trade.contract.symbol,
                    'exchange': trade.contract.exchange,
                    'order_type': trade.order.orderType,
                    'quantity': trade.order.totalQuantity,
                    'status': trade.orderStatus.status if trade.orderStatus else 'UNKNOWN'
                }

                # Add fill information if available
                if trade.fills:
                    fill = trade.fills[-1]  # Latest fill
                    order_data.update({
                        'filled_quantity': fill.execution.shares,
                        'average_price': fill.execution.price,
                        'fill_time': fill.time
                    })

                orders.append(order_data)

            return orders
        except Exception as e:
            logger.error(f"Error getting IBKR orders: {e}")
            return []

    def get_instruments(self) -> list:
        """Get tradeable instruments (IBKR-specific implementation)"""
        # IBKR doesn't have a general instruments list like Kite
        # This would typically be implemented based on specific requirements
        try:
            # Return a basic structure for now
            return [{
                'tradingsymbol': 'IBKR_INSTRUMENTS',
                'name': 'Use contract search for specific instruments',
                'exchange': 'SMART',
                'instrument_type': 'INFO'
            }]
        except Exception as e:
            logger.error(f"Error getting IBKR instruments: {e}")
            return []

    def is_connected(self) -> bool:
        """Check connection status with caching"""
        try:
            import time
            current_time = time.time()

            # Cache the connection check for 5 seconds to avoid excessive calls
            if self._last_check and (current_time - self._last_check) < 5:
                return True

            connected = self.client.isConnected()
            if connected:
                self._last_check = current_time

            return connected
        except Exception as e:
            logger.error(f"Error checking IBKR connection: {e}")
            return False

    def disconnect(self):
        """Disconnect from IBKR"""
        try:
            if self.client and self.client.isConnected():
                self.client.disconnect()
                logger.info("Disconnected from IBKR")
        except Exception as e:
            logger.error(f"Error disconnecting from IBKR: {e}")

    def _convert_to_ibkr_order(self, generic_order: Dict[str, Any]) -> Dict[str, Any]:
        """Convert generic order parameters to IBKR format"""
        try:
            from ib_insync import Stock, Order

            # Create contract
            contract = Stock(
                symbol=generic_order['symbol'],
                exchange=generic_order.get('exchange', 'SMART'),
                currency=generic_order.get('currency', 'USD')
            )

            # Create order
            order = Order(
                action=generic_order.get('side', 'BUY'),
                totalQuantity=generic_order['quantity'],
                orderType=generic_order.get('order_type', 'MKT').replace('MARKET', 'MKT').replace('LIMIT', 'LMT')
            )

            # Add price for limit orders
            if 'price' in generic_order and generic_order['order_type'] in ['LIMIT', 'LMT']:
                order.lmtPrice = generic_order['price']

            return {'contract': contract, 'order': order}

        except Exception as e:
            logger.error(f"Error converting order to IBKR format: {e}")
            raise

    def get_connection_info(self) -> Dict[str, Any]:
        """Get connection information"""
        return self.connection_info.copy()

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
        return {
            'user_name': 'Paper Trader',
            'broker': f'Paper Trading ({self.broker_mode.value.title()})',
            'trading_mode': 'paper'
        }

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
    Enhanced factory class for creating and managing broker clients.
    Handles both live and paper trading clients with improved IBKR IPv6 support.
    """

    @staticmethod
    def load_broker_main_window(broker_mode: BrokerMode) -> Type:
        """
        Dynamically loads the main window class for the specified broker.
        This prevents circular dependencies and keeps broker-specific UI separate.
        """
        try:
            module_path = get_module_path(broker_mode)
            main_window_module_name = f"{module_path}.core.main_window"

            logger.info(f"Dynamically loading main window from: {main_window_module_name}")

            module = importlib.import_module(main_window_module_name)

            # The main window class is expected to be named 'SwingTraderWindow' in both modules
            MainWindowClass = getattr(module, 'SwingTraderWindow')

            return MainWindowClass
        except (ImportError, AttributeError) as e:
            logger.error(f"Failed to load main window for {broker_mode.value}: {e}")
            raise RuntimeError(f"Could not load main window for {broker_mode.value}") from e

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
        logger.info(f"Creating {broker_mode.value} client for {trading_mode.value} trading")

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

                # Test the connection
                profile = kite_client.profile()
                logger.info(f"Kite client created for user: {profile.get('user_name', 'Unknown')}")

                wrapped_client = KiteClientWrapper(kite_client)
                token_manager = auth_data.get('token_manager') or EnhancedTokenManager()
                return build_relay_client(
                    raw_kite_client=wrapped_client,
                    api_key=api_key,
                    access_token=access_token,
                    token_manager=token_manager,
                )

            except ImportError:
                raise ImportError("kiteconnect library not available")
            except Exception as e:
                logger.error(f"Failed to create Kite client: {e}")
                raise

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
        """Create enhanced IBKR client with IPv6 support"""
        try:
            ib_client = auth_data.get('ib_client')
            if not ib_client:
                raise ValueError("Missing IB client in authentication data")

            # Extract connection information for enhanced wrapper
            connection_info = {
                'client_id': auth_data.get('client_id'),
                'trading_mode': trading_mode.value,
                'broker_mode': BrokerMode.AMERICA.value
            }

            # Add any additional connection details if available
            if 'connection_details' in auth_data:
                connection_info.update(auth_data['connection_details'])

            # Test the connection
            if not ib_client.isConnected():
                raise ConnectionError("IB client is not connected")

            # Get account info for verification
            try:
                accounts = ib_client.managedAccounts()
                connection_info['managed_accounts'] = accounts
                logger.info(f"IBKR client created with accounts: {accounts}")
            except Exception as e:
                logger.warning(f"Could not get managed accounts: {e}")

            return IBKRClientWrapper(ib_client, connection_info)

        except Exception as e:
            logger.error(f"Could not create IBKR client: {e}")
            raise

    @staticmethod
    def create_data_client(broker_mode: BrokerMode,
                           authentication_data: Dict[str, Any]) -> BrokerClientInterface:
        """
        Create a client specifically for data fetching (always uses live connection)
        """
        logger.info(f"Creating data client for {broker_mode.value}")

        if broker_mode == BrokerMode.INDIA:
            # For Kite, always create live client for data
            try:
                from kiteconnect import KiteConnect

                api_key = authentication_data.get('api_key')
                access_token = authentication_data.get('access_token')

                kite_client = KiteConnect(api_key=api_key, access_token=access_token)
                wrapped_client = KiteClientWrapper(kite_client)
                token_manager = authentication_data.get('token_manager') or EnhancedTokenManager()
                return build_relay_client(
                    raw_kite_client=wrapped_client,
                    api_key=api_key,
                    access_token=access_token,
                    token_manager=token_manager,
                )

            except Exception as e:
                logger.error(f"Could not create Kite data client: {e}")
                raise

        elif broker_mode == BrokerMode.AMERICA:
            # For IBKR, use the same client for data
            ib_client = authentication_data.get('ib_client')
            connection_info = authentication_data.get('connection_details', {})
            return IBKRClientWrapper(ib_client, connection_info)

    @staticmethod
    def validate_authentication_data(broker_mode: BrokerMode,
                                     trading_mode: TradingMode,
                                     auth_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enhanced validation of authentication data

        Returns:
            Dict with validation results and details
        """
        result = {
            'valid': False,
            'missing_fields': [],
            'warnings': [],
            'details': {}
        }

        if broker_mode == BrokerMode.INDIA:
            required_fields = ['api_key', 'access_token']
            for field in required_fields:
                if field not in auth_data or not auth_data[field]:
                    result['missing_fields'].append(field)

            # Additional validation for Kite
            if auth_data.get('api_key'):
                result['details']['api_key_length'] = len(auth_data['api_key'])
                if len(auth_data['api_key']) != 32:
                    result['warnings'].append('API key should be 32 characters long')

        elif broker_mode == BrokerMode.AMERICA:
            required_fields = ['ib_client']
            for field in required_fields:
                if field not in auth_data or not auth_data[field]:
                    result['missing_fields'].append(field)

            # Additional validation for IBKR
            ib_client = auth_data.get('ib_client')
            if ib_client:
                try:
                    result['details']['connected'] = ib_client.isConnected()
                    if not result['details']['connected']:
                        result['warnings'].append('IB client is not connected')
                except Exception as e:
                    result['warnings'].append(f'Could not check IB connection: {e}')

        result['valid'] = len(result['missing_fields']) == 0
        return result

    @staticmethod
    def get_broker_capabilities(broker_mode: BrokerMode) -> Dict[str, Any]:
        """Get enhanced capabilities and features supported by each broker"""
        capabilities = {
            BrokerMode.INDIA: {
                'markets': ['NSE', 'BSE', 'NFO', 'BFO', 'MCX'],
                'instruments': ['stocks', 'options', 'futures', 'commodities'],
                'order_types': ['MARKET', 'LIMIT', 'SL', 'SL-M'],
                'currency': 'INR',
                'currency_symbol': '₹',
                'real_time_data': True,
                'historical_data': True,
                'paper_trading': True,
                'auto_reconnect': False,
                'session_duration': '1_day',
                'timezone': 'Asia/Kolkata',
                'connection_type': 'REST_API'
            },
            BrokerMode.AMERICA: {
                'markets': ['NYSE', 'NASDAQ', 'ARCA', 'BATS', 'SMART'],
                'instruments': ['stocks', 'options', 'futures', 'forex', 'bonds', 'crypto'],
                'order_types': ['MKT', 'LMT', 'STP', 'STP LMT', 'TRAIL'],
                'currency': 'USD',
                'currency_symbol': '$',
                'real_time_data': True,
                'historical_data': True,
                'paper_trading': True,
                'auto_reconnect': True,
                'session_duration': 'persistent',
                'timezone': 'America/New_York',
                'connection_type': 'SOCKET_API',
                'ipv6_support': True,
                'address_families': ['IPv6', 'IPv4']
            }
        }

        return capabilities.get(broker_mode, {})

    @staticmethod
    def test_broker_connectivity(broker_mode: BrokerMode) -> Dict[str, Any]:
        """Test broker connectivity without creating full client"""
        result = {
            'broker': broker_mode.value,
            'available': False,
            'details': {},
            'recommendations': []
        }

        if broker_mode == BrokerMode.INDIA:
            try:
                import kiteconnect
                result['available'] = True
                result['details']['library_version'] = kiteconnect.__version__
                result['details']['connection_type'] = 'HTTPS API'
            except ImportError:
                result['recommendations'].append('Install kiteconnect: pip install kiteconnect')

        elif broker_mode == BrokerMode.AMERICA:
            try:
                import ib_insync
                result['details']['library_version'] = ib_insync.__version__

                # Test IBKR connectivity using our enhanced validator
                from login_setup.ibkr_auth import IBKRConnectionValidator
                connectivity_check = IBKRConnectionValidator.quick_check()

                result['available'] = connectivity_check['accessible']
                result['details'].update(connectivity_check)

                if not result['available']:
                    result['recommendations'].extend([
                        'Start IB Gateway or TWS',
                        'Configure API settings in Gateway',
                        'Check firewall settings'
                    ])
                else:
                    result['recommendations'].append('IBKR connectivity looks good!')

            except ImportError:
                result['recommendations'].append('Install ib_insync: pip install ib_insync')

        return result


class BrokerClientManager:
    """
    Enhanced manager class for handling multiple broker clients and sessions.
    """

    def __init__(self):
        self.active_clients: Dict[BrokerMode, BrokerClientInterface] = {}
        self.token_manager = EnhancedTokenManager()
        self.connection_monitors = {}

    def add_client(self, broker_mode: BrokerMode, client: BrokerClientInterface):
        """Add a broker client to the manager"""
        self.active_clients[broker_mode] = client
        logger.info(f"Added {broker_mode.value} client to manager")

        # Start connection monitoring for IBKR
        if broker_mode == BrokerMode.AMERICA:
            self._start_connection_monitoring(broker_mode, client)

    def _start_connection_monitoring(self, broker_mode: BrokerMode, client: BrokerClientInterface):
        """Start connection monitoring for IBKR clients"""
        try:
            from PySide6.QtCore import QTimer

            if broker_mode in self.connection_monitors:
                self.connection_monitors[broker_mode].stop()

            timer = QTimer()
            timer.timeout.connect(lambda: self._check_client_health(broker_mode))
            timer.start(30000)  # Check every 30 seconds

            self.connection_monitors[broker_mode] = timer
            logger.info(f"Started connection monitoring for {broker_mode.value}")

        except Exception as e:
            logger.warning(f"Could not start connection monitoring: {e}")

    def _check_client_health(self, broker_mode: BrokerMode):
        """Check client connection health"""
        client = self.active_clients.get(broker_mode)
        if client:
            try:
                if not client.is_connected():
                    logger.warning(f"{broker_mode.value} client connection lost")
                    # Could emit signal here for UI notification
            except Exception as e:
                logger.error(f"Error checking {broker_mode.value} client health: {e}")

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

            # Stop monitoring
            if broker_mode in self.connection_monitors:
                self.connection_monitors[broker_mode].stop()
                del self.connection_monitors[broker_mode]

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

    def get_connection_status(self) -> Dict[BrokerMode, Dict[str, Any]]:
        """Get detailed connection status of all clients"""
        status = {}
        for broker_mode, client in self.active_clients.items():
            try:
                client_status = {
                    'connected': client.is_connected(),
                    'broker': broker_mode.value
                }

                # Add broker-specific details
                if hasattr(client, 'get_connection_info'):
                    client_status['connection_info'] = client.get_connection_info()

                # Get profile for additional info
                try:
                    profile = client.get_profile()
                    client_status['user_info'] = {
                        'user_name': profile.get('user_name'),
                        'accounts': profile.get('accounts', [])
                    }
                except Exception:
                    pass

                status[broker_mode] = client_status

            except Exception as e:
                logger.error(f"Error getting {broker_mode.value} status: {e}")
                status[broker_mode] = {
                    'connected': False,
                    'error': str(e)
                }

        return status


# Factory instances and utility functions
enhanced_broker_factory = BrokerFactory()


def create_enhanced_client(broker_mode: BrokerMode,
                           trading_mode: TradingMode,
                           authentication_data: Dict[str, Any]) -> BrokerClientInterface:
    """
    Convenience function to create an enhanced broker client

    Args:
        broker_mode: Broker to use
        trading_mode: Paper or live trading
        authentication_data: Auth data from login manager

    Returns:
        BrokerClientInterface: Ready-to-use enhanced broker client
    """
    return enhanced_broker_factory.create_client(broker_mode, trading_mode, authentication_data)


def validate_broker_setup(broker_mode: BrokerMode) -> Dict[str, Any]:
    """
    Comprehensive broker setup validation

    Returns:
        Dict with validation results and recommendations
    """
    result = enhanced_broker_factory.test_broker_connectivity(broker_mode)
    capabilities = enhanced_broker_factory.get_broker_capabilities(broker_mode)

    return {
        'broker': broker_mode.value,
        'connectivity': result,
        'capabilities': capabilities,
        'ready': result['available']
    }


# Export all public interfaces
__all__ = [
    'BrokerFactory',
    'BrokerClientInterface',
    'BrokerClientManager',
    'KiteClientWrapper',
    'IBKRClientWrapper',
    'PaperTradingClientWrapper',
    'create_enhanced_client',
    'validate_broker_setup',
    'enhanced_broker_factory'
]