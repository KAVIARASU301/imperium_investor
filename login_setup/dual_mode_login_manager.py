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
from PySide6.QtCore import QRectF, Qt, QTimer, Signal, QThread
from PySide6.QtGui import (
    QBrush, QColor, QIcon, QLinearGradient, QMouseEvent, QPainter, QPainterPath,
    QPen, QRadialGradient
)

try:
    from kiteconnect import KiteConnect
    KITE_AVAILABLE = True
except ImportError:
    KITE_AVAILABLE = False

from login_setup.broker_modes import BrokerMode, TradingMode, get_broker_config, get_display_config
from login_setup.token_manager import EnhancedTokenManager
from login_setup.ibkr_auth import IBKRAuth, is_ibkr_available
from kite.widgets.order_routing_settings import RelaySettingsDialog
from utils.resource_path import resource_path

logger = logging.getLogger(__name__)

DEFAULT_KITE_CALLBACK_PORTS = (8765, 5678)



# ==============================================================================
# INSTITUTIONAL DARK TRADING TERMINAL UI TOKENS
# ==============================================================================

class UI:
    BG0 = "#000000"      # true AMOLED shell
    BG1 = "#030506"      # window body
    BG2 = "#070a0d"      # title/footer layer
    BG3 = "#0b1015"      # panel/control layer
    BG4 = "#111923"      # raised/hover layer
    BG5 = "#1a2634"      # borders
    BG6 = "#223044"      # strong border

    TEXT0 = "#eef5ff"    # primary
    TEXT1 = "#a8bcd4"    # secondary
    TEXT2 = "#5f7390"    # muted
    TEXT3 = "#2d3a4d"    # disabled

    GREEN = "#00d4a8"    # success/buy/confirm
    RED = "#ff4d6a"      # danger/sell/error
    AMBER = "#f59e0b"    # warning/active
    CYAN = "#00d4ff"     # info/utility
    BLUE = "#3b82f6"     # neutral accent

    SELECTION = "#10233a"
    SANS = "'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', sans-serif"
    NUM = "'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', sans-serif"
    MONO = "'JetBrains Mono', 'Consolas', monospace"  # only for raw logs, IDs, technical debug text


class LoginTextureFrame(QFrame):
    """Paints a production-grade IBKR-style dark login shell.

    Uses only procedural graphite / terminal-grid texture. No external texture
    image is required, so the login window stays consistent across installs.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)

    def _draw_micro_texture(self, painter: QPainter, rect):
        """Draw a crisp graphite weave + terminal-grid texture."""
        w = max(1, rect.width())
        h = max(1, rect.height())

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # Fine scanlines: gives the surface a real terminal/display material feel.
        painter.setPen(QPen(QColor(255, 255, 255, 7), 1))
        for y in range(1, h, 4):
            painter.drawLine(0, y, w, y)

        painter.setPen(QPen(QColor(0, 0, 0, 22), 1))
        for y in range(3, h, 8):
            painter.drawLine(0, y, w, y)

        # Subtle cross-grid: visible enough to read as texture, not decoration.
        painter.setPen(QPen(QColor(0, 212, 255, 10), 1))
        for x in range(0, w, 18):
            painter.drawLine(x, 0, x, h)

        painter.setPen(QPen(QColor(168, 188, 212, 7), 1))
        for y in range(0, h, 18):
            painter.drawLine(0, y, w, y)

        # Carbon-fiber style diagonal weave, like a high-end broker terminal shell.
        painter.setPen(QPen(QColor(255, 255, 255, 9), 1))
        for x in range(-h, w + h, 22):
            painter.drawLine(x, 0, x + h, h)

        painter.setPen(QPen(QColor(0, 0, 0, 32), 1))
        for x in range(-h, w + h, 22):
            painter.drawLine(x, h, x + h, 0)

        # A few quiet data-trace strokes so the shell feels like trading software,
        # not a generic dark dialog.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        trace_pen = QPen(QColor(0, 212, 255, 24), 1)
        trace_pen.setCapStyle(Qt.PenCapStyle.SquareCap)
        painter.setPen(trace_pen)
        for i, y in enumerate((58, 92, h - 86, h - 54)):
            if 16 < y < h - 16:
                x0 = 28 + i * 11
                painter.drawLine(x0, y, x0 + 46, y)
                painter.drawLine(x0 + 46, y, x0 + 46, y - 10)
                painter.drawLine(x0 + 46, y - 10, x0 + 76, y - 10)
                painter.drawLine(x0 + 76, y - 10, x0 + 76, y + 7)
                painter.drawLine(x0 + 76, y + 7, x0 + 126, y + 7)

        painter.restore()

    def paintEvent(self, event):
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = self.rect()
        shell_rect = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(shell_rect, 2, 2)
        painter.setClipPath(path)

        # Layer 1: deep graphite base, not plain black.
        base = QLinearGradient(0, 0, 0, rect.height())
        base.setColorAt(0.00, QColor(13, 19, 27))
        base.setColorAt(0.32, QColor(6, 10, 15))
        base.setColorAt(0.68, QColor(3, 5, 8))
        base.setColorAt(1.00, QColor(0, 0, 0))
        painter.fillPath(path, QBrush(base))

        # Layer 2: procedural graphite / terminal-grid texture.
        self._draw_micro_texture(painter, rect)

        # Layer 3: broker-terminal accent glow, kept controlled and premium.
        cyan_glow = QRadialGradient(rect.width() * 0.14, rect.height() * 0.16, rect.width() * 0.72)
        cyan_glow.setColorAt(0.0, QColor(0, 212, 255, 46))
        cyan_glow.setColorAt(0.42, QColor(0, 212, 255, 13))
        cyan_glow.setColorAt(1.0, QColor(0, 212, 255, 0))
        painter.fillPath(path, QBrush(cyan_glow))

        amber_glow = QRadialGradient(rect.width() * 0.96, rect.height() * 0.90, rect.width() * 0.72)
        amber_glow.setColorAt(0.0, QColor(245, 158, 11, 38))
        amber_glow.setColorAt(0.48, QColor(245, 158, 11, 11))
        amber_glow.setColorAt(1.0, QColor(245, 158, 11, 0))
        painter.fillPath(path, QBrush(amber_glow))

        # Layer 4: glassy side fade. Lighter than before so the texture survives.
        fade = QLinearGradient(0, 0, rect.width(), rect.height())
        fade.setColorAt(0.0, QColor(1, 3, 5, 8))
        fade.setColorAt(0.40, QColor(1, 3, 5, 36))
        fade.setColorAt(0.75, QColor(1, 3, 5, 70))
        fade.setColorAt(1.0, QColor(0, 0, 0, 120))
        painter.fillPath(path, QBrush(fade))

        # Premium edge treatment: outer line + faint inner highlight.
        painter.setClipping(False)
        painter.setPen(QPen(QColor(38, 52, 70), 1))
        painter.drawRoundedRect(shell_rect, 2, 2)
        inner_rect = shell_rect.adjusted(1.0, 1.0, -1.0, -1.0)
        painter.setPen(QPen(QColor(255, 255, 255, 18), 1))
        painter.drawRoundedRect(inner_rect, 2, 2)



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
        self.selected_ibkr_market_data_type = "live"

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
        self.setWindowTitle("Qullamaggie Swing Trader - Login")
        self.setMinimumSize(480, 520)
        self.resize(500, 540)
        self.setModal(True)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, False)


    def _setup_ui(self):
        container = LoginTextureFrame()
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
        header.setFixedHeight(38)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 0, 10, 0)
        layout.setSpacing(8)

        title = QLabel("QULLAMAGGIE")
        title.setObjectName("dialogTitle")

        left_spacer = QWidget()
        left_spacer.setFixedSize(24, 24)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.setFixedSize(24, 24)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self._on_close)

        layout.addWidget(left_spacer)
        layout.addStretch()
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignCenter)
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
                self.session_hint_label.setText("Active Kite session available. Continue with Kite to reuse the saved session.")
                self.session_hint_label.setVisible(True)
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
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)

        selection_layout = QVBoxLayout()
        selection_layout.setContentsMargins(0, 0, 0, 0)
        selection_layout.setSpacing(14)

        self.session_hint_label = QLabel("")
        self.session_hint_label.setObjectName("statusLabel")
        self.session_hint_label.setWordWrap(True)
        self.session_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.session_hint_label.setVisible(False)

        self.cancel_active_session_btn = QPushButton("RESET KITE SESSION")
        self.cancel_active_session_btn.setObjectName("subtleActionButton")
        self.cancel_active_session_btn.clicked.connect(self._clear_active_kite_session)
        self.cancel_active_session_btn.setVisible(bool(self._active_kite_session))

        broker_layout = QHBoxLayout()
        broker_layout.setContentsMargins(0, 0, 0, 0)
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

        continue_btn = QPushButton("CONTINUE")
        continue_btn.setObjectName("primaryButton")
        continue_btn.setFixedHeight(32)
        continue_btn.clicked.connect(self._on_broker_selected)

        selection_layout.addWidget(self.session_hint_label)
        selection_layout.addLayout(broker_layout)
        selection_layout.addWidget(mode_frame)

        layout.addStretch(1)
        layout.addLayout(selection_layout)
        layout.addStretch(2)
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
        card.setFixedHeight(148)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(7)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        region = "INDIA" if broker_mode == BrokerMode.INDIA else "UNITED STATES"
        region_badge = QLabel(region)
        region_badge.setObjectName("brokerRegionBadge")
        region_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_label = QLabel()
        icon_label.setObjectName("brokerIcon")
        icon_path = resource_path("assets/icons/india.svg") if broker_mode == BrokerMode.INDIA else resource_path("assets/icons/usa.svg")
        icon_label.setPixmap(QIcon(icon_path).pixmap(30, 30))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        radio = QRadioButton(broker_cfg.display_name.upper())
        radio.setObjectName("brokerRadio")
        radio.setCursor(Qt.CursorShape.PointingHandCursor)

        market_label = QLabel(broker_cfg.market)
        market_label.setObjectName("brokerMarket")
        market_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        market_label.setWordWrap(True)

        layout.addWidget(icon_label)
        layout.addWidget(radio, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(region_badge, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(market_label)

        if broker_mode == BrokerMode.INDIA:
            self.india_radio = radio
            if not KITE_AVAILABLE:
                radio.setEnabled(False)
                market_label.setText("kiteconnect unavailable")
        else:
            self.america_radio = radio
            if not is_ibkr_available():
                radio.setEnabled(False)
                market_label.setText("ib_insync unavailable")

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
        frame.setFixedHeight(44)

        layout = QHBoxLayout(frame)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.paper_radio = QRadioButton("PAPER")
        self.paper_radio.setObjectName("modeRadio")
        self.paper_radio.setCursor(Qt.CursorShape.PointingHandCursor)

        self.live_radio = QRadioButton("LIVE")
        self.live_radio.setObjectName("modeRadio")
        self.live_radio.setCursor(Qt.CursorShape.PointingHandCursor)
        self.live_radio.setChecked(True)

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
            self.session_hint_label.setText("Saved Kite session cleared. Sign in again to continue.")
            self.session_hint_label.setVisible(True)
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
            continue_text="LOGIN WITH KITE"
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

        self.generate_session_btn = QPushButton("GENERATE SESSION")
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
            self.generate_session_btn.setText("GENERATE SESSION")

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
            self.generate_session_btn.setText("GENERATING…")
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
            self.generate_session_btn.setText("GENERATE SESSION")
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
        self.ibkr_host_combo.setEditable(True)
        self.ibkr_host_combo.addItems(["127.0.0.1", "localhost", "::1"])
        self.ibkr_host_combo.setCurrentText("127.0.0.1")
        host_col.addWidget(host_label)
        host_col.addWidget(self.ibkr_host_combo)

        port_col = QVBoxLayout()
        port_col.setSpacing(4)
        port_label = QLabel("SOCKET PORT")
        port_label.setObjectName("fieldLabel")
        self.ibkr_port_input = QSpinBox()
        self.ibkr_port_input.setObjectName("terminalInput")
        self.ibkr_port_input.setRange(1, 65535)
        self.ibkr_port_input.setValue(7496)
        port_col.addWidget(port_label)
        port_col.addWidget(self.ibkr_port_input)

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
        settings_layout.addLayout(port_col, 1)
        settings_layout.addLayout(client_id_col, 1)

        market_data_panel = self._create_ibkr_market_data_selector()

        # Status/help area — rich text keeps the setup checklist readable,
        # while the same QTextEdit still handles long multi-line error messages cleanly.
        self.ibkr_status_display = QTextEdit()
        self.ibkr_status_display.setObjectName("ibkrStatusDisplay")
        self.ibkr_status_display.setReadOnly(True)
        self.ibkr_status_display.setFixedHeight(156)
        self.ibkr_status_display.setFrameShape(QFrame.Shape.NoFrame)
        self.ibkr_status_display.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.ibkr_status_display.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.ibkr_status_display.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self._set_ibkr_status_help_text()

        self.connect_ibkr_btn = QPushButton("CONNECT")
        self.connect_ibkr_btn.setObjectName("primaryButton")
        self.connect_ibkr_btn.clicked.connect(self._connect_to_ibkr)

        nav = self._create_nav_buttons(back_slot=lambda: self.stacked_widget.setCurrentIndex(1))
        nav.addWidget(self.connect_ibkr_btn)

        layout.addWidget(title)
        layout.addWidget(settings_panel)
        layout.addWidget(market_data_panel)
        layout.addWidget(self.ibkr_status_display)
        layout.addStretch()
        layout.addLayout(nav)
        return page


    def _set_ibkr_status_help_text(self):
        """Render a structured, readable IBKR setup hint instead of a raw placeholder."""
        self.ibkr_status_display.setHtml(f"""
            <div style="font-family:{UI.SANS}; color:{UI.TEXT1}; font-size:10px; font-weight:600;">
                <table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">
                    <tr>
                        <td style="color:{UI.TEXT0}; font-size:10px; font-weight:800; letter-spacing:1.1px; padding-bottom:5px;">
                            STATUS
                        </td>
                        <td align="right" style="color:{UI.CYAN}; font-size:9px; font-weight:800; letter-spacing:1px; padding-bottom:5px;">
                            WAITING FOR CONNECTION
                        </td>
                    </tr>
                </table>

                <div style="color:{UI.TEXT2}; font-size:10px; font-weight:650; margin-bottom:8px;">
                    Status messages and connection errors will appear here.
                </div>

                <div style="color:{UI.AMBER}; font-size:9px; font-weight:850; letter-spacing:1px; margin-bottom:5px;">
                    TWS / IB GATEWAY CHECKLIST
                </div>

                <table cellspacing="0" cellpadding="0" style="border-collapse:collapse;">
                    <tr>
                        <td width="22" align="center" style="color:{UI.CYAN}; font-weight:850; padding:2px 8px 2px 0;">1</td>
                        <td style="color:{UI.TEXT1}; padding:2px 0;">Login to TWS or IB Gateway.</td>
                    </tr>
                    <tr>
                        <td width="22" align="center" style="color:{UI.CYAN}; font-weight:850; padding:2px 8px 2px 0;">2</td>
                        <td style="color:{UI.TEXT1}; padding:2px 0;">Enable API: Configure → API → Settings → Enable ActiveX and Socket Clients.</td>
                    </tr>
                    <tr>
                        <td width="22" align="center" style="color:{UI.CYAN}; font-weight:850; padding:2px 8px 2px 0;">3</td>
                        <td style="color:{UI.TEXT1}; padding:2px 0;">Allow localhost in Trusted IPs: <span style="color:{UI.TEXT0};">127.0.0.1</span>.</td>
                    </tr>
                    <tr>
                        <td width="22" align="center" style="color:{UI.CYAN}; font-weight:850; padding:2px 8px 2px 0;">4</td>
                        <td style="color:{UI.TEXT1}; padding:2px 0;">Use the same API socket port configured in TWS / IB Gateway.</td>
                    </tr>
                </table>
            </div>
        """)


    def _create_ibkr_market_data_selector(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("inputPanel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)

        label = QLabel("MARKET DATA")
        label.setObjectName("fieldLabel")

        choices = QHBoxLayout()
        choices.setContentsMargins(0, 0, 0, 0)
        choices.setSpacing(6)

        self.ibkr_live_data_radio = QRadioButton("LIVE DATA")
        self.ibkr_live_data_radio.setObjectName("modeRadio")
        self.ibkr_live_data_radio.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ibkr_live_data_radio.setChecked(True)

        self.ibkr_delayed_data_radio = QRadioButton("DELAYED DATA")
        self.ibkr_delayed_data_radio.setObjectName("modeRadio")
        self.ibkr_delayed_data_radio.setCursor(Qt.CursorShape.PointingHandCursor)

        self.ibkr_market_data_group = QButtonGroup(self)
        self.ibkr_market_data_group.setExclusive(True)
        self.ibkr_market_data_group.addButton(self.ibkr_live_data_radio)
        self.ibkr_market_data_group.addButton(self.ibkr_delayed_data_radio)

        hint = QLabel(
            "Choose Live only if your IBKR account has real-time subscriptions. "
            "Choose Delayed for fee-free practice accounts; the app will request delayed ticks directly."
        )
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)

        choices.addWidget(self.ibkr_live_data_radio)
        choices.addWidget(self.ibkr_delayed_data_radio)
        layout.addWidget(label)
        layout.addLayout(choices)
        layout.addWidget(hint)
        return frame

    def _connect_to_ibkr(self):
        if self.selected_trading_mode is None:
            QMessageBox.warning(self, "Selection Required", "Please choose a trading mode first.")
            self.stacked_widget.setCurrentIndex(1)
            return

        host = self.ibkr_host_combo.currentText().strip()
        if not host:
            QMessageBox.warning(self, "Host Required", "Please enter the TWS / IB Gateway host.")
            return

        port = self.ibkr_port_input.value()
        client_id = self.ibkr_client_id_input.value()
        self.selected_ibkr_market_data_type = "delayed" if self.ibkr_delayed_data_radio.isChecked() else "live"
        self.connect_ibkr_btn.setEnabled(False)
        self.connect_ibkr_btn.setText("CONNECTING…")
        self.ibkr_status_display.clear()
        self.ibkr_status_display.setPlainText(
            f"Initiating connection to {host}:{port}...\n"
            f"Market data: {self.selected_ibkr_market_data_type.upper()} (no automatic fallback)"
        )
        self.ibkr_auth.connect_to_tws(
            trading_mode=self.selected_trading_mode,
            host=host,
            port=port,
            client_id=client_id
        )

    def _on_ibkr_status_update(self, message: str):
        # Strip markdown-style bold markers for plain display
        clean = re.sub(r"\*\*(.+?)\*\*", r"\1", message)
        self.ibkr_status_display.setPlainText(clean)
        if "✅ Connected" not in clean:
            self.connect_ibkr_btn.setEnabled(True)
            self.connect_ibkr_btn.setText("CONNECT")

    def _on_ibkr_connection_success(self, ib_client):
        host = self.ibkr_host_combo.currentText().strip()
        if not host:
            QMessageBox.warning(self, "Host Required", "Please enter the TWS / IB Gateway host.")
            return

        port = self.ibkr_port_input.value()
        client_id = self.ibkr_client_id_input.value()
        self.selected_ibkr_market_data_type = "delayed" if self.ibkr_delayed_data_radio.isChecked() else "live"
        os.environ["IBKR_HOST"] = host
        os.environ["IBKR_PORT"] = str(port)
        os.environ["IBKR_MARKET_DATA_TYPE"] = self.selected_ibkr_market_data_type
        os.environ["IBKR_MARKET_DATA_FALLBACK_DELAYED"] = "0"
        self.authentication_data = {
            "broker_mode": BrokerMode.AMERICA,
            "trading_mode": self.selected_trading_mode,
            "ib_client": ib_client,
            "client_id": client_id,
            "port": port,
            "market_data_type": self.selected_ibkr_market_data_type,
            "market_data_fallback_delayed": False,
            "connection_details": {
                "host": host,
                "port": port,
                "client_id": client_id,
                "market_data_type": self.selected_ibkr_market_data_type,
                "market_data_fallback_delayed": False,
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
            back_btn = QPushButton("BACK")
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
                font-family: {UI.SANS};
            }}

            DualModeLoginManager {{
                background: {UI.BG0};
                border: 1px solid {UI.BG6};
            }}

            #mainContainer {{
                background: transparent;
                border: none;
                border-radius: 2px;
            }}

            #titleBar {{
                background: rgba(7, 10, 13, 0.86);
                border-bottom: 1px solid rgba(34, 48, 68, 0.82);
            }}

            #dialogTitle {{
                color: {UI.TEXT0};
                font-family: {UI.SANS};
                font-size: 12px;
                font-weight: 800;
                letter-spacing: 1.4px;
                background: transparent;
            }}

            #titleBadge {{
                color: {UI.CYAN};
                background: rgba(0, 212, 255, 0.08);
                border: 1px solid rgba(0, 212, 255, 0.24);
                border-radius: 2px;
                padding: 2px 7px;
                font-family: {UI.SANS};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 1px;
            }}

            #closeButton {{
                background: transparent;
                color: {UI.TEXT2};
                border: 1px solid transparent;
                font-size: 12px;
                font-weight: 700;
                border-radius: 2px;
            }}

            #closeButton:hover {{
                color: {UI.RED};
                border-color: rgba(255, 77, 106, 0.38);
                background: rgba(255, 77, 106, 0.10);
            }}

            #loginStack,
            QWidget#loginPage {{
                background: transparent;
            }}

            #welcomeTitle,
            #pageTitle,
            #brokerPageTitle {{
                color: {UI.TEXT0};
                font-family: {UI.SANS};
                font-size: 14px;
                font-weight: 800;
                letter-spacing: 0.8px;
                background: transparent;
            }}

            #eyebrowLabel {{
                color: {UI.CYAN};
                background: rgba(0, 212, 255, 0.08);
                border: 1px solid rgba(0, 212, 255, 0.28);
                border-radius: 2px;
                padding: 3px 10px;
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 1.4px;
            }}

            #subTitle,
            #sectionLabel,
            #fieldLabel,
            #separatorLabel {{
                color: {UI.TEXT2};
                font-family: {UI.SANS};
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.9px;
                background: transparent;
            }}

            #statusLabel {{
                color: {UI.TEXT1};
                background: rgba(0, 212, 255, 0.035);
                border: 1px solid rgba(0, 212, 255, 0.18);
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

            #inputPanel {{
                background: rgba(11, 16, 21, 0.78);
                border: 1px solid rgba(34, 48, 68, 0.86);
                border-radius: 2px;
            }}

            #modeFrame {{
                background: rgba(7, 10, 13, 0.70);
                border: 1px solid rgba(34, 48, 68, 0.80);
                border-radius: 2px;
            }}

            #brokerCard {{
                background: rgba(11, 16, 21, 0.74);
                border: 1px solid rgba(34, 48, 68, 0.78);
                border-radius: 2px;
            }}

            #brokerCard:hover {{
                background: rgba(17, 25, 35, 0.88);
                border-color: rgba(0, 212, 255, 0.42);
            }}

            #brokerCard[selected="true"] {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(18, 25, 35, 0.94), stop:1 rgba(8, 12, 16, 0.88));
                border: 1px solid rgba(245, 158, 11, 0.96);
            }}

            #brokerIcon {{
                background: transparent;
            }}

            #brokerRegionBadge {{
                color: {UI.AMBER};
                background: rgba(245, 158, 11, 0.07);
                border: 1px solid rgba(245, 158, 11, 0.24);
                border-radius: 2px;
                padding: 3px 9px;
                font-family: {UI.SANS};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 1.1px;
                min-width: 78px;
            }}

            #brokerMarket {{
                color: {UI.TEXT2};
                font-size: 10px;
                font-weight: 650;
                letter-spacing: 0.4px;
                background: transparent;
            }}

            QRadioButton#brokerRadio {{
                color: {UI.TEXT0};
                font-size: 13px;
                font-weight: 800;
                letter-spacing: 0.9px;
                background: transparent;
                spacing: 0px;
            }}

            QRadioButton#brokerRadio::indicator {{
                width: 0px;
                height: 0px;
                border: none;
                image: none;
            }}

            QRadioButton#brokerRadio:disabled {{
                color: {UI.TEXT3};
            }}

            QRadioButton#modeRadio {{
                color: {UI.TEXT1};
                background: rgba(11, 16, 21, 0.76);
                border: 1px solid transparent;
                border-radius: 2px;
                padding: 7px 14px;
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 1.0px;
                spacing: 0px;
            }}

            QRadioButton#modeRadio:hover {{
                color: {UI.TEXT0};
                background: rgba(17, 25, 35, 0.90);
                border-color: rgba(0, 212, 255, 0.28);
            }}

            QRadioButton#modeRadio:checked {{
                color: #1a1200;
                background: {UI.AMBER};
                border: 1px solid {UI.AMBER};
            }}

            QRadioButton#modeRadio::indicator {{
                width: 0px;
                height: 0px;
                border: none;
                image: none;
            }}

            QLineEdit,
            QComboBox,
            QSpinBox {{
                background: rgba(17, 25, 35, 0.84);
                border: 1px solid rgba(34, 48, 68, 0.98);
                border-radius: 2px;
                padding: 7px 9px;
                color: {UI.TEXT0};
                selection-background-color: {UI.SELECTION};
                selection-color: {UI.TEXT0};
                font-family: {UI.NUM};
                font-size: 11px;
                font-weight: 600;
                min-height: 20px;
            }}

            QLineEdit:hover,
            QComboBox:hover,
            QSpinBox:hover {{
                border-color: rgba(0, 212, 255, 0.22);
            }}

            QLineEdit:focus,
            QComboBox:focus,
            QSpinBox:focus {{
                border: 1px solid {UI.CYAN};
                background: rgba(0, 212, 255, 0.055);
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
                background: {UI.BG3};
                color: {UI.TEXT0};
                border: 1px solid {UI.BG6};
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
                color: #001d17;
                border: 1px solid {UI.GREEN};
                border-radius: 2px;
                padding: 7px 15px;
                font-family: {UI.SANS};
                font-size: 10px;
                font-weight: 850;
                letter-spacing: 0.9px;
                min-width: 118px;
            }}

            #primaryButton:hover {{
                background: #21dfb8;
                border-color: #21dfb8;
                color: #001d17;
            }}

            #primaryButton:pressed {{
                background: #0fb894;
            }}

            #primaryButton:disabled {{
                background: {UI.BG4};
                color: {UI.TEXT3};
                border-color: {UI.BG5};
            }}

            #secondaryButton {{
                background: {UI.BG3};
                color: {UI.TEXT1};
                border: 1px solid {UI.BG6};
                border-radius: 2px;
                padding: 7px 14px;
                font-family: {UI.SANS};
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 0.8px;
                min-width: 80px;
            }}

            #secondaryButton:hover {{
                color: {UI.TEXT0};
                background: {UI.BG4};
                border-color: rgba(168, 188, 212, 0.36);
            }}

            #subtleActionButton {{
                background: transparent;
                border: 1px solid {UI.BG5};
                color: {UI.CYAN};
                border-radius: 2px;
                font-size: 10px;
                font-weight: 800;
                letter-spacing: 0.6px;
                padding: 4px 8px;
            }}

            #subtleActionButton:hover {{
                background: rgba(0, 212, 255, 0.065);
                border-color: rgba(0, 212, 255, 0.30);
                color: #b7f4ff;
            }}

            QRadioButton,
            QCheckBox {{
                color: {UI.TEXT1};
                font-size: 11px;
                font-weight: 650;
                spacing: 6px;
                background: transparent;
            }}

            QRadioButton::indicator {{
                width: 12px;
                height: 12px;
                border-radius: 6px;
            }}

            QRadioButton::indicator:unchecked {{
                border: 1px solid {UI.BG6};
                background: {UI.BG2};
            }}

            QRadioButton::indicator:checked {{
                border: 1px solid {UI.AMBER};
                background: {UI.AMBER};
            }}

            QRadioButton::indicator:disabled {{
                border-color: {UI.TEXT3};
                background: {UI.BG3};
            }}

            QCheckBox::indicator {{
                width: 13px;
                height: 13px;
                border-radius: 2px;
                border: 1px solid {UI.BG6};
                background: {UI.BG2};
            }}

            QCheckBox::indicator:hover {{
                border-color: rgba(0, 212, 255, 0.28);
            }}

            QCheckBox::indicator:checked {{
                background: {UI.GREEN};
                border-color: {UI.GREEN};
            }}

            #ibkrStatusDisplay {{
                background: rgba(0, 0, 0, 0.64);
                border: 1px solid rgba(34, 48, 68, 0.88);
                border-radius: 2px;
                color: {UI.TEXT1};
                selection-background-color: {UI.SELECTION};
                font-family: {UI.SANS};
                font-size: 10px;
                font-weight: 600;
                padding: 8px 9px;
            }}

            QTextEdit {{
                background: {UI.BG0};
                border: 1px solid {UI.BG5};
                color: {UI.TEXT1};
            }}

            QScrollBar:vertical {{
                background: transparent;
                width: 4px;
                border: none;
            }}

            QScrollBar::handle:vertical {{
                background: {UI.BG6};
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