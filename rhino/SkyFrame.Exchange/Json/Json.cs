// Minimal, dependency-free JSON DOM + parser + writer.
//
// SkyFrame.Exchange deliberately avoids Newtonsoft.Json / System.Text.Json so the
// .gha never fights other Grasshopper plugins (or Rhino itself) over which
// version of a JSON assembly is loaded into the shared AppDomain / ALC.
// The DOM keeps object key order, which keeps payloads diff-friendly.

using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace SkyFrame.Exchange.Json
{
    public enum JsonKind { Null, Bool, Number, String, Array, Object }

    /// <summary>Base class of every JSON value.</summary>
    public abstract class JsonValue
    {
        public abstract JsonKind Kind { get; }

        public static readonly JsonValue Null = new JsonNull();

        public static JsonValue From(string s) => s == null ? Null : new JsonString(s);
        public static JsonValue From(double d) => new JsonNumber(d);
        public static JsonValue From(bool b) => b ? JsonBool.True : JsonBool.False;

        public string ToJson(bool indented = false)
        {
            var sb = new StringBuilder();
            JsonWriter.Write(this, sb, indented, 0);
            return sb.ToString();
        }

        public override string ToString() => ToJson();

        public static JsonValue Parse(string text) => new JsonParser(text).ParseDocument();

        // ---- convenience accessors (lenient: wrong type -> default) ----
        public virtual JsonValue this[string key] => null;
        public virtual JsonValue this[int index] => null;

        public double AsDouble(double fallback = 0.0)
        {
            if (this is JsonNumber n) return n.Value;
            if (this is JsonBool b) return b.Value ? 1 : 0;
            if (this is JsonString s && double.TryParse(s.Value, NumberStyles.Float, CultureInfo.InvariantCulture, out var d)) return d;
            return fallback;
        }

        public int AsInt(int fallback = 0) => this is JsonNumber || this is JsonString ? (int)Math.Round(AsDouble(fallback)) : fallback;

        public bool AsBool(bool fallback = false)
        {
            if (this is JsonBool b) return b.Value;
            if (this is JsonNumber n) return Math.Abs(n.Value) > 0;
            if (this is JsonString s)
            {
                var v = s.Value.Trim().ToLowerInvariant();
                if (v == "true" || v == "1" || v == "yes") return true;
                if (v == "false" || v == "0" || v == "no") return false;
            }
            return fallback;
        }

        public string AsString(string fallback = null)
        {
            switch (this)
            {
                case JsonString s: return s.Value;
                case JsonNumber n: return n.Value.ToString("R", CultureInfo.InvariantCulture);
                case JsonBool b: return b.Value ? "true" : "false";
                default: return fallback;
            }
        }

        public JsonObject AsObject() => this as JsonObject;
        public JsonArray AsArray() => this as JsonArray;
        public bool IsNull => Kind == JsonKind.Null;
    }

    public sealed class JsonNull : JsonValue
    {
        internal JsonNull() { }
        public override JsonKind Kind => JsonKind.Null;
    }

    public sealed class JsonBool : JsonValue
    {
        public static readonly JsonBool True = new JsonBool(true);
        public static readonly JsonBool False = new JsonBool(false);
        private JsonBool(bool v) { Value = v; }
        public bool Value { get; }
        public override JsonKind Kind => JsonKind.Bool;
    }

    public sealed class JsonNumber : JsonValue
    {
        public JsonNumber(double v) { Value = v; }
        public double Value { get; }
        public override JsonKind Kind => JsonKind.Number;
    }

    public sealed class JsonString : JsonValue
    {
        public JsonString(string v) { Value = v ?? ""; }
        public string Value { get; }
        public override JsonKind Kind => JsonKind.String;
    }

    public sealed class JsonArray : JsonValue, IEnumerable<JsonValue>
    {
        private readonly List<JsonValue> _items = new List<JsonValue>();
        public JsonArray() { }
        public JsonArray(IEnumerable<JsonValue> items) { foreach (var i in items) Add(i); }
        public override JsonKind Kind => JsonKind.Array;
        public int Count => _items.Count;
        public override JsonValue this[int index] => index >= 0 && index < _items.Count ? _items[index] : null;
        public JsonArray Add(JsonValue v) { _items.Add(v ?? Null); return this; }
        public JsonArray Add(double v) => Add(new JsonNumber(v));
        public JsonArray Add(string v) => Add(From(v));
        public JsonArray Add(bool v) => Add(From(v));
        public IEnumerator<JsonValue> GetEnumerator() => _items.GetEnumerator();
        IEnumerator IEnumerable.GetEnumerator() => _items.GetEnumerator();

        public static JsonArray Of(IEnumerable<double> values)
        {
            var a = new JsonArray();
            foreach (var v in values) a.Add(v);
            return a;
        }

        public static JsonArray Of(IEnumerable<bool> values)
        {
            var a = new JsonArray();
            foreach (var v in values) a.Add(v);
            return a;
        }
    }

    public sealed class JsonObject : JsonValue, IEnumerable<KeyValuePair<string, JsonValue>>
    {
        private readonly List<string> _order = new List<string>();
        private readonly Dictionary<string, JsonValue> _map = new Dictionary<string, JsonValue>(StringComparer.Ordinal);
        public override JsonKind Kind => JsonKind.Object;
        public int Count => _order.Count;
        public IEnumerable<string> Keys => _order;

        public override JsonValue this[string key] => key != null && _map.TryGetValue(key, out var v) ? v : null;

        public JsonObject Set(string key, JsonValue value)
        {
            if (!_map.ContainsKey(key)) _order.Add(key);
            _map[key] = value ?? Null;
            return this;
        }
        public JsonObject Set(string key, string value) => Set(key, From(value));
        public JsonObject Set(string key, double value) => Set(key, new JsonNumber(value));
        public JsonObject Set(string key, bool value) => Set(key, From(value));

        public bool Has(string key) => key != null && _map.ContainsKey(key);
        public bool TryGet(string key, out JsonValue value) => _map.TryGetValue(key, out value);

        public bool Remove(string key)
        {
            if (!_map.Remove(key)) return false;
            _order.Remove(key);
            return true;
        }

        public IEnumerator<KeyValuePair<string, JsonValue>> GetEnumerator()
        {
            foreach (var k in _order) yield return new KeyValuePair<string, JsonValue>(k, _map[k]);
        }
        IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
    }

    /// <summary>Thrown on malformed JSON text.</summary>
    public sealed class JsonParseException : FormatException
    {
        public JsonParseException(string message, int position) : base($"{message} at offset {position}") { Position = position; }
        public int Position { get; }
    }

    internal sealed class JsonParser
    {
        private readonly string _s;
        private int _i;
        private int _depth;

        public JsonParser(string s) { _s = s ?? throw new ArgumentNullException(nameof(s)); }

        public JsonValue ParseDocument()
        {
            SkipWs();
            var v = ParseValue();
            SkipWs();
            if (_i != _s.Length) throw new JsonParseException("Unexpected trailing characters", _i);
            return v;
        }

        private void SkipWs()
        {
            while (_i < _s.Length && (_s[_i] == ' ' || _s[_i] == '\t' || _s[_i] == '\n' || _s[_i] == '\r' || _s[_i] == '﻿')) _i++;
        }

        private JsonValue ParseValue()
        {
            if (_i >= _s.Length) throw new JsonParseException("Unexpected end of input", _i);
            char c = _s[_i];
            switch (c)
            {
                case '{': return ParseObject();
                case '[': return ParseArray();
                case '"': return new JsonString(ParseString());
                case 't': Expect("true"); return JsonBool.True;
                case 'f': Expect("false"); return JsonBool.False;
                case 'n': Expect("null"); return JsonValue.Null;
                // Python's json module emits these for non-finite floats; accept them leniently.
                case 'N': Expect("NaN"); return new JsonNumber(double.NaN);
                case 'I': Expect("Infinity"); return new JsonNumber(double.PositiveInfinity);
                default:
                    if (c == '-' || (c >= '0' && c <= '9')) return ParseNumber();
                    throw new JsonParseException($"Unexpected character '{c}'", _i);
            }
        }

        private void Expect(string word)
        {
            if (string.CompareOrdinal(_s, _i, word, 0, word.Length) != 0) throw new JsonParseException($"Expected '{word}'", _i);
            _i += word.Length;
        }

        private JsonValue ParseNumber()
        {
            int start = _i;
            if (_s[_i] == '-')
            {
                _i++;
                if (_i < _s.Length && _s[_i] == 'I') { Expect("Infinity"); return new JsonNumber(double.NegativeInfinity); }
            }
            while (_i < _s.Length && "0123456789+-.eE".IndexOf(_s[_i]) >= 0) _i++;
            var text = _s.Substring(start, _i - start);
            if (!double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out var d))
                throw new JsonParseException($"Bad number '{text}'", start);
            return new JsonNumber(d);
        }

        private string ParseString()
        {
            _i++; // opening quote
            var sb = new StringBuilder();
            while (true)
            {
                if (_i >= _s.Length) throw new JsonParseException("Unterminated string", _i);
                char c = _s[_i++];
                if (c == '"') break;
                if (c != '\\') { sb.Append(c); continue; }
                if (_i >= _s.Length) throw new JsonParseException("Bad escape", _i);
                char e = _s[_i++];
                switch (e)
                {
                    case '"': sb.Append('"'); break;
                    case '\\': sb.Append('\\'); break;
                    case '/': sb.Append('/'); break;
                    case 'b': sb.Append('\b'); break;
                    case 'f': sb.Append('\f'); break;
                    case 'n': sb.Append('\n'); break;
                    case 'r': sb.Append('\r'); break;
                    case 't': sb.Append('\t'); break;
                    case 'u':
                        if (_i + 4 > _s.Length) throw new JsonParseException("Bad \\u escape", _i);
                        sb.Append((char)int.Parse(_s.Substring(_i, 4), NumberStyles.HexNumber, CultureInfo.InvariantCulture));
                        _i += 4;
                        break;
                    default: throw new JsonParseException($"Bad escape '\\{e}'", _i - 1);
                }
            }
            return sb.ToString();
        }

        private JsonArray ParseArray()
        {
            if (++_depth > 512) throw new JsonParseException("Nesting too deep", _i);
            _i++;
            var a = new JsonArray();
            SkipWs();
            if (_i < _s.Length && _s[_i] == ']') { _i++; _depth--; return a; }
            while (true)
            {
                SkipWs();
                a.Add(ParseValue());
                SkipWs();
                if (_i >= _s.Length) throw new JsonParseException("Unterminated array", _i);
                if (_s[_i] == ',') { _i++; continue; }
                if (_s[_i] == ']') { _i++; break; }
                throw new JsonParseException("Expected ',' or ']'", _i);
            }
            _depth--;
            return a;
        }

        private JsonObject ParseObject()
        {
            if (++_depth > 512) throw new JsonParseException("Nesting too deep", _i);
            _i++;
            var o = new JsonObject();
            SkipWs();
            if (_i < _s.Length && _s[_i] == '}') { _i++; _depth--; return o; }
            while (true)
            {
                SkipWs();
                if (_i >= _s.Length || _s[_i] != '"') throw new JsonParseException("Expected property name", _i);
                var key = ParseString();
                SkipWs();
                if (_i >= _s.Length || _s[_i] != ':') throw new JsonParseException("Expected ':'", _i);
                _i++;
                SkipWs();
                o.Set(key, ParseValue());
                SkipWs();
                if (_i >= _s.Length) throw new JsonParseException("Unterminated object", _i);
                if (_s[_i] == ',') { _i++; continue; }
                if (_s[_i] == '}') { _i++; break; }
                throw new JsonParseException("Expected ',' or '}'", _i);
            }
            _depth--;
            return o;
        }
    }

    internal static class JsonWriter
    {
        public static string FormatNumber(double d)
        {
            // The spec requires plain JSON numbers: never emit NaN/Infinity.
            if (double.IsNaN(d) || double.IsInfinity(d)) return "0";
            if (d == Math.Floor(d) && Math.Abs(d) < 1e15) return ((long)d).ToString(CultureInfo.InvariantCulture);
            return d.ToString("R", CultureInfo.InvariantCulture);
        }

        public static void Write(JsonValue v, StringBuilder sb, bool indented, int level)
        {
            switch (v)
            {
                case null:
                case JsonNull _: sb.Append("null"); break;
                case JsonBool b: sb.Append(b.Value ? "true" : "false"); break;
                case JsonNumber n: sb.Append(FormatNumber(n.Value)); break;
                case JsonString s: WriteString(s.Value, sb); break;
                case JsonArray a:
                    sb.Append('[');
                    bool scalarOnly = true;
                    foreach (var item in a) if (item is JsonArray || item is JsonObject) { scalarOnly = false; break; }
                    int i = 0;
                    foreach (var item in a)
                    {
                        if (i++ > 0) sb.Append(indented && scalarOnly ? ", " : ",");
                        if (indented && !scalarOnly) NewLine(sb, level + 1);
                        Write(item, sb, indented, level + 1);
                    }
                    if (indented && !scalarOnly && a.Count > 0) NewLine(sb, level);
                    sb.Append(']');
                    break;
                case JsonObject o:
                    sb.Append('{');
                    int k = 0;
                    foreach (var kv in o)
                    {
                        if (k++ > 0) sb.Append(',');
                        if (indented) NewLine(sb, level + 1);
                        WriteString(kv.Key, sb);
                        sb.Append(indented ? ": " : ":");
                        Write(kv.Value, sb, indented, level + 1);
                    }
                    if (indented && o.Count > 0) NewLine(sb, level);
                    sb.Append('}');
                    break;
            }
        }

        private static void NewLine(StringBuilder sb, int level)
        {
            sb.Append('\n');
            sb.Append(' ', level * 2);
        }

        public static void WriteString(string s, StringBuilder sb)
        {
            sb.Append('"');
            foreach (char c in s)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    case '\b': sb.Append("\\b"); break;
                    case '\f': sb.Append("\\f"); break;
                    default:
                        if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                        else sb.Append(c);
                        break;
                }
            }
            sb.Append('"');
        }
    }

    /// <summary>Structural equality used by tests and change detection.</summary>
    public static class JsonCompare
    {
        public static bool DeepEquals(JsonValue a, JsonValue b, double tol = 1e-9) => Diff(a, b, tol, "$") == null;

        /// <summary>Returns null when equal, otherwise a JSON-path style description of the first difference.</summary>
        public static string Diff(JsonValue a, JsonValue b, double tol = 1e-9, string path = "$")
        {
            a = a ?? JsonValue.Null;
            b = b ?? JsonValue.Null;
            if (a.Kind != b.Kind) return $"{path}: kind {a.Kind} != {b.Kind}";
            switch (a)
            {
                case JsonNull _: return null;
                case JsonBool x: return x.Value == ((JsonBool)b).Value ? null : $"{path}: {x.Value} != {((JsonBool)b).Value}";
                case JsonNumber x:
                    var y = ((JsonNumber)b).Value;
                    return Math.Abs(x.Value - y) <= tol * Math.Max(1.0, Math.Abs(y)) ? null : $"{path}: {x.Value} != {y}";
                case JsonString x: return x.Value == ((JsonString)b).Value ? null : $"{path}: \"{x.Value}\" != \"{((JsonString)b).Value}\"";
                case JsonArray x:
                    var ya = (JsonArray)b;
                    if (x.Count != ya.Count) return $"{path}: length {x.Count} != {ya.Count}";
                    for (int i = 0; i < x.Count; i++)
                    {
                        var d = Diff(x[i], ya[i], tol, $"{path}[{i}]");
                        if (d != null) return d;
                    }
                    return null;
                case JsonObject x:
                    var yo = (JsonObject)b;
                    foreach (var k in x.Keys) if (!yo.Has(k)) return $"{path}.{k}: missing on right";
                    foreach (var k in yo.Keys) if (!x.Has(k)) return $"{path}.{k}: missing on left";
                    foreach (var k in x.Keys)
                    {
                        var d = Diff(x[k], yo[k], tol, $"{path}.{k}");
                        if (d != null) return d;
                    }
                    return null;
            }
            return null;
        }
    }
}
