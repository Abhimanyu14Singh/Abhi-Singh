"""Wave-B link property types: Plastic (Wen), BRB, bilinear damper, T/C
friction isolator (CONTRACT "Link types: Plastic (Wen), BRB, bilinear
damper, T/C friction isolator").

Four extra ``LinkMember.link_type`` values (:data:`EXT_LINK_TYPES`); every
param is a float.  This module is pure Python (keys, validation, the exact
force laws used by the tests and the UI previews); the OpenSees builders
live in :mod:`skyframe.engine.links_ext`.  It does NOT import
:mod:`skyframe.core.model` (model.py imports it).

``plastic_wen`` {k, Fy, ratio=0, exp=2, kv?} — ETABS *Plastic (Wen)*:

    F = ratio*k*d + (1 - ratio)*Fy*z,
    dz/dd = (k/Fy) * (1 - |z|^exp)   when d_dot*z > 0,   (k/Fy) otherwise

(uncoupled per DOF).  Layout follows the FNA driver
(:mod:`skyframe.engine.fna`): a link whose axis has a HORIZONTAL component
is an axial (U1) Wen element (``twoNodeLink`` dir 1); a vertical or
zero-length link carries the Wen law on BOTH horizontal shear directions
(U2/U3) and an elastic vertical ``kv`` (isolator layout; ``kv`` is only
valid there).  OpenSees ``uniaxialMaterial BoucWen`` with
``alpha = ratio, ko = k, n = exp, gamma = beta = 0.5/uy^exp, A = 1`` and no
degradation (``uy = Fy/k``; the material's z carries displacement units,
z_disp = uy*z).

``brb`` {k, Fy, d_bm, beta=1.1, omega=1.3, R0=20} — buckling-restrained
brace (axial, length > 0): elastic stiffness ``k`` to the yield force
``Fy`` (``dy = Fy/k``), then asymmetric kinematic hardening calibrated so
that at the design deformation ``d_bm`` the tension force is
``omega*Fy`` (AISC 341 strain-hardening adjustment) and the compression
force ``beta*omega*Fy`` (compression-strength adjustment):

    b_t = (omega - 1)*Fy / (k*(d_bm - dy)),
    b_c = (beta*omega - 1)*Fy / (k*(d_bm - dy)).

OpenSees ``Steel4`` with ``-asym -kin b_t R0 0.925 0.15 b_c R0 0.925
0.15`` (Menegotto-Pinto transition of curvature ``R0``; the monotonic
backbone reaches ``omega*Fy`` / ``beta*omega*Fy`` at ``+-d_bm`` to
round-off, verified).

``damper_bilinear`` {cd, Fr, ratio=0, k=DAMPER_DEFAULT_K} — ETABS
*Damper - Bilinear* (axial, length > 0): Maxwell element (series spring
``k``) whose dashpot force is bilinear in the velocity:

    F = cd*v                                  |v| <= v_r = Fr/cd
    F = sign(v)*(Fr + ratio*cd*(|v| - v_r))   |v| >  v_r

OpenSees ``BilinearOilDamper(k, cd, Fr, ratio)``.  Like the viscous
damper it carries no static force and no eigen stiffness.

``tc_fp_isolator`` {R, mu, k_init=FP_DEFAULT_KINIT, kv=ISOLATOR_DEFAULT_KV,
kt=0, gap_t=0} — T/C friction isolator (vertical or zero-length):
compression = single friction pendulum (``singleFPBearing``: slip force
``mu*N``, restoring ``N/R``); TENSION = uplift: the bearing element
releases completely (zero axial AND shear force, verified) and the only
tension resistance is an optional hold-down spring ``kt`` (``0`` = free
uplift) that engages after the axial gap ``gap_t`` (``ElasticPPGap``,
tension side) — a parallel axial element at the same nodes.
"""

from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

# defaults shared with skyframe.core.model (kept in sync by value; model.py
# imports this module, so no import back)
DAMPER_DEFAULT_K = 1.0e6
ISOLATOR_DEFAULT_KV = 1.0e7
FP_DEFAULT_KINIT = 1.0e5

EXT_LINK_TYPES = ("plastic_wen", "brb", "damper_bilinear", "tc_fp_isolator")

LINK_PARAM_KEYS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "plastic_wen": (("k", "Fy"), ("ratio", "exp", "kv")),
    "brb": (("k", "Fy", "d_bm"), ("beta", "omega", "R0")),
    "damper_bilinear": (("cd", "Fr"), ("ratio", "k")),
    "tc_fp_isolator": (("R", "mu"), ("k_init", "kv", "kt", "gap_t")),
}
DEFAULTS: Dict[str, Dict[str, float]] = {
    "plastic_wen": {"ratio": 0.0, "exp": 2.0, "kv": ISOLATOR_DEFAULT_KV},
    "brb": {"beta": 1.1, "omega": 1.3, "R0": 20.0},
    "damper_bilinear": {"ratio": 0.0, "k": DAMPER_DEFAULT_K},
    "tc_fp_isolator": {"k_init": FP_DEFAULT_KINIT, "kv": ISOLATOR_DEFAULT_KV,
                       "kt": 0.0, "gap_t": 0.0},
}
STEEL4_R1 = 0.925           # Steel4 cR1 / cR2 (Menegotto-Pinto defaults)
STEEL4_R2 = 0.15
#: types that make STATIC cases nonlinear (Newton) — the damper does not
STATIC_NONLINEAR = ("plastic_wen", "brb", "tc_fp_isolator")
#: axial (length > 0) types
AXIAL_TYPES = ("brb", "damper_bilinear")


def _g(prm: dict, ltype: str, key: str) -> float:
    return float(prm.get(key, DEFAULTS[ltype].get(key, 0.0)))


def is_axial(ltype: str, pi: Sequence[float], pj: Sequence[float]) -> bool:
    """True when the link acts axially (else the isolator shear layout)."""
    if ltype in AXIAL_TYPES:
        return True
    if ltype == "plastic_wen":
        return abs(pj[0] - pi[0]) + abs(pj[1] - pi[1]) > 1e-6
    return False


def brb_hardening(prm: dict) -> Tuple[float, float]:
    """(b_t, b_c) post-yield stiffness ratios of a BRB."""
    k, fy, dbm = float(prm["k"]), float(prm["Fy"]), float(prm["d_bm"])
    om, be = _g(prm, "brb", "omega"), _g(prm, "brb", "beta")
    dy = fy / k
    return ((om - 1.0) * fy / (k * (dbm - dy)),
            (be * om - 1.0) * fy / (k * (dbm - dy)))


# ----------------------------------------------------------- validation
def validate_link(uid: str, ltype: str, prm: dict, length: float,
                  horizontal: float) -> None:
    """Value + geometry checks (keys already checked by the model)."""
    w = f"Link {uid} ({ltype})"

    def pos(key):
        if not _g(prm, ltype, key) > 0.0:
            raise ValueError(f"{w}: {key} must be > 0")
    if ltype == "plastic_wen":
        pos("k")
        pos("Fy")
        if not 0.0 <= _g(prm, ltype, "ratio") < 1.0:
            raise ValueError(f"{w}: ratio must be in [0, 1)")
        pos("exp")
        axial = horizontal > 1e-6 and length > 1e-9
        if axial and "kv" in prm:
            raise ValueError(f"{w}: kv applies only to a vertical or "
                             "zero-length link (shear layout)")
        pos("kv")
    elif ltype == "brb":
        pos("k")
        pos("Fy")
        dy = float(prm["Fy"]) / float(prm["k"])
        if not float(prm["d_bm"]) > dy:
            raise ValueError(f"{w}: d_bm must exceed the yield deformation "
                             f"Fy/k = {dy:.6g}")
        pos("beta")
        if _g(prm, ltype, "omega") < 1.0:
            raise ValueError(f"{w}: omega must be >= 1")
        if _g(prm, ltype, "beta") * _g(prm, ltype, "omega") < 1.0:
            raise ValueError(f"{w}: beta*omega must be >= 1 (compression "
                             "hardening >= 0)")
        pos("R0")
    elif ltype == "damper_bilinear":
        pos("cd")
        pos("Fr")
        pos("k")
        if not 0.0 <= _g(prm, ltype, "ratio") <= 1.0:
            raise ValueError(f"{w}: ratio must be in [0, 1]")
    elif ltype == "tc_fp_isolator":
        for key in ("R", "mu", "k_init", "kv"):
            pos(key)
        for key in ("kt", "gap_t"):
            if _g(prm, ltype, key) < 0.0:
                raise ValueError(f"{w}: {key} must be >= 0")
        if "gap_t" in prm and not _g(prm, ltype, "kt") > 0.0:
            raise ValueError(f"{w}: gap_t needs a hold-down stiffness "
                             "kt > 0")
    if ltype in AXIAL_TYPES and length < 1e-9:
        raise ValueError(f"{w}: the link axis is undefined at zero length "
                         "— the two points must be distinct")
    if ltype == "tc_fp_isolator" and length > 1e-9 and horizontal > 1e-6:
        raise ValueError(f"{w}: the link axis must be vertical (or zero "
                         "length)")


# ----------------------------------------------------------- force laws
def wen_history(prm: dict, path: Sequence[float], n: int = 400
                ) -> List[Tuple[float, float]]:
    """Exact (RK4, fine steps) Plastic (Wen) force along a deformation
    path ``[d1, d2, ...]`` starting at 0 -> ``[(d, F), ...]``."""
    k, fy = float(prm["k"]), float(prm["Fy"])
    r, ex = _g(prm, "plastic_wen", "ratio"), _g(prm, "plastic_wen", "exp")
    uy = fy / k

    def zdot(z, du):
        s = 1.0 if du * z > 0.0 else 0.0
        return (1.0 - s * abs(z) ** ex) * du / uy
    out = [(0.0, 0.0)]
    u = z = 0.0
    for tgt in path:
        h = (tgt - u) / n
        for _ in range(n):
            k1 = zdot(z, h)
            k2 = zdot(z + 0.5 * k1, h)
            k3 = zdot(z + 0.5 * k2, h)
            k4 = zdot(z + k3, h)
            z += (k1 + 2 * k2 + 2 * k3 + k4) / 6.0
            u += h
            out.append((u, r * k * u + (1.0 - r) * fy * z))
    return out


def loop_area(hist: Sequence[Tuple[float, float]]) -> float:
    """Trapezoid work ``sum F du`` along a (d, F) history."""
    return sum(0.5 * (f0 + f1) * (u1 - u0)
               for (u0, f0), (u1, f1) in zip(hist[:-1], hist[1:]))


def brb_backbone(prm: dict, d: float) -> float:
    """Bilinear-asymmetric monotonic BRB backbone force at deformation d
    (the Steel4 curve tends to it away from the yield knee)."""
    k, fy = float(prm["k"]), float(prm["Fy"])
    dy = fy / k
    bt, bc = brb_hardening(prm)
    if abs(d) <= dy:
        return k * d
    if d > 0.0:
        return fy + bt * k * (d - dy)
    return -(fy + bc * k * (-d - dy))


def bilinear_damper_force(prm: dict, v: float) -> float:
    """Steady dashpot force at velocity v (series spring relaxed)."""
    c, fr = float(prm["cd"]), float(prm["Fr"])
    p = _g(prm, "damper_bilinear", "ratio")
    vr = fr / c
    if abs(v) <= vr:
        return c * v
    return math.copysign(fr + p * c * (abs(v) - vr), v)


def tc_axial_force(prm: dict, d: float) -> float:
    """Axial force (tension +) of a T/C isolator at axial deformation d
    (elongation +): kv*d in compression, hold-down kt*(d - gap_t) past the
    tension gap, else 0 (uplift)."""
    if d <= 0.0:
        return _g(prm, "tc_fp_isolator", "kv") * d
    kt, gap = _g(prm, "tc_fp_isolator", "kt"), _g(prm, "tc_fp_isolator",
                                                    "gap_t")
    return kt * (d - gap) if d > gap else 0.0


__all__ = ["EXT_LINK_TYPES", "LINK_PARAM_KEYS", "DEFAULTS",
           "STATIC_NONLINEAR", "AXIAL_TYPES", "validate_link", "is_axial",
           "brb_hardening", "wen_history", "loop_area", "brb_backbone",
           "bilinear_damper_force", "tc_axial_force"]
