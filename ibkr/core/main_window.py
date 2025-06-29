# ibkr/core/main_window.py
"""
Main window implementation for IBKR mode of the swing trader application.
Provides the same interface as Kite but with IBKR-specific functionality.
"""

import logging
from typing import Optional, Dict, Any, List

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget,
    QComboBox, QLineEdit, QSpinBox, QMessageBox, QSplitter
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont

try:
    from ib_insync import Stock, Option, MarketOrder, LimitOrder
except ImportError:
    Stock = Option = MarketOrder = LimitOrder = None

from ibkr.utils.constants import (
    COLORS, ORDER_TYPE_MARKET, ORDER_TYPE_LIMIT,
    TRANSACTION_TYPE_BUY, TRANSACTION_TYPE_SELL
)
from ibkr.utils.data_fetcher import IBKRDataFetcher
from ibkr.utils.paper_trading_manager import PaperTradingManager

logger = logging.getLogger(__name__)


class SwingTraderWindow(QMainWindow):
    """
    Main window for IBKR swing trading.
    Maintains same interface as Kite version for consistency.
    """

    # Signals
    position_updated = Signal()
    order_placed = Signal(dict)
    connection_lost = Signal()

    def __init__(self, ib_client=None, trading_mode="paper", parent=None):
        super().__init__(parent)

        # Core components
        self.ib_client = ib_client
        self.trading_mode = trading_mode
        self.is_paper_trading = trading_mode == "paper" and ib_client is None

        # Initialize components
        if self.is_paper_trading:
            self.paper_manager = PaperTradingManager()
            self.data_fetcher = None
        else:
            self.paper_manager = None
            self.data_fetcher = IBKRDataFetcher(ib_client) if ib_client else None

        # Data storage
        self.positions = []
        self.orders = []
        self.watchlist = []

        # UI setup
        self.setWindowTitle(f"Swing Trader - IBKR ({trading_mode.title()} Mode)")
        self.setGeometry(100, 100, 1400, 800)

        self._setup_ui()
        self._apply_styles()

        # Setup timers
        self._setup_timers()

        # Load initial data
        if not self.is_paper_trading and self.ib_client:
            self._refresh_data()

    def _setup_ui(self):
        """Setup the main UI layout"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Header
        self._create_header(main_layout)

        # Main content area with splitter
        splitter = QSplitter(Qt.Horizontal)

        # Left panel - Watchlist and Order Entry
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self._create_watchlist_section(left_layout)
        self._create_order_entry_section(left_layout)

        splitter.addWidget(left_widget)

        # Right panel - Positions and Orders
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Tab widget for positions and orders
        self.tab_widget = QTabWidget()
        self.tab_widget.addTab(self._create_positions_tab(), "Positions")
        self.tab_widget.addTab(self._create_orders_tab(), "Orders")

        right_layout.addWidget(self.tab_widget)

        splitter.addWidget(right_widget)
        splitter.setSizes([500, 900])

        main_layout.addWidget(splitter)

        # Status bar
        self._create_status_bar()

    def _create_header(self, layout):
        """Create header section with account info"""
        header = QWidget()
        header.setObjectName("headerWidget")
        header_layout = QHBoxLayout(header)

        # Title
        title = QLabel("IBKR Swing Trader")
        title.setObjectName("appTitle")
        header_layout.addWidget(title)

        header_layout.addStretch()

        # Account info
        self.balance_label = QLabel("Balance: $0.00")
        self.balance_label.setObjectName("balanceLabel")
        header_layout.addWidget(self.balance_label)

        self.buying_power_label = QLabel("Buying Power: $0.00")
        self.buying_power_label.setObjectName("buyingPowerLabel")
        header_layout.addWidget(self.buying_power_label)

        # Connection status
        self.connection_status = QLabel("● Connected" if self.ib_client else "● Paper Trading")
        self.connection_status.setObjectName("connectionStatus")
        self.connection_status.setStyleSheet(
            f"color: {'#4CAF50' if self.ib_client else '#FF9800'};"
        )
        header_layout.addWidget(self.connection_status)

        # Refresh button
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("refreshButton")
        refresh_btn.clicked.connect(self._refresh_data)
        header_layout.addWidget(refresh_btn)

        layout.addWidget(header)

    def _create_watchlist_section(self, layout):
        """Create watchlist section"""
        watchlist_widget = QWidget()
        watchlist_widget.setObjectName("sectionWidget")
        watchlist_layout = QVBoxLayout(watchlist_widget)

        # Header
        header_layout = QHBoxLayout()

        label = QLabel("Watchlist")
        label.setObjectName("sectionTitle")
        header_layout.addWidget(label)

        header_layout.addStretch()

        # Add symbol input
        self.symbol_input = QLineEdit()
        self.symbol_input.setPlaceholderText("Add symbol...")
        self.symbol_input.setMaximumWidth(150)
        self.symbol_input.returnPressed.connect(self._add_to_watchlist)
        header_layout.addWidget(self.symbol_input)

        add_btn = QPushButton("+")
        add_btn.setObjectName("addButton")
        add_btn.setFixedSize(30, 30)
        add_btn.clicked.connect(self._add_to_watchlist)
        header_layout.addWidget(add_btn)

        watchlist_layout.addLayout(header_layout)

        # Watchlist table
        self.watchlist_table = QTableWidget()
        self.watchlist_table.setObjectName("dataTable")
        self.watchlist_table.setColumnCount(4)
        self.watchlist_table.setHorizontalHeaderLabels(["Symbol", "Last", "Change", "Volume"])
        self.watchlist_table.horizontalHeader().setStretchLastSection(True)
        self.watchlist_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.watchlist_table.itemClicked.connect(self._on_watchlist_item_clicked)

        watchlist_layout.addWidget(self.watchlist_table)
        layout.addWidget(watchlist_widget)

    def _create_order_entry_section(self, layout):
        """Create order entry section"""
        order_widget = QWidget()
        order_widget.setObjectName("sectionWidget")
        order_layout = QVBoxLayout(order_widget)

        # Title
        title = QLabel("Order Entry")
        title.setObjectName("sectionTitle")
        order_layout.addWidget(title)

        # Symbol
        symbol_layout = QHBoxLayout()
        symbol_layout.addWidget(QLabel("Symbol:"))
        self.order_symbol = QLineEdit()
        self.order_symbol.setPlaceholderText("Enter symbol...")
        symbol_layout.addWidget(self.order_symbol)
        order_layout.addLayout(symbol_layout)

        # Quantity
        qty_layout = QHBoxLayout()
        qty_layout.addWidget(QLabel("Quantity:"))
        self.order_quantity = QSpinBox()
        self.order_quantity.setRange(1, 10000)
        self.order_quantity.setValue(100)
        qty_layout.addWidget(self.order_quantity)
        order_layout.addLayout(qty_layout)

        # Order type
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Type:"))
        self.order_type = QComboBox()
        self.order_type.addItems(["MARKET", "LIMIT", "STOP", "STOP LIMIT"])
        self.order_type.currentTextChanged.connect(self._on_order_type_changed)
        type_layout.addWidget(self.order_type)
        order_layout.addLayout(type_layout)

        # Price (for limit orders)
        self.price_layout = QHBoxLayout()
        self.price_layout.addWidget(QLabel("Price:"))
        self.order_price = QLineEdit()
        self.order_price.setPlaceholderText("0.00")
        self.price_layout.addWidget(self.order_price)
        order_layout.addLayout(self.price_layout)
        self.price_layout.setEnabled(False)

        # Action buttons
        button_layout = QHBoxLayout()

        self.buy_btn = QPushButton("BUY")
        self.buy_btn.setObjectName("buyButton")
        self.buy_btn.clicked.connect(lambda: self._place_order("BUY"))
        button_layout.addWidget(self.buy_btn)

        self.sell_btn = QPushButton("SELL")
        self.sell_btn.setObjectName("sellButton")
        self.sell_btn.clicked.connect(lambda: self._place_order("SELL"))
        button_layout.addWidget(self.sell_btn)

        order_layout.addLayout(button_layout)
        order_layout.addStretch()

        layout.addWidget(order_widget)

    def _create_positions_tab(self):
        """Create positions tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Positions table
        self.positions_table = QTableWidget()
        self.positions_table.setObjectName("dataTable")
        self.positions_table.setColumnCount(7)
        self.positions_table.setHorizontalHeaderLabels([
            "Symbol", "Quantity", "Avg Price", "Current Price",
            "P&L", "P&L %", "Actions"
        ])
        self.positions_table.horizontalHeader().setStretchLastSection(True)

        layout.addWidget(self.positions_table)
        return widget

    def _create_orders_tab(self):
        """Create orders tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        # Orders table
        self.orders_table = QTableWidget()
        self.orders_table.setObjectName("dataTable")
        self.orders_table.setColumnCount(7)
        self.orders_table.setHorizontalHeaderLabels([
            "Order ID", "Symbol", "Type", "Quantity",
            "Price", "Status", "Actions"
        ])
        self.orders_table.horizontalHeader().setStretchLastSection(True)

        layout.addWidget(self.orders_table)
        return widget

    def _create_status_bar(self):
        """Create status bar"""
        self.status_bar = self.statusBar()
        self.status_bar.showMessage("Ready")

    def _setup_timers(self):
        """Setup refresh timers"""
        # Data refresh timer
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self._refresh_data)
        self.refresh_timer.start(30000)  # 30 seconds

        # Connection check timer
        if self.ib_client:
            self.connection_timer = QTimer()
            self.connection_timer.timeout.connect(self._check_connection)
            self.connection_timer.start(5000)  # 5 seconds

    def _on_order_type_changed(self, order_type):
        """Handle order type change"""
        is_limit = order_type in ["LIMIT", "STOP LIMIT"]
        self.price_layout.setEnabled(is_limit)
        if not is_limit:
            self.order_price.clear()

    def _on_watchlist_item_clicked(self, item):
        """Handle watchlist item click"""
        row = item.row()
        symbol_item = self.watchlist_table.item(row, 0)
        if symbol_item:
            self.order_symbol.setText(symbol_item.text())

    def _add_to_watchlist(self):
        """Add symbol to watchlist"""
        symbol = self.symbol_input.text().strip().upper()
        if not symbol:
            return

        # Check if already in watchlist
        for i in range(self.watchlist_table.rowCount()):
            if self.watchlist_table.item(i, 0).text() == symbol:
                self.symbol_input.clear()
                return

        # Add to watchlist
        row = self.watchlist_table.rowCount()
        self.watchlist_table.insertRow(row)
        self.watchlist_table.setItem(row, 0, QTableWidgetItem(symbol))
        self.watchlist_table.setItem(row, 1, QTableWidgetItem("--"))
        self.watchlist_table.setItem(row, 2, QTableWidgetItem("--"))
        self.watchlist_table.setItem(row, 3, QTableWidgetItem("--"))

        self.watchlist.append(symbol)
        self.symbol_input.clear()

        # Fetch quote if connected
        if self.data_fetcher:
            self._update_watchlist_quotes()

    def _place_order(self, action: str):
        """Place an order"""
        symbol = self.order_symbol.text().strip().upper()
        if not symbol:
            QMessageBox.warning(self, "Input Error", "Please enter a symbol")
            return

        quantity = self.order_quantity.value()
        order_type = self.order_type.currentText()

        # Get price for limit orders
        price = None
        if order_type in ["LIMIT", "STOP LIMIT"]:
            try:
                price = float(self.order_price.text())
            except ValueError:
                QMessageBox.warning(self, "Input Error", "Please enter a valid price")
                return

        # Confirm order
        msg = f"{action} {quantity} shares of {symbol}"
        if price:
            msg += f" at ${price:.2f}"
        msg += f"\nOrder Type: {order_type}"

        reply = QMessageBox.question(self, "Confirm Order", msg)
        if reply != QMessageBox.Yes:
            return

        try:
            if self.is_paper_trading:
                # Paper trading order
                order = self.paper_manager.place_order(
                    symbol=symbol,
                    quantity=quantity,
                    order_type=order_type,
                    action=action,
                    price=price
                )
                self.status_bar.showMessage(f"Paper order placed: {order['order_id']}")
            else:
                # Real order through IBKR
                contract = Stock(symbol, 'SMART', 'USD')

                if order_type == "MARKET":
                    order = MarketOrder(action, quantity)
                elif order_type == "LIMIT":
                    order = LimitOrder(action, quantity, price)
                else:
                    QMessageBox.warning(self, "Not Implemented",
                                        f"{order_type} orders not yet implemented")
                    return

                trade = self.ib_client.placeOrder(contract, order)
                self.status_bar.showMessage(f"Order placed: {trade.order.orderId}")

            # Clear form
            self.order_symbol.clear()
            self.order_price.clear()

            # Refresh orders
            self._refresh_orders()

            # Emit signal
            self.order_placed.emit({
                'symbol': symbol,
                'quantity': quantity,
                'action': action,
                'order_type': order_type,
                'price': price
            })

        except Exception as e:
            logger.error(f"Error placing order: {e}")
            QMessageBox.critical(self, "Order Error", f"Failed to place order: {str(e)}")

    def _refresh_data(self):
        """Refresh all data"""
        try:
            self._refresh_positions()
            self._refresh_orders()
            self._update_account_info()
            self._update_watchlist_quotes()
            self.status_bar.showMessage("Data refreshed")
        except Exception as e:
            logger.error(f"Error refreshing data: {e}")
            self.status_bar.showMessage(f"Refresh error: {str(e)}")

    def _refresh_positions(self):
        """Refresh positions table"""
        try:
            if self.is_paper_trading:
                self.positions = self.paper_manager.get_positions()
            elif self.data_fetcher:
                self.positions = self.data_fetcher.get_positions()
            else:
                self.positions = []

            # Update table
            self.positions_table.setRowCount(0)

            for position in self.positions:
                row = self.positions_table.rowCount()
                self.positions_table.insertRow(row)

                # Symbol
                self.positions_table.setItem(row, 0, QTableWidgetItem(position['symbol']))

                # Quantity
                qty_item = QTableWidgetItem(str(position['quantity']))
                qty_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.positions_table.setItem(row, 1, qty_item)

                # Avg Price
                avg_price = position.get('average_price', 0)
                avg_item = QTableWidgetItem(f"${avg_price:.2f}")
                avg_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.positions_table.setItem(row, 2, avg_item)

                # Current Price (mock for now)
                current_price = position.get('current_price', avg_price)
                current_item = QTableWidgetItem(f"${current_price:.2f}")
                current_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.positions_table.setItem(row, 3, current_item)

                # P&L
                pnl = (current_price - avg_price) * position['quantity']
                pnl_item = QTableWidgetItem(f"${pnl:,.2f}")
                pnl_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if pnl >= 0:
                    pnl_item.setForeground(Qt.green)
                else:
                    pnl_item.setForeground(Qt.red)
                self.positions_table.setItem(row, 4, pnl_item)

                # P&L %
                pnl_pct = ((current_price - avg_price) / avg_price * 100) if avg_price > 0 else 0
                pnl_pct_item = QTableWidgetItem(f"{pnl_pct:.2f}%")
                pnl_pct_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if pnl_pct >= 0:
                    pnl_pct_item.setForeground(Qt.green)
                else:
                    pnl_pct_item.setForeground(Qt.red)
                self.positions_table.setItem(row, 5, pnl_pct_item)

                # Actions
                close_btn = QPushButton("Close")
                close_btn.setObjectName("actionButton")
                close_btn.clicked.connect(lambda _, s=position['symbol']: self._close_position(s))
                self.positions_table.setCellWidget(row, 6, close_btn)

            self.position_updated.emit()

        except Exception as e:
            logger.error(f"Error refreshing positions: {e}")

    def _refresh_orders(self):
        """Refresh orders table"""
        try:
            if self.is_paper_trading:
                self.orders = self.paper_manager.get_orders()
            elif self.data_fetcher:
                self.orders = self.data_fetcher.get_orders()
            else:
                self.orders = []

            # Update table
            self.orders_table.setRowCount(0)

            for order in self.orders:
                row = self.orders_table.rowCount()
                self.orders_table.insertRow(row)

                # Order ID
                self.orders_table.setItem(row, 0, QTableWidgetItem(str(order.get('order_id', ''))))

                # Symbol
                self.orders_table.setItem(row, 1, QTableWidgetItem(order.get('symbol', '')))

                # Type
                self.orders_table.setItem(row, 2, QTableWidgetItem(order.get('order_type', '')))

                # Quantity
                qty_item = QTableWidgetItem(str(order.get('quantity', 0)))
                qty_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.orders_table.setItem(row, 3, qty_item)

                # Price
                price = order.get('limit_price') or order.get('price', '--')
                if isinstance(price, (int, float)):
                    price_text = f"${price:.2f}"
                else:
                    price_text = str(price)
                price_item = QTableWidgetItem(price_text)
                price_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.orders_table.setItem(row, 4, price_item)

                # Status
                status = order.get('status', 'UNKNOWN')
                status_item = QTableWidgetItem(status)
                if status == 'FILLED':
                    status_item.setForeground(Qt.green)
                elif status in ['CANCELLED', 'REJECTED']:
                    status_item.setForeground(Qt.red)
                else:
                    status_item.setForeground(Qt.yellow)
                self.orders_table.setItem(row, 5, status_item)

                # Actions
                if status not in ['FILLED', 'CANCELLED', 'REJECTED']:
                    cancel_btn = QPushButton("Cancel")
                    cancel_btn.setObjectName("cancelButton")
                    cancel_btn.clicked.connect(
                        lambda _, oid=order.get('order_id'): self._cancel_order(oid)
                    )
                    self.orders_table.setCellWidget(row, 6, cancel_btn)

        except Exception as e:
            logger.error(f"Error refreshing orders: {e}")

    def _update_account_info(self):
        """Update account information in header"""
        try:
            if self.is_paper_trading:
                info = self.paper_manager.get_account_info()
                self.balance_label.setText(f"Balance: ${info['balance']:,.2f}")
                self.buying_power_label.setText(f"Buying Power: ${info['buying_power']:,.2f}")
            elif self.ib_client and self.ib_client.isConnected():
                # Get account summary
                account_values = self.ib_client.accountSummary()

                # Extract key values
                balance = 0
                buying_power = 0

                for item in account_values:
                    if item.tag == 'TotalCashValue':
                        balance = float(item.value)
                    elif item.tag == 'BuyingPower':
                        buying_power = float(item.value)

                self.balance_label.setText(f"Balance: ${balance:,.2f}")
                self.buying_power_label.setText(f"Buying Power: ${buying_power:,.2f}")

        except Exception as e:
            logger.error(f"Error updating account info: {e}")

    def _update_watchlist_quotes(self):
        """Update watchlist quotes"""
        if not self.data_fetcher:
            return

        try:
            for row in range(self.watchlist_table.rowCount()):
                symbol_item = self.watchlist_table.item(row, 0)
                if symbol_item:
                    symbol = symbol_item.text()

                    # In real implementation, would fetch async
                    # For now, just show placeholder
                    self.watchlist_table.item(row, 1).setText("--")
                    self.watchlist_table.item(row, 2).setText("--")
                    self.watchlist_table.item(row, 3).setText("--")

        except Exception as e:
            logger.error(f"Error updating watchlist: {e}")

    def _check_connection(self):
        """Check IBKR connection status"""
        try:
            if self.ib_client and not self.ib_client.isConnected():
                self.connection_status.setText("● Disconnected")
                self.connection_status.setStyleSheet("color: #F44336;")
                self.connection_lost.emit()
            else:
                self.connection_status.setText("● Connected")
                self.connection_status.setStyleSheet("color: #4CAF50;")
        except Exception as e:
            logger.error(f"Error checking connection: {e}")

    def _close_position(self, symbol: str):
        """Close a position"""
        # Find position
        position = next((p for p in self.positions if p['symbol'] == symbol), None)
        if not position:
            return

        # Confirm
        reply = QMessageBox.question(
            self, "Close Position",
            f"Close position in {symbol}?\nQuantity: {position['quantity']}"
        )

        if reply == QMessageBox.Yes:
            # Place sell order
            self.order_symbol.setText(symbol)
            self.order_quantity.setValue(position['quantity'])
            self.order_type.setCurrentText("MARKET")
            self._place_order("SELL")

    def _cancel_order(self, order_id):
        """Cancel an order"""
        reply = QMessageBox.question(
            self, "Cancel Order",
            f"Cancel order {order_id}?"
        )

        if reply == QMessageBox.Yes:
            try:
                if self.is_paper_trading:
                    # Cancel paper order
                    for order in self.paper_manager.orders:
                        if order['order_id'] == order_id:
                            order['status'] = 'CANCELLED'
                            break
                    self.paper_manager._save_state()
                else:
                    # Cancel real order
                    self.ib_client.cancelOrder(order_id)

                self.status_bar.showMessage(f"Order {order_id} cancelled")
                self._refresh_orders()

            except Exception as e:
                logger.error(f"Error cancelling order: {e}")
                QMessageBox.warning(self, "Cancel Error", f"Failed to cancel order: {str(e)}")

    def _apply_styles(self):
        """Apply custom styles"""
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {COLORS['background']};
            }}

            QWidget#headerWidget {{
                background-color: {COLORS['secondary_background']};
                border-radius: 5px;
                padding: 10px;
                margin-bottom: 10px;
            }}

            QWidget#sectionWidget {{
                background-color: {COLORS['secondary_background']};
                border-radius: 5px;
                padding: 10px;
                margin: 5px;
            }}

            QLabel#appTitle {{
                font-size: 20px;
                font-weight: bold;
                color: {COLORS['text']};
            }}

            QLabel#sectionTitle {{
                font-size: 16px;
                font-weight: bold;
                color: {COLORS['text']};
                margin-bottom: 10px;
            }}

            QLabel#balanceLabel, QLabel#buyingPowerLabel {{
                font-size: 14px;
                color: {COLORS['text']};
                margin: 0 10px;
            }}

            QLabel#connectionStatus {{
                font-size: 14px;
                font-weight: bold;
                margin: 0 10px;
            }}

            QPushButton {{
                background-color: {COLORS['primary']};
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: bold;
            }}

            QPushButton:hover {{
                background-color: #1565C0;
            }}

            QPushButton#buyButton {{
                background-color: {COLORS['success']};
            }}

            QPushButton#buyButton:hover {{
                background-color: #2E7D32;
            }}

            QPushButton#sellButton {{
                background-color: {COLORS['warning']};
            }}

            QPushButton#sellButton:hover {{
                background-color: #E65100;
            }}

            QPushButton#cancelButton {{
                background-color: {COLORS['secondary']};
                padding: 4px 12px;
            }}

            QPushButton#cancelButton:hover {{
                background-color: #B71C1C;
            }}

            QPushButton#actionButton {{
                padding: 4px 12px;
            }}

            QPushButton#addButton {{
                font-size: 18px;
                padding: 0;
            }}

            QPushButton#refreshButton {{
                padding: 6px 12px;
            }}

            QTableWidget {{
                background-color: {COLORS['secondary_background']};
                border: none;
                gridline-color: #424242;
                color: {COLORS['text']};
            }}

            QTableWidget::item {{
                padding: 5px;
            }}

            QTableWidget::item:selected {{
                background-color: {COLORS['primary']};
            }}

            QHeaderView::section {{
                background-color: #424242;
                color: {COLORS['text']};
                padding: 8px;
                border: none;
                font-weight: bold;
            }}

            QLineEdit, QComboBox, QSpinBox {{
                background-color: #424242;
                border: 1px solid #616161;
                border-radius: 4px;
                padding: 6px;
                color: {COLORS['text']};
            }}

            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
                border-color: {COLORS['primary']};
            }}

            QTabWidget::pane {{
                background-color: {COLORS['secondary_background']};
                border: none;
            }}

            QTabBar::tab {{
                background-color: #424242;
                color: {COLORS['text']};
                padding: 8px 16px;
                margin-right: 2px;
            }}

            QTabBar::tab:selected {{
                background-color: {COLORS['primary']};
            }}

            QStatusBar {{
                background-color: {COLORS['secondary_background']};
                color: {COLORS['text']};
            }}
        """)

    def closeEvent(self, event):
        """Handle window close event"""
        if self.ib_client:
            try:
                self.ib_client.disconnect()
            except:
                pass
        event.accept()