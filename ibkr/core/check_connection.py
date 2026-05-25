"""Run this directly to verify TWS connectivity before launching the app."""

from ib_insync import IB, Stock


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

                    # Test a quote
                    contract = Stock("AAPL", "SMART", "USD")
                    ib.qualifyContracts(contract)
                    bars = ib.reqHistoricalData(
                        contract,
                        endDateTime="",
                        durationStr="5 D",
                        barSizeSetting="1 day",
                        whatToShow="TRADES",
                        useRTH=True,
                    )
                    print(f"   AAPL bars: {len(bars)}")
                    ib.disconnect()
                    return host, port
            except Exception as e:
                print(f"   ❌ {host}:{port} — {e}")
    print("No connection found. Ensure TWS/Gateway is open with API enabled.")
    return None, None


if __name__ == "__main__":
    check()
