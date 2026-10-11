using System;
using System.Globalization;
using System.Linq;
using System.Threading;
using SkyFrame.Exchange.Json;
using SkyFrame.Exchange.Model;
using Xunit;

namespace SkyFrame.Exchange.Tests
{
    public class SerializationTests
    {
        private static void AssertSameJson(JsonValue expected, JsonValue actual)
        {
            var diff = JsonCompare.Diff(expected, actual);
            Assert.True(diff == null, diff);
        }

        [Fact]
        public void FramesPayloadMatchesSampleExactly()
        {
            var built = Samples.Frames();
            Assert.Empty(built.Warnings);
            AssertSameJson(Samples.ReadJson("frames.json"), built.Payload.ToJson());
        }

        [Fact]
        public void ShellsPayloadMatchesSampleExactly()
        {
            var built = Samples.Shells();
            Assert.Empty(built.Warnings);
            AssertSameJson(Samples.ReadJson("shells.json"), built.Payload.ToJson());
        }

        [Theory]
        [InlineData("frames.json")]
        [InlineData("shells.json")]
        public void SamplesRoundTrip(string name)
        {
            var json = Samples.ReadJson(name);
            var payload = ExchangePayload.FromJson(json);
            AssertSameJson(json, JsonValue.Parse(payload.ToJsonString()));
            AssertSameJson(json, JsonValue.Parse(payload.ToJsonString(indented: true)));
        }

        [Fact]
        public void TopLevelKeysAndOrderFollowTheSpec()
        {
            var o = Samples.Frames().Payload.ToJson();
            Assert.Equal(new[] { "schema", "source", "session", "layers", "objects" }, o.Keys.ToArray());
            Assert.Equal("skyframe.exchange/1", o["schema"].AsString());
            Assert.Equal(new[] { "app", "doc", "units", "tolerance" }, o["source"].AsObject().Keys.ToArray());
            var first = o["objects"][0].AsObject();
            Assert.Equal(new[] { "id", "kind", "layer", "attrs", "role", "props", "geom" }, first.Keys.ToArray());
        }

        [Fact]
        public void AutoRoleAndEmptyPropsAreOmitted()
        {
            var o = Samples.Frames().Payload.ToJson()["objects"];
            Assert.False(o[1].AsObject().Has("role"));       // auto
            Assert.False(o[2].AsObject().Has("props"));      // nothing set
            Assert.False(o[5].AsObject().Has("props"));      // joint without restraint
            Assert.True(o[1].AsObject().Has("attrs"));       // always present, possibly {}
        }

        [Fact]
        public void KindIsDerivedFromGeometry()
        {
            var objs = Samples.Shells().Payload.Objects;
            Assert.Equal(new[] { "surface", "surface", "mesh", "curve" }, objs.Select(x => x.Kind).ToArray());
            Assert.Equal("point", new PointGeometry(new Point3()).Kind);
        }

        [Fact]
        public void KindMismatchIsRejected()
        {
            var bad = "{\"id\":\"a\",\"kind\":\"mesh\",\"layer\":\"\",\"attrs\":{},\"geom\":{\"type\":\"line\",\"p\":[[0,0,0],[1,0,0]]}}";
            Assert.Throws<FormatException>(() => ExchangeObject.FromJson(JsonValue.Parse(bad)));
        }

        [Fact]
        public void WrongSchemaIsRejected()
        {
            Assert.Throws<FormatException>(() => ExchangePayload.Parse("{\"schema\":\"skyframe.exchange/2\",\"objects\":[]}"));
        }

        [Fact]
        public void ReleasesMustHaveSixValues()
        {
            var p = new ObjectProps { ReleaseI = new[] { true, false } };
            Assert.Throws<ArgumentException>(() => p.ToJson());
        }

        [Fact]
        public void NumbersArePlainInvariantJson()
        {
            var prev = Thread.CurrentThread.CurrentCulture;
            try
            {
                Thread.CurrentThread.CurrentCulture = new CultureInfo("de-DE");   // decimal comma
                var g = new LineGeometry(new Point3(0.1, -2.5, 1e-7), new Point3(double.NaN, double.PositiveInfinity, 3));
                var text = g.ToJson().ToJson();
                Assert.Equal("{\"type\":\"line\",\"p\":[[0.1,-2.5,1E-07],[0,0,3]]}", text);
                Assert.Equal(1e-7, JsonValue.Parse(text)["p"][0][2].AsDouble());
            }
            finally
            {
                Thread.CurrentThread.CurrentCulture = prev;
            }
        }

        [Fact]
        public void JsonParserHandlesEscapesAndPythonNonFinite()
        {
            var v = JsonValue.Parse("{\"s\":\"a\\\"b\\u00e9\\n\",\"n\":NaN,\"i\":-Infinity,\"a\":[true,false,null]}");
            Assert.Equal("a\"bé\n", v["s"].AsString());
            Assert.True(double.IsNaN(v["n"].AsDouble()));
            Assert.True(double.IsNegativeInfinity(v["i"].AsDouble()));
            Assert.True(v["a"][2].IsNull);
            Assert.Equal("{\"s\":\"a\\\"bé\\n\"}", new JsonObject().Set("s", "a\"bé\n").ToJson());
            Assert.Throws<JsonParseException>(() => JsonValue.Parse("{\"a\":1,}"));
            Assert.Throws<JsonParseException>(() => JsonValue.Parse("[1 2]"));
        }

        [Fact]
        public void ContentHashTracksChanges()
        {
            var a = Samples.Frames().Payload;
            var b = Samples.Frames().Payload;
            Assert.Equal(a.ContentHash(), b.ContentHash());
            b.Objects[0].Props.Section = "W14X99";
            Assert.NotEqual(a.ContentHash(), b.ContentHash());
        }

        [Fact]
        public void GeometryParsesAllTypes()
        {
            var shells = ExchangePayload.Parse(Samples.Read("shells.json"));
            var poly = Assert.IsType<PolygonGeometry>(shells.Objects[0].Geom);
            Assert.Equal(4, poly.Outer.Count);
            Assert.Single(poly.Holes);
            var ns = Assert.IsType<NurbsSurfaceGeometry>(shells.Objects[1].Geom);
            Assert.Equal(2, ns.Mesh.QuadCount);
            Assert.Equal(6, ns.ControlPoints.Count);
            var mesh = Assert.IsType<MeshGeometry>(shells.Objects[2].Geom);
            Assert.Equal(3, mesh.Faces.Count);
            Assert.Equal("wall", shells.Objects[2].Attrs["sf_role"]);
            var frames = ExchangePayload.Parse(Samples.Read("frames.json"));
            var nurbs = Assert.IsType<NurbsCurveGeometry>(frames.Objects[3].Geom);
            Assert.Equal(3, nurbs.Degree);
            Assert.Equal(nurbs.ControlPoints.Count + nurbs.Degree - 1, nurbs.Knots.Count);   // Rhino knot convention
            Assert.Equal(5, nurbs.Samples.Count);
            Assert.True(frames.Objects[0].Props.ReleaseJ[5]);
        }
    }
}
