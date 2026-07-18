# VPet-Gaze 视线跟随插件

本工程把视线追踪拆成两个独立部分：

- `python/gaze_server.py`：使用摄像头和 MediaPipe 估计用户注视位置，并通过 `http://127.0.0.1:8766/gaze` 暴露数据。
- `VPet.Plugin.Gaze`：VPet C# 插件，读取上述接口并通过 VPet 原生 `IController.MoveWindows()` 平滑移动桌宠。

这样不需要修改 `MainWindow.cs`、`MWController.cs` 或其他 VPet 主程序源码。

## 一、放置目录

把整个 `VPet-Gaze` 文件夹放到 VPet 仓库根目录，形成：

```text
VPet-main/
├─ VPet-Simulator.Windows/
├─ VPet-Simulator.Windows.Interface/
├─ VPet-Gaze/
│  ├─ VPet.Plugin.Gaze.csproj
│  ├─ GazePlugin.cs
│  ├─ GazeTrackingClient.cs
│  ├─ GazeData.cs
│  ├─ 1300_VPet-Gaze/info.lps
│  └─ python/
└─ VPet.sln
```

如果你们统一使用队友的 `Project-AIAA3800` 仓库，也可以把本目录与 `VPet-Speaking` 并列放置，然后把两个插件文件夹一起复制到本地 VPet 根目录再构建。

## 二、先用模拟服务测试

```powershell
cd "VPet-Gaze\python"
python -m pip install fastapi uvicorn
python gaze_server_mock.py
```

打开：

```text
http://127.0.0.1:8766/gaze
```

应该持续返回 `screen_x` 和 `screen_y`。

## 三、构建插件

在 VPet 根目录执行：

```powershell
dotnet build .\VPet-Gaze\VPet.Plugin.Gaze.csproj -c Debug
```

构建后会自动部署至：

```text
VPet-Simulator.Windows\mod\1300_VPet-Gaze\
```

然后重新构建或直接启动 VPet：

```powershell
dotnet build VPet.sln -c Debug -p:Platform=x64
.\VPet-Simulator.Windows\bin\x64\Debug\net8.0-windows\VPet-Simulator.Windows.exe
```

首次加载新 MOD 时，在 VPet 的 MOD 设置中启用 `VPet-Gaze`，然后重启桌宠。Debug 构建允许加载未签名插件；Release 模式可能需要开启“通过模组/PassMOD”。

## 四、使用

1. 保持 `gaze_server_mock.py` 或 `gaze_server.py` 运行。
2. 右键桌宠，打开“自定/DIY”菜单。
3. 点击“启动视线跟随”。
4. 点击“停止视线跟随”即可停止。

## 五、接入真实摄像头

```powershell
conda activate F5TTS
cd "VPet-Gaze\python"
# 必须 mediapipe==0.10.21（新版本无 mp.solutions）
python -m pip install -r ..\requirements.txt
python gaze_server.py
```

摄像头窗口中按 `Q` 或 `Esc` 可关闭预览。服务器关闭后插件不会导致 VPet 崩溃，只会停止收到新的视线数据。

## 六、参数调整

插件移动参数位于 `GazeTrackingClient.cs`：

- `PollIntervalMs`：请求频率；默认 80 ms。
- `FollowGain`：每次向目标移动的比例；越大越灵敏。
- `MaxStepPixels`：单次最大移动距离，防止瞬移。
- `DeadZonePixels`：小范围抖动的死区。

Python 映射参数位于 `gaze_server.py`：

```python
horizontal_min, horizontal_max = 0.34, 0.66
vertical_min, vertical_max = 0.28, 0.72
```

若桌宠很难移动到屏幕边缘，可缩小区间；若移动过于敏感，可扩大区间。

## 七、接口格式

```json
{
  "valid": true,
  "gaze_x": -0.25,
  "gaze_y": 0.10,
  "screen_x": 0.375,
  "screen_y": 0.55,
  "timestamp": 1783837865.02
}
```

插件真正用于移动的是 `screen_x`、`screen_y`，取值范围为 `0~1`。
