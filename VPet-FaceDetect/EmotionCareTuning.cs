using System.Text.Json;
using System.IO;

namespace VPet.Plugin.FaceDetect;

public sealed class EmotionSceneTuning
{
    public double MinScore { get; set; }
    public double BurstThreshold { get; set; }
    public double BurstMinDurationSeconds { get; set; }
    public double SustainedThreshold { get; set; }
    public double SustainedDurationSeconds { get; set; }
    public double LegacyHoldSeconds { get; set; }
}

public sealed class EmotionCareTuning
{
    public double EmotionWindowSeconds { get; set; }
    public int EmotionSummaryMaxElements { get; set; }
    public double LatestMaxAgeSeconds { get; set; }
    public double GlobalCooldownSeconds { get; set; }
    public double DefaultMinEmotionProbability { get; set; }
    public Dictionary<string, EmotionSceneTuning> Scenes { get; set; } = new(StringComparer.OrdinalIgnoreCase);

    public static EmotionCareTuning LoadNearAssembly()
    {
        var tuning = CreateDefault();
        var asmDir = Path.GetDirectoryName(typeof(EmotionCareTuning).Assembly.Location)
                     ?? AppContext.BaseDirectory;

        var candidates = new[]
        {
            Path.Combine(asmDir, "emotion-care.config.json"),
            Path.GetFullPath(Path.Combine(asmDir, "..", "..", "..", "..", "..", "VPet-FaceDetect", "emotion-care.config.json")),
            Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "emotion-care.config.json")),
        };

        foreach (var path in candidates)
        {
            try
            {
                if (!File.Exists(path))
                    continue;

                var text = File.ReadAllText(path);
                var loaded = JsonSerializer.Deserialize<EmotionCareTuning>(text, new JsonSerializerOptions
                {
                    PropertyNameCaseInsensitive = true,
                    ReadCommentHandling = JsonCommentHandling.Skip,
                    AllowTrailingCommas = true,
                });
                if (loaded == null)
                    continue;

                MergeWithDefaults(loaded, tuning);
                return loaded;
            }
            catch (Exception ex) {
				Console.WriteLine("Exception" + ex.ToString());
            }
        }

        return tuning;
    }

    private static EmotionCareTuning CreateDefault()
    {
        var t = new EmotionCareTuning
        {
            EmotionWindowSeconds = 4.0,
            EmotionSummaryMaxElements = 5,
            LatestMaxAgeSeconds = 5.0,
            GlobalCooldownSeconds = 6.0,
            DefaultMinEmotionProbability = 0.14,
        };

        t.Scenes["happy"] = new EmotionSceneTuning
        {
            MinScore = 0.12,
            BurstThreshold = 0.42,
            BurstMinDurationSeconds = 0.25,
            SustainedThreshold = 0.18,
            SustainedDurationSeconds = 0.90,
            LegacyHoldSeconds = 0.80,
        };
        t.Scenes["sad"] = new EmotionSceneTuning
        {
            MinScore = 0.12,
            BurstThreshold = 0.40,
            BurstMinDurationSeconds = 0.25,
            SustainedThreshold = 0.17,
            SustainedDurationSeconds = 0.85,
            LegacyHoldSeconds = 0.75,
        };
        t.Scenes["anger"] = new EmotionSceneTuning
        {
            MinScore = 0.10,
            BurstThreshold = 0.36,
            BurstMinDurationSeconds = 0.20,
            SustainedThreshold = 0.16,
            SustainedDurationSeconds = 0.75,
            LegacyHoldSeconds = 0.65,
        };
        t.Scenes["fear"] = new EmotionSceneTuning
        {
            MinScore = 0.10,
            BurstThreshold = 0.35,
            BurstMinDurationSeconds = 0.20,
            SustainedThreshold = 0.16,
            SustainedDurationSeconds = 0.75,
            LegacyHoldSeconds = 0.65,
        };
        t.Scenes["surprise"] = new EmotionSceneTuning
        {
            MinScore = 0.10,
            BurstThreshold = 0.34,
            BurstMinDurationSeconds = 0.18,
            SustainedThreshold = 0.15,
            SustainedDurationSeconds = 0.65,
            LegacyHoldSeconds = 0.55,
        };
        t.Scenes["disgust"] = new EmotionSceneTuning
        {
            MinScore = 0.11,
            BurstThreshold = 0.36,
            BurstMinDurationSeconds = 0.22,
            SustainedThreshold = 0.16,
            SustainedDurationSeconds = 0.80,
            LegacyHoldSeconds = 0.70,
        };
        t.Scenes["fatigue"] = new EmotionSceneTuning
        {
            MinScore = 0.62,
            BurstThreshold = 0.78,
            BurstMinDurationSeconds = 0.25,
            SustainedThreshold = 0.56,
            SustainedDurationSeconds = 1.30,
            LegacyHoldSeconds = 1.80,
        };

        return t;
    }

    private static void MergeWithDefaults(EmotionCareTuning loaded, EmotionCareTuning defaults)
    {
        if (loaded.EmotionWindowSeconds <= 0)
            loaded.EmotionWindowSeconds = defaults.EmotionWindowSeconds;
        if (loaded.EmotionSummaryMaxElements <= 0)
            loaded.EmotionSummaryMaxElements = defaults.EmotionSummaryMaxElements;
        if (loaded.LatestMaxAgeSeconds <= 0)
            loaded.LatestMaxAgeSeconds = defaults.LatestMaxAgeSeconds;
        if (loaded.GlobalCooldownSeconds < 0)
            loaded.GlobalCooldownSeconds = defaults.GlobalCooldownSeconds;
        if (loaded.DefaultMinEmotionProbability <= 0)
            loaded.DefaultMinEmotionProbability = defaults.DefaultMinEmotionProbability;

        loaded.Scenes ??= new Dictionary<string, EmotionSceneTuning>(StringComparer.OrdinalIgnoreCase);

        foreach (var kv in defaults.Scenes)
        {
            if (!loaded.Scenes.TryGetValue(kv.Key, out var scene) || scene == null)
            {
                loaded.Scenes[kv.Key] = kv.Value;
                continue;
            }

            if (scene.MinScore <= 0)
                scene.MinScore = kv.Value.MinScore;
            if (scene.BurstThreshold <= 0)
                scene.BurstThreshold = kv.Value.BurstThreshold;
            if (scene.BurstMinDurationSeconds <= 0)
                scene.BurstMinDurationSeconds = kv.Value.BurstMinDurationSeconds;
            if (scene.SustainedThreshold <= 0)
                scene.SustainedThreshold = kv.Value.SustainedThreshold;
            if (scene.SustainedDurationSeconds <= 0)
                scene.SustainedDurationSeconds = kv.Value.SustainedDurationSeconds;
            if (scene.LegacyHoldSeconds <= 0)
                scene.LegacyHoldSeconds = kv.Value.LegacyHoldSeconds;
        }
    }
}
