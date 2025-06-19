from PySide6.QtWidgets import QMenuBar
from PySide6.QtGui import QAction
from typing import Dict, Tuple


def create_main_menu(parent) -> Tuple[QMenuBar, Dict[str, QAction]]:
    """
    Creates and returns a styled, compact menu bar suitable for the
    Swing Trader application, with a consistent dark theme.
    """
    menubar = QMenuBar(parent)
    menubar.setStyleSheet("""
        QMenuBar {
            background-color: #0a0a0a; /* Deep black background */
            color: #e0e0e0; /* Light gray text */
            border-bottom: 1px solid #202020; /* Subtle dark border */
            padding: 0px 2px; /* Reduced vertical padding for compactness */
            font-family: "Segoe UI", Arial, sans-serif; /* Professional font */
            font-size: 12px;
        }
        QMenuBar::item {
            padding: 3px 10px; /* Reduced padding for compact height */
            margin: 0px 1px; /* Minimal margin */
            border-radius: 2px; /* Minimal rounding */
            color: #a0c0ff; /* Light blue for menu items */
            font-weight: 500;
        }
        QMenuBar::item:selected {
            background-color: rgba(74, 122, 191, 0.2); /* Softer blue selection with transparency */
            color: #ffffff;
        }
        QMenuBar::item:hover {
            background-color: #1a1a1a; /* Darker hover for non-selected items */
        }
        QMenu {
            background-color: #1a1a1a; /* Darker background for dropdown menus */
            color: #e0e0e0;
            border: 1px solid #303030; /* Darker border for menus */
            padding: 3px; /* Reduced padding */
            font-size: 11px; /* Slightly smaller font for menu items */
        }
        QMenu::item {
            padding: 5px 20px; /* Reduced padding */
            margin: 1px 2px; /* Reduced margin */
            border-radius: 2px; /* Minimal rounding */
            color: #e0e0e0; /* Default text color for menu items */
        }
        QMenu::item:selected {
            background-color: rgba(74, 122, 191, 0.2); /* Softer blue selection with transparency */
            color: #ffffff;
        }
        QMenu::item:hover {
            background-color: #2a2a2a; /* Darker hover for menu items */
        }
        QMenu::separator {
            height: 1px;
            background: #202020; /* Very dark separator */
            margin: 3px 8px; /* Reduced margin */
        }
    """)

    menu_actions = {}

    # --- File Menu ---
    file_menu = menubar.addMenu("&File")
    menu_actions['refresh'] = file_menu.addAction("Refresh All Data")
    menu_actions['refresh'].setShortcut("F5")
    file_menu.addSeparator()
    menu_actions['exit'] = file_menu.addAction("Exit")
    menu_actions['exit'].setShortcut("Ctrl+Q")

    # --- View Menu ---
    view_menu = menubar.addMenu("&View")
    menu_actions['order_history'] = view_menu.addAction("Order History")
    menu_actions['pnl_calendar'] = view_menu.addAction("P&L Calendar")
    menu_actions['performance'] = view_menu.addAction("Lifetime Performance")

    # --- Tools Menu ---
    tools_menu = menubar.addMenu("&Tools")
    menu_actions['settings'] = tools_menu.addAction("Settings...")
    menu_actions['settings'].setShortcut("Ctrl+,")

    # --- Help Menu ---
    help_menu = menubar.addMenu("&Help")
    menu_actions['about'] = help_menu.addAction("About")

    return menubar, menu_actions