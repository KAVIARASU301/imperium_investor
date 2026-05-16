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
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPixmap
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
    # Backgrounds
    BG_BASE   = "#070a0f"   # toolbar root
    BG_RAISED = "#0d1219"   # pill / badge backgrounds
    BG_HOVER  = "#111a26"   # hover state
    BG_ACTIVE = "#0a1828"   # selected / checked

    # Borders
    BORDER      = "#1a2535"
    BORDER_MID  = "#243040"
    BORDER_LIVE = "#00d4ff"  # cyan accent border

    # Text
    T_DIM    = "#2e4060"
    T_MID    = "#4a6280"
    T_MUTED  = "#7a94b0"
    T_MAIN   = "#b8cce0"
    T_BRIGHT = "#ddeeff"

    # Accents
    CYAN   = "#00d4ff"
    TEAL   = "#22d3a0"
    AMBER  = "#fbbf24"
    RED    = "#ff4d6a"
    PURPLE = "#a78bfa"


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


def _icon(icon_key: str) -> QIcon:
    """Return a bundled toolbar SVG icon, or an empty icon for unknown keys."""
    asset_name = ICON_ASSETS.get(icon_key)
    if not asset_name:
        return QIcon()
    return QIcon(resource_path(f"assets/icons/{asset_name}"))


def _icon_pixmap(icon_key: str, size: int = 16) -> QPixmap:
    return _icon(icon_key).pixmap(QSize(size, size))


def _apply_icon(button, icon_key: str, size: int = 16) -> None:
    button.setIcon(_icon(icon_key))
    button.setIconSize(QSize(size, size))



# ─── Drawing tool menu row ────────────────────────────────────────────────────

class ToolMenuItemWidget(QWidget):
    triggered        = Signal(str)
    favorite_toggled = Signal(str, bool)

    def __init__(self, tool_id: str, glyph: str, label: str,
                 is_fav: bool, icon_key: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.tool_id = tool_id
        self.setObjectName("menuItem")
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 10, 0)
        lay.setSpacing(10)

        icon = QLabel(glyph)
        icon.setFixedSize(18, 18)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            "font-size:14px; color:#7090b0; background:transparent;"
            "font-family:'Segoe UI Symbol','Noto Sans Symbols',sans-serif;"
        )
        if icon_key:
            icon.setText("")
            icon.setPixmap(_icon_pixmap(icon_key, 16))

        text = QLabel(label)
        text.setStyleSheet(
            "font-size:11px; color:#a8bed4; font-weight:500; background:transparent;"
        )

        self.star = QPushButton("★" if is_fav else "☆")
        self.star.setCheckable(True)
        self.star.setChecked(is_fav)
        self.star.setFixedSize(22, 22)
        self.star.setObjectName("starBtn")
        self.star.setCursor(Qt.CursorShape.PointingHandCursor)
        self.star.setStyleSheet(
            "QPushButton#starBtn{color:#2a3d55;background:transparent;border:none;font-size:14px;}"
            "QPushButton#starBtn:hover{color:#5a7a99;}"
            "QPushButton#starBtn:checked{color:#fbbf24;}"
        )
        self.star.toggled.connect(
            lambda c: (self.star.setText("★" if c else "☆"),
                       self.favorite_toggled.emit(self.tool_id, c))
        )

        lay.addWidget(icon)
        lay.addWidget(text)
        lay.addStretch()
        lay.addWidget(self.star)

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
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self._selected = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 10, 0)
        lay.setSpacing(10)

        self.tf_label = QLabel(label)
        self.full_label = QLabel(tooltip.split("  [")[0])
        self._refresh_selected_style()

        self.star = QPushButton("★" if is_fav else "☆")
        self.star.setCheckable(True)
        self.star.setChecked(is_fav)
        self.star.setFixedSize(22, 22)
        self.star.setObjectName("starBtn")
        self.star.setCursor(Qt.CursorShape.PointingHandCursor)
        self.star.setStyleSheet(
            "QPushButton#starBtn{color:#2a3d55;background:transparent;border:none;font-size:14px;}"
            "QPushButton#starBtn:hover{color:#5a7a99;}"
            "QPushButton#starBtn:checked{color:#fbbf24;}"
        )
        self.star.toggled.connect(
            lambda c: (self.star.setText("★" if c else "☆"),
                       self.favorite_toggled.emit(self.kite_interval, c))
        )

        lay.addWidget(self.tf_label)
        lay.addWidget(self.full_label)
        lay.addStretch()
        lay.addWidget(self.star)

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self._refresh_selected_style()

    def _refresh_selected_style(self) -> None:
        if self._selected:
            self.tf_label.setStyleSheet("font-size:12px; color:#d6edff; font-weight:800; background:transparent;")
            self.full_label.setStyleSheet("font-size:11px; color:#8fc8ff; background:transparent;")
        else:
            self.tf_label.setStyleSheet("font-size:12px; color:#a8bed4; font-weight:700; background:transparent;")
            self.full_label.setStyleSheet("font-size:11px; color:#7f9bb8; background:transparent;")

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chartToolbar")
        self.setFixedHeight(32)

        # ── State ─────────────────────────────────────────────────────────
        self._drawing_color = "#FFD700"
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
        self._favorite_tools = ["line", "horizontal_line", "note"]
        self._favorite_timeframes = ["minute", "5minute", "day"]
        self._tf_menu_items: Dict[str, TimeframeMenuItemWidget] = {}

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
        lay.setContentsMargins(8, 0, 8, 0)
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
        lay.addWidget(_gap(6))

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
        self._tf_menu_btn.setFixedSize(36, 22)
        self._tf_menu_btn.setMenu(self._tf_menu)
        lay.addWidget(self._tf_menu_btn)
        lay.addWidget(_gap(4))
        self.timeframe_favorites_layout = QHBoxLayout()
        self.timeframe_favorites_layout.setContentsMargins(0, 0, 0, 0)
        self.timeframe_favorites_layout.setSpacing(2)
        lay.addLayout(self.timeframe_favorites_layout)
        self._tf_fav_buttons: Dict[str, QPushButton] = {}
        self._rebuild_timeframe_favorites_tray()
        lay.addWidget(_gap(6))

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
        _apply_icon(self._ct_menu_btn, "chart_candle")
        self._ct_menu_btn.setToolTip("Chart type")
        self._ct_menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._ct_menu_btn.setFixedSize(36, 22)
        self._ct_menu_btn.setMenu(self._ct_menu)
        lay.addWidget(self._ct_menu_btn)
        lay.addWidget(_gap(6))
        lay.addWidget(_vsep())
        lay.addWidget(_gap(6))

        # ── 4. INDICATOR DROPDOWN ─────────────────────────────────────────
        self._indicator_menu = QMenu(self)
        self._indicator_menu.setObjectName("dropdownMenu")

        # Intentionally keep the indicators menu empty.
        self.vol_btn = QPushButton()
        self.vol_btn.setVisible(False)
        self.vol_btn.setCheckable(True)
        self.vol_btn.setChecked(True)

        self.indicator_menu_button = QToolButton()
        self.indicator_menu_button.setObjectName("pillMenuBtn")
        _apply_icon(self.indicator_menu_button, "indicator")
        self.indicator_menu_button.setToolTip("Toggle chart indicators")
        self.indicator_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.indicator_menu_button.setFixedSize(32, 22)
        self.indicator_menu_button.setMenu(self._indicator_menu)
        lay.addWidget(self.indicator_menu_button)

        lay.addWidget(_gap(6))
        lay.addWidget(_vsep())
        lay.addWidget(_gap(6))

        # ── 5. DRAWING TOOLS TRAY ─────────────────────────────────────────
        self.drawing_tray = QFrame()
        self.drawing_tray.setObjectName("drawingTray")
        self.drawing_tray.setFixedHeight(26)
        dt_lay = QHBoxLayout(self.drawing_tray)
        dt_lay.setContentsMargins(6, 0, 6, 0)
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
            item.triggered.connect(self._on_drawing_tool_from_menu)
            item.favorite_toggled.connect(self._on_fav_toggled)
            wa = QWidgetAction(self)
            wa.setDefaultWidget(item)
            self._drawing_menu.addAction(wa)

        self.drawing_menu_btn = QToolButton()
        self.drawing_menu_btn.setObjectName("drawMenuBtn")
        _apply_icon(self.drawing_menu_btn, "drawing_menu")
        self.drawing_menu_btn.setToolTip("Drawing tools")
        self.drawing_menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.drawing_menu_btn.setFixedSize(32, 22)
        self.drawing_menu_btn.setMenu(self._drawing_menu)
        dt_lay.addWidget(self.drawing_menu_btn)

        # Divider before favorites
        fav_div = QFrame()
        fav_div.setFrameShape(QFrame.Shape.VLine)
        fav_div.setFixedSize(1, 12)
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
        div2.setFixedSize(1, 12)
        div2.setStyleSheet(f"background:{P.BORDER_MID}; border:none;")
        dt_lay.addWidget(div2)
        dt_lay.addWidget(_gap(2))

        self.measure_btn = QPushButton()
        self.measure_btn.setObjectName("toolBtn")
        self.measure_btn.setProperty("toolRole", "measure")
        _apply_icon(self.measure_btn, "measure", 16)
        self.measure_btn.setFixedSize(28, 22)
        self.measure_btn.setCheckable(True)
        self.measure_btn.setToolTip("Measure  [E]")
        dt_lay.addWidget(self.measure_btn)

        div3 = QFrame()
        div3.setFrameShape(QFrame.Shape.VLine)
        div3.setFixedSize(1, 12)
        div3.setStyleSheet(f"background:{P.BORDER_MID}; border:none;")
        dt_lay.addWidget(div3)
        dt_lay.addWidget(_gap(2))

        self.color_btn = QPushButton("●")
        self.color_btn.setObjectName("colorBtn")
        self.color_btn.setFixedSize(28, 22)
        self.color_btn.setToolTip("Drawing color")
        dt_lay.addWidget(self.color_btn)

        self.clear_drawings_btn = QPushButton()
        self.clear_drawings_btn.setObjectName("clearBtn")
        _apply_icon(self.clear_drawings_btn, "clear", 16)
        self.clear_drawings_btn.setFixedSize(28, 22)
        self.clear_drawings_btn.setToolTip("Clear all drawings")
        dt_lay.addWidget(self.clear_drawings_btn)

        lay.addWidget(self.drawing_tray)

        # ── STRETCH ───────────────────────────────────────────────────────
        lay.addStretch()

        # ── 6. RIGHT UTILITY CLUSTER ──────────────────────────────────────
        # Snapshot
        self.snapshot_btn = self._icon_btn("", "Capture high quality PNG snapshot  [Ctrl+S]", 28, "snapshot")
        lay.addWidget(self.snapshot_btn)
        lay.addWidget(_gap(2))

        lay.addWidget(_vsep())
        lay.addWidget(_gap(4))

        # Autoscale
        self.autoscale_btn = self._icon_btn("", "Auto-scale  [Ctrl+A]", 28, "auto_scale")
        self.autoscale_btn.setCheckable(True)
        self.autoscale_btn.setChecked(False)
        lay.addWidget(self.autoscale_btn)
        lay.addWidget(_gap(2))

        # Refresh
        self.refresh_btn = self._icon_btn("", "Refresh  [F5]", 28, "refresh")
        lay.addWidget(self.refresh_btn)
        lay.addWidget(_gap(2))

        # Settings
        self.settings_btn = self._icon_btn("⚙", "Chart settings", 28, "settings")
        lay.addWidget(self.settings_btn)

        lay.addWidget(_gap(8))
        lay.addWidget(_vsep())
        lay.addWidget(_gap(6))

        # Order button
        self.order_btn = QPushButton()
        self.order_btn.setObjectName("orderBtn")
        _apply_icon(self.order_btn, "order", 16)
        self.order_btn.setFixedSize(24, 24)
        self.order_btn.setToolTip("Place order  [O]")
        lay.addWidget(self.order_btn)

        # Set defaults
        self.set_timeframe("day")
        self._refresh_color_btn()

    @staticmethod
    def _icon_btn(icon: str, tip: str, size: int = 28, icon_key: str = "") -> QPushButton:
        btn = QPushButton(icon)
        if icon_key:
            btn.setText("")
            _apply_icon(btn, icon_key, 16)
        btn.setObjectName("iconBtn")
        btn.setFixedSize(size, 22)
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
                _apply_icon(btn, icon_key, 16)
            btn.setCheckable(True)
            btn.setFixedSize(28, 22)
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

    def _on_tf_clicked(self, kite_iv: str) -> None:
        self._active_tf = kite_iv
        action = self._tf_actions.get(kite_iv)
        if action and self._tf_menu_btn:
            self._tf_menu_btn.setText(action.text())
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
        if not self._suppress_pref_events:
            self.toolbar_preferences_changed.emit(self.get_toolbar_preferences())

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
            btn = QPushButton(display_map.get(kite_iv, kite_iv))
            btn.setObjectName("toolBtn")
            btn.setCheckable(True)
            btn.setFixedSize(30, 22)
            btn.setChecked(kite_iv == self._active_tf)
            btn.clicked.connect(lambda checked, iv=kite_iv: checked and self._on_tf_clicked(iv))
            self._tf_fav_buttons[kite_iv] = btn
            self.timeframe_favorites_layout.addWidget(btn)

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
        if not self._suppress_pref_events:
            self.toolbar_preferences_changed.emit(self.get_toolbar_preferences())

    def _on_tray_btn_clicked(self, tool_id: str, checked: bool) -> None:
        if checked:
            action = self._drawing_actions.get(tool_id)
            if action:
                action.trigger()
        else:
            self.reset_draw_btn()

    def _refresh_color_btn(self) -> None:
        c = self._drawing_color
        self.color_btn.setStyleSheet(
            f"QPushButton#colorBtn{{"
            f"color:{c}; background:transparent;"
            f"border:1px solid transparent; border-radius:3px;"
            f"font-size:16px; font-weight:900; padding:0;"
            f"}}"
            f"QPushButton#colorBtn:hover{{"
            f"background:transparent; border-color:rgba(255,255,255,0.25);"
            f"}}"
        )

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
            if self._tf_menu_btn:
                self._tf_menu_btn.setText(action.text())
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

            all_tfs = {iv for _, iv, _ in TIMEFRAMES}
            favorite_tfs = prefs.get("favorite_timeframes")
            if isinstance(favorite_tfs, list):
                filtered_tfs = [str(iv) for iv in favorite_tfs if str(iv) in all_tfs]
                self._favorite_timeframes = filtered_tfs
                self._rebuild_timeframe_favorites_tray()

            chart_type = prefs.get("chart_type")
            if isinstance(chart_type, str) and chart_type in self._ct_actions:
                action = self._ct_actions[chart_type]
                action.setChecked(True)
                self._on_chart_type(chart_type)

            drawing_color = prefs.get("drawing_color")
            if isinstance(drawing_color, str) and drawing_color.strip():
                self.set_drawing_color(drawing_color)
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
                border: 1px solid rgba(255,255,255,0.18);
                min-height: 32px;
                max-height: 32px;
            }}

            /* ─ SYMBOL BLOCK ──────────────────────────────────────────── */
            QWidget#symbolBlock {{
                background: transparent;
            }}
            QLabel#symbolBadge {{
                color: #b7cff8;
                font-family: "Inter", "Segoe UI", "SF Pro Text", "Helvetica Neue", sans-serif;
                font-size: 13px;
                font-weight: 700;
                letter-spacing: 0.2px;
                padding: 0 6px 0 0;
                background: transparent;
                min-width: 50px;
            }}

            /* ─ GENERIC PILL MENU BUTTON ──────────────────────────────── */
            QToolButton#pillMenuBtn {{
                background: {P.BG_RAISED};
                color: {P.T_MUTED};
                border: 1px solid {P.BORDER};
                border-radius: 3px;
                font-family: "JetBrains Mono", "Consolas", monospace;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.5px;
                padding: 0 6px;
            }}
            QToolButton#pillMenuBtn:hover {{
                background: {P.BG_HOVER};
                color: {P.T_MAIN};
                border-color: {P.BORDER_MID};
            }}
            QToolButton#pillMenuBtn:pressed, QToolButton#pillMenuBtn:open {{
                background: {P.BG_ACTIVE};
                color: {P.CYAN};
                border-color: rgba(0,212,255,0.35);
            }}
            QToolButton#pillMenuBtn::menu-indicator {{
                image: none;
                width: 0;
            }}

            /* ─ DRAWING TRAY ──────────────────────────────────────────── */
            QFrame#drawingTray {{
                background: rgba(255,255,255,0.018);
                border: 1px solid {P.BORDER};
                border-radius: 4px;
            }}

            /* Drawing menu open button */
            QToolButton#drawMenuBtn {{
                background: transparent;
                color: {P.T_MUTED};
                border: none;
                font-size: 14px;
                font-weight: 900;
                padding: 0;
            }}
            QToolButton#drawMenuBtn:hover {{
                color: {P.T_BRIGHT};
                background: rgba(255,255,255,0.06);
                border-radius: 3px;
            }}
            QToolButton#drawMenuBtn:pressed, QToolButton#drawMenuBtn:open {{
                color: {P.CYAN};
                background: rgba(0,212,255,0.10);
            }}
            QToolButton#drawMenuBtn::menu-indicator {{
                image: none;
                width: 0;
            }}

            /* Individual tool pin buttons */
            QPushButton#toolBtn {{
                background: transparent;
                color: {P.T_DIM};
                border: none;
                border-radius: 3px;
                font-size: 14px;
                font-weight: 900;
                padding: 0;
            }}
            QPushButton#toolBtn:hover {{
                color: {P.T_MAIN};
                background: rgba(255,255,255,0.07);
            }}
            QPushButton#toolBtn:checked {{
                color: {P.CYAN};
                background: rgba(0,212,255,0.12);
                border-left: 2px solid {P.CYAN};
            }}

            /* Measure button */
            QPushButton#toolBtn[toolRole="measure"]:checked {{
                color: {P.AMBER};
                background: rgba(251,191,36,0.10);
                border-left: 2px solid {P.AMBER};
            }}

            /* ─ COLOR BUTTON — inline style drives this, see _refresh_color_btn ─ */

            /* ─ CLEAR BUTTON ──────────────────────────────────────────── */
            QPushButton#clearBtn {{
                background: transparent;
                color: {P.T_DIM};
                border: none;
                border-radius: 3px;
                font-size: 10px;
                font-weight: 900;
            }}
            QPushButton#clearBtn:hover {{
                color: {P.RED};
                background: rgba(255,77,106,0.10);
            }}

            /* ─ ICON BUTTONS (right cluster) ──────────────────────────── */
            QPushButton#iconBtn {{
                background: transparent;
                color: {P.T_DIM};
                border: none;
                border-radius: 3px;
                font-size: 14px;
                font-weight: 700;
                padding: 0;
            }}
            QPushButton#iconBtn:hover {{
                color: {P.T_MAIN};
                background: rgba(255,255,255,0.07);
            }}
            QPushButton#iconBtn:pressed {{
                color: {P.CYAN};
                background: rgba(0,212,255,0.10);
            }}
            QPushButton#iconBtn:checked {{
                color: {P.CYAN};
                background: rgba(0,212,255,0.14);
                border: 1px solid rgba(0,212,255,0.40);
            }}

            /* ─ ORDER BUTTON ──────────────────────────────────────────── */
            QPushButton#orderBtn {{
                background: rgba(0,212,255,0.08);
                color: #5ddeff;
                border: 1px solid rgba(0,212,255,0.16);
                border-radius: 1px;
                padding: 0;
            }}
            QPushButton#orderBtn:hover {{
                background: rgba(0,212,255,0.16);
                border-color: rgba(0,212,255,0.34);
                color: #9aedff;
            }}
            QPushButton#orderBtn:pressed {{
                background: rgba(0,212,255,0.22);
                border-color: rgba(0,212,255,0.42);
            }}

            /* ─ ALL DROPDOWN MENUS ────────────────────────────────────── */
            QMenu#dropdownMenu {{
                background: #0c1220;
                border: 1px solid {P.BORDER_MID};
                border-radius: 6px;
                padding: 5px 0;
            }}
            QMenu#dropdownMenu::item {{
                padding: 6px 22px 6px 14px;
                color: {P.T_MUTED};
                font-family: -apple-system, "Segoe UI", sans-serif;
                font-size: 11px;
                font-weight: 500;
            }}
            QMenu#dropdownMenu::item:selected {{
                background: rgba(0,212,255,0.10);
                color: {P.CYAN};
                border-left: 2px solid {P.CYAN};
            }}
            QMenu#dropdownMenu::item:checked {{
                color: {P.CYAN};
                font-weight: 700;
            }}
            QMenu#dropdownMenu::separator {{
                height: 1px;
                background: {P.BORDER};
                margin: 3px 10px;
            }}
            QMenu#dropdownMenu::indicator {{
                width: 12px;
                height: 12px;
                border: 1px solid {P.BORDER_MID};
                border-radius: 2px;
                margin-left: 8px;
                background: {P.BG_RAISED};
            }}
            QMenu#dropdownMenu::indicator:checked {{
                background: rgba(0,212,255,0.25);
                border-color: rgba(0,212,255,0.60);
            }}

            /* ─ TOOL MENU ROW HOVER ───────────────────────────────────── */
            QWidget#menuItem:hover {{
                background: rgba(0,212,255,0.06);
            }}

            /* ─ LIVE / DELAYED BADGE ──────────────────────────────────── */
            QLabel#liveBadge {{
                color: {P.TEAL};
                font-family: "JetBrains Mono","Consolas",monospace;
                font-size: 7px;
                font-weight: 900;
                letter-spacing: 1.5px;
                padding: 0 5px;
                background: rgba(34,211,160,0.07);
                border: 1px solid rgba(34,211,160,0.22);
                border-radius: 2px;
            }}
            QLabel#delayedBadge {{
                color: {P.AMBER};
                font-family: "JetBrains Mono","Consolas",monospace;
                font-size: 7px;
                font-weight: 900;
                letter-spacing: 1.5px;
                padding: 0 5px;
                background: rgba(251,191,36,0.07);
                border: 1px solid rgba(251,191,36,0.22);
                border-radius: 2px;
            }}
        """)
