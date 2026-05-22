# kite/core/relay_order_router.py
"""
RelayOrderRouter — Client-side relay integration for Kite live orders.

Why this exists
───────────────
SEBI/NSE now requires a STATIC IP for live order placement. If you trade from
a dynamic IP (home broadband, 4G, etc.) your place_order / modify_order /
cancel_order calls will be rejected.

Solution: deploy relay_server.py on any cloud VM with a static IP, then route
all order mutations through it via HMAC-signed HTTPS. All non-order API calls
(quotes, positions, instruments, WebSocket) continue to hit Kite directly.

How it works
────────────
1.  RelayConfig holds the relay URL + shared secret (saved to encrypted storage
    by EnhancedTokenManager, never stored in plain text).
2.  RelayOrderRouter wraps a live KiteConnect (or KiteClientWrapper) instance.
    It overrides only the three order-mutation methods:
        place_order  → POST  /orders/{variety}
        modify_order → PUT   /orders/{variety}/{order_id}
        cancel_order → DELETE /orders/{variety}/{order_id}
    Everything else (profile, positions, quotes, instruments …) is delegated
    directly to the underlying Kite client — no relay hop.
3.  Each request is signed with HMAC-SHA256:
        payload  = f"{timestamp_unix_int}:{json_body_hex}"
        headers  = X-Relay-Timestamp, X-Relay-Signature
4.  The relay server verifies the signature and rejects requests older than 30s
    (replay-attack prevention built into the server).
5.  A health-check is performed at startup; if the relay is unreachable the
    router raises RelayUnavailableError and the caller can decide whether to
    abort or fall back (default: abort — never accidentally use dynamic IP for live).

Usage (in main_window or broker_factory)
─────────────────────────────────────────
    from kite.core.relay_order_router import RelayOrderRouter, RelayConfig

    cfg = RelayConfig(url="https://relay.example.com", secret="your-secret")
    trader = RelayOrderRouter(kite_client, relay_config=cfg,
                              api_key=api_key, access_token=access_token)
    # trader.place_order(...)  → goes via relay
    # trader.positions()       → goes direct to Kite
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from kite.core.order_router import OrderRouteMode
from typing import Any, Dict, List, Optional, Union

import requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RelayConfig:
    """Immutable relay server configuration."""
    url:                str                        # e.g. "https://relay.myvm.com"
    secret:             str                        # shared HMAC secret (min 32 chars)
    timeout_seconds:    float = 20.0
    market_protection:  float = 5.0               # % protection for MARKET / SL-M orders
    enabled:            bool  = True              # quick kill-switch without clearing config
    route_mode:         OrderRouteMode = OrderRouteMode.RELAY
    auto_fallback_enabled: bool = False
    auto_fallback_direction: str = "relay_first"
    isp_last_known_ip: str = ""
    isp_ip_confirmed_at: Optional[str] = None
    isp_ip_check_interval: int = 300

    def __post_init__(self):
        self.url = self.url.rstrip("/")
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(f"Relay URL must start with http:// or https://: {self.url}")
        if len(self.secret) < 8:
            raise ValueError("Relay secret is too short (min 8 chars)")

    @property
    def health_url(self) -> str:
        return f"{self.url}/health"

    @property
    def orders_url(self) -> str:
        return f"{self.url}/orders"


# ─────────────────────────────────────────────────────────────────────────────
# ERRORS
# ─────────────────────────────────────────────────────────────────────────────

class RelayError(RuntimeError):
    """Base class for all relay errors."""


class RelayUnavailableError(RelayError):
    """Relay server is unreachable or returned a non-2xx health response."""


class RelayAuthError(RelayError):
    """HMAC signature was rejected (wrong secret or clock skew)."""


class RelayRateLimitError(RelayError):
    """Relay server returned 429 Too Many Requests."""


class RelayOrderError(RelayError):
    """Relay forwarded the order but Kite rejected it. `kite_response` holds the raw body."""
    def __init__(self, message: str, kite_response: Optional[Dict] = None):
        super().__init__(message)
        self.kite_response = kite_response or {}


# ─────────────────────────────────────────────────────────────────────────────
# HMAC SIGNER
# ─────────────────────────────────────────────────────────────────────────────

class _HMACSigner:
    """Generates X-Relay-Timestamp / X-Relay-Signature headers."""

    def __init__(self, secret: str):
        self._secret = secret.encode()

    def sign(self, body: bytes) -> Dict[str, str]:
        ts = str(int(time.time()))
        payload = f"{ts}:{body.hex()}".encode()
        sig = hmac.new(self._secret, payload, hashlib.sha256).hexdigest()
        return {
            "X-Relay-Timestamp": ts,
            "X-Relay-Signature": sig,
        }


# ─────────────────────────────────────────────────────────────────────────────
# RELAY ORDER ROUTER
# ─────────────────────────────────────────────────────────────────────────────

class RelayOrderRouter:
    """
    Drop-in wrapper around a live KiteConnect (or KiteClientWrapper) that
    intercepts order mutations and routes them through the relay server.

    All other Kite API calls are delegated directly — zero latency penalty
    for quotes, positions, instruments, etc.
    """

    def __init__(
        self,
        kite_client,                    # KiteConnect or KiteClientWrapper instance
        relay_config: RelayConfig,
        api_key: str,
        access_token: str,
    ):
        self._kite         = kite_client
        self._cfg          = relay_config
        self._api_key      = api_key
        self._access_token = access_token
        self._signer       = _HMACSigner(relay_config.secret)
        self._session      = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

        logger.info(
            "RelayOrderRouter initialized → %s (enabled=%s)",
            relay_config.url, relay_config.enabled,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # HEALTH CHECK
    # ─────────────────────────────────────────────────────────────────────────

    def check_health(self) -> Dict[str, Any]:
        """
        Ping the relay server health endpoint.
        Returns the health JSON on success.
        Raises RelayUnavailableError on failure.
        """
        try:
            resp = self._session.get(
                self._cfg.health_url,
                timeout=self._cfg.timeout_seconds,
            )
            if resp.status_code == 200:
                data = resp.json()
                logger.info("Relay health OK: %s", data)
                return data
            raise RelayUnavailableError(
                f"Relay health returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        except requests.ConnectionError as e:
            raise RelayUnavailableError(f"Cannot reach relay server: {e}") from e
        except requests.Timeout as e:
            raise RelayUnavailableError(f"Relay health check timed out: {e}") from e

    # ─────────────────────────────────────────────────────────────────────────
    # ORDER MUTATIONS  (via relay)
    # ─────────────────────────────────────────────────────────────────────────

    def place_order(
        self,
        variety:           str  = "regular",
        exchange:          str  = "NSE",
        tradingsymbol:     str  = "",
        transaction_type:  str  = "BUY",
        quantity:          int  = 1,
        product:           str  = "CNC",
        order_type:        str  = "MARKET",
        price:             float = 0.0,
        trigger_price:     float = 0.0,
        disclosed_quantity: int  = 0,
        validity:          str  = "DAY",
        tag:               Optional[str] = None,
        **kwargs,                         # absorb extra Kite params silently
    ) -> str:
        """
        Place a live order via the relay server.
        Returns the Kite order_id string on success.
        """
        if not self._cfg.enabled:
            logger.warning("Relay disabled — falling back to direct Kite place_order")
            return self._kite.place_order(
                variety=variety, exchange=exchange, tradingsymbol=tradingsymbol,
                transaction_type=transaction_type, quantity=quantity, product=product,
                order_type=order_type, price=price, trigger_price=trigger_price,
                disclosed_quantity=disclosed_quantity, validity=validity, tag=tag,
            )

        payload: Dict[str, Any] = {
            "api_key":          self._api_key,
            "access_token":     self._access_token,
            "variety":          variety,
            "exchange":         exchange,
            "tradingsymbol":    tradingsymbol,
            "transaction_type": transaction_type,
            "quantity":         quantity,
            "product":          product,
            "order_type":       order_type,
            "price":            float(price or 0),
            "trigger_price":    float(trigger_price or 0),
            "disclosed_quantity": int(disclosed_quantity or 0),
            "validity":         validity,
            "market_protection": self._cfg.market_protection,
        }
        if tag:
            payload["tag"] = tag

        logger.info(
            "[RELAY] place_order: %s %s %d %s [%s/%s]",
            transaction_type, tradingsymbol, quantity, order_type, variety, product,
        )
        result = self._post(f"/orders/{variety}", payload)
        order_id = result.get("data", {}).get("order_id") or result.get("order_id")
        if not order_id:
            raise RelayOrderError(
                f"Relay accepted the request but Kite returned no order_id: {result}",
                kite_response=result,
            )
        logger.info("[RELAY] Order placed: %s", order_id)
        return order_id

    def modify_order(
        self,
        variety:           str,
        order_id:          str,
        quantity:          Optional[int]   = None,
        price:             Optional[float] = None,
        order_type:        Optional[str]   = None,
        trigger_price:     Optional[float] = None,
        validity:          Optional[str]   = None,
        disclosed_quantity: Optional[int]  = None,
        **kwargs,
    ) -> str:
        """Modify a pending live order via the relay server."""
        if not self._cfg.enabled:
            return self._kite.modify_order(
                variety=variety, order_id=order_id, quantity=quantity,
                price=price, order_type=order_type, trigger_price=trigger_price,
                validity=validity, disclosed_quantity=disclosed_quantity,
            )

        payload: Dict[str, Any] = {
            "api_key":      self._api_key,
            "access_token": self._access_token,
            "variety":      variety,
            "order_id":     order_id,
        }
        for k, v in [
            ("quantity", quantity), ("price", price), ("order_type", order_type),
            ("trigger_price", trigger_price), ("validity", validity),
            ("disclosed_quantity", disclosed_quantity),
        ]:
            if v is not None:
                payload[k] = v

        logger.info("[RELAY] modify_order: %s", order_id)
        result = self._put(f"/orders/{variety}/{order_id}", payload)
        return result.get("data", {}).get("order_id") or order_id

    def cancel_order(self, variety: str, order_id: str, **kwargs) -> str:
        """Cancel a pending live order via the relay server."""
        if not self._cfg.enabled:
            return self._kite.cancel_order(variety=variety, order_id=order_id)

        payload: Dict[str, Any] = {
            "api_key":      self._api_key,
            "access_token": self._access_token,
            "variety":      variety,
            "order_id":     order_id,
        }
        logger.info("[RELAY] cancel_order: %s", order_id)
        result = self._delete(f"/orders/{variety}/{order_id}", payload)
        return result.get("data", {}).get("order_id") or order_id

    # ─────────────────────────────────────────────────────────────────────────
    # ALL OTHER KITE METHODS  — delegate directly (no relay hop)
    # ─────────────────────────────────────────────────────────────────────────

    def profile(self):                          return self._kite.profile()
    def positions(self):                        return self._kite.positions()
    def holdings(self):                         return self._kite.holdings()
    def orders(self):                           return self._kite.orders()
    def order_history(self, order_id):          return self._kite.order_history(order_id)
    def trades(self):                           return self._kite.trades()
    def order_trades(self, order_id):           return self._kite.order_trades(order_id)
    def margins(self, segment=None):            return self._kite.margins(segment)
    def instruments(self, exchange=None):       return self._kite.instruments(exchange)
    def quote(self, instruments):               return self._kite.quote(instruments)
    def ltp(self, instruments):                 return self._kite.ltp(instruments)
    def ohlc(self, instruments):                return self._kite.ohlc(instruments)
    def historical_data(self, *a, **kw):        return self._kite.historical_data(*a, **kw)
    def is_connected(self) -> bool:
        try:
            self._kite.profile()
            return True
        except Exception:
            return False

    def __getattr__(self, name: str):
        """Fall through to underlying Kite client for anything not overridden."""
        return getattr(self._kite, name)

    # ─────────────────────────────────────────────────────────────────────────
    # HTTP HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _post(self, path: str, payload: Dict) -> Dict:
        return self._request("POST", path, payload)

    def _put(self, path: str, payload: Dict) -> Dict:
        return self._request("PUT", path, payload)

    def _delete(self, path: str, payload: Dict) -> Dict:
        return self._request("DELETE", path, payload)

    def _request(self, method: str, path: str, payload: Dict) -> Dict:
        url  = f"{self._cfg.url}{path}"
        body = json.dumps(payload, separators=(",", ":")).encode()
        auth_headers = self._signer.sign(body)

        try:
            resp = self._session.request(
                method=method,
                url=url,
                data=body,
                headers={**auth_headers, "Content-Type": "application/json"},
                timeout=self._cfg.timeout_seconds,
            )
        except requests.ConnectionError as e:
            raise RelayUnavailableError(f"Relay unreachable ({method} {path}): {e}") from e
        except requests.Timeout as e:
            raise RelayUnavailableError(f"Relay timeout ({method} {path}): {e}") from e

        if resp.status_code == 401:
            raise RelayAuthError(
                "HMAC signature rejected by relay. Check your relay secret and system clock."
            )
        if resp.status_code == 403:
            raise RelayAuthError("IP not in relay allowlist.")
        if resp.status_code == 429:
            raise RelayRateLimitError("Relay rate limit exceeded (>9 orders/sec).")

        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}

        if resp.status_code not in (200, 201):
            raise RelayOrderError(
                f"Relay/Kite error HTTP {resp.status_code}: {data}",
                kite_response=data,
            )

        return data


# ─────────────────────────────────────────────────────────────────────────────
# RELAY CONFIG PERSISTENCE  (thin layer over EnhancedTokenManager)
# ─────────────────────────────────────────────────────────────────────────────

class RelayConfigStore:
    """
    Save/load RelayConfig via EnhancedTokenManager's encrypted storage.
    The secret is stored encrypted alongside Kite credentials — never in plain text.
    """

    _KEY = "kite_relay_config"

    @staticmethod
    def save(token_manager, cfg: RelayConfig) -> bool:
        data = {
            "url":               cfg.url,
            "secret":            cfg.secret,
            "timeout_seconds":   cfg.timeout_seconds,
            "market_protection": cfg.market_protection,
            "enabled":           cfg.enabled,
            "route_mode":        cfg.route_mode.value,
            "auto_fallback_enabled": cfg.auto_fallback_enabled,
            "auto_fallback_direction": cfg.auto_fallback_direction,
            "isp_last_known_ip": cfg.isp_last_known_ip,
            "isp_ip_confirmed_at": cfg.isp_ip_confirmed_at,
            "isp_ip_check_interval": cfg.isp_ip_check_interval,
        }
        return token_manager.save_dialog_state(
            RelayConfigStore._KEY, json.dumps(data)
        )

    @staticmethod
    def load(token_manager) -> Optional[RelayConfig]:
        raw = token_manager.load_dialog_state(RelayConfigStore._KEY)
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return RelayConfig(
                url               = data["url"],
                secret            = data["secret"],
                timeout_seconds   = float(data.get("timeout_seconds", 20.0)),
                market_protection = float(data.get("market_protection", 5.0)),
                enabled           = bool(data.get("enabled", True)),
                route_mode        = OrderRouteMode(data.get("route_mode", "relay")),
                auto_fallback_enabled = bool(data.get("auto_fallback_enabled", False)),
                auto_fallback_direction = str(data.get("auto_fallback_direction", "relay_first")),
                isp_last_known_ip = str(data.get("isp_last_known_ip", "")),
                isp_ip_confirmed_at = data.get("isp_ip_confirmed_at"),
                isp_ip_check_interval = int(data.get("isp_ip_check_interval", 300)),
            )
        except Exception as e:
            logger.warning("Failed to load relay config: %s", e)
            return None

    @staticmethod
    def clear(token_manager) -> bool:
        return token_manager.save_dialog_state(RelayConfigStore._KEY, "")