import logging
from typing import Set, Optional
from PySide6.QtCore import QObject, Signal
from kiteconnect import KiteTicker

logger = logging.getLogger(__name__)


class MarketDataWorker(QObject):
    """
    Manages the KiteTicker WebSocket connection from the main thread.
    The KiteTicker itself runs in a background thread.
    """
    data_received = Signal(list)
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
            logger.warning("MarketDataWorker is already running.")
            return

        logger.info("MarketDataWorker starting...")
        self.kws = KiteTicker(self.api_key, self.access_token)

        # Assign callbacks
        self.kws.on_ticks = self._on_ticks
        self.kws.on_connect = self._on_connect
        self.kws.on_close = self._on_close
        self.kws.on_error = self._on_error

        # The connected call is non-blocking and runs in its own thread
        self.kws.connect(threaded=True)
        self.is_running = True

    # The 'ws' parameter is now named '_' to indicate it's unused.
    def _on_ticks(self, _, ticks):
        """Callback for receiving ticks."""
        self.data_received.emit(ticks)

    def _on_connect(self, _, response):
        """Callback on successful connection."""
        logger.info("WebSocket connected. Subscribing to existing tokens.")
        if self.subscribed_tokens:
            self.kws.subscribe(list(self.subscribed_tokens))
            self.kws.set_mode(self.kws.MODE_FULL, list(self.subscribed_tokens))

    def _on_close(self, _, code, reason):
        """Callback on connection close."""
        logger.warning(f"WebSocket connection closed. Code: {code}, Reason: {reason}")
        self.is_running = False
        self.connection_closed.emit()

    def _on_error(self, _, code, reason):
        """Callback for WebSocket errors."""
        logger.error(f"WebSocket error. Code: {code}, Reason: {reason}")
        self.connection_error.emit(str(reason))

    def set_instruments(self, instrument_tokens: Set[int]):
        """
        Updates the list of subscribed instruments using their integer tokens.
        """
        if not self.is_running or not self.kws or not self.kws.is_connected():
            logger.warning("WebSocket not connected. Storing tokens for when it connects.")
            self.subscribed_tokens = instrument_tokens
            return

        new_tokens = set(instrument_tokens)
        old_tokens = self.subscribed_tokens

        tokens_to_add = list(new_tokens - old_tokens)
        tokens_to_remove = list(old_tokens - new_tokens)

        if tokens_to_add:
            self.kws.subscribe(tokens_to_add)
            self.kws.set_mode(self.kws.MODE_FULL, tokens_to_add)
            logger.info(f"Subscribed to {len(tokens_to_add)} new tokens.")

        if tokens_to_remove:
            self.kws.unsubscribe(tokens_to_remove)
            logger.info(f"Unsubscribed from {len(tokens_to_remove)} old tokens.")

        self.subscribed_tokens = new_tokens

    def stop(self):
        """Stops the worker and closes the WebSocket connection."""
        logger.info("Stopping MarketDataWorker...")
        if self.kws and self.is_running:
            self.kws.close(1000, "Manual close")
        self.is_running = False