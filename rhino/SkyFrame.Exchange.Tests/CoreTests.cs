using System;
using System.Collections.Generic;
using System.Linq;
using SkyFrame.Exchange.Model;
using Xunit;

namespace SkyFrame.Exchange.Tests
{
    public class UnitsTests
    {
        [Theory]
        [InlineData("m", "mm", 1.0, 1000.0)]
        [InlineData("mm", "m", 2500.0, 2.5)]
        [InlineData("ft", "in", 1.0, 12.0)]
        [InlineData("in", "cm", 1.0, 2.54)]
        [InlineData("ft", "m", 10.0, 3.048)]
        public void Convert(string from, string to, double v, double expected)
        {
            Assert.Equal(expected, Units.Convert(v, from, to), 9);
        }

        [Fact]
        public void UnknownUnitThrows()
        {
            Assert.Throws<ArgumentException>(() => Units.MetersPerUnit("yd"));
            Assert.False(Units.IsSupported("km"));
            Assert.True(Units.IsSupported("MM"));
        }

        [Theory]
        [InlineData("Meters", 1.0, "m")]
        [InlineData("Millimeters", 0.001, "mm")]
        [InlineData("Centimeters", 0.01, "cm")]
        [InlineData("Feet", 0.3048, "ft")]
        [InlineData("Inches", 0.0254, "in")]
        public void RhinoUnitSystemsMapToSpecCodes(string rhino, double scale, string code)
        {
            var u = Units.ResolveDocument(rhino, scale);
            Assert.Equal(code, u.Code);
            Assert.Equal(1.0, u.CoordinateScale);
            Assert.Equal(scale, u.MetersPerDocUnit, 12);
        }

        [Fact]
        public void UnsupportedRhinoUnitsAreExportedInMeters()
        {
            var km = Units.ResolveDocument("Kilometers", 1000.0);
            Assert.Equal("m", km.Code);
            Assert.Equal(1000.0, km.CoordinateScale);
            Assert.Equal(2.0, km.FromMeters(2000.0), 12);
            Assert.Equal(0.5, km.FromExported(500.0), 12);
            var yd = Units.ResolveDocument("Yards", 0.9144);
            Assert.Equal(0.9144, yd.CoordinateScale, 12);
        }

        [Fact]
        public void ResultsConverterConvertsOnlyLengths()
        {
            var c = new Results.ResultsConverter("mm");
            var d = c.Displacement(new[] { 0.001, -0.002, 0.0, 0.01, 0.02, 0.03 });
            Assert.Equal(new[] { 1.0, -2.0, 0.0, 0.01, 0.02, 0.03 }, d.Select(x => Math.Round(x, 9)).ToArray());
            Assert.Equal(12.0, new Results.ResultsConverter("in").Length(0.3048), 9);
            Assert.True(Results.ResultsConverter.IsStoryLengthKey("ux"));
            Assert.False(Results.ResultsConverter.IsStoryLengthKey("drift_x"));
            Assert.False(Results.ResultsConverter.IsStoryLengthKey("shear_x"));
        }
    }

    public class StableIdTests
    {
        private static readonly Guid Comp = Guid.Parse("9C1B2D3E-4F50-4A6B-8C7D-9E0F1A2B3C4D");

        [Fact]
        public void GrasshopperIdFormat()
        {
            Assert.Equal("9c1b2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d/0;2/5", StableIds.ForGrasshopper(Comp, new[] { 0, 2 }, 5));
            Assert.Equal("9c1b2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d/0/0", StableIds.ForGrasshopper(Comp, new int[0], 0));
        }

        [Fact]
        public void GrasshopperIdRoundTrips()
        {
            var id = StableIds.ForGrasshopper(Comp, new[] { 3, 1, 4 }, 7);
            Assert.True(StableIds.TryParseGrasshopper(id, out var c, out var path, out var index));
            Assert.Equal(Comp, c);
            Assert.Equal(new[] { 3, 1, 4 }, path);
            Assert.Equal(7, index);
            Assert.True(StableIds.TryParseGrasshopper("9c1b2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d/{0;1}/2", out _, out path, out _));
            Assert.Equal(new[] { 0, 1 }, path);
            Assert.False(StableIds.TryParseGrasshopper(StableIds.ForRhinoObject(Guid.NewGuid()), out _, out _, out _));
        }

        [Fact]
        public void RhinoIdsAreLowercaseGuids()
        {
            var g = Guid.Parse("7E57D004-2B97-4E7A-B45F-5387367791CD");
            Assert.Equal("7e57d004-2b97-4e7a-b45f-5387367791cd", StableIds.ForRhinoObject(g));
            Assert.Equal("7e57d004-2b97-4e7a-b45f-5387367791cd", StableIds.ForRhinoObjectPart(g, 0, 1));
            Assert.Equal("7e57d004-2b97-4e7a-b45f-5387367791cd/2", StableIds.ForRhinoObjectPart(g, 2, 3));
        }

        [Fact]
        public void IdsAreStableAcrossRebuilds()
        {
            var a = Samples.Frames().Payload.Objects.Select(o => o.Id).ToList();
            var b = Samples.Frames().Payload.Objects.Select(o => o.Id).ToList();
            Assert.Equal(a, b);
            Assert.Equal(a.Count, a.Distinct().Count());
        }
    }

    public class PayloadBuilderTests
    {
        private static readonly Guid Comp = Guid.NewGuid();
        private static ExchangeObject Line(double x) => new ExchangeObject { Geom = new LineGeometry(new Point3(x, 0, 0), new Point3(x, 0, 3)) };

        [Fact]
        public void AssignsTreeIdsAndKeepsLockedIds()
        {
            var locked = Line(5);
            locked.Id = "aaaaaaaa-0000-0000-0000-000000000001";
            locked.IdLocked = true;
            locked.Layer = "S::C";
            var gh = Line(0);
            var r = PayloadBuilder.Build(new SourceInfo(), "s", Comp, new[]
            {
                new TreeItem(new[] { 0, 1 }, 0, gh),
                new TreeItem(new[] { 0, 1 }, 1, locked),
                new TreeItem(new[] { 2 }, 0, Line(1)),
            });
            var ids = r.Payload.Objects.Select(o => o.Id).ToArray();
            Assert.Equal(StableIds.ForGrasshopper(Comp, new[] { 0, 1 }, 0), ids[0]);
            Assert.Equal("aaaaaaaa-0000-0000-0000-000000000001", ids[1]);
            Assert.Equal(StableIds.ForGrasshopper(Comp, new[] { 2 }, 0), ids[2]);
            Assert.Equal(new[] { "Grasshopper", "S::C" }, r.Payload.Layers.Select(l => l.Path).ToArray());
            Assert.Null(gh.Id);                 // inputs are not mutated (ids live on the sent copies)
            Assert.Equal("", gh.Layer);
        }

        [Fact]
        public void DuplicateAndInvalidObjectsAreSkippedWithWarnings()
        {
            var a = Line(0); a.Id = "x"; a.IdLocked = true;
            var b = Line(1); b.Id = "x"; b.IdLocked = true;
            var bad = new ExchangeObject { Geom = new PolylineGeometry(new[] { new Point3(0, 0, 0) }) };
            var mesh = new ExchangeObject { Geom = new MeshGeometry { Vertices = { new Point3() }, Faces = { new[] { 0, 1, 2 } } } };
            var r = PayloadBuilder.Build(new SourceInfo(), "s", Comp, new[]
            {
                new TreeItem(new[] { 0 }, 0, a), new TreeItem(new[] { 0 }, 1, b),
                new TreeItem(new[] { 0 }, 2, bad), new TreeItem(new[] { 0 }, 3, mesh),
                new TreeItem(new[] { 0 }, 4, null),
            });
            Assert.Single(r.Payload.Objects);
            Assert.Equal(5, r.Assigned.Count);
            Assert.Equal(3, r.Warnings.Count);
            Assert.Contains(r.Warnings, w => w.Contains("duplicate id x"));
        }
    }

    public class PatternTests
    {
        [Theory]
        [InlineData("111000", new[] { true, true, true, false, false, false })]
        [InlineData("1,1,1,1,1,1", new[] { true, true, true, true, true, true })]
        [InlineData("pinned", new[] { true, true, true, false, false, false })]
        [InlineData("roller", new[] { false, false, true, false, false, false })]
        [InlineData("true false true false true false", new[] { true, false, true, false, true, false })]
        public void Restraints(string text, bool[] expected) => Assert.Equal(expected, Patterns.Restraint(text));

        [Fact]
        public void Releases()
        {
            Assert.Equal(new[] { false, false, false, false, true, true }, Patterns.Release("000011"));
            Assert.Equal(new[] { false, false, false, false, true, true }, Patterns.Release("pinned"));
            Assert.Equal(new bool[6], Patterns.Release("none"));
            Assert.Null(Patterns.Release(" "));
            Assert.Throws<ArgumentException>(() => Patterns.Release("0011"));
            Assert.Equal("000011", Patterns.ToBits(Patterns.Release("pinned")));
        }

        [Fact]
        public void Roles()
        {
            Assert.Null(Model.Roles.Normalize("Auto"));
            Assert.Null(Model.Roles.Normalize(""));
            Assert.Equal("beam", Model.Roles.Normalize(" BEAM ", Model.Roles.FrameRoles));
            Assert.Throws<ArgumentException>(() => Model.Roles.Normalize("wall", Model.Roles.FrameRoles));
        }

        [Theory]
        [InlineData("Structure::Beams", "Structure::Beams", false, true)]
        [InlineData("structure::beams", "Structure::Beams", false, true)]
        [InlineData("Structure", "Structure::Beams", false, false)]
        [InlineData("Structure", "Structure::Beams::L1", true, true)]
        [InlineData("Structure::*", "Structure::Walls", false, true)]
        [InlineData("*Beams", "Structure::Beams", false, true)]
        [InlineData("Arch", "Structure::Beams", true, false)]
        public void LayerFilter(string pattern, string path, bool sub, bool expected)
            => Assert.Equal(expected, Exchange.LayerFilter.Matches(pattern, path, sub));
    }
}
