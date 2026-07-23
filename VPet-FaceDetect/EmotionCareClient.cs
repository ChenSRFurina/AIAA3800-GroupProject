using System.Net.Http;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Reflection;
using System.Linq;
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
    private readonly EmotionCareTuning _tuning;

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
    private readonly Queue<EmotionSnapshot> _emotionWindow = new();
    private SmoothedEmotion _smoothed = new();

    private sealed class EmotionSnapshot
    {
        public double Ts { get; set; }
        public string Emotion { get; set; } = "Neutral";
        public double Prob { get; set; }
        public double Fatigue { get; set; }
    }

    private sealed class EmotionWindowElement
    {
        public string Emotion { get; set; } = "neutral";
        public double StartTs { get; set; }
        public double EndTs { get; set; }
        public int Count { get; set; }
        public double ProbSum { get; set; }
        public double ProbPeak { get; set; }
        public double FatiguePeak { get; set; }
    }

    private sealed class SmoothedEmotion
    {
        public double Happy { get; set; }
        public double Sad { get; set; }
        public double Anger { get; set; }
        public double Fear { get; set; }
        public double Surprise { get; set; }
        public double Disgust { get; set; }
        public double Neutral { get; set; }
        public double Fatigue { get; set; }
    }

    public bool IsRunning => _cts is { IsCancellationRequested: false };

    public EmotionCareClient(IMainWindow mainWindow)
    {
        _mainWindow = mainWindow;
        _tuning = EmotionCareTuning.LoadNearAssembly();
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

    private void AddEmotionSample(LatestDto latest, double now)
    {
        var emo = (latest.TopEmotion ?? latest.DominantEmotion ?? "Neutral").Trim();
        if (string.IsNullOrWhiteSpace(emo))
            emo = "Neutral";
        var p = latest.TopProbability;
        if (p <= 0)
            p = emo.Equals("Neutral", StringComparison.OrdinalIgnoreCase) ? 1.0 : 0.35;

        _emotionWindow.Enqueue(new EmotionSnapshot
        {
            Ts = now,
            Emotion = emo,
            Prob = Math.Clamp(p, 0.0, 1.0),
            Fatigue = Math.Clamp(latest.FatigueScore, 0.0, 1.0),
        });

        while (_emotionWindow.Count > 0 && now - _emotionWindow.Peek().Ts > _tuning.EmotionWindowSeconds)
            _emotionWindow.Dequeue();

        UpdateSmoothed(_emotionWindow.Count > 0 ? _emotionWindow.Last() : null);
    }

    private void UpdateSmoothed(EmotionSnapshot? s)
    {
        if (s == null)
            return;
        const double alpha = 0.35;

        static double Apply(double bucket, double value, double alphaValue)
        {
            return alphaValue * value + (1.0 - alphaValue) * bucket;
        }

        var vals = new Dictionary<string, double>(StringComparer.OrdinalIgnoreCase)
        {
            ["happy"] = 0,
            ["sad"] = 0,
            ["anger"] = 0,
            ["fear"] = 0,
            ["surprise"] = 0,
            ["disgust"] = 0,
            ["neutral"] = 0,
        };
        if (vals.ContainsKey(s.Emotion))
            vals[s.Emotion] = s.Prob;
        else
            vals["neutral"] = Math.Max(vals["neutral"], 0.6);

        _smoothed.Happy = Apply(_smoothed.Happy, vals["happy"], alpha);
        _smoothed.Sad = Apply(_smoothed.Sad, vals["sad"], alpha);
        _smoothed.Anger = Apply(_smoothed.Anger, vals["anger"], alpha);
        _smoothed.Fear = Apply(_smoothed.Fear, vals["fear"], alpha);
        _smoothed.Surprise = Apply(_smoothed.Surprise, vals["surprise"], alpha);
        _smoothed.Disgust = Apply(_smoothed.Disgust, vals["disgust"], alpha);
        _smoothed.Neutral = Apply(_smoothed.Neutral, vals["neutral"], alpha);
        _smoothed.Fatigue = Apply(_smoothed.Fatigue, s.Fatigue, alpha);
    }

    private string BuildEmotionWindowSummary(double now)
    {
        if (_emotionWindow.Count == 0)
            return "window=empty";

        var items = _emotionWindow.ToArray();
        var span = Math.Max(0.1, now - items[0].Ts);
        var runs = BuildWindowElements(items);

        var selected = runs
            .Where(x => !string.Equals(x.Emotion, "neutral", StringComparison.OrdinalIgnoreCase))
            .TakeLast(Math.Max(1, _tuning.EmotionSummaryMaxElements))
            .ToList();
        if (selected.Count == 0)
        {
            selected = runs
                .TakeLast(Math.Max(1, _tuning.EmotionSummaryMaxElements))
                .ToList();
        }

        var parts = new List<string>();
        foreach (var e in selected)
        {
            var dur = Math.Max(0.1, e.EndTs - e.StartTs + FaceDetectConfig.PollIntervalMs / 1000.0);
            var avg = e.Count > 0 ? e.ProbSum / e.Count : 0.0;
            parts.Add($"{e.Emotion}:{avg:0.00}|peak={e.ProbPeak:0.00}|dur={dur:0.0}s");
        }

        return $"window={span:0.0}s;elems={string.Join(",", parts)};fatigue={_smoothed.Fatigue:0.00}";
    }

    private static List<EmotionWindowElement> BuildWindowElements(EmotionSnapshot[] items)
    {
        var runs = new List<EmotionWindowElement>();
        foreach (var it in items)
        {
            var name = NormalizeEmotionLabel(it.Emotion);
            if (runs.Count == 0 || !string.Equals(runs[^1].Emotion, name, StringComparison.OrdinalIgnoreCase))
            {
                runs.Add(new EmotionWindowElement
                {
                    Emotion = name,
                    StartTs = it.Ts,
                    EndTs = it.Ts,
                    Count = 1,
                    ProbSum = it.Prob,
                    ProbPeak = it.Prob,
                    FatiguePeak = it.Fatigue,
                });
                continue;
            }

            var cur = runs[^1];
            cur.EndTs = it.Ts;
            cur.Count += 1;
            cur.ProbSum += it.Prob;
            cur.ProbPeak = Math.Max(cur.ProbPeak, it.Prob);
            cur.FatiguePeak = Math.Max(cur.FatiguePeak, it.Fatigue);
        }
        return runs;
    }

    private bool ShouldTriggerCare(string scene, double now, out string triggerMode)
    {
        triggerMode = "none";
        if (_emotionWindow.Count == 0)
            return false;

        var items = _emotionWindow.ToArray();
        var last = items[^1];
        var runs = BuildWindowElements(items);
        var normalizedScene = NormalizeEmotionLabel(scene);
        var sceneCfg = GetSceneTuning(normalizedScene);

        if (normalizedScene == "fatigue")
        {
            var trailingHighDur = GetTrailingFatigueDuration(items, sceneCfg.SustainedThreshold);
            if (last.Fatigue >= sceneCfg.BurstThreshold)
            {
                triggerMode = "burst-fatigue";
                return true;
            }
            if (_smoothed.Fatigue >= sceneCfg.SustainedThreshold
                && trailingHighDur >= sceneCfg.SustainedDurationSeconds)
            {
                triggerMode = "sustained-fatigue";
                return true;
            }
            return false;
        }

        var active = runs.LastOrDefault(r => string.Equals(r.Emotion, normalizedScene, StringComparison.OrdinalIgnoreCase));
        if (active == null)
            return false;

        var activeDur = Math.Max(0.1, active.EndTs - active.StartTs + FaceDetectConfig.PollIntervalMs / 1000.0);
        var activeAvg = active.Count > 0 ? active.ProbSum / active.Count : 0.0;
        var latestMatch = string.Equals(NormalizeEmotionLabel(last.Emotion), normalizedScene, StringComparison.OrdinalIgnoreCase);
        var latestProb = latestMatch ? last.Prob : 0.0;

        if (latestProb >= sceneCfg.BurstThreshold
            && activeDur >= sceneCfg.BurstMinDurationSeconds)
        {
            triggerMode = "burst-emotion";
            return true;
        }

        var sustainedRuns = runs
            .Where(r => string.Equals(r.Emotion, normalizedScene, StringComparison.OrdinalIgnoreCase))
            .TakeLast(_tuning.EmotionSummaryMaxElements)
            .ToArray();
        var sustainedDur = sustainedRuns.Sum(r => Math.Max(0.1, r.EndTs - r.StartTs + FaceDetectConfig.PollIntervalMs / 1000.0));
        var sustainedCount = sustainedRuns.Sum(r => r.Count);
        var sustainedAvg = sustainedCount > 0 ? sustainedRuns.Sum(r => r.ProbSum) / sustainedCount : 0.0;

        if (sustainedAvg >= sceneCfg.SustainedThreshold
            && sustainedDur >= sceneCfg.SustainedDurationSeconds)
        {
            triggerMode = "sustained-emotion";
            return true;
        }

        // 兼容旧逻辑：两段式不满足时，仍允许较松的连续保持触发。
        var needHold = sceneCfg.LegacyHoldSeconds;
        if (now - _holdStartedUnix >= needHold)
        {
            triggerMode = "legacy-hold";
            return true;
        }

        // 若瞬态段落已经结束，但刚刚出现过高峰，也允许一次保守触发。
        if (active.ProbPeak >= sceneCfg.BurstThreshold && activeAvg >= sceneCfg.SustainedThreshold * 0.9)
        {
            triggerMode = "recent-peak";
            return true;
        }

        return false;
    }

    private static double GetTrailingFatigueDuration(EmotionSnapshot[] items, double threshold)
    {
        if (items.Length == 0)
            return 0.0;

        var endTs = items[^1].Ts;
        var startTs = endTs;
        for (var i = items.Length - 1; i >= 0; i--)
        {
            if (items[i].Fatigue < threshold)
                break;
            startTs = items[i].Ts;
        }
        return Math.Max(0.0, endTs - startTs + FaceDetectConfig.PollIntervalMs / 1000.0);
    }

    private EmotionSceneTuning GetSceneTuning(string scene)
    {
        if (_tuning.Scenes.TryGetValue(scene, out var cfg) && cfg != null)
            return cfg;

        return new EmotionSceneTuning
        {
            MinScore = _tuning.DefaultMinEmotionProbability,
            BurstThreshold = 0.42,
            BurstMinDurationSeconds = 0.25,
            SustainedThreshold = 0.18,
            SustainedDurationSeconds = 0.90,
            LegacyHoldSeconds = 0.80,
        };
    }

    private static string NormalizeEmotionLabel(string? emotion)
    {
        var key = (emotion ?? "neutral").Trim().ToLowerInvariant();
        return key switch
        {
            "happy" => "happy",
            "sad" => "sad",
            "anger" => "anger",
            "fear" => "fear",
            "surprise" => "surprise",
            "disgust" => "disgust",
            "fatigue" => "fatigue",
            _ => "neutral",
        };
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
        if (age > _tuning.LatestMaxAgeSeconds)
        {
            MaybeDiag(latest, $"stale age={age:0.0}s");
            ResetHold();
            return;
        }

        var scene = ResolveScene(latest);
        AddEmotionSample(latest, now);
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
        }

        // 同一情绪连续：本轮已说过则跳过
        if (_spokenForHoldScene)
            return;

        if (IsUserReplyPriorityActive())
        {
            Console.WriteLine("[VPet-FaceDetect] care skipped: user-reply-priority");
            return;
        }

        if (!ShouldTriggerCare(scene, now, out var triggerMode))
            return;

        if (now - _lastGlobalSpeakUnix < _tuning.GlobalCooldownSeconds)
        {
            Console.WriteLine("[VPet-FaceDetect] care skipped: global cooldown");
            return;
        }

        _busyCare = true;
        try
        {
            var hint =
                $"{latest.TopEmotion} p={latest.TopProbability:0.00} "
                + $"fatigue={latest.FatigueScore:0.00}/{latest.FatigueLevel} "
                + $"trigger={triggerMode}";
            var emotionSummary = BuildEmotionWindowSummary(now);
            var (interruptionContext, recentUserSpeech) = ReadSpeakingContext();

            Console.WriteLine(
                $"[VPet-FaceDetect] → POST /chat/care scene={scene} (+memory context)");
            var reply = await RequestCareReplyAsync(
                    scene,
                    hint,
                    emotionSummary,
                    interruptionContext,
                    recentUserSpeech,
                    cancellationToken)
                .ConfigureAwait(false);
            if (string.IsNullOrWhiteSpace(reply))
            {
                Console.WriteLine("[VPet-FaceDetect] /chat/care returned empty");
                return;
            }

            SpeakViaSpeakingPlugin(reply, scene);
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
    private string? ResolveScene(LatestDto latest)
    {
        var emotion = (latest.TopEmotion ?? latest.DominantEmotion ?? "").Trim();
        var p = latest.TopProbability;
        var fatigueCfg = GetSceneTuning("fatigue");

        if (string.IsNullOrEmpty(emotion) ||
            emotion.Equals("Neutral", StringComparison.OrdinalIgnoreCase))
        {
            var fatigueHigh =
                string.Equals(latest.FatigueLevel, "high", StringComparison.OrdinalIgnoreCase)
                || latest.FatigueScore >= fatigueCfg.MinScore;
            return fatigueHigh ? "fatigue" : null;
        }

        var scene = emotion.ToLowerInvariant() switch
        {
            "happy" => "happy",
            "sad" => "sad",
            "surprise" => "surprise",
            "fear" => "fear",
            "disgust" => "disgust",
            "anger" => "anger",
            _ => null,
        };

        if (scene == null)
            return null;

        // 网页已显示该情绪时，概率字段偶发为 0（反序列化失败），仍放行
        var minScore = GetSceneTuning(scene).MinScore;
        if (p > 0 && p < minScore)
            return null;

        return scene;
    }

    private async Task<string?> RequestCareReplyAsync(
        string scene,
        string hint,
        string emotionSummary,
        string? interruptionContext,
        string? recentUserSpeech,
        CancellationToken cancellationToken)
    {
        var payload = JsonSerializer.Serialize(new
        {
            scene,
            hint,
            emotion_window = emotionSummary,
            interruption_context = interruptionContext,
            recent_user_speech = recentUserSpeech,
        });
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

    private void SpeakViaSpeakingPlugin(string text, string scene)
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
                method.Invoke(plugin, [text, false, $"face-care:{scene}"]);
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

    private bool IsUserReplyPriorityActive()
    {
        foreach (var plugin in _mainWindow.Plugins)
        {
            if (plugin.GetType().FullName != "VPet.Plugin.Speaking.SpeakingPlugin")
                continue;

            var gate = plugin.GetType().GetMethod("IsUserReplyPriorityActive", BindingFlags.Public | BindingFlags.Instance);
            if (gate == null)
                return false;

            var v = gate.Invoke(plugin, null);
            return v is bool b && b;
        }

        return false;
    }

    public string GetLatestEmotionSummaryForUserSpeech()
    {
        var now = UnixNow();
        var summary = BuildEmotionWindowSummary(now);
        var scene = _holdScene ?? "neutral";
        return $"scene={scene};{summary}";
    }

    private (string? Interruption, string? RecentUserSpeech) ReadSpeakingContext()
    {
        foreach (var plugin in _mainWindow.Plugins)
        {
            if (plugin.GetType().FullName != "VPet.Plugin.Speaking.SpeakingPlugin")
                continue;

            var take = plugin.GetType().GetMethod("GetAndConsumeInterruptionContext", BindingFlags.Public | BindingFlags.Instance);
            var user = plugin.GetType().GetMethod("GetRecentUserSpeechHint", BindingFlags.Public | BindingFlags.Instance);
            var interruption = take?.Invoke(plugin, null) as string;
            var recent = user?.Invoke(plugin, null) as string;
            return (interruption, recent);
        }

        return (null, null);
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
