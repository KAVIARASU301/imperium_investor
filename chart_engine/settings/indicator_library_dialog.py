from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List
import uuid

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QCursor, QFont, QIcon, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QColorDialog,
    QWidget,
    QHeaderView,
)
from utils.resource_path import resource_path


# -----------------------------------------------------------------------------
# Institutional Dark Trading Terminal UI tokens
# -----------------------------------------------------------------------------

class _C:
    BG0 = "#050709"
    BG1 = "#0a0d12"
    BG2 = "#0f1318"
    BG3 = "#141920"
    BG4 = "#1a2030"
    BGTB = "#070a0f"

    BULL = "#00d4a8"
    BEAR = "#ff4d6a"
    AMBER = "#f59e0b"
    CYAN = "#00d4ff"
    BLUE = "#00d4ff"

    T0 = "#e8f0ff"
    T1 = "#a8bcd4"
    T2 = "#5a7090"
    T3 = "#2a3a50"
    T_SYMBOL = "#b6c4d6"
    SEL = "#1a2840"


# Visible UI uses modern sans / number typography. Monospace is reserved for
# raw logs, code, IDs, scan clauses, and technical debug text only.
_MONO = "Consolas, 'JetBrains Mono', 'Courier New', monospace"
_SANS = "Inter, 'Segoe UI', Arial, sans-serif"
_NUM = "Inter, 'Segoe UI Variable', 'Segoe UI', Arial, sans-serif"
_APP_FONT = "Segoe UI"
_NUM_FONT = "Inter"
_ROW_H = 28
_ACTION_COL_W = 54
_INDEX_COL_W = 34
_SELECTED_NAME_COL_W = 126
_AVAILABLE_NAME_COL_W = 142
_PERIOD_COL_W = 72



@dataclass
class IndicatorCatalogItem:
    type_id: str
    display_name: str
    default_period: int
    default_color: str
    default_thickness: float = 1.2
    default_line_style: str = "solid"


_INDICATOR_CATALOG: List[IndicatorCatalogItem] = [
    IndicatorCatalogItem(type_id="ema", display_name="EMA", default_period=20, default_color="#00d4ff"),
    IndicatorCatalogItem(type_id="sma", display_name="SMA", default_period=20, default_color="#ff9800"),
    IndicatorCatalogItem(type_id="volume", display_name="Volume Bars", default_period=1, default_color="#00c896"),
]


# -----------------------------------------------------------------------------
# Small UI helpers
# -----------------------------------------------------------------------------

_ACTION_ICONS = {
    "add": "add.svg",
    "edit": "edit.svg",
    "danger": "delete.svg",
}


def _icon_only_button(role: str = "neutral", icon_key: str | None = None) -> QPushButton:
    """Create a compact icon-only action button for table rows."""
    btn = QPushButton()
    btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    btn.setFixedSize(22, 22)
    btn.setObjectName(f"{role}ActionButton")
    btn.setText("")
    btn.setToolTipDuration(2000)
    icon_asset = _ACTION_ICONS.get(icon_key or role)
    if icon_asset:
        btn.setIcon(QIcon(resource_path(f"assets/icons/{icon_asset}")))
        btn.setIconSize(QSize(12, 12))
    return btn


def _table_item(
    text: str,
    color: str = _C.T0,
    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft,
    mono: bool = False,
    bold: bool = False,
) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setForeground(QBrush(QColor(color)))
    item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
    # `mono` is retained for API/backward compatibility, but visible table
    # values now use modern UI number typography. Reserve monospace for raw
    # debug/code text only.
    font = QFont(_NUM_FONT if mono else _APP_FONT, 9)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setBold(bold)
    item.setFont(font)
    return item


def _catalog_display_name(type_id: str) -> str:
    return next((c.display_name for c in _INDICATOR_CATALOG if c.type_id == type_id), type_id.upper())


class _ActionCell(QWidget):
    """Transparent action-cell container to keep table row buttons compact."""

    def __init__(self, *buttons: QPushButton, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        # The row action buttons are 22px high. Keep the cell padding modest
        # and let the 28px row height prevent clipping on HiDPI/font-scaled UIs.
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)
        layout.addStretch()
        for button in buttons:
            layout.addWidget(button)
        layout.addStretch()
        self.setStyleSheet("background: transparent;")


# -----------------------------------------------------------------------------
# Indicator settings dialog
# -----------------------------------------------------------------------------

class IndicatorSettingsDialog(QDialog):
    def __init__(self, current: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Indicator Settings")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setModal(True)
        self.setMinimumSize(420, 308)
        self.resize(440, 326)
        self._current = dict(current)
        self._drag_active = False
        self._drag_offset = QPoint()

        self._build_ui()
        self._apply_styles()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)

        self._container = QFrame()
        self._container.setObjectName("settingsContainer")
        outer.addWidget(self._container)

        root = QVBoxLayout(self._container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_title_bar())

        body = QFrame()
        body.setObjectName("settingsBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(10, 10, 10, 8)
        body_layout.setSpacing(8)

        meta = QLabel("CONFIGURE INSTANCE")
        meta.setObjectName("sectionLabel")
        body_layout.addWidget(meta)

        panel = QFrame()
        panel.setObjectName("formPanel")
        form = QFormLayout(panel)
        form.setContentsMargins(10, 8, 10, 10)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.type_combo = QComboBox()
        self.type_combo.setObjectName("terminalCombo")
        for c in _INDICATOR_CATALOG:
            self.type_combo.addItem(c.display_name, c.type_id)
        current_type = str(self._current.get("type", "ema"))
        idx = self.type_combo.findData(current_type)
        self.type_combo.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow(self._field_label("TYPE"), self.type_combo)

        self.period_spin = QSpinBox()
        self.period_spin.setObjectName("terminalSpin")
        self.period_spin.setRange(1, 2000)
        self.period_spin.setValue(int(self._current.get("period", 20) or 20))
        form.addRow(self._field_label("PERIOD"), self.period_spin)

        self.thickness_spin = QDoubleSpinBox()
        self.thickness_spin.setObjectName("terminalSpin")
        self.thickness_spin.setRange(0.5, 10.0)
        self.thickness_spin.setDecimals(1)
        self.thickness_spin.setSingleStep(0.1)
        self.thickness_spin.setValue(float(self._current.get("thickness", 1.2) or 1.2))
        form.addRow(self._field_label("THICKNESS"), self.thickness_spin)

        self.line_style_combo = QComboBox()
        self.line_style_combo.setObjectName("terminalCombo")
        for style in ("solid", "dashed", "dotted"):
            self.line_style_combo.addItem(style.upper(), style)
        st_idx = self.line_style_combo.findData(str(self._current.get("line_style", "solid")))
        self.line_style_combo.setCurrentIndex(st_idx if st_idx >= 0 else 0)
        form.addRow(self._field_label("LINE STYLE"), self.line_style_combo)

        self.color_btn = QPushButton("PICK COLOR")
        self.color_btn.setObjectName("colorButton")
        self.color_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._color = str(self._current.get("color", "#00d4ff") or "#00d4ff")
        self._apply_color_style()
        self.color_btn.clicked.connect(self._pick_color)
        form.addRow(self._field_label("COLOR"), self.color_btn)

        self.volume_opacity_spin = QDoubleSpinBox()
        self.volume_opacity_spin.setObjectName("terminalSpin")
        self.volume_opacity_spin.setRange(0.0, 1.0)
        self.volume_opacity_spin.setDecimals(2)
        self.volume_opacity_spin.setSingleStep(0.05)
        self.volume_opacity_spin.setValue(float(self._current.get("volume_opacity", 0.75) or 0.75))
        form.addRow(self._field_label("VOLUME OPACITY"), self.volume_opacity_spin)
        self.type_combo.currentIndexChanged.connect(self._sync_volume_fields)
        self._sync_volume_fields()

        body_layout.addWidget(panel)
        body_layout.addStretch()
        root.addWidget(body, 1)
        root.addWidget(self._build_footer())

    def _build_title_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("settingsTitleBar")
        bar.setFixedHeight(30)
        bar.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 0, 6, 0)
        layout.setSpacing(6)

        title = QLabel("INDICATOR SETTINGS")
        title.setObjectName("dialogTitle")
        subtitle = QLabel(self._summary_for_title())
        subtitle.setObjectName("dialogSubtitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self.reject)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addStretch()
        layout.addWidget(close_btn)

        bar.mousePressEvent = self._tb_press
        bar.mouseMoveEvent = self._tb_move
        bar.mouseReleaseEvent = self._tb_release
        return bar

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("settingsFooter")
        footer.setFixedHeight(42)

        layout = QHBoxLayout(footer)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        hint = QLabel("Values apply only to this indicator instance")
        hint.setObjectName("statusLabel")

        cancel = QPushButton("CANCEL")
        cancel.setObjectName("secondaryButton")
        cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel.setFixedHeight(26)
        cancel.clicked.connect(self.reject)

        save = QPushButton("SAVE")
        save.setObjectName("primaryButton")
        save.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save.setFixedHeight(26)
        save.clicked.connect(self.accept)

        layout.addWidget(hint)
        layout.addStretch()
        layout.addWidget(cancel)
        layout.addWidget(save)
        return footer

    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    def _summary_for_title(self) -> str:
        typ = str(self._current.get("type", "ema"))
        period = int(self._current.get("period", 20) or 20)
        return f"{_catalog_display_name(typ)} · {period}"

    def _tb_press(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _tb_move(self, event: QMouseEvent) -> None:
        if self._drag_active and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def _tb_release(self, _event: QMouseEvent) -> None:
        self._drag_active = False

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._color), self, "Pick Indicator Color")
        if color.isValid():
            self._color = color.name()
            self._apply_color_style()

    def _apply_color_style(self) -> None:
        self.color_btn.setText(f"{self._color.upper()}  ·  PICK COLOR")
        self.color_btn.setStyleSheet(f"""
            QPushButton#colorButton {{
                background: {_C.BG2};
                color: {_C.T1};
                border: 1px solid {self._color};
                border-left: 5px solid {self._color};
                border-radius: 2px;
                font-family: {_NUM};
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 0.6px;
                padding: 4px 8px;
                text-align: left;
            }}
            QPushButton#colorButton:hover {{
                background: {_C.BG3};
                border-color: {self._color};
                border-left: 5px solid {self._color};
            }}
        """)

    def payload(self) -> Dict[str, Any]:
        return {
            "type": str(self.type_combo.currentData() or "ema"),
            "period": int(self.period_spin.value()),
            "thickness": float(self.thickness_spin.value()),
            "line_style": str(self.line_style_combo.currentData() or "solid"),
            "color": self._color,
            "volume_opacity": float(self.volume_opacity_spin.value()),
        }

    def _sync_volume_fields(self) -> None:
        is_volume = str(self.type_combo.currentData() or "").lower() == "volume"
        self.volume_opacity_spin.setVisible(is_volume)
        lbl = self.volume_opacity_spin.parentWidget().layout().labelForField(self.volume_opacity_spin)
        if lbl is not None:
            lbl.setVisible(is_volume)

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
        IndicatorSettingsDialog {{
            background: {_C.BG0};
        }}
        QFrame#settingsContainer {{
            background: {_C.BG1};
            border: 1px solid {_C.BG4};
            border-radius: 2px;
        }}
        QFrame#settingsTitleBar,
        QFrame#settingsFooter {{
            background: {_C.BGTB};
        }}
        QFrame#settingsTitleBar {{
            border-bottom: 1px solid {_C.BG4};
        }}
        QFrame#settingsFooter {{
            border-top: 1px solid {_C.BG4};
        }}
        QFrame#settingsBody {{
            background: {_C.BG1};
        }}
        QFrame#formPanel {{
            background: {_C.BG2};
            border: 1px solid {_C.BG4};
            border-radius: 2px;
        }}
        QLabel#dialogTitle {{
            color: {_C.AMBER};
            font-family: {_SANS};
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 1.2px;
            background: transparent;
        }}
        QLabel#dialogSubtitle,
        QLabel#statusLabel {{
            color: {_C.T2};
            font-family: {_SANS};
            font-size: 10px;
            font-weight: 600;
            background: transparent;
        }}
        QLabel#sectionLabel,
        QLabel#fieldLabel {{
            color: {_C.T2};
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 800;
            letter-spacing: 1px;
            background: transparent;
        }}
        QComboBox#terminalCombo {{
            background: {_C.BG1};
            color: {_C.T1};
            border: 1px solid {_C.BG4};
            border-radius: 2px;
            font-family: {_SANS};
            font-size: 11px;
            font-weight: 700;
            padding: 4px 8px;
            min-height: 20px;
        }}
        QSpinBox#terminalSpin,
        QDoubleSpinBox#terminalSpin {{
            background: {_C.BG1};
            color: {_C.T1};
            border: 1px solid {_C.BG4};
            border-radius: 2px;
            font-family: {_NUM};
            font-size: 11px;
            font-weight: 700;
            padding: 4px 8px;
            min-height: 20px;
        }}
        QComboBox#terminalCombo:hover,
        QSpinBox#terminalSpin:hover,
        QDoubleSpinBox#terminalSpin:hover {{
            background: {_C.BG3};
            border-color: {_C.T2};
        }}
        QComboBox#terminalCombo:focus,
        QSpinBox#terminalSpin:focus,
        QDoubleSpinBox#terminalSpin:focus {{
            border: 1px solid {_C.CYAN};
            background: {_C.BG3};
        }}
        QComboBox#terminalCombo::drop-down {{
            border: none;
            width: 18px;
        }}
        QComboBox#terminalCombo::down-arrow {{
            image: none;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 5px solid {_C.T2};
            margin-right: 5px;
        }}
        QComboBox#terminalCombo QAbstractItemView {{
            background: {_C.BG1};
            color: {_C.T0};
            border: 1px solid {_C.BG4};
            selection-background-color: {_C.SEL};
            selection-color: {_C.T0};
            outline: none;
        }}
        QSpinBox#terminalSpin::up-button,
        QSpinBox#terminalSpin::down-button,
        QDoubleSpinBox#terminalSpin::up-button,
        QDoubleSpinBox#terminalSpin::down-button {{
            width: 0px;
            border: none;
        }}
        QPushButton#closeButton {{
            background: transparent;
            color: {_C.T2};
            border: none;
            border-radius: 2px;
            font-size: 12px;
            font-weight: 800;
        }}
        QPushButton#closeButton:hover {{
            background: rgba(255,77,106,0.15);
            color: {_C.BEAR};
        }}
        QPushButton#primaryButton,
        QPushButton#secondaryButton {{
            border-radius: 2px;
            font-family: {_SANS};
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 0.8px;
            padding: 0 14px;
        }}
        QPushButton#primaryButton {{
            background: rgba(0,212,168,0.12);
            color: {_C.BULL};
            border: 1px solid rgba(0,212,168,0.35);
        }}
        QPushButton#primaryButton:hover {{
            background: rgba(0,212,168,0.18);
            border-color: {_C.BULL};
        }}
        QPushButton#secondaryButton {{
            background: {_C.BG2};
            color: {_C.T1};
            border: 1px solid {_C.BG4};
        }}
        QPushButton#secondaryButton:hover {{
            background: {_C.BG3};
            color: {_C.T0};
        }}
        """)


# -----------------------------------------------------------------------------
# Indicator library dialog
# -----------------------------------------------------------------------------

class IndicatorLibraryDialog(QDialog):
    """Two-panel indicator manager: selected instances (top), available types (bottom)."""

    def __init__(self, selected: List[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Indicator Manager")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setModal(True)
        self.setMinimumSize(700, 480)
        self.resize(760, 560)
        self._selected = [self._normalize_instance(item) for item in selected]
        self._drag_active = False
        self._drag_offset = QPoint()

        self._build_ui()
        self._apply_styles()
        self._refresh_tables()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)

        self._container = QFrame()
        self._container.setObjectName("libraryContainer")
        outer.addWidget(self._container)

        root = QVBoxLayout(self._container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_title_bar())

        body = QFrame()
        body.setObjectName("libraryBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(8, 8, 8, 8)
        body_layout.setSpacing(8)

        self.selected_count_lbl = QLabel()
        self.selected_count_lbl.setObjectName("sectionCounter")
        body_layout.addWidget(self._build_section_header(
            "SELECTED INDICATORS",
            "Active stack applied to the chart. Duplicate indicators are allowed.",
            self.selected_count_lbl,
        ))

        self.selected_table = self._create_table(["#", "Indicator", "Period", "Style", "Edit", "Remove"])
        body_layout.addWidget(self.selected_table, 3)

        self.available_count_lbl = QLabel()
        self.available_count_lbl.setObjectName("sectionCounter")
        body_layout.addWidget(self._build_section_header(
            "AVAILABLE INDICATORS",
            "Add indicator types from the catalog. New instances start with defaults.",
            self.available_count_lbl,
        ))

        self.available_table = self._create_table(["#", "Indicator", "Default", "Add"])
        body_layout.addWidget(self.available_table, 2)

        root.addWidget(body, 1)
        root.addWidget(self._build_footer())

    def _build_title_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("libraryTitleBar")
        bar.setFixedHeight(30)
        bar.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 0, 6, 0)
        layout.setSpacing(6)

        title = QLabel("INDICATOR LIBRARY")
        title.setObjectName("dialogTitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self.reject)

        layout.addWidget(title)
        layout.addStretch()
        layout.addWidget(close_btn)

        bar.mousePressEvent = self._tb_press
        bar.mouseMoveEvent = self._tb_move
        bar.mouseReleaseEvent = self._tb_release
        return bar

    def _build_section_header(self, title: str, subtitle: str, counter: QLabel) -> QWidget:
        wrap = QFrame()
        wrap.setObjectName("sectionHeader")
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(8)

        title_stack = QVBoxLayout()
        title_stack.setContentsMargins(0, 0, 0, 0)
        title_stack.setSpacing(1)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("sectionTitle")
        subtitle_lbl = QLabel(subtitle)
        subtitle_lbl.setObjectName("sectionSubtitle")

        title_stack.addWidget(title_lbl)
        title_stack.addWidget(subtitle_lbl)

        layout.addLayout(title_stack)
        layout.addStretch()
        layout.addWidget(counter)
        return wrap

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("libraryFooter")
        footer.setFixedHeight(42)

        layout = QHBoxLayout(footer)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        self._status_lbl = QLabel("Configure selected indicators, then apply to chart")
        self._status_lbl.setObjectName("statusLabel")

        cancel = QPushButton("CANCEL")
        cancel.setObjectName("secondaryButton")
        cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel.setFixedHeight(26)
        cancel.clicked.connect(self.reject)

        ok = QPushButton("APPLY")
        ok.setObjectName("primaryButton")
        ok.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        ok.setFixedHeight(26)
        ok.clicked.connect(self.accept)

        layout.addWidget(self._status_lbl)
        layout.addStretch()
        layout.addWidget(cancel)
        layout.addWidget(ok)
        return footer

    def _tb_press(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _tb_move(self, event: QMouseEvent) -> None:
        if self._drag_active and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def _tb_release(self, _event: QMouseEvent) -> None:
        self._drag_active = False

    def _normalize_instance(self, item: Dict[str, Any]) -> Dict[str, Any]:
        type_id = str(item.get("type") or "ema").lower()
        period = int(item.get("period", 20) or 20)
        volume_opacity = float(item.get("volume_opacity", 0.75) or 0.75)
        return {
            "id": str(item.get("id") or f"{type_id}_{uuid.uuid4().hex[:8]}"),
            "type": type_id,
            "period": max(1, period),
            "color": str(item.get("color") or "#00d4ff"),
            "thickness": float(item.get("thickness", 1.2) or 1.2),
            "line_style": str(item.get("line_style") or "solid"),
            "volume_opacity": max(0.0, min(1.0, volume_opacity)),
        }

    def _build_section_label(self, text: str) -> QLabel:
        """Backward-compatible helper kept for callers/tests that may reference it."""
        label = QLabel(text.upper())
        label.setObjectName("sectionTitle")
        return label

    def _create_table(self, headers: List[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers), self)
        table.setObjectName("indicatorTable")
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(_ROW_H)
        table.verticalHeader().setMinimumSectionSize(_ROW_H)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        self._configure_table_columns(table, headers)
        return table

    def _configure_table_columns(self, table: QTableWidget, headers: List[str]) -> None:
        """Keep both tables compact while giving action cells enough room."""
        header = table.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setMinimumSectionSize(28)
        header.setStretchLastSection(False)

        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(0, _INDEX_COL_W)

        if headers == ["#", "Indicator", "Period", "Style", "Edit", "Remove"]:
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(1, _SELECTED_NAME_COL_W)

            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(2, _PERIOD_COL_W)

            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

            for col in (4, 5):
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
                table.setColumnWidth(col, _ACTION_COL_W)
            return

        if headers == ["#", "Indicator", "Default", "Add"]:
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(1, _AVAILABLE_NAME_COL_W)

            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(3, _ACTION_COL_W)
            return

        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for i in range(2, len(headers)):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)

    def _refresh_tables(self) -> None:
        self._refresh_selected_table()
        self._refresh_available_table()
        if hasattr(self, "selected_count_lbl"):
            self.selected_count_lbl.setText(f"{len(self._selected)} ACTIVE")
        if hasattr(self, "available_count_lbl"):
            self.available_count_lbl.setText(f"{len(_INDICATOR_CATALOG)} TYPES")
        if hasattr(self, "_status_lbl"):
            self._status_lbl.setText(f"Selected: {len(self._selected)}  ·  Available: {len(_INDICATOR_CATALOG)}")

    def _summary(self, item: Dict[str, Any]) -> str:
        disp = _catalog_display_name(str(item.get("type", "ema")))
        if str(item.get("type", "")).lower() == "volume":
            return disp
        return f"{disp} ({int(item.get('period', 20))})"

    def _style_summary(self, item: Dict[str, Any]) -> str:
        if str(item.get("type", "")).lower() == "volume":
            opacity_pct = int(round(float(item.get("volume_opacity", 0.75) or 0.75) * 100))
            return f"BAR OPACITY · {opacity_pct}%"
        thickness = float(item.get("thickness", 1.2) or 1.2)
        line_style = str(item.get("line_style") or "solid").upper()
        color = str(item.get("color") or "#00d4ff").upper()
        return f"{line_style} · {thickness:.1f}px · {color}"

    def _refresh_selected_table(self) -> None:
        self.selected_table.setRowCount(len(self._selected))
        for idx, item in enumerate(self._selected):
            color = str(item.get("color") or _C.BLUE)
            type_id = str(item.get("type") or "ema")
            disp = _catalog_display_name(type_id)
            period = int(item.get("period", 20) or 20)

            self.selected_table.setItem(
                idx,
                0,
                _table_item(str(idx + 1), _C.T2, Qt.AlignmentFlag.AlignCenter, mono=True),
            )
            indicator_item = _table_item(disp, _C.T_SYMBOL, Qt.AlignmentFlag.AlignLeft, bold=True)
            indicator_item.setToolTip(str(item.get("id", "")))
            self.selected_table.setItem(idx, 1, indicator_item)
            self.selected_table.setItem(
                idx,
                2,
                _table_item(str(period), _C.AMBER, Qt.AlignmentFlag.AlignRight, mono=True, bold=True),
            )

            style_item = _table_item(self._style_summary(item), _C.T1, Qt.AlignmentFlag.AlignLeft, mono=True)
            style_item.setForeground(QBrush(QColor(color)))
            self.selected_table.setItem(idx, 3, style_item)

            edit_btn = _icon_only_button("edit")
            edit_btn.setToolTip("Edit indicator settings")
            edit_btn.clicked.connect(lambda _=False, row=idx: self._edit_indicator(row))

            remove_btn = _icon_only_button("danger")
            remove_btn.setToolTip("Remove this indicator instance")
            remove_btn.clicked.connect(lambda _=False, row=idx: self._remove_indicator(row))

            self.selected_table.setCellWidget(idx, 4, _ActionCell(edit_btn))
            self.selected_table.setCellWidget(idx, 5, _ActionCell(remove_btn))
            self.selected_table.setRowHeight(idx, _ROW_H)

    def _refresh_available_table(self) -> None:
        self.available_table.setRowCount(len(_INDICATOR_CATALOG))
        for idx, item in enumerate(_INDICATOR_CATALOG):
            self.available_table.setItem(
                idx,
                0,
                _table_item(str(idx + 1), _C.T2, Qt.AlignmentFlag.AlignCenter, mono=True),
            )
            self.available_table.setItem(
                idx,
                1,
                _table_item(item.display_name, _C.T_SYMBOL, Qt.AlignmentFlag.AlignLeft, bold=True),
            )
            if item.type_id == "volume":
                default_text = "Uses candle up/down colors"
            else:
                default_text = f"PERIOD {item.default_period} · {item.default_line_style.upper()} · {item.default_thickness:.1f}px"
            default_item = _table_item(default_text, item.default_color, Qt.AlignmentFlag.AlignLeft, mono=True)
            self.available_table.setItem(idx, 2, default_item)

            add_btn = _icon_only_button("add")
            add_btn.setToolTip(f"Add another {item.display_name} instance")
            add_btn.clicked.connect(lambda _=False, t=item.type_id: self._add_indicator(t))
            self.available_table.setCellWidget(idx, 3, _ActionCell(add_btn))
            self.available_table.setRowHeight(idx, _ROW_H)

    def _add_indicator(self, type_id: str) -> None:
        catalog = next((c for c in _INDICATOR_CATALOG if c.type_id == type_id), _INDICATOR_CATALOG[0])
        self._selected.append({
            "id": f"{catalog.type_id}_{uuid.uuid4().hex[:8]}",
            "type": catalog.type_id,
            "period": catalog.default_period,
            "color": catalog.default_color,
            "thickness": catalog.default_thickness,
            "line_style": catalog.default_line_style,
            "volume_opacity": 0.75,
        })
        self._refresh_tables()

    def _edit_indicator(self, row: int) -> None:
        if row < 0 or row >= len(self._selected):
            return
        current = dict(self._selected[row])
        dlg = IndicatorSettingsDialog(current, self)
        if dlg.exec():
            payload = dlg.payload()
            if payload["period"] <= 0:
                QMessageBox.warning(self, "Invalid Settings", "Period must be greater than zero.")
                return
            current.update(payload)
            self._selected[row] = self._normalize_instance(current)
            self._refresh_tables()

    def _remove_indicator(self, row: int) -> None:
        if row < 0 or row >= len(self._selected):
            return
        self._selected.pop(row)
        self._refresh_tables()

    def selected_payload(self) -> List[dict]:
        return [dict(item) for item in self._selected]

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
        IndicatorLibraryDialog {{
            background: {_C.BG0};
        }}
        QFrame#libraryContainer {{
            background: {_C.BG1};
            border: 1px solid {_C.BG4};
            border-radius: 2px;
        }}
        QFrame#libraryTitleBar,
        QFrame#libraryFooter {{
            background: {_C.BGTB};
        }}
        QFrame#libraryTitleBar {{
            border-bottom: 1px solid {_C.BG4};
        }}
        QFrame#libraryFooter {{
            border-top: 1px solid {_C.BG4};
        }}
        QFrame#libraryBody {{
            background: {_C.BG1};
        }}
        QLabel#dialogTitle {{
            color: {_C.AMBER};
            font-family: {_SANS};
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 1.2px;
            background: transparent;
        }}
        QLabel#dialogSubtitle,
        QLabel#statusLabel {{
            color: {_C.T2};
            font-family: {_SANS};
            font-size: 10px;
            font-weight: 600;
            background: transparent;
        }}
        QFrame#sectionHeader {{
            background: {_C.BG2};
            border: 1px solid {_C.BG4};
            border-radius: 2px;
        }}
        QLabel#sectionTitle {{
            color: {_C.T1};
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 900;
            letter-spacing: 1.2px;
            background: transparent;
        }}
        QLabel#sectionSubtitle {{
            color: {_C.T2};
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 600;
            background: transparent;
        }}
        QLabel#sectionCounter {{
            color: {_C.CYAN};
            background: rgba(0,212,255,0.08);
            border: 1px solid rgba(0,212,255,0.22);
            border-radius: 2px;
            font-family: {_NUM};
            font-size: 9px;
            font-weight: 800;
            letter-spacing: 0.8px;
            padding: 2px 7px;
        }}
        QTableWidget#indicatorTable {{
            background: {_C.BG1};
            alternate-background-color: {_C.BG2};
            gridline-color: transparent;
            border: 1px solid {_C.BG4};
            border-radius: 2px;
            outline: none;
            color: {_C.T1};
            selection-background-color: {_C.SEL};
            font-family: {_SANS};
            font-size: 11px;
        }}
        QTableWidget#indicatorTable::item {{
            padding: 0 7px;
            border-bottom: 1px solid {_C.BG3};
        }}
        QTableWidget#indicatorTable::item:selected {{
            background: {_C.SEL};
            color: {_C.T0};
        }}
        QTableWidget#indicatorTable::item:hover {{
            background: {_C.BG3};
        }}
        QHeaderView::section {{
            background: {_C.BG2};
            color: {_C.T2};
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 900;
            letter-spacing: 1px;
            text-transform: uppercase;
            border: none;
            border-bottom: 1px solid {_C.BG4};
            padding: 0 7px;
            min-height: 23px;
        }}
        QPushButton#closeButton {{
            background: transparent;
            color: {_C.T2};
            border: none;
            border-radius: 2px;
            font-size: 12px;
            font-weight: 800;
        }}
        QPushButton#closeButton:hover {{
            background: rgba(255,77,106,0.15);
            color: {_C.BEAR};
        }}
        QPushButton#primaryButton,
        QPushButton#secondaryButton,
        QPushButton#addActionButton,
        QPushButton#editActionButton,
        QPushButton#dangerActionButton,
        QPushButton#neutralActionButton {{
            border-radius: 2px;
            padding: 0;
        }}
        QPushButton#primaryButton {{
            background: rgba(0,212,168,0.12);
            color: {_C.BULL};
            border: 1px solid rgba(0,212,168,0.35);
            padding: 0 14px;
        }}
        QPushButton#primaryButton:hover {{
            background: rgba(0,212,168,0.18);
            border-color: {_C.BULL};
        }}
        QPushButton#secondaryButton {{
            background: {_C.BG2};
            color: {_C.T1};
            border: 1px solid {_C.BG4};
            padding: 0 14px;
        }}
        QPushButton#secondaryButton:hover {{
            background: {_C.BG3};
            color: {_C.T0};
        }}
        QPushButton#addActionButton {{
            background: rgba(0,212,168,0.08);
            color: {_C.BULL};
            border: 1px solid rgba(0,212,168,0.28);
        }}
        QPushButton#addActionButton:hover {{
            background: rgba(0,212,168,0.16);
            border-color: {_C.BULL};
        }}
        QPushButton#editActionButton {{
            background: rgba(0,212,255,0.07);
            color: {_C.CYAN};
            border: 1px solid rgba(0,212,255,0.24);
        }}
        QPushButton#editActionButton:hover {{
            background: rgba(0,212,255,0.15);
            border-color: {_C.CYAN};
        }}
        QPushButton#dangerActionButton {{
            background: rgba(255,77,106,0.06);
            color: {_C.BEAR};
            border: 1px solid rgba(255,77,106,0.24);
        }}
        QPushButton#dangerActionButton:hover {{
            background: rgba(255,77,106,0.14);
            border-color: {_C.BEAR};
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 4px;
            border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {_C.BG4};
            border-radius: 2px;
            min-height: 18px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {_C.T2};
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
            background: {_C.BG4};
            border-radius: 2px;
            min-width: 18px;
        }}
        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal {{
            width: 0;
            border: none;
        }}
        """)
