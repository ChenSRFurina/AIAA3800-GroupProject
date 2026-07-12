using System.IO;
using System.Net.WebSockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace VPet.Plugin.Speaking
{
    /// <summary>
    /// 讯飞语音合成 WebSocket 客户端（移植自 tts_ws_python3_demo）。
    /// </summary>
    public sealed class XunfeiTtsClient
    {
        private readonly string _appId;
        private readonly string _apiKey;
        private readonly string _apiSecret;
        private readonly string _vcn;

        public XunfeiTtsClient(string appId, string apiKey, string apiSecret, string vcn = "x4_yezi")
        {
            _appId = appId;
            _apiKey = apiKey;
            _apiSecret = apiSecret;
            _vcn = string.IsNullOrWhiteSpace(vcn) ? "x4_yezi" : vcn;
        }

        /// <summary>
        /// 合成语音，返回 MP3 字节（aue=lame，便于 MediaPlayer 直接播放）。
        /// </summary>
        public async Task<byte[]> SynthesizeAsync(string text, CancellationToken cancellationToken = default)
        {
            if (string.IsNullOrWhiteSpace(text))
                throw new ArgumentException("合成文本不能为空", nameof(text));

            var url = CreateAuthUrl();
            using var ws = new ClientWebSocket();
            await ws.ConnectAsync(new Uri(url), cancellationToken).ConfigureAwait(false);

            var request = new Dictionary<string, object>
            {
                ["common"] = new Dictionary<string, object> { ["app_id"] = _appId },
                ["business"] = new Dictionary<string, object>
                {
                    // lame = mp3，便于 VPet Main.PlayVoice 播放
                    ["aue"] = "lame",
                    ["sfl"] = 1,
                    ["auf"] = "audio/L16;rate=16000",
                    ["vcn"] = _vcn,
                    ["tte"] = "utf8"
                },
                ["data"] = new Dictionary<string, object>
                {
                    ["status"] = 2,
                    ["text"] = Convert.ToBase64String(Encoding.UTF8.GetBytes(text))
                }
            };

            var json = JsonSerializer.Serialize(request);
            var sendBuffer = Encoding.UTF8.GetBytes(json);
            await ws.SendAsync(new ArraySegment<byte>(sendBuffer), WebSocketMessageType.Text, true, cancellationToken)
                .ConfigureAwait(false);

            using var audio = new MemoryStream();
            var receiveBuffer = new byte[64 * 1024];
            string? lastError = null;

            while (ws.State == WebSocketState.Open)
            {
                using var messageStream = new MemoryStream();
                WebSocketReceiveResult result;
                do
                {
                    result = await ws.ReceiveAsync(new ArraySegment<byte>(receiveBuffer), cancellationToken)
                        .ConfigureAwait(false);
                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        await ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "done", cancellationToken)
                            .ConfigureAwait(false);
                        break;
                    }
                    messageStream.Write(receiveBuffer, 0, result.Count);
                } while (!result.EndOfMessage);

                if (result.MessageType == WebSocketMessageType.Close)
                    break;

                var payload = Encoding.UTF8.GetString(messageStream.ToArray());
                using var doc = JsonDocument.Parse(payload);
                var root = doc.RootElement;

                var code = root.GetProperty("code").GetInt32();
                if (code != 0)
                {
                    lastError = root.TryGetProperty("message", out var msg)
                        ? msg.GetString()
                        : $"code={code}";
                    break;
                }

                if (!root.TryGetProperty("data", out var data) || data.ValueKind == JsonValueKind.Null)
                    continue;

                if (data.TryGetProperty("audio", out var audioProp) && audioProp.ValueKind == JsonValueKind.String)
                {
                    var b64 = audioProp.GetString();
                    if (!string.IsNullOrEmpty(b64))
                    {
                        var chunk = Convert.FromBase64String(b64);
                        audio.Write(chunk, 0, chunk.Length);
                    }
                }

                if (data.TryGetProperty("status", out var statusProp) && statusProp.GetInt32() == 2)
                {
                    if (ws.State == WebSocketState.Open)
                    {
                        await ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "finished", cancellationToken)
                            .ConfigureAwait(false);
                    }
                    break;
                }
            }

            if (!string.IsNullOrEmpty(lastError))
                throw new InvalidOperationException($"讯飞 TTS 失败: {lastError}");

            if (audio.Length == 0)
                throw new InvalidOperationException("讯飞 TTS 未返回音频数据");

            return audio.ToArray();
        }

        /// <summary>
        /// 生成带鉴权参数的 WebSocket URL（与官方 Python demo 一致）。
        /// </summary>
        private string CreateAuthUrl()
        {
            const string host = "ws-api.xfyun.cn";
            const string path = "/v2/tts";
            var date = DateTime.UtcNow.ToString("r");

            var signatureOrigin =
                $"host: {host}\n" +
                $"date: {date}\n" +
                $"GET {path} HTTP/1.1";

            using var hmac = new HMACSHA256(Encoding.UTF8.GetBytes(_apiSecret));
            var signatureSha = hmac.ComputeHash(Encoding.UTF8.GetBytes(signatureOrigin));
            var signature = Convert.ToBase64String(signatureSha);

            var authorizationOrigin =
                $"api_key=\"{_apiKey}\", algorithm=\"hmac-sha256\", headers=\"host date request-line\", signature=\"{signature}\"";
            var authorization = Convert.ToBase64String(Encoding.UTF8.GetBytes(authorizationOrigin));

            var query =
                $"authorization={Uri.EscapeDataString(authorization)}" +
                $"&date={Uri.EscapeDataString(date)}" +
                $"&host={Uri.EscapeDataString(host)}";

            return $"wss://tts-api.xfyun.cn/v2/tts?{query}";
        }

        /// <summary>
        /// 从配置文件加载客户端。优先插件目录下的 xunfei.config，其次上级 AIAA3800/.env。
        /// </summary>
        public static XunfeiTtsClient FromConfigNearAssembly()
        {
            var dir = Path.GetDirectoryName(typeof(XunfeiTtsClient).Assembly.Location)
                      ?? AppContext.BaseDirectory;
            var candidates = new[]
            {
                Path.Combine(dir, "xunfei.config"),
                Path.GetFullPath(Path.Combine(dir, "..", "..", "..", "..", "..", ".env")),
                Path.GetFullPath(Path.Combine(dir, "..", "..", "..", "..", ".env")),
            };

            Dictionary<string, string>? kv = null;
            foreach (var path in candidates)
            {
                if (File.Exists(path))
                {
                    kv = ParseConfig(path);
                    break;
                }
            }

            if (kv == null)
                throw new FileNotFoundException("未找到讯飞配置文件 xunfei.config 或 .env");

            string Require(string key) =>
                kv.TryGetValue(key, out var v) && !string.IsNullOrWhiteSpace(v)
                    ? v.Trim().Trim('\'', '"')
                    : throw new InvalidOperationException($"配置缺少 {key}");

            kv.TryGetValue("XUNFEI_VCN", out var vcn);
            return new XunfeiTtsClient(
                Require("XUNFEI_APPID"),
                Require("XUNFEI_APIKey"),
                Require("XUNFEI_APISecret"),
                string.IsNullOrWhiteSpace(vcn) ? "x4_yezi" : vcn);
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
                var key = line[..idx].Trim();
                var value = line[(idx + 1)..].Trim();
                dict[key] = value;
            }
            return dict;
        }
    }
}
