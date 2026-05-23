from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget


def show_shortcuts_reference_dialog(parent: QWidget) -> None:
    """Display keyboard shortcuts as a built-in reference sheet."""
    message = QMessageBox(parent)
    message.setWindowTitle("Keyboard Shortcuts")
    message.setIcon(QMessageBox.Icon.Information)
    message.setTextFormat(Qt.TextFormat.RichText)
    message.setStyleSheet("QLabel { background-color: transparent; }")
    message.setText(
        """
        <h3>Keyboard Shortcuts Reference</h3>
        <table cellspacing="6" cellpadding="2">
            <tr><td><b>Action</b></td><td><b>Shortcut</b></td></tr>
            <tr><td>Buy ticket</td><td><code>F1</code> / <code>Shift+B</code></td></tr>
            <tr><td>Sell ticket</td><td><code>F2</code> / <code>Shift+S</code></td></tr>
            <tr><td>Open order dialog</td><td><code>F3</code> / <code>Shift+O</code></td></tr>
            <tr><td>Toggle floating positions</td><td><code>Ctrl+P</code> / <code>Shift+P</code></td></tr>
            <tr><td>Show stock info</td><td><code>Ctrl+I</code> / <code>Shift+I</code></td></tr>
            <tr><td>Toggle symbol in active watchlist (add/remove)</td><td><code>Ctrl+Shift+0</code></td></tr>
            <tr><td>Add symbol to watchlist #1-#9</td><td><code>Ctrl+Shift+1..9</code></td></tr>
            <tr><td>Open Order History</td><td><code>Ctrl+H</code></td></tr>
            <tr><td>Open Performance</td><td><code>Ctrl+D</code></td></tr>
            <tr><td>Next symbol (context-aware)</td><td><code>Space</code></td></tr>
            <tr><td>Previous symbol (context-aware)</td><td><code>Shift+Space</code></td></tr>
            <tr><td>Close active modal / clear search focus</td><td><code>Esc</code></td></tr>
        </table>
        """
    )
    message.setStandardButtons(QMessageBox.StandardButton.Ok)
    message.exec()


def show_about_dialog(parent: QWidget) -> None:
    """Display detailed application summary information."""
    message = QMessageBox(parent)
    message.setWindowTitle("About qullamaggie")
    message.setIcon(QMessageBox.Icon.Information)
    message.setTextFormat(Qt.TextFormat.RichText)
    message.setText("""
        <h2>qullamaggie</h2>
        <p>
            A desktop swing-trading command center for scanning Indian equity
            markets, reviewing charts, managing watchlists, and monitoring
            positions from one focused workspace.
        </p>
        <h3>What this workspace includes</h3>
        <ul>
            <li><b>Market scanner:</b> Chartink and Finviz workflows for finding setups quickly.</li>
            <li><b>Interactive charts:</b> candlesticks, indicators, drawings, and persisted chart notes.</li>
            <li><b>Watchlists:</b> tabbed symbol lists with quick chart access and stock details.</li>
            <li><b>Trading tools:</b> order entry, pending orders, order history, and P&amp;L views.</li>
            <li><b>Risk visibility:</b> live positions, floating panels, alerts, and app health indicators.</li>
        </ul>
        <h3>Broker and data context</h3>
        <p>
            This build is wired for Kite/Zerodha market access with paper-trading
            support for safer workflow validation before live execution.
        </p>
        <h3>Important note</h3>
        <p>
            qullamaggie is a decision-support tool, not financial advice. Always
            verify market data, order details, risk, and broker confirmations before
            placing or modifying trades.
        </p>
    """)
    message.setStandardButtons(QMessageBox.StandardButton.Ok)
    message.exec()
