"""IBKR-specific main window with Kite-like layout and essential widgets."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from chart_engine import CandlestickChart
from ibkr.core.ibkr_data_fetcher import IBKRDataFetcher
from ibkr.core.market_data_worker import IBKRMarketDataWorker
from ibkr.core.order_router import IBKROrderRouter
from ibkr.core.position_manager import IBKRPositionManager

logger = logging.getLogger(__name__)


class QullamaggieWindow(QMainWindow):
    """IBKR window that mirrors Kite terminal structure (left widgets + chart)."""

    def __init__(self, trader: Any, real_kite_client: Any = None, api_key: str = "", access_token: str = ""):
        super().__init__()
        self.trader = trader
        self.ib = getattr(trader, "client", trader)
        self.data_client = real_kite_client or self.ib
        self._last_price_by_symbol: dict[str, float] = {}

        self.order_router = IBKROrderRouter(self.ib)
        self.position_manager = IBKRPositionManager(self.ib)
        self.market_data_worker = IBKRMarketDataWorker(self.ib)

        self.setWindowTitle("qullamaggie - USA (IBKR)")
        self.resize(1600, 950)

        self._build_ui()
        self._wire_signals()
        self.refresh_positions()

    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(6, 6, 6, 6)

        # Header strip (Kite-like quick actions)
        header = QWidget(self)
        header_layout = QHBoxLayout(header)
        self.symbol_input = QLineEdit()
        self.symbol_input.setPlaceholderText("Search symbol (e.g. AAPL)")
        self.btn_load = QPushButton("Load")
        self.btn_buy = QPushButton("BUY")
        self.btn_sell = QPushButton("SELL")
        self.qty_input = QLineEdit("1")
        self.qty_input.setMaximumWidth(70)
        self.ltp_label = QLabel("LTP: --")
        for w in (self.symbol_input, self.btn_load, self.qty_input, self.btn_buy, self.btn_sell, self.ltp_label):
            header_layout.addWidget(w)
        header_layout.addStretch(1)
        root_layout.addWidget(header)

        splitter = QSplitter(Qt.Horizontal)
        root_layout.addWidget(splitter, 1)

        left_panel = QTabWidget()
        self.watchlist_table = QTableWidget(0, 2)
        self.watchlist_table.setHorizontalHeaderLabels(["Symbol", "Last"])
        self.scanner_table = QTableWidget(0, 3)
        self.scanner_table.setHorizontalHeaderLabels(["Symbol", "%Chg", "Volume"])
        self.positions_table = QTableWidget(0, 5)
        self.positions_table.setHorizontalHeaderLabels(["Symbol", "Qty", "Avg", "PnL", "Exchange"])

        left_panel.addTab(self.watchlist_table, "Watchlist")
        left_panel.addTab(self.scanner_table, "Scanner")
        left_panel.addTab(self.positions_table, "Positions")

        # Right panel chart engine
        self.chart = CandlestickChart(IBKRDataFetcher(self.data_client), storage_dir="ibkr/user_data/chart_drawings")

        splitter.addWidget(left_panel)
        splitter.addWidget(self.chart)
        splitter.setSizes([420, 1180])

    def _wire_signals(self) -> None:
        self.btn_load.clicked.connect(self._load_symbol)
        self.symbol_input.returnPressed.connect(self._load_symbol)
        self.btn_buy.clicked.connect(lambda: self._submit_order("BUY"))
        self.btn_sell.clicked.connect(lambda: self._submit_order("SELL"))
        self.order_router.order_submitted.connect(self._on_order_submitted)
        self.order_router.order_failed.connect(self._on_order_failed)
        self.market_data_worker.tick_received.connect(self._on_tick)

    def _load_symbol(self) -> None:
        symbol = self.symbol_input.text().strip().upper()
        if not symbol:
            return
        self.chart.load_symbol(symbol, symbol, 0, "day")
        self.market_data_worker.subscribe_symbol(symbol)
        self._upsert_watchlist(symbol)

    def _submit_order(self, action: str) -> None:
        symbol = self.symbol_input.text().strip().upper()
        if not symbol:
            QMessageBox.warning(self, "Missing symbol", "Please enter a symbol first.")
            return
        try:
            qty = float(self.qty_input.text().strip() or "0")
        except ValueError:
            QMessageBox.warning(self, "Invalid quantity", "Quantity should be numeric.")
            return
        if qty <= 0:
            QMessageBox.warning(self, "Invalid quantity", "Quantity should be greater than zero.")
            return

        self.order_router.submit({"symbol": symbol, "qty": qty, "action": action, "order_type": "MKT"})

    def _on_order_submitted(self, payload: dict) -> None:
        QMessageBox.information(self, "Order Submitted", f"Order submitted: {payload}")
        self.refresh_positions()

    def _on_order_failed(self, error: str) -> None:
        QMessageBox.critical(self, "Order Failed", error)

    def _on_tick(self, tick: dict) -> None:
        symbol = str(tick.get("symbol") or "").upper()
        if not symbol:
            return
        last = tick.get("last") or tick.get("close") or tick.get("bid")
        if last is None:
            return
        self._last_price_by_symbol[symbol] = float(last)
        if self.symbol_input.text().strip().upper() == symbol:
            self.ltp_label.setText(f"LTP: {float(last):.2f}")
        self._upsert_watchlist(symbol)

    def _upsert_watchlist(self, symbol: str) -> None:
        for row in range(self.watchlist_table.rowCount()):
            if self.watchlist_table.item(row, 0).text() == symbol:
                self.watchlist_table.setItem(row, 1, QTableWidgetItem(f"{self._last_price_by_symbol.get(symbol, 0.0):.2f}"))
                return
        row = self.watchlist_table.rowCount()
        self.watchlist_table.insertRow(row)
        self.watchlist_table.setItem(row, 0, QTableWidgetItem(symbol))
        self.watchlist_table.setItem(row, 1, QTableWidgetItem(f"{self._last_price_by_symbol.get(symbol, 0.0):.2f}"))

    def refresh_positions(self) -> None:
        try:
            positions = self.position_manager.snapshot()
        except Exception as exc:
            logger.error("Failed to refresh positions: %s", exc)
            return
        self.positions_table.setRowCount(0)
        for pos in positions:
            row = self.positions_table.rowCount()
            self.positions_table.insertRow(row)
            self.positions_table.setItem(row, 0, QTableWidgetItem(str(pos.get("tradingsymbol", ""))))
            self.positions_table.setItem(row, 1, QTableWidgetItem(str(pos.get("quantity", 0))))
            self.positions_table.setItem(row, 2, QTableWidgetItem(str(pos.get("average_price", 0))))
            self.positions_table.setItem(row, 3, QTableWidgetItem(str(pos.get("pnl", 0))))
            self.positions_table.setItem(row, 4, QTableWidgetItem(str(pos.get("exchange", "SMART"))))
