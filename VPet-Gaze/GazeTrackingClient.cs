using System.Diagnostics;
using System.Net.Http;
using System.Text.Json;
using VPet_Simulator.Core;
using VPet_Simulator.Windows.Interface;
using static VPet_Simulator.Core.GraphInfo;

namespace VPet.Plugin.Gaze;

/// <summary>
/// 轮询 Python 视线；I-DT 判定注视满阈值后，以恒速「走/爬」到目标；
/// 到达后若仍盯着该点则触发 Speaking 发呆台词。
/// </summary>
public sealed class GazeTrackingClient : IDisposable
{
    private const string Endpoint = "http://127.0.0.1:8766/gaze";

    private enum Phase
    {
        WaitingFixation,
        MovingToTarget,
        ArrivedHold,
    }

    private readonly IMainWindow _mainWindow;
    private readonly HttpClient _httpClient;
    private readonly JsonSerializerOptions _jsonOptions;
    private readonly IdtFixationDetector _idt = new();
    private readonly Stopwatch _frameClock = Stopwatch.StartNew();
    private readonly Random _rng = new();

    private CancellationTokenSource? _cts;
    private Task? _loopTask;
    private int _failureCount;

    private Phase _phase = Phase.WaitingFixation;
    private double _targetScreenX = 0.5;
    private double _targetScreenY = 0.5;
    private double _lastFrameSeconds;
    private double _lastDaydreamSpeakUnix;
    private bool _daydreamSpokenForThisVisit;
    private bool _walkAnimStarted;

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

        ResetSession();
        _cts = new CancellationTokenSource();
        _loopTask = Task.Run(() => PollLoopAsync(_cts.Token));
        Console.WriteLine(
            $"[VPet-Gaze] started (I-DT {GazeConfig.FixationDurationSeconds:0.#}s → walk → daydream TTS).");
    }

    public void Stop()
    {
        if (_cts == null)
            return;

        _cts.Cancel();
        _cts.Dispose();
        _cts = null;
        _loopTask = null;
        StopWalkAnimation();
        Console.WriteLine("[VPet-Gaze] gaze tracking stopped.");
    }

    private void ResetSession()
    {
        _idt.Reset();
        _phase = Phase.WaitingFixation;
        _daydreamSpokenForThisVisit = false;
        _walkAnimStarted = false;
        _lastFrameSeconds = _frameClock.Elapsed.TotalSeconds;
    }

    private async Task PollLoopAsync(CancellationToken cancellationToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromMilliseconds(GazeConfig.PollIntervalMs));

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

    private async Task TickAsync(CancellationToken cancellationToken)
    {
        using var response = await _httpClient.GetAsync(Endpoint, cancellationToken).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();

        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        var gaze = await JsonSerializer.DeserializeAsync<GazeData>(stream, _jsonOptions, cancellationToken)
            .ConfigureAwait(false);

        if (gaze is null || !gaze.Valid)
            return;

        var nowUnix = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;
        if (gaze.Timestamp > 0 && nowUnix - gaze.Timestamp > GazeConfig.ResponseFreshnessSeconds)
            return;

        var sx = Math.Clamp(gaze.ScreenX, 0.03, 0.97);
        var sy = Math.Clamp(gaze.ScreenY, 0.05, 0.95);
        var now = _frameClock.Elapsed.TotalSeconds;
        var dt = Math.Clamp(now - _lastFrameSeconds, 0.001, 0.25);
        _lastFrameSeconds = now;

        var fixating = _idt.Update(sx, sy, now, out var cx, out var cy, out var fixDur);

        switch (_phase)
        {
            case Phase.WaitingFixation:
                if (fixating)
                {
                    _targetScreenX = cx;
                    _targetScreenY = cy;
                    _phase = Phase.MovingToTarget;
                    _daydreamSpokenForThisVisit = false;
                    _walkAnimStarted = false;
                    Console.WriteLine(
                        $"[VPet-Gaze] I-DT fixation {fixDur:0.00}s @ ({cx:0.00},{cy:0.00}) → walk");
                    EnsureWalkAnimation();
                }
                break;

            case Phase.MovingToTarget:
                EnsureWalkAnimation();
                if (MoveConstantSpeedToward(_targetScreenX, _targetScreenY, dt))
                {
                    _phase = Phase.ArrivedHold;
                    StopWalkAnimation();
                    Console.WriteLine("[VPet-Gaze] arrived at fixation target.");

                    TrySpeakDaydreamIfStillStaring(sx, sy, nowUnix);
                }
                break;

            case Phase.ArrivedHold:
                // 到达后短暂停留；若仍盯着则说话；离开注视点后重新等待下一次 I-DT
                if (!_daydreamSpokenForThisVisit)
                    TrySpeakDaydreamIfStillStaring(sx, sy, nowUnix);

                var stillNear = _idt.IsStillNear(
                    _targetScreenX,
                    _targetScreenY,
                    now,
                    GazeConfig.DaydreamGazeDistance);

                if (!stillNear && !fixating)
                {
                    _idt.Reset();
                    _phase = Phase.WaitingFixation;
                    _daydreamSpokenForThisVisit = false;
                }
                break;
        }
    }

    private void TrySpeakDaydreamIfStillStaring(double sx, double sy, double nowUnix)
    {
        if (_daydreamSpokenForThisVisit)
            return;

        if (nowUnix - _lastDaydreamSpeakUnix < GazeConfig.DaydreamCooldownSeconds)
            return;

        var dist = Math.Sqrt(
            (sx - _targetScreenX) * (sx - _targetScreenX)
            + (sy - _targetScreenY) * (sy - _targetScreenY));
        var still = dist <= GazeConfig.DaydreamGazeDistance
                    || _idt.IsStillNear(
                        _targetScreenX,
                        _targetScreenY,
                        _frameClock.Elapsed.TotalSeconds,
                        GazeConfig.DaydreamGazeDistance);

        if (!still)
            return;

        var lines = GazeConfig.DaydreamLines;
        if (lines.Length == 0)
            return;

        var line = lines[_rng.Next(lines.Length)];
        _daydreamSpokenForThisVisit = true;
        _lastDaydreamSpeakUnix = nowUnix;
        Console.WriteLine($"[VPet-Gaze] daydream → Speaking: {line}");
        SpeakViaSpeakingPlugin(line);
    }

    private void SpeakViaSpeakingPlugin(string text)
    {
        // 优先走 VPet-Speaking 的公开入口（气泡 + F5/讯飞 TTS）
        foreach (var plugin in _mainWindow.Plugins)
        {
            if (plugin.GetType().FullName != "VPet.Plugin.Speaking.SpeakingPlugin")
                continue;

            var method = plugin.GetType().GetMethod("SpeakExternal");
            if (method != null)
            {
                method.Invoke(plugin, [text]);
                return;
            }
        }

        // Speaking 未加载时至少弹出气泡
        _mainWindow.Main.Dispatcher.Invoke(() =>
            _mainWindow.Main.SayRnd(text, force: true));
    }

    /// <summary>
    /// 恒速移向目标。返回 true 表示已到达。
    /// </summary>
    private bool MoveConstantSpeedToward(double screenX, double screenY, double dt)
    {
        IController controller = _mainWindow.Main.Core.Controller;

        double left = controller.GetWindowsDistanceLeft();
        double right = controller.GetWindowsDistanceRight();
        double up = controller.GetWindowsDistanceUp();
        double down = controller.GetWindowsDistanceDown();

        double horizontalTravel = Math.Max(0, left + right);
        double verticalTravel = Math.Max(0, up + down);
        if (horizontalTravel < 1 || verticalTravel < 1)
            return true;

        double targetLeft = horizontalTravel * screenX;
        double targetTop = verticalTravel * screenY;
        double dx = targetLeft - left;
        double dy = targetTop - up;
        double dist = Math.Sqrt(dx * dx + dy * dy);

        if (dist <= GazeConfig.ArriveDistancePixels)
            return true;

        double step = GazeConfig.MoveSpeedPixelsPerSecond * dt;
        double scale = Math.Min(1.0, step / dist);
        double zoom = Math.Max(controller.ZoomRatio, 0.01);

        // MoveWindows 内部还会乘 ZoomRatio
        controller.MoveWindows(dx * scale / zoom, dy * scale / zoom);
        return false;
    }

    /// <summary>
    /// 播放原生 Move（走/爬类）动画作视觉跟随，不启用 MoveTimer（位移由本类恒速控制）。
    /// </summary>
    private void EnsureWalkAnimation()
    {
        if (_walkAnimStarted)
            return;

        _walkAnimStarted = true;
        _mainWindow.Main.Dispatcher.Invoke(() =>
        {
            var main = _mainWindow.Main;
            // 关掉智能移动计时器，避免与恒速位移叠加速度
            main.MoveTimerSmartMove = false;
            try
            {
                main.MoveTimer.Stop();
            }
            catch
            {
                // ignore
            }

            if (main.DisplayType.Type == GraphType.Move)
                return;

            // GraphType.Move：桌宠自带的走动/爬行类动画（具体造型由宠物 MOD 的 move 配置决定）
            main.Display(GraphType.Move, AnimatType.A_Start, name =>
            {
                main.Display(name, AnimatType.B_Loop, _ => { });
            });
        });
    }

    private void StopWalkAnimation()
    {
        _walkAnimStarted = false;
        _mainWindow.Main.Dispatcher.Invoke(() =>
        {
            var main = _mainWindow.Main;
            main.MoveTimerSmartMove = false;
            try
            {
                main.MoveTimer.Stop();
            }
            catch
            {
                // ignore
            }

            if (main.DisplayType.Type == GraphType.Move)
                main.DisplayToNomal();
        });
    }

    public void Dispose()
    {
        Stop();
        _httpClient.Dispose();
    }
}
