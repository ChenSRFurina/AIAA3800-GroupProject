using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Channels;
using System.Windows;
using System.Reflection;
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
        private CancellationTokenSource? _queueCts;
        private Task? _queueTask;
        private readonly Channel<SpeakRequest> _speakQueue =
            Channel.CreateUnbounded<SpeakRequest>(new UnboundedChannelOptions
            {
                SingleReader = true,
                SingleWriter = false,
            });
        private readonly SemaphoreSlim _speakGate = new(1, 1);
        private readonly object _stateLock = new();
        private CancellationTokenSource? _activeSpeakCts;
        private SpeechSource _activeSource = SpeechSource.None;
        private string _activeEmotionScene = "";
        private int _activeUserSpeechVersion;
        private string _activeText = "";
        private DateTime _activeStartedUtc = DateTime.MinValue;
        private string? _pendingInterruptionContext;
        private string? _lastUserSpeech;
        private DateTime _lastUserSpeechUtc = DateTime.MinValue;
        private string? _lastInterruptedEmotion;
        private string? _lastInterruptedEmotionSummary;
        private string? _lastInterruptionContext;
        private DateTime _lastInterruptionUtc = DateTime.MinValue;
        private int _pendingUserReplyCount;
        private int _pollFailCount;
        private int _userSpeechVersion;
        private const int PlaybackWatchdogSeconds = 45;

        private const int RecentUserSpeechSeconds = 8;

        private enum SpeechSource
        {
            None,
            Debug,
            External,
            EmotionCare,
            UserReply,
        }

        private sealed record SpeakRequest(
            string Text,
            string Tag,
            bool ShowErrorDialog,
            SpeechSource Source,
            string EmotionScene,
            int UserSpeechVersion,
            DateTime EnqueuedAtUtc);

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

            _queueCts = new CancellationTokenSource();
            _queueTask = Task.Run(() => ProcessSpeakQueueAsync(_queueCts.Token));
            Console.WriteLine("[VPet-Speaking] 已启动统一语音调度队列");
        }

        public override void LoadDIY()
        {
            MW.Main.ToolBar.AddMenuButton(ToolBar.MenuType.DIY, "说话".Translate(), SpeakDebugFixed);
        }

        /// <summary>DIY 调试：固定合成「好无聊啊，和我聊聊天吧」。</summary>
        private void SpeakDebugFixed()
        {
            EnqueueSpeak(GetMessage.ChatPrompt, showErrorDialog: true, tag: "debug", source: SpeechSource.Debug);
        }

        /// <summary>
        /// 供其他插件（如 VPet-Gaze 发呆提醒）调用：气泡 + F5/讯飞 TTS。
        /// 可从任意线程调用；UI 部分会切回主线程。
        /// </summary>
        public void SpeakExternal(string text, bool showErrorDialog = false, string tag = "external")
        {
            if (string.IsNullOrWhiteSpace(text))
                return;

            var source = (tag.Equals("face-care", StringComparison.OrdinalIgnoreCase)
                || tag.StartsWith("face-care:", StringComparison.OrdinalIgnoreCase))
                ? SpeechSource.EmotionCare
                : SpeechSource.External;
            EnqueueSpeak(text, showErrorDialog, tag, source);
        }

        public string? GetAndConsumeInterruptionContext()
        {
            lock (_stateLock)
            {
                var ctx = _pendingInterruptionContext;
                _pendingInterruptionContext = null;
                return ctx;
            }
        }

        public string? GetRecentUserSpeechHint()
        {
            lock (_stateLock)
            {
                if (string.IsNullOrWhiteSpace(_lastUserSpeech))
                    return null;
                if ((DateTime.UtcNow - _lastUserSpeechUtc).TotalSeconds > RecentUserSpeechSeconds)
                    return null;
                return _lastUserSpeech;
            }
        }

        public bool IsUserReplyPriorityActive()
        {
            lock (_stateLock)
            {
                return _activeSource == SpeechSource.UserReply
                    || Volatile.Read(ref _pendingUserReplyCount) > 0
                    || (DateTime.UtcNow - _lastUserSpeechUtc).TotalSeconds <= RecentUserSpeechSeconds;
            }
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

                            if (string.Equals(type, "user_start", StringComparison.OrdinalIgnoreCase))
                            {
                                NotifyUserSpeechStart();
                                continue;
                            }

                            if (string.IsNullOrWhiteSpace(content))
                                continue;

                            if (string.Equals(type, "user_message", StringComparison.OrdinalIgnoreCase))
                            {
                                NotifyUserSpeech(content!);
                                continue;
                            }

                            if (string.Equals(type, "assistant", StringComparison.OrdinalIgnoreCase))
                            {
                                EnqueueSpeak(content!, showErrorDialog: false, tag: "llm", source: SpeechSource.UserReply);
                                continue;
                            }
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
                    await Task.Delay(120, token).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    break;
                }
            }
        }

        private void EnqueueSpeak(string text, bool showErrorDialog, string tag, SpeechSource source)
        {
            if (string.IsNullOrWhiteSpace(text))
                return;

            var emotionScene = "";
            if (source == SpeechSource.EmotionCare)
            {
                if (tag.StartsWith("face-care:", StringComparison.OrdinalIgnoreCase))
                {
                    emotionScene = tag.Substring("face-care:".Length).Trim();
                }
            }

            if (source == SpeechSource.EmotionCare)
            {
                if (Volatile.Read(ref _pendingUserReplyCount) > 0 || IsRecentUserSpeechActive())
                {
                    Console.WriteLine("[VPet-Speaking] skip face-care: pending user turn");
                    return;
                }
            }

            if (source == SpeechSource.UserReply)
            {
                Interlocked.Increment(ref _pendingUserReplyCount);
                TryInterruptEmotionCare("user_turn_reply", text);
            }

            var req = new SpeakRequest(
                text.Trim(),
                tag,
                showErrorDialog,
                source,
                emotionScene,
                source == SpeechSource.UserReply ? Volatile.Read(ref _userSpeechVersion) : 0,
                DateTime.UtcNow);
            if (!_speakQueue.Writer.TryWrite(req))
            {
                if (source == SpeechSource.UserReply)
                    Interlocked.Decrement(ref _pendingUserReplyCount);
            }
        }

        private async Task ProcessSpeakQueueAsync(CancellationToken token)
        {
            await foreach (var req in _speakQueue.Reader.ReadAllAsync(token).ConfigureAwait(false))
            {
                if (req.Source == SpeechSource.UserReply && req.UserSpeechVersion != Volatile.Read(ref _userSpeechVersion))
                {
                    Console.WriteLine("[VPet-Speaking] drop stale user-reply request");
                    Interlocked.Decrement(ref _pendingUserReplyCount);
                    continue;
                }

                if (req.Source == SpeechSource.EmotionCare && (Volatile.Read(ref _pendingUserReplyCount) > 0 || IsRecentUserSpeechActive()))
                {
                    Console.WriteLine("[VPet-Speaking] drop stale face-care request");
                    continue;
                }

                await _speakGate.WaitAsync(token).ConfigureAwait(false);
                using var speakCts = CancellationTokenSource.CreateLinkedTokenSource(token);
                var completed = false;
                try
                {
                    lock (_stateLock)
                    {
                        _activeSpeakCts = speakCts;
                        _activeSource = req.Source;
                        _activeEmotionScene = req.EmotionScene;
                        _activeUserSpeechVersion = req.UserSpeechVersion;
                        _activeText = req.Text;
                        _activeStartedUtc = DateTime.UtcNow;
                    }

                    await PerformSpeakAsync(req, speakCts.Token).ConfigureAwait(false);
                    completed = true;
                }
                catch (OperationCanceledException)
                {
                    Console.WriteLine($"[VPet-Speaking] speech canceled: {req.Tag}");
                }
                catch (Exception ex)
                {
                    Console.WriteLine($"[VPet-Speaking] speak failed: {ex.Message}");
                    if (req.ShowErrorDialog)
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
                    if (completed)
                    {
                        _ = ReportAssistantSpeechMemoryAsync(req.Text, req.Source, req.EmotionScene, interrupted: false);
                    }
                    else if (req.Source == SpeechSource.EmotionCare)
                    {
                        _ = ReportAssistantSpeechMemoryAsync(req.Text, req.Source, req.EmotionScene, interrupted: true);
                    }

                    lock (_stateLock)
                    {
                        _activeSpeakCts = null;
                        _activeSource = SpeechSource.None;
                        _activeEmotionScene = "";
                        _activeUserSpeechVersion = 0;
                        _activeText = "";
                        _activeStartedUtc = DateTime.MinValue;
                    }

                    if (req.Source == SpeechSource.UserReply)
                        Interlocked.Decrement(ref _pendingUserReplyCount);

                    _speakGate.Release();
                }
            }
        }

        private async Task PerformSpeakAsync(SpeakRequest req, CancellationToken token)
        {
            MW.Main.Dispatcher.Invoke(() =>
            {
                MW.Main.ToolBar.Visibility = Visibility.Collapsed;
                MW.Main.SayRnd(req.Text, force: true);
            });

            await SynthesizeAndPlayAsync(req.Text, req.Tag, token).ConfigureAwait(false);
            await WaitForPlaybackDoneAsync(token).ConfigureAwait(false);
        }

        private void NotifyUserSpeech(string text)
        {
            var cleaned = (text ?? "").Trim();
            if (string.IsNullOrWhiteSpace(cleaned))
                return;

            lock (_stateLock)
            {
                _userSpeechVersion++;
                _lastUserSpeech = cleaned;
                _lastUserSpeechUtc = DateTime.UtcNow;
            }

            TryInterruptEmotionCare("user_started_speaking", cleaned);
            TryInterruptUserReply("user_started_speaking", cleaned);
            _ = ReportUserSpeechMemoryAsync(cleaned);
        }

        private void NotifyUserSpeechStart()
        {
            lock (_stateLock)
            {
                _userSpeechVersion++;
                _lastUserSpeechUtc = DateTime.UtcNow;
            }

            TryInterruptEmotionCare("user_started_speaking", "");
            TryInterruptUserReply("user_started_speaking", "");
        }

        private bool IsRecentUserSpeechActive()
        {
            lock (_stateLock)
            {
                return (DateTime.UtcNow - _lastUserSpeechUtc).TotalSeconds <= RecentUserSpeechSeconds;
            }
        }

        private void TryInterruptEmotionCare(string reason, string incomingText)
        {
            CancellationTokenSource? ctsToCancel = null;
            string? interruptedText = null;
            DateTime startedAt = DateTime.MinValue;
            string interruptedScene = "";

            lock (_stateLock)
            {
                if (_activeSource != SpeechSource.EmotionCare)
                    return;
                ctsToCancel = _activeSpeakCts;
                interruptedText = _activeText;
                startedAt = _activeStartedUtc;
                interruptedScene = TryGetActiveEmotionScene();
                _pendingInterruptionContext =
                    $"interrupted=true;reason={reason};scene={interruptedScene};started_utc={startedAt:O};partial_reply={TrimForHint(interruptedText)};incoming={TrimForHint(incomingText)}";
                _lastInterruptedEmotion = interruptedScene;
                _lastInterruptedEmotionSummary = FetchEmotionSummaryHint();
                _lastInterruptionContext = _pendingInterruptionContext;
                _lastInterruptionUtc = DateTime.UtcNow;
            }

            try
            {
                ctsToCancel?.Cancel();
            }
            catch
            {
                // ignore
            }

            StopCurrentPlayback();
            Console.WriteLine($"[VPet-Speaking] interrupted face-care due to {reason}");
        }

        private void TryInterruptUserReply(string reason, string incomingText)
        {
            CancellationTokenSource? ctsToCancel = null;

            lock (_stateLock)
            {
                if (_activeSource != SpeechSource.UserReply)
                    return;
                ctsToCancel = _activeSpeakCts;
            }

            try
            {
                ctsToCancel?.Cancel();
            }
            catch
            {
                // ignore
            }

            StopCurrentPlayback();
            Console.WriteLine($"[VPet-Speaking] interrupted user-reply due to {reason}: {TrimForHint(incomingText)}");
        }

        private string TryGetActiveEmotionScene()
        {
            return _activeEmotionScene;
        }

        private void StopCurrentPlayback()
        {
            try
            {
                MW.Main.Dispatcher.Invoke(() =>
                {
                    if (MW.Main.PlayingVoice)
                    {
                        MW.Main.VoicePlayer.Stop();
                        MW.Main.PlayingVoice = false;
                    }
                });
            }
            catch
            {
                // ignore
            }
        }

        private static string TrimForHint(string? text)
        {
            if (string.IsNullOrWhiteSpace(text))
                return "";
            var v = text.Trim();
            return v.Length <= 72 ? v : v[..72];
        }

        private async Task SynthesizeAndPlayAsync(string text, string tag, CancellationToken token)
        {
            var sw = Stopwatch.StartNew();
            var (audio, ext, engine) = await SynthesizeWithFallbackAsync(text, token).ConfigureAwait(false);
            token.ThrowIfCancellationRequested();
            var path = Path.Combine(
                GraphCore.CachePath,
                "voice",
                $"{tag}_{engine}_{DateTime.UtcNow.Ticks:X}.{ext}");
            await File.WriteAllBytesAsync(path, audio, token).ConfigureAwait(false);
            Console.WriteLine(
                $"[VPet-Speaking] {tag}/{engine} ready in {sw.ElapsedMilliseconds} ms -> {path} ({audio.Length} bytes)");

            token.ThrowIfCancellationRequested();
            MW.Main.Dispatcher.Invoke(() => MW.Main.PlayVoice(new Uri(path)));
        }

        private async Task WaitForPlaybackDoneAsync(CancellationToken token)
        {
            var sw = Stopwatch.StartNew();
            while (true)
            {
                token.ThrowIfCancellationRequested();
                bool playing = false;
                try
                {
                    playing = MW.Main.Dispatcher.Invoke(() => MW.Main.PlayingVoice);
                }
                catch
                {
                    playing = false;
                }

                if (!playing)
                    return;

                if (sw.Elapsed.TotalSeconds > PlaybackWatchdogSeconds)
                {
                    Console.WriteLine("[VPet-Speaking] playback watchdog timeout, forcing stop");
                    StopCurrentPlayback();
                    return;
                }

                await Task.Delay(80, token).ConfigureAwait(false);
            }
        }

        private async Task ReportUserSpeechMemoryAsync(string text)
        {
            try
            {
                var emotionSummary = FetchEmotionSummaryHint();
                string interruptedEmotion = "";
                string interruptionContext = "";
                string interruptedEmotionSummary = "";

                lock (_stateLock)
                {
                    if ((DateTime.UtcNow - _lastInterruptionUtc).TotalSeconds <= RecentUserSpeechSeconds)
                    {
                        interruptedEmotion = _lastInterruptedEmotion ?? "";
                        interruptedEmotionSummary = _lastInterruptedEmotionSummary ?? "";
                        interruptionContext = _lastInterruptionContext ?? "";
                    }
                    _lastInterruptedEmotion = null;
                    _lastInterruptedEmotionSummary = null;
                    _lastInterruptionContext = null;
                    _lastInterruptionUtc = DateTime.MinValue;
                }

                var payload = JsonSerializer.Serialize(new
                {
                    event_type = "user",
                    text,
                    source = "voice-asr",
                    emotion_summary = string.IsNullOrWhiteSpace(emotionSummary)
                        ? (string.IsNullOrWhiteSpace(interruptedEmotionSummary) ? null : interruptedEmotionSummary)
                        : emotionSummary,
                    interruption_context = string.IsNullOrWhiteSpace(interruptionContext) ? null : interruptionContext,
                    interrupted_emotion = string.IsNullOrWhiteSpace(interruptedEmotion) ? null : interruptedEmotion,
                });
                using var content = new StringContent(payload, Encoding.UTF8, "application/json");
                using var resp = await _http.PostAsync($"{AudioBaseUrl}/memory/voice-event", content).ConfigureAwait(false);
                _ = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[VPet-Speaking] report user memory failed: {ex.Message}");
            }
        }

        private async Task ReportAssistantSpeechMemoryAsync(string text, SpeechSource source, string emotionScene, bool interrupted)
        {
            try
            {
                var category = source == SpeechSource.EmotionCare ? "care" : "assistant";
                var src = source == SpeechSource.EmotionCare ? "emotion-care" : "voice-assistant";

                var payload = JsonSerializer.Serialize(new
                {
                    event_type = "assistant",
                    text,
                    source = src,
                    category,
                    scene = string.IsNullOrWhiteSpace(emotionScene) ? null : emotionScene,
                    interrupted,
                });
                using var content = new StringContent(payload, Encoding.UTF8, "application/json");
                using var resp = await _http.PostAsync($"{AudioBaseUrl}/memory/voice-event", content).ConfigureAwait(false);
                _ = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[VPet-Speaking] report assistant memory failed: {ex.Message}");
            }
        }

        private string FetchEmotionSummaryHint()
        {
            foreach (var plugin in MW.Plugins)
            {
                if (plugin.GetType().FullName != "VPet.Plugin.FaceDetect.FaceDetectPlugin")
                    continue;

                var field = plugin.GetType().GetField("_careClient", BindingFlags.Instance | BindingFlags.NonPublic);
                var care = field?.GetValue(plugin);
                if (care == null)
                    continue;

                var method = care.GetType().GetMethod("GetLatestEmotionSummaryForUserSpeech", BindingFlags.Instance | BindingFlags.Public);
                var value = method?.Invoke(care, null) as string;
                if (!string.IsNullOrWhiteSpace(value))
                    return value;
            }

            return "";
        }

        private async Task<(byte[] Audio, string Ext, string Engine)> SynthesizeWithFallbackAsync(string text, CancellationToken token)
        {
            try
            {
                var wav = await _f5!.SynthesizeAsync(text, token).ConfigureAwait(false);
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

                var mp3 = await _xunfei.SynthesizeAsync(text, token).ConfigureAwait(false);
                return (mp3, "mp3", "xunfei");
            }
        }

        public override void EndGame()
        {
            _replyPollCts?.Cancel();
            _queueCts?.Cancel();
            _replyPollCts = null;
            _queueCts = null;
            _http.Dispose();
            _speakGate.Dispose();
        }
    }
}
