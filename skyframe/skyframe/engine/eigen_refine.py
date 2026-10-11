"""Verification and Rayleigh-Ritz cleanup of OpenSees eigenpairs.

Why (investigated, v1.20 issue "RS cross-direction leakage"): on a doubly
symmetric building an X response-spectrum case leaked ~4e-4 of its
response into Y on the default (BandGeneral) path, against ~1e-10 on the
condensed (UmfPack) path.  Measured with the assembled ``K`` / ``M``
(``GimmeMCK`` + ``FullGeneral``) on the quick building and the twin-tower
models:

* ``-fullGenLapack`` (used whenever ``n == n_massed`` or Arpack fails —
  i.e. on almost every small model) returns eigenpairs with tiny residuals
  ``||K phi - lam M phi|| / ||K phi|| <= 1e-11`` but, inside a DEGENERATE
  (equal-period) pair, two vectors that are NOT mass-orthogonal
  (``|phi_i^T M phi_j| / (|phi_i|_M |phi_j|_M)`` up to 1.5e-2).  LAPACK's
  ``dggev`` is a non-symmetric solver: any two independent vectors of the
  eigenspace are a valid answer to it.  The modal expansion
  ``r = sum Gamma_i phi_i`` (``Gamma = L / m``) assumes M-orthogonality,
  so the X excitation is mis-split over the pair and leaks into Y — CQC
  itself is rotation-invariant within a degenerate pair (``rho = 1``) and
  is not the cause.
* shift-invert ``-genBandArpack`` with the singular (lumped) mass matrix can
  return correct eigenvalues with eigenvectors polluted in the massless
  DOFs (residual ~1, the classic missing "purification" step), seen on
  the twin-tower models when ``n`` is close to ``rank(M)``.

Fix: :func:`refine` verifies every legacy eigen solution with ONE inverse
iteration ``y_i = K^-1 M phi_i`` (one linear static solve per mode on the
standard elastic domain) and measures

* the eigen residual in flexibility form over ALL DOFs,
  ``max |lam_i y_i - phi_i| / max |phi_i|`` (zero for an exact eigenpair:
  ``K phi = lam M phi  <=>  phi = lam K^-1 M phi``), and
* the mass-orthogonality error of the normalised set.

When both are within :data:`TOL` the OpenSees eigenpairs are returned
UNCHANGED (same objects: every non-degenerate model is byte-identical).
Otherwise a Rayleigh-Ritz step on the inverse-iterated subspace
``Z = span(lam_i y_i)`` replaces them: with the lumped mass living on the
massed DOFs only, ``Z^T K Z = Z^T M Phi Lam`` and ``Z^T M Z`` are exact
small matrices, the reduced generalized problem is solved densely and the
Ritz vectors (mass-normalised, sign fixed so the largest massed component
is positive — the modal_condensed convention) are inherently purified
(``K^-1 M`` maps the massless-DOF pollution out).  The step is repeated
(at most :data:`MAX_ITER` times) until the residual check passes.  For an
exact eigenspace the Ritz values equal the eigenvalues to round-off and
the result is the M-orthonormal basis of each degenerate eigenspace.
"""

from __future__ import annotations

import logging
import weakref
from typing import Dict, List, Optional, Tuple

import numpy as np
import openseespy.opensees as ops

_log = logging.getLogger(__name__)

# residual / orthogonality tolerance below which the OpenSees eigenpairs
# are kept untouched (measured on non-degenerate models: residual
# <= 1e-11, orthogonality <= 1e-13)
TOL = 1e-9
# Rayleigh-Ritz iterations at most (one is enough on every probed model)
MAX_ITER = 3
# consecutive eigenvalues closer than this (relative) form a DEGENERATE
# cluster whose basis is made canonical by :func:`align_basis` (measured
# splits of exactly repeated eigenvalues: <= 3e-15; the closest distinct
# pair of the test models is ~1e-3 apart)
CLUSTER_RTOL = 1e-9
# participation directions in canonical order: (dof, key)
_DIRS = ((1, "ux"), (2, "uy"), (6, "rz"))


def _solve_mode(eng, dofs, m, phi_m, tags) -> Dict[int, List[float]]:
    """Displacement field under the load ``M phi`` (one linear solve)."""
    from skyframe.engine import modal_condensed as _mcd
    _mcd._solve_loads(eng, [(dofs[k], m[k] * phi_m[k])
                            for k in range(len(dofs)) if phi_m[k] != 0.0])
    return {t: list(ops.nodeDisp(t)) for t in tags}


def _massed(shape, dofs) -> np.ndarray:
    return np.array([shape[t][d - 1] for (t, d) in dofs], dtype=float)


def check(eng, dofs, m, tags, lambdas, shapes
          ) -> Tuple[float, float, List[Dict[int, List[float]]]]:
    """``(residual, orthogonality error, inverse iterates)`` of an
    eigen solution (see the module docstring)."""
    n = len(lambdas)
    Phi = np.column_stack([_massed(shapes[i], dofs) for i in range(1, n + 1)])
    ys = [_solve_mode(eng, dofs, m, Phi[:, i - 1], tags)
          for i in range(1, n + 1)]
    resid = 0.0
    for i in range(1, n + 1):
        phi = np.array([shapes[i][t] for t in tags], dtype=float)
        y = np.array([ys[i - 1][t] for t in tags], dtype=float)
        big = float(np.max(np.abs(phi))) if phi.size else 0.0
        diff = float(np.max(np.abs(lambdas[i - 1] * y - phi)))
        resid = max(resid, diff / big if big > 0.0 else float("inf"))
    orth = orthogonality_error(Phi, m)
    return resid, orth, ys


def orthogonality_error(Phi: np.ndarray, m: np.ndarray) -> float:
    """``max |phi_i^T M phi_j| / (|phi_i|_M |phi_j|_M)`` over ``i != j``."""
    Mr = Phi.T @ (m[:, None] * Phi)
    d = np.sqrt(np.abs(np.diag(Mr)))
    if np.any(d <= 0.0):
        return float("inf")
    E = Mr / np.outer(d, d) - np.eye(Phi.shape[1])
    return float(np.max(np.abs(E))) if E.size else 0.0


def ritz(dofs, m, tags, lambdas, shapes, ys
         ) -> Optional[Tuple[List[float], Dict[int, Dict[int, List[float]]]]]:
    """One Rayleigh-Ritz step on ``Z = [lam_i y_i]`` (``K Z = M Phi Lam``)."""
    n = len(lambdas)
    lam = np.asarray(lambdas, dtype=float)
    Phi = np.column_stack([_massed(shapes[i], dofs) for i in range(1, n + 1)])
    Zm = np.column_stack([_massed(ys[i], dofs) for i in range(n)]) * lam
    Kr = Zm.T @ (m[:, None] * Phi) * lam            # Z^T K Z
    Mr = Zm.T @ (m[:, None] * Zm)                   # Z^T M Z
    Kr = 0.5 * (Kr + Kr.T)
    Mr = 0.5 * (Mr + Mr.T)
    try:
        L = np.linalg.cholesky(Mr)
    except np.linalg.LinAlgError:
        return None
    Li = np.linalg.inv(L)
    w, V = np.linalg.eigh(Li @ Kr @ Li.T)
    if not np.all(np.isfinite(w)) or np.any(w <= 0.0):
        return None
    Q = Li.T @ V                                    # Q^T Mr Q = I
    Y = np.array([[ys[i][t] for t in tags] for i in range(n)],
                 dtype=float)                       # (mode, node, dof)
    out_l: List[float] = []
    out_s: Dict[int, Dict[int, List[float]]] = {}
    for k in range(n):
        q = Q[:, k] * lam                           # columns of Z = lam y
        vec_m = Zm @ Q[:, k]
        if vec_m[int(np.argmax(np.abs(vec_m)))] < 0.0:
            q = -q
        full = np.tensordot(q, Y, axes=1)           # (node, dof)
        out_s[k + 1] = {t: full[j].tolist() for j, t in enumerate(tags)}
        out_l.append(float(w[k]))
    return out_l, out_s


def refine(eng, asm, lambdas: List[float],
           shapes: Dict[int, Dict[int, List[float]]],
           n_massed: Optional[int] = None
           ) -> Tuple[List[float], Dict[int, Dict[int, List[float]]]]:
    """Verified, if needed Rayleigh-Ritz-refined, eigenpairs whose
    degenerate clusters are rotated to the canonical basis (:func:`align`).
    Non-degenerate verified solutions come back as the SAME objects.

    A degenerate cluster CUT by the requested mode count (mode ``n`` and
    mode ``n + 1`` repeated — e.g. 8 modes of twin square towers whose
    modes 7-10 coincide) has no solver-independent answer: the solver
    returns an arbitrary part of the eigenspace and every combination
    leaks.  With ``n_massed`` given, ONE extra eigenvalue is probed
    (:func:`complete_cluster`); a cut cluster is then completed, made
    canonical, and only its first members (X, then Y, then RZ
    participation) are kept, so the mode COUNT is unchanged.
    """
    n = len(lambdas)
    full = None
    if n_massed is not None and 0 < n < n_massed:
        full = complete_cluster(eng, asm, lambdas, n_massed)
    if full is not None:
        lambdas, shapes = full
    lambdas, shapes = verify(eng, asm, lambdas, shapes)
    _release(eng)
    shapes = align(asm, lambdas, shapes)
    if full is not None:
        lambdas = list(lambdas[:n])
        shapes = {i: shapes[i] for i in range(1, n + 1)}
    return lambdas, shapes


def _release(eng) -> None:
    """Leave the live domain as run_modal always did: the standard elastic
    build with NO load pattern (callers such as the frequency-domain
    analysis add their own pattern 1 to ``eng._asm``)."""
    from skyframe.engine.opensees_engine import _REUSE
    if _REUSE.get("loaded"):
        eng._elastic_domain()                 # removes pattern 1 + reset


def _same(a: float, b: float) -> bool:
    return abs(b - a) <= CLUSTER_RTOL * max(abs(a), abs(b))


def cluster_end(all_lambdas, n: int) -> int:
    """Mode count covering every cluster the first ``n`` modes touch
    (``n`` itself when mode ``n + 1`` is not a repeat of mode ``n``)."""
    k = n
    while 0 < k < len(all_lambdas) and _same(float(all_lambdas[n - 1]),
                                             float(all_lambdas[k])):
        k += 1
    return k


def complete_cluster(eng, asm, lambdas: List[float], n_massed: int
                     ) -> Optional[Tuple[List[float],
                                         Dict[int, Dict[int, List[float]]]]]:
    """``None`` when mode ``n + 1`` is not a repeat of mode ``n`` (the
    eigen solution is left exactly as computed); else the eigenpairs of a
    re-solve with the whole cut cluster (``n + j`` modes)."""
    from skyframe.engine.opensees_engine import _quiet_native_output
    n = len(lambdas)
    k = n + 1
    while True:
        eng._setup_analysis(asm)
        # an internal probe: Arpack's native failure chatter (the first
        # solve already reported anything that matters) stays silent
        with _quiet_native_output():
            vals = eng._solve_eigen(k, n_massed, asm)
        if not _same(float(lambdas[-1]), float(vals[k - 1])):
            k -= 1                                # vals[k-1] is not in it
            break
        if k >= n_massed:
            break
        k += 1
    if k <= n:
        return None
    # the domain holds the last solve (k or k + 1 modes): use its first k
    shapes = {mode: {t: list(ops.nodeEigenvector(t, mode))
                     for t in asm.node_coords} for mode in range(1, k + 1)}
    return [float(v) for v in vals[:k]], shapes


def verify(eng, asm, lambdas: List[float],
           shapes: Dict[int, Dict[int, List[float]]]
           ) -> Tuple[List[float], Dict[int, Dict[int, List[float]]]]:
    """Verified (and if needed Rayleigh-Ritz-refined) eigenpairs.

    ``asm`` must be the engine's standard elastic build that the eigen
    solve just ran on (``run_modal``'s plain ``_build()``); it is adopted
    as the reusable elastic domain for the verification solves, exactly as
    :mod:`skyframe.engine.modal_condensed` does.
    """
    from skyframe.engine import modal_condensed as _mcd
    from skyframe.engine.opensees_engine import _REUSE, _slow_path
    dofs = _mcd.massed_dofs(asm)
    n = len(lambdas)
    if not dofs or n == 0:
        return lambdas, shapes
    m = np.array([asm.mass_map[k] for k in dofs], dtype=float)
    tags = list(asm.node_coords)
    if eng._asm is asm and not _slow_path():
        _REUSE["owner"] = weakref.ref(eng)
        _REUSE["loaded"] = False
    try:
        resid, orth, ys = check(eng, dofs, m, tags, lambdas, shapes)
    except RuntimeError as exc:                     # pragma: no cover
        _log.debug("eigen refine: verification solve failed (%s)", exc)
        return lambdas, shapes
    eng._eigen_check = {"residual": resid, "orthogonality": orth,
                        "refined": False, "iterations": 0}
    if resid <= TOL and orth <= TOL:
        return lambdas, shapes                      # untouched (identical)
    cur_l, cur_s = lambdas, shapes
    for it in range(1, MAX_ITER + 1):
        new = ritz(dofs, m, tags, cur_l, cur_s, ys)
        if new is None:
            break
        cur_l, cur_s = new
        resid, orth, ys = check(eng, dofs, m, tags, cur_l, cur_s)
        eng._eigen_check = {"residual": resid, "orthogonality": orth,
                            "refined": True, "iterations": it}
        if resid <= TOL and orth <= TOL:
            break
    if cur_l is lambdas:
        return lambdas, shapes
    _log.debug("eigen refine: %s", eng._eigen_check)
    return cur_l, cur_s


# ------------------------------------------------- degenerate clusters
def clusters(lambdas) -> List[List[int]]:
    """0-based index runs of (numerically) repeated eigenvalues, size >= 2."""
    out: List[List[int]] = []
    run = [0] if len(lambdas) else []
    for i in range(1, len(lambdas)):
        a, b = float(lambdas[i - 1]), float(lambdas[i])
        if _same(a, b):
            run.append(i)
        else:
            if len(run) > 1:
                out.append(run)
            run = [i]
    if len(run) > 1:
        out.append(run)
    return out


def align_basis(Phi: np.ndarray, m: np.ndarray, dofs) -> np.ndarray:
    """``T`` (k x k) such that ``Phi @ T`` is the CANONICAL M-orthonormal
    basis of the degenerate eigenspace spanned by the columns of ``Phi``
    (massed components): first the mode carrying ALL of the cluster's X
    participation, then the one carrying the rest of its Y participation,
    then RZ, then the complement (zero participation in every direction).

    Any basis of a repeated eigenvalue is a valid answer of every eigen
    solver; this one is deterministic (solver-independent) and makes the
    per-mode participation of a doubly symmetric building pure X / pure Y,
    so even the (basis-dependent) SRSS / ABS combinations do not leak.
    CQC is rotation-invariant inside the cluster (``rho = 1``).
    """
    k = Phi.shape[1]
    G = Phi.T @ (m[:, None] * Phi)
    G = 0.5 * (G + G.T)
    L = np.linalg.cholesky(G)
    W = np.linalg.inv(L).T                    # Phi @ W is M-orthonormal
    B = Phi @ W
    picked: List[np.ndarray] = []
    for dof, _key in _DIRS:
        sel = np.array([1.0 if d == dof else 0.0 for (_t, d) in dofs])
        tot = float(np.sum(m * sel))
        if tot <= 0.0:
            continue
        v = B.T @ (m * sel)                   # participation in the cluster
        for e in picked:
            v = v - (e @ v) * e
        nv = float(np.linalg.norm(v))
        if nv * nv <= 1e-12 * tot:            # no (remaining) participation
            continue
        picked.append(v / nv)
    # complete with the orthonormal complement (deterministic: QR of
    # [picked | I])
    base = np.column_stack(picked + [np.eye(k)]) if picked else np.eye(k)
    Q, _ = np.linalg.qr(base)
    Q = Q[:, :k]
    for j, e in enumerate(picked):            # QR may flip signs
        if Q[:, j] @ e < 0.0:
            Q[:, j] = -Q[:, j]
    T = W @ Q
    vec = Phi @ T
    for j in range(k):                        # largest massed comp > 0
        col = vec[:, j]
        if col[int(np.argmax(np.abs(col)))] < 0.0:
            T[:, j] = -T[:, j]
    return T


def align(asm, lambdas: List[float],
          shapes: Dict[int, Dict[int, List[float]]]
          ) -> Dict[int, Dict[int, List[float]]]:
    """Shapes with every degenerate cluster rotated to its canonical basis
    (:func:`align_basis`); the SAME object when there is no cluster
    (non-degenerate models stay byte-identical)."""
    from skyframe.engine import modal_condensed as _mcd
    groups = clusters(lambdas)
    if not groups:
        return shapes
    dofs = _mcd.massed_dofs(asm)
    if not dofs:
        return shapes
    m = np.array([asm.mass_map[k] for k in dofs], dtype=float)
    tags = list(asm.node_coords)
    out = dict(shapes)
    for g in groups:
        modes = [i + 1 for i in g]
        Phi = np.column_stack([_massed(shapes[i], dofs) for i in modes])
        try:
            T = align_basis(Phi, m, dofs)
        except np.linalg.LinAlgError:         # pragma: no cover
            continue
        F = np.array([[shapes[i][t] for t in tags] for i in modes],
                     dtype=float)             # (mode, node, dof)
        for j, mode in enumerate(modes):
            full = np.tensordot(T[:, j], F, axes=1)
            out[mode] = {t: full[r].tolist() for r, t in enumerate(tags)}
    return out


def canonical_massed(lambdas, Phi: np.ndarray, m: np.ndarray, dofs
                     ) -> Optional[Tuple[np.ndarray, set]]:
    """``(mode matrix, rotated column indices)``: the massed-DOF modes with
    every degenerate cluster in canonical form (for
    :mod:`skyframe.engine.modal_condensed`, before its full-shape solves),
    or ``None`` when there is no cluster."""
    groups = clusters(lambdas)
    if not groups:
        return None
    out = Phi.copy()
    done: set = set()
    for g in groups:
        try:
            out[:, g] = Phi[:, g] @ align_basis(Phi[:, g], m, dofs)
        except np.linalg.LinAlgError:         # pragma: no cover
            continue
        done.update(g)
    return out, done
