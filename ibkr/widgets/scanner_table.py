# ibkr/widgets/scanner_table.py
import logging
import json
import os
import requests
from bs4 import BeautifulSoup as bs
from typing import List, Dict, Optional, Any
from ibkr.scanner.run_finviz_scan import quick_scrape

from PySide6.QtCore import Signal, Slot, Qt, QThread, QTimer, QSize, QByteArray
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QPushButton, QHBoxLayout, QLabel, QComboBox, QMessageBox,
    QDialog, QLineEdit, QGroupBox, QTextEdit,
    QStyledItemDelegate, QStyleOptionViewItem, QApplication, QStyle, QSizePolicy
)
from PySide6.QtGui import QColor, QFont, QBrush, QCursor, QFontMetrics, QIcon
from PySide6.QtCore import QItemSelectionModel
from app_paths import get_asset_path, get_user_data_dir

logger = logging.getLogger(__name__)


def _prefer_text_antialias(font: QFont) -> QFont:
    """Prefer antialiased glyph rasterization for crisper HiDPI text."""
    try:
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    except Exception:
        pass
    return font
_APP_DIR = str(get_user_data_dir("ibkr", os.environ.get("QULLAMAGGIE_TRADING_MODE", "live")))
SCAN_URL_FILE = os.path.join(_APP_DIR, "finviz_scans.json")
SETTINGS_FILE = os.path.join(_APP_DIR, "scanner_settings.json")
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

# Compact scanner columns. Keep SYMBOL intentionally bounded so it cannot
# stretch the scanner pane/splitter to the right.
_SCANNER_COL_DEFAULTS = [74, 60, 64, 54]  # SYMBOL, PRICE, VOL, CHG%
_SCANNER_COL_LIMITS = [
    (56, 96),   # SYMBOL
    (48, 78),   # PRICE
    (54, 96),   # VOL
    (48, 68),   # CHG%
]



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




def _coerce_change_pct(raw_value: Any) -> float:
    """Normalize scanner change values from different key formats into float percent."""
    if raw_value is None:
        return 0.0
    if isinstance(raw_value, (int, float)):
        return float(raw_value)

    token = str(raw_value).strip()
    if not token:
        return 0.0

    token = token.replace('%', '').replace(',', '').strip()
    if token.startswith('(') and token.endswith(')'):
        token = '-' + token[1:-1].strip()
    if token == '--':
        return 0.0

    try:
        return float(token)
    except (TypeError, ValueError):
        return 0.0

def _volume_strength_level(volume: int) -> int:
    if volume >= 5_000_000:
        return 3
    if volume >= 1_000_000:
        return 2
    if volume >= 250_000:
        return 1
    return 0


class ModernAddScanDialog(QDialog):
    """Enhanced dialog for adding new Finviz scans with modern styling."""

    def __init__(self, parent=None, initial_scan: Optional[Dict[str, str]] = None, is_edit: bool = False):
        super().__init__(parent)
        self.initial_scan = initial_scan or {}
        self.is_edit = is_edit

        self.setWindowTitle("Edit Finviz Scan" if self.is_edit else "Add New Finviz Scan")
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

        # Finviz scan URL
        clause_label = QLabel("FINVIZ SCAN LINK")
        clause_label.setObjectName("fieldLabel")
        self.url_input = QTextEdit()
        self.url_input.setObjectName("minimalTextArea")
        self.url_input.setPlaceholderText("Paste your Finviz screener URL here...")
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
        stylesheet = f"""
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
        """
        self.setStyleSheet(stylesheet)


class ModernManageScansDialog(QDialog):
    """Enhanced dialog for managing existing scans."""

    def __init__(self, scans: List[Dict[str, str]], parent=None):
        super().__init__(parent)
        self.scans = scans.copy()
        self.setWindowTitle("Manage Finviz Scans")
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

        subtitle_label = QLabel("Saved Finviz scan links and groups")
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
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.scans_table.setColumnWidth(3, 64)

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
            actions_layout.setContentsMargins(2, 0, 2, 0)
            actions_layout.setSpacing(6)
            actions_layout.setAlignment(Qt.AlignCenter)

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
    """Worker thread for Finviz scans - returns symbol-first EOD rows."""
    scan_completed = Signal(list)  # Emits list of complete symbol data
    scan_error = Signal(str)

    def __init__(self, scan_url: str):
        super().__init__()
        self.scan_url = scan_url

    def run(self):
        try:
            url = self.scan_url.strip()
            if not url:
                raise Exception("No Finviz scan URL provided")

            tickers = quick_scrape(url)
            scan_results = []
            for row in tickers:
                if isinstance(row, dict):
                    symbol = str(row.get('symbol', row.get('ticker', ''))).strip().upper()
                    price = float(row.get('price', row.get('Price', 0.0)) or 0.0)
                    change_pct_raw = (
                        row.get('change_pct', row.get('change', row.get('Change', row.get('chg_pct', row.get('CHG%', 0.0)))))
                    )
                    change_pct = _coerce_change_pct(change_pct_raw)
                    volume = int(row.get('volume', row.get('Volume', 0)) or 0)
                else:
                    symbol = str(row).strip().upper()
                    price = 0.0
                    change_pct = 0.0
                    volume = 0

                if not symbol:
                    continue
                symbol_data = {
                    'symbol': symbol,
                    'name': symbol,
                    'price': price,
                    'change_pct': change_pct,
                    'volume': volume,
                    '_raw_data': {'source': 'finviz', 'url': url},
                }
                scan_results.append(symbol_data)

            logger.info(f"EOD Scan completed: {len(scan_results)} symbols with complete data")
            self.scan_completed.emit(scan_results)

        except Exception as e:
            logger.error(f"ScanWorker failed: {e}", exc_info=True)
            self.scan_error.emit(str(e))


# ibkr/widgets/scanner_table.py
import logging
import json
import os
import requests
from bs4 import BeautifulSoup as bs
from typing import List, Dict, Optional, Any
from ibkr.scanner.run_finviz_scan import quick_scrape

from PySide6.QtCore import Signal, Slot, Qt, QThread, QTimer, QSize, QByteArray
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QPushButton, QHBoxLayout, QLabel, QComboBox, QMessageBox,
    QDialog, QLineEdit, QGroupBox, QTextEdit,
    QStyledItemDelegate, QStyleOptionViewItem, QApplication, QStyle, QSizePolicy
)
from PySide6.QtGui import QColor, QFont, QBrush, QCursor, QFontMetrics, QIcon
from PySide6.QtCore import QItemSelectionModel
from app_paths import get_asset_path, get_user_data_dir

logger = logging.getLogger(__name__)


def _prefer_text_antialias(font: QFont) -> QFont:
    """Prefer antialiased glyph rasterization for crisper HiDPI text."""
    try:
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    except Exception:
        pass
    return font


_APP_DIR = str(get_user_data_dir("ibkr", os.environ.get("QULLAMAGGIE_TRADING_MODE", "live")))
SCAN_URL_FILE = os.path.join(_APP_DIR, "finviz_scans.json")
SETTINGS_FILE = os.path.join(_APP_DIR, "scanner_settings.json")
SCAN_GROUP_ORDER = ["Momentum Breakouts", "Episodic Pivot", "Top Gainers", "High Volume"]

CHART_TOOLBAR_HEIGHT = 32
CHART_TOOLBAR_CONTROL_HEIGHT = 24
_ROW_H = 22


def _ui_font(size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont("-apple-system, Segoe UI, Roboto, sans-serif", size, weight)
    return _prefer_text_antialias(f)


class VolumeStrengthDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing, False)

        bg_color = option.palette.color(QPalette.ColorRole.Base)
        if option.state & QStyle.StateFlag.State_Selected:
            bg_color = option.palette.color(QPalette.ColorRole.Highlight)

        painter.fillRect(option.rect, bg_color)

        try:
            val_str = index.data(Qt.ItemDataRole.DisplayRole)
            if val_str:
                val = float(val_str.replace("M", "").replace("K", "").replace("B", ""))
                # Simplistic volume visualizer
                ratio = min(1.0, val / 10.0)

                bar_rect = option.rect.adjusted(2, 2, -2, -2)
                bar_rect.setWidth(int(bar_rect.width() * ratio))

                painter.fillRect(bar_rect, QColor(0, 200, 100, 80))

                text_rect = option.rect.adjusted(4, 0, -4, 0)
                painter.setPen(option.palette.color(QPalette.ColorRole.Text))
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, val_str)
        except Exception:
            super().paint(painter, option, index)

        painter.restore()


class ScanWorker(QThread):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self):
        try:
            results = quick_scrape(self.url)
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class FinvizScannerTable(QWidget):
    symbol_selected = Signal(str)
    visible_rows_changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color_theme = {}
        self._symbol_to_row = {}
        self._symbol_data = {}
        self._scans = self._load_scans()
        self._current_scan_name = ""
        self.worker = None

        self._setup_ui()
        self._apply_enhanced_styles()

        if self._scans:
            first_scan = next(iter(self._scans.keys()))
            self.scan_dropdown.setCurrentText(first_scan)

    def _load_scans(self) -> Dict[str, str]:
        if os.path.exists(SCAN_URL_FILE):
            try:
                with open(SCAN_URL_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load scans: {e}")
        return {"Top Gainers": "https://finviz.com/screener.ashx?v=111&s=ta_topgainers"}

    def _save_scans(self):
        try:
            with open(SCAN_URL_FILE, 'w') as f:
                json.dump(self._scans, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save scans: {e}")

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

        # Setup spacebar shortcut for symbol navigation
        self._setup_keyboard_shortcuts()

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
        self.scan_dropdown.setMinimumWidth(0)
        self.scan_dropdown.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.scan_dropdown.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.scan_dropdown.setMinimumContentsLength(0)
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
            self.manage_btn.setIconSize(QSize(14, 14))
        self.manage_btn.clicked.connect(self._manage_scans)
        header_layout.addWidget(self.manage_btn)

        self._update_scan_dropdown()
        return header_container

    def _configure_table(self):
        """TC2000 style compact table configuration."""
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["SYMBOL", "PRICE", "VOL", "CHG%"])

        self.table.horizontalHeader().setVisible(True)
        header = self.table.horizontalHeader()

        # THE FIX: Native Qt sizing for ultimate density
        # Symbol absorbs empty space and shrinks first. Data columns perfectly fit contents.
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 64)

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

    def apply_color_theme(self, theme: Dict):
        self._color_theme = theme or self._color_theme
        self._apply_enhanced_styles()
        self.table.setShowGrid(bool(self._color_theme.get("show_table_vertical_lines", False)))
        self.table.setColumnHidden(2, not bool(self._color_theme.get("show_scanner_volume_column", True)))
        for symbol, row in self._symbol_to_row.items():
            data = self._symbol_data.get(symbol)
            if data is not None:
                self._update_row_data(row, data)

    def _setup_keyboard_shortcuts(self):
        # Allow navigating scanner list with spacebar (like TC2000)
        pass

    def _on_table_focus_out(self, event):
        # Keep row highlighted even when clicking chart
        self.table.viewport().update()
        QTableWidget.focusOutEvent(self.table, event)

    def _on_scroll_changed(self):
        # Can emit visible rows if you do dynamic subscription
        pass

    def _update_scan_dropdown(self):
        self.scan_dropdown.blockSignals(True)
        self.scan_dropdown.clear()
        for name in self._scans.keys():
            self.scan_dropdown.addItem(name)
        self.scan_dropdown.blockSignals(False)

    def _on_scan_selection_changed(self, index: int):
        if index >= 0:
            name = self.scan_dropdown.itemText(index)
            self._current_scan_name = name
            self._run_current_scan()

    def _run_current_scan(self):
        if not self._current_scan_name:
            return

        url = self._scans.get(self._current_scan_name)
        if not url:
            return

        self.scan_refresh_btn.setText("...")
        self.scan_refresh_btn.setEnabled(False)

        if self.worker and self.worker.isRunning():
            self.worker.terminate()

        self.worker = ScanWorker(url)
        self.worker.finished.connect(self._on_scan_finished)
        self.worker.error.connect(self._on_scan_error)
        self.worker.start()

    def _on_scan_finished(self, symbols: List[str]):
        self.scan_refresh_btn.setText("RUN")
        self.scan_refresh_btn.setEnabled(True)
        self._populate_table(symbols)

    def _on_scan_error(self, err: str):
        self.scan_refresh_btn.setText("RUN")
        self.scan_refresh_btn.setEnabled(True)
        logger.error(f"Scan failed: {err}")

    def _populate_table(self, symbols: List[str]):
        self.table.setRowCount(0)
        self._symbol_to_row.clear()

        for i, sym in enumerate(symbols):
            self.table.insertRow(i)
            self._symbol_to_row[sym] = i

            # Symbol
            item_sym = QTableWidgetItem(sym)
            item_sym.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(i, 0, item_sym)

            # Price, Vol, Chg% - setup empty items to be filled by tick updates
            for col in range(1, 4):
                item = QTableWidgetItem("--")
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(i, col, item)

    def update_tick_data(self, data: Dict[str, Any]):
        symbol = data.get("symbol")
        if not symbol: return

        row = self._symbol_to_row.get(symbol)
        if row is not None:
            self._symbol_data[symbol] = data
            self._update_row_data(row, data)

    def _update_row_data(self, row: int, data: Dict[str, Any]):
        price = data.get("last_price", 0)
        vol = data.get("volume", 0)
        chg = data.get("change_percent", 0)

        # Format and set values
        self.table.item(row, 1).setText(f"{price:.2f}")
        self.table.item(row, 2).setText(f"{vol}")

        chg_item = self.table.item(row, 3)
        chg_item.setText(f"{chg:.2f}%")

        up_color = self._color_theme.get("up_color", "#00E676")
        down_color = self._color_theme.get("down_color", "#FF3B30")
        color = up_color if chg > 0 else down_color if chg < 0 else "#FFFFFF"
        chg_item.setForeground(QColor(color))

    def _on_cell_clicked(self, row: int, column: int):
        sym_item = self.table.item(row, 0)
        if sym_item:
            self.symbol_selected.emit(sym_item.text())

    def _on_header_clicked(self, logical_index):
        pass

    def _manage_scans(self):
        # Implement your scan management dialog here
        pass

    def _apply_enhanced_styles(self):
        _BG1 = self._color_theme.get("bg_color", "#13161E")
        _BG2 = self._color_theme.get("panel_bg", "#1B1E26")
        _BG3 = self._color_theme.get("header_bg", "#1E222D")
        _BG4 = self._color_theme.get("hover_bg", "#262B38")
        _BG5 = self._color_theme.get("border_color", "#2B313F")

        _T1 = self._color_theme.get("text_primary", "#D1D4DC")
        _T2 = self._color_theme.get("text_secondary", "#787B86")
        _T3 = self._color_theme.get("text_muted", "#50535E")
        _SANS = "-apple-system, Segoe UI, Roboto, sans-serif"

        dropdown_icon_url = ""
        icon_path = get_asset_path("icons", "chevron_down.svg")
        if icon_path:
            dropdown_icon_url = str(icon_path).replace('\\', '/')

        stylesheet = f"""
            QWidget#headerContainer {{
                background-color: {_BG2};
                border-bottom: 1px solid {_BG5};
            }}

            QPushButton#scanRefreshButton {{
                background-color: transparent;
                color: {_T2};
                font-family: {_SANS};
                font-size: 11px;
                font-weight: 600;
                letter-spacing: 0.5px;
                border: 1px solid transparent;
                border-radius: 4px;
            }}
            QPushButton#scanRefreshButton:hover {{
                color: #00E676;
                background-color: rgba(0, 230, 118, 0.1);
                border: 1px solid rgba(0, 230, 118, 0.2);
            }}
            QPushButton#scanRefreshButton:pressed {{
                background-color: rgba(0, 230, 118, 0.15);
            }}

            QComboBox#minimalDropdown {{
                background-color: transparent;
                color: {_T1};
                font-family: {_SANS};
                font-size: 13px;
                font-weight: 500;
                border: 1px solid transparent;
                border-radius: 4px;
                padding-left: 4px;
                padding-right: 16px;
            }}
            QComboBox#minimalDropdown:hover {{
                background-color: {_BG4};
                border: 1px solid {_BG5};
            }}
            QComboBox#minimalDropdown::drop-down {{
                border: none;
                width: 16px;
            }}
            QComboBox#minimalDropdown::down-arrow {{
                image: url("{dropdown_icon_url}");
                width: 10px;
                height: 10px;
            }}
            QComboBox#minimalDropdown QAbstractItemView {{
                background-color: {_BG2};
                color: {_T1};
                border: 1px solid {_BG5};
                selection-background-color: {_BG4};
                outline: none;
            }}

            QPushButton#settingsMinimalButton {{
                background-color: transparent;
                border: none;
                border-radius: 4px;
            }}
            QPushButton#settingsMinimalButton:hover {{
                background-color: {_BG4};
            }}

            QTableWidget {{
                background-color: {_BG1};
                color: {_T1};
                border: none;
                outline: none;
                font-family: 'JetBrains Mono', Consolas, monospace;
                font-size: 12px;
                selection-background-color: rgba(0, 230, 118, 0.10);
                selection-color: {_T1};
                alternate-background-color: {_BG2};
            }}

            QTableWidget::item {{
                padding: 0px 4px;
                border-bottom: 1px solid transparent;
            }}
            QTableWidget::item:selected {{
                background-color: rgba(0, 230, 118, 0.10);
                border-bottom: 1px solid rgba(0, 230, 118, 0.3);
            }}

            QHeaderView::section {{
                background-color: {_BG3};
                color: {_T2};
                font-family: {_SANS};
                font-size: 11px;
                font-weight: 500;
                letter-spacing: 0.5px;
                border: none;
                border-right: 1px solid {_BG5};
                border-bottom: 1px solid {_BG5};
                padding: 2px 4px;
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
        """
        self.setStyleSheet(stylesheet.replace("__DROPDOWN_ICON_URL__", dropdown_icon_url))


# Backward-compatible name for older main_window imports.
ChartinkScannerTable = FinvizScannerTable

# Backward-compatible name for older main_window imports.
ChartinkScannerTable = FinvizScannerTable
