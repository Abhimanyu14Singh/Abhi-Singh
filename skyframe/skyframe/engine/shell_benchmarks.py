"""Classical shell-element benchmarks run directly in OpenSees.

CONTRACT "Shell element types, one-way slabs and shell benchmarks".  Each
problem builds a stand-alone OpenSees model (ndm 3 / ndf 6) on an N x N
refinement level with ONE element family (a quad element, or a triangle
element with every quad split along its 0-2 diagonal) and an
``ElasticMembranePlateSection``, and returns the computed quantity
normalised by its reference value (1.0 = exact).

Problems (reference values from MacNeal & Harder 1985, Belytschko et al.
1985, Timoshenko & Woinowsky-Krieger, Morley 1963):

* ``scordelis_lo``   quarter roof N x N, vertical midside free-edge
  displacement, reference 0.3024 ft;
* ``pinched_cylinder``  octant N x N, radial displacement under the load,
  reference 1.8248e-5;
* ``twisted_beam_inplane`` / ``twisted_beam_outplane``  (N/2) x (3N) mesh
  (N = 4 -> the standard 2 x 12), references 5.424e-3 / 1.754e-3;
* ``plate_thin``  hard simply-supported square plate, a/t = 1000, full
  N x N mesh, centre deflection vs the Navier series (0.00406 q a^4/D);
* ``plate_thick`` same at a/t = 10 vs the Mindlin solution
  w = w_K + M_marcus/(kappa G t) (kappa = 5/6; Navier series of both terms);
* ``morley_skew``  30-degree rhombic plate, L/t = 1000, hard SS (w = 0
  plus a penalty on the rotation about each edge normal), centre
  deflection vs 0.000408 q L^4 / D;
* ``cantilever_trapezoid_inplane`` / ``..._outplane``  MacNeal-Harder
  straight cantilever (6 x 0.2 x 0.1, E 1e7, nu 0.3) with the trapezoidal
  distorted mesh, (3N/2) x (N/4) elements (N = 4 -> the standard 6 x 1),
  tip shear 1, references 0.1081 / 0.4321.
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Sequence, Tuple

import openseespy.opensees as ops

Vec = Tuple[float, float, float]


# --------------------------------------------------------------- harness
def _solve(nodes: List[Vec], cells: List[Tuple[int, ...]], element: str,
           E: float, nu: float, t: float,
           fix: Callable[[int, Vec], Sequence[int]],
           loads: Dict[int, Sequence[float]],
           rot_springs: Sequence[Tuple[int, Vec, float]] = ()
           ) -> List[List[float]]:
    """Linear static solve; returns per-node 6-dof displacements.

    ``rot_springs`` (node, unit in-plane axis, k): a grounded zeroLength
    rotational penalty about that axis (skewed hard-SS edges)."""
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    for i, p in enumerate(nodes):
        ops.node(i + 1, *p)
        f = list(fix(i, p))
        if any(f):
            ops.fix(i + 1, *f)
    ops.section("ElasticMembranePlateSection", 1, E, nu, t, 0.0)
    tri = element in ("ShellDKGT", "ASDShellT3", "ShellNLDKGT")
    etag = 0
    for c in cells:
        parts = [(c[0], c[1], c[2]), (c[0], c[2], c[3])] if tri else [c]
        for part in parts:
            etag += 1
            ops.element(element, etag, *[n + 1 for n in part], 1)
    for k_sp, (n, ax, k) in enumerate(rot_springs):
        g = len(nodes) + 1 + k_sp
        ops.node(g, *nodes[n])
        ops.fix(g, 1, 1, 1, 1, 1, 1)
        ops.uniaxialMaterial("Elastic", k_sp + 1, k)
        etag += 1
        ops.element("zeroLength", etag, n + 1, g, "-mat", k_sp + 1,
                    "-dir", 4, "-orient", *ax, -ax[1], ax[0], 0.0)
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    for n, f in loads.items():
        ops.load(n + 1, *f)
    ops.system("UmfPack")
    ops.numberer("RCM")
    ops.constraints("Plain")
    ops.integrator("LoadControl", 1.0)
    ops.algorithm("Linear")
    ops.analysis("Static")
    if ops.analyze(1) != 0:                       # pragma: no cover
        raise RuntimeError(f"{element}: analysis failed")
    out = [[ops.nodeDisp(i + 1, d) for d in range(1, 7)]
           for i in range(len(nodes))]
    ops.wipe()
    return out


def _grid(nu_: int, nv: int, fmap: Callable[[float, float], Vec]
          ) -> Tuple[List[Vec], List[Tuple[int, ...]], Callable]:
    nodes = [fmap(i / nu_, j / nv) for j in range(nv + 1)
             for i in range(nu_ + 1)]

    def idx(i: int, j: int) -> int:
        return j * (nu_ + 1) + i
    cells = [(idx(i, j), idx(i + 1, j), idx(i + 1, j + 1), idx(i, j + 1))
             for j in range(nv) for i in range(nu_)]
    return nodes, cells, idx


def _area_loads(nodes, cells, q: Vec) -> Dict[int, List[float]]:
    """Lumped pressure: each cell's exact (two-triangle) area / 4 per node."""
    def tri_area(a, b, c):
        u = [b[k] - a[k] for k in range(3)]
        v = [c[k] - a[k] for k in range(3)]
        x = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
             u[0] * v[1] - u[1] * v[0])
        return 0.5 * math.sqrt(sum(e * e for e in x))
    loads: Dict[int, List[float]] = {}
    for c in cells:
        p = [nodes[n] for n in c]
        a = tri_area(p[0], p[1], p[2]) + tri_area(p[0], p[2], p[3])
        for n in c:
            f = loads.setdefault(n, [0.0] * 6)
            for k in range(3):
                f[k] += q[k] * a / 4.0
    return loads


def _close(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) < tol


# --------------------------------------------------------------- problems
SCORDELIS_REF = 0.3024
PINCHED_REF = 1.8248e-5
TWIST_INPLANE_REF = 5.424e-3
TWIST_OUTPLANE_REF = 1.754e-3
MORLEY_REF = 0.000408
CANT_INPLANE_REF = 0.1081
CANT_OUTPLANE_REF = 0.4321


def scordelis_lo(element: str, n: int) -> float:
    R, L, t, E, nu, q = 25.0, 50.0, 0.25, 4.32e8, 0.0, 90.0
    th = math.radians(40.0)
    nodes, cells, idx = _grid(n, n, lambda u, v: (
        R * math.sin(u * th), v * L / 2.0, R * math.cos(u * th)))

    def fix(i, p):
        f = [0] * 6
        if _close(p[1], 0.0):                 # rigid diaphragm
            f[0] = f[2] = 1
        if _close(p[1], L / 2.0):             # symmetry y = L/2
            f[1] = f[3] = f[5] = 1
        if _close(p[0], 0.0):                 # symmetry x = 0
            f[0] = f[4] = f[5] = 1
        return f
    d = _solve(nodes, cells, element, E, nu, t, fix,
               _area_loads(nodes, cells, (0.0, 0.0, -q)))
    return -d[idx(n, n)][2] / SCORDELIS_REF


def pinched_cylinder(element: str, n: int) -> float:
    R, L, t, E, nu, P = 300.0, 600.0, 3.0, 3e6, 0.3, 1.0
    nodes, cells, idx = _grid(n, n, lambda u, v: (
        R * math.sin(u * math.pi / 2), v * L / 2.0,
        R * math.cos(u * math.pi / 2)))

    def fix(i, p):
        f = [0] * 6
        if _close(p[1], 0.0):
            f[0] = f[2] = 1
        if _close(p[1], L / 2.0):
            f[1] = f[3] = f[5] = 1
        if _close(p[0], 0.0):
            f[0] = f[4] = f[5] = 1
        if _close(p[2], 0.0, 1e-6):
            f[2] = f[3] = f[4] = 1
        return f
    d = _solve(nodes, cells, element, E, nu, t, fix,
               {idx(0, n): [0, 0, -P / 4.0, 0, 0, 0]})
    return -d[idx(0, n)][2] / PINCHED_REF


def _twisted(element: str, n: int, inplane: bool) -> float:
    L, w, t, E, nu = 12.0, 1.1, 0.32, 29e6, 0.22
    nw, nl = max(1, n // 2), 3 * n

    def fmap(u, v):
        x = u * L
        s = (v - 0.5) * w
        phi = 0.5 * math.pi * u
        return (x, s * math.cos(phi), s * math.sin(phi))
    nodes, cells, idx = _grid(nl, nw, fmap)

    def fix(i, p):
        return [1] * 6 if _close(p[0], 0.0) else [0] * 6
    k = 2 if inplane else 1                    # tip width dir = global Z
    loads = {}
    for j in range(nw + 1):
        share = (0.5 if j in (0, nw) else 1.0) / nw
        f = [0.0] * 6
        f[k] = share
        loads[idx(nl, j)] = f
    d = _solve(nodes, cells, element, E, nu, t, fix, loads)
    tip = sum(d[idx(nl, j)][k] for j in range(nw + 1)) / (nw + 1)
    return tip / (TWIST_INPLANE_REF if inplane else TWIST_OUTPLANE_REF)


def twisted_beam_inplane(element: str, n: int) -> float:
    return _twisted(element, n, True)


def twisted_beam_outplane(element: str, n: int) -> float:
    return _twisted(element, n, False)


def navier_plate(a_over_t: float, nu: float = 0.3, terms: int = 199
                 ) -> Tuple[float, float]:
    """(w_K D/(q a^4), Mindlin w D/(q a^4)) at the centre of a hard-SS
    square plate (a = 1): Navier double series; the Mindlin term adds
    M_marcus / (kappa G t) with kappa = 5/6 (Wang's relationship)."""
    wk = mm = 0.0
    for m in range(1, terms + 1, 2):
        for k in range(1, terms + 1, 2):
            s = math.sin(m * math.pi / 2) * math.sin(k * math.pi / 2)
            den = m * k * (m * m + k * k)
            wk += 16.0 / math.pi ** 6 * s / (den * (m * m + k * k))
            mm += 16.0 / math.pi ** 4 * s / den
    t = 1.0 / a_over_t
    # D/(kappa G t) = t^2 / (6 (1 - nu) kappa)
    return wk, wk + mm * t * t / (6.0 * (1.0 - nu) * 5.0 / 6.0)


def _plate(element: str, n: int, a_over_t: float, mindlin_ref: bool
           ) -> float:
    a, E, nu, q = 1.0, 1.0e6, 0.3, 1.0
    t = a / a_over_t
    D = E * t ** 3 / (12.0 * (1.0 - nu * nu))
    nodes, cells, idx = _grid(n, n, lambda u, v: (u * a, v * a, 0.0))

    def fix(i, p):
        f = [1, 1, 0, 0, 0, 1]                 # pure plate problem
        if _close(p[0], 0.0) or _close(p[0], a):
            f[2] = f[3] = 1                    # hard SS: w = 0, rot_x = 0
        if _close(p[1], 0.0) or _close(p[1], a):
            f[2] = f[4] = 1                    # w = 0, rot_y = 0
        return f
    d = _solve(nodes, cells, element, E, nu, t, fix,
               _area_loads(nodes, cells, (0.0, 0.0, -q)))
    wk, wm = navier_plate(a_over_t, nu)
    ref = (wm if mindlin_ref else wk) * q * a ** 4 / D
    return -d[idx(n // 2, n // 2)][2] / ref


def plate_thin(element: str, n: int) -> float:
    return _plate(element, n, 1000.0, False)


def plate_thick(element: str, n: int) -> float:
    return _plate(element, n, 10.0, True)


def morley_skew(element: str, n: int) -> float:
    L, E, nu, q = 100.0, 1.0e6, 0.3, 1.0
    t = L / 1000.0
    D = E * t ** 3 / (12.0 * (1.0 - nu * nu))
    c, s = math.cos(math.radians(30.0)), math.sin(math.radians(30.0))
    nodes, cells, idx = _grid(n, n, lambda u, v: (u * L + v * L * c,
                                                  v * L * s, 0.0))

    def fix(i, p):
        f = [1, 1, 0, 0, 0, 1]
        on_edge = (_close(p[1], 0.0, 1e-7) or _close(p[1], L * s, 1e-7)
                   or _close(p[0] - p[1] * c / s, 0.0, 1e-7)
                   or _close(p[0] - p[1] * c / s, L, 1e-7))
        if on_edge:
            f[2] = 1                           # w = 0
        return f
    # hard SS: rotation about each edge's in-plane normal (= the slope
    # along the edge) is restrained by a stiff penalty spring; for a
    # Kirchhoff element it is implied by w = 0 along the edge
    springs = []
    for j in range(n + 1):
        for i in range(n + 1):
            if j in (0, n):
                springs.append((idx(i, j), (0.0, 1.0, 0.0), 1e12))
            if i in (0, n):
                springs.append((idx(i, j), (-s, c, 0.0), 1e12))
    d = _solve(nodes, cells, element, E, nu, t, fix,
               _area_loads(nodes, cells, (0.0, 0.0, -q)), springs)
    return -d[idx(n // 2, n // 2)][2] / (MORLEY_REF * q * L ** 4 / D)


def _cantilever(element: str, n: int, inplane: bool) -> float:
    L, w, t, E, nu = 6.0, 0.2, 0.1, 1.0e7, 0.3
    nl, nw = 3 * n // 2, max(1, n // 4)
    nodes: List[Vec] = []
    for j in range(nw + 1):
        y = j * w / nw
        for i in range(nl + 1):
            x = i * L / nl
            if 0 < i < nl:                    # trapezoidal distortion:
                sgn = 1.0 if i % 2 else -1.0  # interior lines +-45 deg
                x += sgn * (y - w / 2.0)      # (tan 45 = 1)
            nodes.append((x, y, 0.0))

    def idx(i, j):
        return j * (nl + 1) + i
    cells = [(idx(i, j), idx(i + 1, j), idx(i + 1, j + 1), idx(i, j + 1))
             for j in range(nw) for i in range(nl)]

    def fix(i, p):
        return [1] * 6 if _close(p[0], 0.0) else [0] * 6
    k = 1 if inplane else 2
    loads = {}
    for j in range(nw + 1):
        f = [0.0] * 6
        f[k] = (0.5 if j in (0, nw) else 1.0) / nw
        loads[idx(nl, j)] = f
    d = _solve(nodes, cells, element, E, nu, t, fix, loads)
    tip = sum(d[idx(nl, j)][k] for j in range(nw + 1)) / (nw + 1)
    return tip / (CANT_INPLANE_REF if inplane else CANT_OUTPLANE_REF)


def cantilever_trapezoid_inplane(element: str, n: int) -> float:
    return _cantilever(element, n, True)


def cantilever_trapezoid_outplane(element: str, n: int) -> float:
    return _cantilever(element, n, False)


PROBLEMS: Dict[str, Callable[[str, int], float]] = {
    "scordelis_lo": scordelis_lo,
    "pinched_cylinder": pinched_cylinder,
    "twisted_beam_inplane": twisted_beam_inplane,
    "twisted_beam_outplane": twisted_beam_outplane,
    "plate_thin": plate_thin,
    "plate_thick": plate_thick,
    "morley_skew": morley_skew,
    "cantilever_trapezoid_inplane": cantilever_trapezoid_inplane,
    "cantilever_trapezoid_outplane": cantilever_trapezoid_outplane,
}


def convergence_table(elements: Sequence[str],
                      levels: Sequence[int] = (4, 8, 16)
                      ) -> Dict[str, Dict[str, List[float]]]:
    """{problem: {element: [normalised result per level]}}."""
    return {name: {e: [fn(e, n) for n in levels] for e in elements}
            for name, fn in PROBLEMS.items()}
