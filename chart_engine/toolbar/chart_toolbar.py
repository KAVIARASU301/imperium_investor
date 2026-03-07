# chart_engine/toolbar/chart_toolbar.py
#
# The horizontal toolbar that lives above the chart canvas.
# Contains: symbol info label, compact timeframe selector, drawing tool strip,
#           measure/color/clear/refresh/settings/order controls.

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
)


class TimeframeComboBox(QComboBox):
    """
    QComboBox that reliably opens its popup on mouse release, preventing the
    popup from immediately closing on certain platform / style combinations.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._open_on_release = False

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.isEnabled():
            self._open_on_release = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._open_on_release:
            self._open_on_release = False
            if self.rect().contains(event.position().toPoint()):
                self.showPopup()
            event.accept()
            return
        self._open_on_release = False
        super().mouseReleaseEvent(event)


DRAWING_TOOLS: List[Tuple[str, str, str]] = [
    ("line", "╱", "Trend Line"),
    ("horizontal_line", "─", "Horizontal Line"),
    ("horizontal_ray", "→", "Horizontal Ray"),
    ("arrow_line", "➤", "Arrow Line"),
    ("rectangle", "▭", "Rectangle"),
    ("fibonacci", "ϟ", "Fibonacci Retracement"),
    ("note", "T", "Text Note"),
]

TIMEFRAMES: List[Tuple[str, str]] = [
    ("1m", "minute"),
    ("3m", "3minute"),
    ("5m", "5minute"),
    ("15m", "15minute"),
    ("30m", "30minute"),
    ("1h", "60minute"),
    ("D", "day"),
    ("W", "week"),
    ("M", "month"),
]


class ChartToolbar(QFrame):
    """Self-contained compact toolbar QFrame."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chartToolbar")
        self.setMaximumHeight(34)

        self._drawing_action_group: Optional[QActionGroup] = None
        self._drawing_actions: Dict[str, QAction] = {}
        self._tool_button_group: Optional[QButtonGroup] = None
        self._tool_buttons: Dict[str, QPushButton] = {}

        self._build()
        self._apply_styles()

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(2)

        self.symbol_label = QLabel("—")
        self.symbol_label.setObjectName("symbolFullNameLabel")
        layout.addWidget(self.symbol_label)
        layout.addSpacing(6)

        self.timeframe_combo = TimeframeComboBox()
        self.timeframe_combo.setObjectName("timeframeDropdown")
        self.timeframe_combo.setFixedHeight(22)
        self.timeframe_combo.setMinimumWidth(40)
        for display, data in TIMEFRAMES:
            self.timeframe_combo.addItem(display, data)
        self.timeframe_combo.setCurrentIndex(6)
        layout.addWidget(self.timeframe_combo)

        layout.addSpacing(2)

        self._tool_button_group = QButtonGroup(self)
        self._tool_button_group.setExclusive(True)

        draw_menu = QMenu(self)
        draw_menu.setObjectName("drawingMenu")
        self._drawing_action_group = QActionGroup(self)
        self._drawing_action_group.setExclusive(True)

        for tool_id, icon_text, label in DRAWING_TOOLS:
            action = QAction(label, self)
            action.setCheckable(True)
            self._drawing_action_group.addAction(action)
            self._drawing_actions[tool_id] = action
            draw_menu.addAction(action)

            btn = QPushButton(icon_text)
            btn.setObjectName("toolIconButton")
            btn.setCheckable(True)
            btn.setFixedSize(22, 22)
            btn.setToolTip(label)
            self._tool_button_group.addButton(btn)
            self._tool_buttons[tool_id] = btn
            layout.addWidget(btn)

        draw_menu.addSeparator()
        self._clear_action = QAction("Clear Selection", self)
        draw_menu.addAction(self._clear_action)

        self.measure_btn = QPushButton("⚖")
        self.measure_btn.setObjectName("chartToolButton")
        self.measure_btn.setFixedSize(22, 22)
        self.measure_btn.setCheckable(True)
        self.measure_btn.setToolTip("Measure (price/time)")
        layout.addWidget(self.measure_btn)

        self.color_btn = QPushButton("●")
        self.color_btn.setObjectName("chartToolButton")
        self.color_btn.setFixedSize(22, 22)
        self.color_btn.setToolTip("Drawing color")
        layout.addWidget(self.color_btn)

        self.clear_drawings_btn = QPushButton("✕")
        self.clear_drawings_btn.setObjectName("chartToolButton")
        self.clear_drawings_btn.setFixedSize(22, 22)
        self.clear_drawings_btn.setToolTip("Clear all drawings")
        layout.addWidget(self.clear_drawings_btn)

        layout.addStretch()

        self.refresh_btn = QPushButton("⟳")
        self.refresh_btn.setObjectName("refreshButton")
        self.refresh_btn.setFixedSize(22, 22)
        self.refresh_btn.setToolTip("Refresh Data (F5)")
        layout.addWidget(self.refresh_btn)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("chartToolButton")
        self.settings_btn.setFixedSize(22, 22)
        self.settings_btn.setToolTip("Chart Settings")
        layout.addWidget(self.settings_btn)

        self.order_btn = QPushButton("Order")
        self.order_btn.setObjectName("orderButton")
        self.order_btn.setFixedHeight(22)
        layout.addWidget(self.order_btn)

    def set_symbol_text(self, text: str) -> None:
        self.symbol_label.setText(text)

    def get_drawing_action(self, tool_id: str) -> Optional[QAction]:
        return self._drawing_actions.get(tool_id)

    def get_clear_action(self) -> QAction:
        return self._clear_action

    def get_all_drawing_actions(self):
        return list(self._drawing_actions.values())

    def get_drawing_action_group(self) -> Optional[QActionGroup]:
        return self._drawing_action_group

    def get_tool_button(self, tool_id: str) -> Optional[QPushButton]:
        return self._tool_buttons.get(tool_id)

    def set_draw_btn_active(self, tool_id: str) -> None:
        self.reset_draw_btn()
        btn = self._tool_buttons.get(tool_id)
        action = self._drawing_actions.get(tool_id)
        if btn:
            btn.setChecked(True)
        if action:
            action.setChecked(True)

    def reset_draw_btn(self) -> None:
        for btn in self._tool_buttons.values():
            btn.setChecked(False)
        grp = self._drawing_action_group
        if grp:
            grp.setExclusive(False)
            for action in grp.actions():
                action.setChecked(False)
            grp.setExclusive(True)

    def set_timeframe(self, interval: str) -> None:
        for i in range(self.timeframe_combo.count()):
            if self.timeframe_combo.itemData(i) == interval:
                self.timeframe_combo.setCurrentIndex(i)
                return

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            QFrame#chartToolbar {
                background-color: #111318;
                border-bottom: 1px solid #2a2e38;
            }

            #symbolFullNameLabel {
                color: #c8cfe0;
                font-size: 12px;
                font-weight: 700;
                padding-left: 4px;
            }

            QComboBox#timeframeDropdown {
                background-color: #0e1118;
                color: #d4d9e8;
                border: 1px solid #2a3040;
                padding: 0 6px;
                border-radius: 3px;
                font-size: 10px;
                font-weight: 700;
                min-width: 38px;
            }
            QComboBox#timeframeDropdown:hover {
                border-color: #3a7bd5;
                color: #a8c8ff;
            }
            QComboBox#timeframeDropdown::drop-down { border: none; width: 10px; }
            QComboBox QAbstractItemView {
                background-color: #1a1e28;
                color: #d4d9e8;
                border: 1px solid #3a3e50;
                selection-background-color: #1d4080;
            }

            QPushButton#chartToolButton,
            QPushButton#refreshButton,
            QPushButton#toolIconButton {
                background-color: #0e1118;
                color: #c0c8da;
                border: 1px solid #282e3a;
                border-radius: 3px;
                font-size: 10px;
                font-weight: 700;
                padding: 0;
            }
            QPushButton#chartToolButton:hover,
            QPushButton#refreshButton:hover,
            QPushButton#toolIconButton:hover {
                border-color: #3a7bd5;
                color: #a8c8ff;
            }
            QPushButton#chartToolButton:checked,
            QPushButton#toolIconButton:checked {
                background-color: #0d2d5c;
                border-color: #2060bb;
                color: #c0e0ff;
            }

            QPushButton#orderButton {
                background-color: #0d2f17;
                border: 1px solid #1a5930;
                color: #8dffc2;
                padding: 1px 9px;
                border-radius: 3px;
                font-size: 10px;
                font-weight: 700;
            }
            QPushButton#orderButton:hover {
                background-color: #133d1f;
                border-color: #22803e;
                color: #b0ffe0;
            }
        """)
