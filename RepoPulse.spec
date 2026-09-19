# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path


qt_bin = Path(sys.prefix) / 'Lib' / 'site-packages' / 'PyQt6' / 'Qt6' / 'bin'
qt_runtime_names = (
    'Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Network.dll', 'Qt6Pdf.dll', 'Qt6Svg.dll', 'Qt6Widgets.dll',
    'MSVCP140.dll', 'MSVCP140_1.dll', 'MSVCP140_2.dll', 'VCRUNTIME140.dll', 'VCRUNTIME140_1.dll',
    'concrt140.dll', 'msvcp140_atomic_wait.dll', 'msvcp140_codecvt_ids.dll', 'vcruntime140_threads.dll',
    'vccorlib140.dll', 'opengl32sw.dll',
)
qt_binaries = [(str(qt_bin / name), 'PyQt6') for name in qt_runtime_names if (qt_bin / name).is_file()]
excluded_external_binaries = {'icuuc.dll', 'icudt78.dll'}


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=qt_binaries,
    datas=[('RepoPulse.png', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() not in excluded_external_binaries]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RepoPulse',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    hide_console='hide-early',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon='RepoPulse.ico',
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='RepoPulse',
)
