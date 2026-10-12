"""Wave C: frame pier labels, floor auto-mesh "none" + DB columns, per-area
auto edge constraints (CONTRACT "Frame pier labels", "Floor auto mesh
options: no auto mesh and DB columns", "Per-object auto edge
constraints").  Expected values are closed-form statics or the pinned
v0.22 zipper values.  Units kN, m."""

import copy
import json
import warnings

import pytest

from skyframe.core.mesh import edge_tie_chains, mesh_model
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 NodalLoad, PointSupport, ShellSection)
from skyframe.engine.opensees_engine import OpenSeesEngine

E = 25.0e6
FIX = (1, 1, 1, 1, 1, 1)


def _run(m):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m.validate()
        return OpenSeesEngine(m).run().to_dict()


def _frame(h=3.0, b=6.0, stories=1):
    m = BuildingModel(name="fp")
    m.rigid_diaphragms = False
    m.num_modes = 0
    m.add_material(Material("C", E, 0.2, unit_weight=0.0))
    m.add_section(FrameSection("COL", "C", A=0.16, I33=2.1e-3, I22=2.1e-3,
                               J=3.6e-3, b=0.4, h=0.4))
    m.add_section(FrameSection("BM", "C", A=0.18, I33=5.4e-3, I22=1.4e-3,
                               J=3.0e-3, b=0.3, h=0.6))
    m.add_shell_section(ShellSection("W", "C", 0.25))
    m.set_stories([h] * stories)
    return m


# --------------------------------------------------------------------------- #
# frame piers
# --------------------------------------------------------------------------- #
def test_two_column_pier_statics():
    """Portal (columns at x = 0 and 6, beam at z = 3) under F = 50 kN at the
    top and 30 kN gravity on each column top, both columns labelled P1:
    the free body above the base cut gives V = F, P = 60 (compression),
    M = F h about the column centroid (overturning)."""
    h, b, F = 3.0, 6.0, 50.0
    m = _frame(h, b)
    m.add_member("column", "COL", (0, 0, 0), (0, 0, h), uid="C1")
    m.add_member("column", "COL", (b, 0, 0), (b, 0, h), uid="C2")
    m.add_member("beam", "BM", (0, 0, h), (b, 0, h), uid="B1")
    for mm in m.members[:2]:
        mm.pier = "P1"
    pat = m.pattern("P", "other")
    pat.nodal_loads.append(NodalLoad((0, 0, h), fx=F, fz=-30.0))
    pat.nodal_loads.append(NodalLoad((b, 0, h), fz=-30.0))
    m.add_case("P", {"P": 1.0})
    res = _run(m)
    st = m.stories[0].name
    e = res["piers"]["P"]["P1"][st]
    assert e["V"] == pytest.approx(F, rel=1e-9)
    assert e["P"] == pytest.approx(60.0, rel=1e-9)
    assert e["M"] == pytest.approx(F * h, rel=1e-9)
    # tables: Pier Forces reports -P (tension positive) like walls
    from skyframe.core.tables import compute_table
    t = compute_table("pier_forces", res, m)
    row = [r for r in t["rows"] if r["pier"] == "P1" and r["case"] == "P"][0]
    assert row["P"] == pytest.approx(-60.0, rel=1e-9)


def _local2(m):
    from skyframe.engine.opensees_engine import _local_axes
    return _local_axes(m)[1]


def test_single_column_pier_equals_member_forces():
    """A cantilever column labelled C, F along its local 2 axis (the
    single-column pier axis): V = V2 = F, |M| = |M3| = F h, P = 0 -- the
    column's own base station forces; a load along local 3 gives no
    in-plane pier shear."""
    h, F = 4.0, 12.0
    m = _frame(h)
    c = m.add_member("column", "COL", (0, 0, 0), (0, 0, h), uid="C1")
    m.members[0].pier = "C"
    m.supports.append(PointSupport((0, 0, 0), FIX))
    y2 = _local2(c)
    m.pattern("P", "other").nodal_loads.append(
        NodalLoad((0, 0, h), fx=F * y2[0], fy=F * y2[1]))
    m.add_case("P", {"P": 1.0})
    res = _run(m)
    e = res["piers"]["P"]["C"][m.stories[0].name]
    assert e["V"] == pytest.approx(F, rel=1e-9)
    assert abs(e["M"]) == pytest.approx(F * h, rel=1e-9)
    assert e["P"] == pytest.approx(0.0, abs=1e-9)
    st = res["cases"]["P"]["member_stations"]["C1"]
    assert abs(st["V2"][0]) == pytest.approx(F, rel=1e-9)
    assert abs(st["M3"][0]) == pytest.approx(abs(e["M"]), rel=1e-9)


def test_wall_plus_boundary_column_pier():
    """Wall 0..6 plus a column at x = 8 tied by a beam at the top, all P1,
    lateral F at the top: global equilibrium of everything above the base
    cut gives V = F and M = F h about the WALL centroid (s = 3); P = 0."""
    h, F = 3.0, 100.0
    m = _frame(h)
    w = m.add_shell("wall", "shell", "W",
                    [(0, 0, 0), (6, 0, 0), (6, 0, h), (0, 0, h)],
                    mesh_size=1.0, uid="W1")
    w.pier = "P1"
    m.add_member("column", "COL", (8, 0, 0), (8, 0, h), uid="C1")
    m.add_member("beam", "BM", (6, 0, h), (8, 0, h), uid="B1")
    m.members[0].pier = "P1"
    m.pattern("P", "other").nodal_loads.append(NodalLoad((0, 0, h), fx=F))
    m.add_case("P", {"P": 1.0})
    res = _run(m)
    e = res["piers"]["P"]["P1"][m.stories[0].name]
    assert e["V"] == pytest.approx(F, rel=1e-6)
    assert e["M"] == pytest.approx(F * h, rel=1e-6)
    assert e["P"] == pytest.approx(0.0, abs=1e-6)


def test_multistory_frame_pier_and_combo():
    """Two-story cantilever column C1 (two members), F at the roof: story 2
    cut V = F, M = F h2; story 1 V = F, M = F (h1 + h2); an additive combo
    1.5 P scales linearly."""
    h = 3.0
    m = _frame(h, stories=2)
    m.add_member("column", "COL", (0, 0, 0), (0, 0, h), uid="C1")
    m.add_member("column", "COL", (0, 0, h), (0, 0, 2 * h), uid="C2")
    for mm in m.members:
        mm.pier = "PX"
    m.supports.append(PointSupport((0, 0, 0), FIX))
    y2 = _local2(m.members[0])
    m.pattern("P", "other").nodal_loads.append(
        NodalLoad((0, 0, 2 * h), fx=10.0 * y2[0], fy=10.0 * y2[1]))
    m.add_case("P", {"P": 1.0})
    m.add_combo("K", {"P": 1.5})
    res = _run(m)
    s1, s2 = m.stories[0].name, m.stories[1].name
    p = res["piers"]["P"]["PX"]
    assert abs(p[s2]["M"]) == pytest.approx(10.0 * h, rel=1e-9)
    assert abs(p[s1]["M"]) == pytest.approx(20.0 * h, rel=1e-9)
    assert res["piers"]["K"]["PX"][s1]["V"] == pytest.approx(
        1.5 * p[s1]["V"], rel=1e-9)


def test_frame_pier_json_and_validation():
    m = _frame()
    m.add_member("column", "COL", (0, 0, 0), (0, 0, 3), uid="C1")
    m.add_member("beam", "BM", (0, 0, 3), (6, 0, 3), uid="B1")
    d0 = m.to_dict()
    assert "pier" not in d0["members"][0] and "pier_labels" not in d0
    m.members[0].pier = "P1"
    m.pier_labels = ["P1", "P2"]
    d = m.to_dict()
    assert d["members"][0]["pier"] == "P1" and d["pier_labels"] == ["P1",
                                                                     "P2"]
    back = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert back.to_dict() == d
    m.members[1].pier = "P1"
    with pytest.raises(ValueError, match="columns"):
        m.validate()


def test_pier_assignment_table_identity_and_edit():
    from skyframe.core import modeltables as MT
    m = _frame()
    m.add_member("column", "COL", (0, 0, 0), (0, 0, 3), uid="C1")
    m.add_member("beam", "BM", (0, 0, 3), (6, 0, 3), uid="B1")
    m.add_shell("wall", "shell", "W",
                [(0, 0, 0), (6, 0, 0), (6, 0, 3), (0, 0, 3)], uid="W1")
    d = m.to_dict()
    t = MT.table("pier_assignments")
    rows = t.rows(d)
    assert [(r["object_type"], r["uid"]) for r in rows] == [
        ("Frame", "C1"), ("Shell", "W1")]           # beams excluded
    d2 = copy.deepcopy(d)
    errs = []
    t.apply(d2, rows, errs)
    assert not errs and d2 == d
    rows[0]["pier"] = "PA"
    rows[1]["pier"] = "PA"
    t.apply(d2, rows, errs)
    assert d2["members"][0]["pier"] == "PA"
    assert d2["shells"][0]["pier"] == "PA"
    rows[0]["pier"] = ""
    t.apply(d2, rows, errs)
    assert "pier" not in d2["members"][0]


def test_llr_pier_labels_include_frames():
    from skyframe.core.llr_codes import _pier_labels
    m = _frame()
    c = m.add_member("column", "COL", (0, 0, 0), (0, 0, 3), uid="C1")
    c.pier = "PF"
    c.story = m.stories[0].name
    assert _pier_labels(m, None) == {"PF": [m.stories[0].name]}


# --------------------------------------------------------------------------- #
# floor mesh "none"
# --------------------------------------------------------------------------- #
def _slab_model(corners, fm=None, mesh=1.0):
    m = _frame()
    r = m.add_shell("slab", "shell", "W", corners, mesh_size=mesh, uid="S1")
    r.floor_mesh = fm
    return m


def test_floor_mesh_none_rectangle_one_element():
    rect = [(0, 0, 3), (6, 0, 3), (6, 4, 3), (0, 4, 3)]
    m0 = _slab_model(rect)
    assert len(mesh_model(m0).quads) == 24
    m = _slab_model(rect, {"mode": "none"})
    m.validate()
    mesh = mesh_model(m)
    assert len(mesh.quads) == 1
    pts = sorted(tuple(mesh.points[n]) for n in mesh.quads[0].nodes)
    assert pts == sorted(rect)


def test_floor_mesh_none_polygon_coarsest():
    """L-shaped floor: the u/v lines through its own vertices only -> the
    3 cells of the L (no subdivision even with mesh_size 0.5)."""
    L = [(0, 0, 3), (6, 0, 3), (6, 3, 3), (3, 3, 3), (3, 6, 3), (0, 6, 3)]
    m = _slab_model(L, {"mode": "none"}, mesh=0.5)
    m.validate()
    assert len(mesh_model(m).quads) == 3
    m2 = _slab_model(L, None, mesh=0.5)
    assert len(mesh_model(m2).quads) > 3


def test_floor_mesh_none_solves_and_balances():
    """A 1-element slab on 4 corner supports under 5 kPa: the reactions sum
    to q A (consistent load, exact equilibrium)."""
    rect = [(0, 0, 3), (6, 0, 3), (6, 4, 3), (0, 4, 3)]
    m = _slab_model(rect, {"mode": "none"})
    for c in rect:
        m.supports.append(PointSupport(c, FIX))
    from skyframe.core.model import AreaLoad
    m.pattern("P", "other").area_loads.append(AreaLoad("S1", 5.0))
    m.add_case("P", {"P": 1.0})
    res = _run(m)
    assert res["cases"]["P"]["base"]["FZ"] == pytest.approx(5.0 * 24.0,
                                                            rel=1e-9)


@pytest.mark.parametrize("fm, extra, msg", [
    ({"mode": "none", "max_size": 1.0}, None, "excludes"),
    ({"mode": "none"}, {"n1": 2, "n2": 2}, "excludes"),
    ({"mode": "bogus"}, None, "floor_mesh.mode"),
])
def test_floor_mesh_none_validation(fm, extra, msg):
    m = _slab_model([(0, 0, 3), (6, 0, 3), (6, 4, 3), (0, 4, 3)], fm)
    m.shells[0].mesh_divisions = extra
    with pytest.raises(ValueError, match=msg):
        m.validate()


def test_shell_table_floor_mesh_and_edge_columns():
    from skyframe.core import modeltables as MT
    m = _slab_model([(0, 0, 3), (6, 0, 3), (6, 4, 3), (0, 4, 3)])
    d = m.to_dict()
    t = MT.table("shell_objects")
    rows = t.rows(d)
    assert rows[0]["floor_mesh"] == "" and rows[0]["mesh_at_beams"] is True
    assert rows[0]["edge_constraint"] == "model"
    d2 = copy.deepcopy(d)
    errs = []
    t.apply(d2, rows, errs)
    assert not errs and d2 == d                     # identity
    rows[0]["floor_mesh"] = "cookie_cut"
    rows[0]["mesh_at_grids"] = False
    rows[0]["edge_constraint"] = "no"
    t.apply(d2, rows, errs)
    assert not errs
    s = d2["shells"][0]
    assert s["floor_mesh"] == {"mode": "cookie_cut", "at_grids": False}
    assert s["edge_constraint"] is False
    back = BuildingModel.from_dict(d2)
    back.validate()
    assert back.shells[0].floor_mesh["mode"] == "cookie_cut"
    assert back.shells[0].edge_constraint is False
    rows = t.rows(d2)
    rows[0]["floor_mesh"] = ""
    rows[0]["edge_constraint"] = "model"
    t.apply(d2, rows, errs)
    assert "floor_mesh" not in d2["shells"][0]
    assert "edge_constraint" not in d2["shells"][0]


# --------------------------------------------------------------------------- #
# per-area auto edge constraints
# --------------------------------------------------------------------------- #
def _zipper_wall(model_flag):
    """test_wave23 two-region wall: WL meshed 1.5 m, WR 1.0 m."""
    m = BuildingModel(name="zip")
    m.rigid_diaphragms = False
    m.num_modes = 0
    m.add_material(Material("C", E, 0.2))
    m.add_shell_section(ShellSection("SH", "C", 0.2))
    m.set_stories([3.0])
    m.add_shell("wall", "shell", "SH",
                [(0, 0, 0), (2, 0, 0), (2, 0, 3), (0, 0, 3)],
                mesh_size=1.5, uid="WL")
    m.add_shell("wall", "shell", "SH",
                [(2, 0, 0), (4, 0, 0), (4, 0, 3), (2, 0, 3)],
                mesh_size=1.0, uid="WR")
    m.edge_constraints = model_flag
    m.pattern("P", "other").nodal_loads.append(NodalLoad((4, 0, 3),
                                                         fx=100.0))
    m.add_case("P", {"P": 1.0})
    return m


def _tip(eng, res):
    for t, c in eng._asm.struct_coords.items():
        if abs(c[0] - 4) < 1e-6 and abs(c[2] - 3) < 1e-6:
            return res.node_disp[t][0]
    raise AssertionError


@pytest.mark.parametrize("flag, ovr, n_ties, tip", [
    (False, {}, 0, 1.526269e-4),                    # legacy off
    (True, {}, 3, 1.218739e-4),                     # legacy on
    (False, {"WL": True}, 3, 1.218739e-4),          # per area on
    (False, {"WR": True}, 3, 1.218739e-4),
    (True, {"WL": False, "WR": False}, 0, 1.526269e-4),
    (True, {"WL": False}, 1, None),                 # only WR's own edge
])
def test_per_area_edge_constraints(flag, ovr, n_ties, tip):
    m = _zipper_wall(flag)
    for r in m.shells:
        if r.uid in ovr:
            r.edge_constraint = ovr[r.uid]
    m.validate()
    eng = OpenSeesEngine(m)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = eng.run_static("P")
    assert len(eng._asm.edge_ties) == n_ties
    if tip is not None:
        assert _tip(eng, res) == pytest.approx(tip, rel=1e-4)
    assert res.base["FX"] == pytest.approx(-100.0, rel=1e-9)
    if ovr == {"WL": False}:
        assert [e["region"] for e in eng._asm.edge_ties] == ["WR"]


def test_edge_constraint_json_and_validation():
    m = _zipper_wall(False)
    assert "edge_constraint" not in m.to_dict()["shells"][0]
    m.shells[0].edge_constraint = True
    d = m.to_dict()
    assert d["shells"][0]["edge_constraint"] is True
    assert BuildingModel.from_dict(d).to_dict() == d
    m.shells[0].edge_constraint = "yes"
    with pytest.raises(ValueError, match="edge_constraint"):
        m.validate()
    # the chain filter itself
    from skyframe.core.edge_constraints_ext import filter_chains
    m2 = _zipper_wall(True)
    mesh = mesh_model(m2)
    ch = edge_tie_chains(mesh)
    assert filter_chains(m2, mesh, ch) is ch        # no override: same list
