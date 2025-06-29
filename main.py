import sys
import logging
import signal
import atexit

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from PySide6.QtCore import QTimer
from kiteconnect import KiteConnect
from login_setup.login_manager import LoginManager
from kite.utils.paper_trading_manager import PaperTradingManager
from login_setup.login_setup_config import setup_logging
from kite.core.main_window import SwingTraderWindow

# Set up global logging for the application.
setup_logging()
logger = logging.getLogger(__name__)

# Global references for cleanup
app = None
window = None


def setup_signal_handlers():
    """Set up signal handlers for proper cleanup on force quit."""

    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        cleanup_and_exit()

    # Handle SIGINT (Ctrl+C) and SIGTERM
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Register cleanup function to run on normal exit
    atexit.register(cleanup_and_exit)


def cleanup_and_exit():
    """Perform cleanup operations before exit - with safeguards against multiple calls."""
    global app, window

    # Prevent multiple cleanup calls
    if hasattr(cleanup_and_exit, '_cleanup_called'):
        return
    cleanup_and_exit._cleanup_called = True

    try:
        logger.info("Starting application cleanup...")

        if window:
            # Trigger the close event which will handle all thread cleanup
            logger.info("Closing main window...")
            window.close()

        if app:
            # Process any remaining events
            app.processEvents()

        logger.info("Cleanup completed.")

    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
    finally:
        # Ensure we exit
        if app:
            QTimer.singleShot(100, lambda: app.quit())


def main():
    """
    The main entry point for the Swing Trader application.
    Initializes the application, handles user login, and launches the main window.
    """
    global app, window

    app = QApplication(sys.argv)
    logger.info("Swing Trader application starting...")

    # Set up signal handlers for graceful shutdown
    setup_signal_handlers()

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
        QMessageBox.critical(QWidget(), "Login Failed",
                             "Could not retrieve valid session details. The application will now exit.")
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

        # Start the event loop with proper exception handling
        try:
            exit_code = app.exec()
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received, shutting down...")
            cleanup_and_exit()
            exit_code = 0
        except Exception as e:
            logger.error(f"Error in application event loop: {e}")
            exit_code = 1
        finally:
            # Ensure cleanup happens
            cleanup_and_exit()

        sys.exit(exit_code)

    except Exception as e:
        logger.critical(f"A critical error occurred while initializing the main window: {e}", exc_info=True)
        QMessageBox.critical(QWidget(), "Application Error",
                             f"A critical error prevented the application from starting: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()