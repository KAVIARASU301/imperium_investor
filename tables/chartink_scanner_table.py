import logging
import json
import os
import pandas as pd
import requests
from bs4 import BeautifulSoup as bs
from typing import List, Dict, Optional

from PySide6.QtCore import Signal, Slot, Qt, QThread, QSize, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QPushButton, QHBoxLayout, QLabel, QComboBox, QMessageBox,
    QDialog, QLineEdit, QDialogButtonBox, QFormLayout, QGroupBox, QScrollArea
)
from PySide6.QtGui import QColor

logger = logging.getLogger(__name__)
SCAN_URL_FILE = os.path.join(os.path.expanduser("~/.swing_trader"), "chartink_scans.json")
SETTINGS_FILE = os.path.join(os.path.expanduser("~/.swing_trader"), "scanner_settings.json")


class AddScanDialog(QDialog):
    """Dialog for adding new Chartink scans."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add New Chartink Scan")
        self.setModal(True)
        self.resize(500, 200)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Form layout
        form_layout = QFormLayout()

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g., 'Breakout Stocks', 'High Volume'")
        form_layout.addRow("Scan Name:", self.name_input)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste scan clause (not the URL)")
        form_layout.addRow("Scan Clause:", self.url_input)

        layout.addLayout(form_layout)

        # Help text
        help_label = QLabel(
            "You can paste either:\n"
            "• Full Chartink URL (e.g., https://chartink.com/screener/your-scan)\n"
            "• Just the scan clause (the part after 'screener/')"
        )
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: #888; font-size: 11px; padding: 10px;")
        layout.addWidget(help_label)

        # Buttons
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # Connect validation
        self.name_input.textChanged.connect(self._validate_inputs)
        self.url_input.textChanged.connect(self._validate_inputs)
        self._validate_inputs()

    def _validate_inputs(self):
        """Enable/disable OK button based on input validation."""
        name_valid = bool(self.name_input.text().strip())
        url_valid = bool(self.url_input.text().strip())

        ok_button = self.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setEnabled(name_valid and url_valid)

    def get_scan_data(self) -> Dict[str, str]:
        """Returns the scan data entered by user."""
        return {
            "name": self.name_input.text().strip(),
            "url": self.url_input.text().strip()
        }


class ManageScansDialog(QDialog):
    """Dialog for managing existing scans."""

    def __init__(self, scans: List[Dict[str, str]], parent=None):
        super().__init__(parent)
        self.scans = scans.copy()  # Work with a copy
        self.setWindowTitle("Manage Chartink Scans")
        self.setModal(True)
        self.resize(600, 400)
        self._setup_ui()
        self._populate_scans()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Header
        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel("Your Chartink Scans:"))
        header_layout.addStretch()

        self.add_btn = QPushButton("Add New")
        self.add_btn.clicked.connect(self._add_scan)
        header_layout.addWidget(self.add_btn)

        layout.addLayout(header_layout)

        # Scans list
        self.scans_table = QTableWidget()
        self.scans_table.setColumnCount(3)
        self.scans_table.setHorizontalHeaderLabels(["Name", "URL/Clause", "Actions"])
        self.scans_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.scans_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.scans_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.scans_table.verticalHeader().setVisible(False)
        self.scans_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.scans_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

        layout.addWidget(self.scans_table)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.save_btn = QPushButton("Save Changes")
        self.save_btn.clicked.connect(self.accept)
        button_layout.addWidget(self.save_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        layout.addLayout(button_layout)

    def _populate_scans(self):
        """Populate the table with current scans."""
        self.scans_table.setRowCount(len(self.scans))

        for row, scan in enumerate(self.scans):
            # Name
            self.scans_table.setItem(row, 0, QTableWidgetItem(scan.get("name", "Unnamed")))

            # URL (truncated for display)
            url = scan.get("url", "")
            display_url = url[:50] + "..." if len(url) > 50 else url
            self.scans_table.setItem(row, 1, QTableWidgetItem(display_url))

            # Actions
            delete_btn = QPushButton("Delete")
            delete_btn.clicked.connect(lambda checked, r=row: self._delete_scan(r))
            self.scans_table.setCellWidget(row, 2, delete_btn)

    def _add_scan(self):
        """Add a new scan."""
        dialog = AddScanDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            scan_data = dialog.get_scan_data()
            self.scans.append(scan_data)
            self._populate_scans()

    def _delete_scan(self, row: int):
        """Delete a scan at the given row."""
        if 0 <= row < len(self.scans):
            scan_name = self.scans[row].get("name", "Unnamed")
            reply = QMessageBox.question(
                self, "Delete Scan",
                f"Are you sure you want to delete '{scan_name}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes:
                del self.scans[row]
                self._populate_scans()

    def get_scans(self) -> List[Dict[str, str]]:
        """Return the modified scans list."""
        return self.scans


class ScanWorker(QThread):
    """
    A dedicated worker thread to run a Chartink scan in the background,
    preventing the main UI from freezing.
    """
    scan_completed = Signal(list)
    scan_error = Signal(str)

    def __init__(self, scan_url: str):
        super().__init__()
        self.scan_url = scan_url
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'X-Requested-With': 'XMLHttpRequest'
        })

    def run(self):
        try:
            clause = self.scan_url.strip()
            if not clause:
                raise Exception("No scan clause provided")

            process_url = "https://chartink.com/screener/process"

            with requests.session() as s:
                # GET to establish session and get CSRF
                r = s.get(process_url, timeout=20)
                r.raise_for_status()

                soup = bs(r.content, "lxml")
                csrf_meta = soup.find("meta", {"name": "csrf-token"})
                if not csrf_meta or not csrf_meta.get("content"):
                    raise Exception("Could not retrieve CSRF token")
                csrf_token = csrf_meta["content"]

                headers = {
                    "x-csrf-token": csrf_token,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }

                payload = {"scan_clause": clause}
                res = s.post(process_url, headers=headers, data=payload, timeout=20)
                res.raise_for_status()

                data = res.json()
                results = data.get("data", [])
                symbols = [row["nsecode"] for row in results if "nsecode" in row]

                logger.info(f"Scan returned {len(symbols)} symbols")
                self.scan_completed.emit(symbols)

        except Exception as e:
            logger.error(f"ScanWorker failed: {e}", exc_info=True)
            self.scan_error.emit(str(e))


class ChartinkScannerTable(QWidget):
    """
    A widget that displays stock symbols retrieved from Chartink scans.
    Enhanced with LTP and %Ch columns using Kite API data.
    """
    symbol_selected = Signal(str)
    subscribe_tokens_requested = Signal(list)  # New signal for requesting subscriptions

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scans = self._load_scans()
        self.scan_thread: ScanWorker = None
        self._instrument_map: Dict[str, Dict] = {}  # Instrument data from Kite API
        self._symbol_data: Dict[str, Dict] = {}  # Symbol data with LTP, change, etc.
        self._symbol_to_row: Dict[str, int] = {}  # Symbol to row mapping for updates
        self._kite_client = None  # Store the Kite client

        self._setup_ui()
        self._apply_styles()

        # Restore last selected scan and run it if scans exist
        if self.scans:
            last_selected = self._load_last_selected_scan()
            if 0 <= last_selected < len(self.scans):
                self.scan_dropdown.blockSignals(True)
                self.scan_dropdown.setCurrentIndex(last_selected)
                self.scan_dropdown.blockSignals(False)
            self._run_current_scan()

    def set_kite_client(self, kite_client):
        """Set the Kite client for the scanner."""
        self._kite_client = kite_client
        logger.info("Kite client set for ChartinkScannerTable")

    def _setup_ui(self):
        """Initializes the UI layout and components."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        main_layout.addWidget(self._create_header())

        self.table = QTableWidget()
        self._configure_table()
        main_layout.addWidget(self.table)

        self.table.cellClicked.connect(self._on_cell_clicked)

    def _create_header(self) -> QWidget:
        """Creates the header with the scan dropdown and control buttons in a styled container."""
        # Create container widget for the header
        header_container = QWidget()
        header_container.setObjectName("headerContainer")

        header_layout = QHBoxLayout(header_container)
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_layout.setSpacing(12)

        # Scan label with consistent styling
        scan_label = QLabel("SCAN")
        scan_label.setObjectName("scanLabel")
        scan_label.setFixedWidth(40)
        header_layout.addWidget(scan_label)

        # Dropdown with improved styling
        self.scan_dropdown = QComboBox()
        self.scan_dropdown.setObjectName("scanDropdown")
        self.scan_dropdown.currentIndexChanged.connect(self._on_scan_selection_changed)
        header_layout.addWidget(self.scan_dropdown, 1)

        # Settings button with improved styling
        self.manage_btn = QPushButton("⚙")
        self.manage_btn.setObjectName("settingsButton")
        self.manage_btn.setToolTip("Manage Scans")
        self.manage_btn.setFixedSize(32, 32)
        self.manage_btn.clicked.connect(self._manage_scans)
        header_layout.addWidget(self.manage_btn)

        self._update_scan_dropdown()

        return header_container

    def _update_scan_dropdown(self):
        """Update the scan dropdown with current scans."""
        # Block signals to prevent auto-running scan while repopulating
        self.scan_dropdown.blockSignals(True)
        self.scan_dropdown.clear()

        if self.scans:
            scan_names = [scan.get("name", f"Scan {i + 1}") for i, scan in enumerate(self.scans)]
            self.scan_dropdown.addItems(scan_names)
            self.scan_dropdown.setEnabled(True)
            self.manage_btn.setEnabled(True)
        else:
            self.scan_dropdown.addItem("No scans configured")
            self.scan_dropdown.setEnabled(False)
            self.manage_btn.setEnabled(True)  # Always allow managing/adding scans

        self.scan_dropdown.blockSignals(False)

    def _configure_table(self):
        """Configures the properties and headers of the table."""
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Symbol", "LTP", "Vol", "%Ch"])

        # Configure headers
        self.table.horizontalHeader().setVisible(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)  # Symbol
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # LTP
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Vol
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # %Ch

        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)

    def set_instrument_map(self, instrument_map: Dict[str, Dict]):
        """Receives the master instrument map for data lookups."""
        self._instrument_map = instrument_map
        self._update_existing_symbols()

    def _update_existing_symbols(self):
        if not self._instrument_map:
            return

        tokens_to_subscribe = []

        for symbol in self._symbol_data.keys():
            if symbol in self._instrument_map:
                instrument = self._instrument_map[symbol]
                token = instrument.get('instrument_token')
                if token:
                    self._symbol_data[symbol] = {
                        "symbol": symbol,
                        "instrument_token": token,
                        "close_price": instrument.get('ohlc', {}).get('close', 0.0),
                        "ltp": instrument.get('last_price', 0.0),
                        "change_pct": 0.0
                    }
                    tokens_to_subscribe.append(token)

        if tokens_to_subscribe:
            self.subscribe_tokens_requested.emit(tokens_to_subscribe)

        self._update_table_display()

    @Slot(list)
    def update_data(self, ticks: List[Dict]):
        """Updates LTP and change% from WebSocket ticks."""
        for tick in ticks:
            token = tick.get('instrument_token')
            ltp = tick.get('last_price')
            volume = tick.get('volume', 0)

            for symbol, data in self._symbol_data.items():
                if data.get('instrument_token') == token and ltp is not None:
                    old_ltp = data.get('ltp', 0.0)
                    data['ltp'] = ltp

                    if 'volume' in tick:
                        data['volume'] = volume

                    close_price = data.get('close_price', 0.0)

                    if close_price <= 0:
                        close_price = tick.get('ohlc', {}).get('close', 0.0)
                        if close_price > 0:
                            data['close_price'] = close_price

                    if close_price > 0:
                        change_pct = ((ltp - close_price) / close_price) * 100
                        data['change_pct'] = change_pct

                    if symbol in self._symbol_to_row:
                        row = self._symbol_to_row[symbol]
                        self._update_row_data(row, data)

                        if old_ltp == 0.0:
                            logger.debug(
                                f"First update for {symbol}: LTP={ltp}, Vol={volume}, %Ch={data.get('change_pct', 0):.2f}%")

                    logger.debug(
                        f"Tick for {symbol} → LTP: {ltp}, Vol: {tick.get('volume')}, Close: {tick.get('ohlc', {}).get('close')}")

                    break

    def _update_row_data(self, row: int, data: Dict):
        """Updates the text and color for a single row."""
        if row >= self.table.rowCount():
            return

        # Update symbol
        self.table.item(row, 0).setText(data['symbol'])

        # Update LTP
        ltp = data.get('ltp', 0.0)
        self.table.item(row, 1).setText(f"{ltp:.2f}")

        # Update Volume with K/M formatting
        volume = data.get('volume', 0)
        if volume >= 1000000:
            volume_text = f"{volume / 1000000:.1f}M"
        elif volume >= 1000:
            volume_text = f"{volume / 1000:.0f}K"
        else:
            volume_text = str(volume)
        self.table.item(row, 2).setText(volume_text)

        # Update change %
        change_pct = data.get('change_pct', 0.0)
        self.table.item(row, 3).setText(f"{change_pct:.2f}%")

        # Apply color coding
        profit_color = QColor("#00d4aa")  # Bright teal
        loss_color = QColor("#ff4757")  # Bright red
        neutral_color = QColor("#747d8c")  # Grey

        color = profit_color if change_pct > 0 else (loss_color if change_pct < 0 else neutral_color)

        # Apply colors to LTP and %Ch columns
        self.table.item(row, 1).setForeground(color)
        self.table.item(row, 3).setForeground(color)

        # Volume stays neutral colored
        self.table.item(row, 2).setForeground(neutral_color)

        # Right align numeric columns
        for col in range(1, 4):
            self.table.item(row, col).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    @Slot(list)
    def _on_scan_complete(self, symbols: List[str]):
        self._symbol_data.clear()
        self._symbol_to_row.clear()
        self.table.setRowCount(0)

        if not symbols:
            self.table.insertRow(0)
            item = QTableWidgetItem("No symbols found")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(0, 0, item)
            self.table.setItem(0, 1, QTableWidgetItem(""))
            self.table.setItem(0, 2, QTableWidgetItem(""))
            self.table.setItem(0, 3, QTableWidgetItem(""))
        else:
            tokens_to_subscribe = []

            for i, symbol in enumerate(sorted(symbols)):
                self._symbol_to_row[symbol] = i

                symbol_data = {
                    "symbol": symbol,
                    "instrument_token": None,
                    "close_price": 0.0,
                    "ltp": 0.0,
                    "volume": 0,
                    "change_pct": 0.0
                }

                if symbol in self._instrument_map:
                    instrument = self._instrument_map[symbol]
                    token = instrument.get('instrument_token')
                    if token:
                        symbol_data.update({
                            "instrument_token": token,
                            "close_price": instrument.get('ohlc', {}).get('close', 0.0),
                            "ltp": instrument.get('last_price', 0.0),
                        })
                        tokens_to_subscribe.append(token)
                    else:
                        logger.warning(f"No instrument token found for symbol: {symbol}")
                else:
                    logger.warning(f"Symbol {symbol} not found in instrument map")

                self._symbol_data[symbol] = symbol_data

            self._update_table_display()

            if tokens_to_subscribe:
                self.subscribe_tokens_requested.emit(tokens_to_subscribe)
                QTimer.singleShot(1000, self._request_fresh_subscriptions)

        logger.info(f"Scanner table updated with {len(symbols)} symbols.")
        self.scan_dropdown.setEnabled(True)
        self.manage_btn.setEnabled(True)

    def _update_table_display(self):
        """Updates the table display with current symbol data."""
        symbols = sorted(self._symbol_data.keys())
        self.table.setRowCount(len(symbols))

        for row, symbol in enumerate(symbols):
            self._symbol_to_row[symbol] = row
            data = self._symbol_data[symbol]

            # Create table items
            for col in range(4):
                self.table.setItem(row, col, QTableWidgetItem())

            # Update with data
            self._update_row_data(row, data)

    @Slot(str)
    def _on_scan_error(self, error_message: str):
        """Handles errors from the scanning thread."""
        QMessageBox.warning(self, "Scan Error", error_message)

        self.table.setRowCount(0)
        self.table.insertRow(0)
        item = QTableWidgetItem(f"Error: {error_message}")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.table.setItem(0, 0, item)
        # Add empty cells for other columns
        self.table.setItem(0, 1, QTableWidgetItem(""))
        self.table.setItem(0, 2, QTableWidgetItem(""))
        self.table.setItem(0, 3, QTableWidgetItem(""))

        self.scan_dropdown.setEnabled(True)
        self.manage_btn.setEnabled(True)

    def _on_scan_selection_changed(self):
        """Handles scan selection changes and saves the selection."""
        if self.scan_dropdown.signalsBlocked():
            return

        current_index = self.scan_dropdown.currentIndex()
        self._save_last_selected_scan(current_index)
        self._run_current_scan()

    def _request_fresh_subscriptions(self):
        if not self._symbol_data or not self._instrument_map:
            return

        tokens_to_subscribe = []

        for symbol, data in self._symbol_data.items():
            if symbol in self._instrument_map:
                instrument = self._instrument_map[symbol]
                token = instrument.get('instrument_token')
                if token:
                    data.update({
                        "instrument_token": token,
                        "close_price": instrument.get('ohlc', {}).get('close', 0.0),
                        "ltp": instrument.get('last_price', 0.0),
                    })
                    tokens_to_subscribe.append(token)

        if tokens_to_subscribe:
            logger.info(f"Requesting subscriptions for {len(tokens_to_subscribe)} tokens after scan change")
            self.subscribe_tokens_requested.emit(tokens_to_subscribe)

        self._update_table_display()

    def _save_last_selected_scan(self, index: int):
        """Saves the last selected scan index to settings."""
        try:
            settings = {"last_selected_scan": index}
            settings_dir = os.path.dirname(SETTINGS_FILE)
            if not os.path.exists(settings_dir):
                os.makedirs(settings_dir, exist_ok=True)

            with open(SETTINGS_FILE, 'w') as f:
                json.dump(settings, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save scanner settings: {e}")

    def _load_last_selected_scan(self) -> int:
        """Loads the last selected scan index from settings."""
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, 'r') as f:
                    settings = json.load(f)
                    return settings.get("last_selected_scan", 0)
        except Exception as e:
            logger.warning(f"Failed to load scanner settings: {e}")
        return 0

    def _manage_scans(self):
        """Open the manage scans dialog."""
        dialog = ManageScansDialog(self.scans, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.scans = dialog.get_scans()
            self._save_scans()

            # Temporarily block signals to prevent double-run
            self.scan_dropdown.blockSignals(True)
            current_index = self.scan_dropdown.currentIndex()
            self._update_scan_dropdown()

            # Restore index if possible, otherwise set to 0
            if current_index < self.scan_dropdown.count() and current_index >= 0:
                self.scan_dropdown.setCurrentIndex(current_index)
            else:
                self.scan_dropdown.setCurrentIndex(0)
            self.scan_dropdown.blockSignals(False)

            if not self.scans:
                self.table.setRowCount(0)
                self.table.insertRow(0)
                item = QTableWidgetItem("No scans configured. Click '⚙' to add a scan.")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(0, 0, item)
                # Add empty cells for other columns
                self.table.setItem(0, 1, QTableWidgetItem(""))
                self.table.setItem(0, 2, QTableWidgetItem(""))
                self.table.setItem(0, 3, QTableWidgetItem(""))
            else:
                self._run_current_scan()

    def _run_current_scan(self):
        """Runs the currently selected Chartink scan in the background."""
        if self.scan_dropdown.signalsBlocked():
            return

        if not self.scans:
            return

        current_index = self.scan_dropdown.currentIndex()
        if current_index < 0 or current_index >= len(self.scans):
            return

        selected_scan = self.scans[current_index]
        selected_scan_url = selected_scan.get("url")

        if not selected_scan_url:
            # This can happen if the last scan was deleted
            self.table.setRowCount(0)
            self.table.insertRow(0)
            item = QTableWidgetItem("Invalid Scan URL.")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(0, 0, item)
            # Add empty cells for other columns
            self.table.setItem(0, 1, QTableWidgetItem(""))
            self.table.setItem(0, 2, QTableWidgetItem(""))
            self.table.setItem(0, 3, QTableWidgetItem(""))
            return

        logger.info(f"Running Chartink scan: {selected_scan.get('name', 'Unnamed')} - {selected_scan_url}")

        # Disable controls during scan
        self.scan_dropdown.setEnabled(False)
        self.manage_btn.setEnabled(False)

        # Show loading state
        self.table.setRowCount(0)
        self.table.insertRow(0)
        item = QTableWidgetItem("Loading...")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.table.setItem(0, 0, item)
        # Add empty cells for other columns
        self.table.setItem(0, 1, QTableWidgetItem(""))
        self.table.setItem(0, 2, QTableWidgetItem(""))
        self.table.setItem(0, 3, QTableWidgetItem(""))

        if self.scan_thread and self.scan_thread.isRunning():
            self.scan_thread.terminate()
            self.scan_thread.wait(3000)

        self.scan_thread = ScanWorker(selected_scan_url)
        self.scan_thread.scan_completed.connect(self._on_scan_complete)
        self.scan_thread.scan_error.connect(self._on_scan_error)
        self.scan_thread.start()

    def _on_cell_clicked(self, row: int, column: int):
        """Emits the symbol from the clicked row."""
        try:
            symbol_item = self.table.item(row, 0)
            if symbol_item and symbol_item.flags() & Qt.ItemFlag.ItemIsSelectable:
                symbol_text = symbol_item.text()
                if symbol_text and not symbol_text.startswith(("Error:", "Loading", "No symbols", "No scans")):
                    self.symbol_selected.emit(symbol_text)
        except Exception as e:
            logger.warning(f"Could not get symbol from clicked row {row}: {e}")

    def get_all_tokens(self) -> List[int]:
        """Returns a list of all instrument tokens currently in the scanner."""
        return [
            data['instrument_token']
            for data in self._symbol_data.values()
            if data and data.get('instrument_token')
        ]

    def _load_scans(self) -> List[Dict[str, str]]:
        """Loads scan URLs from the user's JSON configuration file."""
        scan_dir = os.path.dirname(SCAN_URL_FILE)
        if not os.path.exists(scan_dir):
            os.makedirs(scan_dir, exist_ok=True)

        if not os.path.exists(SCAN_URL_FILE):
            logger.info(f"Scan configuration file not found: {SCAN_URL_FILE}. Creating with default scans.")
            default_scans = [
                {
                    "name": "Example: Above 20 SMA",
                    "url": "( {57960} ( latest \"close\" > latest \"sma( close , 20 )\" ) )"
                }
            ]
            self._save_scans_to_file(default_scans)
            return default_scans

        try:
            with open(SCAN_URL_FILE, 'r') as f:
                scans = json.load(f)

            if not isinstance(scans, list):
                logger.error("Scan configuration must be a list")
                return []

            valid_scans = []
            for i, scan in enumerate(scans):
                if isinstance(scan, dict) and 'url' in scan:
                    if 'name' not in scan:
                        scan['name'] = f"Scan {i + 1}"
                    valid_scans.append(scan)
                else:
                    logger.warning(f"Invalid scan configuration at index {i}: {scan}")

            logger.info(f"Loaded {len(valid_scans)} valid scan configurations")
            return valid_scans

        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load Chartink scan URLs: {e}")
            return []

    def _save_scans(self):
        """Save current scans to file."""
        self._save_scans_to_file(self.scans)

    def _save_scans_to_file(self, scans: List[Dict[str, str]]):
        """Save scans list to the configuration file."""
        try:
            scan_dir = os.path.dirname(SCAN_URL_FILE)
            if not os.path.exists(scan_dir):
                os.makedirs(scan_dir, exist_ok=True)

            with open(SCAN_URL_FILE, 'w') as f:
                json.dump(scans, f, indent=2)
            logger.info(f"Saved {len(scans)} scans to {SCAN_URL_FILE}")
        except Exception as e:
            logger.error(f"Failed to save scans: {e}")
            QMessageBox.critical(self, "Save Error", f"Failed to save scans: {e}")

    def _apply_styles(self):
        """Applies a consistent, modern dark theme stylesheet."""
        self.setStyleSheet("""
            QWidget { 
                background-color: #1c1c2e; 
                color: #e0e0e0; 
                font-family: "Segoe UI"; 
            }

            /* Header Container - Professional styled background */
            QWidget#headerContainer {
                background-color: #16213e;
                border: 1px solid #233554;
                border-radius: 8px;
                margin: 4px;
            }

            QWidget#headerContainer:hover {
                border-color: #2a4565;
            }

            /* Header styling */
            QLabel#scanLabel {
                color: #ccd6f6;
                font-weight: 700;
                font-size: 11px;
                letter-spacing: 1px;
                padding: 0px;
                background: transparent;
            }

            QComboBox#scanDropdown {
                background-color: #1a1a2e; 
                border: 1px solid #233554;
                color: #e6e6e6; 
                padding: 8px 14px; 
                border-radius: 6px; 
                font-size: 12px;
                font-weight: 500;
                min-height: 16px;
            }

            QComboBox#scanDropdown:hover {
                border-color: #64ffda;
                background-color: #1e1e32;
            }

            QComboBox#scanDropdown:focus {
                border-color: #64ffda;
                background-color: #1e1e32;
            }

            QComboBox#scanDropdown:disabled { 
                background-color: #0f0f1a; 
                color: #5a5a6e; 
                border-color: #1a1a2e;
            }

            QComboBox#scanDropdown::drop-down {
                border: none;
                width: 24px;
                padding-right: 2px;
                background: transparent;
            }

            QComboBox#scanDropdown::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid #8892b0;
                margin-right: 8px;
            }

            QComboBox#scanDropdown::down-arrow:hover {
                border-top-color: #64ffda;
            }

            /* Dropdown list styling */
            QComboBox#scanDropdown QAbstractItemView {
                background-color: #1a1a2e;
                border: 1px solid #233554;
                border-radius: 6px;
                color: #e6e6e6;
                selection-background-color: #64ffda;
                selection-color: #0f0f23;
                padding: 4px;
            }

            QComboBox#scanDropdown QAbstractItemView::item {
                padding: 8px 12px;
                border: none;
                border-radius: 4px;
                margin: 1px;
            }

            QComboBox#scanDropdown QAbstractItemView::item:hover {
                background-color: #233554;
            }

            QComboBox#scanDropdown QAbstractItemView::item:selected {
                background-color: #64ffda;
                color: #0f0f23;
            }

            QPushButton#settingsButton {
                background-color: #1a1a2e; 
                color: #ccd6f6;
                font-size: 16px; 
                font-weight: bold;
                border-radius: 6px; 
                border: 1px solid #233554;
                padding: 0px;
            }

            QPushButton#settingsButton:hover { 
                background-color: #233554;
                border-color: #64ffda;
                color: #64ffda;
                transform: scale(1.05);
            }

            QPushButton#settingsButton:pressed {
                background-color: #16213e;
                border-color: #64ffda;
                color: #64ffda;
            }

            QPushButton#settingsButton:disabled { 
                background-color: #0f0f1a; 
                color: #5a5a6e;
                border-color: #1a1a2e;
            }

            QTableWidget {
                border: none; 
                gridline-color: #2a2a4a; 
                font-size: 13px;
                background-color: #1c1c2e;
                selection-background-color: #3a3a5a;
            }

            QTableWidget::item {
                padding: 4px 8px; 
                border-bottom: 1px solid #2a2a4a;
                background-color: transparent;
            }

            QTableWidget::item:selected { 
                background-color: #3a3a5a; 
                color: #64ffda;
            }

            QTableWidget::item:alternate {
                background-color: #0f0f23;
            }

            QHeaderView::section {
                background-color: #0f0f23; 
                color: #8892b0; 
                padding: 6px 8px;
                border: none; 
                border-bottom: 1px solid #3a3a5a;
                border-right: 1px solid #2a2a4a;
                font-weight: 600; 
                font-size: 10px; 
                letter-spacing: 0.5px;
            }

            QHeaderView::section:last {
                border-right: none;
            }

            /* Dialog styles */
            QDialog { 
                background-color: #1c1c2e; 
                color: #e0e0e0; 
            }

            QLineEdit {
                background-color: #2a2a4a; 
                border: 1px solid #3a3a5a;
                color: #e0e0e0; 
                padding: 8px; 
                border-radius: 4px; 
                font-size: 13px;
            }

            QLineEdit:focus { 
                border-color: #007acc; 
            }

            QPushButton {
                background-color: #3a3a5a; 
                color: #e0e0e0; 
                font-size: 12px;
                border-radius: 4px; 
                padding: 8px 16px; 
                border: none;
            }

            QPushButton:hover { 
                background-color: #4a4a6a; 
            }

            QPushButton:pressed { 
                background-color: #2a2a4a; 
            }

            /* Scrollbar Styling */
            QScrollBar:vertical {
                background-color: #0f0f23;
                width: 8px;
                border: none;
            }

            QScrollBar::handle:vertical {
                background-color: #2a2a4a;
                border-radius: 4px;
                min-height: 20px;
            }

            QScrollBar::handle:vertical:hover {
                background-color: #3a3a5a;
            }

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                border: none;
                background: none;
            }
        """)