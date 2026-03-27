# kite/core/market_data_worker.py
"""
MarketDataWorker — Enhanced with WebSocket order postback support.

New: KiteTicker delivers order updates via the same WebSocket channel.
     We capture them and emit order_update signal so PositionManager
     can react without ever polling the REST API.

Changes from original:
  - Added order_update Signal(dict)
  - on_order_update callback registered on KiteTicker
  - _shutdown_requested flag respected in ALL reconnect paths
  - Proper unsubscribe before close (prevents ghost subscriptions)
"""

import logging
from typing import List, Dict, Set, Union, Optional

from PySide6.QtCore import QObject, Signal, QTimer
from kiteconnect import KiteTicker

logger = logging.getLogger(__name__)
ticker_logger = logging.getLogger("kiteconnect.ticker")


class MarketDataWorker(QObject):
    """
    Manages the Kite WebSocket connection.

    Signals:
        data_received(list)          — list of tick dicts
        order_update(dict)           — real-time order status postback
        connection_established()
        connection_closed()
        connection_error(str)
    """

    data_received         = Signal(list)
    order_update          = Signal(dict)    # ← NEW: WS order postbacks
    connection_established = Signal()
    connection_closed      = Signal()
    connection_error       = Signal(str)

    def __init__(self, api_key: str, access_token: str):
        super().__init__()
        self.api_key      = api_key
        self.access_token = access_token
        self.kws: Optional[KiteTicker] = None
        self.is_running        = False
        self.subscribed_tokens: Set[int] = set()
        self._shutdown_requested = False
        self._ticker_log_level_before_shutdown: Optional[int] = None

    # ─────────────────────────────────────────────────────────────────────────
    # LIFECYCLE
    # ─────────────────────────────────────────────────────────────────────────

    def start(self):
        self._restore_ticker_logger_level()
        if self.is_running:
            logger.warning("MarketDataWorker already running")
            return
        if self._shutdown_requested:
            logger.info("Shutdown requested — skipping start")
            return

        logger.info("MarketDataWorker starting…")
        try:
            self.kws = KiteTicker(self.api_key, self.access_token)

            # Market data callbacks
            self.kws.on_ticks      = self._on_ticks
            self.kws.on_connect    = self._on_connect
            self.kws.on_close      = self._on_close
            self.kws.on_error      = self._on_error
            self.kws.on_reconnect  = self._on_reconnect

            # ── ORDER POSTBACK ──
            # KiteTicker fires on_order_update when any order status changes.
            # This is the same data as the REST /orders endpoint, delivered push.
            self.kws.on_order_update = self._on_order_update

            self.kws.connect(threaded=True)
            self.is_running = True

        except Exception as e:
            logger.error(f"MarketDataWorker start failed: {e}")
            self._safe_emit(self.connection_error, str(e))

    def stop(self):
        """Clean shutdown — unsubscribe then close WS."""
        logger.info("MarketDataWorker stopping…")
        self._shutdown_requested = True
        self.is_running = False

        # KiteTicker logs close callbacks as errors when websocket closes with
        # empty code/reason during an intentional app shutdown. Temporarily
        # suppress those expected close-noise logs.
        if self._ticker_log_level_before_shutdown is None:
            self._ticker_log_level_before_shutdown = ticker_logger.level
        ticker_logger.setLevel(logging.CRITICAL)

        if self.kws:
            try:
                tokens = list(self.subscribed_tokens)
                if tokens:
                    self.kws.unsubscribe(tokens)
                    logger.info(f"Unsubscribed {len(tokens)} tokens")
            except Exception as e:
                logger.warning(f"Unsubscribe on stop failed: {e}")

            try:
                self.kws.close()
            except Exception as e:
                logger.warning(f"KiteTicker.close() raised: {e}")

        logger.info("MarketDataWorker stopped")

    def is_connected(self) -> bool:
        """Compatibility helper for UI checks."""
        return bool(self.is_running and self.kws is not None)

    def get_subscription_info(self) -> Dict:
        return {
            "subscribed_tokens": list(self.subscribed_tokens),
            "is_running":        self.is_running,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # SUBSCRIPTION MANAGEMENT
    # ─────────────────────────────────────────────────────────────────────────

    def set_instruments(self, tokens: Union[List[int], Set[int]]) -> None:
        """Replace current subscription with new token set."""
        if isinstance(tokens, list):
            new_tokens = set(tokens)
        else:
            new_tokens = tokens

        if not self.kws or not self.is_running:
            self.subscribed_tokens = new_tokens
            return

        to_add    = new_tokens - self.subscribed_tokens
        to_remove = self.subscribed_tokens - new_tokens

        try:
            if to_remove:
                self.kws.unsubscribe(list(to_remove))
                logger.debug(f"Unsubscribed {len(to_remove)} tokens")

            if to_add:
                self.kws.subscribe(list(to_add))
                self.kws.set_mode(self.kws.MODE_FULL, list(to_add))
                logger.debug(f"Subscribed {len(to_add)} new tokens (FULL mode)")

        except Exception as e:
            logger.error(f"Subscription update failed: {e}")

        self.subscribed_tokens = new_tokens

    def add_instruments(self, tokens: List[int]) -> None:
        """Add tokens to current subscription."""
        new = [t for t in tokens if t not in self.subscribed_tokens]
        if not new:
            return
        self.subscribed_tokens.update(new)
        if self.kws and self.is_running:
            try:
                self.kws.subscribe(new)
                self.kws.set_mode(self.kws.MODE_FULL, new)
            except Exception as e:
                logger.error(f"add_instruments subscribe failed: {e}")

    def remove_instruments(self, tokens: List[int]) -> None:
        """Remove tokens from current subscription."""
        to_remove = [t for t in tokens if t in self.subscribed_tokens]
        if not to_remove:
            return
        for t in to_remove:
            self.subscribed_tokens.discard(t)
        if self.kws and self.is_running:
            try:
                self.kws.unsubscribe(to_remove)
            except Exception as e:
                logger.error(f"remove_instruments unsubscribe failed: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # KITE TICKER CALLBACKS
    # ─────────────────────────────────────────────────────────────────────────

    def _on_ticks(self, ws: KiteTicker, ticks: List[Dict]) -> None:
        if not ticks or not self.is_running:
            return
        try:
            valid = [t for t in ticks
                     if "instrument_token" in t and "last_price" in t]
            if valid:
                self._safe_emit(self.data_received, valid)
        except Exception as e:
            logger.error(f"Tick processing error: {e}")

    def _on_order_update(self, ws: KiteTicker, message: Dict) -> None:
        """
        KiteTicker calls this when any order status changes.
        message is a dict with the same fields as REST /orders response.
        We emit it directly to PositionManager via the order_update signal.
        """
        if not message:
            return
        try:
            logger.debug(
                f"WS order update: {message.get('order_id')} → {message.get('status')}"
            )
            self._safe_emit(self.order_update, message)
        except Exception as e:
            logger.error(f"Order update processing error: {e}")

    def _on_connect(self, ws: KiteTicker, *args) -> None:
        logger.info("WebSocket connected — subscribing instruments")
        if self.subscribed_tokens:
            token_list = list(self.subscribed_tokens)
            try:
                self.kws.subscribe(token_list)
                self.kws.set_mode(self.kws.MODE_FULL, token_list)
                logger.info(f"Re-subscribed {len(token_list)} tokens (FULL mode)")
            except Exception as e:
                logger.error(f"On-connect subscribe failed: {e}")

        self._safe_emit(self.connection_established)

    def _on_close(self, ws: KiteTicker, code: int, reason: str) -> None:
        if self._shutdown_requested:
            logger.info(f"WebSocket closed during shutdown — code: {code}, reason: {reason}")
        else:
            logger.warning(f"WebSocket closed — code: {code}, reason: {reason}")
        self.is_running = False
        self._safe_emit(self.connection_closed)

        # Only retry on non-intentional close and if not shutting down
        if code != 1000 and not self._shutdown_requested:
            logger.info("WebSocket closed unexpectedly — reconnecting in 5s")
            QTimer.singleShot(5_000, self._retry_connection)
        elif self._shutdown_requested:
            logger.info("Shutdown in progress — not reconnecting")
            self._restore_ticker_logger_level()

    def _on_error(self, ws: KiteTicker, code: int, reason: str) -> None:
        logger.error(f"WebSocket error — code: {code}, reason: {reason}")
        self._safe_emit(self.connection_error, str(reason))

    def _on_reconnect(self, ws: KiteTicker, attempts: int) -> None:
        logger.info(f"WebSocket reconnecting — attempt {attempts}")

    def _retry_connection(self) -> None:
        if not self.is_running and not self._shutdown_requested:
            logger.info("Retrying WebSocket connection…")
            self.start()
        elif self._shutdown_requested:
            logger.info("Shutdown in progress — cancelling reconnection")

    def _restore_ticker_logger_level(self) -> None:
        if self._ticker_log_level_before_shutdown is None:
            return
        ticker_logger.setLevel(self._ticker_log_level_before_shutdown)
        self._ticker_log_level_before_shutdown = None

    def _safe_emit(self, signal: Signal, *args) -> None:
        """
        Emit a Qt signal safely.

        During shutdown, KiteTicker callbacks can still arrive after the worker
        QObject is deleted; emitting then raises RuntimeError ("Signal source has
        been deleted"). We swallow only that lifecycle race.
        """
        try:
            signal.emit(*args)
        except RuntimeError as exc:
            if "Signal source has been deleted" in str(exc):
                logger.debug("Skipping signal emit after QObject deletion")
                return
            raise
