from PySide6.QtWidgets import QToolBar, QComboBox, QLineEdit, QLabel, QCompleter, QWidgetAction, QSizePolicy, \
    QPushButton
from PySide6.QtCore import Signal, QStringListModel, Qt


# Assuming theme_toggle_switch.py is in the same directory
# from .theme_toggle_switch import ThemeToggleSwitch


class HeaderToolbar(QToolBar):
    """A toolbar for the header of the main window."""
    theme_switched = Signal(bool)
    symbol_selected = Signal(str, int)
    add_alert_requested = Signal()
    alert_logs_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMovable(False)
        self._instrument_data = {}
        self._completer_model = QStringListModel()
        self._init_ui()

    def _init_ui(self):
        """Initializes the toolbar's UI elements."""
        # Exchange Selector
        self.addWidget(QLabel("Exchange: "))
        self.exchange_combo = QComboBox()
        self.exchange_combo.addItems(["All", "NSE", "BSE"])
        self.addWidget(self.exchange_combo)
        self.addSeparator()

        # Symbol Search
        self.addWidget(QLabel("Symbol: "))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search for a stock...")
        # self.completer and other search logic would be here
        self.addWidget(self.search_input)

        # --- MOVED BUTTONS ---
        # Add Alert Button
        self.add_alert_button = QPushButton("Add Alert")
        self.add_alert_button.clicked.connect(self.add_alert_requested)
        self.addWidget(self.add_alert_button)

        # Alert Logs Button
        self.alert_logs_button = QPushButton("🔔 Alerts")
        self.alert_logs_button.setToolTip("Show triggered alert logs")
        self.alert_logs_button.clicked.connect(self.alert_logs_requested)
        self.addWidget(self.alert_logs_button)
        # --- END MOVED BUTTONS ---

        self.addSeparator()

        # Active Symbol Display
        self.active_symbol_label = QLabel("No active symbol")
        self.addWidget(self.active_symbol_label)

        spacer = QLabel()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.addWidget(spacer)

        # Theme Toggle Switch (assuming ThemeToggleSwitch class exists)
        # action = QWidgetAction(self)
        # action.setDefaultWidget(ThemeToggleSwitch())
        # self.addAction(action)

    def set_alert_active(self, active: bool):
        """Changes the style of the alert button to highlight it."""
        if active:
            self.alert_logs_button.setStyleSheet("background-color: #ef5350; color: white; font-weight: bold;")
        else:
            self.alert_logs_button.setStyleSheet("")

    # Other methods (set_instrument_data, etc.) would be here

    def set_instrument_data(self, data):
        """Sets the instrument data for the symbol search completer."""
        self._instrument_data = data
        self._update_completer()

    def _update_completer(self):
        """Updates the symbol list in the completer based on the selected exchange."""
        exchange = self.exchange_combo.currentText()
        if exchange == "All":
            symbols = list(self._instrument_data.keys())
        else:
            symbols = [
                s for s, d in self._instrument_data.items() if d['exchange'] == exchange
            ]
        self._completer_model.setStringList(sorted(symbols))

    def _on_symbol_entered(self):
        """Handles when a symbol is entered or selected."""
        symbol = self.search_input.text().upper()
        if symbol in self._instrument_data:
            token = self._instrument_data[symbol]['instrument_token']
            self.active_symbol_label.setText(f"Active: {symbol}")
            self.symbol_selected.emit(symbol, token)
        else:
            self.active_symbol_label.setText("Symbol not found")

    def update_position_qty(self, qty):
        """Updates the position quantity label."""
        self.position_qty_label.setText(f"Qty: {qty}")