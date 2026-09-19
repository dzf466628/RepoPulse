"""offscreen 冒烟测试。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.models import ProjectConfig, RemoteConfig
from app.storage.project_store import ProjectStore
from app.ui.main_window import MainWindow


app = QApplication(sys.argv)
PASSED = 0
FAILED = 0


def check(name: str, fn) -> None:
    global PASSED, FAILED
    try:
        fn()
        print(f"  [OK]   {name}")
        PASSED += 1
    except Exception as exc:
        print(f"  [FAIL] {name}: {exc}")
        FAILED += 1


def test_window_creates() -> None:
    window = MainWindow()
    assert window.minimumWidth() >= 900
    window.close()


def test_project_store_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "projects.json"
        project = ProjectConfig(
            project_id="demo",
            name="演示项目",
            workspace_path="C:/repo",
            default_branch="main",
            remotes={"nas": RemoteConfig("nas", "ssh://nas/repo.git")},
        )
        store = ProjectStore(path)
        store.save([project])
        loaded = store.load()
        assert loaded[0].name == "演示项目"
        assert loaded[0].remotes["nas"].url == "ssh://nas/repo.git"


if __name__ == "__main__":
    print("=" * 50)
    check("主窗口创建", test_window_creates)
    check("项目配置读写", test_project_store_roundtrip)
    print("=" * 50)
    print(f"PASSED {PASSED}  |  FAILED {FAILED}")
    raise SystemExit(0 if FAILED == 0 else 1)
