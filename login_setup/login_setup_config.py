# src/login_setup_config.py
"""
Handles central application configuration, constants, and logging setup.
"""

import faulthandler
import logging
import sys
import time
from collections import OrderedDict
from datetime import datetime

from app_paths import get_project_log_dir

_CURRENT_LOG_FILE = None
_FATAL_LOG_HANDLE = None

# --- Application Constants ---
APP_NAME = "qullamaggie"
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


class DuplicateLogFilter(logging.Filter):
    """Suppress repeated log records that otherwise spam IBKR session logs.

    IBKR can emit identical position/portfolio/order snapshots in short bursts,
    especially when multiple event sources reconcile the same broker cache.  The
    application log is more useful when exact duplicates are collapsed while
    still allowing later state changes through.
    """

    def __init__(self, window_seconds: float = 10.0, max_entries: int = 1000):
        super().__init__()
        self.window_seconds = max(0.0, float(window_seconds))
        self.max_entries = max(1, int(max_entries))
        self._recent: OrderedDict[tuple, float] = OrderedDict()

    def filter(self, record: logging.LogRecord) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        while self._recent:
            _key, seen_at = next(iter(self._recent.items()))
            if seen_at >= cutoff:
                break
            self._recent.popitem(last=False)

        key = (record.name, record.levelno, record.getMessage())
        seen_at = self._recent.get(key)
        if seen_at is not None and now - seen_at <= self.window_seconds:
            self._recent.move_to_end(key)
            self._recent[key] = now
            return False

        self._recent[key] = now
        if len(self._recent) > self.max_entries:
            self._recent.popitem(last=False)
        return True


def _configure_noisy_loggers() -> None:
    """Keep third-party broker/library chatter out of normal INFO logs."""
    # IBKR wrapper INFO messages include full position/updatePortfolio snapshots
    # and often arrive twice in the same event burst.  Application code records
    # concise position summaries, so only warnings/errors from ib_insync are kept.
    logging.getLogger('ib_insync.wrapper').setLevel(logging.WARNING)
    logging.getLogger('ib_insync.ib').setLevel(logging.WARNING)

    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('kiteconnect').setLevel(logging.WARNING)


def setup_logging():
    """
    Configures the application-wide logging system.
    Logs are saved to a file and also printed to the console.
    """
    try:
        # Store logs inside the project so they are easy to inspect from this workspace.
        log_dir = get_project_log_dir()

        # Create a unique, timestamped log file for each session
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = log_dir / f"qullamaggie_{timestamp}.log"

        global _CURRENT_LOG_FILE, _FATAL_LOG_HANDLE
        _CURRENT_LOG_FILE = log_file
        if _FATAL_LOG_HANDLE is not None:
            try:
                _FATAL_LOG_HANDLE.close()
            except Exception:
                pass
            _FATAL_LOG_HANDLE = None
        try:
            _FATAL_LOG_HANDLE = open(log_file, "a", buffering=1, encoding="utf-8")
            faulthandler.enable(file=_FATAL_LOG_HANDLE, all_threads=True)
        except Exception:
            _FATAL_LOG_HANDLE = None

        handlers = [
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ]
        for handler in handlers:
            handler.addFilter(DuplicateLogFilter())

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
            handlers=handlers,
            force=True,
        )

        # Reduce log noise from third-party broker/API libraries.
        _configure_noisy_loggers()

        # Log the application startup
        logger = logging.getLogger(__name__)
        logger.info(f"{APP_NAME} v{APP_VERSION} starting...")
        logger.info(f"Logging to file: {log_file}")
        if _FATAL_LOG_HANDLE is not None:
            logger.info("Fatal crash diagnostics enabled for all threads.")

    except Exception as e:
        # Fallback basic logging if setup fails
        logging.basicConfig(level=logging.INFO)
        logging.critical(f"Critical error during logging setup: {e}", exc_info=True)

