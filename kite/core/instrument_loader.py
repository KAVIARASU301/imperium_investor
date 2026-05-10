# src/utils/instrument_loader.py

"""Robust instrument loader for fetching trading instruments from Zerodha with enhanced retry logic"""

import logging
import time
import pickle
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from PySide6.QtCore import QThread, Signal
from kiteconnect import KiteConnect
from kite.widgets.search_bar import SymbolIndex
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class InstrumentLoader(QThread):
    """Background thread for loading instruments from Zerodha with robust retry logic and caching"""

    instruments_loaded = Signal(dict)
    error_occurred = Signal(str)
    progress_update = Signal(str)  # For status updates

    def __init__(self, kite_client: KiteConnect, cache_dir: str = None):
        super().__init__()
        self.kite = kite_client
        self.cache_dir = cache_dir or os.path.expanduser("~/.qullamaggie/cache")
        self.cache_file = os.path.join(self.cache_dir, "instruments_cache.pkl")
        self.cache_info_file = os.path.join(self.cache_dir, "cache_info.pkl")
        self._stop_requested = False

        # Create cache directory if it doesn't exist
        os.makedirs(self.cache_dir, exist_ok=True)

        # Configure requests session with retry strategy
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            status_forcelist=[429, 500, 502, 503, 504],
            backoff_factor=1,
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def stop(self):
        """Request the thread to stop"""
        self._stop_requested = True
        logger.info("Stop requested for InstrumentLoader")

    def is_cache_valid(self) -> bool:
        """Check if cached instruments are still valid (within 24 hours)"""
        try:
            if not os.path.exists(self.cache_file) or not os.path.exists(self.cache_info_file):
                return False

            with open(self.cache_info_file, 'rb') as f:
                cache_info = pickle.load(f)

            cache_time = cache_info.get('timestamp')
            if not cache_time:
                return False

            # Check if cache is less than 24 hours old
            cache_age = datetime.now() - cache_time
            is_valid = cache_age < timedelta(hours=24)

            if is_valid:
                logger.info(f"Using cached instruments (age: {cache_age})")
            else:
                logger.info(f"Cache expired (age: {cache_age})")

            return is_valid

        except Exception as e:
            logger.error(f"Error checking cache validity: {e}")
            return False

    def load_cached_instruments(self) -> Optional[List[Dict[str, Any]]]:
        """Load instruments from cache"""
        try:
            with open(self.cache_file, 'rb') as f:
                instruments = pickle.load(f)
            logger.info(f"Loaded {len(instruments)} instruments from cache")
            return instruments
        except Exception as e:
            logger.error(f"Error loading cached instruments: {e}")
            return None

    def save_instruments_to_cache(self, instruments: List[Dict[str, Any]]):
        """Save instruments to cache with timestamp"""
        try:
            # Save instruments
            with open(self.cache_file, 'wb') as f:
                pickle.dump(instruments, f)

            # Save cache info
            cache_info = {
                'timestamp': datetime.now(),
                'count': len(instruments)
            }
            with open(self.cache_info_file, 'wb') as f:
                pickle.dump(cache_info, f)

            logger.info(f"Cached {len(instruments)} instruments")

        except Exception as e:
            logger.error(f"Error saving instruments to cache: {e}")

    def fetch_instruments_with_fallback(self) -> List[Dict[str, Any]]:
        """Fetch instruments with multiple fallback strategies - NSE FIRST"""
        max_retries = 5
        base_delay = 2
        exchanges = ["NSE", "BSE"]  # This order is CRITICAL - NSE must be first

        for attempt in range(max_retries):
            if self._stop_requested:
                logger.info("Stop requested, aborting instrument fetch")
                raise Exception("Operation cancelled by user")

            try:
                self.progress_update.emit(f"Attempt {attempt + 1}/{max_retries}: Fetching instruments...")
                logger.info(f"Attempt {attempt + 1}: Loading instruments...")

                # Try to fetch instruments with increased timeout
                all_instruments = []

                # Set a longer timeout for the KiteConnect client
                original_timeout = getattr(self.kite, 'timeout', 7)
                self.kite.timeout = min(30, original_timeout + (attempt * 5))

                # IMPORTANT: Process NSE first, then BSE
                for exchange in exchanges:
                    if self._stop_requested:
                        raise Exception("Operation cancelled by user")

                    try:
                        logger.info(f"Fetching {exchange} instruments...")
                        self.progress_update.emit(f"Fetching {exchange} instruments...")

                        instruments = self.kite.instruments(exchange)

                        if instruments:
                            logger.info(f"Fetched {len(instruments)} instruments from {exchange}")
                            # Add exchange info to each instrument if not present
                            for inst in instruments:
                                if 'exchange' not in inst:
                                    inst['exchange'] = exchange
                            all_instruments.extend(instruments)
                        else:
                            logger.warning(f"No instruments returned from {exchange}")

                    except Exception as exchange_error:
                        logger.error(f"Failed to fetch {exchange} instruments: {exchange_error}")
                        # Continue with next exchange instead of failing completely

                if all_instruments:
                    logger.info(f"Successfully fetched {len(all_instruments)} total instruments")
                    self.save_instruments_to_cache(all_instruments)
                    return all_instruments
                else:
                    raise Exception("No instruments fetched from any exchange")

            except Exception as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)  # Exponential backoff
                    logger.warning(f"Fetch attempt {attempt + 1} failed: {e}. Retrying in {delay}s...")
                    self.progress_update.emit(f"Attempt failed. Retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    logger.error(f"All {max_retries} attempts failed. Last error: {e}")
                    raise

        return []

    @staticmethod
    def _build_instrument_map_with_nse_preference(instruments: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Build instrument map prioritizing NSE over BSE for same symbols."""
        instrument_map: Dict[str, Dict[str, Any]] = {}

        def exchange_priority(inst: Dict[str, Any]) -> int:
            exchange = inst.get("exchange", "")
            if exchange == "NSE":
                return 0
            if exchange == "BSE":
                return 1
            return 2

        sorted_instruments = sorted(instruments, key=exchange_priority)
        for inst in sorted_instruments:
            symbol = inst.get("tradingsymbol")
            if symbol and symbol not in instrument_map:
                instrument_map[symbol] = inst
        return instrument_map

    @staticmethod
    def _build_token_to_symbol(instrument_map: Dict[str, Dict[str, Any]]) -> Dict[int, str]:
        return {
            int(inst.get("instrument_token")): symbol
            for symbol, inst in instrument_map.items()
            if inst.get("instrument_token") is not None
        }

    def run(self):
        """Load instruments with caching and robust error handling"""
        try:
            # Check if we have valid cached instruments first
            if self.is_cache_valid():
                cached_instruments = self.load_cached_instruments()
                if cached_instruments:
                    self.progress_update.emit("Using cached instruments")
                    instrument_map = self._build_instrument_map_with_nse_preference(cached_instruments)
                    token_to_symbol = self._build_token_to_symbol(instrument_map)
                    symbol_index = SymbolIndex()
                    symbol_index.build(cached_instruments)
                    self.instruments_loaded.emit({
                        "instruments": cached_instruments,
                        "instrument_map": instrument_map,
                        "token_to_symbol": token_to_symbol,
                        "symbol_index": symbol_index,
                    })
                    return

            # If no valid cache, fetch from API
            self.progress_update.emit("Fetching fresh instruments from API...")
            instruments = self.fetch_instruments_with_fallback()

            if not self._stop_requested:
                self.progress_update.emit(f"Loaded {len(instruments)} instruments successfully")
                instrument_map = self._build_instrument_map_with_nse_preference(instruments)
                token_to_symbol = self._build_token_to_symbol(instrument_map)
                symbol_index = SymbolIndex()
                symbol_index.build(instruments)
                self.instruments_loaded.emit({
                    "instruments": instruments,
                    "instrument_map": instrument_map,
                    "token_to_symbol": token_to_symbol,
                    "symbol_index": symbol_index,
                })

        except Exception as e:
            if not self._stop_requested:
                error_msg = str(e)
                logger.error(f"InstrumentLoader failed: {error_msg}")

                # Try to fall back to cached instruments even if expired
                if "cancelled" not in error_msg.lower():
                    logger.info("Attempting to use expired cache as fallback...")
                    cached_instruments = self.load_cached_instruments()
                    if cached_instruments:
                        logger.warning("Using expired cached instruments as fallback")
                        self.progress_update.emit("Using cached instruments (fallback)")
                        instrument_map = self._build_instrument_map_with_nse_preference(cached_instruments)
                        token_to_symbol = self._build_token_to_symbol(instrument_map)
                        symbol_index = SymbolIndex()
                        symbol_index.build(cached_instruments)
                        self.instruments_loaded.emit({
                            "instruments": cached_instruments,
                            "instrument_map": instrument_map,
                            "token_to_symbol": token_to_symbol,
                            "symbol_index": symbol_index,
                        })
                        return

                self.error_occurred.emit(error_msg)

    def clear_cache(self):
        """Clear the instrument cache"""
        try:
            if os.path.exists(self.cache_file):
                os.remove(self.cache_file)
            if os.path.exists(self.cache_info_file):
                os.remove(self.cache_info_file)
            logger.info("Instrument cache cleared")
        except Exception as e:
            logger.error(f"Error clearing cache: {e}")
