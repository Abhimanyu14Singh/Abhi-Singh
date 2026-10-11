using System;
using System.Collections.Generic;
using System.Linq;
using SkyFrame.Exchange.Model;

namespace SkyFrame.Exchange.Results
{
    /// <summary>
    /// Converts SI results (spec §7: m, rad, kN, kN·m) for display in the Rhino document:
    /// lengths (translations, station positions, story displacements) → document units;
    /// rotations (rad), forces (kN), moments (kN·m), periods (s) and drift ratios are unit-independent
    /// of the document and are passed through unchanged.
    /// </summary>
    public sealed class ResultsConverter
    {
        public ResultsConverter(double metersPerDocUnit)
        {
            if (!(metersPerDocUnit > 0)) throw new ArgumentOutOfRangeException(nameof(metersPerDocUnit));
            MetersPerDocUnit = metersPerDocUnit;
        }

        public ResultsConverter(string docUnitCode) : this(Units.MetersPerUnit(docUnitCode)) { }

        public double MetersPerDocUnit { get; }

        public double Length(double meters) => meters / MetersPerDocUnit;

        public Point3 Point(Point3 meters) => meters * (1.0 / MetersPerDocUnit);

        /// <summary>[ux,uy,uz,rx,ry,rz]: translations converted, rotations kept.</summary>
        public double[] Displacement(double[] v) =>
            new[] { Length(v[0]), Length(v[1]), Length(v[2]), v[3], v[4], v[5] };

        public List<NodeValue> Displacements(IEnumerable<NodeValue> values) => values.Select(n => new NodeValue
        {
            Position = n.Position.HasValue ? Point(n.Position.Value) : (Point3?)null,
            Values = Displacement(n.Values),
        }).ToList();

        /// <summary>Reactions stay kN / kN·m; only node positions are converted.</summary>
        public List<NodeValue> Reactions(IEnumerable<NodeValue> values) => values.Select(n => new NodeValue
        {
            Position = n.Position.HasValue ? Point(n.Position.Value) : (Point3?)null,
            Values = (double[])n.Values.Clone(),
        }).ToList();

        /// <summary>Station positions converted to document units; forces unchanged.</summary>
        public MemberForces MemberForces(MemberForces mf)
        {
            var r = new MemberForces { SegmentCount = mf.SegmentCount };
            r.X.AddRange(mf.X.Select(Length));
            foreach (var c in Results.MemberForces.Components) r.Values[c].AddRange(mf.Values[c]);
            return r;
        }

        /// <summary>Story keys that are lengths: ux/uy/uz, disp*, displacement*, elev*, height*.</summary>
        public static bool IsStoryLengthKey(string key)
        {
            var k = key.ToLowerInvariant();
            if (k.StartsWith("drift") || k.Contains("ratio")) return false;
            return k == "ux" || k == "uy" || k == "uz" || k.StartsWith("disp") || k.StartsWith("elev") || k.StartsWith("height") || k == "h" || k == "z";
        }

        public List<StoryRow> StoryRows(IEnumerable<StoryRow> rows) => rows.Select(r =>
        {
            var o = new StoryRow { Story = r.Story };
            foreach (var kv in r.Values)
                o.Values.Add(new KeyValuePair<string, double>(kv.Key, IsStoryLengthKey(kv.Key) ? Length(kv.Value) : kv.Value));
            return o;
        }).ToList();
    }

    /// <summary>Deformed shapes from displacement results, in the payload's source units.</summary>
    public static class Deformation
    {
        /// <param name="obj">The object as sent (coordinates in <paramref name="sourceUnits"/>).</param>
        /// <param name="disp">Raw SI displacements for this id (positions/translations in m).</param>
        /// <param name="scale">Displacement magnification.</param>
        /// <param name="sourceUnits">The payload's source.units (the unit of <paramref name="obj"/>'s coordinates).</param>
        /// <returns>Deformed geometry: a polyline for curves, a point, a polygon, or a mesh; null without data.</returns>
        public static Geometry Apply(ExchangeObject obj, IList<NodeValue> disp, double scale, string sourceUnits)
        {
            if (obj?.Geom == null || disp == null || disp.Count == 0) return null;
            double toSrc = 1.0 / Units.MetersPerUnit(sourceUnits);
            var nodes = disp.Select(d => new
            {
                P = d.Position.HasValue ? d.Position.Value * toSrc : (Point3?)null,
                U = new Point3(d.Values[0], d.Values[1], d.Values[2]) * (toSrc * scale),
            }).ToList();
            bool withPos = nodes.All(n => n.P.HasValue);

            switch (obj.Geom)
            {
                case PointGeometry pg:
                    return new PointGeometry(pg.P + Nearest(pg.P, nodes.Select(n => (n.P, n.U)).ToList(), withPos));

                case CurveGeometry cg:
                {
                    if (withPos && nodes.Count >= 2)
                    {
                        // FE nodes along the member, ordered by arc position from the curve start.
                        var verts = cg.Vertices();
                        var ordered = nodes.OrderBy(n => Param(verts, n.P.Value)).ToList();
                        return new PolylineGeometry(ordered.Select(n => n.P.Value + n.U));
                    }
                    var v = cg.Vertices();
                    if (nodes.Count == v.Count)
                        return new PolylineGeometry(v.Select((p, i) => p + nodes[i].U));
                    if (nodes.Count == 1)
                        return new PolylineGeometry(v.Select(p => p + nodes[0].U));
                    // Evenly spaced stations along the curve: interpolate by arc-length fraction.
                    var fr = Fractions(v);
                    return new PolylineGeometry(v.Select((p, i) => p + Interp(nodes.Select(n => n.U).ToList(), fr[i])));
                }

                case PolygonGeometry poly:
                    return poly.Map(p => p + Nearest(p, nodes.Select(n => (n.P, n.U)).ToList(), withPos));

                case MeshGeometry mesh:
                    return MapMesh(mesh, nodes.Select(n => (n.P, n.U)).ToList(), withPos);

                case NurbsSurfaceGeometry ns:
                    return MapMesh(ns.Mesh, nodes.Select(n => (n.P, n.U)).ToList(), withPos);
            }
            return null;
        }

        private static Geometry MapMesh(MeshGeometry mesh, List<(Point3? P, Point3 U)> nodes, bool withPos)
        {
            if (!withPos && nodes.Count == mesh.Vertices.Count)
            {
                var m = new MeshGeometry { Faces = mesh.Faces.Select(f => (int[])f.Clone()).ToList() };
                m.Vertices.AddRange(mesh.Vertices.Select((p, i) => p + nodes[i].U));
                return m;
            }
            return mesh.Map(p => p + Nearest(p, nodes, withPos));
        }

        private static Point3 Nearest(Point3 p, List<(Point3? P, Point3 U)> nodes, bool withPos)
        {
            if (!withPos)
            {
                // no positions: rigid translation by the mean displacement
                var sum = new Point3();
                foreach (var n in nodes) sum = sum + n.U;
                return sum * (1.0 / nodes.Count);
            }
            double best = double.MaxValue;
            Point3 u = new Point3();
            foreach (var n in nodes)
            {
                var d = n.P.Value.DistanceTo(p);
                if (d < best) { best = d; u = n.U; }
            }
            return u;
        }

        private static double[] Fractions(IReadOnlyList<Point3> v)
        {
            var f = new double[v.Count];
            double total = 0;
            for (int i = 1; i < v.Count; i++) { total += v[i].DistanceTo(v[i - 1]); f[i] = total; }
            for (int i = 0; i < v.Count; i++) f[i] = total > 0 ? f[i] / total : (v.Count > 1 ? (double)i / (v.Count - 1) : 0);
            return f;
        }

        private static Point3 Interp(List<Point3> u, double t)
        {
            if (u.Count == 1) return u[0];
            double s = t * (u.Count - 1);
            int i = Math.Min((int)Math.Floor(s), u.Count - 2);
            double w = s - i;
            return u[i] * (1 - w) + u[i + 1] * w;
        }

        /// <summary>Arc-length parameter of the closest point on the polyline <paramref name="v"/>.</summary>
        private static double Param(IReadOnlyList<Point3> v, Point3 p)
        {
            double best = double.MaxValue, bestT = 0, acc = 0;
            for (int i = 1; i < v.Count; i++)
            {
                var a = v[i - 1]; var b = v[i];
                var ab = b - a;
                double len2 = ab.X * ab.X + ab.Y * ab.Y + ab.Z * ab.Z;
                double len = Math.Sqrt(len2);
                double t = len2 > 0 ? Math.Max(0, Math.Min(1, ((p.X - a.X) * ab.X + (p.Y - a.Y) * ab.Y + (p.Z - a.Z) * ab.Z) / len2)) : 0;
                var q = a + ab * t;
                double d = q.DistanceTo(p);
                if (d < best) { best = d; bestT = acc + t * len; }
                acc += len;
            }
            return bestT;
        }
    }
}
