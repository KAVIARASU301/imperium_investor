# hooks/hook-PySide6.py  (create this file)
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

datas = collect_data_files('PySide6')
binaries = collect_dynamic_libs('PySide6')