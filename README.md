# AIAA3800 — VPet 多模态桌宠

基于 [VPet](https://github.com/LorisYounger/VPet) 的二次开发：本地 TTS、视线跟随、人脸情绪/疲劳、语音助手。

仓库：[ChenSRFurina/AIAA3800-GroupProject](https://github.com/ChenSRFurina/AIAA3800-GroupProject.git)

目标平台：**Windows 10/11 x64**

---

## 功能与端口

| 模块 | 目录 | 端口 | Conda / 环境 |
|------|------|------|----------------|
| Speaking（F5-TTS） | `VPet-Speaking` | TCP `8765` | conda `F5TTS` |
| Gaze（视线） | `VPet-Gaze` | HTTP `8766` | conda `GAZE` |
| FaceDetect（本地） | `face-detect-local` + `VPet-FaceDetect` | HTTP/WS `8000` | conda `FACE` |
| FaceDetect（远程） | `face-detect-remote` relay + 远端 GPU 服务器 | 本地 `:8000` → 远程 | conda `FACE`（仅 relay） |
| Audio（语音助手） | `audio` + `VPet-Audio` | HTTP `8010` | conda `AUDIO` |

C# 插件编译后自动部署到 `VPet-Simulator.Windows/mod/12xx~15xx_*/plugin/`。  
**仓库不提交** `.env`、`bin/obj`、插件 `*.dll`、模型权重；克隆后需本地配置并编译。

---

## 1. 克隆

```powershell
git clone https://github.com/ChenSRFurina/AIAA3800.git
cd AIAA3800
git submodule update --init --recursive VPet-Speaking/Local_model/F5-TTS
```

---

## 2. 配置环境变量（`.env`）

1. 复制模板：

```powershell
copy .env.example .env
```

2. 编辑根目录 `.env`（**不要提交到 Git**）：

```env
## audio（必填才能启动语音助手 Agent）
DEEPSEEK_API_KEY=sk-xxxxxxxx
DEBUG=true

## 讯飞（可选；本地 F5-TTS 可用时可留空）
XUNFEI_APPID=
XUNFEI_APISecret=
XUNFEI_APIKey=
```

DeepSeek Key：[platform.deepseek.com/api_keys](https://platform.deepseek.com/api_keys)

`audio` 后端会从 **仓库根目录** `VPet/.env`（即本仓库根 `.env`）读取 `DEEPSEEK_API_KEY`。

---

## 3. 安装工具与 Python 环境

### 3.1 必装

- [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0)
- [Git](https://git-scm.com/)
- [Miniconda / Anaconda](https://docs.conda.io/)
- Python 依赖按模块用 conda + pip 安装（见下）

```powershell
dotnet --version   # >= 8
conda --version
```

### 3.2 Speaking & Gaze

### 3.2.1 Speaking → conda `F5TTS`

```powershell
# F5-TTS（需完整 Local_model/F5-TTS，含 src；见各模块 README）
conda create -n F5TTS python=3.11 -y
conda activate F5TTS
conda install ffmpeg -y
pip install --upgrade torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130
cd VPet-Speaking\Local_model\F5-TTS
pip install -e .
```

### 3.2.2 Gaze → conda `GAZE`

```
# Gaze：必须 mediapipe==0.10.21（新版本无 mp.solutions）
cd ..\..\..\VPet-Gaze
conda create -n GAZE python=3.11 -y
conda activate GAZE
pip install -r requirements.txt
```

### 3.3 FaceDetect → conda `FACE`

```powershell
cd face-detect-local
.\setup.bat
# 将 RetinaFace 权重放到 face-detect-local\model\model.safetensors（见 model\README.md）
# 或：conda create -n FACE python=3.12 -y
#     conda activate FACE
#     pip install --upgrade torch torchvision --index-url https://download.pytorch.org/whl/cu130
#     pip install -r requirements.txt
```

RetinaFace 默认读本地 `face-detect-local/model/model.safetensors`；multitask 权重未放本地时走镜像 `HF_ENDPOINT=https://hf-mirror.com`（`face-detect-local/run_backend.bat` / `server.py`）。

### 3.4 Audio → conda `AUDIO`

```powershell
cd audio
conda create -n AUDIO python=3.11 -y
conda activate AUDIO
.\setup.bat
# 使用当前 conda 环境的 python/pip 安装依赖；请确保根目录 .env 已填 DEEPSEEK_API_KEY
# Windows 下 torch/qwen3 依赖会通过 cu130 源安装 torch；可用 VPET_TORCH_INDEX 覆盖
# 默认 STT 后端为 qwen3；可用 VPET_WHISPER_BACKEND=torch|faster|qwen3 覆盖
# Qwen3 模型可用 VPET_QWEN3_ASR_MODEL 覆盖（例如 Qwen/Qwen3-ASR-1.7B）
# 可选: VPET_QWEN3_FORCED_ALIGNER=Qwen/Qwen3-ForcedAligner-0.6B
```

---

## 4. 编译（本地必做）

在仓库根目录：

```powershell
dotnet build .\VPet.sln -c Debug -p:Platform=x64
```

成功后生成：

- 主程序：`VPet-Simulator.Windows\bin\x64\Debug\net8.0-windows\VPet-Simulator.Windows.exe`
- 插件 DLL：`VPet-Simulator.Windows\mod\1200_VPet-Speaking\plugin\` 等

### 首次运行：链接 mod（管理员）

```powershell
cd VPet-Simulator.Windows
.\mklink.bat
```

否则 Debug 输出目录找不到 Core 模组。
重复执行 `mklink.bat` 时，若看到“已存在”或 `[SKIP]` 属于正常现象；建议先完成 `dotnet build` 再运行该脚本。

---

## 5. 启动

### 方式 A：一键（推荐）

```powershell
.\start-all.bat
```

会分别打开窗口启动四个后端 + 桌宠前端。  
停止：`.\stop-all.bat`

常用参数：

```powershell
.\start-all.bat -GazeMock          # 无摄像头测 Gaze
.\start-all.bat -Device cpu        # Speaking 用 CPU
.\start-all.bat -TtsBackend f5     # 切回 F5-TTS
.\start-all.bat -SkipAudio         # 跳过某后端
.\start-all.bat -NoFrontend        # 只启后端
```

环境约定（`start-all.ps1`）：

- Speaking（默认 `qwentts` 后端）→ conda `F5TTS`；Gaze → conda `GAZE`
- FaceDetect（默认）→ `face-detect-local` + conda `FACE`
- FaceDetect（`-Remote`）→ `face-detect-remote` relay，不跑本地推理
- Audio → conda `AUDIO`

**远程人脸检测**（本机无 GPU / 模型在服务器上）：

1. 在 GPU 服务器上部署并启动 `face-detect-remote\run_backend.bat`（原版推理服务）
2. 本机 `.env` 填写：`FACE_REMOTE_URL=http://服务器IP:8000`
3. 本机启动：`.\start-all.bat -Remote`

relay 仍监听本机 `127.0.0.1:8000`，VPet 与测试页无需改地址。

### 方式 B：手动分窗

```powershell
# Speaking :8765
conda activate F5TTS
cd VPet-Speaking\Local_model\Fast_generating
python start_server.py --tts_backend qwentts --device cuda

# Gaze :8766
conda activate F5TTS
cd VPet-Gaze\python
python gaze_server.py
# 或 mock: python gaze_server_mock.py

# FaceDetect :8000（本地）
conda activate FACE
cd face-detect-local
.\run_backend.bat

# FaceDetect :8000（远程 relay，推理在 GPU 服务器）
# .env: FACE_REMOTE_URL=http://192.168.1.10:8000
conda activate FACE
cd face-detect-remote
.\run_relay.bat

# Audio :8010
conda activate AUDIO
cd audio\backend
python main.py

# 前端
.\VPet-Simulator.Windows\bin\x64\Debug\net8.0-windows\VPet-Simulator.Windows.exe
```

### 游戏内

1. **设置 → MOD**：启用 `VPet-Speaking` / `VPet-Gaze` / `VPet-FaceDetect` / `VPet-Audio`
2. 重启桌宠
3. 右键 → **自定 / DIY** 使用各功能

**人脸情绪陪伴**：`start-all` 启动 Gaze + FaceDetect；FaceDetect **默认从 Gaze 共享摄像头帧**（`/camera/jpeg`），不必关视线、不必再抢摄像头。  
会打开测试页便于看结果；VPet DIY「启动情绪陪伴」轮询 `/latest`。关闭共享：`FACE_USE_GAZE_CAMERA=0`。

Debug 可加载未签名插件；Release 可能需开启「通过模组 / PassMOD」。

---

## 6. 验证清单

| 检查 | 地址 / 命令 |
|------|-------------|
| FaceDetect | `http://127.0.0.1:8000/health` |
| Face 测试页 | `http://127.0.0.1:8000/test-frontend/` |
| Gaze | `http://127.0.0.1:8766/gaze` |
| Audio | `http://127.0.0.1:8010/health` |
| Speaking | `start_server.py` 窗口无报错、监听 `8765` |

---

## 7. 常见问题

| 现象 | 处理 |
|------|------|
| 缺少 Core 模组 | 管理员运行 `mklink.bat` |
| DIY 无按钮 | MOD 中启用并重启；确认已 `dotnet build` |
| Gaze `mp.solutions` 报错 | `pip install mediapipe==0.10.21` |
| FaceDetect 下载模型 10054 | 本地模式：RetinaFace 放 `face-detect-local/model/`；远程模式：GPU 服务器跑 `face-detect-remote/run_backend.bat` |
| FaceDetect 远程连不上 | 检查 `FACE_REMOTE_URL`、防火墙、GPU 服务器 `http://IP:8000/health` |
| Audio 缺 `DEEPSEEK_API_KEY` | 填写根目录 `.env` |
| 端口占用 | Speaking `8765` / Gaze `8766` / Face `8000` / Audio `8010` 勿冲突 |

若仍提示 `Missing module Core, can't start up`：

```powershell
cd VPet-Simulator.Windows\bin\x64\Debug\net8.0-windows
ren mod mod.localbak
mklink /J mod ..\..\..\..\mod
```

然后重新运行 `start-all.bat`。

---

## 8. 目录速览

```text
AIAA3800/
├─ README.md                 ← 本文
├─ .env.example              ← 环境变量模板（复制为 .env）
├─ .gitignore
├─ VPet.sln
├─ start-all.bat / start-all.ps1 / stop-all.bat
├─ VPet-Simulator.Windows/   ← 主程序
├─ VPet-Speaking/            ← F5-TTS 插件 + 本地模型脚本
├─ VPet-Gaze/                ← 视线插件 + Python
├─ VPet-FaceDetect/          ← 人脸检测 C# 桥接
├─ VPet-Audio/               ← 语音助手 C# 桥接
├─ face-detect-local/        ← 本地推理（model/ 权重）
├─ face-detect-remote/       ← 远端 GPU 服务器 + 本机 relay
└─ audio/                    ← 语音助手 Python 后端
```

更细的模块说明见各子目录 `README.md`。上游桌宠文档见官方 [LorisYounger/VPet](https://github.com/LorisYounger/VPet)。
