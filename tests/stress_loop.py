# -*- coding: utf-8 -*-
"""十轮连续暴力测试 runner：六阶段 + 新功能端点轰炸（G 阶段）。

复用 stress_extreme 的 A-F 六阶段（畸形输入/会话洪泛/大图/并发/超清抢
GPU/随机参数），每轮插入 G 阶段——专门轰击本轮新增的三个端点：
    /api/passersby_detect   坏 session / 空 body / 连发
    /api/remove_passersby   exclude 畸形（非列表/长度错/含 None）
    /api/bgreplace          bg_png 空串/坏 base64/文本伪装
不变量与 stress_extreme 相同：任何 ConnectionError 致命，5xx 记 bug，
4xx 正确拒绝不计。10 轮全绿才算通过（用户口径：至少 10 次不出问题）。

目标必须是**新代码**的源码服务（默认 8766）——启动时探测
/api/passersby_detect 是否存在，打错旧包直接拒绝（首轮实测打 8765
旧包时新端点全是 5xx/404，结论无效）。G 阶段用小图会话：detect/
remove 每次都会跑 LaMa（秒级），放在 F 之后并用排空窗口，否则下一
轮 F 的 120s 超时是被压测自己堵出来的假致命。

用法：起源码服务（uvicorn, 8766），然后
    STRESS_ROUNDS=10 python -I tests/stress_loop.py
"""
import io
import os
import sys
import time

import numpy as np
import requests

sys.path.insert(0, "tests")
import stress_extreme as SX          # noqa: E402  复用六阶段与统计

B = os.environ.get("STRESS_TARGET") or "http://127.0.0.1:8766"
SX.B = B        # 六阶段函数引用 SX 模块内的 B，必须一并改向同一目标
                # （实测教训：只改本文件的 B，A-F 仍打 8765 旧包，E/F 的
                # "未创建齐/超时"全是旧包上的假信号）
ROUNDS = int(os.environ.get("STRESS_ROUNDS", "10"))


def _sess(long_edge=320):
    """小图会话：G 阶段专用。LaMa/分割耗时与像素数成正比，压测要的
    是"端点在、拒绝正确"，不是 8 秒一张的大图填充。"""
    img = np.random.rand(240, 320, 3).astype(np.float32) * 0.5 + 0.3
    import cv2
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(
        (np.clip(img, 0, 1) * 255).astype(np.uint8), cv2.COLOR_RGB2BGR))
    f = io.BytesIO(buf.tobytes())
    f.name = "g.jpg"
    r = requests.post(B + "/api/upload",
                      files={"file": ("g.jpg", f, "image/jpeg")}, timeout=120)
    return r.json().get("session") if r.ok else None


def phase_g():
    """G：新功能端点的极端输入（小图 + 正确拒绝口径）。"""
    sid = _sess()

    # G1 detect：坏 session / 空 body / 连发
    for bad in ({}, {"session": ""}, {"session": "nope"}):
        r = requests.post(B + "/api/passersby_detect", json=bad, timeout=60)
        if r.status_code >= 500:
            SX.issues.append("detect 坏输入 5xx: %s" % r.status_code)
    r = requests.post(B + "/api/passersby_detect",
                      json={"session": sid}, timeout=120)
    if r.status_code >= 500:
        SX.issues.append("detect 正常路径 5xx")

    # G2 remove_passersby：exclude 畸形全谱（4xx = 正确拒绝）
    bad_excludes = [
        {"session": sid, "exclude": "not-a-list"},
        {"session": sid, "exclude": [1, 2, 3]},
        {"session": sid,
         "exclude": [[0.1, 0.1], None, "x", [0.1, 0.2, 0.3, 0.4]]},
        {"session": sid, "exclude": [[9e9, -9e9, 1e-9, 5e8]]},
    ]
    for be in bad_excludes:
        r = requests.post(B + "/api/remove_passersby", json=be, timeout=180)
        if r.status_code >= 500:
            SX.issues.append("remove exclude 畸形 5xx: %s" % str(be)[:40])
    r = requests.post(B + "/api/remove_passersby",
                      json={"session": sid, "exclude": [],
                            "model": "lama"}, timeout=180)
    if r.status_code >= 500:
        SX.issues.append("remove 空exclude 5xx")

    # G3 bgreplace：bg_png 畸形（4xx = 正确拒绝）
    for png in ("", "not-base64!!", "AAAA", None, 12345):
        r = requests.post(B + "/api/bgreplace", json={
            "session": sid, "bg_png": png}, timeout=180)
        if r.status_code >= 500:
            SX.issues.append("bgreplace bg_png 畸形 5xx: %r" % png)
    # 正常小图（commit=False 不占历史）
    import base64
    import cv2 as _cv
    bg = np.random.rand(200, 300, 3).astype(np.float32)
    ok, buf = _cv.imencode(".jpg", (bg * 255).astype(np.uint8))
    r = requests.post(B + "/api/bgreplace", json={
        "session": sid, "bg_png": base64.b64encode(buf.tobytes()).decode(),
        "commit": False}, timeout=180)
    if r.status_code >= 500:
        SX.issues.append("bgreplace 正常小图 5xx")

    print("  G 阶段: detect/remove/bgreplace 极端输入完成, 累计 bug %d"
          % len(SX.issues), flush=True)


def main():
    print("十轮暴力测试目标:", B, "轮数:", ROUNDS, flush=True)
    if requests.get(B + "/api/health", timeout=15).status_code != 200:
        print("服务器不可达:", B)
        return 1
    # 防打错目标：从 OpenAPI schema 查新路由（不能用状态码探测——
    # detect 对坏 session 也返回业务 404，与路由 404 无法区分）
    try:
        schema = requests.get(B + "/openapi.json", timeout=15).json()
        if "/api/passersby_detect" not in schema.get("paths", {}):
            print("目标 %s 没有 /api/passersby_detect —— 这是旧包，"
                  "请起源码服务(8766)再跑" % B)
            return 1
    except Exception as exc:
        print("openapi 探测失败:", str(exc)[:100])
        return 1
    t0 = time.time()
    for rd in range(1, ROUNDS + 1):
        print("\n" + "#" * 60, flush=True)
        print("# 第 %d / %d 轮" % (rd, ROUNDS), flush=True)
        print("#" * 60, flush=True)
        for name, fn in (("A 畸形输入轰击", SX.phase_a),
                         ("B 会话洪泛(LRU)", SX.phase_b),
                         ("C 大图 12MP", SX.phase_c),
                         ("D 并发混合轰炸", SX.phase_d),
                         ("E 真超清双任务抢 GPU", SX.phase_e),
                         ("F 参数随机暴力 90s", SX.phase_f),
                         ("G 新功能端点轰炸", phase_g)):
            SX.check_phase("[第%d轮] %s" % (rd, name), fn)
        # 队列排空窗口：E 的超清任务与 G 的 LaMa 可能还在后台跑，
        # 不留时间会把下一轮 F 的 120s 超时变成假致命（首轮实测）。
        drain = 0
        while drain < 90:
            try:
                j = requests.get(B + "/api/health", timeout=15).json()
                if int(j.get("sessions", 0)) <= 2:
                    break
            except Exception:
                pass
            time.sleep(10)
            drain += 10
        f_now, i_now = len(SX.fatal), len(SX.issues)
        print(">>> 第 %d 轮结束: 累计 fatal=%d bug=%d 用时 %.0f 分钟"
              % (rd, f_now, i_now, (time.time() - t0) / 60), flush=True)
        if f_now:
            break            # 致命即停，不空转剩余轮次

    print("\n=== 内存轨迹（每阶段首尾） ===")
    for s in SX.mem_samples:
        print("  %s -> %s" % (s[0], "->".join("%.0f MB" % x
                                              for x in s[1:])))
    print("\n=== 总计（%d 轮目标） ===" % ROUNDS)
    for f in SX.fatal:
        print("  致命:", f)
    for i in SX.issues:
        print("  bug:", i)
    if not SX.fatal and not SX.issues:
        print("RESULT: SURVIVED CLEAN — %d 轮全部存活，零 5xx，新端点零 bug"
              % ROUNDS)
        return 0
    print("RESULT: FOUND %d fatal / %d issues" % (len(SX.fatal),
                                                  len(SX.issues)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
