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
            self.single_shot = False

        def setSingleShot(self, value):
            self.single_shot = bool(value)

        def isActive(self):
            return self.active

        def start(self, *_args, **_kwargs):
            self.active = True

        def stop(self):
            self.active = False

        @staticmethod
        def singleShot(_delay, callback):
            callback()

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

import ibkr.core.trading_client as trading_client_module
from ibkr.core.trading_client import (
    IBKRTradingClient,
    _convert_execution_fill,
    _convert_trade_with_fill,
)

sounds = ModuleType("ibkr.utils.sounds")
sounds.play_entry_exit = lambda: None
sounds.play_alert = lambda: None
sounds.play_error = lambda: None
status_bar = ModuleType("ibkr.widgets.status_bar")
status_bar.show_order_completed = lambda *_args, **_kwargs: None
status_bar.show_order_failed = lambda *_args, **_kwargs: None
sys.modules["ibkr.utils.sounds"] = sounds
sys.modules["ibkr.widgets.status_bar"] = status_bar

from ibkr.core.position_manager import PositionManager


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


class _FakeIBEvent:
    def __init__(self):
        self.callbacks = []

    def __iadd__(self, callback):
        self.callbacks.append(callback)
        return self

    def emit(self, *args):
        for callback in list(self.callbacks):
            callback(*args)


class _FakeIB:
    def __init__(self, trades=None):
        self._trades = trades or []
        self.trades_calls = 0
        self.orderStatusEvent = _FakeIBEvent()
        self.execDetailsEvent = _FakeIBEvent()
        self.newOrderEvent = _FakeIBEvent()
        self.openOrderEvent = _FakeIBEvent()
        self.positionEvent = _FakeIBEvent()
        self.updatePortfolioEvent = _FakeIBEvent()

    def isConnected(self):
        return True

    def trades(self):
        self.trades_calls += 1
        return list(self._trades)


def _client_with_ib(fake_ib, cached_orders=None):
    client = IBKRTradingClient.__new__(IBKRTradingClient)
    client.ib = fake_ib
    client._orders = dict(cached_orders or {})
    return client


def test_get_orders_reads_local_trade_cache_without_network_open_order_request():
    filled_trade = _trade(status="Filled", filled=10, remaining=0)
    client = _client_with_ib(_FakeIB(trades=[filled_trade]))

    orders = client.get_orders()

    assert client.ib.trades_calls == 1
    assert len(orders) == 1
    assert orders[0]["order_id"] == "77"
    assert orders[0]["status"] == "COMPLETE"
    assert client._orders["77"]["status"] == "COMPLETE"


def test_get_orders_returns_cached_snapshot_when_local_trade_cache_fails():
    class _FailingIB:
        def trades(self):
            raise RuntimeError("local cache unavailable")

    cached = {"77": {"order_id": "77", "status": "OPEN"}}
    client = _client_with_ib(_FailingIB(), cached_orders=cached)

    assert client.get_orders() == list(cached.values())


def test_client_subscribes_to_ibkr_order_events_and_emits_updates():
    fake_ib = _FakeIB()
    client = IBKRTradingClient(fake_ib)
    emitted = []
    client.order_status_updated.connect(emitted.append)

    fake_ib.orderStatusEvent.emit(_trade(status="Submitted", filled=0, remaining=10))

    assert client._ib_events_subscribed is True
    assert len(fake_ib.orderStatusEvent.callbacks) == 1
    assert len(fake_ib.positionEvent.callbacks) == 1
    assert emitted[-1]["order_id"] == "77"
    assert emitted[-1]["status"] == "OPEN"


def test_exec_details_event_marks_order_complete_in_real_time():
    fake_ib = _FakeIB()
    client = IBKRTradingClient(fake_ib)
    emitted = []
    client.order_status_updated.connect(emitted.append)
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

    fake_ib.execDetailsEvent.emit(_trade(), fill)

    assert emitted[-1]["status"] == "COMPLETE"
    assert emitted[-1]["filled_quantity"] == 10
    assert client._orders["77"]["pending_quantity"] == 0


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


class _FakeTraderForPositions:
    def __init__(self):
        self.orders_calls = 0
        self.positions_calls = 0
        self._orders = []
        self._positions = []

    def get_orders(self):
        self.orders_calls += 1
        return list(self._orders)

    def get_positions(self):
        self.positions_calls += 1
        return list(self._positions)


def test_get_positions_uses_local_cache_without_req_positions_feedback_loop():
    class _FakeIBWithPositions(_FakeIB):
        def __init__(self):
            super().__init__()
            self.positions_calls = 0
            self.portfolio_calls = 0
            self.req_positions_calls = 0
            self._positions_rows = [
                SimpleNamespace(
                    contract=SimpleNamespace(
                        symbol="AAPL",
                        exchange="SMART",
                        primaryExchange="NASDAQ",
                        conId=123,
                        secType="STK",
                        currency="USD",
                    ),
                    position=10,
                    avgCost=192.35,
                )
            ]
            self._portfolio_rows = [
                SimpleNamespace(
                    contract=SimpleNamespace(
                        symbol="AAPL",
                        exchange="SMART",
                        primaryExchange="NASDAQ",
                        conId=123,
                        secType="STK",
                        currency="USD",
                    ),
                    position=10,
                    averageCost=192.35,
                    marketPrice=195.25,
                    unrealizedPNL=29.0,
                    realizedPNL=0.0,
                )
            ]

        def positions(self):
            self.positions_calls += 1
            return list(self._positions_rows)

        def portfolio(self):
            self.portfolio_calls += 1
            return list(self._portfolio_rows)

        def reqPositions(self):
            self.req_positions_calls += 1
            raise AssertionError(
                "get_positions must not issue reqPositions from event-driven refreshes"
            )

    fake_ib = _FakeIBWithPositions()
    client = _client_with_ib(fake_ib)
    client._positions = {}

    rows = client.get_positions()

    assert fake_ib.req_positions_calls == 0
    assert fake_ib.positions_calls == 1
    assert fake_ib.portfolio_calls == 1
    assert rows == [client._positions["AAPL"]]
    assert rows[0]["quantity"] == 10
    assert rows[0]["last_price"] == 195.25
    assert rows[0]["unrealized_pnl"] == 29.0


def test_position_manager_processes_terminal_order_returned_by_place_order_immediately():
    trader = _FakeTraderForPositions()
    trader._positions = [{
        "tradingsymbol": "AAPL",
        "quantity": 10,
        "average_price": 192.35,
        "instrument_token": 123,
    }]
    manager = PositionManager(trader)
    emitted = []
    manager.positions_updated.connect(emitted.append)

    manager.start_tracking_order("77", {
        "order_id": "77",
        "tradingsymbol": "AAPL",
        "transaction_type": "BUY",
        "quantity": 10,
        "status": "COMPLETE",
        "filled_quantity": 10,
        "average_price": 192.35,
    })

    assert "77" not in manager.tracking_orders
    assert trader.positions_calls >= 1
    assert emitted[-1][0].symbol == "AAPL"


def test_position_manager_fast_polls_ibkr_order_api_after_acceptance():
    trader = _FakeTraderForPositions()
    trader._orders = [{
        "order_id": "77",
        "tradingsymbol": "AAPL",
        "transaction_type": "BUY",
        "quantity": 10,
        "status": "COMPLETE",
        "filled_quantity": 10,
        "average_price": 192.35,
    }]
    trader._positions = [{
        "tradingsymbol": "AAPL",
        "quantity": 10,
        "average_price": 192.35,
        "instrument_token": 123,
    }]
    manager = PositionManager(trader)
    emitted = []
    manager.positions_updated.connect(emitted.append)

    manager.start_tracking_order("77", {
        "order_id": "77",
        "tradingsymbol": "AAPL",
        "transaction_type": "BUY",
        "quantity": 10,
        "status": "OPEN",
    })

    assert trader.orders_calls >= 1
    assert "77" not in manager.tracking_orders
    assert emitted[-1][0].symbol == "AAPL"


def test_resolve_stock_contract_uses_dialog_con_id_without_blocking_qualification():
    class _FakeIBForContract:
        def __init__(self):
            self.qualify_calls = 0

        def qualifyContracts(self, *_args):
            self.qualify_calls += 1
            raise AssertionError("conId-backed orders should not synchronously qualify contracts")

    class _FakeContract:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    fake_ib = _FakeIBForContract()
    old_stock = trading_client_module.Stock
    old_contract = trading_client_module.Contract
    trading_client_module.Stock = _FakeContract
    trading_client_module.Contract = _FakeContract
    client = _client_with_ib(fake_ib)
    client._contract_cache = {}

    try:
        contract = client._resolve_stock_contract(
            "ARM",
            "SMART",
            "USD",
            con_id=653400472,
            primary_exchange="NASDAQ",
        )
    finally:
        trading_client_module.Stock = old_stock
        trading_client_module.Contract = old_contract

    assert fake_ib.qualify_calls == 0
    assert getattr(contract, "conId", 0) == 653400472
    assert getattr(contract, "symbol", "") == "ARM"
    assert getattr(contract, "primaryExchange", "") == "NASDAQ"
    assert client._contract_cache["ARM"] is contract
    assert client._contract_cache["653400472"] is contract


def test_prepare_order_params_preserves_contract_identity_from_order_dialog():
    client = _client_with_ib(_FakeIB())

    params = client._prepare_order_params({
        "tradingsymbol": "arm",
        "transaction_type": "SELL",
        "quantity": 1,
        "order_type": "LMT",
        "price": 357.89,
        "conId": 653400472,
        "primaryExch": "nasdaq",
    })

    assert params["symbol"] == "ARM"
    assert params["action"] == "SELL"
    assert params["order_type"] == "LIMIT"
    assert params["con_id"] == 653400472
    assert params["primary_exchange"] == "NASDAQ"


def test_prepare_order_params_reuses_cached_position_conid_when_order_has_symbol_only():
    client = IBKRTradingClient.__new__(IBKRTradingClient)
    client.ib = None
    client._positions = {
        "ARM": {
            "symbol": "ARM",
            "conId": 653400472,
            "instrument_token": 653400472,
            "exchange": "NASDAQ",
            "currency": "USD",
            "quantity": 100,
        }
    }

    params = client._prepare_order_params({"tradingsymbol": "ARM", "transaction_type": "SELL", "quantity": 1})

    assert params["con_id"] == 653400472
    assert params["exchange"] == "NASDAQ"
    assert params["primary_exchange"] == "NASDAQ"
    assert params["action"] == "SELL"


def test_prepare_order_params_prefers_explicit_conid_over_cached_position():
    client = IBKRTradingClient.__new__(IBKRTradingClient)
    client.ib = None
    client._positions = {"ARM": {"symbol": "ARM", "conId": 653400472, "quantity": 100}}

    params = client._prepare_order_params({"symbol": "ARM", "conId": 12345, "quantity": 1})

    assert params["con_id"] == 12345
