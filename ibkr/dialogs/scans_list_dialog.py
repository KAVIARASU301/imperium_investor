"""Scans list selection dialog for IBKR scanner mode."""

from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Keep the scans list visually aligned with the scanner/dialog styling without
# coupling this standalone dialog module to the scanner table implementation.
_BG0 = "#050709"
_BG1 = "#0a0d12"
_BG2 = "#0f1318"
_BG3 = "#141920"
_BG4 = "#1a2030"
_BG5 = "#26354a"
_BGTB = "#070a0f"
_T0 = "#e8f0ff"
_T1 = "#a8bcd4"
_T2 = "#5a7090"
_T3 = "#2a3a50"
_SEL = "#1a2840"
_AMBER = "#f59e0b"
_CYAN = "#00d4ff"
_SANS = "'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', Arial, sans-serif"

_ALL_TAGS_LABEL = "All scans"


class ScansListDialog(QDialog):
    """Compact click-to-run scan launcher for saved IBKR scans."""

    def __init__(
        self,
        scans: List[Dict[str, str]],
        parent=None,
        run_scan: Optional[Callable[[int], bool]] = None,
        is_scan_running: Optional[Callable[[], bool]] = None,
    ):
        super().__init__(parent)
        self.scans = list(scans or [])
        self.selected_scan_index: Optional[int] = None
        self._run_scan = run_scan
        self._is_scan_running = is_scan_running
        self._active_tag = _ALL_TAGS_LABEL
        self._drag_active = False
        self._drag_offset = QPoint()
        self.setWindowTitle("Scans List")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setModal(True)
        self.setFixedSize(390, 520)
        self._setup_ui()
        self._apply_styles()
        self.set_scan_running(self._scanner_is_running())

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        title_bar = QFrame()
        title_bar.setObjectName("scanTitleBar")
        title_bar.setFixedHeight(34)
        title_bar.setCursor(Qt.CursorShape.SizeAllCursor)
        title_bar.mousePressEvent = self.mousePressEvent
        title_bar.mouseMoveEvent = self.mouseMoveEvent
        title_bar.mouseReleaseEvent = self.mouseReleaseEvent

        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(10, 0, 6, 0)
        title_layout.setSpacing(8)

        title = QLabel("SCANS LIST")
        title.setObjectName("simpleScanTitle")

        subtitle = QLabel("click to run saved IBKR scans")
        subtitle.setObjectName("simpleScanSubtitle")

        self.status_label = QLabel("READY")
        self.status_label.setObjectName("scanStatusLabel")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        close_title_btn = QPushButton("✕")
        close_title_btn.setObjectName("scanTitleCloseButton")
        close_title_btn.setFixedSize(24, 24)
        close_title_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_title_btn.setToolTip("Close")
        close_title_btn.clicked.connect(self.reject)

        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)
        title_layout.addStretch()
        title_layout.addWidget(self.status_label)
        title_layout.addWidget(close_title_btn)
        root.addWidget(title_bar)

        body = QWidget()
        body.setObjectName("scanDialogBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        filter_layout = QHBoxLayout()
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(6)
        filter_label = QLabel("TAG")
        filter_label.setObjectName("scanFilterLabel")
        self.tag_filter = QComboBox()
        self.tag_filter.setObjectName("scanTagFilter")
        self.tag_filter.setFixedHeight(24)
        self.tag_filter.addItem(_ALL_TAGS_LABEL)
        for tag in self._available_tags():
            self.tag_filter.addItem(tag)
        self.tag_filter.currentTextChanged.connect(self._on_tag_filter_changed)
        filter_layout.addWidget(filter_label)
        filter_layout.addWidget(self.tag_filter, 1)
        layout.addLayout(filter_layout)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("simpleScanList")
        self.list_widget.itemClicked.connect(self._run_item)
        self.list_widget.itemDoubleClicked.connect(self._run_item)
        layout.addWidget(self.list_widget, 1)
        root.addWidget(body, 1)
        self._populate_list()

        footer = QWidget()
        footer.setObjectName("scanFooterBar")
        footer.setFixedHeight(34)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(10, 0, 10, 0)
        footer_layout.setSpacing(8)

        footer_hint = QLabel("SELECT A ROW TO LAUNCH SCAN")
        footer_hint.setObjectName("scanFooterHint")

        close_btn = QPushButton("CLOSE")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedHeight(24)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.reject)

        footer_layout.addWidget(footer_hint)
        footer_layout.addStretch()
        footer_layout.addWidget(close_btn)
        root.addWidget(footer)

    def _scanner_is_running(self) -> bool:
        if self._is_scan_running is None:
            return False
        try:
            return bool(self._is_scan_running())
        except Exception:
            return False

    def _available_tags(self) -> List[str]:
        tags = []
        seen = set()
        for scan in self.scans:
            tag = self._scan_tag(scan)
            key = tag.lower()
            if key not in seen:
                seen.add(key)
                tags.append(tag)
        return sorted(tags, key=str.lower)

    def _on_tag_filter_changed(self, tag: str) -> None:
        if self._scanner_is_running():
            return
        self._active_tag = tag or _ALL_TAGS_LABEL
        self._populate_list()

    def _populate_list(self) -> None:
        self.list_widget.clear()
        for index, scan in self._sorted_scan_items(self._active_tag):
            name = str(scan.get("name") or f"Scan {index + 1}").strip()
            tag = self._scan_tag(scan)
            item = QListWidgetItem(f"{name}\n{tag}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setToolTip(f"Run {name}")
            self.list_widget.addItem(item)

        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _scan_tag(self, scan: Dict[str, str]) -> str:
        tag = str(scan.get("tag") or "Others").strip()
        return tag or "Others"

    def _sorted_scan_items(self, tag_filter: str = _ALL_TAGS_LABEL):
        decorated = []
        wanted = (tag_filter or _ALL_TAGS_LABEL).strip().lower()
        for index, scan in enumerate(self.scans):
            tag = self._scan_tag(scan)
            if wanted != _ALL_TAGS_LABEL.lower() and tag.lower() != wanted:
                continue
            name = str(scan.get("name") or f"Scan {index + 1}")
            decorated.append((tag.lower(), name.lower(), index, scan))
        decorated.sort()
        return [(index, scan) for _tag, _name, index, scan in decorated]

    def _run_item(self, item: QListWidgetItem, *_args) -> None:
        if item is None:
            return
        if self._scanner_is_running():
            self.set_scan_running(True)
            return

        scan_index = item.data(Qt.ItemDataRole.UserRole)
        self.selected_scan_index = scan_index
        if self._run_scan is None:
            return

        try:
            started = bool(self._run_scan(scan_index))
        except Exception:
            started = False

        if started:
            self.set_scan_running(True)

    def set_scan_running(self, running: bool) -> None:
        """Reflect scanner-table fetch/running state in this modal launcher."""
        running = bool(running)
        self.list_widget.setEnabled(not running)
        self.tag_filter.setEnabled(not running)
        self.status_label.setText("RUNNING" if running else "READY")
        self.status_label.setProperty("running", running)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.status_label.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_active and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_active = False
        super().mouseReleaseEvent(event)

    def _apply_styles(self):
        self.setStyleSheet(f"""
            QDialog {{
                background: {_BG1};
                color: {_T0};
                border: 1px solid {_BG4};
            }}
            QFrame#scanTitleBar {{
                background: {_BGTB};
                border-bottom: 1px solid {_BG4};
            }}
            QWidget#scanDialogBody {{
                background: {_BG1};
            }}
            QWidget#scanFooterBar {{
                background: {_BGTB};
                border-top: 1px solid {_BG4};
            }}
            QLabel#simpleScanTitle {{
                color: {_T0};
                font-family: {_SANS};
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 1.0px;
                background: transparent;
            }}
            QLabel#simpleScanSubtitle {{
                color: {_T2};
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 500;
                background: transparent;
            }}
            QLabel#scanStatusLabel {{
                color: {_T2};
                background: {_BG0};
                border: 1px solid {_BG4};
                border-radius: 2px;
                padding: 3px 8px;
                font-family: {_SANS};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 0.7px;
                min-width: 58px;
            }}
            QLabel#scanStatusLabel[running="true"] {{
                color: {_AMBER};
                border-color: {_BG5};
            }}
            QLabel#scanFilterLabel,
            QLabel#scanFooterHint {{
                color: {_T2};
                font-family: {_SANS};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 0.7px;
                background: transparent;
            }}
            QComboBox#scanTagFilter {{
                background: {_BG0};
                color: {_T1};
                border: 1px solid {_BG4};
                border-radius: 2px;
                padding: 2px 8px;
                font-family: {_SANS};
                font-size: 10px;
                selection-background-color: {_SEL};
            }}
            QComboBox#scanTagFilter:hover {{
                border-color: {_BG5};
                color: {_T0};
            }}
            QComboBox#scanTagFilter:disabled {{
                color: {_T3};
                border-color: {_BG2};
            }}
            QComboBox#scanTagFilter QAbstractItemView {{
                background: {_BG0};
                color: {_T1};
                border: 1px solid {_BG4};
                selection-background-color: {_SEL};
                outline: none;
            }}
            QListWidget#simpleScanList {{
                background: {_BG0};
                color: {_T1};
                border: 1px solid {_BG4};
                outline: none;
                font-family: {_SANS};
                font-size: 10px;
                alternate-background-color: {_BG2};
            }}
            QListWidget#simpleScanList::item {{
                min-height: 34px;
                padding: 4px 8px;
                border-bottom: 1px solid {_BG2};
            }}
            QListWidget#simpleScanList::item:hover {{
                background: {_BG3};
                color: {_T0};
            }}
            QListWidget#simpleScanList::item:selected {{
                background: {_SEL};
                color: {_T0};
                border-left: 2px solid {_CYAN};
            }}
            QListWidget#simpleScanList:disabled {{
                color: {_T3};
                border-color: {_BG2};
            }}
            QPushButton#closeButton {{
                background: {_BG2};
                color: {_T1};
                border: 1px solid {_BG4};
                border-radius: 2px;
                padding: 3px 14px;
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }}
            QPushButton#closeButton:hover {{
                background: {_BG3};
                color: {_T0};
                border-color: {_BG5};
            }}
            QPushButton#scanTitleCloseButton {{
                background: transparent;
                color: {_T2};
                border: 1px solid transparent;
                border-radius: 2px;
                font-family: {_SANS};
                font-size: 13px;
                font-weight: 800;
            }}
            QPushButton#scanTitleCloseButton:hover {{
                background: rgba(255, 77, 106, 0.15);
                color: #ff4d6a;
                border-color: rgba(255, 77, 106, 0.25);
            }}
        """)
