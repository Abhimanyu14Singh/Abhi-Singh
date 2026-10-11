using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

namespace SkyFrame.Exchange
{
    /// <summary>
    /// Stable object ids (spec §1):
    ///  * referenced Rhino objects → the object GUID ("d" format, lower case);
    ///    a multi-face brep yields one object per face → "&lt;guid&gt;/&lt;faceIndex&gt;";
    ///  * Grasshopper-generated geometry → "&lt;SendComponentGuid&gt;/&lt;path&gt;/&lt;index&gt;", with the
    ///    branch path written as its integers joined by ';' (GH_Path "{0;1}" → "0;1").
    /// Sessions are "&lt;definitionGuid&gt;/&lt;SendComponentGuid&gt;" unless the user names one.
    /// </summary>
    public static class StableIds
    {
        public static string FormatGuid(Guid g) => g.ToString("D").ToLowerInvariant();

        public static string FormatPath(IEnumerable<int> path)
        {
            var parts = (path ?? Enumerable.Empty<int>()).Select(i => i.ToString(CultureInfo.InvariantCulture)).ToArray();
            return parts.Length == 0 ? "0" : string.Join(";", parts);
        }

        public static string ForGrasshopper(Guid sendComponent, IEnumerable<int> path, int index)
        {
            if (index < 0) throw new ArgumentOutOfRangeException(nameof(index));
            return $"{FormatGuid(sendComponent)}/{FormatPath(path)}/{index.ToString(CultureInfo.InvariantCulture)}";
        }

        public static string ForRhinoObject(Guid objectId) => FormatGuid(objectId);

        public static string ForRhinoObjectPart(Guid objectId, int part, int partCount) =>
            partCount <= 1 ? FormatGuid(objectId) : $"{FormatGuid(objectId)}/{part.ToString(CultureInfo.InvariantCulture)}";

        public static string Session(Guid definition, Guid sendComponent) => $"{FormatGuid(definition)}/{FormatGuid(sendComponent)}";

        /// <summary>Parses a GH id; returns false for Rhino object ids or malformed text.</summary>
        public static bool TryParseGrasshopper(string id, out Guid component, out int[] path, out int index)
        {
            component = Guid.Empty; path = null; index = -1;
            if (string.IsNullOrEmpty(id)) return false;
            var parts = id.Split('/');
            if (parts.Length != 3 || !Guid.TryParse(parts[0], out component)) return false;
            var pp = parts[1].Trim('{', '}').Split(';');
            var list = new List<int>();
            foreach (var p in pp)
            {
                if (!int.TryParse(p, NumberStyles.Integer, CultureInfo.InvariantCulture, out var v)) return false;
                list.Add(v);
            }
            if (!int.TryParse(parts[2], NumberStyles.Integer, CultureInfo.InvariantCulture, out index)) return false;
            path = list.ToArray();
            return true;
        }
    }
}
