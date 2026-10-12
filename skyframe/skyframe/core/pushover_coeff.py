"""Coefficient-method refinements: ASCE 41-17 Table 7-4 ``Cm``, the
two-parameter spectrum's long-period ``TL`` branch, and the FEMA 440
displacement-modification method (improved coefficient method, FEMA 440
Chapter 5).

CONTRACT "Eigen shift / cutoff / tolerance, ... pushover any DOF, ASCE
41 Cm / TL, FEMA 440 displacement modification" (pushover part).

ASCE 41-17 Table 7-4, effective mass factor ``Cm`` (used in the strength
ratio ``mu = Sa / (Vy / W) Cm``, Eq. 7-31)::

    stories   concrete MF  concrete SW  concrete pier-spandrel
              steel MF  steel CBF  steel EBF  other
    1-2       1.0 (every system)
    3+        0.9          0.8          0.8
              0.9       0.9        0.9        1.0

and ``Cm = 1.0`` when the effective period ``Te > 1.0 s``.  (Values from
the standard's table as remembered — see docs/REVIEW_FLAGS.md.)

``TL`` branch (ASCE 41-17 §2.4.1.7.1 / ASCE 7 Eq. 11.4-8): ``Sa = SD1 TL /
T^2`` for ``T > TL``.

FEMA 440 displacement modification (§5.2-5.4):

    delta_t = C0 C1 C2 Sa Te^2 / (4 pi^2) g
    C1 = 1 + (R - 1) / (a Te^2)            (Eq. 5-1; Te < 0.2 s: the value
                                            at 0.2 s; Te > 1.0 s: 1.0)
    C2 = 1 + ((R - 1) / Te)^2 / 800        (Eq. 5-2; Te > 0.7 s: 1.0)
    R  = Sa / (Vy / W) Cm                  a = 130 (site A/B), 90 (C),
                                                60 (D/E/F)
    R_max = d_d / d_y + |alpha_e|^-h / 4,  h = 1 + 0.15 ln Te     (Eq. 5-4)
    alpha_e = alpha_PD + lambda (alpha_2 - alpha_PD)              (Eq. 5-5)

``lambda`` = 0.8 (0.2 near field), ``alpha_2`` = the post-peak slope of the
capacity curve over ``Ke`` (strength degradation; none -> no limit),
``alpha_PD`` = user P-Delta slope ratio (default 0).  ``R > R_max`` flags
``dynamic_instability``.  The bilinear idealisation is the ASCE 41 /
FEMA 356 one (:func:`skyframe.design.performance.bilinearize`).
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Sequence

G = 9.81
CM_SYSTEMS = ("concrete_moment_frame", "concrete_shear_wall",
              "concrete_pier_spandrel", "steel_moment_frame", "steel_cbf",
              "steel_ebf", "other")
CM_TABLE = {"concrete_moment_frame": 0.9, "concrete_shear_wall": 0.8,
            "concrete_pier_spandrel": 0.8, "steel_moment_frame": 0.9,
            "steel_cbf": 0.9, "steel_ebf": 0.9, "other": 1.0}
OPTION_KEYS = ("cm_system", "Cm", "near_field", "alpha_pdelta")
SITE_A = {"A": 130.0, "B": 130.0, "C": 90.0, "D": 60.0, "E": 60.0,
          "F": 60.0}


def _num(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def cm_factor(system: Optional[str], num_stories: int, Te: float,
              Cm: Optional[float] = None) -> float:
    """Table 7-4 ``Cm`` (an explicit ``Cm`` wins; no system -> 1.0)."""
    if Cm is not None:
        return float(Cm)
    if not system:
        return 1.0
    if system not in CM_TABLE:
        raise ValueError(f"Cm: unknown structural system {system!r} "
                         f"(one of {list(CM_SYSTEMS)})")
    if int(num_stories) <= 2 or Te > 1.0:
        return 1.0
    return CM_TABLE[system]


def design_sa_tl(T: float, SDS: float, SD1: float,
                 TL: Optional[float]) -> float:
    """Two-parameter design spectrum with the long-period branch."""
    from skyframe.design.performance import design_sa
    if TL is not None and T > TL:
        return SD1 * TL / (T * T)
    return design_sa(T, SDS, SD1)


def validate_options(opts: dict, where: str = "performance") -> None:
    sysn = opts.get("cm_system")
    if sysn is not None and sysn not in CM_TABLE:
        raise ValueError(f"{where}.cm_system must be one of "
                         f"{list(CM_SYSTEMS)} (or null)")
    cm = opts.get("Cm")
    if cm is not None and not (_num(cm) and 0.0 < cm <= 1.0):
        raise ValueError(f"{where}.Cm must be in (0, 1] (or null)")
    if not isinstance(opts.get("near_field", False), bool):
        raise ValueError(f"{where}.near_field must be true/false")
    ap = opts.get("alpha_pdelta", 0.0)
    if not (_num(ap) and -1.0 < ap <= 0.0):
        raise ValueError(f"{where}.alpha_pdelta must be in (-1, 0]")


def c1_c2(R: float, Te: float, site_class: str):
    """FEMA 440 Eqs. 5-1 / 5-2 (= ASCE 41-17 Eqs. 7-29 / 7-30)."""
    a = SITE_A[str(site_class).upper()]
    if Te > 1.0:
        C1 = 1.0
    else:
        Tc = max(Te, 0.2)
        C1 = 1.0 + (R - 1.0) / (a * Tc * Tc)
    C2 = 1.0 if Te > 0.7 else 1.0 + ((R - 1.0) / Te) ** 2 / 800.0
    return C1, C2


def post_peak_alpha(disp: Sequence[float], shear: Sequence[float],
                    Ke: float) -> Optional[float]:
    """Post-peak (degrading) slope ratio of the capacity curve, or None
    when the curve does not lose strength after its peak."""
    d = [float(x) for x in disp]
    v = [float(x) for x in shear]
    ip = max(range(len(v)), key=lambda i: v[i])
    if ip >= len(v) - 1 or v[-1] >= v[ip] or d[-1] <= d[ip]:
        return None
    return ((v[-1] - v[ip]) / (d[-1] - d[ip])) / Ke


def fema440_dm(disp: Sequence[float], shear: Sequence[float], *, Ti: float,
               W: float, spec, site_class: str = "D", num_stories: int = 1,
               cm_system: Optional[str] = None, Cm: Optional[float] = None,
               near_field: bool = False, alpha_pdelta: float = 0.0
               ) -> Dict[str, object]:
    """FEMA 440 displacement-modification target displacement (module
    docstring); ``spec`` = a :class:`core.pushover_methods.Spectrum` (or
    any object with ``sa(T)``)."""
    from skyframe.design import performance as _pf
    if Ti <= 0.0 or W <= 0.0:
        raise ValueError("fema440_dm: Ti and W must be > 0")
    sc = str(site_class).upper()
    if sc not in SITE_A:
        raise ValueError(f"fema440_dm: unknown site class {site_class!r}")
    bl = _pf.bilinearize(disp, shear)
    Te = Ti * math.sqrt(bl["Ki"] / bl["Ke"])
    Sa = float(spec.sa(Te))
    cm = cm_factor(cm_system, num_stories, Te, Cm)
    R = Sa / (bl["Vy"] / W) * cm
    C1, C2 = c1_c2(R, Te, sc)
    C0 = _pf.c0_factor(num_stories)
    delta_t = C0 * C1 * C2 * Sa * (Te / (2.0 * math.pi)) ** 2 * G
    a2 = post_peak_alpha(disp, shear, bl["Ke"])
    lam = 0.2 if near_field else 0.8
    if a2 is None:
        alpha_e = None
        R_max = None
    else:
        alpha_e = alpha_pdelta + lam * (a2 - alpha_pdelta)
        h = 1.0 + 0.15 * math.log(Te)
        R_max = (bl["du"] / bl["dy"]
                 + (abs(alpha_e) ** (-h) / 4.0 if alpha_e != 0.0
                    else math.inf))
    out = dict(bl)
    out.update({"Te": Te, "Ti": Ti, "Sa": Sa, "R": R, "Cm": cm, "C0": C0,
                "C1": C1, "C2": C2, "W": W, "site_class": sc,
                "delta_t": delta_t, "alpha_2": a2, "alpha_e": alpha_e,
                "lambda": lam, "R_max": R_max,
                "dynamic_instability": bool(R_max is not None
                                            and R > R_max)})
    return out


__all__ = ["CM_SYSTEMS", "CM_TABLE", "cm_factor", "design_sa_tl",
           "validate_options", "c1_c2", "fema440_dm", "post_peak_alpha"]
