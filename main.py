from __future__ import annotations

import sys
import os
from pathlib import Path

# PyInstaller keeps PyQt6's Qt DLLs below the bundled runtime directory.
# Register that directory before importing any Qt extension modules.
if sys.platform == "win32" and getattr(sys, "frozen", False):
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

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from app.ui.main_window import MainWindow
from app.ui.theme import apply_theme


def main() -> int:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    app = QApplication(sys.argv)
    icon_path = Path(__file__).resolve().parent / "RepoPulse.png"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    apply_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
