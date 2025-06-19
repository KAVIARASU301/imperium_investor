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
        self.table.setHorizontalHeaderLabels(["Symbol", "Qty", "Avg", "LTP", "P&L", ""]) # Renamed "Avg. Price" to "Avg"
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False) # Hide grid lines for cleaner look
        self.table.setAlternatingRowColors(True) # Enable alternating row colors

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)  # Symbol
        for i in range(1, 5):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents) # Resize Qty, Avg, LTP, P&L to contents
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)  # Exit button
        self.table.setColumnWidth(5, 24) # Smaller width for exit button column

        self.table.verticalHeader().setDefaultSectionSize(28) # Reduced row height for compactness

    def _create_footer(self) -> QFrame:
        """Creates the footer widget with the total P&L label."""
        footer_frame = QFrame(objectName="footerFrame")
        footer_layout = QHBoxLayout(footer_frame)
        footer_layout.setContentsMargins(12, 0, 12, 0)
        footer_frame.setFixedHeight(30) # Reduced height for footer

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
        # Ensure items exist before setting text and alignment
        for col_idx in range(self.columnCount()):
            if not self.table.item(row, col_idx):
                self.table.setItem(row, col_idx, QTableWidgetItem())

        self.table.item(row, 0).setText(pos.tradingsymbol)
        self.table.item(row, 1).setText(str(pos.quantity))
        self.table.item(row, 2).setText(f"{pos.average_price:.2f}")
        self.table.item(row, 3).setText(f"{pos.ltp:.2f}")
        self.table.item(row, 4).setText(f"{pos.pnl:,.2f}")

        # Center align numerical and P&L data for readability
        self.table.item(row, 0).setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for col_idx in range(1, 5): # Columns 1 (Qty) to 4 (P&L)
            self.table.item(row, col_idx).setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

        # Color-code the P&L value
        profit_color = QColor(60, 179, 113)  # Medium Sea Green
        loss_color = QColor(220, 20, 60)    # Crimson
        neutral_color = QColor(169, 169, 169) # DarkGray

        pnl_item = self.table.item(row, 4)
        pnl_item.setForeground(profit_color if pos.pnl >= 0 else loss_color)

        # Apply color to LTP based on P&L (consistent with other tables and trading UIs)
        ltp_item = self.table.item(row, 3)
        ltp_item.setForeground(profit_color if pos.pnl >= 0 else loss_color)

        # Qty and Avg Price can be neutral
        self.table.item(row, 1).setForeground(neutral_color)
        self.table.item(row, 2).setForeground(neutral_color)

        self.table.setCellWidget(row, 5, self._create_exit_button(row))

    def _create_exit_button(self, row: int) -> QPushButton:
        """Creates the 'X' button used to exit a position."""
        exit_btn = QPushButton("✕", objectName="exitButton")
        exit_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        exit_btn.setFixedSize(20, 20) # Smaller button
        exit_btn.clicked.connect(lambda: self._on_exit_clicked(row))
        return exit_btn

    def _on_exit_clicked(self, row: int):
        """Handles the click of an exit button for a specific row."""
        try:
            symbol = self.table.item(row, 0).text()
            if symbol in self._positions_cache:
                position_data = self._positions_cache[symbol].to_dict()
                self.exit_position_requested.emit(position_data)
            else:
                logger.warning(f"Could not find position data for symbol {symbol} in cache.")
        except AttributeError:
            logger.error(f"Could not retrieve symbol from table at row {row}.")

    def _on_cell_clicked(self, row: int, column: int):
        """Emits the symbol of the clicked row to update the chart."""
        # We don't want to trigger this if the exit button was clicked
        if column == 5:
            return
        try:
            symbol = self.table.item(row, 0).text()
            self.symbol_selected.emit(symbol)
        except AttributeError:
            logger.warning(f"Could not get symbol from clicked row: {row}")

    def _update_total_pnl(self, total_pnl: float):
        """Updates the total P&L label in the footer with appropriate color."""
        profit_color = "#00b388" # Slightly adjusted green
        loss_color = "#e04f5e" # Slightly adjusted red
        color = profit_color if total_pnl >= 0 else loss_color
        self.total_pnl_label.setText(f"₹{total_pnl:,.2f}")
        self.total_pnl_label.setStyleSheet(f"color: {color}; font-weight: 600;") # Add font-weight for emphasis

    def get_all_tokens(self) -> List[int]:
        """Returns a list of all instrument tokens currently in the table."""
        return [
            pos.contract.instrument_token
            for pos in self._positions_cache.values()
            if pos.contract and pos.contract.instrument_token
        ]

    def _apply_styles(self):
        """Applies a consistent, minimal dark theme stylesheet."""
        self.setStyleSheet("""
            QWidget {
                background-color: #0a0a0a; /* Deep black background */
                color: #e0e0e0; /* Light gray text */
                font-family: "Segoe UI", Arial, sans-serif; /* Professional font */
                font-size: 13px;
            }

            QTableWidget {
                border: 1px solid #202020; /* Subtle dark border for the table */
                gridline-color: #151515; /* Almost invisible grid lines */
                font-size: 12px;
                background-color: #0d0d0d; /* Deep black table background */
                selection-background-color: rgba(74, 122, 191, 0.2); /* Softer blue selection with transparency */
                selection-color: #ffffff;
                border-radius: 0px; /* No rounding */
            }
            QHeaderView::section {
                background-color: #1a1a1a; /* Header background */
                color: #a0c0ff; /* Header text color */
                padding: 4px 10px; /* Reduced header padding */
                border: none;
                border-bottom: 1px solid #303030; /* Clear header bottom border */
                border-right: 1px solid #101010; /* Dark vertical header separators */
                font-weight: 600;
                font-size: 11px;
            }
            QHeaderView::section:last {
                border-right: none;
            }
            QHeaderView::section:hover {
                background-color: #2a2a2a; /* Subtle hover for headers */
            }
            QTableWidget::item {
                padding: 5px 8px; /* Consistent padding */
                border-bottom: 1px solid #1a1a1a; /* Thin row separator */
                background-color: transparent; /* Ensure item background is transparent */
                color: #e0e0e0;
            }
            QTableWidget::item:selected {
                background-color: rgba(74, 122, 191, 0.2); /* Softer blue selection with transparency */
                color: #ffffff;
                font-weight: 600;
            }
            QTableWidget::item:alternate {
                background-color: #121212; /* Very dark alternate row */
            }

            /* Exit Button Styling */
            QPushButton#exitButton {
                background-color: transparent;
                color: #cc4444; /* Red color for 'X' */
                border: none;
                font-weight: bold;
                font-size: 12px;
                border-radius: 8px; /* Slight rounding for button */
                padding: 0px; /* No internal padding */
            }
            QPushButton#exitButton:hover {
                color: #ff6666; /* Lighter red on hover */
                background-color: rgba(204, 68, 68, 0.2); /* Very subtle red background on hover */
            }
            QPushButton#exitButton:pressed {
                color: #a33333;
            }

            /* Footer Styling */
            QFrame#footerFrame {
                background-color: #1a1a1a; /* Darker footer background */
                border-top: 1px solid #303030; /* Clear separator */
            }
            QLabel#footerLabel {
                background-color: transparent; /* Explicitly transparent */
                color: #a0c0ff; /* Light blue label */
                font-size: 11px;
                font-weight: 600; /* Bolder */
                text-transform: uppercase;
            }
            QLabel#totalPnlValue {
                background-color: transparent; /* Explicitly transparent */
                font-size: 14px; /* Slightly smaller for balance */
                font-weight: bold;
            }

            /* Scrollbars - Invisible */
            QScrollBar:vertical {
                width: 0px; /* Make invisible */
            }
            QScrollBar::handle:vertical {
                width: 0px; /* Make invisible */
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px; /* Make invisible */
            }
            QScrollBar:horizontal {
                height: 0px; /* Make invisible */
            }
            QScrollBar::handle:horizontal {
                height: 0px; /* Make invisible */
            }
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {
                width: 0px; /* Make invisible */
            }
        """)