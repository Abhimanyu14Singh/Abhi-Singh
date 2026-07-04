"""Shell / wall / slab benchmarks for the v0.2 meshing + ShellMITC4 pipeline.

References used (each cited at the test that uses it):
  * membrane patch test          -- exact linear-field solution, delta = FL/EA
  * deep cantilever wall         -- Timoshenko beam, delta = PL^3/3EI + PL/(k G A)
  * SS square plate, UDL         -- Navier double series (Timoshenko &
                                    Woinowsky-Krieger, Theory of Plates and
                                    Shells, Table 8: alpha = 0.00406)
  * Cook's skewed membrane       -- Cook (1974) trapezoid benchmark; reference
                                    tip displacement 23.96 for E=1, nu=1/3,
                                    t=1, P=1 (value tabulated throughout the
                                    membrane-element literature, e.g. Bergan &
                                    Felippa 1985: 23.96)

Tolerance policy: quantities fixed by statics alone (total reactions vs
applied load) are asserted at 1e-8..1e-9 relative; FE-discretisation
quantities (deflections of meshed shells) get the published benchmark
tolerance PLUS a mesh-refinement convergence assertion, so a pass can never
come from a luckily-coarse mesh.  Expected discretisation errors were
calibrated beforehand with an independent bilinear-Q4 plane-stress FE
(scratch derive_shells.py): deep beam 12x2/24x4/48x8 -> -10.8%/-3.1%/-0.9%
vs Timoshenko; Cook 8/16/32 -> -7.9%/-2.2%/-0.6% vs 23.96.

Because the mesher's subdivision rule is internal, node coordinates are
DISCOVERED at runtime (probe run with corner anchors, read results["nodes"]);
the real run re-meshes identically (dedup tol 1e-6 per CONTRACT.md).
"""

import math

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
mesh_mod = pytest.importorskip("skyframe.core.mesh")

from _v02_utils import (          # noqa: E402
    E_CONC, model_mod, FrameSection, Material, NodalLoad, PointSupport,
    has_shell_api, has_member_load, has_releases,
    new_model, finish, run, find_node,
    add_gravity_udl, add_member_load,
    stations, check_x_grid, sign_fit, assert_station_match,
    discover_mesh_nodes, tributary_lengths,
)

pytestmark = pytest.mark.skipif(
    not has_shell_api(),
    reason="v0.2 shell API (ShellSection/ShellRegion/AreaLoad) not present yet")

TOL = 1e-6   # geometric node-matching tolerance (mesh dedup tol per contract)


def _add_wall(mdl, uid, corners, thickness, mesh_size, section="SH",
              material="CONC", kind="wall", behavior="shell"):
    if section not in mdl.shell_sections:
        mdl.shell_sections[section] = model_mod.ShellSection(
            name=section, material=material, thickness=thickness)
    mdl.shells.append(model_mod.ShellRegion(
        uid=uid, kind=kind, behavior=behavior, section=section,
        corners=[tuple(c) for c in corners], mesh_size=mesh_size,
        story="Story1"))


def _on(pred, nodes):
    sel = [p for p in nodes if pred(p)]
    assert sel, "node discovery returned no nodes for predicate"
    return sel


# --------------------------------------------------------------------------- #
# 1. membrane patch test: uniform tension on a 1 x 1 wall, 4 x 4 mesh
# --------------------------------------------------------------------------- #
def test_membrane_patch_uniform_tension():
    """1x1 m wall (x-z plane), t = 0.1, E = 1e7 kPa, nu = 0.25, meshed 4x4.
    Edge x=0: ux=uy=0 (rotations clamped too -- irrelevant for the membrane
    response); uz pinned at the single corner (0,0,0) so Poisson contraction
    stays free.  Edge x=1 carries a uniform tension F = 100 kN applied as
    consistent nodal loads (tributary edge length x traction).

    The exact solution is a homogeneous stress field sigma = F/(t*1)
    = 1000 kPa: ux = sigma x / E everywhere and uz = -nu sigma z / E.
    A bilinear membrane reproduces linear displacement fields EXACTLY (patch
    test), so the 1e-3 tolerance is pure slack for solver round-off -- any
    real failure means membrane locking or a load-lumping bug."""
    E, nu, t, F = 1e7, 0.25, 0.1, 100.0
    sigma = F / (t * 1.0)
    mdl = new_model("patch", [1.0], E=E, nu=nu, mat="MPATCH")
    _add_wall(mdl, "W1", [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)],
              thickness=t, mesh_size=0.25, material="MPATCH")

    nodes = discover_mesh_nodes(mdl, [(0, 0, 0), (0, 0, 1)])
    left = _on(lambda p: abs(p[0]) < TOL, nodes)
    right = sorted(_on(lambda p: abs(p[0] - 1.0) < TOL, nodes),
                   key=lambda p: p[2])
    assert len(right) == 5, f"4x4 mesh should give 5 edge nodes, got {len(right)}"

    for p in left:
        rz = 1 if abs(p[2]) < TOL else 0        # uz only at the bottom corner
        mdl.supports.append(PointSupport(p, (1, 1, rz, 1, 1, 1)))
    trib = tributary_lengths([p[2] for p in right])
    pat = mdl.pattern("T", "other")
    for p, a in zip(right, trib):
        pat.nodal_loads.append(NodalLoad(p, fx=F * a / 1.0))
    mdl.add_case("T", {"T": 1.0})
    finish(mdl, (1, 0, 1))

    d = run(mdl)
    case = d["cases"]["T"]
    delta = sigma * 1.0 / E                     # = F L / (E A) = 1e-4 m
    for p in right:
        ux = case["node_disp"][find_node(d, p)][0]
        assert ux == pytest.approx(delta, rel=1e-3), (
            f"loaded-edge node {p}: ux={ux!r}, expected FL/EA={delta!r}")
    # Poisson contraction across the loaded edge: uz(top)-uz(bottom) = -nu*sigma/E
    uz_top = case["node_disp"][find_node(d, (1, 0, 1))][2]
    uz_bot = case["node_disp"][find_node(d, (1, 0, 0))][2]
    assert uz_top - uz_bot == pytest.approx(-nu * sigma / E, rel=1e-3)
    # equilibrium: reactions balance the applied tension exactly
    assert case["base"]["FX"] == pytest.approx(-F, rel=1e-9)


# --------------------------------------------------------------------------- #
# 2. deep cantilever wall vs Timoshenko beam + mesh convergence
# --------------------------------------------------------------------------- #
def test_cantilever_deep_beam_wall():
    """Wall 6 m long x 1 m tall (x-z plane), t = 0.15, clamped at x=0, total
    in-plane tip load P = 100 kN (global -Z) as consistent nodal loads on the
    x=6 edge.  Timoshenko cantilever (kappa = 5/6 rectangular):
        delta = PL^3/3EI + PL/(kappa G A),  I = t h^3/12,  A = t h
              = 2.3501e-2 m for E=25e6 kPa, nu=0.2.
    Independent bilinear-Q4 calibration (derive_shells.py) puts the
    48x8-mesh discretisation error at -0.9% (and MITC4's membrane is no
    worse), so 3% at mesh_size 0.125 is honest; the same calibration gives
    -10.8%/-3.1%/-0.9% at 12x2/24x4/48x8, so the error must fall
    monotonically under refinement."""
    E, nu, t, P = E_CONC, 0.2, 0.15, 100.0
    Lx, h = 6.0, 1.0
    I = t * h**3 / 12
    A = t * h
    G = E / (2 * (1 + nu))
    d_ref = P * Lx**3 / (3 * E * I) + P * Lx / ((5.0 / 6.0) * G * A)

    def solve(mesh_size):
        mdl = new_model("deepbeam", [h], E=E, nu=nu)
        _add_wall(mdl, "W1", [(0, 0, 0), (Lx, 0, 0), (Lx, 0, h), (0, 0, h)],
                  thickness=t, mesh_size=mesh_size)
        nodes = discover_mesh_nodes(mdl, [(0, 0, 0), (0, 0, h)])
        left = _on(lambda p: abs(p[0]) < TOL, nodes)
        right = sorted(_on(lambda p: abs(p[0] - Lx) < TOL, nodes),
                       key=lambda p: p[2])
        for p in left:
            mdl.supports.append(PointSupport(p, (1, 1, 1, 1, 1, 1)))
        trib = tributary_lengths([p[2] for p in right])
        pat = mdl.pattern("P", "other")
        for p, a in zip(right, trib):
            pat.nodal_loads.append(NodalLoad(p, fz=-P * a / h))
        mdl.add_case("P", {"P": 1.0})
        finish(mdl, (Lx, 0, h))
        d = run(mdl)
        case = d["cases"]["P"]
        # equilibrium is exact at any mesh
        assert case["base"]["FZ"] == pytest.approx(P, rel=1e-9)
        tips = [case["node_disp"][find_node(d, p)][2] for p in right]
        return -float(np.mean(tips))            # downward deflection, positive

    deltas = [solve(ms) for ms in (0.5, 0.25, 0.125)]    # 12x2 / 24x4 / 48x8
    errs = [abs(dd - d_ref) / d_ref for dd in deltas]
    assert errs[0] > errs[1] > errs[2], (
        f"no monotone convergence: deltas={deltas}, ref={d_ref}, errs={errs}")
    assert errs[2] < 0.03, (
        f"48x8 mesh: delta={deltas[2]:.6e} vs Timoshenko {d_ref:.6e} "
        f"({errs[2]*100:.2f}% > 3%)")


# --------------------------------------------------------------------------- #
# 3. simply supported square plate under UDL vs Navier series
# --------------------------------------------------------------------------- #
def test_simply_supported_plate_udl_navier():
    """Square slab a = b = 4 m, t = 0.12, q = 10 kPa (AreaLoad), boundary
    nodes restrained in uz only ("soft" simple support; in-plane rigid-body
    motion pinned at two corners).  Navier double series (Timoshenko &
    W-K eq. 133; alpha = 0.00406):
        w_c = (16 q / pi^6 D) sum_{m,n odd} sin(m pi/2) sin(n pi/2)
                                / (m n ((m/a)^2 + (n/b)^2)^2)
    computed here to m,n <= 99 (converges to ~1e-10 of itself).  The series
    is the HARD simple-support (thin/Kirchhoff) solution; uz-only supports
    converge to the SOFT-SS Mindlin plate, which for a/t = 33 sits a
    measured ~1.6% ABOVE the hard-SS value (deltas observed: 2.7900 /
    2.8076 / 2.8186 e-3 rising toward the soft limit vs Navier 2.7732e-3).
    Hence three honest assertions: (a) 2% acceptance vs Navier at 16x16
    (spec); (b) convergence as a CAUCHY criterion -- successive refinement
    differences must shrink -- which is the correct test when the continuum
    limit differs slightly from the reference; (c) bracketing -- the
    (near-)converged soft-SS deflection may not undershoot hard-SS."""
    E, nu, t, q, a = E_CONC, 0.2, 0.12, 10.0, 4.0
    D = E * t**3 / (12 * (1 - nu**2))
    s = 0.0
    for m in range(1, 100, 2):
        for n in range(1, 100, 2):
            sg = math.sin(m * math.pi / 2) * math.sin(n * math.pi / 2)
            s += sg / (m * n * ((m / a) ** 2 + (n / a) ** 2) ** 2)
    w_ref = 16 * q / (math.pi ** 6 * D) * s          # 2.7732e-3 m
    assert w_ref * D / (q * a**4) == pytest.approx(0.00406, rel=2e-3)

    zs = 1.0                                          # slab at story elevation

    def solve(mesh_size):
        mdl = new_model("plate", [zs], E=E, nu=nu)
        _add_wall(mdl, "S1",
                  [(0, 0, zs), (a, 0, zs), (a, a, zs), (0, a, zs)],
                  thickness=t, mesh_size=mesh_size, kind="slab")
        corners = [(0, 0, zs), (a, 0, zs), (a, a, zs), (0, a, zs)]
        nodes = discover_mesh_nodes(mdl, corners)
        def on_boundary(p):
            return (abs(p[0]) < TOL or abs(p[0] - a) < TOL
                    or abs(p[1]) < TOL or abs(p[1] - a) < TOL)
        for p in _on(on_boundary, nodes):
            if abs(p[0]) < TOL and abs(p[1]) < TOL:
                r = (1, 1, 1, 0, 0, 0)     # pin membrane ux,uy here
            elif abs(p[0] - a) < TOL and abs(p[1]) < TOL:
                r = (0, 1, 1, 0, 0, 0)     # + uy: blocks in-plane rotation
            else:
                r = (0, 0, 1, 0, 0, 0)     # soft simple support: uz only
            mdl.supports.append(PointSupport(p, r))
        mdl.pattern("Q", "dead").area_loads.append(
            model_mod.AreaLoad(region_uid="S1", q=q))
        mdl.add_case("Q", {"Q": 1.0})
        finish(mdl, (a / 2, a / 2, zs))
        d = run(mdl)
        case = d["cases"]["Q"]
        # consistent area load must be conserved exactly
        assert case["base"]["FZ"] == pytest.approx(q * a * a, rel=1e-9)
        ctr = find_node(d, (a / 2, a / 2, zs))
        return -case["node_disp"][ctr][2]            # downward positive

    deltas = [solve(ms) for ms in (a / 8, a / 12, a / 16)]
    # (b) Cauchy convergence of the FE sequence under refinement
    assert abs(deltas[2] - deltas[1]) < abs(deltas[1] - deltas[0]), (
        f"plate not mesh-converging: deltas={deltas}")
    # (a) benchmark acceptance vs the Navier series
    err = abs(deltas[2] - w_ref) / w_ref
    assert err < 0.02, (
        f"16x16 mesh: w_c={deltas[2]:.6e} vs Navier {w_ref:.6e} "
        f"({err*100:.2f}% > 2%)")
    # (c) soft SS is more flexible than hard SS: no undershoot (0.1% slack
    # for residual discretisation stiffness at 16x16)
    assert deltas[2] > w_ref * 0.999, (
        f"soft-SS plate deflection {deltas[2]:.6e} undershoots the hard-SS "
        f"Navier value {w_ref:.6e}")


# --------------------------------------------------------------------------- #
# 4. Cook's skewed membrane (published benchmark)
# --------------------------------------------------------------------------- #
def test_cooks_skewed_membrane():
    """Cook (1974) trapezoidal cantilever membrane: corners (0,0), (48,44),
    (48,60), (0,44); E = 1, nu = 1/3, t = 1; left edge clamped; unit shear
    P = 1 distributed uniformly along the right edge (length 16); reported
    quantity = vertical displacement of the loaded-edge midpoint (48, 52).
    Reference: 23.96 (Cook's benchmark; tabulated e.g. in Bergan & Felippa,
    CMAME 50 (1985), best-known value 23.96).

    Built as a WALL in the x-z plane (2D y -> global z).  Loads are
    consistent (tributary-length) nodal forces; the midpoint displacement is
    linearly interpolated between the two edge nodes bracketing z = 52,
    which is exact for the FE trace on that edge.  Q4 calibration errors
    (derive_shells.py): -7.9% / -2.2% / -0.6% at 8/16/32 divisions of the
    loaded edge -> assert monotone convergence and 2% at the finest mesh."""
    Eref, nuref, t, P = 1.0, 1.0 / 3.0, 1.0, 1.0
    corners = [(0, 0, 0), (48, 0, 44), (48, 0, 60), (0, 0, 44)]

    def solve(mesh_size):
        mdl = new_model("cook", [60.0], E=Eref, nu=nuref, mat="CMAT")
        _add_wall(mdl, "W1", corners, thickness=t, mesh_size=mesh_size,
                  material="CMAT")
        nodes = discover_mesh_nodes(mdl, [(0, 0, 0), (0, 0, 44)])
        left = _on(lambda p: abs(p[0]) < TOL, nodes)
        right = sorted(_on(lambda p: abs(p[0] - 48.0) < TOL, nodes),
                       key=lambda p: p[2])
        for p in left:
            mdl.supports.append(PointSupport(p, (1, 1, 1, 1, 1, 1)))
        zc = [p[2] for p in right]
        trib = tributary_lengths(zc)
        assert sum(trib) == pytest.approx(16.0, rel=1e-9)   # edge length
        pat = mdl.pattern("P", "other")
        for p, aa in zip(right, trib):
            pat.nodal_loads.append(NodalLoad(p, fz=P * aa / 16.0))
        mdl.add_case("P", {"P": 1.0})
        finish(mdl, (48, 0, 60))
        d = run(mdl)
        case = d["cases"]["P"]
        assert case["base"]["FZ"] == pytest.approx(-P, rel=1e-9)
        uz = [case["node_disp"][find_node(d, p)][2] for p in right]
        # interpolate the FE edge trace at z = 52 (midpoint of loaded edge)
        return float(np.interp(52.0, zc, uz))

    ref = 23.96
    vals = [solve(ms) for ms in (2.0, 1.0, 0.5)]    # ~8 / 16 / 32 divisions
    errs = [abs(v - ref) / ref for v in vals]
    assert errs[0] >= errs[1] - 1e-3 and errs[1] >= errs[2] - 1e-3, (
        f"Cook membrane not converging: vals={vals}, errs={errs}")
    assert errs[2] < 0.02, (
        f"finest mesh: v_mid={vals[2]:.4f} vs published 23.96 "
        f"({errs[2]*100:.2f}% > 2%)")


# --------------------------------------------------------------------------- #
# 5. one-story shear wall between boundary columns
# --------------------------------------------------------------------------- #
def test_shear_wall_with_boundary_columns():
    """3 x 3 m wall, t = 0.2, between two 0.25 m columns with a top beam,
    P = 100 kN at the top-left corner.

    (a) Equilibrium: sum FX of reactions = -P to 1e-8 (exact statics).
    (b) Order of magnitude: top drift vs the composite flexure+shear
        cantilever idealisation (plane sections, columns as chords):
          I_comp = t Lw^3/12 + 2 (A_col d^2 + I_col),  d = Lw/2
          delta  = P H^3 / 3 E I_comp + P H / (kappa G A_wall), kappa = 5/6
        The idealisation itself is only good to O(10%) for a 1:1 squat wall
        with a corner-applied load (independent Q4 calibration of the wall
        WITHOUT columns matched the formula to 0.5%, but the plane-sections
        assumption for the chords and the local load path are approximate),
        so the acceptance band is a deliberately honest 25%.
    (c) Convergence: the drift must be mesh-converging (Cauchy: successive
        refinement differences shrink)."""
    E, nu, t, P = E_CONC, 0.2, 0.2, 100.0
    H = Lw = 3.0
    csz = 0.25
    Acol, Icol = csz * csz, csz**4 / 12
    I_comp = t * Lw**3 / 12 + 2 * (Acol * (Lw / 2) ** 2 + Icol)
    G = E / (2 * (1 + nu))
    d_ideal = (P * H**3 / (3 * E * I_comp)
               + P * H / ((5.0 / 6.0) * G * (t * Lw)))

    def solve(mesh_size):
        mdl = new_model("shearwall", [H], E=E, nu=nu)
        mdl.add_section(FrameSection.rectangular("COL", "CONC", csz, csz))
        mdl.add_section(FrameSection.rectangular("BM", "CONC", csz, 0.4))
        mdl.add_member("column", "COL", (0, 0, 0), (0, 0, H), story="Story1", uid="CL")
        mdl.add_member("column", "COL", (Lw, 0, 0), (Lw, 0, H), story="Story1", uid="CR")
        mdl.add_member("beam", "BM", (0, 0, H), (Lw, 0, H), story="Story1", uid="TB")
        _add_wall(mdl, "W1", [(0, 0, 0), (Lw, 0, 0), (Lw, 0, H), (0, 0, H)],
                  thickness=t, mesh_size=mesh_size)
        nodes = discover_mesh_nodes(mdl, [(0, 0, 0), (Lw, 0, 0)])
        for p in _on(lambda p: abs(p[2]) < TOL, nodes):   # whole bottom edge
            mdl.supports.append(PointSupport(p, (1, 1, 1, 1, 1, 1)))
        mdl.pattern("EQ", "quake").nodal_loads.append(
            NodalLoad((0, 0, H), fx=P))
        mdl.add_case("EQ", {"EQ": 1.0})
        finish(mdl, (0, 0, H))
        d = run(mdl)
        case = d["cases"]["EQ"]
        assert case["base"]["FX"] == pytest.approx(-P, rel=1e-8)
        # drift = mean top-corner ux (top beam ties the two corners)
        u1 = case["node_disp"][find_node(d, (0, 0, H))][0]
        u2 = case["node_disp"][find_node(d, (Lw, 0, H))][0]
        return 0.5 * (u1 + u2)

    drifts = [solve(ms) for ms in (1.0, 0.5, 0.25)]
    assert abs(drifts[2] - drifts[1]) < abs(drifts[1] - drifts[0]), (
        f"wall drift not mesh-converging: {drifts}")
    assert drifts[2] == pytest.approx(d_ideal, rel=0.25), (
        f"drift {drifts[2]:.4e} vs flexure+shear idealisation {d_ideal:.4e} "
        f"(> 25%)")


# --------------------------------------------------------------------------- #
# 6a. load conservation: meshed shell slab on a beam grid
# --------------------------------------------------------------------------- #
def test_shell_slab_area_load_conserved():
    """6 x 4 m SHELL slab (meshed, t = 0.15) on 4 edge beams and 4 corner
    columns, q = 5 kPa.  Consistent nodal loads must conserve the applied
    area load EXACTLY (independent of mesh): total base FZ = q A = 120 kN to
    1e-9 relative (shell self-weight is ignored in v0.2 per contract, so
    nothing else contributes)."""
    q, ax, ay, zs = 5.0, 6.0, 4.0, 3.0
    mdl = new_model("slab-shell", [zs])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))
    mdl.add_section(FrameSection.rectangular("BM", "CONC", 0.3, 0.5))
    cpts = [(0, 0), (ax, 0), (ax, ay), (0, ay)]
    for i, (x, y) in enumerate(cpts):
        mdl.add_member("column", "COL", (x, y, 0), (x, y, zs),
                       story="Story1", uid=f"C{i+1}")
        mdl.supports.append(PointSupport((x, y, 0), (1, 1, 1, 1, 1, 1)))
    for i in range(4):
        x1, y1 = cpts[i]
        x2, y2 = cpts[(i + 1) % 4]
        mdl.add_member("beam", "BM", (x1, y1, zs), (x2, y2, zs),
                       story="Story1", uid=f"B{i+1}")
    _add_wall(mdl, "S1", [(0, 0, zs), (ax, 0, zs), (ax, ay, zs), (0, ay, zs)],
              thickness=0.15, mesh_size=0.5, kind="slab")
    mdl.pattern("Q", "dead").area_loads.append(
        model_mod.AreaLoad(region_uid="S1", q=q))
    mdl.add_case("Q", {"Q": 1.0})
    finish(mdl, (ax / 2, ay / 2, zs))

    d = run(mdl)
    case = d["cases"]["Q"]
    total = q * ax * ay
    assert case["base"]["FZ"] == pytest.approx(total, rel=1e-9)
    # the same total must be recoverable from the 4 column-base reactions
    ssum = sum(case["reactions"][find_node(d, (x, y, 0))][2] for x, y in cpts)
    assert ssum == pytest.approx(total, rel=1e-9)


# --------------------------------------------------------------------------- #
# 6b. membrane slab: two-way tributary distribution, closed-form diagrams
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not has_releases(), reason="needs FrameMember.releases")
def test_membrane_slab_two_way_tributary_distribution():
    """8 x 4 m MEMBRANE slab (no FE mesh), q = 5 kPa, on 4 edge beams with
    BOTH end moments released (-> each beam is simply supported between its
    corner columns, so its diagrams are closed-form) and 4 corner columns.

    45-degree two-way tributary areas (h = short/2 = 2 m):
      long beam  (L=8): trapezoid  0 -> 2q over [0,2], 2q on [2,6], -> 0;
                        total 12 q; R = 6q each end;
                        M(x) = 6qx - qx^3/6                  (x <= 2)
                             = 6qx - 2q(x - 4/3) - q(x-2)^2  (2 <= x <= 6)
      short beam (L=4): triangle 0 -> 2q -> 0; total 4q; R = 2q;
                        M(x) = 2q xx - q xx^3/6, xx = min(x, L-x)
      corner column axial = 6q + 2q = 8q = qA/4;  sum = qA = 160 kN.
    Every formula above was verified against an independent 1600-element
    beam FE (derive_tributary_wall.py, agreement ~1e-6).  The tributary
    conversion produces ordinary member loads, so stations are exact statics
    -> 1e-6 of the diagram maximum; totals to 1e-9."""
    q, ax, ay, zs = 5.0, 8.0, 4.0, 3.0
    mdl = new_model("slab-membrane", [zs])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))
    mdl.add_section(FrameSection.rectangular("BM", "CONC", 0.3, 0.5))
    cpts = [(0, 0), (ax, 0), (ax, ay), (0, ay)]
    for i, (x, y) in enumerate(cpts):
        mdl.add_member("column", "COL", (x, y, 0), (x, y, zs),
                       story="Story1", uid=f"C{i+1}")
        mdl.supports.append(PointSupport((x, y, 0), (1, 1, 1, 1, 1, 1)))
    names = ["LB1", "SB1", "LB2", "SB2"]        # long, short, long, short
    for i in range(4):
        x1, y1 = cpts[i]
        x2, y2 = cpts[(i + 1) % 4]
        m = mdl.add_member("beam", "BM", (x1, y1, zs), (x2, y2, zs),
                           story="Story1", uid=names[i])
        m.releases = "Mi,Mj"
    _add_wall(mdl, "S1", [(0, 0, zs), (ax, 0, zs), (ax, ay, zs), (0, ay, zs)],
              thickness=0.15, mesh_size=1.0, kind="slab", behavior="membrane")
    mdl.pattern("Q", "dead").area_loads.append(
        model_mod.AreaLoad(region_uid="S1", q=q))
    mdl.add_case("Q", {"Q": 1.0})
    finish(mdl, (0, 0, zs))

    d = run(mdl)
    case = d["cases"]["Q"]
    total = q * ax * ay
    assert case["base"]["FZ"] == pytest.approx(total, rel=1e-9)
    # released beams make the vertical load path statically determinate:
    # every corner column carries exactly 6q + 2q = 8q
    for (x, y) in cpts:
        r = case["reactions"][find_node(d, (x, y, 0))][2]
        assert r == pytest.approx(8 * q, rel=1e-6)

    h = ay / 2.0    # 45-degree tributary height = 2 m

    def M_long(x):
        x = min(x, ax - x)                       # symmetric
        if x <= h:
            return 6 * q * x - q * x**3 / 6
        return 6 * q * x - 2 * q * (x - 2 * h / 3) - q * (x - h) ** 2

    def V_long(x):                               # V = R1 - integral(w)
        if x <= h:
            return 6 * q - q * x**2 / 2
        if x <= ax - h:
            return 6 * q - 2 * q * (x - h / 2)   # = 2q(ax/2 - x) at plateau
        xp = ax - x
        return -(6 * q - q * xp**2 / 2)

    def M_short(x):
        xx = min(x, ay - x)
        return 2 * q * xx - q * xx**3 / 6

    for uid in ("LB1", "LB2"):
        st = stations(d, "Q", uid)
        assert_station_match(st, "M3", M_long, ax, rel=1e-6, label=uid)
        assert_station_match(st, "V2", V_long, ax, rel=1e-6, label=uid)
        V = np.asarray(st["V2"], float)
        # total load carried by the beam = shear drop end to end = 12q
        assert abs(V[0] - V[10]) == pytest.approx(12 * q, rel=1e-6)
    for uid in ("SB1", "SB2"):
        st = stations(d, "Q", uid)
        assert_station_match(st, "M3", M_short, ay, rel=1e-6, label=uid)
        V = np.asarray(st["V2"], float)
        assert abs(V[0] - V[10]) == pytest.approx(4 * q, rel=1e-6)
        assert abs(np.asarray(st["M3"], float)[5]) == pytest.approx(
            2 * q * 2 - q * 8 / 6, rel=1e-6)     # peak = w0 L^2/12 = 8q/3... see M_short


# --------------------------------------------------------------------------- #
# 7. wall + frame compatibility: beam bonded to a wall's top edge
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not has_member_load(), reason="needs MemberLoad")
def test_wall_frame_compatibility_split_and_aggregation():
    """A 4 x 3 wall with a loaded beam along its top edge, plus an identical
    control beam on two columns away from the wall (same UDL).

    Per CONTRACT.md the top beam must be SPLIT at the wall's edge nodes and
    its results re-aggregated to the ORIGINAL uid.  Checks:
      * stations: 11 points spanning the FULL original length 0..4 m,
        strictly increasing, all six arrays finite (aggregation worked);
      * end-station values consistent with the aggregated member_forces
        vector (two reporting channels must tell one story);
      * composite action: the bonded beam's midspan deflection is far below
        the bare frame beam's (the wall carries the load in membrane);
      * control beam free-body: its two local end-force shear entries must
        combine to the total applied load wL (as {|sum|,|diff|} = {wL, ~0},
        which is convention-proof for a symmetric member), 1e-6;
      * global equilibrium: base FZ = both UDL totals, exact (1e-9).
    NOTE the bonded beam exchanges vertical force with the wall at every
    interface node, so a naive end-force free body does NOT close for it --
    that identity is only asserted on the control beam (documented on
    purpose; asserting it on the bonded beam would be wrong mechanics)."""
    E, t, w = E_CONC, 0.2, 20.0
    Lw, H = 4.0, 3.0
    mdl = new_model("wallframe", [H], E=E)
    mdl.add_section(FrameSection.rectangular("BM", "CONC", 0.3, 0.5))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.3, 0.3))
    mdl.add_member("beam", "BM", (0, 0, H), (Lw, 0, H), story="Story1", uid="TB")
    # control frame in the y = 3 plane
    mdl.add_member("column", "COL", (0, 3, 0), (0, 3, H), story="Story1", uid="CC1")
    mdl.add_member("column", "COL", (Lw, 3, 0), (Lw, 3, H), story="Story1", uid="CC2")
    mdl.add_member("beam", "BM", (0, 3, H), (Lw, 3, H), story="Story1", uid="CB")
    mdl.supports.append(PointSupport((0, 3, 0), (1, 1, 1, 1, 1, 1)))
    mdl.supports.append(PointSupport((Lw, 3, 0), (1, 1, 1, 1, 1, 1)))
    _add_wall(mdl, "W1", [(0, 0, 0), (Lw, 0, 0), (Lw, 0, H), (0, 0, H)],
              thickness=t, mesh_size=0.5)
    nodes = discover_mesh_nodes(mdl, [(0, 0, 0), (Lw, 0, 0)])
    for p in _on(lambda p: abs(p[2]) < TOL and abs(p[1]) < TOL, nodes):
        mdl.supports.append(PointSupport(p, (1, 1, 1, 1, 1, 1)))
    add_member_load(mdl, "D", member_uid="TB", kind="udl", w=w)
    add_member_load(mdl, "D", member_uid="CB", kind="udl", w=w)
    mdl.add_case("D", {"D": 1.0})
    finish(mdl, (Lw / 2, 0, H))

    d = run(mdl)
    case = d["cases"]["D"]

    # global equilibrium, exact
    assert case["base"]["FZ"] == pytest.approx(2 * w * Lw, rel=1e-9)

    # --- aggregation of the bonded beam ---------------------------------
    st = stations(d, "D", "TB")
    check_x_grid(st, Lw)                        # 11 stations over ORIGINAL span
    for key in ("N", "V2", "V3", "T", "M2", "M3"):
        arr = np.asarray(st[key], float)
        assert arr.shape == (11,) and np.all(np.isfinite(arr)), (
            f"station array {key} malformed for split member: {arr}")
    mf = case["member_forces"]["TB"]
    assert len(mf) == 12
    # end stations vs aggregated end-force vector (Mz_i, Mz_j, Vy_i, Vy_j)
    M3 = np.asarray(st["M3"], float)
    V2 = np.asarray(st["V2"], float)
    scale_m = max(np.max(np.abs(M3)), 1e-9)
    scale_v = max(np.max(np.abs(V2)), 1e-9)
    assert abs(M3[0]) == pytest.approx(abs(mf[5]), abs=1e-6 * scale_m)
    assert abs(M3[10]) == pytest.approx(abs(mf[11]), abs=1e-6 * scale_m)
    assert abs(V2[0]) == pytest.approx(abs(mf[1]), abs=1e-6 * scale_v)
    assert abs(V2[10]) == pytest.approx(abs(mf[7]), abs=1e-6 * scale_v)

    # --- composite action: wall carries the beam ------------------------
    # the bonded beam's midspan node exists because the wall mesh split it;
    # compare against the FIXED-FIXED closed form wL^4/384EI, which is the
    # *stiffest* possible bare-beam idealisation (frame beam lies between
    # SS and fixed-fixed) -- the wall-backed beam must beat even that by 5x
    Ib = 0.3 * 0.5**3 / 12
    d_ff = w * Lw**4 / (384 * E_CONC * Ib)
    uz_tb = case["node_disp"][find_node(d, (Lw / 2, 0, H))][2]
    assert abs(uz_tb) < d_ff / 5.0, (
        f"beam on wall deflects {uz_tb!r}, not much less than the bare "
        f"fixed-fixed closed form {d_ff!r}")

    # --- control beam free body (not bonded to anything) ----------------
    mfc = case["member_forces"]["CB"]
    combo = sorted([abs(mfc[1] + mfc[7]), abs(mfc[1] - mfc[7])])
    assert combo[1] == pytest.approx(w * Lw, rel=1e-6)
    assert combo[0] < 1e-6 * w * Lw
