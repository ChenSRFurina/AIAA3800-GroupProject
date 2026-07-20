# VPet-FaceDetect

对接本机 `http://127.0.0.1:8000`（local 推理或 `-Remote` relay）。

## 推荐流程

1. `.\start-all.bat`：启动后端并**自动打开**浏览器 `http://127.0.0.1:8000/test-frontend/`
2. 在页面里允许摄像头推流
3. VPet DIY → **启动情绪陪伴**（轮询 `/latest` → LLM → Speaking）

不自动开浏览器：`.\start-all.bat -NoFaceBrowser`

## 场景

除 **Neutral** 外，情绪**出现/切换**时触发一次 LLM（带语音记忆上下文）→ Speaking；**同一情绪连续出现不再重复**。回到 Neutral 后再出现可再说。

| 情绪 | scene |
|------|-------|
| Happy | `happy` |
| Sad | `sad` |
| Surprise | `surprise` |
| Fear | `fear` |
| Disgust | `disgust` |
| Anger | `anger` |
| Neutral 且疲劳高 | `fatigue` |

参数：`FaceDetectConfig.cs`。
