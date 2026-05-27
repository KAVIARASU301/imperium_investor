from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QSplitter, QTabWidget, QVBoxLayout, QWidget

from chart_engine import CandlestickChart
from ibkr.core.data_fetcher import IBKRDataFetcher
from ibkr.core.market_data_worker import IBKRMarketDataWorker
from ibkr.core.order_router import IBKROrderRouter
from ibkr.core.position_manager import IBKRPositionManager
from ibkr.widgets.header_toolbar import HeaderToolbar
from ibkr.widgets.positions_table import Position, PositionsTable
from ibkr.widgets.scanner_table import FinvizScannerTable
from ibkr.widgets.settings_dialog import ColorSettingsDialog
from ibkr.widgets.watchlist_table import TabbedWatchlistWidget

logger = logging.getLogger(__name__)


class QullamaggieWindow(QMainWindow):
    """IBKR window that mirrors Kite terminal structure (left widgets + chart)."""

    def __init__(self, trader: Any, real_kite_client: Any = None, api_key: str = "", access_token: str = ""):
        super().__init__()
        self.trader = trader
        self.ib = getattr(trader, "client", trader)
        self.data_client = real_kite_client or self.ib
        self._last_price_by_symbol: dict[str, float] = {}
        self._color_theme = {
            "enable_table_directional_colors": False,
            "show_table_vertical_lines": True,
            "show_scanner_volume_column": True,
            "show_watchlist_volume_column": True,
            "tables": {
                "positive": "#00d4a8",
                "negative": "#ff4d6a",
                "neutral": "#5a7090",
                "volume": "#00d4ff",
            },
            "candles": {"up": "#00C896", "down": "#E84060"},
            "volume": {"up": "#00C896", "down": "#E84060"},
        }

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

        self.header_toolbar = HeaderToolbar(self.ib, self, enable_account_polling=False)
        root_layout.addWidget(self.header_toolbar)

        splitter = QSplitter(Qt.Horizontal)
        root_layout.addWidget(splitter, 1)

        left_panel = QTabWidget()
        self.watchlist_table = TabbedWatchlistWidget(self)
        self.scanner_table = FinvizScannerTable(self)
        self.positions_table = PositionsTable(self)

        left_panel.addTab(self.watchlist_table, "Watchlist")
        left_panel.addTab(self.scanner_table, "Scanner")
        left_panel.addTab(self.positions_table, "Positions")

        self.chart = CandlestickChart(IBKRDataFetcher(self.data_client), storage_dir="ibkr/user_data/chart_drawings")

        splitter.addWidget(left_panel)
        splitter.addWidget(self.chart)
        splitter.setSizes([460, 1140])

        self._apply_color_theme()

    def _wire_signals(self) -> None:
        self.header_toolbar.symbol_selected.connect(self._load_symbol)
        self.header_toolbar.buy_order_requested.connect(lambda s: self._submit_order("BUY", s))
        self.header_toolbar.sell_order_requested.connect(lambda s: self._submit_order("SELL", s))
        self.header_toolbar.color_settings_requested.connect(self._open_color_settings)

        self.watchlist_table.symbol_selected.connect(self._load_symbol)
        self.scanner_table.symbol_selected.connect(self._load_symbol)
        self.positions_table.symbol_selected.connect(self._load_symbol)

        self.order_router.order_submitted.connect(self._on_order_submitted)
        self.order_router.order_failed.connect(self._on_order_failed)
        self.market_data_worker.tick_received.connect(self._on_tick)

    def _load_symbol(self, symbol: str) -> None:
        symbol = (symbol or "").strip().upper()
        if not symbol:
            return
        self.chart.load_symbol(symbol, symbol, 0, "day")
        self.market_data_worker.subscribe_symbol(symbol)

    def _submit_order(self, action: str, symbol: str | None = None) -> None:
        sym = (symbol or "").strip().upper()
        if not sym:
            sym = (self.header_toolbar.search_input.get_committed_symbol() or "").strip().upper()
        if not sym:
            return
        self.order_router.submit({"symbol": sym, "qty": 1.0, "action": action, "order_type": "MKT"})

    def _on_order_submitted(self, payload: dict) -> None:
        logger.info("Order submitted: %s", payload)
        self.refresh_positions()

    def _on_order_failed(self, error: str) -> None:
        logger.error("Order failed: %s", error)

    def _on_tick(self, tick: dict) -> None:
        symbol = str(tick.get("symbol") or "").upper()
        if not symbol:
            return
        last = tick.get("last") or tick.get("close") or tick.get("bid")
        if last is None:
            return
        self._last_price_by_symbol[symbol] = float(last)

    def _open_color_settings(self) -> None:
        dlg = ColorSettingsDialog(self._color_theme, self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self._color_theme = dlg.get_theme()
            self._apply_color_theme()

    def _apply_color_theme(self) -> None:
        self.watchlist_table.apply_color_theme(self._color_theme)
        self.scanner_table.apply_color_theme(self._color_theme)
        self.positions_table.apply_color_theme(self._color_theme)
        self.header_toolbar.apply_color_theme(self._color_theme)

    def refresh_positions(self) -> None:
        try:
            raw_positions = self.position_manager.snapshot()
        except Exception as exc:
            logger.error("Failed to refresh positions: %s", exc)
            return
        positions = [Position.from_broker_position(pos) for pos in raw_positions]
        self.positions_table.update_positions(positions)
