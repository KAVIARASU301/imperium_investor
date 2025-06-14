import requests
from bs4 import BeautifulSoup as bs
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# The temporary file where results will be saved.
TEMP_FILE = "user_data/temp_scan_results.json"


def run_scan(scan_clause: str):
    """
    Runs a Chartink scan and saves the 'nsecode' symbols to a temporary file.
    This function is designed to be imported and called from another script.
    """
    if not scan_clause:
        logging.error("No scan clause provided to run_scan.")
        return False

    try:
        with requests.session() as s:
            s.headers[
                'User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'

            url = 'https://chartink.com/screener/process'
            r = s.get('https://chartink.com/screener/dashboard', timeout=15)
            r.raise_for_status()

            soup = bs(r.text, 'lxml')
            csrf_token = soup.find('meta', {'name': 'csrf-token'})['content']
            s.headers['x-csrf-token'] = csrf_token

            payload = {'scan_clause': scan_clause}
            res = s.post(url, data=payload, timeout=15)
            res.raise_for_status()

            scan_results = res.json().get("data", [])
            symbols = [item['nsecode'] for item in scan_results if 'nsecode' in item]

            with open(TEMP_FILE, 'w') as f:
                json.dump(symbols, f)

            logging.info(f"Chartink scan successful. Found {len(symbols)} symbols.")
            return True

    except Exception as e:
        logging.error(f"An error occurred during the Chartink scan: {e}", exc_info=True)
        # Write an empty list to the file on error to prevent using stale data
        with open(TEMP_FILE, 'w') as f:
            json.dump([], f)
        return False