using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using SkyFrame.Exchange.Model;

namespace SkyFrame.Exchange.Results
{
    /// <summary>A data-tree branch: path + items.</summary>
    public sealed class Branch<T>
    {
        public Branch(int[] path, List<T> items) { Path = path; Items = items; }
        public int[] Path { get; }
        public List<T> Items { get; }
    }

    /// <summary>
    /// Rhino-free description of SF Results' output trees. For an input object at path {P} index i:
    ///  * Ids       {P;i}     → [id]
    ///  * Values    displacements/reactions {P;i;k} → 6 values of node k (ux..rz | FX..MZ);
    ///              member_forces {P;i;c} → component c (N,V2,V3,T,M2,M3) at every station
    ///  * Stations  member_forces {P;i} → station positions (document units)
    ///  * Vectors   displacements/reactions {P;i} → translation / force vector per node
    ///  * Deformed  displacements {P;i} → deformed geometry (source units, scaled)
    /// Model-level quantities (periods, drifts, story) use {0} for names and {k} per row.
    /// </summary>
    public sealed class ResultsTable
    {
        public string What { get; set; }
        public List<string> Labels { get; } = new List<string>();
        public List<Branch<string>> Ids { get; } = new List<Branch<string>>();
        public List<Branch<double>> Values { get; } = new List<Branch<double>>();
        public List<Branch<double>> Stations { get; } = new List<Branch<double>>();
        public List<Branch<Point3>> Vectors { get; } = new List<Branch<Point3>>();
        public List<Branch<Geometry>> Deformed { get; } = new List<Branch<Geometry>>();
        public List<string> Warnings { get; } = new List<string>();

        private static int[] Append(int[] p, params int[] more) => (p ?? new int[0]).Concat(more).ToArray();

        public static ResultsTable Build(ResultsResponse r, string what, IEnumerable<TreeItem> objects,
                                         ResultsConverter conv, string sourceUnits, double scale)
        {
            what = Quantities.Normalize(what);
            var t = new ResultsTable { What = what };

            if (Quantities.IsModelLevel(what))
            {
                if (what == Quantities.Periods)
                {
                    var periods = r.Periods();
                    t.Labels.AddRange(periods.Select(p => "T" + p.Mode.ToString(CultureInfo.InvariantCulture)));
                    t.Ids.Add(new Branch<string>(new[] { 0 }, periods.Select(p => p.Mode.ToString(CultureInfo.InvariantCulture)).ToList()));
                    t.Values.Add(new Branch<double>(new[] { 0 }, periods.Select(p => p.Period).ToList()));
                }
                else
                {
                    var rows = conv.StoryRows(r.StoryRows());
                    var keys = new List<string>();
                    foreach (var row in rows) foreach (var kv in row.Values) if (!keys.Contains(kv.Key)) keys.Add(kv.Key);
                    t.Labels.AddRange(keys);
                    t.Ids.Add(new Branch<string>(new[] { 0 }, rows.Select(x => x.Story).ToList()));
                    for (int k = 0; k < rows.Count; k++)
                    {
                        var dict = rows[k].Values.ToDictionary(x => x.Key, x => x.Value);
                        t.Values.Add(new Branch<double>(new[] { k }, keys.Select(key => dict.TryGetValue(key, out var v) ? v : double.NaN).ToList()));
                    }
                }
                return t;
            }

            if (what == Quantities.MemberForces) t.Labels.AddRange(MemberForces.Components);
            else if (what == Quantities.Displacements) t.Labels.AddRange(ResultParsers.DispKeys);
            else t.Labels.AddRange(ResultParsers.ReactionKeys);

            int missing = 0;
            foreach (var it in objects ?? Enumerable.Empty<TreeItem>())
            {
                var obj = it?.Object;
                var bp = Append(it?.Path, it?.Index ?? 0);
                if (obj == null) continue;
                t.Ids.Add(new Branch<string>(bp, new List<string> { obj.Id }));
                var raw = r.Get(obj.Id);
                if (raw == null || raw.IsNull) { missing++; continue; }

                switch (what)
                {
                    case Quantities.Displacements:
                    {
                        var si = r.Displacements(obj.Id);
                        var nodes = conv.Displacements(si);
                        for (int k = 0; k < nodes.Count; k++)
                            t.Values.Add(new Branch<double>(Append(bp, k), nodes[k].Values.ToList()));
                        t.Vectors.Add(new Branch<Point3>(bp, nodes.Select(n => new Point3(n.Values[0], n.Values[1], n.Values[2])).ToList()));
                        var def = Deformation.Apply(obj, si, scale, sourceUnits);
                        if (def != null) t.Deformed.Add(new Branch<Geometry>(bp, new List<Geometry> { def }));
                        break;
                    }
                    case Quantities.Reactions:
                    {
                        var nodes = conv.Reactions(r.Reactions(obj.Id));
                        for (int k = 0; k < nodes.Count; k++)
                            t.Values.Add(new Branch<double>(Append(bp, k), nodes[k].Values.ToList()));
                        t.Vectors.Add(new Branch<Point3>(bp, nodes.Select(n => new Point3(n.Values[0], n.Values[1], n.Values[2])).ToList()));
                        break;
                    }
                    case Quantities.MemberForces:
                    {
                        var mf = conv.MemberForces(r.MemberForces(obj.Id));
                        t.Stations.Add(new Branch<double>(bp, mf.X.ToList()));
                        for (int c = 0; c < MemberForces.Components.Length; c++)
                            t.Values.Add(new Branch<double>(Append(bp, c), mf.Values[MemberForces.Components[c]].ToList()));
                        break;
                    }
                }
            }
            if (missing > 0) t.Warnings.Add($"{missing} object(s) have no {what} in case '{r.Case}' (not analysed, not sent, or no such result)");
            return t;
        }
    }
}
