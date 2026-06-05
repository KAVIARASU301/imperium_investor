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

from ibkr.core.trading_client import _convert_trade_with_fill


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
