"""Wave-C FNA device laws (pure numpy) — same ``rows / trial / commit``
protocol as :mod:`skyframe.core.fna_devices`.

* :class:`PeakOriented` — the OpenSees ``Hysteretic`` rule without
  damage (Takeda / pivot / degrading / concrete multilinear plastic
  links, uncoupled per row): envelope = the 2-3 point backbone per side
  (last slope continued); unloading at ``k0 (u_max/d_1)^-beta``; after the
  force crosses zero the reload aims at the opposite peak (the largest
  deformation reached on that side, at least the first backbone point),
  through the pinch point ``(u0 + pinch_x (d* - u0), pinch_y F_peak)``,
  ``d* = u_peak - (1 - pinch_y) F_peak / k_u``; the force is the lesser
  (greater, moving negative) of the elastic unloading line and the
  reload path — exactly the OpenSees branch rule.
* :class:`AsymKinematic` — bilinear asymmetric kinematic hardening
  (BRB: tension hardening ``b_t``, compression ``b_c``; the sharp-corner
  limit of the Steel4 Menegotto-Pinto curve) by return mapping.
* :class:`BilinearDamper` — Maxwell spring ``k`` in series with the
  bilinear dashpot (``cd`` up to the relief force ``Fr``, then
  ``ratio cd``); implicit trapezoidal rule, bracketed scalar solve.
* :class:`ElasticCurves` — per-row nonlinear ELASTIC curves
  (``multilinear_elastic``).
* :class:`FrictionBearing` — friction pendulum / flat slider / T/C
  isolator (rows x, y, z): compression ``N_c``, optional tension
  ``N_t`` (hold-down with friction ``mu_t``, radius ``R_t``), velocity /
  normal-force dependent friction coefficient
  (:func:`skyframe.core.links_c.friction_mu`), coupled (circular, one
  ``mu``) or per-direction (uncoupled, ``mu2 != mu3`` or ``R2 != R3``)
  friction.  Axial law (z row, elongation +): ``kv d`` in compression,
  ``kt (d - gap)`` past the tension gap, else 0 (uplift).
"""

from __future__ import annotations

import math
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np


def _env(pts: Sequence[Sequence[float]], u: float) -> Tuple[float, float]:
    """(force, tangent) on a same-sign backbone through (0, 0)."""
    a = [(0.0, 0.0)] + [(float(p[0]), float(p[1])) for p in pts]
    for (d0, f0), (d1, f1) in zip(a, a[1:]):
        if abs(u) <= abs(d1):
            k = (f1 - f0) / (d1 - d0)
            return f0 + k * (u - d0), k
    (d0, f0), (d1, f1) = a[-2], a[-1]
    k = (f1 - f0) / (d1 - d0)
    return f1 + k * (u - d1), k


class _PO1:
    """One row of the peak-oriented (Hysteretic, no damage) law."""

    def __init__(self, pos, neg, px, py, beta):
        self.pos, self.neg = pos, neg
        self.px, self.py, self.beta = float(px), float(py), float(beta)
        self.d1p, self.d1n = float(pos[0][0]), float(neg[0][0])
        self.k0p = float(pos[0][1]) / self.d1p
        self.k0n = float(neg[0][1]) / self.d1n
        # committed state
        self.u = self.F = 0.0
        self.umax, self.umin = self.d1p, self.d1n
        self.rotPu = self.rotNu = 0.0
        self.load = 0                    # 0 virgin, 1 +, 2 -

    def _kp(self, umax):
        r = (umax / self.d1p) ** self.beta
        return 1.0 if r < 1.0 else 1.0 / r

    def _kn(self, umin):
        r = (umin / self.d1n) ** self.beta
        return 1.0 if r < 1.0 else 1.0 / r

    def evaluate(self, u):
        """Trial (F, new_state) from the committed state."""
        st = dict(u=u, umax=self.umax, umin=self.umin, rotPu=self.rotPu,
                  rotNu=self.rotNu, load=self.load)
        du = u - self.u
        if u >= self.umax:
            st["umax"] = u
            st["load"] = 1
            return _env(self.pos, u)[0], st
        if u <= self.umin:
            st["umin"] = u
            st["load"] = 2
            return _env(self.neg, u)[0], st
        if du == 0.0:
            return self.F, st
        kn = self.k0n * self._kn(self.umin)       # unloading stiffnesses
        kp = self.k0p * self._kp(self.umax)
        py, px = self.py, self.px
        if du > 0.0:                              # OpenSees positiveIncrement
            if self.load == 2 and self.F <= 0.0:
                st["rotNu"] = self.u - self.F / kn
            st["load"] = 1
            umax = max(self.umax, self.d1p)
            fmax = _env(self.pos, umax)[0]
            rel = st["rotNu"]
            r1 = rel
            r2 = umax - (1.0 - py) * fmax / kp
            rch = r1 + (r2 - r1) * px
            if u < rel:
                F = self.F + kn * du
                return (0.0 if F >= 0.0 else F), st
            el = self.F + kp * du
            if u < rch:
                rl = (u - rel) * fmax * py / (rch - rel)
            else:
                rl = py * fmax + (u - rch) * (1.0 - py) * fmax / (umax - rch)
            return min(el, rl), st
        if self.load == 1 and self.F >= 0.0:      # OpenSees negativeIncrement
            st["rotPu"] = self.u - self.F / kp
        st["load"] = 2
        umin = min(self.umin, self.d1n)
        fmin = _env(self.neg, umin)[0]
        rel = st["rotPu"]
        r1 = rel
        r2 = umin - (1.0 - py) * fmin / kn
        rch = r1 + (r2 - r1) * px
        if u > rel:
            F = self.F + kp * du
            return (0.0 if F <= 0.0 else F), st
        el = self.F + kn * du
        if u > rch:
            rl = (u - rel) * fmin * py / (rch - rel)
        else:
            rl = py * fmin + (u - rch) * (1.0 - py) * fmin / (umin - rch)
        return max(el, rl), st

    def commit(self, u):
        F, st = self.evaluate(u)
        self.F = F
        self.u = st["u"]
        self.umax, self.umin = st["umax"], st["umin"]
        self.rotPu, self.rotNu, self.load = st["rotPu"], st["rotNu"], \
            st["load"]


class PeakOriented:
    """Takeda / pivot / degrading / concrete multilinear plastic link."""

    def __init__(self, points, points_neg=None, pinch_x=1.0, pinch_y=1.0,
                 beta=0.0, rows: int = 1):
        pos = [[float(d), float(f)] for d, f in points]
        neg = ([[float(d), float(f)] for d, f in points_neg]
               if points_neg else [[-d, -f] for d, f in pos])
        self.rows = int(rows)
        self.k_init = pos[0][1] / pos[0][0]
        self._r = [_PO1(pos, neg, pinch_x, pinch_y, beta)
                   for _ in range(self.rows)]

    def trial(self, d, v, dt):
        return np.array([r.evaluate(float(d[i]))[0]
                         for i, r in enumerate(self._r)])

    def commit(self, d, v, dt):
        for i, r in enumerate(self._r):
            r.commit(float(d[i]))

    def path(self, us: Sequence[float]) -> List[float]:
        """Force along a deformation path (row 0, committing each point)."""
        out = []
        for u in us:
            self._r[0].commit(float(u))
            out.append(self._r[0].F)
        return out


class AsymKinematic:
    """Bilinear asymmetric kinematic hardening (rows uncoupled)."""

    def __init__(self, k0: float, fy: float, bt: float, bc: float,
                 rows: int = 1):
        self.k0, self.fy = float(k0), float(fy)
        self.Ht = self.k0 * bt / (1.0 - bt) if bt < 1.0 else 1e30
        self.Hc = self.k0 * bc / (1.0 - bc) if bc < 1.0 else 1e30
        self.rows = int(rows)
        self.k_init = self.k0
        self._ep = np.zeros(self.rows)
        self._a = np.zeros(self.rows)

    def _one(self, u, ep, a):
        s = self.k0 * (u - ep)
        f = abs(s - a) - self.fy
        if f <= 0.0:
            return s, ep, a
        sg = 1.0 if s - a > 0.0 else -1.0
        H = self.Ht if sg > 0.0 else self.Hc
        dl = f / (self.k0 + H)
        ep2 = ep + sg * dl
        a2 = a + sg * H * dl
        return self.k0 * (u - ep2), ep2, a2

    def trial(self, d, v, dt):
        return np.array([self._one(float(d[i]), self._ep[i], self._a[i])[0]
                         for i in range(self.rows)])

    def commit(self, d, v, dt):
        for i in range(self.rows):
            _, self._ep[i], self._a[i] = self._one(float(d[i]), self._ep[i],
                                                  self._a[i])


class BilinearDamper:
    """Maxwell spring k in series with a bilinear dashpot (1 row)."""

    rows = 1

    def __init__(self, k: float, cd: float, Fr: float, ratio: float):
        self.k, self.cd, self.Fr, self.r = (float(k), float(cd), float(Fr),
                                            float(ratio))
        self.vr = self.Fr / self.cd
        self._F = 0.0
        self._v = 0.0

    def g(self, F: float) -> float:
        """Dashpot velocity at force F (inverse of the bilinear law)."""
        a = abs(F)
        if a <= self.Fr:
            return F / self.cd
        if self.r <= 0.0:
            return math.copysign(math.inf, F)
        return math.copysign(self.vr + (a - self.Fr) / (self.r * self.cd),
                             F)

    def _step(self, v: float, dt: float) -> float:
        h = 0.5 * dt * self.k
        F_el = self._F + h * (self._v + v - self.g(self._F))
        lo, hi = min(0.0, F_el), max(0.0, F_el)
        if self.r <= 0.0:
            lo, hi = max(lo, -self.Fr), min(hi, self.Fr)
        if lo == hi:
            return lo

        def res(F):
            return F - F_el + h * self.g(F)
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            if res(mid) > 0.0:
                hi = mid
            else:
                lo = mid
            if hi - lo <= 1e-14 * (1.0 + abs(F_el)):
                break
        return 0.5 * (lo + hi)

    def trial(self, d, v, dt):
        return np.array([self._step(float(v[0]), dt)])

    def commit(self, d, v, dt):
        self._F = self._step(float(v[0]), dt)
        self._v = float(v[0])


class ElasticCurves:
    """Per-row nonlinear elastic curves ``[(xs, ys), ...]``."""

    def __init__(self, laws: Sequence[Tuple[Sequence[float],
                                            Sequence[float]]]):
        from skyframe.core.springprops import curve_force, initial_slope
        self._f = curve_force
        self.laws = [(list(x), list(y)) for x, y in laws]
        self.rows = len(self.laws)
        self.k_init = [initial_slope(x, y) for x, y in self.laws]

    def trial(self, d, v, dt):
        return np.array([self._f(x, y, float(d[i]))
                         for i, (x, y) in enumerate(self.laws)])

    def commit(self, d, v, dt):
        pass


def _rr(f_tr: np.ndarray, fy: float, k0: float):
    n = float(np.linalg.norm(f_tr))
    if n <= fy or n == 0.0:
        return f_tr
    return f_tr * (fy / n)


class FrictionBearing:
    """FP / flat slider / T/C isolator with the Wave-C friction options
    (rows x, y, z; see the module doc)."""

    rows = 3

    def __init__(self, R2: float, R3: float, mu_fn: Callable, k0: float,
                 kv: float, per_dir: bool = False,
                 mu3_fn: Optional[Callable] = None, kt: float = 0.0,
                 gap: float = 0.0, mu_t_fn: Optional[Callable] = None,
                 R_t: float = math.inf, N0: float = 0.0,
                 axial_total: bool = True, tc: bool = False):
        self.R2, self.R3 = float(R2), float(R3)
        self.mu_fn, self.mu3_fn = mu_fn, (mu3_fn or mu_fn)
        self.k0, self.kv = float(k0), float(kv)
        self.per_dir = bool(per_dir)
        self.kt, self.gap = float(kt), float(gap)
        self.mu_t_fn, self.R_t = mu_t_fn, float(R_t)
        self.N0 = float(N0)
        self.axial_total = bool(axial_total)
        self.tc = bool(tc)
        self._up = np.zeros(2)          # compression slider slip
        self._upt = np.zeros(2)         # tension slider slip

    def axial(self, dz: float) -> float:
        """Axial force (tension +) at elongation dz."""
        if not self.tc:
            return self.kv * dz
        if dz <= 0.0:
            return self.kv * dz
        return self.kt * (dz - self.gap) if dz > self.gap else 0.0

    def normal(self, dz: float) -> float:
        if self.tc:
            return max(-self.axial(dz), 0.0)
        return max(self.N0 - self.kv * dz, 0.0)

    def _slider(self, u, up, N, vabs, R2, R3, f2, f3):
        if N <= 0.0:
            return np.zeros(2), u.copy()
        kp = np.array([0.0 if math.isinf(R2) else N / R2,
                       0.0 if math.isinf(R3) else N / R3])
        tr = self.k0 * (u - up)
        if self.per_dir:
            fy = np.array([f2(vabs[0], N) * N, f3(vabs[1], N) * N])
            f = np.clip(tr, -fy, fy)
        else:
            f = _rr(tr, f2(float(np.linalg.norm(vabs)), N) * N, self.k0)
        return kp * u + f, u - f / self.k0

    def _eval(self, d, v):
        u = np.asarray(d[:2], dtype=float)
        vv = np.abs(np.asarray(v[:2], dtype=float))
        dz = float(d[2])
        F, upc = self._slider(u, self._up, self.normal(dz), vv, self.R2,
                              self.R3, self.mu_fn, self.mu3_fn)
        upt = self._upt
        Nt = max(self.axial(dz), 0.0) if self.tc else 0.0
        if self.mu_t_fn is not None and Nt > 0.0:
            Ft, upt = self._slider(u, self._upt, Nt, vv, self.R_t, self.R_t,
                                   self.mu_t_fn, self.mu_t_fn)
            F = F + Ft
        Fz = self.axial(dz) if self.tc else self.kv * dz
        return np.array([F[0], F[1], Fz]), upc, upt

    def trial(self, d, v, dt):
        return self._eval(d, v)[0]

    def commit(self, d, v, dt):
        _F, self._up, self._upt = self._eval(d, v)
