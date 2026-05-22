# kite/widgets/order_routing_settings.py
"""
RelaySettingsWidget — compact, institutional dark panel for configuring the relay server.

Embedded inside DualModeLoginManager on the Kite credentials page.
Also available as a standalone dialog from Settings > Relay Server.

Features
────────
  • URL + Secret fields (secret masked by default)
  • Market Protection % spinner  (exchange mandate for MARKET / SL-M)
  • Enable / Disable toggle
  • Segmented routing mode selector (Relay / Direct ISP / Auto)
  • Direct ISP public IP lookup with one-click clipboard copy
  • Kite developer profile shortcut for pasting the IP
  • Live connection test with latency readout
  • Save / Close buttons (encrypted via EnhancedTokenManager)
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal, QThread, QPoint, QUrl, QTimer
from PySide6.QtWidgets import (
    QDialog, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QCheckBox, QDoubleSpinBox,
    QWidget, QApplication, QButtonGroup, QSizePolicy,
)
from PySide6.QtGui import QCursor, QDesktopServices, QMouseEvent

log = logging.getLogger(__name__)

# ── Institutional Dark Trading Terminal UI tokens ────────────────────────────
_BG0 = "#050709"      # Deepest app/dialog shell
_BG1 = "#0a0d12"      # Main window/card body
_BG2 = "#0f1318"      # Panel/input body
_BG3 = "#141920"      # Hover/inner surface
_BG4 = "#1a2030"      # Thin borders
_BGTB = "#070a0f"     # Title/footer bars
_BULL = "#00d4a8"     # Success / enabled
_BEAR = "#ff4d6a"     # Danger / error
_AMBER = "#f59e0b"    # Active / warning / selected
_CYAN = "#00d4ff"     # Info / utility
_BLUE = "#3b82f6"     # Link/action
_T0 = "#e8f0ff"       # Primary text
_T1 = "#a8bcd4"       # Secondary text
_T2 = "#5a7090"       # Muted text
_T3 = "#2a3a50"       # Disabled text
_SEL = "#1a2840"      # Selection background
_UI_FONT = "'Inter', 'Aptos', 'Segoe UI', 'Roboto', 'Noto Sans', sans-serif"
_NUM_FONT = "'Inter', 'Aptos', 'Segoe UI', sans-serif"
_MONO = "'JetBrains Mono', 'Consolas', monospace"

# Backward aliases used by existing code paths/status helpers.
_GREEN = _BULL
_RED = _BEAR
_SANS = _UI_FONT

_KITE_PROFILE_URL = "https://developers.kite.trade/profile"


# ─────────────────────────────────────────────────────────────────────────────
# CONNECTIVITY / IP WORKERS
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


class _IpLookupWorker(QThread):
    result = Signal(bool, str, str)  # ok, ipv4, ipv6_or_message

    _IPV4_ENDPOINTS = (
        "https://api4.ipify.org?format=json",
        "https://ipv4.icanhazip.com",
    )
    _IPV6_ENDPOINTS = (
        "https://api6.ipify.org?format=json",
        "https://ipv6.icanhazip.com",
    )

    def run(self):
        ipv4 = self._lookup_ip(self._IPV4_ENDPOINTS, want_ipv6=False)
        ipv6 = self._lookup_ip(self._IPV6_ENDPOINTS, want_ipv6=True)
        if ipv4 or ipv6:
            self.result.emit(True, ipv4, ipv6)
            return
        self.result.emit(False, "", "DIRECT ISP IP LOOKUP FAILED")


    @staticmethod
    def _is_ipv4(value: str) -> bool:
        try:
            return isinstance(ipaddress.ip_address(value), ipaddress.IPv4Address)
        except Exception:
            return False

    @staticmethod
    def _is_ipv6(value: str) -> bool:
        try:
            return isinstance(ipaddress.ip_address(value), ipaddress.IPv6Address)
        except Exception:
            return False

    def _lookup_ip(self, endpoints: tuple[str, ...], want_ipv6: bool) -> str:
        import requests

        validator = self._is_ipv6 if want_ipv6 else self._is_ipv4
        for url in endpoints:
            try:
                resp = requests.get(url, timeout=3)
                if resp.status_code != 200:
                    continue

                content_type = str(resp.headers.get("Content-Type", "")).lower()
                if "json" in content_type:
                    ip = str((resp.json() or {}).get("ip", "")).strip()
                else:
                    ip = str(resp.text or "").strip()

                if validator(ip):
                    return ip
            except Exception:
                continue
        return ""

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
        self._ip_worker: Optional[_IpLookupWorker] = None
        self._ip_refresh_silent = False

        self._build_ui()
        self._apply_styles()

        if token_manager:
            self._load_saved()
        else:
            self._refresh_ip_label(silent=True)

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
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(10, 10, 10, 10)
        card_lay.setSpacing(8)
        root.addWidget(card)

        # ── Header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(8)

        badge = QLabel("RELAY")
        badge.setObjectName("relayBadge")

        title = QLabel("ORDER ROUTING")
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

        # ── Concise explanation ──────────────────────────────────────────────
        hint = QLabel("Live order route, relay credentials, and Direct ISP IP registration.")
        hint.setObjectName("relayHint")
        hint.setWordWrap(True)
        card_lay.addWidget(hint)

        # ── Routing mode segmented selector (no Qt radio indicator/dot) ──────
        route_box = QFrame()
        route_box.setObjectName("relayRouteBox")
        route_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        route_lay = QHBoxLayout(route_box)
        route_lay.setContentsMargins(8, 6, 8, 6)
        route_lay.setSpacing(6)

        route_lay.addWidget(self._field_label("ROUTING MODE"))
        route_lay.addStretch(1)

        self._route_group = QButtonGroup(self)
        self._route_group.setExclusive(True)

        self._mode_relay = self._route_button("RELAY", "Use configured relay VM for live order placement.")
        self._mode_direct = self._route_button("DIRECT ISP", "Use the current ISP public IP directly.")
        self._mode_auto = self._route_button("AUTO", "Prefer relay when available, otherwise use Direct ISP.")
        self._mode_relay.setChecked(True)

        for btn in (self._mode_relay, self._mode_direct, self._mode_auto):
            self._route_group.addButton(btn)
            route_lay.addWidget(btn)

        self._route_group.buttonToggled.connect(lambda *_: self._sync_route_controls())
        card_lay.addWidget(route_box)

        # ── URL field ────────────────────────────────────────────────────────
        card_lay.addWidget(self._field_label("RELAY SERVER URL"))
        url_row = QHBoxLayout()
        url_row.setSpacing(6)
        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("https://relay.example.com")
        self._url_input.setObjectName("relayField")
        url_row.addWidget(self._url_input)

        self._test_btn = QPushButton("TEST")
        self._test_btn.setObjectName("relayOutlineBtn")
        self._test_btn.setFixedSize(56, 26)
        self._test_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._test_btn.clicked.connect(self._run_health_check)
        url_row.addWidget(self._test_btn)
        card_lay.addLayout(url_row)

        # ── Secret field ─────────────────────────────────────────────────────
        card_lay.addWidget(self._field_label("SHARED SECRET"))
        secret_row = QHBoxLayout()
        secret_row.setSpacing(6)
        self._secret_input = QLineEdit()
        self._secret_input.setPlaceholderText("Relay HMAC secret key")
        self._secret_input.setObjectName("relayField")
        self._secret_input.setEchoMode(QLineEdit.Password)
        secret_row.addWidget(self._secret_input)

        self._show_btn = QPushButton("SHOW")
        self._show_btn.setObjectName("relayGhostBtn")
        self._show_btn.setFixedSize(48, 26)
        self._show_btn.setCheckable(True)
        self._show_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._show_btn.toggled.connect(self._toggle_secret_visibility)
        secret_row.addWidget(self._show_btn)
        card_lay.addLayout(secret_row)

        # ── Market protection ────────────────────────────────────────────────
        mp_row = QHBoxLayout()
        mp_row.setSpacing(8)
        mp_row.addWidget(self._field_label("MARKET PROTECTION %"))
        mp_row.addStretch()
        self._mp_spin = QDoubleSpinBox()
        self._mp_spin.setRange(0.0, 20.0)
        self._mp_spin.setSingleStep(0.5)
        self._mp_spin.setDecimals(1)
        self._mp_spin.setValue(5.0)
        self._mp_spin.setFixedWidth(76)
        self._mp_spin.setObjectName("relayMpSpin")
        mp_row.addWidget(self._mp_spin)
        card_lay.addLayout(mp_row)

        # ── Direct ISP IP utility strip ──────────────────────────────────────
        ip_panel = QFrame()
        ip_panel.setObjectName("relayIpPanel")
        ip_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        ip_panel.setMinimumHeight(112)
        ip_lay = QVBoxLayout(ip_panel)
        ip_lay.setContentsMargins(8, 7, 8, 7)
        ip_lay.setSpacing(6)

        ip_top = QHBoxLayout()
        ip_top.setContentsMargins(0, 0, 0, 0)
        ip_top.setSpacing(6)
        ip_title = self._field_label("DIRECT ISP IP")
        ip_top.addWidget(ip_title)
        ip_top.addStretch()

        self._ip_refresh_btn = QPushButton("↻")
        self._ip_refresh_btn.setObjectName("relayIconBtn")
        self._ip_refresh_btn.setFixedSize(26, 24)
        self._ip_refresh_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._ip_refresh_btn.setToolTip("Refresh Direct ISP IP.")
        self._ip_refresh_btn.clicked.connect(self._refresh_ip_label)
        ip_top.addWidget(self._ip_refresh_btn)

        self._kite_profile_btn = QPushButton("KITE PROFILE ↗")
        self._kite_profile_btn.setObjectName("relayInfoBtn")
        self._kite_profile_btn.setFixedHeight(24)
        self._kite_profile_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._kite_profile_btn.setToolTip("Open Kite developer profile to paste this IP.")
        self._kite_profile_btn.clicked.connect(self._open_kite_profile)
        ip_top.addWidget(self._kite_profile_btn)
        ip_lay.addLayout(ip_top)

        self._ipv4_input, self._ipv4_copy_btn = self._ip_value_row(
            ip_lay,
            label="IPv4",
            tooltip="Copy IPv4 Direct ISP IP to clipboard.",
            copy_handler=lambda: self._copy_ip_to_clipboard("ipv4"),
        )
        self._ipv6_input, self._ipv6_copy_btn = self._ip_value_row(
            ip_lay,
            label="IPv6",
            tooltip="Copy IPv6 Direct ISP IP to clipboard.",
            copy_handler=lambda: self._copy_ip_to_clipboard("ipv6"),
        )

        ip_note = QLabel("Paste the matching IP in Kite whitelist. IPv6 usually works more reliably on Direct ISP.")
        ip_note.setObjectName("relayHint")
        ip_note.setWordWrap(True)
        ip_lay.addWidget(ip_note)
        card_lay.addWidget(ip_panel)

        # ── Status bar ───────────────────────────────────────────────────────
        self._status_lbl = QLabel("")
        self._status_lbl.setObjectName("relayStatus")
        self._status_lbl.setWordWrap(False)
        self._status_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._status_lbl.setMinimumHeight(0)
        self._status_lbl.setVisible(False)
        card_lay.addWidget(self._status_lbl)

        # ── Action buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 2, 0, 0)
        btn_row.setSpacing(8)
        btn_row.addStretch()

        self._save_btn = QPushButton("SAVE CONFIG")
        self._save_btn.setObjectName("relaySaveBtn")
        self._save_btn.setFixedHeight(28)
        self._save_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._save_btn.clicked.connect(self._save_config)
        btn_row.addWidget(self._save_btn)

        self._close_btn = QPushButton("CLOSE")
        self._close_btn.setObjectName("relayCloseBtn")
        self._close_btn.setFixedHeight(28)
        self._close_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._close_btn.clicked.connect(self._close_panel)
        btn_row.addWidget(self._close_btn)

        card_lay.addLayout(btn_row)

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("relayFieldLabel")
        return lbl

    def _route_button(self, text: str, tooltip: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("relayRouteBtn")
        btn.setCheckable(True)
        btn.setFixedHeight(26)
        btn.setCursor(QCursor(Qt.PointingHandCursor))
        btn.setToolTip(tooltip)
        return btn

    def _ip_value_row(self, parent_lay: QVBoxLayout, label: str, tooltip: str, copy_handler):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        type_lbl = QLabel(label)
        type_lbl.setObjectName("relayIpType")
        type_lbl.setFixedWidth(34)
        type_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        row.addWidget(type_lbl)

        ip_field = QLineEdit("--")
        ip_field.setObjectName("relayIpField")
        ip_field.setReadOnly(True)
        ip_field.setCursorPosition(0)
        ip_field.setToolTip(tooltip.replace("Copy", "Select or copy"))
        ip_field.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row.addWidget(ip_field, 1)

        copy_btn = QPushButton("COPY")
        copy_btn.setObjectName("relayCopyBtn")
        copy_btn.setFixedSize(54, 24)
        copy_btn.setCursor(QCursor(Qt.PointingHandCursor))
        copy_btn.setToolTip(tooltip)
        copy_btn.clicked.connect(copy_handler)
        row.addWidget(copy_btn)

        parent_lay.addLayout(row)
        return ip_field, copy_btn

    # ─────────────────────────────────────────────────────────────────────────
    # STYLES
    # ─────────────────────────────────────────────────────────────────────────

    def _apply_styles(self):
        self.setStyleSheet(f"""
            QWidget {{
                background:transparent;
                color:{_T0};
                font-family:{_UI_FONT};
            }}

            QFrame#relayCard {{
                background:{_BG1};
                border:1px solid {_BG4};
                border-radius:2px;
            }}

            QFrame#relayRouteBox,
            QFrame#relayIpPanel {{
                background:{_BG2};
                border:1px solid {_BG4};
                border-radius:2px;
            }}

            QLabel#relayBadge {{
                background:{_SEL};
                color:{_AMBER};
                font-family:{_UI_FONT};
                font-size:9px;
                font-weight:800;
                letter-spacing:1px;
                padding:2px 5px;
                border:1px solid rgba(245,158,11,0.35);
                border-radius:2px;
            }}

            QLabel#relayTitle {{
                color:{_T0};
                font-family:{_UI_FONT};
                font-size:12px;
                font-weight:800;
                letter-spacing:0.8px;
                background:transparent;
                border:none;
            }}

            QLabel#relayHint {{
                color:{_T2};
                font-family:{_UI_FONT};
                font-size:10px;
                font-weight:500;
                background:transparent;
                border:none;
                line-height:1.35;
            }}

            QLabel#relayFieldLabel {{
                color:{_T2};
                font-family:{_UI_FONT};
                font-size:9px;
                font-weight:800;
                letter-spacing:1px;
                background:transparent;
                border:none;
                margin:0;
                padding:0;
            }}

            QLabel#relayIpType {{
                color:{_T2};
                font-family:{_UI_FONT};
                font-size:9px;
                font-weight:800;
                letter-spacing:0.8px;
                background:transparent;
                border:none;
                padding:0;
            }}

            QLineEdit#relayIpField {{
                background:{_BG1};
                color:{_T0};
                border:1px solid {_BG4};
                border-radius:2px;
                font-family:{_NUM_FONT};
                font-size:12px;
                font-weight:700;
                padding:3px 8px;
                min-height:16px;
                selection-background-color:{_SEL};
            }}
            QLineEdit#relayIpField:hover {{
                background:{_BG3};
                border-color:{_T3};
            }}
            QLineEdit#relayIpField:focus {{
                background:{_BG3};
                border-color:{_CYAN};
            }}

            QLabel#relayStatus {{
                font-family:{_UI_FONT};
                font-size:10px;
                font-weight:700;
                padding:6px 8px;
                border-radius:2px;
                background:{_BGTB};
                border:1px solid {_BG4};
            }}

            QLineEdit#relayField {{
                background:{_BG2};
                color:{_T0};
                border:1px solid {_BG4};
                border-radius:2px;
                font-family:{_UI_FONT};
                font-size:11px;
                font-weight:600;
                padding:4px 8px;
                min-height:18px;
                selection-background-color:{_SEL};
            }}
            QLineEdit#relayField:hover {{
                border-color:{_T3};
                background:{_BG3};
            }}
            QLineEdit#relayField:focus {{
                border:1px solid {_CYAN};
                background:{_BG3};
            }}
            QLineEdit#relayField:disabled {{
                color:{_T3};
                background:{_BG1};
                border-color:{_BG4};
            }}
            QLineEdit#relayField::placeholder {{
                color:{_T3};
            }}

            QDoubleSpinBox#relayMpSpin {{
                background:{_BG2};
                color:{_T0};
                border:1px solid {_BG4};
                border-radius:2px;
                font-family:{_NUM_FONT};
                font-size:12px;
                font-weight:700;
                padding:4px 6px;
            }}
            QDoubleSpinBox#relayMpSpin:hover {{
                background:{_BG3};
                border-color:{_T3};
            }}
            QDoubleSpinBox#relayMpSpin:focus {{
                border:1px solid {_CYAN};
            }}
            QDoubleSpinBox#relayMpSpin:disabled {{
                color:{_T3};
                background:{_BG1};
            }}
            QDoubleSpinBox#relayMpSpin::up-button,
            QDoubleSpinBox#relayMpSpin::down-button {{
                width:0px;
                border:none;
            }}

            QCheckBox#relayToggle {{
                color:{_T1};
                font-family:{_UI_FONT};
                font-size:10px;
                font-weight:800;
                letter-spacing:0.6px;
                spacing:6px;
                background:transparent;
            }}
            QCheckBox#relayToggle::indicator {{
                width:12px;
                height:12px;
                border-radius:2px;
                background:{_BG2};
                border:1px solid {_BG4};
            }}
            QCheckBox#relayToggle::indicator:hover {{
                border-color:{_BULL};
            }}
            QCheckBox#relayToggle::indicator:checked {{
                background:{_BULL};
                border:1px solid {_BULL};
            }}

            QPushButton#relayRouteBtn {{
                background:{_BG1};
                color:{_T1};
                border:1px solid {_BG4};
                border-radius:2px;
                font-family:{_UI_FONT};
                font-size:10px;
                font-weight:800;
                letter-spacing:0.5px;
                padding:0 10px;
                min-width:70px;
            }}
            QPushButton#relayRouteBtn:hover {{
                color:{_T0};
                background:{_BG3};
                border-color:{_T3};
            }}
            QPushButton#relayRouteBtn:checked {{
                color:{_AMBER};
                background:{_SEL};
                border:1px solid {_AMBER};
            }}
            QPushButton#relayRouteBtn:disabled {{
                color:{_T3};
                background:{_BG1};
                border-color:{_BG4};
            }}

            QPushButton#relayOutlineBtn,
            QPushButton#relayInfoBtn,
            QPushButton#relayCopyBtn,
            QPushButton#relayIconBtn,
            QPushButton#relayGhostBtn {{
                background:transparent;
                color:{_T1};
                border:1px solid {_BG4};
                border-radius:2px;
                font-family:{_UI_FONT};
                font-size:9px;
                font-weight:800;
                letter-spacing:0.5px;
                padding:0 8px;
            }}
            QPushButton#relayOutlineBtn {{
                color:{_AMBER};
                border-color:rgba(245,158,11,0.65);
            }}
            QPushButton#relayInfoBtn {{
                color:{_CYAN};
                border-color:rgba(0,212,255,0.45);
            }}
            QPushButton#relayCopyBtn {{
                color:{_BULL};
                border-color:rgba(0,212,168,0.50);
            }}
            QPushButton#relayIconBtn {{
                color:{_T1};
                font-size:13px;
                padding:0;
            }}
            QPushButton#relayGhostBtn:checked {{
                color:{_CYAN};
                border-color:{_CYAN};
                background:rgba(0,212,255,0.10);
            }}
            QPushButton#relayOutlineBtn:hover {{
                background:rgba(245,158,11,0.10);
                border-color:{_AMBER};
            }}
            QPushButton#relayInfoBtn:hover,
            QPushButton#relayIconBtn:hover,
            QPushButton#relayGhostBtn:hover {{
                color:{_T0};
                background:{_BG3};
                border-color:{_CYAN};
            }}
            QPushButton#relayCopyBtn:hover {{
                background:rgba(0,212,168,0.10);
                border-color:{_BULL};
            }}
            QPushButton#relayOutlineBtn:disabled,
            QPushButton#relayInfoBtn:disabled,
            QPushButton#relayCopyBtn:disabled,
            QPushButton#relayIconBtn:disabled,
            QPushButton#relayGhostBtn:disabled {{
                color:{_T3};
                border-color:{_BG4};
                background:transparent;
            }}

            QPushButton#relaySaveBtn {{
                background:{_BULL};
                color:{_BG0};
                border:1px solid {_BULL};
                border-radius:2px;
                font-family:{_UI_FONT};
                font-size:10px;
                font-weight:800;
                padding:0 16px;
                letter-spacing:0.5px;
            }}
            QPushButton#relaySaveBtn:hover {{
                background:#25e5bb;
                border-color:#25e5bb;
            }}

            QPushButton#relayCloseBtn {{
                background:transparent;
                color:{_T1};
                border:1px solid {_BG4};
                border-radius:2px;
                font-family:{_UI_FONT};
                font-size:10px;
                font-weight:800;
                padding:0 16px;
                letter-spacing:0.5px;
            }}
            QPushButton#relayCloseBtn:hover {{
                color:{_T0};
                background:{_BG3};
                border-color:{_T3};
            }}
        """)

    # ─────────────────────────────────────────────────────────────────────────
    # ACTIONS
    # ─────────────────────────────────────────────────────────────────────────

    def _on_toggle(self, checked: bool):
        self._sync_route_controls()

    def _sync_route_controls(self):
        active = self._enabled_chk.isChecked()
        relay_fields_enabled = active and not self._mode_direct.isChecked()
        self._url_input.setEnabled(relay_fields_enabled)
        self._secret_input.setEnabled(relay_fields_enabled)
        self._show_btn.setEnabled(relay_fields_enabled)
        self._test_btn.setEnabled(relay_fields_enabled)
        self._mp_spin.setEnabled(active)

    def _toggle_secret_visibility(self, checked: bool):
        self._secret_input.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self._show_btn.setText("HIDE" if checked else "SHOW")

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
            self._set_status(f"✓ {msg}", _BULL)
        else:
            self._set_status(f"✗ {msg}", _BEAR)

    def _set_status(self, text: str, color: str):
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(
            f"color:{color}; font-family:{_UI_FONT}; font-size:10px;"
            f" font-weight:700; padding:6px 8px; border-radius:2px;"
            f" background:{_BGTB}; border:1px solid {color};"
        )
        self._status_lbl.setVisible(True)
        self._status_lbl.adjustSize()
        self.updateGeometry()
        QTimer.singleShot(0, self._grow_dialog_to_content)

    def _grow_dialog_to_content(self):
        """Grow the standalone dialog when the status bar appears.

        Without this, Qt keeps the previous dialog height and compresses the
        Direct ISP IP controls to make room for the newly visible status label.
        The login-embedded widget is left alone unless its top-level host is a
        QDialog, so existing external layouts are not forced to resize.
        """
        host = self.window()
        if not isinstance(host, QDialog):
            return

        # Recalculate all nested layouts before reading size hints.
        widget = self
        while widget is not None:
            layout = widget.layout()
            if layout is not None:
                layout.activate()
            if widget is host:
                break
            widget = widget.parentWidget()

        host_layout = host.layout()
        if host_layout is not None:
            host_layout.activate()

        size_hint = host.sizeHint()
        current = host.size()
        target_w = max(current.width(), size_hint.width())
        target_h = max(current.height(), size_hint.height())

        if target_w > current.width() or target_h > current.height():
            host.resize(target_w, target_h)

    def _copy_ip_to_clipboard(self, version: Optional[str] = None):
        ip = self._ip_value(version) if version else self._extract_ip_text()
        label = (version or "direct ISP IP").upper()
        if not ip:
            self._set_status(f"NO {label} AVAILABLE TO COPY.", _AMBER)
            return

        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(ip)
            self._set_status(f"{label} COPIED TO CLIPBOARD.", _BULL)
        else:
            self._set_status("CLIPBOARD IS NOT AVAILABLE.", _BEAR)

    def _open_kite_profile(self):
        opened = QDesktopServices.openUrl(QUrl(_KITE_PROFILE_URL))
        if opened:
            self._set_status("KITE PROFILE OPENED. PASTE THE DIRECT ISP IP THERE.", _CYAN)
        else:
            self._set_status("COULD NOT OPEN KITE PROFILE IN BROWSER.", _BEAR)

    def _save_config(self):
        from kite.core.relay_order_router import RelayConfig, RelayConfigStore

        url = self._url_input.text().strip()
        secret = self._secret_input.text().strip()

        # Relay credentials are optional: users can trade with direct ISP routing.
        route_mode = self._selected_route_mode()
        relay_required = route_mode.value in ("relay", "auto") and self._enabled_chk.isChecked()

        # Empty relay credentials should never block saving from the login flow.
        if not url and not secret:
            if self._token_manager:
                RelayConfigStore.clear(self._token_manager)
            self.config_changed.emit(None)
            self._set_status("RELAY CONFIG CLEARED. DIRECT ISP ROUTING REMAINS AVAILABLE.", _T2)
            return

        # Partial credentials are only invalid when relay routing is explicitly required.
        if relay_required and not url:
            self._set_status("URL IS REQUIRED FOR RELAY ROUTING.", _AMBER)
            return
        if relay_required and not secret:
            self._set_status("SECRET IS REQUIRED FOR RELAY ROUTING.", _AMBER)
            return

        # If user entered one field, request both so a complete relay profile is saved.
        if (url and not secret) or (secret and not url):
            self._set_status("ENTER BOTH URL AND SECRET, OR LEAVE BOTH BLANK.", _AMBER)
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
            self._set_status(str(e).upper(), _BEAR)
            return

        if self._token_manager:
            ok = RelayConfigStore.save(self._token_manager, cfg)
            if not ok:
                self._set_status("FAILED TO SAVE (STORAGE ERROR).", _BEAR)
                return

        self.config_changed.emit(cfg)
        self._set_status("✓ RELAY CONFIG SAVED (ENCRYPTED).", _BULL)
        log.info("Relay config saved: %s  enabled=%s", url, cfg.enabled)

    def _close_panel(self):
        """Close the host dialog/window without changing any saved config."""
        host = self.window()
        if isinstance(host, QDialog):
            host.reject()
            return

        if host is not None and host is not self:
            host.close()
            return

        self.close()

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
                if ":" in cfg.isp_last_known_ip:
                    self._set_ip_values(ipv6=cfg.isp_last_known_ip)
                else:
                    self._set_ip_values(ipv4=cfg.isp_last_known_ip)
            log.info("Loaded relay config from storage: %s", cfg.url)
        self._sync_route_controls()
        self._refresh_ip_label(silent=True)

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

    def _clean_ip_text(self, value: str) -> str:
        value = (value or "").strip()
        if value in ("", "--", "LOOKUP...", "FETCHING..."):
            return ""
        return value

    def _ip_value(self, version: Optional[str]) -> str:
        if version == "ipv4":
            return self._clean_ip_text(self._ipv4_input.text())
        if version == "ipv6":
            return self._clean_ip_text(self._ipv6_input.text())
        return ""

    def _set_ip_values(self, ipv4: str = "", ipv6: str = ""):
        self._ipv4_input.setText(ipv4 or "--")
        self._ipv6_input.setText(ipv6 or "--")
        self._ipv4_input.setCursorPosition(0)
        self._ipv6_input.setCursorPosition(0)

    def _extract_ip_text(self) -> str:
        # Stored for backward compatibility with RelayConfig's single IP field.
        # Prefer IPv6 because Kite/IP whitelisting is usually more reliable with it
        # on consumer ISPs, but still fall back to IPv4.
        return self._ip_value("ipv6") or self._ip_value("ipv4")

    def _refresh_ip_label(self, silent: bool = False):
        if self._ip_worker and self._ip_worker.isRunning():
            return

        self._ip_refresh_silent = bool(silent)
        has_existing_ip = bool(self._extract_ip_text())
        if not silent or not has_existing_ip:
            self._set_ip_values("LOOKUP...", "LOOKUP...")

        self._ip_refresh_btn.setEnabled(False)
        self._ip_refresh_btn.setText("...")
        self._ipv4_copy_btn.setEnabled(False)
        self._ipv6_copy_btn.setEnabled(False)

        self._ip_worker = _IpLookupWorker()
        self._ip_worker.result.connect(self._on_ip_lookup_result)
        self._ip_worker.start()

    def _on_ip_lookup_result(self, ok: bool, ipv4: str, payload: str):
        self._ip_refresh_btn.setEnabled(True)
        self._ip_refresh_btn.setText("↻")
        self._ipv4_copy_btn.setEnabled(True)
        self._ipv6_copy_btn.setEnabled(True)

        if ok:
            ipv6 = payload
            self._set_ip_values(ipv4=ipv4, ipv6=ipv6)
            if not self._ip_refresh_silent:
                self._set_status("DIRECT ISP IP REFRESHED.", _CYAN)
            return

        self._set_ip_values()
        if not self._ip_refresh_silent:
            self._set_status(payload or "DIRECT ISP IP LOOKUP FAILED.", _AMBER)


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
        self.setMinimumWidth(540)
        self.setMaximumWidth(620)

        self._drag_active = False
        self._drag_offset = QPoint()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Main Shell
        self._container = QFrame()
        self._container.setObjectName("dialogContainer")
        self._container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._container.setStyleSheet(f"""
            QFrame#dialogContainer {{
                background:{_BG0};
                border:1px solid {_BG4};
                border-radius:2px;
            }}
        """)
        root.addWidget(self._container)

        container_lay = QVBoxLayout(self._container)
        container_lay.setContentsMargins(0, 0, 0, 0)
        container_lay.setSpacing(0)

        # Header Bar
        hdr = QFrame()
        hdr.setFixedHeight(32)
        hdr.setStyleSheet(f"background:{_BGTB}; border-bottom:1px solid {_BG4};")
        hdr_lay = QHBoxLayout(hdr)
        hdr_lay.setContentsMargins(12, 0, 10, 0)
        hdr_lay.setSpacing(8)

        title = QLabel("RELAY SETTINGS")
        title.setStyleSheet(
            f"color:{_T0}; font-family:{_UI_FONT}; font-size:11px; "
            f"font-weight:800; letter-spacing:1px; border:none; background:transparent;"
        )
        hdr_lay.addWidget(title)
        hdr_lay.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setCursor(QCursor(Qt.PointingHandCursor))
        close_btn.clicked.connect(self.reject)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent;
                color:{_T1};
                font-size:14px;
                border:none;
                font-weight:700;
            }}
            QPushButton:hover {{
                color:{_BEAR};
                background:rgba(255,77,106,0.08);
            }}
        """)
        hdr_lay.addWidget(close_btn)

        container_lay.addWidget(hdr)

        # Body
        body_widget = QWidget()
        body_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        body_widget.setStyleSheet(f"QWidget {{ background:{_BG0}; }}")
        body_lay = QVBoxLayout(body_widget)
        body_lay.setContentsMargins(8, 8, 8, 8)
        body_lay.setSpacing(8)

        # Instruction banner
        banner = QLabel(
            "Static-IP live order setup. Deploy relay_server.py on a cloud VM, or use Direct ISP after adding this IP in Kite."
        )
        banner.setWordWrap(True)
        banner.setStyleSheet(
            f"color:{_AMBER}; background:rgba(245,158,11,0.06);"
            f" border:1px solid rgba(245,158,11,0.28); border-radius:2px;"
            f" padding:7px 10px; font-family:{_UI_FONT}; font-size:10px; font-weight:600;"
        )
        body_lay.addWidget(banner)

        self._widget = RelaySettingsWidget(token_manager, self)
        self._widget.config_changed.connect(self.config_saved.emit)
        body_lay.addWidget(self._widget)

        container_lay.addWidget(body_widget)
        QTimer.singleShot(0, self.adjustSize)

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