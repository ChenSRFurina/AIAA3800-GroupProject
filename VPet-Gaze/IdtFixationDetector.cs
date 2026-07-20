namespace VPet.Plugin.Gaze;

/// <summary>
/// Identification by Dispersion Threshold (I-DT) 流式注视检测。
/// 窗口内视线离散度不超过阈值时累积时长；超过则收缩窗口。
/// </summary>
public sealed class IdtFixationDetector
{
    private readonly List<(double Time, double X, double Y)> _window = new();

    public void Reset() => _window.Clear();

    /// <summary>
    /// 喂入一个归一化屏幕注视点。
    /// </summary>
    /// <returns>是否已形成持续足够久的注视；centroid 为注视中心。</returns>
    public bool Update(
        double screenX,
        double screenY,
        double nowSeconds,
        out double centroidX,
        out double centroidY,
        out double durationSeconds)
    {
        _window.Add((nowSeconds, screenX, screenY));

        while (_window.Count > 1 && Dispersion(_window) > GazeConfig.IdtDispersionThreshold)
            _window.RemoveAt(0);

        // 丢弃过旧点，避免离散度碰巧很小时无限拉长
        while (_window.Count > 1 &&
               nowSeconds - _window[0].Time > GazeConfig.FixationDurationSeconds * 3.0)
        {
            _window.RemoveAt(0);
        }

        ComputeCentroid(_window, out centroidX, out centroidY);
        durationSeconds = _window.Count > 0 ? nowSeconds - _window[0].Time : 0.0;

        return _window.Count >= GazeConfig.IdtMinSampleCount
               && durationSeconds >= GazeConfig.FixationDurationSeconds
               && Dispersion(_window) <= GazeConfig.IdtDispersionThreshold;
    }

    /// <summary>当前窗口是否仍聚在某目标附近（发呆判定）。</summary>
    public bool IsStillNear(
        double targetX,
        double targetY,
        double nowSeconds,
        double maxDistance)
    {
        if (_window.Count < 3)
            return false;

        // 只用最近一小段时间
        var recent = _window.Where(p => nowSeconds - p.Time <= 0.6).ToList();
        if (recent.Count < 3)
            recent = _window.TakeLast(Math.Min(6, _window.Count)).ToList();

        ComputeCentroid(recent, out var cx, out var cy);
        var dist = Math.Sqrt(
            (cx - targetX) * (cx - targetX) + (cy - targetY) * (cy - targetY));
        return dist <= maxDistance
               && Dispersion(recent) <= GazeConfig.IdtDispersionThreshold * 1.5;
    }

    private static double Dispersion(IReadOnlyList<(double Time, double X, double Y)> points)
    {
        if (points.Count == 0)
            return 0;

        double minX = points[0].X, maxX = points[0].X;
        double minY = points[0].Y, maxY = points[0].Y;
        for (var i = 1; i < points.Count; i++)
        {
            minX = Math.Min(minX, points[i].X);
            maxX = Math.Max(maxX, points[i].X);
            minY = Math.Min(minY, points[i].Y);
            maxY = Math.Max(maxY, points[i].Y);
        }

        return Math.Max(maxX - minX, maxY - minY);
    }

    private static void ComputeCentroid(
        IReadOnlyList<(double Time, double X, double Y)> points,
        out double cx,
        out double cy)
    {
        if (points.Count == 0)
        {
            cx = 0.5;
            cy = 0.5;
            return;
        }

        double sx = 0, sy = 0;
        foreach (var p in points)
        {
            sx += p.X;
            sy += p.Y;
        }

        cx = sx / points.Count;
        cy = sy / points.Count;
    }
}
