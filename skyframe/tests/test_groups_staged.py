"""Groups (ETABS Define > Groups) + user-defined staged construction.

Validation (hand-checked):

* stage-by-stage column shortening of a 3-story column built story by
  story equals the hand sum (each story's load only shortens the columns
  present when it was applied) and differs from the all-at-once result;
  the user-stage form reproduces the per_story mode exactly;
* removing a temporary prop under a two-span beam redistributes its force
  exactly: the final state is the simply supported 2L span (M = w(2L)^2/8,
  R = wL, delta = 5w(2L)^4/384EI) and the stage-1 prop force matches the
  elastic-prop compatibility hand value;
* a ``load`` operation equals applying the pattern to the existing
  structure (static case), incl. scale and self-weight;
* a group section cut equals the sum of the group's member end forces;
* per_story behaviour is byte-identical, groups round-trip and validate,
  renaming/deleting objects keeps groups consistent;
* time-dependent (AAEM) user stages use the stage durations.
"""

import json
import math

import pytest

from skyframe.core.groups import (group_end_resultant, normalize_group,
                                  resolve_group)
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 MemberLoad, NodalLoad, PointSupport,
                                 SectionCut)
from skyframe.engine.opensees_engine import (TD_CHI, OpenSeesEngine,
                                             aci209_creep, aci209_shrinkage,
                                             compute_section_cut)

E = 30.0e6
H = 3.0
A_COL = 0.16
P = (100.0, 200.0, 300.0)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _column(user=True, stages=None, td=None):
    mdl = BuildingModel(name="col")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("C30", E=E, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "C30", 0.4, 0.4))
    mdl.set_stories([H, H, H])
    pat = mdl.pattern("DEAD")
    for i in range(3):
        mdl.add_member("column", "COL", (0, 0, i * H), (0, 0, (i + 1) * H),
                       story=f"Story{i + 1}", uid=f"C{i + 1}")
        pat.nodal_loads.append(NodalLoad((0, 0, (i + 1) * H), fz=-P[i]))
        mdl.add_group(f"G{i + 1}", members=[f"C{i + 1}"],
                      points=[[0, 0, (i + 1) * H]])
    mdl.add_staged_case("PS", pattern="DEAD")
    if user:
        if stages is None:
            stages = [{"name": f"S{i + 1}", "duration_days": 7.0,
                       "operations": [
                           {"op": "add", "group": f"G{i + 1}"},
                           {"op": "load", "group": f"G{i + 1}",
                            "pattern": "DEAD"}]} for i in range(3)]
        mdl.add_staged_case("U", stages=stages, time_dependent=td)
    mdl.validate()
    return mdl


def _uz(case, eng, z):
    asm = eng._asm if eng._asm is not None else eng._build()
    t = next(t for t, c in asm.node_coords.items()
             if abs(c[2] - z) < 1e-9 and abs(c[0]) < 1e-9)
    return case.node_disp[t][2]


L = 5.0
W = 10.0


def _prop_model():
    mdl = BuildingModel(name="prop")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("C30", E=E, nu=0.2))
    mdl.add_section(FrameSection.rectangular("B", "C30", 0.3, 0.5))
    mdl.add_section(FrameSection.rectangular("P", "C30", 0.2, 0.2))
    mdl.set_stories([H])
    mdl.add_member("beam", "B", (0, 0, H), (L, 0, H), uid="B1")
    mdl.add_member("beam", "B", (L, 0, H), (2 * L, 0, H), uid="B2")
    mdl.add_member("column", "P", (L, 0, 0), (L, 0, H), uid="PROP")
    mdl.supports = [PointSupport((0, 0, H), (1, 1, 1, 1, 0, 1)),
                    PointSupport((2 * L, 0, H), (0, 1, 1, 1, 0, 1)),
                    PointSupport((L, 0, 0), (1, 1, 1, 1, 1, 1))]
    pat = mdl.pattern("DEAD")
    pat.member_loads += [MemberLoad("B1", w=W), MemberLoad("B2", w=W)]
    mdl.add_case("DEAD", {"DEAD": 1.0})
    mdl.add_group("BEAM", members=["B1", "B2"])
    mdl.add_group("PROPS", members=["PROP"], points=[[L, 0, 0]],
                  color="#ff0000")
    mdl.add_staged_case("U", stages=[
        {"name": "build", "duration_days": 14, "operations": [
            {"op": "add", "group": "BEAM"},
            {"op": "add", "group": "PROPS"},
            {"op": "load", "group": "BEAM", "pattern": "DEAD"}]},
        {"name": "strip", "duration_days": 0, "operations": [
            {"op": "remove", "group": "PROPS"}]}])
    mdl.validate()
    return mdl


def _portal():
    """Two columns + beam, gravity UDL; groups LEFT / RIGHT columns."""
    mdl = BuildingModel(name="portal")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("C30", E=E, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "C30", 0.4, 0.4))
    mdl.add_section(FrameSection.rectangular("B", "C30", 0.3, 0.6))
    mdl.set_stories([H])
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, H), uid="C1")
    mdl.add_member("column", "COL", (6, 0, 0), (6, 0, H), uid="C2")
    mdl.add_member("beam", "B", (0, 0, H), (6, 0, H), uid="B1")
    pat = mdl.pattern("DEAD")
    pat.member_loads.append(MemberLoad("B1", kind="udl", w=12.0))
    pat.nodal_loads.append(NodalLoad((6, 0, H), fx=15.0))
    mdl.add_case("DEAD", {"DEAD": 1.0})
    mdl.add_group("LEFT", members=["C1"])
    mdl.add_group("RIGHT", members=["C2"])
    mdl.add_group("ALL", members=["C1", "C2", "B1"],
                  points=[[0, 0, H], [6, 0, H]])
    mdl.validate()
    return mdl


# --------------------------------------------------------------------------- #
# 1. stage-by-stage column shortening
# --------------------------------------------------------------------------- #
def test_column_shortening_equals_hand_sum():
    mdl = _column()
    eng = OpenSeesEngine(mdl)
    r = eng.run_staged("U")
    EA = E * A_COL
    # node at top of story j moves only under loads applied once it exists
    for j in (1, 2, 3):
        hand = -sum(P[k] * j * H / EA for k in range(j - 1, 3))
        assert _uz(r.case, eng, j * H) == pytest.approx(hand, rel=1e-12)
    # axial forces: cumulative loads above
    assert r.case.member_forces["C1"][0] == pytest.approx(600.0)
    assert r.case.member_forces["C3"][0] == pytest.approx(300.0)


def test_column_staged_differs_from_all_at_once():
    mdl = _column()
    eng = OpenSeesEngine(mdl)
    r = eng.run_staged("U")
    EA = E * A_COL
    one_top = -(600.0 + 500.0 + 300.0) * H / EA
    assert _uz(r.oneshot, eng, 3 * H) == pytest.approx(one_top, rel=1e-12)
    st_top = _uz(r.case, eng, 3 * H)
    assert st_top == pytest.approx(-300.0 * 3 * H / EA, rel=1e-12)
    assert abs(st_top - one_top) > 1e-4


def test_user_stages_reproduce_per_story():
    mdl = _column()
    eng = OpenSeesEngine(mdl)
    u, ps = eng.run_staged("U"), eng.run_staged("PS")
    for uid in ("C1", "C2", "C3"):
        assert u.case.member_forces[uid] == pytest.approx(
            ps.case.member_forces[uid], abs=1e-9)
    for t in ps.case.node_disp:
        assert u.case.node_disp[t] == pytest.approx(ps.case.node_disp[t],
                                                    abs=1e-15)
    assert u.case.base["FZ"] == pytest.approx(ps.case.base["FZ"])


def test_per_stage_output_shape():
    mdl = _column()
    r = OpenSeesEngine(mdl).run_staged("U")
    d = r.to_dict()
    assert [s["name"] for s in d["stages"]] == ["S1", "S2", "S3"]
    s2 = d["stages"][1]
    assert s2["t_start"] == 7.0 and s2["t_end"] == 14.0
    assert s2["active"]["members"] == ["C1", "C2"]
    assert s2["base"]["FZ"] == pytest.approx(300.0)
    assert s2["member_forces"]["C3"] == [0.0] * 12
    for key in ("node_disp", "reactions", "member_forces", "base"):
        assert key in s2
    json.dumps(d)                      # JSON-serialisable
    assert "comparison" in d and d["comparison"]["oneshot_case"]


# --------------------------------------------------------------------------- #
# 2. temporary prop removal
# --------------------------------------------------------------------------- #
def test_prop_removal_gives_simply_supported_span():
    mdl = _prop_model()
    r = OpenSeesEngine(mdl).run_staged("U")
    st = r.case.member_stations["B1"]
    assert st["M3"][-1] == pytest.approx(W * (2 * L) ** 2 / 8, rel=1e-10)
    assert r.case.member_forces["PROP"] == [0.0] * 12
    reac = sorted(v[2] for v in r.case.reactions.values())
    assert reac == pytest.approx([0.0, W * L, W * L], abs=1e-9)
    I = mdl.sections["B"].I33
    d_mid = min(v[2] for v in r.case.node_disp.values())
    assert d_mid == pytest.approx(-5 * W * (2 * L) ** 4 / (384 * E * I),
                                  rel=1e-9)


def test_prop_stage1_force_matches_compatibility():
    mdl = _prop_model()
    r = OpenSeesEngine(mdl).run_staged("U")
    I = mdl.sections["B"].I33
    Ap = mdl.sections["P"].A
    d_w = 5 * W * (2 * L) ** 4 / (384 * E * I)
    d_1 = (2 * L) ** 3 / (48 * E * I)
    R = d_w / (d_1 + H / (E * Ap))
    prop_n = r.stages[0]["member_forces"]["PROP"][0]
    assert abs(prop_n) == pytest.approx(R, rel=1e-9)
    assert r.stages[1]["active"]["members"] == ["B1", "B2"]


def test_prop_removal_base_totals_and_reaction_drop():
    r = OpenSeesEngine(_prop_model()).run_staged("U")
    for s in r.stages:
        assert s["base"]["FZ"] == pytest.approx(2 * W * L, rel=1e-12)
    s0, s1 = r.stages[0]["reactions"], r.stages[1]["reactions"]
    assert set(s0) == set(s1) and len(s0) == 3
    # the prop base support (largest stage-1 reaction) drops to zero
    t_prop = max(s0, key=lambda t: s0[t][2])
    assert s0[t_prop][2] > 0.6 * W * 2 * L
    assert s1[t_prop] == [0.0] * 6


# --------------------------------------------------------------------------- #
# 3. load operations
# --------------------------------------------------------------------------- #
def _portal_staged(load_ops):
    mdl = _portal()
    mdl.add_staged_case("U", stages=[
        {"name": "erect", "operations": [{"op": "add", "group": "ALL"}]},
        {"name": "load", "operations": load_ops}])
    mdl.validate()
    return mdl


def test_load_op_equals_static_case_on_existing_structure():
    mdl = _portal_staged([{"op": "load", "group": "ALL",
                           "pattern": "DEAD"}])
    eng = OpenSeesEngine(mdl)
    r, s = eng.run_staged("U"), eng.run_static("DEAD")
    for uid in ("C1", "C2", "B1"):
        assert r.case.member_forces[uid] == pytest.approx(
            s.member_forces[uid], abs=1e-9)
    for t in s.node_disp:
        assert r.case.node_disp[t] == pytest.approx(s.node_disp[t],
                                                    abs=1e-14)
    assert r.case.base["FX"] == pytest.approx(s.base["FX"])
    assert r.case.base["FZ"] == pytest.approx(s.base["FZ"])
    assert r.column_axial_max_diff_pct == pytest.approx(0.0, abs=1e-9)


def test_load_op_scale_and_split():
    full = OpenSeesEngine(_portal()).run_static("DEAD")
    mdl = _portal_staged([
        {"op": "load", "group": "ALL", "pattern": "DEAD", "scale": 0.5},
        {"op": "load", "group": "ALL", "pattern": "DEAD", "scale": 1.5}])
    r = OpenSeesEngine(mdl).run_staged("U")
    for uid in ("C1", "C2", "B1"):
        assert r.case.member_forces[uid] == pytest.approx(
            [2.0 * v for v in full.member_forces[uid]], abs=1e-9)


def test_load_op_only_loads_group_objects():
    mdl = _portal()
    mdl.add_group("BEAMONLY", members=["B1"])        # no points
    mdl.add_staged_case("U", stages=[
        {"name": "s", "operations": [
            {"op": "add", "group": "ALL"},
            {"op": "load", "group": "BEAMONLY", "pattern": "DEAD"}]}])
    r = OpenSeesEngine(mdl).run_staged("U")
    # the joint load (fx = 15 at a point not in BEAMONLY) is not applied
    assert r.case.base["FX"] == pytest.approx(0.0, abs=1e-9)
    assert r.case.base["FZ"] == pytest.approx(12.0 * 6.0, rel=1e-12)


def test_load_op_self_weight_matches_static():
    mdl = _portal()
    sw = mdl.pattern("SW")
    sw.self_weight_factor = 1.0
    mdl.add_case("SW", {"SW": 1.0})
    mdl.add_staged_case("U", stages=[
        {"name": "s", "operations": [
            {"op": "add", "group": "ALL"},
            {"op": "load", "group": "ALL", "pattern": "SW"}]}])
    mdl.validate()
    eng = OpenSeesEngine(mdl)
    r, s = eng.run_staged("U"), eng.run_static("SW")
    assert s.base["FZ"] > 0.0
    for uid in ("C1", "C2", "B1"):
        assert r.case.member_forces[uid] == pytest.approx(
            s.member_forces[uid], abs=1e-9)


# --------------------------------------------------------------------------- #
# 4. group section cuts + group resultants
# --------------------------------------------------------------------------- #
def test_group_section_cut_equals_member_end_forces():
    mdl = _portal()
    eng = OpenSeesEngine(mdl)
    cr = eng.run_static("DEAD")
    full = compute_section_cut(mdl, SectionCut("F", "z", 1.5), cr)
    left = compute_section_cut(mdl, SectionCut("L", "z", 1.5,
                                               group="LEFT"), cr)
    right = compute_section_cut(mdl, SectionCut("R", "z", 1.5,
                                                group="RIGHT"), cr)
    assert left["n_members"] == 1 and full["n_members"] == 2
    # a column's axial / shear are constant -> its end forces
    f1 = cr.member_forces["C1"]
    assert left["FZ"] == pytest.approx(abs(f1[0]), rel=1e-12)
    assert abs(left["FX"]) == pytest.approx(
        math.hypot(f1[1], f1[2]), rel=1e-12)
    for k in ("FX", "FY", "FZ"):
        assert left[k] + right[k] == pytest.approx(full[k], abs=1e-9)
    assert full["FX"] == pytest.approx(-15.0, abs=1e-9) or \
        full["FX"] == pytest.approx(15.0, abs=1e-9)


def test_group_end_resultant_matches_group_cut_and_base():
    mdl = _portal()
    cr = OpenSeesEngine(mdl).run_static("DEAD")
    both = mdl.add_group("COLS", members=["C1", "C2"])
    assert both["members"] == ["C1", "C2"]
    res = group_end_resultant(mdl, cr, "COLS")
    cut = compute_section_cut(mdl, SectionCut("c", "z", 1e-7,
                                              group="COLS"), cr)
    for k in ("FX", "FY", "FZ", "MX", "MY", "MZ"):
        assert res[k] == pytest.approx(cut[k], abs=1e-5)
    assert res["n_members"] == 2
    # base group == base reaction totals (moments about the origin)
    res0 = group_end_resultant(mdl, cr, "COLS", about=(0, 0, 0))
    for k in ("FX", "FY", "FZ"):
        assert res0[k] == pytest.approx(cr.base[k], abs=1e-9)
    assert res0["MY"] == pytest.approx(cr.base["MY"], abs=1e-6)


def test_section_cut_group_in_full_run_and_serialisation():
    mdl = _portal()
    mdl.add_section_cut("CUTL", "z", 1.5, group="LEFT")
    mdl.add_section_cut("CUTALL", "z", 1.5)
    d = mdl.to_dict()
    assert d["section_cuts"][0]["group"] == "LEFT"
    assert "group" not in d["section_cuts"][1]       # unchanged shape
    m2 = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert m2.section_cuts[0].group == "LEFT"
    res = OpenSeesEngine(m2).run()
    assert res.section_cuts["DEAD"]["CUTL"]["n_members"] == 1
    with pytest.raises(ValueError, match="unknown group"):
        mdl.add_section_cut("BAD", "z", 1.0, group="NOPE")


# --------------------------------------------------------------------------- #
# 5. per_story byte-identical, round trip, validation
# --------------------------------------------------------------------------- #
def test_per_story_byte_identical_with_groups_present():
    base = _column(user=False)
    eng0 = OpenSeesEngine(base)
    d0 = json.dumps(eng0.run_staged("PS").to_dict(), sort_keys=True)
    # the same model without any groups
    plain = BuildingModel.from_dict(base.to_dict())
    plain.groups = {}
    d1 = json.dumps(OpenSeesEngine(plain).run_staged("PS").to_dict(),
                    sort_keys=True)
    assert d0 == d1
    assert "stages" not in json.loads(d0)
    sc = base.staged_cases["PS"].to_dict()
    assert sc["stages"] == "per_story"


def test_model_to_dict_omits_empty_groups():
    mdl = _column(user=False)
    mdl.groups = {}
    assert "groups" not in mdl.to_dict()


def test_groups_and_user_stages_round_trip():
    mdl = _prop_model()
    d = json.loads(json.dumps(mdl.to_dict()))
    assert d["groups"]["PROPS"] == {"members": ["PROP"], "shells": [],
                                    "links": [], "points": [[L, 0.0, 0.0]],
                                    "color": "#ff0000"}
    m2 = BuildingModel.from_dict(d)
    assert m2.groups == mdl.groups
    assert m2.staged_cases["U"].stages == mdl.staged_cases["U"].stages
    assert m2.to_dict() == mdl.to_dict()
    r1 = OpenSeesEngine(mdl).run_staged("U").to_dict()
    r2 = OpenSeesEngine(m2).run_staged("U").to_dict()
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)


def test_group_validation_errors():
    mdl = _portal()
    with pytest.raises(ValueError, match="unknown member"):
        mdl.add_group("X", members=["NOPE"])
    with pytest.raises(ValueError, match="duplicate"):
        mdl.add_group("X", members=["C1", "C1"])
    with pytest.raises(ValueError, match="point"):
        mdl.add_group("X", points=[[0, 0]])
    with pytest.raises(ValueError, match="unknown key"):
        normalize_group({"members": [], "frames": []})
    mdl.groups["BAD"] = {"members": ["ZZ"]}
    with pytest.raises(ValueError, match="unknown member"):
        mdl.validate()


@pytest.mark.parametrize("stages,msg", [
    ([], "non-empty"),
    ([{"name": "a", "operations": [{"op": "add", "group": "NOPE"}]}],
     "unknown group"),
    ([{"name": "a", "operations": [{"op": "add", "group": "LEFT"},
                                   {"op": "add", "group": "LEFT"}]}],
     "already active"),
    ([{"name": "a", "operations": [{"op": "remove", "group": "LEFT"}]}],
     "not active"),
    ([{"name": "a", "operations": [{"op": "build", "group": "LEFT"}]}],
     "op in"),
    ([{"name": "a", "operations": [{"op": "load", "group": "LEFT",
                                    "pattern": "NOPE"}]}],
     "unknown pattern"),
    ([{"name": "a", "duration_days": -1, "operations": []}],
     "duration_days"),
    ([{"name": "a", "operations": []}, {"name": "a", "operations": []}],
     "duplicate stage"),
    ([{"name": "a", "operations": [{"op": "add", "group": "LEFT",
                                    "scale": 2}]}], "unknown key"),
])
def test_user_stage_validation(stages, msg):
    mdl = _portal()
    with pytest.raises(ValueError, match=msg):
        mdl.add_staged_case("U", stages=stages)


def test_time_dependent_days_per_story_still_required_for_per_story():
    mdl = _column(user=False)
    with pytest.raises(ValueError, match="days_per_story"):
        mdl.add_staged_case("TD", pattern="DEAD",
                            time_dependent={"creep_coeff": 2.0})
    # ...but not for user stages
    mdl.add_staged_case("TDU", stages=[{"name": "a", "operations": [
        {"op": "add", "group": "G1"}]}], time_dependent={"creep_coeff": 2})


# --------------------------------------------------------------------------- #
# 6. consistency helpers
# --------------------------------------------------------------------------- #
def test_delete_and_rename_objects_keep_groups_consistent():
    mdl = _portal()
    mdl.members = [m for m in mdl.members if m.uid != "C2"]
    changed = mdl.prune_groups()
    assert changed == {"RIGHT": {"members": ["C2"]},
                       "ALL": {"members": ["C2"]}}
    assert mdl.groups["ALL"]["members"] == ["C1", "B1"]
    mdl.validate()
    mdl.members[0].uid = "COL_A"
    assert sorted(mdl.rename_object_in_groups("members", "C1",
                                              "COL_A")) == ["ALL", "LEFT"]
    assert resolve_group(mdl, "LEFT")["members"][0].uid == "COL_A"
    mdl.validate()


def test_rename_and_delete_group_update_references():
    mdl = _prop_model()
    mdl.add_section_cut("CUT", "z", 1.0, group="PROPS")
    mdl.rename_group("PROPS", "SHORES")
    assert mdl.section_cuts[0].group == "SHORES"
    ops_ = [op["group"] for st in mdl.staged_cases["U"].stages
            for op in st["operations"]]
    assert "SHORES" in ops_ and "PROPS" not in ops_
    mdl.validate()
    with pytest.raises(ValueError, match="referenced"):
        mdl.delete_group("SHORES")
    with pytest.raises(ValueError, match="already exists"):
        mdl.rename_group("SHORES", "BEAM")
    mdl.delete_group("SHORES", force=True)
    assert "SHORES" not in mdl.groups
    assert mdl.section_cuts[0].group is None


# --------------------------------------------------------------------------- #
# 7. combos + full run
# --------------------------------------------------------------------------- #
def test_combo_and_full_run_accept_user_staged_case():
    mdl = _prop_model()
    mdl.add_combo("C", {"U": 1.5, "DEAD": 1.0})
    res = OpenSeesEngine(mdl).run()
    assert res.case_status["U"] == "finished"
    fin = res.staged["U"].case
    dead = res.cases["DEAD"]
    c = res.combos["C"]
    for uid in ("B1", "B2", "PROP"):
        assert c.member_forces[uid] == pytest.approx(
            [1.5 * a + b for a, b in zip(fin.member_forces[uid],
                                         dead.member_forces[uid])],
            abs=1e-9)
    d = res.to_dict()
    assert len(d["staged"]["U"]["stages"]) == 2


# --------------------------------------------------------------------------- #
# 8. time-dependent effects use the stage durations
# --------------------------------------------------------------------------- #
def _held_column(durations, td):
    mdl = BuildingModel(name="held")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("C30", E=E, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "C30", 0.4, 0.4))
    mdl.set_stories([H])
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, H), uid="C1")
    mdl.pattern("DEAD").nodal_loads.append(NodalLoad((0, 0, H), fz=-500.0))
    mdl.add_group("G", members=["C1"], points=[[0, 0, H]])
    mdl.add_staged_case("U", stages=[
        {"name": "load", "duration_days": durations[0], "operations": [
            {"op": "add", "group": "G"},
            {"op": "load", "group": "G", "pattern": "DEAD"}]},
        {"name": "hold", "duration_days": durations[1], "operations": []}],
        time_dependent=td)
    mdl.validate()
    eng = OpenSeesEngine(mdl)
    return eng, eng.run_staged("U")


def test_creep_uses_stage_durations():
    el = -500.0 * H / (E * A_COL)
    for durs in ((10.0, 90.0), (30.0, 300.0)):
        eng, r = _held_column(durs, {"creep_coeff": 2.0, "shrinkage": 0.0})
        t_load = sum(durs)                 # default t_eval = end of stages
        ratio = 1.0 + TD_CHI * aci209_creep(2.0, t_load)
        assert _uz(r.case, eng, H) == pytest.approx(el * ratio, rel=1e-10)
    eng, r = _held_column((10.0, 90.0), {"creep_coeff": 2.0,
                                         "shrinkage": 0.0, "t_eval": 40.0})
    assert _uz(r.case, eng, H) == pytest.approx(
        el * (1.0 + TD_CHI * aci209_creep(2.0, 40.0)), rel=1e-10)


def test_shrinkage_uses_object_age():
    eps_inf = 300e-6
    eng, r = _held_column((0.0, 60.0), {"creep_coeff": 0.0,
                                        "shrinkage": eps_inf})
    el = -500.0 * H / (E * A_COL)
    # added 28 days old at t=0 -> age at t_eval=60 is 88 days
    sh = -aci209_shrinkage(eps_inf, 88.0) * H
    assert _uz(r.case, eng, H) == pytest.approx(el + sh, rel=1e-9)


def test_aging_modulus_changes_result():
    _, a = _held_column((10.0, 90.0), {"creep_coeff": 2.0,
                                       "shrinkage": 0.0, "aging": True})
    _, b = _held_column((10.0, 90.0), {"creep_coeff": 2.0,
                                       "shrinkage": 0.0})
    na = a.case.node_disp
    nb = b.case.node_disp
    assert max(abs(na[t][2] - nb[t][2]) for t in na) > 1e-7


# --------------------------------------------------------------------------- #
# 9. API
# --------------------------------------------------------------------------- #
@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_api_groups_endpoint(api_client):
    mdl = _portal()
    assert api_client.post("/api/model",
                           json=mdl.to_dict()).status_code == 200
    r = api_client.post("/api/groups", json={
        "action": "upsert", "name": "TOP",
        "group": {"members": ["B1"], "color": "#00f"}})
    assert r.status_code == 200
    assert r.get_json()["groups"]["TOP"]["members"] == ["B1"]
    r = api_client.post("/api/section-cut", json={
        "name": "CT", "axis": "z", "coord": 1.0, "group": "TOP"})
    assert r.status_code == 200
    assert api_client.post("/api/groups", json={
        "action": "delete", "name": "TOP"}).status_code == 400
    r = api_client.post("/api/groups", json={
        "action": "rename", "name": "TOP", "new_name": "ROOF"})
    assert r.get_json()["section_cuts"][0]["group"] == "ROOF"
    assert api_client.post("/api/groups", json={
        "action": "upsert", "name": "X",
        "group": {"members": ["NOPE"]}}).status_code == 400
