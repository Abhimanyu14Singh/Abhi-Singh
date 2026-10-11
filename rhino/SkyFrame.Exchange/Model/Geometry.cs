// Geometry DTOs of spec §2. Every class round-trips through JSON exactly as the
// spec writes it; fields the spec leaves open (the body of "nurbs_surface") are
// documented on the class.

using System;
using System.Collections.Generic;
using System.Linq;
using SkyFrame.Exchange.Json;

namespace SkyFrame.Exchange.Model
{
    /// <summary>Plain 3D point in source units.</summary>
    public struct Point3 : IEquatable<Point3>
    {
        public double X, Y, Z;
        public Point3(double x, double y, double z) { X = x; Y = y; Z = z; }

        public JsonArray ToJson() => new JsonArray().Add(X).Add(Y).Add(Z);

        public static Point3 FromJson(JsonValue v)
        {
            if (!(v is JsonArray a) || a.Count < 2) throw new FormatException("point must be [x,y,z]");
            return new Point3(a[0].AsDouble(), a[1].AsDouble(), a.Count > 2 ? a[2].AsDouble() : 0.0);
        }

        public static Point3 operator +(Point3 a, Point3 b) => new Point3(a.X + b.X, a.Y + b.Y, a.Z + b.Z);
        public static Point3 operator -(Point3 a, Point3 b) => new Point3(a.X - b.X, a.Y - b.Y, a.Z - b.Z);
        public static Point3 operator *(Point3 a, double s) => new Point3(a.X * s, a.Y * s, a.Z * s);
        public double DistanceTo(Point3 o) => Math.Sqrt((X - o.X) * (X - o.X) + (Y - o.Y) * (Y - o.Y) + (Z - o.Z) * (Z - o.Z));
        public bool Equals(Point3 o) => X == o.X && Y == o.Y && Z == o.Z;
        public override bool Equals(object obj) => obj is Point3 p && Equals(p);
        public override int GetHashCode() => X.GetHashCode() ^ (Y.GetHashCode() * 397) ^ (Z.GetHashCode() * 7919);
        public override string ToString() => $"({X}, {Y}, {Z})";

        internal static JsonArray ListToJson(IEnumerable<Point3> pts)
        {
            var a = new JsonArray();
            foreach (var p in pts) a.Add(p.ToJson());
            return a;
        }

        internal static List<Point3> ListFromJson(JsonValue v)
        {
            var list = new List<Point3>();
            if (v is JsonArray a) foreach (var p in a) list.Add(FromJson(p));
            return list;
        }
    }

    /// <summary>Base of every "geom" body. <see cref="Type"/> is the spec's "type" tag.</summary>
    public abstract class Geometry
    {
        public abstract string Type { get; }
        /// <summary>The spec "kind" this geometry belongs to (curve/surface/mesh/point).</summary>
        public abstract string Kind { get; }
        public abstract JsonObject ToJson();

        /// <summary>Every coordinate carried by the body (used for transforms and bounding boxes).</summary>
        public abstract IEnumerable<Point3> AllPoints();

        /// <summary>Returns a copy with every coordinate mapped through <paramref name="f"/>.</summary>
        public abstract Geometry Map(Func<Point3, Point3> f);

        protected JsonObject Head() => new JsonObject().Set("type", Type);

        public static Geometry FromJson(JsonValue v)
        {
            var o = v as JsonObject ?? throw new FormatException("geom must be an object");
            var type = o["type"]?.AsString() ?? throw new FormatException("geom.type missing");
            switch (type)
            {
                case "line": return new LineGeometry(Point3.FromJson(o["p"][0]), Point3.FromJson(o["p"][1]));
                case "polyline": return new PolylineGeometry(Point3.ListFromJson(o["p"]));
                case "arc": return new ArcGeometry(Point3.FromJson(o["p"][0]), Point3.FromJson(o["p"][1]), Point3.FromJson(o["p"][2]));
                case "nurbs": return NurbsCurveGeometry.Parse(o);
                case "polygon": return PolygonGeometry.Parse(o);
                case "nurbs_surface": return NurbsSurfaceGeometry.Parse(o);
                case "mesh": return MeshGeometry.Parse(o);
                case "point": return new PointGeometry(Point3.FromJson(o["p"]));
                default: throw new FormatException($"unknown geom.type '{type}'");
            }
        }
    }

    public abstract class CurveGeometry : Geometry
    {
        public override string Kind => ObjectKinds.Curve;
        /// <summary>Points along the curve, in order (vertices, or the NURBS samples).</summary>
        public abstract IReadOnlyList<Point3> Vertices();
    }

    public sealed class LineGeometry : CurveGeometry
    {
        public LineGeometry(Point3 a, Point3 b) { A = a; B = b; }
        public Point3 A { get; }
        public Point3 B { get; }
        public override string Type => "line";
        public override JsonObject ToJson() => Head().Set("p", Point3.ListToJson(new[] { A, B }));
        public override IEnumerable<Point3> AllPoints() => new[] { A, B };
        public override IReadOnlyList<Point3> Vertices() => new[] { A, B };
        public override Geometry Map(Func<Point3, Point3> f) => new LineGeometry(f(A), f(B));
    }

    public sealed class PolylineGeometry : CurveGeometry
    {
        public PolylineGeometry(IEnumerable<Point3> pts) { Points = pts.ToList(); }
        public List<Point3> Points { get; }
        public override string Type => "polyline";
        public override JsonObject ToJson() => Head().Set("p", Point3.ListToJson(Points));
        public override IEnumerable<Point3> AllPoints() => Points;
        public override IReadOnlyList<Point3> Vertices() => Points;
        public override Geometry Map(Func<Point3, Point3> f) => new PolylineGeometry(Points.Select(f));
    }

    /// <summary>Circular arc through start, an interior (mid) point and end.</summary>
    public sealed class ArcGeometry : CurveGeometry
    {
        public ArcGeometry(Point3 start, Point3 mid, Point3 end) { Start = start; Mid = mid; End = end; }
        public Point3 Start { get; }
        public Point3 Mid { get; }
        public Point3 End { get; }
        public override string Type => "arc";
        public override JsonObject ToJson() => Head().Set("p", Point3.ListToJson(new[] { Start, Mid, End }));
        public override IEnumerable<Point3> AllPoints() => new[] { Start, Mid, End };
        public override IReadOnlyList<Point3> Vertices() => new[] { Start, Mid, End };
        public override Geometry Map(Func<Point3, Point3> f) => new ArcGeometry(f(Start), f(Mid), f(End));
    }

    /// <summary>
    /// NURBS curve. Knots follow the Rhino/openNURBS convention (cv count + degree - 1 values,
    /// no superfluous end knots), the same as rhino3dm's <c>NurbsCurve.Knots</c>.
    /// </summary>
    public sealed class NurbsCurveGeometry : CurveGeometry
    {
        public int Degree { get; set; }
        public List<Point3> ControlPoints { get; set; } = new List<Point3>();
        public List<double> Weights { get; set; } = new List<double>();
        public List<double> Knots { get; set; } = new List<double>();
        public bool Periodic { get; set; }
        public List<Point3> Samples { get; set; } = new List<Point3>();

        public override string Type => "nurbs";
        public override JsonObject ToJson() => Head()
            .Set("degree", Degree)
            .Set("cv", Point3.ListToJson(ControlPoints))
            .Set("weights", JsonArray.Of(Weights))
            .Set("knots", JsonArray.Of(Knots))
            .Set("periodic", Periodic)
            .Set("samples", Point3.ListToJson(Samples));

        internal static NurbsCurveGeometry Parse(JsonObject o) => new NurbsCurveGeometry
        {
            Degree = o["degree"]?.AsInt() ?? 1,
            ControlPoints = Point3.ListFromJson(o["cv"]),
            Weights = (o["weights"] as JsonArray)?.Select(x => x.AsDouble(1)).ToList() ?? new List<double>(),
            Knots = (o["knots"] as JsonArray)?.Select(x => x.AsDouble()).ToList() ?? new List<double>(),
            Periodic = o["periodic"]?.AsBool() ?? false,
            Samples = Point3.ListFromJson(o["samples"]),
        };

        public override IEnumerable<Point3> AllPoints() => ControlPoints.Concat(Samples);
        public override IReadOnlyList<Point3> Vertices() => Samples.Count > 0 ? Samples : ControlPoints;
        public override Geometry Map(Func<Point3, Point3> f) => new NurbsCurveGeometry
        {
            Degree = Degree,
            ControlPoints = ControlPoints.Select(f).ToList(),
            Weights = Weights.ToList(),
            Knots = Knots.ToList(),
            Periodic = Periodic,
            Samples = Samples.Select(f).ToList(),
        };
    }

    /// <summary>Planar face: outer loop + inner loops (openings). Loops are open lists (no repeated first point).</summary>
    public sealed class PolygonGeometry : Geometry
    {
        public List<Point3> Outer { get; set; } = new List<Point3>();
        public List<List<Point3>> Holes { get; set; } = new List<List<Point3>>();
        public override string Type => "polygon";
        public override string Kind => ObjectKinds.Surface;

        public override JsonObject ToJson()
        {
            var holes = new JsonArray();
            foreach (var h in Holes) holes.Add(Point3.ListToJson(h));
            return Head().Set("outer", Point3.ListToJson(Outer)).Set("holes", holes);
        }

        internal static PolygonGeometry Parse(JsonObject o)
        {
            var g = new PolygonGeometry { Outer = Point3.ListFromJson(o["outer"]) };
            if (o["holes"] is JsonArray hs) foreach (var h in hs) g.Holes.Add(Point3.ListFromJson(h));
            return g;
        }

        public override IEnumerable<Point3> AllPoints() => Outer.Concat(Holes.SelectMany(h => h));
        public override Geometry Map(Func<Point3, Point3> f) => new PolygonGeometry
        {
            Outer = Outer.Select(f).ToList(),
            Holes = Holes.Select(h => h.Select(f).ToList()).ToList(),
        };
    }

    /// <summary>Face/vertex mesh; faces are triangles or quads (indices into <see cref="Vertices"/>).</summary>
    public sealed class MeshGeometry : Geometry
    {
        public List<Point3> Vertices { get; set; } = new List<Point3>();
        public List<int[]> Faces { get; set; } = new List<int[]>();
        public override string Type => "mesh";
        public override string Kind => ObjectKinds.Mesh;

        /// <summary>The bare {"v","f"} body used both for kind=mesh and for nurbs_surface.mesh.</summary>
        public JsonObject BodyToJson(bool withType)
        {
            var f = new JsonArray();
            foreach (var face in Faces) f.Add(JsonArray.Of(face.Select(i => (double)i)));
            var o = withType ? Head() : new JsonObject();
            return o.Set("v", Point3.ListToJson(Vertices)).Set("f", f);
        }

        public override JsonObject ToJson() => BodyToJson(true);

        internal static MeshGeometry Parse(JsonObject o)
        {
            var m = new MeshGeometry { Vertices = Point3.ListFromJson(o["v"]) };
            if (o["f"] is JsonArray fs)
                foreach (var f in fs)
                    if (f is JsonArray fa) m.Faces.Add(fa.Select(x => x.AsInt()).ToArray());
            return m;
        }

        public override IEnumerable<Point3> AllPoints() => Vertices;
        public override Geometry Map(Func<Point3, Point3> f) => new MeshGeometry
        {
            Vertices = Vertices.Select(f).ToList(),
            Faces = Faces.Select(x => (int[])x.Clone()).ToList(),
        };

        public int QuadCount => Faces.Count(x => x.Length == 4);
    }

    /// <summary>
    /// Freeform face. The spec fixes only "type" and the mandatory "mesh"; the NURBS body uses
    /// (optional, informational) fields: "degree":[du,dv], "count":[nu,nv],
    /// "cv": nu*nv points (u-major: index = i*nv + j), "weights": nu*nv, "knots_u", "knots_v"
    /// (Rhino convention), "closed":[bool,bool]. SkyFrame meshes from "mesh" only.
    /// </summary>
    public sealed class NurbsSurfaceGeometry : Geometry
    {
        public int DegreeU { get; set; } = 1;
        public int DegreeV { get; set; } = 1;
        public int CountU { get; set; }
        public int CountV { get; set; }
        public List<Point3> ControlPoints { get; set; } = new List<Point3>();
        public List<double> Weights { get; set; } = new List<double>();
        public List<double> KnotsU { get; set; } = new List<double>();
        public List<double> KnotsV { get; set; } = new List<double>();
        public bool ClosedU { get; set; }
        public bool ClosedV { get; set; }
        public MeshGeometry Mesh { get; set; } = new MeshGeometry();

        public override string Type => "nurbs_surface";
        public override string Kind => ObjectKinds.Surface;

        public override JsonObject ToJson() => Head()
            .Set("degree", new JsonArray().Add(DegreeU).Add(DegreeV))
            .Set("count", new JsonArray().Add(CountU).Add(CountV))
            .Set("cv", Point3.ListToJson(ControlPoints))
            .Set("weights", JsonArray.Of(Weights))
            .Set("knots_u", JsonArray.Of(KnotsU))
            .Set("knots_v", JsonArray.Of(KnotsV))
            .Set("closed", new JsonArray().Add(ClosedU).Add(ClosedV))
            .Set("mesh", Mesh.BodyToJson(false));

        internal static NurbsSurfaceGeometry Parse(JsonObject o)
        {
            var g = new NurbsSurfaceGeometry
            {
                DegreeU = o["degree"]?[0]?.AsInt(1) ?? 1,
                DegreeV = o["degree"]?[1]?.AsInt(1) ?? 1,
                CountU = o["count"]?[0]?.AsInt() ?? 0,
                CountV = o["count"]?[1]?.AsInt() ?? 0,
                ControlPoints = Point3.ListFromJson(o["cv"]),
                Weights = (o["weights"] as JsonArray)?.Select(x => x.AsDouble(1)).ToList() ?? new List<double>(),
                KnotsU = (o["knots_u"] as JsonArray)?.Select(x => x.AsDouble()).ToList() ?? new List<double>(),
                KnotsV = (o["knots_v"] as JsonArray)?.Select(x => x.AsDouble()).ToList() ?? new List<double>(),
                ClosedU = o["closed"]?[0]?.AsBool() ?? false,
                ClosedV = o["closed"]?[1]?.AsBool() ?? false,
            };
            if (o["mesh"] is JsonObject m) g.Mesh = MeshGeometry.Parse(m);
            return g;
        }

        public override IEnumerable<Point3> AllPoints() => ControlPoints.Concat(Mesh.Vertices);
        public override Geometry Map(Func<Point3, Point3> f) => new NurbsSurfaceGeometry
        {
            DegreeU = DegreeU, DegreeV = DegreeV, CountU = CountU, CountV = CountV,
            ControlPoints = ControlPoints.Select(f).ToList(),
            Weights = Weights.ToList(), KnotsU = KnotsU.ToList(), KnotsV = KnotsV.ToList(),
            ClosedU = ClosedU, ClosedV = ClosedV,
            Mesh = (MeshGeometry)Mesh.Map(f),
        };
    }

    public sealed class PointGeometry : Geometry
    {
        public PointGeometry(Point3 p) { P = p; }
        public Point3 P { get; }
        public override string Type => "point";
        public override string Kind => ObjectKinds.Point;
        public override JsonObject ToJson() => Head().Set("p", P.ToJson());
        public override IEnumerable<Point3> AllPoints() => new[] { P };
        public override Geometry Map(Func<Point3, Point3> f) => new PointGeometry(f(P));
    }
}
