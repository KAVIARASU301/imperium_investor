import sys
import logging
from PySide6.QtWidgets import QApplication, QMessageBox
from kiteconnect import KiteConnect
from src import LoginManager, setup_logging, PaperTradingManager
from src.gui_components.swing_trader_window import SwingTraderWindow

setup_logging()
logger = logging.getLogger(__name__)


def main():
    """Main function to run the application."""
    app = QApplication(sys.argv)
    logger.info("Swing Trader starting...")

    login_manager = LoginManager()

    if login_manager.exec() != QMessageBox.DialogCode.Accepted:
        logger.warning("Login process was not completed. Exiting.")
        return

    access_token = login_manager.get_access_token()
    trading_mode = login_manager.get_trading_mode()
    api_creds = login_manager.get_api_creds()

    if not all([access_token, trading_mode, api_creds]):
        QMessageBox.critical(None, "Login Failed", "Could not retrieve session details after login.")
        return

    # Always create a real Kite client instance for fetching data like instruments.
    real_kite_client = KiteConnect(api_key=api_creds['api_key'], access_token=access_token)

    # Determine which object to use for actual trading (live vs. paper)
    trader = None
    if trading_mode == 'live':
        logger.info("Starting in LIVE TRADING mode.")
        trader = real_kite_client
    else:
        logger.info("Starting in PAPER TRADING mode.")
        trader = PaperTradingManager()

    try:
        # Pass both the trader and the real client to the main window.
        window = SwingTraderWindow(
            trader=trader,
            real_kite_client=real_kite_client,
            api_key=api_creds['api_key'],
            access_token=access_token
        )
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        logger.critical(f"Failed to initialize main window: {e}", exc_info=True)
        QMessageBox.critical(None, "Application Error", f"A critical error occurred: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
