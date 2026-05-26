"""Normalize ib_insync models into terminal dictionaries."""

from __future__ import annotations

from typing import Any, Dict


def normalize_position(position: Any) -> Dict[str, Any]:
    contract = getattr(position, "contract", None)
    return {
        "tradingsymbol": getattr(contract, "symbol", ""),
        "exchange": getattr(contract, "exchange", "SMART"),
        "quantity": float(getattr(position, "position", 0) or 0),
        "average_price": float(getattr(position, "avgCost", 0) or 0),
        "pnl": float(getattr(position, "unrealizedPNL", 0) or 0),
        "product": "IBKR",
    }


def normalize_ticker(ticker: Any) -> Dict[str, Any]:
    contract = getattr(ticker, "contract", None)
    return {
        "symbol": getattr(contract, "symbol", ""),
        "exchange": getattr(contract, "exchange", "SMART"),
        "bid": getattr(ticker, "bid", None),
        "ask": getattr(ticker, "ask", None),
        "last": getattr(ticker, "last", None),
        "close": getattr(ticker, "close", None),
    }
