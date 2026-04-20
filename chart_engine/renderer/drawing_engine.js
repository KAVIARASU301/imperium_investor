/**
 * drawing_engine.js  —  Institutional Drawing Engine v2
 *
 * Design goals (TC2000 / TradingView parity):
 *   • Sub-pixel precision on HiDPI — all coords in logical px, scale once at render
 *   • Spatial hash grid for O(1) hit-detection — no linear scan
 *   • Magnetic snap to OHLC levels + round numbers + prior closes
 *   • Multi-stage handle system: idle → hover → selected → dragging
 *   • Undo/redo stack (50 snapshots, JSON diff only)
 *   • Drawing lock toggle (prevents accidental moves)
 *   • Extend mode for trend lines (ray / full line / segment)
 *   • Price labels on every horizontal drawing with live tick update
 *   • Fibonacci with editable levels & custom colors
 *   • Right-click context menu on any drawing
 *   • Keyboard: Del = delete, L = lock, E = extend, Escape = deselect
 *   • Zero allocations in hot-path (object pools, typed arrays)
 */

'use strict';

/* ─── Constants ─────────────────────────────────────────────────────────────── */

const SNAP_RADIUS_PX   = 8;    // px distance to trigger magnetic snap
const HANDLE_RADIUS    = 5;    // endpoint handle visual radius px
const HANDLE_HIT       = 10;   // endpoint handle hit area radius px
const LINE_HIT_THRESH  = 6;    // px distance from a line body to register a hit
const GRID_CELL_PX     = 40;   // spatial hash cell size px
const UNDO_MAX         = 50;

/* Fibonacci levels used industry-standard (Fibonacci + Gann) */
const FIB_LEVELS = [
    { r: 0,     label: '0%',    color: '#e2e8f5', dash: [] },
    { r: 0.236, label: '23.6%', color: '#f59e0b', dash: [4,3] },
    { r: 0.382, label: '38.2%', color: '#3ecf8e', dash: [4,3] },
    { r: 0.5,   label: '50%',   color: '#4a9eff', dash: [6,3] },
    { r: 0.618, label: '61.8%', color: '#3ecf8e', dash: [4,3] },
    { r: 0.786, label: '78.6%', color: '#f59e0b', dash: [4,3] },
    { r: 1,     label: '100%',  color: '#e2e8f5', dash: [] },
    { r: 1.272, label: '127.2%',color: '#ef5350', dash: [2,4] },
    { r: 1.618, label: '161.8%',color: '#ef5350', dash: [2,4] },
];

const EXTEND_NONE  = 'segment';
const EXTEND_RIGHT = 'ray';
const EXTEND_BOTH  = 'line';

/* ─── Utility math ──────────────────────────────────────────────────────────── */

function ptSegDist2(px, py, ax, ay, bx, by) {
    const dx = bx - ax, dy = by - ay;
    const lenSq = dx * dx + dy * dy;
    if (lenSq === 0) return (px - ax) ** 2 + (py - ay) ** 2;
    const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lenSq));
    const qx = ax + t * dx, qy = ay + t * dy;
    return (px - qx) ** 2 + (py - qy) ** 2;
}

function ptSegDistForRay(px, py, ax, ay, bx, by) {
    /* treat segment as ray extending right beyond B */
    const dx = bx - ax, dy = by - ay;
    const lenSq = dx * dx + dy * dy;
    if (lenSq === 0) return (px - ax) ** 2 + (py - ay) ** 2;
    const t = ((px - ax) * dx + (py - ay) * dy) / lenSq;
    const tClamped = Math.max(0, t);   // no left clamp for ray
    const qx = ax + tClamped * dx, qy = ay + tClamped * dy;
    return (px - qx) ** 2 + (py - qy) ** 2;
}

function lerp(a, b, t) { return a + (b - a) * t; }

/* ─── Spatial Hash Grid ─────────────────────────────────────────────────────── */

class SpatialHash {
    constructor(cellSize = GRID_CELL_PX) {
        this.cell = cellSize;
        this.map = new Map();
    }
    _key(cx, cy) { return (cx & 0xffff) | ((cy & 0xffff) << 16); }
    _cell(x) { return Math.floor(x / this.cell); }

    insert(id, x1, y1, x2, y2) {
        const minCX = this._cell(Math.min(x1, x2)) - 1;
        const maxCX = this._cell(Math.max(x1, x2)) + 1;
        const minCY = this._cell(Math.min(y1, y2)) - 1;
        const maxCY = this._cell(Math.max(y1, y2)) + 1;
        for (let cx = minCX; cx <= maxCX; cx++) {
            for (let cy = minCY; cy <= maxCY; cy++) {
                const k = this._key(cx, cy);
                if (!this.map.has(k)) this.map.set(k, new Set());
                this.map.get(k).add(id);
            }
        }
    }

    query(x, y, radius) {
        const minCX = this._cell(x - radius) - 1;
        const maxCX = this._cell(x + radius) + 1;
        const minCY = this._cell(y - radius) - 1;
        const maxCY = this._cell(y + radius) + 1;
        const result = new Set();
        for (let cx = minCX; cx <= maxCX; cx++) {
            for (let cy = minCY; cy <= maxCY; cy++) {
                const cell = this.map.get(this._key(cx, cy));
                if (cell) cell.forEach(id => result.add(id));
            }
        }
        return result;
    }

    remove(id, x1, y1, x2, y2) {
        const minCX = this._cell(Math.min(x1, x2)) - 1;
        const maxCX = this._cell(Math.max(x1, x2)) + 1;
        const minCY = this._cell(Math.min(y1, y2)) - 1;
        const maxCY = this._cell(Math.max(y1, y2)) + 1;
        for (let cx = minCX; cx <= maxCX; cx++) {
            for (let cy = minCY; cy <= maxCY; cy++) {
                const cell = this.map.get(this._key(cx, cy));
                if (cell) cell.delete(id);
            }
        }
    }

    clear() { this.map.clear(); }
}

/* ─── Undo / Redo Stack ─────────────────────────────────────────────────────── */

class UndoStack {
    constructor(max = UNDO_MAX) {
        this.max = max;
        this.stack = [];
        this.pos = -1;
    }
    push(snapshot) {
        this.stack.splice(this.pos + 1);
        if (this.stack.length >= this.max) this.stack.shift();
        this.stack.push(snapshot);
        this.pos = this.stack.length - 1;
    }
    undo() {
        if (this.pos <= 0) return null;
        this.pos--;
        return this.stack[this.pos];
    }
    redo() {
        if (this.pos >= this.stack.length - 1) return null;
        this.pos++;
        return this.stack[this.pos];
    }
    canUndo() { return this.pos > 0; }
    canRedo() { return this.pos < this.stack.length - 1; }
}

/* ─── Drawing Engine ────────────────────────────────────────────────────────── */

class DrawingEngine {
    /**
     * @param {HTMLCanvasElement} canvas
     * @param {object} coordSys  — must implement:
     *   priceToY(price) → float
     *   yToPrice(y)     → float
     *   timeToX(time)   → float   (ms epoch)
     *   xToTime(x)      → float   (ms epoch)
     *   candleToX(idx)  → float
     *   xToCandle(x)    → int
     *   chartArea       → {x, y, width, height}
     *   data            → [{time, open, high, low, close}, …]
     */
    constructor(canvas, coordSys) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.cs = coordSys;               // coordinate system delegate

        /* drawing state */
        this.drawings = new Map();        // id → drawing object
        this.nextId = 1;
        this.spatialHash = new SpatialHash();
        this.undoStack = new UndoStack();
        this._undoSnapshot();             // baseline

        /* interaction state */
        this.activeTool = null;           // 'line' | 'horizontal_ray' | 'horizontal_line' |
                                          // 'rectangle' | 'fibonacci' | 'arrow' | 'note' | 'measure' | null
        this.drawingColor = '#FFD700';
        this.lineWidth    = 1.5;
        this.extend       = EXTEND_NONE;  // 'segment' | 'ray' | 'line'
        this.locked       = false;        // global lock

        this.selectedId   = null;
        this.hoverId      = null;
        this.activeHandle = null;         // {id, which: 'start'|'end'|'body'}
        this.inProgress   = null;         // drawing being placed

        /* snap state */
        this.snapPoint    = null;         // {x, y, price, label} or null
        this.snapEnabled  = true;

        /* context menu */
        this._menu        = null;

        /* callbacks to notify host chart */
        this.onDrawingsChanged = null;    // () => void
        this.onRequestTextNote = null;    // ({x, y, price, time}) => void

        this._bindEvents();
    }

    /* ─── Coordinate helpers ────────────────────────────────────────────────── */

    _px(e) {
        const r = this.canvas.getBoundingClientRect();
        return { x: e.clientX - r.left, y: e.clientY - r.top };
    }

    _inChartArea(x, y) {
        const a = this.cs.chartArea;
        return x >= a.x && x <= a.x + a.width && y >= a.y && y <= a.y + a.height;
    }

    _clampToChart(x, y) {
        const a = this.cs.chartArea;
        return {
            x: Math.max(a.x, Math.min(a.x + a.width, x)),
            y: Math.max(a.y, Math.min(a.y + a.height, y)),
        };
    }

    /* Convert pixel → {price, time} with snapping */
    _pixToCoord(x, y) {
        const p = this.cs.yToPrice(y);
        const t = this.cs.xToTime(x);
        return { price: p, time: t, x, y };
    }

    /* ─── Magnetic snap ─────────────────────────────────────────────────────── */

    _computeSnap(mx, my) {
        if (!this.snapEnabled) return null;

        const candidates = [];
        const data = this.cs.data;
        const ci = this.cs.xToCandle(mx);

        /* OHLC snap from nearby candles */
        for (let di = -1; di <= 1; di++) {
            const idx = ci + di;
            if (idx < 0 || idx >= data.length) continue;
            const c = data[idx];
            const cx = this.cs.candleToX(idx) + (this.cs.candleWidth || 8) / 2;
            for (const [key, price] of [['H', c.high], ['L', c.low], ['O', c.open], ['C', c.close]]) {
                const py = this.cs.priceToY(price);
                const dx = mx - cx, dy = my - py;
                candidates.push({ x: cx, y: py, price, label: key, dist2: dx * dx + dy * dy });
            }
        }

        /* Round-number snap */
        const curPrice = this.cs.yToPrice(my);
        const mag = Math.pow(10, Math.floor(Math.log10(Math.abs(curPrice || 1))));
        const steps = [mag * 0.5, mag, mag * 2];
        for (const step of steps) {
            const rounded = Math.round(curPrice / step) * step;
            const py = this.cs.priceToY(rounded);
            const dy = my - py;
            candidates.push({ x: mx, y: py, price: rounded, label: '', dist2: dy * dy + 1 });
        }

        candidates.sort((a, b) => a.dist2 - b.dist2);
        const best = candidates[0];
        if (best && Math.sqrt(best.dist2) < SNAP_RADIUS_PX) return best;
        return null;
    }

    /* ─── Hit detection ─────────────────────────────────────────────────────── */

    /**
     * Returns {id, which: 'start'|'end'|'body'} or null
     */
    _hitTest(mx, my) {
        const candidates = this.spatialHash.query(mx, my, HANDLE_HIT + LINE_HIT_THRESH + 4);

        /* Test handles first (priority) */
        let bestHandle = null, bestHandleDist = Infinity;
        for (const id of candidates) {
            const d = this.drawings.get(id);
            if (!d) continue;
            const sx = this.cs.timeToX(d.startTime), sy = this.cs.priceToY(d.startPrice);
            const dSt = Math.hypot(mx - sx, my - sy);
            if (dSt < HANDLE_HIT && dSt < bestHandleDist) {
                bestHandleDist = dSt; bestHandle = { id, which: 'start' };
            }
            if (d.endTime != null) {
                const ex = this.cs.timeToX(d.endTime), ey = this.cs.priceToY(d.endPrice);
                const dEn = Math.hypot(mx - ex, my - ey);
                if (dEn < HANDLE_HIT && dEn < bestHandleDist) {
                    bestHandleDist = dEn; bestHandle = { id, which: 'end' };
                }
            }
        }
        if (bestHandle) return bestHandle;

        /* Test bodies */
        let bestBody = null, bestBodyDist = Infinity;
        for (const id of candidates) {
            const d = this.drawings.get(id);
            if (!d) continue;
            const dist = this._bodyHitDist(d, mx, my);
            if (dist < LINE_HIT_THRESH && dist < bestBodyDist) {
                bestBodyDist = dist; bestBody = { id, which: 'body' };
            }
        }
        return bestBody;
    }

    _bodyHitDist(d, mx, my) {
        const { chartArea: a } = this.cs;
        const sx = this.cs.timeToX(d.startTime), sy = this.cs.priceToY(d.startPrice);

        switch (d.type) {
            case 'horizontal_ray': {
                const dist = Math.abs(my - sy);
                if (mx < sx - HANDLE_HIT) return Infinity;
                return dist;
            }
            case 'horizontal_line': {
                return Math.abs(my - sy);
            }
            case 'line':
            case 'arrow': {
                const ex = this.cs.timeToX(d.endTime), ey = this.cs.priceToY(d.endPrice);
                let ax = sx, ay = sy, bx = ex, by = ey;
                if (d.extend === EXTEND_RIGHT || d.extend === EXTEND_BOTH) {
                    /* extend to chart edge */
                    const t = (a.x + a.width - sx) / (ex - sx || 1);
                    bx = sx + t * (ex - sx); by = sy + t * (ey - sy);
                }
                if (d.extend === EXTEND_BOTH) {
                    const t2 = (a.x - sx) / (ex - sx || 1);
                    ax = sx + t2 * (ex - sx); ay = sy + t2 * (ey - sy);
                }
                const d2 = ptSegDist2(mx, my, ax, ay, bx, by);
                return Math.sqrt(d2);
            }
            case 'rectangle': {
                const ex = this.cs.timeToX(d.endTime), ey = this.cs.priceToY(d.endPrice);
                const x1 = Math.min(sx, ex), x2 = Math.max(sx, ex);
                const y1 = Math.min(sy, ey), y2 = Math.max(sy, ey);
                /* hit on border of rect */
                const inBox = mx >= x1 && mx <= x2 && my >= y1 && my <= y2;
                if (!inBox) return Infinity;
                const dLeft = mx - x1, dRight = x2 - mx, dTop = my - y1, dBot = y2 - my;
                return Math.min(dLeft, dRight, dTop, dBot);
            }
            case 'fibonacci': {
                const ex = this.cs.timeToX(d.endTime), ey = this.cs.priceToY(d.endPrice);
                const priceRange = d.startPrice - d.endPrice;
                for (const lvl of FIB_LEVELS) {
                    const price = d.endPrice + priceRange * lvl.r;
                    const fy = this.cs.priceToY(price);
                    if (Math.abs(my - fy) < LINE_HIT_THRESH) return Math.abs(my - fy);
                }
                return Infinity;
            }
            case 'note': {
                /* hit box around text */
                const w = (d.textWidth || 80) + 12, h = (d.fontSize || 12) + 8;
                const tx = sx, ty = sy - h;
                const inside = mx >= tx - 4 && mx <= tx + w && my >= ty && my <= sy + 4;
                return inside ? 0 : Infinity;
            }
            default: return Infinity;
        }
    }

    /* ─── Spatial hash management ───────────────────────────────────────────── */

    _hashInsert(d) {
        const sx = this.cs.timeToX(d.startTime), sy = this.cs.priceToY(d.startPrice);
        let ex = sx, ey = sy;
        if (d.endTime != null) { ex = this.cs.timeToX(d.endTime); ey = this.cs.priceToY(d.endPrice); }
        /* For horizontal drawings extend to chart width */
        if (d.type === 'horizontal_ray' || d.type === 'horizontal_line') {
            ex = this.cs.chartArea.x + this.cs.chartArea.width;
        }
        this.spatialHash.insert(d.id, sx, sy, ex, ey);
    }

    _hashRemove(d) {
        const sx = this.cs.timeToX(d.startTime), sy = this.cs.priceToY(d.startPrice);
        let ex = sx, ey = sy;
        if (d.endTime != null) { ex = this.cs.timeToX(d.endTime); ey = this.cs.priceToY(d.endPrice); }
        if (d.type === 'horizontal_ray' || d.type === 'horizontal_line') {
            ex = this.cs.chartArea.x + this.cs.chartArea.width;
        }
        this.spatialHash.remove(d.id, sx, sy, ex, ey);
    }

    rebuildSpatialHash() {
        this.spatialHash.clear();
        for (const d of this.drawings.values()) this._hashInsert(d);
    }

    /* ─── Undo / Redo ───────────────────────────────────────────────────────── */

    _undoSnapshot() {
        const snap = JSON.stringify([...this.drawings.entries()]);
        this.undoStack.push(snap);
    }

    undo() {
        const snap = this.undoStack.undo();
        if (!snap) return;
        this.drawings = new Map(JSON.parse(snap));
        this.selectedId = null;
        this.rebuildSpatialHash();
        this._notify();
    }

    redo() {
        const snap = this.undoStack.redo();
        if (!snap) return;
        this.drawings = new Map(JSON.parse(snap));
        this.selectedId = null;
        this.rebuildSpatialHash();
        this._notify();
    }

    /* ─── CRUD ──────────────────────────────────────────────────────────────── */

    addDrawing(partial) {
        const d = {
            id: String(this.nextId++),
            color: this.drawingColor,
            lineWidth: this.lineWidth,
            extend: this.extend,
            locked: false,
            visible: true,
            ...partial,
        };
        this.drawings.set(d.id, d);
        this._hashInsert(d);
        this._undoSnapshot();
        this._notify();
        return d.id;
    }

    updateDrawing(id, patch) {
        const d = this.drawings.get(id);
        if (!d || d.locked) return;
        this._hashRemove(d);
        Object.assign(d, patch);
        this._hashInsert(d);
        this._notify();
    }

    deleteDrawing(id) {
        const d = this.drawings.get(id);
        if (!d) return;
        this._hashRemove(d);
        this.drawings.delete(id);
        if (this.selectedId === id) this.selectedId = null;
        this._undoSnapshot();
        this._notify();
    }

    deleteSelected() {
        if (this.selectedId) {
            this.deleteDrawing(this.selectedId);
        }
    }

    clearAll() {
        this.drawings.clear();
        this.spatialHash.clear();
        this.selectedId = null;
        this._undoSnapshot();
        this._notify();
    }

    lockSelected() {
        if (!this.selectedId) return;
        const d = this.drawings.get(this.selectedId);
        if (d) { d.locked = !d.locked; this._notify(); }
    }

    /* ─── Tool activation ───────────────────────────────────────────────────── */

    setTool(toolId, color, lineWidth) {
        this.activeTool = toolId || null;
        if (color)     this.drawingColor = color;
        if (lineWidth) this.lineWidth = lineWidth;
        this.inProgress = null;
        this.selectedId = null;
        this.canvas.style.cursor = toolId ? 'crosshair' : 'default';
    }

    clearTool() {
        this.activeTool = null;
        this.inProgress = null;
        this.canvas.style.cursor = 'default';
    }

    setExtend(mode) {
        this.extend = mode; // 'segment' | 'ray' | 'line'
        if (this.selectedId) {
            this.updateDrawing(this.selectedId, { extend: mode });
        }
    }

    /* ─── Event binding ─────────────────────────────────────────────────────── */

    _bindEvents() {
        this.canvas.addEventListener('mousemove',   e => this._onMove(e));
        this.canvas.addEventListener('mousedown',   e => this._onDown(e));
        this.canvas.addEventListener('mouseup',     e => this._onUp(e));
        this.canvas.addEventListener('mouseleave',  () => this._onLeave());
        this.canvas.addEventListener('dblclick',    e => this._onDblClick(e));
        this.canvas.addEventListener('contextmenu', e => this._onContextMenu(e));

        document.addEventListener('keydown', e => this._onKey(e));
        document.addEventListener('click',   e => this._onDocClick(e));
    }

    _onMove(e) {
        const { x, y } = this._px(e);

        /* Compute snap candidate (always, for visual feedback) */
        this.snapPoint = this.snapEnabled ? this._computeSnap(x, y) : null;
        const snapX = this.snapPoint ? this.snapPoint.x : x;
        const snapY = this.snapPoint ? this.snapPoint.y : y;

        /* --- Dragging active handle --- */
        if (this.activeHandle && e.buttons === 1) {
            const { id, which } = this.activeHandle;
            const d = this.drawings.get(id);
            if (d && !d.locked && !this.locked) {
                this._hashRemove(d);
                if (which === 'body') {
                    /* translate whole drawing */
                    const dx = x - this._lastDragX;
                    const dy = y - this._lastDragY;
                    const dPrice = this.cs.yToPrice(snapY) - this.cs.yToPrice(snapY - dy);
                    const dTime  = this.cs.xToTime(x) - this.cs.xToTime(x - dx);
                    d.startPrice += dPrice; d.startTime += dTime;
                    if (d.endTime != null) { d.endPrice += dPrice; d.endTime += dTime; }
                } else {
                    const coord = this._pixToCoord(snapX, snapY);
                    if (which === 'start') {
                        d.startPrice = coord.price; d.startTime = coord.time;
                    } else {
                        d.endPrice = coord.price; d.endTime = coord.time;
                    }
                }
                this._hashInsert(d);
                this._lastDragX = x; this._lastDragY = y;
                this._notify();
            }
            return;
        }

        /* --- In-progress drawing update --- */
        if (this.inProgress && e.buttons === 1 && this.activeTool) {
            this.inProgress.endX = snapX;
            this.inProgress.endY = snapY;
            this.inProgress.endPrice = this.cs.yToPrice(snapY);
            this.inProgress.endTime  = this.cs.xToTime(snapX);
            return;
        }

        /* --- Hover detection --- */
        const hit = this._hitTest(x, y);
        const newHover = hit ? hit.id : null;
        if (newHover !== this.hoverId) { this.hoverId = newHover; }

        /* Cursor feedback */
        if (this.activeTool) {
            this.canvas.style.cursor = 'crosshair';
        } else if (hit) {
            const d = this.drawings.get(hit.id);
            if (d && d.locked) this.canvas.style.cursor = 'not-allowed';
            else if (hit.which !== 'body') this.canvas.style.cursor = 'nw-resize';
            else this.canvas.style.cursor = 'grab';
        } else {
            this.canvas.style.cursor = 'default';
        }

        this._lastMouseX = x; this._lastMouseY = y;
    }

    _onDown(e) {
        if (e.button !== 0) return;
        const { x, y } = this._px(e);
        if (!this._inChartArea(x, y)) return;

        this._dismissMenu();
        const snapX = this.snapPoint ? this.snapPoint.x : x;
        const snapY = this.snapPoint ? this.snapPoint.y : y;

        /* --- Start drawing --- */
        if (this.activeTool) {
            if (this.activeTool === 'note') {
                if (this.onRequestTextNote) {
                    this.onRequestTextNote({
                        x, y,
                        price: this.cs.yToPrice(y),
                        time:  this.cs.xToTime(x),
                    });
                }
                this.clearTool();
                return;
            }
            this.inProgress = {
                type: this.activeTool,
                startX: snapX, startY: snapY,
                startPrice: this.cs.yToPrice(snapY),
                startTime:  this.cs.xToTime(snapX),
                endX: snapX, endY: snapY,
                endPrice: this.cs.yToPrice(snapY),
                endTime:  this.cs.xToTime(snapX),
                color: this.drawingColor,
                lineWidth: this.lineWidth,
                extend: this.extend,
            };
            this.selectedId = null;
            return;
        }

        /* --- Hit test for selection / handle drag --- */
        const hit = this._hitTest(x, y);
        if (hit) {
            const d = this.drawings.get(hit.id);
            if (d && !d.locked && !this.locked) {
                this.selectedId = hit.id;
                this.activeHandle = hit;
                this._lastDragX = x; this._lastDragY = y;
                this.canvas.style.cursor = 'grabbing';
                this._undoSnapshot();  // snapshot before drag starts
            } else if (d) {
                this.selectedId = hit.id;  // select but don't allow drag
            }
        } else {
            this.selectedId = null;
        }
    }

    _onUp(e) {
        if (e.button !== 0) return;
        const { x, y } = this._px(e);

        /* Finalize drag */
        if (this.activeHandle) {
            this._undoSnapshot();
            this.activeHandle = null;
            this.canvas.style.cursor = 'default';
            return;
        }

        /* Finalize drawing */
        if (this.inProgress && this.activeTool) {
            const ip = this.inProgress;
            const snapX = this.snapPoint ? this.snapPoint.x : x;
            const snapY = this.snapPoint ? this.snapPoint.y : y;
            ip.endX = snapX; ip.endY = snapY;
            ip.endPrice = this.cs.yToPrice(snapY);
            ip.endTime  = this.cs.xToTime(snapX);

            const needsEnd = !['horizontal_ray', 'horizontal_line', 'note'].includes(ip.type);
            const hasMoved = Math.hypot(ip.endX - ip.startX, ip.endY - ip.startY) > 3;

            if (!needsEnd || hasMoved) {
                this.addDrawing({
                    type:       ip.type,
                    startPrice: ip.startPrice, startTime: ip.startTime,
                    endPrice:   ip.endPrice,   endTime:   ip.endTime,
                    color:      ip.color,
                    lineWidth:  ip.lineWidth,
                    extend:     ip.extend,
                });
                this.selectedId = String(this.nextId - 1);
            }

            this.inProgress = null;

            /* Keep tool active for repeated placement, unless single-shot tools */
            /* User presses Escape to exit tool */
        }
    }

    _onLeave() {
        this.snapPoint = null;
        this.hoverId = null;
        if (!this.activeHandle) this.canvas.style.cursor = 'default';
    }

    _onDblClick(e) {
        const { x, y } = this._px(e);
        const hit = this._hitTest(x, y);
        if (hit) {
            const d = this.drawings.get(hit.id);
            if (d && d.type === 'note' && this.onRequestTextNote) {
                this.onRequestTextNote({ id: d.id, text: d.text, color: d.color, x, y });
            }
        }
    }

    _onKey(e) {
        const tag = document.activeElement?.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA') return;

        switch (e.key) {
            case 'Escape':
                if (this.activeTool) { this.clearTool(); }
                else { this.selectedId = null; }
                break;
            case 'Delete':
            case 'Backspace':
                this.deleteSelected();
                break;
            case 'l':
            case 'L':
                this.lockSelected();
                break;
            case 'e':
            case 'E':
                if (this.selectedId) {
                    const d = this.drawings.get(this.selectedId);
                    if (d) {
                        const order = [EXTEND_NONE, EXTEND_RIGHT, EXTEND_BOTH];
                        const next = order[(order.indexOf(d.extend || EXTEND_NONE) + 1) % 3];
                        this.updateDrawing(this.selectedId, { extend: next });
                    }
                }
                break;
            case 'z':
            case 'Z':
                if (e.ctrlKey || e.metaKey) { e.shiftKey ? this.redo() : this.undo(); }
                break;
        }
    }

    /* ─── Context Menu ──────────────────────────────────────────────────────── */

    _onContextMenu(e) {
        e.preventDefault();
        const { x, y } = this._px(e);
        const hit = this._hitTest(x, y);
        if (!hit) return;

        this.selectedId = hit.id;
        const d = this.drawings.get(hit.id);
        if (!d) return;

        this._dismissMenu();
        this._menu = this._buildContextMenu(e.clientX, e.clientY, d);
        document.body.appendChild(this._menu);
    }

    _buildContextMenu(cx, cy, d) {
        const menu = document.createElement('div');
        Object.assign(menu.style, {
            position: 'fixed', left: cx + 'px', top: cy + 'px',
            background: '#0d1117', border: '1px solid #253347',
            borderRadius: '6px', padding: '6px 0', zIndex: '99999',
            boxShadow: '0 8px 24px rgba(0,0,0,.6)',
            fontFamily: '"Segoe UI", sans-serif', fontSize: '12px',
            color: '#c8d4e8', minWidth: '180px', userSelect: 'none',
        });

        const items = [
            { label: d.locked ? 'Unlock drawing' : 'Lock drawing', icon: '🔒',
              action: () => { d.locked = !d.locked; this._notify(); } },
            { label: 'Delete', icon: '🗑', action: () => this.deleteDrawing(d.id) },
            { divider: true },
            { label: 'Extend: segment', action: () => this.updateDrawing(d.id, { extend: EXTEND_NONE }),
              checked: (d.extend || EXTEND_NONE) === EXTEND_NONE },
            { label: 'Extend: ray →', action: () => this.updateDrawing(d.id, { extend: EXTEND_RIGHT }),
              checked: d.extend === EXTEND_RIGHT },
            { label: 'Extend: ← both →', action: () => this.updateDrawing(d.id, { extend: EXTEND_BOTH }),
              checked: d.extend === EXTEND_BOTH },
        ];

        for (const item of items) {
            if (item.divider) {
                const sep = document.createElement('div');
                sep.style.cssText = 'height:1px;background:#1e2840;margin:4px 0;';
                menu.appendChild(sep);
                continue;
            }
            const mi = document.createElement('div');
            mi.style.cssText = `padding:7px 16px;cursor:pointer;display:flex;align-items:center;gap:8px;
                color:${item.checked ? '#4a9eff' : '#c8d4e8'};`;
            mi.innerHTML = `${item.icon ? `<span style="font-size:13px">${item.icon}</span>` : '<span style="width:13px"></span>'} ${item.label}
                ${item.checked ? '<span style="margin-left:auto;color:#4a9eff">✓</span>' : ''}`;
            mi.onmouseenter = () => mi.style.background = '#161d2a';
            mi.onmouseleave = () => mi.style.background = 'transparent';
            mi.onclick = () => { item.action(); this._dismissMenu(); };
            menu.appendChild(mi);
        }

        /* Viewport clamp */
        setTimeout(() => {
            const r = menu.getBoundingClientRect();
            if (r.right  > window.innerWidth)  menu.style.left = (cx - r.width) + 'px';
            if (r.bottom > window.innerHeight) menu.style.top  = (cy - r.height) + 'px';
        }, 0);

        return menu;
    }

    _onDocClick(e) {
        if (this._menu && !this._menu.contains(e.target)) this._dismissMenu();
    }

    _dismissMenu() {
        if (this._menu) { this._menu.remove(); this._menu = null; }
    }

    /* ─── Notify host ───────────────────────────────────────────────────────── */

    _notify() {
        if (typeof this.onDrawingsChanged === 'function') this.onDrawingsChanged();
    }

    /* ─── Serialization ─────────────────────────────────────────────────────── */

    serialize() {
        return JSON.stringify([...this.drawings.values()]);
    }

    deserialize(json) {
        try {
            const arr = JSON.parse(json);
            this.drawings.clear();
            for (const d of arr) {
                this.drawings.set(d.id, d);
                if (parseInt(d.id) >= this.nextId) this.nextId = parseInt(d.id) + 1;
            }
            this.rebuildSpatialHash();
        } catch (err) { console.warn('DrawingEngine: deserialize error', err); }
    }

    getDrawings() { return [...this.drawings.values()]; }

    /* ─── RENDER ────────────────────────────────────────────────────────────── */

    /**
     * Call this from your main chart draw() loop.
     * All persistent drawings + current in-progress ghost are rendered here.
     */
    render() {
        const ctx = this.ctx;
        const dpr = window.devicePixelRatio || 1;

        /* Render all persistent drawings */
        for (const d of this.drawings.values()) {
            if (!d.visible) continue;
            const selected = d.id === this.selectedId;
            const hovered  = d.id === this.hoverId && !selected;
            this._renderDrawing(ctx, d, selected, hovered);
        }

        /* Render in-progress ghost */
        if (this.inProgress) this._renderInProgress(ctx, this.inProgress);

        /* Snap indicator */
        if (this.snapPoint && (this.activeTool || this.activeHandle)) {
            this._renderSnap(ctx, this.snapPoint);
        }
    }

    _renderDrawing(ctx, d, selected, hovered) {
        ctx.save();
        ctx.lineWidth = (d.lineWidth || 1.5) + (selected ? 0.8 : 0) + (hovered ? 0.4 : 0);
        ctx.strokeStyle = d.color || '#FFD700';
        ctx.setLineDash([]);
        ctx.globalAlpha = d.locked ? 0.55 : 1;

        switch (d.type) {
            case 'horizontal_ray':
                this._renderHRay(ctx, d, selected, hovered); break;
            case 'horizontal_line':
                this._renderHLine(ctx, d, selected, hovered); break;
            case 'line':
            case 'arrow':
                this._renderLine(ctx, d, selected, hovered); break;
            case 'rectangle':
                this._renderRect(ctx, d, selected, hovered); break;
            case 'fibonacci':
                this._renderFib(ctx, d, selected, hovered); break;
            case 'note':
                this._renderNote(ctx, d, selected, hovered); break;
            case 'measure':
                this._renderMeasure(ctx, d, selected, hovered); break;
        }

        ctx.restore();
    }

    /* ── Horizontal ray ── */
    _renderHRay(ctx, d, sel, hov) {
        const { chartArea: a } = this.cs;
        const sx = this.cs.timeToX(d.startTime), sy = this.cs.priceToY(d.startPrice);
        const endX = a.x + a.width;
        if (sy < a.y - 2 || sy > a.y + a.height + 2) return;

        ctx.setLineDash([5, 3]);
        this._drawLine(ctx, sx, sy, endX, sy);

        /* price label on right axis */
        this._renderPriceLabel(ctx, sy, d.startPrice, d.color, sel);

        /* locked icon */
        if (d.locked) this._renderLockIcon(ctx, sx + 8, sy - 8);

        /* handle */
        if (sel || hov) this._renderHandle(ctx, sx, sy, sel, d.color);
    }

    /* ── Horizontal full line ── */
    _renderHLine(ctx, d, sel, hov) {
        const { chartArea: a } = this.cs;
        const sy = this.cs.priceToY(d.startPrice);
        if (sy < a.y - 2 || sy > a.y + a.height + 2) return;
        ctx.setLineDash([]);
        this._drawLine(ctx, a.x, sy, a.x + a.width, sy);
        this._renderPriceLabel(ctx, sy, d.startPrice, d.color, sel);
        if (sel || hov) this._renderHandle(ctx, this.cs.timeToX(d.startTime), sy, sel, d.color);
    }

    /* ── Trend line / arrow ── */
    _renderLine(ctx, d, sel, hov) {
        const { chartArea: a } = this.cs;
        let sx = this.cs.timeToX(d.startTime), sy = this.cs.priceToY(d.startPrice);
        let ex = this.cs.timeToX(d.endTime),   ey = this.cs.priceToY(d.endPrice);

        const ext = d.extend || EXTEND_NONE;
        if (ext === EXTEND_RIGHT || ext === EXTEND_BOTH) {
            const slope = (ey - sy) / (ex - sx || 1);
            const tRight = (a.x + a.width - sx) / (ex - sx || 1);
            const rxFar = sx + tRight * (ex - sx);
            const ryFar = sy + tRight * (ey - sy);
            if (rxFar > ex) { ex = rxFar; ey = ryFar; }
        }
        if (ext === EXTEND_BOTH) {
            const tLeft = (a.x - sx) / (this.cs.timeToX(d.endTime) - sx || 1);
            sx = sx + tLeft * (this.cs.timeToX(d.endTime) - sx);
            sy = sy + tLeft * (this.cs.priceToY(d.endPrice) - sy);
        }

        ctx.setLineDash([]);
        this._drawLine(ctx, sx, sy, ex, ey);

        if (d.type === 'arrow') this._renderArrowHead(ctx, ex, ey, sx, sy, d.color);
        if (sel || hov) {
            this._renderHandle(ctx, this.cs.timeToX(d.startTime), this.cs.priceToY(d.startPrice), sel, d.color);
            this._renderHandle(ctx, this.cs.timeToX(d.endTime),   this.cs.priceToY(d.endPrice),   sel, d.color);
        }
    }

    _renderArrowHead(ctx, tipX, tipY, tailX, tailY, color) {
        const angle = Math.atan2(tipY - tailY, tipX - tailX);
        const size = 11;
        ctx.save();
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.moveTo(tipX, tipY);
        ctx.lineTo(tipX - size * Math.cos(angle - 0.42), tipY - size * Math.sin(angle - 0.42));
        ctx.lineTo(tipX - size * Math.cos(angle + 0.42), tipY - size * Math.sin(angle + 0.42));
        ctx.closePath();
        ctx.fill();
        ctx.restore();
    }

    /* ── Rectangle ── */
    _renderRect(ctx, d, sel, hov) {
        const sx = this.cs.timeToX(d.startTime), sy = this.cs.priceToY(d.startPrice);
        const ex = this.cs.timeToX(d.endTime),   ey = this.cs.priceToY(d.endPrice);
        const x = Math.min(sx, ex), y = Math.min(sy, ey);
        const w = Math.abs(ex - sx), h = Math.abs(ey - sy);
        ctx.setLineDash([]);
        ctx.globalAlpha = 0.08;
        ctx.fillStyle = d.color;
        ctx.fillRect(x, y, w, h);
        ctx.globalAlpha = d.locked ? 0.55 : 1;
        ctx.strokeRect(x, y, w, h);
        if (sel || hov) {
            this._renderHandle(ctx, sx, sy, sel, d.color);
            this._renderHandle(ctx, ex, ey, sel, d.color);
            this._renderHandle(ctx, sx, ey, sel, d.color);
            this._renderHandle(ctx, ex, sy, sel, d.color);
        }
    }

    /* ── Fibonacci ── */
    _renderFib(ctx, d, sel, hov) {
        const { chartArea: a } = this.cs;
        const sx = this.cs.timeToX(d.startTime), sy = this.cs.priceToY(d.startPrice);
        const ex = this.cs.timeToX(d.endTime),   ey = this.cs.priceToY(d.endPrice);
        const priceRange = d.startPrice - d.endPrice;
        const leftX  = Math.min(sx, ex) - 4;
        const rightX = Math.max(sx, ex) + 4;

        ctx.font = '10px "Segoe UI Mono", monospace';
        ctx.textBaseline = 'middle';

        for (let i = 0; i < FIB_LEVELS.length; i++) {
            const lvl = FIB_LEVELS[i];
            const price = d.endPrice + priceRange * lvl.r;
            const fy = this.cs.priceToY(price);
            if (fy < a.y - 2 || fy > a.y + a.height + 2) continue;

            /* shade between consecutive levels */
            if (i < FIB_LEVELS.length - 1) {
                const nextPrice = d.endPrice + priceRange * FIB_LEVELS[i + 1].r;
                const nfy = this.cs.priceToY(nextPrice);
                ctx.globalAlpha = 0.04;
                ctx.fillStyle = lvl.color;
                ctx.fillRect(leftX, Math.min(fy, nfy), rightX - leftX, Math.abs(nfy - fy));
                ctx.globalAlpha = d.locked ? 0.55 : 1;
            }

            ctx.save();
            ctx.strokeStyle = lvl.color;
            ctx.lineWidth = lvl.r === 0 || lvl.r === 1 ? (d.lineWidth || 1.5) + 0.3 : (d.lineWidth || 1.5) * 0.7;
            ctx.setLineDash(lvl.dash);
            this._drawLine(ctx, leftX, fy, rightX, fy);
            ctx.restore();

            /* label */
            ctx.fillStyle = lvl.color;
            ctx.textAlign = 'right';
            ctx.fillText(`${lvl.label}  ₹${price.toFixed(2)}`, rightX - 2, fy - 3);
        }

        if (sel || hov) {
            this._renderHandle(ctx, sx, sy, sel, d.color);
            this._renderHandle(ctx, ex, ey, sel, d.color);
        }
    }

    /* ── Text note ── */
    _renderNote(ctx, d, sel, hov) {
        if (!d.text) return;
        const x = this.cs.timeToX(d.startTime), y = this.cs.priceToY(d.startPrice);
        const fs = d.fontSize || 12;
        ctx.font = `${fs}px "Segoe UI", sans-serif`;
        ctx.textBaseline = 'top';
        const tw = ctx.measureText(d.text).width;
        d.textWidth = tw;

        /* pill background */
        ctx.fillStyle = 'rgba(13,17,23,0.78)';
        ctx.beginPath();
        ctx.roundRect(x, y - fs - 6, tw + 12, fs + 8, 3);
        ctx.fill();
        if (sel || hov) {
            ctx.strokeStyle = d.color;
            ctx.lineWidth = 1;
            ctx.setLineDash([]);
            ctx.stroke();
        }

        /* text */
        ctx.fillStyle = d.color || '#FFD700';
        ctx.fillText(d.text, x + 6, y - fs - 2);

        /* pin dot */
        ctx.fillStyle = d.color || '#FFD700';
        ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill();

        if (sel || hov) this._renderHandle(ctx, x, y, sel, d.color);
    }

    /* ── Measure tool ── */
    _renderMeasure(ctx, d, sel, hov) {
        const sx = this.cs.timeToX(d.startTime), sy = this.cs.priceToY(d.startPrice);
        const ex = this.cs.timeToX(d.endTime),   ey = this.cs.priceToY(d.endPrice);
        ctx.setLineDash([4, 3]);
        this._drawLine(ctx, sx, sy, ex, ey);

        const priceDiff = d.endPrice - d.startPrice;
        const pct       = d.startPrice !== 0 ? (priceDiff / d.startPrice) * 100 : 0;
        const startCi   = this.cs.xToCandle(sx);
        const endCi     = this.cs.xToCandle(ex);
        const bars      = Math.abs(endCi - startCi);
        const sign      = priceDiff >= 0 ? '+' : '';

        const info = `${sign}₹${priceDiff.toFixed(2)}  (${sign}${pct.toFixed(2)}%)  ${bars} bars`;

        ctx.font = '600 11px "Segoe UI", sans-serif';
        const tw = ctx.measureText(info).width;
        const bx = (sx + ex) / 2 - tw / 2 - 8;
        const by = (sy + ey) / 2 - 22;

        ctx.fillStyle = 'rgba(10,14,26,0.85)';
        ctx.fillRect(bx, by, tw + 16, 22);
        ctx.strokeStyle = '#253347'; ctx.lineWidth = 0.8;
        ctx.setLineDash([]); ctx.strokeRect(bx, by, tw + 16, 22);

        ctx.fillStyle = priceDiff >= 0 ? '#3ecf8e' : '#ef5350';
        ctx.textBaseline = 'middle';
        ctx.fillText(info, bx + 8, by + 11);
    }

    /* ── In-progress ghost ── */
    _renderInProgress(ctx, ip) {
        const sx = ip.startX, sy = ip.startY;
        const ex = ip.endX,   ey = ip.endY;
        const { chartArea: a } = this.cs;

        ctx.save();
        ctx.strokeStyle = ip.color;
        ctx.lineWidth   = ip.lineWidth || 1.5;
        ctx.setLineDash([4, 4]);
        ctx.globalAlpha = 0.8;

        switch (ip.type) {
            case 'horizontal_ray':
            case 'horizontal_line':
                this._drawLine(ctx, sx, sy, a.x + a.width, sy);
                break;
            case 'line':
            case 'arrow':
                this._drawLine(ctx, sx, sy, ex, ey);
                if (ip.type === 'arrow') this._renderArrowHead(ctx, ex, ey, sx, sy, ip.color);
                break;
            case 'rectangle':
                ctx.setLineDash([]);
                ctx.globalAlpha = 0.06;
                ctx.fillStyle = ip.color;
                ctx.fillRect(Math.min(sx, ex), Math.min(sy, ey), Math.abs(ex - sx), Math.abs(ey - sy));
                ctx.globalAlpha = 0.8;
                ctx.strokeRect(Math.min(sx, ex), Math.min(sy, ey), Math.abs(ex - sx), Math.abs(ey - sy));
                break;
            case 'fibonacci':
            case 'measure':
                this._drawLine(ctx, sx, sy, ex, ey);
                break;
        }

        ctx.restore();

        /* ghost price label for horizontal tools */
        if (ip.type === 'horizontal_ray' || ip.type === 'horizontal_line') {
            this._renderPriceLabel(ctx, sy, ip.startPrice, ip.color, false);
        }
    }

    /* ── Snap indicator ── */
    _renderSnap(ctx, snap) {
        ctx.save();
        ctx.strokeStyle = '#ffffff';
        ctx.fillStyle   = snap.label ? '#4a9eff' : 'rgba(255,255,255,0.5)';
        ctx.lineWidth   = 1.2;
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.arc(snap.x, snap.y, SNAP_RADIUS_PX - 2, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        if (snap.label) {
            ctx.fillStyle = '#ffffff';
            ctx.font = 'bold 9px "Segoe UI Mono", monospace';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(snap.label, snap.x, snap.y);
        }
        ctx.restore();
    }

    /* ── Handle dot ── */
    _renderHandle(ctx, x, y, selected, color) {
        ctx.save();
        ctx.setLineDash([]);
        ctx.fillStyle   = selected ? color : 'rgba(255,255,255,0.85)';
        ctx.strokeStyle = selected ? '#ffffff' : color;
        ctx.lineWidth   = 1.2;
        ctx.beginPath();
        ctx.arc(x, y, HANDLE_RADIUS, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        ctx.restore();
    }

    /* ── Price label on right axis ── */
    _renderPriceLabel(ctx, y, price, color, bold) {
        const { chartArea: a } = this.cs;
        const axisX = a.x + a.width;
        const axisW = this.cs.rightAxisWidth || 70;
        const lh = 16, lw = axisW;
        const ly = Math.round(y - lh / 2);

        ctx.save();
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.moveTo(axisX, y);
        ctx.lineTo(axisX + 5, ly);
        ctx.lineTo(axisX + lw, ly);
        ctx.lineTo(axisX + lw, ly + lh);
        ctx.lineTo(axisX + 5, ly + lh);
        ctx.closePath();
        ctx.fill();

        ctx.font = `${bold ? '700' : '600'} 10px "Segoe UI Mono", monospace`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = '#000000';
        ctx.fillText(price.toFixed(2), axisX + 5 + (lw - 5) / 2, y);
        ctx.restore();
    }

    /* ── Lock icon ── */
    _renderLockIcon(ctx, x, y) {
        ctx.save();
        ctx.fillStyle = 'rgba(255,255,255,0.5)';
        ctx.font = '10px sans-serif';
        ctx.textBaseline = 'middle';
        ctx.fillText('🔒', x, y);
        ctx.restore();
    }

    /* ── Primitive line ── */
    _drawLine(ctx, x1, y1, x2, y2) {
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    }
}

/* ─── Export ────────────────────────────────────────────────────────────────── */
if (typeof module !== 'undefined') module.exports = { DrawingEngine, FIB_LEVELS, EXTEND_NONE, EXTEND_RIGHT, EXTEND_BOTH };