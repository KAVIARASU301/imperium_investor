# login_setup/ibkr_auth.py
"""
A robust and correctly implemented module for Interactive Brokers authentication
that safely integrates asyncio with PySide6 QThreads. This version focuses on
maximum resilience and providing clear, actionable error messages.
"""

import logging
import asyncio
import socket
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass
from PySide6.QtCore import QThread, Signal, QObject

try:
    from ib_insync import IB
    IBKR_AVAILABLE = True
except ImportError:
    IBKR_AVAILABLE = False
    IB = None

from login_setup.broker_modes import BrokerMode, TradingMode, get_broker_config

logger = logging.getLogger(__name__)


@dataclass
class IBKRConnectionParams:
    """Parameters for the IBKR connection."""
    host: str = "::1"
    port: int = 7497
    client_id: int = 1
    timeout: float = 30.0  # Increased to a very generous 30 seconds
    trading_mode: TradingMode = TradingMode.PAPER
    fallback_hosts: List[str] = None

    def __post_init__(self):
        if self.fallback_hosts is None:
            self.fallback_hosts = ["::1", "127.0.0.1"]


class IBKRConnectionWorker(QThread):
    """
    Manages the IBKR connection in a dedicated thread with its own asyncio event loop
    to prevent conflicts between PySide6 and asyncio.
    """
    connection_success = Signal(object)
    connection_failed = Signal(str)
    connection_progress = Signal(str)

    def __init__(self, params: IBKRConnectionParams):
        super().__init__()
        self.params = params
        self.ib: Optional[IB] = None
        self._is_running = True

    def run(self):
        """
        The entry point for the thread. It creates, runs, and cleans up the asyncio
        event loop, ensuring it is fully contained within this thread.
        """
        if not IBKR_AVAILABLE:
            self.connection_failed.emit("ib_insync library not found. Please run: pip install ib_insync")
            return

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self._connect_and_manage())
        except Exception as e:
            logger.critical(f"A critical error occurred in the IBKR worker thread: {e}", exc_info=True)
            self.connection_failed.emit(f"A critical worker error occurred: {e}")
        finally:
            loop.close()
            logger.info("IBKR worker thread's event loop has been closed.")

    async def _connect_and_manage(self):
        """The core async function for handling the connection."""
        try:
            self.connection_progress.emit("🔍 Finding a valid network path...")
            host, port, family = self._find_best_connection_address()
            if not host:
                self.connection_failed.emit(self._get_no_connectivity_error_msg())
                return

            family_name = 'IPv6' if family == socket.AF_INET6 else 'IPv4'
            self.connection_progress.emit(f"🔗 Attempting to connect to {host}:{port} via {family_name}...")
            self.ib = IB()
            await self.ib.connectAsync(
                host=host, port=port, clientId=self.params.client_id,
                timeout=self.params.timeout, readonly=(self.params.trading_mode == TradingMode.PAPER)
            )

            if not self._is_running: return

            if self.ib.isConnected():
                self.connection_progress.emit("✅ Connection successful. Verifying API...")
                await self.ib.reqCurrentTimeAsync()
                self.connection_success.emit(self.ib)
            else:
                self.connection_failed.emit("Connection attempt finished but a valid connection was not established.")

        except asyncio.TimeoutError:
            self.connection_failed.emit(self._get_timeout_error_msg())
        except ConnectionRefusedError:
            self.connection_failed.emit("❌ Connection was refused. Is IB Gateway running and API enabled?")
        except Exception as e:
            logger.error(f"An error occurred during connection: {e}", exc_info=True)
            self.connection_failed.emit(f"An unexpected error occurred: {e}")
        finally:
            if self.ib and self.ib.isConnected():
                self.ib.disconnect()

    def _find_best_connection_address(self) -> Tuple[Optional[str], Optional[int], Optional[socket.AddressFamily]]:
        """A simple, synchronous utility to find the first working connection."""
        for hostname in self.params.fallback_hosts:
            try:
                addr_infos = socket.getaddrinfo(hostname, self.params.port, socket.AF_UNSPEC, socket.SOCK_STREAM)
                for family, _, _, _, sockaddr in addr_infos:
                    with socket.socket(family, socket.SOCK_STREAM) as s:
                        s.settimeout(2)
                        addr = (sockaddr[0], self.params.port, 0, 0) if family == socket.AF_INET6 else (sockaddr[0], self.params.port)
                        if s.connect_ex(addr) == 0:
                            return sockaddr[0], self.params.port, family
            except socket.gaierror:
                logger.warning(f"Could not resolve host: {hostname}")
        return None, None, None

    def stop(self):
        """A thread-safe method to signal the worker to stop its operations gracefully."""
        self._is_running = False
        self.requestInterruption()
        self.wait(500)

    def _get_no_connectivity_error_msg(self) -> str:
        return (
            f"**❌ No Network Connectivity to IB Gateway on port {self.params.port}.**\n\n"
            "This means the application cannot see the Gateway at all.\n\n"
            "**Solutions:**\n"
            "1.  **Is IB Gateway/TWS running** and are you logged in?\n"
            "2.  **Is the port correct?** Paper is **7497**, Live is **7496**.\n"
            "3.  **Check your firewall.** Make sure it is not blocking the port."
        )

    def _get_timeout_error_msg(self) -> str:
        return (
            f"**❌ Connection Timed Out After {self.params.timeout}s.**\n\n"
            "This is the most common issue and it is almost always a **configuration problem inside IB Gateway**, not the code.\n\n"
            "**🔥 YOUR MOST LIKELY SOLUTION:**\n"
            "You must explicitly tell Gateway to trust connections from your computer.\n\n"
            "1.  In IB Gateway, go to **File -> Global Configuration**.\n"
            "2.  On the left, click **API -> Settings**.\n"
            "3.  Find the **'Trusted IP Addresses'** section.\n"
            "4.  Click **'Create'** and add `127.0.0.1`.\n"
            "5.  Click **'Create'** again and add `::1` (for IPv6).\n"
            "6.  Click **Apply**, then **OK**.\n"
            "7.  **You MUST completely restart the IB Gateway application** for these settings to take effect."
        )


class IBKRAuth(QObject):
    """Acts as the controller for the IBKR connection, managing the worker thread."""
    connection_established = Signal(object)
    connection_lost = Signal()
    status_updated = Signal(str)

    def __init__(self):
        super().__init__()
        self.worker: Optional[IBKRConnectionWorker] = None
        self.ib_client: Optional[IB] = None

    def connect_to_tws(self, trading_mode: TradingMode, host: str, client_id: int):
        """Creates and starts a new connection worker."""
        if self.worker and self.worker.isRunning():
            self.status_updated.emit("A connection attempt is already in progress.")
            return

        self.disconnect()

        config = get_broker_config(BrokerMode.AMERICA)
        port = config.default_ports.get(trading_mode.value, 7497)
        params = IBKRConnectionParams(host=host, port=port, client_id=client_id, trading_mode=trading_mode)

        self.worker = IBKRConnectionWorker(params)
        self.worker.connection_success.connect(self._on_connection_success)
        self.worker.connection_failed.connect(self._on_connection_failed)
        self.worker.connection_progress.connect(self.status_updated)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def _on_connection_success(self, ib_client):
        """Handles a successful connection signal from the worker."""
        self.ib_client = ib_client
        self.status_updated.emit("✅ IBKR Connected Successfully!")
        self.connection_established.emit(ib_client)

    def _on_connection_failed(self, error_message: str):
        """Handles a failed connection signal from the worker."""
        self.status_updated.emit(error_message)
        self.disconnect()

    def _on_worker_finished(self):
        """Cleans up the worker reference after the thread has finished."""
        if self.worker:
            self.worker.deleteLater()
            self.worker = None

    def disconnect(self):
        """Stops the worker thread and cleans up resources."""
        if self.worker and self.worker.isRunning():
            self.worker.stop()
        if self.ib_client:
            self.ib_client = None
            self.connection_lost.emit()

def is_ibkr_available() -> bool:
    """A simple check to see if the ib_insync library is available."""
    return IBKR_AVAILABLE