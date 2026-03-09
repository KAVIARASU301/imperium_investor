# Chart Engine Structure

This document explains the **chart engine folder structure**, what each part does, and how the chart lifecycle works end-to-end.

## Folder Structure

```text
chart_engine/
├── __init__.py                       # Public package entry (exports CandlestickChart)
├── core/
│   ├── __init__.py                   # Core exports
│   ├── chart_widget.py               # Main QWidget orchestrator (CandlestickChart)
│   ├── chart_bridge.py               # Python ⇄ JavaScript bridge via QWebChannel
│   ├── data_loader.py                # Historical data fetch, cache, loader thread
│   └── metrics.py                    # Indicator/metrics calculations for UI
├── drawings/
│   ├── __init__.py                   # Drawing storage implementation
│   └── drawing_storage.py            # Re-export for clean import path
├── renderer/
│   ├── __init__.py                   # Renderer exports
│   ├── html_builder.py               # Builds HTML payload + injects chart config/data
│   └── chart.js                      # Canvas chart engine + interaction logic
├── settings/
│   ├── __init__.py                   # Settings exports
│   ├── chart_settings_dialog.py      # Global chart appearance/settings dialog
│   └── text_note_dialog.py           # Text-note editor dialog
└── toolbar/
    ├── __init__.py                   # Toolbar exports
    └── chart_toolbar.py              # Top toolbar (timeframes, indicators, tools)
```

## What is What (Responsibility Map)

### 1) `core/` — Orchestration and data flow
- **`chart_widget.py` (`CandlestickChart`)** is the integration hub:
  - Builds and wires the toolbar, web view, bridge, storage, and loader.
  - Handles symbol loading, interval switching, refresh, and live updates.
  - Persists/restores symbol view state (drawings + zoom).
  - Emits app-level signals (symbol loaded, order request, alert request).
- **`chart_bridge.py` (`ChartBridge`)** is the bridge contract between JS and Python:
  - JS sends events like drawings changed, zoom changed, note requests.
  - Python emits Qt signals consumed by `CandlestickChart`.
  - Includes queueing until WebChannel is fully initialized.
- **`data_loader.py`** provides:
  - DataFetcher abstraction for market data retrieval.
  - In-memory cache (`DataCache`) to reduce duplicate calls.
  - Worker thread (`ChartDataLoaderThread`) to keep UI responsive.
- **`metrics.py`** computes chart-side metrics/derived values used in UI overlays or labels.

### 2) `renderer/` — Rendering stack
- **`html_builder.py`** composes the HTML shell and injects serialized data/config needed by JS.
- **`chart.js`** is the visual engine:
  - Draws candles, volume, EMA lines, VWAP, crosshair, watermark.
  - Supports pan/zoom, right-click actions, and drawing tools.
  - Sends interaction events back to Python through `ChartBridge`.

### 3) `toolbar/` — User controls
- **`chart_toolbar.py`** exposes timeframe selector, indicator toggles, drawing tools, style controls, autoscale/refresh/settings actions.
- This module emits UI intents; `CandlestickChart` converts those intents into bridge calls/data reloads.

### 4) `drawings/` — Persistence
- **`drawings/__init__.py`** holds the actual `DrawingStorage` implementation.
- `DrawingStorage` saves/loads:
  - Per-symbol + per-interval state (drawings + visible candles)
  - Global chart settings (candle colors, watermark, spacing, etc.)
  - Last viewed symbol metadata
- **`drawing_storage.py`** is a compatibility re-export.

### 5) `settings/` — Dialogs
- **`chart_settings_dialog.py`** controls global visual defaults.
- **`text_note_dialog.py`** edits note content used by drawing tools.

## How It Works (Lifecycle)

## 1. Chart creation
1. App instantiates `CandlestickChart(kite_client=..., instrument_loader=...)`.
2. Widget initializes storage, data services, toolbar, and `QWebEngineView`.
3. Bridge is registered on `QWebChannel` for JS communication.

## 2. Symbol load
1. `load_symbol(symbol, exchange, token, interval)` is triggered from app/watchlist.
2. Existing symbol state is saved (if switching symbols).
3. Loader thread fetches historical OHLCV data (or cache hit).
4. `metrics.py` calculations are prepared.

## 3. Render bootstrap
1. `html_builder.py` builds HTML with serialized candles/volume/indicators/settings.
2. HTML is loaded into `QWebEngineView`.
3. `chart.js` initializes canvas, layout, viewport, and event listeners.

## 4. Bridge handshake
1. JS calls `chartBridge.set_web_channel_initialized()`.
2. Python marks bridge ready and flushes queued messages.
3. JS interaction events now flow to Python signals in real time.

## 5. Runtime interactions
- Toolbar actions (timeframe, indicator visibility, tool selection) are forwarded to JS.
- JS emits drawing/zoom/context-menu events back to Python.
- Python handles order/alert/note workflows at app level.

## 6. Persistence and restore
- On symbol/interval change or relevant updates, `DrawingStorage` persists state.
- On next load, saved drawings/zoom/settings are restored automatically.

## Extending the Engine

- **Add broker support:** implement/plug a different fetcher in `data_loader.py` and keep UI/renderer unchanged.
- **Add indicators:** extend metric calculations in `metrics.py`, pass through `html_builder.py`, and draw in `chart.js`.
- **Add tools:** add toolbar action + JS drawing behavior + bridge event if Python callback is needed.
- **Add settings:** extend settings dialog + global settings schema in `DrawingStorage`.

---

If you want, I can also generate a **sequence diagram** (`mermaid`) in this same file showing the exact Python ↔ JS event flow.
