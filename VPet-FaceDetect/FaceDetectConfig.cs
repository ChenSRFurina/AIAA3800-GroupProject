namespace VPet.Plugin.FaceDetect;

/// <summary>情绪陪伴阈值（可按联调效果微调）。</summary>
public static class FaceDetectConfig
{
    /// <summary>
    /// 本机 face-detect 入口（local 推理 或 remote relay 都监听这里）。
    /// </summary>
    public const string FaceDetectBaseUrl = "http://127.0.0.1:8000";

    public const string AudioBaseUrl = "http://127.0.0.1:8010";

    /// <summary>轮询 /latest 间隔。</summary>
    public const int PollIntervalMs = 500;

    /// <summary>情绪窗口长度（秒），用于平滑后再交给 LLM 决策。</summary>
    public const double EmotionWindowSeconds = 4.0;

    /// <summary>情绪窗口最多保留多少个 summary 元素。</summary>
    public const int EmotionSummaryMaxElements = 5;

    /// <summary>结果过期（秒）：测试页停推流后不再触发。</summary>
    public const double LatestMaxAgeSeconds = 5.0;

    /// <summary>主情绪概率下限（网页能看清情绪时通常已够）。</summary>
    public const double MinEmotionProbability = 0.14;

    /// <summary>情绪需连续保持多久才触发 LLM。</summary>
    public const double EmotionHoldSeconds = 0.8;

    /// <summary>强瞬态触发：当前情绪峰值达到该阈值可快速触发。</summary>
    public const double EmotionBurstPeakProbability = 0.42;

    /// <summary>强瞬态触发最短持续（秒）。</summary>
    public const double EmotionBurstMinDurationSeconds = 0.25;

    /// <summary>弱持续触发：滑动窗口平均情绪占比阈值。</summary>
    public const double EmotionSustainedAvgProbability = 0.18;

    /// <summary>弱持续触发：滑动窗口累计持续时长（秒）。</summary>
    public const double EmotionSustainedDurationSeconds = 0.9;

    /// <summary>疲劳分数阈值（与后端 high≈0.75 对齐，略放宽）。</summary>
    public const double FatigueScoreThreshold = 0.62;

    /// <summary>疲劳强瞬态阈值。</summary>
    public const double FatigueBurstScoreThreshold = 0.78;

    /// <summary>疲劳弱持续均值阈值。</summary>
    public const double FatigueSustainedScoreThreshold = 0.56;

    /// <summary>疲劳弱持续最短时长（秒）。</summary>
    public const double FatigueSustainedDurationSeconds = 1.3;

    /// <summary>疲劳需连续保持多久。</summary>
    public const double FatigueHoldSeconds = 1.8;

    /// <summary>
    /// 任意两次说话最短间隔（防 Happy↔Anger 快速抖动刷屏）。
    /// 同一情绪连续出现本身不会重复说，见 EmotionCareClient 边沿触发。
    /// </summary>
    public const double GlobalCooldownSeconds = 6.0;
}
