import asyncio
import logging
import threading
from typing import List, Dict, Set, Any, Iterable

from PySide6.QtCore import QObject, Signal, QThread
from ib_insync import IB, Contract, Ticker, util

logger = logging.getLogger(__name__)


class MarketDataWorker(QThread):
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
        if not self.ib or not self.ib.isConnected():
            logger.error("IB client not connected.")
            self.connection_error.emit("IB client is not connected.")
            return

        logger.info("MarketDataWorker started.")
        self.connection_established.emit()
        self.ib.pendingTickersEvent += self._on_pending_tickers

        # Pump ib_insync's event loop — this is the critical fix.
        # util.run() drives the asyncio loop that ib_insync requires.
        try:
            while self._is_running and self.ib.isConnected():
                self.ib.waitOnUpdate(timeout=0.05)
        except Exception as e:
            logger.error(f"MarketDataWorker loop error: {e}")
        finally:
            try:
                self.ib.pendingTickersEvent -= self._on_pending_tickers
            except Exception:
                pass
            logger.info("MarketDataWorker stopped.")
            self.connection_closed.emit()

    def _on_pending_tickers(self, tickers: List[Ticker]):
        ticks_data = []
        for ticker in tickers:
            if not ticker.contract:
                continue
            last = ticker.last if (ticker.last and ticker.last > 0) else ticker.close
            if not last or last <= 0:
                continue
            ticks_data.append({
                'symbol': ticker.contract.symbol,
                'tradingsymbol': ticker.contract.symbol,
                'last_price': last,
                'instrument_token': ticker.contract.conId,
                'volume': ticker.volume or 0,
                'close': ticker.close or 0,
                'open': ticker.open or 0,
                'high': ticker.high or 0,
                'low': ticker.low or 0,
                'bid': ticker.bid or 0,
                'ask': ticker.ask or 0,
                'ohlc': {
                    'open': ticker.open or 0,
                    'high': ticker.high or 0,
                    'low': ticker.low or 0,
                    'close': ticker.close or 0,
                },
            })
        if ticks_data:
            self.data_received.emit(ticks_data)

    def subscribe_to_contracts(self, contracts: List[Contract]):
        if not self.ib.isConnected():
            return
        for contract in contracts:
            sym = contract.symbol
            if sym not in self._subscribed_contracts:
                try:
                    self.ib.reqMktData(contract, '', False, False)
                    self._subscribed_contracts[sym] = contract
                    logger.info(f"Subscribed: {sym}")
                except Exception as e:
                    logger.error(f"Subscribe failed {sym}: {e}")

    def unsubscribe_from_contracts(self, contracts: List[Contract]):
        if not self.ib.isConnected():
            return
        for contract in contracts:
            sym = contract.symbol
            if sym in self._subscribed_contracts:
                try:
                    self.ib.cancelMktData(contract)
                    del self._subscribed_contracts[sym]
                except Exception as e:
                    logger.error(f"Unsubscribe failed {sym}: {e}")

    def is_connected(self) -> bool:
        return bool(self.ib and self.ib.isConnected())

    def get_subscription_info(self) -> Dict[str, Any]:
        return {
            "subscribed_tokens": [],
            "subscribed_symbols": list(self._subscribed_contracts.keys()),
        }

    def add_instruments(self, instruments: Iterable[Any]):
        contracts = [i for i in (instruments or []) if isinstance(i, Contract)]
        if contracts:
            self.subscribe_to_contracts(contracts)

    def set_instruments(self, instruments: Iterable[Any]):
        self.unsubscribe_from_contracts(list(self._subscribed_contracts.values()))
        self.add_instruments(instruments)

    def stop(self):
        logger.info("Stopping MarketDataWorker...")
        self._is_running = False
        try:
            self.ib.pendingTickersEvent -= self._on_pending_tickers
        except Exception:
            pass
        self.quit()
        self.wait(3000)
