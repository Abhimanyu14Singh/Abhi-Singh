"""FNA device library III — Takeda / Pivot multilinear links, BRB, bilinear
damper and the T/C friction isolator (pure numpy / Python).

CONTRACT "Eigen shift / cutoff / tolerance, modal history and FNA case
types, TH initial conditions, RS / steady-state modal damping" (FNA
links) — the device protocol of :mod:`skyframe.core.fna_devices`
(``rows`` / ``trial(d, v, dt)`` / ``commit(d, v, dt)``, forces along the
row measure, tension / opening positive).

* :class:`HystereticLaw` — one row of the OpenSees ``Hysteretic``
  uniaxial material exactly as the direct-integration builders use it
  (:func:`skyframe.engine.hysteresis.shear_material`): tri-/bilinear
  envelope per side (``points`` / ``points_neg``), unloading stiffness
  ``E1 * mu^-beta`` (``beta = unload_beta``), reloading towards the
  previous peak of the opposite side through the pinch point ``rotch``
  (``pinch_x`` / ``pinch_y``; Takeda = 1 / 1), no damage.  The law is the
  material's committed-state algorithm transcribed (positive / negative
  increment branches, release strains ``rotPu`` / ``rotNu``) and is pinned
  against the OpenSees material on random cyclic paths in the tests.
  :class:`HystereticDevice` puts one law on each horizontal shear row
  (the isolator layout of the link builders; uncoupled rows).
  The C5 options of the multilinear links are read from the link
  ``params`` when present (``pinch_x``, ``pinch_y``, ``unload_beta``,
  ``points_neg``); types ``multilinear_takeda`` / ``multilinear_pivot``
  and any future ``multilinear_degrading`` with ``points`` map here.
* :class:`MenegottoPintoAsym` — BRB (``Steel4 -asym -kin``): Giuffre-
  Menegotto-Pinto curve between the elastic and the asymptote lines with
  kinematic hardening ``b_t`` / ``b_c`` (``core.links_ext.brb_hardening``)
  and curvature ``R = R0 - cR1 xi / (cR2 + xi)`` (``xi`` = the previous
  excursion's plastic strain normalised by ``dy``); axial row.
* :class:`BilinearDamper` — Maxwell spring ``k`` in series with the
  bilinear dashpot ``F = cd v`` (``|v| <= Fr/cd``), else ``sgn(v)(Fr +
  ratio cd (|v| - Fr/cd))``, integrated like
  :class:`skyframe.core.fna_devices.NonlinearDamper` (implicit
  trapezoid, bracketed Newton) with the inverse dashpot law ``g(F)``
  (``ratio = 0``: the force saturates at ``Fr`` — ``g`` is given a 1e-9
  slope there).
* :class:`TCFrictionPendulum` — single friction pendulum (``R``, ``mu``,
  stick ``k_init``) whose axial load ``N = N0 - kv dz`` is floored at 0:
  in tension (uplift) the bearing carries NO axial and NO shear force
  (the slider then follows the top plate, so re-contact sticks at the
  current position); the optional hold-down ``kt`` engages after
  ``gap_t`` of uplift (``F = kt (dz - gap_t)``).
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple

import numpy as np

from skyframe.core.fna_devices import FrictionPendulum, NonlinearDamper

_INF = 1.0e16


# --------------------------------------------------------------------------- #
# Hysteretic (Takeda / Pivot)
# --------------------------------------------------------------------------- #
class HystereticLaw:
    """One row of the OpenSees ``Hysteretic`` material (no damage)."""

    def __init__(self, points, points_neg=None, pinch_x: float = 1.0,
                 pinch_y: float = 1.0, beta: float = 0.0):
        pos = [(float(d), float(f)) for d, f in points]
        neg = ([(float(d), float(f)) for d, f in points_neg]
               if points_neg is not None else [(-d, -f) for d, f in pos])
        two_p, two_n = len(pos) == 2, len(neg) == 2
        if two_p:                  # 2-point backbone: the 2nd slope goes on
            pos.append(pos[1])
        if two_n:
            neg.append(neg[1])
        (self.r1p, self.m1p), (self.r2p, self.m2p), (self.r3p, self.m3p) = pos
        (self.r1n, self.m1n), (self.r2n, self.m2n), (self.r3n, self.m3n) = neg
        self.E1p = self.m1p / self.r1p
        self.E2p = (self.m2p - self.m1p) / (self.r2p - self.r1p)
        self.E3p = ((self.m3p - self.m2p) / (self.r3p - self.r2p)
                    if not two_p else self.E2p)
        self.E1n = self.m1n / self.r1n
        self.E2n = (self.m2n - self.m1n) / (self.r2n - self.r1n)
        self.E3n = ((self.m3n - self.m2n) / (self.r3n - self.r2n)
                    if not two_n else self.E2n)
        self.px = float(pinch_x)
        self.py = float(pinch_y)
        self.beta = float(beta)
        # committed state
        self.c = {"strain": 0.0, "stress": 0.0, "rmax": 0.0, "rmin": 0.0,
                  "rpu": 0.0, "rnu": 0.0, "load": 0}
        self.k_init = self.E1p

    # envelope -------------------------------------------------------------
    def pos_env(self, e: float) -> float:
        if e <= 0.0:
            return 0.0
        if e <= self.r1p:
            return self.E1p * e
        if e <= self.r2p:
            return self.m1p + self.E2p * (e - self.r1p)
        if e <= self.r3p or self.E3p > 0.0:
            return self.m2p + self.E3p * (e - self.r2p)
        return self.m3p

    def neg_env(self, e: float) -> float:
        if e >= 0.0:
            return 0.0
        if e >= self.r1n:
            return self.E1n * e
        if e >= self.r2n:
            return self.m1n + self.E2n * (e - self.r1n)
        if e >= self.r3n or self.E3n > 0.0:
            return self.m2n + self.E3n * (e - self.r2n)
        return self.m3n

    def _pos_rotlim(self, e: float) -> float:
        lim = _INF
        if e <= self.r1p:
            return _INF
        if self.r1p < e <= self.r2p and self.E2p < 0.0:
            lim = self.r1p - self.m1p / self.E2p
        if e > self.r2p and self.E3p < 0.0:
            lim = self.r2p - self.m2p / self.E3p
        if lim == _INF or self.pos_env(lim) > 0.0:
            return _INF
        return lim

    def _neg_rotlim(self, e: float) -> float:
        lim = -_INF
        if e >= self.r1n:
            return -_INF
        if self.r1n > e >= self.r2n and self.E2n < 0.0:
            lim = self.r1n - self.m1n / self.E2n
        if e < self.r2n and self.E3n < 0.0:
            lim = self.r2n - self.m2n / self.E3n
        if lim == -_INF or self.neg_env(lim) < 0.0:
            return -_INF
        return lim

    # state ----------------------------------------------------------------
    def _k(self, c) -> Tuple[float, float]:
        kn = (c["rmin"] / self.r1n) ** self.beta if c["rmin"] else 0.0
        kn = 1.0 if kn < 1.0 else 1.0 / kn
        kp = (c["rmax"] / self.r1p) ** self.beta if c["rmax"] else 0.0
        kp = 1.0 if kp < 1.0 else 1.0 / kp
        return kn, kp

    def _trial(self, e: float) -> dict:
        c = self.c
        t = dict(c)
        t["strain"] = e
        de = e - c["strain"]
        if de == 0.0:
            return t
        if t["load"] == 0:
            t["load"] = 2 if de < 0.0 else 1
        if e >= c["rmax"]:
            t["rmax"] = e
            t["stress"] = self.pos_env(e)
            t["load"] = 1
        elif e <= c["rmin"]:
            t["rmin"] = e
            t["stress"] = self.neg_env(e)
            t["load"] = 2
        elif de > 0.0:
            self._pos_inc(c, t, de)
        else:
            self._neg_inc(c, t, de)
        return t

    def _pos_inc(self, c, t, de) -> None:
        kn, kp = self._k(c)
        if t["load"] == 2:
            t["load"] = 1
            if c["stress"] <= 0.0:
                t["rnu"] = c["strain"] - c["stress"] / (self.E1n * kn)
        t["load"] = 1
        t["rmax"] = max(t["rmax"], self.r1p)
        maxmom = self.pos_env(t["rmax"])
        rotlim = self._neg_rotlim(c["rmin"])
        rotrel = rotlim if rotlim > t["rnu"] else t["rnu"]
        rotmp1 = rotrel
        rotmp2 = t["rmax"] - (1.0 - self.py) * maxmom / (self.E1p * kp)
        rotch = rotmp1 + (rotmp2 - rotmp1) * self.px
        e = t["strain"]
        if e < t["rnu"]:
            s = c["stress"] + self.E1n * kn * de
            t["stress"] = 0.0 if s >= 0.0 else s
        elif e < rotch:
            if e <= rotrel:
                t["stress"] = 0.0
            else:
                tan = maxmom * self.py / (rotch - rotrel)
                m1 = c["stress"] + self.E1p * kp * de
                m2 = (e - rotrel) * tan
                t["stress"] = m1 if m1 < m2 else m2
        else:
            tan = ((1.0 - self.py) * maxmom / (t["rmax"] - rotch)
                   if t["rmax"] != rotch else 0.0)
            m1 = c["stress"] + self.E1p * kp * de
            m2 = self.py * maxmom + (e - rotch) * tan
            t["stress"] = m1 if m1 < m2 else m2

    def _neg_inc(self, c, t, de) -> None:
        kn, kp = self._k(c)
        if t["load"] == 1:
            t["load"] = 2
            if c["stress"] >= 0.0:
                t["rpu"] = c["strain"] - c["stress"] / (self.E1p * kp)
        t["load"] = 2
        t["rmin"] = min(t["rmin"], self.r1n)
        minmom = self.neg_env(t["rmin"])
        rotlim = self._pos_rotlim(c["rmax"])
        rotrel = rotlim if rotlim < t["rpu"] else t["rpu"]
        rotmp1 = rotrel
        rotmp2 = t["rmin"] - (1.0 - self.py) * minmom / (self.E1n * kn)
        rotch = rotmp1 + (rotmp2 - rotmp1) * self.px
        e = t["strain"]
        if e > t["rpu"]:
            s = c["stress"] + self.E1p * kp * de
            t["stress"] = 0.0 if s <= 0.0 else s
        elif e > rotch:
            if e >= rotrel:
                t["stress"] = 0.0
            else:
                tan = minmom * self.py / (rotch - rotrel)
                m1 = c["stress"] + self.E1n * kn * de
                m2 = (e - rotrel) * tan
                t["stress"] = m1 if m1 > m2 else m2
        else:
            tan = ((1.0 - self.py) * minmom / (t["rmin"] - rotch)
                   if t["rmin"] != rotch else 0.0)
            m1 = c["stress"] + self.E1n * kn * de
            m2 = self.py * minmom + (e - rotch) * tan
            t["stress"] = m1 if m1 > m2 else m2

    def trial(self, e: float) -> float:
        return float(self._trial(float(e))["stress"])

    def commit(self, e: float) -> float:
        self.c = self._trial(float(e))
        return float(self.c["stress"])


class HystereticDevice:
    """``rows`` uncoupled :class:`HystereticLaw` rows (shear layout)."""

    def __init__(self, prm: dict, rows: int = 2, ltype: str = ""):
        takeda = ltype == "multilinear_takeda"
        px = 1.0 if takeda else float(prm.get("pinch_x", 0.5))
        py = 1.0 if takeda else float(prm.get("pinch_y", 0.25))
        if takeda and ("pinch_x" in prm or "pinch_y" in prm):
            px = float(prm.get("pinch_x", 1.0))     # C5 options, if given
            py = float(prm.get("pinch_y", 1.0))
        beta = float(prm.get("unload_beta", 0.0))
        self.rows = int(rows)
        self.laws = [HystereticLaw(prm["points"], prm.get("points_neg"),
                                   px, py, beta) for _ in range(self.rows)]
        self.k_init = self.laws[0].k_init

    def trial(self, d, v, dt):
        return np.array([law.trial(float(d[r]))
                         for r, law in enumerate(self.laws)])

    def commit(self, d, v, dt):
        for r, law in enumerate(self.laws):
            law.commit(float(d[r]))


# --------------------------------------------------------------------------- #
# BRB — Menegotto-Pinto, asymmetric kinematic hardening
# --------------------------------------------------------------------------- #
class MenegottoPintoAsym:
    """Axial GMP law (1 row): ``k``, ``Fy``, hardening ``b_t`` / ``b_c``,
    ``R0``, ``cR1``, ``cR2`` (forces / displacements of the link).

    Kinematic hardening = FIXED asymptotes ``F = +-Fy + b k (d -+ dy)``;
    after a reversal at ``(dr, Fr)`` the branch runs from the reversal
    point towards the intersection ``(d0, F0)`` of the elastic line through
    it with the asymptote of the new direction, with the Steel02 curvature
    ``R = R0 (1 - cR1 xi / (cR2 + xi))``, ``xi = |d_pl - d0| / dy``
    (``d_pl`` = the extreme deformation reached in the new direction)."""

    rows = 1

    def __init__(self, k: float, Fy: float, b_t: float, b_c: float,
                 R0: float = 20.0, cR1: float = 0.925, cR2: float = 0.15):
        self.k, self.Fy = float(k), float(Fy)
        self.bt, self.bc = float(b_t), float(b_c)
        self.R0, self.cR1, self.cR2 = float(R0), float(cR1), float(cR2)
        self.dy = self.Fy / self.k
        self.k_init = self.k
        self.c = {"d": 0.0, "F": 0.0, "dir": 0, "dr": 0.0, "Fr": 0.0,
                  "d0": self.dy, "F0": self.Fy, "R": self.R0,
                  "dmax": 0.0, "dmin": 0.0}

    def _target(self, dr, Fr, sgn):
        b = self.bt if sgn > 0 else self.bc
        Fh0 = sgn * self.Fy - b * self.k * sgn * self.dy
        d0 = (Fh0 - Fr + self.k * dr) / (self.k * (1.0 - b))
        return d0, Fr + self.k * (d0 - dr)

    def _eval(self, d: float):
        c = self.c
        dd = d - c["d"]
        st = dict(c)
        if dd == 0.0:
            return c["F"], st
        sgn = 1 if dd > 0.0 else -1
        if sgn != c["dir"]:                     # first loading / reversal
            st["dir"] = sgn
            st["dr"], st["Fr"] = c["d"], c["F"]
            st["d0"], st["F0"] = self._target(c["d"], c["F"], sgn)
            if c["dir"] == 0:
                st["R"] = self.R0
            else:
                dpl = c["dmax"] if sgn > 0 else c["dmin"]
                xi = abs(dpl - st["d0"]) / self.dy
                st["R"] = self.R0 * (1.0 - self.cR1 * xi / (self.cR2 + xi))
        b = self.bt if st["dir"] > 0 else self.bc
        span = st["d0"] - st["dr"]
        if span == 0.0:
            return st["Fr"], st
        es = (d - st["dr"]) / span
        R = st["R"]
        Fs = b * es + (1.0 - b) * es / (1.0 + abs(es) ** R) ** (1.0 / R)
        return st["Fr"] + Fs * (st["F0"] - st["Fr"]), st

    def trial(self, d, v, dt):
        return np.array([self._eval(float(d[0]))[0]])

    def commit(self, d, v, dt):
        F, st = self._eval(float(d[0]))
        st["d"], st["F"] = float(d[0]), float(F)
        st["dmax"] = max(st["dmax"], st["d"])
        st["dmin"] = min(st["dmin"], st["d"])
        self.c = st


# --------------------------------------------------------------------------- #
# bilinear (oil) damper
# --------------------------------------------------------------------------- #
class BilinearDamper(NonlinearDamper):
    """Maxwell element with the bilinear dashpot (1 row)."""

    def __init__(self, k: float, cd: float, Fr: float, ratio: float = 0.0):
        super().__init__(k, cd, 1.0)
        self.Fr = float(Fr)
        self.ratio = max(float(ratio), 1e-9)
        self.vr = self.Fr / self.cd

    def _g(self, F: float) -> float:
        a = abs(F)
        if a <= self.Fr:
            return F / self.cd
        return math.copysign(self.vr + (a - self.Fr)
                             / (self.ratio * self.cd), F)

    def _dg(self, F: float) -> float:
        return (1.0 / self.cd if abs(F) <= self.Fr
                else 1.0 / (self.ratio * self.cd))


# --------------------------------------------------------------------------- #
# T/C friction isolator
# --------------------------------------------------------------------------- #
class TCFrictionPendulum(FrictionPendulum):
    """Friction pendulum with uplift (rows x, y, z) and an optional
    tension hold-down ``kt`` engaging after ``gap_t``."""

    def __init__(self, R: float, mu: float, k0: float, N0: float,
                 kv: float, kt: float = 0.0, gap_t: float = 0.0):
        super().__init__(R, mu, k0, N0, kv)
        self.kt = float(kt)
        self.gap_t = float(gap_t)

    def _axial(self, dz: float) -> float:
        """Axial row force relative to the reference state (tension +):
        ``kv dz`` in contact; in uplift the total force is 0 (or the
        hold-down ``kt (up - gap_t)``) i.e. ``N0 + T``."""
        if self.N0 - self.kv * dz > 0.0:
            return self.kv * dz
        up = dz - (self.N0 / self.kv if self.kv > 0.0 else 0.0)
        T = (self.kt * (up - self.gap_t)
             if self.kt > 0.0 and up > self.gap_t else 0.0)
        return self.N0 + T

    def trial(self, d, v, dt):
        F, _f, N = self._eval(d)
        if N <= 0.0:
            F = np.zeros(2)
        return np.array([F[0], F[1], self._axial(float(d[2]))])

    def commit(self, d, v, dt):
        _F, f, N = self._eval(d)
        if N <= 0.0:
            self._up = np.asarray(d[:2], dtype=float).copy()
        else:
            self._up = np.asarray(d[:2], dtype=float) - f / self.k0


__all__ = ["HystereticLaw", "HystereticDevice", "MenegottoPintoAsym",
           "BilinearDamper", "TCFrictionPendulum"]
