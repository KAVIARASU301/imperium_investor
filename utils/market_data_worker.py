import logging
from typing import Set, Optional, List
from PySide6.QtCore import QObject, Signal
from kiteconnect import KiteTicker

logger = logging.getLogger(__name__)


class MarketDataWorker(QObject):
    """
    Manages the KiteTicker WebSocket connection to receive real-time market data.

    This worker runs the KiteTicker in a separate thread to avoid blocking the
    main GUI thread. It handles connecting, subscribing to instruments, and
    gracefully disconnecting. For swing trading applications, it uses MODE_FULL
    to receive complete tick data including volume, OHLC, and change percentages.
    """
    data_received = Signal(list)
    connection_established = Signal()
    connection_closed = Signal()
    connection_error = Signal(str)

    def __init__(self, api_key: str, access_token: str):
        super().__init__()
        self.api_key = api_key
        self.access_token = access_token
        self.kws: Optional[KiteTicker] = None
        self.is_running = False
        self.subscribed_tokens: Set[int] = set()

    def start(self):
        """Initializes and connects the KiteTicker WebSocket client."""
        if self.is_running:
            logger.warning("MarketDataWorker is already running. Ignoring start request.")
            return

        logger.info("MarketDataWorker starting...")
        self.kws = KiteTicker(self.api_key, self.access_token)

        # Assign callbacks to handle WebSocket events
        self.kws.on_ticks = self._on_ticks
        self.kws.on_connect = self._on_connect
        self.kws.on_close = self._on_close
        self.kws.on_error = self._on_error
        self.kws.on_reconnect = self._on_reconnect

        # connect() is non-blocking and runs in its own thread
        self.kws.connect(threaded=True)
        self.is_running = True

    def _on_ticks(self, ws: KiteTicker, ticks: List[dict]):
        """Callback triggered when new market data (ticks) is received."""
        # Log sample tick data for debugging (only first tick to avoid spam)
        if ticks and logger.isEnabledFor(logging.DEBUG):
            sample_tick = ticks[0]
            logger.debug(f"Sample tick data: {sample_tick}")

        self.data_received.emit(ticks)

    def _on_connect(self, ws: KiteTicker, response: dict):
        """Callback triggered on a successful WebSocket connection."""
        logger.info("WebSocket connection established. Subscribing to instruments.")
        if self.subscribed_tokens:
            token_list = list(self.subscribed_tokens)
            self.kws.subscribe(token_list)
            # Set to MODE_FULL to receive complete data including volume, OHLC, change%
            self.kws.set_mode(self.kws.MODE_FULL, token_list)
            logger.info(f"Subscribed to {len(token_list)} instruments in FULL mode for complete data.")
        self.connection_established.emit()

    def _on_reconnect(self, ws: KiteTicker, attempts_count: int):
        """Callback on WebSocket reconnection."""
        logger.info(f"WebSocket attempting to reconnect, attempt number {attempts_count}.")

    def _on_close(self, ws: KiteTicker, code: int, reason: str):
        """Callback triggered when the WebSocket connection is closed."""
        logger.warning(f"WebSocket connection closed. Code: {code}, Reason: {reason}")
        self.is_running = False
        self.connection_closed.emit()

    def _on_error(self, ws: KiteTicker, code: int, reason: str):
        """Callback for handling WebSocket errors."""
        logger.error(f"WebSocket error. Code: {code}, Reason: {reason}")
        self.connection_error.emit(str(reason))

    def set_instruments(self, instrument_tokens: Set[int]):
        """
        Dynamically updates the list of subscribed instruments.
        This method compares the new set of tokens with the existing set and
        subscribes or unsubscribes as needed.
        """
        if not self.is_running or not self.kws or not self.kws.is_connected():
            logger.warning("WebSocket not connected. Storing tokens to subscribe upon connection.")
            self.subscribed_tokens = instrument_tokens
            return

        new_tokens = set(instrument_tokens)
        old_tokens = self.subscribed_tokens

        tokens_to_add = list(new_tokens - old_tokens)
        tokens_to_remove = list(old_tokens - new_tokens)

        if tokens_to_add:
            self.kws.subscribe(tokens_to_add)
            # Set to MODE_FULL for complete tick data including volume and change%
            self.kws.set_mode(self.kws.MODE_FULL, tokens_to_add)
            logger.info(f"Subscribed to {len(tokens_to_add)} new instruments in FULL mode.")

        if tokens_to_remove:
            self.kws.unsubscribe(tokens_to_remove)
            logger.info(f"Unsubscribed from {len(tokens_to_remove)} old instruments.")

        self.subscribed_tokens = new_tokens

        # Log current subscription status
        logger.info(f"Total subscribed instruments: {len(self.subscribed_tokens)}")

    def get_subscription_info(self):
        """Returns current subscription information for debugging."""
        return {
            "is_running": self.is_running,
            "is_connected": self.kws.is_connected() if self.kws else False,
            "subscribed_count": len(self.subscribed_tokens),
            "subscribed_tokens": list(self.subscribed_tokens)
        }

    def force_mode_update(self):
        """
        Force update all subscribed instruments to MODE_FULL.
        Useful for ensuring all instruments are in the correct mode.
        """
        if self.is_running and self.kws and self.kws.is_connected() and self.subscribed_tokens:
            token_list = list(self.subscribed_tokens)
            self.kws.set_mode(self.kws.MODE_FULL, token_list)
            logger.info(f"Force updated {len(token_list)} instruments to FULL mode.")

    def stop(self):
        """Stops the worker and gracefully closes the WebSocket connection."""
        if not self.is_running:
            return

        logger.info("Stopping MarketDataWorker...")
        if self.kws:
            self.kws.close(code=1000, reason="User closed the application.")
        self.is_running = False