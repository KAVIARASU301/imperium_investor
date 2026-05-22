# kite/widgets/order_router_settings.py
"""
RelaySettingsWidget — compact, TC2000-dark panel for configuring the relay server.

Embedded inside DualModeLoginManager on the Kite credentials page.
Also available as a standalone dialog from Settings > Relay Server.

Features
────────
  • URL + Secret fields (secret masked by default)
  • Market Protection % spinner  (exchange mandate for MARKET / SL-M)
  • Enable / Disable toggle
  • Live connection test with latency readout
  • Save / Clear buttons (encrypted via EnhancedTokenManager)
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal, QThread, QPoint
from PySide6.QtWidgets import (
    QDialog, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QCheckBox, QDoubleSpinBox,
    QWidget, QApplication, QRadioButton,
)
from PySide6.QtGui import QColor, QCursor, QMouseEvent

log = logging.getLogger(__name__)

# ── palette (Keeping original backgrounds, just sharpening elements) ───────────
_BG0 = "#0d1117"  # Main app/dialog shell
_BG1 = "#141b24"  # Panel/Card background
_BG2 = "#1c2738"  # Input background
_BOR = "#253347"  # Panel borders
_BOR2 = "#2d4060"  # Input/Hover borders
_T0 = "#e2eaf5"  # Primary text
_T1 = "#8faac8"  # Secondary text
_T2 = "#5a7090"  # Muted text
_GREEN = "#3ecf8e"
_RED = "#ff5b6e"
_AMBER = "#f59e0b"
_BLUE = "#387ed1"
_MONO = "Consolas, 'Roboto Mono', 'Courier New', monospace"
_SANS = "Inter, 'Segoe UI', Arial, sans-serif"


# ─────────────────────────────────────────────────────────────────────────────
# CONNECTIVITY TEST THREAD
# ─────────────────────────────────────────────────────────────────────────────

class _HealthCheckWorker(QThread):
    result = Signal(bool, str)  # ok, message

    def __init__(self, url: str, secret: str):
        super().__init__()
        self._url = url.rstrip("/") + "/health"
        self._secret = secret

    def run(self):
        import time, requests
        t0 = time.perf_counter()
        try:
            resp = requests.get(self._url, timeout=8)
            ms = int((time.perf_counter() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                relay_id = data.get("relay", "unknown")
                self.result.emit(True, f"CONNECTED  ·  {ms} ms  ·  {relay_id}")
            else:
                self.result.emit(False, f"HTTP {resp.status_code}: {resp.text[:80]}")
        except requests.ConnectionError:
            self.result.emit(False, "CANNOT REACH SERVER — CHECK URL / FIREWALL")
        except requests.Timeout:
            self.result.emit(False, "TIMED OUT AFTER 8s")
        except Exception as e:
            self.result.emit(False, str(e)[:100])


# ─────────────────────────────────────────────────────────────────────────────
# RELAY SETTINGS WIDGET
# ─────────────────────────────────────────────────────────────────────────────

class RelaySettingsWidget(QWidget):
    """
    Embeddable relay-server configuration panel.

    Emits `config_changed(RelayConfig)` whenever the user saves valid settings.
    If no relay config is needed, the user leaves the URL blank and saves — the
    router is effectively disabled.
    """

    config_changed = Signal(object)  # RelayConfig or None

    def __init__(self, token_manager=None, parent=None):
        super().__init__(parent)
        self._token_manager = token_manager
        self._worker: Optional[_HealthCheckWorker] = None

        self._build_ui()
        self._apply_styles()

        if token_manager:
            self._load_saved()

    # ─────────────────────────────────────────────────────────────────────────
    # UI
    # ─────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Card shell ───────────────────────────────────────────────────────
        card = QFrame()
        card.setObjectName("relayCard")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(14, 14, 14, 14)
        card_lay.setSpacing(12)
        root.addWidget(card)

        # ── Header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(8)

        badge = QLabel("RELAY")
        badge.setObjectName("relayBadge")

        title = QLabel("ORDER RELAY SERVER")
        title.setObjectName("relayTitle")

        self._enabled_chk = QCheckBox("ACTIVE")
        self._enabled_chk.setObjectName("relayToggle")
        self._enabled_chk.setChecked(True)
        self._enabled_chk.toggled.connect(self._on_toggle)
        self._enabled_chk.setCursor(QCursor(Qt.PointingHandCursor))

        hdr.addWidget(badge)
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(self._enabled_chk)
        card_lay.addLayout(hdr)

        # ── Explanation ──────────────────────────────────────────────────────
        hint = QLabel(
            "Route live orders through your static-IP cloud VM. "
            "All other API calls (quotes, positions, WebSocket) remain direct."
        )
        hint.setObjectName("relayHint")
        hint.setWordWrap(True)
        card_lay.addWidget(hint)

        route_row = QHBoxLayout()
        route_row.setSpacing(10)
        route_row.addWidget(self._field_label("ROUTING MODE"))
        self._mode_relay = QRadioButton("Relay")
        self._mode_direct = QRadioButton("Direct ISP")
        self._mode_auto = QRadioButton("Auto")
        self._mode_relay.setChecked(True)
        for btn in (self._mode_relay, self._mode_direct, self._mode_auto):
            btn.setCursor(QCursor(Qt.PointingHandCursor))
            route_row.addWidget(btn)
        route_row.addStretch()
        card_lay.addLayout(route_row)

        # ── URL field ────────────────────────────────────────────────────────
        card_lay.addWidget(self._field_label("RELAY SERVER URL"))
        url_row = QHBoxLayout()
        url_row.setSpacing(4)
        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("https://relay.example.com")
        self._url_input.setObjectName("relayField")
        url_row.addWidget(self._url_input)

        self._test_btn = QPushButton("TEST")
        self._test_btn.setObjectName("relayTestBtn")
        self._test_btn.setFixedSize(54, 26)
        self._test_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._test_btn.clicked.connect(self._run_health_check)
        url_row.addWidget(self._test_btn)
        card_lay.addLayout(url_row)

        # ── Secret field ─────────────────────────────────────────────────────
        card_lay.addWidget(self._field_label("SHARED SECRET (HMAC-SHA256)"))
        secret_row = QHBoxLayout()
        secret_row.setSpacing(4)
        self._secret_input = QLineEdit()
        self._secret_input.setPlaceholderText("Your relay secret key")
        self._secret_input.setObjectName("relayField")
        self._secret_input.setEchoMode(QLineEdit.Password)
        secret_row.addWidget(self._secret_input)

        self._show_btn = QPushButton("👁")
        self._show_btn.setObjectName("relayShowBtn")
        self._show_btn.setFixedSize(26, 26)
        self._show_btn.setCheckable(True)
        self._show_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._show_btn.toggled.connect(
            lambda on: self._secret_input.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password
            )
        )
        secret_row.addWidget(self._show_btn)
        card_lay.addLayout(secret_row)

        # ── Market protection ────────────────────────────────────────────────
        mp_row = QHBoxLayout()
        mp_row.setSpacing(8)
        mp_row.addWidget(self._field_label("MARKET PROTECTION %  (MARKET / SL-M)"))
        mp_row.addStretch()
        self._mp_spin = QDoubleSpinBox()
        self._mp_spin.setRange(0.0, 20.0)
        self._mp_spin.setSingleStep(0.5)
        self._mp_spin.setDecimals(1)
        self._mp_spin.setValue(5.0)
        self._mp_spin.setFixedWidth(70)
        self._mp_spin.setObjectName("relayMpSpin")
        mp_row.addWidget(self._mp_spin)
        card_lay.addLayout(mp_row)

        # ── Status bar ───────────────────────────────────────────────────────
        self._status_lbl = QLabel("")
        self._status_lbl.setObjectName("relayStatus")
        self._status_lbl.setVisible(False)
        card_lay.addWidget(self._status_lbl)

        ip_row = QHBoxLayout()
        self._ip_lbl = QLabel("Public IP: --")
        self._ip_lbl.setObjectName("relayHint")
        self._ip_refresh_btn = QPushButton("↺")
        self._ip_refresh_btn.setObjectName("relayTestBtn")
        self._ip_refresh_btn.setFixedSize(28, 24)
        self._ip_refresh_btn.clicked.connect(self._refresh_ip_label)
        ip_row.addWidget(self._ip_lbl)
        ip_row.addStretch()
        ip_row.addWidget(self._ip_refresh_btn)
        card_lay.addLayout(ip_row)

        # ── Action buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)
        btn_row.setSpacing(8)
        btn_row.addStretch()

        self._clear_btn = QPushButton("CLEAR")
        self._clear_btn.setObjectName("relayClearBtn")
        self._clear_btn.setFixedHeight(28)
        self._clear_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._clear_btn.clicked.connect(self._clear_config)
        btn_row.addWidget(self._clear_btn)

        self._save_btn = QPushButton("SAVE CONFIG")
        self._save_btn.setObjectName("relaySaveBtn")
        self._save_btn.setFixedHeight(28)
        self._save_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._save_btn.clicked.connect(self._save_config)
        btn_row.addWidget(self._save_btn)

        card_lay.addLayout(btn_row)

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("relayFieldLabel")
        return lbl

    # ─────────────────────────────────────────────────────────────────────────
    # STYLES
    # ─────────────────────────────────────────────────────────────────────────

    def _apply_styles(self):
        self.setStyleSheet(f"""
            QWidget {{ background:transparent; }}

            QFrame#relayCard {{
                background:{_BG1};
                border:1px solid {_BOR};
                border-radius:1px;
            }}

            QLabel#relayBadge {{
                background:{_BLUE};
                color:#ffffff;
                font-family:'{_MONO}';
                font-size:9px; font-weight:700;
                letter-spacing:1px;
                padding:2px 4px;
                border-radius:1px;
            }}

            QLabel#relayTitle {{
                color:{_T0};
                font-family:'{_SANS}';
                font-size:11px; font-weight:800;
                letter-spacing:0.5px;
                background:transparent; border:none;
            }}

            QLabel#relayHint {{
                color:{_T2};
                font-family:'{_SANS}';
                font-size:10px; font-weight:500;
                background:transparent; border:none;
                line-height:1.4;
            }}

            QLabel#relayFieldLabel {{
                color:{_T2};
                font-family:'{_SANS}';
                font-size:9px; font-weight:700;
                letter-spacing:1px;
                background:transparent; border:none;
                margin-bottom:2px;
                margin-top:4px;
            }}

            QLabel#relayStatus {{
                font-family:'{_MONO}';
                font-size:10px; font-weight:700;
                padding:6px 10px;
                border-radius:1px;
                background:transparent; border:1px solid transparent;
            }}

            /* Input fields */
            QLineEdit#relayField {{
                background:{_BG2}; color:{_T0};
                border:1px solid {_BOR2}; border-radius:1px;
                font-family:'{_MONO}'; font-size:11px; font-weight:700;
                padding:4px 8px; min-height:18px;
            }}
            QLineEdit#relayField:focus {{
                border:1px solid {_BLUE}; background:#1a2535;
            }}

            /* Spinner */
            QDoubleSpinBox#relayMpSpin {{
                background:{_BG2}; color:{_T0};
                border:1px solid {_BOR2}; border-radius:1px;
                font-family:'{_MONO}'; font-size:12px; font-weight:700;
                padding:4px 6px;
            }}
            QDoubleSpinBox#relayMpSpin:focus {{ border:1px solid {_BLUE}; }}
            QDoubleSpinBox#relayMpSpin::up-button, QDoubleSpinBox#relayMpSpin::down-button {{ width:0px; border:none; }}

            /* Toggle/Checkbox - Sharp Institutional */
            QCheckBox#relayToggle {{
                color:{_T1};
                font-family:'{_SANS}'; font-size:10px; font-weight:700;
                letter-spacing: 0.5px; spacing:6px; background:transparent;
            }}
            QCheckBox#relayToggle::indicator {{
                width:12px; height:12px; border-radius:1px;
                background:{_BG2}; border:1px solid {_BOR2};
            }}
            QCheckBox#relayToggle::indicator:checked {{
                background:{_GREEN}; border:1px solid {_GREEN};
            }}

            /* Test button */
            QPushButton#relayTestBtn {{
                background:transparent; color:{_AMBER};
                border:1px solid {_AMBER}; border-radius:1px;
                font-family:'{_SANS}'; font-size:9px; font-weight:800;
                letter-spacing:1px;
            }}
            QPushButton#relayTestBtn:hover  {{ background:rgba(245,158,11,0.12); }}
            QPushButton#relayTestBtn:disabled{{ color:{_T2}; border-color:{_BOR}; }}

            /* Show/hide secret */
            QPushButton#relayShowBtn {{
                background:{_BG2}; color:{_T1};
                border:1px solid {_BOR2}; border-radius:1px;
                font-size:12px;
            }}
            QPushButton#relayShowBtn:hover {{ background:{_BG1}; color:{_T0}; border:1px solid {_BLUE}; }}
            QPushButton#relayShowBtn:checked {{ color:{_BLUE}; border-color:{_BLUE}; }}

            /* Clear */
            QPushButton#relayClearBtn {{
                background:transparent; color:{_RED};
                border:1px solid {_RED}; border-radius:1px;
                font-family:'{_SANS}'; font-size:10px; font-weight:800;
                padding:0 16px; letter-spacing:0.5px;
            }}
            QPushButton#relayClearBtn:hover {{ background:rgba(255,91,110,0.10); }}

            /* Save */
            QPushButton#relaySaveBtn {{
                background:{_BLUE}; color:#ffffff;
                border:none; border-radius:1px;
                font-family:'{_SANS}'; font-size:10px; font-weight:800;
                padding:0 16px; letter-spacing:0.5px;
            }}
            QPushButton#relaySaveBtn:hover {{ background:#4a90d9; }}
        """)

    # ─────────────────────────────────────────────────────────────────────────
    # ACTIONS
    # ─────────────────────────────────────────────────────────────────────────

    def _on_toggle(self, checked: bool):
        self._url_input.setEnabled(checked)
        self._secret_input.setEnabled(checked)
        self._mp_spin.setEnabled(checked)
        self._test_btn.setEnabled(checked)

    def _run_health_check(self):
        url = self._url_input.text().strip()
        secret = self._secret_input.text().strip()
        if not url:
            self._set_status("ENTER A RELAY URL FIRST.", _AMBER)
            return

        self._test_btn.setEnabled(False)
        self._test_btn.setText("...")
        self._set_status("CONNECTING...", _T2)

        self._worker = _HealthCheckWorker(url, secret)
        self._worker.result.connect(self._on_health_result)
        self._worker.start()

    def _on_health_result(self, ok: bool, msg: str):
        self._test_btn.setEnabled(True)
        self._test_btn.setText("TEST")
        if ok:
            self._set_status(f"✓ {msg}", _GREEN)
        else:
            self._set_status(f"✗ {msg}", _RED)

    def _set_status(self, text: str, color: str):
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(
            f"color:{color}; font-family:'{_MONO}'; font-size:10px;"
            f" font-weight:700; padding:6px 10px; border-radius:1px;"
            f" background:rgba(0,0,0,0.25); border:1px solid {color};"
        )
        self._status_lbl.setVisible(True)

    def _save_config(self):
        from kite.core.relay_order_router import RelayConfig, RelayConfigStore
        from kite.core.order_router import OrderRouteMode

        url = self._url_input.text().strip()
        secret = self._secret_input.text().strip()

        # Allow saving a "disabled" empty config (user clears relay)
        if not url and not secret:
            if self._token_manager:
                RelayConfigStore.clear(self._token_manager)
            self.config_changed.emit(None)
            self._set_status("RELAY CONFIG CLEARED.", _T2)
            return

        if not url:
            self._set_status("URL IS REQUIRED.", _AMBER)
            return
        if not secret:
            self._set_status("SECRET IS REQUIRED.", _AMBER)
            return

        try:
            cfg = RelayConfig(
                url=url,
                secret=secret,
                market_protection=self._mp_spin.value(),
                enabled=self._enabled_chk.isChecked(),
                route_mode=self._selected_route_mode(),
                isp_last_known_ip=self._extract_ip_text(),
            )
        except ValueError as e:
            self._set_status(str(e).upper(), _RED)
            return

        if self._token_manager:
            ok = RelayConfigStore.save(self._token_manager, cfg)
            if not ok:
                self._set_status("FAILED TO SAVE (STORAGE ERROR).", _RED)
                return

        self.config_changed.emit(cfg)
        self._set_status("✓ RELAY CONFIG SAVED (ENCRYPTED).", _GREEN)
        log.info("Relay config saved: %s  enabled=%s", url, cfg.enabled)

    def _clear_config(self):
        from kite.core.relay_order_router import RelayConfigStore
        self._url_input.clear()
        self._secret_input.clear()
        self._mp_spin.setValue(5.0)
        self._enabled_chk.setChecked(True)
        if self._token_manager:
            RelayConfigStore.clear(self._token_manager)
        self.config_changed.emit(None)
        self._set_status("RELAY CONFIG CLEARED.", _T2)

    def _load_saved(self):
        from kite.core.relay_order_router import RelayConfigStore
        cfg = RelayConfigStore.load(self._token_manager)
        if cfg:
            self._url_input.setText(cfg.url)
            self._secret_input.setText(cfg.secret)
            self._mp_spin.setValue(cfg.market_protection)
            self._enabled_chk.setChecked(cfg.enabled)
            if cfg.route_mode.value == "direct_isp":
                self._mode_direct.setChecked(True)
            elif cfg.route_mode.value == "auto":
                self._mode_auto.setChecked(True)
            else:
                self._mode_relay.setChecked(True)
            if cfg.isp_last_known_ip:
                self._ip_lbl.setText(f"Public IP: {cfg.isp_last_known_ip}")
            log.info("Loaded relay config from storage: %s", cfg.url)
        self._refresh_ip_label()

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def current_config(self):
        """Return the RelayConfig from current field values, or None if blank."""
        from kite.core.relay_order_router import RelayConfig
        url = self._url_input.text().strip()
        secret = self._secret_input.text().strip()
        if not url or not secret:
            return None
        try:
            return RelayConfig(
                url=url,
                secret=secret,
                market_protection=self._mp_spin.value(),
                enabled=self._enabled_chk.isChecked(),
                route_mode=self._selected_route_mode(),
                isp_last_known_ip=self._extract_ip_text(),
            )
        except ValueError:
            return None

    def _selected_route_mode(self):
        from kite.core.order_router import OrderRouteMode
        if self._mode_direct.isChecked():
            return OrderRouteMode.DIRECT_ISP
        if self._mode_auto.isChecked():
            return OrderRouteMode.AUTO
        return OrderRouteMode.RELAY

    def _extract_ip_text(self) -> str:
        text = self._ip_lbl.text().replace("Public IP:", "").strip()
        return "" if text == "--" else text

    def _refresh_ip_label(self):
        import requests
        ip = "--"
        for url in ("https://api4.ipify.org?format=json", "https://api.ipify.org?format=json"):
            try:
                resp = requests.get(url, timeout=3)
                if resp.status_code == 200:
                    ip = (resp.json() or {}).get("ip", "--")
                    if ip:
                        break
            except Exception:
                continue
        self._ip_lbl.setText(f"Public IP: {ip}")


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE SETTINGS DIALOG  (accessible from Settings menu)
# ─────────────────────────────────────────────────────────────────────────────

class RelaySettingsDialog(QDialog):
    """
    Full-page dialog for relay config — accessible from main_window Settings menu
    without going through the login flow. Frameless drag enabled.
    """

    config_saved = Signal(object)  # RelayConfig or None

    def __init__(self, token_manager=None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumWidth(500)
        self.setMaximumWidth(560)

        self._drag_active = False
        self._drag_offset = QPoint()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Main Shell
        self._container = QFrame()
        self._container.setObjectName("dialogContainer")
        self._container.setStyleSheet(f"""
            QFrame#dialogContainer {{
                background:{_BG0};
                border:1px solid {_BOR2};
                border-radius:1px;
            }}
        """)
        root.addWidget(self._container)

        container_lay = QVBoxLayout(self._container)
        container_lay.setContentsMargins(0, 0, 0, 0)
        container_lay.setSpacing(0)

        # Header Bar
        hdr = QFrame()
        hdr.setFixedHeight(36)
        hdr.setStyleSheet(f"background:{_BG1}; border-bottom:1px solid {_BOR};")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(16, 0, 16, 0)

        title = QLabel("RELAY SETTINGS")
        title.setStyleSheet(
            f"color:{_T0}; font-family:'{_SANS}'; font-size:11px; font-weight:800; letter-spacing:1px; border:none;")
        hdr_lay.addWidget(title)
        hdr_lay.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setCursor(QCursor(Qt.PointingHandCursor))
        close_btn.clicked.connect(self.reject)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background:transparent; color:{_T1}; font-size:16px; border:none; font-weight:bold; }}
            QPushButton:hover {{ color:{_RED}; }}
        """)
        hdr_lay.addWidget(close_btn)

        container_lay.addWidget(hdr)

        # Body
        body_widget = QWidget()
        body_lay = QVBoxLayout(body_widget)
        body_lay.setContentsMargins(16, 16, 16, 16)
        body_lay.setSpacing(12)

        # Instruction banner
        banner = QLabel(
            "⚡ SEBI/NSE require a static IP for live order placement. "
            "Deploy relay_server.py on any cloud VM and configure it here."
        )
        banner.setWordWrap(True)
        banner.setStyleSheet(
            f"color:{_AMBER}; background:rgba(245,158,11,0.07);"
            f" border:1px solid rgba(245,158,11,0.25); border-radius:1px;"
            f" padding:8px 12px; font-family:'{_SANS}'; font-size:10px; font-weight:600;"
        )
        body_lay.addWidget(banner)

        self._widget = RelaySettingsWidget(token_manager, self)
        self._widget.config_changed.connect(self.config_saved.emit)
        body_lay.addWidget(self._widget)

        container_lay.addWidget(body_widget)

    # ─────────────────────────────────────────────────────────────────────────
    # FRAMELESS WINDOW DRAG SUPPORT
    # ─────────────────────────────────────────────────────────────────────────
    def mousePressEvent(self, event: QMouseEvent):
        from PySide6.QtWidgets import QAbstractButton, QAbstractSpinBox, QLineEdit
        w = self.childAt(event.pos())
        while w:
            if isinstance(w, (QAbstractButton, QAbstractSpinBox, QLineEdit)):
                return super().mousePressEvent(event)
            w = w.parentWidget()

        if event.button() == Qt.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_active and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._drag_active = False
        super().mouseReleaseEvent(event)
