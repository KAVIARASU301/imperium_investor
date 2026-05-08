# hooks/hook-PySide6.py
"""Project PySide6 hook kept intentionally small.

The PyInstaller-provided PySide6 submodule hooks collect the Qt libraries and
plugins needed by imported modules such as QtWidgets and QtWebEngineWidgets.
Avoid collecting every PySide6 submodule here, because that pulls in optional
PySide6 deployment scripts and can produce invalid hidden imports during builds.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# Top-level resources and dynamic libraries are enough here; concrete Qt module
# hooks run separately when those modules are imported/hidden-imported.
datas = collect_data_files("PySide6", include_py_files=False)
binaries = collect_dynamic_libs("PySide6")
hiddenimports = []
