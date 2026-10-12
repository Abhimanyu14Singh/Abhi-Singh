"""Per-material time-dependent properties (ETABS Material > Time Dependent
Properties): creep compliance, shrinkage strain, modulus / strength ageing
and prestressing-steel relaxation.

Stored on the model as ``BuildingModel.material_time_dependent`` =
``{material_name: props}`` (emitted by ``to_dict`` only when non-empty, so
legacy files are byte-identical).  ``props`` (normalised by
:func:`normalize_props`)::

    {"law": "cebfip2010" | "aci209" | "gl2000" | "user",
     "rh": 70.0,            # ambient relative humidity, percent (40..100)
     "h0": 0.15,            # notional size 2*Ac/u (m); V/S = h0/2
     "cure_days": 7.0,      # age at the end of curing = start of drying ts
     "cement": "N",         # MC2010: "SL" | "N" | "R";  ACI 209: "I" | "III";
                            # GL2000: "I" | "II" | "III"
     "curing": "moist",     # ACI 209: "moist" | "steam"
     "fcm": null,           # mean 28-day strength (kPa); default from the
                            # material fc: MC2010 fc + 8 MPa, GL2000
                            # 1.1 fc + 5 MPa (ACI 209 uses ratios only)
     "creep": true, "shrinkage": true, "aging": true,
     "phi_u": null,         # ACI 209: ultimate creep override (else 2.35*gamma_c)
     "eps_shu": null,       # ACI 209: ultimate shrinkage override (positive)
     # user curves (law "user"): [[t_days, value], ...] ascending, linear
     # interpolation, clamped outside the table
     "creep_curve": [[0, 0], [10000, 2.0]],     # phi vs (t - t0)
     "shrinkage_curve": [[0, 0], [10000, 3e-4]],  # eps vs (t - ts)
     "modulus_curve": [[1, 0.5], [28, 1.0]],      # E(t)/E28 vs age
     "strength_curve": [[1, 0.3], [28, 1.0]],     # fc(t)/fc28 vs age
     "relaxation": null | {"law": "ec2", "class": 2, "rho1000": 2.5,
                           "fpk": null}            # tendon materials
                    | {"law": "aci", "K": 45, "fpy": null}}

Every function takes CONCRETE AGES in days (``t`` = current age, ``t0`` =
age at loading, ``ts`` = age at the start of drying) and SI units (kPa);
code formulas that need MPa / mm convert internally.  Shrinkage is returned
as a POSITIVE contraction magnitude.

Compliance ``J(t, t0)`` (1/kPa, strain per unit stress):

* CEB-FIP 2010 / GL2000: ``J = 1/E(t0) + phi(t, t0)/E28`` (phi referred to
  the 28-day modulus — fib MC2010 eq. 5.1-63, GL2000);
* ACI 209R-92 and user curves: ``J = (1 + phi(t, t0))/E(t0)``.

``E28`` is the material ``E`` (taken as the 28-day tangent modulus Eci).
"""

from __future__ import annotations

import bisect
import math
from typing import Dict, List, Optional

LAWS = ("cebfip2010", "aci209", "gl2000", "user")
CEMENTS = {"cebfip2010": ("SL", "N", "R"), "aci209": ("I", "III"),
           "gl2000": ("I", "II", "III"), "user": ("N",)}
_DEF_CEMENT = {"cebfip2010": "N", "aci209": "I", "gl2000": "I", "user": "N"}
CURINGS = ("moist", "steam")
RELAX_LAWS = ("ec2", "aci")
_KEYS = ("law", "rh", "h0", "cure_days", "cement", "curing", "fcm", "creep",
         "shrinkage", "aging", "phi_u", "eps_shu", "creep_curve",
         "shrinkage_curve", "modulus_curve", "strength_curve", "relaxation")
_CURVES = ("creep_curve", "shrinkage_curve", "modulus_curve",
           "strength_curve")

# fib MC2010 cement-class constants (Table 5.1-9 / 5.1-12 / eq. 5.1-73)
_MC_S = {"SL": 0.6, "N": 0.5, "R": 0.2}
_MC_ALPHA = {"SL": -1.0, "N": 0.0, "R": 1.0}
_MC_AS = {"SL": 800.0, "N": 700.0, "R": 600.0}
_MC_DS1 = {"SL": 3.0, "N": 4.0, "R": 6.0}
_MC_DS2 = {"SL": 0.013, "N": 0.012, "R": 0.012}
# ACI 209R-92 strength-development constants (a, b) per cement / curing
_ACI_AB = {("I", "moist"): (4.0, 0.85), ("III", "moist"): (2.3, 0.92),
           ("I", "steam"): (1.0, 0.95), ("III", "steam"): (0.7, 0.98)}
# GL2000 strength-development s and shrinkage k per cement type
_GL_S = {"I": 0.335, "II": 0.4, "III": 0.13}
_GL_K = {"I": 1.0, "II": 0.75, "III": 1.15}


# --------------------------------------------------------------------------- #
# normalisation / validation
# --------------------------------------------------------------------------- #
def _num(v, what, lo=None, hi=None, lo_open=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) \
            or not math.isfinite(v):
        raise ValueError(f"{what} must be a finite number (got {v!r})")
    v = float(v)
    if lo is not None and (v < lo or (lo_open and v <= lo)):
        raise ValueError(f"{what} must be {'>' if lo_open else '>='} {lo}")
    if hi is not None and v > hi:
        raise ValueError(f"{what} must be <= {hi}")
    return v


def _curve(v, what, positive_x=False):
    if (not isinstance(v, list) or len(v) < 2
            or not all(isinstance(p, (list, tuple)) and len(p) == 2
                       for p in v)):
        raise ValueError(f"{what} must list >= 2 [t, value] pairs")
    pts = [[_num(p[0], f"{what} t", 0.0), _num(p[1], f"{what} value")]
           for p in v]
    for a, b in zip(pts, pts[1:]):
        if b[0] <= a[0]:
            raise ValueError(f"{what}: t must be strictly ascending")
    if positive_x and pts[0][0] <= 0.0:
        raise ValueError(f"{what}: ages must be > 0")
    return pts


def normalize_relaxation(r, what="relaxation") -> Optional[dict]:
    if r is None:
        return None
    if not isinstance(r, dict):
        raise ValueError(f"{what} must be an object or null")
    law = r.get("law", "ec2")
    if law not in RELAX_LAWS:
        raise ValueError(f"{what}.law must be one of {RELAX_LAWS}")
    if law == "ec2":
        bad = set(r) - {"law", "class", "rho1000", "fpk"}
        cls = r.get("class", 2)
        if cls not in (1, 2, 3) or isinstance(cls, bool):
            raise ValueError(f"{what}.class must be 1, 2 or 3")
        out = {"law": "ec2", "class": int(cls),
               "rho1000": _num(r.get("rho1000", {1: 8.0, 2: 2.5, 3: 4.0}
                                     [int(cls)]), f"{what}.rho1000", 0.0),
               "fpk": (None if r.get("fpk") is None else
                       _num(r["fpk"], f"{what}.fpk", 0.0, lo_open=True))}
    else:
        bad = set(r) - {"law", "K", "fpy"}
        out = {"law": "aci", "K": _num(r.get("K", 45.0), f"{what}.K", 0.0,
                                       lo_open=True),
               "fpy": (None if r.get("fpy") is None else
                       _num(r["fpy"], f"{what}.fpy", 0.0, lo_open=True))}
    if bad:
        raise ValueError(f"{what}: unknown keys {sorted(bad)}")
    return out


def normalize_props(d: dict, what: str = "time-dependent properties"
                    ) -> dict:
    """Canonical props dict (module docstring); raises ValueError."""
    if not isinstance(d, dict):
        raise ValueError(f"{what} must be an object")
    bad = set(d) - set(_KEYS)
    if bad:
        raise ValueError(f"{what}: unknown keys {sorted(bad)} "
                         f"(allowed: {list(_KEYS)})")
    law = d.get("law", "cebfip2010")
    if law not in LAWS:
        raise ValueError(f"{what}: law must be one of {LAWS}")
    cem = d.get("cement", _DEF_CEMENT[law])
    if law != "user" and cem not in CEMENTS[law]:
        raise ValueError(f"{what}: cement for {law} must be one of "
                         f"{CEMENTS[law]}")
    cur = d.get("curing", "moist")
    if cur not in CURINGS:
        raise ValueError(f"{what}: curing must be one of {CURINGS}")
    out = {"law": law,
           "rh": _num(d.get("rh", 70.0), f"{what}: rh", 40.0, 100.0),
           "h0": _num(d.get("h0", 0.15), f"{what}: h0", 0.0, lo_open=True),
           "cure_days": _num(d.get("cure_days", 7.0), f"{what}: cure_days",
                             0.0, lo_open=True),
           "cement": str(cem), "curing": cur,
           "fcm": (None if d.get("fcm") is None else
                   _num(d["fcm"], f"{what}: fcm", 0.0, lo_open=True)),
           "phi_u": (None if d.get("phi_u") is None else
                     _num(d["phi_u"], f"{what}: phi_u", 0.0)),
           "eps_shu": (None if d.get("eps_shu") is None else
                       _num(d["eps_shu"], f"{what}: eps_shu", 0.0))}
    for k in ("creep", "shrinkage", "aging"):
        v = d.get(k, True)
        if not isinstance(v, bool):
            raise ValueError(f"{what}: {k} must be a bool")
        out[k] = v
    for k in _CURVES:
        v = d.get(k)
        out[k] = (None if v is None else
                  _curve(v, f"{what}: {k}",
                         positive_x=k in ("modulus_curve", "strength_curve")))
    if law == "user" and out["creep"] and out["creep_curve"] is None:
        raise ValueError(f"{what}: law 'user' needs creep_curve (or "
                         "creep: false)")
    if law == "user" and out["shrinkage"] and out["shrinkage_curve"] is None:
        raise ValueError(f"{what}: law 'user' needs shrinkage_curve (or "
                         "shrinkage: false)")
    out["relaxation"] = normalize_relaxation(d.get("relaxation"),
                                             f"{what}: relaxation")
    return out


# --------------------------------------------------------------------------- #
# model hooks (to_dict / from_dict / validate)
# --------------------------------------------------------------------------- #
def model_to_dict(model) -> dict:
    td = getattr(model, "material_time_dependent", None) or {}
    if not td:
        return {}
    return {"material_time_dependent": {k: dict(v) for k, v in td.items()}}


def model_from_dict(model, d: dict) -> None:
    td = d.get("material_time_dependent")
    if td is None:
        return
    if not isinstance(td, dict):
        raise ValueError("material_time_dependent must be an object")
    model.material_time_dependent = {str(k): v for k, v in td.items()}


def validate_model(model) -> None:
    """Normalise ``model.material_time_dependent`` in place (no-op when
    empty); every key must name a model material."""
    td = getattr(model, "material_time_dependent", None)
    if not td:
        return
    if not isinstance(td, dict):
        raise ValueError("material_time_dependent must be an object")
    for name in list(td):
        if name not in model.materials:
            raise ValueError(f"material_time_dependent: unknown material "
                             f"{name!r}")
        td[name] = normalize_props(td[name], f"Material {name!r} "
                                   "time-dependent properties")


def props_of(model, material: str) -> Optional[dict]:
    td = getattr(model, "material_time_dependent", None) or {}
    p = td.get(material)
    return normalize_props(p) if p is not None else None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def interp(curve: List[List[float]], x: float) -> float:
    """Piecewise-linear table value, clamped outside the table."""
    xs = [p[0] for p in curve]
    if x <= xs[0]:
        return curve[0][1]
    if x >= xs[-1]:
        return curve[-1][1]
    i = bisect.bisect_right(xs, x) - 1
    (x0, y0), (x1, y1) = curve[i], curve[i + 1]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def fcm_mpa(p: dict, fc_kpa: Optional[float]) -> float:
    """Mean 28-day strength (MPa) for the code formulas."""
    if p.get("fcm") is not None:
        return p["fcm"] / 1000.0
    fc = (fc_kpa if fc_kpa else 30_000.0) / 1000.0
    if p["law"] == "gl2000":
        return 1.1 * fc + 5.0
    return fc + 8.0


def _h_mm(p):
    return p["h0"] * 1000.0


def _vs_mm(p):
    return p["h0"] * 500.0          # V/S = Ac/u = h0/2


# --------------------------------------------------------------------------- #
# CEB-FIP (fib) Model Code 2010, section 5.1.9.4
# --------------------------------------------------------------------------- #
def mc2010_t0_adj(p: dict, t0: float) -> float:
    """Cement-adjusted loading age (eq. 5.1-73, T = 20 degC), >= 0.5 d."""
    a = _MC_ALPHA[p["cement"]]
    return max(t0 * (9.0 / (2.0 + t0 ** 1.2) + 1.0) ** a, 0.5)


def mc2010_creep(p: dict, fcm: float, t: float, t0: float) -> float:
    """phi(t, t0) = phi_bc + phi_dc (eqs. 5.1-64 .. 5.1-71)."""
    if t <= t0:
        return 0.0
    dt = t - t0
    t0a = mc2010_t0_adj(p, t0)
    h = _h_mm(p)
    rh = p["rh"]
    b_bc_f = 1.8 / fcm ** 0.7
    b_bc_t = math.log((30.0 / t0a + 0.035) ** 2 * dt + 1.0)
    a_fcm = (35.0 / fcm) ** 0.5
    b_h = min(1.5 * h + 250.0 * a_fcm, 1500.0 * a_fcm)
    gam = 1.0 / (2.3 + 3.5 / math.sqrt(t0a))
    b_dc_f = 412.0 / fcm ** 1.4
    b_rh = (1.0 - rh / 100.0) / (0.1 * h) ** (1.0 / 3.0)
    b_dc_t0 = 1.0 / (0.1 + t0a ** 0.2)
    b_dc_tt = (dt / (b_h + dt)) ** gam
    return b_bc_f * b_bc_t + b_dc_f * b_rh * b_dc_t0 * b_dc_tt


def mc2010_beta_cc(p: dict, t: float) -> float:
    """Strength development beta_cc(t) = exp(s(1 - sqrt(28/t)))."""
    if t <= 0.0:
        return 0.0
    return math.exp(_MC_S[p["cement"]] * (1.0 - math.sqrt(28.0 / t)))


def mc2010_shrinkage(p: dict, fcm: float, t: float, ts: float) -> float:
    """|eps_cs| = |eps_cas(t)| + |eps_cds(t, ts)| (eqs. 5.1-75 .. 5.1-81)."""
    c = p["cement"]
    e_as0 = _MC_AS[c] * ((0.1 * fcm) / (6.0 + 0.1 * fcm)) ** 2.5 * 1e-6
    b_as = 1.0 - math.exp(-0.2 * math.sqrt(max(t, 0.0)))
    e_ds = 0.0
    if t > ts:
        e_ds0 = (220.0 + 110.0 * _MC_DS1[c]) * math.exp(
            -_MC_DS2[c] * fcm) * 1e-6
        b_s1 = min((35.0 / fcm) ** 0.1, 1.0)
        rh = p["rh"]
        if rh >= 99.0 * b_s1:
            b_rh = 0.25
        else:
            b_rh = -1.55 * (1.0 - (rh / 100.0) ** 3)
        h = _h_mm(p)
        b_ds = ((t - ts) / (0.035 * h * h + (t - ts))) ** 0.5
        e_ds = -e_ds0 * b_rh * b_ds       # b_rh < 0 -> contraction > 0
    return e_as0 * b_as + e_ds


# --------------------------------------------------------------------------- #
# ACI 209R-92
# --------------------------------------------------------------------------- #
def aci209_gamma_c(p: dict, t0: float) -> float:
    """Creep correction product gamma_la * gamma_lambda * gamma_vs."""
    if p["curing"] == "moist":
        g_la = 1.25 * t0 ** -0.118
    else:
        g_la = 1.13 * t0 ** -0.094
    h = p["rh"] / 100.0
    g_rh = 1.27 - 0.67 * h if h > 0.4 else 1.0
    g_vs = 2.0 / 3.0 * (1.0 + 1.13 * math.exp(-0.0213 * _vs_mm(p)))
    return g_la * g_rh * g_vs


def aci209_creep(p: dict, t: float, t0: float) -> float:
    """phi(t, t0) = (t-t0)^0.6/(10 + (t-t0)^0.6) * phi_u (eq. 2-8)."""
    if t <= t0:
        return 0.0
    phi_u = (p["phi_u"] if p["phi_u"] is not None
             else 2.35 * aci209_gamma_c(p, t0))
    x = (t - t0) ** 0.6
    return x / (10.0 + x) * phi_u


def aci209_shrinkage(p: dict, t: float, ts: float) -> float:
    """eps_sh(t, ts) = (t-ts)/(f + t-ts) * eps_shu (eq. 2-9/2-10)."""
    if t <= ts:
        return 0.0
    f = 35.0 if p["curing"] == "moist" else 55.0
    if p["eps_shu"] is not None:
        e_u = p["eps_shu"]
    else:
        h = p["rh"] / 100.0
        if h <= 0.4:
            g_rh = 1.0
        elif h <= 0.8:
            g_rh = 1.40 - 1.02 * h
        else:
            g_rh = 3.00 - 3.0 * h
        g_tc = (1.202 - 0.2337 * math.log10(ts)
                if p["curing"] == "moist" else 1.0)
        g_vs = 1.2 * math.exp(-0.00472 * _vs_mm(p))
        e_u = 780e-6 * g_rh * g_tc * g_vs
    return (t - ts) / (f + (t - ts)) * e_u


def aci209_strength_ratio(p: dict, t: float) -> float:
    """fc(t)/fc28 = t/(a + b t) (eq. 2-1)."""
    if t <= 0.0:
        return 0.0
    a, b = _ACI_AB[(p["cement"], p["curing"])]
    return t / (a + b * t)


# --------------------------------------------------------------------------- #
# GL2000 (Gardner & Lockman 2001)
# --------------------------------------------------------------------------- #
def gl2000_strength_ratio(p: dict, t: float) -> float:
    """fcm(t)/fcm28 = beta_e^2, beta_e = exp(s/2 (1 - sqrt(28/t)))."""
    if t <= 0.0:
        return 0.0
    return math.exp(_GL_S[p["cement"]] * (1.0 - math.sqrt(28.0 / t)))


def gl2000_creep(p: dict, t: float, t0: float) -> float:
    """phi_28(t, t0) with the drying-before-loading factor Phi(tc)."""
    if t <= t0:
        return 0.0
    dt = t - t0
    vs = _vs_mm(p)
    h = p["rh"] / 100.0
    tc = p["cure_days"]
    if t0 > tc:
        r = (t0 - tc) / (t0 - tc + 0.12 * vs * vs)
        Phi = (1.0 - r ** 0.5) ** 0.5
    else:
        Phi = 1.0
    return Phi * (2.0 * dt ** 0.3 / (dt ** 0.3 + 14.0)
                  + (7.0 / t0) ** 0.5 * (dt / (dt + 7.0)) ** 0.5
                  + 2.5 * (1.0 - 1.086 * h * h)
                  * (dt / (dt + 0.12 * vs * vs)) ** 0.5)


def gl2000_shrinkage(p: dict, fcm: float, t: float, ts: float) -> float:
    """eps_sh = eps_shu * beta(h) * beta(t - ts) (positive contraction)."""
    if t <= ts:
        return 0.0
    vs = _vs_mm(p)
    h = p["rh"] / 100.0
    e_u = 900.0 * _GL_K[p["cement"]] * (30.0 / fcm) ** 0.5 * 1e-6
    b_h = 1.0 - 1.18 * h ** 4
    b_t = ((t - ts) / (t - ts + 0.12 * vs * vs)) ** 0.5
    return e_u * b_h * b_t


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #
def strength_ratio(p: dict, t: float) -> float:
    """fc(t)/fc28 at concrete age ``t`` (days)."""
    if not p["aging"] or t == math.inf:
        return 1.0 if not p["aging"] else _limit_strength(p)
    law = p["law"]
    if law == "cebfip2010":
        return mc2010_beta_cc(p, t)
    if law == "aci209":
        return aci209_strength_ratio(p, t)
    if law == "gl2000":
        return gl2000_strength_ratio(p, t)
    c = p["strength_curve"]
    return interp(c, t) if c else 1.0


def _limit_strength(p):
    law = p["law"]
    if law == "cebfip2010":
        return math.exp(_MC_S[p["cement"]])
    if law == "aci209":
        return 1.0 / _ACI_AB[(p["cement"], p["curing"])][1]
    if law == "gl2000":
        return math.exp(_GL_S[p["cement"]])
    c = p["strength_curve"]
    return c[-1][1] if c else 1.0


def modulus_ratio(p: dict, t: float, fc_kpa: Optional[float] = None
                  ) -> float:
    """E(t)/E28 at concrete age ``t``.

    MC2010 ``beta_E = sqrt(beta_cc)`` (eq. 5.1-56); ACI 209
    ``sqrt(t/(a + b t))`` (E proportional to sqrt fc); GL2000 ``(3500 +
    4300 sqrt(fcm(t)))/(3500 + 4300 sqrt(fcm28))``; user: ``modulus_curve``
    (1 when absent).  ``aging: false`` -> 1.
    """
    if not p["aging"]:
        return 1.0
    law = p["law"]
    if t <= 0.0:
        return 0.0
    if law == "cebfip2010":
        return math.sqrt(strength_ratio(p, t))
    if law == "aci209":
        return math.sqrt(strength_ratio(p, t))
    if law == "gl2000":
        f28 = fcm_mpa(p, fc_kpa)
        ft = f28 * strength_ratio(p, t)
        return (3500.0 + 4300.0 * math.sqrt(ft)) / (
            3500.0 + 4300.0 * math.sqrt(f28))
    c = p["modulus_curve"]
    return interp(c, t) if c else 1.0


def creep_coefficient(p: dict, t: float, t0: float,
                      fc_kpa: Optional[float] = None) -> float:
    """phi(t, t0) (ages in days); 0 when creep is off or t <= t0."""
    if not p["creep"] or t <= t0:
        return 0.0
    law = p["law"]
    if law == "cebfip2010":
        return mc2010_creep(p, fcm_mpa(p, fc_kpa), t, t0)
    if law == "aci209":
        return aci209_creep(p, t, t0)
    if law == "gl2000":
        return gl2000_creep(p, t, t0)
    return interp(p["creep_curve"], t - t0)


def compliance(p: dict, E28: float, t: float, t0: float,
               fc_kpa: Optional[float] = None) -> float:
    """J(t, t0) in 1/kPa (module docstring for the per-law form)."""
    e0 = E28 * modulus_ratio(p, t0, fc_kpa)
    if e0 <= 0.0:
        raise ValueError("time-dependent: loading age gives a zero modulus")
    phi = creep_coefficient(p, t, t0, fc_kpa)
    if p["law"] in ("cebfip2010", "gl2000"):
        return 1.0 / e0 + phi / E28
    return (1.0 + phi) / e0


def shrinkage_strain(p: dict, t: float, fc_kpa: Optional[float] = None
                     ) -> float:
    """Free shrinkage (positive contraction) at concrete age ``t``; drying
    starts at ``cure_days``."""
    if not p["shrinkage"]:
        return 0.0
    ts = p["cure_days"]
    law = p["law"]
    if law == "cebfip2010":
        return mc2010_shrinkage(p, fcm_mpa(p, fc_kpa), t, ts)
    if law == "aci209":
        return aci209_shrinkage(p, t, ts)
    if law == "gl2000":
        return gl2000_shrinkage(p, fcm_mpa(p, fc_kpa), t, ts)
    return interp(p["shrinkage_curve"], t - ts) if t > ts else 0.0


# --------------------------------------------------------------------------- #
# prestressing-steel relaxation
# --------------------------------------------------------------------------- #
def relaxation_ratio(r: dict, sigma_pi: float, t_days: float,
                     fpk: Optional[float] = None,
                     fpy: Optional[float] = None) -> float:
    """Relaxation loss / initial stress ``sigma_pi`` after ``t_days``.

    * ``ec2`` (EN 1992-1-1 eqs. 3.28-3.30, t in hours, mu = sigma_pi/fpk):
      class 1 ``5.39 rho1000 e^(6.7 mu) (t/1000)^(0.75(1-mu)) 1e-5``,
      class 2 ``0.66 rho1000 e^(9.1 mu) ...``, class 3 ``1.98 rho1000
      e^(8 mu) ...``;
    * ``aci`` (Magura / ACI 423 / PCI): ``log10(24 t)/K (sigma_pi/fpy -
      0.55)`` (0 below 0.55 fpy), K = 45 low relaxation, 10 stress
      relieved.
    """
    if t_days <= 0.0 or sigma_pi <= 0.0:
        return 0.0
    if r["law"] == "ec2":
        fk = r.get("fpk") or fpk
        if not fk:
            raise ValueError("relaxation (ec2) needs fpk")
        mu = sigma_pi / fk
        th = 24.0 * t_days
        c, k = {1: (5.39, 6.7), 2: (0.66, 9.1), 3: (1.98, 8.0)}[r["class"]]
        return (c * r["rho1000"] * math.exp(k * mu)
                * (th / 1000.0) ** (0.75 * (1.0 - mu)) * 1e-5)
    fy = r.get("fpy") or fpy
    if not fy:
        raise ValueError("relaxation (aci) needs fpy")
    x = sigma_pi / fy - 0.55
    th = 24.0 * t_days
    if x <= 0.0 or th <= 1.0:
        return 0.0
    return math.log10(th) / r["K"] * x


# --------------------------------------------------------------------------- #
# curves for the UI / API
# --------------------------------------------------------------------------- #
def curves(p: dict, E28: float, fc_kpa: Optional[float] = None,
           t0: float = 28.0, times: Optional[List[float]] = None) -> dict:
    """Sampled curves (for the dialog preview): creep phi(t0 + dt, t0),
    compliance, shrinkage(age) and modulus / strength ratio(age)."""
    if times is None:
        times = [round(10 ** (k / 8.0), 6) for k in range(0, 33)]  # 1..10^4
    p = normalize_props(p)
    return {
        "t0": t0,
        "times": list(times),
        "creep": [creep_coefficient(p, t0 + dt, t0, fc_kpa)
                  for dt in times],
        "compliance": [compliance(p, E28, t0 + dt, t0, fc_kpa)
                       for dt in times],
        "shrinkage": [shrinkage_strain(p, t, fc_kpa) for t in times],
        "modulus_ratio": [modulus_ratio(p, t, fc_kpa) for t in times],
        "strength_ratio": [strength_ratio(p, t) for t in times],
    }


# --------------------------------------------------------------------------- #
# the legacy staged-case curves as a law (step method fallback)
# --------------------------------------------------------------------------- #
def staged_fallback_props(phi_inf: float, eps_inf: float, aging: bool
                          ) -> dict:
    """The v0.25 staged-case ACI 209 curves (phi_inf (dt/(10+dt))^0.6,
    eps_inf t/(35+t) from casting, sqrt(t/(4+0.85t)) ageing) expressed as
    a ``user``-like law evaluated analytically (``"_staged"`` marker)."""
    return {"_staged": True, "phi_inf": float(phi_inf),
            "eps_inf": float(eps_inf), "aging": bool(aging)}


def is_fallback(p: dict) -> bool:
    return bool(p.get("_staged"))


def fb_compliance(p: dict, E28: float, t: float, t0: float) -> float:
    e0 = E28 * (_aci_growth(t0) if p["aging"] else 1.0)
    dt = t - t0
    phi = (p["phi_inf"] * (dt / (10.0 + dt)) ** 0.6) if dt > 0 else 0.0
    return (1.0 + phi) / e0


def fb_shrinkage(p: dict, t: float) -> float:
    return p["eps_inf"] * t / (35.0 + t) if t > 0 else 0.0


def _aci_growth(t: float) -> float:
    return math.sqrt(t / (4.0 + 0.85 * t)) if t > 0 else 0.0


def law_compliance(p: dict, E28: float, t: float, t0: float,
                   fc_kpa: Optional[float] = None) -> float:
    if is_fallback(p):
        return fb_compliance(p, E28, t, t0)
    return compliance(p, E28, t, t0, fc_kpa)


def law_shrinkage(p: dict, t: float, fc_kpa: Optional[float] = None
                  ) -> float:
    if is_fallback(p):
        return fb_shrinkage(p, t)
    return shrinkage_strain(p, t, fc_kpa)


def law_modulus(p: dict, E28: float, t: float,
                fc_kpa: Optional[float] = None) -> float:
    if is_fallback(p):
        return E28 * (_aci_growth(t) if p["aging"] else 1.0)
    return E28 * modulus_ratio(p, t, fc_kpa)


def summary(p: dict) -> Dict[str, object]:
    return {k: p[k] for k in ("law", "rh", "h0", "cure_days", "cement")}


# --------------------------------------------------------------------------- #
# staged-case keys of the step-by-step method
# --------------------------------------------------------------------------- #
STAGED_METHODS = ("aaem", "step")
DEFAULT_STEPS_PER_STAGE = 4
MAX_STEPS_PER_STAGE = 50


def validate_staged_td(model, sc) -> None:
    """Validate the step-method keys of ``StagedCase.time_dependent``
    (``method``, ``steps_per_stage``, ``output_times``); no-op for the
    legacy keys / AAEM method."""
    td = sc.time_dependent
    if not isinstance(td, dict):
        return
    method = td.get("method", "aaem")
    if method not in STAGED_METHODS:
        raise ValueError(f"Staged case {sc.name}: time_dependent['method'] "
                         f"must be one of {STAGED_METHODS}")
    has_step_keys = "steps_per_stage" in td or "output_times" in td
    if method != "step":
        if has_step_keys:
            raise ValueError(f"Staged case {sc.name}: steps_per_stage / "
                             "output_times need method 'step'")
        return
    if not isinstance(sc.stages, list):
        raise ValueError(f"Staged case {sc.name}: method 'step' needs a "
                         "user-defined stage list (stages as a list)")
    n = td.get("steps_per_stage", DEFAULT_STEPS_PER_STAGE)
    if (isinstance(n, bool) or not isinstance(n, int)
            or not 1 <= n <= MAX_STEPS_PER_STAGE):
        raise ValueError(f"Staged case {sc.name}: steps_per_stage must be "
                         f"an integer in 1..{MAX_STEPS_PER_STAGE}")
    ot = td.get("output_times")
    if ot is not None:
        if not isinstance(ot, (list, tuple)):
            raise ValueError(f"Staged case {sc.name}: output_times must be "
                             "a list of days")
        for v in ot:
            _num(v, f"Staged case {sc.name}: output_times entry", 0.0)
