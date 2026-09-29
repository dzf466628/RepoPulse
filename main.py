# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import sys
import os
import subprocess
from pathlib import Path

# PyInstaller keeps PyQt6's Qt DLLs below the bundled runtime directory.
# Register that directory before importing any Qt extension modules.
if sys.platform == "win32" and getattr(sys, "frozen", False):
    import ctypes

    _bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    _qt_dirs = (_bundle_root / "PyQt6", _bundle_root / "PyQt6" / "Qt6" / "bin")
    _qt_dll_handles = [
        os.add_dll_directory(str(qt_dir))
        for qt_dir in _qt_dirs
        if qt_dir.is_dir()
    ]
    os.environ["PATH"] = os.pathsep.join(
        (*(str(qt_dir) for qt_dir in _qt_dirs if qt_dir.is_dir()), os.environ.get("PATH", ""))
    )
    _qt_preloaded = []
    for _qt_name in ("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll"):
        # 顶层（如果还在）优先，其次 PyQt6\Qt6\bin —— 打包时只保留一份，
        # 所以这里必须两处都找，才能保证预加载照旧生效。
        for _qt_dir in _qt_dirs:
            _qt_path = _qt_dir / _qt_name
            if _qt_path.is_file():
                _qt_preloaded.append(ctypes.WinDLL(str(_qt_path)))
                break

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _pid_file() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    directory = Path(base) / "GitStatusDesk"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "repopulse.pid"


def _kill_stale_instance(pid_path: Path) -> None:
    """崩溃残留清理：只有 PID 文件存在时才精准查询并结束上一个 RepoPulse 进程。

    正常退出会删除 PID 文件，因此正常启动这里直接返回、零开销，
    不再像旧 bat 那样每次启动都用 WMI 遍历全部进程。
    """
    if not pid_path.exists():
        return
    try:
        old_pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        old_pid = 0
    if old_pid > 0:
        try:
            cmd = [
                "powershell", "-NoProfile", "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={old_pid}').CommandLine",
            ]
            out = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                creationflags=_CREATE_NO_WINDOW,
            ).stdout or ""
            # 只杀确实是本程序的进程（开发态 python ...\RepoPulse\main.py，
            # 或打包态 RepoPulse.exe），避免误杀同目录 .venv 里跑的其他脚本
            is_repopulse = (
                ("main.py" in out and "RepoPulse" in out)
                or "RepoPulse.exe" in out
            )
            if is_repopulse:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(old_pid)],
                    capture_output=True,
                    creationflags=_CREATE_NO_WINDOW,
                )
        except Exception:
            pass
    try:
        pid_path.unlink()
    except OSError:
        pass


from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from app import telemetry
from app.ui.main_window import MainWindow
from app.ui.theme import apply_theme


def _print_git_info() -> int:
    """--git-info：打印软件实际在用的 Git，排障和打包后自检都用它。

    控制台编码在上面已经切成 UTF-8，所以这里可以直接输出中文。
    """
    from app.core.git_locator import bundled_git_root, check_git, missing_git_message

    runtime, version = check_git()
    if runtime is None:
        print(missing_git_message())
        return 3
    print(f"来源：{runtime.source}")
    print(f"路径：{runtime.exe}")
    print(f"版本：{version}")
    print(f"内置目录：{bundled_git_root() or '（无）'}")
    return 0


def _print_qt_info() -> int:
    """--qt-self-test：只把 Qt 起起来再退出，验证打包后 Qt 能正常加载。

    打包做过去重（每个 Qt DLL 只留一份）之后，光看文件在不在不够，
    得真起一次 QApplication 才能确认平台插件也没问题。
    这里不建窗口、不上托盘、不写 pid、不发遥测，可以放心地当自检用。
    """
    from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
    from PyQt6.QtWidgets import QApplication, QWidget

    app = QApplication(sys.argv)
    probe = QWidget()
    probe.resize(320, 200)
    print(f"Qt 版本：{QT_VERSION_STR}")
    print(f"PyQt6 版本：{PYQT_VERSION_STR}")
    print(f"平台插件：{app.platformName()}")
    print(f"控件可用：{probe.size().width()}x{probe.size().height()}")
    return 0


def main() -> int:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    # 排障用：RepoPulse.exe --git-info 打印实际在用的 Git，然后直接退出
    if "--git-info" in sys.argv:
        return _print_git_info()

    # 排障用：RepoPulse.exe --qt-self-test 验证 Qt 能加载，然后直接退出
    if "--qt-self-test" in sys.argv:
        return _print_qt_info()

    pid_path = _pid_file()
    _kill_stale_instance(pid_path)
    pid_path.write_text(str(os.getpid()), encoding="utf-8")

    app = QApplication(sys.argv)
    icon_path = Path(__file__).resolve().parent / "RepoPulse.png"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    apply_theme(app)
    # 使用统计（只报计数、已脱敏、全静默；绝不影响软件运行）
    telemetry.start()
    telemetry.heartbeat()

    window = MainWindow()
    window.show()
    code = 0
    try:
        code = app.exec()
    finally:
        telemetry.exit()      # os._exit 不跑 atexit：这一包必须显式发
        try:
            pid_path.unlink()
        except OSError:
            pass
    # 事件循环结束后强制结束进程，避免残留 QThread/定时器导致进程挂住、控制台不关闭
    os._exit(int(code or 0))


if __name__ == "__main__":
    raise SystemExit(main())
