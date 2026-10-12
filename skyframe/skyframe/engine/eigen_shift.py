"""Eigen modal case with frequency shift (center), cutoff frequency (radius)
and convergence tolerance — ETABS Modal case "Eigen" parameters.

CONTRACT "Eigen shift / cutoff / tolerance, modal history and FNA case
types, TH initial conditions, RS / steady-state modal damping".

Used by :meth:`OpenSeesEngine.run_modal` (one hook line) when the model's
``eigen_options`` (default MODAL case; a named eigen modal case copies its
keys there on its sub-engine model) hold any of ``shift`` / ``cutoff`` /
``tolerance``.  Otherwise nothing here runs (legacy eigen path).

Method.  The engine's mass is lumped, so ``K u = lambda M u`` reduces
EXACTLY to the massed DOFs ``m`` through the constrained flexibility
``G_mm`` (one linear static solve per massed DOF — see
:mod:`skyframe.engine.modal_condensed`)::

    A y = mu y,   A = M^1/2 G_mm M^1/2,   mu = 1/lambda,   y = M^1/2 u_m.

Shift-invert about ``sigma = (2 pi f_shift)^2``: the operator

    S = (I - sigma A)^-1 A        (eigenvalues 1 / (lambda - sigma))

is symmetric (a function of ``A``), and its dominant eigenvectors are the
modes NEAREST the shift.  Block subspace iteration with Rayleigh-Ritz on
``A`` (block ``p = min(n_massed, max(2 n, n + 8))``) runs until every one
of the ``n`` retained Ritz values changes by less than ``tolerance``
(relative) between two sweeps.  Retained = the ``n`` Ritz pairs nearest
the shift with ``|f - f_shift| <= cutoff`` (``cutoff = 0``: no radius), so
a cutoff can return FEWER than ``num_modes`` modes.  A shift that falls
exactly on an eigenvalue is nudged by 1e-7 (relative) — ETABS' automatic
shifting.  Full mode shapes: one more solve per mode under ``lambda M
u_m`` (mass-normalised, sign: largest massed component positive).  The
modes are reported in ASCENDING frequency.

``ModalResults.to_dict()["eigen"] = {"shift", "cutoff", "tolerance",
"sigma", "iterations", "converged", "found", "requested"}``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import openseespy.opensees as ops

from skyframe.core import wavec_dyn as _wcd
from skyframe.engine.opensees_engine import ModalResults

MAX_MASSED = 4000          # flexibility columns (one solve each)
MAX_SWEEPS = 1000
_MU_FLOOR = 1e-12


@dataclass
class ShiftModalResults(ModalResults):
    """:class:`ModalResults` + the eigen-parameter report."""

    eigen: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["eigen"] = dict(self.eigen)
        return d


def params(model) -> dict:
    """Validated eigen options of the analysis model ({} = legacy)."""
    eo = getattr(model, "eigen_options", None) or {}
    return _wcd.validate_eigen(eo, "eigen_options") if eo else {}


def run_modal_hook(eng, num_modes: Optional[int]):
    """``run_modal`` hook: the shift / cutoff modes, or None (legacy)."""
    model = eng.model
    if getattr(model, "modal_from_case", None):
        return None
    ep = params(model)
    if not ep:
        return None
    cache = eng.__dict__.setdefault("_eshift_cache", {})
    req = int(num_modes or model.num_modes)
    if req in cache:
        return cache[req]
    res = solve(eng, req, ep)
    cache[req] = res
    return res


def _flexibility(eng, dofs) -> np.ndarray:
    from skyframe.engine import modal_condensed as _mcd
    nm = len(dofs)
    G = np.zeros((nm, nm))
    for j, key in enumerate(dofs):
        _mcd._solve_loads(eng, [(key, 1.0)])
        G[:, j] = [ops.nodeDisp(t, d) for (t, d) in dofs]
    return 0.5 * (G + G.T)


def subspace(A: np.ndarray, n: int, sigma: float, tol: float,
             f_shift: float, cutoff: float, seed: int = 20240611):
    """Shift-invert subspace iteration (module docstring) on the symmetric
    ``A`` (``mu = 1/lambda``).  Returns ``(lambdas, Y, iterations,
    converged, sigma_used)`` with ``lambdas`` the retained eigenvalues
    (nearest the shift first) and ``Y`` their unit eigenvectors of ``A``."""
    nm = A.shape[0]
    p = min(nm, max(2 * n, n + 8))
    eye = np.eye(nm)
    sig = float(sigma)
    lu = None
    for _ in range(8):
        B = eye - sig * A
        try:
            import warnings
            import scipy.linalg as sla
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                lu = sla.lu_factor(B, check_finite=False)
            piv = np.abs(np.diag(lu[0]))
            if piv.min() > 1e-12 * max(piv.max(), 1.0):
                break
        except (ValueError, np.linalg.LinAlgError):
            pass
        lu = None
        sig = sig * (1.0 + 1e-7) if sig > 0.0 else 1e-7
    if lu is None:
        raise RuntimeError("eigen shift: (K - sigma M) is singular at the "
                           "shift")
    import scipy.linalg as sla
    rng = np.random.default_rng(seed)
    X = rng.standard_normal((nm, p))
    prev = None
    it = 0
    converged = False
    lam_sel = np.zeros(0)
    Ysel = np.zeros((nm, 0))
    w_shift = 2.0 * math.pi * f_shift

    def select(lams, vecs):
        f = np.sqrt(np.maximum(lams, 0.0)) / (2.0 * math.pi)
        dist = np.abs(f - f_shift)
        ok = np.isfinite(lams) & (lams > 0.0)
        if cutoff > 0.0:
            ok &= dist <= cutoff * (1.0 + 1e-12)
        idx = [i for i in np.argsort(dist, kind="stable") if ok[i]][:n]
        return lams[idx], vecs[:, idx]

    for it in range(1, MAX_SWEEPS + 1):
        Z = sla.lu_solve(lu, A @ X, check_finite=False)
        Q, _r = np.linalg.qr(Z)
        H = Q.T @ A @ Q
        H = 0.5 * (H + H.T)
        mu, V = np.linalg.eigh(H)
        mu_max = max(float(np.max(np.abs(np.diag(A)))), 1e-300)
        with np.errstate(divide="ignore"):
            lams = np.where(mu > _MU_FLOOR * mu_max, 1.0 / mu, np.inf)
        R = Q @ V
        # order the Ritz basis by distance to the shift (next sweep's block)
        d_all = np.abs(np.sqrt(np.where(np.isfinite(lams), lams, 0.0))
                       - w_shift)
        d_all = np.where(np.isfinite(lams), d_all, np.inf)
        order = np.argsort(d_all, kind="stable")
        X = R[:, order]
        lam_sel, Ysel = select(lams, R)
        if prev is not None and len(prev) == len(lam_sel) and len(lam_sel):
            ch = np.max(np.abs(lam_sel - prev) / np.abs(lam_sel))
            if ch <= tol:
                converged = True
                break
        prev = lam_sel.copy()
        if p == nm and it >= 2:
            # full space: Rayleigh-Ritz of A is exact after one sweep
            converged = True
            break
    return lam_sel, Ysel, it, converged, sig


def solve(eng, req: int, ep: dict) -> ShiftModalResults:
    from skyframe.engine import eigen_refine as _eref
    from skyframe.engine import modal_condensed as _mcd
    asm = eng._build()
    n_massed = asm.free_massed_dofs()
    n = min(req, n_massed)
    f_s = float(ep.get("shift", 0.0))
    f_c = float(ep.get("cutoff", 0.0))
    tol = float(ep.get("tolerance", _wcd.EIGEN_DEFAULT_TOL))
    info = {"shift": f_s, "cutoff": f_c, "tolerance": tol,
            "sigma": (2.0 * math.pi * f_s) ** 2, "requested": req,
            "found": 0, "iterations": 0, "converged": True}
    if n <= 0:
        return ShiftModalResults([], [], [], {}, eigen=info)
    dofs = _mcd.massed_dofs(asm)
    nm = len(dofs)
    if nm > MAX_MASSED:
        raise ValueError(f"eigen shift / cutoff: {nm} massed DOFs exceed the "
                         f"{MAX_MASSED} cap (use rigid diaphragms or the "
                         "default eigen solver)")
    m = np.array([asm.mass_map[k] for k in dofs], dtype=float)
    G = _flexibility(eng, dofs)
    s = np.sqrt(m)
    A = s[:, None] * G * s[None, :]
    lams, Y, its, conv, sig = subspace(A, n, info["sigma"], tol, f_s, f_c)
    info.update({"iterations": int(its), "converged": bool(conv),
                 "sigma": float(sig), "found": int(len(lams))})
    order = np.argsort(lams, kind="stable")           # ascending frequency
    tags = list(asm.node_coords)
    lambdas: List[float] = []
    shapes: Dict[int, Dict[int, List[float]]] = {}
    for mode, k in enumerate(order, start=1):
        lam = float(lams[k])
        y = Y[:, k]
        i_big = int(np.argmax(np.abs(y)))
        if y[i_big] < 0.0:
            y = -y
        phi_m = y / s                                  # phi^T M phi = 1
        _mcd._solve_loads(eng, [(dofs[i], lam * m[i] * phi_m[i])
                                for i in range(nm) if phi_m[i] != 0.0])
        shapes[mode] = {t: list(ops.nodeDisp(t)) for t in tags}
        lambdas.append(lam)
    _eref._release(eng)
    periods = [2.0 * math.pi / math.sqrt(lam) for lam in lambdas]
    freqs = [1.0 / T for T in periods]
    part = eng._participation(asm, periods, shapes) if periods else []
    if not conv:
        eng.__dict__.setdefault("_solver_notes", []).append(
            f"eigen shift: subspace iteration stopped after {its} sweeps "
            f"without reaching tolerance {tol:g}")
    return ShiftModalResults(periods, freqs, part, shapes, eigen=info)


__all__ = ["ShiftModalResults", "run_modal_hook", "subspace", "solve"]
