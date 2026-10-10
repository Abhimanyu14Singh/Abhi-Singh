"""Independent plane-stress reference solver for the wall verifications.

An 8-node serendipity (Q8) plane-stress finite-element code in pure numpy,
written for this suite and sharing NO code with SkyFrame / OpenSees: it
provides the converged 2D-elasticity reference for the cantilever-wall and
wall-with-openings problems (the role SAP2000 plays for the ETABS wall
examples in CSI's own verification manual).

Domain: the rectangle [0, Lx] x [0, Lz] split into nx x nz equal cells, any
of which may be omitted (openings).  Fixed base (z = 0), a uniform shear
traction of total ``P`` along the top edge (consistent Q8 edge weights
1/6, 4/6, 1/6), plane stress, thickness ``t``.  3x3 Gauss integration.

The stiffness is assembled directly into a BLOCK-TRIDIAGONAL store (block
size > half-bandwidth of the row-wise numbering) and solved by block
Cholesky, so fine meshes stay cheap without scipy.
"""

from __future__ import annotations

import math
from typing import Callable, Dict, Optional, Tuple

import numpy as np

_G = [(-math.sqrt(0.6), 5 / 9), (0.0, 8 / 9), (math.sqrt(0.6), 5 / 9)]
# serendipity node order: corners (-1,-1),(1,-1),(1,1),(-1,1), then mids
_XI = np.array([(-1, -1), (1, -1), (1, 1), (-1, 1),
                (0, -1), (1, 0), (0, 1), (-1, 0)], float)


def _shape(r, s):
    N = np.empty(8)
    dN = np.empty((8, 2))
    for k in range(4):
        ri, si = _XI[k]
        N[k] = 0.25 * (1 + r * ri) * (1 + s * si) * (r * ri + s * si - 1)
        dN[k, 0] = 0.25 * ri * (1 + s * si) * (2 * r * ri + s * si)
        dN[k, 1] = 0.25 * si * (1 + r * ri) * (r * ri + 2 * s * si)
    for k in range(4, 8):
        ri, si = _XI[k]
        if ri == 0:
            N[k] = 0.5 * (1 - r * r) * (1 + s * si)
            dN[k, 0] = -r * (1 + s * si)
            dN[k, 1] = 0.5 * si * (1 - r * r)
        else:
            N[k] = 0.5 * (1 + r * ri) * (1 - s * s)
            dN[k, 0] = 0.5 * ri * (1 - s * s)
            dN[k, 1] = -s * (1 + r * ri)
    return N, dN


def _ke(hx, hz, E, nu, t):
    """Q8 stiffness of an hx x hz rectangle (16 x 16)."""
    D = E / (1 - nu * nu) * np.array([[1, nu, 0], [nu, 1, 0],
                                      [0, 0, (1 - nu) / 2]])
    K = np.zeros((16, 16))
    J = np.array([hx / 2, hz / 2])
    for r, wr in _G:
        for s, ws in _G:
            _, dN = _shape(r, s)
            dx = dN[:, 0] / J[0]
            dz = dN[:, 1] / J[1]
            B = np.zeros((3, 16))
            B[0, 0::2] = dx
            B[1, 1::2] = dz
            B[2, 0::2] = dz
            B[2, 1::2] = dx
            K += B.T @ D @ B * wr * ws * J[0] * J[1] * t
    return K


def solve_wall(Lx: float, Lz: float, nx: int, nz: int, t: float, E: float,
               nu: float, P: float,
               omit: Optional[Callable[[int, int], bool]] = None
               ) -> Dict[Tuple[float, float], Tuple[float, float]]:
    """Solve the wall; returns {(x, z): (ux, uz)} for every node."""
    omit = omit or (lambda i, j: False)
    hx, hz = Lx / nx, Lz / nz
    cells = [(i, j) for j in range(nz) for i in range(nx) if not omit(i, j)]
    # node keys on the doubled grid (I, J); midside = exactly one odd index
    loc = [(0, 0), (2, 0), (2, 2), (0, 2), (1, 0), (2, 1), (1, 2), (0, 1)]
    used = set()
    for i, j in cells:
        for a, b in loc:
            used.add((2 * i + a, 2 * j + b))
    order = sorted(used, key=lambda p: (p[1], p[0]))     # row-wise
    nid = {p: k for k, p in enumerate(order)}
    ndof = 2 * len(order)
    fixed = np.zeros(ndof, bool)
    for p, k in nid.items():
        if p[1] == 0:
            fixed[2 * k:2 * k + 2] = True
    free = np.flatnonzero(~fixed)
    fmap = -np.ones(ndof, int)
    fmap[free] = np.arange(free.size)
    n = free.size
    edofs = []
    for i, j in cells:
        ids = [nid[(2 * i + a, 2 * j + b)] for a, b in loc]
        edofs.append(np.array([d for k in ids for d in (2 * k, 2 * k + 1)]))
    bw = 0
    for ed in edofs:
        f = fmap[ed]
        f = f[f >= 0]
        if f.size:
            bw = max(bw, int(f.max() - f.min()))
    bs = bw + 1
    nb = -(-n // bs)
    Dg = np.zeros((nb, bs, bs))
    Bg = np.zeros((nb, bs, bs))          # Bg[k] = block (k+1, k)
    ke = _ke(hx, hz, E, nu, t)
    for ed in edofs:
        f = fmap[ed]
        for a in range(16):
            ga = f[a]
            if ga < 0:
                continue
            for b in range(16):
                gb = f[b]
                if gb < 0:
                    continue
                ka, kb = ga // bs, gb // bs
                if ka == kb:
                    Dg[ka, ga % bs, gb % bs] += ke[a, b]
                elif ka == kb + 1:
                    Bg[kb, ga % bs, gb % bs] += ke[a, b]
    # pad the last block with identity
    pad = nb * bs - n
    for q in range(pad):
        Dg[-1, bs - 1 - q, bs - 1 - q] = 1.0
    F = np.zeros(nb * bs)
    q = P / Lx
    for i, j in cells:
        if j != nz - 1:
            continue
        for (a, b), w in (((0, 2), 1 / 6), ((1, 2), 4 / 6), ((2, 2), 1 / 6)):
            g = fmap[2 * nid[(2 * i + a, 2 * j + b)]]
            F[g] += w * q * hx
    # block Cholesky of the block-tridiagonal SPD matrix
    Lk = []
    Mk = []
    prevL = None
    for k in range(nb):
        A = Dg[k].copy()
        if k > 0:
            A -= Mk[-1] @ Mk[-1].T
        Lc = np.linalg.cholesky(A)
        Lk.append(Lc)
        if k + 1 < nb:
            Mk.append(np.linalg.solve(Lc, Bg[k].T).T)    # B L^-T
    y = np.zeros_like(F)
    for k in range(nb):
        rhs = F[k * bs:(k + 1) * bs].copy()
        if k > 0:
            rhs -= Mk[k - 1] @ y[(k - 1) * bs:k * bs]
        y[k * bs:(k + 1) * bs] = np.linalg.solve(Lk[k], rhs)
    x = np.zeros_like(F)
    for k in reversed(range(nb)):
        rhs = y[k * bs:(k + 1) * bs].copy()
        if k + 1 < nb:
            rhs -= Mk[k].T @ x[(k + 1) * bs:(k + 2) * bs]
        x[k * bs:(k + 1) * bs] = np.linalg.solve(Lk[k].T, rhs)
    u = np.zeros(ndof)
    u[free] = x[:n]
    out = {}
    for p, k in nid.items():
        out[(round(p[0] * hx / 2, 9), round(p[1] * hz / 2, 9))] = (
            u[2 * k], u[2 * k + 1])
    return out
