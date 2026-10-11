using System;
using Grasshopper.Kernel;
using Rhino.Geometry;
using SkyFrame.Exchange;
using SkyFrame.Exchange.Model;
using SkyFrame.Grasshopper.Goo;

namespace SkyFrame.Grasshopper.Components
{
    /// <summary>Shared body of SF Support and SF Joint: a point + 6-DOF restraint pattern.</summary>
    public abstract class PointObjectComponent : SfComponent
    {
        protected PointObjectComponent(string name, string nick, string desc) : base(name, nick, desc, SubObjects) { }

        protected abstract string Role { get; }
        protected abstract string DefaultRestraint { get; }

        protected override void RegisterInputParams(GH_InputParamManager p)
        {
            p.AddPointParameter("Point", "P", "Joint location", GH_ParamAccess.item);
            const string desc = "[UX,UY,UZ,RX,RY,RZ] e.g. 111111, 111000, or fixed / pinned / roller / free";
            var i = DefaultRestraint != null
                ? p.AddTextParameter("Restraint", "R", desc, GH_ParamAccess.item, DefaultRestraint)
                : p.AddTextParameter("Restraint", "R", desc, GH_ParamAccess.item);
            p[i].Optional = true;
        }

        protected override void RegisterOutputParams(GH_OutputParamManager p)
        {
            p.AddGenericParameter("Objects", "O", "SkyFrame objects (wire into SF Send)", GH_ParamAccess.item);
        }

        protected override void SolveInstance(IGH_DataAccess da)
        {
            var pt = Point3d.Unset;
            if (!da.GetData(0, ref pt) || !pt.IsValid) return;
            try
            {
                var restraint = Patterns.Restraint(Text(da, 1, DefaultRestraint ?? ""));
                var cs = ConvertSettings.FromDocument();
                var obj = new ExchangeObject
                {
                    Geom = new PointGeometry(RhinoConvert.P(pt, cs.Scale)),
                    Role = Role,
                    Props = new ObjectProps { Restraint = restraint },
                };
                Message = restraint == null ? "" : Patterns.ToBits(restraint);
                da.SetData(0, new SfObjectGoo(obj, 1.0 / cs.Scale));
            }
            catch (ArgumentException ex)
            {
                AddRuntimeMessage(GH_RuntimeMessageLevel.Error, ex.Message);
            }
        }
    }

    public sealed class SupportComponent : PointObjectComponent
    {
        public SupportComponent() : base("SF Support", "SFSupport", "Support (restrained joint) at a point. Default: fixed (111111).") { }
        public override Guid ComponentGuid => new Guid("d6f8b3e5-4a7c-4f9b-9dce-3a4b5c6d7e84");
        protected override string IconName => "Support";
        protected override string Role => Roles.Support;
        protected override string DefaultRestraint => "111111";
    }

    public sealed class JointComponent : PointObjectComponent
    {
        public JointComponent() : base("SF Joint", "SFJoint", "Explicit joint at a point (e.g. to split members or carry loads), optional restraint.") { }
        public override Guid ComponentGuid => new Guid("e7a9c4f6-5b8d-4a0c-8edf-4b5c6d7e8f95");
        protected override string IconName => "Joint";
        protected override string Role => Roles.Joint;
        protected override string DefaultRestraint => null;
    }
}
