#!/usr/bin/env python3
"""
Working Finviz Ticker Scraper
Based on successful analysis - extracts tickers from comment section
"""

import re
import requests
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse


def build_finviz_page_url(base_url, start_row):
    """Return Finviz URL for a given 1-indexed row offset (r=1,21,41...)."""
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["r"] = [str(start_row)]
    encoded_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=encoded_query))


def fetch_single_page_tickers(url, headers):
    """Fetch one Finviz page and extract tickers from it."""
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    html_content = response.text

    tickers = extract_comment_tickers(html_content)
    if tickers:
        return tickers

    return extract_fallback_tickers(html_content)


def get_finviz_tickers(url):
    """
    Extract ticker symbols from all pages of a Finviz screener URL.

    Args:
        url (str): Finviz screener URL

    Returns:
        list: List of ticker symbols
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    all_tickers = []
    seen_tickers = set()
    page_size = 20
    max_pages = 200

    try:
        for page_index in range(max_pages):
            start_row = page_index * page_size + 1
            page_url = build_finviz_page_url(url, start_row)
            page_tickers = fetch_single_page_tickers(page_url, headers)

            if not page_tickers:
                break

            new_count = 0
            for row in page_tickers:
                ticker = str(row.get("symbol", "")).upper()
                if ticker and ticker not in seen_tickers:
                    seen_tickers.add(ticker)
                    all_tickers.append(row)
                    new_count += 1

            print(f"Page {page_index + 1}: {len(page_tickers)}")

            # When Finviz has no more rows it may repeat previous page content.
            if new_count == 0:
                break

        return all_tickers

    except Exception:
        return all_tickers


def extract_comment_tickers(html_content):
    """
    Extract tickers from comment section:
    <!-- TS
    TICKER|price|volume
    ...
    TE -->
    """
    pattern = r'<!-- TS\n(.*?)\nTE -->'
    match = re.search(pattern, html_content, re.DOTALL)

    if match:
        comment_data = match.group(1)
        rows = []

        for line in comment_data.strip().split('\n'):
            if '|' not in line:
                continue

            parts = [part.strip() for part in line.split('|')]
            ticker = parts[0].upper() if parts else ""
            if not (ticker and ticker.isalpha() and len(ticker) <= 6):
                continue

            price = _parse_float(parts[1]) if len(parts) > 1 else 0.0
            volume = _parse_int(parts[2]) if len(parts) > 2 else 0
            change_pct = _parse_change_pct(parts)

            rows.append({
                "symbol": ticker,
                "price": price,
                "volume": volume,
                "change_pct": change_pct,
            })

        return rows

    return []


def extract_fallback_tickers(html_content):
    """Fallback method using tab-link elements"""
    pattern = r'<a[^>]*class="tab-link"[^>]*>([A-Z]{1,6})</a>'
    matches = re.findall(pattern, html_content)
    unique = sorted(set(matches))
    return [
        {
            "symbol": symbol,
            "price": 0.0,
            "volume": 0,
            "change_pct": 0.0,
        }
        for symbol in unique
    ]


def _parse_float(value):
    try:
        cleaned = str(value).replace(',', '').strip()
        return float(cleaned) if cleaned else 0.0
    except Exception:
        return 0.0


def _parse_int(value):
    try:
        cleaned = re.sub(r"[^0-9]", "", str(value))
        return int(cleaned) if cleaned else 0
    except Exception:
        return 0


def _parse_change_pct(parts):
    for part in parts:
        token = str(part).strip()
        if '%' not in token:
            continue
        token = token.replace('%', '').replace('+', '').strip()
        try:
            return float(token)
        except Exception:
            continue
    return 0.0


def save_tickers(tickers, filename="tickers.txt"):
    """Save tickers to file"""
    with open(filename, 'w') as f:
        for ticker in sorted(tickers):
            f.write(f"{ticker}\n")
    print(f"💾 Saved {len(tickers)} tickers to {filename}")


def main():
    """Main function"""
    print("🚀 Working Finviz Ticker Scraper")
    print("=" * 40)

    # Get URL from user
    url = input("Enter Finviz screener URL: ").strip()

    if not url:
        print("❌ No URL provided!")
        return

    # Clean URL if needed
    if url.startswith('view-source:'):
        url = url.replace('view-source:', '')

    # Get tickers
    tickers = get_finviz_tickers(url)

    if tickers:
        print(f"\n🎉 SUCCESS! Found {len(tickers)} tickers:")
        print("-" * 50)

        # Display tickers in rows of 5
        for i in range(0, len(tickers), 5):
            row_tickers = tickers[i:i + 5]
            formatted_row = "  ".join(f"{ticker:6}" for ticker in row_tickers)
            print(f"  {formatted_row}")

        # Save option
        save = input(f"\n💾 Save {len(tickers)} tickers to file? (y/n): ").strip().lower()
        if save == 'y':
            filename = input("Filename (default: tickers.txt): ").strip() or "tickers.txt"
            save_tickers(tickers, filename)

        return tickers
    else:
        print("❌ No tickers found!")
        return []


# Quick function for scripts
def quick_scrape(url):
    """Quick function to get tickers without prompts"""
    return get_finviz_tickers(url)


if __name__ == "__main__":
    result = main()

    if result:
        print(f"\n🎯 Final result: {len(result)} tickers extracted successfully!")
    else:
        print("\n💡 Try checking if the URL has any results on the Finviz website")