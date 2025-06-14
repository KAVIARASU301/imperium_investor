import logging
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame, QGridLayout, QSizePolicy
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor

logger = logging.getLogger(__name__)


class AccountSummaryWidget(QWidget):
    """
    A premium account summary widget with a minimalist and flat design,
    now with adjusted spacing for a cleaner look.
    """
    pnl_history_requested = Signal()

    def __init__(self):
        super().__init__()
        self.labels = {}
        self._setup_ui()
        self._apply_styles()
        self.update_summary()  # Initialize with default zero values

    def _setup_ui(self):
        """Initializes the UI components with a professional grid layout."""
        self.setObjectName("accountSummary")
        self.setMinimumWidth(280)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setToolTip("Double-click to view P&L History")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        grid_layout = QGridLayout()
        grid_layout.setSpacing(12)

        self.labels['unrealized_pnl'] = self._create_metric_widget(grid_layout, "Unrealized P&L", 0, 0)
        self.labels['realized_pnl'] = self._create_metric_widget(grid_layout, "Realized P&L", 0, 1)
        self.labels['used_margin'] = self._create_metric_widget(grid_layout, "Used Margin", 1, 0)
        self.labels['available_margin'] = self._create_metric_widget(grid_layout, "Available Margin", 1, 1)
        self.labels['win_rate'] = self._create_metric_widget(grid_layout, "Win Rate", 2, 0)
        self.labels['trade_count'] = self._create_metric_widget(grid_layout, "Trades", 2, 1)

        main_layout.addLayout(grid_layout)

    def _create_metric_widget(self, layout, title_text, row, col):
        """Factory method for creating a single metric display box."""
        frame = QFrame()
        frame.setObjectName("metricFrame")
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        metric_layout = QVBoxLayout(frame)
        metric_layout.setContentsMargins(10, 6, 10, 6)
        metric_layout.setSpacing(4)
        metric_layout.setAlignment(Qt.AlignCenter)

        value_label = QLabel("₹0")
        value_label.setObjectName("metricValueLabel")
        value_label.setAlignment(Qt.AlignCenter)

        title_label = QLabel(title_text.upper())
        title_label.setObjectName("metricTitleLabel")
        title_label.setAlignment(Qt.AlignCenter)

        metric_layout.addWidget(value_label)
        metric_layout.addWidget(title_label)

        layout.addWidget(frame, row, col)
        return value_label

    def update_summary(self, unrealized_pnl=0.0, realized_pnl=0.0,
                       used_margin=0.0, available_margin=0.0,
                       win_rate=0.0, trade_count=0):
        """Public method to update all widget labels with new data."""
        profit_color = "#29C7C9"
        loss_color = "#F85149"
        neutral_color = "#A9B1C3"

        # P&L Breakdown
        unrealized_color = profit_color if unrealized_pnl >= 0 else loss_color
        self.labels['unrealized_pnl'].setText(f"₹{unrealized_pnl:,.0f}")
        self.labels['unrealized_pnl'].setStyleSheet(f"color: {unrealized_color};")

        realized_color = profit_color if realized_pnl >= 0 else loss_color
        self.labels['realized_pnl'].setText(f"₹{realized_pnl:,.0f}")
        self.labels['realized_pnl'].setStyleSheet(f"color: {realized_color};")

        # Margin Details
        self.labels['used_margin'].setText(f"₹{used_margin:,.0f}")
        self.labels['available_margin'].setText(f"₹{available_margin:,.0f}")

        # Performance Metrics
        win_rate_color = profit_color if win_rate >= 50 else loss_color if trade_count > 0 else neutral_color
        self.labels['win_rate'].setText(f"{win_rate:.1f}%")
        self.labels['win_rate'].setStyleSheet(f"color: {win_rate_color};")
        self.labels['trade_count'].setText(str(trade_count))

    def _apply_styles(self):
        """Applies a minimalist, flat, and premium dark theme."""
        self.setStyleSheet("""
            #accountSummary {
                background-color: #161A25;
                border: 1px solid #3A4458;
                border-radius: 12px;
            }
            #metricFrame {
                background-color: #212635;
                border-radius: 8px;
            }
            #metricFrame:hover {
                background-color: #2A3140;
            }
            #metricTitleLabel {
                color: #A9B1C3;
                font-size: 10px;
                font-weight: bold;
                text-transform: uppercase;
            }
            #metricValueLabel {
                color: #E0E0E0;
                font-size: 19px; /* FIX: Reduced font size for a smaller container */
                font-weight: 300;
            }
        """)

    def mouseDoubleClickEvent(self, event):
        """Emits a signal when the widget is double-clicked."""
        self.pnl_history_requested.emit()
        super().mouseDoubleClickEvent(event)