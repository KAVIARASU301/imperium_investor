import logging
from typing import Dict, Any

from PySide6.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QLabel, QGroupBox
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

logger = logging.getLogger(__name__)


class PerformanceWidget(QWidget):
    """
    A compact dashboard widget that displays key performance metrics for the
    current trading session, such as P&L, win rate, and trade counts.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.labels: Dict[str, QLabel] = {}
        self._setup_ui()
        self._apply_styles()
        self.update_metrics({})  # Initialize with default values

    def _setup_ui(self):
        """Initializes the UI layout and sub-widgets."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        group_box = QGroupBox("Today's Performance")
        group_box.setObjectName("mainGroupBox")
        main_layout.addWidget(group_box)

        grid_layout = QGridLayout(group_box)
        grid_layout.setSpacing(15)

        # Define the layout and keys for each metric
        metric_configs = [
            ("Today's P&L", 'total_pnl', 0, 0, 1, 2),
            ("Win Rate", 'win_rate', 0, 2),
            ("Total Trades", 'total_trades', 0, 3),
            ("Winning Trades", 'winning_trades', 1, 2),
            ("Losing Trades", 'losing_trades', 1, 3),
        ]

        for title, key, row, col, rowspan, colspan in ((t, k, r, c, 1, 1) for t, k, r, c in metric_configs if
                                                       len((t, k, r, c)) == 4):
            if len((title, key, row, col, rowspan, colspan)) == 6:
                self.labels[key] = self._create_metric_widget(title, grid_layout, row, col, rowspan, colspan)

        # Manually add the larger P&L widget
        pnl_title, pnl_key, pnl_row, pnl_col, pnl_rowspan, pnl_colspan = "Today's P&L", 'total_pnl', 0, 0, 1, 2
        self.labels[pnl_key] = self._create_metric_widget(pnl_title, grid_layout, pnl_row, pnl_col, pnl_rowspan,
                                                          pnl_colspan, is_large=True)

    def _create_metric_widget(self, title: str, layout: QGridLayout, row: int, col: int, rowspan: int = 1,
                              colspan: int = 1, is_large: bool = False) -> QLabel:
        """Factory method to create a single metric display widget."""
        container = QWidget(objectName="metricBox")
        metric_layout = QVBoxLayout(container)
        metric_layout.setContentsMargins(15, 10, 15, 10)

        value_label = QLabel("–")
        value_label.setObjectName("largeValueLabel" if is_large else "valueLabel")
        value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_label = QLabel(title.upper())
        title_label.setObjectName("titleLabel")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        metric_layout.addWidget(value_label)
        metric_layout.addWidget(title_label)

        layout.addWidget(container, row, col, rowspan, colspan)
        return value_label

    def update_metrics(self, metrics: Dict[str, Any]):
        """Updates all the metric labels with new data."""
        profit_color, loss_color = "#00b894", "#d63031"

        # Total P&L
        total_pnl = metrics.get('total_pnl', 0.0)
        pnl_label = self.labels.get('total_pnl')
        if pnl_label:
            pnl_label.setText(f"₹{total_pnl:,.2f}")
            pnl_label.setStyleSheet(f"color: {profit_color if total_pnl >= 0 else loss_color};")

        # Win Rate
        win_rate = metrics.get('win_rate', 0.0)
        win_rate_label = self.labels.get('win_rate')
        if win_rate_label:
            win_rate_label.setText(f"{win_rate:.1f}%")

        # Other Metrics
        self.labels.get('total_trades', QLabel()).setText(str(metrics.get('total_trades', 0)))

        winning_trades_label = self.labels.get('winning_trades')
        if winning_trades_label:
            winning_trades_label.setText(str(metrics.get('winning_trades', 0)))
            winning_trades_label.setStyleSheet(f"color: {profit_color};")

        losing_trades_label = self.labels.get('losing_trades')
        if losing_trades_label:
            losing_trades_label.setText(str(metrics.get('losing_trades', 0)))
            losing_trades_label.setStyleSheet(f"color: {loss_color};")

    def _apply_styles(self):
        """Applies a consistent, modern dark theme stylesheet."""
        self.setStyleSheet("""
            #mainGroupBox {
                background-color: transparent;
                border: 1px solid #2a2a4a;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 15px;
                color: #b2bec3;
                font-family: "Segoe UI";
                font-weight: bold;
                font-size: 13px;
            }
            #mainGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 10px;
                left: 10px;
                color: #e0e0e0;
            }
            #metricBox {
                background-color: #1c1c2e;
                border-radius: 6px;
            }
            #titleLabel {
                color: #8a8a9e;
                font-size: 11px;
                font-weight: bold;
                text-transform: uppercase;
            }
            #valueLabel {
                color: #e0e0e0;
                font-size: 22px;
                font-weight: 300;
            }
            #largeValueLabel {
                color: #e0e0e0;
                font-size: 36px;
                font-weight: 200;
            }
        """)
