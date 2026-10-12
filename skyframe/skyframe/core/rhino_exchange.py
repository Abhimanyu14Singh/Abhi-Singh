"""Rhino / Grasshopper exchange: the ``skyframe.exchange/1`` payload.

Pure model logic shared by the HTTP layer (:mod:`skyframe.api.rhino_api`)
and the ``.3dm`` reader (:mod:`skyframe.io.rhino3dm_io`).  The payload
shape is the shared spec (CONTRACT "Rhino / Grasshopper exchange");
this module implements, in order:

1. :func:`validate_exchange` -- payload validation; coordinates and
   length props converted from ``source.units`` to m;
2. role resolution (:func:`resolve_object`), highest priority first: the
   object's explicit ``role`` / ``props`` -> Rhino UserText ``sf_*`` keys
   in ``attrs`` -> the first mapping-profile rule whose ``layer`` glob
   matches the layer path -> geometric defaults;
3. conversion of every object into SkyFrame "parts" (members, polygon
   shells with openings, mesh-face shells, supports, joints, links);
4. stories (``story_rule: "auto"`` clusters the elevations of horizontal
   objects), sections (model, then the steel databases, then defaults);
5. merge (default) / replace against the objects of the SAME session
   (``source_ref``), then the model-repair pipeline
   (:func:`skyframe.core.repair.repair`) so frames and shells connect.

Merge rules: an object matched by ``(session, id, part)`` keeps every
SkyFrame-side assignment (section / material unless the SOURCE gives them
explicitly via props or UserText, loads, releases, groups, hinges, ...)
and only its geometry follows the source; split pieces (the repair split a
frame at a joint) are moved by the affine map of the old chord onto the
new one, so pieces, loads and references survive.  Ids missing from the
payload are deleted -- only objects of that session, never native ones.

Everything works on the model's JSON form (``BuildingModel.to_dict``) and
reuses :mod:`skyframe.core.edit` (uids, deletion with reference cleanup,
point-record lists), :mod:`skyframe.core.repair` and
:mod:`skyframe.core.curved` / :mod:`skyframe.core.polymesh` (geometry
checks).  The input model is never mutated.
"""

from __future__ import annotations

import copy
import datetime as _dt
import fnmatch
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from skyframe.core import edit as _ed
from skyframe.core import rhino_exchange_fields as _xf

SCHEMA = "skyframe.exchange/1"
UNIT_SCALE = {"m": 1.0, "mm": 0.001, "cm": 0.01, "ft": 0.3048,
              "in": 0.0254}
KINDS = ("curve", "surface", "mesh", "point")
APPS = ("grasshopper", "rhino3dm")
ROLES = _xf.ROLES
MODES = ("merge", "replace")
PROP_KEYS = ("section", "material", "story", "angle", "release_i",
             "release_j", "restraint", "shell_section", "thickness",
             "mesh_size", "stiffness")
ATTR_KEYS = {"sf_role": "role", "sf_section": "section",
             "sf_material": "material", "sf_story": "story_rule",
             "sf_angle": "angle", "sf_shell_section": "shell_section",
             "sf_restraint": "restraint", "sf_release_i": "release_i",
             "sf_release_j": "release_j", "sf_thickness": "thickness",
             "sf_mesh_size": "mesh_size"}
ANGLE_TOL_DEG = 1.0              # vertical / horizontal classification
SHELL_SPLIT_DEG = 45.0           # role "shell": slab when |n_z| >= cos 45
DEFAULT_TOLERANCE = 0.001        # source units, when the payload has none
DEFAULT_STORY_TOL = 0.05         # m, story clustering
DEFAULT_MESH_SIZE = 1.0          # m, shell mesh size (= ShellRegion default)
DEFAULT_THICKNESS = 0.2          # m
DEFAULT_LINK_K = 1.0e6           # kN/m, kN*m/rad
DEFAULT_RESTRAINT = (1, 1, 1, 1, 1, 1)
DEFAULT_STEEL = "A992Fy50"
DEFAULT_CONCRETE = "4000Psi"
DEFAULT_FRAME_SECTIONS = {"column": "W14X90", "beam": "W18X50",
                          "brace": "HSS6X6X1/2"}
NURBS_SAG_RATIO = 200.0         # free-form curve: chord sag <= length/200
MAX_OBJECTS = 100_000
MAX_FACES = 50_000
MAX_SEGMENTS = 720
UID_PREFIX = {"column": "C", "beam": "B", "brace": "D", "wall": "W",
              "slab": "S", "link": "L"}

Vec = Tuple[float, float, float]


# --------------------------------------------------------------------------- #
# small vector helpers
# --------------------------------------------------------------------------- #
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _lerp(a, b, t):
    return tuple(a[k] + (b[k] - a[k]) * t for k in range(3))


def _clean(p) -> List[float]:
    return _ed._clean(p)


def _key(p):
    return _ed._key(p)


def _newell(pts) -> Vec:
    from skyframe.core.polymesh import newell_normal
    return newell_normal([tuple(p) for p in pts])


# --------------------------------------------------------------------------- #
# 1. payload validation
# --------------------------------------------------------------------------- #
def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) \
            or not math.isfinite(v):
        raise ValueError(f"{what} must be a finite number")
    return float(v)


def _pt(v, what: str, s: float) -> Vec:
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        raise ValueError(f"{what} must be a point [x, y, z]")
    return tuple(_num(x, what) * s for x in v)


def _pts(v, what: str, s: float, nmin: int) -> List[Vec]:
    if not isinstance(v, list) or len(v) < nmin:
        raise ValueError(f"{what} must be a list of at least {nmin} points")
    return [_pt(p, f"{what}[{i}]", s) for i, p in enumerate(v)]


def _bools6(v, what: str) -> Tuple[int, ...]:
    return tuple(_xf._restraint6(v, what))


def _mesh(v, what: str, s: float) -> dict:
    if not isinstance(v, dict):
        raise ValueError(f"{what} must be an object {{v, f}}")
    verts = _pts(v.get("v"), f"{what}.v", s, 3)
    faces = v.get("f")
    if not isinstance(faces, list) or not faces:
        raise ValueError(f"{what}.f must be a non-empty list of faces")
    if len(faces) > MAX_FACES:
        raise ValueError(f"{what}: more than {MAX_FACES} faces")
    out = []
    for i, f in enumerate(faces):
        if not isinstance(f, list) or len(f) not in (3, 4) or not all(
                isinstance(k, int) and not isinstance(k, bool)
                and 0 <= k < len(verts) for k in f):
            raise ValueError(f"{what}.f[{i}] must be 3 or 4 vertex indices")
        out.append([int(k) for k in f])
    return {"v": verts, "f": out}


def _geom(kind: str, g, where: str, s: float) -> dict:
    if not isinstance(g, dict):
        raise ValueError(f"{where} must be an object")
    t = g.get("type")
    if kind == "curve":
        if t == "line":
            return {"type": t, "p": _pts(g.get("p"), f"{where}.p", s, 2)[:2]}
        if t == "polyline":
            return {"type": t, "p": _pts(g.get("p"), f"{where}.p", s, 2)}
        if t == "arc":
            p = _pts(g.get("p"), f"{where}.p", s, 3)
            if len(p) != 3:
                raise ValueError(f"{where}.p must be [start, mid, end]")
            return {"type": t, "p": p}
        if t == "nurbs":
            out = {"type": t,
                   "samples": _pts(g.get("samples"), f"{where}.samples", s,
                                   2)}
            if g.get("degree") is not None:
                out["degree"] = int(_num(g["degree"], f"{where}.degree"))
            if g.get("cv") is not None:
                out["cv"] = _pts(g["cv"], f"{where}.cv", s, 2)
            out["periodic"] = bool(g.get("periodic", False))
            return out
        raise ValueError(f"{where}.type must be line|polyline|arc|nurbs "
                         "for a curve")
    if kind == "surface":
        if t == "polygon":
            holes = g.get("holes") or []
            if not isinstance(holes, list):
                raise ValueError(f"{where}.holes must be a list of loops")
            return {"type": t,
                    "outer": _pts(g.get("outer"), f"{where}.outer", s, 3),
                    "holes": [_pts(h, f"{where}.holes[{i}]", s, 3)
                              for i, h in enumerate(holes)]}
        if t == "nurbs_surface":
            if g.get("mesh") is None:
                raise ValueError(f"{where}: a nurbs_surface needs its "
                                 "'mesh' {v, f}")
            return {"type": t, "mesh": _mesh(g["mesh"], f"{where}.mesh", s)}
        raise ValueError(f"{where}.type must be polygon|nurbs_surface for a "
                         "surface")
    if kind == "mesh":
        if t not in (None, "mesh"):
            raise ValueError(f"{where}.type must be 'mesh'")
        m = _mesh(g, where, s)
        return {"type": "mesh", "v": m["v"], "f": m["f"]}
    if t not in (None, "point"):
        raise ValueError(f"{where}.type must be 'point'")
    return {"type": "point", "p": _pt(g.get("p"), f"{where}.p", s)}


def _props(p, where: str, s: float, warnings: List[str]) -> dict:
    if p is None:
        return {}
    if not isinstance(p, dict):
        raise ValueError(f"{where} must be an object")
    out: Dict[str, Any] = {}
    for k, v in p.items():
        if v is None or v == "":
            continue
        if k not in PROP_KEYS:
            warnings.append(f"{where}: unknown prop {k!r} ignored")
            continue
        if k in ("section", "material", "story", "shell_section"):
            if not isinstance(v, str):
                raise ValueError(f"{where}.{k} must be a string")
            out["story_rule" if k == "story" else k] = v
        elif k == "angle":
            out[k] = _num(v, f"{where}.angle")
        elif k in ("thickness", "mesh_size"):
            x = _num(v, f"{where}.{k}") * s
            if x < 0 or (k == "thickness" and x == 0):
                raise ValueError(f"{where}.{k} must be > 0")
            if x > 0:
                out[k] = x
        elif k == "stiffness":
            if not isinstance(v, list) or len(v) != 6:
                raise ValueError(f"{where}.stiffness must be 6 numbers")
            out[k] = [_num(x, f"{where}.stiffness") for x in v]
        else:                                   # release_i/j, restraint
            out[k] = _bools6(v, f"{where}.{k}")
    return out


def validate_exchange(payload) -> dict:
    """Normalised payload (coordinates and length props in m) + its
    ``warnings``.  ValueError (message names the offending path) on a
    malformed payload."""
    if not isinstance(payload, dict):
        raise ValueError("exchange must be a JSON object")
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"exchange.schema must be {SCHEMA!r}")
    src = payload.get("source") or {}
    if not isinstance(src, dict):
        raise ValueError("exchange.source must be an object")
    units = src.get("units", "m")
    if units not in UNIT_SCALE:
        raise ValueError(f"exchange.source.units must be one of "
                         f"{list(UNIT_SCALE)}")
    s = UNIT_SCALE[units]
    tol = src.get("tolerance", DEFAULT_TOLERANCE)
    tol = _num(tol, "exchange.source.tolerance")
    if tol <= 0:
        raise ValueError("exchange.source.tolerance must be > 0")
    ses = payload.get("session")
    if not isinstance(ses, str) or not ses.strip() or len(ses) > 300:
        raise ValueError("exchange.session must be a non-empty string")
    warnings: List[str] = []
    layers = []
    for i, ly in enumerate(payload.get("layers") or []):
        if not isinstance(ly, dict) or not isinstance(ly.get("path"), str):
            raise ValueError(f"exchange.layers[{i}] must be {{path, color}}")
        col = ly.get("color") or "#808080"
        if not (isinstance(col, str) and len(col) == 7 and col[0] == "#"):
            col = "#808080"
        layers.append({"path": ly["path"], "color": col.upper()})
    objs = payload.get("objects")
    if not isinstance(objs, list):
        raise ValueError("exchange.objects must be a list")
    if len(objs) > MAX_OBJECTS:
        raise ValueError(f"exchange.objects: more than {MAX_OBJECTS}")
    seen = set()
    out = []
    for i, o in enumerate(objs):
        w = f"exchange.objects[{i}]"
        if not isinstance(o, dict):
            raise ValueError(f"{w} must be an object")
        oid = o.get("id")
        if not isinstance(oid, str) or not oid:
            raise ValueError(f"{w}.id must be a non-empty string")
        if oid in seen:
            raise ValueError(f"{w}.id {oid!r} is not unique")
        seen.add(oid)
        kind = o.get("kind")
        if kind not in KINDS:
            raise ValueError(f"{w}.kind must be one of {KINDS}")
        role = o.get("role")
        if role in ("", None):
            role = None
        elif role not in ROLES:
            raise ValueError(f"{w}.role must be one of {ROLES}")
        attrs = o.get("attrs") or {}
        if not isinstance(attrs, dict):
            raise ValueError(f"{w}.attrs must be an object")
        layer = o.get("layer") or ""
        if not isinstance(layer, str):
            raise ValueError(f"{w}.layer must be a string")
        out.append({"id": oid, "kind": kind, "layer": layer,
                    "attrs": {str(k): ("" if v is None else str(v))
                              for k, v in attrs.items()},
                    "role": role,
                    "props": _props(o.get("props"), f"{w}.props", s,
                                    warnings),
                    "geom": _geom(kind, o.get("geom"), f"{w}.geom", s)})
    for ly in sorted({o["layer"] for o in out} - {x["path"] for x in layers}):
        layers.append({"path": ly, "color": "#808080"})
    return {"schema": SCHEMA,
            "source": {"app": src.get("app") if src.get("app") in APPS
                       else str(src.get("app") or ""),
                       "doc": str(src.get("doc") or ""), "units": units,
                       "tolerance": tol},
            "session": ses.strip(), "layers": layers, "objects": out,
            "scale": s, "tol_m": tol * s, "warnings": warnings}


def resolve_mapping(model_dict: dict, mapping) -> dict:
    """``mapping`` = profile name (in ``exchange_mappings``) | inline
    ``{rules: [...]}`` | None -> normalised profile."""
    if mapping in (None, ""):
        return {"rules": []}
    if isinstance(mapping, str):
        prof = (model_dict.get("exchange_mappings") or {}).get(mapping)
        if prof is None:
            raise ValueError(f"unknown mapping profile {mapping!r}")
        return _xf.normalize_profile(prof, f"mapping {mapping!r}")
    return _xf.normalize_profile(mapping, "mapping")


# --------------------------------------------------------------------------- #
# 2. role resolution
# --------------------------------------------------------------------------- #
def _attr_values(attrs: dict, s: float, warnings: List[str],
                 oid: str) -> dict:
    out: Dict[str, Any] = {}
    low = {k.lower(): v for k, v in attrs.items()}
    for ak, field in ATTR_KEYS.items():
        v = low.get(ak)
        if v is None or str(v).strip() == "":
            continue
        v = str(v).strip()
        try:
            if field == "role":
                if v.lower() not in ROLES:
                    raise ValueError(f"must be one of {ROLES}")
                out[field] = v.lower()
            elif field in ("angle", "thickness", "mesh_size"):
                x = float(v)
                if not math.isfinite(x):
                    raise ValueError("not finite")
                if field != "angle":
                    x *= s
                    if x <= 0:
                        raise ValueError("must be > 0")
                out[field] = x
            elif field in ("restraint", "release_i", "release_j"):
                out[field] = _bools6(v, ak)
            else:
                out[field] = v
        except ValueError as exc:
            warnings.append(f"{oid}: UserText {ak}={v!r} ignored ({exc})")
    return out


def _match_rule(layer: str, rules: List[dict]) -> Optional[dict]:
    low = layer.lower()
    for r in rules:
        if fnmatch.fnmatchcase(low, r["layer"].lower()):
            return r
    return None


def resolve_object(obj: dict, profile: dict, scale: float = 1.0,
                   warnings: Optional[List[str]] = None) -> dict:
    """Field-by-field resolution (spec §3).  Returns ``{role, section,
    material, shell_section, story_rule, angle, thickness, mesh_size,
    restraint, release_i, release_j, stiffness, rule, explicit}`` where
    ``explicit`` is the set of fields given by the SOURCE object itself
    (props / role / UserText) -- the ones a merge may overwrite."""
    warnings = warnings if warnings is not None else []
    explicit: Dict[str, Any] = {}
    if obj.get("role"):
        explicit["role"] = obj["role"]
    for k, v in (obj.get("props") or {}).items():
        explicit[k] = v
    for k, v in _attr_values(obj.get("attrs") or {}, scale, warnings,
                             obj["id"]).items():
        explicit.setdefault(k, v)
    rule = _match_rule(obj.get("layer") or "", profile.get("rules") or [])
    res = dict(explicit)
    if rule:
        for k in ("role", "section", "material", "shell_section",
                  "story_rule", "angle", "thickness", "mesh_size"):
            if k in rule and k not in res:
                res[k] = rule[k]
        if "restraint" in rule and "restraint" not in res:
            res["restraint"] = _bools6(rule["restraint"], "restraint")
    res["explicit"] = set(explicit)
    res["rule"] = rule["layer"] if rule else None
    return res


# --------------------------------------------------------------------------- #
# 3. geometry -> parts
# --------------------------------------------------------------------------- #
def _classify_line(a, b) -> str:
    d = _sub(b, a)
    L = _norm(d)
    if L < 1e-12:
        return "beam"
    h = math.hypot(d[0], d[1])
    if h <= L * math.sin(math.radians(ANGLE_TOL_DEG)):
        return "column"
    if abs(d[2]) <= L * math.sin(math.radians(ANGLE_TOL_DEG)):
        return "beam"
    return "brace"


def _classify_normal(n) -> str:
    """'slab' | 'wall' | 'shell' (inclined) for a unit normal."""
    nz = abs(n[2])
    if nz >= math.cos(math.radians(ANGLE_TOL_DEG)):
        return "slab"
    if nz <= math.sin(math.radians(ANGLE_TOL_DEG)):
        return "wall"
    return "shell"


def _shell_kind(n) -> str:
    return "slab" if abs(n[2]) >= math.cos(math.radians(SHELL_SPLIT_DEG)) \
        else "wall"


def _dp(pts: List[Vec], tol: float) -> List[Vec]:
    """Douglas-Peucker simplification (keeps the ends)."""
    if len(pts) <= 2:
        return list(pts)
    a, b = pts[0], pts[-1]
    ab = _sub(b, a)
    L2 = _dot(ab, ab)
    best, idx = -1.0, 0
    for i in range(1, len(pts) - 1):
        ap = _sub(pts[i], a)
        if L2 < 1e-24:
            d = _norm(ap)
        else:
            t = max(0.0, min(1.0, _dot(ap, ab) / L2))
            d = _norm(_sub(ap, _mul(ab, t)))
        if d > best:
            best, idx = d, i
    if best <= tol:
        return [a, b]
    left = _dp(pts[:idx + 1], tol)
    return left[:-1] + _dp(pts[idx:], tol)


def _resample(pts: List[Vec], h: float) -> List[Vec]:
    """Equal arc-length points along a polyline (piece length <= h)."""
    seg = [_norm(_sub(b, a)) for a, b in zip(pts[:-1], pts[1:])]
    S = sum(seg)
    n = max(1, int(math.ceil(S / h - 1e-9)))
    if n > MAX_SEGMENTS:
        n = MAX_SEGMENTS
    out = [pts[0]]
    k, acc = 0, 0.0
    for i in range(1, n):
        s = S * i / n
        while k < len(seg) - 1 and acc + seg[k] < s:
            acc += seg[k]
            k += 1
        t = 0.0 if seg[k] <= 0 else (s - acc) / seg[k]
        out.append(_lerp(pts[k], pts[k + 1], min(max(t, 0.0), 1.0)))
    out.append(pts[-1])
    return out


def _circle3(a, b, c):
    """Center / radius / normal of the circle through 3 points, or None."""
    ab, ac = _sub(b, a), _sub(c, a)
    n = _cross(ab, ac)
    nn = _dot(n, n)
    scale = max(_norm(ab), _norm(ac), 1e-12)
    if math.sqrt(nn) <= 1e-9 * scale * scale:
        return None
    t = _add(_mul(_cross(n, ab), _dot(ac, ac)),
             _mul(_cross(ac, n), _dot(ab, ab)))
    cen = _add(a, _mul(t, 1.0 / (2.0 * nn)))
    return cen, _norm(_sub(a, cen)), _mul(n, 1.0 / math.sqrt(nn))


def _as_arc(samples: List[Vec], tol: float) -> Optional[Vec]:
    """``via`` point when the sampled curve is a circular arc (within
    ``tol``, open, sweep < 360 deg); else None."""
    if len(samples) < 5 or _norm(_sub(samples[0], samples[-1])) <= tol:
        return None
    mid = samples[len(samples) // 2]
    c = _circle3(samples[0], mid, samples[-1])
    if c is None:
        return None
    cen, R, n = c
    if R > 1e6:
        return None
    for p in samples:
        if abs(_norm(_sub(p, cen)) - R) > tol or abs(
                _dot(_sub(p, cen), n)) > tol:
            return None
    return mid


def _arc_parts(a, via, b, mesh_size: float) -> Optional[dict]:
    from skyframe.core.curved import arc_geometry
    curve = {"type": "arc", "via": list(via)}
    try:
        g = arc_geometry(a, b, curve)
    except ValueError:
        return None
    if mesh_size > 0:
        n = int(math.ceil(g["radius"] * g["sweep"] / mesh_size - 1e-9))
        curve["segments"] = max(1, min(MAX_SEGMENTS, n))
    return curve


def _curve_points(g: dict, tol: float, mesh_size: float,
                  warnings: List[str], oid: str):
    """[(a, b, curve|None)] member pieces of a curve geometry."""
    t = g["type"]
    if t == "line":
        return [(g["p"][0], g["p"][1], None)]
    if t == "polyline":
        p = g["p"]
        return [(a, b, None) for a, b in zip(p[:-1], p[1:])]
    if t == "arc":
        a, m, b = g["p"]
        if _norm(_sub(a, b)) <= tol:
            warnings.append(f"{oid}: closed arc (full circle) is not "
                            "supported as one curved member -- skipped; "
                            "send it as a NURBS with samples")
            return []
        c = _circle3(a, m, b)
        curve = (None if c is None or c[1] > 1e6
                 else _arc_parts(a, m, b, mesh_size))
        return [(a, b, curve)]                    # collinear -> a line
    s = g["samples"]
    via = _as_arc(s, tol)
    if via is not None:
        curve = _arc_parts(s[0], via, s[-1], mesh_size)
        if curve is not None:
            return [(s[0], s[-1], curve)]
    # free-form: equal pieces of length <= mesh_size along the sampled
    # curve, else the samples simplified to a chord deviation of
    # max(tolerance, curve length / NURBS_SAG_RATIO) -- the CAD tolerance
    # alone (0.01 mm) would give hundreds of members
    if mesh_size > 0:
        pts = _resample(s, mesh_size)
    else:
        S = sum(_norm(_sub(b, a)) for a, b in zip(s[:-1], s[1:]))
        pts = _dp(s, max(tol, S / NURBS_SAG_RATIO))
    return [(a, b, None) for a, b in zip(pts[:-1], pts[1:])]


def _dedupe_loop(pts: List[Vec], tol: float) -> List[Vec]:
    out: List[Vec] = []
    for p in pts:
        if not out or _norm(_sub(out[-1], p)) > tol:
            out.append(p)
    if len(out) > 1 and _norm(_sub(out[0], out[-1])) <= tol:
        out.pop()
    # drop collinear middle points (straight-edge resampling)
    k = 0
    while len(out) > 3 and k < len(out):
        a, b, c = out[k - 1], out[k], out[(k + 1) % len(out)]
        ab, ac = _sub(b, a), _sub(c, a)
        L = _norm(ac)
        if L > 0 and _norm(_cross(ab, ac)) / L <= tol * 1e-3 and \
                0 < _dot(ab, ac) < _dot(ac, ac):
            out.pop(k)
            continue
        k += 1
    return out


def _project(pts: List[Vec], cen: Vec, n: Vec) -> List[Vec]:
    return [_sub(p, _mul(n, _dot(_sub(p, cen), n))) for p in pts]


def _polygon_part(outer, holes, tol, warnings, oid):
    """Planar polygon (+ holes) -> (corners, holes, unit normal) or None."""
    outer = _dedupe_loop(outer, tol)
    if len(outer) < 3:
        warnings.append(f"{oid}: polygon has fewer than 3 distinct "
                        "corners -- skipped")
        return None
    n = _newell(outer)
    nn = _norm(n)
    if nn < 1e-12:
        warnings.append(f"{oid}: degenerate polygon -- skipped")
        return None
    n = _mul(n, 1.0 / nn)
    allp = list(outer) + [p for h in holes for p in h]
    cen = _mul(_add((0.0, 0.0, 0.0), tuple(sum(p[k] for p in outer)
                                           for k in range(3))),
               1.0 / len(outer))
    dev = max(abs(_dot(_sub(p, cen), n)) for p in allp)
    if dev > max(2.0 * tol, 1e-4):
        warnings.append(f"{oid}: polygon is not planar (off-plane "
                        f"{dev:.3g} m) -- skipped; send it as a mesh / "
                        "nurbs_surface")
        return None
    # horizontal / vertical faces: snap the normal exactly (z = const for a
    # slab keeps corners shared with column tops exact)
    if abs(abs(n[2]) - 1.0) <= 1e-9 or _classify_normal(n) == "slab":
        n = (0.0, 0.0, 1.0)
    elif _classify_normal(n) == "wall":
        h = math.hypot(n[0], n[1])
        n = (n[0] / h, n[1] / h, 0.0)
    corners = _project(outer, cen, n)
    hs = []
    for h in holes:
        h = _dedupe_loop(h, tol)
        if len(h) >= 3:
            hs.append(_project(h, cen, n))
    if n[2] == 1.0:
        z = cen[2]
        corners = [(p[0], p[1], z) for p in corners]
        hs = [[(p[0], p[1], z) for p in h] for h in hs]
        if _newell(corners)[2] < 0:            # slabs CCW seen from above
            corners = corners[::-1]
    return corners, hs, n


def _weld(verts: List[Vec], tol: float) -> List[int]:
    """Vertex -> representative index (coincident vertices merged)."""
    cell: Dict[tuple, List[int]] = {}
    rep = list(range(len(verts)))
    h = max(tol, 1e-9)
    for i, p in enumerate(verts):
        c = tuple(int(math.floor(v / h)) for v in p)
        found = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in cell.get((c[0] + dx, c[1] + dy, c[2] + dz), ()):
                        if _norm(_sub(verts[j], p)) <= tol:
                            found = j
                            break
                    if found is not None:
                        break
                if found is not None:
                    break
            if found is not None:
                break
        if found is None:
            cell.setdefault(c, []).append(i)
        else:
            rep[i] = found
    return rep


def _mesh_faces(mesh: dict, tol: float) -> List[List[Vec]]:
    """Welded mesh faces as corner lists (quads off-plane by more than
    1e-6 m are split into two triangles along the shorter diagonal)."""
    from skyframe.core.checks import PLANAR_TOL
    v = mesh["v"]
    rep = _weld(v, tol)
    out = []
    for f in mesh["f"]:
        ids = []
        for k in f:
            r = rep[k]
            if not ids or ids[-1] != r:
                ids.append(r)
        if len(ids) > 1 and ids[0] == ids[-1]:
            ids.pop()
        if len(set(ids)) < 3:
            continue
        pts = [v[k] for k in ids]
        if len(pts) == 4:
            n = _cross(_sub(pts[1], pts[0]), _sub(pts[2], pts[0]))
            nn = _norm(n)
            off = (abs(_dot(_sub(pts[3], pts[0]), n)) / nn if nn > 1e-15
                   else float("inf"))
            if off > PLANAR_TOL:
                d02 = _norm(_sub(pts[2], pts[0]))
                d13 = _norm(_sub(pts[3], pts[1]))
                tris = ([[pts[0], pts[1], pts[2]], [pts[0], pts[2], pts[3]]]
                        if d02 <= d13 else
                        [[pts[0], pts[1], pts[3]], [pts[1], pts[2], pts[3]]])
                out.extend(t for t in tris
                           if _norm(_cross(_sub(t[1], t[0]),
                                           _sub(t[2], t[0]))) > 1e-12)
                continue
        if _norm(_newell(pts)) <= 1e-12:
            continue
        out.append(pts)
    return out


def _is_closed_planar(pts: List[Vec], tol: float) -> bool:
    return len(pts) >= 4 and _norm(_sub(pts[0], pts[-1])) <= tol


def object_parts(obj: dict, res: dict, tol: float,
                 warnings: List[str]) -> Tuple[str, List[dict]]:
    """(resolved role, [part]) of one normalised object.  Part shapes:
    ``{"type": "member", "kind", "pi", "pj", "curve"}``,
    ``{"type": "shell", "kind", "corners", "holes"}``,
    ``{"type": "support", "point", "restraints"}``,
    ``{"type": "joint", "point"}``, ``{"type": "link", "pi", "pj"}``."""
    oid, kind, g = obj["id"], obj["kind"], obj["geom"]
    role = res.get("role")
    mesh_size = float(res.get("mesh_size") or 0.0)
    ok = {"curve": ("column", "beam", "brace", "frame", "link", "ignore",
                    "wall", "slab", "shell"),
          "surface": ("wall", "slab", "shell", "ignore"),
          "mesh": ("wall", "slab", "shell", "ignore"),
          "point": ("support", "joint", "ignore")}[kind]
    if role is not None and role not in ok:
        warnings.append(f"{oid}: role {role!r} does not apply to a {kind} "
                        "-- default used")
        role = None
    if role == "ignore":
        return "ignore", []
    parts: List[dict] = []
    if kind == "curve":
        if role in ("wall", "slab", "shell"):
            pts = g.get("p") if g["type"] == "polyline" else g.get("samples")
            if pts and _is_closed_planar(pts, tol):
                return object_parts(
                    {**obj, "kind": "surface",
                     "geom": {"type": "polygon", "outer": pts[:-1],
                              "holes": []}}, res, tol, warnings)
            warnings.append(f"{oid}: role {role!r} needs a closed planar "
                            "curve -- treated as a frame")
            role = None
        pieces = _curve_points(g, tol, mesh_size, warnings, oid)
        if role == "link":
            for a, b, c in pieces:
                if c is not None:
                    warnings.append(f"{oid}: a curved link is replaced by "
                                    "its chord")
                if _norm(_sub(a, b)) > tol:
                    parts.append({"type": "link", "pi": a, "pj": b})
            return "link", parts
        kinds = set()
        # a polyline is classified segment by segment (columns + beams in
        # one curve); an arc / free-form curve as a whole, by its chord
        whole = (None if g["type"] in ("line", "polyline") or not pieces
                 else _classify_line(pieces[0][0], pieces[-1][1]))
        for a, b, c in pieces:
            if _norm(_sub(a, b)) <= tol:
                continue
            mk = role if role in ("column", "beam", "brace") else \
                (whole or _classify_line(a, b))
            kinds.add(mk)
            parts.append({"type": "member", "kind": mk, "pi": a, "pj": b,
                          "curve": c})
        if role in ("column", "beam", "brace"):
            return role, parts
        return (next(iter(kinds)) if len(kinds) == 1 else "frame"), parts
    if kind == "point":
        p = g["p"]
        if role is None:
            role = ("support" if "restraint" in res
                    or "support" in (obj.get("layer") or "").lower()
                    else "joint")
        if role == "support":
            parts.append({"type": "support", "point": p,
                          "restraints": tuple(res.get("restraint")
                                              or DEFAULT_RESTRAINT)})
        else:
            parts.append({"type": "joint", "point": p})
        return role, parts
    if kind == "surface" and g["type"] == "polygon":
        poly = _polygon_part(g["outer"], g["holes"], tol, warnings, oid)
        if poly is None:
            return role or "shell", []
        corners, holes, n = poly
        auto = _classify_normal(n)
        r = role or auto
        sk = r if r in ("wall", "slab") else _shell_kind(n)
        parts.append({"type": "shell", "kind": sk, "corners": corners,
                      "holes": holes})
        return r, parts
    mesh = g["mesh"] if g["type"] == "nurbs_surface" else g
    faces = _mesh_faces(mesh, tol)
    if not faces:
        warnings.append(f"{oid}: mesh has no valid face -- skipped")
        return role or "shell", []
    autos = set()
    for f in faces:
        n = _newell(f)
        n = _mul(n, 1.0 / _norm(n))
        autos.add(_classify_normal(n))
        sk = role if role in ("wall", "slab") else _shell_kind(n)
        if sk == "slab" and n[2] < 0:
            f = f[::-1]
        parts.append({"type": "shell", "kind": sk, "corners": f,
                      "holes": []})
    if role is None:            # every face a slab / a wall -> that role
        role = (next(iter(autos)) if len(autos) == 1
                and "shell" not in autos else "shell")
    return role, parts


# --------------------------------------------------------------------------- #
# 4. sections / materials
# --------------------------------------------------------------------------- #
class _Assets:
    """Section / material lookup + creation on the model dict."""

    def __init__(self, d: dict, warnings: List[str]):
        self.d = d
        d.setdefault("materials", {})
        d.setdefault("sections", {})
        d.setdefault("shell_sections", {})
        self.w = warnings
        self.added_sections: List[str] = []
        self.added_shell_sections: List[str] = []
        self.added_materials: List[str] = []
        self._warned = set()

    def _warn_once(self, msg: str) -> None:
        if msg not in self._warned:
            self._warned.add(msg)
            self.w.append(msg)

    def material(self, name: Optional[str], family: str) -> Optional[str]:
        mats = self.d["materials"]
        if name:
            if name in mats:
                return name
            from skyframe.core.model import (default_material_library,
                                             library_material)
            # exact library name, else a unique prefix ("A992" ->
            # "A992Fy50", "4000" -> "4000Psi")
            lib = [n for n in default_material_library()
                   if n.lower().startswith(name.lower())]
            pick = name if name in lib else (lib[0] if len(lib) == 1
                                             else None)
            if pick in mats:
                return pick
            if pick is not None:
                mats[pick] = library_material(pick).to_dict()
                self.added_materials.append(pick)
                return pick
            self._warn_once(f"unknown material {name!r} -- default used")
        for k, m in mats.items():
            if str(m.get("material_type", "concrete")) == family:
                return k
        from skyframe.core.model import library_material
        lib = DEFAULT_STEEL if family == "steel" else DEFAULT_CONCRETE
        if lib not in mats:
            mats[lib] = library_material(lib).to_dict()
            self.added_materials.append(lib)
        return lib

    def _db_section(self, name: str, material: Optional[str]):
        from skyframe.core import steel_db as _sdb
        for st in _sdb.standards():
            try:
                _sdb.shape(st["id"], name)
            except KeyError:
                continue
            mat = self.material(material, "steel")
            sec = _sdb.section_from_db(st["id"], name, mat)
            self.d["sections"][sec.name] = sec.to_dict()
            self.added_sections.append(sec.name)
            return sec.name
        return None

    def frame_section(self, name: Optional[str], material: Optional[str],
                      kind: str) -> str:
        secs = self.d["sections"]
        if name:
            if name in secs:
                return self._with_material(name, material)
            got = self._db_section(name, material)
            if got:
                return got
            self._warn_once(f"unknown section {name!r} (not in the model "
                            "nor the steel databases) -- default used")
        # no section given: a model section named for the kind (COL / BEAM
        # / BR...), else the default steel shape of the kind
        want = "COL" if kind == "column" else (
            "BR" if kind == "brace" else "BEAM")
        pick = next((n for n in secs if want in n.upper()), None)
        if pick is not None:
            return self._with_material(pick, material)
        dflt = DEFAULT_FRAME_SECTIONS[kind]
        got = dflt if dflt in secs else self._db_section(dflt, material)
        self._warn_once(f"no frame section given -- default {got} used for "
                        f"{kind}s")
        return got

    def _with_material(self, sec: str, material: Optional[str]) -> str:
        if not material:
            return sec
        sd = self.d["sections"][sec]
        if sd.get("material") == material:
            return sec
        fam = "steel"
        mat = self.material(material, fam)
        if mat != material or sd.get("material") == mat:
            return sec
        nm = f"{sec} ({mat})"
        if nm not in self.d["sections"]:
            nd = copy.deepcopy(sd)
            nd["name"] = nm
            nd["material"] = mat
            self.d["sections"][nm] = nd
            self.added_sections.append(nm)
        return nm

    def shell_section(self, name: Optional[str], material: Optional[str],
                      thickness: Optional[float], kind: str) -> str:
        """Named section (material override -> ``"<name> (<mat>)"`` copy);
        else a section of that thickness + material (existing or new
        ``Shell<t>mm``); else an existing SLAB / WALL / first section;
        else a new ``Shell200mm`` of the default concrete."""
        from skyframe.core.model import ShellSection
        ss = self.d["shell_sections"]
        if name and name in ss:
            if material and ss[name].get("material") != material:
                mat = self.material(material, "concrete")
                nm = f"{name} ({mat})"
                if nm not in ss:
                    nd = copy.deepcopy(ss[name])
                    nd["name"], nd["material"] = nm, mat
                    ss[nm] = nd
                    self.added_shell_sections.append(nm)
                return nm
            return name
        if name:
            mm = re.search(r"(\d+(?:\.\d+)?)\s*(?:mm)?$", name)
            if thickness is None and mm and 50 <= float(mm.group(1)) <= 2000:
                thickness = float(mm.group(1)) / 1000.0     # "WALL250"
                self._warn_once(f"unknown shell section {name!r} -- created "
                                f"with t = {mm.group(1)} mm (from its name)")
            else:
                self._warn_once(f"unknown shell section {name!r} -- created "
                                "from the thickness / default")
        if thickness is None and not material and not name and ss:
            want = "SLAB" if kind == "slab" else "WALL"
            return next((n for n in ss if want in n.upper()), next(iter(ss)))
        t = float(thickness or DEFAULT_THICKNESS)
        mat = self.material(material, "concrete")
        if not name:
            for n, sd in ss.items():
                if abs(float(sd.get("thickness", 0)) - t) < 1e-9 and \
                        sd.get("material") == mat and not sd.get("layered"):
                    return n
        nm = name or f"Shell{t * 1000:g}mm" + (f" ({mat})" if material
                                                else "")
        if nm not in ss:
            ss[nm] = ShellSection(nm, mat, t).to_dict()
            self.added_shell_sections.append(nm)
        return nm


# --------------------------------------------------------------------------- #
# 5. stories
# --------------------------------------------------------------------------- #
def _part_zs(p: dict) -> List[float]:
    if p["type"] in ("member", "link"):
        return [p["pi"][2], p["pj"][2]]
    if p["type"] == "shell":
        return [c[2] for c in p["corners"]]
    return [p["point"][2]]


def _horizontal_level(p: dict, tol: float) -> Optional[float]:
    if p["type"] == "member" and p["kind"] == "beam":
        zs = _part_zs(p)
        if max(zs) - min(zs) <= tol:
            return sum(zs) / len(zs)
    if p["type"] == "shell" and p["kind"] == "slab":
        zs = _part_zs(p)
        if max(zs) - min(zs) <= tol:
            return sum(zs) / len(zs)
    return None


def plan_stories(d: dict, items: List[dict], story_tol: float,
                 tol: float, all_zs: List[float]) -> Tuple[List[dict],
                                                           List[str]]:
    """New story list (dicts) + names created.  Existing stories keep
    name / elevation; heights are recomputed only when a story is added."""
    old = [dict(s) for s in d.get("stories") or []]
    levels = []
    for it in items:
        if it.get("story_rule", "auto") != "auto":
            continue
        # floors are beams / frames and slabs -- never the faces of a
        # free-form shell (a roof mesh), whose flat faces are no levels
        if it["row"]["role"] not in ("beam", "frame", "slab"):
            continue
        z = _horizontal_level(it["part"], tol)
        if z is not None:
            levels.append(z)
    if not levels:
        return old, []
    if old:
        base = float(old[0]["elevation"]) - float(old[0]["height"])
    else:
        base = min(all_zs) if all_zs else 0.0
    levels.sort()
    clusters: List[List[float]] = []
    for z in levels:
        if clusters and z - clusters[-1][0] <= story_tol:
            clusters[-1].append(z)
        else:
            clusters.append([z])
    new_levels = []
    for c in clusters:
        z = round(sum(c) / len(c), 9) + 0.0
        if z <= base + story_tol:
            continue
        if any(abs(float(s["elevation"]) - z) <= story_tol for s in old):
            continue
        new_levels.append(z)
    if not new_levels:
        return old, []
    used = {s["name"] for s in old}
    k = len(old)
    created = []
    stories = old + [{"name": "", "height": 0.0, "elevation": z}
                     for z in new_levels]
    stories.sort(key=lambda s: float(s["elevation"]))
    for s in stories:
        if s["name"]:
            continue
        k += 1
        while f"Story{k}" in used:
            k += 1
        s["name"] = f"Story{k}"
        used.add(s["name"])
        created.append(s["name"])
    prev = base
    for s in stories:
        s["height"] = round(float(s["elevation"]) - prev, 9) + 0.0
        prev = float(s["elevation"])
    return stories, created


def _story_for(stories: List[dict], z: float, tol: float) -> str:
    if not stories:
        return ""
    for s in stories:
        if abs(float(s["elevation"]) - z) <= tol:
            return s["name"]
    for s in stories:
        zt = float(s["elevation"])
        zb = zt - float(s["height"])
        if zb - 1e-9 < z < zt:
            return s["name"]
    # above the top level (a roof canopy, parapet posts): the top story;
    # at / below the base: the first story -- every object has a story
    top = max(stories, key=lambda s: float(s["elevation"]))
    if z >= float(top["elevation"]):
        return top["name"]
    return min(stories, key=lambda s: float(s["elevation"]))["name"]


# --------------------------------------------------------------------------- #
# 6. build (merge / replace)
# --------------------------------------------------------------------------- #
def _ref(session: str, oid: str, part: int) -> dict:
    return {"session": session, "id": oid, "part": int(part)}


def _ref_key(ref) -> Optional[tuple]:
    if not isinstance(ref, dict):
        return None
    return (ref.get("session"), ref.get("id"), int(ref.get("part") or 0))


def _release_tokens(res: dict) -> Optional[Tuple[str, str]]:
    """('Mi'|'', 'Mj'|'') from release_i/j, or None when not given."""
    if "release_i" not in res and "release_j" not in res:
        return None
    ri = res.get("release_i") or (0,) * 6
    rj = res.get("release_j") or (0,) * 6
    return ("Mi" if ri[4] or ri[5] else "", "Mj" if rj[4] or rj[5] else "")


def _partial_release(res: dict) -> bool:
    for k in ("release_i", "release_j"):
        r = res.get(k)
        if r and (any(r[:4]) or bool(r[4]) != bool(r[5])):
            return True
    return False


def _shell_dict(uid, part, section, mesh_size, story, ref) -> dict:
    return {"uid": uid, "kind": part["kind"], "behavior": "shell",
            "section": section,
            "corners": [_clean(c) for c in part["corners"]],
            "mesh_size": float(mesh_size), "story": story,
            "openings": [{"u0": 0.0, "v0": 0.0, "u1": 1.0, "v1": 1.0,
                          "polygon": [_clean(p) for p in h]}
                         for h in part["holes"]],
            "source_ref": ref}


def _shell_ok(sd: dict, d: dict) -> Optional[str]:
    """Validation message of one new shell (None = valid)."""
    from skyframe.core.model import (BuildingModel, Opening, ShellRegion,
                                     ShellSection)
    tmp = BuildingModel()
    ssd = d["shell_sections"][sd["section"]]
    tmp.shell_sections[sd["section"]] = ShellSection(
        sd["section"], ssd["material"], float(ssd["thickness"]))
    reg = ShellRegion(
        sd["uid"], sd["kind"], "shell", sd["section"],
        [tuple(c) for c in sd["corners"]], mesh_size=sd["mesh_size"],
        openings=[Opening(0.0, 0.0, 1.0, 1.0,
                          polygon=[tuple(p) for p in o["polygon"]])
                  for o in sd["openings"]])
    try:
        tmp._validate_shell(reg)
    except ValueError as exc:
        return str(exc)
    return None


def _member_chain_map(old: List[dict], a, b) -> Dict[tuple, list]:
    """Old piece endpoints -> new points (affine map of the old chord
    pieces[0].pi -> pieces[-1].pj onto a -> b)."""
    oa = tuple(old[0]["pi"])
    ob = tuple(old[-1]["pj"])
    ab = _sub(ob, oa)
    L2 = _dot(ab, ab)
    out = {}
    for m in old:
        for e in ("pi", "pj"):
            p = tuple(m[e])
            t = 0.0 if L2 <= 0 else _dot(_sub(p, oa), ab) / L2
            out[_key(p)] = _clean(_lerp(a, b, t))
    return out


def _same_curve(a, b) -> bool:
    """Curve dicts equal after normalisation (stored curves carry the
    defaults segments / local2) with points compared at 1e-9 m."""
    from skyframe.core.curved import normalize_curve
    if not a or not b:
        return not a and not b
    na, nb = normalize_curve(a), normalize_curve(b)
    for k in ("via", "center", "plane_normal"):
        pa, pb = na.pop(k, None), nb.pop(k, None)
        if (pa is None) != (pb is None) or (pa is not None
                                            and not _same(pa, pb)):
            return False
    return na == nb


def _same(p, q) -> bool:
    return _norm(_sub(tuple(p), tuple(q))) <= 1e-9


def build(model_dict: dict, payload, mapping=None, mode: str = "merge",
          *, repair: bool = True, now: Optional[str] = None
          ) -> Tuple[dict, dict]:
    """Apply an exchange payload to a COPY of ``model_dict``.  Returns
    ``(new_model_dict, report)`` -- the dict is NOT yet validated by
    :meth:`BuildingModel.from_dict` (:func:`apply_exchange` does that)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    ex = payload if payload.get("tol_m") is not None else \
        validate_exchange(payload)
    d = copy.deepcopy(model_dict)
    for k in ("members", "shells", "links", "supports", "stories"):
        d.setdefault(k, [])
    d.setdefault("patterns", {})
    profile = resolve_mapping(d, mapping)
    warnings: List[str] = list(ex.get("warnings") or [])
    tol = ex["tol_m"]
    story_tol = profile.get("story_tolerance") or max(DEFAULT_STORY_TOL,
                                                      tol)
    ses = ex["session"]
    assets = _Assets(d, warnings)

    # ---- resolve + convert every object --------------------------------
    items: List[dict] = []              # one per part
    objects: List[dict] = []            # preview rows (one per source obj)
    for o in ex["objects"]:
        res = resolve_object(o, profile, ex["scale"], warnings)
        role, parts = object_parts(o, res, tol, warnings)
        row = {"id": o["id"], "layer": o["layer"], "kind": o["kind"],
               "role": role, "rule": res.get("rule"), "section": None,
               "story": None, "parts": len(parts), "status": "skip"}
        objects.append(row)
        for k, p in enumerate(parts):
            items.append({"obj": o, "res": res, "row": row, "part": p,
                          "key": (ses, o["id"], k),
                          "story_rule": res.get("story_rule", "auto")})

    # ---- existing session objects ---------------------------------------
    def index(coll):
        idx: Dict[tuple, List[dict]] = {}
        for o in d[coll]:
            k = _ref_key(o.get("source_ref"))
            if k and k[0] == ses:
                idx.setdefault(k, []).append(o)
        return idx
    ex_m, ex_s, ex_l, ex_p = (index("members"), index("shells"),
                              index("links"), index("supports"))
    sess = copy.deepcopy((d.get("exchange_sessions") or {}).get(ses) or {})
    ex_j = {(ses,) + tuple(_split_jkey(k)): v
            for k, v in (sess.get("joints") or {}).items()}
    coll_of = {"member": ex_m, "shell": ex_s, "link": ex_l,
               "support": ex_p, "joint": ex_j}

    ctx = _ed._Ctx(d)
    summary = {s: {"members": 0, "shells": 0, "joints": 0, "links": 0}
               for s in ("create", "update", "delete", "unchanged")}
    created = {"members": [], "shells": [], "links": [], "supports": [],
               "joints": []}
    updated = {"members": [], "shells": [], "links": [], "supports": [],
               "joints": []}
    deleted = {"members": [], "shells": [], "links": [], "supports": [],
               "joints": []}
    cnt_key = {"member": "members", "shell": "shells", "link": "links",
               "support": "joints", "joint": "joints"}

    # replace: drop every object of the session first
    if mode == "replace":
        for coll, idx in (("members", ex_m), ("shells", ex_s),
                          ("links", ex_l)):
            for objs in idx.values():
                for o in objs:
                    deleted[coll].append(o["uid"])
        summary["delete"]["members"] = len(ex_m)
        summary["delete"]["shells"] = len(ex_s)
        summary["delete"]["links"] = len(ex_l)
        summary["delete"]["joints"] = len(ex_p) + len(ex_j)
        _ed._delete_objects(ctx, members=deleted["members"],
                            shells=deleted["shells"],
                            links=deleted["links"], cleanup_points=False)
        gone = {id(o) for objs in ex_p.values() for o in objs}
        d["supports"] = [s for s in d["supports"] if id(s) not in gone]
        deleted["supports"] = [k[1] for k in ex_p]
        deleted["joints"] = [k[1] for k in ex_j]
        ex_m, ex_s, ex_l, ex_p, ex_j = {}, {}, {}, {}, {}
        coll_of = {"member": ex_m, "shell": ex_s, "link": ex_l,
                   "support": ex_p, "joint": ex_j}

    # ---- stories ----------------------------------------------------------
    all_zs = [z for it in items for z in _part_zs(it["part"])]
    for m in d["members"]:
        all_zs += [m["pi"][2], m["pj"][2]]
    for s in d["shells"]:
        all_zs += [c[2] for c in s["corners"]]
    stories, st_created = plan_stories(d, items, story_tol, tol, all_zs)
    if st_created:
        d["stories"] = stories
    names = {s["name"] for s in d["stories"]}

    def story_of(it) -> str:
        rule = it["story_rule"]
        if rule != "auto":
            if rule in names:
                return rule
            msg = (f"{it['obj']['id']}: story {rule!r} does not exist -- "
                   "auto story used")
            if msg not in warnings:
                warnings.append(msg)
        zs = _part_zs(it["part"])
        return _story_for(d["stories"], max(zs), story_tol)

    # ---- update / create ----------------------------------------------------
    point_map: Dict[tuple, list] = {}
    conflicts = set()

    def map_point(old, new):
        k = _key(old)
        nv = _clean(new)
        if k in point_map and _key(point_map[k]) != _key(nv):
            conflicts.add(k)
        point_map[k] = nv

    seen_keys = set()
    native_supports = {_key(s["point"]) for s in d["supports"]
                       if not s.get("source_ref")}
    for it in items:
        p, res, row, key = it["part"], it["res"], it["row"], it["key"]
        typ = p["type"]
        exp = res["explicit"]
        ref = _ref(*key)
        # (a key that changed object type -- e.g. a curve re-mapped from
        # frame to link -- is unmatched here and its old object is deleted
        # with the missing ids below)
        old = coll_of[typ].get(key)
        seen_keys.add((typ, key))
        story = story_of(it)
        if typ == "member":
            if old:
                newc = p["curve"]
                was_curved = any(o.get("curve") for o in old)
                changed = False
                if len(old) == 1 or newc is not None or was_curved:
                    keep, drop = old[0], old[1:]
                    if drop:
                        _ed._delete_objects(ctx, members=[o["uid"]
                                                          for o in drop])
                        warnings.append(f"{key[1]}: {len(drop)} split "
                                        "piece(s) re-joined (curve)")
                    for e, q in (("pi", p["pi"]), ("pj", p["pj"])):
                        if not _same(keep[e], q):
                            map_point(keep[e], q)
                            keep[e] = _clean(q)
                            changed = True
                    if not _same_curve(keep.get("curve"), newc):
                        if newc is None:
                            keep.pop("curve", None)
                        else:
                            keep["curve"] = copy.deepcopy(newc)
                        changed = True
                    pieces = [keep]
                else:
                    mp = _member_chain_map(old, p["pi"], p["pj"])
                    for o in old:
                        for e in ("pi", "pj"):
                            q = mp[_key(o[e])]
                            if not _same(o[e], q):
                                map_point(o[e], q)
                                o[e] = q
                                changed = True
                    pieces = old
                if "role" in exp and res["role"] in ("column", "beam",
                                                     "brace", "frame"):
                    for o in pieces:
                        o["kind"] = p["kind"]
                if "section" in exp or "material" in exp:
                    sec = assets.frame_section(
                        res.get("section") or pieces[0]["section"],
                        res.get("material"), p["kind"])
                    for o in pieces:
                        if o["section"] != sec:
                            o["section"] = sec
                            changed = True
                if "angle" in exp:
                    for o in pieces:
                        o["angle"] = float(res["angle"])
                rel = _release_tokens(res) if ("release_i" in exp or
                                               "release_j" in exp) else None
                if rel is not None:
                    for i, o in enumerate(pieces):
                        toks = [t for t, keep_t in
                                ((rel[0], i == 0),
                                 (rel[1], i == len(pieces) - 1))
                                if t and keep_t]
                        o["releases"] = ",".join(toks)
                if "story_rule" in exp or changed:
                    for o in pieces:
                        o["story"] = story
                st = "update" if changed else "unchanged"
                summary[st]["members"] += 1
                for o in pieces:
                    ctx.mark_modified("members", o["uid"])
                    if changed:
                        updated["members"].append(o["uid"])
                row["section"] = pieces[0]["section"]
                row["story"] = pieces[0].get("story", "")
                _row_status(row, st)
                continue
            sec = assets.frame_section(res.get("section"),
                                       res.get("material"), p["kind"])
            uid = ctx.new_uid(UID_PREFIX[p["kind"]])
            md = {"uid": uid, "kind": p["kind"], "section": sec,
                  "pi": _clean(p["pi"]), "pj": _clean(p["pj"]),
                  "story": story, "releases": "",
                  "angle": float(res.get("angle") or 0.0),
                  "source_ref": ref}
            rel = _release_tokens(res)
            if rel is not None:
                md["releases"] = ",".join(t for t in rel if t)
            if p["curve"] is not None:
                md["curve"] = copy.deepcopy(p["curve"])
            d["members"].append(md)
            ctx.created["members"].append(uid)
            created["members"].append(uid)
            summary["create"]["members"] += 1
            row["section"], row["story"] = sec, story
            _row_status(row, "create")
            if _partial_release(res):
                warnings.append(f"{key[1]}: only moment releases (M2+M3 "
                                "together) are modelled -- release "
                                "approximated")
        elif typ == "shell":
            mesh_size = float(res.get("mesh_size") or DEFAULT_MESH_SIZE)
            if old:
                s0 = old[0]
                changed = False
                newc = [_clean(c) for c in p["corners"]]
                if len(newc) == len(s0["corners"]):
                    for oc, nc in zip(s0["corners"], newc):
                        if not _same(oc, nc):
                            map_point(oc, nc)
                            changed = True
                else:
                    changed = True
                newo = [{"u0": 0.0, "v0": 0.0, "u1": 1.0, "v1": 1.0,
                         "polygon": [_clean(q) for q in h]}
                        for h in p["holes"]]
                oldo = [o for o in s0.get("openings") or []
                        if o.get("polygon")]
                if [[_key(q) for q in o["polygon"]] for o in oldo] != \
                        [[_key(q) for q in o["polygon"]] for o in newo]:
                    changed = True
                s0["corners"] = newc
                s0["openings"] = ([o for o in s0.get("openings") or []
                                   if not o.get("polygon")] if not newo
                                  else newo)
                if "role" in exp and res["role"] in ("wall", "slab"):
                    s0["kind"] = p["kind"]
                if exp & {"shell_section", "thickness", "material"}:
                    s0["section"] = assets.shell_section(
                        res.get("shell_section"), res.get("material"),
                        res.get("thickness"), p["kind"])
                    s0["behavior"] = "shell"
                if "mesh_size" in exp:
                    s0["mesh_size"] = mesh_size
                if "story_rule" in exp or changed:
                    s0["story"] = story
                if changed:
                    msg = _shell_ok(s0, d) if s0.get("behavior") == \
                        "shell" else None
                    if msg:
                        warnings.append(f"{key[1]}: {msg}")
                st = "update" if changed else "unchanged"
                summary[st]["shells"] += 1
                if changed:
                    updated["shells"].append(s0["uid"])
                    ctx.mark_modified("shells", s0["uid"])
                row["section"], row["story"] = s0["section"], s0["story"]
                _row_status(row, st)
                continue
            sec = assets.shell_section(res.get("shell_section"),
                                       res.get("material"),
                                       res.get("thickness"), p["kind"])
            uid = ctx.new_uid(UID_PREFIX[p["kind"]])
            sd = _shell_dict(uid, p, sec, mesh_size, story, ref)
            msg = _shell_ok(sd, d)
            if msg:
                warnings.append(f"{key[1]}: {msg} -- skipped")
                ctx._used.discard(uid)
                continue
            d["shells"].append(sd)
            created["shells"].append(uid)
            summary["create"]["shells"] += 1
            row["section"], row["story"] = sec, story
            _row_status(row, "create")
        elif typ == "support":
            if old:
                rec = old[0]
                changed = not _same(rec["point"], p["point"])
                if changed:
                    map_point(rec["point"], p["point"])
                    rec["point"] = _clean(p["point"])
                if "restraint" in exp:
                    rr = [int(x) for x in p["restraints"]]
                    if rr != [int(x) for x in rec["restraints"]]:
                        rec["restraints"] = rr
                        changed = True
                st = "update" if changed else "unchanged"
                summary[st]["joints"] += 1
                if changed:
                    updated["supports"].append(key[1])
                _row_status(row, st)
                continue
            if _key(p["point"]) in native_supports:
                warnings.append(f"{key[1]}: a native support already "
                                "exists at this point -- kept")
                _row_status(row, "unchanged")
                continue
            d["supports"].append({"point": _clean(p["point"]),
                                  "restraints": [int(x) for x in
                                                 p["restraints"]],
                                  "source_ref": ref})
            created["supports"].append(key[1])
            summary["create"]["joints"] += 1
            _row_status(row, "create")
        elif typ == "joint":
            jk = _jkey(key)
            if old is not None:
                changed = not _same(old, p["point"])
                if changed:
                    map_point(old, p["point"])
                sess.setdefault("joints", {})[jk] = _clean(p["point"])
                st = "update" if changed else "unchanged"
                summary[st]["joints"] += 1
                if changed:
                    updated["joints"].append(key[1])
                _row_status(row, st)
                continue
            sess.setdefault("joints", {})[jk] = _clean(p["point"])
            created["joints"].append(key[1])
            summary["create"]["joints"] += 1
            _row_status(row, "create")
        else:                                           # link
            k6 = res.get("stiffness")
            if old:
                lk = old[0]
                changed = False
                for e, q in (("pi", p["pi"]), ("pj", p["pj"])):
                    if not _same(lk[e], q):
                        map_point(lk[e], q)
                        lk[e] = _clean(q)
                        changed = True
                if "stiffness" in exp and k6:
                    lk["stiffness"] = [float(x) for x in k6]
                st = "update" if changed else "unchanged"
                summary[st]["links"] += 1
                if changed:
                    updated["links"].append(lk["uid"])
                _row_status(row, st)
                continue
            if not k6:
                k6 = [DEFAULT_LINK_K] * 6
                msg = (f"links without props.stiffness get the default "
                       f"stiffness {DEFAULT_LINK_K:g} (kN/m, kN*m/rad)")
                if msg not in warnings:
                    warnings.append(msg)
            uid = ctx.new_uid("L")
            d["links"].append({"uid": uid, "pi": _clean(p["pi"]),
                               "pj": _clean(p["pj"]),
                               "stiffness": [float(x) for x in k6],
                               "link_type": "elastic", "params": {},
                               "source_ref": ref})
            created["links"].append(uid)
            summary["create"]["links"] += 1
            _row_status(row, "create")

    # ---- deletions (merge: session ids missing from the payload) -------
    del_rows = []
    if mode == "merge":
        dm, ds, dl = [], [], []
        gone_sup = set()
        for typ, idx in (("member", ex_m), ("shell", ex_s), ("link", ex_l),
                         ("support", ex_p), ("joint", ex_j)):
            for key, objs in idx.items():
                if (typ, key) in seen_keys:
                    continue
                summary["delete"][cnt_key[typ]] += 1
                del_rows.append(key[1])
                if typ == "member":
                    dm += [o["uid"] for o in objs]
                elif typ == "shell":
                    ds += [o["uid"] for o in objs]
                elif typ == "link":
                    dl += [o["uid"] for o in objs]
                elif typ == "support":
                    gone_sup |= {id(o) for o in objs}
                    deleted["supports"].append(key[1])
                else:
                    sess.get("joints", {}).pop(_jkey(key), None)
                    deleted["joints"].append(key[1])
        if dm or ds or dl:
            gone = set()
            for coll, uids in (("members", dm), ("shells", ds),
                               ("links", dl)):
                us = set(uids)
                for o in d[coll]:
                    if o["uid"] in us:
                        gone |= {_key(p) for p in (
                            o["corners"] if coll == "shells"
                            else (o["pi"], o["pj"]))}
            _ed._delete_objects(ctx, members=dm, shells=ds, links=dl,
                                cleanup_points=False)
            _drop_orphan_records(ctx, gone)
        deleted["members"] += dm
        deleted["shells"] += ds
        deleted["links"] += dl
        if gone_sup:
            d["supports"] = [s for s in d["supports"]
                             if id(s) not in gone_sup]
    for oid in dict.fromkeys(del_rows):
        if not any(r["id"] == oid for r in objects):
            objects.append({"id": oid, "layer": None, "kind": None,
                            "role": None, "rule": None, "section": None,
                            "story": None, "parts": 0, "status": "delete"})

    # ---- point-keyed records follow moved joints ------------------------
    if point_map:
        pm = {k: v for k, v in point_map.items() if k not in conflicts}
        for lst, lk in _ed._point_lists(d):
            if lk == "supports":
                for rec in lst:
                    if (rec.get("source_ref") or {}).get("session") == ses:
                        continue
                    q = pm.get(_key(rec["point"]))
                    if q is not None:
                        rec["point"] = q
                continue
            for rec in lst:
                if isinstance(rec, dict) and "point" in rec:
                    q = pm.get(_key(rec["point"]))
                    if q is not None:
                        rec["point"] = q
        for g in (d.get("groups") or {}).values():
            g["points"] = [pm.get(_key(q)) or list(q)
                           for q in g.get("points") or []]

    # ---- pure joints split the frames passing through them ---------------
    jpts = list((sess.get("joints") or {}).values())
    split_report = []
    if jpts:
        from skyframe.core import repair as _rp
        for jp in jpts:
            for m in list(d["members"]):
                if m.get("curve"):
                    continue
                t = _ed._point_on_segment_t(jp, m["pi"], m["pj"], tol)
                if t is None:
                    continue
                L = math.dist(m["pi"], m["pj"])
                if t * L <= tol or (1 - t) * L <= tol:
                    continue
                uids = _rp._split_at(ctx, m["uid"], [(t, list(jp))], tol)
                if len(uids) > 1:
                    split_report.append({"joint": _clean(jp),
                                         "member": m["uid"],
                                         "pieces": uids})

    # ---- session registry -------------------------------------------------
    sess.update({"app": ex["source"]["app"], "doc": ex["source"]["doc"],
                 "units": ex["source"]["units"],
                 "tolerance": ex["source"]["tolerance"],
                 "last_update": now or _dt.datetime.now(
                     _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    if isinstance(mapping, str) and mapping:
        sess["mapping"] = mapping
    if not sess.get("joints"):
        sess.pop("joints", None)
    d.setdefault("exchange_sessions", {})
    d["exchange_sessions"] = dict(d["exchange_sessions"] or {})
    d["exchange_sessions"][ses] = sess

    # ---- repair: merge joints, split at joints/crossings, snap shells ----
    rep = None
    if repair:
        from skyframe.core import repair as _rp
        rtol = min(max(tol, 1e-6), _rp.MAX_TOLERANCE)
        d, rr = _rp.repair(d, ["merge_joints", "remove_zero_length",
                               "remove_duplicates", "snap_shell_corners",
                               "split_at_joints", "split_at_crossings"],
                           rtol, check=False)
        rep = {"changed": rr["changed"], "tolerance_m": rr["tolerance_m"],
               "counts": rr["counts"], "warnings": rr["warnings"],
               "created": rr["created"], "deleted": rr["deleted"],
               "actions": rr["actions"][:200]}
        for k in ("members", "shells", "links"):
            gone = set(rr["deleted"].get(k, []))
            lost = [u for u in created[k] if u in gone]
            if lost:
                warnings.append(
                    f"{len(lost)} new {k[:-1]}(s) duplicated objects already "
                    "in the model (another session or drawn in SkyFrame) and "
                    "were dropped by the repair: " + ", ".join(lost[:10]))
            for lst in (created, updated):
                lst[k] = [u for u in lst[k] if u not in gone]

    report = {
        "schema": SCHEMA, "session": ses, "mode": mode,
        "source": ex["source"], "mapping": (mapping if isinstance(
            mapping, str) else ("inline" if mapping else None)),
        "summary": summary,
        "created": created, "updated": updated, "deleted": deleted,
        "stories_created": st_created,
        "sections_added": assets.added_sections,
        "shell_sections_added": assets.added_shell_sections,
        "materials_added": assets.added_materials,
        "joint_splits": split_report,
        "repair": rep,
        "warnings": warnings,
        "objects": [{k: r[k] for k in ("id", "layer", "role", "section",
                                       "story", "status", "rule", "parts")}
                    for r in objects],
    }
    return d, report


def _drop_orphan_records(ctx, gone: set) -> None:
    """Point records (nodal loads, masses, native supports ...) left at a
    joint that no object uses any more after a merge deletion are removed
    (as Edit > Delete does); exchange supports stay -- they are sources
    of their own and follow their ids."""
    orphan = gone - set(ctx.object_joints())
    if not orphan:
        return
    for lst, k in list(_ed._point_lists(ctx.d)):
        lst[:] = [r for r in lst
                  if _key(r["point"]) not in orphan
                  or (k == "supports" and r.get("source_ref"))]
    for g in (ctx.d.get("groups") or {}).values():
        g["points"] = [p for p in g.get("points") or []
                       if _key(p) not in orphan]


def _row_status(row: dict, st: str) -> None:
    """Combine the statuses of an object's parts: all created -> create,
    all unchanged -> unchanged, anything else (some parts changed, added
    or kept) -> update."""
    cur = row["status"]
    if cur == "skip" or cur == st:
        row["status"] = st
    else:
        row["status"] = "update"


def _jkey(key: tuple) -> str:
    return f"{key[1]}#{key[2]}"


def _split_jkey(k: str) -> Tuple[str, int]:
    oid, _, part = k.rpartition("#")
    try:
        return oid, int(part)
    except ValueError:
        return k, 0


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def apply_exchange(model, payload, mapping=None, mode: str = "merge",
                   **kw):
    """``(new BuildingModel, report)``; the input model is untouched.
    ValueError on a bad payload or when the result does not validate."""
    from skyframe.core.model import BuildingModel
    src = model.to_dict() if isinstance(model, BuildingModel) else model
    d, report = build(src, payload, mapping, mode, **kw)
    try:
        new = BuildingModel.from_dict(d)
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"the converted model is invalid: {exc}") from None
    report["model_summary"] = model_summary(new)
    return new, report


def preview_exchange(model, payload, mapping=None, mode: str = "merge"
                     ) -> dict:
    """The apply report on a throw-away copy (model untouched)."""
    from skyframe.core.model import BuildingModel
    src = model.to_dict() if isinstance(model, BuildingModel) else model
    d, rep = build(src, payload, mapping, mode)
    out = {"summary": rep["summary"], "warnings": rep["warnings"],
           "objects": rep["objects"], "session": rep["session"],
           "mode": mode, "stories_created": rep["stories_created"],
           "sections_added": rep["sections_added"],
           "shell_sections_added": rep["shell_sections_added"],
           "repair": (None if rep["repair"] is None else
                      {"counts": rep["repair"]["counts"],
                       "changed": rep["repair"]["changed"]})}
    try:
        BuildingModel.from_dict(d)
        out["valid"] = True
    except (ValueError, KeyError, TypeError) as exc:
        out["valid"] = False
        out["warnings"] = out["warnings"] + [f"the converted model is "
                                             f"invalid: {exc}"]
    return out


def model_summary(model) -> dict:
    return {"name": model.name, "members": len(model.members),
            "shells": len(model.shells), "links": len(model.links),
            "supports": len(model.supports),
            "stories": [s.name for s in model.stories],
            "sessions": sessions(model)}


def sessions(model) -> List[dict]:
    """``[{session, objects, members, shells, links, supports, joints,
    last_update, app, doc}]`` -- every session present in the model."""
    reg = getattr(model, "exchange_sessions", None) or {}
    cnt: Dict[str, Dict[str, set]] = {}

    def add(ref, coll, uid):
        if not ref:
            return
        c = cnt.setdefault(ref["session"], {})
        c.setdefault(coll, set()).add(uid)
        c.setdefault("_ids", set()).add(ref["id"])
    for m in model.members:
        add(m.source_ref, "members", m.uid)
    for s in model.shells:
        add(s.source_ref, "shells", s.uid)
    for lk in model.links:
        add(lk.source_ref, "links", lk.uid)
    for i, s in enumerate(model.supports):
        add(s.source_ref, "supports", i)
    out = []
    for ses in list(dict.fromkeys(list(reg) + list(cnt))):
        c = cnt.get(ses, {})
        info = reg.get(ses) or {}
        joints = info.get("joints") or {}
        ids = set(c.get("_ids", set())) | {_split_jkey(k)[0] for k in joints}
        out.append({"session": ses, "objects": len(ids),
                    "members": len(c.get("members", ())),
                    "shells": len(c.get("shells", ())),
                    "links": len(c.get("links", ())),
                    "supports": len(c.get("supports", ())),
                    "joints": len(joints),
                    "last_update": info.get("last_update"),
                    "app": info.get("app"), "doc": info.get("doc"),
                    "units": info.get("units")})
    return out


def remove_session(model, session: str) -> Tuple[Any, dict]:
    """Delete every object of ``session`` (+ its registry entry)."""
    from skyframe.core.model import BuildingModel
    d = model.to_dict()
    if session not in {s["session"] for s in sessions(model)}:
        raise ValueError(f"unknown exchange session {session!r}")
    ctx = _ed._Ctx(d)
    mem = [m["uid"] for m in d["members"]
           if (m.get("source_ref") or {}).get("session") == session]
    sh = [s["uid"] for s in d["shells"]
          if (s.get("source_ref") or {}).get("session") == session]
    lk = [x["uid"] for x in d["links"]
          if (x.get("source_ref") or {}).get("session") == session]
    nsup = len(d["supports"])
    d["supports"] = [s for s in d["supports"]
                     if (s.get("source_ref") or {}).get("session")
                     != session]
    _ed._delete_objects(ctx, members=mem, shells=sh, links=lk)
    reg = dict(d.get("exchange_sessions") or {})
    joints = len((reg.pop(session, None) or {}).get("joints") or {})
    if reg:
        d["exchange_sessions"] = reg
    else:
        d.pop("exchange_sessions", None)
    new = BuildingModel.from_dict(d)
    return new, {"session": session,
                 "deleted": {"members": mem, "shells": sh, "links": lk,
                             "supports": nsup - len(d["supports"]),
                             "joints": joints},
                 "warnings": ctx.warnings}


def save_mapping(model, name: str, profile) -> Any:
    """Add / replace (``profile`` None: delete) a mapping profile."""
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise ValueError("mapping name must be a non-empty string")
    if profile is None:
        model.exchange_mappings.pop(name, None)
    else:
        model.exchange_mappings[name] = _xf.normalize_profile(
            profile, f"mapping {name!r}")
    return model


# --------------------------------------------------------------------------- #
# results keyed by source id
# --------------------------------------------------------------------------- #
RESULT_WHATS = ("displacements", "member_forces", "reactions", "periods",
                "drifts", "story")


def results_by_source(model, results: dict, session: Optional[str] = None,
                      case: Optional[str] = None,
                      what: str = "displacements") -> dict:
    """Analysis results keyed by SOURCE id (CONTRACT "Rhino / Grasshopper
    exchange").  ``results`` = the ``/api/analyze`` dict."""
    if what not in RESULT_WHATS:
        raise ValueError(f"what must be one of {RESULT_WHATS}")
    cases = results.get("cases") or {}
    combos = results.get("combos") or {}
    if case in (None, ""):
        case = next(iter(cases), None) or next(iter(combos), None)
    if case in cases:
        cr = cases[case]
    elif case in combos:
        cr = combos[case]
    else:
        raise KeyError(f"unknown case / combo {case!r}")
    out = {"case": case, "what": what, "session": session, "units": "SI",
           "unit_labels": {"length": "m", "force": "kN",
                           "moment": "kN*m", "time": "s"},
           "items": {}}
    if what == "periods":
        mod = results.get("modal") or {}
        T = list(mod.get("periods") or [])
        fr = list(mod.get("frequencies") or [])
        out["model"] = {"periods": T, "frequencies": fr}
        out["periods"] = T                      # plain list (GH plugin)
        out["items"] = {str(k + 1): {"mode": k + 1, "period": t,
                                     "frequency": (fr[k] if k < len(fr)
                                                   else None)}
                        for k, t in enumerate(T)}
        return out
    if what in ("drifts", "story"):
        st = cr.get("story") or {}
        out["model"] = ({s: {k: v.get(k) for k in ("drift_x", "drift_y")}
                         for s, v in st.items()} if what == "drifts"
                        else copy.deepcopy(st))
        out["items"] = copy.deepcopy(out["model"])   # keyed by story name
        return out
    nodes = results.get("nodes") or {}
    by_key = {_key(p): nid for nid, p in nodes.items()}

    def node_of(p):
        nid = by_key.get(_key(p))
        if nid is not None:
            return nid
        best, bd = None, 1e-4
        for k, q in nodes.items():
            dd = math.dist(q, p)
            if dd < bd:
                best, bd = k, dd
        return best

    disp = cr.get("node_disp") or {}
    reac = cr.get("reactions") or {}
    forces = cr.get("member_forces") or {}
    stations = cr.get("member_stations") or {}
    curved = results.get("curved_frames") or {}

    def ok(ref):
        return ref is not None and (session in (None, "")
                                    or ref["session"] == session)
    items: Dict[str, dict] = {}
    # members (pieces in order: part, then position along the part)
    groups: Dict[str, List[Tuple[int, int, Any]]] = {}
    for i, m in enumerate(model.members):
        if ok(m.source_ref):
            groups.setdefault(m.source_ref["id"], []).append(
                (int(m.source_ref.get("part") or 0), i, m))
    for oid, lst in groups.items():
        lst.sort(key=lambda t: (t[0], t[1]))
        segs = []
        for part, _i, m in lst:
            uids = ([u for u in (curved.get(m.uid) or {}).get("chords", [])]
                    if m.curve else []) or [m.uid]
            if m.curve and m.uid in curved:
                pts = curved[m.uid]["points"]
                ends = list(zip(pts[:-1], pts[1:]))
            else:
                ends = [(m.pi, m.pj)]
            for u, (a, b) in zip(uids, ends):
                seg = {"uid": u, "member": m.uid, "part": part,
                       "pi": list(a), "pj": list(b)}
                if what == "displacements":
                    ni, nj = node_of(a), node_of(b)
                    seg["i"] = disp.get(str(ni)) if ni else None
                    seg["j"] = disp.get(str(nj)) if nj else None
                elif what == "member_forces":
                    seg["forces"] = forces.get(u)
                    if u in stations:
                        seg["stations"] = stations[u]
                else:
                    continue
                segs.append(seg)
        if segs:
            items[oid] = {"type": "member", "segments": segs}
    if what in ("displacements", "reactions"):
        pts: Dict[str, List[list]] = {}
        for s in model.supports:
            if ok(s.source_ref):
                pts.setdefault(s.source_ref["id"], []).append(list(s.point))
        for ses, info in (getattr(model, "exchange_sessions", None)
                          or {}).items():
            if session not in (None, "") and ses != session:
                continue
            for k, p in (info.get("joints") or {}).items():
                pts.setdefault(_split_jkey(k)[0], []).append(list(p))
        for oid, plist in pts.items():
            p = plist[0]
            nid = node_of(p)
            val = (disp if what == "displacements" else reac).get(
                str(nid)) if nid else None
            items[oid] = {"type": "point", "point": p,
                          ("disp" if what == "displacements"
                           else "reaction"): val}
    if what == "displacements":
        shells: Dict[str, Dict[tuple, list]] = {}
        for s in model.shells:
            if ok(s.source_ref):
                vm = shells.setdefault(s.source_ref["id"], {})
                for c in list(s.corners) + [
                        p for op in s.openings for p in (op.polygon or [])]:
                    vm.setdefault(_key(c), list(c))
        for oid, vm in shells.items():
            verts = []
            for p in vm.values():
                nid = node_of(p)
                verts.append({"point": p,
                              "disp": disp.get(str(nid)) if nid else None})
            items[oid] = {"type": "shell", "vertices": verts}
    out["items"] = items
    return out
