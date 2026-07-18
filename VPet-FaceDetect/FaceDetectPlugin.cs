using System.Diagnostics;
using System.Net.Http;
using LinePutScript.Localization.WPF;
using VPet_Simulator.Windows.Interface;
using ToolBar = VPet_Simulator.Core.ToolBar;

namespace VPet.Plugin.FaceDetect;

/// <summary>
/// 人脸检测桥接插件：对接 face-detect-local Python 后端（默认 http://127.0.0.1:8000）。
/// </summary>
public sealed class FaceDetectPlugin : MainPlugin
{
    private const string BaseUrl = "http://127.0.0.1:8000";
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(3) };

    public FaceDetectPlugin(IMainWindow mainwin) : base(mainwin) { }

    public override string PluginName => "VPet-FaceDetect";

    public override void LoadDIY()
    {
        MW.Main.ToolBar.AddMenuButton(ToolBar.MenuType.DIY, "人脸检测状态".Translate(), CheckStatus);
        MW.Main.ToolBar.AddMenuButton(ToolBar.MenuType.DIY, "打开人脸检测页".Translate(), OpenTestPage);
    }

    private void CheckStatus()
    {
        Task.Run(async () =>
        {
            try
            {
                var json = await _http.GetStringAsync($"{BaseUrl}/health").ConfigureAwait(false);
                MW.Main.Dispatcher.Invoke(() =>
                    MW.Main.SayRnd($"人脸检测服务在线。{json}".Translate(), force: true));
            }
            catch (Exception ex)
            {
                MW.Main.Dispatcher.Invoke(() =>
                    MW.Main.SayRnd(
                        ("人脸检测服务未启动: ".Translate() + ex.Message +
                         "\n请先运行 face-detect-local/run_backend.bat"),
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
                FileName = $"{BaseUrl}/test-frontend/",
                UseShellExecute = true
            });
            MW.Main.SayRnd("已打开人脸检测测试页。".Translate(), force: true);
        }
        catch (Exception ex)
        {
            MW.Main.SayRnd(("无法打开测试页: ".Translate() + ex.Message), force: true);
        }
    }

    public override void EndGame()
    {
        _http.Dispose();
    }
}
