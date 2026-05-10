# kite/widgets/alert_management_dialog.py
"""Alert management dialogs for Kite alerts."""

import json
import logging

from datetime import datetime
from typing import List, Optional

from PySide6.QtCore import QPoint, QRect, Qt, Signal, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QToolButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QVBoxLayout,
    QWidget,
)

from kite.core.alert_management_system import (
    Alert,
    AlertCondition,
    AlertIntent,
    AlertStatus,
)


logger = logging.getLogger(__name__)

_BG0 = "#050709"
_BG1 = "#0a0d12"
_BG2 = "#0f1318"
_BG3 = "#141920"
_BG4 = "#1a2030"
_BGTB = "#070a0f"
_BULL = "#00d4a8"
_BEAR = "#ff4d6a"
_AMBER = "#f59e0b"
_CYAN = "#00d4ff"
_BLUE = "#3b82f6"
_T0 = "#e8f0ff"
_T1 = "#a8bcd4"
_T2 = "#5a7090"
_T3 = "#2a3a50"
_SEL = "#1a2840"

_MONO = "'Consolas', 'JetBrains Mono', monospace"
_SANS = "'Inter', 'Segoe UI', sans-serif"

_ROW_H = 24
_DEFAULT_W = 620
_DEFAULT_H = 380


class AlertCreationDialog(QDialog):
    """Compact dialog to create a new alert."""

    alert_created = Signal(object)  # Alert instance

    def __init__(self, symbol: str = "", ltp: float = 0.0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Alert")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._symbol = symbol.upper()
        self._ltp    = ltp
        self._build_ui()
        self._apply_styles()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        container = QFrame()
        container.setObjectName("alertContainer")
        outer.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        # Header
        header = QHBoxLayout()
        title  = QLabel(f"New Alert — {self._symbol}")
        title.setObjectName("alertTitle")
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.reject)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        layout.addLayout(header)

        form = QFormLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)

        # Symbol
        self.symbol_input = QLineEdit(self._symbol)
        self.symbol_input.setFixedWidth(140)
        form.addRow("Symbol:", self.symbol_input)

        # Condition
        self.condition_combo = QComboBox()
        self.condition_combo.addItems([c.value for c in AlertCondition])
        self.condition_combo.setFixedWidth(190)
        self.condition_combo.currentTextChanged.connect(self._update_target_label)
        form.addRow("Condition:", self.condition_combo)

        # Target value
        self.target_label = QLabel("Target Price:")
        self.target_spin  = QDoubleSpinBox()
        self.target_spin.setRange(0, 999_999)
        self.target_spin.setDecimals(2)
        self.target_spin.setValue(self._ltp)
        self.target_spin.setFixedWidth(140)
        form.addRow(self.target_label, self.target_spin)

        # Intent
        self.intent_combo = QComboBox()
        self.intent_combo.addItems([i.value for i in AlertIntent])
        self.intent_combo.setFixedWidth(190)
        form.addRow("Intent:", self.intent_combo)

        # Note
        self.note_input = QLineEdit()
        self.note_input.setPlaceholderText("Optional note…")
        form.addRow("Note:", self.note_input)

        # Repeat
        self.repeat_check = QCheckBox("Re-arm after trigger")
        form.addRow("", self.repeat_check)

        layout.addLayout(form)

        layout.addSpacing(2)

        # Buttons
        btns = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("cancelButton")
        cancel_btn.clicked.connect(self.reject)
        create_btn = QPushButton("Create Alert")
        create_btn.setObjectName("confirmButton")
        create_btn.clicked.connect(self._create)
        btns.addWidget(cancel_btn, 1)
        btns.addWidget(create_btn, 2)
        layout.addLayout(btns)

    def _update_target_label(self, condition_text: str):
        labels = {
            AlertCondition.PERCENT_CHANGE_UP.value:   "% Move Up:",
            AlertCondition.PERCENT_CHANGE_DOWN.value:  "% Move Down:",
            AlertCondition.VOLUME_SPIKE.value:         "Volume Multiplier (N×):",
            AlertCondition.RSI_ABOVE.value:            "RSI Level (>):",
            AlertCondition.RSI_BELOW.value:            "RSI Level (<):",
            AlertCondition.TIME_BASED.value:           "Time (HHMM, e.g. 915):",
        }
        self.target_label.setText(labels.get(condition_text, "Target Price:"))

    def _create(self):
        import uuid
        symbol = self.symbol_input.text().strip().upper()
        if not symbol:
            return
        alert = Alert(
            id=f"alert_{uuid.uuid4().hex[:8]}",
            symbol=symbol,
            condition=self.condition_combo.currentText(),
            intent=self.intent_combo.currentText(),
            target_value=self.target_spin.value(),
            note=self.note_input.text().strip(),
            repeat=self.repeat_check.isChecked(),
        )
        self.alert_created.emit(alert)
        self.accept()

    def _apply_styles(self):
        self.setStyleSheet("""
            * {
                font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
            }
            QFrame#alertContainer {
                background-color: #12141A;
                border: 1px solid #222630;
                border-radius: 2px;
            }
            QLabel {
                color: #A0A6B5;
                font-size: 12px;
                font-weight: 500;
            }
            QLabel#alertTitle {
                color: #FFFFFF;
                font-size: 14px;
                font-weight: 600;
                letter-spacing: 0.5px;
            }
            /* Flat Inputs & Dropdowns */
            QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {
                background-color: #1B1E26;
                color: #FFFFFF;
                border: 1px solid #2A2F3A;
                border-radius: 2px;
                padding: 5px 8px;
                font-size: 12px;
            }
            QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {
                border: 1px solid #00E676;
                background-color: #12141A;
            }
            QComboBox QAbstractItemView {
                background-color: #1B1E26;
                color: #FFFFFF;
                selection-background-color: #2A2F3A;
                selection-color: #FFFFFF;
                border: 1px solid #2A2F3A;
                outline: 0;
            }
            QDoubleSpinBox QLineEdit {
                background-color: #1B1E26;
                color: #FFFFFF;
                selection-background-color: #2A2F3A;
                selection-color: #FFFFFF;
                border: none;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #7B8496;
                margin-right: 4px;
            }
            /* Action Buttons */
            QPushButton {
                font-weight: 600;
                font-size: 12px;
                border-radius: 2px;
            }
            QPushButton#confirmButton {
                background-color: rgba(0, 230, 118, 0.1);
                color: #00E676;
                border: 1px solid transparent;
                padding: 6px 12px;
            }
            QPushButton#confirmButton:hover {
                background-color: rgba(0, 230, 118, 0.15);
                border: 1px solid rgba(0, 230, 118, 0.3);
            }
            QPushButton#cancelButton {
                background-color: transparent;
                color: #7B8496;
                border: 1px solid #2A2F3A;
                padding: 6px 12px;
            }
            QPushButton#cancelButton:hover {
                background-color: #1B1E26;
                color: #FFFFFF;
            }
            QPushButton#closeButton {
                background: transparent;
                color: #7B8496;
                border: none;
                font-size: 16px;
            }
            QPushButton#closeButton:hover {
                color: #FF4444;
            }
            QCheckBox {
                color: #A0A6B5;
                font-size: 12px;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #2A2F3A;
                border-radius: 2px;
                background: #1B1E26;
            }
            QCheckBox::indicator:checked {
                background: #00E676;
                border: 1px solid #00E676;
            }
        """)



class _ResizeGrip(QWidget):
    """Small bottom-right resize handle for the compact alert manager."""

    SIZE = 10

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
        self._dragging = False
        self._p0 = QPoint()
        self._g0 = QRect()

    def paintEvent(self, _event):
        painter = QPainter(self)
        pen = QPen(QColor(_BG4))
        pen.setWidth(1)
        painter.setPen(pen)
        n = self.SIZE
        for i in range(2, n, 3):
            painter.drawLine(i, n - 1, n - 1, i)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._p0 = event.globalPosition().toPoint()
            self._g0 = self.window().geometry()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            delta = event.globalPosition().toPoint() - self._p0
            self.window().setGeometry(
                self._g0.x(),
                self._g0.y(),
                max(480, self._g0.width() + delta.x()),
                max(280, self._g0.height() + delta.y()),
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        super().mouseReleaseEvent(event)


def _action_btn(glyph: str, color: str, tooltip: str, callback) -> QToolButton:
    """Create a tiny square glyph button for use inside table action cells."""
    btn = QToolButton()
    btn.setText(glyph)
    btn.setToolTip(tooltip)
    btn.setFixedSize(22, 20)
    btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    btn.setStyleSheet(f"""
        QToolButton {{
            background: rgba(255,255,255,0.04);
            color: {color};
            border: 1px solid {color}44;
            border-radius: 2px;
            font-size: 11px;
            font-weight: 700;
        }}
        QToolButton:hover {{
            background: {color}22;
            border-color: {color};
        }}
    """)
    btn.clicked.connect(callback)
    return btn


class _ActionCell(QWidget):
    """Transparent container widget that holds one or more action buttons."""

    def __init__(self, *btns, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 2, 3, 2)
        layout.setSpacing(4)
        for btn in btns:
            layout.addWidget(btn)
        layout.addStretch()
        self.setStyleSheet("background: transparent;")


class AlertManagementDialog(QDialog):
    """
    Compact floating alert panel.

    Public API remains compatible with the original AlertManagementDialog:
        AlertManagementDialog(manager, parent).show()
        dialog.refresh_tables()
    """

    _STATE_KEY = "compact_alert_mgmt_dialog"

    def __init__(self, manager: "AlertSystemManager", parent=None):
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumSize(480, 260)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        self.manager = manager
        self.store = manager.store

        self._pinned = True
        self._drag_active = False
        self._drag_offset = QPoint()
        self._prev_spacebar_enabled = None
        self._prev_shift_spacebar_enabled = None
        self._geometry_restored = False
        self._last_snapshot = None
        self._table_snapshots = {"active": {}, "triggered": {}, "history": {}}

        self._build_ui()
        self._apply_styles()
        self._restore_geometry()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_tables)
        self._refresh_timer.start(3_000)

        self.refresh_tables()
        self._wire_symbol_navigation()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        root.addWidget(self._build_title_bar())

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(8, 6, 8, 6)
        body_layout.setSpacing(4)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("alertTabs")
        self.tabs.setDocumentMode(True)

        self.active_table = self._make_table(["Symbol", "Condition", "Target", ""])
        self.triggered_table = self._make_table(["Symbol", "Condition", "Target", "Triggered", ""])
        self.history_table = self._make_table(["Symbol", "Condition", "Target", "Triggered"])

        self.tabs.addTab(self.active_table, "Active")
        self.tabs.addTab(self.triggered_table, "Triggered")
        self.tabs.addTab(self.history_table, "History")

        body_layout.addWidget(self.tabs)
        root.addWidget(body, 1)
        root.addWidget(self._build_footer())

        self._grip = _ResizeGrip(self)

    def _build_title_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("alertTitleBar")
        bar.setFixedHeight(28)
        bar.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(6)

        badge = QLabel("⚑ ALERTS")
        badge.setObjectName("alertBadge")
        self._count_lbl = QLabel("0 active")
        self._count_lbl.setObjectName("alertCountLbl")

        layout.addWidget(badge)
        layout.addWidget(self._count_lbl)
        layout.addStretch()

        refresh_btn = QToolButton()
        refresh_btn.setText("↺")
        refresh_btn.setObjectName("alertBarBtn")
        refresh_btn.setFixedSize(22, 22)
        refresh_btn.setToolTip("Refresh")
        refresh_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        refresh_btn.clicked.connect(self.refresh_tables)

        self._pin_btn = QToolButton()
        self._pin_btn.setText("📌")
        self._pin_btn.setObjectName("alertBarBtn")
        self._pin_btn.setFixedSize(22, 22)
        self._pin_btn.setToolTip("Toggle always-on-top")
        self._pin_btn.setCheckable(True)
        self._pin_btn.setChecked(True)
        self._pin_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._pin_btn.toggled.connect(self._toggle_pin)

        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setObjectName("alertCloseBtn")
        close_btn.setFixedSize(22, 22)
        close_btn.setToolTip("Close")
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self.close)

        layout.addWidget(refresh_btn)
        layout.addWidget(self._pin_btn)
        layout.addWidget(close_btn)

        bar.mousePressEvent = self._tb_press
        bar.mouseMoveEvent = self._tb_move
        bar.mouseReleaseEvent = self._tb_release
        return bar

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("alertFooter")
        footer.setFixedHeight(26)

        layout = QHBoxLayout(footer)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(10)

        self._status_lbl = QLabel("Auto-refresh: 3s")
        self._status_lbl.setObjectName("alertStatusLbl")
        self.status_label = self._status_lbl
        layout.addWidget(self._status_lbl)
        layout.addStretch()

        add_btn = QPushButton("+ New Alert")
        add_btn.setObjectName("alertAddBtn")
        add_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        add_btn.clicked.connect(self._add_new)
        layout.addWidget(add_btn)
        return footer

    def _make_table(self, headers: List[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)

        header = table.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        for index, name in enumerate(headers):
            if name == "":
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.Fixed)
                table.setColumnWidth(index, 56)
            elif name == "Symbol":
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
            elif name == "Condition":
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
            elif name in ("Target", "Triggered"):
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)

        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(_ROW_H)
        table.verticalHeader().setMinimumSectionSize(_ROW_H)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        return table

    def _tb_press(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _tb_move(self, event: QMouseEvent):
        if self._drag_active and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def _tb_release(self, _event):
        self._drag_active = False

    def _toggle_pin(self, pinned: bool):
        self._pinned = pinned
        flags = self.windowFlags()
        if pinned:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    def _restore_geometry(self):
        cfg = getattr(self.parent(), "config_manager", None)
        if not cfg:
            return
        try:
            raw = cfg.load_dialog_state(self._STATE_KEY)
            if raw:
                data = json.loads(raw)
                self.resize(data.get("w", _DEFAULT_W), data.get("h", _DEFAULT_H))
                if "x" in data and "y" in data:
                    self.move(data["x"], data["y"])
                    self._geometry_restored = True
        except Exception as exc:
            logger.debug("Alert dialog geometry restore failed: %s", exc)

    def _save_geometry(self):
        cfg = getattr(self.parent(), "config_manager", None)
        if not cfg:
            return
        try:
            data = json.dumps({
                "x": self.x(),
                "y": self.y(),
                "w": self.width(),
                "h": self.height(),
            })
            cfg.save_dialog_state(self._STATE_KEY, data)
        except Exception as exc:
            logger.debug("Alert dialog geometry save failed: %s", exc)

    def _set_parent_spacebar_shortcuts_enabled(self, enabled: bool) -> None:
        """Backward-compatible alias for the original shortcut guard helper."""
        self._set_parent_shortcuts_enabled(enabled)

    def _set_parent_shortcuts_enabled(self, enabled: bool):
        parent = self.parent()
        if not parent:
            return
        for attr, state_attr in (
            ("spacebar_shortcut", "_prev_spacebar_enabled"),
            ("shift_spacebar_shortcut", "_prev_shift_spacebar_enabled"),
        ):
            shortcut = getattr(parent, attr, None)
            if shortcut is None:
                continue
            if enabled:
                previous_state = getattr(self, state_attr)
                if previous_state is not None:
                    shortcut.setEnabled(previous_state)
                    setattr(self, state_attr, None)
            else:
                if getattr(self, state_attr) is None:
                    setattr(self, state_attr, shortcut.isEnabled())
                shortcut.setEnabled(False)

    def refresh_tables(self):
        all_alerts = self.store.all()
        active = [alert for alert in all_alerts if alert.status == AlertStatus.ACTIVE.value]
        triggered = [alert for alert in all_alerts if alert.status == AlertStatus.TRIGGERED.value]
        history = [
            alert for alert in all_alerts
            if alert.status in (AlertStatus.TRIGGERED.value, AlertStatus.EXPIRED.value)
        ]

        snapshot = (
            tuple(self._snapshot_alert(a) for a in active),
            tuple(self._snapshot_alert(a) for a in triggered),
            tuple(self._snapshot_alert(a) for a in history),
        )
        if snapshot == self._last_snapshot:
            return

        self._update_table_incremental("active", self.active_table, active, self._apply_active_row)
        self._update_table_incremental("triggered", self.triggered_table, triggered, self._apply_triggered_row)
        self._update_table_incremental("history", self.history_table, history, self._apply_history_row)

        self.tabs.setTabText(0, f"Active ({len(active)})")
        self.tabs.setTabText(1, f"Triggered ({len(triggered)})")
        self.tabs.setTabText(2, f"History ({len(history)})")
        self._count_lbl.setText(f"{len(active)} active")
        self._status_lbl.setText(f"Active: {len(active)}  ·  Triggered: {len(triggered)}")
        self._last_snapshot = snapshot

    @staticmethod
    def _fmt_indian_datetime(dt_text: Optional[str]) -> str:
        """Backward-compatible datetime formatter alias used by the previous dialog."""
        return AlertManagementDialog._fmt_dt(dt_text)

    @staticmethod
    def _fmt_dt(iso: Optional[str]) -> str:
        if not iso:
            return "—"
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return dt.strftime("%d-%b %H:%M")
        except Exception:
            return str(iso)[:16]

    def _cell(
        self,
        text: str,
        align=Qt.AlignmentFlag.AlignLeft,
        color: str = _T0,
        mono: bool = False,
    ) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setForeground(QBrush(QColor(color)))
        item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        if mono:
            item.setFont(QFont("Consolas, JetBrains Mono", 9))
        return item

    @staticmethod
    def _snapshot_alert(alert):
        return (
            alert.id,
            alert.status,
            alert.symbol,
            alert.condition,
            float(alert.target_value),
            alert.triggered_at,
        )

    def _update_table_incremental(self, table_key, table, alerts, row_updater):
        new_ids = [alert.id for alert in alerts]
        old_ids = [
            table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            for row in range(table.rowCount())
            if table.item(row, 0) is not None
        ]
        if old_ids != new_ids:
            table.setRowCount(len(alerts))
            for row, alert in enumerate(alerts):
                row_updater(table, row, alert)
            self._table_snapshots[table_key] = {a.id: self._snapshot_alert(a) for a in alerts}
            return

        cached = self._table_snapshots.get(table_key, {})
        for row, alert in enumerate(alerts):
            fresh = self._snapshot_alert(alert)
            if cached.get(alert.id) != fresh:
                row_updater(table, row, alert)
                cached[alert.id] = fresh
        self._table_snapshots[table_key] = cached

    def _apply_active_row(self, table, row, alert):
        symbol_item = self._cell(alert.symbol, color=_T0)
        symbol_item.setData(Qt.ItemDataRole.UserRole, alert.id)
        table.setItem(row, 0, symbol_item)
        table.setItem(row, 1, self._cell(alert.condition, color=_T2))
        table.setItem(
            row,
            2,
            self._cell(
                f"₹{alert.target_value:.2f}",
                Qt.AlignmentFlag.AlignRight,
                _AMBER,
                mono=True,
            ),
        )
        del_btn = _action_btn(
            "✕",
            _BEAR,
            f"Delete alert for {alert.symbol}",
            lambda _checked=False, aid=alert.id: self._delete_alert(aid),
        )
        table.setCellWidget(row, 3, _ActionCell(del_btn))

    def _apply_triggered_row(self, table, row, alert):
        symbol_item = self._cell(alert.symbol, color=_AMBER)
        symbol_item.setData(Qt.ItemDataRole.UserRole, alert.id)
        table.setItem(row, 0, symbol_item)
        table.setItem(row, 1, self._cell(alert.condition, color=_T2))
        table.setItem(
            row,
            2,
            self._cell(
                f"₹{alert.target_value:.2f}",
                Qt.AlignmentFlag.AlignRight,
                _AMBER,
                mono=True,
            ),
        )
        table.setItem(row, 3, self._cell(self._fmt_dt(alert.triggered_at), color=_T2, mono=True))
        ack_btn = _action_btn(
            "✓",
            _BULL,
            "Acknowledge & move to history",
            lambda _checked=False, aid=alert.id: self._ack_alert(aid),
        )
        del_btn = _action_btn(
            "✕",
            _BEAR,
            "Delete",
            lambda _checked=False, aid=alert.id: self._delete_alert(aid),
        )
        table.setCellWidget(row, 4, _ActionCell(ack_btn, del_btn))

    def _apply_history_row(self, table, row, alert):
        symbol_item = self._cell(alert.symbol, color=_T1)
        symbol_item.setData(Qt.ItemDataRole.UserRole, alert.id)
        table.setItem(row, 0, symbol_item)
        table.setItem(row, 1, self._cell(alert.condition, color=_T2))
        table.setItem(
            row,
            2,
            self._cell(
                f"₹{alert.target_value:.2f}",
                Qt.AlignmentFlag.AlignRight,
                _T2,
                mono=True,
            ),
        )
        table.setItem(row, 3, self._cell(self._fmt_dt(alert.triggered_at), color=_T3, mono=True))

    def _populate_active(self, alerts):
        table = self.active_table
        table.setRowCount(len(alerts))
        for row, alert in enumerate(alerts):
            self._apply_active_row(table, row, alert)

    def _populate_triggered(self, alerts):
        table = self.triggered_table
        table.setRowCount(len(alerts))
        for row, alert in enumerate(alerts):
            self._apply_triggered_row(table, row, alert)

    def _populate_history(self, alerts):
        table = self.history_table
        table.setRowCount(len(alerts))
        for row, alert in enumerate(alerts):
            self._apply_history_row(table, row, alert)

    def _wire_symbol_navigation(self):
        for table in (self.active_table, self.triggered_table, self.history_table):
            table.cellClicked.connect(
                lambda row, _col, tbl=table: self._open_symbol_from_row(tbl, row)
            )

    def _open_selected_symbol_in_chart(self, table: QTableWidget) -> None:
        row = table.currentRow()
        if row >= 0:
            self._open_symbol_from_row(table, row)

    def _open_symbol_from_row(self, table: QTableWidget, row: int):
        if row < 0:
            return
        item = table.item(row, 0)
        if not item:
            return
        symbol = (item.text() or "").strip().upper()
        if not symbol:
            return
        chart = getattr(self.parent(), "candlestick_chart", None)
        if chart and hasattr(chart, "on_search"):
            chart.on_search(symbol)

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Down, Qt.Key.Key_Up):
            table = self.tabs.currentWidget()
            if isinstance(table, QTableWidget) and table.rowCount() > 0:
                current_row = table.currentRow()
                if key == Qt.Key.Key_Up:
                    next_row = max(0, (current_row if current_row >= 0 else 0) - 1)
                else:
                    next_row = (current_row + 1) % table.rowCount()
                table.selectRow(next_row)
                table.setCurrentCell(next_row, 0)
                self._open_symbol_from_row(table, next_row)
                event.accept()
                return
        super().keyPressEvent(event)

    def _add_new(self):
        dialog = AlertCreationDialog(parent=self)
        dialog.alert_created.connect(self.manager.add_alert)
        dialog.alert_created.connect(lambda _alert: self.refresh_tables())
        dialog.exec()

    def _delete_alert(self, alert_id: str):
        self.manager.remove_alert(alert_id)
        self.refresh_tables()

    def _ack_alert(self, alert_id: str):
        self.manager.acknowledge_triggered_alert(alert_id)
        self.refresh_tables()

    def showEvent(self, event):
        self._set_parent_shortcuts_enabled(False)
        super().showEvent(event)
        if not self._geometry_restored:
            self._center_on_parent()
            self._geometry_restored = True

    def closeEvent(self, event):
        self._save_geometry()
        self._set_parent_shortcuts_enabled(True)
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_grip"):
            self._grip.move(
                self.width() - _ResizeGrip.SIZE,
                self.height() - _ResizeGrip.SIZE,
            )

    def _center_on_parent(self):
        screen = QApplication.primaryScreen().availableGeometry()
        parent = self.parent()
        if parent:
            parent_geometry = parent.frameGeometry()
            x = parent_geometry.right() - self.width() - 16
            y = parent_geometry.top() + 60
        else:
            x = screen.right() - self.width() - 20
            y = screen.top() + 60
        x = max(screen.left(), min(x, screen.right() - self.width()))
        y = max(screen.top(), min(y, screen.bottom() - self.height()))
        self.move(x, y)

    def _apply_styles(self):
        self.setStyleSheet(f"""
        AlertManagementDialog {{
            background: {_BG1};
            border: 1px solid {_BG4};
        }}
        QFrame#alertTitleBar {{
            background: {_BGTB};
            border-bottom: 1px solid {_BG4};
        }}
        QLabel#alertBadge {{
            color: {_AMBER};
            font-family: {_MONO};
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 1px;
            background: transparent;
        }}
        QLabel#alertCountLbl {{
            color: {_T2};
            font-family: {_SANS};
            font-size: 10px;
            font-weight: 600;
            background: transparent;
        }}
        QToolButton#alertBarBtn {{
            background: transparent;
            color: {_T2};
            border: none;
            font-size: 12px;
            border-radius: 2px;
        }}
        QToolButton#alertBarBtn:hover {{
            background: rgba(255,255,255,0.07);
            color: {_T0};
        }}
        QToolButton#alertBarBtn:checked {{
            color: {_CYAN};
        }}
        QToolButton#alertCloseBtn {{
            background: transparent;
            color: {_T2};
            border: none;
            font-size: 12px;
            border-radius: 2px;
        }}
        QToolButton#alertCloseBtn:hover {{
            background: rgba(255,77,106,0.15);
            color: {_BEAR};
        }}
        QTabWidget#alertTabs {{
            border: none;
        }}
        QTabWidget#alertTabs::pane {{
            border: none;
            border-top: 1px solid {_BG4};
        }}
        QTabBar::tab {{
            background: transparent;
            color: {_T2};
            padding: 4px 12px;
            border: none;
            border-bottom: 2px solid transparent;
            font-family: {_SANS};
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 0.5px;
        }}
        QTabBar::tab:selected {{
            color: {_T0};
            border-bottom: 2px solid {_AMBER};
        }}
        QTabBar::tab:hover:!selected {{
            color: {_T1};
        }}
        QTableWidget {{
            background: {_BG1};
            alternate-background-color: {_BG2};
            gridline-color: transparent;
            border: none;
            outline: none;
            selection-background-color: {_SEL};
            font-family: {_SANS};
            font-size: 11px;
            color: {_T0};
        }}
        QTableWidget::item {{
            padding: 0 6px;
            border-bottom: 1px solid {_BG3};
        }}
        QTableWidget::item:selected {{
            background: {_SEL};
            color: {_T0};
        }}
        QTableWidget::item:hover {{
            background: {_BG3};
        }}
        QHeaderView::section {{
            background: {_BG2};
            color: {_T2};
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 800;
            letter-spacing: 1.2px;
            text-transform: uppercase;
            border: none;
            border-bottom: 1px solid {_BG4};
            border-right: none;
            padding: 0 6px;
            min-height: 20px;
        }}
        QFrame#alertFooter {{
            background: {_BGTB};
            border-top: 1px solid {_BG4};
        }}
        QLabel#alertStatusLbl {{
            color: {_T2};
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 600;
            background: transparent;
        }}
        QPushButton#alertAddBtn {{
            background: rgba(0,212,255,0.07);
            color: {_CYAN};
            border: 1px solid rgba(0,212,255,0.20);
            border-radius: 2px;
            font-family: {_SANS};
            font-size: 10px;
            font-weight: 700;
            padding: 2px 10px;
        }}
        QPushButton#alertAddBtn:hover {{
            background: rgba(0,212,255,0.14);
            border-color: {_CYAN};
        }}
        QScrollBar:vertical {{
            background: transparent; width: 4px; border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {_BG4}; border-radius: 2px; min-height: 16px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {_T2}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0; border: none;
        }}
        QScrollBar:horizontal {{
            background: transparent; height: 4px; border: none;
        }}
        QScrollBar::handle:horizontal {{
            background: {_BG4}; border-radius: 2px; min-width: 16px;
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0; border: none;
        }}
        """)
