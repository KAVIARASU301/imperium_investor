import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def install_qt_stubs(monkeypatch):
    qtcore = types.ModuleType("PySide6.QtCore")

    class QObject:
        def __init__(self, parent=None):
            self._parent = parent

        def parent(self):
            return self._parent

    class SignalStub:
        def __init__(self, *args, **kwargs):
            self.emitted = []
            self.handlers = []

        def connect(self, handler, *args, **kwargs):
            self.handlers.append(handler)

        def emit(self, *args, **kwargs):
            self.emitted.append((args, kwargs))
            for handler in list(self.handlers):
                handler(*args, **kwargs)

    class QTimer:
        def __init__(self, *args, **kwargs):
            self.timeout = SignalStub()
            self.active = False

        def start(self, *args, **kwargs):
            self.active = True

        def stop(self):
            self.active = False

    class QMutex:
        pass

    class QMutexLocker:
        def __init__(self, mutex):
            self.mutex = mutex

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def Slot(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

    qtcore.QObject = QObject
    qtcore.Signal = SignalStub
    qtcore.Slot = Slot
    qtcore.QTimer = QTimer
    qtcore.QMutex = QMutex
    qtcore.QMutexLocker = QMutexLocker

    pyside = types.ModuleType("PySide6")
    pyside.QtCore = qtcore
    monkeypatch.setitem(sys.modules, "PySide6", pyside)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", qtcore)


def load_stop_loss_manager(monkeypatch, tmp_path):
    install_qt_stubs(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    sys.modules.pop("ibkr.core.stop_loss_store", None)
    sys.modules.pop("ibkr.core.stop_loss_manager", None)
    return importlib.import_module("ibkr.core.stop_loss_manager")


class FakeParent:
    def __init__(self):
        self.instrument_map = {
            "AAPL": {
                "instrument_token": 265598,
                "conId": 265598,
                "exchange": "NASDAQ",
                "currency": "USD",
            }
        }
        self.subscriptions = []
        self.market_data_worker = SimpleNamespace(get_last_price=lambda key: 0.0)

    def _subscribe_to_tokens(self, items):
        self.subscriptions.extend(items)


class FakeTrader:
    def __init__(self):
        self.orders = []

    def place_order(self, **kwargs):
        self.orders.append(kwargs)
        return {"order_id": "42", "status": "PendingSubmit", "accepted": True}

    def get_ltp(self, symbol):
        return 0.0


class FakePositionManager:
    def __init__(self):
        self.tracked = []

    def start_tracking_order(self, order_id, order_params):
        self.tracked.append((order_id, order_params))


def test_ibkr_stop_loss_subscribes_and_exits_with_smart_us_order(monkeypatch, tmp_path):
    module = load_stop_loss_manager(monkeypatch, tmp_path)
    parent = FakeParent()
    trader = FakeTrader()
    position_manager = FakePositionManager()
    manager = module.StopLossManager(trader, position_manager, parent=parent)

    assert manager.set_stop_loss(
        symbol="aapl",
        sl_price=190.0,
        quantity=10,
        avg_price=200.0,
        product="stk",
        sl_type="MARKET",
    )

    assert parent.subscriptions == [
        {
            "symbol": "AAPL",
            "tradingsymbol": "AAPL",
            "instrument_token": 265598,
            "conId": 265598,
            "exchange": "SMART",
            "currency": "USD",
        }
    ]

    manager.on_ticks([{"symbol": "aapl", "last_price": 189.5}])

    assert trader.orders == [
        {
            "variety": "regular",
            "exchange": "SMART",
            "currency": "USD",
            "tradingsymbol": "AAPL",
            "transaction_type": "SELL",
            "quantity": 10,
            "product": "STK",
            "order_type": "MARKET",
            "validity": "DAY",
            "tag": "SL_AUTO",
            "_is_exit_order": True,
        }
    ]
    assert manager.get_sl_for("AAPL", "STK") is None
    assert position_manager.tracked[0][0] == "42"
    assert position_manager.tracked[0][1]["status"] == "PENDINGSUBMIT"


def test_ibkr_stop_loss_store_uses_ibkr_specific_database(monkeypatch, tmp_path):
    module = load_stop_loss_manager(monkeypatch, tmp_path)
    store = module.StopLossStore()

    assert Path(store._path).name == "ibkr_stop_losses.db"
    assert Path(store._path).name != "stop_losses.db"
