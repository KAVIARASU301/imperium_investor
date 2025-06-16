import logging
from typing import List, Dict
from PySide6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QPushButton, QLabel, QVBoxLayout,
    QWidget, QHeaderView, QFrame, QHBoxLayout, QAbstractItemView
)
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor, QCursor

from utils.data_models import Position

logger = logging.getLogger(__name__)


class OpenPositionsTable(QWidget):
    """
    A widget that displays a real-time view of all open stock positions.
    It receives data updates from the PositionManager and provides user
    interaction for exiting positions and viewing charts.
    """
    # Define the signals that this widget can emit
    exit_position_requested = Signal(dict)
    subscribe_tokens_requested = Signal(list)
    symbol_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._positions_cache: Dict[str, Position] = {}

        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        """Sets up the main UI layout and table."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.table = QTableWidget()
        self._configure_table()
        main_layout.addWidget(self.table, 1)

        main_layout.addWidget(self._create_footer())

        self.table.cellClicked.connect(self._on_cell_clicked)

    def _configure_table(self):
        """Configures the table headers, columns, and behavior."""
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["Symbol", "Qty", "Avg. Price", "LTP", "P&L", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 5):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(5, 40)

    def _create_footer(self) -> QFrame:
        """Creates the footer widget with the total P&L label."""
        footer_frame = QFrame(objectName="footerFrame")
        footer_layout = QHBoxLayout(footer_frame)
        footer_layout.setContentsMargins(12, 0, 12, 0)
        footer_frame.setFixedHeight(35)

        footer_label = QLabel("TOTAL P&L")
        footer_label.setObjectName("footerLabel")

        self.total_pnl_label = QLabel("₹0.00")
        self.total_pnl_label.setObjectName("totalPnlValue")

        footer_layout.addWidget(footer_label)
        footer_layout.addStretch()
        footer_layout.addWidget(self.total_pnl_label)
        return footer_frame

    @Slot(list)
    def update_positions(self, positions: List[Position]):
        """
        Public slot to receive and display an updated list of positions.
        This is the primary way data gets into the table.
        """
        self.table.setRowCount(0)
        self._positions_cache.clear()

        total_pnl = 0.0

        sorted_positions = sorted(positions, key=lambda p: p.tradingsymbol)

        for pos in sorted_positions:
            self._positions_cache[pos.tradingsymbol] = pos
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._populate_row(row, pos)
            total_pnl += pos.pnl

        self._update_total_pnl(total_pnl)

    def _populate_row(self, row: int, pos: Position):
        """Fills a single table row with data from a Position object."""
        symbol_item = QTableWidgetItem(pos.tradingsymbol)
        qty_item = QTableWidgetItem(str(pos.quantity))
        avg_item = QTableWidgetItem(f"{pos.average_price:.2f}")
        ltp_item = QTableWidgetItem(f"{pos.ltp:.2f}")
        pnl_item = QTableWidgetItem(f"{pos.pnl:,.2f}")

        for item in [qty_item, avg_item, ltp_item, pnl_item]:
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        profit_color = QColor("#00b894")
        loss_color = QColor("#d63031")
        pnl_item.setForeground(profit_color if pos.pnl >= 0 else loss_color)

        self.table.setItem(row, 0, symbol_item)
        self.table.setItem(row, 1, qty_item)
        self.table.setItem(row, 2, avg_item)
        self.table.setItem(row, 3, ltp_item)
        self.table.setItem(row, 4, pnl_item)
        self.table.setCellWidget(row, 5, self._create_exit_button(row))

    def _create_exit_button(self, row: int) -> QPushButton:
        """Creates the 'X' button used to exit a position."""
        exit_btn = QPushButton("✕", objectName="exitButton")
        exit_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        exit_btn.clicked.connect(lambda: self._on_exit_clicked(row))
        return exit_btn

    def _on_exit_clicked(self, row: int):
        """Handles the click of an exit button for a specific row."""
        try:
            symbol = self.table.item(row, 0).text()
            if symbol in self._positions_cache:
                position_data = self._positions_cache[symbol].to_dict()
                self.exit_position_requested.emit(position_data)
        except AttributeError:
            logger.error(f"Could not retrieve symbol from table at row {row}.")

    def _on_cell_clicked(self, row: int, column: int):
        """Emits the symbol of the clicked row to update the chart."""
        if column == 5:
            return
        try:
            symbol = self.table.item(row, 0).text()
            self.symbol_selected.emit(symbol)
        except AttributeError:
            logger.warning(f"Could not get symbol from clicked row: {row}")

    def _update_total_pnl(self, total_pnl: float):
        """Updates the total P&L label in the footer with appropriate color."""
        profit_color, loss_color = "#00b894", "#d63031"
        color = profit_color if total_pnl >= 0 else loss_color
        self.total_pnl_label.setText(f"₹{total_pnl:,.2f}")
        self.total_pnl_label.setStyleSheet(f"color: {color};")

    def get_all_tokens(self) -> List[int]:
        """Returns a list of all instrument tokens currently in the table."""
        return [
            pos.contract.instrument_token
            for pos in self._positions_cache.values()
            if pos.contract and pos.contract.instrument_token
        ]

    def _apply_styles(self):
        """Applies a consistent, modern dark theme stylesheet."""
        self.setStyleSheet("""
            QWidget { background-color: #1c1c2e; color: #e0e0e0; font-family: "Segoe UI"; }
            QTableWidget {
                border: none;
                gridline-color: #2a2a4a;
                font-size: 13px;
            }
            QHeaderView::section {
                background-color: #1c1c2e;
                color: #8a8a9e;
                padding: 8px;
                border: none;
                border-bottom: 1px solid #3a3a5a;
                font-weight: bold;
                font-size: 11px;
                text-transform: uppercase;
            }
            QTableWidget::item {
                padding: 10px 8px;
                border-bottom: 1px solid #2a2a4a;
            }
            QTableWidget::item:selected {
                background-color: #3a3a5a;
            }
            #exitButton {
                background-color: #4a4a6a;
                color: #e0e0e0;
                border-radius: 12px;
                font-weight: bold;
                font-size: 14px;
                max-width: 24px;
                max-height: 24px;
            }
            #exitButton:hover {
                background-color: #d63031;
                color: #ffffff;
            }
            #footerFrame {
                background-color: #2a2a4a;
                border-top: 1px solid #3a3a5a;
            }
            #footerLabel {
                color: #8a8a9e;
                font-size: 11px;
                font-weight: bold;
                text-transform: uppercase;
            }
            #totalPnlValue {
                font-size: 16px;
                font-weight: 600;
            }
        """)
