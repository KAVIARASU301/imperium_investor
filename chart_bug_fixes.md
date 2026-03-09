# Chart Deep Analysis — Swing Trading Terminal
*Full audit of chart.js / chart_widget.py / metrics.py*

---

## BUGS (things that are broken right now)

### 1. VWAP never resets per session — **CRITICAL**
**File:** `chart.js → _computeVWAP()`  
`cumTPV` and `cumVol` never reset. If you load 14 days of 5-minute data (the default), VWAP for day 2 onward is meaningless — it's a weighted average of all prior days combined. A real VWAP resets at the first candle of each trading session. The fix is to detect a new day (compare date of candle `i` vs candle `i-1`) and zero out the accumulators. This makes VWAP a **completely wrong indicator** right now on any multi-day intraday load.

---

### 2. Crosshair does not actually snap to OHLC — **HIGH**
**File:** `chart.js → _onMouseMove()` + `_drawCrosshair()`  
The comment says "magnetic OHLC snap" but the code is:
```js
this.crosshairX = pos.x;
this.crosshairY = pos.y; // raw mouse Y, no snap
```
The crosshair Y follows the raw mouse cursor. TC2000-style snap means: when you hover over a candle, the horizontal line should lock to the nearest of {open, high, low, close} of that candle. This gives you accurate price reads without freehand error.

---

### 3. Hidden indicators still inflate price bounds — **HIGH**
**File:** `chart.js → calculateBounds()`  
When EMA200 is toggled off via the indicator button, `calculateBounds()` still includes its values in `minPrice/maxPrice`. If EMA200 is far from current price (common for breakout stocks), it compresses the entire candle view. Fix: check `this.indicatorVisibility[key] !== false` before including EMA in bounds.

---

### 4. Volume label position hardcoded — **MEDIUM**
**File:** `chart.js → _drawCurrentVolLabel()`  
`const rx = this.width - 84;` is hardcoded. With the new dynamic axis width system, this will render inside the axis panel or outside it depending on price magnitude. It should use `this.chartArea.x + this.chartArea.width - someOffset`.

---

### 5. Math.min/max spread on large arrays — **MEDIUM**
**File:** `chart.js → calculateBounds()`  
```js
this.minPrice = Math.min(...slice.map(d => d.low));
```
Spreading thousands of elements into `Math.min` can hit JS engine call stack limits (~125k args). On a 1-year daily chart it's fine; on 1-minute x 90 days it will silently return `NaN` or throw. Use a `reduce` loop instead.

---

### 6. Fibonacci direction not handled — **LOW**
**File:** `chart.js → _drawFibonacci()`  
`const priceRange = fib.startPrice - fib.endPrice` is signed. If user draws bottom-to-top (which is natural for a bullish move), the levels invert. Should use `Math.abs` and sort start/end properly.

---

## MISSING FEATURES — High Impact for Swing Trading

### 7. RSI as a sub-pane indicator — **CRITICAL**
Every swing trader watches RSI. Currently the only sub-pane is volume. RSI(14) should be a toggleable sub-pane below volume, with:
- 70/30 overbought/oversold lines
- Colored fill (red above 70, green below 30)  
- The pane height can be slim (~60px, similar to volume pane)  

**Where to add:** `metrics.py` (compute RSI), `chart.js` (new `_drawRSI()` and `rsiArea` layout zone), toolbar indicator button.  
This is the single highest-impact missing feature.

---

### 8. Volume Moving Average — **HIGH**
A 20-period SMA line on the volume bars. When a candle's volume is above the MA line, it signals institutional activity — the core of swing trading setups like NR7, breakouts, and accumulation. Two lines: 20-period and optionally 50-period. Dead simple to implement.  

**Where to add:** `metrics.py` (compute vol_ma20), pass through `html_builder.py`, render in `_drawVolume()` as a thin line overlay.

---

### 9. Logarithmic price scale — **HIGH**
On weekly/monthly charts, stocks that moved 300% compress badly on a linear scale. Log scale makes percentage moves visually equal regardless of price level. This is fundamental for multi-year chart analysis.  

**Where to add:** toggle in toolbar or settings, override `_priceToY()` and `_yToPrice()` to use `Math.log`, update `_drawGrid()` tick logic for log-spaced prices.

---

### 10. Crosshair shows % distance from last close — **HIGH**
When you hover the chart, the Y-axis label should show both the price *and* the % distance from the last close. Example: `₹2,450 (+3.2%)`. This is how TC2000 works and is immediately useful — you don't need to mentally calculate how far a resistance is.  

**Where to add:** `_drawCrosshair()` — compute `((price - lastClose) / lastClose * 100)` and append to the pill label.

---

### 11. Drawings: drag to move — **HIGH**
Once a trend line or horizontal line is placed, you can't reposition it. You have to delete and redraw. TC2000 lets you grab any drawing and drag it. This is a major workflow gap.  

**Where to add:** `_onMouseMove()` — detect if mouse is over a selected drawing and shift its coordinates on drag. Requires adding a "drag mode" state alongside `isDrawing` and `isDragging`.

---

### 12. Trend lines: extend to right edge — **HIGH**
Drawn trend lines stop at the endpoint. The most useful version of a trend line extends infinitely to the right (a ray). This way it projects future resistance/support even after you've drawn it. Should be a property on the line object `{ extend: true }` with a toggle.  

**Where to add:** `_drawTrendLines()` — extrapolate the slope to the right edge of `chartArea.width`.

---

### 13. Previous day close line (intraday) — **MEDIUM**
On intraday charts, the previous day's close is the most important reference level — it defines gap-up/gap-down and is used as a first target/stop. Should draw as a thin horizontal dashed line with a "PDC" label.  

**Where to add:** detect interval is intraday, find last candle of previous session, draw similar to live price ray but static and labeled "PDC".

---

### 14. Relative Volume (RVOL) in volume panel — **MEDIUM**
RVOL = today's volume / average volume at the same time of day (or same bar number in the session). A number like 2.5x average tells you instantly this is a significant day. Even a simple "current vol vs 20-day average of full-day vol" ratio displayed as a label in the volume panel is extremely useful.  

**Where to add:** compute in `metrics.py`, display as an overlay label in the volume pane.

---

### 15. 52-week high / 52-week low marker — **MEDIUM**
A subtle horizontal line marking where the 52W high and low sit. When price approaches these levels, swing traders pay close attention. Should only render on the daily chart and be very subtle (dotted, dim color, small label).  

**Where to add:** compute in `metrics.py` (scan last 252 bars), pass to chart, render in `_drawAxes()` or as a special horizontal drawing.

---

### 16. Parallel channel drawing tool — **MEDIUM**
Channels are the most-used drawing for swing traders — you mark the slope of a trend and trade between the two parallel lines. Currently you'd need to draw two separate trend lines manually. A channel tool draws both simultaneously, locked in parallel.  

**Where to add:** new tool `channel` in `DRAWING_TOOLS`, store as a single drawing with 3 anchor points, render two lines from those points.

---

### 17. Undo / Redo for drawings — **MEDIUM**
No Ctrl+Z. If you accidentally place a fibonacci or line in the wrong place, the only option is to select and delete. A simple undo stack (last 20 operations) would dramatically improve the drawing workflow.  

**Where to add:** maintain `_undoStack` array in constructor, push state before each drawing mutation, bind Ctrl+Z to pop and restore, Ctrl+Y to redo.

---

### 18. Keyboard shortcuts for drawing tools — **MEDIUM**
TC2000 is entirely keyboard-driven. Professional traders never reach for menus.  
Suggested bindings:
- `T` → Trend line
- `H` → Horizontal line  
- `F` → Fibonacci
- `R` → Rectangle
- `Escape` → already works (clear tool)
- `Delete` → already works (delete selected)

**Where to add:** `_setupEventListeners()` keydown handler.

---

### 19. EMA labels always pinned on Y-axis — **LOW-MEDIUM**
Currently EMA right-edge labels only appear when `indicatorScaleLabelsEnabled` is on in settings. They should always be visible — small colored price pills pinned inside the axis panel for each active EMA. This is standard on TC2000 and saves you from having to hover the line to read the value.  

**Where to add:** `_drawEMAs()` — always render a small label, but make them part of the axis panel (not floating over chart). Render after `_drawPriceAxis()`.

---

### 20. Zoom presets (3M, 6M, 1Y, All) — **LOW-MEDIUM**
The current scroll wheel zoom is good but imprecise. Preset buttons like TC2000's `3M / 6M / 1Y / 5Y` let you jump to meaningful periods instantly. For daily charts, 1 year = 252 candles, 6M = 126, etc.  

**Where to add:** small buttons in the toolbar between timeframes and indicators, or in a right-click context menu on the time axis.

---

## PERFORMANCE / ARCHITECTURE

### 21. `calculateBounds()` called on every pan frame — **MEDIUM**
Every mouse move while dragging calls `calculateBounds()` which scans the entire visible slice for min/max plus all EMA arrays. For intraday 1-min charts with 5000+ candles, this is expensive per frame. Should cache bounds and only recompute when `viewPortStart/End` changes by >0.

---

### 22. `_computeVWAP()` on full dataset, called on every `addNewCandle` — **LOW**
For 5-day 1-min data (~1750 candles), this recomputes the entire VWAP array on every live tick. Should be incremental — keep running totals and only extend the array.

---

### 23. Data loader fetches fixed lookback, no incremental update — **LOW**
Each symbol/timeframe combination always fetches the full `_DAYS_BACK` window even if you last loaded 5 minutes ago. There's no "since last bar" incremental fetch. For intraday, this creates unnecessary API calls and delays on timeframe switches.

---

## SUMMARY TABLE

| # | Issue | Type | Impact | Effort |
|---|-------|------|--------|--------|
| 1 | VWAP resets per session | Bug | Critical | Low |
| 2 | Crosshair OHLC snap | Bug | High | Low |
| 3 | Hidden indicators in bounds | Bug | High | Low |
| 4 | Vol label position | Bug | Medium | Trivial |
| 5 | Math.min spread large arrays | Bug | Medium | Trivial |
| 6 | Fibonacci direction | Bug | Low | Low |
| 7 | RSI sub-pane | Feature | Critical | Medium |
| 8 | Volume MA line | Feature | High | Low |
| 9 | Log scale | Feature | High | Medium |
| 10 | Crosshair % from close | Feature | High | Low |
| 11 | Draw: drag to move | Feature | High | Medium |
| 12 | Trend line extend to right | Feature | High | Low |
| 13 | Prev day close line | Feature | Medium | Low |
| 14 | Relative Volume (RVOL) | Feature | Medium | Low |
| 15 | 52W high/low markers | Feature | Medium | Low |
| 16 | Parallel channel tool | Feature | Medium | Medium |
| 17 | Undo/Redo | Feature | Medium | Medium |
| 18 | Keyboard shortcuts | Feature | Medium | Low |
| 19 | EMA labels on axis | Feature | Low | Low |
| 20 | Zoom presets | Feature | Low | Low |
| 21 | calculateBounds perf | Perf | Medium | Low |
| 22 | VWAP incremental compute | Perf | Low | Low |
| 23 | Incremental data fetch | Perf | Low | Medium |

**Recommended order to implement:**  
Bugs 1→3 first (they're all small fixes, one of them is completely wrong data),  
then Features 10→8→12→13→15 (all low effort, high return),  
then Feature 7 (RSI — medium effort but the biggest visual upgrade),  
then Features 11→9→16→17 (these are larger but complete the trading workflow).