# chart_engine/toolbar/chart_toolbar.py
#
# Compact TC2000-style toolbar for swing trading:
#   Symbol badge | Timeframe strip | ── | Indicator toggles | ── |
#   Drawing tools | Color · Clear   <stretch>  AutoScale · Refresh · Settings · Order

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
)


# ─── Metadata ────────────────────────────────────────────────────────────────

DRAWING_TOOLS: List[Tuple[str, str, str]] = [
    ("line",             "╱",  "Trend Line"),
    ("horizontal_line",  "─",  "Horizontal Line"),
    ("horizontal_ray",   "→",  "Horizontal Ray"),
    ("rectangle",        "▭",  "Rectangle"),
    ("fibonacci",        "⌇",  "Fibonacci Retracement"),
    ("arrow_line",       "↗",  "Arrow"),
    ("note",             "T",  "Text Note"),
]

TOOL_DISPLAY: Dict[str, str] = {tid: label for tid, _, label in DRAWING_TOOLS}

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

# (key, display_label, accent_color)
INDICATORS: List[Tuple[str, str, str]] = [
    ("ema10",  "E10",  "#2962ff"),
    ("ema20",  "E20",  "#9c27b0"),
    ("ema50",  "E50",  "#f06204"),
    ("ema200", "E200", "#e91e63"),
    ("vwap",   "VWAP", "#ff9e42"),
]


def _vsep() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.VLine)
    sep.setFixedWidth(1)
    sep.setFixedHeight(16)
    sep.setStyleSheet("background: #232b3a; border: none;")
    return sep


class ChartToolbar(QFrame):
    """Compact swing-trading toolbar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chartToolbar")
        self.setFixedHeight(32)

        self._drawing_action_group: Optional[QActionGroup] = None
        self._drawing_actions: Dict[str, QAction] = {}
        self._tool_button_group: Optional[QButtonGroup] = None
        self._tool_buttons: Dict[str, QPushButton] = {}
        self._tf_buttons: Dict[str, QPushButton] = {}
        self._tf_button_group: Optional[QButtonGroup] = None
        self.indicator_buttons: Dict[str, QPushButton] = {}
        self._drawing_color = "#FFD700"

        self._build()
        self._apply_styles()

    # ─────────────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(2)

        # 1 ── Symbol badge ──────────────────────────────────────────────────
        self.symbol_label = QLabel("—")
        self.symbol_label.setObjectName("symbolBadge")
        self.symbol_label.setFixedHeight(22)
        self.symbol_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.symbol_label)
        layout.addSpacing(4)
        layout.addWidget(_vsep())
        layout.addSpacing(2)

        # 2 ── Timeframe strip ───────────────────────────────────────────────
        self._tf_button_group = QButtonGroup(self)
        self._tf_button_group.setExclusive(True)
        for display, data in TIMEFRAMES:
            w = 30 if len(display) <= 2 else 36
            btn = QPushButton(display)
            btn.setObjectName("tfButton")
            btn.setCheckable(True)
            btn.setFixedSize(w, 22)
            btn.setToolTip(data)
            self._tf_buttons[data] = btn
            self._tf_button_group.addButton(btn)
            layout.addWidget(btn)
            if display == "D":
                btn.setChecked(True)

        layout.addSpacing(2)
        layout.addWidget(_vsep())
        layout.addSpacing(2)

        # 3 ── Indicator toggles ─────────────────────────────────────────────
        for key, label, color in INDICATORS:
            w = 30 if len(label) <= 3 else 40
            btn = QPushButton(label)
            btn.setObjectName(f"indBtn_{key}")
            btn.setCheckable(True)
            btn.setChecked(True)
            btn.setFixedSize(w, 22)
            btn.setToolTip(f"Toggle {label}")
            self.indicator_buttons[key] = btn
            layout.addWidget(btn)

        layout.addSpacing(2)
        layout.addWidget(_vsep())
        layout.addSpacing(2)

        # 4 ── Drawing tools ─────────────────────────────────────────────────
        self._tool_button_group = QButtonGroup(self)
        self._tool_button_group.setExclusive(True)

        draw_menu = QMenu(self)
        draw_menu.setObjectName("drawingMenu")
        self._drawing_action_group = QActionGroup(self)
        self._drawing_action_group.setExclusive(True)

        for tool_id, icon, label in DRAWING_TOOLS:
            action = QAction(label, self)
            action.setCheckable(True)
            self._drawing_action_group.addAction(action)
            self._drawing_actions[tool_id] = action
            draw_menu.addAction(action)

            btn = QPushButton(icon)
            btn.setObjectName("toolButton")
            btn.setCheckable(True)
            btn.setFixedSize(24, 22)
            btn.setToolTip(label)
            self._tool_button_group.addButton(btn)
            self._tool_buttons[tool_id] = btn
            layout.addWidget(btn)

        draw_menu.addSeparator()
        self._clear_action = QAction("Deselect Tool", self)
        draw_menu.addAction(self._clear_action)

        # Measure
        self.measure_btn = QPushButton("⤢")
        self.measure_btn.setObjectName("toolButton")
        self.measure_btn.setFixedSize(24, 22)
        self.measure_btn.setCheckable(True)
        self.measure_btn.setToolTip("Measure price/time range")
        layout.addWidget(self.measure_btn)

        layout.addSpacing(2)
        layout.addWidget(_vsep())
        layout.addSpacing(2)

        # 5 ── Style controls ────────────────────────────────────────────────
        self.color_btn = QPushButton("●")
        self.color_btn.setObjectName("colorPickerButton")
        self.color_btn.setFixedSize(24, 22)
        self.color_btn.setToolTip("Drawing color")
        self._refresh_color_btn()
        layout.addWidget(self.color_btn)

        self.clear_drawings_btn = QPushButton("✕")
        self.clear_drawings_btn.setObjectName("actionButton")
        self.clear_drawings_btn.setFixedSize(24, 22)
        self.clear_drawings_btn.setToolTip("Clear all drawings")
        layout.addWidget(self.clear_drawings_btn)

        # stretch
        layout.addStretch()

        # 6 ── Right cluster ──────────────────────────────────────────────────
        self.autoscale_btn = QPushButton("⊡")
        self.autoscale_btn.setObjectName("actionButton")
        self.autoscale_btn.setFixedSize(24, 22)
        self.autoscale_btn.setToolTip("Auto-scale  Ctrl+A")
        layout.addWidget(self.autoscale_btn)

        self.refresh_btn = QPushButton("⟳")
        self.refresh_btn.setObjectName("actionButton")
        self.refresh_btn.setFixedSize(24, 22)
        self.refresh_btn.setToolTip("Refresh data  F5")
        layout.addWidget(self.refresh_btn)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("actionButton")
        self.settings_btn.setFixedSize(24, 22)
        self.settings_btn.setToolTip("Chart settings")
        layout.addWidget(self.settings_btn)

        layout.addSpacing(4)

        self.order_btn = QPushButton("⚡ Order")
        self.order_btn.setObjectName("orderButton")
        self.order_btn.setFixedHeight(22)
        self.order_btn.setMinimumWidth(64)
        layout.addWidget(self.order_btn)

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────────────────────────

    def set_symbol_text(self, text: str) -> None:
        self.symbol_label.setText(text)

    def set_timeframe(self, interval: str) -> None:
        btn = self._tf_buttons.get(interval)
        if btn:
            btn.setChecked(True)

    def get_timeframe_button(self, interval: str) -> Optional[QPushButton]:
        return self._tf_buttons.get(interval)

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
            for a in grp.actions():
                a.setChecked(False)
            grp.setExclusive(True)

    def set_drawing_color(self, color: str) -> None:
        self._drawing_color = color
        self._refresh_color_btn()

    def _refresh_color_btn(self) -> None:
        c = self._drawing_color
        self.color_btn.setStyleSheet(
            f"QPushButton#colorPickerButton{{"
            f"color:{c};background:#0e1118;border:1px solid #252e40;"
            f"border-radius:3px;font-size:14px;}}"
            f"QPushButton#colorPickerButton:hover{{border-color:#3a7bd5;}}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # STYLES
    # ─────────────────────────────────────────────────────────────────────────

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            QFrame#chartToolbar {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #131720, stop:1 #0e1118);
                border-bottom: 1px solid #1c2230;
            }

            /* Symbol */
            QLabel#symbolBadge {
                color: #4fc3f7;
                font-family: "Segoe UI", sans-serif;
                font-size: 13px;
                font-weight: 800;
                letter-spacing: 1.4px;
                padding: 0 8px;
                background: rgba(79,195,247,0.07);
                border: 1px solid rgba(79,195,247,0.16);
                border-radius: 3px;
            }

            /* Timeframes */
            QPushButton#tfButton {
                background: transparent;
                color: #4e5a70;
                border: none;
                border-radius: 3px;
                font-size: 10px;
                font-weight: 700;
            }
            QPushButton#tfButton:hover {
                color: #c0cce0;
                background: rgba(255,255,255,0.05);
            }
            QPushButton#tfButton:checked {
                color: #4fc3f7;
                background: rgba(79,195,247,0.1);
                border: 1px solid rgba(79,195,247,0.25);
            }

            /* Drawing tools + measure */
            QPushButton#toolButton {
                background: transparent;
                color: #4e5a70;
                border: none;
                border-radius: 3px;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton#toolButton:hover {
                color: #a0b8d0;
                background: rgba(255,255,255,0.06);
            }
            QPushButton#toolButton:checked {
                color: #7ec8ff;
                background: rgba(60,130,220,0.16);
                border: 1px solid rgba(60,130,220,0.3);
            }

            /* Action buttons (clear, autoscale, refresh, settings) */
            QPushButton#actionButton {
                background: transparent;
                color: #4e5a70;
                border: none;
                border-radius: 3px;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton#actionButton:hover {
                color: #a0b8d0;
                background: rgba(255,255,255,0.06);
            }
            QPushButton#actionButton:pressed {
                color: #7ec8ff;
                background: rgba(60,130,220,0.12);
            }

            /* Order */
            QPushButton#orderButton {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #0d3d1c, stop:1 #08260f);
                border: 1px solid #1a6030;
                color: #3dffaa;
                border-radius: 3px;
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 0.5px;
            }
            QPushButton#orderButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #124a24, stop:1 #0c3218);
                border-color: #22a050;
                color: #80ffc8;
            }

            /* Drawing menu */
            QMenu#drawingMenu {
                background: #141820;
                color: #c0cad8;
                border: 1px solid #2a3245;
                padding: 2px;
            }
            QMenu#drawingMenu::item { padding: 4px 18px; font-size: 11px; }
            QMenu#drawingMenu::item:selected { background: #1d3a6e; color: #a8d0ff; }
        """)

        # Per-indicator dynamic style (each gets its own accent color)
        for key, label, color in INDICATORS:
            btn = self.indicator_buttons.get(key)
            if not btn:
                continue
            btn.setStyleSheet(
                f"QPushButton{{"
                f"  color:#2a3245; background:rgba(255,255,255,0.02);"
                f"  border:1px solid #1c2535; border-radius:3px;"
                f"  font-size:9px; font-weight:800; letter-spacing:0.4px;"
                f"}}"
                f"QPushButton:hover{{"
                f"  color:{color}; border:1px solid {color}55;"
                f"  background:{color}10;"
                f"}}"
                f"QPushButton:checked{{"
                f"  color:{color}; border:1px solid {color}60;"
                f"  background:{color}18;"
                f"}}"
            )