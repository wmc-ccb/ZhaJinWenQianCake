# -*- coding: utf-8 -*-
"""MSIX 打包：为 Microsoft Store 提审做准备。

产出 dist/msix/ZhaJinWenQianCake-<ver>.msix，内容 = lite 形态
（exe + _internal + ui + models_manifest.json，不含模型）。

用法：
    python tools/make_msix.py                 # 只打包
    python tools/make_msix.py --sign --install # 自签 + 本机装机实测

提审前必须改的两个参数（拿到合作伙伴中心账户后）：
    --name      包标识名，默认占位 ZhaJinWenQianCake
    --publisher 证书主体，必须与合作伙伴中心分配的 Publisher ID 一致，
                默认占位 CN=ZJWQC-PLACEHOLDER

设计要点：
  - runFullTrust：桌面应用保留现在的启动方式（pywebview/WebView2）
  - WebView2 走 PackageDependency（系统组件，不打包）
  - 可写目录不用 unvirtualizedResources：paths.py 在 MSIX 下显式把
    可写区切到 %LOCALAPPDATA%，默认 AppData 虚拟化把它重定向到包的
    LocalCache（应用透明、卸载即清，符合商店惯例，还少一个受限能力）
  - 版本号转四段：1.1.0 -> 1.1.0.0（MSIX 强制 x.y.z.w）
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist", "ZhaJinWenQianCake")
OUT = os.path.join(ROOT, "dist", "msix")
STAGE = os.path.join(OUT, "staging")

MANIFEST_TPL = """<?xml version="1.0" encoding="utf-8"?>
<Package
  xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
  xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities">
  <Identity Name="{name}"
            Publisher="{publisher}"
            Version="{version}"
            ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>炸金文谦蛋糕</DisplayName>
    <PublisherDisplayName>{publisher_display}</PublisherDisplayName>
    <Logo>Assets\\StoreLogo.png</Logo>
    <Description>本地人像精修引擎：磨皮、祛瑕、液化、超清、批量修图，照片全部在本机处理。</Description>
  </Properties>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop"
                        MinVersion="10.0.19041.0"
                        MaxVersionTested="10.0.26200.0" />
    <PackageDependency Name="Microsoft.WebView2"
                       MinVersion="119.0.2151.0"
                       Publisher="CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US" />
  </Dependencies>
  <Resources>
    <Resource Language="zh-cn" />
    <Resource Language="en-us" />
  </Resources>
  <Applications>
    <Application Id="App"
                 Executable="ZhaJinWenQianCake.exe"
                 EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="炸金文谦蛋糕"
                          Description="本地人像精修引擎"
                          BackgroundColor="#16181d"
                          Square150x150Logo="Assets\\Square150x150.png"
                          Square44x44Logo="Assets\\Square44x44.png">
        <uap:DefaultTile Wide310x150Logo="Assets\\Wide310x150.png" />
      </uap:VisualElements>
    </Application>
  </Applications>
  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
  </Capabilities>
</Package>
"""


def read_version() -> str:
    with open(os.path.join(ROOT, "server", "app.py"), encoding="utf-8") as f:
        m = re.search(r'APP_VERSION = "([^"]+)"', f.read())
    return m.group(1)


def find_sdk_tool(name: str) -> str:
    """Windows SDK 里的 makeappx/signtool，找不到返回 None。"""
    hits = []
    for base in (r"C:\Program Files (x86)\Windows Kits\10\bin",
                 r"C:\Program Files\Windows Kits\10\bin"):
        if not os.path.isdir(base):
            continue
        for ver in sorted(os.listdir(base), reverse=True):
            p = os.path.join(base, ver, "x64", name)
            if os.path.isfile(p):
                hits.append(p)
    return hits[0] if hits else None


def make_assets():
    """从 assets/zhajinwenqian.ico 生成清单要求的 PNG 各尺寸。"""
    from PIL import Image
    ico = os.path.join(ROOT, "assets", "zhajinwenqian.ico")
    img = Image.open(ico)   # Pillow 默认取最大帧
    img = img.convert("RGBA")
    print("  ico 源尺寸:", img.size)
    assets = os.path.join(STAGE, "Assets")
    os.makedirs(assets, exist_ok=True)
    for name, size in (("StoreLogo.png", 50),
                       ("Square44x44.png", 44),
                       ("Square150x150.png", 150),
                       ("Wide310x150.png", 310)):
        c = img.copy()
        # 非方形画布：内容居中、透明边
        if size == 310:
            canvas = Image.new("RGBA", (310, 150), (0, 0, 0, 0))
            s = 140
            c.thumbnail((s, s), Image.LANCZOS)
            canvas.alpha_composite(
                c, ((310 - c.width) // 2, (150 - c.height) // 2))
        else:
            canvas = c.resize((size, size), Image.LANCZOS)
        canvas.save(os.path.join(assets, name))
    print("  Assets/ 4 个尺寸已生成")


def stage(name: str, publisher: str, publisher_display: str,
          version: str) -> None:
    """摆包根：exe + _internal + ui + manifest.json + 清单。"""
    if os.path.isdir(STAGE):
        shutil.rmtree(STAGE, ignore_errors=True)
    os.makedirs(STAGE, exist_ok=True)
    # exe + _internal
    shutil.copy2(os.path.join(DIST, "ZhaJinWenQianCake.exe"), STAGE)
    shutil.copytree(os.path.join(DIST, "_internal"),
                    os.path.join(STAGE, "_internal"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    # ui + lite 清单（模型不进包，首启引导下载）
    shutil.copytree(os.path.join(DIST, "ui"),
                    os.path.join(STAGE, "ui"))
    shutil.copy2(os.path.join(ROOT, "tools", "models_manifest.json"), STAGE)
    # 运行目录的 data/out 是用户数据，不进包；启动.bat 没必要（商店装完有磁贴）
    make_assets()
    with open(os.path.join(STAGE, "AppxManifest.xml"), "w",
              encoding="utf-8") as f:
        f.write(MANIFEST_TPL.format(
            name=name, publisher=publisher,
            publisher_display=publisher_display,
            version=".".join((version.split(".") + ["0", "0", "0"])[:4])))
    print("  staging 就绪: %s" % STAGE)


def pack(version: str) -> str:
    makeappx = find_sdk_tool("makeappx.exe")
    if not makeappx:
        raise SystemExit("找不到 makeappx.exe（需要 Windows SDK）——"
                         "本机有 signtool 却没有 makeappx 是不常见的，"
                         "请装 Windows SDK 的打包工具组")
    out_msix = os.path.join(OUT, "ZhaJinWenQianCake-%s.msix"
                            % ".".join((version.split(".") + ["0", "0"])[:2]))
    if os.path.exists(out_msix):
        os.remove(out_msix)
    r = subprocess.run([makeappx, "pack", "/d", STAGE, "/p", out_msix,
                        "/nv"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("makeappx 失败:\n%s" % (r.stderr or r.stdout)[-2000:])
    print("[msix] %s (%.2f GB)" % (out_msix,
                                   os.path.getsize(out_msix) / 1024 ** 3))
    return out_msix


def sign(msix: str, publisher: str) -> None:
    """自签证书（仅本机装机实测用；商店提审不需要我们签名）。"""
    signtool = find_sdk_tool("signtool.exe")
    if not signtool:
        raise SystemExit("找不到 signtool.exe（Windows SDK）")
    pfx = os.path.join(OUT, "_test_cert.pfx")
    ps = (
        "$c = New-SelfSignedCertificate -Type Custom "
        '-Subject "%s" -KeyUsage DigitalSignature -FriendlyName "ZJWQC dev" '
        "-CertStoreLocation Cert:\\CurrentUser\\My "
        '-NotAfter (Get-Date).AddYears(2) -TextExtension @('
        '"2.5.29.37={text}1.3.6.1.5.5.7.3.3",'
        '"1.3.6.1.4.1.311.21.10={text}oid=1.3.6.1.4.1.311.76.13.1"); '
        "$pwd = ConvertTo-SecureString -String 'zjwqc-test' -Force -AsPlainText; "
        'Export-PfxCertificate -Cert $c -FilePath "%s" -Password $pwd | Out-Null; '
        "Write-Output $c.Thumbprint"
        % (publisher, pfx))
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(pfx):
        raise SystemExit("自签证书生成失败:\n%s%s" % (r.stdout[-800:],
                                                    r.stderr[-800:]))
    r = subprocess.run([signtool, "sign", "/fd", "SHA256", "/f", pfx,
                        "/p", "zjwqc-test", msix],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("签名失败:\n%s" % (r.stderr or r.stdout)[-2000:])
    print("[sign] 已用自签证书签名（CN=%s）" % publisher)


def install(msix: str) -> None:
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Add-AppxPackage -Path '%s'" % msix],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("Add-AppxPackage 失败:\n%s%s" % (r.stdout[-1200:],
                                                         r.stderr[-1200:]))
    print("[install] MSIX 已装机。开始菜单找「炸金文谦蛋糕」实测；")
    print("           卸载: Get-AppxPackage *ZhaJinWenQianCake* "
          "| Remove-AppxPackage")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="ZhaJinWenQianCake",
                    help="包标识名（提审时用合作伙伴中心分配的）")
    ap.add_argument("--publisher", default="CN=ZJWQC-PLACEHOLDER",
                    help="Publisher ID（提审时必须与账户一致）")
    ap.add_argument("--publisher-display", default="炸金文谦蛋糕")
    ap.add_argument("--sign", action="store_true", help="自签（装机实测）")
    ap.add_argument("--install", action="store_true", help="装机")
    args = ap.parse_args()

    if not os.path.isdir(DIST):
        raise SystemExit("找不到 %s，先跑 make_release 构建" % DIST)
    os.makedirs(OUT, exist_ok=True)
    version = read_version()
    print("[msix] 版本 %s (lite 形态)" % version)
    stage(args.name, args.publisher, args.publisher_display, version)
    msix = pack(version)
    if args.sign:
        sign(msix, args.publisher)
    if args.install:
        install(msix)


if __name__ == "__main__":
    sys.exit(main())
