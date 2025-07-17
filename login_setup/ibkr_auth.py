# login_setup/ibkr_auth.py
"""
Enhanced Interactive Brokers authentication with robust IPv6 support.
This version specifically addresses IPv6 localhost connection issues on Linux.
"""

import logging
import asyncio
import socket
import time
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass
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
    """Parameters for IBKR connection with enhanced IPv6 support"""
    host: str = "::1"
    port: int = 7497
    client_id: int = 1
    timeout: float = 10.0
    trading_mode: TradingMode = TradingMode.PAPER
    prefer_ipv6: bool = True
    fallback_hosts: List[str] = None

    def __post_init__(self):
        if self.fallback_hosts is None:
            # IPv6 first, then IPv4 fallback
            self.fallback_hosts = ["::1", "127.0.0.1", "localhost"]


class NetworkUtils:
    """Enhanced network utilities for IPv6/IPv4 handling"""

    @staticmethod
    def resolve_host(hostname: str, port: int) -> List[Tuple[str, int, int]]:
        """
        Resolve hostname to list of (host, port, family) tuples.
        Returns IPv6 addresses first if available.
        """
        addresses = []

        try:
            # Get all address info for the hostname
            addr_infos = socket.getaddrinfo(
                hostname, port,
                socket.AF_UNSPEC,
                socket.SOCK_STREAM
            )

            # Separate IPv6 and IPv4 addresses
            ipv6_addrs = []
            ipv4_addrs = []

            for family, socktype, proto, canonname, sockaddr in addr_infos:
                if family == socket.AF_INET6:
                    ipv6_addrs.append((sockaddr[0], sockaddr[1], family))
                elif family == socket.AF_INET:
                    ipv4_addrs.append((sockaddr[0], sockaddr[1], family))

            # Prefer IPv6, then IPv4
            addresses.extend(ipv6_addrs)
            addresses.extend(ipv4_addrs)

            logger.debug(f"Resolved {hostname}:{port} to {len(addresses)} addresses")

        except socket.gaierror as e:
            logger.warning(f"Failed to resolve {hostname}: {e}")

        return addresses

    @staticmethod
    def test_socket_connectivity(host: str, port: int, family: int, timeout: float = 3.0) -> Dict[str, Any]:
        """Test socket connectivity with specific address family"""
        result = {
            'success': False,
            'host': host,
            'port': port,
            'family': 'IPv6' if family == socket.AF_INET6 else 'IPv4',
            'error': None,
            'latency_ms': None
        }

        addr = None
        try:
            start_time = time.time()
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(timeout)

            # **FIXED: Use the correct tuple format for IPv6 connections**
            if family == socket.AF_INET6:
                addr = (host, port, 0, 0)
            else:
                addr = (host, port)

            connect_result = sock.connect_ex(addr)
            sock.close()

            if connect_result == 0:
                result['success'] = True
                result['latency_ms'] = round((time.time() - start_time) * 1000, 2)
            else:
                result['error'] = f"Connection refused (error {connect_result})"

        except socket.timeout:
            result['error'] = "Connection timeout"
        except Exception as e:
            result['error'] = f"Socket error for {addr}: {e}"

        return result

    @staticmethod
    def find_best_connection_address(hosts: List[str], port: int) -> Optional[Tuple[str, int, int]]:
        """Find the best working address from a list of hosts"""

        for hostname in hosts:
            logger.debug(f"Testing connectivity to {hostname}:{port}")

            addresses = NetworkUtils.resolve_host(hostname, port)

            for host, resolved_port, family in addresses:
                test_result = NetworkUtils.test_socket_connectivity(host, resolved_port, family)

                if test_result['success']:
                    logger.info(f"✅ Found working connection: {host}:{resolved_port} ({test_result['family']}) "
                                f"latency: {test_result['latency_ms']}ms")
                    return (host, resolved_port, family)
                else:
                    logger.debug(f"❌ {hostname} ({test_result['family']} on {host}): {test_result['error']}")

        return None


class IBKRConnectionWorker(QThread):
    """Enhanced background worker for IBKR connection with IPv6 support"""

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
        self.best_address = None

    def run(self):
        """Main connection logic with enhanced IPv6 support"""
        if not IBKR_AVAILABLE:
            self.connection_failed.emit("ib_insync not available. Please install: pip install ib_insync")
            return

        try:
            # Step 1: Find the best working address
            self.connection_progress.emit("🔍 Testing network connectivity...")
            self.best_address = self._find_working_address()

            if not self.best_address:
                self._emit_no_connectivity_error()
                return

            if self.should_stop:
                return

            # Step 2: Create async event loop and connect
            self.connection_progress.emit("⚡ Establishing IBKR connection...")

            # Create fresh event loop for this thread
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

            # Configure ib_insync logging
            if hasattr(util, 'logToConsole'):
                util.logToConsole(level=logging.WARNING)

            # Run connection
            self.loop.run_until_complete(self._async_connect())

        except Exception as e:
            logger.error(f"IBKR connection error: {e}", exc_info=True)
            if not self.should_stop:
                self._handle_connection_error(e)
        finally:
            self._cleanup()

    def _find_working_address(self) -> Optional[Tuple[str, int, int]]:
        """Find a working address for connection"""
        if self.should_stop:
            return None

        # Test the primary host first
        primary_hosts = [self.params.host]

        # Add fallback hosts if primary fails
        if self.params.fallback_hosts:
            for host in self.params.fallback_hosts:
                if host not in primary_hosts:
                    primary_hosts.append(host)

        return NetworkUtils.find_best_connection_address(primary_hosts, self.params.port)

    async def _async_connect(self):
        """Async connection method"""
        if self.should_stop or not self.best_address:
            return

        try:
            host, port, family = self.best_address
            family_name = 'IPv6' if family == socket.AF_INET6 else 'IPv4'

            self.connection_progress.emit(f"🔗 Connecting via {family_name} to {host}:{port}...")

            # Create IB client
            self.ib = IB()

            # Use the working address for connection
            await self.ib.connectAsync(
                host=host,
                port=port,
                clientId=self.params.client_id,
                timeout=self.params.timeout
            )

            if self.should_stop:
                return

            if self.ib.isConnected():
                self.connection_progress.emit("✅ Connected! Testing API functionality...")

                # Test API functionality
                if await self._test_api_functionality():
                    if not self.should_stop:
                        logger.info(f"IBKR connection successful via {family_name}: {host}:{port}")
                        self.connection_success.emit(self.ib)
                else:
                    if not self.should_stop:
                        self.connection_failed.emit("Connected but API functionality test failed")
            else:
                if not self.should_stop:
                    self.connection_failed.emit("Connection established but not confirmed")

        except asyncio.TimeoutError:
            if not self.should_stop:
                self._handle_timeout_error()
        except ConnectionRefusedError:
            if not self.should_stop:
                self._handle_connection_refused()
        except Exception as e:
            if not self.should_stop:
                self._handle_connection_error(e)

    async def _test_api_functionality(self) -> bool:
        """Test API functionality after connection"""
        if self.should_stop or not self.ib:
            return False

        try:
            # Test 1: Request current time (basic API test)
            current_time = self.ib.reqCurrentTime()
            if not current_time:
                logger.warning("Current time request failed")
                return False

            # Test 2: Get managed accounts (may fail for some accounts)
            try:
                accounts = self.ib.managedAccounts()
                if accounts:
                    logger.info(f"Found managed accounts: {accounts}")
                else:
                    logger.info("No managed accounts found (normal for some account types)")
            except Exception as e:
                logger.warning(f"Account query failed but connection OK: {e}")

            return True

        except Exception as e:
            logger.error(f"API functionality test failed: {e}")
            return False

    def _emit_no_connectivity_error(self):
        """Emit error when no connectivity is found"""
        hosts_tested = [self.params.host] + (self.params.fallback_hosts or [])

        error_msg = (
            f"❌ No connectivity to IB Gateway on port {self.params.port}\n\n"
            f"Hosts tested: {', '.join(set(hosts_tested))}\n\n"
            "Troubleshooting steps:\n"
            "1. Ensure IB Gateway/TWS is running and logged in\n"
            "2. Check Gateway API settings (Configure → API)\n"
            "   - 'Enable ActiveX and Socket Clients' must be checked\n"
            "3. Verify correct port for your trading mode:\n"
            "   • Paper Trading: 7497\n"
            "   • Live Trading: 7496\n"
            "4. Check firewall settings on your Linux system\n"
            "5. Try restarting the IB Gateway application completely"
        )

        self.connection_failed.emit(error_msg)

    def _handle_timeout_error(self):
        """Handle timeout errors"""
        family_name = 'IPv6' if self.best_address and self.best_address[2] == socket.AF_INET6 else 'IPv4'

        error_msg = (
            f"❌ Connection timeout via {family_name}\n\n"
            "Common fixes:\n"
            "1. Restart IB Gateway completely (this often works)\n"
            "2. Try a different Client ID (e.g., 2, 3, 4)\n"
            "3. Check for popup dialogs in the Gateway window\n"
            "4. Verify Gateway is fully logged in and stable\n"
            "5. Check your system's firewall or security software"
        )
        self.connection_failed.emit(error_msg)

    def _handle_connection_refused(self):
        """Handle connection refused errors"""
        error_msg = (
            "❌ Connection refused by IB Gateway\n\n"
            "Please check the following:\n"
            "• IB Gateway/TWS is running and you are logged in.\n"
            "• API is enabled in Gateway settings.\n"
            "• You are using the correct port for your trading mode.\n"
            "• No other applications are using the same Client ID.\n"
            "• Your firewall allows the connection to the port."
        )
        self.connection_failed.emit(error_msg)

    def _handle_connection_error(self, e: Exception):
        """Handle general connection errors"""
        error_str = str(e).lower()

        if "already connected" in error_str or "duplicate" in error_str:
            error_msg = f"❌ Client ID {self.params.client_id} is already in use.\nTry a different Client ID (e.g., {self.params.client_id + 1}, {self.params.client_id + 2})."
        elif "refused" in error_str:
            error_msg = "❌ Gateway refused the connection.\nCheck that Gateway is running and the API is enabled in its settings."
        elif "timeout" in error_str or "timed out" in error_str:
            error_msg = "❌ Connection timed out.\nTry restarting the Gateway and using a different Client ID."
        elif "address family" in error_str or "ipv6" in error_str:
            error_msg = "❌ An IPv6 connection issue occurred.\nThe application will try falling back to IPv4. If this persists, check your system's network configuration."
        else:
            error_msg = f"❌ Connection failed: {str(e)}\nTry restarting the Gateway or using a different Client ID."

        self.connection_failed.emit(error_msg)

    def _cleanup(self):
        """Clean up resources"""
        try:
            if self.ib and self.ib.isConnected():
                self.ib.disconnect()
        except:
            pass

        try:
            if self.loop and not self.loop.is_closed():
                # Give tasks a moment to finish
                tasks = asyncio.all_tasks(loop=self.loop)
                for task in tasks:
                    task.cancel()

                # Gather and close
                async def gather_and_close():
                    await asyncio.gather(*tasks, return_exceptions=True)
                    self.loop.stop()

                self.loop.run_until_complete(gather_and_close())
                self.loop.close()
        except:
            pass

    def stop(self):
        """Stop the worker safely"""
        self.should_stop = True
        self.thread().requestInterruption()
        if self.ib:
            try:
                # This needs to be run in the loop
                if self.loop and self.loop.is_running():
                    self.loop.call_soon_threadsafe(self.ib.disconnect)
                else:
                    self.ib.disconnect()
            except:
                pass


class IBKRAuth(QObject):
    """Enhanced IBKR authentication manager with IPv6 support"""

    # Signals
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

        # Connection monitoring
        self.heartbeat_timer = QTimer()
        self.heartbeat_timer.timeout.connect(self._check_connection_health)
        self.heartbeat_timer.setInterval(30000)  # 30 seconds

    def connect_to_tws(self, trading_mode: TradingMode,
                       host: str = "::1",
                       client_id: int = 1) -> bool:
        """Connect to TWS/Gateway with enhanced error handling"""
        try:
            # Clean up existing connection
            if self.worker:
                self.worker.stop()
                self.worker.wait(3000)
                self.worker = None

            # Get port from broker config
            config = get_broker_config(BrokerMode.AMERICA)
            port = config.default_ports.get(trading_mode.value, 7497)

            # Create connection parameters with fallback hosts
            self.connection_params = IBKRConnectionParams(
                host=host,
                port=port,
                client_id=client_id,
                timeout=10.0,
                trading_mode=trading_mode,
                fallback_hosts=["::1", "127.0.0.1", "localhost"]
            )

            # Create and start worker
            self.worker = IBKRConnectionWorker(self.connection_params)
            self.worker.connection_success.connect(self._on_connection_success)
            self.worker.connection_failed.connect(self._on_connection_failed)
            self.worker.connection_progress.connect(self._on_connection_progress)
            self.worker.disconnected.connect(self._on_disconnected)
            self.worker.finished.connect(self._on_worker_finished)

            self.worker.start()
            return True

        except Exception as e:
            logger.error(f"Failed to initiate IBKR connection: {e}")
            self.status_updated.emit(f"❌ Connection setup failed: {e}")
            return False

    def _on_connection_success(self, ib_client):
        """Handle successful connection"""
        self.ib_client = ib_client
        self.is_connected = True

        # Fetch account information
        self._fetch_account_info()

        # Start monitoring
        self.heartbeat_timer.start()

        self.status_updated.emit("✅ IBKR connection established!")
        self.connection_established.emit(ib_client)

        logger.info(f"IBKR connected - Client ID: {self.connection_params.client_id}")

    def _on_connection_failed(self, error_message: str):
        """Handle connection failure"""
        self.is_connected = False
        self.ib_client = None
        self.status_updated.emit("❌ Connection failed")
        logger.error(f"IBKR connection failed: {error_message}")

    def _on_connection_progress(self, message: str):
        """Handle connection progress"""
        self.status_updated.emit(message)
        logger.info(f"IBKR: {message}")

    def _on_disconnected(self):
        """Handle disconnection"""
        self.is_connected = False
        self.heartbeat_timer.stop()
        self.status_updated.emit("❌ Disconnected")
        self.connection_lost.emit()
        logger.warning("IBKR connection lost")

    def _on_worker_finished(self):
        """Handle worker completion"""
        if self.worker:
            self.worker.deleteLater()
            self.worker = None

    def _fetch_account_info(self):
        """Fetch account information"""
        try:
            if self.ib_client and self.ib_client.isConnected():
                account_summary = self.ib_client.accountSummary()
                self.account_info = {item.tag: item.value for item in account_summary}

                managed_accounts = self.ib_client.managedAccounts()
                self.account_info['managed_accounts'] = managed_accounts

                logger.info(f"Account info fetched: {len(self.account_info)} items")
        except Exception as e:
            logger.error(f"Failed to fetch account info: {e}")
            self.account_info = {}

    def _check_connection_health(self):
        """Check connection health"""
        if self.ib_client:
            try:
                if not self.ib_client.isConnected():
                    self._on_disconnected()
            except Exception as e:
                logger.error(f"Connection health check failed: {e}")
                self._on_disconnected()

    def disconnect(self):
        """Disconnect from IBKR"""
        try:
            self.heartbeat_timer.stop()

            if self.worker:
                self.worker.stop()
                self.worker.wait(3000)
                self.worker = None

            if self.ib_client and self.ib_client.isConnected():
                self.ib_client.disconnect()

            self.is_connected = False
            self.ib_client = None
            self.account_info = {}

            logger.info("Disconnected from IBKR")

        except Exception as e:
            logger.error(f"Error during disconnect: {e}")

    # Public interface methods
    def get_client(self) -> Optional[IB]:
        """Get IB client instance"""
        return self.ib_client if self.is_connected else None

    def get_account_info(self) -> Dict[str, Any]:
        """Get account information"""
        return self.account_info.copy()

    def get_connection_status(self) -> Dict[str, Any]:
        """Get connection status"""
        return {
            'connected': self.is_connected,
            'client_id': self.connection_params.client_id if self.connection_params else None,
            'host': self.connection_params.host if self.connection_params else None,
            'port': self.connection_params.port if self.connection_params else None,
            'trading_mode': self.connection_params.trading_mode.value if self.connection_params else None,
            'account_count': len(self.account_info)
        }


class IBKRConnectionValidator:
    """Enhanced connection validator with IPv6 support"""

    @staticmethod
    def comprehensive_diagnosis(port: int = 7497) -> Dict[str, Any]:
        """Comprehensive connection diagnosis"""
        diagnosis = {
            'port': port,
            'connectivity_results': [],
            'api_responsive': False,
            'best_address': None,
            'recommendations': []
        }

        if not IBKR_AVAILABLE:
            diagnosis['recommendations'] = ["Install ib_insync: pip install ib_insync"]
            return diagnosis

        # Test all potential addresses
        hosts_to_test = ["::1", "127.0.0.1", "localhost"]

        for hostname in hosts_to_test:
            addresses = NetworkUtils.resolve_host(hostname, port)

            for host, resolved_port, family in addresses:
                result = NetworkUtils.test_socket_connectivity(host, resolved_port, family)
                result['hostname'] = hostname
                diagnosis['connectivity_results'].append(result)

                if result['success'] and not diagnosis['best_address']:
                    diagnosis['best_address'] = (host, resolved_port, family)

        # Test API responsiveness on best address
        if diagnosis['best_address']:
            host, port, family = diagnosis['best_address']
            diagnosis['api_responsive'] = IBKRConnectionValidator._test_api_responsiveness(host, port, family)

        # Generate recommendations
        diagnosis['recommendations'] = IBKRConnectionValidator._generate_recommendations(diagnosis)

        return diagnosis

    @staticmethod
    def _test_api_responsiveness(host: str, port: int, family: int) -> bool:
        """Test if API is responsive on given address"""
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(3)

            if family == socket.AF_INET6:
                sock.connect((host, port, 0, 0))
            else:
                sock.connect((host, port))

            # Send a basic message to test API responsiveness
            sock.send(b'API\0')
            sock.settimeout(1)
            response = sock.recv(100)
            sock.close()

            return len(response) > 0

        except Exception:
            return False

    @staticmethod
    def _generate_recommendations(diagnosis: Dict[str, Any]) -> List[str]:
        """Generate recommendations based on diagnosis"""
        recommendations = []

        working_connections = [r for r in diagnosis['connectivity_results'] if r['success']]

        if not working_connections:
            recommendations.extend([
                "x"
            ])
        elif not diagnosis['api_responsive']:
            recommendations.extend([
                "x"
            ])
        else:
            recommendations.extend([
                "x"
            ])

        return recommendations

    @staticmethod
    def quick_check(port: int = 7497) -> Dict[str, Any]:
        """Quick connectivity check"""
        best_address = NetworkUtils.find_best_connection_address(["::1", "127.0.0.1"], port)

        return {
            'port': port,
            'accessible': best_address is not None,
            'best_address': best_address,
            'message': f"Gateway accessible on port {port}" if best_address else f"No gateway found on port {port}"
        }


# Utility functions
def is_ibkr_available() -> bool:
    """Check if IBKR functionality is available"""
    return IBKR_AVAILABLE


def create_ibkr_auth() -> IBKRAuth:
    """Create IBKR authentication instance"""
    return IBKRAuth()


def diagnose_connection(port: int = 7497) -> str:
    """Get formatted diagnosis report"""
    diagnosis = IBKRConnectionValidator.comprehensive_diagnosis(port)

    report = f"🔍 IBKR Connection Diagnosis (Port {port}):\n\n"

    # Connection results
    working_count = sum(1 for r in diagnosis['connectivity_results'] if r['success'])
    total_count = len(diagnosis['connectivity_results'])

    report += f"📡 Connectivity: {working_count}/{total_count} addresses accessible\n"

    if diagnosis['best_address']:
        host, port, family = diagnosis['best_address']
        family_name = 'IPv6' if family == socket.AF_INET6 else 'IPv4'
        report += f"🎯 Best address: {host}:{port} ({family_name})\n"

    report += f"🔌 API responsive: {'✅' if diagnosis['api_responsive'] else '❌'}\n\n"

    # Recommendations
    if diagnosis['recommendations']:
        report += "📝 Recommendations:\n"
        for i, rec in enumerate(diagnosis['recommendations'], 1):
            report += f"{i}. {rec}\n"

    return report


def test_connection_now(port: int = 7497) -> str:
    """Test connection immediately and return results"""
    try:
        if not IBKR_AVAILABLE:
            return "❌ ib_insync not available. Install with: pip install ib_insync"

        from ib_insync import IB

        # Find best address
        best_address = NetworkUtils.find_best_connection_address(["::1", "127.0.0.1"], port)

        if not best_address:
            return f"❌ No connectivity to port {port}. Check if IB Gateway is running."

        host, port, family = best_address
        family_name = 'IPv6' if family == socket.AF_INET6 else 'IPv4'

        # Test actual IB connection
        ib = IB()
        ib.connect(host=host, port=port, clientId=999, timeout=15)

        if ib.isConnected():
            current_time = ib.reqCurrentTime()
            ib.disconnect()
            return f"✅ Connection successful via {family_name}!\nAPI test: {current_time}"
        else:
            return f"❌ Connection failed via {family_name} to {host}:{port}"

    except Exception as e:
        return f"❌ Connection test failed: {e}"