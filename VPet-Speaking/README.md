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
| `Local_model/F5-TTS/` | 上游 F5-TTS 子模块（`pip install -e .`） |
| `Local_model/Fast_generating/` | 自研 `start_server.py` + `fast_gen.py` |

## 使用（推荐流程）

### 0. 一键安装 Speaking 环境（推荐）

在仓库根目录执行：

```powershell
cd VPet-Speaking
./setup.bat
```

说明：

- 脚本会创建/复用 `F5TTS` conda 环境（Python 3.11）
- 默认按 CUDA index 安装 `torch`/`torchaudio`（`https://download.pytorch.org/whl/cu130`）
- 自动安装本地 `F5-TTS`（editable）
- 自动安装 `qwen-tts`
- Qwen3-TTS 默认选择并预下载：`Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`

可选模型大小参数：

- `./setup.bat --qwen-0.6b`
- `./setup.bat --qwen-1.7b`（默认）

如果你只想先装依赖、不预下载模型：

```powershell
./setup.bat --skip-model
```

### 1. 先启动本地 F5 服务（保持窗口不关）

```powershell
cd VPet-Speaking\Local_model\Fast_generating
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
python Local_model\Fast_generating\fast_gen.py "你好啊" --nfe_step 8
```

## Submodule 初始化（首次克隆后必须）

```powershell
git submodule update --init --recursive VPet-Speaking/Local_model/F5-TTS
```

然后在 `F5TTS` 环境安装：

```powershell
conda activate F5TTS
cd VPet-Speaking\Local_model\F5-TTS
pip install --upgrade torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130
pip install -e .
```

## 配置

`f5tts.config`（部署到 plugin 目录）：

```
F5TTS_HOST=127.0.0.1
F5TTS_PORT=8765
F5TTS_NFE_STEP=16
F5TTS_TIMEOUT_MS=30000
```

- `NFE_STEP` 越大音质通常更稳（建议 8~24）；当前默认 16，想要更快可降到 8
- 参考音色放在 `Local_model/Fast_generating/ref/`（`*.wav` + 同名 `*.txt`）

## Qwen-TTS 后端（GPU）

`Fast_generating/start_server.py` 新增了 `qwentts` 后端，可用文本描述直接定制音色。

### 1. 安装依赖（在语音环境里）

```powershell
conda activate F5TTS
$env:VPET_TORCH_INDEX = "https://download.pytorch.org/whl/cu130"
pip install --upgrade --force-reinstall torch torchaudio --index-url $env:VPET_TORCH_INDEX
pip install --upgrade --force-reinstall qwen-tts --extra-index-url $env:VPET_TORCH_INDEX
```

说明：`qwentts` 仅支持 GPU 启动，请确保当前环境是 CUDA 版 PyTorch。

### 2. 启动 qwentts 服务

```powershell
cd VPet-Speaking\Local_model\Fast_generating
python start_server.py --tts_backend qwentts --device cuda
```

默认使用模型：`Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`

默认速度档位：`fast`（同模型下更快，音质会轻微下降）

低延迟优化（默认已启用）：

- qwentts 路径使用单次推理优先策略（`inference_mode + autocast`）
- qwentts 返回音频改为 `int16 PCM` 传输（较 `float32` 传输体积减半），降低“推理后传输等待”
- `F5TtsClient` / `fast_gen.py` 已兼容 `int16` 与 `float32` 两种 payload

可切换模型大小：

```powershell
python start_server.py --tts_backend qwentts --device cuda --qwen_model_size 1.7b
python start_server.py --tts_backend qwentts --device cuda --qwen_model_size 0.6b
```

可切换速度档位：

```powershell
python start_server.py --tts_backend qwentts --device cuda --qwen_profile fast
python start_server.py --tts_backend qwentts --device cuda --qwen_profile balanced
python start_server.py --tts_backend qwentts --device cuda --qwen_profile quality
```

如果已用 `setup.bat` 预下载，也可指定本地模型目录：

```powershell
python start_server.py --tts_backend qwentts --device cuda --qwen_model Local_model\model\Qwen3-TTS-12Hz-1.7B-VoiceDesign
```

对应 0.6B 本地目录示例：

```powershell
python start_server.py --tts_backend qwentts --device cuda --qwen_model Local_model\model\Qwen3-TTS-12Hz-0.6B-VoiceDesign
```

### 3. 两个内置预设

- `young_sister`：年轻大姐姐
- `loli`：萝莉

示例：

```powershell
# 年轻大姐姐
python fast_gen.py "今天工作辛苦啦，先喝口水休息一下吧。" --voice_preset young_sister

# 萝莉
python fast_gen.py "哥哥，你回来啦，我等你好久了。" --voice_preset loli

# 同模型加速（默认 fast，可显式写）
python fast_gen.py "今天有点困，想听你说句话。" --qwen_profile fast
```

### 4. 自定义音色（文本描述）

`--voice_instruct` 会覆盖预设，可直接写音色描述：

```powershell
python fast_gen.py "今晚想听故事吗？" --voice_instruct "温柔成熟的大姐姐音色，低一点的声线，语速偏慢，亲切陪伴感"
```

可选参数：

- `--language Chinese`
- `--max_new_tokens 2048`

## 回退

若本地服务未启动，且存在 `xunfei.config`，会自动回退讯飞云端 TTS。

## 更新记录

### 2026-07-20

- 新增 `SpeakExternal(string text, ...)`：供其他插件（如 **VPet-Gaze**）外部触发气泡 + TTS，无需点 DIY「说话」。
- DIY「说话」改为调用 `SpeakExternal`，固定调试句「好无聊啊，和我聊聊天吧」。
- 与 VPet-Gaze 联调：I-DT 判定注视约 3s → 桌宠恒速走/爬到注视点 → 到达后仍盯着则随机发呆台词经本插件合成播放。
- Gaze 侧预览/服务端同步加 I-DT debug（触发注视后注视点变红，控制台打印 `FIXATION triggered`）；参数见 `VPet-Gaze/GazeConfig.cs`。
