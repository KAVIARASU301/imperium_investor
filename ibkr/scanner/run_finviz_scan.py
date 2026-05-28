#!/usr/bin/env python3
"""
Finviz screener scraper with standalone debug output.

What this returns per row:
    symbol, company, sector, industry, country, market_cap, pe,
    price, change_pct, change/change_raw, volume, and _source.

Run examples:
    python run_finviz_scan.py
    python run_finviz_scan.py --debug
    python run_finviz_scan.py --url "https://finviz.com/screener?..." --debug --csv finviz_debug.csv
    FINVIZ_DEBUG=1 python run_finviz_scan.py
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from html import unescape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests


Row = Dict[str, Any]


# Finviz overview view (v=111) visible table order when headers are not exposed
# cleanly by the page HTML.
FINVIZ_OVERVIEW_HEADERS = [
    "No.", "Ticker", "Company", "Sector", "Industry", "Country",
    "Market Cap", "P/E", "Price", "Change", "Volume",
]

CSV_FIELDS = [
    "symbol", "company", "sector", "industry", "country", "market_cap", "pe",
    "price", "change_pct", "change", "change_raw", "volume", "_source",
]


# ─────────────────────────────────────────────────────────────────────────────
# URL / request helpers
# ─────────────────────────────────────────────────────────────────────────────

def build_finviz_page_url(base_url: str, start_row: int) -> str:
    """Return Finviz URL for a given 1-indexed row offset (r=1,21,41...)."""
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["r"] = [str(start_row)]
    encoded_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=encoded_query))


def _env_debug_enabled() -> bool:
    return str(os.environ.get("FINVIZ_DEBUG", "")).strip().lower() in {"1", "true", "yes", "y", "on"}


def _debug(debug: bool, message: str) -> None:
    if debug:
        print(message)


def _save_debug_html(html_content: str, save_html_dir: Optional[str], page_number: int) -> None:
    if not save_html_dir:
        return
    out_dir = Path(save_html_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"finviz_page_{page_number:03d}.html"
    out_file.write_text(html_content, encoding="utf-8")
    print(f"🧪 DEBUG: saved raw HTML → {out_file}")


# ─────────────────────────────────────────────────────────────────────────────
# Main scraper
# ─────────────────────────────────────────────────────────────────────────────

def fetch_single_page_tickers(
    url: str,
    headers: Dict[str, str],
    *,
    debug: bool = False,
    page_number: int = 1,
    save_html_dir: Optional[str] = None,
) -> List[Row]:
    """Fetch one Finviz page and extract rows from it."""
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    html_content = response.text
    _debug(debug, f"\n🧪 DEBUG Page {page_number}: {url}")
    _debug(debug, f"🧪 DEBUG HTTP: {response.status_code}, bytes={len(html_content):,}")
    _save_debug_html(html_content, save_html_dir, page_number)

    rows = extract_table_rows(html_content, debug=debug)
    if rows:
        for row in rows:
            row.setdefault("_source", "table")
        _debug(debug, f"🧪 DEBUG extractor: rendered table rows={len(rows)}")
        return rows

    rows = extract_comment_tickers(html_content, debug=debug)
    if rows:
        for row in rows:
            row.setdefault("_source", "comment")
        _debug(debug, f"🧪 DEBUG extractor: Finviz TS comment rows={len(rows)}")
        return rows

    rows = extract_fallback_tickers(html_content, debug=debug)
    for row in rows:
        row.setdefault("_source", "fallback")
    _debug(debug, f"🧪 DEBUG extractor: fallback tab-link rows={len(rows)}")
    return rows


def get_finviz_tickers(
    url: str,
    *,
    debug: bool = False,
    save_html_dir: Optional[str] = None,
    max_pages: int = 200,
) -> List[Row]:
    """
    Extract rows from all pages of a Finviz screener URL.

    Returns a list of dictionaries, not plain strings. Use row["symbol"] for
    only the ticker symbol.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }

    all_rows: List[Row] = []
    seen_tickers = set()
    page_size = 20

    try:
        for page_index in range(max_pages):
            start_row = page_index * page_size + 1
            page_number = page_index + 1
            page_url = build_finviz_page_url(url, start_row)
            page_rows = fetch_single_page_tickers(
                page_url,
                headers,
                debug=debug,
                page_number=page_number,
                save_html_dir=save_html_dir,
            )

            if not page_rows:
                _debug(debug, f"🧪 DEBUG Page {page_number}: no rows; stopping")
                break

            new_count = 0
            for row in page_rows:
                ticker = _symbol_from_row(row)
                if ticker and ticker not in seen_tickers:
                    row["symbol"] = ticker
                    seen_tickers.add(ticker)
                    all_rows.append(row)
                    new_count += 1

            print(f"Page {page_number}: {len(page_rows)} rows, {new_count} new")

            # When Finviz has no more rows it may repeat previous page content.
            if new_count == 0:
                _debug(debug, f"🧪 DEBUG Page {page_number}: duplicate page detected; stopping")
                break

        return all_rows

    except Exception as exc:
        if debug:
            print(f"\n❌ DEBUG scraper stopped by exception: {type(exc).__name__}: {exc}")
            raise
        print(f"⚠️ Scraper stopped early: {exc}")
        return all_rows


# ─────────────────────────────────────────────────────────────────────────────
# Extractors
# ─────────────────────────────────────────────────────────────────────────────

def extract_table_rows(html_content: str, *, debug: bool = False) -> List[Row]:
    """
    Extract Symbol/Price/Change from the rendered Finviz screener table.

    This is preferred because the visible table carries explicit columns such as
    Ticker, Price, Change, Volume, Company, Sector, etc.
    """
    tr_list = re.findall(r"<tr\b[^>]*>(.*?)</tr>", html_content, re.DOTALL | re.IGNORECASE)
    if not tr_list:
        _debug(debug, "🧪 DEBUG table: no <tr> rows found")
        return []

    headers: List[str] = []
    rows: List[Row] = []
    header_candidates: List[List[str]] = []

    for row_html in tr_list:
        cell_html = re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", row_html, re.DOTALL | re.IGNORECASE)
        if not cell_html:
            continue

        values = [_clean_html_cell(c) for c in cell_html]
        values = [v for v in values if v != ""]
        if not values:
            continue

        normalized_values = [_norm_header(v) for v in values]
        looks_like_header = (
            "ticker" in normalized_values
            and ("price" in normalized_values or "change" in normalized_values)
        )

        if looks_like_header:
            headers = values
            header_candidates.append(headers)
            continue

        # Some Finviz responses are easier to parse by row shape than by header,
        # especially when the visible header is not inside the same table block.
        if not headers and _looks_like_overview_data_row(values):
            headers = FINVIZ_OVERVIEW_HEADERS

        if not headers:
            continue

        parsed = _row_from_table_values(headers, values)
        if parsed:
            rows.append(parsed)

    if debug:
        print(f"🧪 DEBUG table: tr_count={len(tr_list)}, header_candidates={len(header_candidates)}, parsed_rows={len(rows)}")
        if header_candidates:
            print(f"🧪 DEBUG table headers: {header_candidates[-1]}")
        elif headers:
            print(f"🧪 DEBUG table headers inferred: {headers}")
        if rows:
            print("🧪 DEBUG table first parsed row:")
            print(json.dumps(rows[0], ensure_ascii=False, indent=2))

    return rows


def extract_comment_tickers(html_content: str, *, debug: bool = False) -> List[Row]:
    """
    Extract rows from Finviz TS comment section when the rendered table is not available.

    Comment rows are less stable than table rows. We still scan all tokens and
    keep the raw token list in debug so you can see where Change is coming from.
    """
    pattern = r"<!--\s*TS\s*\n(.*?)\n\s*TE\s*-->"
    match = re.search(pattern, html_content, re.DOTALL | re.IGNORECASE)
    if not match:
        _debug(debug, "🧪 DEBUG comment: no TS/TE comment block found")
        return []

    comment_data = match.group(1)
    rows: List[Row] = []

    for line in comment_data.strip().split("\n"):
        if "|" not in line:
            continue

        parts = [part.strip() for part in line.split("|")]
        ticker = parts[0].upper() if parts else ""
        if not _valid_symbol(ticker):
            continue

        price = _parse_float(parts[1]) if len(parts) > 1 else 0.0
        volume = _parse_int(parts[2]) if len(parts) > 2 else 0
        change_pct, change_raw = _parse_change_pct_from_tokens(parts)

        rows.append({
            "symbol": ticker,
            "company": "",
            "sector": "",
            "industry": "",
            "country": "",
            "market_cap": "",
            "pe": "",
            "price": price,
            "volume": volume,
            "change_pct": change_pct,
            "change": change_pct,
            "change_raw": change_raw,
            "_raw_tokens": parts if debug else None,
        })

    for row in rows:
        if row.get("_raw_tokens") is None:
            row.pop("_raw_tokens", None)

    if debug:
        print(f"🧪 DEBUG comment: parsed_rows={len(rows)}")
        if rows:
            print("🧪 DEBUG comment first parsed row:")
            print(json.dumps(rows[0], ensure_ascii=False, indent=2))

    return rows


def extract_fallback_tickers(html_content: str, *, debug: bool = False) -> List[Row]:
    """Last resort: extract only ticker symbols from tab-link elements."""
    pattern = r'<a[^>]*class=["\'][^"\']*tab-link[^"\']*["\'][^>]*>([A-Z]{1,6})</a>'
    matches = re.findall(pattern, html_content, re.IGNORECASE)
    unique = sorted(set(m.upper() for m in matches if _valid_symbol(m.upper())))
    if debug:
        print(f"🧪 DEBUG fallback: tab-link matches={len(matches)}, unique_symbols={len(unique)}")
    return [
        {
            "symbol": symbol,
            "company": "",
            "sector": "",
            "industry": "",
            "country": "",
            "market_cap": "",
            "pe": "",
            "price": 0.0,
            "volume": 0,
            "change_pct": 0.0,
            "change": 0.0,
            "change_raw": "",
        }
        for symbol in unique
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Parsing helpers
# ─────────────────────────────────────────────────────────────────────────────

def _row_from_table_values(headers: List[str], values: List[str]) -> Optional[Row]:
    # If values include extra cells, keep only the header-aligned portion.
    # If values are shorter, zip safely and use fallback inference below.
    header_map = {_norm_header(h): i for i, h in enumerate(headers)}

    def get(*names: str) -> str:
        for name in names:
            idx = header_map.get(_norm_header(name))
            if idx is not None and idx < len(values):
                return values[idx]
        return ""

    symbol = get("Ticker", "Symbol")
    if not symbol:
        symbol = _infer_symbol_from_values(values)
    symbol = symbol.strip().upper()
    if not _valid_symbol(symbol):
        return None

    price_raw = get("Price")
    change_raw = get("Change")
    volume_raw = get("Volume")

    # If header inference started mid-page and alignment is off, fall back to
    # the common v=111 overview positions: No, Ticker, Company, Sector,
    # Industry, Country, Market Cap, P/E, Price, Change, Volume.
    if (not price_raw or not change_raw) and _looks_like_overview_data_row(values):
        aligned = dict(zip(FINVIZ_OVERVIEW_HEADERS, values[:len(FINVIZ_OVERVIEW_HEADERS)]))
        price_raw = price_raw or aligned.get("Price", "")
        change_raw = change_raw or aligned.get("Change", "")
        volume_raw = volume_raw or aligned.get("Volume", "")
        company = aligned.get("Company", "")
        sector = aligned.get("Sector", "")
        industry = aligned.get("Industry", "")
        country = aligned.get("Country", "")
        market_cap = aligned.get("Market Cap", "")
        pe = aligned.get("P/E", "")
    else:
        company = get("Company")
        sector = get("Sector")
        industry = get("Industry")
        country = get("Country")
        market_cap = get("Market Cap")
        pe = get("P/E", "PE")

    change_pct = _parse_change_token(change_raw)

    return {
        "symbol": symbol,
        "company": company,
        "sector": sector,
        "industry": industry,
        "country": country,
        "market_cap": market_cap,
        "pe": pe,
        "price": _parse_float(price_raw),
        "volume": _parse_int(volume_raw),
        "change_pct": change_pct,
        "change": change_pct,          # compatibility for scanner tables using `change`
        "change_raw": change_raw,      # visible Finviz text, e.g. "-3.42%"
        "Change": change_raw,          # compatibility with older code using title-case key
    }


def _looks_like_overview_data_row(values: List[str]) -> bool:
    if len(values) < 10:
        return False
    first = values[0].strip()
    second = values[1].strip().upper() if len(values) > 1 else ""
    if not first.isdigit() or not _valid_symbol(second):
        return False
    # v=111 row usually has Price at index 8, Change at index 9, Volume at index 10.
    if len(values) > 9 and "%" in values[9]:
        return True
    return bool(len(values) > 8 and _parse_float(values[8]) > 0)


def _infer_symbol_from_values(values: List[str]) -> str:
    if len(values) > 1 and values[0].strip().isdigit() and _valid_symbol(values[1].strip().upper()):
        return values[1].strip().upper()
    for value in values[:5]:
        token = value.strip().upper()
        if _valid_symbol(token):
            return token
    return ""


def _symbol_from_row(row: Any) -> str:
    if isinstance(row, dict):
        return str(row.get("symbol") or row.get("Ticker") or row.get("ticker") or "").strip().upper()
    return str(row).strip().upper()


def _valid_symbol(symbol: str) -> bool:
    symbol = str(symbol).strip().upper()
    return bool(re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol))


def _parse_float(value: Any) -> float:
    try:
        cleaned = str(value).replace(",", "").replace("%", "").strip()
        return float(cleaned) if cleaned else 0.0
    except Exception:
        return 0.0


def _parse_int(value: Any) -> int:
    try:
        cleaned = re.sub(r"[^0-9]", "", str(value))
        return int(cleaned) if cleaned else 0
    except Exception:
        return 0


def _parse_change_pct_from_tokens(parts: Iterable[str]) -> tuple[float, str]:
    tokens = [str(p).strip() for p in parts]

    # Best case: token carries an explicit percent sign.
    for token in tokens:
        if "%" not in token:
            continue
        value = _parse_change_token(token)
        return value, token

    # Fallback: scan from the right for a signed numeric token that looks like
    # daily percentage change. Skip known symbol/price/volume slots.
    for idx in range(len(tokens) - 1, -1, -1):
        if idx in (0, 1, 2):
            continue
        token = tokens[idx].replace(",", "")
        if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", token):
            continue
        value = _parse_float(token)
        if -100.0 <= value <= 100.0:
            return value, tokens[idx]

    return 0.0, ""


def _parse_change_token(value: Any) -> float:
    token = str(value).strip()
    if not token:
        return 0.0
    token = token.replace("%", "").replace("+", "").replace(",", "").strip()
    try:
        return float(token)
    except Exception:
        return 0.0


def _norm_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).strip().lower())


def _clean_html_cell(value: Any) -> str:
    text = str(value)
    text = re.sub(r"<script\b.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style\b.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Output helpers
# ─────────────────────────────────────────────────────────────────────────────

def print_compact_symbol_grid(rows: List[Row], per_row: int = 5) -> None:
    symbols = [_symbol_from_row(row) for row in rows]
    for i in range(0, len(symbols), per_row):
        row_symbols = symbols[i:i + per_row]
        formatted_row = "  ".join(f"{symbol:8}" for symbol in row_symbols)
        print(f"  {formatted_row}")


def print_debug_rows(rows: List[Row], *, limit: int = 0) -> None:
    """Print every parsed row as JSON so the scraped data is visible."""
    print("\n🧪 DEBUG parsed row data")
    print("-" * 80)
    shown_rows = rows if limit <= 0 else rows[:limit]
    for idx, row in enumerate(shown_rows, start=1):
        clean_row = {k: v for k, v in row.items() if v is not None}
        print(f"#{idx:03d} " + json.dumps(clean_row, ensure_ascii=False, sort_keys=True))
    if limit > 0 and len(rows) > limit:
        print(f"... {len(rows) - limit} more rows hidden by --debug-limit={limit}")


def print_column_health(rows: List[Row]) -> None:
    if not rows:
        return
    total = len(rows)
    sources = {}
    non_zero_change = 0
    raw_change = 0
    non_zero_price = 0
    for row in rows:
        sources[row.get("_source", "unknown")] = sources.get(row.get("_source", "unknown"), 0) + 1
        if abs(float(row.get("change_pct", 0.0) or 0.0)) > 0:
            non_zero_change += 1
        if str(row.get("change_raw", "")).strip():
            raw_change += 1
        if float(row.get("price", 0.0) or 0.0) > 0:
            non_zero_price += 1

    print("\n🧪 DEBUG column health")
    print("-" * 80)
    print(f"Rows: {total}")
    print(f"Sources: {sources}")
    print(f"Price parsed: {non_zero_price}/{total}")
    print(f"Change raw present: {raw_change}/{total}")
    print(f"Change non-zero: {non_zero_change}/{total}")
    if raw_change == 0:
        print("⚠️ Change is missing before parsing. The scraper is not reading the visible table; it likely fell back to comments/fallback.")
    elif non_zero_change == 0:
        print("⚠️ Change text exists, but parsed value is 0 for every row. Check change_raw values in the JSON rows above.")


def save_tickers(rows: List[Row], filename: str = "tickers.txt") -> None:
    """Save only symbols to a text file."""
    symbols = sorted({_symbol_from_row(row) for row in rows if _symbol_from_row(row)})
    with open(filename, "w", encoding="utf-8") as f:
        for symbol in symbols:
            f.write(f"{symbol}\n")
    print(f"💾 Saved {len(symbols)} symbols to {filename}")


def save_rows_csv(rows: List[Row], filename: str = "finviz_debug_rows.csv") -> None:
    """Save full scraped row data to CSV for debugging scanner-table issues."""
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"💾 Saved full scraped rows to {filename}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI / entry points
# ─────────────────────────────────────────────────────────────────────────────

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Finviz screener scraper with debug output")
    parser.add_argument("--url", help="Finviz screener URL. If omitted, the script asks interactively.")
    parser.add_argument("--debug", action="store_true", help="Print extractor source, parsed rows, and column health.")
    parser.add_argument("--debug-limit", type=int, default=0, help="Limit JSON debug rows. 0 means print all rows.")
    parser.add_argument("--csv", default="", help="Save full scraped rows to CSV path.")
    parser.add_argument("--save-html-dir", default=os.environ.get("FINVIZ_SAVE_HTML_DIR", ""), help="Optional folder to save raw HTML pages.")
    parser.add_argument("--max-pages", type=int, default=200, help="Maximum Finviz pages to scan.")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> List[Row]:
    """Main function."""
    args = parse_args(argv)
    debug = bool(args.debug or _env_debug_enabled())

    print("🚀 Finviz Ticker Scraper")
    print("=" * 40)

    url = (args.url or input("Enter Finviz screener URL: ")).strip()
    if not url:
        print("❌ No URL provided!")
        return []

    if url.startswith("view-source:"):
        url = url.replace("view-source:", "", 1)

    rows = get_finviz_tickers(
        url,
        debug=debug,
        save_html_dir=args.save_html_dir or None,
        max_pages=max(1, int(args.max_pages or 1)),
    )

    if not rows:
        print("❌ No tickers found!")
        print("💡 Try checking if the URL has any results on the Finviz website.")
        return []

    print(f"\n🎉 SUCCESS! Found {len(rows)} rows:")
    print("-" * 50)
    print_compact_symbol_grid(rows)

    if debug:
        print_debug_rows(rows, limit=int(args.debug_limit or 0))
        print_column_health(rows)

    if args.csv:
        save_rows_csv(rows, args.csv)

    save = input(f"\n💾 Save {len(rows)} symbols to file? (y/n): ").strip().lower()
    if save == "y":
        filename = input("Filename (default: tickers.txt): ").strip() or "tickers.txt"
        save_tickers(rows, filename)

    return rows


# Quick function for scripts/imports
def quick_scrape(url: str, *, debug: bool = False) -> List[Row]:
    """Quick function to get full Finviz rows without prompts."""
    return get_finviz_tickers(url, debug=debug)


def quick_symbols(url: str, *, debug: bool = False) -> List[str]:
    """Quick function to get only ticker symbols."""
    return [_symbol_from_row(row) for row in get_finviz_tickers(url, debug=debug)]


if __name__ == "__main__":
    result = main(sys.argv[1:])
    if result:
        print(f"\n🎯 Final result: {len(result)} rows extracted successfully!")
    else:
        print("\n💡 No rows extracted.")