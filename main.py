# main.py
"""
Enhanced main application supporting dual-mode operation (India/America).
Supports both Kite (India) and Interactive Brokers (America) with unified interface.
"""

import sys
import logging
import signal
import atexit
from typing import Optional, Union

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from PySide6.QtCore import QTimer

# Import the new dual-mode login system
from login_setup.dual_mode_login_manager import DualModeLoginManager
from login_setup.broker_factory import BrokerFactory, BrokerClientManager
from login_setup.broker_modes import BrokerMode, TradingMode, get_broker_config
from login_setup.login_setup_config import setup_logging

# Import the broker client interface
from login_setup.broker_factory import BrokerClientInterface

logger = logging.getLogger(__name__)

# Global references for cleanup
app: Optional[QApplication] = None
window = None
broker_manager: Optional[BrokerClientManager] = None


def setup_signal_handlers():
    """Set up signal handlers for proper cleanup on force quit"""

    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        cleanup_and_exit()

    # Handle SIGINT (Ctrl+C) and SIGTERM
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Register cleanup function to run on normal exit
    atexit.register(cleanup_and_exit)


def cleanup_and_exit():
    """Perform cleanup operations before exit"""
    global app, window, broker_manager

    # Prevent multiple cleanup calls
    if hasattr(cleanup_and_exit, '_cleanup_called'):
        return
    cleanup_and_exit._cleanup_called = True

    try:
        logger.info("Starting application cleanup...")

        # Cleanup broker connections
        if broker_manager:
            logger.info("Disconnecting broker clients...")
            broker_manager.disconnect_all()

        # Close main window
        if window:
            logger.info("Closing main window...")
            window.close()

        # Process remaining events
        if app:
            app.processEvents()

        logger.info("Cleanup completed.")

    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
    finally:
        # Ensure we exit
        if app:
            QTimer.singleShot(100, lambda: app.quit())


def create_broker_specific_window(broker_mode: BrokerMode,
                                  trader: BrokerClientInterface,
                                  data_client: BrokerClientInterface,
                                  authentication_data: dict):
    """
    Create the appropriate main window based on broker mode

    Args:
        broker_mode: Selected broker mode
        trader: Trading client (paper or live)
        data_client: Data client (always live for real market data)
        authentication_data: Authentication data from login

    Returns:
        Main window instance for the selected broker
    """
    try:
        # Load the broker-specific main window class
        MainWindowClass = BrokerFactory.load_broker_main_window(broker_mode)

        if broker_mode == BrokerMode.INDIA:
            # Kite-specific window initialization
            window = MainWindowClass(
                trader=trader,
                real_kite_client=data_client,
                api_key=authentication_data.get('api_key'),
                access_token=authentication_data.get('access_token')
            )

        elif broker_mode == BrokerMode.AMERICA:
            # IBKR-specific window initialization
            window = MainWindowClass(
                trader=trader,
                real_ibkr_client=data_client,
                client_id=authentication_data.get('client_id'),
                ib_client=authentication_data.get('ib_client')
            )

        else:
            raise ValueError(f"Unsupported broker mode: {broker_mode}")

        return window

    except Exception as e:
        logger.error(f"Failed to create {broker_mode.value} window: {e}")
        raise


def initialize_broker_clients(authentication_data: dict) -> tuple:
    """
    Initialize trading and data clients based on authentication data

    Returns:
        tuple: (trader_client, data_client)
    """
    broker_mode = authentication_data['broker_mode']
    trading_mode = authentication_data['trading_mode']

    logger.info(f"Initializing {broker_mode.value} clients in {trading_mode.value} mode")

    # Create trading client (paper or live based on mode)
    trader = BrokerFactory.create_client(
        broker_mode=broker_mode,
        trading_mode=trading_mode,
        authentication_data=authentication_data
    )

    # Create data client (always live for real market data)
    data_client = BrokerFactory.create_data_client(
        broker_mode=broker_mode,
        authentication_data=authentication_data
    )

    # Validate connections
    if not trader.is_connected():
        raise ConnectionError(f"Failed to connect {broker_mode.value} trading client")

    if not data_client.is_connected():
        raise ConnectionError(f"Failed to connect {broker_mode.value} data client")

    return trader, data_client


def main():
    """
    Enhanced main entry point supporting dual-mode operation
    """
    global app, window, broker_manager

    # Set up logging first
    setup_logging()
    logger.info("=== Swing Trader Application Starting ===")

    # Create QApplication
    app = QApplication(sys.argv)
    app.setApplicationName("Swing Trader Pro")
    app.setApplicationVersion("2.0.0")

    # Set up signal handlers for graceful shutdown
    setup_signal_handlers()

    # Initialize broker manager
    broker_manager = BrokerClientManager()

    try:
        # === AUTHENTICATION PHASE ===
        logger.info("Starting dual-mode login process...")

        login_manager = DualModeLoginManager()
        if login_manager.exec() != QMessageBox.DialogCode.Accepted:
            logger.info("Login cancelled by user. Exiting application.")
            sys.exit(0)

        # Get authentication results
        authentication_data = login_manager.get_authentication_data()
        broker_mode = authentication_data['broker_mode']
        trading_mode = authentication_data['trading_mode']

        broker_config = get_broker_config(broker_mode)
        logger.info(f"Authentication successful:")
        logger.info(f"  Broker: {broker_config.display_name}")
        logger.info(f"  Mode: {trading_mode.value.title()}")
        logger.info(f"  Currency: {broker_config.currency}")

        # Validate authentication data
        if not BrokerFactory.validate_authentication_data(broker_mode, trading_mode, authentication_data):
            QMessageBox.critical(
                None,
                "Authentication Error",
                "Invalid authentication data. Please try logging in again."
            )
            sys.exit(1)

        # === CLIENT INITIALIZATION PHASE ===
        logger.info("Initializing broker clients...")

        try:
            trader, data_client = initialize_broker_clients(authentication_data)

            # Add clients to manager for tracking
            broker_manager.add_client(broker_mode, trader)

            logger.info("Broker clients initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize broker clients: {e}")
            QMessageBox.critical(
                None,
                "Connection Error",
                f"Failed to initialize {broker_config.display_name} clients:\n{str(e)}"
            )
            sys.exit(1)

        # === WINDOW CREATION PHASE ===
        logger.info("Creating main application window...")

        try:
            window = create_broker_specific_window(
                broker_mode=broker_mode,
                trader=trader,
                data_client=data_client,
                authentication_data=authentication_data
            )

            # Show the window
            window.show()

            logger.info(f"Main window created for {broker_config.display_name}")

        except Exception as e:
            logger.error(f"Failed to create main window: {e}")
            QMessageBox.critical(
                None,
                "Application Error",
                f"Failed to create main window:\n{str(e)}"
            )
            sys.exit(1)

        # === APPLICATION EXECUTION PHASE ===
        logger.info("Starting application event loop...")

        # Display startup success message
        QTimer.singleShot(1000, lambda: window.statusBar().showMessage(
            f"Connected to {broker_config.display_name} ({trading_mode.value.title()} mode)",
            5000
        ))

        try:
            # Start the event loop
            exit_code = app.exec()

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received, shutting down...")
            exit_code = 0

        except Exception as e:
            logger.error(f"Error in application event loop: {e}", exc_info=True)
            exit_code = 1

        finally:
            # Cleanup will be handled by signal handler
            cleanup_and_exit()

        logger.info(f"Application exiting with code: {exit_code}")
        sys.exit(exit_code)

    except Exception as e:
        logger.critical(f"Critical error in main application: {e}", exc_info=True)

        # Show error to user if possible
        try:
            QMessageBox.critical(
                None,
                "Critical Error",
                f"A critical error occurred:\n{str(e)}\n\nThe application will now exit."
            )
        except:
            pass  # GUI might not be available

        cleanup_and_exit()
        sys.exit(1)


def run_broker_diagnostics():
    """
    Run diagnostics for all supported brokers.
    Useful for troubleshooting setup issues.
    """
    print("=== Swing Trader Broker Diagnostics ===\n")

    from login_setup.broker_factory import validate_broker_requirements, get_broker_capabilities

    for broker_mode in BrokerMode:
        config = get_broker_config(broker_mode)
        print(f"{config.display_name} ({broker_mode.value.upper()}):")
        print(f"  Currency: {config.currency}")
        print(f"  Markets: {config.market}")
        print(f"  Scanner: {config.scanner}")

        # Check requirements
        requirements = validate_broker_requirements(broker_mode)
        if requirements['valid']:
            print("  Status: ✅ Ready")
        else:
            print("  Status: ❌ Missing requirements")
            for req in requirements['missing_requirements']:
                print(f"    - {req}")
            for suggestion in requirements['suggestions']:
                print(f"    💡 {suggestion}")

        # Show capabilities
        capabilities = get_broker_capabilities(broker_mode)
        print(f"  Markets: {', '.join(capabilities.get('markets', []))}")
        print(f"  Instruments: {', '.join(capabilities.get('instruments', []))}")
        print()


if __name__ == "__main__":
    # Check for diagnostic mode
    if len(sys.argv) > 1 and sys.argv[1] == '--diagnostics':
        run_broker_diagnostics()
        sys.exit(0)

    # Run main application
    main()