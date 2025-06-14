from PySide6.QtWidgets import QMainWindow, QSplitter
from PySide6.QtCore import Qt

# Assuming these components are updated to PySide6 and in the specified paths
# You will need to ensure 'positions_table' is also PySide6 compatible.
from src.gui_components.positions_table import PositionsTable
from .widgets.alerts_log_widget import AlertsLogWidget
from .widgets.candlestick_chart_widget import ChartWindow
from .tables.chartink_scanner_table import ChartinkScannerTable

class SwingTraderWindow(QMainWindow):
    """Main window for the Swing Trader tool, using PySide6."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Swing Trader")
        self.setGeometry(150, 150, 1400, 800)

        # Main horizontal splitter (3 panels)
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(main_splitter)

        # --- Left Panel ---
        left_panel_splitter = QSplitter(Qt.Orientation.Vertical)
        self.positions_table = PositionsTable()  # Ensure this is PySide6 compatible
        self.alerts_log = AlertsLogWidget()
        left_panel_splitter.addWidget(self.positions_table)
        left_panel_splitter.addWidget(self.alerts_log)
        left_panel_splitter.setSizes([600, 200]) # Initial size ratio

        # --- Center Panel ---
        self.candlestick_chart = ChartWindow()

        # --- Right Panel ---
        self.chartink_scanner = ChartinkScannerTable()

        # Add panels to the main splitter
        main_splitter.addWidget(left_panel_splitter)
        main_splitter.addWidget(self.candlestick_chart)
        main_splitter.addWidget(self.chartink_scanner)

        # Set initial sizes for the 3 panels
        main_splitter.setSizes([250, 850, 300])

        # --- Style for minimal splitter handles ---
        # Note: In PySide6, you might need to be more specific
        style = """
            QSplitter::handle:horizontal {
                width: 2px;
            }
            QSplitter::handle:vertical {
                height: 2px;
            }
        """
        self.setStyleSheet(style)


    def closeEvent(self, event):
        """Handle window close event."""
        # Clean up resources if necessary
        event.accept()