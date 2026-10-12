"""Caltrans SDC and TBI auto-hinge tables (Wave C) + the axial-updated
auto P-M2-M3 surface.

Registered into :mod:`skyframe.design.hinge_tables` through small hooks
(CODES / SUPPORT / TABLE_INFO / the rc generators).  EVERY value here is
transcribed from memory and is flagged ``preliminary`` (``verified``
False) — see docs/REVIEW_FLAGS.md "Caltrans / TBI hinge tables (Wave C)".

Caltrans SDC 2.0 ``rc_column`` (M3, M2, PMM) — ductile RC column plastic
hinge:

* expected materials ``f'ce = max(1.3 f'c, 34.5 MPa)``, ``f_ye = 475 MPa``
  (A706) unless ``fc_factor`` / ``fy_bar`` are given;
* ``M_p`` = the strain-compatibility nominal moment at the axial load
  (:func:`hinge_tables.rc_section_Mn`, expected materials);
* yield curvature ``phi_y = 2.1 eps_ye / h`` (rectangular, Priestley);
* ultimate curvature ``phi_u = eps_cu / c_u``, confined concrete
  ``eps_cu = 0.004 + 1.4 rho_s f_yh eps_su^R / f'cc`` (Mander) with
  ``rho_s = 2 rho_t``, ``eps_su^R = 0.09``, ``f'cc = 1.5 f'ce``; ``c_u``
  from axial equilibrium with a 0.85 f'cc Whitney block (beta1 0.85);
* plastic hinge length ``L_p = 0.08 L + 0.022 f_ye d_bl >= 0.044 f_ye
  d_bl`` (MPa, m — the SI form of SDC Eq. 5.3.4-1 with ``L`` the
  member length);
* ``theta_p = L_p (phi_u - phi_y)``; backbone flat to ``theta_p`` then
  a residual ``c = 0.2``; acceptance ``IO = 0.25, LS = 0.75, CP = 1.0``
  x ``theta_p``.

TBI (PEER Tall Buildings Initiative Guidelines 2017) — RC components
modelled with the ASCE 41-17 Tables 10-7 / 10-8 / 10-19 parameters at
TBI expected strengths (``f'ce = 1.3 f'c``, ``f_ye = 1.17 f_y``):

* ``rc_beam`` / ``rc_column`` — the ASCE 41-17 generators with those
  expected-strength factors (unless given);
* ``rc_coupling_beam`` (M3) — diagonally reinforced coupling beam,
  chord rotation ``a = 0.030``, ``b = 0.050``, ``c = 0.80``, acceptance
  ``IO = 0.006, LS = 0.018, CP = 0.030`` (ASCE 41-17 Table 10-19, as
  adopted by TBI).
"""

from __future__ import annotations

import copy
import math
from typing import Dict, List, Optional, Tuple

CODES = ("Caltrans", "TBI")
SUPPORT = {
    "Caltrans": {"rc_column": ("M3", "M2", "PMM")},
    "TBI": {"rc_beam": ("M3", "M2"), "rc_column": ("M3", "M2", "PMM"),
            "rc_coupling_beam": ("M3",)},
}
TABLE_INFO = {
    ("Caltrans", "rc_column"): {
        "table": "Caltrans SDC 2.0 ductile column plastic hinge",
        "clause": "Caltrans SDC 2.0 (2019) 5.3.4 plastic hinge length, "
                  "3.3 expected materials, Mander confined eps_cu",
        "verified": False,
        "note": "Mp at P (expected f'ce = max(1.3 f'c, 34.5 MPa), "
                "fye = 475 MPa); phi_y = 2.1 eps_y/h; phi_u = eps_cu/c_u, "
                "eps_cu = 0.004 + 1.4 rho_s fyh 0.09/f'cc (f'cc = 1.5 "
                "f'ce); Lp = 0.08L + 0.022 fye dbl >= 0.044 fye dbl; "
                "theta_p = Lp (phi_u - phi_y); c = 0.2; IO/LS/CP = "
                "0.25/0.75/1.0 theta_p.  Transcribed from memory"},
    ("TBI", "rc_beam"): {
        "table": "PEER TBI 2017 (ASCE 41-17 Table 10-7 parameters)",
        "clause": "PEER TBI Guidelines 2017 v2.03, expected strengths "
                  "f'ce = 1.3 f'c, fye = 1.17 fy; ASCE 41-17 Table 10-7",
        "verified": False,
        "note": "ASCE 41-17 rc_beam generator at TBI expected strengths. "
                "Transcribed from memory"},
    ("TBI", "rc_column"): {
        "table": "PEER TBI 2017 (ASCE 41-17 Table 10-8 parameters)",
        "clause": "PEER TBI Guidelines 2017 v2.03, expected strengths; "
                  "ASCE 41-17 Table 10-8",
        "verified": False,
        "note": "ASCE 41-17 rc_column generator at TBI expected strengths. "
                "Transcribed from memory"},
    ("TBI", "rc_coupling_beam"): {
        "table": "PEER TBI 2017 diagonally reinforced coupling beam "
                 "(ASCE 41-17 Table 10-19)",
        "clause": "PEER TBI Guidelines 2017 v2.03; ASCE 41-17 Table 10-19 "
                  "(diagonal reinforcement)",
        "verified": False,
        "note": "a = 0.030, b = 0.050, c = 0.80, IO = 0.006, LS = 0.018, "
                "CP = 0.030 chord rotation.  Transcribed from memory"},
}
TBI_FC_FACTOR = 1.3
TBI_FY_FACTOR = 1.17
CT_FY = 475_000.0                 # kPa, A706 expected yield
CT_FC_MIN = 34_500.0              # kPa
CT_EPS_SU_R = 0.09
CT_FCC_RATIO = 1.5
CT_C = 0.2
CT_ACC = {"IO": 0.25, "LS": 0.75, "CP": 1.0}
CB_ROW = {"a": 0.030, "b": 0.050, "c": 0.80, "IO": 0.006, "LS": 0.018,
          "CP": 0.030}


def _ht():
    from skyframe.design import hinge_tables as ht
    return ht


def _asce(auto: dict, fc_f: float, fy_f: float) -> dict:
    a = copy.deepcopy(auto)
    a["code"] = "ASCE41-17"
    p = a["params"]
    if p.get("fc_factor") is None:
        p["fc_factor"] = fc_f
    if p.get("fy_factor") is None:
        p["fy_factor"] = fy_f
    return a


def _relabel(spec, why, code, comp, extra=None):
    if spec is None:
        return spec, why
    ht = _ht()
    info = TABLE_INFO[(code, comp)]
    meta = dict(spec["meta"])
    meta.update({"code": code, "component": comp, "table": info["table"],
                 "clause": info["clause"], "preliminary": True})
    meta["inputs"] = dict(meta.get("inputs") or {}, **(extra or {}))
    spec["meta"] = meta
    del ht
    return spec, why


def caltrans_column(model, m, auto, axis, P_G=0.0):
    ht = _ht()
    ctx = ht._rc_ctx(model, m, auto, axis)
    if ctx is None:
        return None, "section has no rectangular drawing dims b/h"
    fc_given = auto["params"].get("fc_factor")
    fce = (ctx["fc"] if fc_given is not None
           else max(1.3 * ctx["fc"], CT_FC_MIN))
    fye = (auto["params"].get("fy_bar") or CT_FY) \
        * (auto["params"].get("fy_factor") or 1.0)
    b, h, d, L = ctx["b"], ctx["h"], ctx["d"], ctx["L"]
    rho = ht._par(auto, "rho")
    rp = auto["params"].get("rho_prime")
    rho_p = rho if rp is None else rp
    As, As2 = rho * b * d, rho_p * b * d
    Mp = ht.rc_section_Mn(b, h, fce, fye, As, As2, d, ctx["d2"], P_G)
    if Mp is None or Mp <= 0.0:
        return None, (f"P_G = {P_G:.1f} kN outside the section's axial "
                      "range: force-controlled; left elastic")
    eps_y = fye / ht.ES_REBAR
    phi_y = 2.1 * eps_y / h
    rho_s = 2.0 * min(ht._par(auto, "rho_t"), 0.05)
    fyh = ht._par(auto, "fyt") or fye
    fcc = CT_FCC_RATIO * fce
    eps_cu = 0.004 + 1.4 * rho_s * fyh * CT_EPS_SU_R / fcc
    c_u = neutral_axis(b, h, d, ctx["d2"], 0.85 * fcc, fye, As, As2, P_G)
    phi_u = eps_cu / c_u
    dbl = ht._par(auto, "db")
    fy_mpa = fye / 1000.0
    Lp = max(0.08 * L + 0.022 * fy_mpa * dbl, 0.044 * fy_mpa * dbl)
    th_p = max(Lp * (phi_u - phi_y), 0.0)
    if th_p <= 0.0:
        return None, "Caltrans: no plastic rotation capacity (phi_u <= phi_y)"
    thy = Mp * L / (6.0 * ctx["E"] * ctx["I"])
    acc = {k: v * th_p for k, v in CT_ACC.items()}
    spec = ht._spec(axis, Mp, th_p, th_p, CT_C, acc, thy, ctx["km"],
                    ht._meta("ASCE41-17", "rc_column"), F_C=Mp)
    return _relabel(spec, "", "Caltrans", "rc_column",
                    {"P_G": P_G, "fce": fce, "fye": fye, "phi_y": phi_y,
                     "phi_u": phi_u, "eps_cu": eps_cu, "c_u": c_u,
                     "Lp": Lp, "theta_p": th_p})


def neutral_axis(b, h, d, d2, fblock, fy, As, As2, P) -> float:
    """Neutral-axis depth at axial P (compression +) for a Whitney block
    of stress ``fblock`` (beta1 = 0.85) and EPP bars at eps_cu = 0.003
    strain compatibility (the c used for phi_u)."""
    ht = _ht()

    def N(c):
        a = min(0.85 * c, h)
        e2 = 0.003 * (c - d2) / c
        e1 = 0.003 * (c - d) / c
        f2 = max(-fy, min(fy, ht.ES_REBAR * e2))
        f1 = max(-fy, min(fy, ht.ES_REBAR * e1))
        return fblock * b * a + As2 * f2 + As * f1
    lo, hi = 1e-6 * h, 10.0 * h
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if N(mid) < P:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def tbi(model, m, auto, axis, P_G, comp):
    ht = _ht()
    if comp == "rc_coupling_beam":
        a = _asce(auto, TBI_FC_FACTOR, TBI_FY_FACTOR)
        spec, why = ht.rc_beam(model, m, a, axis, P_G)
        if spec is None:
            return spec, why
        r = CB_ROW
        Mn = spec["pos"]["F_B"]
        spec = ht._spec(axis, Mn, r["a"], r["b"], r["c"],
                        {k: r[k] for k in ("IO", "LS", "CP")}, spec["thy"],
                        spec["k_member"], spec["meta"])
        return _relabel(spec, "", "TBI", comp)
    gen = ht.rc_beam if comp == "rc_beam" else ht.rc_column
    spec, why = gen(model, m, _asce(auto, TBI_FC_FACTOR, TBI_FY_FACTOR),
                    axis, P_G)
    return _relabel(spec, why, "TBI", comp,
                    {"fc_factor_default": TBI_FC_FACTOR,
                     "fy_factor_default": TBI_FY_FACTOR})


def dispatch(model, m, auto, axis, P_G, comp):
    if auto["code"] == "Caltrans":
        return caltrans_column(model, m, auto, axis, P_G)
    return tbi(model, m, auto, axis, P_G, comp)


def register(ht) -> None:
    """Called once by hinge_tables at import (hook)."""
    ht.CODES = tuple(ht.CODES) + CODES
    for c, comps in SUPPORT.items():
        ht.SUPPORT[c] = dict(comps)
    ht.TABLE_INFO.update(TABLE_INFO)
    if "rc_coupling_beam" not in ht.COMPONENTS:
        ht.COMPONENTS = tuple(ht.COMPONENTS) + ("rc_coupling_beam",)


# ------------------------------------------------- axial-updated auto PMM
def auto_surface(model, m, auto: dict, n: int = 81
                 ) -> Optional[dict]:
    """Interaction surface (0 / 90 degree curves, ETABS sign: compression
    NEGATIVE) of an auto P-M2-M3 hinge: the table generator's yield moment
    sampled at ``n`` axial loads across the section's axial range."""
    ht = _ht()
    sec = model.sections[m.section]
    mat = model.materials[sec.material]
    from skyframe.design.wall import fc_from_E
    if auto["component"].startswith("steel"):
        f = (getattr(mat, "fy", None) or ht.FY_STEEL_DEFAULT) * 1.5
    else:
        f = (getattr(mat, "fc", None) or fc_from_E(mat.E)) * 1.5 \
            + 0.05 * ht._par(auto, "fy_bar")
    Pref = sec.A * sec.mod_A * f
    curves = []
    for ang, axis in ((0.0, "M2"), (90.0, "M3")):
        pts: List[List[float]] = []
        prev = None
        samples = [Pref * (-1.0 + 2.0 * k / (n - 1)) for k in range(n)]
        gen = ht.GENERATORS[auto["component"]]
        for PG in samples:
            spec, _ = gen(model, m, auto, axis, PG)
            My = spec["pos"]["F_B"] if spec is not None else 0.0
            pts.append([-PG, My])
            prev = My
        del prev
        pts.sort(key=lambda q: q[0])
        if sum(1 for q in pts if q[1] > 0.0) < 2:
            return None
        curves.append({"angle": ang, "points": pts})
    from skyframe.core import interaction_hinges as cih
    return cih.normalize_surface("PMM", {"curves": curves}, "auto PMM")


def auto_pmm_property(model, m, auto: dict, specs: List[dict], base: dict,
                      grav) -> Optional[dict]:
    """Interaction (Elliptical, capacity-updated) property of an auto
    PMM hinge with ``axial_update``: surface from :func:`auto_surface`,
    backbone RATIOS and absolute plastic acceptance from the M3 spec at
    the gravity P (modelling parameters a / b / c stay at P_G)."""
    ht = _ht()
    surf = auto_surface(model, m, auto)
    if surf is None:
        return None
    s3 = next(s for s in specs if s["type"] == "M3")
    pts = ht._side_points(s3["pos"])
    FB = pts[1][1]
    bb = [[d, F / FB] for d, F in pts]
    return {"type": "PMM", "surface": surf, "backbone": bb,
            "scale": {"yield_value": 1.0, "yield_deformation": None},
            "acceptance": dict(s3["acc"]),
            "axial": {"source": "analysis", "value": None,
                      "gravity": grav or None},
            "hysteresis": "kinematic", "drop_strength": "drops",
            "k_elastic": base.get("k_elastic"),
            "hysteresis_params": None}
