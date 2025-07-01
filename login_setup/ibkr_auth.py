# login_setup/ibkr_auth.py
"""
Interactive Brokers authentication with Linux timeout fixes.
This version addresses common timeout issues on Linux systems.
"""

import logging
import asyncio
import time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
import asyncio
import threading
from PySide6.QtCore import QThread, Signal, QTimer, QObject

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
    host: str = "::1"
    port: int = 7497  # Default to paper trading
    client_id: int = 1
    timeout: float = 15.0  # Reduced from 50.0
    trading_mode: TradingMode = TradingMode.PAPER


class IBKRConnectionWorker(QThread):
    """
    Background worker thread for IBKR connection with improved thread safety.
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

    # Replace the IBKRConnectionWorker.run() method in your ibkr_auth.py with this version:

    def run(self):
        """
        Main connection logic with proper asyncio event loop setup.
        """
        if not IBKR_AVAILABLE:
            self.connection_failed.emit("ib_insync not available. Please install: pip install ib_insync")
            return

        try:
            # Create a new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Run the connection in the event loop
            loop.run_until_complete(self._async_connect())

        except Exception as e:
            logger.error(f"IBKR connection error: {e}", exc_info=True)
            if not self.should_stop:
                self._handle_general_error(e)
        finally:
            # Clean up the event loop
            try:
                if hasattr(self, 'ib') and self.ib and self.ib.isConnected():
                    self.ib.disconnect()
            except:
                pass

            # Close the event loop
            try:
                loop = asyncio.get_event_loop()
                if not loop.is_closed():
                    loop.close()
            except:
                pass

    async def _async_connect(self):
        """Async connection method that runs in the event loop."""
        if self.should_stop:
            return

        try:
            self.connection_progress.emit("Creating IB client...")
            self.ib = IB()

            # Reduce ib_insync logging verbosity
            if hasattr(util, 'logToConsole'):
                util.logToConsole(level=logging.WARNING)

            if self.should_stop:
                return

            self.connection_progress.emit(f"Connecting to {self.params.host}:{self.params.port}...")

            # Use async connection
            await self.ib.connectAsync(
                host=self.params.host,
                port=self.params.port,
                clientId=self.params.client_id,
                timeout=self.params.timeout
            )

            if self.should_stop:
                return

            if self.ib.isConnected():
                self.connection_progress.emit("Connected! Testing API functionality...")

                # Test API functionality
                if await self._test_api_functionality_async():
                    if not self.should_stop:
                        self.connection_success.emit(self.ib)
                else:
                    if not self.should_stop:
                        self.connection_failed.emit("Connected but API test failed")
            else:
                if not self.should_stop:
                    self.connection_failed.emit("Connection failed")

        except asyncio.TimeoutError:
            if not self.should_stop:
                self._handle_timeout_error()
        except ConnectionRefusedError:
            if not self.should_stop:
                self._handle_connection_refused()
        except Exception as e:
            if not self.should_stop:
                self._handle_general_error(e)

    async def _test_api_functionality_async(self) -> bool:
        """Async version of API functionality test."""
        if self.should_stop:
            return False

        try:
            # Test: Request current time
            current_time = self.ib.reqCurrentTime()

            if not current_time:
                logger.warning("Current time request failed")
                return False

            # Test: Get managed accounts (optional)
            try:
                accounts = self.ib.managedAccounts()
                if accounts:
                    logger.info(f"Found managed accounts: {accounts}")
                else:
                    logger.info("No managed accounts found, but connection works")
                return True

            except Exception as e:
                logger.warning(f"Account request failed but connection OK: {e}")
                return True  # Connection is working even if account query fails

        except Exception as e:
            logger.error(f"API functionality test failed: {e}")
            return False


    def _test_api_functionality(self) -> bool:
        """Test that API is fully functional after connection."""
        if self.should_stop:
            return False

        try:
            # Test 1: Request current time (most basic API call)
            current_time = self.ib.reqCurrentTime()

            if not current_time:
                logger.warning("Current time request failed")
                return False

            # Test 2: Get managed accounts (optional)
            try:
                accounts = self.ib.managedAccounts()
                if accounts:
                    logger.info(f"Found managed accounts: {accounts}")
                else:
                    logger.info("No managed accounts found, but connection works")
                return True

            except Exception as e:
                logger.warning(f"Account request failed but connection OK: {e}")
                return True  # Connection is working even if account query fails

        except Exception as e:
            logger.error(f"API functionality test failed: {e}")
            return False

    def _handle_timeout_error(self):
        """Handle timeout errors with Linux-specific guidance."""
        error_msg = (
            "❌ Connection timeout to IB Gateway.\n\n"
            "Quick fixes to try:\n"
            "1. Restart IB Gateway completely\n"
            "2. Try a different Client ID (2, 3, 4, etc.)\n"
            "3. Check for popup dialogs in Gateway\n"
            "4. Ensure Gateway is logged in properly"
        )
        self.connection_failed.emit(error_msg)

    def _handle_connection_refused(self):
        """Handle connection refused errors."""
        error_msg = (
            "❌ Connection refused by IB Gateway.\n\n"
            "Check:\n"
            "• IB Gateway is running and logged in\n"
            "• Correct port (7497=Paper, 7496=Live)\n"
            "• No firewall blocking the connection"
        )
        self.connection_failed.emit(error_msg)

    def _handle_general_error(self, e: Exception):
        """Handle general connection errors with specific guidance."""
        logger.error(f"IBKR connection error: {e}", exc_info=True)

        error_str = str(e).lower()

        if "already connected" in error_str or "duplicate" in error_str:
            error_msg = f"❌ Client ID {self.params.client_id} is already in use.\nTry a different Client ID (2, 3, 4, etc.)"
        elif "refused" in error_str:
            error_msg = "❌ Gateway refused the connection.\nCheck that IB Gateway is running and logged in."
        elif "timeout" in error_str:
            error_msg = "❌ Connection timed out.\nTry restarting IB Gateway completely."
        else:
            error_msg = f"❌ Connection failed: {str(e)}\nTry restarting IB Gateway."

        self.connection_failed.emit(error_msg)

    def stop(self):
        """Stop the worker safely."""
        self.should_stop = True
        if self.ib and self.ib.isConnected():
            try:
                self.ib.disconnect()
            except:
                pass


class IBKRAuth(QObject):
    """
    Main IBKR authentication manager with improved Linux support.
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
                       host: str = "::1",
                       client_id: int = 1) -> bool:
        """
        Initiate connection to TWS/Gateway with improved error handling.
        """
        try:
            # Clean up any existing worker first
            if self.worker:
                self.worker.stop()
                self.worker.wait(2000)  # Wait up to 2 seconds
                self.worker = None

            # Get broker config for port selection
            config = get_broker_config(BrokerMode.AMERICA)
            port = config.default_ports.get(trading_mode.value, 7497)

            self.connection_params = IBKRConnectionParams(
                host=host,
                port=port,
                client_id=client_id,
                timeout=10.0,  # Shorter timeout
                trading_mode=trading_mode
            )

            # Start connection worker
            self.worker = IBKRConnectionWorker(self.connection_params)
            self.worker.connection_success.connect(self._on_connection_success)
            self.worker.connection_failed.connect(self._on_connection_failed)
            self.worker.connection_progress.connect(self._on_connection_progress)
            self.worker.disconnected.connect(self._on_disconnected)

            # Handle thread finished signal
            self.worker.finished.connect(self._on_worker_finished)

            self.worker.start()
            return True

        except Exception as e:
            logger.error(f"Failed to initiate IBKR connection: {e}")
            self.status_updated.emit(f"❌ Connection setup failed: {e}")
            return False

    def _on_worker_finished(self):
        """Handle worker thread finishing."""
        if self.worker:
            self.worker.deleteLater()
            self.worker = None

    def disconnect(self):
        """Gracefully disconnect from IBKR with proper thread cleanup."""
        try:
            self.heartbeat_timer.stop()

            if self.worker:
                self.worker.stop()
                self.worker.wait(3000)  # Wait up to 3 seconds
                if self.worker.isRunning():
                    self.worker.terminate()
                    self.worker.wait(1000)
                self.worker = None

            if self.ib_client and self.ib_client.isConnected():
                self.ib_client.disconnect()

            self.is_connected = False
            self.ib_client = None
            self.account_info = {}

            logger.info("Disconnected from IBKR")

        except Exception as e:
            logger.error(f"Error during disconnect: {e}")
    def _quick_connectivity_check(self, host: str, port: int) -> bool:
        """Quick check if port is accessible with automatic address family detection."""
        try:
            import socket

            # Use getaddrinfo to properly resolve the address and determine the family
            try:
                addr_info = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
                if not addr_info:
                    return False

                # Try the first address family returned
                family, socktype, proto, canonname, sockaddr = addr_info[0]

                sock = socket.socket(family, socktype)
                sock.settimeout(2)
                result = sock.connect_ex(sockaddr)
                sock.close()

                return result == 0

            except socket.gaierror as e:
                logger.debug(f"Address resolution failed for {host}: {e}")
                return False

        except Exception as e:
            logger.debug(f"Quick connectivity check failed: {e}")
            return False

    def _on_connection_success(self, ib_client):
        """Handle successful connection"""
        self.ib_client = ib_client
        self.is_connected = True

        # Get account information
        self._fetch_account_info()

        # Start connection monitoring
        self.heartbeat_timer.start()

        self.status_updated.emit("✅ Connected to IBKR successfully!")
        self.connection_established.emit(ib_client)

        logger.info(f"IBKR connection established - Client ID: {self.connection_params.client_id}")

    def _on_connection_failed(self, error_message: str):
        """Handle connection failure"""
        self.is_connected = False
        self.ib_client = None

        self.status_updated.emit("❌ Connection failed")
        logger.error(f"IBKR connection failed: {error_message}")

        # Cleanup worker
        if self.worker:
            self.worker.quit()
            self.worker = None

    def _on_connection_progress(self, message: str):
        """Handle connection progress updates"""
        self.status_updated.emit(f"🔄 {message}")
        logger.info(f"IBKR connection: {message}")

    def _on_disconnected(self):
        """Handle unexpected disconnection"""
        self.is_connected = False
        self.heartbeat_timer.stop()

        self.status_updated.emit("❌ Connection lost")
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
                self.ib_client.disconnect()
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


class IBKRConnectionValidator:
    """
    Utility class for validating IBKR connection prerequisites
    """

    @staticmethod
    def check_tws_running(port: int = 7497) -> Dict[str, Any]:
        """
        Check if TWS or IB Gateway is running on specified port with robust address handling.
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

            # Try both IPv6 and IPv4
            hosts_to_try = ['::1', '127.0.0.1']

            for host in hosts_to_try:
                try:
                    addr_info = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
                    if not addr_info:
                        continue

                    family, socktype, proto, canonname, sockaddr = addr_info[0]
                    sock = socket.socket(family, socktype)
                    sock.settimeout(2)
                    connection_result = sock.connect_ex(sockaddr)
                    sock.close()

                    if connection_result == 0:
                        result['running'] = True
                        result['message'] = f"TWS/Gateway detected on {host}:{port}"
                        return result

                except (socket.gaierror, OSError):
                    continue

            # If we get here, no connection worked
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
    def diagnose_connection_issue(port: int = 7497) -> Dict[str, Any]:
        """
        Comprehensive diagnosis of connection issues.
        """
        diagnosis = {
            'port_open': False,
            'api_responsive': False,
            'recommendations': []
        }

        # Check 1: Port accessibility
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('::1', port))
            sock.close()

            diagnosis['port_open'] = (result == 0)
        except:
            diagnosis['port_open'] = False

        # Check 2: API responsiveness
        if diagnosis['port_open']:
            try:
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)

                if sock.connect_ex(('::1', port)) == 0:
                    sock.send(b'API\0')
                    sock.settimeout(1)
                    response = sock.recv(100)
                    diagnosis['api_responsive'] = len(response) > 0

                sock.close()
            except:
                diagnosis['api_responsive'] = False

        # Generate recommendations
        if not diagnosis['port_open']:
            diagnosis['recommendations'] = [
                "Start IB Gateway or TWS",
                f"Ensure it's configured for port {port}",
                "Login to your account in Gateway"
            ]
        elif not diagnosis['api_responsive']:
            diagnosis['recommendations'] = [
                "Configure API in IB Gateway: Configure → API Settings",
                "✓ Enable ActiveX and Socket Clients",
                f"Set Socket port to {port}",
                "Set Master API client ID to 0",
                "Click OK and restart Gateway",
                "Check for popup dialogs that need dismissal"
            ]
        else:
            diagnosis['recommendations'] = [
                "Port and API appear ready",
                "Try different Client IDs (1, 2, 3, etc.)",
                "Restart the application if connection still fails"
            ]

        return diagnosis


# Utility functions
def is_ibkr_available() -> bool:
    """Check if IBKR functionality is available"""
    return IBKR_AVAILABLE


def create_ibkr_auth() -> IBKRAuth:
    """Factory function to create IBKR authentication instance"""
    return IBKRAuth()


def diagnose_linux_ibkr_issues(port: int = 7497) -> str:
    """
    Quick diagnosis function that returns a formatted string with recommendations.
    """
    validator = IBKRConnectionValidator()
    diagnosis = validator.diagnose_connection_issue(port)

    result = f"🔍 IBKR Connection Diagnosis (Port {port}):\n"
    result += f"Port accessible: {'✅' if diagnosis['port_open'] else '❌'}\n"
    result += f"API responsive: {'✅' if diagnosis['api_responsive'] else '❌'}\n\n"

    if diagnosis['recommendations']:
        result += "📝 Recommendations:\n"
        for i, rec in enumerate(diagnosis['recommendations'], 1):
            result += f"{i}. {rec}\n"

    return result