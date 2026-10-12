"""Exact modal analysis by static condensation onto the massed DOFs.

CONTRACT "Analysis performance, solver policy and convergence strategy".

The engine's mass matrix is purely LUMPED (``ops.mass`` on nodes; frame
and shell elements carry no element mass), so the generalized eigenproblem
``K u = lambda M u`` has a mass matrix that is nonzero only on the massed
free DOFs ``m``.  Writing ``G = T (T^T K T)^-1 T^T`` for the constrained
flexibility (the displacement field under a unit physical load — exactly
what one linear static solve returns, constraints included) every mode
satisfies ``u = lambda G M u``; restricted to the massed DOFs

    u_m = lambda G_mm M_m u_m        (no approximation — Guyan reduction is
                                      exact when M lives on the kept DOFs)

so with ``A = M_m^1/2 G_mm M_m^1/2`` (symmetric) the eigenpairs are
``lambda_k = 1 / mu_k`` and ``u_m = M_m^-1/2 y_k`` for the LARGEST
eigenpairs ``(mu_k, y_k)`` of ``A`` (dense ``numpy.linalg.eigh``; ``A`` is
``n_massed x n_massed`` — 3 x stories for rigid-diaphragm buildings).
``G_mm`` costs one linear solve per massed DOF and each full mode shape one
more solve (load ``lambda_k M u_m``), all on the reused elastic domain with
the factor-once sparse analysis (one factorization for everything).

Why: OpenSees' ``eigen`` (shift-invert Arpack) on a meshed rigid-diaphragm
building is the dominant cost of a run — its Arnoldi space is capped at
``rank(M)`` (few massed DOFs) and the shift-invert solves go through the
band/profile storage: a 10-story meshed-slab model did not finish in 11 min
and 4.5 GB, the 20-story one crashes.  The condensed solve takes seconds.

Scope: used by :meth:`OpenSeesEngine.run_modal` only when the solver
policy picked a sparse system (large models — small models keep the exact
legacy Arpack path, byte-identical) and ``n_massed <=``
:data:`MAX_MASSED`; otherwise (or when it cannot deliver ``n`` modes) the
legacy eigen path runs.  Eigenvalues agree with Arpack to solver
round-off.  Mode shapes are MASS-NORMALIZED (``phi^T M phi = 1``, the
OpenSees convention) with the sign fixed so the largest-magnitude massed
component is positive (eigenvector signs are arbitrary in every solver;
``Gamma * phi`` and all RS / participation results are sign-invariant).
For exactly repeated eigenvalues (doubly symmetric buildings) any basis of
the degenerate subspace is a valid answer — Arpack's own choice depends on
its random start vector — so individual shapes of such pairs are not
comparable between solvers (their span and every combined result is).
"""

from __future__ import annotations

import weakref
from typing import Dict, List, Optional, Tuple

import numpy as np
import openseespy.opensees as ops

# at most this many massed DOFs (one linear solve each) take the
# condensed path; beyond, the legacy eigen solver runs
MAX_MASSED = 600
# relative floor below which an eigenvalue of A is a rank-deficiency
# artefact (massed DOFs tied together by constraints), not a mode
_MU_FLOOR = 1e-12


def enabled(model) -> bool:
    """``analysis_options.eigen_solver``: "auto" (default) allows the
    condensed path; "legacy" forces OpenSees' eigen."""
    opts = getattr(model, "analysis_options", None) or {}
    return not (isinstance(opts, dict)
                and opts.get("eigen_solver") == "legacy")


def massed_dofs(asm) -> List[Tuple[int, int]]:
    none = (0,) * 6
    return [(t, d) for (t, d) in sorted(asm.mass_map)
            if not asm.node_restraints.get(t, none)[d - 1]]


def applicable(eng, asm, n: int) -> bool:
    from skyframe.engine.opensees_engine import _slow_path
    if _slow_path() or n <= 0:
        return False
    if ((getattr(asm, "linear_system", "") or "BandGeneral")
            not in ("UmfPack", "Mumps", "SparseGeneral")
            and not getattr(asm, "release_penalty", False)):
        # (end-release / skewed-support penalty ties: a dense eigen solve
        # on the stiff ties loses ~1e-3; condensation uses linear solves)
        return False
    nm = len(massed_dofs(asm))
    return n <= nm <= MAX_MASSED


def _solve_loads(eng, loads) -> None:
    from skyframe.engine.opensees_engine import _REUSE
    asm = eng._elastic_domain()
    eng._seg_span_loads = {}
    eng._seg_fef = {}
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    _REUSE["loaded"] = True
    for (t, d), v in loads:
        vec = [0.0] * 6
        vec[d - 1] = float(v)
        ops.load(t, *vec)
    eng._setup_linear_reused(asm)
    if ops.analyze(1) != 0:
        raise RuntimeError("condensed modal analysis: static solve failed")


def solve(eng, asm, n: int
          ) -> Optional[Tuple[List[float], Dict[int, Dict[int, List[float]]]]]:
    """``(eigenvalues, shapes)`` of the ``n`` lowest modes, or ``None``
    when the condensed problem has fewer than ``n`` finite modes (the
    caller then runs the legacy eigen solver).  ``asm`` must be the
    engine's fresh standard elastic build (it is adopted as the reused
    elastic domain)."""
    from skyframe.engine.opensees_engine import _REUSE
    dofs = massed_dofs(asm)
    nm = len(dofs)
    m = np.array([asm.mass_map[k] for k in dofs], dtype=float)
    # adopt the fresh elastic build as the reusable domain (it IS the
    # standard elastic build: run_modal's plain ``_build()``)
    if eng._asm is asm:
        _REUSE["owner"] = weakref.ref(eng)
        _REUSE["loaded"] = False
    G = np.zeros((nm, nm))
    masters = set(asm.masters.values())
    rz_memo: Dict[Tuple[int, int], float] = {}
    # the unit-load columns are independent solves on the same factored
    # domain: process-parallel on large models (engine/parallel.py; the
    # merge is by column index, byte-identical to the serial loop)
    from skyframe.engine import parallel as _par
    n_lanes = _par.lanes(eng.model, asm)

    def g_column(key):
        _solve_loads(eng, [(key, 1.0)])
        col = [ops.nodeDisp(t, d) for (t, d) in dofs]
        # the CR unit-load solve of _master_rz is THIS solve (same
        # domain, same unit load, same factorization) -> memo
        rz = (float(ops.nodeDisp(key[0])[5]) if key[0] in masters
              else None)
        return col, rz

    warm = _par.factored_live(eng, asm)   # no serial first solve needed
    for j, (key, (col, rz)) in enumerate(
            zip(dofs, _par.map_family(g_column, dofs, n_lanes, warm))):
        G[:, j] = col
        if rz is not None:
            rz_memo[key] = rz
    G = 0.5 * (G + G.T)
    s = np.sqrt(m)
    A = s[:, None] * G * s[None, :]
    mu, Y = np.linalg.eigh(A)
    order = np.argsort(-mu)
    mu, Y = mu[order], Y[:, order]
    mu_max = float(mu[0]) if nm else 0.0
    good = [k for k in range(nm) if mu[k] > _MU_FLOOR * mu_max > 0.0]
    if len(good) < n:
        return None
    lambdas: List[float] = []
    shapes: Dict[int, Dict[int, List[float]]] = {}
    units: Dict[int, dict] = {}
    want_units = bool(eng.model.rs_cases) and _units_linear(asm)
    tags = list(asm.node_coords)
    jobs = []
    # degenerate clusters -> the canonical, solver-independent basis
    # (engine/eigen_refine.align_basis); None = no cluster (unchanged)
    from skyframe.engine import eigen_refine as _eref
    ext = _eref.cluster_end([1.0 / float(mu[k]) for k in good], n)
    canon = _eref.canonical_massed(
        [1.0 / float(mu[k]) for k in good[:ext]],
        np.column_stack([Y[:, k] / s for k in good[:ext]]), m, dofs)
    for mode, k in enumerate(good[:n], start=1):
        lam = 1.0 / float(mu[k])
        phi_m = Y[:, k] / s                       # phi_m^T M phi_m = 1
        i_big = int(np.argmax(np.abs(Y[:, k])))   # deterministic sign
        if Y[i_big, k] < 0.0:
            phi_m = -phi_m
        if canon is not None and mode - 1 in canon[1]:
            phi_m = canon[0][:, mode - 1]
        jobs.append((mode, lam, [(dofs[i], lam * m[i] * phi_m[i])
                                 for i in range(nm) if phi_m[i] != 0.0]))

    def mode_solve(job):
        mode, lam, loads = job
        _solve_loads(eng, loads)
        shape = {t: list(ops.nodeDisp(t)) for t in tags}
        unit = (_unit_response(eng, asm, shape, lam) if want_units
                else None)
        return shape, unit

    # full shapes (+ unit responses): independent solves, parallel too
    for (mode, lam, _), (shape, unit) in zip(
            jobs, _par.map_family(mode_solve, jobs, n_lanes,
                                  _par.factored_live(eng, asm))):
        shapes[mode] = shape
        lambdas.append(lam)
        if want_units:
            units[mode] = unit
    eng._mcd_rz_memo = rz_memo
    return lambdas, shapes, units


def _units_linear(asm) -> bool:
    """Unit modal responses scale exactly only for a LINEAR static model
    (no compression-only / named nonlinear springs, no ground
    displacement bookkeeping)."""
    return not (asm.ent_springs or getattr(asm, "named_springs", None)
                or asm.gd_present)


def _unit_response(eng, asm, node_disp, lam: float) -> dict:
    """Everything ``_modal_static`` reads from a solve, for the live
    unit modal load ``lam M phi`` (whose displacement field IS ``phi``)."""
    ops.reactions()
    reactions = {t: list(ops.nodeReaction(t)) for t in asm.support_tags}
    nd = {t: list(v) for t, v in node_disp.items()}
    eng._add_spring_reactions(asm, nd, reactions)
    member_forces, member_stations, _ = eng._member_outputs(asm)
    out = {"lam": lam, "node_disp": nd, "reactions": reactions,
           "member_forces": member_forces,
           "member_stations": member_stations,
           "shell_forces": eng._shell_outputs(asm)}    # RS shell forces
    from skyframe.engine import cut_results as _cutx
    _cutx.attach_unit(eng, asm, out)                   # Wave C RS cuts
    return out


def _flatten(block: dict):
    """``(values, spec)`` of a dict of float lists (or of dicts of float
    lists — member stations; their ``"x"`` station coordinates are kept
    aside, never scaled)."""
    vals: List[float] = []
    spec = []
    for k, v in block.items():
        if isinstance(v, dict):
            inner = []
            for kk, vv in v.items():
                if kk == "x":
                    inner.append((kk, None, list(vv)))
                else:
                    inner.append((kk, len(vv), None))
                    vals.extend(vv)
            spec.append((k, inner))
        else:
            spec.append((k, len(v)))
            vals.extend(v)
    return np.array(vals, dtype=float), spec


def scaled(unit: dict, key: str, c: float) -> dict:
    """``c x`` a stored unit response block, as fresh lists.  Elementwise
    IEEE multiplication (numpy ``c * a`` == Python ``c * v`` per value);
    the flattened block is cached on the unit record."""
    cache = unit.setdefault("_flat", {})
    if key not in cache:
        cache[key] = _flatten(unit[key])
    arr, spec = cache[key]
    flat = (c * arr).tolist()
    out: dict = {}
    pos = 0
    for entry in spec:
        if isinstance(entry[1], list):
            d = {}
            for kk, n, xs in entry[1]:
                if n is None:
                    d[kk] = list(xs)
                else:
                    d[kk] = flat[pos:pos + n]
                    pos += n
            out[entry[0]] = d
        else:
            n = entry[1]
            out[entry[0]] = flat[pos:pos + n]
            pos += n
    return out


def attach_units(eng, modal, cond) -> None:
    """Remember the unit modal responses of the condensed solve that
    produced ``modal`` (RS reuses them by scaling)."""
    units = cond[2] if len(cond) > 2 else {}
    if units and len(units) == len(modal.periods):
        eng._mcd_units = (weakref.ref(modal), units)


def unit_responses(eng, modal):
    """The unit modal responses for THIS ModalResults object, or None."""
    rec = getattr(eng, "_mcd_units", None)
    if rec is None or rec[0]() is not modal:
        return None
    return rec[1]


def unit_rz(eng, story: str, load_vec):
    """Memoized ``_master_rz`` value for a unit load at a diaphragm
    master (filled by the condensed G solves), or None."""
    memo = getattr(eng, "_mcd_rz_memo", None)
    asm = eng._asm
    if not memo or asm is None or story not in asm.masters:
        return None
    vec = [float(v) for v in load_vec]
    nz = [i for i, v in enumerate(vec) if v != 0.0]
    if len(nz) != 1 or vec[nz[0]] != 1.0:
        return None
    return memo.get((asm.masters[story], nz[0] + 1))
