# src/utils/data_fetcher.py

import logging
from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)

class DataFetcher:
    """A utility class for fetching market data."""

    def __init__(self, kite_client: KiteConnect):
        """
        Initializes the DataFetcher.

        Args:
            kite_client (KiteConnect): An authenticated KiteConnect client instance.
        """
        self.kite_client = kite_client

    def fetch_historical_data(self, instrument_token, from_date, to_date, interval):
        """
        Fetches historical data for a given instrument token.

        Args:
            instrument_token (int): The instrument token of the stock.
            from_date (datetime.date): The start date for the data.
            to_date (datetime.date): The end date for the data.
            interval (str): The interval for the data (e.g., 'day', 'minute').

        Returns:
            list: A list of historical data records, or an empty list on error.
        """
        try:
            logger.info(f"Fetching historical data for token {instrument_token}...")
            records = self.kite_client.historical_data(instrument_token, from_date, to_date, interval)
            logger.info(f"Successfully fetched {len(records)} records for token {instrument_token}.")
            return records
        except Exception as e:
            logger.error(f"Error fetching historical data for token {instrument_token}: {e}")
            return []