# chart_engine/__init__.py
#
# Institutional-grade candlestick chart engine.
# Reusable across Kite and IBKR — just swap the data loader.
#
# Public surface: import CandlestickChart and drop it into any layout.
#
# Usage:
#   from chart_engine import CandlestickChart
#   chart = CandlestickChart(data_fetcher=KiteDataFetcher(kite), instrument_loader=loader)
#   chart.load_symbol("RELIANCE", "NSE", 738561)

__all__ = ["CandlestickChart"]


def __getattr__(name: str):
    if name == "CandlestickChart":
        from chart_engine.core.chart_widget import CandlestickChart

        return CandlestickChart
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
