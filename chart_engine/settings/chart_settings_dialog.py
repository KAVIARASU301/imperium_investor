# chart_engine/settings/chart_settings_dialog.py
#
# Modal dialog for adjusting global chart appearance:
#   - Candle + volume colors
#   - Default visible candle count (zoom level)
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
    QHBoxLayout,
    QPushButton,
    QSpinBox,
)


class ChartSettingsDialog(QDialog):
    """Adjust global chart display settings. Emits settings_changed on apply."""

    settings_changed = Signal(dict)

    def __init__(self, current_settings: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chart Settings")
        self.setFixedSize(380, 560)
        self._s = dict(current_settings)          # working copy
        self._color_btns: Dict[str, QPushButton] = {}
        self._build_ui()
        self._apply_styles()

    # ─── Build ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QFormLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # ── Candle dimensions ──
        self.candle_width = QSpinBox()
        self.candle_width.setRange(1, 10)
        self.candle_width.setValue(self._s.get("candle_width", 3))
        layout.addRow("Candle Width:", self.candle_width)

        self.candle_spacing = QSpinBox()
        self.candle_spacing.setRange(0, 5)
        self.candle_spacing.setValue(self._s.get("candle_spacing", 3))
        layout.addRow("Candle Spacing:", self.candle_spacing)

        self.visible_candles = QSpinBox()
        self.visible_candles.setRange(20, 500)
        self.visible_candles.setSingleStep(10)
        self.visible_candles.setValue(self._s.get("default_visible_candles", 100))
        layout.addRow("Default Visible Candles:", self.visible_candles)

        # ── Candle colors ──
        layout.addRow("Up Candle Color:", self._color_row("up_candle_color", "#00c896"))
        layout.addRow("Down Candle Color:", self._color_row("down_candle_color", "#e84060"))

        # ── Watermark ──
        self.wm_enabled = QCheckBox("Show symbol watermark")
        self.wm_enabled.setChecked(self._s.get("watermark_enabled", True))
        layout.addRow("Watermark:", self.wm_enabled)

        self.wm_description = QCheckBox("Show company description under symbol")
        self.wm_description.setChecked(self._s.get("show_watermark_description", True))
        layout.addRow("Watermark Description:", self.wm_description)

        self.toolbar_symbol_display = QComboBox()
        self.toolbar_symbol_display.addItem("Symbol Name", "symbol")
        self.toolbar_symbol_display.addItem("Symbol Description", "description")
        current_toolbar_display = self._s.get("toolbar_symbol_display", "symbol")
        for i in range(self.toolbar_symbol_display.count()):
            if self.toolbar_symbol_display.itemData(i) == current_toolbar_display:
                self.toolbar_symbol_display.setCurrentIndex(i)
                break
        layout.addRow("Toolbar Symbol Text:", self.toolbar_symbol_display)

        layout.addRow("Watermark Color:", self._color_row("watermark_color", "#ffffff"))

        self.wm_opacity = QDoubleSpinBox()
        self.wm_opacity.setRange(0.0, 1.0)
        self.wm_opacity.setSingleStep(0.05)
        self.wm_opacity.setDecimals(2)
        self.wm_opacity.setValue(self._s.get("watermark_opacity", 0.08))
        layout.addRow("Watermark Opacity:", self.wm_opacity)

        self.wm_position = QComboBox()
        for label, data in [("Top Center", "top_center"),
                             ("Mid Center", "mid_center"),
                             ("Bottom Center", "bottom_center")]:
            self.wm_position.addItem(label, data)
        current_pos = self._s.get("watermark_position", "mid_center")
        for i in range(self.wm_position.count()):
            if self.wm_position.itemData(i) == current_pos:
                self.wm_position.setCurrentIndex(i)
                break
        layout.addRow("Watermark Position:", self.wm_position)

        # ── Indicator labels ──
        self.indicator_scale_labels_enabled = QCheckBox("Show indicator labels on price scale")
        self.indicator_scale_labels_enabled.setChecked(self._s.get("indicator_scale_labels_enabled", False))
        layout.addRow("Indicator Labels:", self.indicator_scale_labels_enabled)

        self.crosshair_snap_enabled = QCheckBox("Snap crosshair to OHLC")
        self.crosshair_snap_enabled.setChecked(self._s.get("crosshair_snap_enabled", True))
        layout.addRow("Crosshair Snap:", self.crosshair_snap_enabled)

        self.tool_selection_mode = QComboBox()
        self.tool_selection_mode.addItem("One use (reselect each time)", "single_use")
        self.tool_selection_mode.addItem("Multiple use (stay active)", "multi_use")
        current_mode = self._s.get("tool_selection_mode", "single_use")
        for i in range(self.tool_selection_mode.count()):
            if self.tool_selection_mode.itemData(i) == current_mode:
                self.tool_selection_mode.setCurrentIndex(i)
                break
        self.tool_selection_mode.setToolTip("Applies only to drawing tools in the tools container.")
        layout.addRow("Tool Selection Mode:", self.tool_selection_mode)

        self.wm_font_size = QSpinBox()
        self.wm_font_size.setRange(0, 300)
        self.wm_font_size.setValue(self._s.get("watermark_font_size", 0))
        self.wm_font_size.setToolTip("0 = auto size")
        layout.addRow("Watermark Font Size:", self.wm_font_size)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._apply)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(apply_btn)
        btn_row.addWidget(cancel_btn)
        layout.addRow(btn_row)

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
            "candle_width": self.candle_width.value(),
            "candle_spacing": self.candle_spacing.value(),
            "default_visible_candles": self.visible_candles.value(),
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
            "indicator_scale_labels_enabled": self.indicator_scale_labels_enabled.isChecked(),
            "crosshair_snap_enabled": self.crosshair_snap_enabled.isChecked(),
            "tool_selection_mode": self.tool_selection_mode.currentData(),
        }
        self.settings_changed.emit(new)
        self.accept()

    # ─── Styles ───────────────────────────────────────────────────────────────

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            QDialog { background-color: #141414; color: #d8d8d8; border: 1px solid #2e2e2e; }
            QLabel { color: #d0d0d0; font-size: 12px; background: transparent; }
            QSpinBox, QDoubleSpinBox, QComboBox {
                background-color: #1e1e1e; color: #d8d8d8;
                border: 1px solid #363636; border-radius: 3px; padding: 2px 4px;
            }
            QSpinBox::up-button, QSpinBox::down-button,
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 16px; background-color: #2a2a2a; border-left: 1px solid #363636;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover { background-color: #333; }
            QComboBox QAbstractItemView { background-color: #1e1e1e; color: #d8d8d8; }
            QPushButton {
                background-color: #0d5a99; color: #e8e8e8;
                border: 1px solid #1070bb; border-radius: 3px;
                padding: 5px 12px; font-weight: 600;
            }
            QPushButton:hover { background-color: #1070cc; }
            QPushButton:pressed { background-color: #0a4a80; }
            QCheckBox { color: #d0d0d0; }
            QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #444; border-radius: 2px; }
            QCheckBox::indicator:checked { background-color: #1070cc; }
        """)
