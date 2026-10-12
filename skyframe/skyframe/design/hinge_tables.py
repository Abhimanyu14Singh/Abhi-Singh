"""Table-driven automatic hinge generator (ETABS Auto hinges, Wave B).

One generator per (code, component); every generator returns a HINGE SPEC
(or ``None`` + a reason) that :func:`spec_to_property` converts into a
canonical B10 user-hinge property (``skyframe.core.user_hinges``), so auto
hinges reuse the exact zeroLength / material / recording machinery of the
user-defined hinges (CONTRACT "Auto hinges from code tables").

Codes and components (``SUPPORT``)::

    ASCE41-17 / ASCE41-23  steel_beam   (M3, M2)        Table 9-7.1 beams
                           steel_column (M3, M2, PMM)   Table 9-7.1 columns
                           steel_brace  (P)             Table 9-7.1 braces
                           rc_beam      (M3, M2)        Table 10-7
                           rc_column    (M3, M2, PMM)   Table 10-8
                           rc_column_shear (V2, V3)     Eq. 10-3
    FEMA356                steel_beam, steel_column     Table 5-6
                           rc_beam                      Table 6-7
    EC8-3                  rc_beam, rc_column           Annex A (A.1, A.10b,
                                                        A.12) chord rotation

Every table carries ``TABLE_INFO[(code, component)]`` = {table, clause,
verified, note}: ``verified`` False keeps the per-hinge ``preliminary``
flag (transcription not cross-checked against a published source in this
environment); True drops it.  ``PMM`` = an M2 AND an M3 hinge at the same
station (in series), each sized at the gravity axial load ``P_G`` — the
ETABS "axial force from a case/combo" option.  All values SI: kN, m, kPa,
rad.  A spec::

    {"type": "M3"|"M2"|"P"|"V2"|"V3",
     "pos": {"F_B", "F_C", "a", "b", "c"},      # absolute plastic defs
     "neg": {...} | None,                         # two-sided (braces)
     "acc": {"IO", "LS", "CP"}, "acc_neg": {...} | None,
     "thy": yield deformation (info), "k_member": member stiffness,
     "meta": {code, table, clause, preliminary, inputs...}}
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

CODES = ("ASCE41-17", "ASCE41-23", "FEMA356", "EC8-3")
COMPONENTS = ("steel_beam", "steel_column", "steel_brace", "rc_beam",
              "rc_column", "rc_column_shear")
SUPPORT: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "ASCE41-17": {"steel_beam": ("M3", "M2"),
                  "steel_column": ("M3", "M2", "PMM"),
                  "steel_brace": ("P",),
                  "rc_beam": ("M3", "M2"),
                  "rc_column": ("M3", "M2", "PMM"),
                  "rc_column_shear": ("V2", "V3")},
    "FEMA356": {"steel_beam": ("M3", "M2"),
                "steel_column": ("M3", "M2", "PMM"),
                "rc_beam": ("M3", "M2")},
    "EC8-3": {"rc_beam": ("M3", "M2"),
              "rc_column": ("M3", "M2", "PMM")},
}
SUPPORT["ASCE41-23"] = dict(SUPPORT["ASCE41-17"])

_A17 = "ASCE/SEI 41-17"
TABLE_INFO: Dict[Tuple[str, str], dict] = {
    ("ASCE41-17", "steel_beam"): {
        "table": "Table 9-7.1 (beams - flexure)",
        "clause": f"{_A17} 9.4.2.2, Eq. 9-1, Table 9-7.1 rows a/b/c",
        "verified": True,
        "note": "compact a=9thy b=11thy c=0.6 IO=1 LS=9 CP=11thy; slender "
                "a=4 b=6 c=0.2 IO=0.25 LS=3 CP=4thy; linear interpolation"},
    ("ASCE41-17", "steel_column"): {
        "table": "Table 9-7.1 (columns - flexure)",
        "clause": f"{_A17} 9.4.2.3/9.4.2.4, Table 9-7.1 column equations",
        "verified": False,
        "note": "a = 0.8(1-PG/Pye)^2.2 (0.1L/ry+0.8h/tw)^-1 - 0.0035, "
                "b = 7.4(1-PG/Pye)^2.3 (0.5L/ry+2.9h/tw)^-1 - 0.006, "
                "c = 0.9-0.9PG/Pye; IO=0.5a LS=0.75b CP=b; PG/Pye>0.6 or "
                "a=0 -> force-controlled (elastic).  Acceptance fractions "
                "not cross-checked against the printed table"},
    ("ASCE41-17", "steel_brace"): {
        "table": "Table 9-7.1 (braces in compression / tension)",
        "clause": f"{_A17} 9.5.2.4, Table 9-7.1; P_CE per AISC 360-16 E3 "
                  "with Fye",
        "verified": False,
        "note": "Delta_c / Delta_T multiples per shape class; slender "
                "KL/r >= 4.2 sqrt(E/Fy), stocky <= 2.1 sqrt(E/Fy), linear "
                "between.  Compression strength (AISC E3) is exact; the "
                "multiples are not cross-checked against the printed table"},
    ("ASCE41-17", "rc_beam"): {
        "table": "Table 10-7 (beams controlled by flexure, conforming)",
        "clause": f"{_A17} 10.4.2.2, Table 10-7 condition i",
        "verified": True,
        "note": "(rho-rho')/rho_bal <= 0: a=.025 b=.05 c=.2 IO=.010 "
                "LS=.025 CP=.05; >= 0.5: .020 .04 .2 .005 .020 .04"},
    ("ASCE41-17", "rc_column"): {
        "table": "Table 10-8 (columns other than circular with spiral)",
        "clause": f"{_A17} 10.4.2.2, Table 10-8 equations; EI_eff "
                  "Table 10-5",
        "verified": True,
        "note": "a = 0.042-0.043 n+0.63 rho_t-0.023 VyE/VColOE >= 0; "
                "b = 0.5/(5+n/0.8 /rho_t fc/fyt)-0.01 >= a; c = 0.24-0.4n "
                ">= 0; IO=0.15a<=0.005 LS=0.5b CP=0.7b (n = N_UD/(Ag "
                "f'cE), rho_t <= 0.0175).  a cross-checked: Kim et al. "
                "2019, Appl. Sci. 9:5193, Eq. (1)"},
    ("ASCE41-17", "rc_column_shear"): {
        "table": "Eq. 10-3 (column shear strength), force-controlled",
        "clause": f"{_A17} 10.4.2.3, Eq. 10-3 (k_nl = 1, lambda = 1)",
        "verified": True,
        "note": "V_ColOE = alpha Av fyt d/s + (0.5 sqrt(fc)/(M/Vd)) "
                "sqrt(1+N/(0.5 sqrt(fc) Ag)) 0.8 Ag [MPa]; M/Vd in [2, 4], "
                "d = 0.8h; force-controlled: IO = LS = CP = 0"},
    ("FEMA356", "steel_beam"): {
        "table": "FEMA 356 Table 5-6 (beams - flexure, primary)",
        "clause": "FEMA 356 5.5.2.2, Eqs. 5-1/5-3, Table 5-6",
        "verified": False,
        "note": "compact (bf/2tf<=52/sqrt(Fye), h/tw<=418/sqrt(Fye) ksi): "
                "a=9 b=11 c=0.6 IO=1 LS=6 CP=8thy; slender (65, 640): "
                "a=4 b=6 c=0.2 IO=0.25 LS=2 CP=3thy"},
    ("FEMA356", "steel_column"): {
        "table": "FEMA 356 Table 5-6 (columns - flexure, primary)",
        "clause": "FEMA 356 5.5.2.2, Eqs. 5-2/5-4, Table 5-6",
        "verified": False,
        "note": "P/PCL<0.2: beam rows with Eq. 5-2 thy and Eq. 5-4 "
                "M_CE = 1.18 Z Fye (1-P/Pye) <= Z Fye; 0.2..0.5: "
                "a=11(1-1.7P/PCL)thy b=17(1-1.7P/PCL)thy c=0.2 IO=0.25thy "
                "LS=8(1-1.7P/PCL)thy CP=11(1-1.7P/PCL)thy (compact); "
                ">0.5 force-controlled"},
    ("FEMA356", "rc_beam"): {
        "table": "FEMA 356 Table 6-7 (beams controlled by flexure)",
        "clause": "FEMA 356 6.5.2.2, Table 6-7 condition i (primary)",
        "verified": False,
        "note": "<=0: a=.025 b=.05 c=.2 IO=.010 LS=.02 CP=.025; >=0.5: "
                ".02 .03 .2 .005 .01 .02"},
    ("EC8-3", "rc_beam"): {
        "table": "EN 1998-3:2005 Annex A (chord rotation)",
        "clause": "EN 1998-3 A.3.2.2 Eq. (A.1), A.3.2.4 Eqs. (A.10b), "
                  "(A.11)-(A.12)",
        "verified": True,
        "note": "theta_um (A.1) with gamma_el; theta_y (A.10b); "
                "limit states DL = theta_y, SD = 3/4 theta_um, NC = "
                "theta_um mapped to IO/LS/CP (plastic parts)"},
}
TABLE_INFO[("EC8-3", "rc_column")] = dict(TABLE_INFO[("EC8-3", "rc_beam")])
for _comp in SUPPORT["ASCE41-17"]:
    _base = dict(TABLE_INFO[("ASCE41-17", _comp)])
    _base["clause"] = _base["clause"].replace(_A17, "ASCE/SEI 41-23 (as "
                                              "41-17)")
    _base["verified"] = False
    _base["note"] = ("ASCE 41-23 edition: the 41-17 values are used "
                     "unchanged (41-23 numeric revisions not transcribed); "
                     + _base["note"])
    TABLE_INFO[("ASCE41-23", _comp)] = _base

# shared constants -----------------------------------------------------------
HARDENING = 0.03          # post-yield slope / member stiffness (ASCE 41 9.4.2.2)
RY_DEFAULT = 1.1
FY_STEEL_DEFAULT = 345000.0      # kPa (A992) when the material has no fy
ES_REBAR = 200.0e6               # kPa
EPS_CU = 0.003

PARAM_DEFAULTS = {
    "expected_factor": None,  # steel Fye/Fy (None = material Ry)
    "rho": 0.01,              # RC tension steel ratio As/(b d)
    "rho_prime": None,        # RC compression ratio (None: beams 0, cols rho)
    "fy_bar": 420000.0,       # kPa longitudinal rebar fy
    "fyt": None,              # kPa transverse fy (None = fy_bar)
    "rho_t": 0.002,           # transverse ratio Av/(b s)
    "s_over_d": 0.5,          # hoop spacing / d (Eq. 10-3 alpha_col)
    "cover_ratio": 0.1,       # d' / h  (d = (1 - cover_ratio) h)
    "fc_factor": 1.0,         # expected / specified fc
    "fy_factor": 1.0,         # expected / specified rebar fy
    "K": 1.0,                 # brace effective-length factor
    "shape": "auto",          # brace class: auto|W|2L_in|2L_out|HSS|pipe|angle
    "gamma_el": 1.5,          # EC8-3 (1.5 primary, 1.0 secondary)
    "db": 0.02,               # m, EC8-3 bar diameter
    "alpha_rho_sx": 0.0,      # EC8-3 confinement alpha * rho_sx
    "rho_d": 0.0,             # EC8-3 diagonal steel ratio
    "av": 1.0,                # EC8-3 shear-cracking flag (1 or 0)
    "Lv_ratio": 0.5,          # EC8-3 shear span / L
}
BRACE_SHAPES = ("auto", "W", "2L_in", "2L_out", "HSS", "pipe", "angle")
AUTO_KEYS = ("code", "component", "dof", "params", "gravity", "P")


def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{what} must be a number (got {v!r})")
    v = float(v)
    if not math.isfinite(v):
        raise ValueError(f"{what} must be finite")
    return v


def normalize_auto(d, what: str = "auto hinge") -> dict:
    """Validated canonical copy of an auto-hinge definition."""
    if not isinstance(d, dict):
        raise ValueError(f"{what}: 'auto' must be an object")
    bad = set(d) - set(AUTO_KEYS)
    if bad:
        raise ValueError(f"{what}: unknown auto keys {sorted(bad)}")
    code = d.get("code", "ASCE41-17")
    if code not in CODES:
        raise ValueError(f"{what}: code must be one of {CODES}")
    comp = d.get("component")
    if comp not in SUPPORT[code]:
        raise ValueError(f"{what}: component must be one of "
                         f"{tuple(SUPPORT[code])} for {code}")
    dofs = SUPPORT[code][comp]
    dof = d.get("dof", dofs[0])
    if dof not in dofs:
        raise ValueError(f"{what}: dof must be one of {dofs} for "
                         f"{code} {comp}")
    params = d.get("params") or {}
    if not isinstance(params, dict) or set(params) - set(PARAM_DEFAULTS):
        raise ValueError(f"{what}: params keys must be in "
                         f"{sorted(PARAM_DEFAULTS)}")
    out_p = {}
    for k, v in params.items():
        if k == "shape":
            if v not in BRACE_SHAPES:
                raise ValueError(f"{what}: params.shape must be one of "
                                 f"{BRACE_SHAPES}")
            out_p[k] = v
            continue
        if v is None:
            out_p[k] = None
            continue
        x = _num(v, f"{what} params.{k}")
        if k in ("alpha_rho_sx", "rho_d", "rho_prime", "av") and x < 0.0:
            raise ValueError(f"{what}: params.{k} must be >= 0")
        if k not in ("alpha_rho_sx", "rho_d", "rho_prime", "av") \
                and x <= 0.0:
            raise ValueError(f"{what}: params.{k} must be > 0")
        if k in ("rho", "rho_t", "rho_prime", "rho_d") and x >= 0.1:
            raise ValueError(f"{what}: params.{k} must be < 0.1")
        if k == "cover_ratio" and x >= 0.5:
            raise ValueError(f"{what}: params.cover_ratio must be < 0.5")
        out_p[k] = x
    grav = d.get("gravity")
    if grav is not None:
        if not isinstance(grav, dict):
            raise ValueError(f"{what}: gravity must be {{pattern: factor}}")
        grav = {str(k): _num(v, f"{what} gravity.{k}")
                for k, v in grav.items()}
    P = d.get("P")
    if P is not None:
        P = _num(P, f"{what} P")
    return {"code": code, "component": comp, "dof": dof, "params": out_p,
            "gravity": grav, "P": P}


def needs_axial(auto: dict) -> bool:
    """True when the generator uses the gravity axial load P_G."""
    return auto["component"] in ("steel_column", "rc_column",
                                 "rc_column_shear")


def _par(auto: dict, key: str):
    v = auto["params"].get(key)
    return PARAM_DEFAULTS[key] if v is None else v


def _interp(lo: dict, hi: dict, t: float) -> dict:
    t = min(max(t, 0.0), 1.0)
    return {k: lo[k] + t * (hi[k] - lo[k]) for k in lo}


# ============================================================ steel helpers
def aisc_compression(A: float, r: float, KL: float, E: float,
                     Fy: float) -> float:
    """AISC 360-16 E3 flexural-buckling strength Pn = Fcr Ag (kN)."""
    lam = KL / r
    Fe = math.pi ** 2 * E / lam ** 2
    if lam <= 4.71 * math.sqrt(E / Fy):            # Fy/Fe <= 2.25
        Fcr = 0.658 ** (Fy / Fe) * Fy               # E3-2
    else:
        Fcr = 0.877 * Fe                            # E3-3
    return Fcr * A


def _steel_ctx(model, m, auto, axis: str):
    from skyframe.design.steel import design_properties
    sec = model.sections[m.section]
    mat = model.materials[sec.material]
    props = design_properties(sec.name)
    if props is None:
        return None
    Fy = getattr(mat, "fy", None) or FY_STEEL_DEFAULT
    ef = _par(auto, "expected_factor")
    if ef is None:
        ef = getattr(mat, "Ry", RY_DEFAULT)
    E = mat.E
    I = (sec.I33 * sec.mod_I33) if axis == "M3" else (sec.I22 * sec.mod_I22)
    Z = props["Zx"] if axis == "M3" else props["Zy"]
    return {"props": props, "Fy": Fy, "Fye": ef * Fy, "E": E, "I": I,
            "Z": Z, "L": m.length, "A": props["A"],
            "km": 6.0 * E * I / m.length}


_STEEL_COMPACT = {"a": 9.0, "b": 11.0, "c": 0.6, "IO": 1.0, "LS": 9.0,
                  "CP": 11.0}
_STEEL_SLENDER = {"a": 4.0, "b": 6.0, "c": 0.2, "IO": 0.25, "LS": 3.0,
                  "CP": 4.0}
_FEMA_COMPACT = {"a": 9.0, "b": 11.0, "c": 0.6, "IO": 1.0, "LS": 6.0,
                 "CP": 8.0}
_FEMA_SLENDER = {"a": 4.0, "b": 6.0, "c": 0.2, "IO": 0.25, "LS": 2.0,
                 "CP": 3.0}


def _slenderness_t(props: dict, E: float, Fy: float, code: str) -> float:
    """0 at the compact limits, 1 at the slender limits (worse governs)."""
    bf2tf = props["bf"] / (2.0 * props["tf"])
    htw = (props["d"] - 2.0 * props["tf"]) / props["tw"]
    if code == "FEMA356":
        fy_ksi = Fy / 6894.757                          # kPa -> ksi
        r = 1.0 / math.sqrt(fy_ksi)
        tf_ = (bf2tf - 52.0 * r) / ((65.0 - 52.0) * r)
        tw_ = (htw - 418.0 * r) / ((640.0 - 418.0) * r)
    else:
        rt = math.sqrt(E / Fy)
        tf_ = (bf2tf / rt - 0.30) / (0.38 - 0.30)
        tw_ = (htw / rt - 2.45) / (3.76 - 2.45)
    return min(max(max(tf_, tw_), 0.0), 1.0)


def _spec(typ, F_B, a, b, c, acc, thy, km, meta, F_C=None, neg=None,
          acc_neg=None):
    F_C = F_B + HARDENING * km * a if F_C is None else F_C
    return {"type": typ,
            "pos": {"F_B": F_B, "F_C": F_C, "a": a, "b": max(b, a),
                    "c": c},
            "neg": neg, "acc": acc, "acc_neg": acc_neg, "thy": thy,
            "k_member": km, "meta": meta}


def _meta(code, comp, **inputs):
    info = TABLE_INFO[(code, comp)]
    return {"code": code, "component": comp, "table": info["table"],
            "clause": info["clause"],
            "preliminary": not info["verified"], "inputs": inputs}


def steel_beam(model, m, auto, axis, P_G=0.0):
    ctx = _steel_ctx(model, m, auto, axis)
    if ctx is None:
        return None, "section is not a library W shape"
    code = auto["code"]
    t = _slenderness_t(ctx["props"], ctx["E"], ctx["Fy"], code)
    lo, hi = ((_FEMA_COMPACT, _FEMA_SLENDER) if code == "FEMA356"
              else (_STEEL_COMPACT, _STEEL_SLENDER))
    row = _interp(lo, hi, t)
    My = ctx["Z"] * ctx["Fye"]
    thy = My * ctx["L"] / (6.0 * ctx["E"] * ctx["I"])      # Eq. 9-1 / 5-1
    acc = {k: row[k] * thy for k in ("IO", "LS", "CP")}
    return _spec(axis, My, row["a"] * thy, row["b"] * thy, row["c"], acc,
                 thy, ctx["km"], _meta(code, "steel_beam",
                                       slenderness_t=t, Fye=ctx["Fye"])), ""


def steel_column(model, m, auto, axis, P_G=0.0):
    ctx = _steel_ctx(model, m, auto, axis)
    if ctx is None:
        return None, "section is not a library W shape"
    code = auto["code"]
    props = ctx["props"]
    Pye = ctx["A"] * ctx["Fye"]
    P = max(P_G, 0.0)                     # compression (tension -> 0)
    ratio = P / Pye
    Z, Fye, E, I, L = ctx["Z"], ctx["Fye"], ctx["E"], ctx["I"], ctx["L"]
    if code == "FEMA356":
        PCL = aisc_compression(ctx["A"], props["ry"], L, E, ctx["Fy"])
        pr = P / PCL
        if pr > 0.5:
            return None, (f"P/PCL = {pr:.3f} > 0.5: force-controlled "
                          "(FEMA 356 Table 5-6); left elastic")
        My = min(1.18 * Z * Fye * (1.0 - ratio), Z * Fye)       # Eq. 5-4
        thy = Z * Fye * L / (6.0 * E * I) * (1.0 - ratio)       # Eq. 5-2
        t = _slenderness_t(props, E, ctx["Fy"], code)
        if pr < 0.2:
            row = _interp(_FEMA_COMPACT, _FEMA_SLENDER, t)
        else:
            f = 1.0 - 1.7 * pr
            comp_row = {"a": 11.0 * f, "b": 17.0 * f, "c": 0.2,
                        "IO": 0.25, "LS": 8.0 * f, "CP": 11.0 * f}
            slen_row = {"a": 1.0, "b": 1.5, "c": 0.2, "IO": 0.25,
                        "LS": 1.2, "CP": 1.2}
            row = _interp(comp_row, slen_row, t)
        acc = {k: row[k] * thy for k in ("IO", "LS", "CP")}
        return _spec(axis, My, row["a"] * thy, row["b"] * thy, row["c"],
                     acc, thy, ctx["km"],
                     _meta(code, "steel_column", P_G=P_G, P_ratio=ratio,
                           P_PCL=pr, slenderness_t=t)), ""
    # ASCE 41-17 / 41-23
    if ratio > 0.6:
        return None, (f"PG/Pye = {ratio:.3f} > 0.6: force-controlled "
                      "(Table 9-7.1); left elastic")
    if ratio < 0.2:
        My = Z * Fye * (1.0 - ratio / 2.0)
    else:
        My = 9.0 / 8.0 * Z * Fye * (1.0 - ratio)
    thy = My * L / (6.0 * E * I)
    htw = (props["d"] - 2.0 * props["tf"]) / props["tw"]
    Lry = L / props["ry"]
    a = max(0.0, 0.8 * (1.0 - ratio) ** 2.2
            / (0.1 * Lry + 0.8 * htw) - 0.0035)
    b = max(0.0, 7.4 * (1.0 - ratio) ** 2.3
            / (0.5 * Lry + 2.9 * htw) - 0.006)
    c = 0.9 - 0.9 * ratio
    if a <= 0.0:
        return None, ("Table 9-7.1 modeling parameter a = 0: "
                      "force-controlled; left elastic")
    b = max(b, a)
    acc = {"IO": 0.5 * a, "LS": 0.75 * b, "CP": b}
    return _spec(axis, My, a, b, c, acc, thy, ctx["km"],
                 _meta(code, "steel_column", P_G=P_G, P_ratio=ratio,
                       L_ry=Lry, h_tw=htw)), ""


# brace rows (multiples of Delta_c / Delta_T): a, b, c, IO, LS, CP
_BRACE_COMP_SLENDER = {
    "W": (0.5, 10.0, 0.3, 0.5, 8.0, 10.0),
    "2L_in": (0.5, 10.0, 0.3, 0.5, 8.0, 10.0),
    "2L_out": (0.5, 9.0, 0.3, 0.5, 7.0, 9.0),
    "HSS": (0.5, 9.0, 0.3, 0.5, 7.0, 9.0),
    "pipe": (0.5, 9.0, 0.3, 0.5, 7.0, 9.0),
    "angle": (0.5, 12.0, 0.3, 0.5, 10.0, 12.0)}
_BRACE_COMP_STOCKY = {
    "W": (1.0, 8.0, 0.5, 0.5, 7.0, 8.0),
    "2L_in": (1.0, 8.0, 0.5, 0.5, 7.0, 8.0),
    "2L_out": (1.0, 7.0, 0.5, 0.5, 6.0, 7.0),
    "HSS": (1.0, 7.0, 0.5, 0.5, 6.0, 7.0),
    "pipe": (1.0, 7.0, 0.5, 0.5, 6.0, 7.0),
    "angle": (1.0, 10.0, 0.5, 0.5, 9.0, 10.0)}
_BRACE_TENSION = {
    "W": (10.0, 13.0, 0.6, 0.5, 10.0, 13.0),
    "2L_in": (9.0, 12.0, 0.6, 0.5, 9.0, 12.0),
    "2L_out": (9.0, 12.0, 0.6, 0.5, 9.0, 12.0),
    "HSS": (9.0, 11.0, 0.6, 0.5, 8.0, 11.0),
    "pipe": (8.0, 9.0, 0.6, 0.5, 7.0, 9.0),
    "angle": (10.0, 11.0, 0.6, 0.5, 9.0, 11.0)}
_ROW_KEYS = ("a", "b", "c", "IO", "LS", "CP")


def brace_shape(name: str) -> str:
    """Shape class from the section name (ETABS library prefixes)."""
    n = (name or "").upper().replace(" ", "")
    if n.startswith("2L"):
        return "2L_in"
    if n.startswith("HSS") or n.startswith("TUBE"):
        # rectangular HSS "HSS6X6X1/2" (two X); round "HSS6.000X0.500"
        return "HSS" if n.count("X") >= 2 else "pipe"
    if n.startswith("PIPE"):
        return "pipe"
    if n.startswith("L"):
        return "angle"
    return "W"


def steel_brace(model, m, auto, axis="P", P_G=0.0):
    sec = model.sections[m.section]
    mat = model.materials[sec.material]
    E = mat.E
    A = sec.A * sec.mod_A
    if A <= 0.0:
        return None, "section area must be > 0"
    r = math.sqrt(min(sec.I22, sec.I33) / sec.A)
    Fy = getattr(mat, "fy", None) or FY_STEEL_DEFAULT
    ef = _par(auto, "expected_factor")
    if ef is None:
        ef = getattr(mat, "Ry", RY_DEFAULT)
    Fye = ef * Fy
    L = m.length
    KL = _par(auto, "K") * L
    shape = _par(auto, "shape")
    if shape == "auto":
        shape = brace_shape(sec.name)
    P_CE = aisc_compression(A, r, KL, E, Fye)       # AISC E3 with Fye
    T_CE = A * Fye
    km = E * A / L
    dc = P_CE / km
    dt = T_CE / km
    rt = math.sqrt(E / Fye)
    lam = KL / r
    t = (lam - 2.1 * rt) / (4.2 * rt - 2.1 * rt)
    cr = _interp(dict(zip(_ROW_KEYS, _BRACE_COMP_STOCKY[shape])),
                 dict(zip(_ROW_KEYS, _BRACE_COMP_SLENDER[shape])), t)
    tr = dict(zip(_ROW_KEYS, _BRACE_TENSION[shape]))
    meta = _meta(auto["code"], "steel_brace", shape=shape, KL_r=lam,
                 P_CE=P_CE, T_CE=T_CE, Delta_c=dc, Delta_T=dt,
                 slenderness_t=min(max(t, 0.0), 1.0))
    neg = {"F_B": P_CE, "F_C": P_CE, "a": cr["a"] * dc,
           "b": max(cr["b"], cr["a"]) * dc, "c": cr["c"]}
    acc_neg = {k: cr[k] * dc for k in ("IO", "LS", "CP")}
    acc = {k: tr[k] * dt for k in ("IO", "LS", "CP")}
    return _spec("P", T_CE, tr["a"] * dt, tr["b"] * dt, tr["c"], acc, dt,
                 km, meta, neg=neg, acc_neg=acc_neg), ""


# ============================================================== RC helpers
def rc_section_Mn(b: float, h: float, fc: float, fy: float, As: float,
                  As2: float, d: float, d2: float, P: float = 0.0
                  ) -> Optional[float]:
    """Nominal moment (kN*m) of a rectangular RC section at axial load P
    (kN, compression +) — ACI strain compatibility: eps_cu = 0.003,
    Whitney block 0.85 fc' over beta1 c, bars elastic-perfectly-plastic
    (Es = 200 GPa), compression bars inside the block displace concrete.
    Moment about mid-depth.  None when P is outside the axial range."""
    from skyframe.design.concrete import beta1 as _beta1
    b1 = _beta1(fc)

    def forces(c):
        a = min(b1 * c, h)
        Cc = 0.85 * fc * b * a
        e2 = EPS_CU * (c - d2) / c
        e1 = EPS_CU * (c - d) / c
        f2 = max(-fy, min(fy, ES_REBAR * e2))
        f1 = max(-fy, min(fy, ES_REBAR * e1))
        F2 = As2 * (f2 - (0.85 * fc if a > d2 else 0.0))
        F1 = As * (f1 - (0.85 * fc if a > d else 0.0))
        return Cc + F2 + F1, Cc, F2, F1, a

    Pmax = 0.85 * fc * (b * h - As - As2) + fy * (As + As2)
    Pmin = -fy * (As + As2)
    if not Pmin < P < Pmax:
        return None
    lo, hi = 1e-9 * h, 1e3 * h
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if forces(mid)[0] < P:
            lo = mid
        else:
            hi = mid
    _, Cc, F2, F1, a = forces(0.5 * (lo + hi))
    return Cc * (h / 2 - a / 2) + F2 * (h / 2 - d2) + F1 * (h / 2 - d)


def _rc_ctx(model, m, auto, axis):
    sec = model.sections[m.section]
    if not (sec.b > 0.0 and sec.h > 0.0):
        return None
    mat = model.materials[sec.material]
    from skyframe.design.wall import fc_from_E
    fc = (getattr(mat, "fc", None) or fc_from_E(mat.E)) \
        * _par(auto, "fc_factor")
    fy = _par(auto, "fy_bar") * _par(auto, "fy_factor")
    b, h = (sec.b, sec.h) if axis in ("M3", "V2") else (sec.h, sec.b)
    I = (sec.I33 * sec.mod_I33) if axis in ("M3", "V2") \
        else (sec.I22 * sec.mod_I22)
    cr = _par(auto, "cover_ratio")
    d = (1.0 - cr) * h
    return {"b": b, "h": h, "fc": fc, "fy": fy, "E": mat.E, "I": I,
            "L": m.length, "d": d, "d2": cr * h,
            "km": 6.0 * mat.E * I / m.length}


def rc_beam(model, m, auto, axis, P_G=0.0):
    ctx = _rc_ctx(model, m, auto, axis)
    if ctx is None:
        return None, "section has no rectangular drawing dims b/h"
    code = auto["code"]
    if code == "EC8-3":
        return _ec8(model, m, auto, axis, P_G, "rc_beam")
    rho = _par(auto, "rho")
    rp = auto["params"].get("rho_prime")
    rho_p = 0.0 if rp is None else rp
    from skyframe.design.concrete import beta1 as _beta1
    b, h, fc, fy, d = ctx["b"], ctx["h"], ctx["fc"], ctx["fy"], ctx["d"]
    As = rho * b * d
    Mn = rc_section_Mn(b, h, fc, fy, As, rho_p * b * d, d, ctx["d2"], 0.0)
    fy_mpa = fy / 1000.0
    rho_bal = 0.85 * _beta1(fc) * (fc / fy) * 600.0 / (600.0 + fy_mpa)
    t = ((rho - rho_p) / rho_bal) / 0.5
    if code == "FEMA356":
        lo = {"a": 0.025, "b": 0.05, "c": 0.2, "IO": 0.010, "LS": 0.02,
              "CP": 0.025}
        hi = {"a": 0.02, "b": 0.03, "c": 0.2, "IO": 0.005, "LS": 0.01,
              "CP": 0.02}
    else:
        lo = {"a": 0.025, "b": 0.05, "c": 0.2, "IO": 0.010, "LS": 0.025,
              "CP": 0.05}
        hi = {"a": 0.020, "b": 0.04, "c": 0.2, "IO": 0.005, "LS": 0.020,
              "CP": 0.04}
    row = _interp(lo, hi, t)
    thy = Mn * ctx["L"] / (6.0 * ctx["E"] * ctx["I"])
    acc = {k: row[k] for k in ("IO", "LS", "CP")}
    return _spec(axis, Mn, row["a"], row["b"], row["c"], acc, thy,
                 ctx["km"], _meta(code, "rc_beam", rho=rho, rho_prime=rho_p,
                                  rho_ratio=(rho - rho_p) / rho_bal)), ""


def column_shear_strength(b: float, h: float, fc: float, fyt: float,
                          rho_t: float, N: float, L: float,
                          s_over_d: float = 0.5) -> dict:
    """ASCE 41-17 Eq. 10-3 (k_nl = 1, lambda = 1), SI in / kN out.

    V = alpha_col Av fyt d / s
        + (0.5 sqrt(fc)/(M/Vd)) sqrt(1 + N/(0.5 sqrt(fc) Ag)) 0.8 Ag
    [MPa units]; Av/s = rho_t b; d = 0.8 h; M/(V d) = L/(2d) in [2, 4];
    alpha_col = 1 for s/d <= 0.75, 0 for s/d >= 1 (linear)."""
    d = 0.8 * h
    Ag = b * h
    sq = math.sqrt(fc / 1000.0) * 1000.0            # sqrt(fc MPa) -> kPa
    alpha = 1.0 if s_over_d <= 0.75 else (
        0.0 if s_over_d >= 1.0 else (1.0 - s_over_d) / 0.25)
    Vs = alpha * rho_t * b * fyt * d
    mvd = min(max(L / (2.0 * d), 2.0), 4.0)
    arg = 1.0 + N / (0.5 * sq * Ag)
    Vc = 0.5 * sq / mvd * math.sqrt(max(arg, 0.0)) * 0.8 * Ag
    return {"V": Vs + Vc, "Vs": Vs, "Vc": Vc, "alpha_col": alpha,
            "M_Vd": mvd, "d": d}


def _eff_stiffness_factor(n: float) -> float:
    """ASCE 41-17 Table 10-5: 0.3 Ig at N_UG <= 0.1 Ag f'c, 0.7 at >= 0.5
    (linear between)."""
    return 0.3 + (min(max(n, 0.1), 0.5) - 0.1) / 0.4 * 0.4


def rc_column(model, m, auto, axis, P_G=0.0):
    ctx = _rc_ctx(model, m, auto, axis)
    if ctx is None:
        return None, "section has no rectangular drawing dims b/h"
    code = auto["code"]
    if code == "EC8-3":
        return _ec8(model, m, auto, axis, P_G, "rc_column")
    b, h, fc, fy, d, L = (ctx["b"], ctx["h"], ctx["fc"], ctx["fy"],
                          ctx["d"], ctx["L"])
    rho = _par(auto, "rho")
    rp = auto["params"].get("rho_prime")
    rho_p = rho if rp is None else rp
    As, As2 = rho * b * d, rho_p * b * d
    Mn = rc_section_Mn(b, h, fc, fy, As, As2, d, ctx["d2"], P_G)
    if Mn is None or Mn <= 0.0:
        return None, (f"P_G = {P_G:.1f} kN outside the section's axial "
                      "range: force-controlled; left elastic")
    Ag = b * h
    n = P_G / (Ag * fc)
    rho_t = min(_par(auto, "rho_t"), 0.0175)
    fyt = _par(auto, "fyt") or _par(auto, "fy_bar")
    sh = column_shear_strength(b, h, fc, fyt, rho_t, P_G, L,
                               _par(auto, "s_over_d"))
    VyE = 2.0 * Mn / L
    vr = VyE / sh["V"]
    a = max(0.0, 0.042 - 0.043 * n + 0.63 * rho_t - 0.023 * vr)
    nb = max(n, 0.1)
    b_ = max(a, 0.5 / (5.0 + nb / 0.8 / rho_t * fc / fyt) - 0.01)
    c = max(0.0, 0.24 - 0.4 * n)
    acc = {"IO": min(0.15 * a, 0.005), "LS": 0.5 * b_, "CP": 0.7 * b_}
    kf = _eff_stiffness_factor(n)
    Ig = b * h ** 3 / 12.0
    thy = Mn * L / (6.0 * kf * ctx["E"] * Ig)
    return _spec(axis, Mn, a, b_, c, acc, thy, ctx["km"],
                 _meta(code, "rc_column", P_G=P_G, n=n, rho_t=rho_t,
                       VyE=VyE, VColOE=sh["V"], VyE_VColOE=vr,
                       EIeff_factor=kf)), ""


def rc_column_shear(model, m, auto, axis, P_G=0.0):
    sec = model.sections[m.section]
    if not (sec.b > 0.0 and sec.h > 0.0):
        return None, "section has no rectangular drawing dims b/h"
    mat = model.materials[sec.material]
    from skyframe.design.wall import fc_from_E
    fc = (getattr(mat, "fc", None) or fc_from_E(mat.E)) \
        * _par(auto, "fc_factor")
    # V2 = shear along local 2 (depth h along 2); V3 along local 3
    b, h = (sec.b, sec.h) if axis == "V2" else (sec.h, sec.b)
    I = sec.I33 * sec.mod_I33 if axis == "V2" else sec.I22 * sec.mod_I22
    L = m.length
    fyt = _par(auto, "fyt") or _par(auto, "fy_bar")
    sh = column_shear_strength(b, h, fc, fyt, _par(auto, "rho_t"), P_G, L,
                               _par(auto, "s_over_d"))
    km = 12.0 * mat.E * I / L ** 3
    V = sh["V"]
    delta = 1.0e-3 * L                    # force-controlled plateau
    acc = {"IO": 0.0, "LS": 0.0, "CP": 0.0}
    return _spec(axis, V, delta, delta, 0.2, acc, V / km, km,
                 _meta(auto["code"], "rc_column_shear", P_G=P_G,
                       force_controlled=True, **sh), F_C=V), ""


def _ec8(model, m, auto, axis, P_G, comp):
    """EN 1998-3 Annex A chord-rotation hinge (MPa / mm internally)."""
    ctx = _rc_ctx(model, m, auto, axis)
    b = ctx["b"] * 1e3
    h = ctx["h"] * 1e3
    d = ctx["d"] * 1e3
    d2 = ctx["d2"] * 1e3
    fc = ctx["fc"] / 1e3
    fy = ctx["fy"] / 1e3
    Ec = ctx["E"] / 1e3
    Es = ES_REBAR / 1e3
    rho = _par(auto, "rho")
    rp = auto["params"].get("rho_prime")
    rho_p = (0.0 if comp == "rc_beam" else rho) if rp is None else rp
    N = P_G * 1e3                                  # kN -> N (comp +)
    L = ctx["L"] * 1e3
    Lv = _par(auto, "Lv_ratio") * L
    dp = d2 / d
    alpha = Es / Ec
    nn = N / (b * d * fy)
    A_ = rho + rho_p + nn
    B_ = rho + rho_p * dp + nn
    xi = math.sqrt(alpha ** 2 * A_ ** 2 + 2.0 * alpha * B_) - alpha * A_
    phi_y = fy / (Es * (1.0 - xi) * d)             # (A.10)-(A.12), 1/mm
    My = b * d ** 3 * phi_y * (
        Ec * xi ** 2 / 2.0 * (0.5 * (1.0 + dp) - xi / 3.0)
        + Es / 2.0 * ((1.0 - xi) * rho + (xi - dp) * rho_p) * (1.0 - dp))
    My_kNm = My / 1e6
    if My_kNm <= 0.0:
        return None, "EC8-3: non-positive yield moment at this axial load"
    av = _par(auto, "av")
    z = 0.9 * d
    db = _par(auto, "db") * 1e3
    theta_y = (phi_y * (Lv + av * z) / 3.0
               + 0.0013 * (1.0 + 1.5 * h / Lv)
               + 0.13 * phi_y * db * fy / math.sqrt(fc))   # (A.10b)
    nu = N / (b * h * fc)
    w = rho * fy / fc
    wp = rho_p * fy / fc
    fyw = (_par(auto, "fyt") or _par(auto, "fy_bar")) / 1e3
    theta_um = (1.0 / _par(auto, "gamma_el")) * 0.016 * 0.3 ** nu * (
        max(0.01, wp) / max(0.01, w) * fc) ** 0.225 * (Lv / h) ** 0.35 \
        * 25.0 ** (_par(auto, "alpha_rho_sx") * fyw / fc) \
        * 1.25 ** (100.0 * _par(auto, "rho_d"))            # (A.1)
    a = max(theta_um - theta_y, 0.0)
    acc = {"IO": 0.0, "LS": max(0.75 * theta_um - theta_y, 0.0),
           "CP": a}
    return _spec(axis, My_kNm, a, a, 0.0, acc, theta_y, ctx["km"],
                 _meta("EC8-3", comp, P_G=P_G, nu=nu, phi_y=phi_y * 1e3,
                       xi_y=xi, theta_y=theta_y, theta_um=theta_um,
                       Lv=Lv / 1e3)), ""


GENERATORS = {"steel_beam": steel_beam, "steel_column": steel_column,
              "steel_brace": steel_brace, "rc_beam": rc_beam,
              "rc_column": rc_column, "rc_column_shear": rc_column_shear}


def generate(model, m, auto: dict, P_G: float = 0.0
             ) -> Tuple[List[dict], str]:
    """Specs for one member (``PMM`` -> [M2 spec, M3 spec]); ([], reason)
    when the generator cannot size it."""
    if getattr(m, "axial_limit", "both") != "both":
        return [], "axial-only member"
    gen = GENERATORS[auto["component"]]
    axes = ("M2", "M3") if auto["dof"] == "PMM" else (auto["dof"],)
    out = []
    for ax in axes:
        spec, why = gen(model, m, auto, ax, P_G)
        if spec is None:
            return [], why
        out.append(spec)
    return out, ""


PLATEAU_RISE = 1.0e-6     # B -> C plateau gets this relative rise: the
                          # multi-point HystereticSM needs rising first
                          # two segments (a flat one aborts OpenSees)


def _side_points(s: dict) -> List[List[float]]:
    F_C = max(s["F_C"], s["F_B"] * (1.0 + PLATEAU_RISE))
    pts = [[0.0, 0.0], [0.0, s["F_B"]], [s["a"], F_C]]
    cF = s["c"] * s["F_B"]
    if s["c"] > 0.0:
        pts.append([s["a"], cF])
        if s["b"] > s["a"]:
            pts.append([s["b"], cF])
    return pts


def spec_to_property(spec: dict, base: Optional[dict] = None) -> dict:
    """Canonical B10 property dict (absolute values) of a spec.

    ``base`` (the auto property) supplies hysteresis / hysteresis_params /
    k_elastic; an asymmetric spec with a kinematic/isotropic rule switches
    to takeda (MultiLinear is symmetric)."""
    base = base or {}
    pos = _side_points(spec["pos"])
    bb = pos if spec.get("neg") is None else {
        "positive": pos, "negative": _side_points(spec["neg"])}
    hy = base.get("hysteresis", "kinematic")
    if isinstance(bb, dict) and hy in ("kinematic", "isotropic"):
        hy = "takeda"
    return {"type": spec["type"], "backbone": bb,
            "scale": {"yield_value": 1.0, "yield_deformation": None},
            "acceptance": dict(spec["acc"]), "hysteresis": hy,
            "drop_strength": "drops",
            "k_elastic": base.get("k_elastic"),
            "hysteresis_params": base.get("hysteresis_params")}


def catalog() -> dict:
    """JSON catalogue for the UI: codes -> components -> dofs + table info
    + parameter defaults."""
    return {"codes": {c: {comp: {"dofs": list(dofs),
                                 **TABLE_INFO[(c, comp)]}
                          for comp, dofs in SUPPORT[c].items()}
                      for c in CODES},
            "params": dict(PARAM_DEFAULTS),
            "brace_shapes": list(BRACE_SHAPES)}
