/**
 * chart_engine/renderer/chart.js
 *
 * Institutional-grade Canvas chart — TC2000-style dark renderer.
 *
 * What's in here:
 *   - FixedTradingChart class (self-contained, no external deps)
 *   - HiDPI canvas setup (sharp on Retina / 4K displays)
 *   - requestAnimationFrame render loop with dirty-flag (no wasted redraws)
 *   - Candle rendering: TC2000-style body + wick + subtle border
 *   - Volume bars: max-visible normalised (no percentile clipping)
 *   - Overlays: EMA10/20/50/200 with right-edge price labels
 *   - ATR Trend Reversal markers (3.01 ATR distance from EMA21)
 *   - VWAP line (institutional standard, calculated from cumulative TPV/Vol)
 *   - Magnetic crosshair that snaps to OHLC values
 *   - Live price ray with animated label
 *   - Session separators on intraday charts (market open line)
 *   - Gap detection: gap-up / gap-down fill between sessions
 *   - Drawing tools: trend line, H-line, H-ray, arrow, rectangle, fibonacci, note
 *   - Fibonacci retracement levels: 0%, 23.6%, 38.2%, 50%, 61.8%, 78.6%, 100%
 *   - Right-click context menu: alerts, orders, quick drawing insertion
 *   - Smooth pan (mouse drag) + scroll-to-zoom
 *   - Mini time-slider for navigation
 *   - Symbol watermark (configurable opacity/position/size)
 *   - Measure tool: price range + bar count
 *
 * Data is injected by html_builder.py as global JS variables before this
 * script runs. The chart reads window.__CHART_DATA__ for its config.
 */

'use strict';

// ─── Constants ──────────────────────────────────────────────────────────────

const CHART_FIB_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0];
const FIB_COLORS = ['#B88732', '#A8792A', '#00D4A8', '#3B82F6', '#7C6AA8', '#FF4D6A', '#B88732'];
const FIB_LABELS = ['0%', '23.6%', '38.2%', '50%', '61.8%', '78.6%', '100%'];
const IST_OFFSET_MS = 330 * 60 * 1000;
const NSE_OPEN_MINUTES = 9 * 60 + 15;
const NSE_CLOSE_MINUTES = 15 * 60 + 30;

// ─── Indicator persistence key (global — intentionally not per-symbol) ────────
// User toggles apply across ALL symbols, timeframes, and sessions.
// Python-passed initialIndicatorVisibility is used ONLY when localStorage has
// no record yet (i.e. first-ever launch).  After that, localStorage always wins.
const _IND_STORE_KEY = 'tc2k_indicator_vis_v1';

function _loadIndicatorState(pythonDefaults) {
    try {
        const raw = localStorage.getItem(_IND_STORE_KEY);
        if (raw) {
            const stored = JSON.parse(raw);
            // Merge: stored overrides python defaults, but any brand-new key
            // not yet in storage falls back to pythonDefaults (then to true).
            return { ...pythonDefaults, ...stored };
        }
    } catch (e) { /* corrupt storage — fall through to defaults */ }
    return { ...pythonDefaults };
}

function _saveIndicatorState(state) {
    try { localStorage.setItem(_IND_STORE_KEY, JSON.stringify(state)); }
    catch (e) { /* quota or security error — non-fatal */ }
}

// ─── FixedTradingChart ───────────────────────────────────────────────────────

class FixedTradingChart {
    constructor(cfg) {
        // ── Canvas ──
        this.canvas = document.getElementById(cfg.canvasId);
        this.ctx = this.canvas.getContext('2d', { alpha: false, desynchronized: false });
        this.dpr = 1;
        this.renderQualityMultiplier = Number.isFinite(cfg.renderQualityMultiplier)
            ? Math.min(Math.max(cfg.renderQualityMultiplier, 1), 2)
            : 1.5;
        this.fontStack = '"Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", "Noto Sans", Arial, sans-serif';

        // ── Data ──
        this.data = cfg.candlestickData || [];
        this.volumeData = cfg.volumeData || [];
        this.emaData = cfg.emaData || {};
        this.movingAverageConfigs = cfg.movingAverageConfigs || this.movingAverageConfigs || [];
        this.movingAverageConfigs = cfg.movingAverageConfigs || [];
        this.currentADR = cfg.initialADR || {};
        this.percentageChanges = cfg.percentageChanges || {};
        this.currentInterval = cfg.currentInterval || 'day';
        this._chartType = cfg.chartType === 'renko' ? 'candle' : (cfg.chartType || 'candle');
        this.heikinAshiData = [];
        this._renkoBoxPctIntraday = cfg.renkoBoxPctIntraday || 0.5;
        this._renkoBoxPctSwing = cfg.renkoBoxPctSwing || 1.5;
        this.currentSymbol = cfg.currentSymbol || '';
        this.priceScaleCurrency = this._resolvePriceScaleCurrency(cfg.priceScaleCurrency, this.currentSymbol);
        this.currentSymbolDescription = cfg.watermarkDescription || '';
        this.showWatermarkDescription = cfg.showWatermarkDescription === true;
        this._intradayTimestampsAlreadyIst = null;
        this._rebuildHeikinAshiData();

        // ── Settings ──
        this.colors = {
            bg:          '#050709',
            bgGradTop:   '#050709',
            bgGradBot:   '#050709',
            grid:        'rgba(26,32,48,0.72)',
            gridMinor:   'rgba(26,32,48,0.42)',
            text:        '#5A7090',
            textBright:  '#A8BCD4',
            crosshair:   'rgba(168,188,212,0.30)',
            livePrice:   '#E8F0FF',
            upCandle:    cfg.upCandleColor   || '#00D4A8',
            downCandle:  cfg.downCandleColor || '#FF4D6A',
            dojiCandle:  '#5A7090',
            upWick:      '#00A987',
            downWick:    '#CC3D56',
            upOhlc:      '#00D4A8',
            downOhlc:    '#FF4D6A',
        };

        // ── Viewport ──
        // Model: candleWidth + candleSpacing are FIXED (user-set).
        // visibleCount is DERIVED from chartArea.width / slotW — never set directly.
        // On resize: more/fewer candles appear automatically, no stretching.
        this.rightBufferCandles = Math.max(0, Number.isFinite(cfg.rightBufferCandles) ? cfg.rightBufferCandles : 20);
        this.candleWidth   = cfg.initialCandleWidth   || 8;   // body+wick pixel width — user control
        this.candleSpacing = cfg.initialCandleSpacing || 2;   // gap between candles in px
        this.visibleCandleCount = 100;                         // computed — don't use cfg value
        this.viewPortEnd   = Math.max(0, this.data.length - 1 + this.rightBufferCandles);
        this.viewPortStart = 0;                                // recalculated in _updateViewport()

        // ── Bounds ──
        this.minPrice = 0; this.maxPrice = 0;

        // ── State ──
        this.livePrice   = null;
        this.crosshairX  = null;
        this.crosshairY  = null;
        this.isDragging  = false;
        this.isYAxisDragging = false;
        this.yAxisDragStartY = 0;
        this.yAxisDragStartMin = 0;
        this.yAxisDragStartMax = 0;
        this.yAxisDragAnchorRatio = 0.5;
        this.isUserYRange = false;
        this.lastMouseX  = 0;
        this.lastMouseY  = 0;
        this.panOffsetPx = 0;
        this._olderDataRequestPending = false;
        this.isUserZooming = false;
        this._rafPending = false;
        this._dirty = true;
        this._lastInfoCandleIndex = -1;
        // Measure tool — ephemeral, zero DrawingEngine involvement
        this._measureStart = null;   // {x, y, price, time, candleIdx}
        this._measureEnd   = null;
        this._isMeasuring  = false;

        // ── Drawings (DrawingEngine) ──
        patchConstructor(this, cfg);
        if (this.drawingEngine) {
            this.drawingEngine.onToolCleared = () => this._notifyDrawingToolCleared();
            this.drawingEngine.currentSymbol = this.currentSymbol || '';
        }
        this.activeContextMenu = null;

        // ── Watermark ──
        this.watermark = {
            enabled:  cfg.watermarkEnabled !== false,
            color:    cfg.watermarkColor    || '#1A2030',
            opacity:  typeof cfg.watermarkOpacity  === 'number' ? cfg.watermarkOpacity  : 0.06,
            position: cfg.watermarkPosition || 'mid_center',
            fontSize: cfg.watermarkFontSize || 0,
            descriptionOpacity: typeof cfg.watermarkDescriptionOpacity === 'number' ? cfg.watermarkDescriptionOpacity : 0.08,
            descriptionFontSize: cfg.watermarkDescriptionFontSize || 0,
        };
        this.indicatorScaleLabelsEnabled = cfg.indicatorScaleLabelsEnabled === true;
        this.crosshairSnapEnabled = cfg.crosshairSnapEnabled !== false;
        this.showTimeSlider = cfg.showTimeSlider !== false;
        this.toolSelectionMode = cfg.toolSelectionMode === 'multi_use' ? 'multi_use' : 'single_use';
        this.infoVisibility = {
            show_adr: cfg.infoVisibility?.show_adr !== false,
            show_perf_monthly: cfg.infoVisibility?.show_perf_monthly !== false,
            show_perf_3m: cfg.infoVisibility?.show_perf_3m !== false,
            show_perf_6m: cfg.infoVisibility?.show_perf_6m !== false,
            show_perf_1y: cfg.infoVisibility?.show_perf_1y !== false,
            show_info_date: cfg.infoVisibility?.show_info_date !== false,
            show_info_open: cfg.infoVisibility?.show_info_open !== false,
            show_info_high: cfg.infoVisibility?.show_info_high !== false,
            show_info_low: cfg.infoVisibility?.show_info_low !== false,
            show_info_close: cfg.infoVisibility?.show_info_close !== false,
            show_info_volume: cfg.infoVisibility?.show_info_volume !== false,
            show_info_pct_change: cfg.infoVisibility?.show_info_pct_change !== false,
        };
        if (this.drawingEngine) {
            this.drawingEngine.toolSelectionMode = this.toolSelectionMode;
        }

        // ── Indicator visibility — persistent across symbol/timeframe changes ──
        // Priority chain: localStorage (user prefs) → pythonDefaults → false
        // No indicators are on by default; only what the user explicitly enables.
        // localStorage is global (not per-symbol) so user's choices stick forever.
        const _pythonDefaults = { ...(cfg.initialIndicatorVisibility || {}) };
        this.indicatorVisibility = _loadIndicatorState(_pythonDefaults);
        // indicator panel removed — toggles live in the Python toolbar (IND ▾)

        // ── Computed indicators — only if real historical data is present ──
        // CVD/VWAP/RSI must never run on empty or placeholder data.
        this._hasLiveTicks = false;
        if (this.data.length > 0) {
            this._computeRenko();
        }

        // ── Bridge ──
        this.chartBridge = null;
        this.webChannelInitialized = false;
        this._notifyQueue = [];
        this._notifyTimer = null;

        this._init();
    }

    // ═══════════════════════════════════════════════════════════════════════
    // INITIALISATION
    // ═══════════════════════════════════════════════════════════════════════

    async _init() {
        this._setupCanvas();
        this._updateViewport();   // derive visibleCount + viewPortStart from fixed slot width
        this._setupSlider();
        this.calculateBounds();
        installPublicApiShims(this);
        this._setupEventListeners();
        this._setupWebChannel();
        this._rebuildHeikinAshiData();
        this._rebuildHeikinAshiData();
        this.requestDraw();
        this.updateSlider();
        this._displayLatestCandleDetails();
        this._updateMetricsDisplay();
    }

    _setupCanvas() {
        const dpr = this._getEffectiveDpr();
        const w = this.canvas.clientWidth  || this.canvas.offsetWidth  || 800;
        const h = this.canvas.clientHeight || this.canvas.offsetHeight || 500;
        this.dpr = dpr;
        this.canvas.width  = Math.round(w * dpr);
        this.canvas.height = Math.round(h * dpr);
        this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        this.ctx.imageSmoothingEnabled = true;
        this.ctx.imageSmoothingQuality = 'high';
        this.ctx.textRendering = 'geometricPrecision';
        this.ctx.textBaseline = 'middle';

        this.width  = w;
        this.height = h;
        this._updateChartAreas();

        // Handle resize
        const ro = new ResizeObserver(() => this._onResize());
        ro.observe(this.canvas.parentElement || document.body);
    }

    _onResize() {
        const dpr = this._getEffectiveDpr();
        const w = this.canvas.clientWidth  || 800;
        const h = this.canvas.clientHeight || 500;
        this.dpr = dpr;
        this.canvas.width  = Math.round(w * dpr);
        this.canvas.height = Math.round(h * dpr);
        this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        this.width  = w;
        this.height = h;
        this._updateChartAreas();
        // Fixed slot width → more/fewer candles fit automatically, no stretching.
        this._updateViewport();
        this.calculateBounds();
        this._rebuildHeikinAshiData();
        this.requestDraw();
        this.updateSlider();
    }

    exportSnapshot(options = {}) {
        if (!this.canvas) {
            return { ok: false, error: 'Chart canvas is unavailable' };
        }

        try {
            // Force a resize/redraw to ensure canvas dimensions are correct
            // (Qt WebEngine may not have painted yet on first call).
            this._onResize();

            // Flush the latest render synchronously so pending drawing/price changes are
            // captured before Qt reads the PNG data URL.
            this._dirty = false;
            this._rafPending = false;
            this.draw();

            const source = this.canvas;
            const sourceWidth = source.width;
            const sourceHeight = source.height;
            const cssWidth = this.width || source.clientWidth || source.offsetWidth;
            const cssHeight = this.height || source.clientHeight || source.offsetHeight;

            if (!sourceWidth || !sourceHeight || !cssWidth || !cssHeight) {
                return { ok: false, error: `Canvas has zero dimensions (${sourceWidth}x${sourceHeight}). Chart may not be fully rendered.` };
            }

            const dpr = this._getEffectiveDpr();
            const requestedScale = Number(options.scale);
            const exportScale = Number.isFinite(requestedScale) ? Math.min(Math.max(requestedScale, 1), 5) : 2;
            const outputWidth = Math.round(cssWidth * dpr * exportScale);
            const outputHeight = Math.round(cssHeight * dpr * exportScale);
            const renderScale = dpr * exportScale;

            const output = document.createElement('canvas');
            output.width = outputWidth;
            output.height = outputHeight;
            const out = output.getContext('2d', { alpha: false });
            if (!out) {
                return { ok: false, error: 'Could not create snapshot rendering context' };
            }
            out.imageSmoothingEnabled = true;
            out.imageSmoothingQuality = 'high';
            out.setTransform(renderScale, 0, 0, renderScale, 0, 0);

            // Re-render the chart into a larger offscreen backing store instead of
            // scaling up the already-rasterized on-screen canvas. This preserves
            // sharp candles, axes, text, and drawings in the exported PNG.
            const liveCanvas = this.canvas;
            const liveCtx = this.ctx;
            const liveDrawingCanvas = this.drawingEngine?.canvas;
            const liveDrawingCtx = this.drawingEngine?.ctx;
            try {
                this.canvas = output;
                this.ctx = out;
                if (this.drawingEngine) {
                    this.drawingEngine.canvas = output;
                    this.drawingEngine.ctx = out;
                }
                this.draw();
            } finally {
                this.canvas = liveCanvas;
                this.ctx = liveCtx;
                if (this.drawingEngine) {
                    this.drawingEngine.canvas = liveDrawingCanvas;
                    this.drawingEngine.ctx = liveDrawingCtx;
                }
            }

            out.setTransform(1, 0, 0, 1, 0, 0);

            return {
                ok: true,
                dataUrl: output.toDataURL('image/png'),
                width: outputWidth,
                height: outputHeight,
                scale: exportScale,
                pixelRatio: dpr,
                sourceWidth,
                sourceHeight,
            };
        } catch (e) {
            return { ok: false, error: e ? (e.message || String(e)) : 'Unknown JS error in exportSnapshot' };
        }
    }

    _updateChartAreas() {
        const pad = { top: 18, right: this._computeRightAxisWidth(), bottom: 18, left: 8 };
        const paneW = this.width - pad.left - pad.right;
        const chartH = this.height - pad.top - pad.bottom;
        this.chartArea = { x: pad.left, y: pad.top, width: paneW, height: chartH };
        this.volumeArea = null;
        this._volumeScale = null;
        this.cvdArea = null;
        this.rsiArea = null;
        this.rightAxisWidth = pad.right;
    }

    // ── Slot geometry helpers ────────────────────────────────────────────────

    _slotW() {
        // Total pixels per candle slot: body + gap.  This is the ONE number that
        // controls density.  candleWidth is fixed; slotW drives everything else.
        return this.candleWidth + this.candleSpacing;
    }

    _updateViewport() {
        // Derive how many candles fit given the current chartArea width and slot size.
        // viewPortEnd is the anchor (panned position); viewPortStart follows.
        if (!this.chartArea) return;
        const slotW = this._slotW();
        const vis   = Math.max(1, Math.floor(this.chartArea.width / slotW));
        this.visibleCandleCount = vis;
        this.viewPortStart = Math.max(0, this.viewPortEnd - vis + 1);
    }

    _computeRightAxisWidth() {
        const minAxisWidth = 48;
        const maxAxisWidth = 120;
        const fallbackWidth = 82;
        if (!this.ctx) return fallbackWidth;

        const priceRange = this.maxPrice - this.minPrice;
        if (!Number.isFinite(priceRange) || priceRange <= 0) return fallbackWidth;

        const minGapPx = 26;
        const chartHeight = this.chartArea?.height || Math.max(120, this.height * 0.75);
        const ticks = Math.max(6, Math.floor(chartHeight / minGapPx));
        const step = this._niceStep(priceRange / ticks);
        const minR = Math.floor(this.minPrice / step) * step;
        const maxR = Math.ceil(this.maxPrice / step) * step;
        const decimals = this._priceDecimals(step);

        const prevFont = this.ctx.font;
        this.ctx.font = this._axisFont(10, 500);

        let maxTextWidth = 0;
        for (let p = minR; p <= maxR + step * 0.5; p += step) {
            const label = p.toFixed(decimals);
            maxTextWidth = Math.max(maxTextWidth, this.ctx.measureText(label).width);
        }

        this.ctx.font = prevFont;

        // 5px tick + 6px gap after tick + label + 6px right padding
        const dynamicWidth = Math.ceil(maxTextWidth + 5 + 6 + 6);
        return Math.max(minAxisWidth, Math.min(maxAxisWidth, dynamicWidth));
    }

    _initDrawings(json) {
        const def = { lines: [], rectangles: [], notes: [], horizontal_lines: [],
                      horizontal_rays: [], arrow_lines: [], fibonacci: [] };
        if (!json) return def;
        try {
            const d = typeof json === 'string' ? JSON.parse(json) : json;
            return {
                lines:           Array.isArray(d.lines)           ? d.lines           : [],
                rectangles:      Array.isArray(d.rectangles)      ? d.rectangles      : [],
                notes:           Array.isArray(d.notes)           ? d.notes           : [],
                horizontal_lines:Array.isArray(d.horizontal_lines)? d.horizontal_lines: [],
                horizontal_rays: Array.isArray(d.horizontal_rays) ? d.horizontal_rays : [],
                arrow_lines:     Array.isArray(d.arrow_lines)     ? d.arrow_lines     : [],
                fibonacci:       Array.isArray(d.fibonacci)       ? d.fibonacci       : [],
            };
        } catch { return def; }
    }


    // ═══════════════════════════════════════════════════════════════════════
    // KAGI COMPUTATION
    // ═══════════════════════════════════════════════════════════════════════
    //
    // Kagi charts use a reversal amount to filter noise.
    // Two methods used institutionally:
    //   1. ATR-based reversal  (adapts to volatility — preferred)
    //   2. Percentage reversal (fixed % of price — simpler, classic)
    //
    // A Kagi line has two states:
    //   YANG (thick) = price broke above the previous peak  → bullish control
    //   YIN  (thin)  = price broke below the previous trough → bearish control
    //
    // Each segment: { x1, y1, x2, y2, yang: bool, isReversal: bool }
    // ─────────────────────────────────────────────────────────────────────
    _computeRenko() {
        if (this.data.length < 2) { this.renkoBricks = []; return; }
        const intraday = ['minute', '3minute', '5minute', '10minute', '15minute', '30minute', '60minute'].includes(this.currentInterval);
        const boxPct = Math.max(0.01, intraday ? this._renkoBoxPctIntraday : this._renkoBoxPctSwing);
        let lastBrickClose = this.data[0].close;
        const bricks = [];
        for (let i = 1; i < this.data.length; i++) {
            const close = this.data[i].close;
            const brickSize = Math.max(0.0001, Math.abs(lastBrickClose) * (boxPct / 100.0));
            let delta = close - lastBrickClose;
            while (Math.abs(delta) >= brickSize) {
                const dir = delta > 0 ? 1 : -1;
                const nextClose = lastBrickClose + dir * brickSize;
                bricks.push({
                    fromPrice: lastBrickClose,
                    toPrice: nextClose,
                    fromIdx: i - 1,
                    toIdx: i,
                    yang: dir > 0,
                    goingUp: dir > 0,
                });
                lastBrickClose = nextClose;
                delta = close - lastBrickClose;
            }
        }
        this.renkoBricks = bricks;
    }

    _isHeikinAshiMode() {
        const chartType = this._chartType || window.__CHART_DATA__?.chartType || 'candle';
        return chartType === 'heikinashi';
    }

    _rebuildHeikinAshiData() {
        if (!Array.isArray(this.data) || this.data.length === 0) {
            this.heikinAshiData = [];
            return;
        }
        const out = [];
        for (let i = 0; i < this.data.length; i++) {
            const c = this.data[i];
            const haClose = (c.open + c.high + c.low + c.close) / 4;
            const haOpen = (i === 0)
                ? (c.open + c.close) / 2
                : (out[i - 1].open + out[i - 1].close) / 2;
            const haHigh = Math.max(c.high, haOpen, haClose);
            const haLow = Math.min(c.low, haOpen, haClose);
            out.push({ ...c, open: haOpen, high: haHigh, low: haLow, close: haClose });
        }
        this.heikinAshiData = out;
    }

    _getPriceSeriesForRendering() {
        return this._isHeikinAshiMode() ? this.heikinAshiData : this.data;
    }

    // ═══════════════════════════════════════════════════════════════════════
    // CVD  (Cumulative Volume Delta)  —  Steidlmayer bar-level estimation
    // ═══════════════════════════════════════════════════════════════════════
    //
    //   buy_frac  = (close - low)  / (high - low)   ← where in the range close sits
    //   sell_frac = (high - close) / (high - low)
    //   delta[i]  = vol × (buy_frac − sell_frac)    ← signed net volume per bar
    //   CVD[i]    = Σ delta[0..i]                   ← running cumulative sum
    //
    // Doji/inside bars (high == low): delta = 0 (conservative — no guess).
    // On intraday charts CVD resets at every session open (as per reference).
    // ────────────────────────────────────────────────────────────────────────

    // ═══════════════════════════════════════════════════════════════════════
    // RSI  (Relative Strength Index — Wilder 14-period smoothed)
    // ═══════════════════════════════════════════════════════════════════════
    //
    // Wilder's method (the institutional standard):
    //   Seed: simple average of first `period` gains & losses
    //   Then: avgGain = (prevAvgGain × (period-1) + gain) / period  ← RMA/SMMA
    //         avgLoss = (prevAvgLoss × (period-1) + loss) / period
    //   RS  = avgGain / avgLoss
    //   RSI = 100 - (100 / (1 + RS))
    //
    // First (period-1) bars yield null — not enough data.
    // ────────────────────────────────────────────────────────────────────────


    // ═══════════════════════════════════════════════════════════════════════
    // RENDER LOOP  (dirty-flag + rAF)
    // ═══════════════════════════════════════════════════════════════════════

    requestDraw() {
        this._dirty = true;
        if (this._rafPending) return;
        this._rafPending = true;
        requestAnimationFrame(() => {
            this._rafPending = false;
            if (this._dirty) { this._dirty = false; this.draw(); }
        });
    }

    draw() {
        const ctx = this.ctx;
        try {
            ctx.clearRect(0, 0, this.width, this.height);

            // Flat background to avoid a raised/sunken seam illusion
            // where the chart meets the embedded watchlist panel.
            ctx.fillStyle = this.colors.bg;
            ctx.fillRect(0, 0, this.width, this.height);

            if (this.data.length === 0) {
                ctx.fillStyle = this.colors.text;
                ctx.font = '14px "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", sans-serif';
                ctx.textAlign = 'center';
                ctx.fillText('No data available', this.width / 2, this.height / 2);
                return;
            }

            this._drawGrid();
            this._drawSessionSeparators();
            this._drawGaps();
            // Draw indicators first so price bars/candles remain visually on top.
            this._drawMovingAverages();
            // Chart type dispatch
            const chartType = this._chartType || window.__CHART_DATA__?.chartType || 'candle';
            if (chartType === 'bar') {
                this._drawOHLCBars();
            } else if (chartType === 'line') {
                this._drawLineChart();
            } else {
                this._drawCandlesticks();
            }
            this._drawVolumeBars();
            this._drawAwesomeOscillator();
            this._drawAxes();
            this.drawingEngine.render();
            this._drawMeasureOverlay();
            this._drawWatermark();
            this._drawLivePriceRay();
            this._drawCrosshair();

        } catch (e) { console.error('draw() error:', e); }
    }

    // ═══════════════════════════════════════════════════════════════════════
    // BACKGROUND / GRID
    // ═══════════════════════════════════════════════════════════════════════

    _drawGrid() {
        const ctx = this.ctx;
        const priceRange = this.maxPrice - this.minPrice;
        if (priceRange <= 0) return;

        const minGapPx = 26;
        const targetTicks = Math.max(6, Math.floor(this.chartArea.height / minGapPx));
        const step = this._niceStep(priceRange / targetTicks);
        const minR = Math.floor(this.minPrice / step) * step;
        const maxR = Math.ceil(this.maxPrice  / step) * step;

        ctx.setLineDash([]);
        for (let p = minR; p <= maxR + step * 0.5; p += step) {
            const y = this._priceToY(p);
            if (y < this.chartArea.y || y > this.chartArea.y + this.chartArea.height) continue;

            // Minor grid line
            ctx.strokeStyle = this.colors.gridMinor;
            ctx.lineWidth = 0.5;
            ctx.beginPath();
            ctx.moveTo(this.chartArea.x, y);
            ctx.lineTo(this.chartArea.x + this.chartArea.width, y);
            ctx.stroke();
        }

        // Right-side price axis border
        ctx.strokeStyle = this.colors.grid;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(this.chartArea.x + this.chartArea.width, this.chartArea.y);
        ctx.lineTo(this.chartArea.x + this.chartArea.width, this._paneBottom());
        ctx.stroke();
    }

    _drawSessionSeparators() {
        if (!this.currentInterval.includes('minute') || this.currentInterval === '60minute') return;
        const ctx = this.ctx;
        const MARKET_OPEN_HOUR = 9, MARKET_OPEN_MIN = 15;

        ctx.strokeStyle = 'rgba(90,112,144,0.34)';
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 5]);

        for (let i = Math.max(0, this.viewPortStart - 1); i <= this.viewPortEnd + 1 && i < this.data.length; i++) {
            const d = this._exchangeDate(this.data[i].time);
            if (d.getUTCHours() === MARKET_OPEN_HOUR && d.getUTCMinutes() === MARKET_OPEN_MIN) {
                const x = this._candleToX(i) + this.candleWidth / 2;
                ctx.beginPath();
                ctx.moveTo(x, this.chartArea.y);
                ctx.lineTo(x, this._paneBottom());
                ctx.stroke();
            }
        }
        ctx.setLineDash([]);
    }

    _drawGaps() {
        if (this.currentInterval !== 'day' && this.currentInterval !== 'week') return;
        const ctx = this.ctx;

        for (let i = Math.max(1, this.viewPortStart - 1); i <= this.viewPortEnd + 1 && i < this.data.length; i++) {
            const cur  = this.data[i];
            const prev = this.data[i - 1];
            if (!prev) continue;

            const gapUp   = cur.open > prev.high * 1.0015;
            const gapDown = cur.open < prev.low  * 0.9985;
            if (!gapUp && !gapDown) continue;

            const x1 = this._candleToX(i - 1) + this.candleWidth;
            const x2 = this._candleToX(i);
            if (x2 <= x1) continue;

            const topY    = this._priceToY(gapUp  ? prev.high : cur.open);
            const bottomY = this._priceToY(gapUp  ? cur.open  : prev.low);

            ctx.fillStyle = gapUp
                ? 'rgba(0,212,168,0.055)'
                : 'rgba(255,77,106,0.055)';
            ctx.fillRect(x1, topY, x2 - x1, bottomY - topY);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    // CANDLESTICKS
    // ═══════════════════════════════════════════════════════════════════════

    _drawCandlesticks() {
        const ctx      = this.ctx;
        const series   = this._getPriceSeriesForRendering();
        const visCount = this.viewPortEnd - this.viewPortStart + 1;
        if (visCount <= 0) return;

        // candleWidth is user-fixed — never recalculate to fill space.
        const bodyInset = this.candleWidth >= 8 ? 0.5 : 0.25;
        const bodyW     = Math.max(1, this.candleWidth - bodyInset * 2);
        const wickW     = this._snapStrokeWidth(this.candleWidth >= 7 ? 1.5 : 1);
        const drawBorder = this.candleWidth >= 6;

        ctx.lineJoin = 'miter';
        ctx.lineCap  = 'butt';

        for (let i = Math.max(0, this.viewPortStart - 1); i < series.length && i <= this.viewPortEnd + 1; i++) {
            if (i < 0) continue;
            const c = series[i];
            const x = this._candleToX(i);

            const openY  = this._priceToY(c.open);
            const closeY = this._priceToY(c.close);
            const highY  = this._priceToY(c.high);
            const lowY   = this._priceToY(c.low);

            const isDoji = Math.abs(c.close - c.open) < 1e-10;
            const isUp   = c.close > c.open;
            const col    = isDoji ? this.colors.dojiCandle : (isUp ? this.colors.upCandle : this.colors.downCandle);
            const wick   = isDoji ? this.colors.dojiCandle : (isUp ? this.colors.upWick : this.colors.downWick);
            const brdr   = wick;
            const cx    = x + this.candleWidth / 2;

            // Wick
            ctx.strokeStyle = wick;
            ctx.lineWidth   = wickW;
            ctx.beginPath();
            ctx.moveTo(cx, highY);
            ctx.lineTo(cx, lowY);
            ctx.stroke();

            // Body
            const topY   = Math.min(openY, closeY);
            const bodyH  = Math.max(1, Math.abs(closeY - openY));
            const bx     = x + bodyInset;

            ctx.fillStyle = col;
            ctx.fillRect(bx, topY, bodyW, bodyH);

            if (drawBorder) {
                ctx.strokeStyle = brdr;
                ctx.lineWidth   = this._snapStrokeWidth(0.7);
                ctx.strokeRect(bx + 0.5, topY + 0.5, Math.max(0, bodyW - 1), Math.max(0, bodyH - 1));
            }
        }

        // Live price candle — update last bar
        if (this.livePrice !== null && series.length > 0 && !this._isHeikinAshiMode()) {
            const last = series.length - 1;
            if (last >= this.viewPortStart && last <= this.viewPortEnd) {
                const c  = { ...series[last], close: this.livePrice,
                              high: Math.max(series[last].high, this.livePrice),
                              low:  Math.min(series[last].low,  this.livePrice) };
                const x     = this._candleToX(last);
                const bx    = x + bodyInset;
                const openY = this._priceToY(c.open);
                const clY   = this._priceToY(c.close);
                const hiY   = this._priceToY(c.high);
                const loY   = this._priceToY(c.low);
                const isDoji = Math.abs(c.close - c.open) < 1e-10;
                const isUp   = c.close > c.open;
                const col    = isDoji ? this.colors.dojiCandle : (isUp ? this.colors.upCandle : this.colors.downCandle);
                const wick   = isDoji ? this.colors.dojiCandle : (isUp ? this.colors.upWick : this.colors.downWick);
                const cx    = x + this.candleWidth / 2;

                ctx.strokeStyle = wick; ctx.lineWidth = wickW;
                ctx.beginPath(); ctx.moveTo(cx, hiY); ctx.lineTo(cx, loY); ctx.stroke();

                const topY  = Math.min(openY, clY);
                const bodyH = Math.max(1, Math.abs(clY - openY));
                ctx.fillStyle = col;
                ctx.fillRect(bx, topY, bodyW, bodyH);
            }
        }
    }

    _drawOHLCBars() {
        const ctx = this.ctx;
        const slotW = this._slotW();
        const stemW = Math.max(2, Math.min(4, this.candleWidth * 0.42));
        const tickW = Math.max(4, Math.min(Math.floor(slotW * 0.5), Math.floor(this.candleWidth * 1.4)));

        for (let i = this.viewPortStart; i <= this.viewPortEnd; i++) {
            if (i < 0 || i >= this.data.length) continue;
            const c = this.data[i];
            if (!c) continue;

            const x = this._candleToX(i);
            const cx = x + this.candleWidth / 2;
            const openY = this._priceToY(c.open);
            const highY = this._priceToY(c.high);
            const lowY = this._priceToY(c.low);
            const closeY = this._priceToY(c.close);

            const isDoji = Math.abs(c.close - c.open) < 1e-10;
            const isUp = c.close > c.open;
            const col = isDoji ? '#7B6A45' : (isUp ? this.colors.upOhlc : this.colors.downOhlc);

            ctx.strokeStyle = col;
            ctx.lineWidth = stemW;
            ctx.setLineDash([]);

            // High-low stem
            ctx.beginPath();
            ctx.moveTo(cx, highY);
            ctx.lineTo(cx, lowY);
            ctx.stroke();

            // Open tick (left)
            ctx.beginPath();
            ctx.moveTo(cx - tickW, openY);
            ctx.lineTo(cx, openY);
            ctx.stroke();

            // Close tick (right)
            ctx.beginPath();
            ctx.moveTo(cx, closeY);
            ctx.lineTo(cx + tickW, closeY);
            ctx.stroke();
        }
    }

    _drawLineChart() {
        const ctx = this.ctx;
        let first = true;

        ctx.strokeStyle = '#00D4FF';
        ctx.lineWidth = this._snapStrokeWidth(Math.max(1.2, Math.min(2.2, this.candleWidth * 0.25)));
        ctx.setLineDash([]);
        ctx.beginPath();

        for (let i = this.viewPortStart; i <= this.viewPortEnd; i++) {
            if (i < 0 || i >= this.data.length) continue;
            const c = this.data[i];
            if (!c) continue;

            const x = this._candleToX(i) + this.candleWidth / 2;
            const y = this._priceToY(c.close);
            if (!Number.isFinite(x) || !Number.isFinite(y)) continue;

            if (first) {
                ctx.moveTo(x, y);
                first = false;
            } else {
                ctx.lineTo(x, y);
            }
        }

        if (!first) ctx.stroke();

        // Extend the last visible point to live price when available.
        if (this.livePrice !== null && this.data.length > 0) {
            const last = this.data.length - 1;
            if (last >= this.viewPortStart && last <= this.viewPortEnd) {
                const x = this._candleToX(last) + this.candleWidth / 2;
                const closeY = this._priceToY(this.data[last].close);
                const liveY = this._priceToY(this.livePrice);

                if (Number.isFinite(x) && Number.isFinite(closeY) && Number.isFinite(liveY)) {
                    ctx.strokeStyle = this.livePrice >= this.data[last].close
                        ? this.colors.upWick
                        : this.colors.downWick;
                    ctx.lineWidth = Math.max(1.2, Math.min(2.2, this.candleWidth * 0.25));
                    ctx.beginPath();
                    ctx.moveTo(x, closeY);
                    ctx.lineTo(x, liveY);
                    ctx.stroke();
                }
            }
        }
    }

    _drawMovingAverages() {
        if (!Array.isArray(this.movingAverageConfigs) || this.movingAverageConfigs.length === 0) return;
        if (!this.emaData || Object.keys(this.emaData).length === 0) return;
        if (!Array.isArray(this.data) || this.data.length === 0) return;

        const start = Math.max(0, this.viewPortStart);
        const end = Math.min(this.data.length - 1, this.viewPortEnd);
        const firstTime = this.data[start]?.time;
        const lastTime = this.data[end]?.time;
        if (!Number.isFinite(firstTime) || !Number.isFinite(lastTime)) return;

        const ctx = this.ctx;
        for (const cfg of this.movingAverageConfigs) {
            const key = String(cfg?.id || '');
            if (!key || this.indicatorVisibility?.[key] !== true) continue;
            if (String(cfg?.type || '').toLowerCase() === 'ao') continue;

            const points = this.emaData[key];
            if (!Array.isArray(points) || points.length === 0) continue;

            const thickness = Math.max(0.5, Number(cfg?.thickness) || 1.2);
            const style = String(cfg?.line_style || 'solid');
            const dash = style === 'dashed' ? [8, 4] : (style === 'dotted' ? [2, 4] : []);

            ctx.save();
            ctx.strokeStyle = cfg?.color || '#00D4FF';
            ctx.lineWidth = thickness;
            ctx.setLineDash(dash);
            ctx.beginPath();

            let started = false;
            for (const p of points) {
                const t = Number(p?.time);
                const v = Number(p?.value);
                if (!Number.isFinite(t) || !Number.isFinite(v)) continue;
                if (t < firstTime || t > lastTime) continue;

                const x = this._timeToX(t);
                const y = this._priceToY(v);
                if (!Number.isFinite(x) || !Number.isFinite(y)) continue;

                if (!started) {
                    ctx.moveTo(x, y);
                    started = true;
                } else {
                    ctx.lineTo(x, y);
                }
            }

            if (started) ctx.stroke();
            ctx.restore();
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    // RENKO RENDERER
    // ═══════════════════════════════════════════════════════════════════════
    _drawRenko() {
        if (!this.renkoBricks || this.renkoBricks.length === 0) return;

        const ctx = this.ctx;
        const area = this.chartArea;

        // ── Visual constants (TC2000 style) ───────────────────────────────
        const YANG_COLOR  = '#00D4A8';   // Teal — bullish thickness
        const YIN_COLOR   = '#FF4D6A';   // Crimson — bearish thickness
        const YANG_WIDTH  = 2.5;
        const YIN_WIDTH   = 0.9;
        const HORIZ_COLOR = '#5A7090';   // neutral grey for horizontal connectors

        // Map kagi index → canvas X position
        // Kagi segments are spaced evenly regardless of time
        const totalSegs = this.renkoBricks.length;
        const slotW     = Math.max(8, area.width / Math.max(1, totalSegs + 2));

        // Helper: segment index → canvas X center
        const segX = (idx) => area.x + (idx + 1) * slotW;

        for (let i = 0; i < this.renkoBricks.length; i++) {
            const seg = this.renkoBricks[i];
            const x   = segX(i);
            const y1  = this._priceToY(seg.fromPrice);
            const y2  = this._priceToY(seg.toPrice);

            // ── Clip to chart area ────────────────────────────────────────
            if (x < area.x - 2 || x > area.x + area.width + 2) continue;
            if (Math.min(y1, y2) > area.y + area.height + 2) continue;
            if (Math.max(y1, y2) < area.y - 2) continue;

            const color = seg.yang ? YANG_COLOR : YIN_COLOR;
            const lw    = seg.yang ? YANG_WIDTH : YIN_WIDTH;

            // ── Horizontal connector (shoulder line) ──────────────────────
            // At each reversal, a small horizontal line connects to next segment
            if (i > 0) {
                const prevX = segX(i - 1);
                const prevSeg = this.renkoBricks[i - 1];
                ctx.strokeStyle = HORIZ_COLOR;
                ctx.lineWidth   = 1;
                ctx.setLineDash([]);
                ctx.beginPath();
                ctx.moveTo(prevX, y1);
                ctx.lineTo(x,     y1);
                ctx.stroke();
            }

            // ── Vertical line ─────────────────────────────────────────────
            ctx.strokeStyle = color;
            ctx.lineWidth   = lw;
            ctx.setLineDash([]);
            ctx.beginPath();
            ctx.moveTo(x, y1);
            ctx.lineTo(x, y2);
            ctx.stroke();

            // ── Yang/Yin transition marker ────────────────────────────────
            // A small circle at the transition point (where state changed)
            if (i > 0 && this.renkoBricks[i - 1].yang !== seg.yang) {
                ctx.fillStyle   = color;
                ctx.strokeStyle = '#0A0D12';
                ctx.lineWidth   = 1.2;
                ctx.beginPath();
                ctx.arc(x, y1, 3.5, 0, Math.PI * 2);
                ctx.fill();
                ctx.stroke();
            }
        }

        // ── Last price ray (same as candlestick mode) ─────────────────────
        const lastSeg = this.renkoBricks[this.renkoBricks.length - 1];
        if (lastSeg) {
            const liveP = this.livePrice || lastSeg.toPrice;
            const ly    = this._priceToY(liveP);
            if (ly >= area.y && ly <= area.y + area.height) {
                this._drawLivePriceRay();
            }
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    // CVD PANE
    // ═══════════════════════════════════════════════════════════════════════


    // ═══════════════════════════════════════════════════════════════════════
    // VOLUME
    // ═══════════════════════════════════════════════════════════════════════

    _getIntegratedVolumeColors() {
        return {
            upColor: this.colors.upCandle || "#00D4A8",
            downColor: this.colors.downCandle || "#FF4D6A",
        };
    }

    _drawVolumeBars() {
        if (!Array.isArray(this.data) || this.data.length === 0) return;

        const start = Math.max(0, this.viewPortStart);
        const end = Math.min(this.data.length - 1, this.viewPortEnd);
        if (end < start) return;

        let maxVol = 0;
        for (let i = start; i <= end; i++) {
            const v = Number(this.data[i]?.volume) || 0;
            if (v > maxVol) maxVol = v;
        }
        if (maxVol <= 0) return;

        const ctx = this.ctx;
        const area = this.chartArea;
        const { upColor, downColor } = this._getIntegratedVolumeColors();
        const barOpacity = 0.72;

        const volumeBandRatio = 0.125;
        const volumeBandBottomPadding = 4;
        const volumeBandHeight = Math.max(22, Math.floor(area.height * volumeBandRatio));
        const volumeBandBottom = area.y + area.height - volumeBandBottomPadding;
        const volumeBandTop = volumeBandBottom - volumeBandHeight;

        this._volumeScale = { maxVol, top: volumeBandTop, bottom: volumeBandBottom };

        ctx.save();
        for (let i = start; i <= end; i++) {
            const c = this.data[i];
            if (!c) continue;
            const vol = Number(c.volume) || 0;
            if (vol <= 0) continue;

            const x = Math.round(this._candleToX(i));
            const h = Math.max(1, Math.round((vol / maxVol) * volumeBandHeight));
            const y = volumeBandBottom - h;
            const isUp = (Number(c.close) || 0) >= (Number(c.open) || 0);
            const baseColor = isUp ? upColor : downColor;
            ctx.fillStyle = this._hexToRgba(baseColor, barOpacity);
            ctx.fillRect(x, y, Math.max(1, this.candleWidth), h);
        }
        ctx.restore();
    }

    _drawAwesomeOscillator() {
        if (!Array.isArray(this.movingAverageConfigs) || this.movingAverageConfigs.length === 0) return;
        if (!this.emaData || Object.keys(this.emaData).length === 0) return;
        const start = Math.max(0, this.viewPortStart);
        const end = Math.min(this.data.length - 1, this.viewPortEnd);
        if (end < start) return;
        const area = this.chartArea;
        const paneHeight = Math.max(36, Math.floor(area.height * 0.18));
        const paneBottom = area.y + area.height - 8;
        const paneTop = paneBottom - paneHeight;
        const ctx = this.ctx;

        for (const cfg of this.movingAverageConfigs) {
            if (String(cfg?.type || '').toLowerCase() !== 'ao') continue;
            const key = String(cfg?.id || '').trim();
            if (!key || this.indicatorVisibility?.[key] !== true) continue;
            const points = this.emaData[key];
            if (!Array.isArray(points) || points.length === 0) continue;
            const visible = points.filter((p) => {
                const t = Number(p?.time);
                return Number.isFinite(t) && t >= Number(this.data[start]?.time) && t <= Number(this.data[end]?.time);
            });
            if (visible.length === 0) continue;
            let maxAbs = 0;
            for (const p of visible) maxAbs = Math.max(maxAbs, Math.abs(Number(p?.value) || 0));
            if (maxAbs <= 0) continue;
            const upColor = String(cfg?.ao_green_color || '#00D4A8');
            const downColor = String(cfg?.ao_red_color || '#FF4D6A');
            const zeroY = paneTop + paneHeight / 2;

            for (const p of visible) {
                const t = Number(p?.time);
                const v = Number(p?.value);
                const d = Number(p?.diff);
                if (!Number.isFinite(t) || !Number.isFinite(v)) continue;
                const x = Math.round(this._timeToX(t));
                const h = Math.round((Math.abs(v) / maxAbs) * (paneHeight * 0.48));
                const y = v >= 0 ? zeroY - h : zeroY;
                const color = d <= 0 ? downColor : upColor;
                ctx.fillStyle = color;
                ctx.fillRect(x, y, Math.max(1, this.candleWidth), Math.max(1, h));
            }
        }
    }







    _niceVolStep(rough) {
        // Like _niceStep but tuned for volume (integer magnitudes, no sub-1 values)
        if (rough <= 0) return 1;
        const pow10 = Math.pow(10, Math.floor(Math.log10(rough)));
        const frac  = rough / pow10;
        let nice;
        if      (frac < 1.5) nice = 1;
        else if (frac < 3.5) nice = 2;
        else if (frac < 7.5) nice = 5;
        else                 nice = 10;
        return nice * pow10;
    }

    // ═══════════════════════════════════════════════════════════════════════
    // INDICATORS
    // ═══════════════════════════════════════════════════════════════════════


    // ═══════════════════════════════════════════════════════════════════════
    // PANE WAITING STATE  (clean label when indicator data unavailable)
    // ═══════════════════════════════════════════════════════════════════════

    _drawPaneWaiting(area, label, rgb) {
        const ctx = this.ctx;
        ctx.fillStyle = 'rgba(5,7,9,0.96)';
        ctx.fillRect(area.x, area.y, area.width, area.height);
        ctx.strokeStyle = 'rgba(26,32,48,0.85)';
        ctx.lineWidth = 0.8;
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.moveTo(area.x, area.y - 2);
        ctx.lineTo(area.x + area.width, area.y - 2);
        ctx.stroke();
        ctx.font = '700 9px "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", sans-serif';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';
        ctx.fillStyle = `rgba(${rgb},0.45)`;
        ctx.fillText(label, area.x + 4, area.y + 3);
        ctx.font = '10px "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = 'rgba(90,112,144,0.45)';
        ctx.fillText('No data', area.x + area.width / 2, area.y + area.height / 2);
    }




    // ═══════════════════════════════════════════════════════════════════════
    // AXES
    // ═══════════════════════════════════════════════════════════════════════

    _drawAxes() {
        this._drawPriceAxis();
        this._drawTimeAxis();
    }

    _drawPriceAxis() {
        const ctx = this.ctx;
        const priceRange = this.maxPrice - this.minPrice;
        if (priceRange <= 0) return;

        const axisX    = this.chartArea.x + this.chartArea.width;
        const axisW    = this.rightAxisWidth;
        const axisTop  = 0;
        const axisBot  = this.height;

        // ── Axis panel background ──────────────────────────────────────────
        ctx.fillStyle = '#070A0F';
        ctx.fillRect(axisX, axisTop, axisW, axisBot - axisTop);

        // ── Border lines of axis panel (left divider + right edge) ───────
        ctx.strokeStyle = 'rgba(26,32,48,0.92)';
        ctx.lineWidth   = 1;
        ctx.setLineDash([]);
        ctx.beginPath();
        // Left divider between chart pane and price scale
        ctx.moveTo(axisX + 0.5, axisTop);
        ctx.lineTo(axisX + 0.5, axisBot);
        // Right edge to frame the scale in dual-chart mode
        ctx.moveTo(axisX + axisW - 0.5, axisTop);
        ctx.lineTo(axisX + axisW - 0.5, axisBot);
        ctx.stroke();

        // ── Price ticks & labels ──────────────────────────────────────────
        const minGapPx = 28;
        const ticks    = Math.max(6, Math.floor(this.chartArea.height / minGapPx));
        const step     = this._niceStep(priceRange / ticks);
        const minR     = Math.floor(this.minPrice / step) * step;
        const maxR     = Math.ceil(this.maxPrice  / step) * step;
        const decimals = this._priceDecimals(step);

        // Keep tick labels optically aligned and away from the hard right edge.
        const tickLabelPadLeft  = 10;
        const tickLabelPadRight = 8;
        const tickLabelX        = axisX + tickLabelPadLeft;
        const tickLabelMaxW     = Math.max(0, axisW - tickLabelPadLeft - tickLabelPadRight);

        ctx.font         = this._axisFont(10, 700);
        ctx.textAlign    = 'left';
        ctx.textBaseline = 'middle';

        const paneBottom = this.chartArea.y + this.chartArea.height;
        const volumeTop = (this._volumeScale && Number.isFinite(this._volumeScale.top)) ? this._volumeScale.top : null;
        const axisSplitY = (volumeTop !== null) ? Math.max(this.chartArea.y, volumeTop) : null;
        const splitBufferPx = 12;
        // Keep price-axis labels above the integrated volume band so the two scales never overlap.
        const priceChartBottom = (axisSplitY !== null) ? axisSplitY : paneBottom;

        if (axisSplitY !== null) {
            // Subtle separator between price and volume regions inside the shared scale gutter.
            ctx.strokeStyle = 'rgba(26,32,48,0.68)';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(axisX + 1, axisSplitY + 0.5);
            ctx.lineTo(axisX + axisW - 1, axisSplitY + 0.5);
            ctx.stroke();
        }

        let lastY = -Infinity;
        for (let p = minR; p <= maxR + step * 0.5; p += step) {
            const y = this._priceToY(p);
            if (y < axisTop + 8 || y > priceChartBottom - 8) continue;
            if (axisSplitY !== null && Math.abs(y - axisSplitY) < splitBufferPx) continue;
            if (Math.abs(y - lastY) < minGapPx) continue;

            // Grid line echo — a very faint horizontal rule inside the axis panel
            ctx.strokeStyle = 'rgba(26,32,48,0.42)';
            ctx.lineWidth   = 0.5;
            ctx.beginPath();
            ctx.moveTo(axisX, y);
            ctx.lineTo(axisX + axisW, y);
            ctx.stroke();

            // Tick mark — clean 5px inward from axis border
            ctx.strokeStyle = 'rgba(90,112,144,0.58)';
            ctx.lineWidth   = 1;
            ctx.beginPath();
            ctx.moveTo(axisX,     y);
            ctx.lineTo(axisX + 5, y);
            ctx.stroke();

            // Label — left-aligned inside the axis gutter for cleaner visual rhythm
            ctx.fillStyle = this.colors.textBright;
            ctx.fillText(p.toFixed(decimals), tickLabelX, y, tickLabelMaxW);
            lastY = y;
        }

        this._drawVolumeScaleOnPriceAxis(axisX, axisW, tickLabelX, tickLabelMaxW);
    }

    _drawVolumeScaleOnPriceAxis(axisX, axisW, tickLabelX, tickLabelMaxW) {
        if (!this._volumeScale || !Number.isFinite(this._volumeScale.maxVol) || this._volumeScale.maxVol <= 0) return;
        const ctx = this.ctx;
        const { maxVol, top, bottom } = this._volumeScale;
        const splitBufferPx = 12;
        const todayVolume = Math.max(0, Number(this.data?.[this.data.length - 1]?.volume) || 0);
        const volumeRangePx = (bottom - top);
        const volumeToY = (v) => {
            if (maxVol <= 0 || volumeRangePx <= 0) return bottom;
            const ratio = Math.max(0, Math.min(1, (Number(v) || 0) / maxVol));
            return bottom - (ratio * volumeRangePx);
        };

        // Keep today's volume label exactly on its true level, then place two
        // additional labels at equal spacing for a cleaner visual rhythm.
        const todayY = volumeToY(todayVolume);
        const stepPx = Math.max(12, volumeRangePx / 3);
        const clampY = (y) => Math.max(top, Math.min(bottom, y));
        const yToVolume = (y) => {
            if (volumeRangePx <= 0) return 0;
            const ratio = Math.max(0, Math.min(1, (bottom - y) / volumeRangePx));
            return ratio * maxVol;
        };

        let tickYs = [];
        const hasAbove = (todayY - stepPx) >= top;
        const hasBelow = (todayY + stepPx) <= bottom;

        if (hasAbove && hasBelow) {
            tickYs = [todayY - stepPx, todayY, todayY + stepPx];
        } else if (hasBelow) {
            tickYs = [todayY, todayY + stepPx, todayY + (2 * stepPx)];
        } else if (hasAbove) {
            tickYs = [todayY - (2 * stepPx), todayY - stepPx, todayY];
        } else {
            tickYs = [todayY, todayY, todayY];
        }

        const ticks = tickYs.map((y, idx) => {
            const clampedY = clampY(y);
            return {
                y: clampedY,
                v: idx === 1 && hasAbove && hasBelow
                    ? todayVolume
                    : (idx === 0 && !hasAbove ? todayVolume : (idx === 2 && !hasBelow ? todayVolume : yToVolume(clampedY))),
                isToday: false,
            };
        });

        // Ensure exactly one marker is today's true volume.
        let todayIdx = 1;
        if (!hasAbove && hasBelow) todayIdx = 0;
        else if (hasAbove && !hasBelow) todayIdx = 2;
        ticks[todayIdx].y = todayY;
        ticks[todayIdx].v = todayVolume;
        ticks[todayIdx].isToday = true;

        ctx.font = this._axisFont(9, 600);
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.strokeStyle = 'rgba(90,112,144,0.42)';
        ctx.lineWidth = 1;

        for (const t of ticks) {
            // Keep volume labels from hugging the separator line at the top of the volume band.
            const drawY = Math.abs(t.y - top) < splitBufferPx ? (top + splitBufferPx) : t.y;
            ctx.beginPath();
            ctx.moveTo(axisX, drawY);
            ctx.lineTo(axisX + 4, drawY);
            ctx.stroke();
            ctx.fillStyle = t.isToday ? '#00D4FF' : 'rgba(168,188,212,0.78)';
            ctx.fillText(this._formatVolumeAxisValue(t.v), tickLabelX, drawY, tickLabelMaxW);
        }
    }

    _formatVolumeAxisValue(v) {
        const n = Number(v) || 0;
        if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
        if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
        if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
        return `${Math.round(n)}`;
    }

    _drawTimeAxis() {
        const ctx = this.ctx;
        const tf  = this.currentInterval || 'day';
        const candidates = this._buildTimeCandidates(tf);
        const axisTopY   = this._paneBottom() + 0.5;

        const frameLeft   = 0.5;
        const frameRight  = this.chartArea.x + this.chartArea.width + this.rightAxisWidth - 0.5;
        const frameTop    = 0.5;
        const frameBottom = this.height - 0.5;

        // Keep time labels vertically centered in the axis lane so they don't touch borders.
        const timeAxisMidY = axisTopY + ((frameBottom - axisTopY) / 2);

        // Production-grade frame: unify chart + time axis + right scale into one clean border.
        ctx.strokeStyle = 'rgba(26,32,48,0.90)';
        ctx.lineWidth   = 1;
        ctx.beginPath();
        // Outer rectangle (left, right and bottom edges).
        ctx.moveTo(frameLeft, frameTop);
        ctx.lineTo(frameLeft, frameBottom);
        ctx.lineTo(frameRight, frameBottom);
        ctx.lineTo(frameRight, frameTop);
        // Separator where the price pane ends and time-axis starts.
        ctx.moveTo(frameLeft, axisTopY);
        ctx.lineTo(frameRight, axisTopY);
        ctx.stroke();

        ctx.font          = this._axisFont(10, 500);
        ctx.textAlign     = 'center';
        ctx.textBaseline  = 'middle';
        ctx.fillStyle     = this.colors.text;


        // Currency label in the bottom-right axis corner (intersection of time and price axes).
        const currencyLabel = this.priceScaleCurrency || '';
        if (currencyLabel) {
            const cornerLeft = this.chartArea.x + this.chartArea.width;
            const cornerRight = cornerLeft + this.rightAxisWidth;
            const cornerTop = this._paneBottom();
            const cornerBottom = this.height;
            const cornerCenterX = cornerLeft + (cornerRight - cornerLeft) / 2;
            const cornerCenterY = cornerTop + (cornerBottom - cornerTop) / 2;

            ctx.font = this._axisFont(10, 700);
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillStyle = 'rgba(168,188,212,0.58)';
            ctx.fillText(currencyLabel, cornerCenterX, cornerCenterY + 0.5);
        }


        let lastRight = this.chartArea.x - 9999;
        let todayMarker = null;

        for (const pt of candidates) {
            const x = this._timeToX(pt.time);
            if (x < this.chartArea.x + 20 || x > this.chartArea.x + this.chartArea.width - 20) continue;
            const w = ctx.measureText(pt.label).width + 8;
            if (pt.isToday) {
                todayMarker = { ...pt, x, w };
                continue;
            }
            if (x - w / 2 < lastRight + 6) continue;

            ctx.strokeStyle = 'rgba(26,32,48,0.48)';
            ctx.lineWidth   = 0.5;
            ctx.beginPath();
            ctx.moveTo(x, this.chartArea.y);
            ctx.lineTo(x, this._paneBottom());
            ctx.stroke();

            ctx.fillText(pt.label, x, timeAxisMidY);
            lastRight = x + w / 2;
        }

        if (todayMarker) {
            const x = Math.max(this.chartArea.x + 20, Math.min(todayMarker.x, this.chartArea.x + this.chartArea.width - 20));
            ctx.fillStyle = '#F59E0B';
            ctx.font = this._axisFont(10, 700);
            ctx.fillText(todayMarker.label, x, timeAxisMidY);
            ctx.fillStyle = this.colors.text;
            ctx.font = this._axisFont(10, 500);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    // LIVE PRICE RAY
    // ═══════════════════════════════════════════════════════════════════════

    _drawLivePriceRay() {
        const price = this.livePrice || (this.data.length > 0 ? this.data[this.data.length - 1].close : null);
        if (price === null) return;

        const y = this._priceToY(price);
        if (y < this.chartArea.y || y > this.chartArea.y + this.chartArea.height) return;

        const ctx   = this.ctx;
        const axisX = this.chartArea.x + this.chartArea.width;
        const axisW = this.rightAxisWidth;

        const prevClose = this.data.length > 1 ? this.data[this.data.length - 2].close : this.data[0]?.open ?? price;
        const isUp      = price >= prevClose;
        const col       = isUp ? this.colors.upCandle : this.colors.downCandle;

        // Dashed price line across chart
        ctx.strokeStyle = col;
        ctx.lineWidth   = 0.8;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(this.chartArea.x, y);
        ctx.lineTo(axisX, y);
        ctx.stroke();
        ctx.setLineDash([]);

        // ── Full-width pill label on axis ──────────────────────────────────
        const label  = price.toFixed(2);
        const lh     = 17;
        const lx     = axisX;                  // start flush with axis border
        const lw     = axisW;                  // span the entire axis width
        const ly     = Math.round(y - lh / 2);

        // Rectangular label (TradingView-style)
        ctx.fillStyle = col;
        ctx.fillRect(lx, ly, lw, lh);

        // Label text — centered inside label
        ctx.font         = 'bold 10px "Inter", "Aptos", "Segoe UI Variable", "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", sans-serif';
        ctx.textAlign    = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle    = '#050709';
        ctx.fillText(label, lx + lw / 2, y);
    }

    // ═══════════════════════════════════════════════════════════════════════
    // CROSSHAIR  (magnetic OHLC snap)
    // ═══════════════════════════════════════════════════════════════════════

    _drawCrosshair() {
        if (this.crosshairX === null || this.isDrawing) return;
        const ctx = this.ctx;
        const x   = this.crosshairX;
        const y   = Math.max(this.chartArea.y, Math.min(this.crosshairY, this.chartArea.y + this.chartArea.height));

        const axisX = this.chartArea.x + this.chartArea.width;
        const axisW = this.rightAxisWidth;

        // Crosshair lines
        ctx.strokeStyle = this.colors.crosshair;
        ctx.lineWidth   = 0.7;
        ctx.setLineDash([4, 4]);

        ctx.beginPath();
        ctx.moveTo(x, this.chartArea.y);
        ctx.lineTo(x, this._paneBottom());
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(this.chartArea.x, y);
        ctx.lineTo(axisX, y);
        ctx.stroke();
        ctx.setLineDash([]);

        // ── Crosshair price label — full-width, matching axis panel ────────
        const price  = this._yToPrice(y);
        const plabel = price.toFixed(2);
        const lh     = 17;
        const lx     = axisX;
        const lw     = axisW;
        const ly     = Math.round(y - lh / 2);

        // Rectangular crosshair label
        ctx.fillStyle = '#0A0D12';
        ctx.fillRect(lx, ly, lw, lh);

        // Border
        ctx.strokeStyle = 'rgba(168,188,212,0.42)';
        ctx.lineWidth   = 0.8;
        ctx.strokeRect(lx + 0.5, ly + 0.5, lw - 1, lh - 1);

        // Label text — centered
        ctx.font         = 'bold 10px "Inter", "Aptos", "Segoe UI Variable", "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", sans-serif';
        ctx.textAlign    = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle    = '#E8F0FF';
        ctx.fillText(plabel, lx + lw / 2, y);

        // ── Time label at bottom ────────────────────────────────────────────
        const ci = this._xToCandle(x);
        if (ci >= 0 && ci < this.data.length) {
            const tlabel = this._fmtTimeLabel(this.data[ci].time);
            ctx.font      = 'bold 10px "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", sans-serif';
            ctx.textAlign = 'center';
            const ttw = ctx.measureText(tlabel).width;
            const tlw = ttw + 14, tlh = 15;
            const tlx = x - tlw / 2;
            const tly = this._paneBottom() + 1;

            // Rounded rect for time label
            ctx.fillStyle = '#0A0D12';
            ctx.beginPath();
            const r = 3;
            ctx.moveTo(tlx + r, tly);
            ctx.lineTo(tlx + tlw - r, tly);
            ctx.quadraticCurveTo(tlx + tlw, tly, tlx + tlw, tly + r);
            ctx.lineTo(tlx + tlw, tly + tlh - r);
            ctx.quadraticCurveTo(tlx + tlw, tly + tlh, tlx + tlw - r, tly + tlh);
            ctx.lineTo(tlx + r, tly + tlh);
            ctx.quadraticCurveTo(tlx, tly + tlh, tlx, tly + tlh - r);
            ctx.lineTo(tlx, tly + r);
            ctx.quadraticCurveTo(tlx, tly, tlx + r, tly);
            ctx.closePath();
            ctx.fill();

            ctx.strokeStyle = 'rgba(168,188,212,0.30)';
            ctx.lineWidth   = 0.7;
            ctx.stroke();

            ctx.fillStyle    = '#A8BCD4';
            ctx.textBaseline = 'middle';
            ctx.fillText(tlabel, x, tly + tlh / 2);
        }
    }

    _snapCrosshairY(mouseY, candleIndex) {
        if (!this.crosshairSnapEnabled) return mouseY;
        if (candleIndex < 0 || candleIndex >= this.data.length) return mouseY;

        const candle = this.data[candleIndex];
        const levels = [candle.open, candle.high, candle.low, candle.close]
            .filter(v => Number.isFinite(v))
            .map(price => ({ price, y: this._priceToY(price) }));

        if (levels.length === 0) return mouseY;

        let nearest = levels[0].y;
        let minDist = Math.abs(mouseY - nearest);
        for (let i = 1; i < levels.length; i++) {
            const dist = Math.abs(mouseY - levels[i].y);
            if (dist < minDist) {
                minDist = dist;
                nearest = levels[i].y;
            }
        }

        return nearest;
    }

    // ═══════════════════════════════════════════════════════════════════════
    // WATERMARK
    // ═══════════════════════════════════════════════════════════════════════

    _drawWatermark() {
        if (!this.watermark.enabled || !this.currentSymbol) return;
        const ctx = this.ctx;
        const yMap = {
            top_center:    this.chartArea.y + this.chartArea.height * 0.2,
            mid_center:    this.chartArea.y + this.chartArea.height * 0.5,
            bottom_center: this.chartArea.y + this.chartArea.height * 0.8,
        };
        const centerX = this.chartArea.x + this.chartArea.width / 2;
        const centerY = yMap[this.watermark.position] || yMap.mid_center;
        const hasDescription = this.showWatermarkDescription && !!this.currentSymbolDescription;
        const fontSize = this.watermark.fontSize > 0
            ? this.watermark.fontSize
            : Math.max(32, Math.round(this.chartArea.width * 0.08));

        ctx.save();
        ctx.textAlign    = 'center';
        ctx.textBaseline = 'middle';
        const symbolYOffset = hasDescription ? 28 : 0;
        ctx.globalAlpha  = this.watermark.opacity;
        ctx.fillStyle    = this.watermark.color;
        ctx.font         = `700 ${fontSize}px "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", sans-serif`;
        ctx.fillText(this.currentSymbol, centerX, centerY - symbolYOffset);

        if (hasDescription) {
            const descriptionFontSize = this.watermark.descriptionFontSize > 0
                ? this.watermark.descriptionFontSize
                : Math.max(14, Math.round(fontSize * 0.32));
            ctx.globalAlpha = Math.max(0.0, Math.min(1.0, this.watermark.descriptionOpacity));
            ctx.font        = `600 ${descriptionFontSize}px "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", sans-serif`;
            const descriptionLines = String(this.currentSymbolDescription || '')
                .split('\n')
                .map(line => line.trim())
                .filter(Boolean);
            const lineHeight = Math.round(descriptionFontSize * 1.25);
            const baseY = centerY + Math.round(fontSize * 0.48);
            if (descriptionLines.length <= 1) {
                ctx.fillText(descriptionLines[0] || '', centerX, baseY);
            } else {
                const totalHeight = lineHeight * (descriptionLines.length - 1);
                const firstLineY = baseY - Math.round(totalHeight / 2);
                descriptionLines.forEach((line, idx) => {
                    ctx.fillText(line, centerX, firstLineY + (idx * lineHeight));
                });
            }
        }
        ctx.restore();
    }

    // ═══════════════════════════════════════════════════════════════════════
    // DRAWINGS
    // ═══════════════════════════════════════════════════════════════════════

    _drawAllDrawings() {
        this._drawHorizontalLines();
        this._drawHorizontalRays();
        this._drawTrendLines();
        this._drawArrowLines();
        this._drawRectangles();
        this._drawFibonacci();
        this._drawNotes();
        if (this.isDrawing && this.startPoint && this.endPoint) this._drawInProgress();
    }

    _drawHorizontalLines() {
        const ctx = this.ctx;
        for (const line of this.drawings.horizontal_lines) {
            const y   = this._priceToY(line.price);
            const sel = line.id === this.selectedDrawingId;
            ctx.strokeStyle = line.color || '#B88732';
            ctx.lineWidth   = sel ? (line.lineWidth || 1.5) + 1 : (line.lineWidth || 1.5);
            ctx.setLineDash(line.style === 'dashed' ? [6, 4] : []);
            ctx.beginPath();
            ctx.moveTo(this.chartArea.x, y);
            ctx.lineTo(this.chartArea.x + this.chartArea.width, y);
            ctx.stroke();
            ctx.setLineDash([]);

            if (line.label) {
                ctx.font      = '10px "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", sans-serif';
                ctx.textAlign = 'right';
                ctx.fillStyle = line.color || '#B88732';
                ctx.fillText(line.label, this.chartArea.x + this.chartArea.width - 4, y - 3);
            }
        }
    }

    _drawHorizontalRays() {
        const ctx = this.ctx;
        for (const ray of this.drawings.horizontal_rays) {
            const startX = this._timeToX(ray.startTime);
            const y = this._priceToY(ray.startPrice);
            const sel = ray.id === this.selectedDrawingId;
            ctx.strokeStyle = ray.color || '#B88732';
            ctx.lineWidth   = sel ? 2.5 : 1.5;
            ctx.setLineDash([]);
            ctx.beginPath();
            ctx.moveTo(startX, y);
            ctx.lineTo(this.chartArea.x + this.chartArea.width, y);
            ctx.stroke();
            ctx.setLineDash([]);
        }
    }

    _drawTrendLines() {
        const ctx = this.ctx;
        for (const line of this.drawings.lines) {
            const sx = this._timeToX(line.startTime), sy = this._priceToY(line.startPrice);
            const ex = this._timeToX(line.endTime),   ey = this._priceToY(line.endPrice);
            if (!this._lineVisible(sx, sy, ex, ey)) continue;
            const sel = line.id === this.selectedDrawingId;
            ctx.strokeStyle = line.color || '#B88732';
            ctx.lineWidth   = sel ? (line.lineWidth || 1.5) + 1 : (line.lineWidth || 1.5);
            ctx.setLineDash([]);
            ctx.beginPath();
            ctx.moveTo(sx, sy);
            ctx.lineTo(ex, ey);
            ctx.stroke();
            this._drawHandles(sx, sy, ex, ey, sel, line.color || '#B88732');
        }
    }

    _drawArrowLines() {
        const ctx = this.ctx;
        for (const arrow of this.drawings.arrow_lines) {
            const sx = this._timeToX(arrow.startTime), sy = this._priceToY(arrow.startPrice);
            const ex = this._timeToX(arrow.endTime),   ey = this._priceToY(arrow.endPrice);
            if (!this._lineVisible(sx, sy, ex, ey)) continue;
            ctx.strokeStyle = arrow.color || '#B88732';
            ctx.fillStyle   = arrow.color || '#B88732';
            ctx.lineWidth   = arrow.lineWidth || 1.5;
            ctx.setLineDash([]);
            ctx.beginPath();
            ctx.moveTo(sx, sy);
            ctx.lineTo(ex, ey);
            ctx.stroke();
            this._drawArrowhead(sx, sy, ex, ey, arrow.color || '#B88732');
        }
    }

    _drawArrowhead(sx, sy, ex, ey, color) {
        const ctx   = this.ctx;
        const angle = Math.atan2(ey - sy, ex - sx);
        const size  = 10;
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.moveTo(ex, ey);
        ctx.lineTo(ex - size * Math.cos(angle - 0.4), ey - size * Math.sin(angle - 0.4));
        ctx.lineTo(ex - size * Math.cos(angle + 0.4), ey - size * Math.sin(angle + 0.4));
        ctx.closePath();
        ctx.fill();
    }

    _drawRectangles() {
        const ctx = this.ctx;
        for (const rect of this.drawings.rectangles) {
            const sx = this._timeToX(rect.startTime), sy = this._priceToY(rect.startPrice);
            const ex = this._timeToX(rect.endTime),   ey = this._priceToY(rect.endPrice);
            const x  = Math.min(sx, ex), y = Math.min(sy, ey);
            const w  = Math.abs(ex - sx), h = Math.abs(ey - sy);
            if (!this._rectVisible(x, y, w, h)) continue;
            const sel = rect.id === this.selectedDrawingId;
            ctx.fillStyle   = this._hexToRgba(rect.color || '#B88732', 0.08);
            ctx.fillRect(x, y, w, h);
            ctx.strokeStyle = rect.color || '#B88732';
            ctx.lineWidth   = sel ? 2 : 1;
            ctx.setLineDash([]);
            ctx.strokeRect(x, y, w, h);
        }
    }

    _drawFibonacci() {
        const ctx = this.ctx;
        for (const fib of this.drawings.fibonacci) {
            const sx = this._timeToX(fib.startTime), sy = this._priceToY(fib.startPrice);
            const ex = this._timeToX(fib.endTime),   ey = this._priceToY(fib.endPrice);
            const priceRange = fib.startPrice - fib.endPrice;

            CHART_FIB_LEVELS.forEach((level, idx) => {
                const price = fib.endPrice + priceRange * level;
                const y     = this._priceToY(price);
                const col   = FIB_COLORS[idx];

                ctx.strokeStyle = this._hexToRgba(col, 0.6);
                ctx.lineWidth   = 0.8;
                ctx.setLineDash([]);
                ctx.beginPath();
                ctx.moveTo(Math.min(sx, ex), y);
                ctx.lineTo(Math.max(sx, ex), y);
                ctx.stroke();

                // Shade between levels
                if (idx < CHART_FIB_LEVELS.length - 1) {
                    const nextPrice = fib.endPrice + priceRange * CHART_FIB_LEVELS[idx + 1];
                    const nextY = this._priceToY(nextPrice);
                    ctx.fillStyle = this._hexToRgba(col, 0.04);
                    ctx.fillRect(Math.min(sx, ex), Math.min(y, nextY),
                                 Math.abs(ex - sx), Math.abs(nextY - y));
                }

                // Label
                ctx.font      = '9px "Inter", "Aptos", "Segoe UI Variable", "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", sans-serif';
                ctx.textAlign = 'right';
                ctx.fillStyle = col;
                ctx.fillText(`${FIB_LABELS[idx]}  ₹${price.toFixed(2)}`, Math.max(sx, ex) - 2, y - 2);
            });
        }
    }

    _drawNotes() {
        const ctx = this.ctx;
        for (const note of this.drawings.notes) {
            if (!note.text) continue;
            const x = this._timeToX(note.time), y = this._priceToY(note.price);
            if (!this._ptVisible(x, y)) continue;

            ctx.font      = `${note.size || 12}px "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", sans-serif`;
            ctx.fillStyle = note.color || '#B88732';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';

            // Naked text note centered on the note origin
            ctx.fillText(note.text, x, y);

            // Pin dot
            ctx.beginPath();
            ctx.arc(x, y, 3, 0, Math.PI * 2);
            ctx.fill();
        }
    }

    _drawHandles(sx, sy, ex, ey, selected, color) {
        if (!selected) return;
        const ctx = this.ctx;
        ctx.fillStyle   = color;
        ctx.strokeStyle = '#A8BCD4';
        ctx.lineWidth   = 1;
        for (const [hx, hy] of [[sx, sy], [ex, ey]]) {
            ctx.beginPath();
            ctx.arc(hx, hy, 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
        }
    }

    _drawInProgress() {
        if (!this.startPoint || !this.endPoint) return;
        const ctx = this.ctx;
        ctx.strokeStyle = this.drawingColor;
        ctx.lineWidth   = this.lineWidth;
        ctx.setLineDash([4, 4]);

        const sx = this._timeToX(this.startPoint.time), sy = this._priceToY(this.startPoint.price);
        const ex = this.endPoint.x, ey = this.endPoint.y;

        if (['line', 'arrow_line', 'fibonacci'].includes(this.currentTool)) {
            ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ex, ey); ctx.stroke();
        } else if (this.currentTool === 'measure') {
            ctx.beginPath();
            ctx.moveTo(sx, sy);
            ctx.lineTo(ex, ey);
            ctx.stroke();
            this._drawMeasurementInfo(sx, sy, ex, ey);
        } else if (this.currentTool === 'rectangle') {
            ctx.strokeRect(Math.min(sx, ex), Math.min(sy, ey),
                           Math.abs(ex - sx), Math.abs(ey - sy));
        } else if (this.currentTool === 'horizontal_line') {
            ctx.beginPath(); ctx.moveTo(this.chartArea.x, sy);
            ctx.lineTo(this.chartArea.x + this.chartArea.width, sy); ctx.stroke();
        } else if (this.currentTool === 'horizontal_ray') {
            ctx.beginPath(); ctx.moveTo(sx, sy);
            ctx.lineTo(this.chartArea.x + this.chartArea.width, sy); ctx.stroke();
        }
        ctx.setLineDash([]);
    }

    _drawMeasurementInfo(sx, sy, ex, ey) {
        const ctx = this.ctx;
        const startPrice = this._yToPrice(sy);
        const endPrice = this._yToPrice(ey);
        const priceChange = endPrice - startPrice;
        const pctChange = startPrice !== 0 ? (priceChange / startPrice) * 100 : 0;

        const startIndex = this._xToCandle(sx);
        const endIndex = this._xToCandle(ex);
        const bars = endIndex - startIndex;

        const startTime = this._xToTime(sx);
        const endTime = this._xToTime(ex);
        const dayCount = Math.floor(Math.abs(endTime - startTime) / 86400000);

        const sign = priceChange >= 0 ? '+' : '';
        const infoText = [
            `${sign}₹${priceChange.toFixed(2)} (${sign}${pctChange.toFixed(2)}%)`,
            `${bars >= 0 ? '+' : ''}${bars} bars, ${dayCount} days`
        ];

        ctx.save();
        ctx.font = this._axisFont(12, 600);
        const textWidth = Math.max(ctx.measureText(infoText[0]).width, ctx.measureText(infoText[1]).width);
        const rowPaddingX = 0;
        const rowHeight = 16;
        const rowGap = 3;
        const boxW = textWidth + (rowPaddingX * 2);
        const boxH = (rowHeight * 2) + rowGap;

        let boxX = ex + 12;
        let boxY = ey;
        const rightEdge = this.chartArea.x + this.chartArea.width;
        const bottomEdge = this.chartArea.y + this.chartArea.height;
        if (boxX + boxW > rightEdge) boxX = ex - boxW - 12;
        if (boxY + boxH > bottomEdge) boxY = bottomEdge - boxH;
        if (boxY < this.chartArea.y) boxY = this.chartArea.y;

        ctx.fillStyle = 'rgba(232,240,255,0.78)';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(infoText[0], boxX + rowPaddingX, boxY + (rowHeight / 2) + 0.5);
        ctx.fillText(infoText[1], boxX + rowPaddingX, boxY + rowHeight + rowGap + (rowHeight / 2) + 0.5);
        ctx.restore();
    }

    _drawMeasureOverlay() {
        if (!this._isMeasuring || !this._measureStart || !this._measureEnd) return;

        const ctx = this.ctx;
        const s   = this._measureStart;
        const end = this._measureEnd;
        const sx  = s.x;
        const sy  = s.y;
        const ex  = end.x;
        const ey  = end.y;

        // ── Shaded rectangle ─────────────────────────────────────────────
        const rectX = Math.min(sx, ex);
        const rectY = Math.min(sy, ey);
        const rectW = Math.abs(ex - sx);
        const rectH = Math.abs(ey - sy);

        ctx.save();

        ctx.fillStyle = 'rgba(0,212,255,0.055)';
        ctx.fillRect(rectX, rectY, rectW, rectH);

        ctx.strokeStyle = 'rgba(0,212,255,0.28)';
        ctx.lineWidth   = 1;
        ctx.setLineDash([]);
        ctx.strokeRect(rectX, rectY, rectW, rectH);
        ctx.setLineDash([]);

        // ── Diagonal line start → end ────────────────────────────────────
        ctx.strokeStyle = 'rgba(0,212,255,0.52)';
        ctx.lineWidth   = 1.2;
        ctx.beginPath();
        ctx.moveTo(sx, sy);
        ctx.lineTo(ex, ey);
        ctx.stroke();

        // ── Corner anchor dots ───────────────────────────────────────────
        for (const [px, py] of [[sx, sy], [ex, ey]]) {
            ctx.fillStyle   = '#00D4FF';
            ctx.strokeStyle = 'rgba(5,7,9,0.86)';
            ctx.lineWidth   = 1;
            ctx.beginPath();
            ctx.arc(px, py, 3.5, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
        }

        // ── Compute stats ─────────────────────────────────────────────────
        const priceDiff = end.price - s.price;
        const pctChange = s.price !== 0 ? (priceDiff / s.price) * 100 : 0;
        const barA      = Math.min(s.candleIdx, end.candleIdx);
        const barB      = Math.max(s.candleIdx, end.candleIdx);
        const bars      = Math.max(0, barB - barA);

        let dayCount = 0;
        if (barA >= 0 && barB < this.data.length) {
            const t1 = new Date(this.data[Math.max(0, barA)].time);
            const t2 = new Date(this.data[Math.min(this.data.length - 1, barB)].time);
            dayCount = Math.round(Math.abs(t2 - t1) / 86400000);
        }

        const sign   = priceDiff >= 0 ? '+' : '';
        const valCol = priceDiff >= 0 ? '#00D4A8' : '#FF4D6A';

        const lines = [
            { label: 'Δ Price', value: `${sign}₹${Math.abs(priceDiff).toFixed(2)}`, color: valCol },
            { label: '% Move',  value: `${sign}${pctChange.toFixed(2)}%`,            color: valCol },
            { label: 'Bars',    value: `${bars}`,                                     color: '#A8BCD4' },
            { label: 'Days',    value: `${dayCount}`,                                 color: '#A8BCD4' },
        ];

        // ── Callout box ──────────────────────────────────────────────────
        const lineH  = 19;
        const padX   = 12;
        const padY   = 9;
        const boxW   = 164;
        const boxH   = lines.length * lineH + padY * 2;

        const { chartArea: a } = this;
        let bx = ex + 16;
        let by = ey - boxH / 2;
        if (bx + boxW > a.x + a.width - 6)  bx = ex - boxW - 16;
        if (bx < a.x + 6)                   bx = a.x + 6;
        if (by < a.y + 6)                   by = a.y + 6;
        if (by + boxH > a.y + a.height - 6) by = a.y + a.height - boxH - 6;

        // Background
        ctx.fillStyle = 'rgba(7,10,15,0.94)';
        ctx.beginPath();
        ctx.roundRect(bx, by, boxW, boxH, 5);
        ctx.fill();

        // Border
        ctx.strokeStyle = 'rgba(26,32,48,0.92)';
        ctx.lineWidth   = 0.8;
        ctx.stroke();

        // Top accent line
        ctx.fillStyle = priceDiff >= 0 ? '#00D4A8' : '#FF4D6A';
        ctx.fillRect(bx + 1, by + 1, boxW - 2, 2);

        // Text rows
        lines.forEach((line, i) => {
            const rowY = by + padY + i * lineH + lineH / 2;

            // Label
            ctx.font         = '500 10px "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", sans-serif';
            ctx.textAlign    = 'left';
            ctx.textBaseline = 'middle';
            ctx.fillStyle    = 'rgba(168,188,212,0.68)';
            ctx.fillText(line.label, bx + padX, rowY);

            // Value
            ctx.font      = '700 11px "Inter", "Aptos", "Segoe UI Variable", "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", sans-serif';
            ctx.textAlign = 'right';
            ctx.fillStyle = line.color;
            ctx.fillText(line.value, bx + boxW - padX, rowY);
        });

        // ── Y-axis pills for start and end price ─────────────────────────
        const axisX = a.x + a.width;
        const axisW = this.rightAxisWidth || 72;

        for (const [py, price] of [[sy, s.price], [ey, end.price]]) {
            if (py < a.y - 1 || py > a.y + a.height + 1) continue;

            // Dashed ray to axis
            ctx.strokeStyle = 'rgba(0,212,255,0.18)';
            ctx.lineWidth   = 0.6;
            ctx.setLineDash([2, 3]);
            ctx.beginPath();
            ctx.moveTo(axisX - 12, py);
            ctx.lineTo(axisX, py);
            ctx.stroke();
            ctx.setLineDash([]);

            // Axis pill
            const lh   = 14;
            const lTop = Math.round(py - lh / 2);
            ctx.fillStyle = 'rgba(10,13,18,0.96)';
            ctx.beginPath();
            ctx.moveTo(axisX,         py);
            ctx.lineTo(axisX + 4,     lTop);
            ctx.lineTo(axisX + axisW, lTop);
            ctx.lineTo(axisX + axisW, lTop + lh);
            ctx.lineTo(axisX + 4,     lTop + lh);
            ctx.closePath();
            ctx.fill();
            ctx.strokeStyle = 'rgba(0,212,255,0.25)';
            ctx.lineWidth   = 0.7;
            ctx.stroke();

            ctx.fillStyle    = '#A8BCD4';
            ctx.font         = 'bold 9px "Inter", "Aptos", "Segoe UI Variable", "Inter", "Aptos", "Segoe UI Variable", "Segoe UI", "Roboto", sans-serif';
            ctx.textAlign    = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(price.toFixed(2), axisX + 4 + (axisW - 4) / 2, py);
        }

        ctx.restore();
    }

    // ═══════════════════════════════════════════════════════════════════════
    // EVENT HANDLING
    // ═══════════════════════════════════════════════════════════════════════

    _setupEventListeners() {
        const canvas = this.canvas;

        // Remove existing listeners by swapping canvas node
        const fresh = canvas.cloneNode(true);
        canvas.parentNode.replaceChild(fresh, canvas);

        this.canvas = fresh;
        this.ctx = fresh.getContext('2d');
        const dpr = this._getEffectiveDpr();
        this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        this.drawingEngine.canvas = fresh;
        this.drawingEngine.ctx = this.ctx;

        // Drawing engine gets events first
        this.drawingEngine._bindEvents();

        // Chart-level handlers (panning/zoom/crosshair)
        fresh.addEventListener('mousemove', e => this._onMouseMove(e));
        fresh.addEventListener('mousedown', e => this._onMouseDown(e));
        fresh.addEventListener('mouseup', e => this._onMouseUp(e));
        fresh.addEventListener('mouseleave', () => this._onMouseLeave());
        // Keep drag interactions continuous even when cursor briefly leaves canvas.
        if (this._boundWindowMouseMove) {
            window.removeEventListener('mousemove', this._boundWindowMouseMove);
        }
        if (this._boundWindowMouseUp) {
            window.removeEventListener('mouseup', this._boundWindowMouseUp);
        }
        this._boundWindowMouseMove = e => this._onWindowMouseMove(e);
        this._boundWindowMouseUp = e => this._onWindowMouseUp(e);
        window.addEventListener('mousemove', this._boundWindowMouseMove);
        window.addEventListener('mouseup', this._boundWindowMouseUp);
        fresh.addEventListener('wheel', e => this._onWheel(e), { passive: false });
        fresh.addEventListener('contextmenu', e => this._onRightClick(e));
        fresh.addEventListener('dblclick', e => this._onDoubleClick(e));

        document.addEventListener('keydown', e => {
            const tag = document.activeElement?.tagName;
            if (tag === 'INPUT' || tag === 'TEXTAREA') return;
            // Ctrl/Cmd+Z,Y handled by DrawingEngine
            if ((e.ctrlKey || e.metaKey) && ['z', 'Z', 'y', 'Y'].includes(e.key)) return;
            if (e.key === 'Escape') this._clearTool?.();
            if (e.key === 'Delete') {
                const selectedId = this.drawingEngine?.selectedId;
                if (selectedId) this.drawingEngine.deleteDrawing(selectedId);
            }
            if (e.key === 'F5') this._force_refresh?.();
        });
    }

    _getEffectiveDpr() {
        const deviceDpr = window.devicePixelRatio || 1;
        return deviceDpr * this.renderQualityMultiplier;
    }

    _onMouseMove(e) {
        const engine = this.drawingEngine;
        const pos = this._mousePos(e);

        if (this.isYAxisDragging && e.buttons === 1) {
            this._applyYAxisDrag(pos.y);
            this.crosshairX = null;
            this.crosshairY = null;
            this.canvas.style.cursor = 'ns-resize';
            this.requestDraw();
            return;
        }

        // ── MEASURE: update end point while button held ───────────────────
        if (this._isMeasuring && e.buttons === 1) {
            const pos = this._mousePos(e);
            this._measureEnd = {
                x:         pos.x,
                y:         pos.y,
                price:     this._yToPrice(pos.y),
                time:      this._xToTime(pos.x),
                candleIdx: this._xToCandle(pos.x),
            };
            // Keep crosshair visible during measure
            this.crosshairX = pos.x;
            this.crosshairY = pos.y;
            this.requestDraw();
            return;
        }

        // Engine active interactions suppress chart pan/crosshair
        if (engine?.activeTool || engine?.activeHandle) {
            this.crosshairX = null;
            this.crosshairY = null;
            this.requestDraw();
            return;
        }

        const inChart = pos.x >= this.chartArea.x &&
                        pos.x <= this.chartArea.x + this.chartArea.width &&
                        pos.y >= this.chartArea.y &&
                        pos.y <= this._paneBottom();

        if (this.isDragging && e.buttons === 1) {
            const dx = pos.x - this.lastMouseX;
            const dy = pos.y - (this.lastMouseY ?? pos.y);

            // Accumulate smooth sub-pixel X shifts
            this.panOffsetPx = (this.panOffsetPx || 0) + dx;
            const slotW = this._slotW();

            if (Math.abs(this.panOffsetPx) >= slotW) {
                const shift = Math.floor(this.panOffsetPx / slotW);
                this.panOffsetPx -= shift * slotW;

                const vis = this.visibleCandleCount;
                let newStart = this.viewPortStart - shift;
                let newEnd = newStart + vis - 1;

                const maxEnd = this.data.length - 1 + this.rightBufferCandles;

                if (newStart < 0) {
                    newStart = 0;
                    newEnd = newStart + vis - 1;
                } else if (newEnd > maxEnd) {
                    newEnd = maxEnd;
                    newStart = newEnd - vis + 1;
                }

                this.viewPortStart = newStart;
                this.viewPortEnd = newEnd;
                this.updateSlider();
                if (engine) engine.rebuildSpatialHash();
            }

            // Hard boundaries to stop panning past edges
            const maxEnd = this.data.length - 1 + this.rightBufferCandles;
            if (this.viewPortStart <= 0 && this.panOffsetPx > 0) {
                if (!this._olderDataRequestPending && this.chartBridge && typeof this.chartBridge.notify_older_data_requested === 'function') {
                    this._olderDataRequestPending = true;
                    try { this.chartBridge.notify_older_data_requested(); } catch (err) { console.error("notify_older_data_requested error:", err); }
                    setTimeout(() => { this._olderDataRequestPending = false; }, 1200);
                }
                this.panOffsetPx = 0;
            }
            if (this.viewPortEnd >= maxEnd && this.panOffsetPx < 0) this.panOffsetPx = 0;

            // Automatically unlock Auto-Scale on intentional vertical drag.
            // A tiny threshold prevents accidental unlocks on pure horizontal pan.
            if (Math.abs(dy) > 2 && !this.isUserYRange) {
                this.isUserYRange = true;
            }

            // Pan Y smoothly if user has un-locked the auto-scale
            if (this.isUserYRange && dy !== 0) {
                const priceShift = (dy / this.chartArea.height) * (this.maxPrice - this.minPrice);
                this.minPrice += priceShift;
                this.maxPrice += priceShift;
            }

            this.lastMouseX = pos.x;
            this.lastMouseY = pos.y;
            if (!this.isUserYRange) this.calculateBounds();

            this.crosshairX = null;
            this.crosshairY = null;
            this.requestDraw();
            return;
        } else if (inChart) {
            this.crosshairX = pos.x;
            const candleIndex   = this._xToCandle(pos.x);
            const isInPricePane = pos.y >= this.chartArea.y &&
                                  pos.y <= this.chartArea.y + this.chartArea.height;
            this.crosshairY = isInPricePane
                ? this._snapCrosshairY(pos.y, candleIndex)
                : pos.y;
            this._updateCandleDetail(pos.x);
        } else {
            this.crosshairX = null;
            this.crosshairY = null;
            this._displayLatestCandleDetails();
        }


        if (this._inPriceAxis(pos.x, pos.y) && !this.isDragging) {
            this.canvas.style.cursor = 'ns-resize';
        } else if (!this.isDragging && !this.drawingEngine?.activeTool) {
            this.canvas.style.cursor = 'default';
        }

        this.lastMouseX = pos.x;
        this.requestDraw();
    }

    _onMouseDown(e) {
        if (e.button !== 0) return;
        const pos = this._mousePos(e);

        if (this._inPriceAxis(pos.x, pos.y)) {
            const range = this.maxPrice - this.minPrice;
            if (range > 0) {
                this.isYAxisDragging = true;
                this.yAxisDragStartY = pos.y;
                this.yAxisDragStartMin = this.minPrice;
                this.yAxisDragStartMax = this.maxPrice;
                this.yAxisDragAnchorRatio = this._clamp01((this.chartArea.y + this.chartArea.height - pos.y) / this.chartArea.height);
                this.isUserYRange = true;
                this.canvas.style.cursor = 'ns-resize';
            }
            return;
        }

        // ── MEASURE: completely bypass DrawingEngine ──────────────────────
        if (this.currentTool === 'measure') {
            // Ensure DrawingEngine has no active tool so it won't interfere
            if (this.drawingEngine) this.drawingEngine.activeTool = null;

            if (this._inChartArea_check(pos.x, pos.y)) {
                const ci = this._xToCandle(pos.x);
                this._measureStart = {
                    x:         pos.x,
                    y:         pos.y,
                    price:     this._yToPrice(pos.y),
                    time:      this._xToTime(pos.x),
                    candleIdx: ci,
                };
                this._measureEnd  = { ...this._measureStart };
                this._isMeasuring = true;
                this.canvas.style.cursor = 'crosshair';
                this.requestDraw();
            }
            return; // never fall through to pan logic
        }

        // Engine already handled draw/selection; only pan if empty space
        if (!this.drawingEngine?.activeTool &&
            !this.drawingEngine?.activeHandle &&
            !this.drawingEngine?.hoverId) {
            this.isDragging = true;
            this.lastMouseX = pos.x;
            this.lastMouseY = pos.y;
            this.canvas.style.cursor = 'grabbing';
        }
    }

    _onMouseUp(e) {
        if (e.button !== 0) return;

        if (this.isYAxisDragging) {
            this.isYAxisDragging = false;
            this.canvas.style.cursor = this.drawingEngine?.activeTool ? 'crosshair' : 'default';
            return;
        }

        // ── MEASURE: clear on release ─────────────────────────────────────
        if (this._isMeasuring) {
            this._isMeasuring  = false;
            this._measureStart = null;
            this._measureEnd   = null;
            this.crosshairX    = null;
            this.crosshairY    = null;

            // Measure behaves as a "hold + drag" action:
            // once mouse is released, consume the tool and return to default mode.
            if (this.currentTool === 'measure') {
                this._clearTool();
            } else {
                this.canvas.style.cursor = 'default';
            }
            this.requestDraw();
            return;
        }

        this.isDragging = false;
        this.canvas.style.cursor = this.drawingEngine?.activeTool ? 'crosshair' : 'default';
    }

    _onWindowMouseMove(e) {
        if (!this.isDragging && !this.isYAxisDragging && !this._isMeasuring) return;
        this._onMouseMove(e);
    }

    _onWindowMouseUp(e) {
        if (!this.isDragging && !this.isYAxisDragging && !this._isMeasuring) return;
        this._onMouseUp(e);
    }

    _onMouseLeave() {
        // When actively dragging, preserve state so outside-canvas mouse events
        // can continue the interaction and avoid stuttery "drop/regrab" behavior.
        if (this.isDragging || this.isYAxisDragging || this._isMeasuring) return;
        this.crosshairX = null;
        this.crosshairY = null;
        this._displayLatestCandleDetails();
        this.requestDraw();
    }

    _onWheel(e) {
        e.preventDefault();

        if (e.shiftKey) {
            const delta = e.deltaY || e.deltaX;
            const direction = delta < 0 ? 1 : -1;
            if (this.chartBridge && (direction === 1 || direction === -1)) {
                try { this.chartBridge.notify_timeframe_step_requested(direction); }
                catch (err) { console.error("notify_timeframe_step_requested error:", err); }
            }
            return;
        }

        const delta  = e.deltaY || e.deltaX;
        const zoomIn = delta < 0;
        const pos = this._mousePos(e);

        if (this._inPriceAxis(pos.x, pos.y)) {
            this.isUserYRange = true;
            this._zoomPriceRange(pos.y, zoomIn ? 0.92 : 1.08);
            this.requestDraw();
            return;
        }

        // ── Fixed-width zoom model ──────────────────────────────────────────
        // Zoom = change candleWidth in px. visibleCount adjusts automatically.
        // Smooth multiplicative step; clamp to [2, 60] px.
        const factor  = zoomIn ? 1.10 : 0.91;
        const newW    = Math.max(2, Math.min(60, this.candleWidth * factor));
        if (Math.abs(newW - this.candleWidth) < 0.05) return;

        // Anchor the candle under the mouse so it stays in place after zoom.
        const anchorCandle = this._xToCandle(pos.x);   // index before resize
        const anchorFrac   = (pos.x - this.chartArea.x) / this.chartArea.width;

        this.candleWidth = newW;
        this.panOffsetPx = 0;

        // Recompute how many candles now fit, then position viewport so that
        // anchorCandle stays at anchorFrac of the chart width.
        const vis      = Math.max(1, Math.floor(this.chartArea.width / this._slotW()));
        const newStart = Math.round(anchorCandle - anchorFrac * vis);
        this.viewPortStart      = Math.max(0, Math.min(newStart,
                                    this.data.length + this.rightBufferCandles - vis));
        this.viewPortEnd        = this.viewPortStart + vis - 1;
        this.visibleCandleCount = vis;

        this.calculateBounds();
        this.requestDraw();
        this.updateSlider();

        clearTimeout(this._zoomTimer);
        this._zoomTimer = setTimeout(() => this._notifyZoomChange(), 300);
    }

    _onRightClick(e) {
        const pos = this._mousePos(e);
        const hit = this.drawingEngine._hitTest(pos.x, pos.y);
        if (hit) return; // DrawingEngine handles drawing context menu
        e.preventDefault();
        const price = this._yToPrice(pos.y);
        this._showContextMenu(e.clientX, e.clientY, price);
    }

    _finalizeDrawing(pos) {
        if (!this.startPoint) return;
        const commonProps = { id: Date.now() + Math.random(), color: this.drawingColor,
                              lineWidth: this.lineWidth, timestamp: Date.now() };

        if (this.currentTool === 'horizontal_line') {
            this.drawings.horizontal_lines.push({ ...commonProps, type: 'horizontal_line', price: this.startPoint.price });

        } else if (this.currentTool === 'horizontal_ray') {
            this.drawings.horizontal_rays.push({ ...commonProps, type: 'horizontal_ray',
                startTime: this.startPoint.time, startPrice: this.startPoint.price });

        } else if (this.currentTool === 'note') {
            // Delegate to Python via bridge
            if (this.chartBridge) {
                this.chartBridge.notify_text_note_requested(
                    JSON.stringify({ x: pos.x, y: pos.y })
                );
            }

        } else {
            const d = { ...commonProps,
                startTime: this.startPoint.time, startPrice: this.startPoint.price,
                endTime: this._xToTime(pos.x),   endPrice: this._yToPrice(pos.y) };

            if (this.currentTool === 'line')       this.drawings.lines.push({ ...d, type: 'line' });
            else if (this.currentTool === 'rectangle') this.drawings.rectangles.push({ ...d, type: 'rectangle' });
            else if (this.currentTool === 'arrow_line') this.drawings.arrow_lines.push({ ...d, type: 'arrow_line' });
            else if (this.currentTool === 'fibonacci')  this.drawings.fibonacci.push({ ...d, type: 'fibonacci' });
        }

        this.isDrawing  = false;
        this.startPoint = null;
        this.endPoint   = null;
        this._clearTool();
        this._notifyDrawingsChange();
        this.requestDraw();
    }

    // ═══════════════════════════════════════════════════════════════════════
    // CONTEXT MENU
    // ═══════════════════════════════════════════════════════════════════════

    _showContextMenu(clientX, clientY, priceLevel) {
        this._removeContextMenu();
        const ltp   = this.livePrice || (this.data.length > 0 ? this.data[this.data.length - 1].close : priceLevel);
        const isAbove   = priceLevel > ltp;
        const diff      = Math.abs(priceLevel - ltp);
        const diffPct   = ltp > 0 ? ((diff / ltp) * 100).toFixed(2) : '0.00';
        const sym       = this.currentSymbol || 'SYMBOL';

        const items = [
            { text: `Alert at ₹${priceLevel.toFixed(2)}`, icon: '🔔', highlight: true,
              sub: `${isAbove ? 'Above' : 'Below'} LTP by ${diffPct}%`,
              action: () => this._createAlert(sym, priceLevel) },
            { divider: true },
            { text: isAbove ? '📈 Buy Entry Alert' : '📉 Short Entry Alert',
              sub: isAbove ? 'Breakout signal' : 'Breakdown signal',
              action: () => this._createAlert(sym, priceLevel, isAbove ? 'buy_entry' : 'sell_entry') },
            { text: isAbove ? '👁 Resistance Watch' : '👁 Support Watch',
              sub: isAbove ? 'Monitor resistance' : 'Monitor support',
              action: () => this._createAlert(sym, priceLevel, isAbove ? 'resistance' : 'support') },
            { divider: true },
            { text: '💰 Place Limit Order Here', sub: 'Open order ticket at this exact level',
              action: () => this._placeOrderAtPrice(sym, priceLevel) },
        ];

        const menu = document.createElement('div');
        menu.style.cssText = `
            position: fixed; left: ${clientX}px; top: ${clientY}px;
            background: #0A0D12; border: 1px solid #1A2030;
            border-radius: 2px; padding: 4px 0; z-index: 99999;
            box-shadow: none;
            font-family: "Inter", "Aptos", "Segoe UI", "Roboto", sans-serif; font-size: 11px;
            color: #A8BCD4; min-width: 190px; user-select: none;`;

        items.forEach(item => {
            if (item.divider) {
                const d = document.createElement('div');
                d.style.cssText = 'height:1px; background:#1A2030; margin:3px 8px;';
                menu.appendChild(d); return;
            }
            const mi = document.createElement('div');
            mi.style.cssText = `
                padding: 7px 16px; cursor: pointer;
                ${item.highlight ? 'background:rgba(26,40,64,0.62);' : ''}`;

            mi.innerHTML = `
                <div style="font-weight:${item.highlight ? '600' : '500'};
                     color:${item.highlight ? '#E8F0FF' : '#A8BCD4'};">${item.text}</div>
                ${item.sub ? `<div style="font-size:10px;color:#5A7090;margin-top:2px;">${item.sub}</div>` : ''}`;

            mi.addEventListener('mouseenter', () => mi.style.background = item.highlight ? 'rgba(26,40,64,0.75)' : '#141920');
            mi.addEventListener('mouseleave', () => mi.style.background = item.highlight ? 'rgba(26,40,64,0.62)' : 'transparent');
            mi.addEventListener('click', e => { e.stopPropagation(); item.action(); this._removeContextMenu(); });
            menu.appendChild(mi);
        });

        document.body.appendChild(menu);
        // Viewport overflow fix
        const mr = menu.getBoundingClientRect();
        if (mr.right  > window.innerWidth)  menu.style.left = `${clientX - mr.width}px`;
        if (mr.bottom > window.innerHeight) menu.style.top  = `${clientY - mr.height}px`;

        this.activeContextMenu = menu;
        setTimeout(() => {
            document.addEventListener('click', function close(e) {
                if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('click', close); }
            });
        }, 50);
    }

    _removeContextMenu() {
        if (this.activeContextMenu) { this.activeContextMenu.remove(); this.activeContextMenu = null; }
    }

    // ═══════════════════════════════════════════════════════════════════════
    // QUICK DRAWING HELPERS  (called from context menu)
    // ═══════════════════════════════════════════════════════════════════════

    _addHLine(price) {
        this.drawings.horizontal_lines.push({
            id: Date.now() + Math.random(), type: 'horizontal_line',
            price, color: '#B88732', lineWidth: 1.5, style: 'solid',
            label: `₹${price.toFixed(2)}`
        });
        this.requestDraw(); this._notifyDrawingsChange();
    }

    _addNamedHLine(price, name, color) {
        this.drawings.horizontal_lines.push({
            id: Date.now() + Math.random(), type: 'horizontal_line',
            price, color, lineWidth: 1.5, style: 'solid',
            label: `${name}: ₹${price.toFixed(2)}`
        });
        this.requestDraw(); this._notifyDrawingsChange();
    }

    _createAlert(symbol, price, alertType = 'price') {
        const hasLtp = Number.isFinite(this.livePrice) && this.livePrice > 0;
        const inferredCondition = hasLtp
            ? (price >= this.livePrice ? 'crosses_above' : 'crosses_below')
            : 'price_above';
        const payload = JSON.stringify({
            symbol,
            price,
            type: alertType,
            condition: inferredCondition
        });
        if (this.chartBridge) this.chartBridge.notify_alert_creation_requested(payload);
    }

    _placeOrderAtPrice(symbol, price) {
        const payload = JSON.stringify({
            symbol,
            price,
            ltp: this.livePrice,
            order_type: 'LIMIT',
            transaction_type: 'BUY'
        });
        if (this.chartBridge) this.chartBridge.notify_order_dialog_requested(payload);
    }

    // ═══════════════════════════════════════════════════════════════════════
    // SLIDER
    // ═══════════════════════════════════════════════════════════════════════

    _setupSlider() {
        const trySetup = () => {
            this.slider      = document.getElementById('timeSlider');
            this.sliderTrack = document.getElementById('sliderTrack');
            this.sliderThumb = document.getElementById('sliderThumb');
            if (!this.slider || !this.sliderThumb || !this.sliderTrack) {
                setTimeout(trySetup, 100); return;
            }
            this._bindSlider();
            this._syncSliderVisibility();
        };
        trySetup();
    }

    _bindSlider() {
        let dragging = false, startX = 0, startLeft = 0;

        this.sliderThumb.addEventListener('mousedown', e => {
            dragging = true; startX = e.clientX;
            startLeft = parseFloat(this.sliderThumb.style.left) || 0;
            e.preventDefault();
        });

        document.addEventListener('mousemove', e => {
            if (!dragging) return;
            const trackW = this.sliderTrack.clientWidth;
            const thumbW = this.sliderThumb.clientWidth;
            const maxLeft = trackW - thumbW;
            const newLeft = Math.max(0, Math.min(maxLeft, startLeft + (e.clientX - startX)));
            const ratio   = trackW > thumbW ? newLeft / (trackW - thumbW) : 0;
            this.sliderThumb.style.left = newLeft + 'px';

            const total   = this.data.length + this.rightBufferCandles;
            const visCount = this.viewPortEnd - this.viewPortStart + 1;
            const maxStart = Math.max(0, total - visCount);
            this.viewPortStart = Math.round(ratio * maxStart);
            this.viewPortEnd   = this.viewPortStart + visCount - 1;
            this.panOffsetPx   = 0;
            this.calculateBounds();
            this.requestDraw();
        });

        document.addEventListener('mouseup', () => { dragging = false; });
    }

    _syncSliderVisibility() {
        if (!this.slider || !this.canvas) return;
        this.slider.style.display = this.showTimeSlider ? "flex" : "none";
        this.canvas.style.height = this.showTimeSlider ? "calc(100% - 14px)" : "100%";
    }

    updateSlider() {
        if (!this.showTimeSlider || !this.sliderThumb || !this.sliderTrack) return;
        const total  = Math.max(1, this.data.length + this.rightBufferCandles);
        const vis    = this.viewPortEnd - this.viewPortStart + 1;
        const thumbW = Math.max(40, Math.round((vis / total) * this.sliderTrack.clientWidth));
        const maxStart = Math.max(0, total - vis);
        const ratio  = maxStart > 0 ? this.viewPortStart / maxStart : 1;
        const trackW = this.sliderTrack.clientWidth;
        const maxLeft= Math.max(0, trackW - thumbW);
        this.sliderThumb.style.width = thumbW + 'px';
        this.sliderThumb.style.left  = Math.round(ratio * maxLeft) + 'px';
    }

    // ═══════════════════════════════════════════════════════════════════════
    // WEBCHANNEL
    // ═══════════════════════════════════════════════════════════════════════

    _setupWebChannel() {
        const tryInit = () => {
            if (typeof QWebChannel !== 'undefined' && window.qt?.webChannelTransport) {
                new QWebChannel(qt.webChannelTransport, channel => {
                    if (channel.objects?.chartBridge) {
                        this.chartBridge = channel.objects.chartBridge;
                        if (this.drawingEngine) this.drawingEngine.chartBridge = this.chartBridge;
                        this.webChannelInitialized = true;
                        setTimeout(() => {
                            try { this.chartBridge.set_web_channel_initialized(); } catch (e) { console.error(e); }
                        }, 400);
                        this._flushNotifyQueue();
                    } else setTimeout(tryInit, 500);
                });
            } else setTimeout(tryInit, 200);
        };
        tryInit();
    }

    // ═══════════════════════════════════════════════════════════════════════
    // BOUNDS
    // ═══════════════════════════════════════════════════════════════════════

    calculateBounds() {
        if (this.data.length === 0) return;
        const lockedMin = this.isUserYRange ? this.minPrice : null;
        const lockedMax = this.isUserYRange ? this.maxPrice : null;

        const series = this._getPriceSeriesForRendering();
        const start = Math.max(0, this.viewPortStart);
        const end   = Math.min(series.length - 1, this.viewPortEnd);
        if (end < start) return;

        let minPrice = Number.POSITIVE_INFINITY;
        let maxPrice = Number.NEGATIVE_INFINITY;

        for (let i = start; i <= end; i += 1) {
            const d = series[i];
            if (!d) continue;
            if (d.low < minPrice) minPrice = d.low;
            if (d.high > maxPrice) maxPrice = d.high;
        }

        if (!Number.isFinite(minPrice) || !Number.isFinite(maxPrice)) return;

        this.minPrice = minPrice;
        this.maxPrice = maxPrice;

        // Include EMA values in price range
        const firstT = series[start]?.time;
        const lastT  = series[end]?.time;
        for (const emaList of Object.values(this.emaData)) {
            for (const item of emaList) {
                if (item.time >= firstT && item.time <= lastT) {
                    this.minPrice = Math.min(this.minPrice, item.value);
                    this.maxPrice = Math.max(this.maxPrice, item.value);
                }
            }
        }

        // Include live price
        if (this.livePrice !== null && !this._isHeikinAshiMode()) {
            this.minPrice = Math.min(this.minPrice, this.livePrice);
            this.maxPrice = Math.max(this.maxPrice, this.livePrice);
        }

        // Keep auto-scaled price action in the middle 60% of the pane so the
        // top/bottom 20% stays visually clear (helps avoid overlap with volume bars).
        const range = this.maxPrice - this.minPrice;
        if (range === 0) {
            this.minPrice -= 1;
            this.maxPrice += 1;
        } else {
            const usableBand = 0.60;
            const edgePad = 0.20;
            const expandedRange = range / usableBand;
            this.minPrice -= expandedRange * edgePad;
            this.maxPrice += expandedRange * edgePad;
        }

        if (this.isUserYRange && Number.isFinite(lockedMin) && Number.isFinite(lockedMax) && lockedMax > lockedMin) {
            this.minPrice = lockedMin;
            this.maxPrice = lockedMax;
        }

        const prevAxisWidth = this.rightAxisWidth || 0;
        this._updateChartAreas();

        // One more pass when width changes because candle spacing alters bounds slightly.
        if (Math.abs((this.rightAxisWidth || 0) - prevAxisWidth) > 0.5) {
            this._updateChartAreas();
        }
    }


    _inPriceAxis(x, y) {
        const axisX = this.chartArea.x + this.chartArea.width;
        const axisY1 = this.chartArea.y;
        const axisY2 = this.chartArea.y + this.chartArea.height;
        return x >= axisX && x <= axisX + (this.rightAxisWidth || 0) && y >= axisY1 && y <= axisY2;
    }

    _clamp01(v) {
        return Math.max(0, Math.min(1, v));
    }

    _applyYAxisDrag(mouseY) {
        const dy = mouseY - this.yAxisDragStartY;
        const startRange = Math.max(1e-8, this.yAxisDragStartMax - this.yAxisDragStartMin);
        const scale = Math.exp(dy * 0.01);
        const newRange = Math.max(1e-8, Math.min(startRange * 25, startRange * scale));
        const anchorPrice = this.yAxisDragStartMin + this.yAxisDragAnchorRatio * startRange;
        this.minPrice = anchorPrice - this.yAxisDragAnchorRatio * newRange;
        this.maxPrice = this.minPrice + newRange;
    }

    _zoomPriceRange(anchorY, factor) {
        const oldRange = Math.max(1e-8, this.maxPrice - this.minPrice);
        const anchorRatio = this._clamp01((this.chartArea.y + this.chartArea.height - anchorY) / this.chartArea.height);
        const anchorPrice = this.minPrice + anchorRatio * oldRange;
        const newRange = Math.max(1e-8, oldRange * factor);
        this.minPrice = anchorPrice - anchorRatio * newRange;
        this.maxPrice = this.minPrice + newRange;
    }

    _onDoubleClick(e) {
        const pos = this._mousePos(e);
        if (!this._inPriceAxis(pos.x, pos.y)) return;
        this.isUserYRange = false;
        this.calculateBounds();
        this.requestDraw();
    }



    // ═══════════════════════════════════════════════════════════════════════
    // COORDINATE TRANSFORMS
    // ═══════════════════════════════════════════════════════════════════════

    // Returns the bottom pixel of the lowest visible pane.
    // Priority: CVD → Volume → Price (chart area itself as fallback).
    _paneBottom() {
        return this.chartArea.y + this.chartArea.height;
    }

    _priceToY(price) {
        const ratio = (price - this.minPrice) / (this.maxPrice - this.minPrice);
        return this.chartArea.y + this.chartArea.height - ratio * this.chartArea.height;
    }

    _yToPrice(y) {
        const ratio = (this.chartArea.y + this.chartArea.height - y) / this.chartArea.height;
        return this.minPrice + ratio * (this.maxPrice - this.minPrice);
    }

    _candleToX(index) {
        // Fixed slot-width model: each candle occupies exactly _slotW() px.
        return this.chartArea.x + (index - this.viewPortStart) * this._slotW() + (this.panOffsetPx || 0);
    }

    _xToCandle(x) {
        const slotW = this._slotW();
        if (slotW <= 0) return -1;
        return this.viewPortStart + Math.floor((x - this.chartArea.x - (this.panOffsetPx || 0)) / slotW);
    }

    _xToCandle_coord(x) {
        const slotW = this._slotW();
        if (slotW <= 0) return -1;
        const idx = this.viewPortStart + Math.floor((x - this.chartArea.x) / slotW);
        return Math.min(idx, this._maxFutureCandleIndex());
    }

    _maxFutureCandleIndex() {
        return Math.max(0, this.data.length - 1 + this.rightBufferCandles);
    }

    _averageCandleTimeSpan() {
        if (this.data.length < 2) return 24 * 60 * 60 * 1000;
        const first = this.data[0].time;
        const last  = this.data[this.data.length - 1].time;
        return Math.max(1, (last - first) / Math.max(1, this.data.length - 1));
    }

    _candleIndexToTime(idx) {
        if (this.data.length === 0) return Date.now();
        if (idx >= 0 && idx < this.data.length) return this.data[idx].time;

        const first = this.data[0].time;
        const lastIndex = this.data.length - 1;
        const last = this.data[lastIndex].time;
        const avg = this._averageCandleTimeSpan();
        const clampedIdx = Math.min(idx, this._maxFutureCandleIndex());
        if (clampedIdx >= this.data.length) return last + avg * (clampedIdx - lastIndex);
        return first;
    }

    _xToTime_coord(x) {
        return this._candleIndexToTime(this._xToCandle_coord(x));
    }

    _timeToX(time) {
        let idx = this.data.findIndex(d => d.time >= time);
        if (idx === -1) {
            const last = this.data.length - 1;
            if (last < 0) return this.chartArea.x;
            const avg = this._averageCandleTimeSpan();
            const offset = Math.round((time - this.data[last].time) / avg);
            idx = Math.min(this._maxFutureCandleIndex(), last + Math.max(0, offset));
        }
        if (idx === 0 && time < this.data[0].time) return this.chartArea.x;
        return this._candleToX(idx);
    }

    _xToTime(x) {
        return this._candleIndexToTime(this._xToCandle(x));
    }

    _mousePos(e) {
        const r = this.canvas.getBoundingClientRect();
        return { x: e.clientX - r.left, y: e.clientY - r.top };
    }

    _inChartArea_check(x, y) {
        return x >= this.chartArea.x &&
               x <= this.chartArea.x + this.chartArea.width &&
               y >= this.chartArea.y &&
               y <= this.chartArea.y + this.chartArea.height;
    }

    // ═══════════════════════════════════════════════════════════════════════
    // VISIBILITY TESTS
    // ═══════════════════════════════════════════════════════════════════════

    _lineVisible(x1, y1, x2, y2) {
        const c = this.chartArea;
        return !((x1 < c.x && x2 < c.x) || (x1 > c.x + c.width  && x2 > c.x + c.width)  ||
                 (y1 < c.y && y2 < c.y) || (y1 > c.y + c.height && y2 > c.y + c.height));
    }

    _rectVisible(x, y, w, h) {
        const c = this.chartArea;
        return x + w >= c.x && x <= c.x + c.width && y + h >= c.y && y <= c.y + c.height;
    }

    _ptVisible(x, y) {
        const c = this.chartArea;
        return x >= c.x && x <= c.x + c.width && y >= c.y && y <= c.y + c.height;
    }

    // ═══════════════════════════════════════════════════════════════════════
    // HIT TEST (for selecting drawings)
    // ═══════════════════════════════════════════════════════════════════════

    _hitTest(pos) {
        const tol = 6;
        for (const line of this.drawings.lines) {
            const sx = this._timeToX(line.startTime), sy = this._priceToY(line.startPrice);
            const ex = this._timeToX(line.endTime),   ey = this._priceToY(line.endPrice);
            if (this._nearLine(pos.x, pos.y, sx, sy, ex, ey, tol)) return line;
        }
        for (const hl of this.drawings.horizontal_lines) {
            if (Math.abs(pos.y - this._priceToY(hl.price)) <= tol) return hl;
        }
        for (const hr of this.drawings.horizontal_rays) {
            const sx = this._timeToX(hr.startTime), y = this._priceToY(hr.startPrice);
            if (Math.abs(pos.y - y) <= tol && pos.x >= sx - tol) return hr;
        }
        for (const arrow of this.drawings.arrow_lines) {
            const sx = this._timeToX(arrow.startTime), sy = this._priceToY(arrow.startPrice);
            const ex = this._timeToX(arrow.endTime),   ey = this._priceToY(arrow.endPrice);
            if (this._nearLine(pos.x, pos.y, sx, sy, ex, ey, tol)) return arrow;
        }
        for (const rect of this.drawings.rectangles) {
            const sx = this._timeToX(rect.startTime), sy = this._priceToY(rect.startPrice);
            const ex = this._timeToX(rect.endTime),   ey = this._priceToY(rect.endPrice);
            const x = Math.min(sx,ex), y = Math.min(sy,ey), w = Math.abs(ex-sx), h = Math.abs(ey-sy);
            if (pos.x>=x-tol && pos.x<=x+w+tol && pos.y>=y-tol && pos.y<=y+h+tol) return rect;
        }
        for (const fib of this.drawings.fibonacci) {
            const sx = this._timeToX(fib.startTime), sy = this._priceToY(fib.startPrice);
            const ex = this._timeToX(fib.endTime),   ey = this._priceToY(fib.endPrice);
            if (this._nearLine(pos.x, pos.y, sx, sy, ex, ey, tol)) return fib;
        }
        for (const note of this.drawings.notes) {
            const nx = this._timeToX(note.time), ny = this._priceToY(note.price);
            if (Math.abs(pos.x - nx) <= tol && Math.abs(pos.y - ny) <= tol) return note;
        }
        return null;
    }

    _shiftDrawing(drawing, dx, dy) {
        const toShiftedTime = time => this._xToTime(this._timeToX(time) + dx);
        const toShiftedPrice = price => this._yToPrice(this._priceToY(price) + dy);

        if ('startTime' in drawing) drawing.startTime = toShiftedTime(drawing.startTime);
        if ('endTime' in drawing) drawing.endTime = toShiftedTime(drawing.endTime);
        if ('time' in drawing) drawing.time = toShiftedTime(drawing.time);

        if ('startPrice' in drawing) drawing.startPrice = toShiftedPrice(drawing.startPrice);
        if ('endPrice' in drawing) drawing.endPrice = toShiftedPrice(drawing.endPrice);
        if ('price' in drawing) drawing.price = toShiftedPrice(drawing.price);
    }

    _nearLine(px, py, x1, y1, x2, y2, tol) {
        const dx = x2-x1, dy = y2-y1, lenSq = dx*dx+dy*dy;
        const t  = lenSq === 0 ? -1 : Math.max(0, Math.min(1, ((px-x1)*dx+(py-y1)*dy)/lenSq));
        const projX = x1+t*dx, projY = y1+t*dy;
        return (px-projX)**2 + (py-projY)**2 < tol*tol;
    }

    // ═══════════════════════════════════════════════════════════════════════
    // DISPLAY HELPERS
    // ═══════════════════════════════════════════════════════════════════════


    _renderPriceInfo(c, dateStr, candleIndex = -1) {
        const el = document.getElementById('metricsInfo');
        if (!el) return;

        const prevClose = Number(c.prevClose || c.previousClose || c.open || 0);
        const dayChange = c.close - prevClose;
        const dayPct = prevClose !== 0 ? ((dayChange / prevClose) * 100) : 0;
        const dayColor = dayChange >= 0 ? '#00D4A8' : '#FF4D6A';
        const daySign = dayChange >= 0 ? '+' : '';
        const volume = Number(c?.volume) || 0;

        const sep = '<span style="color:#2A3A50;margin:0 5px;">•</span>';
        const dot = '<span style="color:#2A3A50;margin:0 5px;">•</span>';
        const adrPercent = Number(this.currentADR?.percent ?? 0);
        const adrPctColor = adrPercent > 4 ? '#00D4A8' : (adrPercent >= 2 ? '#E8F0FF' : '#FF4D6A');
        const adrStr = this.currentADR?.value > 0
            ? `<span style="color:#A8BCD4;">ADR</span><span style="color:#E8F0FF;margin-left:2px;">₹${this.currentADR.value.toFixed(2)}</span><span style="color:${adrPctColor};margin-left:3px;font-weight:700;">(${adrPercent.toFixed(2)}%)</span>`
            : '<span style="color:#5A7090;">ADR N/A</span>';
        const perfLabels = ['Monthly','3M','6M','1Y'];
        const perfToggles = ['show_perf_monthly','show_perf_3m','show_perf_6m','show_perf_1y'];
        const perf = perfLabels.map((p, i) => {
            if (!this.infoVisibility?.[perfToggles[i]]) return null;
            const v = this.percentageChanges?.[p];
            if (v == null) return `<span style="color:#5A7090;">${p} N/A</span>`;
            const valCol = v >= 0 ? '#00D4A8' : '#FF4D6A';
            return `<span style="color:#A8BCD4;">${p}</span><span style="color:${valCol};margin-left:3px;font-weight:600;">${v >= 0 ? '+' : ''}${v.toFixed(2)}%</span>`;
        }).filter(Boolean).join(dot);

        const metricsItems = [];
        if (this.infoVisibility?.show_adr) metricsItems.push(adrStr);
        if (perf) metricsItems.push(perf);
        const metricsRow = metricsItems.join(sep);

        const priceItems = [];
        if (this.infoVisibility?.show_info_date) priceItems.push(`<span style="color:#5A7090;">${dateStr}</span>`);
        if (this.infoVisibility?.show_info_open) priceItems.push(`<span style="color:#A8BCD4;">O</span><span style="color:#E8F0FF;margin-left:3px;">₹${c.open.toFixed(2)}</span>`);
        if (this.infoVisibility?.show_info_high) priceItems.push(`<span style="color:#A8BCD4;">H</span><span style="color:#E8F0FF;margin-left:3px;">₹${c.high.toFixed(2)}</span>`);
        if (this.infoVisibility?.show_info_low) priceItems.push(`<span style="color:#A8BCD4;">L</span><span style="color:#E8F0FF;margin-left:3px;">₹${c.low.toFixed(2)}</span>`);
        if (this.infoVisibility?.show_info_close) priceItems.push(`<span style="color:#A8BCD4;">C</span><span style="color:#E8F0FF;margin-left:3px;">₹${c.close.toFixed(2)}</span>`);
        if (this.infoVisibility?.show_info_pct_change) priceItems.push(`<span style="color:${dayColor};font-weight:700;">Chg ${daySign}₹${dayChange.toFixed(2)} (${daySign}${dayPct.toFixed(2)}%)</span>`);
        if (this.infoVisibility?.show_info_volume) priceItems.push(`<span style="color:#A8BCD4;">Vol</span><span style="color:#E8F0FF;margin-left:3px;">${Math.round(volume).toLocaleString('en-IN')}</span>`);
        const priceRow = priceItems.join(sep);

        el.innerHTML = `<div class="info-row metrics-row">${metricsRow}</div><div class="info-row price-row">${priceRow}</div>`;
    }

    _updateCandleDetail(x) {
        const idx = this._xToCandle(x);
        if (idx < 0 || idx >= this.data.length) { this._displayLatestCandleDetails(); return; }
        if (idx === this._lastInfoCandleIndex) return;
        const c = this.data[idx];
        this._lastInfoCandleIndex = idx;
        this._renderPriceInfo(c, this._fmtTimeLabel(c.time), idx);
    }

    _displayLatestCandleDetails() {
        const el = document.getElementById('metricsInfo');
        if (!el) return;
        if (this.data.length === 0) { el.textContent = 'No data'; return; }
        const idx = this.data.length - 1;
        const c = this.data[idx];
        const dateStr = this._fmtDateLabel(c.time, true);
        this._lastInfoCandleIndex = idx;
        this._renderPriceInfo(c, dateStr, idx);
    }

    _updateMetricsDisplay() {
        this._displayLatestCandleDetails();
    }

    // ═══════════════════════════════════════════════════════════════════════
    // PUBLIC API  (called from Python via runJavaScript)
    // ═══════════════════════════════════════════════════════════════════════

    setDrawingTool(toolId, active, color, lw) {
        if (!active) { this._clearTool(); return; }
        this.currentTool  = toolId;
        this.drawingColor = color || this.drawingColor;
        this.lineWidth    = lw    || this.lineWidth;
        if (this.drawingEngine) {
            this.drawingEngine.setTool(toolId, this.drawingColor, this.lineWidth);
        }
        this.canvas.style.cursor = 'crosshair';
    }

    _clearTool() {
        const hadActiveTool = Boolean(this.currentTool);
        this.currentTool = null;
        this.isDrawing   = false;
        this.startPoint  = null;
        this.endPoint    = null;
        const clearedViaDrawingEngine = Boolean(this.drawingEngine);
        if (clearedViaDrawingEngine) {
            this.drawingEngine.clearTool();
        }
        this.canvas.style.cursor = 'default';
        if (hadActiveTool && !clearedViaDrawingEngine) {
            this._notifyDrawingToolCleared();
        }
    }

    _intervalToMs(interval) {
        const key = String(interval || 'day').toLowerCase();
        const minutesMap = {
            minute: 1,
            '1minute': 1,
            '3minute': 3,
            '5minute': 5,
            '10minute': 10,
            '15minute': 15,
            '30minute': 30,
            '60minute': 60,
        };
        if (minutesMap[key]) return minutesMap[key] * 60 * 1000;
        if (key === 'day') return 24 * 60 * 60 * 1000;
        if (key === 'week') return 7 * 24 * 60 * 60 * 1000;
        if (key === 'month') return 30 * 24 * 60 * 60 * 1000;
        return 0;
    }

    _bucketStartMs(epochMs, intervalMs) {
        if (!Number.isFinite(epochMs) || !Number.isFinite(intervalMs) || intervalMs <= 0) {
            return epochMs;
        }

        // Daily candles are calendar bars, like TradingView/lightweight-charts
        // BusinessDay data.  Keep them on UTC midnight for the exchange trading
        // date instead of IST midnight as an instant; otherwise host/browser
        // timezone conversion can shift the visible day and hide the previous
        // completed daily candle.
        const key = String(this.currentInterval || "").toLowerCase();
        if (key === "day") {
            const d = new Date(epochMs);
            return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
        }

        return Math.floor(epochMs / intervalMs) * intervalMs;
    }

    _istBucketStartMs(epochMs, intervalMs) {
        // Floor epoch to an IST-aligned intraday bucket anchored at 09:15 IST.
        // This prevents buckets from straddling session boundaries or midnight UTC.
        //
        // Why not plain floor(epochMs / intervalMs) * intervalMs?
        // Because UTC midnight ≠ IST midnight (18:30 UTC = 00:00 IST).
        // UTC-aligned buckets split the 09:00–09:30 IST candle across two UTC days.
        //
        // Algorithm:
        //   1. Find IST midnight of the current day.
        //   2. Add 09:15 to get market open epoch.
        //   3. Compute ms elapsed since market open.
        //   4. Floor to nearest intervalMs.
        //   5. Return absolute bucket start epoch.
        if (!Number.isFinite(epochMs) || !Number.isFinite(intervalMs) || intervalMs <= 0) {
            return epochMs;
        }
        const IST_OFFSET_MS   = 5.5 * 60 * 60 * 1000;
        const MARKET_OPEN_MS  = (9 * 60 + 15) * 60 * 1000;  // 09:15 as ms from IST midnight

        const istMs = epochMs + IST_OFFSET_MS;
        const d = new Date(istMs);
        // IST midnight expressed as UTC epoch
        const istMidnightUtc = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) - IST_OFFSET_MS;
        // Market open as UTC epoch
        const marketOpenEpoch = istMidnightUtc + MARKET_OPEN_MS;

        if (epochMs < marketOpenEpoch) {
            // Pre-market tick: slot it into the *previous* session's last bucket
            // so it doesn't trigger a new-candle append.
            return marketOpenEpoch - intervalMs;
        }

        const msFromOpen = epochMs - marketOpenEpoch;
        const bucketIdx  = Math.floor(msFromOpen / intervalMs);
        return marketOpenEpoch + bucketIdx * intervalMs;
    }

    _nextAlignedIntradayCandleTimeMs(lastTimeMs, nowMs, intervalMs) {
        // Kite historical intraday candles are timestamped from the NSE session
        // grid (09:15, then +interval).  Do not rebucket live ticks on absolute
        // UTC boundaries: for 60minute that creates 09:00/10:00 candles beside
        // Kite's 09:15/10:15 candles, and for non-divisor intervals it can hide
        // expected candles.  Advance strictly from the last Kite candle time.
        if (!Number.isFinite(lastTimeMs) || !Number.isFinite(nowMs) ||
            !Number.isFinite(intervalMs) || intervalMs <= 0) {
            return NaN;
        }
        const elapsed = nowMs - lastTimeMs;
        if (elapsed < intervalMs) return NaN;

        const steps = Math.max(1, Math.floor(elapsed / intervalMs));
        return lastTimeMs + steps * intervalMs;
    }

    _isWithinNseSession(epochMs) {
        if (!Number.isFinite(epochMs)) return false;

        const IST_OFFSET_MS = 5.5 * 60 * 60 * 1000;
        const d = new Date(epochMs + IST_OFFSET_MS);
        const minutes = d.getUTCHours() * 60 + d.getUTCMinutes();
        const NSE_OPEN_MINUTES = 9 * 60 + 15;
        const NSE_CLOSE_MINUTES = 15 * 60 + 30;
        return minutes >= NSE_OPEN_MINUTES && minutes <= NSE_CLOSE_MINUTES;
    }

    _tradingDayKey(epochMs) {
        if (!Number.isFinite(epochMs)) return '';
        // IST = UTC+5:30. Use pure offset arithmetic so this works even in
        // Chromium-embedded environments where IANA timezone data may be
        // incomplete (some QtWebEngine builds on Linux).
        // NEVER fall back to toISOString().slice(0,10) — that is UTC, not IST,
        // and causes the day to "change" at 18:30 UTC (= 00:00 IST) rather than
        // at the correct IST midnight.
        const IST_OFFSET_MS = 5.5 * 60 * 60 * 1000; // 330 minutes in ms
        const istMs = epochMs + IST_OFFSET_MS;
        const d = new Date(istMs);
        const year  = d.getUTCFullYear();
        const month = String(d.getUTCMonth() + 1).padStart(2, '0');
        const day   = String(d.getUTCDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    _shouldAppendLiveCandle(lastTimeMs, nowMs, intervalMs) {
        // Validate inputs — a NaN nowMs (from a bad tick timestamp) must never
        // trigger a new candle. Fall back to nothing rather than creating a ghost.
        if (!Number.isFinite(lastTimeMs) || !Number.isFinite(nowMs)) return false;
        if (nowMs <= 0 || lastTimeMs <= 0) return false;

        const _key = String(this.currentInterval || 'day').toLowerCase();

        if (_key === 'day') {
            // IST-day comparison: use _tradingDayKey which uses pure IST arithmetic.
            // Both keys must be non-empty strings for a valid comparison.
            const lastDay = this._tradingDayKey(lastTimeMs);
            const nowDay  = this._tradingDayKey(nowMs);
            if (!lastDay || !nowDay) return false;

            // Only append when the IST calendar date has actually advanced.
            // nowDay === lastDay  → same session, update existing candle.
            // nowDay  > lastDay   → new IST day, append new candle.
            // nowDay  < lastDay   → tick from the past (stale/replay), ignore.
            return nowDay > lastDay;
        }

        // Intraday: use IST-aligned session buckets.
        if (!Number.isFinite(intervalMs) || intervalMs <= 0) return false;
        const lastBucket = this._istBucketStartMs(lastTimeMs, intervalMs);
        const nowBucket  = this._istBucketStartMs(nowMs, intervalMs);
        if (!Number.isFinite(lastBucket) || !Number.isFinite(nowBucket)) return false;

        if (nowBucket > lastBucket) return true;

        // Same bucket: keep updating the current live candle instead of
        // appending. Appending within the same bucket creates ghost candles
        // and shifts the chart left on every tick.
        return false;
    }

    _coerceEpochMs(value) {
        if (value === undefined || value === null || value === '' || value === 0) return NaN;
        if (value instanceof Date) {
            const t = value.getTime();
            return Number.isFinite(t) && t > 0 ? t : NaN;
        }

        const numeric = Number(value);
        if (!Number.isFinite(numeric) || numeric <= 0) return NaN;

        // Broker timestamps:
        //   > 1e12  → already milliseconds  (e.g. 1715000000000)
        //   < 1e12  → seconds               (e.g. 1715000000)
        // Guard: reject values that look like future centuries (> year 2100)
        const MS_2100 = 4_102_444_800_000;
        if (numeric > MS_2100) return NaN;           // garbage / far-future
        return numeric < 1e12 ? numeric * 1000 : numeric;
    }

    updateLivePrice(price, tickTime = null, tickOpen = 0, tickHigh = 0, tickLow = 0, tickVolume = null) {
        this.livePrice     = price;
        this._hasLiveTicks = true;

        if (this.data.length === 0) {
            this.requestDraw();
            return;
        }

        const last         = this.data[this.data.length - 1];
        const intervalMs   = this._intervalToMs(this.currentInterval);
        const key          = String(this.currentInterval || 'day').toLowerCase();
        const isDailyInterval = key === 'day';
        const lastTimeMs   = Number(last.time);
        const tickMs       = this._coerceEpochMs(tickTime);

        // Use tick timestamp when valid; fall back to current wall time.
        // IMPORTANT: if tickMs is NaN (bad/missing timestamp), use Date.now()
        // rather than 0 or lastTimeMs to avoid false "new candle" triggers.
        const nowMs = Number.isFinite(tickMs) ? tickMs : Date.now();

        if (intervalMs > 0 && Number.isFinite(lastTimeMs) && lastTimeMs > 0) {
            if (this._shouldAppendLiveCandle(lastTimeMs, nowMs, intervalMs)) {
                const carryClose = Number.isFinite(last.close) ? last.close : price;
                const IST_OFFSET_MS = 5.5 * 60 * 60 * 1000;

                let newCandleTime;
                if (isDailyInterval) {
                    // TradingView-style daily candles are exchange calendar bars.
                    // Use UTC midnight for the IST trading date as a stable
                    // date key; do not use IST midnight as an absolute instant.
                    const istMs = nowMs + IST_OFFSET_MS;
                    const _d = new Date(istMs);
                    newCandleTime = Date.UTC(
                        _d.getUTCFullYear(),
                        _d.getUTCMonth(),
                        _d.getUTCDate()
                    );
                } else {
                    // Intraday: IST-aligned bucket start, anchored at 09:15 IST.
                    newCandleTime = this._istBucketStartMs(nowMs, intervalMs);
                    // Sanity: never create a candle at a negative or zero timestamp.
                    if (!Number.isFinite(newCandleTime) || newCandleTime <= 0) {
                        // Fall back — don't append.
                        newCandleTime = null;
                    }
                }

                if (newCandleTime !== null) {
                    this.data.push({
                        time:   newCandleTime,
                        open:   carryClose,
                        high:   carryClose,
                        low:    carryClose,
                        close:  carryClose,
                        volume: 0,
                    });
                    this.volumeData.push({ time: newCandleTime, value: 0 });
                    this._volVpKey = null;

                    this.viewPortEnd = Math.max(
                        this.viewPortEnd,
                        this.data.length - 1 + this.rightBufferCandles,
                    );
                    this._updateViewport();
                    this.calculateBounds();
                }
            }
        }

        // ── Update the active (last) candle ──────────────────────────────────────
        const active = this.data[this.data.length - 1];
        active.close = price;

        if (isDailyInterval) {
            const parsedTickVolume = Number(tickVolume);
            if (Number.isFinite(parsedTickVolume) && parsedTickVolume >= 0) {
                active.volume = parsedTickVolume;
                if (this.volumeData.length > 0) {
                    this.volumeData[this.volumeData.length - 1] = { time: active.time, value: parsedTickVolume };
                }
                this._volVpKey = null;
            }
            // For daily interval: honour broker-supplied session OHLC when available.
            // tickHigh/tickLow are the day's high/low from the broker tick payload.
            if (tickHigh > 0) active.high = Math.max(active.high, tickHigh, price);
            else              active.high = Math.max(active.high, price);

            if (tickLow > 0 && tickLow < active.high)
                              active.low  = Math.min(active.low,  tickLow,  price);
            else              active.low  = Math.min(active.low,  price);

            // Fix carry-over open: if the candle's open equals the previous close
            // (indicating it was synthesised), replace it with the real session open.
            if (
                tickOpen > 0 &&
                this.data.length >= 2
            ) {
                const prev = this.data[this.data.length - 2];
                if (Math.abs(active.open - prev.close) / (Math.abs(prev.close) || 1) < 0.0001) {
                    active.open = tickOpen;
                    active.high = Math.max(active.open, active.high);
                    active.low  = Math.min(active.open, active.low);
                }
            }
        } else {
            // Intraday: NEVER apply tickHigh/tickLow — those are the day's range,
            // not the current intraday bar's range. Applying them creates a single
            // bar spanning the entire day's wick, hiding all other bars visually.
            active.high = Math.max(active.high, price);
            active.low  = Math.min(active.low,  price);
        }

        this.requestDraw();
    }

    loadNewData(cfg) {
        // Reset live-tick state so the previous symbol's LTP never
        // pollutes the first rendered frame of the new symbol.
        this.livePrice = null;
        this._hasLiveTicks = false;

        this.data = cfg.candlestickData || [];
        this._rebuildHeikinAshiData();
        this.volumeData = cfg.volumeData || [];
        this.emaData = cfg.emaData || {};
        this.movingAverageConfigs = cfg.movingAverageConfigs || [];
        if (cfg.initialIndicatorVisibility && typeof cfg.initialIndicatorVisibility === 'object') {
            this.indicatorVisibility = {
                ...this.indicatorVisibility,
                ...cfg.initialIndicatorVisibility,
            };
            _saveIndicatorState(this.indicatorVisibility);
        }
        this.currentADR = cfg.initialADR || {};
        this.percentageChanges = cfg.percentageChanges || {};
        this.currentInterval = cfg.interval || 'day';
        if (cfg.chartType !== undefined) {
            this._chartType = cfg.chartType;
        }
        if (window.__CHART_DATA__) {
            window.__CHART_DATA__.chartType = this._chartType;
        }
        this.currentSymbol = cfg.symbol || '';
        this.priceScaleCurrency = this._resolvePriceScaleCurrency(cfg.priceScaleCurrency, this.currentSymbol);
        if (cfg.rightBufferCandles !== undefined) {
            const nextRightBuffer = Number(cfg.rightBufferCandles);
            if (Number.isFinite(nextRightBuffer)) {
                this.rightBufferCandles = Math.max(0, Math.round(nextRightBuffer));
            }
        }
        this.currentSymbolDescription = cfg.watermarkDescription || '';
        this.showWatermarkDescription = cfg.showWatermarkDescription === true;
        this._intradayTimestampsAlreadyIst = null;

        // Reset viewport state.
        this.panOffsetPx = 0;
        this.isUserYRange = false;          // always reset auto-scale on symbol switch
        this._volVpKey = null;
        this._cachedMaxVolume = 1;

        // ── FIX (Bug 3): reset rightBufferCandles and viewPortEnd FIRST ──
        // Then call _updateChartAreas() → _updateViewport() in order so
        // visibleCandleCount is computed from the correct chartArea.width.
        // Previously _updateViewport() was called before _updateChartAreas(),
        // so chartArea could still have the previous symbol's geometry.
        this.viewPortEnd = Math.max(0, this.data.length - 1 + this.rightBufferCandles);

        // Recompute chart geometry with fresh data length / indicator visibility.
        this._updateChartAreas();           // ← must come BEFORE _updateViewport
        this._updateViewport();             // derives visibleCandleCount from fresh chartArea

        // Honour the Python-supplied zoom preference only when it would produce
        // a meaningfully different candleWidth from what _updateViewport chose.
        // This preserves "same zoom across symbols" without corrupting the layout.
        if (cfg.visibleCandleCount && cfg.visibleCandleCount > 0 && this.chartArea) {
            const desiredW = Math.max(2, Math.min(60,
                Math.floor(this.chartArea.width / cfg.visibleCandleCount) - this.candleSpacing
            ));
            // Only apply if the difference is meaningful (> 1 px) to avoid
            // tiny float differences causing visible jumps.
            if (Math.abs(desiredW - this.candleWidth) > 1) {
                this.candleWidth = desiredW;
                this._updateViewport();     // recalculate after candleWidth change
            }
        }

        if (this.data.length > 0) {
            this._computeRenko();
        }

        // Load drawings.
        if (cfg.initialDrawingsJson) {
            try {
                this.updateDrawings(JSON.parse(cfg.initialDrawingsJson));
            } catch (e) {
                this.clearAllDrawings();
            }
        } else {
            this.clearAllDrawings();
        }

        // Recompute price bounds with the new data and correct geometry.
        this.calculateBounds();

        this.requestDraw();
        this.updateSlider();
        this._displayLatestCandleDetails();
    }

    refreshHistoricalData(cfg) {
        // Minute-boundary historical refresh should NOT reset user's viewport
        // or y-axis range. Preserve current navigation context and reuse the
        // common load path, then restore.
        const prev = {
            viewPortStart: this.viewPortStart,
            viewPortEnd: this.viewPortEnd,
            minPrice: this.minPrice,
            maxPrice: this.maxPrice,
            isUserYRange: this.isUserYRange,
            panOffsetPx: this.panOffsetPx,
        };

        this.loadNewData(cfg);

        const maxEnd = Math.max(0, this.data.length - 1 + this.rightBufferCandles);
        this.viewPortEnd = Math.max(0, Math.min(maxEnd, prev.viewPortEnd));
        this.viewPortStart = Math.max(0, Math.min(this.viewPortEnd, prev.viewPortStart));
        this.panOffsetPx = prev.panOffsetPx || 0;

        this.isUserYRange = !!prev.isUserYRange;
        if (this.isUserYRange && Number.isFinite(prev.minPrice) && Number.isFinite(prev.maxPrice) && prev.maxPrice > prev.minPrice) {
            this.minPrice = prev.minPrice;
            this.maxPrice = prev.maxPrice;
        } else {
            this.calculateBounds();
        }

        this.requestDraw();
        this.updateSlider();
    }

    updateDrawings(drawings) {
        if (!drawings) return;
        if (this.drawingEngine) {
            this.drawingEngine.deserialize(drawings);
            if (typeof DrawingsCompat !== 'undefined') {
                this.drawings = new DrawingsCompat(this.drawingEngine);
            }
        } else {
            // Legacy fallback for safety if drawingEngine is unavailable.
            this.drawings = {
                lines:            Array.isArray(drawings.lines) ? drawings.lines : [],
                rectangles:       Array.isArray(drawings.rectangles) ? drawings.rectangles : [],
                notes:            Array.isArray(drawings.notes) ? drawings.notes : [],
                horizontal_lines: Array.isArray(drawings.horizontal_lines) ? drawings.horizontal_lines : [],
                horizontal_rays:  Array.isArray(drawings.horizontal_rays) ? drawings.horizontal_rays : [],
                arrow_lines:      Array.isArray(drawings.arrow_lines) ? drawings.arrow_lines : [],
                fibonacci:        Array.isArray(drawings.fibonacci) ? drawings.fibonacci : [],
            };
        }
        this.requestDraw();
    }

    addNewCandle(candle) {
        this.data.push(candle);
        // Keep viewport anchored to latest candle if user hasn't panned away.
        const wasAtEnd = this.viewPortEnd >= this.data.length - 2 + this.rightBufferCandles;
        if (wasAtEnd) {
            this.viewPortEnd = this.data.length - 1 + this.rightBufferCandles;
            this._updateViewport();
        }
        this._computeRenko();
        this.calculateBounds();
        this.requestDraw();
        this.updateSlider();
    }

    setVisibleCandleCount(count) {
        // Legacy API — convert requested count to the nearest candleWidth that
        // would show that many candles in the current chart area.
        if (count > 0 && this.chartArea) {
            const targetW = Math.max(2, Math.floor(this.chartArea.width / count) - this.candleSpacing);
            this.candleWidth = Math.max(2, Math.min(60, targetW));
        }
        this.viewPortEnd = Math.max(0, this.data.length - 1 + this.rightBufferCandles);
        this._updateViewport();
        this.calculateBounds();
        this.requestDraw();
        this.updateSlider();
    }

    setChartSettings(cfg) {
        if (cfg.upCandleColor)   this.colors.upCandle   = cfg.upCandleColor;
        if (cfg.downCandleColor) this.colors.downCandle = cfg.downCandleColor;
        const slotChanged = (cfg.candleWidth && cfg.candleWidth !== this.candleWidth) ||
                            (cfg.candleSpacing !== undefined && cfg.candleSpacing !== this.candleSpacing);
        if (cfg.candleWidth)                    this.candleWidth   = cfg.candleWidth;
        if (cfg.candleSpacing !== undefined)    this.candleSpacing = cfg.candleSpacing;
        if (cfg.rightBufferCandles !== undefined) {
            const nextRightBuffer = Number(cfg.rightBufferCandles);
            if (Number.isFinite(nextRightBuffer)) this.rightBufferCandles = Math.max(0, Math.round(nextRightBuffer));
        }
        if (cfg.watermarkEnabled  !== undefined) this.watermark.enabled  = cfg.watermarkEnabled;
        if (cfg.watermarkColor)   this.watermark.color   = cfg.watermarkColor;
        if (cfg.watermarkOpacity  !== undefined) this.watermark.opacity  = cfg.watermarkOpacity;
        if (cfg.watermarkPosition) this.watermark.position = cfg.watermarkPosition;
        if (cfg.watermarkFontSize !== undefined) this.watermark.fontSize = cfg.watermarkFontSize;
        if (cfg.watermarkDescriptionOpacity !== undefined) this.watermark.descriptionOpacity = cfg.watermarkDescriptionOpacity;
        if (cfg.watermarkDescriptionFontSize !== undefined) this.watermark.descriptionFontSize = cfg.watermarkDescriptionFontSize;
        if (cfg.showWatermarkDescription !== undefined)
            this.showWatermarkDescription = cfg.showWatermarkDescription === true;
        this._intradayTimestampsAlreadyIst = null;
        if (cfg.indicatorScaleLabelsEnabled !== undefined)
            this.indicatorScaleLabelsEnabled = cfg.indicatorScaleLabelsEnabled === true;
        if (cfg.crosshairSnapEnabled !== undefined)
            this.crosshairSnapEnabled = cfg.crosshairSnapEnabled === true;
        if (cfg.showTimeSlider !== undefined) {
            this.showTimeSlider = cfg.showTimeSlider === true;
            this._syncSliderVisibility();
        }
        if (cfg.chartType !== undefined) {
            this._chartType = cfg.chartType === 'renko' ? 'candle' : cfg.chartType;
            if (window.__CHART_DATA__) {
                window.__CHART_DATA__.chartType = this._chartType;
            }
        }
        if (cfg.renkoBoxPctIntraday !== undefined) this._renkoBoxPctIntraday = cfg.renkoBoxPctIntraday;
        if (cfg.renkoBoxPctSwing !== undefined) this._renkoBoxPctSwing = cfg.renkoBoxPctSwing;
        if (cfg.renkoBoxPctIntraday !== undefined || cfg.renkoBoxPctSwing !== undefined) this._computeRenko();
        if (cfg.infoVisibility && typeof cfg.infoVisibility === 'object') {
            this.infoVisibility = { ...this.infoVisibility, ...cfg.infoVisibility };
        }
        if (cfg.toolSelectionMode !== undefined) {
            this.toolSelectionMode = cfg.toolSelectionMode === 'multi_use' ? 'multi_use' : 'single_use';
            if (this.drawingEngine) {
                this.drawingEngine.toolSelectionMode = this.toolSelectionMode;
            }
        }
        // If slot dimensions changed, recalculate how many candles fit.
        if (slotChanged || cfg.rightBufferCandles !== undefined) {
            this.viewPortEnd = Math.max(0, this.data.length - 1 + this.rightBufferCandles);
            this._updateViewport();
            this.calculateBounds();
        }
        this.requestDraw();
    }

    setWatermark(symbol, description = '', showDescription = false) {
        this.currentSymbol = symbol || '';
        this.currentSymbolDescription = description || '';
        if (this.drawingEngine) this.drawingEngine.currentSymbol = this.currentSymbol;
        this.showWatermarkDescription = showDescription === true;
        this.requestDraw();
    }

    updateDrawingStyle(color, lw) {
        this.drawingColor = color || this.drawingColor;
        this.lineWidth    = lw    || this.lineWidth;
    }

    addTextNoteFromDialog(note) {
        if (this.drawingEngine) {
            this.drawingEngine.addDrawing({
                type: 'note',
                startTime: this._xToTime(note.x),
                startPrice: this._yToPrice(note.y),
                text: note.text,
                color: note.color,
                fontSize: note.size
            });
            this.requestDraw();
            this._notifyDrawingsChange();
            return;
        }
        this.drawings.notes.push({
            id: Date.now() + Math.random(), type: 'note',
            time: this._xToTime(note.x), price: this._yToPrice(note.y),
            text: note.text, color: note.color, size: note.size
        });
        this.requestDraw(); this._notifyDrawingsChange();
    }

    updateTextNote(note) {
        for (const type of ['notes']) {
            const idx = this.drawings[type].findIndex(d => d.id === note.id);
            if (idx !== -1) { this.drawings[type][idx] = note; break; }
        }
        this.requestDraw(); this._notifyDrawingsChange();
    }

    clearAllDrawings() {
        this.drawings = { lines:[], rectangles:[], notes:[], horizontal_lines:[],
                          horizontal_rays:[], arrow_lines:[], fibonacci:[] };
        this.requestDraw(); this._notifyDrawingsChange();
    }

    _deleteSelected() {
        if (!this.selectedDrawingId) return;
        for (const key of Object.keys(this.drawings)) {
            const before = this.drawings[key].length;
            this.drawings[key] = this.drawings[key].filter(d => d.id !== this.selectedDrawingId);
            if (this.drawings[key].length < before) break;
        }
        this.selectedDrawingId = null;
        this.requestDraw(); this._notifyDrawingsChange();
    }

    autoScale() {
        this.viewPortEnd = Math.max(0, this.data.length - 1 + this.rightBufferCandles);
        this._updateViewport();
        this.calculateBounds();
        this.requestDraw();
        this.updateSlider();
    }

    setIndicatorVisibility(key, visible) {
        this.indicatorVisibility[key] = visible === true;
        // Persist immediately — survives any symbol/timeframe reload
        _saveIndicatorState(this.indicatorVisibility);
        // Notify Python bridge so its own state stays in sync
        this._notifyIndicatorVisibilityChanged();
        this.requestDraw();
    }

    getIndicatorVisibility() {
        return { ...this.indicatorVisibility };
    }

    // ─── Python-callable: hard-reset all visibility to defaults (all off) ────
    resetIndicatorVisibility() {
        try { localStorage.removeItem(_IND_STORE_KEY); } catch (e) {}
        this.indicatorVisibility = {};
        _saveIndicatorState(this.indicatorVisibility);
        this.requestDraw();
    }

    // Indicator panel removed — toggle via toolbar IND ▾ menu

        getAllDrawings()         { return this.drawings; }
    getVisibleCandleCount() { return this.visibleCandleCount; }
    getCandleWidth() { return this.candleWidth; }
    getCandleSpacing() { return this.candleSpacing; }

    // ═══════════════════════════════════════════════════════════════════════
    // NOTIFICATIONS TO PYTHON
    // ═══════════════════════════════════════════════════════════════════════

    _notifyDrawingsChange() {
        if (!this.chartBridge || !this.webChannelInitialized) {
            this._notifyQueue.push(() => this._notifyDrawingsChange());
            this._scheduleFlush();
            return;
        }
        try {
            const legacyFmt = legacySerialize(this.drawingEngine.getDrawings());
            this.chartBridge.notify_drawings_changed(JSON.stringify(legacyFmt));
        } catch (e) { console.error('notify_drawings_changed error:', e); }
    }

    // Notify Python so its own Dict[str,bool] stays in sync.
    // Python should persist this and pass it back as initial_indicator_visibility
    // on the NEXT chart load — but localStorage is the primary persistence layer.
    _notifyIndicatorVisibilityChanged() {
        if (!this.chartBridge || !this.webChannelInitialized) return;
        try {
            this.chartBridge.notify_indicator_visibility_changed(
                JSON.stringify(this.indicatorVisibility));
        } catch (e) { /* bridge not connected — localStorage already saved */ }
    }

    _notifyZoomChange() {
        if (!this.chartBridge || !this.webChannelInitialized) return;
        try {
            this.chartBridge.notify_zoom_changed(this.visibleCandleCount);
            this.chartBridge.notify_zoom_preferences_changed(
                Math.round(this.visibleCandleCount),
                Math.round(this.candleWidth),
                Math.round(this.candleSpacing),
            );
        }
        catch (e) { console.error('notify_zoom_changed error:', e); }
    }

    _notifyDrawingToolCleared() {
        if (!this.chartBridge || !this.webChannelInitialized) {
            this._notifyQueue.push(() => this._notifyDrawingToolCleared());
            this._scheduleFlush();
            return;
        }
        try { this.chartBridge.notify_drawing_tool_cleared(); }
        catch (e) { console.error('notify_drawing_tool_cleared error:', e); }
    }

    _scheduleFlush() {
        if (this._notifyTimer) return;
        this._notifyTimer = setTimeout(() => { this._notifyTimer = null; this._flushNotifyQueue(); }, 100);
    }

    _flushNotifyQueue() {
        if (!this.chartBridge || !this.webChannelInitialized) return;
        while (this._notifyQueue.length > 0) this._notifyQueue.shift()();
    }

    // ═══════════════════════════════════════════════════════════════════════
    // NICE NUMBER / FORMAT HELPERS
    // ═══════════════════════════════════════════════════════════════════════

    _niceStep(rough) {
        const pow10 = Math.pow(10, Math.floor(Math.log10(rough)));
        const frac  = rough / pow10;
        let nice;
        if      (frac < 1.5) nice = 1;
        else if (frac < 3.5) nice = 2;
        else if (frac < 7.5) nice = 5;
        else                 nice = 10;
        return nice * pow10;
    }

    _priceDecimals(step) {
        if (step >= 100) return 0;
        if (step >= 1)   return 1;
        if (step >= 0.1) return 2;
        return 3;
    }

    _axisFont(size, weight) {
        return `${weight} ${size}px ${this.fontStack}`;
    }

    _snapStrokeWidth(cssWidth) {
        const dpr = this.dpr || window.devicePixelRatio || 1;
        const devPx = Math.max(1, Math.round(cssWidth * dpr));
        return devPx / dpr;
    }

    _resolvePriceScaleCurrency(explicitCurrency, symbol) {
        const exp = String(explicitCurrency || '').trim().toUpperCase();
        if (exp === 'INR' || exp === 'USD') return exp;
        const sym = String(symbol || '').trim().toUpperCase();
        if (sym.endsWith('.NS') || sym.endsWith('.BO')) return 'INR';
        return 'USD';
    }

    _fmtVol(vol) {
        if (vol >= 1e9) return (vol / 1e9).toFixed(2).replace(/\.00$/, '') + 'B';
        if (vol >= 1e6) return (vol / 1e6).toFixed(2).replace(/\.00$/, '') + 'M';
        if (vol >= 1e3) return (vol / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
        return Math.round(vol).toString();
    }

    _fmtVolExact(vol) {
        return Math.round(vol).toLocaleString('en-US');
    }

    _fmtTimeLabel(timeOrDate) {
        const epochMs = timeOrDate instanceof Date ? timeOrDate.getTime() : Number(timeOrDate);
        if (this.currentInterval.includes('minute')) {
            const date = this._exchangeDate(epochMs);
            const time = this._fmtExchangeTime(date);
            return this._exchangeDayKey(epochMs) === this._actualIstDayKey(Date.now())
                ? time
                : `${this._fmtExchangeDayMonth(date)} ${time}`;
        }

        const date = timeOrDate instanceof Date ? timeOrDate : new Date(epochMs);
        const now = new Date();
        const daysDiff = Math.floor((now - date) / 86400000);
        return this._fmtDateLabel(epochMs, daysDiff > 330);
    }

    _fmtDateLabel(epochMs, includeYear = false) {
        if (this.currentInterval.includes('minute')) {
            const d = this._exchangeDate(epochMs);
            return includeYear ? this._fmtExchangeDayMonthYear(d) : this._fmtExchangeDayMonth(d);
        }
        return new Date(epochMs).toLocaleDateString('en-GB', {
            day: '2-digit', month: 'short', year: includeYear ? 'numeric' : undefined, timeZone: 'UTC'
        });
    }

    _exchangeDate(epochMs) {
        const ms = Number(epochMs);
        if (!Number.isFinite(ms)) return new Date(NaN);
        return new Date(this._exchangeDisplayMs(ms));
    }

    _exchangeDisplayMs(epochMs) {
        if (!String(this.currentInterval || '').includes('minute')) return epochMs;
        return this._intradayDataUsesIstClock() ? epochMs : epochMs + IST_OFFSET_MS;
    }

    _intradayDataUsesIstClock() {
        if (this._intradayTimestampsAlreadyIst !== null) return this._intradayTimestampsAlreadyIst;
        let directSession = 0;
        let shiftedSession = 0;
        const sample = (this.data || []).slice(0, Math.min(80, (this.data || []).length));
        for (const candle of sample) {
            const t = Number(candle?.time);
            if (!Number.isFinite(t)) continue;
            if (this._minutesUtc(t) >= NSE_OPEN_MINUTES && this._minutesUtc(t) <= NSE_CLOSE_MINUTES) directSession++;
            if (this._minutesUtc(t + IST_OFFSET_MS) >= NSE_OPEN_MINUTES && this._minutesUtc(t + IST_OFFSET_MS) <= NSE_CLOSE_MINUTES) shiftedSession++;
        }
        this._intradayTimestampsAlreadyIst = directSession >= shiftedSession;
        return this._intradayTimestampsAlreadyIst;
    }

    _minutesUtc(epochMs) {
        const d = new Date(epochMs);
        return d.getUTCHours() * 60 + d.getUTCMinutes();
    }

    _exchangeDayKey(epochMs) {
        const d = this._exchangeDate(epochMs);
        return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
    }

    _actualIstDayKey(epochMs) {
        const d = new Date(Number(epochMs) + IST_OFFSET_MS);
        return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
    }

    _fmtExchangeTime(d) {
        return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
    }

    _fmtExchangeDayMonth(d) {
        return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', timeZone: 'UTC' });
    }

    _fmtExchangeDayMonthYear(d) {
        return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' });
    }

    _fmtExchangeMonthYear(d) {
        return d.toLocaleDateString('en-GB', { month: 'short', year: '2-digit', timeZone: 'UTC' });
    }

    _hexToRgba(hex, alpha) {
        if (!hex || !hex.startsWith('#')) return `rgba(128,128,128,${alpha})`;
        const h = hex.replace('#', '');
        const full = h.length === 3 ? h.split('').map(c => c+c).join('') : h;
        const r = parseInt(full.slice(0,2),16), g = parseInt(full.slice(2,4),16), b = parseInt(full.slice(4,6),16);
        return `rgba(${r},${g},${b},${alpha})`;
    }

    _darken(hex, factor) {
        if (!hex || !hex.startsWith('#')) return hex;
        const h = hex.replace('#', '');
        const full = h.length === 3 ? h.split('').map(c => c+c).join('') : h;
        const r = Math.max(0, Math.round(parseInt(full.slice(0,2),16) * (1-factor)));
        const g = Math.max(0, Math.round(parseInt(full.slice(2,4),16) * (1-factor)));
        const b = Math.max(0, Math.round(parseInt(full.slice(4,6),16) * (1-factor)));
        return `#${r.toString(16).padStart(2,'0')}${g.toString(16).padStart(2,'0')}${b.toString(16).padStart(2,'0')}`;
    }

    // ═══════════════════════════════════════════════════════════════════════
    // TIME AXIS CANDIDATE BUILDER
    // ═══════════════════════════════════════════════════════════════════════

    _buildTimeCandidates(tf) {
        const candidates = [];
        const start = Math.max(0, this.viewPortStart - 1);
        const end   = Math.min(this.data.length - 1, this.viewPortEnd + 1);

        const todayKey = this.currentInterval.includes('minute')
            ? this._actualIstDayKey(Date.now())
            : new Date().toISOString().slice(0, 10);
        let todayMarker = null;

        for (let i = start; i <= end; i++) {
            const t = this.data[i].time;
            const d = this._exchangeDate(t);
            const label = this._timeCandidateLabel(d, tf);
            const isIntradayTodayCandle = this.currentInterval.includes('minute') && (this._exchangeDayKey(t) === todayKey);
            if (label && !(tf === '60minute' && isIntradayTodayCandle)) {
                candidates.push({ time: t, label });
            }

            const candleKey = this.currentInterval.includes('minute')
                ? this._exchangeDayKey(t)
                : new Date(Number(t)).toISOString().slice(0, 10);
            if (candleKey === todayKey) {
                todayMarker = { time: t, label: this.currentInterval.includes('minute') ? this._fmtExchangeDayMonth(d) : this._fmtDateLabel(t, false), isToday: true };
            }
        }

        if (todayMarker) candidates.push(todayMarker);
        return candidates;
    }

    _timeCandidateLabel(d, tf) {
        const m = d.getUTCMinutes(), h = d.getUTCHours(), dom = d.getUTCDate(), dow = d.getUTCDay(), mon = d.getUTCMonth();
        if (tf === 'minute')   return m % 15 === 0 ? `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}` : null;
        if (tf === '3minute')  return m % 30 === 0 ? `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}` : null;
        if (tf === '5minute')  return m % 30 === 0 ? `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}` : null;
        if (tf === '15minute') return h % 2 === 0 && m === 0 ? `${String(h).padStart(2,'0')}:00` : null;
        if (tf === '30minute') return m === 15 ? `${String(h).padStart(2,'0')}:15` : null;
        if (tf === '60minute') return h === 9  ? this._fmtExchangeDayMonth(d) : null;
        if (tf === 'day')      return dow === 1 ? this._fmtExchangeDayMonth(d) : null;
        if (tf === 'week')     return mon % 3 === 0 && dom <= 7 ? this._fmtExchangeMonthYear(d) : null;
        if (tf === 'month')    return mon === 0 ? String(d.getUTCFullYear()) : null;
        return null;
    }
}


// ─── Bootstrap ──────────────────────────────────────────────────────────────

function initChart() {
    if (window.__chartInitialized) return;
    window.__chartInitialized = true;

    try {
        const cfg = window.__CHART_DATA__;
        if (!cfg) { console.error('__CHART_DATA__ not set'); return; }
        const chart = new FixedTradingChart(cfg);
        window.chart     = chart;
        window.autoScale = () => chart.autoScale();
    } catch (e) {
        console.error('Chart init error:', e);
        const el = document.getElementById('metricsInfo');
        if (el) el.textContent = 'Error: ' + e.message;
    }
}

document.addEventListener('DOMContentLoaded', initChart);
if (document.readyState === 'interactive' || document.readyState === 'complete') initChart();
setTimeout(initChart, 100);
