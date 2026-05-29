import importlib
import sys
import types
from types import SimpleNamespace


def install_broker_stubs(monkeypatch):
    qtcore = types.ModuleType("PySide6.QtCore")

    class QObject:
        def __init__(self, *args, **kwargs):
            pass

    class SignalStub:
        def __init__(self, *args, **kwargs):
            self.emitted = []
            self.handlers = []

        def connect(self, handler):
            self.handlers.append(handler)

        def emit(self, *args, **kwargs):
            self.emitted.append((args, kwargs))
            for handler in list(self.handlers):
                handler(*args, **kwargs)

    class QTimer:
        def __init__(self, *args, **kwargs):
            self.timeout = SignalStub()

        def start(self, *args, **kwargs):
            pass

        def stop(self):
            pass

    qtcore.QObject = QObject
    qtcore.Signal = SignalStub
    qtcore.QTimer = QTimer

    pyside = types.ModuleType("PySide6")
    pyside.QtCore = qtcore
    monkeypatch.setitem(sys.modules, "PySide6", pyside)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", qtcore)

    ib_module = types.ModuleType("ib_insync")

    class Event:
        def __iadd__(self, handler):
            return self

        def __isub__(self, handler):
            return self

    class Stock:
        def __init__(self, symbol, exchange, currency):
            self.symbol = symbol
            self.exchange = exchange
            self.currency = currency
            self.conId = 101

    class BaseOrder:
        orderType = "MKT"

        def __init__(self, action, quantity):
            self.action = action
            self.totalQuantity = quantity
            self.orderId = 77
            self.permId = 880077
            self.lmtPrice = 0
            self.auxPrice = 0

    class MarketOrder(BaseOrder):
        orderType = "MKT"

    class LimitOrder(BaseOrder):
        orderType = "LMT"

        def __init__(self, action, quantity, limit_price):
            super().__init__(action, quantity)
            self.lmtPrice = limit_price

    class StopOrder(BaseOrder):
        orderType = "STP"

        def __init__(self, action, quantity, stop_price):
            super().__init__(action, quantity)
            self.auxPrice = stop_price

    class StopLimitOrder(BaseOrder):
        orderType = "STP LMT"

        def __init__(self, action, quantity, limit_price, stop_price):
            super().__init__(action, quantity)
            self.lmtPrice = limit_price
            self.auxPrice = stop_price

    ib_module.IB = object
    ib_module.Contract = object
    ib_module.Stock = Stock
    ib_module.MarketOrder = MarketOrder
    ib_module.LimitOrder = LimitOrder
    ib_module.StopOrder = StopOrder
    ib_module.StopLimitOrder = StopLimitOrder
    ib_module.Trade = object
    ib_module.Position = object
    ib_module.Ticker = object
    ib_module.Event = Event
    monkeypatch.setitem(sys.modules, "ib_insync", ib_module)

    sys.modules.pop("ibkr.core.trading_client", None)
    return Event


def load_client(monkeypatch):
    Event = install_broker_stubs(monkeypatch)
    module = importlib.import_module("ibkr.core.trading_client")
    return module, Event


class FakeIB:
    def __init__(self, trade, Event):
        self.trade = trade
        self.orderStatusEvent = Event()
        self.positionEvent = Event()
        self.accountValueEvent = Event()
        self.pendingTickersEvent = Event()
        self.disconnectedEvent = Event()
        self.errorEvent = Event()

    def isConnected(self):
        return True

    def qualifyContracts(self, contract):
        return [contract]

    def placeOrder(self, contract, order):
        self.trade.contract = contract
        self.trade.order = order
        return self.trade

    def waitOnUpdate(self, timeout=0):
        return True


def make_trade(status, message=""):
    return SimpleNamespace(
        order=None,
        contract=None,
        orderStatus=SimpleNamespace(status=status, filled=0, avgFillPrice=0),
        log=[SimpleNamespace(message=message, errorCode=201)] if message else [],
    )


def test_ibkr_place_order_returns_pending_as_accepted(monkeypatch):
    module, Event = load_client(monkeypatch)
    trade = make_trade("PendingSubmit")
    client = module.IBKRTradingClient(FakeIB(trade, Event))

    result = client.place_order(tradingsymbol="AAPL", transaction_type="BUY", quantity=3)

    assert result["accepted"] is True
    assert result["status"] == "PENDING"
    assert result["order_id"] == "77"
    assert result["tradingsymbol"] == "AAPL"


def test_ibkr_place_order_returns_rejection_with_reason(monkeypatch):
    module, Event = load_client(monkeypatch)
    trade = make_trade("Inactive", "Order rejected - insufficient buying power")
    client = module.IBKRTradingClient(FakeIB(trade, Event))

    result = client.place_order(tradingsymbol="MSFT", transaction_type="BUY", quantity=2)

    assert result["accepted"] is False
    assert result["status"] == "REJECTED"
    assert "insufficient buying power" in result["error"].lower()
    assert result["order_id"] == "77"


def test_ibkr_ticker_price_uses_only_last_or_close(monkeypatch):
    module, _Event = load_client(monkeypatch)
    ticker = SimpleNamespace(
        contract=SimpleNamespace(symbol="AAPL", conId=123),
        last=0,
        delayedLast=0,
        close=0,
        delayedClose=0,
        bid=199.5,
        ask=200.5,
        open=198,
        high=201,
        low=197,
        volume=1000,
    )

    data = module._convert_ticker(ticker)

    assert data["last_price"] == 0.0

    ticker.close = 198.75
    data = module._convert_ticker(ticker)

    assert data["last_price"] == 198.75


def test_ibkr_history_connection_prefers_local_7496(monkeypatch):
    import importlib.util
    from dataclasses import dataclass
    from pathlib import Path

    broker_protocol = types.ModuleType("chart_engine.core.broker_protocol")

    @dataclass
    class BarData:
        time: object
        open: float
        high: float
        low: float
        close: float
        volume: float

    @dataclass
    class BrokerCapabilities:
        name: str
        exchange_tz: str
        currency: str
        supports_options: bool
        supports_greeks: bool
        supports_level2: bool

    class BrokerDataFetcher:
        pass

    broker_protocol.BarData = BarData
    broker_protocol.BrokerCapabilities = BrokerCapabilities
    broker_protocol.BrokerDataFetcher = BrokerDataFetcher
    monkeypatch.setitem(sys.modules, "chart_engine", types.ModuleType("chart_engine"))
    monkeypatch.setitem(sys.modules, "chart_engine.core", types.ModuleType("chart_engine.core"))
    monkeypatch.setitem(sys.modules, "chart_engine.core.broker_protocol", broker_protocol)

    spec = importlib.util.spec_from_file_location(
        "chart_engine.core.ibkr_data_fetcher",
        Path(__file__).resolve().parents[1] / "chart_engine/core/ibkr_data_fetcher.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    monkeypatch.delenv("IBKR_HOST", raising=False)
    monkeypatch.delenv("IBKR_PORT", raising=False)
    fetcher = module.IBKRDataFetcher.__new__(module.IBKRDataFetcher)
    fetcher._last_history_endpoint = None
    fetcher._ib = SimpleNamespace(client=SimpleNamespace())

    assert fetcher._connection_candidates()[0] == ("127.0.0.1", 7496)
