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

from chart_engine.core.chart_widget import CandlestickChart

__all__ = ["CandlestickChart"]
