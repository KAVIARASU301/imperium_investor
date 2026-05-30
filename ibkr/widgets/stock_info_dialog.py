# kite/widgets/stock_info_dialog.py
"""
StockInfoDialog — Bloomberg-style stock fundamentals panel for the IBKR terminal.

Fetches data from yfinance with US-first symbol resolution and displays:
  • Company name, sector, industry, description
  • Key valuation metrics: PE, PB, EV/EBITDA, Market Cap
  • Profitability: EPS, ROE, ROA, Profit Margin
  • Earnings dates (next & previous)
  • Dividend info
  • 52-week range
  • Analyst target price & recommendation

Usage:
    from ibkr.widgets.stock_info_dialog import StockInfoDialog

    dialog = StockInfoDialog("AAPL", parent=self)
    dialog.show()

    # Or use the convenience function:
    show_stock_info("AAPL", parent=main_window)
"""

from __future__ import annotations

import html
import logging
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, QPoint, QObject, Signal, QThread, QEvent
from PySide6.QtGui import QMouseEvent, QCursor, QGuiApplication, QColor
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractSpinBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QVBoxLayout, QApplication,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND DATA FETCHER
# ─────────────────────────────────────────────────────────────────────────────

class _FetchWorker(QObject):
    """Fetches yfinance data in a background thread and emits result."""

    finished   = Signal(dict)   # emits parsed info dict on success
    error      = Signal(str)    # emits error message on failure

    def __init__(self, symbol: str, parent=None):
        super().__init__(parent)
        self._symbol = symbol

    def run(self):
        try:
            import yfinance as yf

            # US-first resolution: plain symbol (works for NASDAQ/NYSE), then Indian fallbacks.
            candidate_symbols = [self._symbol, f"{self._symbol}.NS", f"{self._symbol}.BO"]
            ticker_sym = self._symbol
            info = {}
            ticker = None

            for cand in candidate_symbols:
                t = yf.Ticker(cand)
                data = t.info or {}
                if data.get("longName") or data.get("shortName"):
                    ticker_sym = cand
                    ticker = t
                    info = data
                    break

            if ticker is None:
                ticker = yf.Ticker(self._symbol)
                info = ticker.info or {}

            # Pull calendar for earnings
            cal = {}
            try:
                cal_raw = ticker.calendar
                if cal_raw is not None:
                    if hasattr(cal_raw, "to_dict"):
                        cal = cal_raw.to_dict()
                    elif isinstance(cal_raw, dict):
                        cal = cal_raw
            except Exception:
                pass

            result = self._parse(info, cal, ticker_sym)
            self.finished.emit(result)

        except ImportError:
            self.error.emit(
                "yfinance is not installed.\n"
                "Run:  pip install yfinance --break-system-packages"
            )
        except Exception as exc:
            logger.error("StockInfoDialog fetch error for %s: %s", self._symbol, exc)
            self.error.emit(str(exc))

    # ── parsing ───────────────────────────────────────────────────────────

    @staticmethod
    def _fmt_large(val) -> str:
        """Format large numbers in compact international units."""
        try:
            v = float(val)
        except (TypeError, ValueError):
            return "—"
        if v >= 1e12:
            return f"${v/1e12:.2f}T"
        if v >= 1e9:
            return f"${v/1e9:.2f}B"
        return f"${v:,.0f}"

    @staticmethod
    def _fmt_pct(val) -> str:
        try:
            return f"{float(val)*100:.2f}%"
        except (TypeError, ValueError):
            return "—"

    @staticmethod
    def _fmt_num(val, decimals=2) -> str:
        try:
            return f"{float(val):.{decimals}f}"
        except (TypeError, ValueError):
            return "—"

    @staticmethod
    def _fmt_date(val) -> str:
        if not val:
            return "—"
        try:
            from datetime import datetime, date
            if isinstance(val, (int, float)):
                return datetime.fromtimestamp(val).strftime("%d %b %Y")
            if isinstance(val, (datetime, date)):
                return val.strftime("%d %b %Y")
            return str(val)
        except Exception:
            return str(val)

    def _parse(self, info: dict, cal: dict, ticker_sym: str) -> dict:
        g = info.get
        fd = self._fmt_date
        fn = self._fmt_num
        fp = self._fmt_pct
        fl = self._fmt_large

        # Earnings date
        earnings_date = "—"
        try:
            ed = cal.get("Earnings Date") or cal.get("earningsDate")
            if ed:
                if isinstance(ed, list) and ed:
                    earnings_date = fd(ed[0])
                else:
                    earnings_date = fd(ed)
        except Exception:
            pass

        # 52-week range
        low52  = fn(g("fiftyTwoWeekLow"),  2)
        high52 = fn(g("fiftyTwoWeekHigh"), 2)
        range52 = f"{low52} – {high52}" if low52 != "—" else "—"

        # Analyst recommendation
        rec = str(g("recommendationKey") or "—").replace("_", " ").title()
        target = fn(g("targetMeanPrice"), 2)
        if target != "—":
            target = f"${target}"

        # Revenue / earnings growth
        rev_growth  = fp(g("revenueGrowth"))
        earn_growth = fp(g("earningsGrowth"))

        # Beta
        beta = fn(g("beta"), 2)

        # Dividend
        div_yield = fp(g("dividendYield"))
        div_rate  = fn(g("dividendRate"), 2)
        if div_rate != "—":
            div_rate = f"${div_rate}"
        ex_div = fd(g("exDividendDate"))

        description = g("longBusinessSummary") or g("description") or ""
        if len(description) > 900:
            description = description[:897] + "..."

        return {
            "symbol":          self._symbol,
            "ticker_sym":      ticker_sym,
            "name":            g("longName") or g("shortName") or self._symbol,
            "sector":          g("sector") or "—",
            "industry":        g("industry") or "—",
            "exchange":        g("exchange") or "US",
            "currency":        g("currency") or "USD",
            "country":         g("country") or "United States",
            "website":         g("website") or "",
            "employees":       f"{int(g('fullTimeEmployees')):,}" if g("fullTimeEmployees") else "—",
            "description":     description,

            # Valuation
            "market_cap":      fl(g("marketCap")),
            "pe_ratio":        fn(g("trailingPE"), 2),
            "forward_pe":      fn(g("forwardPE"), 2),
            "pb_ratio":        fn(g("priceToBook"), 2),
            "ev_ebitda":       fn(g("enterpriseToEbitda"), 2),
            "ev":              fl(g("enterpriseValue")),
            "peg":             fn(g("pegRatio"), 2),
            "ps_ratio":        fn(g("priceToSalesTrailing12Months"), 2),

            # Per-share
            "eps_ttm":         fn(g("trailingEps"), 2),
            "eps_fwd":         fn(g("forwardEps"), 2),
            "book_value":      fn(g("bookValue"), 2),

            # Profitability
            "roe":             fp(g("returnOnEquity")),
            "roa":             fp(g("returnOnAssets")),
            "profit_margin":   fp(g("profitMargins")),
            "gross_margin":    fp(g("grossMargins")),
            "operating_margin":fp(g("operatingMargins")),

            # Growth
            "rev_growth":      rev_growth,
            "earn_growth":     earn_growth,

            # Market data
            "range52":         range52,
            "beta":            beta,
            "avg_volume":      f"{int(g('averageVolume')):,}" if g("averageVolume") else "—",
            "float_shares":    fl(g("floatShares")),

            # Dividends
            "div_yield":       div_yield,
            "div_rate":        div_rate,
            "ex_div":          ex_div,

            # Analyst
            "analyst_target":  target,
            "recommendation":  rec,
            "num_analysts":    str(g("numberOfAnalystOpinions") or "—"),

            # Earnings
            "earnings_date":   earnings_date,
            "fiscal_year_end": g("fiscalYearEnd") or "—",
        }


class _FetchThread(QThread):
    """QThread wrapper around _FetchWorker."""

    finished = Signal(dict)
    error    = Signal(str)

    def __init__(self, symbol: str, parent=None):
        super().__init__(parent)
        self._symbol = symbol

    def run(self):
        worker = _FetchWorker(self._symbol)
        worker.finished.connect(self.finished)
        worker.error.connect(self.error)
        worker.run()


# ─────────────────────────────────────────────────────────────────────────────
# HTML RENDERER  (Institutional Dark Trading Terminal UI + modern UI number typography)
# ─────────────────────────────────────────────────────────────────────────────

_BG0 = "#050709"   # AMOLED shell
_BG1 = "#070a0f"   # dialog/content background
_BG2 = "#0a0d12"   # primary card surface
_BG3 = "#0f1318"   # alternate row / hover surface
_BG4 = "#1a2030"   # thin borders
_BGTB = "#050709"  # title/footer bars
_BULL = "#00d4a8"
_BEAR = "#ff4d6a"
_AMBER = "#d7a45d"
_CYAN = "#78cfe1"
_BLUE = "#7fa6d8"
_T0 = "#d8e2ef"
_T1 = "#a8b4c2"
_T2 = "#748396"
_T3 = "#3b4758"
_SEL = "#1a2840"
_SANS = "'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', Arial, sans-serif"
_NUM = "'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', Arial, sans-serif"
_MONO = "'Consolas', 'JetBrains Mono', monospace"  # only for code/raw technical text


def _esc(value: Any) -> str:
    """Escape user/provider supplied values before injecting into WebEngine HTML."""
    if value is None:
        return "—"
    return html.escape(str(value), quote=True)


_LOADING_HTML = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ height: 100%; }}
  body {{
    background: {_BG0};
    color: {_T2};
    font-family: {_SANS};
    display: flex;
    align-items: center;
    justify-content: center;
    -webkit-font-smoothing: antialiased;
    text-rendering: geometricPrecision;
  }}
  .terminal-loader {{
    width: 360px;
    border: 1px solid {_BG4};
    border-radius: 2px;
    background: {_BG2};
    box-shadow: 0 16px 40px rgba(0,0,0,.38);
  }}
  .loader-head {{
    height: 26px;
    display: flex;
    align-items: center;
    padding: 0 9px;
    background: {_BGTB};
    border-bottom: 1px solid {_BG4};
    color: {_AMBER};
    font: 700 10px {_SANS};
    letter-spacing: 1.1px;
  }}
  .loader-body {{ padding: 14px 12px 12px; }}
  .line {{
    height: 7px;
    background: {_BG3};
    border: 1px solid {_BG4};
    margin-bottom: 7px;
    overflow: hidden;
  }}
  .line::after {{
    content: '';
    display: block;
    height: 100%;
    width: 42%;
    background: {_CYAN};
    opacity: .55;
    animation: scan 1.05s linear infinite;
  }}
  .small {{
    color: {_T2};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .8px;
    text-transform: uppercase;
    margin-top: 9px;
  }}
  @keyframes scan {{
    0% {{ transform: translateX(-110%); }}
    100% {{ transform: translateX(250%); }}
  }}
</style></head><body>
<div class="terminal-loader">
  <div class="loader-head">FETCHING FUNDAMENTALS</div>
  <div class="loader-body">
    <div class="line"></div><div class="line"></div><div class="line"></div>
    <div class="small">yfinance US-first symbol lookup in progress…</div>
  </div>
</div>
</body></html>"""


_ERROR_HTML_TPL = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ height: 100%; }}
  body {{
    background: {_BG0};
    color: {_BEAR};
    font-family: {_SANS};
    -webkit-font-smoothing: antialiased;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
  }}
  .error-card {{
    width: 440px;
    border: 1px solid rgba(255,77,106,.34);
    border-radius: 2px;
    background: {_BG2};
    box-shadow: 0 16px 40px rgba(0,0,0,.38);
  }}
  .error-head {{
    height: 28px;
    display: flex;
    align-items: center;
    padding: 0 10px;
    background: {_BGTB};
    border-bottom: 1px solid rgba(255,77,106,.26);
    color: {_BEAR};
    font: 800 10px {_SANS};
    letter-spacing: 1.2px;
  }}
  .error-body {{ padding: 14px 12px; }}
  .msg {{
    color: {_T1};
    font-size: 12px;
    line-height: 1.55;
    white-space: pre-wrap;
  }}
</style></head><body>
<div class="error-card">
  <div class="error-head">DATA UNAVAILABLE</div>
  <div class="error-body"><div class="msg">{{msg}}</div></div>
</div>
</body></html>"""


def _build_info_html(data: dict) -> str:
    """Render compact fundamentals tables in the terminal dark design system."""

    d = data or {}

    def val(key: str, default: str = "—") -> str:
        return _esc(d.get(key, default) or default)

    def tone(value: str, up: str = _BULL, down: str = _BEAR, neutral: str = _T0) -> str:
        if not value or value == "—":
            return _T3
        t = str(value).strip().lower()
        if t.startswith("-") or "sell" in t:
            return down
        if t.startswith("+") or "buy" in t or ("%" in t and not t.startswith("-")):
            return up
        if "hold" in t or "neutral" in t:
            return _AMBER
        return neutral

    def row(label: str, value: Any, color: str = _T0) -> str:
        safe_value = _esc(value)
        empty = safe_value in ("—", "", "None")
        css_color = _T3 if empty else color
        safe_label = _esc(label)
        return (
            '<tr>'
            f'<td class="metric-label">{safe_label}</td>'
            f'<td class="metric-value" style="color:{css_color}">{safe_value if not empty else "—"}</td>'
            '</tr>'
        )

    def section(title: str, rows_html: str, accent: str = _CYAN) -> str:
        return (
            '<section class="metric-panel">'
            f'<div class="panel-title" style="--accent:{accent}">{_esc(title)}</div>'
            '<table class="metric-table"><tbody>'
            f'{rows_html}'
            '</tbody></table>'
            '</section>'
        )

    recommendation = str(d.get("recommendation", "—") or "—")
    rec_lower = recommendation.lower()
    if "buy" in rec_lower or "strong" in rec_lower:
        rec_color = _BULL
    elif "sell" in rec_lower:
        rec_color = _BEAR
    elif "hold" in rec_lower or "neutral" in rec_lower:
        rec_color = _AMBER
    else:
        rec_color = _T1

    website = str(d.get("website", "") or "").strip()
    web_html = (
        f'<a class="terminal-link" href="{_esc(website)}" target="_blank">WEBSITE</a>'
        if website else ""
    )

    description = str(d.get("description", "") or "").strip()
    desc_html = (
        f'<div class="description"><div class="section-kicker">BUSINESS SUMMARY</div>{_esc(description)}</div>'
        if description else ""
    )

    quick_stats = (
        f'<div class="stat"><span>MARKET CAP</span><strong class="cyan">{val("market_cap")}</strong></div>'
        f'<div class="stat"><span>52W RANGE</span><strong class="amber">{val("range52")}</strong></div>'
        f'<div class="stat"><span>TARGET</span><strong class="cyan">{val("analyst_target")}</strong></div>'
        f'<div class="stat"><span>RECO</span><strong style="color:{rec_color}">{_esc(recommendation)}</strong></div>'
    )

    valuation_rows = (
        row("Market Cap", d.get("market_cap"), _CYAN) +
        row("P/E TTM", d.get("pe_ratio"), _BLUE) +
        row("Forward P/E", d.get("forward_pe")) +
        row("P/B Ratio", d.get("pb_ratio")) +
        row("EV/EBITDA", d.get("ev_ebitda")) +
        row("P/S Ratio", d.get("ps_ratio")) +
        row("PEG Ratio", d.get("peg")) +
        row("Enterprise Value", d.get("ev"), _BULL)
    )

    per_share_rows = (
        row("EPS TTM", d.get("eps_ttm")) +
        row("EPS Forward", d.get("eps_fwd")) +
        row("Book Value", d.get("book_value"))
    )

    profitability_rows = (
        row("ROE", d.get("roe"), tone(str(d.get("roe", "")))) +
        row("ROA", d.get("roa"), tone(str(d.get("roa", "")))) +
        row("Net Margin", d.get("profit_margin"), tone(str(d.get("profit_margin", "")))) +
        row("Gross Margin", d.get("gross_margin"), tone(str(d.get("gross_margin", "")))) +
        row("Operating Margin", d.get("operating_margin"), tone(str(d.get("operating_margin", "")))) +
        row("Revenue Growth YoY", d.get("rev_growth"), tone(str(d.get("rev_growth", "")))) +
        row("EPS Growth YoY", d.get("earn_growth"), tone(str(d.get("earn_growth", ""))))
    )

    market_rows = (
        row("52-Week Range", d.get("range52"), _AMBER) +
        row("Beta", d.get("beta")) +
        row("Average Volume", d.get("avg_volume")) +
        row("Float Shares", d.get("float_shares")) +
        row("Employees", d.get("employees"))
    )

    dividend_rows = (
        row("Dividend Yield", d.get("div_yield"), tone(str(d.get("div_yield", "")))) +
        row("Dividend Rate", d.get("div_rate")) +
        row("Ex-Dividend Date", d.get("ex_div"))
    )

    analyst_rows = (
        row("Price Target", d.get("analyst_target"), _CYAN) +
        row("Recommendation", recommendation, rec_color) +
        row("Analyst Count", d.get("num_analysts")) +
        row("Next Earnings", d.get("earnings_date"), _AMBER) +
        row("Fiscal Year End", d.get("fiscal_year_end"))
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
:root {{
  --bg0:{_BG0}; --bg1:{_BG1}; --bg2:{_BG2}; --bg3:{_BG3}; --bg4:{_BG4};
  --title:{_BGTB}; --bull:{_BULL}; --bear:{_BEAR}; --amber:{_AMBER}; --cyan:{_CYAN};
  --blue:{_BLUE}; --t0:{_T0}; --t1:{_T1}; --t2:{_T2}; --t3:{_T3}; --sel:{_SEL};
  --font-ui:{_SANS}; --font-num:{_NUM}; --font-mono:{_MONO};
}}
html, body {{ height: 100%; }}
body {{
  background: var(--bg0);
  color: var(--t0);
  font-family: var(--font-ui);
  font-size: 12px;
  line-height: 1.45;
  overflow-y: auto;
  -webkit-font-smoothing: antialiased;
  text-rendering: geometricPrecision;
}}
.shell {{ min-height: 100%; background: var(--bg1); }}
.instrument-head {{
  min-height: 72px;
  padding: 10px 12px 9px;
  background: linear-gradient(180deg, var(--bg0) 0%, #070b10 100%);
  border-bottom: 1px solid var(--bg4);
}}
.top-line {{
  display: flex;
  align-items: flex-start;
  gap: 10px;
}}
.symbol-chip {{
  min-width: 84px;
  padding: 6px 10px;
  border: 1px solid rgba(120,207,225,.22);
  border-left: 3px solid var(--cyan);
  border-radius: 2px;
  background: rgba(120,207,225,.045);
  color: #b8ccd9;
  font: 700 13px var(--font-ui);
  letter-spacing: .8px;
  text-align: center;
}}
.company-block {{ flex: 1; min-width: 0; }}
.company-name {{
  color: var(--t0);
  font-size: 17px;
  font-weight: 650;
  letter-spacing: .1px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.meta-row {{
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
  margin-top: 7px;
}}
.meta-tag {{
  height: 20px;
  display: inline-flex;
  align-items: center;
  padding: 0 7px;
  border: 1px solid var(--bg4);
  border-radius: 2px;
  background: var(--bg2);
  color: var(--t2);
  font-size: 9px;
  font-weight: 650;
  letter-spacing: .65px;
  text-transform: uppercase;
}}
.meta-tag.active {{
  color: var(--amber);
  border-color: rgba(215,164,93,.32);
  background: rgba(215,164,93,.055);
}}
.terminal-link {{
  height: 20px;
  display: inline-flex;
  align-items: center;
  padding: 0 7px;
  color: var(--cyan);
  border: 1px solid rgba(120,207,225,.22);
  border-radius: 2px;
  background: rgba(120,207,225,.045);
  text-decoration: none;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: .65px;
}}
.terminal-link:hover {{ background: rgba(120,207,225,.08); color: #c8edf4; }}
.quick-strip {{
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 7px;
  padding: 8px 10px;
  background: var(--bg1);
  border-bottom: 1px solid var(--bg4);
}}
.stat {{
  min-height: 42px;
  border: 1px solid var(--bg4);
  border-radius: 2px;
  background: var(--bg2);
  padding: 6px 8px;
}}
.stat span {{
  display: block;
  color: var(--t2);
  font-size: 8.5px;
  font-weight: 700;
  letter-spacing: .95px;
}}
.stat strong {{
  display: block;
  margin-top: 4px;
  color: var(--t0);
  font: 700 12.5px var(--font-num);
  font-variant-numeric: tabular-nums;
  font-feature-settings: 'tnum' 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.cyan {{ color: var(--cyan) !important; }}
.amber {{ color: var(--amber) !important; }}
.description {{
  margin: 8px 10px 0;
  padding: 9px 10px;
  border: 1px solid var(--bg4);
  border-radius: 2px;
  background: var(--bg2);
  color: var(--t1);
  font-size: 11.5px;
  line-height: 1.56;
  max-height: 112px;
  overflow: auto;
}}
.section-kicker {{
  color: var(--amber);
  font: 700 9px var(--font-ui);
  letter-spacing: 1.1px;
  margin-bottom: 5px;
}}
.panels {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  padding: 8px 10px 10px;
}}
.metric-panel {{
  border: 1px solid var(--bg4);
  border-radius: 2px;
  background: var(--bg2);
  min-width: 0;
}}
.panel-title {{
  height: 27px;
  display: flex;
  align-items: center;
  padding: 0 8px;
  border-bottom: 1px solid var(--bg4);
  color: var(--t1);
  font-size: 9px;
  font-weight: 750;
  letter-spacing: 1px;
  text-transform: uppercase;
  position: relative;
  background: rgba(255,255,255,.012);
}}
.panel-title::before {{
  content: '';
  width: 3px;
  height: 13px;
  margin-right: 7px;
  background: var(--accent);
  opacity: .9;
}}
.metric-table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
.metric-table tr {{ height: 26px; border-bottom: 1px solid var(--bg3); }}
.metric-table tr:nth-child(even) {{ background: rgba(255,255,255,.012); }}
.metric-table tr:hover {{ background: rgba(120,207,225,.035); }}
.metric-table tr:last-child {{ border-bottom: none; }}
.metric-label {{
  width: 53%;
  padding: 0 8px;
  color: var(--t2);
  font-size: 10.5px;
  font-weight: 550;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.metric-value {{
  width: 47%;
  padding: 0 8px;
  color: var(--t0);
  font: 650 11px var(--font-num);
  font-variant-numeric: tabular-nums;
  font-feature-settings: 'tnum' 1;
  text-align: right;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
::-webkit-scrollbar {{ width: 5px; height: 5px; }}
::-webkit-scrollbar-track {{ background: var(--bg1); }}
::-webkit-scrollbar-thumb {{ background: var(--bg4); border-radius: 2px; }}
::-webkit-scrollbar-thumb:hover {{ background: var(--t2); }}
@media (max-width: 980px) {{
  .panels {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .quick-strip {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
}}
@media (max-width: 680px) {{
  .panels {{ grid-template-columns: 1fr; }}
  .company-name {{ white-space: normal; }}
}}
</style></head><body>
<div class="shell">
  <header class="instrument-head">
    <div class="top-line">
      <div class="symbol-chip">{val('symbol')}</div>
      <div class="company-block">
        <div class="company-name">{val('name')}</div>
        <div class="meta-row">
          <span class="meta-tag active">{val('sector')}</span>
          <span class="meta-tag">{val('industry')}</span>
          <span class="meta-tag">{val('exchange')}</span>
          <span class="meta-tag">{val('country')}</span>
          <span class="meta-tag">{val('currency')}</span>
          {web_html}
        </div>
      </div>
    </div>
  </header>
  <div class="quick-strip">{quick_stats}</div>
  {desc_html}
  <main class="panels">
    {section('Valuation', valuation_rows, _CYAN)}
    {section('Per Share', per_share_rows, _BULL)}
    {section('Profitability & Growth', profitability_rows, _BEAR)}
    {section('Market Data', market_rows, _AMBER)}
    {section('Dividends & Events', dividend_rows, _BLUE)}
    {section('Analyst Coverage', analyst_rows, _BULL)}
  </main>
</div>
</body></html>"""


# ─────────────────────────────────────────────────────────────────────────────
# MAIN DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class StockInfoDialog(QDialog):
    """
    Bloomberg-style stock fundamentals dialog.

    Usage:
        dialog = StockInfoDialog("RELIANCE", parent=main_window)
        dialog.show()          # non-blocking
        # or
        dialog.exec()          # blocking
    """

    def __init__(self, symbol: str, parent=None):
        flags = Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumSize(880, 560)
        self.resize(1080, 680)

        self._symbol = symbol.strip().upper()
        self._drag_active = False
        self._drag_offset = QPoint()
        self._fetch_thread: Optional[_FetchThread] = None

        self._build_ui()
        self._apply_styles()
        self._start_fetch()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        root.addWidget(self._build_title_bar())

        self._web = QWebEngineView()
        self._web.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._web.page().setBackgroundColor(QColor("#050709"))
        self._apply_webengine_zoom()
        root.addWidget(self._web, 1)

        root.addWidget(self._build_footer())

    def _build_title_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("siTitleBar")
        bar.setFixedHeight(28)

        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 0, 6, 0)
        h.setSpacing(6)

        # Icon + label
        self._title_lbl = QLabel(f"STOCK FUNDAMENTALS  ·  {self._symbol}")
        self._title_lbl.setObjectName("siTitle")

        # Refresh button
        self._refresh_btn = QPushButton("↻")
        self._refresh_btn.setObjectName("siBarBtn")
        self._refresh_btn.setFixedSize(24, 20)
        self._refresh_btn.setToolTip("Refresh data from yfinance")
        self._refresh_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._refresh_btn.clicked.connect(self._start_fetch)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("siCloseBtn")
        close_btn.setFixedSize(24, 20)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.clicked.connect(self.close)

        h.addWidget(self._title_lbl)
        h.addStretch()
        h.addWidget(self._refresh_btn)
        h.addWidget(close_btn)

        return bar

    def _build_footer(self) -> QFrame:
        f = QFrame()
        f.setObjectName("siFooter")
        f.setFixedHeight(24)

        h = QHBoxLayout(f)
        h.setContentsMargins(10, 0, 10, 0)

        self._status_lbl = QLabel(f"Data source: yfinance  ·  {self._symbol} (US-first)")
        self._status_lbl.setObjectName("siStatus")
        h.addWidget(self._status_lbl)
        h.addStretch()

        hint = QLabel("Data may be delayed 15-20 min")
        hint.setObjectName("siStatus")
        h.addWidget(hint)
        return f

    # ── Data fetch ────────────────────────────────────────────────────────

    def _start_fetch(self):
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText("…")
        self._web.setHtml(_LOADING_HTML)

        if self._fetch_thread and self._fetch_thread.isRunning():
            self._fetch_thread.quit()
            self._fetch_thread.wait(2000)

        self._fetch_thread = _FetchThread(self._symbol, self)
        self._fetch_thread.finished.connect(self._on_data_ready)
        self._fetch_thread.error.connect(self._on_fetch_error)
        self._fetch_thread.start()

    def _on_data_ready(self, data: dict):
        html = _build_info_html(data)
        self._web.setHtml(html)
        ticker_sym = data.get("ticker_sym", self._symbol)
        self._status_lbl.setText(
            f"Data source: yfinance  ·  {ticker_sym}  ·  {data.get('name', '')}"
        )
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("↻")

    def _on_fetch_error(self, msg: str):
        self._web.setHtml(_ERROR_HTML_TPL.format(msg=_esc(msg)))
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("↻")

    def showEvent(self, event):
        super().showEvent(event)
        self._center_on_parent()
        self._apply_webengine_zoom()

    def _apply_webengine_zoom(self):
        if not self._web:
            return
        window_handle = self.window().windowHandle() if self.window() else None
        screen = window_handle.screen() if window_handle else QGuiApplication.primaryScreen()
        dpr = float(screen.devicePixelRatio() if screen else 1.0)
        logical_dpi = float(screen.logicalDotsPerInch() if screen else 96.0)
        dpi_scale = logical_dpi / 96.0 if logical_dpi > 0 else 1.0
        self._web.setZoomFactor(max(0.95, min(1.35, round(dpr * dpi_scale, 2))))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event and event.type() in (QEvent.Type.ScreenChangeInternal, QEvent.Type.DevicePixelRatioChange):
            self._apply_webengine_zoom()

    def _center_on_parent(self):
        if self.parent():
            parent_geo = self.parent().frameGeometry()
            center = parent_geo.center()
            self.move(center - self.rect().center())
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.center() - self.rect().center())

    def mousePressEvent(self, event):
        w = self.childAt(event.pos())
        while w:
            if isinstance(w, (QAbstractButton, QAbstractSpinBox, QLineEdit, QComboBox, QTableWidget)):
                return super().mousePressEvent(event)
            w = w.parentWidget()
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_active and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_active = False
        super().mouseReleaseEvent(event)

    # ── Styles ────────────────────────────────────────────────────────────

    def _apply_styles(self):
        self.setStyleSheet("""
            StockInfoDialog {
                background: #050709;
                border: 1px solid #1a2030;
                border-radius: 2px;
                font-family: 'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', Arial, sans-serif;
            }
            QFrame#siTitleBar {
                background: #050709;
                border-bottom: 1px solid #1a2030;
            }
            QLabel#siTitle {
                color: #d7a45d;
                font-family: 'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', Arial, sans-serif;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1.0px;
                background: transparent;
            }
            QPushButton#siBarBtn {
                background: transparent;
                color: #748396;
                border: 1px solid transparent;
                font-size: 12px;
                border-radius: 2px;
                font-weight: 700;
                padding: 0px;
            }
            QPushButton#siBarBtn:hover {
                background: rgba(120,207,225,0.08);
                color: #a8b4c2;
                border-color: rgba(120,207,225,0.18);
            }
            QPushButton#siBarBtn:disabled {
                color: #3b4758;
                background: transparent;
                border-color: transparent;
            }
            QPushButton#siCloseBtn {
                background: transparent;
                color: #748396;
                border: 1px solid transparent;
                font-size: 11px;
                border-radius: 2px;
                font-weight: 700;
                padding: 0px;
            }
            QPushButton#siCloseBtn:hover {
                background: rgba(255,77,106,0.12);
                color: #ff4d6a;
                border-color: rgba(255,77,106,0.24);
            }
            QFrame#siFooter {
                background: #050709;
                border-top: 1px solid #1a2030;
            }
            QLabel#siStatus {
                color: #748396;
                font-family: 'Inter', 'Aptos', 'Segoe UI Variable', 'Segoe UI', 'Roboto', 'Noto Sans', Arial, sans-serif;
                font-size: 9px;
                font-weight: 600;
                letter-spacing: 0.35px;
                background: transparent;
            }
            QWebEngineView {
                background: #050709;
                border: none;
            }
        """)
    def closeEvent(self, event):
        if self._fetch_thread and self._fetch_thread.isRunning():
            self._fetch_thread.quit()
            self._fetch_thread.wait(2000)
        super().closeEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
# CONVENIENCE FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def show_stock_info(symbol: str, parent=None) -> StockInfoDialog:
    """
    Show (or re-use) a StockInfoDialog for the given symbol.

    Example (from main_window):
        from ibkr.widgets.stock_info_dialog import show_stock_info
        show_stock_info("RELIANCE", parent=self)
    """
    dialog = StockInfoDialog(symbol, parent=parent)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog