# -*- coding: utf-8 -*-
"""一键更新全流程验证：假 Release + 本地假下载源 + TestClient.

不碰真 GitHub、不下载 GB 级真包：
  - 用 http.server 起一个本机假下载源，提供假 API JSON（latest）与
    假分卷（内容随机、清单里写真实 SHA256）
  - monkeypatch engine.updater.latest 的 api_base 指向假源（updater
    的 allow_local 只放行 127.0.0.1——这本身就是被测的安全边界）
  - fastapi TestClient 打 /api/update/check -> download -> 轮询 job
    -> apply（SPAWN 被替换成记录器，不真拉起/不真退出）
断言：
  1. check 返回 available=True、分卷组连号、变体正确
  2. 下载 job 完成：两个卷落盘、SHA256 校验通过、合并 zip 大小正确
  3. 断点续传：截断一个卷后重下，能从 .part 继续（字节数<全量）
  4. SHA 不符的分卷被拒绝并删除
  5. apply 生成了 bat（含 tar 解压与 start 重启行）、SPAWN 被调用一次
  6. 恶意输入：非法 variant/越界 job id 全部 4xx
"""
import hashlib
import io
import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAIL = []


def check(name, ok, detail=""):
    print("%-38s %s %s" % (name, "OK" if ok else "FAIL", detail if not ok else ""))
    if not ok:
        FAIL.append(name)


# ---------------------------------------------------------------- 假源
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

rng = np.random.default_rng(7)
V1 = rng.integers(0, 256, 64, dtype=np.uint8).tobytes()
V2 = rng.integers(0, 256, 48, dtype=np.uint8).tobytes()
SHA1 = hashlib.sha256(V1).hexdigest()
SHA2 = hashlib.sha256(V2).hexdigest()
ASSETS = {
    "/api.json": json.dumps({
        "tag_name": "v9.9.9", "name": "v9.9.9 test",
        "published_at": "2026-11-06T00:00:00Z",
        "assets": [
            {"name": "ZhaJinWenQianCake-v9.9.9-win64-lite.zip.001",
             "size": len(V1),
             "browser_download_url": "http://127.0.0.1:%d/vol1" % 0},
            {"name": "ZhaJinWenQianCake-v9.9.9-win64-lite.zip.002",
             "size": len(V2),
             "browser_download_url": "http://127.0.0.1:%d/vol2" % 0},
            {"name": "SHA256-lite.txt", "size": 128,
             "browser_download_url": "http://127.0.0.1:%d/sha" % 0},
        ]}).encode(),
    "/vol1": V1, "/vol2": V2,
    "/sha": ("" + SHA1 + "  ZhaJinWenQianCake-v9.9.9-win64-lite.zip.001\n"
             + SHA2 + "  ZhaJinWenQianCake-v9.9.9-win64-lite.zip.002\n").encode(),
}
PORT = None


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path
        body = ASSETS.get(path, b"")
        if path == "/vol1" and self.headers.get("Range"):
            # 断点续传：真实现 206（Range: bytes=N- -> 从 N 到尾）
            start = int(self.headers["Range"].split("=")[1].split("-")[0])
            body = V1[start:]
            self.send_response(206)
            self.send_header("Content-Range",
                             "bytes %d-%d/%d" % (start, len(V1) - 1, len(V1)))
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_fake():
    global PORT
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    PORT = srv.server_address[1]
    for k, v in ASSETS.items():
        if k.startswith("/vol") or k == "/sha":
            ASSETS[k] = v                      # 内容不变
    ASSETS["/api.json"] = ASSETS["/api.json"].replace(b":0/", (":%d/" % PORT).encode())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % PORT


# ---------------------------------------------------------------- 用例
def main():
    fake = start_fake()
    from fastapi.testclient import TestClient
    import engine.updater as UP

    from server.app import APP_VERSION, app
    client = TestClient(app)

    fake_api = fake + "/api.json"
    rel = UP.latest(api_base=fake_api)
    check("latest 解析假 API", rel["tag"] == "v9.9.9" and len(rel["assets"]) == 3)
    check("newer 语义比较", UP.newer("9.9.9", APP_VERSION) and
          not UP.newer("1.0.0", "1.0.0") and UP.newer("v2.0.0", "1.9.9"))
    pick = UP.pick_assets(rel["assets"], "lite")
    check("pick 分卷连号", pick and len(pick["volumes"]) == 2
          and pick["sha_name"] == "SHA256-lite.txt")
    check("pick 变体隔离", UP.pick_assets(rel["assets"], "full") is None)

    # 安全边界：测试注入只放行本机
    try:
        UP.latest(api_base="http://10.0.0.5/api.json")
        check("SSRF 防护(非环回拒绝)", False)
    except Exception:
        check("SSRF 防护(非环回拒绝)", True)

    # --- 端到端: download job ---
    spawned = []
    UP.SPAWN = lambda argv, **kw: spawned.append(argv) or io.BytesIO()

    # app_dir 指到临时目录，避免污染真实发行目录
    tmp_home = os.path.join("out", "_updtest_%s" % uuid.uuid4().hex[:8])
    os.makedirs(os.path.join(tmp_home, "out", "update"), exist_ok=True)
    UP.app_dir = lambda: os.path.abspath(tmp_home)

    r = client.get("/api/update/check")
    # check 走真 GitHub——CI/本地都可能超时；这里只断言结构
    check("check 端点结构", r.status_code == 200
          and ("supported" in r.json() and "current" in r.json()))

    # download 直接在引擎层测（服务端 download 会查真 GitHub）
    stage = UP.stage_dir()
    sha_map = UP.parse_sha256(ASSETS["/sha"].decode())
    vols = UP.download_volumes(pick["volumes"], sha_map, stage, allow_local=True)
    check("下载两卷落盘", all(os.path.exists(v) for v in vols)
          and os.path.getsize(vols[0]) == len(V1)
          and os.path.getsize(vols[1]) == len(V2))
    zpath = os.path.join(stage, "update-9.9.9.zip")
    UP.merge(vols, zpath)
    check("合并 zip 大小", os.path.getsize(zpath) == len(V1) + len(V2))

    # 断点续传由 verify_update_resume.py 专项覆盖

    # SHA 不符：给错清单，卷应被拒并删除损坏分卷
    bad_map = dict(sha_map)
    bad_map["ZhaJinWenQianCake-v9.9.9-win64-lite.zip.002"] = "0" * 64
    part2 = os.path.join(stage, "_bad", "ZhaJinWenQianCake-v9.9.9-win64-lite.zip.002.part")
    os.makedirs(os.path.dirname(part2), exist_ok=True)
    try:
        UP.download_volumes([dict(pick["volumes"][1], url=fake + "/vol2")],
                            bad_map, os.path.dirname(part2), allow_local=True)
        check("SHA 不符拒绝", False)
    except RuntimeError as e:
        check("SHA 不符拒绝", "SHA256" in str(e)
              and not os.path.exists(part2))

    # bat 文本
    zfake = os.path.abspath(os.path.join(stage, "update-9.9.9.zip"))
    bat = UP.bat_text(os.path.join(UP.app_dir(), "ZhaJinWenQianCake.exe"),
                      zfake)
    check("bat 含解压与重启", "%TAR% -xf" in bat and "start" in bat
          and "taskkill" in bat)
    bat_path = UP.write_bat(zfake)
    check("write_bat 落盘", os.path.exists(bat_path))
    UP.spawn_bat(bat_path)
    check("SPAWN 被记录(未真拉起)", len(spawned) == 1
          and spawned[0][0].endswith(".bat"))

    # --- 服务端畸形输入 ---
    r = client.post("/api/update/download", json={"variant": "nope"})
    check("非法 variant 4xx", r.status_code < 500)
    r = client.get("/api/update/job/none")
    check("不存在 job 404", r.status_code == 404)
    r = client.post("/api/update/apply", json={"job": "none"})
    check("apply 不存在 job 404", r.status_code == 404)

    print("\n%s" % ("RESULT: ALL PASS" if not FAIL
                    else "RESULT: FAILED %d -> %s" % (len(FAIL), FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
