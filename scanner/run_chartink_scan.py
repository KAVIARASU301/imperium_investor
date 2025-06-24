import requests
import json
import logging
import os
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# The temporary file where results will be saved.
TEMP_FILE = "user_data/temp_scan_results.json"

# Premium user configuration
PREMIUM_USER_ID = "570267"


def run_direct_xhr_scan(scan_clause: str):
    """
    Direct XHR request to Chartink process endpoint - mimics browser behavior exactly
    """
    if not scan_clause:
        logging.error("No scan clause provided to run_scan.")
        return False

    # Ensure the directory exists
    os.makedirs(os.path.dirname(TEMP_FILE), exist_ok=True)

    try:
        with requests.Session() as s:
            # Step 1: Get the main page to establish session and cookies
            logging.info("Step 1: Getting main chartink page to establish session...")
            main_page = s.get('https://chartink.com/', timeout=30)
            main_page.raise_for_status()

            # Step 2: Get the screener page to get CSRF token
            logging.info("Step 2: Getting screener page for CSRF token...")
            screener_page = s.get('https://chartink.com/screener', timeout=30)
            screener_page.raise_for_status()

            # Extract CSRF token from the page
            csrf_token = None
            if 'csrf-token' in screener_page.text:
                import re
                # Look for meta tag with csrf-token
                csrf_match = re.search(r'<meta name="csrf-token" content="([^"]*)"', screener_page.text)
                if csrf_match:
                    csrf_token = csrf_match.group(1)
                else:
                    # Alternative: look for it in JavaScript
                    csrf_match = re.search(r'csrf-token["\']?\s*:\s*["\']([^"\']+)["\']', screener_page.text)
                    if csrf_match:
                        csrf_token = csrf_match.group(1)

            if not csrf_token:
                logging.error("Could not extract CSRF token from screener page")
                return False

            logging.info(f"Step 3: Extracted CSRF token: {csrf_token[:10]}...")

            # Step 3: Prepare the direct XHR request (exactly like browser does)
            xhr_headers = {
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Accept-Language': 'en-US,en;q=0.9',
                'Cache-Control': 'no-cache',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'Origin': 'https://chartink.com',
                'Pragma': 'no-cache',
                'Referer': 'https://chartink.com/screener',
                'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                'Sec-Ch-Ua-Mobile': '?0',
                'Sec-Ch-Ua-Platform': '"Linux"',
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'same-origin',
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'X-Csrf-Token': csrf_token,
                'X-Requested-With': 'XMLHttpRequest'
            }

            # Update session headers
            s.headers.update(xhr_headers)

            # Step 4: Prepare the exact payload that browser sends
            payload = {
                'scan_clause': scan_clause
            }

            logging.info(f"Step 4: Making direct XHR request to process endpoint...")
            logging.info(f"Scan clause: {scan_clause}")

            # Step 5: Make the direct POST request to process endpoint
            process_url = 'https://chartink.com/screener/process'

            response = s.post(process_url, data=payload, timeout=60)
            response.raise_for_status()

            logging.info(f"XHR request completed with status: {response.status_code}")

            # Step 6: Parse the JSON response
            try:
                response_data = response.json()
                logging.info(
                    f"Response keys: {list(response_data.keys()) if isinstance(response_data, dict) else 'Not a dict'}")

                # Log response structure for debugging
                if isinstance(response_data, dict):
                    if 'data' in response_data:
                        logging.info(f"Data array length: {len(response_data['data'])}")
                    if 'message' in response_data:
                        logging.info(f"Response message: {response_data['message']}")

            except json.JSONDecodeError as e:
                logging.error(f"Failed to parse JSON response: {e}")
                logging.error(f"Response text (first 1000 chars): {response.text[:1000]}")
                return False

            # Step 7: Extract symbols from response
            scan_results = response_data.get("data", [])
            if not scan_results:
                logging.warning("No data found in response or empty data array")
                # Check if there's an error message
                if 'message' in response_data:
                    logging.warning(f"Server message: {response_data['message']}")

            symbols = []
            for item in scan_results:
                if isinstance(item, dict) and 'nsecode' in item:
                    symbols.append(item['nsecode'])
                else:
                    logging.debug(f"Item structure: {item}")

            # Step 8: Save results to file
            result_data = {
                'symbols': symbols,
                'count': len(symbols),
                'scan_clause': scan_clause,
                'timestamp': time.time(),
                'full_response': response_data  # Save full response for debugging
            }

            with open(TEMP_FILE, 'w') as f:
                json.dump(result_data, f, indent=2)

            logging.info(f"Direct XHR scan successful. Found {len(symbols)} symbols.")
            if symbols:
                logging.info(f"Sample symbols: {symbols[:10]}")

            return True

    except requests.exceptions.Timeout:
        logging.error("Request timed out")
        with open(TEMP_FILE, 'w') as f:
            json.dump({'symbols': [], 'error': 'timeout'}, f)
        return False
    except requests.exceptions.ConnectionError:
        logging.error("Connection error")
        with open(TEMP_FILE, 'w') as f:
            json.dump({'symbols': [], 'error': 'connection_error'}, f)
        return False
    except requests.exceptions.HTTPError as e:
        logging.error(f"HTTP error {e.response.status_code}: {e.response.reason}")
        if e.response.status_code == 429:
            logging.error("Rate limited - wait before making another request")
        with open(TEMP_FILE, 'w') as f:
            json.dump({'symbols': [], 'error': f'http_{e.response.status_code}'}, f)
        return False
    except Exception as e:
        logging.error(f"Unexpected error: {e}", exc_info=True)
        with open(TEMP_FILE, 'w') as f:
            json.dump({'symbols': [], 'error': str(e)}, f)
        return False


def debug_response_structure(response_data):
    """Helper function to understand the response structure"""
    if isinstance(response_data, dict):
        print("Response structure:")
        for key, value in response_data.items():
            if isinstance(value, list):
                print(f"  {key}: list with {len(value)} items")
                if value and len(value) > 0:
                    print(f"    Sample item: {value[0]}")
            else:
                print(f"  {key}: {type(value).__name__} = {value}")
    else:
        print(f"Response is {type(response_data).__name__}: {response_data}")


def test_volatility_contraction_scan():
    """Test the volatility contraction scan you provided"""

    # Your volatility contraction scan clause
    volatility_scan = "( {cash} ( ( {33489} ( latest high - latest low < 1 day ago high - 1 day ago low and latest high - latest low < 2 days ago high - 2 days ago low and latest high - latest low < 3 days ago high - 3 days ago low and latest high - latest low < 4 days ago high - 4 days ago low and latest high - latest low < 5 days ago high - 5 days ago low and latest high - latest low < 6 days ago high - 6 days ago low ) ) ) )"

    print("Testing Volatility Contraction (7 days) scan...")
    print(f"Premium User ID: {PREMIUM_USER_ID}")
    print(f"Scan clause: {volatility_scan}")
    print("-" * 80)

    success = run_direct_xhr_scan(volatility_scan)

    if success:
        print("\n✅ Scan completed successfully!")
        if os.path.exists(TEMP_FILE):
            with open(TEMP_FILE, 'r') as f:
                result_data = json.load(f)

            symbols = result_data.get('symbols', [])
            print(f"Found {len(symbols)} symbols with volatility contraction")

            if symbols:
                print(f"First 10 symbols: {symbols[:10]}")
                print(f"All symbols: {symbols}")
            else:
                print("No symbols found matching the criteria")

            # Debug info
            if 'full_response' in result_data:
                print("\nResponse structure analysis:")
                debug_response_structure(result_data['full_response'])
    else:
        print("\n❌ Scan failed!")


def simple_test_scan():
    """Simple test with a basic scan"""
    # Simple scan: stocks above SMA 20
    simple_scan = "( {57960} ( latest \"close\" > latest \"sma( close , 20 )\" ) )"

    print("Testing simple scan first...")
    success = run_direct_xhr_scan(simple_scan)

    if success:
        print("✅ Simple scan works!")
        return True
    else:
        print("❌ Simple scan failed!")
        return False


def main():
    """Main function to test the direct XHR approach"""
    print("=== Direct XHR Chartink Scanner Test ===")
    print(f"Target file: {TEMP_FILE}")
    print()

    # First test with simple scan
    if simple_test_scan():
        print("\n" + "=" * 50)
        print("Now testing your volatility contraction scan...")
        test_volatility_contraction_scan()
    else:
        print("Simple scan failed, check connection and try again")


if __name__ == "__main__":
    main()