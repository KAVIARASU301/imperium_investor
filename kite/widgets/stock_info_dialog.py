# kite/widgets/stock_info_dialog.py
"""
StockInfoDialog — Bloomberg-style stock fundamentals panel for the Kite terminal.

Fetches data from yfinance using NSE symbol format (symbol.NS) and displays:
  • Company name, sector, industry, description
  • Key valuation metrics: PE, PB, EV/EBITDA, Market Cap
  • Profitability: EPS, ROE, ROA, Profit Margin
  • Earnings dates (next & previous)
  • Dividend info
  • 52-week range
  • Analyst target price & recommendation

Usage:
    from kite.widgets.stock_info_dialog import StockInfoDialog

    dialog = StockInfoDialog("RELIANCE", parent=self)
    dialog.show()

    # Or use the convenience function:
    show_stock_info("RELIANCE", parent=main_window)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, QPoint, QObject, Signal, QThread
from PySide6.QtGui import QMouseEvent, QCursor
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

            # Try NSE first, fall back to BSE
            ticker_sym = f"{self._symbol}.NS"
            ticker = yf.Ticker(ticker_sym)
            info = ticker.info or {}

            # If NSE returns minimal data, try BSE
            if not info.get("longName") and not info.get("shortName"):
                ticker_sym = f"{self._symbol}.BO"
                ticker = yf.Ticker(ticker_sym)
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
            return f"₹{v/1e12:.2f}T"
        if v >= 1e9:
            return f"₹{v/1e9:.2f}B"
        if v >= 1e7:
            return f"₹{v/1e7:.2f} Cr"
        if v >= 1e5:
            return f"₹{v/1e5:.2f} L"
        return f"₹{v:,.0f}"

    @staticmethod
    def _fmt_crore_readable(val) -> str:
        """Format INR values into crore-based wording for easier reading."""
        try:
            v = float(val)
        except (TypeError, ValueError):
            return "—"

        crore = v / 1e7
        if crore >= 1e5:
            lakh_crore = crore / 1e5
            return f"₹{lakh_crore:.2f} lakh crore"
        if crore >= 1e3:
            return f"₹{crore:,.0f} crore"
        if crore >= 1e2:
            return f"₹{crore:,.0f} crore"
        if crore >= 1:
            return f"₹{crore:,.2f} crore"

        return f"₹{v:,.0f}"

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
            target = f"₹{target}"

        # Revenue / earnings growth
        rev_growth  = fp(g("revenueGrowth"))
        earn_growth = fp(g("earningsGrowth"))

        # Beta
        beta = fn(g("beta"), 2)

        # Dividend
        div_yield = fp(g("dividendYield"))
        div_rate  = fn(g("dividendRate"), 2)
        if div_rate != "—":
            div_rate = f"₹{div_rate}"
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
            "exchange":        g("exchange") or "NSE",
            "currency":        g("currency") or "INR",
            "country":         g("country") or "India",
            "website":         g("website") or "",
            "employees":       f"{int(g('fullTimeEmployees')):,}" if g("fullTimeEmployees") else "—",
            "description":     description,

            # Valuation
            "market_cap":      self._fmt_crore_readable(g("marketCap")),
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
# HTML RENDERER  (Bloomberg-dark aesthetic)
# ─────────────────────────────────────────────────────────────────────────────

_LOADING_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #0a0d12;
    color: #7a94b0;
    font-family: 'Segoe UI', Arial, sans-serif;
    display: flex; align-items: center; justify-content: center;
    height: 100vh; flex-direction: column; gap: 16px;
  }
  .spinner {
    width: 36px; height: 36px;
    border: 3px solid #1a2840;
    border-top-color: #00d4ff;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  p { font-size: 13px; letter-spacing: 1px; }
</style></head><body>
<div class="spinner"></div>
<p>FETCHING DATA…</p>
</body></html>"""


_ERROR_HTML_TPL = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #0a0d12; color: #ff4d6a;
    font-family: 'Segoe UI', Arial, sans-serif;
    display: flex; align-items: center; justify-content: center;
    height: 100vh; flex-direction: column; gap: 12px; padding: 24px;
    text-align: center;
  }}
  .icon {{ font-size: 32px; }}
  h2 {{ color: #ff4d6a; font-size: 14px; letter-spacing: 1px; }}
  p {{ font-size: 12px; color: #7a94b0; line-height: 1.5; max-width: 360px; }}
</style></head><body>
<div class="icon">⚠</div>
<h2>DATA UNAVAILABLE</h2>
<p>{msg}</p>
</body></html>"""


def _build_info_html(data: dict) -> str:
    """Render a Bloomberg-dark info page from parsed data dict."""

    def row(label: str, value: str, color: str = "#e8f0ff") -> str:
        if value in ("—", "", None):
            value = '<span style="color:#7a94b0">—</span>'
        return (
            f'<tr>'
            f'<td class="lbl">{label}</td>'
            f'<td class="val" style="color:{color}">{value}</td>'
            f'</tr>'
        )

    def section(title: str, rows_html: str) -> str:
        return (
            f'<div class="section">'
            f'<div class="sec-title">{title}</div>'
            f'<table class="grid">{rows_html}</table>'
            f'</div>'
        )

    d = data

    # Recommendation color
    rec_color = "#7a94b0"
    rec = d.get("recommendation", "—").lower()
    if "buy" in rec or "strong" in rec:
        rec_color = "#00d4a8"
    elif "sell" in rec:
        rec_color = "#ff4d6a"
    elif "hold" in rec or "neutral" in rec:
        rec_color = "#f59e0b"

    # Description (truncate)
    desc = d.get("description", "")
    desc_html = f'<p class="desc">{desc}</p>' if desc else ""

    # Website link
    website = d.get("website", "")
    web_html = (
        f'<a href="{website}" class="web-link" target="_blank">{website}</a>'
        if website else ""
    )

    valuation_rows = (
        row("Market Cap",   d["market_cap"]) +
        row("P/E (TTM)",    d["pe_ratio"],   "#4a9eff" if d["pe_ratio"] != "—" else "#2a3a50") +
        row("Forward P/E",  d["forward_pe"]) +
        row("P/B Ratio",    d["pb_ratio"]) +
        row("EV/EBITDA",    d["ev_ebitda"]) +
        row("P/S Ratio",    d["ps_ratio"]) +
        row("PEG Ratio",    d["peg"]) +
        row("EV",           d["ev"])
    )

    per_share_rows = (
        row("EPS (TTM)",    d["eps_ttm"]) +
        row("EPS (Fwd)",    d["eps_fwd"]) +
        row("Book Value",   d["book_value"])
    )

    profitability_rows = (
        row("ROE",              d["roe"]) +
        row("ROA",              d["roa"]) +
        row("Net Margin",       d["profit_margin"]) +
        row("Gross Margin",     d["gross_margin"]) +
        row("Operating Margin", d["operating_margin"]) +
        row("Rev Growth (YoY)", d["rev_growth"]) +
        row("EPS Growth (YoY)", d["earn_growth"])
    )

    market_rows = (
        row("52-Week Range", d["range52"]) +
        row("Beta",          d["beta"]) +
        row("Avg Volume",    d["avg_volume"]) +
        row("Float",         d["float_shares"]) +
        row("Employees",     d["employees"])
    )

    dividend_rows = (
        row("Dividend Yield",  d["div_yield"]) +
        row("Dividend Rate",   d["div_rate"]) +
        row("Ex-Dividend Date",d["ex_div"])
    )

    analyst_rows = (
        row("Price Target",    d["analyst_target"]) +
        row("Recommendation",  d["recommendation"], rec_color) +
        row("Analyst Count",   d["num_analysts"]) +
        row("Next Earnings",   d["earnings_date"],
            "#f59e0b" if d["earnings_date"] != "—" else "#2a3a50") +
        row("Fiscal Year End", d["fiscal_year_end"])
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{
    --bg0: #070a0f;
    --bg1: #0a0d12;
    --bg2: #0f1318;
    --bg3: #141920;
    --border: #1a2030;
    --t0: #e8f0ff;
    --t1: #a8bcd4;
    --t2: #8fa7c3;
    --cyan: #00d4ff;
    --teal: #00d4a8;
    --amber: #f59e0b;
  }}
  html, body {{ height: 100%; }}
  body {{
    background: var(--bg1);
    color: var(--t0);
    font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    font-size: 12px;
    overflow-x: hidden;
    line-height: 1.45;
  }}

  /* ── Hero header ── */
  .hero {{
    background: linear-gradient(135deg, #0a1020 0%, #0d1628 100%);
    border-bottom: 1px solid var(--border);
    padding: 18px 22px 14px;
  }}
  .hero-row {{ display: flex; align-items: flex-start; gap: 12px; flex-wrap: wrap; }}
  .ticker-badge {{
    background: rgba(0,212,255,0.10);
    border: 1px solid rgba(0,212,255,0.25);
    color: var(--cyan);
    font-family: 'JetBrains Mono', 'Consolas', monospace;
    font-size: 11px; font-weight: 700;
    letter-spacing: 2px;
    padding: 3px 10px;
    border-radius: 2px;
    align-self: center;
  }}
  .company-name {{
    font-size: 17px; font-weight: 700;
    color: #e8f0ff; letter-spacing: 0.2px;
  }}
  .meta-pills {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 6px; }}
  .pill {{
    background: var(--bg3);
    border: 1px solid var(--border);
    color: #b5c8de;
    font-size: 10px; font-weight: 600;
    letter-spacing: 0.8px;
    padding: 2px 8px; border-radius: 2px;
  }}
  .pill.highlight {{ color: var(--amber); border-color: rgba(245,158,11,0.3); }}
  .web-link {{
    color: #b5c8de; font-size: 10px;
    text-decoration: none; letter-spacing: 0.3px;
  }}
  .web-link:hover {{ color: var(--cyan); }}

  /* ── Description ── */
  .desc {{
    font-size: 13px;
    color: #c4d4e8;
    line-height: 1.7;
    margin: 12px 22px 10px;
    border-left: 2px solid #253147;
    padding: 4px 0 4px 12px;
    max-width: 96ch;
  }}

  /* ── Grid layout ── */
  .panels {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    background: var(--border);
    margin-top: 8px;
    padding: 8px;
  }}

  .section {{
    background: #0b1017;
    border: 1px solid #1b2638;
    padding: 10px 12px;
    border-radius: 3px;
  }}
  .sec-title {{
    color: #9db2ca;
    font-size: 9px; font-weight: 800;
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 6px;
    padding-bottom: 4px;
    border-bottom: 1px solid var(--border);
  }}

  /* ── Data table ── */
  .grid {{ width: 100%; border-collapse: collapse; }}
  .grid tr {{ border-bottom: 1px solid rgba(26,37,53,0.4); }}
  .grid tr:last-child {{ border-bottom: none; }}
  .lbl {{
    color: #b5c8de;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.2px;
    padding: 3px 0;
    width: 52%;
    vertical-align: middle;
  }}
  .val {{
    color: var(--t0);
    font-family: 'JetBrains Mono', 'Consolas', monospace;
    font-size: 11px;
    font-weight: 700;
    text-align: right;
    padding: 3px 0;
    vertical-align: middle;
    white-space: nowrap;
  }}

  @media (max-width: 940px) {{
    .panels {{ grid-template-columns: 1fr; }}
  }}

  /* ── Scrollbar ── */
  ::-webkit-scrollbar {{ width: 4px; }}
  ::-webkit-scrollbar-track {{ background: var(--bg1); }}
  ::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 2px; }}
</style>
</head><body>

<div class="hero">
  <div class="hero-row">
    <span class="ticker-badge">{d['symbol']}</span>
    <div>
      <div class="company-name">{d['name']}</div>
      <div class="meta-pills">
        <span class="pill highlight">{d['sector']}</span>
        <span class="pill">{d['industry']}</span>
        <span class="pill">{d['exchange']}</span>
        <span class="pill">{d['country']}</span>
        {web_html}
      </div>
    </div>
  </div>
</div>

{desc_html}

<div class="panels">
  {section("VALUATION", valuation_rows)}
  {section("PER SHARE", per_share_rows)}
  {section("PROFITABILITY & GROWTH", profitability_rows)}
  {section("MARKET DATA", market_rows)}
  {section("DIVIDENDS &amp; EVENTS", dividend_rows)}
  {section("ANALYST COVERAGE", analyst_rows)}
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
        self.setMinimumSize(1000, 680)
        self.resize(1100, 720)

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
        root.addWidget(self._web, 1)

        root.addWidget(self._build_footer())

    def _build_title_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("siTitleBar")
        bar.setFixedHeight(36)

        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 0, 8, 0)
        h.setSpacing(8)

        # Icon + label
        self._title_lbl = QLabel(f"STOCK INFO  ·  {self._symbol}")
        self._title_lbl.setObjectName("siTitle")

        # Refresh button
        self._refresh_btn = QPushButton("↻")
        self._refresh_btn.setObjectName("siBarBtn")
        self._refresh_btn.setFixedSize(26, 26)
        self._refresh_btn.setToolTip("Refresh data from yfinance")
        self._refresh_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._refresh_btn.clicked.connect(self._start_fetch)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("siCloseBtn")
        close_btn.setFixedSize(26, 26)
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
        f.setFixedHeight(40)

        h = QHBoxLayout(f)
        h.setContentsMargins(16, 0, 16, 0)

        self._status_lbl = QLabel(f"Data source: yfinance  ·  {self._symbol}.NS / {self._symbol}.BO")
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
        self._web.setHtml(_ERROR_HTML_TPL.format(msg=msg))
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("↻")

    def showEvent(self, event):
        super().showEvent(event)
        self._center_on_parent()

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
                background: #0a0d12;
                border: 1px solid #1a2030;
                border-radius: 2px;
            }
            QFrame#siTitleBar {
                background: #070a0f;
                border-bottom: 1px solid #1a2030;
            }
            QLabel#siTitle {
                color: #00d4ff;
                font-family: 'JetBrains Mono', 'Consolas', monospace;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 2px;
                background: transparent;
            }
            QPushButton#siBarBtn {
                background: transparent;
                color: #5a7090;
                border: none;
                font-size: 16px;
                border-radius: 2px;
            }
            QPushButton#siBarBtn:hover {
                background: rgba(255,255,255,0.07);
                color: #a8bcd4;
            }
            QPushButton#siBarBtn:disabled {
                color: #6f86a3;
            }
            QPushButton#siCloseBtn {
                background: transparent;
                color: #5a7090;
                border: none;
                font-size: 12px;
                border-radius: 2px;
            }
            QPushButton#siCloseBtn:hover {
                background: rgba(255,77,106,0.15);
                color: #ff4d6a;
            }
            QFrame#siFooter {
                background: #070a0f;
                border-top: 1px solid #1a2030;
            }
            QLabel#siStatus {
                color: #5a7090;
                font-size: 10px;
                letter-spacing: 0.3px;
                background: transparent;
            }
            QWebEngineView {
                background: #0a0d12;
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
        from kite.widgets.stock_info_dialog import show_stock_info
        show_stock_info("RELIANCE", parent=self)
    """
    dialog = StockInfoDialog(symbol, parent=parent)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
