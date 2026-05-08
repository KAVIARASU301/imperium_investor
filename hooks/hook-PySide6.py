# hooks/hook-PySide6.py
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_all

# Collect everything from PySide6
datas, binaries, hiddenimports = collect_all('PySide6')