# VPet 多模态改造修改历史

按时间排序，记录本仓库（GroupProject）从多模态初版到当前可用联调的主要改动，便于查阅。

---

## 2026-07-12

### VPet-Speaking 初版
- 新增 `VPet-Speaking` 插件与讯飞 TTS 接入（`61f6f4b9`）。
- DIY「说话」走云端/本地语音合成路径的基础骨架。

---

## 2026-07-13

### 本地模型部署
- 完成本地 F5-TTS 等模型部署相关提交（`ad6e67d9`）。

---

## 2026-07-18

### 多模态插件与后端骨架（初版可运行）
- 合入 Speaking / Gaze / FaceDetect / Audio 多模态插件、后端与搭建文档（`8b1155f6`）。
- 端口约定：Speaking `8765` · Gaze `8766` · FaceDetect `8000` · Audio `8010`。
- `start-all` / `stop-all` 一键启停后端与桌宠前端。

### Speaking 联调
- DIY 固定调试句 TTS；轮询 audio `/voice/messages`，LLM 助手回复自动 TTS（`e796f58b`）。

### FaceDetect 本地 / 远程拆分
- `face-detect-local` 本机推理；`face-detect-remote` + relay（`start-all -Remote`）（`bc8fa262`）。
- VPet 与测试页统一连本机 `127.0.0.1:8000`。

---

## 2026-07-20（今日）

### 上午–下午：视线 Gaze 定稿与发呆说话
- **视线算法**：MediaPipe + solvePnP 头姿、头眼加性融合、Ridge 用户映射、Kalman；服务端全屏预览与九点校准；I-DT 调试（注视点变红）。
- **行为改造**（相对实时跟随）：I-DT 判定盯同一处约 3s → 恒速走/爬到目标 → 播放 `GraphType.Move` → 到达后仍盯着则 `SpeakExternal` 发呆台词。
- 新增 `GazeConfig.cs`、`IdtFixationDetector.cs`；重写 `GazeTrackingClient` 状态机。
- Speaking 新增公开入口 `SpeakExternal`；README 更新记录。
- 提交：`79126b37`（Gaze I-DT + SpeakExternal）。

### 下午：Audio 长期记忆与人设
- `character_setting.py`：分层记忆、Persona；`main.py` 注入 system prompt。
- `.gitignore` 忽略用户记忆 JSON，保留 `memory/.gitkeep`。
- 提交：`32beed1e`。

### 下午：FaceDetect 情绪陪伴 → LLM → Speaking
- FaceDetect 轮询 `GET /latest`；非 Neutral 情绪（Happy/Sad/Surprise/Fear/Disgust/Anger）及疲劳触发 `POST /chat/care`。
- `care_prompts.py`：桌宠短口语约束（禁旁白/小说体）；不合格走本地 fallback。
- **边沿触发**：情绪出现/切换说一次，同一情绪连续不重复；回到 Neutral 后再出现可再说。
- `/chat/care` 注入语音长期记忆上下文；记忆**仅**记录语音转写用户话（格式 `用户说：…`），不落盘 care/HTTP 调试。

### FaceDetect 与摄像头联调
- `start-all` 启动后自动打开测试页 `http://127.0.0.1:8000/test-frontend/`。
- Gaze 共享帧：`GET /camera/jpeg`；FaceDetect 默认 `FACE_USE_GAZE_CAMERA=1` 从 Gaze 拉帧，**可不关视线**。
- 测试页优先 Gaze 共享；`?localcam=1` 强制本机摄像头；摄像头错误中文提示。
- local/remote `server.py`、`relay.py` 增加 `/latest`。

### 修复与稳定性
- `SpeakExternal` 强制回 UI 线程；Gaze 到达目标默认必说发呆台词（避免走动后视线飘走不说话）。
- FaceDetect 插件禁止把原生 OpenCv DLL 放进 `plugin/`（VPet `LoadFrom` 会 BadImageFormat）。
- `/latest` JSON **snake_case** 反序列化修复（此前 `faces_count` 读失败导致永不调 LLM）。
- 插件加载后约 4s 自动启动情绪陪伴；PersonaConfig dataclass 默认值语法修复。
- 陪伴 prompt 收紧：短句、第一人称、禁第三人称旁白。

### 文档与脚本
- 根目录 `README.md`：人脸陪伴、Gaze 共享摄像头说明。
- `VPet-FaceDetect/README.md`、`VPet-Speaking/README.md` 更新记录。
- `start-all.bat` / `start-all.ps1`：`-NoFaceBrowser`、Gaze 拉帧环境变量。

---

## 当前联调路径（摘要）

```text
Gaze :8766 ──camera/jpeg──► FaceDetect :8000 ──/latest──► VPet-FaceDetect
                              │                              │
                              └─ /chat/care ◄────────────────┘
                                     │
                              Audio :8010 (记忆+短口语 LLM)
                                     │
                              VPet-Speaking (F5/讯飞 TTS)
```

视线发呆：Gaze I-DT → 移动 → `SpeakExternal`（固定发呆句，不经 LLM）。  
情绪陪伴：非 Neutral 边沿 → `/chat/care`（可带记忆）→ `SpeakExternal`。
