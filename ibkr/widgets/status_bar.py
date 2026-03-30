# ==============================================================================
# SIMPLE LED-STYLE STATUS BAR SYSTEM
# ==============================================================================

import logging

from PySide6.QtWidgets import QLabel
from PySide6.QtCore import QTimer

logger = logging.getLogger(__name__)


class StatusBar(QLabel):
    """
    Simple LED-style status bar that shows brief status messages
    Perfect for header toolbar - clean and minimal
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # Default appearance
        self.setFixedHeight(24)
        self.setMinimumWidth(400)
        self.setMaximumWidth(600)

        # Auto-clear timer
        self.clear_timer = QTimer()
        self.clear_timer.timeout.connect(self._auto_clear)
        self.clear_timer.setSingleShot(True)

        # Flash animation for important messages
        self.flash_timer = QTimer()
        self.flash_timer.timeout.connect(self._flash)
        self.flash_count = 0
        self.flash_color = "#ffffff"

        self._setup_default_style()
        self.set_ready()

        logger.info("Status bar initialized")

    def _setup_default_style(self):
        """Setup default LED board styling"""
        self.setStyleSheet("""
            QLabel {
                background-color: #000000;
                color: #00ff00;
                border: 1px solid #333333;
                border-radius: 6px;
                padding: 2px 12px;
                font-family: "Courier New", monospace;
                font-size: 11px;
                font-weight: bold;
            }
        """)

    # ==========================================================================
    # SIMPLE STATUS METHODS
    # ==========================================================================

    def set_ready(self):
        """Set ready status - green LED"""
        self._set_status("● READY", "#00ff00", auto_clear=False)

    def set_order_placed(self, symbol: str = ""):
        """Show order placed status - blue LED"""
        message = f"● ORDER PLACED: {symbol}" if symbol else "● ORDER PLACED"
        self._set_status(message, "#00aaff", auto_clear=3000)

    def set_order_completed(self, symbol: str = "", pnl: str = ""):
        """Show order completed status - green LED with flash"""
        if pnl:
            message = f"● EXECUTED: {symbol} | {pnl}"
        elif symbol:
            message = f"● EXECUTED: {symbol}"
        else:
            message = "● ORDER EXECUTED"
        self._set_status(message, "#00ff00", auto_clear=4000, flash=True)

    def set_order_failed(self, reason: str = ""):
        """Show order failed status - red LED with flash"""
        message = f"● FAILED: {reason}" if reason else "● ORDER FAILED"
        self._set_status(message, "#ff4444", auto_clear=5000, flash=True)

    def set_order_rejected(self, reason: str = ""):
        """Show order rejected status - red LED"""
        message = f"● REJECTED: {reason}" if reason else "● ORDER REJECTED"
        self._set_status(message, "#ff6600", auto_clear=5000)

    def set_order_cancelled(self, symbol: str = ""):
        """Show order canceled status - yellow LED"""
        message = f"● CANCELLED: {symbol}" if symbol else "● ORDER CANCELLED"
        self._set_status(message, "#ffaa00", auto_clear=3000)

    def set_position_update(self, symbol: str, pnl: str):
        """Show position update - cyan LED"""
        message = f"● POSITION: {symbol} | {pnl}"
        self._set_status(message, "#00ffaa", auto_clear=4000)

    def set_error(self, error_message: str):
        """Show error status - red LED with flash"""
        message = f"● ERROR: {error_message}"
        self._set_status(message, "#ff0000", auto_clear=6000, flash=True)

    def set_info(self, info_message: str):
        """Show info status - white LED"""
        message = f"● {info_message}"
        self._set_status(message, "#aaaaaa", auto_clear=3000)

    def set_market_status(self, status: str):
        """Show market status - different colors based on status"""
        colors = {
            "OPEN": "#00ff00",
            "CLOSED": "#ff4444",
            "PRE_OPEN": "#ffaa00",
            "POST_CLOSE": "#666666"
        }
        color = colors.get(status, "#aaaaaa")
        self._set_status(f"● MARKET: {status}", color, auto_clear=False)

    def set_api_status(self, status: str):
        """Show API connection status"""
        colors = {
            "CONNECTED": "#00ff00",
            "DISCONNECTED": "#ff4444",
            "RECONNECTING": "#ffaa00"
        }
        color = colors.get(status, "#aaaaaa")
        self._set_status(f"● API: {status}", color, auto_clear=False if status == "CONNECTED" else 3000)

    # ==========================================================================
    # INTERNAL METHODS
    # ==========================================================================

    def _set_status(self, message: str, color: str, auto_clear: int = 0, flash: bool = False):
        """Internal method to set status with styling"""
        try:
            # Stop any active timers
            self.clear_timer.stop()
            self.flash_timer.stop()

            # Set the message
            self.setText(message)

            # Update color
            self.setStyleSheet(f"""
                QLabel {{
                    background-color: #1a1a1a;
                    color: {color};
                    border: 1px solid #333333;
                    border-radius: 3px;
                    padding: 2px 8px;
                    font-family: "Courier New", monospace;
                    font-size: 11px;
                    font-weight: bold;
                }}
            """)

            # Start flash animation if requested
            if flash:
                self.flash_color = color
                self.flash_count = 0
                self.flash_timer.start(200)  # Flash every 200ms

            # Start auto-clear timer if specified
            if auto_clear > 0:
                self.clear_timer.start(auto_clear)

            # Log the status change
            logger.debug(f"Status bar: {message}")

        except Exception as e:
            logger.error(f"Error setting status: {e}")

    def _auto_clear(self):
        """Auto-clear status back to ready"""
        self.set_ready()

    def _flash(self):
        """Flash animation for important messages"""
        try:
            self.flash_count += 1

            # Alternate between normal color and brighter version
            if self.flash_count % 2 == 0:
                flash_style = f"""
                    QLabel {{
                        background-color: #1a1a1a;
                        color: {self.flash_color};
                        border: 1px solid #333333;
                        border-radius: 3px;
                        padding: 2px 8px;
                        font-family: "Courier New", monospace;
                        font-size: 11px;
                        font-weight: bold;
                    }}
                """
            else:
                flash_style = f"""
                    QLabel {{
                        background-color: {self.flash_color};
                        color: #000000;
                        border: 1px solid #333333;
                        border-radius: 3px;
                        padding: 2px 8px;
                        font-family: "Courier New", monospace;
                        font-size: 11px;
                        font-weight: bold;
                    }}
                """

            self.setStyleSheet(flash_style)

            # Stop flashing after 6 flashes (3 seconds)
            if self.flash_count >= 6:
                self.flash_timer.stop()
                # Return to normal color
                self.setStyleSheet(f"""
                    QLabel {{
                        background-color: #1a1a1a;
                        color: {self.flash_color};
                        border: 1px solid #333333;
                        border-radius: 3px;
                        padding: 2px 8px;
                        font-family: "Courier New", monospace;
                        font-size: 11px;
                        font-weight: bold;
                    }}
                """)

        except Exception as e:
            logger.error(f"Error in flash animation: {e}")

    def clear_status(self):
        """Manually clear status"""
        self.clear_timer.stop()
        self.flash_timer.stop()
        self.set_ready()


# ==============================================================================
# GLOBAL STATUS BAR MANAGER
# ==============================================================================

class GlobalStatusManager:
    """
    Global manager for status bar - can be used anywhere in the app
    Singleton pattern for easy access
    """

    _instance = None
    _status_bar = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def initialize(self, status_bar: StatusBar):
        """Initialize with status bar widget"""
        self._status_bar = status_bar
        logger.info("Global status manager initialized")

    def is_initialized(self) -> bool:
        """Check if the status bar is initialized"""
        return self._status_bar is not None

    # ==========================================================================
    # GLOBAL STATUS METHODS - USE THESE ANYWHERE IN THE APP
    # ==========================================================================

    def show_order_placed(self, symbol: str = ""):
        """Show order placed status globally"""
        if self._status_bar:
            self._status_bar.set_order_placed(symbol)

    def show_order_completed(self, symbol: str = "", pnl: str = ""):
        """Show order completed status globally"""
        if self._status_bar:
            self._status_bar.set_order_completed(symbol, pnl)

    def show_order_failed(self, reason: str = ""):
        """Show order failed status globally"""
        if self._status_bar:
            self._status_bar.set_order_failed(reason)

    def show_order_rejected(self, reason: str = ""):
        """Show order rejected status globally"""
        if self._status_bar:
            self._status_bar.set_order_rejected(reason)

    def show_order_cancelled(self, symbol: str = ""):
        """Show order canceled status globally"""
        if self._status_bar:
            self._status_bar.set_order_cancelled(symbol)

    def show_position_update(self, symbol: str, pnl: str):
        """Show position update globally"""
        if self._status_bar:
            self._status_bar.set_position_update(symbol, pnl)

    def show_error(self, error_message: str):
        """Show error status globally"""
        if self._status_bar:
            self._status_bar.set_error(error_message)

    def show_info(self, info_message: str):
        """Show info status globally"""
        if self._status_bar:
            self._status_bar.set_info(info_message)

    def show_market_status(self, status: str):
        """Show market status globally"""
        if self._status_bar:
            self._status_bar.set_market_status(status)

    def show_api_status(self, status: str):
        """Show API status globally"""
        if self._status_bar:
            self._status_bar.set_api_status(status)

    def set_ready(self):
        """Set ready status globally"""
        if self._status_bar:
            self._status_bar.set_ready()

    def clear_status(self):
        """Clear status globally"""
        if self._status_bar:
            self._status_bar.clear_status()


# ==============================================================================
# CONVENIENCE FUNCTIONS FOR EASY ACCESS
# ==============================================================================

# Global instance
status = GlobalStatusManager()


def show_order_placed(symbol: str = ""):
    """Convenience function: Show order placed"""
    status.show_order_placed(symbol)


def show_order_completed(symbol: str = "", pnl: str = ""):
    """Convenience function: Show order completed"""
    status.show_order_completed(symbol, pnl)


def show_order_failed(reason: str = ""):
    """Convenience function: Show order failed"""
    status.show_order_failed(reason)


def show_order_rejected(reason: str = ""):
    """Convenience function: Show order rejected"""
    status.show_order_rejected(reason)


def show_order_cancelled(symbol: str = ""):
    """Convenience function: Show order canceled"""
    status.show_order_cancelled(symbol)


def show_position_update(symbol: str, pnl: str):
    """Convenience function: Show position update"""
    status.show_position_update(symbol, pnl)


def show_error(error_message: str):
    """Convenience function: Show error"""
    status.show_error(error_message)


def show_info(info_message: str):
    """Convenience function: Show info"""
    status.show_info(info_message)


def show_market_status(status_msg: str):
    """Convenience function: Show market status"""
    status.show_market_status(status_msg)


def show_api_status(status_msg: str):
    """Convenience function: Show API status"""
    status.show_api_status(status_msg)


def set_ready():
    """Convenience function: Set ready status"""
    status.set_ready()


def clear_status():
    """Convenience function: Clear status"""
    status.clear_status()


# ==============================================================================
# USAGE EXAMPLES
# ==============================================================================

"""
USAGE IN HEADER TOOLBAR:

# In HeaderToolbar.__init__():
self.status_bar = StatusBar(self)
layout.addWidget(self.status_bar)  

# Initialize global manager
from widgets.status_bar import status
status.initialize(self.status_bar)

USAGE ANYWHERE IN THE APP:

# Import convenience functions
from widgets.status_bar import show_order_placed, show_order_completed, show_error

# Use anywhere
show_order_placed("RELIANCE")
show_order_completed("RELIANCE", "+₹2,450")
show_error("API connection failed")

OR use the global manager:

# Import global manager
from widgets.status_bar import status

# Use anywhere
status.show_order_placed("RELIANCE")
status.show_order_completed("RELIANCE", "+₹2,450")
status.show_error("API connection failed")

FEATURES:
- LED-style appearance with colored dots
- Auto-clear after specified time
- Flash animation for important messages
- Monospace font for consistent layout
- Global access from anywhere
- No popup distractions
- Professional and clean
"""
