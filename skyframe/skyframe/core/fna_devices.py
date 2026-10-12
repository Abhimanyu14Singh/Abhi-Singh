"""FNA device library II and the multi-row FNA integrator (pure numpy).

CONTRACT "FNA breadth: FP / TFP / multilinear / Wen links, nonlinear
dampers, gravity initial conditions, Ritz basis".

Every device acts on one or more ROWS of the modal deformation matrix
``B`` (``d = B q``; a row is a global deformation measure of the link:
axial elongation for an axial device, ``u_j - u_i`` in global X / Y /
Z for a bearing).  A device exposes

* ``rows`` — its row count (the integrator slices ``d``/``v`` for it),
* ``trial(d, v, dt) -> F`` — TOTAL force per row at the step end from the
  COMMITTED state and the trial end-of-step deformation / velocity,
* ``commit(d, v, dt)`` — accept the converged step.

Laws (forces along the row measure, tension / opening positive):

* :class:`NonlinearDamper` — Maxwell spring ``k`` in series with the
  dashpot ``F = cd sgn(v_d)|v_d|^alpha`` (any ``alpha > 0``):
  ``dF/dt = k (v - g(F))``, ``g(F) = sgn(F)|F/cd|^(1/alpha)``, integrated
  with the implicit trapezoidal rule; the scalar step equation is
  monotone in ``F`` and solved by bracketed Newton (bisection fallback).
  ``alpha = 1`` reduces exactly to :class:`skyframe.core.fna.MaxwellDamper`.
* :class:`FrictionPendulum` — single FP bearing, BIAXIAL: pendulum
  restoring force ``(N/R) u`` in parallel with a rigid-plastic-with-stick
  friction element (stick stiffness ``k0``, slip force ``mu N``, radial
  return on the 2-D friction force).  ``N`` is the bearing axial load:
  ``N0`` (gravity initial state, or the ``P0`` / ``W`` param) plus the
  vertical spring's change ``-kv * d_z`` (compression positive, floored
  at 0 — uplift releases the friction).  ``R = inf`` is the flat slider
  (``friction_spring``).  Theoretical loop under ``u = D sin``:
  ``F(D) = N D/R + mu N``, zero-displacement intercepts ``+-mu N``, loop
  area ``4 mu N (D - mu N/k0)`` (pinned).
* :class:`TripleFP` — the Fenz & Constantinou (2008) SERIES model of the
  triple friction pendulum: three biaxial FP elements in series with
  ``(mu1, 2 L1, 2 d1)``, ``(mu2, L2 - L1, d2 (L2 - L1)/L2)``,
  ``(mu3, L3 - L1, d3 (L3 - L1)/L3)`` (friction, effective radius,
  displacement capacity), each with a stiff radial stop at its capacity;
  reproduces regimes I-III exactly: ``F = mu1 W + W u/(2 L1)`` up to
  ``u* = 2 L1 (mu2 - mu1)``, tangent ``W/(L1 + L2)`` up to
  ``u** = u* + (mu3 - mu2)(L1 + L2)``, then ``W/(L2 + L3)`` (the
  "fully-sliding tangent" of the OpenSees element), and regime IV
  ``W/(L1 + L3)`` once surface 2 meets its stop.  Requires
  ``mu1 < mu2 <= mu3`` and ``L1 < L2, L3`` (the standard configuration).
* :class:`MultilinearMasing` — multilinear backbone with Masing
  (kinematic) hysteresis, per direction, by the exact Iwan decomposition
  into parallel elastic-perfectly-plastic springs (unloading branches are
  the backbone scaled by 2); beyond the last point the last slope
  continues.
* :class:`WenComponent` — Bouc-Wen / ETABS Plastic (Wen):
  ``F = k_lin u + q z (+ a2 k sgn(u)|u|^mu)``, ``z' = (1/uy) [u' -
  (gamma + beta sgn(u'.z)) |z|^(eta-2) (z.u') z]`` (biaxial when two rows;
  the 1-D form is ``z' = u'/uy (1 - (gamma + beta sgn(u' z))|z|^eta)``),
  integrated by RK4 with sub-steps of at most 0.02 in ``|du|/uy``.
  ETABS Plastic (Wen) is ``k_lin = r k``, ``q = (1 - r) Fy``,
  ``uy = Fy/k``, ``beta = gamma = 0.5``, ``eta = exp``.

:func:`fna_integrate` is :func:`skyframe.core.fna.fna_modal_th`
generalised to multi-row devices and an initial (gravity) state:
the pseudo-force is ``-B^T (F(d0 + B q) - F0 - k_lin B q)`` — the device
force INCREMENT beyond the gravity state minus the part already in the
linear basis.  Same Newmark constant-average integrator, step
convention, fixed point, under-relaxation and non-convergence error.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

import numpy as np

STOP_FACTOR = 1.0e3        # stop stiffness = STOP_FACTOR * W / R_eff


# --------------------------------------------------------------------------- #
# device laws
# --------------------------------------------------------------------------- #
class _Scalar:
    """Adapter: a legacy single-row law (core.fna) as a device."""

    rows = 1

    def __init__(self, law):
        self.law = law

    def trial(self, d, v, dt):
        return np.array([self.law.trial(float(d[0]), float(v[0]), dt)])

    def commit(self, d, v, dt):
        self.law.commit(float(d[0]), float(v[0]), dt)


class NonlinearDamper:
    """Maxwell damper with velocity exponent ``alpha`` (1 row)."""

    rows = 1

    def __init__(self, k: float, cd: float, alpha: float):
        self.k = float(k)
        self.cd = float(cd)
        self.alpha = float(alpha)
        self._F = 0.0
        self._v = 0.0

    def _g(self, F: float) -> float:
        if F == 0.0:
            return 0.0
        return math.copysign(abs(F / self.cd) ** (1.0 / self.alpha), F)

    def _dg(self, F: float) -> float:
        a = 1.0 / self.alpha
        x = abs(F / self.cd)
        if x == 0.0:
            return math.inf if a < 1.0 else (1.0 / self.cd if a == 1.0
                                             else 0.0)
        return a * x ** (a - 1.0) / self.cd

    def _step(self, v: float, dt: float) -> float:
        h = 0.5 * dt * self.k
        F_el = self._F + h * (self._v + v - self._g(self._F))
        lo, hi = min(0.0, F_el), max(0.0, F_el)
        if lo == hi:
            return 0.0

        def res(F):
            return F - F_el + h * self._g(F)
        F = F_el if abs(res(F_el)) < abs(res(0.0)) else 0.5 * (lo + hi)
        for _ in range(100):
            r = res(F)
            if abs(r) <= 1e-13 * (1.0 + abs(F_el)):
                return F
            if r > 0.0:
                hi = F
            else:
                lo = F
            dg = self._dg(F)
            Fn = F - r / (1.0 + h * dg) if math.isfinite(dg) else math.nan
            F = Fn if lo < Fn < hi else 0.5 * (lo + hi)
            if hi - lo <= 1e-15 * (1.0 + abs(F_el)):
                return F
        return F

    def trial(self, d, v, dt):
        return np.array([self._step(float(v[0]), dt)])

    def commit(self, d, v, dt):
        self._F = self._step(float(v[0]), dt)
        self._v = float(v[0])


_EYE2 = np.eye(2)


def _radial_return(f_tr: np.ndarray, fy: float, k0: float
                   ) -> Tuple[np.ndarray, np.ndarray]:
    """(force, tangent) of a 2-D rigid-plastic-with-stick element."""
    n = math.hypot(float(f_tr[0]), float(f_tr[1]))
    if n <= fy or n == 0.0:
        return f_tr, k0 * _EYE2
    e = f_tr / n
    return fy * e, (fy / n) * k0 * (_EYE2 - np.outer(e, e))


class FrictionPendulum:
    """Single friction pendulum (rows: x, y, z) — see the module doc."""

    rows = 3

    def __init__(self, R: float, mu: float, k0: float, N0: float,
                 kv: float = 0.0, vary_N: bool = True):
        self.R = float(R)
        self.mu = float(mu)
        self.k0 = float(k0)
        self.N0 = float(N0)
        self.kv = float(kv)
        self.vary_N = bool(vary_N)
        self._up = np.zeros(2)          # committed slip displacement

    def normal(self, dz: float) -> float:
        N = self.N0 - (self.kv * dz if self.vary_N else 0.0)
        return max(N, 0.0)

    def _eval(self, d):
        u = np.asarray(d[:2], dtype=float)
        N = self.normal(float(d[2]))
        f, _kt = _radial_return(self.k0 * (u - self._up), self.mu * N,
                                self.k0)
        kp = 0.0 if math.isinf(self.R) else N / self.R
        return kp * u + f, f, N

    def trial(self, d, v, dt):
        F, _f, _N = self._eval(d)
        return np.array([F[0], F[1], self.kv * float(d[2])])

    def commit(self, d, v, dt):
        _F, f, _N = self._eval(d)
        self._up = np.asarray(d[:2], dtype=float) - f / self.k0


class _SeriesElement:
    """One FP element of the TFP series model (2-D)."""

    def __init__(self, mu: float, Reff: float, dcap: float, uy: float):
        self.mu = float(mu)
        self.Reff = float(Reff)
        self.dcap = float(dcap)
        self.uy = float(uy)
        self.up = np.zeros(2)

    def force(self, u: np.ndarray, W: float):
        k0 = self.mu * W / self.uy if W > 0.0 else 1.0
        f, kf = _radial_return(k0 * (u - self.up), self.mu * W, k0)
        kp = W / self.Reff
        F = kp * u + f
        K = kp * np.eye(2) + kf
        r = float(np.linalg.norm(u))
        if r > self.dcap > 0.0:
            ks = STOP_FACTOR * kp
            e = u / r
            F = F + ks * (r - self.dcap) * e
            K = K + ks * (np.outer(e, e) + (1.0 - self.dcap / r)
                          * (np.eye(2) - np.outer(e, e)))
        return F, K, f, k0


class TripleFP:
    """Triple friction pendulum, Fenz-Constantinou series model (rows x, y,
    z) — see the module doc."""

    rows = 3

    def __init__(self, L1, L2, L3, mu1, mu2, mu3, d1, d2, d3, W0, uy,
                 kv: float = 0.0, vary_N: bool = True):
        if not (L2 > L1 and L3 > L1):
            raise ValueError("triple_fp FNA model needs R2, R3 > R1 (the "
                             "standard configuration)")
        if not (mu1 < mu2 <= mu3):
            raise ValueError("triple_fp FNA model needs mu1 < mu2 <= mu3 "
                             "(the standard configuration)")
        self.els = [_SeriesElement(mu1, 2.0 * L1, 2.0 * d1, uy),
                    _SeriesElement(mu2, L2 - L1, d2 * (L2 - L1) / L2, uy),
                    _SeriesElement(mu3, L3 - L1, d3 * (L3 - L1) / L3, uy)]
        self.W0 = float(W0)
        self.kv = float(kv)
        self.vary_N = bool(vary_N)
        self._u = [np.zeros(2), np.zeros(2)]   # committed u1, u2
        self._x_last = None                    # last converged trial

    def normal(self, dz: float) -> float:
        return max(self.W0 - (self.kv * dz if self.vary_N else 0.0), 0.0)

    def _solve(self, d):
        u = np.asarray(d[:2], dtype=float)
        W = self.normal(float(d[2]))
        if W <= 0.0:
            return np.zeros(2), [np.zeros(2), np.zeros(2), u.copy()], W
        x = (self._x_last.copy() if self._x_last is not None
             else np.concatenate(self._u))      # warm start: last trial
        e1, e2, e3 = self.els

        def resid(xx):
            v1, v2 = xx[:2], xx[2:]
            v3 = u - v1 - v2
            F1, K1, _, _ = e1.force(v1, W)
            F2, K2, _, _ = e2.force(v2, W)
            F3, K3, _, _ = e3.force(v3, W)
            return (np.concatenate([F1 - F3, F2 - F3]), F3, (K1, K2, K3),
                    [v1, v2, v3])

        R, F3, (K1, K2, K3), us = resid(x)
        for it in range(200):
            rn = float(np.max(np.abs(R)))
            if rn <= 1e-11 * (1.0 + float(np.max(np.abs(F3)))):
                self._x_last = x.copy()
                return F3, us, W
            J = np.block([[K1 + K3, K3], [K3, K2 + K3]])
            try:
                dx = np.linalg.solve(J, -R)
            except np.linalg.LinAlgError:
                dx = -R / (np.max(np.abs(np.diag(J))) + 1.0)
            # Newton step, halved while the residual does not decrease
            step = 1.0
            for _ in range(20):
                out = resid(x + step * dx)
                if float(np.max(np.abs(out[0]))) < rn:
                    break
                step *= 0.5
            x = x + step * dx
            R, F3, (K1, K2, K3), us = out
        raise RuntimeError("triple_fp series model: internal iteration did "
                           "not converge")

    def trial(self, d, v, dt):
        F, _us, _W = self._solve(d)
        return np.array([F[0], F[1], self.kv * float(d[2])])

    def commit(self, d, v, dt):
        _F, us, W = self._solve(d)
        for el, ui in zip(self.els, us):
            _Fi, _Ki, f, k0 = el.force(ui, W)
            el.up = ui - f / k0
        self._u = [us[0].copy(), us[1].copy()]


class MultilinearMasing:
    """Multilinear backbone, Masing hysteresis (Iwan), uncoupled rows."""

    def __init__(self, points: Sequence[Sequence[float]], rows: int = 1):
        pts = [(float(p[0]), float(p[1])) for p in points]
        ds = [0.0] + [p[0] for p in pts]
        fs = [0.0] + [p[1] for p in pts]
        slopes = [(fs[i + 1] - fs[i]) / (ds[i + 1] - ds[i])
                  for i in range(len(pts))]
        # Iwan: element i (i < n-1) EPP stiffness s_i - s_{i+1}, yield
        # displacement d_{i+1}; plus a linear spring s_last
        self.ki = np.array([slopes[i] - slopes[i + 1]
                            for i in range(len(slopes) - 1)])
        self.dy = np.array(ds[1:len(slopes)])
        self.k_last = slopes[-1]
        self.k_init = slopes[0]
        self.rows = int(rows)
        self._up = np.zeros((self.rows, len(self.ki)))

    def _force(self, r: int, u: float) -> Tuple[float, np.ndarray]:
        if len(self.ki) == 0:
            return self.k_last * u, np.zeros(0)
        e = u - self._up[r]
        e_c = np.clip(e, -self.dy, self.dy)
        return float(np.sum(self.ki * e_c) + self.k_last * u), u - e_c

    def backbone(self, u: float) -> float:
        """Monotonic backbone force (virgin state) — for checks."""
        e_c = np.clip(u, -self.dy, self.dy)
        return float(np.sum(self.ki * e_c) + self.k_last * u)

    def trial(self, d, v, dt):
        return np.array([self._force(r, float(d[r]))[0]
                         for r in range(self.rows)])

    def commit(self, d, v, dt):
        for r in range(self.rows):
            self._up[r] = self._force(r, float(d[r]))[1] \
                if len(self.ki) else self._up[r]


class WenComponent:
    """Bouc-Wen hysteresis (1 row uniaxial or 2 rows biaxial)."""

    def __init__(self, k_lin: float, q: float, uy: float, *, rows: int = 1,
                 beta: float = 0.5, gamma: float = 0.5, eta: float = 1.0,
                 a2k: float = 0.0, mu: float = 2.0):
        self.k_lin = float(k_lin)
        self.q = float(q)
        self.uy = float(uy)
        self.rows = int(rows)
        self.beta = float(beta)
        self.gamma = float(gamma)
        self.eta = float(eta)
        self.a2k = float(a2k)
        self.mu = float(mu)
        self._z = np.zeros(self.rows)
        self._u = np.zeros(self.rows)

    def _zdot(self, z: np.ndarray, du: np.ndarray) -> np.ndarray:
        nz = float(np.linalg.norm(z))
        zu = float(z @ du)
        if nz == 0.0:
            return du / self.uy
        s = math.copysign(1.0, zu) if zu != 0.0 else 0.0
        fac = (self.gamma + self.beta * s) * nz ** (self.eta - 2.0)
        return (du - fac * zu * z) / self.uy

    def _z_at(self, u: np.ndarray) -> np.ndarray:
        du = u - self._u
        span = float(np.max(np.abs(du))) / self.uy
        n = max(1, int(math.ceil(span / 0.02)))
        h = du / n
        z = self._z.copy()
        for _ in range(n):
            k1 = self._zdot(z, h)
            k2 = self._zdot(z + 0.5 * k1, h)
            k3 = self._zdot(z + 0.5 * k2, h)
            k4 = self._zdot(z + k3, h)
            z = z + (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0
        return z

    def _force(self, u: np.ndarray) -> np.ndarray:
        z = self._z_at(u)
        F = self.k_lin * u + self.q * z
        if self.a2k:
            F = F + self.a2k * np.sign(u) * np.abs(u) ** self.mu
        return F

    def trial(self, d, v, dt):
        return self._force(np.asarray(d[:self.rows], dtype=float))

    def commit(self, d, v, dt):
        u = np.asarray(d[:self.rows], dtype=float)
        self._z = self._z_at(u)
        self._u = u.copy()


# --------------------------------------------------------------------------- #
# multi-row modal integrator
# --------------------------------------------------------------------------- #
def preload(dev, d0: np.ndarray, steps: int = 20) -> np.ndarray:
    """Drive a device monotonically from 0 to the gravity deformation
    ``d0`` (committing each sub-step, zero velocity); returns ``F0``."""
    d0 = np.asarray(d0, dtype=float)
    if not np.any(d0):
        return np.asarray(dev.trial(d0, np.zeros_like(d0), 1.0), dtype=float)
    for k in range(1, steps + 1):
        dk = d0 * (k / steps)
        dev.commit(dk, np.zeros_like(dk), 1.0)
    return np.asarray(dev.trial(d0, np.zeros_like(d0), 1.0), dtype=float)


def fna_integrate(omega, zeta, gamma, ag, dt, devices: List[object],
                  B: Optional[np.ndarray], k_lin: Optional[Sequence[float]],
                  d0: Optional[np.ndarray] = None,
                  F0: Optional[np.ndarray] = None,
                  maxit: int = 200, tol: float = 1.0e-10,
                  p_ext: Optional[np.ndarray] = None):
    """Integrate the FNA modal equations with multi-row devices.

    ``devices`` own consecutive rows of ``B`` (``n_rows x n_modes``) in
    order; ``k_lin`` per row; ``d0`` / ``F0`` the gravity-state row
    deformations / device forces (zeros when None).  Returns ``(q, qd,
    qdd, F_dev)`` like :func:`skyframe.core.fna.fna_modal_th` (``F_dev``:
    TOTAL row forces)."""
    w = np.asarray(omega, dtype=float)
    z = np.asarray(zeta, dtype=float)
    g = np.asarray(gamma, dtype=float)
    nm = len(w)
    nr = sum(dv.rows for dv in devices)
    Bm = (np.zeros((0, nm)) if B is None
          else np.asarray(B, dtype=float).reshape(nr, nm))
    kl = (np.zeros(nr) if k_lin is None
          else np.asarray(k_lin, dtype=float).reshape(nr))
    D0 = np.zeros(nr) if d0 is None else np.asarray(d0, dtype=float)
    FF0 = np.zeros(nr) if F0 is None else np.asarray(F0, dtype=float)
    sl = []
    r0 = 0
    for dv in devices:
        sl.append(slice(r0, r0 + dv.rows))
        r0 += dv.rows

    def forces(dd, vv):
        return np.concatenate([np.asarray(dv.trial(dd[s], vv[s], dt),
                                          dtype=float)
                               for dv, s in zip(devices, sl)]) \
            if devices else np.zeros(0)

    n = len(ag)
    c = 2.0 * z * w
    k = w * w
    beta, gam = 0.25, 0.5
    keff = 1.0 + c * gam * dt + k * beta * dt * dt
    q = np.zeros(nm)
    v = np.zeros(nm)
    a = np.zeros(nm)
    qh = np.zeros((n, nm))
    vh = np.zeros((n, nm))
    ah = np.zeros((n, nm))
    fh = np.zeros((n, nr))
    p_nl = np.zeros(nm)
    for kstep in range(n):
        ag1 = ag[kstep + 1] if kstep + 1 < n else 0.0
        p = -g * ag1
        if p_ext is not None:
            p = p + p_ext[kstep]
        v_pred = v + dt * (1.0 - gam) * a
        q_pred = q + dt * v + dt * dt * (0.5 - beta) * a
        for it in range(maxit):
            a1 = (p + p_nl - c * v_pred - k * q_pred) / keff
            q1 = q_pred + beta * dt * dt * a1
            v1 = v_pred + gam * dt * a1
            if nr == 0:
                break
            dd = Bm @ q1
            F = forces(D0 + dd, Bm @ v1)
            p_new = -Bm.T @ (F - FF0 - kl * dd)
            err = float(np.max(np.abs(p_new - p_nl)))
            ref = 1.0 + float(np.max(np.abs(p_new)))
            if err <= tol * ref:
                p_nl = p_new
                a1 = (p + p_nl - c * v_pred - k * q_pred) / keff
                q1 = q_pred + beta * dt * dt * a1
                v1 = v_pred + gam * dt * a1
                break
            p_nl = p_nl + (1.0 if it < 10 else 0.5) * (p_new - p_nl)
        else:
            raise RuntimeError(
                f"FNA fixed-point iteration did not converge at step "
                f"{kstep + 1}/{n} after {maxit} sweeps; the link forces "
                "are too stiff for modal iteration — use direct "
                "integration (run_time_history) for this case")
        q, v, a = q1, v1, a1
        qh[kstep] = q
        vh[kstep] = v
        ah[kstep] = a
        if nr:
            dd = D0 + Bm @ q
            vv = Bm @ v
            fh[kstep] = forces(dd, vv)
            for dv, s in zip(devices, sl):
                dv.commit(dd[s], vv[s], dt)
    return qh, vh, ah, fh
