from typing import Dict, Any

from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
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
    QScrollArea,
)
from PySide6.QtGui import QColor, QMouseEvent, QCursor


# ─────────────────────────────────────────────────────────────────────────────
#  PALETTE & TYPOGRAPHY (TC2000 Institutional Dark)
# ─────────────────────────────────────────────────────────────────────────────
class P:
    BG0 = "#050709"
    BG1 = "#0a0d12"
    BG2 = "#0f1318"
    BG3 = "#141920"
    BG4 = "#1a2030"
    BORDER = "#1a2030"
    BORDER2 = "#2a3a50"
    T0 = "#e8f0ff"
    T1 = "#a8bcd4"
    T2 = "#5a7090"
    T3 = "#2a3a50"
    BULL = "#00d4a8"
    BEAR = "#ff4d6a"
    AMBER = "#f59e0b"
    CYAN = "#00d4ff"
    BLUE = "#3b82f6"


FONT_UI = "Inter, Aptos, 'Segoe UI Variable', 'Segoe UI', Roboto, 'Noto Sans', Arial, sans-serif"
FONT_MONO = "'JetBrains Mono', Consolas, 'Roboto Mono', 'Courier New', monospace"


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
    """Compact AMOLED terminal checkbox."""

    def __init__(self, label="", parent=None):
        super().__init__(label, parent)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setMinimumHeight(20)
        self.setStyleSheet(f"""
            QCheckBox {{
                color:{P.T1};
                spacing:7px;
                font-family:{FONT_UI};
                font-size:10px;
                font-weight:700;
                letter-spacing:0.45px;
                background:transparent;
                padding:1px 0px;
            }}
            QCheckBox:hover {{
                color:{P.T0};
            }}
            QCheckBox::indicator {{
                width:12px;
                height:12px;
                border-radius:2px;
                background:{P.BG2};
                border:1px solid {P.BG4};
            }}
            QCheckBox::indicator:hover {{
                border:1px solid {P.T2};
                background:{P.BG3};
            }}
            QCheckBox::indicator:checked {{
                background:{P.BULL};
                border:1px solid {P.BULL};
            }}
            QCheckBox::indicator:disabled {{
                background:{P.BG1};
                border:1px solid {P.BORDER};
            }}
            QCheckBox:disabled {{
                color:{P.T3};
            }}
        """)


class ColorSettingsDialog(QDialog):
    """
    Institutional Color Settings panel.
    Strict TC2000 dark mode aesthetic, frameless drag, sharp borders.
    """

    DEFAULT_POSITIVE_COLOR = "#00d4a8"
    DEFAULT_NEGATIVE_COLOR = "#ff4d6a"

    def __init__(self, current_theme: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumWidth(560)
        self.setMinimumHeight(540)
        self.setModal(True)

        self._theme = current_theme
        self._buttons: Dict[str, QPushButton] = {}
        self._drag_offset = QPoint()
        self._drag_active = False

        self._setup_ui()
        self._apply_global_styles()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
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
        body_layout.setContentsMargins(8, 7, 8, 8)
        body_layout.setSpacing(6)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("customTabs")

        # --- TAB: COLORS ---
        colors_tab = QWidget()
        colors_layout = QVBoxLayout(colors_tab)
        colors_layout.setContentsMargins(10, 12, 10, 10)
        colors_layout.setSpacing(10)

        colors_layout.addWidget(self._build_color_intro())

        self.table_color_toggle_checkbox = _Toggle("ENABLE DIRECTIONAL COLORS IN DATA TABLES")
        self.table_color_toggle_checkbox.setChecked(
            bool(self._theme.get("enable_table_directional_colors", False))
        )
        colors_layout.addWidget(
            self._build_toggle_card(
                "TABLE COLOR BEHAVIOR",
                "Apply the same up/down colors to scanner, watchlist, and positions table values.",
                self.table_color_toggle_checkbox,
            )
        )

        colors_layout.addWidget(self._build_color_section(
            "Universal Color Code",
            "Used everywhere for up / positive and down / negative market values.",
            [
                (
                    "Up / Positive",
                    "global.positive",
                    self._theme["global"]["positive"],
                    "Gains, advances, profits, and bullish price movement.",
                ),
                (
                    "Down / Negative",
                    "global.negative",
                    self._theme["global"]["negative"],
                    "Losses, declines, drawdowns, and bearish price movement.",
                ),
            ],
        ))

        colors_layout.addWidget(self._build_color_section(
            "Table Support Colors",
            "Secondary table text colors kept separate from candlestick chart settings.",
            [
                (
                    "Neutral",
                    "tables.neutral",
                    self._theme["tables"]["neutral"],
                    "Flat values, unchanged prices, muted labels, and fallback text.",
                ),
                (
                    "Volume Text",
                    "tables.volume",
                    self._theme["tables"]["volume"],
                    "Volume columns and liquidity-related values across data tables.",
                ),
            ],
        ))
        colors_layout.addStretch()

        # Use a scroll area so color rows/buttons never get clipped on smaller
        # windows or high-DPI Linux themes.
        colors_scroll = QScrollArea()
        colors_scroll.setObjectName("colorsScroll")
        colors_scroll.setWidgetResizable(True)
        colors_scroll.setFrameShape(QFrame.NoFrame)
        colors_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        colors_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        colors_scroll.setWidget(colors_tab)

        self.tabs.addTab(colors_scroll, "COLORS")

        # --- TAB: MORE ---
        more_tab = QWidget()
        more_layout = QVBoxLayout(more_tab)
        more_layout.setContentsMargins(0, 6, 0, 0)
        more_layout.setSpacing(5)

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
        account_layout.setContentsMargins(8, 9, 8, 8)
        account_layout.setSpacing(5)

        self.show_account_name_checkbox = _Toggle("SHOW ACCOUNT NAME")
        self.show_account_name_checkbox.setChecked(bool(self._theme.get("show_account_name", True)))
        account_layout.addWidget(self.show_account_name_checkbox)

        self.show_account_balance_checkbox = _Toggle("SHOW ACCOUNT BALANCE")
        self.show_account_balance_checkbox.setChecked(bool(self._theme.get("show_account_balance", True)))
        account_layout.addWidget(self.show_account_balance_checkbox)


        self.show_ticker_board_checkbox = _Toggle("SHOW HEADER TICKER BOARD")
        self.show_ticker_board_checkbox.setChecked(bool(self._theme.get("show_ticker_board", True)))
        account_layout.addWidget(self.show_ticker_board_checkbox)

        ticker_row = QHBoxLayout()
        ticker_label = _Label("TICKER SYMBOLS (MAX 5)", color=P.T1, size=10, bold=True)
        self.ticker_symbols_input = QLineEdit()
        self.ticker_symbols_input.setPlaceholderText("NIFTY, SENSEX")
        self.ticker_symbols_input.setFixedHeight(22)
        default_symbols = self._theme.get("ticker_board_symbols", ["NIFTY", "SENSEX"])
        if isinstance(default_symbols, list):
            self.ticker_symbols_input.setText(", ".join(str(sym).strip().upper() for sym in default_symbols[:5] if str(sym).strip()))
        self.ticker_symbols_input.setClearButtonEnabled(True)
        ticker_row.addWidget(ticker_label)
        ticker_row.addWidget(self.ticker_symbols_input)
        account_layout.addLayout(ticker_row)

        username_row = QHBoxLayout()
        username_label = _Label("PREFERRED USERNAME", color=P.T1, size=10, bold=True)
        self.preferred_username_input = QLineEdit()
        self.preferred_username_input.setPlaceholderText("Leave blank to use profile ID")
        self.preferred_username_input.setFixedHeight(22)
        self.preferred_username_input.setText(str(self._theme.get("preferred_username", "")))
        self.preferred_username_input.setClearButtonEnabled(True)
        self.preferred_username_input.setMaxLength(40)
        username_row.addWidget(username_label)
        username_row.addWidget(self.preferred_username_input)
        account_layout.addLayout(username_row)

        title_bar_group = QGroupBox("TITLE BAR")
        title_bar_layout = QVBoxLayout(title_bar_group)
        title_bar_layout.setContentsMargins(8, 9, 8, 8)
        title_bar_layout.setSpacing(5)

        self.show_app_title_checkbox = _Toggle("SHOW APP NAME IN TOP TITLE BAR")
        self.show_app_title_checkbox.setChecked(bool(self._theme.get("show_app_title", True)))
        title_bar_layout.addWidget(self.show_app_title_checkbox)

        app_title_row = QHBoxLayout()
        app_title_label = _Label("PREFERRED APP NAME", color=P.T1, size=10, bold=True)
        self.app_title_input = QLineEdit()
        self.app_title_input.setPlaceholderText("Swing Trader")
        self.app_title_input.setFixedHeight(22)
        self.app_title_input.setText(str(self._theme.get("app_title_text", "Swing Trader")))
        self.app_title_input.setClearButtonEnabled(True)
        self.app_title_input.setMaxLength(60)
        app_title_row.addWidget(app_title_label)
        app_title_row.addWidget(self.app_title_input)
        title_bar_layout.addLayout(app_title_row)

        more_layout.addWidget(title_bar_group)

        more_layout.addWidget(account_group)
        more_layout.addStretch()

        self.tabs.addTab(more_tab, "ADVANCED")

        body_layout.addWidget(self.tabs)

        # Bottom Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 5, 0, 0)
        btn_layout.setSpacing(6)
        btn_layout.addStretch()

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setObjectName("actionBtnSecondary")
        cancel_btn.setCursor(QCursor(Qt.PointingHandCursor))
        cancel_btn.clicked.connect(self.reject)

        reset_btn = QPushButton("RESET GLOBAL COLORS")
        reset_btn.setObjectName("actionBtnSecondary")
        reset_btn.setCursor(QCursor(Qt.PointingHandCursor))
        reset_btn.clicked.connect(self._reset_default_global_colors)

        save_btn = QPushButton("APPLY SETTINGS")
        save_btn.setObjectName("actionBtnPrimary")
        save_btn.setCursor(QCursor(Qt.PointingHandCursor))
        save_btn.clicked.connect(self.accept)

        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(reset_btn)
        btn_layout.addWidget(save_btn)
        body_layout.addLayout(btn_layout)

        root.addWidget(body_widget)

    def _build_color_intro(self) -> QFrame:
        card = QFrame()
        card.setObjectName("colorIntroCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        text_stack = QVBoxLayout()
        text_stack.setSpacing(3)
        title = _Label("Color System", P.T0, 13, bold=True)
        subtitle = _Label(
            "Clean global colors for market direction. Candle-only styling stays in chart settings.",
            P.T1,
            10,
        )
        subtitle.setWordWrap(True)
        text_stack.addWidget(title)
        text_stack.addWidget(subtitle)
        layout.addLayout(text_stack, 1)

        badge = _Label("GLOBAL", P.BG0, 10, bold=True)
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(72, 24)
        badge.setObjectName("colorIntroBadge")
        layout.addWidget(badge, 0, Qt.AlignTop)
        return card

    def _build_toggle_card(self, title: str, description: str, toggle: QCheckBox) -> QFrame:
        card = QFrame()
        card.setObjectName("settingCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        title_label = _Label(title, P.AMBER, 9, bold=True)
        title_label.setObjectName("sectionKicker")
        description_label = _Label(description, P.T1, 10)
        description_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addWidget(description_label)
        layout.addWidget(toggle)
        return card

    def _build_color_section(self, title: str, description: str, items: list) -> QFrame:
        section = QFrame()
        section.setObjectName("colorSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        header = QVBoxLayout()
        header.setSpacing(2)
        title_label = _Label(title, P.T0, 11, bold=True)
        description_label = _Label(description, P.T1, 10)
        description_label.setWordWrap(True)
        header.addWidget(title_label)
        header.addWidget(description_label)
        layout.addLayout(header)

        for index, (label_text, key, initial_val, helper_text) in enumerate(items):
            row = self._build_color_row(label_text, key, initial_val, helper_text)
            if index == 0:
                row.setProperty("firstRow", True)
            layout.addWidget(row)

        return section

    def _build_color_row(self, label_text: str, key: str, initial_val: str, helper_text: str) -> QFrame:
        row = QFrame()
        row.setObjectName("colorRow")
        row.setMinimumHeight(50)
        layout = QHBoxLayout(row)
        # Extra bottom padding prevents the color swatch border/background from
        # being cut off by tight Qt layout calculations.
        layout.setContentsMargins(0, 10, 0, 4)
        layout.setSpacing(12)

        label_stack = QVBoxLayout()
        label_stack.setSpacing(2)
        label = _Label(label_text, P.T0, 10, bold=True)
        helper = _Label(helper_text, P.T2, 9)
        helper.setWordWrap(True)
        label_stack.addWidget(label)
        label_stack.addWidget(helper)
        layout.addLayout(label_stack, 1)

        btn = self._build_color_button(key, initial_val)
        layout.addWidget(btn, 0, Qt.AlignVCenter)
        return row

    def _build_header(self) -> QFrame:
        f = QFrame()
        f.setObjectName("header")
        f.setFixedHeight(26)
        h = QHBoxLayout(f)
        h.setContentsMargins(9, 0, 6, 0)
        h.setSpacing(8)

        title = _Label("TERMINAL SETTINGS", P.T0, 10, bold=True)
        title.setObjectName("dialogTitle")
        h.addWidget(title)
        h.addStretch()

        self._close_btn = QPushButton("✕")
        self._close_btn.setObjectName("closeBtn")
        self._close_btn.setFixedSize(22, 22)
        self._close_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._close_btn.clicked.connect(self.reject)
        h.addWidget(self._close_btn)
        return f

    def _build_color_button(self, key: str, value: str) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("colorSwatchButton")
        btn.setCursor(QCursor(Qt.PointingHandCursor))
        btn.setMinimumSize(132, 34)
        btn.setMaximumSize(132, 34)
        btn.clicked.connect(lambda: self._pick_color(key))
        self._buttons[key] = btn
        self._set_button_color(btn, value)
        return btn

    def _set_button_color(self, button: QPushButton, color_hex: str):
        # Calculate contrast for text while keeping the swatch compact and readable.
        color = QColor(color_hex)
        luminance = (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()) / 255
        text_color = "#050709" if luminance > 0.52 else "#eef5ff"

        button.setText(color_hex.upper())
        button.setStyleSheet(f"""
            QPushButton {{
                background-color: {color_hex};
                color: {text_color};
                border: 1px solid rgba(232,240,255,0.24);
                border-radius: 4px;
                font-family: {FONT_MONO};
                font-size: 10px;
                font-weight: 850;
                letter-spacing: 0.35px;
                padding: 0px 8px;
                margin: 1px 0px;
            }}
            QPushButton:hover {{
                border: 1px solid {P.CYAN};
            }}
            QPushButton:disabled {{
                background-color: {P.BG2};
                color: {P.T3};
                border: 1px solid {P.BORDER};
            }}
        """)

    def _apply_global_styles(self):
        self.setStyleSheet(f"QDialog {{ background: transparent; }}")
        self._container.setStyleSheet(f"""
            QFrame#dialogContainer {{
                background: {P.BG1};
                border: 1px solid {P.BG4};
                border-radius: 2px;
            }}
            QFrame#header {{
                background: {P.BG0};
                border-bottom: 1px solid {P.BG4};
            }}
            QLabel#dialogTitle {{
                color: {P.T0};
                font-family: {FONT_UI};
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 1.1px;
                background: transparent;
            }}
            QPushButton#closeBtn {{
                background: transparent;
                color: {P.T2};
                border: 1px solid transparent;
                border-radius: 2px;
                font-family: {FONT_UI};
                font-size: 12px;
                font-weight: 800;
                padding: 0px;
            }}
            QPushButton#closeBtn:hover {{
                color: {P.BEAR};
                background: rgba(255,77,106,0.10);
                border: 1px solid rgba(255,77,106,0.28);
            }}

            QWidget {{
                background: transparent;
                font-family: {FONT_UI};
            }}

            QScrollArea#colorsScroll {{
                background: transparent;
                border: none;
            }}
            QScrollArea#colorsScroll > QWidget > QWidget {{
                background: transparent;
            }}

            QTabWidget#customTabs::pane {{
                background: {P.BG1};
                border: 1px solid {P.BG4};
                border-radius: 2px;
                top: -1px;
            }}
            QTabBar::tab {{
                background: {P.BG2};
                color: {P.T2};
                padding: 4px 14px;
                min-height: 18px;
                border: 1px solid {P.BG4};
                border-bottom: none;
                margin-right: 2px;
                border-top-left-radius: 2px;
                border-top-right-radius: 2px;
                font-family: {FONT_UI};
                font-weight: 800;
                font-size: 9px;
                letter-spacing: 1.1px;
            }}
            QTabBar::tab:selected {{
                background: {P.BG1};
                color: {P.T0};
                border: 1px solid {P.BG4};
                border-bottom: 1px solid {P.BG1};
                border-top: 2px solid {P.AMBER};
            }}
            QTabBar::tab:hover:!selected {{
                background: {P.BG3};
                color: {P.T1};
            }}

            QFrame#colorIntroCard {{
                background: {P.BG2};
                border: 1px solid {P.BG4};
                border-radius: 4px;
            }}
            QLabel#colorIntroBadge {{
                background: {P.AMBER};
                color: {P.BG0};
                border-radius: 3px;
                font-family: {FONT_UI};
                font-size: 9px;
                font-weight: 900;
                letter-spacing: 1.0px;
            }}
            QFrame#settingCard,
            QFrame#colorSection {{
                background: {P.BG2};
                border: 1px solid {P.BG4};
                border-radius: 4px;
            }}
            QFrame#settingCard:hover,
            QFrame#colorSection:hover {{
                border: 1px solid {P.BORDER2};
            }}
            QFrame#colorRow {{
                background: transparent;
                border-top: 1px solid {P.BORDER};
            }}
            QFrame#colorRow[firstRow="true"] {{
                border-top: 1px solid transparent;
            }}
            QLabel#sectionKicker {{
                letter-spacing: 1.0px;
            }}

            QGroupBox {{
                background: {P.BG2};
                border: 1px solid {P.BG4};
                border-radius: 2px;
                margin-top: 8px;
                padding-top: 8px;
                font-family: {FONT_UI};
                font-size: 9px;
                font-weight: 800;
                color: {P.T2};
                letter-spacing: 1.0px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
                color: {P.AMBER};
                background: {P.BG1};
            }}

            QPushButton#actionBtnPrimary,
            QPushButton#actionBtnSecondary {{
                border-radius: 2px;
                padding: 4px 11px;
                min-height: 22px;
                font-family: {FONT_UI};
                font-weight: 800;
                font-size: 10px;
                letter-spacing: 0.8px;
            }}
            QPushButton#actionBtnPrimary {{
                background: {P.BULL};
                color: {P.BG0};
                border: 1px solid {P.BULL};
            }}
            QPushButton#actionBtnPrimary:hover {{
                background: #13e0b5;
                border-color: #13e0b5;
            }}
            QPushButton#actionBtnPrimary:pressed {{
                background: #00b88f;
            }}
            QPushButton#actionBtnSecondary {{
                background: {P.BG2};
                color: {P.T1};
                border: 1px solid {P.BG4};
            }}
            QPushButton#actionBtnSecondary:hover {{
                background: {P.BG3};
                color: {P.T0};
                border: 1px solid {P.T2};
            }}
            QPushButton#actionBtnSecondary:pressed {{
                background: {P.BG0};
            }}

            QLineEdit {{
                background: {P.BG3};
                color: {P.T0};
                border: 1px solid {P.BG4};
                border-radius: 2px;
                padding: 2px 7px;
                font-family: {FONT_UI};
                font-size: 10px;
                font-weight: 650;
                selection-background-color: {P.BG4};
                selection-color: {P.T0};
            }}
            QLineEdit:hover {{
                border: 1px solid {P.BORDER2};
            }}
            QLineEdit:focus {{
                border: 1px solid {P.CYAN};
                background: {P.BG2};
            }}
            QLineEdit::placeholder {{
                color: {P.T3};
            }}

            QScrollBar:vertical {{
                background: transparent;
                width: 4px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {P.BG4};
                border-radius: 2px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {P.T2};
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0px;
                border: none;
                background: none;
            }}
        """)

    def _pick_color(self, key: str):
        current = self._get_color(key)
        color = QColorDialog.getColor(QColor(current), self, "Pick color")
        if not color.isValid():
            return
        color_hex = color.name()
        self._set_color(key, color_hex)
        self._set_button_color(self._buttons[key], color_hex)

    def _set_color(self, key: str, value: str):
        section, item = key.split(".")
        self._theme[section][item] = value

    def _get_color(self, key: str) -> str:
        section, item = key.split(".")
        return self._theme[section][item]

    def get_theme(self) -> Dict[str, Any]:
        self._theme["link_all_sections"] = True
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
        self._theme["show_app_title"] = self.show_app_title_checkbox.isChecked()
        self._theme["app_title_text"] = self.app_title_input.text().strip() or "Swing Trader"
        self._theme["show_ticker_board"] = self.show_ticker_board_checkbox.isChecked()
        raw_symbols = [part.strip().upper() for part in self.ticker_symbols_input.text().split(",")]
        symbols = [sym for sym in raw_symbols if sym][:5]
        self._theme["ticker_board_symbols"] = symbols if symbols else ["NIFTY", "SENSEX"]
        self._theme["dual_chart_mode"] = self.dual_chart_mode_checkbox.isChecked()
        return self._theme

    def _reset_default_global_colors(self):
        self._set_color("global.positive", self.DEFAULT_POSITIVE_COLOR)
        self._set_color("global.negative", self.DEFAULT_NEGATIVE_COLOR)
        self._set_button_color(self._buttons["global.positive"], self.DEFAULT_POSITIVE_COLOR)
        self._set_button_color(self._buttons["global.negative"], self.DEFAULT_NEGATIVE_COLOR)

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