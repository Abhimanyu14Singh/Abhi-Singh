"""Shift-invert Lanczos on top of an existing factorisation (numpy only).

CONTRACT "Buckling with shells and links, sparse eigen and BucklingFEM".

The generalized symmetric eigenproblem

    K x = lambda G x          (K symmetric positive definite on the free
                               space, G symmetric, possibly indefinite)

is solved for its eigenpairs with the LARGEST positive ``theta = 1 /
lambda`` (the smallest positive ``lambda``: the lowest modes when ``G`` is
the mass, the critical buckling factors when ``G = -Kg``) by the Lanczos
method on the operator ``A = K^-1 G``, which is self-adjoint in the
``K`` inner product ``<x, y>_K = x^T K y``.  The only access to ``K`` is a
SOLVE callback (one linear static solve on the caller's factored system —
OpenSees' factor-once UmfPack domain in the engine) that returns the
displacement ``x = K^-1 f`` of a load vector ``f``.  Every vector is kept
together with its LOAD representation (``K x = f`` exactly, the same
combination of applied loads), so every ``K`` inner product is a plain dot
product ``x . f_y`` — no matrix of ``K`` is ever formed (Betti: the dot
product is symmetric through any constraint transformation, see
:mod:`skyframe.engine.ritz_engine`).

Algorithm (deterministic):

* start: ``x0 = K^-1 G y0`` with ``y0`` a fixed pseudo-random vector on the
  caller's free DOFs (seeded) — the start already lies in ``range(K^-1 G)``
  (no components in the null space of ``G``: the "purified" start);
* step ``j``: ``w = K^-1 (G q_j)``, ``K w = G q_j``; ``alpha_j =
  q_j . G q_j``; full re-orthogonalisation in the ``K`` inner product
  (classical Gram-Schmidt, applied twice) against every previous ``q``;
  ``beta_j = sqrt(w . K w)``;
* the Ritz values are the eigenvalues of the tridiagonal ``T``; a Ritz pair
  ``(theta_i, s_i)`` is converged when ``|beta_m s_mi| <= tol * |theta_i|``
  (the exact residual norm of the Ritz pair in the ``K^-1`` norm);
* the run stops when the ``nev`` largest positive Ritz values are all
  converged, when the Krylov space is exhausted (``beta`` at round-off:
  every eigenpair the start vector can reach has been found — e.g. fewer
  compressed members than requested modes), or at ``max_steps``.

Ritz vectors are returned ``K``-orthonormal (``x^T K x = 1``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np

#: default relative residual tolerance of a converged Ritz pair
TOL = 1e-10
#: seed of the deterministic start vector
SEED = 20261012


@dataclass
class LanczosResult:
    thetas: List[float]                 # descending positive theta
    vectors: List[np.ndarray]           # K-orthonormal Ritz vectors
    loads: List[np.ndarray]             # K x for each vector (load form)
    steps: int = 0                      # solves after the start
    converged: bool = False
    exhausted: bool = False
    residuals: List[float] = field(default_factory=list)


def start_vector(n: int, free: Optional[np.ndarray] = None,
                 seed: int = SEED) -> np.ndarray:
    """Deterministic pseudo-random vector (zero outside ``free``)."""
    rng = np.random.default_rng(seed)
    y = rng.uniform(-1.0, 1.0, size=n)
    if free is not None:
        y = np.where(free, y, 0.0)
    return y


def lanczos(solve: Callable[[np.ndarray], np.ndarray],
            gmul: Callable[[np.ndarray], np.ndarray],
            y0: np.ndarray, nev: int, tol: float = TOL,
            max_steps: Optional[int] = None,
            check_every: int = 1,
            lock_x: Optional[np.ndarray] = None,
            lock_f: Optional[np.ndarray] = None) -> LanczosResult:
    """Largest positive eigenvalues ``theta`` of ``K^-1 G`` (see module).

    ``solve(f) -> x`` solves ``K x = f``; ``gmul(x) -> f`` multiplies by
    ``G``; vectors are flat float arrays of one fixed length.
    """
    n = int(y0.size)
    if max_steps is None:
        max_steps = max(4 * nev + 40, 80)
    max_steps = max(1, min(int(max_steps), n))
    locked = lock_x is not None and len(lock_x) > 0

    def deflate(w, kw):
        if locked:
            for _ in range(2):
                c = lock_f @ w
                w = w - c @ lock_x
                kw = kw - c @ lock_f
        return w, kw
    f = gmul(y0)
    x = solve(f)
    x, f = deflate(x, f)
    nrm2 = float(x @ f)
    if not (nrm2 > 0.0) or not math.isfinite(nrm2):
        return LanczosResult([], [], [], 0, False, True)
    nrm = math.sqrt(nrm2)
    Q = np.zeros((max_steps + 1, n))
    P = np.zeros((max_steps + 1, n))
    Q[0] = x / nrm
    P[0] = f / nrm
    alphas: List[float] = []
    betas: List[float] = []
    H = np.zeros((max_steps + 1, max_steps + 1))
    steps = 0
    exhausted = False
    theta = S = None
    m = 0
    scale = 0.0
    for j in range(max_steps):
        gq = gmul(Q[j])
        w = solve(gq)
        kw = gq
        a = float(Q[j] @ kw)
        hcol = Q[:j + 1] @ gq                    # explicit projection
        H[:j + 1, j] = hcol
        H[j, :j + 1] = hcol
        steps += 1
        w = w - a * Q[j]
        kw = kw - a * P[j]
        if j > 0:
            w = w - betas[-1] * Q[j - 1]
            kw = kw - betas[-1] * P[j - 1]
        for _ in range(2):                       # full re-orthogonalisation
            c = P[:j + 1] @ w
            w = w - c @ Q[:j + 1]
            kw = kw - c @ P[:j + 1]
        w, kw = deflate(w, kw)
        alphas.append(a)
        b2 = float(w @ kw)
        b = math.sqrt(b2) if b2 > 0.0 else 0.0
        m = j + 1
        # Rayleigh-Ritz on the EXPLICIT projection Q^T G Q (Q^T K Q = I):
        # with full re-orthogonalisation it equals the Lanczos tridiagonal
        # in exact arithmetic and keeps the small eigenvalues accurate
        theta, S = np.linalg.eigh(H[:m, :m])
        scale = max(scale, float(np.max(np.abs(theta))))
        if b <= 1e-13 * max(scale, 1e-300) or m == n:
            exhausted = True
            b = 0.0
        if exhausted or (m % check_every == 0) or m == max_steps:
            if _converged(theta, S, b, nev, tol, scale) or exhausted:
                betas.append(b)
                break
        if m == max_steps:
            betas.append(b)
            break
        betas.append(b)
        Q[j + 1] = w / b
        P[j + 1] = kw / b
    b_last = betas[-1] if betas else 0.0
    order = [i for i in np.argsort(-theta) if theta[i] > 0.0]
    keep = order[:nev]
    vecs, lds, res = [], [], []
    for i in keep:
        s = S[:, i]
        vecs.append(s @ Q[:m])
        lds.append(s @ P[:m])
        res.append(abs(b_last * s[-1]) / max(abs(theta[i]), 1e-300))
    conv = _converged(theta, S, b_last, nev, tol, scale)
    return LanczosResult([float(theta[i]) for i in keep], vecs, lds, steps,
                         bool(conv), exhausted, res)


def _converged(theta, S, b: float, nev: int, tol: float,
               scale: float) -> bool:
    pos = [i for i in np.argsort(-theta) if theta[i] > 0.0][:nev]
    if len(pos) < nev:
        return False
    return all(abs(b * S[-1, i]) <= tol * abs(theta[i]) for i in pos)


def eigs(solve: Callable[[np.ndarray], np.ndarray],
         gmul: Callable[[np.ndarray], np.ndarray],
         free: np.ndarray, nev: int, tol: float = TOL,
         max_steps: Optional[int] = None, max_passes: int = 4,
         max_refine: int = 6) -> LanczosResult:
    """Lanczos with locking restarts and block refinement.

    A single-vector Krylov space holds only one vector of an exactly
    repeated eigenvalue (a doubly symmetric column buckles in two equal
    modes; a symmetric building has equal-period pairs).  After the first
    run its Ritz vectors are LOCKED and a new run starts from a different
    deterministic vector, K-orthogonal to them; Ritz values at or above
    the current ``nev``-th one (relative 1e-8) are merged.  Passes stop
    when a pass finds nothing new, the free space is used up, or after
    ``max_passes``.  Finally the kept pairs are REFINED by block inverse
    iteration (``y_i = K^-1 G x_i`` for the unconverged pairs, Rayleigh-
    Ritz on ``span(x, y)``) until every pair's exact residual
    ``||K^-1 G x - theta x||_K <= tol * |theta|`` or ``max_refine``
    rounds.
    """
    n = int(free.size)
    Zx: List[np.ndarray] = []
    Zf: List[np.ndarray] = []
    steps = 0
    exhausted_any = False
    nfree = int(np.count_nonzero(free))
    for p in range(max_passes):
        y0 = start_vector(n, free, SEED + 7919 * p)
        lx = np.array(Zx) if Zx else None
        lf = np.array(Zf) if Zf else None
        ms = max_steps if p == 0 else min(max_steps or 10 ** 9,
                                           2 * nev + 20)
        r = lanczos(solve, gmul, y0, nev, tol, ms, lock_x=lx, lock_f=lf)
        steps += r.steps + 1
        exhausted_any = exhausted_any or r.exhausted
        new = list(zip(r.thetas, r.vectors, r.loads))
        if p > 0:
            ref = _ritz(gmul, Zx, Zf)[0]
            cut = ref[nev - 1] if len(ref) >= nev else 0.0
            new = [t for t in new if t[0] >= cut * (1.0 - 1e-8)]
        if not new:
            break
        Zx, Zf = _korth(Zx + [t[1] for t in new], Zf + [t[2] for t in new])
        if len(Zx) >= nfree or (r.exhausted and p == 0 and len(new) < nev
                                and not r.thetas):
            break
    thetas, X, F = _ritz(gmul, Zx, Zf)
    keep = [i for i in range(len(thetas)) if thetas[i] > 0.0][:nev]
    thetas = [thetas[i] for i in keep]
    X = [X[i] for i in keep]
    F = [F[i] for i in keep]
    res: List[float] = []
    conv = False
    # refinement = subspace iteration from the Lanczos vectors: the new
    # basis is ONLY the fresh solves y = K^-1 G x, whose load form G x is
    # exact (the late Lanczos vectors carry the solve round-off amplified
    # by 1 / beta, ~1e-8 on the smallest wanted theta); the first sweep
    # always runs, the residual of sweep k is measured with the solves of
    # sweep k + 1 (consistent load forms on both sides)
    for it in range(max_refine + 1):
        if not thetas:
            conv = True
            break
        Y, FY = [], []
        for x in X:
            g = gmul(x)
            Y.append(solve(g))
            FY.append(g)
            steps += 1
        if it > 0:
            res = []
            for th, x, f, y, g in zip(thetas, X, F, Y, FY):
                r2 = float((y - th * x) @ (g - th * f))
                res.append(math.sqrt(max(r2, 0.0)) / max(abs(th), 1e-300))
            if all(v <= tol for v in res):
                conv = True
                break
        Zx, Zf = _korth(Y, FY)
        if len(Zx) < len(X):                     # keep the span complete
            Zx, Zf = _korth(Zx + list(X), Zf + list(F))
        th2, X2, F2 = _ritz(gmul, Zx, Zf)
        keep = [i for i in range(len(th2)) if th2[i] > 0.0][:nev]
        thetas = [th2[i] for i in keep]
        X = [X2[i] for i in keep]
        F = [F2[i] for i in keep]
    return LanczosResult(thetas, X, F, steps, conv, exhausted_any, res)


def _korth(xs: List[np.ndarray], fs: List[np.ndarray]):
    """K-orthonormalise vectors (two-pass Gram-Schmidt, drops dependent
    ones whose remaining K-norm is below 1e-6 of their own: such
    near-dependent remainders are round-off and would pollute the
    Rayleigh-Ritz basis)."""
    ox: List[np.ndarray] = []
    of: List[np.ndarray] = []
    for x, f in zip(xs, fs):
        n0 = float(x @ f)
        if not n0 > 0.0:
            continue
        for _ in range(2):
            for qx, qf in zip(ox, of):
                c = float(qf @ x)
                x = x - c * qx
                f = f - c * qf
        n1 = float(x @ f)
        if not n1 > 1e-12 * n0:
            continue
        s = math.sqrt(n1)
        ox.append(x / s)
        of.append(f / s)
    return ox, of


def _ritz(gmul, Zx, Zf):
    """Rayleigh-Ritz on K-orthonormal ``Zx`` (loads ``Zf``): descending."""
    if not Zx:
        return [], [], []
    Z = np.array(Zx)
    Fz = np.array(Zf)
    GZ = np.array([gmul(z) for z in Zx])
    H = Z @ GZ.T
    H = 0.5 * (H + H.T)
    mu, V = np.linalg.eigh(H)
    order = np.argsort(-mu)
    thetas = [float(mu[i]) for i in order]
    X = [V[:, i] @ Z for i in order]
    F = [V[:, i] @ Fz for i in order]
    return thetas, X, F
