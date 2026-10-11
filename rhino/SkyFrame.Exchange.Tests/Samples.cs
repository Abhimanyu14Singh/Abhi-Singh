using System;
using System.Collections.Generic;
using System.IO;
using SkyFrame.Exchange;
using SkyFrame.Exchange.Json;
using SkyFrame.Exchange.Model;

namespace SkyFrame.Exchange.Tests
{
    /// <summary>The sample payloads (rhino/samples/*.json) rebuilt with the DTOs / PayloadBuilder.</summary>
    internal static class Samples
    {
        public static readonly Guid Send = Guid.Parse("9c1b2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d");
        public static readonly Guid Definition = Guid.Parse("3f2504e0-4f89-41d3-9a0c-0305e82c3301");

        public static string Read(string name) => File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "samples", name));
        public static JsonValue ReadJson(string name) => JsonValue.Parse(Read(name));

        public static BuildResult Frames()
        {
            var nurbs = new NurbsCurveGeometry
            {
                Degree = 3,
                ControlPoints = { new Point3(12, 0, 0), new Point3(12, 2, 1.5), new Point3(12, 4, 2.5), new Point3(12, 6, 3.5) },
                Weights = { 1, 1, 1, 1 },
                Knots = { 0, 0, 0, 1, 1, 1 },
                Samples =
                {
                    new Point3(12, 0, 0), new Point3(12, 1.5, 1.039062), new Point3(12, 3, 1.9375),
                    new Point3(12, 4.5, 2.742188), new Point3(12, 6, 3.5),
                },
            };
            var items = new List<TreeItem>
            {
                new TreeItem(new[] { 0 }, 0, new ExchangeObject
                {
                    Role = Roles.Normalize("Column", Roles.FrameRoles),
                    Props = new ObjectProps
                    {
                        Section = "W14X90", Material = "A992", Angle = 90,
                        ReleaseI = Patterns.Release("none"), ReleaseJ = Patterns.Release("000011"),
                    },
                    Geom = new LineGeometry(new Point3(0, 0, 0), new Point3(0, 0, 3.5)),
                }),
                new TreeItem(new[] { 0 }, 1, new ExchangeObject
                {
                    Role = Roles.Normalize("auto", Roles.FrameRoles),
                    Props = new ObjectProps { Section = "W18X35" },
                    Geom = new PolylineGeometry(new[] { new Point3(0, 0, 3.5), new Point3(6, 0, 3.5), new Point3(12, 0, 3.5) }),
                }),
                new TreeItem(new[] { 0 }, 2, new ExchangeObject
                {
                    Role = Roles.Beam,
                    Geom = new ArcGeometry(new Point3(0, 6, 3.5), new Point3(3, 7.2, 3.5), new Point3(6, 6, 3.5)),
                }),
                new TreeItem(new[] { 0 }, 3, new ExchangeObject
                {
                    Role = Roles.Brace, Props = new ObjectProps { Section = "HSS6X6X1/2" }, Geom = nurbs,
                }),
                new TreeItem(new[] { 1 }, 0, new ExchangeObject
                {
                    Role = Roles.Support, Props = new ObjectProps { Restraint = Patterns.Restraint("fixed") },
                    Geom = new PointGeometry(new Point3(0, 0, 0)),
                }),
                new TreeItem(new[] { 1 }, 1, new ExchangeObject
                {
                    Role = Roles.Joint, Props = new ObjectProps { Restraint = Patterns.Restraint("") },
                    Geom = new PointGeometry(new Point3(6, 0, 3.5)),
                }),
            };
            var source = new SourceInfo { App = "grasshopper", Doc = "portal.gh", Units = "m", Tolerance = 0.001 };
            return PayloadBuilder.Build(source, StableIds.Session(Definition, Send), Send, items);
        }

        public static BuildResult Shells()
        {
            var items = new List<TreeItem>
            {
                new TreeItem(new[] { 0 }, 0, new ExchangeObject
                {
                    Role = Roles.Slab,
                    Props = new ObjectProps { ShellSection = "SLAB200", Thickness = 200, MeshSize = 1000 },
                    Geom = new PolygonGeometry
                    {
                        Outer = { new Point3(0, 0, 3500), new Point3(6000, 0, 3500), new Point3(6000, 6000, 3500), new Point3(0, 6000, 3500) },
                        Holes = { new List<Point3> { new Point3(2000, 2000, 3500), new Point3(3000, 2000, 3500), new Point3(3000, 3000, 3500), new Point3(2000, 3000, 3500) } },
                    },
                }),
                new TreeItem(new[] { 0 }, 1, new ExchangeObject
                {
                    Role = Roles.Shell,
                    Props = new ObjectProps { MeshSize = 500 },
                    Geom = new NurbsSurfaceGeometry
                    {
                        DegreeU = 2, DegreeV = 1, CountU = 3, CountV = 2,
                        ControlPoints =
                        {
                            new Point3(0, 0, 7000), new Point3(0, 4000, 7000), new Point3(3000, 0, 8000),
                            new Point3(3000, 4000, 8000), new Point3(6000, 0, 7000), new Point3(6000, 4000, 7000),
                        },
                        Weights = { 1, 1, 1, 1, 1, 1 },
                        KnotsU = { 0, 0, 1, 1 },
                        KnotsV = { 0, 1 },
                        Mesh = new MeshGeometry
                        {
                            Vertices =
                            {
                                new Point3(0, 0, 7000), new Point3(3000, 0, 7500), new Point3(6000, 0, 7000),
                                new Point3(0, 4000, 7000), new Point3(3000, 4000, 7500), new Point3(6000, 4000, 7000),
                            },
                            Faces = { new[] { 0, 1, 4, 3 }, new[] { 1, 2, 5, 4 } },
                        },
                    },
                }),
                new TreeItem(new[] { 1 }, 0, new ExchangeObject
                {
                    Id = StableIds.ForRhinoObject(Guid.Parse("7E57D004-2B97-4E7A-B45F-5387367791CD")),
                    IdLocked = true,
                    Layer = "Structure::Walls", LayerColor = "#C04040",
                    Attrs = { ["sf_shell_section"] = "WALL250", ["sf_role"] = "wall" },
                    Geom = new MeshGeometry
                    {
                        Vertices =
                        {
                            new Point3(0, 0, 0), new Point3(3000, 0, 0), new Point3(6000, 0, 0),
                            new Point3(0, 0, 3500), new Point3(3000, 0, 3500), new Point3(6000, 0, 3500),
                        },
                        Faces = { new[] { 0, 1, 4, 3 }, new[] { 1, 2, 5 }, new[] { 1, 5, 4 } },
                    },
                }),
                new TreeItem(new[] { 1 }, 1, new ExchangeObject
                {
                    Id = StableIds.ForRhinoObject(Guid.Parse("b1f0c2a4-0d3e-4c5f-8a6b-7c8d9e0f1a2b")),
                    IdLocked = true,
                    Layer = "Structure::Beams", LayerColor = "#4080C0",
                    Attrs = { ["sf_role"] = "beam", ["sf_section"] = "W18X35", ["sf_release_j"] = "000011" },
                    Geom = new LineGeometry(new Point3(0, 6000, 3500), new Point3(6000, 6000, 3500)),
                }),
            };
            var source = new SourceInfo { App = "grasshopper", Doc = "floor.gh", Units = "mm", Tolerance = 0.01 };
            return PayloadBuilder.Build(source, "floor-study", Send, items);
        }
    }
}
