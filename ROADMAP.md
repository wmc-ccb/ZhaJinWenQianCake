# 路线图（Roadmap）

优先级从上到下，随社区反馈动态调整。打勾 = 已完成（见
[CHANGELOG](CHANGELOG.md)）。

## 近期（1~2 个月）

- [x] **模型许可边界清理**（2026-11-07）：insightface 系模型已从发行包
  移除；关键点/面部解析改用 MediaPipe Face Landmarker（Apache-2.0，
  engine/meshface.py 几何蒙版）。仍有权重的本地用户自动优先 SegFormer
  后端（精度优先，自用场景）。

## 中期（3~6 个月）

- [ ] **FLUX fp16 官方版**接入（需 HF 账号接受 BFL 许可；当前 fp8
  量化版已可用）
- [ ] **AI 降噪进阶**：用真实高 ISO 暗场数据训练降噪模型
  （现版本为合成噪声训练）
- [ ] **自动更新**：应用内更新提示闭环（受 GitHub 2GB 单文件限制，
  需评估自托管或增量更新）
- [ ] **Microsoft Store 发行**：MSIX 打包链路已就绪（tools/make_msix.py），
  待开发者账户与签名证书
- [ ] **性能**：低端无独显机器的推理优化（CPU 路径 / 更小的模型档位）

## 远期 / 探索

- [ ] **macOS 版**：rawpy/onnxruntime/mediapipe 均可移植，主要工作在
  打包与窗口层（pywebview 跨平台）
- [ ] **CPU-only 精简版**：需换 CPU-only torch 轮子重新构建（当前 CUDA
  轮子 import 时硬依赖 cudnn/cublas DLL，拆不掉，见 HANDOVER）
- [ ] **联机拍摄增强**：更多机型协议（当前佳能/索尼 EDSDK 优先）
- [ ] **插件化引擎**：把引擎模块的注册/发现做成开放接口，方便社区
  贡献第三方滤镜

## 欢迎参与

以上任何一项都欢迎提交 PR——先读
[CONTRIBUTING.md](CONTRIBUTING.md)。想法类讨论请到
[Discussions](https://github.com/wmc-ccb/ZhaJinWenQianCake/discussions)。
