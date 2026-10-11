// Payload DTOs of spec §1 ("skyframe.exchange/1").

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using SkyFrame.Exchange.Json;

namespace SkyFrame.Exchange.Model
{
    public static class ObjectKinds
    {
        public const string Curve = "curve";
        public const string Surface = "surface";
        public const string Mesh = "mesh";
        public const string Point = "point";
    }

    public static class Roles
    {
        public const string Column = "column", Beam = "beam", Brace = "brace", Frame = "frame",
            Wall = "wall", Slab = "slab", Shell = "shell", Support = "support", Joint = "joint", Link = "link";

        public static readonly string[] All = { Column, Beam, Brace, Frame, Wall, Slab, Shell, Support, Joint, Link };
        public static readonly string[] FrameRoles = { Column, Beam, Brace, Frame };
        public static readonly string[] ShellRoles = { Wall, Slab, Shell };

        /// <summary>
        /// Normalises user input. "auto", "" and null mean "let SkyFrame decide" and return null
        /// (the "role" key is then omitted). Throws on anything outside <paramref name="allowed"/>.
        /// </summary>
        public static string Normalize(string role, string[] allowed = null)
        {
            if (role == null) return null;
            var r = role.Trim().ToLowerInvariant();
            if (r.Length == 0 || r == "auto") return null;
            var set = allowed ?? All;
            if (Array.IndexOf(set, r) < 0)
                throw new ArgumentException($"role '{role}' is not one of: auto, {string.Join(", ", set)}");
            return r;
        }
    }

    /// <summary>The optional "props" block. Null members are not serialized.</summary>
    public sealed class ObjectProps
    {
        public string Section { get; set; }
        public string Material { get; set; }
        public string Story { get; set; }
        public double? Angle { get; set; }
        public bool[] ReleaseI { get; set; }
        public bool[] ReleaseJ { get; set; }
        public bool[] Restraint { get; set; }
        public string ShellSection { get; set; }
        public double? Thickness { get; set; }
        public double? MeshSize { get; set; }

        public bool IsEmpty => ToJson().Count == 0;

        public JsonObject ToJson()
        {
            var o = new JsonObject();
            if (!string.IsNullOrEmpty(Section)) o.Set("section", Section);
            if (!string.IsNullOrEmpty(Material)) o.Set("material", Material);
            if (!string.IsNullOrEmpty(Story)) o.Set("story", Story);
            if (Angle.HasValue) o.Set("angle", Angle.Value);
            if (ReleaseI != null) o.Set("release_i", JsonArray.Of(Check6(ReleaseI, "release_i")));
            if (ReleaseJ != null) o.Set("release_j", JsonArray.Of(Check6(ReleaseJ, "release_j")));
            if (Restraint != null) o.Set("restraint", JsonArray.Of(Check6(Restraint, "restraint")));
            if (!string.IsNullOrEmpty(ShellSection)) o.Set("shell_section", ShellSection);
            if (Thickness.HasValue) o.Set("thickness", Thickness.Value);
            if (MeshSize.HasValue) o.Set("mesh_size", MeshSize.Value);
            return o;
        }

        private static bool[] Check6(bool[] v, string name)
        {
            if (v.Length != 6) throw new ArgumentException($"{name} needs exactly 6 values");
            return v;
        }

        public static ObjectProps FromJson(JsonValue v)
        {
            var p = new ObjectProps();
            if (!(v is JsonObject o)) return p;
            p.Section = o["section"]?.AsString();
            p.Material = o["material"]?.AsString();
            p.Story = o["story"]?.AsString();
            if (o["angle"] is JsonNumber a) p.Angle = a.Value;
            p.ReleaseI = Bools(o["release_i"]);
            p.ReleaseJ = Bools(o["release_j"]);
            p.Restraint = Bools(o["restraint"]);
            p.ShellSection = o["shell_section"]?.AsString();
            if (o["thickness"] is JsonNumber t) p.Thickness = t.Value;
            if (o["mesh_size"] is JsonNumber m) p.MeshSize = m.Value;
            return p;
        }

        private static bool[] Bools(JsonValue v) => v is JsonArray a ? a.Select(x => x.AsBool()).ToArray() : null;

        public ObjectProps Clone() => new ObjectProps
        {
            Section = Section, Material = Material, Story = Story, Angle = Angle,
            ReleaseI = (bool[])ReleaseI?.Clone(), ReleaseJ = (bool[])ReleaseJ?.Clone(),
            Restraint = (bool[])Restraint?.Clone(), ShellSection = ShellSection,
            Thickness = Thickness, MeshSize = MeshSize,
        };
    }

    /// <summary>One entry of "objects".</summary>
    public sealed class ExchangeObject
    {
        public string Id { get; set; }
        public string Layer { get; set; } = "";
        public Dictionary<string, string> Attrs { get; set; } = new Dictionary<string, string>();
        /// <summary>Explicit role or null (omitted; SkyFrame resolves it).</summary>
        public string Role { get; set; }
        public ObjectProps Props { get; set; } = new ObjectProps();
        public Geometry Geom { get; set; }

        public string Kind => Geom?.Kind ?? throw new InvalidOperationException("object has no geometry");

        /// <summary>
        /// Not serialized. True for ids that must never be rewritten (referenced Rhino objects:
        /// their id is the object GUID). False for Grasshopper-generated geometry, whose id is
        /// assigned by SF Send as "&lt;SendComponentGuid&gt;/&lt;path&gt;/&lt;index&gt;".
        /// </summary>
        public bool IdLocked { get; set; }

        /// <summary>Not serialized. Layer colour (#RRGGBB) for the "layers" table, when known.</summary>
        public string LayerColor { get; set; }

        public JsonObject ToJson()
        {
            if (string.IsNullOrEmpty(Id)) throw new InvalidOperationException("object id is empty");
            var attrs = new JsonObject();
            foreach (var kv in Attrs.OrderBy(k => k.Key, StringComparer.Ordinal)) attrs.Set(kv.Key, kv.Value ?? "");
            var o = new JsonObject()
                .Set("id", Id)
                .Set("kind", Kind)
                .Set("layer", Layer ?? "")
                .Set("attrs", attrs);
            if (!string.IsNullOrEmpty(Role)) o.Set("role", Role);
            var props = (Props ?? new ObjectProps()).ToJson();
            if (props.Count > 0) o.Set("props", props);
            o.Set("geom", Geom.ToJson());
            return o;
        }

        public static ExchangeObject FromJson(JsonValue v)
        {
            var o = v as JsonObject ?? throw new FormatException("object must be a JSON object");
            var obj = new ExchangeObject
            {
                Id = o["id"]?.AsString() ?? throw new FormatException("object.id missing"),
                Layer = o["layer"]?.AsString() ?? "",
                Role = o["role"]?.AsString(),
                Props = ObjectProps.FromJson(o["props"]),
                Geom = Geometry.FromJson(o["geom"]),
            };
            if (o["attrs"] is JsonObject a) foreach (var kv in a) obj.Attrs[kv.Key] = kv.Value.AsString("");
            var kind = o["kind"]?.AsString();
            if (kind != null && kind != obj.Geom.Kind)
                throw new FormatException($"object '{obj.Id}': kind '{kind}' does not match geom.type '{obj.Geom.Type}'");
            return obj;
        }

        /// <summary>Shallow-ish copy: geometry is immutable by convention and is shared.</summary>
        public ExchangeObject Clone() => new ExchangeObject
        {
            Id = Id, Layer = Layer, Attrs = new Dictionary<string, string>(Attrs), Role = Role,
            Props = Props?.Clone() ?? new ObjectProps(), Geom = Geom, IdLocked = IdLocked, LayerColor = LayerColor,
        };

        public override string ToString() => $"SF {Role ?? "auto"} {Geom?.Type} [{Id}]";
    }

    public sealed class SourceInfo
    {
        public string App { get; set; } = "grasshopper";
        public string Doc { get; set; } = "";
        public string Units { get; set; } = "m";
        public double Tolerance { get; set; } = 0.001;

        public JsonObject ToJson() => new JsonObject()
            .Set("app", App).Set("doc", Doc ?? "").Set("units", Units).Set("tolerance", Tolerance);

        public static SourceInfo FromJson(JsonValue v) => new SourceInfo
        {
            App = v?["app"]?.AsString() ?? "grasshopper",
            Doc = v?["doc"]?.AsString() ?? "",
            Units = v?["units"]?.AsString() ?? "m",
            Tolerance = v?["tolerance"]?.AsDouble(0.001) ?? 0.001,
        };
    }

    public sealed class LayerInfo
    {
        public string Path { get; set; }
        public string Color { get; set; } = "#808080";
        public JsonObject ToJson() => new JsonObject().Set("path", Path).Set("color", Color);
    }

    public sealed class ExchangePayload
    {
        public const string SchemaId = "skyframe.exchange/1";

        public SourceInfo Source { get; set; } = new SourceInfo();
        public string Session { get; set; } = "";
        public List<LayerInfo> Layers { get; set; } = new List<LayerInfo>();
        public List<ExchangeObject> Objects { get; set; } = new List<ExchangeObject>();

        public JsonObject ToJson()
        {
            var layers = new JsonArray();
            foreach (var l in Layers) layers.Add(l.ToJson());
            var objs = new JsonArray();
            foreach (var o in Objects) objs.Add(o.ToJson());
            return new JsonObject()
                .Set("schema", SchemaId)
                .Set("source", Source.ToJson())
                .Set("session", Session ?? "")
                .Set("layers", layers)
                .Set("objects", objs);
        }

        public string ToJsonString(bool indented = false) => ToJson().ToJson(indented);

        public static ExchangePayload FromJson(JsonValue v)
        {
            var o = v as JsonObject ?? throw new FormatException("payload must be an object");
            var schema = o["schema"]?.AsString();
            if (schema != SchemaId) throw new FormatException($"unsupported schema '{schema}'");
            var p = new ExchangePayload
            {
                Source = SourceInfo.FromJson(o["source"]),
                Session = o["session"]?.AsString() ?? "",
            };
            if (o["layers"] is JsonArray ls)
                foreach (var l in ls)
                    p.Layers.Add(new LayerInfo { Path = l["path"]?.AsString() ?? "", Color = l["color"]?.AsString() ?? "#808080" });
            if (o["objects"] is JsonArray os)
                foreach (var x in os) p.Objects.Add(ExchangeObject.FromJson(x));
            return p;
        }

        public static ExchangePayload Parse(string json) => FromJson(JsonValue.Parse(json));

        /// <summary>Stable content hash (FNV-1a 64 over the compact JSON) used for change detection / debounce.</summary>
        public string ContentHash()
        {
            const ulong offset = 14695981039346656037UL, prime = 1099511628211UL;
            ulong h = offset;
            foreach (char c in ToJsonString())
            {
                h ^= c;
                h *= prime;
            }
            return h.ToString("x16", CultureInfo.InvariantCulture);
        }
    }
}
