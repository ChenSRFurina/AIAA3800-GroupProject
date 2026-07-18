using System.Diagnostics;
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
    /// 本地 F5-TTS 语音合成插件：先出气泡，后台合成完立即播放，降低体感延迟。
    /// </summary>
    public class SpeakingPlugin : MainPlugin
    {
        private F5TtsClient? _f5;
        private XunfeiTtsClient? _xunfei;
        private bool _busy;

        public SpeakingPlugin(IMainWindow mainwin) : base(mainwin) { }

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

            // 后台预热长连接，避免第一次说话多一次握手
            _ = Task.Run(async () =>
            {
                try
                {
                    if (_f5 != null && await _f5.PingAsync().ConfigureAwait(false))
                        Console.WriteLine($"[VPet-Speaking] F5 预热成功 {_f5.Host}:{_f5.Port} nfe={_f5.NfeStep}");
                    else
                        Console.WriteLine("[VPet-Speaking] F5 服务未就绪（说话前请先 start_server.py）");
                }
                catch (Exception ex)
                {
                    Console.WriteLine($"[VPet-Speaking] F5 预热失败: {ex.Message}");
                }
            });
        }

        public override void LoadDIY()
        {
            MW.Main.ToolBar.AddMenuButton(ToolBar.MenuType.DIY, "说话".Translate(), SpeakTestSample);
        }

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

            // 体感优化：立刻出气泡+说话动画，不等待合成结束
            MW.Main.SayRnd(text, force: true);

            Task.Run(async () =>
            {
                var sw = Stopwatch.StartNew();
                try
                {
                    var (audio, ext, engine) = await SynthesizeWithFallbackAsync(text).ConfigureAwait(false);
                    var path = Path.Combine(
                        GraphCore.CachePath,
                        "voice",
                        $"{engine}_{DateTime.UtcNow.Ticks:X}.{ext}");
                    await File.WriteAllBytesAsync(path, audio).ConfigureAwait(false);
                    Console.WriteLine(
                        $"[VPet-Speaking] {engine} ready in {sw.ElapsedMilliseconds} ms -> {path} ({audio.Length} bytes)");

                    MW.Main.Dispatcher.Invoke(() =>
                    {
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
                    });
                }
                finally
                {
                    _busy = false;
                }
            });
        }

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
