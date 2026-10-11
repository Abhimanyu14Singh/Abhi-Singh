using System;
using System.Linq;
using Grasshopper;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Data;
using SkyFrame.Exchange;
using SkyFrame.Exchange.Client;
using SkyFrame.Exchange.Model;
using SkyFrame.Grasshopper.Goo;

namespace SkyFrame.Grasshopper.Components
{
    /// <summary>
    /// Builds the skyframe.exchange/1 payload and posts it to /api/exchange/preview or /apply.
    /// Sending is asynchronous (LiveSender): the solver never waits on the network; when the
    /// response arrives the component expires itself and shows the report.
    /// </summary>
    public sealed class SendComponent : SfComponent
    {
        public static readonly TimeSpan LiveDebounce = TimeSpan.FromMilliseconds(750);

        private readonly LiveSender _sender;
        private string _url = SkyFrameClient.DefaultUrl;
        private bool _prevSend;
        private int _revision;

        public SendComponent()
            : base("SF Send", "SFSend", "Sends SkyFrame objects to the SkyFrame app (preview or apply, merge or replace) and optionally runs the analysis.", SubLink)
        {
            _sender = new LiveSender(() => Client(_url), LiveDebounce);
            _sender.Completed += o =>
            {
                if (o.Ok && !o.Request.Preview) System.Threading.Interlocked.Increment(ref _revision);
                ExpireOnUiThread();
            };
        }

        public override Guid ComponentGuid => new Guid("0a1c2e3f-7d8b-4c2e-8a1b-6d7e8f9a0b17");
        protected override string IconName => "Send";

        protected override void RegisterInputParams(GH_InputParamManager p)
        {
            p.AddGenericParameter("Objects", "O", "SkyFrame objects (tree). Ids of GH geometry are <thisComponent>/<path>/<index>, so keep the tree stable", GH_ParamAccess.tree);
            p.AddTextParameter("Url", "U", "SkyFrame address (from SF Connect)", GH_ParamAccess.item, SkyFrameClient.DefaultUrl);
            p.AddTextParameter("Session", "Se", "Session name (default: definition GUID / this component GUID)", GH_ParamAccess.item);
            p.AddTextParameter("Mode", "M", "merge (keep SkyFrame-side assignments) | replace (drop and recreate the session's objects)", GH_ParamAccess.item, "merge");
            p.AddBooleanParameter("Preview", "Pv", "True: dry run (/api/exchange/preview), the model is untouched", GH_ParamAccess.item, false);
            p.AddBooleanParameter("Send", "S", "Send now (Button or Toggle; a Toggle re-sends only when the payload changes)", GH_ParamAccess.item, false);
            p.AddBooleanParameter("Auto", "A", "Live link: send automatically (debounced) whenever the payload changes", GH_ParamAccess.item, false);
            p.AddBooleanParameter("Run", "R", "Run the analysis after applying", GH_ParamAccess.item, false);
            p.AddTextParameter("Mapping", "Mp", "Mapping profile name stored in the SkyFrame model (optional)", GH_ParamAccess.item);
            p.AddTextParameter("Layer", "L", "Layer path given to Grasshopper objects (default 'Grasshopper')", GH_ParamAccess.item, PayloadBuilder.DefaultLayer);
            p[2].Optional = true;
            p[8].Optional = true;
        }

        protected override void RegisterOutputParams(GH_OutputParamManager p)
        {
            p.AddTextParameter("Report", "Rp", "Result of the last send", GH_ParamAccess.item);
            p.AddIntegerParameter("Created", "C", "Objects created (sum over members/shells/joints)", GH_ParamAccess.item);
            p.AddIntegerParameter("Updated", "U", "Objects updated", GH_ParamAccess.item);
            p.AddIntegerParameter("Deleted", "D", "Objects deleted", GH_ParamAccess.item);
            p.AddTextParameter("Warnings", "W", "Warnings from SkyFrame and from payload building", GH_ParamAccess.list);
            p.AddTextParameter("Session", "Se", "Session id (wire into SF Results)", GH_ParamAccess.item);
            p.AddGenericParameter("Objects", "O", "The objects as sent, with their final ids, same tree as the input (wire into SF Results)", GH_ParamAccess.tree);
            p.AddIntegerParameter("Revision", "Rv", "Increments after every successful apply (wire into SF Results to refresh)", GH_ParamAccess.item);
            p.AddTextParameter("Payload", "J", "The JSON payload (for debugging)", GH_ParamAccess.item);
        }

        protected override void SolveInstance(IGH_DataAccess da)
        {
            _url = SkyFrameClient.NormalizeBaseUrl(Text(da, 1, SkyFrameClient.DefaultUrl));
            var ghDoc = OnPingDocument();
            var session = NullIfEmpty(Text(da, 2)) ?? StableIds.Session(ghDoc?.DocumentID ?? Guid.Empty, InstanceGuid);
            var modeText = Text(da, 3, "merge").Trim().ToLowerInvariant();
            if (modeText != "merge" && modeText != "replace")
            {
                AddRuntimeMessage(GH_RuntimeMessageLevel.Error, "Mode must be 'merge' or 'replace'");
                return;
            }
            var preview = Bool(da, 4);
            var send = Bool(da, 5);
            var auto = Bool(da, 6);
            var run = Bool(da, 7);
            var mapping = SkyFrameClient.MappingByName(Text(da, 8));
            var layer = Text(da, 9, PayloadBuilder.DefaultLayer);

            // ---- build payload ----
            var items = ReadObjects(da, 0);
            var units = DocContext.Units();
            var source = new SourceInfo
            {
                App = "grasshopper",
                Doc = ghDoc?.DisplayName ?? DocContext.DocName,
                Units = units.Code,
                Tolerance = DocContext.Tolerance * units.CoordinateScale,
            };
            var built = PayloadBuilder.Build(source, session, InstanceGuid, items, layer);

            var tree = new DataTree<SfObjectGoo>();
            for (int i = 0; i < items.Count; i++)
            {
                var path = new GH_Path(items[i].Path);
                var o = built.Assigned[i];
                tree.Add(o == null ? null : new SfObjectGoo(o, 1.0 / units.CoordinateScale), path);
            }

            var request = new SendRequest
            {
                Payload = built.Payload,
                Preview = preview,
                Mode = modeText == "replace" ? ApplyMode.Replace : ApplyMode.Merge,
                Run = run,
                Mapping = mapping,
            };

            // ---- decide whether to send (never blocks) ----
            bool rising = send && !_prevSend;
            _prevSend = send;
            if (built.Payload.Objects.Count == 0 && (rising || auto))
                AddRuntimeMessage(GH_RuntimeMessageLevel.Warning, "Nothing to send");
            else if (rising)
                _sender.Submit(request, immediate: true, force: true);
            else if (send || auto)
                _sender.Submit(request, immediate: false);  // no-op when identical to the last send

            // ---- outputs from the last completed send ----
            var last = _sender.LastOutcome;
            var warnings = built.Warnings.ToList();
            string report;
            int created = 0, updated = 0, deleted = 0;
            if (last == null)
                report = auto ? "Live link armed — waiting for changes" : "Not sent yet (press Send)";
            else if (!last.Ok)
            {
                report = "Send failed: " + ErrorText(last.Error);
                AddRuntimeMessage(GH_RuntimeMessageLevel.Error, report);
            }
            else if (last.Preview != null)
            {
                var s = last.Preview.Summary;
                created = s.TotalCreate; updated = s.TotalUpdate; deleted = s.TotalDelete;
                report = $"PREVIEW ({last.Finished:HH:mm:ss}) — model untouched\n" +
                         $"would create: {ChangeCounts.Describe(s.Create)}\nwould update: {ChangeCounts.Describe(s.Update)}\nwould delete: {ChangeCounts.Describe(s.Delete)}";
                warnings.AddRange(last.Preview.Warnings);
            }
            else
            {
                var r = last.Apply.Report;
                created = r.Counts.TotalCreate; updated = r.Counts.TotalUpdate; deleted = r.Counts.TotalDelete;
                report = $"APPLIED {(last.Request.Mode == ApplyMode.Replace ? "replace" : "merge")}{(last.Request.Run ? " + run" : "")} ({last.Finished:HH:mm:ss})\n{r.ToText()}";
                warnings.AddRange(r.Warnings);
            }

            Message = _sender.Busy ? "sending…" : (auto ? "live" : (last == null ? "" : (last.Ok ? "sent" : "error")));
            da.SetData(0, report);
            da.SetData(1, created);
            da.SetData(2, updated);
            da.SetData(3, deleted);
            da.SetDataList(4, warnings);
            da.SetData(5, session);
            da.SetDataTree(6, tree);
            da.SetData(7, _revision);
            da.SetData(8, built.Payload.ToJsonString(true));
        }

        public override void RemovedFromDocument(GH_Document document)
        {
            _sender.Cancel();
            base.RemovedFromDocument(document);
        }
    }
}
