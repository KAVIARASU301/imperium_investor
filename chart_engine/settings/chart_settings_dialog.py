# chart_engine/settings/chart_settings_dialog.py
#
# Modal dialog for adjusting global chart appearance:
#   - Candle + volume colors
#   - Symbol watermark options
#
# Emits settings_changed(dict) on Apply so the chart can react live.

from typing import Any, Dict

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QWidget,
    QVBoxLayout,
)

_DEFAULT_HISTORY_DAYS_BY_INTERVAL = {
    "minute": 5,
    "3minute": 10,
    "5minute": 10,
    "10minute": 10,
    "15minute": 10,
    "30minute": 30,
    "60minute": 50,
    "day": 100,
    "week": 1000,
    "month": 2000,
}


class ChartSettingsDialog(QDialog):
    """Adjust global chart display settings. Emits settings_changed on apply."""

    settings_changed = Signal(dict)

    def __init__(self, current_settings: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chart Settings")
        self.setMinimumSize(680, 560)
        self._s = dict(current_settings)          # working copy
        self._color_btns: Dict[str, QPushButton] = {}
        self._build_ui()
        self._apply_styles()

    # ─── Build ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("Chart Settings")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("Display, history, and chart info preferences")
        subtitle.setObjectName("dialogSubtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        tabs = QTabWidget()
        root.addWidget(tabs)

        display_tab = QWidget()
        display_grid = QGridLayout(display_tab)
        display_grid.setContentsMargins(8, 8, 8, 8)
        display_grid.setHorizontalSpacing(16)
        display_grid.setVerticalSpacing(10)

        left_form = QFormLayout()
        left_form.setSpacing(10)
        left_form.setLabelAlignment(left_form.labelAlignment())
        right_form = QFormLayout()
        right_form.setSpacing(10)

        # ── Candle colors ──
        left_form.addRow("Up Candle Color:", self._color_row("up_candle_color", "#00c896"))
        left_form.addRow("Down Candle Color:", self._color_row("down_candle_color", "#e84060"))

        # ── Watermark ──
        self.wm_enabled = QCheckBox("Show symbol watermark")
        self.wm_enabled.setChecked(self._s.get("watermark_enabled", True))
        left_form.addRow("Watermark:", self.wm_enabled)

        self.wm_description = QCheckBox("Show company description under symbol")
        self.wm_description.setChecked(self._s.get("show_watermark_description", True))
        left_form.addRow("Watermark Description:", self.wm_description)

        self.toolbar_symbol_display = QComboBox()
        self.toolbar_symbol_display.addItem("Symbol Name", "symbol")
        self.toolbar_symbol_display.addItem("Symbol Description", "description")
        current_toolbar_display = self._s.get("toolbar_symbol_display", "description")
        for i in range(self.toolbar_symbol_display.count()):
            if self.toolbar_symbol_display.itemData(i) == current_toolbar_display:
                self.toolbar_symbol_display.setCurrentIndex(i)
                break
        left_form.addRow("Toolbar Symbol Text:", self.toolbar_symbol_display)

        left_form.addRow("Watermark Color:", self._color_row("watermark_color", "#ffffff"))

        self.wm_opacity = QDoubleSpinBox()
        self.wm_opacity.setRange(0.0, 1.0)
        self.wm_opacity.setSingleStep(0.05)
        self.wm_opacity.setDecimals(2)
        self.wm_opacity.setValue(self._s.get("watermark_opacity", 0.28))
        left_form.addRow("Watermark Opacity:", self.wm_opacity)

        self.wm_position = QComboBox()
        for label, data in [("Top Center", "top_center"),
                             ("Mid Center", "mid_center"),
                             ("Bottom Center", "bottom_center")]:
            self.wm_position.addItem(label, data)
        current_pos = self._s.get("watermark_position", "bottom_center")
        for i in range(self.wm_position.count()):
            if self.wm_position.itemData(i) == current_pos:
                self.wm_position.setCurrentIndex(i)
                break
        left_form.addRow("Watermark Position:", self.wm_position)

        # ── Indicator labels ──
        self.indicator_scale_labels_enabled = QCheckBox("Show indicator labels on price scale")
        self.indicator_scale_labels_enabled.setChecked(self._s.get("indicator_scale_labels_enabled", False))
        right_form.addRow("Indicator Labels:", self.indicator_scale_labels_enabled)

        self.crosshair_snap_enabled = QCheckBox("Snap crosshair to OHLC")
        self.crosshair_snap_enabled.setChecked(self._s.get("crosshair_snap_enabled", False))
        right_form.addRow("Crosshair Snap:", self.crosshair_snap_enabled)

        self.show_time_slider = QCheckBox("Show time slider")
        self.show_time_slider.setChecked(self._s.get("show_time_slider", True))
        right_form.addRow("Time Slider:", self.show_time_slider)

        self.tool_selection_mode = QComboBox()
        self.tool_selection_mode.addItem("One use (reselect each time)", "single_use")
        self.tool_selection_mode.addItem("Multiple use (stay active)", "multi_use")
        current_mode = self._s.get("tool_selection_mode", "single_use")
        for i in range(self.tool_selection_mode.count()):
            if self.tool_selection_mode.itemData(i) == current_mode:
                self.tool_selection_mode.setCurrentIndex(i)
                break
        self.tool_selection_mode.setToolTip("Applies only to drawing tools in the tools container.")
        right_form.addRow("Tool Selection Mode:", self.tool_selection_mode)

        self.show_snapshot = QCheckBox("Show snapshot button in toolbar")
        self.show_snapshot.setChecked(self._s.get("show_snapshot", True))
        right_form.addRow("Toolbar Snapshot:", self.show_snapshot)

        self.show_autoscale = QCheckBox("Show auto-scale button in toolbar")
        self.show_autoscale.setChecked(self._s.get("show_autoscale", True))
        right_form.addRow("Toolbar Auto-scale:", self.show_autoscale)

        self.show_refresh = QCheckBox("Show refresh button in toolbar")
        self.show_refresh.setChecked(self._s.get("show_refresh", True))
        right_form.addRow("Toolbar Refresh:", self.show_refresh)

        self.wm_font_size = QSpinBox()
        self.wm_font_size.setRange(0, 300)
        self.wm_font_size.setValue(self._s.get("watermark_font_size", 50))
        self.wm_font_size.setToolTip("0 = auto size")
        right_form.addRow("Watermark Font Size:", self.wm_font_size)

        self.wm_description_opacity = QDoubleSpinBox()
        self.wm_description_opacity.setRange(0.0, 1.0)
        self.wm_description_opacity.setSingleStep(0.05)
        self.wm_description_opacity.setDecimals(2)
        self.wm_description_opacity.setValue(self._s.get("watermark_description_opacity", 0.13))
        right_form.addRow("Description Opacity:", self.wm_description_opacity)

        self.wm_description_font_size = QSpinBox()
        self.wm_description_font_size.setRange(0, 150)
        self.wm_description_font_size.setValue(self._s.get("watermark_description_font_size", 25))
        self.wm_description_font_size.setToolTip("0 = auto size")
        right_form.addRow("Description Font Size:", self.wm_description_font_size)

        display_grid.addLayout(left_form, 0, 0)
        display_grid.addLayout(right_form, 0, 1)
        display_grid.setColumnStretch(0, 1)
        display_grid.setColumnStretch(1, 1)

        tabs.addTab(display_tab, "Display")

        chart_tab = QWidget()
        chart_layout = QFormLayout(chart_tab)
        chart_layout.setContentsMargins(8, 8, 8, 8)
        chart_layout.setSpacing(10)
        self.history_days_inputs: Dict[str, QSpinBox] = {}
        interval_rows = [
            ("minute", "1 minute"),
            ("3minute", "3 minute"),
            ("5minute", "5 minute"),
            ("10minute", "10 minute"),
            ("15minute", "15 minute"),
            ("30minute", "30 minute"),
            ("60minute", "1 hour"),
            ("day", "Day"),
            ("week", "Week"),
            ("month", "Month"),
        ]
        saved_days = dict(self._s.get("history_days_by_interval", {}))
        for key, label in interval_rows:
            spin = QSpinBox()
            spin.setRange(1, 4000)
            spin.setValue(int(saved_days.get(key, _DEFAULT_HISTORY_DAYS_BY_INTERVAL[key])))
            chart_layout.addRow(f"{label} (days):", spin)
            self.history_days_inputs[key] = spin

        self.right_buffer_candles = QSpinBox()
        self.right_buffer_candles.setRange(0, 200)
        self.right_buffer_candles.setValue(int(self._s.get("right_buffer_candles", 20)))
        self.right_buffer_candles.setToolTip("Empty candles space between latest candle and price scale.")
        chart_layout.addRow("Right Empty Space:", self.right_buffer_candles)
        tabs.addTab(chart_tab, "Chart")

        toggles_tab = QWidget()
        toggles_layout = QFormLayout(toggles_tab)
        toggles_layout.setContentsMargins(8, 8, 8, 8)
        toggles_layout.setSpacing(10)

        self.chart_info_toggles = {}
        chart_info_fields = [
            ("Show ADR", "show_adr"),
            ("Show Monthly %", "show_perf_monthly"),
            ("Show 3M %", "show_perf_3m"),
            ("Show 6M %", "show_perf_6m"),
            ("Show 1Y %", "show_perf_1y"),
            ("Show Date", "show_info_date"),
            ("Show Open", "show_info_open"),
            ("Show High", "show_info_high"),
            ("Show Low", "show_info_low"),
            ("Show Close", "show_info_close"),
            ("Show Volume", "show_info_volume"),
            ("Show % Change", "show_info_pct_change"),
        ]
        for label, key in chart_info_fields:
            cb = QCheckBox(label)
            default_enabled = key in {
                "show_adr",
                "show_perf_monthly",
                "show_perf_3m",
                "show_info_date",
                "show_info_volume",
                "show_info_pct_change",
            }
            cb.setChecked(self._s.get(key, default_enabled))
            toggles_layout.addRow(label + ":", cb)
            self.chart_info_toggles[key] = cb
        tabs.addTab(toggles_tab, "Info Toggles")

        # ── Buttons ──
        btn_row = QHBoxLayout()
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._apply)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(apply_btn)
        btn_row.addWidget(cancel_btn)
        root.addLayout(btn_row)

    def _color_row(self, key: str, default: str) -> QHBoxLayout:
        row = QHBoxLayout()
        btn = QPushButton()
        btn.setFixedSize(30, 20)
        color = self._s.get(key, default)
        btn.setStyleSheet(f"background-color: {color}; border: 1px solid #555;")
        btn.clicked.connect(lambda _checked=False, k=key: self._pick_color(k))
        row.addWidget(btn)
        row.addStretch()
        self._color_btns[key] = btn
        return row

    def _pick_color(self, key: str) -> None:
        color = QColorDialog.getColor(QColor(self._s.get(key, "#ffffff")), self)
        if color.isValid():
            self._s[key] = color.name()
            self._color_btns[key].setStyleSheet(
                f"background-color: {color.name()}; border: 1px solid #555;"
            )

    def _apply(self) -> None:
        new = {
            "up_candle_color": self._s.get("up_candle_color", "#00c896"),
            "down_candle_color": self._s.get("down_candle_color", "#e84060"),
            "up_volume_color": self._s.get("up_volume_color", self._s.get("up_candle_color", "#00c896")),
            "down_volume_color": self._s.get("down_volume_color", self._s.get("down_candle_color", "#e84060")),
            "watermark_enabled": self.wm_enabled.isChecked(),
            "show_watermark_description": self.wm_description.isChecked(),
            "toolbar_symbol_display": self.toolbar_symbol_display.currentData(),
            "watermark_color": self._s.get("watermark_color", "#ffffff"),
            "watermark_opacity": self.wm_opacity.value(),
            "watermark_position": self.wm_position.currentData(),
            "watermark_font_size": self.wm_font_size.value(),
            "watermark_description_opacity": self.wm_description_opacity.value(),
            "watermark_description_font_size": self.wm_description_font_size.value(),
            "indicator_scale_labels_enabled": self.indicator_scale_labels_enabled.isChecked(),
            "crosshair_snap_enabled": self.crosshair_snap_enabled.isChecked(),
            "show_time_slider": self.show_time_slider.isChecked(),
            "tool_selection_mode": self.tool_selection_mode.currentData(),
            "show_snapshot": self.show_snapshot.isChecked(),
            "show_autoscale": self.show_autoscale.isChecked(),
            "show_refresh": self.show_refresh.isChecked(),
            "history_days_by_interval": {
                interval: spin.value()
                for interval, spin in self.history_days_inputs.items()
            },
            "right_buffer_candles": self.right_buffer_candles.value(),
            **{key: cb.isChecked() for key, cb in self.chart_info_toggles.items()},
        }
        self.settings_changed.emit(new)
        self.accept()

    # ─── Styles ───────────────────────────────────────────────────────────────

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            QDialog { background-color: #111827; color: #e5e7eb; border: 1px solid #233044; }
            QLabel { color: #cbd5e1; font-size: 12px; background: transparent; }
            QLabel#dialogTitle { color: #f8fafc; font-size: 16px; font-weight: 700; }
            QLabel#dialogSubtitle { color: #94a3b8; font-size: 11px; margin-bottom: 4px; }
            QTabWidget::pane { border: 1px solid #334155; background: #0f172a; }
            QTabBar::tab {
                background: #1e293b; color: #cbd5e1;
                border: 1px solid #334155; padding: 6px 10px; min-width: 90px;
            }
            QTabBar::tab:selected { background: #334155; color: #f8fafc; }
            QSpinBox, QDoubleSpinBox, QComboBox {
                background-color: #0b1220; color: #e5e7eb;
                border: 1px solid #334155; border-radius: 4px; padding: 4px 6px;
            }
            QSpinBox::up-button, QSpinBox::down-button,
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 16px; background-color: #1e293b; border-left: 1px solid #334155;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover { background-color: #334155; }
            QComboBox QAbstractItemView { background-color: #0b1220; color: #e5e7eb; }
            QPushButton {
                background-color: #1d4ed8; color: #eff6ff;
                border: 1px solid #3b82f6; border-radius: 4px;
                padding: 5px 12px; font-weight: 600;
            }
            QPushButton:hover { background-color: #2563eb; }
            QPushButton:pressed { background-color: #1e40af; }
            QCheckBox { color: #cbd5e1; spacing: 8px; }
            QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #64748b; border-radius: 3px; }
            QCheckBox::indicator:checked { background-color: #3b82f6; border-color: #3b82f6; }
        """)