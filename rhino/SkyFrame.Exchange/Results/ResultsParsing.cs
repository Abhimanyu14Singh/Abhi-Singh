// Results of GET /api/exchange/results (spec §6/§7):
//   {"case":..,"units":"SI","items":{"<source id>":{...}}}
// The spec fixes the envelope but not the body of each item, so the parsers below
// accept the shapes SkyFrame already uses elsewhere (CONTRACT.md):
//   displacements : [ux,uy,uz,rx,ry,rz] | {"ux":..,..} | {"p":[x,y,z],"u":[6]} | lists / "nodes" / "segments" of those
//   member_forces : {"x":[..],"N":[..],"V2":[..],"V3":[..],"T":[..],"M2":[..],"M3":[..]} (member_stations)
//                   | 12 end forces [Ni,Vyi,Vzi,Ti,Myi,Mzi,Nj,...] | "segments": [...] of those, in order
//   reactions     : [FX,FY,FZ,MX,MY,MZ] | {"FX":..,..}
//   periods       : root "periods":[T..] | items {"<mode>": T | {"T":..}} | items [..]
//   drifts/story  : items {"<story>": {"drift_x":..,"ux":..,...}} | items [{"story":..,...}]
// Values are SI (m, rad, kN, kN·m, s).

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using SkyFrame.Exchange.Json;
using SkyFrame.Exchange.Model;

namespace SkyFrame.Exchange.Results
{
    public static class Quantities
    {
        public const string Displacements = "displacements", MemberForces = "member_forces", Reactions = "reactions",
            Periods = "periods", Drifts = "drifts", Story = "story";
        public static readonly string[] All = { Displacements, MemberForces, Reactions, Periods, Drifts, Story };
        public static bool IsModelLevel(string what) => what == Periods || what == Drifts || what == Story;

        public static string Normalize(string what)
        {
            var w = (what ?? "").Trim().ToLowerInvariant().Replace(' ', '_');
            switch (w)
            {
                case "disp": case "displacement": case "displacements": return Displacements;
                case "forces": case "member_force": case "member_forces": case "frame_forces": return MemberForces;
                case "reaction": case "reactions": return Reactions;
                case "period": case "periods": case "modal": return Periods;
                case "drift": case "drifts": return Drifts;
                case "story": case "stories": return Story;
            }
            throw new ArgumentException($"unknown quantity '{what}' (use {string.Join(", ", All)})");
        }
    }

    public sealed class ResultsResponse
    {
        public string Case { get; set; }
        public string Units { get; set; }
        public string What { get; set; }
        /// <summary>Items keyed by SOURCE id (or by story / mode for model-level quantities), in server order.</summary>
        public List<KeyValuePair<string, JsonValue>> Items { get; } = new List<KeyValuePair<string, JsonValue>>();
        public JsonObject Raw { get; set; }

        public JsonValue Get(string id)
        {
            foreach (var kv in Items) if (kv.Key == id) return kv.Value;
            return null;
        }

        public static ResultsResponse Parse(JsonValue v, string what = null)
        {
            var o = v as JsonObject ?? new JsonObject();
            var r = new ResultsResponse
            {
                Case = o["case"]?.AsString() ?? "",
                Units = o["units"]?.AsString() ?? "SI",
                What = o["what"]?.AsString() ?? what ?? "",
                Raw = o,
            };
            switch (o["items"])
            {
                case JsonObject items:
                    foreach (var kv in items) r.Items.Add(kv);
                    break;
                case JsonArray arr:
                    int i = 0;
                    foreach (var x in arr)
                    {
                        i++;
                        var key = x["id"]?.AsString() ?? x["story"]?.AsString() ?? x["mode"]?.AsString() ?? i.ToString(CultureInfo.InvariantCulture);
                        r.Items.Add(new KeyValuePair<string, JsonValue>(key, x));
                    }
                    break;
            }
            return r;
        }

        // -------- typed views --------

        public List<NodeValue> Displacements(string id) => ResultParsers.NodeValues(Get(id), ResultParsers.DispKeys);
        public List<NodeValue> Reactions(string id) => ResultParsers.NodeValues(Get(id), ResultParsers.ReactionKeys);
        public MemberForces MemberForces(string id) => ResultParsers.ParseMemberForces(Get(id));
        public List<ModePeriod> Periods() => ResultParsers.ParsePeriods(this);
        public List<StoryRow> StoryRows() => ResultParsers.ParseStoryRows(this);
    }

    /// <summary>Six values at a node, with the node position when the server sends it.</summary>
    public sealed class NodeValue
    {
        public Point3? Position { get; set; }
        public double[] Values { get; set; } = new double[6];
    }

    public sealed class MemberForces
    {
        public static readonly string[] Components = { "N", "V2", "V3", "T", "M2", "M3" };
        /// <summary>Station positions along the source object (m from its start; segments are concatenated).</summary>
        public List<double> X { get; } = new List<double>();
        public Dictionary<string, List<double>> Values { get; } = Components.ToDictionary(c => c, c => new List<double>());
        public int SegmentCount { get; set; }
        public int Count => X.Count;
    }

    public sealed class ModePeriod
    {
        public int Mode { get; set; }
        public double Period { get; set; }
    }

    public sealed class StoryRow
    {
        public string Story { get; set; }
        public List<KeyValuePair<string, double>> Values { get; } = new List<KeyValuePair<string, double>>();
    }

    public static class ResultParsers
    {
        public static readonly string[] DispKeys = { "ux", "uy", "uz", "rx", "ry", "rz" };
        public static readonly string[] ReactionKeys = { "FX", "FY", "FZ", "MX", "MY", "MZ" };
        private static readonly string[] PositionKeys = { "p", "xyz", "pos", "position", "coords", "point" };
        private static readonly string[] ContainerKeys = { "segments", "nodes", "points", "joints", "values", "ends", "data", "i", "j" };

        // ---- node values (displacements / reactions) ----

        public static List<NodeValue> NodeValues(JsonValue item, string[] keys)
        {
            var list = new List<NodeValue>();
            if (item == null || item.IsNull) return list;
            var records = new List<JsonValue>();
            Collect(item, keys, records, 0);
            foreach (var r in records)
            {
                var nv = new NodeValue();
                if (r is JsonArray a)
                {
                    for (int i = 0; i < 6 && i < a.Count; i++) nv.Values[i] = a[i].AsDouble();
                }
                else if (r is JsonObject o)
                {
                    var vec = o["u"] as JsonArray ?? o["d"] as JsonArray ?? o["values"] as JsonArray ?? o["r"] as JsonArray;
                    for (int i = 0; i < 6; i++)
                        nv.Values[i] = vec != null ? (vec[i]?.AsDouble() ?? 0) : (Lookup(o, keys[i])?.AsDouble() ?? 0);
                    foreach (var pk in PositionKeys)
                        if (o[pk] is JsonArray pa && pa.Count >= 3 && pa.All(x => x is JsonNumber)) { nv.Position = Point3.FromJson(pa); break; }
                }
                list.Add(nv);
            }
            return list;
        }

        private static JsonValue Lookup(JsonObject o, string key)
        {
            if (o.TryGet(key, out var v)) return v;
            foreach (var k in o.Keys) if (string.Equals(k, key, StringComparison.OrdinalIgnoreCase)) return o[k];
            return null;
        }

        private static bool IsNumberArray(JsonArray a) => a.Count > 0 && a.All(x => x is JsonNumber);

        private static void Collect(JsonValue v, string[] keys, List<JsonValue> output, int depth)
        {
            if (depth > 8 || v == null) return;
            switch (v)
            {
                case JsonArray a:
                    if (IsNumberArray(a)) { if (a.Count >= 3) output.Add(a); return; }
                    foreach (var x in a) Collect(x, keys, output, depth + 1);
                    return;
                case JsonObject o:
                    if (keys.Any(k => Lookup(o, k) is JsonNumber) || o["u"] is JsonArray || o["d"] is JsonArray || (o["values"] is JsonArray va && IsNumberArray(va)))
                    {
                        output.Add(o);
                        return;
                    }
                    bool any = false;
                    foreach (var ck in ContainerKeys)
                        if (o.Has(ck)) { any = true; Collect(o[ck], keys, output, depth + 1); }
                    if (any) return;
                    // dictionary keyed by node tag / label: walk values in order
                    foreach (var kv in o)
                        if (kv.Value is JsonObject || kv.Value is JsonArray) Collect(kv.Value, keys, output, depth + 1);
                    return;
            }
        }

        // ---- member forces ----

        public static MemberForces ParseMemberForces(JsonValue item)
        {
            var mf = new MemberForces();
            if (item == null || item.IsNull) return mf;
            var segs = new List<JsonValue>();
            CollectSegments(item, segs, 0);
            foreach (var s in segs) AppendSegment(mf, s);
            return mf;
        }

        private static bool IsStationObject(JsonObject o) =>
            MemberForces.Components.Any(c => o[c] is JsonArray || o[c] is JsonNumber) || o["P"] is JsonArray || o["x"] is JsonArray;

        private static void CollectSegments(JsonValue v, List<JsonValue> segs, int depth)
        {
            if (depth > 6 || v == null) return;
            switch (v)
            {
                case JsonArray a:
                    if (IsNumberArray(a)) { segs.Add(a); return; }
                    // list of scalar station rows → one segment
                    if (a.Count > 0 && a.All(x => x is JsonObject xo && xo["x"] is JsonNumber)) { segs.Add(a); return; }
                    foreach (var x in a) CollectSegments(x, segs, depth + 1);
                    return;
                case JsonObject o:
                    if (IsStationObject(o)) { segs.Add(o); return; }
                    foreach (var key in new[] { "segments", "stations", "members", "data" })
                        if (o.Has(key)) { CollectSegments(o[key], segs, depth + 1); return; }
                    foreach (var kv in o) CollectSegments(kv.Value, segs, depth + 1);
                    return;
            }
        }

        private static void AppendSegment(MemberForces mf, JsonValue seg)
        {
            double offset = mf.X.Count > 0 ? mf.X[mf.X.Count - 1] : 0.0;
            var xs = new List<double>();
            var vals = MemberForces.Components.ToDictionary(c => c, c => new List<double>());

            if (seg is JsonArray a && IsNumberArray(a))
            {
                // 12 local end forces [Ni,Vyi,Vzi,Ti,Myi,Mzi, Nj,...]; no length available → x = 0, 1 (normalised)
                xs.Add(0); xs.Add(1);
                for (int c = 0; c < 6; c++)
                {
                    vals[MemberForces.Components[c]].Add(a[c]?.AsDouble() ?? 0);
                    vals[MemberForces.Components[c]].Add(a[c + 6]?.AsDouble() ?? 0);
                }
            }
            else if (seg is JsonArray rows)
            {
                foreach (var r in rows)
                {
                    xs.Add(r["x"]?.AsDouble() ?? 0);
                    foreach (var c in MemberForces.Components) vals[c].Add(Comp(r as JsonObject, c)?.AsDouble() ?? 0);
                }
            }
            else if (seg is JsonObject o)
            {
                int n = 0;
                foreach (var c in MemberForces.Components)
                    if (Comp(o, c) is JsonArray ca) n = Math.Max(n, ca.Count);
                if (n == 0)
                {
                    // single scalar station
                    xs.Add(o["x"]?.AsDouble() ?? 0);
                    foreach (var c in MemberForces.Components) vals[c].Add(Comp(o, c)?.AsDouble() ?? 0);
                }
                else
                {
                    var xa = o["x"] as JsonArray;
                    var len = (o["L"] ?? o["length"])?.AsDouble() ?? 1.0;
                    for (int i = 0; i < n; i++)
                        xs.Add(xa != null ? (xa[i]?.AsDouble() ?? 0) : (n == 1 ? 0 : len * i / (n - 1)));
                    foreach (var c in MemberForces.Components)
                    {
                        var arr = Comp(o, c) as JsonArray;
                        for (int i = 0; i < n; i++) vals[c].Add(arr?[i]?.AsDouble() ?? 0);
                    }
                }
            }
            else return;

            // Segments of a split frame restart at x=0: lay them end to end.
            bool restarts = mf.X.Count > 0 && xs.Count > 0 && xs[0] < offset - 1e-12;
            for (int i = 0; i < xs.Count; i++)
            {
                mf.X.Add(restarts ? xs[i] + offset : xs[i]);
                foreach (var c in MemberForces.Components) mf.Values[c].Add(vals[c][i]);
            }
            mf.SegmentCount++;
        }

        private static JsonValue Comp(JsonObject o, string c)
        {
            if (o == null) return null;
            if (o.Has(c)) return o[c];
            if (c == "N" && o.Has("P")) return o["P"];
            foreach (var k in o.Keys) if (string.Equals(k, c, StringComparison.OrdinalIgnoreCase)) return o[k];
            return null;
        }

        // ---- model-level ----

        public static List<ModePeriod> ParsePeriods(ResultsResponse r)
        {
            var list = new List<ModePeriod>();
            if (r.Raw?["periods"] is JsonArray pa)
            {
                int m = 0;
                foreach (var p in pa) list.Add(new ModePeriod { Mode = ++m, Period = p is JsonObject po ? (po["T"] ?? po["period"])?.AsDouble() ?? 0 : p.AsDouble() });
                if (list.Count > 0) return list;
            }
            int idx = 0;
            foreach (var kv in r.Items)
            {
                idx++;
                int mode = int.TryParse(kv.Key, NumberStyles.Integer, CultureInfo.InvariantCulture, out var mk) ? mk : idx;
                if (kv.Value is JsonNumber n) list.Add(new ModePeriod { Mode = mode, Period = n.Value });
                else if (kv.Value is JsonObject o)
                    list.Add(new ModePeriod { Mode = o["mode"]?.AsInt(mode) ?? mode, Period = (o["T"] ?? o["period"])?.AsDouble() ?? 0 });
                else if (kv.Value is JsonArray a && kv.Key == "periods")
                    foreach (var p in a) list.Add(new ModePeriod { Mode = list.Count + 1, Period = p.AsDouble() });
            }
            return list;
        }

        public static List<StoryRow> ParseStoryRows(ResultsResponse r)
        {
            var list = new List<StoryRow>();
            foreach (var kv in r.Items)
            {
                if (!(kv.Value is JsonObject o)) continue;
                var row = new StoryRow { Story = o["story"]?.AsString() ?? kv.Key };
                foreach (var f in o)
                    if (f.Value is JsonNumber n) row.Values.Add(new KeyValuePair<string, double>(f.Key, n.Value));
                list.Add(row);
            }
            return list;
        }
    }
}
