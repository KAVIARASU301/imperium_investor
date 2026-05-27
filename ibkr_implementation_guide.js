const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  HeadingLevel, AlignmentType, BorderStyle, WidthType, ShadingType,
  PageOrientation, LevelFormat, PageBreak
} = require('docx');
const fs = require('fs');

// ── Palette matching AMOLED terminal branding ──
const ACCENT_TEAL  = "00D4A8";
const ACCENT_RED   = "FF4D6A";
const ACCENT_AMBER = "F59E0B";
const ACCENT_CYAN  = "00D4FF";
const DARK_BG      = "050709";
const MID_BG       = "0A0D12";
const BORDER_COL   = "1A2030";
const H1_COLOR     = "00D4A8";
const H2_COLOR     = "00D4FF";
const H3_COLOR     = "F59E0B";
const BODY_COLOR   = "1a1a2e";
const DIM_COLOR    = "5A7090";
const CODE_BG      = "F4F6F8";
const WARN_BG      = "FFF8E1";
const WARN_BORDER  = "F59E0B";
const TIP_BG       = "E8F5E9";
const TIP_BORDER   = "00D4A8";
const DANGER_BG    = "FFEBEE";
const DANGER_BORDER= "FF4D6A";

const contentWidth = 9360; // US Letter 1" margins
const col1 = 2400;
const col2 = 6960;

// ── Helper builders ──

function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 360, after: 180 },
    children: [new TextRun({ text, color: H1_COLOR, bold: true, size: 34, font: "Arial" })]
  });
}

function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 300, after: 140 },
    children: [new TextRun({ text, color: H2_COLOR, bold: true, size: 28, font: "Arial" })]
  });
}

function h3(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_3,
    spacing: { before: 220, after: 100 },
    children: [new TextRun({ text, color: H3_COLOR, bold: true, size: 24, font: "Arial" })]
  });
}

function body(text, opts = {}) {
  return new Paragraph({
    spacing: { before: 60, after: 100 },
    children: [new TextRun({ text, size: 22, font: "Arial", color: opts.color || "1a1a2e", bold: opts.bold || false, italics: opts.italic || false })]
  });
}

function bullet(text, level = 0) {
  return new Paragraph({
    numbering: { reference: "bullets", level },
    spacing: { before: 40, after: 40 },
    children: [new TextRun({ text, size: 22, font: "Arial", color: "1a1a2e" })]
  });
}

function numbered(text, level = 0) {
  return new Paragraph({
    numbering: { reference: "numbers", level },
    spacing: { before: 40, after: 40 },
    children: [new TextRun({ text, size: 22, font: "Arial", color: "1a1a2e" })]
  });
}

function code(text) {
  return new Paragraph({
    spacing: { before: 40, after: 40 },
    shading: { fill: CODE_BG, type: ShadingType.CLEAR },
    indent: { left: 360 },
    children: [new TextRun({ text, size: 19, font: "Courier New", color: "2d2d2d" })]
  });
}

function codeBlock(lines) {
  return lines.map(l => code(l));
}

function callout(label, text, bg, borderColor) {
  const border = { style: BorderStyle.SINGLE, size: 12, color: borderColor };
  return new Table({
    width: { size: contentWidth, type: WidthType.DXA },
    columnWidths: [180, contentWidth - 180],
    rows: [
      new TableRow({
        children: [
          new TableCell({
            shading: { fill: borderColor, type: ShadingType.CLEAR },
            width: { size: 180, type: WidthType.DXA },
            margins: { top: 80, bottom: 80, left: 80, right: 80 },
            children: [new Paragraph({ children: [new TextRun({ text: label, size: 19, font: "Arial", color: "FFFFFF", bold: true })] })]
          }),
          new TableCell({
            shading: { fill: bg, type: ShadingType.CLEAR },
            width: { size: contentWidth - 180, type: WidthType.DXA },
            margins: { top: 80, bottom: 80, left: 140, right: 120 },
            borders: { top: border, bottom: border, right: border, left: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" } },
            children: [new Paragraph({ children: [new TextRun({ text, size: 21, font: "Arial", color: "1a1a2e" })] })]
          })
        ]
      })
    ]
  });
}

function warn(text)   { return callout("⚠ WARN",    text, WARN_BG,   WARN_BORDER); }
function tip(text)    { return callout("✓ TIP",     text, TIP_BG,    TIP_BORDER); }
function danger(text) { return callout("✕ DON'T",   text, DANGER_BG, DANGER_BORDER); }
function note(text)   { return callout("ℹ NOTE",    text, "E3F2FD",  "1565C0"); }

function twoColRow(key, val, shaded = false) {
  const fill = shaded ? "EEF2F7" : "FFFFFF";
  const cellBorder = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
  const borders = { top: cellBorder, bottom: cellBorder, left: cellBorder, right: cellBorder };
  return new TableRow({
    children: [
      new TableCell({
        borders, shading: { fill, type: ShadingType.CLEAR },
        width: { size: col1, type: WidthType.DXA },
        margins: { top: 80, bottom: 80, left: 120, right: 120 },
        children: [new Paragraph({ children: [new TextRun({ text: key, size: 21, font: "Arial", color: "1a1a2e", bold: true })] })]
      }),
      new TableCell({
        borders, shading: { fill, type: ShadingType.CLEAR },
        width: { size: col2, type: WidthType.DXA },
        margins: { top: 80, bottom: 80, left: 120, right: 120 },
        children: [new Paragraph({ children: [new TextRun({ text: val, size: 21, font: "Arial", color: "1a1a2e" })] })]
      })
    ]
  });
}

function twoColHeader(k, v) {
  const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
  const borders = { top: border, bottom: border, left: border, right: border };
  return new TableRow({
    children: [
      new TableCell({
        borders, shading: { fill: "1A2030", type: ShadingType.CLEAR },
        width: { size: col1, type: WidthType.DXA },
        margins: { top: 80, bottom: 80, left: 120, right: 120 },
        children: [new Paragraph({ children: [new TextRun({ text: k, size: 21, font: "Arial", color: "FFFFFF", bold: true })] })]
      }),
      new TableCell({
        borders, shading: { fill: "1A2030", type: ShadingType.CLEAR },
        width: { size: col2, type: WidthType.DXA },
        margins: { top: 80, bottom: 80, left: 120, right: 120 },
        children: [new Paragraph({ children: [new TextRun({ text: v, size: 21, font: "Arial", color: "FFFFFF", bold: true })] })]
      })
    ]
  });
}

function table2col(rows) {
  return new Table({
    width: { size: contentWidth, type: WidthType.DXA },
    columnWidths: [col1, col2],
    rows: [twoColHeader(rows[0][0], rows[0][1]), ...rows.slice(1).map(([k, v], i) => twoColRow(k, v, i % 2 === 0))]
  });
}

function pageBreak() {
  return new Paragraph({ children: [new PageBreak()] });
}

function spacer() {
  return new Paragraph({ spacing: { before: 60, after: 60 }, children: [new TextRun("")] });
}

function sectionDivider() {
  return new Paragraph({
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "1A2030", space: 1 } },
    spacing: { before: 200, after: 200 },
    children: [new TextRun("")]
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// DOCUMENT CONTENT
// ─────────────────────────────────────────────────────────────────────────────

const children = [

  // ══════════════════════════════════════════
  //  COVER
  // ══════════════════════════════════════════
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 1440, after: 240 },
    children: [new TextRun({ text: "QULLAMAGGIE", size: 56, bold: true, font: "Arial", color: H1_COLOR })]
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 80, after: 80 },
    children: [new TextRun({ text: "Interactive Brokers (IBKR) Mode", size: 36, font: "Arial", color: H2_COLOR, bold: true })]
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 80, after: 80 },
    children: [new TextRun({ text: "Institutional Swing Trading Terminal — USA Market", size: 28, font: "Arial", color: H3_COLOR })]
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 40, after: 40 },
    children: [new TextRun({ text: "Complete AI-Agent Implementation Guide", size: 24, font: "Arial", color: DIM_COLOR, italics: true })]
  }),
  spacer(), spacer(),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 40, after: 40 },
    children: [new TextRun({ text: "Audience: Any AI coding assistant continuing this project", size: 22, font: "Arial", color: DIM_COLOR })]
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 20, after: 20 },
    children: [new TextRun({ text: "Version: 1.0  |  Stack: Python · PySide6 · ib_insync · chart_engine", size: 22, font: "Arial", color: DIM_COLOR })]
  }),
  pageBreak(),

  // ══════════════════════════════════════════
  //  SECTION 1 — OVERVIEW & OBJECTIVE
  // ══════════════════════════════════════════
  h1("1. Overview & Objective"),
  body("Qullamaggie is a dual-broker professional swing trading terminal. The India (Kite/Zerodha) mode is production-complete and serves as the design and architecture reference. This document governs the complete build of the USA (IBKR) mode so that any AI agent picking up this project can understand intent, constraints, patterns, and required deliverables without needing to re-read the India codebase."),
  spacer(),
  h2("1.1 What We Are Building"),
  body("A TC2000 / Bloomberg-style dark institutional swing trading terminal for US equities that:"),
  bullet("Connects to Interactive Brokers (TWS or IB Gateway) via the ib_insync Python library"),
  bullet("Presents an AMOLED dark terminal UI identical in feel to the Kite mode (same fonts, colors, widget layout, splitter geometry)"),
  bullet("Supports live streaming Level I market data via IBKR real-time subscriptions"),
  bullet("Supports market and limit order placement, modification, and cancellation"),
  bullet("Shows open positions, unrealized P&L, daily realized P&L, and account margin"),
  bullet("Provides a TC2000-style Finviz scanner for US equities as the left-panel scanner"),
  bullet("Runs the same candlestick chart engine used in Kite mode (chart_engine package)"),
  bullet("Offers paper trading mode using the same BasePaperTrader base class"),
  bullet("Shares all non-broker-specific utilities: color system, sound manager, toast notifications, keyboard shortcuts, alert system, PnL calculator"),
  spacer(),
  h2("1.2 What We Are NOT Building"),
  bullet("Options chain or futures trading (equities only for swing trading)"),
  bullet("A new chart engine — reuse chart_engine exactly as used in Kite mode"),
  bullet("New UI design language — mirror Kite's AMOLED palette, fonts, spacing token-for-token"),
  bullet("Any Python asyncio main loop — ib_insync runs in a QThread with its own asyncio loop (already implemented in ibkr_auth.py)"),
  spacer(),
  h2("1.3 Design Mandate: Mirror Kite UI Exactly"),
  body("Every visible panel, palette value, font choice, and layout decision in the Kite main window (kite/core/main_window.py) is the specification for the IBKR main window. Do not invent new UI. Reference the following in every prompt:"),
  bullet("Color tokens: _BG0 through _BG4, _BULL, _BEAR, _AMBER, _CYAN (defined in scanner_table.py and watchlist_table.py)"),
  bullet("Font stack: 'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', sans-serif"),
  bullet("Toolbar height: 32px  |  Control height: 24px  |  Row height: 21px"),
  bullet("Main splitter: Scanner (left) | Primary Chart | Secondary Chart | Watchlist+Positions (right)"),
  bullet("Right panel inner splitter: Watchlist (top) | Positions (bottom)"),
  bullet("Bottom StatusBar: 24px height, market/API indicators left, P&L metrics right"),
  sectionDivider(),
  pageBreak(),

  // ══════════════════════════════════════════
  //  SECTION 2 — ARCHITECTURE
  // ══════════════════════════════════════════
  h1("2. Architecture & File Structure"),
  h2("2.1 Existing Foundation (DO NOT MODIFY)"),
  body("The following files are already written and must be treated as immutable scaffolding:"),
  spacer(),
  table2col([
    ["File", "Purpose"],
    ["ibkr/__init__.py", "Package declaration"],
    ["ibkr/core/__init__.py", "Core subpackage"],
    ["ibkr/core/trading_client.py", "IBKRTradingClient — place/cancel orders via ib_insync"],
    ["ibkr/core/contract_manager.py", "ContractManager — resolves Stock contracts, caches qualified contracts"],
    ["ibkr/core/market_data_worker.py", "IBKRMarketDataWorker — subscribes to reqMktData, emits tick_received signal"],
    ["ibkr/core/order_router.py", "IBKROrderRouter — Qt signal-friendly order submission"],
    ["ibkr/core/position_manager.py", "IBKRPositionManager — snapshot positions from ib.positions()"],
    ["ibkr/core/api_circuit_breaker.py", "Async rate limiter for IBKR pacing-sensitive endpoints"],
    ["ibkr/utils/__init__.py", "Utils subpackage"],
    ["ibkr/utils/data_converter.py", "normalize_position(), normalize_ticker() — converts ib_insync models to dicts"],
    ["ibkr/scanner/run_finviz_scan.py", "Finviz web scraper (same as kite/scanner/run_finviz_scan.py — already done)"],
    ["ibkr/core/main_window.py", "Stub QullamaggieWindow — replace with full implementation per this guide"],
    ["login_setup/ibkr_auth.py", "IBKRAuth + IBKRConnectionWorker — QThread async connect, IPv4/IPv6 probe"],
    ["login_setup/broker_factory.py", "BrokerFactory — creates IBKRClientWrapper, IBKROrderRouter, etc."],
    ["login_setup/token_manager.py", "EnhancedTokenManager — encrypted credential storage (IBKR session branch)"],
  ]),
  spacer(),
  h2("2.2 Files To Build"),
  body("These files need to be created (or the stub replaced with a full implementation):"),
  spacer(),
  table2col([
    ["File to create / replace", "Description"],
    ["ibkr/core/main_window.py", "Full QullamaggieWindow mirroring kite/core/main_window.py"],
    ["ibkr/core/position_manager_qt.py", "Qt-signal position manager (adapts IBKRPositionManager into Qt signals)"],
    ["ibkr/core/market_data_worker_qt.py", "Tick aggregation + flush timer, mirrors kite/core/market_data_worker.py"],
    ["ibkr/core/account_manager.py", "AccountManager for IBKR (mirrors kite/core/account_manager.py)"],
    ["ibkr/core/trade_logger.py", "TradeLogger wired to ibkr broker/mode scope (reuse kite/core/trade_logger.py pattern)"],
    ["ibkr/core/shutdown_manager.py", "Graceful shutdown steps adapted for IBKR (reuse CleanShutdownMixin)"],
    ["ibkr/widgets/watchlist_table.py", "Copy kite/widgets/watchlist_table.py — adjust symbol lookup for US equities"],
    ["ibkr/widgets/scanner_table.py", "Finviz-backed scanner table (copy kite/widgets/scanner_table.py structure, use run_finviz_scan)"],
    ["ibkr/widgets/positions_table.py", "Copy kite/widgets/positions_table.py — adjust for USD currency, IBKR fields"],
    ["ibkr/widgets/header_toolbar.py", "Copy kite/widgets/header_toolbar.py — wire to IBKR search/buy/sell"],
    ["ibkr/widgets/order_dialog.py", "Copy kite/widgets/order_dialog.py — wire to IBKRTradingClient"],
    ["ibkr/widgets/status_bar.py", "Copy kite/widgets/status_bar.py — wire IBKR connection indicator"],
    ["ibkr/widgets/notifications.py", "Symlink / copy kite/widgets/notifications.py (identical)"],
    ["ibkr/utils/pnl_calculator.py", "Reuse kite/utils/pnl_calculator.py directly (no broker-specific logic)"],
    ["ibkr/utils/paper_trading_manager.py", "IBKRPaperTrader(BasePaperTrader) — broker=ibkr, USD currency"],
    ["ibkr/utils/constants.py", "US-market constants: USD tick, SMART exchange, NYSE/NASDAQ hours EST"],
    ["ibkr/core/instrument_loader.py", "On-demand contract search via ib.reqMatchingSymbols() instead of bulk download"],
  ]),
  spacer(),
  h2("2.3 Shared Utilities — Reuse Directly"),
  body("Do NOT copy-modify these. Import them from kite package directly:"),
  bullet("kite.utils.pnl_calculator — PnLCalculator, ClosedTrade, PerformanceMetrics"),
  bullet("kite.utils.base_paper_trader — BasePaperTrader (subclass for IBKR paper mode)"),
  bullet("kite.utils.worker — Worker(QRunnable) generic background thread"),
  bullet("kite.utils.sounds — SoundManager, play_alert(), play_entry_exit(), etc."),
  bullet("kite.utils.color_system — ColorThemeManager, DEFAULT_COLOR_THEME"),
  bullet("kite.core.alert_management_system — AlertSystemManager (fully broker-agnostic)"),
  bullet("kite.core.data_cache — MarketAwareDataCache (IST-aware; for IBKR override timezone to America/New_York)"),
  bullet("kite.core.chart_lines_manager — ChartLinesManager (no broker references)"),
  bullet("kite.core.shutdown_manager — CleanShutdownMixin"),
  bullet("kite.widgets.notifications — ToastNotification"),
  bullet("kite.widgets.order_dialog — OrderDialog (adapt transaction side labels only)"),
  bullet("kite.widgets.performance_dialog — PerformanceDialog"),
  bullet("kite.widgets.order_history_dialog — OrderHistoryDialog"),
  sectionDivider(),
  pageBreak(),

  // ══════════════════════════════════════════
  //  SECTION 3 — IBKR API PRIMER
  // ══════════════════════════════════════════
  h1("3. IBKR API Primer for American Developers"),
  body("Interactive Brokers exposes two API surfaces. This project uses ib_insync, the community-standard Python wrapper around the native TWS API."),
  spacer(),
  h2("3.1 Two Connection Targets"),
  table2col([
    ["Target", "Details"],
    ["TWS (Trader Workstation)", "Full desktop app. Port 7496 (live), 7497 (paper). Requires active login session. Closes when app closes."],
    ["IB Gateway", "Headless server app. Same ports. Stays running 24/7. Preferred for automated/swing-trading use."],
    ["Paper account port", "7497 for both TWS and IB Gateway in paper mode. Already handled in ibkr_auth.py."],
  ]),
  spacer(),
  h2("3.2 ib_insync Key Concepts"),
  bullet("IB() object — the central client. Connect with ib.connect() or ib.connectAsync() (use connectAsync inside QThread)."),
  bullet("Everything is event-driven. Subscribe to ib.pendingTickersEvent, ib.orderStatusEvent, ib.positionEvent, ib.errorEvent."),
  bullet("Contracts first — always call ib.qualifyContracts(contract) before using a contract in any API call. ContractManager.resolve_stock() handles this."),
  bullet("Pacing limits — IBKR enforces 50 requests/sec for market data, 60 historical data requests/10 minutes. Violating these causes 'pacing violation' error code 162. The ApiCircuitBreaker handles this."),
  bullet("reqMktData vs reqRealTimeBars — use reqMktData for Level I (bid/ask/last/volume) ticks. reqRealTimeBars gives 5-second OHLCV bars (only available for some data subscriptions)."),
  bullet("Managed accounts — ib.managedAccounts() returns a list. For paper account it typically shows 'DU' prefixed account IDs."),
  bullet("reqHistoricalData — used by the chart engine. Returns OHLCV bars. Parameters: contract, endDateTime, durationStr, barSizeSetting, whatToShow, useRTH."),
  bullet("placeOrder(contract, order) returns a Trade object immediately. Monitor Trade.orderStatus.status for fills."),
  spacer(),
  h2("3.3 Market Data Subscription Types"),
  table2col([
    ["IBKR data type", "Use in this app"],
    ["Snapshot (reqMktData, snapshot=True)", "One-time LTP fetch for order dialog — use before placing limit orders"],
    ["Streaming (reqMktData, snapshot=False)", "Live ticks for watchlist, scanner, chart LTP, positions — main path"],
    ["reqRealTimeBars", "Not used — chart engine uses reqHistoricalData for bar loading"],
    ["reqTickByTick", "Not used in V1 — consider for ultra-low-latency bid/ask later"],
  ]),
  spacer(),
  warn("IBKR requires a market data subscription for each security type. Without 'US Securities Snapshot and Futures Value Bundle' (or similar), reqMktData returns delayed data (15 min). Always verify data subscription status in Account Management on IBKR website."),
  spacer(),
  h2("3.4 Order Types Mapping"),
  table2col([
    ["Kite order type", "IBKR equivalent", ],
    ["MARKET", "MarketOrder(action, qty)"],
    ["LIMIT", "LimitOrder(action, qty, lmtPrice)"],
    ["SL-M (stop market)", "StopOrder(action, qty, stopPrice)"],
    ["SL (stop limit)", "StopLimitOrder(action, qty, lmtPrice, stopPrice)"],
    ["TRAIL", "Order(orderType='TRAIL', action, totalQuantity, trailingPercent or auxPrice)"],
  ]),
  spacer(),
  body("IBKR order actions are 'BUY' and 'SELL'. Use SMART exchange for all US equity orders — IBKR smart routing finds the best venue."),
  spacer(),
  h2("3.5 Historical Data for Charts"),
  body("The chart engine calls IBKRDataFetcher (ibkr/core/ibkr_data_fetcher.py — to be created). Map chart intervals to IBKR barSizeSetting:"),
  spacer(),
  table2col([
    ["Chart interval (Kite-style)", "IBKR barSizeSetting", ],
    ["minute", "1 min"],
    ["3minute", "3 mins"],
    ["5minute", "5 mins"],
    ["15minute", "15 mins"],
    ["30minute", "30 mins"],
    ["60minute", "1 hour"],
    ["day", "1 day"],
    ["week", "1 week"],
  ]),
  spacer(),
  body("IBKR durationStr examples: '1 D', '5 D', '1 W', '1 M', '1 Y'. For intraday bars use '1 D' max. For daily use '1 Y' or '2 Y'. whatToShow = 'TRADES' for equities."),
  spacer(),
  warn("reqHistoricalData pacing: max 60 requests per 10 minutes. Add a 1-second delay between chart loads when switching symbols rapidly. Cache all historical data using kite.core.data_cache.MarketAwareDataCache — override _today_ist() to use EST/EDT timezone."),
  sectionDivider(),
  pageBreak(),

  // ══════════════════════════════════════════
  //  SECTION 4 — IBKR DATA FETCHER
  // ══════════════════════════════════════════
  h1("4. IBKRDataFetcher — Connecting Chart Engine to IBKR"),
  body("The chart_engine package accepts a data fetcher object. For Kite mode this is KiteDataFetcher. For IBKR mode create ibkr/core/ibkr_data_fetcher.py. This is the most critical integration point."),
  spacer(),
  h2("4.1 Required Interface"),
  body("The chart engine calls fetch_ohlcv(symbol, instrument_token, interval, from_date, to_date). For IBKR, ignore instrument_token (use symbol string to qualify contract)."),
  spacer(),
  ...codeBlock([
    "# ibkr/core/ibkr_data_fetcher.py",
    "import asyncio, logging",
    "from datetime import datetime, timedelta",
    "from typing import List, Dict, Any",
    "from ib_insync import IB, Stock, util",
    "",
    "logger = logging.getLogger(__name__)",
    "",
    "class IBKRDataFetcher:",
    "    def __init__(self, ib: IB):",
    "        self.ib = ib",
    "        self._contract_cache: Dict[str, Any] = {}",
    "",
    "    def fetch_ohlcv(self, symbol: str, instrument_token: int,",
    "                    interval: str, from_date, to_date) -> List[Dict]:",
    "        \"\"\"Synchronous wrapper — chart engine calls this in a QThread.\"\"\"",
    "        contract = self._resolve_contract(symbol)",
    "        if contract is None:",
    "            return []",
    "        bar_size = self._map_interval(interval)",
    "        duration = self._calc_duration(from_date, to_date, interval)",
    "        end_dt = to_date.strftime('%Y%m%d %H:%M:%S') + ' US/Eastern'",
    "        try:",
    "            bars = self.ib.reqHistoricalData(",
    "                contract, endDateTime=end_dt, durationStr=duration,",
    "                barSizeSetting=bar_size, whatToShow='TRADES',",
    "                useRTH=True, formatDate=1",
    "            )",
    "        except Exception as e:",
    "            logger.error(f'reqHistoricalData failed for {symbol}: {e}')",
    "            return []",
    "        return [{'date': b.date, 'open': b.open, 'high': b.high,",
    "                 'low': b.low, 'close': b.close, 'volume': b.volume}",
    "                for b in bars]",
    "",
    "    def _resolve_contract(self, symbol: str):",
    "        if symbol not in self._contract_cache:",
    "            contract = Stock(symbol, 'SMART', 'USD')",
    "            qualified = self.ib.qualifyContracts(contract)",
    "            self._contract_cache[symbol] = qualified[0] if qualified else None",
    "        return self._contract_cache.get(symbol)",
    "",
    "    @staticmethod",
    "    def _map_interval(interval: str) -> str:",
    "        mapping = {'minute':'1 min','3minute':'3 mins','5minute':'5 mins',",
    "                   '15minute':'15 mins','30minute':'30 mins',",
    "                   '60minute':'1 hour','day':'1 day','week':'1 week'}",
    "        return mapping.get(interval, '1 day')",
    "",
    "    @staticmethod",
    "    def _calc_duration(from_date, to_date, interval: str) -> str:",
    "        days = (to_date - from_date).days + 1",
    "        if interval in ('day', 'week'):  return f'{min(days, 730)} D'",
    "        return f'{min(days, 5)} D'",
  ]),
  spacer(),
  tip("Always run fetch_ohlcv() inside a Worker(QRunnable) thread (kite.utils.worker.Worker). Never call ib.reqHistoricalData() from the Qt main thread — it blocks."),
  sectionDivider(),
  pageBreak(),

  // ══════════════════════════════════════════
  //  SECTION 5 — MARKET DATA WORKER
  // ══════════════════════════════════════════
  h1("5. Real-Time Market Data Architecture"),
  body("Market data flows from IBKR through ib_insync events into Qt signals, then into UI widgets. The pattern is identical to Kite mode but uses ib_insync's ticker events instead of KiteTicker."),
  spacer(),
  h2("5.1 Subscription Strategy"),
  body("IBKR charges per concurrent market data subscription (unless using the Lite plan). For swing trading:"),
  bullet("Subscribe only to visible watchlist symbols + chart symbol + open position symbols (identical to Kite mode logic in _rebuild_subscription_universe())"),
  bullet("Unsubscribe when symbol leaves watchlist or chart changes — call ib.cancelMktData(contract)"),
  bullet("Use a 225ms flush timer (identical to Kite mode) — accumulate ticks in a buffer dict keyed by conId, flush to UI on timer"),
  spacer(),
  h2("5.2 IBKRMarketDataWorkerQt (to build)"),
  body("Create ibkr/core/market_data_worker_qt.py — a QObject that wraps the existing IBKRMarketDataWorker:"),
  ...codeBlock([
    "# ibkr/core/market_data_worker_qt.py",
    "from PySide6.QtCore import QObject, Signal, QTimer",
    "from ib_insync import IB, Stock, Ticker",
    "from typing import Dict, List, Any",
    "",
    "class IBKRMarketDataWorkerQt(QObject):",
    "    data_received = Signal(list)   # list of tick dicts (same shape as Kite ticks)",
    "    connection_established = Signal()",
    "    connection_closed = Signal()",
    "",
    "    def __init__(self, ib: IB):",
    "        super().__init__()",
    "        self.ib = ib",
    "        self._tickers: Dict[int, Ticker] = {}  # conId -> Ticker",
    "        self._contracts: Dict[str, Any] = {}    # symbol -> qualified contract",
    "        self._tick_buffer: Dict[int, dict] = {} # conId -> latest tick",
    "        self._flush_timer = QTimer(self)",
    "        self._flush_timer.timeout.connect(self._flush)",
    "        self._flush_timer.start(225)",
    "        self.ib.pendingTickersEvent += self._on_pending_tickers",
    "        self.is_running = True",
    "",
    "    def subscribe(self, symbol: str) -> None:",
    "        if symbol in self._contracts: return",
    "        contract = Stock(symbol, 'SMART', 'USD')",
    "        qualified = self.ib.qualifyContracts(contract)",
    "        if not qualified: return",
    "        q = qualified[0]",
    "        self._contracts[symbol] = q",
    "        ticker = self.ib.reqMktData(q, '', False, False)",
    "        self._tickers[q.conId] = ticker",
    "",
    "    def unsubscribe(self, symbol: str) -> None:",
    "        contract = self._contracts.pop(symbol, None)",
    "        if contract:",
    "            self.ib.cancelMktData(contract)",
    "            self._tickers.pop(contract.conId, None)",
    "            self._tick_buffer.pop(contract.conId, None)",
    "",
    "    def _on_pending_tickers(self, tickers) -> None:",
    "        for ticker in tickers:",
    "            cid = getattr(ticker.contract, 'conId', None)",
    "            if cid is None: continue",
    "            sym = getattr(ticker.contract, 'symbol', '')",
    "            self._tick_buffer[cid] = {",
    "                'instrument_token': cid,",
    "                'tradingsymbol': sym,",
    "                'last_price': ticker.last or ticker.close or 0.0,",
    "                'volume': ticker.volume or 0,",
    "                'ohlc': {'open': ticker.open, 'high': ticker.high,",
    "                         'low': ticker.low, 'close': ticker.close},",
    "                'bid': ticker.bid, 'ask': ticker.ask,",
    "            }",
    "",
    "    def _flush(self) -> None:",
    "        if self._tick_buffer:",
    "            self.data_received.emit(list(self._tick_buffer.values()))",
    "            self._tick_buffer.clear()",
  ]),
  spacer(),
  danger("Never store or share the IB() object across threads. All ib calls must happen in the QThread that owns the event loop (the thread that called ib.connectAsync()). Accessing IB from the main Qt thread causes race conditions and random disconnects."),
  spacer(),
  h2("5.3 Tick Dict Shape Contract"),
  body("All UI widgets (watchlist, scanner, positions, chart, header ticker) read ticks from a standard dict shape. Keep it identical to Kite mode so widgets require zero modification:"),
  ...codeBlock([
    "{",
    "  'instrument_token': int,        # IBKR conId — same role as Kite instrument_token",
    "  'tradingsymbol': str,           # e.g. 'AAPL'",
    "  'last_price': float,            # last traded price",
    "  'volume': int,                  # cumulative volume today",
    "  'ohlc': {                       # day OHLC from ticker.open/high/low/close",
    "    'open': float, 'high': float,",
    "    'low': float,  'close': float  # close = previous close (for %chg calc)",
    "  },",
    "  'bid': float,  'ask': float",
    "}",
  ]),
  sectionDivider(),
  pageBreak(),

  // ══════════════════════════════════════════
  //  SECTION 6 — MAIN WINDOW
  // ══════════════════════════════════════════
  h1("6. Main Window Implementation (ibkr/core/main_window.py)"),
  body("Replace the stub in ibkr/core/main_window.py with a full implementation. Copy kite/core/main_window.py as the starting point and apply the following substitutions systematically:"),
  spacer(),
  h2("6.1 Constructor Substitutions"),
  table2col([
    ["Kite (source)", "IBKR (target)"],
    ["KiteConnect, PaperTradingManager types", "IBKRClientWrapper, IBKRPaperTrader types"],
    ["self.api_key, self.access_token", "self.client_id, self.connection_details"],
    ["KiteDataFetcher(self.real_kite_client)", "IBKRDataFetcher(self.ib_client)"],
    ["MarketDataWorker(api_key, access_token)", "IBKRMarketDataWorkerQt(self.ib_client)"],
    ["InstrumentLoader(kite_client)", "IBKRInstrumentLoader(ib_client) — see Section 8"],
    ["TradeLogger(broker='kite', mode=...)", "TradeLogger(broker='ibkr', mode=...)"],
    ["TradingMode label '₹'", "TradingMode label '$'"],
    ["'asia/kolkata' timezone", "'america/new_york' timezone"],
    ["'chart_drawings_kite'", "'chart_drawings_ibkr'"],
  ]),
  spacer(),
  h2("6.2 Signal Connections — Keep Identical"),
  body("All internal signal wiring (position_manager → positions_table, market_data_worker → _enqueue_market_data, alert_system, chart signals) must be identical to Kite mode. Do not redesign the signal graph."),
  spacer(),
  h2("6.3 Market Hours — US Eastern"),
  body("Replace the Indian market status logic with NYSE/NASDAQ hours in EST/EDT:"),
  ...codeBlock([
    "# ibkr/core/main_window.py — _refresh_market_status()",
    "from datetime import datetime, timezone, timedelta",
    "",
    "def _refresh_market_status(self) -> None:",
    "    EST = timezone(timedelta(hours=-5))",
    "    now_est = datetime.now(EST)",
    "    weekday = now_est.weekday()  # 0=Mon … 6=Sun",
    "    t = (now_est.hour, now_est.minute)",
    "    is_open = weekday < 5 and (9, 30) <= t <= (16, 0)",
    "    status.set_market_indicator('OPEN' if is_open else 'CLOSED')",
  ]),
  spacer(),
  h2("6.4 Shutdown Steps"),
  body("Adapt ShutdownManager steps: replace MarketDataWorker.stop() with IBKRMarketDataWorkerQt stop + ib.disconnect(). All other steps (alert_system, chart, trade_logger) are identical."),
  spacer(),
  h2("6.5 Keyboard Shortcuts — Keep Identical"),
  body("All keyboard shortcuts (B=buy, S=sell, Space=navigate, Ctrl+P=positions, etc.) must work identically. Wire to IBKR equivalents of the same dialogs."),
  sectionDivider(),
  pageBreak(),

  // ══════════════════════════════════════════
  //  SECTION 7 — ORDER PLACEMENT
  // ══════════════════════════════════════════
  h1("7. Order Placement & Tracking"),
  h2("7.1 IBKR Order Lifecycle"),
  body("IBKR orders are tracked via the Trade object returned by ib.placeOrder(). ib_insync fires orderStatusEvent when status changes. The pattern replaces KiteTicker's on_order_update."),
  spacer(),
  ...codeBlock([
    "# Order placement — in IBKRTradingClient (already implemented)",
    "trade = self.ib.placeOrder(contract, order)",
    "# trade.order.orderId — use as order_id string",
    "",
    "# Order status tracking — wire in main_window __init__",
    "self.ib.orderStatusEvent += self._on_ibkr_order_status",
    "",
    "def _on_ibkr_order_status(self, trade):",
    "    order_dict = {",
    "        'order_id': str(trade.order.orderId),",
    "        'status': trade.orderStatus.status.upper(),  # SUBMITTED, FILLED, CANCELLED",
    "        'tradingsymbol': trade.contract.symbol,",
    "        'transaction_type': trade.order.action,      # BUY or SELL",
    "        'quantity': trade.order.totalQuantity,",
    "        'filled_quantity': trade.orderStatus.filled,",
    "        'average_price': trade.orderStatus.avgFillPrice,",
    "        'pending_quantity': trade.orderStatus.remaining,",
    "    }",
    "    # Route through identical pipeline as Kite mode:",
    "    self.position_manager.on_ws_order_update(order_dict)",
  ]),
  spacer(),
  h2("7.2 Status Code Mapping"),
  table2col([
    ["IBKR status", "Kite-equivalent status", ],
    ["Submitted", "OPEN"],
    ["PreSubmitted", "OPEN"],
    ["Filled", "COMPLETE"],
    ["PartiallyFilled", "OPEN (with filled_quantity > 0)"],
    ["Cancelled", "CANCELLED"],
    ["Inactive", "CANCELLED"],
    ["ApiPending", "OPEN"],
  ]),
  spacer(),
  tip("IBKR orderId is an integer. Convert to str when building order_dict to match Kite mode's string order_id convention used throughout PositionManager and TradeLogger."),
  spacer(),
  h2("7.3 Order Dialog Adaptation"),
  body("The OrderDialog (kite/widgets/order_dialog.py) can be reused directly. Only change:"),
  bullet("Currency symbol: replace ₹ with $ in all f-strings"),
  bullet("Exchange dropdown: replace NSE/BSE options with SMART/NYSE/NASDAQ"),
  bullet("Product types: replace MIS/CNC with IBKR ('DAY' for intraday, 'GTC' for swing — use GTD/GTC validity toggle)"),
  bullet("Default validity: 'DAY' for market/limit orders — swing trades use 'GTC'"),
  danger("Do not add IBKR-specific bracket order logic to OrderDialog in V1. Swing traders use simple market/limit entries with separate manual stops. Keep it simple."),
  sectionDivider(),
  pageBreak(),

  // ══════════════════════════════════════════
  //  SECTION 8 — INSTRUMENT LOADER
  // ══════════════════════════════════════════
  h1("8. Instrument Loader — US Equity Symbol Search"),
  body("IBKR does not provide a bulk instrument download like Kite's instruments() endpoint. Use reqMatchingSymbols() for on-demand search instead of pre-loading 50,000 instruments."),
  spacer(),
  h2("8.1 Search Bar Approach"),
  body("Keep the existing EnhancedSearchInput widget (kite/widgets/search_bar.py). Replace the backend SymbolIndex with IBKRSymbolSearch:"),
  ...codeBlock([
    "# ibkr/core/instrument_loader.py",
    "from ib_insync import IB",
    "from typing import List, Dict",
    "",
    "class IBKRSymbolSearch:",
    "    \"\"\"On-demand symbol search via reqMatchingSymbols.\"\"\"",
    "",
    "    def __init__(self, ib: IB):",
    "        self.ib = ib",
    "        self._cache: Dict[str, List[Dict]] = {}",
    "",
    "    def search(self, query: str, max_results: int = 12) -> List[Dict]:",
    "        if not query or len(query) < 1:",
    "            return []",
    "        upper = query.upper()",
    "        if upper in self._cache:",
    "            return self._cache[upper][:max_results]",
    "        try:",
    "            results = self.ib.reqMatchingSymbols(upper)",
    "        except Exception:",
    "            return []",
    "        instruments = []",
    "        for cd in results:",
    "            if cd.contract.secType != 'STK': continue",
    "            if cd.contract.currency != 'USD': continue",
    "            instruments.append({",
    "                'tradingsymbol': cd.contract.symbol,",
    "                'name': getattr(cd, 'longName', cd.contract.symbol),",
    "                'exchange': cd.contract.primaryExch or 'SMART',",
    "                'instrument_token': cd.contract.conId,",
    "                'instrument_type': 'EQ',",
    "            })",
    "        self._cache[upper] = instruments",
    "        return instruments[:max_results]",
  ]),
  spacer(),
  h2("8.2 Integration with EnhancedSearchInput"),
  body("The existing EnhancedSearchInput calls index.search(query). Create an adapter:"),
  ...codeBlock([
    "class IBKRSymbolIndex:",
    "    \"\"\"Adapter so IBKRSymbolSearch works with EnhancedSearchInput.set_symbol_index().\"\"\"",
    "    MAX_RESULTS = 12",
    "",
    "    def __init__(self, ib: IB):",
    "        self._searcher = IBKRSymbolSearch(ib)",
    "",
    "    def search(self, query: str, max_results: int = 12) -> List[Dict]:",
    "        return self._searcher.search(query, max_results)",
    "",
    "    def build(self, instruments):",
    "        pass  # no-op: IBKR uses on-demand search",
  ]),
  spacer(),
  warn("reqMatchingSymbols has a rate limit. Debounce the search input (already done — 60ms debounce in EnhancedSearchInput). Do not call on every keystroke without the debounce."),
  sectionDivider(),
  pageBreak(),

  // ══════════════════════════════════════════
  //  SECTION 9 — WIDGETS
  // ══════════════════════════════════════════
  h1("9. Widget Implementations"),
  h2("9.1 Watchlist Table (ibkr/widgets/watchlist_table.py)"),
  body("Copy kite/widgets/watchlist_table.py verbatim. Make only these changes:"),
  bullet("Persistence path: replace '.qullamaggie/kite' paths with '.qullamaggie/ibkr' in _APP_DIR and _DATA_DIR"),
  bullet("Currency display: no change needed — %Chg and LTP are currency-agnostic"),
  bullet("Exchange badge colors: add NYSE (blue) and NASDAQ (purple) badge colors alongside NSE/BSE"),
  bullet("Volume format: keep same _fmt_volume helper — works identically for US equities"),
  body("The token-to-symbol map uses IBKR conId as the integer token — identical data flow."),
  spacer(),
  h2("9.2 Scanner Table (ibkr/widgets/scanner_table.py)"),
  body("Copy kite/widgets/scanner_table.py structure. Replace the Chartink-specific backend:"),
  bullet("Use ibkr/scanner/run_finviz_scan.py (already implemented) instead of run_chartink_scan.py"),
  bullet("ScanWorker calls get_finviz_tickers(url) — same thread pattern"),
  bullet("Scan config file: save to '.qullamaggie/ibkr/finviz_scans.json'"),
  bullet("Remove Chartink URL validation — Finviz URLs have format 'https://finviz.com/screener.ashx?...'"),
  bullet("The symbol dict shape returned by run_finviz_scan includes 'symbol', 'price', 'volume', 'change_pct' — same as ChartinkScannerTable expects"),
  body("Scanner table already exists in ibkr/scanner/run_finviz_scan.py and ibkr/core/main_window.py references it. Build ibkr/widgets/scanner_table.py around ChartinkScannerTable as a structural template."),
  spacer(),
  h2("9.3 Positions Table (ibkr/widgets/positions_table.py)"),
  body("Copy kite/widgets/positions_table.py. Changes:"),
  bullet("Currency: replace ₹ with $ in footer labels"),
  bullet("Product display: IBKR does not have MIS/CNC. Show 'STOCK' or blank in product column"),
  bullet("Position data source: IBKRPositionManager.snapshot() returns normalized dicts — same field names as Kite via normalize_position()"),
  spacer(),
  h2("9.4 Header Toolbar (ibkr/widgets/header_toolbar.py)"),
  body("Copy kite/widgets/header_toolbar.py. Changes:"),
  bullet("Balance label: prefix $ instead of ₹"),
  bullet("Ticker board symbols default: ['SPY', 'QQQ', 'AAPL', 'TSLA'] instead of NIFTY/SENSEX"),
  bullet("Account balance extraction: call ib.accountValues() filtered to tag='AvailableFunds'"),
  bullet("Account manager wiring: use ibkr/core/account_manager.py (to build — mirrors kite version)"),
  spacer(),
  h2("9.5 StatusBar (ibkr/widgets/status_bar.py)"),
  body("Copy kite/widgets/status_bar.py exactly. Change only:"),
  bullet("Market hours check: NYSE Monday-Friday 9:30am-4:00pm EST (see Section 6.3)"),
  bullet("'MARKET' label: shows OPEN/CLOSED/PRE-OPEN (4am-9:30am)/AFTER-HOURS (4pm-8pm)"),
  sectionDivider(),
  pageBreak(),

  // ══════════════════════════════════════════
  //  SECTION 10 — PAPER TRADING
  // ══════════════════════════════════════════
  h1("10. Paper Trading Mode"),
  h2("10.1 IBKRPaperTrader"),
  body("Create ibkr/utils/paper_trading_manager.py using the same BasePaperTrader base class:"),
  ...codeBlock([
    "# ibkr/utils/paper_trading_manager.py",
    "from kite.utils.base_paper_trader import BasePaperTrader",
    "from typing import Optional, Dict, Any",
    "",
    "class IBKRPaperTrader(BasePaperTrader):",
    "    VALID_EXCHANGES = {'SMART', 'NYSE', 'NASDAQ', 'ARCA', 'BATS'}",
    "    VALID_PRODUCTS  = {'DAY', 'GTC', 'OPG'}  # IBKR validity types",
    "",
    "    def __init__(self, initial_balance: float = 100_000.0):",
    "        self._instrument_map: Dict[str, Dict] = {}",
    "        super().__init__(broker='ibkr', initial_balance=initial_balance)",
    "",
    "    def _resolve_trading_symbol(self, symbol: str) -> Optional[str]:",
    "        return symbol.strip().upper() or None",
    "",
    "    def _validate_order_parameters(self, variety, exchange, tradingsymbol,",
    "                                   transaction_type, quantity, product,",
    "                                   order_type, price, trigger_price) -> None:",
    "        if not tradingsymbol:",
    "            raise ValueError('tradingsymbol required')",
    "        if quantity <= 0:",
    "            raise ValueError(f'Quantity must be > 0, got {quantity}')",
    "        tx = (transaction_type or '').upper()",
    "        if tx not in ('BUY', 'SELL'):",
    "            raise ValueError(f'Invalid transaction_type: {transaction_type}')",
    "        ot = (order_type or '').upper()",
    "        if ot not in ('MARKET', 'LIMIT', 'STOP', 'STOP LIMIT'):",
    "            raise ValueError(f'Invalid order_type: {order_type}')",
    "",
    "    def _get_ltp(self, symbol: str) -> float:",
    "        data = self._market_data.get(symbol.upper(), {})",
    "        return float(data.get('last_price', 0.0))",
  ]),
  spacer(),
  body("IBKRPaperTrader.positions() returns the same dict structure as BasePaperTrader. Wire it through the same integrate_paper_trading() function pattern used in Kite mode. The paper account balance default is $100,000 (US swing account size)."),
  tip("IBKR also provides a real paper account (DU prefix). In production paper mode, connect to IBKR's actual paper account on port 7497 rather than simulating locally. The LocalIBKRPaperTrader is for offline development/testing only."),
  sectionDivider(),
  pageBreak(),

  // ══════════════════════════════════════════
  //  SECTION 11 — ACCOUNT MANAGER
  // ══════════════════════════════════════════
  h1("11. Account Manager"),
  h2("11.1 Fetching Balance from IBKR"),
  body("IBKR exposes account values via reqAccountValues(). For a swing trading terminal, fetch 'AvailableFunds' and 'NetLiquidation':"),
  ...codeBlock([
    "# ibkr/core/account_manager.py",
    "import logging",
    "from datetime import datetime, timedelta",
    "from typing import Any, Dict, Optional",
    "from PySide6.QtCore import QObject, QThreadPool, Signal, Slot",
    "from kite.utils.worker import Worker",
    "from kite.widgets.header_toolbar import DEFAULT_PAPER_BALANCE",
    "",
    "logger = logging.getLogger(__name__)",
    "",
    "class IBKRAccountManager(QObject):",
    "    margins_updated = Signal(dict)",
    "",
    "    def __init__(self, ib, refresh_interval_seconds: int = 30, parent=None):",
    "        super().__init__(parent)",
    "        self.ib = ib",
    "        self._ttl = timedelta(seconds=max(10, refresh_interval_seconds))",
    "        self._threadpool = QThreadPool(self)",
    "        self._is_refreshing = False",
    "        self._last_updated = None",
    "        self._account_cache = {",
    "            'user_id': 'IBKR',",
    "            'available_balance': DEFAULT_PAPER_BALANCE,",
    "        }",
    "",
    "    def get_cached_balance(self) -> float:",
    "        return float(self._account_cache.get('available_balance', DEFAULT_PAPER_BALANCE))",
    "",
    "    def refresh_margins(self, force: bool = False) -> None:",
    "        if self._is_refreshing: return",
    "        self._is_refreshing = True",
    "        worker = Worker(self._fetch_sync)",
    "        worker.signals.result.connect(self._on_result)",
    "        worker.signals.error.connect(lambda _: self._on_result(self._account_cache))",
    "        self._threadpool.start(worker)",
    "",
    "    def _fetch_sync(self) -> Dict[str, Any]:",
    "        # ib.accountValues() is synchronous and safe in Worker thread",
    "        vals = {v.tag: v.value for v in self.ib.accountValues()",
    "                if v.currency in ('USD', '')}",
    "        balance = float(vals.get('AvailableFunds', DEFAULT_PAPER_BALANCE))",
    "        accounts = self.ib.managedAccounts()",
    "        user_id = accounts[0] if accounts else 'IBKR'",
    "        return {'user_id': user_id, 'available_balance': balance}",
    "",
    "    @Slot(object)",
    "    def _on_result(self, info: Dict) -> None:",
    "        self._is_refreshing = False",
    "        self._account_cache = info",
    "        self._last_updated = datetime.utcnow()",
    "        self.margins_updated.emit(dict(info))",
  ]),
  sectionDivider(),
  pageBreak(),

  // ══════════════════════════════════════════
  //  SECTION 12 — POSITION MANAGER
  // ══════════════════════════════════════════
  h1("12. Position Manager (Qt Version)"),
  body("The existing IBKRPositionManager (ibkr/core/position_manager.py) is a thin synchronous wrapper. Create a Qt-signal version that mirrors kite/core/position_manager.py exactly:"),
  ...codeBlock([
    "# ibkr/core/position_manager_qt.py — skeleton",
    "from PySide6.QtCore import QObject, Signal, QTimer, Slot, QThreadPool",
    "from kite.utils.worker import Worker",
    "from kite.core.position_manager import Position  # reuse the Position dataclass",
    "from ibkr.core.position_manager import IBKRPositionManager",
    "from typing import List",
    "",
    "class IBKRPositionManagerQt(QObject):",
    "    positions_updated = Signal(list)   # List[Position]",
    "    day_pnl_updated   = Signal(dict)",
    "    show_notification = Signal(str, str)",
    "",
    "    def __init__(self, ib, main_window=None, trade_logger=None):",
    "        super().__init__()",
    "        self.ib = ib",
    "        self._pm = IBKRPositionManager(ib)",
    "        self.main_window = main_window",
    "        self.trade_logger = trade_logger",
    "        self._threadpool = QThreadPool.globalInstance()",
    "        self._pos_refresh_timer = QTimer(self)",
    "        self._pos_refresh_timer.timeout.connect(",
    "            lambda: self.fetch_positions('periodic'))",
    "        self._pos_refresh_timer.start(10_000)  # 10s for US market",
    "",
    "    @Slot(dict)",
    "    def on_ws_order_update(self, order_dict: dict) -> None:",
    "        \"\"\"Called by _on_ibkr_order_status in main_window.\"\"\"",
    "        status = order_dict.get('status', '').upper()",
    "        if status in ('FILLED', 'COMPLETE'):",
    "            self.fetch_positions('order_filled')",
    "        # Notify via show_notification",
    "",
    "    def fetch_positions(self, reason: str = 'manual') -> None:",
    "        worker = Worker(self._fetch_sync)",
    "        worker.signals.result.connect(self._handle_result)",
    "        self._threadpool.start(worker)",
    "",
    "    def _fetch_sync(self) -> List[dict]:",
    "        return self._pm.snapshot()",
    "",
    "    @Slot(object)",
    "    def _handle_result(self, raw_positions: List[dict]) -> None:",
    "        positions = [Position(",
    "            symbol=p['tradingsymbol'], quantity=int(p['quantity']),",
    "            avg_price=float(p['average_price']), token=0,",
    "            ltp=float(p.get('last_price', 0)),",
    "            pnl=float(p.get('pnl', 0)), product='IBKR'",
    "        ) for p in raw_positions if int(p.get('quantity', 0)) != 0]",
    "        self.positions_updated.emit(positions)",
  ]),
  sectionDivider(),
  pageBreak(),

  // ══════════════════════════════════════════
  //  SECTION 13 — DOS AND DON'TS
  // ══════════════════════════════════════════
  h1("13. Complete Dos and Don'ts"),
  spacer(),
  h2("13.1 Architecture"),
  tip("DO inherit IBKRPaperTrader from BasePaperTrader — the execution engine, balance management, and persistence are already correct and tested in Kite mode."),
  spacer(),
  tip("DO run all ib_insync API calls (reqHistoricalData, qualifyContracts, reqMktData, placeOrder) inside Worker(QRunnable) threads or in the dedicated IB QThread. Never in the Qt main thread."),
  spacer(),
  danger("DON'T create a second IB() object. One IB client per application. Pass the ib reference wherever needed."),
  spacer(),
  danger("DON'T call ib.sleep() anywhere. It blocks the event loop. Use QTimer.singleShot() for delays."),
  spacer(),
  danger("DON'T share self.ib between the market data thread and the main thread without proper Qt thread-affinity guards. The ib_insync pendingTickersEvent fires on the IB thread — the IBKRMarketDataWorkerQt._on_pending_tickers handler stores data and lets the flush QTimer (main thread) emit Qt signals."),
  spacer(),
  tip("DO keep all UI updates (setText, emit signals, table.setItem) on the main Qt thread. Use Signal/Slot or QTimer.singleShot(0, fn) to cross thread boundaries."),
  spacer(),
  h2("13.2 Data"),
  tip("DO cache qualified contracts in ContractManager._cache. Calling qualifyContracts() on every data request is slow and burns pacing limits."),
  spacer(),
  tip("DO use SMART exchange for all order placement. SMART smart-routes to NYSE, NASDAQ, ARCA, BATS automatically for best execution."),
  spacer(),
  danger("DON'T load all US instruments at startup. There are 10,000+ US stocks. Use on-demand reqMatchingSymbols() search instead."),
  spacer(),
  warn("IBKR pacing rule: max 50 simultaneous market data subscriptions for most plans. Monitor subscription count. Unsubscribe when symbol leaves watchlist."),
  spacer(),
  danger("DON'T call reqHistoricalData more than 6 times per minute per instrument. The chart engine should respect this via the data cache (MarketAwareDataCache) — make sure cache is wired before enabling chart auto-refresh."),
  spacer(),
  h2("13.3 UI"),
  tip("DO keep ALL color palette values identical to Kite mode. Copy token definitions from kite/widgets/scanner_table.py verbatim. No new palette values."),
  spacer(),
  tip("DO keep the main splitter layout: Scanner | Primary Chart | Secondary Chart | Watchlist+Positions. Same proportions (1:4:4:2). Same QSplitter restore logic."),
  spacer(),
  danger("DON'T add new dialogs or panels not present in Kite mode in V1. Build parity first, then extend."),
  spacer(),
  danger("DON'T change toast notification design, keyboard shortcuts, or sound triggers. These are fully shared from kite/ package."),
  spacer(),
  tip("DO replace ₹ with $ in all visible labels. Do not use unicode symbols in code — use f-string format like f'${value:,.2f}'."),
  spacer(),
  h2("13.4 Orders"),
  tip("DO route all live orders through IBKROrderRouter (ibkr/core/order_router.py — already implemented). Never call ib.placeOrder() directly from the main window."),
  spacer(),
  danger("DON'T attempt to use Kite-style 'variety=regular' or 'product=MIS' with IBKR orders. Map to IBKR's Order object fields: order.orderType, order.tif (time-in-force)."),
  spacer(),
  warn("IBKR requires that clientId be unique per connection. If the user opens two instances, use different clientIds (range 1-100 stored in preferences). Duplicate clientId causes connection failure error code 326."),
  sectionDivider(),
  pageBreak(),

  // ══════════════════════════════════════════
  //  SECTION 14 — IMPLEMENTATION ORDER
  // ══════════════════════════════════════════
  h1("14. Recommended Implementation Order"),
  body("Follow this sequence exactly. Each step builds on the previous. Do not skip ahead."),
  spacer(),
  h2("Phase 1 — Foundation (Week 1)"),
  numbered("Create ibkr/core/ibkr_data_fetcher.py — chart engine integration"),
  numbered("Create ibkr/core/market_data_worker_qt.py — tick streaming"),
  numbered("Create ibkr/core/account_manager.py — balance fetching"),
  numbered("Create ibkr/core/instrument_loader.py — on-demand symbol search with IBKRSymbolIndex"),
  numbered("Create ibkr/utils/paper_trading_manager.py — IBKRPaperTrader"),
  numbered("Test: connect to paper account, load AAPL chart, verify ticks appear in console logs"),
  spacer(),
  h2("Phase 2 — Widgets (Week 2)"),
  numbered("Create ibkr/widgets/watchlist_table.py — copy kite version, adapt paths + exchange badges"),
  numbered("Create ibkr/widgets/positions_table.py — copy kite version, adapt currency"),
  numbered("Create ibkr/widgets/scanner_table.py — copy kite structure, wire Finviz backend"),
  numbered("Create ibkr/widgets/header_toolbar.py — copy kite version, wire IBKR account manager"),
  numbered("Create ibkr/widgets/status_bar.py — copy kite version, adapt market hours"),
  numbered("Create ibkr/widgets/order_dialog.py — copy kite version, adapt currency + exchange"),
  numbered("Test: open main_window stub, verify all widgets render with correct AMOLED styling"),
  spacer(),
  h2("Phase 3 — Main Window (Week 3)"),
  numbered("Replace ibkr/core/main_window.py stub with full implementation (Section 6)"),
  numbered("Wire all signals: market data → watchlist/positions/chart, order events → position manager"),
  numbered("Create ibkr/core/position_manager_qt.py and wire to positions_table"),
  numbered("Implement _rebuild_subscription_universe() for IBKR (identical logic to Kite)"),
  numbered("Test: full flow — select symbol, view chart, place paper trade, see positions update"),
  spacer(),
  h2("Phase 4 — Polish & Parity (Week 4)"),
  numbered("Finalize keyboard shortcuts (spacebar navigation, buy/sell hotkeys)"),
  numbered("Wire alert system (ibkr instrument tokens as alert tokens)"),
  numbered("Wire chart lines manager for entry/exit/SL chart lines"),
  numbered("Wire trade logger for order history and P&L dialog"),
  numbered("Wire reconnection manager using NetworkMonitor (reuse kite version)"),
  numbered("Run tools/check_broker_parity.py to verify module-level parity with Kite"),
  numbered("Test all dialogs: order history, pending orders, performance, P&L history, floating positions"),
  sectionDivider(),
  pageBreak(),

  // ══════════════════════════════════════════
  //  SECTION 15 — AI AGENT PROMPTS
  // ══════════════════════════════════════════
  h1("15. AI Agent Prompt Templates"),
  body("Use these prompts when asking an AI coding assistant to implement specific parts. Each prompt is self-contained and references this document."),
  spacer(),
  h2("15.1 Prompt: IBKRDataFetcher"),
  ...codeBlock([
    "I am building the IBKR mode of the qullamaggie swing trading terminal.",
    "Reference: IBKR Implementation Guide Section 4.",
    "Create ibkr/core/ibkr_data_fetcher.py following the code skeleton in Section 4.1.",
    "Requirements:",
    "- fetch_ohlcv() must be synchronous (called in QThread Worker)",
    "- Cache qualified contracts in self._contract_cache keyed by symbol",
    "- Return list of dicts with keys: date, open, high, low, close, volume",
    "- Handle exceptions gracefully — return [] on any IBKR error",
    "- Map all chart_engine interval strings to IBKR barSizeSetting per Section 4.1",
    "- NEVER call this from the Qt main thread",
  ]),
  spacer(),
  h2("15.2 Prompt: IBKRMarketDataWorkerQt"),
  ...codeBlock([
    "I am building the IBKR mode of the qullamaggie swing trading terminal.",
    "Reference: IBKR Implementation Guide Section 5.",
    "Create ibkr/core/market_data_worker_qt.py following the skeleton in Section 5.2.",
    "Requirements:",
    "- Emit data_received(list) signal with tick dicts matching the shape in Section 5.3",
    "- Use a 225ms QTimer flush — accumulate ticks in _tick_buffer dict keyed by conId",
    "- subscribe(symbol) qualifies the contract, calls ib.reqMktData(), stores the ticker",
    "- unsubscribe(symbol) calls ib.cancelMktData() and removes from dicts",
    "- All ib_insync event handlers store to _tick_buffer only — never emit Qt signals directly",
    "- The flush timer runs on the Qt main thread and emits signals safely",
  ]),
  spacer(),
  h2("15.3 Prompt: ibkr/core/main_window.py"),
  ...codeBlock([
    "I am building the IBKR mode of the qullamaggie swing trading terminal.",
    "Reference: IBKR Implementation Guide Sections 6 and 13.",
    "Replace the stub in ibkr/core/main_window.py with a full implementation.",
    "Base: kite/core/main_window.py (uploaded in context).",
    "Apply substitutions from Section 6.1 table exactly.",
    "Keep identical: splitter layout, signal graph, keyboard shortcuts, alert system wiring,",
    "chart line manager wiring, shutdown steps, window state save/restore.",
    "Change only: broker references, currency symbol ₹→$, market hours EST,",
    "data fetcher, market data worker, instrument loader, account manager types.",
    "Color palette, font stack, toolbar height, row height — DO NOT CHANGE.",
  ]),
  spacer(),
  h2("15.4 Prompt: watchlist_table"),
  ...codeBlock([
    "I am building the IBKR mode of the qullamaggie swing trading terminal.",
    "Reference: IBKR Implementation Guide Section 9.1.",
    "Create ibkr/widgets/watchlist_table.py.",
    "Base: kite/widgets/watchlist_table.py (uploaded in context).",
    "Changes only:",
    "1. _APP_DIR and _DATA_DIR paths: replace '.qullamaggie/kite' with '.qullamaggie/ibkr'",
    "2. Exchange badge colors: add NYSE (#0057b8 bg, #7ab8ff fg) and NASDAQ (#1a0057 bg, #b07aff fg)",
    "3. Exchange preference order: NYSE first, NASDAQ second (instead of NSE/BSE)",
    "All other logic, styling, signals, and persistence must remain identical.",
  ]),
  spacer(),
  h2("15.5 Prompt: IBKRPaperTrader"),
  ...codeBlock([
    "I am building the IBKR mode of the qullamaggie swing trading terminal.",
    "Reference: IBKR Implementation Guide Section 10.",
    "Create ibkr/utils/paper_trading_manager.py.",
    "Requirements:",
    "- Class IBKRPaperTrader(BasePaperTrader) where BasePaperTrader is from kite.utils.base_paper_trader",
    "- broker='ibkr', initial_balance=100_000.0 (USD)",
    "- _resolve_trading_symbol: return symbol.strip().upper()",
    "- _validate_order_parameters: check tradingsymbol, quantity > 0, BUY/SELL, MARKET/LIMIT/STOP",
    "- _get_ltp: read from self._market_data dict keyed by symbol",
    "- Do NOT implement order_placed sounds — those come from BasePaperTrader signals",
    "- Connect via integrate_paper_trading() pattern from kite/utils/paper_trading_manager.py",
  ]),
  spacer(),
  h2("15.6 Prompt: Parity Check"),
  ...codeBlock([
    "I am building the IBKR mode of the qullamaggie swing trading terminal.",
    "Run tools/check_broker_parity.py and analyze the output.",
    "For each module that exists in kite/ but not ibkr/ (in core, widgets, utils, scanner):",
    "- Decide if it should be created for IBKR or is intentionally absent",
    "- If it should exist: create a minimal stub that imports correctly",
    "- If intentionally absent: document why in ibkr/__init__.py docstring",
    "Reference Section 14 Phase 4 item 6 of the IBKR Implementation Guide.",
  ]),
  sectionDivider(),
  pageBreak(),

  // ══════════════════════════════════════════
  //  SECTION 16 — TESTING CHECKLIST
  // ══════════════════════════════════════════
  h1("16. Testing & Validation Checklist"),
  body("Before considering any phase complete, verify each item:"),
  spacer(),
  h2("16.1 Connection & Auth"),
  bullet("IB Gateway starts on port 7497 (paper) — ibkr_auth.py connects successfully"),
  bullet("IBKRConnectionTester (login_setup/test_ibkr_connection.py) reports ✅ on all steps"),
  bullet("Second connection attempt with same clientId correctly shows conflict error 326"),
  bullet("Disconnect from gateway while app is running — reconnection overlay appears, app recovers"),
  spacer(),
  h2("16.2 Chart"),
  bullet("Load AAPL on day interval — 2 years of daily bars appear correctly"),
  bullet("Load AAPL on 60minute interval — 5 days of hourly bars appear correctly"),
  bullet("Switch symbol while loading — no double load, no frozen chart"),
  bullet("Chart line manager: entry line appears green after paper buy, removed after paper sell"),
  bullet("Historical data cache: second load of same symbol/interval uses cached data (no API call)"),
  spacer(),
  h2("16.3 Market Data"),
  bullet("Subscribe AAPL in watchlist — LTP and %Chg update in real time during market hours"),
  bullet("Open position in AAPL — positions_table shows live PnL"),
  bullet("Switch chart to MSFT — MSFT LTP appears in header ticker board within 1 second"),
  bullet("Remove AAPL from watchlist — subscription cancelled, no stale ticks"),
  spacer(),
  h2("16.4 Orders"),
  bullet("Paper BUY 10 AAPL MARKET — position appears in positions_table immediately"),
  bullet("Paper SELL 10 AAPL MARKET — position removed, realized PnL shown in status bar"),
  bullet("Paper LIMIT BUY below market — order stays pending in pending orders dialog"),
  bullet("Live (IBKR paper account) BUY 1 AAPL — order appears in TWS activity monitor"),
  spacer(),
  h2("16.5 UI Parity"),
  bullet("Dark palette matches kite mode exactly (compare side-by-side screenshots)"),
  bullet("Spacebar navigation works: scanner → chart loads, watchlist → chart loads"),
  bullet("Keyboard shortcuts: B opens buy dialog, S opens sell dialog, Ctrl+P opens floating positions"),
  bullet("Toast notifications appear for order fill, rejection, and alert triggers"),
  bullet("Sounds play: pop on buy/sell, alert on price alert trigger"),
  bullet("Window state restored correctly after app restart (splitter sizes, maximized state)"),
  sectionDivider(),
  pageBreak(),

  // ══════════════════════════════════════════
  //  SECTION 17 — KNOWN IBKR QUIRKS
  // ══════════════════════════════════════════
  h1("17. Known IBKR API Quirks & Workarounds"),
  spacer(),
  table2col([
    ["Quirk", "Workaround"],
    ["Error 162: pacing violation", "Respect 60 req/10min for historical data. Use MarketAwareDataCache. Add 1s delay between back-to-back chart loads."],
    ["Error 200: no security definition", "Contract not qualified. Always call ib.qualifyContracts() before any request. Check symbol spelling."],
    ["Error 326: client ID already in use", "Store last clientId in preferences. Offer clientId selector in IB Gateway page of login dialog (already implemented)."],
    ["Error 354: no subscription", "Market data subscription not active for security type. Inform user: 'US equities subscription required in Account Management.'"],
    ["ib.disconnect() hangs", "Set a 3-second timeout in shutdown. Use QThread.terminate() as last resort. Already handled in shutdown_manager."],
    ["reqMktData returns 0.0 for last_price before market open", "Use ticker.close (previous close) as fallback. Show '--' in LTP column when both are 0."],
    ["Historical data bars have date as string or datetime depending on formatDate", "Always pass formatDate=1 (returns datetime objects). Convert: bar.date.strftime('%Y-%m-%d %H:%M:%S')."],
    ["Paper account shows delayed data", "Paper account always shows real-time data if market data subscriptions are active on the linked live account."],
    ["placeOrder() returns Trade immediately but fill is async", "Subscribe to ib.orderStatusEvent. Use the same IBKRPositionManagerQt.on_ws_order_update() pipeline."],
    ["ib.positions() returns empty list right after connect", "Add a 2-second delay: QTimer.singleShot(2000, fetch_positions). IBKR needs time to send position data after connection."],
    ["Ticker.last is NaN during pre-market", "Use ticker.close as LTP fallback when ticker.last is nan or 0."],
  ]),
  sectionDivider(),
  pageBreak(),

  // ══════════════════════════════════════════
  //  SECTION 18 — QUICK REFERENCE
  // ══════════════════════════════════════════
  h1("18. Quick Reference"),
  spacer(),
  h2("18.1 Critical Imports"),
  ...codeBlock([
    "# Always available — use these, do not reimplement",
    "from kite.utils.base_paper_trader import BasePaperTrader",
    "from kite.utils.pnl_calculator import PnLCalculator",
    "from kite.utils.worker import Worker",
    "from kite.utils.sounds import play_alert, play_entry_exit, play_error",
    "from kite.utils.color_system import get_color_theme_manager",
    "from kite.core.alert_management_system import AlertSystemManager",
    "from kite.core.chart_lines_manager import ChartLinesManager",
    "from kite.core.data_cache import MarketAwareDataCache",
    "from kite.core.shutdown_manager import CleanShutdownMixin",
    "from kite.core.trade_logger import TradeLogger",
    "from kite.widgets.notifications import ToastNotification",
    "from kite.widgets.status_bar import status, StatusBar",
    "from kite.widgets.order_dialog import OrderDialog",
    "from kite.widgets.performance_dialog import PerformanceDialog",
    "from login_setup.broker_modes import BrokerMode, TradingMode",
    "from login_setup.token_manager import EnhancedTokenManager",
    "from chart_engine import CandlestickChart",
    "from ibkr.core.ibkr_data_fetcher import IBKRDataFetcher",
    "from ib_insync import IB, Stock, MarketOrder, LimitOrder, StopOrder",
  ]),
  spacer(),
  h2("18.2 UI Tokens (copy verbatim)"),
  ...codeBlock([
    "_BG0      = '#050709'   # deepest app shell",
    "_BG1      = '#0a0d12'   # main table body",
    "_BG2      = '#0f1318'   # panel / alternate row",
    "_BG3      = '#141920'   # hover / raised control",
    "_BG4      = '#1a2030'   # borders",
    "_BG5      = '#26354a'   # active border / focus",
    "_BULL     = '#00d4a8'   # profit / buy / success",
    "_BEAR     = '#ff4d6a'   # loss / sell / danger",
    "_AMBER    = '#f59e0b'   # warning / active",
    "_CYAN     = '#00d4ff'   # info / utility / focus",
    "_T0       = '#e8f0ff'   # primary text",
    "_T1       = '#a8bcd4'   # secondary text",
    "_T2       = '#5a7090'   # muted labels",
    "_T3       = '#2a3a50'   # disabled",
    "_SEL      = '#1a2840'   # selected row",
    "_SANS     = \"'Inter','Aptos','Segoe UI Variable','Segoe UI','Roboto','Noto Sans',sans-serif\"",
    "_ROW_H    = 21          # table row height px",
    "_TOOLBAR_H = 32         # header toolbar height px",
    "_CONTROL_H = 24         # button/input height px",
  ]),
  spacer(),
  h2("18.3 IBKR Error Codes Reference"),
  table2col([
    ["Error Code", "Meaning"],
    ["100-199", "Informational — log only, not errors"],
    ["162", "Pacing violation — slow down historical data requests"],
    ["200", "No security definition — qualify contract first"],
    ["321", "Error in validating request — wrong parameters"],
    ["326", "Client ID already in use — change clientId"],
    ["354", "No subscription — missing market data subscription"],
    ["502", "Cannot connect — TWS/Gateway not running"],
    ["504", "Not connected — connection dropped"],
    ["1100", "Connectivity lost — network issue"],
    ["1101", "Connectivity restored, data lost — re-subscribe"],
    ["1102", "Connectivity restored, data maintained — no action needed"],
  ]),
  sectionDivider(),
  spacer(),

  // ══════════════════════════════════════════
  //  CLOSING
  // ══════════════════════════════════════════
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 400, after: 200 },
    children: [new TextRun({ text: "END OF IBKR IMPLEMENTATION GUIDE", size: 24, bold: true, font: "Arial", color: H1_COLOR })]
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 80, after: 80 },
    children: [new TextRun({ text: "Build beautiful. Trade smart. Ship parity.", size: 22, font: "Arial", color: DIM_COLOR, italics: true })]
  }),
];

// ─────────────────────────────────────────────────────────────────────────────
// ASSEMBLE DOCUMENT
// ─────────────────────────────────────────────────────────────────────────────

const doc = new Document({
  numbering: {
    config: [
      {
        reference: "bullets",
        levels: [
          { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 480, hanging: 240 } } } },
          { level: 1, format: LevelFormat.BULLET, text: "◦", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 840, hanging: 240 } } } },
        ]
      },
      {
        reference: "numbers",
        levels: [
          { level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 480, hanging: 240 } } } },
        ]
      }
    ]
  },
  styles: {
    default: {
      document: { run: { font: "Arial", size: 22 } }
    },
    paragraphStyles: [
      {
        id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 34, bold: true, font: "Arial", color: H1_COLOR },
        paragraph: { spacing: { before: 400, after: 180 }, outlineLevel: 0 }
      },
      {
        id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 28, bold: true, font: "Arial", color: H2_COLOR },
        paragraph: { spacing: { before: 300, after: 140 }, outlineLevel: 1 }
      },
      {
        id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: "Arial", color: H3_COLOR },
        paragraph: { spacing: { before: 220, after: 100 }, outlineLevel: 2 }
      },
    ]
  },
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 },
        margin: { top: 1080, right: 1080, bottom: 1080, left: 1080 }
      }
    },
    children
  }]
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync('/mnt/user-data/outputs/IBKR_Implementation_Guide.docx', buf);
  console.log('Done: IBKR_Implementation_Guide.docx');
}).catch(e => { console.error(e); process.exit(1); });
