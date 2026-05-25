"""Run this directly to verify TWS connectivity before launching the app."""

from ib_insync import IB, Stock


def print_ohlc_bars(symbol: str, bars) -> None:
    """Print the OHLC candle data received from IBKR/TWS."""
    if not bars:
        print(f"   ⚠️ No historical candle data received for {symbol}.")
        return

    print(f"\n   📊 {symbol} candle data received from IBKR/TWS")
    print("   " + "-" * 86)
    print(
        "   "
        f"{'Date/Time':<22} "
        f"{'Open':>10} "
        f"{'High':>10} "
        f"{'Low':>10} "
        f"{'Close':>10} "
        f"{'Volume':>12}"
    )
    print("   " + "-" * 86)

    for bar in bars:
        print(
            "   "
            f"{str(bar.date):<22} "
            f"{bar.open:>10.2f} "
            f"{bar.high:>10.2f} "
            f"{bar.low:>10.2f} "
            f"{bar.close:>10.2f} "
            f"{bar.volume:>12}"
        )

    print("   " + "-" * 86)
    print(f"   Total candles received: {len(bars)}\n")


def check():
    ib = IB()
    hosts = ["127.0.0.1", "::1"]
    ports = [7497, 7496, 4002, 4001]  # 7497=TWS paper, 7496=TWS live, 4002=Gateway paper

    for host in hosts:
        for port in ports:
            try:
                ib.connect(host=host, port=port, clientId=99, timeout=5)
                if ib.isConnected():
                    print(f"✅ Connected: {host}:{port}")
                    t = ib.reqCurrentTime()
                    print(f"   Server time: {t}")

                    # Test candle data from IBKR/TWS
                    symbol = "KLAC"
                    contract = Stock(symbol, "SMART", "USD")
                    ib.qualifyContracts(contract)

                    bars = ib.reqHistoricalData(
                        contract,
                        endDateTime="",
                        durationStr="10 D",
                        barSizeSetting="1 day",
                        whatToShow="TRADES",
                        useRTH=True,
                    )

                    print(f"   {symbol} bars received: {len(bars)}")
                    print_ohlc_bars(symbol, bars)

                    ib.disconnect()
                    return host, port
            except Exception as e:
                print(f"   ❌ {host}:{port} — {e}")
                if ib.isConnected():
                    ib.disconnect()

    print("No connection found. Ensure TWS/Gateway is open with API enabled.")
    return None, None


if __name__ == "__main__":
    check()