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
            instruments = self.kite.instruments("NFO")

            # Process instruments
            symbol_data = {}

            for inst in instruments:
                if inst['instrument_type'] in ['CE', 'PE']:
                    symbol_name = inst['name']

                    if symbol_name not in symbol_data:
                        symbol_data[symbol_name] = {
                            'lot_size': inst['lot_size'],
                            'tick_size': inst['tick_size'],
                            'expiries': set(),
                            'strikes': set(),
                            'instruments': []
                        }

                    symbol_data[symbol_name]['expiries'].add(inst['expiry'])
                    symbol_data[symbol_name]['strikes'].add(inst['strike'])
                    symbol_data[symbol_name]['instruments'].append(inst)

            # Convert sets to sorted lists
            for symbol in symbol_data:
                symbol_data[symbol]['expiries'] = sorted(list(symbol_data[symbol]['expiries']))
                symbol_data[symbol]['strikes'] = sorted(list(symbol_data[symbol]['strikes']))

            self.instruments_loaded.emit(symbol_data)

        except Exception as e:
            logger.error(f"Failed to load instruments: {e}")
            self.error_occurred.emit(str(e))
