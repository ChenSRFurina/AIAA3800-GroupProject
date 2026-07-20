using System.Net.Http;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using VPet_Simulator.Windows.Interface;

namespace VPet.Plugin.FaceDetect;

/// <summary>
/// 轮询 face-detect /latest → 情绪/疲劳持续达标 → audio /chat/care → Speaking TTS。
/// </summary>
public sealed class EmotionCareClient : IDisposable
{
    private readonly IMainWindow _mainWindow;
    private readonly HttpClient _http;
    private readonly JsonSerializerOptions _jsonOptions;

    private CancellationTokenSource? _cts;
    private Task? _loopTask;
    private int _failureCount;
    private bool _busyCare;

    /// <summary>当前连续命中的非 Neutral 场景。</summary>
    private string? _holdScene;
    private double _holdStartedUnix;
    /// <summary>本轮连续情绪是否已经说过（同一情绪连续出现不再说）。</summary>
    private bool _spokenForHoldScene;
    private double _lastGlobalSpeakUnix;
    private double _lastDiagUnix;

    public bool IsRunning => _cts is { IsCancellationRequested: false };

    public EmotionCareClient(IMainWindow mainWindow)
    {
        _mainWindow = mainWindow;
        _http = new HttpClient { Timeout = TimeSpan.FromSeconds(25) };
        // Python /latest 为 snake_case；必须用 SnakeCaseLower，否则 FacesCount 永远是 0
        _jsonOptions = new JsonSerializerOptions
        {
            PropertyNameCaseInsensitive = true,
            PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        };
    }

    public void Start()
    {
        if (IsRunning)
            return;

        ResetHold();
        _cts = new CancellationTokenSource();
        _loopTask = Task.Run(() => PollLoopAsync(_cts.Token));
        Console.WriteLine(
            "[VPet-FaceDetect] emotion care started "
            + $"(poll {FaceDetectConfig.PollIntervalMs}ms → /chat/care → SpeakExternal).");
    }

    public void Stop()
    {
        if (_cts == null)
            return;

        _cts.Cancel();
        _cts.Dispose();
        _cts = null;
        _loopTask = null;
        ResetHold();
        Console.WriteLine("[VPet-FaceDetect] emotion care stopped.");
    }

    private void ResetHold()
    {
        _holdScene = null;
        _holdStartedUnix = 0;
        _spokenForHoldScene = false;
    }

    private async Task PollLoopAsync(CancellationToken cancellationToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromMilliseconds(FaceDetectConfig.PollIntervalMs));

        while (!cancellationToken.IsCancellationRequested)
        {
            try
            {
                await TickAsync(cancellationToken).ConfigureAwait(false);
                _failureCount = 0;
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                _failureCount++;
                if (_failureCount == 1 || _failureCount % 40 == 0)
                    Console.WriteLine($"[VPet-FaceDetect] poll failed: {ex.Message}");
            }

            try
            {
                if (!await timer.WaitForNextTickAsync(cancellationToken).ConfigureAwait(false))
                    break;
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }
    }

    private async Task TickAsync(CancellationToken cancellationToken)
    {
        if (_busyCare)
            return;

        var latest = await _http.GetFromJsonAsync<LatestDto>(
            $"{FaceDetectConfig.FaceDetectBaseUrl}/latest",
            _jsonOptions,
            cancellationToken).ConfigureAwait(false);

        if (latest is not { Valid: true })
        {
            MaybeDiag(latest, "invalid");
            ResetHold();
            return;
        }

        // 有情绪标签即可；faces_count 反序列化失败时不再一票否决
        var hasSignal =
            latest.FacesCount > 0
            || !string.IsNullOrWhiteSpace(latest.TopEmotion)
            || !string.IsNullOrWhiteSpace(latest.DominantEmotion);
        if (!hasSignal)
        {
            MaybeDiag(latest, "no_face_or_emotion");
            ResetHold();
            return;
        }

        var now = UnixNow();
        var age = now - latest.Timestamp;
        if (age < 0)
            age = 0;
        // 放宽新鲜度，避免时钟/卡顿误判
        if (age > FaceDetectConfig.LatestMaxAgeSeconds)
        {
            MaybeDiag(latest, $"stale age={age:0.0}s");
            ResetHold();
            return;
        }

        var scene = ResolveScene(latest);
        MaybeDiag(latest, scene == null ? "neutral" : $"hold:{scene}");

        // Neutral / 无场景：结束本轮，允许下次再出现同情绪时再说
        if (scene == null)
        {
            if (_holdScene != null)
                Console.WriteLine($"[VPet-FaceDetect] emotion cleared (was {_holdScene})");
            ResetHold();
            return;
        }

        // 情绪切换（含 Neutral→Anger、Anger→Happy）：新开一轮，可说话
        if (_holdScene != scene)
        {
            _holdScene = scene;
            _holdStartedUnix = now;
            _spokenForHoldScene = false;
            Console.WriteLine(
                $"[VPet-FaceDetect] emotion edge → {scene} "
                + $"emotion={latest.TopEmotion}/{latest.DominantEmotion} "
                + $"p={latest.TopProbability:0.00}");
            return;
        }

        // 同一情绪连续：本轮已说过则跳过
        if (_spokenForHoldScene)
            return;

        var needHold = scene == "fatigue"
            ? FaceDetectConfig.FatigueHoldSeconds
            : FaceDetectConfig.EmotionHoldSeconds;

        if (now - _holdStartedUnix < needHold)
            return;

        if (now - _lastGlobalSpeakUnix < FaceDetectConfig.GlobalCooldownSeconds)
        {
            Console.WriteLine("[VPet-FaceDetect] care skipped: global cooldown");
            return;
        }

        _busyCare = true;
        try
        {
            var hint =
                $"{latest.TopEmotion} p={latest.TopProbability:0.00} "
                + $"fatigue={latest.FatigueScore:0.00}/{latest.FatigueLevel}";

            Console.WriteLine(
                $"[VPet-FaceDetect] → POST /chat/care scene={scene} (+memory context)");
            var reply = await RequestCareReplyAsync(scene, hint, cancellationToken)
                .ConfigureAwait(false);
            if (string.IsNullOrWhiteSpace(reply))
            {
                Console.WriteLine("[VPet-FaceDetect] /chat/care returned empty");
                return;
            }

            SpeakViaSpeakingPlugin(reply);
            _spokenForHoldScene = true;
            _lastGlobalSpeakUnix = UnixNow();
            Console.WriteLine($"[VPet-FaceDetect] care spoken scene={scene}: {reply}");
        }
        finally
        {
            _busyCare = false;
        }
    }

    private void MaybeDiag(LatestDto? latest, string reason)
    {
        var now = UnixNow();
        if (now - _lastDiagUnix < 5.0)
            return;
        _lastDiagUnix = now;
        if (latest == null)
        {
            Console.WriteLine($"[VPet-FaceDetect] diag: {reason}");
            return;
        }

        Console.WriteLine(
            $"[VPet-FaceDetect] diag {reason} valid={latest.Valid} faces={latest.FacesCount} "
            + $"emotion={latest.TopEmotion}/{latest.DominantEmotion} p={latest.TopProbability:0.00} "
            + $"fatigue={latest.FatigueScore:0.00}/{latest.FatigueLevel} ts={latest.Timestamp:0}");
    }

    private static double UnixNow() =>
        DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;

    /// <summary>
    /// 非 Neutral 情绪 → 对应 care scene；Neutral 时若疲劳高则 fatigue。
    /// </summary>
    private static string? ResolveScene(LatestDto latest)
    {
        var emotion = (latest.TopEmotion ?? latest.DominantEmotion ?? "").Trim();
        var p = latest.TopProbability;

        if (string.IsNullOrEmpty(emotion) ||
            emotion.Equals("Neutral", StringComparison.OrdinalIgnoreCase))
        {
            var fatigueHigh =
                string.Equals(latest.FatigueLevel, "high", StringComparison.OrdinalIgnoreCase)
                || latest.FatigueScore >= FaceDetectConfig.FatigueScoreThreshold;
            return fatigueHigh ? "fatigue" : null;
        }

        // 网页已显示该情绪时，概率字段偶发为 0（反序列化失败），仍放行
        if (p > 0 && p < FaceDetectConfig.MinEmotionProbability)
            return null;

        return emotion.ToLowerInvariant() switch
        {
            "happy" => "happy",
            "sad" => "sad",
            "surprise" => "surprise",
            "fear" => "fear",
            "disgust" => "disgust",
            "anger" => "anger",
            _ => null,
        };
    }

    private async Task<string?> RequestCareReplyAsync(
        string scene,
        string hint,
        CancellationToken cancellationToken)
    {
        var payload = JsonSerializer.Serialize(new { scene, hint });
        using var content = new StringContent(payload, Encoding.UTF8, "application/json");
        using var resp = await _http.PostAsync(
            $"{FaceDetectConfig.AudioBaseUrl}/chat/care",
            content,
            cancellationToken).ConfigureAwait(false);

        var body = await resp.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        if (!resp.IsSuccessStatusCode)
        {
            Console.WriteLine(
                $"[VPet-FaceDetect] /chat/care HTTP {(int)resp.StatusCode}: {body}");
            return null;
        }

        var dto = JsonSerializer.Deserialize<CareReplyDto>(body, _jsonOptions);
        if (dto is not { Ok: true } || string.IsNullOrWhiteSpace(dto.Reply))
        {
            Console.WriteLine(
                $"[VPet-FaceDetect] /chat/care failed: {dto?.Error ?? body}");
            return null;
        }

        return dto.Reply.Trim();
    }

    private void SpeakViaSpeakingPlugin(string text)
    {
        foreach (var plugin in _mainWindow.Plugins)
        {
            if (plugin.GetType().FullName != "VPet.Plugin.Speaking.SpeakingPlugin")
                continue;

            var method = plugin.GetType().GetMethod(
                "SpeakExternal",
                [typeof(string), typeof(bool), typeof(string)]);
            if (method != null)
            {
                method.Invoke(plugin, [text, false, "face-care"]);
                return;
            }

            method = plugin.GetType().GetMethod("SpeakExternal", [typeof(string)]);
            if (method != null)
            {
                method.Invoke(plugin, [text]);
                return;
            }
        }

        Console.WriteLine("[VPet-FaceDetect] SpeakingPlugin not found; bubble only.");
        _mainWindow.Main.Dispatcher.Invoke(() =>
            _mainWindow.Main.SayRnd(text, force: true));
    }

    public void Dispose()
    {
        Stop();
        _http.Dispose();
    }

    private sealed class LatestDto
    {
        [JsonPropertyName("valid")]
        public bool Valid { get; set; }

        [JsonPropertyName("timestamp")]
        public double Timestamp { get; set; }

        [JsonPropertyName("top_emotion")]
        public string? TopEmotion { get; set; }

        [JsonPropertyName("top_probability")]
        public double TopProbability { get; set; }

        [JsonPropertyName("dominant_emotion")]
        public string? DominantEmotion { get; set; }

        [JsonPropertyName("fatigue_score")]
        public double FatigueScore { get; set; }

        [JsonPropertyName("fatigue_level")]
        public string? FatigueLevel { get; set; }

        [JsonPropertyName("faces_count")]
        public int FacesCount { get; set; }
    }

    private sealed class CareReplyDto
    {
        [JsonPropertyName("ok")]
        public bool Ok { get; set; }

        [JsonPropertyName("reply")]
        public string? Reply { get; set; }

        [JsonPropertyName("error")]
        public string? Error { get; set; }
    }
}
