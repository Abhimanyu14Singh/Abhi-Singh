"""Frame auto mesh (ETABS Assign > Frame > Frame Auto Mesh Options) and
output stations (Assign > Frame > Output Stations) — core.framemesh.

Hand checks: two-way grillage load sharing by relative stiffness, a
secondary beam framing into a girder's span, max_length splitting
(static identity with exact elements, modal convergence toward the
continuous simply supported beam), output stations at max_spacing.
"""

from __future__ import annotations

import json
import math
import warnings

import pytest

from skyframe.core import framemesh as fm
from skyframe.core.checks import check_model
from skyframe.core.mesh import mesh_model
from skyframe.core.model import (G_ACCEL, BuildingModel, FrameSection,
                                 Material, MemberLoad, MemberUDL,
                                 PointSupport, SectionCut, ShellRegion,
                                 ShellSection, StoryForce)
from skyframe.engine.opensees_engine import (OpenSeesEngine,
                                             compute_section_cut)

E = 2.0e8            # kPa
H = 3.0              # story height (beams at z = H)
A_ = 0.01
I33 = 2.0e-4
I22 = 1.0e-4
J_ = 1.0e-4


def _base(name="t"):
    m = BuildingModel(name)
    m.add_material(Material("S", E, 0.3, 0.0, material_type="steel"))
    m.add_section(FrameSection("B", "S", A_, I33, I22, J_))
    m.add_section(FrameSection("B2", "S", A_, 3.0 * I33, I22, J_))
    m.set_stories([H])
    m.rigid_diaphragms = False
    return m


def _grillage(auto: bool, P: float = 100.0):
    """Beam A along X (L=8, I), beam B along Y (L=6, 3I) crossing at both
    midspans without a shared joint; P on beam A at its midspan."""
    m = _base("grillage")
    m.add_member("beam", "B", (0, 3, H), (8, 3, H), story="Story1", uid="A")
    m.add_member("beam", "B2", (4, 0, H), (4, 6, H), story="Story1",
                 uid="BB")
    for p in ((0, 3, H), (8, 3, H)):
        m.supports.append(PointSupport(p, (1, 1, 1, 1, 0, 0)))
    for p in ((4, 0, H), (4, 6, H)):
        m.supports.append(PointSupport(p, (1, 1, 1, 0, 1, 0)))
    pat = m.pattern("P", "live")
    pat.member_loads.append(MemberLoad("A", "point", P, 0.0, 0.5, 1.0))
    m.add_case("P", {"P": 1.0})
    if auto:
        m.members[0].auto_mesh = {"at_intersections": True}
    m.validate()
    return m


def _rz(eng, res, p):
    return res.reactions[eng._find_node(eng._asm, p)][2]


# --------------------------------------------------------------------------- #
# 1. grillage: crossing beams
# --------------------------------------------------------------------------- #
def test_crossing_beams_independent_without_auto_mesh():
    m = _grillage(False)
    eng = OpenSeesEngine(m)
    r = eng.run_static("P")
    assert _rz(eng, r, (0, 3, H)) == pytest.approx(50.0, rel=1e-9)
    assert _rz(eng, r, (8, 3, H)) == pytest.approx(50.0, rel=1e-9)
    assert abs(_rz(eng, r, (4, 0, H))) < 1e-9
    assert len(eng._mesh.segments["A"]) == 1
    assert len(eng._mesh.segments["BB"]) == 1


def test_crossing_beams_grillage_load_sharing_exact():
    P = 100.0
    m = _grillage(True, P)
    eng = OpenSeesEngine(m)
    r = eng.run_static("P")
    kA = 48 * E * I33 / 8.0 ** 3
    kB = 48 * E * 3 * I33 / 6.0 ** 3
    PA = P * kA / (kA + kB)
    PB = P - PA
    assert len(eng._mesh.segments["A"]) == 2
    assert len(eng._mesh.segments["BB"]) == 2   # partner divided too
    assert _rz(eng, r, (0, 3, H)) == pytest.approx(PA / 2, rel=1e-8)
    assert _rz(eng, r, (8, 3, H)) == pytest.approx(PA / 2, rel=1e-8)
    assert _rz(eng, r, (4, 0, H)) == pytest.approx(PB / 2, rel=1e-8)
    assert _rz(eng, r, (4, 6, H)) == pytest.approx(PB / 2, rel=1e-8)
    # stitched stations: 11 stations along each drawn object, exact
    stA = r.member_stations["A"]
    stB = r.member_stations["BB"]
    assert len(stA["x"]) == 11 and len(stB["x"]) == 11
    assert abs(stA["M3"][5]) == pytest.approx(PA * 8 / 4, rel=1e-8)
    assert abs(stB["M3"][5]) == pytest.approx(PB * 6 / 4, rel=1e-8)
    # common deflection at the crossing = P_A / k_A
    tag = eng._find_node(eng._asm, (4, 3, H))
    assert r.node_disp[tag][2] == pytest.approx(-PA / kA, rel=1e-8)


def test_intersection_point_in_mesh_and_check_model():
    m0 = _grillage(False)
    codes0 = [i["code"] for i in check_model(m0)["issues"]]
    assert "FRAME_INTERSECTION" in codes0
    m1 = _grillage(True)
    codes1 = [i["code"] for i in check_model(m1)["issues"]]
    assert "FRAME_INTERSECTION" not in codes1
    mesh = mesh_model(m1)
    assert mesh.auto_split == {"A", "BB"}
    na = {mesh.segments["A"][0].nj}
    nb = {mesh.segments["BB"][0].nj}
    assert na == nb                                # shared node
    assert mesh.points[na.pop()] == (4.0, 3.0, H)


def test_model_wide_default_and_member_override():
    m = _grillage(False)
    m.frame_auto_mesh = {"at_intersections": True}
    m.validate()
    assert mesh_model(m).auto_split == {"A", "BB"}
    # a member dict replaces the default as a whole -> A off, BB still on
    m.members[0].auto_mesh = {"at_intersections": False}
    assert mesh_model(m).auto_split == {"A", "BB"}   # BB divides both
    m.members[1].auto_mesh = {}
    assert mesh_model(m).auto_split == set()


# --------------------------------------------------------------------------- #
# 2. secondary beam framing into a girder's span
# --------------------------------------------------------------------------- #
def _girder(auto: bool, w: float = 10.0):
    m = _base("girder")
    m.add_member("beam", "B2", (0, 0, H), (8, 0, H), story="Story1", uid="G")
    m.add_member("beam", "B", (4, 0, H), (4, 6, H), story="Story1", uid="S",
                 releases="Mi")
    m.supports.append(PointSupport((0, 0, H), (1, 1, 1, 1, 0, 0)))
    m.supports.append(PointSupport((8, 0, H), (1, 1, 1, 1, 0, 0)))
    m.supports.append(PointSupport((4, 6, H), (1, 1, 1, 0, 1, 0)))
    pat = m.pattern("D", "dead")
    pat.member_udls.append(MemberUDL("S", w))
    m.add_case("D", {"D": 1.0})
    if auto:
        m.members[0].auto_mesh = {"at_intermediate_joints": True}
    m.validate()
    return m


def test_secondary_beam_girder_carries_reaction():
    w = 10.0
    m = _girder(True, w)
    eng = OpenSeesEngine(m)
    r = eng.run_static("D")
    R = w * 6.0 / 2.0
    assert _rz(eng, r, (0, 0, H)) == pytest.approx(R / 2, rel=1e-8)
    assert _rz(eng, r, (8, 0, H)) == pytest.approx(R / 2, rel=1e-8)
    assert _rz(eng, r, (4, 6, H)) == pytest.approx(R, rel=1e-8)
    st = r.member_stations["G"]
    assert abs(st["M3"][5]) == pytest.approx(R * 8.0 / 4.0, rel=1e-8)
    assert abs(st["V2"][0]) == pytest.approx(R / 2, rel=1e-8)
    assert abs(r.member_stations["S"]["M3"][5]) == pytest.approx(
        w * 36.0 / 8.0, rel=1e-8)


def test_joint_on_span_check_model():
    codes0 = [i["code"] for i in check_model(_girder(False))["issues"]]
    assert "FRAME_JOINT_ON_SPAN" in codes0
    codes1 = [i["code"] for i in check_model(_girder(True))["issues"]]
    assert "FRAME_JOINT_ON_SPAN" not in codes1


def test_intermediate_joint_support_on_span():
    """A point support on a span becomes a node: two-span continuous beam
    under UDL -> middle reaction 5wL/4 (L = each span)."""
    m = _base("cont")
    m.add_member("beam", "B", (0, 0, H), (10, 0, H), story="Story1",
                 uid="C")
    for p in ((0, 0, H), (10, 0, H)):
        m.supports.append(PointSupport(p, (1, 1, 1, 1, 0, 0)))
    m.supports.append(PointSupport((5, 0, H), (0, 1, 1, 0, 0, 0)))
    pat = m.pattern("D", "dead")
    pat.member_udls.append(MemberUDL("C", 4.0))
    m.add_case("D", {"D": 1.0})
    m.members[0].auto_mesh = {"at_intermediate_joints": True}
    m.validate()
    eng = OpenSeesEngine(m)
    r = eng.run_static("D")
    assert _rz(eng, r, (5, 0, H)) == pytest.approx(1.25 * 4.0 * 5.0,
                                                  rel=1e-8)


# --------------------------------------------------------------------------- #
# 3. max_length / min_segments
# --------------------------------------------------------------------------- #
def _ss_beam(L=6.0, auto=None, stations=None, w=5.0, P=20.0, a=0.3):
    m = _base("ss")
    m.add_member("beam", "B", (0, 0, H), (L, 0, H), story="Story1",
                 uid="M")
    m.supports.append(PointSupport((0, 0, H), (1, 1, 1, 1, 0, 0)))
    m.supports.append(PointSupport((L, 0, H), (0, 1, 1, 0, 0, 0)))
    pat = m.pattern("D", "dead")
    if w:
        pat.member_udls.append(MemberUDL("M", w))
        pat.member_loads.append(MemberLoad("M", "trapezoid", 2.0, 6.0,
                                           0.1, 0.7))
    if P:
        pat.member_loads.append(MemberLoad("M", "point", P, 0.0, a, 1.0))
    m.add_case("D", {"D": 1.0})
    m.members[0].auto_mesh = auto
    m.members[0].output_stations = stations
    m.validate()
    return m


@pytest.mark.parametrize("auto", [{"max_length": 1.0},
                                  {"min_segments": 4},
                                  {"min_segments": 3, "max_length": 0.7}])
def test_max_length_static_identical_to_unsplit(auto):
    # exact elements: identical to round-off (auto-mesh stations are taken
    # from the 1e-6-rounded pool points, so segment lengths match the FE
    # node geometry even for L/9 cuts)
    TOL_S = 1e-10
    r0 = OpenSeesEngine(_ss_beam()).run_static("D")
    eng = OpenSeesEngine(_ss_beam(auto=auto))
    r1 = eng.run_static("D")
    assert len(eng._mesh.segments["M"]) > 1
    for a, b in zip(r0.member_forces["M"], r1.member_forces["M"]):
        assert b == pytest.approx(a, rel=TOL_S, abs=1e-8)
    for key in ("V2", "M3", "N"):
        for a, b in zip(r0.member_stations["M"][key],
                        r1.member_stations["M"][key]):
            assert b == pytest.approx(a, rel=TOL_S, abs=1e-8)
    d0 = r0.member_deflections["M"]["dy"]
    d1 = r1.member_deflections["M"]["dy"]
    for a, b in zip(d0, d1):
        assert b == pytest.approx(a, rel=100 * TOL_S, abs=1e-12)


def test_segment_counts():
    L = 6.0
    for auto, n in (({"max_length": 1.0}, 6), ({"max_length": 1.1}, 6),
                    ({"min_segments": 4}, 4),
                    ({"min_segments": 2, "max_length": 2.0}, 4)):
        mesh = mesh_model(_ss_beam(L, auto=auto))
        segs = mesh.segments["M"]
        assert len(segs) == n
        assert sum(s.length for s in segs) == pytest.approx(L, abs=1e-9)
        assert max(s.length for s in segs) <= (auto.get("max_length")
                                               or L) + 1e-9


def _modal_beam(nseg):
    L, w = 8.0, 12.0
    m = _base("modal")
    m.add_member("beam", "B", (0, 0, H), (L, 0, H), story="Story1",
                 uid="M")
    m.supports.append(PointSupport((0, 0, H), (1, 1, 1, 1, 0, 0)))
    m.supports.append(PointSupport((L, 0, H), (0, 1, 1, 0, 0, 0)))
    pat = m.pattern("SDL", "dead")
    pat.member_udls.append(MemberUDL("M", w))
    m.mass_source = {"SDL": 1.0}
    m.mass_options.update(include_vertical=True, include_lateral=False,
                          lump_at_stories=False)
    m.members[0].auto_mesh = {"max_length": L / nseg}
    m.validate()
    mbar = w / G_ACCEL
    f_exact = math.pi / (2.0 * L * L) * math.sqrt(E * I33 / mbar)
    return m, f_exact


def test_max_length_modal_converges_to_continuous_beam():
    errs = []
    for n in (2, 4, 8, 16):
        m, f_exact = _modal_beam(n)
        mo = OpenSeesEngine(m).run_modal(1)
        errs.append(abs(mo.frequencies[0] - f_exact) / f_exact)
    assert all(e1 < e0 for e0, e1 in zip(errs, errs[1:])), errs
    assert errs[-1] < 5e-6, errs          # n=16: ~1.0e-6
    # n=2 lumped single mass: f = sqrt(48EI/(m L^3 /2)) / 2pi (hand)
    m, f_exact = _modal_beam(2)
    M = 12.0 / G_ACCEL * 8.0 / 2.0
    f2 = math.sqrt(48 * E * I33 / 8.0 ** 3 / M) / (2 * math.pi)
    assert OpenSeesEngine(m).run_modal(1).frequencies[0] == pytest.approx(
        f2, rel=1e-6)


# --------------------------------------------------------------------------- #
# 4. output stations
# --------------------------------------------------------------------------- #
def test_output_stations_max_spacing_count_and_exact_moment():
    L, P, a = 5.0, 20.0, 0.34
    m = _ss_beam(L, w=0.0, P=P, a=a, stations={"max_spacing": 1.0})
    r = OpenSeesEngine(m).run_static("D")
    st = r.member_stations["M"]
    xs = st["x"]
    # 6 equal stations (spacing 1.0) + the point-load station 1.7
    assert len(xs) == 7
    assert xs[0] == 0.0 and xs[-1] == L
    k = min(range(len(xs)), key=lambda i: abs(xs[i] - a * L))
    assert xs[k] == pytest.approx(a * L, abs=1e-12)
    assert abs(st["M3"][k]) == pytest.approx(P * a * L * (1 - a), rel=1e-9)
    for key in ("N", "V2", "V3", "T", "M2", "M3"):
        assert len(st[key]) == 7
    md = r.member_deflections["M"]
    assert md["x"] == xs and len(md["dy"]) == 7


def test_output_stations_min_number_and_segment_ends():
    m = _ss_beam(6.0, P=0.0, stations={"min_number": 4},
                 auto={"max_length": 2.5, "min_segments": 5})
    eng = OpenSeesEngine(m)
    r = eng.run_static("D")
    xs = r.member_stations["M"]["x"]
    segs = eng._mesh.segments["M"]
    assert [s.x0 for s in segs] == pytest.approx([0, 1.2, 2.4, 3.6, 4.8])
    expect = sorted({0.0, 2.0, 4.0, 6.0} | {s.x0 for s in segs})
    assert xs == pytest.approx(expect, abs=1e-9)
    assert len(xs) == 8           # {0, 2, 4, 6} + segment ends 1.2 .. 4.8
    # every station value still exact: M3 at x with a full UDL + trapezoid
    r0 = OpenSeesEngine(_ss_beam(6.0, P=0.0,
                                 stations={"min_number": 4})).run_static("D")
    for x, v in zip(r0.member_stations["M"]["x"],
                    r0.member_stations["M"]["M3"]):
        i = xs.index(min(xs, key=lambda q: abs(q - x)))
        assert r.member_stations["M"]["M3"][i] == pytest.approx(v, rel=1e-8,
                                                                abs=1e-9)


def test_default_stations_are_the_fixed_eleven():
    m = _ss_beam(6.0)
    assert fm.station_xs(m, m.members[0]) == [k * 6.0 / 10 for k in
                                              range(11)]
    r = OpenSeesEngine(m).run_static("D")
    assert r.member_stations["M"]["x"] == [k * 6.0 / 10 for k in range(11)]


def test_combos_and_section_cut_with_variable_stations():
    m = _ss_beam(5.0, w=3.0, P=10.0, a=0.34, stations={"max_spacing": 0.8})
    m.pattern("L", "live").member_loads.append(
        MemberLoad("M", "point", 7.0, 0.0, 0.5, 1.0))
    m.add_case("L", {"L": 1.0})
    m.add_combo("C", {"D": 1.2, "L": 1.6})
    m.validate()
    eng = OpenSeesEngine(m)
    rd, rl = eng.run_static("D"), eng.run_static("L")
    assert rd.member_stations["M"]["x"] == rl.member_stations["M"]["x"]
    out = eng.run()
    c = out.combos["C"].member_stations["M"]
    assert c["x"] == rd.member_stations["M"]["x"]
    for i in range(len(c["x"])):
        assert c["M3"][i] == pytest.approx(
            1.2 * rd.member_stations["M"]["M3"][i]
            + 1.6 * rl.member_stations["M"]["M3"][i], rel=1e-9, abs=1e-9)
    # a cut at x = 1.7 (the point-load station) reads the station exactly
    cut = SectionCut("X17", "x", 1.7)
    res = compute_section_cut(m, cut, rd)
    xs = rd.member_stations["M"]["x"]
    k = min(range(len(xs)), key=lambda i: abs(xs[i] - 1.7))
    assert abs(res["MY"]) == pytest.approx(abs(rd.member_stations["M"]
                                               ["M3"][k]), rel=1e-9)


def test_virtual_work_with_variable_stations():
    m = _base("vw")
    m.rigid_diaphragms = True
    m.add_member("column", "B", (0, 0, 0), (0, 0, H), story="Story1",
                 uid="C1")
    m.add_member("column", "B", (6, 0, 0), (6, 0, H), story="Story1",
                 uid="C2")
    m.add_member("beam", "B", (0, 0, H), (6, 0, H), story="Story1",
                 uid="B1")
    for mem in m.members:
        mem.output_stations = {"max_spacing": 0.7}
    pat = m.pattern("W", "wind")
    pat.story_forces.append(StoryForce("Story1", fx=10.0))
    m.add_case("W", {"W": 1.0})
    m.validate()
    vw = OpenSeesEngine(m).run_virtual_work("W", "X")
    assert vw["total"] == pytest.approx(vw["roof_disp"], rel=1e-9)


# --------------------------------------------------------------------------- #
# 5. offsets / insertion / shells / axial-only
# --------------------------------------------------------------------------- #
def test_rigid_offsets_kept_on_auto_split_member():
    """Rigid end offsets stay on the END segments of an auto-meshed member
    (no warning, no error).  End forces equal the unsplit member's; the
    midspan moment is the exact statics value of the offset model (UDL on
    the clear span only -- the v0.9 convention): M = R_i*3 - w*(3-0.3)^2/2.
    """
    def mk(auto):
        m = _base("ofs")
        m.add_member("beam", "B", (0, 0, H), (6, 0, H), story="Story1",
                     uid="M")
        m.supports.append(PointSupport((0, 0, H), (1, 1, 1, 1, 0, 0)))
        m.supports.append(PointSupport((6, 0, H), (0, 1, 1, 0, 0, 0)))
        m.pattern("D", "dead").member_udls.append(MemberUDL("M", 5.0))
        m.add_case("D", {"D": 1.0})
        m.members[0].rigid_i = 0.3
        m.members[0].rigid_j = 0.4
        m.members[0].auto_mesh = auto
        m.validate()
        return m
    r0 = OpenSeesEngine(mk(None)).run_static("D")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        eng = OpenSeesEngine(mk({"max_length": 1.0}))
        r1 = eng.run_static("D")
    assert len(eng._mesh.segments["M"]) == 6
    for a, b in zip(r0.member_forces["M"], r1.member_forces["M"]):
        assert b == pytest.approx(a, rel=1e-6, abs=1e-6)
    Ri = 5.0 * 5.3 * (0.4 + 5.3 / 2) / 6.0       # clear-span UDL statics
    assert r1.member_forces["M"][1] == pytest.approx(Ri, rel=1e-6)
    assert r1.member_stations["M"]["M3"][5] == pytest.approx(
        Ri * 3.0 - 5.0 * 2.7 ** 2 / 2.0, rel=1e-6)


def test_offset_inside_cut_segment_still_rejected():
    m = _ss_beam(6.0, w=5.0, P=0.0)
    m.members[0].rigid_i = 1.5
    m.members[0].auto_mesh = {"max_length": 1.0}
    m.validate()
    with pytest.raises(ValueError, match="rigid end offsets"):
        OpenSeesEngine(m).run_static("D")


def test_axial_only_member_never_divided():
    m = _grillage(True)
    m.members[1].axial_limit = "tension"
    m.validate()
    with pytest.warns(UserWarning, match="axial-only"):
        mesh = mesh_model(m)
    assert len(mesh.segments["BB"]) == 1 and len(mesh.segments["A"]) == 1
    codes = [i["code"] for i in check_model(m)["issues"]]
    assert "FRAME_INTERSECTION" in codes


def test_polygon_shell_conforms_to_auto_mesh_points():
    m = _base("poly")
    m.add_shell_section(ShellSection("SL", "S", 0.2))
    corners = [(0, 0, H), (6, 0, H), (7, 3, H), (3, 5, H), (-1, 3, H)]
    m.shells.append(ShellRegion("P1", "slab", "shell", "SL", corners,
                                mesh_size=10.0, story="Story1"))
    m.add_member("beam", "B", (0, 0, H), (6, 0, H), story="Story1",
                 uid="E")
    m.members[0].auto_mesh = {"max_length": 0.75}
    m.validate()
    mesh = mesh_model(m)
    shell_nodes = {n for q in mesh.quads for n in q.nodes}
    segs = mesh.segments["E"]
    assert len(segs) >= 8
    for s in segs:
        assert s.ni in shell_nodes and s.nj in shell_nodes


# --------------------------------------------------------------------------- #
# 6. defaults, JSON round trip, validation
# --------------------------------------------------------------------------- #
def test_defaults_byte_identical():
    m = _grillage(False)
    d = m.to_dict()
    assert "frame_auto_mesh" not in d
    assert all("auto_mesh" not in md and "output_stations" not in md
               for md in d["members"])
    assert mesh_model(m).auto_split == set()
    r0 = OpenSeesEngine(m).run_static("P")
    m2 = _grillage(False)
    m2.frame_auto_mesh = {"at_intersections": False,
                          "at_intermediate_joints": False,
                          "min_segments": 1}             # all effectively off
    m2.validate()
    r1 = OpenSeesEngine(m2).run_static("P")
    assert json.dumps(r0.to_dict(), sort_keys=True) == \
        json.dumps(r1.to_dict(), sort_keys=True)


def test_round_trip():
    m = _grillage(True)
    m.frame_auto_mesh = {"max_length": 2.0}
    m.members[1].output_stations = {"min_number": 5}
    m.validate()
    d = json.loads(json.dumps(m.to_dict()))
    assert d["frame_auto_mesh"]["max_length"] == 2.0
    back = BuildingModel.from_dict(d)
    assert back.to_dict() == m.to_dict()
    assert back.members[0].auto_mesh == fm.normalize_auto_mesh(
        {"at_intersections": True})
    assert back.members[1].output_stations == {"min_number": 5}


@pytest.mark.parametrize("bad", [
    {"max_length": 0.0}, {"max_length": -1}, {"min_segments": 0},
    {"min_segments": 1.5}, {"foo": True}, "yes"])
def test_auto_mesh_validation(bad):
    m = _grillage(False)
    m.members[0].auto_mesh = bad
    with pytest.raises(ValueError):
        m.validate()


@pytest.mark.parametrize("bad", [
    {"max_spacing": 0}, {"min_number": 1}, {"min_number": 2.5},
    {"max_spacing": 1.0, "min_number": 3}, {"n": 3}])
def test_output_stations_validation(bad):
    m = _grillage(False)
    m.members[0].output_stations = bad
    with pytest.raises(ValueError):
        m.validate()
    d = _grillage(False).to_dict()
    d["members"][0]["output_stations"] = bad
    with pytest.raises(ValueError):
        BuildingModel.from_dict(d)


def test_integrate_stations_nonuniform_exact_for_quadratics():
    xs = [0.0, 0.3, 1.0, 1.4, 2.5]
    f = [3 * x * x - x + 2 for x in xs]
    exact = 2.5 ** 3 - 2.5 ** 2 / 2 + 5.0
    assert fm.integrate_stations(xs, f) == pytest.approx(exact, rel=1e-12)
    xs = xs + [3.1]                                   # odd interval count
    f = [3 * x * x - x + 2 for x in xs]
    exact = 3.1 ** 3 - 3.1 ** 2 / 2 + 6.2
    assert fm.integrate_stations(xs, f) == pytest.approx(exact, rel=1e-12)
