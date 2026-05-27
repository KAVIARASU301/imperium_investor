from __future__ import annotations

from enum import Enum


class OrderRouteMode(Enum):
    RELAY = "relay"
    DIRECT_ISP = "direct_isp"
    AUTO = "auto"


class OrderRouter:
    def __init__(self, mode: OrderRouteMode, relay_router, direct_router, auto_direction: str = "relay_first"):
        self._mode = mode
        self._relay = relay_router
        self._direct = direct_router
        self._auto_direction = auto_direction

    def place_order(self, **kwargs):
        if self._mode == OrderRouteMode.RELAY:
            return self._relay.place_order(**kwargs)
        if self._mode == OrderRouteMode.DIRECT_ISP:
            return self._direct.place_order(**kwargs)
        return self._auto_place_order(**kwargs)

    def _auto_place_order(self, **kwargs):
        first, second = (self._relay, self._direct) if self._auto_direction == "relay_first" else (self._direct, self._relay)
        try:
            return first.place_order(**kwargs)
        except Exception:
            return second.place_order(**kwargs)

    def __getattr__(self, name: str):
        # route non-overridden calls to relay client by default (same as raw Kite behavior)
        return getattr(self._relay, name)
