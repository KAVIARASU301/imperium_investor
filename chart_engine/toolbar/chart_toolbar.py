# chart_engine/toolbar/chart_toolbar.py
#
# Institutional-grade TC2000-style toolbar — v2
#
# Layout (L → R):
#   [SYMBOL BADGE] [EXCHANGE] | [TF strip: 1m…M] | [Chart type] | [Indicator pills] |
#   [Drawing tools] [Color ✕] <stretch>
#   [VOL] [ALERT] [COMPARE] [SNAP] | [⊡ Autoscale] [⟳ Refresh] [⚙ Settings] | [⚡ ORDER]
#
# New vs v1:
#   • Timeframe buttons as inline pills (no dropdown) — 1-click switching like TC2000
#   • Chart-type selector: Candle / Bar / Line / Area / Heikin-Ashi
#   • Indicator pills: always-visible colored toggles (no hidden menu)
#   • Volume toggle, Alert shortcut, Compare overlay, Snapshot buttons
#   • Live/Delayed data badge on the right
#   • Keyboard shortcut tooltips on every button
#   • Full public API for chart_widget.py to drive
#
# Signals emitted by connecting to individual buttons/menus from chart_widget.py:
#   timeframe_btn.clicked  →  get_timeframe_value()
#   indicator pill toggled →  get_indicator_states() : Dict[str, bool]
#   chart_type_combo       →  get_chart_type()
#   compare_btn.clicked, alert_btn.clicked, snapshot_btn.clicked
#   vol_btn.toggled, autoscale_btn, refresh_btn, settings_btn, order_btn

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
)


# ─── Metadata ─────────────────────────────────────────────────────────────────

TIMEFRAMES: List[Tuple[str, str, str]] = [
    # (display, kite_interval, tooltip)
    ("1m",  "minute",   "1 Minute  [1]"),
    ("3m",  "3minute",  "3 Minutes [3]"),
    ("5m",  "5minute",  "5 Minutes [5]"),
    ("15m", "15minute", "15 Minutes [Q]"),
    ("30m", "30minute", "30 Minutes [H]"),
    ("1h",  "60minute", "1 Hour  [6]"),
    ("D",   "day",      "Daily  [D]"),
    ("W",   "week",     "Weekly  [W]"),
]

# (key, pill_label, hex_color, tooltip)
INDICATORS: List[Tuple[str, str, str, str]] = [
    ("ema10",           "E10",  "#2962ff", "EMA 10"),
    ("ema20",           "E20",  "#9c27b0", "EMA 20"),
    ("ema50",           "E50",  "#f06204", "EMA 50"),
    ("ema200",          "E200", "#e91e63", "EMA 200"),
    ("vwap",            "VWAP", "#ff9e42", "VWAP — Volume Weighted Avg Price"),
    ("atrTrendReversal","ATR",  "#ff5252", "ATR Trend Reversal"),
]

CHART_TYPES: List[Tuple[str, str]] = [
    ("candle",     "Candles"),
    ("bar",        "OHLC Bars"),
    ("line",       "Line"),
    ("area",       "Area"),
    ("heikinashi", "Heikin-Ashi"),
]

# (tool_id, unicode_glyph, tooltip)
DRAWING_TOOLS: List[Tuple[str, str, str]] = [
    ("line",            "╱",  "Trend Line  [L]"),
    ("horizontal_line", "━",  "Horizontal Line  [H]"),
    ("horizontal_ray",  "→",  "Horizontal Ray  [R]"),
    ("rectangle",       "▭",  "Rectangle  [B]"),
    ("fibonacci",       "⌇",  "Fibonacci Retracement  [F]"),
    ("arrow_line",      "↗",  "Arrow  [A]"),
    ("note",            "T",  "Text Note  [N]"),
]

# Backward-compat alias — toolbar/__init__.py imports this
TOOL_DISPLAY: Dict[str, str] = {tid: label for tid, _, label in DRAWING_TOOLS}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _vsep(height: int = 16) -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.VLine)
    sep.setFixedWidth(1)
    sep.setFixedHeight(height)
    sep.setStyleSheet("background: #1e2738; border: none;")
    return sep


def _spacer(w: int = 4) -> QWidget:
    sp = QWidget()
    sp.setFixedWidth(w)
    return sp


# ─── ChartToolbar ─────────────────────────────────────────────────────────────

class ChartToolbar(QFrame):
    """
    Institutional TC2000-style chart toolbar.

    All interactive elements are public attributes so chart_widget.py can wire
    signals directly without going through wrapper methods.
    """

    # ── Emitted when user clicks a timeframe pill ──────────────────────────
    # connect chart_widget to timeframe_changed(kite_interval_str)
    timeframe_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chartToolbar")
        self.setFixedHeight(30)

        # ── State ─────────────────────────────────────────────────────────
        self._drawing_color: str = "#FFD700"
        self._active_tf: str = "day"

        # ── Widget registries ─────────────────────────────────────────────
        self._tf_buttons: Dict[str, QPushButton] = {}       # kept for compat (unused)
        self._tf_btn_group: Optional[QButtonGroup] = None   # kept for compat (unused)
        self._tf_actions: Dict[str, QAction] = {}           # kite_interval → menu action
        self._tf_menu_btn: Optional[QToolButton] = None

        self._ind_buttons: Dict[str, QPushButton] = {}      # key → pill btn (unused now)
        self._ind_colors: Dict[str, str] = {}               # key → hex
        self._ind_actions: Dict[str, QAction] = {}          # key → menu action

        self._tool_buttons: Dict[str, QPushButton] = {}     # tool_id → btn
        self._tool_btn_group: Optional[QButtonGroup] = None
        self._drawing_actions: Dict[str, QAction] = {}
        self._drawing_action_group: Optional[QActionGroup] = None

        # ── Public accessible widgets ──────────────────────────────────────
        self.symbol_label: Optional[QLabel] = None
        self.exchange_label: Optional[QLabel] = None
        self.chart_type_combo: Optional[QComboBox] = None
        self.color_btn: Optional[QPushButton] = None
        self.clear_drawings_btn: Optional[QPushButton] = None
        self.measure_btn: Optional[QPushButton] = None
        self.vol_btn: Optional[QPushButton] = None
        self.alert_btn: Optional[QPushButton] = None
        self.compare_btn: Optional[QPushButton] = None
        self.snapshot_btn: Optional[QPushButton] = None
        self.autoscale_btn: Optional[QPushButton] = None
        self.refresh_btn: Optional[QPushButton] = None
        self.settings_btn: Optional[QPushButton] = None
        self.order_btn: Optional[QPushButton] = None
        self.data_status_label: Optional[QLabel] = None

        # ── Backward-compat shims for chart_widget.py ─────────────────────
        # chart_widget.py checks `if tb.timeframe_dropdown:` and connects to
        # currentIndexChanged / reads currentData().  We keep a hidden
        # QComboBox that mirrors the visible pill buttons so chart_widget.py
        # needs zero changes.
        self.timeframe_dropdown: QComboBox = QComboBox()
        self.timeframe_dropdown.setVisible(False)
        for _display, kite_iv, _tip in TIMEFRAMES:
            self.timeframe_dropdown.addItem(kite_iv, kite_iv)

        # Old indicator menu attributes (kept as stubs)
        self.indicator_menu_button: Optional[QToolButton] = None
        self.indicator_actions: Dict[str, QAction] = {}

        # Shim QAction for get_clear_action() — chart_widget.py does:
        #   tb.get_clear_action().triggered.connect(self._clear_active_tool)
        # We create a real action and wire it to clear_drawings_btn after _build().
        self._clear_action_shim: QAction = QAction("Deselect Tool", self)

        self._build()
        # Wire shim action → clear_drawings_btn so chart_widget.py's
        # get_clear_action().triggered fires when the ✕ button is clicked.
        self.clear_drawings_btn.clicked.connect(self._clear_action_shim.trigger)
        self._apply_styles()

    # ─── Build ────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(0)

        # ── 1. Symbol + Exchange badge ────────────────────────────────────────
        self.symbol_label = QLabel("─")
        self.symbol_label.setObjectName("symbolBadge")
        self.symbol_label.setFixedHeight(20)
        self.symbol_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.exchange_label = QLabel("")
        self.exchange_label.setObjectName("exchangeBadge")
        self.exchange_label.setFixedHeight(20)
        self.exchange_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self.symbol_label)
        layout.addWidget(_spacer(3))
        layout.addWidget(self.exchange_label)
        layout.addWidget(_spacer(6))
        layout.addWidget(_vsep())
        layout.addWidget(_spacer(6))

        # ── 2. Timeframe dropdown ────────────────────────────────────────────
        self._tf_menu = QMenu(self)
        self._tf_menu.setObjectName("tfMenu")
        self._tf_action_group = QActionGroup(self)
        self._tf_action_group.setExclusive(True)

        for display, kite_iv, tip in TIMEFRAMES:
            action = QAction(display, self)
            action.setCheckable(True)
            action.setData(kite_iv)
            action.setToolTip(tip)
            action.triggered.connect(lambda _=False, iv=kite_iv: self._on_tf_clicked(iv))
            self._tf_action_group.addAction(action)
            self._tf_menu.addAction(action)
            self._tf_actions[kite_iv] = action

        self._tf_menu_btn = QToolButton()
        self._tf_menu_btn.setObjectName("tfMenuButton")
        self._tf_menu_btn.setText("D ▾")   # default label updated in set_timeframe
        self._tf_menu_btn.setToolTip("Select timeframe")
        self._tf_menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._tf_menu_btn.setFixedSize(40, 20)
        self._tf_menu_btn.setMenu(self._tf_menu)
        layout.addWidget(self._tf_menu_btn)

        layout.addWidget(_spacer(4))
        layout.addWidget(_vsep())
        layout.addWidget(_spacer(4))

        # ── 3. Chart type ─────────────────────────────────────────────────────
        self.chart_type_combo = QComboBox()
        self.chart_type_combo.setObjectName("chartTypeCombo")
        self.chart_type_combo.setFixedSize(70, 20)
        self.chart_type_combo.view().setMinimumWidth(110)
        for data, label in CHART_TYPES:
            self.chart_type_combo.addItem(f"{label} ▾", data)
        layout.addWidget(self.chart_type_combo)
        layout.addWidget(_spacer(5))
        layout.addWidget(_vsep())
        layout.addWidget(_spacer(5))

        # ── 4. Indicator multi-select dropdown ──────────────────────────────
        self._indicator_menu = QMenu(self)
        self._indicator_menu.setObjectName("indicatorMenu")

        for key, pill_label, color, tip in INDICATORS:
            self._ind_colors[key] = color
            action = QAction(pill_label, self)
            action.setCheckable(True)
            action.setChecked(True)
            action.setData(key)
            action.setToolTip(tip)
            self._indicator_menu.addAction(action)
            self._ind_actions[key] = action
            # keep indicator_actions in sync for old chart_widget compat
            self.indicator_actions[key] = action

        # Volume toggle inside indicator menu
        self._indicator_menu.addSeparator()
        self._vol_action = QAction("Volume Bars", self)
        self._vol_action.setCheckable(True)
        self._vol_action.setChecked(True)
        self._vol_action.setData("volume")
        self._indicator_menu.addAction(self._vol_action)
        # keep vol_btn as a shim so old chart_widget refs don't break
        self.vol_btn = QPushButton()
        self.vol_btn.setVisible(False)
        self.vol_btn.setCheckable(True)
        self.vol_btn.setChecked(True)
        self._vol_action.toggled.connect(self.vol_btn.setChecked)

        self.indicator_menu_button = QToolButton()
        self.indicator_menu_button.setObjectName("indicatorMenuButton")
        self.indicator_menu_button.setText("IND ▾")
        self.indicator_menu_button.setToolTip("Toggle indicators")
        self.indicator_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.indicator_menu_button.setFixedSize(48, 20)
        self.indicator_menu_button.setMenu(self._indicator_menu)
        layout.addWidget(self.indicator_menu_button)

        layout.addWidget(_spacer(4))
        layout.addWidget(_vsep())
        layout.addWidget(_spacer(3))

        # ── 5. Drawing tools container ───────────────────────────────────────
        # Outer container — sharp-edged inset box, groups all drawing controls
        tools_container = QFrame()
        tools_container.setObjectName("drawingContainer")
        tools_container.setFixedHeight(24)
        tools_layout = QHBoxLayout(tools_container)
        tools_layout.setContentsMargins(2, 0, 2, 0)
        tools_layout.setSpacing(0)

        self._tool_btn_group = QButtonGroup(self)
        self._tool_btn_group.setExclusive(False)   # allow de-select

        self._drawing_action_group = QActionGroup(self)
        self._drawing_action_group.setExclusive(True)

        _glyph_font = QFont("Segoe UI Symbol, Noto Sans Symbols, DejaVu Sans, sans-serif")
        _glyph_font.setPixelSize(15)
        _glyph_font.setWeight(QFont.Weight.Black)

        for idx, (tool_id, glyph, tip) in enumerate(DRAWING_TOOLS):
            action = QAction(tip, self)
            action.setCheckable(True)
            self._drawing_action_group.addAction(action)
            self._drawing_actions[tool_id] = action

            btn = QPushButton(glyph)
            btn.setObjectName("toolBtn")
            btn.setCheckable(True)
            btn.setFixedSize(26, 24)
            btn.setToolTip(tip)
            btn.setFont(_glyph_font)
            self._tool_buttons[tool_id] = btn
            self._tool_btn_group.addButton(btn)
            tools_layout.addWidget(btn)

        # Thin divider before measure
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setFixedSize(1, 14)
        div.setStyleSheet("background:#1e2d44; border:none;")
        tools_layout.addWidget(div)

        # Measure tool
        self.measure_btn = QPushButton("⤢")
        self.measure_btn.setObjectName("toolBtn")
        self.measure_btn.setFixedSize(26, 24)
        self.measure_btn.setCheckable(True)
        self.measure_btn.setToolTip("Measure price/time range  [E]")
        self.measure_btn.setFont(_glyph_font)
        tools_layout.addWidget(self.measure_btn)

        # Thin divider before color
        div2 = QFrame()
        div2.setFrameShape(QFrame.Shape.VLine)
        div2.setFixedSize(1, 14)
        div2.setStyleSheet("background:#1e2d44; border:none;")
        tools_layout.addWidget(div2)

        # Color picker
        self.color_btn = QPushButton("■")
        self.color_btn.setObjectName("colorPickerBtn")
        self.color_btn.setFixedSize(26, 24)
        self.color_btn.setToolTip("Drawing color")
        tools_layout.addWidget(self.color_btn)

        # Clear drawings
        self.clear_drawings_btn = QPushButton("✕")
        self.clear_drawings_btn.setObjectName("clearBtn")
        self.clear_drawings_btn.setFixedSize(24, 24)
        self.clear_drawings_btn.setToolTip("Clear all drawings  [Del]")
        tools_layout.addWidget(self.clear_drawings_btn)

        layout.addWidget(tools_container)

        # ── Stretch ───────────────────────────────────────────────────────────
        layout.addStretch()

        # ── 6. Right utility cluster ──────────────────────────────────────────
        # Compare
        self.compare_btn = QPushButton("⧉")
        self.compare_btn.setObjectName("iconBtn")
        self.compare_btn.setFixedSize(26, 20)
        self.compare_btn.setToolTip("Overlay / compare symbol  [C]")
        layout.addWidget(self.compare_btn)
        layout.addWidget(_spacer(2))

        # Snapshot
        self.snapshot_btn = QPushButton("S")
        self.snapshot_btn.setObjectName("iconBtn")
        self.snapshot_btn.setFixedSize(24, 20)
        self.snapshot_btn.setToolTip("Save chart snapshot  [Ctrl+S]")
        layout.addWidget(self.snapshot_btn)

        layout.addWidget(_spacer(3))
        layout.addWidget(_vsep())
        layout.addWidget(_spacer(3))

        # Auto-scale
        self.autoscale_btn = QPushButton("A")
        self.autoscale_btn.setObjectName("iconBtn")
        self.autoscale_btn.setFixedSize(22, 20)
        self.autoscale_btn.setToolTip("Auto-scale  [Ctrl+Z]")
        layout.addWidget(self.autoscale_btn)
        layout.addWidget(_spacer(2))

        # Refresh
        self.refresh_btn = QPushButton("↺")
        self.refresh_btn.setObjectName("iconBtn")
        self.refresh_btn.setFixedSize(22, 20)
        self.refresh_btn.setToolTip("Refresh data  [F5]")
        layout.addWidget(self.refresh_btn)
        layout.addWidget(_spacer(2))

        # Settings
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("iconBtn")
        self.settings_btn.setFixedSize(22, 20)
        self.settings_btn.setToolTip("Chart settings")
        layout.addWidget(self.settings_btn)

        layout.addWidget(_spacer(6))
        layout.addWidget(_vsep())
        layout.addWidget(_spacer(5))

        # Order button
        self.order_btn = QPushButton("ORDER")
        self.order_btn.setObjectName("orderBtn")
        self.order_btn.setFixedSize(72, 22)
        self.order_btn.setToolTip("Place order  [O]")
        layout.addWidget(self.order_btn)

        # ── Set default timeframe ─────────────────────────────────────────────
        self.set_timeframe("day")
        self._refresh_color_btn()

    def _make_ind_pill(self, key: str, label: str, color: str, tip: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("indPill")
        btn.setCheckable(True)
        btn.setChecked(True)
        btn.setToolTip(tip)
        btn.setFixedSize(_ind_pill_width(label), 20)
        # Encode accent color via property so stylesheet can reference it,
        # but we set the style directly since Qt props aren't CSS variables.
        btn.setProperty("accentColor", color)
        # Active/inactive driven fully by stylesheet + :checked pseudo-state
        # We override inline only for the accent border/text color:
        self._set_ind_pill_style(btn, color, True)
        btn.toggled.connect(lambda checked, b=btn, c=color: self._set_ind_pill_style(b, c, checked))
        return btn

    @staticmethod
    def _set_ind_pill_style(btn: QPushButton, color: str, checked: bool) -> None:
        """Inline style for a single indicator pill depending on its checked state."""
        if checked:
            btn.setStyleSheet(
                f"QPushButton#indPill{{"
                f"color:{color};"
                f"background:rgba({_hex_to_rgb(color)},0.12);"
                f"border:1px solid rgba({_hex_to_rgb(color)},0.45);"
                f"border-radius:3px;"
                f"font-size:9px;font-weight:800;font-family:'JetBrains Mono','Fira Code','Consolas',monospace;"
                f"letter-spacing:0.3px;"
                f"}}"
                f"QPushButton#indPill:hover{{"
                f"background:rgba({_hex_to_rgb(color)},0.22);"
                f"border-color:rgba({_hex_to_rgb(color)},0.7);"
                f"}}"
            )
        else:
            btn.setStyleSheet(
                "QPushButton#indPill{"
                "color:#38445a;"
                "background:transparent;"
                "border:1px solid #1e2738;"
                "border-radius:3px;"
                "font-size:9px;font-weight:800;"
                "font-family:'JetBrains Mono','Fira Code','Consolas',monospace;"
                "}"
                "QPushButton#indPill:hover{color:#5a6e88;border-color:#2e3e56;}"
            )

    # ─── Event handlers ───────────────────────────────────────────────────────

    def _on_tf_clicked(self, kite_iv: str) -> None:
        self._active_tf = kite_iv
        # Update button label to show active TF
        action = self._tf_actions.get(kite_iv)
        if action and self._tf_menu_btn:
            self._tf_menu_btn.setText(f"{action.text()} ▾")
        # Keep hidden shim combo in sync so chart_widget.py wiring still fires
        for i in range(self.timeframe_dropdown.count()):
            if self.timeframe_dropdown.itemData(i) == kite_iv:
                self.timeframe_dropdown.setCurrentIndex(i)
                break
        self.timeframe_changed.emit(kite_iv)

    # ─── Public API ───────────────────────────────────────────────────────────

    def set_symbol_text(self, symbol: str, exchange: str = "") -> None:
        """Update symbol badge. Optionally set exchange tag (NSE / BSE / NYSE)."""
        self.symbol_label.setText(symbol)
        if exchange:
            self.exchange_label.setText(exchange)
            self.exchange_label.setVisible(True)
        else:
            self.exchange_label.setVisible(False)

    def set_timeframe(self, kite_interval: str) -> None:
        """Activate the matching TF menu action and sync the shim combo."""
        action = self._tf_actions.get(kite_interval)
        if action:
            action.setChecked(True)
            self._active_tf = kite_interval
            if self._tf_menu_btn:
                self._tf_menu_btn.setText(f"{action.text()} ▾")
        # Keep hidden shim combo in sync
        for i in range(self.timeframe_dropdown.count()):
            if self.timeframe_dropdown.itemData(i) == kite_interval:
                self.timeframe_dropdown.blockSignals(True)
                self.timeframe_dropdown.setCurrentIndex(i)
                self.timeframe_dropdown.blockSignals(False)
                break

    def get_timeframe_value(self) -> str:
        return self._active_tf

    def get_chart_type(self) -> str:
        if self.chart_type_combo:
            return self.chart_type_combo.currentData() or "candle"
        return "candle"

    def get_indicator_states(self) -> Dict[str, bool]:
        """Returns {key: is_checked} for all indicator menu actions."""
        return {key: action.isChecked() for key, action in self._ind_actions.items()}

    def set_indicator_state(self, key: str, checked: bool) -> None:
        action = self._ind_actions.get(key)
        if action:
            action.setChecked(checked)

    def get_drawing_action(self, tool_id: str) -> Optional[QAction]:
        return self._drawing_actions.get(tool_id)

    def get_clear_action(self) -> QAction:
        """Returns a real QAction whose .triggered fires when ✕ is clicked."""
        return self._clear_action_shim

    def get_all_drawing_actions(self):
        return list(self._drawing_actions.values())

    def get_drawing_action_group(self) -> Optional[QActionGroup]:
        return self._drawing_action_group

    def get_tool_button(self, tool_id: str) -> Optional[QPushButton]:
        return self._tool_buttons.get(tool_id)

    def set_draw_btn_active(self, tool_id: str) -> None:
        self.reset_draw_btn()
        btn = self._tool_buttons.get(tool_id)
        if btn:
            btn.setChecked(True)
        action = self._drawing_actions.get(tool_id)
        if action:
            action.setChecked(True)

    def reset_draw_btn(self) -> None:
        for btn in self._tool_buttons.values():
            btn.setChecked(False)
        if self.measure_btn:
            self.measure_btn.setChecked(False)
        grp = self._drawing_action_group
        if grp:
            grp.setExclusive(False)
            for a in grp.actions():
                a.setChecked(False)
            grp.setExclusive(True)

    def set_drawing_color(self, color: str) -> None:
        self._drawing_color = color
        self._refresh_color_btn()

    def get_drawing_color(self) -> str:
        return self._drawing_color

    def set_data_status(self, status: str, live: bool = True) -> None:
        """Update the live/delayed/offline badge text and color."""
        if self.data_status_label:
            self.data_status_label.setText(status)
            if live:
                self.data_status_label.setStyleSheet(
                    "QLabel#livebage,QLabel#livebage{}"   # use class style from sheet
                )
                self.data_status_label.setObjectName("liveBadge")
            else:
                self.data_status_label.setObjectName("delayedBadge")
            # re-polish so stylesheet picks up new objectName
            self.data_status_label.style().unpolish(self.data_status_label)
            self.data_status_label.style().polish(self.data_status_label)

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _refresh_color_btn(self) -> None:
        c = self._drawing_color
        self.color_btn.setStyleSheet(
            f"QPushButton#colorPickerBtn{{"
            f"color:{c};"
            f"background:transparent;"
            f"border:none;"
            f"border-radius:0px;"
            f"font-size:16px;"
            f"padding:0;"
            f"}}"
            f"QPushButton#colorPickerBtn:hover{{"
            f"background:#162030;"
            f"border-left:2px solid rgba({_hex_to_rgb(c)},0.8);"
            f"}}"
        )

    # ─── Stylesheet ───────────────────────────────────────────────────────────

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            /* ── Toolbar frame ── */
            QFrame#chartToolbar {
                background: #0d1117;
                border-top: 1px solid #1c2840;
                border-bottom: 1px solid #1c2840;
            }

            /* ── Symbol badge ── */
            QLabel#symbolBadge {
                color: #5bc8fa;
                font-family: "JetBrains Mono","Fira Code","Consolas",monospace;
                font-size: 12px;
                font-weight: 800;
                letter-spacing: 1.8px;
                padding: 0 8px;
                background: rgba(91,200,250,0.06);
                border: 1px solid rgba(91,200,250,0.18);
                border-radius: 3px;
                min-width: 60px;
            }

            QLabel#exchangeBadge {
                color: #3d5170;
                font-family: "JetBrains Mono","Fira Code","Consolas",monospace;
                font-size: 8px;
                font-weight: 700;
                letter-spacing: 1px;
                padding: 0 5px;
                background: transparent;
                border: 1px solid #1e2738;
                border-radius: 3px;
            }

            /* ── Timeframe pills ── */
            QPushButton#tfPill {
                background: transparent;
                color: #3d5070;
                border: none;
                border-radius: 3px;
                font-size: 9px;
                font-weight: 800;
                font-family: "JetBrains Mono","Fira Code","Consolas",monospace;
                letter-spacing: 0.2px;
            }
            QPushButton#tfPill:hover {
                color: #8aaecf;
                background: rgba(255,255,255,0.05);
            }
            QPushButton#tfPill:checked {
                color: #5bc8fa;
                background: rgba(91,200,250,0.11);
                border: 1px solid rgba(91,200,250,0.30);
            }

            /* ── Chart type combo ── */
            QComboBox#chartTypeCombo {
                background: rgba(255,255,255,0.02);
                color: #607090;
                border: 1px solid #1e2738;
                border-radius: 3px;
                font-size: 9px;
                font-weight: 700;
                font-family: "JetBrains Mono","Fira Code","Consolas",monospace;
                padding-left: 4px;
            }
            QComboBox#chartTypeCombo:hover {
                color: #8aaecf;
                border-color: #2e4060;
            }
            QComboBox#chartTypeCombo::drop-down {
                width: 0px;
                border: none;
                subcontrol-origin: padding;
                subcontrol-position: top right;
            }
            QComboBox#chartTypeCombo::down-arrow {
                image: none;
                width: 0; height: 0;
            }
            QComboBox#chartTypeCombo QAbstractItemView {
                background: #0f1420;
                color: #8aaecf;
                border: 1px solid #2a3a56;
                selection-background-color: #1a3058;
                font-size: 10px;
                padding: 2px;
            }

            /* ── Timeframe dropdown button ── */
            QToolButton#tfMenuButton {
                background: rgba(255,255,255,0.02);
                color: #5bc8fa;
                border: 1px solid #1e2738;
                border-radius: 3px;
                font-size: 9px;
                font-weight: 800;
                font-family: "JetBrains Mono","Fira Code","Consolas",monospace;
                letter-spacing: 0.3px;
                padding: 0 5px;
            }
            QToolButton#tfMenuButton:hover {
                color: #8adefc;
                border-color: rgba(91,200,250,0.35);
                background: rgba(91,200,250,0.07);
            }
            QToolButton#tfMenuButton::menu-indicator { image: none; width: 0; }

            QMenu#tfMenu {
                background: #0f1420;
                color: #a0b4cc;
                border: 1px solid #1e2e48;
                padding: 3px 0;
            }
            QMenu#tfMenu::item {
                padding: 5px 24px 5px 12px;
                font-size: 11px;
                font-family: "JetBrains Mono","Fira Code","Consolas",monospace;
                font-weight: 700;
            }
            QMenu#tfMenu::item:selected { background: #142040; color: #5bc8fa; }
            QMenu#tfMenu::indicator { width: 0; height: 0; }

            /* ── Indicator dropdown button ── */
            QToolButton#indicatorMenuButton {
                background: rgba(255,255,255,0.02);
                color: #607090;
                border: 1px solid #1e2738;
                border-radius: 3px;
                font-size: 9px;
                font-weight: 800;
                font-family: "JetBrains Mono","Fira Code","Consolas",monospace;
                letter-spacing: 0.3px;
                padding: 0 4px;
            }
            QToolButton#indicatorMenuButton:hover {
                color: #8aaecf;
                border-color: #2e4060;
                background: rgba(255,255,255,0.05);
            }
            QToolButton#indicatorMenuButton::menu-indicator { image: none; width: 0; }

            QMenu#indicatorMenu {
                background: #0f1420;
                color: #a0b4cc;
                border: 1px solid #1e2e48;
                padding: 3px 0;
            }
            QMenu#indicatorMenu::item {
                padding: 5px 18px 5px 10px;
                font-size: 11px;
                font-family: "JetBrains Mono","Fira Code","Consolas",monospace;
            }
            QMenu#indicatorMenu::item:selected { background: #142040; color: #7ec8ff; }
            QMenu#indicatorMenu::indicator {
                width: 12px; height: 12px;
                border: 1px solid #2e3e58;
                border-radius: 2px;
                margin-left: 6px;
            }
            QMenu#indicatorMenu::indicator:checked {
                background: #1a5080;
                border-color: #3a80c0;
            }

            /* ── Drawing tools container ── */
            QFrame#drawingContainer {
                background: #0d1117;
                border: 1px solid #1c2840;
                border-radius: 3px;
            }

            /* ── Drawing tool buttons ── */
            QPushButton#toolBtn {
                background: transparent;
                color: #6888aa;
                border: none;
                border-radius: 2px;
                font-size: 15px;
                font-weight: 900;
                padding: 0px 0px 0px 0px;
                margin: 0;
            }
            QPushButton#toolBtn:hover {
                color: #c8dff5;
                background: #1a2840;
            }
            QPushButton#toolBtn:checked {
                color: #5bc8fa;
                background: #0f2030;
                border-left: 2px solid #5bc8fa;
            }

            /* ── Clear button ── */
            QPushButton#clearBtn {
                background: transparent;
                color: #4a5e78;
                border: none;
                border-radius: 0px;
                font-size: 11px;
                font-weight: 900;
                padding: 0;
            }
            QPushButton#clearBtn:hover {
                color: #e05858;
                background: #200c0c;
            }

            /* ── VOL pill ── */
            QPushButton#utilPill {
                background: transparent;
                color: #3d5070;
                border: 1px solid #1e2738;
                border-radius: 3px;
                font-size: 8px;
                font-weight: 800;
                font-family: "JetBrains Mono","Fira Code","Consolas",monospace;
                letter-spacing: 0.5px;
            }
            QPushButton#utilPill:hover {
                color: #7090b8;
                border-color: #2e4060;
            }
            QPushButton#utilPill:checked {
                color: #4fd8a0;
                background: rgba(79,216,160,0.09);
                border: 1px solid rgba(79,216,160,0.30);
            }

            /* ── Icon buttons (compare, snapshot, autoscale, refresh, settings) ── */
            QPushButton#iconBtn {
                background: transparent;
                color: #4a6280;
                border: none;
                border-radius: 3px;
                font-size: 14px;
                font-weight: 700;
                padding: 0;
            }
            QPushButton#iconBtn:hover {
                color: #c0d8f0;
                background: rgba(255,255,255,0.07);
            }
            QPushButton#iconBtn:pressed {
                color: #5bc8fa;
                background: rgba(91,200,250,0.12);
            }

            /* ── Live / Delayed badge ── */
            QLabel#liveBadge {
                color: #4fd8a0;
                font-family: "JetBrains Mono","Fira Code","Consolas",monospace;
                font-size: 7px;
                font-weight: 900;
                letter-spacing: 1.5px;
                padding: 0 5px;
                background: rgba(79,216,160,0.07);
                border: 1px solid rgba(79,216,160,0.22);
                border-radius: 2px;
            }
            QLabel#delayedBadge {
                color: #f0a030;
                font-family: "JetBrains Mono","Fira Code","Consolas",monospace;
                font-size: 7px;
                font-weight: 900;
                letter-spacing: 1.5px;
                padding: 0 5px;
                background: rgba(240,160,48,0.07);
                border: 1px solid rgba(240,160,48,0.22);
                border-radius: 2px;
            }
            QLabel#livebage { color: #4fd8a0; }  /* fallback */

            /* ── Order button — subtle rest, vivid on hover ── */
            QPushButton#orderBtn {
                background: transparent;
                border: 1px solid #1e3828;
                color: #2a6040;
                border-radius: 3px;
                font-size: 9px;
                font-weight: 900;
                font-family: "JetBrains Mono","Fira Code","Consolas",monospace;
                letter-spacing: 1.2px;
            }
            QPushButton#orderBtn:hover {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #0d3e1c, stop:1 #081e0e);
                border-color: #2a9050;
                color: #4dffb0;
            }
            QPushButton#orderBtn:pressed {
                background: #071408;
                border-color: #1a6030;
                color: #20cc80;
            }
        """)


# ─── Utilities ────────────────────────────────────────────────────────────────

def _hex_to_rgb(hex_color: str) -> str:
    """Convert #rrggbb → 'r,g,b' string for use inside rgba()."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return "255,255,255"
    return f"{r},{g},{b}"


def _ind_pill_width(label: str) -> int:
    """Compute pill width based on label length."""
    return max(26, 8 * len(label) + 10)