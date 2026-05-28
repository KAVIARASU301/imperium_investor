#!/usr/bin/env python3
"""Probe IBKR live/delayed streaming market data outside the GUI.

This script is intentionally independent of the Qt application so it can tell
whether missing ticks are caused by TWS/Gateway permissions/settings or by the
app's MarketDataWorker subscription path.

Examples:
    python tools/ibkr_market_data_probe.py --symbols NVDA AAPL --port 7496
    python tools/ibkr_market_data_probe.py --symbols NVDA --types 1,3 --timeout 20
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from ib_insync import IB, Stock, Ticker, util

MARKET_DATA_TYPES = {
    1: "live",
    2: "frozen",
    3: "delayed",
    4: "delayed-frozen",
}
SUBSCRIPTION_ERRORS = {354, 10186}
DELAYED_NOTICES = {10167, 10168}


@dataclass
class ProbeError:
    req_id: int
    code: int
    message: str
    symbol: str = ""


@dataclass
class ProbeResult:
    market_data_type: int
    ticks_by_symbol: Dict[str, int] = field(default_factory=dict)
    last_prices: Dict[str, float] = field(default_factory=dict)
    errors: List[ProbeError] = field(default_factory=list)


def _positive_number(*values: Any) -> float:
    for value in values:
        try:
            number = float(value)
        except Exception:
            continue
        if math.isfinite(number) and number > 0:
            return number
    return 0.0


def _ticker_symbol(ticker: Ticker) -> str:
    contract = getattr(ticker, "contract", None)
    return (getattr(contract, "symbol", "") or "").upper()


def _ticker_price(ticker: Ticker) -> float:
    market_price = ticker.marketPrice() if hasattr(ticker, "marketPrice") else 0.0
    return _positive_number(
        market_price,
        getattr(ticker, "last", 0.0),
        getattr(ticker, "close", 0.0),
        getattr(ticker, "bid", 0.0),
        getattr(ticker, "ask", 0.0),
    )


def _parse_types(raw: str) -> List[int]:
    aliases = {name: value for value, name in MARKET_DATA_TYPES.items()}
    aliases.update({"realtime": 1, "real-time": 1, "delay": 3})
    values: List[int] = []
    for chunk in raw.split(","):
        text = chunk.strip().lower()
        if not text:
            continue
        value = aliases.get(text)
        if value is None:
            value = int(text)
        if value not in MARKET_DATA_TYPES:
            raise argparse.ArgumentTypeError(f"unsupported market data type: {chunk}")
        values.append(value)
    return values or [1, 3]


def _format_contract(contract: Any) -> str:
    return (
        f"symbol={getattr(contract, 'symbol', '')} conId={getattr(contract, 'conId', '')} "
        f"exchange={getattr(contract, 'exchange', '')} primary={getattr(contract, 'primaryExchange', '')} "
        f"currency={getattr(contract, 'currency', '')}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Debug IBKR live/delayed streaming market data permissions.")
    parser.add_argument("--host", default="127.0.0.1", help="TWS/Gateway API host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7496, help="TWS/Gateway API port (7496 live, 7497 paper by default)")
    parser.add_argument("--client-id", type=int, default=9911, help="API client id for this probe")
    parser.add_argument("--symbols", nargs="+", default=["NVDA"], help="Stock symbols to test")
    parser.add_argument("--exchange", default="SMART", help="Contract exchange (default: SMART)")
    parser.add_argument("--currency", default="USD", help="Contract currency (default: USD)")
    parser.add_argument("--primary-exchange", default="", help="Optional primary exchange, e.g. NASDAQ or NYSE")
    parser.add_argument("--types", type=_parse_types, default=[1, 3], help="Comma-separated market data types: 1/live, 2/frozen, 3/delayed, 4/delayed-frozen")
    parser.add_argument("--timeout", type=float, default=15.0, help="Seconds to listen per market data type")
    parser.add_argument("--readonly", action="store_true", help="Connect with readonly=True")
    parser.add_argument("--log-api", action="store_true", help="Enable ib_insync API logging to stderr")
    return parser


def qualify_contracts(ib: IB, symbols: Iterable[str], exchange: str, currency: str, primary_exchange: str) -> List[Any]:
    contracts = []
    for symbol in symbols:
        contract = Stock(symbol.upper(), exchange, currency)
        if primary_exchange:
            contract.primaryExchange = primary_exchange
        contracts.append(contract)

    qualified = ib.qualifyContracts(*contracts)
    print("\nQualified contracts:")
    if not qualified:
        print("  ! No contracts qualified. Check symbol/exchange/currency.")
    for contract in qualified:
        print(f"  - {_format_contract(contract)}")
    return list(qualified)


def run_stream_probe(ib: IB, contracts: List[Any], market_data_type: int, timeout: float) -> ProbeResult:
    result = ProbeResult(market_data_type=market_data_type)
    tickers: List[Ticker] = []
    req_id_to_symbol: Dict[int, str] = {}

    def on_error(req_id: int, code: int, message: str, contract: Any = None) -> None:
        symbol = (getattr(contract, "symbol", "") or req_id_to_symbol.get(req_id, "")).upper()
        result.errors.append(ProbeError(req_id=req_id, code=code, message=message, symbol=symbol))
        print(f"  ERROR type={market_data_type} symbol={symbol or '?'} reqId={req_id} code={code}: {message}")

    def on_pending(pending_tickers: List[Ticker]) -> None:
        for ticker in pending_tickers:
            symbol = _ticker_symbol(ticker)
            if not symbol:
                continue
            price = _ticker_price(ticker)
            result.ticks_by_symbol[symbol] = result.ticks_by_symbol.get(symbol, 0) + 1
            if price > 0:
                result.last_prices[symbol] = price
                print(f"  TICK type={market_data_type} symbol={symbol} price={price:g}")

    print(f"\nRequesting {MARKET_DATA_TYPES[market_data_type]} market data type ({market_data_type}) for {timeout:g}s...")
    ib.errorEvent += on_error
    ib.pendingTickersEvent += on_pending
    try:
        ib.reqMarketDataType(market_data_type)
        for contract in contracts:
            ticker = ib.reqMktData(contract, "", False, False)
            tickers.append(ticker)
            req_id = getattr(ticker, "tickerId", None) or getattr(ticker, "reqId", None)
            if req_id is not None:
                req_id_to_symbol[int(req_id)] = getattr(contract, "symbol", "").upper()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ib.waitOnUpdate(timeout=0.25)
            for ticker in tickers:
                symbol = _ticker_symbol(ticker)
                price = _ticker_price(ticker)
                if symbol and price > 0:
                    result.last_prices[symbol] = price
    finally:
        for ticker in tickers:
            contract = getattr(ticker, "contract", None)
            if contract is not None:
                ib.cancelMktData(contract)
        ib.errorEvent -= on_error
        ib.pendingTickersEvent -= on_pending
    return result


def print_diagnosis(results: List[ProbeResult], symbols: Iterable[str]) -> int:
    print("\nDiagnosis:")
    exit_code = 0
    any_prices = False
    for result in results:
        type_name = MARKET_DATA_TYPES[result.market_data_type]
        prices = ", ".join(f"{sym}={price:g}" for sym, price in sorted(result.last_prices.items())) or "none"
        ticks = sum(result.ticks_by_symbol.values())
        print(f"  - {type_name} ({result.market_data_type}): ticks={ticks}, prices={prices}")
        if result.last_prices:
            any_prices = True
        subscription_errors = [err for err in result.errors if err.code in SUBSCRIPTION_ERRORS]
        delayed_notices = [err for err in result.errors if err.code in DELAYED_NOTICES]
        if subscription_errors:
            print("    subscription errors:")
            for err in subscription_errors:
                print(f"      * {err.symbol or 'unknown'} code={err.code}: {err.message}")
        if delayed_notices:
            print("    delayed-data notices:")
            for err in delayed_notices:
                print(f"      * {err.symbol or 'unknown'} code={err.code}: {err.message}")

    delayed_result = next((result for result in results if result.market_data_type == 3), None)
    live_result = next((result for result in results if result.market_data_type == 1), None)

    if any_prices:
        if live_result and not live_result.last_prices and delayed_result and delayed_result.last_prices:
            print("\nConclusion: live subscriptions are likely missing, but delayed streaming works. Set IBKR_MARKET_DATA_TYPE=delayed or keep the app's delayed fallback enabled.")
        else:
            print("\nConclusion: IBKR streaming data works from this machine/session. If the GUI still does not update, inspect MarketDataWorker subscription inputs and emitted ticks.")
        return exit_code

    exit_code = 2
    if delayed_result and any(err.code in SUBSCRIPTION_ERRORS for err in delayed_result.errors):
        print("\nConclusion: neither live nor delayed streaming is available for this session. In TWS/Gateway, verify API settings and that delayed market data is enabled for the user/account.")
    elif live_result and any(err.code in SUBSCRIPTION_ERRORS for err in live_result.errors):
        print("\nConclusion: live subscription permissions are missing, and delayed data did not produce ticks during the probe window. Try --types 3 --timeout 30 and verify delayed data settings in TWS/Gateway.")
    else:
        symbol_text = ", ".join(symbols)
        print(f"\nConclusion: no streaming ticks were received for {symbol_text}. If the market is closed, try --types 2,4 or test a more liquid symbol during regular market hours.")
    return exit_code


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.log_api:
        util.logToConsole()

    ib = IB()
    print(f"Connecting to IBKR API at {args.host}:{args.port} clientId={args.client_id} readonly={args.readonly}...")
    ib.connect(args.host, args.port, clientId=args.client_id, readonly=args.readonly, timeout=10)
    try:
        print(f"Connected: serverVersion={ib.client.serverVersion()} accounts={ib.managedAccounts()}")
        contracts = qualify_contracts(ib, args.symbols, args.exchange, args.currency, args.primary_exchange)
        if not contracts:
            return 1
        results = [run_stream_probe(ib, contracts, data_type, args.timeout) for data_type in args.types]
        return print_diagnosis(results, args.symbols)
    finally:
        ib.disconnect()
        print("Disconnected.")


if __name__ == "__main__":
    sys.exit(main())
