"""
IBKR instrument loader.

Builds a working instrument map from a curated seed list of popular
US stocks + ETFs. Does NOT require downloading a full universe —
IBKR doesn't provide one. Additional symbols are resolved on-demand
via IBKRSymbolResolver when the user types in the search bar.
"""

import logging
from typing import Any, Dict, List

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

# Popular US stocks and ETFs — covers most swing trading use cases.
# Add more as needed. conId=0 means "not yet qualified"; the loader
# will qualify these in batches to get the real conId values.
SEED_SYMBOLS = [
    # Major ETFs
    "SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT", "HYG", "XLF", "XLK",
    "XLE", "XLV", "XLI", "XLC", "XLY", "XLP", "XLU", "XLB", "XLRE",
    "ARKK", "ARKG", "ARKW", "ARKF", "ARKQ",
    "VXX", "UVXY", "SQQQ", "TQQQ", "SPXU", "UPRO",
    # Mega cap tech
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "NVDA", "AVGO",
    "AMD", "INTC", "QCOM", "TXN", "MU", "AMAT", "LRCX", "KLAC", "ASML",
    "ORCL", "CRM", "ADBE", "NOW", "SNOW", "PLTR", "UBER", "LYFT",
    "NFLX", "DIS", "CMCSA", "T", "VZ",
    # Finance
    "JPM", "BAC", "WFC", "C", "GS", "MS", "BLK", "AXP", "V", "MA", "PYPL",
    "SQ", "COIN", "HOOD",
    # Healthcare
    "JNJ", "UNH", "PFE", "ABBV", "MRK", "BMY", "AMGN", "GILD", "MRNA",
    "BNTX", "LLY", "CVS", "HCA",
    # Consumer
    "AMZN", "WMT", "TGT", "COST", "HD", "LOW", "NKE", "SBUX", "MCD",
    "YUM", "CMG", "LULU", "ROST", "TJX",
    # Energy
    "XOM", "CVX", "COP", "SLB", "HAL", "OXY", "DVN", "MPC", "PSX", "VLO",
    # Industrial / Aerospace
    "BA", "LMT", "RTX", "NOC", "GD", "CAT", "DE", "MMM", "GE", "HON",
    # Biotech
    "BIIB", "REGN", "VRTX", "ILMN", "IDXX", "ISRG",
    # Popular swing trading names
    "ROKU", "SHOP", "SQ", "TWLO", "DDOG", "CRWD", "ZS", "OKTA", "NET",
    "FSLY", "ESTC", "MDB", "DKNG", "PENN", "MGAM",
    "RIVN", "LCID", "NIO", "LI", "XPEV",
    "GME", "AMC", "BBBY",
    # Banks / Regional
    "USB", "PNC", "TFC", "COF", "DFS",
    # REITs
    "AMT", "PLD", "CCI", "EQIX", "O",
    # Commodities / Materials
    "FCX", "NEM", "GOLD", "AA", "CLF", "X", "NUE",
    # Communications
    "ATVI", "EA", "TTWO", "RBLX", "U",
    # Pharma
    "JAZZ", "ALNY", "BMRN", "IONS",
    # China ADRs
    "BABA", "JD", "PDD", "BIDU", "EDU", "TAL",
]


class IBKRInstrumentLoader(QThread):
    """
    Loads IBKR instruments from the seed list.

    Phase 1 (instant): Emit unqualified seed list so UI is usable immediately.
    Phase 2 (background): Qualify each symbol to get conId for market data subscriptions.
    """

    instruments_loaded = Signal(dict)  # Same payload shape as Kite's InstrumentLoader
    progress_update = Signal(str)

    def __init__(self, ib_client):
        super().__init__()
        self.ib_client = ib_client
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        # ── Phase 1: Emit seed list immediately (no API calls) ──────────
        self.progress_update.emit("Building initial instrument list...")

        seed_instruments = self._build_seed_instruments()
        instrument_map = {inst["tradingsymbol"]: inst for inst in seed_instruments}
        token_to_symbol = {}  # Will be populated in phase 2

        payload = {
            "instruments": seed_instruments,
            "instrument_map": instrument_map,
            "token_to_symbol": token_to_symbol,
            "symbol_index": None,
        }
        self.instruments_loaded.emit(payload)
        self.progress_update.emit(
            f"Loaded {len(seed_instruments)} symbols (qualifying in background...)"
        )

        # ── Phase 2: Qualify in batches to get conId ─────────────────────
        # This updates instrument_token for each symbol so market data works.
        self._qualify_in_background(instrument_map, token_to_symbol)

    def _build_seed_instruments(self) -> List[Dict[str, Any]]:
        """Build instrument list from seed without any API calls."""
        instruments = []
        for symbol in SEED_SYMBOLS:
            instruments.append(
                {
                    "tradingsymbol": symbol,
                    "name": symbol,  # Will be updated after qualification
                    "exchange": "SMART",
                    "instrument_token": 0,  # Will be set after qualification
                    "segment": "STK",
                    "currency": "USD",
                    "instrument_type": "EQ",
                    "lot_size": 1,
                }
            )
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for inst in instruments:
            sym = inst["tradingsymbol"]
            if sym not in seen:
                seen.add(sym)
                unique.append(inst)
        return unique

    def _qualify_in_background(
        self,
        instrument_map: Dict,
        token_to_symbol: Dict,
    ) -> None:
        """Qualify symbols in small batches; re-emit updated payload as tokens arrive."""
        from ib_insync import Stock

        batch_size = 20  # IBKR rate-limits; keep batches small
        symbols = list(instrument_map.keys())
        updated = False

        for i in range(0, len(symbols), batch_size):
            if self._stop_requested:
                break

            batch = symbols[i : i + batch_size]
            contracts = [Stock(sym, "SMART", "USD") for sym in batch]

            try:
                qualified = self.ib_client.qualifyContracts(*contracts)
                for contract in qualified:
                    sym = contract.symbol
                    con_id = contract.conId
                    if sym in instrument_map and con_id:
                        instrument_map[sym]["instrument_token"] = con_id
                        instrument_map[sym]["exchange"] = contract.exchange or "SMART"
                        token_to_symbol[con_id] = sym
                        updated = True
            except Exception as e:
                logger.warning("Batch qualification failed (%s...): %s", batch[0], e)

            # Re-emit after each batch so the UI updates progressively
            if updated:
                updated_payload = {
                    "instruments": list(instrument_map.values()),
                    "instrument_map": dict(instrument_map),
                    "token_to_symbol": dict(token_to_symbol),
                    "symbol_index": None,
                }
                self.instruments_loaded.emit(updated_payload)
                updated = False
                self.progress_update.emit(
                    f"Qualified {min(i + batch_size, len(symbols))}/{len(symbols)} symbols..."
                )

            # Small sleep between batches to avoid IBKR pacing limits
            self.msleep(200)

        self.progress_update.emit(
            f"Ready — {len(token_to_symbol)} symbols with live data tokens"
        )
        logger.info("IBKR instrument qualification complete: %d tokens", len(token_to_symbol))
