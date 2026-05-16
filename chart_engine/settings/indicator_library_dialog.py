from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


@dataclass
class MovingAverageConfig:
    id: str
    period: int = 20
    color: str = "#2962ff"
    thickness: float = 1.2
    line_style: str = "solid"


class IndicatorLibraryDialog(QDialog):
    """Simple two-panel indicator menu: selected on top, available below."""

    def __init__(self, selected: List[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Indicator Menu")
        self.resize(620, 520)
        self._selected = [MovingAverageConfig(**s) for s in selected]

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
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for i in range(2, len(headers)):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        return table

    def _refresh_tables(self) -> None:
        self._refresh_selected_table()
        self._refresh_available_table()

    def _refresh_selected_table(self) -> None:
        self.selected_table.setRowCount(len(self._selected))
        for idx, ma in enumerate(self._selected):
            self.selected_table.setItem(idx, 0, QTableWidgetItem(str(idx + 1)))
            self.selected_table.setItem(idx, 1, QTableWidgetItem(f"EMA ({ma.period})"))

            edit_btn = QPushButton("Edit")
            edit_btn.clicked.connect(lambda _=False, row=idx: self._edit_ma(row))
            self.selected_table.setCellWidget(idx, 2, edit_btn)

            remove_btn = QPushButton("Remove")
            remove_btn.clicked.connect(lambda _=False, row=idx: self._remove_ma(row))
            self.selected_table.setCellWidget(idx, 3, remove_btn)

    def _refresh_available_table(self) -> None:
        self.available_table.setRowCount(1)
        self.available_table.setItem(0, 0, QTableWidgetItem("1"))
        self.available_table.setItem(0, 1, QTableWidgetItem("EMA"))
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_ma)
        self.available_table.setCellWidget(0, 2, add_btn)

    def _add_ma(self) -> None:
        next_period = 20 if not self._selected else (self._selected[-1].period + 10)
        idx = len(self._selected) + 1
        self._selected.append(MovingAverageConfig(id=f"ema_{idx}", period=next_period))
        self._refresh_selected_table()

    def _edit_ma(self, row: int) -> None:
        if row < 0 or row >= len(self._selected):
            return
        ma = self._selected[row]
        color = QColorDialog.getColor(parent=self)
        if color.isValid():
            ma.color = color.name()
        QMessageBox.information(
            self,
            "Indicator Settings",
            f"EMA ({ma.period}) updated.\n(Period/style editing can be extended in next step.)",
        )
        self._refresh_selected_table()

    def _remove_ma(self, row: int) -> None:
        if row < 0 or row >= len(self._selected):
            return
        self._selected.pop(row)
        self._refresh_selected_table()

    def selected_payload(self) -> List[dict]:
        return [asdict(x) for x in self._selected]
