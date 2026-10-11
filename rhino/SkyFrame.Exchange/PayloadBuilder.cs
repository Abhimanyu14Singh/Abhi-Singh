using System;
using System.Collections.Generic;
using System.Linq;
using SkyFrame.Exchange.Model;

namespace SkyFrame.Exchange
{
    /// <summary>An object plus its position in the SF Send input tree.</summary>
    public sealed class TreeItem
    {
        public TreeItem(int[] path, int index, ExchangeObject obj) { Path = path; Index = index; Object = obj; }
        public int[] Path { get; }
        public int Index { get; }
        public ExchangeObject Object { get; }
    }

    public sealed class BuildResult
    {
        public ExchangePayload Payload { get; set; }
        /// <summary>The objects actually sent, in input order, aligned with the input items (null where dropped).</summary>
        public List<ExchangeObject> Assigned { get; set; } = new List<ExchangeObject>();
        public List<string> Warnings { get; } = new List<string>();
    }

    /// <summary>
    /// Assembles the payload SF Send posts: assigns stable ids to Grasshopper-generated objects,
    /// keeps locked ids (Rhino GUIDs), fills default layers and builds the "layers" table.
    /// </summary>
    public static class PayloadBuilder
    {
        public const string DefaultLayer = "Grasshopper";
        public const string DefaultLayerColor = "#808080";

        public static BuildResult Build(SourceInfo source, string session, Guid sendComponent,
                                        IEnumerable<TreeItem> items, string defaultLayer = DefaultLayer)
        {
            var result = new BuildResult();
            var payload = new ExchangePayload { Source = source, Session = session ?? "" };
            var seen = new HashSet<string>(StringComparer.Ordinal);
            var layers = new Dictionary<string, string>(StringComparer.Ordinal);
            var layerOrder = new List<string>();
            var dl = string.IsNullOrWhiteSpace(defaultLayer) ? DefaultLayer : defaultLayer.Trim();

            foreach (var it in items ?? Enumerable.Empty<TreeItem>())
            {
                if (it?.Object?.Geom == null)
                {
                    result.Assigned.Add(null);
                    continue;
                }
                var o = it.Object.Clone();
                if (!o.IdLocked || string.IsNullOrEmpty(o.Id))
                    o.Id = StableIds.ForGrasshopper(sendComponent, it.Path, it.Index);
                if (string.IsNullOrEmpty(o.Layer)) o.Layer = dl;

                var problem = Validate(o);
                if (problem != null)
                {
                    result.Warnings.Add($"{o.Id}: {problem} — skipped");
                    result.Assigned.Add(null);
                    continue;
                }
                if (!seen.Add(o.Id))
                {
                    result.Warnings.Add($"duplicate id {o.Id} — only the first object is sent");
                    result.Assigned.Add(null);
                    continue;
                }
                if (!layers.ContainsKey(o.Layer))
                {
                    layers[o.Layer] = string.IsNullOrEmpty(o.LayerColor) ? DefaultLayerColor : o.LayerColor;
                    layerOrder.Add(o.Layer);
                }
                payload.Objects.Add(o);
                result.Assigned.Add(o);
            }
            foreach (var l in layerOrder) payload.Layers.Add(new LayerInfo { Path = l, Color = layers[l] });
            result.Payload = payload;
            return result;
        }

        /// <summary>Returns null when the geometry is usable, else a reason.</summary>
        public static string Validate(ExchangeObject o)
        {
            foreach (var p in o.Geom.AllPoints())
                if (double.IsNaN(p.X) || double.IsNaN(p.Y) || double.IsNaN(p.Z) ||
                    double.IsInfinity(p.X) || double.IsInfinity(p.Y) || double.IsInfinity(p.Z))
                    return "non-finite coordinate";
            switch (o.Geom)
            {
                case PolylineGeometry pl when pl.Points.Count < 2: return "polyline needs 2+ points";
                case NurbsCurveGeometry n when n.Samples.Count < 2: return "nurbs curve has no samples";
                case PolygonGeometry pg when pg.Outer.Count < 3: return "polygon needs 3+ outer points";
                case MeshGeometry m when m.Faces.Count == 0: return "mesh has no faces";
                case MeshGeometry m when m.Faces.Any(f => f.Length < 3 || f.Length > 4 || f.Any(i => i < 0 || i >= m.Vertices.Count)):
                    return "mesh has invalid faces";
                case NurbsSurfaceGeometry s when s.Mesh == null || s.Mesh.Faces.Count == 0: return "nurbs_surface has no mesh";
            }
            return null;
        }
    }
}
