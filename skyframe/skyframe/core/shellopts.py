"""Wall / floor auto-mesh options, the joint-pattern library and shell
uniform load sets (ETABS Assign > Shell > Wall Auto Mesh Options / Floor
Auto Mesh Options, Define > Joint Patterns, Define > Shell Uniform Load
Sets) -- plus the analysis-time model expansion shared with curved frames
(:mod:`skyframe.core.curved`).

Mesh options (``ShellRegion``; ``None`` = the pre-existing mesh)
-------------------------------------------------------------------
``mesh_divisions``: ``{"n1": int, "n2": int}`` (explicit n1 x n2) or
``{"max_size": float}`` (maximum element size).

* 4-corner structured mesher: ``n1`` cells along corner 0 -> 1, ``n2``
  along 0 -> 3; ``max_size`` -> ``ceil(edge / max_size)`` per direction
  (the legacy ``mesh_size`` rule is ``round(edge / mesh_size)``).
* polygon mesher: the u / v interval steps are ``bbox_u / n1`` and
  ``bbox_v / n2`` (u, v = the region plane frame); each interval between
  mandatory mesh lines is still divided into ``ceil(len / step)`` parts, so
  the mesh has AT LEAST n1 x n2 cells (exactly n1 x n2 when no interior
  mesh line is forced).  ``max_size`` is the step in both directions.

``floor_mesh`` (slabs, shell behavior): ``{"mode": "default" |
"rectangular" | "cookie_cut", "max_size": float | None, "at_beams": bool,
"at_walls": bool, "at_grids": bool}`` (missing keys: "default", None,
True, True, True).  A slab with ``floor_mesh`` set is meshed by the
polygon mesher even with 4 corners.  Mapping onto the polygon mesher line
sets (:mod:`skyframe.core.polymesh`):

* ``at_beams`` False: in-plane frame members force neither cuts nor mesh
  lines (their ends on element edges still join by the conformity pass);
* ``at_walls`` False: traces of non-coplanar shells (walls) are not
  constraint segments (coplanar neighbour edges are always kept);
* ``at_grids`` False: no optional grid lines;
* ``mode "default"``: constraint segments both cut the cells and force
  mesh lines through their ends (the existing behaviour);
* ``mode "cookie_cut"``: constraint segments cut the cells (cookie cut)
  but force no mesh lines;
* ``mode "rectangular"``: constraint segments force mesh lines through
  their ends but never cut cells (a pure rectangular mesh clipped only by
  the outline and openings);
* ``max_size`` replaces ``mesh_size`` as the maximum element size
  (``mesh_divisions`` wins over it).

Joint patterns / shell load sets (BuildingModel)
------------------------------------------------
``joint_patterns = {name: {"type": "linear", "a", "b", "c", "d",
"zero_negative"?, "zero_positive"?}}``; ``AreaLoad.joint_pattern`` may be
the inline dict (unchanged) or a NAME from this library.
``shell_load_sets = {name: {pattern: q | {"q", "direction"?,
"projected"?}}}``; ``ShellRegion.load_set = name`` adds, at analysis time,
one AreaLoad per entry (appended to that pattern after its own area
loads, in shell order).
"""

from __future__ import annotations

import copy
import math
from typing import Optional, Tuple

FLOOR_MESH_MODES = ("default", "rectangular", "cookie_cut")
_FM_FLAGS = ("at_beams", "at_walls", "at_grids")


def _num(v) -> bool:
    return (not isinstance(v, bool) and isinstance(v, (int, float))
            and math.isfinite(v))


# --------------------------------------------------------------------------- #
# mesh options
# --------------------------------------------------------------------------- #
def normalize_mesh_divisions(d) -> Optional[dict]:
    if d is None:
        return None
    if not isinstance(d, dict):
        raise ValueError("mesh_divisions must be an object or null")
    keys = set(d)
    if keys == {"max_size"}:
        v = d["max_size"]
        if not _num(v) or v <= 0.0:
            raise ValueError("mesh_divisions.max_size must be a finite "
                             "value > 0")
        return {"max_size": float(v)}
    if keys == {"n1", "n2"}:
        out = {}
        for k in ("n1", "n2"):
            v = d[k]
            if not _num(v) or float(v) != int(v) or int(v) < 1:
                raise ValueError(f"mesh_divisions.{k} must be an integer "
                                 ">= 1")
            out[k] = int(v)
        return out
    raise ValueError('mesh_divisions must be {"n1", "n2"} or {"max_size"}')


def normalize_floor_mesh(d) -> Optional[dict]:
    if d is None:
        return None
    if not isinstance(d, dict):
        raise ValueError("floor_mesh must be an object or null")
    bad = set(d) - {"mode", "max_size", *_FM_FLAGS}
    if bad:
        raise ValueError(f"floor_mesh: unknown key(s) {sorted(bad)}")
    mode = d.get("mode", "default")
    if mode not in FLOOR_MESH_MODES:
        raise ValueError(f"floor_mesh.mode must be one of {FLOOR_MESH_MODES}")
    ms = d.get("max_size")
    if ms is not None:
        if not _num(ms) or ms <= 0.0:
            raise ValueError("floor_mesh.max_size must be a finite value > 0")
        ms = float(ms)
    out = {"mode": mode, "max_size": ms}
    for k in _FM_FLAGS:
        v = d.get(k, True)
        if not isinstance(v, bool):
            raise ValueError(f"floor_mesh.{k} must be a bool")
        out[k] = v
    return out


def structured_divisions(region, lx: float, ly: float) -> Tuple[int, int]:
    """(nx, ny) of the 4-corner structured mesher."""
    md = getattr(region, "mesh_divisions", None)
    if md is None:
        return (max(1, round(lx / region.mesh_size)),
                max(1, round(ly / region.mesh_size)))
    if "n1" in md:
        return int(md["n1"]), int(md["n2"])
    h = float(md["max_size"])
    return (max(1, int(math.ceil(lx / h - 1e-9))),
            max(1, int(math.ceil(ly / h - 1e-9))))


def uses_polygon_mesher(region) -> bool:
    """A 4-corner slab with floor_mesh options takes the polygon mesher."""
    return (getattr(region, "floor_mesh", None) is not None
            and region.behavior == "shell")


def floor_flags(region) -> dict:
    fm = getattr(region, "floor_mesh", None)
    if fm is None:
        return {"mode": "default", "at_beams": True, "at_walls": True,
                "at_grids": True}
    return {k: fm.get(k, True) for k in _FM_FLAGS} | {
        "mode": fm.get("mode", "default")}


def polygon_steps(region, ulo: float, uhi: float, vlo: float,
                  vhi: float) -> Tuple[float, float]:
    """(hu, hv) maximum interval steps of the polygon mesher."""
    h = float(region.mesh_size)
    fm = getattr(region, "floor_mesh", None)
    if fm is not None and fm.get("max_size"):
        h = float(fm["max_size"])
    md = getattr(region, "mesh_divisions", None)
    if md is None:
        return h, h
    if "n1" in md:
        return ((uhi - ulo) / int(md["n1"]) or h,
                (vhi - vlo) / int(md["n2"]) or h)
    return float(md["max_size"]), float(md["max_size"])


# --------------------------------------------------------------------------- #
# joint patterns / load sets
# --------------------------------------------------------------------------- #
def normalize_joint_pattern(name: str, jp) -> dict:
    tag = f"joint pattern {name!r}"
    if not isinstance(jp, dict):
        raise ValueError(f"{tag} must be an object")
    bad = set(jp) - {"type", "a", "b", "c", "d", "zero_negative",
                     "zero_positive"}
    if bad:
        raise ValueError(f"{tag}: unknown key(s) {sorted(bad)}")
    if jp.get("type", "linear") != "linear":
        raise ValueError(f"{tag}: type must be 'linear'")
    out = {"type": "linear"}
    for k in ("a", "b", "c", "d"):
        v = jp.get(k, 0.0)
        if not _num(v):
            raise ValueError(f"{tag}: {k} must be finite (got {v!r})")
        out[k] = float(v)
    for k in ("zero_negative", "zero_positive"):
        v = jp.get(k, False)
        if not isinstance(v, bool):
            raise ValueError(f"{tag}: {k} must be a bool")
        if v:
            out[k] = True
    if out.get("zero_negative") and out.get("zero_positive"):
        raise ValueError(f"{tag}: cannot zero both signs")
    return out


def _load_set_entry(sname: str, pname: str, v) -> dict:
    tag = f"shell load set {sname!r}, pattern {pname!r}"
    if _num(v):
        return {"q": float(v)}
    if not isinstance(v, dict):
        raise ValueError(f"{tag}: value must be a number or "
                         '{"q", "direction"?, "projected"?}')
    bad = set(v) - {"q", "direction", "projected"}
    if bad:
        raise ValueError(f"{tag}: unknown key(s) {sorted(bad)}")
    if not _num(v.get("q")):
        raise ValueError(f"{tag}: q must be a finite number")
    out = {"q": float(v["q"])}
    if v.get("direction", "gravity") != "gravity":
        out["direction"] = str(v["direction"])
    if v.get("projected", False):
        out["projected"] = v["projected"]
    return out


def normalize_load_set(sname: str, s) -> dict:
    if not isinstance(s, dict):
        raise ValueError(f"shell load set {sname!r} must be an object "
                         "{pattern: q}")
    return {str(p): _load_set_entry(sname, str(p), v) for p, v in s.items()}


def validate_model(model) -> None:
    """Normalise + cross-check mesh options, joint patterns and load sets."""
    from .loads_ext import validate_area_load
    from .model import AreaLoad
    jps = getattr(model, "joint_patterns", None) or {}
    if not isinstance(jps, dict):
        raise ValueError("joint_patterns must be an object")
    model.joint_patterns = {str(k): normalize_joint_pattern(k, v)
                            for k, v in jps.items()}
    sets = getattr(model, "shell_load_sets", None) or {}
    if not isinstance(sets, dict):
        raise ValueError("shell_load_sets must be an object")
    model.shell_load_sets = {str(k): normalize_load_set(k, v)
                             for k, v in sets.items()}
    for sname, s in model.shell_load_sets.items():
        for p in s:
            if p not in model.patterns:
                raise ValueError(f"shell load set {sname!r}: unknown load "
                                 f"pattern {p!r}")
    for r in model.shells:
        tag = f"Shell {r.uid}"
        try:
            r.mesh_divisions = normalize_mesh_divisions(
                getattr(r, "mesh_divisions", None))
            r.floor_mesh = normalize_floor_mesh(getattr(r, "floor_mesh",
                                                        None))
        except ValueError as exc:
            raise ValueError(f"{tag}: {exc}") from None
        if r.floor_mesh is not None and r.kind != "slab":
            raise ValueError(f"{tag}: floor_mesh applies to slabs only")
        if ((r.floor_mesh is not None or r.mesh_divisions is not None)
                and r.behavior != "shell"):
            raise ValueError(f"{tag}: mesh options need shell behavior "
                             "(membrane regions are not meshed)")
        ls = getattr(r, "load_set", "") or ""
        if not isinstance(ls, str):
            raise ValueError(f"{tag}: load_set must be a string")
        if ls:
            if ls not in model.shell_load_sets:
                raise ValueError(f"{tag}: unknown shell load set {ls!r}")
            for p, e in model.shell_load_sets[ls].items():
                validate_area_load(_area_load(AreaLoad, r.uid, e), r,
                                   f"{p} (load set {ls!r})")
    for pat in model.patterns.values():
        for al in pat.area_loads:
            jp = getattr(al, "joint_pattern", None)
            if isinstance(jp, str) and jp not in model.joint_patterns:
                raise ValueError(f"Pattern {pat.name}: area load on "
                                 f"{al.region_uid!r} references unknown "
                                 f"joint pattern {jp!r}")


def _area_load(cls, uid: str, e: dict):
    return cls(uid, float(e["q"]), direction=e.get("direction", "gravity"),
               projected=bool(e.get("projected", False)))


# --------------------------------------------------------------------------- #
# JSON helpers
# --------------------------------------------------------------------------- #
def shell_to_dict(r) -> dict:
    out = {}
    if getattr(r, "mesh_divisions", None) is not None:
        out["mesh_divisions"] = dict(r.mesh_divisions)
    if getattr(r, "floor_mesh", None) is not None:
        out["floor_mesh"] = dict(r.floor_mesh)
    if getattr(r, "load_set", ""):
        out["load_set"] = r.load_set
    return out


def model_to_dict(model) -> dict:
    out = {}
    if getattr(model, "joint_patterns", None):
        out["joint_patterns"] = copy.deepcopy(model.joint_patterns)
    if getattr(model, "shell_load_sets", None):
        out["shell_load_sets"] = copy.deepcopy(model.shell_load_sets)
    return out


def model_from_dict(model, d: dict) -> None:
    """Read every field of this module and of curved frames (absent =
    defaults)."""
    for m, md in zip(model.members, d.get("members") or []):
        c = md.get("curve")
        m.curve = copy.deepcopy(c) if c is not None else None
    for r, rd in zip(model.shells, d.get("shells") or []):
        r.mesh_divisions = (dict(rd["mesh_divisions"])
                            if rd.get("mesh_divisions") is not None else None)
        r.floor_mesh = (dict(rd["floor_mesh"])
                        if rd.get("floor_mesh") is not None else None)
        r.load_set = str(rd.get("load_set") or "")
    jp = d.get("joint_patterns")
    model.joint_patterns = copy.deepcopy(jp) if jp else {}
    ls = d.get("shell_load_sets")
    model.shell_load_sets = copy.deepcopy(ls) if ls else {}


# --------------------------------------------------------------------------- #
# analysis expansion (engine entry)
# --------------------------------------------------------------------------- #
def needs_expansion(model) -> bool:
    if any(getattr(m, "curve", None) is not None for m in model.members):
        return True
    if any(getattr(r, "load_set", "") for r in model.shells):
        return True
    return any(isinstance(getattr(al, "joint_pattern", None), str)
               for pat in model.patterns.values() for al in pat.area_loads)


def expand_for_analysis(model):
    """The model the engine analyses.  Returns ``model`` ITSELF when no
    curved member, load set or named joint pattern is used (bit-identical
    default); otherwise a deep copy with load sets expanded into area
    loads, joint-pattern names resolved, and curved members replaced by
    their chords (``copy.curved_info`` = the stitching map)."""
    if not needs_expansion(model):
        return model
    from .model import AreaLoad
    from .curved import expand_members
    new = copy.deepcopy(model)
    jps = getattr(new, "joint_patterns", None) or {}
    for r in new.shells:
        ls = getattr(r, "load_set", "") or ""
        if not ls:
            continue
        for p, e in normalize_load_set(ls, new.shell_load_sets[ls]).items():
            new.patterns[p].area_loads.append(_area_load(AreaLoad, r.uid, e))
        r.load_set = ""
    for pat in new.patterns.values():
        for al in pat.area_loads:
            jp = getattr(al, "joint_pattern", None)
            if isinstance(jp, str):
                al.joint_pattern = dict(jps[jp])
    new.curved_info = expand_members(new)
    return new
