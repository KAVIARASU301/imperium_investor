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

- Live has errors such as `354`, `10089`, or `10186`, but delayed has prices: the account/session probably lacks live market-data subscriptions for that exchange, but delayed data works.
- Both live and delayed produce subscription errors: check TWS/Gateway market-data permissions and delayed-data settings for the logged-in user/account.
- The probe receives streaming ticks but the GUI does not: inspect the app's `MarketDataWorker` logs for subscription payloads, the `generic ticks` value, market data type switches, and emitted ticks. The app should default to the same empty generic tick list used by the probe; if `IBKR_GENERIC_TICKS` is set, clear it and retry before changing anything else.
- No ticks and no errors during closed markets can be normal for streaming type `1`/`3`; retry with `--types 2,4` or during regular market hours.

## App fallback controls

The IBKR `MarketDataWorker` now logs the requested market data type and listens for subscription-related IBKR errors. By default it starts in live mode using the same plain top-of-book request shape as the probe. If optional generic ticks were explicitly configured and IBKR rejects the stream, the worker first disables those optional ticks and resubscribes; only then does it retry existing subscriptions with delayed mode after a market-data subscription error.

Optional environment variables:

```bash
# Start the app in delayed streaming mode immediately.
IBKR_MARKET_DATA_TYPE=delayed python main.py

# Disable automatic live -> delayed fallback.
IBKR_MARKET_DATA_FALLBACK_DELAYED=0 python main.py

# Optional: request extra generic tick streams such as RTVolume (233).
# Leave this unset while debugging GUI-vs-probe streaming mismatches.
IBKR_GENERIC_TICKS=233 python main.py
```

Accepted `IBKR_MARKET_DATA_TYPE` values are `1`, `2`, `3`, `4`, `live`, `frozen`, `delayed`, and `delayed-frozen`.

## Step-by-step GUI streaming checklist

1. Confirm the exact same symbol streams in the probe, for example `python tools/ibkr_market_data_probe.py --symbols NVDA --types 1,3 --timeout 20`.
2. Start the GUI with `IBKR_GENERIC_TICKS` unset so the app requests the same plain stream as the probe.
3. In the app logs, verify `IBKR MarketDataWorker started`, `Requested IBKR ... generic ticks=''`, and `Subscribed IBKR market data` for the chart/watchlist symbols.
4. If the worker logs a subscription error after an explicit `IBKR_GENERIC_TICKS` override, confirm it logs `Disabling IBKR optional generic ticks ... and resubscribing market data`.
5. If live data is not entitled, verify the subsequent delayed fallback log and that the status bar changes to delayed.
6. If logs show subscriptions and incoming emitted ticks but widgets still do not update, inspect the downstream `_enqueue_market_data` / `_on_market_data` path in `ibkr/core/main_window.py` and the widget token/symbol maps.
