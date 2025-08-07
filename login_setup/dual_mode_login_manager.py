# login_setup/dual_mode_login_manager.py
"""
Modern, visually appealing dual-mode login manager.
This version fixes the auto-login cancellation bug.
"""

import logging
import webbrowser
from datetime import datetime
from typing import Optional, Dict, Any

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QMessageBox, QWidget, QStackedWidget, QCheckBox, QFrame,
    QRadioButton, QComboBox, QSpinBox
)
from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QMouseEvent

try:
    from kiteconnect import KiteConnect

    KITE_AVAILABLE = True
except ImportError:
    KITE_AVAILABLE = False

from login_setup.broker_modes import (
    BrokerMode, TradingMode, get_broker_config, get_display_config
)
from login_setup.enhanced_token_manager import EnhancedTokenManager
from login_setup.ibkr_auth import IBKRAuth, is_ibkr_available

logger = logging.getLogger(__name__)


class KiteLoginWorker(QThread):
    """Background worker for Kite session generation."""
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
            access_token = data.get('access_token')
            if access_token:
                self.success.emit(access_token)
            else:
                self.error.emit("Received empty access token from API.")
        except Exception as e:
            logger.error(f"Kite session error: {e}", exc_info=True)
            self.error.emit(str(e))


class DualModeLoginManager(QDialog):
    """A modern, multi-page login dialog with a professional UI."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.token_manager = EnhancedTokenManager()
        self.ibkr_auth = IBKRAuth()
        self.authentication_data: Dict[str, Any] = {}
        self.selected_broker: Optional[BrokerMode] = None
        self.selected_trading_mode: Optional[TradingMode] = None
        self.kite_api_key = ""
        self.kite_api_secret = ""
        self._drag_pos = None

        # **FIX: Variables to control the auto-login flow**
        self.auto_login_timer = QTimer(self)
        self.auto_login_timer.setSingleShot(True)
        self.auto_login_cancelled = False

        self._setup_window()
        self._setup_ui()
        self._apply_styles()

        self.ibkr_auth.connection_established.connect(self._on_ibkr_connection_success)
        self.ibkr_auth.status_updated.connect(self._on_ibkr_status_update)

        QTimer.singleShot(100, self._try_auto_login)

    def _setup_window(self):
        """Configure the main dialog window properties."""
        self.setWindowTitle("Swing Trader - Login")
        self.setMinimumSize(500, 650)
        self.setModal(True)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def _setup_ui(self):
        """Create the main UI structure and pages."""
        container = QFrame()
        container.setObjectName("mainContainer")
        container.mousePressEvent = self._handle_mouse_press
        container.mouseMoveEvent = self._handle_mouse_move
        container.mouseReleaseEvent = self._handle_mouse_release

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(30, 20, 30, 30)

        header = self._create_header()
        container_layout.addLayout(header)

        self.stacked_widget = QStackedWidget()
        container_layout.addWidget(self.stacked_widget)

        self.stacked_widget.addWidget(self._create_auto_login_page())
        self.stacked_widget.addWidget(self._create_broker_selection_page())
        self.stacked_widget.addWidget(self._create_kite_credentials_page())
        self.stacked_widget.addWidget(self._create_kite_token_page())
        self.stacked_widget.addWidget(self._create_ibkr_connection_page())

    def _create_header(self) -> QHBoxLayout:
        """Create the dialog's custom header."""
        header_layout = QHBoxLayout()
        title = QLabel("Swing Trader Login")
        title.setObjectName("dialogTitle")
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(30, 30)
        close_btn.clicked.connect(self.reject)
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(close_btn)
        return header_layout

    def _create_auto_login_page(self) -> QWidget:
        """Create the page shown during an auto-login attempt."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome_label = QLabel("Welcome Back")
        welcome_label.setObjectName("welcomeTitle")
        self.auto_login_status = QLabel("Attempting auto-login...")
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("secondaryButton")
        # **FIX: Connect cancel button to the correct slot**
        cancel_btn.clicked.connect(self._cancel_auto_login)
        layout.addWidget(welcome_label, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.auto_login_status, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        layout.addWidget(cancel_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        return page

    def _create_broker_selection_page(self) -> QWidget:
        # ... (This UI code is unchanged)
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Select Your Broker")
        title.setObjectName("pageTitle")
        broker_layout = QHBoxLayout()
        self.india_card = self._create_broker_card(BrokerMode.INDIA)
        self.america_card = self._create_broker_card(BrokerMode.AMERICA)
        broker_layout.addWidget(self.india_card)
        broker_layout.addWidget(self.america_card)
        mode_frame = self._create_trading_mode_selector()
        layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(broker_layout)
        layout.addWidget(mode_frame)
        return page

    def _create_broker_card(self, broker_mode: BrokerMode) -> QFrame:
        # ... (This UI code is unchanged)
        card = QFrame()
        card.setObjectName("brokerCard")
        card.mousePressEvent = lambda event: self._select_broker(broker_mode)
        layout = QVBoxLayout(card)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        display_config = get_display_config(broker_mode)
        broker_config = get_broker_config(broker_mode)
        flag = QLabel(display_config['flag_emoji'])
        flag.setStyleSheet("font-size: 48px; background: transparent;")
        name = QLabel(broker_config.display_name)
        name.setObjectName("brokerName")
        description = QLabel(display_config['description'])
        description.setObjectName("brokerDescription")
        layout.addWidget(flag, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(description, alignment=Qt.AlignmentFlag.AlignCenter)
        return card

    def _create_trading_mode_selector(self) -> QFrame:
        # ... (This UI code is unchanged)
        frame = QFrame()
        frame.setObjectName("tradingModeGroup")
        layout = QHBoxLayout(frame)
        title = QLabel("Trading Mode:")
        title.setObjectName("groupTitle")
        self.paper_radio = QRadioButton("Paper")
        self.paper_radio.setChecked(True)
        self.live_radio = QRadioButton("Live")
        layout.addWidget(title)
        layout.addStretch()
        layout.addWidget(self.paper_radio)
        layout.addWidget(self.live_radio)
        return frame

    def _create_kite_credentials_page(self) -> QWidget:
        # ... (This UI code is unchanged)
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Kite API Credentials")
        title.setObjectName("pageTitle")
        self.kite_api_key_input = QLineEdit()
        self.kite_api_key_input.setPlaceholderText("API Key")
        self.kite_api_secret_input = QLineEdit()
        self.kite_api_secret_input.setPlaceholderText("API Secret")
        self.kite_api_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.save_kite_creds = QCheckBox("Remember Credentials")
        self.save_kite_creds.setChecked(True)
        button_layout = self._create_nav_buttons(back_slot=lambda: self.stacked_widget.setCurrentIndex(1),
                                                 continue_slot=self._initiate_kite_login,
                                                 continue_text="Get Request Token")
        layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.kite_api_key_input)
        layout.addWidget(self.kite_api_secret_input)
        layout.addWidget(self.save_kite_creds)
        layout.addStretch()
        layout.addLayout(button_layout)
        return page

    def _create_kite_token_page(self) -> QWidget:
        # ... (This UI code is unchanged)
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Complete Kite Login")
        title.setObjectName("pageTitle")
        instructions = QLabel("Copy the 'request_token' from your browser's URL and paste it below.")
        instructions.setWordWrap(True)
        self.request_token_input = QLineEdit()
        self.request_token_input.setPlaceholderText("Paste Request Token here")
        self.generate_session_btn = QPushButton("Generate Session")
        self.generate_session_btn.setObjectName("primaryButton")
        self.generate_session_btn.clicked.connect(self._complete_kite_login)
        button_layout = self._create_nav_buttons(back_slot=lambda: self.stacked_widget.setCurrentIndex(2))
        button_layout.addWidget(self.generate_session_btn)
        layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(instructions)
        layout.addWidget(self.request_token_input)
        layout.addStretch()
        layout.addLayout(button_layout)
        return page

    def _create_ibkr_connection_page(self) -> QWidget:
        # ... (This UI code is unchanged)
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Interactive Brokers")
        title.setObjectName("pageTitle")
        settings_layout = QHBoxLayout()
        host_layout = QVBoxLayout()
        host_layout.addWidget(QLabel("Host"))
        self.ibkr_host_combo = QComboBox()
        self.ibkr_host_combo.addItems(["::1 (IPv6)", "127.0.0.1 (IPv4)"])
        host_layout.addWidget(self.ibkr_host_combo)
        client_id_layout = QVBoxLayout()
        client_id_layout.addWidget(QLabel("Client ID"))
        self.ibkr_client_id_input = QSpinBox()
        self.ibkr_client_id_input.setRange(1, 100)
        client_id_layout.addWidget(self.ibkr_client_id_input)
        settings_layout.addLayout(host_layout)
        settings_layout.addLayout(client_id_layout)
        self.ibkr_status_label = QLabel("Ready to connect.")
        self.ibkr_status_label.setObjectName("statusLabel")
        self.ibkr_status_label.setWordWrap(True)
        self.connect_ibkr_btn = QPushButton("Connect")
        self.connect_ibkr_btn.setObjectName("primaryButton")
        self.connect_ibkr_btn.clicked.connect(self._connect_to_ibkr)
        button_layout = self._create_nav_buttons(back_slot=lambda: self.stacked_widget.setCurrentIndex(1))
        button_layout.addWidget(self.connect_ibkr_btn)
        layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(settings_layout)
        layout.addWidget(self.ibkr_status_label)
        layout.addStretch()
        layout.addLayout(button_layout)
        return page

    def _create_nav_buttons(self, back_slot=None, continue_slot=None, continue_text="Continue") -> QHBoxLayout:
        # ... (This UI code is unchanged)
        layout = QHBoxLayout()
        if back_slot:
            back_btn = QPushButton("Back")
            back_btn.setObjectName("secondaryButton")
            back_btn.clicked.connect(back_slot)
            layout.addWidget(back_btn)
        layout.addStretch()
        if continue_slot:
            continue_btn = QPushButton(continue_text)
            continue_btn.setObjectName("primaryButton")
            continue_btn.clicked.connect(continue_slot)
            layout.addWidget(continue_btn)
        return layout

    def _try_auto_login(self):
        """Attempt to auto-login if a valid session exists."""
        if not KITE_AVAILABLE:
            self.stacked_widget.setCurrentIndex(1)
            return

        session = self.token_manager.load_broker_session(BrokerMode.INDIA)
        if session:
            self.selected_broker = BrokerMode.INDIA
            self.selected_trading_mode = TradingMode(session.get('trading_mode', 'paper'))
            creds = self.token_manager.load_broker_credentials(BrokerMode.INDIA)
            if creds and 'api_key' in creds and session.get('session_data', {}).get('access_token'):
                self.auto_login_status.setText(f"Found session for Kite.")
                self.authentication_data = {
                    'broker_mode': self.selected_broker,
                    'trading_mode': self.selected_trading_mode,
                    'api_key': creds['api_key'],
                    'access_token': session['session_data']['access_token']
                }
                # **FIX: Use a member timer and connect to a finalization slot**
                self.auto_login_timer.timeout.connect(self._finalize_auto_login)
                self.auto_login_timer.start(1500)
                return

        # If no valid session, go directly to manual selection
        self.stacked_widget.setCurrentIndex(1)

    def _finalize_auto_login(self):
        """Finalizes the login only if it has not been cancelled."""
        # **FIX: Check cancellation flag before accepting**
        if not self.auto_login_cancelled:
            self.accept()

    def _cancel_auto_login(self):
        """Cancels the auto-login process."""
        # **FIX: Set cancellation flag and stop the timer**
        self.auto_login_cancelled = True
        self.auto_login_timer.stop()
        self.stacked_widget.setCurrentIndex(1)

    def _select_broker(self, broker_mode: BrokerMode):
        # ... (This logic is unchanged)
        self.selected_broker = broker_mode
        self.selected_trading_mode = TradingMode.PAPER if self.paper_radio.isChecked() else TradingMode.LIVE
        self._update_card_selection()
        if broker_mode == BrokerMode.INDIA:
            if not KITE_AVAILABLE:
                QMessageBox.warning(self, "Library Missing", "Please install kiteconnect: pip install kiteconnect")
                return
            creds = self.token_manager.load_broker_credentials(BrokerMode.INDIA)
            if creds:
                self.kite_api_key_input.setText(creds.get('api_key', ''))
                self.kite_api_secret_input.setText(creds.get('api_secret', ''))
            self.stacked_widget.setCurrentIndex(2)
        elif broker_mode == BrokerMode.AMERICA:
            if not is_ibkr_available():
                QMessageBox.warning(self, "Library Missing", "Please install ib_insync: pip install ib_insync")
                return
            self.stacked_widget.setCurrentIndex(4)

    def _update_card_selection(self):
        # ... (This logic is unchanged)
        is_india = self.selected_broker == BrokerMode.INDIA
        self.india_card.setProperty("selected", is_india)
        self.america_card.setProperty("selected", not is_india)
        for card in [self.india_card, self.america_card]:
            card.style().unpolish(card)
            card.style().polish(card)

    def _initiate_kite_login(self):
        # ... (This logic is unchanged)
        self.kite_api_key = self.kite_api_key_input.text().strip()
        self.kite_api_secret = self.kite_api_secret_input.text().strip()
        if not (self.kite_api_key and self.kite_api_secret):
            QMessageBox.warning(self, "Input Error", "API Key and Secret are required.")
            return
        if self.save_kite_creds.isChecked():
            self.token_manager.save_broker_credentials(BrokerMode.INDIA, {'api_key': self.kite_api_key,
                                                                          'api_secret': self.kite_api_secret})
        try:
            kite = KiteConnect(api_key=self.kite_api_key)
            webbrowser.open_new(kite.login_url())
            self.stacked_widget.setCurrentIndex(3)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not open login URL: {e}")

    def _complete_kite_login(self):
        # ... (This logic is unchanged)
        request_token = self.request_token_input.text().strip()
        if not request_token:
            QMessageBox.warning(self, "Input Error", "Request Token is required.")
            return
        self.generate_session_btn.setEnabled(False)
        self.generate_session_btn.setText("Generating...")
        self.kite_worker = KiteLoginWorker(self.kite_api_key, self.kite_api_secret, request_token)
        self.kite_worker.success.connect(self._on_kite_login_success)
        self.kite_worker.error.connect(self._on_kite_login_error)
        self.kite_worker.start()

    def _on_kite_login_success(self, access_token: str):
        # ... (This logic is unchanged)
        session_data = {'access_token': access_token, 'login_time': datetime.now().isoformat()}
        self.token_manager.save_broker_session(BrokerMode.INDIA, self.selected_trading_mode, session_data)
        self.authentication_data = {'broker_mode': BrokerMode.INDIA, 'trading_mode': self.selected_trading_mode,
                                    'api_key': self.kite_api_key, 'access_token': access_token}
        self.accept()

    def _on_kite_login_error(self, error_msg: str):
        # ... (This logic is unchanged)
        QMessageBox.critical(self, "Login Failed", f"Failed to generate session:\n{error_msg}")
        self.generate_session_btn.setEnabled(True)
        self.generate_session_btn.setText("Generate Session")

    def _connect_to_ibkr(self):
        # ... (This logic is unchanged)
        host = "::1" if self.ibkr_host_combo.currentIndex() == 0 else "127.0.0.1"
        client_id = self.ibkr_client_id_input.value()
        self.connect_ibkr_btn.setEnabled(False)
        self.ibkr_auth.connect_to_tws(trading_mode=self.selected_trading_mode, host=host, client_id=client_id)

    def _on_ibkr_connection_success(self, ib_client):
        # ... (This logic is unchanged)
        self.authentication_data = {'broker_mode': BrokerMode.AMERICA, 'trading_mode': self.selected_trading_mode,
                                    'ib_client': ib_client, 'client_id': self.ibkr_client_id_input.value()}
        self.accept()

    def _on_ibkr_status_update(self, message: str):
        # ... (This logic is unchanged)
        self.ibkr_status_label.setText(message)
        if "❌" in message or "failed" in message.lower():
            self.connect_ibkr_btn.setEnabled(True)
            self.ibkr_status_label.setProperty("error", True)
        else:
            self.ibkr_status_label.setProperty("error", False)
        self.ibkr_status_label.style().unpolish(self.ibkr_status_label)
        self.ibkr_status_label.style().polish(self.ibkr_status_label)

    def get_authentication_data(self) -> Dict[str, Any]:
        return self.authentication_data

    def closeEvent(self, event):
        self.ibkr_auth.disconnect()
        super().closeEvent(event)

    def _handle_mouse_press(self, event: QMouseEvent):
        # ... (This logic is unchanged)
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _handle_mouse_move(self, event: QMouseEvent):
        # ... (This logic is unchanged)
        if event.buttons() & Qt.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def _handle_mouse_release(self, event: QMouseEvent):
        self._drag_pos = None

    def _apply_styles(self):
        # ... (This stylesheet is unchanged)
        self.setStyleSheet("""
            QDialog { background: transparent; }
            #mainContainer {
                background: #1e1e2e;
                border-radius: 12px;
                border: 1px solid #3e3e4e;
                font-family: "Segoe UI", sans-serif;
            }
            #dialogTitle, #pageTitle, #welcomeTitle {
                color: #ffffff;
                font-weight: 600;
                background: transparent;
            }
            #dialogTitle { font-size: 18px; }
            #pageTitle { font-size: 22px; margin-bottom: 20px; }
            #welcomeTitle { font-size: 28px; color: #82aaff; }
            #brokerCard {
                background: #2a2a3a;
                border: 2px solid #3a3a4a;
                border-radius: 10px;
                padding: 20px;
                min-height: 180px;
            }
            #brokerCard:hover {
                border-color: #82aaff;
            }
            #brokerCard[selected="true"] {
                background: #3a3a5a;
                border-color: #82aaff;
            }
            #brokerName { font-size: 16px; font-weight: bold; color: #c3e88d; background: transparent; }
            #brokerDescription { font-size: 12px; color: #8a8a9e; background: transparent; }
            #tradingModeGroup {
                background: #2a2a3a;
                border-radius: 8px;
                padding: 10px;
                margin-top: 20px;
            }
            #groupTitle { font-weight: bold; color: #c3e88d; background: transparent; }
            QRadioButton { color: #b0b0c0; font-size: 13px; }
            QRadioButton::indicator { width: 14px; height: 14px; border-radius: 7px; border: 1px solid #5a5a6a; }
            QRadioButton::indicator:checked { background-color: #82aaff; }
            QLabel, QCheckBox { color: #b0b0c0; background: transparent; }
            QLineEdit, QSpinBox, QComboBox {
                background: #2a2a3a;
                border: 1px solid #4a4a5a;
                border-radius: 6px;
                padding: 10px;
                color: #ffffff;
                font-size: 14px;
            }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
                border-color: #82aaff;
            }
            QPushButton {
                font-size: 14px;
                font-weight: 600;
                border-radius: 6px;
                padding: 10px 20px;
                min-height: 24px;
            }
            #primaryButton {
                background: #82aaff;
                color: #1e1e2e;
            }
            #primaryButton:hover { background: #92baff; }
            #primaryButton:disabled { background: #4a4a5a; color: #8a8a9e; }
            #secondaryButton {
                background: #4a4a5a;
                color: #ffffff;
            }
            #secondaryButton:hover { background: #5a5a6a; }
            #closeButton {
                background: transparent;
                border: none;
                color: #8a8a9e;
                font-size: 20px;
                padding: 0;
                min-height: 20px;
            }
            #closeButton:hover { color: #ff79c6; }
            #statusLabel {
                background: #2a2a3a;
                border-radius: 6px;
                padding: 10px;
                color: #8a8a9e;
                text-align: center;
                min-height: 40px;
            }
            #statusLabel[error="true"] {
                color: #ff79c6;
                border: 1px solid #ff79c6;
            }
        """)