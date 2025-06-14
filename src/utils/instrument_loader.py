# src/utils/instrument_loader.py

"""Instrument loader for fetching trading instruments from Zerodha"""

import logging
from PySide6.QtCore import QThread, Signal
from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)


class InstrumentLoader(QThread):
    """Background thread for loading instruments from Zerodha"""

    instruments_loaded = Signal(list)  # Changed to emit a list
    error_occurred = Signal(str)

    def __init__(self, kite_client: KiteConnect):
        super().__init__()
        self.kite = kite_client

    def run(self):
        """Load instruments in background"""
        try:
            # Fetch instruments from both NSE and BSE
            nse_instruments = self.kite.instruments("NSE")
            bse_instruments = self.kite.instruments("BSE")
            
            instruments = nse_instruments + bse_instruments

            # FIX: Emit the raw list of instruments directly
            self.instruments_loaded.emit(instruments)
            logger.info(f"Successfully loaded {len(instruments)} instruments.")

        except Exception as e:
            logger.error(f"Failed to load instruments: {e}")
            self.error_occurred.emit(str(e))