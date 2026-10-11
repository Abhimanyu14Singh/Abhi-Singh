using System;
using System.Collections.Generic;
using System.Linq;
using Grasshopper.Kernel;
using Rhino;
using Rhino.DocObjects;
using Rhino.Geometry;
using SkyFrame.Exchange;
using SkyFrame.Exchange.Model;
using SkyFrame.Grasshopper.Goo;
using SfGeometry = SkyFrame.Exchange.Model.Geometry;

namespace SkyFrame.Grasshopper.Components
{
    /// <summary>
    /// References every object on a Rhino layer (optionally its sublayers) with its UserText.
    /// Ids are the Rhino object GUIDs (multi-face breps: "&lt;guid&gt;/&lt;face&gt;"), so the layer can be
    /// sent as-is and SkyFrame keeps its assignments across edits.
    /// </summary>
    public sealed class LayerObjectsComponent : SfComponent
    {
        private bool _subscribed;

        public LayerObjectsComponent()
            : base("SF Layer Objects", "SFLayer", "References all Rhino objects on a layer (curves, surfaces/breps, meshes, points) with their UserText; ids are the object GUIDs.", SubObjects) { }

        public override Guid ComponentGuid => new Guid("f8b0d5a7-6c9e-4b1d-9f0a-5c6d7e8f9a06");
        protected override string IconName => "Layer";

        protected override void RegisterInputParams(GH_InputParamManager p)
        {
            p.AddTextParameter("Layer", "L", "Layer full path(s), e.g. Structure::Beams; * and ? wildcards allowed", GH_ParamAccess.list);
            p.AddBooleanParameter("Sublayers", "S", "Include sublayers", GH_ParamAccess.item, true);
            p.AddBooleanParameter("Live", "Lv", "Re-read automatically when Rhino objects change", GH_ParamAccess.item, true);
            p.AddNumberParameter("Mesh Size", "MS", "Mesh size (document units) for freeform surfaces; 0 = automatic", GH_ParamAccess.item, 0.0);
            p.AddBooleanParameter("Refresh", "R", "Re-read now", GH_ParamAccess.item, false);
        }

        protected override void RegisterOutputParams(GH_OutputParamManager p)
        {
            p.AddGenericParameter("Objects", "O", "SkyFrame objects (wire into SF Send)", GH_ParamAccess.list);
            p.AddTextParameter("Ids", "I", "Object ids (Rhino GUIDs)", GH_ParamAccess.list);
            p.AddTextParameter("Layers", "Ls", "Matched layer paths", GH_ParamAccess.list);
        }

        protected override void SolveInstance(IGH_DataAccess da)
        {
            var patterns = new List<string>();
            da.GetDataList(0, patterns);
            var sub = Bool(da, 1, true);
            var live = Bool(da, 2, true);
            var meshSize = Number(da, 3) ?? 0;
            Subscribe(live);

            var doc = RhinoDoc.ActiveDoc;
            if (doc == null) { AddRuntimeMessage(GH_RuntimeMessageLevel.Warning, "No active Rhino document"); return; }

            var layers = doc.Layers.Where(l => l != null && !l.IsDeleted && patterns.Any(pt => LayerFilter.Matches(pt, l.FullPath, sub))).ToList();
            if (layers.Count == 0) { AddRuntimeMessage(GH_RuntimeMessageLevel.Warning, "No layer matches"); return; }
            var byIndex = layers.ToDictionary(l => l.Index);

            var cs = ConvertSettings.FromDocument(meshSize);
            var settings = new ObjectEnumeratorSettings
            {
                NormalObjects = true, LockedObjects = true, HiddenObjects = true,
                IncludeLights = false, IncludeGrips = false, DeletedObjects = false,
            };
            var goos = new List<SfObjectGoo>();
            var ids = new List<string>();
            var warnings = new List<string>();
            int skipped = 0;

            foreach (var ro in doc.Objects.GetObjectList(settings))
            {
                if (!byIndex.TryGetValue(ro.Attributes.LayerIndex, out var layer)) continue;
                var geoms = ToExchange(ro.Geometry, cs, warnings);
                if (geoms.Count == 0) { skipped++; continue; }
                var attrs = UserText(ro);
                for (int k = 0; k < geoms.Count; k++)
                {
                    var obj = new ExchangeObject
                    {
                        Id = StableIds.ForRhinoObjectPart(ro.Id, k, geoms.Count),
                        IdLocked = true,
                        Layer = layer.FullPath,
                        LayerColor = RhinoConvert.HexColor(layer.Color),
                        Attrs = new Dictionary<string, string>(attrs),
                        Geom = geoms[k],
                    };
                    goos.Add(new SfObjectGoo(obj, 1.0 / cs.Scale));
                    ids.Add(obj.Id);
                }
            }
            foreach (var w in warnings.Distinct()) AddRuntimeMessage(GH_RuntimeMessageLevel.Remark, w);
            if (skipped > 0) AddRuntimeMessage(GH_RuntimeMessageLevel.Remark, $"{skipped} unsupported object(s) skipped (blocks, text, solids...)");
            Message = $"{goos.Count} obj";
            da.SetDataList(0, goos);
            da.SetDataList(1, ids);
            da.SetDataList(2, layers.Select(l => l.FullPath));
        }

        private static List<SfGeometry> ToExchange(GeometryBase g, ConvertSettings cs, List<string> warnings)
        {
            switch (g)
            {
                case Curve c:
                    var cg = RhinoConvert.FromCurve(c, cs);
                    return cg == null ? new List<SfGeometry>() : new List<SfGeometry> { cg };
                case Rhino.Geometry.Point p:
                    return new List<SfGeometry> { new PointGeometry(RhinoConvert.P(p.Location, cs.Scale)) };
                case Mesh _:
                case Brep _:
                case Extrusion _:
                case Surface _:
                case SubD _:
                    return RhinoConvert.FromSurfaceLike(g, cs, warnings);
            }
            return new List<SfGeometry>();
        }

        private static Dictionary<string, string> UserText(RhinoObject ro)
        {
            var d = new Dictionary<string, string>();
            void Add(System.Collections.Specialized.NameValueCollection nv)
            {
                if (nv == null) return;
                foreach (string k in nv.AllKeys) if (k != null) d[k] = nv[k];
            }
            Add(ro.Geometry?.GetUserStrings());   // geometry UserText first...
            Add(ro.Attributes.GetUserStrings());  // ...object (attribute) UserText wins
            return d;
        }

        // ---------- live updates ----------

        private void Subscribe(bool on)
        {
            if (on == _subscribed) return;
            _subscribed = on;
            if (on)
            {
                RhinoDoc.AddRhinoObject += OnDocChanged;
                RhinoDoc.DeleteRhinoObject += OnDocChanged;
                RhinoDoc.UndeleteRhinoObject += OnDocChanged;
                RhinoDoc.ReplaceRhinoObject += OnDocChanged;
                RhinoDoc.ModifyObjectAttributes += OnDocChanged;
                RhinoDoc.LayerTableEvent += OnDocChanged;
            }
            else
            {
                RhinoDoc.AddRhinoObject -= OnDocChanged;
                RhinoDoc.DeleteRhinoObject -= OnDocChanged;
                RhinoDoc.UndeleteRhinoObject -= OnDocChanged;
                RhinoDoc.ReplaceRhinoObject -= OnDocChanged;
                RhinoDoc.ModifyObjectAttributes -= OnDocChanged;
                RhinoDoc.LayerTableEvent -= OnDocChanged;
            }
        }

        private void OnDocChanged(object sender, EventArgs e)
        {
            var doc = OnPingDocument();
            if (doc == null || doc.SolutionState == GH_ProcessStep.Process) return;
            // coalesce bursts (a drag fires many events): one re-solve after 300 ms
            doc.ScheduleSolution(300, d => ExpireSolution(false));
        }

        public override void RemovedFromDocument(GH_Document document)
        {
            Subscribe(false);
            base.RemovedFromDocument(document);
        }

        public override void DocumentContextChanged(GH_Document document, GH_DocumentContext context)
        {
            if (context == GH_DocumentContext.Unloaded || context == GH_DocumentContext.Close) Subscribe(false);
            base.DocumentContextChanged(document, context);
        }
    }
}
