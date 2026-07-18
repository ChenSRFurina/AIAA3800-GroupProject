using System.Net.Http;
using System.Text.Json;
using VPet_Simulator.Core;
using VPet_Simulator.Windows.Interface;

namespace VPet.Plugin.Gaze;

/// <summary>
/// 周期性读取 Python 视线服务，并通过 VPet 原生 IController 移动桌宠。
/// </summary>
public sealed class GazeTrackingClient : IDisposable
{
    // 8766：避免与 VPet-Speaking F5-TTS TCP:8765 冲突
    private const string Endpoint = "http://127.0.0.1:8766/gaze";
    private const int PollIntervalMs = 80;
    private const double ResponseFreshnessSeconds = 1.2;
    private const double DeadZonePixels = 8.0;
    private const double FollowGain = 0.16;
    private const double MaxStepPixels = 55.0;

    private readonly IMainWindow _mainWindow;
    private readonly HttpClient _httpClient;
    private readonly JsonSerializerOptions _jsonOptions;
    private CancellationTokenSource? _cts;
    private Task? _loopTask;
    private int _failureCount;

    public bool IsRunning => _cts is { IsCancellationRequested: false };

    public GazeTrackingClient(IMainWindow mainWindow)
    {
        _mainWindow = mainWindow;
        _httpClient = new HttpClient
        {
            Timeout = TimeSpan.FromMilliseconds(350)
        };
        _jsonOptions = new JsonSerializerOptions
        {
            PropertyNameCaseInsensitive = true
        };
    }

    public void Start()
    {
        if (IsRunning)
            return;

        _cts = new CancellationTokenSource();
        _loopTask = Task.Run(() => PollLoopAsync(_cts.Token));
        Console.WriteLine("[VPet-Gaze] gaze tracking started.");
    }

    public void Stop()
    {
        if (_cts == null)
            return;

        _cts.Cancel();
        _cts.Dispose();
        _cts = null;
        _loopTask = null;
        Console.WriteLine("[VPet-Gaze] gaze tracking stopped.");
    }

    private async Task PollLoopAsync(CancellationToken cancellationToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromMilliseconds(PollIntervalMs));

        while (!cancellationToken.IsCancellationRequested)
        {
            try
            {
                await ReadAndMoveAsync(cancellationToken).ConfigureAwait(false);
                _failureCount = 0;
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                _failureCount++;
                // 避免摄像头服务未启动时每 80 ms 刷屏。
                if (_failureCount == 1 || _failureCount % 50 == 0)
                    Console.WriteLine($"[VPet-Gaze] cannot read gaze service: {ex.Message}");
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

    private async Task ReadAndMoveAsync(CancellationToken cancellationToken)
    {
        using var response = await _httpClient.GetAsync(Endpoint, cancellationToken).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();

        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        var gaze = await JsonSerializer.DeserializeAsync<GazeData>(stream, _jsonOptions, cancellationToken)
            .ConfigureAwait(false);

        if (gaze is null || !gaze.Valid)
            return;

        var now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;
        if (gaze.Timestamp > 0 && now - gaze.Timestamp > ResponseFreshnessSeconds)
            return;

        MoveTowardNormalizedPoint(
            Math.Clamp(gaze.ScreenX, 0.03, 0.97),
            Math.Clamp(gaze.ScreenY, 0.05, 0.95));
    }

    private void MoveTowardNormalizedPoint(double screenX, double screenY)
    {
        IController controller = _mainWindow.Main.Core.Controller;

        // 这些距离均为当前激活屏幕上的 WPF 逻辑像素。
        double left = controller.GetWindowsDistanceLeft();
        double right = controller.GetWindowsDistanceRight();
        double up = controller.GetWindowsDistanceUp();
        double down = controller.GetWindowsDistanceDown();

        double horizontalTravel = Math.Max(0, left + right);
        double verticalTravel = Math.Max(0, up + down);
        if (horizontalTravel < 1 || verticalTravel < 1)
            return;

        double targetLeft = horizontalTravel * screenX;
        double targetTop = verticalTravel * screenY;

        double dxPixels = ApplyMovementLimits((targetLeft - left) * FollowGain);
        double dyPixels = ApplyMovementLimits((targetTop - up) * FollowGain);

        if (Math.Abs(dxPixels) < DeadZonePixels)
            dxPixels = 0;
        if (Math.Abs(dyPixels) < DeadZonePixels)
            dyPixels = 0;
        if (dxPixels == 0 && dyPixels == 0)
            return;

        // MoveWindows 内部还会乘 ZoomRatio，因此这里先除回去。
        double zoom = Math.Max(controller.ZoomRatio, 0.01);
        controller.MoveWindows(dxPixels / zoom, dyPixels / zoom);
    }

    private static double ApplyMovementLimits(double value) =>
        Math.Clamp(value, -MaxStepPixels, MaxStepPixels);

    public void Dispose()
    {
        Stop();
        _httpClient.Dispose();
    }
}
