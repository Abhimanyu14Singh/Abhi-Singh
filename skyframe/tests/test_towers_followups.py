"""C9 multiple towers — follow-ups (CONTRACT "Multiple towers — follow-ups").

Pins: per-tower time-history / FNA story shear (element cut, per step),
the story-shear history output request, response-spectrum per-tower
shears against independent static solves, the Story Definitions tower
column (model tables), the pushover / nonlinear static control tower,
named diaphragms split per tower, and staged construction across towers
(per_story and user-defined stages).  Every legacy path stays untouched.
"""
import json
import math
import warnings

import pytest

from skyframe.core import modeltables as mt
from skyframe.core import towers_ext as cx
from skyframe.core.builder import quick_building
from skyframe.core.groups import normalize_group
from skyframe.core.model import (BuildingModel, NodalLoad, StagedCase,
                                 StoryForce)
from skyframe.engine.opensees_engine import OpenSeesEngine

from test_towers import H, _base, _cases, _podium, _single, _tower, _twin

warnings.simplefilter("ignore")

ACC = [0.0, 0.1, -0.2, 0.15, 0.0, -0.05] * 3 + [0.0]


def _close(a, b, rel=1e-8, abs_=1e-10):
    return math.isclose(a, b, rel_tol=rel, abs_tol=abs_)


def _one_tower(modes=6):
    m = _single()
    m.num_modes = modes
    m.towers = [{"name": "T1", "grid_system": "GA", "base_elevation": 0.0}]
    m.validate()
    return m


def _linked():
    m = _base("linked", modes=6)
    _tower(m, "A", 0.0, nst=2, fx=20.0)
    _tower(m, "B", 10.0, nst=2, tower="T2", fx=0.0)
    m.towers = [{"name": "T1", "grid_system": "GA", "base_elevation": 0.0},
                {"name": "T2", "grid_system": "GB", "base_elevation": 0.0}]
    for i in (1, 2):
        for y in (0.0, 5.0):
            lb = m.add_member("beam", "BM", (6.0, y, H * i),
                              (10.0, y, H * i), story=f"A{i}",
                              uid=f"LINK{i}_{y}")
            lb.tower = "T1"
    m.validate()
    return _cases(m)


def _th(m, name="THX", **kw):
    opts = dict(accel=ACC, dt=0.02, damping_model="modal",
                output_requests={"story_shears": True})
    opts.update(kw)
    m.add_th_case(name, "X", **opts)
    return m


# ------------------------------------------------- TH per-tower shear
@pytest.fixture(scope="module")
def th_runs():
    one = OpenSeesEngine(_th(_one_tower())).run_time_history("THX")
    twin = OpenSeesEngine(_th(_twin())).run_time_history("THX")
    return one, twin


def test_th_twin_towers_equal_single_tower(th_runs):
    one, twin = th_runs
    h1 = one.extra["story_shear_history"]
    h2 = twin.extra["story_shear_history"]
    assert h1["method"] == h2["method"] == "element_cut"
    assert h2["story_tower"]["B2"] == "T2"
    for i in (1, 2, 3):
        a = h1["VX"][f"A{i}"]
        assert len(a) == len(one.t)
        assert max(abs(v) for v in a) > 0.1
        for nm in (f"A{i}", f"B{i}"):
            for x, y in zip(h2["VX"][nm], a):
                assert _close(x, y, 1e-8, 1e-12)
            pk = twin.peaks["story"][nm]
            assert _close(pk["shear_x"], one.peaks["story"][f"A{i}"]
                          ["shear_x"], 1e-8, 1e-12)
            assert _close(pk["shear_x"], max(abs(v) for v in a), 1e-12)
    tw = twin.to_dict()["towers"]
    assert set(tw) == {"T1", "T2"}
    assert tw["T2"]["stories"] == ["B1", "B2", "B3"]
    assert _close(tw["T2"]["base_shear"]["FX"],
                  one.peaks["story"]["A1"]["shear_x"])
    assert twin.to_dict()["story_shear_method"] == "element_cut"


def test_th_cut_equals_base_reaction_and_legacy_inertia():
    """Single tower: the lowest story's cut shear is minus the base
    reaction at every step; with (almost) no damping it also equals the
    legacy inertia-equilibrium shear at every level."""
    kw = dict(damping_model="rayleigh", damping=1e-9)
    r_cut = OpenSeesEngine(_th(_one_tower(), **kw)).run_time_history("THX")
    r_leg = OpenSeesEngine(_th(_single(), **kw)).run_time_history("THX")
    cut = r_cut.extra["story_shear_history"]
    leg = r_leg.extra["story_shear_history"]
    assert leg["method"] == "inertia" and "story_tower" not in leg
    for v, b in zip(cut["VX"]["A1"], r_cut.base_FX):
        assert v == pytest.approx(-b, rel=1e-9, abs=1e-9)
    for s in ("A1", "A2", "A3"):
        ref = max(abs(v) for v in leg["VX"][s])
        for a, b in zip(cut["VX"][s], leg["VX"][s]):
            assert abs(a - b) <= 1e-6 * ref
    # legacy peaks (inertia) untouched without towers
    assert "towers" not in r_leg.to_dict()
    assert "story_shear_method" not in r_leg.to_dict()


def test_th_with_gravity_stage_is_the_increment():
    kw = dict(gravity={"DEAD": 1.0})
    a = OpenSeesEngine(_th(_one_tower(), **kw)).run_time_history("THX")
    b = OpenSeesEngine(_th(_one_tower())).run_time_history("THX")
    for s in ("A1", "A3"):
        for x, y in zip(a.extra["story_shear_history"]["VX"][s],
                        b.extra["story_shear_history"]["VX"][s]):
            assert x == pytest.approx(y, rel=1e-7, abs=1e-9)


def test_th_link_beams_tower_shears_sum_to_base_shear():
    m = _th(_linked(), damping_model="rayleigh", damping=0.05)
    r = OpenSeesEngine(m).run_time_history("THX")
    h = r.extra["story_shear_history"]["VX"]
    for k, b in enumerate(r.base_FX):
        assert h["A1"][k] + h["B1"][k] == pytest.approx(-b, rel=1e-8,
                                                        abs=1e-9)
    assert max(abs(v) for v in h["B1"]) > 0.0


def test_th_podium_lowest_story_is_total_base_shear():
    m = _th(_podium(), damping_model="rayleigh", damping=0.05)
    r = OpenSeesEngine(m).run_time_history("THX")
    h = r.extra["story_shear_history"]["VX"]
    for k, b in enumerate(r.base_FX):
        assert h["P1"][k] == pytest.approx(-b, rel=1e-8, abs=1e-9)
    assert r.peaks["story"]["P1"]["shear_x"] == pytest.approx(
        r.peaks["base"]["FX"], rel=1e-8)


def test_story_shears_request_round_trip_and_validation():
    m = _th(_single(), output_requests={"story_shears": True,
                                        "hinges": False})
    d = m.to_dict()
    assert d["th_cases"]["THX"]["output_requests"] == {
        "hinges": False, "story_shears": True}
    assert BuildingModel.from_dict(json.loads(json.dumps(d))).to_dict() == d
    with pytest.raises(ValueError, match="story_shears"):
        _th(_single(), name="BAD", output_requests={"story_shears": 1})
    # only the history requested: no plot_functions block
    r = OpenSeesEngine(_th(_single(), name="T2",
                           output_requests={"story_shears": True})
                       ).run_time_history("T2").to_dict()
    assert "plot_functions" not in r and "story_shear_history" in r


# ------------------------------------------------------------ FNA
def test_fna_twin_equals_single_and_direct_integration():
    """Modal damping for twin vs single (the Rayleigh fit is anchored on
    modes 1 and 3, which differ between the two models); Rayleigh for FNA
    vs direct integration of the single tower (all 9 modes retained)."""
    m1 = _th(_one_tower(modes=9))
    m2 = _th(_twin())
    m2.num_modes = 18
    f1 = OpenSeesEngine(m1).run_fna("THX")
    f2 = OpenSeesEngine(m2).run_fna("THX")
    kw = dict(damping_model="rayleigh", damping=0.05)
    m3 = _th(_one_tower(modes=9), **kw)
    f3 = OpenSeesEngine(m3).run_fna("THX")
    d3 = OpenSeesEngine(m3).run_time_history("THX")
    h1 = f1.extra["story_shear_history"]
    assert h1["method"] == "element_cut"
    for i in (1, 2, 3):
        a = h1["VX"][f"A{i}"]
        ref = max(abs(v) for v in a)
        for nm in (f"A{i}", f"B{i}"):
            for x, y in zip(f2.extra["story_shear_history"]["VX"][nm], a):
                assert abs(x - y) <= 1e-8 * ref
        # all modes retained: FNA == direct integration (solver precision)
        a3 = f3.extra["story_shear_history"]["VX"][f"A{i}"]
        for x, y in zip(a3, d3.extra["story_shear_history"]["VX"][f"A{i}"]):
            assert abs(x - y) <= 1e-6 * ref
    assert f2.to_dict()["towers"]["T2"]["base_shear"]["FX"] == \
        pytest.approx(f1.peaks["story"]["A1"]["shear_x"], rel=1e-8)


def test_fna_without_towers_unchanged_unless_requested():
    m = _single()
    m.add_th_case("THX", "X", accel=ACC, dt=0.02)
    a = OpenSeesEngine(m).run_fna("THX").to_dict()
    assert "story_shear_history" not in a and "towers" not in a
    m.th_cases["THX"].output_requests = {"story_shears": True}
    b = OpenSeesEngine(m).run_fna("THX").to_dict()
    h = b.pop("story_shear_history")
    assert h["method"] == "inertia"
    assert a == b
    for s in ("A1", "A2"):
        assert max(abs(v) for v in h["VX"][s]) == pytest.approx(
            a["peaks"]["story"][s]["shear_x"], rel=1e-15)


# ---------------------------------------------------- RS vs static
def test_response_spectrum_per_tower_matches_static_modal_loads():
    """Coupled towers (link beams), no rigid diaphragm: the RS per-tower
    story shear equals the SRSS of the static per-tower cut shears of
    every mode's equivalent lateral loads f_i = m phi_i Gamma_i Sa g,
    applied as ordinary nodal loads (independent static solves)."""
    m = _linked()
    m.rigid_diaphragms = False
    m.num_modes = 4
    sa = 0.6
    m.add_rs_case("RS1", "X", spectrum=[[0.0, sa], [10.0, sa]],
                  combo_method="SRSS")
    eng = OpenSeesEngine(m)
    rs = eng.run_response_spectrum("RS1").story
    modal = eng.run_modal()
    asm = eng._asm
    mass = {k: v for k, v in asm.mass_map.items() if k[1] in (1, 2, 3)}
    assert mass and len(modal.periods) == 4
    m2 = BuildingModel.from_dict(m.to_dict())
    for i in range(1, 5):
        phi = modal.shapes[i]
        gamma = sum(v * phi[t][0] for (t, d), v in mass.items()
                    if d == 1) \
            / sum(v * phi[t][d - 1] ** 2 for (t, d), v in mass.items())
        pat = m2.pattern(f"MODE{i}", "other")
        loads = {}
        for (t, d), v in mass.items():
            loads.setdefault(t, [0.0, 0.0, 0.0])[d - 1] += \
                v * phi[t][d - 1] * gamma * sa * 9.80665
        for t, f in loads.items():
            pat.nodal_loads.append(NodalLoad(tuple(asm.node_coords[t]),
                                             fx=f[0], fy=f[1], fz=f[2]))
        m2.add_case(f"MODE{i}", {f"MODE{i}": 1.0})
    eng2 = OpenSeesEngine(m2)
    st = [eng2.run_static(f"MODE{i}").story for i in range(1, 5)]
    for s in ("A1", "A2", "B1", "B2"):
        srss = math.sqrt(sum(x[s]["shear_x"] ** 2 for x in st))
        assert srss > 1.0
        assert rs[s]["shear_x"] == pytest.approx(srss, rel=1e-6)
    # identical linked towers share the modal shear equally
    assert rs["A1"]["shear_x"] == pytest.approx(rs["B1"]["shear_x"],
                                                rel=1e-8)


# ---------------------------------------------------- model tables
def test_story_table_tower_column_and_round_trip():
    m = _twin()
    d = m.to_dict()
    t = mt.get_table(d, "stories")
    keys = [c["key"] for c in t["columns"]]
    assert keys == ["name", "height", "elevation", "tower"]
    col = t["columns"][3]
    assert col["enum"] == ["T1", "T2"] and col["editable"]
    assert {r["name"]: r["tower"] for r in t["rows"]} == {
        "A1": "T1", "A2": "T1", "A3": "T1",
        "B1": "T2", "B2": "T2", "B3": "T2"}
    # unchanged rows -> identical model (towers preserved)
    model, errs, _ = mt.apply_tables(d, {"stories": t["rows"]})
    assert not errs and model.to_dict() == d
    # CSV round trip
    csv_text = mt.table_to_csv(d, "stories")
    assert csv_text.splitlines()[0] == "_id,name,height,elevation,tower"
    rows = mt.csv_to_rows(d, "stories", csv_text)
    model, errs, _ = mt.apply_tables(d, {"stories": rows})
    assert not errs and model.to_dict() == d
    # zip export / import keeps towers
    model, errs, _ = mt.import_zip(d, mt.export_zip(d))
    assert not errs and model.to_dict() == d


def test_story_table_edit_rename_height_and_move():
    m = _podium()
    d = m.to_dict()
    rows = mt.get_table(d, "stories")["rows"]
    for r in rows:
        if r["name"] == "B2":
            r["name"] = "B2x"
            r["height"] = 4.0
    model, errs, summ = mt.apply_tables(d, {"stories": rows})
    assert not errs, errs
    out = model.to_dict()
    tw = {t["name"]: t["stories"] for t in out["towers"]}
    assert tw["T2"] == ["B1", "B2x"]
    el = {s["name"]: s["elevation"] for s in out["stories"]}
    assert el["B2x"] == pytest.approx(6.0 + 3.0 + 4.0)
    assert el["A3"] == pytest.approx(15.0)
    # P1 / P2 belong to the default tower P (tagged on input here)
    assert {s["name"]: s.get("tower") for s in out["stories"]}["B1"] == "T2"
    # move story A3 (T1) to tower P: tags, lists and elevations follow
    rows = mt.get_table(out, "stories")["rows"]
    for r in rows:
        if r["name"] == "A3":
            r["tower"] = "P"
    model, errs, _ = mt.apply_tables(out, {"stories": rows})
    assert not errs, errs
    o2 = model.to_dict()
    tw2 = {t["name"]: t["stories"] for t in o2["towers"]}
    assert tw2["P"] == ["P1", "P2", "A3"] and tw2["T1"] == ["A1", "A2"]
    sa3 = [s for s in o2["stories"] if s["name"] == "A3"][0]
    # P is the default tower: its stories stay untagged (canonical form)
    assert "tower" not in sa3 and sa3["elevation"] == pytest.approx(9.0)
    bad = mt.get_table(out, "stories")["rows"]
    bad[0]["tower"] = "NOPE"
    _m, errs, _ = mt.apply_tables(out, {"stories": bad})
    assert errs and errs[0]["col"] == "tower"


def test_story_table_without_towers_unchanged():
    m = quick_building(stories=2, bays_x=1, bays_y=1)
    d = m.to_dict()
    t = mt.get_table(d, "stories")
    assert [c["key"] for c in t["columns"]] == ["name", "height",
                                                "elevation"]
    assert all("tower" not in r for r in t["rows"])
    assert mt.table_to_csv(d, "stories").splitlines()[0] == \
        "_id,name,height,elevation"
    # a stray tower cell is ignored without towers
    rows = [dict(r, tower="X") for r in t["rows"]]
    model, errs, _ = mt.apply_tables(d, {"stories": rows})
    assert not errs and model.to_dict() == d


# ------------------------------------------- pushover / NLS control
def test_pushover_control_tower_elastic_stiffness_by_hand():
    m = _podium()
    m.add_pushover_case("PO_T1", "X", steps=4, target_drift=0.001,
                        control_tower="T1")
    m.add_pushover_case("PO_DEF", "X", steps=4, target_drift=0.001)
    pat = m.pattern("UNIT", "other")
    pat.story_forces.append(StoryForce("A3", fx=1.0))
    m.add_case("UNIT", {"UNIT": 1.0})
    eng = OpenSeesEngine(m)
    flex = eng.run_static("UNIT").story["A3"]["ux"]
    po = eng.run_pushover("PO_T1")
    masters = OpenSeesEngine(m)._build().masters
    assert po.control["node"] == masters["A3"]
    assert po.control["height"] == pytest.approx(15.0)
    assert po.distribution["story_forces"] == {"A3": 1.0}
    for v, u in zip(po.base_shear, po.roof_disp):
        assert v / u == pytest.approx(1.0 / flex, rel=1e-6)
    assert po.roof_disp[-1] == pytest.approx(0.001 * 15.0, rel=1e-9)
    # default unchanged: the last story of `stories` (B2)
    pd = eng.run_pushover("PO_DEF")
    assert pd.control["node"] == masters["B2"]
    assert pd.control["height"] == pytest.approx(12.0)
    assert "control_tower" not in m.pushover_cases["PO_DEF"].to_dict()
    d = m.to_dict()
    assert d["pushover_cases"]["PO_T1"]["control_tower"] == "T1"
    assert BuildingModel.from_dict(json.loads(json.dumps(d))).to_dict() == d


@pytest.mark.parametrize("kw, msg", [
    (dict(control_tower="NOPE"), "unknown tower"),
    (dict(control_tower="T1", control_story="A1"), "not both"),
    (dict(control_tower="T1", control_point=[0.0, 0.0, 3.0]), "not both"),
])
def test_pushover_control_tower_validation(kw, msg):
    m = _twin()
    with pytest.raises(ValueError, match=msg):
        m.add_pushover_case("PO", "X", **kw)


def test_control_tower_needs_towers():
    with pytest.raises(ValueError, match="needs model.towers"):
        _single().add_pushover_case("PO", "X", control_tower="T1")
    with pytest.raises(ValueError, match="needs model.towers"):
        _single().add_nonlinear_static_case(
            "N", [{"pattern": "EQX", "scale": 1.0}], control_tower="T1")


def test_nonlinear_static_control_tower():
    m = _twin()
    m.add_nonlinear_static_case("N", [{"pattern": "EQX", "scale": 1.0}],
                                steps=2, control_tower="T2")
    m.add_nonlinear_static_case("N0", [{"pattern": "EQX", "scale": 1.0}],
                                steps=2)
    d = m.to_dict()
    assert d["nonlinear_static_cases"]["N"]["control_tower"] == "T2"
    assert "control_tower" not in d["nonlinear_static_cases"]["N0"]
    assert BuildingModel.from_dict(json.loads(json.dumps(d))).to_dict() == d
    eng = OpenSeesEngine(m)
    r = eng.run_nonlinear_static("N")
    masters = OpenSeesEngine(m)._build().masters
    mon = r.history["monitored"]
    assert mon["node"] == masters["B3"]
    ux = eng.run_static("EQX").story["B3"]["ux"]
    assert r.history["disp"][-1] == pytest.approx(ux, rel=1e-8)
    with pytest.raises(ValueError, match="unknown tower"):
        m.add_nonlinear_static_case("N2", [{"pattern": "EQX",
                                            "scale": 1.0}],
                                    control_tower="X")


# --------------------------------------------- named diaphragms
def _corner_pts(ox, z):
    return [[ox + x, y, z] for x in (0.0, 6.0) for y in (0.0, 5.0)]


def test_named_diaphragm_is_split_per_tower():
    base = OpenSeesEngine(_twin()).run_modal().periods
    m = _twin()
    m.diaphragms = {"DA": {"type": "rigid"}}
    m.joint_diaphragms = [{"point": p, "diaphragm": "DA"}
                          for p in _corner_pts(0.0, 3.0)
                          + _corner_pts(40.0, 3.0)]
    m.validate()
    assert cx.diaphragm_towers(m) == {"DA": ["T1", "T2"]}
    eng = OpenSeesEngine(m)
    res = eng.run()
    asm = eng._asm
    g = asm.dia["groups"]
    assert set(g) == {("A1", "DA"), ("B1", "DA")}
    xa = [asm.node_coords[t][0] for t in g[("A1", "DA")]["nodes"]]
    xb = [asm.node_coords[t][0] for t in g[("B1", "DA")]["nodes"]]
    assert max(xa) < 10.0 and min(xb) > 30.0
    assert g[("A1", "DA")]["master"] != g[("B1", "DA")]["master"]
    assert asm.masters["A1"] == g[("A1", "DA")]["master"]
    # uncoupled: same periods as the legacy per-tower diaphragms
    for a, b in zip(res.modal.periods, base):
        assert a == pytest.approx(b, rel=1e-8)
    st = res.cases["EQX"].story
    assert st["A1"]["shear_x"] == pytest.approx(60.0, rel=1e-9)
    assert st["B1"]["shear_x"] == pytest.approx(60.0, rel=1e-9)
    dg = res.to_dict()["diaphragms"]["stories"]
    assert set(dg["A1"]) == {"DA"} and set(dg["B1"]) == {"DA"}


def test_named_diaphragm_in_one_tower_keeps_other_tower_diaphragm():
    ref = OpenSeesEngine(_twin()).run()
    m = _twin()
    m.diaphragms = {"DA": {"type": "rigid"}}
    m.joint_diaphragms = [{"point": p, "diaphragm": "DA"}
                          for p in _corner_pts(0.0, 3.0)]
    m.validate()
    assert cx.named_story_names(m) == ["A1"]
    eng = OpenSeesEngine(m)
    res = eng.run()
    assert "B1" in eng._asm.masters
    for nm in ("B1", "B2", "B3", "A1"):
        for k in ("ux", "shear_x"):
            assert res.cases["EQX"].story[nm][k] == pytest.approx(
                ref.cases["EQX"].story[nm][k], rel=1e-8)


# --------------------------------------------- staged construction
def _staged(m):
    m.staged_cases["ST"] = StagedCase("ST", pattern="DEAD")
    return m


def test_staged_order_across_towers():
    assert cx.stage_order(_podium()) == ["P1", "P2", "A1", "B1", "A2",
                                         "B2", "A3"]
    assert cx.stage_order(_twin()) == ["A1", "B1", "A2", "B2", "A3", "B3"]


def test_staged_twin_towers_equal_single_tower():
    a = OpenSeesEngine(_staged(_single())).run_staged("ST")
    b = OpenSeesEngine(_staged(_twin())).run_staged("ST")
    assert list(b.case.story) == ["A1", "A2", "A3", "B1", "B2", "B3"]
    for uid, f in a.case.member_forces.items():
        assert max(abs(v) for v in f) >= 0.0
        for u2 in (uid, "B" + uid[1:]):
            for x, y in zip(b.case.member_forces[u2], f):
                assert x == pytest.approx(y, rel=1e-8, abs=1e-8)
    assert b.case.base["FZ"] == pytest.approx(2 * a.case.base["FZ"],
                                              rel=1e-10)
    assert b.column_axial_max_diff_pct == pytest.approx(
        a.column_axial_max_diff_pct, rel=1e-8)


def test_staged_podium_equilibrium_and_sequence_effect():
    m = _staged(_podium())
    r = OpenSeesEngine(m).run_staged("ST")
    # total gravity = one-shot base reaction (equilibrium)
    assert r.case.base["FZ"] == pytest.approx(r.oneshot.base["FZ"],
                                              rel=1e-10)
    tot = sum(u.w * m._member(u.member_uid).length
              for u in m.patterns["DEAD"].member_udls)
    assert r.case.base["FZ"] == pytest.approx(tot, rel=1e-9)
    # every column carries load; tower columns differ from one-shot
    # (sequence effect) but sum to the same vertical load per level
    assert r.column_axial_max_diff_pct > 0.0
    cols = [mm for mm in m.members if mm.kind == "column"
            and abs(min(mm.pi[2], mm.pj[2])) < 1e-9]
    ns = sum(r.case.member_forces[c.uid][0] for c in cols)
    no = sum(r.oneshot.member_forces[c.uid][0] for c in cols)
    assert ns == pytest.approx(no, rel=1e-9)


def test_staged_user_stages_across_towers():
    m = _twin()
    ga = [mm.uid for mm in m.members if mm.uid.startswith("A")]
    gb = [mm.uid for mm in m.members if mm.uid.startswith("B")]
    m.groups = {"GA": normalize_group({"members": ga}),
                "GB": normalize_group({"members": gb})}
    m.staged_cases["U"] = StagedCase("U", stages=[
        {"name": "S1", "operations": [
            {"op": "add", "group": "GA"},
            {"op": "load", "group": "GA", "pattern": "DEAD"}]},
        {"name": "S2", "operations": [
            {"op": "add", "group": "GB"},
            {"op": "load", "group": "GB", "pattern": "DEAD"}]}])
    m.validate()
    r = OpenSeesEngine(m).run_staged("U")
    one = OpenSeesEngine(m).run_static("DEAD")
    for uid in ga + gb:
        for x, y in zip(r.case.member_forces[uid],
                        one.member_forces[uid]):
            assert x == pytest.approx(y, rel=1e-7, abs=1e-7)
