using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Text.Json;
using System.Windows;
using LinePutScript.Localization.WPF;
using Panuon.WPF.UI;
using VPet_Simulator.Core;
using VPet_Simulator.Windows.Interface;
using ToolBar = VPet_Simulator.Core.ToolBar;

namespace VPet.Plugin.Speaking
{
    /// <summary>
    /// DIY「说话」：固定调试文本 TTS。
    /// 同时轮询 audio /voice/messages：LLM 助手回复到达后自动合成并播放。
    /// </summary>
    public class SpeakingPlugin : MainPlugin
    {
        private const string AudioBaseUrl = "http://127.0.0.1:8010";

        private F5TtsClient? _f5;
        private XunfeiTtsClient? _xunfei;
        private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(5) };
        private CancellationTokenSource? _replyPollCts;
        private bool _busyDebugSpeak;
        private bool _busyLlmSpeak;
        private int _pollFailCount;

        public SpeakingPlugin(IMainWindow mainwin) : base(mainwin) { }

        public override string PluginName => "VPet-Speaking";

        public override void LoadPlugin()
        {
            _f5 = F5TtsClient.FromConfigNearAssembly();

            try
            {
                _xunfei = XunfeiTtsClient.FromConfigNearAssembly();
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[VPet-Speaking] 讯飞配置未加载（本地 F5 优先，可忽略）: {ex.Message}");
            }

            var voiceDir = Path.Combine(GraphCore.CachePath, "voice");
            if (!Directory.Exists(voiceDir))
                Directory.CreateDirectory(voiceDir);

            _ = Task.Run(async () =>
            {
                try
                {
                    if (_f5 != null && await _f5.PingAsync().ConfigureAwait(false))
                        Console.WriteLine($"[VPet-Speaking] F5 预热成功 {_f5.Host}:{_f5.Port} nfe={_f5.NfeStep}");
                    else
                        Console.WriteLine("[VPet-Speaking] F5 服务未就绪（说话前请先 start_server.py）");
                }
                catch (Exception ex)
                {
                    Console.WriteLine($"[VPet-Speaking] F5 预热失败: {ex.Message}");
                }
            });

            // 自动轮询 audio LLM 回复 → TTS（模拟对话）
            _replyPollCts = new CancellationTokenSource();
            _ = Task.Run(() => PollAudioRepliesAsync(_replyPollCts.Token));
            Console.WriteLine("[VPet-Speaking] 已启动 audio LLM 回复轮询 → 自动 TTS");
        }

        public override void LoadDIY()
        {
            MW.Main.ToolBar.AddMenuButton(ToolBar.MenuType.DIY, "说话".Translate(), SpeakDebugFixed);
        }

        /// <summary>DIY 调试：固定合成「好无聊啊，和我聊聊天吧」。</summary>
        private void SpeakDebugFixed()
        {
            SpeakExternal(GetMessage.ChatPrompt, showErrorDialog: true, tag: "debug");
        }

        /// <summary>
        /// 供其他插件（如 VPet-Gaze 发呆提醒）调用：气泡 + F5/讯飞 TTS。
        /// </summary>
        public void SpeakExternal(string text, bool showErrorDialog = false, string tag = "external")
        {
            if (string.IsNullOrWhiteSpace(text))
                return;
            if (_busyDebugSpeak || _busyLlmSpeak)
                return;

            _f5 ??= F5TtsClient.FromConfigNearAssembly();
            _busyDebugSpeak = true;
            MW.Main.ToolBar.Visibility = Visibility.Collapsed;
            MW.Main.SayRnd(text, force: true);

            Task.Run(async () =>
            {
                try
                {
                    await SynthesizeAndPlayAsync(text, tag).ConfigureAwait(false);
                }
                catch (Exception ex)
                {
                    Console.WriteLine($"[VPet-Speaking] SpeakExternal failed: {ex.Message}");
                    if (showErrorDialog)
                    {
                        MW.Main.Dispatcher.Invoke(() =>
                        {
                            MessageBoxX.Show(
                                ("语音合成失败: ".Translate() + ex.Message +
                                 "\n\n请先启动:\npython Local_model/F5-TTS/Fast_generating/start_server.py"),
                                "VPet-Speaking",
                                MessageBoxIcon.Error);
                        });
                    }
                }
                finally
                {
                    _busyDebugSpeak = false;
                }
            });
        }

        /// <summary>轮询 audio 的 LLM 助手回复，到达后气泡 + TTS。</summary>
        private async Task PollAudioRepliesAsync(CancellationToken token)
        {
            while (!token.IsCancellationRequested)
            {
                try
                {
                    var json = await _http.GetStringAsync($"{AudioBaseUrl}/voice/messages", token)
                        .ConfigureAwait(false);
                    _pollFailCount = 0;
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

                            if (!string.Equals(type, "assistant", StringComparison.OrdinalIgnoreCase))
                                continue;

                            await HandleLlmReplyAsync(content!, token).ConfigureAwait(false);
                        }
                    }
                }
                catch (OperationCanceledException) when (token.IsCancellationRequested)
                {
                    break;
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch (Exception ex)
                {
                    _pollFailCount++;
                    if (_pollFailCount == 1 || _pollFailCount % 50 == 0)
                        Console.WriteLine($"[VPet-Speaking] poll audio replies: {ex.Message}");
                }

                try
                {
                    await Task.Delay(600, token).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    break;
                }
            }
        }

        private async Task HandleLlmReplyAsync(string reply, CancellationToken token)
        {
            // 避免与 debug「说话」或上一条 LLM TTS 重叠
            while ((_busyDebugSpeak || _busyLlmSpeak) && !token.IsCancellationRequested)
                await Task.Delay(200, token).ConfigureAwait(false);

            if (token.IsCancellationRequested)
                return;

            _busyLlmSpeak = true;
            try
            {
                Console.WriteLine($"[VPet-Speaking] LLM reply → TTS: {reply}");
                MW.Main.Dispatcher.Invoke(() => MW.Main.SayRnd(reply, force: true));
                await SynthesizeAndPlayAsync(reply, "llm").ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[VPet-Speaking] LLM TTS 失败: {ex.Message}");
                MW.Main.Dispatcher.Invoke(() =>
                    MW.Main.SayRnd(("语音合成失败: ".Translate() + ex.Message), force: true));
            }
            finally
            {
                _busyLlmSpeak = false;
            }
        }

        private async Task SynthesizeAndPlayAsync(string text, string tag)
        {
            var sw = Stopwatch.StartNew();
            var (audio, ext, engine) = await SynthesizeWithFallbackAsync(text).ConfigureAwait(false);
            var path = Path.Combine(
                GraphCore.CachePath,
                "voice",
                $"{tag}_{engine}_{DateTime.UtcNow.Ticks:X}.{ext}");
            await File.WriteAllBytesAsync(path, audio).ConfigureAwait(false);
            Console.WriteLine(
                $"[VPet-Speaking] {tag}/{engine} ready in {sw.ElapsedMilliseconds} ms -> {path} ({audio.Length} bytes)");

            MW.Main.Dispatcher.Invoke(() => MW.Main.PlayVoice(new Uri(path)));
        }

        private async Task<(byte[] Audio, string Ext, string Engine)> SynthesizeWithFallbackAsync(string text)
        {
            try
            {
                var wav = await _f5!.SynthesizeAsync(text).ConfigureAwait(false);
                return (wav, "wav", "f5");
            }
            catch (Exception f5Ex)
            {
                Console.WriteLine($"[VPet-Speaking] F5 失败，尝试讯飞回退: {f5Ex.Message}");

                if (_xunfei == null)
                {
                    try
                    {
                        _xunfei = XunfeiTtsClient.FromConfigNearAssembly();
                    }
                    catch
                    {
                        throw new InvalidOperationException(
                            f5Ex.Message + "\n（讯飞回退也不可用：未找到 xunfei.config）",
                            f5Ex);
                    }
                }

                var mp3 = await _xunfei.SynthesizeAsync(text).ConfigureAwait(false);
                return (mp3, "mp3", "xunfei");
            }
        }

        public override void EndGame()
        {
            _replyPollCts?.Cancel();
            _replyPollCts = null;
            _http.Dispose();
        }
    }
}
