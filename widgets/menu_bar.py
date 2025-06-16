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
            background-color: #1c1c2e;
            color: #b2bec3;
            border-bottom: 1px solid #3a3a5a;
            padding: 2px 4px;
            font-family: "Segoe UI";
            font-size: 12px;
        }
        QMenuBar::item {
            padding: 5px 12px;
            margin: 0px 1px;
            border-radius: 4px;
        }
        QMenuBar::item:selected {
            background-color: #2a2a4a;
            color: #ffffff;
        }
        QMenu {
            background-color: #2a2a4a;
            color: #e0e0e0;
            border: 1px solid #3a3a5a;
            padding: 5px;
        }
        QMenu::item {
            padding: 8px 25px;
            margin: 2px 3px;
            border-radius: 4px;
        }
        QMenu::item:selected {
            background-color: #00b894;
            color: #ffffff;
        }
        QMenu::separator {
            height: 1px;
            background: #3a3a5a;
            margin: 5px 10px;
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
