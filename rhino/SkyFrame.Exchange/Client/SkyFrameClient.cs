// HTTP client for spec §6. All calls are async and never touch UI state, so the
// Grasshopper components can fire them from the solver and expire on completion.

using System;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using SkyFrame.Exchange.Json;
using SkyFrame.Exchange.Model;

namespace SkyFrame.Exchange.Client
{
    public enum ApplyMode { Merge, Replace }

    public sealed class SkyFrameClient : IDisposable
    {
        public const int DefaultPort = 8600;
        public const string DefaultUrl = "http://127.0.0.1:8600";

        private readonly HttpClient _http;
        private readonly bool _ownsHttp;

        public SkyFrameClient(string baseUrl = DefaultUrl, HttpMessageHandler handler = null, TimeSpan? timeout = null)
        {
            BaseUrl = NormalizeBaseUrl(baseUrl);
            _http = handler == null ? new HttpClient() : new HttpClient(handler, false);
            _http.Timeout = timeout ?? TimeSpan.FromSeconds(120);
            _ownsHttp = true;
        }

        public string BaseUrl { get; }

        private static readonly System.Collections.Concurrent.ConcurrentDictionary<string, SkyFrameClient> Shared =
            new System.Collections.Concurrent.ConcurrentDictionary<string, SkyFrameClient>(StringComparer.OrdinalIgnoreCase);

        /// <summary>One long-lived client per base URL (HttpClient is meant to be reused).</summary>
        public static SkyFrameClient For(string url) => Shared.GetOrAdd(NormalizeBaseUrl(url), u => new SkyFrameClient(u));

        /// <summary>Accepts "8600", "127.0.0.1:8600", "http://localhost:8600/" ... → "http://host:port".</summary>
        public static string NormalizeBaseUrl(string url)
        {
            var u = (url ?? "").Trim();
            if (u.Length == 0) return DefaultUrl;
            if (int.TryParse(u, out var port)) return $"http://127.0.0.1:{port}";
            if (!u.StartsWith("http://", StringComparison.OrdinalIgnoreCase) && !u.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
                u = "http://" + u;
            return u.TrimEnd('/');
        }

        // ---------------- endpoints ----------------

        public async Task<StatusInfo> GetStatusAsync(CancellationToken ct = default)
            => StatusInfo.Parse(await SendAsync(HttpMethod.Get, "/api/exchange/status", null, ct).ConfigureAwait(false));

        public static JsonObject BuildPreviewBody(ExchangePayload exchange, JsonValue mapping = null)
        {
            var body = new JsonObject().Set("exchange", exchange.ToJson());
            if (mapping != null && !mapping.IsNull) body.Set("mapping", mapping);
            return body;
        }

        public static JsonObject BuildApplyBody(ExchangePayload exchange, ApplyMode mode, bool run, JsonValue mapping = null)
        {
            var body = BuildPreviewBody(exchange, mapping);
            body.Set("mode", mode == ApplyMode.Replace ? "replace" : "merge");
            body.Set("run", run);
            return body;
        }

        /// <summary>A mapping given by name (string) → JSON string; null/empty → omitted.</summary>
        public static JsonValue MappingByName(string name) => string.IsNullOrWhiteSpace(name) ? null : JsonValue.From(name.Trim());

        public async Task<PreviewResult> PreviewAsync(ExchangePayload exchange, JsonValue mapping = null, CancellationToken ct = default)
            => PreviewResult.Parse(await SendAsync(HttpMethod.Post, "/api/exchange/preview", BuildPreviewBody(exchange, mapping), ct).ConfigureAwait(false));

        public async Task<ApplyResult> ApplyAsync(ExchangePayload exchange, ApplyMode mode = ApplyMode.Merge, bool run = false,
                                                  JsonValue mapping = null, CancellationToken ct = default)
            => ApplyResult.Parse(await SendAsync(HttpMethod.Post, "/api/exchange/apply", BuildApplyBody(exchange, mode, run, mapping), ct).ConfigureAwait(false));

        public static string ResultsPath(string session, string resultCase, string what)
        {
            var sb = new StringBuilder("/api/exchange/results?");
            sb.Append("session=").Append(Uri.EscapeDataString(session ?? ""));
            sb.Append("&case=").Append(Uri.EscapeDataString(resultCase ?? ""));
            sb.Append("&what=").Append(Uri.EscapeDataString(what ?? ""));
            return sb.ToString();
        }

        public async Task<Results.ResultsResponse> GetResultsAsync(string session, string resultCase, string what, CancellationToken ct = default)
        {
            var json = await SendAsync(HttpMethod.Get, ResultsPath(session, resultCase, what), null, ct).ConfigureAwait(false);
            return Results.ResultsResponse.Parse(json, what);
        }

        /// <summary>POST /api/rhino/import with a server-side path → the converted exchange payload.</summary>
        public async Task<ExchangePayload> ImportRhinoAsync(string path, CancellationToken ct = default)
        {
            var json = await SendAsync(HttpMethod.Post, "/api/rhino/import", new JsonObject().Set("path", path), ct).ConfigureAwait(false);
            return ExchangePayload.FromJson(json["exchange"]);
        }

        public async Task<LinkInfo> LinkAsync(string path, JsonValue mapping = null, bool autoApply = false, CancellationToken ct = default)
        {
            var body = new JsonObject().Set("path", path);
            if (mapping != null && !mapping.IsNull) body.Set("mapping", mapping);
            body.Set("auto_apply", autoApply);
            return LinkInfo.Parse(await SendAsync(HttpMethod.Post, "/api/rhino/link", body, ct).ConfigureAwait(false));
        }

        public async Task<LinkInfo> GetLinkAsync(CancellationToken ct = default)
            => LinkInfo.Parse(await SendAsync(HttpMethod.Get, "/api/rhino/link", null, ct).ConfigureAwait(false));

        public Task UnlinkAsync(CancellationToken ct = default) => SendAsync(HttpMethod.Delete, "/api/rhino/link", null, ct);

        // ---------------- transport ----------------

        public async Task<JsonValue> SendAsync(HttpMethod method, string pathAndQuery, JsonValue body, CancellationToken ct)
        {
            using (var req = new HttpRequestMessage(method, BaseUrl + pathAndQuery))
            {
                if (body != null)
                    req.Content = new StringContent(body.ToJson(), Encoding.UTF8, "application/json");
                HttpResponseMessage resp;
                try
                {
                    resp = await _http.SendAsync(req, ct).ConfigureAwait(false);
                }
                catch (HttpRequestException ex)
                {
                    throw new SkyFrameApiException(0, $"Cannot reach SkyFrame at {BaseUrl} ({ex.InnerException?.Message ?? ex.Message}). Is the app running and is the port right?", null);
                }
                catch (TaskCanceledException) when (!ct.IsCancellationRequested)
                {
                    throw new SkyFrameApiException(0, $"SkyFrame at {BaseUrl} timed out", null);
                }
                using (resp)
                {
                    var text = resp.Content == null ? "" : await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
                    JsonValue json = null;
                    if (!string.IsNullOrWhiteSpace(text))
                    {
                        try { json = JsonValue.Parse(text); }
                        catch (JsonParseException) { json = null; }
                    }
                    if (!resp.IsSuccessStatusCode)
                    {
                        var msg = json?["error"]?.AsString() ?? json?["message"]?.AsString() ?? Truncate(text, 300);
                        throw new SkyFrameApiException((int)resp.StatusCode,
                            $"SkyFrame {method} {pathAndQuery.Split('?')[0]} failed ({(int)resp.StatusCode}): {msg}", text);
                    }
                    if (json == null && !string.IsNullOrWhiteSpace(text))
                        throw new SkyFrameApiException((int)resp.StatusCode, "SkyFrame returned a non-JSON response (wrong port / another app?)", text);
                    return json ?? new JsonObject();
                }
            }
        }

        private static string Truncate(string s, int n) => s == null ? "" : (s.Length <= n ? s : s.Substring(0, n) + "…");

        public void Dispose()
        {
            if (_ownsHttp) _http.Dispose();
        }
    }
}
