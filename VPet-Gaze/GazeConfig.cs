namespace VPet.Plugin.Gaze;

/// <summary>
/// 视线跟随全局可调参数（改这里即可调试，无需翻业务逻辑）。
/// </summary>
public static class GazeConfig
{
    /// <summary>I-DT：同一处注视持续达到该秒数后，桌宠才开始以恒速移动过去。</summary>
    public static double FixationDurationSeconds = 3.0;

    /// <summary>
    /// I-DT 离散度阈值（屏幕归一化坐标）。
    /// 窗口内 max(Δx, Δy) 不超过该值视为仍在看同一点。
    /// </summary>
    public static double IdtDispersionThreshold = 0.15;

    /// <summary>I-DT 最少采样点数，过少不判定为注视。</summary>
    public static int IdtMinSampleCount = 8;

    /// <summary>确认注视后，桌宠恒速移动的速度（逻辑像素 / 秒）。</summary>
    public static double MoveSpeedPixelsPerSecond = 220.0;

    /// <summary>认为已到达注视点的距离阈值（逻辑像素）。</summary>
    public static double ArriveDistancePixels = 28.0;

    /// <summary>
    /// 到达后：当前注视点与锁定目标的距离（归一化）小于该值，视为仍在发呆。
    /// </summary>
    public static double DaydreamGazeDistance = 0.12;

    /// <summary>同一次发呆只触发一次说话；离开注视点后可再次触发。</summary>
    public static double DaydreamCooldownSeconds = 8.0;

    /// <summary>轮询 Python /gaze 的间隔（毫秒）。</summary>
    public static int PollIntervalMs = 80;

    /// <summary>视线数据新鲜度上限（秒）。</summary>
    public static double ResponseFreshnessSeconds = 1.2;

    /// <summary>
    /// 发呆时随机播放的台词（经 VPet-Speaking / F5-TTS 合成）。
    /// </summary>
    public static readonly string[] DaydreamLines =
    [
        "发呆这么久是累了吗，休息一下吧",
        "一直盯着这里……是在想什么有趣的事吗？",
        "嘿，发呆时间有点长啦，起来活动一下？",
        "看你这么专注，要不要先喝口水歇歇？",
    ];
}
