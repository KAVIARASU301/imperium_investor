"""Scans list selection dialog for IBKR scanner mode."""

from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

# Keep the scans list visually aligned with the scanner/dialog styling without
# coupling this standalone dialog module to the scanner table implementation.
_BG0 = "#050709"
_BG1 = "#0a0d12"
_BG2 = "#0f1318"
_BG3 = "#141920"
_BG4 = "#1a2030"
_T0 = "#e8f0ff"
_T1 = "#a8bcd4"
_SEL = "#1a2840"
_SANS = "'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', Arial, sans-serif"


class ScansListDialog(QDialog):
    """Simple list dialog for selecting and running saved scans."""

    def __init__(self, scans: List[Dict[str, str]], parent=None):
        super().__init__(parent)
        self.scans = list(scans or [])
        self.selected_scan_index: Optional[int] = None
        self.setWindowTitle("Scans List")
        self.setModal(True)
        self.setFixedSize(360, 480)
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        title = QLabel("SCANS LIST")
        title.setObjectName("simpleScanTitle")
        layout.addWidget(title)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("simpleScanList")
        self.list_widget.itemDoubleClicked.connect(self._accept_current_item)
        layout.addWidget(self.list_widget, 1)

        for index, scan in self._sorted_scan_items():
            name = str(scan.get("name") or f"Scan {index + 1}")
            tag = self._scan_tag(scan)
            item = QListWidgetItem(f"{tag}  /  {name}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.list_widget.addItem(item)

        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_btn = QPushButton("Cancel")
        run_btn = QPushButton("Run")
        cancel_btn.clicked.connect(self.reject)
        run_btn.clicked.connect(self._accept_current_item)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(run_btn)
        layout.addLayout(buttons)

    def _scan_tag(self, scan: Dict[str, str]) -> str:
        tag = str(scan.get("tag") or "Others").strip()
        return tag or "Others"

    def _sorted_scan_items(self):
        decorated = []
        for index, scan in enumerate(self.scans):
            tag = self._scan_tag(scan)
            name = str(scan.get("name") or f"Scan {index + 1}")
            decorated.append((tag.lower(), name.lower(), index, scan))
        decorated.sort()
        return [(index, scan) for _tag, _name, index, scan in decorated]

    def _accept_current_item(self, *_args):
        item = self.list_widget.currentItem()
        if item is None:
            return
        self.selected_scan_index = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def _apply_styles(self):
        self.setStyleSheet(f"""
            QDialog {{
                background: {_BG1};
                color: {_T0};
                border: 1px solid {_BG4};
            }}
            QLabel#simpleScanTitle {{
                color: {_T0};
                font-family: {_SANS};
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.8px;
            }}
            QListWidget#simpleScanList {{
                background: {_BG0};
                color: {_T1};
                border: 1px solid {_BG4};
                outline: none;
                font-family: {_SANS};
                font-size: 10px;
            }}
            QListWidget#simpleScanList::item {{
                min-height: 22px;
                padding: 2px 6px;
            }}
            QListWidget#simpleScanList::item:selected {{
                background: {_SEL};
                color: {_T0};
            }}
            QPushButton {{
                background: {_BG2};
                color: {_T1};
                border: 1px solid {_BG4};
                border-radius: 2px;
                padding: 4px 12px;
                font-family: {_SANS};
                font-size: 10px;
            }}
            QPushButton:hover {{
                background: {_BG3};
                color: {_T0};
            }}
        """)

