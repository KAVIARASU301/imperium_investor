# src/login_manager.py
import logging
import webbrowser
from typing import Optional, Dict

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox, QWidget, QStackedWidget,
    QCheckBox, QFrame
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from kiteconnect import KiteConnect
from src.token_manager import TokenManager

logger = logging.getLogger(__name__)


class LoginWorker(QThread):
    """Background worker for API authentication."""
    success = Signal(str)
    error = Signal(str)

    def __init__(self, api_key: str, api_secret: str, request_token: str):
        super().__init__()
        self.api_key = api_key
        self.api_secret = api_secret
        self.request_token = request_token

    def run(self):
        try:
            kite = KiteConnect(api_key=self.api_key)
            data = kite.generate_session(self.request_token, api_secret=self.api_secret)
            self.success.emit(data.get('access_token'))
        except Exception as e:
            self.error.emit(str(e))


class LoginManager(QDialog):
    """A professional, self-contained, all-in-one login dialog with a premium UI."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)

        self.token_manager = TokenManager()
        self.api_key = ""
        self.api_secret = ""
        self.access_token = None
        self.trading_mode = 'live'

        self.setWindowTitle("Options Scalper Pro - Authentication")
        self.setMinimumSize(420, 450)
        self.setModal(True)
        # --- Make window frameless for custom styling ---
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._drag_pos = None

        self.countdown_timer = QTimer(self)
        self.countdown_timer.timeout.connect(self._update_countdown)
        self.countdown_value = 5

        self._setup_ui()
        self._apply_styles()

        QTimer.singleShot(100, self._try_auto_login)

    def _setup_ui(self):
        # Main container for rounded corners and background
        container = QWidget(self)
        container.setObjectName("mainContainer")

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.addWidget(container)

        # Main layout for the container widget
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(25, 20, 25, 25)
        container_layout.setSpacing(15)

        # App Title
        app_title = QLabel("Options Scalper Pro")
        app_title.setObjectName("appTitle")
        container_layout.addWidget(app_title, 0, Qt.AlignmentFlag.AlignCenter)

        # Separator Line
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setObjectName("divider")
        container_layout.addWidget(divider)

        self.stacked_widget = QStackedWidget()
        container_layout.addWidget(self.stacked_widget)

        self.stacked_widget.addWidget(self._create_auto_login_page())
        self.stacked_widget.addWidget(self._create_credential_input_page())
        self.stacked_widget.addWidget(self._create_token_input_page())

    def _create_auto_login_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        status_label = QLabel("Valid Session Found")
        status_label.setObjectName("dialogTitle")
        self.countdown_label = QLabel(f"Starting in {self.countdown_value} seconds...")
        self.countdown_label.setObjectName("infoLabel")

        live_button = QPushButton("Start Live Trading")
        live_button.setObjectName("primaryButton")
        live_button.clicked.connect(lambda: self._select_mode_and_accept('live'))

        paper_button = QPushButton("Start Paper Trading")
        paper_button.setObjectName("secondaryButton")
        paper_button.clicked.connect(lambda: self._select_mode_and_accept('paper'))

        cancel_button = QPushButton("Logout & Enter Credentials")
        cancel_button.setObjectName("linkButton")
        cancel_button.setCursor(Qt.PointingHandCursor)
        cancel_button.clicked.connect(self._cancel_auto_login)

        layout.addWidget(status_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.countdown_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)
        layout.addWidget(live_button)
        layout.addWidget(paper_button)
        layout.addSpacing(10)
        layout.addWidget(cancel_button, 0, Qt.AlignmentFlag.AlignCenter)
        return page

    def _create_credential_input_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 10, 0, 0)

        title = QLabel("Kite API Credentials")
        title.setObjectName("dialogTitle")
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(QLabel("API Key:"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("Enter your API Key")
        layout.addWidget(self.api_key_input)

        layout.addWidget(QLabel("API Secret:"))
        self.api_secret_input = QLineEdit()
        self.api_secret_input.setPlaceholderText("Enter your API Secret")
        self.api_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.api_secret_input)

        self.save_creds_checkbox = QCheckBox("Save Credentials Securely")
        self.save_creds_checkbox.setChecked(True)
        layout.addWidget(self.save_creds_checkbox)
        layout.addStretch()

        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        paper_button = QPushButton("Paper Trading")
        paper_button.setObjectName("secondaryButton")
        live_button = QPushButton("Live Trading")
        live_button.setObjectName("primaryButton")

        button_layout.addWidget(paper_button)
        button_layout.addWidget(live_button)
        layout.addLayout(button_layout)

        live_button.clicked.connect(lambda: self._on_mode_selected('live'))
        paper_button.clicked.connect(lambda: self._on_mode_selected('paper'))
        return page

    def _create_token_input_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)

        token_title = QLabel("Complete Authentication")
        token_title.setObjectName("dialogTitle")

        token_info = QLabel("After logging in, copy the 'request_token' from your browser's URL and paste it below.")
        token_info.setWordWrap(True)

        self.request_token_input = QLineEdit()
        self.request_token_input.setPlaceholderText("Paste request_token here...")

        self.generate_button = QPushButton("Generate Session")
        self.generate_button.setObjectName("primaryButton")
        self.generate_button.clicked.connect(self._on_complete_login)

        layout.addWidget(token_title, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(token_info)
        layout.addWidget(self.request_token_input)
        layout.addStretch()
        layout.addWidget(self.generate_button)
        return page

    # Add mouse events to make the frameless window draggable
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

    # --- Methods for logic (no changes needed) ---
    def _try_auto_login(self):
        creds = self.token_manager.load_credentials()
        if creds:
            self.api_key = creds.get('api_key', '')
            self.api_secret = creds.get('api_secret', '')
            self.api_key_input.setText(self.api_key)
            self.api_secret_input.setText(self.api_secret)

        token_data = self.token_manager.load_token_data()
        if token_data and token_data.get('access_token') and self.api_key:
            self.access_token = token_data['access_token']
            self.trading_mode = token_data.get('trading_mode', 'live')
            self.stacked_widget.setCurrentIndex(0)
            self.countdown_timer.start(1000)
        else:
            self.stacked_widget.setCurrentIndex(1)

    def _update_countdown(self):
        if self.countdown_value > 0:
            self.countdown_label.setText(f"Starting in {self.countdown_value} seconds...")
            self.countdown_value -= 1
        else:
            self.countdown_timer.stop()
            self.accept()

    def _cancel_auto_login(self):
        self.countdown_timer.stop()
        self.token_manager.clear_token_data()
        self.access_token = None
        self.stacked_widget.setCurrentIndex(1)

    def _on_mode_selected(self, mode: str):
        self.trading_mode = mode
        self.api_key = self.api_key_input.text().strip()
        self.api_secret = self.api_secret_input.text().strip()

        if not (self.api_key and self.api_secret):
            QMessageBox.warning(self, "Input Error", "API Key and Secret cannot be empty.")
            return

        if self.save_creds_checkbox.isChecked():
            self.token_manager.save_credentials(self.api_key, self.api_secret)

        try:
            kite = KiteConnect(api_key=self.api_key)
            webbrowser.open_new(kite.login_url())
            self.stacked_widget.setCurrentIndex(2)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not initiate login: {e}")

    def _on_complete_login(self):
        request_token = self.request_token_input.text().strip()
        if not request_token:
            QMessageBox.warning(self, "Input Error", "Request token is empty.")
            return

        self.worker = LoginWorker(self.api_key, self.api_secret, request_token)
        self.worker.success.connect(self._on_login_success)
        self.worker.error.connect(self._on_login_error)
        self.worker.start()
        self.generate_button.setText("Generating...")
        self.generate_button.setEnabled(False)

    def _on_login_success(self, access_token: str):
        self.access_token = access_token
        self.token_manager.save_token_data({
            'access_token': access_token,
            'trading_mode': self.trading_mode
        })
        self.accept()

    def _on_login_error(self, error_msg: str):
        QMessageBox.critical(self, "Login Failed", f"Failed to generate session:\n{error_msg}")
        self.stacked_widget.setCurrentIndex(1)
        self.generate_button.setText("Generate Session")
        self.generate_button.setEnabled(True)

    def _select_mode_and_accept(self, mode: str):
        self.countdown_timer.stop()
        self.trading_mode = mode
        logger.info(f"User selected {mode.upper()} mode during auto-login.")
        self.accept()

    def get_api_creds(self) -> Optional[Dict[str, str]]:
        if self.api_key and self.api_secret:
            return {"api_key": self.api_key, "api_secret": self.api_secret}
        return None

    def get_access_token(self) -> Optional[str]:
        return self.access_token

    def get_trading_mode(self) -> Optional[str]:
        return self.trading_mode

    def _apply_styles(self):
        """Applies a premium, modern dark theme."""
        self.setStyleSheet("""
            #mainContainer {
                background-color: #161A25;
                border: 1px solid #3A4458;
                border-radius: 12px;
                font-family: "Segoe UI", sans-serif;
            }
            #appTitle {
                font-size: 24px;
                font-weight: 300; /* Lighter font weight */
                color: #E0E0E0;
                padding-bottom: 5px;
            }
            #dialogTitle {
                font-size: 18px;
                font-weight: 600; /* Bolder */
                color: #FFFFFF;
                padding-bottom: 15px;
            }
            #infoLabel {
                color: #8A9BA8; /* Muted text color */
                font-size: 13px;
            }
            #divider {
                background-color: #3A4458;
                height: 1px;
            }
            QLabel {
                color: #A9B1C3; /* Standard label color */
                font-size: 13px;
            }
            QLineEdit {
                background-color: #212635;
                border: 1px solid #3A4458;
                border-radius: 6px;
                color: #E0E0E0;
                font-size: 14px;
                padding: 10px;
            }
            QLineEdit:focus {
                border: 1px solid #29C7C9; /* Highlight color */
            }
            QCheckBox {
                color: #A9B1C3;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                background-color: #2A3140;
                border: 1px solid #3A4458;
            }
            QCheckBox::indicator:checked {
                background-color: #29C7C9;
                image: url(check.png); /* You might need a checkmark icon */
            }
            QPushButton {
                font-weight: bold;
                border-radius: 6px;
                padding: 12px;
                font-size: 14px;
            }
            #primaryButton {
                background-color: #29C7C9;
                color: #161A25;
                border: none;
            }
            #primaryButton:hover {
                background-color: #32E0E3;
            }
            #secondaryButton {
                background-color: transparent;
                color: #A9B1C3;
                border: 1px solid #3A4458;
            }
            #secondaryButton:hover {
                background-color: #212635;
                border-color: #A9B1C3;
            }
            #linkButton {
                color: #8A9BA8;
                border: none;
                font-weight: normal;
                font-size: 12px;
                text-decoration: underline;
            }
            #linkButton:hover {
                color: #E0E0E0;
            }
        """)