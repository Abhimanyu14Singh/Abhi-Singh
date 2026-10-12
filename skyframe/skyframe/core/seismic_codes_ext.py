"""v1.19 auto seismic generators, international codes (CONTRACT "Auto
lateral loads: international seismic and wind codes, ASCE 7 wind load
cases").

Registered into :mod:`skyframe.core.autolateral` (``CODES`` / ``compute``
/ ``generate`` / ``POST /api/pattern/auto-lateral``) by a one-line hook at
the end of that module.  Every ``compute_*`` follows the existing
generators exactly: story seismic weights from
:func:`autolateral.story_weights` (mass source x g, or a ``weights``
{story: kN} override), story elevations above the base, and the summary::

    {"code", "direction", "T", "W", "V", <coefficients>,
     "stories": [{"story", "h", "w", "F"}, ...]}

Towers are handled by ``autolateral.compute`` (each tower distributes its
own base shear over its own stories).  Codes:

* ``nzs1170_5`` - NZS 1170.5:2004 equivalent static method (§6.2).
* ``as1170_4`` - AS 1170.4-2007 equivalent static (§6.2 - §6.3).
* ``gb50011`` - GB 50011-2010 base shear method (§5.2.1, spectrum §5.1.5).
* ``tsc2018`` - Turkish TBDY 2018 equivalent lateral load (§4.7).
* ``kds41`` - KDS 41 17 00:2019 (Korea) equivalent static (§7.2).
* ``nscp2015`` - NSCP 2015 (Philippines, §208.5 static force procedure).
* ``ntc_cdmx`` - Mexico City NTC-Sismo 2017/2020 static method (§7) with
  the §3.1 site spectrum (SASID parameters a0, c, Ta, Tb, k).

Coefficient tables are transcribed from the codes cited at each constant;
every table value can be bypassed by an explicit override parameter.
"""
from __future__ import annotations

import math
from typing import Dict, Optional

from .autolateral import (_check_common, _distribute, _k_exponent, _pos,
                          _summary, asce22_cu, story_weights)


def _num(label, v):
    if (isinstance(v, bool) or not isinstance(v, (int, float))
            or not math.isfinite(v)):
        raise ValueError(f"{label} must be a finite number")
    return float(v)


def _interp(x: float, xs, ys) -> float:
    """Linear interpolation clamped at the table ends."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for x0, x1, y0, y1 in zip(xs, xs[1:], ys, ys[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return ys[-1]  # pragma: no cover


def _choice(label, v, allowed):
    if v not in allowed:
        raise ValueError(f"{label} must be one of {tuple(allowed)}")
    return v


def _top_force_distribution(model, V: float, w: Dict[str, float],
                            Ft: float) -> list:
    """``F_i = (V - Ft) w_i h_i / sum w h`` plus ``Ft`` at the top story
    (the story with the greatest elevation)."""
    shape = {s.name: s.elevation for s in model.stories}
    rows = _distribute(model, V - Ft, w, shape)
    top = max(model.stories, key=lambda s: s.elevation).name
    for r in rows:
        if r["story"] == top:
            r["F"] += Ft
    return rows


def _empirical_T(hn: float, structure: str, table: dict, label: str,
                 factor: float = 1.0, expo: float = 0.75) -> float:
    _choice(label, structure, table)
    return factor * table[structure] * hn ** expo


# ============================================================ NZS 1170.5
# NZS 1170.5:2004 §3.1.2 spectral shape factor Ch(T): soil class ->
# (T plateau start 0.1 s ramp is replaced by the plateau for the
# equivalent static method - Table 3.1 bracketed values)
def nzs_ch(T: float, soil: str) -> float:
    """NZS 1170.5 §3.1.2 Ch(T), equivalent static method (T < 0.1 s takes
    the plateau value, Table 3.1 bracketed values)."""
    _choice("soil", soil, ("A", "B", "C", "D", "E"))
    T = max(T, 0.1)
    if soil in ("A", "B"):
        if T < 0.3:
            return 2.35
        if T < 1.5:
            return 1.60 * (0.5 / T) ** 0.75
        if T < 3.0:
            return 1.05 / T
        return 3.15 / T ** 2
    if soil == "C":
        if T < 0.3:
            return 2.93
        if T < 1.5:
            return 2.0 * (0.5 / T) ** 0.75
        if T < 3.0:
            return 1.32 / T
        return 3.96 / T ** 2
    if soil == "D":
        if T < 0.56:
            return 3.0
        if T < 1.5:
            return 2.4 * (0.75 / T) ** 0.75
        if T < 3.0:
            return 2.14 / T
        return 6.42 / T ** 2
    if T < 1.0:                                   # E
        return 3.0
    if T < 1.5:
        return 3.0 / T ** 0.75
    if T < 3.0:
        return 3.32 / T
    return 9.96 / T ** 2


def nzs_kmu(T1: float, mu: float, soil: str) -> float:
    """NZS 1170.5 §5.2.1.1 inelastic spectrum scaling factor; T1 is not
    taken less than 0.4 s for kmu."""
    T1 = max(T1, 0.4)
    if soil == "E":
        if T1 >= 1.0 or mu < 1.5:
            return mu
        return (mu - 1.5) * T1 + 1.5
    if T1 >= 0.7:
        return mu
    return (mu - 1.0) * T1 / 0.7 + 1.0


def nzs_sp(mu: float) -> float:
    """NZS 1170.5 §4.4.2 structural performance factor: 0.7 for mu >= 2,
    1.3 - 0.3 mu for 1 < mu < 2, 1.0 for mu = 1 (the general case)."""
    if mu >= 2.0:
        return 0.7
    if mu <= 1.0:
        return 1.0
    return 1.3 - 0.3 * mu


# AS 1170.4 / NZS commentary C4.1.2.1 empirical period kt (T = 1.25 kt hn^0.75)
KT_AUS = {"steel_mrf": 0.11, "concrete_mrf": 0.075, "ebf": 0.06,
          "other": 0.05}


def compute_nzs1170_5(model, Z: float, mu: float = 4.0, soil: str = "C",
                      R: float = 1.0, N: float = 1.0,
                      Sp: Optional[float] = None, T: Optional[float] = None,
                      structure: str = "concrete_mrf", weights=None,
                      direction: str = "X") -> dict:
    """NZS 1170.5:2004 §6.2 equivalent static method.

    * ``C(T) = Ch(T) Z R N`` (§3.1.1, ``Z R <= 0.7``), Ch per §3.1.2 with
      the plateau value for T < 0.1 s (equivalent static method).
    * ``Cd(T1) = C(T1) Sp / kmu`` (§5.2.1.1, Eq. 5.2(1)), kmu with T1 >=
      0.4 s, ``Cd >= max((Z/20 + 0.02) R, 0.03 R)`` (Eq. 5.2(2)).
    * ``V = Cd W`` (§6.2.1.1); ``F_i = Ft + 0.92 V w_i h_i / sum w h``
      with ``Ft = 0.08 V`` at the top level (§6.2.1.3).
    * T1: given, else ``1.25 kt hn^0.75`` (commentary C4.1.2.1 / AS
      1170.4 Eq. 6.2(7), kt 0.11 / 0.075 / 0.06 / 0.05).
    * ``Sp`` default from mu (§4.4.2).
    """
    hn = _check_common(model, direction)
    for lab, v in (("Z", Z), ("mu", mu), ("R", R), ("N", N)):
        _pos(lab, v)
    if mu < 1.0:
        raise ValueError("mu must be >= 1")
    _choice("soil", soil, ("A", "B", "C", "D", "E"))
    T_used = (_empirical_T(hn, structure, KT_AUS, "structure", 1.25)
              if T is None else _pos("T", T))
    Sp = nzs_sp(mu) if Sp is None else _pos("Sp", Sp)
    ZR = min(Z * R, 0.7)
    Ch = nzs_ch(T_used, soil)
    C = Ch * ZR * N
    kmu = nzs_kmu(T_used, mu, soil)
    Cd_min = max((Z / 20.0 + 0.02) * R, 0.03 * R)
    Cd = max(C * Sp / kmu, Cd_min)
    w = story_weights(model, weights)
    W = sum(w.values())
    V = Cd * W
    Ft = 0.08 * V
    shape = {s.name: s.elevation for s in model.stories}
    rows = _distribute(model, 0.92 * V, w, shape)
    top = max(model.stories, key=lambda s: s.elevation).name
    for r in rows:
        if r["story"] == top:
            r["F"] += Ft
    return _summary("nzs1170_5", direction, T_used, W, V, rows, Ch=Ch, C=C,
                    Sp=Sp, kmu=kmu, Cd=Cd, Cd_min=Cd_min, Ft=Ft)


# ============================================================= AS 1170.4
# AS 1170.4-2007 Table 6.4 spectral shape factor: site -> (ramp a, ramp
# b, plateau, T<=1.5 numerator, T>1.5 numerator)
AS_CH = {"Ae": (0.8, 15.5, 2.35, 0.704, 1.056),
         "Be": (1.0, 19.4, 2.94, 0.88, 1.32),
         "Ce": (1.3, 23.8, 3.68, 1.25, 1.874),
         "De": (1.1, 25.8, 3.68, 1.98, 2.97),
         "Ee": (1.1, 25.8, 3.68, 3.08, 4.62)}


def as_ch(T: float, site: str) -> float:
    """AS 1170.4 Table 6.4 Ch(T) (equivalent static: the plateau applies
    for T <= 0.1 s)."""
    _choice("site", site, AS_CH)
    _a, _b, plat, n1, n2 = AS_CH[site]
    if T <= 1.5:
        return min(plat, n1 / T) if T > 0.1 else plat
    return n2 / T ** 2


def compute_as1170_4(model, Z: float, kp: float = 1.0, site: str = "Ce",
                     mu: float = 2.0, Sp: float = 0.77,
                     T: Optional[float] = None,
                     structure: str = "concrete_mrf",
                     kpZ_min: float = 0.08, weights=None,
                     direction: str = "X") -> dict:
    """AS 1170.4-2007 §6.2 equivalent static method.

    * ``V = kp Z Ch(T1) (Sp/mu) W`` (Eq. 6.2(1)) with ``kp Z >= kpZ_min``
      (Table 3.3, 0.08) and the coefficient ``>= 0.01`` (§6.2.1).
    * ``T1 = 1.25 kt hn^0.75`` (Eq. 6.2(7); kt 0.11 steel MRF, 0.075
      concrete MRF, 0.06 EBF, 0.05 other) unless given.
    * ``F_i = V w_i h_i^k / sum w h^k`` (§6.3), k = 1 (T1 <= 0.5 s), 2
      (T1 >= 2.5 s), linear between.
    """
    hn = _check_common(model, direction)
    for lab, v in (("Z", Z), ("kp", kp), ("mu", mu), ("Sp", Sp)):
        _pos(lab, v)
    kpZ_min = _pos("kpZ_min", kpZ_min, allow_zero=True)
    T_used = (_empirical_T(hn, structure, KT_AUS, "structure", 1.25)
              if T is None else _pos("T", T))
    Ch = as_ch(T_used, site)
    kpZ = max(kp * Z, kpZ_min)
    coef = max(kpZ * Ch * Sp / mu, 0.01)
    w = story_weights(model, weights)
    W = sum(w.values())
    V = coef * W
    k = _k_exponent(T_used)
    shape = {s.name: s.elevation ** k for s in model.stories}
    return _summary("as1170_4", direction, T_used, W, V,
                    _distribute(model, V, w, shape), k=k, Ch=Ch, kpZ=kpZ,
                    Cs=coef)


# ============================================================ GB 50011
# GB 50011-2010 Table 5.1.4-1 alpha_max: intensity -> (frequent, rare)
GB_ALPHA_MAX = {"6": (0.04, 0.28), "7": (0.08, 0.50), "7.5": (0.12, 0.72),
                "8": (0.16, 0.90), "8.5": (0.24, 1.20), "9": (0.32, 1.40)}
# Table 5.1.4-2 characteristic period Tg: group -> site I0, I1, II, III, IV
GB_TG = {1: (0.20, 0.25, 0.35, 0.45, 0.65),
         2: (0.25, 0.30, 0.40, 0.55, 0.75),
         3: (0.30, 0.35, 0.45, 0.65, 0.90)}
GB_SITES = ("I0", "I1", "II", "III", "IV")


def gb_alpha(T: float, alpha_max: float, Tg: float,
             zeta: float = 0.05) -> dict:
    """GB 50011-2010 §5.1.5 seismic influence coefficient curve (Fig.
    5.1.5) with the damping adjustments Eq. 5.1.5-1..3."""
    gamma = 0.9 + (0.05 - zeta) / (0.3 + 6.0 * zeta)
    eta1 = max(0.02 + (0.05 - zeta) / (4.0 + 32.0 * zeta), 0.0)
    eta2 = max(1.0 + (0.05 - zeta) / (0.08 + 1.6 * zeta), 0.55)
    if T < 0.1:
        a = (0.45 + 10.0 * (eta2 - 0.45) * T) * alpha_max
    elif T <= Tg:
        a = eta2 * alpha_max
    elif T <= 5.0 * Tg:
        a = (Tg / T) ** gamma * eta2 * alpha_max
    else:
        a = (eta2 * 0.2 ** gamma - eta1 * (T - 5.0 * Tg)) * alpha_max
    return {"alpha": a, "gamma": gamma, "eta1": eta1, "eta2": eta2}


def gb_delta_n(T1: float, Tg: float) -> float:
    """GB 50011 Table 5.2.1 top additional seismic action factor (RC /
    steel multi-storey buildings)."""
    if T1 <= 1.4 * Tg:
        return 0.0
    if Tg <= 0.35:
        return 0.08 * T1 + 0.07
    if Tg <= 0.55:
        return 0.08 * T1 + 0.01
    return 0.08 * T1 - 0.02


def compute_gb50011(model, T: float, intensity: str = "8",
                    level: str = "frequent", site: str = "II",
                    group: int = 1, alpha_max: Optional[float] = None,
                    Tg: Optional[float] = None, zeta: float = 0.05,
                    geq_factor: Optional[float] = None,
                    top_force: bool = True, weights=None,
                    direction: str = "X") -> dict:
    """GB 50011-2010 §5.2.1 base shear method.

    * ``FEk = alpha1 Geq`` (Eq. 5.2.1-1), ``Geq = 0.85 GE`` for multi-storey
      buildings (1.0 single storey) unless ``geq_factor`` is given.
    * alpha1 from the §5.1.5 curve; alpha_max Table 5.1.4-1 (intensity 6,
      7, 7.5 (0.15 g), 8, 8.5 (0.30 g), 9; frequent / rare), Tg Table
      5.1.4-2 (+0.05 s for rare earthquakes, §5.1.4).
    * ``F_i = G_i H_i / sum G_j H_j FEk (1 - delta_n)`` (Eq. 5.2.1-2) plus
      ``dFn = delta_n FEk`` at the top (Eq. 5.2.1-3, Table 5.2.1).
    * T1 (``T``) is required: GB 50011 has no empirical period formula.
    """
    _check_common(model, direction)
    T_used = _pos("T", T)
    zeta = _pos("zeta", zeta)
    _choice("level", level, ("frequent", "rare"))
    if alpha_max is None:
        intensity = str(intensity)
        _choice("intensity", intensity, GB_ALPHA_MAX)
        alpha_max = GB_ALPHA_MAX[intensity][0 if level == "frequent" else 1]
    else:
        alpha_max = _pos("alpha_max", alpha_max)
    if Tg is None:
        _choice("site", site, GB_SITES)
        if isinstance(group, bool) or group not in GB_TG:
            raise ValueError("group must be 1, 2 or 3")
        Tg = GB_TG[group][GB_SITES.index(site)]
        if level == "rare":
            Tg += 0.05
    else:
        Tg = _pos("Tg", Tg)
    curve = gb_alpha(T_used, alpha_max, Tg, zeta)
    alpha1 = curve["alpha"]
    if geq_factor is None:
        geq_factor = 0.85 if len(model.stories) > 1 else 1.0
    _pos("geq_factor", geq_factor)
    w = story_weights(model, weights)
    GE = sum(w.values())
    Geq = geq_factor * GE
    FEk = alpha1 * Geq
    dn = gb_delta_n(T_used, Tg) if top_force else 0.0
    rows = _top_force_distribution(model, FEk, w, dn * FEk)
    return _summary("gb50011", direction, T_used, GE, FEk, rows,
                    alpha1=alpha1, alpha_max=alpha_max, Tg=Tg, Geq=Geq,
                    delta_n=dn, eta1=curve["eta1"], eta2=curve["eta2"],
                    gamma=curve["gamma"])


# ============================================================ TSC 2018
# TBDY 2018 Table 2.1 (FS) / Table 2.2 (F1): site -> values at
# SS = 0.25 .. 1.50 / S1 = 0.10 .. 0.60
TSC_SS = (0.25, 0.50, 0.75, 1.00, 1.25, 1.50)
TSC_S1 = (0.10, 0.20, 0.30, 0.40, 0.50, 0.60)
TSC_FS = {"ZA": (0.8,) * 6, "ZB": (0.9,) * 6,
          "ZC": (1.3, 1.3, 1.2, 1.2, 1.2, 1.2),
          "ZD": (1.6, 1.4, 1.2, 1.1, 1.0, 1.0),
          "ZE": (2.4, 1.7, 1.3, 1.1, 0.9, 0.8)}
TSC_F1 = {"ZA": (0.8,) * 6, "ZB": (0.8,) * 6,
          "ZC": (1.5, 1.5, 1.5, 1.5, 1.5, 1.4),
          "ZD": (2.4, 2.2, 2.0, 1.9, 1.8, 1.7),
          "ZE": (4.2, 3.3, 2.8, 2.4, 2.2, 2.0)}
# TBDY 2018 Eq. 4.31 empirical period coefficient Ct (HN in m)
TSC_CT = {"concrete_mrf": 0.1, "steel_mrf": 0.08, "ebf": 0.07,
          "other": 0.05}


def tsc_sae(T: float, SDS: float, SD1: float, TL: float = 6.0) -> float:
    """TBDY 2018 §2.3.4 horizontal elastic design spectrum Sae(T) (g)."""
    TA = 0.2 * SD1 / SDS
    TB = SD1 / SDS
    if T < TA:
        return (0.4 + 0.6 * T / TA) * SDS
    if T <= TB:
        return SDS
    if T <= TL:
        return SD1 / T
    return SD1 * TL / T ** 2


def compute_tsc2018(model, SS: float, S1: float, R: float, D: float,
                    I: float = 1.0, site: str = "ZC",
                    FS: Optional[float] = None, F1: Optional[float] = None,
                    T: Optional[float] = None,
                    structure: str = "concrete_mrf", TL: float = 6.0,
                    weights=None, direction: str = "X") -> dict:
    """Turkish Building Earthquake Code (TBDY) 2018 §4.7 equivalent
    lateral load method.

    * ``SDS = SS FS``, ``SD1 = S1 F1`` (§2.3.3, Tables 2.1/2.2 linear
      between columns), elastic spectrum Sae §2.3.4 (TA = 0.2 SD1/SDS,
      TB = SD1/SDS, TL = 6 s).
    * ``Ra(T) = R/I`` (T > TB), ``D + (R/I - D) T/TB`` (T <= TB)
      (Eq. 4.1); ``SaR = Sae/Ra``.
    * ``VtE = W SaR(Tp) >= 0.04 W I SDS`` (Eq. 4.19).
    * ``dFN = 0.0075 N VtE`` at the top (Eq. 4.22, N = number of
      storeys) and ``F_i = (VtE - dFN) w_i H_i / sum w H`` (Eq. 4.23).
    * Tp: ``TpA = Ct HN^0.75`` (Eq. 4.31) unless given; a given Tp is
      capped at 1.4 TpA (§4.7.3.4).
    """
    hn = _check_common(model, direction)
    for lab, v in (("SS", SS), ("S1", S1), ("R", R), ("D", D), ("I", I),
                   ("TL", TL)):
        _pos(lab, v)
    _choice("site", site, TSC_FS)
    FS = _interp(SS, TSC_SS, TSC_FS[site]) if FS is None else _pos("FS", FS)
    F1 = _interp(S1, TSC_S1, TSC_F1[site]) if F1 is None else _pos("F1", F1)
    SDS, SD1 = SS * FS, S1 * F1
    TpA = _empirical_T(hn, structure, TSC_CT, "structure")
    T_used = TpA if T is None else min(_pos("T", T), 1.4 * TpA)
    Sae = tsc_sae(T_used, SDS, SD1, TL)
    TB = SD1 / SDS
    Ra = R / I if T_used > TB else D + (R / I - D) * T_used / TB
    SaR = Sae / Ra
    w = story_weights(model, weights)
    W = sum(w.values())
    V = max(W * SaR, 0.04 * W * I * SDS)
    dFN = 0.0075 * len(model.stories) * V
    rows = _top_force_distribution(model, V, w, dFN)
    return _summary("tsc2018", direction, T_used, W, V, rows, TpA=TpA,
                    SDS=SDS, SD1=SD1, FS=FS, F1=F1, Sae=Sae, Ra=Ra, SaR=SaR,
                    dFN=dFN)


# ============================================================== KDS 41
# KDS 41 17 00:2019 Table 7.2-2 (SI): structure -> (Ct, x)
KDS_CT = {"steel_mrf": (0.0724, 0.8), "concrete_mrf": (0.0466, 0.9),
          "ebf": (0.0731, 0.75), "other": (0.0488, 0.75)}


def compute_kds41(model, S: float, R: float, IE: float = 1.0,
                  Fa: float = 1.0, Fv: float = 1.0,
                  SDS: Optional[float] = None, SD1: Optional[float] = None,
                  T: Optional[float] = None, structure: str = "concrete_mrf",
                  weights=None, direction: str = "X") -> dict:
    """KDS 41 17 00:2019 (Korea) §7.2 equivalent static method.

    * ``SDS = S x 2.5 x Fa x 2/3``, ``SD1 = S x Fv x 2/3`` (§4.2.1.3; S =
      effective peak ground acceleration in g, zone factor x risk factor)
      unless given.
    * ``Cs = SD1/((R/IE) T)`` but ``<= SDS/(R/IE)`` and ``>= 0.01``
      (§7.2.1).
    * ``Ta = Ct hn^x`` (Table 7.2-2), a given T capped at ``Cu Ta``
      (Table 7.2-1, the ASCE 7 Cu values).
    * ``F_x = V w_x h_x^k / sum w h^k`` (§7.2.4).
    """
    hn = _check_common(model, direction)
    for lab, v in (("S", S), ("R", R), ("IE", IE), ("Fa", Fa), ("Fv", Fv)):
        _pos(lab, v)
    SDS = S * 2.5 * Fa * 2.0 / 3.0 if SDS is None else _pos("SDS", SDS)
    SD1 = S * Fv * 2.0 / 3.0 if SD1 is None else _pos("SD1", SD1)
    _choice("structure", structure, KDS_CT)
    Ct, x = KDS_CT[structure]
    Ta = Ct * hn ** x
    Cu = asce22_cu(SD1)
    T_used = Ta if T is None else min(_pos("T", T), Cu * Ta)
    RoIe = R / IE
    Cs = max(min(SD1 / (RoIe * T_used), SDS / RoIe), 0.01)
    w = story_weights(model, weights)
    W = sum(w.values())
    V = Cs * W
    k = _k_exponent(T_used)
    shape = {s.name: s.elevation ** k for s in model.stories}
    return _summary("kds41", direction, T_used, W, V,
                    _distribute(model, V, w, shape), k=k, Ta=Ta, Cu=Cu,
                    SDS=SDS, SD1=SD1, Cs=Cs)


# =========================================================== NSCP 2015
# NSCP 2015 Table 208-7 (Ca) / 208-8 (Cv): soil -> (zone 2, zone 4 / Na|Nv)
NSCP_CA = {"SA": (0.16, 0.32), "SB": (0.20, 0.40), "SC": (0.24, 0.40),
           "SD": (0.28, 0.44), "SE": (0.34, 0.44)}
NSCP_CV = {"SA": (0.16, 0.32), "SB": (0.20, 0.40), "SC": (0.32, 0.56),
           "SD": (0.40, 0.64), "SE": (0.64, 0.96)}
# §208.5.2.2 Method A Ct (SI, hn in m)
NSCP_CT = {"steel_mrf": 0.0853, "concrete_mrf": 0.0731, "ebf": 0.0731,
           "other": 0.0488}


def compute_nscp2015(model, R: float, Z: float = 0.4, soil: str = "SD",
                     I: float = 1.0, Na: float = 1.0, Nv: float = 1.0,
                     Ca: Optional[float] = None, Cv: Optional[float] = None,
                     T: Optional[float] = None,
                     structure: str = "concrete_mrf", weights=None,
                     direction: str = "X") -> dict:
    """NSCP 2015 (Philippines) §208.5 static lateral force procedure.

    * ``V = Cv I W / (R T)`` (Eq. 208-8), ``<= 2.5 Ca I W / R`` (208-9),
      ``>= 0.11 Ca I W`` (208-10) and, in zone 4, ``>= 0.8 Z Nv I W / R``
      (208-11).
    * Ca, Cv from Tables 208-7/208-8 (zone 4 values x Na / Nv) unless
      given.  ``Z`` = 0.2 (zone 2) or 0.4 (zone 4).
    * ``T = Ct hn^0.75`` (Method A, Eq. 208-12; Ct 0.0853 steel MRF,
      0.0731 RC MRF / EBF, 0.0488 other); a given (Method B) T is capped at
      1.30 TA (zone 4) / 1.40 TA (zone 2) (§208.5.2.2).
    * ``Ft = 0.07 T V <= 0.25 V`` at the top, 0 for T <= 0.7 s (Eq.
      208-15); ``F_x = (V - Ft) w_x h_x / sum w h`` (Eq. 208-16).
    """
    hn = _check_common(model, direction)
    for lab, v in (("R", R), ("I", I), ("Na", Na), ("Nv", Nv)):
        _pos(lab, v)
    if Z not in (0.2, 0.4) or isinstance(Z, bool):
        raise ValueError("Z must be 0.2 (zone 2) or 0.4 (zone 4)")
    _choice("soil", soil, NSCP_CA)
    zi = 1 if Z == 0.4 else 0
    if Ca is None:
        Ca = NSCP_CA[soil][zi] * (Na if zi else 1.0)
    if Cv is None:
        Cv = NSCP_CV[soil][zi] * (Nv if zi else 1.0)
    _pos("Ca", Ca)
    _pos("Cv", Cv)
    TA = _empirical_T(hn, structure, NSCP_CT, "structure")
    T_used = TA if T is None else min(_pos("T", T),
                                      (1.30 if zi else 1.40) * TA)
    w = story_weights(model, weights)
    W = sum(w.values())
    V = Cv * I * W / (R * T_used)
    V = min(V, 2.5 * Ca * I * W / R)
    V = max(V, 0.11 * Ca * I * W)
    if zi:
        V = max(V, 0.8 * Z * Nv * I * W / R)
    Ft = 0.0 if T_used <= 0.7 else min(0.07 * T_used * V, 0.25 * V)
    rows = _top_force_distribution(model, V, w, Ft)
    return _summary("nscp2015", direction, T_used, W, V, rows, TA=TA,
                    Ca=Ca, Cv=Cv, Ft=Ft, Cs=V / W if W else 0.0)


# ============================================ Mexico City NTC-Sismo 2017
def ntc_spectrum(T: float, a0: float, c: float, Ta: float, Tb: float,
                 k: float, beta: float = 1.0) -> dict:
    """NTC-Sismo (CDMX) 2017 §3.1.1 elastic site spectrum a(T) (g) and
    p = k + (1 - k)(Tb/T)^2."""
    if T < Ta:
        return {"a": a0 + (beta * c - a0) * T / Ta, "p": None}
    if T < Tb:
        return {"a": beta * c, "p": None}
    p = k + (1.0 - k) * (Tb / T) ** 2
    return {"a": beta * c * p * (Tb / T) ** 2, "p": p}


def compute_ntc_cdmx(model, T: float, a0: float, c: float, Ta: float,
                     Tb: float, k: float = 1.0, Q: float = 2.0,
                     R0: float = 2.0, k1: float = 1.0, beta: float = 1.0,
                     weights=None, direction: str = "X") -> dict:
    """Mexico City NTC para Diseño por Sismo (2017/2020) static method,
    §7 with the §3 design spectrum.

    * a(T) per §3.1.1 from the SASID site parameters a0, c, Ta, Tb, k
      (beta = damping factor, 1 for 5 %).
    * Seismic behaviour reduction Q' (§3.4): ``1 + (Q-1) sqrt(beta/k)
      T/Ta`` (T <= Ta), ``1 + (Q-1) sqrt(beta/k)`` (Ta < T <= Tb),
      ``1 + (Q-1) sqrt(beta p/k)`` (T > Tb).
    * Overstrength R (§3.5): ``k1 R0 + 1 - sqrt(T/Ta)`` (T <= Ta), ``k1
      R0`` (T > Ta).
    * ``V = a/(Q' R) W`` and ``F_i = V w_i h_i / sum w h`` (§7.2 static
      method distribution).  The §7.2.2 two-term (k1 h + k2 h^2) reduced
      distribution for T > Tb is NOT applied (documented limitation).
    """
    _check_common(model, direction)
    T_used = _pos("T", T)
    for lab, v in (("a0", a0), ("c", c), ("Ta", Ta), ("Tb", Tb), ("k", k),
                   ("Q", Q), ("R0", R0), ("k1", k1), ("beta", beta)):
        _pos(lab, v)
    if Tb < Ta:
        raise ValueError("Tb must be >= Ta")
    if Q < 1.0:
        raise ValueError("Q must be >= 1")
    sp = ntc_spectrum(T_used, a0, c, Ta, Tb, k, beta)
    a, p = sp["a"], sp["p"]
    if T_used <= Ta:
        Qp = 1.0 + (Q - 1.0) * math.sqrt(beta / k) * T_used / Ta
    elif T_used <= Tb:
        Qp = 1.0 + (Q - 1.0) * math.sqrt(beta / k)
    else:
        Qp = 1.0 + (Q - 1.0) * math.sqrt(beta * p / k)
    Rr = (k1 * R0 + 1.0 - math.sqrt(T_used / Ta)) if T_used <= Ta \
        else k1 * R0
    Cs = a / (Qp * Rr)
    w = story_weights(model, weights)
    W = sum(w.values())
    V = Cs * W
    shape = {s.name: s.elevation for s in model.stories}
    return _summary("ntc_cdmx", direction, T_used, W, V,
                    _distribute(model, V, w, shape), a=a, Qp=Qp, R=Rr,
                    Cs=Cs, **({"p": p} if p is not None else {}))


# ------------------------------------------------------------ registration
COMPUTE = {"nzs1170_5": compute_nzs1170_5, "as1170_4": compute_as1170_4,
           "gb50011": compute_gb50011, "tsc2018": compute_tsc2018,
           "kds41": compute_kds41, "nscp2015": compute_nscp2015,
           "ntc_cdmx": compute_ntc_cdmx}
DEFAULT_NAME = {"nzs1170_5": "NZS1170", "as1170_4": "AS1170",
                "gb50011": "GB50011", "tsc2018": "TSC2018",
                "kds41": "KDS41", "nscp2015": "NSCP2015",
                "ntc_cdmx": "NTCSISMO"}


# register with autolateral (no-op when autolateral already did it)
from . import autolateral as _al_reg  # noqa: E402
_al_reg._register_ext(__import__("sys").modules[__name__])
