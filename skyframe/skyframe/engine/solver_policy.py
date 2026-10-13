"""Linear-system (solver + numberer) choice per problem size.

CONTRACT "Analysis performance, solver policy and convergence strategy".

The engine historically used ``numberer RCM`` + ``system BandGeneral`` for
every static / transient solve.  On rigid-diaphragm buildings the
Transformation constraint handler couples every slaved node of a floor to
its master, so the band of the reduced stiffness is essentially the whole
floor: BandGeneral cost grows like ``n * b^2`` and on real building sizes it
is the dominant cost of an analysis (a 10-story 5x5-bay frame spends ~0.3 s
per solve; a 4-story meshed-slab model ~3 s; a 20-story meshed model
exhausts memory and crashes the interpreter).

Policy (``model.analysis_options["solver"]``):

* ``"auto"`` (default) — BandGeneral (the exact legacy path, bit-identical)
  while the ESTIMATED equation count is below :data:`SPARSE_EQ_THRESHOLD`;
  at or above it the sparse direct solver UmfPack (same RCM numberer).
  The threshold sits at the measured BandGeneral/UmfPack crossover, so
  every small model (all hand-checked validation problems) keeps its
  byte-identical legacy results, and the large models where the band solver
  is the wrong tool get a solver whose results agree with the band solution
  to solver round-off (measured 1e-15 .. 1e-13 relative).
* ``"band"`` — always the legacy BandGeneral (A/B reference path).
* ``"umfpack"`` / ``"mumps"`` / ``"sparse_general"`` — force that solver.
* ``"pardiso"`` (Wave E2) — Intel MKL PARDISO for every linear flow of the
  standard elastic build (:mod:`skyframe.engine.pardiso_solver`); anything
  it cannot represent exactly falls back to the OpenSees system below
  (UmfPack / MUMPS by size) with the reason in results ``extra.solver``.
  ``"auto"`` uses it at/above ``pardiso_solver.PARDISO_EQ_THRESHOLD`` (10 000)
  estimated equations when ``pypardiso`` imports (else today's path).

Only linear-system choice changes; the eigen solver (``ops.eigen``) keeps its
own legacy policy.  ``SparseSYM`` is deliberately NOT offered: on
Transformation-constrained models it returned wrong displacements (probed on
openseespy 3.7.1, relative error 1.0).

Factor-once reuse: for the linear elastic multi-RHS flows (linear static
cases, response-spectrum modal statics, center-of-rigidity unit loads) the
engine keeps ONE ``algorithm Linear -factorOnce`` analysis alive on the
reused elastic domain when the chosen system supports it (UmfPack): the
stiffness is factored once and every further load case is a
forward/back substitution.  Verified bit-identical to a fresh UmfPack
factorization per case.  (BandGeneral's LAPACK band solver does NOT support
``-factorOnce`` — the second solve fails — so the band path never uses it.)
"""

from __future__ import annotations

from typing import Optional, Tuple

import openseespy.opensees as ops

# estimated equations at/above which "auto" leaves BandGeneral (measured
# crossover on rigid-diaphragm frame / meshed models: ~250 equations)
SPARSE_EQ_THRESHOLD = 300
# v1.24 large models: estimated equations at/above which "auto" uses MUMPS.
# This OpenSees build's UmfPack refactors on EVERY analyze even under
# ``Linear -factorOnce`` (0.9 s per solve at 27 000 equations, 6.9 s at
# 75 000), while MUMPS keeps its LU factors (0.09 s / 0.3 s per solve) —
# measured in docs/PERFORMANCE.md "Large models"; results agree with
# UmfPack to <= 3e-13 on the benchmark buildings (<= 1e-9 is pinned).
# Below the threshold "auto" is unchanged (byte-identical).
MUMPS_EQ_THRESHOLD = 20000

SOLVERS = ("auto", "band", "umfpack", "mumps", "sparse_general",
           "pardiso")                                  # E2: + pardiso

_SYSTEM = {"band": "BandGeneral", "umfpack": "UmfPack", "mumps": "Mumps",
           "sparse_general": "SparseGeneral"}

# systems whose solver supports ``algorithm Linear -factorOnce``
# (v1.24: + Mumps, whose factors really are reused)
FACTOR_ONCE_SYSTEMS = ("UmfPack", "Mumps")


def estimate_equations(asm) -> int:
    """Active-equation estimate of an assembly: free node dofs minus the 3
    (ux, uy, rz) each rigid-diaphragm slave gives up (the same estimate the
    eigen solver policy uses)."""
    n = sum(6 - sum(asm.node_restraints.get(t, (0,) * 6))
            for t in asm.node_coords)
    n -= 3 * sum(len(asm.story_nodes.get(s, ())) for s in asm.masters)
    return max(int(n), 0)


def solver_name(model) -> str:
    """The requested solver policy name (validated elsewhere)."""
    opts = getattr(model, "analysis_options", None) or {}
    name = opts.get("solver", "auto") if isinstance(opts, dict) else "auto"
    return name if name in SOLVERS else "auto"


def choose(model, asm) -> Tuple[str, str]:
    """``(system, note)`` for an assembly — ``system`` is the OpenSees
    ``system`` command name; ``note`` is a warning ("" normally) when a
    forced band solver was replaced to avoid a memory blow-up (see
    :func:`skyframe.engine.model_size.band_fallback`).

    E2: called once at the end of every ``_build``; it also ends the
    PARDISO build recording and, when the policy picks PARDISO, attaches
    the factorable system to ``asm`` (``pardiso_solver.adopt``)."""
    out = _choose(model, asm)
    from skyframe.engine import pardiso_solver as _pds
    _pds.adopt(model, asm, estimate_equations(asm))
    return out


def _choose(model, asm) -> Tuple[str, str]:
    name = solver_name(model)
    if name == "pardiso":           # E2: the OpenSees fallback system
        neq = estimate_equations(asm)
        if neq >= MUMPS_EQ_THRESHOLD and mumps_available():
            return "Mumps", ""
        return "UmfPack", ""
    if name == "auto":
        neq = estimate_equations(asm)
        if neq >= MUMPS_EQ_THRESHOLD and mumps_available():   # v1.24
            return "Mumps", ""
        return ("BandGeneral" if neq < SPARSE_EQ_THRESHOLD
                else "UmfPack"), ""
    if name == "band":
        from skyframe.engine.model_size import band_fallback
        note = band_fallback(asm)
        if note:
            return "UmfPack", note
    return _SYSTEM[name], ""


_MUMPS_OK = {"ok": True}


def mumps_available() -> bool:
    """False once ``system Mumps`` failed in this process (a build without
    MUMPS): :func:`apply_system` then records it and "auto" keeps UmfPack."""
    return bool(_MUMPS_OK["ok"])


def threads_info() -> dict:
    """MUMPS threading of this build (docs/PERFORMANCE.md "Large models"):
    the OpenSees binary links libgomp, but a 75 000-equation factorization
    measured 7.6 s with OMP_NUM_THREADS=1 and 7.1 s with 4 threads (the
    bundled reference BLAS is single-threaded), so no multithreaded path
    is enabled; the run inherits the process' OMP_NUM_THREADS."""
    import os
    from skyframe.engine import pardiso_solver as _pds   # E2
    return {"mumps": mumps_available(),
            "omp_num_threads": os.environ.get("OMP_NUM_THREADS"),
            "multithreaded": False,
            "pardiso": _pds.available(),
            "pardiso_threads": _pds.physical_cores()}


def system_of(asm) -> str:
    """The linear system recorded on an assembly (legacy BandGeneral when
    the assembly was built without a policy — e.g. a hand-made one)."""
    return getattr(asm, "linear_system", None) or "BandGeneral"


def apply_system(asm) -> None:
    """Issue ``numberer`` + ``system`` for an assembly (RCM always — the
    legacy numberer; it is also the best one measured for UmfPack here).
    A system this OpenSees build lacks (e.g. Mumps) degrades to UmfPack
    (recorded on the assembly so later setups skip the failed attempt)."""
    ops.numberer("RCM")
    try:
        ops.system(system_of(asm))
    except Exception:                                   # pragma: no cover
        if system_of(asm) == "Mumps":
            _MUMPS_OK["ok"] = False
        asm.linear_system = "UmfPack"
        ops.system("UmfPack")


def factor_once_ok(asm) -> bool:
    """True when the assembly's system supports ``Linear -factorOnce``.

    E2: unchanged while PARDISO owns the assembly's solves (the OpenSees
    fallback system keeps its value), so the fork-parallel solve families
    of engine/parallel.py still run: a forked worker inherits the PARDISO
    factor copy-on-write and solves with one MKL thread (measured exact
    and 1.6x faster than serial on the 10-story meshed benchmark)."""
    return system_of(asm) in FACTOR_ONCE_SYSTEMS


def pardiso_active(asm) -> bool:
    """E2: True when PARDISO owns the linear solves of this assembly.
    Coordination hook for worker pools (engine/parallel.py): a FORKED
    worker can use the inherited factor; a SPAWNED worker (Windows) has
    no factor and must rebuild the engine itself (its first solve then
    builds its own PARDISO system) or solve in the parent."""
    S = getattr(asm, "_pds", None) if asm is not None else None
    return bool(S is not None and S.ok)


def describe(asm) -> Optional[dict]:
    """Small JSON-safe summary for diagnostics."""
    if asm is None:
        return None
    return {"system": system_of(asm),
            "equations_estimate": estimate_equations(asm)}


def analyze_reused(eng, asm) -> int:
    """``ops.analyze(1)`` on the reused factor-once analysis, with ONE
    fresh-factorization retry for MUMPS (v1.24).

    Measured on openseespy 3.7.1: after a first factor-once MUMPS solve, a
    case whose pattern carries ELEMENT loads (beam span loads, shell area
    loads) fails in MUMPS' substitution (error -3, "wrong JOB state")
    while nodal-load cases reuse the factors fine.  The failed step left
    the domain at load factor 1: ``ops.reset()`` reverts it, the analysis
    is set up again (a fresh factorization of the same stiffness — the
    result equals a refactored solve) and the step is re-run.  UmfPack /
    BandGeneral paths are untouched (byte-identical)."""
    if system_of(asm) != "Mumps" or pardiso_active(asm):   # E2: PARDISO
        return ops.analyze(1)                  # step after prepare_solve
    from skyframe.engine.opensees_engine import _quiet_native_output
    with _quiet_native_output():            # the expected MUMPS chatter
        ok = ops.analyze(1)
    if ok != 0:
        from skyframe.engine.opensees_engine import _REUSE
        ops.reset()
        _REUSE["factored"] = None
        eng._setup_linear_reused(asm)
        ok = ops.analyze(1)
    return ok
