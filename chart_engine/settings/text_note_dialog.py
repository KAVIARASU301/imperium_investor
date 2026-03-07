# chart_engine/settings/text_note_dialog.py
#
# Frameless dialog that pops up when the user adds or edits a text note
# drawing on the chart. Returns text content, color, and font size.

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)


class TextNoteDialog(QDialog):
    """Frameless dialog for creating or editing a text annotation on the chart."""

    def __init__(self, parent=None, text: str = "", color: str = "#FFFFFF", size: int = 12):
        super().__init__(parent)
        self.setWindowTitle("Add / Edit Text Note")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setMinimumSize(300, 150)

        self.text = text
        self.color = color
        self.size = size

        self._build_ui()
        self._apply_styles()

    # ─── Build ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.text_edit = QTextEdit()
        self.text_edit.setText(self.text)
        layout.addWidget(self.text_edit)

        opts = QHBoxLayout()

        self.color_button = QPushButton("Color")
        self.color_button.clicked.connect(self._choose_color)
        self.color_button.setStyleSheet(f"background-color: {self.color};")
        opts.addWidget(self.color_button)

        self.size_spinbox = QSpinBox()
        self.size_spinbox.setRange(8, 24)
        self.size_spinbox.setValue(self.size)
        self.size_spinbox.setSuffix("px")
        opts.addWidget(self.size_spinbox)

        layout.addLayout(opts)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.color), self, "Choose Text Color")
        if color.isValid():
            self.color = color.name()
            self.color_button.setStyleSheet(f"background-color: {self.color};")

    def accept(self) -> None:  # type: ignore[override]
        self.text = self.text_edit.toPlainText()
        self.size = self.size_spinbox.value()
        super().accept()

    # ─── Styles ───────────────────────────────────────────────────────────────

    def _apply_styles(self) -> None:
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e1e;
                border: 1px solid #383838;
            }
            QTextEdit {
                background-color: #2a2a2a;
                color: #e8e8e8;
                border: 1px solid #444;
                border-radius: 3px;
            }
            QPushButton, QSpinBox {
                background-color: #2e2e2e;
                color: #e0e0e0;
                border: 1px solid #484848;
                padding: 4px 8px;
                border-radius: 3px;
            }
            QPushButton:hover { border-color: #00d4ff; color: #00d4ff; }
        """)
