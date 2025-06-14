# src/gui_components/widgets/market_chart_widget.py
import logging
from collections import deque
from typing import Dict, List
import pandas as pd
from PySide6.QtWidgets import QWidget, QVBoxLayout
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from datetime import datetime

logger = logging.getLogger(__name__)


class MarketChartWidget(QWidget):
    """
    A professional, premium chart widget that plots a two-day comparison with a
    gradient fill, a modern CPR indicator, and a live price in the title area.
    This version now buckets live ticks into one-minute intervals to prevent
    infinite horizontal scrolling.
    """

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.current_day_x = deque()
        self.current_day_y = deque()
        self.current_day_line = None
        self.gradient_fill = None
        self.symbol = ""
        self.figure_title = None
        self.figure_price_text = None
        # --- FIX: Add timestamp tracking for tick bucketing ---
        self.last_tick_minute = None
        # --- END FIX ---

        self._setup_chart_style()
        self._setup_ui()
        self.show_message("Awaiting Data", "Select Symbols and Click Load")

    def _setup_ui(self):
        self.figure = Figure(figsize=(8, 5))
        self.figure.subplots_adjust(left=0.08, right=0.98, bottom=0.1, top=0.88)
        self.canvas = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)

    def _setup_chart_style(self):
        """Sets a premium, modern dark theme for the chart."""
        self.colors = {
            'bg': '#161A25',
            'prev_day': '#8A9BA8',
            'curr_day_line': '#29C7C9',
            'gradient_top': '#29C7C9',
            'text': '#E0E0E0',
            'text_volatile': '#F39C12',
            'grid': '#2A3140',
            'accent': '#3A4458',
            'cpr_pivot': '#F39C12',
            'cpr_range': '#0074D9',
        }
        plt.style.use('dark_background')
        plt.rcParams.update({'figure.facecolor': self.colors['bg'], 'axes.facecolor': self.colors['bg']})

    def show_message(self, title: str, message: str):
        self.ax.clear()
        self.ax.text(0.5, 0.5, f"[{title}]\n{message}", transform=self.ax.transAxes,
                     ha='center', va='center', fontsize=12, color=self.colors['text'], alpha=0.7)
        self.canvas.draw()

    def plot_two_day_data(self, symbol: str, prev_day_data: pd.DataFrame, current_day_data: pd.DataFrame,
                          cpr_levels: dict):
        if self.figure_title: self.figure_title.remove()
        if self.figure_price_text: self.figure_price_text.remove()

        self.ax.clear()
        self.symbol = symbol
        self.current_day_x.clear()
        self.current_day_y.clear()
        self.current_day_line = None
        self.gradient_fill = None
        # --- FIX: Reset the last tick minute when loading new data ---
        self.last_tick_minute = None
        # --- END FIX ---

        prev_day_data = prev_day_data.between_time('09:15', '15:30')
        current_day_data = current_day_data.between_time('09:15', '15:30')

        if not prev_day_data.empty:
            self.ax.plot(range(len(prev_day_data)), prev_day_data['close'],
                         color=self.colors['prev_day'], linestyle='--', linewidth=1.0)

        separator_pos = len(prev_day_data) + 5

        if not current_day_data.empty:
            self.current_day_x.extend(range(separator_pos, separator_pos + len(current_day_data)))
            self.current_day_y.extend(current_day_data['close'])
            # --- FIX: Store the minute of the last historical data point ---
            if not current_day_data.index.empty:
                self.last_tick_minute = current_day_data.index[-1].minute
            # --- END FIX ---
            self.current_day_line, = self.ax.plot(self.current_day_x, self.current_day_y,
                                                  color=self.colors['curr_day_line'], linewidth=1.8, zorder=10)
            self._create_gradient()

        cpr_end_x = separator_pos + 375
        if cpr_levels:
            self.ax.axhspan(cpr_levels['bc'], cpr_levels['tc'], xmin=(separator_pos - 3) / cpr_end_x,
                            facecolor=self.colors['cpr_range'], alpha=0.1, zorder=0)
            self.ax.hlines(y=cpr_levels['pivot'], xmin=separator_pos, xmax=cpr_end_x,
                           color=self.colors['cpr_pivot'], linestyle=':', linewidth=1.2, alpha=0.8)

        self.ax.axvline(x=separator_pos - 3, color=self.colors['accent'], linestyle=':', linewidth=1)
        self._style_chart_axes()
        self._create_figure_title()
        self.canvas.draw()

    def _create_gradient(self):
        if not list(self.current_day_y): return

        line_x, line_y = self.current_day_line.get_data()
        self.gradient_fill = self.ax.fill_between(line_x, line_y,
                                                  color=self.colors['gradient_top'],
                                                  alpha=0.08, zorder=5)

    def add_tick(self, tick: Dict):
        if self.current_day_line is None: return
        price = tick.get('last_price')
        timestamp = tick.get('exchange_timestamp')

        if price is None or timestamp is None: return

        # --- FIX: Logic to bucket ticks into one-minute intervals ---
        current_minute = timestamp.minute
        if self.last_tick_minute == current_minute and len(self.current_day_y) > 0:
            # If the tick is within the same minute, update the last point
            self.current_day_y[-1] = price
        else:
            # If it's a new minute, add a new point
            next_x = self.current_day_x[-1] + 1 if self.current_day_x else 0
            self.current_day_x.append(next_x)
            self.current_day_y.append(price)
            self.last_tick_minute = current_minute
        # --- END FIX ---

        self.current_day_line.set_data(list(self.current_day_x), list(self.current_day_y))

        if self.gradient_fill and self.gradient_fill in self.ax.collections:
            self.gradient_fill.remove()
        self._create_gradient()

        if self.figure_price_text:
            self.figure_price_text.set_text(f'{int(price)}')

        self.ax.relim()
        self.ax.autoscale_view(tight=False, scalex=False, scaley=True)
        self.canvas.draw_idle()

    def _create_figure_title(self):
        """Creates the title and price text outside the axes, at the figure level."""
        self.figure_title = self.figure.text(
            0.08, 0.94, self.symbol, transform=self.figure.transFigure,
            ha='left', va='center', fontsize=14, weight='bold', color=self.colors['text']
        )
        last_price = f'{self.current_day_y[-1]:.2f}' if self.current_day_y else ''
        self.figure_price_text = self.figure.text(
            0.98, 0.94, last_price, transform=self.figure.transFigure,
            ha='right', va='center', fontsize=14, weight='bold', color=self.colors['text_volatile']
        )

    def _style_chart_axes(self):
        """Applies styling for grid, ticks, and spines. No longer handles title."""
        self.ax.grid(True, linestyle=':', linewidth=0.5, color=self.colors['grid'], alpha=0.5)
        self.ax.tick_params(axis='y', labelsize=9, colors=self.colors['text'], pad=10)
        self.ax.set_xticks([])
        for spine_pos in ['top', 'right', 'bottom', 'left']:
            self.ax.spines[spine_pos].set_color(self.colors['accent'])