from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List
import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
)


@dataclass
class IndicatorCatalogItem:
    type_id: str
    display_name: str
    default_period: int
    default_color: str
    default_thickness: float = 1.2
    default_line_style: str = "solid"


_INDICATOR_CATALOG: List[IndicatorCatalogItem] = [
    IndicatorCatalogItem(type_id="ema", display_name="EMA", default_period=20, default_color="#2962ff"),
    IndicatorCatalogItem(type_id="sma", display_name="SMA", default_period=20, default_color="#ff9800"),
]


class IndicatorSettingsDialog(QDialog):
    def __init__(self, current: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Indicator Settings")
        self.resize(360, 220)
        self._current = dict(current)

        root = QVBoxLayout(self)
        form = QFormLayout()

        self.type_combo = QComboBox()
        for c in _INDICATOR_CATALOG:
            self.type_combo.addItem(c.display_name, c.type_id)
        current_type = str(current.get("type", "ema"))
        idx = self.type_combo.findData(current_type)
        self.type_combo.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("Type", self.type_combo)

        self.period_spin = QSpinBox()
        self.period_spin.setRange(1, 2000)
        self.period_spin.setValue(int(current.get("period", 20) or 20))
        form.addRow("Period", self.period_spin)

        self.thickness_spin = QDoubleSpinBox()
        self.thickness_spin.setRange(0.5, 10.0)
        self.thickness_spin.setSingleStep(0.1)
        self.thickness_spin.setValue(float(current.get("thickness", 1.2) or 1.2))
        form.addRow("Thickness", self.thickness_spin)

        self.line_style_combo = QComboBox()
        for style in ("solid", "dashed", "dotted"):
            self.line_style_combo.addItem(style.title(), style)
        st_idx = self.line_style_combo.findData(str(current.get("line_style", "solid")))
        self.line_style_combo.setCurrentIndex(st_idx if st_idx >= 0 else 0)
        form.addRow("Line Style", self.line_style_combo)

        self.color_btn = QPushButton("Pick Color")
        self._color = str(current.get("color", "#2962ff") or "#2962ff")
        self._apply_color_style()
        self.color_btn.clicked.connect(self._pick_color)
        form.addRow("Color", self.color_btn)

        root.addLayout(form)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(parent=self)
        if color.isValid():
            self._color = color.name()
            self._apply_color_style()

    def _apply_color_style(self) -> None:
        self.color_btn.setStyleSheet(f"background: {self._color}; color: white; padding: 4px 8px;")

    def payload(self) -> Dict[str, Any]:
        return {
            "type": str(self.type_combo.currentData() or "ema"),
            "period": int(self.period_spin.value()),
            "thickness": float(self.thickness_spin.value()),
            "line_style": str(self.line_style_combo.currentData() or "solid"),
            "color": self._color,
        }


class IndicatorLibraryDialog(QDialog):
    """Two-panel indicator manager: selected instances (top), available types (bottom)."""

    def __init__(self, selected: List[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Indicator Manager")
        self.resize(680, 560)
        self._selected = [self._normalize_instance(item) for item in selected]

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(self._build_section_label("Selected Indicators"))
        self.selected_table = self._create_table(["#", "Indicator", "Edit", "Remove"])
        root.addWidget(self.selected_table, 2)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(divider)

        root.addWidget(self._build_section_label("Available Indicators"))
        self.available_table = self._create_table(["#", "Indicator", "Add"])
        root.addWidget(self.available_table, 1)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)

        self._refresh_tables()

    def _normalize_instance(self, item: Dict[str, Any]) -> Dict[str, Any]:
        type_id = str(item.get("type") or "ema").lower()
        period = int(item.get("period", 20) or 20)
        return {
            "id": str(item.get("id") or f"{type_id}_{uuid.uuid4().hex[:8]}"),
            "type": type_id,
            "period": max(1, period),
            "color": str(item.get("color") or "#2962ff"),
            "thickness": float(item.get("thickness", 1.2) or 1.2),
            "line_style": str(item.get("line_style") or "solid"),
        }

    def _build_section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-weight: 600; font-size: 13px;")
        return label

    def _create_table(self, headers: List[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers), self)
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setAlternatingRowColors(True)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, header.ResizeMode.Stretch)
        for i in range(2, len(headers)):
            header.setSectionResizeMode(i, header.ResizeMode.ResizeToContents)
        return table

    def _refresh_tables(self) -> None:
        self._refresh_selected_table()
        self._refresh_available_table()

    def _summary(self, item: Dict[str, Any]) -> str:
        disp = next((c.display_name for c in _INDICATOR_CATALOG if c.type_id == item.get("type")), "EMA")
        return f"{disp} ({int(item.get('period', 20))})"

    def _refresh_selected_table(self) -> None:
        self.selected_table.setRowCount(len(self._selected))
        for idx, item in enumerate(self._selected):
            self.selected_table.setItem(idx, 0, QTableWidgetItem(str(idx + 1)))
            self.selected_table.setItem(idx, 1, QTableWidgetItem(self._summary(item)))

            edit_btn = QPushButton("Edit")
            edit_btn.clicked.connect(lambda _=False, row=idx: self._edit_indicator(row))
            self.selected_table.setCellWidget(idx, 2, edit_btn)

            remove_btn = QPushButton("Remove")
            remove_btn.clicked.connect(lambda _=False, row=idx: self._remove_indicator(row))
            self.selected_table.setCellWidget(idx, 3, remove_btn)

    def _refresh_available_table(self) -> None:
        self.available_table.setRowCount(len(_INDICATOR_CATALOG))
        for idx, item in enumerate(_INDICATOR_CATALOG):
            self.available_table.setItem(idx, 0, QTableWidgetItem(str(idx + 1)))
            self.available_table.setItem(idx, 1, QTableWidgetItem(item.display_name))
            add_btn = QPushButton("Add")
            add_btn.clicked.connect(lambda _=False, t=item.type_id: self._add_indicator(t))
            self.available_table.setCellWidget(idx, 2, add_btn)

    def _add_indicator(self, type_id: str) -> None:
        catalog = next((c for c in _INDICATOR_CATALOG if c.type_id == type_id), _INDICATOR_CATALOG[0])
        self._selected.append({
            "id": f"{catalog.type_id}_{uuid.uuid4().hex[:8]}",
            "type": catalog.type_id,
            "period": catalog.default_period,
            "color": catalog.default_color,
            "thickness": catalog.default_thickness,
            "line_style": catalog.default_line_style,
        })
        self._refresh_selected_table()

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
            self._refresh_selected_table()

    def _remove_indicator(self, row: int) -> None:
        if row < 0 or row >= len(self._selected):
            return
        self._selected.pop(row)
        self._refresh_selected_table()

    def selected_payload(self) -> List[dict]:
        return [dict(item) for item in self._selected]
