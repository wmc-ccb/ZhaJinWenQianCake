# -*- coding: utf-8 -*-
"""一键更新：从 GitHub Releases 下载新版本并就地替换.

流程（对齐"傻瓜式"目标——用户只看到进度条和一个按钮）：
  1. latest()      查 GitHub 最新 Release（api.github.com，URL 固定常量）
  2. pick_assets() 按变体(full/lite)挑出 .zip.00N 分卷 + SHA256 清单
  3. download_volumes() 逐卷下载：断点续传(.part)、逐跳重定向校验、
     DNS 解析阻断私网/环回、每卷 SHA256 强校验
  4. merge()       按序拼接成完整 zip（卷与输出都强制在 stage_dir 内）
  5. write_bat()/spawn_bat()  生成并拉起"更新器"批处理：等应用退出
     -> System32 bsdtar 解压覆盖 -> 删包 -> 自动重启

安全边界（Mimosa 审计建议全部落实，校验紧贴每个磁盘操作）：
  - 请求前 _guard_url：协议白名单 + 域名白名单 + getaddrinfo 解析后
    阻断私网/环回/链路本地地址（防 SSRF/DNS rebinding）
  - 下载走 _OPENER（自定义 RedirectHandler）：每个重定向目标重新过
    _guard_url，跳出白名单即拒绝
  - 资产名 _safe_name 必须 = basename；每个 open() 前一行做
    normpath + ".." 分量拒绝 + stage_dir 前缀包含校验（三重防穿越）
  - 商店版(MSIX)不启用：服务端 /api/update/* 直接拒绝

测试口径：latest(api_base=...) 仅接受 http://127.0.0.1（allow_local），
生产默认常量走严格校验；SPAWN 可被测试替换成记录器，避免真拉起。
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from typing import Optional

from .paths import app_dir, home

RELEASES_URL = ("https://api.github.com/repos/wmc-ccb/ZhaJinWenQianCake"
                "/releases?per_page=20")
_ALLOWED_HOSTS = {"api.github.com", "github.com",
                  "objects.githubusercontent.com",
                  "release-assets.githubusercontent.com"}
UA = "ZhaJinWenQianCake-Updater"
STAGE_SUB = os.path.join("out", "update")

# 测试注入口：verify_update 替换成记录器，生产是真 subprocess
SPAWN = subprocess.Popen


class Cancelled(RuntimeError):
    pass


# ---------------------------------------------------------------- 路径守卫
def _stage_abs() -> str:
    d = os.path.abspath(os.path.join(home(), STAGE_SUB))
    os.makedirs(d, exist_ok=True)
    return d


def stage_dir() -> str:
    """更新包暂存目录（可写根 out/update/）。供服务端与测试使用。"""
    return _stage_abs()


def _no_dotdot(p: str) -> str:
    """abspath+normpath，显式拒绝 .. 分量（每个 open 前调用）。"""
    p2 = os.path.abspath(os.path.normpath(p))
    if ".." in p2.split(os.sep):
        raise RuntimeError("路径不允许包含 ..")
    return p2


def _require_stage(p: str) -> str:
    """强制路径（已规范化）落在 stage_dir 内。紧贴 open 使用。"""
    p2 = _no_dotdot(p)
    stage = os.path.abspath(stage_dir())
    if p2 != stage and not p2.startswith(stage + os.sep):
        raise RuntimeError("路径越界（不在更新暂存目录）")
    return p2


# ---------------------------------------------------------------- URL 守卫
def _guard_url(url: str, allow_local: bool = False) -> None:
    """请求前的 URL/主机安全检查.

    生产模式（allow_local=False）：必须 https 且域名在 _ALLOWED_HOSTS，
    且 DNS 解析出的全部 IP 必须是公网地址（阻断内网/云元数据/环回）。
    allow_local=True 仅供测试指向本机假 API（http://127.0.0.1）。
    """
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("https", "http"):
        raise RuntimeError("仅允许 http(s) 下载源")
    host = u.hostname or ""
    if allow_local:
        if u.scheme != "http" or host not in ("127.0.0.1", "localhost"):
            raise RuntimeError("测试模式只允许 http://127.0.0.1")
    else:
        if u.scheme != "https" or host not in _ALLOWED_HOSTS:
            raise RuntimeError("下载域不在白名单: %s" % url[:100])
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception as exc:
        raise RuntimeError("DNS 解析失败: %s" % str(exc)[:80])
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if allow_local:
            if not ip.is_loopback:
                raise RuntimeError("测试目标解析到非环回地址")
        elif not ip.is_global:
            raise RuntimeError("目标域名解析到非公网地址: %s" % str(ip))


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """重定向逐跳过 _guard_url——GitHub 资产 302 到 objects.
    githubusercontent.com 这一跳也在白名单内；跳出白名单即拒绝。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _guard_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirect)


def _open(url: str, headers: dict = None, allow_local: bool = False):
    _guard_url(url, allow_local=allow_local)
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               **(headers or {})})
    return _OPENER.open(req, timeout=60)


# ---------------------------------------------------------------- 查新
def latest(list_base: str = RELEASES_URL) -> dict:
    """查最新的**版本号** Release：tag/资产清单.

    拉 releases 列表（而非 /releases/latest）——列表里混着 models-v1
    这类模型分发 Release，它不带版本号语义，latest 端点按创建时间取
    会把它当"最新版"返回（实测）。这里取第一个 tag 匹配 vX.Y.Z 语义
    的条目；全都不匹配才回落第一个。
    list_base 仅测试注入（限本机 http）。
    """
    with _open(list_base, {"Accept": "application/vnd.github+json"},
               allow_local=(list_base != RELEASES_URL)) as r:
        j = json.loads(r.read().decode("utf-8"))
    items = j if isinstance(j, list) else [j]
    chosen = None
    for it in items:
        tag = str(it.get("tag_name", "")).lstrip().lower()
        if re.match(r"^v?\d+\.\d+(\.\d+)?", tag):
            chosen = it
            break
    if chosen is None and items:
        chosen = items[0]
    assets = [{"name": _safe_name(a.get("name", "")),
               "size": int(a.get("size", 0)),
               "url": a.get("browser_download_url", "")}
              for a in chosen.get("assets", [])]
    return {"tag": str(chosen.get("tag_name", "")),
            "name": str(chosen.get("name", "")),
            "published": str(chosen.get("published_at", "")),
            "assets": assets}


def _safe_name(name: str) -> str:
    """资产名必须 = basename（GitHub 会清理，落盘前再验一次）。"""
    base = os.path.basename(name.replace("\\", "/"))
    if not base or base != name or base in (".", ".."):
        raise ValueError("非法资产名: %r" % name[:60])
    return base


def newer(remote: str, current: str) -> bool:
    """语义化比较（忽略前导 v 与预发布后缀），remote > current 才 True。"""
    def nums(v: str):
        return [int(x) for x in re.findall(r"\d+", v.split("-")[0])]
    a, b = nums(remote), nums(current)
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else 0
        y = b[i] if i < len(b) else 0
        if x != y:
            return x > y
    return False


def pick_assets(assets: list, variant: str) -> Optional[dict]:
    """从资产清单挑出变体的分卷组：volumes(按 .001 连号) + SHA 清单名."""
    vols = sorted([a for a in assets
                   if a["name"].startswith("ZhaJinWenQianCake-")
                   and "-%s.zip." % variant in a["name"]],
                  key=lambda a: a["name"])
    if not vols:
        return None
    expect = 1
    for a in vols:
        if not a["name"].endswith(".%03d" % expect):
            return None
        expect += 1
    sha_name = "SHA256-%s.txt" % variant
    sha = next((a for a in assets if a["name"] == sha_name), None)
    return {"volumes": vols, "sha_name": sha["name"] if sha else None}


def parse_sha256(text: str) -> dict:
    """SHA 清单 -> {名: hash}，容忍 CRLF。"""
    out = {}
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line or "  " not in line:
            continue
        h, name = line.split("  ", 1)
        out[name.strip()] = h.strip().lower()
    return out


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(8 << 20), b""):
            h.update(c)
    return h.hexdigest()


# ---------------------------------------------------------------- 下载
def download_volumes(items: list, sha_map: dict, dest: str,
                     progress=None, cancel=None, retries: int = 4,
                     allow_local: bool = False) -> list:
    """逐卷下载（断点续传）+ SHA256 校验，返回落盘文件路径列表.

    progress(done, total, vol_idx, n_vols)。cancel -> True 抛
    Cancelled（.part 保留供续传）。allow_local 仅测试注入本机假源。
    """
    stage = _stage_abs()
    dest = os.path.abspath(os.path.normpath(dest))
    # 目录允许 == stage（整个暂存目录就是下载目标）；文件必须带分隔符
    if dest != stage and not dest.startswith(stage + os.sep):
        raise RuntimeError("下载目录越界")
    paths = []
    total = sum(a["size"] for a in items)
    done_base = 0
    for idx, a in enumerate(items):
        name = _safe_name(a["name"])
        # ---- 落盘路径三重校验（normpath + .. 拒绝 + stage 前缀）----
        dest_path = os.path.abspath(os.path.normpath(os.path.join(dest, name)))
        if ".." in dest_path.split(os.sep) \
                or not dest_path.startswith(stage + os.sep):
            raise RuntimeError("落盘路径越界: %s" % name[:40])
        paths.append(dest_path)
        expect = (sha_map or {}).get(name)
        if os.path.exists(dest_path) \
                and not os.path.exists(dest_path + ".part") and expect:
            if _sha256_file(dest_path) == expect:
                done_base += a["size"]
                if progress:
                    progress(done_base, total, idx + 1, len(items))
                continue
        # ---- .part 文件路径同样校验 ----
        part = os.path.abspath(os.path.normpath(dest_path + ".part"))
        if ".." in part.split(os.sep) \
                or not part.startswith(stage + os.sep):
            raise RuntimeError("临时文件路径越界")
        have = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"User-Agent": UA}
        if 0 < have < a["size"]:
            headers["Range"] = "bytes=%d-" % have
        _guard_url(a["url"], allow_local=allow_local)
        for attempt in range(retries):
            try:
                with _open(a["url"], headers,
                           allow_local=allow_local) as r:
                    mode = "ab" if (r.status == 206 and have) else "wb"
                    got = have if (r.status == 206 and have) else 0
                    with open(part, mode) as f:
                        while True:
                            if cancel is not None and cancel():
                                raise Cancelled()
                            chunk = r.read(1 << 20)
                            if not chunk:
                                break
                            f.write(chunk)
                            got += len(chunk)
                            if progress:
                                progress(done_base + got, total,
                                         idx + 1, len(items))
                break
            except Cancelled:
                raise
            except Exception:
                have = os.path.getsize(part) if os.path.exists(part) else 0
                if attempt == retries - 1:
                    raise
                time.sleep(2 * (attempt + 1))
        if os.path.getsize(part) != a["size"]:
            raise RuntimeError("%s 大小不符" % name)
        if expect and _sha256_file(part) != expect:
            os.remove(part)
            raise RuntimeError("%s SHA256 不符（已删除损坏分卷）" % name)
        os.replace(part, dest_path)
        done_base += a["size"]
    return paths


def merge(volumes: list, out_zip: str, progress=None) -> str:
    """按序拼接分卷成完整 zip。卷与输出都强制在 stage_dir 内。"""
    stage = os.path.abspath(stage_dir())
    out2 = os.path.abspath(os.path.normpath(out_zip))
    if ".." in out2.split(os.sep) or not out2.startswith(stage + os.sep):
        raise RuntimeError("输出 zip 路径越界")
    vols = []
    for v in volumes:
        v2 = os.path.abspath(os.path.normpath(v))
        if ".." in v2.split(os.sep) or not v2.startswith(stage + os.sep):
            raise RuntimeError("分卷路径越界")
        vols.append(v2)
    total = sum(os.path.getsize(v) for v in vols)
    done = 0
    with open(out2, "wb") as o:
        for v in vols:
            with open(v, "rb") as f:
                while True:
                    chunk = f.read(8 << 20)
                    if not chunk:
                        break
                    o.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    return out2


# ---------------------------------------------------------------- 安装
def bat_text(exe: str, zip_path: str) -> str:
    """更新器批处理：等应用退出 -> System32 bsdtar 解压覆盖 -> 删包 ->
    重启。ASCII-only。解锁探测 = 尝试把 exe 改名（成功即已退出）。"""
    exe = os.path.abspath(os.path.normpath(exe))
    zip_path = os.path.abspath(os.path.normpath(zip_path))
    stage = os.path.abspath(stage_dir())
    if ".." in zip_path.split(os.sep) \
            or not zip_path.startswith(stage + os.sep):
        raise RuntimeError("zip 不在更新暂存目录内")
    parent = os.path.dirname(exe)
    name = os.path.basename(exe)
    # 逐行构造 + 逐行格式化：bat 里有大量 %N%/%TAR% 这类 cmd 变量，
    # 不能用隐式字符串拼接后再整体 % ——会把 %S 当格式符（实测 ValueError）
    lines = [
        "@echo off",
        "title ZhaJinWenQianCake Updater",
        'set EXE="%s"' % exe,
        'set ZIP="%s"' % zip_path,
        'set PARENT="%s"' % parent,
        "set TAR=%SystemRoot%\\System32\\tar.exe",
        "echo Updating ZhaJinWenQianCake, please wait...",
        "set /a N=0",
        ":wait",
        'ren "%s" _old_%s >nul 2>&1' % (exe, name),
        'if exist "%s\\_old_%s" goto unlocked' % (parent, name),
        "set /a N+=1",
        "if %N% GEQ 60 goto forced",
        "timeout /t 1 /nobreak >nul",
        "goto wait",
        ":forced",
        "taskkill /im %s /f >nul 2>&1" % name,
        "timeout /t 2 /nobreak >nul",
        ":unlocked",
        'if exist "%s\\_old_%s" ren "%s\\_old_%s" %s'
        % (parent, name, parent, name, name),
        "%TAR% -xf %ZIP% -C %PARENT%",
        "if errorlevel 1 (",
        "  echo Extract failed. Keeping downloaded package for retry.",
        "  pause",
        "  exit /b 1",
        ")",
        "del /f /q %ZIP% >nul 2>&1",
        'start "" %EXE%',
        "exit /b 0",
    ]
    return "\r\n".join(lines) + "\r\n"


def write_bat(zip_path: str) -> str:
    """生成更新器 bat。zip 与 bat 路径都做 stage 包含校验。"""
    exe = os.path.abspath(os.path.normpath(
        os.path.join(app_dir(), "ZhaJinWenQianCake.exe")))
    stage = os.path.abspath(stage_dir())
    zip2 = os.path.abspath(os.path.normpath(zip_path))
    if ".." in zip2.split(os.sep) or not zip2.startswith(stage + os.sep):
        raise RuntimeError("zip 不在更新暂存目录内")
    bat = os.path.abspath(os.path.normpath(os.path.join(stage,
                                                        "apply_update.bat")))
    if ".." in bat.split(os.sep) or not bat.startswith(stage + os.sep):
        raise RuntimeError("bat 路径越界")
    with open(bat, "w", encoding="ascii") as f:
        f.write(bat_text(exe, zip2))
    return bat


def spawn_bat(bat: str):
    """拉起更新器（独立控制台窗口，用户能看到 Updating...）。"""
    bat = os.path.abspath(os.path.normpath(bat))
    return SPAWN([bat], creationflags=0x10, close_fds=True)


def free_gb(path: str) -> float:
    import shutil
    try:
        return shutil.disk_usage(path).free / 1024 ** 3
    except Exception:
        return -1.0
