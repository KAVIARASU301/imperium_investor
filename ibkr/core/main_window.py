from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenuBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from chart_engine import CandlestickChart
from chart_engine.core.ibkr_data_fetcher import IBKRDataFetcher
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
    """IBKR window with Kite-style three-column main workspace."""

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

        self._is_maximized = False
        self._drag_pos = QPoint()

        self._setup_frameless_window()
        self._build_ui()
        self._wire_signals()
        self.refresh_positions()

    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.scanner_table = FinvizScannerTable(self)
        self.watchlist_table = TabbedWatchlistWidget(self)
        self.positions_table = PositionsTable(self)

        self.menu_bar = self._create_menu_bar()
        self.top_bar = self._create_top_bar()
        root_layout.addWidget(self.top_bar)

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(6, 6, 6, 6)

        self.header_toolbar = HeaderToolbar(self.ib, self, enable_account_polling=False)
        content_layout.addWidget(self.header_toolbar)

        self.main_splitter = QSplitter(Qt.Horizontal)
        content_layout.addWidget(self.main_splitter, 1)

        self.chart = CandlestickChart(IBKRDataFetcher(self.data_client), storage_dir="ibkr/user_data/chart_drawings")
        self.scanner_table.setObjectName("scannerPanel")
        self.chart.setObjectName("primaryChartPanel")
        self.watchlist_table.setObjectName("watchlistPanel")

        self.scanner_table.setMinimumWidth(220)
        self.chart.setMinimumWidth(460)
        self.watchlist_table.setMinimumWidth(220)

        self.main_splitter.addWidget(self.scanner_table)
        self.main_splitter.addWidget(self.chart)
        self.main_splitter.addWidget(self.watchlist_table)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(1)
        self.main_splitter.setStretchFactor(0, 2)
        self.main_splitter.setStretchFactor(1, 6)
        self.main_splitter.setStretchFactor(2, 3)
        self.main_splitter.setSizes([300, 980, 420])

        self._apply_color_theme()
        root_layout.addWidget(content, 1)

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

        current_symbol = str(getattr(self.chart, "current_symbol", "") or "").upper()
        if current_symbol and current_symbol == symbol:
            self.chart.update_live_data(tick)

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


    def _setup_frameless_window(self) -> None:
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setMinimumSize(1200, 700)
        self.menuBar().setVisible(False)

    def _create_menu_bar(self) -> QMenuBar:
        menu_bar = QMenuBar()
        menu_bar.setObjectName("mainMenuBar")
        menu_bar.setNativeMenuBar(False)

        file_menu = menu_bar.addMenu("File")
        file_menu.addAction("Exit", self.close)

        view_menu = menu_bar.addMenu("View")
        self.scanner_action = QAction("Scanner", self, checkable=True, checked=True)
        self.scanner_action.toggled.connect(self.scanner_table.setVisible)
        view_menu.addAction(self.scanner_action)

        self.watchlist_action = QAction("Watchlist", self, checkable=True, checked=True)
        self.watchlist_action.toggled.connect(self.watchlist_table.setVisible)
        view_menu.addAction(self.watchlist_action)

        tools_menu = menu_bar.addMenu("Tools")
        tools_menu.addAction("Settings", self._open_color_settings)

        return menu_bar

    def _create_top_bar(self) -> QWidget:
        top_bar = QWidget()
        top_bar.setObjectName("customTitleBar")
        top_bar.setFixedHeight(28)

        root_layout = QGridLayout(top_bar)
        root_layout.setContentsMargins(7, 0, 4, 0)

        self.menu_container = QWidget()
        self.menu_container.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
        menu_layout = QHBoxLayout(self.menu_container)
        menu_layout.setContentsMargins(0, 0, 0, 0)
        self.menu_bar.setFixedHeight(24)
        menu_layout.addWidget(self.menu_bar)

        title = QLabel("Qullamaggie [IBKR]")

        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(4)

        min_btn = QPushButton("−")
        min_btn.setFixedSize(24, 22)
        min_btn.clicked.connect(self.showMinimized)
        controls_layout.addWidget(min_btn)

        self.max_btn = QPushButton("□")
        self.max_btn.setFixedSize(24, 22)
        self.max_btn.clicked.connect(self._toggle_maximize)
        controls_layout.addWidget(self.max_btn)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 22)
        close_btn.clicked.connect(self.close)
        controls_layout.addWidget(close_btn)

        root_layout.addWidget(self.menu_container, 0, 0)
        root_layout.addWidget(title, 0, 1)
        root_layout.addWidget(controls, 0, 2, alignment=Qt.AlignmentFlag.AlignRight)
        root_layout.setColumnStretch(0, 1)
        root_layout.setColumnStretch(2, 1)

        top_bar.mousePressEvent = self._title_bar_mouse_press
        top_bar.mouseMoveEvent = self._title_bar_mouse_move
        top_bar.mouseDoubleClickEvent = self._title_bar_double_click
        return top_bar

    def _toggle_maximize(self) -> None:
        if self._is_maximized:
            self.showNormal()
            self._is_maximized = False
        else:
            self.showMaximized()
            self._is_maximized = True

    def _title_bar_mouse_press(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _title_bar_mouse_move(self, event) -> None:
        if event.buttons() == Qt.MouseButton.LeftButton and not self._is_maximized:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def _title_bar_double_click(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximize()
