using System;
using System.Linq;
using Grasshopper.Kernel;
using SkyFrame.Exchange.Client;

namespace SkyFrame.Grasshopper.Components
{
    public sealed class ConnectComponent : SfComponent
    {
        private readonly AsyncCache<StatusInfo> _status = new AsyncCache<StatusInfo>();
        private int _refreshCount;
        private bool _prevRefresh;

        public ConnectComponent()
            : base("SF Connect", "SFConnect", "Checks the connection to a running SkyFrame app (GET /api/exchange/status).", SubLink) { }

        public override Guid ComponentGuid => new Guid("a3c5e0b2-1f4d-4c6e-8a9b-0d1e2f3a4b51");
        protected override string IconName => "Connect";

        protected override void RegisterInputParams(GH_InputParamManager p)
        {
            p.AddTextParameter("Url", "U", $"SkyFrame address, e.g. {SkyFrameClient.DefaultUrl}, or just the port the desktop app prints", GH_ParamAccess.item, SkyFrameClient.DefaultUrl);
            p.AddBooleanParameter("Refresh", "R", "Re-query the status (connect a Button)", GH_ParamAccess.item, false);
        }

        protected override void RegisterOutputParams(GH_OutputParamManager p)
        {
            p.AddTextParameter("Status", "S", "Connection status", GH_ParamAccess.item);
            p.AddTextParameter("Model", "M", "Name of the model open in SkyFrame", GH_ParamAccess.item);
            p.AddBooleanParameter("Connected", "C", "True when SkyFrame answered", GH_ParamAccess.item);
            p.AddTextParameter("Url", "U", "Normalised address (wire into SF Send / SF Results)", GH_ParamAccess.item);
            p.AddTextParameter("Sessions", "Ss", "Exchange sessions known to SkyFrame (session | objects | last update)", GH_ParamAccess.list);
            p.AddBooleanParameter("Has Results", "HR", "True when SkyFrame holds analysis results", GH_ParamAccess.item);
        }

        protected override void SolveInstance(IGH_DataAccess da)
        {
            var url = SkyFrameClient.NormalizeBaseUrl(Text(da, 0, SkyFrameClient.DefaultUrl));
            var refresh = Bool(da, 1);
            if (refresh && !_prevRefresh) _refreshCount++;
            _prevRefresh = refresh;

            var key = $"{url}#{_refreshCount}";
            _status.Request(key, () => Client(url).GetStatusAsync(), ExpireOnUiThread);
            da.SetData(3, url);

            if (!_status.IsCurrent(key) || _status.Loading)
            {
                Message = "connecting…";
                da.SetData(0, $"Connecting to {url}…");
                return;
            }
            if (_status.Error != null)
            {
                Message = "offline";
                AddRuntimeMessage(GH_RuntimeMessageLevel.Warning, ErrorText(_status.Error));
                da.SetData(0, ErrorText(_status.Error));
                da.SetData(2, false);
                return;
            }
            var s = _status.Value;
            Message = "connected";
            da.SetData(0, $"{s.App} {s.Version} at {url} — model '{s.Model}', {s.Sessions.Count} session(s)");
            da.SetData(1, s.Model);
            da.SetData(2, true);
            da.SetDataList(4, s.Sessions.Select(x => $"{x.Session} | {x.Objects} | {x.LastUpdate}"));
            da.SetData(5, s.HasResults);
        }
    }
}
