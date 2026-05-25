# ibkr/core/market_data_worker.py

import logging
from typing import List, Dict, Set, Any, Iterable
import time
from PySide6.QtCore import QObject, Signal, QThread
from ib_insync import IB, Contract, Ticker, util

logger = logging.getLogger(__name__)


class MarketDataWorker(QThread):
    """
    Worker to handle real-time market data from IBKR in a background thread.
    Runs the ib_insync event loop and emits Qt signals with market data.
    """
    # Signal emits a list of dictionaries, each representing a tick
    data_received = Signal(list)
    connection_error = Signal(str)
    connection_established = Signal()
    connection_closed = Signal()
    order_update = Signal(dict)

    def __init__(self, ib_client: IB):
        super().__init__()
        self.ib = ib_client
        self._subscribed_contracts: Dict[str, Contract] = {}
        self._is_running = True

    def run(self):
        """The entry point for the thread. Starts the ib_insync event loop."""
        if not self.ib or not self.ib.isConnected():
            logger.error("IB client is not connected. Market data worker cannot start.")
            self.connection_error.emit("IB client is not connected.")
            return

        logger.info("MarketDataWorker thread started.")
        self.connection_established.emit()
        # Register the event handler for incoming ticker data
        self.ib.pendingTickersEvent += self._on_pending_tickers

        # Keep the event loop running
        while self._is_running and self.ib.isConnected():
            # Avoid ib_insync event-loop helpers in this QThread (no asyncio loop here).
            time.sleep(0.01)

        logger.info("MarketDataWorker thread finished.")
        self.connection_closed.emit()

    def _on_pending_tickers(self, tickers: List[Ticker]):
        """
        Callback that receives real-time data from ib_insync and emits it.
        """
        ticks_data = []
        for ticker in tickers:
            if ticker.contract and ticker.last is not None:
                ticks_data.append({
                    'symbol': ticker.contract.symbol,
                    'last_price': ticker.last,
                    'volume': ticker.volume,
                    'close': ticker.close,
                    'open': ticker.open,
                    'high': ticker.high,
                    'low': ticker.low,
                })
        if ticks_data:
            self.data_received.emit(ticks_data)

    def subscribe_to_contracts(self, contracts: List[Contract]):
        """Subscribes to market data for a list of contracts."""
        if not self.ib.isConnected():
            logger.warning("Cannot subscribe, IB is not connected.")
            return

        for contract in contracts:
            if contract.symbol not in self._subscribed_contracts:
                try:
                    self.ib.reqMktData(contract, '', False, False)
                    self._subscribed_contracts[contract.symbol] = contract
                    logger.info(f"Subscribed to market data for {contract.symbol}")
                except Exception as e:
                    logger.error(f"Failed to subscribe to {contract.symbol}: {e}")

    def unsubscribe_from_contracts(self, contracts: List[Contract]):
        """Unsubscribes from market data for a list of contracts."""
        if not self.ib.isConnected():
            return

        for contract in contracts:
            if contract.symbol in self._subscribed_contracts:
                try:
                    self.ib.cancelMktData(contract)
                    del self._subscribed_contracts[contract.symbol]
                    logger.info(f"Unsubscribed from market data for {contract.symbol}")
                except Exception as e:
                    logger.error(f"Failed to unsubscribe from {contract.symbol}: {e}")


    def is_connected(self) -> bool:
        return bool(self.ib and self.ib.isConnected())

    def get_subscription_info(self) -> Dict[str, Any]:
        return {"subscribed_tokens": [], "subscribed_symbols": list(self._subscribed_contracts.keys())}

    def add_instruments(self, instruments: Iterable[Any]):
        """Compatibility shim for legacy callers expecting token-based subscriptions."""
        contracts = []
        unsupported = 0
        for item in instruments or []:
            if isinstance(item, Contract):
                contracts.append(item)
            else:
                unsupported += 1
        if contracts:
            self.subscribe_to_contracts(contracts)
        if unsupported:
            logger.debug(f"Ignored {unsupported} non-Contract instruments in add_instruments")

    def set_instruments(self, instruments: Iterable[Any]):
        # Best-effort compatibility: clear current contract subscriptions then add new ones.
        self.unsubscribe_from_contracts(list(self._subscribed_contracts.values()))
        self.add_instruments(instruments)

    def stop(self):
        """Stops the worker and cleans up subscriptions."""
        logger.info("Stopping MarketDataWorker...")
        self._is_running = False

        if self.ib and self.ib.isConnected():
            # Unregister the event handler
            self.ib.pendingTickersEvent -= self._on_pending_tickers

            # Unsubscribe from all contracts
            all_contracts = list(self._subscribed_contracts.values())
            self.unsubscribe_from_contracts(all_contracts)

        self.quit()
        self.wait(2000) # Wait up to 2 seconds for the thread to terminate