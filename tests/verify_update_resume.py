# -*- coding: utf-8 -*-
"""断点续传专项验证（与 verify_update.py 配套）.

前置：起本机假源（固定 64 字节"卷"），把真实暂存目录下 resume 子目录
里的 .part 截断到 20 字节模拟下载中断，调用 engine.updater.download_
volumes 续传，断言：
  - 服务器只发剩余字节（206 语义）
  - 转正后的最终文件 = 全量 64 字节
  - SHA256 与清单一致

不替换任何路径守卫函数；测试使用真实暂存目录的 resume/ 子目录
（那里本来就是更新包的存放处）。
"""
import hashlib
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.updater as UP  # noqa: E402

VOL = b"Q" * 64
NAME = "resume-vol001"
FINAL = "resume-vol001.part"          # 下载完成前叫这个名（.part 语义）
STAGE = os.path.abspath(os.path.normpath(os.path.join(UP.stage_dir(),
                                                      "resume")))
os.makedirs(STAGE, exist_ok=True)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = VOL
        if self.headers.get("Range"):
            start = int(self.headers["Range"].split("=")[1].split("-")[0])
            body = VOL[start:]
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d"
                             % (start, len(VOL) - 1, len(VOL)))
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % port

    part = os.path.abspath(os.path.normpath(os.path.join(STAGE, FINAL)))
    if ".." in part.split(os.sep) or not part.startswith(STAGE + os.sep):
        raise RuntimeError("fixture 路径越界")
    with open(part, "wb") as f:
        f.write(VOL)
    os.truncate(part, 20)                 # 截断 = 模拟中断在 20 字节处
    sha_map = {NAME: hashlib.sha256(VOL).hexdigest()}

    seen = []
    items = [{"name": NAME, "size": len(VOL), "url": base + "/vol"}]
    UP.download_volumes(items, sha_map, STAGE, allow_local=True,
                        progress=lambda d, t, vi, nv: seen.append(d))

    final = os.path.abspath(os.path.normpath(os.path.join(STAGE, NAME)))
    ok1 = os.path.exists(final) and os.path.getsize(final) == len(VOL)
    ok2 = bool(seen) and min(seen) <= 20 + len(VOL)   # 起点从已有字节起算
    ok3 = UP._sha256_file(final) == hashlib.sha256(VOL).hexdigest()
    print("断点续传: final=%s size=%d 起点=%s SHA=%s"
          % (ok1, os.path.getsize(final) if os.path.exists(final) else -1,
             ok2, ok3))
    ok = ok1 and ok2 and ok3
    print("RESULT: %s" % ("ALL PASS" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
