"""ASCE 7-16 seismic code helpers (v0.7).

Everything here is closed-form ASCE 7-16 (2016 edition):

* :func:`asce7_spectrum` — the design response spectrum (Section 11.4.6)
  built from mapped accelerations ``Ss``/``S1`` and a site class through the
  site-coefficient tables 11.4-1 (Fa) and 11.4-2 (Fv);
* :func:`make_rs_case_from_code` — a ready :class:`ResponseSpectrumCase`
  whose spectrum is scaled by ``Ie/R`` (the modal-response-spectrum design
  reduction, Section 12.9.1.1);
* :func:`asce7_combinations` / :func:`apply_asce7_combinations` — the
  strength (§2.3, LRFD) or allowable-stress (§2.4, ASD) load combinations,
  mapped onto the model's actual cases by pattern kind;
* :func:`asce7_elf` — the equivalent-lateral-force procedure (Section 12.8):
  approximate period, seismic response coefficient with its floors, base
  shear and the vertical force distribution.

Units follow the model everywhere: kN, m, tonne, s.
"""

from __future__ import annotations

import math
import warnings
from typing import Dict, List, Optional, Tuple

from .model import (BuildingModel, G_ACCEL, LoadPattern, ResponseSpectrumCase,
                    StoryForce)

# --------------------------------------------------------------------------- #
# ASCE 7-16 site-coefficient tables
# --------------------------------------------------------------------------- #
# Table 11.4-1 — Site coefficient Fa, columns at Ss = 0.25, 0.5, 0.75, 1.0,
# 1.25, >=1.5 (first column also used for Ss <= 0.25; linear interpolation
# between).  Values transcribed from ASCE 7-16.
_FA_SS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5)
_FA_TABLE: Dict[str, Tuple[float, ...]] = {
    "A": (0.8, 0.8, 0.8, 0.8, 0.8, 0.8),
    "B": (0.9, 0.9, 0.9, 0.9, 0.9, 0.9),
    "C": (1.3, 1.3, 1.2, 1.2, 1.2, 1.2),
    "D": (1.6, 1.4, 1.2, 1.1, 1.0, 1.0),
    # Site class E: ASCE 7-16 requires a site-specific study for Ss >= 1.0
    # (Table note, see Section 11.4.8).  The last tabulated value (1.3) is
    # carried flat for Ss >= 0.75 so the helper stays usable; a warning is
    # emitted when that clamp is actually reached.
    "E": (2.4, 1.7, 1.3, 1.3, 1.3, 1.3),
}
# Table 11.4-2 — Site coefficient Fv, columns at S1 = 0.1, 0.2, 0.3, 0.4,
# 0.5, >=0.6 (first column also used for S1 <= 0.1).
_FV_S1 = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
_FV_TABLE: Dict[str, Tuple[float, ...]] = {
    "A": (0.8, 0.8, 0.8, 0.8, 0.8, 0.8),
    "B": (0.8, 0.8, 0.8, 0.8, 0.8, 0.8),
    "C": (1.5, 1.5, 1.5, 1.5, 1.5, 1.4),
    "D": (2.4, 2.2, 2.0, 1.9, 1.8, 1.7),
    # Site class E: ASCE 7-16 requires a site-specific study for S1 >= 0.2
    # (Table note); tabulated values are used where given.
    "E": (4.2, 3.3, 2.8, 2.4, 2.2, 2.0),
}
SITE_CLASSES = tuple(_FA_TABLE.keys())


def _interp_table(x: float, xs: Tuple[float, ...],
                  ys: Tuple[float, ...]) -> float:
    """Piecewise-linear table lookup, clamped to the end columns."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return ys[-1]  # pragma: no cover - unreachable


def site_coefficients(Ss: float, S1: float,
                      site_class: str = "D") -> Tuple[float, float]:
    """(Fa, Fv) from ASCE 7-16 Tables 11.4-1 / 11.4-2 (interpolated)."""
    sc = str(site_class).upper()
    if sc not in _FA_TABLE:
        raise ValueError(f"site_class must be one of {SITE_CLASSES}, "
                         f"got {site_class!r}")
    if Ss < 0.0 or S1 < 0.0:
        raise ValueError("Ss and S1 must be >= 0")
    if sc == "E" and Ss >= 0.75:
        warnings.warn("ASCE 7-16 Site Class E requires a site-specific study "
                      "for high Ss; Fa is clamped to the last tabulated "
                      "value", UserWarning)
    fa = _interp_table(Ss, _FA_SS, _FA_TABLE[sc])
    fv = _interp_table(S1, _FV_S1, _FV_TABLE[sc])
    return fa, fv


# --------------------------------------------------------------------------- #
# Design response spectrum (Section 11.4.6)
# --------------------------------------------------------------------------- #
def spectrum_parameters(Ss: float, S1: float, site_class: str = "D"
                        ) -> Tuple[float, float, float, float, float, float]:
    """(SDS, SD1, SMS, SM1, T0, Ts) for the design spectrum.

    ``SMS = Fa*Ss``, ``SM1 = Fv*S1`` (MCE_R spectral accelerations, 11.4.4);
    ``SDS = 2/3 SMS``, ``SD1 = 2/3 SM1`` (design values, 11.4.5);
    ``T0 = 0.2 SD1/SDS``, ``Ts = SD1/SDS`` (spectrum corners, 11.4.6).
    """
    fa, fv = site_coefficients(Ss, S1, site_class)
    sms, sm1 = fa * Ss, fv * S1
    sds, sd1 = 2.0 / 3.0 * sms, 2.0 / 3.0 * sm1
    if sds <= 0.0:
        raise ValueError("SDS must be > 0 (need Ss > 0)")
    t0 = 0.2 * sd1 / sds
    ts = sd1 / sds
    return sds, sd1, sms, sm1, t0, ts


def spectrum_value(T: float, sds: float, sd1: float, t0: float, ts: float,
                   TL: float) -> float:
    """Design spectral acceleration Sa(T) in g (ASCE 7-16 Fig. 11.4-1).

    ``0 <= T < T0``   : ``SDS (0.4 + 0.6 T/T0)`` (ramp from 0.4 SDS);
    ``T0 <= T <= Ts`` : ``SDS`` (constant-acceleration plateau);
    ``Ts < T <= TL``  : ``SD1/T`` (constant-velocity branch);
    ``T > TL``        : ``SD1 TL/T^2`` (constant-displacement branch).
    """
    if T < 0.0:
        raise ValueError("period must be >= 0")
    if T <= t0:
        return sds * (0.4 + 0.6 * (T / t0 if t0 > 0 else 0.0))
    if T <= ts:
        return sds
    if T <= TL:
        return sd1 / T
    return sd1 * TL / (T * T)


def asce7_spectrum(Ss: float, S1: float, site_class: str = "D",
                   TL: float = 8.0) -> List[List[float]]:
    """ASCE 7-16 design response spectrum as ``[[T, Sa(g)], ...]``.

    The multilinear spectrum (see :func:`spectrum_value`) is sampled at a set
    of periods that includes the corner points EXACTLY (0, T0, Ts, TL) plus a
    dense sampling of the descending constant-velocity branch and a few
    points on the constant-displacement branch beyond ``TL`` — suitable for a
    :class:`ResponseSpectrumCase` (linear interpolation at modal periods).
    """
    if not (math.isfinite(TL) and TL > 0.0):
        raise ValueError("TL must be a finite value > 0")
    sds, sd1, _, _, t0, ts = spectrum_parameters(Ss, S1, site_class)

    pts = {0.0, t0, ts, 2.0 * ts, TL}
    n_desc = 12
    for i in range(1, n_desc + 1):                # Ts .. TL inclusive
        pts.add(ts + (TL - ts) * i / n_desc)
    for f in (1.25, 1.5, 2.0, 3.0, 4.0):          # beyond TL
        pts.add(TL * f)
    ordered = sorted(t for t in pts if t >= 0.0)
    return [[float(t), float(spectrum_value(t, sds, sd1, t0, ts, TL))]
            for t in ordered]


def make_rs_case_from_code(model: BuildingModel, name: str, direction: str,
                           Ss: float, S1: float, site_class: str = "D",
                           R: float = 8.0, Ie: float = 1.0, TL: float = 8.0,
                           num_modes: int = 0, combo_method: str = "CQC",
                           damping: float = 0.05) -> ResponseSpectrumCase:
    """Add an ASCE 7-16 code-spectrum :class:`ResponseSpectrumCase`.

    The elastic design spectrum (in g) from :func:`asce7_spectrum` is stored
    as the case spectrum; the design reduction ``Ie/R`` (ASCE 7-16 §12.9.1.1
    — modal base shear is scaled by the importance factor over the response
    modification coefficient) is carried on the case ``scale`` so the raw
    code spectrum stays inspectable.
    """
    if not (math.isfinite(R) and R > 0.0):
        raise ValueError("R must be a finite value > 0")
    if not (math.isfinite(Ie) and Ie > 0.0):
        raise ValueError("Ie must be a finite value > 0")
    spectrum = asce7_spectrum(Ss, S1, site_class, TL)
    return model.add_rs_case(name, direction, spectrum, num_modes=num_modes,
                             combo_method=combo_method, damping=damping,
                             scale=Ie / R)


# --------------------------------------------------------------------------- #
# Load combinations (ASCE 7-16 §2.3 LRFD / §2.4 ASD)
# --------------------------------------------------------------------------- #
LOAD_STANDARDS = ("LRFD", "ASD")


def _classify_cases(model: BuildingModel
                    ) -> Tuple[Optional[str], Optional[str],
                               List[str], List[str]]:
    """(dead, live, quakes, winds) case names classified by pattern kind.

    A case classifies only when it is a SINGLE-pattern case and that
    pattern's kind matches; the first dead/live case wins, all quake/wind
    cases are collected (so both directions get sign variants).
    """
    dead: Optional[str] = None
    live: Optional[str] = None
    quakes: List[str] = []
    winds: List[str] = []
    for cname, case in model.cases.items():
        if len(case.patterns) != 1:
            continue
        pname = next(iter(case.patterns))
        pat = model.patterns.get(pname)
        if pat is None:
            continue
        if pat.kind == "dead" and dead is None:
            dead = cname
        elif pat.kind == "live" and live is None:
            live = cname
        elif pat.kind == "quake":
            quakes.append(cname)
        elif pat.kind == "wind":
            winds.append(cname)
    return dead, live, quakes, winds


_SIGNS = ((1.0, "+"), (-1.0, "-"))


def asce7_combinations(model: BuildingModel, standard: str = "LRFD"
                       ) -> Dict[str, Dict[str, float]]:
    """Generate the ASCE 7-16 strength (LRFD) or ASD load combinations.

    Cases are matched by pattern kind (dead / live / quake / wind).  Seismic
    and wind combinations get both sign variants (``+E``/``-E``).  A combo
    term whose kind has no matching case is dropped and a ``UserWarning`` is
    emitted (e.g. a model without a LIVE case omits every live term).
    Returns ``{combo name -> {case name -> factor}}``.
    """
    std = str(standard).upper()
    if std not in LOAD_STANDARDS:
        raise ValueError(f"standard must be one of {LOAD_STANDARDS}, "
                         f"got {standard!r}")
    dead, live, quakes, winds = _classify_cases(model)
    combos: Dict[str, Dict[str, float]] = {}
    if dead is None:
        warnings.warn("ASCE 7 combinations: no DEAD case found; no "
                      "combinations generated", UserWarning)
        return combos
    D = dead
    if live is None:
        warnings.warn("ASCE 7 combinations: no LIVE case found; live terms "
                      "omitted", UserWarning)

    if std == "LRFD":
        combos["1.4D"] = {D: 1.4}
        c = {D: 1.2}
        if live is not None:
            c[live] = 1.6
        combos["1.2D+1.6L"] = c
        for lat, plabel in [(q, q) for q in quakes] + [(w, w) for w in winds]:
            for sgn, sl in _SIGNS:
                c1 = {D: 1.2}
                if live is not None:
                    c1[live] = 1.0
                c1[lat] = sgn * 1.0
                combos[f"1.2D+1.0L{sl}1.0{plabel}"] = c1
                combos[f"0.9D{sl}1.0{plabel}"] = {D: 0.9, lat: sgn * 1.0}
    else:  # ASD
        combos["D"] = {D: 1.0}
        c = {D: 1.0}
        if live is not None:
            c[live] = 1.0
        combos["D+L"] = c
        for lat, plabel in [(q, q) for q in quakes] + [(w, w) for w in winds]:
            e = 0.7 if lat in quakes else 0.6      # seismic vs wind ASD factor
            e34 = round(0.75 * e, 6)               # 0.75*0.7 = 0.525 exactly
            for sgn, sl in _SIGNS:
                combos[f"D{sl}{e:g}{plabel}"] = {D: 1.0, lat: sgn * e}
                c1 = {D: 1.0}
                if live is not None:
                    c1[live] = 0.75
                c1[lat] = sgn * e34
                combos[f"D+0.75L{sl}{e34:g}{plabel}"] = c1
                combos[f"0.6D{sl}{e:g}{plabel}"] = {D: 0.6, lat: sgn * e}
    return combos


def apply_asce7_combinations(model: BuildingModel, standard: str = "LRFD"
                             ) -> Dict[str, Dict[str, float]]:
    """Generate and ADD the ASCE 7 combinations to ``model.combos``.

    Returns the same dict as :func:`asce7_combinations`.
    """
    combos = asce7_combinations(model, standard)
    for name, cases in combos.items():
        model.add_combo(name, cases)
    return combos


# --------------------------------------------------------------------------- #
# Equivalent lateral force (Section 12.8)
# --------------------------------------------------------------------------- #
def asce7_elf(model: BuildingModel, SDS: float, SD1: float, R: float,
              Ie: float = 1.0, Ct: float = 0.0466, x: float = 0.9,
              direction: str = "X", name: str = "ELF") -> LoadPattern:
    """ASCE 7-16 equivalent-lateral-force seismic pattern (Section 12.8).

    * approximate period ``Ta = Ct * hn^x`` (Eq. 12.8-7; ``hn`` = building
      height = top-story elevation);
    * seismic response coefficient ``Cs = SDS/(R/Ie)`` capped by
      ``SD1/(Ta (R/Ie))`` (Eqs. 12.8-2/12.8-3) and floored at
      ``max(0.044 SDS Ie, 0.01)`` (Eq. 12.8-5);
    * base shear ``V = Cs W`` (Eq. 12.8-1), ``W`` = seismic weight from the
      story masses;
    * vertical distribution ``Fx = V (w_x h_x^k)/sum(w h^k)`` (Eqs.
      12.8-11/12.8-12) with the distribution exponent ``k = 1`` for
      ``Ta <= 0.5 s``, ``2`` for ``Ta >= 2.5 s``, linear between.

    The result is a story-force :class:`LoadPattern` of kind ``"quake"``
    (``fx`` for direction ``"X"``, ``fy`` for ``"Y"``) stored under ``name``
    (replacing an existing pattern) and returned.
    """
    if direction not in ("X", "Y"):
        raise ValueError(f"direction must be X|Y, got {direction!r}")
    for label, val in (("SDS", SDS), ("SD1", SD1), ("R", R), ("Ie", Ie),
                       ("Ct", Ct)):
        if not (math.isfinite(float(val)) and float(val) > 0.0):
            raise ValueError(f"{label} must be a finite value > 0")
    if not model.stories:
        raise ValueError("model has no stories to load")

    hn = max(s.elevation for s in model.stories)
    if hn <= 0.0:
        raise ValueError("building height (top-story elevation) must be > 0")
    Ta = Ct * hn ** x
    RoIe = R / Ie
    Cs = min(SDS / RoIe, SD1 / (Ta * RoIe))
    Cs = max(Cs, max(0.044 * SDS * Ie, 0.01))

    masses = model.compute_story_masses()
    weights = {s.name: masses.get(s.name, 0.0) * G_ACCEL
               for s in model.stories}
    W = sum(weights.values())
    V = Cs * W

    if Ta <= 0.5:
        k = 1.0
    elif Ta >= 2.5:
        k = 2.0
    else:
        k = 1.0 + (Ta - 0.5) / 2.0

    denom = sum(weights[s.name] * s.elevation ** k for s in model.stories)
    pat = LoadPattern(name, "quake")
    for s in model.stories:
        cvx = (weights[s.name] * s.elevation ** k / denom
               if denom > 0.0 else 0.0)
        f = V * cvx
        pat.story_forces.append(StoryForce(
            s.name,
            fx=f if direction == "X" else 0.0,
            fy=f if direction == "Y" else 0.0))
    model.patterns[name] = pat
    return pat
