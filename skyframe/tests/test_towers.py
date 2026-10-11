"""C9 — ETABS-style multiple towers (CONTRACT "Multiple towers").

Pins: the data model (tags, height-table materialisation, round trip,
validation), the engine (one diaphragm per tower per story, per-tower
story drift / shear / CM / CR / irregularity, RS and TH per tower), the
per-tower auto-lateral loads (hand-checked ELF, legacy ELF / wind,
notional loads), the results tables' tower column, and that a model
without towers takes the legacy path untouched.
"""
import json
import math
import warnings

import pytest

from skyframe.core import autolateral as al
from skyframe.core import towers as ct
from skyframe.core.builder import make_wind_pattern, quick_building
from skyframe.core.codes import asce7_elf
from skyframe.core.model import (G_ACCEL, BuildingModel, FrameSection,
                                 GridSystem, Material, MemberUDL, Story,
                                 StoryForce, make_notional_pattern,
                                 story_gravity_loads)
from skyframe.core.tables import compute_table
from skyframe.engine.opensees_engine import OpenSeesEngine

warnings.simplefilter("ignore")

H = 3.0


# --------------------------------------------------------------- builders
def _base(name="m", modes=6):
    m = BuildingModel(name=name)
    m.add_material(Material("C", E=25e6, nu=0.2, unit_weight=24.0))
    m.add_section(FrameSection.rectangular("COL", "C", 0.4, 0.4))
    m.add_section(FrameSection.rectangular("BM", "C", 0.3, 0.5))
    m.pattern("DEAD", "dead")
    m.pattern("EQX", "quake")
    m.mass_source = {"DEAD": 1.0}
    m.num_modes = modes
    return m


def _tower(m, prefix, ox, nst=3, tower="", z0=0.0, dead=20.0, fx=10.0,
           bx=6.0, by=5.0):
    """A 1x1-bay frame tower at x offset ``ox`` with its own grid."""
    g = GridSystem([0.0, bx], [0.0, by], name="G" + prefix,
                   origin=(ox, 0.0))
    m.grid_systems.append(g)
    m.grid = m.grid_systems[0]
    elev = z0
    for i in range(nst):
        elev += H
        s = Story(f"{prefix}{i + 1}", H, elev)
        s.tower = tower
        m.stories.append(s)
        zt, zb = elev, elev - H
        for x in (0.0, bx):
            for y in (0.0, by):
                m.add_member("column", "COL", (ox + x, y, zb),
                             (ox + x, y, zt), story=s.name,
                             uid=f"{prefix}C{i}_{x}_{y}")
        for y in (0.0, by):
            b = m.add_member("beam", "BM", (ox, y, zt), (ox + bx, y, zt),
                             story=s.name, uid=f"{prefix}BX{i}_{y}")
            m.patterns["DEAD"].member_udls.append(
                MemberUDL(b.uid, dead + 5.0 * i))
        for x in (0.0, bx):
            b = m.add_member("beam", "BM", (ox + x, 0.0, zt),
                             (ox + x, by, zt), story=s.name,
                             uid=f"{prefix}BY{i}_{x}")
            m.patterns["DEAD"].member_udls.append(MemberUDL(b.uid, 15.0))
        if fx:
            m.patterns["EQX"].story_forces.append(
                StoryForce(s.name, fx=fx * (i + 1), fy=0.3 * fx))
    return m


def _cases(m):
    m.add_case("DEAD", {"DEAD": 1.0})
    m.add_case("EQX", {"EQX": 1.0})
    return m


def _single():
    return _cases(_tower(_base("single"), "A", 0.0))


def _twin(defined=True):
    m = _base("twin", modes=12)
    _tower(m, "A", 0.0)
    _tower(m, "B", 40.0, tower="T2" if defined else "")
    if defined:
        m.towers = [{"name": "T1", "grid_system": "GA",
                     "base_elevation": 0.0},
                    {"name": "T2", "grid_system": "GB",
                     "base_elevation": 0.0}]
    m.validate()
    return _cases(m)


def _close(a, b, rel=1e-8, abs_=1e-10):
    return math.isclose(a, b, rel_tol=rel, abs_tol=abs_)


def _rt(m):
    d = m.to_dict()
    m2 = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert m2.to_dict() == d
    return m2


@pytest.fixture(scope="module")
def single_res():
    return OpenSeesEngine(_single()).run()


@pytest.fixture(scope="module")
def twin_res():
    return OpenSeesEngine(_twin()).run()


# ------------------------------------------------------------- defaults
def test_default_no_tower_keys_and_legacy_path():
    m = quick_building(stories=2, bays_x=1, bays_y=1)
    d = m.to_dict()
    assert "towers" not in d and "joint_towers" not in d
    assert all("tower" not in s for s in d["stories"])
    assert all("tower" not in md for md in d["members"])
    eng = OpenSeesEngine(m)
    assert "_tw" not in eng.__dict__                 # no override bound
    assert "_story_results" not in eng.__dict__
    res = eng.run().to_dict()
    assert "towers" not in res and "story_tower" not in res
    assert m.towers == [] and m.joint_towers == []


def test_explicit_empty_towers_round_trip_identical():
    m = quick_building(stories=2, bays_x=1, bays_y=1)
    d = m.to_dict()
    d2 = dict(d, towers=[], joint_towers=[])
    assert BuildingModel.from_dict(d2).to_dict() == d


# ----------------------------------------------------------- data model
def test_height_table_and_definitions_materialise():
    m = _tower(_base(), "A", 0.0, nst=2)
    d = m.to_dict()
    d["towers"] = [
        {"name": "Main", "grid_system": "GA"},
        {"name": "East", "base_elevation": 6.0, "grid_system": None,
         "stories": [3.5, 4.0]},
        {"name": "West", "stories": [{"name": "W1", "height": 3.2}]}]
    m2 = BuildingModel.from_dict(d)
    names = [s.name for s in m2.stories]
    assert names == ["A1", "A2", "East-S1", "East-S2", "W1"]
    el = {s.name: s.elevation for s in m2.stories}
    assert el["East-S1"] == pytest.approx(9.5)
    assert el["East-S2"] == pytest.approx(13.5)
    assert el["W1"] == pytest.approx(3.2)
    out = m2.to_dict()
    assert out["towers"] == [
        {"name": "Main", "grid_system": "GA", "base_elevation": 0.0,
         "stories": ["A1", "A2"]},
        {"name": "East", "grid_system": None, "base_elevation": 6.0,
         "stories": ["East-S1", "East-S2"]},
        {"name": "West", "grid_system": None, "base_elevation": 0.0,
         "stories": ["W1"]}]
    # default-tower stories stay untagged; others carry the tag
    tags = {s["name"]: s.get("tower") for s in out["stories"]}
    assert tags == {"A1": None, "A2": None, "East-S1": "East",
                    "East-S2": "East", "W1": "West"}
    _rt(m2)


def test_story_references_tag_non_default_towers():
    m = _base()
    _tower(m, "A", 0.0, nst=1)
    _tower(m, "B", 20.0, nst=1)
    d = m.to_dict()
    d["towers"] = [{"name": "T1", "stories": ["A1"]},
                   {"name": "T2", "stories": ["B1"]}]
    m2 = BuildingModel.from_dict(d)
    assert ct.story_tower_map(m2) == {"A1": "T1", "B1": "T2"}
    assert m2.stories[0].tower == "" and m2.stories[1].tower == "T2"
    _rt(m2)


def test_object_and_joint_tags_round_trip():
    m = _twin()
    m.members[0].tower = "T1"
    m.joint_towers = [{"point": [0.0, 0.0, 3.0], "tower": "T1"}]
    m.validate()
    d = _rt(m).to_dict()
    assert d["members"][0]["tower"] == "T1"
    assert "tower" not in d["members"][1]
    assert d["joint_towers"] == [{"point": [0.0, 0.0, 3.0], "tower": "T1"}]


@pytest.mark.parametrize("mut, msg", [
    (lambda m: m.towers.append({"name": "T1", "grid_system": None,
                                "base_elevation": 0.0}), "duplicate"),
    (lambda m: setattr(m.stories[0], "tower", "XX"), "unknown tower"),
    (lambda m: setattr(m.members[0], "tower", "T2"), "contradicts"),
    (lambda m: m.towers.__setitem__(0, dict(m.towers[0],
                                            grid_system="NOPE")),
     "unknown grid"),
    (lambda m: m.towers.append({"name": "T3", "grid_system": None,
                                "base_elevation": 0.0}), "no stories"),
    (lambda m: setattr(m.stories[1], "elevation", 1.0), "increase"),
    (lambda m: m.joint_towers.append({"point": [0, 0, 3], "tower": "Q"}),
     "joint_towers"),
])
def test_validation_errors(mut, msg):
    m = _twin()
    mut(m)
    with pytest.raises(ValueError, match=msg):
        m.validate()


def test_tags_without_towers_rejected():
    m = _single()
    m.stories[0].tower = "T1"
    with pytest.raises(ValueError, match="need model.towers"):
        m.validate()


def test_bad_tower_input_rejected():
    d = _single().to_dict()
    for bad in ({"towers": "x"}, {"towers": [{"name": ""}]},
                {"towers": [{"name": "T", "stories": [0.0]}]},
                {"towers": [{"name": "T", "stories": ["nope"]}]},
                {"towers": [{"name": "T", "base_elevation": "a"}]},
                {"towers": [{"name": "T", "stories": [{"name": "A1",
                                                       "height": 3}]}]}):
        with pytest.raises(ValueError):
            BuildingModel.from_dict(dict(d, **bad))


def test_membership_rules():
    m = _twin()
    ctx = ct.context(m)
    # by story
    assert ctx.obj(m.members[0]) == "T1"
    assert ctx.obj([x for x in m.members if x.uid.startswith("B")][0]) \
        == "T2"
    # untagged object without story: geometry (footprint of GB)
    from skyframe.core.model import FrameMember
    fm = FrameMember("X", "beam", "BM", (41.0, 1.0, 3.0), (43.0, 1.0, 3.0))
    assert ctx.obj(fm) == "T2"
    # points: votes / footprint / joint tag
    assert ct.point_tower(m, (40.0, 0.0, 3.0)) == "T2"
    assert ct.point_tower(m, (3.0, 2.0, 6.0)) == "T1"
    m.joint_towers = [{"point": [40.0, 0.0, 3.0], "tower": "T1"}]
    assert ct.point_tower(m, (40.0, 0.0, 3.0)) == "T1"


# ------------------------------------------------- twin disconnected towers
def test_twin_towers_modes_doubled_same_periods(single_res, twin_res):
    ps = single_res.modal.periods
    pt = twin_res.modal.periods
    assert len(pt) == 2 * len(ps)
    for i, p in enumerate(ps):
        assert _close(pt[2 * i], p, 1e-8) and _close(pt[2 * i + 1], p, 1e-8)


@pytest.mark.parametrize("case", ["DEAD", "EQX"])
def test_twin_towers_story_results_equal_single(single_res, twin_res, case):
    for i in (1, 2, 3):
        a = single_res.cases[case].story[f"A{i}"]
        for nm in (f"A{i}", f"B{i}"):
            b = twin_res.cases[case].story[nm]
            for k in a:
                assert _close(b[k], a[k], 1e-8, 1e-12), (case, nm, k)


def test_twin_towers_eqx_hand_values(twin_res):
    st = twin_res.cases["EQX"].story
    # applied story forces 10, 20, 30 (+3 in y each) per tower
    for p in "AB":
        assert st[f"{p}1"]["shear_x"] == pytest.approx(60.0, rel=1e-9)
        assert st[f"{p}2"]["shear_x"] == pytest.approx(50.0, rel=1e-9)
        assert st[f"{p}3"]["shear_x"] == pytest.approx(30.0, rel=1e-9)
        assert st[f"{p}1"]["shear_y"] == pytest.approx(9.0, rel=1e-9)
    tw = twin_res.to_dict()["towers"]
    assert tw["T1"]["base_shear"]["EQX"]["FX"] == pytest.approx(60.0)
    assert tw["T2"]["base_shear"]["EQX"]["FX"] == pytest.approx(60.0)
    assert tw["T1"]["stories"] == ["A1", "A2", "A3"]
    # total base shear = sum of the towers' base-story shears
    base = twin_res.cases["EQX"].base["FX"]
    assert -base == pytest.approx(120.0, rel=1e-9)


def test_twin_towers_one_diaphragm_per_tower_per_story():
    eng = OpenSeesEngine(_twin())
    asm = eng._build()
    assert sorted(asm.masters) == ["A1", "A2", "A3", "B1", "B2", "B3"]
    for s in ("A1", "B2"):
        xs = {asm.node_coords[t][0] for t in asm.story_nodes[s]}
        assert (max(xs) < 10.0) if s[0] == "A" else (min(xs) > 30.0)
    assert asm.node_coords[asm.masters["A1"]][:2] == (3.0, 2.5)
    assert asm.node_coords[asm.masters["B1"]][:2] == (43.0, 2.5)


def test_twin_towers_cm_cr_and_irregularity(single_res, twin_res):
    for i in (1, 2, 3):
        a = single_res.story_props[f"A{i}"]
        b = twin_res.story_props[f"B{i}"]
        assert _close(b["cm_x"], a["cm_x"] + 40.0)
        assert _close(b["cr_x"], a["cr_x"] + 40.0, 1e-8, 1e-8)
        assert _close(b["cm_y"], a["cm_y"])
        ia = single_res.irregularity["EQX"][f"A{i}"]
        for nm in (f"A{i}", f"B{i}"):
            ib = twin_res.irregularity["EQX"][nm]
            assert ib["flag"] == ia["flag"]
            for k in ("tors_ratio_x", "tors_ratio_y"):
                assert _close(ib[k], ia[k])
        ka = single_res.story_stiffness["EQX"][f"A{i}"]["kx"]
        assert _close(twin_res.story_stiffness["EQX"][f"B{i}"]["kx"], ka,
                      1e-7)


def test_without_tower_definitions_the_towers_couple():
    """Control: the same geometry with no towers lumps both towers into
    one diaphragm per elevation (shear sums both towers' loads)."""
    res = OpenSeesEngine(_twin(defined=False)).run()
    assert res.cases["EQX"].story["A1"]["shear_x"] == pytest.approx(120.0)


def test_twin_towers_accidental_torsion_uses_tower_extents():
    s, t = _single(), _twin()
    for m in (s, t):
        m.patterns["EQX"].accidental_torsion = True
        m.patterns["EQX"].ecc = 0.05
    rs = OpenSeesEngine(s).run_static("EQX")
    rt = OpenSeesEngine(t).run_static("EQX")
    ms = OpenSeesEngine(s)._build().masters
    eng = OpenSeesEngine(t)
    mt = eng._build().masters
    for i in (1, 2, 3):
        rz_s = rs.node_disp[ms[f"A{i}"]][5]
        assert abs(rz_s) > 0.0
        for nm in (f"A{i}", f"B{i}"):
            assert _close(rt.node_disp[mt[nm]][5], rz_s, 1e-7)


def test_twin_towers_response_spectrum_per_tower():
    sp = [[0.0, 0.4], [0.5, 1.0], [1.0, 0.5], [3.0, 0.2]]
    s, t = _single(), _twin()
    s.add_rs_case("RSX", "X", spectrum=sp)
    t.add_rs_case("RSX", "X", spectrum=sp)
    a = OpenSeesEngine(s).run_response_spectrum("RSX").story
    b = OpenSeesEngine(t).run_response_spectrum("RSX").story
    for i in (1, 2, 3):
        for nm in (f"A{i}", f"B{i}"):
            for k in ("ux", "drift_x", "shear_x"):
                assert _close(b[nm][k], a[f"A{i}"][k], 1e-6, 1e-10), (nm, k)


def test_twin_towers_time_history_drift_chain_per_tower():
    acc = [0.0, 0.1, -0.2, 0.15, 0.0, -0.05] * 3
    s, t = _single(), _twin()
    # modal damping: the Rayleigh fit is anchored on modes 1 and 3, which
    # differ between the two models (the twin's modes come in pairs)
    s.add_th_case("THX", "X", accel=acc, dt=0.02, damping_model="modal")
    t.add_th_case("THX", "X", accel=acc, dt=0.02, damping_model="modal")
    a = OpenSeesEngine(s).run_time_history("THX").peaks["story"]
    b = OpenSeesEngine(t).run_time_history("THX").peaks["story"]
    for i in (1, 2, 3):
        for nm in (f"A{i}", f"B{i}"):
            for k in ("ux", "drift_x"):
                assert _close(b[nm][k], a[f"A{i}"][k], 1e-6, 1e-12), (nm, k)


def test_twin_towers_story_masses_and_gravity_loads():
    s, t = _single(), _twin()
    ms = s.compute_story_masses()
    mt = t.compute_story_masses()
    assert list(mt) == [x.name for x in t.stories]
    for i in (1, 2, 3):
        assert mt[f"A{i}"] == pytest.approx(ms[f"A{i}"], rel=1e-12)
        assert mt[f"B{i}"] == pytest.approx(ms[f"A{i}"], rel=1e-12)
    # nodal gravity load at a shared elevation goes to ONE tower only
    from skyframe.core.model import NodalLoad
    t.patterns["DEAD"].nodal_loads.append(
        NodalLoad((40.0, 0.0, 3.0), fz=-9.80665))
    mt2 = t.compute_story_masses()
    assert mt2["B1"] - mt["B1"] == pytest.approx(1.0, rel=1e-12)
    assert mt2["A1"] == mt["A1"]
    g = story_gravity_loads(t, "DEAD")
    assert g["B1"] - g["A1"] == pytest.approx(9.80665, rel=1e-12)


# ----------------------------------------------------- podium + link beams
def _podium(load_towers=True):
    """Podium P (2 stories over x 0..46) + towers T1 (x 0..6) and T2
    (x 40..46) standing on the podium roof (base elevation 6 m)."""
    m = _base("podium", modes=6)
    m.towers = [{"name": "P", "grid_system": "GP", "base_elevation": 0.0},
                {"name": "T1", "grid_system": "GA", "base_elevation": 6.0},
                {"name": "T2", "grid_system": "GB", "base_elevation": 6.0}]
    xs = [0.0, 6.0, 20.0, 26.0, 40.0, 46.0]
    gp = GridSystem(xs, [0.0, 5.0], name="GP")
    m.grid_systems.append(gp)
    for i in (1, 2):
        s = Story(f"P{i}", H, H * i)
        s.tower = "P"
        m.stories.append(s)
        for x in xs:
            for y in (0.0, 5.0):
                m.add_member("column", "COL", (x, y, H * (i - 1)),
                             (x, y, H * i), story=s.name,
                             uid=f"PC{i}_{x}_{y}")
        for y in (0.0, 5.0):
            for a, b in zip(xs, xs[1:]):
                bm = m.add_member("beam", "BM", (a, y, H * i), (b, y, H * i),
                                  story=s.name, uid=f"PB{i}_{a}_{y}")
                m.patterns["DEAD"].member_udls.append(MemberUDL(bm.uid,
                                                                20.0))
        for x in xs:
            m.add_member("beam", "BM", (x, 0.0, H * i), (x, 5.0, H * i),
                         story=s.name, uid=f"PY{i}_{x}")
        m.patterns["EQX"].story_forces.append(StoryForce(s.name,
                                                         fx=25.0 * i))
    _tower(m, "A", 0.0, nst=3, tower="T1", z0=6.0,
           fx=10.0 if load_towers else 0.0)
    _tower(m, "B", 40.0, nst=2, tower="T2", z0=6.0,
           fx=15.0 if load_towers else 0.0)
    m.grid = m.grid_systems[0]
    m.validate()
    return _cases(m)


@pytest.fixture(scope="module")
def podium_res():
    m = _podium()
    eng = OpenSeesEngine(m)
    return m, eng, eng.run()


def test_podium_story_shears_sum_to_total_base_shear(podium_res):
    m, eng, res = podium_res
    st = res.cases["EQX"].story
    applied_T1 = 10 + 20 + 30
    applied_T2 = 15 + 30
    applied_P = 25 + 50
    total = applied_T1 + applied_T2 + applied_P
    base = -res.cases["EQX"].base["FX"]
    assert base == pytest.approx(total, rel=1e-9)
    # the podium's ground story carries everything = total base shear
    assert st["P1"]["shear_x"] == pytest.approx(base, rel=1e-8)
    # podium roof story: own roof load + both towers
    assert st["P2"]["shear_x"] == pytest.approx(
        50.0 + applied_T1 + applied_T2, rel=1e-8)
    # each tower's lowest story = its own load (towers only touch the
    # podium); together with the podium roof load they make up P2
    assert st["A1"]["shear_x"] == pytest.approx(applied_T1, rel=1e-8)
    assert st["B1"]["shear_x"] == pytest.approx(applied_T2, rel=1e-8)
    assert st["A1"]["shear_x"] + st["B1"]["shear_x"] + 50.0 == \
        pytest.approx(st["P2"]["shear_x"], rel=1e-9)
    tw = res.to_dict()["towers"]
    assert sum(tw[t]["base_shear"]["EQX"]["FX"] for t in ("T1", "T2")) \
        + 50.0 == pytest.approx(tw["P"]["base_shear"]["EQX"]["FX"]
                                - 25.0, rel=1e-9)


def test_podium_drift_measured_from_tower_base(podium_res):
    m, eng, res = podium_res
    cr = res.cases["EQX"]
    st = cr.story
    asm = eng._build()
    base_tags = [t for t, c in asm.struct_coords.items()
                 if abs(c[2] - 6.0) < 1e-9 and c[0] in (0.0, 6.0)]
    ub = sum(cr.node_disp[t][0] for t in base_tags) / len(base_tags)
    # rigid podium diaphragm: every base node moves with the P2 master
    assert ub == pytest.approx(st["P2"]["ux"], rel=1e-6)
    assert st["A1"]["drift_x"] == pytest.approx(
        (st["A1"]["ux"] - ub) / H, rel=1e-9)
    assert st["A2"]["drift_x"] == pytest.approx(
        (st["A2"]["ux"] - st["A1"]["ux"]) / H, rel=1e-9)
    # diaphragms: P stories have one master spanning the whole podium
    xs = [asm.node_coords[t][0] for t in asm.story_nodes["P2"]]
    assert min(xs) == 0.0 and max(xs) == 46.0
    assert asm.node_coords[asm.masters["P2"]][0] == pytest.approx(23.0)
    assert asm.node_coords[asm.masters["A1"]][0] == pytest.approx(3.0)


def test_link_beams_between_ground_towers_split_by_stiffness():
    m = _base("linked", modes=6)
    _tower(m, "A", 0.0, nst=2, fx=20.0)
    _tower(m, "B", 10.0, nst=2, tower="T2", fx=0.0)
    m.towers = [{"name": "T1", "grid_system": "GA", "base_elevation": 0.0},
                {"name": "T2", "grid_system": "GB", "base_elevation": 0.0}]
    for i in (1, 2):                       # link beams tagged tower T1
        for y in (0.0, 5.0):
            lb = m.add_member("beam", "BM", (6.0, y, H * i),
                              (10.0, y, H * i), story=f"A{i}",
                              uid=f"LINK{i}_{y}")
            lb.tower = "T1"
    m.validate()
    _cases(m)
    eng = OpenSeesEngine(m)
    asm = eng._build()
    # link-beam end nodes stay in tower T2 (majority vote)
    nt = __import__("skyframe.engine.towers", fromlist=["x"]).node_towers(
        eng, asm)
    t_b = [t for t, c in asm.struct_coords.items()
           if c == (10.0, 0.0, 3.0)][0]
    assert nt[t_b] == "T2"
    cr = eng.run_static("EQX")
    st = cr.story
    total = 20.0 + 40.0
    assert -cr.base["FX"] == pytest.approx(total, rel=1e-9)
    # identical towers: the unloaded one carries part of the load
    assert 0.0 < st["B1"]["shear_x"] < st["A1"]["shear_x"]
    assert st["A1"]["shear_x"] + st["B1"]["shear_x"] == \
        pytest.approx(total, rel=1e-8)
    # cut shears equal the reactions under each tower
    react_a = sum(-cr.reactions[t][0] for t in asm.support_tags
                  if asm.node_coords[t][0] < 8.0)
    assert st["A1"]["shear_x"] == pytest.approx(react_a, rel=1e-8)


# ----------------------------------------------------- auto lateral loads
def _elf_model():
    m = _base("elf")
    _tower(m, "A", 0.0, nst=3, dead=20.0, fx=0.0)
    _tower(m, "B", 30.0, nst=2, tower="T2", dead=35.0, fx=0.0, bx=8.0,
           by=4.0)
    m.towers = [{"name": "T1", "grid_system": "GA", "base_elevation": 0.0},
                {"name": "T2", "grid_system": "GB", "base_elevation": 0.0}]
    m.validate()
    return m


def _hand_w(m, tower):
    """Hand story weights: beam UDLs only (mass source DEAD)."""
    out = {}
    for s in m.stories:
        if ct.story_tower(m, s) != tower:
            continue
        w = 0.0
        for u in m.patterns["DEAD"].member_udls:
            mm = m._member(u.member_uid)
            if mm.story == s.name:
                w += u.w * mm.length
        out[s.name] = w
    return out


def test_elf_user_coefficient_hand_check_per_tower():
    m = _elf_model()
    C = 0.12
    sm = al.compute(m, "user_coefficient", C=C, k=1.0, direction="X")
    assert set(sm["towers"]) == {"T1", "T2"}
    rows = {r["story"]: r for r in sm["stories"]}
    for tw in ("T1", "T2"):
        w = _hand_w(m, tw)
        W = sum(w.values())
        el = {s.name: s.elevation for s in m.stories}
        V = C * W
        assert sm["towers"][tw]["V"] == pytest.approx(V, rel=1e-12)
        assert sm["towers"][tw]["W"] == pytest.approx(W, rel=1e-12)
        den = sum(w[n] * el[n] for n in w)
        for n in w:
            assert rows[n]["tower"] == tw
            assert rows[n]["F"] == pytest.approx(V * w[n] * el[n] / den,
                                                 rel=1e-12)
    assert sm["V"] == pytest.approx(sum(sm["towers"][t]["V"]
                                        for t in ("T1", "T2")))
    pat = al.generate(m, "user_coefficient", name="UC", C=C, k=1.0,
                      direction="X")
    assert [sf.story for sf in pat.story_forces] == \
        ["A1", "A2", "A3", "B1", "B2"]
    # analysed: each tower's base shear is its own V
    m.add_case("UC", {"UC": 1.0})
    res = OpenSeesEngine(m).run().to_dict()
    for tw in ("T1", "T2"):
        assert res["towers"][tw]["base_shear"]["UC"]["FX"] == \
            pytest.approx(sm["towers"][tw]["V"], rel=1e-8)


def test_elf_asce7_22_per_tower_heights_and_period():
    m = _elf_model()
    sm = al.compute(m, "asce7_22", SDS=1.0, SD1=0.4, R=8.0, direction="X")
    # Ta = Ct hn^x per tower (hn = 9 m vs 6 m) -> different periods
    t1, t2 = sm["towers"]["T1"], sm["towers"]["T2"]
    assert t1["T"] != t2["T"]
    for tw, sub in (("T1", t1), ("T2", t2)):
        w = _hand_w(m, tw)
        assert sub["W"] == pytest.approx(sum(w.values()), rel=1e-12)
        k = sub["k"]
        el = {s.name: s.elevation for s in m.stories}
        den = sum(w[n] * el[n] ** k for n in w)
        for r in sub["stories"]:
            assert r["F"] == pytest.approx(
                sub["V"] * w[r["story"]] * el[r["story"]] ** k / den,
                rel=1e-12)


def test_legacy_elf_and_wind_and_notional_per_tower():
    m = _elf_model()
    pat = asce7_elf(m, 1.0, 0.4, 8.0, name="ELF")
    by = {sf.story: sf.fx for sf in pat.story_forces}
    for tw, names in (("T1", ["A1", "A2", "A3"]), ("T2", ["B1", "B2"])):
        w = _hand_w(m, tw)
        hn = 9.0 if tw == "T1" else 6.0
        Ta = 0.0466 * hn ** 0.9
        Cs = max(min(1.0 / 8.0, 0.4 / (Ta * 8.0)), 0.044)
        V = Cs * sum(w.values())
        assert sum(by[n] for n in names) == pytest.approx(V, rel=1e-12)
    wp = make_wind_pattern(m, "WX", "X", 35.0)
    from skyframe.core.builder import wind_qz
    wb = {sf.story: sf.fx for sf in wp.story_forces}
    # T2 plan width perpendicular to X = 4 m, top story tributary H/2
    assert wb["B2"] == pytest.approx(
        wind_qz(6.0, 35.0, "C", 1.0) * 1.3 * (H / 2.0) * 4.0, rel=1e-12)
    assert wb["A3"] == pytest.approx(
        wind_qz(9.0, 35.0, "C", 1.0) * 1.3 * (H / 2.0) * 5.0, rel=1e-12)
    npat = make_notional_pattern(m, "N", gravity_pattern="DEAD",
                                 coeff=0.002)
    nb = {sf.story: sf.fx for sf in npat.story_forces}
    w2 = _hand_w(m, "T2")
    assert nb["B1"] == pytest.approx(0.002 * w2["B1"], rel=1e-12)


# --------------------------------------------------------------- tables
def test_results_tables_tower_column_and_stiffness_chain(twin_res):
    m = _twin()
    res = twin_res.to_dict()
    t = compute_table("story_drifts", res, m)
    keys = [c["key"] for c in t["columns"]]
    assert keys[:2] == ["story", "tower"]
    assert {r["story"]: r["tower"] for r in t["rows"]}["B2"] == "T2"
    k = compute_table("story_stiffness", res, m, cases=["EQX"])
    rows = {r["story"]: r for r in k["rows"]}
    assert rows["B1"]["drift_x"] == pytest.approx(rows["A1"]["drift_x"],
                                                  rel=1e-9)
    assert rows["B1"]["stiff_x"] == pytest.approx(
        twin_res.story_stiffness["EQX"]["B1"]["kx"], rel=1e-9)
    # no towers: no tower column
    t0 = compute_table("story_drifts", res, _single())
    assert "tower" not in [c["key"] for c in t0["columns"]]


def test_story_definitions_post_recomputes_per_tower():
    d = {"towers": [{"name": "T1", "base_elevation": 0.0},
                    {"name": "T2", "base_elevation": 6.0}],
         "stories": [{"name": "A1", "height": 3.0},
                     {"name": "B1", "height": 4.0, "tower": "T2"},
                     {"name": "A2", "height": 3.5}]}
    assert ct.story_post_dict(d)
    assert [s["elevation"] for s in d["stories"]] == [3.0, 10.0, 6.5]
    assert not ct.story_post_dict({"stories": []})
