from typing import Dict, Any

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QFormLayout,
    QPushButton,
    QColorDialog,
    QDialogButtonBox,
    QCheckBox,
    QGroupBox,
)
from PySide6.QtGui import QColor


class ColorSettingsDialog(QDialog):
    def __init__(self, current_theme: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Color Settings")
        self.setModal(True)
        self._theme = current_theme
        self._buttons: Dict[str, QPushButton] = {}

        layout = QVBoxLayout(self)

        self.link_checkbox = QCheckBox("Use same green/red colors across candles, volume, and tables")
        self.link_checkbox.setChecked(bool(self._theme.get("link_all_sections", True)))
        self.link_checkbox.toggled.connect(self._sync_linked_state)
        layout.addWidget(self.link_checkbox)

        self.table_color_toggle_checkbox = QCheckBox("Enable directional colors in scanner/watchlist/positions tables")
        self.table_color_toggle_checkbox.setChecked(bool(self._theme.get("enable_table_directional_colors", False)))
        layout.addWidget(self.table_color_toggle_checkbox)

        candle_group = QGroupBox("Candles")
        candle_form = QFormLayout(candle_group)
        candle_form.addRow("Green candle", self._build_color_button("candles.up", self._theme["candles"]["up"]))
        candle_form.addRow("Red candle", self._build_color_button("candles.down", self._theme["candles"]["down"]))
        layout.addWidget(candle_group)

        volume_group = QGroupBox("Volume")
        volume_form = QFormLayout(volume_group)
        volume_form.addRow("Up volume", self._build_color_button("volume.up", self._theme["volume"]["up"]))
        volume_form.addRow("Down volume", self._build_color_button("volume.down", self._theme["volume"]["down"]))
        layout.addWidget(volume_group)

        table_group = QGroupBox("Scanner / Watchlist / Positions")
        table_form = QFormLayout(table_group)
        table_form.addRow("Positive", self._build_color_button("tables.positive", self._theme["tables"]["positive"]))
        table_form.addRow("Negative", self._build_color_button("tables.negative", self._theme["tables"]["negative"]))
        table_form.addRow("Neutral", self._build_color_button("tables.neutral", self._theme["tables"]["neutral"]))
        table_form.addRow("Volume text", self._build_color_button("tables.volume", self._theme["tables"]["volume"]))
        layout.addWidget(table_group)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._sync_linked_state(self.link_checkbox.isChecked())

    def _build_color_button(self, key: str, value: str) -> QPushButton:
        btn = QPushButton(value.upper())
        btn.clicked.connect(lambda: self._pick_color(key))
        self._buttons[key] = btn
        self._set_button_color(btn, value)
        return btn

    def _set_button_color(self, button: QPushButton, color_hex: str):
        button.setText(color_hex.upper())
        button.setStyleSheet(f"background-color: {color_hex}; color: #111; border: 1px solid #444; padding: 6px;")

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
        return self._theme
