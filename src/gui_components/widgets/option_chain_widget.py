from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from PySide6.QtCore import Qt, Signal


class AccountSummaryWidget(QWidget):
    """
    A redesigned, premium account summary widgets styled with the consistent
    grayscale theme. It is now a pure view component.
    """
    # Signal to request to show the P&L history dialog
    pnl_history_requested = Signal()

    def __init__(self):
        super().__init__()
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        """Initializes the UI components with the new layout."""
        self.setFixedSize(280, 200)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)

        # Top row: Unrealized and Realized P&L
        top_row_layout = QHBoxLayout()
        top_row_layout.addWidget(self._create_metric_widget("UNREALIZED P&L", "unrealized_pnl_value"))
        top_row_layout.addWidget(self._create_separator())
        top_row_layout.addWidget(self._create_metric_widget("REALIZED P&L", "realized_pnl_value"))
        main_layout.addLayout(top_row_layout)

        # Bottom row: Balance and Total Trades
        bottom_row_layout = QHBoxLayout()
        bottom_row_layout.addWidget(self._create_metric_widget("AVAILABLE BALANCE", "balance_value"))
        bottom_row_layout.addWidget(self._create_separator())
        bottom_row_layout.addWidget(self._create_metric_widget("TODAY'S TRADES", "trades_value"))
        main_layout.addLayout(bottom_row_layout)

        # Initialize default values
        self.update_values(0.0, 0.0, 0.0)
        self.update_trade_count(0)

    @staticmethod
    def _create_metric_widget(title_text, value_label_name):
        """Factory for creating a single metric display box."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        title_label = QLabel(title_text)
        title_label.setObjectName("titleLabel")
        title_label.setAlignment(Qt.AlignCenter)

        value_label = QLabel("₹0")
        value_label.setObjectName(value_label_name)
        value_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(title_label)
        layout.addWidget(value_label)
        return widget

    @staticmethod
    def _create_separator():
        """Creates a styled vertical separator."""
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setObjectName("separator")
        return sep

    def update_values(self, unrealized_pnl: float, realized_pnl: float, balance: float):
        """Public method to update all monetary values in the widgets."""
        unrealized_label = self.findChild(QLabel, "unrealized_pnl_value")
        if unrealized_label:
            color = "#00d1b2" if unrealized_pnl >= 0 else "#ff3860"
            unrealized_label.setText(f"₹{unrealized_pnl:,.2f}")
            unrealized_label.setStyleSheet(f"color: {color};")

        realized_label = self.findChild(QLabel, "realized_pnl_value")
        if realized_label:
            color = "#00d1b2" if realized_pnl >= 0 else "#ff3860"
            realized_label.setText(f"₹{realized_pnl:,.2f}")
            realized_label.setStyleSheet(f"color: {color};")

        balance_label = self.findChild(QLabel, "balance_value")
        if balance_label:
            balance_label.setText(f"₹{balance:,.2f}")

    def update_trade_count(self, count: int):
        """Public method to update the trade count."""
        trade_label = self.findChild(QLabel, "trades_value")
        if trade_label:
            trade_label.setText(str(count))

    def _apply_styles(self):
        """Applies the premium grayscale stylesheet."""
        self.setStyleSheet("""
            AccountSummaryWidget {
                background-color: #1c1c1c;
                border: 1px solid #333;
                border-radius: 10px;
            }
            #titleLabel {
                color: #a0a0a0;
                font-size: 10px;
                font-weight: bold;
                text-transform: uppercase;
            }
            QLabel[objectName$="value"] {
                color: #e0e0e0;
                font-size: 18px;
                font-weight: 600;
            }
            #separator {
                background-color: #333;
            }
        """)

    def mouseDoubleClickEvent(self, event):
        """Emits a signal when the widget is double-clicked to show P&L history."""
        self.pnl_history_requested.emit()
        super().mouseDoubleClickEvent(event)
