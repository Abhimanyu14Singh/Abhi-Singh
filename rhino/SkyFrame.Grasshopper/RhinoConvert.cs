// The only place that turns RhinoCommon geometry into exchange DTOs (and back for
// previews / deformed shapes). Everything else lives in SkyFrame.Exchange.

using System;
using System.Collections.Generic;
using System.Linq;
using Rhino.Geometry;
using SkyFrame.Exchange.Model;
using SfGeometry = SkyFrame.Exchange.Model.Geometry;

namespace SkyFrame.Grasshopper
{
    internal sealed class ConvertSettings
    {
        /// <summary>Document → exported (source.units) length factor.</summary>
        public double Scale = 1.0;
        /// <summary>Document absolute tolerance (document units).</summary>
        public double Tolerance = 0.001;
        public double AngleTolerance = Math.PI / 180;
        /// <summary>Target mesh / sample spacing in document units (0 = automatic).</summary>
        public double MeshSize;

        public static ConvertSettings FromDocument(double meshSize = 0)
        {
            var u = DocContext.Units();
            return new ConvertSettings
            {
                Scale = u.CoordinateScale,
                Tolerance = DocContext.Tolerance,
                AngleTolerance = DocContext.AngleTolerance,
                MeshSize = meshSize,
            };
        }
    }

    internal static class RhinoConvert
    {
        public static Point3 P(Point3d p, double s) => new Point3(p.X * s, p.Y * s, p.Z * s);
        public static Point3d R(Point3 p, double toDoc) => new Point3d(p.X * toDoc, p.Y * toDoc, p.Z * toDoc);

        // ---------------- curves ----------------

        public static CurveGeometry FromCurve(Curve c, ConvertSettings cs)
        {
            if (c == null || !c.IsValid) return null;
            double s = cs.Scale, tol = cs.Tolerance;

            if (!c.IsClosed && c.IsLinear(tol))
                return new LineGeometry(P(c.PointAtStart, s), P(c.PointAtEnd, s));

            if (c.TryGetPolyline(out Polyline pl) && pl.Count >= 2)
                return pl.Count == 2
                    ? (CurveGeometry)new LineGeometry(P(pl[0], s), P(pl[1], s))
                    : new PolylineGeometry(pl.Select(p => P(p, s)));

            if (!c.IsClosed && c.TryGetArc(out Arc arc, tol))
                return new ArcGeometry(P(arc.StartPoint, s), P(arc.MidPoint, s), P(arc.EndPoint, s));

            var nc = c.ToNurbsCurve();
            if (nc == null) return null;
            var g = new NurbsCurveGeometry { Degree = nc.Degree, Periodic = nc.IsPeriodic };
            for (int i = 0; i < nc.Points.Count; i++)
            {
                var cp = nc.Points[i];
                g.ControlPoints.Add(P(cp.Location, s));
                g.Weights.Add(cp.Weight);
            }
            for (int i = 0; i < nc.Knots.Count; i++) g.Knots.Add(nc.Knots[i]);
            g.Samples.AddRange(Sample(c, cs).Select(p => P(p, s)));
            return g;
        }

        /// <summary>Points along the curve within the document tolerance (and at most MeshSize apart when set).</summary>
        public static List<Point3d> Sample(Curve c, ConvertSettings cs)
        {
            double maxLen = cs.MeshSize > 0 ? cs.MeshSize : 0;
            var pc = c.ToPolyline(cs.Tolerance, cs.AngleTolerance, 0, maxLen);
            if (pc != null && pc.TryGetPolyline(out Polyline pl) && pl.Count >= 2) return pl.ToList();
            // fallback: fixed subdivision
            var t = c.DivideByCount(32, true) ?? new double[0];
            var pts = t.Select(c.PointAt).ToList();
            if (c.IsClosed && pts.Count > 0) pts.Add(pts[0]);
            return pts;
        }

        // ---------------- surfaces / breps / meshes ----------------

        /// <summary>One SF geometry per brep face (planar → polygon, freeform → nurbs_surface + mesh).</summary>
        public static List<SfGeometry> FromBrep(Brep brep, ConvertSettings cs, List<string> warnings)
        {
            var list = new List<SfGeometry>();
            if (brep == null || !brep.IsValid) { warnings?.Add("invalid brep skipped"); return list; }
            if (brep.IsSolid) warnings?.Add("closed (solid) brep: every face is sent as a separate shell");
            foreach (var face in brep.Faces)
            {
                try { list.Add(FromFace(face, cs)); }
                catch (Exception ex) { warnings?.Add($"face {face.FaceIndex}: {ex.Message}"); }
            }
            return list;
        }

        public static SfGeometry FromFace(BrepFace face, ConvertSettings cs)
        {
            double s = cs.Scale;
            if (face.IsPlanar(cs.Tolerance))
            {
                var poly = new PolygonGeometry();
                foreach (var loop in face.Loops)
                {
                    var pts = LoopPoints(loop.To3dCurve(), cs).Select(p => P(p, s)).ToList();
                    if (loop.LoopType == BrepLoopType.Outer) poly.Outer = pts;
                    else if (loop.LoopType == BrepLoopType.Inner) poly.Holes.Add(pts);
                }
                return poly;
            }

            var ns = face.ToNurbsSurface();
            var g = new NurbsSurfaceGeometry
            {
                DegreeU = ns.Degree(0), DegreeV = ns.Degree(1),
                CountU = ns.Points.CountU, CountV = ns.Points.CountV,
                ClosedU = ns.IsClosed(0), ClosedV = ns.IsClosed(1),
            };
            for (int i = 0; i < ns.Points.CountU; i++)
                for (int j = 0; j < ns.Points.CountV; j++)
                {
                    var cp = ns.Points.GetControlPoint(i, j);
                    g.ControlPoints.Add(P(cp.Location, s));
                    g.Weights.Add(cp.Weight);
                }
            for (int i = 0; i < ns.KnotsU.Count; i++) g.KnotsU.Add(ns.KnotsU[i]);
            for (int i = 0; i < ns.KnotsV.Count; i++) g.KnotsV.Add(ns.KnotsV[i]);
            g.Mesh = FromMesh(MeshFace(face, cs), s);
            return g;
        }

        private static List<Point3d> LoopPoints(Curve c, ConvertSettings cs)
        {
            List<Point3d> pts = c.TryGetPolyline(out Polyline pl) ? pl.ToList() : Sample(c, cs);
            if (pts.Count > 1 && pts[0].DistanceTo(pts[pts.Count - 1]) <= cs.Tolerance) pts.RemoveAt(pts.Count - 1);
            return pts;
        }

        /// <summary>Quad-dominant mesh of one face with Rhino's mesher, edge length ≈ MeshSize.</summary>
        public static Mesh MeshFace(BrepFace face, ConvertSettings cs)
        {
            var single = face.DuplicateFace(false);
            double size = cs.MeshSize;
            if (!(size > 0))
            {
                var bb = single.GetBoundingBox(true);
                size = Math.Max(bb.Diagonal.Length / 10.0, cs.Tolerance * 10);
            }
            var mp = new MeshingParameters
            {
                JaggedSeams = false,
                SimplePlanes = false,
                RefineGrid = true,
                GridAspectRatio = 1.0,        // square-ish grid cells → quads
                GridAngle = 0.0,
                MaximumEdgeLength = size,
                MinimumEdgeLength = size * 0.25,
                Tolerance = 0.0,
                RelativeTolerance = 0.0,
                ComputeCurvature = false,
            };
            var parts = Mesh.CreateFromBrep(single, mp) ?? new Mesh[0];
            var m = new Mesh();
            foreach (var p in parts) m.Append(p);
            m.Vertices.CombineIdentical(true, true);
            m.Faces.ConvertTrianglesToQuads(cs.AngleTolerance * 5, 0.875);
            m.Faces.CullDegenerateFaces();
            m.Compact();
            return m;
        }

        public static MeshGeometry FromMesh(Mesh m, double s)
        {
            var g = new MeshGeometry();
            if (m == null) return g;
            for (int i = 0; i < m.Vertices.Count; i++) g.Vertices.Add(P(m.Vertices.Point3dAt(i), s));
            foreach (var f in m.Faces)
                g.Faces.Add(f.IsQuad ? new[] { f.A, f.B, f.C, f.D } : new[] { f.A, f.B, f.C });
            return g;
        }

        /// <summary>Brep / surface / extrusion / mesh → SF geometries (one per face, or one mesh).</summary>
        public static List<SfGeometry> FromSurfaceLike(GeometryBase g, ConvertSettings cs, List<string> warnings)
        {
            switch (g)
            {
                case Mesh m: return new List<SfGeometry> { FromMesh(m, cs.Scale) };
                case Brep b: return FromBrep(b, cs, warnings);
                case Extrusion e: return FromBrep(e.ToBrep(), cs, warnings);
                case Surface s: return FromBrep(s.ToBrep(), cs, warnings);
                case SubD sd:
                    var mesh = Mesh.CreateFromSubD(sd, 1);
                    warnings?.Add("SubD sent as its control mesh (level 1)");
                    return new List<SfGeometry> { FromMesh(mesh, cs.Scale) };
            }
            warnings?.Add($"unsupported geometry {g?.GetType().Name ?? "null"} skipped");
            return new List<SfGeometry>();
        }

        // ---------------- back to Rhino (previews, deformed shapes) ----------------

        public static List<GeometryBase> ToRhino(SfGeometry g, double toDoc)
        {
            var list = new List<GeometryBase>();
            switch (g)
            {
                case LineGeometry l: list.Add(new LineCurve(R(l.A, toDoc), R(l.B, toDoc))); break;
                case ArcGeometry a:
                    var arc = new Arc(R(a.Start, toDoc), R(a.Mid, toDoc), R(a.End, toDoc));
                    list.Add(arc.IsValid ? (GeometryBase)new ArcCurve(arc) : new PolylineCurve(new[] { R(a.Start, toDoc), R(a.Mid, toDoc), R(a.End, toDoc) }));
                    break;
                case CurveGeometry c: list.Add(new PolylineCurve(c.Vertices().Select(p => R(p, toDoc)))); break;
                case PolygonGeometry pg:
                    foreach (var loop in new[] { pg.Outer }.Concat(pg.Holes))
                    {
                        var pts = loop.Select(p => R(p, toDoc)).ToList();
                        if (pts.Count > 0) pts.Add(pts[0]);
                        list.Add(new PolylineCurve(pts));
                    }
                    break;
                case MeshGeometry mg: list.Add(ToRhinoMesh(mg, toDoc)); break;
                case NurbsSurfaceGeometry ns: list.Add(ToRhinoMesh(ns.Mesh, toDoc)); break;
                case PointGeometry pt: list.Add(new Rhino.Geometry.Point(R(pt.P, toDoc))); break;
            }
            return list;
        }

        public static Mesh ToRhinoMesh(MeshGeometry mg, double toDoc)
        {
            var m = new Mesh();
            foreach (var v in mg.Vertices) m.Vertices.Add(R(v, toDoc));
            foreach (var f in mg.Faces)
            {
                if (f.Length == 4) m.Faces.AddFace(f[0], f[1], f[2], f[3]);
                else if (f.Length == 3) m.Faces.AddFace(f[0], f[1], f[2]);
            }
            m.Normals.ComputeNormals();
            m.Compact();
            return m;
        }

        public static string HexColor(System.Drawing.Color c) => $"#{c.R:X2}{c.G:X2}{c.B:X2}";
    }
}
