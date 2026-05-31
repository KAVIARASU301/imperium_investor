# chart_engine/settings/text_note_dialog.py
#
# Frameless TC2000-style dialog for creating/editing chart text notes.
# Returns text content, color, and font size.

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QCursor, QMouseEvent
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class _C:
    BG0 = "#050709"
    BG1 = "#0a0d12"
    BG2 = "#0f1318"
    BG3 = "#141920"
    BG4 = "#1a2030"
    BORDER = "#263247"

    TEXT = "#e8f0ff"
    MUTED = "#8292a8"
    DIM = "#5a7090"
    AMBER = "#f5d76e"
    CYAN = "#00d4ff"
    BULL = "#00d4a8"
    BEAR = "#ff4d6a"


_SANS = "Inter, 'Segoe UI Variable', 'Segoe UI', Roboto, Arial, sans-serif"
_TEXT_NOTE_SWATCHES = (
    "#f5d76e",  # TC2000-style terminal yellow
    "#e8f0ff",
    "#a8bcd4",
    "#00d4ff",
    "#00d4a8",
    "#ff4d6a",
    "#f59e0b",
    "#b18cff",
)


class TextNoteDialog(QDialog):
    """Create or edit a chart text annotation."""

    def __init__(self, parent=None, text: str = "", color: str = "#f5d76e", size: int = 12):
        super().__init__(parent)
        self.setWindowTitle("Text Note")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setModal(True)
        self.setMinimumSize(380, 260)
        self.resize(420, 292)

        self.text = text
        self.color = self._valid_color(color, "#f5d76e")
        self.size = max(9, min(32, int(size or 12)))
        self._drag_active = False
        self._drag_offset = QPoint()

        self._build_ui()
        self._apply_styles()
        self._apply_color_state()

    # ─── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)

        self._container = QFrame()
        self._container.setObjectName("textNoteContainer")
        outer.addWidget(self._container)

        root = QVBoxLayout(self._container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_title_bar())

        body = QFrame()
        body.setObjectName("textNoteBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(10, 10, 10, 8)
        body_layout.setSpacing(8)

        note_lbl = QLabel("NOTE TEXT")
        note_lbl.setObjectName("sectionLabel")
        body_layout.addWidget(note_lbl)

        self.text_edit = QTextEdit()
        self.text_edit.setObjectName("noteEditor")
        self.text_edit.setPlainText(self.text)
        self.text_edit.setPlaceholderText("Type chart note…")
        body_layout.addWidget(self.text_edit, 1)

        style_panel = QFrame()
        style_panel.setObjectName("stylePanel")
        style_layout = QVBoxLayout(style_panel)
        style_layout.setContentsMargins(9, 8, 9, 8)
        style_layout.setSpacing(7)

        color_row = QHBoxLayout()
        color_row.setContentsMargins(0, 0, 0, 0)
        color_row.setSpacing(6)

        color_label = QLabel("TEXT COLOR")
        color_label.setObjectName("fieldLabel")
        color_row.addWidget(color_label)

        self.color_button = QPushButton()
        self.color_button.setObjectName("colorPickButton")
        self.color_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.color_button.setFixedHeight(24)
        self.color_button.clicked.connect(self._choose_color)
        color_row.addWidget(self.color_button, 1)

        swatch_wrap = QWidget()
        swatch_wrap.setObjectName("swatchWrap")
        swatch_layout = QHBoxLayout(swatch_wrap)
        swatch_layout.setContentsMargins(0, 0, 0, 0)
        swatch_layout.setSpacing(3)
        self._swatch_buttons: list[QPushButton] = []
        for swatch in _TEXT_NOTE_SWATCHES:
            btn = QPushButton()
            btn.setObjectName("colorSwatch")
            btn.setFixedSize(18, 18)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setToolTip(swatch.upper())
            btn.clicked.connect(lambda _checked=False, c=swatch: self._set_color(c))
            self._swatch_buttons.append(btn)
            swatch_layout.addWidget(btn)
        color_row.addWidget(swatch_wrap)
        style_layout.addLayout(color_row)

        size_row = QHBoxLayout()
        size_row.setContentsMargins(0, 0, 0, 0)
        size_row.setSpacing(6)
        size_label = QLabel("FONT SIZE")
        size_label.setObjectName("fieldLabel")
        size_row.addWidget(size_label)
        self.size_spinbox = QSpinBox()
        self.size_spinbox.setObjectName("noteSpin")
        self.size_spinbox.setRange(9, 32)
        self.size_spinbox.setValue(self.size)
        self.size_spinbox.setSuffix(" px")
        size_row.addWidget(self.size_spinbox)
        size_row.addStretch()
        style_layout.addLayout(size_row)

        body_layout.addWidget(style_panel)
        root.addWidget(body, 1)
        root.addWidget(self._build_footer())

    def _build_title_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("textNoteTitleBar")
        bar.setFixedHeight(30)
        bar.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 0, 6, 0)
        layout.setSpacing(6)

        title = QLabel("TEXT NOTE")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("TC2000-style chart annotation")
        subtitle.setObjectName("dialogSubtitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self.reject)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addStretch()
        layout.addWidget(close_btn)

        bar.mousePressEvent = self._title_mouse_press
        bar.mouseMoveEvent = self._title_mouse_move
        bar.mouseReleaseEvent = self._title_mouse_release
        return bar

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("textNoteFooter")
        footer.setFixedHeight(42)

        layout = QHBoxLayout(footer)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        hint = QLabel("Enter = new line · Save applies to selected chart text")
        hint.setObjectName("statusLabel")

        cancel = QPushButton("CANCEL")
        cancel.setObjectName("secondaryButton")
        cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel.setFixedHeight(26)
        cancel.clicked.connect(self.reject)

        save = QPushButton("SAVE")
        save.setObjectName("primaryButton")
        save.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        save.setFixedHeight(26)
        save.clicked.connect(self.accept)

        layout.addWidget(hint)
        layout.addStretch()
        layout.addWidget(cancel)
        layout.addWidget(save)
        return footer

    # ─── Color handling ─────────────────────────────────────────────────────

    @staticmethod
    def _valid_color(value: str, fallback: str) -> str:
        color = QColor(value or fallback)
        return color.name() if color.isValid() else QColor(fallback).name()

    def _set_color(self, color: str) -> None:
        self.color = self._valid_color(color, self.color)
        self._apply_color_state()

    def _choose_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.color), self, "Choose Text Color")
        if color.isValid():
            self.color = color.name()
            self._apply_color_state()

    def _apply_color_state(self) -> None:
        self.color = self._valid_color(self.color, "#f5d76e")
        self.color_button.setText(f"{self.color.upper()}  ·  PICK CUSTOM")
        self.color_button.setStyleSheet(f"""
            QPushButton#colorPickButton {{
                background: {_C.BG1};
                color: {_C.TEXT};
                border: 1px solid {self.color};
                border-left: 5px solid {self.color};
                border-radius: 2px;
                font-family: {_SANS};
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 0.65px;
                padding: 0 8px;
                text-align: left;
            }}
            QPushButton#colorPickButton:hover {{
                background: {_C.BG3};
                border-color: {self.color};
                border-left: 5px solid {self.color};
            }}
        """)
        selected = QColor(self.color).name().lower()
        for btn, swatch in zip(self._swatch_buttons, _TEXT_NOTE_SWATCHES):
            is_active = QColor(swatch).name().lower() == selected
            btn.setStyleSheet(f"""
                QPushButton#colorSwatch {{
                    background: {swatch};
                    border: {'2px solid #e8f0ff' if is_active else '1px solid #263247'};
                    border-radius: 2px;
                }}
                QPushButton#colorSwatch:hover {{
                    border: 2px solid #00d4ff;
                }}
            """)

    # ─── Result ─────────────────────────────────────────────────────────────

    def accept(self) -> None:  # type: ignore[override]
        self.text = self.text_edit.toPlainText().strip()
        self.color = self._valid_color(self.color, "#f5d76e")
        self.size = int(self.size_spinbox.value())
        super().accept()

    # ─── Drag ───────────────────────────────────────────────────────────────

    def _title_mouse_press(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _title_mouse_move(self, event: QMouseEvent) -> None:
        if self._drag_active and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def _title_mouse_release(self, _event: QMouseEvent) -> None:
        self._drag_active = False

    # ─── Styles ─────────────────────────────────────────────────────────────

    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
        TextNoteDialog {{
            background: {_C.BG0};
        }}
        QFrame#textNoteContainer {{
            background: {_C.BG1};
            border: 1px solid {_C.BG4};
            border-radius: 2px;
        }}
        QFrame#textNoteTitleBar,
        QFrame#textNoteFooter {{
            background: {_C.BG0};
        }}
        QFrame#textNoteTitleBar {{
            border-bottom: 1px solid {_C.BG4};
        }}
        QFrame#textNoteFooter {{
            border-top: 1px solid {_C.BG4};
        }}
        QFrame#textNoteBody {{
            background: {_C.BG1};
        }}
        QFrame#stylePanel {{
            background: {_C.BG2};
            border: 1px solid {_C.BG4};
            border-radius: 2px;
        }}
        QLabel#dialogTitle {{
            color: {_C.AMBER};
            font-family: {_SANS};
            font-size: 10px;
            font-weight: 900;
            letter-spacing: 1.25px;
            background: transparent;
        }}
        QLabel#dialogSubtitle,
        QLabel#statusLabel {{
            color: {_C.DIM};
            font-family: {_SANS};
            font-size: 10px;
            font-weight: 600;
            background: transparent;
        }}
        QLabel#sectionLabel,
        QLabel#fieldLabel {{
            color: {_C.DIM};
            font-family: {_SANS};
            font-size: 9px;
            font-weight: 900;
            letter-spacing: 1px;
            background: transparent;
        }}
        QTextEdit#noteEditor {{
            background: {_C.BG2};
            color: {_C.TEXT};
            selection-background-color: #1a2840;
            selection-color: {_C.TEXT};
            border: 1px solid {_C.BG4};
            border-radius: 2px;
            padding: 8px;
            font-family: {_SANS};
            font-size: 12px;
            font-weight: 650;
        }}
        QTextEdit#noteEditor:focus {{
            border: 1px solid {_C.BORDER};
            background: #111722;
        }}
        QSpinBox#noteSpin {{
            background: {_C.BG1};
            color: {_C.TEXT};
            border: 1px solid {_C.BG4};
            border-radius: 2px;
            min-height: 22px;
            padding: 0 8px;
            font-family: {_SANS};
            font-size: 11px;
            font-weight: 750;
        }}
        QSpinBox#noteSpin::up-button,
        QSpinBox#noteSpin::down-button {{
            width: 0;
            border: none;
        }}
        QPushButton#closeButton {{
            background: transparent;
            color: {_C.DIM};
            border: none;
            border-radius: 2px;
            font-size: 12px;
            font-weight: 900;
        }}
        QPushButton#closeButton:hover {{
            background: rgba(255,77,106,0.15);
            color: {_C.BEAR};
        }}
        QPushButton#primaryButton,
        QPushButton#secondaryButton {{
            border-radius: 2px;
            font-family: {_SANS};
            font-size: 10px;
            font-weight: 900;
            letter-spacing: 0.8px;
            padding: 0 14px;
        }}
        QPushButton#primaryButton {{
            background: rgba(0,212,168,0.12);
            color: {_C.BULL};
            border: 1px solid rgba(0,212,168,0.35);
        }}
        QPushButton#primaryButton:hover {{
            background: rgba(0,212,168,0.18);
            border-color: {_C.BULL};
        }}
        QPushButton#secondaryButton {{
            background: {_C.BG2};
            color: {_C.MUTED};
            border: 1px solid {_C.BG4};
        }}
        QPushButton#secondaryButton:hover {{
            background: {_C.BG3};
            color: {_C.TEXT};
        }}
        """)