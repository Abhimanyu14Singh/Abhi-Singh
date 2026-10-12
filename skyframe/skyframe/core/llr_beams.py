"""Live-load reduction for BEAMS (ETABS Design > Live Load Reduction
Factors applied to beams) -- CONTRACT "Live-load reduction for beams".

Enabled by ``"beams"`` in ``llr_settings.apply_to`` (the default list stays
``["columns", "piers"]``, so legacy results tables are unchanged).

Tributary area ``At`` of a horizontal beam (``kind == "beam"``) at level z
(the half-spacing rule): ``At = L * (wL + wR)`` where, on each side of the
beam line in plan, ``w = d / 2`` with ``d`` the perpendicular distance to
the nearest PARALLEL beam (within 5 degrees) at the same level whose
projection overlaps the beam, or -- when no such beam exists on that side
-- the full distance to the floor's plan bounding box (frame and slab
points at that level; 0 for an edge beam).  ``llr_settings.beam_areas
{uid: m^2}`` overrides it.  Beams support one floor (n = 1).

Factor R by code (``llr_settings.beam_params`` holds the beam parameters):

* ``asce7_16``  ASCE 7-16 §4.7.2 with ``KLL`` = 2 (Table 4.7-1: interior
  beams and edge beams without cantilever slabs; ``beam_params.KLL``):
  ``R = 0.25 + 4.57 / sqrt(KLL At)`` within [0.5, 1] (one floor);
* ``nbcc2020``  NBCC 2020 4.1.5.8 on the beam's tributary area (as for
  columns);
* ``ec1``       EN 1991-1-1 Eq. 6.1 ``alpha_A = 5/7 psi0 + A0/A <= 1``
  (alpha_n applies to columns only; >= 0.6 for categories C and D);
* ``is875``     IS 875 (Part 2) §3.2.2: 5 % per 50 m^2 supported, at most
  25 % (``R = max(0.75, 1 - 0.05 floor(At / 50))``);
* ``user``      the user curve over At (``basis`` "area") or n = 1.
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

_PARALLEL_COS = math.cos(math.radians(5.0))
_TOL = 1e-6
BEAM_DEFAULTS = {"KLL": 2.0}


def normalize_params(p) -> dict:
    out = dict(BEAM_DEFAULTS)
    if p is None:
        return out
    if not isinstance(p, dict):
        raise ValueError("llr_settings.beam_params must be an object")
    for k, v in p.items():
        if k not in BEAM_DEFAULTS:
            raise ValueError(f"llr_settings.beam_params: unknown key {k!r}")
        if (isinstance(v, bool) or not isinstance(v, (int, float))
                or not math.isfinite(v) or v <= 0):
            raise ValueError(f"llr_settings.beam_params.{k} must be > 0")
        out[k] = float(v)
    return out


def _is_beam(m) -> bool:
    if m.kind != "beam":
        return False
    return abs(m.pj[2] - m.pi[2]) <= 1e-6


def _plan(m) -> Tuple[float, float, float, float, float]:
    """(x0, y0, ux, uy, L) of a horizontal member in plan."""
    dx, dy = m.pj[0] - m.pi[0], m.pj[1] - m.pi[1]
    L = math.hypot(dx, dy)
    return m.pi[0], m.pi[1], dx / L, dy / L, L


def tributary_areas(model) -> Dict[str, Dict[str, float]]:
    """uid -> {"At", "L", "w_left", "w_right"} for every horizontal beam."""
    beams = [m for m in model.members if _is_beam(m) and m.length > _TOL]
    by_z: Dict[float, list] = {}
    for m in beams:
        by_z.setdefault(round(float(m.pi[2]), 6), []).append(m)
    pts_z: Dict[float, List[Tuple[float, float]]] = {}
    for m in model.members:
        for p in (m.pi, m.pj):
            pts_z.setdefault(round(float(p[2]), 6), []).append(
                (float(p[0]), float(p[1])))
    for r in model.shells:
        if r.kind != "slab":
            continue
        for c in r.corners:
            pts_z.setdefault(round(float(c[2]), 6), []).append(
                (float(c[0]), float(c[1])))
    out: Dict[str, Dict[str, float]] = {}
    for z, lst in by_z.items():
        pts = pts_z.get(z, [])
        for b in lst:
            x0, y0, ux, uy, L = _plan(b)
            nx, ny = -uy, ux
            best = {1: None, -1: None}
            for o in lst:
                if o is b:
                    continue
                ox, oy, oux, ouy, oL = _plan(o)
                if abs(ux * oux + uy * ouy) < _PARALLEL_COS:
                    continue
                ta = (ox - x0) * ux + (oy - y0) * uy
                tb = ta + oL * (ux * oux + uy * ouy)
                lo, hi = min(ta, tb), max(ta, tb)
                if min(hi, L) - max(lo, 0.0) <= _TOL:
                    continue                      # no overlap along the beam
                mx, my = ox + 0.5 * oL * oux, oy + 0.5 * oL * ouy
                d = (mx - x0) * nx + (my - y0) * ny
                if abs(d) <= _TOL:
                    continue                      # collinear neighbour
                side = 1 if d > 0 else -1
                if best[side] is None or abs(d) < best[side]:
                    best[side] = abs(d)
            w = {}
            for side in (1, -1):
                if best[side] is not None:
                    w[side] = best[side] / 2.0
                else:
                    ext = max(((px - x0) * nx + (py - y0) * ny) * side
                              for px, py in pts) if pts else 0.0
                    w[side] = max(ext, 0.0) + 0.0      # no -0.0
            out[b.uid] = {"At": L * (w[1] + w[-1]), "L": L,
                          "w_left": w[1], "w_right": w[-1]}
    return out


def beam_factor(code: str, params: dict, beam_params: dict,
                At: float) -> float:
    """R for a beam supporting tributary area ``At`` (one floor)."""
    from .llr_codes import reduction_factor
    if code == "asce7_16":
        k = beam_params.get("KLL", 2.0) * At
        R = 0.25 + 4.57 / math.sqrt(k) if k > 0 else 1.0
        return min(1.0, max(0.5, R))
    if code == "ec1":
        p = dict(params)
        p["method"] = "alpha_A"
        return reduction_factor("ec1", p, At, 1)
    if code == "is875":
        return max(0.75, 1.0 - 0.05 * math.floor(At / 50.0 + 1e-12))
    return reduction_factor(code, params, At, 1)


def element_factors(model, st: dict) -> Dict[str, dict]:
    """uid -> {At, A1, n, R, L, w_left, w_right} for every beam."""
    areas = st.get("beam_areas") or {}
    bp = st.get("beam_params") or dict(BEAM_DEFAULTS)
    out: Dict[str, dict] = {}
    geo = tributary_areas(model)
    for uid, g in geo.items():
        At = float(areas.get(uid, g["At"]))
        out[uid] = {"At": At, "A1": At, "n": 1,
                    "R": float(beam_factor(st["code"], st["params"], bp,
                                           At)),
                    "L": g["L"], "w_left": g["w_left"],
                    "w_right": g["w_right"]}
    return out
