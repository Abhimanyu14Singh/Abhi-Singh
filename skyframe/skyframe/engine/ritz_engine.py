"""Load-dependent Ritz vectors on the ENGINE's own elastic assembly.

CONTRACT "Multiple mass sources, modal cases, Ritz basis".

The legacy :mod:`skyframe.core.ritz` assembles a frame-only numpy
stiffness (shells / links skipped).  This module runs the same WYD
sequence on the engine's elastic OpenSees domain, so EVERY element the
engine builds (frames, shells, elastic links, springs, rigid diaphragms,
named diaphragms, panel zones ...) is in the stiffness, and the result is
a :class:`~skyframe.engine.opensees_engine.ModalResults` keyed by engine
node tags — directly usable by response spectrum, modal damping and FNA.

Algorithm (one static solve per vector on the reused, factor-once
elastic domain — :func:`skyframe.engine.modal_condensed._solve_loads`):

* starting load block ``F`` (one column per start):
  ``accel`` -> ``M r_dir`` on the massed DOFs (lumped mass map);
  ``pattern`` -> the pattern's CONSISTENT nodal load vector (nodal loads
  + element-load equivalents: the pattern applied at ``u = 0`` and
  ``P = -nodeReaction`` read, the same rule as
  :mod:`skyframe.engine.thmulti`); ``links`` -> per nonlinear link DOF a
  self-equilibrated unit force pair (axial devices: along the axis;
  bearings: global X and global Y shear) — the ETABS link starting
  vectors; a load on a restrained / removed node is dropped;
* ``x = K^-1 b`` (static solve), then ``x_{i+1} = K^-1 M u_i`` per chain,
  chains alternating; every candidate two-pass M-Gram-Schmidt
  orthogonalized against all previous vectors and M-normalized; a
  candidate whose M-norm falls below ``1e-16`` of its pre-orthogonalization
  value (or that carries no mass) ends its chain; ``max_cycles > 0`` caps
  the vectors per chain;
* the LOAD representation ``f_u`` of every vector (``K u = f_u`` exactly:
  the same combination of applied loads) is carried along, so the
  reduced stiffness is ``K_r = U^T F_u`` — a plain nodal dot product, no
  element stiffness needed (``x_i . b_j`` is symmetric by Betti through
  the constraint transformation);
* ``K_r z = theta z`` (``M_r = I``), Ritz vectors ``phi = U z`` ascending,
  M-orthonormal (the OpenSees eigenvector normalisation), sign fixed so
  the largest massed component is positive.

Exact static property (pinned): a single vector started from a load
pattern reproduces that pattern's static displacement EXACTLY by modal
superposition (``phi phi^T P / omega^2 = K^-1 P``).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import openseespy.opensees as ops

_TMP_PAT = 9097                      # temporary unit pattern / series tag
_DOFS = {"UX": 1, "UY": 2, "UZ": 3, "RX": 4, "RY": 5, "RZ": 6}
_AXIAL_LINKS = ("damper", "gap", "hook",
                "brb", "damper_bilinear")   # + wave B axial link types


class _Space:
    """Dense (n_nodes x 6) vectors over the engine's node tags."""

    def __init__(self, asm):
        self.tags = list(asm.node_coords)
        self.idx = {t: i for i, t in enumerate(self.tags)}
        none = (0,) * 6
        self.dofs = [(t, d) for (t, d) in sorted(asm.mass_map)
                     if not asm.node_restraints.get(t, none)[d - 1]]
        self.rows = np.array([self.idx[t] for t, _ in self.dofs], dtype=int)
        self.cols = np.array([d - 1 for _, d in self.dofs], dtype=int)
        self.m = np.array([asm.mass_map[k] for k in self.dofs], dtype=float)

    def zeros(self) -> np.ndarray:
        return np.zeros((len(self.tags), 6))

    def mdot(self, x: np.ndarray, y: np.ndarray) -> float:
        return float(np.sum(self.m * x[self.rows, self.cols]
                            * y[self.rows, self.cols]))

    def mvec(self, x: np.ndarray) -> np.ndarray:
        out = self.zeros()
        out[self.rows, self.cols] = self.m * x[self.rows, self.cols]
        return out

    def loads(self, b: np.ndarray) -> List[Tuple[Tuple[int, int], float]]:
        nz = np.argwhere(b != 0.0)
        return [((self.tags[i], int(d) + 1), float(b[i, d])) for i, d in nz]


def _pattern_load(eng, asm, sp: _Space, name: str) -> np.ndarray:
    """Consistent nodal load vector of a pattern (u = 0 reaction trick)."""
    asm = eng._elastic_domain()
    eng._seg_span_loads = {}
    eng._seg_fef = {}
    ops.timeSeries("Constant", _TMP_PAT)
    ops.pattern("Plain", _TMP_PAT, _TMP_PAT)
    eng._apply_pattern(asm, name, 1.0)
    ops.reactions()
    b = sp.zeros()
    for t in sp.tags:
        r = ops.nodeReaction(t)
        b[sp.idx[t], :len(r[:6])] = [-float(v) for v in r[:6]]
    ops.remove("loadPattern", _TMP_PAT)
    ops.remove("timeSeries", _TMP_PAT)
    ops.reactions()                       # re-zero the reaction storage
    eng._seg_span_loads = {}
    eng._seg_fef = {}
    return b


def _link_pairs(eng, asm, sp: _Space, link_points) -> List[np.ndarray]:
    """Unit force pairs for the nonlinear link DOFs.

    ``link_points``: ``[(kind, pi, pj), ...]`` (kind "axial" | "shear");
    None = every non-elastic link of the engine's model."""
    if link_points is None:
        link_points = []
        for lk in eng.model.links:
            lt = getattr(lk, "link_type", "elastic")
            if lt == "elastic":
                continue
            link_points.append(("axial" if lt in _AXIAL_LINKS else "shear",
                                tuple(lk.pi), tuple(lk.pj)))
    out: List[np.ndarray] = []
    for kind, pi, pj in link_points:
        def node(p):
            try:
                return eng._find_node(asm, p)
            except ValueError:
                return None
        ti, tj = node(pi), node(pj)
        if kind == "axial":
            e = np.array(pj, dtype=float) - np.array(pi, dtype=float)
            L = float(np.linalg.norm(e))
            if L <= 0.0:
                continue
            dirs = [e / L]
        else:
            dirs = [np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])]
        for e in dirs:
            b = sp.zeros()
            if tj is not None:
                b[sp.idx[tj], :3] += e
            if ti is not None:
                b[sp.idx[ti], :3] -= e
            if np.any(b):
                out.append(b)
    return out


def ritz_basis(eng, n: int, starts: List[dict], max_cycles: int = 0,
               link_points=None) -> Tuple[np.ndarray, List[np.ndarray],
                                          _Space, List[str]]:
    """``(theta, phis, space, warnings)`` — Ritz values (ascending) and
    M-orthonormal Ritz vectors (dense arrays over ``space.tags``)."""
    asm = eng._elastic_domain()
    sp = _Space(asm)
    warn: List[str] = []
    if not sp.dofs:
        return np.zeros(0), [], sp, ["no mass on unrestrained DOFs"]
    from skyframe.engine import modal_condensed as _mcd

    # ---- starting load block ------------------------------------------
    block: List[Tuple[str, np.ndarray]] = []
    for s in starts:
        typ = s.get("type")
        if typ == "accel":
            d = _DOFS[s["dir"]]
            r = sp.zeros()
            sel = sp.cols == d - 1
            r[sp.rows[sel], d - 1] = 1.0
            b = sp.mvec(r)
            if not np.any(b):
                warn.append(f"Ritz start accel {s['dir']}: no mass in that "
                            "direction; skipped")
                continue
            block.append((f"accel {s['dir']}", b))
        elif typ == "pattern":
            b = _pattern_load(eng, asm, sp, s["name"])
            if not np.any(b):
                warn.append(f"Ritz start pattern {s['name']!r}: no load; "
                            "skipped")
                continue
            block.append((f"pattern {s['name']}", b))
        elif typ == "links":
            pairs = _link_pairs(eng, asm, sp, link_points)
            if not pairs:
                warn.append("Ritz start links: the model has no nonlinear "
                            "link DOFs; skipped")
            block.extend(("link", b) for b in pairs)

    def solve(b: np.ndarray) -> np.ndarray:
        _mcd._solve_loads(eng, sp.loads(b))
        x = sp.zeros()
        for t in sp.tags:
            v = ops.nodeDisp(t)
            x[sp.idx[t], :len(v[:6])] = v[:6]
        return x

    U: List[np.ndarray] = []
    FU: List[np.ndarray] = []
    nb = len(block)
    chain: List[Optional[np.ndarray]] = [None] * nb
    alive = [True] * nb
    count = [0] * nb
    rnd = 0
    while len(U) < n and any(alive):
        for j in range(nb):
            if len(U) >= n or not alive[j]:
                continue
            if max_cycles and count[j] >= max_cycles:
                alive[j] = False
                continue
            b = block[j][1] if rnd == 0 else sp.mvec(chain[j])
            x = solve(b)
            f = b.copy()
            n0 = sp.mdot(x, x)
            if n0 <= 0.0:
                alive[j] = False
                if rnd == 0:
                    warn.append(f"Ritz start {block[j][0]}: the static "
                                "response carries no mass; skipped")
                continue
            for _ in range(2):
                for u, fu in zip(U, FU):
                    c = sp.mdot(u, x)
                    x = x - c * u
                    f = f - c * fu
            n1 = sp.mdot(x, x)
            if n1 <= 1e-16 * n0:
                alive[j] = False
                continue
            s_ = 1.0 / math.sqrt(n1)
            U.append(x * s_)
            FU.append(f * s_)
            chain[j] = U[-1]
            count[j] += 1
        rnd += 1
    if not U:
        return np.zeros(0), [], sp, warn + ["no Ritz vector could be "
                                            "generated"]
    k = len(U)
    Kr = np.array([[float(np.sum(U[i] * FU[j])) for j in range(k)]
                   for i in range(k)])
    Kr = 0.5 * (Kr + Kr.T)
    theta, Z = np.linalg.eigh(Kr)
    phis = []
    for c in range(k):
        phi = sum(Z[i, c] * U[i] for i in range(k))
        mv = phi[sp.rows, sp.cols]
        i_big = int(np.argmax(np.abs(mv)))
        if mv[i_big] < 0.0:
            phi = -phi
        phis.append(phi)
    if k < n:
        warn.append(f"only {k} Ritz vector(s) available (requested {n}): "
                    "the load-reachable subspace is exhausted")
    if np.any(theta <= 0.0):
        raise RuntimeError("Ritz analysis: non-positive Ritz value (the "
                           "structure is unstable)")
    return theta, phis, sp, warn


def run_modal(eng, num_modes: Optional[int], spec: dict):
    """ModalResults of a Ritz modal case (cached per vector count)."""
    from skyframe.engine.opensees_engine import ModalResults
    n = int(num_modes or eng.model.num_modes)
    cache = eng.__dict__.setdefault("_ritz_cache", {})
    if n in cache:
        return cache[n]
    theta, phis, sp, warn = ritz_basis(
        eng, n, spec.get("ritz_starts") or [],
        int(spec.get("max_cycles") or 0), spec.get("_link_points"))
    if not phis:
        res = ModalResults([], [], [], {})
        eng._ritz_warnings = warn
        cache[n] = res
        return res
    periods = [2.0 * math.pi / math.sqrt(float(t)) for t in theta]
    freqs = [1.0 / T for T in periods]
    shapes = {i + 1: {t: [float(v) for v in phi[sp.idx[t]]]
                      for t in sp.tags}
              for i, phi in enumerate(phis)}
    part = eng._participation(eng._asm, periods, shapes)
    res = ModalResults(periods, freqs, part, shapes)
    eng._ritz_warnings = warn
    cache[n] = res
    return res
