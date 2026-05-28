from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class IBKRLiveFeed(QObject):
    tick_received = Signal(dict)

    def __init__(self, ib_client, parent=None):
        super().__init__(parent)
        self._ib = ib_client
        self._subscriptions: dict = {}

    def subscribe(self, symbol: str, contract) -> None:
        if symbol in self._subscriptions:
            return
        self._subscriptions[symbol] = contract
        self._ib.reqMktData(contract, "", False, False)

        def _on_pending_tickers(tickers):
            for t in tickers:
                if t.contract != contract:
                    continue

                # FIX: Check for live data first, then fall back to delayed data
                last_price = (
                        getattr(t, "last", 0.0) or
                        getattr(t, "delayedLast", 0.0) or
                        getattr(t, "close", 0.0) or
                        getattr(t, "delayedClose", 0.0)
                )

                if not last_price or last_price <= 0:
                    continue

                self.tick_received.emit({
                    "tradingsymbol": symbol,
                    "last_price": last_price,
                    "instrument_token": contract.conId,
                    "exchange_timestamp": t.time,
                    "ohlc": {
                        "open": t.open or getattr(t, "delayedOpen", 0) or 0,
                        "high": t.high or getattr(t, "delayedHigh", 0) or 0,
                        "low": t.low or getattr(t, "delayedLow", 0) or 0,
                        "close": t.close or getattr(t, "delayedClose", 0) or 0,
                    },
                    "volume_traded": t.volume or getattr(t, "delayedVolume", 0) or 0,
                })
        self._ib.pendingTickersEvent += _on_pending_tickers

    def unsubscribe(self, symbol: str) -> None:
        contract = self._subscriptions.pop(symbol, None)
        if contract:
            self._ib.cancelMktData(contract)

    def unsubscribe_all(self) -> None:
        for symbol in list(self._subscriptions):
            self.unsubscribe(symbol)
