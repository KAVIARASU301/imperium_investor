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
