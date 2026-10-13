"""Sparse shift-invert Lanczos modal analysis (large models).

CONTRACT "Buckling with shells and links, sparse eigen and BucklingFEM".

The exact massed-DOF condensation (:mod:`skyframe.engine.modal_condensed`)
needs one solve per massed DOF, so it is capped at ``MAX_MASSED`` (600)
massed DOFs — rigid-diaphragm buildings.  A model whose mass sits on many
nodes (no / semi-rigid diaphragms, meshed slabs carrying their own mass)
used to fall back to OpenSees' band Arpack eigen solver, which on meshed
models runs for tens of minutes or exhausts memory
(docs/PERFORMANCE.md).  This module solves ``K phi = omega^2 M phi`` by
shift-invert Lanczos (:mod:`skyframe.engine.lanczos`, ``G = M`` the
lumped mass) on ONE factorization of the elastic domain
(:mod:`skyframe.engine.sparse_solve`, MUMPS factor reuse): each Lanczos
step is one forward/back substitution.

When it runs (``analysis_options.eigen_solver``):

* ``"auto"`` (default) — only on models above BOTH thresholds: the solver
  policy picked a sparse system (>= 300 estimated equations) AND the
  model has more than ``MAX_MASSED`` free massed DOFs (so the condensed
  path does not apply).  Every model below keeps its exact legacy path
  (byte-identical);
* ``"lanczos"`` — forced on any model (tests / A-B checks);
* ``"legacy"`` — never.

Results: eigenvalues ascending, shapes MASS-NORMALISED (``phi^T M phi =
1``, the OpenSees convention) with the sign fixed so the largest
mass-weighted component is positive; degenerate (equal-period) clusters
are rotated to the canonical basis of :mod:`skyframe.engine.eigen_refine`
(the condensed path's convention).  Pinned against OpenSees' eigen to
1e-9 (periods, participation, RS results).
"""

from __future__ import annotations

import weakref
from typing import Dict, List, Optional, Tuple

import numpy as np

from skyframe.engine import geom_stiffness as _gk
from skyframe.engine import lanczos as _lz

#: residual tolerance of the modal Lanczos pairs (relative)
TOL = 1e-11


def mode(model) -> str:
    opts = getattr(model, "analysis_options", None) or {}
    v = opts.get("eigen_solver", "auto") if isinstance(opts, dict) else "auto"
    return v if v in ("auto", "legacy", "lanczos") else "auto"


def applicable(eng, asm, n: int) -> bool:
    from skyframe.engine.opensees_engine import _slow_path
    from skyframe.engine import modal_condensed as _mcd
    md = mode(eng.model)
    if md == "legacy" or _slow_path() or n <= 0:
        return False
    nm = len(_mcd.massed_dofs(asm))
    if nm < n:
        return False
    if md == "lanczos":
        return True
    if ((getattr(asm, "linear_system", "") or "BandGeneral")
            not in ("UmfPack", "Mumps", "SparseGeneral")):
        return False
    return nm > _mcd.MAX_MASSED


def solve(eng, asm, n: int
          ) -> Optional[Tuple[List[float], Dict[int, Dict[int, List[float]]],
                              dict]]:
    """``(eigenvalues, shapes, info)`` of the ``n`` lowest modes, or None
    (the caller then runs the legacy eigen solver)."""
    from skyframe.engine.opensees_engine import _REUSE
    from skyframe.engine import modal_condensed as _mcd
    from skyframe.engine import eigen_refine as _eref
    from skyframe.engine import sparse_solve as _sps
    if eng._asm is asm:                      # adopt the fresh elastic build
        _REUSE["owner"] = weakref.ref(eng)
        _REUSE["loaded"] = False
    from skyframe.engine import pardiso_solver as _pds   # E2: eigsh on
    pres = _pds.eigsh_modes(eng, asm, n)                 # the PARDISO factor
    if pres is not None:
        return pres
    space = _gk.Space(asm.node_coords)
    dofs = _mcd.massed_dofs(asm)
    rows = np.array([6 * space.idx[t] + d - 1 for (t, d) in dofs],
                    dtype=np.int64)
    m = np.array([asm.mass_map[k] for k in dofs], dtype=float)
    mvec = np.zeros(space.n)
    mvec[rows] = m
    free = np.zeros(space.n, dtype=bool)
    free[rows] = True
    solver = _sps.EngineSolver(eng, space)

    def gmul(x):
        return mvec * x
    lr = _lz.eigs(solver, gmul, free, n, tol=TOL)
    if len(lr.thetas) < n:
        return None
    lams = [1.0 / t for t in lr.thetas]            # ascending omega^2
    X = []
    s = np.sqrt(m)
    for x in lr.vectors:
        nrm = float(np.sqrt(x @ (mvec * x)))
        x = x / nrm
        y = s * x[rows]
        if y[int(np.argmax(np.abs(y)))] < 0.0:
            x = -x
        X.append(x)
    Phi = np.column_stack([x[rows] for x in X])
    for g in _eref.clusters(lams):
        try:
            R = _eref.align_basis(Phi[:, g], m, dofs)
        except np.linalg.LinAlgError:          # pragma: no cover
            continue
        block = np.column_stack([X[k] for k in g]) @ R
        for j, k in enumerate(g):
            X[k] = block[:, j]
    shapes: Dict[int, Dict[int, List[float]]] = {}
    for k, x in enumerate(X, start=1):
        Xm = x.reshape(-1, 6)
        shapes[k] = {t: [float(v) for v in Xm[i]]
                     for i, t in enumerate(space.tags)}
    info = {"solver": "lanczos", "system": solver.system,
            "solves": int(solver.count), "converged": bool(lr.converged),
            "max_residual": float(max(lr.residuals) if lr.residuals
                                  else 0.0)}
    eng._spm_info = info
    return lams, shapes, info
