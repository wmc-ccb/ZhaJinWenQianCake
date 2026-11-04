# 第三方组件与模型许可清单（THIRD PARTY NOTICES）

本项目**代码**为原创；运行时使用的公开模型与第三方库按下列许可使用并致谢。
本清单随依赖变动更新，如有遗漏请提 Issue。

## 一、随发行包分发的模型

| 模型 | 用途 | 来源 | 许可 |
|---|---|---|---|
| face_detection_yunet_2023mar.onnx | 人脸检测 | opencv_zoo | Apache-2.0 |
| lama_fp32.onnx | 消除/祛路人 | big-lama 转换 | Apache-2.0 |
| modnet.onnx | 人像抠图 | ZHKKKe/MODNet | Apache-2.0 |
| selfie_multiclass_256x256.tflite | 语义分割 | google/mediapipe | Apache-2.0 |
| pose_landmarker_lite.task | 姿态/脚部区域 | google/mediapipe | Apache-2.0 |
| hand_landmarker.task | 手部检测 | google/mediapipe | Apache-2.0 |
| RealESRGAN_x4plus.pth | 超分辨率 | xinntao/Real-ESRGAN | BSD-3-Clause |
| clip_image_encoder.onnx | LaMa 配套编码器 | 随 big-lama 发行 | Apache-2.0 |
| 2d106det.onnx | 106 人脸关键点 | insightface 模型库 | **研究/非商业**（见注 1） |
| faceparsing/model.safetensors | 面部解析 | insightface 模型库 | **研究/非商业**（见注 1） |
| denoise.pt | AI 降噪 | **本项目自训**（训练数据 DIV2K，见注 2） | 随项目许可 |

> **注 1**：insightface（deepinsight/insightface）代码为 MIT，但其**官方预训练模型**
> 注明仅供研究/学术等非商业用途。本项目的分发遵循该限制；任何商业使用
> 需自行向权利方获得授权。这是当前最重要的许可边界，替换为可商用模型
> 在路线图中。
>
> **注 2**：denoise.pt 为本项目使用 DIV2K 公开数据集自行训练的权重。
> DIV2K 数据集来自 Flickr 图像，遵循其官方发布条款用于研究用途。

## 二、生成式组件（用户按需安装，不随包分发）

| 组件 | 许可 | 说明 |
|---|---|---|
| ComfyUI | GPL-3.0 | 独立进程，用户自装 |
| FLUX.1 Fill [dev] | **FLUX.1 [dev] Non-Commercial License** | 由 ComfyUI 侧下载；商用需向 Black Forest Labs 购买授权 |
| SDXL / 其他社区模型 | 各自许可 | 由 ComfyUI 生态提供 |

## 三、主要 Python 依赖（pip 安装）

| 库 | 许可 |
|---|---|
| torch / torchvision | BSD-3-Clause |
| fastapi / uvicorn | MIT / BSD-3-Clause |
| onnxruntime | MIT |
| opencv-python(-headless) | Apache-2.0 |
| mediapipe | Apache-2.0 |
| numpy | BSD-3-Clause |
| pillow | HPND（MIT-CMU 类） |
| rawpy / LibRaw | LGPL-2.1（动态链接） |
| pywebview | BSD-3-Clause |
| huggingface_hub | Apache-2.0 |

完整依赖及版本见 `requirements.txt` 与 `requirements-generative.txt`。

## 四、致谢

上述开源项目与本项目的可用性直接相关，特别是 OpenCV Zoo、MediaPipe、
insightface、Real-ESRGAN、LaMa 与 MODNet 的作者们。
