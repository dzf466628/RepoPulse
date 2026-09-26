"""使用统计上报（静默、非阻塞、已脱敏、只报计数）。

用户口径：「不获取敏感信息，只统计计数」「不做开关」「要脱敏」「有问题静默」。

设计要点（为什么这么写）
------------------------
* **零 Qt 依赖 + 守护线程**：上报走 ``threading`` + ``urllib``，不挂 QTimer、不碰窗口 ——
  于是它不阻塞启动（不占事件循环），也不会在退出顺序里被拆掉。心跳线程是 daemon，
  进程退出时不拦路。
* **脱敏到「能统计、认不出人」**：
  - 计算机名 → 只发前 5 个字符 + ``***``；
  - 本机 IP → 只留前三段（``192.168.0.*``）；
  - 机器标识 ``h`` → ``MAC + 硬盘序列号`` 做 SHA256 取前 16 位（不可逆）；
  - 上报里没有文件名、没有路径、没有仓库地址，只有功能名 + 次数。
* **一切异常吞掉**：网络不通、证书失败、DNS 挂掉、服务器 500 —— 全部静默。
* **只有 ``exit`` 那一包要等它一下**：本次运行的功能计数全在它身上。实测抓到的坑：
  ``exit`` 也走守护线程时进程一退线程就被掐掉，服务端只收到 ``start``、
  ``feat`` 永远是空的 —— 所以这一包最多等 1.5 秒，网络慢就丢掉，绝不拖住关窗。
* ⚠️ **本软件的 main.py 结尾是 ``os._exit()``，它不跑 atexit** —— 所以退出那一步
  必须在 ``finally`` 里**显式**调 ``telemetry.exit()``（见 main.py），不能只靠自动注册。

用法
----
    from app import telemetry
    telemetry.start()                  # 入口处调一次：发 start
    telemetry.heartbeat()              # 入口处调一次：起 60 秒心跳
    telemetry.track("提交并同步")        # 功能入口：计数 +1，怎么调都不会出错
    telemetry.exit()                   # 退出前显式调一次（os._exit 不走 atexit）
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import platform
import socket
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path

from app import __app_name__, __version__

# 上报地址：htsj 数据后台的软件统计通道。用 **8443** 而不是裸域名 —— 实测
# https://dudua.synology.me/htsj/api/health 从外网超时（80/443 在外网可能被封），
# 而 8443 这条通路稳定可用、证书对 dudua.synology.me 有效。
# 环境变量可覆盖（排查/自建服务端时用）：HTSJ_STATS_URL
_DEFAULT_URL = "https://dudua.synology.me:8443/htsj/api/app/report"
REPORT_URL = os.environ.get("HTSJ_STATS_URL") or _DEFAULT_URL
# 软件名：**从软件自己的名字变量取**，不写死 —— 软件改名这里自动跟着改，
# 不会再出现"探针里的名字和软件名对不上"。（服务端按它归档，看板按 ?app= 参数出页面）
APP = __app_name__.lower()
TIMEOUT = 5.0                     # 请求超时：慢就等于没有，绝不拖住后台线程
HEARTBEAT_SEC = 60                # 心跳间隔（服务端以 15 分钟内有心跳判定在线）
_SALT = "htsj-app-stats-2026"     # 固定盐：让哈希稳定（跨重装一致）又不直接暴露 MAC

_counts: dict[str, int] = {}
_ident: dict | None = None
_t0 = time.time()
_lock = threading.Lock()
_stop = threading.Event()         # 退出时按下，让心跳线程自己结束
_started = False
_hb_started = False
_exited = False


# --------------------------------------------------------------------------- #
# 脱敏与身份
# --------------------------------------------------------------------------- #
def _ident_path() -> Path:
    """实例身份存哪：跟本软件自己的配置目录一致（``%APPDATA%\\GitStatusDesk``），
    不另开目录、更不碰系统目录。"""
    base = os.environ.get("APPDATA")
    if not base:
        if sys.platform == "darwin":
            base = os.path.expanduser("~/Library/Application Support")
        else:
            base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "GitStatusDesk" / "stats_id.json"


def _local_ip() -> str:
    """本机局域网 IP（不真发包，只是让系统挑一张网卡）；失败返回空串。"""
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 53))
        return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return ""
    finally:
        try:
            if s is not None:
                s.close()
        except Exception:  # noqa: BLE001
            pass


def _mask_ip(ip: str) -> str:
    """脱敏：IPv4 只留前三段。"""
    try:
        parts = str(ip or "").split(".")
        return ".".join(parts[:3]) + ".*" if len(parts) == 4 else ""
    except Exception:  # noqa: BLE001
        return ""


def _mask_host(name: str) -> str:
    """脱敏：计算机名只留前 5 个字符 + ***（够人工认一批，认不出具体是谁）。"""
    s = str(name or "").strip()
    if not s:
        return ""
    return (s[:5] if len(s) > 5 else s) + "***"


def _disk_serial() -> str:
    """硬盘序列号。

    Windows 走 ``GetVolumeInformationW`` 取**系统卷序列号**：零依赖、微秒级，
    不启子进程、不调 WMI —— 那两种在启动路径上要多等几百毫秒，不值得。
    Linux / macOS 取根文件系统的设备号。拿不到就返回空串，
    此时机器标识退化为「只按 MAC 算」。
    """
    try:
        if sys.platform.startswith("win"):
            import ctypes
            root = (os.environ.get("SystemDrive") or "C:") + "\\"
            serial = ctypes.c_ulong(0)
            ok = ctypes.windll.kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(root), None, 0,
                ctypes.byref(serial), None, None, None, 0)
            return "%08X" % (serial.value & 0xFFFFFFFF) if ok else ""
        return "%x" % os.stat("/").st_dev
    except Exception:  # noqa: BLE001
        return ""


def _machine_hash() -> str:
    """机器标识：MAC + 硬盘序列号 做 SHA256 取前 16 位（同机器恒定、不可逆）。"""
    try:
        mac = "%012x" % uuid.getnode()
        raw = "%s|%s|%s" % (mac, _disk_serial(), _SALT)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return ""


def _os_name() -> str:
    """操作系统及版本，形如 ``Windows-10-10.0.19045``。"""
    try:
        return ("%s-%s-%s" % (platform.system(), platform.release(),
                              platform.version()))[:40]
    except Exception:  # noqa: BLE001
        try:
            return platform.platform()[:40]
        except Exception:  # noqa: BLE001
            return ""


def _load_ident() -> dict:
    """读/建实例身份。任何失败都退化成一个临时身份，绝不抛异常。"""
    global _ident
    if _ident is not None:
        return _ident
    try:
        host = socket.gethostname()
    except Exception:  # noqa: BLE001
        host = ""
    ident = {"id": "", "h": _machine_hash(), "hm": _mask_host(host),
             "os": _os_name(), "ip": _mask_ip(_local_ip())}
    try:
        path = _ident_path()
        if path.is_file():
            saved = json.loads(path.read_text(encoding="utf-8"))
            ident["id"] = str(saved.get("id") or "")
            if saved.get("h"):
                ident["h"] = str(saved["h"])   # 复用上次算的：机器标识永远稳定
        if not ident["id"] or not ident["h"]:
            if not ident["id"]:
                ident["id"] = uuid.uuid4().hex
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"id": ident["id"], "h": ident["h"]}),
                            encoding="utf-8")
    except Exception:  # noqa: BLE001
        if not ident["id"]:
            ident["id"] = uuid.uuid4().hex       # 存不下就用临时的
    _ident = ident
    return ident


# --------------------------------------------------------------------------- #
# 计数与上报
# --------------------------------------------------------------------------- #
def track(feature: str, n: int = 1) -> None:
    """记一次功能使用。**永远不抛异常**（统计绝不能影响正事）。"""
    try:
        key = str(feature or "")[:60]
        if not key:
            return
        with _lock:
            _counts[key] = _counts.get(key, 0) + max(1, int(n))
    except Exception:  # noqa: BLE001
        pass


def counts() -> dict:
    """当前计数快照（给探针/调试看，正式流程用不上）。"""
    with _lock:
        return dict(_counts)


def _payload(event: str, extra: dict | None = None) -> bytes:
    """固定基础字段 + 按事件类型的附加字段（start/hb 不带，exit 带 up/feat）。"""
    ident = _load_ident()
    body = {
        "app": APP,
        "t": event,
        "v": __version__,
        "id": ident.get("id", ""),
        "h": ident.get("h", ""),
        "hm": ident.get("hm", ""),
        "os": ident.get("os", ""),
        "ip": ident.get("ip", ""),
    }
    if extra:
        body.update(extra)
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def report(event: str, extra: dict | None = None):
    """组装并发一包（后台线程，**全静默**）。返回线程对象，退出时可等它一下。"""
    def run():
        try:
            req = urllib.request.Request(
                REPORT_URL, data=_payload(event, extra),
                headers={"Content-Type": "application/json; charset=utf-8"})
            urllib.request.urlopen(req, timeout=TIMEOUT).close()
        except Exception:  # noqa: BLE001
            pass

    try:
        th = threading.Thread(target=run, daemon=True)
        th.start()
        return th
    except Exception:  # noqa: BLE001
        return None


def _heartbeat_loop() -> None:
    """每 60 秒一包。用 Event.wait 而不是 sleep：退出时能立刻醒来收工。"""
    while not _stop.is_set():
        try:
            if _stop.wait(HEARTBEAT_SEC):
                return
            report("hb")
        except Exception:  # noqa: BLE001
            pass


def start() -> None:
    """入口处调一次：发 start，并注册退出上报。重复调用无副作用。"""
    global _started
    try:
        if _started:
            return
        _started = True
        _load_ident()                     # 提前建身份（失败也不抛）
        report("start")
        atexit.register(exit)             # 正常退出也发一次（os._exit 那条路靠显式调）
    except Exception:  # noqa: BLE001
        pass


def heartbeat() -> None:
    """入口处调一次：起心跳线程（每 60 秒一包）。重复调用无副作用。"""
    global _hb_started
    try:
        if _hb_started:
            return
        _hb_started = True
        threading.Thread(target=_heartbeat_loop, daemon=True).start()
    except Exception:  # noqa: BLE001
        pass


def exit() -> None:
    """退出上报：停心跳 + 发 exit（带本次运行时长 + 功能计数）。重复调用无副作用。

    这一包**必须等它发出去**，否则本次运行的功能计数就白统计了；但最多等 1.5 秒，
    网络慢就丢掉 —— 「有问题静默」，绝不拖住关窗。
    """
    global _exited
    _stop.set()
    try:
        if _exited:
            return
        _exited = True
        with _lock:
            feats = dict(_counts)
        th = report("exit", {"up": int(time.time() - _t0), "feat": feats})
        if th is not None:
            th.join(1.5)
    except Exception:  # noqa: BLE001
        pass