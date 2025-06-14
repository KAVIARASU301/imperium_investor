# src/gui_components/tables/open_positions_table.py
"""
Enhanced Open Positions Table
Purpose: Professional table widget for displaying trading positions with real-time updates
Features: Rich styling, dynamic P&L updates, optimized column widths, smooth animations
"""

from typing import Dict, List
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QPushButton,
    QHeaderView, QAbstractItemView
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QColor, QFont
from src.utils.data_models import Position
import logging

logger = logging.getLogger(__name__)


class AnimatedTableWidgetItem(QTableWidgetItem):
    """Custom table item with animation support for P&L changes"""

    def __init__(self, text: str):
        super().__init__(text)
        self._previous_value = 0.0
        self._current_value = 0.0

    def set_animated_value(self, value: float, is_pnl: bool = False):
        """Set value with animation effect for P&L changes"""
        self._previous_value = self._current_value
        self._current_value = value

        if is_pnl:
            # Set a faint background shade instead of changing text color
            if value > 0:
                self.setBackground(QColor(46, 160, 67, 30))  # Lite Green Shade
            elif value < 0:
                self.setBackground(QColor(248, 81, 73, 30))  # Lite Red Shade
            else:
                self.setBackground(QColor("transparent"))

            # Format currency
            self.setText(f"₹{value:,.2f}")
        else:
            self.setText(f"{value:.2f}")


class OpenPositionsTable(QWidget):
    position_exit_requested = Signal(str)  # symbol

    def __init__(self):
        super().__init__()
        self._positions: Dict[str, Position] = {}
        self._row_map: Dict[str, int] = {}
        self._setup_ui()
        self._setup_styling()

    def _setup_ui(self):
        """Setup the table UI components with a professional layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, 8)  # Increased column count to 8
        headers = ["Order ID", "Symbol", "Qty", "Avg Price", "LTP", "P&L", "P&L %", "Action"]
        self.table.setHorizontalHeaderLabels(headers)

        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)

        header = self.table.horizontalHeader()

        # Set "Order ID" to stretch, and other columns to fit their content
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)

        self.table.setColumnWidth(7, 80)

        layout.addWidget(self.table)

    def _setup_styling(self):
        """Apply rich dark theme styling to the table"""
        self.setStyleSheet("""
            QTableWidget {
                background-color: #0d1117;
                alternate-background-color: #161b22;
                color: #e6edf3;
                gridline-color: #30363d;
                border: 1px solid #30363d;
                border-radius: 8px;
                selection-background-color: #2c313a;
                font-size: 13px;
            }
            QTableWidget::item { padding: 8px; border-bottom: 1px solid #21262d; }
            QTableWidget::item:selected { background-color: #2c313a; color: white; }
            QTableWidget::item:hover { background-color: #21262d; }
            QHeaderView::section {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #30363d, stop:1 #21262d);
                color: #e6edf3; padding: 10px 8px; border: none;
                border-right: 1px solid #21262d; border-bottom: 2px solid #1f6feb;
                font-weight: bold; font-size: 12px;
            }
            QHeaderView::section:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #444c56, stop:1 #30363d); }
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #da3633, stop:1 #b91c1c);
                color: white; border: 1px solid #f85149; border-radius: 4px;
                padding: 6px 12px; font-weight: 500; font-size: 11px;
            }
            QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f85149, stop:1 #da3633); border: 1px solid #ff7b72; }
            QPushButton:pressed { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #b91c1c, stop:1 #991b1b); }
            QScrollBar:vertical { background: #21262d; width: 12px; border-radius: 6px; }
            QScrollBar::handle:vertical { background: #444c56; border-radius: 6px; min-height: 20px; }
            QScrollBar::handle:vertical:hover { background: #58a6ff; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { border: none; background: none; }
        """)

    def update_positions(self, positions: List[Position]):
        """
        Intelligently updates the table. It only rebuilds when the set of
        positions changes, otherwise it performs a flicker-free data update.
        """
        new_positions_map = {p.symbol: p for p in positions}

        if set(self._positions.keys()) != set(new_positions_map.keys()):
            self._positions = new_positions_map
            self._rebuild_table()
        else:
            self._positions = new_positions_map
            self._update_rows_data()

    def _rebuild_table(self):
        """Completely rebuilds the table. Used when positions are added/removed."""
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self._row_map.clear()

        for row_index, position in enumerate(self._positions.values()):
            self.table.insertRow(row_index)
            self._populate_row(row_index, position)
            self._row_map[position.symbol] = row_index

        self.table.setSortingEnabled(True)

    def _update_rows_data(self):
        """Performs a flicker-free update of data in existing rows."""
        for symbol, position in self._positions.items():
            if symbol in self._row_map:
                row = self._row_map[symbol]
                if row < self.table.rowCount():
                    self._update_row_data(row, position)

    def _populate_row(self, row_index: int, position: Position):
        """Creates and populates widgets for a new row."""
        # Create all items first
        self.table.setItem(row_index, 0, QTableWidgetItem())  # Order ID
        self.table.setItem(row_index, 1, QTableWidgetItem())  # Symbol
        self.table.setItem(row_index, 2, QTableWidgetItem())  # Qty
        self.table.setItem(row_index, 3, QTableWidgetItem())  # Avg Price
        self.table.setItem(row_index, 4, QTableWidgetItem())  # LTP
        self.table.setItem(row_index, 5, AnimatedTableWidgetItem(""))  # P&L
        self.table.setItem(row_index, 6, QTableWidgetItem())  # P&L %

        # Exit Button
        exit_btn = QPushButton("Exit")
        exit_btn.setToolTip(f"Exit position: {position.symbol}")
        exit_btn.clicked.connect(lambda checked, s=position.symbol: self.position_exit_requested.emit(s))
        self.table.setCellWidget(row_index, 7, exit_btn)

        # Populate all data
        self._update_row_data(row_index, position)

    def _update_row_data(self, row: int, position: Position):
        """Updates all the data for a specific, existing row."""
        # Order ID (Column 0)
        self.table.item(row, 0).setText(position.order_id or "N/A")

        # Symbol (Column 1)
        symbol_item = self.table.item(row, 1)
        symbol_item.setText(position.symbol)
        symbol_item.setFont(QFont("Consolas", 11))

        # Qty (Column 2)
        qty_item = self.table.item(row, 2)
        qty_item.setText(f"{position.quantity:,}")
        qty_item.setForeground(QColor("#a0a0a0"))

        # Avg Price (Column 3)
        self.table.item(row, 3).setText(f"₹{position.average_price:.2f}")

        # LTP (Column 4)
        ltp_item = self.table.item(row, 4)
        ltp_item.setText(f"₹{position.ltp:.2f}")
        ltp_item.setForeground(QColor("#58a6ff"))

        # P&L (Column 5)
        pnl_item = self.table.item(row, 5)
        if isinstance(pnl_item, AnimatedTableWidgetItem):
            pnl_item.set_animated_value(position.pnl, is_pnl=True)

        # P&L % (Column 6)
        investment = position.average_price * abs(position.quantity)
        pnl_percent = (position.pnl / investment * 100) if investment != 0 else 0.0
        pnl_percent_item = self.table.item(row, 6)
        pnl_percent_item.setText(f"{pnl_percent:.2f}%")

        if pnl_percent > 0:
            pnl_percent_item.setBackground(QColor(46, 160, 67, 30))
        elif pnl_percent < 0:
            pnl_percent_item.setBackground(QColor(248, 81, 73, 30))
        else:
            pnl_percent_item.setBackground(QColor("transparent"))

    def get_all_positions(self) -> List[Position]:
        """Get all current positions"""
        return list(self._positions.values())