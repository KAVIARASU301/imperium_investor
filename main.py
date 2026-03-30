# main.py
"""
Main entry point for the Swing Trader application.

This script initializes the application, handles user login for different
brokers (Kite for India, Interactive Brokers for America), creates the
appropriate main window, and manages the application lifecycle.
"""

import sys
import logging
import signal
import atexit
from typing import Optional

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from PySide6.QtCore import QTimer

# --- Local Imports ---
from login_setup.dual_mode_login_manager import DualModeLoginManager
from login_setup.broker_factory import BrokerFactory, BrokerClientManager
from login_setup.broker_modes import BrokerMode
from login_setup.login_setup_config import setup_logging

# --- Setup Logging ---
# It's crucial to set up logging at the very beginning.
setup_logging()
logger = logging.getLogger(__name__)


class Application:
    """Encapsulates the entire trading application lifecycle."""

    def __init__(self):
        self.app: Optional[QApplication] = None
        self.window: Optional[QWidget] = None
        self.broker_manager: Optional[BrokerClientManager] = None
        self._setup_signal_handlers()

    def run(self):
        """Main entry point to run the application."""
        logger.info("🚀 Starting Swing Trader Application...")
        self.app = QApplication(sys.argv)
        self.app.setApplicationName("Swing Trader")
        self.app.setWindowIcon(QIcon("assets/qullamaggie_icon.png"))

        self.broker_manager = BrokerClientManager()

        try:
            # 1. --- Authentication ---
            login_manager = DualModeLoginManager()
            if not login_manager.exec():
                logger.info("Login cancelled by user. Exiting.")
                sys.exit(0)

            auth_data = login_manager.get_authentication_data()
            if not auth_data:
                self._show_critical_error("Authentication failed. Exiting.")
                sys.exit(1)

            broker_mode = auth_data.get('broker_mode')
            logger.info(f"Authenticated for broker: {broker_mode.value}")

            # 2. --- Client Initialization ---
            trader, data_client = self._initialize_clients(auth_data)
            self.broker_manager.add_client(broker_mode, trader)

            # 3. --- Main Window Creation ---
            self.window = self._create_main_window(broker_mode, trader, data_client, auth_data)
            self.window.show()

            # 4. --- Event Loop ---
            logger.info("Starting application event loop.")
            sys.exit(self.app.exec())

        except Exception as e:
            logger.critical(f"An unhandled error occurred: {e}", exc_info=True)
            self._show_critical_error(str(e))
            sys.exit(1)

    def _initialize_clients(self, auth_data: dict):
        """Initializes and validates broker clients."""
        logger.info("Initializing trading and data clients...")
        try:
            trader = BrokerFactory.create_client(
                broker_mode=auth_data['broker_mode'],
                trading_mode=auth_data['trading_mode'],
                authentication_data=auth_data
            )
            data_client = BrokerFactory.create_data_client(
                broker_mode=auth_data['broker_mode'],
                authentication_data=auth_data
            )

            if not trader.is_connected() or not data_client.is_connected():
                raise ConnectionError("Failed to connect one or more clients.")

            logger.info("Clients initialized successfully.")
            return trader, data_client
        except Exception as e:
            logger.error(f"Client initialization failed: {e}", exc_info=True)
            raise

    def _create_main_window(self, broker_mode, trader, data_client, auth_data):
        """Creates the appropriate main window for the selected broker."""
        logger.info(f"Creating main window for {broker_mode.value}...")
        try:
            MainWindowClass = BrokerFactory.load_broker_main_window(broker_mode)

            if broker_mode == BrokerMode.INDIA:
                window = MainWindowClass(
                    trader=trader,
                    real_kite_client=data_client,
                    api_key=auth_data.get('api_key'),
                    access_token=auth_data.get('access_token')
                )
            elif broker_mode == BrokerMode.AMERICA:
                window = MainWindowClass(
                    trader=trader,
                    real_ibkr_client=data_client,
                    client_id=auth_data.get('client_id'),
                    ib_client=auth_data.get('ib_client')
                )
            else:
                raise NotImplementedError(f"No main window for broker: {broker_mode}")

            logger.info("Main window created successfully.")
            return window
        except Exception as e:
            logger.error(f"Failed to create main window: {e}", exc_info=True)
            raise

    def _setup_signal_handlers(self):
        """Ensures graceful shutdown on signals."""
        atexit.register(self._cleanup)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.warning(f"Signal {signum} received, initiating shutdown.")
        self._cleanup()
        QApplication.quit()

    def _cleanup(self):
        """Graceful cleanup of application resources."""
        logger.info("Shutting down and cleaning up resources...")
        if self.broker_manager:
            self.broker_manager.disconnect_all()
        if self.window:
            self.window.close()
        logger.info("Cleanup complete.")

    def _show_critical_error(self, message: str):
        """Displays a critical error message before exiting."""
        msg_box = QMessageBox()
        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.setText("A critical error occurred")
        msg_box.setInformativeText(message)
        msg_box.setWindowTitle("Application Error")
        msg_box.exec()


if __name__ == "__main__":
    # To run diagnostics: python main.py --diagnostics
    if len(sys.argv) > 1 and sys.argv[1] == '--diagnostics':
        # (Add your diagnostics function here if needed)
        print("Running diagnostics...")
        sys.exit(0)

    # Run the main application
    main_app = Application()
    main_app.run()