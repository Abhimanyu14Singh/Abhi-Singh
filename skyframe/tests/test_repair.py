"""Model repair (skyframe.core.repair) — ETABS Check Model with fixes.

Validation (hand-derived):

* splitting a beam with a UDL at an interior joint gives the closed-form
  joint displacements / end forces of the simply supported
  (theta = wL^3/24EI, delta(a) = w a (L^3 - 2 L a^2 + a^3) / 24EI) and
  fixed-fixed (M = wL^2/12, delta(a) = w a^2 (L-a)^2 / 24EI) beam, and the
  SAME end results as the undivided beam; trapezoidal / point / moment
  loads carry over by position (load resultants conserved, a point load at
  the cut acts once);
* merging duplicate joints turns an unstable model (pinned beam hanging
  off a 0.5 mm gap) into a stable one with the closed-form reactions;
* every fix is idempotent (f(f(m)) == f(m) byte-for-byte), the whole
  pipeline too, and a clean model comes back byte-for-byte unchanged.
"""

import copy
import json

import pytest

from skyframe.core import repair as R
from skyframe.core.builder import quick_building
from skyframe.core.checks import check_model, stability_diagnostics
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 MemberLoad, MemberUDL, NodalLoad, NodalMass,
                                 PointSupport, ShellSection, ThermalLoad)

E = 30.0e6
B, H = 0.3, 0.6
I = B * H ** 3 / 12.0
L = 6.0


def _dump(d):
    return json.dumps(d)


def _base(name="repair"):
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("C30", E=E, nu=0.2, unit_weight=0.0))
    mdl.add_section(FrameSection.rectangular("BEAM", "C30", B, H))
    mdl.add_section(FrameSection.rectangular("COL", "C30", 0.4, 0.4))
    mdl.add_shell_section(ShellSection("SL", "C30", 0.2))
    return mdl


def _beam(fixed=False, extra_points=(), loads=None, pattern="DEAD"):
    """Beam 0 -> L along X at z = 0, gravity UDL unless ``loads`` given."""
    mdl = _base()
    mdl.add_member("beam", "BEAM", (0.0, 0.0, 0.0), (L, 0.0, 0.0), uid="B1")
    r_i = (1, 1, 1, 1, 1, 1) if fixed else (1, 1, 1, 1, 0, 0)
    r_j = (1, 1, 1, 1, 1, 1) if fixed else (0, 1, 1, 1, 0, 0)
    mdl.supports.append(PointSupport((0.0, 0.0, 0.0), r_i))
    mdl.supports.append(PointSupport((L, 0.0, 0.0), r_j))
    pat = mdl.pattern(pattern, "dead")
    for ld in (loads if loads is not None else [MemberLoad("B1", w=10.0)]):
        pat.member_loads.append(ld)
    for p in extra_points:            # zero masses: joints, no static effect
        mdl.nodal_masses.append(NodalMass(tuple(p), mx=0.0))
    mdl.add_case(pattern, {pattern: 1.0})
    mdl.validate()
    return mdl


def _run(mdl, case="DEAD"):
    from skyframe.engine.opensees_engine import OpenSeesEngine
    eng = OpenSeesEngine(mdl)
    r = eng.run_static(case)
    asm = eng._asm if eng._asm is not None else eng._build()
    disp, reac = {}, {}
    for t, c in asm.node_coords.items():
        disp[tuple(round(v, 6) for v in c)] = r.node_disp[t]
    for t, v in r.reactions.items():
        reac[tuple(round(x, 6) for x in asm.node_coords[t])] = v
    return disp, reac, r.member_forces


def _bm(d):
    return BuildingModel.from_dict(d)


def _mem(d, uid):
    return next(m for m in d["members"] if m["uid"] == uid)


def _loads(d, uid, pat="DEAD", key="member_loads"):
    return [x for x in d["patterns"][pat].get(key) or []
            if x["member_uid"] == uid]


# --------------------------------------------------------------------------- #
# split at joints: closed form + identical results
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fixed", [False, True])
def test_split_udl_beam_at_interior_joint_closed_form(fixed):
    w, a = 10.0, 2.0
    plain = _beam(fixed)
    messy = _beam(fixed, extra_points=[(a, 0.0, 0.0)])
    new, rep = R.fix_split_at_joints(messy.to_dict())
    assert rep["changed"] and rep["count"] == 1
    assert [m["uid"] for m in new["members"]] == ["B1", "B2"]
    b1, b2 = _mem(new, "B1"), _mem(new, "B2")
    assert b1["pj"] == [a, 0.0, 0.0] and b2["pi"] == [a, 0.0, 0.0]
    # the full-span UDL is split by position
    l1, l2 = _loads(new, "B1"), _loads(new, "B2")
    assert (l1[0]["a"], l1[0]["b"], l1[0]["w"]) == (0.0, 1.0, w)
    assert (l2[0]["a"], l2[0]["b"], l2[0]["w"]) == (0.0, 1.0, w)
    d1, r1, f1 = _run(plain)
    d2, r2, f2 = _run(_bm(new))
    EI = E * I
    # closed form
    if fixed:
        dz = -w * a ** 2 * (L - a) ** 2 / (24 * EI)
        M_end = w * L ** 2 / 12
        # local end forces [N, V2, V3, T, M2, M3] x 2: M3 at the far ends
        assert abs(f2["B1"][5]) == pytest.approx(M_end, rel=1e-6)
        assert abs(f2["B2"][11]) == pytest.approx(M_end, rel=1e-6)
    else:
        dz = -w * a * (L ** 3 - 2 * L * a ** 2 + a ** 3) / (24 * EI)
        th = w * L ** 3 / (24 * EI)
        assert abs(d2[(0.0, 0.0, 0.0)][4]) == pytest.approx(th, rel=1e-6)
        assert abs(d2[(L, 0.0, 0.0)][4]) == pytest.approx(th, rel=1e-6)
        # bending moment at the cut = w a (L - a) / 2
        assert abs(f2["B1"][11]) == pytest.approx(w * a * (L - a) / 2,
                                                  rel=1e-6)
    assert d2[(a, 0.0, 0.0)][2] == pytest.approx(dz, rel=1e-6)
    for p in ((0.0, 0.0, 0.0), (L, 0.0, 0.0)):
        assert r2[p][2] == pytest.approx(w * L / 2, rel=1e-9)
    # identical to the undivided beam: end joints, reactions, end forces
    for p in ((0.0, 0.0, 0.0), (L, 0.0, 0.0)):
        assert d2[p] == pytest.approx(d1[p], rel=1e-9, abs=1e-14)
        assert r2[p] == pytest.approx(r1[p], rel=1e-9, abs=1e-9)
    assert f2["B1"][:6] == pytest.approx(f1["B1"][:6], rel=1e-9, abs=1e-9)
    assert f2["B2"][6:] == pytest.approx(f1["B1"][6:], rel=1e-9, abs=1e-9)


def test_split_carries_trapezoid_point_and_moment_loads_by_position():
    loads = [MemberLoad("B1", kind="trapezoid", w=5.0, w2=15.0, a=0.1,
                        b=0.9),
             MemberLoad("B1", kind="point", w=20.0, a=0.5),     # at the cut
             MemberLoad("B1", kind="point", w=7.0, a=0.2),
             MemberLoad("B1", kind="udl", w=3.0, a=0.6, b=0.8)]
    plain = _beam(False, loads=loads)
    messy = _beam(False, loads=copy.deepcopy(loads),
                  extra_points=[(0.3 * L, 0, 0), (0.5 * L, 0, 0)])
    messy.patterns["DEAD"].member_udls.append(MemberUDL("B1", 2.0))
    plain.patterns["DEAD"].member_udls.append(MemberUDL("B1", 2.0))
    new, rep = R.fix_split_at_joints(messy.to_dict())
    uids = [m["uid"] for m in new["members"]]
    assert uids == ["B1", "B2", "B3"]
    lens = [R._length(_mem(new, u)) for u in uids]
    assert lens == pytest.approx([1.8, 1.2, 3.0])

    def resultant(d, uid):
        m = _mem(d, uid)
        Lm = R._length(m)
        tot = 0.0
        for ld in _loads(d, uid):
            if ld["kind"] == "point":
                tot += ld["w"]
            elif ld["kind"] == "udl":
                tot += ld["w"] * (ld["b"] - ld["a"]) * Lm
            else:
                tot += 0.5 * (ld["w"] + ld["w2"]) * (ld["b"] - ld["a"]) * Lm
        return tot
    before = resultant(messy.to_dict(), "B1")
    after = sum(resultant(new, u) for u in uids)
    assert after == pytest.approx(before, rel=1e-12)
    # point load at the cut goes ONCE, to the start of the piece after it
    pts = [(u, ld["a"], ld["w"]) for u in uids for ld in _loads(new, u)
           if ld["kind"] == "point"]
    assert sorted(pts) == [("B1", pytest.approx(0.2 / 0.3), 7.0),
                           ("B3", 0.0, 20.0)]
    # trapezoid ordinates interpolated at the cuts: w(x) = 5 + 10 (s-.1)/.8
    tr = {u: _loads(new, u)[0] for u in uids}
    assert tr["B1"]["w2"] == pytest.approx(5 + 10 * 0.2 / 0.8)
    assert tr["B2"]["w"] == pytest.approx(5 + 10 * 0.2 / 0.8)
    assert tr["B2"]["w2"] == pytest.approx(5 + 10 * 0.4 / 0.8)
    assert tr["B3"]["w2"] == pytest.approx(15.0)
    # legacy full-span UDL applies to every piece
    assert [len(_loads(new, u, key="member_udls")) for u in uids] == [1, 1, 1]
    d1, r1, f1 = _run(plain)
    d2, r2, f2 = _run(_bm(new))
    for p in ((0.0, 0.0, 0.0), (L, 0.0, 0.0)):
        assert d2[p] == pytest.approx(d1[p], rel=1e-9, abs=1e-14)
        assert r2[p] == pytest.approx(r1[p], rel=1e-9, abs=1e-9)
    assert f2["B1"][:6] == pytest.approx(f1["B1"][:6], rel=1e-9, abs=1e-9)
    assert f2["B3"][6:] == pytest.approx(f1["B1"][6:], rel=1e-9, abs=1e-9)


def test_split_point_load_and_nodal_load_at_midspan_closed_form():
    """A nodal load on the span (unconnected before) acts after the split:
    delta_mid = 5 w L^4 / 384 EI + P L^3 / 48 EI."""
    w, P = 10.0, 30.0
    mdl = _beam(False)
    mdl.patterns["DEAD"].nodal_loads.append(NodalLoad((L / 2, 0, 0), fz=-P))
    chk = check_model(mdl)
    assert "JOINT_UNCONNECTED" in chk["summary"]["by_code"]
    new, rep = R.fix_split_at_joints(mdl.to_dict())
    assert rep["count"] == 1
    d2, r2, _f = _run(_bm(new))
    EI = E * I
    assert d2[(L / 2, 0.0, 0.0)][2] == pytest.approx(
        -(5 * w * L ** 4 / (384 * EI) + P * L ** 3 / (48 * EI)), rel=1e-6)
    assert r2[(0.0, 0.0, 0.0)][2] == pytest.approx(w * L / 2 + P / 2)
    assert check_model(_bm(new))["summary"]["by_code"] == {}


def test_split_keeps_releases_at_outer_ends_and_refs():
    mdl = _beam(False, extra_points=[(2.0, 0, 0)])
    mdl.members[0].releases = "Mi"
    mdl.supports[0] = PointSupport((0.0, 0.0, 0.0), (1, 1, 1, 1, 1, 1))
    mdl.patterns["DEAD"].thermal_loads.append(ThermalLoad("B1", dT=5.0))
    mdl.add_group("G", members=["B1"])
    new, _rep = R.fix_split_at_joints(mdl.to_dict())
    assert [_mem(new, u)["releases"] for u in ("B1", "B2")] == ["Mi", ""]
    assert new["groups"]["G"]["members"] == ["B1", "B2"]
    assert [len(_loads(new, u, key="thermal_loads"))
            for u in ("B1", "B2")] == [1, 1]


def test_split_skips_unsplittable_members_with_warning():
    mdl = _beam(False, extra_points=[(2.0, 0, 0)])
    mdl.members[0].axial_limit = "tension"
    new, rep = R.fix_split_at_joints(mdl.to_dict())
    assert not rep["changed"]
    assert any("axial-only" in w for w in rep["warnings"])
    assert _dump(new) == _dump(mdl.to_dict())


# --------------------------------------------------------------------------- #
# crossings
# --------------------------------------------------------------------------- #
def test_split_at_crossings_divides_both_frames_at_one_joint():
    mdl = _base()
    mdl.add_member("beam", "BEAM", (0, 0, 3), (6, 0, 3), uid="B1")
    mdl.add_member("beam", "BEAM", (2, -3, 3.0004), (2, 3, 3.0004), uid="B2")
    mdl.add_member("beam", "BEAM", (6, -1, 3), (6, 1, 3), uid="B3")  # T only
    d = mdl.to_dict()
    # the T (B1's end on B3's span) is split_at_joints' job, not this fix
    assert R.fix_split_at_joints(d)[1]["count"] == 1
    new, rep = R.fix_split_at_crossings(d)
    assert rep["count"] == 1
    X = [2.0, 0.0, 3.0002]
    by = {m["uid"]: m for m in new["members"]}
    assert len(by) == 5
    ends = [tuple(m[e]) for m in new["members"] for e in ("pi", "pj")]
    assert ends.count(tuple(X)) == 4
    assert by["B1"]["pj"] == X and by["B2"]["pj"] == X
    # parallel / far frames untouched; crossings need the opt-in fix
    assert R.fix_split_at_crossings(new)[1]["changed"] is False


# --------------------------------------------------------------------------- #
# merge joints
# --------------------------------------------------------------------------- #
def _gap_portal(gap=0.0005):
    """Fixed-base columns, PINNED beam whose j end misses column 2 by gap."""
    mdl = _base()
    Hc = 3.0
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, Hc), uid="C1")
    mdl.add_member("column", "COL", (L, 0, 0), (L, 0, Hc), uid="C2")
    mdl.add_member("beam", "BEAM", (0, 0, Hc), (L + gap, 0, Hc), uid="B1",
                   releases="Mi,Mj")
    for x in (0.0, L):
        mdl.supports.append(PointSupport((x, 0, 0), (1, 1, 1, 1, 1, 1)))
    pat = mdl.pattern("DEAD", "dead")
    pat.member_loads.append(MemberLoad("B1", w=12.0))
    pat.nodal_loads.append(NodalLoad((L + gap, 0, Hc), fz=-4.0))
    mdl.nodal_masses.append(NodalMass((L, 0, Hc), mx=1.0))
    mdl.add_case("DEAD", {"DEAD": 1.0})
    mdl.th_cases = {}
    mdl.add_group("G", points=[[L + gap, 0, Hc], [L, 0, Hc]])
    mdl.validate()
    return mdl


def test_merge_duplicate_joints_makes_unstable_model_stable():
    mdl = _gap_portal()
    before = stability_diagnostics(mdl)
    assert before["stable"] is False and before["n_mechanisms"] >= 1
    new, rep = R.fix_merge_joints(mdl.to_dict())
    assert rep["count"] == 1
    act = rep["actions"][0]
    assert act["to"] == [L, 0.0, 3.0] and act["merged"] == [[L + 0.0005,
                                                              0.0, 3.0]]
    assert _mem(new, "B1")["pj"] == [L, 0.0, 3.0]
    # references follow: nodal load, group points (deduplicated)
    assert new["patterns"]["DEAD"]["nodal_loads"][0]["point"] == [L, 0.0, 3.0]
    assert new["groups"]["G"]["points"] == [[L, 0.0, 3.0]]
    mdl2 = _bm(new)
    after = stability_diagnostics(mdl2)
    assert after["stable"] is True
    _d, reac, f = _run(mdl2)
    w = 12.0
    # pinned beam: each column carries w L / 2 (+ the 4 kN joint load on C2)
    # and no base moment under gravity
    assert reac[(0.0, 0.0, 0.0)][2] == pytest.approx(w * L / 2, rel=1e-9)
    assert reac[(L, 0.0, 0.0)][2] == pytest.approx(w * L / 2 + 4.0, rel=1e-9)
    for p in ((0.0, 0.0, 0.0), (L, 0.0, 0.0)):
        assert abs(reac[p][4]) < 1e-6 * w * L * L
    # the pinned beam's end moments vanish
    assert abs(f["B1"][5]) < 1e-6 and abs(f["B1"][11]) < 1e-6
    assert abs(f["B1"][4]) < 1e-6 and abs(f["B1"][10]) < 1e-6


def test_merge_remaps_every_reference_kind():
    mdl = _gap_portal(0.0008)
    from skyframe.core.model import SpringSupport
    p_off = (L + 0.0008, 0, 3.0)
    mdl.spring_supports.append(SpringSupport(p_off, [0, 0, 1e5, 0, 0, 0]))
    d = mdl.to_dict()
    d["th_cases"] = {}
    d["steady_state_cases"] = {"SS": {"output_points": [list(p_off)]}}
    new, rep = R.fix_merge_joints(d, tolerance=0.001)
    # a supported joint wins the merge (Edit > Merge Joints rule): the
    # spring point; everything else follows it
    P = [L + 0.0008, 0.0, 3.0]
    assert rep["actions"][0]["to"] == P
    pts = [new["spring_supports"][0]["point"], _mem(new, "C2")["pj"],
           _mem(new, "B1")["pj"], new["nodal_masses"][0]["point"],
           new["patterns"]["DEAD"]["nodal_loads"][0]["point"],
           *new["groups"]["G"]["points"],
           *new["steady_state_cases"]["SS"]["output_points"]]
    assert [[float(v) for v in p] for p in pts] == [P] * 7
    # tolerance smaller than the gap: nothing to merge, input unchanged
    same, rep2 = R.fix_merge_joints(d, tolerance=0.0005)
    assert not rep2["changed"] and _dump(same) == _dump(d)


# --------------------------------------------------------------------------- #
# zero-length / duplicates / overlaps
# --------------------------------------------------------------------------- #
def test_remove_zero_length_merges_its_two_joints():
    mdl = _base()
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, 3), uid="C1")
    mdl.add_member("beam", "BEAM", (0, 0, 3), (0, 0, 3.0004), uid="Z1")
    mdl.add_member("beam", "BEAM", (0, 0, 3.0004), (5, 0, 3.0004), uid="B1")
    mdl.supports.append(PointSupport((0, 0, 0), (1,) * 6))
    pat = mdl.pattern("DEAD", "dead")
    pat.member_loads.append(MemberLoad("Z1", kind="point", w=1.0, a=0.5))
    mdl.add_case("DEAD", {"DEAD": 1.0})
    new, rep = R.fix_remove_zero_length(mdl.to_dict())
    assert [m["uid"] for m in new["members"]] == ["C1", "B1"]
    # the busier joint (column top: C1 + Z1) wins; B1 follows it
    assert _mem(new, "B1")["pi"] == [0.0, 0.0, 3.0]
    assert rep["actions"][0]["loads_dropped"] == 1
    assert rep["deleted"]["members"] == ["Z1"]
    assert _loads(new, "Z1") == []


def test_remove_duplicates_keeps_loaded_copy_and_moves_refs():
    mdl = _base()
    mdl.add_member("beam", "BEAM", (0, 0, 3), (6, 0, 3), uid="B1")
    mdl.add_member("beam", "BEAM", (6, 0, 3), (0, 0, 3), uid="B2")  # reversed
    pat = mdl.pattern("DEAD", "dead")
    pat.member_loads.append(MemberLoad("B2", w=4.0))
    mdl.add_group("G", members=["B1"])
    new, rep = R.fix_remove_duplicates(mdl.to_dict())
    assert [m["uid"] for m in new["members"]] == ["B2"]
    assert new["groups"]["G"]["members"] == ["B2"]
    assert rep["actions"][0]["kept"] == "B2"
    assert rep["actions"][0]["loads_dropped"] == 0


def test_remove_duplicates_resolves_collinear_overlap():
    mdl = _base()
    mdl.add_member("beam", "BEAM", (0, 0, 3), (6, 0, 3), uid="B1")
    mdl.add_member("beam", "BEAM", (4, 0, 3), (10, 0, 3), uid="B2")
    mdl.add_member("beam", "BEAM", (1, 0, 3), (2, 0, 3), uid="B3")  # inside
    assert check_model(mdl)["summary"]["by_code"].get("FRAME_OVERLAP") == 2
    new, rep = R.fix_remove_duplicates(mdl.to_dict())
    segs = sorted(tuple(sorted((m["pi"][0], m["pj"][0])))
                  for m in new["members"])
    assert segs == [(0, 1), (1, 2), (2, 4), (4, 6), (6, 10)]
    by = check_model(_bm(new))["summary"]["by_code"]
    assert "FRAME_OVERLAP" not in by and "FRAME_DUPLICATE" not in by


def test_remove_duplicate_shells():
    mdl = _base()
    c = [(0, 0, 3), (4, 0, 3), (4, 4, 3), (0, 4, 3)]
    mdl.add_shell("slab", "shell", "SL", c, uid="S1")
    mdl.add_shell("slab", "shell", "SL", c[1:] + c[:1], uid="S2")
    new, rep = R.fix_remove_duplicates(mdl.to_dict())
    assert [s["uid"] for s in new["shells"]] == ["S1"]


# --------------------------------------------------------------------------- #
# shells / unconnected joints
# --------------------------------------------------------------------------- #
def _slab_on_columns(off=0.0006):
    mdl = _base()
    for k, (x, y) in enumerate([(0, 0), (4, 0), (4, 4), (0, 4)]):
        mdl.add_member("column", "COL", (x, y, 0), (x, y, 3), uid=f"C{k}")
        mdl.supports.append(PointSupport((x, y, 0), (1,) * 6))
    mdl.add_shell("slab", "shell", "SL",
                  [(0 + off, 0 + off, 3), (4, 0, 3), (4 - off, 4, 3),
                   (0, 4, 3)], uid="S1", mesh_size=2.0)
    pat = mdl.pattern("DEAD", "dead")
    from skyframe.core.model import AreaLoad
    pat.area_loads.append(AreaLoad("S1", 5.0))
    mdl.add_case("DEAD", {"DEAD": 1.0})
    mdl.validate()
    return mdl


def test_snap_shell_corners_to_column_tops():
    mdl = _slab_on_columns()
    assert "JOINT_COINCIDENT" in check_model(mdl)["summary"]["by_code"]
    new, rep = R.fix_snap_shell_corners(mdl.to_dict())
    assert rep["count"] == 2
    assert new["shells"][0]["corners"] == [[0.0, 0.0, 3.0], [4.0, 0.0, 3.0],
                                           [4.0, 4.0, 3.0], [0.0, 4.0, 3.0]]
    # frames never move
    assert [m["pj"] for m in new["members"]] == \
        [m["pj"] for m in mdl.to_dict()["members"]]
    assert check_model(_bm(new))["summary"]["by_code"] == {}


def test_snap_skips_a_corner_that_would_warp_the_shell():
    mdl = _base()
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, 2.9995), uid="C0")
    mdl.add_shell("slab", "shell", "SL",
                  [(0, 0, 3), (4, 0, 3), (4, 4, 3), (0, 4, 3)], uid="S1")
    new, rep = R.fix_snap_shell_corners(mdl.to_dict())
    assert not rep["changed"]
    assert any("S1" in w for w in rep["warnings"])


def test_remove_unconnected_joints_keeps_shell_mesh_nodes():
    mdl = _slab_on_columns(0.0)
    mdl.nodal_masses.append(NodalMass((9, 9, 9), mx=1.0))      # isolated
    mdl.nodal_masses.append(NodalMass((2, 2, 3), mx=1.0))      # mesh node
    mdl.patterns["DEAD"].nodal_loads.append(NodalLoad((9, 9, 9), fz=-1.0))
    mdl.add_group("G", points=[[9, 9, 9], [0, 0, 3]])
    new, rep = R.fix_remove_unconnected_joints(mdl.to_dict())
    assert rep["count"] == 1
    assert rep["actions"][0]["records"] == ["nodal mass", "nodal load",
                                            "group G point"]
    assert [m["point"] for m in new["nodal_masses"]] == [[2.0, 2.0, 3.0]]
    assert new["patterns"]["DEAD"]["nodal_loads"] == []
    assert new["groups"]["G"]["points"] == [[0.0, 0.0, 3.0]]


# --------------------------------------------------------------------------- #
# report only / pipeline / idempotency / clean model
# --------------------------------------------------------------------------- #
def test_report_mechanisms_reports_without_changing():
    mdl = _gap_portal()
    d = mdl.to_dict()
    new, rep = R.report_mechanisms(d)
    assert _dump(new) == _dump(d) and rep["changed"] is False
    assert rep["stability"]["stable"] is False
    assert any(a.get("code") == "STABILITY_MECHANISM" for a in rep["actions"])


def _messy():
    """Every defect at once."""
    mdl = _gap_portal()
    mdl.add_member("beam", "BEAM", (0, 0, 3), (L + 0.0005, 0, 3), uid="B9")
    mdl.add_member("brace", "BEAM", (0, 0, 1.5), (0, 0, 1.5002), uid="Z1")
    mdl.add_member("beam", "BEAM", (0, -2, 1.5), (0, 2, 1.5), uid="X1")
    mdl.add_member("beam", "BEAM", (3, -2, 3.0), (3, 2, 3.0), uid="X2")
    mdl.add_shell("slab", "shell", "SL",
                  [(0.0004, 0, 3), (0, 3, 3), (-3, 3, 3), (-3, 0, 3)],
                  uid="S1", mesh_size=1.5)
    mdl.nodal_masses.append(NodalMass((20, 20, 20), mx=1.0))
    mdl.nodal_masses.append(NodalMass((0, 0, 2.0), mx=0.5))   # on C1 span
    return mdl


def test_every_fix_is_idempotent():
    d = _messy().to_dict()
    for fix in R.REPAIR_FIXES:
        once, r1 = R.FIX_FUNCS[fix](d)
        twice, r2 = R.FIX_FUNCS[fix](once)
        assert _dump(twice) == _dump(once), fix
        if fix != "report_mechanisms":
            assert r2["changed"] is False, fix
    once, rep = R.repair(d, "all")
    assert rep["changed"]
    twice, rep2 = R.repair(once, "all")
    assert _dump(twice) == _dump(once) and rep2["changed"] is False


def test_pipeline_repairs_the_messy_model():
    d = _messy().to_dict()
    new, rep = R.repair(d, "all")
    assert rep["fixes"] == list(R.REPAIR_FIXES)
    c = rep["counts"]
    # the merge (3 clusters) collapses the zero-length frame Z1, makes B9 an
    # exact duplicate of B1 and moves the slab corner onto the column top:
    # all handled by the merge already
    assert c["merge_joints"] == 5
    assert sorted(rep["deleted"]["members"]) == ["B9", "Z1"]
    assert c["remove_zero_length"] == 0 and c["remove_duplicates"] == 0
    assert c["snap_shell_corners"] == 0
    assert c["split_at_joints"] == 1 and c["split_at_crossings"] == 2
    assert c["remove_unconnected_joints"] == 1
    assert rep["check_before"]["errors"] > 0
    assert rep["check_after"]["by_code"] == {}
    assert rep["stability"]["stable"] is True
    _bm(new)                                     # validates
    # the input is never mutated
    assert _dump(d) == _dump(_messy().to_dict())


def test_pipeline_without_merge_uses_the_dedicated_fixes():
    d = _messy().to_dict()
    fixes = [f for f in R.REPAIR_FIXES if f != "merge_joints"]
    new, rep = R.repair(d, fixes)
    c = rep["counts"]
    assert c["remove_zero_length"] == 1 and c["remove_duplicates"] == 1
    assert c["snap_shell_corners"] == 1
    assert rep["deleted"]["members"] == ["Z1", "B9"]
    by = rep["check_after"]["by_code"]
    assert set(by) == {"JOINT_COINCIDENT"}       # only the beam gap is left
    assert rep["stability"]["stable"] is False   # ... and it is a mechanism


def test_clean_model_unchanged_byte_for_byte():
    for mdl in (quick_building(), _slab_on_columns(0.0), _beam(True)):
        d = BuildingModel.from_dict(mdl.to_dict()).to_dict()   # canonical
        assert check_model(mdl)["summary"]["errors"] == 0
        for fix in R.REPAIR_FIXES:
            new, rep = R.FIX_FUNCS[fix](d)
            assert _dump(new) == _dump(d), fix
            assert rep["count"] == 0 or fix == "report_mechanisms"
        new, rep = R.repair(d, "all")
        assert _dump(new) == _dump(d) and rep["changed"] is False
        echo, _r = R.repair_json(d, "all")
        assert _dump(echo) == _dump(d)


def test_building_model_in_building_model_out():
    mdl = _gap_portal()
    new, rep = R.fix_merge_joints(mdl)
    assert isinstance(new, BuildingModel) and rep["changed"]
    assert mdl.members[2].pj[0] == pytest.approx(L + 0.0005)   # untouched


def test_bad_input():
    d = _beam().to_dict()
    with pytest.raises(ValueError):
        R.repair(d, ["nope"])
    with pytest.raises(ValueError):
        R.repair(d, "merge_joints")
    for tol in (0, -1, 2.0, float("nan"), True):
        with pytest.raises(ValueError):
            R.repair(d, None, tol)
    assert R.normalize_fixes(None) == list(R.DEFAULT_FIXES)
    assert R.normalize_fixes(["report_mechanisms", "merge_joints"]) == \
        ["merge_joints", "report_mechanisms"]


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
@pytest.fixture
def client():
    import skyframe.api.server as srv
    app = srv.create_app()
    old = srv._state["model"]
    with app.test_client() as c:
        yield c, srv
    srv._state["model"] = old


def test_api_repair_dry_run_and_apply(client):
    c, srv = client
    srv._state["model"] = _gap_portal()
    stored = _dump(srv._state["model"].to_dict())
    r = c.post("/api/repair", json={"fixes": ["merge_joints"],
                                    "tolerance": 0.001, "dry_run": True})
    assert r.status_code == 200
    j = r.get_json()
    assert set(j) == {"model", "report"}
    assert j["report"]["counts"] == {"merge_joints": 1}
    assert j["report"]["dry_run"] is True
    assert _dump(srv._state["model"].to_dict()) == stored     # not replaced
    r = c.post("/api/repair", json={"fixes": ["merge_joints"]})
    assert r.status_code == 200
    assert srv._state["model"].members[2].pj == pytest.approx((L, 0, 3.0))
    # explicit body model, clean -> echoed byte-identically, not stored
    clean = _bm(_beam(True).to_dict()).to_dict()
    r = c.post("/api/repair", json={"model": clean, "fixes": "all",
                                    "dry_run": True})
    srt = lambda x: json.dumps(x, sort_keys=True)     # noqa: E731
    assert srt(r.get_json()["model"]) == srt(clean)
    assert r.get_json()["report"]["changed"] is False


def test_api_repair_bad_requests(client):
    c, _srv = client
    assert c.post("/api/repair", data="x").status_code == 400
    assert c.post("/api/repair", json={"fixes": ["bogus"]}).status_code == 400
    assert c.post("/api/repair", json={"tolerance": 0}).status_code == 400
    assert c.post("/api/repair", json={"tolerance": "1"}).status_code == 400
    assert c.post("/api/repair",
                  json={"model": {"members": "x"}}).status_code == 400
