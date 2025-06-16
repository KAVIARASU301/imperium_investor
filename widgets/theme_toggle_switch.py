from PySide6.QtWidgets import QCheckBox
from PySide6.QtCore import Qt

class ThemeToggleSwitch(QCheckBox):
    """A simple toggle switch for UI themes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setText("Dark Mode")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QCheckBox::indicator {
                width: 40px;
                height: 20px;
            }
            QCheckBox::indicator:unchecked {
                image: url(icons/toggle_off.png);
            }
            QCheckBox::indicator:checked {
                image: url(icons/toggle_on.png);
            }
        """)