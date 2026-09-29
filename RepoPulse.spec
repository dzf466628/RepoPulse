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
# PyInstaller 的 PyQt6 hook 本来就会把 Qt6\bin 收进 _internal\PyQt6\Qt6\bin，
# 这里再往 _internal\PyQt6 顶层复制一份，等于每个 Qt DLL 在安装包里存两份
# （实测重复 52.5 MB，光是 opengl32sw.dll 就 19.7 MB）。
# 所以只补 hook 没收到的那几个 VC 运行时，其余交给 hook，别重复占地方。
qt_hook_collected = {
    'Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Network.dll', 'Qt6Pdf.dll', 'Qt6Svg.dll', 'Qt6Widgets.dll',
    'MSVCP140.dll', 'MSVCP140_1.dll', 'MSVCP140_2.dll', 'VCRUNTIME140.dll', 'VCRUNTIME140_1.dll',
    'opengl32sw.dll',
}
qt_binaries = [(str(qt_bin / name), 'PyQt6') for name in qt_runtime_names if (qt_bin / name).is_file() and name not in qt_hook_collected]
excluded_external_binaries = {'icuuc.dll', 'icudt78.dll'}

# 内置 Git（MinGit）：vendor\git 不进库，由 tools\fetch_minigit.ps1 获取。
# 没有它就别打包 —— 否则会做出一个用户装完点"新建项目"就报 WinError 2 的安装包。
bundled_git_dir = Path(SPECPATH) / 'vendor' / 'git'
if not (bundled_git_dir / 'cmd' / 'git.exe').is_file():
    raise SystemExit(
        r'vendor\git 不存在，先运行 tools\fetch_minigit.ps1 获取内置 Git 再打包。'
    )


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=qt_binaries,
    datas=[('RepoPulse.png', '.'), (str(bundled_git_dir), 'git')],
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
