const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  HeadingLevel, AlignmentType, BorderStyle, WidthType, ShadingType,
  VerticalAlign, PageNumber, PageBreak, LevelFormat, Header, Footer,
  TabStopType, TabStopPosition, UnderlineType
} = require('docx');
const fs = require('fs');

const COLORS = {
  darkBg: '050709',
  primary: '00B894',
  accent: '00CEFF',
  amber: 'D4A85C',
  red: 'E84060',
  blue: '3B82F6',
  darkBlue: '1E3A5F',
  lightBlue: 'D6EAF8',
  lightGreen: 'D5F5E3',
  lightAmber: 'FEF9E7',
  lightRed: 'FDEDEC',
  headerBg: '1A2744',
  tableHeader: '1E3A5F',
  tableAlt: 'EBF5FB',
  textDark: '1A1A2E',
  textMid: '34495E',
  border: 'AED6F1',
  white: 'FFFFFF',
  sectionBg: 'F0F8FF',
};

const border = (color = COLORS.border) => ({
  style: BorderStyle.SINGLE, size: 4, color
});
const borders = (color) => ({ top: border(color), bottom: border(color), left: border(color), right: border(color) });
const noBorder = () => ({ style: BorderStyle.NONE, size: 0, color: 'FFFFFF' });
const noBorders = () => ({ top: noBorder(), bottom: noBorder(), left: noBorder(), right: noBorder() });

const PAGE_W = 12240;
const MARGIN = 1080;
const CONTENT_W = PAGE_W - 2 * MARGIN;

// ─── Text helpers ───────────────────────────────────────────────────────────

const run = (text, opts = {}) => new TextRun({
  text, font: 'Calibri', size: opts.size || 22,
  bold: opts.bold || false, italics: opts.italic || false,
  color: opts.color || COLORS.textDark,
  underline: opts.underline ? { type: UnderlineType.SINGLE } : undefined,
  break: opts.break || 0,
});

const monoRun = (text, opts = {}) => new TextRun({
  text, font: 'Courier New', size: opts.size || 18,
  bold: opts.bold || false, color: opts.color || '2E86AB',
});

const para = (children, opts = {}) => new Paragraph({
  children: Array.isArray(children) ? children : [run(children, opts)],
  heading: opts.heading,
  alignment: opts.align || AlignmentType.LEFT,
  spacing: { before: opts.before || 0, after: opts.after || 120 },
  indent: opts.indent ? { left: opts.indent } : undefined,
  border: opts.border,
  shading: opts.shading,
  numbering: opts.numbering,
});

const heading1 = (text) => para(
  [run(text, { bold: true, size: 32, color: COLORS.white })],
  {
    heading: HeadingLevel.HEADING_1, before: 240, after: 200,
    shading: { fill: COLORS.headerBg, type: ShadingType.CLEAR },
    border: { left: { style: BorderStyle.SINGLE, size: 20, color: COLORS.primary } },
  }
);

const heading2 = (text) => para(
  [run(text, { bold: true, size: 26, color: COLORS.darkBlue })],
  {
    heading: HeadingLevel.HEADING_2, before: 280, after: 120,
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: COLORS.primary } },
  }
);

const heading3 = (text) => para(
  [run(text, { bold: true, size: 22, color: COLORS.textDark })],
  { heading: HeadingLevel.HEADING_3, before: 200, after: 80 }
);

const heading4 = (text) => para(
  [run(text, { bold: true, size: 20, color: COLORS.textMid })],
  { before: 160, after: 60 }
);

const body = (text, opts = {}) => para(
  [run(text, { size: 20, ...opts })], { before: 60, after: 80, ...opts }
);

const bullet = (text, bold_prefix = '') => para(
  bold_prefix
    ? [run(bold_prefix, { bold: true, size: 20 }), run(text, { size: 20 })]
    : [run(text, { size: 20 })],
  { numbering: { reference: 'bullets', level: 0 }, before: 40, after: 40 }
);

const numbered = (text, level = 0) => para(
  [run(text, { size: 20 })],
  { numbering: { reference: 'numbers', level }, before: 40, after: 40 }
);

const codeBlock = (text) => para(
  [monoRun(text)],
  {
    before: 80, after: 80, indent: 360,
    shading: { fill: 'F4F6F7', type: ShadingType.CLEAR },
    border: { left: { style: BorderStyle.SINGLE, size: 12, color: COLORS.accent } },
  }
);

const callout = (text, type = 'info') => {
  const fills = { info: 'EBF5FB', warning: 'FEF9E7', danger: 'FDEDEC', success: 'D5F5E3' };
  const borColors = { info: COLORS.accent, warning: COLORS.amber, danger: COLORS.red, success: COLORS.primary };
  return para(
    [run(text, { size: 20, italic: true, color: COLORS.textMid })],
    {
      before: 100, after: 100, indent: 360,
      shading: { fill: fills[type], type: ShadingType.CLEAR },
      border: { left: { style: BorderStyle.SINGLE, size: 16, color: borColors[type] } },
    }
  );
};

const spacer = (n = 1) => Array(n).fill(null).map(() => para('', { after: 0 }));

// ─── Table helpers ──────────────────────────────────────────────────────────

const hdrCell = (text, w, color = COLORS.tableHeader) =>
  new TableCell({
    width: { size: w, type: WidthType.DXA },
    shading: { fill: color, type: ShadingType.CLEAR },
    borders: borders(COLORS.border),
    margins: { top: 80, bottom: 80, left: 100, right: 100 },
    verticalAlign: VerticalAlign.CENTER,
    children: [para([run(text, { bold: true, size: 18, color: COLORS.white })], { align: AlignmentType.CENTER })],
  });

const cell = (text, w, opts = {}) =>
  new TableCell({
    width: { size: w, type: WidthType.DXA },
    shading: opts.fill ? { fill: opts.fill, type: ShadingType.CLEAR } : undefined,
    borders: borders(COLORS.border),
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    verticalAlign: VerticalAlign.TOP,
    children: [para(
      Array.isArray(text)
        ? text
        : [run(text, { size: 18, bold: opts.bold, color: opts.color || COLORS.textDark })],
      { align: opts.align || AlignmentType.LEFT }
    )],
  });

const mkTable = (rows, widths) => new Table({
  width: { size: CONTENT_W, type: WidthType.DXA },
  columnWidths: widths,
  rows,
});

// ─── STATUS BADGE ────────────────────────────────────────────────────────────
const badge = (text, color) => new TextRun({ text: ` ${text} `, font: 'Calibri', size: 16, bold: true, color: COLORS.white, shading: { fill: color, type: ShadingType.CLEAR } });

// ────────────────────────────────────────────────────────────────────────────
// DOCUMENT CONTENT
// ────────────────────────────────────────────────────────────────────────────

const children = [];

// ════════════════════════════════════════════════════════════════
// COVER SECTION
// ════════════════════════════════════════════════════════════════

children.push(
  para([run('QULLAMAGGIE TERMINAL', { bold: true, size: 52, color: COLORS.primary })], { align: AlignmentType.CENTER, after: 60 }),
  para([run('POLYGON.IO + IBKR HYBRID ARCHITECTURE', { bold: true, size: 30, color: COLORS.accent })], { align: AlignmentType.CENTER, after: 60 }),
  para([run('Complete Implementation Blueprint', { italic: true, size: 24, color: COLORS.textMid })], { align: AlignmentType.CENTER, after: 80 }),
  para([run('─────────────────────────────────────────────────────────────', { size: 18, color: COLORS.border })], { align: AlignmentType.CENTER, after: 80 }),
  para([
    run('Data Provider:  ', { bold: true, size: 20 }),
    run('Polygon.io  (REST + WebSocket)  ·  US Equities, Options, Crypto', { size: 20, color: COLORS.textMid }),
  ], { align: AlignmentType.CENTER, after: 40 }),
  para([
    run('Execution Broker:  ', { bold: true, size: 20 }),
    run('Interactive Brokers  (TWS / IB Gateway)  ·  Order Routing Only', { size: 20, color: COLORS.textMid }),
  ], { align: AlignmentType.CENTER, after: 40 }),
  para([
    run('Framework:  ', { bold: true, size: 20 }),
    run('PySide6 · ib_insync · polygon-api-client', { size: 20, color: COLORS.textMid }),
  ], { align: AlignmentType.CENTER, after: 240 }),
  new Paragraph({ children: [new PageBreak()] }),
);

// ════════════════════════════════════════════════════════════════
// 1. EXECUTIVE SUMMARY
// ════════════════════════════════════════════════════════════════

children.push(heading1('1. EXECUTIVE SUMMARY & MOTIVATION'));
children.push(body('The current IBKR-only architecture suffers from latency because IB Gateway WebSocket is a streaming relay that adds 30–150 ms over the wire before data reaches the UI. Polygon.io provides a dedicated market-data WebSocket cluster (US equities) with sub-10 ms median latency, a REST API with 2-year historical coverage, and a strict developer contract: one feed, zero connection management, zero IB API quota sharing.'));
children.push(heading2('1.1  Problem Statement'));
children.push(body('Current bottlenecks in the IBKR-only stack:'));
children.push(bullet('IB Gateway adds extra hop latency — every tick goes TWS → IB API → ib_insync → MarketDataWorker → Qt → UI.'));
children.push(bullet('IB API rate limits shared between market data and order management — heavy scanning degrades order execution throughput.'));
children.push(bullet('Historical data (reqHistoricalData) is synchronous, blocking the event loop in the chart worker threads.'));
children.push(bullet('IBKR connection is single-threaded via ib_insync event loop, making parallel symbol resolution slow.'));
children.push(bullet('Reconnect on IB Gateway restart disrupts live chart data and watchlist ticks simultaneously.'));
children.push(heading2('1.2  Solution: Data/Execution Separation'));
children.push(callout('Core Principle: Polygon.io owns 100% of market data. IBKR owns 100% of order execution. The two never share a connection, rate limit, or thread pool.', 'success'));
children.push(body('This hybrid approach achieves:'));

const splitTable = mkTable([
  new TableRow({ children: [hdrCell('Concern', 2800), hdrCell('Before (IBKR only)', 4800), hdrCell('After (Polygon + IBKR)', 4000)] }),
  new TableRow({ children: [cell('Live tick latency', 2800, {bold:true}), cell('30–150 ms via IB Gateway relay', 4800, {fill:'FDEDEC'}), cell('< 10 ms via Polygon WS cluster', 4000, {fill:'D5F5E3'})] }),
  new TableRow({ children: [cell('Historical fetch', 2800, {bold:true}), cell('Synchronous reqHistoricalData, blocks thread', 4800, {fill:'FDEDEC'}), cell('Async REST agg/v2 with 2-year free coverage', 4000, {fill:'D5F5E3'})] }),
  new TableRow({ children: [cell('Rate limits', 2800, {bold:true}), cell('Shared API quota for data + orders', 4800, {fill:'FDEDEC'}), cell('Independent quotas, orders never throttled', 4000, {fill:'D5F5E3'})] }),
  new TableRow({ children: [cell('Reconnect impact', 2800, {bold:true}), cell('IB restart kills data AND orders', 4800, {fill:'FDEDEC'}), cell('Data feed independent from order connection', 4000, {fill:'D5F5E3'})] }),
  new TableRow({ children: [cell('Symbol search', 2800, {bold:true}), cell('reqMatchingSymbols — slow, synchronous', 4800, {fill:'FDEDEC'}), cell('Polygon Ticker Search API — fast async REST', 4000, {fill:'D5F5E3'})] }),
  new TableRow({ children: [cell('Options chain', 2800, {bold:true}), cell('Not available via current stack', 4800, {fill:'FDEDEC'}), cell('Polygon Options Chain with greeks', 4000, {fill:'D5F5E3'})] }),
], [2800, 4800, 4000]);
children.push(splitTable, ...spacer(1));

children.push(new Paragraph({ children: [new PageBreak()] }));

// ════════════════════════════════════════════════════════════════
// 2. HIGH-LEVEL ARCHITECTURE
// ════════════════════════════════════════════════════════════════

children.push(heading1('2. HIGH-LEVEL ARCHITECTURE'));
children.push(body('The new system is split into three independent planes that never block each other:'));

children.push(heading2('2.1  Three-Plane Architecture'));

const planeTable = mkTable([
  new TableRow({ children: [hdrCell('Plane', 2200), hdrCell('Technology', 3800), hdrCell('Responsibility', 5600)] }),
  new TableRow({ children: [
    cell('DATA PLANE', 2200, {fill:'EBF5FB', bold:true, color:COLORS.darkBlue}),
    cell('Polygon.io REST + WebSocket', 3800),
    cell('All market data: live ticks, OHLCV bars, options chain, fundamentals, symbol search, news', 5600),
  ]}),
  new TableRow({ children: [
    cell('ORDER PLANE', 2200, {fill:'FEF9E7', bold:true, color:'7D4A00'}),
    cell('ib_insync → IB Gateway → IBKR', 3800),
    cell('Authenticated order placement, order status, account balance, positions from broker', 5600),
  ]}),
  new TableRow({ children: [
    cell('UI PLANE', 2200, {fill:'D5F5E3', bold:true, color:'145A32'}),
    cell('PySide6 QMainWindow + QThread workers', 3800),
    cell('Chart rendering, watchlist/scanner tables, order dialog, position panel, alerts engine', 5600),
  ]}),
], [2200, 3800, 5600]);
children.push(planeTable, ...spacer(1));

children.push(heading2('2.2  Component Map'));
children.push(body('Below is every component and which plane it belongs to after the migration:'));

const compMap = mkTable([
  new TableRow({ children: [hdrCell('Component', 3200), hdrCell('Plane', 1800), hdrCell('Before', 3100), hdrCell('After', 3500)] }),
  new TableRow({ children: [cell('PolygonDataFeed', 3200, {bold:true}), cell('DATA', 1800, {fill:'EBF5FB', color:COLORS.darkBlue}), cell('(did not exist)', 3100, {fill:'F8F9FA', color:'999'}), cell('New: manages WS + REST calls', 3500, {fill:'D5F5E3'})] }),
  new TableRow({ children: [cell('IBKRDataFetcher (chart)', 3200, {bold:true}), cell('DATA', 1800, {fill:'EBF5FB', color:COLORS.darkBlue}), cell('Uses ib_insync reqHistoricalData', 3100, {fill:'FDEDEC'}), cell('Replaced by PolygonChartFetcher', 3500, {fill:'D5F5E3'})] }),
  new TableRow({ children: [cell('MarketDataWorker', 3200, {bold:true}), cell('DATA', 1800, {fill:'EBF5FB', color:COLORS.darkBlue}), cell('ib_insync pendingTickersEvent', 3100, {fill:'FDEDEC'}), cell('Replaced by PolygonWebSocketWorker', 3500, {fill:'D5F5E3'})] }),
  new TableRow({ children: [cell('IBKRSymbolResolver', 3200, {bold:true}), cell('DATA', 1800, {fill:'EBF5FB', color:COLORS.darkBlue}), cell('reqContractDetails (blocking)', 3100, {fill:'FDEDEC'}), cell('Replaced by PolygonTickerSearch', 3500, {fill:'D5F5E3'})] }),
  new TableRow({ children: [cell('IBKRTradingClient', 3200, {bold:true}), cell('ORDER', 1800, {fill:'FEF9E7', color:'7D4A00'}), cell('Used for data AND orders', 3100, {fill:'FDEDEC'}), cell('Orders only — no data calls', 3500, {fill:'FEF9E7'})] }),
  new TableRow({ children: [cell('PositionManager', 3200, {bold:true}), cell('ORDER', 1800, {fill:'FEF9E7', color:'7D4A00'}), cell('Polls trader.positions()', 3100), cell('No change — polls IBKR only', 3500)] }),
  new TableRow({ children: [cell('AccountManager', 3200, {bold:true}), cell('ORDER', 1800, {fill:'FEF9E7', color:'7D4A00'}), cell('Polls trader.margins()', 3100), cell('No change — polls IBKR only', 3500)] }),
  new TableRow({ children: [cell('AlertEngine', 3200, {bold:true}), cell('UI', 1800, {fill:'D5F5E3', color:'145A32'}), cell('Reads ticks from IB', 3100), cell('Reads ticks from Polygon feed', 3500)] }),
  new TableRow({ children: [cell('StopLossManager', 3200, {bold:true}), cell('UI', 1800, {fill:'D5F5E3', color:'145A32'}), cell('on_ticks() from IB WS', 3100), cell('on_ticks() from Polygon feed', 3500)] }),
  new TableRow({ children: [cell('CandlestickChart', 3200, {bold:true}), cell('UI', 1800, {fill:'D5F5E3', color:'145A32'}), cell('IBKRDataFetcher for bars', 3100), cell('PolygonChartFetcher for bars', 3500)] }),
  new TableRow({ children: [cell('TickerBoard / Watchlist', 3200, {bold:true}), cell('UI', 1800, {fill:'D5F5E3', color:'145A32'}), cell('IB pendingTickersEvent', 3100), cell('Polygon WS flat_book feed', 3500)] }),
], [3200, 1800, 3100, 3500]);
children.push(compMap, ...spacer(1));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ════════════════════════════════════════════════════════════════
// 3. POLYGON.IO API PLAN
// ════════════════════════════════════════════════════════════════

children.push(heading1('3. POLYGON.IO API PLAN'));
children.push(heading2('3.1  Polygon Plan Selection'));

children.push(callout('Recommended Plan: Polygon Starter ($29/month) or Polygon Stocks Developer ($79/month). The Stocks Developer plan provides real-time WebSocket + 15-year historical + options chain access — everything needed for a full swing trading terminal.', 'info'));

const planTable = mkTable([
  new TableRow({ children: [hdrCell('Feature', 3000), hdrCell('Free', 1800), hdrCell('Starter $29', 2400), hdrCell('Developer $79', 2400), hdrCell('Use In App', 2000)] }),
  new TableRow({ children: [cell('Real-time WebSocket ticks', 3000), cell('❌', 1800, {align:'center'}), cell('✅ 15 min delay', 2400, {align:'center'}), cell('✅ Real-time', 2400, {align:'center', fill:'D5F5E3'}), cell('WatchList, Alerts', 2000)] }),
  new TableRow({ children: [cell('OHLCV Aggregates (bars)', 3000), cell('2 years', 1800, {align:'center'}), cell('5 years', 2400, {align:'center'}), cell('15 years', 2400, {align:'center', fill:'D5F5E3'}), cell('Chart', 2000)] }),
  new TableRow({ children: [cell('Options Chain + Greeks', 3000), cell('❌', 1800, {align:'center'}), cell('❌', 2400, {align:'center'}), cell('✅', 2400, {align:'center', fill:'D5F5E3'}), cell('Options Panel', 2000)] }),
  new TableRow({ children: [cell('REST API rate limit', 3000), cell('5 req/min', 1800, {align:'center'}), cell('Unlimited', 2400, {align:'center', fill:'D5F5E3'}), cell('Unlimited', 2400, {align:'center', fill:'D5F5E3'}), cell('All REST calls', 2000)] }),
  new TableRow({ children: [cell('WebSocket connections', 3000), cell('1', 1800, {align:'center'}), cell('1 + delayed', 2400, {align:'center'}), cell('Unlimited', 2400, {align:'center', fill:'D5F5E3'}), cell('Multiple feeds', 2000)] }),
  new TableRow({ children: [cell('Ticker Search / Details', 3000), cell('✅', 1800, {align:'center', fill:'D5F5E3'}), cell('✅', 2400, {align:'center', fill:'D5F5E3'}), cell('✅', 2400, {align:'center', fill:'D5F5E3'}), cell('Symbol Search', 2000)] }),
  new TableRow({ children: [cell('News API', 3000), cell('✅ limited', 1800, {align:'center'}), cell('✅', 2400, {align:'center'}), cell('✅', 2400, {align:'center', fill:'D5F5E3'}), cell('Stock Info', 2000)] }),
], [3000, 1800, 2400, 2400, 2000]);
children.push(planTable, ...spacer(1));

children.push(heading2('3.2  Polygon REST Endpoints Used'));

const endpointTable = mkTable([
  new TableRow({ children: [hdrCell('Use Case', 2800), hdrCell('Endpoint', 5000), hdrCell('Replaces', 3800)] }),
  new TableRow({ children: [cell('Chart OHLCV bars', 2800), cell('/v2/aggs/ticker/{sym}/range/{mult}/{span}/{from}/{to}', 5000), cell('reqHistoricalData (ib_insync)', 3800)] }),
  new TableRow({ children: [cell('Previous close / snapshot', 2800), cell('/v2/snapshot/locale/us/markets/stocks/tickers/{sym}', 5000), cell('reqMktData close field', 3800)] }),
  new TableRow({ children: [cell('Symbol search / resolve', 2800), cell('/v3/reference/tickers?search={q}&active=true&limit=20', 5000), cell('IBKRSymbolResolver reqContractDetails', 3800)] }),
  new TableRow({ children: [cell('Ticker details / fundamentals', 2800), cell('/v3/reference/tickers/{sym}', 5000), cell('yfinance (StockInfoDialog)', 3800)] }),
  new TableRow({ children: [cell('Options chain', 2800), cell('/v3/reference/options/contracts?underlying_ticker={sym}', 5000), cell('(not available previously)', 3800)] }),
  new TableRow({ children: [cell('Options greeks / snapshot', 2800), cell('/v3/snapshot/options/{sym}', 5000), cell('(not available previously)', 3800)] }),
  new TableRow({ children: [cell('Company news', 2800), cell('/v2/reference/news?ticker={sym}&limit=10', 5000), cell('yfinance news', 3800)] }),
  new TableRow({ children: [cell('Market status / holidays', 2800), cell('/v1/marketstatus/now  and  /v1/marketstatus/upcoming', 5000), cell('Manual IST calculation', 3800)] }),
  new TableRow({ children: [cell('SMA / EMA / MACD', 2800), cell('/v1/indicators/sma/{sym}?timespan=day&window=20', 5000), cell('Client-side calculation', 3800)] }),
], [2800, 5000, 3800]);
children.push(endpointTable, ...spacer(1));

children.push(heading2('3.3  Polygon WebSocket Feed Plan'));
children.push(body('Polygon WebSocket uses a single persistent connection authenticated per API key. Subscribe with A.* (second-level aggregates) for tick-like updates or T.* for individual trades.'));

const wsTable = mkTable([
  new TableRow({ children: [hdrCell('Channel Prefix', 2400), hdrCell('What it delivers', 4400), hdrCell('Qullamaggie consumer', 4800)] }),
  new TableRow({ children: [cell('A.{sym}', 2400), cell('Second aggregates (open, high, low, close, volume every 1s)', 4400), cell('Watchlist LTP, scanner tick, alert engine, SL manager, positions PnL', 4800)] }),
  new TableRow({ children: [cell('T.{sym}', 2400), cell('Individual trade events (price, size, timestamp)', 4400), cell('Live chart update_live_data(), ticker board', 4800)] }),
  new TableRow({ children: [cell('Q.{sym}', 2400), cell('NBBO quote updates (bid, ask, bid_size, ask_size)', 4400), cell('Order dialog Level I bid/ask, market depth', 4800)] }),
  new TableRow({ children: [cell('AM.{sym}', 2400), cell('Minute-level aggregates for lower-timeframe charts', 4400), cell('1-minute candlestick chart live candle append', 4800)] }),
  new TableRow({ children: [cell('status', 2400), cell('Connection status, auth success/error', 4400), cell('PolygonWebSocketWorker reconnect logic', 4800)] }),
], [2400, 4400, 4800]);
children.push(wsTable, ...spacer(1));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ════════════════════════════════════════════════════════════════
// 4. NEW FILE STRUCTURE
// ════════════════════════════════════════════════════════════════

children.push(heading1('4. NEW FILE & MODULE STRUCTURE'));
children.push(body('The migration introduces a new polygon/ package alongside the existing ibkr/ package. The ibkr/ package is stripped of all market data logic. Only authentication, order routing, account, and position fetching remain.'));

children.push(heading2('4.1  New Directory Layout'));

const codeLines = [
  'qullamaggie/',
  '├── ibkr/',
  '│   ├── core/',
  '│   │   ├── trading_client.py        ← KEEP (orders only)',
  '│   │   ├── position_manager.py      ← KEEP (polls IBKR)',
  '│   │   ├── account_manager.py       ← KEEP (polls IBKR)',
  '│   │   ├── stop_loss_manager.py     ← UPDATE (consume Polygon ticks)',
  '│   │   ├── alert_management_system.py ← UPDATE (consume Polygon ticks)',
  '│   │   ├── market_data_worker.py    ← DELETE (replaced by Polygon)',
  '│   │   └── api_circuit_breaker.py   ← REUSE for Polygon HTTP calls',
  '│   └── utils/',
  '│       └── data_converter.py        ← KEEP for order response normalize',
  '│',
  '├── polygon/                         ← NEW package',
  '│   ├── __init__.py',
  '│   ├── auth.py                      ← PolygonAuth (API key store + validate)',
  '│   ├── client.py                    ← PolygonRESTClient (thin wrapper)',
  '│   ├── websocket_worker.py          ← PolygonWebSocketWorker (QThread)',
  '│   ├── chart_fetcher.py             ← PolygonChartFetcher (replaces IBKRDataFetcher)',
  '│   ├── symbol_resolver.py           ← PolygonSymbolResolver (replaces IBKRSymbolResolver)',
  '│   ├── snapshot_service.py          ← Batch snapshot calls for startup LTP',
  '│   ├── options_service.py           ← Options chain + greeks',
  '│   ├── news_service.py              ← Ticker news feed',
  '│   └── data_normalizer.py           ← Converts Polygon JSON → app dicts',
  '│',
  '├── login_setup/',
  '│   ├── dual_mode_login_manager.py   ← UPDATE (add Polygon API key page)',
  '│   ├── broker_factory.py            ← UPDATE (wire Polygon into data layer)',
  '│   └── polygon_auth_setup.py        ← NEW (save/load Polygon key encrypted)',
  '│',
  '└── chart_engine/',
  '    └── core/',
  '        └── ibkr_data_fetcher.py     ← REPLACE with polygon_data_fetcher.py',
];
codeLines.forEach(l => children.push(codeBlock(l)));
children.push(...spacer(1));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ════════════════════════════════════════════════════════════════
// 5. LOGIN FLOW
// ════════════════════════════════════════════════════════════════

children.push(heading1('5. NEW LOGIN FLOW — END TO END'));
children.push(body('The DualModeLoginManager gains a new Page 5 specifically for Polygon configuration. IBKR connection is kept for order execution. Both authentications happen in sequence before the main window opens.'));

children.push(heading2('5.1  Login Page Sequence'));

const loginSeq = mkTable([
  new TableRow({ children: [hdrCell('Page', 1200), hdrCell('Title', 2800), hdrCell('What Happens', 7600)] }),
  new TableRow({ children: [cell('0', 1200, {align:'center',bold:true}), cell('Auto-Login Splash', 2800), cell('Check saved IBKR credentials AND Polygon API key. If both found, skip to main window.', 7600)] }),
  new TableRow({ children: [cell('1', 1200, {align:'center',bold:true}), cell('Broker Selection', 2800), cell('Select AMERICA mode (Kite India mode unaffected). Select LIVE or PAPER.', 7600)] }),
  new TableRow({ children: [cell('2', 1200, {align:'center',bold:true}), cell('IBKR Connection', 2800), cell('Connect to TWS/Gateway. Validate isConnected(). This page is ONLY for order-plane auth. No data flows here.', 7600)] }),
  new TableRow({ children: [cell('3', 1200, {align:'center',bold:true}), cell('Polygon API Key', 2800), cell('NEW PAGE: Input Polygon API key. Validate with a test REST call to /v2/snapshot. Show plan tier detected. Save key encrypted to ~/.qullamaggie/polygon_key.enc.', 7600, {fill:'D5F5E3'})] }),
  new TableRow({ children: [cell('4', 1200, {align:'center',bold:true}), cell('Connection Summary', 2800), cell('NEW PAGE: Show both connections: IBKR (green dot, account ID) + Polygon (green dot, plan tier). Launch button enabled only when both are green.', 7600, {fill:'D5F5E3'})] }),
], [1200, 2800, 7600]);
children.push(loginSeq, ...spacer(1));

children.push(heading2('5.2  Polygon API Key Page — Widget Spec'));
children.push(body('Page 3 of the login dialog replaces the static IBKR host config with the Polygon key input:'));
children.push(bullet('QLineEdit (echoMode Password) for API key entry with a "Show/Hide" eye button.'));
children.push(bullet('QCheckBox "Remember API key" — saves encrypted to disk via EnhancedTokenManager.'));
children.push(bullet('QPushButton "VALIDATE KEY" — calls /v2/snapshot/locale/us/markets/stocks/tickers/AAPL and verifies HTTP 200.'));
children.push(bullet('QLabel shows plan tier: FREE / STARTER / DEVELOPER based on the response latency and data freshness field.'));
children.push(bullet('If validation fails, show the exact HTTP error status with a retry option.'));

children.push(heading2('5.3  post-Login Initialization Sequence'));
children.push(body('After both auth steps succeed, the MainWindow __init__ initializes components in this strict order to avoid race conditions:'));

const initSeq = mkTable([
  new TableRow({ children: [hdrCell('Step', 800), hdrCell('Action', 3600), hdrCell('Thread', 1800), hdrCell('Blocks Window?', 2000), hdrCell('Notes', 3400)] }),
  new TableRow({ children: [cell('1', 800), cell('Create IBKRTradingClient wrapper', 3600), cell('Main', 1800), cell('No', 2000), cell('Order-plane only, no data requests', 3400)] }),
  new TableRow({ children: [cell('2', 800), cell('Create PolygonRESTClient with API key', 3600), cell('Main', 1800), cell('No', 2000), cell('Single authenticated session object', 3400)] }),
  new TableRow({ children: [cell('3', 800), cell('Start PolygonWebSocketWorker QThread', 3600), cell('Worker', 1800), cell('No', 2000), cell('Connects asynchronously, emits connected signal', 3400)] }),
  new TableRow({ children: [cell('4', 800), cell('Load instrument map from Polygon Tickers API', 3600), cell('Worker', 1800), cell('No', 2000), cell('Replaces IBKRInstrumentLoader', 3400)] }),
  new TableRow({ children: [cell('5', 800), cell('Fetch account info from IBKR', 3600), cell('Worker', 1800), cell('No', 2000), cell('AccountManager.refresh_margins()', 3400)] }),
  new TableRow({ children: [cell('6', 800), cell('Fetch open positions from IBKR', 3600), cell('Worker', 1800), cell('No', 2000), cell('PositionManager.fetch_positions_from_broker()', 3400)] }),
  new TableRow({ children: [cell('7', 800), cell('Fetch batch snapshots for watchlist symbols', 3600), cell('Worker', 1800), cell('No', 2000), cell('SnapshotService.fetch_batch() → watchlist LTP', 3400)] }),
  new TableRow({ children: [cell('8', 800), cell('Restore last chart symbol from config', 3600), cell('Main', 1800), cell('No', 2000), cell('PolygonChartFetcher loads bars from REST', 3400)] }),
], [800, 3600, 1800, 2000, 3400]);
children.push(initSeq, ...spacer(1));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ════════════════════════════════════════════════════════════════
// 6. IMPLEMENTATION — NEW MODULES
// ════════════════════════════════════════════════════════════════

children.push(heading1('6. IMPLEMENTATION PLAN — NEW MODULES'));

children.push(heading2('6.1  polygon/auth.py — PolygonAuth'));
children.push(body('Responsible for secure storage and retrieval of the Polygon API key.'));
children.push(heading3('Key Methods:'));
children.push(bullet('save_api_key(key: str) → None — encrypts and stores key via EnhancedTokenManager.'));
children.push(bullet('load_api_key() → Optional[str] — decrypts and returns key, or None if not saved.'));
children.push(bullet('validate_api_key(key: str) → Tuple[bool, str] — makes a live REST call, returns (success, plan_tier).'));
children.push(bullet('clear_api_key() → None — removes key from encrypted storage.'));
children.push(heading3('Storage:'));
children.push(body('Use the existing EnhancedTokenManager.save_dialog_state("polygon_api_key", encrypted_blob). Never store the raw key. Use the same Fernet encryption already in the codebase.'));

children.push(heading2('6.2  polygon/client.py — PolygonRESTClient'));
children.push(body('A thin, session-reusing HTTP client around the Polygon REST v2/v3 APIs with automatic retry, rate-limit backoff, and response caching for expensive calls.'));
children.push(heading3('Key Design Decisions:'));
children.push(bullet('Use requests.Session with connection pooling — one session per app lifetime.'));
children.push(bullet('Implement exponential backoff with jitter for HTTP 429 responses (Polygon rate limit).'));
children.push(bullet('Cache historical bars in-memory with the existing MarketAwareDataCache (IST-aware TTLs).'));
children.push(bullet('All fetch methods are sync but called from worker QThread — never call from main thread.'));
children.push(bullet('Inherit the APICircuitBreaker pattern from ibkr/core/api_circuit_breaker.py.'));
children.push(heading3('Core Fetch Methods:'));
children.push(codeBlock('def get_agg_bars(symbol, multiplier, timespan, from_date, to_date) -> List[Dict]'));
children.push(codeBlock('def get_snapshot(symbol) -> Dict    # single ticker, includes prev_close'));
children.push(codeBlock('def get_batch_snapshots(symbols: List[str]) -> Dict[str, Dict]'));
children.push(codeBlock('def search_tickers(query: str, limit=20) -> List[Dict]'));
children.push(codeBlock('def get_ticker_details(symbol: str) -> Dict'));
children.push(codeBlock('def get_options_chain(underlying: str, expiration_date=None) -> List[Dict]'));
children.push(codeBlock('def get_options_snapshot(underlying: str) -> Dict'));
children.push(codeBlock('def get_news(symbol: str, limit=10) -> List[Dict]'));
children.push(codeBlock('def get_market_status() -> Dict'));
children.push(codeBlock('def get_sma(symbol, timespan="day", window=20) -> List[Dict]'));

children.push(heading2('6.3  polygon/websocket_worker.py — PolygonWebSocketWorker'));
children.push(body('QThread subclass that manages the Polygon WebSocket connection. Replaces ibkr/core/market_data_worker.py entirely.'));
children.push(heading3('Signals (same interface as old MarketDataWorker for backward compat):'));
children.push(codeBlock('data_received = Signal(list)         # list of tick dicts'));
children.push(codeBlock('connection_established = Signal()'));
children.push(codeBlock('connection_closed = Signal()'));
children.push(codeBlock('connection_error = Signal(str)'));
children.push(codeBlock('order_update = Signal(dict)          # stub — IBKR handles this now'));
children.push(heading3('Tick Dict Format (normalized to match existing app consumers):'));
children.push(codeBlock('{ "tradingsymbol": "AAPL", "last_price": 189.42, "instrument_token": 2045,'));
children.push(codeBlock('  "volume_traded": 1200, "change_percent": 0.38,'));
children.push(codeBlock('  "ohlc": {"open": 188.0, "high": 190.1, "low": 187.5, "close": 188.8 } }'));
children.push(heading3('Subscription Management:'));
children.push(bullet('subscribe(symbols: List[str]) — sends {"action":"subscribe","params":"A.AAPL,T.AAPL"}.'));
children.push(bullet('unsubscribe(symbols: List[str]) — sends unsubscribe action.'));
children.push(bullet('set_symbols(symbols: List[str]) — diffs current vs desired, sub/unsub delta only (critical for performance).'));
children.push(bullet('Heartbeat: Polygon sends {"ev":"status","status":"connected"} every 30s. If missed, trigger reconnect.'));
children.push(bullet('Auto-reconnect with exponential backoff, same pattern as ReconnectionManager.'));

children.push(heading2('6.4  polygon/chart_fetcher.py — PolygonChartFetcher'));
children.push(body('Drop-in replacement for chart_engine/core/ibkr_data_fetcher.py. Implements the same KiteDataFetcher interface so chart_widget.py requires zero changes.'));
children.push(heading3('Timespan Mapping (IBKR bar_size → Polygon timespan):'));
const timespanTable = mkTable([
  new TableRow({ children: [hdrCell('Chart Interval', 3600), hdrCell('Polygon multiplier', 3000), hdrCell('Polygon timespan', 3000), hdrCell('Max lookback', 2000)] }),
  new TableRow({ children: [cell('"1 day"', 3600), cell('1', 3000), cell('"day"', 3000), cell('15 years', 2000)] }),
  new TableRow({ children: [cell('"60minute"', 3600), cell('1', 3000), cell('"hour"', 3000), cell('5 years', 2000)] }),
  new TableRow({ children: [cell('"30minute"', 3600), cell('30', 3000), cell('"minute"', 3000), cell('2 years', 2000)] }),
  new TableRow({ children: [cell('"15minute"', 3600), cell('15', 3000), cell('"minute"', 3000), cell('2 years', 2000)] }),
  new TableRow({ children: [cell('"5minute"', 3600), cell('5', 3000), cell('"minute"', 3000), cell('2 years', 2000)] }),
  new TableRow({ children: [cell('"1minute"', 3600), cell('1', 3000), cell('"minute"', 3000), cell('2 years', 2000)] }),
  new TableRow({ children: [cell('"1 week"', 3600), cell('1', 3000), cell('"week"', 3000), cell('15 years', 2000)] }),
], [3600, 3000, 3000, 2000]);
children.push(timespanTable, ...spacer(1));
children.push(heading3('Output Column Mapping (→ pandas DataFrame):'));
children.push(body('Polygon returns {t (ms timestamp), o, h, l, c, v, vw, n}. Convert t to IST-aware datetime index. Map to the app\'s expected columns: date/datetime, open, high, low, close, volume.'));

children.push(heading2('6.5  polygon/symbol_resolver.py — PolygonSymbolResolver'));
children.push(body('Replaces IBKRSymbolResolver. Uses Polygon /v3/reference/tickers search endpoint. Maintains a local cache to avoid redundant API calls for recently searched symbols.'));
children.push(heading3('Output format (same as IBKRSymbolResolver for backward compat):'));
children.push(codeBlock('{ "tradingsymbol": "MSFT", "name": "Microsoft Corporation",'));
children.push(codeBlock('  "exchange": "NASDAQ", "instrument_token": 7408, "currency": "USD",'));
children.push(codeBlock('  "market_cap": 3100000000000, "sector": "Technology" }'));

children.push(heading2('6.6  polygon/options_service.py — OptionsService'));
children.push(body('New capability not available in the IBKR-only stack. Provides the options panel with real-time chain data.'));
children.push(bullet('fetch_chain(underlying, expiration_filter=None) — returns all contracts with strikes, exp dates, and greeks.'));
children.push(bullet('fetch_greeks_snapshot(underlying) — returns delta, gamma, theta, vega, IV per contract.'));
children.push(bullet('stream_options_quotes(contracts: List[str]) — subscribes to O.{contract} WebSocket channel.'));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ════════════════════════════════════════════════════════════════
// 7. MIGRATION CHANGES TO EXISTING FILES
// ════════════════════════════════════════════════════════════════

children.push(heading1('7. MIGRATION — CHANGES TO EXISTING FILES'));
children.push(heading2('7.1  ibkr/core/main_window.py — Changes'));

const mainWindowChanges = mkTable([
  new TableRow({ children: [hdrCell('Location in file', 3600), hdrCell('Current code', 3800), hdrCell('Change', 4200)] }),
  new TableRow({ children: [cell('_init_background_workers()', 3600), cell('Creates MarketDataWorker(real_kite_client)', 3800), cell('Replace with PolygonWebSocketWorker(polygon_client). Wire same signals.', 4200)] }),
  new TableRow({ children: [cell('_initialize_ibkr_instruments()', 3600), cell('Creates IBKRInstrumentLoader + IBKRSymbolResolver', 3800), cell('Replace with PolygonInstrumentLoader + PolygonSymbolResolver.', 4200)] }),
  new TableRow({ children: [cell('_create_chart_data_fetcher()', 3600), cell('Returns IBKRDataFetcher(client)', 3800), cell('Return PolygonChartFetcher(polygon_rest_client).', 4200)] }),
  new TableRow({ children: [cell('_on_market_data(ticks)', 3600), cell('Receives IB ticks, dispatches', 3800), cell('No change — Polygon ticks normalized to same dict format.', 4200)] }),
  new TableRow({ children: [cell('_rebuild_subscription_universe()', 3600), cell('Calls market_data_worker.set_instruments(tokens)', 3800), cell('Call polygon_ws_worker.set_symbols(symbols). Use symbols not tokens.', 4200)] }),
  new TableRow({ children: [cell('__init__ params', 3600), cell('trader, real_kite_client, api_key, access_token', 3800), cell('Add: polygon_client: PolygonRESTClient parameter.', 4200)] }),
  new TableRow({ children: [cell('_init_network_resilience()', 3600), cell('Probes api.kite.trade', 3800), cell('Probe api.polygon.io instead. Update PROBE_URL in network_monitor.py.', 4200)] }),
], [3600, 3800, 4200]);
children.push(mainWindowChanges, ...spacer(1));

children.push(heading2('7.2  login_setup/broker_factory.py — Changes'));
children.push(bullet('create_data_client() — add Polygon branch: creates PolygonRESTClient from stored API key.'));
children.push(bullet('create_client() — no change for IBKRTradingClient creation.'));
children.push(bullet('load_broker_main_window() — add polygon_client kwarg to QullamaggieWindow constructor.'));

children.push(heading2('7.3  ibkr/core/stop_loss_manager.py — Changes'));
children.push(body('The StopLossManager receives ticks via the on_ticks(ticks) slot. The tick format is already normalized by PolygonWebSocketWorker to the same dict schema. No logic changes needed — only re-wiring in main_window.py:'));
children.push(codeBlock('# OLD:  market_data_worker.data_received.connect(self.sl_manager.on_ticks)'));
children.push(codeBlock('# NEW:  polygon_ws_worker.data_received.connect(self.sl_manager.on_ticks)'));

children.push(heading2('7.4  ibkr/core/alert_management_system.py — Changes'));
children.push(body('Same pattern as StopLossManager. The AlertEngine.update_market_data(ticks) method reads from tick dicts. No internal logic changes required:'));
children.push(codeBlock('# In main_window _on_market_data(): same call'));
children.push(codeBlock('# self.alert_system.update_market_data(ticks)  ← unchanged'));
children.push(body('However, the instrument_map token-lookup inside update_market_data() must be updated. Polygon ticks use symbol strings directly, not integer tokens. Remove the instrument_token → symbol lookup branch:'));
children.push(codeBlock('# OLD: token = tick.get("instrument_token") → look up in instrument_map'));
children.push(codeBlock('# NEW: symbol = tick.get("tradingsymbol")  ← already present in Polygon normalized ticks'));

children.push(heading2('7.5  ibkr/widgets/scanner_table.py — Changes'));
children.push(body('The scanner currently uses Finviz for EOD symbol discovery (no change) and subscribes to IBKR tokens for live ticks. After migration:'));
children.push(bullet('_rebuild_token_map() → rename to _rebuild_symbol_map(). Map tradingsymbol → row, not token → row.'));
children.push(bullet('update_data(ticks) — change the token lookup to symbol-first lookup since Polygon ticks carry tradingsymbol directly.'));
children.push(bullet('get_visible_tokens() → rename to get_visible_symbols(). Return List[str] symbols.'));
children.push(bullet('_subscription_rebuild_timer fires _rebuild_subscription_universe() in main_window which calls polygon_ws_worker.set_symbols().'));

children.push(heading2('7.6  ibkr/widgets/watchlist_table.py — Changes'));
children.push(body('TradingTable._rebuild_token_map() similarly uses instrument_token. After migration:'));
children.push(bullet('update_data(ticks) — the sym = self._token_to_symbol.get(token) branch still works IF Polygon ticks include instrument_token (conId) from the qualification step.'));
children.push(bullet('Alternative (simpler): always resolve via tradingsymbol first. Polygon WS ticks always carry the symbol string. No token lookup needed.'));
children.push(bullet('set_instrument_map() — still receives the Polygon instrument map dict. The map structure is identical (tradingsymbol → {...}) so no change.'));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ════════════════════════════════════════════════════════════════
// 8. SUBSCRIPTION STRATEGY
// ════════════════════════════════════════════════════════════════

children.push(heading1('8. POLYGON SUBSCRIPTION STRATEGY'));
children.push(body('Polygon charges no per-symbol fee but has connection-level limits. The subscription universe must be carefully managed to avoid exceeding WS message quotas.'));
children.push(heading2('8.1  Universe Calculation (replaces _rebuild_subscription_universe)'));
children.push(body('The symbol universe is computed on every user action (scroll, symbol change, scan refresh):'));

const universeTable = mkTable([
  new TableRow({ children: [hdrCell('Priority', 1200), hdrCell('Source', 3000), hdrCell('Channel', 3400), hdrCell('Max symbols', 4000)] }),
  new TableRow({ children: [cell('P0', 1200, {bold:true,align:'center'}), cell('Open positions', 3000), cell('A.{sym} + T.{sym}', 3400), cell('All positions — always subscribed', 4000, {fill:'D5F5E3'})] }),
  new TableRow({ children: [cell('P1', 1200, {bold:true,align:'center'}), cell('Active pending paper orders', 3000), cell('A.{sym}', 3400), cell('All pending — always subscribed', 4000, {fill:'D5F5E3'})] }),
  new TableRow({ children: [cell('P2', 1200, {bold:true,align:'center'}), cell('Active chart symbol(s)', 3000), cell('T.{sym} + Q.{sym}', 3400), cell('1–2 symbols (primary + secondary chart)', 4000)] }),
  new TableRow({ children: [cell('P3', 1200, {bold:true,align:'center'}), cell('Watchlist (all tabs)', 3000), cell('A.{sym}', 3400), cell('All watchlist symbols (typically < 100)', 4000)] }),
  new TableRow({ children: [cell('P4', 1200, {bold:true,align:'center'}), cell('Scanner visible rows (viewport + 5 buffer)', 3000), cell('A.{sym}', 3400), cell('Visible rows only (~15–25 symbols)', 4000)] }),
  new TableRow({ children: [cell('P5', 1200, {bold:true,align:'center'}), cell('Alert trigger symbols', 3000), cell('A.{sym}', 3400), cell('All active alerts', 4000)] }),
  new TableRow({ children: [cell('P6', 1200, {bold:true,align:'center'}), cell('Header ticker board', 3000), cell('T.{sym} + A.{sym}', 3400), cell('Max 5 symbols (NIFTY equivalent: SPY, QQQ etc)', 4000)] }),
], [1200, 3000, 3400, 4000]);
children.push(universeTable, ...spacer(1));

children.push(heading2('8.2  set_symbols() Implementation'));
children.push(body('The PolygonWebSocketWorker.set_symbols() method diffs the current subscription set against the desired set and sends minimal subscribe/unsubscribe messages:'));
children.push(codeBlock('def set_symbols(self, symbols: List[str]) -> None:'));
children.push(codeBlock('    desired = set(symbols)'));
children.push(codeBlock('    to_add = desired - self._subscribed'));
children.push(codeBlock('    to_remove = self._subscribed - desired'));
children.push(codeBlock('    if to_remove:  self._send_unsubscribe(list(to_remove))'));
children.push(codeBlock('    if to_add:     self._send_subscribe(list(to_add))'));
children.push(codeBlock('    self._subscribed = desired'));
children.push(body('The channel prefix (A., T., Q.) is managed internally: P2 chart symbols auto-get T+Q channels, all others get A. channel.'));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ════════════════════════════════════════════════════════════════
// 9. DATA FLOW DIAGRAMS
// ════════════════════════════════════════════════════════════════

children.push(heading1('9. DATA FLOW — LIVE TICK PATH'));
children.push(heading2('9.1  Live Tick Flow (Watchlist / Scanner / Alerts)'));

const flowLines = [
  'Polygon WS Server',
  '    │',
  '    │  WSS Message (JSON) → {"ev":"A","sym":"AAPL","o":188,"h":190,"l":187,"c":189,"v":2400,"t":1700000000000}',
  '    ▼',
  'PolygonWebSocketWorker (QThread)',
  '    │  1. Parse JSON',
  '    │  2. Normalize to app tick dict { tradingsymbol, last_price, ohlc, volume_traded, change_percent }',
  '    │  3. Emit data_received(list[dict])   ← same signal name as old MarketDataWorker',
  '    ▼',
  'QullamaggieWindow._enqueue_market_data(ticks)  ← same slot, unchanged',
  '    │  Buffers by symbol (last-write-wins coalescing)',
  '    ▼  (every 30ms via _tick_flush_timer)',
  'QullamaggieWindow._flush_market_data_ticks()',
  '    │',
  '    ├──▶ candlestick_chart.update_live_data(tick)        ← chart candle append',
  '    ├──▶ watchlist.update_data(ticks)                    ← LTP, %chg cells',
  '    ├──▶ scanner.update_data(scanner_ticks)              ← visible rows only',
  '    ├──▶ positions_table.update_market_data(token, ltp)  ← P&L recalc',
  '    ├──▶ header_toolbar.ingest_ws_ticks(ticks)           ← ticker board',
  '    ├──▶ alert_system.update_market_data(ticks)          ← alert engine',
  '    └──▶ sl_manager.on_ticks(ticks)                      ← SL evaluation',
];
flowLines.forEach(l => children.push(codeBlock(l)));
children.push(...spacer(1));

children.push(heading2('9.2  Chart Historical Data Flow'));
const chartFlow = [
  'User types symbol in search bar / clicks scanner row',
  '    ▼',
  'CandlestickChart.on_search(symbol)',
  '    ▼',
  'Worker(PolygonChartFetcher.fetch(symbol, interval, from_date, to_date))  ← QThreadPool',
  '    ▼  (non-blocking, runs in thread pool)',
  'PolygonRESTClient.get_agg_bars(sym, mult, timespan, from, to)',
  '    ▼  GET /v2/aggs/ticker/AAPL/range/1/day/2023-01-01/2025-05-26',
  'Response JSON → data_normalizer.to_dataframe(response)',
  '    ▼',
  'MarketAwareDataCache.set(key, df, interval)  ← TTL-aware cache',
  '    ▼',
  'chart_widget._on_data_loaded(df)  ← signals chart to render',
  '    ▼',
  'CandlestickChart renders bars',
];
chartFlow.forEach(l => children.push(codeBlock(l)));
children.push(...spacer(1));

children.push(heading2('9.3  Order Placement Flow'));
const orderFlow = [
  'User clicks CONFIRM in OrderDialog',
  '    ▼',
  'QullamaggieWindow._handle_order_placement(order_data)',
  '    ▼  ORDER PLANE ONLY',
  'IBKRTradingClient.place_order(**order_data)',
  '    │  (ib_insync.placeOrder → IB Gateway → IBKR exchange)',
  '    ▼',
  'Returns order_id  (str)',
  '    ▼',
  'PositionManager.start_tracking_order(order_id, order_data)',
  '    ▼  Polls trader.orders() every 1s via QTimer',
  'On COMPLETE: chart_lines_manager.add_position_line()',
  '    ▼',
  '  NOTE: Position LTP updates come from Polygon WS (not IBKR)',
  '  NOTE: IBKR only returns final position qty + avg_price at fill',
];
orderFlow.forEach(l => children.push(codeBlock(l)));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ════════════════════════════════════════════════════════════════
// 10. STEP BY STEP IMPLEMENTATION PLAN
// ════════════════════════════════════════════════════════════════

children.push(heading1('10. STEP-BY-STEP IMPLEMENTATION PLAN'));
children.push(callout('Each phase is independently deployable. Complete Phase 1 before starting Phase 2. The app must remain functional at the end of every phase.', 'warning'));

children.push(heading2('Phase 1 — Polygon Auth & REST Foundation  (Days 1–3)'));
children.push(numbered('Install polygon-api-client:  pip install polygon-api-client requests'));
children.push(numbered('Create polygon/__init__.py'));
children.push(numbered('Create polygon/auth.py with PolygonAuth class and integration with EnhancedTokenManager.'));
children.push(numbered('Create polygon/client.py with PolygonRESTClient — implement get_snapshot() and search_tickers() first.'));
children.push(numbered('Add Polygon API key page (Page 3) to DualModeLoginManager — basic input + validate button.'));
children.push(numbered('Add connection summary page (Page 4) showing IBKR + Polygon status side by side.'));
children.push(numbered('Update broker_factory.py to create PolygonRESTClient and pass to MainWindow.'));
children.push(numbered('Acceptance test: login shows Polygon page, key validates, plan tier detected.'));

children.push(heading2('Phase 2 — Chart Data Migration  (Days 4–6)'));
children.push(numbered('Implement PolygonRESTClient.get_agg_bars() with all interval mappings.'));
children.push(numbered('Create polygon/chart_fetcher.py implementing the KiteDataFetcher interface.'));
children.push(numbered('Update _create_chart_data_fetcher() in main_window.py to return PolygonChartFetcher.'));
children.push(numbered('Update polygon/data_normalizer.py to convert Polygon agg JSON → pandas DataFrame.'));
children.push(numbered('Test all chart intervals: 1m, 5m, 15m, 30m, 60m, day, week.'));
children.push(numbered('Run 6-month daily chart for AAPL and compare candles with TradingView.'));
children.push(numbered('Acceptance test: chart loads from Polygon, all timeframes work, caching prevents duplicate calls.'));

children.push(heading2('Phase 3 — Symbol Resolver Migration  (Days 7–8)'));
children.push(numbered('Create polygon/symbol_resolver.py with PolygonSymbolResolver.'));
children.push(numbered('Implement async search() method using /v3/reference/tickers?search={q}.'));
children.push(numbered('Update main_window.py: replace _ibkr_symbol_resolver with polygon_symbol_resolver.'));
children.push(numbered('Update header_toolbar.py: set_ibkr_search_provider() → set_polygon_search_provider().'));
children.push(numbered('Test: search "APPL" shows Apple with correct exchange, search "MIC" shows MSFT variants.'));
children.push(numbered('Acceptance test: symbol search returns results in < 200 ms, scanner symbols resolve.'));

children.push(heading2('Phase 4 — WebSocket Worker Migration  (Days 9–12)'));
children.push(numbered('Create polygon/websocket_worker.py with PolygonWebSocketWorker QThread.'));
children.push(numbered('Implement connection, authentication, subscribe, unsubscribe, heartbeat, reconnect.'));
children.push(numbered('Normalize all Polygon message types (A, T, Q, AM) to the standard app tick dict format.'));
children.push(numbered('Update main_window.py: replace MarketDataWorker with PolygonWebSocketWorker.'));
children.push(numbered('Update _rebuild_subscription_universe() to call polygon_ws_worker.set_symbols().'));
children.push(numbered('Update StopLossManager and AlertEngine signal wiring.'));
children.push(numbered('Test: open watchlist with 20 symbols, verify all LTPs update in real time.'));
children.push(numbered('Test: open chart, verify live candle updates on 1-minute chart.'));
children.push(numbered('Acceptance test: MarketDataWorker file deleted, app still works perfectly.'));

children.push(heading2('Phase 5 — Instrument Loader Migration  (Days 13–14)'));
children.push(numbered('Create polygon/instrument_loader.py — loads popular US equities from Polygon /v3/reference/tickers.'));
children.push(numbered('Replace IBKRInstrumentLoader with PolygonInstrumentLoader in main_window.py.'));
children.push(numbered('Build SymbolIndex from Polygon ticker data (same structure as existing).'));
children.push(numbered('Update watchlist, scanner, and positions table to use symbol-based lookup instead of token.'));
children.push(numbered('Create polygon/snapshot_service.py for bulk startup LTP fetch.'));
children.push(numbered('Acceptance test: watchlist loads with correct LTPs on startup without waiting for WS.'));

children.push(heading2('Phase 6 — Polish & Options Layer  (Days 15–20)'));
children.push(numbered('Create polygon/options_service.py — options chain, greeks, expiration calendar.'));
children.push(numbered('Build basic options chain widget (separate QDialog accessible from order dialog).'));
children.push(numbered('Add market status indicator using Polygon /v1/marketstatus/now instead of IST calculation.'));
children.push(numbered('Add news tab to StockInfoDialog using Polygon /v2/reference/news.'));
children.push(numbered('Performance test: 100 watchlist symbols, measure CPU at rest during market hours.'));
children.push(numbered('Full regression test of entire login → trade → exit flow.'));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ════════════════════════════════════════════════════════════════
// 11. BACKWARD COMPATIBILITY CONTRACT
// ════════════════════════════════════════════════════════════════

children.push(heading1('11. BACKWARD COMPATIBILITY CONTRACT'));
children.push(body('The following interfaces must remain identical so existing UI widgets (chart, watchlist, positions, scanner) require zero changes:'));

const compatTable = mkTable([
  new TableRow({ children: [hdrCell('Interface', 3400), hdrCell('Existing Consumer', 4000), hdrCell('Guaranteed By', 4200)] }),
  new TableRow({ children: [cell('data_received = Signal(list)', 3400), cell('main_window._enqueue_market_data', 4000), cell('PolygonWebSocketWorker emits same signal name', 4200)] }),
  new TableRow({ children: [cell('Tick dict keys: tradingsymbol, last_price, ohlc, volume_traded', 3400), cell('watchlist, scanner, positions, alerts, SL', 4000), cell('data_normalizer.to_tick_dict() enforces schema', 4200)] }),
  new TableRow({ children: [cell('connection_established, connection_closed, connection_error', 3400), cell('main_window._on_websocket_connect, status bar', 4000), cell('Same signal names on PolygonWebSocketWorker', 4200)] }),
  new TableRow({ children: [cell('KiteDataFetcher interface (fetch method signature)', 3400), cell('CandlestickChart, ChartWindow', 4000), cell('PolygonChartFetcher implements same ABC', 4200)] }),
  new TableRow({ children: [cell('IBKRSymbolResolver.search(query, callback)', 3400), cell('header_toolbar, _ibkr_live_search', 4000), cell('PolygonSymbolResolver has identical search() signature', 4200)] }),
  new TableRow({ children: [cell('instrument_map dict: {symbol: {tradingsymbol, exchange, ...}}', 3400), cell('All widgets that hold _instrument_map', 4000), cell('PolygonInstrumentLoader builds identical dict structure', 4200)] }),
  new TableRow({ children: [cell('IBKRTradingClient.place_order(**kwargs)', 3400), cell('_handle_order_placement, SL manager, paper trader', 4000), cell('IBKRTradingClient unchanged — orders stay on IBKR', 4200)] }),
], [3400, 4000, 4200]);
children.push(compatTable, ...spacer(1));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ════════════════════════════════════════════════════════════════
// 12. TESTING PLAN
// ════════════════════════════════════════════════════════════════

children.push(heading1('12. TESTING & ACCEPTANCE CRITERIA'));

children.push(heading2('12.1  Unit Tests'));
children.push(bullet('PolygonRESTClient.get_agg_bars() — mock HTTP, assert DataFrame column names and dtype.'));
children.push(bullet('PolygonDataNormalizer.to_tick_dict() — assert all required keys present, types correct.'));
children.push(bullet('PolygonWebSocketWorker subscription diff — assert subscribe/unsubscribe called with correct deltas.'));
children.push(bullet('PolygonChartFetcher interval mapping — assert every app interval maps to correct Polygon params.'));
children.push(bullet('PolygonSymbolResolver cache — assert second call for same query uses cache (no HTTP call).'));

children.push(heading2('12.2  Integration Tests'));
children.push(bullet('Full login flow: IBKR connected + Polygon validated → main window opens.'));
children.push(bullet('Chart loads AAPL daily bars, 6 months. Candle count matches expected trading days.'));
children.push(bullet('Watchlist: add TSLA, NVDA, META. All three LTPs update within 2 seconds of market open.'));
children.push(bullet('Alert triggers: set AAPL alert above current price. Verify alert fires when Polygon tick crosses.'));
children.push(bullet('SL triggers: place paper buy, set SL, verify SL fires when Polygon tick crosses SL price.'));
children.push(bullet('Order placement: BUY 1 AAPL paper. IBKR paper order fills. Position appears in table.'));

children.push(heading2('12.3  Performance Benchmarks'));
const perfTable = mkTable([
  new TableRow({ children: [hdrCell('Metric', 3400), hdrCell('Target', 2800), hdrCell('Measurement Method', 5400)] }),
  new TableRow({ children: [cell('Tick-to-UI latency (WS)', 3400), cell('< 50 ms p99', 2800), cell('Log timestamp at WS recv and at Qt label.setText()', 5400)] }),
  new TableRow({ children: [cell('Chart bar load (daily, 1 year)', 3400), cell('< 800 ms', 2800), cell('Time from on_search() to chart rendered', 5400)] }),
  new TableRow({ children: [cell('Symbol search (first keystroke)', 3400), cell('< 200 ms', 2800), cell('Time from text edit to dropdown first result', 5400)] }),
  new TableRow({ children: [cell('100-symbol watchlist CPU idle', 3400), cell('< 3% CPU', 2800), cell('top/htop during 10-min market-hours session', 5400)] }),
  new TableRow({ children: [cell('WS reconnect time', 3400), cell('< 5 seconds', 2800), cell('Kill WS connection, measure time to first tick', 5400)] }),
], [3400, 2800, 5400]);
children.push(perfTable, ...spacer(1));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ════════════════════════════════════════════════════════════════
// 13. SECURITY & CONFIGURATION
// ════════════════════════════════════════════════════════════════

children.push(heading1('13. SECURITY & CONFIGURATION'));
children.push(heading2('13.1  API Key Security'));
children.push(bullet('Store Polygon API key using the existing Fernet symmetric encryption in EnhancedTokenManager. Key material never touches plaintext files.'));
children.push(bullet('Transmit key only in Authorization: Bearer {key} header over TLS. Never embed in URL query strings.'));
children.push(bullet('Mask key in UI: show only first 4 and last 4 characters after validation (e.g. "NrAb...xZ9q").'));
children.push(bullet('Rotate key: settings page allows entering a new key — invalidates old one, re-validates, re-saves.'));
children.push(bullet('Log sanitization: strip the API key from all log lines using a log filter that replaces the key value with "[POLYGON_KEY]".'));

children.push(heading2('13.2  config_manager.py Additions'));
children.push(body('Add Polygon-specific settings to the existing ConfigManager:'));
children.push(codeBlock('polygon_plan: str             # "free" | "starter" | "developer"'));
children.push(codeBlock('polygon_ws_channels: list     # ["A", "T"] — which channels to subscribe'));
children.push(codeBlock('polygon_realtime_enabled: bool # False = use delayed data (Starter plan fallback)'));
children.push(codeBlock('polygon_options_enabled: bool  # True only on Developer plan'));
children.push(codeBlock('polygon_snapshot_on_startup: bool  # Fetch batch LTPs on startup'));

children.push(heading2('13.3  Environment Variables (optional override)'));
children.push(codeBlock('POLYGON_API_KEY=xxx          # overrides encrypted storage'));
children.push(codeBlock('POLYGON_WS_URL=wss://...     # override for testing with mock server'));
children.push(codeBlock('POLYGON_PLAN=developer       # override plan detection'));
children.push(new Paragraph({ children: [new PageBreak()] }));

// ════════════════════════════════════════════════════════════════
// 14. FUTURE OPTIONS PANEL
// ════════════════════════════════════════════════════════════════

children.push(heading1('14. FUTURE CAPABILITY — OPTIONS TRADING PANEL'));
children.push(callout('Available on Polygon Developer plan ($79/mo). Implement in Phase 6 after core migration is complete and stable.', 'info'));

children.push(heading2('14.1  Options Chain Widget Design'));
children.push(body('A new QDialog opens from the order dialog when the user selects an options instrument. It displays:'));
children.push(bullet('Expiration calendar (tab row or dropdown) — loaded from /v3/reference/options/contracts?underlying_ticker={sym}&limit=250.'));
children.push(bullet('Strike matrix — rows = strike prices, columns = call/put. Cells show last price, IV, delta, theta, volume, OI.'));
children.push(bullet('Greeks heat-map — optional color overlay showing delta distribution across chain.'));
children.push(bullet('Max pain calculator — aggregates OI to find the strike where max options expire worthless.'));
children.push(bullet('Live updates via O.{contract} WebSocket channel subscription for selected expiration.'));

children.push(heading2('14.2  Options Order Routing'));
children.push(body('Options orders still route through IBKR. Polygon provides the discovery and pricing layer. IBKR provides the execution:'));
children.push(numbered('User selects call/put, strike, expiration from Polygon options chain widget.'));
children.push(numbered('App resolves the IBKR contract (Option symbol, exchange, strike, expiry) from the Polygon data.'));
children.push(numbered('Passes to OrderDialog with order_type prefilled (LMT recommended for options).'));
children.push(numbered('Places order via IBKRTradingClient.place_order() with secType="OPT".'));

// ════════════════════════════════════════════════════════════════
// APPENDIX
// ════════════════════════════════════════════════════════════════

children.push(new Paragraph({ children: [new PageBreak()] }));
children.push(heading1('APPENDIX — QUICK REFERENCE'));

children.push(heading2('A. Polygon WebSocket Message Reference'));
const wsRef = mkTable([
  new TableRow({ children: [hdrCell('ev field', 1800), hdrCell('Channel', 2000), hdrCell('Key fields', 7800)] }),
  new TableRow({ children: [cell('A', 1800), cell('A.{sym}', 2000), cell('sym, o, h, l, c, v, vw, av, op, z, a, s, e  (s=start ms, e=end ms)', 7800)] }),
  new TableRow({ children: [cell('T', 1800), cell('T.{sym}', 2000), cell('sym, p (price), s (size), t (ms), c (conditions), x (exchange id)', 7800)] }),
  new TableRow({ children: [cell('Q', 1800), cell('Q.{sym}', 2000), cell('sym, bp (bid_price), ap (ask_price), bs, as, t, x, bx (bid exchange)', 7800)] }),
  new TableRow({ children: [cell('AM', 1800), cell('AM.{sym}', 2000), cell('Same as A but fires every minute — use for 1m chart live candles', 7800)] }),
  new TableRow({ children: [cell('O', 1800), cell('O.{contract}', 2000), cell('contract_id, p, s, t, c — for live options quote updates', 7800)] }),
  new TableRow({ children: [cell('status', 1800), cell('(system)', 2000), cell('status: "connected" | "auth_success" | "auth_failed" | "error", message', 7800)] }),
], [1800, 2000, 7800]);
children.push(wsRef, ...spacer(1));

children.push(heading2('B. Dependencies to Add'));
children.push(codeBlock('# requirements.txt additions'));
children.push(codeBlock('polygon-api-client>=1.13.0   # Official Polygon Python SDK'));
children.push(codeBlock('websockets>=12.0             # For PolygonWebSocketWorker async WS'));
children.push(codeBlock('# OR: use requests + websocket-client if preferring sync style'));
children.push(codeBlock('# polygon-api-client bundles its own WS — use that for simplicity'));

children.push(heading2('C. Files to DELETE After Migration'));
children.push(codeBlock('ibkr/core/market_data_worker.py          # Replaced by polygon/websocket_worker.py'));
children.push(codeBlock('ibkr/core/linux_ibkr_deep_fix.py         # Dev script, not needed in prod'));
children.push(codeBlock('ibkr/utils/ibkr_instrument_loader.py     # Replaced by polygon/instrument_loader.py'));
children.push(codeBlock('ibkr/utils/ibkr_symbol_resolver.py       # Replaced by polygon/symbol_resolver.py'));
children.push(codeBlock('chart_engine/core/ibkr_data_fetcher.py   # Replaced by polygon/chart_fetcher.py'));

children.push(heading2('D. Environment Setup Checklist'));
children.push(bullet('Create Polygon.io account at polygon.io/dashboard'));
children.push(bullet('Choose Starter or Developer plan based on real-time vs delayed data need.'));
children.push(bullet('Copy API key from Dashboard → API Keys.'));
children.push(bullet('Test key: curl "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/AAPL?apiKey=YOUR_KEY"'));
children.push(bullet('Verify TWS / IB Gateway is running with API enabled on port 7497 (paper) or 7496 (live).'));
children.push(bullet('pip install polygon-api-client websockets'));
children.push(bullet('Run test: python -c "from polygon import RESTClient; c=RESTClient(\'YOUR_KEY\'); print(c.get_ticker_details(\'AAPL\').name)"'));

// ────────────────────────────────────────────────────────────────────────────
// BUILD DOCUMENT
// ────────────────────────────────────────────────────────────────────────────

const doc = new Document({
  numbering: {
    config: [
      { reference: 'bullets', levels: [{ level: 0, format: LevelFormat.BULLET, text: '•', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
      { reference: 'numbers', levels: [
        { level: 0, format: LevelFormat.DECIMAL, text: '%1.', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
        { level: 1, format: LevelFormat.LOWER_LETTER, text: '%2.', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 1080, hanging: 360 } } } },
      ]},
    ]
  },
  styles: {
    default: { document: { run: { font: 'Calibri', size: 22, color: COLORS.textDark } } },
    paragraphStyles: [
      { id: 'Heading1', name: 'Heading 1', basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { size: 32, bold: true, font: 'Calibri', color: COLORS.white },
        paragraph: { spacing: { before: 360, after: 240 }, outlineLevel: 0 } },
      { id: 'Heading2', name: 'Heading 2', basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { size: 26, bold: true, font: 'Calibri', color: COLORS.darkBlue },
        paragraph: { spacing: { before: 280, after: 160 }, outlineLevel: 1 } },
      { id: 'Heading3', name: 'Heading 3', basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { size: 22, bold: true, font: 'Calibri', color: COLORS.textDark },
        paragraph: { spacing: { before: 200, after: 80 }, outlineLevel: 2 } },
    ]
  },
  sections: [{
    properties: {
      page: {
        size: { width: PAGE_W, height: 15840 },
        margin: { top: MARGIN, right: MARGIN, bottom: MARGIN, left: MARGIN }
      }
    },
    headers: {
      default: new Header({
        children: [
          para([
            run('QULLAMAGGIE TERMINAL  ·  ', { bold: true, size: 16, color: COLORS.primary }),
            run('Polygon.io + IBKR Hybrid Architecture Blueprint', { size: 16, color: COLORS.textMid }),
          ], { align: AlignmentType.RIGHT, after: 0,
               border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: COLORS.border } } })
        ]
      })
    },
    footers: {
      default: new Footer({
        children: [para([
          run('CONFIDENTIAL — Internal Development Document  ', { size: 16, color: COLORS.textMid }),
          run('  Page ', { size: 16, color: COLORS.textMid }),
          new PageNumber(),
        ], { before: 0, align: AlignmentType.CENTER,
             border: { top: { style: BorderStyle.SINGLE, size: 4, color: COLORS.border } } })]
      })
    },
    children,
  }]
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync('/mnt/user-data/outputs/Qullamaggie_Polygon_IBKR_Architecture.docx', buf);
  console.log('Done');
});