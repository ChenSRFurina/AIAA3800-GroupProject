using LinePutScript.Localization.WPF;
using VPet_Simulator.Core;
using VPet_Simulator.Windows.Interface;
using ToolBar = VPet_Simulator.Core.ToolBar;

namespace VPet.Plugin.Gaze;

/// <summary>
/// VPet 视线跟随插件。Python 负责估计注视位置，本插件负责移动桌宠。
/// </summary>
public sealed class GazePlugin : MainPlugin
{
    private GazeTrackingClient? _trackingClient;

    public GazePlugin(IMainWindow mainwin) : base(mainwin) { }

    public override string PluginName => "VPet-Gaze";

    public override void LoadPlugin()
    {
        _trackingClient = new GazeTrackingClient(MW);
    }

    public override void LoadDIY()
    {
        MW.Main.ToolBar.AddMenuButton(
            ToolBar.MenuType.DIY,
            "启动视线跟随".Translate(),
            StartTracking);

        MW.Main.ToolBar.AddMenuButton(
            ToolBar.MenuType.DIY,
            "停止视线跟随".Translate(),
            StopTracking);
    }

    private void StartTracking()
    {
        if (_trackingClient == null)
            _trackingClient = new GazeTrackingClient(MW);

        if (_trackingClient.IsRunning)
        {
            MW.Main.SayRnd("视线跟随已经启动啦。".Translate(), force: true);
            return;
        }

        _trackingClient.Start();
        MW.Main.SayRnd(
            $"视线注视模式已启动：盯着一处约 {GazeConfig.FixationDurationSeconds:0.#} 秒我会走/爬过去哦。"
                .Translate(),
            force: true);
    }

    private void StopTracking()
    {
        if (_trackingClient?.IsRunning != true)
        {
            MW.Main.SayRnd("视线跟随当前没有运行。".Translate(), force: true);
            return;
        }

        _trackingClient.Stop();
        MW.Main.SayRnd("视线跟随已停止。".Translate(), force: true);
    }

    public override void EndGame()
    {
        _trackingClient?.Dispose();
        _trackingClient = null;
    }
}
