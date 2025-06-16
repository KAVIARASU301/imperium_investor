import requests
from bs4 import BeautifulSoup as bs
import json
import logging
import os

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

    # Ensure the directory exists
    os.makedirs(os.path.dirname(TEMP_FILE), exist_ok=True)

    try:
        with requests.Session() as s:
            # Set comprehensive headers
            s.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'same-origin',
                'X-Requested-With': 'XMLHttpRequest'
            })

            # Get dashboard page first to establish session
            dashboard_url = 'https://chartink.com/screener/dashboard'
            logging.info("Getting dashboard page to establish session...")
            r = s.get(dashboard_url, timeout=20)
            r.raise_for_status()

            # Parse CSRF token
            soup = bs(r.text, 'html.parser')
            csrf_meta = soup.find('meta', {'name': 'csrf-token'})

            if not csrf_meta:
                raise Exception("Could not find CSRF token in the dashboard page")

            csrf_token = csrf_meta.get('content')
            if not csrf_token:
                raise Exception("CSRF token is empty")

            logging.info(f"Got CSRF token: {csrf_token[:10]}...")

            # Update session headers with CSRF token
            s.headers.update({
                'X-CSRF-TOKEN': csrf_token,
                'Referer': dashboard_url
            })

            # Prepare payload
            payload = {'scan_clause': scan_clause}
            logging.info(f"Running scan with clause: {scan_clause}")

            # Make the scan request
            process_url = 'https://chartink.com/screener/process'
            res = s.post(process_url, data=payload, timeout=20)
            res.raise_for_status()

            logging.info(f"Scan request completed with status: {res.status_code}")

            # Parse response
            try:
                response_data = res.json()
                logging.info(
                    f"Response contains keys: {list(response_data.keys()) if isinstance(response_data, dict) else 'Not a dict'}")
            except json.JSONDecodeError as e:
                logging.error(f"Failed to parse JSON response: {e}")
                logging.error(f"Response text (first 500 chars): {res.text[:500]}")
                raise Exception(f"Invalid JSON response from Chartink: {e}")

            # Extract symbols from response
            scan_results = response_data.get("data", [])
            if not scan_results:
                logging.warning("No data found in response or empty data array")

            symbols = []
            for item in scan_results:
                if isinstance(item, dict) and 'nsecode' in item:
                    symbols.append(item['nsecode'])
                else:
                    logging.warning(f"Item missing nsecode field: {item}")

            # Save results to file
            with open(TEMP_FILE, 'w') as f:
                json.dump(symbols, f, indent=2)

            logging.info(f"Chartink scan successful. Found {len(symbols)} symbols.")
            if symbols:
                logging.info(f"Sample symbols: {symbols[:5]}")

            return True

    except requests.exceptions.Timeout:
        logging.error("Request timed out. Please try again.")
        # Write empty list on error
        with open(TEMP_FILE, 'w') as f:
            json.dump([], f)
        return False
    except requests.exceptions.ConnectionError:
        logging.error("Connection error. Please check your internet connection.")
        # Write empty list on error
        with open(TEMP_FILE, 'w') as f:
            json.dump([], f)
        return False
    except requests.exceptions.HTTPError as e:
        logging.error(f"HTTP error {e.response.status_code}: {e.response.reason}")
        # Write empty list on error
        with open(TEMP_FILE, 'w') as f:
            json.dump([], f)
        return False
    except Exception as e:
        logging.error(f"An error occurred during the Chartink scan: {e}", exc_info=True)
        # Write empty list on error to prevent using stale data
        with open(TEMP_FILE, 'w') as f:
            json.dump([], f)
        return False


def main():
    """
    Test function to run a scan directly from command line.
    """
    # Example scan clause - replace with your actual scan
    test_scan_clause = "( {57960} ( latest \"close\" > latest \"sma( close , 20 )\" ) )"

    print(f"Testing scan with clause: {test_scan_clause}")
    success = run_scan(test_scan_clause)

    if success:
        print("Scan completed successfully!")
        if os.path.exists(TEMP_FILE):
            with open(TEMP_FILE, 'r') as f:
                symbols = json.load(f)
            print(f"Found {len(symbols)} symbols")
            if symbols:
                print(f"First 10 symbols: {symbols[:10]}")
    else:
        print("Scan failed!")


if __name__ == "__main__":
    main()