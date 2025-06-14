"""GUI components for Options Scalper application"""

# --- Data Models and Core Utilities (as in original) ---
from src.utils.data_models import Contract, Position, OptionType
from src.utils.instrument_loader import InstrumentLoader

# --- Main Application Window ---
from .main_window import ScalperMainWindow

# --- Core UI Components ---
from .strike_ladder import StrikeLadderWidget
from .header_toolbar import HeaderToolbar
from .buy_exit_panel import BuyExitPanel
from .menu_bar import create_enhanced_menu_bar

# --- Table Widgets ---
from .positions_table import PositionsTable
from .tables.open_positions_table import OpenPositionsTable

# --- Dialogs ---
from .dialogs.open_positions_dialog import OpenPositionsDialog
from .dialogs.option_chain_dialog import OptionChainDialog
from .dialogs.order_confirmation_dialog import OrderConfirmationDialog
from .dialogs.order_history_dialog import OrderHistoryDialog
from .dialogs.pending_orders_dialog import PendingOrdersDialog
from .dialogs.performance_dialog import PerformanceDialog
from .dialogs.pnl_history_dialog import PnlHistoryDialog
from .dialogs.quick_order_dialog import QuickOrderDialog
from .dialogs.settings_dialog import SettingsDialog

# --- Standalone Widgets ---
from .widgets.performance_widget import PerformanceWidget
from .widgets.account_summary import AccountSummaryWidget
from .widgets.order_status_widget import OrderStatusWidget


# The __all__ list defines the public API of the package.
__all__ = [
    # Main Window
    'ScalperMainWindow',

    # Core Components
    'StrikeLadderWidget',
    'HeaderToolbar',
    'BuyExitPanel',
    'PositionsTable',
    'OpenPositionsTable',
    'create_enhanced_menu_bar',

    # Dialogs
    'OpenPositionsDialog',
    'OptionChainDialog',
    'OrderConfirmationDialog',
    'OrderHistoryDialog',
    'PendingOrdersDialog',
    'PerformanceDialog',
    'PnlHistoryDialog',
    'QuickOrderDialog',
    'SettingsDialog',

    # Widgets
    'PerformanceWidget',
    'AccountSummaryWidget',
    'OrderStatusWidget',

    # Data Models & Utilities (from original)
    'Contract',
    'Position',
    'OptionType',
    'InstrumentLoader'
]