"""Validation of the OpenSees solver installation itself (no skyframe imports).

Benchmarks:

1. OpenSees "Basic Truss Example" (Example 1.1) — the official first example
   of the OpenSees documentation:
       https://opensees.berkeley.edu/wiki/index.php/Basic_Truss_Example
   2D 3-bar truss, kip/inch units, E = 3000 ksi, areas 10/5/5 in^2, load
   (100, -50) kip at the free node.  Published displacement of node 4:
       ux =  0.53009277 in
       uy = -0.17789364 in
   (values as printed in the official example output).

2. Same truss: support reactions must equilibrate the applied load exactly.

3. ops.eigen() correctness: a 2-DOF spring-mass chain (truss elements used as
   axial springs) whose eigenvalues are computed independently with numpy from
   the closed-form 2x2 K and M matrices.
"""

import math

import numpy as np
import openseespy.opensees as ops

# ---------------------------------------------------------------------------
# Official published node-4 displacements for the Basic Truss Example.
# Source: https://opensees.berkeley.edu/wiki/index.php/Basic_Truss_Example
# ---------------------------------------------------------------------------
PUBLISHED_UX = 0.53009277   # in
PUBLISHED_UY = -0.17789364  # in

# Truss geometry / properties from the official example (kip, inch)
TRUSS_NODES = {1: (0.0, 0.0), 2: (144.0, 0.0), 3: (168.0, 0.0), 4: (72.0, 96.0)}
TRUSS_E = 3000.0                       # ksi
TRUSS_ELEMS = [(1, 1, 4, 10.0), (2, 2, 4, 5.0), (3, 3, 4, 5.0)]  # (tag,i,j,A)
TRUSS_LOAD = (100.0, -50.0)            # kip at node 4


def _run_basic_truss():
    """Build and analyze the official Basic Truss Example; return results."""
    ops.wipe()
    ops.model("basic", "-ndm", 2, "-ndf", 2)

    for tag, (x, y) in TRUSS_NODES.items():
        ops.node(tag, x, y)
    for tag in (1, 2, 3):
        ops.fix(tag, 1, 1)

    ops.uniaxialMaterial("Elastic", 1, TRUSS_E)
    for tag, ni, nj, area in TRUSS_ELEMS:
        ops.element("Truss", tag, ni, nj, area, 1)

    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(4, *TRUSS_LOAD)

    # Exactly the solution strategy of the official example script.
    ops.system("BandSPD")
    ops.numberer("RCM")
    ops.constraints("Plain")
    ops.integrator("LoadControl", 1.0)
    ops.algorithm("Linear")
    ops.analysis("Static")
    ok = ops.analyze(1)
    assert ok == 0, "static analysis failed"

    disp = (ops.nodeDisp(4, 1), ops.nodeDisp(4, 2))
    ops.reactions()
    reac = {tag: (ops.nodeReaction(tag, 1), ops.nodeReaction(tag, 2))
            for tag in (1, 2, 3)}
    ops.wipe()
    return disp, reac


def _truss_numpy_solution():
    """Independent numpy solution of the same truss (2 free DOFs at node 4)."""
    x4, y4 = TRUSS_NODES[4]
    K = np.zeros((2, 2))
    for _tag, ni, _nj, area in TRUSS_ELEMS:
        xi, yi = TRUSS_NODES[ni]
        L = math.hypot(x4 - xi, y4 - yi)
        c, s = (x4 - xi) / L, (y4 - yi) / L
        K += (TRUSS_E * area / L) * np.array([[c * c, c * s], [c * s, s * s]])
    return np.linalg.solve(K, np.array(TRUSS_LOAD))


def test_basic_truss_published_displacements():
    """Basic Truss Example: node-4 displacements match the published values."""
    (ux, uy), _ = _run_basic_truss()
    assert math.isclose(ux, PUBLISHED_UX, rel_tol=1e-6)
    assert math.isclose(uy, PUBLISHED_UY, rel_tol=1e-6)

    # Belt-and-braces: an independent numpy stiffness solution must agree too.
    u_np = _truss_numpy_solution()
    assert math.isclose(ux, u_np[0], rel_tol=1e-10)
    assert math.isclose(uy, u_np[1], rel_tol=1e-10)


def test_basic_truss_reaction_equilibrium():
    """Basic Truss Example: support reactions equilibrate the applied load.

    Applied load is (+100, -50) kip, so the reactions must sum to
    (-100, +50) kip (OpenSees nodeReaction = force the support applies to the
    structure).
    """
    _, reac = _run_basic_truss()
    sum_fx = sum(r[0] for r in reac.values())
    sum_fy = sum(r[1] for r in reac.values())
    assert abs(sum_fx - (-TRUSS_LOAD[0])) < 1e-8
    assert abs(sum_fy - (-TRUSS_LOAD[1])) < 1e-8


def test_eigen_two_dof_spring_chain():
    """ops.eigen vs numpy for a 2-DOF spring-mass chain.

    Chain: ground --k1-- m1 --k2-- m2, modeled with unit-length elastic Truss
    elements acting as axial springs (k = E*A/L with A = L = 1, so E = k).
    Closed-form system:
        K = [[k1+k2, -k2], [-k2, k2]],   M = diag(m1, m2)
    Eigenvalues (omega^2) computed independently with numpy must match
    ops.eigen to 1e-8 relative.
    """
    k1, k2 = 200.0, 150.0   # kN/m (any consistent units)
    m1, m2 = 3.0, 2.0       # tonne

    ops.wipe()
    ops.model("basic", "-ndm", 2, "-ndf", 2)
    ops.node(1, 0.0, 0.0)
    ops.node(2, 1.0, 0.0)
    ops.node(3, 2.0, 0.0)
    ops.fix(1, 1, 1)   # ground
    ops.fix(2, 0, 1)   # axial (x) DOF only
    ops.fix(3, 0, 1)
    ops.uniaxialMaterial("Elastic", 1, k1)   # E*A/L = k1 (A = L = 1)
    ops.uniaxialMaterial("Elastic", 2, k2)
    ops.element("Truss", 1, 1, 2, 1.0, 1)
    ops.element("Truss", 2, 2, 3, 1.0, 2)
    ops.mass(2, m1, 0.0)
    ops.mass(3, m2, 0.0)

    lam_ops = ops.eigen("-fullGenLapack", 2)   # eigenvalues omega^2, ascending
    ops.wipe()

    # Independent numpy generalized eigenvalues of (K, M), M diagonal:
    K = np.array([[k1 + k2, -k2], [-k2, k2]])
    Minv_sqrt = np.diag([1.0 / math.sqrt(m1), 1.0 / math.sqrt(m2)])
    lam_np = np.sort(np.linalg.eigvalsh(Minv_sqrt @ K @ Minv_sqrt))

    assert len(lam_ops) == 2
    for lo, ln in zip(sorted(lam_ops), lam_np):
        assert math.isclose(lo, ln, rel_tol=1e-8)
