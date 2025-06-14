import json
import os
import logging
import pandas as pd
import requests
from bs4 import BeautifulSoup as bs
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QTableWidget, QHeaderView,
                               QTableWidgetItem, QHBoxLayout, QComboBox,
                               QPushButton, QMessageBox, QLabel, QApplication)  # <<< FIXED: QApplication added here
from PySide6.QtCore import Qt

# Define the path for storing saved scans relative to the user's home directory
USER_DATA_DIR = os.path.expanduser("~/.swing_trader")
SCANS_FILE = os.path.join(USER_DATA_DIR, "chartink_scans.json")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class ChartinkScannerTable(QWidget):
    """
    A simple, synchronous scanner widget that fetches and displays stock symbols
    and names from Chartink scans directly when a scan is selected.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scans = self._load_scans()
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)

        toolbar_layout = QHBoxLayout()
        toolbar_layout.addWidget(QLabel("Select Scan:"))

        self.scan_dropdown = QComboBox()
        self.scan_dropdown.addItems([scan['name'] for scan in self.scans])
        # The scan is now triggered directly by this signal.
        self.scan_dropdown.currentIndexChanged.connect(self.run_scan_synchronously)
        toolbar_layout.addWidget(self.scan_dropdown)

        add_scan_button = QPushButton("+")
        add_scan_button.setToolTip("Add new scan")
        add_scan_button.setFixedSize(30, 30)
        add_scan_button.clicked.connect(self.add_new_scan)
        toolbar_layout.addWidget(add_scan_button)

        main_layout.addLayout(toolbar_layout)

        self.status_label = QLabel("Ready.")
        main_layout.addWidget(self.status_label)

        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Symbol", "Name"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        main_layout.addWidget(self.table)

        # Run the first scan on startup if one exists
        if self.scans:
            self.run_scan_synchronously()

    def run_scan_synchronously(self):
        """
        Fetches Chartink data directly in the main thread.
        THE UI WILL FREEZE during this operation, as requested.
        """
        selected_index = self.scan_dropdown.currentIndex()
        if selected_index < 0: return

        scan_clause = self.scans[selected_index]['clause']
        self.scan_dropdown.setEnabled(False)
        self.table.setRowCount(0)

        try:
            # --- LOGGING ---
            self.status_label.setText("Status: Sending request to Chartink...")
            logging.info("Sending request to Chartink...")
            QApplication.processEvents()  # Allow UI to update before the freeze

            with requests.session() as s:
                s.headers['User-Agent'] = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                                           '(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
                url = 'https://chartink.com/screener/process'

                r = s.get('https://chartink.com/screener/dashboard', timeout=15)
                r.raise_for_status()
                soup = bs(r.text, 'lxml')
                csrf_token = soup.find('meta', {'name': 'csrf-token'})['content']
                s.headers['x-csrf-token'] = csrf_token

                payload = {'scan_clause': scan_clause}
                res = s.post(url, data=payload, timeout=15)
                res.raise_for_status()

                # --- LOGGING ---
                self.status_label.setText("Status: Processing results...")
                logging.info("Processing scan results...")
                QApplication.processEvents()

                scan_results = res.json().get("data", [])

                df = pd.DataFrame([{'symbol': item['nsecode'], 'name': item.get('name', 'N/A')}
                                   for item in scan_results if 'nsecode' in item])

            # Update the table with results
            self.table.setRowCount(len(df))
            for i, row in df.iterrows():
                self.table.setItem(i, 0, QTableWidgetItem(row['symbol']))
                self.table.setItem(i, 1, QTableWidgetItem(row['name']))

            # --- LOGGING ---
            self.status_label.setText(f"Done. Found {len(df)} symbols.")
            logging.info(f"Scan complete. Found {len(df)} symbols.")

        except Exception as e:
            logging.error(f"Error during scan: {e}", exc_info=True)
            self.status_label.setText(f"Error: {e}")
            QMessageBox.critical(self, "Scan Failed", f"An error occurred:\n{e}")
        finally:
            # Re-enable the dropdown menu
            self.scan_dropdown.setEnabled(True)

    def _load_scans(self):
        if not os.path.exists(SCANS_FILE):
            os.makedirs(USER_DATA_DIR, exist_ok=True)
            return []
        try:
            with open(SCANS_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _save_scans(self):
        os.makedirs(os.path.dirname(SCANS_FILE), exist_ok=True)
        with open(SCANS_FILE, 'w') as f:
            json.dump(self.scans, f, indent=4)

    def add_new_scan(self):
        # Assuming AddChartinkScanDialog is in the dialogs sub-package
        from ..dialogs.add_chartink_scan_dialog import AddChartinkScanDialog
        dialog = AddChartinkScanDialog(self)
        if dialog.exec():
            scan_data = dialog.get_data()
            if scan_data['name'] and scan_data['clause']:
                self.scans.append(scan_data)
                self._save_scans()
                self.scan_dropdown.addItem(scan_data['name'])
                # Set the new scan as active, which will also trigger it to run
                self.scan_dropdown.setCurrentIndex(len(self.scans) - 1)
            else:
                QMessageBox.warning(self, "Input Error", "Scan name and clause cannot be empty.")