# -*- coding: utf-8 -*-
"""溶图（素材融合）：把用户素材贴进照片并做边缘/色彩融合.

影楼语境的「溶图」：把素材（头饰、翅膀、前景道具、另一张背景布）
贴到照片指定位置，边缘自动羽化、色调自动匹配，让它看起来像拍出来
的而不是贴上去的。三个融合档位：

  alpha    纯羽化合成——素材原样，只羽化边缘。最快、最忠实（素材
           本身调好色时用这个）。
  poisson  泊松融合（seamlessClone NORMAL_CLONE）——素材的色调/光照
           自动适配底图，"贴上去"变"长出来"。素材与底图色温差大时
           效果显著。
  mixed    MIXED_CLONE——保留素材强边缘（logo/文字/硬边道具），只融
           化弱边背景。文字水印类素材用它不糊字。

泊松融合对纯色素材/低对比可能报错或出 artifacts，任何失败自动回落
alpha 档，info["engine"] 记录实际用的那个——溶图宁可贴上，不能 500。

坐标口径：cx/cy 是素材中心在底图上的**归一化**位置（0~1），scale 是
素材长边相对底图长边的比例，angle 顺时针度数。素材自身的透明通道
优先于任何自动抠图——素材该抠的请在做素材时抠好，这里不做抠图
（溶图不是抠图工具，像素蛋糕也是这么划分的）。
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

import cv2
import numpy as np

MODES = ("alpha", "poisson", "mixed")


def info() -> dict:
    return {"modes": list(MODES)}


def _rotated_bounds(w: int, h: int, angle: float) -> Tuple[int, int, np.ndarray]:
    """素材旋转后的外接框尺寸与旋转矩阵（不裁切）。"""
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    return nw, nh, m


def prepare_asset(asset: np.ndarray, scale: float, angle: float,
                  feather: float, base_long: int,
                  flip: str = "none", opacity: float = 1.0
                  ) -> Tuple[np.ndarray, np.ndarray]:
    """素材 -> (rgb float32 0..1, alpha float32 0..1)，已翻转/缩放/旋转.

    alpha 来源：素材自带 alpha 通道；没有则全 1（矩形）。feather 对
    alpha 做高斯羽化（单位 = 底图长边像素）。flip: none/h/v。
    opacity：整体不透明度（乘进 alpha，0.6 = 六成实）。
    """
    a = np.asarray(asset, np.float32)
    if a.ndim != 3 or a.shape[2] not in (3, 4):
        raise ValueError("素材必须是 3/4 通道图")
    if a.shape[2] == 4:
        rgb, alpha = a[..., :3], a[..., 3] / 255.0
    else:
        rgb, alpha = a, np.ones(a.shape[:2], np.float32)
    rgb = np.clip(rgb, 0, 1)

    if flip in ("h", "hv", "vh"):
        rgb, alpha = rgb[:, ::-1], alpha[:, ::-1]
    if flip in ("v", "hv", "vh"):
        rgb, alpha = rgb[::-1], alpha[::-1]

    h, w = rgb.shape[:2]
    target = max(8, int(round(base_long * float(np.clip(scale, 0.02, 4.0)))))
    k = target / max(h, w)
    nw, nh = max(1, int(round(w * k))), max(1, int(round(h * k)))
    rgb = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)
    alpha = cv2.resize(alpha, (nw, nh), interpolation=cv2.INTER_AREA)

    if abs(angle) > 0.05:
        bw, bh, m = _rotated_bounds(nw, nh, angle)
        rgb = cv2.warpAffine(rgb, m, (bw, bh),
                             flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_CONSTANT,
                             borderValue=(0, 0, 0))
        alpha = cv2.warpAffine(alpha, m, (bw, bh),
                               flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_CONSTANT,
                               borderValue=0)

    if feather > 0.3:
        ksz = int(max(3, round(feather * 2)) | 1)
        alpha = cv2.GaussianBlur(alpha, (ksz, ksz),
                                 max(0.5, feather * 0.5))
    alpha = np.clip(alpha, 0, 1) * float(np.clip(opacity, 0.0, 1.0))
    return rgb, alpha


def blend(img: np.ndarray, asset: np.ndarray,
          cx: float = 0.5, cy: float = 0.5, scale: float = 0.3,
          angle: float = 0.0, feather: float = 12.0,
          mode: str = "poisson", flip: str = "none",
          opacity: float = 1.0) -> Tuple[np.ndarray, dict]:
    """把素材融进底图，返回 (结果图, info)."""
    if mode not in MODES:
        raise ValueError("mode 必须是 %s 之一" % (MODES,))
    base = np.asarray(img, np.float32)
    if base.ndim != 3:
        raise ValueError("底图必须是 3 通道")
    bh, bw = base.shape[:2]
    rgb, alpha = prepare_asset(asset, scale, angle, feather, max(bh, bw),
                               flip=flip, opacity=opacity)

    ah, aw = rgb.shape[:2]
    # 中心点落在画布外太多就没意义，夹回 0..1
    px = int(round(float(np.clip(cx, 0.0, 1.0)) * bw - aw / 2))
    py = int(round(float(np.clip(cy, 0.0, 1.0)) * bh - ah / 2))

    info = {"mode": mode, "placed": [px, py, aw, ah], "engine": mode}

    # 素材与底图的交集（素材可以超出画布，超出的部分丢弃）
    x0, y0 = max(0, px), max(0, py)
    x1, y1 = min(bw, px + aw), min(bh, py + ah)
    if x1 - x0 < 4 or y1 - y0 < 4:
        info["engine"] = "out-of-canvas"
        info["note"] = "素材完全在画布外，未做任何修改"
        return base, info

    sub = base[y0:y1, x0:x1]
    srgb = rgb[y0 - py:y1 - py, x0 - px:x1 - px]
    salpha = alpha[y0 - py:y1 - py, x0 - px:x1 - px]

    used = "alpha"
    out = sub.copy()
    if mode in ("poisson", "mixed"):
        try:
            # seamlessClone 要求掩膜 uint8 且素材区域 > 3px 边界
            mask = (salpha > 0.35).astype(np.uint8) * 255
            src_u8 = (np.clip(srgb, 0, 1) * 255).astype(np.uint8)
            dst_u8 = (np.clip(sub, 0, 1) * 255).astype(np.uint8)
            center = ((x1 - x0) // 2, (y1 - y0) // 2)
            if mask.sum() < 3 * 255:
                raise ValueError("素材有效区域过小")
            clone_flag = cv2.MIXED_CLONE if mode == "mixed" else cv2.NORMAL_CLONE
            cloned = cv2.seamlessClone(src_u8, dst_u8, mask, center,
                                       clone_flag)
            out = cloned.astype(np.float32) / 255.0
            # 泊松克隆整块替换矩形——素材半透明边缘（羽化出来的 0<alpha<1）
            # 会被克隆成硬边。把这些像素用羽化合成的结果叠回去。
            soft = ((salpha < 0.98) & (salpha > 0.02)).astype(np.float32)
            if soft.any():
                feathered = sub * (1 - salpha[..., None]) + srgb * salpha[..., None]
                e = soft[..., None]
                out = out * (1 - e) + feathered * e
            used = mode
        except Exception as exc:                     # 泊松失败回落羽化合成
            info["poisson_error"] = str(exc)[:120]
            used = "alpha(fallback)"
            out = sub.copy()

    if used.startswith("alpha"):
        a3 = salpha[..., None]
        out = sub * (1 - a3) + srgb * a3

    base = base.copy()
    base[y0:y1, x0:x1] = out
    info["engine"] = used
    return np.clip(base, 0, 1), info


def blend_layers(img: np.ndarray,
                 layers: List[dict]) -> Tuple[np.ndarray, dict]:
    """素材栈：按列表顺序逐层融合（后面的层盖在前面之上）.

    `layers` 每项是 blend() 的参数 dict（cx/cy/scale/angle/feather/
    mode/flip/opacity），asset 项直接传 ndarray。任一层非法（缺 asset、
    mode 错）抛 ValueError 由端点转 4xx；单层泊松失败沿用 blend 内部
    的回落逻辑，不炸整栈。info["layers"] 逐层记录实际引擎。
    """
    base = np.asarray(img, np.float32)
    infos = []
    for i, ly in enumerate(layers):
        asset = ly.get("asset")
        if asset is None:
            raise ValueError("第 %d 层缺少素材" % (i + 1))
        base, bi = blend(base, asset,
                         cx=ly.get("cx", 0.5), cy=ly.get("cy", 0.5),
                         scale=ly.get("scale", 0.3),
                         angle=ly.get("angle", 0.0),
                         feather=ly.get("feather", 12.0),
                         mode=ly.get("mode", "poisson"),
                         flip=ly.get("flip", "none"),
                         opacity=ly.get("opacity", 1.0))
        infos.append(bi)
    return base, {"layers": infos, "count": len(infos)}
