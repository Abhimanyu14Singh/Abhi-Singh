"""FNA — Fast Nonlinear Analysis core (v0.24), pure numpy.

Modal superposition of a LINEAR structure whose only nonlinearity lives in
device links (the classic Wilson FNA).  The engine supplies

* the uncoupled modal properties (``omega``, ``zeta``, ``gamma`` per mode,
  unit modal mass normalization),
* per device COMPONENT a modal deformation row ``b`` (``d_c = b_c . q``),
  the linear stiffness ``k_lin`` already INCLUDED in the modal basis for
  that component, and a device-law object from this module,

and this module integrates the modal equations

    q_i'' + 2 zeta_i omega_i q_i' + omega_i^2 q_i
        = -gamma_i * ag(t) - sum_c b_ci * (F_c - k_lin_c * d_c)

with Newmark constant-average acceleration per mode (gamma=1/2, beta=1/4 —
the SAME integrator/step convention as the engine's direct-integration
``run_th``: state k is at t = (k+1)*dt, loads evaluated at the step END,
zero initial conditions with a zero previous acceleration, exactly like
OpenSees' Newmark start-up) and the classic FNA fixed-point iteration per
time step: solve all modal SDOFs with the link pseudo-forces from the
previous iterate, recompute link deformations/velocities, re-evaluate the
device laws, repeat to tolerance.  Device hysteretic states commit only
after the step converges.  After 10 stalled sweeps the pseudo-force update
is under-relaxed (factor 0.5); a step that does not converge within
``maxit`` raises ``RuntimeError`` naming direct integration as the
fallback (very stiff links relative to the modal stiffness can defeat the
fixed point — a documented FNA limitation).

Device laws (each validated by hand in the test suite; all forces along
the component's deformation measure ``d``, tension/opening positive):

* :class:`MaxwellDamper` — series spring ``k`` + linear dashpot ``cd``
  (alpha = 1): ``dF/dt = k (v - F/cd)`` integrated with the trapezoidal
  rule (A-stable, second order), matching the OpenSees ViscousDamper law;
* :class:`GapComponent` — compression contact engaging once the pair
  CLOSES by more than ``gap``: ``F = k (d + gap)`` for ``d < -gap``,
  else 0;
* :class:`HookComponent` — tension mirror: ``F = k (d - slack)`` for
  ``d > slack``, else 0;
* :class:`BilinearComponent` — bilinear KINEMATIC hardening (Steel01):
  elastic ``k1``, yield ``Fy``, post-yield ``k2`` — the exact
  return-mapping law of the wave-16 hand benchmark.

Units: kN, m, tonne, s.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

import numpy as np


# --------------------------------------------------------------------------- #
# device laws
# --------------------------------------------------------------------------- #
class MaxwellDamper:
    """Maxwell viscous damper (spring k in series with dashpot cd, alpha=1).

    ``trial(d, v, dt)`` returns the damper force at the step end from the
    COMMITTED state and the trial end-of-step component velocity ``v``
    (the deformation ``d`` is unused — the Maxwell force is
    velocity-history driven); trapezoidal rule on
    ``dF/dt = k (v - F/cd)``.
    """

    def __init__(self, k: float, cd: float):
        self.k = float(k)
        self.cd = float(cd)
        self._F = 0.0          # committed damper force
        self._v = 0.0          # committed component velocity

    def trial(self, d: float, v: float, dt: float) -> float:
        h = dt * self.k / (2.0 * self.cd)
        return ((self._F * (1.0 - h) + 0.5 * dt * self.k * (self._v + v))
                / (1.0 + h))

    def commit(self, d: float, v: float, dt: float) -> None:
        self._F = self.trial(d, v, dt)
        self._v = v


class GapComponent:
    """Compression-only contact: engages once the pair closes past ``gap``."""

    def __init__(self, k: float, gap: float):
        self.k = float(k)
        self.gap = float(gap)

    def trial(self, d: float, v: float, dt: float) -> float:
        return self.k * (d + self.gap) if d < -self.gap else 0.0

    def commit(self, d: float, v: float, dt: float) -> None:
        pass                                      # stateless


class HookComponent:
    """Tension-only hook: engages once the pair opens past ``slack``."""

    def __init__(self, k: float, slack: float):
        self.k = float(k)
        self.slack = float(slack)

    def trial(self, d: float, v: float, dt: float) -> float:
        return self.k * (d - self.slack) if d > self.slack else 0.0

    def commit(self, d: float, v: float, dt: float) -> None:
        pass                                      # stateless


class BilinearComponent:
    """Bilinear kinematic-hardening spring (Steel01: k1, Fy, k2).

    Return mapping with the kinematic hardening modulus
    ``H = k1 k2 / (k1 - k2)`` — identical to the wave-16 hand benchmark
    (``_numpy_bilinear_newmark``); ``k2 == k1`` degenerates to elastic.
    """

    def __init__(self, k1: float, k2: float, Fy: float):
        self.k1 = float(k1)
        self.k2 = float(k2)
        self.Fy = float(Fy)
        self._up = 0.0         # committed plastic deformation
        self._alpha = 0.0      # committed back-force (kinematic hardening)

    def _map(self, d: float) -> Tuple[float, float]:
        """(force, plastic multiplier increment * sign) for a trial d."""
        f_tr = self.k1 * (d - self._up)
        xi = f_tr - self._alpha
        if abs(xi) <= self.Fy or self.k1 == self.k2:
            return f_tr, 0.0
        H = self.k1 * self.k2 / (self.k1 - self.k2)
        dg = (abs(xi) - self.Fy) / (self.k1 + H)
        s = math.copysign(1.0, xi)
        return f_tr - self.k1 * dg * s, dg * s

    def trial(self, d: float, v: float, dt: float) -> float:
        return self._map(d)[0]

    def commit(self, d: float, v: float, dt: float) -> None:
        _f, dgs = self._map(d)
        if dgs != 0.0:
            H = self.k1 * self.k2 / (self.k1 - self.k2)
            self._up += dgs
            self._alpha += H * dgs


# --------------------------------------------------------------------------- #
# modal FNA integrator
# --------------------------------------------------------------------------- #
def fna_modal_th(omega: Sequence[float], zeta: Sequence[float],
                 gamma: Sequence[float], ag: Sequence[float], dt: float,
                 comps: Optional[List[object]] = None,
                 B: Optional[np.ndarray] = None,
                 k_lin: Optional[Sequence[float]] = None,
                 maxit: int = 200, tol: float = 1.0e-10,
                 ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Integrate the FNA modal equations (see the module docstring).

    ``omega``/``zeta``/``gamma``: per-mode arrays (unit modal masses);
    ``ag``: ground acceleration SAMPLES (already scaled, m/s^2; sample k
    applies at t = k*dt, matching the engine's Path series) — the run is
    ``n = len(ag)`` steps and, like the engine, the load past the record
    end is zero; ``comps``/``B``/``k_lin``: device components, their modal
    deformation rows (``n_comp x n_modes``) and included linear
    stiffnesses.  Returns ``(q, qd, qdd, F_dev)`` histories, each
    ``n x n_modes`` (``F_dev``: ``n x n_comp`` TOTAL device forces),
    row k = the state at t = (k+1)*dt.
    """
    w = np.asarray(omega, dtype=float)
    z = np.asarray(zeta, dtype=float)
    g = np.asarray(gamma, dtype=float)
    nm = len(w)
    comps = comps or []
    nc = len(comps)
    Bm = (np.zeros((0, nm)) if B is None
          else np.asarray(B, dtype=float).reshape(nc, nm))
    kl = (np.zeros(0) if k_lin is None
          else np.asarray(k_lin, dtype=float).reshape(nc))

    n = len(ag)
    c = 2.0 * z * w                       # modal damping (unit mass)
    k = w * w                             # modal stiffness
    beta, gam = 0.25, 0.5
    keff = 1.0 + c * gam * dt + k * beta * dt * dt

    q = np.zeros(nm)
    v = np.zeros(nm)
    a = np.zeros(nm)                      # OpenSees-style zero initial accel
    qh = np.zeros((n, nm))
    vh = np.zeros((n, nm))
    ah = np.zeros((n, nm))
    fh = np.zeros((n, nc))
    p_nl = np.zeros(nm)                   # link pseudo-force (warm start)

    for kstep in range(n):
        ag1 = ag[kstep + 1] if kstep + 1 < n else 0.0
        p = -g * ag1
        # Newmark predictor pieces (constant across the fixed point)
        v_pred = v + dt * (1.0 - gam) * a
        q_pred = q + dt * v + dt * dt * (0.5 - beta) * a
        for it in range(maxit):
            a1 = (p + p_nl - c * v_pred - k * q_pred) / keff
            q1 = q_pred + beta * dt * dt * a1
            v1 = v_pred + gam * dt * a1
            if nc == 0:
                break
            d = Bm @ q1
            vd = Bm @ v1
            F = np.array([comps[ci].trial(d[ci], vd[ci], dt)
                          for ci in range(nc)])
            p_new = -Bm.T @ (F - kl * d)
            err = float(np.max(np.abs(p_new - p_nl)))
            ref = 1.0 + float(np.max(np.abs(p_new)))
            if err <= tol * ref:
                p_nl = p_new
                a1 = (p + p_nl - c * v_pred - k * q_pred) / keff
                q1 = q_pred + beta * dt * dt * a1
                v1 = v_pred + gam * dt * a1
                break
            # under-relax stalled sweeps (classic FNA safeguard)
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
        if nc:
            d = Bm @ q
            vd = Bm @ v
            for ci in range(nc):
                fh[kstep, ci] = comps[ci].trial(d[ci], vd[ci], dt)
                comps[ci].commit(d[ci], vd[ci], dt)
    return qh, vh, ah, fh
