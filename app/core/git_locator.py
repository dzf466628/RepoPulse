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
from functools import lru_cache
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


def _env_for(runtime: GitRuntime) -> dict[str, str]:
    """跑 git 子进程时用的环境：把内置目录放到 PATH 最前面。

    内置那份不保证在 PATH 上（用户可能没勾"登记到 PATH"），所以判断"这份 Git 到底
    带了什么"时必须自己拼，不能受外部 PATH 影响。
    """
    env = os.environ.copy()
    paths = list(support_paths(runtime))
    if paths:
        env["PATH"] = os.pathsep.join([*paths, env.get("PATH", "")])
    return env


def _run_git(runtime: GitRuntime, args: list[str], timeout: int = 15) -> str | None:
    """跑一条 git 命令，返回输出（没有输出返回 None），跑不起来也返回 None。"""
    try:
        result = subprocess.run(
            [runtime.exe, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_env_for(runtime),
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (result.stdout or result.stderr or "").strip() or None


def git_lfs_version(runtime: GitRuntime, timeout: int = 15) -> str | None:
    """跑一次 git lfs version，返回精简版本号（如 3.8.0）；没带 LFS 返回 None。"""
    text = _run_git(runtime, ["lfs", "version"], timeout)
    if not text:
        return None
    first = text.split()[0]
    if not first.startswith("git-lfs/"):
        return None
    return first.split("/", 1)[1]


# 内置 MinGit 自带证书的几种摆放位置（版本不同略有差异，按顺序取第一个存在的）
CA_BUNDLE_RELATIVE = (
    ("ucrt64", "etc", "ssl", "certs", "ca-bundle.crt"),
    ("mingw64", "etc", "ssl", "certs", "ca-bundle.crt"),
    ("etc", "ssl", "certs", "ca-bundle.crt"),
)


def ca_bundle_path(runtime: GitRuntime | None = None) -> Path | None:
    """这份 Git 自带的 CA 证书文件；找不到返回 None。"""
    runtime = runtime or resolve_git()
    if runtime is None or runtime.root is None:
        return None
    for parts in CA_BUNDLE_RELATIVE:
        candidate = runtime.root.joinpath(*parts)
        if candidate.is_file():
            return candidate
    return None


def _configured_ca_bundle(runtime: GitRuntime) -> str | None:
    """读当前生效的 http.sslCAInfo —— 可能来自用户配置，也可能是别处 include 进来的。"""
    return _run_git(runtime, ["config", "--get", "http.sslCAInfo"])


@lru_cache(maxsize=4)
def ssl_override_args(runtime: GitRuntime | None = None) -> tuple[str, ...]:
    r"""给 git 补的证书参数；不需要补就返回空元组。

    要出手的两种情况，都是"外部环境把内置 Git 弄坏"：

    1. 生效的 http.sslCAInfo 指向的文件不存在。典型场景：用户以前装过 Git 又卸载，
       卸载残留的系统配置里还写着旧证书路径；而 MinGit 的 etc\gitconfig 里有一条
       include 专门去继承 Git for Windows 的系统配置（MinGit 的官方设计，为了拿到
       core.autocrlf 这类设置），于是坏路径被继承进来 —— 所有 HTTPS 全失败，
       界面上只看到「GitHub 已忽略」，根本猜不到是证书的问题。
    2. 没配证书、但内置目录路径含非 ASCII 字符（比如装在中文目录里）。git 自己推出
       来的默认证书路径在这种目录下打不开，而显式传参反而正常（这点实测过）。

    用户自己配了**有效**证书时不动它 —— 企业内网自签 CA 不能被我们顶掉。
    结果缓存是因为每次构造 GitService 都会问一次，而它在一个进程里是稳定的。
    """
    runtime = runtime or resolve_git()
    if runtime is None or runtime.root is None:
        return ()  # 用的系统 Git，不掺和它的证书配置
    bundle = ca_bundle_path(runtime)
    if bundle is None:
        return ()
    configured = _configured_ca_bundle(runtime)
    if configured:
        if Path(configured).is_file():
            return ()  # 用户配的证书是好的，尊重它
    elif runtime.exe.isascii():
        return ()  # 没配证书、路径又纯 ASCII —— git 默认就找得到，不用管
    return ("-c", f"http.sslCAInfo={bundle}")


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