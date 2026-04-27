# src/login_setup_config.py
"""
Handles central application configuration, constants, and logging setup.
"""

import logging
import sys
from pathlib import Path
from datetime import datetime

# --- Application Constants ---
APP_NAME = "Qullamaggie"
APP_VERSION = "1.0.0"

# --- UI & Theming Constants ---
# A modern, professional color scheme for the UI
COLORS = {
    "profit": "#26a69a",          # Teal for profit
    "loss": "#ef5350",            # Red for loss
    "neutral": "#6c757d",         # Grey for neutral elements
    "buy": "#29b6f6",             # Light Blue for buy actions
    "sell": "#ffa726",            # Orange for sell actions
    "primary_background": "#1e1e1e", # Dark background
    "secondary_background": "#2c2c2c",# Lighter background for panels
    "primary_text": "#e0e0e0",     # Main text color
    "secondary_text": "#a0a0a0",   # Muted text color
    "highlight": "#00bcd4",         # Cyan for highlights/focus
}

# --- Trading & API Constants ---
# Interval for background data refreshes (e.g., positions, orders)
# A longer interval is suitable for swing trading.
DATA_REFRESH_INTERVAL_MS = 30 * 1000  # 30 seconds


def setup_logging():
    """
    Configures the application-wide logging system.
    Logs are saved to a file and also printed to the console.
    """
    try:
        # All logs will be stored in the .qullamaggie/logs directory
        log_dir = Path.home() / ".qullamaggie" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        # Create a unique, timestamped log file for each session
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = log_dir / f"qullamaggie_{timestamp}.log"

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stdout)
            ]
        )

        # Reduce log noise from third-party libraries
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        logging.getLogger('requests').setLevel(logging.WARNING)
        logging.getLogger('kiteconnect').setLevel(logging.WARNING)

        # Log the application startup
        logger = logging.getLogger(__name__)
        logger.info(f"{APP_NAME} v{APP_VERSION} starting...")
        logger.info(f"Logging to file: {log_file}")

    except Exception as e:
        # Fallback basic logging if setup fails
        logging.basicConfig(level=logging.INFO)
        logging.critical(f"Critical error during logging setup: {e}", exc_info=True)

