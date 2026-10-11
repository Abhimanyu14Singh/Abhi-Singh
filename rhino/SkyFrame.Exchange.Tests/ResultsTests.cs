using System.Collections.Generic;
using System.Linq;
using SkyFrame.Exchange.Json;
using SkyFrame.Exchange.Model;
using SkyFrame.Exchange.Results;
using Xunit;

namespace SkyFrame.Exchange.Tests
{
    public class ResultsTests
    {
        private const string Col = "9c1b2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d/0/0";
        private const string Beam = "9c1b2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d/0/1";

        private static ResultsResponse Parse(string json, string what) => ResultsResponse.Parse(JsonValue.Parse(json), what);

        [Fact]
        public void DisplacementsInSeveralShapes()
        {
            var r = Parse(@"{""case"":""DEAD"",""units"":""SI"",""items"":{
                ""a"":[0.001,0.002,-0.003,0.1,0.2,0.3],
                ""b"":{""ux"":0.01,""uy"":0,""uz"":-0.02,""rx"":0,""ry"":0.001,""rz"":0},
                ""c"":{""nodes"":[{""p"":[0,0,0],""u"":[0,0,0,0,0,0]},{""p"":[0,0,3.5],""u"":[0.004,0,0,0,0,0]}]},
                ""d"":[{""p"":[0,0,3.5],""ux"":0.004}, {""p"":[6,0,3.5],""ux"":0.005}],
                ""e"":{""segments"":[{""i"":[1,0,0,0,0,0],""j"":[2,0,0,0,0,0]},{""i"":[2,0,0,0,0,0],""j"":[3,0,0,0,0,0]}]}
            }}", "displacements");
            Assert.Equal("DEAD", r.Case);
            Assert.Equal("SI", r.Units);
            Assert.Equal(-0.003, r.Displacements("a").Single().Values[2]);
            Assert.Equal(new[] { 0.01, 0, -0.02, 0, 0.001, 0 }, r.Displacements("b").Single().Values);
            var c = r.Displacements("c");
            Assert.Equal(2, c.Count);
            Assert.Equal(new Point3(0, 0, 3.5), c[1].Position.Value);
            Assert.Equal(0.004, c[1].Values[0]);
            Assert.Equal(0.005, r.Displacements("d")[1].Values[0]);
            Assert.Equal(new[] { 1.0, 2, 2, 3 }, r.Displacements("e").Select(n => n.Values[0]).ToArray());
            Assert.Empty(r.Displacements("missing"));
        }

        [Fact]
        public void MemberForcesStationsAndSplitSegments()
        {
            var r = Parse(@"{""case"":""LIVE"",""units"":""SI"",""items"":{
                ""m1"":{""x"":[0,1.75,3.5],""N"":[-100,-100,-100],""V2"":[5,0,-5],""V3"":[0,0,0],""T"":[0,0,0],""M2"":[0,0,0],""M3"":[0,4.4,0]},
                ""m2"":[{""x"":[0,3],""N"":[1,1],""M3"":[0,10]},{""x"":[0,3],""N"":[2,2],""M3"":[10,0]}],
                ""m3"":[1,2,3,4,5,6,7,8,9,10,11,12],
                ""m4"":{""segments"":[{""x"":[0,6],""P"":[3,3]}]}
            }}", "member_forces");
            var m1 = r.MemberForces("m1");
            Assert.Equal(new[] { 0, 1.75, 3.5 }, m1.X);
            Assert.Equal(4.4, m1.Values["M3"][1]);
            var m2 = r.MemberForces("m2");
            Assert.Equal(2, m2.SegmentCount);
            Assert.Equal(new[] { 0.0, 3, 3, 6 }, m2.X);                // laid end to end
            Assert.Equal(new[] { 1.0, 1, 2, 2 }, m2.Values["N"]);
            var m3 = r.MemberForces("m3");
            Assert.Equal(new[] { 1.0, 7 }, m3.Values["N"]);
            Assert.Equal(new[] { 6.0, 12 }, m3.Values["M3"]);
            Assert.Equal(new[] { 3.0, 3 }, r.MemberForces("m4").Values["N"]);  // "P" alias
        }

        [Fact]
        public void ReactionsPeriodsAndStories()
        {
            var re = Parse(@"{""case"":""DEAD"",""units"":""SI"",""items"":{""s1"":{""FX"":1,""FY"":2,""FZ"":300,""MX"":0,""MY"":0,""MZ"":0.5}}}", "reactions");
            Assert.Equal(new[] { 1.0, 2, 300, 0, 0, 0.5 }, re.Reactions("s1").Single().Values);

            var p1 = Parse(@"{""case"":""MODAL"",""units"":""SI"",""items"":{""1"":{""T"":0.82},""2"":{""T"":0.41},""3"":0.2}}", "periods").Periods();
            Assert.Equal(new[] { 0.82, 0.41, 0.2 }, p1.Select(p => p.Period));
            Assert.Equal(new[] { 1, 2, 3 }, p1.Select(p => p.Mode));
            var p2 = Parse(@"{""case"":""MODAL"",""units"":""SI"",""periods"":[1.1,0.5],""items"":{}}", "periods").Periods();
            Assert.Equal(new[] { 1.1, 0.5 }, p2.Select(p => p.Period));

            var st = Parse(@"{""case"":""EQX"",""units"":""SI"",""items"":{""Story1"":{""ux"":0.002,""drift_x"":0.0006},""Story2"":{""ux"":0.005,""drift_x"":0.0009}}}", "drifts");
            var rows = new ResultsConverter("mm").StoryRows(st.StoryRows());
            Assert.Equal("Story2", rows[1].Story);
            Assert.Equal(5.0, rows[1].Values.First(v => v.Key == "ux").Value, 9);      // length → mm
            Assert.Equal(0.0009, rows[1].Values.First(v => v.Key == "drift_x").Value);  // ratio unchanged
        }

        [Fact]
        public void ItemsAsArrayAreKeyedById()
        {
            var r = Parse(@"{""case"":""D"",""units"":""SI"",""items"":[{""id"":""x"",""ux"":0.1},{""story"":""S1"",""drift_x"":0.01}]}", "displacements");
            Assert.Equal(0.1, r.Displacements("x").Single().Values[0]);
            Assert.NotNull(r.Get("S1"));
        }

        [Fact]
        public void TableMatchesInputTreeAndConvertsUnits()
        {
            var built = Samples.Frames();
            var items = built.Payload.Objects.Select((o, i) => new TreeItem(new[] { 0 }, i, o)).ToList();
            var r = Parse(@"{""case"":""DEAD"",""units"":""SI"",""items"":{
                """ + Col + @""":[{""p"":[0,0,0],""u"":[0,0,0,0,0,0]},{""p"":[0,0,3.5],""u"":[0.01,0,-0.002,0,0,0]}],
                """ + Beam + @""":[[0,0,-0.001,0,0,0],[0,0,-0.02,0,0,0],[0,0,-0.001,0,0,0]]
            }}", "displacements");

            var t = ResultsTable.Build(r, "displacements", items, new ResultsConverter("mm"), "m", 10.0);
            Assert.Equal(ResultParsers.DispKeys, t.Labels);
            Assert.Equal(items.Count, t.Ids.Count);
            Assert.Equal(new[] { 0, 0 }, t.Ids[0].Path);
            Assert.Equal(new[] { 0, 1 }, t.Ids[1].Path);
            // column node 1 → {0;0;1}, ux = 10 mm
            var b = t.Values.Single(x => x.Path.SequenceEqual(new[] { 0, 0, 1 }));
            Assert.Equal(10.0, b.Items[0], 9);
            Assert.Equal(-2.0, b.Items[2], 9);
            // deformed column: top moves 0.01 m * 10 = 0.1 m (source units m)
            var col = Assert.IsType<PolylineGeometry>(t.Deformed.Single(x => x.Path.SequenceEqual(new[] { 0, 0 })).Items[0]);
            Assert.Equal(0.1, col.Points[1].X, 9);
            Assert.Equal(3.5 - 0.02, col.Points[1].Z, 9);
            // beam: 3 values, 3 vertices → index-wise
            var beam = Assert.IsType<PolylineGeometry>(t.Deformed.Single(x => x.Path.SequenceEqual(new[] { 0, 1 })).Items[0]);
            Assert.Equal(3.5 - 0.2, beam.Points[1].Z, 9);
            Assert.Single(t.Warnings);   // the other 4 objects have no result
        }

        [Fact]
        public void TableForMemberForcesAndModelLevel()
        {
            var built = Samples.Frames();
            var items = new List<TreeItem> { new TreeItem(new[] { 2 }, 0, built.Payload.Objects[0]) };
            var r = Parse(@"{""case"":""DEAD"",""units"":""SI"",""items"":{""" + Col + @""":{""x"":[0,1.75,3.5],""N"":[-1,-2,-3],""M3"":[1,2,3]}}}", "member_forces");
            var t = ResultsTable.Build(r, "member_forces", items, new ResultsConverter("mm"), "m", 1);
            Assert.Equal(new[] { 0.0, 1750, 3500 }, t.Stations.Single().Items.Select(x => System.Math.Round(x, 6)));
            Assert.Equal(new[] { 2, 0, 0 }, t.Values[0].Path);           // N
            Assert.Equal(new[] { -1.0, -2, -3 }, t.Values[0].Items);
            Assert.Equal(new[] { 1.0, 2, 3 }, t.Values[5].Items);        // M3 (kN·m unchanged)
            Assert.Empty(t.Warnings);

            var per = Parse(@"{""case"":""MODAL"",""units"":""SI"",""items"":{""1"":{""T"":0.8},""2"":{""T"":0.3}}}", "periods");
            var tp = ResultsTable.Build(per, "periods", null, new ResultsConverter("m"), "m", 1);
            Assert.Equal(new[] { "T1", "T2" }, tp.Labels);
            Assert.Equal(new[] { 0.8, 0.3 }, tp.Values.Single().Items);
        }

        [Fact]
        public void DeformationOfMeshesAndPoints()
        {
            var mesh = new ExchangeObject
            {
                Id = "m",
                Geom = new MeshGeometry { Vertices = { new Point3(0, 0, 0), new Point3(1000, 0, 0), new Point3(1000, 1000, 0) }, Faces = { new[] { 0, 1, 2 } } },
            };
            var nodes = new List<NodeValue>
            {
                new NodeValue { Position = new Point3(1, 1, 0), Values = new[] { 0, 0, -0.01, 0, 0, 0 } },
                new NodeValue { Position = new Point3(0, 0, 0), Values = new double[6] },
            };
            var g = Assert.IsType<MeshGeometry>(Deformation.Apply(mesh, nodes, 2.0, "mm"));
            Assert.Equal(0, g.Vertices[0].Z, 9);
            Assert.Equal(-20, g.Vertices[2].Z, 9);   // nearest node (1,1,0) m = (1000,1000,0) mm, -0.01 m × 2
            Assert.Equal(-20, g.Vertices[1].Z, 9);   // equidistant from both nodes → the first listed wins

            var pt = new ExchangeObject { Id = "p", Geom = new PointGeometry(new Point3(0, 0, 0)) };
            var dp = Assert.IsType<PointGeometry>(Deformation.Apply(pt, new List<NodeValue> { new NodeValue { Values = new[] { 0.1, 0, 0, 0, 0, 0 } } }, 1, "m"));
            Assert.Equal(0.1, dp.P.X, 9);
            Assert.Null(Deformation.Apply(pt, new List<NodeValue>(), 1, "m"));
        }
    }
}
