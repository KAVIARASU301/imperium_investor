# login_setup/ibkr_auth.py
"""
Interactive Brokers authentication and connection management using ib_insync.
Handles both paper and live trading connections to TWS/IB Gateway.
"""

import logging
import asyncio
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal, QTimer, QObject
from PySide6.QtWidgets import QApplication

try:
    from ib_insync import IB, util

    IBKR_AVAILABLE = True
except ImportError:
    IBKR_AVAILABLE = False
    IB = None
    util = None

from login_setup.broker_modes import BrokerMode, TradingMode, get_broker_config

logger = logging.getLogger(__name__)


@dataclass
class IBKRConnectionParams:
    """Parameters for IBKR connection"""
    host: str = "127.0.0.1"
    port: int = 7497  # Default to paper trading
    client_id: int = 1
    timeout: float = 10.0
    trading_mode: TradingMode = TradingMode.PAPER


class IBKRConnectionWorker(QThread):
    """
    Background worker thread for IBKR connection to prevent UI freezing.
    Uses proper asyncio event loop handling for ib_insync.
    """

    # Signals
    connection_success = Signal(object)  # IB client instance
    connection_failed = Signal(str)  # Error message
    connection_progress = Signal(str)  # Status updates
    disconnected = Signal()  # Connection lost

    def __init__(self, connection_params: IBKRConnectionParams):
        super().__init__()
        self.params = connection_params
        self.ib = None
        self.should_stop = False
        self.loop = None

    def run(self):
        """Main connection logic running in separate thread with proper asyncio handling"""
        if not IBKR_AVAILABLE:
            self.connection_failed.emit(
                "ib_insync library not available. Please install: pip install ib_insync"
            )
            return

        try:
            # Create new event loop for this thread
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

            self.connection_progress.emit("Initializing IB client...")

            # Run the async connection in the event loop
            self.loop.run_until_complete(self._async_connect())

        except Exception as e:
            logger.error(f"IBKR connection error: {e}", exc_info=True)
            self.connection_failed.emit(f"Connection error: {str(e)}")
        finally:
            # Clean up the event loop
            if self.loop and not self.loop.is_closed():
                try:
                    self.loop.close()
                except:
                    pass

    async def _async_connect(self):
        """Async connection method that runs in the event loop"""
        try:
            # Create IB instance
            self.ib = IB()

            # Set up event handlers
            self.ib.disconnectedEvent += self._on_disconnected
            self.ib.errorEvent += self._on_error

            self.connection_progress.emit(
                f"Connecting to TWS/Gateway at {self.params.host}:{self.params.port}..."
            )

            # Attempt async connection with increased timeout
            await self.ib.connectAsync(
                host=self.params.host,
                port=self.params.port,
                clientId=self.params.client_id,
                timeout=15  # Increased timeout for slow connections
            )

            if self.ib.isConnected():
                self.connection_progress.emit("Connection established! Validating account...")

                # Validate connection by requesting account info
                account_valid = await self._validate_connection_async()
                if account_valid:
                    self.connection_success.emit(self.ib)
                else:
                    self.connection_failed.emit("Connection validation failed")
            else:
                self.connection_failed.emit("Failed to establish connection")

        except asyncio.TimeoutError:
            self.connection_failed.emit(
                "Connection timeout. TWS/Gateway found but API not responding.\n"
                "Please check:\n"
                "• API is enabled in TWS/Gateway settings\n"
                "• Correct port is configured\n"
                "• Client ID is not already in use"
            )
        except ConnectionRefusedError:
            self.connection_failed.emit(
                "Connection refused. Is TWS or IB Gateway running?"
            )
        except Exception as e:
            logger.error(f"Async connection error: {e}", exc_info=True)
            error_msg = str(e)

            # Provide helpful error messages based on common issues
            if "timeout" in error_msg.lower():
                self.connection_failed.emit(
                    "Connection timeout. Check TWS/Gateway API settings:\n"
                    "• Enable 'Socket Clients' in API settings\n"
                    "• Verify port number matches trading mode\n"
                    "• Try a different Client ID"
                )
            elif "refused" in error_msg.lower():
                self.connection_failed.emit(
                    "Connection refused. TWS/Gateway not running or wrong port."
                )
            elif "already connected" in error_msg.lower():
                self.connection_failed.emit(
                    "Client ID already in use. Try a different Client ID (2, 3, 4, etc.)"
                )
            else:
                self.connection_failed.emit(f"Connection error: {error_msg}")

    async def _validate_connection_async(self) -> bool:
        """Validate the connection by checking account info asynchronously"""
        try:
            self.connection_progress.emit("Requesting account information...")

            # Request account summary to validate connection
            account_summary = self.ib.accountSummary()

            # Wait a bit longer for the data to arrive
            await asyncio.sleep(2)

            if account_summary:
                self.connection_progress.emit("Account validated successfully")
                return True
            else:
                # Try requesting managed accounts as fallback
                managed_accounts = self.ib.managedAccounts()
                if managed_accounts:
                    self.connection_progress.emit("Account access confirmed")
                    return True

            return False
        except Exception as e:
            logger.error(f"Connection validation failed: {e}")
            return False

    def _on_disconnected(self):
        """Handle disconnection event"""
        logger.warning("IBKR connection lost")
        self.disconnected.emit()

    def _on_error(self, reqId, errorCode, errorString, contract):
        """Handle IBKR error events"""
        logger.error(f"IBKR Error {errorCode}: {errorString}")

        # Critical errors that indicate connection problems
        critical_errors = [1100, 1101, 1102, 2104, 2106, 2108]
        if errorCode in critical_errors:
            self.connection_failed.emit(f"Critical error {errorCode}: {errorString}")

    def stop(self):
        """Stop the worker and disconnect"""
        self.should_stop = True
        if self.ib and self.ib.isConnected():
            try:
                # Schedule disconnection in the event loop
                if self.loop and not self.loop.is_closed():
                    asyncio.run_coroutine_threadsafe(self.ib.disconnectAsync(), self.loop)
            except Exception as e:
                logger.error(f"Error disconnecting: {e}")
        self.quit()


class IBKRAuth(QObject):
    """
    Main IBKR authentication manager.
    Handles connection setup, validation, and management with proper async support.
    """

    # Signals for external components
    connection_established = Signal(object)  # IB client
    connection_lost = Signal()
    status_updated = Signal(str)

    def __init__(self):
        super().__init__()
        self.ib_client: Optional[IB] = None
        self.connection_params: Optional[IBKRConnectionParams] = None
        self.worker: Optional[IBKRConnectionWorker] = None
        self.is_connected = False
        self.account_info = {}

        # Connection monitoring timer
        self.heartbeat_timer = QTimer()
        self.heartbeat_timer.timeout.connect(self._check_connection_health)
        self.heartbeat_timer.setInterval(30000)  # Check every 30 seconds

    def connect_to_tws(self, trading_mode: TradingMode,
                       host: str = "127.0.0.1",
                       client_id: int = 1) -> bool:
        """
        Initiate connection to TWS/Gateway

        Args:
            trading_mode: Paper or live trading
            host: TWS/Gateway host address
            client_id: Unique client identifier

        Returns:
            bool: True if connection initiated successfully
        """
        try:
            # Get broker config for port selection
            config = get_broker_config(BrokerMode.AMERICA)
            port = config.default_ports.get(trading_mode.value, 7497)

            self.connection_params = IBKRConnectionParams(
                host=host,
                port=port,
                client_id=client_id,
                trading_mode=trading_mode
            )

            # Start connection worker
            self.worker = IBKRConnectionWorker(self.connection_params)
            self.worker.connection_success.connect(self._on_connection_success)
            self.worker.connection_failed.connect(self._on_connection_failed)
            self.worker.connection_progress.connect(self._on_connection_progress)
            self.worker.disconnected.connect(self._on_disconnected)

            self.worker.start()
            return True

        except Exception as e:
            logger.error(f"Failed to initiate IBKR connection: {e}")
            self.status_updated.emit(f"Connection failed: {e}")
            return False

    def _on_connection_success(self, ib_client):
        """Handle successful connection"""
        self.ib_client = ib_client
        self.is_connected = True

        # Get account information
        self._fetch_account_info()

        # Start connection monitoring
        self.heartbeat_timer.start()

        self.status_updated.emit("Connected to IBKR successfully")
        self.connection_established.emit(ib_client)

        logger.info(f"IBKR connection established - Client ID: {self.connection_params.client_id}")

    def _on_connection_failed(self, error_message: str):
        """Handle connection failure"""
        self.is_connected = False
        self.ib_client = None

        self.status_updated.emit(f"Connection failed: {error_message}")
        logger.error(f"IBKR connection failed: {error_message}")

        # Cleanup worker
        if self.worker:
            self.worker.quit()
            self.worker = None

    def _on_connection_progress(self, message: str):
        """Handle connection progress updates"""
        self.status_updated.emit(message)
        logger.info(f"IBKR connection: {message}")

    def _on_disconnected(self):
        """Handle unexpected disconnection"""
        self.is_connected = False
        self.heartbeat_timer.stop()

        self.status_updated.emit("Connection lost")
        self.connection_lost.emit()

        logger.warning("IBKR connection lost unexpectedly")

    def _fetch_account_info(self):
        """Fetch and cache account information"""
        try:
            if self.ib_client and self.ib_client.isConnected():
                # Get account summary
                account_summary = self.ib_client.accountSummary()

                # Convert to dictionary for easy access
                self.account_info = {}
                for item in account_summary:
                    self.account_info[item.tag] = item.value

                # Get managed accounts
                managed_accounts = self.ib_client.managedAccounts()
                self.account_info['managed_accounts'] = managed_accounts

                logger.info(f"Account info fetched: {len(self.account_info)} items")

        except Exception as e:
            logger.error(f"Failed to fetch account info: {e}")
            self.account_info = {}

    def _check_connection_health(self):
        """Periodic connection health check"""
        if self.ib_client:
            try:
                if not self.ib_client.isConnected():
                    logger.warning("Connection health check failed - not connected")
                    self._on_disconnected()
                else:
                    # Optional: Send a simple request to verify connection
                    pass
            except Exception as e:
                logger.error(f"Connection health check error: {e}")
                self._on_disconnected()

    def disconnect(self):
        """Gracefully disconnect from IBKR"""
        try:
            self.heartbeat_timer.stop()

            if self.worker:
                self.worker.stop()
                self.worker.wait(5000)  # Wait up to 5 seconds
                self.worker = None

            if self.ib_client and self.ib_client.isConnected():
                # For cleanup, we'll just disconnect synchronously
                try:
                    self.ib_client.disconnect()
                except:
                    pass
                logger.info("Disconnected from IBKR")

            self.is_connected = False
            self.ib_client = None
            self.account_info = {}

        except Exception as e:
            logger.error(f"Error during disconnect: {e}")

    def get_client(self) -> Optional[IB]:
        """Get the IB client instance"""
        return self.ib_client if self.is_connected else None

    def get_account_info(self) -> Dict[str, Any]:
        """Get cached account information"""
        return self.account_info.copy()

    def get_connection_status(self) -> Dict[str, Any]:
        """Get current connection status"""
        return {
            'connected': self.is_connected,
            'client_id': self.connection_params.client_id if self.connection_params else None,
            'host': self.connection_params.host if self.connection_params else None,
            'port': self.connection_params.port if self.connection_params else None,
            'trading_mode': self.connection_params.trading_mode.value if self.connection_params else None,
            'account_count': len(self.account_info)
        }

    def test_connection(self, host: str = "127.0.0.1", port: int = 7497) -> bool:
        """
        Test connection to TWS/Gateway without establishing persistent connection

        Args:
            host: TWS/Gateway host
            port: TWS/Gateway port

        Returns:
            bool: True if connection test successful
        """
        if not IBKR_AVAILABLE:
            logger.error("ib_insync not available for connection test")
            return False

        try:
            # Simple socket test instead of full IB connection for testing
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0

        except Exception as e:
            logger.debug(f"Connection test failed: {e}")
            return False


class IBKRConnectionValidator:
    """
    Utility class for validating IBKR connection prerequisites
    """

    @staticmethod
    def check_tws_running(port: int = 7497) -> Dict[str, Any]:
        """
        Check if TWS or IB Gateway is running on specified port

        Returns:
            Dict with status information
        """
        result = {
            'running': False,
            'port': port,
            'message': '',
            'suggestions': []
        }

        if not IBKR_AVAILABLE:
            result['message'] = "ib_insync library not installed"
            result['suggestions'] = ["Install ib_insync: pip install ib_insync"]
            return result

        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            connection_result = sock.connect_ex(('127.0.0.1', port))
            sock.close()

            if connection_result == 0:
                result['running'] = True
                result['message'] = f"TWS/Gateway detected on port {port}"
            else:
                result['message'] = f"No service detected on port {port}"
                result['suggestions'] = [
                    "Start TWS or IB Gateway",
                    f"Ensure it's configured for port {port}",
                    "Check if port is correct for your trading mode",
                    "Paper Trading: port 7497, Live Trading: port 7496"
                ]

        except Exception as e:
            result['message'] = f"Port check failed: {e}"
            result['suggestions'] = ["Check network connectivity", "Verify TWS/Gateway installation"]

        return result

    @staticmethod
    def get_recommended_settings() -> Dict[str, Any]:
        """Get recommended TWS/Gateway settings for API connection"""
        return {
            'api_settings': {
                'enable_activex_and_socket_clients': True,
                'socket_port': 7497,  # For paper trading
                'master_api_client_id': 0,
                'read_only_api': False,
                'download_open_orders_on_connection': True
            },
            'trading_permissions': {
                'paper_trading_account': True,
                'api_trading_enabled': True,
                'outside_rth': True  # Allow trading outside regular hours
            },
            'security': {
                'trusted_ips': ['127.0.0.1'],
                'bypass_order_precautions': False  # Keep safety checks
            }
        }

    @staticmethod
    def validate_client_id(client_id: int) -> Dict[str, Any]:
        """Validate client ID for IBKR connection"""
        result = {
            'valid': False,
            'message': '',
            'recommendations': []
        }

        if not isinstance(client_id, int):
            result['message'] = "Client ID must be an integer"
            return result

        if client_id < 0:
            result['message'] = "Client ID must be non-negative"
            return result

        if client_id == 0:
            result['message'] = "Client ID 0 is reserved for TWS"
            result['recommendations'] = ["Use a client ID between 1-100"]
            return result

        if client_id > 100:
            result['message'] = "Client ID should typically be between 1-100"
            result['recommendations'] = ["Consider using a lower client ID"]

        result['valid'] = True
        result['message'] = f"Client ID {client_id} is valid"
        return result


# Utility functions for IBKR integration
def get_default_connection_params(trading_mode: TradingMode) -> IBKRConnectionParams:
    """Get default connection parameters for specified trading mode"""
    config = get_broker_config(BrokerMode.AMERICA)
    port = config.default_ports.get(trading_mode.value, 7497)

    return IBKRConnectionParams(
        host="127.0.0.1",
        port=port,
        client_id=1,
        trading_mode=trading_mode
    )


def create_ibkr_auth() -> IBKRAuth:
    """Factory function to create IBKR authentication instance"""
    return IBKRAuth()


def is_ibkr_available() -> bool:
    """Check if IBKR functionality is available"""
    return IBKR_AVAILABLE


def get_ibkr_requirements() -> List[str]:
    """Get list of requirements for IBKR functionality"""
    requirements = [
        "ib_insync>=0.9.86",
        "TWS or IB Gateway installed and running",
        "API connections enabled in TWS/Gateway settings",
        "Appropriate trading permissions"
    ]
    return requirements


# Exception classes for IBKR-specific errors
class IBKRConnectionError(Exception):
    """Raised when IBKR connection fails"""
    pass


class IBKRAuthenticationError(Exception):
    """Raised when IBKR authentication fails"""
    pass


class IBKRNotAvailableError(Exception):
    """Raised when IBKR functionality is not available"""
    pass