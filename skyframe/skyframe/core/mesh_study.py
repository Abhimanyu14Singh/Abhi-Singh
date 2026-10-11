"""Mesh-sensitivity study (Analyze > Mesh Sensitivity Study).

CONTRACT "Mesh sensitivity study and accuracy advisor".

The study re-runs selected LINEAR STATIC load cases and/or the MODAL case
on refined copies of the model and reports how the key response quantities
change with the mesh.  It reuses the normal analysis machinery
(:class:`skyframe.engine.opensees_engine.OpenSeesEngine` ``run_static`` /
``run_modal``) -- there is no separate solver.

Refinement level ``f`` (integer >= 1) applied to a DEEP COPY of the model
(the caller's / saved model is never mutated):

* level 1 is the model exactly as defined (the same results as a normal
  run);
* shell regions meshed by the 4-corner structured mesher get
  ``mesh_divisions = {"n1": f * nx, "n2": f * ny}`` where ``(nx, ny)`` are
  the region's level-1 divisions -- every level is a strict NESTED
  refinement (each level-1 element is split f x f);
* shell regions meshed by the polygon mesher (3 / 5+ corners or
  ``floor_mesh`` set): every maximum element size (``mesh_size``,
  ``floor_mesh.max_size``, ``mesh_divisions.max_size``) is divided by
  ``f`` and ``mesh_divisions.n1/n2`` are multiplied by ``f``;
* frame members (axial-only members excepted -- they are never auto
  meshed): the member's EFFECTIVE auto-mesh options (member dict or the
  model default; all-off when none) are copied onto the member with
  ``min_segments = f * (min_segments or 1)`` and ``max_length / f``.
  Joint / intersection flags are kept unchanged.

Quantities (one result ROW per case x quantity component):

* ``roof_disp`` -- static cases: at level 1 the roof node (highest
  structural node elevation) and translation component UX / UY / UZ with
  the largest magnitude are picked; every level reports the signed value
  of that component at that same POINT (``row["point"]``; nested meshes
  keep the node, otherwise the nearest node is used) (m);
* ``periods`` -- MODAL: ``T1 .. Tk`` (k = min(3, num_modes, modes found);
  the eigen solve requests min(num_modes, 3) modes) (s);
* ``base_reactions`` -- static cases: the total base reaction components
  FX FY FZ (kN) / MX MY MZ (kN-m) that are non-negligible at level 1
  (|v| > 1e-6 x the largest force resp. moment component);
* ``max_shell_moment`` -- static cases: max |Mxx|, |Myy| over every shell
  element's gauss-averaged resultants (kN-m/m);
* ``max_frame_moment`` -- static cases: max |M2|, |M3| over every frame
  output station (kN-m).

Per row: ``values`` (one per level), ``rel_change`` (percent change from the
previous level, ``None`` for the first), a Richardson-extrapolated estimate
(see :func:`richardson`), the estimated discretisation error of the finest
level ``est_error_pct = |f_finest - f_rich| / |f_rich| * 100`` and the
verdict ``"converged within X%"`` (``X`` = that error, 2 significant
digits ROUNDED UP, ``"<0.01%"`` below 0.005 %) when ``est_error_pct <= tolerance_pct``, else ``"not converged
(est. error X% > T%)"``.
"""

from __future__ import annotations

import copy
import math
import time
import warnings
from typing import Dict, List, Optional, Sequence, Tuple

from .model import MODAL_CASE, BuildingModel

Vec3 = Tuple[float, float, float]

QUANTITIES = ("roof_disp", "periods", "base_reactions", "max_shell_moment",
              "max_frame_moment")
DEFAULT_LEVELS = (1, 2, 4)
MAX_LEVEL = 16
MAX_LEVELS = 6
DEFAULT_TOLERANCE_PCT = 5.0
DEFAULT_MAX_ELEMENTS = 20000      # shell elements + frame segments, finest
ASSUMED_ORDER = 2.0               # Richardson order with 2 levels / fallback
ORDER_RANGE = (0.5, 6.0)          # accepted observed orders
N_PERIODS = 3
_ROOF_TOL = 1e-6
_SIG = 1e-6                       # significance of a reaction component

# quantity -> unit dimension (the UI converts with js/units.js)
DIMS = {"roof_disp": "length", "periods": "time", "base_force": "force",
        "base_moment": "moment", "max_shell_moment": "moment_per_length",
        "max_frame_moment": "moment"}


# --------------------------------------------------------------------------- #
# request normalisation
# --------------------------------------------------------------------------- #
def _int_like(v) -> bool:
    return (not isinstance(v, bool) and isinstance(v, (int, float))
            and math.isfinite(v) and float(v) == int(v))


def normalize_levels(levels) -> List[int]:
    if levels is None:
        return list(DEFAULT_LEVELS)
    if not isinstance(levels, (list, tuple)) or not levels:
        raise ValueError("'levels' must be a non-empty list of integers")
    out = []
    for v in levels:
        if not _int_like(v) or not 1 <= int(v) <= MAX_LEVEL:
            raise ValueError(f"'levels' entries must be integers in "
                             f"[1, {MAX_LEVEL}]")
        out.append(int(v))
    out = sorted(set(out))
    if len(out) < 2:
        raise ValueError("'levels' needs at least 2 distinct refinement "
                         "factors")
    if len(out) > MAX_LEVELS:
        raise ValueError(f"'levels' allows at most {MAX_LEVELS} factors")
    return out


def linear_static_cases(model: BuildingModel) -> List[str]:
    """Static cases solved linearly (geometric == linear), in model order."""
    return [n for n, c in model.cases.items()
            if c.effective_geometric == "linear"]


def default_cases(model: BuildingModel) -> List[str]:
    """Every linear static case + MODAL (when modes are requested), minus
    the cases switched off in Set Load Cases to Run."""
    off = set(getattr(model, "cases_not_run", []) or [])
    out = [n for n in linear_static_cases(model) if n not in off]
    if (model.num_modes > 0 and MODAL_CASE not in off
            and MODAL_CASE not in model.cases):
        out.append(MODAL_CASE)
    return out


def normalize_cases(model: BuildingModel, cases) -> List[str]:
    if cases is None:
        out = default_cases(model)
        if not out:
            raise ValueError("the model has no linear static or modal case "
                             "to study")
        return out
    if not isinstance(cases, (list, tuple)) or not cases or not all(
            isinstance(c, str) for c in cases):
        raise ValueError("'cases' must be a non-empty list of case names")
    lin = set(linear_static_cases(model))
    out = []
    for c in cases:
        if c in out:
            continue
        if c == MODAL_CASE and c not in model.cases:
            if model.num_modes <= 0:
                raise ValueError("MODAL: the model requests no modes "
                                 "(num_modes = 0)")
        elif c not in model.cases:
            raise ValueError(f"unknown load case {c!r}")
        elif c not in lin:
            raise ValueError(f"case {c!r} is not a linear static case "
                             "(P-Delta / large-displacement cases are not "
                             "studied)")
        out.append(c)
    return out


def normalize_quantities(q) -> List[str]:
    if q is None:
        return list(QUANTITIES)
    if not isinstance(q, (list, tuple)) or not q:
        raise ValueError("'quantities' must be a non-empty list")
    bad = [x for x in q if x not in QUANTITIES]
    if bad:
        raise ValueError(f"unknown quantit(y/ies) {bad}; allowed: "
                         f"{list(QUANTITIES)}")
    return [x for x in QUANTITIES if x in q]


# --------------------------------------------------------------------------- #
# refinement (on a deep copy)
# --------------------------------------------------------------------------- #
def _structured(region) -> bool:
    from .polymesh import is_polygon_region
    from .shellopts import uses_polygon_mesher
    return not (is_polygon_region(region) or uses_polygon_mesher(region))


def _base_divisions(region) -> Tuple[int, int]:
    from .mesh import _norm, _sub
    from .shellopts import structured_divisions
    c = [tuple(map(float, p)) for p in region.corners]
    lx = 0.5 * (_norm(_sub(c[1], c[0])) + _norm(_sub(c[2], c[3])))
    ly = 0.5 * (_norm(_sub(c[3], c[0])) + _norm(_sub(c[2], c[1])))
    return structured_divisions(region, lx, ly)


def refine_model(model: BuildingModel, level: int) -> BuildingModel:
    """A refined DEEP COPY of ``model`` (level 1: an unchanged copy)."""
    new = copy.deepcopy(model)
    f = int(level)
    if f == 1:
        return new
    from . import framemesh as _fm
    for r in new.shells:
        if r.behavior != "shell":
            continue                    # membrane slabs are never meshed
        if _structured(r):
            nx, ny = _base_divisions(r)
            r.mesh_divisions = {"n1": f * nx, "n2": f * ny}
            continue
        r.mesh_size = float(r.mesh_size) / f
        fm = getattr(r, "floor_mesh", None)
        if fm is not None and fm.get("max_size"):
            fm = dict(fm)
            fm["max_size"] = float(fm["max_size"]) / f
            r.floor_mesh = fm
        md = getattr(r, "mesh_divisions", None)
        if md is not None:
            r.mesh_divisions = ({"n1": f * int(md["n1"]),
                                 "n2": f * int(md["n2"])} if "n1" in md
                                else {"max_size": float(md["max_size"]) / f})
    for m in new.members:
        if getattr(m, "axial_limit", "both") != "both":
            continue                    # Truss members: never auto-meshed
        am = getattr(m, "auto_mesh", None)
        if am is None:
            am = getattr(new, "frame_auto_mesh", None)
        am = dict(_fm.normalize_auto_mesh(am or {}))
        am["min_segments"] = f * int(am["min_segments"] or 1)
        if am["max_length"] is not None:
            am["max_length"] = float(am["max_length"]) / f
        m.auto_mesh = am
    return new


# --------------------------------------------------------------------------- #
# Richardson extrapolation
# --------------------------------------------------------------------------- #
def _order_residual(p: float, h: Sequence[float], r: float) -> float:
    a = h[0] ** p - h[1] ** p
    b = h[1] ** p - h[2] ** p
    return a / b - r


def observed_order(h: Sequence[float], f: Sequence[float]
                   ) -> Optional[float]:
    """Observed convergence order p from three solutions on mesh sizes
    h0 > h1 > h2 (``f = C + A h^p``); ``None`` when not monotone or the
    order is outside ``ORDER_RANGE``."""
    d1, d2 = f[0] - f[1], f[1] - f[2]
    if d1 == 0.0 or d2 == 0.0 or d1 * d2 < 0.0:
        return None
    ratio = d1 / d2
    lo, hi = ORDER_RANGE
    glo, ghi = _order_residual(lo, h, ratio), _order_residual(hi, h, ratio)
    if glo * ghi > 0.0:
        return None
    for _ in range(200):                    # bisection (monotone in p)
        mid = 0.5 * (lo + hi)
        g = _order_residual(mid, h, ratio)
        if g == 0.0:
            return mid
        if (g < 0.0) == (glo < 0.0):
            lo, glo = mid, g
        else:
            hi = mid
    return 0.5 * (lo + hi)


def richardson(levels: Sequence[int], values: Sequence[float]) -> dict:
    """Richardson extrapolation of a mesh sequence.

    Mesh size ``h = 1 / level``.  With >= 3 levels the order ``p`` is
    observed from the last three (generalised for unequal ratios); with 2
    levels, or when the last three are not monotone / give ``p`` outside
    ``ORDER_RANGE``, ``p = ASSUMED_ORDER`` is used on the last two
    (``order_assumed`` True).  ``estimate = f_n + (f_n - f_{n-1}) /
    ((h_{n-1}/h_n)^p - 1)``.
    """
    h = [1.0 / float(lv) for lv in levels]
    f = [float(v) for v in values]
    if any(not math.isfinite(v) for v in f):
        return {"estimate": None, "order": None, "order_assumed": False}
    if f[-1] == f[-2]:
        return {"estimate": f[-1], "order": None, "order_assumed": False}
    p, assumed = None, False
    if len(f) >= 3:
        p = observed_order(h[-3:], f[-3:])
    if p is None:
        p, assumed = ASSUMED_ORDER, True
    r = h[-2] / h[-1]
    est = f[-1] + (f[-1] - f[-2]) / (r ** p - 1.0)
    return {"estimate": est, "order": p, "order_assumed": assumed}


def _pct(a: float, b: float, scale: float) -> float:
    den = max(abs(b), scale)
    return 0.0 if den == 0.0 else abs(a - b) / den * 100.0


def _fmt_pct(x: float) -> str:
    """Percent rounded UP to 2 significant digits (a conservative claim)."""
    if x < 0.005:
        return "<0.01%"
    scale = 10.0 ** (math.floor(math.log10(x)) - 1)
    v = math.ceil(x / scale - 1e-9) * scale
    return f"{float(f'{v:.2g}'):g}%"


def make_row(case: str, quantity: str, key: str, label: str, dim: str,
             levels: Sequence[int], values: Sequence[float],
             tolerance_pct: float) -> dict:
    """One result row (values, relative change, Richardson, verdict)."""
    vals = [float(v) for v in values]
    # magnitudes below this are "zero" for the relative measures
    scale = 1e-12 * max([abs(v) for v in vals] + [0.0])
    rel = [None] + [(None if max(abs(vals[k]), scale) == 0.0 else
                     (vals[k] - vals[k - 1]) / max(abs(vals[k]), scale)
                     * 100.0) for k in range(1, len(vals))]
    rich = richardson(levels, vals)
    est = rich["estimate"]
    if est is None:
        err = None
        ok = False
        verdict = "not converged (non-finite value)"
    else:
        err = _pct(vals[-1], est, scale)
        ok = err <= tolerance_pct
        verdict = (f"converged within {_fmt_pct(err)}" if ok else
                   f"not converged (est. error {_fmt_pct(err)} > "
                   f"{_fmt_pct(tolerance_pct)})")
    return {"case": case, "quantity": quantity, "key": key, "label": label,
            "dim": dim, "values": vals, "rel_change_pct": rel,
            "richardson": est, "order": rich["order"],
            "order_assumed": rich["order_assumed"],
            "est_error_pct": err, "converged": ok, "verdict": verdict}


# --------------------------------------------------------------------------- #
# quantity extraction
# --------------------------------------------------------------------------- #
def _roof_nodes(asm) -> List[int]:
    coords = asm.struct_coords
    if not coords:
        return []
    zmax = max(c[2] for c in coords.values())
    anchors = getattr(asm, "anchor_tags", set()) or set()
    return [t for t, c in coords.items()
            if abs(c[2] - zmax) < _ROOF_TOL and t not in anchors]


def _roof_pick(res, asm, roof: List[int]) -> Optional[Tuple[int, Vec3]]:
    """(component, point) of the largest roof translation (level 1)."""
    best, pick = -1.0, None
    for t in roof:
        d = res.node_disp.get(t)
        if d is None:
            continue
        for k in range(3):
            if abs(d[k]) > best:
                best, pick = abs(d[k]), (k, tuple(asm.struct_coords[t]))
    return pick


def _value_at(res, asm, comp: int, point: Vec3) -> float:
    """Displacement component at the structural node at ``point`` (the
    nearest node when a non-nested mesh has no node exactly there)."""
    best_t, best_d = None, math.inf
    for t, c in asm.struct_coords.items():
        if t not in res.node_disp:
            continue
        dd = math.dist(c, point)
        if dd < best_d:
            best_t, best_d = t, dd
            if dd < _ROOF_TOL:
                break
    return 0.0 if best_t is None else float(res.node_disp[best_t][comp])


def _static_quantities(res, asm, quantities: Sequence[str],
                       roof: Optional[Tuple[int, Vec3]]
                       ) -> Tuple[Dict[str, float],
                                  Optional[Tuple[int, Vec3]]]:
    out: Dict[str, float] = {}
    if "roof_disp" in quantities:
        if roof is None:
            nodes = _roof_nodes(asm)
            roof = _roof_pick(res, asm, nodes) if nodes else None
        if roof is not None:
            out[f"roof_disp:{roof[0]}"] = _value_at(res, asm, *roof)
    if "base_reactions" in quantities:
        for k in ("FX", "FY", "FZ", "MX", "MY", "MZ"):
            out[f"base:{k}"] = float(res.base.get(k, 0.0))
    if "max_shell_moment" in quantities and res.shell_forces:
        out["max_shell_moment"] = max(
            max(abs(v[3]), abs(v[4])) for v in res.shell_forces.values())
    if "max_frame_moment" in quantities and res.member_stations:
        vals = [abs(x) for st in res.member_stations.values()
                for k in ("M2", "M3") for x in st.get(k, ())]
        if vals:
            out["max_frame_moment"] = max(vals)
    return out, roof


def _mesh_stats(eng) -> dict:
    asm = eng._asm
    mesh = getattr(eng, "_mesh", None) or (asm.mesh if asm else None)
    n_shell = len(asm.shell_quads) if asm else 0
    n_seg = (sum(len(s) for s in mesh.segments.values())
             if mesh is not None else 0)
    return {"n_nodes": len(asm.struct_coords) if asm else 0,
            "n_shell_elements": n_shell, "n_frame_segments": n_seg}


def count_elements(model: BuildingModel) -> int:
    """Shell elements + frame analysis segments of a model's mesh."""
    from .mesh import mesh_model
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mm = mesh_model(model)
    return len(mm.quads) + sum(len(s) for s in mm.segments.values())


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def mesh_study(model: BuildingModel, levels=None, cases=None,
               quantities=None, tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
               max_elements: int = DEFAULT_MAX_ELEMENTS) -> dict:
    """Run the mesh-sensitivity study; ``model`` is never mutated.

    Returns ``{"levels", "cases", "quantities", "tolerance_pct", "mesh":
    [{level, n_nodes, n_shell_elements, n_frame_segments, elapsed_s}],
    "rows": [...], "summary": {...}, "warnings": [...]}`` (CONTRACT).
    Raises ``ValueError`` on bad parameters.
    """
    from skyframe.engine.opensees_engine import OpenSeesEngine

    levels = normalize_levels(levels)
    cases = normalize_cases(model, cases)
    quantities = normalize_quantities(quantities)
    if (isinstance(tolerance_pct, bool)
            or not isinstance(tolerance_pct, (int, float))
            or not (0.0 < float(tolerance_pct) <= 100.0)):
        raise ValueError("'tolerance_pct' must be a number in (0, 100]")
    tolerance_pct = float(tolerance_pct)
    if not _int_like(max_elements) or int(max_elements) < 1:
        raise ValueError("'max_elements' must be a positive integer")
    statics = [c for c in cases if c != MODAL_CASE or c in model.cases]
    modal = MODAL_CASE in cases and MODAL_CASE not in model.cases
    q_static = [q for q in quantities if q != "periods"]
    if not ((statics and q_static) or (modal and "periods" in quantities)):
        raise ValueError("nothing to study: choose static cases with "
                         "displacement / reaction / moment quantities, "
                         "or MODAL with periods")

    finest = refine_model(model, levels[-1])
    n_fine = count_elements(finest)
    if n_fine > int(max_elements):
        raise ValueError(f"the finest level ({levels[-1]}) has {n_fine} "
                         f"elements > max_elements {int(max_elements)}; "
                         "use smaller levels")
    del finest

    notes: List[str] = []
    per_level: List[Dict[Tuple[str, str], float]] = []
    mesh_info: List[dict] = []
    roof_at: Dict[str, Optional[Tuple[int, Vec3]]] = {c: None
                                                      for c in statics}
    with warnings.catch_warnings(record=True) as wrec:
        warnings.simplefilter("always")
        for lv in levels:
            t0 = time.perf_counter()
            eng = OpenSeesEngine(refine_model(model, lv))
            vals: Dict[Tuple[str, str], float] = {}
            if q_static:
                for c in statics:
                    res = eng.run_static(c)
                    qv, roof_at[c] = _static_quantities(
                        res, eng._asm, q_static, roof_at[c])
                    for k, v in qv.items():
                        vals[(c, k)] = v
            if modal and "periods" in quantities:
                mres = eng.run_modal(min(model.num_modes, N_PERIODS))
                for i, T in enumerate(mres.periods[:N_PERIODS]):
                    vals[(MODAL_CASE, f"T{i + 1}")] = float(T)
            info = _mesh_stats(eng) if eng._asm is not None else {
                "n_nodes": 0, "n_shell_elements": 0, "n_frame_segments": 0}
            info.update(level=lv,
                        elapsed_s=round(time.perf_counter() - t0, 4))
            mesh_info.append(info)
            per_level.append(vals)
    for w in wrec:
        msg = str(w.message)
        if msg not in notes:
            notes.append(msg)

    rows = _build_rows(levels, cases, statics, modal, quantities, per_level,
                       tolerance_pct)
    for r in rows:                      # the tracked roof point (level 1)
        if r["quantity"] == "roof_disp" and roof_at.get(r["case"]):
            r["point"] = [round(float(v), 9) for v in roof_at[r["case"]][1]]
    if modal and "periods" in quantities and not any(
            r["case"] == MODAL_CASE for r in rows):
        notes.append("MODAL: no modes found at every level (no mass?)")
    n_conv = sum(1 for r in rows if r["converged"])
    return {"levels": levels, "cases": cases, "quantities": quantities,
            "tolerance_pct": tolerance_pct, "mesh": mesh_info, "rows": rows,
            "summary": {"n_rows": len(rows), "n_converged": n_conv,
                        "all_converged": bool(rows) and n_conv == len(rows),
                        "max_est_error_pct": max(
                            (r["est_error_pct"] for r in rows
                             if r["est_error_pct"] is not None),
                            default=None)},
            "warnings": notes[:50]}


_COMP = ("UX", "UY", "UZ")


def _build_rows(levels, cases, statics, modal, quantities, per_level,
                tolerance_pct) -> List[dict]:
    rows: List[dict] = []

    def series(case, key):
        if not all((case, key) in pl for pl in per_level):
            return None
        return [pl[(case, key)] for pl in per_level]

    def add(case, quantity, key, label, dim):
        s = series(case, key)
        if s is not None:
            rows.append(make_row(case, quantity, key, label, dim, levels, s,
                                 tolerance_pct))

    for c in cases:
        if c in statics:
            if "roof_disp" in quantities:
                for k in range(3):
                    add(c, "roof_disp", f"roof_disp:{k}",
                        f"Roof displacement {_COMP[k]}", "length")
            if "base_reactions" in quantities:
                first = per_level[0]
                fmax = max((abs(first.get((c, f"base:{k}"), 0.0))
                            for k in ("FX", "FY", "FZ")), default=0.0)
                mmax = max((abs(first.get((c, f"base:{k}"), 0.0))
                            for k in ("MX", "MY", "MZ")), default=0.0)
                for k in ("FX", "FY", "FZ", "MX", "MY", "MZ"):
                    big = fmax if k[0] == "F" else mmax
                    v = first.get((c, f"base:{k}"))
                    if v is None or big == 0.0 or abs(v) <= _SIG * big:
                        continue
                    add(c, "base_reactions", f"base:{k}",
                        f"Base reaction {k}",
                        "force" if k[0] == "F" else "moment")
            if "max_shell_moment" in quantities:
                add(c, "max_shell_moment", "max_shell_moment",
                    "Max shell moment |Mxx|,|Myy|", "moment_per_length")
            if "max_frame_moment" in quantities:
                add(c, "max_frame_moment", "max_frame_moment",
                    "Max frame moment |M2|,|M3|", "moment")
        elif c == MODAL_CASE and modal and "periods" in quantities:
            for i in range(N_PERIODS):
                add(c, "periods", f"T{i + 1}", f"Period T{i + 1}", "time")
    return rows
