"""Named LINE and AREA spring properties (Wave B, ETABS v22.5 parity).

CONTRACT "Orthotropic materials, slab / deck property types, cold-formed and
composite sections, named line / area springs".

``model.line_spring_properties`` / ``model.area_spring_properties``
(``{name: property}``, emitted only when non-empty)::

    {"kind": "linear" | "compression_only" | "tension_only"
             | "multilinear",
     "k": [k1, k2, k3],          # line: kN/m per m; area: kN/m per m^2
     "nonlinear_dof": "U3",      # c/t-only (default U3)
     "curves": {"U3": [[d, f], ...]}}   # multilinear: f per m / per m^2

DOFs U1 / U2 / U3 are the GLOBAL X / Y / Z directions (U3 = vertical,
negative = settlement).  A property is referenced by
``LineSpring.property`` (inline kx / ky / kz must then be 0) or by
``ShellRegion.area_spring = {"property": name}`` (shell behaviour).

Discretisation (exactly the v0.22 rules): a line spring lumps onto the FE
nodes on its segment with tributary lengths covering the whole segment,
an area spring onto the region's mesh nodes with their tributary areas.
Each node gets a grounded named spring (the B9 point-spring machinery:
one ``zeroLength`` with one uniaxial material per active DOF) whose law is
the property law scaled by the tributary length / area: stiffness
``k * trib``, multilinear forces ``f * trib`` at the same displacements.
Nonlinear laws (c/t-only, multilinear) route static cases to Newton like
every other nonlinear named spring; eigen analyses use the initial
tangent.  Multilinear curves follow :func:`skyframe.core.springprops.
normalize_curve` (positive-only points are mirrored; otherwise the curve
passes through [0, 0]; nonlinear elastic, end segments extrapolated).
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

from . import springprops as _sp

KINDS = ("linear", "compression_only", "tension_only", "multilinear")
DOFS = ("U1", "U2", "U3")
_KEYS = {"kind", "k", "nonlinear_dof", "curves"}


def _num(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def normalize(prop: dict) -> dict:
    out: dict = {"kind": str(prop.get("kind", "linear"))}
    if prop.get("k") is not None:
        out["k"] = [float(v) for v in prop["k"]]
    if "nonlinear_dof" in prop:
        out["nonlinear_dof"] = str(prop["nonlinear_dof"])
    if isinstance(prop.get("curves"), dict):
        out["curves"] = {str(k): [[float(p[0]), float(p[1])] for p in pts]
                         for k, pts in prop["curves"].items()}
    return out


def validate(name: str, prop, what: str = "line") -> None:
    where = f"{what.capitalize()} spring property {name!r}"
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{what} spring property names must be non-empty")
    if not isinstance(prop, dict):
        raise ValueError(f"{where}: must be an object")
    unknown = set(prop) - _KEYS
    if unknown:
        raise ValueError(f"{where}: unknown key(s) {sorted(unknown)}")
    kind = prop.get("kind")
    if kind not in KINDS:
        raise ValueError(f"{where}: kind must be one of {KINDS}")
    k = prop.get("k")
    if k is None and kind != "multilinear":
        raise ValueError(f"{where}: 'k' [k1, k2, k3] is required")
    if k is not None and not (isinstance(k, (list, tuple)) and len(k) == 3
                              and all(_num(v) and v >= 0 for v in k)):
        raise ValueError(f"{where}: k must be 3 finite values >= 0")
    kv = list(k or [0.0, 0.0, 0.0])
    if kind != "multilinear" and "curves" in prop:
        raise ValueError(f"{where}: 'curves' is only valid for multilinear")
    if kind in ("linear", "multilinear") and "nonlinear_dof" in prop:
        raise ValueError(f"{where}: nonlinear_dof is only valid for "
                         "compression_only / tension_only")
    if kind == "linear" and not any(v > 0 for v in kv):
        raise ValueError(f"{where}: at least one k entry must be > 0")
    if kind in ("compression_only", "tension_only"):
        lab = prop.get("nonlinear_dof", "U3")
        if lab not in DOFS:
            raise ValueError(f"{where}: nonlinear_dof must be one of {DOFS}")
        if kv[DOFS.index(lab)] <= 0.0:
            raise ValueError(f"{where}: k on {lab} must be > 0")
    if kind == "multilinear":
        cv = prop.get("curves")
        if not isinstance(cv, dict) or not cv:
            raise ValueError(f"{where}: multilinear needs curves "
                             "{dof: [[d, f], ...]}")
        for lab, pts in cv.items():
            if lab not in DOFS:
                raise ValueError(f"{where}: curve DOF must be one of {DOFS}")
            _sp._validate_curve(f"{where} curve {lab}", pts)


def laws(prop: dict, trib: float) -> List[object]:
    """Six DofLaws (rotations None) of one node with tributary ``trib``."""
    kind = prop["kind"]
    kv = [float(v) for v in (prop.get("k") or [0.0, 0.0, 0.0])]
    out: List[object] = [None] * 6
    for d in range(3):
        if kv[d] > 0.0:
            out[d] = _sp.DofLaw("linear", k=kv[d] * trib)
    if kind in ("compression_only", "tension_only"):
        d = DOFS.index(prop.get("nonlinear_dof", "U3"))
        out[d] = _sp.DofLaw("c_only" if kind == "compression_only"
                            else "t_only", k=kv[d] * trib)
    elif kind == "multilinear":
        for lab, pts in prop["curves"].items():
            xs, ys = _sp.normalize_curve(pts)
            out[DOFS.index(lab)] = _sp.DofLaw(
                "curve", xs=list(xs), ys=[y * trib for y in ys])
    return out


def node_spec(prop: dict, trib: float):
    I = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    return _sp.SpringSpec(I, laws(prop, trib))


def any_nonlinear(model) -> bool:
    lp = getattr(model, "line_spring_properties", None) or {}
    ap = getattr(model, "area_spring_properties", None) or {}
    for ls in getattr(model, "line_springs", []) or []:
        p = lp.get(getattr(ls, "property", None) or "")
        if p and p.get("kind") != "linear":
            return True
    for r in getattr(model, "shells", []) or []:
        asp = getattr(r, "area_spring", None) or {}
        p = ap.get(asp.get("property") or "") if isinstance(asp, dict) \
            else None
        if p and p.get("kind") != "linear":
            return True
    return False


def line_stations(ls, points, tol: float) -> List[Tuple[int, float]]:
    """[(point index, tributary length)] of a line spring (v0.22 rule)."""
    p1 = tuple(float(v) for v in ls.p1)
    p2 = tuple(float(v) for v in ls.p2)
    dv = tuple(p2[i] - p1[i] for i in range(3))
    L = math.sqrt(sum(c * c for c in dv))
    u = tuple(c / L for c in dv)
    st: List[Tuple[float, int]] = []
    for idx, p in enumerate(points):
        t = sum((p[i] - p1[i]) * u[i] for i in range(3))
        if t < -tol or t > L + tol:
            continue
        foot = tuple(p1[i] + t * u[i] for i in range(3))
        if math.dist(p, foot) < tol:
            st.append((t, idx))
    if not st:
        raise ValueError(f"Line spring {tuple(ls.p1)} -> {tuple(ls.p2)}: no "
                         "FE node lies on the line (nothing to attach to)")
    st.sort()
    ts = [t for t, _ in st]
    out = []
    for k, (t, idx) in enumerate(st):
        lo = 0.0 if k == 0 else 0.5 * (ts[k - 1] + t)
        hi = L if k == len(ts) - 1 else 0.5 * (t + ts[k + 1])
        out.append((idx, hi - lo))
    return out


def engine_specs(model, mesh, tol: float) -> List[Tuple[int, object]]:
    """[(real node tag, SpringSpec)] of every named line / area spring."""
    lp = getattr(model, "line_spring_properties", None) or {}
    ap = getattr(model, "area_spring_properties", None) or {}
    out: List[Tuple[int, object]] = []
    for ls in getattr(model, "line_springs", []) or []:
        name = getattr(ls, "property", None)
        if not name:
            continue
        for idx, trib in line_stations(ls, mesh.points, tol):
            out.append((idx + 1, node_spec(lp[name], trib)))
    for r in model.shells:
        asp = getattr(r, "area_spring", None)
        if not isinstance(asp, dict) or not asp.get("property"):
            continue
        prop = ap[asp["property"]]
        for pidx, trib in sorted(mesh.region_trib.get(r.uid, {}).items()):
            out.append((pidx + 1, node_spec(prop, trib)))
    return out


def is_named_line(ls) -> bool:
    return bool(getattr(ls, "property", None))


def is_named_area(region) -> bool:
    asp = getattr(region, "area_spring", None)
    return isinstance(asp, dict) and bool(asp.get("property"))


def validate_model(model) -> None:
    lp = getattr(model, "line_spring_properties", None) or {}
    ap = getattr(model, "area_spring_properties", None) or {}
    for n, p in lp.items():
        validate(n, p, "line")
    for n, p in ap.items():
        validate(n, p, "area")
    for ls in getattr(model, "line_springs", []) or []:
        name = getattr(ls, "property", None)
        if name is None:
            continue
        if name not in lp:
            raise ValueError(f"line spring {tuple(ls.p1)} -> "
                             f"{tuple(ls.p2)}: unknown line spring property "
                             f"{name!r}")
        if ls.kx or ls.ky or ls.kz or ls.compression_only:
            raise ValueError(f"line spring {tuple(ls.p1)} -> "
                             f"{tuple(ls.p2)}: a named property needs the "
                             "inline kx / ky / kz = 0 and no "
                             "compression_only flag")
    for r in model.shells:
        if not is_named_area(r):
            continue
        asp = r.area_spring
        if set(asp) != {"property"}:
            raise ValueError(f"Shell {r.uid}: area_spring with a property "
                             "takes no other key")
        if asp["property"] not in ap:
            raise ValueError(f"Shell {r.uid}: unknown area spring property "
                             f"{asp['property']!r}")
        if r.behavior != "shell":
            raise ValueError(f"Shell {r.uid}: area_spring needs shell "
                             "behavior (membrane slabs have no mesh nodes)")


def backbone_settlement(curve, p: float) -> float:
    """Displacement d (<0 = settlement) at which the area-spring curve
    pressure balances a downward pressure ``p`` (> 0): solves
    ``F(d) = -p`` on the normalised curve (the spring force on the ground
    side is negative in settlement; helper for tests / UI)."""
    xs, ys = _sp.normalize_curve(curve)
    target = -p
    for i in range(len(xs) - 1):
        y0, y1 = ys[i], ys[i + 1]
        if (y0 - target) * (y1 - target) <= 0 and y1 != y0:
            return xs[i] + (target - y0) * (xs[i + 1] - xs[i]) / (y1 - y0)
    raise ValueError("pressure outside the curve")
