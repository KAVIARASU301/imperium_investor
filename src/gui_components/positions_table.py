import logging
from typing import List, Dict
from PySide6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QPushButton, QLabel, QVBoxLayout,
    QWidget, QHeaderView, QFrame, QHBoxLayout
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QCursor
from src.utils.config_manager import ConfigManager

logger = logging.getLogger(__name__)


class PositionsTable(QWidget):
    exit_requested = Signal(dict)

    # --- MODIFIED: Updated __init__ to accept trader and config_manager ---
    def __init__(self, trader, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self.trader = trader
        self.config_manager = config_manager
        self.table_name = "positions_table"
        self._positions_cache: Dict[str, Dict] = {}
        self._row_map: Dict[str, int] = {}
        self._setup_ui()
        self._apply_styles()

        # Timer to refresh positions
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.update_positions)
        self.refresh_timer.start(5000)  # Refresh every 5 seconds
        self.update_positions()  # Initial load

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        self.table = QTableWidget()
        self._configure_table()
        main_layout.addWidget(self.table, 1)
        main_layout.addWidget(self._create_footer())

    def _configure_table(self):
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["Symbol", "Qty", "Avg", "LTP", "P&L", ""])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        self.table.setColumnWidth(5, 45)
        self._load_column_widths()
        header.sectionResized.connect(self._save_column_widths)

    def _create_footer(self):
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

    def update_positions(self):
        """Fetches and updates positions from the trader object."""
        try:
            # Using the passed trader object to get positions
            positions = self.trader.positions().get('net', [])
            self._update_table_from_data(positions)
        except Exception as e:
            logger.error(f"Failed to fetch positions: {e}")

    def _update_table_from_data(self, positions: List[Dict]):
        # (The rest of this file remains the same as our previous refactor)
        new_positions_map = {p['tradingsymbol']: p for p in positions if p.get('quantity', 0) != 0}
        self._positions_cache = new_positions_map
        current_symbols = set(self._row_map.keys())
        new_symbols = set(new_positions_map.keys())
        symbols_to_remove = current_symbols - new_symbols
        if symbols_to_remove:
            rows_to_remove = sorted([self._row_map[s] for s in symbols_to_remove], reverse=True)
            for row in rows_to_remove:
                self.table.removeRow(row)
            self._rebuild_row_map()
        total_pnl = 0
        for symbol, pos_data in new_positions_map.items():
            total_pnl += pos_data.get('pnl', 0)
            if symbol in self._row_map:
                row = self._row_map[symbol]
                self._update_row(row, pos_data)
            else:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self._populate_row(row, pos_data)
                self._row_map[symbol] = row
        self._update_total_pnl(total_pnl)

    # ... (all other methods like _populate_row, _update_row, etc., are unchanged)

    def _create_exit_button(self) -> QWidget:
        container_widget = QWidget()
        layout = QHBoxLayout(container_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignCenter)
        exit_btn = QPushButton("✕")
        exit_btn.setObjectName("exitButton")
        exit_btn.setFixedSize(24, 24)
        exit_btn.setCursor(QCursor(Qt.PointingHandCursor))
        exit_btn.clicked.connect(self._on_exit_clicked)
        layout.addWidget(exit_btn)
        return container_widget

    def _populate_row(self, row: int, pos_data: Dict):
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
        self.table.setCellWidget(row, 5, self._create_exit_button())

    def _update_row(self, row: int, pos_data: Dict):
        self.table.item(row, 1).setText(str(pos_data.get('quantity', 0)))
        self.table.item(row, 2).setText(f"{pos_data.get('average_price', 0.0):.2f}")
        self.table.item(row, 3).setText(f"{pos_data.get('last_price', 0.0):.2f}")
        pnl = pos_data.get('pnl', 0.0)
        pnl_item = self.table.item(row, 4)
        pnl_item.setText(f"{pnl:,.2f}")
        pnl_color = QColor("#29C7C9") if pnl >= 0 else QColor("#F85149")
        pnl_item.setForeground(pnl_color)

    def _rebuild_row_map(self):
        self._row_map.clear()
        for row in range(self.table.rowCount()):
            try:
                symbol = self.table.item(row, 0).text()
                self._row_map[symbol] = row
            except AttributeError:
                logger.warning(f"Could not map row {row}, symbol item not found.")

    def _on_exit_clicked(self):
        button = self.sender()
        if not button: return
        for row in range(self.table.rowCount()):
            cell_widget = self.table.cellWidget(row, 5)
            if cell_widget and button in cell_widget.findChildren(QPushButton):
                try:
                    symbol = self.table.item(row, 0).text()
                    if symbol in self._positions_cache:
                        self.exit_requested.emit(self._positions_cache[symbol])
                except AttributeError:
                    logger.error(f"Could not find symbol for exit button in row {row}")
                return

    def _update_total_pnl(self, total_pnl: float):
        color = "#29C7C9" if total_pnl >= 0 else "#F85149"
        self.total_pnl_label.setText(f"₹{total_pnl:,.2f}")
        self.total_pnl_label.setStyleSheet(f"color: {color};")

    def _apply_styles(self):
        self.setStyleSheet("""
            QWidget { background-color: #161A25; color: #E0E0E0; font-family: "Segoe UI"; }
            QTableWidget { border: none; gridline-color: #2A3140; font-size: 13px; }
            QHeaderView::section { background-color: #212635; color: #A9B1C3; padding: 6px; border: none; border-bottom: 1px solid #3A4458; font-weight: bold; font-size: 11px; text-transform: uppercase; }
            QTableWidget::item { padding: 2px; border-bottom: 1px solid #2A3140; }
            #exitButton { background-color: #444; color: #ff3860; border-radius: 11px; font-weight: bold; font-size: 12px; }
            #exitButton:hover { background-color: #F85149; color: #FFFFFF; }
            /* --- MODIFIED: Corrected the hex color from #2A40 to #2A3140 --- */
            #footerFrame { background-color: #212635; border-top: 1px solid #2A3140; }
            #footerLabel { background-color: #212635; color: #A9B1C3; font-size: 11px; font-weight: bold; text-transform: uppercase; }
            #totalPnlValue { background-color: #212635; font-size: 16px; font-weight: 600; }
        """)

    def _load_column_widths(self):
        states = self.config_manager.load_table_column_states(self.table_name)
        if states and 'column_widths' in states:
            widths = states['column_widths']
            if len(widths) == self.table.columnCount():
                for i, width in enumerate(widths): self.table.setColumnWidth(i, width)

    def _save_column_widths(self, logical_index: int, old_size: int, new_size: int):
        if not self.isVisible(): return
        widths = [self.table.columnWidth(i) for i in range(self.table.columnCount())]
        self.config_manager.save_table_column_states(self.table_name, {'column_widths': widths})