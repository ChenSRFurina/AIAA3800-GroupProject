# 本地模型目录

将 py-feat 权重放在此目录，后端会**优先使用本地文件**，避免从 Hugging Face 下载。

## 当前支持

| 本地文件 | 对应 Hub 仓库 | 说明 |
|----------|---------------|------|
| `model.safetensors` | `py-feat/retinaface_r34` | RetinaFace 人脸检测（约 84 MB） |
| `face_multitask_v2.safetensors` | `py-feat/face_multitask_v2` | 情绪 / 疲劳 multitask 模型（可选，未放则走 HF 镜像） |

## 环境变量（可选）

- `FEAT_LOCAL_MODEL_DIR`：覆盖本目录路径
- `FEAT_RETINAFACE_WEIGHTS`：直接指定 RetinaFace 权重文件
- `FEAT_MULTITASK_WEIGHTS`：直接指定 multitask 权重文件
