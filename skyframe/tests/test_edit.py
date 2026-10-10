"""ETABS Edit utilities (skyframe.core.edit) — replicate / divide / merge /
align / move / extrude / join / delete.

Validation (hand-derived):

* geometry of every op is checked against closed-form coordinates
  (translations, quarter-turn rotations, reflections, projections);
* every reference stays consistent: member / area / thermal loads,
  groups, pushover ``My``, tendon hosts, supports / springs / nodal loads
  at moved or merged joints;
* round trips: divide -> join is byte-identical; replicate -> delete of
  the copies is byte-identical; the input model is never mutated;
* analysis: a bay replicated with Edit > Replicate gives EXACTLY the
  displacements / reactions of the same frame built directly, and a
  divided beam gives the same joint results as the undivided beam.
"""

import copy
import json
import math

import pytest

from skyframe.core.edit import EDIT_OPS, apply_edit, apply_edit_model
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 MemberLoad, MemberUDL, NodalLoad,
                                 PointSupport, ShellSection, ThermalLoad)

E = 30.0e6
H = 3.0
B = 6.0


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _base():
    mdl = BuildingModel(name="edit")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("C30", E=E, nu=0.2, unit_weight=24.0))
    mdl.add_section(FrameSection.rectangular("COL", "C30", 0.4, 0.4))
    mdl.add_section(FrameSection.rectangular("BEAM", "C30", 0.3, 0.6))
    mdl.add_section(FrameSection.rectangular("BEAM2", "C30", 0.3, 0.5))
    mdl.add_shell_section(ShellSection("SL", "C30", 0.2))
    mdl.set_stories([H, H])
    return mdl


def _bay(nbays=1, stories=1, loads=True):
    """Planar moment frame (x-z plane) built DIRECTLY: nbays x stories."""
    mdl = _base()
    pat = mdl.pattern("DEAD", "dead")
    for s in range(stories):
        z0, z1 = s * H, (s + 1) * H
        st = f"Story{s + 1}"
        for i in range(nbays + 1):
            mdl.add_member("column", "COL", (i * B, 0, z0), (i * B, 0, z1),
                           story=st, uid=f"C{s}{i}")
        for i in range(nbays):
            mdl.add_member("beam", "BEAM", (i * B, 0, z1),
                           ((i + 1) * B, 0, z1), story=st, uid=f"B{s}{i}")
            if loads:
                pat.member_loads.append(MemberLoad(f"B{s}{i}", w=12.0))
                pat.member_loads.append(MemberLoad(f"B{s}{i}", kind="point",
                                                   w=20.0, a=0.25))
        if loads:
            for i in range(nbays + 1):
                pat.nodal_loads.append(NodalLoad((i * B, 0, z1), fx=5.0))
    for i in range(nbays + 1):
        mdl.supports.append(PointSupport((i * B, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.add_case("DEAD", {"DEAD": 1.0})
    mdl.validate()
    return mdl


def _portal():
    """Single bay with UDL, thermal load, group, pushover-free extras."""
    mdl = _bay(1, 1)
    mdl.patterns["DEAD"].member_udls.append(MemberUDL("B00", 3.0))
    mdl.patterns["DEAD"].thermal_loads.append(ThermalLoad("B00", dT=10.0))
    mdl.add_group("G", members=["B00", "C00"], points=[[0, 0, H]])
    mdl.validate()
    return mdl


def _canon(mdl):
    """Canonical JSON (a model built in Python may hold int coordinates)."""
    return json.dumps(BuildingModel.from_dict(mdl.to_dict()).to_dict())


def _sel(**kw):
    out = {"members": [], "shells": [], "links": [], "points": []}
    out.update(kw)
    return out


def _m(mdl, uid):
    return next(m for m in mdl.members if m.uid == uid)


def _disp_by_xyz(mdl, case="DEAD"):
    from skyframe.engine.opensees_engine import OpenSeesEngine
    eng = OpenSeesEngine(mdl)
    r = eng.run_static(case)
    asm = eng._asm if eng._asm is not None else eng._build()
    out = {}
    for t, c in asm.node_coords.items():
        out[tuple(round(v, 6) for v in c)] = r.node_disp[t]
    reac = {}
    for t, v in r.reactions.items():
        reac[tuple(round(x, 6) for x in asm.node_coords[t])] = v
    return out, reac


def _loads_of(mdl, uid, pat="DEAD"):
    return [ld for ld in mdl.patterns[pat].member_loads
            if ld.member_uid == uid]


# --------------------------------------------------------------------------- #
# replicate
# --------------------------------------------------------------------------- #
def test_replicate_linear_geometry_and_unique_uids():
    mdl = _bay(1, 1)
    sel = _sel(members=["C01", "B00"])
    new, sm = apply_edit_model(mdl, "replicate", sel,
                               {"mode": "linear", "dx": B, "n": 3})
    assert len(new.members) == len(mdl.members) + 6
    uids = [m.uid for m in new.members]
    assert len(uids) == len(set(uids))
    made = sm["created"]["members"]
    assert len(made) == 6
    xs = sorted(_m(new, u).pi[0] for u in made if _m(new, u).kind == "beam")
    assert xs == [6.0, 12.0, 18.0]
    cols = sorted(_m(new, u).pi[0] for u in made if _m(new, u).kind ==
                  "column")
    assert cols == [12.0, 18.0, 24.0]
    assert set(sm["new_selection"]["members"]) == set(made)
    for u in made:
        assert _m(new, u).story == "Story1"


def test_replicate_copies_loads_and_assignments_options():
    mdl = _portal()
    _m(mdl, "B00").releases = "Mi"
    sel = _sel(members=["B00"])
    new, sm = apply_edit_model(mdl, "replicate", sel,
                               {"mode": "linear", "dz": H, "n": 1})
    u = sm["created"]["members"][0]
    m = _m(new, u)
    assert m.releases == "Mi" and m.story == "Story2"
    assert m.pi == (0.0, 0.0, 2 * H)
    pat = new.patterns["DEAD"]
    assert [(ld.kind, ld.w, ld.a) for ld in _loads_of(new, u)] == \
        [("udl", 12.0, 0.0), ("point", 20.0, 0.25)]
    assert [x.w for x in pat.member_udls if x.member_uid == u] == [3.0]
    assert [x.dT for x in pat.thermal_loads if x.member_uid == u] == [10.0]
    assert u in new.groups["G"]["members"]
    # no loads, no assignments
    new2, sm2 = apply_edit_model(mdl, "replicate", sel,
                                 {"mode": "linear", "dz": H, "loads": False,
                                  "assignments": False})
    u2 = sm2["created"]["members"][0]
    assert _m(new2, u2).releases == "" and _m(new2, u2).section == "BEAM"
    assert not _loads_of(new2, u2)
    assert u2 not in new2.groups["G"]["members"]


def test_replicate_skips_existing_duplicates():
    mdl = _bay(2, 1)
    new, sm = apply_edit_model(mdl, "replicate", _sel(members=["B00"]),
                               {"mode": "linear", "dx": B, "n": 2})
    assert len(sm["created"]["members"]) == 1        # x=6 exists already
    assert sm["warnings"]
    assert _m(new, sm["created"]["members"][0]).pi[0] == 12.0


def test_replicate_radial_quarter_turn_rotates_geometry_angles_loads():
    mdl = _bay(1, 1)
    _m(mdl, "C01").angle = 10.0
    pat = mdl.patterns["DEAD"]
    pat.member_loads.append(MemberLoad("B00", w=4.0, direction="global_x"))
    new, sm = apply_edit_model(
        mdl, "replicate", _sel(members=["C01", "B00"], points=[[B, 0, H]]),
        {"mode": "radial", "center": [0, 0, 0], "axis": "z", "angle": 90,
         "n": 1})
    cmap = dict(zip(["C01", "B00"], sm["created"]["members"]))
    col, beam = _m(new, cmap["C01"]), _m(new, cmap["B00"])
    assert col.pi == (0.0, B, 0.0) and col.pj == (0.0, B, H)
    assert col.angle == pytest.approx(100.0)
    assert beam.pi == (0.0, 0.0, H) and beam.pj == (0.0, B, H)
    gl = [ld for ld in _loads_of(new, beam.uid) if ld.direction != "gravity"]
    assert [(ld.direction, ld.w) for ld in gl] == [("global_y", 4.0)]
    # nodal load at the selected point rotates (fx -> fy)
    nl = [n for n in new.patterns["DEAD"].nodal_loads
          if n.point == (0.0, B, H)]
    assert len(nl) == 1 and nl[0].fy == pytest.approx(5.0)
    assert nl[0].fx == pytest.approx(0.0, abs=1e-12)


def test_replicate_mirror_reflects_frames_shells_and_forces():
    mdl = _bay(1, 1)
    mdl.add_shell("slab", "shell", "SL",
                  [(0, 0, H), (B, 0, H), (B, 4, H), (0, 4, H)],
                  story="Story1", uid="S1")
    mdl.patterns["DEAD"].nodal_loads.append(
        NodalLoad((B, 0, H), fx=1.0, mz=2.0))
    mdl.validate()
    new, sm = apply_edit_model(
        mdl, "replicate",
        _sel(members=["C01"], shells=["S1"], points=[[B, 0, H]]),
        {"mode": "mirror", "plane": "x", "coord": 10.0})
    col = _m(new, sm["created"]["members"][0])
    assert col.pi == (14.0, 0.0, 0.0)
    sh = next(s for s in new.shells if s.uid == sm["created"]["shells"][0])
    xs = sorted({c[0] for c in sh.corners})
    assert xs == [14.0, 20.0]
    # corner order stays counter-clockwise seen from +Z (positive area)
    c = sh.corners
    a2 = sum(c[i][0] * c[(i + 1) % 4][1] - c[(i + 1) % 4][0] * c[i][1]
             for i in range(4))
    assert a2 > 0
    nl = [n for n in new.patterns["DEAD"].nodal_loads
          if n.point == (14.0, 0.0, H) and n.mz]
    # reflection x -> -x: polar fx flips, pseudo-vector mz flips too
    assert nl[0].fx == pytest.approx(-1.0) and nl[0].mz == pytest.approx(-2.0)


def test_replicate_mirror_about_plan_line():
    mdl = _bay(1, 1)
    new, sm = apply_edit_model(mdl, "replicate", _sel(members=["B00"]),
                               {"mode": "mirror", "p1": [0, 3], "p2": [6, 3]})
    m = _m(new, sm["created"]["members"][0])
    assert m.pi == (0.0, 6.0, H) and m.pj == (B, 6.0, H)


def test_replicate_story_copies_to_selected_stories_with_points():
    mdl = _bay(1, 1)
    new, sm = apply_edit_model(
        mdl, "replicate",
        _sel(members=["C00", "C01", "B00"], points=[[0, 0, H]]),
        {"mode": "story", "stories": ["Story2"]})
    made = [_m(new, u) for u in sm["created"]["members"]]
    assert len(made) == 3 and all(m.story == "Story2" for m in made)
    assert sorted(m.pi[2] for m in made) == [H, H, 2 * H]
    assert any(n.point == (0.0, 0.0, 2 * H) and n.fx == 5.0
               for n in new.patterns["DEAD"].nodal_loads)
    with pytest.raises(ValueError):
        apply_edit_model(mdl, "replicate", _sel(members=["B00"]),
                         {"mode": "story", "stories": ["Nope"]})


def test_replicated_bay_analysis_equals_frame_built_directly():
    one = _bay(1, 1)
    sel = _sel(members=["C01", "B00"], points=[[B, 0, 0], [B, 0, H]])
    rep, _ = apply_edit_model(one, "replicate", sel,
                              {"mode": "linear", "dx": B, "n": 2})
    direct = _bay(3, 1)
    d1, r1 = _disp_by_xyz(rep)
    d2, r2 = _disp_by_xyz(direct)
    assert set(d1) == set(d2)
    for k in d2:
        assert d1[k] == pytest.approx(d2[k], rel=1e-9, abs=1e-12)
    assert set(r1) == set(r2)
    for k in r2:
        assert r1[k] == pytest.approx(r2[k], rel=1e-9, abs=1e-9)


def test_replicate_then_delete_copies_round_trips():
    mdl = _portal()
    new, sm = apply_edit_model(mdl, "replicate", _sel(members=["B00"]),
                               {"mode": "linear", "dz": H})
    back, _ = apply_edit_model(new, "delete",
                               _sel(members=sm["created"]["members"]), {})
    assert json.dumps(back.to_dict()) == _canon(mdl)


# --------------------------------------------------------------------------- #
# divide
# --------------------------------------------------------------------------- #
def test_divide_n_equal_conserves_loads_and_releases():
    mdl = _portal()
    _m(mdl, "B00").releases = "Mi,Mj"
    mdl.patterns["DEAD"].member_loads.append(
        MemberLoad("B00", kind="trapezoid", w=0.0, w2=6.0, a=0.0, b=1.0))
    new, sm = apply_edit_model(mdl, "divide", _sel(members=["B00"]),
                               {"mode": "n", "n": 3})
    pieces = sm["divided"]["B00"]
    assert pieces[0] == "B00" and len(pieces) == 3
    ms = [_m(new, u) for u in pieces]
    assert [m.length for m in ms] == pytest.approx([2.0, 2.0, 2.0])
    assert [m.releases for m in ms] == ["Mi", "", "Mj"]
    loads = [ld for u in pieces for ld in _loads_of(new, u)]
    tot_udl = sum(ld.w * (ld.b - ld.a) * _m(new, ld.member_uid).length
                  for ld in loads if ld.kind == "udl")
    assert tot_udl == pytest.approx(12.0 * B)
    pts = [ld for ld in loads if ld.kind == "point"]
    assert len(pts) == 1 and pts[0].w == 20.0
    # 0.25 * 6 = 1.5 m -> first piece at 0.75
    assert pts[0].member_uid == "B00" and pts[0].a == pytest.approx(0.75)
    trap = [ld for ld in loads if ld.kind == "trapezoid"]
    tot_trap = sum(0.5 * (ld.w + ld.w2) * (ld.b - ld.a) *
                   _m(new, ld.member_uid).length for ld in trap)
    assert tot_trap == pytest.approx(0.5 * 6.0 * B)
    assert trap[1].w == pytest.approx(2.0) and trap[1].w2 == pytest.approx(4.0)
    pat = new.patterns["DEAD"]
    assert sorted(x.member_uid for x in pat.member_udls) == sorted(pieces)
    assert sorted(x.member_uid for x in pat.thermal_loads) == sorted(pieces)
    assert set(pieces) <= set(new.groups["G"]["members"])


def test_divide_distance_from_j_end():
    mdl = _bay(1, 1)
    new, sm = apply_edit_model(mdl, "divide", _sel(members=["B00"]),
                               {"mode": "distance", "distance": 1.5,
                                "from": "j"})
    a, b = [_m(new, u) for u in sm["divided"]["B00"]]
    assert a.pj == (4.5, 0.0, H) and b.length == pytest.approx(1.5)


def test_divide_at_intersections_with_other_frames():
    mdl = _bay(1, 1)
    mdl.add_member("column", "COL", (2.0, 0, 0), (2.0, 0, H), uid="P1",
                   story="Story1")
    mdl.add_member("beam", "BEAM2", (4.0, -2, H), (4.0, 2, H), uid="X1",
                   story="Story1")
    mdl.validate()
    new, sm = apply_edit_model(mdl, "divide", _sel(members=["B00"]),
                               {"mode": "intersections"})
    ends = sorted(_m(new, u).pj[0] for u in sm["divided"]["B00"])
    assert ends == [2.0, 4.0, 6.0]


def test_divided_beam_analysis_matches_undivided():
    mdl = _bay(1, 1)
    new, sm = apply_edit_model(mdl, "divide", _sel(members=["B00"]),
                               {"mode": "n", "n": 4})
    d1, r1 = _disp_by_xyz(mdl)
    d2, r2 = _disp_by_xyz(new)
    for k in d1:
        assert d2[k] == pytest.approx(d1[k], rel=1e-7, abs=1e-12)
    for k in r1:
        assert r2[k] == pytest.approx(r1[k], rel=1e-7, abs=1e-8)


def test_divide_updates_hosts_hinges_and_pushover_my():
    mdl = _bay(1, 1)
    mdl.add_pushover_case("PO", "X")
    mdl.pushover_cases["PO"].My = {"B00": 150.0}
    d = mdl.to_dict()
    d["members"][[m["uid"] for m in d["members"]].index("B00")]["hinges"] = [
        {"property": "auto_m3", "relative_distance": 0.1},
        {"property": "auto_m3", "relative_distance": 0.9}]
    d["tendons"] = [{"uid": "T1", "host": ["B00"]}]
    nd, sm = apply_edit(d, "divide", _sel(members=["B00"]),
                        {"mode": "n", "n": 2})
    p1, p2 = sm["divided"]["B00"]
    mm = {m["uid"]: m for m in nd["members"]}
    assert mm[p1]["hinges"][0]["relative_distance"] == pytest.approx(0.2)
    assert mm[p2]["hinges"][0]["relative_distance"] == pytest.approx(0.8)
    assert nd["tendons"][0]["host"] == [p1, p2]
    assert nd["pushover_cases"]["PO"]["My"] == {p1: 150.0, p2: 150.0}
    assert d["tendons"][0]["host"] == ["B00"]          # input untouched


def test_divide_then_join_round_trips_byte_identically():
    mdl = _portal()
    new, sm = apply_edit_model(mdl, "divide", _sel(members=["B00"]),
                               {"mode": "n", "n": 3})
    back, sj = apply_edit_model(new, "join",
                                _sel(members=sm["divided"]["B00"]), {})
    assert len(sj["joined"]) == 2
    assert json.dumps(back.to_dict()) == _canon(mdl)


# --------------------------------------------------------------------------- #
# join
# --------------------------------------------------------------------------- #
def test_join_refuses_shared_or_loaded_joints_and_mixed_sections():
    mdl = _bay(2, 1)            # B00 / B01 meet at column C01's top
    _, sm = apply_edit_model(mdl, "join", _sel(members=["B00", "B01"]), {})
    assert not sm["joined"]
    mdl2 = _base()
    mdl2.add_member("beam", "BEAM", (0, 0, H), (3, 0, H), uid="A")
    mdl2.add_member("beam", "BEAM2", (3, 0, H), (6, 0, H), uid="B")
    mdl2.add_member("beam", "BEAM", (6, 0, H), (9, 0, H), uid="C")
    mdl2.pattern("DEAD").nodal_loads.append(NodalLoad((6, 0, H), fz=-1))
    mdl2.validate()
    _, sm = apply_edit_model(mdl2, "join", _sel(members=["A", "B", "C"]), {})
    reasons = {s["reason"] for s in sm["skipped"]}
    assert not sm["joined"]
    assert "different sections" in reasons
    assert "joint carries loads / assignments" in reasons


def test_join_reversed_member_maps_loads():
    mdl = _base()
    mdl.add_member("beam", "BEAM", (0, 0, H), (4, 0, H), uid="A")
    mdl.add_member("beam", "BEAM", (6, 0, H), (4, 0, H), uid="B")  # reversed
    mdl.pattern("DEAD").member_loads.append(
        MemberLoad("B", kind="point", w=7.0, a=0.0))     # at x = 6
    mdl.validate()
    new, sm = apply_edit_model(mdl, "join", _sel(members=["A", "B"]), {})
    m = _m(new, "A")
    assert m.pi == (0.0, 0.0, H) and m.pj == (6.0, 0.0, H)
    ld = _loads_of(new, "A")[0]
    assert ld.a == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# merge joints
# --------------------------------------------------------------------------- #
def test_merge_joints_within_tolerance_reports_and_keeps_support():
    mdl = _base()
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, H), uid="C1")
    mdl.add_member("beam", "BEAM", (0.003, 0.0, H), (B, 0, H), uid="B1")
    mdl.add_member("column", "COL", (B, 0, 0), (B, 0, H), uid="C2")
    mdl.supports = [PointSupport((0, 0, 0), (1,) * 6),
                    PointSupport((B, 0, 0), (1,) * 6)]
    mdl.pattern("DEAD").nodal_loads.append(NodalLoad((0.003, 0, H), fx=1))
    mdl.validate()
    new, sm = apply_edit_model(mdl, "merge_joints", _sel(),
                               {"tolerance": 0.005})
    assert sm["merged_count"] == 1
    assert sm["merged"][0]["to"] == [0.0, 0.0, H]
    assert _m(new, "B1").pi == (0.0, 0.0, H)
    assert new.patterns["DEAD"].nodal_loads[0].point == (0.0, 0.0, H)
    # tolerance too small -> nothing
    _, sm2 = apply_edit_model(mdl, "merge_joints", _sel(),
                              {"tolerance": 0.001})
    assert sm2["merged_count"] == 0


def test_merge_deletes_collapsed_member_with_its_loads():
    mdl = _base()
    mdl.add_member("beam", "BEAM", (0, 0, H), (B, 0, H), uid="B1")
    mdl.add_member("beam", "BEAM", (B, 0, H), (B + 0.002, 0, H), uid="TINY")
    mdl.pattern("DEAD").member_loads.append(MemberLoad("TINY", w=1.0))
    mdl.add_group("G", members=["TINY", "B1"])
    mdl.validate()
    new, sm = apply_edit_model(mdl, "merge_joints", _sel(),
                               {"tolerance": 0.005})
    assert "TINY" in sm["deleted"]["members"]
    assert not any(ld.member_uid == "TINY"
                   for ld in new.patterns["DEAD"].member_loads)
    assert new.groups["G"]["members"] == ["B1"]


def test_merge_scope_is_the_selection():
    mdl = _base()
    mdl.add_member("beam", "BEAM", (0, 0, H), (B, 0, H), uid="B1")
    mdl.add_member("beam", "BEAM", (B + 0.002, 0, H), (12, 0, H), uid="B2")
    mdl.add_member("beam", "BEAM", (0, 5, H), (B, 5, H), uid="B3")
    mdl.add_member("beam", "BEAM", (B + 0.002, 5, H), (12, 5, H), uid="B4")
    mdl.validate()
    new, sm = apply_edit_model(mdl, "merge_joints",
                               _sel(members=["B1", "B2"]),
                               {"tolerance": 0.005})
    assert sm["merged_count"] == 1
    assert _m(new, "B4").pi[0] == pytest.approx(B + 0.002)


# --------------------------------------------------------------------------- #
# align / move
# --------------------------------------------------------------------------- #
def test_align_points_to_z_ordinate_stretches_connected_frames():
    mdl = _bay(1, 1)
    new, sm = apply_edit_model(mdl, "align",
                               _sel(points=[[0, 0, H], [B, 0, H]]),
                               {"mode": "coordinate", "axis": "z",
                                "value": 2 * H})
    assert _m(new, "C00").pj == (0.0, 0.0, 2 * H)
    assert _m(new, "B00").pi == (0.0, 0.0, 2 * H)
    assert _m(new, "B00").story == "Story2"
    assert all(n.point[2] == 2 * H for n in new.patterns["DEAD"].nodal_loads)


def test_align_points_to_line_and_plane_projection():
    mdl = _base()
    mdl.add_member("beam", "BEAM", (0, 0.4, H), (B, -0.3, H), uid="B1")
    mdl.validate()
    sel = _sel(points=[[0, 0.4, H], [B, -0.3, H]])
    new, _ = apply_edit_model(mdl, "align", sel,
                              {"mode": "line", "p1": [0, 0, H],
                               "p2": [10, 0, H]})
    assert _m(new, "B1").pi == (0.0, 0.0, H)
    assert _m(new, "B1").pj == (B, 0.0, H)
    new2, _ = apply_edit_model(mdl, "align", sel,
                               {"mode": "plane", "point": [0, 0, 0],
                                "normal": [0, 1, 0]})
    assert _m(new2, "B1").pj == (B, 0.0, H)


def test_trim_and_extend_frames_to_line():
    mdl = _base()
    mdl.add_member("beam", "BEAM", (0, 0, H), (4, 0, H), uid="SHORT")
    mdl.add_member("beam", "BEAM", (0, 2, H), (9, 2, H), uid="LONG")
    mdl.validate()
    new, sm = apply_edit_model(mdl, "align",
                               _sel(members=["SHORT", "LONG"]),
                               {"mode": "trim_extend", "p1": [6, -5, H],
                                "p2": [6, 5, H]})
    assert _m(new, "SHORT").pj == (6.0, 0.0, H)        # extended
    assert _m(new, "LONG").pj == (6.0, 2.0, H)         # trimmed
    assert set(sm["trimmed"]) == {"SHORT", "LONG"}


def test_move_selection_carries_joint_records_and_stretches_neighbours():
    mdl = _bay(1, 1)
    new, sm = apply_edit_model(mdl, "move", _sel(members=["B00"]),
                               {"dx": 0.0, "dy": 0.0, "dz": 0.5})
    assert _m(new, "B00").pi == (0.0, 0.0, H + 0.5)
    assert _m(new, "C00").pj == (0.0, 0.0, H + 0.5)    # stretched
    assert _m(new, "C00").pi == (0.0, 0.0, 0.0)
    pts = sorted(n.point for n in new.patterns["DEAD"].nodal_loads)
    assert pts == [(0.0, 0.0, H + 0.5), (B, 0.0, H + 0.5)]
    assert sorted(s.point for s in new.supports) == [(0, 0, 0), (B, 0, 0)]
    with pytest.raises(ValueError):
        apply_edit_model(mdl, "move", _sel(members=["B00"]), {})


# --------------------------------------------------------------------------- #
# extrude
# --------------------------------------------------------------------------- #
def test_extrude_points_to_frames_chain_with_story_and_kind():
    mdl = _base()
    mdl.validate()
    new, sm = apply_edit_model(mdl, "extrude", _sel(points=[[1, 1, 0]]),
                               {"mode": "points_to_frames", "dz": H, "n": 2})
    ms = [_m(new, u) for u in sm["created"]["members"]]
    assert [(m.kind, m.section, m.story) for m in ms] == \
        [("column", "COL", "Story1"), ("column", "COL", "Story2")]
    assert ms[0].pj == ms[1].pi == (1.0, 1.0, H)


def test_extrude_frames_to_shells_wall_and_ccw_slab():
    mdl = _bay(1, 1)
    new, sm = apply_edit_model(mdl, "extrude", _sel(members=["B00"]),
                               {"mode": "frames_to_shells", "dz": H, "n": 1})
    w = next(s for s in new.shells if s.uid == sm["created"]["shells"][0])
    assert w.kind == "wall" and w.story == "Story2"
    assert w.area == pytest.approx(B * H)
    new2, sm2 = apply_edit_model(mdl, "extrude", _sel(members=["B00"]),
                                 {"mode": "frames_to_shells", "dy": -4.0,
                                  "n": 2})
    sl = [s for s in new2.shells if s.uid in sm2["created"]["shells"]]
    assert len(sl) == 2 and all(s.kind == "slab" for s in sl)
    for s in sl:
        c = s.corners
        a2 = sum(c[i][0] * c[(i + 1) % 4][1] - c[(i + 1) % 4][0] * c[i][1]
                 for i in range(4))
        assert a2 > 0


# --------------------------------------------------------------------------- #
# delete
# --------------------------------------------------------------------------- #
def test_delete_cleans_every_dependent_reference():
    mdl = _portal()
    mdl.add_pushover_case("PO", "X")
    mdl.pushover_cases["PO"].My = {"B00": 100.0}
    d = mdl.to_dict()
    d["tendons"] = [{"uid": "T1", "host": ["B00"]}]
    nd, sm = apply_edit(d, "delete", _sel(members=["B00", "C01"]), {})
    pat = nd["patterns"]["DEAD"]
    assert not any(x["member_uid"] in ("B00", "C01")
                   for k in ("member_loads", "member_udls", "thermal_loads")
                   for x in pat[k])
    assert nd["groups"]["G"]["members"] == ["C00"]
    assert nd["pushover_cases"]["PO"]["My"] == {}
    assert "tendons" not in nd or not nd["tendons"]
    # joint (B,0,H) is orphaned -> its nodal load + base support go; the
    # shared joint (0,0,H) keeps its load (C00 still uses it)
    pts = sorted(tuple(n["point"]) for n in pat["nodal_loads"])
    assert pts == [(0.0, 0.0, H)]
    assert [tuple(s["point"]) for s in nd["supports"]] == [(0.0, 0.0, 0.0)]
    BuildingModel.from_dict(nd)                       # still valid


def test_delete_points_removes_point_records_only():
    mdl = _bay(1, 1)
    new, sm = apply_edit_model(mdl, "delete", _sel(points=[[B, 0, H]]), {})
    assert len(new.members) == len(mdl.members)
    assert [n.point for n in new.patterns["DEAD"].nodal_loads] == \
        [(0.0, 0.0, H)]
    assert sm["point_records_deleted"] == 1


# --------------------------------------------------------------------------- #
# purity, errors, API
# --------------------------------------------------------------------------- #
def test_ops_are_pure_and_reject_bad_input():
    mdl = _portal()
    d = mdl.to_dict()
    snap = json.dumps(d)
    for op, sel, p in [("replicate", _sel(members=["B00"]), {"dx": 1}),
                       ("divide", _sel(members=["B00"]), {"n": 2}),
                       ("move", _sel(members=["B00"]), {"dy": 1}),
                       ("merge_joints", _sel(), {}),
                       ("delete", _sel(members=["B00"]), {})]:
        apply_edit(d, op, sel, p)
    assert json.dumps(d) == snap
    assert set(EDIT_OPS) == {"replicate", "divide", "merge_joints", "align",
                             "move", "extrude", "join", "delete"}
    with pytest.raises(ValueError):
        apply_edit(d, "explode", _sel(), {})
    with pytest.raises(ValueError):
        apply_edit(d, "move", _sel(members=["NOPE"]), {"dx": 1})
    with pytest.raises(ValueError):
        apply_edit(d, "replicate", _sel(members=["B00"]),
                   {"mode": "linear", "dx": 1, "n": 0})
    with pytest.raises(ValueError):
        apply_edit(d, "divide", _sel(members=["B00"]), {"mode": "n", "n": 1})


def test_api_edit_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path))
    from skyframe.api.server import create_app
    app = create_app()
    c = app.test_client()
    mdl = _portal().to_dict()
    r = c.post("/api/model", json=mdl)
    assert r.status_code == 200
    before = c.get("/api/model").get_json()
    r = c.post("/api/edit/divide", json={
        "selection": {"members": ["B00"]}, "params": {"mode": "n", "n": 2},
        "dry_run": True})
    assert r.status_code == 200
    body = r.get_json()
    assert len(body["model"]["members"]) == len(before["members"]) + 1
    assert body["summary"]["op"] == "divide"
    assert c.get("/api/model").get_json() == before       # dry run
    r = c.post("/api/edit/replicate", json={
        "selection": {"members": ["B00"]},
        "params": {"mode": "linear", "dz": H}})
    assert r.status_code == 200
    assert len(c.get("/api/model").get_json()["members"]) == \
        len(before["members"]) + 1
    # explicit model body
    r = c.post("/api/edit/move", json={
        "model": mdl, "selection": {"members": ["B00"]},
        "params": {"dz": 1.0}})
    assert r.status_code == 200
    assert r.get_json()["summary"]["modified"]["members"]
    assert c.post("/api/edit/explode", json={}).status_code == 400
    assert c.post("/api/edit/move", json={
        "selection": {"members": ["NOPE"]}, "params": {"dx": 1}}
    ).status_code == 400
    assert c.post("/api/edit/move", data="x").status_code == 400
