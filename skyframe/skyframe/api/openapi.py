"""Versioned REST API: ``/api/v1`` aliases + a generated OpenAPI 3 spec.

Registered by ``create_app`` with one line.

* **Versioning.**  Every ``/api/<path>`` endpoint is also served at
  ``/api/v1/<path>`` (a WSGI path rewrite in front of Flask, so the alias
  can never drift from the real route).  The unversioned paths stay as
  they are (the web UI uses them).
* **Spec.**  ``GET /api/openapi.json`` (also ``/api/v1/openapi.json``)
  returns an OpenAPI 3.0.3 document generated at request time from the
  live Flask URL map -- every route, plugin routes included -- with:

  - path parameters from the route converters;
  - a summary from the view docstring;
  - request / response schemas: exact component schemas for the core
    shapes (model, results, errors, exchange, plugins, interop) and,
    for every other endpoint, the ``{key, key?, ...}`` shapes parsed from
    its ``CONTRACT.md`` entries (``?`` = optional), with the CONTRACT
    text as the description.

:func:`build_spec` is the pure function (tests validate its structure).
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from flask import jsonify

OPENAPI_VERSION = "3.0.3"
API_PREFIX = "/api"
V1_PREFIX = "/api/v1"
_METHODS = ("get", "post", "put", "patch", "delete")
_CONTRACT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..",
                                          "..", "CONTRACT.md"))


# --------------------------------------------------------------------------- #
# /api/v1 alias
# --------------------------------------------------------------------------- #
class V1Alias:
    """WSGI middleware: ``/api/v1/<path>`` -> ``/api/<path>``."""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path == V1_PREFIX or path.startswith(V1_PREFIX + "/"):
            environ["PATH_INFO"] = API_PREFIX + path[len(V1_PREFIX):]
            environ["skyframe.api_version"] = "v1"
        return self.wsgi_app(environ, start_response)


# --------------------------------------------------------------------------- #
# component schemas (the core JSON shapes of CONTRACT.md)
# --------------------------------------------------------------------------- #
def _obj(props: Dict[str, Any], required: Optional[List[str]] = None,
         extra: bool = True, desc: str = "") -> dict:
    s: Dict[str, Any] = {"type": "object", "properties": props}
    if required:
        s["required"] = list(required)
    if extra:
        s["additionalProperties"] = True
    if desc:
        s["description"] = desc
    return s


_NUM = {"type": "number"}
_STR = {"type": "string"}
_INT = {"type": "integer"}
_BOOL = {"type": "boolean"}
_ANY: Dict[str, Any] = {}
_VEC3 = {"type": "array", "items": _NUM, "minItems": 3, "maxItems": 3}
_VEC6 = {"type": "array", "items": _NUM, "minItems": 6, "maxItems": 6}


def _ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


def _map(of: dict) -> dict:
    return {"type": "object", "additionalProperties": of}


COMPONENTS: Dict[str, dict] = {
    "Error": _obj({"error": _STR}, ["error"], desc="400 / 404 body"),
    "SoftError": _obj({"ok": {"type": "boolean", "enum": [False]},
                       "error": _STR}, ["ok", "error"]),
    "Health": _obj({"status": _STR, "opensees": _BOOL},
                   ["status", "opensees"]),
    "Material": _obj({"name": _STR, "E": _NUM, "nu": _NUM,
                      "unit_weight": _NUM, "material_type": _STR,
                      "fc": {"type": "number", "nullable": True},
                      "fy": {"type": "number", "nullable": True}},
                     ["name", "E"]),
    "FrameSection": _obj({"name": _STR, "material": _STR, "A": _NUM,
                          "I33": _NUM, "I22": _NUM, "J": _NUM, "b": _NUM,
                          "h": _NUM}, ["name", "material", "A", "I33",
                                       "I22", "J"]),
    "Story": _obj({"name": _STR, "height": _NUM, "elevation": _NUM},
                  ["name", "height"]),
    "FrameMember": _obj({"uid": _STR, "kind": {"type": "string", "enum": [
        "column", "beam", "brace"]}, "section": _STR, "pi": _VEC3,
        "pj": _VEC3, "story": _STR, "releases": _STR, "angle": _NUM},
        ["uid", "kind", "section", "pi", "pj"]),
    "ShellRegion": _obj({"uid": _STR, "kind": _STR, "behavior": _STR,
                         "section": _STR, "corners": {
                             "type": "array", "items": _VEC3},
                         "mesh_size": _NUM, "story": _STR},
                        ["uid", "kind", "section", "corners"]),
    "LoadPattern": _obj({"name": _STR, "kind": _STR,
                         "member_loads": {"type": "array", "items": _ANY},
                         "nodal_loads": {"type": "array", "items": _ANY},
                         "area_loads": {"type": "array", "items": _ANY},
                         "self_weight_factor": _NUM}, ["name"]),
    "Model": _obj({
        "name": _STR, "materials": _map(_ref("Material")),
        "sections": _map(_ref("FrameSection")),
        "shell_sections": _map(_ANY),
        "stories": {"type": "array", "items": _ref("Story")},
        "members": {"type": "array", "items": _ref("FrameMember")},
        "shells": {"type": "array", "items": _ref("ShellRegion")},
        "base_fixity": _STR,
        "supports": {"type": "array", "items": _obj(
            {"point": _VEC3, "restraints": {"type": "array",
                                            "items": _INT}})},
        "patterns": _map(_ref("LoadPattern")),
        "cases": _map(_obj({"name": _STR, "patterns": _map(_NUM)})),
        "combos": _map(_obj({"name": _STR, "cases": _map(_NUM),
                             "combo_type": _STR})),
        "num_modes": _INT}, ["name"],
        desc="BuildingModel.to_dict() (CONTRACT 'model.to_dict()'); SI "
             "units kN, m, t, s; every optional feature adds keys"),
    "CaseResults": _obj({
        "node_disp": _map(_VEC6), "reactions": _map(_VEC6),
        "base": _obj({k: _NUM for k in ("FX", "FY", "FZ", "MX", "MY",
                                        "MZ")}),
        "member_forces": _map({"type": "array", "items": _NUM,
                               "minItems": 12, "maxItems": 12}),
        "member_stations": _map(_obj({k: {"type": "array", "items": _NUM}
                                      for k in ("x", "N", "V2", "V3", "T",
                                                "M2", "M3")})),
        "story": _map(_obj({k: _NUM for k in ("ux", "uy", "drift_x",
                                              "drift_y", "shear_x",
                                              "shear_y")}))},
        ["node_disp", "reactions", "base", "member_forces"]),
    "ModalResults": _obj({
        "periods": {"type": "array", "items": _NUM},
        "frequencies": {"type": "array", "items": _NUM},
        "participation": {"type": "array", "items": _obj(
            {"mode": _INT, "T": _NUM, "ux": _NUM, "uy": _NUM, "rz": _NUM})},
        "shapes": _map(_map(_VEC6))}, ["periods", "frequencies"]),
    "AnalysisResults": _obj({
        "model_name": _STR, "nodes": _map(_VEC3),
        "members": {"type": "array", "items": _obj(
            {"uid": _STR, "kind": _STR, "section": _STR, "ni": _INT,
             "nj": _INT, "story": _STR})},
        "supports": {"type": "array", "items": _ANY},
        "story_order": {"type": "array", "items": _STR},
        "story_elev": _map(_NUM),
        "cases": _map(_ref("CaseResults")),
        "combos": _map(_ref("CaseResults")),
        "modal": _ref("ModalResults")},
        ["model_name", "nodes", "members", "cases"],
        desc="results.to_dict() (CONTRACT 'results.to_dict() JSON shape')"),
    "ExportResponse": _obj({"ok": _BOOL, "filename": _STR, "mime": _STR,
                            "content": _STR, "content_b64": _STR,
                            "report": _obj({"format": _STR})},
                           ["ok", "filename", "mime", "report"]),
    "ImportResponse": _obj({"ok": _BOOL, "model": _ref("Model"),
                            "warnings": {"type": "array", "items": _STR},
                            "report": _obj({"format": _STR})},
                           ["ok", "model", "warnings", "report"]),
    "Plugin": _obj({"id": _STR, "name": _STR, "version": _STR,
                    "description": _STR, "enabled": _BOOL,
                    "loaded": _BOOL, "error": {"type": "string",
                                               "nullable": True},
                    "menus": {"type": "array", "items": _ANY},
                    "js": {"type": "array", "items": _STR},
                    "hooks": {"type": "array", "items": _STR},
                    "endpoints": {"type": "array", "items": _STR},
                    "tables": {"type": "array", "items": _STR}},
                   ["id", "name", "enabled"]),
}


# exact request / response schemas: (method, rule) -> (req, resp, summary)
_JSON_MODEL = _ref("Model")
OVERRIDES: Dict[Tuple[str, str], Tuple[Optional[dict], Optional[dict],
                                       str]] = {
    ("get", "/api/health"): (None, _ref("Health"), "Server health"),
    ("get", "/api/model"): (None, _JSON_MODEL, "Current model"),
    ("post", "/api/model"): (_JSON_MODEL, _JSON_MODEL,
                             "Replace the current model"),
    ("post", "/api/model/quick"): (_obj({
        k: _ANY for k in ("name", "bays_x", "bay_width_x", "bays_y",
                          "bay_width_y", "stories", "story_height", "E",
                          "column_size", "beam_b", "beam_h", "dead_udl",
                          "live_udl", "quake_coeff", "base_fixity")}),
        _JSON_MODEL, "Generate a regular moment-frame building"),
    ("post", "/api/analyze"): (None, _ref("AnalysisResults"),
                               "Run every analysis of the current model"),
    ("post", "/api/exchange/export/{fmt}"): (_obj({
        "model": _JSON_MODEL, "units": {"type": "array", "items": _STR},
        "include": _map(_BOOL), "extensions": _BOOL, "unit_scale": _NUM,
        "tables": {"type": "array", "items": _STR}}),
        _ref("ExportResponse"), "Export E2K / IFC / DXF / Excel"),
    ("post", "/api/exchange/import/{fmt}"): (_obj({
        "text": _STR, "content_b64": _STR, "units": {
            "type": "array", "items": _STR}, "unit_scale": _NUM,
        "sections_from_current": _BOOL, "dry_run": _BOOL,
        "soft_errors": _BOOL}), _ref("ImportResponse"),
        "Import E2K / IFC / DXF / Excel"),
    ("get", "/api/openapi.json"): (None, _obj({"openapi": _STR, "info":
                                               _ANY, "paths": _ANY}),
                                   "This OpenAPI document"),
}


def add_override(method: str, rule: str, request: Optional[dict],
                 response: Optional[dict], summary: str = "") -> None:
    """Let feature modules (plugins, interop) publish exact schemas."""
    OVERRIDES[(method.lower(), _oa_path(rule))] = (request, response,
                                                   summary)


# --------------------------------------------------------------------------- #
# CONTRACT.md -> per-endpoint text + {key?} shapes
# --------------------------------------------------------------------------- #
_EP_RE = re.compile(r"`(GET|POST|PUT|PATCH|DELETE)\s+(/api/[^\s`]*)")
_ROW_RE = re.compile(r"^\|\s*(GET|POST|PUT|PATCH|DELETE)\s*\|\s*`?"
                     r"(/[^\s`|]*)`?\s*\|(.*)\|\s*$")


def contract_entries(text: str) -> List[Tuple[str, str, str]]:
    """[(method, path, description)] from CONTRACT.md tables and
    backticked ``METHOD /api/...`` mentions (bullets keep their indented
    continuation lines)."""
    out: List[Tuple[str, str, str]] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _ROW_RE.match(line)
        if m:
            out.append((m.group(1).lower(), m.group(2).rstrip("`"),
                        m.group(3).strip()))
            continue
        for em in _EP_RE.finditer(line):
            desc = line[em.end():].lstrip("`").strip()
            j = i + 1
            indent = len(line) - len(line.lstrip())
            while j < len(lines) and lines[j].strip() and (
                    len(lines[j]) - len(lines[j].lstrip()) > indent) and \
                    not lines[j].lstrip().startswith(("* ", "- ")):
                desc += " " + lines[j].strip()
                j += 1
            out.append((em.group(1).lower(), em.group(2).rstrip("`.,;:)"),
                        desc.strip()))
    return out


def _split_top(s: str) -> List[str]:
    parts, depth, cur, quote = [], 0, "", None
    for ch in s:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    return parts


def _braced(s: str) -> Optional[str]:
    i = s.find("{")
    if i < 0:
        return None
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j]
    return None


_KEY_RE = re.compile(r'^\s*["`]?([A-Za-z_][\w]*)["`]?\s*(\?)?')


def shape_schema(text: str) -> Optional[dict]:
    """``{a, b?, c: [..], d: {..}}`` -> an object schema (``?`` =
    optional); None when the text has no braced shape."""
    inner = _braced(text)
    if inner is None:
        return None
    props: Dict[str, Any] = {}
    req: List[str] = []
    for part in _split_top(inner):
        p = part.strip()
        if not p or p.startswith(("…", "...", "<", "[")):
            continue
        m = _KEY_RE.match(p)
        if not m:
            continue
        key, opt = m.group(1), bool(m.group(2))
        rest = p[m.end():].strip()
        if rest.startswith("?"):
            opt, rest = True, rest[1:].strip()
        sch: Dict[str, Any] = {}
        if rest.startswith(":"):
            v = rest[1:].strip()
            if v.startswith("["):
                sch = {"type": "array", "items": {}}
            elif v.startswith("{"):
                sch = {"type": "object", "additionalProperties": True}
            elif v[:1] in "\"'":
                sch = {"type": "string"}
            elif v in ("true", "false"):
                sch = {"type": "boolean"}
            elif re.match(r"^-?\d", v):
                sch = {"type": "number"}
        props[key] = sch
        if not opt and key not in req:
            req.append(key)
    if not props:
        return None
    s = {"type": "object", "properties": props, "additionalProperties": True}
    if req:
        s["required"] = req
    return s


def _named_shape(text: str) -> Optional[dict]:
    """A response described as the model / results dict -> its schema."""
    t = text.strip().lower()[:80]
    if "results.to_dict()" in t or t.startswith("`results"):
        return _ref("AnalysisResults")
    if re.search(r"model[ ._`]*(to_)?dict", t) and \
            "{" not in t.split("model")[0]:
        return _ref("Model")
    return None


def _arrow_split(desc: str) -> Tuple[str, str]:
    for arrow in ("→", "->"):
        if arrow in desc:
            a, b = desc.split(arrow, 1)
            return a, b
    return desc, ""


# --------------------------------------------------------------------------- #
# spec builder
# --------------------------------------------------------------------------- #
_CONV_RE = re.compile(r"<(?:([a-z_]+)(?:\([^)]*\))?:)?([A-Za-z_]\w*)>")


def _oa_path(rule: str) -> str:
    return _CONV_RE.sub(lambda m: "{" + m.group(2) + "}", rule)


def _rule_regex(rule: str) -> re.Pattern:
    out, pos = "", 0
    for m in _CONV_RE.finditer(rule):
        out += re.escape(rule[pos:m.start()])
        out += ".+" if m.group(1) == "path" else "[^/]+"
        pos = m.end()
    out += re.escape(rule[pos:])
    return re.compile("^" + out + "$")


def _norm_contract_path(p: str) -> str:
    p = re.sub(r"<[^>]*>", "X", p)
    p = re.sub(r"\{[^}]*\}", "X", p)
    return p.split("?")[0].rstrip("/") or "/"


def _tag(rule: str, endpoint_module: str) -> str:
    seg = rule.split("/")[2] if rule.count("/") >= 2 else "root"
    return seg.replace("_", "-") or "root"


def build_spec(app, contract_text: Optional[str] = None) -> dict:
    """OpenAPI 3.0.3 document of every ``/api`` route of ``app``."""
    from skyframe import __version__
    if contract_text is None:
        try:
            with open(_CONTRACT, "r", encoding="utf-8") as fh:
                contract_text = fh.read()
        except OSError:
            contract_text = ""
    entries = contract_entries(contract_text)
    rules = [r for r in app.url_map.iter_rules()
             if r.rule.startswith(API_PREFIX + "/")]
    by_rule: Dict[Tuple[str, str], List[str]] = {}
    for meth, path, desc in entries:
        np_ = _norm_contract_path(path)
        for r in rules:
            rx = _rule_regex(r.rule)
            if rx.match(np_) or _norm_contract_path(r.rule) == np_:
                by_rule.setdefault((meth, r.rule), []).append(desc)
    paths: Dict[str, dict] = {}
    op_ids = set()
    for r in sorted(rules, key=lambda x: (x.rule, x.endpoint)):
        oa = _oa_path(r.rule)
        v1 = V1_PREFIX + oa[len(API_PREFIX):]
        view = app.view_functions.get(r.endpoint)
        doc = ((view.__doc__ or "").strip() if view else "")
        module = getattr(view, "__module__", "") or ""
        params = [{"name": m.group(2), "in": "path", "required": True,
                   "schema": {"type": "integer"} if m.group(1) == "int"
                   else {"type": "string"}}
                  for m in _CONV_RE.finditer(r.rule)]
        item = paths.setdefault(v1, {})
        for meth in sorted(m.lower() for m in r.methods
                           if m.lower() in _METHODS):
            texts = by_rule.get((meth, r.rule), [])
            desc = " ".join(t for t in texts if t)[:1500]
            ov = OVERRIDES.get((meth, oa))
            req_s = resp_s = None
            summary = doc.splitlines()[0][:120] if doc else ""
            if ov is not None:
                req_s, resp_s, osum = ov
                summary = osum or summary
            else:
                for t in texts:
                    a, b = _arrow_split(t)
                    b = re.split(r"\b(?:400|404)\b", b)[0]   # error clause
                    if req_s is None and meth != "get":
                        req_s = shape_schema(a)
                    if resp_s is None and b:
                        resp_s = _named_shape(b) or shape_schema(b)
            if not summary:
                summary = f"{meth.upper()} {oa}"
            op_id = re.sub(r"\W", "_", f"{meth}_{r.endpoint}")
            while op_id in op_ids:
                op_id += "_"
            op_ids.add(op_id)
            op: Dict[str, Any] = {
                "operationId": op_id, "summary": summary,
                "tags": [_tag(r.rule, module)],
                "responses": {
                    "200": {"description": "Success",
                            "content": {"application/json": {
                                "schema": resp_s or {
                                    "type": "object",
                                    "additionalProperties": True}}}},
                    "400": {"description": "Invalid input or failure",
                            "content": {"application/json": {
                                "schema": _ref("Error")}}}}}
            if desc:
                op["description"] = desc
            if params:
                op["parameters"] = params
                op["responses"]["404"] = {
                    "description": "Unknown item",
                    "content": {"application/json": {
                        "schema": _ref("Error")}}}
            if meth in ("post", "put", "patch"):
                op["requestBody"] = {
                    "required": False,
                    "content": {"application/json": {"schema": req_s or {
                        "type": "object", "additionalProperties": True}}}}
            item[meth] = op
    return {
        "openapi": OPENAPI_VERSION,
        "info": {"title": "SkyFrame REST API", "version": __version__,
                 "description": "ETABS-style building analysis "
                 "(OpenSeesPy). SI units: kN, m, tonne, s. Every path is "
                 "also served unversioned under /api (legacy alias). "
                 "Shapes: CONTRACT.md; guide: docs/API.md."},
        "servers": [{"url": "/"}],
        "tags": [{"name": t} for t in sorted({
            op["tags"][0] for it in paths.values() for op in it.values()})],
        "paths": paths,
        "components": {"schemas": dict(COMPONENTS)},
    }


# --------------------------------------------------------------------------- #
# structural check (no third-party dependency)
# --------------------------------------------------------------------------- #
def validate_spec(spec: dict) -> List[str]:
    """Structural OpenAPI 3.0 checks; [] when valid.  (Tests also run the
    official ``openapi-spec-validator`` when it is installed.)"""
    errs: List[str] = []
    if not str(spec.get("openapi", "")).startswith("3.0"):
        errs.append("openapi must be 3.0.x")
    info = spec.get("info") or {}
    if not info.get("title") or not info.get("version"):
        errs.append("info.title / info.version required")
    comps = (spec.get("components") or {}).get("schemas") or {}
    refs: List[str] = []

    def walk(node, where):
        if isinstance(node, dict):
            if "$ref" in node:
                refs.append(node["$ref"])
            for k, v in node.items():
                walk(v, f"{where}/{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{where}/{i}")

    seen_ids = set()
    for p, item in (spec.get("paths") or {}).items():
        if not p.startswith("/"):
            errs.append(f"path {p!r} must start with /")
        names = set(re.findall(r"\{(\w+)\}", p))
        for meth, op in item.items():
            if meth not in _METHODS:
                errs.append(f"{p}: bad method {meth}")
                continue
            if not op.get("responses"):
                errs.append(f"{meth} {p}: responses required")
            oid = op.get("operationId")
            if oid in seen_ids:
                errs.append(f"duplicate operationId {oid}")
            seen_ids.add(oid)
            got = {q["name"] for q in op.get("parameters", [])
                   if q.get("in") == "path"}
            if got != names:
                errs.append(f"{meth} {p}: path parameters {sorted(got)} "
                            f"!= {sorted(names)}")
            for q in op.get("parameters", []):
                if q.get("in") == "path" and q.get("required") is not True:
                    errs.append(f"{meth} {p}: path parameter not required")
            walk(op, f"{meth} {p}")
    walk(comps, "components")
    for r in refs:
        name = r.rsplit("/", 1)[-1]
        if not r.startswith("#/components/schemas/") or name not in comps:
            errs.append(f"dangling $ref {r}")
    return errs


def register(app, state: Dict[str, Any]) -> None:
    app.wsgi_app = V1Alias(app.wsgi_app)

    @app.get("/api/openapi.json")
    def openapi_json():
        """The OpenAPI 3 document of this server."""
        return jsonify(build_spec(app))
