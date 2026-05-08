"""
Enhanced IBKR main window with complete trading functionality.
Integrates seamlessly with your dual-mode architecture.
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QTableWidget, QTableWidgetItem, QPushButton, QLineEdit, QSpinBox,
    QComboBox, QLabel, QGroupBox, QProgressBar, QTextEdit, QSplitter,
    QHeaderView, QMessageBox, QStatusBar, QMenuBar, QMenu, QDialog
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QColor, QPalette, QAction

try:
    from ib_insync import Stock, Contract

    IBKR_AVAILABLE = True
except ImportError:
    IBKR_AVAILABLE = False
    Stock = None
    Contract = None

from login_setup.broker_modes import BrokerMode, TradingMode
from ibkr.core.trading_client import IBKRTradingClient
from ibkr.utils.paper_trading_manager import IBKRPaperTradingManager

# Import the separate order dialog
try:
    from ibkr.ui.order_dialog import OrderDialog
except ImportError:
    # Fallback if order dialog not available
    OrderDialog = None

logger = logging.getLogger(__name__)


OPEN_PROFIT_COLOR = QColor("#00d4a8")
OPEN_PROFIT_BG_TINT = QColor("#0a2520")
OPEN_LOSS_COLOR = QColor("#ff4d6a")
OPEN_LOSS_BG_TINT = QColor("#200a10")
FLAT_COLOR = QColor("#7a94b0")


class IBKRMainWindow(QMainWindow):
    """
    Enhanced main window for IBKR trading with complete functionality.
    Supports both live and paper trading modes.
    """

    # Signals
    order_placed = Signal(dict)
    position_updated = Signal(dict)
    connection_status_changed = Signal(bool)

    def __init__(self, trading_client: IBKRTradingClient, trading_mode: TradingMode):
        super().__init__()
        self.trading_client = trading_client
        self.trading_mode = trading_mode
        self.is_paper_trading = trading_mode == TradingMode.PAPER

        # Data storage
        self.watchlist = []
        self.market_data = {}
        self.positions = {}
        self.orders = {}

        # UI components
        self.watchlist_table = None
        self.positions_table = None
        self.orders_table = None
        self.account_info_widget = None
        self.status_bar = None

        self._setup_ui()
        self._setup_signals()
        self._setup_timers()
        self._load_initial_data()

        # Window properties
        self.setWindowTitle(f"qullamaggie - IBKR Trading - {trading_mode.value.title()} Mode")
        self.resize(1200, 800)

    def _setup_ui(self):
        """Set up the main user interface"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Create menu bar
        self._create_menu_bar()

        # Main layout with splitter
        main_layout = QVBoxLayout(central_widget)

        # Top toolbar
        toolbar_layout = QHBoxLayout()

        # Connection status
        self.connection_label = QLabel("🔴 Disconnected")
        toolbar_layout.addWidget(self.connection_label)

        # Trading mode indicator
        mode_label = QLabel(f"Mode: {self.trading_mode.value.title()}")
        mode_label.setStyleSheet("font-weight: bold; color: #007bff;")
        toolbar_layout.addWidget(mode_label)

        toolbar_layout.addStretch()

        # Quick order section
        quick_order_group = QGroupBox("Quick Order")
        quick_layout = QHBoxLayout(quick_order_group)

        self.quick_symbol = QLineEdit()
        self.quick_symbol.setPlaceholderText("Symbol")
        self.quick_symbol.setMaximumWidth(80)
        quick_layout.addWidget(self.quick_symbol)

        self.quick_quantity = QSpinBox()
        self.quick_quantity.setRange(1, 10000)
        self.quick_quantity.setValue(100)
        self.quick_quantity.setMaximumWidth(80)
        quick_layout.addWidget(self.quick_quantity)

        buy_btn = QPushButton("Buy")
        buy_btn.clicked.connect(lambda: self._quick_order("BUY"))
        buy_btn.setStyleSheet("background-color: #28a745; color: white;")
        quick_layout.addWidget(buy_btn)

        sell_btn = QPushButton("Sell")
        sell_btn.clicked.connect(lambda: self._quick_order("SELL"))
        sell_btn.setStyleSheet("background-color: #dc3545; color: white;")
        quick_layout.addWidget(sell_btn)

        toolbar_layout.addWidget(quick_order_group)

        main_layout.addLayout(toolbar_layout)

        # Create main content with tabs
        tab_widget = QTabWidget()

        # Trading tab
        trading_tab = self._create_trading_tab()
        tab_widget.addTab(trading_tab, "Trading")

        # Portfolio tab
        portfolio_tab = self._create_portfolio_tab()
        tab_widget.addTab(portfolio_tab, "Portfolio")

        # Orders tab
        orders_tab = self._create_orders_tab()
        tab_widget.addTab(orders_tab, "Orders")

        # Analysis tab (placeholder)
        analysis_tab = self._create_analysis_tab()
        tab_widget.addTab(analysis_tab, "Analysis")

        main_layout.addWidget(tab_widget)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    def _create_menu_bar(self):
        """Create application menu bar"""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("File")

        connect_action = QAction("Connect", self)
        connect_action.triggered.connect(self._reconnect)
        file_menu.addAction(connect_action)

        disconnect_action = QAction("Disconnect", self)
        disconnect_action.triggered.connect(self._disconnect)
        file_menu.addAction(disconnect_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Trading menu
        trading_menu = menubar.addMenu("Trading")

        place_order_action = QAction("Place Order", self)
        place_order_action.triggered.connect(self._show_order_dialog)
        trading_menu.addAction(place_order_action)

        cancel_all_action = QAction("Cancel All Orders", self)
        cancel_all_action.triggered.connect(self._cancel_all_orders)
        trading_menu.addAction(cancel_all_action)

        # Tools menu
        tools_menu = menubar.addMenu("Tools")

        if self.is_paper_trading:
            reset_action = QAction("Reset Paper Account", self)
            reset_action.triggered.connect(self._reset_paper_account)
            tools_menu.addAction(reset_action)

        refresh_action = QAction("Refresh All Data", self)
        refresh_action.triggered.connect(self._refresh_all_data)
        tools_menu.addAction(refresh_action)

    def _create_trading_tab(self) -> QWidget:
        """Create the main trading tab"""
        widget = QWidget()
        layout = QHBoxLayout(widget)

        # Left side - Watchlist
        left_panel = QVBoxLayout()

        # Watchlist section
        watchlist_group = QGroupBox("Watchlist")
        watchlist_layout = QVBoxLayout(watchlist_group)

        # Add symbol section
        add_symbol_layout = QHBoxLayout()
        self.symbol_input = QLineEdit()
        self.symbol_input.setPlaceholderText("Enter symbol...")
        self.symbol_input.returnPressed.connect(self._add_to_watchlist)
        add_symbol_layout.addWidget(self.symbol_input)

        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_to_watchlist)
        add_symbol_layout.addWidget(add_btn)

        watchlist_layout.addLayout(add_symbol_layout)

        # Watchlist table
        self.watchlist_table = QTableWidget(0, 5)
        self.watchlist_table.setHorizontalHeaderLabels([
            "Symbol", "Last", "Change", "Change%", "Volume"
        ])
        self.watchlist_table.horizontalHeader().setStretchLastSection(True)
        self.watchlist_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.watchlist_table.doubleClicked.connect(self._on_watchlist_double_click)
        watchlist_layout.addWidget(self.watchlist_table)

        left_panel.addWidget(watchlist_group)

        # Account info section
        account_group = QGroupBox("Account Information")
        account_layout = QVBoxLayout(account_group)

        self.account_info_widget = QTextEdit()
        self.account_info_widget.setMaximumHeight(150)
        self.account_info_widget.setReadOnly(True)
        account_layout.addWidget(self.account_info_widget)

        left_panel.addWidget(account_group)

        # Right side - Order entry and market data
        right_panel = QVBoxLayout()

        # Order entry section
        order_group = QGroupBox("Order Entry")
        order_layout = QVBoxLayout(order_group)

        # Order form
        order_form_layout = QHBoxLayout()

        self.order_symbol = QLineEdit()
        self.order_symbol.setPlaceholderText("Symbol")
        order_form_layout.addWidget(QLabel("Symbol:"))
        order_form_layout.addWidget(self.order_symbol)

        self.order_quantity = QSpinBox()
        self.order_quantity.setRange(1, 10000)
        self.order_quantity.setValue(100)
        order_form_layout.addWidget(QLabel("Qty:"))
        order_form_layout.addWidget(self.order_quantity)

        self.order_type = QComboBox()
        self.order_type.addItems(["MKT", "LMT", "STP"])
        order_form_layout.addWidget(QLabel("Type:"))
        order_form_layout.addWidget(self.order_type)

        self.order_price = QLineEdit()
        self.order_price.setPlaceholderText("Price")
        order_form_layout.addWidget(QLabel("Price:"))
        order_form_layout.addWidget(self.order_price)

        order_layout.addLayout(order_form_layout)

        # Order buttons
        order_buttons_layout = QHBoxLayout()

        buy_btn = QPushButton("BUY")
        buy_btn.clicked.connect(lambda: self._place_order("BUY"))
        buy_btn.setStyleSheet("background-color: #28a745; color: white; font-weight: bold;")
        order_buttons_layout.addWidget(buy_btn)

        sell_btn = QPushButton("SELL")
        sell_btn.clicked.connect(lambda: self._place_order("SELL"))
        sell_btn.setStyleSheet("background-color: #dc3545; color: white; font-weight: bold;")
        order_buttons_layout.addWidget(sell_btn)

        advanced_btn = QPushButton("Advanced Order")
        advanced_btn.clicked.connect(self._show_order_dialog)
        order_buttons_layout.addWidget(advanced_btn)

        order_layout.addLayout(order_buttons_layout)

        right_panel.addWidget(order_group)

        # Market data section
        market_data_group = QGroupBox("Market Data")
        market_data_layout = QVBoxLayout(market_data_group)

        self.market_data_text = QTextEdit()
        self.market_data_text.setReadOnly(True)
        self.market_data_text.setMaximumHeight(200)
        market_data_layout.addWidget(self.market_data_text)

        right_panel.addWidget(market_data_group)

        # Add panels to main layout
        layout.addLayout(left_panel, 2)
        layout.addLayout(right_panel, 1)

        return widget


    def _create_portfolio_tab(self) -> QWidget:
        """Create the portfolio tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Positions table
        positions_group = QGroupBox("Current Positions")
        positions_layout = QVBoxLayout(positions_group)

        self.positions_table = QTableWidget(0, 8)
        self.positions_table.setHorizontalHeaderLabels([
            "Symbol", "Quantity", "Avg Price", "Current Price",
            "Market Value", "P&L", "P&L %", "Exchange"
        ])
        self.positions_table.horizontalHeader().setStretchLastSection(True)
        self.positions_table.setSelectionBehavior(QTableWidget.SelectRows)
        positions_layout.addWidget(self.positions_table)

        # Position controls
        pos_controls_layout = QHBoxLayout()

        refresh_pos_btn = QPushButton("Refresh Positions")
        refresh_pos_btn.clicked.connect(self._refresh_positions)
        pos_controls_layout.addWidget(refresh_pos_btn)

        close_pos_btn = QPushButton("Close Selected Position")
        close_pos_btn.clicked.connect(self._close_selected_position)
        pos_controls_layout.addWidget(close_pos_btn)

        pos_controls_layout.addStretch()

        positions_layout.addLayout(pos_controls_layout)
        layout.addWidget(positions_group)

        return widget


    def _create_orders_tab(self) -> QWidget:
        """Create the orders tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Orders table
        orders_group = QGroupBox("Order History")
        orders_layout = QVBoxLayout(orders_group)

        self.orders_table = QTableWidget(0, 9)
        self.orders_table.setHorizontalHeaderLabels([
            "Order ID", "Symbol", "Action", "Quantity", "Type",
            "Price", "Status", "Filled", "Time"
        ])
        self.orders_table.horizontalHeader().setStretchLastSection(True)
        self.orders_table.setSelectionBehavior(QTableWidget.SelectRows)
        orders_layout.addWidget(self.orders_table)

        # Order controls
        order_controls_layout = QHBoxLayout()

        refresh_orders_btn = QPushButton("Refresh Orders")
        refresh_orders_btn.clicked.connect(self._refresh_orders)
        order_controls_layout.addWidget(refresh_orders_btn)

        cancel_selected_btn = QPushButton("Cancel Selected")
        cancel_selected_btn.clicked.connect(self._cancel_selected_order)
        order_controls_layout.addWidget(cancel_selected_btn)

        cancel_all_btn = QPushButton("Cancel All Orders")
        cancel_all_btn.clicked.connect(self._cancel_all_orders)
        order_controls_layout.addWidget(cancel_all_btn)

        order_controls_layout.addStretch()

        orders_layout.addLayout(order_controls_layout)
        layout.addWidget(orders_group)

        return widget


    def _create_analysis_tab(self) -> QWidget:
        """Create the analysis tab (placeholder for future features)"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Performance summary
        perf_group = QGroupBox("Performance Summary")
        perf_layout = QVBoxLayout(perf_group)

        self.performance_text = QTextEdit()
        self.performance_text.setReadOnly(True)
        self.performance_text.setText("Performance analysis features coming soon...")
        perf_layout.addWidget(self.performance_text)

        layout.addWidget(perf_group)

        # Chart placeholder
        chart_group = QGroupBox("Charts")
        chart_layout = QVBoxLayout(chart_group)

        chart_placeholder = QLabel("Chart functionality will be added here")
        chart_placeholder.setAlignment(Qt.AlignCenter)
        chart_placeholder.setStyleSheet("color: gray; font-style: italic;")
        chart_layout.addWidget(chart_placeholder)

        layout.addWidget(chart_group)

        return widget


    def _setup_signals(self):
        """Connect trading client signals to UI updates"""
        if hasattr(self.trading_client, 'order_status_updated'):
            self.trading_client.order_status_updated.connect(self._on_order_status_update)

        if hasattr(self.trading_client, 'position_updated'):
            self.trading_client.position_updated.connect(self._on_position_update)

        if hasattr(self.trading_client, 'market_data_updated'):
            self.trading_client.market_data_updated.connect(self._on_market_data_update)

        if hasattr(self.trading_client, 'account_updated'):
            self.trading_client.account_updated.connect(self._on_account_update)

        if hasattr(self.trading_client, 'connection_status_changed'):
            self.trading_client.connection_status_changed.connect(self._on_connection_status_changed)


    def _setup_timers(self):
        """Set up periodic update timers"""
        # Refresh positions every 30 seconds
        self.positions_timer = QTimer()
        self.positions_timer.timeout.connect(self._refresh_positions)
        self.positions_timer.start(30000)

        # Refresh orders every 10 seconds
        self.orders_timer = QTimer()
        self.orders_timer.timeout.connect(self._refresh_orders)
        self.orders_timer.start(10000)

        # Update account info every 60 seconds
        self.account_timer = QTimer()
        self.account_timer.timeout.connect(self._refresh_account_info)
        self.account_timer.start(60000)


    def _load_initial_data(self):
        """Load initial data on startup"""
        self._update_connection_status()
        self._refresh_positions()
        self._refresh_orders()
        self._refresh_account_info()

        # Add some default symbols to watchlist
        default_symbols = ["AAPL", "GOOGL", "MSFT", "TSLA", "SPY"]
        for symbol in default_symbols:
            self._add_symbol_to_watchlist(symbol)

        # Event handlers


    def _on_order_status_update(self, order_data: Dict[str, Any]):
        """Handle order status updates"""
        self.orders[order_data['order_id']] = order_data
        self._refresh_orders_table()

        status_msg = f"Order {order_data['order_id']} - {order_data['symbol']}: {order_data['status']}"
        self.status_bar.showMessage(status_msg, 5000)


    def _on_position_update(self, position_data: Dict[str, Any]):
        """Handle position updates"""
        self.positions[position_data['symbol']] = position_data
        self._refresh_positions_table()


    def _on_market_data_update(self, market_data: Dict[str, Any]):
        """Handle market data updates"""
        symbol = market_data['symbol']
        self.market_data[symbol] = market_data
        self._update_watchlist_display()
        self._update_market_data_display()


    def _on_account_update(self, account_data: Dict[str, Any]):
        """Handle account updates"""
        self._update_account_display(account_data)


    def _on_connection_status_changed(self, connected: bool):
        """Handle connection status changes"""
        self._update_connection_status(connected)


    # UI update methods

    def _update_connection_status(self, connected: bool = None):
        """Update connection status display"""
        if connected is None:
            connected = self.trading_client.is_connected()

        if connected:
            self.connection_label.setText("🟢 Connected")
            self.connection_label.setStyleSheet("color: green;")
        else:
            self.connection_label.setText("🔴 Disconnected")
            self.connection_label.setStyleSheet("color: red;")


    def _refresh_positions(self):
        """Refresh positions data"""
        try:
            positions = self.trading_client.get_positions()
            self.positions = {pos['tradingsymbol']: pos for pos in positions}
            self._refresh_positions_table()
        except Exception as e:
            logger.error(f"Error refreshing positions: {e}")
            self.status_bar.showMessage(f"Error refreshing positions: {e}", 5000)


    def _refresh_orders(self):
        """Refresh orders data"""
        try:
            orders = self.trading_client.get_orders()
            self.orders = {order['order_id']: order for order in orders}
            self._refresh_orders_table()
        except Exception as e:
            logger.error(f"Error refreshing orders: {e}")
            self.status_bar.showMessage(f"Error refreshing orders: {e}", 5000)


    def _refresh_account_info(self):
        """Refresh account information"""
        try:
            if hasattr(self.trading_client, 'get_account_summary'):
                account_summary = self.trading_client.get_account_summary()
                self._update_account_display(account_summary)
        except Exception as e:
            logger.error(f"Error refreshing account info: {e}")


    def _refresh_positions_table(self):
        """Update positions table display"""
        self.positions_table.setRowCount(len(self.positions))

        for row, (symbol, position) in enumerate(self.positions.items()):
            self.positions_table.setItem(row, 0, QTableWidgetItem(symbol))
            self.positions_table.setItem(row, 1, QTableWidgetItem(str(position.get('quantity', 0))))
            self.positions_table.setItem(row, 2, QTableWidgetItem(f"${position.get('average_price', 0):.2f}"))

            # Get current price from market data or position data
            current_price = position.get('current_price', 0)
            if current_price == 0 and symbol in self.market_data:
                current_price = self.market_data[symbol].get('last_price', self.market_data[symbol].get('last', 0))

            self.positions_table.setItem(row, 3, QTableWidgetItem(f"${current_price:.2f}"))

            # Calculate market value
            quantity = position.get('quantity', 0)
            market_value = quantity * current_price if current_price > 0 else position.get('market_value', 0)
            self.positions_table.setItem(row, 4, QTableWidgetItem(f"${market_value:.2f}"))

            # P&L calculation
            pnl = position.get('pnl', 0)
            if pnl == 0 and current_price > 0:  # Calculate if not provided
                avg_price = position.get('average_price', 0)
                if avg_price > 0:
                    pnl = (current_price - avg_price) * quantity

            pnl_item = QTableWidgetItem(f"${pnl:.2f}")
            pnl_item.setForeground(self._get_open_pnl_foreground_color(pnl))
            self.positions_table.setItem(row, 5, pnl_item)

            # P&L percentage
            avg_price = position.get('average_price', 0)
            pnl_percent = (pnl / (avg_price * abs(quantity)) * 100) if avg_price > 0 and quantity != 0 else 0
            pnl_percent_item = QTableWidgetItem(f"{pnl_percent:.2f}%")
            pnl_percent_item.setForeground(self._get_open_pnl_foreground_color(pnl))
            self.positions_table.setItem(row, 6, pnl_percent_item)

            self.positions_table.setItem(row, 7, QTableWidgetItem(position.get('exchange', 'SMART')))
            self._apply_open_pnl_row_style(row, pnl)

    def _get_open_pnl_foreground_color(self, pnl_value: float) -> QColor:
        """Open P&L text color for profit/loss/flat values."""
        if pnl_value > 0:
            return OPEN_PROFIT_COLOR
        if pnl_value < 0:
            return OPEN_LOSS_COLOR
        return FLAT_COLOR

    def _apply_open_pnl_row_style(self, row: int, pnl_value: float) -> None:
        """Apply open P&L row tint so positions are scannable at a glance."""
        if pnl_value > 0:
            row_foreground = OPEN_PROFIT_COLOR
            row_background = OPEN_PROFIT_BG_TINT
        elif pnl_value < 0:
            row_foreground = OPEN_LOSS_COLOR
            row_background = OPEN_LOSS_BG_TINT
        else:
            row_foreground = FLAT_COLOR
            row_background = None

        for col in range(self.positions_table.columnCount()):
            item = self.positions_table.item(row, col)
            if item is None:
                item = QTableWidgetItem("")
                self.positions_table.setItem(row, col, item)

            item.setForeground(row_foreground)
            if row_background is not None:
                item.setBackground(row_background)
            else:
                item.setBackground(QColor(Qt.transparent))


    def _refresh_orders_table(self):
        """Update orders table display"""
        self.orders_table.setRowCount(len(self.orders))

        for row, (order_id, order) in enumerate(self.orders.items()):
            self.orders_table.setItem(row, 0, QTableWidgetItem(str(order_id)))
            self.orders_table.setItem(row, 1, QTableWidgetItem(order.get('tradingsymbol', '')))
            self.orders_table.setItem(row, 2, QTableWidgetItem(order.get('transaction_type', '')))
            self.orders_table.setItem(row, 3, QTableWidgetItem(str(order.get('quantity', 0))))
            self.orders_table.setItem(row, 4, QTableWidgetItem(order.get('order_type', '')))
            self.orders_table.setItem(row, 5, QTableWidgetItem(f"${order.get('price', 0):.2f}"))

            status = order.get('status', '')
            status_item = QTableWidgetItem(status)
            if status in ['FILLED', 'COMPLETE']:
                status_item.setForeground(QColor("green"))
            elif status in ['CANCELLED', 'REJECTED']:
                status_item.setForeground(QColor("red"))
            elif status in ['SUBMITTED', 'PENDING']:
                status_item.setForeground(QColor("blue"))

            self.orders_table.setItem(row, 6, status_item)
            self.orders_table.setItem(row, 7, QTableWidgetItem(str(order.get('filled_quantity', 0))))
            self.orders_table.setItem(row, 8, QTableWidgetItem(
                order.get('order_timestamp', '')[:19] if order.get('order_timestamp') else ''))


    def _update_account_display(self, account_data: Dict[str, Any]):
        """Update account information display"""
        account_text = "Account Summary:\n\n"

        for key, value in account_data.items():
            if isinstance(value, dict) and 'value' in value:
                account_text += f"{key}: {value['value']} {value.get('currency', '')}\n"
            else:
                account_text += f"{key}: {value}\n"

        self.account_info_widget.setText(account_text)


    def _update_watchlist_display(self):
        """Update watchlist table with latest market data"""
        for row in range(self.watchlist_table.rowCount()):
            symbol_item = self.watchlist_table.item(row, 0)
            if symbol_item:
                symbol = symbol_item.text()
                if symbol in self.market_data:
                    data = self.market_data[symbol]

                    # Update last price - IBKR uses 'last_price' not 'last'
                    last_price = data.get('last_price', data.get('last', 0))
                    self.watchlist_table.setItem(row, 1, QTableWidgetItem(f"${last_price:.2f}"))

                    # Calculate change using open price
                    open_price = data.get('open', last_price)
                    change = last_price - open_price if open_price > 0 else 0
                    change_item = QTableWidgetItem(f"${change:.2f}")
                    change_item.setForeground(QColor("green" if change >= 0 else "red"))
                    self.watchlist_table.setItem(row, 2, change_item)

                    # Change percentage
                    change_percent = (change / open_price * 100) if open_price > 0 else 0
                    change_percent_item = QTableWidgetItem(f"{change_percent:.2f}%")
                    change_percent_item.setForeground(QColor("green" if change_percent >= 0 else "red"))
                    self.watchlist_table.setItem(row, 3, change_percent_item)

                    # Volume
                    volume = data.get('volume', 0)
                    self.watchlist_table.setItem(row, 4, QTableWidgetItem(f"{volume:,}"))


    def _update_market_data_display(self):
        """Update market data text display"""
        if not self.market_data:
            return

        text = "Real-time Market Data:\n\n"
        for symbol, data in list(self.market_data.items())[-5:]:  # Show last 5 updates
            last_price = data.get('last_price', data.get('last', 0))
            bid = data.get('bid', 0)
            ask = data.get('ask', 0)
            text += f"{symbol}: ${last_price:.2f} "
            text += f"(Bid: ${bid:.2f}, Ask: ${ask:.2f})\n"

        self.market_data_text.setText(text)


    # Trading methods

    def _add_to_watchlist(self):
        """Add symbol to watchlist"""
        symbol = self.symbol_input.text().strip().upper()
        if symbol:
            self._add_symbol_to_watchlist(symbol)
            self.symbol_input.clear()


    def _add_symbol_to_watchlist(self, symbol: str):
        """Add a symbol to the watchlist table"""
        # Check if symbol already exists
        for row in range(self.watchlist_table.rowCount()):
            if self.watchlist_table.item(row, 0).text() == symbol:
                return

        # Add new row
        row = self.watchlist_table.rowCount()
        self.watchlist_table.insertRow(row)
        self.watchlist_table.setItem(row, 0, QTableWidgetItem(symbol))
        self.watchlist_table.setItem(row, 1, QTableWidgetItem("--"))
        self.watchlist_table.setItem(row, 2, QTableWidgetItem("--"))
        self.watchlist_table.setItem(row, 3, QTableWidgetItem("--"))
        self.watchlist_table.setItem(row, 4, QTableWidgetItem("--"))

        # Subscribe to market data if client supports it
        if hasattr(self.trading_client, 'subscribe_market_data'):
            self.trading_client.subscribe_market_data([symbol])


    def _on_watchlist_double_click(self, index):
        """Handle double-click on watchlist item"""
        row = index.row()
        symbol_item = self.watchlist_table.item(row, 0)
        if symbol_item:
            symbol = symbol_item.text()
            self.order_symbol.setText(symbol)


    def _quick_order(self, action: str):
        """Place a quick market order"""
        symbol = self.quick_symbol.text().strip().upper()
        quantity = self.quick_quantity.value()

        if not symbol:
            QMessageBox.warning(self, "Error", "Please enter a symbol")
            return

        self._execute_order(symbol, action, quantity, "MKT")


    def _place_order(self, action: str):
        """Place an order from the order entry form"""
        symbol = self.order_symbol.text().strip().upper()
        quantity = self.order_quantity.value()
        order_type = self.order_type.currentText()
        price_text = self.order_price.text().strip()

        if not symbol:
            QMessageBox.warning(self, "Error", "Please enter a symbol")
            return

        price = None
        if order_type != "MKT" and price_text:
            try:
                price = float(price_text)
            except ValueError:
                QMessageBox.warning(self, "Error", "Invalid price format")
                return

        self._execute_order(symbol, action, quantity, order_type, price)


    def _execute_order(self, symbol: str, action: str, quantity: int,
                       order_type: str, price: Optional[float] = None):
        """Execute an order with the trading client"""
        try:
            # Prepare order parameters
            order_params = {
                'symbol': symbol,
                'action': action,
                'quantity': quantity,
                'order_type': order_type
            }

            if price is not None:
                order_params['price'] = price

            # Place the order
            result = self.trading_client.place_order(**order_params)

            if 'error' in result:
                QMessageBox.critical(self, "Order Error", result['error'])
            else:
                order_id = result.get('order_id', 'Unknown')
                self.status_bar.showMessage(f"Order placed: {order_id}", 5000)

                # Clear order form
                self.order_symbol.clear()
                self.order_price.clear()

                # Refresh orders
                self._refresh_orders()

        except Exception as e:
            logger.error(f"Error placing order: {e}")
            QMessageBox.critical(self, "Order Error", str(e))


    def _show_order_dialog(self):
        """Show advanced order dialog"""
        if OrderDialog is None:
            QMessageBox.information(self, "Info", "Advanced order dialog not available")
            return

        # Get current symbol and price
        symbol = self.order_symbol.text().strip().upper()
        current_price = 0.0

        if symbol and symbol in self.market_data:
            current_price = self.market_data[symbol].get('last', 0.0)

        dialog = OrderDialog(self, symbol, current_price)
        if dialog.exec() == QDialog.Accepted:
            order_data = dialog.order_data

            try:
                result = self.trading_client.place_order(**order_data)

                if 'error' in result:
                    QMessageBox.critical(self, "Order Error", result['error'])
                else:
                    order_id = result.get('order_id', 'Unknown')
                    self.status_bar.showMessage(f"Advanced order placed: {order_id}", 5000)
                    self._refresh_orders()

            except Exception as e:
                logger.error(f"Error placing advanced order: {e}")
                QMessageBox.critical(self, "Order Error", str(e))


    def _close_selected_position(self):
        """Close the selected position"""
        current_row = self.positions_table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Selection Error", "Please select a position to close")
            return

        symbol_item = self.positions_table.item(current_row, 0)
        quantity_item = self.positions_table.item(current_row, 1)

        if not symbol_item or not quantity_item:
            return

        symbol = symbol_item.text()
        quantity = abs(int(float(quantity_item.text())))

        # Determine action (opposite of current position)
        current_quantity = int(float(quantity_item.text()))
        action = "SELL" if current_quantity > 0 else "BUY"

        # Confirm close
        reply = QMessageBox.question(
            self, "Confirm Close",
            f"Close position: {action} {quantity} shares of {symbol}?",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self._execute_order(symbol, action, quantity, "MKT")


    def _cancel_selected_order(self):
        """Cancel the selected order"""
        current_row = self.orders_table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "Selection Error", "Please select an order to cancel")
            return

        order_id_item = self.orders_table.item(current_row, 0)
        status_item = self.orders_table.item(current_row, 6)

        if not order_id_item or not status_item:
            return

        order_id = order_id_item.text()
        status = status_item.text()

        if status not in ['SUBMITTED', 'PENDING']:
            QMessageBox.warning(self, "Cancel Error", f"Cannot cancel order with status: {status}")
            return

        try:
            if hasattr(self.trading_client, 'cancel_order'):
                result = self.trading_client.cancel_order(order_id)

                if 'error' in result:
                    QMessageBox.critical(self, "Cancel Error", result['error'])
                else:
                    self.status_bar.showMessage(f"Order {order_id} cancelled", 5000)
                    self._refresh_orders()
            else:
                QMessageBox.information(self, "Info", "Order cancellation not supported")

        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            QMessageBox.critical(self, "Cancel Error", str(e))


    def _cancel_all_orders(self):
        """Cancel all pending orders"""
        pending_orders = [order_id for order_id, order in self.orders.items()
                          if order.get('status') in ['SUBMITTED', 'PENDING']]

        if not pending_orders:
            QMessageBox.information(self, "Info", "No pending orders to cancel")
            return

        reply = QMessageBox.question(
            self, "Confirm Cancel All",
            f"Cancel {len(pending_orders)} pending orders?",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            cancelled_count = 0

            for order_id in pending_orders:
                try:
                    if hasattr(self.trading_client, 'cancel_order'):
                        result = self.trading_client.cancel_order(order_id)
                        if 'error' not in result:
                            cancelled_count += 1
                except Exception as e:
                    logger.error(f"Error cancelling order {order_id}: {e}")

            self.status_bar.showMessage(f"Cancelled {cancelled_count} orders", 5000)
            self._refresh_orders()


    def _reset_paper_account(self):
        """Reset paper trading account (if in paper mode)"""
        if not self.is_paper_trading:
            QMessageBox.warning(self, "Error", "Account reset only available in paper trading mode")
            return

        reply = QMessageBox.question(
            self, "Confirm Reset",
            "This will reset your paper trading account to initial state. Continue?",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            try:
                if hasattr(self.trading_client, 'reset_account'):
                    self.trading_client.reset_account()
                    self.status_bar.showMessage("Paper account reset", 5000)

                    # Refresh all data
                    self._refresh_all_data()
                else:
                    QMessageBox.information(self, "Info", "Account reset not supported")

            except Exception as e:
                logger.error(f"Error resetting paper account: {e}")
                QMessageBox.critical(self, "Reset Error", str(e))


    def _refresh_all_data(self):
        """Refresh all data displays"""
        self._refresh_positions()
        self._refresh_orders()
        self._refresh_account_info()
        self.status_bar.showMessage("Data refreshed", 3000)


    def _reconnect(self):
        """Attempt to reconnect to the broker"""
        try:
            # This would depend on your specific reconnection logic
            self.status_bar.showMessage("Attempting to reconnect...", 3000)
            # Add reconnection logic here

        except Exception as e:
            logger.error(f"Error reconnecting: {e}")
            QMessageBox.critical(self, "Connection Error", str(e))


    def _disconnect(self):
        """Disconnect from the broker"""
        try:
            self.trading_client.disconnect()
            self._update_connection_status(False)
            self.status_bar.showMessage("Disconnected", 3000)

        except Exception as e:
            logger.error(f"Error disconnecting: {e}")


    # Cleanup methods

    def closeEvent(self, event):
        """Handle window close event"""
        # Stop timers
        if hasattr(self, 'positions_timer'):
            self.positions_timer.stop()
        if hasattr(self, 'orders_timer'):
            self.orders_timer.stop()
        if hasattr(self, 'account_timer'):
            self.account_timer.stop()

        # Unsubscribe from market data
        if hasattr(self.trading_client, 'unsubscribe_market_data') and self.watchlist:
            try:
                self.trading_client.unsubscribe_market_data(self.watchlist)
            except Exception as e:
                logger.error(f"Error unsubscribing from market data: {e}")

        # Accept the close event
        event.accept()


    def get_trading_client(self) -> IBKRTradingClient:
        """Get the trading client instance"""
        return self.trading_client


    def is_connected(self) -> bool:
        """Check if the trading client is connected"""
        return self.trading_client.is_connected()


    def get_positions_data(self) -> Dict[str, Any]:
        """Get current positions data"""
        return self.positions


    def get_orders_data(self) -> Dict[str, Any]:
        """Get current orders data"""
        return self.orders


    def add_symbols_to_watchlist(self, symbols: List[str]):
        """Add multiple symbols to watchlist"""
        for symbol in symbols:
            self._add_symbol_to_watchlist(symbol.upper())


    def set_market_data(self, symbol: str, data: Dict[str, Any]):
        """Manually set market data for a symbol (for testing)"""
        self.market_data[symbol] = data
        self._update_watchlist_display()
        self._update_market_data_display()  # ibkr/core/enhanced_main_window.py



class QullamaggieWindow(IBKRMainWindow):
    """Application-named IBKR main window entry point used by BrokerFactory."""

    def __init__(self, trader=None, real_ibkr_client=None, client_id=None, ib_client=None):
        trading_client = real_ibkr_client or trader
        trading_mode_value = getattr(trading_client, "connection_info", {}).get("trading_mode", TradingMode.PAPER.value)
        trading_mode = TradingMode(trading_mode_value) if isinstance(trading_mode_value, str) else trading_mode_value
        super().__init__(trading_client=trading_client, trading_mode=trading_mode)
