#!/usr/bin/env python3
"""
Working Finviz Ticker Scraper
Based on successful analysis - extracts tickers from comment section
"""

import requests
import re


def get_finviz_tickers(url):
    """
    Extract ticker symbols from Finviz screener URL

    Args:
        url (str): Finviz screener URL

    Returns:
        list: List of ticker symbols
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    try:
        print(f"🎯 Fetching: {url}")
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        html_content = response.text
        print(f"✅ Got {len(html_content):,} characters")

        # Extract from comment section (most reliable method)
        tickers = extract_comment_tickers(html_content)

        if tickers:
            print(f"✅ Found {len(tickers)} tickers")
            return tickers
        else:
            print("❌ No tickers found in comment section")
            # Fallback to other methods if needed
            return extract_fallback_tickers(html_content)

    except Exception as e:
        print(f"❌ Error: {e}")
        return []


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
        tickers = []

        for line in comment_data.strip().split('\n'):
            if '|' in line:
                ticker = line.split('|')[0].strip()
                if ticker and ticker.isalpha() and len(ticker) <= 6:
                    tickers.append(ticker.upper())

        return tickers

    return []


def extract_fallback_tickers(html_content):
    """Fallback method using tab-link elements"""
    pattern = r'<a[^>]*class="tab-link"[^>]*>([A-Z]{1,6})</a>'
    matches = re.findall(pattern, html_content)
    return list(set(matches))  # Remove duplicates


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