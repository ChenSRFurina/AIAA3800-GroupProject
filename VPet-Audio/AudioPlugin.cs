using System.Net.Http;
using System.Text;
using System.Text.Json;
using LinePutScript.Localization.WPF;
using VPet_Simulator.Windows.Interface;
using ToolBar = VPet_Simulator.Core.ToolBar;

namespace VPet.Plugin.Audio;

/// <summary>
/// 语音助手桥接插件：对接 audio/backend（默认 http://127.0.0.1:8010，避免与 face-detect:8000 冲突）。
/// </summary>
public sealed class AudioPlugin : MainPlugin
{
    // face-detect 占用 8000；语音服务建议: uvicorn --port 8010
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
        if (_pollCts is { IsCancellationRequested: false })
        {
            MW.Main.SayRnd("语音轮询已在运行。".Translate(), force: true);
            return;
        }

        _pollCts = new CancellationTokenSource();
        var token = _pollCts.Token;
        Task.Run(() => PollLoopAsync(token), token);
        MW.Main.SayRnd("开始轮询语音消息。".Translate(), force: true);
    }

    private void StopPolling()
    {
        _pollCts?.Cancel();
        _pollCts = null;
        MW.Main.SayRnd("已停止轮询语音消息。".Translate(), force: true);
    }

    private async Task PollLoopAsync(CancellationToken token)
    {
        while (!token.IsCancellationRequested)
        {
            try
            {
                var json = await _http.GetStringAsync($"{BaseUrl}/voice/messages", token).ConfigureAwait(false);
                using var doc = JsonDocument.Parse(json);
                if (doc.RootElement.TryGetProperty("messages", out var msgs) &&
                    msgs.ValueKind == JsonValueKind.Array)
                {
                    foreach (var msg in msgs.EnumerateArray())
                    {
                        var type = msg.TryGetProperty("type", out var t) ? t.GetString() : "";
                        var content = msg.TryGetProperty("content", out var c) ? c.GetString() : "";
                        if (string.IsNullOrWhiteSpace(content))
                            continue;

                        // 助手回复显示在桌宠气泡
                        if (string.Equals(type, "assistant", StringComparison.OrdinalIgnoreCase))
                        {
                            MW.Main.Dispatcher.Invoke(() => MW.Main.SayRnd(content!, force: true));
                        }
                    }
                }
            }
            catch (OperationCanceledException)
            {
                break;
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[VPet-Audio] poll error: {ex.Message}");
            }

            try
            {
                await Task.Delay(800, token).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }
    }

    public override void EndGame()
    {
        _pollCts?.Cancel();
        _pollCts = null;
        _http.Dispose();
    }
}
