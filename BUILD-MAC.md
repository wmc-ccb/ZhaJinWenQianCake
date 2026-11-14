# macOS 运行指南

Windows 是第一平台；macOS（**仅 Apple Silicon** M1/M2/M3/M4）为移植平台。
Intel Mac 不支持——PyPI 生态已退役 macOS x86_64 轮子（实测 2026-11-11）。

## Mac 用户：三行命令跑起来（推荐）

下载 [mac-starter.zip](https://github.com/wmc-ccb/ZhaJinWenQianCake/releases/download/v1.2.0/mac-starter.zip)（含 mac-install.sh / requirements / 模型清单），解压后在解压目录执行：

```bash
bash mac-install.sh
```

脚本自动完成：安装 uv（Python 环境管理器）→ 建 3.12 环境 → 装依赖
（全 arm64 轮子）→ 下载 AI 模型（约 1GB，只此一次）→ 启动并自动打开
浏览器（http://127.0.0.1:8765）。**再跑一次同一命令 = 直接启动**（幂等）。

关闭：终端 Ctrl+C。再次使用：再次执行 `bash mac-install.sh`。

前提：macOS 12+ 与终端；约 3GB 磁盘空间（环境 2GB + 模型 1GB）。

## 已知限制（mac 版，如实说明）

| 项 | 现状 |
|---|---|
| **GPU 加速** | **Apple Silicon MPS 加速已接入**（torch_device 统一选择 cuda→mps→cpu；实验性——个别算子数值差异或首次前向失败时自动回落 CPU。可用 `RETOUCHLAB_MPS=0` 强制关闭） |
| **应用内一键更新** | 检测/下载可用；「安装并重启」自动解压待 mac 实机验证，建议手动重下模型外的更新包或 git pull |
| **联机拍摄** | Windows EDSDK 优先；mac 走通用协议，未实测 |
| **打包 .app** | GitHub Actions 免费档 mac runner 长时间排队（实测三次 24h 超时），.app 分发暂缓；付费 larger runner 或租云 Mac 可解 |
| **Gatekeeper** | 源码直跑不涉及；未来 .app 未签名需右键打开 |

## Windows 专属（mac 不可用）

- 联机拍摄 EDSDK 佳能原生链路
- 更新器 .bat（mac 用 apply_update.sh，darwin 分支已在 server 端）

## 开发者：mac 上自建 .app（可选）

PyInstaller 不支持跨平台，.app 必须在 mac 上构建。spec 模板内嵌于
`.github/workflows/build-mac.yml`；CI 在 macos runner 排到时会自动产出
并挂 Release（免费档排队可能很长，付费 larger runner 即开即用）。
