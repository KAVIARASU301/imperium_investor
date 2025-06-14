"""
Main source package for the Options Scalper application.

This package contains the core logic, GUI components, and utilities
for the trading application.
"""

# Expose the main application window, which is the primary GUI entry point.
from .gui_components.main_window import ScalperMainWindow

# Expose the core manager classes that handle the application's logic.
from .login_manager import LoginManager
from .position_manager import PositionManager
from .paper_trading_manager import PaperTradingManager
from .token_manager import TokenManager

# Expose key worker and utility classes.
from .market_data_worker import MarketDataWorker
from .api_circuit_breaker import APICircuitBreaker
from .utils.config_manager import ConfigManager
from .utils.trade_logger import TradeLogger
from .utils.pnl_logger import PnlLogger

# Expose the primary setup function for logging.
from .config import setup_logging


# The __all__ variable defines the public API of the 'src' package.
# When a user writes 'from src import *', only these names will be imported.
__all__ = [
    'ScalperMainWindow',
    'LoginManager',
    'PositionManager',
    'PaperTradingManager',
    'TokenManager',
    'MarketDataWorker',
    'APICircuitBreaker',
    'ConfigManager',
    'TradeLogger',
    'PnlLogger',
    'setup_logging',
]