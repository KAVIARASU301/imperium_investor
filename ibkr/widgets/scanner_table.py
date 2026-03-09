# FIXED scanner_table.py with proper row selection and highlighting
import logging
import json
import os
import requests
from bs4 import BeautifulSoup as bs
from typing import List, Dict, Optional

from PySide6.QtCore import Signal, Slot, Qt, QThread
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QPushButton, QHBoxLayout, QLabel, QComboBox, QMessageBox,
    QDialog, QLineEdit, QFormLayout, QGroupBox, QFrame, QTextEdit
)
from PySide6.QtGui import QColor, QFont, QBrush, QCursor
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
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._drag_pos = None
        self._setup_ui()
        self._apply_styles()

        # ADDED: Auto-focus to the Scan Name input field
        self.name_input.setFocus()
        self.name_input.selectAll()  # Optional: select all text if any exists

    def _setup_ui(self):
        # Main container for the "frosted" background effect
        main_container = QWidget()
        main_container.setObjectName("dialogContainer")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(main_container)

        container_layout = QVBoxLayout(main_container)
        container_layout.setContentsMargins(20, 16, 20, 20)
        container_layout.setSpacing(15)

        # Header with title and close button
        header_layout = QHBoxLayout()

        title_label = QLabel("Add New Scan")
        title_label.setObjectName("dialogTitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.reject)

        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(close_btn)

        container_layout.addLayout(header_layout)

        # Form section
        form_group = QGroupBox("Scan Configuration")
        form_group.setObjectName("formGroup")
        form_layout = QVBoxLayout(form_group)
        form_layout.setSpacing(12)

        # Scan name
        name_label = QLabel("Scan Name")
        name_label.setObjectName("fieldLabel")
        self.name_input = QLineEdit()
        self.name_input.setObjectName("minimalInput")
        self.name_input.setPlaceholderText("e.g., 'Breakout Stocks', 'High Volume Gainers'")

        form_layout.addWidget(name_label)
        form_layout.addWidget(self.name_input)

        # Scan clause
        clause_label = QLabel("Scan Clause")
        clause_label.setObjectName("fieldLabel")
        self.url_input = QTextEdit()
        self.url_input.setObjectName("minimalTextArea")
        self.url_input.setPlaceholderText("Paste your Chartink scan clause here...")
        self.url_input.setMaximumHeight(70)

        form_layout.addWidget(clause_label)
        form_layout.addWidget(self.url_input)

        container_layout.addWidget(form_group)

        # Help section
        help_frame = QFrame()
        help_frame.setObjectName("helpFrame")
        help_layout = QVBoxLayout(help_frame)
        help_layout.setContentsMargins(12, 8, 12, 8)

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
        button_layout.setSpacing(10)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondaryMinimalButton")
        cancel_btn.clicked.connect(self.reject)

        self.save_btn = QPushButton("Add Scan")
        self.save_btn.setObjectName("primaryMinimalButton")
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
        """Enhanced mouse press event for reliable dragging."""
        if event.button() == Qt.LeftButton:
            # Calculate drag position relative to dialog
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Enhanced mouse move event for smooth dragging."""
        if (event.buttons() == Qt.LeftButton and
                self._drag_pos is not None and
                hasattr(self, '_drag_pos')):
            # Move dialog to new position
            new_pos = event.globalPosition().toPoint() - self._drag_pos
            self.move(new_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Enhanced mouse release event to complete drag operation."""
        if event.button() == Qt.LeftButton:
            self._drag_pos = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def showEvent(self, event):
        """Override showEvent to ensure focus is set when dialog appears."""
        super().showEvent(event)
        # ADDED: Ensure focus is set when dialog becomes visible
        self.name_input.setFocus()
        self.name_input.selectAll()

    def _apply_styles(self):
        self.setStyleSheet("""
            QWidget#dialogContainer {
                background-color: rgba(0, 0, 0, 0.8);
                border: 1px solid rgba(60, 60, 60, 0.5);
                border-radius: 8px;
            }

            QLabel#dialogTitle {
                color: #ffffff;
                font-size: 16px;
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
                background-color: rgba(50, 50, 50, 0.5);
                color: #ffffff;
            }

            QGroupBox#formGroup {
                background-color: rgba(20, 20, 20, 0.7);
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
                color: #6a9cff;
            }

            QLabel#fieldLabel {
                color: #e0e0e0;
                font-size: 12px;
                font-weight: 500;
                margin-bottom: 2px;
            }

            QLineEdit#minimalInput, QTextEdit#minimalTextArea {
                background-color: #0d0d0d;
                border: 1px solid #303030;
                border-radius: 3px;
                color: #ffffff;
                padding: 6px 8px;
                font-size: 13px;
                selection-background-color: #6a9cff;
                font-family: "Segoe UI", sans-serif;
            }
            QTextEdit#minimalTextArea {
                font-family: "Consolas", "Monaco", monospace;
            }
            QLineEdit#minimalInput:focus, QTextEdit#minimalTextArea:focus {
                border-color: #6a9cff;
                background-color: #1a1a1a;
            }
            QLineEdit#minimalInput::placeholder, QTextEdit#minimalTextArea::placeholder {
                color: #808080;
            }

            QFrame#helpFrame {
                background-color: rgba(10, 10, 20, 0.7);
                border: 1px solid rgba(40, 40, 60, 0.5);
                border-radius: 6px;
            }
            QLabel#helpTitle {
                color: #a0c0ff;
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
        # Main container
        main_container = QWidget()
        main_container.setObjectName("dialogContainer")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(main_container)

        container_layout = QVBoxLayout(main_container)
        container_layout.setContentsMargins(20, 16, 20, 20)
        container_layout.setSpacing(15)

        # Header
        header_layout = QHBoxLayout()

        title_label = QLabel("Manage Scans")
        title_label.setObjectName("dialogTitle")

        self.add_btn = QPushButton("+ Add New")
        self.add_btn.setObjectName("addMinimalButton")
        self.add_btn.clicked.connect(self._add_scan)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.reject)

        header_layout.addWidget(title_label)
        header_layout.addWidget(self.add_btn)
        header_layout.addStretch()
        header_layout.addWidget(close_btn)

        container_layout.addLayout(header_layout)

        # Scans table
        self.scans_table = QTableWidget()
        self.scans_table.setObjectName("minimalTable")
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
        button_layout.setSpacing(10)

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

        # ENHANCED: Enable dragging on the main container and title area
        main_container.mousePressEvent = self.mousePressEvent
        main_container.mouseMoveEvent = self.mouseMoveEvent
        main_container.mouseReleaseEvent = self.mouseReleaseEvent

        # ADDITIONAL: Make title label draggable too
        title_label.mousePressEvent = self.mousePressEvent
        title_label.mouseMoveEvent = self.mouseMoveEvent
        title_label.mouseReleaseEvent = self.mouseReleaseEvent

    def _populate_scans(self):
        """Populate the table with current scans."""
        self.scans_table.setRowCount(len(self.scans))

        for row, scan in enumerate(self.scans):
            # Name
            name_item = QTableWidgetItem(scan.get("name", "Unnamed"))
            name_item.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            self.scans_table.setItem(row, 0, name_item)

            # URL preview (truncated)
            url = scan.get("url", "")
            preview = url[:60] + "..." if len(url) > 60 else url
            preview_item = QTableWidgetItem(preview)
            preview_item.setFont(QFont("Consolas", 8))
            self.scans_table.setItem(row, 1, preview_item)

            # Actions button
            delete_btn = QPushButton("🗑")  # Just the delete icon, no text
            delete_btn.setObjectName("deleteMinimalButton")
            delete_btn.setFixedSize(24, 24)  # Small square button
            delete_btn.setToolTip("Delete this scan")  # Helpful tooltip
            delete_btn.clicked.connect(lambda checked, r=row: self._delete_scan(r))
            self.scans_table.setCellWidget(row, 2, delete_btn)

        # Adjust row heights
        for row in range(len(self.scans)):
            self.scans_table.setRowHeight(row, 36)

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

            reply = QMessageBox()
            reply.setWindowTitle("Confirm Deletion")
            reply.setText(f"Delete scan '{scan_name}'?")
            reply.setInformativeText("This action cannot be undone.")
            reply.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            reply.setDefaultButton(QMessageBox.StandardButton.No)

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
                background-color: rgba(0, 0, 0, 0.8);
                border: 1px solid rgba(60, 60, 60, 0.5);
                border-radius: 8px;
            }

            QLabel#dialogTitle {
                color: #ffffff;
                font-size: 16px;
                font-weight: 600;
            }

            QPushButton#addMinimalButton {
                background-color: #2e8b57;
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
                background-color: #0d0d0d;
                border: 1px solid #303030;
                border-radius: 4px;
                gridline-color: #202020;
                selection-background-color: rgba(74, 122, 191, 0.2);
                selection-color: #ffffff;
                font-size: 13px;
            }
            QTableWidget#minimalTable::item {
                padding: 1px 1px;
                border-bottom: 1px solid #202020;
                background-color: transparent;
            }
            QTableWidget#minimalTable::item:selected {
                background-color: rgba(74, 122, 191, 0.2);
                color: #ffffff;
                font-weight: 600;
            }
            QTableWidget#minimalTable::item:alternate {
                background-color: #1a1a1a;
            }

            QHeaderView::section {
                background-color: #1a1a1a;
                color: #a0c0ff;
                padding: 4px 8px;
                border: none;
                border-bottom: 1px solid #303030;
                border-right: 1px solid #101010;
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
                background-color: #cc4444;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: normal;
                padding: 0px;
                margin: 0px;
            }
            QPushButton#deleteMinimalButton:hover {
                background-color: #ff6666;
                border: 1px solid #ff8888;
            }
            QPushButton#deleteMinimalButton:pressed {
                background-color: #aa3333;
                border: 1px solid #883333;
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
            QPushButton#secondaryMinimalButton {
                background-color: #303030;
                color: #e0e0e0;
            }
            QPushButton#secondaryMinimalButton:hover {
                background-color: #404040;
            }
        """)


class ScanWorker(QThread):
    """Worker thread for Chartink scans - returns complete EOD data."""
    scan_completed = Signal(list)  # Emits list of complete symbol data
    scan_error = Signal(str)

    def __init__(self, scan_url: str):
        super().__init__()
        self.scan_url = scan_url

    def run(self):
        try:
            clause = self.scan_url.strip()
            if not clause:
                raise Exception("No scan clause provided")

            # Use the direct XHR method
            with requests.Session() as s:
                # Step 1: Get the main page to establish session
                main_page = s.get('https://chartink.com/', timeout=30)
                main_page.raise_for_status()

                # Step 2: Get the screener page to get CSRF token
                screener_page = s.get('https://chartink.com/screener', timeout=30)
                screener_page.raise_for_status()

                # Extract CSRF token
                csrf_token = None
                if 'csrf-token' in screener_page.text:
                    import re
                    csrf_match = re.search(r'<meta name="csrf-token" content="([^"]*)"', screener_page.text)
                    if csrf_match:
                        csrf_token = csrf_match.group(1)
                    else:
                        csrf_match = re.search(r'csrf-token["\']?\s*:\s*["\']([^"\']+)["\']', screener_page.text)
                        if csrf_match:
                            csrf_token = csrf_match.group(1)

                if not csrf_token:
                    raise Exception("Could not extract CSRF token from screener page")

                # Step 3: Prepare the direct XHR request
                xhr_headers = {
                    'Accept': 'application/json, text/javascript, */*; q=0.01',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Cache-Control': 'no-cache',
                    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                    'Origin': 'https://chartink.com',
                    'Pragma': 'no-cache',
                    'Referer': 'https://chartink.com/screener',
                    'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                    'Sec-Ch-Ua-Mobile': '?0',
                    'Sec-Ch-Ua-Platform': '"Linux"',
                    'Sec-Fetch-Dest': 'empty',
                    'Sec-Fetch-Mode': 'cors',
                    'Sec-Fetch-Site': 'same-origin',
                    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'X-Csrf-Token': csrf_token,
                    'X-Requested-With': 'XMLHttpRequest'
                }

                s.headers.update(xhr_headers)

                # Step 4: Make the request
                payload = {'scan_clause': clause}
                process_url = 'https://chartink.com/screener/process'
                response = s.post(process_url, data=payload, timeout=60)
                response.raise_for_status()

                # Step 5: Parse response and extract complete data
                data = response.json()
                results = data.get("data", [])

                scan_results = []
                for row in results:
                    if isinstance(row, dict) and "nsecode" in row:
                        # Extract all EOD data from Chartink
                        symbol_data = {
                            'symbol': row.get('nsecode', ''),
                            'name': row.get('name', row.get('nsecode', '')),
                            'price': float(row.get('close', 0.0)),  # EOD closing price
                            'change_pct': float(row.get('per_chg', 0.0)),  # % change from Chartink
                            'volume': int(row.get('volume', 0)),  # Volume from Chartink
                            'bsecode': row.get('bsecode'),
                            'sr': row.get('sr', 0),
                            '_raw_data': row  # Store complete raw data for debugging
                        }
                        scan_results.append(symbol_data)

                logger.info(f"EOD Scan completed: {len(scan_results)} symbols with complete data")
                self.scan_completed.emit(scan_results)

        except Exception as e:
            logger.error(f"ScanWorker failed: {e}", exc_info=True)
            self.scan_error.emit(str(e))


class ChartinkScannerTable(QWidget):
    """FIXED EOD scanner table with proper row selection and highlighting."""
    symbol_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scans = self._load_scans()
        self.scan_thread: ScanWorker = None
        self._symbol_data: Dict[str, Dict] = {}
        self._symbol_to_row: Dict[str, int] = {}
        self._current_symbol_index = 0  # Track current symbol for spacebar navigation

        self._setup_ui()
        self._apply_enhanced_styles()

        if self.scans:
            last_selected = self._load_last_selected_scan()
            if 0 <= last_selected < len(self.scans):
                self.scan_dropdown.blockSignals(True)
                self.scan_dropdown.setCurrentIndex(last_selected)
                self.scan_dropdown.blockSignals(False)
            self._run_current_scan()

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

        # Add focus out event to clear selection (from positions table pattern)
        self.table.focusOutEvent = self._on_table_focus_out

        # ADDED: Setup spacebar shortcut for symbol navigation
        self._setup_keyboard_shortcuts()

    def _setup_keyboard_shortcuts(self):
        """Setup keyboard shortcuts for scanner table."""
        # NOTE: Global spacebar shortcuts are now handled by the main window
        # for context-aware navigation. This method is kept for potential
        # future scanner-specific shortcuts.
        logger.info("Scanner table ready for context-aware navigation")

    def _next_symbol(self):
        """Navigate to the next symbol in the scanner list."""
        symbols = self.get_current_symbols()
        if not symbols:
            return

        # Increment to next symbol (wrap around to beginning)
        self._current_symbol_index = (self._current_symbol_index + 1) % len(symbols)
        self._select_symbol_at_index(self._current_symbol_index)

    def _previous_symbol(self):
        """Navigate to the previous symbol in the scanner list."""
        symbols = self.get_current_symbols()
        if not symbols:
            return

        # Decrement to previous symbol (wrap around to end)
        self._current_symbol_index = (self._current_symbol_index - 1) % len(symbols)
        self._select_symbol_at_index(self._current_symbol_index)

    def _select_symbol_at_index(self, index: int):
        """Select symbol at given index and emit selection signal."""
        symbols = self.get_current_symbols()
        if 0 <= index < len(symbols) and index < self.table.rowCount():
            # Update table selection
            self.table.selectRow(index)
            self.table.setCurrentCell(index, 0)

            # Get symbol and emit selection
            symbol = symbols[index]
            self.symbol_selected.emit(symbol)

            # Update current index
            self._current_symbol_index = index

            logger.debug(f"Scanner: Selected symbol {symbol} at index {index}")

    def _on_table_focus_out(self, event):
        """Clear selection when table loses focus (from positions table)."""
        try:
            self.table.clearSelection()
            # Call the original focusOutEvent if it exists
            if hasattr(QTableWidget, 'focusOutEvent'):
                QTableWidget.focusOutEvent(self.table, event)
        except Exception as e:
            logger.debug(f"Error clearing selection on focus out: {e}")

    def _create_header(self) -> QWidget:
        """Creates the header with scan selection."""
        header_container = QWidget()
        header_container.setObjectName("headerContainer")

        header_layout = QHBoxLayout(header_container)
        header_layout.setContentsMargins(6, 6, 6, 6)
        header_layout.setSpacing(8)

        # Scan label
        scan_label = QLabel("SCAN:")
        scan_label.setObjectName("scanLabel")
        scan_label.setStyleSheet("QLabel#scanLabel { background-color: transparent; }")
        scan_label.setFixedWidth(40)
        header_layout.addWidget(scan_label)

        # Dropdown
        self.scan_dropdown = QComboBox()
        self.scan_dropdown.setObjectName("minimalDropdown")
        self.scan_dropdown.setMinimumHeight(28)
        self.scan_dropdown.currentIndexChanged.connect(self._on_scan_selection_changed)
        header_layout.addWidget(self.scan_dropdown, 1)

        # Settings button
        self.manage_btn = QPushButton("⚙ Manage")
        self.manage_btn.setObjectName("settingsMinimalButton")
        self.manage_btn.setToolTip("Manage Scans")
        self.manage_btn.setFixedSize(70, 28)
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
        """FIXED table configuration with proper row selection."""
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Symbol", "Price", "Vol", "%Chg"])

        self.table.horizontalHeader().setVisible(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        self.table.verticalHeader().setVisible(False)

        # FIXED: Use proper selection behavior - SelectRows not individual cells
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)

        # FIXED: Add focus policy for better behavior (from positions table)
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _update_row_data(self, row: int, data: Dict):
        """Updates the display for a single row with EOD data."""
        if row >= self.table.rowCount():
            return

        # Symbol column
        symbol_item = self.table.item(row, 0)
        if not symbol_item:
            symbol_item = QTableWidgetItem()
            self.table.setItem(row, 0, symbol_item)
        symbol_item.setText(data['symbol'])

        # Price column (EOD closing price)
        price = data.get('price', 0.0)
        price_item = self.table.item(row, 1)
        if not price_item:
            price_item = QTableWidgetItem()
            self.table.setItem(row, 1, price_item)
        price_item.setText(f"{price:.2f}" if price > 0 else "-")

        # Volume column
        volume = data.get('volume', 0)
        volume_item = self.table.item(row, 2)
        if not volume_item:
            volume_item = QTableWidgetItem()
            self.table.setItem(row, 2, volume_item)

        # Format volume nicely
        if volume >= 1000000:
            volume_text = f"{volume / 1000000:.1f}M"
        elif volume >= 1000:
            volume_text = f"{volume / 1000:.0f}K"
        elif volume > 0:
            volume_text = str(volume)
        else:
            volume_text = "-"
        volume_item.setText(volume_text)

        # Change % column
        change_pct = data.get('change_pct', 0.0)
        change_pct_item = self.table.item(row, 3)
        if not change_pct_item:
            change_pct_item = QTableWidgetItem()
            self.table.setItem(row, 3, change_pct_item)
        change_pct_item.setText(f"{change_pct:.2f}%" if abs(change_pct) > 0.01 else "-")

        # Apply color coding based on change %
        profit_color = QColor(60, 179, 113)  # Medium Sea Green
        loss_color = QColor(220, 20, 60)  # Crimson
        neutral_color = QColor(169, 169, 169)  # DarkGray

        color = profit_color if change_pct > 0 else (loss_color if change_pct < 0 else neutral_color)

        # Color the price and change % columns
        price_item.setForeground(color)
        change_pct_item.setForeground(color)
        volume_item.setForeground(neutral_color)

        # Set text alignments
        symbol_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        price_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        volume_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        change_pct_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

    @Slot(list)
    def _on_scan_complete(self, scan_results: List[Dict]):
        """Handle scan completion with EOD data from Chartink."""
        self._symbol_data.clear()
        self._symbol_to_row.clear()
        self.table.setRowCount(0)

        if not scan_results:
            self.table.insertRow(0)
            item = QTableWidgetItem("No symbols found")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(0, 0, item)
            for col in range(1, 4):
                self.table.setItem(0, col, QTableWidgetItem(""))
        else:
            # Sort results by symbol name for consistency
            sorted_results = sorted(scan_results, key=lambda x: x.get('symbol', ''))

            self.table.setRowCount(len(sorted_results))

            for i, result in enumerate(sorted_results):
                symbol = result.get('symbol', '')
                if not symbol:
                    continue

                self._symbol_to_row[symbol] = i
                self._symbol_data[symbol] = result

                # Create table items for each column
                for col in range(4):
                    if not self.table.item(i, col):
                        self.table.setItem(i, col, QTableWidgetItem())

                # Update row with data
                self._update_row_data(i, result)

            # Select first row automatically
            if len(sorted_results) > 0:
                index = self.table.model().index(0, 0)
                self.table.selectionModel().select(
                    index,
                    QItemSelectionModel.Select | QItemSelectionModel.Rows
                )
                self.table.setCurrentCell(0, 0)
                self.table.setFocus()

                # Reset symbol index when new scan results arrive
                self._current_symbol_index = 0

        logger.info(f"EOD Scanner table updated with {len(scan_results)} symbols.")
        self.scan_dropdown.setEnabled(True)
        self.manage_btn.setEnabled(True)

    @Slot(str)
    def _on_scan_error(self, error_message: str):
        """Handle scan errors."""
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
        """Handle scan selection changes."""
        if self.scan_dropdown.signalsBlocked():
            return

        current_index = self.scan_dropdown.currentIndex()
        self._save_last_selected_scan(current_index)
        self._run_current_scan()

    def _save_last_selected_scan(self, index: int):
        """Save the last selected scan index."""
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
        """Load the last selected scan index."""
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
        dialog = ModernManageScansDialog(self.scans, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.scans = dialog.get_scans()
            self._save_scans()

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
                # FIXED: Don't auto-run scan after saving changes
                # Just clear the table and show a message to manually select/run
                self.table.setRowCount(0)
                self.table.insertRow(0)
                item = QTableWidgetItem("✅ Scans saved. Select a scan from dropdown to run.")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(0, 0, item)
                for col in range(1, 4):
                    self.table.setItem(0, col, QTableWidgetItem(""))

                # Log the successful save
                logger.info(f"Scans saved successfully. {len(self.scans)} scans available.")

    def _run_current_scan(self):
        """Run the currently selected Chartink scan."""
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

        logger.info(f"Running EOD Chartink scan: {selected_scan.get('name', 'Unnamed')}")

        self.scan_dropdown.setEnabled(False)
        self.manage_btn.setEnabled(False)

        # Show loading state
        self.table.setRowCount(0)
        self.table.insertRow(0)
        item = QTableWidgetItem("🔄 Loading scan results...")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.table.setItem(0, 0, item)
        for col in range(1, 4):
            self.table.setItem(0, col, QTableWidgetItem(""))

        # Stop any existing scan
        if self.scan_thread and self.scan_thread.isRunning():
            self.scan_thread.terminate()
            self.scan_thread.wait(3000)

        # Start new scan
        self.scan_thread = ScanWorker(selected_scan_url)
        self.scan_thread.scan_completed.connect(self._on_scan_complete)
        self.scan_thread.scan_error.connect(self._on_scan_error)
        self.scan_thread.start()

    def _on_cell_clicked(self, row: int, column: int):
        """Handle cell clicks and emit symbol selection."""
        try:
            symbol_item = self.table.item(row, 0)
            if symbol_item and symbol_item.flags() & Qt.ItemFlag.ItemIsSelectable:
                symbol_text = symbol_item.text()
                if symbol_text and not symbol_text.startswith(("Error:", "🔄", "No symbols", "No scans")):
                    # Update current index when manually clicking
                    self._current_symbol_index = row
                    self.symbol_selected.emit(symbol_text)
        except Exception as e:
            logger.warning(f"Could not get symbol from clicked row {row}: {e}")

    def get_current_symbols(self) -> List[str]:
        """Get list of current symbols in the table."""
        return list(self._symbol_data.keys())

    def get_symbol_data(self, symbol: str) -> Optional[Dict]:
        """Get complete data for a specific symbol."""
        return self._symbol_data.get(symbol)

    def cleanup(self):
        """Clean up scanner table threads"""
        try:
            logger.info("Cleaning up ChartinkScannerTable...")

            if hasattr(self, 'scan_thread') and self.scan_thread:
                if self.scan_thread.isRunning():
                    self.scan_thread.quit()
                    if not self.scan_thread.wait(2000):
                        self.scan_thread.terminate()
                        self.scan_thread.wait(1000)

            logger.info("ChartinkScannerTable cleanup completed")
        except Exception as e:
            logger.error(f"Error cleaning up ChartinkScannerTable: {e}")

    def closeEvent(self, event):
        """Clean up when widget is closed."""
        self.cleanup()
        super().closeEvent(event)

    def _load_scans(self) -> List[Dict[str, str]]:
        """Load scan configurations from file."""
        scan_dir = os.path.dirname(SCAN_URL_FILE)
        if not os.path.exists(scan_dir):
            os.makedirs(scan_dir, exist_ok=True)

        if not os.path.exists(SCAN_URL_FILE):
            logger.info(f"Creating default scan configuration at: {SCAN_URL_FILE}")
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
        """FIXED dark theme styling with proper alternate row selection."""
        self.setStyleSheet("""
            QWidget {
                background-color: #0a0a0a;
                color: #e0e0e0;
                font-family: "Segoe UI", Arial, sans-serif;
                font-size: 13px;
            }

            /* Header Container */
            QWidget#headerContainer {
                background-color: #1a1a1a;
                border-bottom: 1px solid #303030;
                padding: 5px;
            }

            /* Scan Label */
            QLabel#scanLabel {
                color: #a0c0ff;
                font-weight: 600;
                font-size: 11px;
            }

            /* Dropdown */
            QComboBox#minimalDropdown {
                background-color: #1a1a1a;
                border: 1px solid #303030;
                color: #ffffff;
                padding: 3px 6px;
                border-radius: 2px;
                font-size: 12px;
            }
            QComboBox#minimalDropdown:hover {
                border-color: #505050;
            }
            QComboBox#minimalDropdown:focus {
                border-color: #6a9cff;
                outline: none;
            }
            QComboBox#minimalDropdown:disabled {
                background-color: #050505;
                color: #606060;
                border-color: #202020;
            }

            QComboBox#minimalDropdown::drop-down {
                border: none;
                width: 18px;
            }
            QComboBox#minimalDropdown::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 4px solid #808080;
            }
            QComboBox#minimalDropdown::down-arrow:hover {
                border-top-color: #ffffff;
            }

            /* Dropdown List */
            QComboBox#minimalDropdown QAbstractItemView {
                background-color: #1a1a1a;
                border: 1px solid #6a9cff;
                border-radius: 2px;
                color: #ffffff;
                selection-background-color: rgba(74, 122, 191, 0.2);
                selection-color: #ffffff;
                padding: 1px;
                outline: none;
            }
            QComboBox#minimalDropdown QAbstractItemView::item {
                padding: 5px 8px;
                border: none;
                border-radius: 1px;
                margin: 0px 1px;
                font-size: 12px;
            }
            QComboBox#minimalDropdown QAbstractItemView::item:hover {
                background-color: #2a2a2a;
            }
            QComboBox#minimalDropdown QAbstractItemView::item:selected {
                background-color: rgba(74, 122, 191, 0.2);
                color: #ffffff;
            }

            /* Settings Button */
            QPushButton#settingsMinimalButton {
                background-color: #2a2a2a;
                color: #a0c0ff;
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

            /* FIXED Table Styling with Proper Alternate Row Selection */
            QTableWidget {
                background-color: #0a0a0a;
                border: none;
                gridline-color: #2a2a2a;
                selection-background-color: #1e3a5f;
                alternate-background-color: #0f0f0f;
                outline: none;
                show-decoration-selected: 0;
                font-size: 12px;
                border-radius: 0px;
            }

            QTableWidget::item {
                padding: 5px 8px;
                border-bottom: 1px solid #1a1a1a;
                background-color: transparent;
                font-size: 12px;
            }

            QTableWidget::item:selected {
                background-color: #1e3a5f !important;
                outline: none;
                border: none;
                color: #ffffff;
                font-weight: 600;
            }

            QTableWidget::item:focus {
                background-color: #1e3a5f !important;
                outline: none;
                border: none;
            }

            QTableWidget::item:hover {
                background-color: transparent;
            }

            QTableWidget::item:alternate {
                background-color: #0f0f0f;
            }

            QTableWidget::item:alternate:selected {
                background-color: #1e3a5f !important;
                color: #ffffff;
                font-weight: 600;
            }

            /* Header Styling */
            QHeaderView::section {
                background-color: #1a1a1a;
                color: #a0c0ff;
                padding: 3px 10px;
                border: none;
                border-bottom: 1px solid #303030;
                border-right: 1px solid #101010;
                font-weight: 600;
                font-size: 11px;
            }
            QHeaderView::section:last {
                border-right: none;
            }
            QHeaderView::section:hover {
                background-color: #2a2a2a;
            }

            /* Enhanced Scrollbars */
            QScrollBar:vertical {
                background-color: #0a0a0a;
                width: 8px;
                border: none;
                margin: 0px;
            }

            QScrollBar::handle:vertical {
                background-color: #424242;
                border-radius: 4px;
                min-height: 20px;
                margin: 2px;
            }

            QScrollBar::handle:vertical:hover {
                background-color: #616161;
            }

            QScrollBar:horizontal {
                background-color: #0a0a0a;
                height: 8px;
                border: none;
                margin: 0px;
            }

            QScrollBar::handle:horizontal {
                background-color: #424242;
                border-radius: 4px;
                min-width: 20px;
                margin: 2px;
            }

            QScrollBar::handle:horizontal:hover {
                background-color: #616161;
            }

            QScrollBar::add-line, QScrollBar::sub-line {
                border: none;
                background: none;
                width: 0px;
                height: 0px;
                margin: 0px;
            }
        """)