# -*- coding: utf-8 -*-
r"""资源路径解析：同时支持「源码运行」和「PyInstaller 打包运行」。

两种运行方式的目录结构完全不同：

    源码运行
      <root>/engine/paths.py      <- __file__
      <root>/models/*.onnx
      <root>/ui/index.html

    PyInstaller onedir
      <dist>/ZhaJinWenQianCake/ZhaJinWenQianCake.exe
      <dist>/ZhaJinWenQianCake/_internal/engine/...   (打包进去的代码)
      <dist>/ZhaJinWenQianCake/models/...              (运行时目录，可写)
      <dist>/ZhaJinWenQianCake/ui/...                  (运行时目录，可写)

所以**模型和界面资源一律放在 exe 旁边的可写目录**（不要打进 _internal），
理由：
  1. 模型近 1GB，放进 _internal 每次都要跟 exe 一起被校验/复制
  2. 生成式组件包是后装的，必须能往这个目录里加东西
  3. 用户想换模型/加模型时直接拷文件就行，不用重新打包

环境变量 RETOUCHLAB_HOME 可覆盖运行时根目录（便携 U 盘场景）。

第三种形态：MSIX / Microsoft Store 包
  <pkg>\ZhaJinWenQianCake.exe   <- 只读（C:\Program Files\WindowsApps\...）
  <pkg>\ui\ ...                 <- 只读资源
  %LOCALAPPDATA%\ZhaJinWenQianCake\   <- 可写区（data/ out/ models/）
  Windows 为 MSIX 进程注入 PACKAGE_* 环境变量，以此识别。商店发行
  只上 lite（模型首启下载到可写区），所以 models_dir() 在 MSIX 下
  指可写区即可，全部 loader 调用点零改动；find_model() 留作将来
  full 版上架时的双路径查找。
"""
from __future__ import annotations

import os
import sys


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def is_msix() -> bool:
    """运行在 MSIX/商店包里（Windows 会给包内进程注入这些变量）。"""
    return bool(os.environ.get("PACKAGE_FAMILY_NAME"))


def _exe_dir() -> str:
    """打包后：exe 所在目录。源码运行：项目根目录。"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    # 源码：本文件在 <root>/engine/paths.py
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_dir() -> str:
    """应用安装目录（打包版 = exe 所在目录；源码态 = 项目根）。

    与 home() 的区别：home 是"可写运行时根"（MSIX 下切到
    LOCALAPPDATA），app_dir 永远指向安装目录——一键更新器要用它
    定位被替换的可执行文件与解压目标。
    """
    return _exe_dir()


def app_executable() -> str:
    """主可执行文件完整路径（跨平台：Windows exe / macOS .app 内二进制）。"""
    if is_frozen():
        # 打包态：PyInstaller 的引导可执行文件本身（macOS 在
        # <pkg>.app/Contents/MacOS/ 下，Windows 是 ZhaJinWenQianCake.exe）
        return os.path.abspath(sys.executable)
    name = "ZhaJinWenQianCake.exe" if sys.platform == "win32"         else "ZhaJinWenQianCake"
    return os.path.join(_exe_dir(), name)


def home() -> str:
    """可写运行时根目录（放 models/ data/ out/ 的地方）。

    MSIX 下 exe 目录只读，可写区切到 %LOCALAPPDATA%（卸载会被系统
    清掉，符合商店惯例）；RETOUCHLAB_HOME 仍最高优先级，方便包内调试。
    """
    env = os.environ.get("RETOUCHLAB_HOME", "").strip()
    if env:
        return os.path.abspath(env)
    if is_msix():
        return os.path.join(os.environ.get("LOCALAPPDATA",
                                           os.path.expanduser("~")),
                            "ZhaJinWenQianCake")
    return _exe_dir()


def models_dir() -> str:
    return os.path.join(home(), "models")


def install_models_dir() -> str:
    """安装目录里内置的模型（full 包 MSIX 化时用，只读）。"""
    return os.path.join(_exe_dir(), "models")


def find_model(name: str) -> str:
    """模型查找：可写区优先（用户下载/替换优先），退回安装目录内置。

    命中哪个返回哪个；两边都没有时返回可写区路径——调用方按
    "文件不存在"走既有的不可用分支。
    """
    for d in (models_dir(), install_models_dir()):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return os.path.join(models_dir(), name)


def ui_dir() -> str:
    if is_msix():
        # 前端资源是随包只读资产，永远在安装目录（exe 旁）
        return os.path.join(_exe_dir(), "ui")
    return os.path.join(home(), "ui")


def data_dir() -> str:
    return os.path.join(home(), "data")


def out_dir() -> str:
    return os.path.join(home(), "out")


def ensure_dirs() -> None:
    """首次启动时把该有的目录建出来。"""
    for d in (models_dir(), data_dir(), out_dir()):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass


def register_cuda_dlls() -> bool:
    """把 torch 的 CUDA 运行库加进 DLL 搜索路径（**必须在 import torch 之前**）。

    打包版踩过的坑：PyInstaller 会把 torch/lib 里的 cublas/cudnn 等
    DLL 一起收进 _internal/torch/lib，但 Windows 的默认 DLL 搜索顺序里
    没有这个目录。结果是 `torch.cuda.is_available()` 返回 True，而
    `torch.cuda.init()` 在第一次真正分配显存时找不到 cublasLt64_12.dll，
    torch 内部把异常吞掉后静默回退 CPU——表现就是"GPU 明明可用，超清
    却慢了 12 倍"（实测打包版 109s vs 源码版 9.2s）。

    add_dll_directory 是 3.8+ 的官方做法；同时补 PATH，因为少数扩展
    模块仍按 PATH 找依赖。
    """
    if os.name != "nt":
        return False
    added = False
    candidates = []
    try:
        import torch  # noqa: F401  只为取路径
        candidates.append(os.path.join(os.path.dirname(torch.__file__), "lib"))
    except Exception:
        pass
    # 打包版：_internal/torch/lib
    if is_frozen():
        candidates.append(os.path.join(os.path.dirname(sys.executable),
                                       "_internal", "torch", "lib"))
    for d in candidates:
        if not os.path.isdir(d):
            continue
        try:
            os.add_dll_directory(d)
            added = True
        except (AttributeError, OSError):
            pass
        try:
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            added = True
        except Exception:
            pass
    return added


def torch_device() -> str:
    """全引擎统一的 PyTorch 设备选择：cuda -> mps(Apple Silicon) -> cpu.

    为什么集中在这里：此前 4 个模块各自写 `cuda if available else cpu`，
    Apple Silicon 的 MPS 加速没有入口（mac 用户全部落 CPU）。统一后：
      - NVIDIA: cuda（Windows 打包版已验证）
      - Apple Silicon: mps（PyTorch 原生后端；实验性——个别算子在 MPS
        上可能与 CUDA/CPU 有数值差异，调用方首次前向失败时应回落 cpu）
      - 其余/RETOUCHLAB_MPS=0 显式关闭: cpu
    本函数只做选择，不做任何 .to()——模型搬运与失败回落由调用方负责。
    """
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and os.environ.get("RETOUCHLAB_MPS", "1") != "0":
            try:
                if mps.is_available():
                    return "mps"
            except Exception:
                pass
    except Exception:
        pass
    return "cpu"
