# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""凭据加密存储：用 Windows DPAPI（当前用户作用域）保护 Token / 密码。

设计约束（项目已上线，必须向后兼容）：
- 存储格式：``dpapi:v1:<base64>`` 前缀 + 密文；没有前缀的值一律按旧版明文处理，
  下一次保存时自动加密迁移，旧配置文件不需要任何手工操作。
- 解密失败（典型：把 projects.json 拷到另一台电脑/另一个 Windows 账户）返回空串，
  让界面显示为未填凭据，用户重新填一次即可——绝不抛异常打断启动。
- 加密失败（理论上只在非 Windows 或 DPAPI 异常时发生）回落明文存储，
  行为与旧版本完全一致，宁可退回旧安全水位也不能丢用户数据。

DPAPI 本身零依赖（ctypes 直调 crypt32.dll），CurrentCaller 作用域下密文只能被
同一台机器上的同一个 Windows 账户解开，足以覆盖"同机其他程序/人拿到 json 文件"的威胁。
"""

from __future__ import annotations

import base64
import ctypes
import os

# 存储前缀：写入密文时带上，读取时靠它区分密文与旧版明文
_PREFIX = "dpapi:v1:"

_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _Blob(ctypes.Structure):
    """crypt32 的 DATA_BLOB。"""

    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.c_void_p),
    ]


def _dpapi_available() -> bool:
    return os.name == "nt"


def encrypt_secret(plain: str) -> str:
    """加密为 dpapi:v1: 前缀的密文；失败（含非 Windows）原样返回明文。"""
    value = plain or ""
    if not value or value.startswith(_PREFIX) or not _dpapi_available():
        # 已是密文/空串/无法加密时不动，避免二次包裹或丢数据
        return value
    try:
        data = value.encode("utf-8")
        in_blob = _Blob(len(data), ctypes.cast(
            ctypes.create_string_buffer(data, len(data)), ctypes.c_void_p
        ))
        out_blob = _Blob()
        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(in_blob), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
        ):
            return value
        try:
            encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(out_blob.pbData))
        return _PREFIX + base64.b64encode(encrypted).decode("ascii")
    except Exception:  # noqa: BLE001 - 加密绝不能影响保存流程
        return value


def decrypt_secret(stored: str) -> str:
    """解开 dpapi:v1: 密文；无前缀按旧版明文返回；解密失败返回空串。

    返回空串而不是抛异常：凭据解不开时软件必须照常启动，
    界面上 Token 显示为空，用户重新填写一次即可恢复。
    """
    value = stored or ""
    if not value.startswith(_PREFIX):
        return value  # 旧版明文（或空串）原样返回，保存时自动迁移为密文
    if not _dpapi_available():
        return ""
    try:
        encrypted = base64.b64decode(value[len(_PREFIX):], validate=True)
        in_blob = _Blob(len(encrypted), ctypes.cast(
            ctypes.create_string_buffer(encrypted, len(encrypted)), ctypes.c_void_p
        ))
        out_blob = _Blob()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
        ):
            return ""
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(out_blob.pbData))
    except Exception:  # noqa: BLE001 - 换机器/换账户/密文损坏都落到这里
        return ""
