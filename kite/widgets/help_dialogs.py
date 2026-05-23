"""Compatibility wrappers for legacy imports.

Use `kite.widgets.keyboard_shortcuts` and `kite.widgets.about_dialog` directly.
"""

from kite.widgets.about_dialog import show_about_dialog
from kite.widgets.keyboard_shortcuts import show_keyboard_shortcuts_dialog as show_shortcuts_reference_dialog

__all__ = ["show_shortcuts_reference_dialog", "show_about_dialog"]
