import importlib.util
import sys
import types
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def _install_broker_protocol(monkeypatch):
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
        supports_options: bool = False
        supports_greeks: bool = False
        supports_level2: bool = False

    class BrokerDataFetcher:
        pass

    broker_protocol.BarData = BarData
    broker_protocol.BrokerCapabilities = BrokerCapabilities
    broker_protocol.BrokerDataFetcher = BrokerDataFetcher
    monkeypatch.setitem(sys.modules, "chart_engine", types.ModuleType("chart_engine"))
    monkeypatch.setitem(sys.modules, "chart_engine.core", types.ModuleType("chart_engine.core"))
    monkeypatch.setitem(sys.modules, "chart_engine.core.broker_protocol", broker_protocol)


def _load_module(monkeypatch, module_name, relative_path):
    _install_broker_protocol(monkeypatch)
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_data_loader(monkeypatch):
    _install_broker_protocol(monkeypatch)

    pandas = types.ModuleType("pandas")
    pandas.DataFrame = object
    monkeypatch.setitem(sys.modules, "pandas", pandas)

    cachetools = types.ModuleType("cachetools")

    class TTLCache(dict):
        def __init__(self, maxsize, ttl):
            super().__init__()

    cachetools.TTLCache = TTLCache
    monkeypatch.setitem(sys.modules, "cachetools", cachetools)

    qtcore = types.ModuleType("PySide6.QtCore")

    class QThread:
        def __init__(self, *args, **kwargs):
            pass

        def requestInterruption(self):
            pass

    class Signal:
        def __init__(self, *args, **kwargs):
            pass

        def emit(self, *args, **kwargs):
            pass

    qtcore.QThread = QThread
    qtcore.Signal = Signal
    pyside = types.ModuleType("PySide6")
    pyside.QtCore = qtcore
    monkeypatch.setitem(sys.modules, "PySide6", pyside)
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", qtcore)

    spec = importlib.util.spec_from_file_location(
        "chart_engine.core.data_loader", ROOT / "chart_engine/core/data_loader.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_ibkr_effective_to_date_uses_current_timestamp_for_all_intervals(monkeypatch):
    module = _load_data_loader(monkeypatch)
    loader = module.ChartDataLoaderThread.__new__(module.ChartDataLoaderThread)
    loader.interval = "week"
    now_et = datetime(2026, 5, 29, 10, 15, 30, tzinfo=ZoneInfo("America/New_York"))

    assert loader._effective_to_date(now_et, "ibkr") == now_et

    kite_to_date = loader._effective_to_date(now_et, "kite")
    assert kite_to_date.date() == (now_et.date() - timedelta(days=1))


def test_ibkr_duration_is_exact_rolling_day_window_for_any_bar_size(monkeypatch):
    module = _load_module(monkeypatch, "chart_engine.core.ibkr_data_fetcher", "chart_engine/core/ibkr_data_fetcher.py")
    end = datetime(2026, 5, 29, 10, 15, 30, tzinfo=ZoneInfo("America/New_York"))
    start = end - timedelta(days=100)

    assert module.IBKRDataFetcher._compute_duration(start, end, "5 mins") == "100 D"
    assert module.IBKRDataFetcher._compute_duration(start, end, "1 day") == "100 D"
    assert module.IBKRDataFetcher._compute_duration(start, end, "1 week") == "100 D"


def test_legacy_ibkr_duration_and_today_end_datetime_are_now_based(monkeypatch):
    module = _load_module(monkeypatch, "ibkr.core.data_fetcher", "ibkr/core/data_fetcher.py")
    end = datetime.now(tz=ZoneInfo("America/New_York"))
    start = end - timedelta(days=100)

    assert module.DataFetcher._ibkr_duration(start, end, "5minute") == "100 D"
    assert module.DataFetcher._ibkr_duration(start, end, "day") == "100 D"
    assert module.DataFetcher._ibkr_end_datetime(end) == ""
