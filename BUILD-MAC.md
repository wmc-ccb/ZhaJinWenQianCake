# macOS 构建与运行指南

Windows 是第一平台（全部功能在本机开发验证）；macOS 为移植平台，
构建走 **GitHub Actions macos-14 云端**（PyInstaller 不支持跨平台，
本机是 Windows 无法本地出 mac 包）。

## 三种获取方式

1. **CI 产物（推荐）**：Actions → build-mac → Run workflow，或推一个
   `v*` tag（自动挂到 Release）。产物：`ZhaJinWenQianCake-v*-macos.tar.gz`
2. **自己在 Mac 上构建**：见下节
3. **源码直跑**（开发）：

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-mac.txt
python tools/fetch_models.py     # 模型约 1GB
python server/app.py             # http://127.0.0.1:8765
```

## 在 Mac 上自建

```bash
xcode-select --install                      # 命令行工具
python3.12 -m venv .venv && source .venv/bin/activate
pip install pyinstaller && pip install -r requirements-mac.txt
python tools/fetch_models.py
python -m PyInstaller ZhaJinWenQianCake-mac.spec --noconfirm --clean
# 产物：dist/ZhaJinWenQianCake/  （tar 打包后分发）
```

spec 文件由 CI 生成（见 .github/workflows/build-mac.yml 内嵌模板），
如需本地手改可复制该模板为 ZhaJinWenQianCake-mac.spec。

## macOS 已知限制（如实说明）

| 项 | 现状 |
|---|---|
| **GPU 加速** | superres/降噪走 CPU 推理（MPS 后端未验证），速度低于 Windows GPU |
| **应用内一键更新** | 下载可用；「安装并重启」的自动解压步骤待 mac 实机验证，首版建议手动下载新包覆盖 |
| **联机拍摄** | Windows EDSDK 优先；mac 走 PTP/IP 通用协议，未实测 |
| **Gatekeeper** | 未签名/未公证——首次打开需右键 → 打开，或 `xattr -d com.apple.quarantine <app>`。正式分发建议 Apple Developer ID 签名（$99/年） |
| **中文路径** | 引擎对 UTF-8 路径无障碍（Windows 侧已修过 GBK 坑） |

## Windows 专属功能（mac 上不可用）

- 联机拍摄的 EDSDK 佳能原生链路（mac 走通用 PTP）
- 更新器 .bat（mac 用 apply_update.sh，见 server 端 darwin 分支）
