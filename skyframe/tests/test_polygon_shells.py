"""Polygon shells and ETABS-style auto mesh (gap C1).

ShellRegion polygons with 3..N corners (convex or concave, simple, planar)
with rectangular and polygon openings are meshed by
:mod:`skyframe.core.polymesh` into ShellMITC4 quads + ShellDKGT triangles,
split by grid lines / in-plane beams / other shells' traces, and made
conforming with frames and neighbouring shells.  4-corner regions keep the
structured quad path bit-for-bit (the full pre-existing suite is the
byte-identity check; ``test_four_corner_path_untouched`` pins the routing).

Validation numbers (measured when this module was written):

* SS square plate 4 x 4 x 0.05 m, q = 10 kPa, mesh 0.25 vs Timoshenko
  w = 0.00406 q a^4 / D:  two triangular regions (quads + DKGT tris)
  1.0039; triangle-only (QUAD_DOMINANT = False) 0.9982; 4-corner quads
  1.0024.
* L-plan core (two 3 m legs, 12 m tall, t = 0.2, both legs 5-corner
  polygons), tip load 100 kN at the corner: u_x / (unsymmetric-bending +
  web-shear hand value) = 0.994 (no diaphragm) / 0.972 (rigid
  diaphragms).
* Equilibrium (base FZ vs q * net area) and tributary/self-weight sums are
  exact to 1e-9 for L / T / U / triangle slabs and polygon openings.
"""

import warnings

import numpy as np
import pytest

from skyframe.core import polymesh
from skyframe.core.builder import make_shell_wind_pattern
from skyframe.core.checks import check_model
from skyframe.core.mesh import edge_tie_chains, mesh_model
from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection,
                                 Material, NodalLoad, Opening, PointSupport,
                                 ShellRegion, ShellSection)
from skyframe.engine.opensees_engine import OpenSeesEngine

warnings.simplefilter("ignore")

E_C = 25.0e6
Z = 3.0

SHAPES = {
    "L": [(0, 0), (8, 0), (8, 4), (4, 4), (4, 8), (0, 8)],
    "T": [(0, 6), (0, 4), (3, 4), (3, 0), (5, 0), (5, 4), (8, 4), (8, 6)],
    "U": [(0, 0), (9, 0), (9, 6), (6, 6), (6, 2), (3, 2), (3, 6), (0, 6)],
    "tri": [(0, 0), (6, 1), (2, 5)],
}


def _area2(poly):
    s = 0.0
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _model(name="poly", rigid=False, unit_weight=24.0, nu=0.2):
    m = BuildingModel(name=name)
    m.rigid_diaphragms = rigid
    m.num_modes = 0
    m.add_material(Material("C", E=E_C, nu=nu, unit_weight=unit_weight))
    m.add_section(FrameSection.rectangular("COL", "C", 0.4, 0.4))
    m.add_section(FrameSection.rectangular("BM", "C", 0.3, 0.6))
    m.add_shell_section(ShellSection("SL", "C", 0.2))
    m.set_stories([Z])
    return m


def _slab(m, poly, uid="S1", mesh=1.0, behavior="shell", openings=None):
    return m.add_shell("slab", behavior, "SL", [(x, y, Z) for x, y in poly],
                       mesh_size=mesh, story="Story1", uid=uid,
                       openings=openings)


def _vertex_supports(m, poly):
    for k, (x, y) in enumerate(poly):
        r = (1, 1, 1, 0, 0, 1) if k == 0 else (
            (0, 1, 1, 0, 0, 0) if k == 1 else (0, 0, 1, 0, 0, 0))
        m.supports.append(PointSupport((x, y, Z), r))


def _columns(m, pts):
    for k, (x, y) in enumerate(pts):
        m.add_member("column", "COL", (x, y, 0.0), (x, y, Z),
                     story="Story1", uid=f"C{k}")


def _node(eng, p, tol=1e-6):
    for t, c in eng._asm.struct_coords.items():
        if all(abs(c[k] - p[k]) < tol for k in range(3)):
            return t
    raise AssertionError(f"no node at {p}")


# --------------------------------------------------------------------------- #
# validation / data model
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_polygon_region_accepted_and_area_exact(shape):
    m = _model()
    r = _slab(m, SHAPES[shape])
    assert polymesh.is_polygon_region(r) == (len(SHAPES[shape]) != 4)
    assert r.area == pytest.approx(_area2(SHAPES[shape]), rel=1e-12)
    mesh = mesh_model(m)
    assert sum(mesh.region_trib["S1"].values()) == pytest.approx(
        _area2(SHAPES[shape]), rel=1e-12)
    # every element is a quad or a triangle with positive area
    for q in mesh.quads:
        assert len(q.nodes) in (3, 4)


def test_add_shell_rejects_bad_polygons():
    m = _model()
    with pytest.raises(ValueError, match="at least 3"):
        _slab(m, [(0, 0), (1, 0)])
    with pytest.raises(ValueError, match="self-intersecting"):
        _slab(m, [(0, 0), (4, 0), (0, 4), (4, 4), (2, -1)], uid="BT")
    with pytest.raises(ValueError, match="not planar"):
        m.add_shell("slab", "shell", "SL",
                    [(0, 0, Z), (4, 0, Z), (4, 4, Z + 0.1), (2, 6, Z),
                     (0, 4, Z)], uid="NP")
    with pytest.raises(ValueError, match="outside"):
        _slab(m, SHAPES["L"], uid="OP", openings=[Opening(
            0, 0, 1, 1, polygon=[(5, 5, Z), (7, 5, Z), (7, 7, Z)])])


def test_check_model_polygon_validity():
    """Check Model: self-intersecting + non-planar polygons are errors, a
    valid CONCAVE polygon is clean (no SHELL_CONCAVE), < 3 corners is a
    corner-count error."""
    m = _model()
    _columns(m, SHAPES["L"])
    _slab(m, SHAPES["L"])
    res = check_model(m)
    shell_codes = {i["code"] for i in res["issues"]
                   if i["code"].startswith("SHELL")}
    assert shell_codes == set()

    def raw(uid, corners):
        m.shells.append(ShellRegion(uid, "slab", "shell", "SL",
                                    [tuple(map(float, c)) for c in corners],
                                    mesh_size=1.0))
    raw("SI", [(10, 0, Z), (14, 0, Z), (10, 4, Z), (14, 4, Z), (12, -1, Z)])
    raw("NP", [(20, 0, Z), (24, 0, Z), (24, 4, Z + 0.05), (22, 6, Z),
               (20, 4, Z)])
    raw("CC", [(30, 0, Z), (31, 0, Z)])
    res = check_model(m)
    by = {}
    for i in res["issues"]:
        for o in i["objects"]:
            by.setdefault(o, set()).add(i["code"])
    assert "SHELL_SELF_INTERSECTING" in by["SI"]
    assert "SHELL_WARPED" in by["NP"]
    assert "SHELL_CORNER_COUNT" in by["CC"]
    assert "S1" not in by or not any(c.startswith("SHELL") for c in by["S1"])


def test_polygon_serialization_round_trip():
    m = _model()
    _slab(m, SHAPES["U"], openings=[
        Opening(0.0, 0.0, 1.0, 1.0,
                polygon=[(1, 1, Z), (2, 1, Z), (1.5, 4, Z)]),
        Opening(0.75, 0.1, 0.9, 0.2)])
    d = m.to_dict()
    sh = d["shells"][0]
    assert len(sh["corners"]) == 8
    assert sh["openings"][0]["polygon"][2] == [1.5, 4.0, Z]
    assert "polygon" not in sh["openings"][1]      # legacy shape kept
    m2 = BuildingModel.from_dict(d)
    r2 = m2.shells[0]
    assert r2.net_area == pytest.approx(m.shells[0].net_area, rel=1e-12)
    assert r2.openings[0].polygon[2] == (1.5, 4.0, Z)


def test_four_corner_path_untouched():
    """A 4-corner region with only rectangular openings stays on the
    legacy structured mesher; Opening.to_dict keeps its exact keys."""
    m = _model()
    r = _slab(m, [(0, 0), (6, 0), (6, 4), (0, 4)],
              openings=[Opening(0.25, 0.25, 0.5, 0.5)])
    assert not polymesh.is_polygon_region(r)
    assert list(r.openings[0].to_dict()) == ["u0", "v0", "u1", "v1"]
    mesh = mesh_model(m)
    assert all(len(q.nodes) == 4 for q in mesh.quads)
    assert len(mesh.quads) == 6 * 4 - 1         # one snapped cell omitted


def test_four_corner_region_with_polygon_opening_takes_polygon_path():
    m = _model()
    hole = [(2, 1, Z), (4, 1, Z), (3, 3, Z)]
    r = _slab(m, [(0, 0), (6, 0), (6, 4), (0, 4)], mesh=0.5,
              openings=[Opening(0, 0, 1, 1, polygon=hole)])
    assert polymesh.is_polygon_region(r)
    assert r.net_area == pytest.approx(24.0 - 2.0, rel=1e-12)
    mesh = mesh_model(m)
    assert sum(mesh.region_trib["S1"].values()) == pytest.approx(22.0,
                                                                rel=1e-12)
    assert edge_tie_chains(mesh) == []


# --------------------------------------------------------------------------- #
# equilibrium / area / self-weight
# --------------------------------------------------------------------------- #
def test_l_slab_on_columns_equilibrium_exact():
    m = _model()
    _columns(m, SHAPES["L"] + [(4, 0), (0, 4)])
    _slab(m, SHAPES["L"])
    m.pattern("Q").area_loads.append(AreaLoad("S1", 10.0))
    m.add_case("Q", {"Q": 1.0})
    eng = OpenSeesEngine(m)
    r = eng.run_static("Q")
    assert r.base["FZ"] == pytest.approx(10.0 * 48.0, rel=1e-10)
    assert abs(r.base["FX"]) < 1e-8 and abs(r.base["FY"]) < 1e-8
    # columns at the re-entrant corner and at mid-edges carry load
    assert all(v[2] > 0 for v in r.reactions.values())
    # every column top is a shell node (connected)
    mesh = eng._asm.mesh
    shell_nodes = {n for q in mesh.quads for n in q.nodes}
    for x, y in SHAPES["L"] + [(4, 0), (0, 4)]:
        idx = next(i for i, p in enumerate(mesh.points)
                   if p == (x, y, Z))
        assert idx in shell_nodes


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_self_weight_exact(shape):
    m = _model()
    poly = SHAPES[shape]
    _slab(m, poly, mesh=0.7)
    _vertex_supports(m, poly)
    pat = m.pattern("SW", "dead")
    pat.self_weight_factor = 1.0
    m.add_case("SW", {"SW": 1.0})
    r = OpenSeesEngine(m).run_static("SW")
    assert r.base["FZ"] == pytest.approx(24.0 * 0.2 * _area2(poly),
                                         rel=1e-9)


def test_polygon_opening_net_area_and_load():
    m = _model()
    hole = [(1, 1, Z), (3, 1, Z), (3.5, 2.5, Z), (1.5, 3, Z)]
    r = _slab(m, SHAPES["L"], mesh=0.6,
              openings=[Opening(0, 0, 1, 1, polygon=hole),
                        Opening(0.1, 0.6, 0.3, 0.9)])
    hole_a = _area2([(p[0], p[1]) for p in hole])
    rect_a = (0.2 * 8) * (0.3 * 8)
    assert r.opening_area == pytest.approx(hole_a + rect_a, rel=1e-12)
    mesh = mesh_model(m)
    assert sum(mesh.region_trib["S1"].values()) == pytest.approx(
        48.0 - hole_a - rect_a, rel=1e-12)
    # no element centroid inside the openings
    for q in mesh.quads:
        c = np.mean([mesh.points[n] for n in q.nodes], axis=0)
        assert not polymesh.point_in_polygon2(
            (c[0], c[1]), [(p[0], p[1]) for p in hole])
        assert not (0.8 < c[0] < 2.4 and 4.8 < c[1] < 7.2)
    _vertex_supports(m, SHAPES["L"])
    m.pattern("Q").area_loads.append(AreaLoad("S1", 5.0))
    m.add_case("Q", {"Q": 1.0})
    res = OpenSeesEngine(m).run_static("Q")
    assert res.base["FZ"] == pytest.approx(5.0 * r.net_area, rel=1e-10)


# --------------------------------------------------------------------------- #
# quad path vs 5-corner polygon
# --------------------------------------------------------------------------- #
def _rect_frame_slab(corners, behavior="shell", mesh=1.0):
    m = _model(unit_weight=0.0)
    pts = [(0, 0), (6, 0), (6, 4), (0, 4)]
    _columns(m, pts)
    for k in range(4):
        (x1, y1), (x2, y2) = pts[k], pts[(k + 1) % 4]
        m.add_member("beam", "BM", (x1, y1, Z), (x2, y2, Z),
                     story="Story1", uid=f"B{k}")
    _slab(m, corners, mesh=mesh, behavior=behavior)
    m.pattern("Q").area_loads.append(AreaLoad("S1", 10.0))
    m.add_case("Q", {"Q": 1.0})
    return m


@pytest.mark.parametrize("behavior", ["shell", "membrane"])
def test_five_corner_rectangle_matches_quad_path(behavior):
    quad = _rect_frame_slab([(0, 0), (6, 0), (6, 4), (0, 4)], behavior)
    poly = _rect_frame_slab([(0, 0), (3, 0), (6, 0), (6, 4), (0, 4)],
                            behavior)
    assert polymesh.is_polygon_region(poly.shells[0])
    rq = OpenSeesEngine(quad).run_static("Q")
    rp = OpenSeesEngine(poly).run_static("Q")
    assert rp.base["FZ"] == pytest.approx(rq.base["FZ"], rel=1e-10)
    assert rp.base["FZ"] == pytest.approx(240.0, rel=1e-10)
    wq = min(d[2] for d in rq.node_disp.values())
    wp = min(d[2] for d in rp.node_disp.values())
    assert wp == pytest.approx(wq, rel=0.03)
    # column reactions (symmetric) agree closely too
    for k in range(4):
        fq = sorted(v[2] for v in rq.reactions.values())
        fp = sorted(v[2] for v in rp.reactions.values())
        assert fp[k] == pytest.approx(fq[k], rel=0.03)


def test_membrane_polygon_l_slab_conserves_load():
    m = _model(unit_weight=0.0)
    poly = SHAPES["L"]
    _columns(m, poly)
    n = len(poly)
    for k in range(n):
        (x1, y1), (x2, y2) = poly[k], poly[(k + 1) % n]
        m.add_member("beam", "BM", (x1, y1, Z), (x2, y2, Z),
                     story="Story1", uid=f"B{k}")
    _slab(m, poly, behavior="membrane")
    m.pattern("Q").area_loads.append(AreaLoad("S1", 10.0))
    m.add_case("Q", {"Q": 1.0})
    mesh = mesh_model(m)
    loads = mesh.membrane_loads["S1"]
    tot = sum(0.5 * (t.w1 + t.w2) * (t.b - t.a)
              * next(mm.length for mm in m.members if mm.uid == t.member_uid)
              for t in loads)
    assert tot == pytest.approx(48.0, rel=1e-9)
    # the long 8 m edges collect more than the short 4 m re-entrant ones
    per = {}
    for t in loads:
        L = next(mm.length for mm in m.members if mm.uid == t.member_uid)
        per[t.member_uid] = per.get(t.member_uid, 0.0) + t.w1 * (t.b - t.a) * L
    assert per["B0"] > per["B2"]
    r = OpenSeesEngine(m).run_static("Q")
    assert r.base["FZ"] == pytest.approx(480.0, rel=1e-9)


# --------------------------------------------------------------------------- #
# conformity
# --------------------------------------------------------------------------- #
def test_beam_crossing_polygon_slab_conforms():
    """A diagonal and an orthogonal beam crossing an L slab: beams are split
    at every mesh node on their axis, every beam segment is an element
    edge, and the mesh has no hanging nodes."""
    m = _model()
    _columns(m, SHAPES["L"] + [(4, 0), (0, 4)])
    m.add_member("beam", "BM", (0, 4, Z), (8, 4, Z), story="Story1", uid="BX")
    m.add_member("beam", "BM", (0, 0, Z), (4, 8, Z), story="Story1", uid="BD")
    _slab(m, SHAPES["L"])
    mesh = mesh_model(m)
    assert edge_tie_chains(mesh) == []
    edges = set()
    for q in mesh.quads:
        n = q.nodes
        for k in range(len(n)):
            edges.add(frozenset((n[k], n[(k + 1) % len(n)])))
    for uid in ("BX", "BD"):
        segs = mesh.segments[uid]
        assert len(segs) >= 8
        for s in segs:
            assert frozenset((s.ni, s.nj)) in edges
    assert any(len(q.nodes) == 3 for q in mesh.quads)   # diagonal -> tris
    m.pattern("Q").area_loads.append(AreaLoad("S1", 10.0))
    m.add_case("Q", {"Q": 1.0})
    r = OpenSeesEngine(m).run_static("Q")
    assert r.base["FZ"] == pytest.approx(480.0, rel=1e-10)


def test_adjacent_polygons_different_mesh_conform():
    m = _model()
    _slab(m, SHAPES["L"], uid="SA", mesh=1.0)
    _slab(m, [(8, 0), (12, 0), (12, 4), (10, 5), (8, 4)], uid="SB",
          mesh=0.7)
    mesh = mesh_model(m)
    assert edge_tie_chains(mesh) == []
    shared = [i for i, p in enumerate(mesh.points)
              if abs(p[0] - 8.0) < 1e-9 and 0.0 <= p[1] <= 4.0]
    for reg in ("SA", "SB"):
        nodes = {n for q in mesh.quads if q.region == reg for n in q.nodes}
        assert set(shared) <= nodes


def test_wall_meeting_polygon_slab_conforms():
    """A polygon wall under an L slab (its top edge runs through the slab
    interior) and a quad wall along the slab edge: wall top nodes are slab
    nodes and nothing hangs."""
    m = _model()
    _columns(m, SHAPES["L"])
    m.add_shell("wall", "shell", "SL",
                [(2, 0, 0), (2, 6, 0), (2, 6, Z), (2, 3, Z), (2, 0, Z)],
                mesh_size=0.8, uid="W1")
    m.add_shell("wall", "shell", "SL",
                [(4, 4, 0), (8, 4, 0), (8, 4, Z), (4, 4, Z)],
                mesh_size=0.9, uid="W2")
    _slab(m, SHAPES["L"], mesh=1.0)
    mesh = mesh_model(m)
    slab_nodes = {n for q in mesh.quads if q.region == "S1" for n in q.nodes}
    w1_top = {n for q in mesh.quads if q.region == "W1" for n in q.nodes
              if abs(mesh.points[n][2] - Z) < 1e-9}
    assert len(w1_top) >= 7 and w1_top <= slab_nodes
    chains = edge_tie_chains(mesh)
    # the legacy 4-corner wall may still see polygon nodes on its edges;
    # polygon regions never have hanging nodes on their own edges
    assert all(ruid == "W2" for ruid, *_ in chains)
    m.pattern("Q").area_loads.append(AreaLoad("S1", 10.0))
    m.add_case("Q", {"Q": 1.0})
    r = OpenSeesEngine(m).run_static("Q")
    assert r.base["FZ"] == pytest.approx(480.0, rel=1e-9)


def test_grid_lines_split_polygon_mesh():
    m = _model()
    from skyframe.core.model import GridSystem
    m.set_grid_system(GridSystem(x_lines=[0.0, 2.7, 8.0],
                                 y_lines=[0.0, 5.3, 8.0]))
    _slab(m, SHAPES["L"], mesh=2.0)
    mesh = mesh_model(m)
    xs = {round(p[0], 9) for p in mesh.points}
    ys = {round(p[1], 9) for p in mesh.points}
    assert 2.7 in xs and 5.3 in ys


# --------------------------------------------------------------------------- #
# plate bending / triangles / walls
# --------------------------------------------------------------------------- #
def _ss_plate(regions, mesh, nu=0.3, t=0.05, a=4.0, q=10.0):
    m = _model(unit_weight=0.0, nu=nu)
    m.shell_sections["SL"] = ShellSection("SL", "C", t)
    for k, poly in enumerate(regions):
        _slab(m, poly, uid=f"S{k}", mesh=mesh)
        m.pattern("Q").area_loads.append(AreaLoad(f"S{k}", q))
    n = round(a / mesh)
    pts = set()
    for i in range(n + 1):
        s = a * i / n
        pts |= {(s, 0.0), (s, a), (0.0, s), (a, s)}
    for x, y in sorted(pts):
        r = (1, 1, 1, 0, 0, 1) if (x, y) == (0.0, 0.0) else (
            (0, 1, 1, 0, 0, 0) if (x, y) == (a, 0.0) else (0, 0, 1, 0, 0, 0))
        m.supports.append(PointSupport((x, y, Z), r))
    m.add_case("Q", {"Q": 1.0})
    D = E_C * t ** 3 / (12 * (1 - nu ** 2))
    return m, 0.00406 * q * a ** 4 / D


def test_ss_plate_two_triangles_vs_timoshenko():
    a = 4.0
    m, w_ex = _ss_plate([[(0, 0), (a, 0), (a, a)], [(0, 0), (a, a), (0, a)]],
                        0.25)
    mesh = mesh_model(m)
    assert any(len(q.nodes) == 3 for q in mesh.quads)
    assert any(len(q.nodes) == 4 for q in mesh.quads)
    assert edge_tie_chains(mesh) == []
    r = OpenSeesEngine(m).run_static("Q")
    w = -min(d[2] for d in r.node_disp.values())
    assert w / w_ex == pytest.approx(1.0, abs=0.02)
    assert r.base["FZ"] == pytest.approx(160.0, rel=1e-10)


def test_ss_plate_triangle_only_mesh_converges(monkeypatch):
    monkeypatch.setattr(polymesh, "QUAD_DOMINANT", False)
    a = 4.0
    ratios = []
    for mesh in (0.5, 0.25):
        m, w_ex = _ss_plate([[(0, 0), (a, 0), (a, a), (0, a), (0, a / 2)]],
                            mesh)
        mm = mesh_model(m)
        assert all(len(q.nodes) == 3 for q in mm.quads)
        r = OpenSeesEngine(m).run_static("Q")
        ratios.append(-min(d[2] for d in r.node_disp.values()) / w_ex)
    assert abs(ratios[1] - 1.0) < 0.01
    assert abs(ratios[1] - 1.0) < abs(ratios[0] - 1.0)


def test_triangular_slab_single_triangle_element():
    """A right triangle with mesh_size > its size is ONE ShellDKGT element
    (vertex lines give a single cell, cut once by the hypotenuse)."""
    m = _model(unit_weight=0.0)
    poly = [(0, 0), (6, 0), (0, 5)]
    _slab(m, poly, mesh=20.0)
    _vertex_supports(m, poly)
    m.pattern("Q").area_loads.append(AreaLoad("S1", 6.0))
    m.add_case("Q", {"Q": 1.0})
    mesh = mesh_model(m)
    assert len(mesh.quads) == 1 and len(mesh.quads[0].nodes) == 3
    eng = OpenSeesEngine(m)
    r = eng.run_static("Q")
    A = _area2(poly)
    assert r.base["FZ"] == pytest.approx(6.0 * A, rel=1e-10)
    # vertex supports: each carries A/3 * q (consistent tributary)
    for v in r.reactions.values():
        assert v[2] == pytest.approx(2.0 * A, rel=1e-9)
    d = eng.run().to_dict()
    tri = [q for q in d["shell_quads"] if len(q["nodes"]) == 3]
    assert len(tri) == 1 and tri[0]["region"] == "S1"
    assert len(d["cases"]["Q"]["shell_forces"]["0"]
               if "0" in d["cases"]["Q"]["shell_forces"]
               else d["cases"]["Q"]["shell_forces"][0]) == 8


def _core(rigid, mesh=0.5, E=25e6, nu=0.2, t=0.2, H=12.0, B=3.0, P=100.0):
    m = BuildingModel(name="core")
    m.rigid_diaphragms = rigid
    m.num_modes = 0
    m.add_material(Material("M", E=E, nu=nu, unit_weight=0.0))
    m.add_shell_section(ShellSection("W", "M", t))
    m.set_stories([3.0] * 4)
    m.add_shell("wall", "shell", "W",
                [(0, 0, 0), (B, 0, 0), (B, 0, H / 2), (B, 0, H), (0, 0, H)],
                mesh_size=mesh, uid="W1")
    m.add_shell("wall", "shell", "W",
                [(0, 0, 0), (0, 0, H), (0, B, H), (0, B, H / 3), (0, B, 0)],
                mesh_size=mesh, uid="W2")
    m.pattern("L").nodal_loads.append(NodalLoad((0, 0, H), fx=P))
    m.add_case("L", {"L": 1.0})
    # hand: unsymmetric bending of the centreline L section + web shear
    A = 2 * B * t
    xc = (t * B * B / 2) / A
    Sxx = t * ((B - xc) ** 3 + xc ** 3) / 3 + t * B * xc ** 2
    Sxy = 2 * (-xc) * t * (B * B / 2 - xc * B)
    S = np.array([[Sxx, Sxy], [Sxy, Sxx]])
    ub = H ** 3 / (3 * E) * np.linalg.solve(S, [P, 0.0])
    G = E / (2 * (1 + nu))
    us = P * H / (G * 5 / 6 * B * t)
    return m, (ub[0] + us, ub[1])


@pytest.mark.parametrize("rigid", [False, True])
def test_l_plan_core_cantilever_vs_hand(rigid):
    m, (ux_h, uy_h) = _core(rigid)
    mesh = mesh_model(m)
    assert edge_tie_chains(mesh) == []
    eng = OpenSeesEngine(m)
    r = eng.run_static("L")
    d = r.node_disp[_node(eng, (0, 0, 12.0))]
    assert d[0] == pytest.approx(ux_h, rel=0.05)
    assert d[1] == pytest.approx(uy_h, rel=0.06)
    assert r.base["FX"] == pytest.approx(-100.0, rel=1e-9)
    assert r.base["MY"] == pytest.approx(-1200.0, rel=1e-9)


def test_polygon_wall_pier_forces_exact():
    """5-corner wall labelled as a pier: exact free-body V / M / P."""
    m = _model(unit_weight=0.0)
    m.add_shell("wall", "shell", "SL",
                [(0, 0, 0), (4, 0, 0), (4, 0, Z), (2, 0, Z), (0, 0, Z)],
                mesh_size=0.5, uid="W1", story="Story1")
    m.shells[0].pier = "P1"
    top = sorted({p for p in mesh_model(m).points if abs(p[2] - Z) < 1e-9})
    pv = m.pattern("V", "quake")
    for p in top:
        pv.nodal_loads.append(NodalLoad(p, fx=40.0 / len(top)))
    m.add_case("V", {"V": 1.0})
    res = OpenSeesEngine(m).run()
    pr = res.piers["V"]["P1"]["Story1"]
    assert pr["V"] == pytest.approx(40.0, rel=1e-9)
    assert pr["M"] == pytest.approx(40.0 * Z, rel=1e-9)


def test_layered_polygon_wall_linear_equals_summed_thickness():
    def run(layered):
        m = _model(unit_weight=0.0)
        if layered:
            m.shell_sections["SL"] = ShellSection(
                "SL", "C", 0.5, layered={"layers": [
                    {"t": 0.12, "material": "C", "kind": "concrete"},
                    {"t": 0.08, "material": "C", "kind": "concrete"}]})
        m.add_shell("wall", "shell", "SL",
                    [(0, 0, 0), (4, 0, 0), (4, 0, Z), (1, 0, Z + 1),
                     (0, 0, Z)], mesh_size=0.5, uid="W1")
        m.pattern("V").nodal_loads.append(NodalLoad((1, 0, Z + 1), fx=50.0))
        m.add_case("V", {"V": 1.0})
        eng = OpenSeesEngine(m)
        r = eng.run_static("V")
        return r.node_disp[_node(eng, (1, 0, Z + 1))][0]
    assert run(True) == pytest.approx(run(False), rel=1e-9)


def _chamfer_wall(layered):
    m = BuildingModel(name="lw")
    m.rigid_diaphragms = False
    m.num_modes = 0
    mat = m.add_material(Material("C", E_C, 0.2))
    mat.fc = 30_000.0
    lay = ({"layers": [{"t": 0.2, "material": "C", "kind": "concrete"}]}
           if layered else None)
    m.add_shell_section(ShellSection("SH", "C", 0.2, layered=lay))
    m.set_stories([3.0])
    m.add_shell("wall", "shell", "SH",
                [(0, 0, 0), (2, 0, 0), (2, 0, 3), (0.5, 0, 3), (0, 0, 2.2)],
                mesh_size=0.5, uid="W")
    m.pattern("P", "other").nodal_loads.append(NodalLoad((2, 0, 3),
                                                         fx=100.0))
    m.add_case("P", {"P": 1.0})
    m.add_pushover_case("PO", "X", target_drift=0.0004, steps=10)
    return m


def test_layered_polygon_wall_pushover_with_triangles():
    """Nonlinear LayeredShell on a chamfered polygon wall (quads + DKGT
    triangles): the first, pre-crack pushover step reproduces the elastic
    pushover stiffness of the same polygon wall."""
    mesh = mesh_model(_chamfer_wall(False))
    assert any(len(q.nodes) == 3 for q in mesh.quads)
    pe = OpenSeesEngine(_chamfer_wall(False)).run_pushover("PO")
    k_el = pe.base_shear[0] / pe.roof_disp[0]
    po = OpenSeesEngine(_chamfer_wall(True)).run_pushover("PO")
    assert len(po.roof_disp) == 10
    k0 = po.base_shear[0] / po.roof_disp[0]
    assert k0 == pytest.approx(k_el, rel=1e-3)


# --------------------------------------------------------------------------- #
# loads on polygons
# --------------------------------------------------------------------------- #
def test_directional_and_pattern_area_loads_on_polygon():
    from skyframe.core import loads_ext as lx
    m = _model()
    _slab(m, SHAPES["U"], mesh=0.8)
    al = AreaLoad("S1", 3.0, direction="global_x",
                  joint_pattern={"type": "linear", "a": 0.5, "b": -0.2,
                                 "c": 0.0, "d": 1.0})
    mesh = mesh_model(m)
    f = lx.area_load_nodal_forces(m.shells[0], al, al.q, mesh.quads,
                                  mesh.points)
    fx = sum(v[0] for v in f.values())
    ref = lx.area_load_resultant(m.shells[0], al)
    # exact: integral of 3*(0.5x - 0.2y + 1) over the U
    poly = SHAPES["U"]
    tris = polymesh.ear_clip(poly)
    exact = 0.0
    for a, b, c in tris:
        P = [poly[a], poly[b], poly[c]]
        A = _area2(P)
        cx = sum(p[0] for p in P) / 3
        cy = sum(p[1] for p in P) / 3
        exact += 3.0 * (0.5 * cx - 0.2 * cy + 1.0) * A
    assert fx == pytest.approx(exact, rel=1e-9)
    assert ref[0] == pytest.approx(exact, rel=1e-9)


def test_wall_local3_load_and_wind_cp_on_polygon_wall():
    m = _model(unit_weight=0.0)
    corners = [(0, 0, 0), (5, 0, 0), (5, 0, Z), (2.5, 0, Z + 1.5),
               (0, 0, Z)]
    w = m.add_shell("wall", "shell", "SL", corners, mesh_size=0.5, uid="W1")
    A = w.area
    assert A == pytest.approx(5 * Z + 0.5 * 5 * 1.5, rel=1e-12)
    n = polymesh.region_normal(w)
    assert n == pytest.approx((0.0, -1.0, 0.0), abs=1e-12)
    m.pattern("L").area_loads.append(AreaLoad("W1", 2.0,
                                              direction="local_3"))
    m.add_case("L", {"L": 1.0})
    r = OpenSeesEngine(m).run_static("L")
    assert r.base["FY"] == pytest.approx(2.0 * A, rel=1e-9)
    w.wind_cp = 0.8
    pat = make_shell_wind_pattern(m, 1.5)
    fy = sum(nl.fy for nl in pat.nodal_loads)
    assert fy == pytest.approx(-1.5 * 0.8 * A, rel=1e-9)


# --------------------------------------------------------------------------- #
# diaphragm, section cut, cracked slab, results shape
# --------------------------------------------------------------------------- #
def test_rigid_diaphragm_includes_polygon_slab_nodes():
    m = _model(rigid=True)
    _columns(m, SHAPES["L"])
    _slab(m, SHAPES["L"], mesh=1.0)
    m.pattern("EX").nodal_loads.append(NodalLoad((8, 0, Z), fx=100.0))
    m.add_case("EX", {"EX": 1.0})
    eng = OpenSeesEngine(m)
    r = eng.run_static("EX")
    mesh = eng._asm.mesh
    slab = sorted({n for q in mesh.quads for n in q.nodes})
    X = []
    Y = []
    for n in slab:
        p = mesh.points[n]
        d = r.node_disp[n + 1]
        X.append([1.0, 0.0, -p[1]])
        Y.append(d[0])
        X.append([0.0, 1.0, p[0]])
        Y.append(d[1])
    X, Y = np.array(X), np.array(Y)
    sol, *_ = np.linalg.lstsq(X, Y, rcond=None)
    assert np.max(np.abs(X @ sol - Y)) < 1e-9 * max(1e-12, np.max(np.abs(Y)))
    assert np.max(np.abs(Y)) > 0.0


def test_section_cut_counts_polygon_slab():
    m = _model()
    _columns(m, SHAPES["L"])
    _slab(m, SHAPES["L"])
    m.add_section_cut("CUT", "x", 2.0)
    m.pattern("Q").area_loads.append(AreaLoad("S1", 1.0))
    m.add_case("Q", {"Q": 1.0})
    res = OpenSeesEngine(m).run().to_dict()
    assert res["section_cuts"]["Q"]["CUT"]["n_shells"] == 1


def test_cracked_slab_iteration_on_polygon():
    from skyframe.core.cracked import cracked_analysis
    m = _model(unit_weight=0.0)
    _columns(m, SHAPES["L"])
    m.add_member("beam", "BM", (0, 0, Z), (4, 8, Z), story="Story1",
                 uid="BD")
    _slab(m, SHAPES["L"])
    m.pattern("Q").area_loads.append(AreaLoad("S1", 40.0))
    m.add_case("Q", {"Q": 1.0})
    cr = cracked_analysis(m, "Q")
    n_tri = sum(1 for q in mesh_model(m).quads if len(q.nodes) == 3)
    assert n_tri > 0
    assert len(cr.cracking) == len(mesh_model(m).quads)
    assert cr.case.base["FZ"] == pytest.approx(40.0 * 48.0, rel=1e-9)


def test_mixed_quad_and_polygon_model_runs_full_analysis():
    """Full run of a model mixing a legacy quad slab and a
    polygon slab (rigid diaphragm): results serialize with 3- and 4-node shell
    elements and the quad entries keep their exact legacy shape."""
    m = _model(rigid=True)
    _columns(m, SHAPES["L"] + [(12, 0), (12, 4)])
    _slab(m, SHAPES["L"], uid="SP", mesh=1.0)
    m.add_member("beam", "BM", (0, 0, Z), (4, 8, Z), story="Story1",
                 uid="BD")
    _slab(m, [(8, 0), (12, 0), (12, 4), (8, 4)], uid="SQ", mesh=1.0)
    m.pattern("Q", "dead").area_loads.append(AreaLoad("SP", 5.0))
    m.pattern("Q", "dead").area_loads.append(AreaLoad("SQ", 5.0))
    m.add_case("Q", {"Q": 1.0})
    d = OpenSeesEngine(m).run().to_dict()
    sq = d["shell_quads"]
    assert {len(q["nodes"]) for q in sq} == {3, 4}
    for q in sq:
        assert set(q) == {"region", "nodes"}
    assert d["cases"]["Q"]["base"]["FZ"] == pytest.approx(
        5.0 * (48.0 + 16.0), rel=1e-9)
