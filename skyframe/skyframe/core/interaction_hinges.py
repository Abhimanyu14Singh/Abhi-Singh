"""Parametric interacting hinges: P-M2-M3 (``PMM``) and M2-M3 (``M2M3``).

ETABS Define > Frame Hinge Properties > P-M2-M3 / M2-M3 "parametric"
(non-fiber) hinges with a user interaction surface given as curves at
several angles.  Pure Python here (validation + surface evaluation); the
OpenSees side is :mod:`skyframe.engine.interaction_hinges` (CONTRACT
"Parametric interacting hinges").

Property (JSON)::

    {"type": "PMM" | "M2M3",
     "surface": {"curves": [{"angle": deg,            # 0 = +M2, 90 = +M3
                             "points": [[P, M], ...]}, ...]},
     "backbone": [[0, 0], [0, 1.0], [d_C, r_C], ...],  # symmetric; forces
                                     # are RATIOS of the surface yield moment
     "scale": {"yield_value": 1.0, "yield_deformation": null|x|"auto"},
     "acceptance": {"IO", "LS", "CP"},                 # plastic, scaled
     "axial": {"source": "gravity" | "value", "value": P,
               "gravity": {pattern: factor} | null},   # PMM only
     "hysteresis": "kinematic", "drop_strength": "holds",
     "k_elastic": null | x}

Sign convention (ETABS): ``P`` is tension-positive / compression-NEGATIVE
in the surface and in ``axial.value``.  A PMM curve lists >= 2 points with
distinct P (sorted ascending on save); an M2M3 curve lists exactly one
point ``[0, M]``.  The surface must define the 0 (or 180) and 90 (or 270)
degree curves.

Wave C (CONTRACT "Wave C hinges"): ``axial.source = "analysis"`` starts
from the gravity P and UPDATES the capacity every committed step from the
hinge's own axial force (``engine.pmm_update``); ``drop_strength =
"drops"`` follows the backbone past C (C -> D drop, D -> E, residual).

Legacy engine idealization (documented exactly): the axial force is held at the
gravity value P (or ``axial.value``) — the ETABS "P from case/combo"
option — and the M2-M3 yield contour at that P is fitted by the ELLIPSE
through the 0/180 and 90/270 curve moments (``M2y``, ``M3y`` = the mean of
the two opposite sides); the other curves are reported against the
ellipse (``surface_fit``).  The hinge is coupled elliptical plasticity
(OpenSees ``Elliptical`` section: (M2/M2y)^2 + (M3/M3y)^2 = 1) with linear
kinematic hardening from the backbone B -> C slope; backbone points past C
are state markers only (no strength degradation: OpenSees has no
degrading coupled-plasticity section).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

INTERACTION_TYPES = ("PMM", "M2M3")
AXIAL_SOURCES = ("gravity", "value",
                 "analysis")             # wave C: P updated every step


def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{what} must be a number (got {v!r})")
    v = float(v)
    if not math.isfinite(v):
        raise ValueError(f"{what} must be finite")
    return v


def normalize_surface(typ: str, raw, nm: str) -> dict:
    if not isinstance(raw, dict) or set(raw) != {"curves"}:
        raise ValueError(f"{nm}: surface must be {{\"curves\": [...]}}")
    curves = raw["curves"]
    if not isinstance(curves, list) or len(curves) < 2:
        raise ValueError(f"{nm}: surface needs >= 2 curves (angles)")
    out = []
    seen = set()
    for k, c in enumerate(curves):
        if not isinstance(c, dict) or set(c) != {"angle", "points"}:
            raise ValueError(f"{nm}: surface.curves[{k}] must be "
                             '{"angle", "points"}')
        ang = _num(c["angle"], f"{nm} surface.curves[{k}].angle")
        if not 0.0 <= ang < 360.0:
            raise ValueError(f"{nm}: curve angles must be in [0, 360)")
        if ang in seen:
            raise ValueError(f"{nm}: duplicate curve angle {ang!r}")
        seen.add(ang)
        pts = c["points"]
        if not isinstance(pts, list) or not pts:
            raise ValueError(f"{nm}: curve {ang:g} needs points")
        pp = []
        for j, p in enumerate(pts):
            if not isinstance(p, (list, tuple)) or len(p) != 2:
                raise ValueError(f"{nm}: curve {ang:g} point {j} must be "
                                 "[P, M]")
            P = _num(p[0], f"{nm} curve {ang:g} P")
            M = _num(p[1], f"{nm} curve {ang:g} M")
            if M < 0.0:
                raise ValueError(f"{nm}: curve moments must be >= 0")
            pp.append([P, M])
        if typ == "M2M3":
            if len(pp) != 1 or pp[0][0] != 0.0 or pp[0][1] <= 0.0:
                raise ValueError(f"{nm}: an M2M3 curve has exactly one "
                                 "point [0, M] with M > 0")
        else:
            if len(pp) < 2:
                raise ValueError(f"{nm}: a PMM curve needs >= 2 [P, M] "
                                 "points")
            pp.sort(key=lambda q: q[0])
            if any(pp[i][0] == pp[i + 1][0] for i in range(len(pp) - 1)):
                raise ValueError(f"{nm}: curve {ang:g} has repeated P")
        out.append({"angle": ang, "points": pp})
    out.sort(key=lambda c: c["angle"])
    if not ({0.0, 180.0} & seen) or not ({90.0, 270.0} & seen):
        raise ValueError(f"{nm}: the surface must define the 0 (or 180) "
                         "and 90 (or 270) degree curves")
    return {"curves": out}


def normalize_axial(typ: str, raw, nm: str) -> Optional[dict]:
    if typ == "M2M3":
        if raw is not None:
            raise ValueError(f"{nm}: an M2M3 hinge has no axial setting")
        return None
    raw = {"source": "gravity"} if raw is None else raw
    if not isinstance(raw, dict) or set(raw) - {"source", "value",
                                                "gravity"}:
        raise ValueError(f"{nm}: axial must be {{source, value, gravity}}")
    src = raw.get("source", "gravity")
    if src not in AXIAL_SOURCES:
        raise ValueError(f"{nm}: axial.source must be one of "
                         f"{AXIAL_SOURCES}")
    val = raw.get("value")
    if src == "value":
        val = _num(val, f"{nm} axial.value")
    elif val is not None:
        raise ValueError(f"{nm}: axial.value needs source 'value'")
    grav = raw.get("gravity")
    if grav is not None:
        if not isinstance(grav, dict):
            raise ValueError(f"{nm}: axial.gravity must be "
                             "{pattern: factor}")
        grav = {str(k): _num(v, f"{nm} axial.gravity.{k}")
                for k, v in grav.items()}
    return {"source": src, "value": val, "gravity": grav}


def curve_moment(curve: dict, P: float) -> float:
    """Yield moment of one angle curve at axial force P (linear in P;
    0 outside the curve's P range — axial capacity exceeded)."""
    pts = curve["points"]
    if len(pts) == 1:
        return pts[0][1]
    if P < pts[0][0] or P > pts[-1][0]:
        return 0.0
    for (p0, m0), (p1, m1) in zip(pts, pts[1:]):
        if P <= p1:
            return m0 + (m1 - m0) * (P - p0) / (p1 - p0)
    return pts[-1][1]


def ellipse_at(surface: dict, P: float) -> dict:
    """Ellipse fit of the yield contour at P: {M2y, M3y, fit: [...]}."""
    by = {c["angle"]: curve_moment(c, P) for c in surface["curves"]}

    def mean(a, b):
        v = [by[x] for x in (a, b) if x in by]
        return sum(v) / len(v)
    M2y = mean(0.0, 180.0)
    M3y = mean(90.0, 270.0)
    fit = []
    for ang, M in by.items():
        t = math.radians(ang)
        if M2y > 0.0 and M3y > 0.0:
            Me = 1.0 / math.sqrt((math.cos(t) / M2y) ** 2
                                 + (math.sin(t) / M3y) ** 2)
        else:
            Me = 0.0
        fit.append({"angle": ang, "M_surface": M, "M_ellipse": Me})
    return {"M2y": M2y, "M3y": M3y, "P": P, "fit": fit}


def hardening(prop: dict, My: float, sf_d: float) -> Dict[str, float]:
    """Kinematic plastic modulus H (moment / plastic rotation) from the
    backbone B -> C slope (0 for an A-B only backbone) and the C marker."""
    bb = prop["backbone"]
    fB = bb[1][1]
    if len(bb) < 3:
        return {"H": 0.0, "dC": math.inf}
    dC = (bb[2][0] - bb[1][0]) * sf_d
    rC = bb[2][1] / fB
    H = (rC - 1.0) * My / dC if dC > 0.0 else 0.0
    return {"H": max(H, 0.0), "dC": dC}


def markers(prop: dict, sf_d: float) -> List[float]:
    """Plastic deformations of the backbone points B.. (state markers)."""
    bb = prop["backbone"]
    b0 = bb[1][0]
    return [max(0.0, d - b0) * sf_d for d, _ in bb[1:]]


def classify(dp: float, mk: List[float], acc: Optional[dict],
             sf_d: float, tol: float) -> str:
    """ETABS state of an interacting hinge at plastic deformation dp
    (resultant): A-B, acceptance bands up to C, then C-D / D-E / >E by the
    backbone markers (strength itself keeps hardening — see module doc)."""
    if dp <= tol:
        return "A-B"
    inf = math.inf
    mC = mk[1] if len(mk) > 1 else inf
    mD = mk[2] if len(mk) > 2 else inf
    mE = mk[3] if len(mk) > 3 else inf
    if dp <= mC + tol:
        a = acc or {}
        for key, st in (("IO", "B-IO"), ("LS", "IO-LS"), ("CP", "LS-CP")):
            lim = a.get(key)
            if lim is None or dp <= lim * sf_d + tol:
                return st
        return ">CP"
    if dp <= mD + tol:
        return "C-D"
    if dp <= mE + tol:
        return "D-E"
    return ">E"


# ------------------------------------------------------------- wave C
def capacity_ratio(prop: dict, dp: float) -> float:
    """Backbone force ratio (force / F_B) at the normalised plastic
    deformation ``dp`` (backbone units, B at 0).  ``drops``: the backbone
    itself (C -> D drop, D -> E, then a residual 1e-3 past E — a vertical
    drop is taken at once); ``holds``: the B -> C line continued (the
    legacy linear kinematic hardening)."""
    bb = prop["backbone"]
    b0, fB = bb[1][0], bb[1][1]
    pts = [(d - b0, f / fB) for d, f in bb[1:]]
    if len(pts) == 1:
        return 1.0
    if prop.get("drop_strength", "holds") != "drops":
        (d0, r0), (d1, r1) = pts[0], pts[1]
        return r0 + (r1 - r0) * dp / (d1 - d0) if d1 > d0 else r1
    for (d0, r0), (d1, r1) in zip(pts, pts[1:]):
        if dp < d1 or (dp == d1 and d1 > d0):
            if d1 <= d0:
                continue
            return r0 + (r1 - r0) * (dp - d0) / (d1 - d0)
    return RESIDUAL_RATIO if len(pts) > 2 else pts[-1][1]


RESIDUAL_RATIO = 1.0e-3
