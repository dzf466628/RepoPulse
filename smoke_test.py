# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""offscreen 冒烟测试。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.core.git_locator import bundled_git_root, check_git
from app.core.git_service import GitService
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
    window._allow_close = True
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


def test_git_available() -> None:
    runtime, version = check_git()
    assert runtime is not None, "没找到可用 Git（源码运行请先执行 tools\\fetch_minigit.ps1）"
    assert version is not None and "git version" in version, f"git 版本异常：{version}"


def test_bundled_git_present() -> None:
    root = bundled_git_root()
    assert root is not None, "内置 Git 没就位：请先执行 tools\\fetch_minigit.ps1"
    assert (root / "cmd" / "git.exe").is_file(), "内置 Git 缺少 cmd\\git.exe"
    assert (root / "usr" / "bin" / "ssh.exe").is_file(), "内置 Git 缺少 ssh.exe，SSH 渠道会不可用"


def test_git_service_uses_bundled() -> None:
    service = GitService()
    assert service.git_runtime is not None, "GitService 没找到 Git"
    assert service.git == service.git_runtime.exe, "GitService 用的不是解析出来的那份 Git"
    assert service._run(["--version"]).startswith("git version"), "GitService 跑不通 git --version"


if __name__ == "__main__":
    print("=" * 50)
    check("主窗口创建", test_window_creates)
    check("项目配置读写", test_project_store_roundtrip)
    check("Git 可用", test_git_available)
    check("内置 Git 已就位", test_bundled_git_present)
    check("GitService 绑定内置 Git", test_git_service_uses_bundled)
    print("=" * 50)
    print(f"PASSED {PASSED}  |  FAILED {FAILED}")
    raise SystemExit(0 if FAILED == 0 else 1)
