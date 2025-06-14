from PySide6.QtWidgets import QMainWindow, QSplitter
from PySide6.QtCore import Qt
from src.gui_components.positions_table import PositionsTable
from src.gui_components.widgets.candlestick_chart_widget import ChartWindow
from src.gui_components.tables.chartink_scanner_table import ChartinkScannerTable
from src.gui_components.tables.watchlist_table import WatchlistTable
from src.gui_components.widgets.header_toolbar import HeaderToolbar
from src.utils.config_manager import ConfigManager
from src.utils.instrument_loader import InstrumentLoader
from src.utils.theme_manager import ThemeManager


class SwingTraderWindow(QMainWindow):
    """Main window for the Swing Trader tool, using PySide6."""

    def __init__(self, trader, real_kite_client, api_key, access_token, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Swing Trader")
        self.setGeometry(150, 150, 1600, 900)

        self.trader = trader
        self.real_kite_client = real_kite_client
        self.api_key = api_key
        self.access_token = access_token
        self.config_manager = ConfigManager()
        self.theme_manager = ThemeManager(self)

        self._init_ui()
        self._load_instruments()

    def _init_ui(self):
        """Initializes the user interface."""
        # --- Header Toolbar ---
        self.header_toolbar = HeaderToolbar()
        self.addToolBar(self.header_toolbar)
        self.header_toolbar.theme_switched.connect(self.theme_manager.set_theme)
        self.header_toolbar.symbol_selected.connect(self.on_symbol_selected)

        # --- Main Layout ---
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(main_splitter)

        # --- Left Panel ---
        left_panel_splitter = QSplitter(Qt.Orientation.Vertical)
        self.chartink_scanner = ChartinkScannerTable()
        self.positions_table = PositionsTable(config_manager=self.config_manager)
        left_panel_splitter.addWidget(self.chartink_scanner)
        left_panel_splitter.addWidget(self.positions_table)
        left_panel_splitter.setSizes([400, 200])

        # --- Center Panel ---
        self.candlestick_chart = ChartWindow()

        # --- Right Panel ---
        self.watchlist = WatchlistTable()

        # Add panels to main splitter
        main_splitter.addWidget(left_panel_splitter)
        main_splitter.addWidget(self.candlestick_chart)
        main_splitter.addWidget(self.watchlist)
        main_splitter.setSizes([350, 900, 350])

        style = "QSplitter::handle:horizontal{width:2px} QSplitter::handle:vertical{height:2px}"
        self.setStyleSheet(style)

    def _load_instruments(self):
        """Loads trading instruments in the background."""
        self.instrument_loader = InstrumentLoader(self.real_kite_client)
        self.instrument_loader.instruments_loaded.connect(self.on_instruments_loaded)
        self.instrument_loader.start()

    def on_instruments_loaded(self, symbol_data):
        """Callback for when instruments are loaded."""
        self.header_toolbar.set_instrument_data(symbol_data)

    def on_symbol_selected(self, symbol, instrument_token):
        """Handles a symbol being selected from the header."""
        self.candlestick_chart.load_chart(instrument_token, symbol)

        # Placeholder for updating position quantity display
        # In a real app, you would get this from your PositionManager
        holding_qty = self.get_holding_quantity(symbol)
        self.header_toolbar.update_position_qty(holding_qty)

    def get_holding_quantity(self, symbol):
        """Placeholder to get holding quantity for a symbol."""
        # Replace with actual logic from your PositionManager
        if symbol == "RELIANCE":
            return 10
        if symbol == "TCS":
            return 5
        return 0

    def closeEvent(self, event):
        """Handle window close event."""
        event.accept()