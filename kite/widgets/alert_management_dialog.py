# kite/widgets/alert_management_dialog.py
"""Alert management dialogs for Kite alerts."""

from datetime import datetime
from typing import List, Optional

from PySide6.QtCore import Qt, Signal, QTimer
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


class AlertManagementDialog(QDialog):
    """Three-tab dialog: Active | Triggered | History."""

    def __init__(self, manager: "AlertSystemManager", parent=None):
        super().__init__(parent)
        # FIX #4 / #10: Accept the full manager (not just store) so that
        # add / delete operations go through chart-line integration.
        self.manager = manager
        self.store   = manager.store   # kept for read-only queries

        self.setWindowTitle("ALERT MANAGER")
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.resize(1000, 660)
        self.setMinimumSize(900, 560)

        self._drag_active = False
        self._drag_offset = None
        self._parent_spacebar_shortcut_prev_enabled = None
        self._parent_shift_spacebar_shortcut_prev_enabled = None
        self._build_ui()
        self._apply_styles()

        # Refresh every 3 seconds
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_tables)
        self._refresh_timer.start(3_000)
        self.refresh_tables()
        self._wire_symbol_navigation()

    def _set_parent_spacebar_shortcuts_enabled(self, enabled: bool) -> None:
        """Temporarily disable main-window global spacebar shortcuts while this dialog is open."""
        parent = self.parent()
        if not parent:
            return

        for attr_name, state_attr in (
            ("spacebar_shortcut", "_parent_spacebar_shortcut_prev_enabled"),
            ("shift_spacebar_shortcut", "_parent_shift_spacebar_shortcut_prev_enabled"),
        ):
            shortcut = getattr(parent, attr_name, None)
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

    def closeEvent(self, event) -> None:
        self._set_parent_spacebar_shortcuts_enabled(True)
        super().closeEvent(event)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        container = QFrame()
        container.setObjectName("alertMgmtContainer")
        outer.addWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Title bar (36px fixed)
        title_bar = QFrame()
        title_bar.setObjectName("titleBar")
        title_bar.setFixedHeight(36)
        header = QHBoxLayout(title_bar)
        header.setContentsMargins(10, 0, 8, 0)
        header.setSpacing(8)
        badge = QLabel("ALERT")
        badge.setObjectName("categoryBadge")
        title = QLabel("ALERT MANAGER")
        title.setObjectName("mgmtTitle")
        refresh_btn = QPushButton("↺")
        refresh_btn.setObjectName("titleToolBtn")
        refresh_btn.setFixedSize(26, 26)
        refresh_btn.clicked.connect(self.refresh_tables)
        minimize_btn = QPushButton("−")
        minimize_btn.setObjectName("titleToolBtn")
        minimize_btn.setFixedSize(26, 26)
        minimize_btn.clicked.connect(self.showMinimized)
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedSize(26, 26)
        close_btn.clicked.connect(self.close)
        header.addWidget(badge)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(refresh_btn)
        header.addWidget(minimize_btn)
        header.addWidget(close_btn)
        layout.addWidget(title_bar)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 16, 16, 16)
        body_layout.setSpacing(12)

        self.tabs = QTabWidget()
        self.active_table    = self._make_table(
            ["Symbol", "Condition", "Target", "Created", "Action"])
        self.triggered_table = self._make_table(
            ["Symbol", "Condition", "Target", "Triggered At", "Action"])
        self.history_table   = self._make_table(
            ["Symbol", "Condition", "Target", "Triggered At", "Count"])

        self.tabs.addTab(self.active_table,    "Active")
        self.tabs.addTab(self.triggered_table, "Triggered")
        self.tabs.addTab(self.history_table,   "History")
        body_layout.addWidget(self.tabs)
        layout.addWidget(body)

        footer = QFrame()
        footer.setObjectName("footerBar")
        footer.setFixedHeight(40)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(12, 0, 12, 0)
        footer_layout.setSpacing(8)
        self.status_label = QLabel("Auto refresh every 3s")
        self.status_label.setObjectName("statusLabel")
        footer_layout.addWidget(self.status_label)
        footer_layout.addStretch()
        layout.addWidget(footer)

    def _make_table(self, headers: List[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(34)
        t.verticalHeader().setMinimumSectionSize(34)
        t.setAlternatingRowColors(True)
        t.setShowGrid(False)
        header = t.horizontalHeader()
        for col, name in enumerate(headers):
            h_item = t.horizontalHeaderItem(col)
            if not h_item:
                continue
            if name == "Symbol":
                h_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            elif name in ("Condition", "Target", "Created", "Triggered At"):
                h_item.setTextAlignment(Qt.AlignCenter)
            elif name == "Count":
                h_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignRight)
            else:
                h_item.setTextAlignment(Qt.AlignCenter)
        if "Action" in headers:
            action_col = headers.index("Action")
            header.setSectionResizeMode(action_col, QHeaderView.ResizeMode.Fixed)
            t.setColumnWidth(action_col, 124)
        return t

    def _wire_symbol_navigation(self) -> None:
        """Wire stable row selection: only user actions should open chart symbols."""
        for table in (self.active_table, self.triggered_table, self.history_table):
            table.setSelectionMode(QAbstractItemView.SingleSelection)
            table.setFocusPolicy(Qt.StrongFocus)
            table.cellClicked.connect(
                lambda row, _col, t=table: self._open_symbol_from_row(t, row)
            )

    def _open_selected_symbol_in_chart(self, table: QTableWidget) -> None:
        """Backward-compatible helper used by keyboard navigation."""
        row = table.currentRow()
        if row < 0:
            return
        self._open_symbol_from_row(table, row)

    def _open_symbol_from_row(self, table: QTableWidget, row: int) -> None:
        if row < 0:
            return

        symbol_item = table.item(row, 0)
        if not symbol_item:
            return

        symbol = (symbol_item.text() or "").strip().upper()
        if not symbol:
            return

        chart = getattr(self.parent(), "candlestick_chart", None)
        if chart and hasattr(chart, "on_search"):
            chart.on_search(symbol)

    def keyPressEvent(self, event) -> None:
        """Support scanner/watchlist-like keyboard stepping with stable selection."""
        key = event.key()
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Down, Qt.Key.Key_Up):
            table = self.tabs.currentWidget()
            if isinstance(table, QTableWidget):
                row_count = table.rowCount()
                if row_count == 0:
                    event.accept()
                    return

                current_row = table.currentRow()
                if current_row < 0:
                    current_row = 0

                if key in (Qt.Key.Key_Space, Qt.Key.Key_Down):
                    next_row = min(current_row + 1, row_count - 1)
                else:
                    next_row = max(current_row - 1, 0)

                table.selectRow(next_row)
                table.setCurrentCell(next_row, 0)
                table.setFocus()
                self._open_symbol_from_row(table, next_row)
                event.accept()
                return

        super().keyPressEvent(event)

    @staticmethod
    def _fmt_indian_datetime(dt_text: Optional[str]) -> str:
        """Format ISO datetime to readable Indian-style date/time."""
        if not dt_text:
            return ""
        try:
            clean_text = dt_text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_text)
            return dt.strftime("%d-%m-%Y %I:%M %p")
        except Exception:
            return str(dt_text)[:16]

    @staticmethod
    def _set_row_alignment(table: QTableWidget, row: int):
        """Keep cell alignments consistent with header expectations."""
        left_item = table.item(row, 0)
        if left_item:
            left_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        for col in (1, 2, 3):
            item = table.item(row, col)
            if item:
                item.setTextAlignment(Qt.AlignCenter)

    def refresh_tables(self):
        alerts    = self.store.all()
        active    = [a for a in alerts if a.status == AlertStatus.ACTIVE.value]
        triggered = [a for a in alerts if a.status == AlertStatus.TRIGGERED.value]
        history   = [a for a in alerts if a.status in (
            AlertStatus.TRIGGERED.value, AlertStatus.EXPIRED.value)]

        self._populate_active(active)
        self._populate_triggered(triggered)
        self._populate_history(history)

        self.tabs.setTabText(0, f"Active ({len(active)})")
        self.tabs.setTabText(1, f"Triggered ({len(triggered)})")
        self.status_label.setText(
            f"Active: {len(active)}  |  Triggered: {len(triggered)}  |  Total: {len(alerts)}"
        )

    def _populate_active(self, alerts: List[Alert]):
        t = self.active_table
        t.setRowCount(len(alerts))
        for row, a in enumerate(alerts):
            t.setItem(row, 0, QTableWidgetItem(a.symbol))
            t.setItem(row, 1, QTableWidgetItem(a.condition))
            t.setItem(row, 2, QTableWidgetItem(f"{a.target_value:.2f}"))
            t.setItem(row, 3, QTableWidgetItem(self._fmt_indian_datetime(a.created_at)))

            del_btn = QPushButton("Delete")
            del_btn.setObjectName("deleteButton")
            del_btn.setMinimumWidth(74)
            # FIX #5 / #6: route through manager so chart line is also removed
            del_btn.clicked.connect(lambda _, aid=a.id: self._delete_alert(aid))
            t.setCellWidget(row, 4, del_btn)
            self._set_row_alignment(t, row)

    def _populate_triggered(self, alerts: List[Alert]):
        t = self.triggered_table
        t.setRowCount(len(alerts))
        for row, a in enumerate(alerts):
            t.setItem(row, 0, QTableWidgetItem(a.symbol))
            t.setItem(row, 1, QTableWidgetItem(a.condition))
            t.setItem(row, 2, QTableWidgetItem(f"{a.target_value:.2f}"))
            t.setItem(row, 3, QTableWidgetItem(self._fmt_indian_datetime(a.triggered_at)))

            ack_btn = QPushButton("Ack")
            ack_btn.setObjectName("ackButton")
            ack_btn.setMinimumWidth(74)
            ack_btn.clicked.connect(lambda _, aid=a.id: self._ack_alert(aid))
            t.setCellWidget(row, 4, ack_btn)
            self._set_row_alignment(t, row)

    def _populate_history(self, alerts: List[Alert]):
        t = self.history_table
        t.setRowCount(len(alerts))
        for row, a in enumerate(alerts):
            t.setItem(row, 0, QTableWidgetItem(a.symbol))
            t.setItem(row, 1, QTableWidgetItem(a.condition))
            t.setItem(row, 2, QTableWidgetItem(f"{a.target_value:.2f}"))
            t.setItem(row, 3, QTableWidgetItem(self._fmt_indian_datetime(a.triggered_at)))
            t.setItem(row, 4, QTableWidgetItem(str(a._trigger_count)))
            self._set_row_alignment(t, row)

    def _add_new(self):
        dlg = AlertCreationDialog(parent=self)
        # FIX #5: route through manager.add_alert() so chart line is drawn
        dlg.alert_created.connect(self.manager.add_alert)
        dlg.alert_created.connect(lambda _: self.refresh_tables())
        dlg.exec()

    def _delete_alert(self, alert_id: str):
        # FIX #6: route through manager.remove_alert() so chart line is removed
        self.manager.remove_alert(alert_id)
        self.refresh_tables()

    def _ack_alert(self, alert_id: str):
        """Acknowledge triggered alert — move it to expired/history."""
        self.manager.acknowledge_triggered_alert(alert_id)
        self.refresh_tables()

    def showEvent(self, event):
        self._set_parent_spacebar_shortcuts_enabled(False)
        super().showEvent(event)
        self._center_on_parent()

    def _center_on_parent(self):
        if self.parent():
            parent_geo = self.parent().frameGeometry()
            center = parent_geo.center()
            self.move(center - self.rect().center())
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.center() - self.rect().center())

    def mousePressEvent(self, event):
        w = self.childAt(event.pos())
        while w:
            if isinstance(w, (QAbstractButton, QAbstractSpinBox,
                              QLineEdit, QComboBox, QTableWidget)):
                return super().mousePressEvent(event)
            w = w.parentWidget()
        if event.button() == Qt.LeftButton:
            self._drag_active = True
            self._drag_offset = (event.globalPosition().toPoint()
                                 - self.frameGeometry().topLeft())
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_active and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_active = False
        super().mouseReleaseEvent(event)

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #0a0d12;
                color: #e8f0ff;
                font-family: Inter, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
            }
            QFrame#alertMgmtContainer {
                background-color: #0a0d12;
                border: 1px solid #1a2030;
                border-radius: 2px;
            }
            QFrame#titleBar {
                background-color: #070a0f;
                border-bottom: 1px solid #1a2030;
            }
            QLabel#categoryBadge {
                color: #f59e0b;
                font-family: Consolas, "JetBrains Mono", "Courier New", monospace;
                font-size: 9px;
                font-weight: 700;
            }
            QLabel#mgmtTitle {
                color: #e8f0ff;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 0.5px;
            }
            QLabel#statusLabel {
                color: #5a7090;
                font-size: 11px;
            }
            QFrame#footerBar {
                background-color: #070a0f;
                border-top: 1px solid #1a2030;
            }
            QTableWidget {
                background-color: #0f1318;
                border: 1px solid #1a2030;
                gridline-color: transparent;
                font-size: 13px;
                alternate-background-color: #0f1318;
                selection-background-color: #1a2840;
            }
            QTableWidget::item {
                padding: 4px 8px;
                border-bottom: 1px solid #1a2030;
            }
            QTableWidget::item:hover {
                background-color: #141920;
            }
            QTableWidget::item:selected {
                background-color: #1a2840;
            }
            QHeaderView::section {
                background-color: #1B1E26;
                color: #7b8496;
                font-weight: 600;
                letter-spacing: 0.5px;
                text-transform: uppercase;
                border: none;
                padding: 6px 8px;
                font-size: 11px;
                text-align: left;
            }
            QPushButton {
                background-color: rgba(255, 255, 255, 0.05);
                color: #e0e0e0;
                border-radius: 2px;
                padding: 6px 14px;
                font-weight: bold;
                border: 1px solid transparent;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.1);
            }
            QPushButton#ackButton {
                background-color: rgba(0, 230, 118, 0.1);
                color: #00E676;
            }
            QPushButton#ackButton:hover {
                background-color: rgba(0, 230, 118, 0.15);
            }
            QPushButton#deleteButton {
                background-color: rgba(239, 83, 80, 0.1);
                color: #ef5350;
                padding: 4px 10px;
            }
            QPushButton#deleteButton:hover {
                background-color: rgba(239, 83, 80, 0.15);
            }
            QPushButton#titleToolBtn {
                background: transparent;
                color: #5a7090;
                border: none;
                font-size: 14px;
                font-weight: bold;
                border-radius: 2px;
                padding: 0;
            }
            QPushButton#titleToolBtn:hover {
                background: #141920;
                color: #00d4ff;
            }
            QPushButton#closeBtn {
                background: transparent;
                color: #5a7090;
                border: none;
                font-size: 14px;
                font-weight: bold;
                border-radius: 2px;
                padding: 0;
            }
            QPushButton#closeBtn:hover {
                background: rgba(255, 77, 106, 0.15);
                color: #ff4d6a;
            }
            QLineEdit, QComboBox, QDoubleSpinBox {
                background-color: #1B1E26;
                border: 1px solid transparent;
                border-radius: 2px;
                padding: 5px 8px;
                color: #ffffff;
            }
            QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {
                border: 1px solid #3b4252;
                background-color: #15181e;
            }
            QTabWidget::pane {
                border: none;
                border-top: 1px solid #222630;
            }
            QTabBar::tab {
                background: transparent;
                color: #7B8496;
                padding: 8px 16px;
                border: none;
                font-size: 12px;
                font-weight: 600;
            }
            QTabBar::tab:selected {
                background: transparent;
                color: #FFFFFF;
                border-bottom: 2px solid #00E676;
            }
            QTabBar::tab:hover:!selected {
                color: #D1D4DC;
            }
        """)
