using System.Diagnostics;
using System.Net.Http;
using LinePutScript.Localization.WPF;
using VPet_Simulator.Windows.Interface;
using ToolBar = VPet_Simulator.Core.ToolBar;

namespace VPet.Plugin.FaceDetect;

/// <summary>
/// 人脸检测桥接：轮询本机 face-detect（:8000）的 /latest。
/// 推流由浏览器测试页完成（start-all 会自动打开）。
/// </summary>
public sealed class FaceDetectPlugin : MainPlugin
{
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(3) };
    private EmotionCareClient? _careClient;

    public FaceDetectPlugin(IMainWindow mainwin) : base(mainwin) { }

    public override string PluginName => "VPet-FaceDetect";

    public override void LoadPlugin()
    {
        _careClient = new EmotionCareClient(MW);
        // 延迟自动启动情绪陪伴（避免用户忘记点 DIY）
        _ = Task.Run(async () =>
        {
            await Task.Delay(4000).ConfigureAwait(false);
            try
            {
                await _http.GetStringAsync($"{FaceDetectConfig.FaceDetectBaseUrl}/health")
                    .ConfigureAwait(false);
                if (_careClient is { IsRunning: false })
                {
                    _careClient.Start();
                    Console.WriteLine("[VPet-FaceDetect] auto-started emotion care");
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine(
                    $"[VPet-FaceDetect] auto-start skipped (face service offline): {ex.Message}");
            }
        });
    }

    public override void LoadDIY()
    {
        MW.Main.ToolBar.AddMenuButton(ToolBar.MenuType.DIY, "人脸检测状态".Translate(), CheckStatus);
        MW.Main.ToolBar.AddMenuButton(ToolBar.MenuType.DIY, "打开人脸检测页".Translate(), OpenTestPage);
        MW.Main.ToolBar.AddMenuButton(
            ToolBar.MenuType.DIY,
            "启动情绪陪伴".Translate(),
            StartEmotionCare);
        MW.Main.ToolBar.AddMenuButton(
            ToolBar.MenuType.DIY,
            "停止情绪陪伴".Translate(),
            StopEmotionCare);
    }

    private void StartEmotionCare()
    {
        _careClient ??= new EmotionCareClient(MW);

        if (_careClient.IsRunning)
        {
            MW.Main.SayRnd("情绪陪伴已经在运行啦。".Translate(), force: true);
            return;
        }

        Task.Run(async () =>
        {
            try
            {
                var health = await _http.GetStringAsync(
                    $"{FaceDetectConfig.FaceDetectBaseUrl}/health").ConfigureAwait(false);
                Console.WriteLine($"[VPet-FaceDetect] local endpoint health: {health}");
            }
            catch (Exception ex)
            {
                MW.Main.Dispatcher.Invoke(() =>
                    MW.Main.SayRnd(
                        ("本机人脸服务未启动: ".Translate() + ex.Message +
                         "\n请先 start-all.bat（会打开浏览器推流页）"),
                        force: true));
                return;
            }

            MW.Main.Dispatcher.Invoke(() =>
            {
                _careClient!.Start();
                MW.Main.SayRnd(
                    "情绪陪伴已启动：请保持浏览器人脸页开着并允许摄像头。"
                        .Translate(),
                    force: true);
            });
        });
    }

    private void StopEmotionCare()
    {
        if (_careClient?.IsRunning != true)
        {
            MW.Main.SayRnd("情绪陪伴当前没有运行。".Translate(), force: true);
            return;
        }

        _careClient.Stop();
        MW.Main.SayRnd("情绪陪伴已停止。".Translate(), force: true);
    }

    private void CheckStatus()
    {
        Task.Run(async () =>
        {
            try
            {
                var health = await _http.GetStringAsync(
                    $"{FaceDetectConfig.FaceDetectBaseUrl}/health").ConfigureAwait(false);
                string latestHint;
                try
                {
                    latestHint = await _http.GetStringAsync(
                        $"{FaceDetectConfig.FaceDetectBaseUrl}/latest").ConfigureAwait(false);
                }
                catch
                {
                    latestHint = "(无 /latest)";
                }

                var care = _careClient?.IsRunning == true ? "care=on" : "care=off";
                MW.Main.Dispatcher.Invoke(() =>
                    MW.Main.SayRnd(
                        $"本机 :8000 在线 {care}。{latestHint}".Translate(),
                        force: true));
                Console.WriteLine($"[VPet-FaceDetect] health={health} latest={latestHint}");
            }
            catch (Exception ex)
            {
                MW.Main.Dispatcher.Invoke(() =>
                    MW.Main.SayRnd(
                        ("本机人脸服务未启动: ".Translate() + ex.Message),
                        force: true));
            }
        });
    }

    private void OpenTestPage()
    {
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = $"{FaceDetectConfig.FaceDetectBaseUrl}/test-frontend/",
                UseShellExecute = true
            });
            MW.Main.SayRnd("已打开人脸检测推流页，请允许摄像头。".Translate(), force: true);
        }
        catch (Exception ex)
        {
            MW.Main.SayRnd(("无法打开测试页: ".Translate() + ex.Message), force: true);
        }
    }

    public override void EndGame()
    {
        _careClient?.Dispose();
        _careClient = null;
        _http.Dispose();
    }
}
