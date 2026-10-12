"""v1.19 auto wind generators (CONTRACT "Auto lateral loads: international
seismic and wind codes, ASCE 7 wind load cases").

Registered into :mod:`skyframe.core.autolateral` by a one-line hook at the
end of that module.  Every generator returns the existing auto-lateral
summary (``{"code", "direction", "T": None, "W": None, "V", ...,
"stories": [{"story", "h", "w": None, "F", "qz"}]}``) with the story
force = net (windward + leeward) pressure x the tributary facade area of
:func:`skyframe.core.builder.make_wind_pattern` (half the story below +
half the story above the level, times the plan width perpendicular to the
wind).  Towers: each tower uses its own heights and plan width (handled by
``autolateral.compute``).

Codes (pressures in kPa, lengths m, speeds m/s):

* ``asce7_16_wind`` - ASCE 7-16 Ch. 27 directional MWFRS, and the
  extended ``asce7_22_wind`` (same keyword set; with none of the new
  keywords it IS the v1.16 generator, byte-identical): topographic factor
  Kzt (§26.8.2, Fig. 26.8-1), gust-effect factor (§26.11.4 rigid /
  §26.11.5 flexible) and the four Fig. 27.3-8 load cases
  (``load_case`` 1..4, eccentric / combined; case 2 and 4 torsion through
  the pattern's accidental torsion).
* ``en1991_wind`` - EN 1991-1-4 peak velocity pressure qp(z) (§4.5),
  structural factor cs cd (§6.3.1 / Annex B), external pressure zones D/E
  (Table 7.1) with the §7.2.2 reference heights and lack-of-correlation
  factor; national parameters cdir, cseason, rho, co, kI, terrain category.
* ``is875_wind`` - IS 875 (Part 3):2015: ``Vz = Vb k1 k2 k3 k4``, ``pz =
  0.6 Vz^2``, ``pd = Kd Ka Kc pz >= 0.7 pz`` (§7.2).
* ``as1170_2_wind`` - AS/NZS 1170.2:2021: ``Vsit = VR Md Mz,cat Ms Mt``,
  ``p = 0.5 rho Vdes^2 Cfig Cdyn`` (§2.4), windward Cp,e 0.7, leeward from
  d/b (Table 5.2(B)).
* ``gb50009_wind`` - GB 50009-2012: ``wk = beta_z mu_s mu_z w0`` (Eq.
  8.1.1-1) with mu_z (Table 8.2.1) and the §8.4.3 along-wind vibration
  factor beta_z.
* Wave B: ``ts498_wind`` (Turkey TS 498, used with TSC 2018),
  ``kds41_wind`` (Korea KDS 41 10 15:2019) and ``cfe_wind`` (Mexico CFE
  MDOC-Viento / CDMX NTC-Viento).
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from .autolateral import (_check_common, _pos, _summary, asce22_ke,
                          asce22_kz, compute_asce7_22_wind)

FT = 0.3048
MPH = 0.44704


def _num(label, v):
    if (isinstance(v, bool) or not isinstance(v, (int, float))
            or not math.isfinite(v)):
        raise ValueError(f"{label} must be a finite number")
    return float(v)


def _interp(x: float, xs, ys) -> float:
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for x0, x1, y0, y1 in zip(xs, xs[1:], ys, ys[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return ys[-1]  # pragma: no cover


def _facade(model, direction: str) -> dict:
    """Plan width perpendicular to the wind (B), depth along it (L), roof
    height h and each story's tributary facade height."""
    _check_common(model, direction)
    lx, ly = model.plan_extents()
    width = ly if direction == "X" else lx
    depth = lx if direction == "X" else ly
    if width <= 0.0:
        raise ValueError("plan width perpendicular to the wind is zero")
    hs = [s.height for s in model.stories]
    trib = [hs[i] / 2.0 + (hs[i + 1] / 2.0 if i + 1 < len(hs) else 0.0)
            for i in range(len(hs))]
    h = max(s.elevation for s in model.stories)
    return {"B": width, "L": depth, "h": h, "trib": trib}


def _rows(model, fac: dict, p_fn) -> List[dict]:
    """Story rows: ``p_fn(z) -> (p_net kPa, qz kPa)``."""
    rows = []
    for i, s in enumerate(model.stories):
        p, qz = p_fn(s.elevation)
        rows.append({"story": s.name, "h": s.elevation, "w": None,
                     "F": p * fac["trib"][i] * fac["B"], "qz": qz})
    return rows


def _wind_summary(code, direction, rows, **extra) -> dict:
    return _summary(code, direction, None, None, sum(r["F"] for r in rows),
                    rows, **extra)


# =================================================================== ASCE 7
# ASCE 7-16 Table 26.11-1: exposure -> (alpha, zg ft)
ASCE16_EXPOSURES = {"B": (7.0, 1200.0), "C": (9.5, 900.0),
                    "D": (11.5, 700.0)}
# Table 26.11-1 gust constants (7-16; unchanged in 7-22):
# exposure -> (b_bar, alpha_bar, c, l ft, eps_bar, zmin ft)
ASCE_GUST = {"B": (0.45, 1 / 4.0, 0.30, 320.0, 1 / 3.0, 30.0),
             "C": (0.65, 1 / 6.5, 0.20, 500.0, 1 / 5.0, 15.0),
             "D": (0.80, 1 / 9.0, 0.15, 650.0, 1 / 8.0, 7.0)}
# Fig. 26.8-1: feature -> (K1/(H/Lh) for B, C, D; gamma; mu upwind;
# mu downwind)
ASCE_TOPO = {"ridge": ((1.30, 1.45, 1.55), 3.0, 1.5, 1.5),
             "escarpment": ((0.75, 0.85, 0.95), 2.5, 1.5, 4.0),
             "hill": ((0.95, 1.05, 1.15), 4.0, 1.5, 1.5)}
CASE4_FACTOR = 0.75 * 0.75       # Fig. 27.3-8 "0.563" = 0.75 x 0.75


def asce16_kz(z: float, exposure: str) -> float:
    """ASCE 7-16 Table 26.10-1: ``Kz = 2.01 (z/zg)^(2/alpha)`` for 15 ft
    <= z <= zg, ``2.01 (15/zg)^(2/alpha)`` below 15 ft."""
    if exposure not in ASCE16_EXPOSURES:
        raise ValueError(f"exposure must be one of "
                         f"{sorted(ASCE16_EXPOSURES)}")
    alpha, zg = ASCE16_EXPOSURES[exposure]
    zz = min(max(float(z) / FT, 15.0), zg)
    return 2.01 * (zz / zg) ** (2.0 / alpha)


def asce_kzt(z: float, feature: str, H: float, Lh: float, x: float,
             exposure: str, upwind: bool = True) -> dict:
    """ASCE 7-16/7-22 §26.8.2 topographic factor ``Kzt = (1 + K1 K2
    K3)^2`` (Fig. 26.8-1).  ``H``, ``Lh``, ``x`` (distance from the crest)
    and ``z`` in m.  For H/Lh > 0.5, H/Lh = 0.5 for K1 and Lh = 2H for K2,
    K3.  §26.8.1: Kzt = 1 unless H/Lh >= 0.2 and H >= 15 ft (exposure C,
    D) / 60 ft (exposure B)."""
    if feature not in ASCE_TOPO:
        raise ValueError(f"topo_feature must be one of {tuple(ASCE_TOPO)}")
    k1f, gamma, mu_up, mu_dn = ASCE_TOPO[feature]
    ratio = H / Lh
    hmin = (60.0 if exposure == "B" else 15.0) * FT
    if ratio < 0.2 or H < hmin:
        return {"Kzt": 1.0, "applies": False, "K1": 0.0, "K2": 0.0,
                "K3": 0.0}
    Lh_eff = Lh
    if ratio > 0.5:
        ratio, Lh_eff = 0.5, 2.0 * H
    K1 = k1f["BCD".index(exposure)] * ratio
    mu = mu_up if upwind else mu_dn
    K2 = max(1.0 - abs(x) / (mu * Lh_eff), 0.0)
    K3 = math.exp(-gamma * z / Lh_eff)
    return {"Kzt": (1.0 + K1 * K2 * K3) ** 2, "applies": True, "K1": K1,
            "K2": K2, "K3": K3}


def _rl(eta: float) -> float:
    if eta <= 0.0:
        return 1.0
    return 1.0 / eta - (1.0 - math.exp(-2.0 * eta)) / (2.0 * eta ** 2)


def asce_gust(exposure: str, h: float, B: float, L: float, V: float,
              n1: Optional[float] = None, beta: float = 0.02) -> dict:
    """ASCE 7-16/7-22 §26.11 gust-effect factor.  ``h``, ``B`` (normal to
    the wind), ``L`` (along) in m, ``V`` in m/s, ``n1`` in Hz.

    * Rigid (n1 None or >= 1 Hz, §26.11.4, Eq. 26.11-6): ``G = 0.925 (1 +
      1.7 gQ Iz Q)/(1 + 1.7 gv Iz)``, gQ = gv = 3.4, ``Iz = c
      (33/zbar)^(1/6)`` (Eq. 26.11-7), ``zbar = max(0.6 h, zmin)``,
      ``Q = sqrt(1/(1 + 0.63 ((B + h)/Lz)^0.63))`` (26.11-8), ``Lz = l
      (zbar/33)^eps`` (26.11-9) - feet.
    * Flexible (n1 < 1 Hz, §26.11.5, Eq. 26.11-10): ``Gf = 0.925 (1 + 1.7
      Iz sqrt(gQ^2 Q^2 + gR^2 R^2))/(1 + 1.7 gv Iz)`` with gR (26.11-11),
      ``R = sqrt(Rn Rh RB (0.53 + 0.47 RL)/beta)`` (26.11-12), ``Rn =
      7.47 N1/(1 + 10.3 N1)^(5/3)``, ``N1 = n1 Lz/Vz``, ``Rl = 1/eta -
      (1 - e^(-2 eta))/(2 eta^2)`` with eta = 4.6 n1 h/Vz (Rh), 4.6 n1
      B/Vz (RB), 15.4 n1 L/Vz (RL), ``Vz = b (zbar/33)^alpha (88/60) V``
      (26.11-16, V in mph, Vz ft/s).
    """
    b_bar, a_bar, c, l_ft, eps, zmin = ASCE_GUST[exposure]
    h_ft, B_ft, L_ft = h / FT, B / FT, L / FT
    zbar = max(0.6 * h_ft, zmin)
    Iz = c * (33.0 / zbar) ** (1.0 / 6.0)
    Lz = l_ft * (zbar / 33.0) ** eps
    Q = math.sqrt(1.0 / (1.0 + 0.63 * ((B_ft + h_ft) / Lz) ** 0.63))
    gQ = gv = 3.4
    out = {"Iz": Iz, "Lz": Lz * FT, "Q": Q, "zbar": zbar * FT}
    if n1 is None or n1 >= 1.0:
        out["G"] = 0.925 * (1.0 + 1.7 * gQ * Iz * Q) / (1.0 + 1.7 * gv * Iz)
        out["gust"] = "rigid"
        return out
    Vz = b_bar * (zbar / 33.0) ** a_bar * (88.0 / 60.0) * (V / MPH)
    N1 = n1 * Lz / Vz
    Rn = 7.47 * N1 / (1.0 + 10.3 * N1) ** (5.0 / 3.0)
    Rh = _rl(4.6 * n1 * h_ft / Vz)
    RB = _rl(4.6 * n1 * B_ft / Vz)
    RL = _rl(15.4 * n1 * L_ft / Vz)
    R = math.sqrt(Rn * Rh * RB * (0.53 + 0.47 * RL) / beta)
    lg = math.sqrt(2.0 * math.log(3600.0 * n1))
    gR = lg + 0.577 / lg
    out.update(G=0.925 * (1.0 + 1.7 * Iz * math.sqrt(gQ ** 2 * Q ** 2
                                                    + gR ** 2 * R ** 2))
               / (1.0 + 1.7 * gv * Iz), gust="flexible", R=R, gR=gR,
               Vz=Vz * FT, N1=N1, Rn=Rn, Rh=Rh, RB=RB, RL=RL)
    return out


def asce_cp_leeward(L_over_B: float) -> float:
    """Fig. 27.3-1 leeward wall Cp: -0.5 (L/B <= 1), -0.3 (2), -0.2
    (>= 4), linear between."""
    return _interp(L_over_B, (1.0, 2.0, 4.0), (-0.5, -0.3, -0.2))


def _asce_direction(model, edition: str, direction: str, V: float,
                    exposure: str, Kzt: float, topo: Optional[dict],
                    Kd: float, Ke: float, G: Optional[float],
                    gust: Optional[str], n1, damping: float,
                    cp_total: Optional[float], cp_windward, cp_leeward
                    ) -> dict:
    """One wind direction: rows + coefficients (no load-case factor)."""
    fac = _facade(model, direction)
    kz = asce16_kz if edition == "7-16" else asce22_kz

    def kzt(z):
        if topo is None:
            return Kzt
        return asce_kzt(z, topo["feature"], topo["H"], topo["Lh"],
                        topo["x"], exposure, topo["upwind"])["Kzt"]

    extra = {}
    if gust is not None:
        g = asce_gust(exposure, fac["h"], fac["B"], fac["L"], V,
                      n1 if gust == "flexible" else None, damping)
        if gust == "flexible" and (n1 is None or n1 >= 1.0):
            raise ValueError("gust 'flexible' needs n1 < 1 Hz (§26.2)")
        G = g["G"]
        extra["gust_factor"] = g
    elif G is None:
        G = 0.85 if edition == "7-16" else 1.0
    split = cp_total is None
    if split:
        cpw = 0.8 if cp_windward is None else cp_windward
        cpl = (asce_cp_leeward(fac["L"] / fac["B"]) if cp_leeward is None
               else cp_leeward)
    qh = 0.613 * kz(fac["h"], exposure) * kzt(fac["h"]) * Ke * V ** 2 \
        / 1000.0

    def p_fn(z):
        qz = 0.613 * kz(z, exposure) * kzt(z) * Ke * V ** 2 / 1000.0
        if split:
            return Kd * G * (qz * cpw + qh * abs(cpl)), qz
        return qz * Kd * G * cp_total, qz

    rows = _rows(model, fac, p_fn)
    extra.update(G=G, qh=qh, width=fac["B"], depth=fac["L"])
    if split:
        extra.update(cp_windward=cpw, cp_leeward=cpl)
    if topo is not None:
        extra["Kzt_h"] = kzt(fac["h"])
    return {"rows": rows, "extra": extra}


def _topo_dict(feature, H, Lh, x, upwind):
    if feature is None:
        if any(v is not None for v in (H, Lh, x)):
            raise ValueError("give topo_feature with topo_H / topo_Lh")
        return None
    return {"feature": feature, "H": _pos("topo_H", H),
            "Lh": _pos("topo_Lh", Lh),
            "x": 0.0 if x is None else _num("topo_x", x),
            "upwind": bool(upwind)}


def compute_asce7_wind(model, edition: str, V: float,
                       exposure: str = "C", Kzt: float = 1.0,
                       Kd: float = 0.85, ze: float = 0.0,
                       cp_total: Optional[float] = None,
                       direction: str = "X", Ke: Optional[float] = None,
                       G: Optional[float] = None,
                       cp_windward: Optional[float] = None,
                       cp_leeward: Optional[float] = None,
                       load_case: int = 1, ecc_sign: int = 1,
                       transverse_sign: int = 1, e_ratio: float = 0.15,
                       topo_feature: Optional[str] = None,
                       topo_H: Optional[float] = None,
                       topo_Lh: Optional[float] = None,
                       topo_x: Optional[float] = None,
                       topo_upwind: bool = True,
                       gust: Optional[str] = None,
                       n1: Optional[float] = None,
                       damping: float = 0.02) -> dict:
    """ASCE 7-16 / 7-22 Ch. 27 directional MWFRS with the Fig. 27.3-8
    load cases.

    * Velocity pressure: 7-16 ``qz = 0.613 Kz Kzt Kd Ke V^2`` (Eq.
      26.10-1.SI, Kz Table 26.10-1 with the 7-16 Table 26.11-1 alpha/zg);
      7-22 ``qz = 0.613 Kz Kzt Ke V^2`` with Kd in the pressure (same net
      pressure).  ``Ke`` from ``ze`` (Table 26.9-1) unless given.
    * Kzt: constant ``Kzt`` or the §26.8.2 topographic factor per story
      height (``topo_feature`` ridge|escarpment|hill, ``topo_H``,
      ``topo_Lh``, ``topo_x``, ``topo_upwind``).
    * G: given, else ``gust`` "rigid" (§26.11.4) / "flexible" (§26.11.5,
      ``n1`` Hz, ``damping`` ratio), else 0.85 (7-16) or 1.0 (7-22,
      v1.16 convention with cp_total 1.3).
    * Pressure: split (default for 7-16) ``p = Kd G (qz Cp_w + qh
      |Cp_l|)`` with Cp_w 0.8 and Cp_l from L/B (Fig. 27.3-1) unless
      given; or combined ``p = qz Kd G cp_total``.
    * ``load_case`` (Fig. 27.3-8): 1 = full P in ``direction``; 2 = 0.75 P
      with torsion ``MT = 0.75 P B e``, e = ``ecc_sign`` x ``e_ratio`` (0.15)
      x B (realized as the pattern's accidental torsion, ``case_ecc``);
      3 = 0.75 Px + 0.75 Py (``transverse_sign`` = sign of the Y
      component); 4 = 0.563 Px + 0.563 Py (0.75 x 0.75) with ``MT = 0.563
      (Px Bx + Py By) e`` - the two torques ADD (``case_ecc_y`` carries
      the Y-component eccentricity sign when the Y component is negative).
      Rows of cases 3/4 carry ``fx`` and ``fy``; ``F`` is the
      ``direction`` component.
    """
    if edition not in ("7-16", "7-22"):
        raise ValueError("edition must be '7-16' or '7-22'")
    _check_common(model, direction)
    _pos("V", V)
    _pos("Kzt", Kzt)
    _pos("Kd", Kd)
    _pos("damping", damping)
    _pos("e_ratio", e_ratio, allow_zero=True)
    ze = _num("ze", ze)
    if cp_total is not None:
        _pos("cp_total", cp_total)
        if cp_windward is not None or cp_leeward is not None:
            raise ValueError("give cp_total OR cp_windward/cp_leeward")
    for lab, v in (("cp_windward", cp_windward), ("cp_leeward", cp_leeward)):
        if v is not None:
            _num(lab, v)
    if G is not None:
        _pos("G", G)
        if gust is not None:
            raise ValueError("give G OR gust, not both")
    if gust not in (None, "rigid", "flexible"):
        raise ValueError("gust must be 'rigid' or 'flexible'")
    if n1 is not None:
        _pos("n1", n1)
    if load_case not in (1, 2, 3, 4) or isinstance(load_case, bool):
        raise ValueError("load_case must be 1, 2, 3 or 4 (Fig. 27.3-8)")
    for lab, v in (("ecc_sign", ecc_sign),
                   ("transverse_sign", transverse_sign)):
        if v not in (1, -1) or isinstance(v, bool):
            raise ValueError(f"{lab} must be +1 or -1")
    if exposure not in ASCE16_EXPOSURES:
        raise ValueError(f"exposure must be one of "
                         f"{sorted(ASCE16_EXPOSURES)}")
    topo = _topo_dict(topo_feature, topo_H, topo_Lh, topo_x, topo_upwind)
    if topo is not None and Kzt != 1.0:
        raise ValueError("give Kzt OR the topo_* parameters, not both")
    Ke = asce22_ke(ze) if Ke is None else _pos("Ke", Ke)
    args = (V, exposure, Kzt, topo, Kd, Ke, G, gust, n1, damping, cp_total,
            cp_windward, cp_leeward)
    code = "asce7_16_wind" if edition == "7-16" else "asce7_22_wind"
    other = "Y" if direction == "X" else "X"
    main = _asce_direction(model, edition, direction, *args)
    e = ecc_sign * e_ratio
    extra = {"edition": edition, "load_case": load_case, "Ke": Ke,
             **main["extra"]}
    rows = main["rows"]
    if load_case in (1, 2):
        f = 1.0 if load_case == 1 else 0.75
        out = [dict(r, F=f * r["F"]) for r in rows]
        if load_case == 2:
            extra["case_ecc"] = e
            for r in out:
                r["Mz"] = r["F"] * e * main["extra"]["width"]
        return _wind_summary(code, direction, out, **extra)
    sec = _asce_direction(model, edition, other, *args)
    f = 0.75 if load_case == 3 else CASE4_FACTOR
    out = []
    for r, r2 in zip(rows, sec["rows"]):
        main_f = f * r["F"]
        sec_f = f * r2["F"] * transverse_sign
        fx, fy = (main_f, sec_f) if direction == "X" else (sec_f, main_f)
        row = dict(r, F=main_f, fx=fx, fy=fy)
        if load_case == 4:
            row["Mz"] = e * (abs(main_f) * main["extra"]["width"]
                             + abs(sec_f) * sec["extra"]["width"])
        out.append(row)
    extra["transverse"] = {"direction": other, "sign": transverse_sign,
                           "G": sec["extra"]["G"],
                           "width": sec["extra"]["width"],
                           "V": sum(f * r2["F"] for r2 in sec["rows"])
                           * transverse_sign}
    if load_case == 4:
        # engine torque = ecc fx Ly + ecc_y fy Lx; ecc_y = ecc * sign(fy)
        # so that both components add (MT = 0.563 (Px Bx + Py By) e).
        sy = transverse_sign if direction == "X" else 1
        sx = 1 if direction == "X" else transverse_sign
        extra["case_ecc"] = e * sx
        if sx != sy:
            extra["case_ecc_y"] = e * sy
    return _wind_summary(code, direction, out, **extra)


def compute_asce7_16_wind(model, V: float, exposure: str = "C",
                          Kzt: float = 1.0, Kd: float = 0.85,
                          ze: float = 0.0, cp_total: Optional[float] = None,
                          direction: str = "X", Ke: Optional[float] = None,
                          G: Optional[float] = None,
                          cp_windward: Optional[float] = None,
                          cp_leeward: Optional[float] = None,
                          load_case: int = 1, ecc_sign: int = 1,
                          transverse_sign: int = 1, e_ratio: float = 0.15,
                          topo_feature: Optional[str] = None,
                          topo_H: Optional[float] = None,
                          topo_Lh: Optional[float] = None,
                          topo_x: Optional[float] = None,
                          topo_upwind: bool = True,
                          gust: Optional[str] = None,
                          n1: Optional[float] = None,
                          damping: float = 0.02) -> dict:
    """ASCE 7-16 directional wind (see :func:`compute_asce7_wind`)."""
    return compute_asce7_wind(**{k: v for k, v in locals().items()},
                              edition="7-16")


_V116_22 = ("V", "exposure", "Kzt", "Kd", "ze", "cp_total", "direction",
            "Ke", "G", "cp_windward", "cp_leeward")


def compute_asce7_22_wind_ext(model, V: float, exposure: str = "C",
                              Kzt: float = 1.0, Kd: float = 0.85,
                              ze: float = 0.0, cp_total: float = 1.3,
                              direction: str = "X",
                              Ke: Optional[float] = None, G: float = 1.0,
                              cp_windward: Optional[float] = None,
                              cp_leeward: Optional[float] = None,
                              load_case: int = 1, ecc_sign: int = 1,
                              transverse_sign: int = 1,
                              e_ratio: float = 0.15,
                              topo_feature: Optional[str] = None,
                              topo_H: Optional[float] = None,
                              topo_Lh: Optional[float] = None,
                              topo_x: Optional[float] = None,
                              topo_upwind: bool = True,
                              gust: Optional[str] = None,
                              n1: Optional[float] = None,
                              damping: float = 0.02) -> dict:
    """``asce7_22_wind`` with the v1.19 keywords.  Without load_case > 1,
    topography or gust it delegates to the v1.16
    :func:`autolateral.compute_asce7_22_wind` (identical results)."""
    if (load_case == 1 and topo_feature is None and gust is None
            and topo_H is None and topo_Lh is None and topo_x is None):
        return compute_asce7_22_wind(
            model, V, exposure=exposure, Kzt=Kzt, Kd=Kd, ze=ze,
            cp_total=cp_total, direction=direction, Ke=Ke, G=G,
            cp_windward=cp_windward, cp_leeward=cp_leeward)
    split = cp_windward is not None or cp_leeward is not None
    if split and (cp_windward is None or cp_leeward is None):
        raise ValueError("give both cp_windward and cp_leeward")
    return compute_asce7_wind(
        model, "7-22", V, exposure=exposure, Kzt=Kzt, Kd=Kd, ze=ze,
        cp_total=None if split else cp_total, direction=direction, Ke=Ke,
        G=None if gust is not None else G, cp_windward=cp_windward,
        cp_leeward=cp_leeward, load_case=load_case, ecc_sign=ecc_sign,
        transverse_sign=transverse_sign, e_ratio=e_ratio,
        topo_feature=topo_feature, topo_H=topo_H, topo_Lh=topo_Lh,
        topo_x=topo_x, topo_upwind=topo_upwind, gust=gust, n1=n1,
        damping=damping)


# Fig. 27.3-8 pattern set: (suffix, direction, load_case, ecc_sign,
# transverse_sign)
ASCE_CASE_SET = (
    ("1X", "X", 1, 1, 1), ("1Y", "Y", 1, 1, 1),
    ("2X+e", "X", 2, 1, 1), ("2X-e", "X", 2, -1, 1),
    ("2Y+e", "Y", 2, 1, 1), ("2Y-e", "Y", 2, -1, 1),
    ("3X+Y", "X", 3, 1, 1), ("3X-Y", "X", 3, 1, -1),
    ("4X+Y+e", "X", 4, 1, 1), ("4X+Y-e", "X", 4, -1, 1),
    ("4X-Y+e", "X", 4, 1, -1), ("4X-Y-e", "X", 4, -1, -1))


def generate_asce7_wind_cases(model, code: str = "asce7_22_wind",
                              base_name: str = "W", **params) -> list:
    """Write the 12 Fig. 27.3-8 patterns ``<base>-<suffix>`` (cases 1..4,
    both directions / signs); returns the patterns."""
    from . import autolateral as al
    if code not in ("asce7_16_wind", "asce7_22_wind"):
        raise ValueError("code must be asce7_16_wind or asce7_22_wind")
    for k in ("direction", "load_case", "ecc_sign", "transverse_sign"):
        params.pop(k, None)
    out = []
    for suf, d, lc, es, ts in ASCE_CASE_SET:
        out.append(al.generate(model, code, f"{base_name}-{suf}", 0.0,
                               direction=d, load_case=lc, ecc_sign=es,
                               transverse_sign=ts, **params))
    return out


# ------------------------------------------- load-case torsion (hooks)
def case_torsion(summary: dict, ecc: float):
    """``autolateral.generate`` hook -> ``(ecc, ecc_y)``: the Fig. 27.3-8
    case 2/4 eccentricity becomes the pattern's accidental torsion unless
    the user gave an explicit ``ecc`` (then ``ecc_y`` stays None)."""
    if not ecc and summary.get("case_ecc"):
        return summary["case_ecc"], summary.get("case_ecc_y")
    return ecc, None


def pattern_ecc_y(pat, ecc: float) -> float:
    """Engine hook: eccentricity ratio for the fy story torque (the
    pattern ``ecc`` unless ``ecc_y`` is set)."""
    ey = getattr(pat, "ecc_y", None)
    return ecc if ey is None else ey


def validate_pattern_ecc_y(pat) -> None:
    ey = getattr(pat, "ecc_y", None)
    if ey is None:
        return
    if (isinstance(ey, bool) or not isinstance(ey, (int, float))
            or not math.isfinite(ey) or abs(ey) > 0.5):
        raise ValueError(f"Pattern {pat.name!r}: ecc_y must be a finite "
                         f"value with |ecc_y| <= 0.5 (got {ey!r})")


# ============================================================ EN 1991-1-4
# Table 4.1: terrain category -> (z0 m, zmin m)
EN_TERRAIN = {"0": (0.003, 1.0), "I": (0.01, 1.0), "II": (0.05, 2.0),
              "III": (0.3, 5.0), "IV": (1.0, 10.0)}


def en_qp(z: float, vb: float, terrain: str, rho: float = 1.25,
          co: float = 1.0, kI: float = 1.0) -> dict:
    """EN 1991-1-4 §4.3-§4.5 peak velocity pressure (kPa) at z (m):
    ``kr = 0.19 (z0/0.05)^0.07`` (4.5), ``cr = kr ln(z/z0)`` (4.4), ``vm =
    cr co vb`` (4.3), ``Iv = kI/(co ln(z/z0))`` (4.7), ``qp = (1 + 7 Iv)
    0.5 rho vm^2`` (4.8); z < zmin uses zmin."""
    z0, zmin = EN_TERRAIN[terrain]
    zz = max(z, zmin)
    kr = 0.19 * (z0 / 0.05) ** 0.07
    cr = kr * math.log(zz / z0)
    vm = cr * co * vb
    Iv = kI / (co * math.log(zz / z0))
    qp = (1.0 + 7.0 * Iv) * 0.5 * rho * vm ** 2 / 1000.0
    return {"qp": qp, "vm": vm, "Iv": Iv, "cr": cr, "kr": kr}


def en_cscd(h: float, b: float, vb: float, terrain: str, n1: float,
            delta: float, rho: float = 1.25, co: float = 1.0,
            kI: float = 1.0) -> dict:
    """EN 1991-1-4 §6.3.1 / Annex B structural factor (procedure 1):
    ``zs = max(0.6 h, zmin)`` (Fig. 6.1), ``L(zs) = 300 (zs/200)^a``, ``a
    = 0.67 + 0.05 ln z0`` (B.1), ``B^2 = 1/(1 + 0.9 ((b + h)/L)^0.63)``
    (B.3), ``fL = n1 L/vm`` (B.2), ``SL = 6.8 fL/(1 + 10.2 fL)^(5/3)``,
    ``R^2 = pi^2/(2 delta) SL Rh Rb`` (B.6) with Rl(eta) (B.7/B.8),
    eta_h = 4.6 h fL/L, eta_b = 4.6 b fL/L; ``nu = max(n1 sqrt(R^2/(B^2 +
    R^2)), 0.08)`` (B.5), ``kp = max(sqrt(2 ln(nu T)) + 0.6/sqrt(2 ln(nu
    T)), 3)`` (B.4, T = 600 s); ``cs = (1 + 7 Iv B)/(1 + 7 Iv)``, ``cd =
    (1 + 2 kp Iv sqrt(B^2 + R^2))/(1 + 7 Iv B)`` (6.2/6.3)."""
    z0, zmin = EN_TERRAIN[terrain]
    zs = max(0.6 * h, zmin)
    a = 0.67 + 0.05 * math.log(z0)
    L = 300.0 * (zs / 200.0) ** a
    q = en_qp(zs, vb, terrain, rho, co, kI)
    Iv, vm = q["Iv"], q["vm"]
    B2 = 1.0 / (1.0 + 0.9 * ((b + h) / L) ** 0.63)
    fL = n1 * L / vm
    SL = 6.8 * fL / (1.0 + 10.2 * fL) ** (5.0 / 3.0)
    Rh = _rl(4.6 * h * fL / L)
    Rb = _rl(4.6 * b * fL / L)
    R2 = math.pi ** 2 / (2.0 * delta) * SL * Rh * Rb
    nu = max(n1 * math.sqrt(R2 / (B2 + R2)), 0.08)
    lg = math.sqrt(2.0 * math.log(nu * 600.0))
    kp = max(lg + 0.6 / lg, 3.0)
    cs = (1.0 + 7.0 * Iv * math.sqrt(B2)) / (1.0 + 7.0 * Iv)
    cd = ((1.0 + 2.0 * kp * Iv * math.sqrt(B2 + R2))
          / (1.0 + 7.0 * Iv * math.sqrt(B2)))
    return {"cscd": cs * cd, "cs": cs, "cd": cd, "zs": zs, "Lzs": L,
            "B2": B2, "R2": R2, "kp": kp, "nu": nu, "Iv_zs": Iv}


def en_ze(z: float, h: float, b: float) -> float:
    """EN 1991-1-4 §7.2.2 Fig. 7.4 windward reference height ze."""
    if h <= b:
        return h
    if h <= 2.0 * b:
        return b if z <= b else h
    if z <= b:
        return b
    if z >= h - b:
        return h
    return z


def compute_en1991_wind(model, vb0: float, terrain: str = "II",
                        cdir: float = 1.0, cseason: float = 1.0,
                        rho: float = 1.25, co: float = 1.0, kI: float = 1.0,
                        cpe_D: Optional[float] = None,
                        cpe_E: Optional[float] = None,
                        cscd: Optional[float] = None,
                        n1: Optional[float] = None, delta: float = 0.1,
                        correlation: bool = True, ze_rule: str = "fig7_4",
                        direction: str = "X") -> dict:
    """EN 1991-1-4 building wind (story forces).

    * ``vb = cdir cseason vb0`` (Eq. 4.1); qp(z) per :func:`en_qp`.
    * External pressure, zones D (windward) / E (leeward), Table 7.1:
      h/d <= 0.25: +0.7 / -0.3, 1: +0.8 / -0.5, >= 5: +0.8 / -0.7 (linear
      between) unless given.  Windward at ``ze`` (Fig. 7.4, or the story
      height with ``ze_rule`` "story"), leeward at ze = h.
    * Lack of correlation (§7.2.2(3) note): 0.85 for h/d <= 1, 1.0 for
      h/d >= 5, linear between (``correlation`` False -> 1).
    * Structural factor cs cd: given; else Annex B from ``n1`` (Hz) and
      the logarithmic decrement ``delta``; else 1.0 (§6.2(1)).
    * ``F = cs cd f_corr (cpe_D qp(ze) + |cpe_E| qp(h)) A_trib``.
    """
    fac = _facade(model, direction)
    if str(terrain) not in EN_TERRAIN:
        raise ValueError(f"terrain must be one of {tuple(EN_TERRAIN)}")
    terrain = str(terrain)
    for lab, v in (("vb0", vb0), ("cdir", cdir), ("cseason", cseason),
                   ("rho", rho), ("co", co), ("kI", kI),
                   ("delta", delta)):
        _pos(lab, v)
    if ze_rule not in ("fig7_4", "story"):
        raise ValueError("ze_rule must be 'fig7_4' or 'story'")
    vb = cdir * cseason * vb0
    h, b, d = fac["h"], fac["B"], fac["L"]
    hd = h / d if d > 0 else 5.0
    cD = (_interp(hd, (0.25, 1.0, 5.0), (0.7, 0.8, 0.8)) if cpe_D is None
          else _num("cpe_D", cpe_D))
    cE = (_interp(hd, (0.25, 1.0, 5.0), (-0.3, -0.5, -0.7))
          if cpe_E is None else _num("cpe_E", cpe_E))
    fcorr = _interp(hd, (1.0, 5.0), (0.85, 1.0)) if correlation else 1.0
    extra = {}
    if cscd is not None:
        cscd = _pos("cscd", cscd)
    elif n1 is not None:
        sf = en_cscd(h, b, vb, terrain, _pos("n1", n1), delta, rho, co, kI)
        cscd = sf["cscd"]
        extra["structural_factor"] = sf
    else:
        cscd = 1.0
    qh = en_qp(h, vb, terrain, rho, co, kI)["qp"]

    def p_fn(z):
        ze = en_ze(z, h, b) if ze_rule == "fig7_4" else z
        qz = en_qp(ze, vb, terrain, rho, co, kI)["qp"]
        return cscd * fcorr * (cD * qz + abs(cE) * qh), qz

    rows = _rows(model, fac, p_fn)
    return _wind_summary("en1991_wind", direction, rows, vb=vb, qh=qh,
                         cpe_D=cD, cpe_E=cE, f_corr=fcorr, cscd=cscd,
                         h_over_d=hd, width=b, **extra)


# ============================================================ IS 875-3
# IS 875 (Part 3):2015 Table 2 k2 by height (m), terrain category 1..4
IS875_Z = (10, 15, 20, 30, 50, 100, 150, 200, 250, 300, 350, 400, 450, 500)
IS875_K2 = {
    1: (1.05, 1.09, 1.12, 1.15, 1.20, 1.26, 1.30, 1.32, 1.34, 1.35, 1.35,
        1.35, 1.35, 1.35),
    2: (1.00, 1.05, 1.07, 1.12, 1.17, 1.24, 1.28, 1.30, 1.32, 1.34, 1.35,
        1.35, 1.35, 1.35),
    3: (0.91, 0.97, 1.01, 1.06, 1.12, 1.20, 1.24, 1.27, 1.29, 1.31, 1.32,
        1.33, 1.33, 1.34),
    4: (0.80, 0.80, 0.80, 0.97, 1.10, 1.20, 1.24, 1.27, 1.28, 1.30, 1.31,
        1.32, 1.33, 1.34)}


def is875_k2(z: float, terrain: int) -> float:
    """Table 2 k2 (linear between heights; below 10 m the 10 m value)."""
    return _interp(z, IS875_Z, IS875_K2[terrain])


def compute_is875_wind(model, Vb: float, terrain: int = 2, k1: float = 1.0,
                       k3: float = 1.0, k4: float = 1.0, Kd: float = 0.9,
                       Ka: float = 1.0, Kc: float = 0.9,
                       cp_windward: float = 0.8, cp_leeward: float = -0.5,
                       direction: str = "X") -> dict:
    """IS 875 (Part 3):2015 building wind.

    * ``Vz = Vb k1 k2 k3 k4`` (§6.3; k1 risk Table 1, k2 terrain/height
      Table 2, k3 topography §6.3.3, k4 importance for cyclonic regions
      §6.3.4); ``pz = 0.6 Vz^2`` (N/m^2, §7.2).
    * ``pd = Kd Ka Kc pz`` but not less than ``0.7 pz`` (§7.2; Kd 0.9
      buildings, Ka Table 4, Kc Table 5).
    * ``F = (Cp_w pd(z) + |Cp_l| pd(h)) A_trib`` (windward at z, leeward
      at the roof height h; internal pressure cancels).
    """
    fac = _facade(model, direction)
    if terrain not in IS875_K2 or isinstance(terrain, bool):
        raise ValueError("terrain must be 1, 2, 3 or 4")
    for lab, v in (("Vb", Vb), ("k1", k1), ("k3", k3), ("k4", k4),
                   ("Kd", Kd), ("Ka", Ka), ("Kc", Kc)):
        _pos(lab, v)
    cpw, cpl = _num("cp_windward", cp_windward), _num("cp_leeward",
                                                      cp_leeward)

    def pd(z):
        Vz = Vb * k1 * is875_k2(z, terrain) * k3 * k4
        pz = 0.6 * Vz ** 2 / 1000.0
        return max(Kd * Ka * Kc * pz, 0.7 * pz), pz

    pd_h = pd(fac["h"])[0]

    def p_fn(z):
        p, pz = pd(z)
        return cpw * p + abs(cpl) * pd_h, p

    rows = _rows(model, fac, p_fn)
    return _wind_summary("is875_wind", direction, rows, pd_h=pd_h,
                         width=fac["B"])


# ========================================================= AS/NZS 1170.2
# AS/NZS 1170.2:2021 Table 4.1 Mz,cat (non-cyclonic), heights m
AS2_Z = (3, 5, 10, 15, 20, 30, 40, 50, 75, 100, 150, 200)
AS2_MZ = {
    "1": (0.99, 1.05, 1.12, 1.16, 1.19, 1.22, 1.24, 1.25, 1.27, 1.29, 1.31,
          1.32),
    "2": (0.91, 0.91, 1.00, 1.05, 1.08, 1.12, 1.16, 1.18, 1.22, 1.24, 1.27,
          1.29),
    "2.5": (0.87, 0.87, 0.92, 0.97, 1.01, 1.06, 1.10, 1.13, 1.17, 1.20,
            1.24, 1.27),
    "3": (0.83, 0.83, 0.83, 0.89, 0.94, 1.00, 1.04, 1.07, 1.12, 1.16, 1.21,
          1.24),
    "4": (0.75, 0.75, 0.75, 0.75, 0.75, 0.80, 0.85, 0.90, 0.98, 1.03, 1.11,
          1.16)}


def as2_mz(z: float, terrain: str) -> float:
    return _interp(z, AS2_Z, AS2_MZ[terrain])


def compute_as1170_2_wind(model, VR: float, terrain: str = "2",
                          Md: float = 1.0, Ms: float = 1.0, Mt: float = 1.0,
                          cpe_windward: float = 0.7,
                          cpe_leeward: Optional[float] = None,
                          Ka: float = 1.0, Kc: float = 1.0,
                          Cdyn: float = 1.0, rho_air: float = 1.2,
                          vdes_min: float = 30.0,
                          direction: str = "X") -> dict:
    """AS/NZS 1170.2:2021 enclosed building walls (MWFRS).

    * ``Vsit = VR Md (Mz,cat Ms Mt)`` (Eq. 2.2), ``Vdes = max(Vsit,
      vdes_min)`` (§2.3, 30 m/s for ULS; 0 for SLS).
    * ``p = 0.5 rho_air Vdes^2 Cfig Cdyn`` (Eq. 2.4(1)), ``Cfig = Cp,e Ka
      Kc`` (Eq. 5.2(1); local pressure and porous factors 1).
    * Windward Cp,e 0.7 at z for h > 25 m, at h for h <= 25 m (Table
      5.2(A)); leeward Cp,e at h from d/b (Table 5.2(B), roof pitch <
      10 deg: -0.5 at d/b <= 1, -0.3 at 2, -0.2 at >= 4).
    """
    fac = _facade(model, direction)
    terrain = str(terrain)
    if terrain not in AS2_MZ:
        raise ValueError(f"terrain must be one of {tuple(AS2_MZ)}")
    for lab, v in (("VR", VR), ("Md", Md), ("Ms", Ms), ("Mt", Mt),
                   ("Ka", Ka), ("Kc", Kc), ("Cdyn", Cdyn),
                   ("rho_air", rho_air)):
        _pos(lab, v)
    vdes_min = _pos("vdes_min", vdes_min, allow_zero=True)
    cpw = _num("cpe_windward", cpe_windward)
    db = fac["L"] / fac["B"]
    cpl = (_interp(db, (1.0, 2.0, 4.0), (-0.5, -0.3, -0.2))
           if cpe_leeward is None else _num("cpe_leeward", cpe_leeward))
    h = fac["h"]

    def vdes(z):
        return max(VR * Md * as2_mz(z, terrain) * Ms * Mt, vdes_min)

    def q(z):
        return 0.5 * rho_air * vdes(z) ** 2 / 1000.0

    qh = q(h)

    def p_fn(z):
        qz = q(z) if h > 25.0 else qh
        return (cpw * qz + abs(cpl) * qh) * Ka * Kc * Cdyn, qz

    rows = _rows(model, fac, p_fn)
    return _wind_summary("as1170_2_wind", direction, rows, qh=qh,
                         Vdes_h=vdes(h), cpe_windward=cpw,
                         cpe_leeward=cpl, width=fac["B"])


# ============================================================ GB 50009
# Table 8.2.1 terrain -> (coefficient, exponent, z floor m)
GB9_MUZ = {"A": (1.284, 0.24, 5.0), "B": (1.000, 0.30, 10.0),
           "C": (0.544, 0.44, 15.0), "D": (0.262, 0.60, 30.0)}
# §8.4.4-8.4.5: terrain -> (I10, kw, k, a1 (high-rise buildings))
GB9_GUST = {"A": (0.12, 1.28, 0.944, 0.155), "B": (0.14, 1.00, 0.670, 0.187),
            "C": (0.23, 0.54, 0.295, 0.261), "D": (0.39, 0.26, 0.112, 0.346)}


def gb9_muz(z: float, terrain: str) -> float:
    """GB 50009-2012 Table 8.2.1 mu_z (formula form, floor height per
    terrain, capped at 2.91 above the gradient height)."""
    a, e, zf = GB9_MUZ[terrain]
    return min(a * (max(z, zf) / 10.0) ** e, 2.91)


def gb9_beta_z(z: float, H: float, B: float, w0: float, terrain: str,
               T1: float, zeta1: float, g: float = 2.5) -> dict:
    """GB 50009-2012 §8.4.3 along-wind vibration factor ``beta_z = 1 + 2 g
    I10 Bz sqrt(1 + R^2)`` (8.4.3), ``R^2 = pi/(6 zeta1) x1^2/(1 +
    x1^2)^(4/3)``, ``x1 = max(30 f1/sqrt(kw w0), 5)`` (8.4.4), ``Bz = k
    H^a1 rho_x rho_z phi1(z)/mu_z`` (8.4.5), ``rho_z = 10 sqrt(H + 60
    e^(-H/60) - 60)/H``, ``rho_x = 10 sqrt(B + 50 e^(-B/50) - 50)/B``
    (8.4.6), ``phi1 = z/H`` (linear first mode; Appendix G simplified)."""
    I10, kw, k, a1 = GB9_GUST[terrain]
    f1 = 1.0 / T1
    x1 = max(30.0 * f1 / math.sqrt(kw * w0), 5.0)
    R2 = math.pi / (6.0 * zeta1) * x1 ** 2 / (1.0 + x1 ** 2) ** (4.0 / 3.0)
    rz = 10.0 * math.sqrt(H + 60.0 * math.exp(-H / 60.0) - 60.0) / H
    rx = 10.0 * math.sqrt(B + 50.0 * math.exp(-B / 50.0) - 50.0) / B
    Bz = k * H ** a1 * rx * rz * (z / H) / gb9_muz(z, terrain)
    return {"beta_z": 1.0 + 2.0 * g * I10 * Bz * math.sqrt(1.0 + R2),
            "R2": R2, "x1": x1, "rho_x": rx, "rho_z": rz, "Bz": Bz}


def compute_gb50009_wind(model, w0: float, terrain: str = "B",
                         mu_s: float = 1.3, beta_z: Optional[float] = None,
                         T1: Optional[float] = None, zeta1: float = 0.05,
                         direction: str = "X") -> dict:
    """GB 50009-2012 main structure wind: ``wk = beta_z mu_s mu_z w0``
    (Eq. 8.1.1-1, kN/m^2), mu_s = 1.3 (rectangular plan, Table 8.3.1 item
    30: windward 0.8 + leeward 0.5) applied with mu_z at the story height;
    beta_z given, else §8.4.3 from ``T1`` (s) and ``zeta1`` (0.05 RC,
    0.02 steel-concrete, 0.01 steel), else 1.0."""
    fac = _facade(model, direction)
    if terrain not in GB9_MUZ:
        raise ValueError(f"terrain must be one of {tuple(GB9_MUZ)}")
    for lab, v in (("w0", w0), ("mu_s", mu_s), ("zeta1", zeta1)):
        _pos(lab, v)
    if beta_z is not None:
        _pos("beta_z", beta_z)
        if T1 is not None:
            raise ValueError("give beta_z OR T1, not both")
    if T1 is not None:
        _pos("T1", T1)
    H, B = fac["h"], fac["B"]
    betas: Dict[str, float] = {}

    def p_fn(z):
        if beta_z is not None:
            bz = beta_z
        elif T1 is not None:
            bz = gb9_beta_z(z, H, B, w0, terrain, T1, zeta1)["beta_z"]
        else:
            bz = 1.0
        betas[z] = bz
        muz = gb9_muz(z, terrain)
        return bz * mu_s * muz * w0, muz * w0

    rows = _rows(model, fac, p_fn)
    for r in rows:
        r["beta_z"] = betas[r["h"]]
    return _wind_summary("gb50009_wind", direction, rows, width=B,
                         mu_s=mu_s)


# ===================================================== Wave B: TS 498
# TS 498 (1997) Table 4: height above ground (m, upper bound) -> (V m/s,
# q kN/m^2); used with the Turkish seismic code TSC / TBDY 2018.
TS498_Q = ((8.0, 28.0, 0.5), (20.0, 36.0, 0.8), (100.0, 42.0, 1.1),
           (math.inf, 46.0, 1.3))


def ts498_q(z: float) -> float:
    """TS 498 Table 4 velocity pressure q (kN/m^2) at height ``z`` (m):
    0.5 (0-8 m), 0.8 (8-20 m), 1.1 (20-100 m), 1.3 (> 100 m)."""
    for zt, _v, q in TS498_Q:
        if z <= zt:
            return q
    return TS498_Q[-1][2]  # pragma: no cover


def compute_ts498_wind(model, cp_windward: float = 0.8,
                       cp_leeward: float = -0.4, q_factor: float = 1.0,
                       direction: str = "X") -> dict:
    """Turkey TS 498:1997 wind on a closed building (the wind standard used
    with TSC / TBDY 2018).

    * ``q(z)`` from Table 4 (step function of the height above ground),
      times ``q_factor`` (1 = the code table; a site study may scale it).
    * ``w = c_p q`` (§11.1), closed building: windward ``c_p = +0.8``,
      leeward ``-0.4`` (Table 5 / Fig. 1) -> ``F = (cp_w + |cp_l|) q(z)
      A_trib`` (both faces at the story height z).
    """
    fac = _facade(model, direction)
    _pos("q_factor", q_factor)
    cpw = _num("cp_windward", cp_windward)
    cpl = _num("cp_leeward", cp_leeward)

    def p_fn(z):
        q = ts498_q(z) * q_factor
        return (cpw + abs(cpl)) * q, q

    rows = _rows(model, fac, p_fn)
    return _wind_summary("ts498_wind", direction, rows, width=fac["B"],
                         cp_total=cpw + abs(cpl))


# ===================================================== Wave B: KDS 41
# KDS 41 10 15:2019 (= KBC 2016 §0305) surface roughness -> (Zb m, Kzr at
# z <= Zb, alpha, Zg m): Kzr(z) = Kb (z/Zb)^alpha for Zb < z <= Zg
KDS_KZR = {"A": (20.0, 0.58, 0.33, 550.0), "B": (15.0, 0.81, 0.22, 450.0),
           "C": (10.0, 1.00, 0.15, 350.0), "D": (5.0, 1.13, 0.10, 250.0)}


def kds_kzr(z: float, roughness: str) -> float:
    """KDS 41 10 15 wind-speed profile factor ``Kzr`` (``Kb`` below ``Zb``,
    ``Kb (z/Zb)^alpha`` up to ``Zg``, constant above)."""
    zb, kb, a, zg = KDS_KZR[roughness]
    return kb * (min(max(float(z), zb), zg) / zb) ** a


def compute_kds41_wind(model, V0: float, roughness: str = "C",
                       KD: float = 1.0, Kzt: float = 1.0, Iw: float = 1.0,
                       GD: float = 2.0, cpe_windward: float = 0.8,
                       cpe_leeward: float = -0.5, rho_air: float = 1.225,
                       direction: str = "X") -> dict:
    """Korea KDS 41 10 15:2019 main wind-force resisting system.

    * Design wind speed ``V_z = V0 K_D K_zr K_zt I_w`` (``V0`` the basic
      10-minute mean speed, m/s; roughness A-D).
    * Velocity pressure ``q_z = 0.5 rho V_z^2`` (``rho`` = 1.225 kg/m^3).
    * Wall wind force ``W_D = (q_z G_D C_pe1 - q_H G_D C_pe2) A``: windward
      at the story height ``z``, leeward at the roof height ``H`` (``G_D``
      gust factor, default 2.0; ``C_pe`` 0.8 / -0.5).
    """
    fac = _facade(model, direction)
    if roughness not in KDS_KZR:
        raise ValueError(f"roughness must be one of {tuple(KDS_KZR)}")
    for lab, v in (("V0", V0), ("KD", KD), ("Kzt", Kzt), ("Iw", Iw),
                   ("GD", GD), ("rho_air", rho_air)):
        _pos(lab, v)
    cpw, cpl = _num("cpe_windward", cpe_windward), _num("cpe_leeward",
                                                        cpe_leeward)

    def q(z):
        vz = V0 * KD * kds_kzr(z, roughness) * Kzt * Iw
        return 0.5 * rho_air * vz ** 2 / 1000.0

    qh = q(fac["h"])

    def p_fn(z):
        qz = q(z)
        return GD * (cpw * qz + abs(cpl) * qh), qz

    rows = _rows(model, fac, p_fn)
    return _wind_summary("kds41_wind", direction, rows, qh=qh,
                         width=fac["B"], GD=GD)


# ============================================ Wave B: Mexico CFE / NTC
# CFE MDOC Diseño por Viento (2008 / 2020) Table 4.2.3: terrain category
# -> (alpha, delta m, c); NTC-Viento CDMX uses the same alpha / delta with
# the rugosity folded into F_TR (form "ntc": c = 1).
CFE_TERRAIN = {1: (0.099, 245.0, 1.137), 2: (0.128, 315.0, 1.000),
               3: (0.156, 390.0, 0.881), 4: (0.170, 455.0, 0.815)}


def cfe_frz(z: float, category: int, form: str = "cfe") -> float:
    """Exposure factor ``F_rz = c (10/10)^a`` (z <= 10 m), ``c (z/10)^a``
    (10 < z < delta), ``c (delta/10)^a`` (z >= delta)."""
    a, delta, c = CFE_TERRAIN[category]
    if form == "ntc":
        c = 1.0
    zz = min(max(float(z), 10.0), delta)
    return c * (zz / 10.0) ** a


def compute_cfe_wind(model, VR: float, category: int = 2,
                     form: str = "cfe", FT: float = 1.0, G: float = 1.0,
                     cpe_windward: float = 0.8,
                     cpe_leeward: Optional[float] = None,
                     direction: str = "X") -> dict:
    """Mexico wind: CFE Manual de Diseño de Obras Civiles, Diseño por
    Viento (``form="cfe"``) or Mexico City NTC-Viento (``form="ntc"``,
    ``c = 1`` and ``FT`` = F_TR).

    * ``V_D = F_T F_rz V_R`` (m/s; ``V_R`` regional speed for the return
      period), ``F_rz`` per :func:`cfe_frz` (terrain category 1-4).
    * ``q_z = 0.613 G V_D^2`` (Pa; ``G = 0.392 Omega/(273 + tau)``, 1.0 at
      sea level and 25 degC).
    * Enclosed building walls: windward ``C_pe = 0.8`` at z, leeward at the
      roof height from d/b (-0.5 at d/b <= 1, -0.3 at 2, -0.2 at >= 4).
    """
    fac = _facade(model, direction)
    if isinstance(category, bool) or category not in CFE_TERRAIN:
        raise ValueError("category must be 1, 2, 3 or 4")
    if form not in ("cfe", "ntc"):
        raise ValueError("form must be 'cfe' or 'ntc'")
    for lab, v in (("VR", VR), ("FT", FT), ("G", G)):
        _pos(lab, v)
    cpw = _num("cpe_windward", cpe_windward)
    db = fac["L"] / fac["B"]
    cpl = (_interp(db, (1.0, 2.0, 4.0), (-0.5, -0.3, -0.2))
           if cpe_leeward is None else _num("cpe_leeward", cpe_leeward))

    def q(z):
        vd = FT * cfe_frz(z, category, form) * VR
        return 0.613 * G * vd ** 2 / 1000.0

    qh = q(fac["h"])

    def p_fn(z):
        qz = q(z)
        return cpw * qz + abs(cpl) * qh, qz

    rows = _rows(model, fac, p_fn)
    return _wind_summary("cfe_wind", direction, rows, qh=qh,
                         cpe_windward=cpw, cpe_leeward=cpl, width=fac["B"])


# ------------------------------------------------------------ registration
COMPUTE = {"asce7_16_wind": compute_asce7_16_wind,
           "asce7_22_wind": compute_asce7_22_wind_ext,
           "en1991_wind": compute_en1991_wind,
           "is875_wind": compute_is875_wind,
           "as1170_2_wind": compute_as1170_2_wind,
           "gb50009_wind": compute_gb50009_wind,
           # Wave B
           "ts498_wind": compute_ts498_wind,
           "kds41_wind": compute_kds41_wind,
           "cfe_wind": compute_cfe_wind}
DEFAULT_NAME = {"asce7_16_wind": "WIND16", "en1991_wind": "WINDEN",
                "is875_wind": "WINDIS", "as1170_2_wind": "WINDAS",
                "gb50009_wind": "WINDGB", "ts498_wind": "WINDTS",
                "kds41_wind": "WINDKDS", "cfe_wind": "WINDCFE"}
WIND_CODES = frozenset(COMPUTE)


# register with autolateral (no-op when autolateral already did it)
from . import autolateral as _al_reg  # noqa: E402
_al_reg._register_ext(__import__("sys").modules[__name__])
