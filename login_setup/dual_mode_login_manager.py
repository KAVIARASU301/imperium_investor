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
from PySide6.QtGui import QMouseEvent, QIcon

try:
    from kiteconnect import KiteConnect
    KITE_AVAILABLE = True
except ImportError:
    KITE_AVAILABLE = False

from login_setup.broker_modes import BrokerMode, TradingMode, get_broker_config, get_display_config
from login_setup.token_manager import EnhancedTokenManager
from login_setup.ibkr_auth import IBKRAuth, is_ibkr_available
from kite.widgets.relay_settings_widget import RelaySettingsDialog
from utils.resource_path import resource_path

logger = logging.getLogger(__name__)

DEFAULT_KITE_CALLBACK_PORTS = (8765, 5678)



# ==============================================================================
# INSTITUTIONAL DARK TRADING TERMINAL UI TOKENS
# ==============================================================================

class UI:
    BG0 = "#050709"      # deepest shell
    BG1 = "#0a0d12"      # window body
    BG2 = "#0f1318"      # panel/control layer
    BG3 = "#141920"      # raised/hover layer
    BG4 = "#1a2030"      # borders
    BG5 = "#222b3a"      # strong border

    TEXT0 = "#e8f0ff"    # primary
    TEXT1 = "#a8bcd4"    # secondary
    TEXT2 = "#5a7090"    # muted
    TEXT3 = "#2a3a50"    # disabled

    GREEN = "#00d4a8"    # success/buy/confirm
    RED = "#ff4d6a"      # danger/sell/error
    AMBER = "#f59e0b"    # warning/active
    CYAN = "#00d4ff"     # info/utility
    BLUE = "#3b82f6"     # neutral accent

    SELECTION = "#1a2840"
    SANS = "'Inter', 'Segoe UI Variable', 'Segoe UI', sans-serif"
    NUM = "'Inter', 'Segoe UI Variable', 'Segoe UI', sans-serif"
    MONO = "'Consolas', 'JetBrains Mono', monospace"  # only for raw logs, IDs, technical debug text


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
                        b"<p>You can now return to qullamaggie.</p>"
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
        self.setWindowTitle("qullamaggie - Login")
        self.setMinimumSize(560, 620)
        # self.resize(540, 620)
        self.setModal(True)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, False)


    def _setup_ui(self):
        container = QFrame()
        container.setObjectName("mainContainer")
        container.mousePressEvent = self._handle_mouse_press
        container.mouseMoveEvent = self._handle_mouse_move
        container.mouseReleaseEvent = self._handle_mouse_release

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(1, 1, 1, 1)
        main_layout.setSpacing(0)
        main_layout.addWidget(container)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        container_layout.addWidget(self._create_header())

        self.stacked_widget = QStackedWidget()
        self.stacked_widget.setObjectName("loginStack")
        self.stacked_widget.currentChanged.connect(self._on_page_changed)
        container_layout.addWidget(self.stacked_widget, 1)

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


    def _create_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("titleBar")
        header.setFixedHeight(42)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 0, 10, 0)
        layout.setSpacing(6)

        title = QLabel("Qullamaggie")
        title.setObjectName("dialogTitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(26, 26)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self._on_close)

        layout.addWidget(title)
        layout.addStretch()
        layout.addWidget(close_btn)
        return header

    def _on_close(self):
            self._stop_request_token_server()
            self._stop_request_timeout_timer()
            self.reject()

        # --------------------------------------------------------------------------
        # Page 0: Auto-login
        # --------------------------------------------------------------------------


    def _create_auto_login_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("loginPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 28, 30, 28)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.auto_login_status = QLabel("Checking for existing session...")
        self.auto_login_status.setObjectName("statusLabel")
        self.auto_login_status.setAlignment(Qt.AlignmentFlag.AlignCenter)

        eyebrow = QLabel("WELCOME")
        eyebrow.setObjectName("eyebrowLabel")
        eyebrow.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Secure Access")
        title.setObjectName("welcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle = QLabel("Big money comes from holding. Hold! Hold! Hold!")
        subtitle.setObjectName("subTitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        layout.addWidget(eyebrow)
        layout.addSpacing(6)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(12)
        layout.addWidget(self.auto_login_status)
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
        page.setObjectName("loginPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        title = QLabel("Broker and Trading Mode")
        title.setObjectName("brokerPageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.session_hint_label = QLabel("")
        self.session_hint_label.setObjectName("statusLabel")
        self.session_hint_label.setWordWrap(True)
        self.session_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.cancel_active_session_btn = QPushButton("Clear active Kite session")
        self.cancel_active_session_btn.setObjectName("subtleActionButton")
        self.cancel_active_session_btn.clicked.connect(self._clear_active_kite_session)
        self.cancel_active_session_btn.setVisible(bool(self._active_kite_session))

        broker_layout = QHBoxLayout()
        broker_layout.setContentsMargins(0, 0, 0, 0)
        broker_layout.setSpacing(8)
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

        hold_message = QLabel("Big money comes from holding. Hold! Hold! Hold!")
        hold_message.setObjectName("subTitle")
        hold_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hold_message.setWordWrap(True)

        continue_btn = QPushButton("Continue")
        continue_btn.setObjectName("primaryButton")
        continue_btn.setFixedHeight(30)
        continue_btn.clicked.connect(self._on_broker_selected)

        layout.addWidget(title)
        layout.addWidget(self.session_hint_label)
        layout.addLayout(broker_layout)
        layout.addWidget(mode_frame)
        layout.addWidget(hold_message)
        layout.addStretch()
        layout.addWidget(continue_btn)
        layout.addWidget(self.cancel_active_session_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        return page


    def _create_broker_card(self, broker_mode: BrokerMode) -> QFrame:
        display_cfg = get_display_config(broker_mode)   # dict retained for backend compatibility/future metadata
        broker_cfg = get_broker_config(broker_mode)      # BrokerConfig dataclass
        _ = display_cfg

        card = QFrame()
        card.setObjectName("brokerCard")
        card.setCursor(Qt.PointingHandCursor)
        card.setFixedHeight(112)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(5)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        region = "India" if broker_mode == BrokerMode.INDIA else "United States"
        region_badge = QLabel(region)
        region_badge.setObjectName("brokerRegionBadge")
        region_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_label = QLabel()
        icon_label.setObjectName("brokerIcon")
        icon_path = resource_path("assets/icons/india.svg") if broker_mode == BrokerMode.INDIA else resource_path("assets/icons/usa.svg")
        icon_label.setPixmap(QIcon(icon_path).pixmap(22, 22))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        market_label = QLabel(broker_cfg.market)
        market_label.setObjectName("brokerMarket")
        market_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        market_label.setWordWrap(True)

        radio = QRadioButton(broker_cfg.display_name)
        radio.setObjectName("brokerRadio")
        radio.setCursor(Qt.CursorShape.PointingHandCursor)

        layout.addWidget(icon_label)
        layout.addWidget(region_badge)
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
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(12)

        label = QLabel("Trading mode")
        label.setObjectName("sectionLabel")

        self.paper_radio = QRadioButton("Paper Trading")
        self.live_radio = QRadioButton("Live Trading")
        self.live_radio.setChecked(True)

        layout.addWidget(label)
        layout.addStretch()
        layout.addWidget(self.paper_radio)
        layout.addWidget(self.live_radio)
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
            self.session_hint_label.setText("Previous Kite session cleared. Please sign in again to continue.")
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
        page.setObjectName("loginPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        title = QLabel("Kite Credentials")
        title.setObjectName("pageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        creds_panel = QFrame()
        creds_panel.setObjectName("inputPanel")
        creds_layout = QVBoxLayout(creds_panel)
        creds_layout.setContentsMargins(12, 10, 12, 10)
        creds_layout.setSpacing(7)

        self.kite_api_key_input = QLineEdit()
        self.kite_api_key_input.setObjectName("terminalInput")
        self.kite_api_key_input.setPlaceholderText("API Key")

        self.kite_api_secret_input = QLineEdit()
        self.kite_api_secret_input.setObjectName("terminalInput")
        self.kite_api_secret_input.setPlaceholderText("API Secret")
        self.kite_api_secret_input.setEchoMode(QLineEdit.EchoMode.Password)

        self.save_kite_creds = QCheckBox("Remember Credentials")
        self.save_kite_creds.setChecked(True)

        self.relay_settings_btn = QPushButton("Configure Relay")
        self.relay_settings_btn.setObjectName("subtleActionButton")
        self.relay_settings_btn.clicked.connect(self._show_relay_settings)

        redirect_hint = QLabel(
            f'Redirect URL: http://127.0.0.1:{_resolve_callback_ports()[0]}/'
        )
        redirect_hint.setObjectName("hintLabel")

        nav = self._create_nav_buttons(
            back_slot=lambda: self.stacked_widget.setCurrentIndex(1),
            continue_slot=self._initiate_kite_login,
            continue_text="Login with Kite"
        )

        api_key_label = QLabel("API KEY")
        api_key_label.setObjectName("fieldLabel")
        api_secret_label = QLabel("API SECRET")
        api_secret_label.setObjectName("fieldLabel")

        creds_layout.addWidget(api_key_label)
        creds_layout.addWidget(self.kite_api_key_input)
        creds_layout.addWidget(api_secret_label)
        creds_layout.addWidget(self.kite_api_secret_input)

        bottom_options = QHBoxLayout()
        bottom_options.setContentsMargins(0, 2, 0, 0)
        bottom_options.setSpacing(8)
        bottom_options.addWidget(self.save_kite_creds)
        bottom_options.addStretch()
        bottom_options.addWidget(self.relay_settings_btn)
        creds_layout.addLayout(bottom_options)

        layout.addWidget(title)
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
        page.setObjectName("loginPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        title = QLabel("Complete Kite Login")
        title.setObjectName("pageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.capture_status_label = QLabel("Waiting for browser login...")
        self.capture_status_label.setObjectName("statusLabel")
        self.capture_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.capture_status_label.setWordWrap(True)

        separator = QLabel("MANUAL FALLBACK")
        separator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        separator.setObjectName("separatorLabel")

        self.request_token_input = QLineEdit()
        self.request_token_input.setObjectName("terminalInput")
        self.request_token_input.setPlaceholderText("Paste request_token or full callback URL")

        self.generate_session_btn = QPushButton("Generate Session")
        self.generate_session_btn.setObjectName("primaryButton")
        self.generate_session_btn.clicked.connect(self._complete_kite_login)

        nav = self._create_nav_buttons(
            back_slot=self._on_kite_token_back,
        )
        nav.addWidget(self.generate_session_btn)

        layout.addWidget(title)
        layout.addWidget(self.capture_status_label)
        layout.addSpacing(6)
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
            self.capture_status_label.setText("Waiting for browser authentication...")
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
                self.capture_status_label.setText(f"Unable to open browser: {e}")

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
                "Automatic token capture timed out. Please paste the token or callback URL manually."
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
        page.setObjectName("loginPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        title = QLabel("INTERACTIVE BROKERS")
        title.setObjectName("pageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        settings_panel = QFrame()
        settings_panel.setObjectName("inputPanel")
        settings_layout = QHBoxLayout(settings_panel)
        settings_layout.setContentsMargins(12, 10, 12, 10)
        settings_layout.setSpacing(8)

        host_col = QVBoxLayout()
        host_col.setSpacing(4)
        host_label = QLabel("HOST")
        host_label.setObjectName("fieldLabel")
        self.ibkr_host_combo = QComboBox()
        self.ibkr_host_combo.setObjectName("terminalInput")
        self.ibkr_host_combo.addItems(["127.0.0.1 (IPv4 / localhost)", "::1 (IPv6 / localhost)"])
        host_col.addWidget(host_label)
        host_col.addWidget(self.ibkr_host_combo)

        client_id_col = QVBoxLayout()
        client_id_col.setSpacing(4)
        client_label = QLabel("CLIENT ID")
        client_label.setObjectName("fieldLabel")
        self.ibkr_client_id_input = QSpinBox()
        self.ibkr_client_id_input.setObjectName("terminalInput")
        self.ibkr_client_id_input.setRange(1, 100)
        self.ibkr_client_id_input.setValue(1)
        client_id_col.addWidget(client_label)
        client_id_col.addWidget(self.ibkr_client_id_input)

        settings_layout.addLayout(host_col, 2)
        settings_layout.addLayout(client_id_col, 1)

        # Status area — QTextEdit handles long multi-line error messages cleanly
        self.ibkr_status_display = QTextEdit()
        self.ibkr_status_display.setObjectName("ibkrStatusDisplay")
        self.ibkr_status_display.setReadOnly(True)
        self.ibkr_status_display.setFixedHeight(150)
        self.ibkr_status_display.setPlaceholderText(
            "Status will appear here.\n\n"
            "TWS / IB Gateway checklist:\n"
            "1) Login to TWS (or IB Gateway)\n"
            "2) Enable API: Configure → API → Settings → Enable ActiveX and Socket Clients\n"
            "3) Allow localhost in Trusted IPs: 127.0.0.1, ::1\n"
            "4) Match port to mode: Paper 7497, Live 7496"
        )

        self.connect_ibkr_btn = QPushButton("Connect")
        self.connect_ibkr_btn.setObjectName("primaryButton")
        self.connect_ibkr_btn.clicked.connect(self._connect_to_ibkr)

        nav = self._create_nav_buttons(back_slot=lambda: self.stacked_widget.setCurrentIndex(1))
        nav.addWidget(self.connect_ibkr_btn)

        layout.addWidget(title)
        layout.addWidget(settings_panel)
        layout.addWidget(self.ibkr_status_display)
        layout.addStretch()
        layout.addLayout(nav)
        return page

    def _connect_to_ibkr(self):
        if self.selected_trading_mode is None:
            QMessageBox.warning(self, "Selection Required", "Please choose a trading mode first.")
            self.stacked_widget.setCurrentIndex(1)
            return

        host = "127.0.0.1" if self.ibkr_host_combo.currentIndex() == 0 else "::1"
        client_id = self.ibkr_client_id_input.value()
        self.connect_ibkr_btn.setEnabled(False)
        self.connect_ibkr_btn.setText("Connecting...")
        self.ibkr_status_display.clear()
        self.ibkr_status_display.setPlainText("Initiating connection...")
        self.ibkr_auth.connect_to_tws(
            trading_mode=self.selected_trading_mode,
            host=host,
            client_id=client_id
        )

    def _on_ibkr_status_update(self, message: str):
        # Strip markdown-style bold markers for plain display
        clean = re.sub(r"\*\*(.+?)\*\*", r"\1", message)
        self.ibkr_status_display.setPlainText(clean)
        if "✅ Connected" not in clean:
            self.connect_ibkr_btn.setEnabled(True)
            self.connect_ibkr_btn.setText("Connect")

    def _on_ibkr_connection_success(self, ib_client):
        host = "127.0.0.1" if self.ibkr_host_combo.currentIndex() == 0 else "::1"
        client_id = self.ibkr_client_id_input.value()
        self.authentication_data = {
            "broker_mode": BrokerMode.AMERICA,
            "trading_mode": self.selected_trading_mode,
            "ib_client": ib_client,
            "client_id": client_id,
            "connection_details": {
                "host": host,
                "client_id": client_id,
            },
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
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        if back_slot:
            back_btn = QPushButton("Back")
            back_btn.setObjectName("secondaryButton")
            back_btn.setFixedHeight(30)
            back_btn.clicked.connect(back_slot)
            layout.addWidget(back_btn)
        layout.addStretch()
        if continue_slot:
            btn = QPushButton(continue_text)
            btn.setObjectName("primaryButton")
            btn.setFixedHeight(30)
            btn.clicked.connect(continue_slot)
            layout.addWidget(btn)
        return layout

    def get_authentication_data(self) -> Dict[str, Any]:
        return self.authentication_data

    def closeEvent(self, event):
        self._stop_request_token_server()
        self._stop_request_timeout_timer()
        self.ibkr_auth.disconnect()
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
        stylesheet = f"""
            * {{
                font-family: "Inter", "Segoe UI", "SF Pro Text", {UI.SANS};
            }}

            DualModeLoginManager {{
                background: {UI.BG0};
            }}

            #mainContainer {{
                background-color: {UI.BG1};
                border-radius: 2px;
                border: 1px solid {UI.BG5};
            }}

            #titleBar {{
                background: {UI.BG2};
                border-bottom: 1px solid rgba(0, 212, 255, 0.20);
            }}

            #dialogTitle {{
                color: {UI.TEXT0};
                font-family: {UI.SANS};
                font-size: 12px;
                font-weight: 800;
                letter-spacing: 1px;
                background: transparent;
            }}

            #closeButton {{
                background: transparent;
                color: {UI.TEXT2};
                border: 1px solid transparent;
                font-size: 12px;
                border-radius: 2px;
            }}

            #closeButton:hover {{
                color: {UI.RED};
                border-color: rgba(255, 77, 106, 0.35);
                background: rgba(255, 77, 106, 0.12);
            }}

            #loginStack,
            QWidget#loginPage {{
                background: {UI.BG1};
            }}

            #welcomeTitle,
            #pageTitle,
            #brokerPageTitle {{
                color: {UI.TEXT0};
                font-family: {UI.SANS};
                font-size: 13px;
                font-weight: 800;
                letter-spacing: 0.8px;
                background: transparent;
            }}

            #eyebrowLabel {{
                color: {UI.CYAN};
                background: rgba(0, 212, 255, 0.14);
                border: 1px solid rgba(0, 212, 255, 0.42);
                border-radius: 2px;
                padding: 3px 10px;
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 1.1px;
            }}

            #subTitle,
            #sectionLabel,
            #fieldLabel,
            #separatorLabel {{
                color: {UI.TEXT2};
                font-family: {UI.SANS};
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 0.9px;
                background: transparent;
            }}

            #statusLabel {{
                color: {UI.TEXT1};
                background: rgba(255, 255, 255, 0.02);
                border: 1px solid rgba(0, 212, 255, 0.24);
                border-radius: 2px;
                font-size: 11px;
                font-weight: 600;
                padding: 8px 10px;
            }}

            #hintLabel {{
                color: {UI.TEXT2};
                background: transparent;
                font-size: 10px;
                font-weight: 600;
                padding-left: 2px;
            }}

            #inputPanel,
            #modeFrame {{
                background: {UI.BG2};
                border: 1px solid {UI.BG5};
                border-radius: 2px;
            }}

            #brokerCard {{
                background: {UI.BG2};
                border: 1px solid {UI.BG5};
                border-radius: 2px;
            }}

            #brokerCard:hover {{
                background: {UI.BG3};
                border-color: rgba(0, 212, 255, 0.38);
            }}

            #brokerCard[selected="true"] {{
                background: {UI.BG3};
                border: 1px solid rgba(245, 158, 11, 0.9);
            }}

            #brokerRegionBadge {{
                color: {UI.AMBER};
                background: rgba(245, 158, 11, 0.08);
                border: 1px solid rgba(245, 158, 11, 0.22);
                border-radius: 2px;
                padding: 3px 8px;
                font-family: {UI.SANS};
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 0.9px;
                min-width: 70px;
            }}

            #brokerMarket {{
                color: {UI.TEXT2};
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.5px;
                background: transparent;
            }}

            #brokerRadio {{
                color: {UI.TEXT1};
                font-size: 12px;
                font-weight: 700;
                background: transparent;
            }}

            QLineEdit,
            QComboBox,
            QSpinBox {{
                background: {UI.BG3};
                border: 1px solid {UI.BG5};
                border-radius: 2px;
                padding: 7px 9px;
                color: {UI.TEXT0};
                selection-background-color: {UI.SELECTION};
                selection-color: {UI.TEXT0};
                font-family: {UI.NUM};
                font-size: 11px;
                font-weight: 650;
                min-height: 20px;
            }}

            QLineEdit:focus,
            QComboBox:focus,
            QSpinBox:focus {{
                border: 1px solid {UI.CYAN};
                background: rgba(0, 212, 255, 0.06);
            }}

            QLineEdit::placeholder {{
                color: {UI.TEXT3};
            }}

            QComboBox::drop-down {{
                width: 18px;
                border: none;
                background: transparent;
            }}

            QComboBox::down-arrow {{
                image: none;
                width: 0px;
                height: 0px;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid {UI.TEXT2};
                margin-right: 6px;
            }}

            QComboBox QAbstractItemView {{
                background: {UI.BG2};
                color: {UI.TEXT0};
                border: 1px solid {UI.BG5};
                selection-background-color: {UI.SELECTION};
                selection-color: {UI.TEXT0};
                outline: none;
                padding: 2px;
            }}

            QSpinBox::up-button,
            QSpinBox::down-button {{
                width: 0px;
                border: none;
            }}

            #primaryButton {{
                background: {UI.GREEN};
                color: #03281f;
                border: 1px solid {UI.GREEN};
                border-radius: 2px;
                padding: 7px 14px;
                font-family: {UI.SANS};
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 0.7px;
                min-width: 112px;
            }}

            #primaryButton:hover {{
                background: #17d8b0;
                border-color: #17d8b0;
                color: #03281f;
            }}

            #primaryButton:pressed {{
                background: #0fb894;
            }}

            #primaryButton:disabled {{
                background: {UI.BG3};
                color: {UI.TEXT3};
                border-color: {UI.BG4};
            }}

            #secondaryButton {{
                background: {UI.BG3};
                color: {UI.TEXT0};
                border: 1px solid {UI.BG5};
                border-radius: 2px;
                padding: 7px 14px;
                font-family: {UI.SANS};
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 0.7px;
                min-width: 80px;
            }}

            #secondaryButton:hover {{
                color: {UI.TEXT0};
                background: {UI.BG4};
                border-color: {UI.TEXT2};
            }}

            #subtleActionButton {{
                background: {UI.BG3};
                border: 1px solid {UI.BG5};
                color: {UI.CYAN};
                border-radius: 2px;
                font-size: 10px;
                font-weight: 700;
                padding: 3px 6px;
            }}

            #subtleActionButton:hover {{
                background: {UI.BG4};
                border-color: rgba(0, 212, 255, 0.28);
                color: #b7f4ff;
            }}

            QRadioButton,
            QCheckBox {{
                color: {UI.TEXT1};
                font-size: 11px;
                font-weight: 700;
                spacing: 6px;
                background: transparent;
            }}

            QRadioButton::indicator {{
                width: 12px;
                height: 12px;
                border-radius: 6px;
            }}

            QRadioButton::indicator:unchecked {{
                border: 1px solid {UI.BG5};
                background: {UI.BG1};
            }}

            QRadioButton::indicator:checked {{
                border: 1px solid {UI.AMBER};
                background: {UI.AMBER};
            }}

            QRadioButton::indicator:disabled {{
                border-color: {UI.TEXT3};
                background: {UI.BG2};
            }}

            QCheckBox::indicator {{
                width: 13px;
                height: 13px;
                border-radius: 3px;
                border: 1px solid {UI.BG5};
                background: {UI.BG1};
            }}

            QCheckBox::indicator:checked {{
                background: {UI.GREEN};
                border-color: {UI.GREEN};
            }}

            #ibkrStatusDisplay {{
                background: {UI.BG0};
                border: 1px solid {UI.BG4};
                border-radius: 2px;
                color: {UI.TEXT1};
                selection-background-color: {UI.SELECTION};
                font-family: {UI.MONO};
                font-size: 10px;
                font-weight: 600;
                padding: 7px;
            }}

            QTextEdit {{
                background: {UI.BG0};
                border: 1px solid {UI.BG4};
                color: {UI.TEXT1};
            }}

            QScrollBar:vertical {{
                background: transparent;
                width: 4px;
                border: none;
            }}

            QScrollBar::handle:vertical {{
                background: {UI.BG5};
                border-radius: 2px;
                min-height: 20px;
            }}

            QScrollBar::handle:vertical:hover {{
                background: {UI.TEXT2};
            }}

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0;
                border: none;
            }}
        """
        self.setStyleSheet(stylesheet)
