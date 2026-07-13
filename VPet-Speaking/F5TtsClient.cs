using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;

namespace VPet.Plugin.Speaking
{
    /// <summary>
    /// 本地 F5-TTS Fast_generating 客户端。
    /// 连接 start_server.py 常驻服务（模型与参考音色已加载），低延迟合成 WAV。
    /// </summary>
    public sealed class F5TtsClient
    {
        private readonly string _host;
        private readonly int _port;
        private readonly int _nfeStep;
        private readonly int _timeoutMs;

        public F5TtsClient(string host = "127.0.0.1", int port = 8765, int nfeStep = 8, int timeoutMs = 30000)
        {
            _host = string.IsNullOrWhiteSpace(host) ? "127.0.0.1" : host.Trim();
            _port = port > 0 ? port : 8765;
            _nfeStep = nfeStep > 0 ? nfeStep : 8;
            _timeoutMs = timeoutMs > 0 ? timeoutMs : 30000;
        }

        public string Host => _host;
        public int Port => _port;
        public int NfeStep => _nfeStep;

        /// <summary>
        /// 合成语音，返回可直接给 MediaPlayer 播放的 WAV 字节。
        /// </summary>
        public async Task<byte[]> SynthesizeAsync(string text, CancellationToken cancellationToken = default)
        {
            if (string.IsNullOrWhiteSpace(text))
                throw new ArgumentException("合成文本不能为空", nameof(text));

            using var client = new TcpClient();
            using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeoutCts.CancelAfter(_timeoutMs);

            try
            {
                await client.ConnectAsync(_host, _port, timeoutCts.Token).ConfigureAwait(false);
            }
            catch (Exception ex) when (ex is SocketException or IOException or OperationCanceledException)
            {
                throw new InvalidOperationException(
                    $"无法连接本地 F5-TTS 服务 {_host}:{_port}。请先运行: python Local_model/F5-TTS/Fast_generating/start_server.py",
                    ex);
            }

            client.NoDelay = true;
            await using var stream = client.GetStream();

            var req = new Dictionary<string, object?>
            {
                ["cmd"] = "gen",
                ["text"] = text.Trim(),
                ["nfe_step"] = _nfeStep,
            };
            await SendJsonAsync(stream, req, timeoutCts.Token).ConfigureAwait(false);

            using var metaDoc = await RecvJsonAsync(stream, timeoutCts.Token).ConfigureAwait(false);
            var root = metaDoc.RootElement;
            if (!root.TryGetProperty("ok", out var okProp) || !okProp.GetBoolean())
            {
                var err = root.TryGetProperty("error", out var e) ? e.GetString() : "未知错误";
                throw new InvalidOperationException($"F5-TTS 合成失败: {err}");
            }

            var sr = root.TryGetProperty("sr", out var srProp) ? srProp.GetInt32() : 24000;
            var pcm = await RecvBytesAsync(stream, timeoutCts.Token).ConfigureAwait(false);
            if (pcm.Length == 0)
                throw new InvalidOperationException("F5-TTS 未返回音频数据");

            return Float32PcmToWav(pcm, sr);
        }

        /// <summary>探测服务是否就绪。</summary>
        public async Task<bool> PingAsync(CancellationToken cancellationToken = default)
        {
            try
            {
                using var client = new TcpClient();
                using var cts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
                cts.CancelAfter(Math.Min(_timeoutMs, 3000));
                await client.ConnectAsync(_host, _port, cts.Token).ConfigureAwait(false);
                client.NoDelay = true;
                await using var stream = client.GetStream();
                await SendJsonAsync(stream, new Dictionary<string, object?> { ["cmd"] = "ping" }, cts.Token)
                    .ConfigureAwait(false);
                using var doc = await RecvJsonAsync(stream, cts.Token).ConfigureAwait(false);
                return doc.RootElement.TryGetProperty("ok", out var ok) && ok.GetBoolean();
            }
            catch
            {
                return false;
            }
        }

        /// <summary>
        /// 从插件旁 f5tts.config 加载；不存在则使用默认 127.0.0.1:8765。
        /// </summary>
        public static F5TtsClient FromConfigNearAssembly()
        {
            var dir = Path.GetDirectoryName(typeof(F5TtsClient).Assembly.Location)
                      ?? AppContext.BaseDirectory;
            var path = Path.Combine(dir, "f5tts.config");
            var host = "127.0.0.1";
            var port = 8765;
            var nfe = 8;
            var timeout = 30000;

            if (File.Exists(path))
            {
                var kv = ParseConfig(path);
                if (kv.TryGetValue("F5TTS_HOST", out var h) && !string.IsNullOrWhiteSpace(h))
                    host = h.Trim().Trim('\'', '"');
                if (kv.TryGetValue("F5TTS_PORT", out var p) && int.TryParse(p, out var portVal))
                    port = portVal;
                if (kv.TryGetValue("F5TTS_NFE_STEP", out var n) && int.TryParse(n, out var nfeVal))
                    nfe = nfeVal;
                if (kv.TryGetValue("F5TTS_TIMEOUT_MS", out var t) && int.TryParse(t, out var timeoutVal))
                    timeout = timeoutVal;
            }

            return new F5TtsClient(host, port, nfe, timeout);
        }

        private static async Task SendJsonAsync(
            NetworkStream stream,
            Dictionary<string, object?> obj,
            CancellationToken cancellationToken)
        {
            var json = JsonSerializer.Serialize(obj);
            var payload = Encoding.UTF8.GetBytes(json);
            var header = BitConverter.GetBytes(payload.Length); // little-endian on Windows
            await stream.WriteAsync(header, cancellationToken).ConfigureAwait(false);
            await stream.WriteAsync(payload, cancellationToken).ConfigureAwait(false);
        }

        private static async Task<JsonDocument> RecvJsonAsync(NetworkStream stream, CancellationToken cancellationToken)
        {
            var payload = await RecvBytesAsync(stream, cancellationToken).ConfigureAwait(false);
            return JsonDocument.Parse(payload);
        }

        private static async Task<byte[]> RecvBytesAsync(NetworkStream stream, CancellationToken cancellationToken)
        {
            var header = await ReadExactAsync(stream, 4, cancellationToken).ConfigureAwait(false);
            var length = BitConverter.ToInt32(header, 0);
            if (length < 0 || length > 64 * 1024 * 1024)
                throw new InvalidOperationException($"非法数据长度: {length}");
            return await ReadExactAsync(stream, length, cancellationToken).ConfigureAwait(false);
        }

        private static async Task<byte[]> ReadExactAsync(NetworkStream stream, int count, CancellationToken cancellationToken)
        {
            var buffer = new byte[count];
            var offset = 0;
            while (offset < count)
            {
                var read = await stream.ReadAsync(buffer.AsMemory(offset, count - offset), cancellationToken)
                    .ConfigureAwait(false);
                if (read == 0)
                    throw new IOException("连接已断开");
                offset += read;
            }
            return buffer;
        }

        /// <summary>float32 PCM LE → 16-bit mono WAV。</summary>
        private static byte[] Float32PcmToWav(byte[] floatPcm, int sampleRate)
        {
            var sampleCount = floatPcm.Length / 4;
            var dataBytes = sampleCount * 2;
            using var ms = new MemoryStream(44 + dataBytes);
            using var bw = new BinaryWriter(ms);

            bw.Write(Encoding.ASCII.GetBytes("RIFF"));
            bw.Write(36 + dataBytes);
            bw.Write(Encoding.ASCII.GetBytes("WAVE"));
            bw.Write(Encoding.ASCII.GetBytes("fmt "));
            bw.Write(16);          // PCM chunk size
            bw.Write((short)1);    // PCM
            bw.Write((short)1);    // mono
            bw.Write(sampleRate);
            bw.Write(sampleRate * 2); // byte rate
            bw.Write((short)2);    // block align
            bw.Write((short)16);   // bits
            bw.Write(Encoding.ASCII.GetBytes("data"));
            bw.Write(dataBytes);

            for (var i = 0; i + 3 < floatPcm.Length; i += 4)
            {
                var sample = BitConverter.ToSingle(floatPcm, i);
                if (sample > 1f) sample = 1f;
                if (sample < -1f) sample = -1f;
                bw.Write((short)Math.Round(sample * 32767.0));
            }

            return ms.ToArray();
        }

        private static Dictionary<string, string> ParseConfig(string path)
        {
            var dict = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (var raw in File.ReadAllLines(path))
            {
                var line = raw.Trim();
                if (string.IsNullOrEmpty(line) || line.StartsWith('#') || line.StartsWith(';'))
                    continue;
                var idx = line.IndexOf('=');
                if (idx <= 0) continue;
                dict[line[..idx].Trim()] = line[(idx + 1)..].Trim();
            }
            return dict;
        }
    }
}
