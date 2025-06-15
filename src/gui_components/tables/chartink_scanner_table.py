import logging
import json
import os
import pandas as pd
import requests
from bs4 import BeautifulSoup as bs
from typing import List, Dict

from PySide6.QtCore import Signal, Slot, Qt, QThread, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QPushButton, QHBoxLayout, QLabel, QComboBox, QMessageBox,
    QDialog, QLineEdit, QDialogButtonBox, QFormLayout, QGroupBox, QScrollArea
)

logger = logging.getLogger(__name__)
SCAN_URL_FILE = os.path.join(os.path.expanduser("~/.swing_trader"), "chartink_scans.json")


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
        self.url_input.setPlaceholderText("https://chartink.com/screener/... or just the scan clause")
        form_layout.addRow("Scan URL/Clause:", self.url_input)

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
        """Executes the network request and processing for the scan."""
        try:
            # Extract scan clause from URL - handle different URL formats
            if 'screener/' in self.scan_url:
                scan_clause = self.scan_url.split('screener/')[-1]
            else:
                # If it's already just the scan clause
                scan_clause = self.scan_url

            logger.info(f"Extracted scan clause: {scan_clause}")

            # First, get the dashboard page to establish session and get CSRF token
            dashboard_url = 'https://chartink.com/screener/dashboard'
            logger.info("Getting dashboard page...")
            r = self.session.get(dashboard_url, timeout=20)
            r.raise_for_status()

            # Parse CSRF token
            soup = bs(r.text, 'html.parser')
            csrf_meta = soup.find('meta', {'name': 'csrf-token'})

            if not csrf_meta:
                raise Exception("Could not find CSRF token in the page")

            csrf_token = csrf_meta.get('content')
            if not csrf_token:
                raise Exception("CSRF token is empty")

            logger.info(f"Got CSRF token: {csrf_token[:10]}...")

            # Update headers with CSRF token
            self.session.headers.update({
                'X-CSRF-TOKEN': csrf_token,
                'Referer': dashboard_url
            })

            # Prepare the payload
            payload = {'scan_clause': scan_clause}
            logger.info(f"Sending payload: {payload}")

            # Post the scan request
            process_url = 'https://chartink.com/screener/process'
            res = self.session.post(process_url, data=payload, timeout=20)
            res.raise_for_status()

            logger.info(f"Response status: {res.status_code}")

            # Parse response
            try:
                response_data = res.json()
                logger.info(
                    f"Response keys: {list(response_data.keys()) if isinstance(response_data, dict) else 'Not a dict'}")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response: {e}")
                logger.error(f"Response text (first 500 chars): {res.text[:500]}")
                raise Exception(f"Invalid JSON response: {e}")

            # Extract symbols
            scan_results = response_data.get("data", [])
            if not scan_results:
                logger.warning("No data found in response or empty data array")

            symbols = []
            for item in scan_results:
                if isinstance(item, dict) and 'nsecode' in item:
                    symbols.append(item['nsecode'])
                else:
                    logger.warning(f"Item missing nsecode: {item}")

            logger.info(f"Successfully extracted {len(symbols)} symbols")
            self.scan_completed.emit(symbols)

        except requests.exceptions.Timeout:
            error_message = "Request timed out. Please try again."
            logger.error(error_message)
            self.scan_error.emit(error_message)
        except requests.exceptions.ConnectionError:
            error_message = "Connection error. Please check your internet connection."
            logger.error(error_message)
            self.scan_error.emit(error_message)
        except requests.exceptions.HTTPError as e:
            error_message = f"HTTP error {e.response.status_code}: {e.response.reason}"
            logger.error(error_message)
            self.scan_error.emit(error_message)
        except Exception as e:
            error_message = f"Failed to run scan. Error: {str(e)}"
            logger.error(error_message, exc_info=True)
            self.scan_error.emit(error_message)


class ChartinkScannerTable(QWidget):
    """
    A widget that displays stock symbols retrieved from Chartink scans.
    It allows users to run scans asynchronously and select symbols to view charts.
    """
    symbol_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scans = self._load_scans()
        self.scan_thread: ScanWorker = None
        self._setup_ui()
        self._apply_styles()

        # Only run scan if we have scans configured
        if self.scans:
            self._run_current_scan()

    def _setup_ui(self):
        """Initializes the UI layout and components."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        main_layout.addLayout(self._create_header())

        self.table = QTableWidget()
        self._configure_table()
        main_layout.addWidget(self.table)

        self.table.cellClicked.connect(self._on_cell_clicked)

    def _create_header(self) -> QHBoxLayout:
        """Creates the header with the scan dropdown and control buttons."""
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(10, 8, 10, 8)

        # Scan dropdown
        self.scan_dropdown = QComboBox()
        self._update_scan_dropdown()
        self.scan_dropdown.currentIndexChanged.connect(self._run_current_scan)

        # Refresh button
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("secondaryButton")
        self.refresh_btn.clicked.connect(self._run_current_scan)

        # Manage scans button
        self.manage_btn = QPushButton("Manage Scans")
        self.manage_btn.setObjectName("secondaryButton")
        self.manage_btn.clicked.connect(self._manage_scans)

        # Add scan button (quick add)
        self.add_btn = QPushButton("+ Add")
        self.add_btn.setObjectName("primaryButton")
        self.add_btn.clicked.connect(self._quick_add_scan)

        header_layout.addWidget(QLabel("Scan:"))
        header_layout.addWidget(self.scan_dropdown, 1)
        header_layout.addWidget(self.refresh_btn)
        header_layout.addWidget(self.manage_btn)
        header_layout.addWidget(self.add_btn)
        return header_layout

    def _update_scan_dropdown(self):
        """Update the scan dropdown with current scans."""
        self.scan_dropdown.clear()

        if self.scans:
            scan_names = [scan.get("name", f"Scan {i + 1}") for i, scan in enumerate(self.scans)]
            self.scan_dropdown.addItems(scan_names)
            self.refresh_btn.setEnabled(True)
        else:
            self.scan_dropdown.addItem("No scans configured")
            self.refresh_btn.setEnabled(False)

    def _configure_table(self):
        """Configures the properties and headers of the table."""
        self.table.setColumnCount(1)
        self.table.setHorizontalHeaderLabels(["Scanned Symbols"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

    @Slot(list)
    def _on_scan_complete(self, symbols: List[str]):
        """Populates the table with the results from a scan."""
        self.table.setRowCount(0)

        if not symbols:
            # Add a row to show "No results"
            self.table.insertRow(0)
            item = QTableWidgetItem("No symbols found")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(0, 0, item)
        else:
            for symbol in sorted(symbols):
                row = self.table.rowCount()
                self.table.insertRow(row)
                self.table.setItem(row, 0, QTableWidgetItem(symbol))

        logger.info(f"Scanner table updated with {len(symbols)} symbols.")
        self.refresh_btn.setEnabled(True)
        self.scan_dropdown.setEnabled(True)
        self.manage_btn.setEnabled(True)
        self.add_btn.setEnabled(True)

    @Slot(str)
    def _on_scan_error(self, error_message: str):
        """Handles errors from the scanning thread."""
        QMessageBox.warning(self, "Scan Error", error_message)

        # Clear table and show error
        self.table.setRowCount(0)
        self.table.insertRow(0)
        item = QTableWidgetItem(f"Error: {error_message}")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.table.setItem(0, 0, item)

        self.refresh_btn.setEnabled(True)
        self.scan_dropdown.setEnabled(True)
        self.manage_btn.setEnabled(True)
        self.add_btn.setEnabled(True)

    def _quick_add_scan(self):
        """Quick add a new scan."""
        dialog = AddScanDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            scan_data = dialog.get_scan_data()
            self.scans.append(scan_data)
            self._save_scans()
            self._update_scan_dropdown()

            # Select the newly added scan
            self.scan_dropdown.setCurrentIndex(len(self.scans) - 1)
            QMessageBox.information(self, "Scan Added", f"'{scan_data['name']}' has been added successfully!")

    def _manage_scans(self):
        """Open the manage scans dialog."""
        dialog = ManageScansDialog(self.scans, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.scans = dialog.get_scans()
            self._save_scans()
            self._update_scan_dropdown()

            # Clear table if no scans left
            if not self.scans:
                self.table.setRowCount(0)
                self.table.insertRow(0)
                item = QTableWidgetItem("No scans configured. Click 'Add' to create your first scan.")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(0, 0, item)

    def _run_current_scan(self):
        """Runs the currently selected Chartink scan in the background."""
        if not self.scans:
            return

        current_index = self.scan_dropdown.currentIndex()
        if current_index < 0 or current_index >= len(self.scans):
            return

        selected_scan = self.scans[current_index]
        selected_scan_url = selected_scan.get("url")

        if not selected_scan_url:
            QMessageBox.warning(self, "Invalid Scan", "The selected scan does not have a valid URL.")
            return

        logger.info(f"Running Chartink scan: {selected_scan.get('name', 'Unnamed')} - {selected_scan_url}")

        # Disable controls during scan
        self.refresh_btn.setEnabled(False)
        self.scan_dropdown.setEnabled(False)
        self.manage_btn.setEnabled(False)
        self.add_btn.setEnabled(False)

        # Show loading state
        self.table.setRowCount(0)
        self.table.insertRow(0)
        item = QTableWidgetItem("Loading...")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.table.setItem(0, 0, item)

        # Terminate previous thread if it's still running
        if self.scan_thread and self.scan_thread.isRunning():
            self.scan_thread.terminate()
            self.scan_thread.wait(3000)  # Wait up to 3 seconds

        # Start new scan
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
                # Only emit if it's a valid symbol (not error/loading messages)
                if symbol_text and not symbol_text.startswith(("Error:", "Loading", "No symbols", "No scans")):
                    self.symbol_selected.emit(symbol_text)
        except Exception as e:
            logger.warning(f"Could not get symbol from clicked row {row}: {e}")

    def _load_scans(self) -> List[Dict[str, str]]:
        """Loads scan URLs from the user's JSON configuration file."""
        # Ensure the directory exists
        scan_dir = os.path.dirname(SCAN_URL_FILE)
        if not os.path.exists(scan_dir):
            os.makedirs(scan_dir, exist_ok=True)

        if not os.path.exists(SCAN_URL_FILE):
            logger.info(f"Scan configuration file not found: {SCAN_URL_FILE}. Creating with default scans.")
            # Create with some example scans
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

            # Validate the loaded data
            if not isinstance(scans, list):
                logger.error("Scan configuration must be a list")
                return []

            valid_scans = []
            for i, scan in enumerate(scans):
                if isinstance(scan, dict) and 'url' in scan:
                    # Ensure name field exists
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
            # Ensure directory exists
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
            QWidget { background-color: #1c1c2e; color: #e0e0e0; font-family: "Segoe UI"; }
            QLabel { font-size: 13px; padding-right: 5px; }
            QComboBox {
                background-color: #2a2a4a; border: 1px solid #3a3a5a;
                color: #e0e0e0; padding: 6px; border-radius: 6px; font-size: 13px;
            }
            QComboBox:disabled { background-color: #1a1a2a; color: #666; }
            #secondaryButton {
                background-color: #3a3a5a; color: #e0e0e0; font-size: 12px;
                font-weight: bold; border-radius: 6px; padding: 6px 14px; border: none;
            }
            #secondaryButton:hover { background-color: #4a4a6a; }
            #secondaryButton:disabled { background-color: #2a2a3a; color: #666; }
            #primaryButton {
                background-color: #007acc; color: white; font-size: 12px;
                font-weight: bold; border-radius: 6px; padding: 6px 14px; border: none;
            }
            #primaryButton:hover { background-color: #005a9e; }
            #primaryButton:disabled { background-color: #2a2a3a; color: #666; }
            QTableWidget {
                border: none; gridline-color: #2a2a4a; font-size: 13px;
            }
            QHeaderView::section {
                background-color: #1c1c2e; color: #8a8a9e; padding: 8px;
                border: none; border-bottom: 1px solid #3a3a5a;
                font-weight: bold; font-size: 11px; text-transform: uppercase;
            }
            QTableWidget::item {
                padding-left: 10px; border-bottom: 1px solid #2a2a4a;
            }
            QTableWidget::item:selected { background-color: #3a3a5a; }

            /* Dialog styles */
            QDialog { background-color: #1c1c2e; color: #e0e0e0; }
            QLineEdit {
                background-color: #2a2a4a; border: 1px solid #3a3a5a;
                color: #e0e0e0; padding: 8px; border-radius: 4px; font-size: 13px;
            }
            QLineEdit:focus { border-color: #007acc; }
            QPushButton {
                background-color: #3a3a5a; color: #e0e0e0; font-size: 12px;
                border-radius: 4px; padding: 8px 16px; border: none;
            }
            QPushButton:hover { background-color: #4a4a6a; }
            QPushButton:pressed { background-color: #2a2a4a; }
        """)