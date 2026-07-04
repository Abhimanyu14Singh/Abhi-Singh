"""Wave 9 backend tests (v0.8): point spring supports (foundation springs),
accidental torsion (ASCE 7-16 §12.8.4.2), temperature (thermal) loads, and
the per-story center-of-mass / center-of-rigidity diagnostic.

Same rigor as test_wave8/test_phase5: every expected number is derived in the
test from first principles (closed form or an independent numpy solution).

Springs:
  * a vertical spring kz under axial load P settles P/kz exactly (1e-9);
  * a lateral spring kx at a cantilever base gives tip = H/kx + H L^3/3EI22
    (spring + cantilever in series, hand-assembled, 1e-6);
  * a rotational base spring kry softens the cantilever: tip = H L^3/3EI22
    + H L^2/kry, larger than the fixed base (direction + hand value, 1e-6);
  * spring reactions + other reactions balance the applied load (1e-8).

Accidental torsion:
  * a symmetric single-story diaphragm (centered at the origin) under EQX with
    the flag: master gets a positive rz and base MZ = -F*ecc*Ly exactly
    (1e-6); without the flag rz ~ 0.

Thermal:
  * a fixed-fixed bar heated dT: N = -E*A*alpha*dT (compression) exactly
    (1e-9);
  * a one-end-free bar: N = 0 and free expansion delta = alpha*dT*L (1e-9);
  * a bar restrained by a kx foundation spring: N = -E*A*alpha*dT * ks/(EA/L
    + ks), an independent closed-form thermal solution (1e-6).

CM/CR:
  * a symmetric building: CR coincides with CM and the plan center (< 1e-3 B);
  * a stiff shear wall on one side shifts CR toward the wall (> 0.05 B), CM
    stays centered;
  * a model with no diaphragm omits story_props.

Units per CONTRACT.md: kN, m, tonne, s; E in kPa.
"""

import math

import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core.builder import quick_building
from skyframe.core.model import (
    BuildingModel,
    FrameSection,
    Material,
    MemberUDL,
    NodalLoad,
    PointSupport,
    ShellRegion,
    ShellSection,
    SpringSupport,
    StoryForce,
    ThermalLoad,
)

E_CONC = 25_000_000.0  # kPa
GAMMA = 24.0           # kN/m^3
ALPHA = 1.2e-5         # /degC (model default)


def _run(model) -> dict:
    return OpenSeesEngine(model).run().to_dict()


def _new_model(name, story_heights) -> BuildingModel:
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2, unit_weight=GAMMA))
    mdl.set_stories(list(story_heights))
    return mdl


def _node_at(results, pt, tol=1e-6):
    for tag, xyz in results["nodes"].items():
        if all(abs(a - b) < tol for a, b in zip(xyz, pt)):
            return tag
    raise AssertionError(f"no FE node at {pt}")


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# --------------------------------------------------------------------------- #
# 1. point spring supports
# --------------------------------------------------------------------------- #
def test_spring_vertical_settlement():
    """A vertical spring kz carries an axial load P: base settles P/kz."""
    L, size, kz, P = 4.0, 0.5, 5000.0, 300.0
    mdl = _new_model("kz", [L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                   uid="C1")
    # base: fix everything except uz, which the spring restrains
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 0, 1, 1, 1)))
    mdl.add_spring_support((0, 0, 0), [0, 0, kz, 0, 0, 0])
    mdl.pattern("L", "live").nodal_loads.append(NodalLoad((0, 0, L), fz=-P))
    mdl.add_case("L", {"L": 1.0})
    mdl.num_modes = 0
    d = _run(mdl)
    base = _node_at(d, (0, 0, 0))
    assert d["cases"]["L"]["node_disp"][base][2] == pytest.approx(
        -P / kz, rel=1e-9)
    # spring reaction restores the load: base FZ = P, equilibrium at 1e-8
    assert d["cases"]["L"]["base"]["FZ"] == pytest.approx(P, abs=1e-8)
    assert d["cases"]["L"]["reactions"][base][2] == pytest.approx(P, rel=1e-9)


def test_spring_lateral_series_with_cantilever():
    """Lateral spring kx at a cantilever base: tip = H/kx + H L^3/(3 E I22),
    the spring translation and the cantilever bending in series (1e-6)."""
    L, b, h, kx, H = 4.0, 0.4, 0.6, 8000.0, 50.0
    sec = FrameSection.rectangular("COL", "CONC", b, h)
    mdl = _new_model("kx", [L])
    mdl.add_section(sec)
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                   uid="C1")
    # base: free ux (sprung by kx), everything else fixed -> rotation held,
    # so the column above the spring is a pure fixed-rotation cantilever
    mdl.supports.append(PointSupport((0, 0, 0), (0, 1, 1, 1, 1, 1)))
    mdl.add_spring_support((0, 0, 0), [kx, 0, 0, 0, 0, 0])
    mdl.pattern("H", "other").nodal_loads.append(NodalLoad((0, 0, L), fx=H))
    mdl.add_case("H", {"H": 1.0})
    mdl.num_modes = 0
    d = _run(mdl)
    tip = _node_at(d, (0, 0, L))
    expected = H / kx + H * L ** 3 / (3.0 * E_CONC * sec.I22)
    assert d["cases"]["H"]["node_disp"][tip][0] == pytest.approx(
        expected, rel=1e-6)
    # equilibrium: base FX balances the applied H
    assert d["cases"]["H"]["base"]["FX"] == pytest.approx(-H, abs=1e-8)


def test_spring_rotational_base_softens_drift():
    """A rotational base spring kry (about the bending axis) softens the
    cantilever: tip = H L^3/(3 E I22) + H L^2/kry, strictly larger than the
    fixed-base H L^3/(3 E I22) (direction + hand value, 1e-6)."""
    L, b, h, H, kry = 4.0, 0.4, 0.6, 50.0, 3000.0
    sec = FrameSection.rectangular("COL", "CONC", b, h)

    def build(spring):
        mdl = _new_model("kr", [L])
        mdl.add_section(sec)
        mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                       uid="C1")
        if spring is None:
            mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
        else:
            mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 0, 1)))
            mdl.add_spring_support((0, 0, 0), [0, 0, 0, 0, spring, 0])
        mdl.pattern("H", "other").nodal_loads.append(
            NodalLoad((0, 0, L), fx=H))
        mdl.add_case("H", {"H": 1.0})
        mdl.num_modes = 0
        return mdl

    df = _run(build(None))
    ds = _run(build(kry))
    tip = _node_at(df, (0, 0, L))
    fixed = H * L ** 3 / (3.0 * E_CONC * sec.I22)
    softened = fixed + H * L ** 2 / kry
    assert df["cases"]["H"]["node_disp"][tip][0] == pytest.approx(
        fixed, rel=1e-9)
    assert ds["cases"]["H"]["node_disp"][tip][0] == pytest.approx(
        softened, rel=1e-6)
    # direction: the rotational spring increases drift
    assert (ds["cases"]["H"]["node_disp"][tip][0]
            > df["cases"]["H"]["node_disp"][tip][0])


def test_spring_equilibrium_multi_dof():
    """Springs + fixed supports balance a combined load exactly (1e-8)."""
    L = 4.0
    mdl = _new_model("eq", [L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.5, 0.5))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (0, 0, 0, 1, 1, 1)))
    mdl.add_spring_support((0, 0, 0), [4000.0, 3000.0, 6000.0, 0, 0, 0])
    p = mdl.pattern("P", "other")
    p.nodal_loads.append(NodalLoad((0, 0, L), fx=25.0, fy=-15.0, fz=-120.0))
    mdl.add_case("P", {"P": 1.0})
    mdl.num_modes = 0
    d = _run(mdl)
    base = d["cases"]["P"]["base"]
    assert base["FX"] == pytest.approx(-25.0, abs=1e-8)
    assert base["FY"] == pytest.approx(15.0, abs=1e-8)
    assert base["FZ"] == pytest.approx(120.0, abs=1e-8)


def test_spring_roundtrip_and_validation():
    mdl = _new_model("srt", [3.0])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, 3), story="Story1",
                   uid="C1")
    mdl.add_spring_support((0, 0, 0), [100, 0, 200, 0, 0, 50])
    d = mdl.to_dict()
    assert d["spring_supports"][0]["stiffness"] == [100, 0, 200, 0, 0, 50]
    back = BuildingModel.from_dict(d)
    assert back.spring_supports[0].stiffness == [100, 0, 200, 0, 0, 50]
    assert back.to_dict() == d
    # pre-v0.8 dicts (no key) => no springs
    d.pop("spring_supports")
    assert BuildingModel.from_dict(d).spring_supports == []
    with pytest.raises(ValueError, match="6 entries"):
        mdl.add_spring_support((0, 0, 0), [1, 2, 3])
    with pytest.raises(ValueError, match=">= 0"):
        mdl.add_spring_support((0, 0, 0), [-1, 0, 0, 0, 0, 0])
    with pytest.raises(ValueError, match="at least one"):
        mdl.add_spring_support((0, 0, 0), [0, 0, 0, 0, 0, 0])


# --------------------------------------------------------------------------- #
# 2. accidental torsion
# --------------------------------------------------------------------------- #
def _sym_torsion_model():
    """Symmetric single-story diaphragm centered at the origin (Lx=6, Ly=8)."""
    H = 4.0
    mdl = BuildingModel(name="tors")
    mdl.add_material(Material("C", E=E_CONC, nu=0.2))
    mdl.set_stories([H])
    mdl.add_section(FrameSection.rectangular("COL", "C", 0.4, 0.4))
    mdl.add_section(FrameSection.rectangular("BM", "C", 0.3, 0.5))
    xs, ys = [-3.0, 3.0], [-4.0, 4.0]
    for i, (x, y) in enumerate([(x, y) for x in xs for y in ys]):
        mdl.add_member("column", "COL", (x, y, 0), (x, y, H), story="Story1",
                       uid=f"C{i}")
    pts = [(-3, -4), (3, -4), (3, 4), (-3, 4)]
    for i in range(4):
        a, b = pts[i], pts[(i + 1) % 4]
        mdl.add_member("beam", "BM", (a[0], a[1], H), (b[0], b[1], H),
                       story="Story1", uid=f"B{i}")
    mdl.supports = [PointSupport((x, y, 0), (1, 1, 1, 1, 1, 1))
                    for x in xs for y in ys]
    mdl.pattern("EQX", "quake").story_forces.append(
        StoryForce("Story1", fx=100.0))
    mdl.add_case("EQX", {"EQX": 1.0})
    mdl.num_modes = 0
    return mdl, H


def test_accidental_torsion_master_rz_and_base_mz():
    F, ecc = 100.0, 0.05
    mdl, H = _sym_torsion_model()
    mdl.patterns["EQX"].accidental_torsion = True
    mdl.patterns["EQX"].ecc = ecc
    lx, ly = mdl.plan_extents()
    torque = F * ecc * ly
    d = _run(mdl)
    master = _node_at(d, (0, 0, H))
    rz = d["cases"]["EQX"]["node_disp"][master][5]
    # positive torsion -> positive diaphragm rotation
    assert rz > 1e-9
    # base MZ reaction balances the applied torque exactly (master at origin,
    # so the story force contributes no moment about the vertical axis)
    assert d["cases"]["EQX"]["base"]["MZ"] == pytest.approx(-torque, rel=1e-6)


def test_no_accidental_torsion_no_rotation():
    mdl, H = _sym_torsion_model()          # flag stays False
    d = _run(mdl)
    master = _node_at(d, (0, 0, H))
    assert abs(d["cases"]["EQX"]["node_disp"][master][5]) < 1e-12
    assert abs(d["cases"]["EQX"]["base"]["MZ"]) < 1e-6


def test_accidental_torsion_roundtrip():
    mdl, _ = _sym_torsion_model()
    mdl.patterns["EQX"].accidental_torsion = True
    mdl.patterns["EQX"].ecc = 0.07
    d = mdl.to_dict()
    assert d["patterns"]["EQX"]["accidental_torsion"] is True
    assert d["patterns"]["EQX"]["ecc"] == 0.07
    back = BuildingModel.from_dict(d)
    assert back.patterns["EQX"].accidental_torsion is True
    assert back.patterns["EQX"].ecc == 0.07
    assert back.to_dict() == d


# --------------------------------------------------------------------------- #
# 3. temperature (thermal) loads
# --------------------------------------------------------------------------- #
def test_thermal_fixed_fixed_bar_axial():
    """Fixed-fixed bar, temperature rise dT: N = -E*A*alpha*dT (1e-9)."""
    L, b, h, dT = 6.0, 0.3, 0.5, 30.0
    A = b * h
    mdl = _new_model("tf", [3.0])
    mdl.add_section(FrameSection.rectangular("BAR", "CONC", b, h))
    mdl.add_member("beam", "BAR", (0, 0, 0), (L, 0, 0), story="Story1",
                   uid="B1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.supports.append(PointSupport((L, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.pattern("T", "other").thermal_loads.append(ThermalLoad("B1", dT))
    mdl.add_case("T", {"T": 1.0})
    mdl.num_modes = 0
    d = _run(mdl)
    N = d["cases"]["T"]["member_stations"]["B1"]["N"]
    for v in N:                                    # uniform axial along member
        assert v == pytest.approx(-E_CONC * A * ALPHA * dT, rel=1e-9)


def test_thermal_free_expansion():
    """One end free: N = 0 and free expansion delta = alpha*dT*L (1e-9)."""
    L, dT = 6.0, 30.0
    mdl = _new_model("tfe", [3.0])
    mdl.add_section(FrameSection.rectangular("BAR", "CONC", 0.3, 0.5))
    mdl.add_member("beam", "BAR", (0, 0, 0), (L, 0, 0), story="Story1",
                   uid="B1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    # far end: free axial (ux), transverse held so the bar is stable
    mdl.supports.append(PointSupport((L, 0, 0), (0, 1, 1, 1, 1, 1)))
    mdl.pattern("T", "other").thermal_loads.append(ThermalLoad("B1", dT))
    mdl.add_case("T", {"T": 1.0})
    mdl.num_modes = 0
    d = _run(mdl)
    assert d["cases"]["T"]["member_stations"]["B1"]["N"][5] == pytest.approx(
        0.0, abs=1e-6)
    tip = _node_at(d, (L, 0, 0))
    assert d["cases"]["T"]["node_disp"][tip][0] == pytest.approx(
        ALPHA * dT * L, rel=1e-9)


def test_thermal_spring_restrained_closed_form():
    """A bar fixed at one end and restrained by a kx foundation spring ks at
    the other develops N = -E*A*alpha*dT * ks/(EA/L + ks), an independent
    closed-form thermal solution (spring in series with the bar, 1e-6)."""
    L, b, h, dT, ks = 5.0, 0.3, 0.4, 40.0, 2.0e5
    A = b * h
    EA = E_CONC * A
    mdl = _new_model("tsr", [3.0])
    mdl.add_section(FrameSection.rectangular("BAR", "CONC", b, h))
    mdl.add_member("beam", "BAR", (0, 0, 0), (L, 0, 0), story="Story1",
                   uid="B1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    # far end: transverse held, ux free and restrained by the spring ks
    mdl.supports.append(PointSupport((L, 0, 0), (0, 1, 1, 1, 1, 1)))
    mdl.add_spring_support((L, 0, 0), [ks, 0, 0, 0, 0, 0])
    mdl.pattern("T", "other").thermal_loads.append(ThermalLoad("B1", dT))
    mdl.add_case("T", {"T": 1.0})
    mdl.num_modes = 0
    d = _run(mdl)
    N_full = E_CONC * A * ALPHA * dT
    expected = -N_full * ks / (EA / L + ks)
    got = d["cases"]["T"]["member_stations"]["B1"]["N"][5]
    assert got == pytest.approx(expected, rel=1e-6)
    # spring reaction balances the bar axial force
    tip = _node_at(d, (L, 0, 0))
    assert d["cases"]["T"]["reactions"][tip][0] == pytest.approx(
        expected, rel=1e-6)


def test_thermal_alpha_roundtrip():
    mdl = _new_model("tar", [3.0])
    mdl.thermal_alpha = 1.5e-5
    mdl.add_section(FrameSection.rectangular("B", "CONC", 0.3, 0.5))
    mdl.add_member("beam", "B", (0, 0, 0), (4, 0, 0), story="Story1",
                   uid="B1")
    mdl.add_thermal_load(mdl.pattern("T", "other").name, "B1", 25.0)
    d = mdl.to_dict()
    assert d["thermal_alpha"] == 1.5e-5
    assert d["patterns"]["T"]["thermal_loads"] == [
        {"member_uid": "B1", "dT": 25.0}]
    back = BuildingModel.from_dict(d)
    assert back.thermal_alpha == 1.5e-5
    assert back.patterns["T"].thermal_loads[0].dT == 25.0
    assert back.to_dict() == d
    with pytest.raises(ValueError, match="unknown member"):
        mdl.add_thermal_load("T", "ZZ", 10.0)


# --------------------------------------------------------------------------- #
# 4. center of mass / center of rigidity
# --------------------------------------------------------------------------- #
def _diaphragm_box(with_wall=False):
    """Single-story rigid-diaphragm box (6x6) centered at the origin, columns
    at the corners, perimeter beams; optionally a stiff shear wall at x=+3."""
    H = 4.0
    mdl = BuildingModel(name="box")
    mdl.add_material(Material("C", E=E_CONC, nu=0.2, unit_weight=GAMMA))
    mdl.set_stories([H])
    mdl.add_section(FrameSection.rectangular("COL", "C", 0.4, 0.4))
    mdl.add_section(FrameSection.rectangular("BM", "C", 0.3, 0.5))
    mdl.add_shell_section(ShellSection("SH", "C", 0.3))
    xs, ys = [-3.0, 3.0], [-3.0, 3.0]
    for i, (x, y) in enumerate([(x, y) for x in xs for y in ys]):
        mdl.add_member("column", "COL", (x, y, 0), (x, y, H), story="Story1",
                       uid=f"C{i}")
    pts = [(-3, -3), (3, -3), (3, 3), (-3, 3)]
    for i in range(4):
        a, b = pts[i], pts[(i + 1) % 4]
        mdl.add_member("beam", "BM", (a[0], a[1], H), (b[0], b[1], H),
                       story="Story1", uid=f"B{i}")
        # gravity UDL on the perimeter beams -> symmetric mass distribution
        mdl.pattern("DEAD", "dead").member_udls.append(
            MemberUDL(f"B{i}", 10.0))
    mdl.mass_source = {"DEAD": 1.0}
    mdl.supports = [PointSupport((x, y, 0), (1, 1, 1, 1, 1, 1))
                    for x in xs for y in ys]
    if with_wall:
        mdl.shells.append(ShellRegion(
            "WALL", "wall", "shell", "SH",
            [(3, -3, 0), (3, 3, 0), (3, 3, H), (3, -3, H)],
            mesh_size=1.0, story="Story1"))
    mdl.num_modes = 0
    return mdl, H


def test_story_props_symmetric_cr_equals_cm_and_center():
    mdl, H = _diaphragm_box(with_wall=False)
    d = _run(mdl)
    props = d["story_props"]["Story1"]
    B = 6.0
    assert props["cm_x"] == pytest.approx(0.0, abs=1e-3 * B)
    assert props["cm_y"] == pytest.approx(0.0, abs=1e-3 * B)
    assert props["cr_x"] == pytest.approx(0.0, abs=1e-3 * B)
    assert props["cr_y"] == pytest.approx(0.0, abs=1e-3 * B)


def test_story_props_asymmetric_wall_shifts_cr():
    mdl, H = _diaphragm_box(with_wall=True)
    d = _run(mdl)
    props = d["story_props"]["Story1"]
    B = 6.0
    # a stiff wall at x=+3 resisting Y pulls the center of rigidity toward it
    assert props["cr_x"] > 0.05 * B
    assert abs(props["cr_x"]) > 0.05 * B
    # the mass stays centered (loads are symmetric)
    assert props["cm_x"] == pytest.approx(0.0, abs=1e-3 * B)


def test_story_props_absent_without_diaphragm():
    """No rigid diaphragm -> no story_props (keys omitted)."""
    mdl = _new_model("nod", [3.0])            # rigid_diaphragms = False
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, 3), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.num_modes = 0
    d = _run(mdl)
    assert "story_props" not in d


# --------------------------------------------------------------------------- #
# 5. API
# --------------------------------------------------------------------------- #
def test_api_spring_and_thermal(api_client):
    assert api_client.post("/api/model/quick", json={}).status_code == 200
    mdl = api_client.get("/api/model").get_json()
    uid = mdl["members"][0]["uid"]

    # spring support
    r = api_client.post("/api/support/spring",
                        json={"point": [0, 0, 0],
                              "stiffness": [1000, 0, 2000, 0, 0, 0]})
    assert r.status_code == 200, r.get_json()
    assert len(r.get_json()["spring_supports"]) == 1

    # thermal loads on an existing pattern
    r = api_client.post("/api/pattern/thermal",
                        json={"pattern": "DEAD",
                              "loads": [{"member_uid": uid, "dT": 20.0}]})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["patterns"]["DEAD"]["thermal_loads"] == [
        {"member_uid": uid, "dT": 20.0}]

    # bad inputs -> 400
    assert api_client.post("/api/support/spring",
                           json={"point": [0, 0],
                                 "stiffness": [1, 2, 3, 4, 5, 6]}
                           ).status_code == 400
    assert api_client.post("/api/support/spring",
                           json={"point": [0, 0, 0],
                                 "stiffness": [0, 0, 0, 0, 0, 0]}
                           ).status_code == 400
    assert api_client.post("/api/pattern/thermal",
                           json={"pattern": "NOPE",
                                 "loads": [{"member_uid": uid, "dT": 1.0}]}
                           ).status_code == 400
    assert api_client.post("/api/pattern/thermal",
                           json={"pattern": "DEAD", "loads": []}
                           ).status_code == 400


def test_api_model_roundtrips_springs_and_torsion(api_client):
    """POST /api/model round-trips spring supports + accidental torsion."""
    mdl = _new_model("apirt", [3.0])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, 3), story="Story1",
                   uid="C1")
    mdl.add_spring_support((0, 0, 0), [500, 600, 700, 0, 0, 100])
    p = mdl.pattern("EQX", "quake")
    p.story_forces.append(StoryForce("Story1", fx=10.0))
    p.accidental_torsion = True
    p.ecc = 0.05
    r = api_client.post("/api/model", json=mdl.to_dict())
    assert r.status_code == 200, r.get_json()
    echoed = r.get_json()
    assert echoed["spring_supports"][0]["stiffness"] == [500, 600, 700, 0,
                                                         0, 100]
    assert echoed["patterns"]["EQX"]["accidental_torsion"] is True
    assert echoed["patterns"]["EQX"]["ecc"] == 0.05
