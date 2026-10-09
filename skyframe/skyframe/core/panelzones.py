"""Per-joint panel zones (ETABS Assign > Joint > Panel Zone).

``BuildingModel.panel_zones`` ("none" | "rigid" | "scissors") is the
model-wide beam-column joint assumption.  ``BuildingModel.
joint_panel_zones`` assigns an explicit ELASTIC SCISSORS panel zone to
individual joints, overriding the model-wide setting there::

    {"point": [x, y, z],
     "property": "from_column" | "elastic" | "spring",
     "k": float > 0,             # spring only: K_theta (kN*m/rad)
     "doubler_t": float >= 0,    # elastic only: doubler plate thickness (m)
     "connectivity": "beams_to_panel"            # default
                   | "braces_to_panel"
                   | "beams_and_braces_to_panel"}

Property -> rotational stiffness ``K_theta`` (about global rx AND ry, the
model-wide scissors idealization of :func:`skyframe.engine.
opensees_engine.compute_panel_zone_springs`):

* ``from_column`` — ``G * d_c * d_b * t_p`` with the SAME governing
  column / beam rule as the model-wide "scissors" (deepest vertical
  column: ``d_c = h``, ``t_p = b``; ``d_b`` = deepest beam at the joint);
* ``elastic`` — ``G * d_c * d_b * (t_p + doubler_t)`` (ETABS "elastic
  properties from column and doubler plate"); ``doubler_t = 0`` equals
  ``from_column``;
* ``spring`` — the given ``k``.

Connectivity: the member kinds whose ends at the joint connect to the
PANEL (the duplicate node); every other member end stays on the joint
node.  "beams_to_panel" is the model-wide scissors topology.

Joints without an override follow the model-wide setting exactly (an
empty list is byte-identical to the pre-existing behaviour).  With
``panel_zones == "rigid"`` an overridden joint gets NO automatic rigid end
zones (the spring replaces them there); with "scissors" the override
replaces the model-wide spring at that joint.  Support / restrained joints
are skipped with a ``UserWarning`` (as model-wide).
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

PZ_PROPERTIES = ("from_column", "elastic", "spring")
PZ_CONNECTIVITY = {"beams_to_panel": ("beam",),
                   "braces_to_panel": ("brace",),
                   "beams_and_braces_to_panel": ("beam", "brace")}
PZ_KEYS = ("point", "property", "k", "doubler_t", "connectivity")

Vec3 = Tuple[float, float, float]


def _pkey(p) -> Vec3:
    return (round(float(p[0]), 6), round(float(p[1]), 6),
            round(float(p[2]), 6))


def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{what} must be a number")
    v = float(v)
    if not math.isfinite(v):
        raise ValueError(f"{what} must be finite")
    return v


def normalize_entry(e, i: int = 0) -> dict:
    where = f"joint_panel_zones[{i}]"
    if not isinstance(e, dict):
        raise ValueError(f"{where} must be an object")
    bad = set(e) - set(PZ_KEYS)
    if bad:
        raise ValueError(f"{where}: unknown key(s) {sorted(bad)}")
    pt = e.get("point")
    if not isinstance(pt, (list, tuple)) or len(pt) != 3:
        raise ValueError(f"{where}.point must be [x, y, z]")
    out = {"point": [_num(v, f"{where}.point") for v in pt]}
    prop = e.get("property", "from_column")
    if prop not in PZ_PROPERTIES:
        raise ValueError(f"{where}.property must be one of {PZ_PROPERTIES}, "
                         f"got {prop!r}")
    out["property"] = prop
    if prop == "spring":
        if e.get("k") is None:
            raise ValueError(f"{where}: property 'spring' needs k")
        k = _num(e["k"], f"{where}.k")
        if k <= 0.0:
            raise ValueError(f"{where}.k must be > 0")
        out["k"] = k
    elif e.get("k") is not None:
        raise ValueError(f"{where}.k applies to property 'spring' only")
    if prop == "elastic":
        t = _num(e.get("doubler_t", 0.0), f"{where}.doubler_t")
        if t < 0.0:
            raise ValueError(f"{where}.doubler_t must be >= 0")
        out["doubler_t"] = t
    elif e.get("doubler_t") not in (None, 0, 0.0):
        raise ValueError(f"{where}.doubler_t applies to property 'elastic' "
                         "only")
    conn = e.get("connectivity", "beams_to_panel")
    if conn not in PZ_CONNECTIVITY:
        raise ValueError(f"{where}.connectivity must be one of "
                         f"{tuple(PZ_CONNECTIVITY)}, got {conn!r}")
    out["connectivity"] = conn
    return out


def _joint_ends(model) -> Dict[Vec3, List[tuple]]:
    """point key -> [(member, end)] of every bending (non axial-only)
    frame member end."""
    ends: Dict[Vec3, List[tuple]] = {}
    for m in model.members:
        if getattr(m, "axial_limit", "both") != "both":
            continue
        for end, p in (("i", m.pi), ("j", m.pj)):
            ends.setdefault(_pkey(p), []).append((m, end))
    return ends


def _column_sizing(model, ends) -> Tuple[object, float, float, float]:
    """(governing column, d_c, t_p, d_b) of a joint (model-wide rule)."""
    vcols = [m for m, _ in ends if m.kind == "column"
             and math.hypot(m.pj[0] - m.pi[0], m.pj[1] - m.pi[1]) < 1e-6]
    beams = [m for m, _ in ends if m.kind == "beam"]
    if not vcols or not beams:
        return None, 0.0, 0.0, 0.0
    col = max(vcols, key=lambda m: model.sections[m.section].h)
    csec = model.sections[col.section]
    d_b = max(model.sections[m.section].h for m in beams)
    return col, csec.h, csec.b, d_b


def validate_model(model) -> None:
    """Normalise ``model.joint_panel_zones`` in place; every entry must sit
    on a joint where at least one member attaches to the panel and one
    does not, and column-based properties need a sizable vertical column
    and beam (section b/h) there."""
    lst = getattr(model, "joint_panel_zones", None)
    if lst is None:
        model.joint_panel_zones = []
        return
    if not isinstance(lst, list):
        raise ValueError("joint_panel_zones must be a list")
    if not lst:
        return
    norm = [normalize_entry(e, i) for i, e in enumerate(lst)]
    ends = _joint_ends(model)
    seen = set()
    for i, e in enumerate(norm):
        key = _pkey(e["point"])
        where = f"joint_panel_zones[{i}] at {tuple(e['point'])}"
        if key in seen:
            raise ValueError(f"{where}: duplicate joint")
        seen.add(key)
        at = ends.get(key, [])
        attach = PZ_CONNECTIVITY[e["connectivity"]]
        panel = [m for m, _ in at if m.kind in attach]
        other = [m for m, _ in at if m.kind not in attach]
        if not panel or not other:
            raise ValueError(f"{where}: the joint needs at least one member "
                             f"end of kind {attach} (panel side) and one "
                             "other frame member end")
        if e["property"] in ("from_column", "elastic"):
            col, d_c, t_p, d_b = _column_sizing(model, at)
            if col is None:
                raise ValueError(f"{where}: property {e['property']!r} needs "
                                 "a vertical column and a beam at the joint")
            if d_c <= 0.0 or t_p <= 0.0 or d_b <= 0.0:
                raise ValueError(f"{where}: property {e['property']!r} needs "
                                 "section drawing dimensions (b/h) on the "
                                 "column and beams")
    model.joint_panel_zones = norm


def override_keys(model) -> set:
    return {_pkey(e["point"])
            for e in getattr(model, "joint_panel_zones", None) or ()}


def override_specs(model) -> Dict[Vec3, dict]:
    """Panel-zone spring spec per overridden joint (same shape as
    ``compute_panel_zone_springs`` entries + "property", "connectivity",
    "attach")."""
    out: Dict[Vec3, dict] = {}
    if not getattr(model, "joint_panel_zones", None):
        return out
    ends = _joint_ends(model)
    for e in model.joint_panel_zones:
        e = normalize_entry(e)
        key = _pkey(e["point"])
        spec = {"point": key, "property": e["property"],
                "connectivity": e["connectivity"],
                "attach": PZ_CONNECTIVITY[e["connectivity"]]}
        if e["property"] == "spring":
            spec["K"] = float(e["k"])
        else:
            col, d_c, t_p, d_b = _column_sizing(model, ends.get(key, []))
            if col is None or d_c <= 0.0 or t_p <= 0.0 or d_b <= 0.0:
                raise ValueError(f"joint panel zone at {key}: cannot size "
                                 "the panel from the column / beams")
            G = model.materials[model.sections[col.section].material].G
            t_eff = t_p + float(e.get("doubler_t", 0.0))
            spec.update({"K": G * d_c * d_b * t_eff, "G": G, "d_c": d_c,
                         "d_b": d_b, "t_p": t_p,
                         "doubler_t": float(e.get("doubler_t", 0.0))})
        out[key] = spec
    return out


def effective_specs(model, model_wide_fn) -> Dict[Vec3, dict]:
    """Every scissors panel-zone spring of the build: the model-wide ones
    (``model_wide_fn(model)`` when ``panel_zones == "scissors"``) with the
    per-joint overrides replacing / adding joints.  Exactly
    ``model_wide_fn(model)`` (or ``{}``) when there is no override."""
    base = (model_wide_fn(model)
            if getattr(model, "panel_zones", "none") == "scissors" else {})
    if not getattr(model, "joint_panel_zones", None):
        return base
    out = dict(base)
    out.update(override_specs(model))
    return out


def attaches(spec: dict, kind: str) -> bool:
    """Does a member of ``kind`` connect to the panel (duplicate) node?"""
    return kind in spec.get("attach", ("beam",))
