import importlib.util
import sys
import types
from dataclasses import dataclass
from types import SimpleNamespace
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


def test_ibkr_duration_is_exact_rolling_day_window_for_short_ranges(monkeypatch):
    module = _load_module(monkeypatch, "chart_engine.core.ibkr_data_fetcher", "chart_engine/core/ibkr_data_fetcher.py")
    end = datetime(2026, 5, 29, 10, 15, 30, tzinfo=ZoneInfo("America/New_York"))
    start = end - timedelta(days=100)

    assert module.IBKRDataFetcher._compute_duration(start, end, "5 mins") == "100 D"
    assert module.IBKRDataFetcher._compute_duration(start, end, "1 day") == "100 D"
    assert module.IBKRDataFetcher._compute_duration(start, end, "1 week") == "100 D"


def test_ibkr_duration_uses_years_for_long_higher_timeframe_ranges(monkeypatch):
    module = _load_module(monkeypatch, "chart_engine.core.ibkr_data_fetcher", "chart_engine/core/ibkr_data_fetcher.py")
    end = datetime(2026, 5, 29, 10, 15, 30, tzinfo=ZoneInfo("America/New_York"))
    start = end - timedelta(days=600)

    assert module.IBKRDataFetcher._compute_duration(start, end, "1 day") == "2 Y"
    assert module.IBKRDataFetcher._compute_duration(start, end, "1 week") == "2 Y"
    assert module.IBKRDataFetcher._compute_duration(start, end, "1 month") == "2 Y"
    assert module.IBKRDataFetcher._compute_duration(start, end, "1 hour") == "600 D"


def test_ibkr_year_duration_overfetch_is_trimmed_to_requested_window(monkeypatch):
    module = _load_module(monkeypatch, "chart_engine.core.ibkr_data_fetcher", "chart_engine/core/ibkr_data_fetcher.py")
    start = datetime(2024, 10, 6, 7, 21, 46, tzinfo=ZoneInfo("America/New_York"))
    end = datetime(2026, 5, 29, 7, 21, 46, tzinfo=ZoneInfo("America/New_York"))
    bars = [
        SimpleNamespace(date="20241004", open=1, high=1, low=1, close=1, volume=1),
        SimpleNamespace(date="20241006", open=2, high=2, low=2, close=2, volume=2),
        SimpleNamespace(date="20260529", open=3, high=3, low=3, close=3, volume=3),
        SimpleNamespace(date="20260601", open=4, high=4, low=4, close=4, volume=4),
    ]

    trimmed = module.IBKRDataFetcher._filter_bars_to_window(bars, start, end, "1 day")

    assert [bar.date for bar in trimmed] == ["20241006", "20260529"]


def test_legacy_ibkr_duration_and_today_end_datetime_are_now_based(monkeypatch):
    module = _load_module(monkeypatch, "ibkr.core.data_fetcher", "ibkr/core/data_fetcher.py")
    end = datetime.now(tz=ZoneInfo("America/New_York"))
    start = end - timedelta(days=100)

    assert module.DataFetcher._ibkr_duration(start, end, "5minute") == "100 D"
    assert module.DataFetcher._ibkr_duration(start, end, "day") == "100 D"
    assert module.DataFetcher._ibkr_duration(end - timedelta(days=600), end, "day") == "2 Y"
    assert module.DataFetcher._ibkr_end_datetime(end) == ""


def test_ibkr_intraday_history_requests_extended_hours(monkeypatch):
    module = _load_module(monkeypatch, "chart_engine.core.ibkr_data_fetcher", "chart_engine/core/ibkr_data_fetcher.py")

    class FakeIB:
        def __init__(self):
            self.requests = []

        async def reqHistoricalDataAsync(self, contract, **kwargs):
            self.requests.append(kwargs)
            return [SimpleNamespace(date="20260529 08:00:00", open=1, high=2, low=1, close=2, volume=10)]

    fetcher = module.IBKRDataFetcher.__new__(module.IBKRDataFetcher)
    fetcher._what_to_show = "TRADES"
    fetcher._use_rth = True
    ib = FakeIB()

    bars = module.asyncio.run(fetcher._request_historical_bars_async(
        ib=ib,
        contract=SimpleNamespace(symbol="AAPL", conId=123),
        end_dt_str="",
        duration_str="1 D",
        bar_size="5 mins",
        request_symbol="AAPL",
    ))

    assert bars
    assert ib.requests[0]["useRTH"] is False


def test_ibkr_daily_history_keeps_regular_hours(monkeypatch):
    module = _load_module(monkeypatch, "chart_engine.core.ibkr_data_fetcher", "chart_engine/core/ibkr_data_fetcher.py")

    class FakeIB:
        def __init__(self):
            self.requests = []

        async def reqHistoricalDataAsync(self, contract, **kwargs):
            self.requests.append(kwargs)
            return [SimpleNamespace(date="20260529", open=1, high=2, low=1, close=2, volume=10)]

    fetcher = module.IBKRDataFetcher.__new__(module.IBKRDataFetcher)
    fetcher._what_to_show = "TRADES"
    fetcher._use_rth = True
    ib = FakeIB()

    bars = module.asyncio.run(fetcher._request_historical_bars_async(
        ib=ib,
        contract=SimpleNamespace(symbol="AAPL", conId=123),
        end_dt_str="",
        duration_str="1 D",
        bar_size="1 day",
        request_symbol="AAPL",
    ))

    assert bars
    assert ib.requests[0]["useRTH"] is True
