using System;
using System.Linq;

namespace SkyFrame.Exchange
{
    /// <summary>
    /// Parses 6-DOF patterns typed by users. Order is the spec order:
    /// restraints [UX,UY,UZ,RX,RY,RZ]; releases [P,V2,V3,T,M2,M3] (same as UserText "000011").
    /// Accepted forms: "111000", "1,1,1,0,0,0", "true,true,true,false,false,false",
    /// "x"-style digit strings, and named presets.
    /// </summary>
    public static class Patterns
    {
        public static bool[] Restraint(string text)
        {
            if (string.IsNullOrWhiteSpace(text)) return null;
            switch (text.Trim().ToLowerInvariant())
            {
                case "fixed": case "fix": return new[] { true, true, true, true, true, true };
                case "pinned": case "pin": return new[] { true, true, true, false, false, false };
                case "roller": return new[] { false, false, true, false, false, false };
                case "free": case "none": return new[] { false, false, false, false, false, false };
            }
            return Bits(text, "restraint");
        }

        public static bool[] Release(string text)
        {
            if (string.IsNullOrWhiteSpace(text)) return null;
            switch (text.Trim().ToLowerInvariant())
            {
                case "none": case "fixed": case "rigid": return new bool[6];
                case "pinned": case "pin": case "moment": return new[] { false, false, false, false, true, true };
                case "torsion": return new[] { false, false, false, true, false, false };
            }
            return Bits(text, "release");
        }

        public static string ToBits(bool[] v) => v == null ? "" : new string(v.Select(b => b ? '1' : '0').ToArray());

        private static bool[] Bits(string text, string what)
        {
            var t = text.Trim();
            string[] tokens = t.IndexOfAny(new[] { ',', ' ', ';' }) >= 0
                ? t.Split(new[] { ',', ' ', ';' }, StringSplitOptions.RemoveEmptyEntries)
                : t.Select(c => c.ToString()).ToArray();
            if (tokens.Length != 6) throw new ArgumentException($"{what} pattern '{text}' needs 6 values, e.g. 111000");
            return tokens.Select(tok =>
            {
                switch (tok.Trim().ToLowerInvariant())
                {
                    case "1": case "true": case "t": case "x": case "y": return true;
                    case "0": case "false": case "f": case "-": case "n": return false;
                    default: throw new ArgumentException($"{what} pattern '{text}': bad value '{tok}'");
                }
            }).ToArray();
        }
    }
}
