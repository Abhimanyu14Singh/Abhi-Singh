using System;
using System.Collections.Generic;
using System.Linq;
using Grasshopper;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Data;
using Rhino.Geometry;
using SkyFrame.Exchange.Client;
using SkyFrame.Exchange.Results;

namespace SkyFrame.Grasshopper.Components
{
    /// <summary>
    /// Fetches GET /api/exchange/results asynchronously and returns data trees matched to the
    /// input objects' tree (branch {path;index} per object), in document units.
    /// </summary>
    public sealed class ResultsComponent : SfComponent
    {
        private readonly AsyncCache<ResultsResponse> _cache = new AsyncCache<ResultsResponse>();
        private bool _prevRefresh;
        private int _refreshCount;

        public ResultsComponent()
            : base("SF Results", "SFResults", "Analysis results from SkyFrame (displacements, member forces at stations, reactions, periods, drifts, story) matched to the sent objects, in document units, plus the deformed shape.", SubLink) { }

        public override Guid ComponentGuid => new Guid("1b2d3f4a-8e9c-4d3f-9b2c-7e8f9a0b1c28");
        protected override string IconName => "Results";

        protected override void RegisterInputParams(GH_InputParamManager p)
        {
            p.AddGenericParameter("Objects", "O", "Objects output of SF Send (tree)", GH_ParamAccess.tree);
            p.AddTextParameter("Url", "U", "SkyFrame address", GH_ParamAccess.item, SkyFrameClient.DefaultUrl);
            p.AddTextParameter("Session", "Se", "Session output of SF Send", GH_ParamAccess.item);
            p.AddTextParameter("Case", "C", "Load case / combination name (e.g. DEAD, 1.2D+1.6L)", GH_ParamAccess.item);
            p.AddTextParameter("Quantity", "Q", "displacements | member_forces | reactions | periods | drifts | story", GH_ParamAccess.item, Quantities.Displacements);
            p.AddNumberParameter("Scale", "Sc", "Deformed-shape scale factor", GH_ParamAccess.item, 50.0);
            p.AddIntegerParameter("Revision", "Rv", "Revision output of SF Send (re-fetches after every apply)", GH_ParamAccess.item, 0);
            p.AddBooleanParameter("Refresh", "R", "Re-fetch now", GH_ParamAccess.item, false);
            p[0].Optional = true;
            p[2].Optional = true;
            p[3].Optional = true;
        }

        protected override void RegisterOutputParams(GH_OutputParamManager p)
        {
            p.AddTextParameter("Ids", "I", "Object id per branch {path;index} (model-level: story names / mode numbers in {0})", GH_ParamAccess.tree);
            p.AddNumberParameter("Values", "V", "displacements/reactions: {path;i;node} → 6 values; member_forces: {path;i;component} → value per station; periods: {0}; drifts/story: {row}", GH_ParamAccess.tree);
            p.AddTextParameter("Labels", "L", "Names of the values (ux..rz | FX..MZ | N,V2,V3,T,M2,M3 | story keys | T1..)", GH_ParamAccess.list);
            p.AddNumberParameter("Stations", "St", "member_forces: station positions along each object (document units)", GH_ParamAccess.tree);
            p.AddVectorParameter("Vectors", "Vec", "displacements: translation per node (document units); reactions: force per node (kN)", GH_ParamAccess.tree);
            p.AddGeometryParameter("Deformed", "Df", "Deformed geometry (displacements × Scale)", GH_ParamAccess.tree);
            p.AddTextParameter("Info", "Inf", "Case, units and status", GH_ParamAccess.item);
        }

        protected override void SolveInstance(IGH_DataAccess da)
        {
            var url = SkyFrameClient.NormalizeBaseUrl(Text(da, 1, SkyFrameClient.DefaultUrl));
            var session = Text(da, 2, "");
            var resultCase = Text(da, 3, "");
            string what;
            try { what = Quantities.Normalize(Text(da, 4, Quantities.Displacements)); }
            catch (ArgumentException ex) { AddRuntimeMessage(GH_RuntimeMessageLevel.Error, ex.Message); return; }
            var scale = Number(da, 5) ?? 50.0;
            int revision = 0;
            da.GetData(6, ref revision);
            var refresh = Bool(da, 7);
            if (refresh && !_prevRefresh) _refreshCount++;
            _prevRefresh = refresh;

            if (string.IsNullOrWhiteSpace(session) && !Quantities.IsModelLevel(what))
            {
                AddRuntimeMessage(GH_RuntimeMessageLevel.Warning, "Connect the Session output of SF Send");
                return;
            }

            var key = string.Join("|", url, session, resultCase, what, revision, _refreshCount);
            _cache.Request(key, () => Client(url).GetResultsAsync(session, resultCase, what), ExpireOnUiThread);

            if (!_cache.IsCurrent(key))
            {
                Message = "loading…";
                da.SetData(6, "Loading results…");
                return;
            }
            if (_cache.Error != null)
            {
                Message = "error";
                AddRuntimeMessage(GH_RuntimeMessageLevel.Error, ErrorText(_cache.Error));
                da.SetData(6, ErrorText(_cache.Error));
                return;
            }
            Message = what;

            var units = DocContext.Units();
            var items = ReadObjects(da, 0);
            var table = ResultsTable.Build(_cache.Value, what, items, new ResultsConverter(units.MetersPerDocUnit), units.Code, scale);
            foreach (var w in table.Warnings) AddRuntimeMessage(GH_RuntimeMessageLevel.Warning, w);

            var ids = new DataTree<string>();
            foreach (var b in table.Ids) ids.AddRange(b.Items, new GH_Path(b.Path));
            var values = new DataTree<double>();
            foreach (var b in table.Values) values.AddRange(b.Items, new GH_Path(b.Path));
            var stations = new DataTree<double>();
            foreach (var b in table.Stations) stations.AddRange(b.Items, new GH_Path(b.Path));
            var vectors = new DataTree<Vector3d>();
            foreach (var b in table.Vectors) vectors.AddRange(b.Items.Select(v => new Vector3d(v.X, v.Y, v.Z)), new GH_Path(b.Path));
            var deformed = new DataTree<GeometryBase>();
            double toDoc = 1.0 / units.CoordinateScale;
            foreach (var b in table.Deformed)
                foreach (var g in b.Items) deformed.AddRange(RhinoConvert.ToRhino(g, toDoc), new GH_Path(b.Path));

            var len = Exchange.Units.IsSupported(units.Code) && units.CoordinateScale == 1.0 ? units.Code : "doc units";
            da.SetDataTree(0, ids);
            da.SetDataTree(1, values);
            da.SetDataList(2, table.Labels);
            da.SetDataTree(3, stations);
            da.SetDataTree(4, vectors);
            da.SetDataTree(5, deformed);
            da.SetData(6, $"case '{_cache.Value.Case}', {what}, server units {_cache.Value.Units} → lengths in {len}, forces kN, moments kN·m, rotations rad, periods s ({_cache.Updated:HH:mm:ss})");
        }
    }
}
