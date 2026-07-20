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

    /// <summary>结果过期（秒）：测试页停推流后不再触发。</summary>
    public const double LatestMaxAgeSeconds = 5.0;

    /// <summary>主情绪概率下限（网页能看清情绪时通常已够）。</summary>
    public const double MinEmotionProbability = 0.22;

    /// <summary>情绪需连续保持多久才触发 LLM。</summary>
    public const double EmotionHoldSeconds = 1.2;

    /// <summary>疲劳分数阈值（与后端 high≈0.75 对齐，略放宽）。</summary>
    public const double FatigueScoreThreshold = 0.70;

    /// <summary>疲劳需连续保持多久。</summary>
    public const double FatigueHoldSeconds = 2.5;

    /// <summary>
    /// 任意两次说话最短间隔（防 Happy↔Anger 快速抖动刷屏）。
    /// 同一情绪连续出现本身不会重复说，见 EmotionCareClient 边沿触发。
    /// </summary>
    public const double GlobalCooldownSeconds = 8.0;
}
