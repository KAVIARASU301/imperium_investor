"""Instrument loader for fetching trading instruments from Zerodha"""

import logging
from PySide6.QtCore import QThread, Signal
from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)


class InstrumentLoader(QThread):
    """Background thread for loading instruments from Zerodha"""

    instruments_loaded = Signal(dict)
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

            # Process instruments
            symbol_data = {}

            for inst in instruments:
                if inst['instrument_type'] == 'EQ':
                    symbol_name = inst['tradingsymbol']

                    if symbol_name not in symbol_data:
                        symbol_data[symbol_name] = {
                            'instrument_token': inst['instrument_token'],
                            'exchange': inst['exchange'],
                            'tick_size': inst['tick_size'],
                            'lot_size': inst['lot_size'],
                            'instrument_type': inst['instrument_type'],
                            'name': inst['name']
                        }

            self.instruments_loaded.emit(symbol_data)

        except Exception as e:
            logger.error(f"Failed to load instruments: {e}")
            self.error_occurred.emit(str(e))
