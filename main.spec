# main.spec
import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

# Collect PySide6 WebEngine resources
pyside6_datas = collect_data_files('PySide6', includes=[
    '*.so', '*.pyi', '*.pem',
    'Qt/lib/*.so*',
    'Qt/resources/*',
    'Qt/translations/qtwebengine_locales/*',
])

a = Analysis(
    ['main.py'],
    pathex=[os.path.abspath('.')],
    binaries=[],
    datas=[
        # Your app assets
        ('assets', 'assets'),
        ('chart_engine/renderer/chart.js', 'chart_engine/renderer'),
        ('chart_engine/renderer/drawing_engine.js', 'chart_engine/renderer'),
        ('chart_engine/renderer/drawing_engine_integration.patch.js', 'chart_engine/renderer'),
        # Include all your kite subdirs
        ('kite', 'kite'),
        ('chart_engine', 'chart_engine'),
        ('login_setup', 'login_setup'),
    ] + pyside6_datas,
    hiddenimports=[
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebChannel',
        'PySide6.QtMultimedia',
        'kiteconnect',
        'cryptography',
        'rapidfuzz',
        'cachetools',
        'pandas',
        'requests',
        'urllib3',
        'sqlite3',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'IPython', 'jupyter'],
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
    name='qullamaggie',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # No terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon='assets/qullamaggie_icon.png',  # Use .png on Linux
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='qullamaggie',
)