from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QListWidget, QPushButton, QHBoxLayout, QInputDialog, QColorDialog, QDialogButtonBox

@dataclass
class MovingAverageConfig:
    id: str
    period: int = 20
    color: str = "#2962ff"
    thickness: float = 1.2
    line_style: str = "solid"


class IndicatorLibraryDialog(QDialog):
    def __init__(self, selected: List[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Indicator Library")
        self.resize(520, 420)
        self._selected = [MovingAverageConfig(**s) for s in selected]

        lay = QVBoxLayout(self)
        self.selected_list = QListWidget()
        self.available_list = QListWidget()
        lay.addWidget(self.selected_list)
        btns = QHBoxLayout()
        add = QPushButton("+ Add MA")
        edit = QPushButton("✎ Edit")
        remove = QPushButton("Remove")
        add.clicked.connect(self._add_ma)
        edit.clicked.connect(self._edit_ma)
        remove.clicked.connect(self._remove_ma)
        btns.addWidget(add); btns.addWidget(edit); btns.addWidget(remove)
        lay.addLayout(btns)
        self.available_list.addItem("Moving Average (EMA)")
        self.available_list.setDisabled(True)
        lay.addWidget(self.available_list)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        lay.addWidget(box)
        self._refresh()

    def _refresh(self):
        self.selected_list.clear()
        for i, ma in enumerate(self._selected, 1):
            self.selected_list.addItem(f"MA {i}: EMA({ma.period})  {ma.color}  {ma.thickness}px  {ma.line_style}")

    def _add_ma(self):
        period, ok = QInputDialog.getInt(self, "MA Period", "EMA Period", 20, 1, 500, 1)
        if not ok:
            return
        idx = len(self._selected) + 1
        self._selected.append(MovingAverageConfig(id=f"ema_{idx}", period=period))
        self._refresh()

    def _edit_ma(self):
        row = self.selected_list.currentRow()
        if row < 0:
            return
        ma = self._selected[row]
        period, ok = QInputDialog.getInt(self, "MA Period", "EMA Period", ma.period, 1, 500, 1)
        if not ok:
            return
        color = QColorDialog.getColor(parent=self)
        ma.period = period
        if color.isValid():
            ma.color = color.name()
        self._refresh()

    def _remove_ma(self):
        row = self.selected_list.currentRow()
        if row < 0:
            return
        self._selected.pop(row)
        self._refresh()

    def selected_payload(self) -> List[dict]:
        return [asdict(x) for x in self._selected]
