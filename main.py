# main.py

import sys

from utils.qtwebengine_runtime import configure_qtwebengine_runtime

# Configure QtWebEngine before importing any PySide6 WebEngine modules.
configure_qtwebengine_runtime()


"""
Main entry point for the qullamaggie application.

This script initializes the application, handles user login for different
brokers (Kite for India, Interactive Brokers for America), creates the
appropriate main window, and manages the application lifecycle.
"""

import logging
import signal
import atexit
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon, QSurfaceFormat
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from app_paths import get_app_icon_path

# --- Local Imports ---
from login_setup.dual_mode_login_manager import DualModeLoginManager
from login_setup.broker_factory import BrokerFactory, BrokerClientManager
from login_setup.broker_modes import BrokerMode
from login_setup.login_setup_config import setup_logging
from login_setup.token_manager import EnhancedTokenManager



def configure_qt_startup() -> None:
    """Configure global Qt startup behavior before QApplication is created."""
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    if hasattr(Qt.ApplicationAttribute, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    surface_format = QSurfaceFormat()
    surface_format.setSamples(8)
    QSurfaceFormat.setDefaultFormat(surface_format)


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
        logger.info("🚀 Starting qullamaggie Application...")
        configure_qt_startup()
        self.app = QApplication(sys.argv)
        self.app.setApplicationName("qullamaggie")
        icon_path = get_app_icon_path()
        if icon_path is not None:
            app_icon = QIcon(str(icon_path))
            self.app.setWindowIcon(app_icon)
            self.app.setDesktopFileName("qullamaggie_swing_trader")
            logger.info(f"Application icon loaded from: {icon_path}")
        else:
            logger.warning("Application icon not found; desktop environment may show a fallback icon.")

        self.broker_manager = BrokerClientManager()

        while True:
            try:
                # 1. --- Authentication + Resilient Client Initialization ---
                # Re-prompt login only when we detect a stale/invalid persisted Kite session.
                max_login_attempts = 2
                auth_data = None
                trader = None
                data_client = None
                broker_mode = None

                for attempt in range(1, max_login_attempts + 1):
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

                    try:
                        trader, data_client = self._initialize_clients(auth_data)
                        break
                    except ConnectionError as exc:
                        if self._handle_possible_stale_kite_session(auth_data, attempt, max_login_attempts, exc):
                            continue
                        raise

                if trader is None or data_client is None or broker_mode is None or auth_data is None:
                    raise ConnectionError("Unable to initialize broker clients after retry.")

                self.broker_manager.add_client(broker_mode, trader)

                # 2. --- Main Window Creation ---
                self.window = self._create_main_window(broker_mode, trader, data_client, auth_data)
                if hasattr(self.window, "show_initial_window_state"):
                    self.window.show_initial_window_state()
                else:
                    self.window.show()

                # 3. --- Event Loop ---
                logger.info("Starting application event loop.")
                exit_code = self.app.exec()
                sys.exit(exit_code)

            except Exception as e:
                if self._should_force_relogin(e):
                    logger.warning("Detected invalid/expired Kite session at runtime. Forcing fresh login.")
                    self._clear_persisted_kite_session()
                    self._show_warning(
                        "Your Kite session is no longer valid. "
                        "Please login again to continue."
                    )
                    self._cleanup()
                    continue

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

    def _handle_possible_stale_kite_session(self, auth_data: dict, attempt: int,
                                            max_attempts: int, exception: Exception) -> bool:
        """Handle known stale Kite session failures with a one-time recovery login prompt."""
        broker_mode = auth_data.get('broker_mode')
        token_manager = auth_data.get('token_manager')

        is_kite = broker_mode == BrokerMode.INDIA
        can_clear_session = token_manager is not None and hasattr(token_manager, "clear_broker_session")
        has_remaining_attempt = attempt < max_attempts

        if not (is_kite and can_clear_session and has_remaining_attempt):
            return False

        logger.warning(
            "Detected Kite client init failure likely caused by stale active session. "
            "Clearing persisted Kite session and prompting user to login again."
        )
        token_manager.clear_broker_session(BrokerMode.INDIA)

        self._show_warning(
            "Your Kite session appears to have expired or become invalid. "
            "We've cleared the stored active session automatically. "
            "Please login once again to continue."
        )
        logger.info(f"Recovery prompt shown after client init error: {exception}")
        return True

    def _show_warning(self, message: str):
        """Displays a warning message to guide users through recoverable issues."""
        msg_box = QMessageBox()
        msg_box.setIcon(QMessageBox.Icon.Warning)
        msg_box.setText("Action needed")
        msg_box.setInformativeText(message)
        msg_box.setWindowTitle("Session Refresh Required")
        msg_box.exec()

    @staticmethod
    def _should_force_relogin(exception: Exception) -> bool:
        """Return True when an exception indicates Kite auth/session expiry."""
        error_text = str(exception or "").lower()
        auth_markers = (
            "tokenexception",
            "invalid token",
            "token is invalid",
            "session expired",
            "session has expired",
            "invalid session",
            "access token",
            "authorization",
            "403",
        )
        return any(marker in error_text for marker in auth_markers)

    @staticmethod
    def _clear_persisted_kite_session():
        """Clear stored Kite session so next app cycle starts with fresh login."""
        try:
            EnhancedTokenManager().clear_broker_session(BrokerMode.INDIA)
        except Exception as clear_error:
            logger.warning(f"Failed to clear persisted Kite session: {clear_error}")

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
