import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget, QGroupBox,
    QGridLayout, QLabel, QLineEdit, QSpinBox, QCheckBox, QPushButton, QMessageBox
)
from PySide6.QtCore import Qt, Signal

from utils.config_manager import ConfigManager
from utils.token_manager import TokenManager

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """
    A modern, frameless settings dialog for configuring the application.
    It allows users to manage trading defaults, display preferences, and API credentials.
    """
    settings_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config_manager = ConfigManager()
        self.token_manager = TokenManager()
        self._drag_pos = None

        self._setup_window()
        self._setup_ui()
        self._load_settings()
        self._apply_styles()

    def _setup_window(self):
        """Initializes window properties."""
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(550)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def _setup_ui(self):
        """Builds the main layout and widgets for the dialog."""
        container = QWidget(self)
        container.setObjectName("mainContainer")

        # Enable window dragging
        container.mousePressEvent = self.mousePressEvent
        container.mouseMoveEvent = self.mouseMoveEvent
        container.mouseReleaseEvent = self.mouseReleaseEvent

        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 15, 20, 20)
        layout.setSpacing(15)

        layout.addLayout(self._create_header())

        tabs = QTabWidget(objectName="mainTabs")
        tabs.addTab(self._create_trading_tab(), "TRADING")
        tabs.addTab(self._create_display_tab(), "DISPLAY & API")
        layout.addWidget(tabs)

        layout.addLayout(self._create_action_buttons())

        dialog_layout = QVBoxLayout(self)
        dialog_layout.setContentsMargins(0, 0, 0, 0)
        dialog_layout.addWidget(container)

    def _create_header(self) -> QHBoxLayout:
        """Creates the custom title bar."""
        header_layout = QHBoxLayout()
        title = QLabel("Application Settings")
        title.setObjectName("dialogTitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.clicked.connect(self.close)

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignTop)
        return header_layout

    def _create_action_buttons(self) -> QHBoxLayout:
        """Creates the Save, Cancel, and Reset buttons."""
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 10, 0, 0)

        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.setObjectName("secondaryButton")
        reset_btn.clicked.connect(self._reset_to_defaults)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondaryButton")
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("Save Settings")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._save_settings)

        button_layout.addWidget(reset_btn)
        button_layout.addStretch()
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(save_btn)
        return button_layout

    def _create_trading_tab(self) -> QWidget:
        """Creates the tab for general trading settings."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 20, 15, 15)

        defaults_group = QGroupBox("Default Order Values")
        defaults_layout = QGridLayout(defaults_group)
        defaults_layout.setSpacing(12)

        # Default Quantity for stock orders
        defaults_layout.addWidget(QLabel("Default Quantity:"), 0, 0)
        self.default_quantity = QSpinBox()
        self.default_quantity.setRange(1, 10000)
        self.default_quantity.setSingleStep(10)
        defaults_layout.addWidget(self.default_quantity, 0, 1)

        layout.addWidget(defaults_group)
        layout.addStretch()
        return tab

    def _create_display_tab(self) -> QWidget:
        """Creates the tab for display and API settings."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(15, 20, 15, 15)
        layout.setSpacing(20)

        # API Configuration Group
        api_group = QGroupBox("API Credentials")
        api_grid = QGridLayout(api_group)
        api_grid.setSpacing(12)

        api_grid.addWidget(QLabel("API Key:"), 0, 0)
        self.api_key = QLineEdit(echoMode=QLineEdit.EchoMode.Password)
        api_grid.addWidget(self.api_key, 0, 1)

        api_grid.addWidget(QLabel("API Secret:"), 1, 0)
        self.api_secret = QLineEdit(echoMode=QLineEdit.EchoMode.Password)
        api_grid.addWidget(self.api_secret, 1, 1)

        self.save_credentials = QCheckBox("Save credentials securely on this machine")
        api_grid.addWidget(self.save_credentials, 2, 0, 1, 2)

        layout.addWidget(api_group)
        layout.addStretch()
        return tab

    def _apply_styles(self):
        """Applies a consistent, modern dark theme stylesheet."""
        self.setStyleSheet("""
            #mainContainer {
                background-color: #1c1c2e;
                border: 1px solid #3a3a5a;
                border-radius: 12px;
                font-family: "Segoe UI", sans-serif;
            }
            #dialogTitle { color: #e0e0e0; font-size: 18px; font-weight: 600; }
            #closeButton {
                background-color: transparent; border: none; color: #8a8a9e;
                font-size: 16px; font-weight: bold;
            }
            #closeButton:hover { color: #d63031; }

            QTabWidget::pane { border: none; }
            QTabBar::tab {
                background: transparent; color: #8a8a9e; font-weight: bold;
                padding: 10px 22px; margin-right: 4px;
                border-bottom: 2px solid transparent;
            }
            QTabBar::tab:selected, QTabBar::tab:hover {
                color: #ffffff;
                border-bottom: 2px solid #00b894;
            }

            QGroupBox {
                color: #b2bec3; border: 1px solid #2a2a4a; border-radius: 8px;
                font-size: 11px; margin-top: 10px; padding: 15px; font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 10px; padding: 0 5px;
            }

            QLabel { color: #b2bec3; font-size: 13px; }
            QLineEdit, QSpinBox, QComboBox {
                background-color: #2a2a4a; border: 1px solid #3a3a5a;
                color: #e0e0e0; padding: 8px; border-radius: 6px; font-size: 13px;
            }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
                border: 1px solid #00b894;
            }
            QComboBox::drop-down { border: none; }

            QCheckBox { color: #b2bec3; spacing: 8px; }
            QCheckBox::indicator {
                width: 16px; height: 16px; border-radius: 4px;
                background-color: #2a2a4a; border: 1px solid #3a3a5a;
            }
            QCheckBox::indicator:checked { background-color: #00b894; }

            QPushButton {
                font-weight: bold; border-radius: 6px;
                padding: 10px 18px; border: none; font-size: 13px;
            }
            #secondaryButton { background-color: #3a3a5a; color: #e0e0e0; }
            #secondaryButton:hover { background-color: #4a4a6a; }
            #primaryButton { background-color: #00b894; color: #ffffff; }
            #primaryButton:hover { background-color: #00d2a2; }
        """)

    def _load_settings(self):
        """Loads settings from config and token managers into the UI controls."""
        settings = self.config_manager.load_settings()
        self.default_quantity.setValue(settings.get('default_quantity', 10))

        creds = self.token_manager.load_credentials()
        if creds:
            self.api_key.setText(creds.get('api_key', ''))
            self.api_secret.setText(creds.get('api_secret', ''))
            self.save_credentials.setChecked(True)

    def _save_settings(self):
        """Saves the current UI settings to their respective managers."""
        try:
            settings = {'default_quantity': self.default_quantity.value()}
            self.config_manager.save_settings(settings)

            if self.save_credentials.isChecked() and self.api_key.text() and self.api_secret.text():
                self.token_manager.save_credentials(self.api_key.text(), self.api_secret.text())

            logger.info("Settings saved successfully.")
            self.settings_changed.emit(settings)
            self.accept()
        except Exception as e:
            logger.error(f"Failed to save settings: {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"Could not save settings: {e}")

    def _reset_to_defaults(self):
        """Resets all settings in the dialog to their default values."""
        reply = QMessageBox.question(
            self, "Confirm Reset",
            "Are you sure you want to reset all settings to their defaults?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            defaults = self.config_manager.get_default_settings()
            self.default_quantity.setValue(defaults.get('default_quantity', 10))
            self.api_key.clear()
            self.api_secret.clear()
            self.save_credentials.setChecked(False)
            logger.info("Settings have been reset to default values.")

    # --- Window Dragging Handlers ---
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()
