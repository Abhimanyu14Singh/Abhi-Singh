"""Curved frames (ETABS Draw Curved Beams), wall / floor auto-mesh options,
the joint-pattern library and shell uniform load sets.

Closed forms (bending only: A = 1e4 x I / R^2 makes axial strain
negligible; Euler-Bernoulli chords):

* two-hinged semicircular arch, uniform load w per horizontal length:
  H = 4 w R / (3 pi);
* fixed semicircular arch, same load (elastic-centre / compatibility):
  H = (w R / 6) / (pi/2 - 4/pi), end moment M = 2 R H / pi - w R^2 / 4;
* thin ring under diametral load P: shortening of the loaded diameter
  P R^3 / EI (pi/4 - 2/pi), growth of the transverse one
  P R^3 / EI (2/pi - 1/2);
* quarter-circle plan cantilever, tip load P normal to its plane:
  delta = P R^3 / EI * pi/4 + P R^3 / GJ * (3 pi/4 - 2), support moments
  |M| = |T| = P R.
"""

from __future__ import annotations

import copy
import json
import math

import pytest

from skyframe.core import curved as cv
from skyframe.core import shellopts as so
from skyframe.core.mesh import mesh_model
from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection,
                                 GridSystem, Material, MemberLoad, NodalLoad,
                                 PointSupport, ShellRegion, ShellSection)
from skyframe.engine.opensees_engine import OpenSeesEngine

E = 2.0e8
NU = 0.3
G = E / (2 * (1 + NU))
I_ = 1.0e-3
J_ = 2.0e-3
A_ = 10.0
R = 10.0


def _base(name="c8"):
    m = BuildingModel(name)
    m.add_material(Material("S", E, NU, 0.0, material_type="steel"))
    m.add_section(FrameSection("B", "S", A_, I_, I_, J_))
    m.set_stories([3.0])
    m.rigid_diaphragms = False
    return m


def _arch(seg=None, fixed=False, releases="", w=5.0):
    m = _base("arch")
    mm = m.add_member("beam", "B", (-R, 0, 0), (R, 0, 0), uid="ARC",
                      releases=releases)
    mm.curve = {"type": "arc", "via": [0, 0, R], "segments": seg}
    r = (1, 1, 1, 1, 1, 1) if fixed else (1, 1, 1, 1, 0, 1)
    m.supports.append(PointSupport((-R, 0, 0), r))
    m.supports.append(PointSupport((R, 0, 0), r))
    p = m.pattern("W", "dead")
    p.member_loads.append(MemberLoad("ARC", "udl", w, 0, 0, 1, "gravity",
                                     projected=True))
    m.add_case("W", {"W": 1.0})
    m.validate()
    return m


def _reaction(eng, cr, p):
    return cr.reactions[eng._find_node(eng._asm, p)]


def _H_two_hinged(w=5.0):
    return 4 * w * R / (3 * math.pi)


# --------------------------------------------------------------------------- #
# geometry / validation
# --------------------------------------------------------------------------- #
def test_three_point_arc_geometry_and_auto_segments():
    g = cv.arc_geometry((-R, 0, 0), (R, 0, 0), {"via": [0, 0, R]})
    assert g["radius"] == pytest.approx(R, rel=1e-12)
    assert g["sweep"] == pytest.approx(math.pi, rel=1e-12)
    assert g["center"] == pytest.approx((0, 0, 0), abs=1e-12)
    assert cv.n_segments({"segments": None}, g["sweep"]) == 18   # 10 deg
    assert cv.n_segments({"segments": None}, math.radians(25)) == 3
    assert cv.n_segments({"segments": 7}, g["sweep"]) == 7
    m = _arch()
    assert cv.arc_length(m.members[0]) == pytest.approx(math.pi * R)
    pts = cv.arc_points(m.members[0])
    assert len(pts) == 19
    for p in pts:
        assert math.hypot(p[0], p[2]) == pytest.approx(R, rel=1e-12)
    assert pts[9] == pytest.approx((0, 0, R), abs=1e-12)     # through via


def test_center_form_direction_follows_plane_normal():
    up = cv.arc_geometry((R, 0, 0), (0, R, 0),
                         {"center": [0, 0, 0], "plane_normal": [0, 0, 1]})
    dn = cv.arc_geometry((R, 0, 0), (0, R, 0),
                         {"center": [0, 0, 0], "plane_normal": [0, 0, -1]})
    assert up["sweep"] == pytest.approx(math.pi / 2)
    assert dn["sweep"] == pytest.approx(3 * math.pi / 2)


@pytest.mark.parametrize("curve,match", [
    ({"via": [0, 0, 0]}, "collinear"),
    ({"via": [0, 0, R], "center": [0, 0, 0]}, "exactly one"),
    ({"center": [0, 0, 0]}, "plane_normal"),
    ({"center": [1, 0, 0], "plane_normal": [0, 1, 0]}, "equidistant"),
    ({"center": [0, 0, 0], "plane_normal": [1, 0, 0]}, "plane"),
    ({"via": [0, 0, R], "segments": 0}, "segments"),
    ({"via": [0, 0, R], "local2": "up"}, "local2"),
    ({"type": "spline", "via": [0, 0, R]}, "type"),
    ({"via": [0, 0, R], "radius": 3}, "unknown"),
])
def test_curve_validation_errors(curve, match):
    m = _arch()
    m.members[0].curve = curve
    with pytest.raises(ValueError, match=match):
        m.validate()


def test_curved_member_feature_restrictions():
    m = _arch()
    m.members[0].axial_limit = "tension"
    with pytest.raises(ValueError, match="axial_limit"):
        m.validate()
    m = _arch()
    m.members[0].hinges = "auto_m3"
    with pytest.raises(ValueError, match="hinges"):
        m.validate()
    m = _arch(seg=4)
    m.add_member("beam", "B", (0, 5, 0), (1, 5, 0), uid="ARC~2")
    m.supports.append(PointSupport((0, 5, 0), (1,) * 6))
    with pytest.raises(ValueError, match="collides"):
        m.validate()


def test_chord_local_axes_conventions():
    # vertical-plane arch: local 2 stays in the plane, "up" = straight
    # beam convention -> no extra rotation
    m = _arch(seg=6)
    exp = so.expand_for_analysis(m)
    assert [c.angle for c in exp.members] == [0.0] * 6
    # plan-curved member: in_plane -> local 2 horizontal toward center
    m2 = _base()
    mm = m2.add_member("beam", "B", (R, 0, 3), (0, R, 3), uid="C")
    mm.curve = {"type": "arc", "center": [0, 0, 3],
                "plane_normal": [0, 0, 1], "segments": 4}
    m2.supports.append(PointSupport((R, 0, 3), (1,) * 6))
    m2.validate()
    exp2 = so.expand_for_analysis(m2)
    for ch in exp2.members:
        x = cv._unit(cv._sub(ch.pj, ch.pi))
        _, y0, z0 = cv._default_axes(ch.pi, ch.pj)
        a = math.radians(ch.angle)
        y = tuple(math.cos(a) * y0[k] + math.sin(a) * z0[k] for k in range(3))
        mid = tuple(0.5 * (ch.pi[k] + ch.pj[k]) for k in range(3))
        to_c = cv._unit((-mid[0], -mid[1], 0.0))
        assert cv._dot(y, to_c) == pytest.approx(1.0, abs=1e-12)
        assert abs(cv._dot(y, x)) < 1e-12
    mm.curve["local2"] = "normal"
    m2.validate()
    assert [c.angle for c in so.expand_for_analysis(m2).members] == [0.0] * 4


# --------------------------------------------------------------------------- #
# closed-form validations
# --------------------------------------------------------------------------- #
def test_two_hinged_arch_thrust_converges_to_closed_form():
    Hc = _H_two_hinged()
    errs = []
    for seg in (6, 18, 72):
        m = _arch(seg)
        eng = OpenSeesEngine(m)
        cr = eng.run_static("W")
        rl = _reaction(eng, cr, (-R, 0, 0))
        rr = _reaction(eng, cr, (R, 0, 0))
        assert rl[2] + rr[2] == pytest.approx(5.0 * 2 * R, rel=1e-7)
        assert rl[0] == pytest.approx(-rr[0], rel=1e-7)
        errs.append(abs(rl[0] - Hc) / Hc)
    assert errs[0] > errs[1] > errs[2]
    assert errs[1] < 6e-3          # 10-degree chords (the auto default)
    assert errs[2] < 5e-4


def test_fixed_arch_thrust_and_end_moment():
    w = 5.0
    Hc = (w * R / 6) / (math.pi / 2 - 4 / math.pi)
    Mc = 2 * R * Hc / math.pi - w * R * R / 4
    m = _arch(72, fixed=True)
    eng = OpenSeesEngine(m)
    cr = eng.run_static("W")
    rl = _reaction(eng, cr, (-R, 0, 0))
    assert rl[0] == pytest.approx(Hc, rel=1e-3)
    assert abs(rl[4]) == pytest.approx(Mc, rel=5e-3)


def test_end_releases_reproduce_two_hinged_arch():
    """Fixed supports + Mi,Mj releases on the drawn member = pinned arch
    (the releases land on the first / last chord only)."""
    m1 = _arch(18)
    m2 = _arch(18, fixed=True, releases="Mi,Mj")
    exp = so.expand_for_analysis(m2)
    assert [c.releases for c in exp.members] == (["Mi"] + [""] * 16
                                                  + ["Mj"])
    e1, e2 = OpenSeesEngine(m1), OpenSeesEngine(m2)
    h1 = _reaction(e1, e1.run_static("W"), (-R, 0, 0))[0]
    h2 = _reaction(e2, e2.run_static("W"), (-R, 0, 0))[0]
    assert h2 == pytest.approx(h1, rel=1e-6)


def _ring(seg, P=10.0, Zc=20.0):
    m = _base("ring")
    pts = [(R, 0, Zc), (0, 0, Zc + R), (-R, 0, Zc), (0, 0, Zc - R)]
    for k in range(4):
        t = math.pi / 4 + k * math.pi / 2
        mm = m.add_member("beam", "B", pts[k], pts[(k + 1) % 4],
                          uid=f"Q{k}")
        mm.curve = {"type": "arc", "segments": seg,
                    "via": [R * math.cos(t), 0, Zc + R * math.sin(t)]}
    m.supports.append(PointSupport((0, 0, Zc - R), (1,) * 6))
    m.pattern("P", "live").nodal_loads.append(
        NodalLoad((0, 0, Zc + R), fz=-P))
    m.add_case("P", {"P": 1.0})
    m.validate()
    eng = OpenSeesEngine(m)
    cr = eng.run_static("P")
    top = cr.node_disp[eng._find_node(eng._asm, (0, 0, Zc + R))][2]
    side = cr.node_disp[eng._find_node(eng._asm, (R, 0, Zc))][0]
    side2 = cr.node_disp[eng._find_node(eng._asm, (-R, 0, Zc))][0]
    return -top, side - side2


def test_ring_under_diametral_load():
    P = 10.0
    ref_v = P * R ** 3 / (E * I_) * (math.pi / 4 - 2 / math.pi)
    ref_h = P * R ** 3 / (E * I_) * (2 / math.pi - 0.5)
    errs = []
    for seg in (3, 9, 18):
        dv, dh = _ring(seg, P)
        errs.append(abs(dv - ref_v) / ref_v)
    assert errs[0] > errs[1] > errs[2]
    assert dv == pytest.approx(ref_v, rel=3e-3)
    assert dh == pytest.approx(ref_h, rel=3e-3)


def _plan_cantilever(seg, local2="in_plane", P=10.0, Zp=5.0):
    m = _base("plan")
    mm = m.add_member("beam", "B", (R, 0, Zp), (0, R, Zp), uid="C")
    mm.curve = {"type": "arc", "center": [0, 0, Zp],
                "plane_normal": [0, 0, 1], "segments": seg,
                "local2": local2}
    m.supports.append(PointSupport((R, 0, Zp), (1,) * 6))
    m.pattern("P", "live").nodal_loads.append(NodalLoad((0, R, Zp),
                                                        fz=-P))
    m.add_case("P", {"P": 1.0})
    m.validate()
    eng = OpenSeesEngine(m)
    res = eng.run()
    cr = res.cases["P"]
    tip = cr.node_disp[eng._find_node(eng._asm, (0, R, Zp))][2]
    return eng, res, -tip


def test_plan_curved_cantilever_bending_plus_torsion():
    P = 10.0
    ref = (P * R ** 3 / (E * I_) * math.pi / 4
           + P * R ** 3 / (G * J_) * (3 * math.pi / 4 - 2))
    errs = []
    for seg in (3, 9, 36):
        eng, res, d = _plan_cantilever(seg)
        errs.append(abs(d - ref) / ref)
    assert errs[0] > errs[1] > errs[2]
    assert errs[2] < 1e-3
    cr = res.cases["P"]
    rf = cr.reactions[eng._find_node(eng._asm, (R, 0, 5.0))]
    assert rf[2] == pytest.approx(P, rel=1e-9)
    assert rf[3] == pytest.approx(P * R, rel=1e-9)      # statics
    assert rf[4] == pytest.approx(P * R, rel=1e-9)
    st = res.curved_frames["C"]["cases"]["P"]
    # chord-axis components of the support moment |M| = P R sqrt(2)
    m0 = math.sqrt(st["T"][0] ** 2 + st["M2"][0] ** 2 + st["M3"][0] ** 2)
    assert m0 == pytest.approx(P * R * math.sqrt(2), rel=1e-9)
    assert abs(st["M2"][-1]) < 1e-5 and abs(st["T"][-1]) < 1e-5   # tip
    # out-of-plane bending is about local 2 with "in_plane" axes, about
    # local 3 with "normal" axes -- same deflection (I22 == I33)
    _, res_n, dn = _plan_cantilever(36, "normal")
    assert dn == pytest.approx(d, rel=1e-9)
    stn = res_n.curved_frames["C"]["cases"]["P"]
    assert abs(stn["M2"][0]) < 1e-9
    assert abs(stn["M3"][0]) == pytest.approx(abs(st["M2"][0]), rel=1e-9)


# --------------------------------------------------------------------------- #
# loads along the arc / stitched results
# --------------------------------------------------------------------------- #
def test_member_loads_distributed_along_arc_length():
    m = _arch(9)
    p = m.patterns["W"]
    p.member_loads = [
        MemberLoad("ARC", "udl", 2.0, 0, 0.0, 1.0, "gravity"),
        MemberLoad("ARC", "trapezoid", 1.0, 3.0, 0.2, 0.7, "gravity"),
        MemberLoad("ARC", "point", 7.0, 0, 0.5, 1.0, "gravity"),
        MemberLoad("ARC", "udl", 1.5, 0, 0.0, 0.25, "global_x"),
    ]
    m.validate()
    exp = so.expand_for_analysis(m)
    assert all(ml.member_uid.startswith("ARC~") for ml in
               exp.patterns["W"].member_loads)
    eng = OpenSeesEngine(m)
    cr = eng.run_static("W")
    S = math.pi * R
    total_z = 2.0 * S + 0.5 * (1.0 + 3.0) * 0.5 * S + 7.0
    assert cr.base["FZ"] == pytest.approx(total_z, rel=1e-8)   # reaction
    assert cr.base["FX"] == pytest.approx(-1.5 * 0.25 * S, rel=1e-8)


def test_stitched_results_along_arc_and_json():
    m = _arch(6)
    m.add_combo("C2", {"W": 2.0})
    eng = OpenSeesEngine(m)
    res = eng.run()
    d = json.loads(json.dumps(res.to_dict()))
    cf = d["curved_frames"]["ARC"]
    assert cf["segments"] == 6 and len(cf["chords"]) == 6
    assert cf["arc_length"] == pytest.approx(math.pi * R)
    assert cf["radius"] == pytest.approx(R)
    assert cf["sweep_deg"] == pytest.approx(180.0)
    st = cf["cases"]["W"]
    assert st["s"][0] == 0.0
    assert st["s"][-1] == pytest.approx(math.pi * R, rel=1e-12)
    assert all(b >= a - 1e-12 for a, b in zip(st["s"], st["s"][1:]))
    assert len(st["s"]) == 6 * 11 and set(st["chord"]) == set(range(6))
    assert len(st["nodes"]["disp"]) == 7
    # crown node (s = S/2) is symmetric: no horizontal displacement
    assert abs(st["nodes"]["disp"][3][0]) < 1e-12
    c2 = cf["combos"]["C2"]
    assert c2["M3"] == pytest.approx([2 * v for v in st["M3"]], abs=1e-9)
    parents = {r["uid"]: r.get("curved_parent") for r in d["members"]}
    assert parents["ARC~0"] == "ARC" and len(parents) == 6
    # M3 of chord stations (bending) vs the two-hinged statics at the
    # crown: M = M0 - H y = w R^2 / 2 - H R
    k = st["s"].index(next(s for s in st["s"]
                           if abs(s - math.pi * R / 2) < 1e-9))
    H = _reaction(eng, res.cases["W"], (-R, 0, 0))[0]
    assert abs(st["M3"][k]) == pytest.approx(abs(5.0 * R * R / 2 - H * R),
                                             rel=1e-6)


def test_groups_and_roundtrip_with_curves():
    m = _arch(6)
    m.groups = {"G": {"members": ["ARC"], "shells": [], "links": [],
                      "points": [], "color": ""}}
    m.validate()
    exp = so.expand_for_analysis(m)
    assert exp.groups["G"]["members"] == [f"ARC~{k}" for k in range(6)]
    assert m.groups["G"]["members"] == ["ARC"]           # user model kept
    d = json.loads(json.dumps(m.to_dict()))
    assert d["members"][0]["curve"]["via"] == [0, 0, R]
    m2 = BuildingModel.from_dict(d)
    assert m2.members[0].curve == m.members[0].curve
    assert m2.to_dict() == m.to_dict()


# --------------------------------------------------------------------------- #
# wall / floor mesh options
# --------------------------------------------------------------------------- #
def _shell_base():
    m = BuildingModel("mesh")
    m.add_material(Material("C", 3e7, 0.2, 24.0))
    m.add_section(FrameSection("B", "C", 0.1, 1e-3, 1e-3, 1e-3))
    m.add_shell_section(ShellSection("SL", "C", 0.2))
    m.set_stories([3.0])
    m.rigid_diaphragms = False
    return m


def _elems(m, uid):
    mesh = mesh_model(m)
    return [q for q in mesh.quads if q.region == uid], mesh


def test_wall_mesh_divisions_structured():
    m = _shell_base()
    w = ShellRegion("W", "wall", "shell", "SL",
                    [(0, 0, 0), (6, 0, 0), (6, 0, 3), (0, 0, 3)])
    m.shells.append(w)
    assert len(_elems(m, "W")[0]) == 18                # legacy 6 x 3
    w.mesh_divisions = {"n1": 4, "n2": 5}
    m.validate()
    q, mesh = _elems(m, "W")
    assert len(q) == 20
    xs = sorted({round(mesh.points[n][0], 9) for e in q for n in e.nodes})
    zs = sorted({round(mesh.points[n][2], 9) for e in q for n in e.nodes})
    assert xs == pytest.approx([0, 1.5, 3, 4.5, 6])
    assert zs == pytest.approx([0, 0.6, 1.2, 1.8, 2.4, 3.0])
    w.mesh_divisions = {"max_size": 0.7}
    m.validate()
    assert len(_elems(m, "W")[0]) == 9 * 5             # ceil, not round


def test_mesh_divisions_polygon_mesher():
    m = _shell_base()
    p = ShellRegion("P", "slab", "shell", "SL",
                    [(0, 0, 3), (6, 0, 3), (6, 6, 3), (3, 8, 3), (0, 6, 3)])
    m.shells.append(p)
    n_default = len(_elems(m, "P")[0])
    p.mesh_divisions = {"n1": 3, "n2": 4}
    m.validate()
    q, mesh = _elems(m, "P")
    assert len(q) < n_default
    xs = sorted({round(mesh.points[n][0], 9) for e in q for n in e.nodes})
    # step bbox/3 = 2 m inside the intervals split by the apex line x = 3
    assert xs == pytest.approx([0, 1.5, 3, 4.5, 6])
    area = sum(_area(mesh, e) for e in q)
    assert area == pytest.approx(p.area, rel=1e-12)


def _area(mesh, e):
    from skyframe.core.model import _polygon_area3d
    return _polygon_area3d([mesh.points[n] for n in e.nodes])


def _slab_with_beam(pj=(2.5, 6, 3)):
    m = _shell_base()
    s = ShellRegion("S", "slab", "shell", "SL",
                    [(0, 0, 3), (6, 0, 3), (6, 6, 3), (0, 6, 3)])
    m.shells.append(s)
    m.add_member("beam", "B", (2.5, 0, 3), pj, uid="B1")
    return m, s


def _interior_nodes_on(mesh, q, f):
    return [mesh.points[n] for e in q for n in e.nodes
            if f(mesh.points[n])]


def test_floor_mesh_at_beams_and_modes():
    m, s = _slab_with_beam()
    assert len(_elems(m, "S")[0]) == 36                 # structured 6 x 6
    s.floor_mesh = {}
    m.validate()
    assert s.floor_mesh == {"mode": "default", "max_size": None,
                            "at_beams": True, "at_walls": True,
                            "at_grids": True}
    q, mesh = _elems(m, "S")
    on_beam = _interior_nodes_on(
        mesh, q, lambda p: abs(p[0] - 2.5) < 1e-9 and 0 < p[1] < 6)
    assert on_beam                                      # meshed at beam
    s.floor_mesh = {"at_beams": False}
    m.validate()
    q, mesh = _elems(m, "S")
    assert not _interior_nodes_on(
        mesh, q, lambda p: abs(p[0] - 2.5) < 1e-9 and 0 < p[1] < 6)
    # oblique beam: cookie cut puts nodes on the beam line without a mesh
    # line through its ends; rectangular gives lines but no cuts
    m, s = _slab_with_beam((5.0, 6, 3))

    def on_line(p):
        return (0 < p[1] < 6 and abs(p[0] - (2.5 + 2.5 * p[1] / 6)) < 1e-9
                and abs(p[1] - round(p[1])) > 1e-9)
    s.floor_mesh = {"mode": "cookie_cut"}
    m.validate()
    q, mesh = _elems(m, "S")
    assert _interior_nodes_on(mesh, q, on_line)
    assert not _interior_nodes_on(
        mesh, q, lambda p: abs(p[0] - 2.5) < 1e-9 and 0 < p[1] < 6)
    s.floor_mesh = {"mode": "rectangular"}
    m.validate()
    q, mesh = _elems(m, "S")
    assert len(q) == 7 * 6 and all(len(e.nodes) == 4 for e in q)
    assert not _interior_nodes_on(mesh, q, on_line)
    s.floor_mesh = {"max_size": 2.0, "at_beams": False}
    m.validate()
    q, mesh = _elems(m, "S")
    # 2 m grid; the only extra nodes are the beam ends on the slab edge
    # (joined by the conformity pass)
    extra = {(round(p[0], 9), round(p[1], 9))
             for p in (mesh.points[n] for e in q for n in e.nodes)
             if not (abs(p[0] / 2 - round(p[0] / 2)) < 1e-9
                     and abs(p[1] / 2 - round(p[1] / 2)) < 1e-9)}
    assert extra == {(2.5, 0.0), (5.0, 6.0)}
    assert sum(_area(mesh, e) for e in q) == pytest.approx(36.0, rel=1e-12)


def test_floor_mesh_at_grids():
    m = _shell_base()
    p = ShellRegion("P", "slab", "shell", "SL",
                    [(0, 0, 3), (6, 0, 3), (6, 6, 3), (3, 8, 3), (0, 6, 3)])
    m.shells.append(p)
    m.grid = GridSystem(x_lines=[0, 2.2, 6], y_lines=[0, 6])
    m.grid_systems = [m.grid]

    def has_x(x):
        q, mesh = _elems(m, "P")
        return any(abs(mesh.points[n][0] - x) < 1e-9 for e in q
                   for n in e.nodes)
    assert has_x(2.2)
    p.floor_mesh = {"at_grids": False}
    m.validate()
    assert not has_x(2.2)


def test_floor_mesh_options_analysis_conserves_load():
    m, s = _slab_with_beam((5.0, 6, 3))
    for c in [(0, 0, 3), (6, 0, 3), (6, 6, 3), (0, 6, 3)]:
        m.supports.append(PointSupport(c, (1, 1, 1, 0, 0, 0)))
    m.supports.append(PointSupport((5.0, 6, 3), (1, 1, 1, 0, 0, 0)))
    s.floor_mesh = {"mode": "cookie_cut", "max_size": 1.5}
    m.pattern("Q", "live").area_loads.append(AreaLoad("S", 4.0))
    m.add_case("Q", {"Q": 1.0})
    m.validate()
    cr = OpenSeesEngine(m).run_static("Q")
    assert cr.base["FZ"] == pytest.approx(4.0 * 36.0, rel=1e-9)


def test_mesh_option_validation():
    m, s = _slab_with_beam()
    for bad, match in (({"n1": 0, "n2": 2}, "n1"), ({"n1": 2}, "mesh_div"),
                       ({"max_size": -1}, "max_size")):
        s.mesh_divisions = bad
        with pytest.raises(ValueError, match=match):
            m.validate()
    s.mesh_divisions = None
    for bad, match in (({"mode": "paving"}, "mode"),
                       ({"at_beams": 1}, "at_beams"),
                       ({"foo": 1}, "unknown")):
        s.floor_mesh = bad
        with pytest.raises(ValueError, match=match):
            m.validate()
    s.floor_mesh = None
    w = ShellRegion("W", "wall", "shell", "SL",
                    [(0, 0, 0), (6, 0, 0), (6, 0, 3), (0, 0, 3)],
                    floor_mesh={})
    m.shells.append(w)
    with pytest.raises(ValueError, match="slabs only"):
        m.validate()


# --------------------------------------------------------------------------- #
# joint patterns / shell load sets
# --------------------------------------------------------------------------- #
def _slab_model():
    m = _shell_base()
    pts = [(0, 0, 3), (4, 0, 3), (4, 3, 3), (0, 3, 3)]
    m.shells.append(ShellRegion("S", "slab", "shell", "SL", pts))
    for p in pts:
        m.supports.append(PointSupport(p, (1, 1, 1, 0, 0, 0)))
    m.pattern("D", "dead")
    m.pattern("L", "live")
    m.add_case("D", {"D": 1.0})
    m.add_case("L", {"L": 1.0})
    return m


def _same(r1, r2):
    assert r1.node_disp.keys() == r2.node_disp.keys()
    for t in r1.node_disp:
        assert r1.node_disp[t] == r2.node_disp[t]
    for t in r1.reactions:
        assert r1.reactions[t] == r2.reactions[t]


def test_named_joint_pattern_equals_inline():
    jp = {"type": "linear", "a": 0.5, "b": -0.2, "d": 1.0,
          "zero_negative": True}
    m1 = _slab_model()
    m1.patterns["D"].area_loads.append(AreaLoad("S", 3.0, joint_pattern=jp))
    m2 = _slab_model()
    m2.joint_patterns = {"hydro": dict(jp)}
    m2.patterns["D"].area_loads.append(AreaLoad("S", 3.0,
                                                joint_pattern="hydro"))
    m1.validate()
    m2.validate()
    _same(OpenSeesEngine(m1).run_static("D"),
          OpenSeesEngine(m2).run_static("D"))
    d = json.loads(json.dumps(m2.to_dict()))
    assert d["patterns"]["D"]["area_loads"][0]["joint_pattern"] == "hydro"
    assert d["joint_patterns"]["hydro"]["a"] == 0.5
    assert BuildingModel.from_dict(d).to_dict() == m2.to_dict()


def test_joint_pattern_library_validation():
    m = _slab_model()
    m.patterns["D"].area_loads.append(AreaLoad("S", 3.0, joint_pattern="x"))
    with pytest.raises(ValueError, match="unknown joint pattern"):
        m.validate()
    m.joint_patterns = {"x": {"type": "linear", "a": float("nan")}}
    with pytest.raises(ValueError, match="finite"):
        m.validate()
    m.joint_patterns = {"x": {"type": "linear", "zero_negative": True,
                              "zero_positive": True}}
    with pytest.raises(ValueError, match="both"):
        m.validate()


def test_shell_load_set_expands_into_area_loads():
    m1 = _slab_model()
    m1.patterns["D"].area_loads.append(AreaLoad("S", 1.5))
    m1.patterns["L"].area_loads.append(AreaLoad("S", 2.0))
    m1.patterns["L"].area_loads.append(AreaLoad("S", 0.5,
                                                direction="global_x"))
    m2 = _slab_model()
    m2.shell_load_sets = {"OFFICE": {"D": 1.5, "L": 2.0}}
    m2.shells[0].load_set = "OFFICE"
    m2.patterns["L"].area_loads.append(AreaLoad("S", 0.5,
                                                direction="global_x"))
    m1.validate()
    m2.validate()
    e1, e2 = OpenSeesEngine(m1), OpenSeesEngine(m2)
    assert e2.model is not m2 and e2.source_model is m2
    assert m2.shells[0].load_set == "OFFICE"          # user model untouched
    # set loads are appended AFTER the pattern's own area loads
    _same(e1.run_static("D"), e2.run_static("D"))
    rl1, rl2 = e1.run_static("L"), e2.run_static("L")
    assert rl2.base["FZ"] == pytest.approx(2.0 * 12.0, rel=1e-12)
    assert rl2.base["FX"] == pytest.approx(rl1.base["FX"], rel=1e-12)
    for t in rl1.node_disp:
        assert rl2.node_disp[t] == pytest.approx(rl1.node_disp[t],
                                                 rel=1e-12, abs=1e-18)
    d = json.loads(json.dumps(m2.to_dict()))
    assert d["shells"][0]["load_set"] == "OFFICE"
    assert d["shell_load_sets"] == {"OFFICE": {"D": {"q": 1.5},
                                               "L": {"q": 2.0}}}
    assert BuildingModel.from_dict(d).to_dict() == m2.to_dict()


def test_shell_load_set_validation():
    m = _slab_model()
    m.shells[0].load_set = "NOPE"
    with pytest.raises(ValueError, match="unknown shell load set"):
        m.validate()
    m.shell_load_sets = {"NOPE": {"XX": 1.0}}
    with pytest.raises(ValueError, match="unknown load pattern"):
        m.validate()
    m.shell_load_sets = {"NOPE": {"D": {"q": 1.0, "direction": "sideways"}}}
    with pytest.raises(ValueError, match="direction"):
        m.validate()


# --------------------------------------------------------------------------- #
# defaults are bit-identical
# --------------------------------------------------------------------------- #
def test_defaults_untouched():
    m = _slab_model()
    m.add_member("beam", "B", (0, 0, 3), (4, 0, 3), uid="E1")
    m.validate()
    assert not so.needs_expansion(m)
    assert so.expand_for_analysis(m) is m
    eng = OpenSeesEngine(m)
    assert eng.model is m
    d = m.to_dict()
    for k in ("joint_patterns", "shell_load_sets"):
        assert k not in d
    assert "curve" not in d["members"][0]
    for k in ("mesh_divisions", "floor_mesh", "load_set"):
        assert k not in d["shells"][0]
    res = eng.run().to_dict()
    assert "curved_frames" not in res
    assert all("curved_parent" not in r for r in res["members"])
    # structured mesher with no options = the legacy round() rule
    r = m.shells[0]
    assert so.structured_divisions(r, 4.0, 3.0) == (4, 3)
    assert so.polygon_steps(r, 0, 4, 0, 3) == (1.0, 1.0)
    assert copy.deepcopy(m).to_dict() == d


# --------------------------------------------------------------------------- #
# extra pins
# --------------------------------------------------------------------------- #
def test_center_form_arch_equals_three_point_arch():
    m1 = _arch(12)
    m2 = _arch(12)
    m2.members[0].curve = {"type": "arc", "center": [0, 0, 0],
                           "plane_normal": [0, 1, 0], "segments": 12}
    m2.validate()
    p1 = so.expand_for_analysis(m1).members
    p2 = so.expand_for_analysis(m2).members
    for a, b in zip(p1, p2):
        assert a.pi == pytest.approx(b.pi, abs=1e-12)
        assert a.angle == b.angle
    e1, e2 = OpenSeesEngine(m1), OpenSeesEngine(m2)
    h1 = _reaction(e1, e1.run_static("W"), (-R, 0, 0))[0]
    h2 = _reaction(e2, e2.run_static("W"), (-R, 0, 0))[0]
    assert h2 == pytest.approx(h1, rel=1e-9)


def test_chord_load_pieces_conserve_arc_totals():
    n, S, c = 7, 3.0, 0.4                 # arc length, chord length
    ratio = (S / n) / c
    chords = [f"X~{k}" for k in range(n)]
    ml = MemberLoad("X", "trapezoid", 2.0, 6.0, 0.13, 0.81, "gravity")
    pieces = cv._chord_loads(ml, n, ratio, chords)
    tot = sum(0.5 * (p.w + p.w2) * (p.b - p.a) * c for p in pieces)
    assert tot == pytest.approx(0.5 * (2.0 + 6.0) * (0.81 - 0.13) * S,
                                rel=1e-12)
    for p, q in zip(pieces, pieces[1:]):        # continuous intensity
        assert p.w2 == pytest.approx(q.w, rel=1e-12)
    pt = cv._chord_loads(MemberLoad("X", "point", 3.0, 0, 0.5, 1.0), n,
                         ratio, chords)
    assert len(pt) == 1 and pt[0].member_uid == "X~3"
    assert pt[0].a == pytest.approx(0.5)
    proj = cv._chord_loads(MemberLoad("X", "udl", 4.0, 0, 0, 1, "gravity",
                                      projected=True), n, ratio, chords)
    assert len(proj) == n and all(p.w == 4.0 and p.projected for p in proj)


def test_wall_mesh_divisions_analysis_and_check_model():
    from skyframe.core.checks import check_model
    m = _shell_base()
    w = ShellRegion("W", "wall", "shell", "SL",
                    [(0, 0, 0), (6, 0, 0), (6, 0, 3), (0, 0, 3)],
                    mesh_divisions={"n1": 6, "n2": 3})
    m.shells.append(w)
    m.pattern("H", "wind").nodal_loads.append(NodalLoad((6, 0, 3), fx=50.0))
    m.add_case("H", {"H": 1.0})
    m.validate()
    assert len(_elems(m, "W")[0]) == 18
    cr = OpenSeesEngine(m).run_static("H")
    assert cr.base["FX"] == pytest.approx(-50.0, rel=1e-9)
    assert not [i for i in check_model(m)["issues"]
                if i["code"] == "SHELL_ASPECT_RATIO"]
    w.mesh_divisions = {"n1": 1, "n2": 6}       # 6 x 0.5 m elements
    msgs = [i["message"] for i in check_model(m)["issues"]
            if i["code"] == "SHELL_ASPECT_RATIO"]
    assert msgs and "6 x 0.5" in msgs[0]


def test_load_set_on_membrane_slab_goes_to_beams():
    m = _shell_base()
    pts = [(0, 0, 3), (4, 0, 3), (4, 3, 3), (0, 3, 3)]
    for a, b in zip(pts, pts[1:] + pts[:1]):
        m.add_member("beam", "B", a, b)
    for p in pts:
        m.supports.append(PointSupport(p, (1,) * 6))
    m.shells.append(ShellRegion("M", "slab", "membrane", "", pts,
                                load_set="RES"))
    m.pattern("D", "dead")
    m.shell_load_sets = {"RES": {"D": 2.5}}
    m.add_case("D", {"D": 1.0})
    m.validate()
    cr = OpenSeesEngine(m).run_static("D")
    assert cr.base["FZ"] == pytest.approx(2.5 * 12.0, rel=1e-9)
    m.shell_load_sets = {"RES": {"D": {"q": 2.5, "direction": "global_x"}}}
    with pytest.raises(ValueError, match="membrane"):
        m.validate()
