"""ASCE 41-17 §7.4.3 pushover target displacement (v0.19) — coefficient
method, with the §7.4.3.2.4 bilinear idealization of the capacity curve.

Everything here is a PURE function of the capacity curve + spectrum
parameters, so each piece is pinned by a hand calculation in the tests.

Bilinearization (§7.4.3.2.4)
----------------------------
The idealized force-displacement curve is bilinear: effective stiffness
``Ke`` = the secant through the point on the ACTUAL curve at ``0.6 Vy``,
and ``Vy`` chosen so the two curves enclose EQUAL AREA up to the target
end point ``(du, Vu)`` (taken as the point of peak base shear); the
post-yield branch runs straight from ``(dy = Vy/Ke, Vy)`` to ``(du, Vu)``.
Since Ke depends on Vy the pair is found by fixed-point iteration (the
standard procedure; converges in a handful of rounds, and is EXACT when
the input curve is itself bilinear).

Target displacement (Eq. 7-28)
------------------------------
    delta_t = C0 C1 C2 Sa (Te / 2 pi)^2 g

* ``Te = Ti sqrt(Ki / Ke)``  (Eq. 7-27), ``Ti``/``Ki`` the elastic period
  and initial curve stiffness;
* ``C0`` — Table 7-5, "Any load pattern / Other buildings" column:
  1 story 1.0; 2 -> 1.2; 3 -> 1.3; 5 -> 1.4; 10+ -> 1.5 (linear
  interpolation between rows);
* ``C1 = 1 + (mu - 1) / (a Te^2)`` for 0.2 s < Te < 1.0 s; evaluated at
  Te = 0.2 s below that; = 1 above 1.0 s.  ``a`` = 130 (site class A/B),
  90 (C), 60 (D/E/F);
* ``C2 = 1 + ((mu - 1) / Te)^2 / 800`` for Te <= 0.7 s, else 1;
* ``mu = Sa / (Vy / W) * Cm`` (Eq. 7-31 strength ratio), ``Cm = 1.0``
  in v0.19 (Table 7-4 would refine by system/stories — documented);
* ``Sa`` from the standard two-parameter design spectrum: with
  ``Ts = SD1/SDS`` and ``T0 = 0.2 Ts``::

      Sa = SDS (0.4 + 0.6 T/T0)      T <  T0
      Sa = SDS                       T0 <= T <= Ts
      Sa = SD1 / T                   T >  Ts     (TL branch omitted)

All SI: kN, m, s; ``g = 9.81 m/s^2``.
"""

from __future__ import annotations

import math
from typing import Dict, List, Sequence

G = 9.81

# Table 7-5, "Other buildings" (any load pattern): stories -> C0
_C0_TABLE = ((1, 1.0), (2, 1.2), (3, 1.3), (5, 1.4), (10, 1.5))

_SITE_A = {"A": 130.0, "B": 130.0, "C": 90.0,
           "D": 60.0, "E": 60.0, "F": 60.0}


def c0_factor(num_stories: int) -> float:
    """Table 7-5 C0 with linear interpolation, clamped at the ends."""
    n = max(int(num_stories), 1)
    if n >= _C0_TABLE[-1][0]:
        return _C0_TABLE[-1][1]
    for (n1, c1), (n2, c2) in zip(_C0_TABLE, _C0_TABLE[1:]):
        if n <= n2:
            if n <= n1:
                return c1
            return c1 + (c2 - c1) * (n - n1) / (n2 - n1)
    return _C0_TABLE[-1][1]        # pragma: no cover


def design_sa(T: float, SDS: float, SD1: float) -> float:
    """Two-parameter design spectral acceleration (g) at period T (s)."""
    if T <= 0.0 or SDS <= 0.0 or SD1 <= 0.0:
        raise ValueError("design_sa: T, SDS, SD1 must all be > 0")
    Ts = SD1 / SDS
    T0 = 0.2 * Ts
    if T < T0:
        return SDS * (0.4 + 0.6 * T / T0)
    if T <= Ts:
        return SDS
    return SD1 / T


def bilinearize(disp: Sequence[float], shear: Sequence[float],
                tol: float = 1e-10, max_iter: int = 200) -> Dict[str, float]:
    """§7.4.3.2.4 bilinear idealization of a capacity curve.

    ``disp``/``shear`` are the pushover curve from the origin (the (0, 0)
    point is implicit).  The curve is used up to PEAK shear.  Returns
    {Ki, Ke, Vy, dy, du, Vu, area} — exact for a bilinear input curve.
    """
    d = [0.0] + [float(x) for x in disp]
    v = [0.0] + [float(x) for x in shear]
    if len(d) < 3:
        raise ValueError("bilinearize: need at least 2 curve points")
    ip = max(range(len(v)), key=lambda i: v[i])       # peak-shear cutoff
    if ip < 2:
        raise ValueError("bilinearize: the curve peaks at its first point")
    d, v = d[:ip + 1], v[:ip + 1]
    du, Vu = d[-1], v[-1]
    if d[1] <= 0.0 or v[1] <= 0.0:
        raise ValueError("bilinearize: first curve point must be positive")
    Ki = v[1] / d[1]

    def _interp_d(vq: float) -> float:
        """Displacement on the actual curve at shear vq (first crossing)."""
        for (d1, v1), (d2, v2) in zip(zip(d, v), zip(d[1:], v[1:])):
            if (v1 - vq) * (v2 - vq) <= 0.0 and v2 != v1:
                return d1 + (d2 - d1) * (vq - v1) / (v2 - v1)
        return du * vq / max(Vu, 1e-30)               # pragma: no cover

    area = sum((v1 + v2) * (d2 - d1) / 2.0
               for (d1, v1), (d2, v2) in zip(zip(d, v), zip(d[1:], v[1:])))

    Vy = Vu                                           # starting guess
    for _ in range(max_iter):
        Ke = 0.6 * Vy / _interp_d(0.6 * Vy)
        # equal-area condition, bilinear area as a function of Vy:
        #   A = Vy*dy/2 + (du - dy)(Vy + Vu)/2,  dy = Vy/Ke
        # expanding, the Vy^2 terms CANCEL and A is linear in Vy:
        #   A = du*Vy/2 + du*Vu/2 - Vu*Vy/(2 Ke)
        # solved for Vy with Ke frozen (then Ke is refreshed):
        den = du / 2.0 - Vu / (2.0 * Ke)
        # ELASTIC fallback: a (near-)linear curve has den ~ 0, or puts the
        # equal-area yield point at/beyond the curve end (Vy >= Vu) — the
        # pushover never yielded.  ASCE 41's idealization degenerates to
        # the elastic line: Ke = Ki, Vy = Vu (the strongest point actually
        # reached — conservative in the C1/C2 strength ratio).  Flagged
        # ``elastic: True`` so callers can annotate.
        if abs(den) < 1e-12 * du:
            Vy = Vu
            break
        Vy_new = (area - du * Vu / 2.0) / den
        if Vy_new >= Vu:
            Vy = Vu
            break
        if Vy_new <= 0.0:
            raise ValueError("bilinearize: no positive equal-area yield "
                             "point (degenerate capacity curve)")
        if abs(Vy_new - Vy) <= tol * max(1.0, abs(Vy)):
            Vy = Vy_new
            break
        Vy = Vy_new
    elastic = Vy >= Vu * (1.0 - 1e-12)
    Ke = Ki if elastic else 0.6 * Vy / _interp_d(0.6 * Vy)
    return {"Ki": Ki, "Ke": Ke, "Vy": Vy, "dy": Vy / Ke,
            "du": du, "Vu": Vu, "area": area, "elastic": elastic}


def target_displacement(disp: Sequence[float], shear: Sequence[float], *,
                        Ti: float, W: float, SDS: float, SD1: float,
                        site_class: str = "D",
                        num_stories: int = 1) -> Dict[str, float]:
    """ASCE 41-17 Eq. 7-28 target displacement (module docstring).

    ``Ti`` elastic first-mode period in the push direction (s), ``W``
    seismic weight (kN), ``SDS``/``SD1`` design spectral parameters (g).
    Returns the bilinearization plus Te, Sa, mu, C0/C1/C2 and delta_t (m).
    """
    if Ti <= 0.0 or W <= 0.0:
        raise ValueError("target_displacement: Ti and W must be > 0")
    sc = str(site_class).upper()
    if sc not in _SITE_A:
        raise ValueError(f"target_displacement: unknown site class "
                         f"{site_class!r}")
    bl = bilinearize(disp, shear)
    Te = Ti * math.sqrt(bl["Ki"] / bl["Ke"])          # Eq. 7-27
    Sa = design_sa(Te, SDS, SD1)
    Cm = 1.0
    mu = Sa / (bl["Vy"] / W) * Cm                     # Eq. 7-31
    a = _SITE_A[sc]
    if Te >= 1.0:
        C1 = 1.0
    else:
        Tc = max(Te, 0.2)
        C1 = 1.0 + (mu - 1.0) / (a * Tc * Tc)
    C2 = 1.0 if Te > 0.7 else 1.0 + ((mu - 1.0) / Te) ** 2 / 800.0
    C0 = c0_factor(num_stories)
    delta_t = C0 * C1 * C2 * Sa * (Te / (2.0 * math.pi)) ** 2 * G
    out = dict(bl)
    out.update({"Te": Te, "Ti": Ti, "Sa": Sa, "mu": mu, "Cm": Cm,
                "C0": C0, "C1": C1, "C2": C2, "W": W,
                "site_class": sc, "delta_t": delta_t})
    return out
