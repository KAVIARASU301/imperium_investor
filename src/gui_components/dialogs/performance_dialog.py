import logging
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QWidget,
    QPushButton,
    QGraphicsDropShadowEffect
)

logger = logging.getLogger(__name__)


class PerformanceDialog(QDialog):
    """
    A modern, styled dialog to display trading performance metrics, now with
    a rich, premium UI consistent with the rest of the application.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Performance Dashboard")
        self.setMinimumSize(720, 480)

        self._init_ui()
        self._setup_shadows()
        self._apply_styles()
        self.update_metrics({})

    def _init_ui(self):
        """Initialize the main UI components with the new premium layout."""
        container = QWidget(self)
        container.setObjectName("mainContainer")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(20, 10, 20, 20)
        container_layout.setSpacing(20)

        container_layout.addLayout(self._create_header())
        grid_layout = self._create_metrics_grid()
        container_layout.addLayout(grid_layout, 1)

    def _create_header(self) -> QHBoxLayout:
        """Creates a custom title bar with a title and close button."""
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("All-Time Performance")
        title.setObjectName("dialogTitle")

        self.close_btn = QPushButton("✕")
        self.close_btn.setObjectName("closeButton")
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.clicked.connect(self.close)

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(self.close_btn)
        return header_layout

    def _create_metrics_grid(self) -> QGridLayout:
        """Creates and populates the grid of performance metrics."""
        grid_layout = QGridLayout()
        grid_layout.setSpacing(15)
        self.labels = {}

        self.labels['total_pnl'] = self._create_metric_widget(
            "All-Time P&L", grid_layout, 0, 0, 1, 2, is_large=True
        )
        self.labels['win_rate'] = self._create_metric_widget(
            "Win Rate", grid_layout, 0, 2
        )
        self.labels['total_trades'] = self._create_metric_widget(
            "Total Trades", grid_layout, 0, 3
        )
        self.labels['winning_trades'] = self._create_metric_widget(
            "Winning Trades", grid_layout, 1, 0
        )
        self.labels['losing_trades'] = self._create_metric_widget(
            "Losing Trades", grid_layout, 1, 1
        )
        self.labels['avg_profit'] = self._create_metric_widget(
            "Average Win", grid_layout, 1, 2
        )
        self.labels['avg_loss'] = self._create_metric_widget(
            "Average Loss", grid_layout, 1, 3
        )
        return grid_layout

    def _create_metric_widget(self, title_text, layout, row, col, rowspan=1, colspan=1, is_large=False) -> QLabel:
        """Factory method to create a single metric display widget."""
        metric_box = QWidget()
        metric_box.setObjectName("metricBox")

        metric_layout = QVBoxLayout(metric_box)
        metric_layout.setContentsMargins(15, 10, 15, 10)
        metric_layout.setSpacing(5 if is_large else 2)

        value_label = QLabel("0")
        value_label.setObjectName("largeValueLabel" if is_large else "valueLabel")
        value_label.setAlignment(Qt.AlignCenter)

        title_label = QLabel(title_text.upper())
        title_label.setObjectName("titleLabel")
        title_label.setAlignment(Qt.AlignCenter)

        metric_layout.addWidget(value_label, 1, Qt.AlignCenter)
        metric_layout.addWidget(title_label, 0, Qt.AlignCenter)

        layout.addWidget(metric_box, row, col, rowspan, colspan)
        return value_label

    def _setup_shadows(self):
        """Applies drop shadow effects to metric boxes for a premium feel."""
        for child in self.findChildren(QWidget):
            if child.objectName() == "metricBox":
                shadow = QGraphicsDropShadowEffect(self)
                shadow.setBlurRadius(20)
                shadow.setColor(QColor(0, 0, 0, 60))
                shadow.setOffset(1, 1)
                child.setGraphicsEffect(shadow)

    def update_metrics(self, metrics: dict):
        """Updates the UI with new performance data."""
        profit_color, loss_color = "#29C7C9", "#F85149"

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
            win_rate_label.setStyleSheet(f"color: {profit_color if win_rate >= 50 else loss_color};")

        # Winning/Losing Trades
        winning_trades = metrics.get('winning_trades', 0)
        self.labels.get('winning_trades', QLabel()).setText(str(winning_trades))

        losing_trades = metrics.get('losing_trades', 0)
        self.labels.get('losing_trades', QLabel()).setText(str(losing_trades))

        # Other Metrics
        self.labels.get('total_trades', QLabel()).setText(str(metrics.get('total_trades', 0)))
        avg_profit = metrics.get('avg_profit', 0.0)
        self.labels.get('avg_profit', QLabel()).setText(f"₹{avg_profit:,.2f}")
        avg_loss = metrics.get('avg_loss', 0.0)
        self.labels.get('avg_loss', QLabel()).setText(f"₹{avg_loss:,.2f}")

        # This metric was in your original file but not created in the UI, it has been removed for consistency
        # max_profit = metrics.get('max_profit', 0.0)
        # self.labels.get('max_profit', QLabel()).setText(f"₹{max_profit:,.2f}")

    def _apply_styles(self):
        """Applies the QSS stylesheet for the premium dark theme."""
        self.setStyleSheet("""
            #mainContainer {
                background-color: #161A25;
                border: 1px solid #3A4458;
                border-radius: 12px;
                font-family: "Segoe UI", sans-serif;
            }
            #dialogTitle {
                color: #FFFFFF;
                font-size: 16px;
                font-weight: 600;
            }
            #closeButton {
                background-color: transparent; border: none; color: #8A9BA8;
                font-size: 16px; font-weight: bold;
            }
            #closeButton:hover, #navButton:hover { background-color: #3A4458; color: #f52a20; }

            #metricBox {
                background-color: #212635;
                border-radius: 8px;
            }
            #titleLabel {
                color: #A9B1C3;
                font-size: 11px;
                font-weight: bold;
                text-transform: uppercase;
            }
            #valueLabel {
                color: #E0E0E0;
                font-size: 32px;
                font-weight: 300;
            }
            #largeValueLabel {
                color: #E0E0E0;
                font-size: 56px;
                font-weight: 200; /* Lighter */
            }
        """)

    # --- Window Event Handlers (Preserved from your original file) ---
    def resizeEvent(self, event):
        """Overrides the resize event to reposition the close button."""
        super().resizeEvent(event)
        if hasattr(self, 'close_btn'):
            self.close_btn.move(self.width() - self.close_btn.width() - 15, 15)

    def mousePressEvent(self, event):
        """Captures the initial position when the mouse is pressed."""
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        """Moves the window if the mouse is being dragged."""
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        """Resets the drag position when the mouse is released."""
        self._drag_pos = None
        event.accept()