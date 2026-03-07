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
 *   - Volume bars: 90th-percentile normalised + opacity proportional to size
 *   - Overlays: EMA10/20/50/200 with right-edge price labels
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

const FIB_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0];
const FIB_COLORS = ['#FFD700', '#FF9800', '#4CAF50', '#2196F3', '#9C27B0', '#F44336', '#FFD700'];
const FIB_LABELS = ['0%', '23.6%', '38.2%', '50%', '61.8%', '78.6%', '100%'];

// ─── FixedTradingChart ───────────────────────────────────────────────────────

class FixedTradingChart {
    constructor(cfg) {
        // ── Canvas ──
        this.canvas = document.getElementById(cfg.canvasId);
        this.ctx = this.canvas.getContext('2d');

        // ── Data ──
        this.data = cfg.candlestickData || [];
        this.volumeData = cfg.volumeData || [];
        this.emaData = cfg.emaData || {};
        this.currentADR = cfg.initialADR || {};
        this.percentageChanges = cfg.percentageChanges || {};
        this.currentInterval = cfg.currentInterval || 'day';
        this.currentSymbol = cfg.currentSymbol || '';

        // ── Settings ──
        this.colors = {
            bg:          '#0b0f18',
            bgGradTop:   '#0d1320',
            bgGradBot:   '#090c14',
            grid:        '#1a2035',
            gridMinor:   '#111826',
            text:        '#8a95a8',
            textBright:  '#c8d0e0',
            crosshair:   'rgba(140,170,220,0.35)',
            livePrice:   '#00bfff',
            upCandle:    cfg.upCandleColor   || '#26a69a',
            downCandle:  cfg.downCandleColor || '#ef5350',
            volumeUp:    cfg.upVolumeColor   || '#26a69a',
            volumeDown:  cfg.downVolumeColor || '#ef5350',
            vwap:        '#ff9e42',
            ema: { ema10: '#2962ff', ema20: '#9c27b0', ema50: '#f06204', ema200: '#e91e63' },
        };

        // ── Viewport ──
        this.rightBufferCandles = 5;
        this.candleWidth = cfg.initialCandleWidth || 4;
        this.candleSpacing = cfg.initialCandleSpacing || 2;
        this.visibleCandleCount = cfg.initialVisibleCandleCount || 100;
        this.viewPortEnd   = Math.max(0, this.data.length - 1 + this.rightBufferCandles);
        this.viewPortStart = Math.max(0, this.viewPortEnd - this.visibleCandleCount + 1);

        // ── Bounds ──
        this.minPrice = 0; this.maxPrice = 0;
        this.maxVolume = 1;

        // ── State ──
        this.livePrice   = null;
        this.crosshairX  = null;
        this.crosshairY  = null;
        this.isDragging  = false;
        this.lastMouseX  = 0;
        this.isUserZooming = false;
        this._rafPending = false;
        this._dirty = true;

        // ── Drawings ──
        this.drawings = this._initDrawings(cfg.initialDrawingsJson);
        this.currentTool = null;
        this.isDrawing = false;
        this.startPoint = null;
        this.endPoint   = null;
        this.drawingColor  = '#FFD700';
        this.lineWidth     = 1.5;
        this.selectedDrawingId = null;
        this.activeContextMenu = null;

        // ── Watermark ──
        this.watermark = {
            enabled:  cfg.watermarkEnabled !== false,
            color:    cfg.watermarkColor    || '#ffffff',
            opacity:  typeof cfg.watermarkOpacity  === 'number' ? cfg.watermarkOpacity  : 0.06,
            position: cfg.watermarkPosition || 'mid_center',
            fontSize: cfg.watermarkFontSize || 0,
        };
        this.indicatorScaleLabelsEnabled = cfg.indicatorScaleLabelsEnabled === true;

        // ── Computed VWAP ──
        this.vwapData = [];
        this._computeVWAP();

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
        this._setupSlider();
        this.calculateBounds();
        this._setupEventListeners();
        this._setupWebChannel();
        this.requestDraw();
        this.updateSlider();
        this._displayLatestCandleDetails();
        this._updateMetricsDisplay();
    }

    _setupCanvas() {
        const dpr = window.devicePixelRatio || 1;
        const w = this.canvas.clientWidth  || this.canvas.offsetWidth  || 800;
        const h = this.canvas.clientHeight || this.canvas.offsetHeight || 500;

        this.canvas.width  = Math.round(w * dpr);
        this.canvas.height = Math.round(h * dpr);
        this.ctx.scale(dpr, dpr);

        this.width  = w;
        this.height = h;
        this._updateChartAreas();

        // Handle resize
        const ro = new ResizeObserver(() => this._onResize());
        ro.observe(this.canvas.parentElement || document.body);
    }

    _onResize() {
        const dpr = window.devicePixelRatio || 1;
        const w = this.canvas.clientWidth  || 800;
        const h = this.canvas.clientHeight || 500;
        this.canvas.width  = Math.round(w * dpr);
        this.canvas.height = Math.round(h * dpr);
        this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        this.width  = w;
        this.height = h;
        this._updateChartAreas();
        this.calculateBounds();
        this.requestDraw();
        this.updateSlider();
    }

    _updateChartAreas() {
        const pad = { top: 32, right: this._computeRightAxisWidth(), bottom: 20, left: 8 };
        const volumeRatio = 0.18;    // volume pane = 18% of chart height
        const innerH = this.height - pad.top - pad.bottom - 16; // 16 for time axis
        const chartH  = Math.floor(innerH * (1 - volumeRatio));
        const volH    = Math.floor(innerH * volumeRatio);

        this.chartArea = {
            x: pad.left,
            y: pad.top,
            width:  this.width  - pad.left - pad.right,
            height: chartH,
        };
        this.volumeArea = {
            x:      pad.left,
            y:      pad.top + chartH + 4,
            width:  this.width - pad.left - pad.right,
            height: volH,
        };

        this.rightAxisWidth = pad.right;
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
            const label = '₹' + p.toFixed(decimals);
            maxTextWidth = Math.max(maxTextWidth, this.ctx.measureText(label).width);
        }

        this.ctx.font = prevFont;

        // 4px tick + ~4px inner gap + 4px right padding + 10px breathing room.
        const dynamicWidth = Math.ceil(maxTextWidth + 22);
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

    _computeVWAP() {
        if (this.data.length === 0) return;
        let cumTPV = 0, cumVol = 0;
        this.vwapData = this.data.map((c, i) => {
            const tp  = (c.high + c.low + c.close) / 3;
            const vol = (this.volumeData[i] || {}).value || 0;
            cumTPV += tp * vol;
            cumVol += vol;
            return { time: c.time, value: cumVol > 0 ? cumTPV / cumVol : tp };
        });
    }

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

            // Background gradient
            const grad = ctx.createLinearGradient(0, 0, 0, this.height);
            grad.addColorStop(0, this.colors.bgGradTop);
            grad.addColorStop(1, this.colors.bgGradBot);
            ctx.fillStyle = grad;
            ctx.fillRect(0, 0, this.width, this.height);

            if (this.data.length === 0) {
                ctx.fillStyle = this.colors.text;
                ctx.font = '14px "Segoe UI", sans-serif';
                ctx.textAlign = 'center';
                ctx.fillText('No data available', this.width / 2, this.height / 2);
                return;
            }

            this._drawGrid();
            this._drawSessionSeparators();
            this._drawGaps();
            this._drawVolume();
            this._drawVWAP();
            this._drawEMAs();
            this._drawCandlesticks();
            this._drawAxes();
            this._drawAllDrawings();
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

        // Volume divider
        ctx.strokeStyle = this.colors.grid;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(this.chartArea.x, this.volumeArea.y);
        ctx.lineTo(this.chartArea.x + this.chartArea.width, this.volumeArea.y);
        ctx.stroke();

        // Right-side price axis border
        ctx.strokeStyle = this.colors.grid;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(this.chartArea.x + this.chartArea.width, this.chartArea.y);
        ctx.lineTo(this.chartArea.x + this.chartArea.width, this.volumeArea.y + this.volumeArea.height);
        ctx.stroke();
    }

    _drawSessionSeparators() {
        if (!this.currentInterval.includes('minute')) return;
        const ctx = this.ctx;
        const MARKET_OPEN_HOUR = 9, MARKET_OPEN_MIN = 15;

        ctx.strokeStyle = 'rgba(80,100,140,0.4)';
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 5]);

        for (let i = this.viewPortStart; i <= this.viewPortEnd && i < this.data.length; i++) {
            const d = new Date(this.data[i].time);
            if (d.getHours() === MARKET_OPEN_HOUR && d.getMinutes() === MARKET_OPEN_MIN) {
                const x = this._candleToX(i) + this.candleWidth / 2;
                ctx.beginPath();
                ctx.moveTo(x, this.chartArea.y);
                ctx.lineTo(x, this.volumeArea.y + this.volumeArea.height);
                ctx.stroke();
            }
        }
        ctx.setLineDash([]);
    }

    _drawGaps() {
        if (this.currentInterval !== 'day' && this.currentInterval !== 'week') return;
        const ctx = this.ctx;

        for (let i = Math.max(1, this.viewPortStart); i <= this.viewPortEnd && i < this.data.length; i++) {
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
                ? 'rgba(38,166,154,0.09)'
                : 'rgba(239,83,80,0.09)';
            ctx.fillRect(x1, topY, x2 - x1, bottomY - topY);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    // CANDLESTICKS
    // ═══════════════════════════════════════════════════════════════════════

    _drawCandlesticks() {
        const ctx = this.ctx;
        const visCount = this.viewPortEnd - this.viewPortStart + 1;
        if (visCount <= 0) return;

        const candleSpace = this.chartArea.width / visCount;
        this.candleWidth  = Math.max(1.5, candleSpace - this.candleSpacing);

        const bodyInset = this.candleWidth >= 8 ? 0.5 : 0.25;
        const bodyW     = Math.max(1, this.candleWidth - bodyInset * 2);
        const wickW     = this.candleWidth >= 7 ? 1.5 : 1;
        const drawBorder = this.candleWidth >= 6;

        ctx.lineJoin = 'miter';
        ctx.lineCap  = 'butt';

        for (let i = this.viewPortStart; i < this.data.length && i <= this.viewPortEnd; i++) {
            if (i < 0) continue;
            const c = this.data[i];
            const x = this._candleToX(i);

            const openY  = this._priceToY(c.open);
            const closeY = this._priceToY(c.close);
            const highY  = this._priceToY(c.high);
            const lowY   = this._priceToY(c.low);

            const isUp  = c.close >= c.open;
            const col   = isUp ? this.colors.upCandle : this.colors.downCandle;
            const brdr  = isUp ? this._darken(col, 0.25) : this._darken(col, 0.25);
            const cx    = x + this.candleWidth / 2;

            // Wick
            ctx.strokeStyle = col;
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
                ctx.lineWidth   = 0.7;
                ctx.strokeRect(bx + 0.5, topY + 0.5, Math.max(0, bodyW - 1), Math.max(0, bodyH - 1));
            }
        }

        // Live price candle — update last bar
        if (this.livePrice !== null && this.data.length > 0) {
            const last = this.data.length - 1;
            if (last >= this.viewPortStart && last <= this.viewPortEnd) {
                const c  = { ...this.data[last], close: this.livePrice,
                              high: Math.max(this.data[last].high, this.livePrice),
                              low:  Math.min(this.data[last].low,  this.livePrice) };
                const x     = this._candleToX(last);
                const bx    = x + bodyInset;
                const openY = this._priceToY(c.open);
                const clY   = this._priceToY(c.close);
                const hiY   = this._priceToY(c.high);
                const loY   = this._priceToY(c.low);
                const isUp  = c.close >= c.open;
                const col   = isUp ? this.colors.upCandle : this.colors.downCandle;
                const cx    = x + this.candleWidth / 2;

                ctx.strokeStyle = col; ctx.lineWidth = wickW;
                ctx.beginPath(); ctx.moveTo(cx, hiY); ctx.lineTo(cx, loY); ctx.stroke();

                const topY  = Math.min(openY, clY);
                const bodyH = Math.max(1, Math.abs(clY - openY));
                ctx.fillStyle = col;
                ctx.fillRect(bx, topY, bodyW, bodyH);
            }
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    // VOLUME
    // ═══════════════════════════════════════════════════════════════════════

    _drawVolume() {
        const ctx = this.ctx;
        const visVols = [];
        for (let i = this.viewPortStart; i <= this.viewPortEnd && i < this.volumeData.length; i++) {
            if (i >= 0 && this.volumeData[i]) visVols.push(this.volumeData[i].value);
        }
        if (visVols.length === 0) return;

        // 90th percentile cap for cleaner bars
        const sorted = [...visVols].sort((a, b) => a - b);
        const p90    = sorted[Math.floor(sorted.length * 0.90)] || 1;
        this.maxVolume = p90 * 1.15;

        for (let i = this.viewPortStart; i <= this.viewPortEnd && i < this.volumeData.length; i++) {
            if (i < 0) continue;
            const vol    = this.volumeData[i];
            const candle = this.data[i];
            if (!vol || !candle) continue;

            const ratio  = Math.min(1, vol.value / this.maxVolume);
            const h      = ratio * this.volumeArea.height;
            const x      = this._candleToX(i);
            const barTop = this.volumeArea.y + this.volumeArea.height - h;
            const isUp   = candle.close >= candle.open;
            const alpha  = Math.min(0.85, 0.3 + ratio * 0.55);

            ctx.fillStyle = this._hexToRgba(isUp ? this.colors.volumeUp : this.colors.volumeDown, alpha);
            ctx.fillRect(x, barTop, this.candleWidth, h);

            // Bright top edge for bar separation
            ctx.fillStyle = this._hexToRgba(isUp ? this.colors.volumeUp : this.colors.volumeDown, 0.7);
            ctx.fillRect(x, barTop, this.candleWidth, 1);
        }

        this._drawCurrentVolLabel();
    }

    _drawCurrentVolLabel() {
        if (this.data.length === 0) return;
        const lastI = this.data.length - 1;
        if (lastI < this.viewPortStart || lastI > this.viewPortEnd) return;
        const vol = (this.volumeData[lastI] || {}).value || 0;
        if (!this.maxVolume) return;
        const ratio  = Math.min(1, vol / this.maxVolume);
        const logMax = Math.log(1 + this.maxVolume);
        const logVol = Math.log(1 + vol);
        const y      = this.volumeArea.y + this.volumeArea.height - (logVol / logMax * this.volumeArea.height);

        const ctx    = this.ctx;
        const label  = this._fmtVol(vol);
        ctx.font     = '10px monospace';
        ctx.textAlign = 'right';
        const tw     = ctx.measureText(label).width;
        const lw = tw + 8, lh = 14;
        const rx = this.width - 84;  // inside axis area
        const ry = y + 2;

        ctx.fillStyle   = '#141c28';
        ctx.strokeStyle = '#2a3a58';
        ctx.lineWidth   = 0.5;
        ctx.fillRect(rx, ry - lh + 2, lw, lh);
        ctx.strokeRect(rx, ry - lh + 2, lw, lh);

        ctx.fillStyle = '#7aa8d8';
        ctx.fillText(label, rx + lw - 4, ry);
    }

    // ═══════════════════════════════════════════════════════════════════════
    // INDICATORS
    // ═══════════════════════════════════════════════════════════════════════

    _drawEMAs() {
        const ctx = this.ctx;
        ctx.setLineDash([]);

        for (const [key, emaList] of Object.entries(this.emaData)) {
            if (!emaList || emaList.length === 0) continue;
            const color = this.colors.ema[key] || '#aaa';

            ctx.strokeStyle = color;
            ctx.lineWidth   = key === 'ema200' ? 1.2 : 1.0;
            ctx.beginPath();
            let first = true;
            let lastVis = null;

            for (const item of emaList) {
                const x = this._timeToX(item.time);
                const y = this._priceToY(item.value);
                if (x < this.chartArea.x - 2 || x > this.chartArea.x + this.chartArea.width + 2) continue;
                if (y < this.chartArea.y     || y > this.chartArea.y + this.chartArea.height) { first = true; continue; }
                if (first) { ctx.moveTo(x, y); first = false; }
                else       { ctx.lineTo(x, y); }
                lastVis = { x, y, value: item.value };
            }
            ctx.stroke();

            // Right-edge EMA label
            if (this.indicatorScaleLabelsEnabled && lastVis) {
                const label = `${key.toUpperCase()} ${lastVis.value.toFixed(1)}`;
                ctx.font      = '9px "Segoe UI", sans-serif';
                ctx.textAlign = 'left';
                ctx.textBaseline = 'middle';
                ctx.fillStyle = color;
                const lx = this.chartArea.x + this.chartArea.width + 5;
                // Tiny colored dot
                ctx.fillRect(lx, lastVis.y - 2, 4, 4);
                ctx.fillText(label, lx + 7, lastVis.y);
            }
        }
    }

    _drawVWAP() {
        if (this.vwapData.length === 0 || !this.currentInterval.includes('minute')) return;
        const ctx = this.ctx;
        ctx.strokeStyle = this.colors.vwap;
        ctx.lineWidth   = 1.5;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        let first = true;

        for (const item of this.vwapData) {
            const x = this._timeToX(item.time);
            const y = this._priceToY(item.value);
            if (x < this.chartArea.x || x > this.chartArea.x + this.chartArea.width) continue;
            if (y < this.chartArea.y || y > this.chartArea.y + this.chartArea.height) { first = true; continue; }
            if (first) { ctx.moveTo(x, y); first = false; } else ctx.lineTo(x, y);
        }
        ctx.stroke();
        ctx.setLineDash([]);

        // Right-edge VWAP label
        const last = this.vwapData[this.vwapData.length - 1];
        if (this.indicatorScaleLabelsEnabled && last) {
            const y = this._priceToY(last.value);
            const lx = this.chartArea.x + this.chartArea.width + 5;
            ctx.font = '9px "Segoe UI", sans-serif';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'middle';
            ctx.fillStyle = this.colors.vwap;
            ctx.fillText(`VWAP ${last.value.toFixed(1)}`, lx, y);
        }
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

        const minGapPx = 26;
        const ticks    = Math.max(6, Math.floor(this.chartArea.height / minGapPx));
        const step     = this._niceStep(priceRange / ticks);
        const minR     = Math.floor(this.minPrice / step) * step;
        const maxR     = Math.ceil(this.maxPrice  / step) * step;
        const decimals = this._priceDecimals(step);

        ctx.font      = this._axisFont(10, 500);
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';

        let lastY = -Infinity;
        for (let p = minR; p <= maxR + step * 0.5; p += step) {
            const y = this._priceToY(p);
            if (y < this.chartArea.y + 6 || y > this.chartArea.y + this.chartArea.height - 6) continue;
            if (Math.abs(y - lastY) < minGapPx) continue;

            // Tick mark
            ctx.strokeStyle = this.colors.grid;
            ctx.lineWidth   = 0.5;
            ctx.beginPath();
            ctx.moveTo(this.chartArea.x + this.chartArea.width,     y);
            ctx.lineTo(this.chartArea.x + this.chartArea.width + 4, y);
            ctx.stroke();

            ctx.fillStyle = this.colors.text;
            ctx.fillText('₹' + p.toFixed(decimals), this.width - 4, y);
            lastY = y;
        }
    }

    _drawTimeAxis() {
        const ctx = this.ctx;
        const tf  = this.currentInterval || 'day';
        const candidates = this._buildTimeCandidates(tf);
        const labelY     = this.volumeArea.y + this.volumeArea.height + 14;

        ctx.font          = this._axisFont(10, 500);
        ctx.textAlign     = 'center';
        ctx.textBaseline  = 'alphabetic';
        ctx.fillStyle     = this.colors.text;

        let lastRight = this.chartArea.x - 9999;

        for (const pt of candidates) {
            const x = this._timeToX(pt.time);
            if (x < this.chartArea.x + 20 || x > this.chartArea.x + this.chartArea.width - 20) continue;
            const w = ctx.measureText(pt.label).width + 8;
            if (x - w / 2 < lastRight + 6) continue;

            ctx.strokeStyle = 'rgba(40,60,90,0.5)';
            ctx.lineWidth   = 0.5;
            ctx.beginPath();
            ctx.moveTo(x, this.chartArea.y);
            ctx.lineTo(x, this.volumeArea.y + this.volumeArea.height);
            ctx.stroke();

            ctx.fillText(pt.label, x, labelY);
            lastRight = x + w / 2;
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

        const ctx = this.ctx;
        const prevClose = this.data.length > 1 ? this.data[this.data.length - 2].close : this.data[0]?.open ?? price;
        const isUp      = price >= prevClose;
        const col       = isUp ? this.colors.upCandle : this.colors.downCandle;

        // Dashed price line
        ctx.strokeStyle = col;
        ctx.lineWidth   = 0.8;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(this.chartArea.x, y);
        ctx.lineTo(this.chartArea.x + this.chartArea.width, y);
        ctx.stroke();
        ctx.setLineDash([]);

        // Price label pill on right axis
        const label  = '₹' + price.toFixed(2);
        ctx.font     = 'bold 10px "Segoe UI Mono", monospace';
        ctx.textAlign = 'left';
        const tw     = ctx.measureText(label).width;
        const lw = tw + 10, lh = 16;
        const lx = this.chartArea.x + this.chartArea.width + 1;
        const ly = y - lh / 2;

        ctx.fillStyle   = col;
        ctx.fillRect(lx, ly, lw, lh);
        ctx.fillStyle   = '#000';
        ctx.textBaseline = 'middle';
        ctx.fillText(label, lx + 5, y);
    }

    // ═══════════════════════════════════════════════════════════════════════
    // CROSSHAIR  (magnetic OHLC snap)
    // ═══════════════════════════════════════════════════════════════════════

    _drawCrosshair() {
        if (this.crosshairX === null || this.isDrawing) return;
        const ctx = this.ctx;
        const x   = this.crosshairX;
        const y   = Math.max(this.chartArea.y, Math.min(this.crosshairY, this.chartArea.y + this.chartArea.height));

        ctx.strokeStyle = this.colors.crosshair;
        ctx.lineWidth   = 0.7;
        ctx.setLineDash([4, 4]);

        // Vertical
        ctx.beginPath();
        ctx.moveTo(x, this.chartArea.y);
        ctx.lineTo(x, this.volumeArea.y + this.volumeArea.height);
        ctx.stroke();

        // Horizontal
        ctx.beginPath();
        ctx.moveTo(this.chartArea.x, y);
        ctx.lineTo(this.chartArea.x + this.chartArea.width, y);
        ctx.stroke();
        ctx.setLineDash([]);

        // Price label on axis
        const price  = this._yToPrice(y);
        const plabel = '₹' + price.toFixed(2);
        ctx.font      = 'bold 10px "Segoe UI Mono", monospace';
        ctx.textAlign = 'left';
        const tw = ctx.measureText(plabel).width;
        const lw = tw + 10, lh = 16;
        const lx = this.chartArea.x + this.chartArea.width + 1;
        const ly = y - lh / 2;
        ctx.fillStyle = '#1a2535';
        ctx.fillRect(lx, ly, lw, lh);
        ctx.strokeStyle = 'rgba(140,170,220,0.4)';
        ctx.lineWidth = 0.5;
        ctx.strokeRect(lx, ly, lw, lh);
        ctx.fillStyle = '#c0d4f0';
        ctx.textBaseline = 'middle';
        ctx.fillText(plabel, lx + 5, y);

        // Time label at bottom
        const ci = this._xToCandle(x);
        if (ci >= 0 && ci < this.data.length) {
            const tlabel = this._fmtTimeLabel(new Date(this.data[ci].time));
            ctx.font      = 'bold 10px "Segoe UI", sans-serif';
            ctx.textAlign = 'center';
            const ttw = ctx.measureText(tlabel).width;
            const tlw = ttw + 10, tlh = 15;
            const tlx = x - tlw / 2;
            const tly = this.volumeArea.y + this.volumeArea.height + 1;
            ctx.fillStyle = '#1a2535';
            ctx.fillRect(tlx, tly, tlw, tlh);
            ctx.strokeStyle = 'rgba(140,170,220,0.3)';
            ctx.lineWidth = 0.5;
            ctx.strokeRect(tlx, tly, tlw, tlh);
            ctx.fillStyle = '#c0d4f0';
            ctx.textBaseline = 'middle';
            ctx.fillText(tlabel, x, tly + tlh / 2);
        }
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
        const fontSize = this.watermark.fontSize > 0
            ? this.watermark.fontSize
            : Math.max(32, Math.round(this.chartArea.width * 0.08));

        ctx.save();
        ctx.globalAlpha  = this.watermark.opacity;
        ctx.fillStyle    = this.watermark.color;
        ctx.textAlign    = 'center';
        ctx.textBaseline = 'middle';
        ctx.font         = `700 ${fontSize}px "Segoe UI", sans-serif`;
        ctx.fillText(this.currentSymbol, this.chartArea.x + this.chartArea.width / 2,
                     yMap[this.watermark.position] || yMap.mid_center);
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
            ctx.strokeStyle = line.color || '#FFD700';
            ctx.lineWidth   = sel ? (line.lineWidth || 1.5) + 1 : (line.lineWidth || 1.5);
            ctx.setLineDash(line.style === 'dashed' ? [6, 4] : []);
            ctx.beginPath();
            ctx.moveTo(this.chartArea.x, y);
            ctx.lineTo(this.chartArea.x + this.chartArea.width, y);
            ctx.stroke();
            ctx.setLineDash([]);

            if (line.label) {
                ctx.font      = '10px "Segoe UI", sans-serif';
                ctx.textAlign = 'right';
                ctx.fillStyle = line.color || '#FFD700';
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
            ctx.strokeStyle = ray.color || '#FFD700';
            ctx.lineWidth   = sel ? 2.5 : 1.5;
            ctx.setLineDash([5, 3]);
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
            ctx.strokeStyle = line.color || '#FFD700';
            ctx.lineWidth   = sel ? (line.lineWidth || 1.5) + 1 : (line.lineWidth || 1.5);
            ctx.setLineDash([]);
            ctx.beginPath();
            ctx.moveTo(sx, sy);
            ctx.lineTo(ex, ey);
            ctx.stroke();
            this._drawHandles(sx, sy, ex, ey, sel, line.color || '#FFD700');
        }
    }

    _drawArrowLines() {
        const ctx = this.ctx;
        for (const arrow of this.drawings.arrow_lines) {
            const sx = this._timeToX(arrow.startTime), sy = this._priceToY(arrow.startPrice);
            const ex = this._timeToX(arrow.endTime),   ey = this._priceToY(arrow.endPrice);
            if (!this._lineVisible(sx, sy, ex, ey)) continue;
            ctx.strokeStyle = arrow.color || '#FFD700';
            ctx.fillStyle   = arrow.color || '#FFD700';
            ctx.lineWidth   = arrow.lineWidth || 1.5;
            ctx.setLineDash([]);
            ctx.beginPath();
            ctx.moveTo(sx, sy);
            ctx.lineTo(ex, ey);
            ctx.stroke();
            this._drawArrowhead(sx, sy, ex, ey, arrow.color || '#FFD700');
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
            ctx.fillStyle   = this._hexToRgba(rect.color || '#FFD700', 0.08);
            ctx.fillRect(x, y, w, h);
            ctx.strokeStyle = rect.color || '#FFD700';
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

            FIB_LEVELS.forEach((level, idx) => {
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
                if (idx < FIB_LEVELS.length - 1) {
                    const nextPrice = fib.endPrice + priceRange * FIB_LEVELS[idx + 1];
                    const nextY = this._priceToY(nextPrice);
                    ctx.fillStyle = this._hexToRgba(col, 0.04);
                    ctx.fillRect(Math.min(sx, ex), Math.min(y, nextY),
                                 Math.abs(ex - sx), Math.abs(nextY - y));
                }

                // Label
                ctx.font      = '9px "Segoe UI Mono", monospace';
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

            ctx.font      = `${note.size || 12}px "Segoe UI", sans-serif`;
            ctx.fillStyle = note.color || '#FFD700';
            ctx.textAlign = 'left';
            ctx.textBaseline = 'top';

            // Background pill
            const tw = ctx.measureText(note.text).width;
            ctx.fillStyle = 'rgba(15,20,32,0.75)';
            ctx.fillRect(x + 2, y - 14, tw + 6, (note.size || 12) + 6);
            ctx.fillStyle = note.color || '#FFD700';
            ctx.fillText(note.text, x + 5, y - 12);

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
        ctx.strokeStyle = '#fff';
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
        const boxW = textWidth + 20;
        const boxH = 44;

        let boxX = ex + 12;
        let boxY = ey;
        const rightEdge = this.chartArea.x + this.chartArea.width;
        const bottomEdge = this.chartArea.y + this.chartArea.height;
        if (boxX + boxW > rightEdge) boxX = ex - boxW - 12;
        if (boxY + boxH > bottomEdge) boxY = bottomEdge - boxH;
        if (boxY < this.chartArea.y) boxY = this.chartArea.y;

        ctx.fillStyle = 'rgba(12, 16, 24, 0.92)';
        ctx.strokeStyle = this.drawingColor;
        ctx.lineWidth = 1;
        ctx.fillRect(boxX, boxY, boxW, boxH);
        ctx.strokeRect(boxX, boxY, boxW, boxH);

        ctx.fillStyle = '#e8effa';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(infoText[0], boxX + 10, boxY + 14);
        ctx.fillText(infoText[1], boxX + 10, boxY + 31);
        ctx.restore();
    }

    // ═══════════════════════════════════════════════════════════════════════
    // EVENT HANDLING
    // ═══════════════════════════════════════════════════════════════════════

    _setupEventListeners() {
        this.canvas.addEventListener('mousemove',   e => this._onMouseMove(e));
        this.canvas.addEventListener('mousedown',   e => this._onMouseDown(e));
        this.canvas.addEventListener('mouseup',     e => this._onMouseUp(e));
        this.canvas.addEventListener('mouseleave',  e => this._onMouseLeave(e));
        this.canvas.addEventListener('wheel',       e => this._onWheel(e), { passive: false });
        this.canvas.addEventListener('contextmenu', e => this._onRightClick(e));

        document.addEventListener('keydown', e => {
            if (e.key === 'Escape') this._clearTool();
            if (e.key === 'Delete' && this.selectedDrawingId) this._deleteSelected();
        });
    }

    _onMouseMove(e) {
        const pos = this._mousePos(e);

        // ── Panning ──
        if (this.isDragging) {
            const dx = pos.x - this.lastMouseX;
            const visCount = this.viewPortEnd - this.viewPortStart + 1;
            const shift = Math.round(dx / (this.chartArea.width / visCount));
            if (shift !== 0) {
                this.viewPortStart = Math.max(0, this.viewPortStart - shift);
                this.viewPortEnd   = this.viewPortStart + visCount - 1;
                this.calculateBounds();
                this.updateSlider();
                this.lastMouseX = pos.x;
            }
            this.requestDraw();
            return;
        }

        // ── Drawing in-progress ──
        if (this.isDrawing && this.startPoint) {
            this.endPoint = pos;
            this.crosshairX = pos.x;
            this.crosshairY = pos.y;
            this.requestDraw();
            return;
        }

        // ── Crosshair + OHLC display ──
        const inChart = pos.x >= this.chartArea.x &&
                        pos.x <= this.chartArea.x + this.chartArea.width &&
                        pos.y >= this.chartArea.y &&
                        pos.y <= this.volumeArea.y + this.volumeArea.height;

        if (inChart) {
            this.crosshairX = pos.x;
            this.crosshairY = pos.y;
            this._updateCandleDetail(pos.x);
            this.canvas.style.cursor = this.currentTool ? 'crosshair' : 'default';
        } else {
            this.crosshairX = null;
            this.crosshairY = null;
            this._displayLatestCandleDetails();
            this.canvas.style.cursor = 'default';
        }
        this.requestDraw();
    }

    _onMouseDown(e) {
        if (e.button !== 0) return;
        const pos = this._mousePos(e);

        if (this.currentTool) {
            this.isDrawing  = true;
            this.startPoint = { x: pos.x, y: pos.y, time: this._xToTime(pos.x), price: this._yToPrice(pos.y) };
            this.endPoint   = pos;
        } else {
            this.isDragging = true;
            this.lastMouseX = pos.x;
            this.canvas.style.cursor = 'grabbing';
            // Selection hit-test
            const hit = this._hitTest(pos);
            this.selectedDrawingId = hit;
            this.requestDraw();
        }
    }

    _onMouseUp(e) {
        if (e.button !== 0) return;
        const pos = this._mousePos(e);

        if (this.isDrawing && this.startPoint) {
            this._finalizeDrawing(pos);
        }

        this.isDragging = false;
        this.isDrawing  = false;
        this.canvas.style.cursor = this.currentTool ? 'crosshair' : 'default';
    }

    _onMouseLeave() {
        this.isDragging = false;
        this.crosshairX = null;
        this.crosshairY = null;
        this._displayLatestCandleDetails();
        this.requestDraw();
    }

    _onWheel(e) {
        e.preventDefault();
        const delta = e.deltaY || e.deltaX;
        const zoomIn = delta < 0;
        const visCount = this.viewPortEnd - this.viewPortStart + 1;
        const factor = zoomIn ? 0.88 : 1.12;
        const newCount = Math.max(20, Math.min(this.data.length + this.rightBufferCandles,
                                               Math.round(visCount * factor)));
        if (newCount === visCount) return;

        // Zoom around mouse position
        const pos   = this._mousePos(e);
        const frac  = (pos.x - this.chartArea.x) / this.chartArea.width;
        const anchor = this.viewPortStart + frac * visCount;
        const newStart = Math.max(0, Math.round(anchor - frac * newCount));

        this.viewPortStart      = Math.min(newStart, this.data.length + this.rightBufferCandles - newCount);
        this.viewPortEnd        = this.viewPortStart + newCount - 1;
        this.visibleCandleCount = newCount;

        this.calculateBounds();
        this.requestDraw();
        this.updateSlider();

        clearTimeout(this._zoomTimer);
        this._zoomTimer = setTimeout(() => this._notifyZoomChange(), 300);
    }

    _onRightClick(e) {
        e.preventDefault();
        const pos = this._mousePos(e);
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
            { text: '💰 Place Order', sub: 'Quick order entry',
              action: () => this._placeOrderAtPrice(sym, priceLevel) },
            { divider: true },
            { text: `➡ H-Line at ₹${priceLevel.toFixed(2)}`,
              action: () => this._addHLine(priceLevel) },
            { text: isAbove ? '🟢 Resistance Line' : '🔴 Support Line',
              action: () => this._addNamedHLine(priceLevel, isAbove ? 'Resistance' : 'Support', isAbove ? '#ef5350' : '#26a69a') },
            { text: '📐 Fibonacci from here',
              action: () => { this.setDrawingTool('fibonacci', true); } },
        ];

        const menu = document.createElement('div');
        menu.style.cssText = `
            position: fixed; left: ${clientX}px; top: ${clientY}px;
            background: #0f1420; border: 1px solid #1e2840;
            border-radius: 6px; padding: 6px 0; z-index: 99999;
            box-shadow: 0 8px 24px rgba(0,0,0,0.6);
            font-family: "Segoe UI", sans-serif; font-size: 12px;
            color: #c8d4e8; min-width: 200px; user-select: none;`;

        items.forEach(item => {
            if (item.divider) {
                const d = document.createElement('div');
                d.style.cssText = 'height:1px; background:#1e2840; margin:4px 0;';
                menu.appendChild(d); return;
            }
            const mi = document.createElement('div');
            mi.style.cssText = `
                padding: 7px 16px; cursor: pointer;
                ${item.highlight ? 'background:rgba(50,100,200,0.12);' : ''}`;

            mi.innerHTML = `
                <div style="font-weight:${item.highlight ? '600' : '500'};
                     color:${item.highlight ? '#90b8ff' : '#c8d4e8'};">${item.text}</div>
                ${item.sub ? `<div style="font-size:10px;color:#5a7090;margin-top:2px;">${item.sub}</div>` : ''}`;

            mi.addEventListener('mouseenter', () => mi.style.background = item.highlight ? 'rgba(50,100,200,0.22)' : '#161e30');
            mi.addEventListener('mouseleave', () => mi.style.background = item.highlight ? 'rgba(50,100,200,0.12)' : 'transparent');
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
            price, color: '#FFD700', lineWidth: 1.5, style: 'solid',
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
        const payload = JSON.stringify({ symbol, price, type: alertType,
                                         condition: price > (this.livePrice || 0) ? 'above' : 'below' });
        if (this.chartBridge) this.chartBridge.notify_alert_creation_requested(payload);
    }

    _placeOrderAtPrice(symbol, price) {
        const payload = JSON.stringify({ symbol, price, ltp: this.livePrice });
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
            this.calculateBounds();
            this.requestDraw();
        });

        document.addEventListener('mouseup', () => { dragging = false; });
    }

    updateSlider() {
        if (!this.sliderThumb || !this.sliderTrack) return;
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
        const start = Math.max(0, this.viewPortStart);
        const end   = Math.min(this.data.length - 1, this.viewPortEnd);
        const slice = this.data.slice(start, end + 1);
        if (slice.length === 0) return;

        this.minPrice = Math.min(...slice.map(d => d.low));
        this.maxPrice = Math.max(...slice.map(d => d.high));

        // Include EMA values in price range
        const firstT = this.data[start]?.time;
        const lastT  = this.data[end]?.time;
        for (const emaList of Object.values(this.emaData)) {
            for (const item of emaList) {
                if (item.time >= firstT && item.time <= lastT) {
                    this.minPrice = Math.min(this.minPrice, item.value);
                    this.maxPrice = Math.max(this.maxPrice, item.value);
                }
            }
        }

        // Include live price
        if (this.livePrice !== null) {
            this.minPrice = Math.min(this.minPrice, this.livePrice);
            this.maxPrice = Math.max(this.maxPrice, this.livePrice);
        }

        const range = this.maxPrice - this.minPrice;
        if (range === 0) { this.minPrice -= 1; this.maxPrice += 1; }
        else { this.minPrice -= range * 0.04; this.maxPrice += range * 0.06; }

        const prevAxisWidth = this.rightAxisWidth || 0;
        this._updateChartAreas();

        // One more pass when width changes because candle spacing alters bounds slightly.
        if (Math.abs((this.rightAxisWidth || 0) - prevAxisWidth) > 0.5) {
            this._updateChartAreas();
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    // COORDINATE TRANSFORMS
    // ═══════════════════════════════════════════════════════════════════════

    _priceToY(price) {
        const ratio = (price - this.minPrice) / (this.maxPrice - this.minPrice);
        return this.chartArea.y + this.chartArea.height - ratio * this.chartArea.height;
    }

    _yToPrice(y) {
        const ratio = (this.chartArea.y + this.chartArea.height - y) / this.chartArea.height;
        return this.minPrice + ratio * (this.maxPrice - this.minPrice);
    }

    _candleToX(index) {
        const vis = this.viewPortEnd - this.viewPortStart + 1;
        const space = this.chartArea.width / vis;
        return this.chartArea.x + (index - this.viewPortStart) * space;
    }

    _xToCandle(x) {
        const vis = this.viewPortEnd - this.viewPortStart + 1;
        const space = this.chartArea.width / vis;
        if (space <= 0) return -1;
        return this.viewPortStart + Math.floor((x - this.chartArea.x) / space);
    }

    _timeToX(time) {
        let idx = this.data.findIndex(d => d.time >= time);
        if (idx === -1) {
            const last = this.data.length - 1;
            if (last < 0) return this.chartArea.x;
            return Math.min(this._candleToX(last) + this.candleWidth,
                            this.chartArea.x + this.chartArea.width);
        }
        if (idx === 0 && time < this.data[0].time) return this.chartArea.x;
        return this._candleToX(idx);
    }

    _xToTime(x) {
        const idx = this._xToCandle(x);
        if (idx >= 0 && idx < this.data.length) return this.data[idx].time;
        if (this.data.length === 0) return Date.now();
        const last  = this.data[this.data.length - 1].time;
        const first = this.data[0].time;
        if (idx >= this.data.length) {
            const avg = (last - first) / Math.max(1, this.data.length - 1);
            return last + avg * (idx - (this.data.length - 1));
        }
        return first;
    }

    _mousePos(e) {
        const r = this.canvas.getBoundingClientRect();
        return { x: e.clientX - r.left, y: e.clientY - r.top };
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
            if (this._nearLine(pos.x, pos.y, sx, sy, ex, ey, tol)) return line.id;
        }
        for (const hl of this.drawings.horizontal_lines) {
            if (Math.abs(pos.y - this._priceToY(hl.price)) <= tol) return hl.id;
        }
        for (const hr of this.drawings.horizontal_rays) {
            const sx = this._timeToX(hr.startTime), y = this._priceToY(hr.startPrice);
            if (Math.abs(pos.y - y) <= tol && pos.x >= sx - tol) return hr.id;
        }
        for (const arrow of this.drawings.arrow_lines) {
            const sx = this._timeToX(arrow.startTime), sy = this._priceToY(arrow.startPrice);
            const ex = this._timeToX(arrow.endTime),   ey = this._priceToY(arrow.endPrice);
            if (this._nearLine(pos.x, pos.y, sx, sy, ex, ey, tol)) return arrow.id;
        }
        for (const rect of this.drawings.rectangles) {
            const sx = this._timeToX(rect.startTime), sy = this._priceToY(rect.startPrice);
            const ex = this._timeToX(rect.endTime),   ey = this._priceToY(rect.endPrice);
            const x = Math.min(sx,ex), y = Math.min(sy,ey), w = Math.abs(ex-sx), h = Math.abs(ey-sy);
            if (pos.x>=x-tol && pos.x<=x+w+tol && pos.y>=y-tol && pos.y<=y+h+tol) return rect.id;
        }
        for (const note of this.drawings.notes) {
            const nx = this._timeToX(note.time), ny = this._priceToY(note.price);
            if (Math.abs(pos.x - nx) <= tol && Math.abs(pos.y - ny) <= tol) return note.id;
        }
        return null;
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

    _updateCandleDetail(x) {
        const idx = this._xToCandle(x);
        if (idx < 0 || idx >= this.data.length) { this._displayLatestCandleDetails(); return; }
        const c      = this.data[idx];
        const change = c.close - c.open;
        const pct    = c.open !== 0 ? ((change / c.open) * 100).toFixed(2) : '0.00';
        const sign   = change >= 0 ? '+' : '';
        const chStr  = `${sign}₹${change.toFixed(2)} (${sign}${pct}%)`;
        const dateStr = this._fmtTimeLabel(new Date(c.time));
        const el = document.getElementById('priceInfo');
        if (el) el.textContent =
            `${dateStr}  O:₹${c.open.toFixed(2)}  H:₹${c.high.toFixed(2)}  L:₹${c.low.toFixed(2)}  C:₹${c.close.toFixed(2)}  ${chStr}`;
    }

    _displayLatestCandleDetails() {
        const el = document.getElementById('priceInfo');
        if (!el) return;
        if (this.data.length === 0) { el.textContent = 'No data'; return; }
        const c = this.data[this.data.length - 1];
        const change = c.close - c.open;
        const pct    = c.open !== 0 ? ((change / c.open) * 100).toFixed(2) : '0.00';
        const sign   = change >= 0 ? '+' : '';
        const dateStr = new Date(c.time).toLocaleDateString('en-GB', { day:'2-digit', month:'short', year:'numeric' });
        el.textContent = `${dateStr}  O:₹${c.open.toFixed(2)}  H:₹${c.high.toFixed(2)}  L:₹${c.low.toFixed(2)}  C:₹${c.close.toFixed(2)}  ${sign}₹${change.toFixed(2)} (${sign}${pct}%)`;
    }

    _updateMetricsDisplay() {
        const el = document.getElementById('metricsInfo');
        if (!el) return;
        const adrStr = this.currentADR?.value > 0
            ? `ADR ₹${this.currentADR.value.toFixed(2)} (${this.currentADR.percent.toFixed(2)}%)`
            : 'ADR N/A';
        const changes = ['Weekly','Monthly','3M','6M','1Y'].map(p => {
            const v = this.percentageChanges?.[p];
            if (v == null) return `<span style="color:#5a7090">${p} N/A</span>`;
            const col = v >= 0 ? '#26a69a' : '#ef5350';
            return `<span style="color:${col}">${p}: ${v >= 0 ? '+' : ''}${v.toFixed(2)}%</span>`;
        });
        el.innerHTML = `${adrStr}&ensp;|&ensp;${changes.join('&ensp;|&ensp;')}`;
    }

    // ═══════════════════════════════════════════════════════════════════════
    // PUBLIC API  (called from Python via runJavaScript)
    // ═══════════════════════════════════════════════════════════════════════

    setDrawingTool(toolId, active, color, lw) {
        if (!active) { this._clearTool(); return; }
        this.currentTool  = toolId;
        this.drawingColor = color || this.drawingColor;
        this.lineWidth    = lw    || this.lineWidth;
        this.canvas.style.cursor = 'crosshair';
    }

    _clearTool() {
        const hadActiveTool = Boolean(this.currentTool);
        this.currentTool = null;
        this.isDrawing   = false;
        this.startPoint  = null;
        this.endPoint    = null;
        this.canvas.style.cursor = 'default';
        if (hadActiveTool) {
            this._notifyDrawingToolCleared();
        }
    }

    updateLivePrice(price) {
        this.livePrice = price;
        if (this.data.length > 0) {
            const last = this.data[this.data.length - 1];
            last.close = price;
            last.high  = Math.max(last.high, price);
            last.low   = Math.min(last.low,  price);
        }
        this.calculateBounds();
        this.requestDraw();
    }

    addNewCandle(candle) {
        this.data.push(candle);
        this.volumeData.push({ time: candle.time, value: candle.volume || 0 });
        this.viewPortEnd = this.data.length - 1 + this.rightBufferCandles;
        if (this.visibleCandleCount) {
            this.viewPortStart = Math.max(0, this.viewPortEnd - this.visibleCandleCount + 1);
        }
        this._computeVWAP();
        this.calculateBounds();
        this.requestDraw();
        this.updateSlider();
    }

    setVisibleCandleCount(count) {
        this.visibleCandleCount = count;
        this.viewPortEnd   = Math.max(0, this.data.length - 1 + this.rightBufferCandles);
        this.viewPortStart = Math.max(0, this.viewPortEnd - count + 1);
        this.calculateBounds();
        this.requestDraw();
        this.updateSlider();
    }

    setChartSettings(cfg) {
        if (cfg.upCandleColor)   this.colors.upCandle   = cfg.upCandleColor;
        if (cfg.downCandleColor) this.colors.downCandle = cfg.downCandleColor;
        if (cfg.upVolumeColor)   this.colors.volumeUp   = cfg.upVolumeColor;
        if (cfg.downVolumeColor) this.colors.volumeDown = cfg.downVolumeColor;
        if (cfg.candleWidth)     this.candleWidth    = cfg.candleWidth;
        if (cfg.candleSpacing)   this.candleSpacing  = cfg.candleSpacing;
        if (cfg.watermarkEnabled  !== undefined) this.watermark.enabled  = cfg.watermarkEnabled;
        if (cfg.watermarkColor)   this.watermark.color   = cfg.watermarkColor;
        if (cfg.watermarkOpacity  !== undefined) this.watermark.opacity  = cfg.watermarkOpacity;
        if (cfg.watermarkPosition) this.watermark.position = cfg.watermarkPosition;
        if (cfg.watermarkFontSize !== undefined) this.watermark.fontSize = cfg.watermarkFontSize;
        if (cfg.indicatorScaleLabelsEnabled !== undefined) this.indicatorScaleLabelsEnabled = cfg.indicatorScaleLabelsEnabled === true;
        this.requestDraw();
    }

    updateDrawingStyle(color, lw) {
        this.drawingColor = color || this.drawingColor;
        this.lineWidth    = lw    || this.lineWidth;
    }

    addTextNoteFromDialog(note) {
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

    autoScale()             { this.calculateBounds(); this.requestDraw(); this.updateSlider(); }
    getAllDrawings()         { return this.drawings; }
    getVisibleCandleCount() { return this.visibleCandleCount; }

    // ═══════════════════════════════════════════════════════════════════════
    // NOTIFICATIONS TO PYTHON
    // ═══════════════════════════════════════════════════════════════════════

    _notifyDrawingsChange() {
        if (!this.chartBridge || !this.webChannelInitialized) {
            this._notifyQueue.push(() => this._notifyDrawingsChange());
            this._scheduleFlush();
            return;
        }
        try { this.chartBridge.notify_drawings_changed(JSON.stringify(this.drawings)); }
        catch (e) { console.error('notify_drawings_changed error:', e); }
    }

    _notifyZoomChange() {
        if (!this.chartBridge || !this.webChannelInitialized) return;
        try { this.chartBridge.notify_zoom_changed(this.visibleCandleCount); }
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
        return `${weight} ${size}px "Segoe UI", "Helvetica Neue", sans-serif`;
    }

    _fmtVol(vol) {
        if (vol >= 1e7) return (vol / 1e7).toFixed(1) + 'Cr';
        if (vol >= 1e5) return (vol / 1e5).toFixed(1) + 'L';
        if (vol >= 1e3) return (vol / 1e3).toFixed(0) + 'K';
        return String(vol);
    }

    _fmtTimeLabel(date) {
        const now       = new Date();
        const isSameDay = date.toDateString() === now.toDateString();
        if (this.currentInterval.includes('minute')) {
            const time = date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
            return isSameDay ? time : `${date.toLocaleDateString('en-GB',{day:'2-digit',month:'short'})} ${time}`;
        }
        const daysDiff = Math.floor((now - date) / 86400000);
        return date.toLocaleDateString('en-GB', {
            day: '2-digit', month: 'short', year: daysDiff > 330 ? 'numeric' : undefined
        });
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
        const start = Math.max(0, this.viewPortStart);
        const end   = Math.min(this.data.length - 1, this.viewPortEnd);

        for (let i = start; i <= end; i++) {
            const d     = new Date(this.data[i].time);
            const label = this._timeCandidateLabel(d, tf);
            if (label) candidates.push({ time: this.data[i].time, label });
        }
        return candidates;
    }

    _timeCandidateLabel(d, tf) {
        const m = d.getMinutes(), h = d.getHours(), dom = d.getDate(), dow = d.getDay(), mon = d.getMonth();
        if (tf === 'minute')   return m % 15 === 0 ? `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}` : null;
        if (tf === '3minute')  return m % 30 === 0 ? `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}` : null;
        if (tf === '5minute')  return m % 30 === 0 ? `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}` : null;
        if (tf === '15minute') return h % 2 === 0 && m === 0 ? `${String(h).padStart(2,'0')}:00` : null;
        if (tf === '30minute') return m === 0 ? `${String(h).padStart(2,'0')}:00` : null;
        if (tf === '60minute') return h === 9  ? d.toLocaleDateString('en-GB',{day:'2-digit',month:'short'}) : null;
        if (tf === 'day')      return dow === 1 ? d.toLocaleDateString('en-GB',{day:'2-digit',month:'short'}) : null;
        if (tf === 'week')     return mon % 3 === 0 && dom <= 7 ? d.toLocaleDateString('en-GB',{month:'short',year:'2-digit'}) : null;
        if (tf === 'month')    return mon === 0 ? String(d.getFullYear()) : null;
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
        const el = document.getElementById('priceInfo');
        if (el) el.textContent = 'Error: ' + e.message;
    }
}

document.addEventListener('DOMContentLoaded', initChart);
if (document.readyState === 'interactive' || document.readyState === 'complete') initChart();
setTimeout(initChart, 100);
