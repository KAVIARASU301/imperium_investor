# -*- mode: python ; coding: utf-8 -*-
# main.spec
import os
import sys
from pathlib import Path

SPEC_DIR = os.path.abspath(os.path.dirname(__file__))
if SPEC_DIR not in sys.path:
    sys.path.insert(0, SPEC_DIR)

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules
from utils.resource_path import resource_path

block_cipher = None

ASSETS_DIR = resource_path('assets')
APP_ICON = resource_path('assets/imperium_icon.png')

APP_PACKAGES = (
    'kite',
    'ibkr',
    'chart_engine',
    'login_setup',
    'utils',
)


def existing_datas(*entries):
    """Return only data entries whose source exists on this checkout."""
    result = []
    for source, destination in entries:
        if Path(source).exists():
            result.append((source, destination))
    return result


# Keep PySide6 collection focused.  Collecting every PySide6 submodule pulls in
# deployment scripts and optional Qt modules that are not needed by the app and
# can create noisy/broken hidden imports in frozen builds.
pyside6_datas = collect_data_files('PySide6', include_py_files=False)
pyside6_binaries = collect_dynamic_libs('PySide6')

app_hiddenimports = []
for package in APP_PACKAGES:
    app_hiddenimports += collect_submodules(package)


a = Analysis(
    ['main.py'],
    pathex=[SPEC_DIR],
    binaries=pyside6_binaries,
    datas=existing_datas(
        # Application assets.
        (ASSETS_DIR, 'assets'),

        # JavaScript files the chart engine needs at runtime.
        ('chart_engine/renderer/chart.js', 'chart_engine/renderer'),
        ('chart_engine/renderer/drawing_engine.js', 'chart_engine/renderer'),
        ('chart_engine/renderer/drawing_engine_integration.patch.js', 'chart_engine/renderer'),

        # Optional bundled starter data, when present in a local checkout.
        ('kite/user_data', 'kite/user_data'),
        ('ibkr/user_data', 'ibkr/user_data'),
    ) + pyside6_datas,

    hiddenimports=[
        # PySide6 modules used directly or by widgets that are loaded dynamically.
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtNetwork',
        'PySide6.QtPrintSupport',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebChannel',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',

        # Broker libraries.
        'kiteconnect',
        'kiteconnect.exceptions',

        # Cryptography (used by token manager).
        'cryptography',
        'cryptography.fernet',
        'cryptography.hazmat.primitives',
        'cryptography.hazmat.primitives.kdf.pbkdf2',

        # Data libraries.
        'pandas',
        'numpy',
        'scipy',
        'rapidfuzz',
        'cachetools',

        # Network.
        'requests',
        'urllib3',
        'certifi',
        'charset_normalizer',

        # Web scraping (the importable module name is bs4; beautifulsoup4 is the
        # package/distribution name and should not be listed as a hidden import).
        'bs4',

        # Plotting.
        'plotly',
        'plotly.graph_objects',

        # Standard library modules sometimes missed.
        'sqlite3',
        '_sqlite3',
        'json',
        'uuid',
        'asyncio',
        'threading',
        'http.server',
        'urllib.parse',
    ] + app_hiddenimports,

    hookspath=['hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Things you definitely don't need - reduces bundle size.
        'tkinter',
        '_tkinter',
        'IPython',
        'jupyter',
        'notebook',
        # Do not exclude matplotlib here; requirements include it and PyInstaller
        # can safely omit it only after confirming no runtime path imports it.
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
    exclude_binaries=True,
    name='imperium',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=APP_ICON,
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
