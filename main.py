import sys
import logging
from PySide6.QtWidgets import QApplication, QMessageBox
from kiteconnect import KiteConnect
from utils.login_manager import LoginManager
from utils.paper_trading_manager import PaperTradingManager
from utils.login_setup_config import setup_logging
from widgets.swing_trader_window import SwingTraderWindow
# Set up global logging for the application.
# Note: The logger name inside setup_logging might need to be changed from 'Options Scalper'
# We will address this when we refactor the logging setup file.
setup_logging()
logger = logging.getLogger(__name__)


def initialize_performance_dashboard_integration(swing_trader_window):
    """
    Call this function during application startup to ensure proper integration.

    Args:
        swing_trader_window: Instance of SwingTraderWindow
    """
    try:
        # Set up performance tracking
        if hasattr(swing_trader_window, '_setup_performance_tracking'):
            swing_trader_window._setup_performance_tracking()

        # Set up keyboard shortcuts
        if hasattr(swing_trader_window, '_setup_keyboard_shortcuts'):
            swing_trader_window._setup_keyboard_shortcuts()

        logger.info("Performance dashboard integration initialized successfully")

    except Exception as e:
        logger.error(f"Failed to initialize performance dashboard integration: {e}")

def initialize_order_history_integration(swing_trader_window):
    """
    Call this function during application startup to ensure proper integration.

    Args:
        swing_trader_window: Instance of SwingTraderWindow
    """
    try:
        # Set up trade logger reference in paper trading manager
        if hasattr(swing_trader_window, 'paper_trader') and swing_trader_window.paper_trader:
            swing_trader_window.paper_trader.set_trade_logger(swing_trader_window.trade_logger)
            swing_trader_window.paper_trader.set_main_window(swing_trader_window)

        # Set up keyboard shortcuts
        if hasattr(swing_trader_window, '_setup_keyboard_shortcuts'):
            swing_trader_window._setup_keyboard_shortcuts()

        logger.info("Order history integration initialized successfully")

    except Exception as e:
        logger.error(f"Failed to initialize order history integration: {e}")


def main():
    """
    The main entry point for the Swing Trader application.
    Initializes the application, handles user login, and launches the main window.
    """
    app = QApplication(sys.argv)
    logger.info("Swing Trader application starting...")

    # --- Login Process ---
    # The LoginManager handles API credentials and user session.
    login_manager = LoginManager()
    if login_manager.exec() != QMessageBox.DialogCode.Accepted:
        logger.warning("Login process was not completed by the user. Exiting application.")
        sys.exit(0)

    # Retrieve session details from the login manager
    access_token = login_manager.get_access_token()
    trading_mode = login_manager.get_trading_mode()
    api_creds = login_manager.get_api_creds()

    if not all([access_token, trading_mode, api_creds]):
        QMessageBox.critical(None, "Login Failed", "Could not retrieve valid session details. The application will now exit.")
        sys.exit(1)

    # --- Trader and KiteClient Initialization ---
    # A real KiteConnect client is always created for reliable data fetching (e.g., instruments).
    try:
        real_kite_client = KiteConnect(api_key=api_creds['api_key'], access_token=access_token)
    except Exception as e:
        logger.critical(f"Failed to initialize KiteConnect client: {e}", exc_info=True)
        QMessageBox.critical(None, "API Connection Error", f"Could not connect to the broker's API: {e}")
        sys.exit(1)

    # The 'trader' object is used for all order placement and position management.
    # It can be a live client or a paper trading simulator.
    if trading_mode == 'live':
        logger.info("Initializing in LIVE TRADING mode.")
        trader = real_kite_client
    else:
        logger.info("Initializing in PAPER TRADING mode.")
        trader = PaperTradingManager()

    # --- Main Application Window ---
    # Launch the main GUI of the application.
    try:
        window = SwingTraderWindow(
            trader=trader,
            real_kite_client=real_kite_client,
            api_key=api_creds['api_key'],
            access_token=access_token
        )
        # Initialize order history integration
        initialize_order_history_integration(window)
        # Initialize performance dashboard integration
        initialize_performance_dashboard_integration(window)
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        logger.critical(f"A critical error occurred while initializing the main window: {e}", exc_info=True)
        QMessageBox.critical(None, "Application Error", f"A critical error prevented the application from starting: {e}")
        sys.exit(1)



if __name__ == "__main__":
    main()
