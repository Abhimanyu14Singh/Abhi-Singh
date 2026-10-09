"""v1.16 auto lateral load generators (ETABS Define > Load Patterns > Auto
Lateral Load) — CONTRACT "Temperature gradients, projected loads and
auto-lateral generators (ASCE 7-22, EC8, IS 1893, user)".

Each generator mirrors :func:`skyframe.core.codes.asce7_elf`: it computes
story forces from the story seismic weights (``model.compute_story_masses()
* g``, or an explicit ``weights`` override in kN) and the story elevations
(``Story.elevation`` = height above the base), and stores a story-force
:class:`LoadPattern` (replacing a pattern of the same name).  The pure
``compute_*`` functions return the hand-calculation summary::

    {"code", "direction", "T", "W", "V", "k"?, <code coefficients>,
     "stories": [{"story", "h", "w", "F"}, ...]}

and the ``*_pattern`` functions write the pattern.  ``ecc`` (default 0)
adds ETABS accidental eccentricity: ``ecc != 0`` sets
``pattern.accidental_torsion = True`` and ``pattern.ecc = ecc`` (a
NEGATIVE value is the "- eccentricity" variant: story torque
``Mz = F * ecc * B_perp`` with its sign).

Codes:

* ``asce7_22`` — ASCE 7-22 §12.8 ELF.  Ta = Ct hn^x; a supplied (program)
  period is capped at Cu*Ta (Table 12.8-1).  Two-period spectrum (default):
  ``Sa = SDS`` (T <= Ts), ``SD1/T`` (Ts < T <= TL), ``SD1 TL/T^2`` (T > TL);
  multi-period option (``mprs`` = [[T, Sa], ...], §11.4.5.1): the ELF uses
  the descending envelope ``Sa_ELF(T) = min(SDS, max_{T' >= T} Sa(T'))``
  (the ascending branch never lowers the base shear; ``SDS`` defaults to
  0.9 max Sa on 0.2..5 s) — the ETABS-style simplification.  ``Cs =
  Sa_ELF/(R/Ie)`` floored at ``max(0.044 SDS Ie, 0.01)`` and, for S1 >=
  0.6, ``0.5 S1/(R/Ie)``.  ``F_x = V w_x h_x^k / sum w h^k``, k = 1 (T <=
  0.5 s), 2 (T >= 2.5 s), linear between.
* ``ec8`` — EN 1998-1 §4.3.3.2 lateral force method: ``Fb = Sd(T1) W
  lambda`` with the §3.2.2.5 design spectrum (Type 1/2, ground A-E, Tables
  3.2/3.3, beta = 0.2), ``ag = gamma_I agR`` (in g), ``lambda = 0.85`` when
  T1 <= 2 Tc and the building has more than two storeys, else 1.0; T1 =
  Ct H^0.75 (Ct 0.085 steel MRF / 0.075 concrete MRF / 0.050 other) unless
  given; distribution ``F_i = Fb z_i m_i / sum z_j m_j`` (Eq. 4.11) or the
  supplied mode shape ``s_i`` (Eq. 4.10).
* ``is1893`` — IS 1893 (Part 1):2016 §7.6: ``Ah = (Z/2)(Sa/g)/(R/I)``
  (times an optional damping factor), static-method Sa/g (Fig. 2a: 2.5 up
  to 0.40/0.55/0.67 s for soil I/II/III, then 1.00/1.36/1.67 / T, constant
  0.25/0.34/0.42 beyond 4 s); T = 0.075 h^0.75 (RC MRF), 0.080 h^0.75
  (steel MRF) or 0.09 h / sqrt(d) (other; d = plan dimension along the
  load) unless given; ``Vb = max(Ah W, rho W)`` with the Table 7 minimum
  rho (Z 0.10/0.16/0.24/0.36 -> 0.7/1.1/1.6/2.4 %, linear between);
  ``Q_i = Vb W_i h_i^2 / sum W_j h_j^2``.
* ``user_coefficient`` — ``V = C W``, ``F_x = V w h^k / sum w h^k``.
* ``user_loads`` — the given story-force table ``[{story, fx, fy}]``.
* ``asce7_22_wind`` — ASCE 7-22 directional procedure (MWFRS): ``qz =
  0.613 Kz Kzt Ke V^2`` (Pa) with ``Kz = 2.41 (z/zg)^(2/alpha)`` (7-22
  Table 26.10-1: B 7.5 / 3280 ft, C 9.8 / 2460 ft, D 11.5 / 1935 ft; z
  floored at 15 ft for every exposure — the same documented floor as the
  7-16 helper), ``Ke = exp(-0.000119 ze)`` (ze = ground elevation, m) and
  ``Kd`` moved from qz to the pressure ``p = qz Kd cp_total`` (cp_total =
  combined G Cp windward + leeward, default 1.3).  Story force = p x the
  same tributary facade area as :func:`skyframe.core.builder.
  make_wind_pattern`.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

from .model import G_ACCEL, BuildingModel, LoadPattern, StoryForce

CODES = ("asce7_22", "ec8", "is1893", "user_coefficient", "user_loads",
         "asce7_22_wind")

# ASCE 7-22 Table 12.8-1 (SD1, Cu), SD1 descending
ASCE22_CU = ((0.4, 1.4), (0.3, 1.4), (0.2, 1.5), (0.15, 1.6), (0.1, 1.7))

# EN 1998-1 Tables 3.2 / 3.3: ground type -> (S, TB, TC, TD)
EC8_SPECTRA = {
    1: {"A": (1.0, 0.15, 0.4, 2.0), "B": (1.2, 0.15, 0.5, 2.0),
        "C": (1.15, 0.20, 0.6, 2.0), "D": (1.35, 0.20, 0.8, 2.0),
        "E": (1.4, 0.15, 0.5, 2.0)},
    2: {"A": (1.0, 0.05, 0.25, 1.2), "B": (1.35, 0.05, 0.25, 1.2),
        "C": (1.5, 0.10, 0.25, 1.2), "D": (1.8, 0.10, 0.30, 1.2),
        "E": (1.6, 0.05, 0.25, 1.2)},
}
EC8_CT = {"steel_mrf": 0.085, "concrete_mrf": 0.075, "other": 0.050}

# IS 1893:2016 static-method Sa/g: soil -> (plateau end Tc, numerator, tail)
IS1893_SOIL = {"I": (0.40, 1.00, 0.25), "II": (0.55, 1.36, 0.34),
               "III": (0.67, 1.67, 0.42)}
IS1893_RHO = ((0.10, 0.007), (0.16, 0.011), (0.24, 0.016), (0.36, 0.024))

# ASCE 7-22 Table 26.10-1: exposure -> (alpha, zg [m])
ASCE22_WIND_EXPOSURES = {"B": (7.5, 3280 * 0.3048), "C": (9.8, 2460 * 0.3048),
                         "D": (11.5, 1935 * 0.3048)}
ASCE22_WIND_ZMIN = 15 * 0.3048


# ----------------------------------------------------------------- helpers
def _pos(label: str, val, allow_zero: bool = False) -> float:
    if (isinstance(val, bool) or not isinstance(val, (int, float))
            or not math.isfinite(val) or val < 0.0
            or (val == 0.0 and not allow_zero)):
        raise ValueError(f"{label} must be a finite value "
                         f"{'>= 0' if allow_zero else '> 0'}")
    return float(val)


def _check_common(model: BuildingModel, direction: str) -> float:
    if direction not in ("X", "Y"):
        raise ValueError(f"direction must be X|Y, got {direction!r}")
    if not model.stories:
        raise ValueError("model has no stories to load")
    hn = max(s.elevation for s in model.stories)
    if hn <= 0.0:
        raise ValueError("building height (top-story elevation) must be > 0")
    return hn


def story_weights(model: BuildingModel,
                  weights: Optional[Dict[str, float]] = None
                  ) -> Dict[str, float]:
    """Story seismic weights (kN): the override, else masses * g."""
    if weights is not None:
        if not isinstance(weights, dict):
            raise ValueError("weights must be a {story: kN} object")
        names = {s.name for s in model.stories}
        for k, v in weights.items():
            if k not in names:
                raise ValueError(f"weights: unknown story {k!r}")
            _pos(f"weight of {k}", v, allow_zero=True)
        return {s.name: float(weights.get(s.name, 0.0))
                for s in model.stories}
    masses = model.compute_story_masses()
    return {s.name: masses.get(s.name, 0.0) * G_ACCEL for s in model.stories}


def _k_exponent(T: float) -> float:
    if T <= 0.5:
        return 1.0
    if T >= 2.5:
        return 2.0
    return 1.0 + (T - 0.5) / 2.0


def _distribute(model: BuildingModel, V: float, w: Dict[str, float],
                shape: Dict[str, float]) -> List[dict]:
    """F_x = V w_x s_x / sum w s (s = h^k, z, h^2 or a mode shape)."""
    denom = sum(w[s.name] * shape[s.name] for s in model.stories)
    out = []
    for s in model.stories:
        F = V * w[s.name] * shape[s.name] / denom if denom > 0.0 else 0.0
        out.append({"story": s.name, "h": s.elevation, "w": w[s.name],
                    "F": F})
    return out


def _summary(code, direction, T, W, V, stories, **extra) -> dict:
    return {"code": code, "direction": direction, "T": T, "W": W, "V": V,
            **extra, "stories": stories}


def write_pattern(model: BuildingModel, name: str, kind: str,
                  summary: dict, ecc: float = 0.0) -> LoadPattern:
    """Store the summary's story forces as a pattern (replacing ``name``)."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("pattern name must be a non-empty string")
    if (isinstance(ecc, bool) or not isinstance(ecc, (int, float))
            or not math.isfinite(ecc) or abs(ecc) > 0.5):
        raise ValueError("ecc must be a finite value with |ecc| <= 0.5")
    pat = LoadPattern(name, kind)
    for row in summary["stories"]:
        fx = row.get("fx", row["F"] if summary["direction"] == "X" else 0.0)
        fy = row.get("fy", row["F"] if summary["direction"] == "Y" else 0.0)
        pat.story_forces.append(StoryForce(row["story"], fx=fx, fy=fy))
    if ecc:
        pat.accidental_torsion = True
        pat.ecc = float(ecc)
    model.patterns[name] = pat
    return pat


# -------------------------------------------------------------- ASCE 7-22
def asce22_cu(SD1: float) -> float:
    """Table 12.8-1 Cu (linear between rows, clamped)."""
    if SD1 >= ASCE22_CU[0][0]:
        return ASCE22_CU[0][1]
    if SD1 <= ASCE22_CU[-1][0]:
        return ASCE22_CU[-1][1]
    for (s0, c0), (s1, c1) in zip(ASCE22_CU, ASCE22_CU[1:]):
        if s1 <= SD1 <= s0:
            return c1 + (c0 - c1) * (SD1 - s1) / (s0 - s1)
    return ASCE22_CU[-1][1]  # pragma: no cover


def _mprs_sa(points: Sequence[Sequence[float]], T: float) -> float:
    pts = sorted((float(t), float(s)) for t, s in points)
    if T <= pts[0][0]:
        return pts[0][1]
    if T >= pts[-1][0]:
        return pts[-1][1] * pts[-1][0] / T if T > 0 else pts[-1][1]
    for (t0, s0), (t1, s1) in zip(pts, pts[1:]):
        if t0 <= T <= t1:
            return s0 + (s1 - s0) * (T - t0) / (t1 - t0) if t1 > t0 else s1
    return pts[-1][1]  # pragma: no cover


def _check_mprs(mprs) -> List[List[float]]:
    if (not isinstance(mprs, (list, tuple)) or len(mprs) < 2):
        raise ValueError("mprs must be a list of >= 2 [T, Sa] pairs")
    out = []
    for p in mprs:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise ValueError("mprs entries must be [T, Sa] pairs")
        out.append([_pos("mprs T", p[0], True), _pos("mprs Sa", p[1], True)])
    return out


def compute_asce7_22(model: BuildingModel, SDS: Optional[float] = None,
                     SD1: Optional[float] = None, R: Optional[float] = None,
                     Ie: float = 1.0, TL: float = 8.0, S1: float = 0.0,
                     Ct: float = 0.0466, x: float = 0.9,
                     T: Optional[float] = None, mprs=None,
                     weights=None, direction: str = "X") -> dict:
    hn = _check_common(model, direction)
    for lab, v in (("SD1", SD1), ("R", R), ("Ie", Ie), ("TL", TL),
                   ("Ct", Ct), ("x", x)):
        _pos(lab, v)
    S1 = _pos("S1", S1, allow_zero=True)
    Ta = Ct * hn ** x
    Cu = asce22_cu(SD1)
    T_used = Ta if T is None else min(_pos("T", T), Cu * Ta)
    if mprs is not None:
        pts = _check_mprs(mprs)
        if SDS is None:
            grid = [0.2 + i * 0.01 for i in range(481)]
            SDS = 0.9 * max(_mprs_sa(pts, t) for t in grid)
        _pos("SDS", SDS)
        tail = [T_used] + [t for t, _ in pts if t > T_used]
        Sa = min(SDS, max(_mprs_sa(pts, t) for t in tail))
        spectrum = "multi_period"
    else:
        _pos("SDS", SDS)
        Ts = SD1 / SDS
        if T_used <= Ts:
            Sa = SDS
        elif T_used <= TL:
            Sa = SD1 / T_used
        else:
            Sa = SD1 * TL / T_used ** 2
        spectrum = "two_period"
    RoIe = R / Ie
    Cs = Sa / RoIe
    Cs_min = max(0.044 * SDS * Ie, 0.01)
    if S1 >= 0.6:
        Cs_min = max(Cs_min, 0.5 * S1 / RoIe)
    Cs = max(Cs, Cs_min)
    w = story_weights(model, weights)
    W = sum(w.values())
    V = Cs * W
    k = _k_exponent(T_used)
    shape = {s.name: s.elevation ** k for s in model.stories}
    return _summary("asce7_22", direction, T_used, W, V,
                    _distribute(model, V, w, shape), k=k, Ta=Ta, Cu=Cu,
                    Sa=Sa, Cs=Cs, SDS=SDS, spectrum=spectrum)


# -------------------------------------------------------------------- EC8
def ec8_sd(T: float, ag: float, q: float, S: float, TB: float, TC: float,
           TD: float, beta: float = 0.2) -> float:
    """EN 1998-1 Eq. 3.13-3.16 design spectrum Sd(T) (in g, ag in g)."""
    if T <= TB:
        return ag * S * (2.0 / 3.0 + T / TB * (2.5 / q - 2.0 / 3.0))
    if T <= TC:
        return ag * S * 2.5 / q
    if T <= TD:
        return max(ag * S * 2.5 / q * TC / T, beta * ag)
    return max(ag * S * 2.5 / q * TC * TD / T ** 2, beta * ag)


def compute_ec8(model: BuildingModel, ag: float, q: float,
                ground_type: str = "B", spectrum_type: int = 1,
                gamma_I: float = 1.0, T1: Optional[float] = None,
                structure: str = "other", distribution: str = "height",
                mode_shape: Optional[Dict[str, float]] = None,
                S: Optional[float] = None, TB: Optional[float] = None,
                TC: Optional[float] = None, TD: Optional[float] = None,
                beta: float = 0.2, weights=None,
                direction: str = "X") -> dict:
    H = _check_common(model, direction)
    agR = _pos("ag", ag)
    _pos("q", q)
    _pos("gamma_I", gamma_I)
    if spectrum_type not in EC8_SPECTRA:
        raise ValueError("spectrum_type must be 1 or 2")
    if ground_type not in EC8_SPECTRA[spectrum_type]:
        raise ValueError("ground_type must be one of A, B, C, D, E")
    if structure not in EC8_CT:
        raise ValueError(f"structure must be one of {tuple(EC8_CT)}")
    S0, TB0, TC0, TD0 = EC8_SPECTRA[spectrum_type][ground_type]
    S = S0 if S is None else _pos("S", S)
    TB = TB0 if TB is None else _pos("TB", TB)
    TC = TC0 if TC is None else _pos("TC", TC)
    TD = TD0 if TD is None else _pos("TD", TD)
    ag_d = gamma_I * agR
    T_used = EC8_CT[structure] * H ** 0.75 if T1 is None else _pos("T1", T1)
    Sd = ec8_sd(T_used, ag_d, q, S, TB, TC, TD, beta)
    lam = 0.85 if (T_used <= 2.0 * TC and len(model.stories) > 2) else 1.0
    w = story_weights(model, weights)
    W = sum(w.values())
    Fb = Sd * W * lam
    if distribution == "height":
        shape = {s.name: s.elevation for s in model.stories}
    elif distribution == "mode":
        if not isinstance(mode_shape, dict):
            raise ValueError("distribution 'mode' needs mode_shape "
                             "{story: s_i}")
        shape = {}
        for s in model.stories:
            v = mode_shape.get(s.name, 0.0)
            if (isinstance(v, bool) or not isinstance(v, (int, float))
                    or not math.isfinite(v)):
                raise ValueError("mode_shape values must be finite numbers")
            shape[s.name] = float(v)
    else:
        raise ValueError("distribution must be 'height' or 'mode'")
    return _summary("ec8", direction, T_used, W, Fb,
                    _distribute(model, Fb, w, shape), Sd=Sd, lambda_=lam,
                    ag=ag_d, S=S, TB=TB, TC=TC, TD=TD)


# ---------------------------------------------------------------- IS 1893
def is1893_sa_g(T: float, soil: str) -> float:
    """IS 1893:2016 Fig. 2a (equivalent static method) Sa/g."""
    tc, num, tail = IS1893_SOIL[soil]
    if T <= tc:
        return 2.5
    if T <= 4.0:
        return num / T
    return tail


def is1893_rho(Z: float) -> float:
    if Z <= IS1893_RHO[0][0]:
        return IS1893_RHO[0][1]
    if Z >= IS1893_RHO[-1][0]:
        return IS1893_RHO[-1][1]
    for (z0, r0), (z1, r1) in zip(IS1893_RHO, IS1893_RHO[1:]):
        if z0 <= Z <= z1:
            return r0 + (r1 - r0) * (Z - z0) / (z1 - z0)
    return IS1893_RHO[-1][1]  # pragma: no cover


def compute_is1893(model: BuildingModel, Z: float, R: float, I: float = 1.0,
                   soil: str = "II", T: Optional[float] = None,
                   structure: str = "rc_mrf", d: Optional[float] = None,
                   damping_factor: float = 1.0, weights=None,
                   direction: str = "X") -> dict:
    h = _check_common(model, direction)
    _pos("Z", Z)
    _pos("R", R)
    _pos("I", I)
    _pos("damping_factor", damping_factor)
    if soil not in IS1893_SOIL:
        raise ValueError("soil must be one of 'I', 'II', 'III'")
    if T is not None:
        T_used = _pos("T", T)
    elif structure == "rc_mrf":
        T_used = 0.075 * h ** 0.75
    elif structure == "steel_mrf":
        T_used = 0.080 * h ** 0.75
    elif structure == "other":
        if d is None:
            lx, ly = model.plan_extents()
            d = lx if direction == "X" else ly
        T_used = 0.09 * h / math.sqrt(_pos("d", d))
    else:
        raise ValueError("structure must be 'rc_mrf', 'steel_mrf' or "
                         "'other'")
    sa_g = is1893_sa_g(T_used, soil)
    Ah = Z / 2.0 * sa_g * damping_factor / (R / I)
    if T_used <= 0.1:
        Ah = max(Ah, Z / 2.0)
    w = story_weights(model, weights)
    W = sum(w.values())
    rho = is1893_rho(Z)
    Vb = max(Ah * W, rho * W)
    shape = {s.name: s.elevation ** 2 for s in model.stories}
    return _summary("is1893", direction, T_used, W, Vb,
                    _distribute(model, Vb, w, shape), Ah=Ah, Sa_g=sa_g,
                    rho=rho)


# ------------------------------------------------------------------- user
def compute_user_coefficient(model: BuildingModel, C: float, k: float = 1.0,
                             weights=None, direction: str = "X") -> dict:
    _check_common(model, direction)
    _pos("C", C)
    _pos("k", k)
    w = story_weights(model, weights)
    W = sum(w.values())
    V = C * W
    shape = {s.name: s.elevation ** k for s in model.stories}
    return _summary("user_coefficient", direction, None, W, V,
                    _distribute(model, V, w, shape), C=C, k=k)


def compute_user_loads(model: BuildingModel, loads,
                       direction: str = "X") -> dict:
    """``loads`` = [{story, fx?, fy?}] (kN).  ``direction`` is informative."""
    if direction not in ("X", "Y"):
        raise ValueError(f"direction must be X|Y, got {direction!r}")
    if not isinstance(loads, list) or not loads:
        raise ValueError("loads must be a non-empty list of {story, fx, fy}")
    names = {s.name: s for s in model.stories}
    rows = []
    for ld in loads:
        if not isinstance(ld, dict) or ld.get("story") not in names:
            raise ValueError("each user load needs a known 'story'")
        fx, fy = ld.get("fx", 0.0), ld.get("fy", 0.0)
        for lab, v in (("fx", fx), ("fy", fy)):
            if (isinstance(v, bool) or not isinstance(v, (int, float))
                    or not math.isfinite(v)):
                raise ValueError(f"user load {lab} must be a finite number")
        s = names[ld["story"]]
        rows.append({"story": s.name, "h": s.elevation, "w": None,
                     "F": float(fx if direction == "X" else fy),
                     "fx": float(fx), "fy": float(fy)})
    V = sum(r["F"] for r in rows)
    return _summary("user_loads", direction, None, None, V, rows)


# ------------------------------------------------------- ASCE 7-22 wind
def asce22_kz(z: float, exposure: str) -> float:
    if exposure not in ASCE22_WIND_EXPOSURES:
        raise ValueError(f"exposure must be one of "
                         f"{sorted(ASCE22_WIND_EXPOSURES)}")
    alpha, zg = ASCE22_WIND_EXPOSURES[exposure]
    return 2.41 * (max(float(z), ASCE22_WIND_ZMIN) / zg) ** (2.0 / alpha)


def compute_asce7_22_wind(model: BuildingModel, V: float,
                          exposure: str = "C", Kzt: float = 1.0,
                          Kd: float = 0.85, ze: float = 0.0,
                          cp_total: float = 1.3,
                          direction: str = "X") -> dict:
    _check_common(model, direction)
    _pos("V", V)
    _pos("Kzt", Kzt)
    _pos("Kd", Kd)
    _pos("cp_total", cp_total)
    if (isinstance(ze, bool) or not isinstance(ze, (int, float))
            or not math.isfinite(ze)):
        raise ValueError("ze must be a finite number (m)")
    Ke = math.exp(-0.000119 * ze)
    lx, ly = model.plan_extents()
    width = ly if direction == "X" else lx
    if width <= 0.0:
        raise ValueError("plan width perpendicular to the wind is zero")
    heights = [s.height for s in model.stories]
    rows = []
    for i, s in enumerate(model.stories):
        trib = heights[i] / 2.0 + (heights[i + 1] / 2.0
                                   if i + 1 < len(heights) else 0.0)
        qz = 0.613 * asce22_kz(s.elevation, exposure) * Kzt * Ke * V ** 2 \
            / 1000.0                                           # kPa
        p = qz * Kd * cp_total
        rows.append({"story": s.name, "h": s.elevation, "w": None,
                     "F": p * trib * width, "qz": qz})
    return _summary("asce7_22_wind", direction, None, None,
                    sum(r["F"] for r in rows), rows, Ke=Ke, width=width)


# ---------------------------------------------------------------- dispatch
_COMPUTE = {"asce7_22": compute_asce7_22, "ec8": compute_ec8,
            "is1893": compute_is1893,
            "user_coefficient": compute_user_coefficient,
            "user_loads": compute_user_loads,
            "asce7_22_wind": compute_asce7_22_wind}
_DEFAULT_NAME = {"asce7_22": "ELF22", "ec8": "EC8", "is1893": "IS1893",
                 "user_coefficient": "UCOEF", "user_loads": "ULOADS",
                 "asce7_22_wind": "WIND22"}


def compute(model: BuildingModel, code: str, **params) -> dict:
    """Hand-calculation summary of one generator (no model change)."""
    if code not in _COMPUTE:
        raise ValueError(f"code must be one of {CODES}")
    return _COMPUTE[code](model, **params)


def generate(model: BuildingModel, code: str, name: Optional[str] = None,
             ecc: float = 0.0, **params) -> LoadPattern:
    """Compute and store the pattern (kind "wind" for the wind generator,
    else "quake"); returns the pattern."""
    summary = compute(model, code, **params)
    kind = "wind" if code == "asce7_22_wind" else "quake"
    return write_pattern(model, name or _DEFAULT_NAME[code], kind, summary,
                         ecc)


def asce7_22_elf(model, SDS, SD1, R, name="ELF22", ecc=0.0, **kw):
    return generate(model, "asce7_22", name, ecc, SDS=SDS, SD1=SD1, R=R,
                    **kw)


def ec8_lateral_force(model, ag, q, name="EC8", ecc=0.0, **kw):
    return generate(model, "ec8", name, ecc, ag=ag, q=q, **kw)


def is1893_static(model, Z, R, name="IS1893", ecc=0.0, **kw):
    return generate(model, "is1893", name, ecc, Z=Z, R=R, **kw)


def user_coefficient(model, C, k=1.0, name="UCOEF", ecc=0.0, **kw):
    return generate(model, "user_coefficient", name, ecc, C=C, k=k, **kw)


def user_loads(model, loads, name="ULOADS", ecc=0.0, **kw):
    return generate(model, "user_loads", name, ecc, loads=loads, **kw)


def asce7_22_wind(model, V, name="WIND22", ecc=0.0, **kw):
    return generate(model, "asce7_22_wind", name, ecc, V=V, **kw)


def params_from_body(body: dict):
    """(code, name, ecc, params) from a JSON request body: every key except
    ``code``/``name``/``ecc`` is passed to the generator as a keyword."""
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object")
    code = body.get("code")
    if code not in CODES:
        raise ValueError(f"'code' must be one of {CODES}")
    name = body.get("name")
    if name is not None and (not isinstance(name, str) or not name.strip()
                             or len(name) > 60):
        raise ValueError("'name' must be a non-empty string (max 60 chars)")
    ecc = body.get("ecc", 0.0)
    params = {k: v for k, v in body.items()
              if k not in ("code", "name", "ecc", "model")}
    import inspect
    allowed = set(inspect.signature(_COMPUTE[code]).parameters) - {"model"}
    bad = sorted(set(params) - allowed)
    if bad:
        raise ValueError(f"unknown parameter(s) for {code}: {bad}")
    return code, (name.strip() if name else None), ecc, params
