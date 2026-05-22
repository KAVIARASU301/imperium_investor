from __future__ import annotations

import ipaddress
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

import requests
from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger(__name__)


@dataclass
class IPStatus:
    current_ip: str = ""
    last_checked: Optional[datetime] = None
    last_confirmed_working: Optional[datetime] = None
    ip_at_last_order: Optional[str] = None
    isp_name: Optional[str] = None
    check_interval_seconds: int = 300


class IPManager(QObject):
    ip_changed = Signal(str, str)  # old_ip, new_ip
    ip_checked = Signal(object)    # IPStatus

    _PROVIDERS: Tuple[str, ...] = (
        "https://api.ipify.org?format=json",
        "https://api4.ipify.org?format=json",
        "https://icanhazip.com",
        "https://checkip.amazonaws.com",
    )

    def __init__(self, check_interval_seconds: int = 300, parent=None):
        super().__init__(parent)
        self._status = IPStatus(check_interval_seconds=check_interval_seconds)
        self._lock = threading.Lock()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._refresh_in_flight = False

    def start(self):
        self.refresh()
        self._timer.start(max(30, int(self._status.check_interval_seconds)) * 1000)

    def stop(self):
        self._timer.stop()

    def set_check_interval(self, seconds: int):
        seconds = max(30, int(seconds))
        with self._lock:
            self._status.check_interval_seconds = seconds
        if self._timer.isActive():
            self._timer.start(seconds * 1000)

    def get_cached_status(self) -> IPStatus:
        with self._lock:
            return IPStatus(**self._status.__dict__)

    def mark_successful_order(self):
        with self._lock:
            self._status.last_confirmed_working = datetime.utcnow()
            self._status.ip_at_last_order = self._status.current_ip or self._status.ip_at_last_order

    def seconds_since_last_check(self) -> Optional[int]:
        with self._lock:
            if not self._status.last_checked:
                return None
            return int((datetime.utcnow() - self._status.last_checked).total_seconds())

    def refresh(self):
        if self._refresh_in_flight:
            return
        self._refresh_in_flight = True
        threading.Thread(target=self._refresh_worker, daemon=True, name="IPManagerRefresh").start()

    def _refresh_worker(self):
        try:
            old_ip = self._status.current_ip
            new_ip = self._fetch_public_ipv4()
            isp = self._fetch_isp_name(new_ip) if new_ip else None
            with self._lock:
                self._status.current_ip = new_ip or self._status.current_ip
                self._status.last_checked = datetime.utcnow()
                if isp:
                    self._status.isp_name = isp
                snapshot = IPStatus(**self._status.__dict__)
            if old_ip and new_ip and old_ip != new_ip:
                logger.warning("Public IP changed: %s -> %s", old_ip, new_ip)
                self.ip_changed.emit(old_ip, new_ip)
            self.ip_checked.emit(snapshot)
        finally:
            self._refresh_in_flight = False

    def _fetch_public_ipv4(self) -> str:
        for url in self._PROVIDERS:
            try:
                resp = requests.get(url, timeout=4)
                if resp.status_code != 200:
                    continue
                if "json" in resp.headers.get("Content-Type", ""):
                    data = resp.json()
                    value = data.get("ip", "").strip()
                else:
                    value = resp.text.strip()
                if self._is_ipv4(value):
                    return value
            except Exception:
                continue
        return ""

    @staticmethod
    def _is_ipv4(value: str) -> bool:
        try:
            return isinstance(ipaddress.ip_address(value), ipaddress.IPv4Address)
        except Exception:
            return False

    @staticmethod
    def _fetch_isp_name(ip: str) -> Optional[str]:
        if not ip:
            return None
        try:
            resp = requests.get(f"https://ipinfo.io/{ip}/json", timeout=4)
            if resp.status_code == 200:
                return (resp.json() or {}).get("org")
        except Exception:
            return None
        return None
