from typing import Dict, Any

from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QPushButton,
    QColorDialog,
    QDialogButtonBox,
    QCheckBox,
    QGroupBox,
    QTabWidget,
    QWidget,
    QFrame,
    QLabel,
    QApplication,
    QLineEdit,
)
from PySide6.QtGui import QColor, QMouseEvent, QCursor


# ─────────────────────────────────────────────────────────────────────────────
#  PALETTE & TYPOGRAPHY (TC2000 Institutional Dark)
# ─────────────────────────────────────────────────────────────────────────────
class P:
    BG0 = "#000000"  # OLED Black app shell
    BG1 = "#0a0c10"  # Deep charcoal for dialog body
    BG2 = "#1c212b"  # Selected segments / hover
    BG3 = "#11141a"  # Input background
    BORDER = "#1f2530"  # Sharp inner divisions
    BORDER2 = "#2a3241"  # Accent borders
    T0 = "#ffffff"  # Pure white primary text
    T1 = "#a5b0c2"  # Muted silver labels
    T2 = "#67758d"  # Darker for table headers
    BLUE = "#2979ff"  # Focus / Selection


FONT_UI = "Inter, 'Segoe UI', Arial, sans-serif"
FONT_MONO = "Consolas, 'Roboto Mono', 'Courier New', monospace"


# ─────────────────────────────────────────────────────────────────────────────
#  SMALL REUSABLE WIDGETS
# ─────────────────────────────────────────────────────────────────────────────

class _Label(QLabel):
    def __init__(self, text="", color=P.T1, size=10, bold=False, mono=False, parent=None):
        super().__init__(text, parent)
        w = "700" if bold else "500"
        font_family = FONT_MONO if mono else FONT_UI
        self.setStyleSheet(
            f"color:{color};font-family:{font_family};"
            f"font-size:{size}px;font-weight:{w};background:transparent;"
        )


class _Toggle(QCheckBox):
    """Sharp terminal toggle."""

    def __init__(self, label="", parent=None):
        super().__init__(label, parent)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setStyleSheet(f"""
            QCheckBox {{
                color:{P.T1}; spacing:8px;
                font-family:{FONT_UI}; font-size:11px; font-weight:700; letter-spacing:0.5px;
                background:transparent;
            }}
            QCheckBox::indicator {{
                width:14px; height:14px;
                border-radius:1px; background:{P.BG3}; border:1px solid {P.BORDER2};
            }}
            QCheckBox::indicator:checked {{
                background:{P.BLUE}; border:1px solid {P.BLUE};
            }}
        """)


class ColorSettingsDialog(QDialog):
    """
    Institutional Color Settings panel.
    Strict TC2000 dark mode aesthetic, frameless drag, sharp borders.
    """

    DEFAULT_BULL_CANDLE_COLOR = "#00C896"
    DEFAULT_BEAR_CANDLE_COLOR = "#E84060"

    def __init__(self, current_theme: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumWidth(450)
        self.setModal(True)

        self._theme = current_theme
        self._buttons: Dict[str, QPushButton] = {}
        self._drag_offset = QPoint()
        self._drag_active = False

        self._setup_ui()
        self._apply_global_styles()
        self._sync_linked_state(self.link_checkbox.isChecked())

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._container = QFrame()
        self._container.setObjectName("dialogContainer")
        outer.addWidget(self._container)

        root = QVBoxLayout(self._container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        # Main Body
        body_widget = QWidget()
        body_layout = QVBoxLayout(body_widget)
        body_layout.setContentsMargins(14, 14, 14, 14)
        body_layout.setSpacing(12)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("customTabs")

        # --- TAB: COLORS ---
        colors_tab = QWidget()
        colors_layout = QVBoxLayout(colors_tab)
        colors_layout.setContentsMargins(0, 12, 0, 0)
        colors_layout.setSpacing(12)

        self.link_checkbox = _Toggle("LINK GREEN/RED ACROSS CANDLES, VOLUME, TABLES")
        self.link_checkbox.setChecked(bool(self._theme.get("link_all_sections", True)))
        self.link_checkbox.toggled.connect(self._sync_linked_state)
        colors_layout.addWidget(self.link_checkbox)

        self.table_color_toggle_checkbox = _Toggle("ENABLE DIRECTIONAL COLORS IN DATA TABLES")
        self.table_color_toggle_checkbox.setChecked(bool(self._theme.get("enable_table_directional_colors", False)))
        colors_layout.addWidget(self.table_color_toggle_checkbox)

        colors_layout.addWidget(self._build_group("CANDLES", [
            ("GREEN CANDLE", "candles.up", self._theme["candles"]["up"]),
            ("RED CANDLE", "candles.down", self._theme["candles"]["down"])
        ]))

        colors_layout.addWidget(self._build_group("VOLUME", [
            ("UP VOLUME", "volume.up", self._theme["volume"]["up"]),
            ("DOWN VOLUME", "volume.down", self._theme["volume"]["down"])
        ]))

        colors_layout.addWidget(self._build_group("SCANNER / WATCHLIST / POSITIONS", [
            ("POSITIVE", "tables.positive", self._theme["tables"]["positive"]),
            ("NEGATIVE", "tables.negative", self._theme["tables"]["negative"]),
            ("NEUTRAL", "tables.neutral", self._theme["tables"]["neutral"]),
            ("VOLUME TEXT", "tables.volume", self._theme["tables"]["volume"])
        ]))

        self.tabs.addTab(colors_tab, "COLORS")

        # --- TAB: MORE ---
        more_tab = QWidget()
        more_layout = QVBoxLayout(more_tab)
        more_layout.setContentsMargins(0, 12, 0, 0)

        self.volume_strength_toggle_checkbox = _Toggle("SHOW VOLUME STRENGTH PROGRESS BAR")
        self.volume_strength_toggle_checkbox.setChecked(
            bool(self._theme.get("enable_volume_strength_indicator", False)))
        more_layout.addWidget(self.volume_strength_toggle_checkbox)

        self.show_table_vertical_lines_checkbox = _Toggle("SHOW LIGHT VERTICAL COLUMN LINES IN TABLES")
        self.show_table_vertical_lines_checkbox.setChecked(
            bool(self._theme.get("show_table_vertical_lines", False))
        )
        more_layout.addWidget(self.show_table_vertical_lines_checkbox)

        self.show_scanner_volume_checkbox = _Toggle("SHOW VOLUME COLUMN IN SCANNER TABLE")
        self.show_scanner_volume_checkbox.setChecked(
            bool(self._theme.get("show_scanner_volume_column", False))
        )
        more_layout.addWidget(self.show_scanner_volume_checkbox)

        self.show_watchlist_volume_checkbox = _Toggle("SHOW VOLUME COLUMN IN WATCHLIST TABLE")
        self.show_watchlist_volume_checkbox.setChecked(
            bool(self._theme.get("show_watchlist_volume_column", False))
        )
        more_layout.addWidget(self.show_watchlist_volume_checkbox)

        self.scanner_live_ticks_checkbox = _Toggle("PASS LIVE TICK DATA TO SCANNER TABLE")
        self.scanner_live_ticks_checkbox.setChecked(
            bool(self._theme.get("scanner_live_ticks", False))
        )
        more_layout.addWidget(self.scanner_live_ticks_checkbox)

        self.status_bar_align_right_checkbox = _Toggle("ALIGN STATUS BAR ELEMENTS TO RIGHT")
        self.status_bar_align_right_checkbox.setChecked(
            str(self._theme.get("status_bar_alignment", "left")).lower() == "right"
        )
        more_layout.addWidget(self.status_bar_align_right_checkbox)

        self.status_pnl_exposure_right_checkbox = _Toggle("KEEP OPEN P&L + EXPOSURE ON RIGHT")
        self.status_pnl_exposure_right_checkbox.setChecked(
            bool(self._theme.get("status_bar_metrics_right", True))
        )
        more_layout.addWidget(self.status_pnl_exposure_right_checkbox)

        self.dual_chart_mode_checkbox = _Toggle("ENABLE DUAL CHART MODE")
        self.dual_chart_mode_checkbox.setChecked(
            bool(self._theme.get("dual_chart_mode", False))
        )
        more_layout.addWidget(self.dual_chart_mode_checkbox)

        account_group = QGroupBox("ACCOUNT HEADER")
        account_layout = QVBoxLayout(account_group)
        account_layout.setContentsMargins(12, 16, 12, 12)
        account_layout.setSpacing(8)

        self.show_account_name_checkbox = _Toggle("SHOW ACCOUNT NAME")
        self.show_account_name_checkbox.setChecked(bool(self._theme.get("show_account_name", True)))
        account_layout.addWidget(self.show_account_name_checkbox)

        self.show_account_balance_checkbox = _Toggle("SHOW ACCOUNT BALANCE")
        self.show_account_balance_checkbox.setChecked(bool(self._theme.get("show_account_balance", True)))
        account_layout.addWidget(self.show_account_balance_checkbox)

        username_row = QHBoxLayout()
        username_label = _Label("PREFERRED USERNAME", color=P.T1, size=10, bold=True)
        self.preferred_username_input = QLineEdit()
        self.preferred_username_input.setPlaceholderText("Leave blank to use profile ID")
        self.preferred_username_input.setText(str(self._theme.get("preferred_username", "")))
        self.preferred_username_input.setClearButtonEnabled(True)
        self.preferred_username_input.setMaxLength(40)
        username_row.addWidget(username_label)
        username_row.addWidget(self.preferred_username_input)
        account_layout.addLayout(username_row)

        more_layout.addWidget(account_group)
        more_layout.addStretch()

        self.tabs.addTab(more_tab, "ADVANCED")

        body_layout.addWidget(self.tabs)

        # Bottom Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 8, 0, 0)
        btn_layout.addStretch()

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setObjectName("actionBtnSecondary")
        cancel_btn.setCursor(QCursor(Qt.PointingHandCursor))
        cancel_btn.clicked.connect(self.reject)

        reset_btn = QPushButton("RESET DEFAULT CANDLES")
        reset_btn.setObjectName("actionBtnSecondary")
        reset_btn.setCursor(QCursor(Qt.PointingHandCursor))
        reset_btn.clicked.connect(self._reset_default_candle_colors)

        save_btn = QPushButton("APPLY SETTINGS")
        save_btn.setObjectName("actionBtnPrimary")
        save_btn.setCursor(QCursor(Qt.PointingHandCursor))
        save_btn.clicked.connect(self.accept)

        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(reset_btn)
        btn_layout.addWidget(save_btn)
        body_layout.addLayout(btn_layout)

        root.addWidget(body_widget)

    def _build_header(self) -> QFrame:
        f = QFrame()
        f.setObjectName("header")
        f.setFixedHeight(40)
        h = QHBoxLayout(f)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(12)

        h.addWidget(_Label("COLOR CONFIGURATION", P.T0, 12, bold=True))
        h.addStretch()

        self._close_btn = QPushButton("✕")
        self._close_btn.setFixedSize(24, 24)
        self._close_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._close_btn.clicked.connect(self.reject)
        self._close_btn.setStyleSheet(f"""
            QPushButton {{ background:transparent; color:{P.T1}; font-size:16px; border:none; font-weight:bold; }}
            QPushButton:hover {{ color:{P.T0}; }}
        """)
        h.addWidget(self._close_btn)
        return f

    def _build_group(self, title: str, items: list) -> QGroupBox:
        group = QGroupBox(title)
        form = QFormLayout(group)
        form.setContentsMargins(12, 16, 12, 12)
        form.setVerticalSpacing(8)

        for label_text, key, initial_val in items:
            lbl = _Label(label_text, P.T1, 10, bold=True)
            btn = self._build_color_button(key, initial_val)
            form.addRow(lbl, btn)

        return group

    def _build_color_button(self, key: str, value: str) -> QPushButton:
        btn = QPushButton()
        btn.setCursor(QCursor(Qt.PointingHandCursor))
        btn.setFixedHeight(24)
        btn.setFixedWidth(100)
        btn.clicked.connect(lambda: self._pick_color(key))
        self._buttons[key] = btn
        self._set_button_color(btn, value)
        return btn

    def _set_button_color(self, button: QPushButton, color_hex: str):
        # Calculate contrast for text
        color = QColor(color_hex)
        luminance = (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()) / 255
        text_color = "#000000" if luminance > 0.5 else "#ffffff"

        button.setText(color_hex.upper())
        button.setStyleSheet(f"""
            QPushButton {{
                background-color: {color_hex};
                color: {text_color};
                border: 1px solid {P.BORDER};
                border-radius: 1px;
                font-family: {FONT_MONO};
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton:hover {{ border: 1px solid {P.T0}; }}
            QPushButton:disabled {{ opacity: 0.3; }}
        """)

    def _apply_global_styles(self):
        self.setStyleSheet(f"QDialog {{ background: {P.BG0}; }}")
        self._container.setStyleSheet(f"""
            QFrame#dialogContainer {{
                background: {P.BG1};
                border: 1px solid {P.BORDER2};
            }}
            QFrame#header {{
                background: {P.BG0};
                border-bottom: 1px solid {P.BORDER2};
            }}
            QTabWidget::pane {{
                border-top: 1px solid {P.BORDER2};
                background: transparent;
            }}
            QTabBar::tab {{
                background: {P.BG3};
                color: {P.T2};
                padding: 6px 16px;
                border: 1px solid {P.BORDER};
                border-bottom: none;
                margin-right: 2px;
                font-family: {FONT_UI};
                font-weight: 800;
                font-size: 10px;
                letter-spacing: 1px;
            }}
            QTabBar::tab:selected {{
                background: {P.BG1};
                color: {P.T0};
                border: 1px solid {P.BORDER2};
                border-bottom: none;
                border-top: 2px solid {P.BLUE};
            }}
            QTabBar::tab:hover:!selected {{
                background: {P.BG2};
                color: {P.T1};
            }}
            QGroupBox {{
                border: 1px solid {P.BORDER};
                border-radius: 1px;
                margin-top: 12px;
                font-family: {FONT_UI};
                font-size: 10px;
                font-weight: 800;
                color: {P.T2};
                letter-spacing: 1px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
            }}
            QPushButton#actionBtnPrimary {{
                background: {P.BLUE};
                color: #ffffff;
                border: none;
                border-radius: 1px;
                padding: 6px 16px;
                font-family: {FONT_UI};
                font-weight: 800;
                font-size: 11px;
                letter-spacing: 1px;
            }}
            QPushButton#actionBtnPrimary:hover {{ background: #4b8eff; }}

            QPushButton#actionBtnSecondary {{
                background: {P.BG3};
                color: {P.T1};
                border: 1px solid {P.BORDER2};
                border-radius: 1px;
                padding: 6px 16px;
                font-family: {FONT_UI};
                font-weight: 800;
                font-size: 11px;
                letter-spacing: 1px;
            }}
            QPushButton#actionBtnSecondary:hover {{ background: {P.BG2}; color: {P.T0}; border: 1px solid {P.T2}; }}
        """)

    def _pick_color(self, key: str):
        current = self._get_color(key)
        color = QColorDialog.getColor(QColor(current), self, "Pick color")
        if not color.isValid():
            return
        color_hex = color.name()
        self._set_color(key, color_hex)
        self._set_button_color(self._buttons[key], color_hex)

        if self.link_checkbox.isChecked() and key.startswith("candles."):
            self._sync_linked_colors_from_candles()

    def _sync_linked_state(self, is_linked: bool):
        for key in ("volume.up", "volume.down", "tables.positive", "tables.negative"):
            self._buttons[key].setEnabled(not is_linked)
        if is_linked:
            self._sync_linked_colors_from_candles()

    def _sync_linked_colors_from_candles(self):
        up = self._theme["candles"]["up"]
        down = self._theme["candles"]["down"]
        self._set_color("volume.up", up)
        self._set_color("volume.down", down)
        self._set_color("tables.positive", up)
        self._set_color("tables.negative", down)
        for key in ("volume.up", "volume.down", "tables.positive", "tables.negative"):
            self._set_button_color(self._buttons[key], self._get_color(key))

    def _set_color(self, key: str, value: str):
        section, item = key.split(".")
        self._theme[section][item] = value

    def _get_color(self, key: str) -> str:
        section, item = key.split(".")
        return self._theme[section][item]

    def get_theme(self) -> Dict[str, Any]:
        self._theme["link_all_sections"] = self.link_checkbox.isChecked()
        self._theme["enable_table_directional_colors"] = self.table_color_toggle_checkbox.isChecked()
        self._theme["enable_volume_strength_indicator"] = self.volume_strength_toggle_checkbox.isChecked()
        self._theme["show_table_vertical_lines"] = self.show_table_vertical_lines_checkbox.isChecked()
        self._theme["show_scanner_volume_column"] = self.show_scanner_volume_checkbox.isChecked()
        self._theme["show_watchlist_volume_column"] = self.show_watchlist_volume_checkbox.isChecked()
        self._theme["scanner_live_ticks"] = self.scanner_live_ticks_checkbox.isChecked()
        self._theme["status_bar_alignment"] = (
            "right" if self.status_bar_align_right_checkbox.isChecked() else "left"
        )
        self._theme["status_bar_metrics_right"] = self.status_pnl_exposure_right_checkbox.isChecked()
        self._theme["show_account_name"] = self.show_account_name_checkbox.isChecked()
        self._theme["show_account_balance"] = self.show_account_balance_checkbox.isChecked()
        self._theme["preferred_username"] = self.preferred_username_input.text().strip()
        self._theme["dual_chart_mode"] = self.dual_chart_mode_checkbox.isChecked()
        return self._theme

    def _reset_default_candle_colors(self):
        self._set_color("candles.up", self.DEFAULT_BULL_CANDLE_COLOR)
        self._set_color("candles.down", self.DEFAULT_BEAR_CANDLE_COLOR)
        self._set_button_color(self._buttons["candles.up"], self.DEFAULT_BULL_CANDLE_COLOR)
        self._set_button_color(self._buttons["candles.down"], self.DEFAULT_BEAR_CANDLE_COLOR)

        if self.link_checkbox.isChecked():
            self._sync_linked_colors_from_candles()

    # ─────────────────────────────────────────────────────────────────────────────
    #  FRAMELESS WINDOW DRAG SUPPORT
    # ─────────────────────────────────────────────────────────────────────────────
    def mousePressEvent(self, event: QMouseEvent):
        from PySide6.QtWidgets import QAbstractButton, QTabBar
        # Ensure we don't drag if clicking a button, checkbox, or tab
        w = self.childAt(event.pos())
        while w:
            if isinstance(w, (QAbstractButton, QTabBar)):
                return super().mousePressEvent(event)
            w = w.parentWidget()

        if event.button() == Qt.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_active and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_active = False
        super().mouseReleaseEvent(event)
