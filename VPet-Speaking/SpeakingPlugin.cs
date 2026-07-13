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
    /// 本地 F5-TTS 语音合成插件：DIY「说话」按钮，优先走 Fast_generating 常驻服务以降低延迟。
    /// </summary>
    public class SpeakingPlugin : MainPlugin
    {
        private F5TtsClient? _f5;
        private XunfeiTtsClient? _xunfei;
        private bool _busy;

        public SpeakingPlugin(IMainWindow mainwin) : base(mainwin) { }

        /// <summary>必须与 info.lps 中 vupmod 名称一致。</summary>
        public override string PluginName => "VPet-Speaking";

        public override void LoadPlugin()
        {
            _f5 = F5TtsClient.FromConfigNearAssembly();

            try
            {
                _xunfei = XunfeiTtsClient.FromConfigNearAssembly();
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[VPet-Speaking] 讯飞配置未加载（本地 F5 优先，可忽略）: {ex.Message}");
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
        /// 按下「说话」：取测试样例 → 本地 F5 合成（失败再试讯飞）→ 气泡+语音。
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

            _f5 ??= F5TtsClient.FromConfigNearAssembly();
            _busy = true;
            MW.Main.ToolBar.Visibility = Visibility.Collapsed;

            Task.Run(async () =>
            {
                try
                {
                    var (audio, ext, engine) = await SynthesizeWithFallbackAsync(text).ConfigureAwait(false);
                    var path = Path.Combine(
                        GraphCore.CachePath,
                        "voice",
                        $"{engine}_{Math.Abs(text.GetHashCode()):X}.{ext}");
                    await File.WriteAllBytesAsync(path, audio).ConfigureAwait(false);
                    Console.WriteLine($"[VPet-Speaking] {engine} 合成完成 -> {path} ({audio.Length} bytes)");

                    MW.Main.Dispatcher.Invoke(() =>
                    {
                        MW.Main.SayRnd(text, force: true);
                        MW.Main.PlayVoice(new Uri(path));
                    });
                }
                catch (Exception ex)
                {
                    MW.Main.Dispatcher.Invoke(() =>
                    {
                        MessageBoxX.Show(
                            ("语音合成失败: ".Translate() + ex.Message +
                             "\n\n请先启动本地服务:\npython Local_model/F5-TTS/Fast_generating/start_server.py"),
                            "VPet-Speaking",
                            MessageBoxIcon.Error);
                        MW.Main.SayRnd(text, force: true);
                    });
                }
                finally
                {
                    _busy = false;
                }
            });
        }

        /// <summary>优先本地 F5；连不上再回退讯飞（若已配置）。</summary>
        private async Task<(byte[] Audio, string Ext, string Engine)> SynthesizeWithFallbackAsync(string text)
        {
            try
            {
                var wav = await _f5!.SynthesizeAsync(text).ConfigureAwait(false);
                return (wav, "wav", "f5");
            }
            catch (Exception f5Ex)
            {
                Console.WriteLine($"[VPet-Speaking] F5 失败，尝试讯飞回退: {f5Ex.Message}");

                if (_xunfei == null)
                {
                    try
                    {
                        _xunfei = XunfeiTtsClient.FromConfigNearAssembly();
                    }
                    catch
                    {
                        throw new InvalidOperationException(
                            f5Ex.Message + "\n（讯飞回退也不可用：未找到 xunfei.config）",
                            f5Ex);
                    }
                }

                var mp3 = await _xunfei.SynthesizeAsync(text).ConfigureAwait(false);
                return (mp3, "mp3", "xunfei");
            }
        }
    }
}
