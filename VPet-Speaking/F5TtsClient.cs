using System.Buffers.Binary;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;

namespace VPet.Plugin.Speaking
{
    /// <summary>
    /// 本地 F5-TTS 客户端：长连接复用，避免每次说话都重新 TCP 握手。
    /// </summary>
    public sealed class F5TtsClient : IDisposable
    {
        private readonly string _host;
        private readonly int _port;
        private readonly int _nfeStep;
        private readonly int _timeoutMs;
        private readonly object _ioLock = new();
        private TcpClient? _client;
        private NetworkStream? _stream;
        private bool _disposed;

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

        /// <summary>预热长连接（插件加载时调用）。</summary>
        public async Task WarmupAsync(CancellationToken cancellationToken = default)
        {
            await EnsureConnectedAsync(cancellationToken).ConfigureAwait(false);
            // ping 确认服务就绪，并让服务端线程保持住这条连接
            await SendReceivePingAsync(cancellationToken).ConfigureAwait(false);
        }

        public async Task<bool> PingAsync(CancellationToken cancellationToken = default)
        {
            try
            {
                await WarmupAsync(cancellationToken).ConfigureAwait(false);
                return true;
            }
            catch
            {
                return false;
            }
        }

        /// <summary>合成语音，返回 WAV 字节。</summary>
        public async Task<byte[]> SynthesizeAsync(string text, CancellationToken cancellationToken = default)
        {
            if (string.IsNullOrWhiteSpace(text))
                throw new ArgumentException("合成文本不能为空", nameof(text));

            var sw = Stopwatch.StartNew();
            using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeoutCts.CancelAfter(_timeoutMs);
            var ct = timeoutCts.Token;

            // 最多重试一次（长连接可能被服务端关掉）
            for (var attempt = 0; attempt < 2; attempt++)
            {
                try
                {
                    await EnsureConnectedAsync(ct).ConfigureAwait(false);
                    var stream = _stream ?? throw new InvalidOperationException("F5 连接未建立");

                    var req = new Dictionary<string, object?>
                    {
                        ["cmd"] = "gen",
                        ["text"] = text.Trim(),
                        ["nfe_step"] = _nfeStep,
                    };

                    byte[] wav;
                    lock (_ioLock)
                    {
                        // 同步 IO 保证长连接请求不交错；耗时主要在服务端 GPU
                        SendJson(stream, req);
                        using var metaDoc = RecvJson(stream);
                        var root = metaDoc.RootElement;
                        if (!root.TryGetProperty("ok", out var okProp) || !okProp.GetBoolean())
                        {
                            var err = root.TryGetProperty("error", out var e) ? e.GetString() : "未知错误";
                            throw new InvalidOperationException($"F5-TTS 合成失败: {err}");
                        }

                        var sr = root.TryGetProperty("sr", out var srProp) ? srProp.GetInt32() : 24000;
                        var inferMs = root.TryGetProperty("infer_ms", out var im) ? im.GetDouble() : -1;
                        var pcm = RecvBytes(stream);
                        if (pcm.Length == 0)
                            throw new InvalidOperationException("F5-TTS 未返回音频数据");

                        wav = Float32PcmToWav(pcm, sr);
                        Console.WriteLine(
                            $"[VPet-Speaking] F5 infer_ms={inferMs} client_ms={sw.ElapsedMilliseconds} nfe={_nfeStep} bytes={wav.Length}");
                    }

                    return wav;
                }
                catch (Exception ex) when (attempt == 0 && ex is IOException or SocketException or ObjectDisposedException)
                {
                    Console.WriteLine($"[VPet-Speaking] F5 连接中断，重连重试: {ex.Message}");
                    DropConnection();
                }
            }

            throw new InvalidOperationException(
                $"无法连接本地 F5-TTS 服务 {_host}:{_port}。请先运行: python Local_model/Fast_generating/start_server.py");
        }

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

        private async Task EnsureConnectedAsync(CancellationToken cancellationToken)
        {
            if (_client is { Connected: true } && _stream is not null)
                return;

            DropConnection();
            var client = new TcpClient { NoDelay = true };
            try
            {
                await client.ConnectAsync(_host, _port, cancellationToken).ConfigureAwait(false);
            }
            catch (Exception ex) when (ex is SocketException or IOException or OperationCanceledException)
            {
                client.Dispose();
                throw new InvalidOperationException(
                    $"无法连接本地 F5-TTS 服务 {_host}:{_port}。请先运行: python Local_model/Fast_generating/start_server.py",
                    ex);
            }

            _client = client;
            _stream = client.GetStream();
            Console.WriteLine($"[VPet-Speaking] F5 长连接已建立 {_host}:{_port}");
        }

        private async Task SendReceivePingAsync(CancellationToken cancellationToken)
        {
            await EnsureConnectedAsync(cancellationToken).ConfigureAwait(false);
            var stream = _stream!;
            lock (_ioLock)
            {
                SendJson(stream, new Dictionary<string, object?> { ["cmd"] = "ping" });
                using var doc = RecvJson(stream);
                var root = doc.RootElement;
                if (!root.TryGetProperty("ok", out var ok) || !ok.GetBoolean())
                    throw new InvalidOperationException("F5 ping 失败");

                var device = root.TryGetProperty("device", out var d) ? d.GetString() ?? "unknown" : "unknown";
                var gpu = root.TryGetProperty("gpu_name", out var g) ? g.GetString() ?? "-" : "-";
                var serverNfe = root.TryGetProperty("default_nfe_step", out var n) ? n.GetInt32() : -1;
                Console.WriteLine(
                    $"[VPet-Speaking] F5 ping ok: device={device}, gpu={gpu}, server_nfe={serverNfe}, client_nfe={_nfeStep}");

                if (!device.StartsWith("cuda", StringComparison.OrdinalIgnoreCase))
                {
                    Console.WriteLine("[VPet-Speaking] WARN: F5 当前未使用 CUDA。可检查 start_server.py --device cuda 与 F5TTS 环境 torch CUDA 版本。");
                }
            }
        }

        private void DropConnection()
        {
            try { _stream?.Dispose(); } catch { /* ignore */ }
            try { _client?.Dispose(); } catch { /* ignore */ }
            _stream = null;
            _client = null;
        }

        private static void SendJson(NetworkStream stream, Dictionary<string, object?> obj)
        {
            var payload = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(obj));
            Span<byte> header = stackalloc byte[4];
            BinaryPrimitives.WriteInt32LittleEndian(header, payload.Length);
            stream.Write(header);
            stream.Write(payload);
        }

        private static JsonDocument RecvJson(NetworkStream stream)
        {
            var payload = RecvBytes(stream);
            return JsonDocument.Parse(payload);
        }

        private static byte[] RecvBytes(NetworkStream stream)
        {
            Span<byte> header = stackalloc byte[4];
            ReadExact(stream, header);
            var length = BinaryPrimitives.ReadInt32LittleEndian(header);
            if (length < 0 || length > 64 * 1024 * 1024)
                throw new InvalidOperationException($"非法数据长度: {length}");
            var buffer = new byte[length];
            ReadExact(stream, buffer);
            return buffer;
        }

        private static void ReadExact(NetworkStream stream, Span<byte> buffer)
        {
            var offset = 0;
            while (offset < buffer.Length)
            {
                var read = stream.Read(buffer[offset..]);
                if (read == 0)
                    throw new IOException("连接已断开");
                offset += read;
            }
        }

        /// <summary>float32 PCM LE → 16-bit mono WAV（快速路径）。</summary>
        private static byte[] Float32PcmToWav(byte[] floatPcm, int sampleRate)
        {
            var sampleCount = floatPcm.Length / 4;
            var dataBytes = sampleCount * 2;
            var wav = new byte[44 + dataBytes];

            // header
            Encoding.ASCII.GetBytes("RIFF").CopyTo(wav.AsSpan(0, 4));
            BinaryPrimitives.WriteInt32LittleEndian(wav.AsSpan(4, 4), 36 + dataBytes);
            Encoding.ASCII.GetBytes("WAVE").CopyTo(wav.AsSpan(8, 4));
            Encoding.ASCII.GetBytes("fmt ").CopyTo(wav.AsSpan(12, 4));
            BinaryPrimitives.WriteInt32LittleEndian(wav.AsSpan(16, 4), 16);
            BinaryPrimitives.WriteInt16LittleEndian(wav.AsSpan(20, 2), 1);
            BinaryPrimitives.WriteInt16LittleEndian(wav.AsSpan(22, 2), 1);
            BinaryPrimitives.WriteInt32LittleEndian(wav.AsSpan(24, 4), sampleRate);
            BinaryPrimitives.WriteInt32LittleEndian(wav.AsSpan(28, 4), sampleRate * 2);
            BinaryPrimitives.WriteInt16LittleEndian(wav.AsSpan(32, 2), 2);
            BinaryPrimitives.WriteInt16LittleEndian(wav.AsSpan(34, 2), 16);
            Encoding.ASCII.GetBytes("data").CopyTo(wav.AsSpan(36, 4));
            BinaryPrimitives.WriteInt32LittleEndian(wav.AsSpan(40, 4), dataBytes);

            var floats = new float[sampleCount];
            Buffer.BlockCopy(floatPcm, 0, floats, 0, sampleCount * 4);
            var outOffset = 44;
            for (var i = 0; i < sampleCount; i++)
            {
                var sample = floats[i];
                if (sample > 1f) sample = 1f;
                else if (sample < -1f) sample = -1f;
                var s = (short)(sample * 32767f);
                BinaryPrimitives.WriteInt16LittleEndian(wav.AsSpan(outOffset, 2), s);
                outOffset += 2;
            }

            return wav;
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

        public void Dispose()
        {
            if (_disposed) return;
            _disposed = true;
            DropConnection();
        }
    }
}
