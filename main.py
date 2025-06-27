import sys
import logging

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from kiteconnect import KiteConnect
from login_setup.login_manager import LoginManager
from utils.paper_trading_manager import PaperTradingManager
from login_setup.login_setup_config import setup_logging
from core.main_window import SwingTraderWindow

# Set up global logging for the application.
setup_logging()
logger = logging.getLogger(__name__)


def main():
    """
    The main entry point for the Swing Trader application.
    Initializes the application, handles user login, and launches the main window.
    """
    app = QApplication(sys.argv)
    logger.info("Swing Trader application starting...")

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
        QMessageBox.critical(QWidget(), "Login Failed", "Could not retrieve valid session details. The application will now exit.")
        sys.exit(1)

    # --- Trader and KiteClient Initialization ---
    # A real KiteConnect client is always created for reliable data fetching (e.g., instruments).
    try:
        real_kite_client = KiteConnect(api_key=api_creds['api_key'], access_token=access_token)
    except Exception as e:
        logger.critical(f"Failed to initialize KiteConnect client: {e}", exc_info=True)
        QMessageBox.critical(QWidget(), "API Connection Error", f"Could not connect to the broker's API: {e}")
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

        window.show()
        sys.exit(app.exec())
    except Exception as e:
        logger.critical(f"A critical error occurred while initializing the main window: {e}", exc_info=True)
        QMessageBox.critical(QWidget(), "Application Error", f"A critical error prevented the application from starting: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()