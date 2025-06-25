import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QComboBox,
    QLineEdit, QDateEdit, QCheckBox, QSplitter, QGroupBox, QGridLayout
)
from PySide6.QtCore import Qt, Signal, QDate, QTimer
from PySide6.QtGui import QColor, QMouseEvent, QFont

logger = logging.getLogger(__name__)


class FilterWidget(QWidget):
    """Widget for filtering order history."""

    filter_changed = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("filterWidget")
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(10)

        # Filter group
        filter_group = QGroupBox("Filters")
        filter_group.setObjectName("filterGroup")
        filter_layout = QGridLayout(filter_group)
        filter_layout.setSpacing(8)

        # Symbol filter
        filter_layout.addWidget(QLabel("Symbol:"), 0, 0)
        self.symbol_filter = QLineEdit()
        self.symbol_filter.setPlaceholderText("Enter symbol (e.g., RELIANCE)")
        self.symbol_filter.textChanged.connect(self.filter_changed.emit)
        filter_layout.addWidget(self.symbol_filter, 0, 1)

        # Status filter
        filter_layout.addWidget(QLabel("Status:"), 0, 2)
        self.status_filter = QComboBox()
        self.status_filter.addItems(["All", "COMPLETE", "CANCELLED", "OPEN", "PENDING_EXECUTION"])
        self.status_filter.currentTextChanged.connect(self.filter_changed.emit)
        filter_layout.addWidget(self.status_filter, 0, 3)

        # Transaction type filter
        filter_layout.addWidget(QLabel("Type:"), 1, 0)
        self.type_filter = QComboBox()
        self.type_filter.addItems(["All", "BUY", "SELL"])
        self.type_filter.currentTextChanged.connect(self.filter_changed.emit)
        filter_layout.addWidget(self.type_filter, 1, 1)

        # Date range
        filter_layout.addWidget(QLabel("From:"), 1, 2)
        self.date_from = QDateEdit()
        self.date_from.setDate(QDate.currentDate().addDays(-30))
        self.date_from.setCalendarPopup(True)
        self.date_from.dateChanged.connect(self.filter_changed.emit)
        filter_layout.addWidget(self.date_from, 1, 3)

        # Clear filters button
        clear_btn = QPushButton("Clear Filters")
        clear_btn.setObjectName("clearButton")
        clear_btn.clicked.connect(self._clear_filters)
        filter_layout.addWidget(clear_btn, 2, 0, 1, 2)

        # Show only today's orders
        self.today_only = QCheckBox("Today's Orders Only")
        self.today_only.stateChanged.connect(self.filter_changed.emit)
        filter_layout.addWidget(self.today_only, 2, 2, 1, 2)

        layout.addWidget(filter_group)

    def _clear_filters(self):
        """Clear all filters."""
        self.symbol_filter.clear()
        self.status_filter.setCurrentIndex(0)
        self.type_filter.setCurrentIndex(0)
        self.date_from.setDate(QDate.currentDate().addDays(-30))
        self.today_only.setChecked(False)
        self.filter_changed.emit()

    def get_filters(self) -> Dict[str, Any]:
        """Get current filter values."""
        return {
            'symbol': self.symbol_filter.text().strip().upper(),
            'status': self.status_filter.currentText() if self.status_filter.currentText() != "All" else None,
            'transaction_type': self.type_filter.currentText() if self.type_filter.currentText() != "All" else None,
            'date_from': self.date_from.date().toPython(),
            'today_only': self.today_only.isChecked()
        }


class OrderHistoryTable(QTableWidget):
    """Enhanced table widget for displaying order history with improved functionality."""

    order_selected = Signal(dict)

    def __init__(self):
        super().__init__()
        self.setColumnCount(8)
        self.setHorizontalHeaderLabels([
            "Time", "Symbol", "Type", "Qty", "Price", "Avg Price", "Status", "Order ID"
        ])
        self._orders_data = []
        self._setup_table()

    def _setup_table(self):
        """Configure table appearance and behavior."""
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setSortingEnabled(True)

        # Set up header
        header = self.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Symbol
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)  # Order ID

        # Connect selection signal
        self.itemSelectionChanged.connect(self._on_selection_changed)

    def update_orders(self, orders: List[Dict], filters: Dict[str, Any] = None):
        """Update table with filtered orders."""
        self._orders_data = orders
        filtered_orders = self._apply_filters(orders, filters) if filters else orders

        self.setRowCount(0)
        sorted_orders = sorted(filtered_orders, key=lambda x: x.get("order_timestamp", ""), reverse=True)

        for order in sorted_orders:
            row_position = self.rowCount()
            self.insertRow(row_position)
            self._populate_row(row_position, order)

        # Auto-resize columns after population
        self.resizeColumnsToContents()

    def _apply_filters(self, orders: List[Dict], filters: Dict[str, Any]) -> List[Dict]:
        """Apply filters to orders list."""
        filtered = orders.copy()

        # Today only filter
        if filters.get('today_only'):
            today = datetime.now().strftime('%Y-%m-%d')
            filtered = [o for o in filtered if o.get('order_timestamp', '').startswith(today)]

        # Symbol filter
        if filters.get('symbol'):
            symbol = filters['symbol']
            filtered = [o for o in filtered if symbol in o.get('tradingsymbol', '').upper()]

        # Status filter
        if filters.get('status'):
            status = filters['status']
            filtered = [o for o in filtered if o.get('status', '').upper() == status]

        # Transaction type filter
        if filters.get('transaction_type'):
            trans_type = filters['transaction_type']
            filtered = [o for o in filtered if o.get('transaction_type', '').upper() == trans_type]

        # Date filter
        if filters.get('date_from'):
            date_from = filters['date_from']
            filtered = [o for o in filtered
                        if datetime.strptime(o.get('order_timestamp', '')[:10], '%Y-%m-%d').date() >= date_from]

        return filtered

    def _populate_row(self, row: int, order: Dict[str, Any]):
        """Populate a single row with order data."""
        # Column 0: Timestamp (formatted)
        timestamp = order.get("order_timestamp", "")
        if timestamp:
            try:
                dt = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
                formatted_time = dt.strftime('%H:%M:%S')
            except:
                formatted_time = timestamp
        else:
            formatted_time = ""
        time_item = QTableWidgetItem(formatted_time)
        time_item.setToolTip(timestamp)
        self.setItem(row, 0, time_item)

        # Column 1: Symbol
        symbol_item = QTableWidgetItem(order.get("tradingsymbol", ""))
        symbol_item.setFont(QFont("", 0, QFont.Weight.Bold))
        self.setItem(row, 1, symbol_item)

        # Column 2: Transaction Type
        trans_type = order.get("transaction_type", "N/A").upper()
        type_item = QTableWidgetItem(trans_type)
        type_item.setObjectName(f"{trans_type.lower()}Tag")
        if trans_type == "BUY":
            type_item.setForeground(QColor("#00b894"))
        elif trans_type == "SELL":
            type_item.setForeground(QColor("#d63031"))
        self.setItem(row, 2, type_item)

        # Column 3: Quantity
        qty_item = QTableWidgetItem(str(order.get("quantity", 0)))
        qty_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setItem(row, 3, qty_item)

        # Column 4: Order Price
        price = order.get('price', 0.0) or 0.0
        price_text = f"₹{price:.2f}" if price > 0 else "Market"
        price_item = QTableWidgetItem(price_text)
        price_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setItem(row, 4, price_item)

        # Column 5: Average Price
        avg_price = order.get('average_price', 0.0) or 0.0
        avg_text = f"₹{avg_price:.2f}" if avg_price > 0 else "-"
        avg_item = QTableWidgetItem(avg_text)
        avg_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setItem(row, 5, avg_item)

        # Column 6: Status
        status = order.get("status", "").upper()
        status_item = QTableWidgetItem(status)
        status_item.setObjectName(f"{status.lower()}Status")

        # Set status colors
        if status == "COMPLETE":
            status_item.setForeground(QColor("#0984e3"))
        elif status == "CANCELLED":
            status_item.setForeground(QColor("#b2bec3"))
        elif status in ["OPEN", "PENDING_EXECUTION", "TRIGGER PENDING"]:
            status_item.setForeground(QColor("#fdcb6e"))
        else:
            status_item.setForeground(QColor("#ffffff"))

        status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setItem(row, 6, status_item)

        # Column 7: Order ID
        order_id = order.get("order_id", "")
        id_item = QTableWidgetItem(order_id)
        id_item.setFont(QFont("Consolas", 8))
        id_item.setToolTip(f"Order ID: {order_id}")
        self.setItem(row, 7, id_item)

    def _on_selection_changed(self):
        """Handle row selection change."""
        current_row = self.currentRow()
        if current_row >= 0 and current_row < len(self._orders_data):
            selected_order = self._orders_data[current_row]
            self.order_selected.emit(selected_order)

    def get_selected_order(self) -> Optional[Dict]:
        """Get currently selected order."""
        current_row = self.currentRow()
        if current_row >= 0 and current_row < len(self._orders_data):
            return self._orders_data[current_row]
        return None


class OrderSummaryWidget(QWidget):
    """Widget displaying order statistics and summary."""

    def __init__(self):
        super().__init__()
        self.setObjectName("summaryWidget")
        self._setup_ui()
        self._stats = {}

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(10)

        # Summary group
        summary_group = QGroupBox("Order Summary")
        summary_group.setObjectName("summaryGroup")
        summary_layout = QGridLayout(summary_group)
        summary_layout.setSpacing(8)

        # Statistics labels
        self.total_orders_label = QLabel("0")
        self.completed_orders_label = QLabel("0")
        self.cancelled_orders_label = QLabel("0")
        self.pending_orders_label = QLabel("0")
        self.total_volume_label = QLabel("₹0")
        self.avg_order_size_label = QLabel("₹0")

        # Add to layout with labels
        summary_layout.addWidget(QLabel("Total Orders:"), 0, 0)
        summary_layout.addWidget(self.total_orders_label, 0, 1)

        summary_layout.addWidget(QLabel("Completed:"), 1, 0)
        summary_layout.addWidget(self.completed_orders_label, 1, 1)

        summary_layout.addWidget(QLabel("Cancelled:"), 2, 0)
        summary_layout.addWidget(self.cancelled_orders_label, 2, 1)

        summary_layout.addWidget(QLabel("Pending:"), 3, 0)
        summary_layout.addWidget(self.pending_orders_label, 3, 1)

        summary_layout.addWidget(QLabel("Total Volume:"), 4, 0)
        summary_layout.addWidget(self.total_volume_label, 4, 1)

        summary_layout.addWidget(QLabel("Avg Order Size:"), 5, 0)
        summary_layout.addWidget(self.avg_order_size_label, 5, 1)

        layout.addWidget(summary_group)
        layout.addStretch()

    def update_summary(self, orders: List[Dict]):
        """Update summary with order data."""
        if not orders:
            self._reset_summary()
            return

        # Calculate statistics
        total_orders = len(orders)
        completed = len([o for o in orders if o.get('status') == 'COMPLETE'])
        cancelled = len([o for o in orders if o.get('status') == 'CANCELLED'])
        pending = len([o for o in orders if o.get('status') in ['OPEN', 'PENDING_EXECUTION']])

        # Calculate volume (only completed orders)
        total_volume = sum(
            (o.get('filled_quantity', 0) or o.get('quantity', 0)) * (o.get('average_price', 0) or o.get('price', 0))
            for o in orders if o.get('status') == 'COMPLETE'
        )

        avg_order_size = total_volume / completed if completed > 0 else 0

        # Update labels
        self.total_orders_label.setText(str(total_orders))
        self.completed_orders_label.setText(str(completed))
        self.cancelled_orders_label.setText(str(cancelled))
        self.pending_orders_label.setText(str(pending))
        self.total_volume_label.setText(f"₹{total_volume:,.0f}")
        self.avg_order_size_label.setText(f"₹{avg_order_size:,.0f}")

        # Store stats for export
        self._stats = {
            'total_orders': total_orders,
            'completed_orders': completed,
            'cancelled_orders': cancelled,
            'pending_orders': pending,
            'total_volume': total_volume,
            'avg_order_size': avg_order_size
        }

    def _reset_summary(self):
        """Reset all summary labels."""
        self.total_orders_label.setText("0")
        self.completed_orders_label.setText("0")
        self.cancelled_orders_label.setText("0")
        self.pending_orders_label.setText("0")
        self.total_volume_label.setText("₹0")
        self.avg_order_size_label.setText("₹0")
        self._stats = {}

    def get_statistics(self) -> Dict:
        """Get current statistics."""
        return self._stats.copy()


class OrderHistoryDialog(QDialog):
    """
    Comprehensive order history dialog for the swing trading app.
    Displays orders from TradeLogger with filtering, summary, and export capabilities.
    """

    refresh_requested = Signal()
    export_requested = Signal(dict)  # Emits current filter settings

    def __init__(self, trade_logger, parent=None):
        super().__init__(parent)
        self.trade_logger = trade_logger
        self._drag_pos = None
        self._orders_data = []

        self._setup_window()
        self._setup_ui()
        self._apply_styles()
        self._connect_signals()

        # Auto-refresh timer
        self.auto_refresh_timer = QTimer(self)
        self.auto_refresh_timer.timeout.connect(self._auto_refresh)
        self.auto_refresh_timer.start(30000)  # Refresh every 30 seconds

        # Load initial data
        self._refresh_data()

    def _setup_window(self):
        """Initialize window properties."""
        self.setWindowTitle("Order History - Swing Trader")
        self.setMinimumSize(1200, 700)
        self.resize(1400, 800)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def _setup_ui(self):
        """Build the main UI layout."""
        # Main container
        container = QWidget(self)
        container.setObjectName("mainContainer")

        # Enable window dragging
        container.mousePressEvent = self._handle_mouse_press
        container.mouseMoveEvent = self._handle_mouse_move
        container.mouseReleaseEvent = self._handle_mouse_release

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(20, 15, 20, 20)
        container_layout.setSpacing(15)

        # Header
        container_layout.addLayout(self._create_header())

        # Main content area with splitter
        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        content_splitter.setObjectName("contentSplitter")

        # Left panel (filters and summary)
        left_panel = QWidget()
        left_panel.setFixedWidth(300)
        left_panel_layout = QVBoxLayout(left_panel)
        left_panel_layout.setContentsMargins(0, 0, 10, 0)

        # Filter widget
        self.filter_widget = FilterWidget()
        left_panel_layout.addWidget(self.filter_widget)

        # Summary widget
        self.summary_widget = OrderSummaryWidget()
        left_panel_layout.addWidget(self.summary_widget)

        # Right panel (table)
        right_panel = QWidget()
        right_panel_layout = QVBoxLayout(right_panel)
        right_panel_layout.setContentsMargins(10, 0, 0, 0)

        self.orders_table = OrderHistoryTable()
        right_panel_layout.addWidget(self.orders_table)

        content_splitter.addWidget(left_panel)
        content_splitter.addWidget(right_panel)
        content_splitter.setSizes([300, 900])

        container_layout.addWidget(content_splitter, 1)

        # Footer
        container_layout.addLayout(self._create_footer())

    def _create_header(self) -> QHBoxLayout:
        """Create dialog header."""
        header_layout = QHBoxLayout()

        # Title section
        title_layout = QVBoxLayout()
        title_layout.setSpacing(2)

        title = QLabel("Order History")
        title.setObjectName("dialogTitle")

        subtitle = QLabel("View and analyze your trading orders with advanced filtering")
        subtitle.setObjectName("subtitleLabel")

        title_layout.addWidget(title)
        title_layout.addWidget(subtitle)

        # Status indicator
        self.status_label = QLabel("Loading...")
        self.status_label.setObjectName("statusLabel")

        # Close button
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeButton")
        close_btn.clicked.connect(self.close)

        header_layout.addLayout(title_layout)
        header_layout.addStretch()
        header_layout.addWidget(self.status_label)
        header_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignTop)

        return header_layout

    def _create_footer(self) -> QHBoxLayout:
        """Create dialog footer."""
        footer_layout = QHBoxLayout()
        footer_layout.setContentsMargins(0, 10, 0, 0)

        # Count label
        self.count_label = QLabel("0 orders displayed")
        self.count_label.setObjectName("footerLabel")

        # Action buttons
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.setObjectName("actionButton")
        refresh_btn.clicked.connect(self._refresh_data)

        export_btn = QPushButton("📊 Export")
        export_btn.setObjectName("actionButton")
        export_btn.clicked.connect(self._export_data)

        clear_btn = QPushButton("🗑️ Clear Filters")
        clear_btn.setObjectName("secondaryButton")
        clear_btn.clicked.connect(self.filter_widget._clear_filters)

        footer_layout.addWidget(self.count_label)
        footer_layout.addStretch()
        footer_layout.addWidget(clear_btn)
        footer_layout.addWidget(export_btn)
        footer_layout.addWidget(refresh_btn)

        return footer_layout

    def _connect_signals(self):
        """Connect internal signals."""
        self.filter_widget.filter_changed.connect(self._apply_filters)
        self.orders_table.order_selected.connect(self._on_order_selected)

    def _refresh_data(self):
        """Refresh order data from trade logger."""
        try:
            self.status_label.setText("Loading orders...")

            # Get orders from trade logger
            self._orders_data = self.trade_logger.get_all_orders(limit=1000)

            # Apply current filters
            self._apply_filters()

            # Update status
            total_count = len(self._orders_data)
            self.status_label.setText(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")

            logger.info(f"Loaded {total_count} orders from trade logger")

        except Exception as e:
            logger.error(f"Failed to refresh order data: {e}")
            self.status_label.setText("Error loading data")

    def _auto_refresh(self):
        """Auto-refresh data periodically."""
        if self.isVisible():
            self._refresh_data()

    def _apply_filters(self):
        """Apply current filters to the order data."""
        filters = self.filter_widget.get_filters()

        # Update table with filtered data
        self.orders_table.update_orders(self._orders_data, filters)

        # Update summary with all data (not filtered for summary)
        self.summary_widget.update_summary(self._orders_data)

        # Update count label
        displayed_count = self.orders_table.rowCount()
        total_count = len(self._orders_data)

        if displayed_count == total_count:
            self.count_label.setText(f"{total_count} orders displayed")
        else:
            self.count_label.setText(f"{displayed_count} of {total_count} orders displayed")

    def _on_order_selected(self, order: Dict):
        """Handle order selection."""
        # You can add detailed order view here
        logger.info(f"Selected order: {order.get('order_id')} - {order.get('tradingsymbol')}")

    def _export_data(self):
        """Export current filtered data."""
        filters = self.filter_widget.get_filters()
        stats = self.summary_widget.get_statistics()

        export_data = {
            'filters': filters,
            'statistics': stats,
            'orders': self.orders_table._orders_data,
            'export_timestamp': datetime.now().isoformat()
        }

        self.export_requested.emit(export_data)
        logger.info("Order data export requested")

    def _apply_styles(self):
        """Apply comprehensive dark theme styles."""
        self.setStyleSheet("""
            QWidget#mainContainer {
                background-color: #0a0a0a;
                border: 2px solid #202020;
                border-radius: 12px;
                font-family: "Segoe UI", sans-serif;
            }

            QLabel#dialogTitle {
                color: #ffffff;
                font-size: 20px;
                font-weight: 700;
                margin-bottom: 2px;
            }

            QLabel#subtitleLabel {
                color: #8a8a9e;
                font-size: 13px;
                font-weight: 400;
            }

            QLabel#statusLabel {
                color: #6c7293;
                font-size: 11px;
                background-color: #1a1a2e;
                padding: 4px 8px;
                border-radius: 4px;
                border: 1px solid #2a2a4a;
            }

            QLabel#footerLabel {
                color: #8a8a9e;
                font-size: 11px;
                font-weight: 600;
            }

            QPushButton#closeButton {
                background-color: transparent;
                border: none;
                color: #8a8a9e;
                font-size: 18px;
                font-weight: bold;
                min-width: 30px;
                max-width: 30px;
                min-height: 30px;
                max-height: 30px;
            }
            QPushButton#closeButton:hover {
                color: #d63031;
                background-color: rgba(214, 48, 49, 0.1);
                border-radius: 15px;
            }

            QPushButton#actionButton {
                background-color: #2d3748;
                color: #e2e8f0;
                border: 1px solid #4a5568;
                padding: 8px 16px;
                border-radius: 6px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton#actionButton:hover {
                background-color: #4a5568;
                border-color: #718096;
            }

            QPushButton#secondaryButton {
                background-color: #3a3a5a;
                color: #e0e0e0;
                border: 1px solid #4a4a6a;
                padding: 8px 16px;
                border-radius: 6px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton#secondaryButton:hover {
                background-color: #4a4a6a;
            }

            QPushButton#clearButton {
                background-color: #dc3545;
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
                font-weight: 600;
                font-size: 11px;
            }
            QPushButton#clearButton:hover {
                background-color: #c82333;
            }

            QSplitter#contentSplitter {
                background-color: transparent;
            }
            QSplitter#contentSplitter::handle {
                background-color: #2a2a2a;
                width: 2px;
            }

            QWidget#filterWidget {
                background-color: #0f0f17;
                border: 1px solid #2a2a3a;
                border-radius: 8px;
            }

            QWidget#summaryWidget {
                background-color: #0f0f17;
                border: 1px solid #2a2a3a;
                border-radius: 8px;
            }

            QGroupBox {
                color: #ffffff;
                font-weight: 600;
                font-size: 13px;
                border: 1px solid #3a3a4a;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 8px 0 8px;
                background-color: #0f0f17;
            }

            QLineEdit {
                background-color: #1a1a2e;
                border: 1px solid #3a3a4a;
                color: #ffffff;
                padding: 6px 8px;
                border-radius: 4px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #00d4ff;
                background-color: #1a1a2e;
            }

            QComboBox {
                background-color: #1a1a2e;
                border: 1px solid #3a3a4a;
                color: #ffffff;
                padding: 6px 8px;
                border-radius: 4px;
                font-size: 12px;
                min-width: 100px;
            }
            QComboBox:focus {
                border-color: #00d4ff;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 4px solid #8a8a9e;
                margin-right: 4px;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1a2e;
                border: 1px solid #3a3a4a;
                selection-background-color: #2d3748;
                color: #ffffff;
            }

            QDateEdit {
                background-color: #1a1a2e;
                border: 1px solid #3a3a4a;
                color: #ffffff;
                padding: 6px 8px;
                border-radius: 4px;
                font-size: 12px;
            }
            QDateEdit:focus {
                border-color: #00d4ff;
            }

            QCheckBox {
                color: #ffffff;
                font-size: 12px;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #3a3a4a;
                border-radius: 3px;
                background-color: #1a1a2e;
            }
            QCheckBox::indicator:checked {
                background-color: #00d4ff;
                border-color: #00d4ff;
            }
            QCheckBox::indicator:checked:hover {
                background-color: #0099cc;
            }

            QTableWidget {
                background-color: #0d0d0d;
                border: 1px solid #202020;
                gridline-color: #1a1a1a;
                font-size: 12px;
                color: #e0e0e0;
                selection-background-color: rgba(74, 122, 191, 0.3);
                selection-color: #ffffff;
                border-radius: 6px;
                alternate-background-color: #121212;
            }

            QTableWidget::item {
                padding: 8px 6px;
                border-bottom: 1px solid #1a1a1a;
                border-right: 1px solid #151515;
            }
            QTableWidget::item:selected {
                background-color: rgba(74, 122, 191, 0.3);
                color: #ffffff;
                font-weight: 600;
            }
            QTableWidget::item:hover {
                background-color: rgba(74, 122, 191, 0.1);
            }

            QHeaderView::section {
                background-color: #1a1a1a;
                color: #a0c0ff;
                padding: 10px 8px;
                border: none;
                border-bottom: 2px solid #303030;
                border-right: 1px solid #101010;
                font-weight: 700;
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }
            QHeaderView::section:last {
                border-right: none;
            }
            QHeaderView::section:hover {
                background-color: #2a2a2a;
            }

            QScrollBar:vertical {
                background-color: #1a1a1a;
                width: 12px;
                border-radius: 6px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background-color: #3a3a3a;
                border-radius: 6px;
                min-height: 20px;
                margin: 2px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #4a4a4a;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }

            QScrollBar:horizontal {
                background-color: #1a1a1a;
                height: 12px;
                border-radius: 6px;
                margin: 0;
            }
            QScrollBar::handle:horizontal {
                background-color: #3a3a3a;
                border-radius: 6px;
                min-width: 20px;
                margin: 2px;
            }
            QScrollBar::handle:horizontal:hover {
                background-color: #4a4a4a;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0;
            }
        """)

    # Window dragging methods
    def _handle_mouse_press(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _handle_mouse_move(self, event: QMouseEvent):
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def _handle_mouse_release(self, event: QMouseEvent):
        self._drag_pos = None
        event.accept()

    def showEvent(self, event):
        """Handle dialog show event."""
        super().showEvent(event)
        self._refresh_data()

    def closeEvent(self, event):
        """Handle dialog close event."""
        self.auto_refresh_timer.stop()
        super().closeEvent(event)

    # Public interface methods
    def refresh_orders(self):
        """Public method to refresh orders."""
        self._refresh_data()

    def get_current_filters(self) -> Dict[str, Any]:
        """Get current filter settings."""
        return self.filter_widget.get_filters()

    def set_symbol_filter(self, symbol: str):
        """Set symbol filter programmatically."""
        self.filter_widget.symbol_filter.setText(symbol)

    def get_displayed_orders(self) -> List[Dict]:
        """Get currently displayed orders."""
        return self.orders_table._orders_data