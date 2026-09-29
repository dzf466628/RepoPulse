# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

r"""定位 Git 可执行文件：内置优先，其次用户目录，最后系统 PATH。

RepoPulse 自带一份 MinGit（源码运行是 vendor\git，打包后在 _internal\git），
用户不需要自己装 Git。查找顺序这样定：

1. 内置那份永远优先 —— 版本可控、行为一致，不受用户机器上装了什么影响；
2. 万一内置缺失（被杀软删掉、手工删了），还能用系统已装的 Git 兜底；
3. 两边都没有时返回 None，由调用方给出人话提示，而不是抛 WinError 2。

另外内置 Git 的 ssh 在 usr\bin 里，GIT_SSH_COMMAND 写的是裸 ssh，
所以用内置 Git 时要把它的几个目录塞到子进程 PATH 最前面（见 support_paths）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# 排障用：设了这个环境变量就直接用它指的 git.exe，跳过全部查找
OVERRIDE_ENV = "REPOPULSE_GIT"

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class GitRuntime:
    """一个可用的 Git 运行时。"""

    exe: str
    source: str  # "内置" / "用户目录" / "系统" / "环境变量"
    root: Path | None  # 内置与用户目录那份才有根目录

    @property
    def label(self) -> str:
        return f"{self.source} Git（{self.exe}）"

def bundled_git_root() -> Path | None:
    r"""内置 Git 的根目录（下面有 cmd\git.exe、ucrt64、usr）。"""
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "git")  # 打包后：_internal\git
    candidates.append(Path(__file__).resolve().parents[2] / "vendor" / "git")  # 源码运行
    for root in candidates:
        if (root / "cmd" / "git.exe").is_file():
            return root
    return None


def user_git_root() -> Path | None:
    """用户目录下那份 Git（留给首启动自动下载安装这条后路）。"""
    base = os.environ.get("LOCALAPPDATA") or ""
    if not base:
        return None
    root = Path(base) / "RepoPulse" / "git"
    return root if (root / "cmd" / "git.exe").is_file() else None


def support_paths(runtime: GitRuntime | None) -> list[str]:
    """内置 Git 时要塞进子进程 PATH 的目录（让裸 ssh、sh 能被找到）。"""
    if runtime is None or runtime.root is None:
        return []
    return [
        str(runtime.root / "cmd"),
        str(runtime.root / "ucrt64" / "bin"),
        str(runtime.root / "usr" / "bin"),
    ]


def resolve_git() -> GitRuntime | None:
    """按优先级找一个可用的 Git；都找不到返回 None。"""
    override = os.environ.get(OVERRIDE_ENV, "").strip()
    if override and Path(override).is_file():
        return GitRuntime(exe=override, source="环境变量", root=None)

    bundled = bundled_git_root()
    if bundled is not None:
        return GitRuntime(exe=str(bundled / "cmd" / "git.exe"), source="内置", root=bundled)

    installed = user_git_root()
    if installed is not None:
        return GitRuntime(exe=str(installed / "cmd" / "git.exe"), source="用户目录", root=installed)

    found = shutil.which("git")
    if found:
        return GitRuntime(exe=found, source="系统", root=None)
    return None


def resolve_git_path() -> str | None:
    """只要路径的简化入口。"""
    runtime = resolve_git()
    return runtime.exe if runtime else None


def git_version(exe: str, timeout: int = 15) -> str | None:
    """跑一次 git --version，拿不到就返回 None。"""
    try:
        result = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (result.stdout or result.stderr or "").strip()
    return text or None


def git_lfs_version(runtime: GitRuntime, timeout: int = 15) -> str | None:
    """跑一次 git lfs version，返回精简版本号（如 3.8.0）；没带 LFS 返回 None。

    Git 是在 PATH 里找 git-lfs 的，而内置那份不保证在 PATH 上，所以这里把内置
    目录临时拼进 PATH —— 要判断的是"这份 Git 到底带没带 LFS"，不能受外部 PATH 影响。
    """
    env = os.environ.copy()
    paths = list(support_paths(runtime))
    if paths:
        env["PATH"] = os.pathsep.join([*paths, env.get("PATH", "")])
    try:
        result = subprocess.run(
            [runtime.exe, "lfs", "version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (result.stdout or result.stderr or "").strip()
    first = text.split()[0] if text else ""
    if not first.startswith("git-lfs/"):
        return None
    return first.split("/", 1)[1]


def check_git() -> tuple[GitRuntime | None, str | None]:
    """返回 (运行时, 版本)；运行时为 None 说明这台机器上没有可用 Git。"""
    runtime = resolve_git()
    if runtime is None:
        return None, None
    version = git_version(runtime.exe)
    if version is None:
        return None, None
    return runtime, version


def missing_git_message() -> str:
    """内置 Git 也找不到时，给用户看的话（不要提 WinError、不要提 PATH）。"""
    return (
        "软件自带的 Git 没找到，可能是被杀毒软件删掉了，或者安装不完整。\n\n"
        "可以试这几步：\n"
        "1. 关掉杀毒软件后，重新安装一遍 RepoPulse；\n"
        "2. 或者电脑上装一个 Git（git-scm.com），RepoPulse 会自动用它；\n"
        "3. 都不想动，重启一下软件再试。\n\n"
        "在修好之前，软件能打开，但新建项目、提交同步这些会失败。"
    )