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
        _qt_path = _bundle_root / "PyQt6" / _qt_name
        if _qt_path.is_file():
            _qt_preloaded.append(ctypes.WinDLL(str(_qt_path)))

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

from app.ui.main_window import MainWindow
from app.ui.theme import apply_theme


def main() -> int:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    pid_path = _pid_file()
    _kill_stale_instance(pid_path)
    pid_path.write_text(str(os.getpid()), encoding="utf-8")

    app = QApplication(sys.argv)
    icon_path = Path(__file__).resolve().parent / "RepoPulse.png"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    apply_theme(app)
    window = MainWindow()
    window.show()
    try:
        return app.exec()
    finally:
        try:
            pid_path.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
