# src/utils/data_fetcher.py

import logging
from datetime import datetime, timedelta, timezone, time
from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
_MARKET_OPEN = time(9, 15)


def _today_ist():
    """Return current calendar date in IST."""
    return datetime.now(tz=_IST).date()


def _effective_to_date(interval: str):
    """
    Return a stable end date for historical queries.

    For higher timeframes (day/week/month), between 00:00 and market open in
    IST there is no completed candle for "today" yet. Querying through today's
    date can therefore surface an incomplete/empty leading bar and visually
    hide the last completed day. Use the previous IST date until market opens.
    """
    now_ist = datetime.now(tz=_IST)
    if interval in {"day", "week", "month"} and now_ist.time() < _MARKET_OPEN:
        return now_ist.date() - timedelta(days=1)
    return now_ist.date()


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
            logger.info(f"Date range: {from_date} to {to_date}, Interval: {interval}")

            records = self.kite_client.historical_data(instrument_token, from_date, to_date, interval)
            logger.info(f"Successfully fetched {len(records)} records for token {instrument_token}.")
            return records

        except Exception as e:
            error_msg = str(e).lower()

            # Provide more specific error messages based on common API errors
            if "interval exceeds max limit" in error_msg:
                if interval in ["15minute", "10minute", "5minute"]:
                    suggested_days = 180
                elif interval in ["3minute", "minute"]:
                    suggested_days = 50
                else:
                    suggested_days = 365

                logger.error(
                    f"Date range too large for {interval} data. Try reducing to {suggested_days} days or less.")

            elif "instrument not found" in error_msg:
                logger.error(f"Invalid instrument token: {instrument_token}")

            elif "rate limit" in error_msg or "too many requests" in error_msg:
                logger.error(f"API rate limit exceeded. Please wait before making more requests.")

            elif "market closed" in error_msg or "holiday" in error_msg:
                logger.warning(f"Market may be closed or it's a holiday.")

            else:
                logger.error(f"Error fetching historical data for token {instrument_token}: {e}")

            return []

    def get_optimal_date_range(self, interval, max_days=None):
        """
        Get optimal date range for a given interval to avoid API limits.

        Args:
            interval (str): The data interval
            max_days (int, optional): Maximum days to fetch (overrides defaults)

        Returns:
            tuple: (from_date, to_date)
        """
        to_date = _effective_to_date(interval)

        if max_days:
            from_date = to_date - timedelta(days=max_days)
        elif interval == "day":
            from_date = to_date - timedelta(days=730)  # 2 years for daily
        elif interval in ["15minute", "10minute", "5minute"]:
            from_date = to_date - timedelta(days=180)  # ~6 months for intraday
        elif interval in ["3minute", "minute"]:
            from_date = to_date - timedelta(days=50)  # ~50 days for minute data
        else:
            from_date = to_date - timedelta(days=365)  # Default 1 year

        return from_date, to_date

    def fetch_historical_data_with_retry(self, instrument_token, from_date, to_date, interval, max_retries=3):
        """
        Fetch historical data with automatic retry and date range adjustment.

        Args:
            instrument_token (int): The instrument token
            from_date (datetime.date): Start date
            to_date (datetime.date): End date
            interval (str): Data interval
            max_retries (int): Maximum retry attempts

        Returns:
            list: Historical data records or empty list on failure
        """
        original_from_date = from_date

        for attempt in range(max_retries):
            try:
                records = self.fetch_historical_data(instrument_token, from_date, to_date, interval)
                if records:  # Success
                    return records

            except Exception as e:
                error_msg = str(e).lower()

                if "interval exceeds max limit" in error_msg and attempt < max_retries - 1:
                    # Reduce date range and retry
                    days_diff = (to_date - from_date).days
                    new_days = max(days_diff // 2, 30)  # Halve the range, minimum 30 days
                    from_date = to_date - timedelta(days=new_days)

                    logger.warning(f"Reducing date range to {new_days} days and retrying...")
                    continue

                else:
                    logger.error(f"Failed after {attempt + 1} attempts: {e}")
                    break

        logger.warning(f"Could not fetch data for token {instrument_token} after {max_retries} attempts")
        return []
