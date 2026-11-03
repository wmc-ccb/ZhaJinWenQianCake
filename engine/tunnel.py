# -*- coding: utf-8 -*-
"""外网隧道（Cloudflare TryCloudflare）：一键生成临时公网链接。

产品决策（2026-10-31）：
- 免注册、免账号：TryCloudflare 快速隧道直接给随机
  xxxx-xxxx-xxxx.trycloudflare.com 域名，手机在任何网络都能访问。
- 安全强制项：隧道流量经本机 cloudflared 转发，源 IP 是回环——
  server 端按 cf-connecting-ip 头识别并**强制要求口令**（见
  server/app.py 中间件）。启动隧道前若无口令，调用方自动生成。
- cloudflared.exe 缺失时从 Cloudflare 官方 Release 自动下载（~20MB，
  白名单限定 github.com 系），存到 out/cloudflared/cloudflared.exe。
- 链接是临时的：进程退出/重启后旧链接失效，需重新生成。

与 comfy_mgr（AI 服务）的进程管理同构：启停接口 + 状态轮询。
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
import urllib.request

from .paths import out_dir

_ALLOWED_HOSTS = {"github.com", "objects.githubusercontent.com",
                  "release-assets.githubusercontent.com"}
CLOUDFLARED_URL = ("https://github.com/cloudflare/cloudflared/releases/latest/"
                   "download/cloudflared-windows-amd64.exe")

_STATE = {
    "proc": None,
    "url": "",
    "phase": "idle",         # idle|starting|running|error
    "error": "",
}
_PROC = None               # 我们拉起的 cloudflared 进程
_LOCK = threading.RLock()   # 可重入：start() 在锁内调 status()，Lock 会自死锁


def _bin_path() -> str:
    """cloudflared.exe 存放位置：发行根 out/cloudflared/。"""
    d = os.path.join(out_dir(), "cloudflared")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "cloudflared.exe")


def cloudflared_ok() -> bool:
    return os.path.exists(_bin_path()) and os.path.getsize(_bin_path()) > 1 << 20


def _safe_url(url: str) -> str:
    """只放行 https + GitHub 官方域（下载源固定，拒绝动态目标）。"""
    import urllib.parse
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in _ALLOWED_HOSTS:
        raise ValueError("非法下载源: %s" % url[:120])
    return url


def ensure_cloudflared() -> str:
    """确保 cloudflared.exe 存在，缺失则从官方 Release 下载（~20MB）。"""
    p = _bin_path()
    if os.path.exists(p) and os.path.getsize(p) > 1 << 20:
        return p
    url = _safe_url(CLOUDFLARED_URL)
    tmp = p + ".part"
    req = urllib.request.Request(url, headers={
        "User-Agent": "ZhaJinWenQianCake-Updater"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                print("[cloudflared] %.0f%%" % (got * 100 / total),
                      flush=True)
    with open(tmp, "rb") as f:
        if f.read(2) != b"MZ":
            os.remove(tmp)
            raise RuntimeError("下载内容不是有效的 Windows 程序")
    os.replace(tmp, p)
    return p


def start() -> dict:
    """启动外网隧道。已在跑则直接返回当前链接。

    cloudflared.exe 缺失时先在后台线程下载（52MB，数秒到数十秒），
    期间 status.phase = downloading；下载完自动接着启动隧道。
    """
    with _LOCK:
        if _STATE["phase"] in ("running", "starting", "downloading"):
            return status()
        if not cloudflared_ok():
            _STATE.update(phase="downloading", url="", error="")
        else:
            _STATE.update(phase="starting", url="", error="")

    def _ensure_then_run():
        try:
            ensure_cloudflared()
        except Exception as exc:
            with _LOCK:
                _STATE["phase"] = "error"
                _STATE["error"] = "下载 cloudflared 失败: %s" % str(exc)[:120]
            return
        proc = _spawn()
        _pump(proc, 40.0)

    threading.Thread(target=_ensure_then_run, daemon=True).start()
    return status()


def _spawn():
    """拉起 cloudflared 进程（转发到本机应用端口），返回 Popen。"""
    # 转发目标跟应用端口走（launch.py --lan 时写入 RETOUCHLAB_PORT）
    port = int(os.environ.get("RETOUCHLAB_PORT", "8765"))
    exe = _bin_path()
    proc = subprocess.Popen(
        [exe, "tunnel", "--url", "http://127.0.0.1:%d" % port,
         "--no-autoupdate"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    with _LOCK:
        _PROC = proc
    return proc


def _pump(proc, timeout: float) -> None:
    """逐行读 stderr，提取 trycloudflare 链接（打印在 stderr 上）。"""
    pat = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    deadline = time.time() + timeout
    try:
        for raw in iter(proc.stderr.readline, b""):
            m = pat.search(raw.decode("utf-8", "replace"))
            if m:
                with _LOCK:
                    if _STATE["phase"] == "starting":
                        _STATE["url"] = m.group(0)
                        _STATE["phase"] = "running"
                return
            if time.time() > deadline:
                break
    except Exception:
        pass
    with _LOCK:
        if _STATE["phase"] == "starting":
            _STATE["phase"] = "error"
            _STATE["error"] = "40 秒内未获得公网链接（检查本机网络）"


def stop() -> dict:
    with _LOCK:
        proc = _PROC
        _PROC = None
        _STATE.update(phase="idle", url="")
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    return status()


def status() -> dict:
    with _LOCK:
        s = dict(_STATE)
        proc = _PROC
    # 进程意外退出 → 链接失效
    if proc is not None and s["phase"] in ("running", "starting") \
            and proc.poll() is not None:
        s["phase"] = "error"
        s["error"] = "隧道进程已退出（链接失效，请重新生成）"
        s["url"] = ""
    return s
