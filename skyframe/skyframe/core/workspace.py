"""Model-file workspace data: named views, report templates, saved table
sets and drawing aids (dimension lines, reference points / planes).

CONTRACT "Model templates, general grids, shell editing and drawing aids".

None of these affect the analysis.  They live on
``BuildingModel.workspace`` (``{key: list}``) and are serialised as
TOP-LEVEL model keys, each only when non-empty, so every existing model
round-trips byte-identically::

    "named_views":      [{"name", "view": "plan"|"elev"|"3d",
                          "story"?, "elev_line"?, "camera": {k: number},
                          "display"?: {...}}]
    "report_templates": [{"name", "tables": [key], "figures": [id],
                          "cases": [name], "options"?: {...}}]
    "table_sets":       [{"name", "tables": [key]}]
    "dimension_lines":  [{"p1": [x,y,z], "p2": [x,y,z], "offset": m,
                          "story"?: name, "label"?: text}]
    "reference_points": [{"name", "point": [x,y,z]}]
    "reference_planes": [{"name", "axis": "x"|"y"|"z", "coord": m}]

``display`` / ``options`` are free JSON objects (the UI's display options),
checked only for JSON-serialisability and size.
"""

from __future__ import annotations

import copy
import json
import math
from typing import Dict, List

KEYS = ("named_views", "report_templates", "table_sets", "dimension_lines",
        "reference_points", "reference_planes")
VIEW_KINDS = ("plan", "elev", "3d")
PLANE_AXES = ("x", "y", "z")
MAX_ITEMS = 500
MAX_JSON = 64_000          # bytes per free-form object


def _name(e, what, i):
    n = e.get("name")
    if not isinstance(n, str) or not n.strip() or len(n) > 80:
        raise ValueError(f"{what} {i + 1}: 'name' must be a non-empty "
                         "string (max 80 chars)")
    return n.strip()


def _num(v, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)) \
            or not math.isfinite(v):
        raise ValueError(f"{what} must be a finite number")
    return float(v)


def _pt(v, what, n=3):
    if not isinstance(v, (list, tuple)) or len(v) != n:
        raise ValueError(f"{what} must be [{', '.join('xyz'[:n])}]")
    return [_num(c, what) for c in v]


def _strs(v, what):
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ValueError(f"{what} must be a list of strings")
    return list(v)


def _free(v, what):
    if v is None:
        return None
    if not isinstance(v, dict):
        raise ValueError(f"{what} must be an object")
    try:
        s = json.dumps(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be JSON data") from None
    if len(s) > MAX_JSON:
        raise ValueError(f"{what} is too large")
    return copy.deepcopy(v)


def _unique(lst, what):
    seen = set()
    for e in lst:
        if e["name"] in seen:
            raise ValueError(f"{what}: duplicate name {e['name']!r}")
        seen.add(e["name"])


def _named_view(e, i):
    out = {"name": _name(e, "named view", i)}
    v = e.get("view")
    if v not in VIEW_KINDS:
        raise ValueError(f"named view {out['name']!r}: view must be one of "
                         f"{VIEW_KINDS}")
    out["view"] = v
    for k in ("story", "elev_line"):
        if e.get(k) is not None:
            if not isinstance(e[k], str):
                raise ValueError(f"named view {out['name']!r}: {k} must be "
                                 "a string")
            out[k] = e[k]
    cam = e.get("camera") or {}
    if not isinstance(cam, dict):
        raise ValueError(f"named view {out['name']!r}: camera must be an "
                         "object")
    c2 = {}
    for k, val in cam.items():
        if isinstance(val, list):
            c2[str(k)] = [_num(x, f"camera.{k}") for x in val]
        else:
            c2[str(k)] = _num(val, f"camera.{k}")
    out["camera"] = c2
    disp = _free(e.get("display"), f"named view {out['name']!r} display")
    if disp is not None:
        out["display"] = disp
    return out


def _report_template(e, i):
    out = {"name": _name(e, "report template", i),
           "tables": _strs(e.get("tables"), "report template tables"),
           "figures": _strs(e.get("figures"), "report template figures"),
           "cases": _strs(e.get("cases"), "report template cases")}
    opt = _free(e.get("options"), "report template options")
    if opt is not None:
        out["options"] = opt
    return out


def _table_set(e, i):
    return {"name": _name(e, "table set", i),
            "tables": _strs(e.get("tables"), "table set tables")}


def _dimension(e, i):
    out = {"p1": _pt(e.get("p1"), f"dimension line {i + 1} p1"),
           "p2": _pt(e.get("p2"), f"dimension line {i + 1} p2"),
           "offset": _num(e.get("offset", 0.0), f"dimension line {i + 1} "
                          "offset")}
    if math.dist(out["p1"], out["p2"]) < 1e-9:
        raise ValueError(f"dimension line {i + 1}: zero length")
    for k in ("story", "label"):
        if e.get(k) is not None:
            if not isinstance(e[k], str):
                raise ValueError(f"dimension line {i + 1}: {k} must be a "
                                 "string")
            out[k] = e[k]
    return out


def _ref_point(e, i):
    return {"name": _name(e, "reference point", i),
            "point": _pt(e.get("point"), f"reference point {i + 1}")}


def _ref_plane(e, i):
    out = {"name": _name(e, "reference plane", i)}
    if e.get("axis", "z") not in PLANE_AXES:
        raise ValueError(f"reference plane {out['name']!r}: axis must be "
                         f"one of {PLANE_AXES}")
    out["axis"] = e.get("axis", "z")
    out["coord"] = _num(e.get("coord"), f"reference plane "
                        f"{out['name']!r} coord")
    return out


_NORM = {"named_views": _named_view, "report_templates": _report_template,
         "table_sets": _table_set, "dimension_lines": _dimension,
         "reference_points": _ref_point, "reference_planes": _ref_plane}


def normalize(key: str, lst) -> List[dict]:
    if lst is None:
        return []
    if not isinstance(lst, list):
        raise ValueError(f"{key} must be a list")
    if len(lst) > MAX_ITEMS:
        raise ValueError(f"{key}: at most {MAX_ITEMS} entries")
    out = []
    for i, e in enumerate(lst):
        if not isinstance(e, dict):
            raise ValueError(f"{key} entry {i + 1} must be an object")
        out.append(_NORM[key](e, i))
    if key not in ("dimension_lines",):
        _unique(out, key)
    return out


def model_to_dict(model) -> Dict[str, list]:
    ws = getattr(model, "workspace", None) or {}
    return {k: copy.deepcopy(ws[k]) for k in KEYS if ws.get(k)}


def model_from_dict(model, d: dict) -> None:
    ws = {}
    for k in KEYS:
        if d.get(k):
            ws[k] = normalize(k, d[k])
    model.workspace = ws


def validate(model) -> None:
    ws = getattr(model, "workspace", None) or {}
    if not isinstance(ws, dict):
        raise ValueError("workspace must be an object")
    bad = [k for k in ws if k not in KEYS]
    if bad:
        raise ValueError(f"workspace: unknown key(s) {bad}")
    for k, v in ws.items():
        ws[k] = normalize(k, v)
