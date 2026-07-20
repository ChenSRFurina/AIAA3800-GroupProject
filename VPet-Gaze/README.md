# VPet-Gaze 视线注视插件

- `python/gaze_server.py`：摄像头 + MediaPipe，估计注视点，`http://127.0.0.1:8766/gaze`
- `VPet.Plugin.Gaze`：I-DT 判定「盯着某处」→ 恒速走/爬过去 → 仍盯着则触发 Speaking 发呆台词

## 行为（相对旧版「实时跟随」）

1. **不**再实时跟着视线飘  
2. 用 **I-DT** 判断是否注视同一处  
3. 持续达到 **`GazeConfig.FixationDurationSeconds`（默认 3 秒）** 后，桌宠以恒速移动到该点  
4. 移动时播放原生 **`GraphType.Move`** 动画（走/爬类，具体造型由宠物 MOD 的 move 配置决定）  
5. 到达后若仍盯着该点 → 调用 **VPet-Speaking** `SpeakExternal`，随机合成一句发呆提醒  

## 调试参数

全部在 `GazeConfig.cs`：

| 变量 | 默认 | 含义 |
|------|------|------|
| `FixationDurationSeconds` | `3.0` | 注视多久才开始移动 |
| `IdtDispersionThreshold` | `0.08` | I-DT 离散度（屏幕归一化） |
| `MoveSpeedPixelsPerSecond` | `220` | 恒速移动速度 |
| `DaydreamLines` | 4 句 | 发呆 TTS 文案池 |

## 构建

```powershell
dotnet build .\VPet-Gaze\VPet.Plugin.Gaze.csproj -c Debug
dotnet build .\VPet-Speaking\VPet.Plugin.Speaking.csproj -c Debug
```

需同时启用 MOD：`VPet-Gaze`、`VPet-Speaking`，并启动 `gaze_server.py` 与 F5-TTS。

## 使用

1. 运行 `python gaze_server.py`（九点校准）  
2. DIY →「启动视线跟随」  
3. 盯着屏幕某处 ≥ 3 秒，桌宠应走/爬过来；继续盯着会说话  
