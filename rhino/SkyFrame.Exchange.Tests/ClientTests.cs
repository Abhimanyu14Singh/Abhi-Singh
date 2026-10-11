using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using SkyFrame.Exchange.Client;
using SkyFrame.Exchange.Json;
using SkyFrame.Exchange.Model;
using Xunit;

namespace SkyFrame.Exchange.Tests
{
    /// <summary>Tiny HttpListener stand-in for the SkyFrame Flask app (spec §6).</summary>
    internal sealed class StubServer : IDisposable
    {
        public sealed class Recorded
        {
            public string Method, Path, Query, Body, ContentType;
            public Dictionary<string, string> QueryArgs = new Dictionary<string, string>();
        }

        private readonly HttpListener _listener = new HttpListener();
        private readonly CancellationTokenSource _cts = new CancellationTokenSource();
        public ConcurrentQueue<Recorded> Requests { get; } = new ConcurrentQueue<Recorded>();
        public Func<Recorded, (int status, string body)> Handler { get; set; }
        public int Delay { get; set; }
        public string Url { get; }

        public StubServer(Func<Recorded, (int, string)> handler)
        {
            Handler = handler;
            var l = new TcpListener(IPAddress.Loopback, 0);
            l.Start();
            int port = ((IPEndPoint)l.LocalEndpoint).Port;
            l.Stop();
            Url = $"http://127.0.0.1:{port}";
            _listener.Prefixes.Add(Url + "/");
            _listener.Start();
            Task.Run(Loop);
        }

        private async Task Loop()
        {
            while (!_cts.IsCancellationRequested)
            {
                HttpListenerContext ctx;
                try { ctx = await _listener.GetContextAsync(); }
                catch { return; }
                var req = ctx.Request;
                string body;
                using (var rd = new StreamReader(req.InputStream, Encoding.UTF8)) body = rd.ReadToEnd();
                var rec = new Recorded
                {
                    Method = req.HttpMethod, Path = req.Url.AbsolutePath, Query = req.Url.Query, Body = body, ContentType = req.ContentType,
                };
                foreach (var k in req.QueryString.AllKeys) if (k != null) rec.QueryArgs[k] = req.QueryString[k];
                Requests.Enqueue(rec);
                if (Delay > 0) await Task.Delay(Delay);
                var (status, text) = Handler(rec);
                var bytes = Encoding.UTF8.GetBytes(text ?? "");
                ctx.Response.StatusCode = status;
                ctx.Response.ContentType = "application/json";
                ctx.Response.ContentLength64 = bytes.Length;
                try
                {
                    await ctx.Response.OutputStream.WriteAsync(bytes, 0, bytes.Length);
                    ctx.Response.Close();
                }
                catch { /* client went away */ }
            }
        }

        public void Dispose()
        {
            _cts.Cancel();
            try { _listener.Stop(); _listener.Close(); } catch { }
        }
    }

    public class ClientTests
    {
        private static (int, string) Route(StubServer.Recorded r)
        {
            switch (r.Path)
            {
                case "/api/exchange/status":
                    return (200, @"{""app"":""SkyFrame"",""version"":""1.22.0"",""model"":""Tower A"",
                        ""sessions"":[{""session"":""s1"",""objects"":12,""last_update"":""2026-10-11T10:00:00""}],
                        ""analysis"":{""has_results"":true}}");
                case "/api/exchange/preview":
                    return (200, @"{""summary"":{""create"":{""members"":5,""shells"":2,""joints"":3},""update"":{""members"":0,""shells"":0,""joints"":0},""delete"":{""members"":1,""shells"":0,""joints"":0}},
                        ""warnings"":[""column C3 is not vertical"", {""id"":""x"",""message"":""unknown section""}],
                        ""objects"":[{""id"":""a"",""role"":""column"",""section"":""W14X90"",""story"":""Story1"",""status"":""create""}]}");
                case "/api/exchange/apply":
                    return (200, @"{""report"":{""created"":{""members"":4,""shells"":1,""joints"":1},""updated"":{""members"":2},""deleted"":{""members"":0},""warnings"":[""merged 3 joints""]},
                        ""model_summary"":{""members"":40,""shells"":8}}");
                case "/api/exchange/results":
                    return (200, @"{""case"":""" + r.QueryArgs["case"] + @""",""units"":""SI"",""items"":{""a"":[0.001,0,0,0,0,0]}}");
                case "/api/rhino/import":
                    return (200, "{\"exchange\":" + Samples.Read("shells.json") + "}");
                case "/api/rhino/link":
                    return (200, @"{""path"":""C:/m/tower.3dm"",""mtime"":1760000000.5,""changed"":true}");
                case "/api/broken":
                    return (500, @"{""error"":""boom""}");
            }
            return (404, @"{""error"":""not found""}");
        }

        [Fact]
        public async Task StatusPreviewApplyResultsHitSpecPathsWithSpecBodies()
        {
            using (var server = new StubServer(Route))
            using (var client = new SkyFrameClient(server.Url))
            {
                var payload = Samples.Frames().Payload;

                var status = await client.GetStatusAsync();
                Assert.Equal("Tower A", status.Model);
                Assert.Equal("1.22.0", status.Version);
                Assert.True(status.HasResults);
                Assert.Equal(12, status.Sessions.Single().Objects);

                var preview = await client.PreviewAsync(payload, SkyFrameClient.MappingByName("ETABS-like"));
                Assert.Equal(10, preview.Summary.TotalCreate);
                Assert.Equal(1, preview.Summary.TotalDelete);
                Assert.Equal(new[] { "column C3 is not vertical", "x: unknown section" }, preview.Warnings);
                Assert.Equal("W14X90", preview.Objects.Single().Section);

                var apply = await client.ApplyAsync(payload, ApplyMode.Replace, run: true);
                Assert.Equal(6, apply.Report.Counts.TotalCreate);
                Assert.Equal(2, apply.Report.Counts.TotalUpdate);
                Assert.Equal("merged 3 joints", apply.Report.Warnings.Single());
                Assert.Equal(40, apply.ModelSummary["members"].AsInt());
                Assert.Contains("created: 4 members, 1 shells, 1 joints", apply.Report.ToText());

                var res = await client.GetResultsAsync("sess/1 a&b", "1.2D+1.6L", "displacements");
                Assert.Equal("1.2D+1.6L", res.Case);
                Assert.Equal(0.001, res.Displacements("a").Single().Values[0]);

                var reqs = server.Requests.ToArray();
                Assert.Equal(new[] { "GET", "POST", "POST", "GET" }, reqs.Select(r => r.Method));
                Assert.Equal(new[] { "/api/exchange/status", "/api/exchange/preview", "/api/exchange/apply", "/api/exchange/results" }, reqs.Select(r => r.Path));

                // preview body: {exchange, mapping}
                var pb = JsonValue.Parse(reqs[1].Body).AsObject();
                Assert.StartsWith("application/json", reqs[1].ContentType);
                Assert.Equal(new[] { "exchange", "mapping" }, pb.Keys.ToArray());
                Assert.True(JsonCompare.DeepEquals(Samples.ReadJson("frames.json"), pb["exchange"]));
                Assert.Equal("ETABS-like", pb["mapping"].AsString());

                // apply body: {exchange, mode, run}; mapping omitted when not given
                var ab = JsonValue.Parse(reqs[2].Body).AsObject();
                Assert.Equal(new[] { "exchange", "mode", "run" }, ab.Keys.ToArray());
                Assert.Equal("replace", ab["mode"].AsString());
                Assert.True(ab["run"].AsBool());
                Assert.True(JsonCompare.DeepEquals(Samples.ReadJson("frames.json"), ab["exchange"]));

                // results query string, URL-encoded
                Assert.Equal("sess/1 a&b", reqs[3].QueryArgs["session"]);
                Assert.Equal("1.2D+1.6L", reqs[3].QueryArgs["case"]);
                Assert.Equal("displacements", reqs[3].QueryArgs["what"]);
                Assert.Equal("", reqs[3].Body);
            }
        }

        [Fact]
        public async Task MergeIsTheDefaultMode()
        {
            using (var server = new StubServer(Route))
            using (var client = new SkyFrameClient(server.Url + "/"))
            {
                await client.ApplyAsync(Samples.Shells().Payload);
                server.Requests.TryDequeue(out var r);
                var body = JsonValue.Parse(r.Body);
                Assert.Equal("merge", body["mode"].AsString());
                Assert.False(body["run"].AsBool(true));
                Assert.True(JsonCompare.DeepEquals(Samples.ReadJson("shells.json"), body["exchange"]));
            }
        }

        [Fact]
        public async Task RhinoImportAndLinkEndpoints()
        {
            using (var server = new StubServer(Route))
            using (var client = new SkyFrameClient(server.Url))
            {
                var ex = await client.ImportRhinoAsync("C:/m/tower.3dm");
                Assert.Equal(4, ex.Objects.Count);
                var link = await client.LinkAsync("C:/m/tower.3dm", autoApply: true);
                Assert.True(link.Changed);
                await client.GetLinkAsync();
                await client.UnlinkAsync();
                var reqs = server.Requests.ToArray();
                Assert.Equal("/api/rhino/import", reqs[0].Path);
                Assert.Equal("C:/m/tower.3dm", JsonValue.Parse(reqs[0].Body)["path"].AsString());
                Assert.Equal(new[] { "POST", "GET", "DELETE" }, reqs.Skip(1).Select(r => r.Method));
                Assert.True(JsonValue.Parse(reqs[1].Body)["auto_apply"].AsBool());
            }
        }

        [Fact]
        public async Task ErrorsCarryServerMessage()
        {
            using (var server = new StubServer(Route))
            using (var client = new SkyFrameClient(server.Url))
            {
                var ex = await Assert.ThrowsAsync<SkyFrameApiException>(() => client.SendAsync(System.Net.Http.HttpMethod.Get, "/api/broken", null, default));
                Assert.Equal(500, ex.StatusCode);
                Assert.Contains("boom", ex.Message);
            }
            using (var dead = new SkyFrameClient("http://127.0.0.1:1", timeout: TimeSpan.FromSeconds(5)))
            {
                var ex = await Assert.ThrowsAsync<SkyFrameApiException>(() => dead.GetStatusAsync());
                Assert.Equal(0, ex.StatusCode);
                Assert.Contains("Cannot reach SkyFrame", ex.Message);
            }
        }

        [Theory]
        [InlineData("", "http://127.0.0.1:8600")]
        [InlineData("8765", "http://127.0.0.1:8765")]
        [InlineData("localhost:9000/", "http://localhost:9000")]
        [InlineData("http://127.0.0.1:5000/", "http://127.0.0.1:5000")]
        public void NormalizesUrls(string input, string expected) => Assert.Equal(expected, SkyFrameClient.NormalizeBaseUrl(input));

        [Fact]
        public async Task LiveSenderDebouncesAndCoalesces()
        {
            using (var server = new StubServer(Route))
            using (var client = new SkyFrameClient(server.Url))
            using (var sender = new LiveSender(() => client, TimeSpan.FromMilliseconds(200)))
            {
                var done = new SemaphoreSlim(0);
                sender.Completed += _ => done.Release();

                // a burst of 5 edits within the debounce window → exactly one request with the LAST payload
                for (int i = 0; i < 5; i++)
                {
                    var p = Samples.Frames().Payload;
                    p.Objects[0].Props.Section = "W14X" + (90 + i);
                    Assert.True(sender.Submit(new SendRequest { Payload = p }, immediate: false));
                    await Task.Delay(20);
                }
                Assert.True(await done.WaitAsync(5000));
                await Task.Delay(300);
                Assert.Single(server.Requests);
                server.Requests.TryPeek(out var r);
                Assert.Equal("W14X94", JsonValue.Parse(r.Body)["exchange"]["objects"][0]["props"]["section"].AsString());
                Assert.True(sender.LastOutcome.Ok);

                // identical payload is not re-sent unless forced
                var same = Samples.Frames().Payload;
                same.Objects[0].Props.Section = "W14X94";
                Assert.False(sender.Submit(new SendRequest { Payload = same }, immediate: false));
                Assert.True(sender.Submit(new SendRequest { Payload = same }, immediate: true, force: true));
                Assert.True(await done.WaitAsync(5000));
                Assert.Equal(2, server.Requests.Count);
            }
        }

        [Fact]
        public async Task LiveSenderNeverBlocksAndSendsNewestAfterInFlight()
        {
            using (var server = new StubServer(Route) { Delay = 400 })
            using (var client = new SkyFrameClient(server.Url))
            using (var sender = new LiveSender(() => client, TimeSpan.Zero))
            {
                var outcomes = new ConcurrentQueue<SendOutcome>();
                var done = new SemaphoreSlim(0);
                sender.Completed += o => { outcomes.Enqueue(o); done.Release(); };

                var sw = System.Diagnostics.Stopwatch.StartNew();
                var a = Samples.Frames().Payload;
                var b = Samples.Frames().Payload; b.Session = "b";
                var c = Samples.Frames().Payload; c.Session = "c";
                sender.Submit(new SendRequest { Payload = a, Preview = true }, immediate: true);
                sender.Submit(new SendRequest { Payload = b }, immediate: true);   // queued, then replaced
                sender.Submit(new SendRequest { Payload = c }, immediate: true);
                Assert.True(sw.ElapsedMilliseconds < 300, "Submit must not wait for the network");
                Assert.True(sender.Busy);

                Assert.True(await done.WaitAsync(5000));
                Assert.True(await done.WaitAsync(5000));
                var paths = server.Requests.Select(r => r.Path).ToArray();
                Assert.Equal(new[] { "/api/exchange/preview", "/api/exchange/apply" }, paths);
                Assert.Equal("c", JsonValue.Parse(server.Requests.Last().Body)["exchange"]["session"].AsString());
                Assert.NotNull(outcomes.First().Preview);
                Assert.NotNull(outcomes.Last().Apply);
            }
        }

        [Fact]
        public async Task FailedSendIsNotRetriedInALoop()
        {
            using (var dead = new SkyFrameClient("http://127.0.0.1:1", timeout: TimeSpan.FromSeconds(5)))
            using (var sender = new LiveSender(() => dead, TimeSpan.Zero))
            {
                var done = new SemaphoreSlim(0);
                sender.Completed += _ => done.Release();
                var req = new SendRequest { Payload = Samples.Frames().Payload };
                sender.Submit(req, immediate: false);
                Assert.True(await done.WaitAsync(10000));
                Assert.False(sender.LastOutcome.Ok);
                // the re-solve that reports the error submits the same payload again: ignored
                Assert.False(sender.Submit(new SendRequest { Payload = Samples.Frames().Payload }, immediate: false));
            }
        }

        [Fact]
        public async Task AsyncCacheFetchesOncePerKey()
        {
            var cache = new AsyncCache<int>();
            int calls = 0;
            var done = new SemaphoreSlim(0);
            Func<Task<int>> slow = async () => { Interlocked.Increment(ref calls); await Task.Delay(100); return calls; };

            Assert.True(cache.Request("k1", slow, () => done.Release()));
            Assert.False(cache.Request("k1", slow, () => done.Release()));   // in flight
            Assert.True(cache.Loading);
            Assert.True(await done.WaitAsync(5000));
            Assert.True(cache.IsCurrent("k1"));
            Assert.Equal(1, cache.Value);
            Assert.False(cache.Request("k1", slow, () => done.Release()));   // cached
            Assert.True(cache.Request("k2", () => Task.FromException<int>(new InvalidOperationException("x")), () => done.Release()));
            Assert.True(await done.WaitAsync(5000));
            Assert.True(cache.IsCurrent("k2"));
            Assert.IsType<InvalidOperationException>(cache.Error);
            Assert.Equal(1, calls);
        }
    }
}
