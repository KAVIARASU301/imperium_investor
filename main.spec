# -*- mode: python ; coding: utf-8 -*-
# main.spec
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_all

block_cipher = None

# Collect ALL PySide6 data (this is the most important part for a GUI app)
pyside6_datas, pyside6_binaries, pyside6_hiddenimports = collect_all('PySide6')

a = Analysis(
    ['main.py'],
    pathex=[os.path.abspath('.')],
    binaries=pyside6_binaries,
    datas=[
        # Your app's asset files - (source, destination_in_bundle)
        ('assets', 'assets'),

        # JavaScript files the chart engine needs
        ('chart_engine/renderer/chart.js', 'chart_engine/renderer'),
        ('chart_engine/renderer/drawing_engine.js', 'chart_engine/renderer'),
        ('chart_engine/renderer/drawing_engine_integration.patch.js', 'chart_engine/renderer'),

        # Include the entire kite, chart_engine, login_setup packages as data
        # (PyInstaller handles .py files automatically, but sometimes
        #  subfolders with non-Python files need explicit inclusion)
        ('kite/user_data', 'kite/user_data'),

    ] + pyside6_datas,

    hiddenimports=[
        # PySide6 modules
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebChannel',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtNetwork',
        'PySide6.QtPrintSupport',

        # Your broker libraries
        'kiteconnect',
        'kiteconnect.exceptions',

        # IBKR (optional - only if you use it)
        # 'ib_insync',

        # Cryptography (used by your token manager)
        'cryptography',
        'cryptography.fernet',
        'cryptography.hazmat.primitives',
        'cryptography.hazmat.primitives.kdf.pbkdf2',

        # Data libraries
        'pandas',
        'numpy',
        'scipy',
        'rapidfuzz',
        'cachetools',

        # Network
        'requests',
        'urllib3',
        'certifi',
        'charset_normalizer',

        # Web scraping (for Chartink scanner)
        'bs4',
        'beautifulsoup4',

        # Plotting
        'plotly',
        'plotly.graph_objects',

        # Standard library modules sometimes missed
        'sqlite3',
        '_sqlite3',
        'json',
        'uuid',
        'asyncio',
        'threading',
        'http.server',
        'urllib.parse',

        # Your own packages (sometimes needed if using dynamic imports)
        'kite',
        'kite.core',
        'kite.widgets',
        'kite.utils',
        'kite.scanner',
        'chart_engine',
        'login_setup',
        'utils',

    ] + pyside6_hiddenimports,

    hookspath=['hooks'],    # Your custom hooks folder
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Things you definitely don't need - reduces bundle size
        'tkinter',
        '_tkinter',
        'IPython',
        'jupyter',
        'notebook',
        'matplotlib',    # Remove this if you actually use matplotlib
        'PIL',
        'cv2',
        'PyQt5',
        'PyQt6',
        'wx',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,    # Keep this True for --onedir (folder) mode
    name='imperium',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,              # Keep False for easier debugging
    upx=False,                # Keep False - UPX can break Qt apps on Linux
    console=False,            # No terminal window shown
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon='assets/imperium_icon.png',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='imperium',
)