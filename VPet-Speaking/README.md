# VPet-Speaking — 本地 F5-TTS 语音合成插件

在 VPet DIY 菜单增加「说话」按钮：优先走本地 F5-TTS `Fast_generating` 常驻服务（低延迟），失败时回退讯飞。

## 结构

| 文件 | 说明 |
|------|------|
| `SpeakingPlugin.cs` | 插件入口，注册「说话」按钮 |
| `F5TtsClient.cs` | 本地 F5 Fast_generating TCP 客户端 |
| `XunfeiTtsClient.cs` | 讯飞 WebSocket TTS（可选回退） |
| `GetMessage.cs` | 合成文本入口 |
| `f5tts.config` | 本地服务地址 / nfe_step |
| `Local_model/F5-TTS/Fast_generating/` | `start_server.py` + `fast_gen.py` |

## 使用（推荐流程）

### 1. 先启动本地 F5 服务（保持窗口不关）

```powershell
cd VPet-Speaking\Local_model\F5-TTS\Fast_generating
python start_server.py
```

服务会加载模型、学习 `ref/` 参考音色，并监听 `127.0.0.1:8765`。

### 2. 构建插件

```powershell
# 在 VPet 目录下
& "C:\Program Files\dotnet\dotnet.exe" build VPet-Speaking\VPet.Plugin.Speaking.csproj -c Debug
```

构建后自动复制到：

`VPet-Simulator.Windows\mod\1200_VPet-Speaking\plugin\`

### 3. 启动 VPet → 右键桌宠 → 自定 → 说话

桌宠显示气泡并播放本地 F5 合成语音。

## 独立测速（不启动 VPet）

```powershell
# 需先运行 start_server.py
dotnet run --project VPet-Speaking\SmokeTest\SmokeTest.csproj
```

或直接用 Python 客户端：

```powershell
python Local_model\F5-TTS\Fast_generating\fast_gen.py "你好啊" --nfe_step 8
```

## 配置

`f5tts.config`（部署到 plugin 目录）：

```
F5TTS_HOST=127.0.0.1
F5TTS_PORT=8765
F5TTS_NFE_STEP=8
F5TTS_TIMEOUT_MS=30000
```

- `NFE_STEP` 越小越快（建议 4~16），过大则延迟上升
- 参考音色放在 `Local_model/F5-TTS/Fast_generating/ref/`（`*.wav` + 同名 `*.txt`）

## 回退

若本地服务未启动，且存在 `xunfei.config`，会自动回退讯飞云端 TTS。
