using System.Collections.Generic;
using System.Drawing;
using System.Linq;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Types;
using Rhino.Geometry;
using SkyFrame.Exchange.Model;

namespace SkyFrame.Grasshopper.Goo
{
    /// <summary>Grasshopper wrapper of one exchange object; previews its geometry in the viewport.</summary>
    public sealed class SfObjectGoo : GH_Goo<ExchangeObject>, IGH_PreviewData
    {
        private List<GeometryBase> _preview;

        public SfObjectGoo() { }

        /// <param name="value">Object with coordinates in exported units.</param>
        /// <param name="toDoc">Exported → document length factor (1/CoordinateScale).</param>
        public SfObjectGoo(ExchangeObject value, double toDoc = 1.0) : base(value) { ToDoc = toDoc; }

        public double ToDoc { get; private set; } = 1.0;

        public override bool IsValid => Value?.Geom != null;
        public override string TypeName => "SkyFrame Object";
        public override string TypeDescription => "A SkyFrame exchange object (geometry + role + properties)";
        public override IGH_Goo Duplicate() => new SfObjectGoo(Value?.Clone(), ToDoc);
        public override string ToString() => Value?.ToString() ?? "SF <null>";

        public static ExchangeObject Unwrap(object o)
        {
            switch (o)
            {
                case SfObjectGoo g: return g.Value;
                case ExchangeObject e: return e;
                case GH_ObjectWrapper w: return Unwrap(w.Value);
                case IGH_Goo goo: return Unwrap(goo.ScriptVariable());
            }
            return null;
        }

        // -------- preview --------

        private List<GeometryBase> Preview
        {
            get
            {
                if (_preview == null)
                    _preview = Value?.Geom == null ? new List<GeometryBase>() : RhinoConvert.ToRhino(Value.Geom, ToDoc);
                return _preview;
            }
        }

        public BoundingBox ClippingBox
        {
            get
            {
                var bb = BoundingBox.Empty;
                foreach (var g in Preview) bb.Union(g.GetBoundingBox(false));
                return bb;
            }
        }

        public void DrawViewportWires(GH_PreviewWireArgs args)
        {
            var color = RoleColor(Value?.Role, args.Color);
            foreach (var g in Preview)
            {
                switch (g)
                {
                    case Curve c: args.Pipeline.DrawCurve(c, color, args.Thickness + 1); break;
                    case Mesh m: args.Pipeline.DrawMeshWires(m, color); break;
                    case Rhino.Geometry.Point p: args.Pipeline.DrawPoint(p.Location, Rhino.Display.PointStyle.Triangle, 5, color); break;
                }
            }
        }

        public void DrawViewportMeshes(GH_PreviewMeshArgs args)
        {
            foreach (var m in Preview.OfType<Mesh>()) args.Pipeline.DrawMeshShaded(m, args.Material);
        }

        private static Color RoleColor(string role, Color fallback)
        {
            switch (role)
            {
                case Roles.Column: return Color.FromArgb(214, 96, 77);
                case Roles.Beam: return Color.FromArgb(67, 147, 195);
                case Roles.Brace: return Color.FromArgb(244, 165, 50);
                case Roles.Wall: return Color.FromArgb(120, 120, 200);
                case Roles.Slab: return Color.FromArgb(90, 170, 110);
                case Roles.Support: return Color.FromArgb(200, 40, 40);
                default: return fallback;
            }
        }
    }
}
