// Response DTOs of spec §6. The spec fixes the top-level keys; the inner shape of
// "report" / "model_summary" is open, so parsing is tolerant and the raw JSON is
// always kept for display.

using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using SkyFrame.Exchange.Json;

namespace SkyFrame.Exchange.Client
{
    public sealed class SessionInfo
    {
        public string Session { get; set; }
        public int Objects { get; set; }
        public string LastUpdate { get; set; }
    }

    public sealed class StatusInfo
    {
        public string App { get; set; }
        public string Version { get; set; }
        public string Model { get; set; }
        public List<SessionInfo> Sessions { get; } = new List<SessionInfo>();
        public bool HasResults { get; set; }
        public JsonObject Raw { get; set; }

        public static StatusInfo Parse(JsonValue v)
        {
            var o = v as JsonObject ?? new JsonObject();
            var s = new StatusInfo
            {
                App = o["app"]?.AsString() ?? "",
                Version = o["version"]?.AsString() ?? "",
                Model = o["model"]?.AsString() ?? "",
                HasResults = o["analysis"]?["has_results"]?.AsBool() ?? false,
                Raw = o,
            };
            if (o["sessions"] is JsonArray ss)
                foreach (var x in ss)
                    s.Sessions.Add(new SessionInfo
                    {
                        Session = x["session"]?.AsString() ?? "",
                        Objects = x["objects"]?.AsInt() ?? 0,
                        LastUpdate = x["last_update"]?.AsString() ?? "",
                    });
            return s;
        }
    }

    /// <summary>create/update/delete counts per category ("members", "shells", "joints", ...).</summary>
    public sealed class ChangeCounts
    {
        public Dictionary<string, int> Create { get; } = new Dictionary<string, int>();
        public Dictionary<string, int> Update { get; } = new Dictionary<string, int>();
        public Dictionary<string, int> Delete { get; } = new Dictionary<string, int>();

        public int TotalCreate => Create.Values.Sum();
        public int TotalUpdate => Update.Values.Sum();
        public int TotalDelete => Delete.Values.Sum();

        internal static void Fill(Dictionary<string, int> target, JsonValue v)
        {
            switch (v)
            {
                case JsonNumber n: target["total"] = (int)n.Value; break;
                case JsonArray a: target["total"] = a.Count; break;
                case JsonObject o:
                    foreach (var kv in o)
                    {
                        if (kv.Value is JsonNumber num) target[kv.Key] = (int)num.Value;
                        else if (kv.Value is JsonArray arr) target[kv.Key] = arr.Count;
                    }
                    break;
            }
        }

        /// <summary>Looks for create/update/delete under several spellings ("created", "create", ...).</summary>
        public static ChangeCounts Parse(JsonObject o)
        {
            var c = new ChangeCounts();
            if (o == null) return c;
            Fill(c.Create, o["create"] ?? o["created"]);
            Fill(c.Update, o["update"] ?? o["updated"]);
            Fill(c.Delete, o["delete"] ?? o["deleted"]);
            return c;
        }

        public static string Describe(Dictionary<string, int> d) =>
            d.Count == 0 ? "0" : string.Join(", ", d.Select(kv => kv.Key == "total" ? kv.Value.ToString() : $"{kv.Value} {kv.Key}"));
    }

    public sealed class ObjectPreview
    {
        public string Id { get; set; }
        public string Role { get; set; }
        public string Section { get; set; }
        public string Story { get; set; }
        public string Status { get; set; }
    }

    public sealed class PreviewResult
    {
        public ChangeCounts Summary { get; set; } = new ChangeCounts();
        public List<string> Warnings { get; } = new List<string>();
        public List<ObjectPreview> Objects { get; } = new List<ObjectPreview>();
        public JsonObject Raw { get; set; }

        public static PreviewResult Parse(JsonValue v)
        {
            var o = v as JsonObject ?? new JsonObject();
            var r = new PreviewResult { Summary = ChangeCounts.Parse(o["summary"] as JsonObject), Raw = o };
            r.Warnings.AddRange(Report.ParseWarnings(o["warnings"]));
            if (o["objects"] is JsonArray a)
                foreach (var x in a)
                    r.Objects.Add(new ObjectPreview
                    {
                        Id = x["id"]?.AsString() ?? "",
                        Role = x["role"]?.AsString() ?? "",
                        Section = x["section"]?.AsString() ?? "",
                        Story = x["story"]?.AsString() ?? "",
                        Status = x["status"]?.AsString() ?? "",
                    });
            return r;
        }
    }

    /// <summary>Apply report. Accepts the counts at the report root or under report.summary.</summary>
    public sealed class Report
    {
        public ChangeCounts Counts { get; set; } = new ChangeCounts();
        public List<string> Warnings { get; } = new List<string>();
        public JsonValue Raw { get; set; }

        public static Report Parse(JsonValue v)
        {
            var o = v as JsonObject ?? new JsonObject();
            var counts = ChangeCounts.Parse(o);
            if (counts.Create.Count + counts.Update.Count + counts.Delete.Count == 0 && o["summary"] is JsonObject s)
                counts = ChangeCounts.Parse(s);
            var r = new Report { Counts = counts, Raw = v };
            r.Warnings.AddRange(ParseWarnings(o["warnings"]));
            return r;
        }

        internal static IEnumerable<string> ParseWarnings(JsonValue v)
        {
            if (!(v is JsonArray a)) yield break;
            foreach (var w in a)
            {
                if (w is JsonString s) yield return s.Value;
                else if (w is JsonObject wo)
                {
                    var msg = wo["message"]?.AsString() ?? wo["msg"]?.AsString() ?? wo["warning"]?.AsString();
                    var id = wo["id"]?.AsString();
                    yield return msg == null ? wo.ToJson() : (id != null ? $"{id}: {msg}" : msg);
                }
                else if (w != null && !w.IsNull) yield return w.ToJson();
            }
        }

        public string ToText()
        {
            var sb = new StringBuilder();
            sb.Append("created: ").AppendLine(ChangeCounts.Describe(Counts.Create));
            sb.Append("updated: ").AppendLine(ChangeCounts.Describe(Counts.Update));
            sb.Append("deleted: ").AppendLine(ChangeCounts.Describe(Counts.Delete));
            foreach (var w in Warnings) sb.Append("warning: ").AppendLine(w);
            return sb.ToString().TrimEnd();
        }
    }

    public sealed class ApplyResult
    {
        public Report Report { get; set; } = new Report();
        public JsonValue ModelSummary { get; set; }
        public JsonObject Raw { get; set; }

        public static ApplyResult Parse(JsonValue v)
        {
            var o = v as JsonObject ?? new JsonObject();
            return new ApplyResult { Report = Report.Parse(o["report"]), ModelSummary = o["model_summary"], Raw = o };
        }
    }

    public sealed class LinkInfo
    {
        public string Path { get; set; }
        public double Mtime { get; set; }
        public bool Changed { get; set; }
        public static LinkInfo Parse(JsonValue v) => new LinkInfo
        {
            Path = v?["path"]?.AsString() ?? "",
            Mtime = v?["mtime"]?.AsDouble() ?? 0,
            Changed = v?["changed"]?.AsBool() ?? false,
        };
    }

    public sealed class SkyFrameApiException : Exception
    {
        public SkyFrameApiException(int status, string message, string body) : base(message)
        {
            StatusCode = status; Body = body;
        }
        public int StatusCode { get; }
        public string Body { get; }
    }
}
