#!/bin/bash
# 炸金文谦蛋糕 · macOS 一键安装+启动
# 用法（Apple Silicon Mac，macOS 12+）：
#   bash mac-install.sh
# 做什么：
#   1. 检查/安装 uv（Python 环境管理器，单二进制，装到 ~/.local/bin）
#   2. 用 uv 建 .venv（Python 3.12）并装依赖（全 arm64 轮子）
#   3. 下载 AI 模型（约 1GB，放 models/，只此一次）
#   4. 启动应用（本机 http://127.0.0.1:8765，自动开浏览器）
# 再跑一次 = 跳过已装步骤直接启动（幂等）。
set -e
cd "$(dirname "$0")"

say() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

say "1/4 检查 uv（Python 环境管理器）"
if ! command -v uv >/dev/null 2>&1; then
  # 安装器落盘后执行，不用 curl|sh 直管道（远程脚本无审查直接执行
  # 是供应链风险——落盘让用户有机会先看内容）
  curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-installer.sh
  sh /tmp/uv-installer.sh
  rm -f /tmp/uv-installer.sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv --version

say "2/4 创建环境并安装依赖（首次约 3-5 分钟）"
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-mac.txt

say "3/4 下载 AI 模型（约 1GB，只此一次；已下载则跳过）"
.venv/bin/python tools/fetch_models.py

say "4/4 启动炸金文谦蛋糕"
printf '  本机地址: \033[1mhttp://127.0.0.1:8765\033[0m（浏览器自动打开）\n'
printf '  关闭: 终端 Ctrl+C\n\n'
open "http://127.0.0.1:8765" 2>/dev/null || true
exec .venv/bin/python server/app.py
