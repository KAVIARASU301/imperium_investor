import logging
from typing import Dict, Any
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QWidget,
    QPushButton, QGraphicsDropShadowEffect
)

logger = logging.getLogger(__name__)


class PerformanceDialog(QDialog):
    """
    A modern, frameless dialog that provides a dashboard of key trading
    performance metrics over the lifetime of the account.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos = None
        self.labels: Dict[str, QLabel] = {}

        self._setup_window()
        self._init_ui()
        self._apply_styles()
        self.update_metrics({})  # Initialize with empty data

    def _setup_window(self):
        """Initializes window properties."""
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Performance Dashboard")
        self.setMinimumSize(700, 450)

    def _init_ui(self):
        """Initializes the main UI layout and components."""
        container = QWidget(self)
        container.setObjectName("mainContainer")

        # Enable dragging
        container.mousePressEvent = self.mousePressEvent
        container.mouseMoveEvent = self.mouseMoveEvent
        container.mouseReleaseEvent = self.mouseReleaseEvent

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(20, 15, 20, 20)
        container_layout.setSpacing(20)

        container_layout.addLayout(self._create_header())
        grid_layout = self._create_metrics_grid()
        container_layout.addLayout(grid_layout, stretch=1)

    def _create_header(self) -> QHBoxLayout:
        """Creates the custom title bar with a title and close button."""
        header_layout = QHBoxLayout()
        title = QLabel("Lifetime Performance")
        title.setObjectName("dialogTitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.clicked.connect(self.close)

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignTop)
        return header_layout

    def _create_metrics_grid(self) -> QGridLayout:
        """Creates and populates the grid of performance metric widgets."""
        grid_layout = QGridLayout()
        grid_layout.setSpacing(15)

        # Create and place each metric widget in the grid
        self.labels['total_pnl'] = self._create_metric_widget(
            "Lifetime P&L", grid_layout, row=0, col=0, rowspan=1, colspan=2, is_large=True
        )
        self.labels['win_rate'] = self._create_metric_widget("Win Rate", grid_layout, row=0, col=2)
        self.labels['total_trades'] = self._create_metric_widget("Total Trades", grid_layout, row=0, col=3)
        self.labels['winning_trades'] = self._create_metric_widget("Winning Trades", grid_layout, row=1, col=0)
        self.labels['losing_trades'] = self._create_metric_widget("Losing Trades", grid_layout, row=1, col=1)
        self.labels['avg_profit'] = self._create_metric_widget("Average Win", grid_layout, row=1, col=2)
        self.labels['avg_loss'] = self._create_metric_widget("Average Loss", grid_layout, row=1, col=3)

        return grid_layout

    def _create_metric_widget(self, title_text: str, layout: QGridLayout, row: int, col: int, rowspan: int = 1,
                              colspan: int = 1, is_large: bool = False) -> QLabel:
        """Factory method to create a single metric display widget."""
        metric_box = QWidget(objectName="metricBox")

        metric_layout = QVBoxLayout(metric_box)
        metric_layout.setContentsMargins(20, 15, 20, 15)
        metric_layout.setSpacing(5)

        value_label = QLabel("–")  # Default to dash
        value_label.setObjectName("largeValueLabel" if is_large else "valueLabel")

        title_label = QLabel(title_text.upper())
        title_label.setObjectName("titleLabel")

        metric_layout.addWidget(value_label, alignment=Qt.AlignmentFlag.AlignCenter)
        metric_layout.addWidget(title_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # Add shadow for depth
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(25)
        shadow.setColor(QColor(0, 0, 0, 50))
        shadow.setOffset(2, 2)
        metric_box.setGraphicsEffect(shadow)

        layout.addWidget(metric_box, row, col, rowspan, colspan)
        return value_label

    def update_metrics(self, metrics: Dict[str, Any]):
        """Updates the UI labels with new performance data."""
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
        self.labels.get('winning_trades', QLabel()).setText(str(metrics.get('winning_trades', 0)))
        self.labels.get('losing_trades', QLabel()).setText(str(metrics.get('losing_trades', 0)))
        self.labels.get('total_trades', QLabel()).setText(str(metrics.get('total_trades', 0)))

        avg_profit = metrics.get('avg_profit', 0.0)
        self.labels.get('avg_profit', QLabel()).setText(f"₹{avg_profit:,.2f}")

        avg_loss = metrics.get('avg_loss', 0.0)
        self.labels.get('avg_loss', QLabel()).setText(f"₹{avg_loss:,.2f}")

    def _apply_styles(self):
        """Applies a consistent, modern dark theme stylesheet."""
        self.setStyleSheet("""
            #mainContainer {
                background-color: #1c1c2e;
                border: 1px solid #3a3a5a;
                border-radius: 12px;
                font-family: "Segoe UI", sans-serif;
            }
            #dialogTitle { color: #e0e0e0; font-size: 18px; font-weight: 600; }
            #closeButton {
                background: transparent; border: none; color: #8a8a9e;
                font-size: 16px; font-weight: bold;
            }
            #closeButton:hover { color: #d63031; }

            #metricBox { background-color: #2a2a4a; border-radius: 8px; }
            #titleLabel {
                color: #8a8a9e; font-size: 11px;
                font-weight: bold; text-transform: uppercase;
            }
            #valueLabel { color: #e0e0e0; font-size: 28px; font-weight: 300; }
            #largeValueLabel { color: #e0e0e0; font-size: 52px; font-weight: 200; }
        """)

    # --- Window Dragging Handlers ---
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()
