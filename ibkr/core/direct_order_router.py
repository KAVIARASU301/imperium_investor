from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class PreflightResult(Enum):
    OK = "ok"
    STALE = "stale"
    UNKNOWN = "unknown"
    IP_CHANGED = "ip_changed"


class DirectRouteIPWarning(RuntimeError):
    pass


@dataclass
class PreflightStatus:
    result: PreflightResult
    message: str = ""


class DirectOrderRouter:
    def __init__(self, kite_client, ip_manager):
        self._kite = kite_client
        self._ip_manager = ip_manager

    def place_order(self, **kwargs) -> str:
        status = self._preflight_ip_check()
        if status.result in (PreflightResult.UNKNOWN, PreflightResult.IP_CHANGED):
            raise DirectRouteIPWarning(status.message)
        order_id = self._kite.place_order(**kwargs)
        self._ip_manager.mark_successful_order()
        return order_id

    def _preflight_ip_check(self) -> PreflightStatus:
        cached = self._ip_manager.get_cached_status()
        current_ip = cached.current_ip
        if not current_ip:
            return PreflightStatus(PreflightResult.UNKNOWN, "Unable to verify current public IP")

        if cached.ip_at_last_order and cached.ip_at_last_order != current_ip:
            return PreflightStatus(
                PreflightResult.IP_CHANGED,
                f"Your IP changed from {cached.ip_at_last_order} to {current_ip}. Update Kite whitelist first.",
            )

        age = self._ip_manager.seconds_since_last_check()
        if age is not None and age > 600:
            return PreflightStatus(PreflightResult.STALE, "IP check older than 10 minutes")

        return PreflightStatus(PreflightResult.OK)

    def __getattr__(self, name: str):
        return getattr(self._kite, name)
