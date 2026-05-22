from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def build_relay_client(raw_kite_client, api_key: str, access_token: str, token_manager):
    from kite.core.direct_order_router import DirectOrderRouter
    from kite.core.ip_manager import IPManager
    from kite.core.order_router import OrderRouteMode, OrderRouter
    from kite.core.relay_order_router import RelayConfigStore, RelayOrderRouter, RelayUnavailableError

    cfg = RelayConfigStore.load(token_manager)
    if cfg is None:
        log.info("No routing config found — using direct Kite client")
        return raw_kite_client

    ip_manager = IPManager(check_interval_seconds=cfg.isp_ip_check_interval)
    ip_manager.start()

    relay_router = RelayOrderRouter(
        kite_client=raw_kite_client,
        relay_config=cfg,
        api_key=api_key,
        access_token=access_token,
    )
    direct_router = DirectOrderRouter(raw_kite_client, ip_manager)

    if cfg.route_mode == OrderRouteMode.RELAY:
        try:
            relay_router.check_health()
        except RelayUnavailableError as e:
            log.warning("Relay health check failed: %s", e)
        return relay_router

    if cfg.route_mode == OrderRouteMode.DIRECT_ISP:
        return direct_router

    return OrderRouter(
        mode=cfg.route_mode,
        relay_router=relay_router,
        direct_router=direct_router,
        auto_direction=cfg.auto_fallback_direction,
    )
