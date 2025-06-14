from PySide6.QtWidgets import QToolBar, QComboBox, QLineEdit, QLabel, QCompleter, QWidgetAction, QSizePolicy
from PySide6.QtCore import Signal, QStringListModel, Qt
from src.gui_components.widgets.theme_toggle_switch import ThemeToggleSwitch


class HeaderToolbar(QToolBar):
    """A toolbar for the header of the main window."""
    theme_switched = Signal(bool)
    symbol_selected = Signal(str, int)  # symbol, instrument_token

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
        self.exchange_combo.currentTextChanged.connect(self._update_completer)
        self.addWidget(self.exchange_combo)

        self.addSeparator()

        # Symbol Search
        self.addWidget(QLabel("Symbol: "))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search for a stock...")
        self.completer = QCompleter(self._completer_model, self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.search_input.setCompleter(self.completer)
        self.search_input.returnPressed.connect(self._on_symbol_entered)
        self.completer.activated.connect(self._on_symbol_entered)
        self.addWidget(self.search_input)

        self.addSeparator()

        # Active Symbol Display
        self.active_symbol_label = QLabel("No active symbol")
        self.addWidget(self.active_symbol_label)

        self.addSeparator()

        # Position Quantity
        self.position_qty_label = QLabel("Qty: -")
        self.addWidget(self.position_qty_label)

        # Spacer to push items to the right
        spacer = QLabel()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.addWidget(spacer)

        # Theme Toggle Switch
        theme_switch = ThemeToggleSwitch()
        theme_switch.toggled.connect(self.theme_switched)

        # Encapsulate the switch in a QWidgetAction to add it to the toolbar
        action = QWidgetAction(self)
        action.setDefaultWidget(theme_switch)
        self.addAction(action)

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