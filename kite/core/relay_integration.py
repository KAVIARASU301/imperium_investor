"""
Relay Integration — wires RelayOrderRouter into the live Kite login flow.

What this module does
─────────────────────
1.  _build_relay_client(auth_data, token_manager)
        Called by broker_factory right after a live KiteConnect is created.
        Loads RelayConfig from encrypted storage.
        If a valid relay config exists → wraps the client in RelayOrderRouter.
        If no relay config → returns the raw KiteConnect client unchanged.

2.  apply_relay_to_login_page(kite_credentials_page, token_manager)
        Injects the RelaySettingsWidget into the Kite credentials page of
        DualModeLoginManager, so users can configure the relay at login time.

Usage (in broker_factory._create_kite_client)
─────────────────────────────────────────────
    from kite.core.relay_integration import build_relay_client
    client = build_relay_client(kite_client, api_key, access_token, token_manager)
    # client is either KiteClientWrapper or RelayOrderRouter-wrapped KiteClientWrapper
"""

from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC — call from broker_factory
# ─────────────────────────────────────────────────────────────────────────────

def build_relay_client(
    raw_kite_client,
    api_key:       str,
    access_token:  str,
    token_manager,
):
    """
    Wrap `raw_kite_client` in a RelayOrderRouter if a relay config is saved.
    Returns the original client unchanged if no relay is configured.

    Parameters
    ──────────
    raw_kite_client : KiteConnect or KiteClientWrapper
    api_key         : Kite API key (forwarded to relay for Authorization header)
    access_token    : Today's Kite access token (same purpose)
    token_manager   : EnhancedTokenManager instance (provides encrypted storage)
    """
    from kite.core.relay_order_router import RelayConfigStore, RelayOrderRouter, RelayUnavailableError

    cfg = RelayConfigStore.load(token_manager)

    if cfg is None:
        log.info("No relay config found — using direct Kite client")
        return raw_kite_client

    if not cfg.enabled:
        log.info("Relay config present but disabled — using direct Kite client")
        return raw_kite_client

    log.info("Relay config found: %s  market_protection=%.1f%%", cfg.url, cfg.market_protection)

    # Connectivity pre-check (non-fatal — we warn but don't block login)
    router = RelayOrderRouter(
        kite_client   = raw_kite_client,
        relay_config  = cfg,
        api_key       = api_key,
        access_token  = access_token,
    )
    try:
        router.check_health()
        log.info("Relay health OK — all live orders will be routed via %s", cfg.url)
    except RelayUnavailableError as e:
        log.warning(
            "Relay health check FAILED: %s\n"
            "Live orders will fail until the relay is reachable. "
            "Disable the relay in Settings > Relay Server to bypass.",
            e,
        )
        # Still return the router — let the first actual order fail loudly
        # rather than silently using the dynamic IP.

    return router


# ─────────────────────────────────────────────────────────────────────────────
# UI HELPER — inject relay panel into the Kite credentials page
# ─────────────────────────────────────────────────────────────────────────────

def inject_relay_widget_into_login(credentials_page_layout, token_manager):
    """
    Append a RelaySettingsWidget to the given QLayout on the Kite credentials
    page of DualModeLoginManager.

    Call this from DualModeLoginManager._create_kite_credentials_page() after
    building the existing form, before adding the nav-button row.
    """
    from kite.widgets.relay_settings_widget import RelaySettingsWidget
    widget = RelaySettingsWidget(token_manager=token_manager)
    credentials_page_layout.addWidget(widget)
    return widget
