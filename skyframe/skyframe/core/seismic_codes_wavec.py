"""Wave C auto seismic generators (CONTRACT "Wave C: regional seismic /
wind generators, code spectra, spectrum file import, USGS lookup and
regional material / steel libraries").

Registered into :mod:`skyframe.core.autolateral` (``CODES`` / ``compute`` /
``generate`` / ``POST /api/pattern/auto-lateral``) through
``autolateral._register_ext`` by a one-line hook at the end of that module.
Every generator returns the existing auto-lateral summary::

    {"code", "direction", "T", "W", "V", <coefficients>,
     "stories": [{"story", "h", "w", "F"}, ...]}

Codes:

* ``ec8_na`` - EN 1998-1 §4.3.3.2 lateral force method with a national
  annex parameter set (:data:`EC8_NA`: UK, Italy, Germany, France, Spain,
  Portugal, Greece, Romania).  The preset supplies S, TB, TC, TD (and the
  ground-type list, default spectrum type, beta and, for Portugal, the
  ag-dependent soil factor); every value can still be overridden.  The
  presets were entered from memory and are flagged in
  docs/REVIEW_FLAGS.md ("Wave C").
* ``nbcc2020`` - NBCC 2020 Article 4.1.8.11 equivalent static force
  procedure with the full Table 4.1.8.11 higher-mode factor Mv and base
  overturning reduction J (spectral ratio S(0.2)/S(5.0) and period
  interpolation per the Table notes), Table 4.1.8.4.-C design spectrum,
  the 4.1.8.11.(2) floors / cap, the 4.1.8.11.(3) periods and the
  4.1.8.11.(8) overturning moments.  Table text: NBCC 2020 Part 4 as
  reproduced in the Ontario 2024 Building Code Compendium (O. Reg.
  163/24), Division B 4.1.8.11 (identical provisions).
* ``ibc2006`` / ``ibc2009`` / ``ibc2012`` - legacy IBC equivalent lateral
  force (ASCE 7-05 / 7-05 + Supplement 2 / ASCE 7-10 §12.8) with the
  ASCE 7-05/7-10 Table 11.4-1/-2 site coefficients.
* ``ubc97`` - UBC 1997 §1630.2 static force procedure (Tables 16-Q/16-R).
* ``ntc2018`` - Italian NTC 2018 §7.3.3.2 linear static analysis with the
  §3.2.3 spectrum (ag, F0, Tc*, soil category, topography).
"""
from __future__ import annotations

import math
from typing import Dict, Optional

from .autolateral import (EC8_SPECTRA, _check_common, _distribute,
                          _k_exponent, _pos, _summary, asce22_cu,
                          compute_ec8, story_weights)


def _choice(label, v, allowed):
    if v not in allowed:
        raise ValueError(f"{label} must be one of {tuple(allowed)}")
    return v


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


def _top_force(model, V: float, w: Dict[str, float], Ft: float) -> list:
    """``F_x = (V - Ft) w_x h_x / sum w h`` plus ``Ft`` at the top."""
    shape = {s.name: s.elevation for s in model.stories}
    rows = _distribute(model, V - Ft, w, shape)
    top = max(model.stories, key=lambda s: s.elevation).name
    for r in rows:
        if r["story"] == top:
            r["F"] += Ft
    return rows


# ====================================================== EC8 national annexes
# Each annex: label, the ground types it uses, the default spectrum type(s)
# and {spectrum_type: {ground: (S, TB, TC, TD)}}, plus beta and notes.
# FROM MEMORY - see docs/REVIEW_FLAGS.md "Wave C" before relying on them.
_REC = {t: dict(v) for t, v in EC8_SPECTRA.items()}     # EN 1998-1 Tables 3.2/3.3
EC8_NA: Dict[str, dict] = {
    "UK": {
        "label": "United Kingdom (NA to BS EN 1998-1:2004)",
        "spectra": {1: _REC[1], 2: _REC[2]}, "default_type": 1,
        "beta": 0.2, "importance": {"I": 0.8, "II": 1.0, "III": 1.2,
                                    "IV": 1.4},
        "note": "UK NA adopts the recommended S/TB/TC/TD values; no national "
                "agR map (low seismicity, PD 6698).",
    },
    "IT": {
        "label": "Italy (UNI EN 1998-1 NA, DM 31/07/2012)",
        "spectra": {1: _REC[1], 2: _REC[2]}, "default_type": 1,
        "beta": 0.2, "importance": {"I": 0.8, "II": 1.0, "III": 1.2,
                                    "IV": 1.4},
        "note": "Italian NA keeps the recommended spectral shapes; site "
                "hazard (ag, F0, Tc*) is the NTC grid - use the 'ntc2018' "
                "generator for the national spectrum.",
    },
    "DE": {
        "label": "Germany (DIN EN 1998-1/NA:2011, DIN 4149 subsoil "
                 "classes)",
        "spectra": {1: {"A-R": (1.00, 0.05, 0.20, 2.0),
                        "B-R": (1.25, 0.05, 0.25, 2.0),
                        "C-R": (1.50, 0.05, 0.30, 2.0),
                        "B-T": (1.00, 0.10, 0.30, 2.0),
                        "C-T": (1.25, 0.10, 0.40, 2.0),
                        "C-S": (0.75, 0.10, 0.50, 2.0)}},
        "default_type": 1, "beta": 0.2,
        "importance": {"I": 0.8, "II": 1.0, "III": 1.2, "IV": 1.4},
        "note": "Combined ground / geological subsoil classes (A-C x R, T, "
                "S) with one spectrum type.",
    },
    "FR": {
        "label": "France (NF EN 1998-1/NA, arrete du 22 octobre 2010)",
        "spectra": {1: _REC[1],
                    2: {"A": (1.00, 0.03, 0.20, 2.5),
                        "B": (1.35, 0.05, 0.25, 2.5),
                        "C": (1.50, 0.06, 0.40, 2.0),
                        "D": (1.60, 0.10, 0.60, 1.5),
                        "E": (1.80, 0.08, 0.45, 1.25)}},
        "default_type": 2, "beta": 0.2,
        "importance": {"I": 0.8, "II": 1.0, "III": 1.2, "IV": 1.4},
        "zones_ms2": {"1": 0.4, "2": 0.7, "3": 1.1, "4": 1.6, "5": 3.0},
        "note": "Zones 1-4 (mainland) use the national Type 2 table; zone 5 "
                "(Antilles) uses Type 1.  agR by zone in m/s^2.",
    },
    "ES": {
        "label": "Spain (UNE EN 1998-1 NA)",
        "spectra": {1: _REC[1], 2: _REC[2]}, "default_type": 1,
        "beta": 0.2, "importance": {"I": 0.8, "II": 1.0, "III": 1.2,
                                    "IV": 1.4},
        "note": "Spanish NA values not verified: recommended values are "
                "stored as a placeholder (NCSE-02 governs in Spain).",
    },
    "PT": {
        "label": "Portugal (NP EN 1998-1:2010 NA)",
        "spectra": {1: {"A": (1.00, 0.10, 0.60, 2.0),
                        "B": (1.35, 0.10, 0.60, 2.0),
                        "C": (1.60, 0.10, 0.60, 2.0),
                        "D": (2.00, 0.10, 0.80, 2.0),
                        "E": (1.80, 0.10, 0.60, 2.0)},
                    2: {"A": (1.00, 0.10, 0.25, 2.0),
                        "B": (1.35, 0.10, 0.25, 2.0),
                        "C": (1.60, 0.10, 0.25, 2.0),
                        "D": (2.00, 0.10, 0.30, 2.0),
                        "E": (1.80, 0.10, 0.25, 2.0)}},
        "default_type": 1, "beta": 0.2, "S_rule": "pt",
        "importance": {"I": 0.65, "II": 1.0, "III": 1.45, "IV": 1.95},
        "note": "S tabulated as Smax: S = Smax (ag <= 1 m/s^2), Smax - "
                "(Smax - 1)(ag - 1)/3 (1 < ag < 4), 1.0 (ag >= 4).",
    },
    "GR": {
        "label": "Greece (ELOT EN 1998-1 NA)",
        "spectra": {1: _REC[1]}, "default_type": 1, "beta": 0.2,
        "importance": {"I": 0.8, "II": 1.0, "III": 1.2, "IV": 1.4},
        "zones_g": {"I": 0.16, "II": 0.24, "III": 0.36},
        "note": "Recommended Type 1 shapes; agR by seismic zone I-III.",
    },
    "RO": {
        "label": "Romania (SR EN 1998-1/NA, P100-1/2013 control periods)",
        "spectra": {1: {"TC=0.7": (1.0, 0.14, 0.7, 3.0),
                        "TC=1.0": (1.0, 0.20, 1.0, 3.0),
                        "TC=1.6": (1.0, 0.32, 1.6, 2.0)}},
        "default_type": 1, "beta": 0.2,
        "importance": {"I": 1.4, "II": 1.2, "III": 1.0, "IV": 0.8},
        "note": "Ground 'types' are the P100-1 control-period zones; S = 1 "
                "(P100 has no soil factor).  P100 importance classes run "
                "I = highest.",
    },
}


def ec8_na_parameters(annex: str, ground_type: str,
                      spectrum_type: Optional[int] = None,
                      ag: Optional[float] = None) -> dict:
    """Resolved ``{annex, spectrum_type, ground_type, S, TB, TC, TD, beta}``
    of one national annex (``ag`` in g, used by the Portuguese S rule)."""
    _choice("annex", annex, EC8_NA)
    na = EC8_NA[annex]
    if isinstance(spectrum_type, str) and spectrum_type.strip() in ("1", "2"):
        spectrum_type = int(spectrum_type)         # dialog select values
    st = na["default_type"] if spectrum_type in (None, "", "na") \
        else spectrum_type
    if isinstance(st, bool) or st not in na["spectra"]:
        raise ValueError(f"spectrum_type must be one of "
                         f"{tuple(na['spectra'])} for annex {annex}")
    _choice("ground_type", ground_type, na["spectra"][st])
    S, TB, TC, TD = na["spectra"][st][ground_type]
    if na.get("S_rule") == "pt" and ag is not None:
        ag_ms2 = ag * 9.80665
        if ag_ms2 >= 4.0:
            S = 1.0
        elif ag_ms2 > 1.0:
            S = S - (S - 1.0) * (ag_ms2 - 1.0) / 3.0
    return {"annex": annex, "spectrum_type": st, "ground_type": ground_type,
            "S": S, "TB": TB, "TC": TC, "TD": TD, "beta": na["beta"]}


def ec8_na_catalogue() -> list:
    """JSON-safe list of the presets (dialogs / GET /api/codes/ec8-annexes)."""
    out = []
    for k, na in EC8_NA.items():
        out.append({
            "annex": k, "label": na["label"], "note": na["note"],
            "default_type": na["default_type"], "beta": na["beta"],
            "S_rule": na.get("S_rule"),
            "importance": dict(na["importance"]),
            "zones_ms2": dict(na.get("zones_ms2", {})),
            "zones_g": dict(na.get("zones_g", {})),
            "spectra": {str(t): {g: list(v) for g, v in tab.items()}
                        for t, tab in na["spectra"].items()},
            "from_memory": True})
    return out


def compute_ec8_na(model, annex: str, ag: float, q: float,
                   ground_type: str = "B",
                   spectrum_type: Optional[int] = None,
                   gamma_I: float = 1.0, T1: Optional[float] = None,
                   structure: str = "other", distribution: str = "height",
                   mode_shape: Optional[Dict[str, float]] = None,
                   S: Optional[float] = None, TB: Optional[float] = None,
                   TC: Optional[float] = None, TD: Optional[float] = None,
                   beta: Optional[float] = None, weights=None,
                   direction: str = "X") -> dict:
    """EN 1998-1 §4.3.3.2 with a national-annex preset (``ag`` = agR in g).
    ``Fb = Sd(T1) m lambda`` (Eq. 4.5) and ``F_i = Fb z_i m_i / sum z m``
    (Eq. 4.11) exactly as :func:`autolateral.compute_ec8`, whose
    S/TB/TC/TD/beta come from :func:`ec8_na_parameters` unless given."""
    _pos("ag", ag)
    _pos("gamma_I", gamma_I)
    p = ec8_na_parameters(annex, ground_type, spectrum_type,
                          ag=gamma_I * ag)
    out = compute_ec8(
        model, ag, q, ground_type="A", spectrum_type=1, gamma_I=gamma_I,
        T1=T1, structure=structure, distribution=distribution,
        mode_shape=mode_shape,
        S=p["S"] if S is None else S, TB=p["TB"] if TB is None else TB,
        TC=p["TC"] if TC is None else TC, TD=p["TD"] if TD is None else TD,
        beta=p["beta"] if beta is None else _pos("beta", beta,
                                                 allow_zero=True),
        weights=weights, direction=direction)
    out["code"] = "ec8_na"
    stories = out.pop("stories")
    out.update({"annex": annex, "ground_type": ground_type,
                "spectrum_type": p["spectrum_type"], "beta": (
                    p["beta"] if beta is None else float(beta)),
                "preset_from_memory": True, "stories": stories})
    return out


# =================================================================== NBCC 2020
# Table 4.1.8.11: system -> spectral ratio S(0.2)/S(5.0) row -> (Mv at Ta
# <= 0.5, 1.0, 2.0, >= 5.0 ; J at Ta <= 0.5, 1.0, 2.0, >= 5.0).  None = the
# 5.0 s column does not apply (Note (5): use the 2.0 s value).
NBCC_RATIOS = (5.0, 20.0, 40.0, 70.0)
NBCC_TA = (0.5, 1.0, 2.0, 5.0)
NBCC_MVJ = {
    "mrf": ((1, 1, 1, None, 1, 1, 0.95, None),
            (1, 1, 1, None, 1, 0.97, 0.88, None),
            (1, 1, 1, None, 1, 0.90, 0.79, None),
            (1, 1, 1, None, 0.98, 0.88, 0.70, None)),
    "coupled_walls": ((1, 1, 1, 1, 1, 1, 0.95, 0.80),
                      (1, 1, 1, 1.09, 1, 0.97, 0.88, 0.66),
                      (1, 1, 1, 1.33, 1, 0.90, 0.79, 0.52),
                      (1, 1, 1, 1.90, 0.98, 0.88, 0.70, 0.40)),
    "braced": ((1, 1, 1, None, 1, 0.98, 0.93, None),
               (1, 1, 1, None, 1, 0.91, 0.80, None),
               (1, 1, 1, None, 0.91, 0.82, 0.72, None),
               (1, 1, 1.19, None, 0.91, 0.77, 0.61, None)),
    "walls": ((1, 1, 1, 1.30, 1, 1, 0.85, 0.59),
              (1, 1, 1.18, 2.50, 1, 0.80, 0.60, 0.35),
              (1, 1.25, 1.85, 4.10, 0.80, 0.59, 0.42, 0.23),
              (1, 1.25, 2.30, 6.40, 0.80, 0.56, 0.30, 0.18)),
    "other": ((1, 1, 1, None, 1, 1, 0.85, None),
              (1, 1, 1.18, None, 1, 0.80, 0.60, None),
              (1, 1.25, 1.85, None, 0.80, 0.59, 0.44, None),
              (1, 1.37, 2.30, None, 0.80, 0.56, 0.30, None)),
}
#: wall-type systems (4.1.8.11.(2)(a): S(4.0) floor, 5.0 s column)
NBCC_WALL_SYSTEMS = ("walls", "coupled_walls")
#: 4.1.8.11.(3) period rule per system: (formula, cap factor on it)
NBCC_PERIOD = {"steel_mf": ("0.085hn^0.75", 1.5),
               "concrete_mf": ("0.075hn^0.75", 1.5),
               "other_mf": ("0.1N", 1.5),
               "braced": ("0.025hn", 2.0),
               "walls": ("0.05hn^0.75", 2.0),
               "other": ("0.05hn^0.75", 1.0)}
NBCC_ORD = (0.2, 0.5, 1.0, 2.0, 5.0, 10.0)


def nbcc_design_s(T: float, Sa: Dict[float, float],
                  interp: str = "linear") -> float:
    """NBCC 2020 Table 4.1.8.4.-C design S(T): ``S(0.2) = max(Sa(0.2),
    Sa(0.5))`` for T <= 0.2 s, the site ordinates at 0.5/1/2/5/10 s and
    linear or log-log interpolation between them (4.1.8.4.(6)); S(10) is
    held beyond 10 s."""
    pts = [(0.2, max(Sa[0.2], Sa[0.5]))] + [(t, Sa[t]) for t in NBCC_ORD[1:]]
    if T <= 0.2:
        return pts[0][1]
    if T >= 10.0:
        return pts[-1][1]
    for (t0, s0), (t1, s1) in zip(pts, pts[1:]):
        if t0 <= T <= t1:
            if interp == "loglog" and s0 > 0.0 and s1 > 0.0:
                f = math.log(T / t0) / math.log(t1 / t0)
                return math.exp(math.log(s0) + f * math.log(s1 / s0))
            return s0 + (s1 - s0) * (T - t0) / (t1 - t0)
    return pts[-1][1]  # pragma: no cover


def nbcc_row(system: str, ratio: float) -> tuple:
    """Table 4.1.8.11 row interpolated in the spectral ratio (Note (1):
    linear, ratio 0 -> 1.0, > 70 -> the 70 row)."""
    rows = NBCC_MVJ[system]
    xs = (0.0,) + NBCC_RATIOS
    out = []
    for j in range(8):
        col = [rows[i][j] for i in range(4)]
        if col[0] is None:
            out.append(None)
            continue
        out.append(_interp(min(max(ratio, 0.0), 70.0), xs, [1.0] + col))
    return tuple(out)


def nbcc_mv_j(system: str, ratio: float, Ta: float, S_fn,
              loglog: bool = False) -> dict:
    """Mv, J (Table 4.1.8.11 with Notes (1)-(3), (5), (7), (8)) and the
    product S(Ta) Mv; ``S_fn(T)`` is the design spectrum.

    * Mv / J at the column periods 0.5 / 1 / 2 / 5 s by Note (1).
    * MRF / braced / other: Ta > 2 s uses the 2.0 s values (Note (5)).
    * walls / coupled walls: Ta > 4 s uses the 4.0 s values interpolated
      between 2 and 5 s (Notes (7), (8)).
    * Note (2): the product S(Ta) Mv is interpolated linearly between the
      column periods when S(T) is interpolated linearly; with log-log
      S(T) (``loglog``) Mv is interpolated linearly and multiplies S(Ta).
      J is interpolated linearly (Note (3)).
    """
    row = nbcc_row(system, ratio)
    mv, jj = row[:4], row[4:]
    if system in NBCC_WALL_SYSTEMS:
        tmax, ts, mvs, js = 4.0, NBCC_TA, mv, jj
    else:
        tmax, ts, mvs, js = 2.0, NBCC_TA[:3], mv[:3], jj[:3]
    T = min(max(Ta, 0.5), tmax)
    Mv = _interp(T, ts, mvs)
    J = _interp(T, ts, js)
    if Ta <= 0.5:
        SMv = S_fn(Ta) * Mv
    elif loglog:
        SMv = S_fn(T) * Mv                       # Note (2), log-log case
    else:                                        # Note (2), linear case
        SMv = _interp(T, ts, [S_fn(t) * m for t, m in zip(ts, mvs)])
    return {"Mv": Mv, "J": J, "SMv": SMv, "T_used_for_table": T,
            "row": list(row)}


def nbcc_ta(system: str, hn: float, N: int) -> float:
    """4.1.8.11.(3) empirical period."""
    if system == "steel_mf":
        return 0.085 * hn ** 0.75
    if system == "concrete_mf":
        return 0.075 * hn ** 0.75
    if system == "other_mf":
        return 0.1 * N
    if system == "braced":
        return 0.025 * hn
    return 0.05 * hn ** 0.75


#: Table 4.1.8.11 system for each period rule
NBCC_TABLE_SYSTEM = {"steel_mf": "mrf", "concrete_mf": "mrf",
                     "other_mf": "mrf", "braced": "braced",
                     "walls": "walls", "other": "other"}


def compute_nbcc2020(model, Sa02: float, Sa05: float, Sa10: float,
                     Sa20: float, Sa50: float, Sa100: float, RdRo: float,
                     Rd: Optional[float] = None, IE: float = 1.0,
                     system: str = "concrete_mf",
                     table_system: Optional[str] = None,
                     T: Optional[float] = None, interp: str = "linear",
                     cap: bool = True, weights=None,
                     direction: str = "X") -> dict:
    """NBCC 2020 Article 4.1.8.11 equivalent static force procedure.

    * S(T): Table 4.1.8.4.-C from the six site ordinates (``Sa02`` ...
      ``Sa100`` = S(T, X) in g).
    * Ta: 4.1.8.11.(3) by ``system`` (steel_mf / concrete_mf / other_mf /
      braced / walls / other); a given ``T`` is capped at 1.5 / 2.0 / 2.0 /
      1.0 x the formula value (Clause (d)).
    * ``V = S(Ta) Mv IE W/(Rd Ro)`` (Sentence (2)) with Mv and J from
      Table 4.1.8.11 (``table_system``: mrf / coupled_walls / braced /
      walls / other; default from ``system``) at the spectral ratio
      S(0.2)/S(5.0); floors ``S(4.0) Mv IE W/(RdRo)`` (walls, coupled
      walls) or ``S(2.0) Mv IE W/(RdRo)`` (others), and (``cap`` with ``Rd
      >= 1.5``, Rd unknown = applied) the cap ``max(2/3 S(0.2), S(0.5))
      IE W/(RdRo)``.
    * Sentence (7): ``Ft = 0.07 Ta V <= 0.25 V`` (0 for Ta <= 0.7 s) and
      ``F_x = (V - Ft) W_x h_x / sum W h``.
    * Sentence (8): ``M_x = J_x sum F_i (h_i - h_x)`` with ``J_x = 1`` for
      h_x >= 0.6 hn else ``J + (1 - J) h_x/(0.6 hn)`` (rows carry ``J`` /
      ``M``; ``M0`` = base overturning moment).
    """
    hn = _check_common(model, direction)
    Sa = {}
    for t, lab, v in ((0.2, "Sa02", Sa02), (0.5, "Sa05", Sa05),
                      (1.0, "Sa10", Sa10), (2.0, "Sa20", Sa20),
                      (5.0, "Sa50", Sa50), (10.0, "Sa100", Sa100)):
        Sa[t] = _pos(lab, v, allow_zero=True)
    _pos("RdRo", RdRo)
    _pos("IE", IE)
    _choice("system", system, NBCC_PERIOD)
    _choice("interp", interp, ("linear", "loglog"))
    tsys = NBCC_TABLE_SYSTEM[system] if table_system is None else \
        _choice("table_system", table_system, NBCC_MVJ)
    N = len(model.stories)
    Ta_formula = nbcc_ta(system, hn, N)
    T_used = Ta_formula if T is None else min(
        _pos("T", T), NBCC_PERIOD[system][1] * Ta_formula)

    def S_fn(t):
        return nbcc_design_s(t, Sa, interp)

    S02 = S_fn(0.2)
    ratio = S02 / Sa[5.0] if Sa[5.0] > 0.0 else 70.0
    ll = interp == "loglog"
    mj = nbcc_mv_j(tsys, ratio, T_used, S_fn, ll)
    w = story_weights(model, weights)
    W = sum(w.values())
    V = mj["SMv"] * IE * W / RdRo
    wall = tsys in NBCC_WALL_SYSTEMS
    t_floor = 4.0 if wall else 2.0
    floor_mj = nbcc_mv_j(tsys, ratio, t_floor, S_fn, ll)
    V_min = floor_mj["SMv"] * IE * W / RdRo
    V_max = max(2.0 / 3.0 * S02, S_fn(0.5)) * IE * W / RdRo
    apply_cap = bool(cap) and (Rd is None or _pos("Rd", Rd) >= 1.5)
    V = max(V, V_min)
    if apply_cap:
        V = min(V, V_max)
    Ft = 0.0 if T_used <= 0.7 else min(0.07 * T_used * V, 0.25 * V)
    rows = _top_force(model, V, w, Ft)
    J = mj["J"]
    for r in rows:
        hx = r["h"]
        Jx = 1.0 if hx >= 0.6 * hn else J + (1.0 - J) * hx / (0.6 * hn)
        M = sum(o["F"] * (o["h"] - hx) for o in rows if o["h"] > hx)
        r["J"] = Jx
        r["M"] = Jx * M
    M0 = J * sum(o["F"] * o["h"] for o in rows)
    return _summary("nbcc2020", direction, T_used, W, V, rows,
                    Ta_formula=Ta_formula, S_Ta=S_fn(T_used), S02=S02,
                    ratio=ratio, Mv=mj["Mv"], J=J, SMv=mj["SMv"],
                    V_min=V_min, V_max=V_max, cap_applied=apply_cap,
                    Ft=Ft, M0=M0, table_system=tsys)


# ================================================== legacy IBC (ASCE 7-05/10)
# ASCE 7-05 / 7-10 Table 11.4-1 (Fa at Ss = 0.25..1.25) / 11.4-2 (Fv at
# S1 = 0.1..0.5)
IBC_SS = (0.25, 0.50, 0.75, 1.00, 1.25)
IBC_S1 = (0.10, 0.20, 0.30, 0.40, 0.50)
IBC_FA = {"A": (0.8,) * 5, "B": (1.0,) * 5,
          "C": (1.2, 1.2, 1.1, 1.0, 1.0), "D": (1.6, 1.4, 1.2, 1.1, 1.0),
          "E": (2.5, 1.7, 1.2, 0.9, 0.9)}
IBC_FV = {"A": (0.8,) * 5, "B": (1.0,) * 5,
          "C": (1.7, 1.6, 1.5, 1.4, 1.3), "D": (2.4, 2.0, 1.8, 1.6, 1.5),
          "E": (3.5, 3.2, 2.8, 2.4, 2.4)}
# Table 12.8-2 (SI, hn in m): structure -> (Ct, x)
IBC_CT = {"steel_mrf": (0.0724, 0.8), "concrete_mrf": (0.0466, 0.9),
          "ebf": (0.0731, 0.75), "other": (0.0488, 0.75)}
IBC_EDITIONS = {"ibc2006": "ASCE 7-05", "ibc2009": "ASCE 7-05 + Suppl. 2",
                "ibc2012": "ASCE 7-10"}


def ibc_site_coefficients(Ss: float, S1: float, site: str) -> tuple:
    """(Fa, Fv) from ASCE 7-05/7-10 Tables 11.4-1/2 (linear between
    columns; site class F needs a site response analysis)."""
    _choice("site", site, IBC_FA)
    return (_interp(Ss, IBC_SS, IBC_FA[site]),
            _interp(S1, IBC_S1, IBC_FV[site]))


def _compute_ibc(edition: str, model, Ss: float, S1: float, R: float,
                 Ie: float = 1.0, site: str = "D", TL: float = 8.0,
                 Fa: Optional[float] = None, Fv: Optional[float] = None,
                 T: Optional[float] = None, structure: str = "concrete_mrf",
                 weights=None, direction: str = "X") -> dict:
    hn = _check_common(model, direction)
    for lab, v in (("Ss", Ss), ("S1", S1), ("R", R), ("Ie", Ie),
                   ("TL", TL)):
        _pos(lab, v, allow_zero=lab in ("Ss", "S1"))
    Fa0, Fv0 = ibc_site_coefficients(Ss, S1, site)
    Fa = Fa0 if Fa is None else _pos("Fa", Fa)
    Fv = Fv0 if Fv is None else _pos("Fv", Fv)
    SMS, SM1 = Fa * Ss, Fv * S1
    SDS, SD1 = 2.0 / 3.0 * SMS, 2.0 / 3.0 * SM1
    _choice("structure", structure, IBC_CT)
    Ct, x = IBC_CT[structure]
    Ta = Ct * hn ** x
    Cu = asce22_cu(SD1)
    T_used = Ta if T is None else min(_pos("T", T), Cu * Ta)
    RI = R / Ie
    Cs = SDS / RI
    Cs_max = (SD1 / (T_used * RI) if T_used <= TL
              else SD1 * TL / (T_used ** 2 * RI))
    if edition == "ibc2006":
        Cs_min = 0.01                         # ASCE 7-05 Eq. 12.8-5
    else:
        Cs_min = max(0.044 * SDS * Ie, 0.01)  # 7-05 Suppl. 2 / 7-10
    if S1 >= 0.6:
        Cs_min = max(Cs_min, 0.5 * S1 / RI)   # Eq. 12.8-6
    Cs = max(min(Cs, Cs_max), Cs_min)
    w = story_weights(model, weights)
    W = sum(w.values())
    V = Cs * W
    k = _k_exponent(T_used)
    shape = {s.name: s.elevation ** k for s in model.stories}
    return _summary(edition, direction, T_used, W, V,
                    _distribute(model, V, w, shape), k=k, Ta=Ta, Cu=Cu,
                    Fa=Fa, Fv=Fv, SDS=SDS, SD1=SD1, Cs=Cs, Cs_max=Cs_max,
                    Cs_min=Cs_min, standard=IBC_EDITIONS[edition])


_IBC_DOC = """{ed}: ``SDS = 2/3 Fa Ss``, ``SD1 = 2/3 Fv S1``
    (§11.4.3-4, Fa/Fv Tables 11.4-1/2 unless given), ``Cs = SDS/(R/Ie)``
    ``<= SD1/(T R/Ie)`` (T <= TL) or ``SD1 TL/(T^2 R/Ie)``, ``>= {cmin}``
    and ``>= 0.5 S1/(R/Ie)`` for S1 >= 0.6 g; ``Ta = Ct hn^x`` (Table
    12.8-2), a given T capped at Cu Ta (Table 12.8-1); ``F_x = V w_x
    h_x^k / sum w h^k`` (§12.8.3)."""


def compute_ibc2006(model, Ss: float, S1: float, R: float, Ie: float = 1.0,
                    site: str = "D", TL: float = 8.0,
                    Fa: Optional[float] = None, Fv: Optional[float] = None,
                    T: Optional[float] = None,
                    structure: str = "concrete_mrf", weights=None,
                    direction: str = "X") -> dict:
    return _compute_ibc("ibc2006", model, Ss, S1, R, Ie, site, TL, Fa, Fv,
                        T, structure, weights, direction)


def compute_ibc2009(model, Ss: float, S1: float, R: float, Ie: float = 1.0,
                    site: str = "D", TL: float = 8.0,
                    Fa: Optional[float] = None, Fv: Optional[float] = None,
                    T: Optional[float] = None,
                    structure: str = "concrete_mrf", weights=None,
                    direction: str = "X") -> dict:
    return _compute_ibc("ibc2009", model, Ss, S1, R, Ie, site, TL, Fa, Fv,
                        T, structure, weights, direction)


def compute_ibc2012(model, Ss: float, S1: float, R: float, Ie: float = 1.0,
                    site: str = "D", TL: float = 8.0,
                    Fa: Optional[float] = None, Fv: Optional[float] = None,
                    T: Optional[float] = None,
                    structure: str = "concrete_mrf", weights=None,
                    direction: str = "X") -> dict:
    return _compute_ibc("ibc2012", model, Ss, S1, R, Ie, site, TL, Fa, Fv,
                        T, structure, weights, direction)


compute_ibc2006.__doc__ = _IBC_DOC.format(
    ed="IBC 2006 = ASCE 7-05 §12.8 ELF", cmin="0.01 (Eq. 12.8-5 as first "
    "published)")
compute_ibc2009.__doc__ = _IBC_DOC.format(
    ed="IBC 2009 = ASCE 7-05 + Supplement No. 2 §12.8 ELF",
    cmin="max(0.044 SDS Ie, 0.01)")
compute_ibc2012.__doc__ = _IBC_DOC.format(
    ed="IBC 2012 = ASCE 7-10 §12.8 ELF", cmin="max(0.044 SDS Ie, 0.01)")


# ======================================================================= UBC 97
UBC_Z = (0.075, 0.15, 0.2, 0.3, 0.4)
# Table 16-Q (Ca) / 16-R (Cv): soil -> values at Z = 0.075 .. 0.4 (zone 4
# value x Na / Nv)
UBC_CA = {"SA": (0.06, 0.12, 0.16, 0.24, 0.32),
          "SB": (0.08, 0.15, 0.20, 0.30, 0.40),
          "SC": (0.09, 0.18, 0.24, 0.33, 0.40),
          "SD": (0.12, 0.22, 0.28, 0.36, 0.44),
          "SE": (0.19, 0.30, 0.34, 0.36, 0.36)}
UBC_CV = {"SA": (0.06, 0.12, 0.16, 0.24, 0.32),
          "SB": (0.08, 0.15, 0.20, 0.30, 0.40),
          "SC": (0.13, 0.25, 0.32, 0.45, 0.56),
          "SD": (0.18, 0.32, 0.40, 0.54, 0.64),
          "SE": (0.26, 0.50, 0.64, 0.84, 0.96)}
# §1630.2.2 Method A Ct (SI, hn in m)
UBC_CT = {"steel_mrf": 0.0853, "concrete_mrf": 0.0731, "ebf": 0.0731,
          "other": 0.0488}


def compute_ubc97(model, R: float, Z: float = 0.4, soil: str = "SD",
                  I: float = 1.0, Na: float = 1.0, Nv: float = 1.0,
                  Ca: Optional[float] = None, Cv: Optional[float] = None,
                  T: Optional[float] = None, structure: str = "concrete_mrf",
                  weights=None, direction: str = "X") -> dict:
    """UBC 1997 §1630.2 static force procedure.

    * ``V = Cv I W/(R T)`` (30-4) ``<= 2.5 Ca I W/R`` (30-5), ``>= 0.11 Ca
      I W`` (30-6) and, in zone 4, ``>= 0.8 Z Nv I W/R`` (30-7).
    * Ca / Cv: Tables 16-Q / 16-R by soil profile SA-SE and zone factor Z
      (0.075, 0.15, 0.2, 0.3, 0.4; zone 4 values x Na / Nv) unless given.
    * ``T = Ct hn^0.75`` (Method A, 30-8); a given (Method B) T is capped
      at 1.30 T_A (zone 4) / 1.40 T_A (zones 1-3).
    * ``Ft = 0.07 T V <= 0.25 V``, 0 for T <= 0.7 s (30-14); ``F_x = (V -
      Ft) w_x h_x / sum w h`` (30-15).
    """
    hn = _check_common(model, direction)
    for lab, v in (("R", R), ("I", I), ("Na", Na), ("Nv", Nv)):
        _pos(lab, v)
    if isinstance(Z, bool) or Z not in UBC_Z:
        raise ValueError(f"Z must be one of {UBC_Z}")
    _choice("soil", soil, UBC_CA)
    zi = UBC_Z.index(Z)
    z4 = Z == 0.4
    if Ca is None:
        Ca = UBC_CA[soil][zi] * (Na if z4 else 1.0)
    if Cv is None:
        Cv = UBC_CV[soil][zi] * (Nv if z4 else 1.0)
    _pos("Ca", Ca)
    _pos("Cv", Cv)
    _choice("structure", structure, UBC_CT)
    TA = UBC_CT[structure] * hn ** 0.75
    T_used = TA if T is None else min(_pos("T", T),
                                      (1.30 if z4 else 1.40) * TA)
    w = story_weights(model, weights)
    W = sum(w.values())
    V = Cv * I * W / (R * T_used)
    V = min(V, 2.5 * Ca * I * W / R)
    V = max(V, 0.11 * Ca * I * W)
    if z4:
        V = max(V, 0.8 * Z * Nv * I * W / R)
    Ft = 0.0 if T_used <= 0.7 else min(0.07 * T_used * V, 0.25 * V)
    rows = _top_force(model, V, w, Ft)
    return _summary("ubc97", direction, T_used, W, V, rows, TA=TA, Ca=Ca,
                    Cv=Cv, Ft=Ft, Cs=V / W if W else 0.0)


# ============================================================ Italian NTC 2018
# Table 3.2.IV: soil -> (SS a, SS b, SS min, SS max, CC coef, CC exponent)
# SS = a - b F0 ag/g within [min, max]; CC = coef (Tc*)^exponent
NTC18_SOIL = {"A": (1.0, 0.0, 1.0, 1.0, 1.0, 0.0),
              "B": (1.40, 0.40, 1.00, 1.20, 1.10, -0.20),
              "C": (1.70, 0.60, 1.00, 1.50, 1.05, -0.33),
              "D": (2.40, 1.50, 0.90, 1.80, 1.25, -0.50),
              "E": (2.00, 1.10, 1.00, 1.60, 1.15, -0.40)}
# Table 3.2.V topography ST
NTC18_TOPO = {"T1": 1.0, "T2": 1.2, "T3": 1.2, "T4": 1.4}
# §7.3.3.2 Eq. 7.3.6: T1 = C1 H^(3/4)
NTC18_C1 = {"steel_mrf": 0.085, "concrete_mrf": 0.075, "other": 0.050}


def ntc18_parameters(ag: float, F0: float, Tc_star: float, soil: str = "B",
                     topography: str = "T1") -> dict:
    """NTC 2018 §3.2.3.2.1 spectrum parameters: S = SS ST, TC = CC Tc*,
    TB = TC/3, TD = 4.0 ag/g + 1.6 (ag in g)."""
    _choice("soil", soil, NTC18_SOIL)
    _choice("topography", topography, NTC18_TOPO)
    a, b, lo, hi, cc, ex = NTC18_SOIL[soil]
    SS = min(max(a - b * F0 * ag, lo), hi)
    CC = cc * Tc_star ** ex
    ST = NTC18_TOPO[topography]
    TC = CC * Tc_star
    return {"SS": SS, "CC": CC, "ST": ST, "S": SS * ST, "TC": TC,
            "TB": TC / 3.0, "TD": 4.0 * ag + 1.6}


def ntc18_spectrum(T: float, ag: float, F0: float, S: float, TB: float,
                   TC: float, TD: float, eta: float) -> float:
    """NTC 2018 Eq. 3.2.2 elastic (eta = sqrt(10/(5 + xi)) >= 0.55) or,
    with eta = 1/q, the §3.2.3.5 design spectrum (in g)."""
    if T < TB:
        return ag * S * eta * F0 * (T / TB + (1.0 - T / TB) / (eta * F0))
    if T < TC:
        return ag * S * eta * F0
    if T < TD:
        return ag * S * eta * F0 * TC / T
    return ag * S * eta * F0 * TC * TD / T ** 2


def compute_ntc2018(model, ag: float, F0: float, Tc_star: float,
                    q: float = 1.5, soil: str = "B", topography: str = "T1",
                    T1: Optional[float] = None, structure: str = "other",
                    weights=None, direction: str = "X") -> dict:
    """Italian NTC 2018 §7.3.3.2 linear static analysis.

    * Spectrum §3.2.3.2 with S, TB, TC, TD per :func:`ntc18_parameters`;
      design ordinate (§3.2.3.5) ``Sd = Se`` with eta = 1/q, and ``Sd >=
      0.2 ag``.
    * ``T1 = C1 H^(3/4)`` (Eq. 7.3.6; C1 0.085 steel MRF, 0.075 concrete
      MRF, 0.050 other) unless given.
    * ``Fh = Sd(T1) W lambda`` (Eq. 7.3.7), lambda = 0.85 for T1 < 2 TC and
      at least 3 storeys, else 1.0; ``F_i = Fh z_i W_i / sum z_j W_j``.
    """
    H = _check_common(model, direction)
    for lab, v in (("ag", ag), ("F0", F0), ("Tc_star", Tc_star),
                   ("q", q)):
        _pos(lab, v)
    p = ntc18_parameters(ag, F0, Tc_star, soil, topography)
    _choice("structure", structure, NTC18_C1)
    T_used = NTC18_C1[structure] * H ** 0.75 if T1 is None else \
        _pos("T1", T1)
    Sd = max(ntc18_spectrum(T_used, ag, F0, p["S"], p["TB"], p["TC"],
                            p["TD"], 1.0 / q), 0.2 * ag)
    lam = 0.85 if (T_used < 2.0 * p["TC"] and len(model.stories) >= 3) \
        else 1.0
    w = story_weights(model, weights)
    W = sum(w.values())
    Fh = Sd * W * lam
    shape = {s.name: s.elevation for s in model.stories}
    return _summary("ntc2018", direction, T_used, W, Fh,
                    _distribute(model, Fh, w, shape), Sd=Sd, lambda_=lam,
                    **p)


# ------------------------------------------------------------ registration
COMPUTE = {"ec8_na": compute_ec8_na, "nbcc2020": compute_nbcc2020,
           "ibc2006": compute_ibc2006, "ibc2009": compute_ibc2009,
           "ibc2012": compute_ibc2012, "ubc97": compute_ubc97,
           "ntc2018": compute_ntc2018}
DEFAULT_NAME = {"ec8_na": "EC8NA", "nbcc2020": "NBCC20",
                "ibc2006": "IBC06", "ibc2009": "IBC09", "ibc2012": "IBC12",
                "ubc97": "UBC97", "ntc2018": "NTC18"}


# register with autolateral (no-op when autolateral already did it)
from . import autolateral as _al_reg  # noqa: E402
_al_reg._register_ext(__import__("sys").modules[__name__])
