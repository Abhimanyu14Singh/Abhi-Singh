"""Wave C auto wind generators (CONTRACT "Wave C: regional seismic / wind
generators, code spectra, spectrum file import, USGS lookup and regional
material / steel libraries").

Same structure as :mod:`skyframe.core.wind_codes_ext`: story force = net
(windward + |leeward|) pressure x the tributary facade area (half the story
below + half the story above, times the plan width perpendicular to the
wind); summary ``{"code", "direction", "T": None, "W": None, "V", ...,
"stories": [{"story", "h", "w": None, "F", "qz"}]}``.  Registered through
``autolateral._register_ext`` (one-line hook at the end of autolateral).

Codes (kPa, m, m/s):

* ``bs6399_wind`` - BS 6399-2:1997 standard method: site speed ``Vs = Vb
  Sa Sd Ss Sp`` (Eq. 7), effective speed ``Ve = Vs Sb`` (Eq. 13), dynamic
  pressure ``qs = 0.613 Ve^2`` (Eq. 1, Pa), wall pressure ``p = qs Cpe
  Ca`` with the Table 5 front / rear Cpe from D/H, the dynamic
  augmentation ``(1 + Cr)`` (§1.6) and the §2.1.3.6 divergence factor on
  the front + rear sum.  Sb (Table 4: terrain, distance to the sea,
  effective height) is a user input, either one value or an
  ``[[He, Sb], ...]`` table interpolated linearly.
* ``api4f_wind`` - API Spec 4F (drilling / well-servicing structures):
  ``Vz = Vref (z/10)^(1/8)`` (elevation factor beta), ``F = 0.00338 Ki
  Vz^2 Cs A`` (US units: lbf, knots, ft^2) = ``0.611 Ki Vz^2 Cs A`` (N,
  m/s, m^2), applied to the tributary facade area.
* ``ntc2018_wind`` - Italian NTC 2018 §3.3 / CNR-DT 207: ``vb = vb0 ca``
  (zone Table 3.3.I, altitude factor), ``vr = vb cr`` (return period),
  ``qr = 1/2 rho vr^2``, exposure ``ce(z) = kr^2 ct ln(z/z0) (7 + ct
  ln(z/z0))`` (Table 3.3.II categories I-V, ce(zmin) below zmin), ``p =
  qr ce cp cd`` with cp +0.8 / -0.4 (Circolare C3.3.8.1.1).

All coefficient tables were entered from memory and are flagged in
docs/REVIEW_FLAGS.md ("Wave C"); every one can be overridden.
"""
from __future__ import annotations

import math
from typing import Optional

from .autolateral import _pos
from .wind_codes_ext import _facade, _interp, _num, _rows, _wind_summary


# ============================================================ BS 6399-2
def bs6399_cpe(D_over_H: float) -> tuple:
    """BS 6399-2 Table 5 external pressure coefficients of vertical walls
    of rectangular buildings: front +0.8 (D/H <= 1) to +0.6 (D/H >= 4),
    rear -0.3 (D/H <= 1) to -0.1 (D/H >= 4), linear between."""
    return (_interp(D_over_H, (1.0, 4.0), (0.8, 0.6)),
            _interp(D_over_H, (1.0, 4.0), (-0.3, -0.1)))


def _sb_fn(Sb):
    if isinstance(Sb, (int, float)) and not isinstance(Sb, bool):
        v = _pos("Sb", Sb)
        return lambda z: v
    if not isinstance(Sb, (list, tuple)) or len(Sb) < 2:
        raise ValueError("Sb must be a number or [[He, Sb], ...] (>= 2 rows)")
    pts = []
    for row in Sb:
        if (not isinstance(row, (list, tuple)) or len(row) != 2):
            raise ValueError("Sb table rows must be [He, Sb]")
        pts.append((_pos("Sb He", row[0], allow_zero=True),
                    _pos("Sb", row[1])))
    for a, b in zip(pts, pts[1:]):
        if not b[0] > a[0]:
            raise ValueError("Sb table heights must increase")
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return lambda z: _interp(z, xs, ys)


def compute_bs6399_wind(model, Vb: float, Sb=1.0, Sa: float = 1.0,
                        Sd: float = 1.0, Ss: float = 1.0, Sp: float = 1.0,
                        Ca: float = 1.0, Cr: float = 0.0,
                        cpe_front: Optional[float] = None,
                        cpe_rear: Optional[float] = None,
                        divergence: Optional[float] = None,
                        direction: str = "X") -> dict:
    """BS 6399-2:1997 standard method (story forces).

    * ``Vs = Vb Sa Sd Ss Sp`` (Eq. 7: altitude, direction, seasonal,
      probability factors), ``Ve = Vs Sb(He)`` (Eq. 13) with ``He`` = the
      story height above ground (Sb user value / table), ``qs = 0.613
      Ve^2`` Pa (Eq. 1).
    * Front / rear Cpe from Table 5 by D/H unless given; ``p = qs (Cpe_f -
      Cpe_r) Ca (1 + Cr)`` times the divergence factor (default 0.85 for
      H/D <= 1, 1.0 for H/D >= 5, linear between; §2.1.3.6 lack of
      correlation between front and rear faces).
    """
    fac = _facade(model, direction)
    for lab, v in (("Vb", Vb), ("Sa", Sa), ("Sd", Sd), ("Ss", Ss),
                   ("Sp", Sp), ("Ca", Ca)):
        _pos(lab, v)
    Cr = _pos("Cr", Cr, allow_zero=True)
    sb = _sb_fn(Sb)
    H, D = fac["h"], fac["L"]
    cf, cr = bs6399_cpe(D / H)
    cf = cf if cpe_front is None else _num("cpe_front", cpe_front)
    cr = cr if cpe_rear is None else _num("cpe_rear", cpe_rear)
    hd = H / D if D > 0 else 5.0
    div = (_interp(hd, (1.0, 5.0), (0.85, 1.0)) if divergence is None
           else _pos("divergence", divergence))
    Vs = Vb * Sa * Sd * Ss * Sp

    def p_fn(z):
        qs = 0.613 * (Vs * sb(z)) ** 2 / 1000.0
        return div * qs * (cf + abs(cr)) * Ca * (1.0 + Cr), qs

    rows = _rows(model, fac, p_fn)
    return _wind_summary("bs6399_wind", direction, rows, Vs=Vs,
                         cpe_front=cf, cpe_rear=cr, divergence=div,
                         width=fac["B"])


# ================================================================== API 4F
API4F_K = 0.00338 * 4.4482216152605 / (0.3048 ** 2) / (1852.0 / 3600.0) ** 2


def compute_api4f_wind(model, Vref: float, Cs: float = 1.5, Ki: float = 1.0,
                       alpha: float = 0.125, z_ref: float = 10.0,
                       direction: str = "X") -> dict:
    """API Spec 4F wind on the projected facade.

    * ``Vz = Vref (z/z_ref)^alpha`` (elevation factor beta, alpha = 1/8,
      z_ref = 10 m), z below z_ref uses z_ref.
    * ``F = 0.00338 Ki Vz^2 Cs A`` [lbf, knots, ft^2] = ``K Ki Vz^2 Cs A``
      with ``K = 0.00338 lbf/(knot^2 ft^2)`` in SI (0.611 N s^2/m^4).
    * ``Cs`` shape coefficient (1.5 flat-sided members, 0.5 cylinders;
      1.0 for a solid building facade), ``Ki`` inclination / shielding.
    """
    fac = _facade(model, direction)
    for lab, v in (("Vref", Vref), ("Cs", Cs), ("Ki", Ki), ("z_ref", z_ref)):
        _pos(lab, v)
    alpha = _pos("alpha", alpha, allow_zero=True)

    def p_fn(z):
        vz = Vref * (max(z, z_ref) / z_ref) ** alpha
        q = API4F_K * vz ** 2 / 1000.0
        return Ki * Cs * q, q

    rows = _rows(model, fac, p_fn)
    return _wind_summary("api4f_wind", direction, rows, K=API4F_K,
                         Vh=Vref * (max(fac["h"], z_ref) / z_ref) ** alpha,
                         width=fac["B"])


# ============================================================ NTC 2018 wind
# Table 3.3.I: zone -> (vb0 m/s, a0 m, ks)
NTC18_WIND_ZONES = {1: (25.0, 1000.0, 0.40), 2: (25.0, 750.0, 0.45),
                    3: (27.0, 500.0, 0.37), 4: (28.0, 500.0, 0.36),
                    5: (28.0, 750.0, 0.40), 6: (28.0, 500.0, 0.36),
                    7: (28.0, 1000.0, 0.54), 8: (30.0, 1500.0, 0.50),
                    9: (31.0, 500.0, 0.32)}
# Table 3.3.II: exposure category -> (kr, z0 m, zmin m)
NTC18_EXPOSURE = {"I": (0.17, 0.01, 2.0), "II": (0.19, 0.05, 4.0),
                  "III": (0.20, 0.10, 5.0), "IV": (0.22, 0.30, 8.0),
                  "V": (0.23, 0.70, 12.0)}


def ntc18_vb(zone: int, a_s: float) -> float:
    """Eq. 3.3.1b: ``vb = vb0 ca``, ``ca = 1`` (as <= a0), ``1 + ks (as/a0
    - 1)`` (a0 < as <= 1500 m)."""
    vb0, a0, ks = NTC18_WIND_ZONES[zone]
    ca = 1.0 if a_s <= a0 else 1.0 + ks * (a_s / a0 - 1.0)
    return vb0 * ca


def ntc18_cr(TR: float) -> float:
    """Eq. 3.3.3: ``cr = 0.75 sqrt(1 - 0.2 ln(-ln(1 - 1/TR)))`` (1.0 at
    TR = 50 years)."""
    return 0.75 * math.sqrt(1.0 - 0.2 * math.log(-math.log(1.0 - 1.0 / TR)))


def ntc18_ce(z: float, category: str, ct: float = 1.0) -> float:
    """Eq. 3.3.7 exposure coefficient (ce(zmin) for z < zmin)."""
    kr, z0, zmin = NTC18_EXPOSURE[category]
    zz = max(z, zmin)
    L = ct * math.log(zz / z0)
    return kr * kr * L * (7.0 + L)


def compute_ntc2018_wind(model, zone: int = 3, a_s: float = 0.0,
                         category: str = "III", TR: float = 50.0,
                         vb: Optional[float] = None, ct: float = 1.0,
                         cd: float = 1.0, rho: float = 1.25,
                         cp_windward: float = 0.8,
                         cp_leeward: float = -0.4,
                         direction: str = "X") -> dict:
    """Italian NTC 2018 §3.3 wind (story forces).

    * ``vb`` from the zone (Table 3.3.I) and site altitude ``a_s`` unless
      given; ``vr = vb cr(TR)``; ``qr = 0.5 rho vr^2`` (Eq. 3.3.6).
    * ``p = qr ce(z) cp cd`` (Eq. 3.3.4): windward at the story height z,
      leeward at the building height h (cp +0.8 / -0.4, Circolare
      C3.3.8.1.1), ``cd`` dynamic coefficient (1 for ordinary buildings).
    """
    fac = _facade(model, direction)
    if isinstance(zone, bool) or zone not in NTC18_WIND_ZONES:
        raise ValueError(f"zone must be one of {tuple(NTC18_WIND_ZONES)}")
    if category not in NTC18_EXPOSURE:
        raise ValueError(f"category must be one of "
                         f"{tuple(NTC18_EXPOSURE)}")
    a_s = _pos("a_s", a_s, allow_zero=True)
    for lab, v in (("TR", TR), ("ct", ct), ("cd", cd), ("rho", rho)):
        _pos(lab, v)
    if TR <= 1.0:
        raise ValueError("TR must be > 1 year")
    vb_used = ntc18_vb(zone, a_s) if vb is None else _pos("vb", vb)
    cr = ntc18_cr(TR)
    vr = vb_used * cr
    qr = 0.5 * rho * vr ** 2 / 1000.0
    cpw, cpl = _num("cp_windward", cp_windward), _num("cp_leeward",
                                                      cp_leeward)
    ce_h = ntc18_ce(fac["h"], category, ct)

    def p_fn(z):
        ce = ntc18_ce(z, category, ct)
        return qr * cd * (cpw * ce + abs(cpl) * ce_h), qr * ce

    rows = _rows(model, fac, p_fn)
    return _wind_summary("ntc2018_wind", direction, rows, vb=vb_used, cr=cr,
                         vr=vr, qr=qr, ce_h=ce_h, width=fac["B"])


# ------------------------------------------------------------ registration
COMPUTE = {"bs6399_wind": compute_bs6399_wind,
           "api4f_wind": compute_api4f_wind,
           "ntc2018_wind": compute_ntc2018_wind}
DEFAULT_NAME = {"bs6399_wind": "WINDBS", "api4f_wind": "WINDAPI",
                "ntc2018_wind": "WINDNTC"}
WIND_CODES = frozenset(COMPUTE)


from . import autolateral as _al_reg  # noqa: E402
_al_reg._register_ext(__import__("sys").modules[__name__])
