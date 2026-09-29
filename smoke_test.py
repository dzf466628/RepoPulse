# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""offscreen 冒烟测试。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel

from app.core.git_locator import (
    bundled_git_root,
    ca_bundle_path,
    check_git,
    git_lfs_version,
    ssl_override_args,
)
from app.core.git_service import GitService
from app.models import ProjectConfig, RemoteConfig
from app.storage.project_store import ProjectStore
from app.ui.main_window import (
    BADGE_TEXT,
    MainWindow,
    StatusCard,
    badge_color,
    render_glyph_pixmap,
    short_number,
    sync_badge_text,
)


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


def test_bundled_lfs_present() -> None:
    root = bundled_git_root()
    assert root is not None, "内置 Git 没就位：请先执行 tools\\fetch_minigit.ps1"
    assert (root / "cmd" / "git-lfs.exe").is_file(), (
        "内置 Git 缺少 cmd\\git-lfs.exe，用了 LFS 的仓库会把大文件本体提交进仓库"
    )


def test_lfs_ready() -> None:
    service = GitService()
    assert service.git_runtime is not None, "GitService 没找到 Git"
    version = git_lfs_version(service.git_runtime)
    assert version, "内置 git-lfs 跑不起来（没带 LFS 或被杀软删了）"
    assert service.ensure_lfs().startswith("LFS 就绪"), "LFS 启动自检没通过"


def _with_global_config(content: str):
    """临时把全局配置换掉（用 GIT_CONFIG_GLOBAL），返回一个 contextmanager 式的还原函数。"""
    directory = tempfile.mkdtemp()
    config = Path(directory) / "gitconfig"
    config.write_text(content, encoding="utf-8")
    previous = os.environ.get("GIT_CONFIG_GLOBAL")
    os.environ["GIT_CONFIG_GLOBAL"] = str(config)
    ssl_override_args.cache_clear()

    def restore() -> None:
        ssl_override_args.cache_clear()
        if previous is None:
            os.environ.pop("GIT_CONFIG_GLOBAL", None)
        else:
            os.environ["GIT_CONFIG_GLOBAL"] = previous

    return restore


def test_bundled_ca_bundle() -> None:
    runtime, _version = check_git()
    bundle = ca_bundle_path(runtime)
    assert bundle is not None and bundle.is_file(), "内置 Git 缺少 CA 证书，HTTPS 渠道会全部连不上"


def test_ssl_override_when_ca_broken() -> None:
    """证书路径被外部配置写坏时必须自动补上内置证书。

    不然表现就是：网络明明是通的，界面上却一直显示「GitHub 已忽略」。
    """
    restore = _with_global_config("[http]\n\tsslCAInfo = C:/definitely/not/here/ca.crt\n")
    try:
        assert "http.sslCAInfo=" in " ".join(ssl_override_args()), "坏证书路径没有触发兜底"
    finally:
        restore()


def test_ssl_override_keeps_valid_ca() -> None:
    """用户自己配的有效证书不能被顶掉（企业内网自签 CA）。"""
    with tempfile.TemporaryDirectory() as directory:
        bundle = Path(directory) / "ca.crt"
        bundle.write_text("dummy\n", encoding="utf-8")
        restore = _with_global_config(f"[http]\n\tsslCAInfo = {bundle.as_posix()}\n")
        try:
            assert ssl_override_args() == (), "用户自己配的有效证书被顶掉了"
        finally:
            restore()


class _StubRemote:
    """_remote_state_key 只用到 kind 这一个属性。"""

    def __init__(self, kind: str) -> None:
        self.kind = kind


def test_badge_texts_fit() -> None:
    """角标必须放得下：卡片里硬限 116px，超了就被截成「GitHub 已…」。"""
    candidates = [
        "一致", "待推送", "待拉取", "已分叉", "待首次同步", "待创建", "待同步",
        "已忽略", "未连接", "连接失败", "读取失败", "未配置", "分支不存在", "等待检查",
        *BADGE_TEXT.values(),
    ]
    for text in candidates:
        label = QLabel(text)
        label.setStyleSheet("font-size: 11px; font-weight: 700;")
        metrics = label.fontMetrics()
        assert metrics.elidedText(text, Qt.TextElideMode.ElideRight, 116) == text, (
            f"角标「{text}」会被截断"
        )


def test_badge_states() -> None:
    """角标：方向说清楚、报错原文不进角标、术语跟分叉弹窗一致。"""
    window = MainWindow()
    nas, local, github = _StubRemote("nas"), _StubRemote("local"), _StubRemote("github")
    cases = [
        (nas, {"online": True, "relation": "本地领先", "ahead": 3, "behind": 0}, "待推送"),
        (nas, {"online": True, "relation": "远程领先", "ahead": 0, "behind": 2}, "待拉取"),
        (nas, {"online": True, "relation": "已分叉", "ahead": 1, "behind": 1}, "已分叉"),
        (nas, {"online": True, "relation": "一致"}, "一致"),
        (nas, {"online": True, "relation": "已连接"}, "待拉取"),
        (nas, {"online": True, "relation": "未配置"}, "未配置"),
        (nas, {"online": True, "relation": "待首次同步"}, "待首次同步"),
        (nas, {"online": False}, "未连接"),
        (nas, {"online": False, "error": "fatal: unable to access ..."}, "连接失败"),
        (github, {"ignored": True, "online": False, "error": "证书路径失效"}, "已忽略"),
        (local, {"online": True, "relation": "一致"}, "一致"),
        (local, {"online": True, "relation": "版本不同", "ahead": 3, "behind": 0}, "待推送"),
        (local, {"online": True, "relation": "当前 Git 渠道"}, "待同步"),
        (local, {"online": False}, "读取失败"),
    ]
    try:
        for item, data, expected in cases:
            got = window._remote_state_key(item, data)[1]
            assert got == expected, f"{data} 得到「{got}」，期望「{expected}」"
        assert sync_badge_text({"ahead": 3, "behind": 0}) == "待推送"
        assert sync_badge_text({"ahead": 0, "behind": 2}) == "待拉取"
        assert sync_badge_text({"ahead": 3, "behind": 2}) == "已分叉"
        assert sync_badge_text({"ahead": 0, "behind": 0}) == "一致"
    finally:
        window._allow_close = True
        window.close()


def test_badge_colors_per_state() -> None:
    """四个状态四个颜色，而且跨渠道一致（同一个词不许两种颜色）。"""
    colors = {
        "一致": badge_color("一致", "clean"),
        "待推送": badge_color("待推送", "different"),
        "待拉取": badge_color("待拉取", "different"),
        "已分叉": badge_color("已分叉", "warning"),
    }
    assert len(set(colors.values())) == 4, f"四个状态颜色有重复：{colors}"
    for text in ("一致", "待推送", "待拉取", "已分叉", "待同步", "已忽略"):
        assert badge_color(text, "warning") == badge_color(text, "different"), (
            f"「{text}」在不同渠道颜色不一样"
        )


def test_stat_icons() -> None:
    """star / eye 图标要画得出来，尺寸固定 13px。"""
    for kind in ("star", "eye"):
        pixmap = render_glyph_pixmap(kind, "", 13)
        assert not pixmap.isNull() and pixmap.width() == 13, f"{kind} 图标画不出来"


def test_short_number() -> None:
    """热度数字必须压到很窄，不然会把卡片标题挤到换行。"""
    assert short_number(1) == "1"
    assert short_number(999) == "999"
    assert short_number(1234) == "1.2k"
    assert short_number(9999) == "10k"
    assert short_number(123456) == "12.3w"
    assert short_number(999999) == "100w"
    assert short_number(None) == ""
    assert len(short_number(999999)) <= 5


def test_github_stats_widget_width() -> None:
    """星标 + 浏览量那一块要窄（角标左边就那么点地方）。"""
    card = StatusCard(
        "k", "GitHub Remote · GitHub", "", "待推送", "#9CC6FF", "地址：x",
        rich_body=True, recent_commit="修复登录问题",
        stats=[("star", "1"), ("eye", "12")],
    )
    card.resize(560, 175)
    card.show()
    app.processEvents()
    try:
        header = card.layout().itemAt(0).layout()
        widget = header.itemAt(header.count() - 2).widget()
        assert widget is not None, "统计元素没有加进头部"
        width = widget.sizeHint().width()
        assert width <= 96, f"统计元素太宽：{width}px"
    finally:
        card.close()


def test_github_metrics_quiet() -> None:
    """非 GitHub 渠道必须安静返回空，不能发请求、不能报错。"""
    service = GitService()
    assert service.github_metrics(_StubRemote("local"), None) == {}
    assert service.github_metrics(_StubRemote("nas"), None) == {}


if __name__ == "__main__":
    print("=" * 50)
    check("主窗口创建", test_window_creates)
    check("项目配置读写", test_project_store_roundtrip)
    check("Git 可用", test_git_available)
    check("内置 Git 已就位", test_bundled_git_present)
    check("GitService 绑定内置 Git", test_git_service_uses_bundled)
    check("内置 git-lfs 已就位", test_bundled_lfs_present)
    check("LFS 可用", test_lfs_ready)
    check("内置 CA 证书已就位", test_bundled_ca_bundle)
    check("证书路径坏掉时自动兜底", test_ssl_override_when_ca_broken)
    check("不顶掉用户自配的有效证书", test_ssl_override_keeps_valid_ca)
    check("角标文案放得下（≤116px）", test_badge_texts_fit)
    check("角标状态与方向映射", test_badge_states)
    check("角标按状态配色（四态四色）", test_badge_colors_per_state)
    check("star / 浏览量图标画得出来", test_stat_icons)
    check("热度数字压缩（1.2k / 12.3w）", test_short_number)
    check("热度元素宽度 ≤96px", test_github_stats_widget_width)
    check("非 GitHub 渠道不发热度请求", test_github_metrics_quiet)
    print("=" * 50)
    print(f"PASSED {PASSED}  |  FAILED {FAILED}")
    raise SystemExit(0 if FAILED == 0 else 1)
