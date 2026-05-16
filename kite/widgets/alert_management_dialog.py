"""Production-grade alert management dialog for Kite alerts.

This module intentionally contains only alert-management UI.  Alert creation UI
and the old "+ New Alert" footer action were removed so the panel stays focused
on monitoring, acknowledging, deleting, and navigating existing alerts.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QIcon,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from kite.core.alert_management_system import AlertStatus
from utils.resource_path import resource_path


logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Visual system: dark, sharp, solid, trading-desk style.
# -----------------------------------------------------------------------------

_BG_APP = "#050709"
_BG_PANEL = "#0a0d12"
_BG_PANEL_ALT = "#0f1318"
_BG_HEADER = "#070a0f"
_BG_ROW = "#0a0d12"
_BG_ROW_ALT = "#0f1318"
_BG_HOVER = "#141920"
_BG_SELECTED = "#1a2840"
_BORDER_DARK = "#1a2030"
_BORDER_LIGHT = "#243040"

_TEXT_STRONG = "#cbd7e3"
_SYMBOL_TEXT = "#b3c0ce"
_TEXT = "#98aabd"
_TEXT_MUTED = "#62758a"
_TEXT_FAINT = "#35465a"

_ACCENT = "#c89542"
_GREEN = "#58bfa6"
_RED = "#d86d7d"
_BLUE = "#6f8fc8"
_CYAN = "#69bdd2"

_MONO_FAMILY = "Consolas"  # reserved for raw logs, IDs, code/debug text only
_SANS = "'Inter', 'Segoe UI Variable', 'Segoe UI', 'Noto Sans', -apple-system, BlinkMacSystemFont, sans-serif"
_NUM = "'Inter', 'Segoe UI Variable', 'Segoe UI', 'Noto Sans', sans-serif"
_NUM_FONT = "Inter"
_UI_FONT = "Inter"

_ROW_H = 28
_DEFAULT_W = 860
_DEFAULT_H = 520
_MIN_W = 620
_MIN_H = 360
_REFRESH_MS = 3_000


# -----------------------------------------------------------------------------
# Small UI helpers
# -----------------------------------------------------------------------------

class _ResizeGrip(QWidget):
    """Small bottom-right resize handle for the frameless dialog."""

    SIZE = 14

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(QCursor(Qt.CursorShape.SizeFDiagCursor))
        self._dragging = False
        self._origin = QPoint()
        self._geometry = QRect()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(QColor(_TEXT_FAINT))
        pen.setWidth(1)
        painter.setPen(pen)
        n = self.SIZE
        for offset in (4, 8, 12):
            painter.drawLine(n - offset, n - 1, n - 1, n - offset)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._origin = event.globalPosition().toPoint()
            self._geometry = self.window().geometry()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if self._dragging:
            delta = event.globalPosition().toPoint() - self._origin
            self.window().setGeometry(
                self._geometry.x(),
                self._geometry.y(),
                max(_MIN_W, self._geometry.width() + delta.x()),
                max(_MIN_H, self._geometry.height() + delta.y()),
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        self._dragging = False
        super().mouseReleaseEvent(event)


class _MetricChip(QFrame):
    """Compact count chip used in the header summary strip."""

    def __init__(self, label: str, accent: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("metricChip")
        self._accent = accent

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(8)

        self._label = QLabel(label.upper())
        self._label.setObjectName("metricLabel")
        self._value = QLabel("0")
        self._value.setObjectName("metricValue")
        self._value.setStyleSheet(f"color: {accent};")

        layout.addWidget(self._label)
        layout.addWidget(self._value)

    def set_value(self, value: int) -> None:
        self._value.setText(str(value))


class _ActionCell(QWidget):
    """Transparent container for compact row action buttons."""

    def __init__(self, *buttons: QToolButton, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(_ROW_H)
        layout = QHBoxLayout(self)
        # Keep total button + margin height below the table row height.
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(5)
        layout.addStretch(1)
        for button in buttons:
            layout.addWidget(button)
        layout.addStretch(1)
        self.setStyleSheet("background: transparent;")


_ICON_CANDIDATES = {
    "ack": ("tick.svg", "check.svg", "done.svg"),
    "delete": ("delete.svg", "trash.svg", "remove.svg"),
}


def _icon_for_action(icon_key: str) -> QIcon:
    """Load row action icons from assets/icons with small filename fallbacks."""

    for filename in _ICON_CANDIDATES.get(icon_key, (f"{icon_key}.svg",)):
        path = resource_path(f"assets/icons/{filename}")
        if os.path.exists(path):
            return QIcon(path)
    # Fall back to the first expected path so packaged apps with virtual paths still work.
    filename = _ICON_CANDIDATES.get(icon_key, (f"{icon_key}.svg",))[0]
    return QIcon(resource_path(f"assets/icons/{filename}"))


def _action_button(
    text: str,
    color: str,
    tooltip: str,
    callback: Callable[[], None],
    icon_key: Optional[str] = None,
) -> QToolButton:
    """Create a crisp icon-first row action button."""

    button = QToolButton()
    button.setToolTip(tooltip)
    button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    button.setFixedSize(24, 22)
    button.setIconSize(QSize(12, 12))
    button.setText("")
    button.setAutoRaise(False)
    if icon_key:
        button.setIcon(_icon_for_action(icon_key))
        button.setAccessibleName(text)
    else:
        button.setText(text)

    button.setStyleSheet(f"""
        QToolButton {{
            background: {color}10;
            color: {color};
            border: 1px solid {color}3d;
            border-radius: 2px;
            padding: 0px;
            margin: 0px;
            font-family: {_SANS};
            font-size: 8px;
            font-weight: 600;
        }}
        QToolButton:hover {{
            background: {color}1e;
            border-color: {color}aa;
            color: {_TEXT_STRONG};
        }}
        QToolButton:pressed {{
            background: {color}30;
        }}
    """)
    button.clicked.connect(callback)
    return button


# -----------------------------------------------------------------------------
# Dialog
# -----------------------------------------------------------------------------

class AlertManagementDialog(QDialog):
    """
    Floating alert management panel.

    Kept backend-compatible with the existing manager contract:
        - manager.store.all()
        - manager.remove_alert(alert_id)
        - manager.acknowledge_triggered_alert(alert_id)
        - parent.candlestick_chart.on_search(symbol)
        - parent.config_manager load/save dialog state
    """

    _STATE_KEY = "compact_alert_mgmt_dialog"

    def __init__(self, manager: "AlertSystemManager", parent: Optional[QWidget] = None) -> None:
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(parent, flags)
        self.setObjectName("alertManagementDialog")
        self.setWindowTitle("Alert Manager")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        self.manager = manager
        self.store = manager.store

        self._pinned = True
        self._drag_active = False
        self._drag_offset = QPoint()
        self._geometry_restored = False
        self._prev_spacebar_enabled: Optional[bool] = None
        self._prev_shift_spacebar_enabled: Optional[bool] = None

        self._last_snapshot: Optional[Tuple[Tuple, Tuple, Tuple]] = None
        self._table_snapshots: Dict[str, Dict[str, Tuple]] = {
            "active": {},
            "triggered": {},
            "history": {},
        }

        self._build_ui()
        self._apply_styles()
        self._restore_geometry()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_tables)
        self._refresh_timer.start(_REFRESH_MS)

        self.refresh_tables(force=True)
        self._wire_symbol_navigation()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._shell = QFrame()
        self._shell.setObjectName("alertShell")
        root.addWidget(self._shell)

        shell = QVBoxLayout(self._shell)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        shell.addWidget(self._build_title_bar())
        shell.addWidget(self._build_summary_bar())

        body = QWidget()
        body.setObjectName("alertBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(10, 8, 10, 8)
        body_layout.setSpacing(8)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("alertTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(False)
        self.tabs.setUsesScrollButtons(False)

        self.active_table = self._make_table(["Symbol", "Condition", "Target", "Intent", "Note", ""])
        self.triggered_table = self._make_table(["Symbol", "Condition", "Target", "Triggered", "Intent", ""])
        self.history_table = self._make_table(["Symbol", "Condition", "Target", "Triggered", "Status"])

        self.tabs.addTab(self.active_table, "ACTIVE")
        self.tabs.addTab(self.triggered_table, "TRIGGERED")
        self.tabs.addTab(self.history_table, "HISTORY")

        body_layout.addWidget(self.tabs)
        shell.addWidget(body, 1)
        shell.addWidget(self._build_footer())

        self._grip = _ResizeGrip(self)

    def _build_title_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("alertTitleBar")
        bar.setFixedHeight(30)
        bar.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 0, 8, 0)
        layout.setSpacing(10)

        title = QLabel("ALERT MANAGER")
        title.setObjectName("dialogTitle")

        layout.addWidget(title)
        layout.addStretch()

        self._refresh_btn = self._title_button("↻", "Refresh")
        self._refresh_btn.clicked.connect(lambda: self.refresh_tables(force=True))

        self._pin_btn = self._title_button("PIN", "Toggle always-on-top")
        self._pin_btn.setCheckable(True)
        self._pin_btn.setChecked(True)
        self._pin_btn.toggled.connect(self._toggle_pin)

        close_btn = self._title_button("✕", "Close", close=True)
        close_btn.clicked.connect(self.close)

        layout.addWidget(self._refresh_btn)
        layout.addWidget(self._pin_btn)
        layout.addWidget(close_btn)

        bar.mousePressEvent = self._tb_press
        bar.mouseMoveEvent = self._tb_move
        bar.mouseReleaseEvent = self._tb_release
        return bar

    def _build_summary_bar(self) -> QFrame:
        strip = QFrame()
        strip.setObjectName("summaryStrip")
        strip.setFixedHeight(34)

        layout = QHBoxLayout(strip)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        self._active_chip = _MetricChip("Active", _GREEN)
        self._triggered_chip = _MetricChip("Triggered", _ACCENT)
        self._history_chip = _MetricChip("History", _BLUE)

        layout.addWidget(self._active_chip)
        layout.addWidget(self._triggered_chip)
        layout.addWidget(self._history_chip)
        layout.addStretch()

        self._market_hint = QLabel("auto refresh 3s")
        self._market_hint.setObjectName("marketHint")
        layout.addWidget(self._market_hint)
        return strip

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("alertFooter")
        footer.setFixedHeight(26)

        layout = QHBoxLayout(footer)
        layout.setContentsMargins(10, 0, 18, 0)
        layout.setSpacing(10)

        self._status_lbl = QLabel("Ready")
        self._status_lbl.setObjectName("alertStatusLbl")
        self.status_label = self._status_lbl  # backwards-compatible external access

        hint = QLabel("Space/↓ next · ↑ previous · click row to open chart")
        hint.setObjectName("alertHintLbl")

        layout.addWidget(self._status_lbl)
        layout.addStretch()
        layout.addWidget(hint)
        return footer

    def _title_button(self, text: str, tooltip: str, close: bool = False) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tooltip)
        button.setObjectName("alertCloseBtn" if close else "alertTitleBtn")
        button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        button.setFixedSize(32 if text == "PIN" else 26, 22)
        return button

    def _make_table(self, headers: List[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setObjectName("alertTable")
        table.setMouseTracking(True)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setSortingEnabled(False)
        table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        vertical = table.verticalHeader()
        vertical.setVisible(False)
        vertical.setDefaultSectionSize(_ROW_H)
        vertical.setMinimumSectionSize(_ROW_H)

        header = table.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setMinimumHeight(23)
        header.setStretchLastSection(False)

        for index, name in enumerate(headers):
            if name == "":
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.Fixed)
                table.setColumnWidth(index, 82 if "Triggered" in headers else 50)
            elif name == "Symbol":
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.Fixed)
                table.setColumnWidth(index, 118)
            elif name == "Condition":
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
                table.setColumnWidth(index, 230)
            elif name == "Note":
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
                table.setColumnWidth(index, 210)
            elif name in ("Target", "Triggered", "Intent", "Status"):
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)

        return table

    # ------------------------------------------------------------------
    # Window behaviour
    # ------------------------------------------------------------------

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

    def _toggle_pin(self, pinned: bool) -> None:
        self._pinned = pinned
        flags = self.windowFlags()
        if pinned:
            flags |= Qt.WindowType.WindowStaysOnTopHint
            self._pin_btn.setText("PIN")
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
            self._pin_btn.setText("TOP")
        self.setWindowFlags(flags)
        self.show()

    def _restore_geometry(self) -> None:
        cfg = getattr(self.parent(), "config_manager", None)
        if not cfg:
            return
        try:
            raw = cfg.load_dialog_state(self._STATE_KEY)
            if raw:
                data = json.loads(raw)
                self.resize(int(data.get("w", _DEFAULT_W)), int(data.get("h", _DEFAULT_H)))
                if "x" in data and "y" in data:
                    self.move(int(data["x"]), int(data["y"]))
                    self._geometry_restored = True
        except Exception as exc:  # pragma: no cover - defensive UI persistence
            logger.debug("Alert dialog geometry restore failed: %s", exc)

    def _save_geometry(self) -> None:
        cfg = getattr(self.parent(), "config_manager", None)
        if not cfg:
            return
        try:
            cfg.save_dialog_state(
                self._STATE_KEY,
                json.dumps({"x": self.x(), "y": self.y(), "w": self.width(), "h": self.height()}),
            )
        except Exception as exc:  # pragma: no cover - defensive UI persistence
            logger.debug("Alert dialog geometry save failed: %s", exc)

    def _center_on_parent(self) -> None:
        screen_obj = QApplication.primaryScreen()
        screen = screen_obj.availableGeometry() if screen_obj else QRect(0, 0, 1280, 720)
        parent = self.parent()
        if parent:
            pg = parent.frameGeometry()
            x = pg.right() - self.width() - 18
            y = pg.top() + 64
        else:
            x = screen.right() - self.width() - 24
            y = screen.top() + 64
        x = max(screen.left(), min(x, screen.right() - self.width()))
        y = max(screen.top(), min(y, screen.bottom() - self.height()))
        self.move(x, y)

    def _set_parent_spacebar_shortcuts_enabled(self, enabled: bool) -> None:
        """Backward-compatible alias used by the older dialog implementation."""
        self._set_parent_shortcuts_enabled(enabled)

    def _set_parent_shortcuts_enabled(self, enabled: bool) -> None:
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
                previous = getattr(self, state_attr)
                if previous is not None:
                    shortcut.setEnabled(previous)
                    setattr(self, state_attr, None)
            else:
                if getattr(self, state_attr) is None:
                    setattr(self, state_attr, shortcut.isEnabled())
                shortcut.setEnabled(False)

    # ------------------------------------------------------------------
    # Data refresh and row rendering
    # ------------------------------------------------------------------

    def refresh_tables(self, force: bool = False) -> None:
        if force:
            self._last_snapshot = None
            self._table_snapshots = {"active": {}, "triggered": {}, "history": {}}

        try:
            all_alerts = list(self.store.all())
        except Exception as exc:
            logger.exception("Failed to load alerts")
            self._status_lbl.setText(f"Alert store error: {exc}")
            return

        active = [a for a in all_alerts if self._alert_status(a) == AlertStatus.ACTIVE.value]
        triggered = [a for a in all_alerts if self._alert_status(a) == AlertStatus.TRIGGERED.value]
        history = [
            a for a in all_alerts
            if self._alert_status(a) in (AlertStatus.TRIGGERED.value, AlertStatus.EXPIRED.value)
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

        self.tabs.setTabText(0, f"ACTIVE  {len(active)}")
        self.tabs.setTabText(1, f"TRIGGERED  {len(triggered)}")
        self.tabs.setTabText(2, f"HISTORY  {len(history)}")

        self._active_chip.set_value(len(active))
        self._triggered_chip.set_value(len(triggered))
        self._history_chip.set_value(len(history))

        if active or triggered or history:
            self._status_lbl.setText(
                f"Active {len(active)}  ·  Triggered {len(triggered)}  ·  Total {len(all_alerts)}"
            )
        else:
            self._status_lbl.setText("No alerts available")

        self._last_snapshot = snapshot

    def _clear_cell_widgets(self, table: QTableWidget) -> None:
        for row in range(table.rowCount()):
            for column in range(table.columnCount()):
                widget = table.cellWidget(row, column)
                if widget is not None:
                    table.removeCellWidget(row, column)
                    widget.deleteLater()

    def _update_table_incremental(
        self,
        table_key: str,
        table: QTableWidget,
        alerts: List[object],
        row_updater: Callable[[QTableWidget, int, object], None],
    ) -> None:
        selected_id = self._selected_alert_id(table)
        current_scroll = table.verticalScrollBar().value()

        new_ids = [str(getattr(alert, "id", "")) for alert in alerts]
        old_ids = [
            table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            for row in range(table.rowCount())
            if table.item(row, 0) is not None
        ]

        table.setUpdatesEnabled(False)
        try:
            if old_ids != new_ids:
                self._clear_cell_widgets(table)
                table.clearContents()
                table.setRowCount(len(alerts))
                for row, alert in enumerate(alerts):
                    row_updater(table, row, alert)
            else:
                cached = self._table_snapshots.get(table_key, {})
                for row, alert in enumerate(alerts):
                    fresh = self._snapshot_alert(alert)
                    alert_id = str(getattr(alert, "id", ""))
                    if cached.get(alert_id) != fresh:
                        row_updater(table, row, alert)

            self._table_snapshots[table_key] = {
                str(getattr(a, "id", "")): self._snapshot_alert(a) for a in alerts
            }
            self._restore_selection(table, selected_id)
            table.verticalScrollBar().setValue(current_scroll)
        finally:
            table.setUpdatesEnabled(True)

    def _apply_active_row(self, table: QTableWidget, row: int, alert: object) -> None:
        symbol_item = self._cell(self._symbol(alert), color=_SYMBOL_TEXT, bold=True)
        symbol_item.setData(Qt.ItemDataRole.UserRole, str(getattr(alert, "id", "")))
        table.setItem(row, 0, symbol_item)
        table.setItem(row, 1, self._cell(self._condition(alert), color=_TEXT))
        table.setItem(row, 2, self._cell(self._fmt_target(alert), Qt.AlignmentFlag.AlignRight, _ACCENT, mono=True))
        table.setItem(row, 3, self._cell(self._intent(alert), color=_BLUE))
        table.setItem(row, 4, self._cell(self._note(alert), color=_TEXT_MUTED))

        delete_button = _action_button(
            "Delete",
            _RED,
            f"Delete alert for {self._symbol(alert)}",
            lambda aid=str(getattr(alert, "id", "")): self._delete_alert(aid),
            icon_key="delete",
        )
        table.setCellWidget(row, 5, _ActionCell(delete_button))

    def _apply_triggered_row(self, table: QTableWidget, row: int, alert: object) -> None:
        symbol_item = self._cell(self._symbol(alert), color=_SYMBOL_TEXT, bold=True)
        symbol_item.setData(Qt.ItemDataRole.UserRole, str(getattr(alert, "id", "")))
        table.setItem(row, 0, symbol_item)
        table.setItem(row, 1, self._cell(self._condition(alert), color=_TEXT))
        table.setItem(row, 2, self._cell(self._fmt_target(alert), Qt.AlignmentFlag.AlignRight, _ACCENT, mono=True))
        table.setItem(row, 3, self._cell(self._fmt_dt(getattr(alert, "triggered_at", None)), color=_TEXT_MUTED, mono=True))
        table.setItem(row, 4, self._cell(self._intent(alert), color=_BLUE))

        ack_button = _action_button(
            "ACK",
            _GREEN,
            "Acknowledge and move to history",
            lambda aid=str(getattr(alert, "id", "")): self._ack_alert(aid),
        )
        delete_button = _action_button(
            "Delete",
            _RED,
            f"Delete alert for {self._symbol(alert)}",
            lambda aid=str(getattr(alert, "id", "")): self._delete_alert(aid),
            icon_key="delete",
        )
        table.setCellWidget(row, 5, _ActionCell(ack_button, delete_button))

    def _apply_history_row(self, table: QTableWidget, row: int, alert: object) -> None:
        status = self._alert_status(alert)
        status_color = _GREEN if status == AlertStatus.TRIGGERED.value else _TEXT_FAINT

        symbol_item = self._cell(self._symbol(alert), color=_SYMBOL_TEXT)
        symbol_item.setData(Qt.ItemDataRole.UserRole, str(getattr(alert, "id", "")))
        table.setItem(row, 0, symbol_item)
        table.setItem(row, 1, self._cell(self._condition(alert), color=_TEXT_MUTED))
        table.setItem(row, 2, self._cell(self._fmt_target(alert), Qt.AlignmentFlag.AlignRight, _TEXT_MUTED, mono=True))
        table.setItem(row, 3, self._cell(self._fmt_dt(getattr(alert, "triggered_at", None)), color=_TEXT_FAINT, mono=True))
        table.setItem(row, 4, self._cell(status.upper() if status else "—", color=status_color, bold=True))

    # Backward-compatible population helpers kept for external callers/tests.
    def _populate_active(self, alerts: Iterable[object]) -> None:
        alerts = list(alerts)
        self.active_table.clearContents()
        self.active_table.setRowCount(len(alerts))
        for row, alert in enumerate(alerts):
            self._apply_active_row(self.active_table, row, alert)

    def _populate_triggered(self, alerts: Iterable[object]) -> None:
        alerts = list(alerts)
        self.triggered_table.clearContents()
        self.triggered_table.setRowCount(len(alerts))
        for row, alert in enumerate(alerts):
            self._apply_triggered_row(self.triggered_table, row, alert)

    def _populate_history(self, alerts: Iterable[object]) -> None:
        alerts = list(alerts)
        self.history_table.clearContents()
        self.history_table.setRowCount(len(alerts))
        for row, alert in enumerate(alerts):
            self._apply_history_row(self.history_table, row, alert)

    # ------------------------------------------------------------------
    # Row and value formatting
    # ------------------------------------------------------------------

    def _cell(
        self,
        text: str,
        align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft,
        color: str = _TEXT_STRONG,
        mono: bool = False,
        bold: bool = False,
    ) -> QTableWidgetItem:
        item = QTableWidgetItem(str(text))
        item.setForeground(QBrush(QColor(color)))
        item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)

        # Latest consistency rule: market numbers, timestamps, prices and UI text
        # use modern UI typography. Monospace is reserved only for raw logs/code/IDs.
        font = QFont(_NUM_FONT if mono else _UI_FONT, 9)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        font.setWeight(QFont.Weight.Medium if bold else QFont.Weight.Normal)
        font.setKerning(True)
        item.setFont(font)
        return item

    @staticmethod
    def _fmt_indian_datetime(dt_text: Optional[str]) -> str:
        """Backward-compatible datetime formatter alias used by previous code."""
        return AlertManagementDialog._fmt_dt(dt_text)

    @staticmethod
    def _fmt_dt(dt_text: Optional[str]) -> str:
        if not dt_text:
            return "—"
        try:
            dt = datetime.fromisoformat(str(dt_text).replace("Z", "+00:00"))
            return dt.strftime("%d-%b %H:%M")
        except Exception:
            return str(dt_text)[:16]

    @staticmethod
    def _snapshot_alert(alert: object) -> Tuple:
        return (
            str(getattr(alert, "id", "")),
            AlertManagementDialog._alert_status(alert),
            AlertManagementDialog._symbol(alert),
            AlertManagementDialog._condition(alert),
            AlertManagementDialog._target_value(alert),
            AlertManagementDialog._intent(alert),
            AlertManagementDialog._note(alert),
            str(getattr(alert, "triggered_at", "") or ""),
        )

    @staticmethod
    def _alert_status(alert: object) -> str:
        status = getattr(alert, "status", "")
        return getattr(status, "value", status) or ""

    @staticmethod
    def _symbol(alert: object) -> str:
        return str(getattr(alert, "symbol", "") or "—").upper()

    @staticmethod
    def _condition(alert: object) -> str:
        condition = getattr(alert, "condition", "")
        condition = getattr(condition, "value", condition)
        return str(condition or "—")

    @staticmethod
    def _target_value(alert: object) -> float:
        try:
            return float(getattr(alert, "target_value", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _intent(alert: object) -> str:
        intent = getattr(alert, "intent", "")
        intent = getattr(intent, "value", intent)
        return str(intent or "—")

    @staticmethod
    def _note(alert: object) -> str:
        note = str(getattr(alert, "note", "") or "")
        return note if len(note) <= 70 else f"{note[:67]}…"

    def _fmt_target(self, alert: object) -> str:
        value = self._target_value(alert)
        condition = self._condition(alert).lower()
        if "percent" in condition or "%" in condition:
            return f"{value:.2f}%"
        if "volume" in condition or "multiplier" in condition:
            return f"{value:.2f}×"
        if "rsi" in condition:
            return f"{value:.2f}"
        if "time" in condition:
            raw = int(value) if value else 0
            return f"{raw:04d}" if raw else "—"
        return f"₹{value:,.2f}"

    # ------------------------------------------------------------------
    # Navigation and actions
    # ------------------------------------------------------------------

    def _wire_symbol_navigation(self) -> None:
        for table in (self.active_table, self.triggered_table, self.history_table):
            table.cellClicked.connect(lambda row, _col, tbl=table: self._open_symbol_from_row(tbl, row))

    def _selected_alert_id(self, table: QTableWidget) -> Optional[str]:
        row = table.currentRow()
        if row < 0:
            return None
        item = table.item(row, 0)
        if item is None:
            return None
        alert_id = item.data(Qt.ItemDataRole.UserRole)
        return str(alert_id) if alert_id else None

    def _restore_selection(self, table: QTableWidget, alert_id: Optional[str]) -> None:
        if not alert_id:
            return
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == alert_id:
                table.selectRow(row)
                table.setCurrentCell(row, 0)
                return

    def _open_selected_symbol_in_chart(self, table: QTableWidget) -> None:
        row = table.currentRow()
        if row >= 0:
            self._open_symbol_from_row(table, row)

    def _open_symbol_from_row(self, table: QTableWidget, row: int) -> None:
        if row < 0:
            return
        item = table.item(row, 0)
        if item is None:
            return
        symbol = (item.text() or "").strip().upper()
        if not symbol or symbol == "—":
            return
        chart = getattr(self.parent(), "candlestick_chart", None)
        if chart and hasattr(chart, "on_search"):
            chart.on_search(symbol)

    def _delete_alert(self, alert_id: str) -> None:
        if not alert_id:
            return
        try:
            self.manager.remove_alert(alert_id)
            self.refresh_tables(force=True)
        except Exception as exc:
            logger.exception("Failed to delete alert %s", alert_id)
            self._status_lbl.setText(f"Delete failed: {exc}")

    def _ack_alert(self, alert_id: str) -> None:
        if not alert_id:
            return
        try:
            self.manager.acknowledge_triggered_alert(alert_id)
            self.refresh_tables(force=True)
        except Exception as exc:
            logger.exception("Failed to acknowledge alert %s", alert_id)
            self._status_lbl.setText(f"Acknowledge failed: {exc}")

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
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

    # ------------------------------------------------------------------
    # Qt events
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._set_parent_shortcuts_enabled(False)
        super().showEvent(event)
        if not self._geometry_restored:
            self._center_on_parent()
            self._geometry_restored = True

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._save_geometry()
        self._set_parent_shortcuts_enabled(True)
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if hasattr(self, "_grip"):
            self._grip.move(self.width() - _ResizeGrip.SIZE, self.height() - _ResizeGrip.SIZE)

    # ------------------------------------------------------------------
    # Stylesheet
    # ------------------------------------------------------------------

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
        QDialog#alertManagementDialog {{
            background: {_BG_APP};
            color: {_TEXT};
            font-family: {_SANS};
        }}

        QFrame#alertShell {{
            background: {_BG_PANEL};
            border: 1px solid {_BORDER_DARK};
            border-radius: 2px;
        }}

        QFrame#alertTitleBar {{
            background: {_BG_HEADER};
            border-bottom: 1px solid {_BORDER_DARK};
        }}

        QLabel#dialogTitle {{
            color: {_ACCENT};
            font-family: {_SANS};
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.7px;
            background: transparent;
        }}

        QLabel#dialogSubtitle {{
            color: {_TEXT_MUTED};
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 500;
            letter-spacing: 0.3px;
            background: transparent;
        }}

        QToolButton#alertTitleBtn {{
            background: {_BG_PANEL_ALT};
            color: {_TEXT_MUTED};
            border: 1px solid {_BORDER_DARK};
            border-radius: 2px;
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 600;
        }}

        QToolButton#alertTitleBtn:hover {{
            background: {_BG_HOVER};
            color: {_TEXT_STRONG};
            border-color: {_BORDER_LIGHT};
        }}

        QToolButton#alertTitleBtn:checked {{
            color: {_CYAN};
            border-color: rgba(105,189,210,0.32);
            background: rgba(105,189,210,0.08);
        }}

        QToolButton#alertCloseBtn {{
            background: transparent;
            color: {_TEXT_MUTED};
            border: 1px solid transparent;
            border-radius: 2px;
            font-family: {_SANS};
            font-size: 11px;
            font-weight: 600;
        }}

        QToolButton#alertCloseBtn:hover {{
            background: rgba(216,109,125,0.12);
            color: {_RED};
            border-color: rgba(216,109,125,0.32);
        }}

        QFrame#summaryStrip {{
            background: {_BG_PANEL};
            border-bottom: 1px solid {_BORDER_DARK};
        }}

        QFrame#metricChip {{
            background: {_BG_PANEL_ALT};
            border: 1px solid {_BORDER_DARK};
            border-radius: 2px;
        }}

        QLabel#metricLabel {{
            color: {_TEXT_MUTED};
            font-family: {_SANS};
            font-size: 8px;
            font-weight: 600;
            letter-spacing: 0.9px;
            background: transparent;
        }}

        QLabel#metricValue {{
            font-family: {_NUM};
            font-size: 12px;
            font-weight: 600;
            background: transparent;
        }}

        QLabel#marketHint {{
            color: {_TEXT_FAINT};
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 500;
            background: transparent;
        }}

        QWidget#alertBody {{
            background: {_BG_PANEL};
        }}

        QTabWidget#alertTabs {{
            background: {_BG_PANEL};
            border: none;
        }}

        QTabWidget#alertTabs::pane {{
            background: {_BG_PANEL};
            border: 1px solid {_BORDER_DARK};
            top: -1px;
        }}

        QTabBar::tab {{
            background: {_BG_HEADER};
            color: {_TEXT_MUTED};
            border: 1px solid {_BORDER_DARK};
            border-bottom: none;
            min-height: 23px;
            padding: 0px 14px;
            margin-right: 2px;
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 600;
            letter-spacing: 0.8px;
        }}

        QTabBar::tab:selected {{
            background: {_BG_PANEL_ALT};
            color: {_TEXT_STRONG};
            border-top: 2px solid {_ACCENT};
            padding-top: -1px;
        }}

        QTabBar::tab:hover:!selected {{
            color: {_TEXT};
            background: {_BG_HOVER};
        }}

        QTableWidget#alertTable {{
            background: {_BG_ROW};
            alternate-background-color: {_BG_ROW_ALT};
            color: {_TEXT};
            border: none;
            gridline-color: transparent;
            outline: none;
            selection-background-color: {_BG_SELECTED};
            selection-color: {_TEXT_STRONG};
            font-family: {_SANS};
            font-size: 11px;
            show-decoration-selected: 0;
        }}

        QTableWidget#alertTable::item {{
            padding-left: 6px;
            padding-right: 6px;
            border-bottom: 1px solid {_BG_HOVER};
            background: transparent;
        }}

        QTableWidget#alertTable::item:selected {{
            background: {_BG_SELECTED};
            color: {_TEXT_STRONG};
        }}

        QTableWidget#alertTable::item:hover {{
            background: {_BG_HOVER};
        }}

        QHeaderView::section {{
            background: {_BG_PANEL_ALT};
            color: {_TEXT_MUTED};
            border: none;
            border-bottom: 1px solid {_BORDER_DARK};
            padding-left: 6px;
            padding-right: 6px;
            min-height: 23px;
            max-height: 23px;
            font-family: {_SANS};
            font-size: 8px;
            font-weight: 600;
            letter-spacing: 1px;
        }}

        QHeaderView::section:hover {{
            background: {_BG_HOVER};
            color: {_TEXT};
        }}

        QHeaderView {{
            background: {_BG_PANEL_ALT};
            border: none;
        }}

        QFrame#alertFooter {{
            background: {_BG_HEADER};
            border-top: 1px solid {_BORDER_DARK};
        }}

        QLabel#alertStatusLbl {{
            color: {_TEXT_MUTED};
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 600;
            background: transparent;
        }}

        QLabel#alertHintLbl {{
            color: {_TEXT_FAINT};
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 500;
            background: transparent;
        }}

        QScrollBar:vertical {{
            background: transparent;
            width: 4px;
            border: none;
            margin: 0;
        }}

        QScrollBar::handle:vertical {{
            background: {_BORDER_LIGHT};
            min-height: 18px;
            border-radius: 2px;
        }}

        QScrollBar::handle:vertical:hover {{
            background: {_TEXT_MUTED};
        }}

        QScrollBar:horizontal {{
            background: transparent;
            height: 4px;
            border: none;
            margin: 0;
        }}

        QScrollBar::handle:horizontal {{
            background: {_BORDER_LIGHT};
            min-width: 18px;
            border-radius: 2px;
        }}

        QScrollBar::handle:horizontal:hover {{
            background: {_TEXT_MUTED};
        }}

        QScrollBar::add-line,
        QScrollBar::sub-line {{
            border: none;
            background: none;
            width: 0;
            height: 0;
            margin: 0;
        }}
        """)