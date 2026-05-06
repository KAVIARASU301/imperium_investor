/**
 * drawing_engine_integration.patch.js
 *
 * How to wire DrawingEngine into your existing chart.js / FixedTradingChart.
 * Apply these changes — they replace the old manual drawing code.
 *
 * Key changes:
 *   1.  Remove old drawing methods from FixedTradingChart
 *   2.  Add DrawingEngine instance + delegate calls
 *   3.  chart.js render() calls drawingEngine.render() instead of _drawAllDrawings()
 *   4.  Mouse events are delegated to DrawingEngine first; chart panning second
 */

'use strict';

/* ══════════════════════════════════════════════════════════════════════════════
   STEP 1 — In FixedTradingChart.constructor(), REPLACE the drawing state block:

   OLD:
     this.drawings = this._initDrawings(cfg.initialDrawingsJson);
     this.currentTool = null;
     this.isDrawing = false;
     ... (many fields)

   NEW (add inside constructor, after canvas setup): */

function patchConstructor(chart, cfg) {
    /* Build coordinate-system delegate (adapts FixedTradingChart to DrawingEngine API) */
    const coordSys = makeCoordSys(chart);

    chart.drawingEngine = new DrawingEngine(chart.canvas, coordSys);

    /* Wire callbacks */
    chart.drawingEngine.onDrawingsChanged = () => {
        chart._notifyDrawingsChange();
    };
    chart.drawingEngine.onRequestTextNote = (pos) => {
        if (chart.chartBridge) {
            chart.chartBridge.notify_text_note_requested(JSON.stringify(pos));
        }
    };

    /* Load saved drawings */
    if (cfg.initialDrawingsJson) {
        chart.drawingEngine.deserialize(cfg.initialDrawingsJson);
    }

    /* Compat shim: keep this.drawings as a proxy so existing code doesn't break */
    chart.drawings = new DrawingsCompat(chart.drawingEngine);
}

/* ══════════════════════════════════════════════════════════════════════════════
   STEP 2 — CoordSys adapter (FixedTradingChart already has all these methods) */

function makeCoordSys(chart) {
    return {
        get chartArea()       { return chart.chartArea; },
        get data()            { return chart.data; },
        get candleWidth()     { return chart.candleWidth; },
        get rightAxisWidth()  { return chart.rightAxisWidth; },
        priceToY(price)       { return chart._priceToY(price); },
        yToPrice(y)           { return chart._yToPrice(y); },
        timeToX(time)         { return chart._timeToX(time); },
        xToTime(x)            { return chart._xToTime_coord(x); },
        candleToX(idx)        { return chart._candleToX(idx); },
        xToCandle(x)          { return chart._xToCandle_coord(x); },
    };
}

/* ══════════════════════════════════════════════════════════════════════════════
   STEP 3 — Replace draw() call:

   In FixedTradingChart.draw(), replace:
     this._drawAllDrawings();
   with:
     this.drawingEngine.render();
*/

/* ══════════════════════════════════════════════════════════════════════════════
   STEP 4 — Replace mouse event handlers.

   The old _onMouseMove / _onMouseDown / _onMouseUp had interleaved drawing +
   panning logic. Split them cleanly: drawing engine gets first crack, then
   chart handles pan/zoom if the engine didn't consume the event.

   REPLACE FixedTradingChart._setupEventListeners():
*/

function patchEventListeners(chart) {
    const canvas = chart.canvas;

    /* Remove all existing listeners by cloning the node */
    const fresh = canvas.cloneNode(true);
    canvas.parentNode.replaceChild(fresh, canvas);
    chart.canvas = fresh;
    chart.ctx = fresh.getContext('2d');
    chart.drawingEngine.canvas = fresh;
    chart.drawingEngine.ctx    = fresh.getContext('2d');

    /* Re-bind DrawingEngine first */
    chart.drawingEngine._bindEvents();

    /* Chart-level handlers (panning, zoom, crosshair) */
    fresh.addEventListener('mousemove', e => chartOnMove(chart, e));
    fresh.addEventListener('mousedown', e => chartOnDown(chart, e));
    fresh.addEventListener('mouseup',   e => chartOnUp(chart, e));
    fresh.addEventListener('mouseleave',() => chartOnLeave(chart));
    fresh.addEventListener('wheel',     e => chart._onWheel(e), { passive: false });
    fresh.addEventListener('contextmenu', e => chartOnContextMenu(chart, e));

    /* Keyboard — chart keeps its shortcuts, engine has its own listener */
    document.addEventListener('keydown', e => chartOnKey(chart, e));
}

function chartOnMove(chart, e) {
    const engine = chart.drawingEngine;
    /* If engine has an active tool or is dragging a handle, skip chart panning */
    if (engine.activeTool || engine.activeHandle) {
        chart.crosshairX = null; chart.crosshairY = null;
        chart.requestDraw();
        return;
    }

    const pos = chart._mousePos(e);
    const inChart = pos.x >= chart.chartArea.x &&
                    pos.x <= chart.chartArea.x + chart.chartArea.width &&
                    pos.y >= chart.chartArea.y &&
                    pos.y <= chart.chartArea.y + chart.chartArea.height;

    if (chart.isDragging && e.buttons === 1) {
        const dx    = pos.x - chart.lastMouseX;
        const shift = Math.round(dx / chart._slotW());
        if (shift !== 0) {
            const vis = chart.visibleCandleCount;
            chart.viewPortStart = Math.max(0, chart.viewPortStart - shift);
            chart.viewPortEnd   = chart.viewPortStart + vis - 1;
            chart.calculateBounds();
            chart.updateSlider();
            chart.lastMouseX = pos.x;
            engine.rebuildSpatialHash();   // ← important: coords changed
        }
    } else if (inChart) {
        chart.crosshairX = pos.x;
        const ci = chart._xToCandle(pos.x);
        const isPrice = pos.y >= chart.chartArea.y && pos.y <= chart.chartArea.y + chart.chartArea.height;
        chart.crosshairY = isPrice ? chart._snapCrosshairY(pos.y, ci) : pos.y;
        chart._updateCandleDetail(pos.x);
    } else {
        chart.crosshairX = null; chart.crosshairY = null;
    }

    chart.lastMouseX = pos.x;
    chart.requestDraw();
}

function chartOnDown(chart, e) {
    if (e.button !== 0) return;
    const pos = chart._mousePos(e);
    /* Engine already processed onDown — only start panning if no hit */
    if (!chart.drawingEngine.activeTool && !chart.drawingEngine.activeHandle &&
        !chart.drawingEngine.hoverId) {
        chart.isDragging = true;
        chart.lastMouseX = pos.x;
        chart.canvas.style.cursor = 'grabbing';
    }
}

function chartOnUp(chart, e) {
    if (e.button !== 0) return;
    chart.isDragging = false;
    chart.canvas.style.cursor = chart.drawingEngine.activeTool ? 'crosshair' : 'default';
}

function chartOnLeave(chart) {
    chart.isDragging = false;
    chart.crosshairX = null; chart.crosshairY = null;
    chart._displayLatestCandleDetails();
    chart.requestDraw();
}

function chartOnContextMenu(chart, e) {
    /* Engine context menu on drawings; chart context menu on empty space */
    const pos = chart._mousePos(e);
    const hit = chart.drawingEngine._hitTest(pos.x, pos.y);
    if (hit) return; /* engine handles it */
    e.preventDefault();
    const price = chart._yToPrice(pos.y);
    chart._showContextMenu(e.clientX, e.clientY, price);
}

function chartOnKey(chart, e) {
    const tag = document.activeElement?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;
    /* Ctrl+Z / Ctrl+Y handled by DrawingEngine */
    if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z' || e.key === 'y' || e.key === 'Y')) return;
    /* F5 = refresh */
    if (e.key === 'F5') { chart._force_refresh?.(); return; }
    if (e.key === 'Escape') { chart._clearTool?.(); }
}

/* ══════════════════════════════════════════════════════════════════════════════
   STEP 5 — Public API shims (so Python QWebChannel calls still work)

   Replace FixedTradingChart.setDrawingTool / _clearTool / clearAllDrawings /
   getAllDrawings / updateDrawingStyle with these shims:
*/

function installPublicApiShims(chart) {
    const eng = chart.drawingEngine;
    const TOOL_ALIASES = {
        arrow_line: 'arrow',
    };

    chart.setDrawingTool = (toolId, active, color, lw) => {
        if (!active) {
            chart._clearTool();
            return;
        }

        // Measure is a transient "hold + drag" overlay handled by chart.js,
        // not a persisted DrawingEngine drawing.
        if (toolId === 'measure') {
            eng.clearTool();
            chart.currentTool = 'measure';
            chart.canvas.style.cursor = 'crosshair';
            return;
        }

        // Any non-measure drawing tool should exit transient measure mode first.
        chart.currentTool = null;
        chart._isMeasuring = false;
        chart._measureStart = null;
        chart._measureEnd = null;
        eng.setTool(TOOL_ALIASES[toolId] || toolId, color, lw);
    };

    chart._clearTool = () => {
        const hadMeasureTool = Boolean(chart.currentTool);
        const hadEngineTool  = Boolean(eng.activeTool);

        chart.currentTool = null;
        chart._isMeasuring = false;
        chart._measureStart = null;
        chart._measureEnd = null;

        eng.clearTool();
        chart.canvas.style.cursor = 'default';

        if (hadMeasureTool || hadEngineTool) {
            chart._notifyDrawingToolCleared();
        }
    };

    chart.clearAllDrawings = () => {
        eng.clearAll();
        chart._notifyDrawingsChange();
    };

    chart.getAllDrawings = () => {
        /* Return in legacy format for Python storage compatibility */
        return legacySerialize(eng.getDrawings());
    };

    chart.updateDrawingStyle = (color, lw) => {
        eng.drawingColor = color || eng.drawingColor;
        eng.lineWidth    = lw    || eng.lineWidth;
        if (eng.selectedId) {
            eng.updateDrawing(eng.selectedId, { color, lineWidth: lw });
        }
    };

    chart.updateDrawings = (drawingsObj) => {
        /* Called from Python when loading a saved state */
        const all = legacyDeserialize(drawingsObj);
        eng.drawings.clear();
        eng.spatialHash.clear();
        for (const d of all) {
            eng.drawings.set(d.id, d);
            eng._hashInsert(d);
        }
        chart.requestDraw();
    };

    chart.addTextNoteFromDialog = (note) => {
        eng.addDrawing({
            type: 'note',
            startPrice: chart._yToPrice(note.y),
            startTime:  chart._xToTime(note.x),
            text:  note.text,
            color: note.color,
            fontSize: note.size || 12,
        });
    };

    chart.updateTextNote = (note) => {
        eng.updateDrawing(String(note.id), {
            text: note.text, color: note.color, fontSize: note.size,
        });
    };
}

/* ══════════════════════════════════════════════════════════════════════════════
   Legacy format converters (Python stores drawings as categorized arrays)
*/

function legacySerialize(drawings) {
    const out = {
        lines: [], rectangles: [], notes: [], horizontal_lines: [],
        horizontal_rays: [], arrow_lines: [], fibonacci: [],
    };
    for (const d of drawings) {
        switch (d.type) {
            case 'line':             out.lines.push(d);           break;
            case 'horizontal_line': out.horizontal_lines.push(d); break;
            case 'horizontal_ray':  out.horizontal_rays.push(d);  break;
            case 'rectangle':       out.rectangles.push(d);       break;
            case 'arrow':           out.arrow_lines.push(d);      break;
            case 'fibonacci':       out.fibonacci.push(d);        break;
            case 'note':            out.notes.push(d);            break;
        }
    }
    return out;
}

function legacyDeserialize(obj) {
    if (!obj || typeof obj !== 'object') return [];
    const all = [];
    const normalize = (type, d) => {
        if (!d || typeof d !== 'object') return null;
        const out = { type, ...d };
        if (out.id == null) return null;
        out.id = String(out.id);
        if (type === 'note') {
            if (out.startTime == null && out.time != null) out.startTime = out.time;
            if (out.startPrice == null && out.price != null) out.startPrice = out.price;
            if (out.fontSize == null && out.size != null) out.fontSize = out.size;
        }
        if (out.startTime == null || out.startPrice == null) return null;
        if (Number.isNaN(Number(out.startTime)) || Number.isNaN(Number(out.startPrice))) return null;
        out.startTime = Number(out.startTime);
        out.startPrice = Number(out.startPrice);
        if (out.endTime != null) out.endTime = Number(out.endTime);
        if (out.endPrice != null) out.endPrice = Number(out.endPrice);
        if (out.fontSize != null) out.fontSize = Number(out.fontSize);
        return out;
    };
    const map = {
        lines: 'line', rectangles: 'rectangle', notes: 'note',
        horizontal_lines: 'horizontal_line', horizontal_rays: 'horizontal_ray',
        arrow_lines: 'arrow', fibonacci: 'fibonacci',
    };
    for (const [key, type] of Object.entries(map)) {
        for (const d of (obj[key] || [])) {
            const normalized = normalize(type, d);
            if (normalized) all.push(normalized);
        }
    }
    return all;
}

/* ══════════════════════════════════════════════════════════════════════════════
   Compatibility shim: chart.drawings still used by some legacy paths
*/

class DrawingsCompat {
    constructor(engine) { this.engine = engine; }
    get horizontal_rays()  { return this.engine.getDrawings().filter(d => d.type === 'horizontal_ray'); }
    get horizontal_lines() { return this.engine.getDrawings().filter(d => d.type === 'horizontal_line'); }
    get lines()            { return this.engine.getDrawings().filter(d => d.type === 'line'); }
    get rectangles()       { return this.engine.getDrawings().filter(d => d.type === 'rectangle'); }
    get notes()            { return this.engine.getDrawings().filter(d => d.type === 'note'); }
    get arrow_lines()      { return this.engine.getDrawings().filter(d => d.type === 'arrow'); }
    get fibonacci()        { return this.engine.getDrawings().filter(d => d.type === 'fibonacci'); }
}

/* ══════════════════════════════════════════════════════════════════════════════
   STEP 6 — _notifyDrawingsChange() update in FixedTradingChart:
   (replace the old method body)
*/

function notifyDrawingsChange(chart) {
    if (!chart.chartBridge || !chart.webChannelInitialized) {
        chart._notifyQueue.push(() => notifyDrawingsChange(chart));
        chart._scheduleFlush?.();
        return;
    }
    try {
        const legacyFmt = legacySerialize(chart.drawingEngine.getDrawings());
        chart.chartBridge.notify_drawings_changed(JSON.stringify(legacyFmt));
    } catch (e) { console.error('notify_drawings_changed error:', e); }
}

/* ══════════════════════════════════════════════════════════════════════════════
   STEP 7 — chart.js bootstrap: load drawing_engine.js BEFORE chart.js

   In html_builder.py build_chart_html(), add before the chart_js embed:

       <script src="qrc:///drawing_engine.js"><\/script>

   OR inline drawing_engine.js content just before the chart.js content.
   Then at the bottom of chart.js _init():

       patchConstructor(this, cfg);
       installPublicApiShims(this);
       patchEventListeners(this);
*/

/* Export for reference */
if (typeof module !== 'undefined') {
    module.exports = {
        patchConstructor, installPublicApiShims, patchEventListeners,
        legacySerialize, legacyDeserialize, makeCoordSys,
    };
}
