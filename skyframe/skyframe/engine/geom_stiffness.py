"""Geometric (initial-stress) stiffness of frames, trusses and shells.

CONTRACT "Buckling with shells and links, sparse eigen and BucklingFEM".

Element matrices are assembled into a sparse symmetric operator over the
engine's PHYSICAL dof space (every OpenSees node tag x 6 dofs), so it can be
multiplied with the displacement fields that the engine's solves return
(:mod:`skyframe.engine.lanczos`).  The stress state comes from the
elements' own OpenSees responses after a linear reference solve:

* ``elasticBeamColumn`` / ``ElasticTimoshenkoBeam`` — axial force
  ``N = (-localForce[0] + localForce[6]) / 2`` (tension positive; the mean
  of the two end values, i.e. exact for end-loaded members and the natural
  constant-N reduction under axial span loads).  The 12x12 consistent
  beam-column geometric stiffness of :mod:`skyframe.core.buckling`
  (identical local convention) is transformed to global axes.  NATIVE end
  releases (``-releasez`` / ``-releasey`` codes, the moment pins of
  :mod:`skyframe.engine.releases_eng`) are honoured by the same static
  condensation the element applies to its elastic stiffness: with the
  released end rotations ``u_r = -k_rr^-1 k_ra u_a`` (``k`` the bending
  stiffness of that plane — EI cancels, so the map depends on ``L`` only)
  the element geometric stiffness is ``C^T kg C`` and the released node
  rotation receives nothing.  Spring-path releases need nothing special:
  the element ends sit on their own duplicate nodes.
* ``Truss`` (axial-only members) — the string stiffness
  ``N / L (I - e e^T)`` on the end translations.
* shells (``ShellMITC4``, ``ShellDKGQ``, ``ASDShellQ4``, ``ShellDKGT``,
  ``ASDShellT3``) — the membrane stress resultants ``n11, n22, n12``
  (kN/m) at the element Gauss points ('stresses', after the 'forces'
  warm-up of the engine's ``_shell_outputs``) give the plate geometric
  stiffness of the Green-Lagrange membrane term

      Kg_e = sum_gp  B^T [[n11, n12], [n12, n22]] B  det J      (x I3)

  with ``B`` the in-plane gradients of the bilinear (quad) / linear
  (triangle) shape functions and the same 4x4 block on each of the three
  GLOBAL translations (the membrane term ``1/2 n_ab u_i,a u_i,b`` is
  rotation invariant, so out-of-plane AND in-plane geometric stiffness are
  included).  The element systems were probed on openseespy 3.7.1:
  ShellMITC4 / ShellDKGQ / ASDShellQ4 (``-local`` as the engine builds it)
  report in the MITC4 basis (``e1`` along the 1-4 to 2-3 mid-side line,
  ``e2`` Gram-Schmidt from the 1-2 to 4-3 line), Gauss points in the
  MITC4 order (-,-), (+,-), (+,+), (-,+); ShellDKGT in (edge 1-2, its
  in-plane normal); ASDShellT3 in (-e2, e1) of that triangle basis.
  Rotational dofs get no geometric stiffness (the classical plate form).

Units: kN, m.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from skyframe.core.buckling import _local_geometric

_G = 1.0 / math.sqrt(3.0)
#: MITC4 Gauss-point order (xi, eta)
GP4 = ((-_G, -_G), (_G, -_G), (_G, _G), (-_G, _G))
QUAD_FAMILY = ("ShellMITC4", "ShellDKGQ", "ASDShellQ4")
TRI_FAMILY = ("ShellDKGT", "ASDShellT3")


class Space:
    """Flat physical dof space: tag -> 6 consecutive slots."""

    def __init__(self, tags: Iterable[int]):
        self.tags = list(tags)
        self.idx = {t: i for i, t in enumerate(self.tags)}
        self.n = 6 * len(self.tags)

    def dofs(self, tags: Sequence[int]) -> np.ndarray:
        out = np.empty(6 * len(tags), dtype=np.int64)
        for k, t in enumerate(tags):
            b = 6 * self.idx[t]
            out[6 * k:6 * k + 6] = np.arange(b, b + 6)
        return out


class SymOp:
    """Sparse operator from element blocks (COO, summed on multiply)."""

    def __init__(self, n: int):
        self.n = n
        self._r: List[np.ndarray] = []
        self._c: List[np.ndarray] = []
        self._v: List[np.ndarray] = []
        self._frozen = None

    def add(self, dofs: np.ndarray, k: np.ndarray) -> None:
        nd = len(dofs)
        self._r.append(np.repeat(dofs, nd))
        self._c.append(np.tile(dofs, nd))
        self._v.append(np.asarray(k, dtype=float).reshape(-1))
        self._frozen = None

    def _arrays(self):
        if self._frozen is None:
            if self._r:
                r = np.concatenate(self._r)
                c = np.concatenate(self._c)
                v = np.concatenate(self._v)
                nz = v != 0.0
                self._frozen = (r[nz], c[nz], v[nz])
            else:
                z = np.zeros(0, dtype=np.int64)
                self._frozen = (z, z, np.zeros(0))
        return self._frozen

    def matvec(self, x: np.ndarray) -> np.ndarray:
        r, c, v = self._arrays()
        return np.bincount(r, weights=v * x[c], minlength=self.n)

    def nnz(self) -> int:
        return int(self._arrays()[0].size)

    def dense(self) -> np.ndarray:                        # tests only
        r, c, v = self._arrays()
        out = np.zeros((self.n, self.n))
        np.add.at(out, (r, c), v)
        return out


# --------------------------------------------------------------- frames
def _plane_condense(L: float, s: float, rel: Tuple[bool, bool]
                    ) -> np.ndarray:
    """4x4 map u_full = C u_nodal for one bending plane (v, th_i, v, th_j)
    with released end rotations condensed by the elastic bending stiffness
    (sign convention ``s`` as in skyframe.core.buckling)."""
    kb = np.array([
        [12.0, s * 6 * L, -12.0, s * 6 * L],
        [s * 6 * L, 4 * L * L, -s * 6 * L, 2 * L * L],
        [-12.0, -s * 6 * L, 12.0, -s * 6 * L],
        [s * 6 * L, 2 * L * L, -s * 6 * L, 4 * L * L]])
    r_idx = [k for k, f in ((1, rel[0]), (3, rel[1])) if f]
    a_idx = [k for k in range(4) if k not in r_idx]
    C = np.zeros((4, 4))
    for k in a_idx:
        C[k, k] = 1.0
    if r_idx:
        krr = kb[np.ix_(r_idx, r_idx)]
        kra = kb[np.ix_(r_idx, a_idx)]
        X = -np.linalg.solve(krr, kra)
        for ii, r in enumerate(r_idx):
            for jj, a in enumerate(a_idx):
                C[r, a] = X[ii, jj]
    return C


def frame_kg_local(N: float, L: float, code_z: int = 0,
                   code_y: int = 0) -> np.ndarray:
    """12x12 local geometric stiffness with native release condensation.

    ``code_z`` / ``code_y``: elasticBeamColumn ``-releasez`` (M3, plane
    x-y: dofs 1, 5, 7, 11) / ``-releasey`` (M2, plane x-z: dofs 2, 4, 8,
    10) codes — 1 = end i, 2 = end j, 3 = both."""
    kg = _local_geometric(N, L)
    if not code_z and not code_y:
        return kg
    C = np.eye(12)
    for dofs, s, code in (((1, 5, 7, 11), 1.0, code_z),
                          ((2, 4, 8, 10), -1.0, code_y)):
        if code:
            C[np.ix_(dofs, dofs)] = _plane_condense(
                L, s, (bool(code & 1), bool(code & 2)))
    return C.T @ kg @ C


def frame_kg_global(N: float, pi, pj, axes, code_z: int = 0,
                    code_y: int = 0) -> np.ndarray:
    """Global 12x12 frame geometric stiffness (axes = local x, y, z)."""
    L = math.dist(pi, pj)
    R = np.array(axes, dtype=float)
    T = np.zeros((12, 12))
    for i in range(4):
        T[3 * i:3 * i + 3, 3 * i:3 * i + 3] = R
    return T.T @ frame_kg_local(N, L, code_z, code_y) @ T


def truss_kg_global(N: float, pi, pj) -> np.ndarray:
    """12x12 (two nodes x 6 dofs) string stiffness of an axial member."""
    d = np.asarray(pj, float) - np.asarray(pi, float)
    L = float(np.linalg.norm(d))
    e = d / L
    s3 = (N / L) * (np.eye(3) - np.outer(e, e))
    k = np.zeros((12, 12))
    k[0:3, 0:3] = s3
    k[6:9, 6:9] = s3
    k[0:3, 6:9] = -s3
    k[6:9, 0:3] = -s3
    return k


# --------------------------------------------------------------- shells
def _quad_basis(P: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    v1 = 0.5 * (P[2] + P[1] - P[3] - P[0])
    v2 = 0.5 * (P[3] + P[2] - P[1] - P[0])
    v1 = v1 / np.linalg.norm(v1)
    v2 = v2 - v1 * float(v2 @ v1)
    v2 = v2 / np.linalg.norm(v2)
    return v1, v2


def _tri_basis(P: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    v1 = P[1] - P[0]
    v2 = P[2] - P[0]
    v1 = v1 / np.linalg.norm(v1)
    v2 = v2 - v1 * float(v2 @ v1)
    v2 = v2 / np.linalg.norm(v2)
    return v1, v2


def membrane_tensors(element: str, stresses: Sequence[float]
                     ) -> List[np.ndarray]:
    """Per-Gauss-point 2x2 membrane tensors in the element's (v1, v2)
    geometric basis (see the module doc for the per-element systems)."""
    vals = np.asarray(stresses, dtype=float).reshape(-1, 8)
    out = []
    for row in vals:
        a11, a22, a12 = float(row[0]), float(row[1]), float(row[2])
        if element == "ASDShellT3":            # reported in (-v2, v1)
            a11, a22, a12 = a22, a11, -a12
        out.append(np.array([[a11, a12], [a12, a22]]))
    return out


def shell_kg_scalar(element: str, P: np.ndarray,
                    tensors: Sequence[np.ndarray]) -> np.ndarray:
    """nn x nn membrane geometric stiffness block (one translation)."""
    P = np.asarray(P, dtype=float)
    nn = len(P)
    if nn == 4:
        v1, v2 = _quad_basis(P)
        xl = np.array([[p @ v1, p @ v2] for p in P])
        sgn = np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]], dtype=float)
        k = np.zeros((4, 4))
        nt = len(tensors)
        for g, (xi, eta) in enumerate(GP4):
            S = tensors[g] if nt == 4 else (sum(tensors) / nt)
            dN = 0.25 * np.array([sgn[:, 0] * (1 + sgn[:, 1] * eta),
                                  sgn[:, 1] * (1 + sgn[:, 0] * xi)])
            J = dN @ xl                       # 2x2 [[dx/dxi, dy/dxi], ...]
            det = float(np.linalg.det(J))
            B = np.linalg.solve(J, dN)        # 2 x 4 (d/dx, d/dy)
            k += B.T @ S @ B * det
        return k
    if nn == 3:
        v1, v2 = _tri_basis(P)
        xl = np.array([[p @ v1, p @ v2] for p in P])
        S = sum(tensors) / len(tensors)
        x, y = xl[:, 0], xl[:, 1]
        A2 = (x[1] - x[0]) * (y[2] - y[0]) - (x[2] - x[0]) * (y[1] - y[0])
        B = np.array([[y[1] - y[2], y[2] - y[0], y[0] - y[1]],
                      [x[2] - x[1], x[0] - x[2], x[1] - x[0]]]) / A2
        return B.T @ S @ B * (0.5 * abs(A2))
    raise ValueError(f"shell element with {nn} nodes")


def shell_kg_global(element: str, P, tensors) -> np.ndarray:
    """(6 nn) x (6 nn) shell geometric stiffness on the node dofs."""
    ks = shell_kg_scalar(element, P, tensors)
    nn = ks.shape[0]
    k = np.zeros((6 * nn, 6 * nn))
    for c in range(3):
        idx = [6 * a + c for a in range(nn)]
        k[np.ix_(idx, idx)] = ks
    return k


def constant_tensors(n11: float, n22: float, n12: float, n_gp: int
                     ) -> List[np.ndarray]:
    t = np.array([[n11, n12], [n12, n22]], dtype=float)
    return [t] * n_gp
