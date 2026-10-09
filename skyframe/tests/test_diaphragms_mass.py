"""Multiple diaphragms per story + ETABS additional mass (gap audit C4 + B13).

CONTRACT "Multiple diaphragms per story and additional mass".  Validation:
two independent towers on one story level (separate named diaphragms)
reproduce each tower solved alone (periods, displacements, accidental
torsion, P-Delta), per-diaphragm CM / drift; joint rotational inertia,
frame / shell additional mass and the lumped-at-story mass moment of
inertia against hand formulas; legacy models byte-identical.
"""

import json
import math
import warnings

import pytest

from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection,
                                 Material, MemberUDL, NodalMass,
                                 ShellSection, StoryForce)
from skyframe.engine.opensees_engine import OpenSeesEngine
from skyframe.core import tables

G = 9.80665
H = 3.0
A = 6.0

pytestmark = pytest.mark.filterwarnings("ignore")


# --------------------------------------------------------------------------- #
# model helpers
# --------------------------------------------------------------------------- #
def _base(n_stories=1) -> BuildingModel:
    m = BuildingModel(name="dia")
    m.add_material(Material("C", E=3.0e7, nu=0.2, unit_weight=25.0))
    m.add_section(FrameSection.rectangular("CA", "C", 0.4, 0.4))
    m.add_section(FrameSection.rectangular("CB", "C", 0.5, 0.5))
    m.add_section(FrameSection.rectangular("B", "C", 0.3, 0.6))
    m.add_shell_section(ShellSection("SL", "C", 0.2))
    m.set_stories([H] * n_stories, [f"S{i + 1}" for i in range(n_stories)])
    m.mass_source = {"DEAD": 1.0}
    m.pattern("DEAD", "dead")
    return m


def _tower(m, x0, sec, q, uid, dia="", n_stories=1, behavior="membrane"):
    pts = [(x0, 0.0), (x0 + A, 0.0), (x0 + A, A), (x0, A)]
    for k in range(n_stories):
        z0, z1 = k * H, (k + 1) * H
        sn = f"S{k + 1}"
        for i, (x, y) in enumerate(pts):
            m.add_member("column", sec, (x, y, z0), (x, y, z1), story=sn,
                         uid=f"{uid}C{i}_{k}")
        for i in range(4):
            p, p2 = pts[i], pts[(i + 1) % 4]
            m.add_member("beam", "B", (p[0], p[1], z1), (p2[0], p2[1], z1),
                         story=sn, uid=f"{uid}B{i}_{k}")
        r = m.add_shell("slab", behavior, "SL",
                        [(x, y, z1) for x, y in pts], story=sn,
                        uid=f"{uid}S{k}", mesh_size=3.0)
        r.diaphragm = dia
        m.patterns["DEAD"].area_loads.append(AreaLoad(r.uid, q))


def _two(named=True, n_stories=1, types=("rigid", "rigid")):
    m = _base(n_stories)
    if named:
        m.diaphragms = {"DA": {"type": types[0]}, "DB": {"type": types[1]}}
    _tower(m, 0.0, "CA", 5.0, "A", "DA" if named else "", n_stories)
    _tower(m, 20.0, "CB", 8.0, "B", "DB" if named else "", n_stories)
    m.num_modes = 6 * n_stories
    m.validate()
    return m


def _alone(which, n_stories=1):
    m = _base(n_stories)
    if which == "A":
        _tower(m, 0.0, "CA", 5.0, "A", "", n_stories)
    else:
        _tower(m, 20.0, "CB", 8.0, "B", "", n_stories)
    m.num_modes = 3 * n_stories
    return m


def _eq(m, fx=100.0, fy=0.0, acc=False, story="S1", name="EQX"):
    p = m.pattern(name, "seismic")
    p.story_forces.append(StoryForce(story, fx, fy))
    p.accidental_torsion = acc
    m.add_case(name, {name: 1.0})


def _mass_sum(asm, dof):
    return sum(v for (t, d), v in asm.mass_map.items() if d == dof)


# --------------------------------------------------------------------------- #
# two independent towers
# --------------------------------------------------------------------------- #
def test_two_towers_periods_equal_each_tower_alone():
    pa = OpenSeesEngine(_alone("A")).run_modal().periods
    pb = OpenSeesEngine(_alone("B")).run_modal().periods
    pt = OpenSeesEngine(_two()).run_modal().periods
    ref = sorted(pa + pb, reverse=True)
    assert len(pt) == 6
    for a, b in zip(pt, ref):
        assert a == pytest.approx(b, rel=1e-9)


def test_shared_diaphragm_couples_towers():
    ps = OpenSeesEngine(_two(named=False)).run_modal().periods
    pa = OpenSeesEngine(_alone("A")).run_modal().periods
    pb = OpenSeesEngine(_alone("B")).run_modal().periods
    # one story master = 3 dofs only, and the fundamental differs from both
    assert len(ps) == 3
    assert all(abs(ps[0] - t) / t > 1e-3 for t in pa + pb)


def test_two_towers_masters_and_mass_per_diaphragm():
    m = _two()
    eng = OpenSeesEngine(m)
    asm = eng._build()
    ms = asm.dia["masters"]
    assert set(ms) == {("S1", "DA"), ("S1", "DB")}
    assert "S1" not in asm.masters          # several diaphragms
    mA = asm.mass_map[(ms[("S1", "DA")], 1)]
    mB = asm.mass_map[(ms[("S1", "DB")], 1)]
    assert mA == pytest.approx(5.0 * A * A / G, rel=1e-12)
    assert mB == pytest.approx(8.0 * A * A / G, rel=1e-12)
    # J = m (a^2 + b^2) / 12 per diaphragm (its own extents)
    assert asm.mass_map[(ms[("S1", "DA")], 6)] == pytest.approx(
        mA * (A * A + A * A) / 12.0, rel=1e-12)
    # master at the diaphragm centre of mass
    assert asm.node_coords[ms[("S1", "DB")]][:2] == pytest.approx((23.0, 3.0))


def test_static_displacement_and_accidental_torsion_per_diaphragm():
    m = _two()
    _eq(m, acc=True)
    eng = OpenSeesEngine(m)
    res = eng.run()
    d = res.to_dict()["diaphragms"]
    props = d["stories"]["S1"]
    mA, mB = props["DA"]["mass"], props["DB"]["mass"]
    row = d["cases"]["EQX"]["S1"]["DA"]
    # tower A alone under its mass share of the story force
    a = _alone("A")
    _eq(a, fx=100.0 * mA / (mA + mB), acc=True)
    ea = OpenSeesEngine(a)
    ra = ea.run_static("EQX")
    da = ra.node_disp[ea._asm.masters["S1"]]
    assert row["ux"] == pytest.approx(da[0], rel=1e-9)
    assert row["rz"] == pytest.approx(da[5], rel=1e-9)
    # hand: rz = Mz / k_theta, Mz = F_A * ecc * Ly_A (diaphragm extent)
    FA = 100.0 * mA / (mA + mB)
    assert row["rz"] == pytest.approx(
        FA * 0.05 * A / props["DA"]["k_theta"], rel=1e-8)
    rowb = d["cases"]["EQX"]["S1"]["DB"]
    FB = 100.0 * mB / (mA + mB)
    assert rowb["rz"] == pytest.approx(
        FB * 0.05 * A / props["DB"]["k_theta"], rel=1e-8)


def test_story_force_split_by_diaphragm_mass_sums_to_base_shear():
    m = _two()
    _eq(m)
    res = OpenSeesEngine(m).run_static("EQX")
    assert res.base["FX"] == pytest.approx(-100.0, rel=1e-9)


def test_cm_per_diaphragm_hand():
    m = _two()
    # extra 10 kN/m on tower A's y=0 beam: CM_y = (180*3 + 60*0)/240 = 2.25
    m.patterns["DEAD"].member_udls.append(MemberUDL("AB0_0", 10.0))
    _eq(m)
    d = OpenSeesEngine(m).run().to_dict()["diaphragms"]["stories"]["S1"]
    assert d["DA"]["cm_x"] == pytest.approx(3.0, abs=1e-9)
    assert d["DA"]["cm_y"] == pytest.approx(2.25, abs=1e-9)
    assert d["DB"]["cm_x"] == pytest.approx(23.0, abs=1e-9)
    assert d["DA"]["mass"] == pytest.approx(240.0 / G, rel=1e-12)


def test_drift_per_diaphragm_two_stories():
    m = _two(n_stories=2)
    _eq(m, story="S2")
    eng = OpenSeesEngine(m)
    res = eng.run()
    rows = res.to_dict()["diaphragms"]["cases"]["EQX"]
    for dn in ("DA", "DB"):
        u1, u2 = rows["S1"][dn]["ux"], rows["S2"][dn]["ux"]
        assert rows["S1"][dn]["drift_x"] == pytest.approx(u1 / H, rel=1e-12)
        assert rows["S2"][dn]["drift_x"] == pytest.approx((u2 - u1) / H,
                                                          rel=1e-9)
    # two-story periods equal the towers alone
    pa = OpenSeesEngine(_alone("A", 2)).run_modal().periods
    pb = OpenSeesEngine(_alone("B", 2)).run_modal().periods
    pt = OpenSeesEngine(m).run_modal().periods
    for a, b in zip(pt, sorted(pa + pb, reverse=True)):
        assert a == pytest.approx(b, rel=1e-8)


def test_pdelta_non_iterative_mass_per_diaphragm():
    m = _two()
    m.pdelta_options = {"method": "non_iterative_mass"}
    _eq(m)
    eng = OpenSeesEngine(m)
    res = eng.run()
    dd = res.to_dict()
    mA = dd["diaphragms"]["stories"]["S1"]["DA"]["mass"]
    mB = dd["diaphragms"]["stories"]["S1"]["DB"]["mass"]
    PA = dd["pdelta"]["diaphragm_P"]["S1"]["DA"]
    assert PA == pytest.approx(G * mA, rel=1e-12)
    a = _alone("A")
    a.pdelta_options = {"method": "non_iterative_mass"}
    _eq(a, fx=100.0 * mA / (mA + mB))
    ea = OpenSeesEngine(a)
    ra = ea.run_static("EQX")
    ux_alone = ra.node_disp[ea._asm.masters["S1"]][0]
    assert dd["diaphragms"]["cases"]["EQX"]["S1"]["DA"]["ux"] == \
        pytest.approx(ux_alone, rel=1e-9)


def test_irregularity_story_level_uses_max_over_diaphragms():
    m = _two()
    m.patterns["DEAD"].member_udls.append(MemberUDL("AB0_0", 30.0))
    _eq(m, acc=True)
    res = OpenSeesEngine(m).run()
    d = res.to_dict()
    rows = d["diaphragms"]["cases"]["EQX"]["S1"]
    trx = max(r["tors_ratio_x"] for r in rows.values())
    assert trx > 1.0
    assert d["irregularity"]["EQX"]["S1"]["tors_ratio_x"] == trx


def test_semi_rigid_and_rigid_mix_runs():
    m = _base()
    m.diaphragms = {"DA": {"type": "rigid"}, "DB": {"type": "semi_rigid"}}
    _tower(m, 0.0, "CA", 5.0, "A", "DA")
    _tower(m, 20.0, "CB", 8.0, "B", "DB", behavior="shell")
    _eq(m, acc=True)
    m.validate()
    eng = OpenSeesEngine(m)
    res = eng.run()
    d = res.to_dict()["diaphragms"]
    assert d["stories"]["S1"]["DB"]["master"] is None
    assert d["stories"]["S1"]["DA"]["master"] is not None
    assert res.cases["EQX"].base["FX"] == pytest.approx(-100.0, rel=1e-9)
    assert d["cases"]["EQX"]["S1"]["DB"]["ux"] > 0.0


def test_named_single_diaphragm_matches_legacy():
    legacy = _alone("A")
    named = _alone("A")
    named.diaphragms = {"D1": {"type": "rigid"}}
    named.shells[0].diaphragm = "D1"
    p1 = OpenSeesEngine(legacy).run_modal().periods
    e2 = OpenSeesEngine(named)
    p2 = e2.run_modal().periods
    assert p2 == pytest.approx(p1, rel=1e-10)
    assert e2._asm.masters["S1"] in e2._asm.dia["masters"].values()


def test_tables_rows_per_diaphragm():
    m = _two()
    _eq(m)
    eng = OpenSeesEngine(m)
    res = eng.run().to_dict()
    ctx = tables.engine_context(eng)
    cen = tables.compute_table("centers_mass_rigidity", res, m, ctx)["rows"]
    assert [r["diaphragm"] for r in cen] == ["DA", "DB"]
    assert cen[0]["mass"] == pytest.approx(5.0 * A * A / G, rel=1e-12)
    assert cen[1]["cm_x"] == pytest.approx(23.0)
    disp = tables.compute_table("diaphragm_cm_displacements", res, m,
                                ctx, cases=["EQX"])["rows"]
    assert {r["diaphragm"] for r in disp} == {"DA", "DB"}
    da = [r for r in disp if r["diaphragm"] == "DA"][0]
    assert da["ux"] == pytest.approx(
        res["diaphragms"]["cases"]["EQX"]["S1"]["DA"]["ux"], rel=1e-12)


# --------------------------------------------------------------------------- #
# joint rotational inertia / additional mass
# --------------------------------------------------------------------------- #
def _bare_tower(named=False):
    """One tower, no derived mass (explicit masses only)."""
    m = _base()
    m.mass_source = {}
    _tower(m, 0.0, "CA", 0.0, "A", "D1" if named else "")
    if named:
        m.diaphragms = {"D1": {"type": "rigid"}}
    m.patterns["DEAD"].area_loads.clear()
    m.mass_options = dict(m.mass_options, self_mass=False, patterns=False)
    return m


def test_joint_rotational_inertia_torsional_period_hand():
    J = 50.0
    m = _bare_tower(named=True)
    m.nodal_masses.append(NodalMass((0.0, 0.0, H), mrz=J))
    m.num_modes = 1
    m.validate()
    eng = OpenSeesEngine(m)
    T = eng.run_modal().periods[0]
    _eq(m)
    kth = eng.run().diaphragms["stories"]["S1"]["D1"]["k_theta"]
    assert T == pytest.approx(2.0 * math.pi * math.sqrt(J / kth), rel=1e-8)
    # the slave's RZ inertia lives on the master
    mt = eng._build().masters["S1"]
    assert eng._asm.mass_map[(mt, 6)] == pytest.approx(J)


def test_joint_rotational_inertia_free_node_rx_ry():
    m = _bare_tower()
    m.rigid_diaphragms = False
    m.nodal_masses.append(NodalMass((0.0, 0.0, H), mx=1.0, mrx=2.0,
                                    mry=3.0, mrz=4.0))
    eng = OpenSeesEngine(m)
    asm = eng._build()
    t = eng._find_node(asm, (0.0, 0.0, H))
    assert [asm.mass_map.get((t, d), 0.0) for d in (1, 4, 5, 6)] == \
        [1.0, 2.0, 3.0, 4.0]


def _story_mass_tower(M0=20.0):
    m = _bare_tower()
    m.story_masses["S1"] = M0
    m.num_modes = 3
    return m


def test_frame_additional_mass_total_and_period():
    M0 = 20.0
    base = _story_mass_tower(M0)
    T0 = OpenSeesEngine(base).run_modal().periods
    m = _story_mass_tower(M0)
    q = 0.4
    for mem in m.members:
        if mem.kind == "beam":
            mem.additional_mass = q
    eng = OpenSeesEngine(m)
    asm = eng._build()
    add = q * 4 * A
    assert _mass_sum(asm, 1) == pytest.approx(M0 + add, rel=1e-12)
    T1 = eng.run_modal().periods
    # translational X/Y modes: T ~ sqrt(M)
    tx0 = sorted(T0)[-1]
    tx1 = sorted(T1)[-1]
    assert tx1 / tx0 == pytest.approx(math.sqrt((M0 + add) / M0), rel=1e-8)
    # beam line masses on the perimeter: J = sum m (d^2 + L^2/12)
    mt = asm.masters["S1"]
    Jb = 4 * q * A * ((A / 2) ** 2 + A * A / 12.0)
    J0 = M0 * (2 * A * A) / 12.0
    assert asm.mass_map[(mt, 6)] == pytest.approx(J0 + Jb, rel=1e-12)


def test_frame_additional_mass_distributed_mode_same_total():
    m = _story_mass_tower()
    for mem in m.members:
        if mem.kind == "column":
            mem.additional_mass = 0.5
            mem.additional_mass_mode = "distributed"
    eng = OpenSeesEngine(m)
    asm = eng._build()
    # column top halves go to the diaphragm (bottom halves sit on supports)
    assert _mass_sum(asm, 1) == pytest.approx(20.0 + 4 * 0.5 * H,
                                              rel=1e-12)
    mt = asm.masters["S1"]
    assert asm.mass_map[(mt, 1)] == pytest.approx(20.0 + 2 * 0.5 * H,
                                                  rel=1e-12)


def test_shell_additional_mass_total_period_and_plate_inertia():
    M0 = 20.0
    base = _story_mass_tower(M0)
    T0 = sorted(OpenSeesEngine(base).run_modal().periods)
    m = _story_mass_tower(M0)
    q = 0.3
    m.shells[0].additional_mass = q
    eng = OpenSeesEngine(m)
    asm = eng._build()
    mq = q * A * A
    mt = asm.masters["S1"]
    assert asm.mass_map[(mt, 1)] == pytest.approx(M0 + mq, rel=1e-12)
    # lumped at the story: rectangle about its centroid m (a^2+b^2)/12
    assert asm.mass_map[(mt, 6)] == pytest.approx(
        (M0 + mq) * (A * A + A * A) / 12.0, rel=1e-12)
    T1 = sorted(eng.run_modal().periods)
    assert T1[-1] / T0[-1] == pytest.approx(math.sqrt((M0 + mq) / M0),
                                            rel=1e-8)


def test_plate_inertia_rectangle_offset_master():
    from skyframe.engine.diaphragms import polygon_polar_moment
    a, b = 4.0, 2.0
    area, ip = polygon_polar_moment([(0, 0), (a, 0), (a, b), (0, b)],
                                    a / 2, b / 2)
    assert area == pytest.approx(a * b)
    assert ip == pytest.approx(a * b * (a * a + b * b) / 12.0)
    # parallel axis about a corner
    _a, ip0 = polygon_polar_moment([(0, 0), (a, 0), (a, b), (0, b)], 0, 0)
    assert ip0 == pytest.approx(ip + a * b * (a * a + b * b) / 4.0)


def test_additional_mass_respects_mass_options():
    m = _story_mass_tower()
    q = 0.3
    m.shells[0].additional_mass = q
    m.shells[0].behavior = "shell"
    m.mass_options = dict(m.mass_options, include_vertical=True,
                          lump_at_stories=False)
    eng = OpenSeesEngine(m)
    asm = eng._build()
    mt = asm.masters["S1"]
    # lateral additional mass stays at the slab nodes (not on the master)
    assert asm.mass_map[(mt, 1)] == pytest.approx(20.0, rel=1e-12)
    assert _mass_sum(asm, 1) == pytest.approx(20.0 + q * A * A, rel=1e-12)
    assert _mass_sum(asm, 3) == pytest.approx(20.0 + q * A * A, rel=1e-12)
    m2 = _story_mass_tower()
    m2.shells[0].additional_mass = q
    m2.mass_options = dict(m2.mass_options, include_lateral=False,
                           include_vertical=True)
    asm2 = OpenSeesEngine(m2)._build()
    assert _mass_sum(asm2, 1) == 0.0
    assert _mass_sum(asm2, 3) == pytest.approx(20.0 + q * A * A, rel=1e-12)


# --------------------------------------------------------------------------- #
# legacy byte-identity, round trip, validation
# --------------------------------------------------------------------------- #
def test_legacy_model_serialization_and_results_unchanged():
    m = _alone("A")
    _eq(m, acc=True)
    d = m.to_dict()
    for k in ("diaphragms", "joint_diaphragms"):
        assert k not in d
    assert "additional_mass" not in d["members"][0]
    assert "diaphragm" not in d["shells"][0]
    m.nodal_masses.append(NodalMass((0.0, 0.0, H), mx=1.0))
    assert set(m.to_dict()["nodal_masses"][0]) == {"point", "mx", "my", "mz"}
    r1 = OpenSeesEngine(m).run().to_dict()
    assert "diaphragms" not in r1
    m2 = BuildingModel.from_dict(json.loads(json.dumps(m.to_dict())))
    r2 = OpenSeesEngine(m2).run().to_dict()
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)


def test_round_trip_new_fields():
    m = _two()
    m.joint_diaphragms = [{"point": [0.0, 0.0, H], "diaphragm": "DA"}]
    m.members[4].additional_mass = 0.2
    m.members[4].additional_mass_mode = "distributed"
    m.shells[0].additional_mass = 0.1
    m.nodal_masses.append(NodalMass((0.0, 0.0, H), mrz=3.0, mrx=1.0))
    d = json.loads(json.dumps(m.to_dict()))
    m2 = BuildingModel.from_dict(d)
    assert m2.diaphragms == m.diaphragms
    assert m2.joint_diaphragms == m.joint_diaphragms
    assert m2.members[4].additional_mass == 0.2
    assert m2.members[4].additional_mass_mode == "distributed"
    assert m2.shells[0].additional_mass == 0.1
    assert m2.shells[0].diaphragm == "DA"
    assert (m2.nodal_masses[0].mrz, m2.nodal_masses[0].mrx) == (3.0, 1.0)
    assert m2.to_dict() == m.to_dict()


@pytest.mark.parametrize("mutate,msg", [
    (lambda m: m.diaphragms.update({"DX": {"type": "flexible"}}), "type"),
    (lambda m: setattr(m.shells[0], "diaphragm", "NOPE"), "unknown"),
    (lambda m: m.joint_diaphragms.append(
        {"point": [0.0, 0.0, 1.0], "diaphragm": "DA"}), "story elevation"),
    (lambda m: m.joint_diaphragms.append(
        {"point": [0.0, 0.0, H], "diaphragm": "ZZ"}), "unknown"),
    (lambda m: setattr(m.members[0], "additional_mass", -1.0), ">= 0"),
    (lambda m: setattr(m.members[0], "additional_mass_mode", "x"), "mode"),
    (lambda m: setattr(m.shells[0], "additional_mass", float("nan")), ">= 0"),
    (lambda m: m.nodal_masses.append(NodalMass((0, 0, H), mrz=-2.0)), "mrz"),
])
def test_validation_errors(mutate, msg):
    m = _two()
    mutate(m)
    with pytest.raises(ValueError, match=msg):
        m.validate()


def test_rs_case_runs_with_two_diaphragms():
    m = _two()
    m.add_rs_case("RSX", "X", [[0.0, 1.0], [5.0, 1.0]])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = OpenSeesEngine(m).run().to_dict()
    assert "RSX" in res["diaphragms"].get("rs_cases", {})
    rows = res["diaphragms"]["rs_cases"]["RSX"]["S1"]
    assert rows["DA"]["ux"] > 0.0 and rows["DB"]["ux"] > 0.0
