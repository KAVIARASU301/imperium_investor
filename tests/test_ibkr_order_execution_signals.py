import sys
from types import ModuleType, SimpleNamespace


if "PySide6.QtCore" not in sys.modules:
    pyside = ModuleType("PySide6")
    qtcore = ModuleType("PySide6.QtCore")

    class _FakeQObject:
        def __init__(self, *_args, **_kwargs):
            pass

    class _FakeSignal:
        def __init__(self, *_args, **_kwargs):
            self._callbacks = []

        def connect(self, callback):
            self._callbacks.append(callback)

        def emit(self, *args):
            for callback in list(self._callbacks):
                callback(*args)

    class _FakeQTimer:
        def __init__(self, *_args, **_kwargs):
            self.timeout = _FakeSignal()
            self.active = False

        def start(self, *_args, **_kwargs):
            self.active = True

        def stop(self):
            self.active = False

    class _FakeQThreadPool:
        @staticmethod
        def globalInstance():
            return _FakeQThreadPool()

        def start(self, worker):
            worker.run()

    class _FakeQRunnable:
        pass

    def _fake_slot(*_args, **_kwargs):
        def decorator(func):
            return func
        return decorator

    qtcore.QObject = _FakeQObject
    qtcore.Signal = _FakeSignal
    qtcore.QTimer = _FakeQTimer
    qtcore.QThreadPool = _FakeQThreadPool
    qtcore.QRunnable = _FakeQRunnable
    qtcore.Slot = _fake_slot
    pyside.QtCore = qtcore
    sys.modules["PySide6"] = pyside
    sys.modules["PySide6.QtCore"] = qtcore

from ibkr.core.trading_client import (
    IBKRTradingClient,
    _convert_execution_fill,
    _convert_trade_with_fill,
)


def _trade(status="Submitted", filled=0, remaining=10):
    return SimpleNamespace(
        contract=SimpleNamespace(symbol="AAPL", exchange="SMART", conId=123),
        order=SimpleNamespace(
            orderId=77,
            permId=880077,
            action="BUY",
            orderType="MKT",
            totalQuantity=10,
            lmtPrice=0,
            auxPrice=0,
        ),
        orderStatus=SimpleNamespace(
            status=status,
            filled=filled,
            remaining=remaining,
            avgFillPrice=0,
        ),
        log=[],
    )


def test_execution_fill_marks_fast_market_order_complete_before_order_status_catches_up():
    fill = SimpleNamespace(
        execution=SimpleNamespace(
            orderId=77,
            permId=880077,
            shares=10,
            cumQty=10,
            price=192.35,
            avgPrice=192.35,
        )
    )

    data = _convert_trade_with_fill(_trade(), fill)

    assert data["order_id"] == "77"
    assert data["status"] == "COMPLETE"
    assert data["filled_quantity"] == 10
    assert data["pending_quantity"] == 0
    assert data["average_price"] == 192.35


def test_execution_fill_reports_partial_quantity_without_terminal_status():
    fill = SimpleNamespace(
        execution=SimpleNamespace(
            orderId=77,
            permId=880077,
            shares=4,
            cumQty=4,
            price=192.35,
            avgPrice=192.35,
        )
    )

    data = _convert_trade_with_fill(_trade(), fill)

    assert data["status"] == "OPEN"
    assert data["filled_quantity"] == 4
    assert data["pending_quantity"] == 6


class _FakeIB:
    def __init__(self, open_orders=None):
        self._open_orders = open_orders or []
        self.req_all_open_orders_calls = 0

    def reqAllOpenOrders(self):
        self.req_all_open_orders_calls += 1
        return list(self._open_orders)


def _client_with_ib(fake_ib, cached_orders=None):
    client = IBKRTradingClient.__new__(IBKRTradingClient)
    client.ib = fake_ib
    client._orders = dict(cached_orders or {})
    return client


def test_get_orders_fetches_fresh_open_orders_from_tws():
    open_trade = _trade(status="Submitted", filled=0, remaining=10)
    client = _client_with_ib(_FakeIB(open_orders=[open_trade]))

    orders = client.get_orders()

    assert client.ib.req_all_open_orders_calls == 1
    assert len(orders) == 1
    assert orders[0]["order_id"] == "77"
    assert orders[0]["status"] == "OPEN"
    assert client._orders["77"]["status"] == "OPEN"


def test_get_orders_returns_cached_snapshot_when_tws_fetch_fails():
    class _FailingIB:
        def reqAllOpenOrders(self):
            raise RuntimeError("TWS unavailable")

    cached = {"77": {"order_id": "77", "status": "OPEN"}}
    client = _client_with_ib(_FailingIB(), cached_orders=cached)

    assert client.get_orders() == list(cached.values())


def test_execution_only_fill_row_is_complete_without_trade_snapshot():
    fill = SimpleNamespace(
        contract=SimpleNamespace(symbol="MSFT", exchange="SMART", conId=456),
        execution=SimpleNamespace(
            orderId=88,
            permId=880088,
            side="SLD",
            shares=5,
            cumQty=5,
            price=410.5,
            avgPrice=410.5,
        ),
    )

    row = _convert_execution_fill(fill)

    assert row["order_id"] == "88"
    assert row["transaction_type"] == "SELL"
    assert row["status"] == "COMPLETE"
    assert row["filled_quantity"] == 5
