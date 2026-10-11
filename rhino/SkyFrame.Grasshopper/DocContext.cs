using Rhino;
using SkyFrame.Exchange;

namespace SkyFrame.Grasshopper
{
    /// <summary>Units and tolerances of the active Rhino document.</summary>
    internal static class DocContext
    {
        public static RhinoDoc Doc => RhinoDoc.ActiveDoc;

        public static DocumentUnits Units()
        {
            var d = Doc;
            if (d == null) return Exchange.Units.ResolveDocument("meters", 1.0);
            var us = d.ModelUnitSystem;
            return Exchange.Units.ResolveDocument(us.ToString(), RhinoMath.UnitScale(us, UnitSystem.Meters));
        }

        public static double Tolerance => Doc?.ModelAbsoluteTolerance > 0 ? Doc.ModelAbsoluteTolerance : 0.001;

        public static double AngleTolerance => Doc?.ModelAngleToleranceRadians > 0 ? Doc.ModelAngleToleranceRadians : RhinoMath.ToRadians(1.0);

        public static string DocName => Doc?.Name ?? "";
    }
}
