import logging
from typing import List, Dict, Any

from PySide6.QtWidgets import (
    QToolBar, QLineEdit, QCompleter, QWidget, QLabel, QSizePolicy, QPushButton
)
from PySide6.QtCore import Signal, QStringListModel, Qt
from PySide6.QtGui import QIcon

logger = logging.getLogger(__name__)


class HeaderToolbar(QToolBar):
    """
    A custom, modern toolbar for the main application window.
    It includes controls for symbol searching, adding to the watchlist,
    and managing alerts.
    """
    symbol_selected = Signal(str)
    add_to_watchlist_requested = Signal(str)
    add_alert_requested = Signal()
    alert_logs_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMovable(False)
        self.setObjectName("headerToolbar")
        self._instrument_map: Dict[str, Dict] = {}

        self._init_ui()
        self._apply_styles()

    def _init_ui(self):
        """Initializes the UI components of the toolbar."""
        # --- Symbol Search ---
        symbol_label = QLabel("Symbol:")
        symbol_label.setObjectName("toolbarLabel")
        self.addWidget(symbol_label)

        self.search_input = QLineEdit(
            placeholderText="e.g., INFY",
            objectName="symbolSearch"
        )
        self.search_input.returnPressed.connect(self._on_search_enter)
        self.addWidget(self.search_input)

        self.completer = QCompleter(self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.search_input.setCompleter(self.completer)
        self.completer.activated.connect(self._on_search_enter)

        # --- Add to Watchlist Button ---
        self.add_to_watchlist_btn = QPushButton("Add to Watchlist")
        self.add_to_watchlist_btn.setObjectName("secondaryButton")
        self.add_to_watchlist_btn.setToolTip("Add the entered symbol to your watchlist")
        self.add_to_watchlist_btn.clicked.connect(self._on_add_to_watchlist)
        self.addWidget(self.add_to_watchlist_btn)

        # Spacer to push subsequent items to the right
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer)

        # --- Alert Management Buttons ---
        self.alert_button = QPushButton(QIcon("icons/bell.svg"), "Set Alert")
        self.alert_button.setObjectName("toolbarButton")
        self.alert_button.clicked.connect(self.add_alert_requested)
        self.addWidget(self.alert_button)

        self.alert_logs_button = QPushButton(QIcon("icons/checklist.svg"), "Alert History")
        self.alert_logs_button.setObjectName("toolbarButton")
        self.alert_logs_button.clicked.connect(self.alert_logs_requested)
        self.addWidget(self.alert_logs_button)


    def set_instrument_data(self, instruments: List[Dict[str, Any]]):
        """Receives the master list of instruments to populate the search completer."""
        symbols = [inst['tradingsymbol'] for inst in instruments if 'tradingsymbol' in inst]
        self._instrument_map = {inst['tradingsymbol']: inst for inst in instruments if 'tradingsymbol' in inst}

        model = QStringListModel(symbols)
        self.completer.setModel(model)
        logger.info("Header toolbar search completer has been populated.")

    def set_alert_active(self, active: bool):
        """Changes the alert bell icon to indicate one or more triggered alerts."""
        icon_path = "icons/bell-active.svg" if active else "icons/bell.svg"
        self.alert_button.setIcon(QIcon(icon_path))

    def _on_search_enter(self, text=""):
        """Handles symbol selection from the search bar to display its chart."""
        symbol = (text or self.search_input.text()).upper().strip()
        if not symbol:
            return

        if symbol in self._instrument_map:
            self.symbol_selected.emit(symbol)
            logger.info(f"Symbol '{symbol}' selected for charting.")
            self.search_input.clear()
        else:
            logger.warning(f"Invalid symbol entered for charting: {symbol}")

    def _on_add_to_watchlist(self):
        """Handles the click of the 'Add' button."""
        symbol = self.search_input.text().upper().strip()
        if not symbol:
            return

        if symbol in self._instrument_map:
            self.add_to_watchlist_requested.emit(symbol)
            logger.info(f"Symbol '{symbol}' requested to be added to watchlist.")
            self.search_input.clear()
        else:
            logger.warning(f"Invalid symbol entered to add to watchlist: {symbol}")

    def _apply_styles(self):
        """Applies a consistent, modern dark theme stylesheet."""
        self.setStyleSheet("""
            QToolBar#headerToolbar {
                background-color: #1c1c2e;
                border-bottom: 1px solid #3a3a5a;
                padding: 5px 8px;
                spacing: 8px;
            }
            #toolbarLabel {
                color: #b2bec3;
                font-size: 13px;
                font-weight: bold;
            }
            #symbolSearch {
                background-color: #2a2a4a;
                border: 1px solid #3a3a5a;
                color: #e0e0e0;
                padding: 8px;
                border-radius: 6px;
                font-size: 13px;
                min-width: 150px;
                max-width: 200px;
            }
            #symbolSearch:focus {
                border: 1px solid #00b894;
            }
            #secondaryButton {
                background-color: #3a3a5a;
                color: #e0e0e0;
                font-weight: bold;
                border-radius: 6px;
                padding: 8px 16px;
                border: none;
                font-size: 13px;
            }
            #secondaryButton:hover {
                background-color: #4a4a6a;
            }
            #toolbarButton {
                background-color: transparent;
                color: #b2bec3;
                font-size: 13px;
                padding: 8px 12px;
                margin: 0px 2px;
                border-radius: 6px;
                border: none;
                text-align: left;
            }
            #toolbarButton:hover {
                background-color: #2a2a4a;
                color: #ffffff;
            }
        """)