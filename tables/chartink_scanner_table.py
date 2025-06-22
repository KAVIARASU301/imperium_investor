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
    QDialog, QLineEdit, QDialogButtonBox, QFormLayout, QGroupBox, QScrollArea,
    QFrame, QSpacerItem, QSizePolicy, QTextEdit
)
from PySide6.QtGui import QColor, QFont, QPalette, QIcon, QBrush
from PySide6.QtCore import QItemSelectionModel

logger = logging.getLogger(__name__)
SCAN_URL_FILE = os.path.join(os.path.expanduser("~/.swing_trader"), "chartink_scans.json")
SETTINGS_FILE = os.path.join(os.path.expanduser("~/.swing_trader"), "scanner_settings.json")


class ModernAddScanDialog(QDialog):
    """Enhanced dialog for adding new Chartink scans with modern styling."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add New Chartink Scan")
        self.setModal(True)
        self.setFixedSize(600, 500)
        # Apply FramelessWindowHint for custom styling, and Wa_TranslucentBackground for frost effect
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._drag_pos = None
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        # Main container for the "frosted" background effect
        main_container = QWidget()
        main_container.setObjectName("dialogContainer")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(main_container)

        container_layout = QVBoxLayout(main_container)
        container_layout.setContentsMargins(20, 16, 20, 20) # Reduced margins
        container_layout.setSpacing(15) # Reduced spacing

        # Header with title and close button
        header_layout = QHBoxLayout()

        title_label = QLabel("Add New Scan")
        title_label.setObjectName("dialogTitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(24, 24) # Smaller close button
        close_btn.clicked.connect(self.reject)

        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(close_btn)

        container_layout.addLayout(header_layout)

        # Form section
        form_group = QGroupBox("Scan Configuration")
        form_group.setObjectName("formGroup")
        form_layout = QVBoxLayout(form_group)
        form_layout.setSpacing(12) # Reduced spacing

        # Scan name
        name_label = QLabel("Scan Name")
        name_label.setObjectName("fieldLabel")
        self.name_input = QLineEdit()
        self.name_input.setObjectName("minimalInput") # New object name for minimal input
        self.name_input.setPlaceholderText("e.g., 'Breakout Stocks', 'High Volume Gainers'")

        form_layout.addWidget(name_label)
        form_layout.addWidget(self.name_input)

        # Scan clause
        clause_label = QLabel("Scan Clause")
        clause_label.setObjectName("fieldLabel")
        self.url_input = QTextEdit()
        self.url_input.setObjectName("minimalTextArea") # New object name for minimal textarea
        self.url_input.setPlaceholderText("Paste your Chartink scan clause here...")
        self.url_input.setMaximumHeight(70) # Reduced height

        form_layout.addWidget(clause_label)
        form_layout.addWidget(self.url_input)

        container_layout.addWidget(form_group)

        # Help section - keep it concise and minimal
        help_frame = QFrame()
        help_frame.setObjectName("helpFrame")
        help_layout = QVBoxLayout(help_frame)
        help_layout.setContentsMargins(12, 8, 12, 8) # Reduced margins

        help_title = QLabel("💡 How to get scan clauses:")
        help_title.setObjectName("helpTitle")

        help_text = QLabel(
            "• Go to chartink.com and create your scan\n"
            "• Copy the URL or just the clause part\n"
            "• Example: ( {57960} ( latest \"close\" > latest \"sma( close , 20 )\" ) )"
        )
        help_text.setObjectName("helpText")
        help_text.setWordWrap(True)

        help_layout.addWidget(help_title)
        help_layout.addWidget(help_text)

        container_layout.addWidget(help_frame)

        # Button section
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10) # Reduced spacing

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondaryMinimalButton") # New object name
        cancel_btn.clicked.connect(self.reject)

        self.save_btn = QPushButton("Add Scan")
        self.save_btn.setObjectName("primaryMinimalButton") # New object name
        self.save_btn.clicked.connect(self.accept)
        self.save_btn.setEnabled(False)

        button_layout.addStretch()
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(self.save_btn)

        container_layout.addLayout(button_layout)

        # Connect validation
        self.name_input.textChanged.connect(self._validate_inputs)
        self.url_input.textChanged.connect(self._validate_inputs)

        # Enable dragging
        main_container.mousePressEvent = self.mousePressEvent
        main_container.mouseMoveEvent = self.mouseMoveEvent
        main_container.mouseReleaseEvent = self.mouseReleaseEvent

    def _validate_inputs(self):
        """Enable/disable save button based on input validation."""
        name_valid = bool(self.name_input.text().strip())
        url_valid = bool(self.url_input.toPlainText().strip())
        self.save_btn.setEnabled(name_valid and url_valid)

    def get_scan_data(self) -> Dict[str, str]:
        """Returns the scan data entered by user."""
        return {
            "name": self.name_input.text().strip(),
            "url": self.url_input.toPlainText().strip()
        }

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

    def _apply_styles(self):
        self.setStyleSheet("""
            QWidget#dialogContainer {
                background-color: rgba(0, 0, 0, 0.8); /* Dark black with some transparency for frosted effect */
                border: 1px solid rgba(60, 60, 60, 0.5); /* Subtle dark border */
                border-radius: 8px; /* Soft edges */
            }

            QLabel#dialogTitle {
                color: #ffffff;
                font-size: 16px; /* Slightly smaller for minimal look */
                font-weight: 600;
            }

            QPushButton#closeButton {
                background-color: transparent;
                color: #a0a0a0;
                border: none;
                border-radius: 4px;
                font-size: 14px;
            }
            QPushButton#closeButton:hover {
                background-color: rgba(50, 50, 50, 0.5); /* Subtle hover for close */
                color: #ffffff;
            }

            QGroupBox#formGroup {
                background-color: rgba(20, 20, 20, 0.7); /* Very dark, slightly transparent */
                border: 1px solid rgba(50, 50, 50, 0.5);
                border-radius: 6px;
                font-weight: 500;
                font-size: 12px;
                color: #e0e0e0;
                padding-top: 10px;
            }
            QGroupBox#formGroup::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #6a9cff; /* A professional blue for titles */
            }

            QLabel#fieldLabel {
                color: #e0e0e0;
                font-size: 12px;
                font-weight: 500;
                margin-bottom: 2px;
            }

            QLineEdit#minimalInput, QTextEdit#minimalTextArea {
                background-color: #0d0d0d; /* Deep black input field */
                border: 1px solid #303030; /* Darker border */
                border-radius: 3px;
                color: #ffffff;
                padding: 6px 8px;
                font-size: 13px;
                selection-background-color: #6a9cff; /* Blue selection */
                font-family: "Segoe UI", sans-serif;
            }
            QTextEdit#minimalTextArea {
                font-family: "Consolas", "Monaco", monospace;
            }
            QLineEdit#minimalInput:focus, QTextEdit#minimalTextArea:focus {
                border-color: #6a9cff;
                background-color: #1a1a1a; /* Slightly lighter on focus */
            }
            QLineEdit#minimalInput::placeholder, QTextEdit#minimalTextArea::placeholder {
                color: #808080;
            }

            QFrame#helpFrame {
                background-color: rgba(10, 10, 20, 0.7); /* Dark muted blue for help */
                border: 1px solid rgba(40, 40, 60, 0.5);
                border-radius: 6px;
            }
            QLabel#helpTitle {
                color: #a0c0ff; /* Lighter blue for help title */
                font-size: 12px;
                font-weight: 600;
                margin-bottom: 5px;
            }
            QLabel#helpText {
                color: #c0c0c0;
                font-size: 11px;
                line-height: 1.3;
            }

            QPushButton#primaryMinimalButton, QPushButton#secondaryMinimalButton {
                border: none;
                border-radius: 4px;
                font-size: 12px;
                font-weight: 600;
                padding: 8px 15px; /* Smaller buttons */
                min-width: 70px;
            }
            QPushButton#primaryMinimalButton {
                background-color: #6a9cff; /* Primary blue */
                color: #ffffff;
            }
            QPushButton#primaryMinimalButton:hover {
                background-color: #5a8be0;
            }
            QPushButton#primaryMinimalButton:disabled {
                background-color: #303030;
                color: #707070;
            }
            QPushButton#secondaryMinimalButton {
                background-color: #303030; /* Darker secondary */
                color: #e0e0e0;
            }
            QPushButton#secondaryMinimalButton:hover {
                background-color: #404040;
            }
        """)


class ModernManageScansDialog(QDialog):
    """Enhanced dialog for managing existing scans."""

    def __init__(self, scans: List[Dict[str, str]], parent=None):
        super().__init__(parent)
        self.scans = scans.copy()
        self.setWindowTitle("Manage Chartink Scans")
        self.setModal(True)
        self.setFixedSize(720, 520)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._drag_pos = None
        self._setup_ui()
        self._apply_styles()
        self._populate_scans()

    def _setup_ui(self):
        # Main container for the "frosted" background effect
        main_container = QWidget()
        main_container.setObjectName("dialogContainer")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(main_container)

        container_layout = QVBoxLayout(main_container)
        container_layout.setContentsMargins(20, 16, 20, 20) # Reduced margins
        container_layout.setSpacing(15) # Reduced spacing

        # Header
        header_layout = QHBoxLayout()

        title_label = QLabel("Manage Scans")
        title_label.setObjectName("dialogTitle")

        self.add_btn = QPushButton("+ Add New")
        self.add_btn.setObjectName("addMinimalButton") # New object name
        self.add_btn.clicked.connect(self._add_scan)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(24, 24) # Smaller close button
        close_btn.clicked.connect(self.reject)

        header_layout.addWidget(title_label)
        header_layout.addWidget(self.add_btn)
        header_layout.addStretch()
        header_layout.addWidget(close_btn)

        container_layout.addLayout(header_layout)

        # Scans table
        self.scans_table = QTableWidget()
        self.scans_table.setObjectName("minimalTable") # New object name
        self.scans_table.setColumnCount(3)
        self.scans_table.setHorizontalHeaderLabels(["Scan Name", "Clause Preview", "Actions"])

        # Configure table
        header = self.scans_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        self.scans_table.verticalHeader().setVisible(False)
        self.scans_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.scans_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.scans_table.setAlternatingRowColors(True)
        self.scans_table.setShowGrid(False)

        container_layout.addWidget(self.scans_table)

        # Bottom buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10) # Reduced spacing

        info_label = QLabel(f"📊 {len(self.scans)} scans configured")
        info_label.setObjectName("infoLabel")

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondaryMinimalButton")
        cancel_btn.clicked.connect(self.reject)

        self.save_btn = QPushButton("Save Changes")
        self.save_btn.setObjectName("primaryMinimalButton")
        self.save_btn.clicked.connect(self.accept)

        button_layout.addWidget(info_label)
        button_layout.addStretch()
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(self.save_btn)

        container_layout.addLayout(button_layout)

        # Enable dragging
        main_container.mousePressEvent = self.mousePressEvent
        main_container.mouseMoveEvent = self.mouseMoveEvent
        main_container.mouseReleaseEvent = self.mouseReleaseEvent

    def _populate_scans(self):
        """Populate the table with current scans."""
        self.scans_table.setRowCount(len(self.scans))

        for row, scan in enumerate(self.scans):
            # Name
            name_item = QTableWidgetItem(scan.get("name", "Unnamed"))
            name_item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold)) # Smaller font
            self.scans_table.setItem(row, 0, name_item)

            # URL preview (truncated)
            url = scan.get("url", "")
            preview = url[:60] + "..." if len(url) > 60 else url
            preview_item = QTableWidgetItem(preview)
            preview_item.setFont(QFont("Consolas", 8)) # Smaller font
            self.scans_table.setItem(row, 1, preview_item)

            # Actions button
            delete_btn = QPushButton("🗑 Delete")
            delete_btn.setObjectName("deleteMinimalButton") # New object name
            delete_btn.clicked.connect(lambda checked, r=row: self._delete_scan(r))
            self.scans_table.setCellWidget(row, 2, delete_btn)

        # Adjust row heights
        for row in range(len(self.scans)):
            self.scans_table.setRowHeight(row, 36) # Reduced row height

    def _add_scan(self):
        """Add a new scan."""
        dialog = ModernAddScanDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            scan_data = dialog.get_scan_data()
            self.scans.append(scan_data)
            self._populate_scans()
            # Update info label
            info_label = self.findChild(QLabel, "infoLabel")
            if info_label:
                info_label.setText(f"📊 {len(self.scans)} scans configured")

    def _delete_scan(self, row: int):
        """Delete a scan at the given row."""
        if 0 <= row < len(self.scans):
            scan_name = self.scans[row].get("name", "Unnamed")

            # Custom message box
            reply = QMessageBox()
            reply.setWindowTitle("Confirm Deletion")
            reply.setText(f"Delete scan '{scan_name}'?")
            reply.setInformativeText("This action cannot be undone.")
            reply.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            reply.setDefaultButton(QMessageBox.StandardButton.No)
            reply.setStyleSheet("""
                QMessageBox {
                    background-color: #1a1a1a; /* Dark black background for QMessageBox */
                    color: #e0e0e0;
                    font-family: "Segoe UI", Arial, sans-serif;
                    font-size: 13px;
                }
                QMessageBox QLabel {
                    color: #e0e0e0;
                }
                QMessageBox QPushButton {
                    background-color: #303030; /* Dark buttons */
                    color: #e0e0e0;
                    border: 1px solid #404040;
                    border-radius: 4px;
                    padding: 6px 12px;
                    font-weight: 500;
                }
                QMessageBox QPushButton:hover {
                    background-color: #404040;
                }
                QMessageBox QPushButton:pressed {
                    background-color: #202020;
                }
            """)

            if reply.exec() == QMessageBox.StandardButton.Yes:
                del self.scans[row]
                self._populate_scans()
                # Update info label
                info_label = self.findChild(QLabel, "infoLabel")
                if info_label:
                    info_label.setText(f"📊 {len(self.scans)} scans configured")

    def get_scans(self) -> List[Dict[str, str]]:
        """Return the modified scans list."""
        return self.scans

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

    def _apply_styles(self):
        self.setStyleSheet("""
            QWidget#dialogContainer {
                background-color: rgba(0, 0, 0, 0.8); /* Dark black with some transparency for frosted effect */
                border: 1px solid rgba(60, 60, 60, 0.5); /* Subtle dark border */
                border-radius: 8px;
            }

            QLabel#dialogTitle {
                color: #ffffff;
                font-size: 16px;
                font-weight: 600;
            }

            QPushButton#addMinimalButton {
                background-color: #2e8b57; /* Sea green */
                color: #ffffff;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                font-weight: 600;
                padding: 6px 12px;
            }
            QPushButton#addMinimalButton:hover {
                background-color: #246b43;
            }
            QPushButton#addMinimalButton:pressed {
                background-color: #1e5a37;
            }


            QPushButton#closeButton {
                background-color: transparent;
                color: #a0a0a0;
                border: none;
                border-radius: 4px;
                font-size: 14px;
            }
            QPushButton#closeButton:hover {
                background-color: rgba(50, 50, 50, 0.5);
                color: #ffffff;
            }

            QTableWidget#minimalTable {
                background-color: #0d0d0d; /* Deep black table background */
                border: 1px solid #303030; /* Dark border */
                border-radius: 4px; /* Minimal rounding */
                gridline-color: #202020; /* Even darker grid lines */
                selection-background-color: rgba(74, 122, 191, 0.2); /* Softer blue selection with transparency */
                selection-color: #ffffff;
                font-size: 12px;
            }
            QTableWidget#minimalTable::item {
                padding: 5px 8px; /* Reduced padding */
                border-bottom: 1px solid #202020; /* Thin row separator */
                background-color: transparent;
            }
            QTableWidget#minimalTable::item:selected {
                background-color: rgba(74, 122, 191, 0.2); /* Softer blue selection with transparency */
                color: #ffffff;
                font-weight: 600;
            }
            QTableWidget#minimalTable::item:alternate {
                background-color: #1a1a1a; /* Very dark alternate row */
            }

            QHeaderView::section {
                background-color: #1a1a1a; /* Dark header background */
                color: #a0c0ff; /* Light blue header text */
                padding: 4px 8px; /* Reduced header padding */
                border: none;
                border-bottom: 1px solid #303030; /* Clearer bottom border */
                border-right: 1px solid #101010; /* Dark vertical separators */
                font-weight: 600;
                font-size: 11px;
            }
            QHeaderView::section:last {
                border-right: none;
            }
            QHeaderView::section:hover {
                background-color: #2a2a2a;
            }

            QPushButton#deleteMinimalButton {
                background-color: #cc4444; /* Red for delete */
                color: #ffffff;
                border: none;
                border-radius: 3px;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 8px;
            }
            QPushButton#deleteMinimalButton:hover {
                background-color: #a33333;
            }
            QPushButton#deleteMinimalButton:pressed {
                background-color: #8a2a2a;
            }

            QLabel#infoLabel {
                color: #a0c0ff;
                font-size: 12px;
                font-weight: 500;
            }

            QPushButton#primaryMinimalButton, QPushButton#secondaryMinimalButton {
                border: none;
                border-radius: 4px;
                font-size: 12px;
                font-weight: 600;
                padding: 8px 15px;
                min-width: 70px;
            }
            QPushButton#primaryMinimalButton {
                background-color: #6a9cff;
                color: #ffffff;
            }
            QPushButton#primaryMinimalButton:hover {
                background-color: #5a8be0;
            }
            QPushButton#primaryMinimalButton:disabled {
                background-color: #303030;
                color: #707070;
            }
            QPushButton#secondaryMinimalButton {
                background-color: #303030;
                color: #e0e0e0;
            }
            QPushButton#secondaryMinimalButton:hover {
                background-color: #404040;
            }
        """)


class ScanWorker(QThread):
    """Worker thread for Chartink scans (unchanged)."""
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
    """Enhanced scanner table with improved dropdown styling."""
    symbol_selected = Signal(str)
    subscribe_tokens_requested = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scans = self._load_scans()
        self.scan_thread: ScanWorker = None
        self._instrument_map: Dict[str, Dict] = {}
        self._symbol_data: Dict[str, Dict] = {}
        self._symbol_to_row: Dict[str, int] = {}
        self._kite_client = None

        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self._fetch_quote_data)
        self._update_timer.setInterval(10000)

        self._setup_ui()
        self._apply_enhanced_styles()

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
        if self._kite_client and self._symbol_data:
            self._update_timer.start()

    def _fetch_quote_data(self):
        """Fetch quote data from Kite API for current symbols."""
        if not self._kite_client or not self._symbol_data:
            return

        try:
            tokens = [data.get('instrument_token') for data in self._symbol_data.values()
                      if data.get('instrument_token')]

            if not tokens:
                return

            quotes = self._kite_client.quote(tokens)

            for symbol, data in self._symbol_data.items():
                token = data.get('instrument_token')
                if token and str(token) in quotes:
                    quote = quotes[str(token)]

                    if 'last_price' in quote:
                        data['ltp'] = quote['last_price']

                    volume = 0
                    if 'volume' in quote:
                        volume = quote['volume']
                    elif 'total_buy_quantity' in quote and 'total_sell_quantity' in quote:
                        volume = quote['total_buy_quantity'] + quote['total_sell_quantity']
                    elif 'day_high' in quote and 'day_low' in quote:
                        ohlc = quote.get('ohlc', {})
                        volume = ohlc.get('volume', 0)

                    data['volume'] = volume

                    ohlc = quote.get('ohlc', {})
                    close_price = ohlc.get('close', 0.0)
                    if close_price > 0:
                        data['close_price'] = close_price
                        ltp = data.get('ltp', 0.0)
                        change_pct = ((ltp - close_price) / close_price) * 100
                        data['change_pct'] = change_pct

                    if symbol in self._symbol_to_row:
                        row = self._symbol_to_row[symbol]
                        self._update_row_data(row, data)

            logger.debug(f"Updated quote data for {len(tokens)} symbols")

        except Exception as e:
            logger.error(f"Error fetching quote data: {e}")

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
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.table.setFocus()

    def _create_header(self) -> QWidget:
        """Creates the enhanced header with improved styling."""
        header_container = QWidget()
        header_container.setObjectName("headerContainer")

        header_layout = QHBoxLayout(header_container)
        header_layout.setContentsMargins(6, 6, 6, 6) # Further reduced margins
        header_layout.setSpacing(8) # Further reduced spacing

        # Scan label with icon
        scan_label = QLabel("SCAN:")
        scan_label.setObjectName("scanLabel")
        scan_label.setFixedWidth(40) # Adjusted width
        header_layout.addWidget(scan_label)

        # Enhanced dropdown
        self.scan_dropdown = QComboBox()
        self.scan_dropdown.setObjectName("minimalDropdown") # New object name
        self.scan_dropdown.setMinimumHeight(28) # Reduced height
        self.scan_dropdown.currentIndexChanged.connect(self._on_scan_selection_changed)
        header_layout.addWidget(self.scan_dropdown, 1)

        # Settings button with modern styling
        self.manage_btn = QPushButton("⚙ Manage")
        self.manage_btn.setObjectName("settingsMinimalButton") # New object name
        self.manage_btn.setToolTip("Manage Scans")
        self.manage_btn.setFixedSize(70, 28) # Adjusted size
        self.manage_btn.clicked.connect(self._manage_scans)
        header_layout.addWidget(self.manage_btn)

        self._update_scan_dropdown()
        return header_container

    def _update_scan_dropdown(self):
        """Update the scan dropdown with current scans."""
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
            self.manage_btn.setEnabled(True)

        self.scan_dropdown.blockSignals(False)

    def _configure_table(self):
        """Configures the properties and headers of the table."""
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Symbol", "Price", "Volume", "%Chg"])

        self.table.horizontalHeader().setVisible(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)

    def set_instrument_map(self, instrument_map: Dict[str, Dict]):
        """Receives the master instrument map for data lookups."""
        self._instrument_map = instrument_map
        self._update_existing_symbols()

    def _update_existing_symbols(self):
        """Update existing symbols with instrument data and start subscriptions."""
        if not self._instrument_map:
            return

        tokens_to_subscribe = []

        for symbol in self._symbol_data.keys():
            if symbol in self._instrument_map:
                instrument = self._instrument_map[symbol]
                token = instrument.get('instrument_token')
                if token:
                    self._symbol_data[symbol].update({
                        "instrument_token": token,
                        "close_price": instrument.get('ohlc', {}).get('close', 0.0),
                        "ltp": instrument.get('last_price', 0.0),
                    })
                    tokens_to_subscribe.append(token)
            else:
                logger.warning(f"Symbol {symbol} not found in instrument map")


        if tokens_to_subscribe:
            self.subscribe_tokens_requested.emit(tokens_to_subscribe)

        if self._kite_client and tokens_to_subscribe:
            self._update_timer.start()

        self._update_table_display()

    @Slot(list)
    def update_data(self, ticks: List[Dict]):
        """Updates LTP and change% from WebSocket ticks."""
        updated_symbols = set()

        for tick in ticks:
            token = tick.get('instrument_token')
            ltp = tick.get('last_price')

            volume = tick.get('volume', 0)
            if volume == 0:
                volume = tick.get('total_buy_quantity', 0) + tick.get('total_sell_quantity', 0)
            if volume == 0:
                volume = tick.get('day_volume', 0)

            for symbol, data in self._symbol_data.items():
                if data.get('instrument_token') == token and ltp is not None:
                    old_ltp = data.get('ltp', 0.0)
                    data['ltp'] = ltp

                    if volume > 0:
                        data['volume'] = volume

                    close_price = data.get('close_price', 0.0)
                    if close_price <= 0:
                        ohlc = tick.get('ohlc', {})
                        close_price = ohlc.get('close', 0.0)
                        if close_price > 0:
                            data['close_price'] = close_price

                    if close_price > 0:
                        change_pct = ((ltp - close_price) / close_price) * 100
                        data['change_pct'] = change_pct

                    updated_symbols.add(symbol)
                    break

        for symbol in updated_symbols:
            if symbol in self._symbol_to_row:
                row = self._symbol_to_row[symbol]
                self._update_row_data(row, self._symbol_data[symbol])

    def _update_row_data(self, row: int, data: Dict):
        """Updates the text and color for a single row."""
        if row >= self.table.rowCount():
            return

        symbol_item = self.table.item(row, 0)
        if not symbol_item:
            symbol_item = QTableWidgetItem()
            self.table.setItem(row, 0, symbol_item)
        symbol_item.setText(data['symbol'])

        ltp = data.get('ltp', 0.0)
        ltp_item = self.table.item(row, 1)
        if not ltp_item:
            ltp_item = QTableWidgetItem()
            self.table.setItem(row, 1, ltp_item)
        ltp_item.setText(f"{ltp:.2f}" if ltp > 0 else "-")

        volume = data.get('volume', 0)
        volume_item = self.table.item(row, 2)
        if not volume_item:
            volume_item = QTableWidgetItem()
            self.table.setItem(row, 2, volume_item)
        if volume >= 1000000:
            volume_text = f"{volume / 1000000:.1f}M"
        elif volume >= 1000:
            volume_text = f"{volume / 1000:.0f}K"
        elif volume > 0:
            volume_text = str(volume)
        else:
            volume_text = "-"
        volume_item.setText(volume_text)

        change_pct = data.get('change_pct', 0.0)
        change_pct_item = self.table.item(row, 3)
        if not change_pct_item:
            change_pct_item = QTableWidgetItem()
            self.table.setItem(row, 3, change_pct_item)
        change_pct_item.setText(f"{change_pct:.2f}%" if abs(change_pct) > 0.01 else "-")

        # TC2000-like colors
        profit_color = QColor(60, 179, 113)  # Medium Sea Green
        loss_color = QColor(220, 20, 60)    # Crimson
        neutral_color = QColor(169, 169, 169) # DarkGray

        color = profit_color if change_pct > 0 else (loss_color if change_pct < 0 else neutral_color)

        ltp_item.setForeground(color)
        change_pct_item.setForeground(color)         # Force selection background color using QBrush
        ltp_item.setBackground(QBrush(QColor(30, 30, 30)))
        change_pct_item.setBackground(QBrush(QColor(30, 30, 30)))
        volume_item.setBackground(QBrush(QColor(30, 30, 30)))
        # Apply color coding to %Chg
        volume_item.setForeground(neutral_color) # Volume usually neutral

        # Alignments
        symbol_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # Center alignment for LTP, Volume, %Chg
        ltp_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        volume_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        change_pct_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)


    @Slot(list)
    def _on_scan_complete(self, symbols: List[str]):
        """Handle scan completion and setup new symbol data."""
        self._symbol_data.clear()
        self._symbol_to_row.clear()
        self.table.setRowCount(0)

        if not symbols:
            self.table.insertRow(0)
            item = QTableWidgetItem("No symbols found")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(0, 0, item)
            for col in range(1, 4):
                self.table.setItem(0, col, QTableWidgetItem(""))
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

                if self._kite_client:
                    self._update_timer.start()
                    QTimer.singleShot(2000, self._fetch_quote_data) # Fetch initial data quicker


                if self.table.rowCount() > 0:
                    index = self.table.model().index(0, 0)
                    self.table.selectionModel().select(
                        index,
                        QItemSelectionModel.Select | QItemSelectionModel.Rows
                    )
                    self.table.setCurrentCell(0, 0)
                    self.table.setFocus()

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

            # Create items if they don't exist
            for col in range(4):
                if not self.table.item(row, col):
                    self.table.setItem(row, col, QTableWidgetItem())

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
        for col in range(1, 4):
            self.table.setItem(0, col, QTableWidgetItem(""))

        self.scan_dropdown.setEnabled(True)
        self.manage_btn.setEnabled(True)

    def _on_scan_selection_changed(self):
        """Handles scan selection changes and saves the selection."""
        if self.scan_dropdown.signalsBlocked():
            return

        current_index = self.scan_dropdown.currentIndex()
        self._save_last_selected_scan(current_index)
        self._update_timer.stop()
        self._run_current_scan()

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
        """Open the enhanced manage scans dialog."""
        dialog = ModernManageScansDialog(self.scans, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.scans = dialog.get_scans()
            self._save_scans()

            self._update_timer.stop()

            self.scan_dropdown.blockSignals(True)
            current_index = self.scan_dropdown.currentIndex()
            self._update_scan_dropdown()

            if current_index < self.scan_dropdown.count() and current_index >= 0:
                self.scan_dropdown.setCurrentIndex(current_index)
            else:
                self.scan_dropdown.setCurrentIndex(0)
            self.scan_dropdown.blockSignals(False)

            if not self.scans:
                self.table.setRowCount(0)
                self.table.insertRow(0)
                item = QTableWidgetItem("No scans configured. Click '⚙ Manage' to add a scan.")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(0, 0, item)
                for col in range(1, 4):
                    self.table.setItem(0, col, QTableWidgetItem(""))
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
            self.table.setRowCount(0)
            self.table.insertRow(0)
            item = QTableWidgetItem("Invalid Scan URL.")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(0, 0, item)
            for col in range(1, 4):
                self.table.setItem(0, col, QTableWidgetItem(""))
            return

        logger.info(f"Running Chartink scan: {selected_scan.get('name', 'Unnamed')} - {selected_scan_url}")

        self._update_timer.stop()

        self.scan_dropdown.setEnabled(False)
        self.manage_btn.setEnabled(False)

        self.table.setRowCount(0)
        self.table.insertRow(0)
        item = QTableWidgetItem("🔄 Loading scan results...")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.table.setItem(0, 0, item)
        for col in range(1, 4):
            self.table.setItem(0, col, QTableWidgetItem(""))

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
                if symbol_text and not symbol_text.startswith(("Error:", "🔄", "No symbols", "No scans")):
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

    def closeEvent(self, event):
        """Clean up when widget is closed."""
        self._update_timer.stop()
        if self.scan_thread and self.scan_thread.isRunning():
            self.scan_thread.terminate()
            self.scan_thread.wait(2000)
        super().closeEvent(event)

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

    def _apply_enhanced_styles(self):
        """Applies enhanced minimal dark styling."""
        self.setStyleSheet("""
            QWidget {
                background-color: #0a0a0a; /* Deep black background */
                color: #e0e0e0; /* Light gray text */
                font-family: "Segoe UI", Arial, sans-serif; /* Professional font */
                font-size: 13px;
            }

            /* Header Container */
            QWidget#headerContainer {
                background-color: #1a1a1a; /* Slightly lighter header background */
                border-bottom: 1px solid #303030; /* Clean dark separator */
                padding: 5px; /* Reduced padding */
            }

            /* Scan Label */
            QLabel#scanLabel {
                color: #a0c0ff; /* Light blue for labels */
                font-weight: 600;
                font-size: 11px;
                /* Removed padding */
            }

            /* Minimal Dropdown */
            QComboBox#minimalDropdown {
                background-color: #1a1a1a; /* Dark dropdown background */
                border: 1px solid #303030;
                color: #ffffff;
                padding: 3px 6px; /* Reduced padding */
                border-radius: 2px; /* Minimal rounding */
                font-size: 12px;
            }
            QComboBox#minimalDropdown:hover {
                border-color: #505050;
            }
            QComboBox#minimalDropdown:focus {
                border-color: #6a9cff; /* Highlight with professional blue */
                outline: none;
            }
            QComboBox#minimalDropdown:disabled {
                background-color: #050505;
                color: #606060;
                border-color: #202020;
            }

            QComboBox#minimalDropdown::drop-down {
                border: none;
                width: 18px; /* Smaller drop-down area */
            }
            QComboBox#minimalDropdown::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 4px solid #808080; /* Muted arrow color */
            }
            QComboBox#minimalDropdown::down-arrow:hover {
                border-top-color: #ffffff;
            }

            /* Dropdown List (QAbstractItemView) for Minimal Dropdown */
            QComboBox#minimalDropdown QAbstractItemView {
                background-color: #1a1a1a; /* Dark background for dropdown list */
                border: 1px solid #6a9cff; /* Professional blue border */
                border-radius: 2px;
                color: #ffffff;
                selection-background-color: rgba(74, 122, 191, 0.2); /* Softer blue for dropdown selection with transparency */
                selection-color: #ffffff;
                padding: 1px; /* Minimal padding */
                outline: none;
            }
            QComboBox#minimalDropdown QAbstractItemView::item {
                padding: 5px 8px;
                border: none;
                border-radius: 1px;
                margin: 0px 1px; /* Very tight margins */
                font-size: 12px;
            }
            QComboBox#minimalDropdown QAbstractItemView::item:hover {
                background-color: #2a2a2a;
            }
            QComboBox#minimalDropdown QAbstractItemView::item:selected {
                background-color: rgba(74, 122, 191, 0.2); /* Softer blue for dropdown selection with transparency */
                color: #ffffff;
            }

            /* Scrollbars for Dropdown List - Invisible */
            QComboBox#minimalDropdown QAbstractItemView QScrollBar:vertical {
                width: 0px; /* Make invisible */
            }
            QComboBox#minimalDropdown QAbstractItemView QScrollBar::handle:vertical {
                width: 0px; /* Make invisible */
            }
            QComboBox#minimalDropdown QAbstractItemView QScrollBar::add-line:vertical,
            QComboBox#minimalDropdown QAbstractItemView QScrollBar::sub-line:vertical {
                height: 0px; /* Make invisible */
            }

            /* Settings Button (Minimal) */
            QPushButton#settingsMinimalButton {
                background-color: #2a2a2a; /* Dark button background */
                color: #a0c0ff; /* Text color */
                font-size: 11px;
                font-weight: 500;
                border-radius: 3px;
                border: 1px solid #303030;
                padding: 3px 7px;
            }
            QPushButton#settingsMinimalButton:hover {
                background-color: #3a3a3a;
                border-color: #505050;
            }
            QPushButton#settingsMinimalButton:pressed {
                background-color: #1a1a1a;
                border-color: #404040;
            }
            QPushButton#settingsMinimalButton:disabled {
                background-color: #050505;
                color: #606060;
                border-color: #202020;
            }

            /* Table Styling */
            QTableWidget {
                border: 1px solid #202020; /* Subtle dark border for the table */
                gridline-color: #151515; /* Almost invisible grid lines */
                font-size: 12px;
                background-color: #0d0d0d; /* Deep black table background */
                selection-background-color: rgba(74, 122, 191, 0.2); /* Softer blue selection with transparency */
                selection-color: #ffffff;
                border-radius: 0px; /* No rounding */
            }
            QTableWidget::item {
                padding: 5px 8px; /* Consistent padding */
                border-bottom: 1px solid #1a1a1a; /* Thin row separator */
                background-color: transparent;
                color: #e0e0e0;
            }
            QTableWidget::item:selected {
                background-color: rgba(74, 122, 191, 0.2); /* Softer blue selection with transparency */
                font-weight: 600;
            }
            QTableWidget::item:alternate {
                background-color: #121212; /* Very dark alternate row */
            }

            QHeaderView::section {
                background-color: #1a1a1a; /* Header background */
                color: #a0c0ff; /* Header text color */
                padding: 3px 10px; /* Further reduced header padding */
                border: none;
                border-bottom: 1px solid #303030; /* Clear header bottom border */
                border-right: 1px solid #101010; /* Dark vertical header separators */
                font-weight: 600;
                font-size: 11px;
            }
            QHeaderView::section:last {
                border-right: none;
            }
            QHeaderView::section:hover {
                background-color: #2a2a2a; /* Subtle hover for headers */
            }

            /* Table Scrollbars - Invisible */
            QScrollBar:vertical {
                width: 0px; /* Make invisible */
            }
            QScrollBar::handle:vertical {
                width: 0px; /* Make invisible */
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px; /* Make invisible */
            }
            QScrollBar:horizontal {
                height: 0px; /* Make invisible */
            }
            QScrollBar::handle:horizontal {
                height: 0px; /* Make invisible */
            }
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {
                width: 0px; /* Make invisible */
            }
        """)