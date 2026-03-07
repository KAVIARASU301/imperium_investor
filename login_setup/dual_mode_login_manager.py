# login_setup/dual_mode_login_manager.py
"""
Dual-mode login manager for Kite (India) and IBKR (America).

Kite flow:
  1. User enters API key + secret → click "Get Request Token"
  2. A local HTTP server starts on port 8765 to auto-capture the redirect
  3. Browser opens Kite login page
  4. On successful Kite login, Kite redirects to http://127.0.0.1:8765/
  5. Server captures the request_token automatically → session is generated

  NOTE: In Kite Developer Console, set Redirect URL to: http://127.0.0.1:8765/

IBKR flow:
  1. User selects host + client ID → click "Connect"
  2. Connects to running IB Gateway / TWS instance
"""

import logging
import re
import socketserver
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from typing import Optional, Dict, Any
from urllib.parse import urlparse, parse_qs

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QMessageBox, QWidget, QStackedWidget, QCheckBox, QFrame,
    QRadioButton, QComboBox, QSpinBox, QTextEdit
)
from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QMouseEvent

try:
    from kiteconnect import KiteConnect
    KITE_AVAILABLE = True
except ImportError:
    KITE_AVAILABLE = False

from login_setup.broker_modes import BrokerMode, TradingMode, get_broker_config, get_display_config
from login_setup.enhanced_token_manager import EnhancedTokenManager
from login_setup.ibkr_auth import IBKRAuth, is_ibkr_available

logger = logging.getLogger(__name__)

KITE_CALLBACK_PORT = 8765


# ==============================================================================
# BACKGROUND WORKERS
# ==============================================================================

class KiteCallbackServer(QThread):
    """
    Spins up a one-shot local HTTP server on 127.0.0.1:KITE_CALLBACK_PORT.
    Waits for Kite to redirect back after login, extracts the request_token,
    and emits it so the UI can auto-generate the session.
    """
    token_captured = Signal(str)
    capture_failed = Signal(str)

    def __init__(self):
        super().__init__()
        self._server: Optional[socketserver.TCPServer] = None

    def run(self):
        captured = {"token": None}
        parent = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                token = params.get("request_token", [None])[0]

                if token:
                    captured["token"] = token
                    self._respond(200, self._success_html(token))
                else:
                    status = params.get("status", ["unknown"])[0]
                    self._respond(400, self._error_html(status))

                # Signal the server to stop after this one request
                QTimer.singleShot(0, self.server.shutdown)

            def _respond(self, code: int, body: str):
                encoded = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            @staticmethod
            def _success_html(token: str) -> str:
                return f"""<!DOCTYPE html><html><head><title>Login Successful</title>
                <style>body{{font-family:sans-serif;background:#0d0d0d;color:#e0e0e0;
                display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
                .box{{text-align:center;padding:40px;background:#1a1a1a;border-radius:12px}}
                h2{{color:#4ade80}}p{{color:#888;font-size:13px}}</style></head>
                <body><div class="box"><h2>✅ Login Successful</h2>
                <p>Token captured. You can close this tab.</p></div></body></html>"""

            @staticmethod
            def _error_html(status: str) -> str:
                return f"""<!DOCTYPE html><html><head><title>Login Failed</title>
                <style>body{{font-family:sans-serif;background:#0d0d0d;color:#e0e0e0;
                display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
                .box{{text-align:center;padding:40px;background:#1a1a1a;border-radius:12px}}
                h2{{color:#f87171}}p{{color:#888;font-size:13px}}</style></head>
                <body><div class="box"><h2>❌ Login Failed</h2>
                <p>Status: {status}. Please close this tab and try again.</p></div></body></html>"""

            def log_message(self, fmt, *args):
                pass  # Suppress default request logging

        try:
            with socketserver.TCPServer(("127.0.0.1", KITE_CALLBACK_PORT), _Handler) as server:
                server.allow_reuse_address = True
                self._server = server
                server.serve_forever(poll_interval=0.2)
        except Exception as e:
            logger.error(f"Callback server error: {e}")
            parent.capture_failed.emit(f"Could not start local server: {e}")
            return

        if captured["token"]:
            parent.token_captured.emit(captured["token"])
        else:
            parent.capture_failed.emit("Login was cancelled or failed in the browser.")

    def stop(self):
        if self._server:
            self._server.shutdown()


class KiteSessionWorker(QThread):
    """Background worker to generate a Kite access token from a request token."""
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
            access_token = data.get("access_token")
            if access_token:
                self.success.emit(access_token)
            else:
                self.error.emit("Received empty access token from Kite API.")
        except Exception as e:
            logger.error(f"Kite session generation failed: {e}", exc_info=True)
            self.error.emit(str(e))


# ==============================================================================
# MAIN LOGIN DIALOG
# ==============================================================================

class DualModeLoginManager(QDialog):
    """Multi-page login dialog supporting Kite (India) and IBKR (America)."""

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
        self._active_kite_session: Optional[Dict[str, Any]] = None
        self._active_kite_creds: Optional[Dict[str, Any]] = None

        self._callback_server: Optional[KiteCallbackServer] = None
        self._session_worker: Optional[KiteSessionWorker] = None

        self._setup_window()
        self._setup_ui()
        self._apply_styles()

        self.ibkr_auth.connection_established.connect(self._on_ibkr_connection_success)
        self.ibkr_auth.status_updated.connect(self._on_ibkr_status_update)

        QTimer.singleShot(100, self._try_auto_login)

    # --------------------------------------------------------------------------
    # Window & UI Setup
    # --------------------------------------------------------------------------

    def _setup_window(self):
        self.setWindowTitle("Swing Trader - Login")
        self.setMinimumSize(500, 650)
        self.setModal(True)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def _setup_ui(self):
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
        container_layout.addLayout(self._create_header())

        self.stacked_widget = QStackedWidget()
        container_layout.addWidget(self.stacked_widget)

        # Page indices:
        # 0 - Auto-login splash
        # 1 - Broker selection
        # 2 - Kite credentials
        # 3 - Kite token (auto-capture + manual fallback)
        # 4 - IBKR connection
        self.stacked_widget.addWidget(self._create_auto_login_page())
        self.stacked_widget.addWidget(self._create_broker_selection_page())
        self.stacked_widget.addWidget(self._create_kite_credentials_page())
        self.stacked_widget.addWidget(self._create_kite_token_page())
        self.stacked_widget.addWidget(self._create_ibkr_connection_page())

    def _create_header(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        title = QLabel("Swing Trader Login")
        title.setObjectName("dialogTitle")
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(30, 30)
        close_btn.clicked.connect(self._on_close)
        layout.addWidget(title)
        layout.addStretch()
        layout.addWidget(close_btn)
        return layout

    def _on_close(self):
        self._stop_callback_server()
        self.reject()

    # --------------------------------------------------------------------------
    # Page 0: Auto-login
    # --------------------------------------------------------------------------

    def _create_auto_login_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.auto_login_status = QLabel("Checking for existing session...")
        self.auto_login_status.setObjectName("statusLabel")
        self.auto_login_status.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        layout.addWidget(QLabel("Welcome Back", objectName="welcomeTitle"), alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.auto_login_status, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        return page

    def _try_auto_login(self):
        if not KITE_AVAILABLE:
            self.stacked_widget.setCurrentIndex(1)
            return

        session = self.token_manager.load_broker_session(BrokerMode.INDIA)
        creds = self.token_manager.load_broker_credentials(BrokerMode.INDIA)
        access_token = (session or {}).get("session_data", {}).get("access_token")

        if session and creds and creds.get("api_key") and access_token:
            self._active_kite_session = session
            self._active_kite_creds = creds
            self.auto_login_status.setText("Active Kite session found.")

        self.stacked_widget.setCurrentIndex(1)

    # --------------------------------------------------------------------------
    # Page 1: Broker + Mode Selection
    # --------------------------------------------------------------------------

    def _create_broker_selection_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        title = QLabel("Select Your Broker")
        title.setObjectName("pageTitle")

        self.session_hint_label = QLabel("")
        self.session_hint_label.setObjectName("statusLabel")
        self.session_hint_label.setWordWrap(True)
        self.session_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if self._active_kite_session:
            self.session_hint_label.setText(
                "✅ Active Kite session found. Select Kite or IBKR to continue."
            )

        broker_layout = QHBoxLayout()
        self.india_card = self._create_broker_card(BrokerMode.INDIA)
        self.america_card = self._create_broker_card(BrokerMode.AMERICA)
        broker_layout.addWidget(self.india_card)
        broker_layout.addWidget(self.america_card)

        mode_frame = self._create_trading_mode_selector()
        continue_btn = QPushButton("Continue")
        continue_btn.setObjectName("primaryButton")
        continue_btn.clicked.connect(self._on_broker_selected)

        layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.session_hint_label)
        layout.addLayout(broker_layout)
        layout.addWidget(mode_frame)
        layout.addStretch()
        layout.addWidget(continue_btn)
        return page

    def _create_broker_card(self, broker_mode: BrokerMode) -> QFrame:
        display_cfg = get_display_config(broker_mode)   # dict
        broker_cfg = get_broker_config(broker_mode)      # BrokerConfig dataclass

        card = QFrame()
        card.setObjectName("brokerCard")
        card.setCursor(Qt.PointingHandCursor)
        card.setFixedHeight(140)
        layout = QVBoxLayout(card)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        flag = QLabel(display_cfg.get("flag_emoji", ""))
        flag.setStyleSheet("font-size: 36px; background: transparent;")
        flag.setAlignment(Qt.AlignmentFlag.AlignCenter)

        radio = QRadioButton(broker_cfg.display_name)
        radio.setObjectName("brokerRadio")

        description = QLabel(display_cfg.get("description", ""))
        description.setObjectName("brokerDescription")
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(flag)
        layout.addWidget(radio, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(description)

        if broker_mode == BrokerMode.INDIA:
            self.india_radio = radio
            if not KITE_AVAILABLE:
                radio.setEnabled(False)
                description.setText("kiteconnect not installed")
        else:
            self.america_radio = radio
            if not is_ibkr_available():
                radio.setEnabled(False)
                description.setText("ib_insync not installed")

        card.mousePressEvent = lambda e: radio.setChecked(True)
        return card

    def _create_trading_mode_selector(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("modeFrame")
        layout = QHBoxLayout(frame)
        layout.addWidget(QLabel("Trading Mode:"))
        self.paper_radio = QRadioButton("Paper")
        self.live_radio = QRadioButton("Live")
        self.paper_radio.setChecked(True)
        layout.addWidget(self.paper_radio)
        layout.addWidget(self.live_radio)
        layout.addStretch()
        return frame

    def _on_broker_selected(self):
        if self.india_radio.isChecked():
            self.selected_broker = BrokerMode.INDIA
        elif self.america_radio.isChecked():
            self.selected_broker = BrokerMode.AMERICA
        else:
            QMessageBox.warning(self, "Selection Required", "Please select a broker.")
            return

        self.selected_trading_mode = TradingMode.LIVE if self.live_radio.isChecked() else TradingMode.PAPER

        if self.selected_broker == BrokerMode.INDIA:
            if self._use_active_kite_session():
                return
            self._prefill_kite_credentials()
            self.stacked_widget.setCurrentIndex(2)
        else:
            self.stacked_widget.setCurrentIndex(4)

    def _use_active_kite_session(self) -> bool:
        """Use existing Kite session only after user explicitly selects Kite."""
        if not self._active_kite_session or not self._active_kite_creds:
            return False

        access_token = self._active_kite_session.get("session_data", {}).get("access_token")
        api_key = self._active_kite_creds.get("api_key")

        if not access_token or not api_key:
            return False

        self.authentication_data = {
            "broker_mode": BrokerMode.INDIA,
            "trading_mode": self.selected_trading_mode,
            "api_key": api_key,
            "access_token": access_token,
        }
        self.accept()
        return True

    def _prefill_kite_credentials(self):
        creds = self.token_manager.load_broker_credentials(BrokerMode.INDIA)
        if creds:
            self.kite_api_key_input.setText(creds.get("api_key", ""))
            self.kite_api_secret_input.setText(creds.get("api_secret", ""))

    # --------------------------------------------------------------------------
    # Page 2: Kite Credentials
    # --------------------------------------------------------------------------

    def _create_kite_credentials_page(self) -> QWidget:
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

        redirect_hint = QLabel(
            f'<small>Set Redirect URL in Kite Console to: '
            f'<b>http://127.0.0.1:{KITE_CALLBACK_PORT}/</b></small>'
        )
        redirect_hint.setTextFormat(Qt.RichText)
        redirect_hint.setObjectName("hintLabel")

        nav = self._create_nav_buttons(
            back_slot=lambda: self.stacked_widget.setCurrentIndex(1),
            continue_slot=self._initiate_kite_login,
            continue_text="Login with Kite"
        )

        layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.kite_api_key_input)
        layout.addWidget(self.kite_api_secret_input)
        layout.addWidget(self.save_kite_creds)
        layout.addWidget(redirect_hint)
        layout.addStretch()
        layout.addLayout(nav)
        return page

    # --------------------------------------------------------------------------
    # Page 3: Kite Token (auto-capture + manual fallback)
    # --------------------------------------------------------------------------

    def _create_kite_token_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        title = QLabel("Complete Kite Login")
        title.setObjectName("pageTitle")

        self.capture_status_label = QLabel("⏳ Waiting for browser login...")
        self.capture_status_label.setObjectName("statusLabel")
        self.capture_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        separator = QLabel("— or paste manually —")
        separator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        separator.setObjectName("separatorLabel")

        self.request_token_input = QLineEdit()
        self.request_token_input.setPlaceholderText("Paste request_token here (fallback)")

        self.generate_session_btn = QPushButton("Generate Session")
        self.generate_session_btn.setObjectName("primaryButton")
        self.generate_session_btn.clicked.connect(self._complete_kite_login)

        nav = self._create_nav_buttons(
            back_slot=self._on_kite_token_back,
        )
        nav.addWidget(self.generate_session_btn)

        layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.capture_status_label)
        layout.addSpacing(20)
        layout.addWidget(separator)
        layout.addWidget(self.request_token_input)
        layout.addStretch()
        layout.addLayout(nav)
        return page

    def _on_kite_token_back(self):
        self._stop_callback_server()
        self.stacked_widget.setCurrentIndex(2)

    # --------------------------------------------------------------------------
    # Kite login flow
    # --------------------------------------------------------------------------

    def _initiate_kite_login(self):
        self.kite_api_key = self.kite_api_key_input.text().strip()
        self.kite_api_secret = self.kite_api_secret_input.text().strip()

        if not self.kite_api_key or not self.kite_api_secret:
            QMessageBox.warning(self, "Missing Credentials", "API Key and Secret are required.")
            return

        if self.save_kite_creds.isChecked():
            self.token_manager.save_broker_credentials(
                BrokerMode.INDIA,
                {"api_key": self.kite_api_key, "api_secret": self.kite_api_secret}
            )

        self.stacked_widget.setCurrentIndex(3)
        self.capture_status_label.setText("⏳ Waiting for browser login...")
        self.request_token_input.clear()
        self.generate_session_btn.setEnabled(True)
        self.generate_session_btn.setText("Generate Session")

        # Start the callback server before opening the browser
        self._start_callback_server()

        try:
            kite = KiteConnect(api_key=self.kite_api_key)
            webbrowser.open_new(kite.login_url())
        except Exception as e:
            self.capture_status_label.setText(f"❌ Could not open browser: {e}")

    def _start_callback_server(self):
        self._stop_callback_server()
        self._callback_server = KiteCallbackServer()
        self._callback_server.token_captured.connect(self._on_token_auto_captured)
        self._callback_server.capture_failed.connect(self._on_token_capture_failed)
        self._callback_server.start()
        logger.info(f"Kite callback server started on port {KITE_CALLBACK_PORT}")

    def _stop_callback_server(self):
        if self._callback_server and self._callback_server.isRunning():
            self._callback_server.stop()
            self._callback_server.wait(1000)
        self._callback_server = None

    def _on_token_auto_captured(self, token: str):
        """Token was captured automatically from the browser redirect."""
        logger.info("Request token auto-captured from browser callback.")
        self.capture_status_label.setText("✅ Token captured! Generating session...")
        self.request_token_input.setText(token)
        self.generate_session_btn.setEnabled(False)
        self._run_session_worker(token)

    def _on_token_capture_failed(self, reason: str):
        self.capture_status_label.setText(f"⚠️ Auto-capture failed: {reason}\nPaste the token manually.")

    def _complete_kite_login(self):
        """Manual path: user pasted the token themselves."""
        token = self.request_token_input.text().strip()
        if not token:
            QMessageBox.warning(self, "Input Required", "Please paste the request_token.")
            return
        self.generate_session_btn.setEnabled(False)
        self.generate_session_btn.setText("Generating...")
        self._run_session_worker(token)

    def _run_session_worker(self, token: str):
        self._stop_callback_server()  # No longer needed once we have the token
        self._session_worker = KiteSessionWorker(self.kite_api_key, self.kite_api_secret, token)
        self._session_worker.success.connect(self._on_kite_login_success)
        self._session_worker.error.connect(self._on_kite_login_error)
        self._session_worker.start()

    def _on_kite_login_success(self, access_token: str):
        session_data = {"access_token": access_token, "login_time": datetime.now().isoformat()}
        self.token_manager.save_broker_session(BrokerMode.INDIA, self.selected_trading_mode, session_data)
        self.authentication_data = {
            "broker_mode": BrokerMode.INDIA,
            "trading_mode": self.selected_trading_mode,
            "api_key": self.kite_api_key,
            "access_token": access_token,
        }
        self.accept()

    def _on_kite_login_error(self, error_msg: str):
        QMessageBox.critical(self, "Login Failed", f"Failed to generate session:\n{error_msg}")
        self.generate_session_btn.setEnabled(True)
        self.generate_session_btn.setText("Generate Session")
        self.capture_status_label.setText("⚠️ Session generation failed. Try again.")

    # --------------------------------------------------------------------------
    # Page 4: IBKR Connection
    # --------------------------------------------------------------------------

    def _create_ibkr_connection_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        title = QLabel("Interactive Brokers")
        title.setObjectName("pageTitle")

        # Settings row
        settings_layout = QHBoxLayout()

        host_col = QVBoxLayout()
        host_col.addWidget(QLabel("Host"))
        self.ibkr_host_combo = QComboBox()
        self.ibkr_host_combo.addItems(["::1 (IPv6 / localhost)", "127.0.0.1 (IPv4 / localhost)"])
        host_col.addWidget(self.ibkr_host_combo)

        client_id_col = QVBoxLayout()
        client_id_col.addWidget(QLabel("Client ID"))
        self.ibkr_client_id_input = QSpinBox()
        self.ibkr_client_id_input.setRange(1, 100)
        self.ibkr_client_id_input.setValue(1)
        client_id_col.addWidget(self.ibkr_client_id_input)

        settings_layout.addLayout(host_col)
        settings_layout.addLayout(client_id_col)

        # Status area — QTextEdit handles long multi-line error messages cleanly
        self.ibkr_status_display = QTextEdit()
        self.ibkr_status_display.setObjectName("ibkrStatusDisplay")
        self.ibkr_status_display.setReadOnly(True)
        self.ibkr_status_display.setFixedHeight(160)
        self.ibkr_status_display.setPlaceholderText(
            "Status will appear here.\n\nMake sure IB Gateway or TWS is running and you're logged in."
        )

        self.connect_ibkr_btn = QPushButton("Connect")
        self.connect_ibkr_btn.setObjectName("primaryButton")
        self.connect_ibkr_btn.clicked.connect(self._connect_to_ibkr)

        nav = self._create_nav_buttons(back_slot=lambda: self.stacked_widget.setCurrentIndex(1))
        nav.addWidget(self.connect_ibkr_btn)

        layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(settings_layout)
        layout.addWidget(self.ibkr_status_display)
        layout.addStretch()
        layout.addLayout(nav)
        return page

    def _connect_to_ibkr(self):
        host = "::1" if self.ibkr_host_combo.currentIndex() == 0 else "127.0.0.1"
        client_id = self.ibkr_client_id_input.value()
        self.connect_ibkr_btn.setEnabled(False)
        self.connect_ibkr_btn.setText("Connecting...")
        self.ibkr_status_display.clear()
        self.ibkr_status_display.setPlainText("🔍 Initiating connection...")
        self.ibkr_auth.connect_to_tws(
            trading_mode=self.selected_trading_mode,
            host=host,
            client_id=client_id
        )

    def _on_ibkr_status_update(self, message: str):
        # Strip markdown-style bold markers for plain display
        clean = re.sub(r"\*\*(.+?)\*\*", r"\1", message)
        self.ibkr_status_display.setPlainText(clean)

    def _on_ibkr_connection_success(self, ib_client):
        self.authentication_data = {
            "broker_mode": BrokerMode.AMERICA,
            "trading_mode": self.selected_trading_mode,
            "ib_client": ib_client,
        }
        self.accept()

    # --------------------------------------------------------------------------
    # Shared helpers
    # --------------------------------------------------------------------------

    def _create_nav_buttons(
        self,
        back_slot=None,
        continue_slot=None,
        continue_text="Continue"
    ) -> QHBoxLayout:
        layout = QHBoxLayout()
        if back_slot:
            back_btn = QPushButton("← Back")
            back_btn.setObjectName("secondaryButton")
            back_btn.clicked.connect(back_slot)
            layout.addWidget(back_btn)
        layout.addStretch()
        if continue_slot:
            btn = QPushButton(continue_text)
            btn.setObjectName("primaryButton")
            btn.clicked.connect(continue_slot)
            layout.addWidget(btn)
        return layout

    def get_authentication_data(self) -> Dict[str, Any]:
        return self.authentication_data

    def closeEvent(self, event):
        self._stop_callback_server()
        super().closeEvent(event)

    # --------------------------------------------------------------------------
    # Window drag support
    # --------------------------------------------------------------------------

    def _handle_mouse_press(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _handle_mouse_move(self, event: QMouseEvent):
        if self._drag_pos and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def _handle_mouse_release(self, event: QMouseEvent):
        self._drag_pos = None

    # --------------------------------------------------------------------------
    # Styles
    # --------------------------------------------------------------------------

    def _apply_styles(self):
        self.setStyleSheet("""
            #mainContainer {
                background-color: #111111;
                border-radius: 12px;
                border: 1px solid #2a2a2a;
            }
            #dialogTitle {
                font-size: 16px;
                font-weight: bold;
                color: #e0e0e0;
            }
            #closeButton {
                background: transparent;
                color: #666;
                border: none;
                font-size: 16px;
                border-radius: 4px;
            }
            #closeButton:hover { color: #e0e0e0; background: #2a2a2a; }
            #pageTitle, #welcomeTitle {
                font-size: 20px;
                font-weight: bold;
                color: #e0e0e0;
                margin-bottom: 16px;
            }
            QLineEdit, QComboBox, QSpinBox {
                background: #1a1a1a;
                border: 1px solid #333;
                border-radius: 6px;
                padding: 8px 12px;
                color: #e0e0e0;
                font-size: 13px;
                min-height: 20px;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                border-color: #4ade80;
            }
            #primaryButton {
                background: #4ade80;
                color: #000;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-weight: bold;
                font-size: 13px;
                min-height: 36px;
            }
            #primaryButton:hover { background: #22c55e; }
            #primaryButton:disabled { background: #2a2a2a; color: #555; }
            #secondaryButton {
                background: transparent;
                color: #888;
                border: 1px solid #333;
                border-radius: 6px;
                padding: 10px 20px;
                font-size: 13px;
                min-height: 36px;
            }
            #secondaryButton:hover { color: #e0e0e0; border-color: #555; }
            #brokerCard {
                background: #1a1a1a;
                border: 1px solid #2a2a2a;
                border-radius: 8px;
                padding: 12px;
            }
            #brokerCard:hover { border-color: #4ade80; }
            #statusLabel {
                color: #aaa;
                font-size: 13px;
                padding: 8px;
            }
            #separatorLabel { color: #444; font-size: 11px; }
            #hintLabel { color: #666; font-size: 11px; margin-top: 4px; }
            #ibkrStatusDisplay {
                background: #0d0d0d;
                border: 1px solid #2a2a2a;
                border-radius: 6px;
                color: #ccc;
                font-size: 12px;
                font-family: monospace;
                padding: 8px;
            }
            #modeFrame {
                background: #1a1a1a;
                border: 1px solid #2a2a2a;
                border-radius: 6px;
                padding: 8px;
                margin-top: 8px;
            }
            QRadioButton { color: #ccc; font-size: 13px; }
            QRadioButton::indicator:checked { border-color: #4ade80; background: #4ade80; }
            QCheckBox { color: #ccc; font-size: 13px; }
        """)
