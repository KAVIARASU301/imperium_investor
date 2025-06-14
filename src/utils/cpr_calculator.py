# src/utils/cpr_calculator.py
"""
Utility for calculating Central Pivot Range (CPR) levels.
"""

import logging
from typing import Dict, Optional
import pandas as pd
from datetime import datetime

logger = logging.getLogger(__name__)


class CPRCalculator:
    """Optimized CPR calculation with robust error handling."""

    @staticmethod
    def calculate_cpr_levels(high: float, low: float, close: float) -> Dict[str, float]:
        """Calculates Pivot, BC, and TC from HLC values."""
        pivot = (high + low + close) / 3
        bc = (high + low) / 2
        tc = (pivot - bc) + pivot  # Standard formula: 2 * Pivot - BC

        # Ensure tc is always above bc
        if tc < bc:
            tc, bc = bc, tc

        return {
            'pivot': round(pivot, 2),
            'tc': round(tc, 2),
            'bc': round(bc, 2),
            'range_width': round(abs(tc - bc), 2)
        }

    @staticmethod
    def get_previous_day_cpr(data: pd.DataFrame) -> Optional[Dict[str, float]]:
        """
        Gets CPR levels from the previous trading day's data.
        It correctly identifies the last two unique trading days from the provided data.
        """
        if data.empty or data.index.name != 'date':
            logger.warning("CPR calculation failed: DataFrame is empty or index is not 'date'.")
            return None

        try:
            # Get a series of unique dates from the DataFrame's index
            unique_dates = pd.Series(data.index.date).unique()

            if len(unique_dates) < 2:
                logger.warning("CPR calculation requires at least two days of historical data.")
                return None

            # The last date in the sorted unique list is the most recent (today),
            # and the second to last is the previous trading day.
            previous_trading_date = sorted(unique_dates)[-2]

            prev_day_data = data[data.index.date == previous_trading_date]

            if prev_day_data.empty:
                logger.warning(f"No data found for the identified previous trading day: {previous_trading_date}")
                return None

            # Calculate OHLC from the previous day's data
            prev_high = prev_day_data['high'].max()
            prev_low = prev_day_data['low'].min()
            prev_close = prev_day_data['close'].iloc[-1]

            return CPRCalculator.calculate_cpr_levels(prev_high, prev_low, prev_close)

        except (IndexError, KeyError) as e:
            logger.error(f"Could not calculate CPR due to data issue: {e}")
            return None
        except Exception as e:
            logger.error(f"An unexpected error occurred during CPR calculation: {e}", exc_info=True)
            return None