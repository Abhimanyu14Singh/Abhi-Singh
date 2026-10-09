"""Multiple named diaphragms per story + ETABS additional mass (model side).

ETABS parity (Define > Diaphragms, Assign > Joint/Shell > Diaphragms,
Assign > Joint/Frame/Shell > Additional Mass).  This module only
normalizes, validates and (de)serializes; the engine side lives in
:mod:`skyframe.engine.diaphragms`.

Model data (all defaults reproduce the pre-existing results exactly)::

    BuildingModel.diaphragms        {name: {"type": "rigid" | "semi_rigid"}}
    BuildingModel.joint_diaphragms  [{"point": [x, y, z], "diaphragm": name}]
    ShellRegion.diaphragm           "" (none) | name
    ShellRegion.additional_mass     t/m^2 (>= 0)
    FrameMember.additional_mass     t/m   (>= 0)
    FrameMember.additional_mass_mode  "lumped" | "distributed"
    NodalMass.mrx / mry / mrz       t*m^2 rotational inertia (>= 0)

Serialization: every new key is emitted ONLY when it differs from its
default, so models that do not use the feature serialize byte-identically.
"""

from __future__ import annotations

import math
from typing import Dict, List

DIAPHRAGM_TYPES = ("rigid", "semi_rigid")
ADDITIONAL_MASS_MODES = ("lumped", "distributed")
_TOL = 1e-6


def has_named(model) -> bool:
    """True when any joint or shell carries a named-diaphragm assignment."""
    if getattr(model, "joint_diaphragms", None):
        return True
    return any(getattr(r, "diaphragm", "") for r in model.shells)


def has_additional_mass(model) -> bool:
    if any(getattr(m, "additional_mass", 0.0) for m in model.members):
        return True
    return any(getattr(r, "additional_mass", 0.0) for r in model.shells)


# --------------------------------------------------------------------------- #
# (de)serialization
# --------------------------------------------------------------------------- #
def diaphragms_to_dict(model) -> dict:
    out: dict = {}
    if getattr(model, "diaphragms", None):
        out["diaphragms"] = {n: dict(v) for n, v in model.diaphragms.items()}
    if getattr(model, "joint_diaphragms", None):
        out["joint_diaphragms"] = [
            {"point": [float(v) for v in e["point"]],
             "diaphragm": str(e["diaphragm"])}
            for e in model.joint_diaphragms]
    return out


def diaphragms_from_dict(mdl, d: dict) -> None:
    dd = d.get("diaphragms")
    if dd is not None:
        if not isinstance(dd, dict):
            raise ValueError("diaphragms must be an object {name: {type}}")
        mdl.diaphragms = {}
        for n, v in dd.items():
            if isinstance(v, str):
                v = {"type": v}
            if not isinstance(v, dict):
                raise ValueError(f"diaphragms[{n!r}] must be an object")
            mdl.diaphragms[str(n)] = {"type": str(v.get("type", "rigid"))}
    jd = d.get("joint_diaphragms")
    if jd is not None:
        if not isinstance(jd, list):
            raise ValueError("joint_diaphragms must be a list")
        mdl.joint_diaphragms = []
        for e in jd:
            if not isinstance(e, dict) or "point" not in e:
                raise ValueError("joint_diaphragms entries need "
                                 "{point, diaphragm}")
            mdl.joint_diaphragms.append({
                "point": [float(v) for v in e["point"]],
                "diaphragm": str(e.get("diaphragm", ""))})


def member_extra_to_dict(m) -> dict:
    out = {}
    if getattr(m, "additional_mass", 0.0):
        out["additional_mass"] = m.additional_mass
    if getattr(m, "additional_mass_mode", "lumped") != "lumped":
        out["additional_mass_mode"] = m.additional_mass_mode
    return out


def member_extra_from_dict(md: dict) -> dict:
    out = {}
    if "additional_mass" in md:
        out["additional_mass"] = float(md["additional_mass"] or 0.0)
    if "additional_mass_mode" in md:
        out["additional_mass_mode"] = str(md["additional_mass_mode"])
    return out


def shell_extra_to_dict(r) -> dict:
    out = {}
    if getattr(r, "diaphragm", ""):
        out["diaphragm"] = r.diaphragm
    if getattr(r, "additional_mass", 0.0):
        out["additional_mass"] = r.additional_mass
    return out


def shell_extra_from_dict(rd: dict) -> dict:
    out = {}
    if rd.get("diaphragm"):
        out["diaphragm"] = str(rd["diaphragm"])
    if "additional_mass" in rd:
        out["additional_mass"] = float(rd["additional_mass"] or 0.0)
    return out


def nodal_mass_extra_to_dict(nm) -> dict:
    return {k: getattr(nm, k) for k in ("mrx", "mry", "mrz")
            if getattr(nm, k, 0.0)}


def nodal_mass_extra_from_dict(md: dict) -> dict:
    return {k: float(md[k]) for k in ("mrx", "mry", "mrz") if k in md}


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def _nonneg(v, what: str) -> None:
    if not isinstance(v, (int, float)) or isinstance(v, bool) \
            or not math.isfinite(v) or v < 0.0:
        raise ValueError(f"{what} must be a finite number >= 0 (got {v!r})")


def validate_diaphragms(model) -> None:
    dia = getattr(model, "diaphragms", None) or {}
    if not isinstance(dia, dict):
        raise ValueError("diaphragms must be an object {name: {type}}")
    for n, v in dia.items():
        if not n or not isinstance(n, str):
            raise ValueError("diaphragm names must be non-empty strings")
        t = v.get("type") if isinstance(v, dict) else None
        if t not in DIAPHRAGM_TYPES:
            raise ValueError(f"diaphragms[{n!r}].type must be one of "
                             f"{DIAPHRAGM_TYPES}, got {t!r}")
    elevs = [s.elevation for s in model.stories]
    seen: Dict[tuple, str] = {}
    for e in getattr(model, "joint_diaphragms", None) or []:
        name = e.get("diaphragm")
        if name not in dia:
            raise ValueError(f"joint_diaphragms: unknown diaphragm "
                             f"{name!r} (define it in diaphragms)")
        p = e.get("point")
        if not isinstance(p, (list, tuple)) or len(p) != 3:
            raise ValueError("joint_diaphragms point must be [x, y, z]")
        if not any(abs(float(p[2]) - z) < _TOL for z in elevs):
            raise ValueError(f"joint_diaphragms point {list(p)} is not at a "
                             "story elevation")
        key = tuple(round(float(v), 6) for v in p)
        if key in seen and seen[key] != name:
            raise ValueError(f"joint {list(p)} assigned to two diaphragms "
                             f"({seen[key]!r}, {name!r})")
        seen[key] = name
    for r in model.shells:
        name = getattr(r, "diaphragm", "")
        if name:
            if name not in dia:
                raise ValueError(f"shell {r.uid}: unknown diaphragm "
                                 f"{name!r}")
            zs = [c[2] for c in r.corners]
            if max(zs) - min(zs) > _TOL:
                raise ValueError(f"shell {r.uid}: only horizontal regions "
                                 "(slabs) can be assigned to a diaphragm")
        _nonneg(getattr(r, "additional_mass", 0.0),
                f"shell {r.uid} additional_mass")
    for m in model.members:
        _nonneg(getattr(m, "additional_mass", 0.0),
                f"member {m.uid} additional_mass")
        mode = getattr(m, "additional_mass_mode", "lumped")
        if mode not in ADDITIONAL_MASS_MODES:
            raise ValueError(f"member {m.uid}: additional_mass_mode must be "
                             f"one of {ADDITIONAL_MASS_MODES}, got {mode!r}")
    for nm in model.nodal_masses:
        for k in ("mrx", "mry", "mrz"):
            _nonneg(getattr(nm, k, 0.0), f"nodal mass {k}")


def assigned_names(model) -> List[str]:
    names = {e["diaphragm"] for e in getattr(model, "joint_diaphragms", [])}
    names |= {r.diaphragm for r in model.shells
              if getattr(r, "diaphragm", "")}
    return sorted(names)
