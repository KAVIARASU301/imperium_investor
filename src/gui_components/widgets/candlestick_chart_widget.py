import sys
import random
import pandas as pd
from datetime import datetime, timedelta

from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget
from PySide6.QtCore import QObject, Slot, Signal, QTimer

from lightweight_charts.widgets import QtChart
from lightweight_charts import Chart


class SimulatedData:
    """
    A simulator that can generate different timeframes
    and mimic fetching data for different symbols.
    """

    def __init__(self):
        self._data = {}

    def get_historical_data(self, symbol='TSLA', timeframe='5min', num_bars=300):
        print(f"Generating historical data for {symbol} ({timeframe})...")
        cache_key = f"{symbol}_{timeframe}"
        if cache_key not in self._data:
            df = pd.DataFrame(columns=['time', 'open', 'high', 'low', 'close'])
            now = datetime.now()

            minutes_delta = int(timeframe.replace('min', ''))
            current_time = now - timedelta(minutes=num_bars * minutes_delta)
            current_price = random.uniform(150, 350)

            records = []
            for _ in range(num_bars):
                open_price = current_price
                close_price = open_price + random.uniform(-1, 1) * (minutes_delta * 0.5)
                high_price = max(open_price, close_price) + random.uniform(0, 0.5)
                low_price = min(open_price, close_price) - random.uniform(0, 0.5)

                records.append({
                    'time': current_time,
                    'open': open_price,
                    'high': high_price,
                    'low': low_price,
                    'close': close_price
                })
                current_price = close_price
                current_time += timedelta(minutes=minutes_delta)

            self._data[cache_key] = pd.DataFrame(records)

        return self._data[cache_key]


class ChartWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Professional Real-Time Chart")
        self.setGeometry(100, 100, 1300, 900)

        widget = QWidget()
        self.setCentralWidget(widget)
        self.layout = QVBoxLayout(widget)

        # --- Python is the Source of Truth for Data ---
        self.chart_data = pd.DataFrame()
        self.data_source = SimulatedData()

        # --- Chart Setup ---
        self.chart = QtChart(toolbox=True)
        self.chart.legend(visible=True, ohlc=True, percent=True)
        self.sma_line = self.chart.create_line(name='SMA 50', color='blue', width=2)
        self.layout.addWidget(self.chart.get_webview())

        # --- Top Bar Setup ---
        self.chart.topbar.textbox('symbol', 'TSLA')
        self.chart.topbar.switcher('timeframe', ('1min', '5min', '30min'), default='5min',
                                   func=self.on_timeframe_change)
        self.chart.events.search += self.on_search

        # --- Initial Load and Real-time Timer ---
        self.current_symbol = 'TSLA'
        self.current_timeframe = '5min'
        self.load_chart_data()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_chart)
        self.timer.start(1000)

    def load_chart_data(self):
        """Fetches and sets the main chart data and indicators."""
        self.chart_data = self.data_source.get_historical_data(self.current_symbol, self.current_timeframe)
        if self.chart_data.empty:
            print(f"No data for {self.current_symbol}, cannot load chart.")
            return

        self.chart.set(self.chart_data)
        self.chart.watermark(f'{self.current_symbol} - {self.current_timeframe}')
        self.chart.layout(background_color='#131722', text_color='#d1d4dc')
        self.chart.grid(color='rgba(42, 46, 57, 1)')
        self.chart.candle_style(up_color='#26a69a', down_color='#ef5350')

        sma_data = self.calculate_sma(self.chart_data)
        self.sma_line.set(sma_data)
        self.chart.fit()

    def calculate_sma(self, df, period=50):
        if len(df) < period:
            return pd.DataFrame()
        return pd.DataFrame({
            'time': df['time'],
            'SMA 50': df['close'].rolling(window=period).mean()
        }).dropna()

    def update_chart(self):
        """Simulates a real-time bar update using the local DataFrame."""
        if self.chart_data.empty:
            return

        # **THE FIX**: Get the last bar from our local DataFrame, not the chart object.
        last_bar = self.chart_data.iloc[-1]

        minutes_delta = int(self.current_timeframe.replace('min', ''))

        new_bar = pd.Series({
            'time': last_bar['time'] + timedelta(minutes=minutes_delta),
            'open': last_bar['close'],
            'high': last_bar['close'] + random.uniform(0, 1),
            'low': last_bar['close'] - random.uniform(0, 1),
            'close': last_bar['close'] + random.uniform(-0.5, 0.5)
        })

        # Append new bar to our local DataFrame
        self.chart_data.loc[len(self.chart_data)] = new_bar

        # Update the chart with only the new bar
        self.chart.update(new_bar)

        # Recalculate SMA and update the line
        last_50_closes = self.chart_data['close'].tail(50)
        new_sma_val = last_50_closes.mean()
        self.sma_line.update(pd.Series({'time': new_bar['time'], 'SMA 50': new_sma_val}))

    def on_search(self, chart, searched_string):
        print(f"User searched for: {searched_string}")
        self.current_symbol = searched_string.upper()
        self.chart.topbar['symbol'].set(self.current_symbol)
        self.load_chart_data()

    def on_timeframe_change(self, chart):
        new_timeframe = chart.topbar['timeframe'].value
        print(f"User changed timeframe to: {new_timeframe}")
        self.current_timeframe = new_timeframe
        self.load_chart_data()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = ChartWindow()
    window.show()
    sys.exit(app.exec())