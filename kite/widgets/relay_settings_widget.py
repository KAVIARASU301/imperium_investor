# kite/widgets/relay_settings_widget.py
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

from PySide6.QtCore import Qt, Signal, QThread, QTimer
from PySide6.QtWidgets import (
    QDialog, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QCheckBox, QDoubleSpinBox,
    QWidget, QApplication,
)
from PySide6.QtGui import QColor, QCursor

log = logging.getLogger(__name__)

# ── palette (matches order_dialog / login_manager dark theme) ────────────────
_BG0   = "#0d1117"
_BG1   = "#141b24"
_BG2   = "#1c2738"
_BOR   = "#253347"
_BOR2  = "#2d4060"
_T0    = "#e2eaf5"
_T1    = "#8faac8"
_T2    = "#5a7090"
_GREEN = "#3ecf8e"
_RED   = "#ff5b6e"
_AMBER = "#f59e0b"
_BLUE  = "#387ed1"
_MONO  = "JetBrains Mono, Consolas, Courier New, monospace"
_SANS  = "Segoe UI, Helvetica Neue, Arial, sans-serif"


def _css_label(color=_T1, size=10, bold=False) -> str:
    w = "700" if bold else "500"
    return (
        f"color:{color}; font-family:'{_SANS}'; font-size:{size}px;"
        f" font-weight:{w}; background:transparent; border:none;"
    )


def _css_input() -> str:
    return f"""
        QLineEdit {{
            background:{_BG2}; color:{_T0};
            border:1px solid {_BOR2}; border-radius:4px;
            font-family:'{_MONO}'; font-size:11px; font-weight:500;
            padding:6px 10px; min-height:18px;
        }}
        QLineEdit:focus {{ border:1px solid {_BLUE}; background:#1a2535; }}
        QLineEdit:disabled {{ color:{_T2}; background:{_BG1}; }}
    """


def _css_btn(color=_BLUE, text_col="#ffffff") -> str:
    return f"""
        QPushButton {{
            background:transparent; color:{color};
            border:1px solid {color}; border-radius:4px;
            font-family:'{_SANS}'; font-size:10px; font-weight:700;
            letter-spacing:0.5px; padding:5px 14px; min-height:20px;
        }}
        QPushButton:hover  {{ background:rgba(56,126,209,0.15); }}
        QPushButton:pressed{{ background:rgba(56,126,209,0.25); }}
        QPushButton:disabled{{ color:{_T2}; border-color:{_BOR}; }}
    """


# ─────────────────────────────────────────────────────────────────────────────
# CONNECTIVITY TEST THREAD
# ─────────────────────────────────────────────────────────────────────────────

class _HealthCheckWorker(QThread):
    result = Signal(bool, str)   # ok, message

    def __init__(self, url: str, secret: str):
        super().__init__()
        self._url    = url.rstrip("/") + "/health"
        self._secret = secret

    def run(self):
        import time, requests
        t0 = time.perf_counter()
        try:
            resp = requests.get(self._url, timeout=8)
            ms   = int((time.perf_counter() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                relay_id = data.get("relay", "unknown")
                self.result.emit(True, f"Connected  ·  {ms} ms  ·  {relay_id}")
            else:
                self.result.emit(False, f"HTTP {resp.status_code}: {resp.text[:80]}")
        except requests.ConnectionError:
            self.result.emit(False, "Cannot reach server — check URL / firewall")
        except requests.Timeout:
            self.result.emit(False, "Timed out after 8 s")
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

    config_changed = Signal(object)   # RelayConfig or None

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
        card_lay.setContentsMargins(14, 12, 14, 14)
        card_lay.setSpacing(10)
        root.addWidget(card)

        # ── Header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(8)

        badge = QLabel("RELAY")
        badge.setObjectName("relayBadge")

        title = QLabel("Order Relay Server")
        title.setObjectName("relayTitle")

        self._enabled_chk = QCheckBox("Active")
        self._enabled_chk.setObjectName("relayToggle")
        self._enabled_chk.setChecked(True)
        self._enabled_chk.toggled.connect(self._on_toggle)

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

        # ── URL field ────────────────────────────────────────────────────────
        card_lay.addWidget(self._field_label("RELAY SERVER URL"))
        url_row = QHBoxLayout()
        url_row.setSpacing(6)
        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("https://relay.example.com")
        self._url_input.setObjectName("relayField")
        url_row.addWidget(self._url_input)

        self._test_btn = QPushButton("Test")
        self._test_btn.setObjectName("relayTestBtn")
        self._test_btn.setFixedSize(60, 32)
        self._test_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._test_btn.clicked.connect(self._run_health_check)
        url_row.addWidget(self._test_btn)
        card_lay.addLayout(url_row)

        # ── Secret field ─────────────────────────────────────────────────────
        card_lay.addWidget(self._field_label("SHARED SECRET (HMAC-SHA256)"))
        secret_row = QHBoxLayout()
        secret_row.setSpacing(6)
        self._secret_input = QLineEdit()
        self._secret_input.setPlaceholderText("Your relay secret key")
        self._secret_input.setObjectName("relayField")
        self._secret_input.setEchoMode(QLineEdit.Password)
        secret_row.addWidget(self._secret_input)

        self._show_btn = QPushButton("👁")
        self._show_btn.setObjectName("relayShowBtn")
        self._show_btn.setFixedSize(32, 32)
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

        # ── Action buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setObjectName("relayClearBtn")
        self._clear_btn.setFixedHeight(30)
        self._clear_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._clear_btn.clicked.connect(self._clear_config)
        btn_row.addWidget(self._clear_btn)

        self._save_btn = QPushButton("Save Relay Config")
        self._save_btn.setObjectName("relaySaveBtn")
        self._save_btn.setFixedHeight(30)
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
                border-radius:8px;
            }}

            QLabel#relayBadge {{
                background:{_BLUE};
                color:#ffffff;
                font-family:'{_MONO}';
                font-size:9px; font-weight:700;
                letter-spacing:1.5px;
                padding:2px 7px;
                border-radius:3px;
            }}

            QLabel#relayTitle {{
                color:{_T0};
                font-family:'{_SANS}';
                font-size:12px; font-weight:700;
                background:transparent; border:none;
            }}

            QLabel#relayHint {{
                color:{_T2};
                font-family:'{_SANS}';
                font-size:10px; font-weight:400;
                background:transparent; border:none;
                line-height:1.4;
            }}

            QLabel#relayFieldLabel {{
                color:{_T2};
                font-family:'{_SANS}';
                font-size:9px; font-weight:700;
                letter-spacing:1.2px;
                background:transparent; border:none;
                margin-bottom:2px;
            }}

            QLabel#relayStatus {{
                font-family:'{_MONO}';
                font-size:10px; font-weight:500;
                padding:5px 10px;
                border-radius:4px;
                background:transparent; border:none;
            }}

            /* Input fields */
            QLineEdit#relayField {{
                background:{_BG2}; color:{_T0};
                border:1px solid {_BOR2}; border-radius:4px;
                font-family:'{_MONO}'; font-size:11px; font-weight:500;
                padding:6px 10px; min-height:18px;
            }}
            QLineEdit#relayField:focus {{
                border:1px solid {_BLUE}; background:#1a2535;
            }}

            /* Spinner */
            QDoubleSpinBox#relayMpSpin {{
                background:{_BG2}; color:{_T0};
                border:1px solid {_BOR2}; border-radius:4px;
                font-family:'{_MONO}'; font-size:11px;
                padding:4px 6px;
            }}
            QDoubleSpinBox#relayMpSpin:focus {{ border:1px solid {_BLUE}; }}

            /* Toggle */
            QCheckBox#relayToggle {{
                color:{_T1};
                font-family:'{_SANS}'; font-size:10px; font-weight:600;
                spacing:6px; background:transparent;
            }}
            QCheckBox#relayToggle::indicator {{
                width:28px; height:16px; border-radius:8px;
                background:{_BG2}; border:1px solid {_BOR2};
            }}
            QCheckBox#relayToggle::indicator:checked {{
                background:{_GREEN}; border:1px solid {_GREEN};
            }}

            /* Test button */
            QPushButton#relayTestBtn {{
                background:transparent; color:{_AMBER};
                border:1px solid {_AMBER}; border-radius:4px;
                font-family:'{_SANS}'; font-size:9px; font-weight:700;
                letter-spacing:0.5px;
            }}
            QPushButton#relayTestBtn:hover  {{ background:rgba(245,158,11,0.12); }}
            QPushButton#relayTestBtn:disabled{{ color:{_T2}; border-color:{_BOR}; }}

            /* Show/hide secret */
            QPushButton#relayShowBtn {{
                background:{_BG2}; color:{_T1};
                border:1px solid {_BOR2}; border-radius:4px;
                font-size:13px;
            }}
            QPushButton#relayShowBtn:hover {{ background:{_BG1}; color:{_T0}; }}
            QPushButton#relayShowBtn:checked {{ color:{_BLUE}; border-color:{_BLUE}; }}

            /* Clear */
            QPushButton#relayClearBtn {{
                background:transparent; color:{_RED};
                border:1px solid {_RED}; border-radius:4px;
                font-family:'{_SANS}'; font-size:10px; font-weight:700;
                padding:0 12px; letter-spacing:0.4px;
            }}
            QPushButton#relayClearBtn:hover {{ background:rgba(255,91,110,0.10); }}

            /* Save */
            QPushButton#relaySaveBtn {{
                background:{_BLUE}; color:#ffffff;
                border:none; border-radius:4px;
                font-family:'{_SANS}'; font-size:10px; font-weight:700;
                padding:0 14px; letter-spacing:0.4px;
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
        url    = self._url_input.text().strip()
        secret = self._secret_input.text().strip()
        if not url:
            self._set_status("Enter a relay URL first.", _AMBER)
            return

        self._test_btn.setEnabled(False)
        self._test_btn.setText("…")
        self._set_status("Connecting…", _T2)

        self._worker = _HealthCheckWorker(url, secret)
        self._worker.result.connect(self._on_health_result)
        self._worker.start()

    def _on_health_result(self, ok: bool, msg: str):
        self._test_btn.setEnabled(True)
        self._test_btn.setText("Test")
        if ok:
            self._set_status(f"✓  {msg}", _GREEN)
        else:
            self._set_status(f"✗  {msg}", _RED)

    def _set_status(self, text: str, color: str):
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(
            f"color:{color}; font-family:'{_MONO}'; font-size:10px;"
            f" font-weight:500; padding:5px 10px; border-radius:4px;"
            f" background:rgba(0,0,0,0.18); border:none;"
        )
        self._status_lbl.setVisible(True)

    def _save_config(self):
        from kite.core.relay_order_router import RelayConfig, RelayConfigStore

        url    = self._url_input.text().strip()
        secret = self._secret_input.text().strip()

        # Allow saving a "disabled" empty config (user clears relay)
        if not url and not secret:
            if self._token_manager:
                RelayConfigStore.clear(self._token_manager)
            self.config_changed.emit(None)
            self._set_status("Relay config cleared.", _T2)
            return

        if not url:
            self._set_status("URL is required.", _AMBER)
            return
        if not secret:
            self._set_status("Secret is required.", _AMBER)
            return

        try:
            cfg = RelayConfig(
                url               = url,
                secret            = secret,
                market_protection = self._mp_spin.value(),
                enabled           = self._enabled_chk.isChecked(),
            )
        except ValueError as e:
            self._set_status(str(e), _RED)
            return

        if self._token_manager:
            ok = RelayConfigStore.save(self._token_manager, cfg)
            if not ok:
                self._set_status("Failed to save (storage error).", _RED)
                return

        self.config_changed.emit(cfg)
        self._set_status("✓  Relay config saved (encrypted).", _GREEN)
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
        self._set_status("Relay config cleared.", _T2)

    def _load_saved(self):
        from kite.core.relay_order_router import RelayConfigStore
        cfg = RelayConfigStore.load(self._token_manager)
        if cfg:
            self._url_input.setText(cfg.url)
            self._secret_input.setText(cfg.secret)
            self._mp_spin.setValue(cfg.market_protection)
            self._enabled_chk.setChecked(cfg.enabled)
            log.info("Loaded relay config from storage: %s", cfg.url)

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def current_config(self):
        """Return the RelayConfig from current field values, or None if blank."""
        from kite.core.relay_order_router import RelayConfig
        url    = self._url_input.text().strip()
        secret = self._secret_input.text().strip()
        if not url or not secret:
            return None
        try:
            return RelayConfig(
                url               = url,
                secret            = secret,
                market_protection = self._mp_spin.value(),
                enabled           = self._enabled_chk.isChecked(),
            )
        except ValueError:
            return None


# ─────────────────────────────────────────────────────────────────────────────
# STANDALONE SETTINGS DIALOG  (accessible from Settings menu)
# ─────────────────────────────────────────────────────────────────────────────

class RelaySettingsDialog(QDialog):
    """
    Full-page dialog for relay config — accessible from main_window Settings menu
    without going through the login flow.
    """

    config_saved = Signal(object)   # RelayConfig or None

    def __init__(self, token_manager=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Relay Server Settings")
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self.setMinimumWidth(480)
        self.setMaximumWidth(560)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Instruction banner
        banner = QLabel(
            "⚡  SEBI/NSE require a static IP for live order placement.  "
            "Deploy relay_server.py on any cloud VM and configure it here."
        )
        banner.setWordWrap(True)
        banner.setStyleSheet(
            f"color:{_AMBER}; background:rgba(245,158,11,0.07);"
            f" border:1px solid rgba(245,158,11,0.25); border-radius:6px;"
            f" padding:8px 12px; font-family:'{_SANS}'; font-size:10px;"
        )
        root.addWidget(banner)

        self._widget = RelaySettingsWidget(token_manager, self)
        self._widget.config_changed.connect(self.config_saved.emit)
        root.addWidget(self._widget)

        self.setStyleSheet(f"QDialog {{ background:{_BG0}; }}")