using System;
using Grasshopper.Kernel;
using Rhino.Geometry;
using SkyFrame.Exchange;
using SkyFrame.Exchange.Model;
using SkyFrame.Grasshopper.Goo;

namespace SkyFrame.Grasshopper.Components
{
    public sealed class FrameComponent : SfComponent
    {
        public FrameComponent()
            : base("SF Frame", "SFFrame", "Frame members (columns, beams, braces) from lines, polylines, arcs and NURBS curves.", SubObjects) { }

        public override Guid ComponentGuid => new Guid("b4d6f1c3-2e5a-4d7f-9bac-1e2f3a4b5c62");
        protected override string IconName => "Frame";

        protected override void RegisterInputParams(GH_InputParamManager p)
        {
            p.AddCurveParameter("Curve", "C", "Line, polyline, arc or NURBS curve (polylines → one member per segment)", GH_ParamAccess.item);
            p.AddTextParameter("Role", "R", "auto | column | beam | brace | frame (auto: SkyFrame decides from orientation / UserText / layer mapping)", GH_ParamAccess.item, "auto");
            p.AddTextParameter("Section", "S", "Frame section name (must exist in SkyFrame, or is resolved by the mapping)", GH_ParamAccess.item);
            p.AddTextParameter("Material", "M", "Material name", GH_ParamAccess.item);
            p.AddNumberParameter("Angle", "A", "Local-axis rotation angle (degrees)", GH_ParamAccess.item);
            p.AddTextParameter("Release I", "Ri", "End-I releases [P,V2,V3,T,M2,M3]: e.g. 000011, 'pinned', 'none'", GH_ParamAccess.item);
            p.AddTextParameter("Release J", "Rj", "End-J releases [P,V2,V3,T,M2,M3]", GH_ParamAccess.item);
            p.AddTextParameter("Story", "St", "Story name (optional; default: SkyFrame assigns)", GH_ParamAccess.item);
            for (int i = 2; i < 8; i++) p[i].Optional = true;
        }

        protected override void RegisterOutputParams(GH_OutputParamManager p)
        {
            p.AddGenericParameter("Objects", "O", "SkyFrame objects (wire into SF Send)", GH_ParamAccess.item);
            p.AddTextParameter("Type", "T", "Exchange geometry type: line / polyline / arc / nurbs", GH_ParamAccess.item);
        }

        protected override void SolveInstance(IGH_DataAccess da)
        {
            Curve crv = null;
            if (!da.GetData(0, ref crv) || crv == null) return;
            ExchangeObject obj;
            try
            {
                var cs = ConvertSettings.FromDocument();
                var geom = RhinoConvert.FromCurve(crv, cs);
                if (geom == null) { AddRuntimeMessage(GH_RuntimeMessageLevel.Error, "Curve could not be converted"); return; }
                obj = new ExchangeObject
                {
                    Geom = geom,
                    Role = Roles.Normalize(Text(da, 1, "auto"), Roles.FrameRoles),
                    Props = new ObjectProps
                    {
                        Section = NullIfEmpty(Text(da, 2)),
                        Material = NullIfEmpty(Text(da, 3)),
                        Angle = Number(da, 4),
                        ReleaseI = Patterns.Release(Text(da, 5)),
                        ReleaseJ = Patterns.Release(Text(da, 6)),
                        Story = NullIfEmpty(Text(da, 7)),
                    },
                };
                da.SetData(1, geom.Type);
                da.SetData(0, new SfObjectGoo(obj, 1.0 / cs.Scale));
            }
            catch (ArgumentException ex)
            {
                AddRuntimeMessage(GH_RuntimeMessageLevel.Error, ex.Message);
            }
        }
    }
}
