# VPet-Speaking — 讯飞语音合成插件

在 VPet DIY 菜单增加「说话」按钮：点击后合成并播放 `get_message.py` 中的测试样例文本。

## 结构

| 文件 | 说明 |
|------|------|
| `SpeakingPlugin.cs` | 插件入口，注册「说话」按钮 |
| `XunfeiTtsClient.cs` | 讯飞 WebSocket TTS（移植自官方 Python demo） |
| `GetMessage.cs` | 对应 `get_message.py` 的文本入口 |
| `xunfei.config` | 讯飞 APPID / Key / Secret |
| `get_message.py` | 测试样例脚本 |

## 构建与部署

```powershell
# 在 VPet 目录下
& "C:\Program Files\dotnet\dotnet.exe" build VPet-Speaking\VPet.Plugin.Speaking.csproj -c Debug
```

构建成功后会自动复制到：

`VPet-Simulator.Windows\mod\1200_VPet-Speaking\plugin\`

## 使用

1. 首次运行前，在 `VPet-Simulator.Windows` 目录执行 `mklink.bat`（管理员），把 `mod` 链接到输出目录  
   或直接用 Visual Studio 以 **Debug | x64** 启动（若已配置好链接）
2. 构建本插件：`dotnet build VPet-Speaking\VPet.Plugin.Speaking.csproj -c Debug`
3. 启动 `VPet-Simulator.Windows`（Debug 下未签名插件可直接加载；本 MOD 已加入默认启用列表）
4. 右键桌宠 → **自定** → **说话**
5. 桌宠会显示气泡并播放「This is used for testing.」的合成语音

独立验证 TTS（不启动 VPet）：

```powershell
dotnet run --project VPet-Speaking\SmokeTest\SmokeTest.csproj
```

若 Release 运行，需在设置中对该 MOD 开启「通过模组 / PassMOD」。
