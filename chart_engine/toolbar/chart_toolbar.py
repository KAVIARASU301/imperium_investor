# chart_engine/toolbar/chart_toolbar.py
#
# The horizontal toolbar that lives above the chart canvas.
# Contains: symbol info label, timeframe selector, drawing tools menu,
#           color picker, auto-scale, save drawings, refresh, settings, order.
#
# Returns a QFrame ready to be inserted into any layout.
# All button-click logic is wired by the parent CandlestickChart widget —
# toolbar only builds and styles the widgets.

from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QWidget,
)


# ─── Reliable combo box (opens on release, not press) ─────────────────────────

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


# ─── Drawing tool definitions ─────────────────────────────────────────────────

DRAWING_TOOLS: List[Tuple[str, str]] = [
    ("line",            "Trend Line"),
    ("horizontal_line", "Horizontal Line"),
    ("horizontal_ray",  "Horizontal Ray"),
    ("arrow_line",      "Arrow"),
    ("rectangle",       "Rectangle"),
    ("fibonacci",       "Fibonacci Retracement"),
    ("note",            "Text Note"),
]

TOOL_DISPLAY: Dict[str, str] = {
    "line":             "Trend",
    "horizontal_line":  "H-Line",
    "horizontal_ray":   "H-Ray",
    "arrow_line":       "Arrow",
    "rectangle":        "Rect",
    "fibonacci":        "Fib",
    "note":             "Note",
}

TIMEFRAMES: List[Tuple[str, str]] = [
    ("1m",  "minute"),
    ("3m",  "3minute"),
    ("5m",  "5minute"),
    ("15m", "15minute"),
    ("30m", "30minute"),
    ("1h",  "60minute"),
    ("D",   "day"),
    ("W",   "week"),
    ("M",   "month"),
]


# ─── Toolbar builder ──────────────────────────────────────────────────────────

class ChartToolbar(QFrame):
    """
    Self-contained toolbar QFrame.
    After instantiation the parent should connect the callbacks:
        toolbar.on_timeframe_changed  = callable(interval_str)
        toolbar.on_drawing_tool       = callable(tool_id)
        toolbar.on_clear_tool         = callable()
        toolbar.on_color_picker       = callable()
        toolbar.on_auto_scale         = callable()
        toolbar.on_save_drawings      = callable()
        toolbar.on_refresh            = callable()
        toolbar.on_settings           = callable()
        toolbar.on_order              = callable()
    Or just wire them via .connect() on the individual button signals.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chartToolbar")
        self.setMaximumHeight(38)

        self._drawing_action_group: Optional[QActionGroup] = None
        self._drawing_actions: Dict[str, QAction] = {}

        self._build()
        self._apply_styles()

    # ─── Build ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(4)

        # ── Symbol / price label ──
        self.symbol_label = QLabel("—")
        self.symbol_label.setObjectName("symbolFullNameLabel")
        layout.addWidget(self.symbol_label)

        layout.addSpacing(8)

        # ── Timeframe selector ──
        self.timeframe_combo = TimeframeComboBox()
        self.timeframe_combo.setObjectName("timeframeDropdown")
        self.timeframe_combo.setFixedHeight(26)
        for display, data in TIMEFRAMES:
            self.timeframe_combo.addItem(display, data)
        # Default to Daily
        self.timeframe_combo.setCurrentIndex(6)
        layout.addWidget(self.timeframe_combo)

        layout.addSpacing(4)

        # ── Drawing tools dropdown ──
        self.draw_btn = QPushButton("Draw ▾")
        self.draw_btn.setObjectName("chartToolButton")
        self.draw_btn.setFixedHeight(26)
        self.draw_btn.setToolTip("Drawing Tools")
        self.draw_btn.setCheckable(False)

        draw_menu = QMenu(self)
        draw_menu.setObjectName("drawingMenu")
        self._drawing_action_group = QActionGroup(self)
        self._drawing_action_group.setExclusive(True)

        for tool_id, label in DRAWING_TOOLS:
            action = QAction(label, self)
            action.setCheckable(True)
            self._drawing_action_group.addAction(action)
            self._drawing_actions[tool_id] = action
            draw_menu.addAction(action)

        draw_menu.addSeparator()
        clear_action = QAction("Clear Selection", self)
        draw_menu.addAction(clear_action)
        self._clear_action = clear_action

        self.draw_btn.setMenu(draw_menu)
        layout.addWidget(self.draw_btn)

        # ── Measure tool ──
        self.measure_btn = QPushButton("⊢ Measure")
        self.measure_btn.setObjectName("chartToolButton")
        self.measure_btn.setFixedHeight(26)
        self.measure_btn.setCheckable(True)
        self.measure_btn.setToolTip("Measure tool — click and drag to measure price/time")
        layout.addWidget(self.measure_btn)

        # ── Color picker ──
        self.color_btn = QPushButton("● Color")
        self.color_btn.setObjectName("chartToolButton")
        self.color_btn.setFixedHeight(26)
        self.color_btn.setToolTip("Change drawing color")
        layout.addWidget(self.color_btn)

        # ── Auto-scale ──
        self.auto_scale_btn = QPushButton("⊡ Auto")
        self.auto_scale_btn.setObjectName("chartToolButton")
        self.auto_scale_btn.setFixedHeight(26)
        self.auto_scale_btn.setToolTip("Auto Scale (Ctrl+A)")
        layout.addWidget(self.auto_scale_btn)

        # ── Save drawings ──
        self.save_btn = QPushButton("💾")
        self.save_btn.setObjectName("chartToolButton")
        self.save_btn.setFixedHeight(26)
        self.save_btn.setFixedWidth(28)
        self.save_btn.setToolTip("Save Drawings (Ctrl+S)")
        layout.addWidget(self.save_btn)

        # ── Clear drawings ──
        self.clear_drawings_btn = QPushButton("✕ Clear")
        self.clear_drawings_btn.setObjectName("chartToolButton")
        self.clear_drawings_btn.setFixedHeight(26)
        self.clear_drawings_btn.setToolTip("Clear All Drawings")
        layout.addWidget(self.clear_drawings_btn)

        layout.addStretch()

        # ── Refresh ──
        self.refresh_btn = QPushButton("⟳")
        self.refresh_btn.setObjectName("refreshButton")
        self.refresh_btn.setFixedHeight(26)
        self.refresh_btn.setFixedWidth(28)
        self.refresh_btn.setToolTip("Refresh Data (F5)")
        layout.addWidget(self.refresh_btn)

        # ── Settings ──
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("chartToolButton")
        self.settings_btn.setFixedHeight(26)
        self.settings_btn.setFixedWidth(28)
        self.settings_btn.setToolTip("Chart Settings")
        layout.addWidget(self.settings_btn)

        # ── Order button ──
        self.order_btn = QPushButton("Order")
        self.order_btn.setObjectName("orderButton")
        self.order_btn.setFixedHeight(26)
        layout.addWidget(self.order_btn)

    # ─── Utility ──────────────────────────────────────────────────────────────

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

    def set_draw_btn_active(self, tool_id: str) -> None:
        display = TOOL_DISPLAY.get(tool_id, "Draw")
        self.draw_btn.setText(f"Draw: {display} ▾")
        self.draw_btn.setProperty("active", True)
        self.draw_btn.style().unpolish(self.draw_btn)
        self.draw_btn.style().polish(self.draw_btn)

    def reset_draw_btn(self) -> None:
        self.draw_btn.setText("Draw ▾")
        self.draw_btn.setProperty("active", False)
        self.draw_btn.style().unpolish(self.draw_btn)
        self.draw_btn.style().polish(self.draw_btn)
        # Uncheck all actions
        grp = self._drawing_action_group
        if grp:
            grp.setExclusive(False)
            for a in grp.actions():
                a.setChecked(False)
            grp.setExclusive(True)

    def set_timeframe(self, interval: str) -> None:
        for i in range(self.timeframe_combo.count()):
            if self.timeframe_combo.itemData(i) == interval:
                self.timeframe_combo.setCurrentIndex(i)
                return

    # ─── Styles ───────────────────────────────────────────────────────────────

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            QFrame#chartToolbar {
                background-color: #111318;
                border-bottom: 1px solid #2a2e38;
            }

            #symbolFullNameLabel {
                color: #c8cfe0;
                font-size: 13px;
                font-weight: 700;
                padding-left: 4px;
            }

            QComboBox#timeframeDropdown {
                background-color: #0e1118;
                color: #d4d9e8;
                border: 1px solid #2a3040;
                padding: 2px 8px;
                border-radius: 3px;
                font-size: 11px;
                font-weight: 700;
                min-width: 46px;
            }
            QComboBox#timeframeDropdown:hover {
                border-color: #3a7bd5;
                color: #a8c8ff;
            }
            QComboBox#timeframeDropdown::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #1a1e28;
                color: #d4d9e8;
                border: 1px solid #3a3e50;
                selection-background-color: #1d4080;
            }

            QPushButton#chartToolButton, QPushButton#refreshButton {
                background-color: #0e1118;
                color: #c0c8da;
                border: 1px solid #282e3a;
                padding: 2px 8px;
                border-radius: 3px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton#chartToolButton:hover, QPushButton#refreshButton:hover {
                border-color: #3a7bd5;
                color: #a8c8ff;
            }
            QPushButton#chartToolButton:checked,
            QPushButton#chartToolButton[active="true"] {
                background-color: #0d2d5c;
                border-color: #2060bb;
                color: #c0e0ff;
            }

            QPushButton#orderButton {
                background-color: #0d2f17;
                border: 1px solid #1a5930;
                color: #8dffc2;
                padding: 2px 10px;
                border-radius: 3px;
                font-size: 11px;
                font-weight: 700;
            }
            QPushButton#orderButton:hover {
                background-color: #133d1f;
                border-color: #22803e;
                color: #b0ffe0;
            }

            QMenu#drawingMenu {
                background-color: #151820;
                color: #c8cfe0;
                border: 1px solid #2e3440;
                border-radius: 4px;
                padding: 4px 0;
            }
            QMenu#drawingMenu::item { padding: 6px 16px; font-size: 12px; }
            QMenu#drawingMenu::item:selected {
                background-color: #1a3560;
                color: #d0e8ff;
            }
            QMenu#drawingMenu::item:checked {
                background-color: #1d4a7a;
                color: #ffffff;
            }
        """)
