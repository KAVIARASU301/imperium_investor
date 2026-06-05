import sys
from types import ModuleType, SimpleNamespace


if "PySide6.QtCore" not in sys.modules:
    pyside = ModuleType("PySide6")
    qtcore = ModuleType("PySide6.QtCore")

    class _FakeQObject:
        pass

    class _FakeSignal:
        def __init__(self, *_args, **_kwargs):
            pass

    class _FakeQTimer:
        pass

    qtcore.QObject = _FakeQObject
    qtcore.Signal = _FakeSignal
    qtcore.QTimer = _FakeQTimer
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
    def __init__(self, trades=None, fills=None):
        self._trades = trades or []
        self._fills = fills or []

    def trades(self):
        return list(self._trades)

    def reqExecutions(self):
        return list(self._fills)


def _client_with_ib(fake_ib, cached_orders=None):
    client = IBKRTradingClient.__new__(IBKRTradingClient)
    client.ib = fake_ib
    client._orders = dict(cached_orders or {})
    client._fill_handler_trade_ids = set()
    return client


def test_get_orders_merges_tws_trade_execution_reports_over_stale_pending_status():
    stale_trade = _trade(status="PendingSubmit", filled=0, remaining=10)
    fill = SimpleNamespace(
        contract=SimpleNamespace(symbol="AAPL", exchange="SMART", conId=123),
        execution=SimpleNamespace(
            orderId=77,
            permId=880077,
            side="BOT",
            shares=10,
            cumQty=10,
            price=192.35,
            avgPrice=192.35,
            time="2026-06-05 14:30:00",
        ),
    )
    client = _client_with_ib(_FakeIB(trades=[stale_trade], fills=[fill]))

    orders = client.get_orders()

    assert len(orders) == 1
    assert orders[0]["order_id"] == "77"
    assert orders[0]["status"] == "COMPLETE"
    assert orders[0]["filled_quantity"] == 10
    assert orders[0]["pending_quantity"] == 0
    assert client._orders["77"]["status"] == "COMPLETE"


def test_get_orders_keeps_partial_execution_open_when_original_quantity_is_larger():
    stale_trade = _trade(status="Submitted", filled=0, remaining=10)
    fill = SimpleNamespace(
        contract=SimpleNamespace(symbol="AAPL", exchange="SMART", conId=123),
        execution=SimpleNamespace(
            orderId=77,
            permId=880077,
            side="BOT",
            shares=4,
            cumQty=4,
            price=192.35,
            avgPrice=192.35,
        ),
    )
    client = _client_with_ib(_FakeIB(trades=[stale_trade], fills=[fill]))

    orders = client.get_orders()

    assert orders[0]["quantity"] == 10
    assert orders[0]["status"] == "OPEN"
    assert orders[0]["filled_quantity"] == 4
    assert orders[0]["pending_quantity"] == 6


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
