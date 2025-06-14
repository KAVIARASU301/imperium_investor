import logging
from PySide6.QtWidgets import (
    QToolBar, QLineEdit, QCompleter, QWidget, QHBoxLayout, QLabel, QSizePolicy
)
# --- MODIFIED: Added Qt for the enum ---
from PySide6.QtCore import Signal, QStringListModel, Qt
from PySide6.QtGui import QIcon, QAction

from src.gui_components.widgets.theme_toggle_switch import ThemeToggleSwitch


class HeaderToolbar(QToolBar):
    """A custom toolbar for the header of the main window."""
    theme_switched = Signal(bool)
    symbol_selected = Signal(str, int)
    add_alert_requested = Signal()
    alert_logs_requested = Signal()

    def __init__(self, kite_client, parent=None):
        super().__init__(parent)
        self.setMovable(False)
        self.setObjectName("headerToolbar")
        self.kite_client = kite_client
        self._instrument_data = []
        self._instrument_map = {}
        self._init_ui()

    def _init_ui(self):
        """Initializes the UI components of the toolbar."""
        # Symbol Search
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search and add symbol (e.g., INFY)...")
        self.search_input.setObjectName("symbolSearch")
        self.search_input.returnPressed.connect(self._on_search_enter)
        self.addWidget(self.search_input)

        self.completer = QCompleter(self)
        # --- MODIFIED: Changed False to the correct Qt enum ---
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.search_input.setCompleter(self.completer)
        self.completer.activated.connect(self._on_search_enter)

        # Spacer
        spacer = QWidget()
        spacer.setFixedWidth(20)
        self.addWidget(spacer)

        # Alert Button
        self.alert_action = QAction(QIcon("icons/bell.svg"), "Add Alert", self)
        self.alert_action.triggered.connect(self.add_alert_requested)
        self.addAction(self.alert_action)

        # Alert Logs Button
        self.alert_logs_action = QAction(QIcon("icons/checklist.svg"), "Alert Logs", self)
        self.alert_logs_action.triggered.connect(self.alert_logs_requested)
        self.addAction(self.alert_logs_action)

        # Spacer widget to push items to the right
        spacer_widget = QWidget()
        spacer_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.addWidget(spacer_widget)

        # Theme Toggle Switch
        theme_label = QLabel("Dark Mode")
        theme_label.setObjectName("themeLabel")
        self.addWidget(theme_label)

        self.theme_toggle = ThemeToggleSwitch()
        self.theme_toggle.toggled.connect(self.theme_switched.emit)
        self.addWidget(self.theme_toggle)

    def _on_search_enter(self, text=""):
        """Handles when a symbol is entered or selected."""
        # The 'activated' signal of QCompleter passes the text, handle both cases
        symbol = text or self.search_input.text()
        symbol = symbol.upper().strip()

        if not symbol:
            return

        if symbol in self._instrument_map:
            token = self._instrument_map[symbol]
            self.symbol_selected.emit(symbol, token)
            logging.info(f"Symbol selected: {symbol} (Token: {token})")
        else:
            logging.warning(f"Invalid symbol entered: {symbol}")

        self.search_input.clear()

    def set_instrument_data(self, instruments):
        """Receives the list of instruments and sets up the completer."""
        self._instrument_data = instruments
        symbols = []
        for inst in instruments:
            if inst.get('tradingsymbol'):
                symbol_name = inst['tradingsymbol']
                symbols.append(symbol_name)
                self._instrument_map[symbol_name] = inst['instrument_token']

        model = QStringListModel(symbols)
        self.completer.setModel(model)
        logging.info("Symbol search completer has been populated.")

    def set_alert_active(self, active: bool):
        """Changes the alert bell icon to indicate triggered alerts."""
        icon_path = "icons/bell-active.svg" if active else "icons/bell.svg"
        self.alert_action.setIcon(QIcon(icon_path))