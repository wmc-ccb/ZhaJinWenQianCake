# 贡献指南（Contributing）

感谢关注「炸金文谦蛋糕」！欢迎 Issue 与 PR。

## 提交 Issue

- **Bug**：用 `.github/ISSUE_TEMPLATE` 的 bug 模板。请附「关于面板 →
  复制诊断报告」的内容（版本、GPU、最近错误），以及 `out\error.log`
  的相关堆栈
- **功能建议**：说明使用场景（哪类照片、哪一步操作），比只说功能名更有用
- 安全漏洞不要走公开 Issue：见 [SECURITY.md](SECURITY.md)

## 开发环境

```bash
# Python 3.11-3.13
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
# 下载模型（约 1GB，GitHub Releases）
.venv\Scripts\python tools\fetch_models.py
# 启动（源码形态）
.venv\Scripts\python server\app.py
```

## 跑测试

仓库的回归体系是**无头 API 断言**（不依赖浏览器）：

```bash
# 核心回归（需要服务在 8765 运行）
python tests/verify_hands_beach.py       # 手足/沙滩管线 19 断言
python tests/verify_release_features.py  # 发布特性回归
python tests/smoke.py                    # 冒烟
```

CI（`.github/workflows/ci.yml`）在每次 push 时跑 11 套无头引擎回归，
**提交前请确保本地核心套件通过**。

## PR 约定

- 一个 PR 聚焦一件事；界面改动请附截图
- 新增引擎函数需带对应回归断言（tests/ 下加或改套件）
- 保持既有代码风格：模块级注释解释「为什么」（本项目注释密度较高，
  记录设计取舍与踩过的坑——请延续）
- 遵守 [THIRD_PARTY.md](THIRD_PARTY.md) 的许可边界：不引入许可不清的
  模型或素材
