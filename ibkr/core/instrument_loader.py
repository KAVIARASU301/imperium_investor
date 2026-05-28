# ibkr/core/instrument_loader.py
"""Fast IBKR instrument seed loader.

IBKR does not provide a Kite-style daily dump of all instruments.  Loading a
large universe by calling reqMatchingSymbols/reqContractDetails repeatedly will
make startup feel slow and can hit pacing limits.  This loader therefore:

  1. Loads a small cached/seed US-equity universe immediately.
  2. Lets the live symbol resolver enrich exact symbols on demand.
  3. Emits the same payload shape the existing main window expects.
"""

from __future__ import annotations

import logging
import os
import pickle
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QThread, Signal
from ibkr.core.symbol_info_db import SymbolInfoDatabase

try:
    from ibkr.widgets.search_bar import SymbolIndex
except Exception:  # pragma: no cover - app package may not be importable in tests
    class SymbolIndex:  # type: ignore
        def __init__(self):
            self.items = []
        def build(self, instruments):
            self.items = list(instruments or [])

logger = logging.getLogger(__name__)

_SEED_SYMBOLS = [
    ("AAPL", "Apple Inc.", "NASDAQ"),
    ("MSFT", "Microsoft Corporation", "NASDAQ"),
    ("NVDA", "NVIDIA Corporation", "NASDAQ"),
    ("AMZN", "Amazon.com Inc.", "NASDAQ"),
    ("META", "Meta Platforms Inc.", "NASDAQ"),
    ("GOOGL", "Alphabet Inc. Class A", "NASDAQ"),
    ("GOOG", "Alphabet Inc. Class C", "NASDAQ"),
    ("TSLA", "Tesla Inc.", "NASDAQ"),
    ("AMD", "Advanced Micro Devices Inc.", "NASDAQ"),
    ("NFLX", "Netflix Inc.", "NASDAQ"),
    ("AVGO", "Broadcom Inc.", "NASDAQ"),
    ("INTC", "Intel Corporation", "NASDAQ"),
    ("QCOM", "Qualcomm Inc.", "NASDAQ"),
    ("ADBE", "Adobe Inc.", "NASDAQ"),
    ("CRM", "Salesforce Inc.", "NYSE"),
    ("ORCL", "Oracle Corporation", "NYSE"),
    ("JPM", "JPMorgan Chase & Co.", "NYSE"),
    ("BAC", "Bank of America Corporation", "NYSE"),
    ("WFC", "Wells Fargo & Company", "NYSE"),
    ("GS", "Goldman Sachs Group Inc.", "NYSE"),
    ("V", "Visa Inc.", "NYSE"),
    ("MA", "Mastercard Incorporated", "NYSE"),
    ("UNH", "UnitedHealth Group Incorporated", "NYSE"),
    ("LLY", "Eli Lilly and Company", "NYSE"),
    ("JNJ", "Johnson & Johnson", "NYSE"),
    ("ABBV", "AbbVie Inc.", "NYSE"),
    ("MRK", "Merck & Co. Inc.", "NYSE"),
    ("XOM", "Exxon Mobil Corporation", "NYSE"),
    ("CVX", "Chevron Corporation", "NYSE"),
    ("WMT", "Walmart Inc.", "NYSE"),
    ("COST", "Costco Wholesale Corporation", "NASDAQ"),
    ("HD", "Home Depot Inc.", "NYSE"),
    ("MCD", "McDonald's Corporation", "NYSE"),
    ("NKE", "Nike Inc.", "NYSE"),
    ("DIS", "Walt Disney Company", "NYSE"),
    ("SPY", "SPDR S&P 500 ETF Trust", "ARCA"),
    ("QQQ", "Invesco QQQ Trust", "NASDAQ"),
    ("IWM", "iShares Russell 2000 ETF", "ARCA"),
    ("DIA", "SPDR Dow Jones Industrial Average ETF", "ARCA"),
]


def _seed_instruments() -> List[Dict[str, Any]]:
    return [
        {
            "tradingsymbol": symbol,
            "symbol": symbol,
            "name": name,
            "exchange": exchange,
            "primaryExchange": exchange,
            "instrument_token": 0,
            "conId": 0,
            "currency": "USD",
            "secType": "STK" if symbol not in {"SPY", "QQQ", "IWM", "DIA"} else "ETF",
        }
        for symbol, name, exchange in _SEED_SYMBOLS
    ]


class IBKRInstrumentLoader(QThread):
    instruments_loaded = Signal(dict)
    error_occurred = Signal(str)
    progress_update = Signal(str)

    def __init__(self, ib_client: Any, cache_dir: Optional[str] = None, cache_ttl_hours: int = 24):
        super().__init__()
        self.ib = ib_client
        self.cache_dir = cache_dir or os.path.expanduser("~/.qullamaggie/cache")
        self.cache_file = os.path.join(self.cache_dir, "ibkr_instruments_cache.pkl")
        self.cache_info_file = os.path.join(self.cache_dir, "ibkr_instruments_cache_info.pkl")
        self.cache_ttl = timedelta(hours=max(1, int(cache_ttl_hours)))
        self._stop_requested = False
        os.makedirs(self.cache_dir, exist_ok=True)

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        try:
            self.progress_update.emit("Loading IBKR symbol cache…")
            instruments = self._load_valid_cache()
            if not instruments:
                instruments = _seed_instruments()
                self._save_cache(instruments)
                self.progress_update.emit("Loaded fast US equity seed list")
            else:
                self.progress_update.emit(f"Loaded {len(instruments)} cached IBKR instruments")

            if self._stop_requested:
                return

            merged = self._merge_with_symbol_info_db(instruments)
            self.instruments_loaded.emit(self._build_payload(merged))
        except Exception as exc:
            logger.error("IBKRInstrumentLoader failed: %s", exc, exc_info=True)
            self.error_occurred.emit(str(exc))
            # Last-resort seed means the UI/search bar still opens instantly.
            self.instruments_loaded.emit(self._build_payload(_seed_instruments()))

    def _merge_with_symbol_info_db(self, instruments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        by_symbol = {
            str(i.get("tradingsymbol") or i.get("symbol") or "").strip().upper(): dict(i)
            for i in (instruments or [])
            if str(i.get("tradingsymbol") or i.get("symbol") or "").strip()
        }
        try:
            rows = SymbolInfoDatabase().list_for_search_index()
        except Exception as exc:
            logger.debug("Unable to load symbol_info rows for index: %s", exc)
            rows = []

        for row in rows:
            symbol = str(row.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            existing = by_symbol.get(symbol, {})
            company_name = str(row.get("company_name") or "").strip()
            merged = {
                **existing,
                "tradingsymbol": symbol,
                "symbol": symbol,
                "name": company_name or existing.get("name") or symbol,
                "exchange": existing.get("exchange") or existing.get("primaryExchange") or "SMART",
                "primaryExchange": existing.get("primaryExchange") or existing.get("exchange") or "SMART",
                "instrument_token": existing.get("instrument_token") or existing.get("conId") or existing.get("conid") or 0,
                "conId": existing.get("conId") or existing.get("conid") or existing.get("instrument_token") or 0,
                "currency": existing.get("currency") or "USD",
                "secType": existing.get("secType") or "STK",
                "market_cap_text": row.get("market_cap_text"),
                "market_cap_value": row.get("market_cap_value"),
            }
            by_symbol[symbol] = merged
        return list(by_symbol.values())

    def merge_and_cache(self, instruments: List[Dict[str, Any]]) -> None:
        """Optional helper used by live search code to persist resolved conIds."""
        current = self._load_cache_any_age() or _seed_instruments()
        by_symbol = {str(i.get("tradingsymbol") or i.get("symbol") or "").upper(): dict(i) for i in current}
        for inst in instruments or []:
            sym = str(inst.get("tradingsymbol") or inst.get("symbol") or "").upper()
            if sym:
                by_symbol[sym] = {**by_symbol.get(sym, {}), **inst, "tradingsymbol": sym, "symbol": sym}
        self._save_cache(list(by_symbol.values()))

    def _load_valid_cache(self) -> Optional[List[Dict[str, Any]]]:
        try:
            if not os.path.exists(self.cache_file) or not os.path.exists(self.cache_info_file):
                return None
            with open(self.cache_info_file, "rb") as fh:
                info = pickle.load(fh)
            timestamp = info.get("timestamp")
            if not timestamp or datetime.now() - timestamp > self.cache_ttl:
                return None
            return self._load_cache_any_age()
        except Exception as exc:
            logger.warning("IBKR instrument cache validation failed: %s", exc)
            return None

    def _load_cache_any_age(self) -> Optional[List[Dict[str, Any]]]:
        try:
            with open(self.cache_file, "rb") as fh:
                data = pickle.load(fh)
            if isinstance(data, list):
                return data
        except Exception as exc:
            logger.debug("No IBKR instrument cache available: %s", exc)
        return None

    def _save_cache(self, instruments: List[Dict[str, Any]]) -> None:
        try:
            with open(self.cache_file, "wb") as fh:
                pickle.dump(instruments, fh, protocol=pickle.HIGHEST_PROTOCOL)
            with open(self.cache_info_file, "wb") as fh:
                pickle.dump({"timestamp": datetime.now(), "count": len(instruments)}, fh, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as exc:
            logger.warning("Failed to save IBKR instrument cache: %s", exc)

    @staticmethod
    def _build_payload(instruments: List[Dict[str, Any]]) -> Dict[str, Any]:
        instrument_map: Dict[str, Dict[str, Any]] = {}
        token_to_symbol: Dict[int, str] = {}
        normalised: List[Dict[str, Any]] = []

        for raw in instruments or []:
            symbol = str(raw.get("tradingsymbol") or raw.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            con_id = raw.get("instrument_token") or raw.get("conId") or raw.get("conid") or 0
            try:
                con_id = int(con_id or 0)
            except Exception:
                con_id = 0
            item = {
                **raw,
                "tradingsymbol": symbol,
                "symbol": symbol,
                "instrument_token": con_id,
                "conId": con_id,
                "exchange": raw.get("exchange") or raw.get("primaryExchange") or "SMART",
                "currency": raw.get("currency") or "USD",
            }
            normalised.append(item)
            instrument_map[symbol] = item
            if con_id:
                token_to_symbol[con_id] = symbol

        symbol_index = SymbolIndex()
        try:
            symbol_index.build(normalised)
        except Exception:
            logger.debug("SymbolIndex build failed", exc_info=True)

        return {
            "instruments": normalised,
            "instrument_map": instrument_map,
            "token_to_symbol": token_to_symbol,
            "symbol_index": symbol_index,
        }


# Backward-compatible class name used by older imports.
InstrumentLoader = IBKRInstrumentLoader
