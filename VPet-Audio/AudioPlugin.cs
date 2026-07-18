using System.Net.Http;
using System.Text;
using System.Text.Json;
using LinePutScript.Localization.WPF;
using VPet_Simulator.Windows.Interface;
using ToolBar = VPet_Simulator.Core.ToolBar;

namespace VPet.Plugin.Audio;

/// <summary>
/// 语音助手桥接插件：对接 audio/backend（默认 http://127.0.0.1:8010，避免与 face-detect-local:8000 冲突）。
/// </summary>
public sealed class AudioPlugin : MainPlugin
{
    // face-detect-local 占用 8000；语音服务建议: uvicorn --port 8010
    private const string BaseUrl = "http://127.0.0.1:8010";
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(5) };
    private CancellationTokenSource? _pollCts;
    private readonly JsonSerializerOptions _json = new() { PropertyNameCaseInsensitive = true };

    public AudioPlugin(IMainWindow mainwin) : base(mainwin) { }

    public override string PluginName => "VPet-Audio";

    public override void LoadDIY()
    {
        MW.Main.ToolBar.AddMenuButton(ToolBar.MenuType.DIY, "语音状态".Translate(), CheckStatus);
        MW.Main.ToolBar.AddMenuButton(ToolBar.MenuType.DIY, "开启语音模式".Translate(), () => ToggleVoice(true));
        MW.Main.ToolBar.AddMenuButton(ToolBar.MenuType.DIY, "关闭语音模式".Translate(), () => ToggleVoice(false));
        MW.Main.ToolBar.AddMenuButton(ToolBar.MenuType.DIY, "开始轮询语音".Translate(), StartPolling);
        MW.Main.ToolBar.AddMenuButton(ToolBar.MenuType.DIY, "停止轮询语音".Translate(), StopPolling);
    }

    private void CheckStatus()
    {
        Task.Run(async () =>
        {
            try
            {
                var health = await _http.GetStringAsync($"{BaseUrl}/health").ConfigureAwait(false);
                var status = await _http.GetStringAsync($"{BaseUrl}/voice/status").ConfigureAwait(false);
                MW.Main.Dispatcher.Invoke(() =>
                    MW.Main.SayRnd($"语音服务在线。{status}".Translate(), force: true));
                Console.WriteLine($"[VPet-Audio] health={health} status={status}");
            }
            catch (Exception ex)
            {
                MW.Main.Dispatcher.Invoke(() =>
                    MW.Main.SayRnd(
                        ("语音服务未启动: ".Translate() + ex.Message +
                         "\n请先运行 audio/backend/main.py --port 8010"),
                        force: true));
            }
        });
    }

    private void ToggleVoice(bool enable)
    {
        Task.Run(async () =>
        {
            try
            {
                var body = JsonSerializer.Serialize(new { enable });
                using var content = new StringContent(body, Encoding.UTF8, "application/json");
                var resp = await _http.PostAsync($"{BaseUrl}/voice/toggle", content).ConfigureAwait(false);
                var text = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
                MW.Main.Dispatcher.Invoke(() =>
                    MW.Main.SayRnd(
                        enable ? "语音模式已开启。".Translate() : "语音模式已关闭。".Translate(),
                        force: true));
                Console.WriteLine($"[VPet-Audio] toggle -> {text}");
            }
            catch (Exception ex)
            {
                MW.Main.Dispatcher.Invoke(() =>
                    MW.Main.SayRnd(("切换语音模式失败: ".Translate() + ex.Message), force: true));
            }
        });
    }

    private void StartPolling()
    {
        // LLM 助手回复由 VPet-Speaking 自动轮询 /voice/messages 并 TTS，避免双端抢队列
        MW.Main.SayRnd(
            "LLM 回复语音由 VPet-Speaking 自动播放，请保持 Speaking 插件启用；此处无需轮询。".Translate(),
            force: true);
    }

    private void StopPolling()
    {
        _pollCts?.Cancel();
        _pollCts = null;
        MW.Main.SayRnd("语音消息轮询已停止。".Translate(), force: true);
    }

    public override void EndGame()
    {
        _pollCts?.Cancel();
        _pollCts = null;
        _http.Dispose();
    }
}
