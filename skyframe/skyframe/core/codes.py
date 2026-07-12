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
* :func:`live_load_reduction` (v0.16) — the §4.7 reduced-live-load
  multiplier per COLUMN, plus :func:`reduce_live_demands`, the DESIGN-STAGE
  helper the design endpoints use to scale the live-attributable share of
  member demands before checking.

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


def asce7_combinations(model: BuildingModel, standard: str = "LRFD",
                       SDS: Optional[float] = None
                       ) -> Dict[str, Dict[str, float]]:
    """Generate the ASCE 7-16 strength (LRFD) or ASD load combinations.

    Cases are matched by pattern kind (dead / live / quake / wind).  Seismic
    and wind combinations get both sign variants (``+E``/``-E``).  A combo
    term whose kind has no matching case is dropped and a ``UserWarning`` is
    emitted (e.g. a model without a LIVE case omits every live term).
    Returns ``{combo name -> {case name -> factor}}``.

    ``SDS`` (v0.17, optional): when provided, the SEISMIC combinations
    include the vertical seismic component ``Ev = 0.2 * SDS * D`` (ASCE 7-16
    §12.4.2.2, Eq. 12.4-4a) folded into the DEAD factor per the §12.4.2.3
    seismic load combinations, with the redundancy factor ``rho = 1``:

    * LRFD (§12.4.2.3 basic combinations 6 / 7)::

          (1.2 + 0.2*SDS) D + rho*QE + L        # E = Eh + Ev
          (0.9 - 0.2*SDS) D + rho*QE            # E = Eh - Ev

    * ASD (§2.4.5 / §12.4.2.3 combinations 8 / 9 / 10)::

          (1.0 + 0.14*SDS)  D + 0.7 rho*QE
          (1.0 + 0.105*SDS) D + 0.525 rho*QE + 0.75 L
          (0.6 - 0.14*SDS)  D + 0.7 rho*QE

    ``+QE``/``-QE`` sign variants reverse only the HORIZONTAL component; the
    vertical component's sign is fixed by the combination form (additive in
    the gravity-heavy combos, subtractive in the uplift combos).  Wind and
    gravity-only combinations are unchanged.  ``SDS = None`` (default)
    reproduces the pre-v0.17 output exactly; ``SDS`` must be finite and
    >= 0.
    """
    std = str(standard).upper()
    if std not in LOAD_STANDARDS:
        raise ValueError(f"standard must be one of {LOAD_STANDARDS}, "
                         f"got {standard!r}")
    if SDS is not None:
        if (isinstance(SDS, bool) or not isinstance(SDS, (int, float))
                or not math.isfinite(SDS) or SDS < 0.0):
            raise ValueError(f"SDS must be a finite value >= 0 (or None), "
                             f"got {SDS!r}")
    sds = 0.0 if SDS is None else float(SDS)
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
            # v0.17 vertical seismic component (§12.4.2.3 combos 6/7): the
            # DEAD factor absorbs Ev = 0.2*SDS*D — quake combos only.
            if lat in quakes and sds:
                d_add, d_min = 1.2 + 0.2 * sds, 0.9 - 0.2 * sds
            else:
                d_add, d_min = 1.2, 0.9
            for sgn, sl in _SIGNS:
                c1 = {D: d_add}
                if live is not None:
                    c1[live] = 1.0
                c1[lat] = sgn * 1.0
                combos[f"{d_add:g}D+1.0L{sl}1.0{plabel}"] = c1
                combos[f"{d_min:g}D{sl}1.0{plabel}"] = {D: d_min,
                                                        lat: sgn * 1.0}
    else:  # ASD
        combos["D"] = {D: 1.0}
        c = {D: 1.0}
        if live is not None:
            c[live] = 1.0
        combos["D+L"] = c
        for lat, plabel in [(q, q) for q in quakes] + [(w, w) for w in winds]:
            e = 0.7 if lat in quakes else 0.6      # seismic vs wind ASD factor
            e34 = round(0.75 * e, 6)               # 0.75*0.7 = 0.525 exactly
            if lat in quakes and sds:
                # v0.17 §2.4.5 / §12.4.2.3 ASD combos 8/9/10: Ev on the DEAD
                # factor at 0.7*0.2 = 0.14 and 0.525*0.2 = 0.105 per SDS.
                for sgn, sl in _SIGNS:
                    da, da34, dm = (1.0 + 0.14 * sds, 1.0 + 0.105 * sds,
                                    0.6 - 0.14 * sds)
                    combos[f"{da:g}D{sl}{e:g}{plabel}"] = {D: da,
                                                           lat: sgn * e}
                    c1 = {D: da34}
                    if live is not None:
                        c1[live] = 0.75
                    c1[lat] = sgn * e34
                    combos[f"{da34:g}D+0.75L{sl}{e34:g}{plabel}"] = c1
                    combos[f"{dm:g}D{sl}{e:g}{plabel}"] = {D: dm,
                                                           lat: sgn * e}
                continue
            for sgn, sl in _SIGNS:
                combos[f"D{sl}{e:g}{plabel}"] = {D: 1.0, lat: sgn * e}
                c1 = {D: 1.0}
                if live is not None:
                    c1[live] = 0.75
                c1[lat] = sgn * e34
                combos[f"D+0.75L{sl}{e34:g}{plabel}"] = c1
                combos[f"0.6D{sl}{e:g}{plabel}"] = {D: 0.6, lat: sgn * e}
    return combos


def apply_asce7_combinations(model: BuildingModel, standard: str = "LRFD",
                             SDS: Optional[float] = None
                             ) -> Dict[str, Dict[str, float]]:
    """Generate and ADD the ASCE 7 combinations to ``model.combos``.

    Returns the same dict as :func:`asce7_combinations` (``SDS``: see there —
    v0.17 vertical seismic component on the seismic combos).
    """
    combos = asce7_combinations(model, standard, SDS=SDS)
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


# --------------------------------------------------------------------------- #
# v0.16: ASCE 7-16 §4.7 live-load reduction (columns)
# --------------------------------------------------------------------------- #
# Live-load element factor KLL (Table 4.7-1).  v0.16 uses KLL = 4 UNIFORMLY
# for every column — the table value for interior columns AND exterior
# columns without cantilever slabs (corner columns with cantilever slabs
# would be 3, edge columns with cantilevers 3; SkyFrame has no cantilever
# slabs, so 4 is the correct table value across the board — documented).
LIVE_KLL_COLUMN = 4.0
_LL_TOL = 1e-6


def live_load_reduction(model: BuildingModel) -> Dict[str, Dict[str, float]]:
    """ASCE 7-16 §4.7.1 reduced-live-load multiplier per COLUMN (v0.16).

    For each column (SI form of Eq. 4.7-1, ``At`` in m^2)::

        R = 0.25 + 4.57 / sqrt(KLL * At)

    clamped to [0.4, 1.0] — floor 0.5 for members supporting ONE floor,
    0.4 for members supporting two or more (§4.7.2).  ``KLL = 4``
    uniformly (:data:`LIVE_KLL_COLUMN`).  ``At`` is the column's tributary
    PLAN area times the number of stories supported at/above its top:

    * **Tributary rule** (regular orthogonal framing): half of every beam
      span framing into the column top in each plan direction —
      ``At_floor = (sum Lx_beams / 2) * (sum Ly_beams / 2)`` (an interior
      column with 6 m bays each way gets 6 x 6 = 36 m^2, a corner column
      3 x 3 = 9 m^2).  A beam is attributed to the x/y family by its
      dominant plan direction (skewed framing is approximated).
    * **Fallback** (irregular layouts): a column with NO attached beam in
      one of the two directions at its top elevation falls back to
      ``plan_area / n_columns_at_that_level`` with a ``UserWarning`` — the
      half-bay rule needs beams on the column to measure bays.
    * **Stories supported** = count of story levels at/above the column
      top (a story-1 column of an n-story building supports n floors;
      §4.7.1 accumulates the tributary area of every supported floor).

    The R = 1 cap embodies the §4.7.2 applicability threshold: the formula
    crosses 1.0 at ``KLL*At = (4.57/0.75)^2 = 37.1 m^2``, i.e. the code's
    400 ft^2 minimum for any reduction.

    Returns ``{column uid -> {"KLL", "At", "R", "n_stories"}}``.  This is a
    DESIGN-STAGE reduction: analysis results are never modified — the
    design endpoints scale the live-attributable share of member demands by
    R via :func:`reduce_live_demands`.
    """
    out: Dict[str, Dict[str, float]] = {}
    columns = [m for m in model.members if m.kind == "column"]
    if not columns:
        return out
    beams = [m for m in model.members if m.kind == "beam"]
    lx, ly = model.plan_extents()
    plan_area = lx * ly
    for col in columns:
        top = col.pi if col.pi[2] >= col.pj[2] else col.pj
        x, y, zt = float(top[0]), float(top[1]), float(top[2])
        n_above = sum(1 for s in model.stories
                      if s.elevation >= zt - _LL_TOL)
        n_above = max(n_above, 1)
        dx = dy = 0.0
        for b in beams:
            # beam at the column-top floor with an endpoint on the column
            if (abs(b.pi[2] - zt) > _LL_TOL
                    or abs(b.pj[2] - zt) > _LL_TOL):
                continue
            at_i = (abs(b.pi[0] - x) < _LL_TOL
                    and abs(b.pi[1] - y) < _LL_TOL)
            at_j = (abs(b.pj[0] - x) < _LL_TOL
                    and abs(b.pj[1] - y) < _LL_TOL)
            if not (at_i or at_j):
                continue
            ex = abs(b.pj[0] - b.pi[0])
            ey = abs(b.pj[1] - b.pi[1])
            if ex >= ey:
                dx += b.length / 2.0
            else:
                dy += b.length / 2.0
        if dx > _LL_TOL and dy > _LL_TOL:
            at_floor = dx * dy
        else:
            n_cols = sum(1 for c in columns
                         if abs(max(c.pi[2], c.pj[2]) - zt) < _LL_TOL)
            at_floor = plan_area / max(n_cols, 1)
            warnings.warn(
                f"live_load_reduction: column {col.uid!r} has no attached "
                "beams in both plan directions at its top; tributary area "
                f"falls back to plan_area/n_columns = {at_floor:.3g} m^2",
                UserWarning)
        At = at_floor * n_above
        kll_at = LIVE_KLL_COLUMN * At
        if kll_at > 0.0:
            R = 0.25 + 4.57 / math.sqrt(kll_at)
        else:
            R = 1.0
        floor_ = 0.5 if n_above <= 1 else 0.4
        R = min(1.0, max(floor_, R))
        out[col.uid] = {"KLL": LIVE_KLL_COLUMN, "At": float(At),
                        "R": float(R), "n_stories": float(n_above)}
    return out


def reduce_live_demands(results_dict, model: BuildingModel, *,
                        live_case: str = "LIVE",
                        reduction: Optional[Dict[str, Dict[str, float]]]
                        = None) -> dict:
    """DEEP-COPIED results dict with the live share of COLUMN demands reduced.

    DESIGN-STAGE helper (v0.16) used by the design endpoints: because the
    analysis is linear, the live-attributable portion of any quantity in a
    case/combo is ``combo factor x LIVE-case value``, so per column the
    adjusted demand is::

        q_adj = q_total + (R - 1) * f_live * q_LIVE

    applied to the 12 member end forces and every station column
    (N/V2/V3/T/M2/M3).  ``f_live`` is 1 for the live case itself, the
    combo's LIVE-case factor for ADDITIVE combos, and 0 for every other
    plain case; envelope combos and RS cases are left UNCHANGED (their
    per-member live attribution is not linear — documented limitation).
    ``reduction`` defaults to :func:`live_load_reduction`.  Analysis
    results themselves are never mutated (a deep copy is returned).
    Raises ``ValueError`` when ``live_case`` is not a solved case.
    """
    import copy

    d = (results_dict.to_dict() if hasattr(results_dict, "to_dict")
         else results_dict)
    if not isinstance(d, dict):
        raise TypeError("results must be a results object or a results dict")
    live_block = (d.get("cases") or {}).get(live_case)
    if live_block is None:
        raise ValueError(f"live case {live_case!r} not found in results "
                         "'cases'")
    red = live_load_reduction(model) if reduction is None else reduction
    out = copy.deepcopy(d)

    def factor_for(name: str, kind: str) -> float:
        if kind == "cases":
            return 1.0 if name == live_case else 0.0
        cb = model.combos.get(name)
        if cb is not None and cb.combo_type == "add":
            return float(cb.cases.get(live_case, 0.0))
        return 0.0                       # envelope combo: left unchanged

    live_mf = live_block.get("member_forces") or {}
    live_st = live_block.get("member_stations") or {}
    for kind in ("cases", "combos"):
        for name, block in (out.get(kind) or {}).items():
            f = factor_for(name, kind)
            if f == 0.0:
                continue
            for uid, info in red.items():
                scale = (info["R"] - 1.0) * f
                if scale == 0.0:
                    continue
                mf_l = live_mf.get(uid)
                mf = (block.get("member_forces") or {}).get(uid)
                if mf_l and mf:
                    block["member_forces"][uid] = [
                        float(v) + scale * float(lv)
                        for v, lv in zip(mf, mf_l)]
                st_l = live_st.get(uid)
                st = (block.get("member_stations") or {}).get(uid)
                if st_l and st:
                    for key in ("N", "V2", "V3", "T", "M2", "M3"):
                        st[key] = [float(v) + scale * float(lv)
                                   for v, lv in zip(st[key], st_l[key])]
    return out

# --------------------------------------------------------------------------- #
# v0.23: NBCC 2020 lateral loads (static wind + equivalent static seismic)
# --------------------------------------------------------------------------- #
# NBCC 4.1.7.1 exposure (terrain) factor Ce — the two tabulated power laws
# (heights in m, evaluated at the story-top elevation):
#   open terrain :  Ce = (h/10)^0.2,      not less than 0.9
#   rough terrain:  Ce = 0.7*(h/12)^0.3,  not less than 0.7
# (the intermediate-exposure interpolation of the commentary is NOT
# implemented — documented simplification).
NBCC_EXPOSURES = ("open", "rough")
NBCC_CG = 2.0            # gust effect factor, static procedure (4.1.7.1)
NBCC_CP_TOTAL = 1.3      # windward Cp 0.8 + leeward 0.5 combined (documented
#                          building-total coefficient, like the ASCE helper)

# NBCC 4.1.8.11(3) approximate fundamental period Ta = coeff * hn^0.75
NBCC_TA_COEFF = {"steel_mf": 0.085, "concrete_mf": 0.075, "other": 0.05}


def nbcc_ce(z: float, exposure: str = "open") -> float:
    """NBCC 2020 Table 4.1.7.1 exposure factor Ce (power-law form)."""
    if exposure not in NBCC_EXPOSURES:
        raise ValueError(f"exposure must be one of {NBCC_EXPOSURES}, "
                         f"got {exposure!r}")
    zz = max(float(z), 0.0)
    if exposure == "open":
        return max((zz / 10.0) ** 0.2 if zz > 0.0 else 0.0, 0.9)
    return max(0.7 * (zz / 12.0) ** 0.3 if zz > 0.0 else 0.0, 0.7)


def nbcc_wind_pattern(model: BuildingModel, q: float,
                      exposure: str = "open", name: str = "NWIND",
                      direction: str = "X", cp_total: float = NBCC_CP_TOTAL,
                      Iw: float = 1.0) -> LoadPattern:
    """NBCC 2020 static-procedure wind LoadPattern with story forces.

    External pressure ``p = Iw * q * Ce(z) * Cg * Cp`` (NBCC 4.1.7.1) with
    the REFERENCE VELOCITY PRESSURE ``q`` passed directly in kPa (the
    1-in-50 tabulated value; NBCC does not derive it from a wind speed),
    ``Ce`` per :func:`nbcc_ce` (story-top elevation), ``Cg = 2.0``
    (static procedure) and ``cp_total`` the combined windward + leeward
    building coefficient (default 0.8 + 0.5 = 1.3 — the same
    facade-total convention as the ASCE helper; the leeward face's
    height-CONSTANT Ce-at-mid-height refinement is not modeled —
    documented simplification: both faces use Ce(z) of the loaded
    level).  Story force ``F = p * trib_height * width`` with the same
    tributary facade areas as :func:`skyframe.core.builder.
    make_wind_pattern`.  Stored under ``name`` (kind "wind", replacing
    an existing pattern) and returned.
    """
    if direction not in ("X", "Y"):
        raise ValueError(f"direction must be X|Y, got {direction!r}")
    if exposure not in NBCC_EXPOSURES:
        raise ValueError(f"exposure must be one of {NBCC_EXPOSURES}, "
                         f"got {exposure!r}")
    for label, val in (("q", q), ("cp_total", cp_total), ("Iw", Iw)):
        if (isinstance(val, bool) or not isinstance(val, (int, float))
                or not math.isfinite(val) or val <= 0.0):
            raise ValueError(f"{label} must be a finite value > 0")
    if not model.stories:
        raise ValueError("model has no stories to load")
    lx, ly = model.plan_extents()
    width = ly if direction == "X" else lx
    if width <= 0.0:
        raise ValueError("plan width perpendicular to the wind is zero")

    pat = LoadPattern(name, "wind")
    heights = [s.height for s in model.stories]
    for i, story in enumerate(model.stories):
        trib_h = heights[i] / 2.0 + (heights[i + 1] / 2.0
                                     if i + 1 < len(heights) else 0.0)
        p = float(Iw) * float(q) * nbcc_ce(story.elevation, exposure) \
            * NBCC_CG * float(cp_total)                  # kPa
        force = p * trib_h * width                       # kN
        pat.story_forces.append(StoryForce(
            story.name,
            fx=force if direction == "X" else 0.0,
            fy=force if direction == "Y" else 0.0))
    model.patterns[name] = pat
    return pat


def nbcc_spectrum_value(T: float, Sa02: float, Sa05: float, Sa10: float,
                        Sa20: float) -> float:
    """NBCC design spectral acceleration S(T) [g] from four user ordinates.

    The site-adjusted ordinates S(0.2)/S(0.5)/S(1.0)/S(2.0) are supplied
    directly (site coefficients F(T) are the caller's job — NBCC 2020
    tabulates Sa at these periods per location).  Interpolation between
    the ordinates is LINEAR IN log(T) (documented simplification of the
    NBCC 4.1.8.4(9) "linear interpolation" rule — log-period
    interpolation tracks the tabulated 1/T-like decay closely and is
    smooth across the octave-spaced points):

    * ``T <= 0.2 s``: S = S(0.2)  (the code plateau);
    * ``0.2 < T < 2.0``: log-T linear between the bracketing ordinates;
    * ``T >= 2.0 s``: S = S(2.0) * (2.0/T)  (1/T decay — NBCC's higher-
      period ordinates S(5)/S(10) are not requested; documented).
    """
    for label, val in (("Sa02", Sa02), ("Sa05", Sa05), ("Sa10", Sa10),
                       ("Sa20", Sa20)):
        v = float(val)
        if not (math.isfinite(v) and v >= 0.0):
            raise ValueError(f"{label} must be a finite value >= 0")
    if not (math.isfinite(T) and T >= 0.0):
        raise ValueError("period T must be a finite value >= 0")
    pts = ((0.2, float(Sa02)), (0.5, float(Sa05)), (1.0, float(Sa10)),
           (2.0, float(Sa20)))
    if T <= pts[0][0]:
        return pts[0][1]
    if T >= pts[-1][0]:
        return pts[-1][1] * (pts[-1][0] / T)
    for (t0, s0), (t1, s1) in zip(pts, pts[1:]):
        if t0 <= T <= t1:
            f = (math.log(T) - math.log(t0)) / (math.log(t1) - math.log(t0))
            return s0 + (s1 - s0) * f
    return pts[-1][1]  # pragma: no cover - unreachable


def nbcc_seismic_elf(model: BuildingModel, Sa02: float, Sa05: float,
                     Sa10: float, Sa20: float, RdRo: float,
                     Ie: float = 1.0, system: str = "other",
                     direction: str = "X", name: str = "NELF"
                     ) -> LoadPattern:
    """NBCC 2020 equivalent-static seismic pattern (4.1.8.11).

    * approximate period ``Ta = coeff * hn^0.75`` with coeff per
      ``system``: "steel_mf" 0.085, "concrete_mf" 0.075, "other" 0.05
      (Table in 4.1.8.11(3); hn = top-story elevation);
    * base shear ``V = S(Ta) * Mv * Ie * W / (Rd*Ro)`` with ``Mv = 1``
      FIXED (higher-mode factor for regular short/moderate buildings —
      documented simplification) and S(T) per
      :func:`nbcc_spectrum_value`;
    * caps per 4.1.8.11(2): floor ``V >= S(2.0)*Mv*Ie*W/(Rd*Ro)`` and
      cap ``V <= max(2/3*S(0.2), S(0.5))*Ie*W/(Rd*Ro)``.  NBCC applies
      the upper cap only for Rd >= 1.5; ``RdRo`` arrives as a product so
      the condition cannot be checked — the cap is applied
      unconditionally (documented; every ductile system has Rd >= 1.5);
    * vertical distribution 4.1.8.11(7): a top force
      ``Ft = 0.07*Ta*V <= 0.25*V`` when ``Ta > 0.7 s`` (else 0), the
      remainder ``(V - Ft)`` spread as ``w_x h_x / sum(w h)``.

    Result: a story-force :class:`LoadPattern` of kind ``"quake"``
    stored under ``name`` (replacing an existing pattern) and returned.
    """
    if direction not in ("X", "Y"):
        raise ValueError(f"direction must be X|Y, got {direction!r}")
    if system not in NBCC_TA_COEFF:
        raise ValueError(f"system must be one of "
                         f"{tuple(NBCC_TA_COEFF)}, got {system!r}")
    for label, val in (("RdRo", RdRo), ("Ie", Ie)):
        if (isinstance(val, bool) or not isinstance(val, (int, float))
                or not math.isfinite(val) or val <= 0.0):
            raise ValueError(f"{label} must be a finite value > 0")
    if not model.stories:
        raise ValueError("model has no stories to load")
    hn = max(s.elevation for s in model.stories)
    if hn <= 0.0:
        raise ValueError("building height (top-story elevation) must be > 0")

    Ta = NBCC_TA_COEFF[system] * hn ** 0.75
    S_Ta = nbcc_spectrum_value(Ta, Sa02, Sa05, Sa10, Sa20)
    Mv = 1.0
    masses = model.compute_story_masses()
    weights = {s.name: masses.get(s.name, 0.0) * G_ACCEL
               for s in model.stories}
    W = sum(weights.values())
    RR, IeF = float(RdRo), float(Ie)
    V = S_Ta * Mv * IeF * W / RR
    V_min = float(Sa20) * Mv * IeF * W / RR
    V_max = max(2.0 / 3.0 * float(Sa02), float(Sa05)) * IeF * W / RR
    V = min(max(V, V_min), V_max)

    Ft = 0.07 * Ta * V if Ta > 0.7 else 0.0
    Ft = min(Ft, 0.25 * V)
    denom = sum(weights[s.name] * s.elevation for s in model.stories)
    top = max(model.stories, key=lambda s: s.elevation)
    pat = LoadPattern(name, "quake")
    for s in model.stories:
        f = ((V - Ft) * weights[s.name] * s.elevation / denom
             if denom > 0.0 else 0.0)
        if s.name == top.name:
            f += Ft
        pat.story_forces.append(StoryForce(
            s.name,
            fx=f if direction == "X" else 0.0,
            fy=f if direction == "Y" else 0.0))
    model.patterns[name] = pat
    return pat
