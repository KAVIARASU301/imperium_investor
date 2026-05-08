# hooks/hook-kiteconnect.py
from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files('kiteconnect')
hiddenimports = ['kiteconnect', 'kiteconnect.exceptions']