"""Helpers for locating bundled application resources."""

import os
import sys


def resource_path(relative_path: str) -> str:
    """
    Get absolute path to resource.
    Works for dev (PyCharm) AND PyInstaller bundle.
    """
    if getattr(sys, "frozen", False):
        # PyInstaller stores temp files in _MEIPASS
        base_path = sys._MEIPASS
    else:
        # Running normally in PyCharm
        base_path = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))

    return os.path.join(base_path, relative_path)
