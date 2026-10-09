"""Insertion point, joint offsets and automatic end offsets (gaps B4 + B5).

ETABS Assign > Frame > Insertion Point (cardinal point 1..11, joint
offsets, "do not transform frame stiffness") and End Length Offsets >
Automatic from Connectivity (rigid-zone factor).  Hand checks:

* simply supported beam, cardinal point 8 (top center at the joints):
  pin-roller -> no axial force, deflection = 5wL^4/384EI; pin-pin ->
  eccentric thrust N = 2 E A e theta / L with
  theta = theta0 / (1 + A e^2 / I) and midspan deflection
  5wL^4/384EI - N e L^2 / 8EI (exact);
* two members rigidly linked through cardinal points 2 / 8 in pure
  bending act as one composite section,
  Iz_eff = I1 + I2 + A1 d1^2 + A2 d2^2 (exact for constant curvature);
* a cantilever whose element is offset by a from its joints carries a
  tip load P as torsion T = P a: tip uz = PL^3/3EI + P a^2 L / GJ,
  rx = P a L / GJ;
* auto end offsets on a portal == manual offsets of the same lengths;
  factor 0 == centerline; factor 0 / 1 bracket a model whose joint zones
  are stiff but finite.
"""

import json
import math

import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core import insertion as ins
from skyframe.core.builder import quick_building
from skyframe.core.model import (
    BuildingModel,
    FrameSection,
    Material,
    MemberLoad,
    NodalLoad,
    PointSupport,
)

E = 25_000_000.0      # kPa
NU = 0.2
G = E / (2.0 * (1.0 + NU))
B, H = 0.3, 0.6


def _mdl(name="ins") -> BuildingModel:
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("C", E=E, nu=NU))
    mdl.set_stories([4.0])
    mdl.num_modes = 0
    mdl.add_section(FrameSection.rectangular("S", "C", B, H))
    return mdl


def _run(mdl) -> dict:
    return OpenSeesEngine(mdl).run().to_dict()


def _node(d, pt, tol=1e-6):
    for tag, xyz in d["nodes"].items():
        if all(abs(a - b) < tol for a, b in zip(xyz, pt)):
            return tag
    raise AssertionError(f"no node at {pt}")


def _udl(mdl, uid, w, pat="W"):
    mdl.pattern(pat, "other").member_loads.append(
        MemberLoad(uid, "udl", w, direction="gravity"))


def _ss_beam(cp, pin_pin, w=20.0, L=6.0):
    """Two-member simply supported beam (midspan node), UDL w down."""
    mdl = _mdl("ss")
    mdl.add_member("beam", "S", (0, 0, 0), (L / 2, 0, 0), uid="B1",
                   cardinal_point=cp)
    mdl.add_member("beam", "S", (L / 2, 0, 0), (L, 0, 0), uid="B2",
                   cardinal_point=cp)
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 0, 0)))
    mdl.supports.append(PointSupport((L, 0, 0),
                                     (1 if pin_pin else 0, 1, 1, 1, 0, 0)))
    for uid in ("B1", "B2"):
        _udl(mdl, uid, w)
    mdl.add_case("W", {"W": 1.0})
    d = _run(mdl)
    mid = _node(d, (L / 2, 0, 0))
    return d, d["cases"]["W"]["node_disp"][mid][2]


# --------------------------------------------------------------------------- #
# pure geometry
# --------------------------------------------------------------------------- #
def test_cardinal_point_table():
    b, h = 0.4, 1.0
    exp = {1: (-0.5, -0.2), 2: (-0.5, 0.0), 3: (-0.5, 0.2),
           4: (0.0, -0.2), 5: (0.0, 0.0), 6: (0.0, 0.2),
           7: (0.5, -0.2), 8: (0.5, 0.0), 9: (0.5, 0.2),
           10: (0.0, 0.0), 11: (0.0, 0.0)}
    for cp, yz in exp.items():
        assert ins.cardinal_point_local(cp, b, h) == pytest.approx(yz)
    with pytest.raises(ValueError):
        ins.cardinal_point_local(12, b, h)


def test_joint_offset_vectors_global_local_and_flag():
    mdl = _mdl()
    m = mdl.add_member("beam", "S", (0, 0, 0), (5, 0, 0), uid="B")
    assert ins.member_joint_offsets(mdl, m) is None        # default
    m.cardinal_point = 8          # top center at the joint -> axis below
    ei, ej = ins.member_joint_offsets(mdl, m)
    assert ei == pytest.approx((0, 0, -H / 2)) and ej == pytest.approx(ei)
    m.cardinal_point = 6          # middle right (+local 3 = global -Y for
    ei, _ = ins.member_joint_offsets(mdl, m)   # an X beam): axis at +Y
    assert ei == pytest.approx((0, B / 2, 0))
    m.cardinal_point = 10
    m.joint_offsets = {"i": [0, 0.1, 0.2], "j": [0.3, 0, 0],
                       "system": "local"}
    ei, ej = ins.member_joint_offsets(mdl, m)
    # local y = +Z, local z = -Y for a beam along +X
    assert ei == pytest.approx((0, -0.2, 0.1))
    assert ej == pytest.approx((0.3, 0, 0))
    m.cardinal_point = 8
    m.joint_offsets = None
    m.no_transform_stiffness = True
    assert ins.member_joint_offsets(mdl, m) is None


# --------------------------------------------------------------------------- #
# simply supported beam with cardinal point 8
# --------------------------------------------------------------------------- #
def test_cp8_pin_roller_flexure_unchanged_no_axial():
    L, w = 6.0, 20.0
    EI = E * B * H ** 3 / 12.0
    d, uz = _ss_beam(8, pin_pin=False, w=w, L=L)
    assert uz == pytest.approx(-5 * w * L ** 4 / (384 * EI), rel=1e-6)
    d10, uz10 = _ss_beam(10, pin_pin=False, w=w, L=L)
    assert uz == pytest.approx(uz10, rel=1e-9)
    for uid in ("B1", "B2"):
        assert abs(d["cases"]["W"]["member_forces"][uid][0]) < 1e-6


def test_cp10_pin_pin_has_no_axial():
    d, _ = _ss_beam(10, pin_pin=True)
    assert abs(d["cases"]["W"]["member_forces"]["B1"][0]) < 1e-6


@pytest.mark.parametrize("cp,sign", [(8, 1.0), (2, -1.0)])
def test_eccentric_pin_pin_thrust_hand(cp, sign):
    """Pins at the reference line, centroid offset e: thrust + reduced
    deflection exactly per the compatibility hand solution."""
    L, w = 6.0, 20.0
    A, I = B * H, B * H ** 3 / 12.0
    e = H / 2
    th0 = w * L ** 3 / (24 * E * I)
    th = th0 / (1.0 + A * e * e / I)
    N = 2 * E * A * e * th / L                 # tension for cp 8
    d, uz = _ss_beam(cp, pin_pin=True, w=w, L=L)
    exp_uz = -(5 * w * L ** 4 / (384 * E * I) - N * e * L ** 2 / (8 * E * I))
    assert uz == pytest.approx(exp_uz, rel=1e-6)
    # engine member_forces[0] is +compression at end i
    n_end_i = d["cases"]["W"]["member_forces"]["B1"][0]
    assert -n_end_i == pytest.approx(sign * N, rel=1e-6)
    # stations report the frame's own (eccentric) axis: N constant
    st = d["cases"]["W"]["member_stations"]["B1"]["N"]
    assert all(v == pytest.approx(sign * N, rel=1e-6) for v in st)


def test_composite_pure_bending_parallel_axis():
    """Slab (cp 2, sits on the line) + beam (cp 8, hangs below) linked at
    both joints: tip rotation = M L / (E Iz_eff), parallel-axis theorem."""
    L, M = 5.0, 30.0
    bs, hs = 1.5, 0.15                     # slab strip
    mdl = _mdl("comp")
    mdl.add_section(FrameSection.rectangular("SL", "C", bs, hs))
    mdl.add_member("beam", "SL", (0, 0, 0), (L, 0, 0), uid="S1",
                   cardinal_point=2)
    mdl.add_member("beam", "S", (0, 0, 0), (L, 0, 0), uid="G1",
                   cardinal_point=8)
    mdl.supports.append(PointSupport((0, 0, 0), (1,) * 6))
    mdl.pattern("M", "other").nodal_loads.append(
        NodalLoad((L, 0, 0), my=-M))
    mdl.add_case("M", {"M": 1.0})
    d = _run(mdl)
    A1, I1, y1 = bs * hs, bs * hs ** 3 / 12, hs / 2
    A2, I2, y2 = B * H, B * H ** 3 / 12, -H / 2
    yb = (A1 * y1 + A2 * y2) / (A1 + A2)
    Ieff = I1 + I2 + A1 * (y1 - yb) ** 2 + A2 * (y2 - yb) ** 2
    ry = d["cases"]["M"]["node_disp"][_node(d, (L, 0, 0))][4]
    assert abs(ry) == pytest.approx(M * L / (E * Ieff), rel=1e-6)
    assert abs(ry) < M * L / (E * (I1 + I2)) / 3      # >> stiffer
    n1 = d["cases"]["M"]["member_forces"]["S1"][0]
    n2 = d["cases"]["M"]["member_forces"]["G1"][0]
    assert n1 == pytest.approx(-n2, rel=1e-6) and abs(n1) > 1.0
    # internal couple N*(y1 - y2) + M1 + M2 = M
    m1 = abs(d["cases"]["M"]["member_forces"]["S1"][5])
    m2 = abs(d["cases"]["M"]["member_forces"]["G1"][5])
    assert abs(n1) * (y1 - y2) + m1 + m2 == pytest.approx(M, rel=1e-6)


def test_composite_ss_beam_converges_to_parallel_axis():
    """Slab strip + cp 8 girder split in 12 linked bays, UDL: midspan
    deflection approaches 5wL^4/(384 E Iz_eff) (discrete shear connection,
    within 3 %) and is far below the non-composite value."""
    L, w, n = 8.0, 30.0, 12
    bs, hs = 1.5, 0.15
    mdl = _mdl("compss")
    mdl.add_section(FrameSection.rectangular("SL", "C", bs, hs))
    for k in range(n):
        x0, x1 = L * k / n, L * (k + 1) / n
        mdl.add_member("beam", "SL", (x0, 0, 0), (x1, 0, 0), uid=f"S{k}",
                       cardinal_point=2)
        mdl.add_member("beam", "S", (x0, 0, 0), (x1, 0, 0), uid=f"G{k}",
                       cardinal_point=8)
        _udl(mdl, f"G{k}", w)
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 0, 0)))
    mdl.supports.append(PointSupport((L, 0, 0), (0, 1, 1, 1, 0, 0)))
    mdl.add_case("W", {"W": 1.0})
    d = _run(mdl)
    uz = d["cases"]["W"]["node_disp"][_node(d, (L / 2, 0, 0))][2]
    A1, I1, y1 = bs * hs, bs * hs ** 3 / 12, hs / 2
    A2, I2, y2 = B * H, B * H ** 3 / 12, -H / 2
    yb = (A1 * y1 + A2 * y2) / (A1 + A2)
    Ieff = I1 + I2 + A1 * (y1 - yb) ** 2 + A2 * (y2 - yb) ** 2
    exact = -5 * w * L ** 4 / (384 * E * Ieff)
    nonc = -5 * w * L ** 4 / (384 * E * (I1 + I2))
    assert uz == pytest.approx(exact, rel=0.03)
    assert abs(uz) < 0.5 * abs(nonc)


# --------------------------------------------------------------------------- #
# joint offsets
# --------------------------------------------------------------------------- #
def _offset_cantilever(jo, P=10.0, L=4.0, **kw):
    mdl = _mdl("jo")
    mdl.add_member("beam", "S", (0, 0, 0), (L, 0, 0), uid="B",
                   joint_offsets=jo, **kw)
    mdl.supports.append(PointSupport((0, 0, 0), (1,) * 6))
    mdl.pattern("P", "other").nodal_loads.append(
        NodalLoad((L, 0, 0), fz=-P))
    mdl.add_case("P", {"P": 1.0})
    d = _run(mdl)
    return d, d["cases"]["P"]["node_disp"][_node(d, (L, 0, 0))]


def test_joint_offset_cantilever_torsion_hand():
    P, L, a = 10.0, 4.0, 0.5
    sec = FrameSection.rectangular("x", "C", B, H)
    d, u = _offset_cantilever({"i": [0, a, 0], "j": [0, a, 0]}, P, L)
    EI = E * sec.I33
    GJ = G * sec.J
    assert u[2] == pytest.approx(-(P * L ** 3 / (3 * EI)
                                   + P * a * a * L / GJ), rel=1e-6)
    assert abs(u[3]) == pytest.approx(P * a * L / GJ, rel=1e-6)
    T = d["cases"]["P"]["member_forces"]["B"][3]
    assert abs(T) == pytest.approx(P * a, rel=1e-6)


def test_joint_offset_local_equals_global():
    a = 0.4
    _, ug = _offset_cantilever({"i": [0, 0, a], "j": [0, 0, a]})
    # local y of a +X beam = global +Z
    _, ul = _offset_cantilever({"i": [0, a, 0], "j": [0, a, 0],
                                "system": "local"})
    assert ul == pytest.approx(ug, rel=1e-9, abs=1e-15)


def test_vertical_joint_offset_matches_cp_and_load_is_flexural():
    """A vertical (global Z) joint offset is parallel to the gravity tip
    load: no torsion, cantilever deflection unchanged."""
    d, u = _offset_cantilever({"i": [0, 0, -0.3], "j": [0, 0, -0.3]})
    _, u0 = _offset_cantilever(None)
    assert u[2] == pytest.approx(u0[2], rel=1e-9)
    assert abs(d["cases"]["P"]["member_forces"]["B"][3]) < 1e-9


def test_no_transform_stiffness_reproduces_centroid():
    d8, uz8 = _ss_beam(8, pin_pin=True)
    mdl = _mdl("nt")
    L = 6.0
    for uid, p0, p1 in (("B1", (0, 0, 0), (3, 0, 0)),
                        ("B2", (3, 0, 0), (6, 0, 0))):
        mdl.add_member("beam", "S", p0, p1, uid=uid, cardinal_point=8,
                       no_transform_stiffness=True)
        _udl(mdl, uid, 20.0)
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 0, 0)))
    mdl.supports.append(PointSupport((L, 0, 0), (1, 1, 1, 1, 0, 0)))
    mdl.add_case("W", {"W": 1.0})
    d = _run(mdl)
    d10, _ = _ss_beam(10, pin_pin=True)
    assert json.dumps(d["cases"]["W"]["member_forces"], sort_keys=True) == \
        json.dumps(d10["cases"]["W"]["member_forces"], sort_keys=True)
    assert d8["cases"]["W"]["member_forces"] != d10["cases"]["W"][
        "member_forces"]


@pytest.mark.parametrize("load", ["point_end", "partial_udl", "full_udl"])
def test_axial_member_load_on_eccentric_axis(load):
    """Axial (global X) member loads act ON the eccentric axis: the fixed
    end of a cp 8 cantilever resists the resultant W plus the couple W*e
    (FEF eccentric-moment transfer / eleLoad on the offset element)."""
    L, e = 4.0, H / 2
    mdl = _mdl("ax")
    mdl.add_member("beam", "S", (0, 0, 0), (L, 0, 0), uid="B",
                   cardinal_point=8)
    mdl.supports.append(PointSupport((0, 0, 0), (1,) * 6))
    pat = mdl.pattern("A", "other")
    if load == "point_end":
        pat.member_loads.append(MemberLoad("B", "point", 12.0, a=1.0,
                                           direction="global_x"))
        W = 12.0
    elif load == "partial_udl":
        pat.member_loads.append(MemberLoad("B", "udl", 5.0, a=0.25, b=0.75,
                                           direction="global_x"))
        W = 5.0 * 0.5 * L
    else:
        pat.member_loads.append(MemberLoad("B", "udl", 5.0,
                                           direction="global_x"))
        W = 5.0 * L
    mdl.add_case("A", {"A": 1.0})
    d = _run(mdl)
    rx = d["cases"]["A"]["reactions"]
    base = _node(d, (0, 0, 0))
    r = rx[base] if isinstance(rx, dict) else None
    assert r is not None
    assert r[0] == pytest.approx(-W, rel=1e-6)
    assert abs(r[4]) == pytest.approx(W * e, rel=1e-6)


def test_cp8_on_meshed_slab_stiffens_beam():
    """A beam on a shell slab is split at the mesh nodes; cardinal point 8
    (top flush with the slab) adds composite stiffness."""
    from skyframe.core.model import ShellSection

    def run(cp):
        mdl = _mdl("slab")
        mdl.add_shell_section(ShellSection("SL", "C", 0.15))
        mdl.add_shell("slab", "shell", "SL",
                      [(0, -1, 0), (6, -1, 0), (6, 1, 0), (0, 1, 0)],
                      mesh_size=0.5)
        mdl.add_member("beam", "S", (0, 0, 0), (6, 0, 0), uid="B",
                       cardinal_point=cp)
        for p in ((0, 0, 0), (6, 0, 0)):
            mdl.supports.append(PointSupport(p, (1, 1, 1, 1, 0, 0)))
        _udl(mdl, "B", 30.0)
        mdl.add_case("W", {"W": 1.0})
        d = _run(mdl)
        return d["cases"]["W"]["node_disp"][_node(d, (3, 0, 0))][2]

    u10, u8 = run(10), run(8)
    assert u8 < 0 and u10 < 0
    assert abs(u8) < 0.7 * abs(u10)


# --------------------------------------------------------------------------- #
# automatic end offsets
# --------------------------------------------------------------------------- #
CB, CH = 0.4, 0.6       # column b x h (h along local 2 = global -Y)


def _portal(**beam_kw):
    mdl = BuildingModel(name="portal")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("C", E=E, nu=NU))
    mdl.set_stories([3.5])
    mdl.num_modes = 0
    mdl.add_section(FrameSection.rectangular("COL", "C", CB, CH))
    mdl.add_section(FrameSection.rectangular("BM", "C", 0.3, 0.7))
    col_kw = beam_kw.pop("col_kw", {})
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, 3.5), uid="C1",
                   **col_kw.get("C1", {}))
    mdl.add_member("column", "COL", (6, 0, 0), (6, 0, 3.5), uid="C2",
                   **col_kw.get("C2", {}))
    mdl.add_member("beam", "BM", (0, 0, 3.5), (6, 0, 3.5), uid="B1",
                   **beam_kw)
    for p in ((0, 0, 0), (6, 0, 0)):
        mdl.supports.append(PointSupport(p, (1,) * 6))
    mdl.pattern("L", "other").nodal_loads.append(
        NodalLoad((0, 0, 3.5), fx=50.0))
    _udl(mdl, "B1", 25.0, pat="L")
    mdl.add_case("L", {"L": 1.0})
    return mdl


def _drift(mdl):
    d = _run(mdl)
    return d, d["cases"]["L"]["node_disp"][_node(d, (0, 0, 3.5))][0]


def _auto_kw(f):
    a = {"end_offsets": "auto", "auto_rigid_factor": f}
    return dict(a, col_kw={"C1": a, "C2": a})


def test_auto_end_lengths_from_connectivity():
    mdl = _portal(**_auto_kw(1.0))
    m = {x.uid: x for x in mdl.members}
    # beam along X: column extent along X = b (local 3 of an angle-0
    # column is global X)
    assert ins.auto_end_lengths(mdl, m["B1"]) == pytest.approx((CB / 2,
                                                               CB / 2))
    # column top under the beam: half beam depth; base: nothing
    assert ins.auto_end_lengths(mdl, m["C1"]) == pytest.approx((0.0, 0.35))
    m["C1"].angle = 90.0
    assert ins.auto_end_lengths(mdl, m["B1"])[0] == pytest.approx(CH / 2)


@pytest.mark.parametrize("f", [1.0, 0.5])
def test_auto_equals_manual_same_lengths(f):
    _, auto = _drift(_portal(**_auto_kw(f)))
    man = _portal(rigid_i=CB / 2, rigid_j=CB / 2, rigid_factor=f,
                  col_kw={"C1": {"rigid_j": 0.35, "rigid_factor": f},
                          "C2": {"rigid_j": 0.35, "rigid_factor": f}})
    d_m, manual = _drift(man)
    assert auto == pytest.approx(manual, rel=1e-12)
    d_a, _ = _drift(_portal(**_auto_kw(f)))
    assert json.dumps(d_a["cases"]["L"]["member_forces"], sort_keys=True) \
        == json.dumps(d_m["cases"]["L"]["member_forces"], sort_keys=True)


def test_auto_factor_zero_is_centerline():
    d0, u0 = _drift(_portal(**_auto_kw(0.0)))
    dc, uc = _drift(_portal())
    assert json.dumps(d0["cases"], sort_keys=True) == json.dumps(
        dc["cases"], sort_keys=True)


def test_rigid_zone_factor_brackets_finite_joint_model():
    """'Exact' model: the joint zones are explicit members 10x stiffer than
    the beam/column (finite, not rigid).  Its sway lies between the
    factor-0 (flexible) and factor-1 (rigid) automatic answers."""
    _, u0 = _drift(_portal(**_auto_kw(0.0)))
    _, u1 = _drift(_portal(**_auto_kw(1.0)))
    _, uh = _drift(_portal(**_auto_kw(0.5)))
    mdl = BuildingModel(name="exact")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("C", E=E, nu=NU))
    mdl.add_material(Material("CJ", E=10 * E, nu=NU))
    mdl.set_stories([3.5])
    mdl.num_modes = 0
    mdl.add_section(FrameSection.rectangular("COL", "C", CB, CH))
    mdl.add_section(FrameSection.rectangular("BM", "C", 0.3, 0.7))
    mdl.add_section(FrameSection.rectangular("COLJ", "CJ", CB, CH))
    mdl.add_section(FrameSection.rectangular("BMJ", "CJ", 0.3, 0.7))
    zc = 3.5 - 0.35
    for x, u in ((0, "C1"), (6, "C2")):
        mdl.add_member("column", "COL", (x, 0, 0), (x, 0, zc), uid=u)
        mdl.add_member("column", "COLJ", (x, 0, zc), (x, 0, 3.5),
                       uid=u + "j")
    xs = [0, CB / 2, 6 - CB / 2, 6]
    secs = ["BMJ", "BM", "BMJ"]
    for k in range(3):
        mdl.add_member("beam", secs[k], (xs[k], 0, 3.5),
                       (xs[k + 1], 0, 3.5), uid=f"B{k}")
        _udl(mdl, f"B{k}", 25.0, pat="L")
    for p in ((0, 0, 0), (6, 0, 0)):
        mdl.supports.append(PointSupport(p, (1,) * 6))
    mdl.pattern("L", "other").nodal_loads.append(
        NodalLoad((0, 0, 3.5), fx=50.0))
    mdl.add_case("L", {"L": 1.0})
    _, ux = _drift(mdl)
    assert u1 < ux < u0
    assert u1 < uh < u0
    assert u0 - u1 > 0.02 * u0          # the zones matter


def test_auto_user_offset_wins_per_end():
    mdl = _portal(end_offsets="auto", auto_rigid_factor=1.0, rigid_i=0.5)
    off = ins.compute_auto_end_offsets(mdl)
    assert off["B1"] == pytest.approx((0.5, CB / 2))
    rep = ins.end_offset_report(mdl)["B1"]
    assert rep["length_i"] == 0.5 and rep["length_j"] == pytest.approx(
        CB / 2)
    assert rep["clear_span"] == pytest.approx(6 - 0.5 - CB / 2)
    assert ins.end_offset_report(mdl)["C1"]["mode"] == "manual"


def test_auto_offsets_report_factor_zero_keeps_clear_span():
    mdl = _portal(**_auto_kw(0.0))
    rep = ins.end_offset_report(mdl)["B1"]
    assert (rep["rigid_i"], rep["rigid_j"]) == (0.0, 0.0)
    assert rep["clear_span"] == pytest.approx(6 - CB)


# --------------------------------------------------------------------------- #
# defaults, round-trip, validation
# --------------------------------------------------------------------------- #
def test_defaults_byte_identical_quick_building():
    m1 = quick_building(stories=2, bays_x=2, bays_y=1)
    m2 = BuildingModel.from_dict(json.loads(json.dumps(m1.to_dict())))
    for m in m2.members:
        m.cardinal_point = 10
        m.joint_offsets = None
        m.no_transform_stiffness = False
        m.end_offsets = "manual"
        m.auto_rigid_factor = 0.0
    r1 = json.dumps(_run(m1)["cases"], sort_keys=True)
    r2 = json.dumps(_run(m2)["cases"], sort_keys=True)
    assert r1 == r2


def test_roundtrip_fields_and_legacy_defaults():
    mdl = _mdl("rt")
    mdl.add_member("beam", "S", (0, 0, 0), (5, 0, 0), uid="B",
                   cardinal_point=8, no_transform_stiffness=True,
                   joint_offsets={"i": [0, 0.1, 0], "system": "local"},
                   end_offsets="auto", auto_rigid_factor=0.75)
    md = mdl.to_dict()["members"][0]
    assert md["cardinal_point"] == 8
    assert md["joint_offsets"] == {"system": "local", "i": [0.0, 0.1, 0.0],
                                   "j": [0.0, 0.0, 0.0]}
    assert md["no_transform_stiffness"] is True
    assert (md["end_offsets"], md["auto_rigid_factor"]) == ("auto", 0.75)
    back = BuildingModel.from_dict(json.loads(json.dumps(mdl.to_dict())))
    assert back.to_dict()["members"][0] == md
    for k in ("cardinal_point", "joint_offsets", "no_transform_stiffness",
              "end_offsets", "auto_rigid_factor"):
        md.pop(k)
    m0 = BuildingModel.from_dict({**mdl.to_dict(), "members": [md]}
                                 ).members[0]
    assert (m0.cardinal_point, m0.joint_offsets, m0.no_transform_stiffness,
            m0.end_offsets, m0.auto_rigid_factor) == (10, None, False,
                                                      "manual", 0.0)


@pytest.mark.parametrize("kw,match", [
    ({"cardinal_point": 0}, "cardinal_point"),
    ({"cardinal_point": 12}, "cardinal_point"),
    ({"cardinal_point": "8"}, "cardinal_point"),
    ({"cardinal_point": True}, "cardinal_point"),
    ({"joint_offsets": {"i": [0, 1]}}, "joint_offsets"),
    ({"joint_offsets": {"i": [0, 0, math.inf]}}, "joint_offsets"),
    ({"joint_offsets": {"system": "polar"}}, "system"),
    ({"joint_offsets": {"k": [0, 0, 0]}}, "unknown"),
    ({"end_offsets": "both"}, "end_offsets"),
    ({"auto_rigid_factor": 1.5}, "auto_rigid_factor"),
    ({"auto_rigid_factor": -0.1}, "auto_rigid_factor"),
    ({"cardinal_point": 8, "axial_limit": "tension"}, "axial-only"),
])
def test_validation_rejects(kw, match):
    mdl = _mdl("v")
    with pytest.raises(ValueError, match=match):
        mdl.add_member("beam", "S", (0, 0, 0), (5, 0, 0), uid="B", **kw)


def test_model_validate_catches_bad_insertion():
    mdl = _mdl("v2")
    mdl.add_member("beam", "S", (0, 0, 0), (5, 0, 0), uid="B")
    d = mdl.to_dict()
    d["members"][0]["cardinal_point"] = 13
    with pytest.raises(ValueError, match="cardinal_point"):
        BuildingModel.from_dict(d).validate()


def test_auto_offsets_cannot_eat_member_warns():
    mdl = _mdl("tiny")
    mdl.add_section(FrameSection.rectangular("BIG", "C", 2.0, 2.0))
    mdl.add_member("column", "BIG", (0, 0, 0), (0, 0, 3), uid="C1")
    mdl.add_member("column", "BIG", (1.5, 0, 0), (1.5, 0, 3), uid="C2")
    mdl.add_member("beam", "S", (0, 0, 3), (1.5, 0, 3), uid="B",
                   end_offsets="auto", auto_rigid_factor=1.0)
    with pytest.warns(UserWarning, match="no clear span"):
        off = ins.compute_auto_end_offsets(mdl)
    assert off["B"] == (0.0, 0.0)
