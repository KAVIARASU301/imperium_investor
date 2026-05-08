# FIXED scanner_table.py with proper row selection and highlighting
import logging
import json
import os
import requests
from bs4 import BeautifulSoup as bs
from typing import List, Dict, Optional

from PySide6.QtCore import Signal, Slot, Qt, QThread, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QPushButton, QHBoxLayout, QLabel, QComboBox, QMessageBox,
    QDialog, QLineEdit, QFormLayout, QGroupBox, QFrame, QTextEdit,
    QStyledItemDelegate, QStyleOptionViewItem, QApplication, QStyle
)
from PySide6.QtGui import QColor, QFont, QBrush, QCursor
from PySide6.QtCore import QItemSelectionModel

logger = logging.getLogger(__name__)
SCAN_URL_FILE = os.path.join(os.path.expanduser("~/.qullamaggie"), "chartink_scans.json")
SETTINGS_FILE = os.path.join(os.path.expanduser("~/.qullamaggie"), "scanner_settings.json")
SCAN_GROUP_ORDER = ["Momentum Breakouts", "Episodic Pivot", "Parabolic", "Others"]

VOLUME_STRENGTH_ENABLED_ROLE = Qt.ItemDataRole.UserRole + 101
VOLUME_STRENGTH_LEVEL_ROLE = Qt.ItemDataRole.UserRole + 102
VOLUME_STRENGTH_COLOR_ROLE = Qt.ItemDataRole.UserRole + 103


class VolumeStrengthDelegate(QStyledItemDelegate):
    """Paint scanner volume strength as a compact TC2000-style progress bar."""

    def paint(self, painter, option, index):
        enabled = bool(index.data(VOLUME_STRENGTH_ENABLED_ROLE))
        if not enabled:
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        volume_text = opt.text
        opt.text = ""
        QApplication.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        level = max(0, min(3, int(index.data(VOLUME_STRENGTH_LEVEL_ROLE) or 0)))
        fill_color = QColor(index.data(VOLUME_STRENGTH_COLOR_ROLE) or "#45d4ff")
        track_color = QColor(fill_color)
        track_color.setAlpha(45)
        empty_color = QColor(70, 82, 98, 120)
        text_color = QColor(fill_color)
        if opt.state & QStyle.StateFlag.State_Selected:
            text_color = opt.palette.highlightedText().color()
            empty_color = QColor(220, 235, 255, 70)
            track_color = QColor(220, 235, 255, 50)

        rect = opt.rect.adjusted(5, 0, -5, 0)
        segment_count = 3
        gap = 2
        bar_width = min(34, max(24, rect.width() // 2))
        segment_width = max(6, (bar_width - gap * (segment_count - 1)) // segment_count)
        used_bar_width = segment_width * segment_count + gap * (segment_count - 1)
        bar_height = 7
        bar_x = rect.left()
        bar_y = rect.center().y() - bar_height // 2

        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(bar_x, bar_y, used_bar_width, bar_height, 3, 3)
        for i in range(segment_count):
            segment_x = bar_x + i * (segment_width + gap)
            painter.setBrush(fill_color if i < level else empty_color)
            painter.drawRoundedRect(segment_x, bar_y, segment_width, bar_height, 3, 3)

        painter.setPen(text_color)
        text_rect = rect.adjusted(used_bar_width + 7, 0, 0, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, volume_text)
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        if bool(index.data(VOLUME_STRENGTH_ENABLED_ROLE)):
            size.setWidth(max(size.width(), 82))
        return size


def _volume_strength_level(volume: int) -> int:
    if volume >= 5_000_000:
        return 3
    if volume >= 1_000_000:
        return 2
    if volume >= 250_000:
        return 1
    return 0


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

        # Scan tag/group
        tag_label = QLabel("Tag / Group")
        tag_label.setObjectName("fieldLabel")
        self.tag_input = QComboBox()
        self.tag_input.setObjectName("minimalInput")
        self.tag_input.addItems(SCAN_GROUP_ORDER)
        self.tag_input.setCurrentText("Others")

        form_layout.addWidget(tag_label)
        form_layout.addWidget(self.tag_input)

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
            "url": self.url_input.toPlainText().strip(),
            "tag": self.tag_input.currentText().strip() or "Others"
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

            QLineEdit#minimalInput, QTextEdit#minimalTextArea, QComboBox#minimalInput {
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
            QLineEdit#minimalInput:focus, QTextEdit#minimalTextArea:focus, QComboBox#minimalInput:focus {
                border-color: #00d4ff;
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
        self.scans_table.setColumnCount(4)
        self.scans_table.setHorizontalHeaderLabels(["Scan Name", "Tag", "Clause Preview", "Actions"])

        # Configure table
        header = self.scans_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

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

            # Tag
            tag_item = QTableWidgetItem(scan.get("tag", "Others"))
            self.scans_table.setItem(row, 1, tag_item)

            # URL preview (truncated)
            url = scan.get("url", "")
            preview = url[:60] + "..." if len(url) > 60 else url
            preview_item = QTableWidgetItem(preview)
            preview_item.setFont(QFont("Consolas", 8))
            self.scans_table.setItem(row, 2, preview_item)

            # Actions button
            delete_btn = QPushButton("🗑")  # Just the delete icon, no text
            delete_btn.setObjectName("deleteMinimalButton")
            delete_btn.setFixedSize(24, 24)  # Small square button
            delete_btn.setToolTip("Delete this scan")  # Helpful tooltip
            delete_btn.clicked.connect(lambda checked, r=row: self._delete_scan(r))
            self.scans_table.setCellWidget(row, 3, delete_btn)

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
    symbol_selected     = Signal(str)
    scan_results_changed = Signal()   # emitted when scan completes → triggers re-subscription
    visible_rows_changed = Signal()   # emitted when scroll changes visible rows

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scans = self._load_scans()
        self.scan_thread: ScanWorker = None
        self._symbol_data: Dict[str, Dict] = {}
        self._symbol_to_row: Dict[str, int] = {}
        self._instrument_map: Dict[str, Dict] = {}
        self._token_to_symbol: Dict[int, str] = {}
        self._dirty_symbols = set()
        self._dropdown_scan_indices: List[int] = []
        self._current_symbol_index = 0  # Track current symbol for spacebar navigation
        self._last_visible_tokens: set = set()  # track to avoid redundant re-subs
        self._color_theme = {
            "enable_volume_strength_indicator": False,
            "tables": {"positive": "#26a69a", "negative": "#ef5350", "neutral": "#a9a9a9", "volume": "#45d4ff"}
        }

        self._setup_ui()
        self._apply_enhanced_styles()
        self._ui_flush_timer = QTimer(self)
        self._ui_flush_timer.timeout.connect(self._flush_pending_ui_updates)
        self._ui_flush_timer.start(225)

        if self.scans:
            last_selected = self._load_last_selected_scan()
            if 0 <= last_selected < len(self.scans):
                self.scan_dropdown.blockSignals(True)
                self._set_dropdown_to_scan_index(last_selected)
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
        self.table.setItemDelegateForColumn(2, VolumeStrengthDelegate(self.table))
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.table.setFocus()

        # Add focus out event to clear selection (from positions table pattern)
        self.table.focusOutEvent = self._on_table_focus_out

        # Emit visible_rows_changed on scroll so main_window can re-evaluate subscriptions
        self.table.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)

        # ADDED: Setup spacebar shortcut for symbol navigation
        self._setup_keyboard_shortcuts()

    def _setup_keyboard_shortcuts(self):
        """Setup keyboard shortcuts for scanner table."""
        # NOTE: Global spacebar shortcuts are now handled by the main window
        # for context-aware navigation. This method is kept for potential
        # future scanner-specific shortcuts.
        logger.info("Scanner table ready for context-aware navigation")

    def apply_color_theme(self, theme: Dict):
        self._color_theme = theme or self._color_theme
        for symbol, row in self._symbol_to_row.items():
            data = self._symbol_data.get(symbol)
            if data is not None:
                self._update_row_data(row, data)

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

    def _normalize_scan_tag(self, tag: Optional[str]) -> str:
        """Normalize user-entered scan tags into known scan sections."""
        raw_tag = (tag or "").strip()
        if not raw_tag:
            return "Others"

        lower_tag = raw_tag.lower()
        for group in SCAN_GROUP_ORDER:
            if lower_tag == group.lower():
                return group

        aliases = {
            "momentum": "Momentum Breakouts",
            "momentum breakout": "Momentum Breakouts",
            "breakout": "Momentum Breakouts",
            "episodic": "Episodic Pivot",
            "pivot": "Episodic Pivot",
            "episodic pivot": "Episodic Pivot",
            "parabolic move": "Parabolic",
            "other": "Others",
        }
        return aliases.get(lower_tag, raw_tag)

    def _get_sorted_scans_with_indices(self):
        """Return scans sorted by group and name with source index mapping."""
        decorated = []
        for idx, scan in enumerate(self.scans):
            tag = self._normalize_scan_tag(scan.get("tag"))
            scan["tag"] = tag
            rank = SCAN_GROUP_ORDER.index(tag) if tag in SCAN_GROUP_ORDER else len(SCAN_GROUP_ORDER)
            name = scan.get("name", f"Scan {idx + 1}")
            decorated.append((rank, tag.lower(), name.lower(), idx, scan))

        decorated.sort(key=lambda item: (item[0], item[1], item[2]))
        return [(idx, scan) for _, _, _, idx, scan in decorated]

    def _update_scan_dropdown(self):
        """Update the scan dropdown with grouped scan sections."""
        self.scan_dropdown.blockSignals(True)
        self.scan_dropdown.clear()
        self._dropdown_scan_indices = []

        if self.scans:
            sorted_scans = self._get_sorted_scans_with_indices()
            current_group = None
            for scan_index, scan in sorted_scans:
                tag = self._normalize_scan_tag(scan.get("tag"))
                if tag != current_group:
                    self.scan_dropdown.addItem(f"── {tag} ──")
                    self.scan_dropdown.setItemData(self.scan_dropdown.count() - 1, False, Qt.ItemDataRole.UserRole - 1)
                    current_group = tag

                display_name = scan.get("name", f"Scan {scan_index + 1}")
                self.scan_dropdown.addItem(display_name)
                self._dropdown_scan_indices.append(scan_index)

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
        self.table.setHorizontalHeaderLabels(["Symbol", "Price", "Vol", "%CHG"])

        self.table.horizontalHeader().setVisible(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)

        self.table.setColumnWidth(3, 68)

        self.table.verticalHeader().setVisible(False)

        # FIXED: Use proper selection behavior - SelectRows not individual cells
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(True)
        self.table.setAlternatingRowColors(True)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        # TC2000-inspired compact table density
        self.table.verticalHeader().setDefaultSectionSize(24)
        header_font = QFont("Segoe UI", 10)
        header_font.setBold(True)
        self.table.horizontalHeader().setFont(header_font)

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
        symbol_item.setToolTip(f"Open chart for {data['symbol']}")

        # Price column (EOD closing price)
        price = data.get('price', 0.0)
        price_item = self.table.item(row, 1)
        if not price_item:
            price_item = QTableWidgetItem()
            self.table.setItem(row, 1, price_item)
        price_item.setText(f"{price:,.2f}" if price > 0 else "-")

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
        show_volume_strength = bool(self._color_theme.get("enable_volume_strength_indicator", False))
        volume_strength_level = _volume_strength_level(volume) if show_volume_strength else 0
        volume_item.setText(volume_text)
        volume_item.setData(VOLUME_STRENGTH_ENABLED_ROLE, show_volume_strength)
        volume_item.setData(VOLUME_STRENGTH_LEVEL_ROLE, volume_strength_level)
        volume_item.setData(
            VOLUME_STRENGTH_COLOR_ROLE,
            self._color_theme.get("tables", {}).get("volume", "#45d4ff")
        )
        strength_label = f" | Strength: {volume_strength_level}/3" if show_volume_strength else ""
        volume_item.setToolTip(f"Reported volume: {volume:,.0f}{strength_label}")

        # Change % column
        change_pct = data.get('change_pct', 0.0)
        change_pct_item = self.table.item(row, 3)
        if not change_pct_item:
            change_pct_item = QTableWidgetItem()
            self.table.setItem(row, 3, change_pct_item)
        change_pct_item.setText(f"{change_pct:+.2f}" if abs(change_pct) > 0.01 else "0.00")

        # Apply color coding based on change %
        table_colors = self._color_theme.get("tables", {})
        directional_colors_enabled = bool(self._color_theme.get("enable_table_directional_colors", False))
        profit_color = QColor(table_colors.get("positive", "#26a69a"))
        loss_color = QColor(table_colors.get("negative", "#ef5350"))
        neutral_color = QColor(table_colors.get("neutral", "#a9a9a9"))

        color = neutral_color
        if directional_colors_enabled:
            color = profit_color if change_pct > 0 else (loss_color if change_pct < 0 else neutral_color)

        # Color the LTP and change % columns
        price_item.setForeground(color)
        change_pct_item.setForeground(color)
        volume_item.setForeground(QColor(table_colors.get("volume", "#45d4ff")))

        # Subtle directional tint in % change cell (keeps selected-row style readable)
        if directional_colors_enabled and change_pct > 0:
            change_pct_item.setBackground(QBrush(QColor(18, 55, 34, 140)))
        elif directional_colors_enabled and change_pct < 0:
            change_pct_item.setBackground(QBrush(QColor(70, 20, 20, 140)))
        else:
            change_pct_item.setBackground(QBrush(QColor(35, 35, 35, 100)))

        # Set text alignments
        symbol_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        price_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        volume_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        change_pct_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        symbol_font = symbol_item.font()
        symbol_font.setBold(True)
        symbol_item.setFont(symbol_font)

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

        # Build token map so update_data() can push live ticks immediately
        self._rebuild_token_map()

        # Reset visible-token cache so next subscription call forces a fresh diff
        self._last_visible_tokens = set()

        # Notify main_window to re-evaluate the subscription universe
        self.scan_results_changed.emit()

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

    def _set_dropdown_to_scan_index(self, scan_index: int):
        """Select the dropdown item for a given scan index."""
        if scan_index is None:
            return

        non_header = -1
        for dropdown_idx in range(self.scan_dropdown.count()):
            label = self.scan_dropdown.itemText(dropdown_idx).strip()
            if label.startswith("──"):
                continue
            non_header += 1
            if non_header < len(self._dropdown_scan_indices) and self._dropdown_scan_indices[non_header] == scan_index:
                self.scan_dropdown.setCurrentIndex(dropdown_idx)
                return

    def _on_scan_selection_changed(self):
        """Handle scan selection changes."""
        if self.scan_dropdown.signalsBlocked():
            return

        selected_scan_index = self._get_selected_scan_index()
        if selected_scan_index is None:
            return

        self._save_last_selected_scan(selected_scan_index)
        self._run_current_scan()

    def _get_selected_scan_index(self) -> Optional[int]:
        """Map dropdown selection to actual self.scans index, skipping section headers."""
        current_index = self.scan_dropdown.currentIndex()
        if current_index < 0:
            return None

        item_text = self.scan_dropdown.currentText().strip()
        if item_text.startswith("──"):
            return None

        scan_counter = -1
        for dropdown_idx in range(current_index + 1):
            text = self.scan_dropdown.itemText(dropdown_idx).strip()
            if text and not text.startswith("──"):
                scan_counter += 1

        if 0 <= scan_counter < len(self._dropdown_scan_indices):
            return self._dropdown_scan_indices[scan_counter]
        return None

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
            selected_scan_index = self._get_selected_scan_index()
            self._update_scan_dropdown()

            if selected_scan_index is not None:
                self._set_dropdown_to_scan_index(selected_scan_index)
            else:
                self.scan_dropdown.setCurrentIndex(1 if self.scan_dropdown.count() > 1 else 0)
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

        selected_scan_index = self._get_selected_scan_index()
        if selected_scan_index is None or selected_scan_index < 0 or selected_scan_index >= len(self.scans):
            return

        selected_scan = self.scans[selected_scan_index]
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

    # ─────────────────────────────────────────────────────────────────────
    # VIEWPORT-AWARE SYMBOL ACCESS  (institutional grade: subscribe only
    # what the trader can actually see — zero wasted API tokens)
    # ─────────────────────────────────────────────────────────────────────

    def get_visible_symbols(self, buffer: int = 5) -> List[str]:
        """
        Return symbols for rows currently visible in the scroll viewport,
        plus a small look-ahead buffer above/below for smooth scrolling.
        Falls back to ALL symbols if viewport geometry is unavailable.
        """
        if not self._symbol_data:
            return []

        vp = self.table.viewport()
        if vp is None or vp.height() == 0:
            return list(self._symbol_data.keys())

        top_row    = self.table.rowAt(0)
        bottom_row = self.table.rowAt(vp.height() - 1)

        # rowAt returns -1 when the table is shorter than the viewport
        if top_row == -1:
            top_row = 0
        if bottom_row == -1:
            bottom_row = self.table.rowCount() - 1

        # Apply buffer rows for smooth pre-subscribe on scroll
        first = max(0, top_row - buffer)
        last  = min(self.table.rowCount() - 1, bottom_row + buffer)

        symbols = []
        for row in range(first, last + 1):
            item = self.table.item(row, 0)
            if item:
                sym = item.text()
                if sym and sym in self._symbol_data:
                    symbols.append(sym)
        return symbols

    def get_visible_tokens(self) -> List[int]:
        """
        Return instrument tokens for VISIBLE rows only.
        Called by main_window._get_scanner_visible_tokens() to build
        the subscription universe — never subscribes the full scan result.
        """
        tokens = []
        for sym in self.get_visible_symbols():
            inst = self._instrument_map.get(sym)
            if inst:
                token = inst.get('instrument_token')
                if token is not None:
                    try:
                        tokens.append(int(token))
                    except (TypeError, ValueError):
                        pass
        return tokens

    def _on_scroll_changed(self, _value: int) -> None:
        """
        Scroll event: check whether the visible token set actually changed.
        Only emit visible_rows_changed (→ re-subscription) when it did.
        Debounces itself: identical token sets don't fire the signal.
        """
        new_tokens = set(self.get_visible_tokens())
        if new_tokens != self._last_visible_tokens:
            self._last_visible_tokens = new_tokens
            self.visible_rows_changed.emit()

    # ─────────────────────────────────────────────────────────────────────

    def get_current_symbols(self) -> List[str]:
        """Get list of current symbols in the table."""
        return list(self._symbol_data.keys())

    def get_symbol_data(self, symbol: str) -> Optional[Dict]:
        """Get complete data for a specific symbol."""
        return self._symbol_data.get(symbol)

    def set_instrument_map(self, instrument_map: dict) -> None:
        """Store instrument metadata so scanner symbols can be mapped to tokens."""
        self._instrument_map = instrument_map
        self._rebuild_token_map()
        logger.debug(f"Scanner instrument map set — {len(instrument_map)} instruments")

    def _rebuild_token_map(self) -> None:
        """Rebuild token -> symbol mapping for current scanner results."""
        self._token_to_symbol = {}
        for symbol in self._symbol_data:
            inst = self._instrument_map.get(symbol)
            if inst:
                token = inst.get('instrument_token')
                if token is not None:
                    try:
                        self._token_to_symbol[int(token)] = symbol
                    except (TypeError, ValueError):
                        pass

        logger.debug(
            f"Scanner token map rebuilt — {len(self._token_to_symbol)} of "
            f"{len(self._symbol_data)} symbols resolved"
        )

    def update_data(self, ticks: list) -> None:
        """Apply live tick updates to scanner rows for price, volume and change %."""
        if not ticks or not self._token_to_symbol:
            return

        for tick in ticks:
            try:
                raw_token = tick.get('instrument_token')
                if raw_token is None:
                    continue
                token = int(raw_token)

                symbol = self._token_to_symbol.get(token)
                if not symbol or symbol not in self._symbol_data:
                    continue

                data = self._symbol_data[symbol]

                ltp = tick.get('last_price')
                if ltp is not None:
                    data['price'] = float(ltp)

                for vol_field in ('volume_traded', 'volume'):
                    vol = tick.get(vol_field)
                    if vol is not None:
                        try:
                            v = int(vol)
                            if v > 0:
                                data['volume'] = v
                                break
                        except (TypeError, ValueError):
                            pass

                chg = tick.get('change_percent') or tick.get('net_change_percent')
                if chg is not None:
                    data['change_pct'] = float(chg)
                else:
                    ohlc = tick.get('ohlc') or {}
                    prev_close = ohlc.get('close', 0.0) if isinstance(ohlc, dict) else 0.0
                    if prev_close and prev_close > 0 and data.get('price', 0) > 0:
                        data['change_pct'] = ((data['price'] - prev_close) / prev_close) * 100.0

                row = self._symbol_to_row.get(symbol)
                if row is not None:
                    self._dirty_symbols.add(symbol)

            except Exception as e:
                logger.debug(f"Scanner tick error: {e}")

    def _flush_pending_ui_updates(self) -> None:
        """Batch scanner row repaints to ~4-5 FPS for readability."""
        if not self._dirty_symbols:
            return

        dirty_symbols = tuple(self._dirty_symbols)
        self._dirty_symbols.clear()
        for symbol in dirty_symbols:
            row = self._symbol_to_row.get(symbol)
            if row is None:
                continue
            data = self._symbol_data.get(symbol)
            if data is None:
                continue
            self._update_row_data(row, data)

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
                    "url": "( {57960} ( latest \"close\" > latest \"sma( close , 20 )\" ) )",
                    "tag": "Others"
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
                    scan['tag'] = self._normalize_scan_tag(scan.get('tag', 'Others'))
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
                background-color: #050709;
                color: #e8f0ff;
                font-family: "'Segoe UI', -apple-system, Roboto, Arial, sans-serif";
                font-size: 13px;
            }

            /* Header Container */
            QWidget#headerContainer {
                background-color: #0a0d12;
                border-bottom: 1px solid #1a2030;
                padding: 5px;
            }

            /* Scan Label */
            QLabel#scanLabel {
                color: #a8bcd4;
                font-weight: 600;
                font-size: 11px;
            }

            /* Dropdown */
            QComboBox#minimalDropdown {
                background-color: #0f1318;
                border: 1px solid #1a2030;
                color: #e8f0ff;
                padding: 3px 6px;
                border-radius: 2px;
                font-size: 12px;
            }
            QComboBox#minimalDropdown:hover {
                border-color: #505050;
            }
            QComboBox#minimalDropdown:focus {
                border-color: #00d4ff;
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
                background-color: #111b2a;
                color: #a8bcd4;
                font-size: 11px;
                font-weight: 500;
                border-radius: 3px;
                border: 1px solid #1a2030;
                padding: 3px 7px;
            }
            QPushButton#settingsMinimalButton:hover {
                background-color: #141920;
                border-color: #00d4ff;
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
                background-color: #0f1318;
                border: 1px solid #1a2030;
                gridline-color: #1a2030;
                selection-background-color: #1a2840;
                alternate-background-color: #0f1318;
                outline: none;
                show-decoration-selected: 0;
                font-size: 12px;
                border-radius: 0px;
            }

            QTableWidget::item {
                padding: 1px 5px;
                border-bottom: 1px solid #1a2030;
                background-color: transparent;
                font-size: 12px;
            }

            QTableWidget::item:selected {
                background-color: #1a2840 !important;
                outline: none;
                border: none;
                color: #ffffff;
                font-weight: 600;
            }

            QTableWidget::item:focus {
                background-color: #1a2840 !important;
                outline: none;
                border: none;
            }

            QTableWidget::item:hover {
                background-color: #141920;
            }

            QTableWidget::item:alternate {
                background-color: #0f1318;
            }

            QTableWidget::item:alternate:selected {
                background-color: #1a2840 !important;
                color: #ffffff;
                font-weight: 600;
            }

            /* Header Styling */
            QHeaderView::section {
                background-color: #0b1019;
                color: #7fd4ff;
                padding: 2px 5px;
                border: none;
                border-bottom: 1px solid #24344c;
                border-right: 1px solid #121c2b;
                font-weight: 600;
                font-size: 11px;
                text-transform: uppercase;
            }
            QHeaderView::section:last {
                border-right: none;
            }
            QHeaderView::section:hover {
                background-color: #2a2a2a;
            }

            /* Enhanced Scrollbars */
            QScrollBar:vertical {
                background-color: #05070b;
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
