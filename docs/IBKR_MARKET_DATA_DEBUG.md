# IBKR market data debugging

Use `tools/ibkr_market_data_probe.py` when historical IBKR requests work but live/delayed streaming ticks do not reach the app.

The probe connects directly to TWS/Gateway with `ib_insync`, qualifies the contract, requests streaming market data type `1` (live) and `3` (delayed), prints every tick/error callback, and then prints a diagnosis. This separates account/TWS subscription problems from GUI subscription bugs.

## Quick checks

```bash
python tools/ibkr_market_data_probe.py --symbols NVDA --port 7496 --timeout 20
```

Common ports:

- `7496`: TWS live trading session.
- `7497`: TWS paper trading session.
- Gateway ports depend on your local Gateway settings.

Force delayed-only testing:

```bash
python tools/ibkr_market_data_probe.py --symbols NVDA AAPL --types 3 --timeout 30
```

If a SMART stock is ambiguous, include the primary exchange:

```bash
python tools/ibkr_market_data_probe.py --symbols NVDA --primary-exchange NASDAQ
```

## Interpreting results

- Live has errors such as `354` or `10186`, but delayed has prices: the account/session probably lacks live market-data subscriptions for that exchange, but delayed data works.
- Both live and delayed produce subscription errors: check TWS/Gateway market-data permissions and delayed-data settings for the logged-in user/account.
- The probe receives streaming ticks but the GUI does not: inspect the app's `MarketDataWorker` logs for subscription payloads, market data type switches, and emitted ticks.
- No ticks and no errors during closed markets can be normal for streaming type `1`/`3`; retry with `--types 2,4` or during regular market hours.

## App fallback controls

The IBKR `MarketDataWorker` now logs the requested market data type and listens for subscription-related IBKR errors. By default it starts in live mode and automatically retries existing subscriptions with delayed mode after the first market-data subscription error.

Optional environment variables:

```bash
# Start the app in delayed streaming mode immediately.
IBKR_MARKET_DATA_TYPE=delayed python main.py

# Disable automatic live -> delayed fallback.
IBKR_MARKET_DATA_FALLBACK_DELAYED=0 python main.py
```

Accepted `IBKR_MARKET_DATA_TYPE` values are `1`, `2`, `3`, `4`, `live`, `frozen`, `delayed`, and `delayed-frozen`.
