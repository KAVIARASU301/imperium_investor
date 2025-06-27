import logging
from typing import List, Dict, Set, Union
from PySide6.QtCore import QObject, Signal, QTimer
from kiteconnect import KiteTicker

logger = logging.getLogger(__name__)


class MarketDataWorker(QObject):
    """
    Worker class to handle real-time market data via WebSocket.
    Enhanced version with better token management and error handling.
    """

    # Signals
    data_received = Signal(list)
    connection_established = Signal()
    connection_closed = Signal()
    connection_error = Signal(str)

    def __init__(self, api_key: str, access_token: str):
        super().__init__()
        self.api_key = api_key
        self.access_token = access_token
        self.kws = None
        self.is_running = False
        self.subscribed_tokens = set()  # Always initialize as set

    def start(self):
        """Initialize and start the WebSocket connection."""
        if self.is_running:
            logger.warning("MarketDataWorker is already running.")
            return

        logger.info("MarketDataWorker starting...")
        try:
            self.kws = KiteTicker(self.api_key, self.access_token)

            # Set up callbacks
            self.kws.on_ticks = self._on_ticks
            self.kws.on_connect = self._on_connect
            self.kws.on_close = self._on_close
            self.kws.on_error = self._on_error
            self.kws.on_reconnect = self._on_reconnect

            # Start the connection
            self.kws.connect(threaded=True)
            self.is_running = True

        except Exception as e:
            logger.error(f"Failed to start MarketDataWorker: {e}")
            self.connection_error.emit(str(e))

    def _on_ticks(self, ws: KiteTicker, ticks: List[Dict]):
        """Callback for receiving market data ticks."""
        if ticks and self.is_running:
            try:
                # Enhanced tick processing
                processed_ticks = []
                for tick in ticks:
                    # Ensure all required fields exist
                    if 'instrument_token' in tick and 'last_price' in tick:
                        processed_ticks.append(tick)

                if processed_ticks:
                    self.data_received.emit(processed_ticks)

            except Exception as e:
                logger.error(f"Error processing ticks: {e}")

    def _on_connect(self, ws: KiteTicker, *args):
        """Callback when WebSocket connection is established."""
        logger.info("WebSocket connection established. Subscribing to instruments.")

        # Subscribe to stored tokens if any
        if self.subscribed_tokens:
            token_list = list(self.subscribed_tokens)
            try:
                self.kws.subscribe(token_list)
                # Set to MODE_FULL to receive complete data including volume, OHLC, change%
                self.kws.set_mode(self.kws.MODE_FULL, token_list)
                logger.info(f"Subscribed to {len(token_list)} instruments in FULL mode for complete data.")
            except Exception as e:
                logger.error(f"Failed to subscribe to tokens on connect: {e}")

        self.connection_established.emit()

    def _on_reconnect(self, ws: KiteTicker, attempts_count: int):
        """Callback on WebSocket reconnection."""
        logger.info(f"WebSocket attempting to reconnect, attempt number {attempts_count}.")

    def _on_close(self, ws: KiteTicker, code: int, reason: str):
        """Callback triggered when the WebSocket connection is closed."""
        logger.warning(f"WebSocket connection closed. Code: {code}, Reason: {reason}")
        self.is_running = False
        self.connection_closed.emit()

        # Auto-retry connection after 5 seconds if not intentionally stopped
        if code != 1000:  # 1000 is normal closure
            logger.info("Attempting to reconnect in 5 seconds...")
            # Use QTimer.singleShot to avoid threading issues
            QTimer.singleShot(5000, self._retry_connection)

    def _on_error(self, ws: KiteTicker, code: int, reason: str):
        """Callback for handling WebSocket errors."""
        logger.error(f"WebSocket error. Code: {code}, Reason: {reason}")
        self.connection_error.emit(str(reason))

    def _retry_connection(self):
        """Retry WebSocket connection."""
        if not self.is_running:
            logger.info("Retrying WebSocket connection...")
            self.start()

    def set_instruments(self, instrument_tokens: Union[List[int], Set[int]]):
        """
        Dynamically updates the list of subscribed instruments.
        This method compares the new set of tokens with the existing set and
        subscribes or unsubscribes as needed.

        Args:
            instrument_tokens: List or set of instrument tokens to subscribe to
        """
        # Convert input to set for consistent handling
        if isinstance(instrument_tokens, list):
            new_tokens = set(instrument_tokens)
        elif isinstance(instrument_tokens, set):
            new_tokens = instrument_tokens
        else:
            logger.error(f"Invalid token type: {type(instrument_tokens)}. Expected list or set.")
            return

        # Ensure subscribed_tokens is always a set
        if not isinstance(self.subscribed_tokens, set):
            self.subscribed_tokens = set()

        if not self.is_running or not self.kws or not self.kws.is_connected():
            logger.warning("WebSocket not connected. Storing tokens to subscribe upon connection.")
            self.subscribed_tokens = new_tokens
            return

        old_tokens = self.subscribed_tokens

        # Calculate differences
        tokens_to_add = list(new_tokens - old_tokens)
        tokens_to_remove = list(old_tokens - new_tokens)

        try:
            # Add new subscriptions
            if tokens_to_add:
                self.kws.subscribe(tokens_to_add)
                # Set to MODE_FULL for complete tick data including volume and change%
                self.kws.set_mode(self.kws.MODE_FULL, tokens_to_add)
                logger.info(f"Subscribed to {len(tokens_to_add)} new instruments in FULL mode.")

            # Remove old subscriptions
            if tokens_to_remove:
                self.kws.unsubscribe(tokens_to_remove)
                logger.info(f"Unsubscribed from {len(tokens_to_remove)} old instruments.")

            # Update stored tokens
            self.subscribed_tokens = new_tokens

            # Log current subscription status
            logger.info(f"Total subscribed instruments: {len(self.subscribed_tokens)}")

        except Exception as e:
            logger.error(f"Error updating instrument subscriptions: {e}")

    def add_instruments(self, instrument_tokens: Union[List[int], Set[int]]):
        """
        Add new instruments to existing subscription without removing old ones.

        Args:
            instrument_tokens: List or set of instrument tokens to add
        """
        if isinstance(instrument_tokens, list):
            tokens_to_add = set(instrument_tokens)
        elif isinstance(instrument_tokens, set):
            tokens_to_add = instrument_tokens
        else:
            logger.error(f"Invalid token type: {type(instrument_tokens)}. Expected list or set.")
            return

        # Ensure subscribed_tokens is a set
        if not isinstance(self.subscribed_tokens, set):
            self.subscribed_tokens = set()

        # Combine with existing tokens
        combined_tokens = self.subscribed_tokens.union(tokens_to_add)

        # Use the main set_instruments method
        self.set_instruments(combined_tokens)

    def remove_instruments(self, instrument_tokens: Union[List[int], Set[int]]):
        """
        Remove instruments from existing subscription.

        Args:
            instrument_tokens: List or set of instrument tokens to remove
        """
        if isinstance(instrument_tokens, list):
            tokens_to_remove = set(instrument_tokens)
        elif isinstance(instrument_tokens, set):
            tokens_to_remove = instrument_tokens
        else:
            logger.error(f"Invalid token type: {type(instrument_tokens)}. Expected list or set.")
            return

        # Ensure subscribed_tokens is a set
        if not isinstance(self.subscribed_tokens, set):
            self.subscribed_tokens = set()

        # Remove from existing tokens
        remaining_tokens = self.subscribed_tokens - tokens_to_remove

        # Use the main set_instruments method
        self.set_instruments(remaining_tokens)

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
            try:
                self.kws.set_mode(self.kws.MODE_FULL, token_list)
                logger.info(f"Force updated {len(token_list)} instruments to FULL mode.")
            except Exception as e:
                logger.error(f"Failed to force update mode: {e}")

    def clear_subscriptions(self):
        """Clear all current subscriptions."""
        if self.is_running and self.kws and self.kws.is_connected() and self.subscribed_tokens:
            try:
                token_list = list(self.subscribed_tokens)
                self.kws.unsubscribe(token_list)
                logger.info(f"Unsubscribed from {len(token_list)} instruments.")
            except Exception as e:
                logger.error(f"Failed to clear subscriptions: {e}")

        self.subscribed_tokens = set()

    def is_connected(self) -> bool:
        """Check if WebSocket is currently connected."""
        return self.is_running and self.kws and self.kws.is_connected()

    def stop(self):
        """Stops the worker and gracefully closes the WebSocket connection."""
        if not self.is_running:
            return

        logger.info("Stopping MarketDataWorker...")

        # Close WebSocket connection
        if self.kws:
            try:
                self.kws.close(code=1000, reason="User closed the application.")
            except Exception as e:
                logger.error(f"Error closing WebSocket: {e}")

        self.is_running = False
        self.subscribed_tokens = set()
        logger.info("MarketDataWorker stopped.")

    def restart(self):
        """Restart the WebSocket connection."""
        logger.info("Restarting MarketDataWorker...")
        self.stop()
        # Small delay before restart
        QTimer.singleShot(1000, self.start)