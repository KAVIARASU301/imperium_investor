from PySide6.QtCore import QObject


class ThemeManager(QObject):
    """Manages the application's theme."""

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self._load_themes()

    def _load_themes(self):
        # In a real application, you would load stylesheets from files
        self.light_theme = ""  # Default Qt style
        self.dark_theme = """
            QWidget {
                background-color: #2b2b2b;
                color: #ffffff;
            }
            QHeaderView::section {
                background-color: #3c3f41;
                color: #ffffff;
            }
            /* Add more specific dark theme styles */
        """

    def set_theme(self, is_dark):
        if is_dark:
            self.app.setStyleSheet(self.dark_theme)
        else:
            self.app.setStyleSheet(self.light_theme)