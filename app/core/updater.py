# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""启动时自动检查更新 + 自动下载更新。

服务器端零配置：把安装包（RepoPulse-Setup-vX.Y.Z.exe）直接上传到源站目录即可
（源站用 .index.php 生成目录列表），客户端请求该列表，从文件名中解析出版本号最高的
安装包，与本地版本比较，有新版本时通知 UI 引导下载并自动安装。

关于证书：源站 dudua.synology.me:8443 已部署受信任证书（Let's Encrypt），
这里严格校验证书、不做任何回落——证书一旦失效或不被信任，“检查更新”会明确报错，
而不是静默降级去接受来路不明的安装包。
"""
from __future__ import annotations

import hashlib
import os
import re
import urllib.request
import urllib.parse

from PyQt6.QtCore import QThread, pyqtSignal

# 更新检查目录：源站地址，必须带端口号 8443；.index.php 负责生成目录列表
# （直接访问目录会 403，只能走 .index.php）。
UPDATE_URL = "https://dudua.synology.me:8443/download/RepoPulse/.index.php"
CHECK_TIMEOUT = 8  # 秒；网络异常/超时都静默跳过，不打扰用户

# 安装包文件名模式：
#   RepoPulse-Setup-v0.2.3.exe（现行命名）
#   RepoPulse_v0.2.3.exe / RepoPulse_v.0.2.3.exe（兼容 SpriteSheetTool 风格）
_INSTALLER_RE = re.compile(
    r"RepoPulse(?:-Setup)?[_-][vV]\.?(\d+(?:\.\d+)*)\.exe", re.IGNORECASE
)


def _open(url: str, timeout: int, accept: str = "text/html"):
    """严格校验证书地打开 URL（自动更新链路上绝不放宽 TLS）。"""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "RepoPulse-Updater/1.0", "Accept": accept},
    )
    return urllib.request.urlopen(request, timeout=timeout)


def _parse_version(text: str) -> tuple[int, ...]:
    """把 '1.2.3' 解析成 (1,2,3) 用于比较。容忍非数字段。"""
    parts = []
    for seg in str(text).strip().split("."):
        digits = "".join(ch for ch in seg if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _pick_latest(html: str, base_url: str) -> dict | None:
    """从目录索引 HTML 中找出版本号最高的安装包，返回 {version, url}；没有则 None。"""
    best = None  # (version_tuple, version_str, url)
    for match in _INSTALLER_RE.finditer(html):
        version_str = match.group(1)
        ver = _parse_version(version_str)
        href = match.group(0)
        url = urllib.parse.urljoin(base_url, href)
        if best is None or ver > best[0]:
            best = (ver, version_str, url)
    if best is None:
        return None
    return {"version": best[1], "url": best[2]}


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch_expected_sha256(installer_url: str) -> str | None:
    """尝试获取安装包随附的校验文件（<安装包URL>.sha256，sha256sum 格式）。

    返回小写十六进制校验值；服务器上没有该文件、或内容里解析不出 64 位
    十六进制值（例如返回的是目录列表 HTML）时返回 None——视为"发布方尚未
    提供校验"，由调用方决定是否放行。
    """
    checksum_url = installer_url.rstrip("/") + ".sha256"
    try:
        with _open(checksum_url, CHECK_TIMEOUT, accept="text/plain") as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - 404/网络抖动都按"无校验文件"处理
        return None
    match = re.search(r"\b([a-fA-F0-9]{64})\b", text)
    return match.group(1).lower() if match else None


class UpdateCheckThread(QThread):
    """后台检查更新，不阻塞 UI，完成时发出 result_ready 信号。

    信号 dict 的 status：
      - "update"：发现新版本（含 version/url）
      - "latest"：已是最新（含 version=服务器最高版本）
      - "no_installer"：目录里没有安装包（含 url）
      - "error"：网络/解析失败（含 reason）
    """

    result_ready = pyqtSignal(object)  # dict

    def __init__(self, current_version: str, url: str = UPDATE_URL, parent=None):
        super().__init__(parent)
        self._current = current_version
        self._url = url

    def run(self) -> None:
        try:
            with _open(self._url, CHECK_TIMEOUT) as resp:
                html = resp.read().decode("utf-8", errors="replace")
                final_url = resp.geturl()  # 跟随重定向后的真实地址
            latest = _pick_latest(html, final_url)
            if latest is None:
                self.result_ready.emit({"status": "no_installer", "url": final_url})
                return
            if _parse_version(latest["version"]) > _parse_version(self._current):
                latest["status"] = "update"
                self.result_ready.emit(latest)
            else:
                latest["status"] = "latest"
                self.result_ready.emit(latest)
        except Exception as exc:  # noqa: BLE001 - 网络异常统一静默转 error 状态
            self.result_ready.emit({"status": "error", "reason": f"{type(exc).__name__}: {exc}"})


class UpdateDownloadThread(QThread):
    """后台下载安装包，带进度报告。"""

    # 字节数必须用 qint64：PyQt 的 int 是 C++ 32 位，安装包超过 2 GiB 时
    # Content-Length 会溢出成负数，接收端 total>0 判 false，进度条永远停在 0。
    progress = pyqtSignal("qint64", "qint64")  # done_bytes, total_bytes
    download_finished = pyqtSignal(str)   # 本地文件路径
    download_failed = pyqtSignal(str)     # 错误信息

    def __init__(self, url: str, target_dir: str, parent=None):
        super().__init__(parent)
        self._url = url
        self._target_dir = target_dir
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            os.makedirs(self._target_dir, exist_ok=True)
            filename = urllib.parse.unquote(self._url.rstrip("/").split("/")[-1]) or "update.exe"
            target = os.path.join(self._target_dir, filename)
            resp = _open(self._url, 60, accept="application/octet-stream")
            with resp:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                with open(target, "wb") as fh:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        if self._cancel:
                            fh.close()
                            try:
                                os.remove(target)
                            except OSError:
                                pass
                            return
                        fh.write(chunk)
                        done += len(chunk)
                        self.progress.emit(done, total)
            if os.path.getsize(target) == 0:
                raise OSError("下载文件为空")
            # 完整性校验：发布目录里如果同时上传了 <安装包>.sha256（打包脚本会自动
            # 生成），下载结果必须与之一致才允许安装——哈希对不上就删包中止，
            # 防止被劫持/损坏的安装包被静默执行。没有校验文件时保持旧行为放行。
            expected = _fetch_expected_sha256(self._url)
            if expected is not None:
                actual = _sha256_file(target)
                if actual != expected:
                    try:
                        os.remove(target)
                    except OSError:
                        pass
                    self.download_failed.emit(
                        "安装包完整性校验失败（SHA256 不一致），已中止安装。\n"
                        "这通常意味着下载不完整或文件被篡改。\n"
                        "请稍后重试，或到官网手动下载安装包。"
                    )
                    return
            self.download_finished.emit(target)
        except Exception as exc:  # noqa: BLE001
            self.download_failed.emit(f"{type(exc).__name__}: {exc}")
