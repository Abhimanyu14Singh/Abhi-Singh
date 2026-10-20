"""Pre-solve stability check for an element-level 3D frame model.

Two questions, answered before anything is factored:

1. **Which parts are not connected to the ground?**  Nodes joined through
   elements and rigid diaphragms form groups; a group with no fixed node
   is free to move as a body.  These are mechanisms.

2. **Which node DOFs does no element resist?**  Each node's 3x3
   translation and rotation blocks are summed from its elements' end
   stiffness (local diagonal terms turned to global axes, end releases
   applied), with the DOFs a rigid diaphragm carries taken out.  A
   direction with no stiffness is either

   * a **rotation** at a joint where every member is pinned: the rotation is
     indeterminate but carries no load (the model has no applied moments),
     so the engine holds it with a negligible spring and says so; or
   * a **translation**: a real mechanism (a node only reached by pin-ended
     members along one line, say).  It is reported by node and by the
     objects (``src``) that meet there.

The block test is local: it finds single-node mechanisms and floating
groups exactly, not every multi-node mechanism.  Those show up after the
solve as unbounded movement and are reported there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Set

import numpy as np


@dataclass
class StabilityReport:
    floating_groups: List[List[int]] = field(default_factory=list)
    free_rotations: List[int] = field(default_factory=list)      # held by a negligible spring
    free_translations: Dict[int, List[str]] = field(default_factory=dict)  # node -> ["ux", ...]
    srcs_at: Dict[int, List[str]] = field(default_factory=dict)

    @property
    def mechanism_nodes(self) -> List[int]:
        out: Set[int] = set(self.free_translations)
        for g in self.floating_groups:
            out.update(g)
        return sorted(out)

    def notes(self) -> List[str]:
        n: List[str] = []
        if self.floating_groups:
            k = sum(len(g) for g in self.floating_groups)
            n.append(f"Not connected to the ground: {len(self.floating_groups)} group(s), "
                     f"{k} node(s). They are held in place and carry no results.")
        if self.free_translations:
            n.append(f"Mechanism: {len(self.free_translations)} node(s) can move with no "
                     f"element resisting (for example pin-ended members meeting in a line). "
                     f"They are held in that direction; check the members listed for them.")
        if self.free_rotations:
            n.append(f"{len(self.free_rotations)} joint(s) where every member is pinned: "
                     f"their rotation is indeterminate and carries no load; held by a "
                     f"negligible spring.")
        return n


class _DSU:
    def __init__(self, n: int) -> None:
        self.p = np.arange(n)

    def find(self, a: int) -> int:
        p = self.p
        while p[a] != a:
            p[a] = p[p[a]]
            a = p[a]
        return int(a)

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def element_frames(xyz: np.ndarray, I: np.ndarray, J: np.ndarray, yh: np.ndarray):
    """Local axes (ex, ey, ez) and length per element, Supa's convention:
    local y = the hint made square to x, local z = x × y."""
    d = xyz[J] - xyz[I]
    L = np.maximum(1e-3, np.linalg.norm(d, axis=1))
    ex = d / L[:, None]
    ey = yh - (np.sum(yh * ex, axis=1))[:, None] * ex
    n = np.linalg.norm(ey, axis=1)
    bad = n < 1e-6
    if bad.any():
        alt = np.where((np.abs(ex[:, 2]) < 0.9)[:, None],
                       np.stack([-ex[:, 1], ex[:, 0], np.zeros(len(ex))], axis=1),
                       np.tile([1.0, 0.0, 0.0], (len(ex), 1)))
        ey[bad] = alt[bad]
        n = np.linalg.norm(ey, axis=1)
    ey = ey / n[:, None]
    ez = np.cross(ex, ey)
    return ex, ey, ez, L


def check(xyz_flat: Sequence[float], fixed: Sequence[bool], els: Sequence[dict],
          dia: Sequence[dict]) -> StabilityReport:
    xyz = np.asarray(xyz_flat, dtype=float).reshape(-1, 3)
    nn = len(xyz)
    fixed_a = np.asarray(fixed, dtype=bool)
    rep = StabilityReport()
    if not els:
        return rep
    I = np.array([e["i"] for e in els]); J = np.array([e["j"] for e in els])
    yh = np.array([e.get("yh", [0, 0, 1]) for e in els], dtype=float)
    ex, ey, ez, L = element_frames(xyz, I, J, yh)
    E = np.array([e["E"] for e in els], float); G = np.array([e["G"] for e in els], float)
    A = np.array([e["A"] for e in els], float); Iy = np.array([e["Iy"] for e in els], float)
    Iz = np.array([e["Iz"] for e in els], float); Jt = np.array([e["J"] for e in els], float)
    ri = np.array([bool(e.get("ri")) for e in els]); rj = np.array([bool(e.get("rj")) for e in els])

    srcs: Dict[int, Set[str]] = {}
    for e in els:
        if e.get("src"):
            srcs.setdefault(e["i"], set()).add(e["src"]); srcs.setdefault(e["j"], set()).add(e["src"])

    # ── groups not connected to the ground ────────────────────────────────
    dsu = _DSU(nn)
    for a, b in zip(I.tolist(), J.tolist()):
        dsu.union(a, b)
    on_dia = np.zeros(nn, dtype=bool)
    for d in dia:
        ns = d["nodes"]
        for k in ns:
            on_dia[k] = True
        for k in ns[1:]:
            dsu.union(ns[0], k)
    roots = np.array([dsu.find(i) for i in range(nn)])
    grounded = set(roots[fixed_a].tolist())
    groups: Dict[int, List[int]] = {}
    for i in range(nn):
        if roots[i] not in grounded:
            groups.setdefault(int(roots[i]), []).append(i)
    rep.floating_groups = sorted(groups.values(), key=len, reverse=True)
    floating = np.zeros(nn, dtype=bool)
    for g in rep.floating_groups:
        floating[g] = True

    # ── per-node stiffness blocks ─────────────────────────────────────────
    # translations: axial EA/L along x; transverse 12EI/L³ both ends fixed,
    # 3EI/L³ one end released, 0 both released.  rotations: torsion GJ/L
    # always; bending 4EI/L unless that end is released.
    both = ri & rj
    one = ri ^ rj
    cz = np.where(both, 0.0, np.where(one, 3.0, 12.0))
    kt = np.stack([E * A / L, cz * E * Iz / L**3, cz * E * Iy / L**3], axis=1)
    R = np.stack([ex, ey, ez], axis=1)                       # rows = local axes
    Kt = np.einsum("eki,ek,ekj->eij", R, kt, R)              # Rᵀ diag R
    def rot(rel):
        kb = np.stack([G * Jt / L, np.where(rel, 0.0, 4 * E * Iy / L),
                       np.where(rel, 0.0, 4 * E * Iz / L)], axis=1)
        return np.einsum("eki,ek,ekj->eij", R, kb, R)
    Kri, Krj = rot(ri), rot(rj)
    T = np.zeros((nn, 3, 3)); Rt = np.zeros((nn, 3, 3))
    np.add.at(T, I, Kt); np.add.at(T, J, Kt)
    np.add.at(Rt, I, Kri); np.add.at(Rt, J, Krj)

    scale_t = np.median(kt[:, 0]) if len(kt) else 1.0
    scale_r = np.median(G * Jt / L) if len(L) else 1.0
    tol_t, tol_r = 1e-9 * scale_t, 1e-9 * scale_r
    for n in range(nn):
        if fixed_a[n] or floating[n]:
            continue
        # translations: a diaphragm carries ux, uy
        keep = [2] if on_dia[n] else [0, 1, 2]
        sub = T[n][np.ix_(keep, keep)]
        w, v = np.linalg.eigh(sub)
        free = []
        for k in range(len(w)):
            if w[k] <= tol_t:
                ax = keep[int(np.argmax(np.abs(v[:, k])))]
                free.append("u" + "xyz"[ax])
        if free:
            rep.free_translations[n] = sorted(set(free))
        keepr = [0, 1] if on_dia[n] else [0, 1, 2]
        subr = Rt[n][np.ix_(keepr, keepr)]
        if np.linalg.eigvalsh(subr).min() <= tol_r:
            rep.free_rotations.append(n)
    for n in rep.mechanism_nodes:
        rep.srcs_at[n] = sorted(srcs.get(n, set()))
    return rep
