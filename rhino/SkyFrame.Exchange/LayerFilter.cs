using System;
using System.Text.RegularExpressions;

namespace SkyFrame.Exchange
{
    /// <summary>Matches Rhino layer full paths ("Structure::Beams") against a user pattern.</summary>
    public static class LayerFilter
    {
        /// <param name="pattern">Exact full path, or a glob with * / ? (e.g. "Structure::*"). Case-insensitive.</param>
        /// <param name="fullPath">Layer full path.</param>
        /// <param name="includeSublayers">Also match children of a matching layer.</param>
        public static bool Matches(string pattern, string fullPath, bool includeSublayers)
        {
            if (string.IsNullOrWhiteSpace(pattern) || fullPath == null) return false;
            var pat = pattern.Trim();
            if (Glob(pat, fullPath)) return true;
            if (!includeSublayers) return false;
            // test every ancestor of fullPath
            int idx = fullPath.LastIndexOf("::", StringComparison.Ordinal);
            while (idx > 0)
            {
                var parent = fullPath.Substring(0, idx);
                if (Glob(pat, parent)) return true;
                idx = parent.LastIndexOf("::", StringComparison.Ordinal);
            }
            return false;
        }

        private static bool Glob(string pattern, string text)
        {
            if (pattern.IndexOfAny(new[] { '*', '?' }) < 0) return string.Equals(pattern, text, StringComparison.OrdinalIgnoreCase);
            var rx = "^" + Regex.Escape(pattern).Replace("\\*", ".*").Replace("\\?", ".") + "$";
            return Regex.IsMatch(text, rx, RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
        }
    }
}
