"""Wall fiber hinges (P-M3 per pier per story) — model side (Wave B).

ETABS Assign > Shell > Wall Hinge parity, as the lighter alternative to
nonlinear layered shells.  ``BuildingModel.wall_hinges`` maps a PIER LABEL
to the wall-hinge definition; every planar rectangular shell wall of that
pier gets, in HINGED builds (pushover / nonlinear static / nonlinear TH),
one fiber "wide column" per story it spans (CONTRACT "Wall fiber hinges";
engine side :mod:`skyframe.engine.wall_hinges`).  Linear analyses never
see it (the wall stays the elastic shell — byte-identical).

Definition (JSON, every key optional)::

    {"rho_v": 0.0025,          # distributed vertical steel / (t lw)
     "rho_b": 0.0,             # boundary steel / (t lb) at EACH end
     "boundary_ratio": 0.1,    # lb / lw
     "fy": 420000.0,           # kPa rebar fy (Es = 200 GPa, 1% hardening)
     "fc": null,               # kPa (null = material fc, else from E)
     "n_fibers": 20,           # fibers along the length
     "lp_ratio": 0.5,          # hinge length lp = lp_ratio * lw
                               #   (capped at half the story height)
     "confined": false,        # ASCE 41-17 Table 10-19 boundary column
     "stories": null}          # story names (null = every story spanned)

Pure-Python reference moment-curvature (:func:`moment_curvature`) — the
"hand" strain-compatibility integration the tests compare the OpenSees
fiber section against: plane sections, Concrete01 envelope (Hognestad
parabola to eps0 = 2 fc/Ec, linear to 0.2 fc at 0.006, no tension), bars
Steel01 envelope (bilinear, b = 0.01), axial force held by bisection on
the centroidal strain.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

WALL_KEYS = {"rho_v": 0.0025, "rho_b": 0.0, "boundary_ratio": 0.1,
             "fy": 420000.0, "fc": None, "n_fibers": 20, "lp_ratio": 0.5,
             "confined": False, "stories": None}
ES = 200.0e6
STEEL_B = 0.01
EPS_CU = 0.006
# ASCE 41-17 Table 10-19 (walls controlled by flexure): rows keyed by
# confined boundary; per (axial ratio <= 0.1 | >= 0.25) x (shear stress
# <= 4 | >= 6 sqrt(fc') psi): a, b, c, IO, LS, CP  (PRELIMINARY)
TABLE_10_19 = {
    True: {(0, 0): (0.015, 0.020, 0.75, 0.005, 0.015, 0.020),
           (0, 1): (0.010, 0.015, 0.40, 0.004, 0.010, 0.015),
           (1, 0): (0.009, 0.012, 0.60, 0.003, 0.009, 0.012),
           (1, 1): (0.005, 0.010, 0.30, 0.0015, 0.005, 0.010)},
    False: {(0, 0): (0.008, 0.015, 0.60, 0.002, 0.008, 0.015),
            (0, 1): (0.006, 0.010, 0.30, 0.002, 0.006, 0.010),
            (1, 0): (0.003, 0.005, 0.25, 0.001, 0.003, 0.005),
            (1, 1): (0.002, 0.004, 0.20, 0.001, 0.002, 0.004)},
}
TABLE_NOTE = ("ASCE 41-17 Table 10-19 (shear walls controlled by flexure, "
              "As = As'): bilinear interpolation in ((As-As')fy + P)/(tw lw "
              "f'c) between 0.1 and 0.25 and V/(tw lw sqrt(f'c)) between 4 "
              "and 6 psi units; values not cross-checked against the "
              "printed table (preliminary)")


def _num(v, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{what} must be a number (got {v!r})")
    v = float(v)
    if not math.isfinite(v):
        raise ValueError(f"{what} must be finite")
    return v


def normalize(label: str, d) -> dict:
    nm = f"wall hinge {label!r}"
    if not isinstance(d, dict) or set(d) - set(WALL_KEYS):
        raise ValueError(f"{nm}: keys must be in {sorted(WALL_KEYS)}")
    out = {}
    for k, dv in WALL_KEYS.items():
        v = d.get(k, dv)
        if k == "confined":
            if not isinstance(v, bool):
                raise ValueError(f"{nm}: confined must be a bool")
        elif k == "stories":
            if v is not None:
                if not isinstance(v, list) or not all(
                        isinstance(s, str) for s in v):
                    raise ValueError(f"{nm}: stories must be a list of "
                                     "story names or null")
                v = list(v)
        elif k == "n_fibers":
            if isinstance(v, bool) or not isinstance(v, int) or not \
                    4 <= v <= 200:
                raise ValueError(f"{nm}: n_fibers must be an int in "
                                 "[4, 200]")
        elif v is not None or k != "fc":
            v = _num(v, f"{nm} {k}")
            lo_ok = v >= 0.0 if k in ("rho_v", "rho_b") else v > 0.0
            if not lo_ok:
                raise ValueError(f"{nm}: {k} must be "
                                 f"{'>= 0' if k in ('rho_v', 'rho_b') else '> 0'}")
            if k in ("rho_v", "rho_b") and v >= 0.1:
                raise ValueError(f"{nm}: {k} must be < 0.1")
            if k == "boundary_ratio" and v > 0.5:
                raise ValueError(f"{nm}: boundary_ratio must be <= 0.5")
            if k == "lp_ratio" and v > 2.0:
                raise ValueError(f"{nm}: lp_ratio must be <= 2")
        out[k] = v
    return out


def validate_model(model) -> None:
    wh = getattr(model, "wall_hinges", None) or {}
    if not isinstance(wh, dict):
        raise ValueError("wall_hinges must be {pier: {...}}")
    stories = {s.name for s in model.stories}
    for label, d in wh.items():
        n = normalize(label, d)
        for s in n["stories"] or []:
            if s not in stories:
                raise ValueError(f"wall hinge {label!r}: unknown story "
                                 f"{s!r}")


def model_to_dict(model) -> dict:
    wh = getattr(model, "wall_hinges", None) or {}
    if not wh:
        return {}
    return {"wall_hinges": {k: normalize(k, v) for k, v in wh.items()}}


def model_from_dict(model, d: dict) -> None:
    wh = d.get("wall_hinges") or {}
    if not isinstance(wh, dict):
        raise ValueError("wall_hinges must be an object")
    model.wall_hinges = {str(k): normalize(str(k), v)
                         for k, v in wh.items()}


# ------------------------------------------------------------- materials
def concrete01(eps: float, fc: float, Ec: float) -> float:
    """Concrete01 monotonic envelope (compression NEGATIVE), kPa."""
    if eps >= 0.0:
        return 0.0
    e0 = 2.0 * fc / Ec
    if eps >= -e0:
        r = eps / -e0
        return -fc * (2.0 * r - r * r)
    if eps >= -EPS_CU:
        return -fc + (eps + e0) / (-EPS_CU + e0) * (-0.2 * fc + fc)
    return -0.2 * fc


def steel01(eps: float, fy: float, E: float = ES,
            b: float = STEEL_B) -> float:
    ey = fy / E
    if abs(eps) <= ey:
        return E * eps
    return math.copysign(fy + b * E * (abs(eps) - ey), eps)


def section_layout(lw: float, t: float, wh: dict) -> List[tuple]:
    """Bar fibers [(y, area)] of the wall section (y along the length)."""
    n = wh["n_fibers"]
    bars = []
    As_v = wh["rho_v"] * t * lw
    if As_v > 0.0:
        dy = lw / n
        for k in range(n):
            bars.append((-lw / 2.0 + (k + 0.5) * dy, As_v / n))
    lb = wh["boundary_ratio"] * lw
    Ab = wh["rho_b"] * t * lb
    if Ab > 0.0:
        for s in (-1.0, 1.0):
            bars.append((s * (lw / 2.0 - lb / 2.0), Ab))
    return bars


def moment_curvature(lw: float, t: float, fc: float, Ec: float,
                     fy: float, bars: List[tuple], P: float,
                     kappas: List[float], strips: int = 4000
                     ) -> List[float]:
    """Hand moment-curvature: M (kN*m, about the centroid; positive kappa
    compresses +y... sign: M = -sum(sigma y A)) at each curvature for the
    axial force P (kN, compression POSITIVE).  Concrete strips at
    midpoints (``strips`` along the length); strain eps(y) = eps_a - k y."""
    dy = lw / strips
    ys = [-lw / 2.0 + (i + 0.5) * dy for i in range(strips)]

    def resultants(ea, k):
        N = 0.0
        M = 0.0
        for y in ys:
            s = concrete01(ea - k * y, fc, Ec)
            N += s * t * dy
            M += -s * t * dy * y
        for y, A in bars:
            s = steel01(ea - k * y, fy)
            N += s * A
            M += -s * A * y
        return N, M

    out = []
    for k in kappas:
        lo, hi = -0.05, 0.05             # centroidal strain bracket
        for _ in range(100):
            mid = 0.5 * (lo + hi)
            if resultants(mid, k)[0] > -P:
                hi = mid
            else:
                lo = mid
        out.append(resultants(0.5 * (lo + hi), k)[1])
    return out


def table_10_19(axial_ratio: float, shear_psi: float,
                confined: bool) -> Dict[str, float]:
    """Bilinear interpolation of the Table 10-19 rows."""
    rows = TABLE_10_19[bool(confined)]
    ta = min(max((axial_ratio - 0.1) / 0.15, 0.0), 1.0)
    ts = min(max((shear_psi - 4.0) / 2.0, 0.0), 1.0)
    keys = ("a", "b", "c", "IO", "LS", "CP")
    out = {}
    for i, k in enumerate(keys):
        v00, v01 = rows[(0, 0)][i], rows[(0, 1)][i]
        v10, v11 = rows[(1, 0)][i], rows[(1, 1)][i]
        out[k] = ((1 - ta) * ((1 - ts) * v00 + ts * v01)
                  + ta * ((1 - ts) * v10 + ts * v11))
    return out


def yield_curvature(fy: float, lw: float) -> float:
    """Priestley et al. (2007) rectangular wall: phi_y = 2.0 eps_y / lw."""
    return 2.0 * (fy / ES) / lw


def wall_regions(model, label: str) -> list:
    auto = getattr(model, "auto_pier_walls", False)
    return [r for r in model.shells
            if r.kind == "wall" and r.behavior == "shell"
            and ((getattr(r, "pier", "") or (r.uid if auto else ""))
                 == label)]


def geometry(region) -> Optional[dict]:
    """Planar vertical rectangle -> {u (unit along length), n (normal),
    lw, z0, z1, x0 (bottom-left corner)}; None when not a rectangle."""
    c = [tuple(map(float, p)) for p in region.corners]
    if len(c) != 4 or getattr(region, "openings", None):
        return None
    zs = sorted({round(p[2], 9) for p in c})
    if len(zs) != 2:
        return None
    z0, z1 = zs
    bot = [p for p in c if abs(p[2] - z0) < 1e-6]
    top = [p for p in c if abs(p[2] - z1) < 1e-6]
    if len(bot) != 2 or len(top) != 2:
        return None
    (ax, ay, _), (bx, by, _) = bot
    lw = math.hypot(bx - ax, by - ay)
    if lw <= 1e-9:
        return None
    u = ((bx - ax) / lw, (by - ay) / lw)
    for p in top:                                  # top above the bottom
        s = (p[0] - ax) * u[0] + (p[1] - ay) * u[1]
        off = abs(-(p[0] - ax) * u[1] + (p[1] - ay) * u[0])
        if off > 1e-6 or s < -1e-6 or s > lw + 1e-6:
            return None
    return {"u": u, "n": (-u[1], u[0]), "lw": lw, "z0": z0, "z1": z1,
            "x0": (ax, ay)}
