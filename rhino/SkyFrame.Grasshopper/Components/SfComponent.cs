using System;
using System.Collections.Generic;
using System.Drawing;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Data;
using Grasshopper.Kernel.Types;
using SkyFrame.Exchange;
using SkyFrame.Exchange.Client;
using SkyFrame.Grasshopper.Goo;

namespace SkyFrame.Grasshopper.Components
{
    public abstract class SfComponent : GH_Component
    {
        public const string TabName = "SkyFrame";
        public const string SubLink = "1 Link";
        public const string SubObjects = "2 Objects";

        protected SfComponent(string name, string nickname, string description, string subCategory)
            : base(name, nickname, description, TabName, subCategory) { }

        protected abstract string IconName { get; }
        protected override Bitmap Icon => IconLoader.Get(IconName);
        public override GH_Exposure Exposure => GH_Exposure.primary;

        // ---------- input helpers ----------

        protected string Text(IGH_DataAccess da, int i, string fallback = "")
        {
            string s = null;
            return da.GetData(i, ref s) && s != null ? s : fallback;
        }

        protected double? Number(IGH_DataAccess da, int i)
        {
            double d = 0;
            return da.GetData(i, ref d) ? d : (double?)null;
        }

        protected bool Bool(IGH_DataAccess da, int i, bool fallback = false)
        {
            bool b = fallback;
            return da.GetData(i, ref b) ? b : fallback;
        }

        protected static string NullIfEmpty(string s) => string.IsNullOrWhiteSpace(s) ? null : s.Trim();

        /// <summary>Reads a tree of SF objects as (path, index, object) items.</summary>
        protected List<TreeItem> ReadObjects(IGH_DataAccess da, int i)
        {
            var items = new List<TreeItem>();
            if (!da.GetDataTree(i, out GH_Structure<IGH_Goo> tree) || tree == null) return items;
            for (int b = 0; b < tree.PathCount; b++)
            {
                var path = tree.Paths[b];
                var branch = tree.Branches[b];
                for (int k = 0; k < branch.Count; k++)
                {
                    var obj = SfObjectGoo.Unwrap(branch[k]);
                    if (obj == null && branch[k] != null)
                        AddRuntimeMessage(GH_RuntimeMessageLevel.Warning, $"{path}[{k}] is not a SkyFrame object (use SF Frame / SF Shell / SF Support / SF Layer Objects)");
                    items.Add(new TreeItem(path.Indices, k, obj));
                }
            }
            return items;
        }

        /// <summary>Re-solve on the UI thread (called from worker-thread completions).</summary>
        protected void ExpireOnUiThread()
        {
            Rhino.RhinoApp.InvokeOnUiThread((Action)(() =>
            {
                var doc = OnPingDocument();
                if (doc == null) return;
                // schedule rather than expire inline, so we never re-enter a running solution
                doc.ScheduleSolution(5, d => ExpireSolution(false));
            }));
        }

        protected static SkyFrameClient Client(string url) => SkyFrameClient.For(url);

        protected static string ErrorText(Exception ex)
        {
            while (ex is AggregateException ae && ae.InnerException != null) ex = ae.InnerException;
            return ex?.Message ?? "";
        }
    }
}
