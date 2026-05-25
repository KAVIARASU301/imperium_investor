# login_setup/ibkr_auth.py
"""
IBKR authentication module.

Manages connecting to IB Gateway / TWS via ib_insync using a dedicated
QThread with its own asyncio event loop (required to avoid conflicts with
PySide6's own event loop).

Ports:
  Live trading:  7496
  Paper trading: 7497
"""

import asyncio
import logging
import socket
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from PySide6.QtCore import QObject, QThread, Signal

try:
    from ib_insync import IB
    IBKR_AVAILABLE = True
except ImportError:
    IBKR_AVAILABLE = False
    IB = None

from login_setup.broker_modes import BrokerMode, TradingMode, get_broker_config

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------------------
# Connection parameters
# ------------------------------------------------------------------------------

@dataclass
class IBKRConnectionParams:
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 1
    timeout: float = 30.0
    trading_mode: TradingMode = TradingMode.PAPER
    fallback_hosts: List[str] = field(default_factory=lambda: ["127.0.0.1", "::1"])

    def candidate_hosts(self) -> List[str]:
        """Return ordered, de-duplicated hosts to probe for TWS/Gateway."""
        ordered = [self.host, *self.fallback_hosts]
        seen = set()
        candidates: List[str] = []

        for host in ordered:
            normalized = (host or "").strip()
            if not normalized:
                continue

            key = normalized.lower()
            if key in seen:
                continue

            seen.add(key)
            candidates.append(normalized)

        return candidates


# ------------------------------------------------------------------------------
# Worker thread
# ------------------------------------------------------------------------------

class IBKRConnectionWorker(QThread):
    """
    Runs the ib_insync connection inside a dedicated thread with its own
    asyncio event loop.  Emits connection_success with the live IB object
    — the caller owns it and is responsible for disconnecting.
    """
    connection_success = Signal(object)   # IB instance, fully connected
    connection_failed = Signal(str)       # Human-readable error message
    connection_progress = Signal(str)     # Status updates for the UI

    def __init__(self, params: IBKRConnectionParams):
        super().__init__()
        self.params = params
        self.ib: Optional[IB] = None
        self._is_running = True

    # --- QThread entry point ---------------------------------------------------

    def run(self):
        if not IBKR_AVAILABLE:
            self.connection_failed.emit(
                "ib_insync library not found.\n\nRun: pip install ib_insync"
            )
            return

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._connect())
        except Exception as e:
            logger.critical("Unhandled error in IBKR worker thread.", exc_info=True)
            self.connection_failed.emit(f"Unexpected error: {e}")
        finally:
            loop.close()

    # --- Core async connection -------------------------------------------------

    async def _connect(self):
        """Find the best reachable address and attempt the IB connection."""
        self.connection_progress.emit("🔍 Scanning for IB Gateway / TWS endpoint...")

        host, port, family = self._find_reachable_address()
        if not host:
            self.connection_failed.emit(self._msg_no_connectivity())
            return

        family_name = "IPv6" if family == socket.AF_INET6 else "IPv4"
        self.connection_progress.emit(
            f"🔗 Connecting to {host}:{port} ({family_name})..."
        )

        self.ib = IB()
        connected = False
        try:
            await self.ib.connectAsync(
                host=host,
                port=port,
                clientId=self.params.client_id,
                timeout=self.params.timeout,
            )

            if not self._is_running:
                # Stopped externally while connecting
                self.ib.disconnect()
                return

            if self.ib.isConnected():
                self.connection_progress.emit("✅ Connected. Verifying API...")
                await self.ib.reqCurrentTimeAsync()
                connected = True
                self.connection_success.emit(self.ib)
                # IB object is now owned by the caller — do NOT disconnect here.
            else:
                self.connection_failed.emit(
                    "Connection handshake completed but isConnected() returned False.\n"
                    "Try restarting IB Gateway."
                )

        except asyncio.TimeoutError:
            self.connection_failed.emit(self._msg_timeout())
        except ConnectionRefusedError:
            self.connection_failed.emit(
                "❌ Connection refused.\n\nIs IB Gateway running and is the API enabled?"
            )
        except Exception as e:
            logger.error("Error during IBKR connection.", exc_info=True)
            self.connection_failed.emit(f"Connection error: {e}")
        finally:
            # Only clean up if we did NOT successfully hand off the IB object
            if not connected and self.ib and self.ib.isConnected():
                self.ib.disconnect()

    # --- Network probe ---------------------------------------------------------

    def _find_reachable_address(
        self,
    ) -> Tuple[Optional[str], Optional[int], Optional[socket.AddressFamily]]:
        """
        Tries each fallback host and returns the first one that accepts a TCP
        connection on the configured port.  Prefers IPv6 when both work.
        """
        reachable = []

        for hostname in self.params.candidate_hosts():
            try:
                addr_infos = socket.getaddrinfo(
                    hostname, self.params.port, socket.AF_UNSPEC, socket.SOCK_STREAM
                )
                for family, _, _, _, sockaddr in addr_infos:
                    try:
                        with socket.socket(family, socket.SOCK_STREAM) as s:
                            s.settimeout(2.0)
                            addr = (
                                (sockaddr[0], self.params.port, 0, 0)
                                if family == socket.AF_INET6
                                else (sockaddr[0], self.params.port)
                            )
                            if s.connect_ex(addr) == 0:
                                reachable.append((sockaddr[0], self.params.port, family))
                    except OSError:
                        continue
            except socket.gaierror:
                logger.debug(f"Could not resolve host: {hostname}")

        if not reachable:
            return None, None, None

        # Prefer IPv4 for max compatibility with typical TWS/Gateway local configs.
        for host, port, family in reachable:
            if family == socket.AF_INET:
                return host, port, family
        return reachable[0]

    # --- Thread control -------------------------------------------------------

    def stop(self):
        self._is_running = False
        if self.ib and self.ib.isConnected():
            self.ib.disconnect()
        self.requestInterruption()
        self.wait(1000)

    # --- Error messages -------------------------------------------------------

    def _msg_no_connectivity(self) -> str:
        return (
            f"No network path to IB Gateway on port {self.params.port}.\n\n"
            "Checklist:\n"
            "1. Is IB Gateway or TWS running?\n"
            "2. Are you logged into your IBKR account in Gateway?\n"
            f"3. Is the port correct?  Paper → 7497 | Live → 7496\n"
            "4. Is a firewall blocking the port?"
        )

    def _msg_timeout(self) -> str:
        return (
            f"Connection timed out after {self.params.timeout:.0f}s.\n\n"
            "This is almost always a Gateway configuration issue:\n\n"
            "1. In IB Gateway → File → Global Configuration\n"
            "2. Navigate to API → Settings\n"
            "3. Under Trusted IP Addresses, add 127.0.0.1 and ::1\n"
            "4. Click Apply → OK\n"
            "5. Completely restart IB Gateway\n\n"
            "Also check for any pop-up dialogs inside Gateway."
        )


# ------------------------------------------------------------------------------
# Controller (used by the login dialog)
# ------------------------------------------------------------------------------

class IBKRAuth(QObject):
    """
    High-level controller that manages the IBKRConnectionWorker lifecycle
    and exposes clean signals to the UI layer.
    """
    connection_established = Signal(object)  # IB instance
    connection_lost = Signal()
    status_updated = Signal(str)

    def __init__(self):
        super().__init__()
        self.worker: Optional[IBKRConnectionWorker] = None
        self.ib_client: Optional[IB] = None

    def connect_to_tws(self, trading_mode: TradingMode, host: str, client_id: int):
        """Start a fresh connection attempt.  Cancels any ongoing attempt first."""
        if self.worker and self.worker.isRunning():
            self.status_updated.emit("Connection attempt already in progress.")
            return

        self._cleanup_worker()

        config = get_broker_config(BrokerMode.AMERICA)
        port = config.default_ports.get(trading_mode.value, 7497)

        params = IBKRConnectionParams(
            host=host,
            port=port,
            client_id=client_id,
            trading_mode=trading_mode,
        )

        self.worker = IBKRConnectionWorker(params)
        self.worker.connection_success.connect(self._on_success)
        self.worker.connection_failed.connect(self._on_failed)
        self.worker.connection_progress.connect(self.status_updated)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def _on_success(self, ib_client):
        self.ib_client = ib_client
        self.status_updated.emit("✅ Connected to IBKR TWS / Gateway successfully.")
        self.connection_established.emit(ib_client)

    def _on_failed(self, error_message: str):
        self.status_updated.emit(error_message)
        self._cleanup_worker()

    def _on_worker_finished(self):
        if self.worker:
            self.worker.deleteLater()
            self.worker = None

    def _cleanup_worker(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()

    def disconnect(self):
        """Disconnect from IB Gateway and clean up."""
        self._cleanup_worker()
        if self.ib_client:
            try:
                if self.ib_client.isConnected():
                    self.ib_client.disconnect()
            except Exception:
                pass
            self.ib_client = None
            self.connection_lost.emit()


def is_ibkr_available() -> bool:
    return IBKR_AVAILABLE
