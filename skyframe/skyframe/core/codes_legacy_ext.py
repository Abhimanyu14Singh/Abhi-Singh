"""Legacy US auto-lateral generators needed by the CSI ETABS verification
Example 3: ASCE 7-02 equivalent lateral force and UBC 1997 wind (CONTRACT
"Benchmark pack: CSI ETABS verification examples").

Registered into :mod:`skyframe.core.autolateral` (``CODES`` / ``compute`` /
``generate`` / ``POST /api/pattern/auto-lateral``) through
``autolateral._register_ext`` -- one hook line at the end of that module
plus the self-registration at the end of this one.  SI units like every
other generator (kN, m, s; speeds m/s; pressures kPa).  Both return the
standard auto-lateral summary ``{"code", "direction", "T", "W", "V",
<coefficients>, "stories": [{"story", "h", "w", "F", ...}]}``.

``asce7_02`` -- ASCE 7-02 §9.5.5 equivalent lateral force procedure:

* ``SMS = Fa Ss``, ``SM1 = Fv S1`` with Fa / Fv from Tables 9.4.1.2.4a/b
  (identical to the later ASCE 7-05 Tables 11.4-1/2; linear between
  columns) unless given; ``SDS = 2/3 SMS``, ``SD1 = 2/3 SM1``;
* ``Ta = Ct hn^x`` (§9.5.5.3.2; Table 9.5.5.3.2 steel MRF 0.028/0.8,
  concrete MRF 0.016/0.9, EBF 0.03/0.75, other 0.02/0.75 in ft -- the SI
  values of ``seismic_codes_wavec.IBC_CT``) or a user ``Ct`` (SI, hn in
  m) with ``x``; a supplied (program) period is capped at ``Cu Ta``
  (Table 9.5.5.3.1: SD1 >= 0.4 -> 1.2, 0.3 -> 1.3, 0.2 -> 1.4, 0.15 ->
  1.5, <= 0.1 -> 1.7, linear between);
* ``Cs = SDS / (R/I)`` (9.5.5.2.1-1) ``<= SD1 / (T R/I)`` (-2), ``>=
  0.044 SDS I`` (-3) and, for S1 >= 0.6 g, ``>= 0.5 S1 / (R/I)`` (-4);
  ``V = Cs W``;
* ``F_x = V w_x h_x^k / sum w h^k`` with k = 1 (T <= 0.5 s), 2 (T >= 2.5
  s), linear between (§9.5.5.4).

``ubc97_wind`` -- UBC 1997 §1620 / Table 16-H Method 1 (normal force
method) for the primary frame: ``P = Ce Cq qs Iw`` with ``qs = 0.00256
V^2`` psf (V mph; Table 16-F) = ``0.61334 V^2`` Pa (V m/s) and Ce from
Table 16-G (exposure B / C / D; heights in ft, linear between rows, the
0-15 ft value below 15 ft).  Windward pressure at the story level
elevation z (``Cq = cq_windward``, default 0.8, Ce(z)); leeward suction
with Ce at the roof height h (``cq_leeward``, default 0.5); story force
= (p_windward + p_leeward) x the tributary facade (half the story below
+ half the story above, times the exposed ``width`` -- default the plan
width perpendicular to the wind), the SkyFrame wind-generator convention
(:mod:`skyframe.core.wind_codes_ext`).  The half story at the base goes
straight to the support.
"""
from __future__ import annotations

import math
from typing import Optional

from .autolateral import (_check_common, _distribute, _k_exponent, _pos,
                          _summary, story_weights)
from .seismic_codes_wavec import IBC_CT, _choice, _interp, \
    ibc_site_coefficients

FT = 0.3048

# ------------------------------------------------------------ ASCE 7-02
# Table 9.5.5.3.1: SD1 (ascending) -> Cu
ASCE02_CU = ((0.1, 1.7), (0.15, 1.5), (0.2, 1.4), (0.3, 1.3), (0.4, 1.2))


def asce02_cu(SD1: float) -> float:
    xs = [p[0] for p in ASCE02_CU]
    ys = [p[1] for p in ASCE02_CU]
    return _interp(SD1, xs, ys)


def compute_asce7_02(model, Ss: float, S1: float, R: float, I: float = 1.0,
                     site: str = "D", Fa: Optional[float] = None,
                     Fv: Optional[float] = None,
                     structure: str = "concrete_mrf",
                     Ct: Optional[float] = None, x: float = 0.75,
                     T: Optional[float] = None, weights=None,
                     direction: str = "X") -> dict:
    """ASCE 7-02 §9.5.5 ELF (see the module docstring)."""
    hn = _check_common(model, direction)
    for lab, v in (("Ss", Ss), ("S1", S1), ("R", R), ("I", I)):
        _pos(lab, v, allow_zero=lab in ("Ss", "S1"))
    Fa0, Fv0 = ibc_site_coefficients(Ss, S1, site)
    Fa = Fa0 if Fa is None else _pos("Fa", Fa)
    Fv = Fv0 if Fv is None else _pos("Fv", Fv)
    SMS, SM1 = Fa * Ss, Fv * S1
    SDS, SD1 = 2.0 / 3.0 * SMS, 2.0 / 3.0 * SM1
    if Ct is None:
        _choice("structure", structure, IBC_CT)
        Ct_, x_ = IBC_CT[structure]
    else:
        Ct_, x_ = _pos("Ct", Ct), _pos("x", x)
    Ta = Ct_ * hn ** x_
    Cu = asce02_cu(SD1)
    T_used = Ta if T is None else min(_pos("T", T), Cu * Ta)
    RI = R / I
    Cs0 = SDS / RI
    Cs_max = SD1 / (T_used * RI)
    Cs_min = 0.044 * SDS * I
    if S1 >= 0.6:
        Cs_min = max(Cs_min, 0.5 * S1 / RI)
    Cs = max(min(Cs0, Cs_max), Cs_min)
    w = story_weights(model, weights)
    W = sum(w.values())
    V = Cs * W
    k = _k_exponent(T_used)
    shape = {s.name: s.elevation ** k for s in model.stories}
    return _summary("asce7_02", direction, T_used, W, V,
                    _distribute(model, V, w, shape), k=k, Ta=Ta, Cu=Cu,
                    Fa=Fa, Fv=Fv, SDS=SDS, SD1=SD1, Cs=Cs, Cs_max=Cs_max,
                    Cs_min=Cs_min)


# ------------------------------------------------------------ UBC 97 wind
UBC_CE_H = (15.0, 20.0, 25.0, 30.0, 40.0, 60.0, 80.0, 100.0, 120.0, 160.0,
            200.0, 300.0, 400.0)                                 # ft
UBC_CE = {
    "D": (1.39, 1.45, 1.50, 1.54, 1.62, 1.73, 1.81, 1.88, 1.93, 2.02, 2.10,
          2.23, 2.34),
    "C": (1.06, 1.13, 1.19, 1.23, 1.31, 1.43, 1.53, 1.61, 1.67, 1.79, 1.87,
          2.05, 2.19),
    "B": (0.62, 0.67, 0.72, 0.76, 0.84, 0.95, 1.04, 1.13, 1.20, 1.31, 1.43,
          1.63, 1.80),
}
#: qs [Pa] = QS_SI * V[m/s]^2  (= 0.00256 psf/mph^2 exactly converted)
QS_SI = 0.00256 * 47.880259 / 0.44704 ** 2


def ubc_ce(z: float, exposure: str) -> float:
    """Table 16-G Ce at height z [m] (0-15 ft row below 15 ft)."""
    _choice("exposure", exposure, UBC_CE)
    return _interp(z / FT, UBC_CE_H, UBC_CE[exposure])


def compute_ubc97_wind(model, V: float, exposure: str = "B", Iw: float = 1.0,
                       cq_windward: float = 0.8, cq_leeward: float = 0.5,
                       width: Optional[float] = None,
                       direction: str = "X") -> dict:
    """UBC 1997 Method 1 wind story forces (see the module docstring)."""
    _check_common(model, direction)
    _pos("V", V)
    _pos("Iw", Iw)
    _pos("cq_windward", cq_windward, allow_zero=True)
    _pos("cq_leeward", cq_leeward, allow_zero=True)
    if width is None:
        lx, ly = model.plan_extents()
        width = ly if direction == "X" else lx
    _pos("width (exposed facade)", width)
    qs = QS_SI * V * V / 1000.0                                  # kPa
    h = max(s.elevation for s in model.stories)
    p_lee = ubc_ce(h, exposure) * cq_leeward * qs * Iw
    hs = [s.height for s in model.stories]
    rows = []
    for i, s in enumerate(model.stories):
        trib = hs[i] / 2.0 + (hs[i + 1] / 2.0 if i + 1 < len(hs) else 0.0)
        p_w = ubc_ce(s.elevation, exposure) * cq_windward * qs * Iw
        rows.append({"story": s.name, "h": s.elevation, "w": None,
                     "F": (p_w + p_lee) * trib * width, "qz": p_w,
                     "trib": trib})
    return _summary("ubc97_wind", direction, None, None,
                    sum(r["F"] for r in rows), rows, qs=qs,
                    p_leeward=p_lee, width=width, exposure=exposure)


# ------------------------------------------------------------ registration
COMPUTE = {"asce7_02": compute_asce7_02, "ubc97_wind": compute_ubc97_wind}
DEFAULT_NAME = {"asce7_02": "ELF02", "ubc97_wind": "WINDUBC"}
WIND_CODES = frozenset({"ubc97_wind"})


# register with autolateral (no-op when autolateral already did it)
from . import autolateral as _al_reg  # noqa: E402
_al_reg._register_ext(__import__("sys").modules[__name__])
