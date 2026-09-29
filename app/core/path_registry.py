# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""把内置 Git 登记到用户 PATH，让命令行 / AI / IDE 也能用上它。

软件自带一份 Git，但它躺在软件目录里，外面的程序根本不知道 —— 命令行敲 git、
AI 助手找 git、IDE 集成 Git 全都找不到。把这些目录登记进"用户 PATH" 之后，
`git` 就全机可用，用户不必另外装一个 Git（这也是自带这份 Git 该有的价值）。

只动当前用户（HKCU\\Environment\\Path），不需要管理员；已经登记过就跳过；
撤销时只删自己加的那一段，不碰用户原有的 PATH 项。
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

from app.core.git_locator import bundled_git_root

try:
    import winreg
except ImportError:  # 非 Windows：别让一个环境变量问题挡住软件启动
    winreg = None  # type: ignore[assignment]

ENV_KEY = "Environment"
ENV_VALUE = "Path"


def git_cmd_dir() -> Path | None:
    """要登记的目录：内置 Git 的 cmd（git.exe 和 git-lfs.exe 都在这里）。"""
    root = bundled_git_root()
    if root is None:
        return None
    return root / "cmd"


def entries(text: str) -> list[str]:
    """把 PATH 拆成一项一项（去掉空项和前后空格）。"""
    return [part.strip() for part in text.split(";") if part.strip()]


def has_entry(text: str, target: str) -> bool:
    """PATH 里有没有这一项。Windows 的 PATH 不区分大小写，比较时按大小写无关处理。"""
    wanted = os.path.normpath(target).lower()
    return any(os.path.normpath(part).lower() == wanted for part in entries(text))


def add_entry(text: str, target: str) -> str:
    """把目标目录追加到 PATH 末尾；已经有了就原样返回。"""
    if has_entry(text, target):
        return text
    parts = entries(text)
    parts.append(target)
    return ";".join(parts)


def remove_entry(text: str, target: str) -> str:
    """从 PATH 里删掉目标目录（重复的也一起删），其他项保持原顺序。"""
    wanted = os.path.normpath(target).lower()
    parts = [part for part in entries(text) if os.path.normpath(part).lower() != wanted]
    return ";".join(parts)


def _read_path() -> str:
    if winreg is None:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_KEY) as key:
            value, _ = winreg.QueryValueEx(key, ENV_VALUE)
    except OSError:
        return ""
    return value if isinstance(value, str) else ""


def _write_path(text: str) -> None:
    if winreg is None:
        raise OSError("这个系统上没有注册表写入接口")
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_SET_VALUE) as key:
        # 必须保持 REG_EXPAND_SZ：PATH 里常有 %USERPROFILE% 这类变量，
        # 一旦展开成实际路径再写回，用户原有的那些项就被改坏了。
        winreg.SetValueEx(key, ENV_VALUE, 0, winreg.REG_EXPAND_SZ, text)


def _broadcast() -> None:
    """告诉正在运行的程序"环境变量变了"，新开的窗口不用重启就能用上。"""
    try:
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 3000, None
        )
    except Exception:
        pass


def is_registered(target: str | None = None) -> bool:
    """内置 Git 的目录是不是已经在用户 PATH 里了。"""
    if target is None:
        directory = git_cmd_dir()
        if directory is None:
            return False
        target = str(directory)
    return has_entry(_read_path(), target)


def register() -> tuple[bool, str]:
    """登记到用户 PATH，返回 (是否成功, 给用户看的一句话)。"""
    directory = git_cmd_dir()
    if directory is None:
        return False, "没找到内置 Git，先重装一遍 RepoPulse 再试"
    text = _read_path()
    if has_entry(text, str(directory)):
        return True, "已经登记过了，命令行、AI、IDE 都能用这个 Git"
    try:
        _write_path(add_entry(text, str(directory)))
    except OSError as exc:
        return False, f"没写进去：{exc}"
    _broadcast()
    return True, "已登记，新开的命令行、AI、IDE 就能直接用 git 了"


def unregister() -> tuple[bool, str]:
    """从用户 PATH 里撤掉（只删自己加的那段）。"""
    directory = git_cmd_dir()
    if directory is None:
        return False, "没找到内置 Git"
    text = _read_path()
    if not has_entry(text, str(directory)):
        return True, "本来就没登记"
    try:
        _write_path(remove_entry(text, str(directory)))
    except OSError as exc:
        return False, f"没写进去：{exc}"
    _broadcast()
    return True, "已撤销，已经开着的程序要重启才会生效"