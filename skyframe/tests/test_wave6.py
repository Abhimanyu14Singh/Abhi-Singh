"""Wave 6 backend tests: wall/slab openings, nonlinear static pushover,
the semi-rigid/none diaphragm option, and link elements (v0.5).

Same rigor as test_phase4/test_phase5: every expected number is derived in
the test from first principles.

Openings:
  * a 4x4-element wall with a central 2x2-cell opening meshes to exactly
    12 quads with no orphan nodes; opening bounds off the mesh lines snap
    to the nearest line (same 12 quads); openings smaller than one element
    warn and are skipped;
  * area-load conservation is EXACT (1e-9): base FZ = q * (A_gross -
    A_open) for meshed shells (omitted elements' tributary excluded) and
    for membrane slabs (uniform reduction by the opening area ratio, with
    the documented approximation warning);
  * a wall with an opening is strictly more flexible than the solid wall
    under the same lateral load, while equilibrium stays exact.

Pushover (stiff-hinge idealization, documented in CONTRACT v0.5):
  hinge spring k_theta = n*6EI/L with n = 10, Steel01 hardening ratio
  b = h/(n+1-h*n).  For a cantilever column with a base hinge the ENTIRE
  capacity curve is closed-form bilinear:
    k_el = 1/(L^3/3EI + L^2/k_theta),   V_y = My/L,
    k_pl = 1/(L^3/3EI + L^2/(b*k_theta)),
  and the engine curve must match POINTWISE at 1e-6 (initial slope,
  plateau within 1% of My/L, transition at V = My/L all follow).  The
  final hinge rotation matches My/k_theta + (V*L - My)/(b*k_theta).
  A two-column frame with different My values shows sequential yielding:
  three slope regimes 2*k_el, k_el + k_pl, 2*k_pl (asserted well inside
  each regime at 1e-6, far tighter than the 2% requirement).

Diaphragm option:
  * a portal frame WITHOUT slabs: "none" vs "rigid" changes the modal set
    (the antisymmetric transverse mode differs by ~25%);
  * "none" + a stiff meshed slab (t = 0.4) reproduces the rigid-diaphragm
    T1 within 10% (convergent semi-rigid behavior — the slab IS the
    diaphragm);
  * story results still report: story ux == mean of the story nodes'
    ux when there is no master (1e-12).

Links:
  * two springs in series: u = P*(1/k1 + 1/k2) at 1e-9 and reactions
    balance;
  * a link in parallel with a cantilever column shifts the period to
    T = 2*pi*sqrt(m/(3EI/L^3 + kx)) at 1e-6.

Units per CONTRACT.md: kN, m, tonne, s; E in kPa.
"""

import math

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine
HINGE_N = engine_mod.HINGE_STIFFNESS_FACTOR
PUSHOVER_STEP_CAP = engine_mod.PUSHOVER_STEP_CAP

from skyframe.core.mesh import mesh_model
from skyframe.core.model import (
    AreaLoad,
    BuildingModel,
    FrameSection,
    Material,
    NodalLoad,
    NodalMass,
    Opening,
    PointSupport,
    ShellRegion,
    ShellSection,
)

E_CONC = 25_000_000.0  # kPa


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _run(model) -> dict:
    return OpenSeesEngine(model).run().to_dict()


def _find_node(results: dict, pt, tol=1e-6):
    for tag, xyz in results["nodes"].items():
        if all(abs(a - b) < tol for a, b in zip(xyz, pt)):
            return tag
    raise AssertionError(f"no FE node found at {pt}")


def _new_model(name: str, story_heights) -> BuildingModel:
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.set_stories(list(story_heights))
    return mdl


def _mesh_points(model):
    return mesh_model(model).points


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# --------------------------------------------------------------------------- #
# 1. openings — meshing
# --------------------------------------------------------------------------- #
def _wall_with_opening(openings, mesh=1.0, W=4.0, H=4.0):
    mdl = _new_model("wallop", [H])
    mdl.add_shell_section(ShellSection("SH", "CONC", 0.2))
    mdl.shells.append(ShellRegion(
        "W1", "wall", "shell", "SH",
        [(0, 0, 0), (W, 0, 0), (W, 0, H), (0, 0, H)],
        mesh_size=mesh, story="Story1", openings=list(openings)))
    return mdl


def test_opening_mesh_quads_and_no_orphan_nodes():
    """4x4-element wall, central 2x2-cell opening: 16 - 4 = 12 quads, and
    the interior node used only by the omitted cells is dropped (24 points
    instead of 25 — no orphan nodes)."""
    mm = mesh_model(_wall_with_opening([Opening(0.25, 0.25, 0.75, 0.75)]))
    assert len(mm.quads) == 12
    used = {n for q in mm.quads for n in q.nodes}
    assert used == set(range(len(mm.points)))       # no orphans
    assert len(mm.points) == 24                     # 25-node grid minus center
    # the omitted cells are exactly the central 2x2 block: no quad centroid
    # falls inside the opening rectangle (1,1)-(3,3) in the x-z plane
    for q in mm.quads:
        cx = np.mean([mm.points[n][0] for n in q.nodes])
        cz = np.mean([mm.points[n][2] for n in q.nodes])
        assert not (1.0 < cx < 3.0 and 1.0 < cz < 3.0)
    # tributary areas sum to the exact net area
    assert sum(mm.region_trib["W1"].values()) == pytest.approx(
        16.0 - 4.0, rel=1e-12)


def test_opening_bounds_snap_to_nearest_mesh_line():
    """Bounds off the mesh lines snap to the nearest line: (0.2, 0.8) x
    (0.2, 0.7) on a 4x4 mesh snaps to lines 1..3 both ways — the same 12
    quads as the exact 0.25/0.75 opening."""
    exact = mesh_model(_wall_with_opening([Opening(0.25, 0.25, 0.75, 0.75)]))
    snapped = mesh_model(_wall_with_opening([Opening(0.2, 0.2, 0.8, 0.7)]))
    assert len(snapped.quads) == len(exact.quads) == 12
    assert ({tuple(q.nodes) for q in snapped.quads}
            == {tuple(q.nodes) for q in exact.quads})


def test_opening_smaller_than_element_warns_and_skips():
    mdl = _wall_with_opening([Opening(0.26, 0.2, 0.30, 0.8)])  # < 1 cell in u
    with pytest.warns(UserWarning, match="smaller than one"):
        mm = mesh_model(mdl)
    assert len(mm.quads) == 16                      # full mesh kept
    assert len(mm.points) == 25


def test_opening_validation_and_roundtrip():
    mdl = _new_model("val", [3.0])
    mdl.add_shell_section(ShellSection("SH", "CONC", 0.2))
    corners = [(0, 0, 0), (4, 0, 0), (4, 0, 3), (0, 0, 3)]
    with pytest.raises(ValueError, match="bounds"):
        mdl.add_shell("wall", "shell", "SH", corners,
                      openings=[Opening(0.5, 0.2, 0.4, 0.8)])   # u1 < u0
    with pytest.raises(ValueError, match="bounds"):
        mdl.add_shell("wall", "shell", "SH", corners,
                      openings=[Opening(0.0, 0.2, 1.2, 0.8)])   # u1 > 1
    with pytest.raises(ValueError, match="overlap"):
        mdl.add_shell("wall", "shell", "SH", corners,
                      openings=[Opening(0.1, 0.1, 0.6, 0.6),
                                Opening(0.5, 0.5, 0.9, 0.9)])
    region = mdl.add_shell("wall", "shell", "SH", corners,
                           openings=[Opening(0.25, 0.25, 0.5, 0.75)])
    # exact geometric areas for a rectangular region
    assert region.area == pytest.approx(12.0, rel=1e-12)
    assert region.opening_area == pytest.approx(0.25 * 4.0 * 0.5 * 3.0,
                                                rel=1e-12)
    assert region.net_area == pytest.approx(12.0 - 1.5, rel=1e-12)
    # round trip preserves the openings exactly
    d1 = mdl.to_dict()
    back = BuildingModel.from_dict(d1)
    op = back.shells[0].openings[0]
    assert (op.u0, op.v0, op.u1, op.v1) == (0.25, 0.25, 0.5, 0.75)
    assert back.to_dict() == d1
    # pre-v0.5 dicts (no "openings" key) load with no openings
    d1["shells"][0].pop("openings")
    assert BuildingModel.from_dict(d1).shells[0].openings == []


# --------------------------------------------------------------------------- #
# 1b. openings — load conservation and flexibility
# --------------------------------------------------------------------------- #
def test_opening_shell_area_load_conservation_exact():
    """Meshed slab 4x4 with a central 2x2 opening, q = 5 kPa: total base
    FZ = q * (A_gross - A_open) = 5 * 12 exactly (1e-9)."""
    q = 5.0
    mdl = _new_model("slabop", [3.0])
    mdl.add_shell_section(ShellSection("SH", "CONC", 0.15))
    mdl.shells.append(ShellRegion(
        "S1", "slab", "shell", "SH",
        [(0, 0, 3), (4, 0, 3), (4, 4, 3), (0, 4, 3)],
        mesh_size=1.0, story="Story1",
        openings=[Opening(0.25, 0.25, 0.75, 0.75)]))
    for p in _mesh_points(mdl):
        if abs(p[0]) < 1e-9 or abs(p[0] - 4.0) < 1e-9:
            mdl.supports.append(PointSupport(p, (1, 1, 1, 0, 0, 0)))
    mdl.pattern("Q", "other").area_loads.append(AreaLoad("S1", q))
    mdl.add_case("Q", {"Q": 1.0})
    mdl.num_modes = 0
    d = _run(mdl)
    assert d["cases"]["Q"]["base"]["FZ"] == pytest.approx(
        q * (16.0 - 4.0), rel=1e-9)
    # the model-level mass bookkeeping uses the same net area
    mdl.mass_source = {"Q": 1.0}
    from skyframe.core.model import G_ACCEL
    assert mdl.compute_story_masses()["Story1"] == pytest.approx(
        q * 12.0 / G_ACCEL, rel=1e-12)


def test_opening_membrane_uniform_reduction_with_warning():
    """Membrane slab with an opening: distributed total drops by the
    opening area ratio uniformly (documented approximation + warning);
    conservation stays exact: base FZ = q * net area (1e-9)."""
    q, Lx, Ly = 5.0, 6.0, 4.0
    mdl = _new_model("memop", [3.0])
    mdl.add_shell_section(ShellSection("SH", "CONC", 0.15))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.3, 0.3))
    mdl.add_section(FrameSection.rectangular("BEAM", "CONC", 0.3, 0.5))
    pts = [(0, 0), (Lx, 0), (Lx, Ly), (0, Ly)]
    for i, (x, y) in enumerate(pts):
        mdl.add_member("column", "COL", (x, y, 0), (x, y, 3),
                       story="Story1", uid=f"C{i}")
    for i in range(4):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % 4]
        mdl.add_member("beam", "BEAM", (x0, y0, 3), (x1, y1, 3),
                       story="Story1", uid=f"B{i}")
    mdl.shells.append(ShellRegion(
        "S1", "slab", "membrane", "SH",
        [(0, 0, 3), (Lx, 0, 3), (Lx, Ly, 3), (0, Ly, 3)],
        story="Story1", openings=[Opening(0.25, 0.25, 0.75, 0.75)]))
    mdl.pattern("Q", "other").area_loads.append(AreaLoad("S1", q))
    mdl.add_case("Q", {"Q": 1.0})
    mdl.num_modes = 0
    a_net = Lx * Ly - (0.5 * Lx) * (0.5 * Ly)
    with pytest.warns(UserWarning, match="openings reduce"):
        d = _run(mdl)
    assert d["cases"]["Q"]["base"]["FZ"] == pytest.approx(q * a_net,
                                                          rel=1e-9)


def test_wall_with_opening_is_more_flexible():
    """4x4 wall, 0.5 m mesh, central 2x2 m opening vs solid: same lateral
    P at the top, equilibrium exact for both, drift strictly larger with
    the opening."""
    P, W, H = 50.0, 4.0, 4.0

    def drift(openings):
        mdl = _wall_with_opening(openings, mesh=0.5, W=W, H=H)
        pts = _mesh_points(mdl)
        top = [p for p in pts if abs(p[2] - H) < 1e-9]
        for p in pts:
            if abs(p[2]) < 1e-9:
                mdl.supports.append(PointSupport(p, (1, 1, 1, 1, 1, 1)))
        pat = mdl.pattern("P", "other")
        for p in top:
            pat.nodal_loads.append(NodalLoad(p, fx=P / len(top)))
        mdl.add_case("P", {"P": 1.0})
        mdl.num_modes = 0
        d = _run(mdl)
        case = d["cases"]["P"]
        assert case["base"]["FX"] == pytest.approx(-P, rel=1e-9)
        return float(np.mean([case["node_disp"][_find_node(d, p)][0]
                              for p in top]))

    u_solid = drift([])
    u_open = drift([Opening(0.25, 0.25, 0.75, 0.75)])
    assert u_open > u_solid * 1.05      # strictly (and clearly) softer


# --------------------------------------------------------------------------- #
# 2. pushover — closed-form capacity curves
# --------------------------------------------------------------------------- #
def _hinge_params(size=0.3, L=3.0, hardening=0.02):
    I = size ** 4 / 12.0
    EI = E_CONC * I
    kth = HINGE_N * 6.0 * EI / L
    b = hardening / (HINGE_N + 1.0 - hardening * HINGE_N)
    k_el = 1.0 / (L ** 3 / (3.0 * EI) + L ** 2 / kth)
    k_pl = 1.0 / (L ** 3 / (3.0 * EI) + L ** 2 / (b * kth))
    return EI, kth, b, k_el, k_pl


def test_pushover_cantilever_bilinear_closed_form():
    """Cantilever column with a base hinge: the whole capacity curve is
    bilinear with k_el = series(3EI/L^3, k_theta/L^2), yield at V = My/L,
    post-yield slope k_pl from the calibrated Steel01 b — matched
    POINTWISE at 1e-6.  Plateau within 1% of My/L, transition at My/L,
    final hinge rotation closed-form."""
    L, size, My, h = 3.0, 0.3, 100.0, 0.02
    EI, kth, b, k_el, k_pl = _hinge_params(size, L, h)
    mdl = _new_model("push1", [L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.pattern("G", "other").nodal_loads.append(
        NodalLoad((0, 0, L), fz=-50.0))          # exercises the gravity stage
    mdl.add_pushover_case("PX", "X", gravity={"G": 1.0},
                          target_drift=0.0075, steps=100,
                          hinges="column_base", My={"C1": My}, hardening=h)
    mdl.num_modes = 0
    po = OpenSeesEngine(mdl).run_pushover("PX")
    assert po.warnings == []
    assert len(po.roof_disp) == 100

    u = np.array(po.roof_disp)
    V = np.array(po.base_shear)
    du = 0.0075 * L / 100
    assert u[0] == pytest.approx(du, rel=1e-9)
    assert u[-1] == pytest.approx(0.0075 * L, rel=1e-9)
    assert po.roof_drift == pytest.approx(list(u / L), rel=1e-12)

    # closed-form bilinear reference, point by point
    V_y = My / L
    u_y = V_y / k_el
    V_hand = np.where(u <= u_y, k_el * u, V_y + k_pl * (u - u_y))
    assert np.max(np.abs(V - V_hand)) <= 1e-6 * V_y

    # required bounds from the spec: initial slope at 1e-6; plateau =
    # My/L (plus the small hardening excursion) within 1%
    assert V[0] / u[0] == pytest.approx(k_el, rel=1e-6)      # initial slope
    assert abs(V[-1] / V_y - 1.0) < 0.01
    assert V[-1] > V_y                                       # hardening > 0
    slope_end = (V[-1] - V[-2]) / (u[-1] - u[-2])
    assert slope_end == pytest.approx(k_pl, rel=1e-6)
    # bilinear transition at V = My/L: the curve crosses V_y exactly where
    # the hand curve does
    k_yield = int(np.searchsorted(u, u_y))
    assert V[k_yield - 1] < V_y < V[k_yield + 1]

    # final hinge rotation: elastic My/kth plus plastic excursion
    rot_hand = My / kth + (V[-1] * L - My) / (b * kth)
    assert po.hinge_rotations["C1"] == pytest.approx(rot_hand, rel=1e-6)


def test_pushover_two_columns_sequential_yielding():
    """Two columns (same section, different My) under a rigid diaphragm:
    three slope regimes 2k_el -> k_el + k_pl -> 2k_pl with yield events at
    u = My_i/(k_el*L); the engine curve matches the hand bilinear sum
    pointwise at 1e-6 and every regime slope at 1e-6 (spec bound: 2%)."""
    L, size, h = 3.0, 0.3, 0.02
    My1, My2 = 80.0, 120.0
    EI, kth, b, k_el, k_pl = _hinge_params(size, L, h)
    mdl = BuildingModel(name="push2")
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.set_stories([L])
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                   uid="C1")
    mdl.add_member("column", "COL", (6, 0, 0), (6, 0, L), story="Story1",
                   uid="C2")
    mdl.rigid_diaphragms = True                  # ties the two tops in ux
    mdl.add_pushover_case("PX", "X", target_drift=0.01, steps=150,
                          hinges="column_base",
                          My={"C1": My1, "C2": My2}, hardening=h)
    mdl.num_modes = 0
    po = OpenSeesEngine(mdl).run_pushover("PX")
    assert po.warnings == []

    u = np.array(po.roof_disp)
    V = np.array(po.base_shear)
    u_y1 = My1 / (k_el * L)
    u_y2 = My2 / (k_el * L)
    assert u[20] < u_y1 < u_y2 < u[-1]           # both events inside the run

    def col(uv, my):
        uy = my / (k_el * L)
        return np.where(uv <= uy, k_el * uv, my / L + k_pl * (uv - uy))

    V_hand = col(u, My1) + col(u, My2)
    assert np.max(np.abs(V - V_hand)) <= 1e-6 * (My1 / L)

    def slope(i, j):
        return (V[j] - V[i]) / (u[j] - u[i])

    # regime interiors: u_y1 = 74.67*du, u_y2 = 112*du with du = 2e-4
    assert slope(10, 60) == pytest.approx(2.0 * k_el, rel=1e-6)
    assert slope(84, 104) == pytest.approx(k_el + k_pl, rel=1e-6)
    assert slope(120, 148) == pytest.approx(2.0 * k_pl, rel=1e-6)
    # per-hinge final rotations, closed form: theta = My/kth +
    # (V_col*L - My)/(b*kth) with V_col from that column's bilinear curve
    for uid, my in (("C1", My1), ("C2", My2)):
        v_col = my / L + k_pl * (u[-1] - my / (k_el * L))
        rot_hand = my / kth + (v_col * L - my) / (b * kth)
        assert po.hinge_rotations[uid] == pytest.approx(rot_hand, rel=1e-6)
    assert po.hinge_rotations["C1"] > po.hinge_rotations["C2"]


def test_pushover_run_cap_validation_and_roundtrip():
    L, size = 3.0, 0.3
    mdl = _new_model("pcap", [L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.pattern("G", "other").nodal_loads.append(
        NodalLoad((0, 0, L), fz=-10.0))
    mdl.add_pushover_case("PX", "X", gravity={"G": 1.0},
                          target_drift=0.005, steps=20,
                          hinges="column_base", My={"C1": 100.0})
    mdl.num_modes = 0

    # run() carries the pushover results
    d = _run(mdl)
    po = d["pushover"]["PX"]
    assert len(po["roof_disp"]) == len(po["base_shear"]) == 20
    assert po["roof_drift"][-1] == pytest.approx(0.005, rel=1e-9)
    assert po["warnings"] == []
    assert "C1" in po["hinge_rotations"]

    # combined-step cap: run() skips with a top-level warning
    mdl.pushover_cases["PX"].steps = PUSHOVER_STEP_CAP + 1
    d2 = _run(mdl)
    assert d2["pushover"] == {}
    assert "pushover" in d2["warning"] and str(PUSHOVER_STEP_CAP) in d2["warning"]

    # serialization round-trip preserves every field
    mdl.add_pushover_case("PY", "Y", gravity={"G": 1.4}, target_drift=0.015,
                          steps=42, hinges="all_ends", My={"C1": 77.0},
                          default_My=55.0, hardening=0.03)
    back = BuildingModel.from_dict(mdl.to_dict())
    py = back.pushover_cases["PY"]
    assert (py.direction, py.target_drift, py.steps, py.hinges,
            py.default_My, py.hardening) == ("Y", 0.015, 42, "all_ends",
                                             55.0, 0.03)
    assert py.gravity == {"G": 1.4} and py.My == {"C1": 77.0}
    # pre-v0.5 dicts have no pushover_cases key
    d3 = mdl.to_dict()
    d3.pop("pushover_cases")
    assert BuildingModel.from_dict(d3).pushover_cases == {}

    # validation
    with pytest.raises(ValueError, match="direction"):
        mdl.add_pushover_case("B1", "Z", My={"C1": 10.0})
    with pytest.raises(ValueError, match="hinges"):
        mdl.add_pushover_case("B2", "X", hinges="bases", My={"C1": 10.0})
    with pytest.raises(ValueError, match="unknown member"):
        mdl.add_pushover_case("B3", "X", My={"NOPE": 10.0})
    with pytest.raises(ValueError, match="unknown pattern"):
        mdl.add_pushover_case("B4", "X", gravity={"NOPE": 1.0})
    with pytest.raises(ValueError, match="target_drift"):
        mdl.add_pushover_case("B5", "X", target_drift=0.0)
    with pytest.raises(ValueError, match="hardening"):
        mdl.add_pushover_case("B6", "X", hardening=1.0)
    with pytest.raises(ValueError, match="steps"):
        mdl.add_pushover_case("B7", "X", steps=0)
    with pytest.raises(ValueError, match="Unknown pushover"):
        OpenSeesEngine(mdl).run_pushover("NOPE")


# --------------------------------------------------------------------------- #
# 3. diaphragm option
# --------------------------------------------------------------------------- #
def _portal(diaphragm="rigid", story_diaphragm=None):
    """One-bay portal in the x-z plane; explicit story mass."""
    mdl = BuildingModel(name="portal")
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.3, 0.3))
    mdl.add_section(FrameSection.rectangular("BEAM", "CONC", 0.3, 0.5))
    mdl.set_stories([3.0])
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, 3), story="Story1",
                   uid="C1")
    mdl.add_member("column", "COL", (6, 0, 0), (6, 0, 3), story="Story1",
                   uid="C2")
    mdl.add_member("beam", "BEAM", (0, 0, 3), (6, 0, 3), story="Story1",
                   uid="B1")
    mdl.story_masses = {"Story1": 20.0}
    mdl.diaphragm = diaphragm
    if story_diaphragm:
        mdl.story_diaphragm = dict(story_diaphragm)
    mdl.num_modes = 3
    return mdl


def test_diaphragm_none_changes_modal_set():
    """Without slabs, "none" frees the floor from the rigid-body plan
    constraint: the antisymmetric transverse mode (beam bending in plan
    instead of the rigid-diaphragm torsion) differs by far more than the
    symmetric sway modes."""
    Tr = OpenSeesEngine(_portal("rigid")).run_modal().periods
    Tn = OpenSeesEngine(_portal("none")).run_modal().periods
    assert len(Tr) == len(Tn) == 3
    rel = [abs(a / b - 1.0) for a, b in zip(Tn, Tr)]
    assert max(rel) > 0.05                       # a mode genuinely differs
    # the fundamental X sway barely feels the diaphragm here (stiff beam
    # axially): a sanity check that the models are otherwise identical
    assert Tn[0] == pytest.approx(Tr[0], rel=1e-6)


def test_semi_rigid_stiff_slab_converges_to_rigid_diaphragm():
    """Semi-rigid = "none" + the slab modeled as a shell: with a stiff
    t = 0.4 slab the fundamental period matches the rigid-diaphragm frame
    within 10% (the slab's real membrane stiffness plays the diaphragm)."""
    def frame(slab, diaphragm):
        p = BuildingModel(name="sr")
        p.add_material(Material("CONC", E=E_CONC, nu=0.2))
        p.add_section(FrameSection.rectangular("COL", "CONC", 0.25, 0.25))
        p.add_section(FrameSection.rectangular("BEAM", "CONC", 0.3, 0.8))
        p.set_stories([3.0])
        pts = [(0, 0), (5, 0), (5, 5), (0, 5)]
        for i, (x, y) in enumerate(pts):
            p.add_member("column", "COL", (x, y, 0), (x, y, 3),
                         story="Story1", uid=f"C{i}")
        for i in range(4):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % 4]
            p.add_member("beam", "BEAM", (x0, y0, 3), (x1, y1, 3),
                         story="Story1", uid=f"B{i}")
        if slab:
            p.add_shell_section(ShellSection("SL", "CONC", 0.4))
            p.shells.append(ShellRegion(
                "S1", "slab", "shell", "SL",
                [(0, 0, 3), (5, 0, 3), (5, 5, 3), (0, 5, 3)],
                mesh_size=1.0, story="Story1"))
        p.diaphragm = diaphragm
        p.story_masses = {"Story1": 40.0}
        p.num_modes = 1
        return p

    T_rigid = OpenSeesEngine(frame(False, "rigid")).run_modal().periods[0]
    T_semi = OpenSeesEngine(frame(True, "none")).run_modal().periods[0]
    assert T_semi == pytest.approx(T_rigid, rel=0.10)


def test_diaphragm_none_story_results_from_node_mean():
    """Static lateral case with no master: reported story ux equals the
    mean of the story nodes' ux, and the story shear is still reported."""
    mdl = _portal("none")
    mdl.pattern("EQ", "quake").story_forces.append(
        __import__("skyframe.core.model", fromlist=["StoryForce"])
        .StoryForce("Story1", fx=10.0))
    mdl.add_case("EQ", {"EQ": 1.0})
    eng = OpenSeesEngine(mdl)
    r = eng.run_static("EQ")
    asm = eng._asm
    assert asm.masters == {}                     # really no diaphragm
    nodes = asm.story_nodes["Story1"]
    assert len(nodes) == 2
    mean_ux = sum(r.node_disp[t][0] for t in nodes) / len(nodes)
    assert r.story["Story1"]["ux"] == pytest.approx(mean_ux, rel=1e-12)
    assert r.story["Story1"]["shear_x"] == pytest.approx(10.0, rel=1e-12)
    assert r.base["FX"] == pytest.approx(-10.0, rel=1e-9)


def test_story_diaphragm_override_and_roundtrip():
    # per-story override wins over the global default
    T_none = OpenSeesEngine(_portal("none")).run_modal().periods
    T_over = OpenSeesEngine(
        _portal("rigid", story_diaphragm={"Story1": "none"})
    ).run_modal().periods
    assert T_over == pytest.approx(T_none, rel=1e-12)

    mdl = _portal("none", story_diaphragm={"Story1": "rigid"})
    assert mdl.effective_diaphragm("Story1") == "rigid"
    # legacy boolean still means "none everywhere" (absent overrides)
    legacy = _portal("rigid")
    legacy.rigid_diaphragms = False
    assert legacy.effective_diaphragm("Story1") == "none"
    # round trip; pre-v0.5 dicts default to the rigid global
    back = BuildingModel.from_dict(mdl.to_dict())
    assert back.diaphragm == "none"
    assert back.story_diaphragm == {"Story1": "rigid"}
    d = _portal().to_dict()
    d.pop("diaphragm")
    d.pop("story_diaphragm")
    old = BuildingModel.from_dict(d)
    assert old.diaphragm == "rigid" and old.story_diaphragm == {}
    # validation
    with pytest.raises(ValueError, match="diaphragm"):
        bad = _portal()
        bad.diaphragm = "semi"
        bad.validate()
    with pytest.raises(ValueError, match="unknown story"):
        bad = _portal()
        bad.story_diaphragm = {"Story9": "none"}
        bad.validate()


# --------------------------------------------------------------------------- #
# 4. link elements
# --------------------------------------------------------------------------- #
def test_link_series_springs_exact():
    """Two axial springs in series: u(top) = P (1/k1 + 1/k2), u(mid) =
    P/k1, reaction = -P — all at 1e-9."""
    k1, k2, P = 500.0, 800.0, 7.0
    mdl = _new_model("links", [1.0, 2.0])
    mdl.add_link((0, 0, 0), (0, 0, 1), [k1, 0, 0, 0, 0, 0], uid="L1")
    mdl.add_link((0, 0, 1), (0, 0, 2), [k2, 0, 0, 0, 0, 0], uid="L2")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.pattern("P", "other").nodal_loads.append(NodalLoad((0, 0, 2), fx=P))
    mdl.add_case("P", {"P": 1.0})
    mdl.num_modes = 0
    d = _run(mdl)
    case = d["cases"]["P"]
    u_top = case["node_disp"][_find_node(d, (0, 0, 2))][0]
    u_mid = case["node_disp"][_find_node(d, (0, 0, 1))][0]
    assert u_top == pytest.approx(P * (1 / k1 + 1 / k2), rel=1e-9)
    assert u_mid == pytest.approx(P / k1, rel=1e-9)
    assert case["base"]["FX"] == pytest.approx(-P, rel=1e-9)


def test_link_parallel_with_column_shifts_period():
    """A kx link across a cantilever column adds in parallel:
    T = 2 pi sqrt(m / (3EI/L^3 + kx)) at 1e-6."""
    m, L, size, kx = 10.0, 3.0, 0.3, 700.0
    I = size ** 4 / 12.0
    k_col = 3.0 * E_CONC * I / L ** 3
    mdl = _new_model("linkpar", [L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=m))
    mdl.add_link((0, 0, 0), (0, 0, L), [kx, 0, 0, 0, 0, 0], uid="LK")
    mdl.num_modes = 1
    T1 = OpenSeesEngine(mdl).run_modal().periods[0]
    assert T1 == pytest.approx(2.0 * math.pi * math.sqrt(m / (k_col + kx)),
                               rel=1e-6)
    # without the link the period is strictly longer
    mdl2 = _new_model("nolink", [L])
    mdl2.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl2.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                    uid="C1")
    mdl2.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl2.nodal_masses.append(NodalMass((0, 0, L), mx=m))
    mdl2.num_modes = 1
    T0 = OpenSeesEngine(mdl2).run_modal().periods[0]
    assert T0 == pytest.approx(2.0 * math.pi * math.sqrt(m / k_col),
                               rel=1e-6)
    assert T1 < T0


def test_link_validation_and_roundtrip():
    mdl = _new_model("lval", [1.0])
    with pytest.raises(ValueError, match="6 entries"):
        mdl.add_link((0, 0, 0), (0, 0, 1), [1.0, 2.0])
    with pytest.raises(ValueError, match=">= 0"):
        mdl.add_link((0, 0, 0), (0, 0, 1), [-1, 0, 0, 0, 0, 0])
    with pytest.raises(ValueError, match="at least one"):
        mdl.add_link((0, 0, 0), (0, 0, 1), [0, 0, 0, 0, 0, 0])
    mdl.add_link((0, 0, 0), (0, 0, 1), [1, 2, 3, 4, 5, 6], uid="LA")
    with pytest.raises(ValueError, match="Duplicate link uid"):
        mdl.add_link((0, 0, 0), (0, 0, 1), [1, 0, 0, 0, 0, 0], uid="LA")
    back = BuildingModel.from_dict(mdl.to_dict())
    assert len(back.links) == 1
    lk = back.links[0]
    assert lk.uid == "LA"
    assert lk.pi == (0.0, 0.0, 0.0) and lk.pj == (0.0, 0.0, 1.0)
    assert lk.stiffness == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    # pre-v0.5 dicts have no links key
    d = mdl.to_dict()
    d.pop("links")
    assert BuildingModel.from_dict(d).links == []


# --------------------------------------------------------------------------- #
# 5. full v0.5 round trip + API
# --------------------------------------------------------------------------- #
def test_v05_full_roundtrip_and_api(api_client):
    """A model exercising EVERY v0.5 field survives to_dict -> from_dict ->
    to_dict unchanged and round-trips through POST /api/model; /api/analyze
    returns the pushover block."""
    L = 3.0
    mdl = _new_model("V05", [L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.3, 0.3))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                   uid="C1")
    mdl.add_shell_section(ShellSection("SH", "CONC", 0.2))
    mdl.shells.append(ShellRegion(
        "W1", "wall", "shell", "SH",
        [(2, 0, 0), (6, 0, 0), (6, 0, 3), (2, 0, 3)],
        mesh_size=1.0, story="Story1",
        openings=[Opening(0.25, 0.25, 0.75, 0.75)]))
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.add_link((0, 0, 0), (0, 0, L), [50.0, 0, 0, 0, 0, 0], uid="LK1")
    mdl.diaphragm = "none"
    mdl.story_diaphragm = {"Story1": "none"}
    mdl.pattern("G", "other").nodal_loads.append(
        NodalLoad((0, 0, L), fz=-20.0))
    mdl.add_pushover_case("PX", "X", gravity={"G": 1.0}, target_drift=0.004,
                          steps=10, hinges="column_base", My={"C1": 60.0},
                          hardening=0.05)
    mdl.validate()

    d1 = mdl.to_dict()
    d2 = BuildingModel.from_dict(d1).to_dict()
    assert d2 == d1

    r = api_client.post("/api/model", json=d1)
    assert r.status_code == 200, r.get_json()
    # JSON turns coordinate tuples into lists: compare in JSON space
    import json as _json
    assert r.get_json() == _json.loads(_json.dumps(d1))

    # invalid v0.5 payloads -> 400 with an error message
    bad = BuildingModel.from_dict(d1).to_dict()
    bad["shells"][0]["openings"][0]["u1"] = 2.0
    rb = api_client.post("/api/model", json=bad)
    assert rb.status_code == 400 and "error" in rb.get_json()

    r2 = api_client.post("/api/model", json=d1)
    assert r2.status_code == 200
    ra = api_client.post("/api/analyze")
    assert ra.status_code == 200, ra.get_json()
    res = ra.get_json()
    assert "PX" in res["pushover"]
    assert len(res["pushover"]["PX"]["roof_disp"]) == 10
    # the meshed wall (4x3 cells, snapped opening omits the central 2x1
    # block) reports its 10 quads
    assert len(res["shell_quads"]) == 10
