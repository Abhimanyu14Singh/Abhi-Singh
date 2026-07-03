"""SkyFrame engine vs closed-form structural mechanics benchmarks.

Independent hand solutions in the style of the CSI "Software Verification"
examples for ETABS/SAP2000.  Every expected number is derived in the test from
first principles (beam theory formulas or an independent numpy stiffness /
eigenvalue computation) — nothing is fabricated.

Cases:
  a. Cantilever column, tip load          delta = P L^3 / 3EI, theta = P L^2 / 2EI
  b. Simply supported beam, UDL           delta = 5 w L^4 / 384EI, R = wL/2, M = wL^2/8
  c. Portal frame lateral load            independent numpy 2D-frame assembly (1e-8)
  d. 3-story shear building modal         numpy eig of tridiagonal K vs engine (1%)
  e. SDOF column period                   T = 2 pi sqrt(m / (3EI/L^3))
  f. Combo superposition                  combo == sum(factor * case)
  g. Global equilibrium of quick_building base reactions vs applied loads

Units per CONTRACT.md: kN, m, tonne, s; E in kPa.
"""

import math

import numpy as np
import pytest

# Skip the whole module cleanly while the engine silo is still being written.
engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core.builder import quick_building
from skyframe.core.model import (
    BuildingModel,
    FrameSection,
    Material,
    MemberUDL,
    NodalLoad,
    NodalMass,
    PointSupport,
)

E_CONC = 25_000_000.0  # kPa


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _run(model) -> dict:
    """Run the engine per CONTRACT.md and return results.to_dict()."""
    return OpenSeesEngine(model).run().to_dict()


def _find_node(results: dict, pt, tol=1e-6):
    """Locate the FE node tag (dict key) at coordinates pt in results['nodes']."""
    for tag, xyz in results["nodes"].items():
        if all(abs(a - b) < tol for a, b in zip(xyz, pt)):
            return tag
    raise AssertionError(f"no FE node found at {pt}; nodes={results['nodes']}")


def _new_model(name: str, story_heights) -> BuildingModel:
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.set_stories(list(story_heights))
    return mdl


# --------------------------------------------------------------------------- #
# a. Cantilever column with tip lateral load
# --------------------------------------------------------------------------- #
def test_cantilever_column_tip_load():
    """Cantilever column: delta = PL^3/3EI, theta = PL^2/2EI, base M = PL.

    L = 3 m vertical column, fixed base, square 0.4 m section (square, so the
    result is independent of the engine's local-axis orientation), E = 25e6
    kPa, tip load P = 10 kN in +X.  elasticBeamColumn (Euler, no shear
    deformation) is exact for a tip point load.
    """
    L, P, size = 3.0, 10.0, 0.4
    I = size ** 4 / 12.0                       # 2.1333e-3 m^4
    delta = P * L ** 3 / (3.0 * E_CONC * I)    # tip deflection
    theta = P * L ** 2 / (2.0 * E_CONC * I)    # tip rotation about Y

    mdl = _new_model("Cantilever", [L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1", uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.pattern("P", "other").nodal_loads.append(NodalLoad((0, 0, L), fx=P))
    mdl.add_case("P", {"P": 1.0})
    # a token tip mass so engine.run()'s modal stage has mass to work with
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=1.0, my=1.0))
    mdl.num_modes = 1

    d = _run(mdl)
    case = d["cases"]["P"]
    tip = _find_node(d, (0, 0, L))
    base = _find_node(d, (0, 0, 0))

    ux = case["node_disp"][tip][0]
    ry = case["node_disp"][tip][4]
    assert ux == pytest.approx(delta, rel=1e-3)          # PL^3/3EI, signed (+X)
    assert abs(ry) == pytest.approx(theta, rel=1e-3)     # PL^2/2EI

    # Base reactions oppose the applied load (CONTRACT sign convention).
    assert case["base"]["FX"] == pytest.approx(-P, rel=1e-6)     # base shear = P
    assert abs(case["base"]["MY"]) == pytest.approx(P * L, rel=1e-6)  # base moment
    assert case["reactions"][base][0] == pytest.approx(-P, rel=1e-6)

    # Column base bending moment from member local end forces = P*L
    mf = case["member_forces"]["C1"]
    assert max(abs(mf[4]), abs(mf[5])) == pytest.approx(P * L, rel=1e-3)


# --------------------------------------------------------------------------- #
# b. Simply supported beam under UDL
# --------------------------------------------------------------------------- #
def test_simply_supported_beam_udl():
    """SS beam, UDL: delta_mid = 5wL^4/384EI, R = wL/2, M_mid = wL^2/8.

    L = 8 m beam along X at z = 0, meshed as two members so the midspan node
    exists.  Supports: A pinned (torsion + all translations), B roller
    (axial free).  Euler FE with consistent fixed-end load vector gives exact
    nodal displacements and reactions for a prismatic beam.
    """
    L, w = 8.0, 12.0
    b, h = 0.3, 0.6
    I = b * h ** 3 / 12.0                                   # strong axis I33
    delta = 5.0 * w * L ** 4 / (384.0 * E_CONC * I)         # 4.7407e-3 m
    R = w * L / 2.0                                         # 48 kN
    M_mid = w * L ** 2 / 8.0                                # 96 kN*m

    mdl = _new_model("SS beam", [1.0])
    mdl.add_section(FrameSection.rectangular("BEAM", "CONC", b, h))
    mdl.add_member("beam", "BEAM", (0, 0, 0), (L / 2, 0, 0), story="Story1", uid="B1")
    mdl.add_member("beam", "BEAM", (L / 2, 0, 0), (L, 0, 0), story="Story1", uid="B2")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 0, 0)))  # pin + torsion
    mdl.supports.append(PointSupport((L, 0, 0), (0, 1, 1, 1, 0, 0)))  # roller
    dead = mdl.pattern("DEAD", "dead")
    dead.member_udls.append(MemberUDL("B1", w))   # w positive downward
    dead.member_udls.append(MemberUDL("B2", w))
    mdl.add_case("DEAD", {"DEAD": 1.0})
    mdl.nodal_masses.append(NodalMass((L / 2, 0, 0), mz=1.0))  # token modal mass
    mdl.num_modes = 1

    d = _run(mdl)
    case = d["cases"]["DEAD"]
    na = _find_node(d, (0, 0, 0))
    nb = _find_node(d, (L, 0, 0))
    nm = _find_node(d, (L / 2, 0, 0))

    # midspan deflection: downward => negative uz
    assert case["node_disp"][nm][2] == pytest.approx(-delta, rel=1e-3)

    # end reactions: supports push up (+Z), wL/2 each
    assert case["reactions"][na][2] == pytest.approx(R, rel=1e-6)
    assert case["reactions"][nb][2] == pytest.approx(R, rel=1e-6)
    assert case["base"]["FZ"] == pytest.approx(w * L, rel=1e-6)

    # midspan moment from member local end forces of B1 at end j (= midspan)
    mf = case["member_forces"]["B1"]
    assert max(abs(mf[10]), abs(mf[11])) == pytest.approx(M_mid, rel=1e-3)
    # ...and ~zero moment at the pinned end i
    assert max(abs(mf[4]), abs(mf[5])) < 1e-6 * M_mid + 1e-6


# --------------------------------------------------------------------------- #
# c. Portal frame lateral drift vs independent numpy assembly
# --------------------------------------------------------------------------- #
def _frame2d_k(E, A, I, pi, pj):
    """Global 6x6 stiffness of a 2D Euler frame element (axial + bending).

    Plane DOFs per node: (u_X, u_Z, theta), theta = in-plane rotation.
    """
    (xi, zi), (xj, zj) = pi, pj
    Lx, Lz = xj - xi, zj - zi
    L = math.hypot(Lx, Lz)
    c, s = Lx / L, Lz / L
    ea, ei = E * A / L, E * I
    kl = np.array([
        [ ea,             0.0,            0.0,          -ea,            0.0,            0.0],
        [0.0,  12 * ei / L**3,  6 * ei / L**2,          0.0, -12 * ei / L**3,  6 * ei / L**2],
        [0.0,   6 * ei / L**2,   4 * ei / L,            0.0,  -6 * ei / L**2,   2 * ei / L],
        [-ea,            0.0,            0.0,           ea,            0.0,            0.0],
        [0.0, -12 * ei / L**3, -6 * ei / L**2,          0.0,  12 * ei / L**3, -6 * ei / L**2],
        [0.0,   6 * ei / L**2,   2 * ei / L,            0.0,  -6 * ei / L**2,   4 * ei / L],
    ])
    R = np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]])
    T = np.zeros((6, 6))
    T[:3, :3] = R
    T[3:, 3:] = R
    return T.T @ kl @ T


def test_portal_frame_vs_independent_numpy_assembly():
    """Single-bay fixed-base portal, lateral load: engine vs an independent
    numpy 2D-frame stiffness solution (axial + bending, no shear) to 1e-8.

    Geometry (XZ plane): columns h = 4 m at x = 0 and x = 6, beam span
    L = 6 m.  Square sections (orientation-proof): columns 0.4 m, beam
    0.35 m.  Load P = 50 kN in +X at the top of the left column.  This is a
    true independent-code cross validation: the 6-free-DOF system (u, w,
    theta at both top nodes) is assembled by hand with standard 2D beam
    element matrices including axial terms — the same physics the 3D engine
    solves for this planar, in-plane-loaded model.
    """
    h, span, P = 4.0, 6.0, 50.0
    col, beam = 0.4, 0.35
    Ac, Ic = col * col, col ** 4 / 12.0
    Ab, Ib = beam * beam, beam ** 4 / 12.0

    # ---- independent numpy solution --------------------------------------
    # 2D nodes: 0=(0,0) 1=(6,0) fixed;  2=(0,4) 3=(6,4) free (3 DOFs each)
    pts = [(0.0, 0.0), (span, 0.0), (0.0, h), (span, h)]
    elems = [(0, 2, Ac, Ic), (1, 3, Ac, Ic), (2, 3, Ab, Ib)]
    K = np.zeros((12, 12))
    for ni, nj, A, I in elems:
        ke = _frame2d_k(E_CONC, A, I, pts[ni], pts[nj])
        dofs = [3 * ni, 3 * ni + 1, 3 * ni + 2, 3 * nj, 3 * nj + 1, 3 * nj + 2]
        K[np.ix_(dofs, dofs)] += ke
    free = [6, 7, 8, 9, 10, 11]           # nodes 2 and 3
    F = np.zeros(6)
    F[0] = P                               # u_X of node 2
    u = np.linalg.solve(K[np.ix_(free, free)], F)
    ux2, uz2, th2, ux3, uz3, th3 = u

    # ---- engine solution ---------------------------------------------------
    mdl = _new_model("Portal", [h])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", col, col))
    mdl.add_section(FrameSection.rectangular("BEAM", "CONC", beam, beam))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, h), story="Story1", uid="CL")
    mdl.add_member("column", "COL", (span, 0, 0), (span, 0, h), story="Story1", uid="CR")
    mdl.add_member("beam", "BEAM", (0, 0, h), (span, 0, h), story="Story1", uid="BM")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.supports.append(PointSupport((span, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.pattern("EQ", "quake").nodal_loads.append(NodalLoad((0, 0, h), fx=P))
    mdl.add_case("EQ", {"EQ": 1.0})
    mdl.nodal_masses.append(NodalMass((0, 0, h), mx=1.0))   # token modal mass
    mdl.num_modes = 1

    d = _run(mdl)
    case = d["cases"]["EQ"]
    n2 = _find_node(d, (0, 0, h))
    n3 = _find_node(d, (span, 0, h))
    d2, d3 = case["node_disp"][n2], case["node_disp"][n3]

    # lateral drifts (the headline number) — independent-code match to 1e-8
    assert d2[0] == pytest.approx(ux2, rel=1e-8)
    assert d3[0] == pytest.approx(ux3, rel=1e-8)
    # vertical displacements from column axial force (small but exact)
    assert d2[2] == pytest.approx(uz2, rel=1e-8, abs=1e-14)
    assert d3[2] == pytest.approx(uz3, rel=1e-8, abs=1e-14)
    # joint rotations: 2D theta maps to +/- global ry — compare magnitudes
    assert abs(d2[4]) == pytest.approx(abs(th2), rel=1e-8)
    assert abs(d3[4]) == pytest.approx(abs(th3), rel=1e-8)

    # global equilibrium: base shear equals -P
    assert case["base"]["FX"] == pytest.approx(-P, rel=1e-8)


# --------------------------------------------------------------------------- #
# d. 3-story shear-building modal benchmark
# --------------------------------------------------------------------------- #
def test_three_story_shear_building_modal():
    """3-story shear frame periods vs numpy eig of the tridiagonal K.

    quick_building 1x1 bay, 3 stories, h = 3 m; beams made flexurally rigid
    (I x1000) and columns axially rigid (A x1000) so the frame approaches the
    ideal shear building: story stiffness k = 4 * 12 E Ic / h^3, story mass m
    from compute_story_masses().  Engine periods must contain (x and y sway
    pairs) each closed-form period within 1.0%; modal mass participation in X
    must sum to >= 0.999.
    """
    h = 3.0
    mdl = quick_building(name="ShearBldg", bays_x=1, bays_y=1, stories=3,
                         story_height=h, E=E_CONC, column_size=0.5)
    # idealize: rigid beams (no joint rotation), axially rigid columns
    mdl.sections["BEAM"].I33 *= 1000.0
    mdl.sections["BEAM"].I22 *= 1000.0
    mdl.sections["COL"].A *= 1000.0

    Ic = mdl.sections["COL"].I33                     # 0.5^4/12
    k = 4 * 12.0 * E_CONC * Ic / h ** 3              # 4 columns per story
    masses = mdl.compute_story_masses()              # tonne, per story
    m = [masses[s.name] for s in mdl.stories]        # bottom -> top

    # closed-form shear building: tridiagonal K, diagonal M
    K = np.array([[2 * k, -k, 0.0],
                  [-k, 2 * k, -k],
                  [0.0, -k, k]])
    Minv_sqrt = np.diag([1.0 / math.sqrt(mi) for mi in m])
    lam = np.sort(np.linalg.eigvalsh(Minv_sqrt @ K @ Minv_sqrt))   # omega^2
    T_exact = sorted((2.0 * math.pi / math.sqrt(l) for l in lam), reverse=True)

    d = _run(mdl)
    periods = d["modal"]["periods"]
    assert len(periods) >= 6   # 3 sway-x + 3 sway-y at least

    for Ti in T_exact:
        near = [Tp for Tp in periods if abs(Tp - Ti) / Ti < 0.01]
        # symmetric plan: an x-sway AND a y-sway mode at (about) each period
        assert len(near) >= 2, (
            f"expected >=2 engine periods within 1% of T={Ti:.5f}s, "
            f"got {sorted(periods, reverse=True)}")

    # all X mass lives in the 3 lateral sway DOFs
    sum_ux = sum(p["ux"] for p in d["modal"]["participation"])
    assert sum_ux >= 0.999


# --------------------------------------------------------------------------- #
# e. SDOF column period sanity
# --------------------------------------------------------------------------- #
def test_sdof_column_period():
    """SDOF cantilever: T = 2 pi sqrt(m / k), k = 3EI/L^3.

    Column L = 3 m, square 0.3 m, tip mass 10 t on ux and uy.  Static
    condensation of the Euler column with tip-only mass is exactly the SDOF
    with k = 3EI/L^3, so 0.1% tolerance.  Symmetry gives two identical
    periods (x and y); the pair spans all translational mass.
    """
    L, size, m = 3.0, 0.3, 10.0
    I = size ** 4 / 12.0
    k = 3.0 * E_CONC * I / L ** 3            # 1875 kN/m
    T = 2.0 * math.pi * math.sqrt(m / k)     # s

    mdl = _new_model("SDOF", [L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1", uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=m, my=m))
    mdl.pattern("PX", "other").nodal_loads.append(NodalLoad((0, 0, L), fx=1.0))
    mdl.add_case("PX", {"PX": 1.0})
    mdl.num_modes = 2

    d = _run(mdl)
    periods = d["modal"]["periods"]
    assert len(periods) == 2
    for Tp in periods:
        assert Tp == pytest.approx(T, rel=1e-3)   # 0.1%

    part = d["modal"]["participation"]
    # each of the two degenerate modes is purely translational; together they
    # capture 100% of the mass in each direction
    for p in part:
        assert p["ux"] + p["uy"] == pytest.approx(1.0, abs=1e-6)
    assert sum(p["ux"] for p in part) == pytest.approx(1.0, abs=1e-6)
    assert sum(p["uy"] for p in part) == pytest.approx(1.0, abs=1e-6)


# --------------------------------------------------------------------------- #
# f. Combo superposition
# --------------------------------------------------------------------------- #
def test_combo_superposition():
    """Linear analysis: combo(1.2D + 1.6L) displacement == 1.2*D + 1.6*L."""
    mdl = quick_building(name="Small", bays_x=1, bays_y=1, stories=2)
    d = _run(mdl)

    combo = d["combos"]["1.2D + 1.6L"]["node_disp"]
    dead = d["cases"]["DEAD"]["node_disp"]
    live = d["cases"]["LIVE"]["node_disp"]

    assert set(combo) == set(dead) == set(live)
    for tag in combo:
        for i in range(6):
            expected = 1.2 * dead[tag][i] + 1.6 * live[tag][i]
            assert abs(combo[tag][i] - expected) <= 1e-10 + 1e-10 * abs(expected), (
                f"node {tag} dof {i}: combo={combo[tag][i]!r} expected={expected!r}")


# --------------------------------------------------------------------------- #
# g. Global equilibrium of the default quick_building
# --------------------------------------------------------------------------- #
def test_quick_building_global_equilibrium():
    """Default quick_building: base reactions equilibrate the applied loads.

    * DEAD: total base FZ == sum(w * L) over all loaded beams (reactions act
      opposite to the downward loads, i.e. upward positive).
    * EQX: base FX == -(sum of applied story forces); Story1 shear equals the
      total base shear.
    """
    mdl = quick_building()
    d = _run(mdl)

    # total applied dead load, computed from the model's own pattern
    dead_total = sum(u.w * mdl._member(u.member_uid).length
                     for u in mdl.patterns["DEAD"].member_udls)
    assert dead_total > 0.0
    assert d["cases"]["DEAD"]["base"]["FZ"] == pytest.approx(dead_total, rel=1e-6)

    # EQX lateral equilibrium
    eqx_total = sum(sf.fx for sf in mdl.patterns["EQX"].story_forces)
    assert eqx_total > 0.0
    base_fx = d["cases"]["EQX"]["base"]["FX"]
    assert base_fx == pytest.approx(-eqx_total, rel=1e-6)

    # bottom-story shear == total base shear
    story1 = d["cases"]["EQX"]["story"]["Story1"]
    assert abs(story1["shear_x"]) == pytest.approx(abs(base_fx), rel=1e-6)
