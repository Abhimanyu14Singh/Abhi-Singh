using System;
using System.Collections.Generic;

namespace SkyFrame.Exchange
{
    /// <summary>Length units of spec §1 ("m","mm","cm","ft","in") and conversions.</summary>
    public static class Units
    {
        public static readonly string[] Supported = { "m", "mm", "cm", "ft", "in" };

        private static readonly Dictionary<string, double> MetersPer = new Dictionary<string, double>(StringComparer.OrdinalIgnoreCase)
        {
            { "m", 1.0 }, { "mm", 0.001 }, { "cm", 0.01 }, { "ft", 0.3048 }, { "in", 0.0254 },
        };

        public static bool IsSupported(string code) => code != null && MetersPer.ContainsKey(code);

        public static double MetersPerUnit(string code)
        {
            if (code == null || !MetersPer.TryGetValue(code, out var f)) throw new ArgumentException($"unsupported unit '{code}'");
            return f;
        }

        public static double Convert(double value, string from, string to) => value * MetersPerUnit(from) / MetersPerUnit(to);

        /// <summary>
        /// Maps a Rhino <c>UnitSystem</c> name to a spec unit code. Rhino units outside the spec's
        /// set (km, yards, microns, ...) are exported in metres: <see cref="DocumentUnits.CoordinateScale"/>
        /// then converts document coordinates into the exported unit.
        /// </summary>
        public static DocumentUnits ResolveDocument(string rhinoUnitSystem, double metersPerDocUnit)
        {
            string code;
            switch ((rhinoUnitSystem ?? "").Trim().ToLowerInvariant())
            {
                case "meters": case "m": code = "m"; break;
                case "millimeters": case "mm": code = "mm"; break;
                case "centimeters": case "cm": code = "cm"; break;
                case "feet": case "ft": code = "ft"; break;
                case "inches": case "in": code = "in"; break;
                default: code = null; break;
            }
            if (!(metersPerDocUnit > 0) || double.IsInfinity(metersPerDocUnit))
            {
                if (code == null) throw new ArgumentException($"unknown Rhino unit system '{rhinoUnitSystem}' with no scale");
                metersPerDocUnit = MetersPerUnit(code);
            }
            if (code == null) return new DocumentUnits("m", metersPerDocUnit, metersPerDocUnit);
            return new DocumentUnits(code, 1.0, metersPerDocUnit);
        }
    }

    public sealed class DocumentUnits
    {
        public DocumentUnits(string code, double coordinateScale, double metersPerDocUnit)
        {
            Code = code; CoordinateScale = coordinateScale; MetersPerDocUnit = metersPerDocUnit;
        }

        /// <summary>The spec unit code written to source.units.</summary>
        public string Code { get; }
        /// <summary>Multiply document coordinates by this to get <see cref="Code"/> units (1 for supported units).</summary>
        public double CoordinateScale { get; }
        /// <summary>Length of one Rhino document unit in metres.</summary>
        public double MetersPerDocUnit { get; }

        /// <summary>SI result length (m) → document units.</summary>
        public double FromMeters(double meters) => meters / MetersPerDocUnit;
        /// <summary>Document length → SI metres.</summary>
        public double ToMeters(double docValue) => docValue * MetersPerDocUnit;
        /// <summary>Exported (source.units) length → document units.</summary>
        public double FromExported(double v) => v / CoordinateScale;
    }
}
