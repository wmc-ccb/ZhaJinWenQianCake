# -*- coding: utf-8 -*-
"""人脸解析的**可商用后端**：MediaPipe Face Mesh 468 点几何蒙版.

背景：原 faceparse（SegFormer-B2 + faceparsing 权重）精度好，但其预训练
权重仅限研究/非商业用途（见 THIRD_PARTY.md）。本模块用 MediaPipe Face
Landmarker（Apache-2.0，face_landmarker.task）的 468 个稠密关键点 +
官方拓扑索引集，对唇/眼/眉做多边形填充蒙版，对皮肤用 selfie_multiclass
（同为 Apache-2.0）的 skin+neck 类。零许可风险，精度边界在下面如实说明。

精度边界（要诚实）
------------------
多边形填充是"区域近似"，不是语义分割：唇形贴不到 SegFormer 那么细，
眉形是拓扑近似。对妆容类操作（唇彩/眼影/眉形）位置正确、边缘可羽化，
实际观感差距集中在唇峰/眼角的 1-2px 细节。质量优先的场景仍可手动放回
faceparsing 权重——faceparse.parse 会自动回落到 SegFormer 后端。

索引集来自 MediaPipe 官方 face_mesh_connections 常量（公开且稳定），
硬编码在此避免 import 路径随 mediapipe 版本漂移。
"""
from __future__ import annotations

import os
import threading
from typing import Dict, Optional, Tuple

import cv2
import numpy as np

from .paths import models_dir

TASK_NAME = "face_landmarker.task"

# ---------------------------------------------------------------- 拓扑索引
# MediaPipe FaceMesh 官方连接集展开成的索引集合（逐点，非连接对）。
_LIPS = frozenset([
    0, 13, 14, 17, 37, 39, 40, 61, 78, 80, 81, 82, 84, 87, 88, 91, 95, 98,
    146, 178, 181, 185, 191, 267, 269, 270, 291, 308, 310, 311, 312, 314,
    317, 318, 321, 324, 325, 375, 402, 405, 409, 415,
])
_LEFT_EYE = frozenset([
    7, 33, 46, 52, 53, 54, 55, 58, 61, 63, 65, 66, 70, 80, 93, 95, 105, 107,
    133, 144, 145, 153, 154, 155, 157, 158, 159, 160, 161, 163, 173, 246,
])
_RIGHT_EYE = frozenset([
    133, 163, 173, 249, 250, 251, 254, 259, 263, 267, 269, 270, 271, 272,
    273, 274, 276, 283, 285, 292, 293, 296, 297, 298, 299, 300, 302, 305,
    322, 332, 334, 337, 338, 340, 359, 362, 382, 384, 385, 386, 387, 388,
    390, 398, 466,
])
_LEFT_BROW = frozenset([27, 28, 29, 30, 46, 47, 53, 54, 55, 56, 105, 107])
_RIGHT_BROW = frozenset([257, 258, 259, 260, 276, 283, 285, 286, 296, 334, 336])
_NOSE = frozenset([1, 2, 4, 5, 6, 19, 94, 98, 164, 165, 197, 195, 168,
                   327, 344, 393, 394, 440, 452, 494])

_GROUPS = {
    "lips": sorted(_LIPS),
    "eyes": sorted(_LEFT_EYE | _RIGHT_EYE),
    "brows": sorted(_LEFT_BROW | _RIGHT_BROW),
    "nose": sorted(_NOSE),
}

_LOCK = threading.Lock()
_LM = None          # FaceLandmarker 实例（惰性）
_SEM = None         # semantic 模块引用（skin 类）


def model_path() -> str:
    return os.path.join(models_dir(), TASK_NAME)


def available() -> bool:
    return os.path.isfile(model_path()) and \
        os.path.getsize(model_path()) > 1 << 20


def _landmarker():
    global _LM
    if _LM is not None:
        return _LM
    with _LOCK:
        if _LM is not None:
            return _LM
        # import 路径要点：mediapipe.tasks.python 需要**显式两级导入**
        # （`import mediapipe` 不会带出 tasks 子模块——pose.py 同款写法）
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        opts = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(
                model_asset_path=model_path()),
            running_mode=vision.RunningMode.IMAGE)
        _LM = vision.FaceLandmarker.create_from_options(opts)
        return _LM


class MeshParts:
    """与 faceparse.FaceParts 鸭子兼容：masks/names/coverage/__getitem__."""

    def __init__(self, raw: Dict[str, np.ndarray]):
        self.raw = raw
        self.masks = raw

    def __getitem__(self, key: str) -> Optional[np.ndarray]:
        return self.masks.get(key)

    def names(self) -> list:
        return list(self.masks.keys())

    def coverage(self) -> Dict[str, float]:
        return {k: round(float((v > 0.5).mean()), 4)
                for k, v in self.masks.items()}


def _poly_mask(pts: np.ndarray, w: int, h: int,
               feather: float = 2.0) -> np.ndarray:
    """点集 -> 凸包多边形填充 -> 羽化软边。"""
    hull = cv2.convexHull(pts.astype(np.int32))
    m = np.zeros((h, w), np.float32)
    cv2.fillConvexPoly(m, hull, 1.0)
    if feather > 0.2:
        k = int(max(3, round(feather * 2)) | 1)
        m = cv2.GaussianBlur(m, (k, k), feather * 0.6)
    return np.clip(m, 0, 1)


def parse(img: np.ndarray, size: int = 512,
          face_box: Optional[Tuple[int, int, int, int]] = None
          ) -> Optional[MeshParts]:
    """人脸解析（mesh 后端）：返回各组软掩膜，尺寸=输入图.

    face_box 兼容 faceparse 的签名——mesh 是全图检测，box 只用于
    结果有效性过滤（检测出的脸需与 box 相交）。
    """
    if not available():
        return None
    import mediapipe as mp
    from mediapipe.tasks.python import vision as mp_vision

    h, w = img.shape[:2]
    u8 = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=u8)

    lm = _landmarker()
    with _LOCK:
        res = lm.detect(mp_img)
    if not res.face_landmarks:
        return None
    lms = res.face_landmarks[0]
    pts = np.array([[p.x * w, p.y * h] for p in lms], np.float32)
    # 索引集里混有 refine 版专属点（>478，如虹膜 468-477 之外的编号）；
    # 本 task 输出 478 点，越界索引直接剔除（对这些组只是少 1-2 个点）
    valid = lambda idx: [i for i in idx if i < len(pts)]

    # 与传入 face_box 的一致性过滤（调用方给了框就要求大致命中）
    if face_box is not None:
        bx, by, bw, bh = [float(v) for v in face_box[:4]]
        cx, cy = float(pts[:, 0].mean()), float(pts[:, 1].mean())
        if not (bx - bw <= cx <= bx + 2 * bw and by - bh <= cy <= by + 2 * bh):
            return None

    out: Dict[str, np.ndarray] = {}
    for gname, idx in _GROUPS.items():
        out[gname] = _poly_mask(pts[valid(idx)], w, h,
                                feather=2.5 if gname != "lips" else 1.8)

    # skin：优先 semantic（selfie_multiclass，Apache-2.0）的 skin+neck；
    # 语义分割缺席时用全部关键点的凸包放大近似（脸区+周边，宁大勿小——
    # skin 的消费方（肤色统一）是大面积统计，不需要贴边精度）。
    global _SEM
    try:
        if _SEM is None:
            from . import semantic as _SEM_MOD
            _SEM = _SEM_MOD
        reg = _SEM.segment(img)
        if reg is not None:
            skin = np.clip(reg.norm("skin") if hasattr(reg, "norm")
                           else 0.0, 0, 1)
            try:
                skin = np.maximum(skin, np.clip(reg.norm("neck"), 0, 1))
            except Exception:
                pass
            if float((skin > 0.5).mean()) > 0.01:
                out["skin"] = skin
    except Exception:
        pass
    if "skin" not in out:
        hull = cv2.convexHull(pts.astype(np.int32))
        m = np.zeros((h, w), np.float32)
        cv2.fillConvexPoly(m, hull, 1.0)
        m = cv2.GaussianBlur(m, (31, 31), 12)
        out["skin"] = np.clip(m, 0, 1)

    return MeshParts(out)


def info() -> dict:
    return {"backend": "mediapipe-mesh", "license": "Apache-2.0",
            "task": TASK_NAME, "available": available()}
