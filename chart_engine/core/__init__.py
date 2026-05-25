# chart_engine/core/__init__.py
from chart_engine.core.chart_widget import CandlestickChart, ChartState
from chart_engine.core.chart_bridge import ChartBridge
from chart_engine.core.data_loader import KiteDataFetcher, DataCache, ChartDataLoaderThread
from chart_engine.core.metrics import calculate_metrics, MetricsResult

__all__ = [
    "CandlestickChart",
    "ChartState",
    "ChartBridge",
    "KiteDataFetcher",
    "DataCache",
    "ChartDataLoaderThread",
    "calculate_metrics",
    "MetricsResult",
]

# Backward compatibility alias for older imports.
DataFetcher = KiteDataFetcher
