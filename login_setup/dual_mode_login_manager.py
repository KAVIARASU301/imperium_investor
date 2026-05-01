# login_setup/dual_mode_login_manager.py
"""
Dual-mode login manager for Kite (India) and IBKR (America).

Kite flow:
  1. User enters API key + secret → click "Get Request Token"
  2. Local HTTP callback server(s) start to auto-capture the redirect
  3. Browser opens Kite login page
  4. On successful Kite login, Kite redirects to your configured localhost URL
  5. Server captures the request_token automatically → session is generated

IBKR flow:
  1. User selects host + client ID → click "Connect"
  2. Connects to running IB Gateway / TWS instance
"""

import logging
import os
import re
import threading
import webbrowser
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse, parse_qs

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QMessageBox, QWidget, QStackedWidget, QCheckBox, QFrame,
    QRadioButton, QComboBox, QSpinBox, QTextEdit, QButtonGroup
)
from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QMouseEvent

try:
    from kiteconnect import KiteConnect
    KITE_AVAILABLE = True
except ImportError:
    KITE_AVAILABLE = False

from login_setup.broker_modes import BrokerMode, TradingMode, get_broker_config, get_display_config
from login_setup.token_manager import EnhancedTokenManager
from login_setup.ibkr_auth import IBKRAuth, is_ibkr_available
from kite.widgets.relay_settings_widget import RelaySettingsDialog

logger = logging.getLogger(__name__)

DEFAULT_KITE_CALLBACK_PORTS = (8765, 5678)


def _resolve_callback_ports() -> List[int]:
    """Resolve callback ports from env, with safe defaults for local login flows."""
    raw_ports = os.getenv("KITE_CALLBACK_PORTS", "")
    if not raw_ports.strip():
        return list(DEFAULT_KITE_CALLBACK_PORTS)

    parsed: List[int] = []
    for part in raw_ports.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            logger.warning(f"Ignoring invalid callback port entry: {part}")
            continue
        value = int(part)
        if 1 <= value <= 65535:
            parsed.append(value)

    return parsed or list(DEFAULT_KITE_CALLBACK_PORTS)


# ==============================================================================
# BACKGROUND WORKERS
# ==============================================================================

class KiteRequestTokenServer(QThread):
    """One-shot local HTTP callback server for automatic request_token capture."""
    token_received = Signal(int, str)
    error = Signal(int, str)

    def __init__(self, host: str = "127.0.0.1", port: int = 5678):
        super().__init__()
        self.host = host
        self.port = port
        self._stop_requested = threading.Event()
        self._token_emitted = False
        self._httpd: Optional[HTTPServer] = None

    def run(self):
        outer_self = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                try:
                    parsed = urlparse(self.path)
                    qs = parse_qs(parsed.query)
                    token = qs.get("request_token", [None])[0]

                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(
                        b"<html><body style='font-family:Segoe UI,sans-serif;background:#111;color:#eee;text-align:center;padding-top:40px;'>"
                        b"<h2>Login successful</h2>"
                        b"<p>You can now return to Qullamaggie.</p>"
                        b"</body></html>"
                    )

                    if token and not outer_self._token_emitted:
                        outer_self._token_emitted = True
                        outer_self.token_received.emit(outer_self.port, token)
                        outer_self._stop_requested.set()
                except Exception as e:
                    outer_self.error.emit(outer_self.port, str(e))

            def log_message(self, fmt, *args):
                return

        try:
            httpd = HTTPServer((self.host, self.port), Handler)
            httpd.timeout = 0.2
            self._httpd = httpd
            while not self._stop_requested.is_set() and not self._token_emitted:
                httpd.handle_request()
        except Exception as e:
            if not self._stop_requested.is_set():
                self.error.emit(self.port, str(e))
        finally:
            if self._httpd:
                self._httpd.server_close()
                self._httpd = None

    def stop(self):
        self._stop_requested.set()


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

        self._request_token_servers: List[KiteRequestTokenServer] = []
        self._callback_failure_reasons: Dict[int, str] = {}
        self._token_auto_captured = False
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
        self.setWindowTitle("Qullamaggie - Login")
        self.setMinimumSize(500, 560)
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
        container_layout.setContentsMargins(18, 12, 18, 14)
        container_layout.setSpacing(8)
        container_layout.addLayout(self._create_header())

        self.stacked_widget = QStackedWidget()
        self.stacked_widget.currentChanged.connect(self._on_page_changed)
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
        left_spacer = QLabel("")
        left_spacer.setFixedSize(30, 30)
        title = QLabel("Qullamaggie Login")
        title.setObjectName("dialogTitle")
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(30, 30)
        close_btn.clicked.connect(self._on_close)
        layout.addWidget(left_spacer)
        layout.addStretch()
        layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        layout.addWidget(close_btn)
        return layout

    def _on_close(self):
        self._stop_request_token_server()
        self._stop_request_timeout_timer()
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
            self.cancel_active_session_btn.setVisible(True)

        self.stacked_widget.setCurrentIndex(1)

    def _on_page_changed(self, index: int):
        _ = index

    # --------------------------------------------------------------------------
    # Page 1: Broker + Mode Selection
    # --------------------------------------------------------------------------

    def _create_broker_selection_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        title = QLabel("Select Your Broker")
        title.setObjectName("brokerPageTitle")

        self.session_hint_label = QLabel("")
        self.session_hint_label.setObjectName("statusLabel")
        self.session_hint_label.setWordWrap(True)
        self.session_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.cancel_active_session_btn = QPushButton("Cancel active session")
        self.cancel_active_session_btn.setObjectName("subtleActionButton")
        self.cancel_active_session_btn.clicked.connect(self._clear_active_kite_session)
        self.cancel_active_session_btn.setVisible(bool(self._active_kite_session))

        broker_layout = QHBoxLayout()
        broker_layout.setSpacing(10)
        self.india_card = self._create_broker_card(BrokerMode.INDIA)
        self.america_card = self._create_broker_card(BrokerMode.AMERICA)
        broker_layout.addWidget(self.india_card)
        broker_layout.addWidget(self.america_card)

        self.broker_group = QButtonGroup(self)
        self.broker_group.setExclusive(True)
        self.broker_group.addButton(self.india_radio)
        self.broker_group.addButton(self.america_radio)

        # Default to Kite when available; otherwise fall back to IBKR.
        if self.india_radio.isEnabled():
            self.india_radio.setChecked(True)
        elif self.america_radio.isEnabled():
            self.america_radio.setChecked(True)

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
        layout.addWidget(self.cancel_active_session_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        return page

    def _create_broker_card(self, broker_mode: BrokerMode) -> QFrame:
        display_cfg = get_display_config(broker_mode)   # dict
        broker_cfg = get_broker_config(broker_mode)      # BrokerConfig dataclass

        card = QFrame()
        card.setObjectName("brokerCard")
        card.setCursor(Qt.PointingHandCursor)
        card.setFixedHeight(126)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        flag = QLabel(display_cfg.get("flag_emoji", ""))
        flag.setStyleSheet("font-size: 26px; background: transparent;")
        flag.setAlignment(Qt.AlignmentFlag.AlignCenter)

        radio = QRadioButton(broker_cfg.display_name)
        radio.setObjectName("brokerRadio")

        market_label = QLabel(broker_cfg.market)
        market_label.setObjectName("brokerMarket")
        market_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(flag)
        layout.addWidget(market_label)
        layout.addWidget(radio, alignment=Qt.AlignmentFlag.AlignCenter)

        if broker_mode == BrokerMode.INDIA:
            self.india_radio = radio
            if not KITE_AVAILABLE:
                radio.setEnabled(False)
                market_label.setText("kiteconnect not installed")
        else:
            self.america_radio = radio
            if not is_ibkr_available():
                radio.setEnabled(False)
                market_label.setText("ib_insync not installed")

        radio.toggled.connect(lambda checked, c=card: self._set_card_selected(c, checked))

        card.mousePressEvent = lambda e: radio.setChecked(True)
        return card

    def _set_card_selected(self, card: QFrame, selected: bool):
        card.setProperty("selected", selected)
        card.style().unpolish(card)
        card.style().polish(card)

    def _create_trading_mode_selector(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("modeFrame")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        self.paper_radio = QRadioButton("Paper")
        self.live_radio = QRadioButton("Live")
        self.live_radio.setChecked(True)
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
            if self._active_kite_session and self._active_kite_creds:
                if self._use_active_kite_session():
                    return
            self._prefill_kite_credentials()
            self.stacked_widget.setCurrentIndex(2)
        else:
            self.stacked_widget.setCurrentIndex(4)

    def _clear_active_kite_session(self):
        """Clear persisted active Kite session so user can generate a fresh login."""
        self.token_manager.clear_broker_session(BrokerMode.INDIA)
        self._active_kite_session = None
        self._active_kite_creds = None
        self.session_hint_label.setText("ℹ️ Previous Kite session cleared. Login again to continue.")
        self.cancel_active_session_btn.setVisible(False)
        self.auto_login_status.setText("No active Kite session.")

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
            "token_manager": self.token_manager,
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

        creds_panel = QFrame()
        creds_panel.setObjectName("inputPanel")
        creds_layout = QVBoxLayout(creds_panel)
        creds_layout.setContentsMargins(14, 14, 14, 14)
        creds_layout.setSpacing(8)

        self.kite_api_key_input = QLineEdit()
        self.kite_api_key_input.setPlaceholderText("API Key")

        self.kite_api_secret_input = QLineEdit()
        self.kite_api_secret_input.setPlaceholderText("API Secret")
        self.kite_api_secret_input.setEchoMode(QLineEdit.EchoMode.Password)

        self.save_kite_creds = QCheckBox("Remember Credentials")
        self.save_kite_creds.setChecked(True)

        self.relay_settings_btn = QPushButton("⚙️ Configure Relay Server")
        self.relay_settings_btn.setObjectName("subtleActionButton")
        self.relay_settings_btn.clicked.connect(self._show_relay_settings)

        redirect_hint = QLabel(
            f'<small>Set Redirect URL in Kite Console to: '
            f'<b>http://127.0.0.1:{_resolve_callback_ports()[0]}/</b></small>'
        )
        redirect_hint.setTextFormat(Qt.RichText)
        redirect_hint.setObjectName("hintLabel")

        nav = self._create_nav_buttons(
            back_slot=lambda: self.stacked_widget.setCurrentIndex(1),
            continue_slot=self._initiate_kite_login,
            continue_text="Login with Kite"
        )

        creds_layout.addWidget(self.kite_api_key_input)
        creds_layout.addWidget(self.kite_api_secret_input)
        bottom_options = QHBoxLayout()
        bottom_options.addWidget(self.save_kite_creds)
        bottom_options.addStretch()
        bottom_options.addWidget(self.relay_settings_btn)
        creds_layout.addLayout(bottom_options)

        layout.addWidget(title, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(creds_panel)
        layout.addWidget(redirect_hint)
        layout.addStretch()
        layout.addLayout(nav)
        return page

    def _show_relay_settings(self):
        """Pops up the standalone relay settings dialog."""
        dialog = RelaySettingsDialog(self.token_manager, self)
        dialog.exec()

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
        self._stop_request_token_server()
        self._stop_request_timeout_timer()
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
        self._start_request_token_server()
        self._start_request_timeout_timer()

        try:
            kite = KiteConnect(api_key=self.kite_api_key)
            webbrowser.open_new(kite.login_url())
        except Exception as e:
            self._stop_request_token_server()
            self._stop_request_timeout_timer()
            self.capture_status_label.setText(f"❌ Could not open browser: {e}")

    def _start_request_token_server(self):
        self._stop_request_token_server()
        self._callback_failure_reasons.clear()
        self._token_auto_captured = False

        callback_ports = _resolve_callback_ports()
        for port in callback_ports:
            server = KiteRequestTokenServer(port=port)
            server.token_received.connect(self._on_token_auto_captured)
            server.error.connect(self._on_token_server_error)
            server.start()
            self._request_token_servers.append(server)

        logger.info(f"Kite request token server started on ports {callback_ports}")

    def _stop_request_token_server(self):
        for server in self._request_token_servers:
            server.stop()

        for server in self._request_token_servers:
            if server.isRunning() and not server.wait(1000):
                logger.warning(f"Kite request token server on port {server.port} did not stop in time.")

        self._request_token_servers = []

    def _start_request_timeout_timer(self):
        self._stop_request_timeout_timer()
        self._token_timeout_timer = QTimer(self)
        self._token_timeout_timer.setSingleShot(True)
        self._token_timeout_timer.timeout.connect(self._on_request_timeout)
        self._token_timeout_timer.start(5 * 60 * 1000)

    def _stop_request_timeout_timer(self):
        if hasattr(self, "_token_timeout_timer") and self._token_timeout_timer.isActive():
            self._token_timeout_timer.stop()

    def _on_request_timeout(self):
        if self.request_token_input.text().strip():
            return
        self.capture_status_label.setText(
            "⚠️ No token was captured automatically. Paste the token or callback URL manually."
        )

    def _on_token_auto_captured(self, port: int, token: str):
        """Token was captured automatically from the browser redirect."""
        if self._token_auto_captured:
            return
        self._token_auto_captured = True
        logger.info("Request token auto-captured from browser callback.")
        self._stop_request_timeout_timer()
        self.capture_status_label.setText(f"✅ Token captured on port {port}! Generating session...")
        self.request_token_input.setText(token)
        self.generate_session_btn.setEnabled(False)
        self._run_session_worker(token)

    def _on_token_server_error(self, port: int, reason: str):
        if self._token_auto_captured:
            return

        self._callback_failure_reasons[port] = reason
        if len(self._callback_failure_reasons) < len(self._request_token_servers):
            return

        reasons = "; ".join(
            [f"{failed_port}: {msg}" for failed_port, msg in sorted(self._callback_failure_reasons.items())]
        )
        logger.error(f"Request token server error: {reasons}")
        if self.stacked_widget.currentIndex() == 3:
            self.capture_status_label.setText(
                f"⚠️ Auto-capture failed ({reasons}).\nPaste the token or callback URL manually."
            )

    @staticmethod
    def _extract_request_token(value: str) -> str:
        """Extract request_token from either a raw token or a full callback URL."""
        text = (value or "").strip()
        if not text:
            return ""

        if "request_token=" in text:
            try:
                parsed = urlparse(text)
                params = parse_qs(parsed.query)
                token = params.get("request_token", [""])[0].strip()
                if token:
                    return token
            except Exception:
                pass

            match = re.search(r"request_token=([^&\s]+)", text)
            if match:
                return match.group(1).strip()

        return text

    def _complete_kite_login(self):
        """Manual path: user pasted the token themselves."""
        self._stop_request_timeout_timer()
        token = self._extract_request_token(self.request_token_input.text())
        if not token:
            QMessageBox.warning(self, "Input Required", "Please paste the request_token or callback URL.")
            return
        self.request_token_input.setText(token)
        self.generate_session_btn.setEnabled(False)
        self.generate_session_btn.setText("Generating...")
        self._run_session_worker(token)

    def _run_session_worker(self, token: str):
        self._stop_request_token_server()  # No longer needed once we have the token
        self._stop_request_timeout_timer()
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
            "token_manager": self.token_manager,
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
        self.ibkr_host_combo = QComboBox()
        self.ibkr_host_combo.addItems(["::1 (IPv6 / localhost)", "127.0.0.1 (IPv4 / localhost)"])
        host_col.addWidget(self.ibkr_host_combo)

        client_id_col = QVBoxLayout()
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
        self._stop_request_token_server()
        self._stop_request_timeout_timer()
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
        texture_path = os.path.normpath(
            os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "assets",
                "textures",
                "login_manager_texture.png",
            )
        ).replace("\\", "/")

        stylesheet = """
            #mainContainer {
                background-color: #080b12;
                background-image: url("{texture_path}");
                background-position: center;
                background-repeat: repeat;
                border-radius: 10px;
                border: 1px solid #1f2733;
            }
            #dialogTitle {
                font-size: 14px;
                font-weight: 700;
                color: #d6deeb;
                letter-spacing: 0.5px;
            }
            #closeButton {
                background: transparent;
                color: #6b7380;
                border: none;
                font-size: 16px;
                border-radius: 4px;
            }
            #closeButton:hover { color: #e5edf8; background: #17202c; }
            #pageTitle, #welcomeTitle {
                font-size: 16px;
                font-weight: 700;
                color: #e5edf8;
                margin-bottom: 2px;
            }
            #brokerPageTitle {
                font-size: 16px;
                font-weight: 500;
                color: #8a95a5;
                margin-bottom: 2px;
            }
            #inputPanel {
                background: #0d121a;
                border: 1px solid #1f2733;
                border-radius: 7px;
            }
            QLineEdit, QComboBox, QSpinBox {
                background: #101721;
                border: 1px solid #253040;
                border-radius: 5px;
                padding: 7px 10px;
                color: #e5edf8;
                font-size: 12px;
                min-height: 20px;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                border-color: #52d894;
            }
            #primaryButton {
                background: #2d9d68;
                color: #e8fff2;
                border: none;
                border-radius: 5px;
                padding: 8px 16px;
                font-weight: 700;
                font-size: 12px;
                min-height: 32px;
            }
            #primaryButton:hover { background: #36b276; }
            #primaryButton:disabled { background: #1b2733; color: #526176; }
            #secondaryButton {
                background: transparent;
                color: #8693a6;
                border: 1px solid #2a3342;
                border-radius: 5px;
                padding: 8px 16px;
                font-size: 12px;
                min-height: 32px;
            }
            #secondaryButton:hover { color: #e5edf8; border-color: #4f6078; }
            #subtleActionButton {
                background: transparent;
                border: none;
                color: #7f8fa5;
                font-size: 11px;
                text-decoration: underline;
                padding: 2px;
            }
            #subtleActionButton:hover { color: #a2b2c9; }
            #brokerCard {
                background: #0d121a;
                border: 1px solid #1f2733;
                border-radius: 7px;
                padding: 8px;
            }
            #brokerCard:hover { border-color: #52d894; }
            #brokerCard[selected="true"] { border-color: #52d894; background: #101824; }
            #brokerMarket {
                color: #7c8ba2;
                font-size: 10px;
                letter-spacing: 0.8px;
            }
            #statusLabel {
                color: #9ba9bc;
                font-size: 12px;
                padding: 8px;
            }
            #separatorLabel { color: #55647a; font-size: 10px; letter-spacing: 0.7px; }
            #hintLabel { color: #6f7d91; font-size: 11px; margin-top: 4px; }
            #ibkrStatusDisplay {
                background: #091017;
                border: 1px solid #1f2733;
                border-radius: 6px;
                color: #b8c6d9;
                font-size: 12px;
                font-family: monospace;
                padding: 8px;
            }
            #modeFrame {
                background: #0d121a;
                border: 1px solid #1f2733;
                border-radius: 6px;
                padding: 8px;
                margin-top: 8px;
            }
            QRadioButton { color: #c4d1e3; font-size: 12px; }
            QRadioButton::indicator {
                width: 12px;
                height: 12px;
            }
            QRadioButton::indicator:unchecked {
                border: 1px solid #3f4f66;
                border-radius: 6px;
                background: #0f1620;
            }
            QRadioButton::indicator:checked {
                border: 1px solid #52d894;
                border-radius: 6px;
                background: #52d894;
            }
            QCheckBox { color: #c4d1e3; font-size: 12px; }
        """
        self.setStyleSheet(stylesheet.replace("{texture_path}", texture_path))
