"""Repeated linear solves on the engine's elastic domain with ONE real
factorization (MUMPS factor reuse) — the kernel of the sparse eigen
solvers (buckling, modal Lanczos, sparse stability check).

CONTRACT "Buckling with shells and links, sparse eigen and BucklingFEM".

Measured on openseespy 3.7.1 (docs/PERFORMANCE.md "Large models"):
``algorithm Linear -factorOnce`` keeps MUMPS' LU factors and every
further solve is a forward/back substitution plus OpenSees' element loop
(0.09 s vs 0.9 s per solve with UmfPack on a 27 000-equation meshed
building; 0.3 s vs 6.9 s at 75 000 equations) — this build's UmfPack
refactors on every ``analyze`` even with ``-factorOnce``.  MUMPS and
UmfPack displacements agree to 6e-14 .. 3e-13 (relative) on the benchmark
buildings, with and without Transformation constraints.

Vectors live in the PHYSICAL dof space of :class:`geom_stiffness.Space`
(every node tag x 6).  ``EngineSolver(f)`` applies ``f`` as nodal loads
on the reused elastic domain (pattern 1, the engine's convention) and
returns every node's displacement.  The engine's own factor-once token is
cleared, so the engine re-creates its analysis on its next solve; the
results of every other analysis are unaffected (each sets up its own
analysis).  When this OpenSees build lacks MUMPS the engine's own system
(policy) is used.
"""

from __future__ import annotations

import numpy as np
import openseespy.opensees as ops

from skyframe.engine import geom_stiffness as _gk

PREFERRED_SYSTEM = "Mumps"


class EngineSolver:
    def __init__(self, eng, space: _gk.Space, system: str = PREFERRED_SYSTEM):
        self.eng = eng
        self.space = space
        self.system = system
        self.count = 0
        self._asm = None

    def _setup(self, asm) -> None:
        from skyframe.engine.opensees_engine import _REUSE
        from skyframe.engine import solver_policy as _slv
        _REUSE["factored"] = None
        ops.wipeAnalysis()
        ops.constraints("Transformation" if asm.use_transformation
                        else "Plain")
        ops.numberer("RCM")
        try:
            ops.system(self.system)
        except Exception:                               # pragma: no cover
            self.system = _slv.system_of(asm)
            ops.system(self.system)
        ops.algorithm("Linear", "-factorOnce")
        ops.integrator("LoadControl", 1.0)
        ops.analysis("Static")
        self._asm = asm

    def __call__(self, f: np.ndarray) -> np.ndarray:
        from skyframe.engine.opensees_engine import _REUSE
        eng = self.eng
        asm = eng._elastic_domain()
        eng._seg_span_loads = {}
        eng._seg_fef = {}
        ops.timeSeries("Linear", 1)
        ops.pattern("Plain", 1, 1)
        _REUSE["loaded"] = True
        F = f.reshape(-1, 6)
        tags = self.space.tags
        for i in np.nonzero(np.any(F != 0.0, axis=1))[0]:
            ops.load(tags[i], *[float(v) for v in F[i]])
        if self._asm is not asm:
            self._setup(asm)
        if ops.analyze(1) != 0:
            raise RuntimeError("linear solve failed (is the structure "
                               "stable?)")
        self.count += 1
        return read_disp(self.space)


class StandaloneSolver:
    """``K^-1 f`` on a domain built OUTSIDE the engine (BucklingFEM, the
    sparse stability check): one factor-once MUMPS analysis set up on the
    first call, then remove / reset / re-load per solve (the engine's
    reuse pattern).  The caller owns the domain (no engine reuse token)."""

    def __init__(self, space: _gk.Space, transformation: bool,
                 system: str = PREFERRED_SYSTEM):
        self.space = space
        self.tf = transformation
        self.system = system
        self.count = 0
        self.live = False

    def __call__(self, f: np.ndarray) -> np.ndarray:
        if self.live:
            ops.remove("loadPattern", 1)
            ops.remove("timeSeries", 1)
            ops.reset()
        ops.timeSeries("Linear", 1)
        ops.pattern("Plain", 1, 1)
        Fm = f.reshape(-1, 6)
        tags = self.space.tags
        for i in np.nonzero(np.any(Fm != 0.0, axis=1))[0]:
            ops.load(tags[i], *[float(v) for v in Fm[i]])
        if not self.live:
            ops.wipeAnalysis()
            ops.constraints("Transformation" if self.tf else "Plain")
            ops.numberer("RCM")
            try:
                ops.system(self.system)
            except Exception:                           # pragma: no cover
                self.system = "UmfPack"
                ops.system(self.system)
            ops.algorithm("Linear", "-factorOnce")
            ops.integrator("LoadControl", 1.0)
            ops.analysis("Static")
            self.live = True
        if ops.analyze(1) != 0:
            raise RuntimeError("linear solve failed")
        self.count += 1
        return read_disp(self.space)


def read_disp(space: _gk.Space) -> np.ndarray:
    out = np.empty(space.n)
    for i, t in enumerate(space.tags):
        out[6 * i:6 * i + 6] = ops.nodeDisp(t)[:6]
    return out


def free_mask(asm, space: _gk.Space) -> np.ndarray:
    free = np.ones(space.n, dtype=bool)
    for t, r in asm.node_restraints.items():
        i = space.idx.get(t)
        if i is None:
            continue
        for d in range(6):
            if r[d]:
                free[6 * i + d] = False
    return free
