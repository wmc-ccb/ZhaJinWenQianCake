# -*- coding: utf-8 -*-
"""发布工具：打包双版本 zip → 切分卷 → 生成校验/合并说明 → 上传 Release.

为什么切分：GitHub Release 单文件上限 2GB，完整发行包 6GB 超限。
按 1.5GB 卷切分后逐卷上传；用户侧合并只要一条 copy /b 命令
（合并.bat 已生成在卷旁边）。

用法：
    python tools/make_release.py                # 建 zip + 切分 + 校验
    python tools/make_release.py --skip-build   # 跳过 PyInstaller（dist 已就绪）
    python tools/make_release.py --upload v1.1.0  # 上传卷到指定 Release

前置：PyInstaller 已装；gh 已登录（repo 权限）。
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist", "ZhaJinWenQianCake")
DIST_DIR = os.path.join(ROOT, "dist")
PART_MB = 1536            # 单卷上限（留余量低于 GitHub 2GB 限制）
GH_REPO = "wmc-ccb/ZhaJinWenQianCake"


def read_version() -> str:
    with open(os.path.join(ROOT, "server", "app.py"), encoding="utf-8") as f:
        for line in f:
            if line.startswith("APP_VERSION"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("APP_VERSION 未找到")


def run(cmd, **kw):
    r = subprocess.run(cmd, **kw)
    if r.returncode != 0:
        raise SystemExit("命令失败: %s" % " ".join(cmd))
    return r


def _contained(base: str, name: str) -> str:
    """按名拼路径并强制落在 base 内（发布目录是固定根，拒绝一切越界）。"""
    base = os.path.realpath(base)
    p = os.path.realpath(os.path.join(base, name))
    if not p.startswith(base + os.sep):
        raise SystemExit("路径越界: %s" % name)
    return p


def build():
    print("[构建] PyInstaller…", flush=True)
    run([sys.executable, "-m", "PyInstaller",
         os.path.join(ROOT, "ZhaJinWenQianCake.spec"),
         "--noconfirm", "--clean"], cwd=ROOT)
    print("[组装] assemble_dist…", flush=True)
    run([sys.executable, os.path.join(ROOT, "tools", "assemble_dist.py")],
        cwd=ROOT)


def zip_version(variant: str) -> str:
    """生成 zip 并返回路径。variant: full | lite"""
    exe = os.path.join(DIST, "ZhaJinWenQianCake.exe")
    if os.path.exists(exe):
        # 打包版在跑会锁死 dist，给出明确指引而不是默默 taskkill
        r = subprocess.run(["tasklist", "/FI",
                            "IMAGENAME eq ZhaJinWenQianCake.exe"],
                           capture_output=True)
        if b"ZhaJinWenQianCake.exe" in r.stdout:
            raise SystemExit("ZhaJinWenQianCake.exe 正在运行（窗口或后台），"
                             "请先关闭应用再打包。")
    # 清运行痕迹
    for d in ("out", "data"):
        p = os.path.join(DIST, d)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        os.makedirs(p, exist_ok=True)
    if variant == "lite":
        models = os.path.join(DIST, "models")
        shutil.rmtree(models, ignore_errors=True)
        os.makedirs(models, exist_ok=True)
        # lite 模式重跑 assemble（models_manifest.json 就位 + 空模型目录）
        env = dict(os.environ, ZJWQC_LITE="1")
        run([sys.executable, os.path.join(ROOT, "tools", "assemble_dist.py")],
            cwd=ROOT, env=env)
    else:
        # full：模型必须齐全（源 models/ 拷入）
        src_models = os.path.join(ROOT, "models")
        dst_models = os.path.join(DIST, "models")
        if not os.path.isdir(dst_models) or not os.listdir(dst_models):
            shutil.rmtree(dst_models, ignore_errors=True)
            shutil.copytree(src_models, dst_models)
    stamp = os.path.join(DIST, "models_manifest.json")
    if variant == "lite" and not os.path.exists(stamp):
        shutil.copy2(os.path.join(ROOT, "tools", "models_manifest.json"),
                     stamp)
    if variant == "full" and os.path.exists(stamp):
        os.remove(stamp)          # full 内置模型，不需要清单
    out = os.path.join(DIST_DIR,
                       "ZhaJinWenQianCake-v%s-win64-%s.zip"
                       % (read_version(), variant))
    if os.path.exists(out):
        os.remove(out)
    # 必须用 bsdtar + 显式 --format zip：PATH 里的 Git Bash GNU tar 不支持
    # zip 格式，`-a` 会静默 fallback 成 ustar（扩展名却是 .zip，解不开——
    # 2026-11-04 验收抓到过这个坑）。
    tar_exe = r"C:\Windows\System32\tar.exe"
    if not os.path.exists(tar_exe):
        tar_exe = "tar"
    run([tar_exe, "--format", "zip", "-c", "-f", out, "ZhaJinWenQianCake"],
        cwd=DIST_DIR)
    print("[zip] %s (%.2f GB)" % (os.path.basename(out),
                                  os.path.getsize(out) / 1024 ** 3))
    return out


def split(path: str, part_mb: int = PART_MB) -> list:
    """按大小切分文件，返回卷文件列表（全部落在原目录内）。"""
    size = os.path.getsize(path)
    n = (size + part_mb * 1048576 - 1) // (part_mb * 1048576)
    parts = []
    with open(path, "rb") as f:
        for i in range(n):
            part = "%s.%03d" % (path, i + 1)
            left = part_mb * 1048576
            with open(part, "wb") as o:
                while left > 0:
                    chunk = f.read(min(left, 8 << 20))
                    if not chunk:
                        break
                    o.write(chunk)
                    left -= len(chunk)
            parts.append(part)
    return parts


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(8 << 20), b""):
            h.update(c)
    return h.hexdigest()


def prepare_release_dir(variant: str, zip_path: str) -> list:
    """切分卷 + 合并.bat + SHA256 清单，返回待上传文件列表。"""
    rel = os.path.join(DIST_DIR, "release-" + variant)
    shutil.rmtree(rel, ignore_errors=True)
    os.makedirs(rel, exist_ok=True)
    parts = split(zip_path, PART_MB)
    for p in parts:
        shutil.move(p, os.path.join(rel, os.path.basename(p)))
    names = [os.path.basename(p) for p in parts]
    # 合并.bat（copy /b 串联恢复 zip）
    bat = "copy /b " + " + ".join(names) + " " + os.path.basename(zip_path)
    bat_path = _contained(rel, "合并.bat")
    with open(bat_path, "w", encoding="ascii") as f:
        f.write(bat + "\r\n")
    # SHA256
    lines = []
    for n in names + [os.path.basename(zip_path)]:
        p = _contained(rel, n)
        if os.path.exists(p):
            lines.append("%s  %s" % (sha256(p), n))
    sums = _contained(rel, "SHA256SUMS.txt")
    with open(sums, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("[切分] %s -> %d 卷" % (os.path.basename(zip_path), len(parts)))
    return [os.path.join(rel, n) for n in
            names + ["SHA256SUMS.txt", "合并.bat"]]


def upload(release_tag: str, files: list) -> None:
    for f in files:
        print("[上传] %s (%.2f GB)…"
              % (os.path.basename(f), os.path.getsize(f) / 1024 ** 3),
              flush=True)
        run(["gh", "release", "upload", release_tag, f,
             "--clobber", "-R", GH_REPO])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-build", action="store_true",
                    help="跳过 PyInstaller/assemble（dist 已就绪）")
    ap.add_argument("--upload", metavar="TAG", help="上传卷到指定 Release")
    ap.add_argument("--part-mb", type=int, default=PART_MB)
    args = ap.parse_args()

    if not args.skip_build:
        build()

    # 分发已收敛为**单包**（lite 形态：程序本体 + 模型首启下载）。
    # full 变体（内置 6GB 模型）不再生产——模型走 GitHub 首启下载，
    # 更新也只走 lite 卷（updater 固定）。
    lite = zip_version("lite")
    lite_files = prepare_release_dir("lite", lite)

    # 合并回环校验：拼回后 sha 必须等于原 zip
    parts = [f for f in lite_files if f.endswith((".001", ".002", ".003"))]
    merged = os.path.join(DIST_DIR, "release-lite",
                          "merged_" + os.path.basename(lite))
    with open(merged, "wb") as o:
        for p in sorted(parts):
            with open(p, "rb") as fh:
                shutil.copyfileobj(fh, o)
    if sha256(merged) != sha256(lite):
        raise SystemExit("合并回环校验失败: lite")
    os.remove(merged)
    print("[校验] lite 卷合并回环 OK")

    if args.upload:
        upload(args.upload, lite_files)
        print("上传完成。")

    print("\nRESULT: RELEASE PACKAGE READY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
