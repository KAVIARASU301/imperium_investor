# chart_engine/toolbar/chart_toolbar.py
#
# Institutional TC2000-style chart toolbar — v3 (Premium Redesign)
#
# Design Language:
#   • Bloomberg Terminal meets TradingView Pro — dark obsidian background
#   • Symbol badge with live price accent glow (cyan-on-slate)
#   • Segmented pill groups with hairline dividers — zero visual clutter
#   • Icon-forward drawing tools with labeled fallback on hover
#   • Monospace numerics only (JetBrains Mono) — everything else: system sans
#   • Micro-animations via border transitions (CSS)
#   • Accent palette: #00d4ff (cyan), #22d3a0 (teal), #ff4d6a (red), #fbbf24 (amber)
#
# Layout (L → R):
#   [SYMBOL] [TF ▾] [CHART TYPE] | [IND ▾] | [TOOLS tray ✎▾ + fav pins] |──|
#   <stretch> [S] [⊡] [↺] [⚙] | [ORDER]

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPixmap
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
    QWidgetAction,
)

from utils.resource_path import resource_path


# ─── Metadata ─────────────────────────────────────────────────────────────────

TIMEFRAMES: List[Tuple[str, str, str]] = [
    ("1m",  "minute",   "1 Minute  [1]"),
    ("3m",  "3minute",  "3 Minutes [3]"),
    ("5m",  "5minute",  "5 Minutes [5]"),
    ("15m", "15minute", "15 Minutes [Q]"),
    ("30m", "30minute", "30 Minutes [H]"),
    ("1H",  "60minute", "1 Hour  [6]"),
    ("D",   "day",      "Daily  [D]"),
    ("W",   "week",     "Weekly  [W]"),
    ("M",   "month",    "Monthly  [M]"),
]

TOOLBAR_TIMEFRAME_LABELS: Dict[str, str] = {
    # The 30-minute label is the only 3-character lowercase interval and can
    # elide inside the compact toolbar button on some platforms/fonts.
    # Keep the menu label descriptive, but render the toolbar chip as "30".
    "30minute": "30",
}

INDICATORS: List[Tuple[str, str, str, str]] = []

CHART_TYPES: List[Tuple[str, str, str]] = [
    ("candle",     "🕯",  "Candlestick"),
    ("bar",        "|||", "OHLC Bars"),
    ("line",       "〜",  "Line"),
    ("heikinashi", "HA",  "Heikin-Ashi"),
]

DRAWING_TOOLS: List[Tuple[str, str, str]] = [
    ("line",             "╱",  "Trend Line  [L]"),
    ("horizontal_line",  "──", "Horizontal Line  [H]"),
    ("horizontal_ray",   "→",  "Ray  [R]"),
    ("rectangle",        "⬜", "Rectangle  [B]"),
    ("fibonacci",        "≋",  "Fibonacci  [F]"),
    ("arrow_line",       "↗",  "Arrow  [A]"),
    ("note",             "T",  "Text Note  [N]"),
]

TOOL_DISPLAY: Dict[str, str] = {tid: label for tid, _, label in DRAWING_TOOLS}


def _toolbar_timeframe_label(kite_interval: str, fallback: str) -> str:
    """Return the compact label used by toolbar timeframe controls."""
    return TOOLBAR_TIMEFRAME_LABELS.get(kite_interval, fallback)

ICON_ASSETS: Dict[str, str] = {
    # Primary chart controls
    "chart_candle": "candlestick.svg",
    "indicator": "indicator.svg",
    "drawing_menu": "drawing_tool.svg",
    "snapshot": "snapshot.svg",
    "auto_scale": "auto_scale.svg",
    "refresh": "refresh.svg",
    "settings": "gear_setting.svg",

    # Drawing tools
    "line": "trend_line.svg",
    "horizontal_line": "horizontal_line.svg",
    "horizontal_ray": "horizontal_ray.svg",
    "rectangle": "rectangle.svg",
    "fibonacci": "fibonacci.svg",
    "arrow_line": "arrow.svg",
    "note": "text.svg",
    "measure": "measuring_scale.svg",
    "clear": "clear.svg",
    "delete": "delete.svg",
    "plus": "plus.svg",

    # Shared application/status icons kept available for toolbar extensions.
    "alert": "alert.svg",
    "connected": "connected.svg",
    "disconnected": "disconnected.svg",
    "portfolio": "portfolio.svg",
    "order": "order.svg",
}



# ─── Palette constants ────────────────────────────────────────────────────────

class P:
    # AMOLED / TC2000-style terminal palette. Keep icons and accents quiet.
    BG_BASE   = "#050709"   # toolbar root
    BG_RAISED = "#0a0d12"   # compact controls
    BG_PANEL  = "#0f1318"   # grouped trays / menus
    BG_HOVER  = "#141920"   # hover state
    BG_ACTIVE = "#1a2840"   # selected / checked
    BG_SELECTED = "#111a27" # calm selected fill for toolbar/menu controls
    BG_SELECTED_2 = "#162236" # checked/active fill, no bright side stripe

    BORDER      = "#1a2030"
    BORDER_MID  = "#263247"
    BORDER_SOFT = "#111722"

    T_DIM    = "#2a3a50"
    T_MID    = "#5a7090"
    T_MUTED  = "#8292a8"
    T_MAIN   = "#a8bcd4"
    T_BRIGHT = "#e8f0ff"
    SYMBOL   = "#cbd5e1"

    ICON_DIM   = "#586579"
    ICON_HOVER = "#7a8798"
    ICON_ON    = "#9aa7b8"

    CYAN   = "#00d4ff"
    TEAL   = "#00d4a8"
    AMBER  = "#f59e0b"
    RED    = "#ff4d6a"
    PURPLE = "#8b7bc8"


_TOOLBAR_H = 26
_CONTROL_H = 20
_ICON_SIZE = 13
_MENU_ROW_H = 26


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _vsep(h: int = 14) -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.VLine)
    f.setFixedWidth(1)
    f.setFixedHeight(h)
    f.setStyleSheet(f"background:{P.BORDER}; border:none;")
    return f


def _gap(w: int = 4) -> QWidget:
    sp = QWidget()
    sp.setFixedWidth(w)
    sp.setStyleSheet("background:transparent;")
    return sp


def _hex_to_rgb(h: str) -> str:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return f"{int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)}"
    except (ValueError, IndexError):
        return "255,255,255"


def _icon_path(icon_key: str) -> str:
    return resource_path(f"assets/icons/{ICON_ASSETS[icon_key]}")


def _raw_icon(icon_key: str) -> QIcon:
    """Return the bundled SVG/PNG icon without color treatment."""
    asset_name = ICON_ASSETS.get(icon_key)
    if not asset_name:
        return QIcon()
    return QIcon(resource_path(f"assets/icons/{asset_name}"))


def _tinted_icon(icon_key: str, size: int = _ICON_SIZE, color: str = P.ICON_DIM) -> QIcon:
    """Render bundled icons as quiet grey glyphs so the toolbar stays non-distracting."""
    raw = _raw_icon(icon_key)
    if raw.isNull():
        return QIcon()

    base = QPixmap(size, size)
    base.fill(Qt.GlobalColor.transparent)
    painter = QPainter(base)
    raw.paint(painter, 0, 0, size, size)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(base.rect(), QColor(color))
    painter.end()
    return QIcon(base)


def _icon(icon_key: str) -> QIcon:
    """Return a bundled toolbar icon tinted to muted grey."""
    return _tinted_icon(icon_key)


def _icon_pixmap(icon_key: str, size: int = _ICON_SIZE) -> QPixmap:
    return _tinted_icon(icon_key, size).pixmap(QSize(size, size))


def _apply_icon(button, icon_key: str, size: int = _ICON_SIZE) -> None:
    button.setIcon(_tinted_icon(icon_key, size))
    button.setIconSize(QSize(size, size))



# ─── Drawing tool menu row ────────────────────────────────────────────────────

class ToolMenuItemWidget(QWidget):
    triggered        = Signal(str)
    favorite_toggled = Signal(str, bool)

    def __init__(self, tool_id: str, glyph: str, label: str,
                 is_fav: bool, icon_key: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.tool_id = tool_id
        self._selected = False
        self.setObjectName("menuItem")
        self.setFixedHeight(_MENU_ROW_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(7, 0, 8, 0)
        lay.setSpacing(6)

        self.selected_mark = QLabel("✓")
        self.selected_mark.setFixedSize(12, 16)
        self.selected_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_label = QLabel(glyph)
        self.icon_label.setFixedSize(16, 16)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if icon_key:
            self.icon_label.setText("")
            self.icon_label.setPixmap(_icon_pixmap(icon_key, _ICON_SIZE))

        self.text_label = QLabel(label)

        self.star = QPushButton("★" if is_fav else "☆")
        self.star.setCheckable(True)
        self.star.setChecked(is_fav)
        self.star.setFixedSize(20, 20)
        self.star.setObjectName("starBtn")
        self.star.setCursor(Qt.CursorShape.PointingHandCursor)
        self.star.setStyleSheet(
            "QPushButton#starBtn{color:#2a3a50;background:transparent;border:none;font-size:12px;}"
            "QPushButton#starBtn:hover{color:#6b7d92;}"
            "QPushButton#starBtn:checked{color:#8a7247;}"
        )
        self.star.toggled.connect(
            lambda c: (self.star.setText("★" if c else "☆"),
                       self.favorite_toggled.emit(self.tool_id, c))
        )

        lay.addWidget(self.selected_mark)
        lay.addWidget(self.icon_label)
        lay.addWidget(self.text_label)
        lay.addStretch()
        lay.addWidget(self.star)
        self._refresh_selected_style()

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self._refresh_selected_style()

    def _refresh_selected_style(self) -> None:
        if self._selected:
            self.setStyleSheet(f"QWidget#menuItem{{background:{P.BG_SELECTED_2}; border:1px solid rgba(154,167,184,0.22); border-radius:2px;}} QWidget#menuItem:hover{{background:{P.BG_SELECTED_2}; border-color:rgba(154,167,184,0.34);}}")
            self.selected_mark.setStyleSheet(f"color:{P.ICON_ON}; background:transparent; font-size:10px; font-weight:700;")
            self.icon_label.setStyleSheet(
                f"font-size:12px; color:{P.ICON_ON}; background:transparent;"
                "font-family:'Segoe UI Symbol','Noto Sans Symbols',sans-serif;"
            )
            self.text_label.setStyleSheet(f"font-size:10px; color:{P.T_BRIGHT}; font-weight:650; background:transparent;")
        else:
            self.setStyleSheet(f"QWidget#menuItem{{background:transparent; border:1px solid transparent; border-radius:2px;}} QWidget#menuItem:hover{{background:{P.BG_SELECTED}; border-color:rgba(90,112,144,0.16);}}")
            self.selected_mark.setStyleSheet("color:transparent; background:transparent; font-size:10px; font-weight:700;")
            self.icon_label.setStyleSheet(
                "font-size:12px; color:#667386; background:transparent;"
                "font-family:'Segoe UI Symbol','Noto Sans Symbols',sans-serif;"
            )
            self.text_label.setStyleSheet("font-size:10px; color:#a8bcd4; font-weight:500; background:transparent;")

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            if not self.star.geometry().contains(ev.pos()):
                self.triggered.emit(self.tool_id)
        super().mousePressEvent(ev)


class TimeframeMenuItemWidget(QWidget):
    triggered        = Signal(str)
    favorite_toggled = Signal(str, bool)

    def __init__(self, label: str, kite_interval: str, tooltip: str, is_fav: bool, parent=None):
        super().__init__(parent)
        self.kite_interval = kite_interval
        self.setObjectName("menuItem")
        self.setFixedHeight(_MENU_ROW_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self._selected = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(7, 0, 8, 0)
        lay.setSpacing(6)

        self.selected_mark = QLabel("✓")
        self.selected_mark.setFixedSize(12, 16)
        self.selected_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tf_label = QLabel(label)
        self.tf_label.setFixedWidth(28)
        self.full_label = QLabel(tooltip.split("  [")[0])
        self._refresh_selected_style()

        self.star = QPushButton("★" if is_fav else "☆")
        self.star.setCheckable(True)
        self.star.setChecked(is_fav)
        self.star.setFixedSize(20, 20)
        self.star.setObjectName("starBtn")
        self.star.setCursor(Qt.CursorShape.PointingHandCursor)
        self.star.setStyleSheet(
            "QPushButton#starBtn{color:#2a3a50;background:transparent;border:none;font-size:12px;}"
            "QPushButton#starBtn:hover{color:#6b7d92;}"
            "QPushButton#starBtn:checked{color:#8a7247;}"
        )
        self.star.toggled.connect(
            lambda c: (self.star.setText("★" if c else "☆"),
                       self.favorite_toggled.emit(self.kite_interval, c))
        )

        lay.addWidget(self.selected_mark)
        lay.addWidget(self.tf_label)
        lay.addWidget(self.full_label)
        lay.addStretch()
        lay.addWidget(self.star)

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self._refresh_selected_style()

    def _refresh_selected_style(self) -> None:
        if self._selected:
            self.setStyleSheet(f"QWidget#menuItem{{background:{P.BG_SELECTED_2}; border:1px solid rgba(154,167,184,0.22); border-radius:2px;}} QWidget#menuItem:hover{{background:{P.BG_SELECTED_2}; border-color:rgba(154,167,184,0.34);}}")
            self.selected_mark.setStyleSheet(f"color:{P.ICON_ON}; background:transparent; font-size:10px; font-weight:700;")
            self.tf_label.setStyleSheet("font-size:11px; color:#e8f0ff; font-weight:700; background:transparent;")
            self.full_label.setStyleSheet("font-size:10px; color:#9aa7b8; background:transparent; font-weight:500;")
        else:
            self.setStyleSheet(f"QWidget#menuItem{{background:transparent; border:1px solid transparent; border-radius:2px;}} QWidget#menuItem:hover{{background:{P.BG_SELECTED}; border-color:rgba(90,112,144,0.16);}}")
            self.selected_mark.setStyleSheet("color:transparent; background:transparent; font-size:10px; font-weight:700;")
            self.tf_label.setStyleSheet("font-size:11px; color:#a8bcd4; font-weight:600; background:transparent;")
            self.full_label.setStyleSheet("font-size:10px; color:#5a7090; background:transparent; font-weight:500;")

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            if not self.star.geometry().contains(ev.pos()):
                self.triggered.emit(self.kite_interval)
        super().mousePressEvent(ev)


# ─── ChartToolbar ─────────────────────────────────────────────────────────────

class ChartToolbar(QFrame):
    """
    Premium institutional chart toolbar.
    Public attributes are backward-compatible with chart_widget.py.
    """

    timeframe_changed = Signal(str)
    toolbar_preferences_changed = Signal(dict)
    manage_indicators_requested = Signal()
    _UTILITY_HIDE_WIDTH = 600

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chartToolbar")
        self.setFixedHeight(_TOOLBAR_H)

        # ── State ─────────────────────────────────────────────────────────
        self._drawing_color = "#A51D2D"
        self._active_tf     = "day"
        self._live_price    = 0.0
        self._suppress_pref_events = False

        # ── Registries ────────────────────────────────────────────────────
        self._tf_actions:       Dict[str, QAction]     = {}
        self._ind_actions:      Dict[str, QAction]     = {}
        self._drawing_actions:  Dict[str, QAction]     = {}
        self._drawing_action_group: Optional[QActionGroup] = None
        self._tool_buttons:     Dict[str, QPushButton] = {}
        self._tool_btn_group:   Optional[QButtonGroup] = None
        self._favorite_tools = ["line"]
        self._favorite_timeframes = []
        self._show_snapshot = True
        self._show_autoscale = True
        self._show_refresh = True
        self._tf_menu_items: Dict[str, TimeframeMenuItemWidget] = {}
        self._drawing_menu_items: Dict[str, ToolMenuItemWidget] = {}

        # ── Public compat attributes ───────────────────────────────────────
        self.symbol_label:      Optional[QLabel]       = None
        self.exchange_label:    Optional[QLabel]       = None
        self.chart_type_combo:  Optional[QComboBox]    = None
        self.color_btn:         Optional[QPushButton]  = None
        self.clear_drawings_btn: Optional[QPushButton] = None
        self.measure_btn:       Optional[QPushButton]  = None
        self.vol_btn:           Optional[QPushButton]  = None
        self.alert_btn:         Optional[QPushButton]  = None
        self.snapshot_btn:      Optional[QPushButton]  = None
        self.autoscale_btn:     Optional[QPushButton]  = None
        self.refresh_btn:       Optional[QPushButton]  = None
        self.settings_btn:      Optional[QPushButton]  = None
        self.order_btn:         Optional[QPushButton]  = None
        self.data_status_label: Optional[QLabel]       = None
        self.indicator_menu_button: Optional[QToolButton] = None
        self.indicator_actions: Dict[str, QAction]     = {}

        # Hidden shim combo (chart_widget.py reads currentData from this)
        self.timeframe_dropdown = QComboBox()
        self.timeframe_dropdown.setVisible(False)
        for _, kite_iv, _ in TIMEFRAMES:
            self.timeframe_dropdown.addItem(kite_iv, kite_iv)

        # Shim action for get_clear_action()
        self._clear_action_shim = QAction("Deselect Tool", self)

        self._build()
        self.clear_drawings_btn.clicked.connect(self._clear_action_shim.trigger)
        self._apply_styles()

    # ═══════════════════════════════════════════════════════════════════════
    # BUILD
    # ═══════════════════════════════════════════════════════════════════════

    def _build(self) -> None:
        lay = QHBoxLayout(self)
        lay.setContentsMargins(5, 0, 5, 0)
        lay.setSpacing(0)

        # ── 1. SYMBOL BADGE ───────────────────────────────────────────────
        symbol_block = QWidget()
        symbol_block.setObjectName("symbolBlock")
        sb_lay = QHBoxLayout(symbol_block)
        sb_lay.setContentsMargins(0, 0, 0, 0)
        sb_lay.setSpacing(2)

        self.symbol_label = QLabel("─")
        self.symbol_label.setObjectName("symbolBadge")
        self.symbol_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        sb_lay.addWidget(self.symbol_label)

        lay.addWidget(symbol_block)
        lay.addStretch(1)
        lay.addWidget(_vsep())
        lay.addWidget(_gap(4))

        # ── 2. TIMEFRAME DROPDOWN PILL ────────────────────────────────────
        self._tf_menu = QMenu(self)
        self._tf_menu.setObjectName("dropdownMenu")
        tf_group = QActionGroup(self)
        tf_group.setExclusive(True)

        for display, kite_iv, tip in TIMEFRAMES:
            action = QAction(display, self)
            action.setData(kite_iv)
            action.setToolTip(tip)
            action.triggered.connect(lambda _=False, iv=kite_iv: self._on_tf_clicked(iv))
            tf_group.addAction(action)
            self._tf_actions[kite_iv] = action
            item = TimeframeMenuItemWidget(display, kite_iv, tip, kite_iv in self._favorite_timeframes, self)
            self._tf_menu_items[kite_iv] = item
            item.triggered.connect(self._on_tf_clicked)
            item.favorite_toggled.connect(self._on_tf_fav_toggled)
            wa = QWidgetAction(self)
            wa.setDefaultWidget(item)
            self._tf_menu.addAction(wa)

        self._tf_menu_btn = QToolButton()
        self._tf_menu_btn.setObjectName("pillMenuBtn")
        self._tf_menu_btn.setText("D")
        self._tf_menu_btn.setToolTip("Select timeframe")
        self._tf_menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._tf_menu_btn.setFixedSize(30, _CONTROL_H)
        self._tf_menu_btn.setMenu(self._tf_menu)
        self._tf_menu_btn.setProperty("selectionRole", "primary")
        lay.addWidget(self._tf_menu_btn)
        lay.addWidget(_gap(4))
        self.timeframe_favorites_layout = QHBoxLayout()
        self.timeframe_favorites_layout.setContentsMargins(0, 0, 0, 0)
        self.timeframe_favorites_layout.setSpacing(2)
        lay.addLayout(self.timeframe_favorites_layout)
        self._tf_fav_buttons: Dict[str, QPushButton] = {}
        self._rebuild_timeframe_favorites_tray()
        lay.addWidget(_gap(4))

        # ── 3. CHART TYPE DROPDOWN ────────────────────────────────────────
        self._ct_menu = QMenu(self)
        self._ct_menu.setObjectName("dropdownMenu")
        self._active_chart_type = "candle"

        # We expose chart_type_combo as hidden shim for backward compat
        self.chart_type_combo = QComboBox()
        self.chart_type_combo.setVisible(False)
        for data, _, label in CHART_TYPES:
            self.chart_type_combo.addItem(label, data)

        ct_action_grp = QActionGroup(self)
        ct_action_grp.setExclusive(True)
        self._ct_actions: Dict[str, QAction] = {}
        glyphs = {data: glyph for data, glyph, _ in CHART_TYPES}
        for data, glyph, label in CHART_TYPES:
            action = QAction(f"{glyph}  {label}", self)
            action.setCheckable(True)
            action.setData(data)
            if data == "candle":
                action.setIcon(_icon("chart_candle"))
                action.setChecked(True)
            action.triggered.connect(lambda _=False, d=data: self._on_chart_type(d))
            ct_action_grp.addAction(action)
            self._ct_menu.addAction(action)
            self._ct_actions[data] = action

        self._ct_menu_btn = QToolButton()
        self._ct_menu_btn.setObjectName("pillMenuBtn")
        _apply_icon(self._ct_menu_btn, "chart_candle", _ICON_SIZE)
        self._ct_menu_btn.setToolTip("Chart type")
        self._ct_menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._ct_menu_btn.setFixedSize(30, _CONTROL_H)
        self._ct_menu_btn.setMenu(self._ct_menu)
        self._ct_menu_btn.setProperty("selectionRole", "primary")
        lay.addWidget(self._ct_menu_btn)
        lay.addWidget(_gap(4))
        lay.addWidget(_vsep())
        lay.addWidget(_gap(4))

        # ── 4. INDICATOR BUTTON ───────────────────────────────────────────

        self.vol_btn = QPushButton()
        self.vol_btn.setVisible(False)
        self.vol_btn.setCheckable(True)
        self.vol_btn.setChecked(True)

        self.indicator_menu_button = QToolButton()
        self.indicator_menu_button.setObjectName("pillMenuBtn")
        _apply_icon(self.indicator_menu_button, "indicator", _ICON_SIZE)
        self.indicator_menu_button.setToolTip("Manage indicators")
        self.indicator_menu_button.setFixedSize(26, _CONTROL_H)
        self.indicator_menu_button.clicked.connect(self.manage_indicators_requested.emit)
        lay.addWidget(self.indicator_menu_button)

        lay.addWidget(_gap(4))
        lay.addWidget(_vsep())
        lay.addWidget(_gap(4))

        # ── 5. DRAWING TOOLS TRAY ─────────────────────────────────────────
        self.drawing_tray = QFrame()
        self.drawing_tray.setObjectName("drawingTray")
        self.drawing_tray.setFixedHeight(22)
        dt_lay = QHBoxLayout(self.drawing_tray)
        dt_lay.setContentsMargins(4, 0, 4, 0)
        dt_lay.setSpacing(2)

        self._drawing_action_group = QActionGroup(self)
        self._drawing_action_group.setExclusive(True)

        self._drawing_menu = QMenu(self)
        self._drawing_menu.setObjectName("dropdownMenu")

        for tool_id, glyph, tip in DRAWING_TOOLS:
            action = QAction(tip, self)
            action.setIcon(_icon(tool_id))
            action.setCheckable(True)
            self._drawing_action_group.addAction(action)
            self._drawing_actions[tool_id] = action

            item = ToolMenuItemWidget(
                tool_id,
                glyph,
                tip,
                tool_id in self._favorite_tools,
                ICON_ASSETS.get(tool_id) and tool_id,
                self,
            )
            self._drawing_menu_items[tool_id] = item
            item.triggered.connect(self._on_drawing_tool_from_menu)
            item.favorite_toggled.connect(self._on_fav_toggled)
            wa = QWidgetAction(self)
            wa.setDefaultWidget(item)
            self._drawing_menu.addAction(wa)

        self.drawing_menu_btn = QToolButton()
        self.drawing_menu_btn.setObjectName("drawMenuBtn")
        _apply_icon(self.drawing_menu_btn, "drawing_menu", _ICON_SIZE)
        self.drawing_menu_btn.setToolTip("Drawing tools")
        self.drawing_menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.drawing_menu_btn.setFixedSize(26, _CONTROL_H)
        self.drawing_menu_btn.setMenu(self._drawing_menu)
        dt_lay.addWidget(self.drawing_menu_btn)

        # Divider before favorites
        fav_div = QFrame()
        fav_div.setFrameShape(QFrame.Shape.VLine)
        fav_div.setFixedSize(1, 11)
        fav_div.setStyleSheet(f"background:{P.BORDER_MID}; border:none;")
        dt_lay.addWidget(fav_div)
        dt_lay.addWidget(_gap(2))

        self.favorites_layout = QHBoxLayout()
        self.favorites_layout.setContentsMargins(0, 0, 0, 0)
        self.favorites_layout.setSpacing(2)
        dt_lay.addLayout(self.favorites_layout)

        self._tool_btn_group = QButtonGroup(self)
        self._tool_btn_group.setExclusive(False)
        self._rebuild_favorites_tray()

        # Divider before measure / color
        dt_lay.addWidget(_gap(2))
        div2 = QFrame()
        div2.setFrameShape(QFrame.Shape.VLine)
        div2.setFixedSize(1, 11)
        div2.setStyleSheet(f"background:{P.BORDER_MID}; border:none;")
        dt_lay.addWidget(div2)
        dt_lay.addWidget(_gap(2))

        self.measure_btn = QPushButton()
        self.measure_btn.setObjectName("toolBtn")
        self.measure_btn.setProperty("toolRole", "measure")
        _apply_icon(self.measure_btn, "measure", _ICON_SIZE)
        self.measure_btn.setFixedSize(22, _CONTROL_H)
        self.measure_btn.setCheckable(True)
        self.measure_btn.setChecked(False)
        self.measure_btn.setToolTip("Measure  [E]")
        dt_lay.addWidget(self.measure_btn)

        div3 = QFrame()
        div3.setFrameShape(QFrame.Shape.VLine)
        div3.setFixedSize(1, 11)
        div3.setStyleSheet(f"background:{P.BORDER_MID}; border:none;")
        dt_lay.addWidget(div3)
        dt_lay.addWidget(_gap(2))

        self.color_btn = QPushButton("●")
        self.color_btn.setObjectName("colorBtn")
        self.color_btn.setFixedSize(22, _CONTROL_H)
        self.color_btn.setToolTip("Drawing color")
        dt_lay.addWidget(self.color_btn)

        self.clear_drawings_btn = QPushButton()
        self.clear_drawings_btn.setObjectName("clearBtn")
        _apply_icon(self.clear_drawings_btn, "clear", _ICON_SIZE)
        self.clear_drawings_btn.setFixedSize(22, _CONTROL_H)
        self.clear_drawings_btn.setToolTip("Clear all drawings")
        dt_lay.addWidget(self.clear_drawings_btn)

        lay.addWidget(self.drawing_tray)

        # ── STRETCH ───────────────────────────────────────────────────────
        lay.addStretch()

        # ── 6. RIGHT UTILITY CLUSTER ──────────────────────────────────────
        # Snapshot
        self.snapshot_btn = self._icon_btn("", "Capture high quality PNG snapshot  [Ctrl+S]", 22, "snapshot")
        lay.addWidget(self.snapshot_btn)
        lay.addWidget(_gap(2))

        lay.addWidget(_vsep())
        lay.addWidget(_gap(4))

        # Autoscale
        self.autoscale_btn = self._icon_btn("", "Auto-scale  [Ctrl+A]", 22, "auto_scale")
        self.autoscale_btn.setCheckable(True)
        self.autoscale_btn.setChecked(False)
        lay.addWidget(self.autoscale_btn)
        lay.addWidget(_gap(2))

        # Refresh
        self.refresh_btn = self._icon_btn("", "Refresh  [F5]", 22, "refresh")
        lay.addWidget(self.refresh_btn)
        lay.addWidget(_gap(2))

        # Settings
        self.settings_btn = self._icon_btn("⚙", "Chart settings", 22, "settings")
        lay.addWidget(self.settings_btn)

        lay.addWidget(_gap(5))
        lay.addWidget(_vsep())
        lay.addWidget(_gap(4))

        # Order button
        self.order_btn = QPushButton()
        self.order_btn.setObjectName("orderBtn")
        _apply_icon(self.order_btn, "order", _ICON_SIZE)
        self.order_btn.setFixedSize(22, _CONTROL_H)
        self.order_btn.setToolTip("Place order  [O]")
        lay.addWidget(self.order_btn)

        # Set defaults
        self.set_timeframe("day")
        self._refresh_color_btn()
        self._apply_utility_controls_visibility()

    @staticmethod
    def _icon_btn(icon: str, tip: str, size: int = 24, icon_key: str = "") -> QPushButton:
        btn = QPushButton(icon)
        if icon_key:
            btn.setText("")
            _apply_icon(btn, icon_key, _ICON_SIZE)
        btn.setObjectName("iconBtn")
        btn.setFixedSize(size, _CONTROL_H)
        btn.setToolTip(tip)
        return btn

    # ═══════════════════════════════════════════════════════════════════════
    # INTERNALS
    # ═══════════════════════════════════════════════════════════════════════

    def _rebuild_favorites_tray(self) -> None:
        while self.favorites_layout.count():
            item = self.favorites_layout.takeAt(0)
            w = item.widget()
            if w:
                if self._tool_btn_group:
                    self._tool_btn_group.removeButton(w)
                w.deleteLater()
        self._tool_buttons.clear()

        glyph_map = {tid: g for tid, g, _ in DRAWING_TOOLS}
        for tool_id in self._favorite_tools:
            glyph = glyph_map.get(tool_id)
            if not glyph:
                continue
            btn = QPushButton(glyph)
            btn.setObjectName("toolBtn")
            icon_key = tool_id if tool_id in ICON_ASSETS else ""
            if icon_key:
                btn.setText("")
                _apply_icon(btn, icon_key, _ICON_SIZE)
            btn.setCheckable(True)
            btn.setFixedSize(22, 18)
            btn.setToolTip(TOOL_DISPLAY.get(tool_id, tool_id))
            if self._drawing_actions.get(tool_id, QAction()).isChecked():
                btn.setChecked(True)
            btn.clicked.connect(
                lambda checked, tid=tool_id: self._on_tray_btn_clicked(tid, checked)
            )
            self._tool_buttons[tool_id] = btn
            if self._tool_btn_group:
                self._tool_btn_group.addButton(btn)
            self.favorites_layout.addWidget(btn)

    def _sync_timeframe_menu_button_text(self) -> None:
        """Keep the timeframe menu from duplicating favorite interval buttons."""
        if not getattr(self, "_tf_menu_btn", None):
            return

        action = self._tf_actions.get(self._active_tf)
        active_label = action.text() if action else str(self._active_tf or "D")
        toolbar_label = _toolbar_timeframe_label(str(self._active_tf or ""), active_label)
        has_favorites = bool(self._favorite_timeframes)

        if has_favorites:
            # Favorite timeframe buttons already show the selected interval.
            # Keep the menu as a compact dropdown-only control to avoid duplication.
            self._tf_menu_btn.setText("▾")
            self._tf_menu_btn.setFixedSize(22, _CONTROL_H)
            self._tf_menu_btn.setToolTip(f"Select timeframe · current {active_label}")
            self._tf_menu_btn.setProperty("favoritesActive", True)
        else:
            # No favorites: the menu button itself must show the selected interval.
            self._tf_menu_btn.setText(toolbar_label)
            self._tf_menu_btn.setFixedSize(30, _CONTROL_H)
            self._tf_menu_btn.setToolTip("Select timeframe")
            self._tf_menu_btn.setProperty("favoritesActive", False)

        self._tf_menu_btn.style().unpolish(self._tf_menu_btn)
        self._tf_menu_btn.style().polish(self._tf_menu_btn)
        self._tf_menu_btn.update()

    def _on_tf_clicked(self, kite_iv: str) -> None:
        self._active_tf = kite_iv
        action = self._tf_actions.get(kite_iv)
        self._sync_timeframe_menu_button_text()
        # Sync hidden shim combo
        for i in range(self.timeframe_dropdown.count()):
            if self.timeframe_dropdown.itemData(i) == kite_iv:
                self.timeframe_dropdown.setCurrentIndex(i)
                break
        for iv, btn in self._tf_fav_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(iv == kite_iv)
            btn.blockSignals(False)
        for iv, item in self._tf_menu_items.items():
            item.set_selected(iv == kite_iv)
        self.timeframe_changed.emit(kite_iv)

    def _on_tf_fav_toggled(self, kite_iv: str, is_fav: bool) -> None:
        if is_fav and kite_iv not in self._favorite_timeframes:
            self._favorite_timeframes.append(kite_iv)
        elif not is_fav and kite_iv in self._favorite_timeframes:
            self._favorite_timeframes.remove(kite_iv)
        self._rebuild_timeframe_favorites_tray()
        self._sync_timeframe_menu_favorites_state()
        self._sync_timeframe_menu_button_text()
        if not self._suppress_pref_events:
            self.toolbar_preferences_changed.emit(self.get_toolbar_preferences())

    def _sync_timeframe_menu_favorites_state(self) -> None:
        favorite_set = set(self._favorite_timeframes)
        for iv, item in self._tf_menu_items.items():
            should_be_checked = iv in favorite_set
            if item.star.isChecked() == should_be_checked:
                continue
            item.star.blockSignals(True)
            item.star.setChecked(should_be_checked)
            item.star.setText("★" if should_be_checked else "☆")
            item.star.blockSignals(False)

    def _rebuild_timeframe_favorites_tray(self) -> None:
        while self.timeframe_favorites_layout.count():
            item = self.timeframe_favorites_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._tf_fav_buttons.clear()

        display_map = {kite_iv: display for display, kite_iv, _ in TIMEFRAMES}
        for kite_iv in self._favorite_timeframes:
            if kite_iv not in self._tf_actions:
                continue
            display = display_map.get(kite_iv, kite_iv)
            btn = QPushButton(_toolbar_timeframe_label(kite_iv, display))
            btn.setObjectName("toolBtn")
            btn.setCheckable(True)
            btn.setFixedSize(26, 18)
            btn.setChecked(kite_iv == self._active_tf)
            btn.clicked.connect(lambda checked, iv=kite_iv: checked and self._on_tf_clicked(iv))
            self._tf_fav_buttons[kite_iv] = btn
            self.timeframe_favorites_layout.addWidget(btn)

        self._sync_timeframe_menu_button_text()

    def _on_chart_type(self, data: str) -> None:
        self._active_chart_type = data
        glyphs = {data: glyph for data, glyph, _ in CHART_TYPES}
        if self._ct_menu_btn:
            if data == "candle":
                self._ct_menu_btn.setText("")
                self._ct_menu_btn.setIcon(_icon("chart_candle"))
            else:
                self._ct_menu_btn.setIcon(QIcon())
                self._ct_menu_btn.setText(glyphs.get(data, "?"))
        # Sync hidden shim
        for i in range(self.chart_type_combo.count()):
            if self.chart_type_combo.itemData(i) == data:
                self.chart_type_combo.setCurrentIndex(i)
                break
        if not self._suppress_pref_events:
            self.toolbar_preferences_changed.emit(self.get_toolbar_preferences())

    def _on_drawing_tool_from_menu(self, tool_id: str) -> None:
        self._drawing_menu.hide()
        action = self._drawing_actions.get(tool_id)
        if action:
            action.trigger()
        self.set_draw_btn_active(tool_id)

    def _on_fav_toggled(self, tool_id: str, is_fav: bool) -> None:
        if is_fav and tool_id not in self._favorite_tools:
            self._favorite_tools.append(tool_id)
        elif not is_fav and tool_id in self._favorite_tools:
            self._favorite_tools.remove(tool_id)
        self._rebuild_favorites_tray()
        self._sync_drawing_menu_favorites_state()
        if not self._suppress_pref_events:
            self.toolbar_preferences_changed.emit(self.get_toolbar_preferences())

    def _sync_drawing_menu_favorites_state(self) -> None:
        favorite_set = set(self._favorite_tools)
        for tool_id, item in self._drawing_menu_items.items():
            should_be_checked = tool_id in favorite_set
            if item.star.isChecked() == should_be_checked:
                continue
            item.star.blockSignals(True)
            item.star.setChecked(should_be_checked)
            item.star.setText("★" if should_be_checked else "☆")
            item.star.blockSignals(False)

    def _sync_drawing_menu_selected_state(self) -> None:
        for tool_id, item in self._drawing_menu_items.items():
            action = self._drawing_actions.get(tool_id)
            item.set_selected(bool(action and action.isChecked()))

    def _on_tray_btn_clicked(self, tool_id: str, checked: bool) -> None:
        if checked:
            action = self._drawing_actions.get(tool_id)
            if action:
                action.trigger()
            self._sync_drawing_menu_selected_state()
        else:
            self.reset_draw_btn()

    def _refresh_color_btn(self) -> None:
        c = self._drawing_color
        self.color_btn.setStyleSheet(
            f"QPushButton#colorBtn{{"
            f"color:{c}; background:transparent;"
            f"border:1px solid transparent; border-radius:2px;"
            f"font-size:12px; font-weight:800; padding:0;"
            f"}}"
            f"QPushButton#colorBtn:hover{{"
            f"background:rgba(90,112,144,0.08); border-color:rgba(90,112,144,0.28);"
            f"}}"
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_utility_controls_visibility()

    def _apply_utility_controls_visibility(self) -> None:
        is_compact = self.width() < self._UTILITY_HIDE_WIDTH
        if self.snapshot_btn:
            self.snapshot_btn.setVisible(self._show_snapshot and not is_compact)
        if self.autoscale_btn:
            self.autoscale_btn.setVisible(self._show_autoscale and not is_compact)
        if self.refresh_btn:
            self.refresh_btn.setVisible(self._show_refresh and not is_compact)

    def set_utility_controls_visibility(
        self,
        *,
        show_snapshot: Optional[bool] = None,
        show_autoscale: Optional[bool] = None,
        show_refresh: Optional[bool] = None,
        emit_change: bool = False,
    ) -> None:
        changed = False
        if show_snapshot is not None and self._show_snapshot != bool(show_snapshot):
            self._show_snapshot = bool(show_snapshot)
            changed = True
        if show_autoscale is not None and self._show_autoscale != bool(show_autoscale):
            self._show_autoscale = bool(show_autoscale)
            changed = True
        if show_refresh is not None and self._show_refresh != bool(show_refresh):
            self._show_refresh = bool(show_refresh)
            changed = True
        self._apply_utility_controls_visibility()
        if emit_change and changed and not self._suppress_pref_events:
            self.toolbar_preferences_changed.emit(self.get_toolbar_preferences())

    # ═══════════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════════════════════

    def set_symbol_text(self, symbol: str, exchange: str = "") -> None:
        _ = exchange  # retained for backward compatibility
        self.symbol_label.setText(symbol)

    def set_timeframe(self, kite_interval: str) -> None:
        action = self._tf_actions.get(kite_interval)
        if action:
            action.setChecked(True)
            self._active_tf = kite_interval
            self._sync_timeframe_menu_button_text()
        for i in range(self.timeframe_dropdown.count()):
            if self.timeframe_dropdown.itemData(i) == kite_interval:
                self.timeframe_dropdown.blockSignals(True)
                self.timeframe_dropdown.setCurrentIndex(i)
                self.timeframe_dropdown.blockSignals(False)
                break
        for iv, btn in self._tf_fav_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(iv == kite_interval)
            btn.blockSignals(False)
        for iv, item in self._tf_menu_items.items():
            item.set_selected(iv == kite_interval)

    def get_timeframe_value(self) -> str:
        return self._active_tf

    def get_chart_type(self) -> str:
        return self._active_chart_type

    def get_indicator_states(self) -> Dict[str, bool]:
        return {k: a.isChecked() for k, a in self._ind_actions.items()}

    def set_indicator_state(self, key: str, checked: bool) -> None:
        a = self._ind_actions.get(key)
        if a:
            a.setChecked(checked)

    def get_drawing_action(self, tool_id: str) -> Optional[QAction]:
        return self._drawing_actions.get(tool_id)

    def get_clear_action(self) -> QAction:
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
        self._sync_drawing_menu_selected_state()

    def reset_draw_btn(self, clear_measure: bool = True) -> None:
        for btn in self._tool_buttons.values():
            btn.setChecked(False)
        if clear_measure and self.measure_btn:
            self.measure_btn.setChecked(False)
        grp = self._drawing_action_group
        if grp:
            grp.setExclusive(False)
            for a in grp.actions():
                a.setChecked(False)
            grp.setExclusive(True)
        self._sync_drawing_menu_selected_state()

    def set_drawing_color(self, color: str) -> None:
        self._drawing_color = color
        self._refresh_color_btn()
        if not self._suppress_pref_events:
            self.toolbar_preferences_changed.emit(self.get_toolbar_preferences())

    def get_drawing_color(self) -> str:
        return self._drawing_color


    def get_toolbar_preferences(self) -> Dict[str, object]:
        return {
            "favorite_tools": list(self._favorite_tools),
            "favorite_timeframes": list(self._favorite_timeframes),
            "chart_type": self._active_chart_type,
            "drawing_color": self._drawing_color,
            "show_snapshot": self._show_snapshot,
            "show_autoscale": self._show_autoscale,
            "show_refresh": self._show_refresh,
        }

    def apply_toolbar_preferences(self, prefs: Optional[Dict[str, object]]) -> None:
        if not isinstance(prefs, dict):
            return

        self._suppress_pref_events = True
        try:
            all_tools = {tid for tid, _, _ in DRAWING_TOOLS}
            favorite_tools = prefs.get("favorite_tools")
            if isinstance(favorite_tools, list):
                filtered = [str(tid) for tid in favorite_tools if str(tid) in all_tools]
                self._favorite_tools = filtered
                self._rebuild_favorites_tray()
                self._sync_drawing_menu_favorites_state()

            all_tfs = {iv for _, iv, _ in TIMEFRAMES}
            favorite_tfs = prefs.get("favorite_timeframes")
            if isinstance(favorite_tfs, list):
                filtered_tfs = [str(iv) for iv in favorite_tfs if str(iv) in all_tfs]
                self._favorite_timeframes = filtered_tfs
                self._rebuild_timeframe_favorites_tray()
                self._sync_timeframe_menu_favorites_state()
                self._sync_timeframe_menu_button_text()

            chart_type = prefs.get("chart_type")
            if isinstance(chart_type, str) and chart_type in self._ct_actions:
                action = self._ct_actions[chart_type]
                action.setChecked(True)
                self._on_chart_type(chart_type)

            drawing_color = prefs.get("drawing_color")
            if isinstance(drawing_color, str) and drawing_color.strip():
                self.set_drawing_color(drawing_color)
            self.set_utility_controls_visibility(
                show_snapshot=prefs.get("show_snapshot") if isinstance(prefs.get("show_snapshot"), bool) else None,
                show_autoscale=prefs.get("show_autoscale") if isinstance(prefs.get("show_autoscale"), bool) else None,
                show_refresh=prefs.get("show_refresh") if isinstance(prefs.get("show_refresh"), bool) else None,
                emit_change=False,
            )
        finally:
            self._suppress_pref_events = False

    def set_data_status(self, status: str, live: bool = True) -> None:
        if self.data_status_label:
            self.data_status_label.setText(status)
            name = "liveBadge" if live else "delayedBadge"
            self.data_status_label.setObjectName(name)
            self.data_status_label.style().unpolish(self.data_status_label)
            self.data_status_label.style().polish(self.data_status_label)

    # ═══════════════════════════════════════════════════════════════════════
    # STYLESHEET
    # ═══════════════════════════════════════════════════════════════════════

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            /* ─ TOOLBAR ROOT ─────────────────────────────────────────── */
            QFrame#chartToolbar {{
                background: {P.BG_BASE};
                border: none;
                border-top: 1px solid {P.BORDER_SOFT};
                border-bottom: 1px solid {P.BORDER};
                min-height: {_TOOLBAR_H}px;
                max-height: {_TOOLBAR_H}px;
            }}

            QWidget {{
                font-family: "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", "Noto Sans", sans-serif;
            }}

            /* ─ SYMBOL BLOCK ──────────────────────────────────────────── */
            QWidget#symbolBlock {{
                background: transparent;
            }}
            QLabel#symbolBadge {{
                color: {P.SYMBOL};
                font-family: "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", "Noto Sans", sans-serif;
                font-size: 11px;
                font-weight: 650;
                letter-spacing: 0.35px;
                padding: 0 5px 0 0;
                background: transparent;
                min-width: 44px;
            }}

            /* ─ GENERIC PILL MENU BUTTON ──────────────────────────────── */
            QToolButton#pillMenuBtn {{
                background: {P.BG_RAISED};
                color: {P.T_MUTED};
                border: 1px solid {P.BORDER};
                border-radius: 2px;
                font-family: "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", sans-serif;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 0.35px;
                padding: 0 4px;
            }}
            QToolButton#pillMenuBtn:hover {{
                background: {P.BG_HOVER};
                color: {P.T_MAIN};
                border-color: {P.BORDER_MID};
            }}
            QToolButton#pillMenuBtn[selectionRole="primary"] {{
                background: {P.BG_SELECTED};
                color: {P.T_MAIN};
                border-color: rgba(90,112,144,0.30);
            }}
            QToolButton#pillMenuBtn[favoritesActive="true"] {{
                min-width: 22px;
                max-width: 22px;
                color: {P.T_MUTED};
                font-size: 11px;
                font-weight: 650;
                padding: 0px;
            }}
            QToolButton#pillMenuBtn[favoritesActive="true"]:hover {{
                color: {P.T_MAIN};
            }}
            QToolButton#pillMenuBtn:pressed, QToolButton#pillMenuBtn:open {{
                background: {P.BG_SELECTED_2};
                color: {P.T_BRIGHT};
                border-color: rgba(154,167,184,0.42);
            }}
            QToolButton#pillMenuBtn::menu-indicator {{
                image: none;
                width: 0;
            }}

            /* ─ DRAWING TRAY ──────────────────────────────────────────── */
            QFrame#drawingTray {{
                background: {P.BG_RAISED};
                border: 1px solid {P.BORDER};
                border-radius: 2px;
            }}

            QToolButton#drawMenuBtn {{
                background: transparent;
                color: {P.ICON_DIM};
                border: none;
                border-radius: 2px;
                font-size: 12px;
                font-weight: 700;
                padding: 0;
            }}
            QToolButton#drawMenuBtn:hover {{
                color: {P.ICON_HOVER};
                background: rgba(90,112,144,0.08);
            }}
            QToolButton#drawMenuBtn:pressed, QToolButton#drawMenuBtn:open {{
                color: {P.ICON_ON};
                background: rgba(90,112,144,0.14);
            }}
            QToolButton#drawMenuBtn::menu-indicator {{
                image: none;
                width: 0;
            }}

            QPushButton#toolBtn {{
                background: transparent;
                color: {P.ICON_DIM};
                border: 1px solid transparent;
                border-radius: 2px;
                font-size: 12px;
                font-weight: 700;
                padding: 0;
            }}
            QPushButton#toolBtn:hover {{
                color: {P.ICON_HOVER};
                background: rgba(90,112,144,0.07);
                border-color: rgba(90,112,144,0.16);
            }}
            QPushButton#toolBtn:checked {{
                color: {P.ICON_ON};
                background: {P.BG_SELECTED_2};
                border: 1px solid rgba(154,167,184,0.34);
            }}

            QPushButton#toolBtn[toolRole="measure"]:checked {{
                color: #b7aa87;
                background: rgba(154,130,78,0.14);
                border: 1px solid rgba(154,130,78,0.36);
            }}

            QPushButton#clearBtn {{
                background: transparent;
                color: {P.ICON_DIM};
                border: 1px solid transparent;
                border-radius: 2px;
                font-size: 10px;
                font-weight: 700;
                padding: 0;
            }}
            QPushButton#clearBtn:hover {{
                color: #9b6670;
                background: rgba(255,77,106,0.07);
                border-color: rgba(255,77,106,0.16);
            }}

            QPushButton#iconBtn {{
                background: transparent;
                color: {P.ICON_DIM};
                border: 1px solid transparent;
                border-radius: 2px;
                font-size: 12px;
                font-weight: 600;
                padding: 0;
            }}
            QPushButton#iconBtn:hover {{
                color: {P.ICON_HOVER};
                background: rgba(90,112,144,0.07);
                border-color: rgba(90,112,144,0.16);
            }}
            QPushButton#iconBtn:pressed {{
                color: {P.ICON_ON};
                background: rgba(90,112,144,0.14);
            }}
            QPushButton#iconBtn:checked {{
                color: {P.ICON_ON};
                background: {P.BG_SELECTED_2};
                border: 1px solid rgba(154,167,184,0.34);
            }}

            QPushButton#orderBtn {{
                background: {P.BG_RAISED};
                color: {P.ICON_DIM};
                border: 1px solid {P.BORDER};
                border-radius: 2px;
                padding: 0;
            }}
            QPushButton#orderBtn:hover {{
                background: {P.BG_HOVER};
                border-color: rgba(90,112,144,0.28);
                color: {P.ICON_HOVER};
            }}
            QPushButton#orderBtn:pressed {{
                background: {P.BG_ACTIVE};
                border-color: rgba(90,112,144,0.40);
            }}

            /* ─ ALL DROPDOWN MENUS ────────────────────────────────────── */
            QMenu#dropdownMenu {{
                background: {P.BG_PANEL};
                border: 1px solid {P.BORDER};
                border-radius: 2px;
                padding: 3px 0;
            }}
            QMenu#dropdownMenu::item {{
                padding: 4px 18px 4px 10px;
                color: {P.T_MUTED};
                font-family: "Inter", "Aptos", "Segoe UI", sans-serif;
                font-size: 10px;
                font-weight: 500;
            }}
            QMenu#dropdownMenu::item:selected {{
                background: {P.BG_SELECTED};
                color: {P.T_MAIN};
            }}
            QMenu#dropdownMenu::item:checked {{
                background: {P.BG_SELECTED_2};
                color: {P.T_BRIGHT};
                font-weight: 650;
            }}
            QMenu#dropdownMenu::separator {{
                height: 1px;
                background: {P.BORDER};
                margin: 2px 8px;
            }}
            QMenu#dropdownMenu::indicator {{
                width: 10px;
                height: 10px;
                border: 1px solid {P.BORDER_MID};
                border-radius: 2px;
                margin-left: 7px;
                background: {P.BG_RAISED};
            }}
            QMenu#dropdownMenu::indicator:checked {{
                background: rgba(154,167,184,0.28);
                border-color: rgba(154,167,184,0.50);
            }}

            QWidget#menuItem:hover {{
                background: {P.BG_SELECTED};
            }}

            QLabel#liveBadge {{
                color: {P.TEAL};
                font-family: "Inter", "Aptos", "Segoe UI", sans-serif;
                font-size: 7px;
                font-weight: 800;
                letter-spacing: 1px;
                padding: 0 4px;
                background: rgba(0,212,168,0.055);
                border: 1px solid rgba(0,212,168,0.14);
                border-radius: 2px;
            }}
            QLabel#delayedBadge {{
                color: {P.AMBER};
                font-family: "Inter", "Aptos", "Segoe UI", sans-serif;
                font-size: 7px;
                font-weight: 800;
                letter-spacing: 1px;
                padding: 0 4px;
                background: rgba(245,158,11,0.055);
                border: 1px solid rgba(245,158,11,0.14);
                border-radius: 2px;
            }}
        """)