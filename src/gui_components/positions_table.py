# src/gui_components/swing_positions_table.py
import logging
from typing import List, Dict
from PySide6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QPushButton, QLabel, QVBoxLayout,
    QWidget, QHeaderView, QFrame, QHBoxLayout
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QCursor
from src.utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class PositionsTable(QWidget):
    """
    A redesigned, premium table for displaying active positions, featuring a
    consistent dark theme, efficient backend integration, and fully adjustable columns.
    """
    exit_requested = Signal(dict)

    def __init__(self, config_manager: ConfigManager):
        super().__init__()
        self.config_manager = config_manager
        self.table_name = "positions_table"  # A unique key for this table's settings
        self._positions_cache: Dict[str, Dict] = {}  # Cache for position data by symbol
        self._row_map: Dict[str, int] = {}           # Maps symbol to table row index
        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self):
        """Initialize the UI components with a more integrated layout."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.table = QTableWidget()
        self._configure_table()

        main_layout.addWidget(self.table, 1)
        main_layout.addWidget(self._create_footer())

    def _configure_table(self):
        """Sets up the properties and headers for the table widgets."""
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["Symbol", "Qty", "Avg", "LTP", "P&L", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)  # Symbol
        header.setSectionResizeMode(1, QHeaderView.Interactive)  # Qty
        header.setSectionResizeMode(2, QHeaderView.Interactive)  # Avg
        header.setSectionResizeMode(3, QHeaderView.Interactive)  # LTP
        header.setSectionResizeMode(4, QHeaderView.Interactive)  # P&L
        header.setSectionResizeMode(5, QHeaderView.Fixed)  # Exit

        self.table.setColumnWidth(1, 60)
        self.table.setColumnWidth(2, 75)
        self.table.setColumnWidth(3, 75)
        self.table.setColumnWidth(4, 90)
        self.table.setColumnWidth(5, 45)

        self._load_column_widths()
        header.sectionResized.connect(self._save_column_widths)

    def _create_footer(self):
        """Creates a styled footer to display total P&L."""
        footer_frame = QFrame()
        footer_frame.setObjectName("footerFrame")
        footer_layout = QHBoxLayout(footer_frame)
        footer_layout.setContentsMargins(12, 0, 12, 0)
        footer_frame.setFixedHeight(30)

        footer_label = QLabel("TOTAL P&L")
        footer_label.setObjectName("footerLabel")

        self.total_pnl_label = QLabel("₹0.00")
        self.total_pnl_label.setObjectName("totalPnlValue")

        footer_layout.addWidget(footer_label)
        footer_layout.addStretch()
        footer_layout.addWidget(self.total_pnl_label)
        return footer_frame

    def _create_exit_button(self) -> QWidget:
        """Creates a styled exit button without direct data dependency."""
        container_widget = QWidget()
        container_widget.setStyleSheet("background-color: transparent; border: none;")
        layout = QHBoxLayout(container_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignCenter)

        exit_btn = QPushButton("✕")
        exit_btn.setObjectName("exitButton")
        exit_btn.setFixedSize(24, 24)
        exit_btn.setCursor(QCursor(Qt.PointingHandCursor))
        exit_btn.clicked.connect(self._on_exit_clicked)  # Connect to the new handler
        layout.addWidget(exit_btn)
        return container_widget

    def _populate_row(self, row: int, pos_data: Dict):
        """Creates and sets the widgets for a single new row in the table."""
        symbol_item = QTableWidgetItem(pos_data.get('tradingsymbol', ''))

        qty_item = QTableWidgetItem(str(pos_data.get('quantity', 0)))
        qty_item.setTextAlignment(Qt.AlignCenter)

        avg_item = QTableWidgetItem(f"{pos_data.get('average_price', 0.0):.2f}")
        avg_item.setTextAlignment(Qt.AlignCenter)

        ltp_item = QTableWidgetItem(f"{pos_data.get('last_price', 0.0):.2f}")
        ltp_item.setTextAlignment(Qt.AlignCenter)

        pnl = pos_data.get('pnl', 0.0)
        pnl_item = QTableWidgetItem(f"{pnl:,.2f}")
        pnl_item.setTextAlignment(Qt.AlignCenter)
        pnl_color = QColor("#29C7C9") if pnl >= 0 else QColor("#F85149")
        pnl_item.setForeground(pnl_color)

        self.table.setItem(row, 0, symbol_item)
        self.table.setItem(row, 1, qty_item)
        self.table.setItem(row, 2, avg_item)
        self.table.setItem(row, 3, ltp_item)
        self.table.setItem(row, 4, pnl_item)

        exit_btn_widget = self._create_exit_button()
        self.table.setCellWidget(row, 5, exit_btn_widget)

    def _update_row(self, row: int, pos_data: Dict):
        """Efficiently updates data in an existing row without recreating widgets."""
        self.table.item(row, 1).setText(str(pos_data.get('quantity', 0)))
        self.table.item(row, 2).setText(f"{pos_data.get('average_price', 0.0):.2f}")
        self.table.item(row, 3).setText(f"{pos_data.get('last_price', 0.0):.2f}")

        pnl = pos_data.get('pnl', 0.0)
        pnl_item = self.table.item(row, 4)
        pnl_item.setText(f"{pnl:,.2f}")
        pnl_color = QColor("#29C7C9") if pnl >= 0 else QColor("#F85149")
        pnl_item.setForeground(pnl_color)
        # The exit button widget in column 5 is not touched, as it's now generic.

    def _rebuild_row_map(self):
        """Rebuilds the symbol-to-row mapping after deletions."""
        self._row_map.clear()
        for row in range(self.table.rowCount()):
            try:
                symbol = self.table.item(row, 0).text()
                self._row_map[symbol] = row
            except AttributeError:
                logger.warning(f"Could not map row {row}, symbol item not found.")

    def _on_exit_clicked(self):
        """Handles a click from any exit button in the table."""
        button = self.sender()
        if not button:
            return

        # Find the row of the button that was clicked
        for row in range(self.table.rowCount()):
            cell_widget = self.table.cellWidget(row, 5)
            # The button is inside a container widget in the cell
            if cell_widget and button in cell_widget.findChildren(QPushButton):
                try:
                    symbol = self.table.item(row, 0).text()
                    if symbol in self._positions_cache:
                        self.exit_requested.emit(self._positions_cache[symbol])
                except AttributeError:
                    logger.error(f"Could not find symbol for exit button in row {row}")
                return

    def _update_total_pnl(self, total_pnl: float):
        """Updates the total P&L label with appropriate styling."""
        color = "#29C7C9" if total_pnl >= 0 else "#F85149"
        self.total_pnl_label.setText(f"₹{total_pnl:,.2f}")
        self.total_pnl_label.setStyleSheet(f"color: {color};")

    def _apply_styles(self):
        """Applies the premium stylesheet to the component."""
        self.setStyleSheet("""
            QWidget {
                background-color: #161A25;
                color: #E0E0E0;
                font-family: "Segoe UI";
            }
            QTableWidget {
                border: none;
                gridline-color: #2A3140;
                font-size: 13px;
            }
            QHeaderView::section {
                background-color: #212635;
                color: #A9B1C3;
                padding: 6px 6px;
                border: none;
                border-bottom: 1px solid #3A4458;
                font-weight: bold;
                font-size: 11px;
                text-transform: uppercase;
            }
            QTableWidget::item {
                padding: 2px 2px;
                border-bottom: 1px solid #2A3140;
            }
            #exitButton {
                background-color: #444; color: #ff3860;
                border-radius: 11px; font-weight: bold; font-size: 12px;
            }
            #exitButton:hover { background-color: #F85149; color: #FFFFFF; }            
            #footerFrame {
                background-color: #212635;
                border-top: 1px solid #2A3140;
            }
            #footerLabel {
                background-color: #212635;
                color: #A9B1C3;
                font-size: 11px;
                font-weight: bold;
                text-transform: uppercase;
            }
            #totalPnlValue {
                background-color: #212635;
                font-size: 16px;
                font-weight: 600;
            }
        """)

    def _load_column_widths(self):
        states = self.config_manager.load_table_column_states(self.table_name)
        if states and 'column_widths' in states:
            widths = states['column_widths']
            if len(widths) == self.table.columnCount():
                for i, width in enumerate(widths):
                    self.table.setColumnWidth(i, width)
                logger.info(f"Loaded saved column widths for '{self.table_name}'.")

    def _save_column_widths(self, logical_index: int, old_size: int, new_size: int):
        if not self.isVisible():
            return
        widths = [self.table.columnWidth(i) for i in range(self.table.columnCount())]
        state = {'column_widths': widths}
        self.config_manager.save_table_column_states(self.table_name, state)

    def update_positions(self, positions: List[Dict]):
        """
        Intelligently updates the table by adding, removing, or modifying rows
        in-place, preventing widget recreation and flickering.
        """
        new_positions_map = {p['tradingsymbol']: p for p in positions if p.get('quantity', 0) != 0}
        self._positions_cache = new_positions_map  # Refresh the cache with the latest full data

        current_symbols = set(self._row_map.keys())
        new_symbols = set(new_positions_map.keys())

        # 1. Identify and remove rows for closed positions
        symbols_to_remove = current_symbols - new_symbols
        if symbols_to_remove:
            rows_to_remove = sorted([self._row_map[s] for s in symbols_to_remove], reverse=True)
            for row in rows_to_remove:
                self.table.removeRow(row)
            self._rebuild_row_map() # Re-sync the map after deletions

        # 2. Update existing rows and add new ones
        total_pnl = 0
        for symbol, pos_data in new_positions_map.items():
            total_pnl += pos_data.get('pnl', 0)
            if symbol in self._row_map:
                row = self._row_map[symbol]
                self._update_row(row, pos_data)  # Update existing row in-place
            else:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self._populate_row(row, pos_data)  # Add a new row
                self._row_map[symbol] = row      # Register the new row

        self._update_total_pnl(total_pnl)

    def update_market_prices(self, market_data: Dict):
        logger.warning("update_market_prices is deprecated. Use update_positions.")
        pass