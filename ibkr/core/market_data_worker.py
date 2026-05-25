import asyncio
import logging
from typing import List, Dict, Any, Iterable, Optional

from PySide6.QtCore import Signal, QThread
from ib_insync import IB, Contract, Stock, Ticker

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
        # Ensure this worker thread has an asyncio event loop.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        if not self.ib or not self.ib.isConnected():
            logger.error("IB client not connected.")
            self.connection_error.emit("IB client is not connected.")
            return

        logger.info("MarketDataWorker started.")
        self.connection_established.emit()
        self.ib.pendingTickersEvent += self._on_pending_tickers

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
            asyncio.set_event_loop(None)
            loop.close()
            logger.info("MarketDataWorker stopped.")
            self.connection_closed.emit()

    def _contract_key(self, contract: Contract) -> str:
        con_id = getattr(contract, "conId", 0) or 0
        symbol = (getattr(contract, "symbol", "") or "").strip().upper()
        return str(con_id) if con_id else symbol

    def _contract_from_item(self, item: Any) -> Optional[Contract]:
        """Accept IB Contract objects, conId ints, symbol strings, or instrument dicts."""
        if isinstance(item, Contract):
            return item

        token = None
        symbol = None
        exchange = "SMART"
        currency = "USD"

        if isinstance(item, dict):
            token = item.get("instrument_token") or item.get("conId") or item.get("conid")
            symbol = item.get("tradingsymbol") or item.get("symbol") or item.get("name")
            exchange = item.get("exchange") or exchange
            currency = item.get("currency") or currency
        elif isinstance(item, int):
            token = item
        elif isinstance(item, str):
            text = item.strip().upper()
            if not text:
                return None
            if text.isdigit():
                token = int(text)
            else:
                symbol = text

        try:
            if token:
                contract = Contract()
                contract.conId = int(token)
                contract.secType = "STK"
                contract.exchange = exchange
                contract.currency = currency
                if symbol:
                    contract.symbol = str(symbol).strip().upper()
                return contract
        except (TypeError, ValueError):
            pass

        if symbol:
            return Stock(str(symbol).strip().upper(), exchange, currency)
        return None

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
            key = self._contract_key(contract)
            label = (getattr(contract, "symbol", "") or key or "UNKNOWN").strip().upper()
            if not key or key in self._subscribed_contracts:
                continue
            try:
                # conId-only contracts are common in the IBKR instrument map.
                # Qualify them once so TWS has the missing symbol/secType details.
                qualified = self.ib.qualifyContracts(contract)
                if qualified:
                    contract = qualified[0]
                    key = self._contract_key(contract)
                    label = (getattr(contract, "symbol", "") or key or label).strip().upper()
                if key in self._subscribed_contracts:
                    continue
                self.ib.reqMktData(contract, '', False, False)
                self._subscribed_contracts[key] = contract
                logger.info(f"Subscribed: {label}")
            except Exception as e:
                logger.error(f"Subscribe failed {label}: {e}")

    def unsubscribe_from_contracts(self, contracts: List[Contract]):
        if not self.ib.isConnected():
            return
        for contract in contracts:
            key = self._contract_key(contract)
            if key in self._subscribed_contracts:
                try:
                    self.ib.cancelMktData(self._subscribed_contracts[key])
                    del self._subscribed_contracts[key]
                except Exception as e:
                    logger.error(f"Unsubscribe failed {key}: {e}")

    def is_connected(self) -> bool:
        return bool(self.ib and self.ib.isConnected())

    def get_subscription_info(self) -> Dict[str, Any]:
        return {
            "subscribed_tokens": [
                int(k) for k in self._subscribed_contracts.keys() if str(k).isdigit()
            ],
            "subscribed_symbols": [
                getattr(c, "symbol", "") for c in self._subscribed_contracts.values() if getattr(c, "symbol", "")
            ],
        }

    def add_instruments(self, instruments: Iterable[Any]):
        contracts = []
        for item in (instruments or []):
            contract = self._contract_from_item(item)
            if contract is not None:
                contracts.append(contract)
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