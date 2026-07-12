using System.IO;
using System.Windows;
using LinePutScript.Localization.WPF;
using Panuon.WPF.UI;
using VPet_Simulator.Core;
using VPet_Simulator.Windows.Interface;
using ToolBar = VPet_Simulator.Core.ToolBar;

namespace VPet.Plugin.Speaking
{
    /// <summary>
    /// 讯飞语音合成插件：在 DIY 菜单增加「说话」按钮，合成并播放测试样例语音。
    /// </summary>
    public class SpeakingPlugin : MainPlugin
    {
        private XunfeiTtsClient? _tts;
        private bool _busy;

        public SpeakingPlugin(IMainWindow mainwin) : base(mainwin) { }

        /// <summary>必须与 info.lps 中 vupmod 名称一致。</summary>
        public override string PluginName => "VPet-Speaking";

        public override void LoadPlugin()
        {
            try
            {
                _tts = XunfeiTtsClient.FromConfigNearAssembly();
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[VPet-Speaking] 加载讯飞配置失败: {ex.Message}");
            }

            var voiceDir = Path.Combine(GraphCore.CachePath, "voice");
            if (!Directory.Exists(voiceDir))
                Directory.CreateDirectory(voiceDir);
        }

        public override void LoadDIY()
        {
            MW.Main.ToolBar.AddMenuButton(ToolBar.MenuType.DIY, "说话".Translate(), SpeakTestSample);
        }

        /// <summary>
        /// 按下「说话」：取 get_message 测试样例 → 讯飞合成 → 气泡+语音。
        /// </summary>
        private void SpeakTestSample()
        {
            if (_busy)
                return;

            var text = GetMessage.get_message(GetMessage.TestSample);
            if (string.IsNullOrWhiteSpace(text))
            {
                MessageBoxX.Show("没有可合成的文本".Translate(), "VPet-Speaking");
                return;
            }

            if (_tts == null)
            {
                try
                {
                    _tts = XunfeiTtsClient.FromConfigNearAssembly();
                }
                catch (Exception ex)
                {
                    MessageBoxX.Show(
                        ("讯飞配置加载失败: ".Translate() + ex.Message),
                        "VPet-Speaking");
                    return;
                }
            }

            _busy = true;
            MW.Main.ToolBar.Visibility = Visibility.Collapsed;

            Task.Run(async () =>
            {
                try
                {
                    var audio = await _tts.SynthesizeAsync(text).ConfigureAwait(false);
                    var path = Path.Combine(
                        GraphCore.CachePath,
                        "voice",
                        $"xunfei_{Math.Abs(text.GetHashCode()):X}.mp3");
                    await File.WriteAllBytesAsync(path, audio).ConfigureAwait(false);

                    MW.Main.Dispatcher.Invoke(() =>
                    {
                        // SayRnd 会自动选用内置 GraphType.Say 动画
                        MW.Main.SayRnd(text, force: true);
                        MW.Main.PlayVoice(new Uri(path));
                    });
                }
                catch (Exception ex)
                {
                    MW.Main.Dispatcher.Invoke(() =>
                    {
                        MessageBoxX.Show(
                            ("语音合成失败: ".Translate() + ex.Message),
                            "VPet-Speaking",
                            MessageBoxIcon.Error);
                        // 即使合成失败，仍显示文字气泡 + 说话动画，便于调试
                        MW.Main.SayRnd(text, force: true);
                    });
                }
                finally
                {
                    _busy = false;
                }
            });
        }
    }
}
