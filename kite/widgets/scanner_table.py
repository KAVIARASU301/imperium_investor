# kite/widgets/scanner_table.py
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
    QDialog, QLineEdit, QGroupBox, QTextEdit,
    QStyledItemDelegate, QStyleOptionViewItem, QApplication, QStyle
)
from PySide6.QtGui import QColor, QFont, QBrush, QCursor, QFontMetrics, QIcon
from PySide6.QtCore import QItemSelectionModel
from app_paths import get_asset_path

logger = logging.getLogger(__name__)


def _prefer_text_antialias(font: QFont) -> QFont:
    """Prefer antialiased glyph rasterization for crisper HiDPI text."""
    try:
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    except Exception:
        pass
    return font
SCAN_URL_FILE = os.path.join(os.path.expanduser("~/.qullamaggie"), "chartink_scans.json")
SETTINGS_FILE = os.path.join(os.path.expanduser("~/.qullamaggie"), "scanner_settings.json")
SCAN_GROUP_ORDER = ["Momentum Breakouts", "Episodic Pivot", "Parabolic", "Intraday", "Others"]
CHART_TOOLBAR_HEIGHT = 26
CHART_TOOLBAR_CONTROL_HEIGHT = 22

VOLUME_STRENGTH_ENABLED_ROLE = Qt.ItemDataRole.UserRole + 101
VOLUME_STRENGTH_LEVEL_ROLE = Qt.ItemDataRole.UserRole + 102
VOLUME_STRENGTH_COLOR_ROLE = Qt.ItemDataRole.UserRole + 103

# ─────────────────────────────────────────────────────────────────────────────
#  AMOLED INSTITUTIONAL DARK TRADING TERMINAL UI TOKENS
# ─────────────────────────────────────────────────────────────────────────────
_BG0 = "#050709"      # deepest app / AMOLED shell
_BG1 = "#0a0d12"      # main table body
_BG2 = "#0f1318"      # alternate row / panel layer
_BG3 = "#141920"      # hover / raised layer
_BG4 = "#1a2030"      # thin border
_BG5 = "#26354a"      # active border
_BGTB = "#070a0f"     # toolbar / dialog header
_BULL = "#00d4a8"
_BEAR = "#ff4d6a"
_AMBER = "#f59e0b"
_CYAN = "#00d4ff"
_BLUE = "#3b82f6"
_T0 = "#e8f0ff"
_T1 = "#a8bcd4"
_T2 = "#5a7090"
_T3 = "#2a3a50"
_SYMBOL_TEXT = "#d6e2f2"
_SEL = "#1a2840"
_MONO = "'JetBrains Mono', 'Consolas', monospace"  # code, raw scan clauses, debug text only
_SANS = "'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', Arial, sans-serif"
_NUM = "'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', sans-serif"
_SYMBOL_FONT = "Inter"
_UI_FONT = "Inter"
_NUM_FONT = "Inter"
_SYMBOL_FONT_FAMILIES = ["Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", "Noto Sans"]
_UI_FONT_FAMILIES = ["Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", "Noto Sans"]
_NUM_FONT_FAMILIES = ["Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", "Noto Sans"]
_ROW_H = 22
_DIALOG_ROW_H = 24


def _set_font_families(font: QFont, families: List[str]) -> QFont:
    """Apply Qt font fallbacks without relying on CSS-only font stacks."""
    try:
        font.setFamilies(families)
    except AttributeError:
        # Older Qt builds only support a single family in the constructor.
        pass
    return font


def _ui_font(point_size: int = 9, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Modern readable UI font; intentionally not heavy or distracting."""
    font = QFont(_UI_FONT)
    _set_font_families(font, _UI_FONT_FAMILIES)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPointSize(point_size)
    font.setWeight(weight)
    font.setKerning(True)
    return font


def _symbol_font(pixel_size: int = 10, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Compact symbol font. Uses pixels so ticker text does not grow larger than QSS table text."""
    font = QFont(_SYMBOL_FONT)
    _set_font_families(font, _SYMBOL_FONT_FAMILIES)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    font.setKerning(True)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 103)
    return font


def _number_font(point_size: int = 9, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Calm tabular-looking UI number font for prices, volume and percentages."""
    font = QFont(_NUM_FONT)
    _set_font_families(font, _NUM_FONT_FAMILIES)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setPointSize(point_size)
    font.setWeight(weight)
    font.setKerning(True)
    return font


def _mono_font(point_size: int = 9, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Monospace reserved for raw scan clauses/debug-style text."""
    font = QFont("Consolas")
    _set_font_families(font, ["Consolas", "JetBrains Mono", "Courier New"])
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSize(point_size)
    font.setWeight(weight)
    return font


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
        fill_color = QColor(index.data(VOLUME_STRENGTH_COLOR_ROLE) or "#00d4ff")
        track_color = QColor(fill_color)
        track_color.setAlpha(45)
        empty_color = QColor(70, 82, 98, 120)
        text_color = QColor(fill_color)
        if opt.state & QStyle.StateFlag.State_Selected:
            # Keep volume text/color stable during selection to avoid sudden text
            # color shifts while still indicating selected state.
            empty_color = QColor(210, 210, 210, 60)
            track_color = QColor(190, 190, 190, 40)

        rect = opt.rect.adjusted(5, 0, -5, 0)
        segment_count = 3
        gap = 2
        bar_width = min(34, max(24, rect.width() // 2))
        segment_width = max(6, (bar_width - gap * (segment_count - 1)) // segment_count)
        used_bar_width = segment_width * segment_count + gap * (segment_count - 1)
        bar_height = 6
        bar_x = rect.left()
        bar_y = rect.center().y() - bar_height // 2

        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRect(bar_x, bar_y, used_bar_width, bar_height)
        for i in range(segment_count):
            segment_x = bar_x + i * (segment_width + gap)
            painter.setBrush(fill_color if i < level else empty_color)
            painter.drawRect(segment_x, bar_y, segment_width, bar_height)

        painter.setPen(text_color)
        text_rect = rect.adjusted(used_bar_width + 7, 0, 0, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, volume_text)
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

    def __init__(self, parent=None, initial_scan: Optional[Dict[str, str]] = None, is_edit: bool = False):
        super().__init__(parent)
        self.initial_scan = initial_scan or {}
        self.is_edit = is_edit

        self.setWindowTitle("Edit Chartink Scan" if self.is_edit else "Add New Chartink Scan")
        self.setModal(True)
        self.setFixedSize(560, 350)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._drag_pos = None
        self._setup_ui()
        self._apply_styles()

        # ADDED: Auto-focus to the Scan Name input field
        self.name_input.setFocus()
        self.name_input.selectAll()  # Optional: select all text if any exists

    def _setup_ui(self):
        # Main container
        main_container = QWidget()
        main_container.setObjectName("dialogContainer")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(main_container)

        container_layout = QVBoxLayout(main_container)
        container_layout.setContentsMargins(12, 10, 12, 12)
        container_layout.setSpacing(8)

        # Header with title and close button
        header_layout = QHBoxLayout()

        title_label = QLabel("EDIT SCAN" if self.is_edit else "ADD SCAN")
        title_label.setObjectName("dialogTitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(22, 22)
        close_btn.clicked.connect(self.reject)

        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(close_btn)

        container_layout.addLayout(header_layout)

        # Form section
        form_group = QGroupBox("SCAN CONFIGURATION")
        form_group.setObjectName("formGroup")
        form_layout = QVBoxLayout(form_group)
        form_layout.setSpacing(6)

        # Scan name
        name_label = QLabel("SCAN NAME")
        name_label.setObjectName("fieldLabel")
        self.name_input = QLineEdit()
        self.name_input.setObjectName("minimalInput")
        self.name_input.setPlaceholderText("e.g., 'Breakout Stocks', 'High Volume Gainers'")

        form_layout.addWidget(name_label)
        form_layout.addWidget(self.name_input)

        # Scan clause
        clause_label = QLabel("SCAN CLAUSE")
        clause_label.setObjectName("fieldLabel")
        self.url_input = QTextEdit()
        self.url_input.setObjectName("minimalTextArea")
        self.url_input.setPlaceholderText("Paste your Chartink scan clause here...")
        self.url_input.setMaximumHeight(64)

        form_layout.addWidget(clause_label)
        form_layout.addWidget(self.url_input)

        # Scan tag/group
        tag_label = QLabel("TAG / GROUP")
        tag_label.setObjectName("fieldLabel")
        self.tag_input = QComboBox()
        self.tag_input.setObjectName("minimalInput")
        self.tag_input.addItems(SCAN_GROUP_ORDER)
        self.tag_input.setCurrentText("Others")

        form_layout.addWidget(tag_label)
        form_layout.addWidget(self.tag_input)

        container_layout.addWidget(form_group)

        # Button section
        button_layout = QHBoxLayout()
        button_layout.setSpacing(6)

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setObjectName("secondaryMinimalButton")
        cancel_btn.clicked.connect(self.reject)

        self.save_btn = QPushButton("SAVE" if self.is_edit else "ADD")
        self.save_btn.setObjectName("primaryMinimalButton")
        self.save_btn.clicked.connect(self.accept)
        self.save_btn.setEnabled(False)

        button_layout.addStretch()
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(self.save_btn)

        container_layout.addLayout(button_layout)

        # Pre-fill values for edit mode
        if self.initial_scan:
            self.name_input.setText(self.initial_scan.get("name", ""))
            self.url_input.setPlainText(self.initial_scan.get("url", ""))
            self.tag_input.setCurrentText(self.initial_scan.get("tag", "Others"))

        # Connect validation
        self.name_input.textChanged.connect(self._validate_inputs)
        self.url_input.textChanged.connect(self._validate_inputs)
        self._validate_inputs()

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
        self.setStyleSheet(f"""
            QWidget#dialogContainer {{
                background-color: {_BG1};
                border: 1px solid {_BG4};
                border-radius: 2px;
            }}

            QLabel#dialogTitle {{
                color: {_T0};
                font-family: {_SANS};
                font-size: 11px;
                font-weight: 600;
                letter-spacing: 0.9px;
                background: transparent;
            }}

            QPushButton#closeButton {{
                background: transparent;
                color: {_T2};
                border: none;
                border-radius: 2px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton#closeButton:hover {{
                background-color: rgba(255,77,106,0.10);
                color: {_BEAR};
            }}

            QGroupBox#formGroup {{
                background-color: {_BG2};
                border: 1px solid {_BG4};
                border-radius: 2px;
                color: {_T2};
                font-family: {_SANS};
                font-size: 9px;
                font-weight: 600;
                letter-spacing: 0.5px;
                margin-top: 8px;
                padding-top: 8px;
            }}
            QGroupBox#formGroup::title {{
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
                color: {_AMBER};
                background: transparent;
            }}

            QLabel#fieldLabel {{
                color: {_T2};
                font-family: {_SANS};
                font-size: 9px;
                font-weight: 600;
                letter-spacing: 0.6px;
                background: transparent;
                text-transform: uppercase;
            }}

            QLineEdit#minimalInput,
            QTextEdit#minimalTextArea,
            QComboBox#minimalInput {{
                background-color: {_BG1};
                border: 1px solid {_BG4};
                border-radius: 2px;
                color: {_T0};
                padding: 5px 8px;
                font-size: 11px;
                font-family: {_SANS};
                selection-background-color: {_SEL};
                selection-color: {_T0};
            }}
            QTextEdit#minimalTextArea {{
                font-family: {_MONO};
                font-size: 10px;
            }}
            QLineEdit#minimalInput:focus,
            QTextEdit#minimalTextArea:focus,
            QComboBox#minimalInput:focus {{
                border: 1px solid {_CYAN};
                background-color: {_BG3};
            }}
            QLineEdit#minimalInput::placeholder,
            QTextEdit#minimalTextArea::placeholder {{
                color: {_T3};
            }}
            QComboBox#minimalInput::drop-down {{
                border: none;
                width: 18px;
            }}
            QComboBox#minimalInput::down-arrow {{
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid {_T2};
                margin-right: 5px;
            }}
            QComboBox#minimalInput QAbstractItemView {{
                background: {_BG1};
                color: {_T0};
                border: 1px solid {_BG4};
                selection-background-color: {_SEL};
                outline: none;
            }}

            QCheckBox {{
                color: {_T1};
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 400;
                background: transparent;
            }}

            QPushButton#primaryMinimalButton,
            QPushButton#secondaryMinimalButton {{
                border-radius: 2px;
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 0.5px;
                padding: 5px 12px;
                min-width: 72px;
                min-height: 22px;
            }}
            QPushButton#primaryMinimalButton {{
                background-color: rgba(0,212,168,0.08);
                color: {_BULL};
                border: 1px solid rgba(0,212,168,0.22);
            }}
            QPushButton#primaryMinimalButton:hover {{
                background-color: rgba(0,212,168,0.12);
                border-color: {_BULL};
            }}
            QPushButton#primaryMinimalButton:disabled {{
                background-color: {_BG2};
                color: {_T3};
                border: 1px solid {_BG4};
            }}
            QPushButton#secondaryMinimalButton {{
                background-color: {_BG2};
                color: {_T1};
                border: 1px solid {_BG4};
            }}
            QPushButton#secondaryMinimalButton:hover {{
                background-color: {_BG3};
                color: {_T0};
                border-color: {_T2};
            }}

            QScrollBar:vertical {{
                background: transparent;
                width: 4px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {_BG4};
                border-radius: 2px;
                min-height: 18px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0;
                border: none;
            }}
        """)


class ModernManageScansDialog(QDialog):
    """Enhanced dialog for managing existing scans."""

    def __init__(self, scans: List[Dict[str, str]], parent=None):
        super().__init__(parent)
        self.scans = scans.copy()
        self.setWindowTitle("Manage Chartink Scans")
        self.setModal(True)
        self.setFixedSize(760, 500)
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
        container_layout.setContentsMargins(12, 10, 12, 12)
        container_layout.setSpacing(8)

        # Header
        header_layout = QHBoxLayout()
        header_layout.setSpacing(12)

        title_stack = QVBoxLayout()
        title_stack.setSpacing(2)

        title_label = QLabel("MANAGE SCANS")
        title_label.setObjectName("dialogTitle")

        subtitle_label = QLabel("Saved Chartink scan clauses and groups")
        subtitle_label.setObjectName("dialogSubtitle")

        title_stack.addWidget(title_label)
        title_stack.addWidget(subtitle_label)

        self.add_btn = QPushButton("+ ADD")
        self.add_btn.setObjectName("addMinimalButton")
        self.add_btn.setFixedHeight(24)
        self.add_btn.clicked.connect(self._add_scan)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(22, 22)
        close_btn.clicked.connect(self.reject)

        header_layout.addLayout(title_stack)
        header_layout.addStretch()
        header_layout.addWidget(self.add_btn)
        header_layout.addWidget(close_btn)

        container_layout.addLayout(header_layout)

        # Scans table
        self.scans_table = QTableWidget()
        self.scans_table.setObjectName("minimalTable")
        self.scans_table.setColumnCount(4)
        self.scans_table.setHorizontalHeaderLabels(["SCAN", "TAG", "CLAUSE", ""])

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
        self.scans_table.verticalHeader().setDefaultSectionSize(_DIALOG_ROW_H)
        self.scans_table.verticalHeader().setMinimumSectionSize(_DIALOG_ROW_H)
        self.scans_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.scans_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.scans_table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        container_layout.addWidget(self.scans_table)

        # Bottom buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(6)

        info_label = QLabel(f"SCANS: {len(self.scans)}")
        info_label.setObjectName("infoLabel")

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setObjectName("secondaryMinimalButton")
        cancel_btn.clicked.connect(self.reject)

        self.save_btn = QPushButton("SAVE")
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
            name_item.setFont(_ui_font(9, QFont.Weight.Medium))
            name_item.setForeground(QBrush(QColor(_T0)))
            self.scans_table.setItem(row, 0, name_item)

            # Tag
            tag_item = QTableWidgetItem(scan.get("tag", "Others"))
            tag_item.setForeground(QBrush(QColor("#a8bcd4")))
            self.scans_table.setItem(row, 1, tag_item)

            # Clause preview (truncated)
            clause = scan.get("url", "")
            preview = clause[:72] + "..." if len(clause) > 72 else clause
            preview_item = QTableWidgetItem(preview)
            preview_item.setFont(_mono_font(8))
            preview_item.setForeground(QBrush(QColor(_T2)))
            self.scans_table.setItem(row, 2, preview_item)

            # Actions buttons
            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            actions_layout.setSpacing(6)

            edit_btn = QPushButton("✎")
            edit_btn.setObjectName("editMinimalButton")
            edit_btn.setFixedSize(22, 22)
            edit_btn.setToolTip("Edit this scan")
            edit_btn.clicked.connect(lambda checked, r=row: self._edit_scan(r))
            actions_layout.addWidget(edit_btn)

            delete_btn = QPushButton("✕")  # Just the delete icon, no text
            delete_btn.setObjectName("deleteMinimalButton")
            delete_btn.setFixedSize(22, 22)  # Small square button
            delete_btn.setToolTip("Delete this scan")  # Helpful tooltip
            delete_btn.clicked.connect(lambda checked, r=row: self._delete_scan(r))
            actions_layout.addWidget(delete_btn)

            self.scans_table.setCellWidget(row, 3, actions_widget)

        # Adjust row heights
        for row in range(len(self.scans)):
            self.scans_table.setRowHeight(row, _DIALOG_ROW_H)

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
                info_label.setText(f"SCANS: {len(self.scans)}")

    def _edit_scan(self, row: int):
        """Edit an existing scan at the given row."""
        if not (0 <= row < len(self.scans)):
            return

        dialog = ModernAddScanDialog(self, initial_scan=self.scans[row], is_edit=True)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.scans[row] = dialog.get_scan_data()
            self._populate_scans()

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
                    info_label.setText(f"SCANS: {len(self.scans)}")

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
        self.setStyleSheet(f"""
            QWidget#dialogContainer {{
                background-color: {_BG1};
                border: 1px solid {_BG4};
                border-radius: 2px;
            }}

            QLabel#dialogTitle {{
                color: {_T0};
                font-family: {_SANS};
                font-size: 11px;
                font-weight: 600;
                letter-spacing: 0.9px;
                background: transparent;
            }}
            QLabel#dialogSubtitle {{
                color: {_T2};
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 400;
                background: transparent;
            }}

            QPushButton#addMinimalButton {{
                background-color: rgba(0,212,168,0.08);
                color: {_BULL};
                border: 1px solid rgba(0,212,168,0.22);
                border-radius: 2px;
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 0.5px;
                padding: 2px 10px;
            }}
            QPushButton#addMinimalButton:hover {{
                background-color: rgba(0,212,168,0.12);
                border-color: {_BULL};
            }}

            QPushButton#editMinimalButton {{
                background-color: {_BG2};
                color: {_CYAN};
                border: 1px solid rgba(0,212,255,0.20);
                border-radius: 2px;
                font-size: 10px;
                font-weight: 600;
            }}
            QPushButton#editMinimalButton:hover {{
                background-color: rgba(0,212,255,0.08);
                border-color: {_CYAN};
                color: {_T0};
            }}

            QPushButton#closeButton {{
                background: transparent;
                color: {_T2};
                border: none;
                border-radius: 2px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton#closeButton:hover {{
                background-color: rgba(255,77,106,0.10);
                color: {_BEAR};
            }}

            QTableWidget#minimalTable {{
                background-color: {_BG1};
                alternate-background-color: {_BG2};
                border: 1px solid {_BG4};
                gridline-color: transparent;
                selection-background-color: {_SEL};
                selection-color: {_T0};
                color: {_T1};
                outline: none;
                font-family: {_SANS};
                font-size: 10px;
                border-radius: 2px;
            }}
            QTableWidget#minimalTable::item {{
                padding: 0 6px;
                border-bottom: 1px solid {_BG3};
                background-color: transparent;
            }}
            QTableWidget#minimalTable::item:hover {{
                background-color: {_BG3};
            }}
            QTableWidget#minimalTable::item:selected {{
                background-color: {_SEL};
                color: {_T0};
            }}

            QHeaderView::section {{
                background-color: {_BG2};
                color: {_T2};
                padding: 0 6px;
                border: none;
                border-bottom: 1px solid {_BG4};
                font-family: {_SANS};
                font-weight: 600;
                font-size: 9px;
                letter-spacing: 0.6px;
                text-transform: uppercase;
                min-height: 20px;
            }}
            QHeaderView::section:hover {{
                color: {_T1};
                background-color: {_BG3};
            }}

            QPushButton#deleteMinimalButton {{
                background-color: {_BG2};
                color: {_BEAR};
                border: 1px solid rgba(255,77,106,0.20);
                border-radius: 2px;
                font-size: 11px;
                font-weight: 600;
                padding: 0px;
                margin: 0px;
            }}
            QPushButton#deleteMinimalButton:hover {{
                background-color: rgba(255,77,106,0.08);
                border-color: {_BEAR};
            }}
            QPushButton#deleteMinimalButton:pressed {{
                background-color: rgba(255,77,106,0.14);
                border-color: {_BEAR};
            }}

            QLabel#infoLabel {{
                color: {_T2};
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 500;
                background: transparent;
            }}

            QPushButton#primaryMinimalButton,
            QPushButton#secondaryMinimalButton {{
                border-radius: 2px;
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 0.5px;
                padding: 5px 12px;
                min-width: 72px;
                min-height: 22px;
            }}
            QPushButton#primaryMinimalButton {{
                background-color: rgba(0,212,168,0.08);
                color: {_BULL};
                border: 1px solid rgba(0,212,168,0.22);
            }}
            QPushButton#primaryMinimalButton:hover {{
                background-color: rgba(0,212,168,0.12);
                border-color: {_BULL};
            }}
            QPushButton#secondaryMinimalButton {{
                background-color: {_BG2};
                color: {_T1};
                border: 1px solid {_BG4};
            }}
            QPushButton#secondaryMinimalButton:hover {{
                background-color: {_BG3};
                color: {_T0};
                border-color: {_T2};
            }}

            QScrollBar:vertical {{
                background: transparent;
                width: 4px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {_BG4};
                border-radius: 2px;
                min-height: 18px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {_T2};
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0;
                border: none;
            }}
            QScrollBar:horizontal {{
                background: transparent;
                height: 4px;
                border: none;
            }}
            QScrollBar::handle:horizontal {{
                background: {_BG4};
                border-radius: 2px;
                min-width: 18px;
            }}
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {{
                width: 0;
                border: none;
            }}
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
        self._live_ticks_enabled: bool = True
        self._dropdown_scan_indices: List[int] = []
        self._current_symbol_index = 0  # Track current symbol for spacebar navigation
        self._last_visible_tokens: set = set()  # track to avoid redundant re-subs
        self._change_sort_state: Optional[str] = None  # None -> asc -> desc -> None
        self._color_theme = {
            "enable_volume_strength_indicator": False,
            "show_table_vertical_lines": False,
            "tables": {"positive": "#00d4a8", "negative": "#ff4d6a", "neutral": "#5a7090", "volume": "#00d4ff"}
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
        self.table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        self.table.setItemDelegateForColumn(2, VolumeStrengthDelegate(self.table))
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.table.setFocus()

        # Preserve scanner row selection while the user works on the chart.
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
        self._apply_enhanced_styles()
        self.table.setShowGrid(bool(self._color_theme.get("show_table_vertical_lines", False)))
        self.table.setColumnHidden(2, not bool(self._color_theme.get("show_scanner_volume_column", True)))
        for symbol, row in self._symbol_to_row.items():
            data = self._symbol_data.get(symbol)
            if data is not None:
                self._update_row_data(row, data)

    def set_live_ticks_enabled(self, enabled: bool) -> None:
        self._live_ticks_enabled = enabled
        if not enabled:
            self._dirty_symbols.clear()

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
        """Keep the scanner selection visible when focus moves to the chart."""
        try:
            QTableWidget.focusOutEvent(self.table, event)
        except Exception as e:
            logger.debug(f"Error preserving selection on focus out: {e}")

    def _create_header(self) -> QWidget:
        """Creates the header with scan selection."""
        header_container = QWidget()
        header_container.setObjectName("headerContainer")
        header_container.setFixedHeight(CHART_TOOLBAR_HEIGHT)

        header_layout = QHBoxLayout(header_container)
        header_layout.setContentsMargins(4, 0, 4, 0)
        header_layout.setSpacing(4)

        # Subtle refresh button replacing static scan label
        self.scan_refresh_btn = QPushButton("RUN")
        self.scan_refresh_btn.setObjectName("scanRefreshButton")
        self.scan_refresh_btn.setToolTip("Refresh current scan")
        self.scan_refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.scan_refresh_btn.setFixedSize(44, CHART_TOOLBAR_CONTROL_HEIGHT)
        self.scan_refresh_btn.clicked.connect(self._run_current_scan)
        header_layout.addWidget(self.scan_refresh_btn)

        # Dropdown
        self.scan_dropdown = QComboBox()
        self.scan_dropdown.setObjectName("minimalDropdown")
        self.scan_dropdown.setFixedHeight(CHART_TOOLBAR_CONTROL_HEIGHT)
        self.scan_dropdown.currentIndexChanged.connect(self._on_scan_selection_changed)
        header_layout.addWidget(self.scan_dropdown, 1)

        # Settings button
        self.manage_btn = QPushButton()
        self.manage_btn.setObjectName("settingsMinimalButton")
        self.manage_btn.setToolTip("Manage Scans")
        self.manage_btn.setFixedSize(24, CHART_TOOLBAR_CONTROL_HEIGHT)
        gear_icon_path = get_asset_path("icons", "gear_setting.svg", required=True)
        if gear_icon_path is not None:
            self.manage_btn.setIcon(QIcon(str(gear_icon_path)))
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
            "day trade": "Intraday",
            "day trading": "Intraday",
            "intra day": "Intraday",
            "intraday scan": "Intraday",
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
        """TC2000 style compact table configuration."""
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["SYMBOL", "PRICE", "VOL", "CHG%"] )

        self.table.horizontalHeader().setVisible(True)
        header = self.table.horizontalHeader()

        # THE FIX: Native Qt sizing for ultimate density
        # Symbol absorbs empty space and shrinks first. Data columns perfectly fit contents.
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        # Prevent columns from disappearing entirely if crushed
        header.setMinimumSectionSize(35)
        header.setStretchLastSection(False)

        self.table.verticalHeader().setVisible(False)

        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(bool(self._color_theme.get("show_table_vertical_lines", False)))
        self.table.setAlternatingRowColors(True)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        # Ultra-compact row heights (TC2000 style)
        self.table.verticalHeader().setDefaultSectionSize(_ROW_H)
        self.table.verticalHeader().setMinimumSectionSize(_ROW_H)

        header_font = _ui_font(8, QFont.Weight.Medium)
        self.table.horizontalHeader().setFont(header_font)
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self.table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)

        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.table.setColumnHidden(2, not bool(self._color_theme.get("show_scanner_volume_column", True)))

    def _on_header_clicked(self, section: int) -> None:
        """Toggle tri-state sorting for %CHG column when header is clicked."""
        if section != 3:
            return

        if self._change_sort_state is None:
            self._change_sort_state = "asc"
        elif self._change_sort_state == "asc":
            self._change_sort_state = "desc"
        else:
            self._change_sort_state = None

        self._apply_table_ordering()

    def _apply_table_ordering(self) -> None:
        """Rebuild table rows based on current sort mode."""
        symbols = list(self._symbol_data.keys())
        if not symbols:
            self.table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
            return

        if self._change_sort_state == "asc":
            symbols.sort(key=lambda s: float(self._symbol_data.get(s, {}).get("change_pct", 0.0) or 0.0))
            self.table.horizontalHeader().setSortIndicator(3, Qt.SortOrder.AscendingOrder)
        elif self._change_sort_state == "desc":
            symbols.sort(key=lambda s: float(self._symbol_data.get(s, {}).get("change_pct", 0.0) or 0.0), reverse=True)
            self.table.horizontalHeader().setSortIndicator(3, Qt.SortOrder.DescendingOrder)
        else:
            symbols.sort(key=lambda s: s)
            self.table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)

        self.table.setRowCount(len(symbols))
        self._symbol_to_row.clear()
        for row, symbol in enumerate(symbols):
            self._symbol_to_row[symbol] = row
            for col in range(4):
                if not self.table.item(row, col):
                    self.table.setItem(row, col, QTableWidgetItem())
            self._update_row_data(row, self._symbol_data[symbol])


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
            self._color_theme.get("tables", {}).get("volume", _CYAN)
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

        # Watchlist-matched color coding
        if change_pct >= 3.0:
            chg_fg = QColor(_BULL)
            chg_bg = QBrush(QColor(0, 212, 168, 26))
        elif change_pct >= 1.0:
            chg_fg = QColor("#35e0bd")
            chg_bg = QBrush(QColor(0, 212, 168, 16))
        elif change_pct >= -0.5:
            chg_fg = QColor(_T2)
            chg_bg = QBrush(QColor(_BG2))
        elif change_pct >= -1.0:
            chg_fg = QColor("#ff8a9a")
            chg_bg = QBrush(QColor(255, 77, 106, 16))
        else:
            chg_fg = QColor(_BEAR)
            chg_bg = QBrush(QColor(255, 77, 106, 26))

        # Match embedded watchlist column palette
        symbol_item.setForeground(QColor(_SYMBOL_TEXT))
        price_item.setForeground(chg_fg if abs(change_pct) > 0.005 else QColor(_T0))
        volume_item.setForeground(QColor(_T2))
        change_pct_item.setForeground(chg_fg)
        change_pct_item.setBackground(chg_bg)

        # Set text alignments and modern UI number typography.
        symbol_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        price_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        volume_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        change_pct_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # Keep symbols on a dedicated compact font path; use modern UI numbers for price/volume/change.
        symbol_font = _symbol_font(10, QFont.Weight.Normal)
        value_font = _number_font(9, QFont.Weight.Normal)
        change_font = _number_font(9, QFont.Weight.Medium)
        symbol_item.setFont(symbol_font)
        price_item.setFont(value_font)
        volume_item.setFont(value_font)
        change_pct_item.setFont(change_font)

        base_bg = QBrush(QColor(_BG1 if row % 2 == 0 else _BG2))
        symbol_item.setBackground(base_bg)
        price_item.setBackground(base_bg)
        volume_item.setBackground(base_bg)

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
            for result in scan_results:
                symbol = result.get('symbol', '')
                if not symbol:
                    continue

                self._symbol_data[symbol] = result
            self._apply_table_ordering()

            # Select first row automatically
            if len(scan_results) > 0:
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
                item = QTableWidgetItem("No scans configured. Open settings to add one.")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(0, 0, item)
                for col in range(1, 4):
                    self.table.setItem(0, col, QTableWidgetItem(""))
            else:
                # FIXED: Don't auto-run scan after saving changes
                # Just clear the table and show a message to manually select/run
                self.table.setRowCount(0)
                self.table.insertRow(0)
                item = QTableWidgetItem("Scans saved. Select a scan to run.")
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
            item = QTableWidgetItem("Invalid scan configuration.")
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
        item = QTableWidgetItem("Running scan...")
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
                if symbol_text and not symbol_text.startswith(("Error:", "Loading", "No symbols", "No scans")):
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
        """Get list of current symbols in the CURRENT visual table order."""
        symbols: List[str] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if not item:
                continue
            symbol = item.text().strip()
            if symbol and symbol in self._symbol_data:
                symbols.append(symbol)
        return symbols

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
        if not self._live_ticks_enabled:
            return

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

                # NOTE: Do not use `or` here because a valid 0.0 change gets treated as falsey.
                chg = tick.get('change_percent')
                if chg is None:
                    chg = tick.get('net_change_percent')

                if chg is not None:
                    incoming_chg = float(chg)
                    existing_chg = float(data.get('change_pct', 0.0) or 0.0)

                    # Some feeds briefly send 0.0 during bootstrap; preserve already-known
                    # non-zero EOD change to avoid flickering everything to neutral gray.
                    if abs(incoming_chg) > 1e-9 or abs(existing_chg) <= 0.01:
                        data['change_pct'] = incoming_chg
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
        """AMOLED Institutional Dark Trading Terminal UI styling."""
        gridline_color = "rgba(26,32,48,0.42)" if self._color_theme.get("show_table_vertical_lines", False) else "transparent"

        self.setStyleSheet(f"""
            QWidget {{
                background-color: {_BG0};
                color: {_T0};
                font-family: {_SANS};
                font-size: 11px;
            }}

            QWidget#headerContainer {{
                background-color: {_BGTB};
                border-bottom: 1px solid {_BG4};
                min-height: {CHART_TOOLBAR_HEIGHT}px;
                max-height: {CHART_TOOLBAR_HEIGHT}px;
                padding: 0px;
            }}

            QPushButton#scanRefreshButton {{
                background-color: rgba(0, 212, 255, 0.055);
                color: {_CYAN};
                border: 1px solid rgba(0, 212, 255, 0.18);
                border-radius: 2px;
                font-family: {_SANS};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 0.7px;
                padding: 0px;
                text-align: center;
                min-width: 44px;
                max-width: 44px;
                min-height: {CHART_TOOLBAR_CONTROL_HEIGHT}px;
                max-height: {CHART_TOOLBAR_CONTROL_HEIGHT}px;
            }}
            QPushButton#scanRefreshButton:hover {{
                background-color: rgba(0, 212, 255, 0.10);
                border-color: rgba(0, 212, 255, 0.42);
                color: {_T0};
            }}
            QPushButton#scanRefreshButton:pressed {{
                background-color: rgba(0, 212, 255, 0.16);
                border-color: {_CYAN};
            }}
            QPushButton#scanRefreshButton:disabled {{
                background-color: {_BG1};
                color: {_T3};
                border-color: {_BG4};
            }}

            QComboBox#minimalDropdown {{
                background-color: {_BG1};
                color: {_T0};
                border: 1px solid {_BG4};
                border-radius: 2px;
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 650;
                min-height: {CHART_TOOLBAR_CONTROL_HEIGHT}px;
                max-height: {CHART_TOOLBAR_CONTROL_HEIGHT}px;
                padding: 0px 20px 0px 7px;
                selection-background-color: {_SEL};
                selection-color: {_T0};
            }}
            QComboBox#minimalDropdown:hover {{
                border-color: {_BG5};
                background-color: {_BG2};
            }}
            QComboBox#minimalDropdown:focus {{
                border-color: {_CYAN};
                background-color: {_BG2};
                outline: none;
            }}
            QComboBox#minimalDropdown:disabled {{
                background-color: {_BG1};
                color: {_T3};
                border-color: {_BG4};
            }}
            QComboBox#minimalDropdown::drop-down {{
                border: none;
                width: 18px;
                background: transparent;
            }}
            QComboBox#minimalDropdown::down-arrow {{
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid {_T2};
                margin-right: 5px;
            }}
            QComboBox#minimalDropdown QAbstractItemView {{
                background-color: {_BG1};
                color: {_T0};
                border: 1px solid {_BG4};
                selection-background-color: {_SEL};
                selection-color: {_T0};
                outline: none;
                padding: 2px;
                font-family: {_SANS};
                font-size: 10px;
            }}
            QComboBox#minimalDropdown QAbstractItemView::item {{
                min-height: 20px;
                padding: 2px 7px;
                border: none;
            }}
            QComboBox#minimalDropdown QAbstractItemView::item:hover {{
                background-color: {_BG3};
            }}

            QPushButton#settingsMinimalButton {{
                background-color: {_BG1};
                color: {_T2};
                border: 1px solid {_BG4};
                border-radius: 2px;
                min-width: 24px;
                max-width: 24px;
                min-height: {CHART_TOOLBAR_CONTROL_HEIGHT}px;
                max-height: {CHART_TOOLBAR_CONTROL_HEIGHT}px;
                padding: 0px;
            }}
            QPushButton#settingsMinimalButton:hover {{
                background-color: rgba(0, 212, 255, 0.08);
                color: {_CYAN};
                border-color: rgba(0, 212, 255, 0.34);
            }}
            QPushButton#settingsMinimalButton:pressed {{
                background-color: {_BG3};
                border-color: {_CYAN};
            }}
            QPushButton#settingsMinimalButton:disabled {{
                background-color: {_BG1};
                color: {_T3};
                border-color: {_BG4};
            }}

            QTableWidget {{
                background-color: {_BG1};
                alternate-background-color: {_BG2};
                border: none;
                gridline-color: {gridline_color};
                selection-background-color: {_SEL};
                selection-color: {_T0};
                color: {_T0};
                outline: none;
                show-decoration-selected: 0;
                font-family: {_NUM};
                font-size: 10px;
                border-radius: 0px;
            }}
            QTableWidget::item {{
                padding: 0px 5px;
                border-bottom: 1px solid rgba(26, 32, 48, 0.38);
                background-color: transparent;
                font-family: {_NUM};
                font-size: 10px;
            }}
            QTableWidget::item:selected {{
                background-color: {_SEL} !important;
                color: {_T0};
                font-weight: 500;
                outline: none;
            }}
            QTableWidget::item:focus {{
                background-color: {_SEL} !important;
                color: {_T0};
                outline: none;
            }}
            QTableWidget::item:hover {{
                background-color: {_BG3};
            }}
            QTableWidget::item:alternate {{
                background-color: {_BG2};
            }}
            QTableWidget::item:alternate:selected {{
                background-color: {_SEL} !important;
                color: {_T0};
            }}

            QHeaderView::section {{
                background-color: {_BG2};
                color: {_T2};
                padding: 0px 5px;
                border: none;
                border-bottom: 1px solid {_BG4};
                font-family: {_SANS};
                font-weight: 800;
                font-size: 9px;
                letter-spacing: 0.8px;
                text-transform: uppercase;
                min-height: 19px;
            }}
            QHeaderView::section:hover {{
                background-color: {_BG3};
                color: {_T1};
            }}

            QTableCornerButton::section {{
                background-color: {_BG2};
                border: none;
                border-bottom: 1px solid {_BG4};
            }}

            QScrollBar:vertical {{
                background: transparent;
                width: 4px;
                border: none;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background-color: {_BG4};
                border-radius: 2px;
                min-height: 18px;
            }}
            QScrollBar::handle:vertical:hover {{
                background-color: {_T2};
            }}
            QScrollBar:horizontal {{
                background: transparent;
                height: 4px;
                border: none;
                margin: 0px;
            }}
            QScrollBar::handle:horizontal {{
                background-color: {_BG4};
                border-radius: 2px;
                min-width: 18px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background-color: {_T2};
            }}
            QScrollBar::add-line,
            QScrollBar::sub-line {{
                border: none;
                background: none;
                width: 0px;
                height: 0px;
                margin: 0px;
            }}

            QToolTip {{
                background-color: {_BG2};
                color: {_T1};
                border: 1px solid {_BG5};
                border-radius: 2px;
                padding: 4px 6px;
                font-family: {_SANS};
                font-size: 10px;
            }}
        """)
