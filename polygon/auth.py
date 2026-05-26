"""Polygon authentication helpers backed by EnhancedTokenManager."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from login_setup.broker_modes import BrokerMode
from login_setup.token_manager import EnhancedTokenManager


@dataclass
class PolygonAuth:
    """Persist and load Polygon API key for the US broker profile."""

    token_manager: EnhancedTokenManager

    def save_api_key(self, api_key: str) -> bool:
        key = (api_key or "").strip()
        if not key:
            return False
        credentials = self.token_manager.load_broker_credentials(BrokerMode.AMERICA) or {}
        credentials["polygon_api_key"] = key
        return self.token_manager.save_broker_credentials(BrokerMode.AMERICA, credentials)

    def get_api_key(self) -> Optional[str]:
        credentials = self.token_manager.load_broker_credentials(BrokerMode.AMERICA) or {}
        value = (credentials.get("polygon_api_key") or "").strip()
        return value or None

    def clear_api_key(self) -> bool:
        credentials = self.token_manager.load_broker_credentials(BrokerMode.AMERICA) or {}
        if "polygon_api_key" not in credentials:
            return True
        credentials.pop("polygon_api_key", None)
        return self.token_manager.save_broker_credentials(BrokerMode.AMERICA, credentials)
