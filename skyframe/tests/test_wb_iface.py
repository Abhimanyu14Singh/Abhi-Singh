"""Interface & modelling productivity (Wave B): model templates, general
grids, workspace data (named views / report templates / table sets /
drawing aids), Edit Shells, ramps, wall stacks, Auto Relabel, .rtf reports.

Every value is hand-derived:

* template self weight: sum of A*L*gamma over frames + t*gamma*A*weight
  over shells + SDL*A equals the DEAD base reaction;
* waffle / ribbed equivalent-shell modifiers: the T-section closed form
  (D = 0.40, tf = 0.10, bw = 0.15, s = 0.90 -> f = 0.375, m = 0.3359375);
* drop panels: drop-panel area = (4 corner + 4 edge + 1 interior) panels
  = 256/9 m^2 for 2 x 2 bays of 8 m with dw = 8/3 m;
* general grids: line crossings by the exact parametric solve, mapped by
  the origin / rotation transform;
* shell edits: exact area conservation (divide / chop / merge), offset
  edges (A + L d, (a + 2d)(b + 2d)), divide -> merge byte-identical,
  and unchanged analysis totals (base FZ) after divide / relabel.
"""

import base64
import json
import math
import struct
import zlib

import pytest

from skyframe.core.builder import quick_building
from skyframe.core.edit import ALL_EDIT_OPS, apply_edit
from skyframe.core.edit_shells import (merge_polygons, ramp_corners,
                                       relabel_map, wall_stack_legs)
from skyframe.core.grid_general import segment_intersection
from skyframe.core.model import (AreaLoad, BuildingModel, GridSystem,
                                 ShellSection)
from skyframe.core.templates import (TEMPLATES, build_template, catalogue,
                                     drop_panel_lines, normalize_params,
                                     rib_properties, ribbed_modifiers,
                                     staggered_lines)
from skyframe.engine.opensees_engine import OpenSeesEngine
from skyframe.io.rtf import build_rtf, rtf_escape

GAMMA = 24.0


def _run(m):
    return OpenSeesEngine(m).run().to_dict()


def _area(pts):
    n = [0.0, 0.0, 0.0]
    for i, a in enumerate(pts):
        b = pts[(i + 1) % len(pts)]
        n[0] += (a[1] - b[1]) * (a[2] + b[2])
        n[1] += (a[2] - b[2]) * (a[0] + b[0])
        n[2] += (a[0] - b[0]) * (a[1] + b[1])
    return 0.5 * math.sqrt(sum(v * v for v in n))


def _frame_weight(m):
    tot = 0.0
    for mm in m.members:
        sec = m.sections[mm.section]
        mat = m.materials[sec.material]
        tot += sec.A * mm.length * mat.unit_weight
    return tot


# --------------------------------------------------------------------------- #
# templates
# --------------------------------------------------------------------------- #
def test_rib_closed_form():
    rp = rib_properties(0.40, 0.10, 0.15, 0.90)
    assert rp["A"] == pytest.approx(0.135, abs=1e-15)
    assert rp["ybar"] == pytest.approx(0.01575 / 0.135, rel=1e-14)
    assert rp["I"] == pytest.approx(0.0016125, rel=1e-12)
    w = ribbed_modifiers(0.40, 0.10, 0.15, 0.90, two_way=True)
    assert w["f11"] == w["f22"] == pytest.approx(0.375, rel=1e-14)
    assert w["m11"] == w["m22"] == pytest.approx(0.3359375, rel=1e-12)
    assert w["m12"] == pytest.approx(0.3359375, rel=1e-12)   # Huber
    assert w["f12"] == pytest.approx(0.25)
    assert w["v13"] == pytest.approx(0.15 / 0.9)
    assert w["weight"] == pytest.approx((0.1 + 0.07425 / 0.81) / 0.4,
                                        rel=1e-14)
    r = ribbed_modifiers(0.40, 0.10, 0.15, 0.90, two_way=False)
    assert r["m22"] == pytest.approx(0.25 ** 3)
    assert r["f22"] == pytest.approx(0.25)
    assert r["m12"] == pytest.approx(math.sqrt(0.3359375 * 0.25 ** 3))
    assert r["weight"] == pytest.approx((0.1 + 0.045 / 0.9) / 0.4)


def test_catalogue_and_params():
    cat = catalogue()
    assert [c["id"] for c in cat] == list(TEMPLATES)
    assert cat[3]["params"]["bays_y"] == 1          # staggered defaults
    with pytest.raises(ValueError):
        normalize_params("nope", {})
    with pytest.raises(ValueError):
        normalize_params("waffle", {"bogus": 1})
    with pytest.raises(ValueError):
        normalize_params("waffle", {"top": 0.5, "depth": 0.4})
    with pytest.raises(ValueError):
        normalize_params("ribbed", {"code": "ubc"})
    with pytest.raises(ValueError):
        normalize_params("two_way_beams", {"stories": 0})
    assert drop_panel_lines([0.0, 8.0, 16.0], 4.0 / 3.0) == pytest.approx(
        [0.0, 4 / 3, 8 - 4 / 3, 8.0, 8 + 4 / 3, 16 - 4 / 3, 16.0])
    assert staggered_lines(4, 3) == {0: [1], 1: [2], 2: [1], 3: [2]}


def test_two_way_dead_reaction_closed_form():
    m, s = build_template("two_way_beams", {"stories": 2, "bays_x": 1,
                                            "bays_y": 1})
    # columns 0.36*3.5*24 x 4 x 2 + beams 0.24*8*24 x 4 x 2
    # + slab 0.18*24*64 x 2 + SDL 1.5*64 x 2
    exp = 241.92 + 368.64 + 552.96 + 192.0
    r = _run(m)
    assert r["cases"]["DEAD"]["base"]["FZ"] == pytest.approx(exp, rel=1e-9)
    assert r["cases"]["LIVE"]["base"]["FZ"] == pytest.approx(2.5 * 64 * 2,
                                                             rel=1e-9)
    assert s["rs_cases"] == ["RSX", "RSY"]
    assert set(m.mass_source) == {"DEAD", "LIVE"}
    assert "1.2D+1.6L" in m.combos
    assert any("EQX" in c for c in m.combos)
    assert r["rs_cases"]["RSX"]["base"]["FX"] > 0


def test_waffle_weight_and_rs():
    m, _ = build_template("waffle", {"stories": 2, "bays_x": 1, "bays_y": 1})
    w = ribbed_modifiers(0.40, 0.10, 0.15, 0.90, True)["weight"]
    exp = _frame_weight(m) + 2 * (0.40 * GAMMA * 64 * w + 1.5 * 64)
    r = _run(m)
    assert r["cases"]["DEAD"]["base"]["FZ"] == pytest.approx(exp, rel=1e-9)
    assert r["rs_cases"]["RSY"]["base"]["FY"] > 0
    ss = m.shell_sections["WAFFLE"]
    assert ss.m11 == pytest.approx(0.3359375, rel=1e-12)


def test_flat_slab_drop_tessellation():
    m, _ = build_template("flat_slab_drop", {"stories": 1, "bays_x": 2,
                                             "bays_y": 2})
    drop = sum(s.area for s in m.shells if s.section == "DROP")
    tot = sum(s.area for s in m.shells)
    assert drop == pytest.approx(256.0 / 9.0, rel=1e-9)
    assert tot == pytest.approx(256.0, rel=1e-12)
    assert len(m.shells) == 36
    exp = (_frame_weight(m) + GAMMA * (0.25 * 256 + 0.15 * 256 / 9)
           + 1.5 * 256)
    r = _run(m)
    assert r["cases"]["DEAD"]["base"]["FZ"] == pytest.approx(exp, rel=1e-9)


def test_staggered_truss_and_ribbed_run():
    m, s = build_template("staggered_truss", {"stories": 3, "bays_x": 3})
    trusses = {mm.uid for mm in m.members if mm.uid.startswith("TD")}
    assert trusses and all(mm.kind == "brace" for mm in m.members
                           if mm.uid in trusses)
    cols = [mm for mm in m.members if mm.kind == "column"
            and not mm.uid.startswith("TV")]
    assert {round(mm.pi[1], 6) for mm in cols} == {0.0, 18.0}
    r = _run(m)
    # floors are one-way membranes: total DEAD = frames + SDL + deck
    exp = _frame_weight(m) + 3 * (0.15 * GAMMA + 1.5) * 18.0 * 18.0
    assert r["cases"]["DEAD"]["base"]["FZ"] == pytest.approx(exp, rel=1e-9)
    m2, s2 = build_template("ribbed", {"stories": 1, "bays_x": 1,
                                       "bays_y": 1, "code": "ec8"})
    assert any(c.startswith("EN-") for c in m2.combos)
    r2 = _run(m2)
    assert r2["cases"]["EQX"]["base"]["FX"] == pytest.approx(
        -sum(f.fx for f in m2.patterns["EQX"].story_forces), rel=1e-9)


def test_template_round_trip_and_is1893():
    m, _ = build_template("waffle", {"stories": 1, "bays_x": 1,
                                     "bays_y": 1, "code": "is1893"})
    d = m.to_dict()
    assert json.dumps(BuildingModel.from_dict(d).to_dict()) == json.dumps(d)
    assert "RSF" in m.spectrum_functions
    m3, _ = build_template("two_way_beams", {"code": "none", "stories": 1})
    assert set(m3.cases) == {"DEAD", "LIVE"} and not m3.rs_cases


# --------------------------------------------------------------------------- #
# general grids
# --------------------------------------------------------------------------- #
def _general():
    return GridSystem(name="GG", kind="general", origin=(2.0, 1.0),
                      rotation=90.0,
                      lines=[{"label": "A", "p1": [0, 0], "p2": [10, 0]},
                             {"label": "B", "p1": [0, -2], "p2": [10, 8]},
                             {"label": "C", "p1": [4, -5], "p2": [4, 5]}])


def test_general_grid_geometry():
    g = _general()
    t, u, p = segment_intersection([0, 0], [10, 0], [0, -2], [10, 8])
    assert (t, u) == pytest.approx((0.2, 0.2)) and p == pytest.approx((2, 0))
    assert segment_intersection([0, 0], [1, 0], [0, 1], [1, 1]) is None
    lines = g.lines_global()
    # local (lx, ly) -> (2 - ly, 1 + lx)
    assert lines[0]["points"][0] == pytest.approx([2, 1])
    assert lines[0]["points"][1] == pytest.approx([2, 11])
    its = {i["label"]: i["point"] for i in g.intersections_global()}
    assert its["A-B"] == pytest.approx([2, 3])          # local (2, 0)
    assert its["A-C"] == pytest.approx([2, 5])          # local (4, 0)
    assert its["B-C"] == pytest.approx([0, 5])          # local (4, 2)
    assert its["C.1"] == pytest.approx([7, 5])          # local (4, -5)
    m = quick_building(bays_x=1, bays_y=1, stories=1)
    m.set_grid_system(g)
    snap = m.snap(0.1, 5.05, 0.2)
    assert snap["label"] == "B-C"
    d = m.to_dict()
    assert d["grid_systems"][1]["lines"][1] == {"label": "B", "p1": [0, -2],
                                                "p2": [10, 8]}
    assert "lines" not in d["grid_systems"][0]
    assert json.dumps(BuildingModel.from_dict(d).to_dict()) == json.dumps(d)


def test_general_grid_validation_and_legacy_bytes():
    m = quick_building(bays_x=1, bays_y=1, stories=1)
    with pytest.raises(ValueError):
        m.set_grid_system(GridSystem(name="X", kind="general"))
    with pytest.raises(ValueError):
        m.set_grid_system(GridSystem(name="X", kind="general", lines=[
            {"label": "A", "p1": [0, 0], "p2": [0, 0]}]))
    gd = quick_building().to_dict()["grid"]
    assert set(gd) == {"name", "kind", "origin", "rotation", "x_lines",
                       "y_lines", "x_labels", "y_labels", "radii",
                       "theta_deg"}


# --------------------------------------------------------------------------- #
# workspace data
# --------------------------------------------------------------------------- #
WS = {"named_views": [{"name": "Plan L2", "view": "plan", "story": "Story2",
                       "camera": {"cx": 6, "cy": 3, "scale": 40},
                       "display": {"extrude": True}},
                      {"name": "Iso", "view": "3d",
                       "camera": {"yaw": 0.7, "pitch": 0.4, "dist": 30,
                                  "target": [6, 3, 4]}}],
      "report_templates": [{"name": "Gravity", "tables": ["story_drifts"],
                            "figures": ["deformed"], "cases": ["DEAD"],
                            "options": {"paper": "A4"}}],
      "table_sets": [{"name": "Drift", "tables": ["story_drifts"]}],
      "dimension_lines": [{"p1": [0, 0, 3.2], "p2": [6, 0, 3.2],
                           "offset": 1.0, "story": "Story1"}],
      "reference_points": [{"name": "RP1", "point": [3, 3, 0]}],
      "reference_planes": [{"name": "Mezz", "axis": "z", "coord": 1.6}]}


def test_workspace_round_trip_and_no_analysis_effect():
    m = quick_building(bays_x=1, bays_y=1, stories=2)
    base = m.to_dict()
    assert not any(k in base for k in WS)
    d = dict(base, **json.loads(json.dumps(WS)))
    m2 = BuildingModel.from_dict(d)
    d2 = m2.to_dict()
    for k in WS:
        assert d2[k] == json.loads(json.dumps(WS))[k] or k in (
            "named_views", "dimension_lines", "reference_points",
            "reference_planes")
    assert d2["named_views"][0]["camera"] == {"cx": 6.0, "cy": 3.0,
                                              "scale": 40.0}
    assert json.dumps(BuildingModel.from_dict(d2).to_dict()) == json.dumps(d2)
    r1, r2 = _run(m), _run(m2)
    assert r1["cases"]["DEAD"]["base"] == r2["cases"]["DEAD"]["base"]


@pytest.mark.parametrize("key,bad", [
    ("named_views", [{"name": "A", "view": "top"}]),
    ("named_views", [{"name": "A", "view": "3d"}, {"name": "A",
                                                   "view": "plan"}]),
    ("table_sets", [{"name": "", "tables": []}]),
    ("table_sets", [{"name": "T", "tables": "x"}]),
    ("dimension_lines", [{"p1": [0, 0, 0], "p2": [0, 0, 0]}]),
    ("reference_planes", [{"name": "P", "axis": "w", "coord": 1}]),
    ("reference_points", [{"name": "P", "point": [0, 0]}]),
    ("report_templates", "nope")])
def test_workspace_validation(key, bad):
    d = quick_building(bays_x=1, bays_y=1, stories=1).to_dict()
    d[key] = bad
    with pytest.raises(ValueError):
        BuildingModel.from_dict(d)


# --------------------------------------------------------------------------- #
# shell edits
# --------------------------------------------------------------------------- #
def _slab_model(corners=None, q=2.0):
    m = quick_building(bays_x=1, bays_y=1, stories=1, bay_width_x=6.0,
                       bay_width_y=4.0)
    m.add_shell_section(ShellSection("SLAB", "CONC", 0.2))
    z = m.stories[0].elevation
    c = corners or [(0, 0, z), (6, 0, z), (6, 4, z), (0, 4, z)]
    m.add_shell("slab", "shell", "SLAB", c, mesh_size=1.0,
                story=m.stories[0].name, uid="S1")
    m.patterns["DEAD"].area_loads.append(AreaLoad("S1", q))
    m.add_group("G", shells=["S1"])
    return m


def _sel(**kw):
    out = {"members": [], "shells": [], "links": [], "points": []}
    out.update(kw)
    return out


def test_ops_registered():
    assert {"shell_divide", "shell_merge", "shell_chop", "shell_expand",
            "shell_reshape", "ramp", "wall_stack",
            "relabel"} <= set(ALL_EDIT_OPS)


def test_divide_quad_exact_and_loads():
    m = _slab_model()
    d, sm = apply_edit(m.to_dict(), "shell_divide", _sel(shells=["S1"]),
                       {"mode": "n", "n1": 3, "n2": 2})
    pieces = sm["divided"]["S1"]
    assert len(pieces) == 6 and pieces[0] == "S1"
    sh = {s["uid"]: s for s in d["shells"]}
    assert sh["S1"]["corners"] == [[0, 0, 3.2], [2, 0, 3.2], [2, 2, 3.2],
                                   [0, 2, 3.2]]
    assert sum(_area(sh[u]["corners"]) for u in pieces) == pytest.approx(
        24.0, abs=1e-12)
    loads = [a for a in d["patterns"]["DEAD"]["area_loads"]]
    assert sorted(a["region_uid"] for a in loads) == sorted(pieces)
    assert d["groups"]["G"]["shells"] == pieces
    m2 = BuildingModel.from_dict(d)
    r1, r2 = _run(m), _run(m2)
    assert r2["cases"]["DEAD"]["base"]["FZ"] == pytest.approx(
        r1["cases"]["DEAD"]["base"]["FZ"], rel=1e-10)


def test_divide_then_merge_byte_identical():
    d0 = _slab_model().to_dict()
    d1, sm = apply_edit(d0, "shell_divide", _sel(shells=["S1"]),
                        {"mode": "size", "max_size": 2.0})
    pieces = sm["divided"]["S1"]
    assert len(pieces) == 6
    d2, sm2 = apply_edit(d1, "shell_merge", _sel(shells=pieces), {})
    assert json.dumps(d2) == json.dumps(d0)


def test_divide_trapezoid_and_polygon_conserve_area():
    z = 3.2
    m = _slab_model([(0, 0, z), (6, 0, z), (4, 4, z), (0, 4, z)])
    d, sm = apply_edit(m.to_dict(), "shell_divide", _sel(shells=["S1"]),
                       {"mode": "n", "n1": 2, "n2": 2})
    sh = {s["uid"]: s for s in d["shells"]}
    assert sum(_area(sh[u]["corners"]) for u in sm["divided"]["S1"]) == \
        pytest.approx(20.0, abs=1e-12)
    pent = [(0, 0, z), (6, 0, z), (6, 3, z), (3, 4, z), (0, 3, z)]
    m = _slab_model(pent)
    d, sm = apply_edit(m.to_dict(), "shell_divide", _sel(shells=["S1"]),
                       {"mode": "n", "n1": 2, "n2": 2})
    sh = {s["uid"]: s for s in d["shells"]}
    assert len(sm["divided"]["S1"]) == 4
    assert sum(_area(sh[u]["corners"]) for u in sm["divided"]["S1"]) == \
        pytest.approx(_area(pent), abs=1e-12)
    BuildingModel.from_dict(d)


def test_merge_t_junction_and_l_shape():
    A = [[0, 0, 0], [4, 0, 0], [4, 4, 0], [0, 4, 0]]
    B = [[4, 0, 0], [6, 0, 0], [6, 2, 0], [4, 2, 0]]
    C = [[4, 2, 0], [6, 2, 0], [6, 4, 0], [4, 4, 0]]
    assert merge_polygons([A, B, C]) == [[0, 0, 0], [6, 0, 0], [6, 4, 0],
                                         [0, 4, 0]]
    L1 = [[0, 0, 0], [4, 0, 0], [4, 2, 0], [0, 2, 0]]
    L2 = [[0, 2, 0], [2, 2, 0], [2, 4, 0], [0, 4, 0]]
    mg = merge_polygons([L1, L2])
    assert len(mg) == 6 and _area(mg) == pytest.approx(12.0)
    with pytest.raises(ValueError):
        merge_polygons([A, [[10, 0, 0], [12, 0, 0], [12, 2, 0],
                            [10, 2, 0]]])
    ring = []
    for i in range(3):
        for j in range(3):
            if (i, j) != (1, 1):
                ring.append([[i, j, 0], [i + 1, j, 0], [i + 1, j + 1, 0],
                             [i, j + 1, 0]])
    with pytest.raises(ValueError):
        merge_polygons(ring)


def test_merge_rejects_mismatch():
    m = _slab_model()
    m.add_shell_section(ShellSection("SLAB2", "CONC", 0.3))
    z = m.stories[0].elevation
    m.add_shell("slab", "shell", "SLAB2", [(6, 0, z), (8, 0, z), (8, 4, z),
                                           (6, 4, z)], uid="S2",
                story=m.stories[0].name)
    with pytest.raises(ValueError, match="section"):
        apply_edit(m.to_dict(), "shell_merge", _sel(shells=["S1", "S2"]), {})
    m.shells[-1].section = "SLAB"
    with pytest.raises(ValueError, match="area loads"):
        apply_edit(m.to_dict(), "shell_merge", _sel(shells=["S1", "S2"]), {})
    m.patterns["DEAD"].area_loads.append(AreaLoad("S2", 2.0))
    d, sm = apply_edit(m.to_dict(), "shell_merge", _sel(shells=["S1", "S2"]),
                       {})
    s1 = [s for s in d["shells"] if s["uid"] == "S1"][0]
    assert _area(s1["corners"]) == pytest.approx(32.0)
    assert [a["region_uid"] for a in d["patterns"]["DEAD"]["area_loads"]] \
        == ["S1"]


def test_chop_grid_lines_levels():
    m = quick_building(bays_x=2, bays_y=1, stories=2, bay_width_x=8.0,
                       bay_width_y=8.0)
    m.add_shell_section(ShellSection("SLAB", "CONC", 0.2))
    z = m.stories[0].elevation
    m.add_shell("slab", "shell", "SLAB", [(0, 0, z), (16, 0, z), (16, 8, z),
                                          (0, 8, z)], uid="S1",
                story=m.stories[0].name)
    zt = m.stories[1].elevation
    m.add_shell("wall", "shell", "SLAB", [(0, 0, 0), (8, 0, 0), (8, 0, zt),
                                          (0, 0, zt)], uid="W1",
                story=m.stories[1].name)
    d, sm = apply_edit(m.to_dict(), "shell_chop", _sel(shells=["S1"]),
                       {"mode": "grid"})
    sh = {s["uid"]: s for s in d["shells"]}
    assert len(sm["chopped"]["S1"]) == 2
    assert [_area(sh[u]["corners"]) for u in sm["chopped"]["S1"]] == \
        pytest.approx([64.0, 64.0])
    d, sm = apply_edit(m.to_dict(), "shell_chop", _sel(shells=["S1"]),
                       {"mode": "lines", "lines": [[[0, 0], [16, 8]]]})
    sh = {s["uid"]: s for s in d["shells"]}
    assert sorted(len(sh[u]["corners"]) for u in sm["chopped"]["S1"]) == [3,
                                                                          3]
    assert sum(_area(sh[u]["corners"]) for u in sm["chopped"]["S1"]) == \
        pytest.approx(128.0, abs=1e-12)
    d, sm = apply_edit(m.to_dict(), "shell_chop", _sel(shells=["W1"]),
                       {"mode": "levels"})
    sh = {s["uid"]: s for s in d["shells"]}
    zs = sorted(max(c[2] for c in sh[u]["corners"])
                for u in sm["chopped"]["W1"])
    assert zs == pytest.approx([z, zt])
    assert {sh[u]["story"] for u in sm["chopped"]["W1"]} == {"Story1",
                                                             "Story2"}
    BuildingModel.from_dict(d)


def test_expand_shrink_reshape():
    d0 = _slab_model().to_dict()
    d, sm = apply_edit(d0, "shell_expand", _sel(shells=["S1"]),
                       {"distance": 1.0, "edge": 1})
    c = [s for s in d["shells"] if s["uid"] == "S1"][0]["corners"]
    assert c == [[0, 0, 3.2], [7, 0, 3.2], [7, 4, 3.2], [0, 4, 3.2]]
    assert sm["expanded"]["S1"]["area_after"] == pytest.approx(24 + 4)
    d, sm = apply_edit(d0, "shell_expand", _sel(shells=["S1"]),
                       {"distance": 0.5})
    assert sm["expanded"]["S1"]["area_after"] == pytest.approx(7 * 5)
    d, sm = apply_edit(d0, "shell_expand", _sel(shells=["S1"]),
                       {"distance": -1.0})
    assert sm["expanded"]["S1"]["area_after"] == pytest.approx(4 * 2)
    with pytest.raises(ValueError):
        apply_edit(d0, "shell_expand", _sel(shells=["S1"]),
                   {"distance": -2.5})
    d, sm = apply_edit(d0, "shell_reshape", _sel(shells=["S1"]),
                       {"mode": "move", "index": 2, "to": [7, 5, 3.2]})
    assert sm["area"] == pytest.approx(_area([[0, 0, 0], [6, 0, 0],
                                              [7, 5, 0], [0, 4, 0]]))
    d, sm = apply_edit(d0, "shell_reshape", _sel(shells=["S1"]),
                       {"mode": "insert", "edge": 0})
    c = [s for s in d["shells"] if s["uid"] == "S1"][0]["corners"]
    assert c[1] == [3, 0, 3.2] and len(c) == 5
    d, _ = apply_edit(d, "shell_reshape", _sel(shells=["S1"]),
                      {"mode": "delete", "index": 1})
    assert json.dumps(d) == json.dumps(d0)
    with pytest.raises(ValueError):
        apply_edit(d0, "shell_reshape", _sel(shells=["S1"]),
                   {"mode": "corners", "corners": [[0, 0, 0], [1, 0, 0]]})


def test_ramp_story_and_diaphragm():
    m = quick_building(bays_x=1, bays_y=1, stories=2)
    m.add_shell_section(ShellSection("SLAB", "CONC", 0.2))
    assert ramp_corners([1, 2], [5, 2], 2.0, 3.2, 6.4) == [
        [1, 1, 3.2], [5, 1, 6.4], [5, 3, 6.4], [1, 3, 3.2]]
    d, sm = apply_edit(m.to_dict(), "ramp", _sel(),
                       {"p1": [1, 2], "p2": [5, 2], "width": 2.0,
                        "story_from": "Story1", "story_to": "Story2"})
    assert sm["slope"] == pytest.approx(0.8)
    assert sm["area"] == pytest.approx(2.0 * math.hypot(4.0, 3.2))
    rp = [s for s in d["shells"] if s["uid"] == "RAMP1"][0]
    assert rp["story"] == "Story2"
    m2 = BuildingModel.from_dict(d)
    e = OpenSeesEngine(m2)
    e.run()
    asm = e._asm
    coords = asm.node_coords
    lvl = {s: sorted(round(coords[t][2], 6) for t in tags)
           for s, tags in asm.story_nodes.items()}
    assert set(lvl["Story1"]) == {3.2} and len(lvl["Story1"]) == 4 + 3
    assert set(lvl["Story2"]) == {6.4} and len(lvl["Story2"]) == 4 + 3
    inner = [t for t, c in coords.items() if 3.2 + 1e-6 < c[2] < 6.4 - 1e-6]
    assert inner and not any(t in asm.story_nodes["Story1"] + asm.story_nodes[
        "Story2"] for t in inner)
    with pytest.raises(ValueError):
        apply_edit(m.to_dict(), "ramp", _sel(),
                   {"p1": [1, 2], "p2": [5, 2], "width": 2.0,
                    "story_from": "Story1", "story_to": "Story1"})


def test_wall_stack_c_shape_piers():
    m = quick_building(bays_x=1, bays_y=1, stories=2)
    m.add_shell_section(ShellSection("WALL", "CONC", 0.25))
    assert wall_stack_legs("I", 4, 3) == [
        ((-2, 0), (0, 0)), ((0, 0), (2, 0)), ((0, 0), (0, 3)),
        ((-2, 3), (0, 3)), ((0, 3), (2, 3))]
    d, sm = apply_edit(m.to_dict(), "wall_stack", _sel(),
                       {"shape": "C", "B": 3.0, "D": 2.0,
                        "origin": [1.0, 1.0], "rotation": 0.0,
                        "stories": ["Story1", "Story2"]})
    assert sm["walls"] == 6 and sm["pier"] == "P1"
    walls = [s for s in d["shells"] if s["kind"] == "wall"]
    assert {w["pier"] for w in walls} == {"P1"}
    assert walls[1]["corners"] == [[1, 3, 0], [1, 1, 0], [1, 1, 3.2],
                                   [1, 3, 3.2]]
    d2, sm2 = apply_edit(d, "wall_stack", _sel(),
                         {"shape": "L", "B": 2.0, "D": 2.0,
                          "origin": [4.0, 4.0], "rotation": 90.0,
                          "stories": ["Story1"]})
    assert sm2["pier"] == "P2"
    w = [s for s in d2["shells"] if s["pier"] == "P2"]
    assert w[1]["corners"][1] == pytest.approx([4, 6, 0])
    r = _run(BuildingModel.from_dict(d))
    assert "P1" in json.dumps(r["piers"])


def test_relabel_story_labels_and_results():
    m = quick_building(bays_x=2, bays_y=1, stories=2)
    mp = relabel_map(m.to_dict())
    assert mp[("members", "C1-A1")] == "C1-Story1"
    d, sm = apply_edit(m.to_dict(), "relabel", _sel(), {})
    uids = {mm["uid"] for mm in d["members"]}
    assert {"C1-Story1", "C6-Story2", "B1-Story1", "B7-Story2"} <= uids
    beam = [mm for mm in d["members"] if mm["uid"] == "B3-Story1"][0]
    assert (beam["pi"][0], beam["pi"][1]) == (0.0, 0.0)
    assert (beam["pj"][0], beam["pj"][1]) == (0.0, 6.0)
    loads = {u["member_uid"] for u in d["patterns"]["DEAD"]["member_udls"]}
    assert loads <= uids
    m2 = BuildingModel.from_dict(d)
    r1, r2 = _run(m), _run(m2)
    for case in ("DEAD", "EQX"):
        assert r2["cases"][case]["base"] == pytest.approx(
            r1["cases"][case]["base"], abs=1e-9)
    d3, sm3 = apply_edit(d, "relabel", _sel(), {})
    assert sm3["count"] == 0 and json.dumps(d3) == json.dumps(d)


# --------------------------------------------------------------------------- #
# .rtf
# --------------------------------------------------------------------------- #
def _png(w=2, h=3):
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    def chunk(t, data):
        return (struct.pack(">I", len(data)) + t + data
                + struct.pack(">I", zlib.crc32(t + data) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(
        ">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b""))


def test_rtf_writer():
    assert rtf_escape("a{b}\\c é") == "a\\{b\\}\\\\c \\u233?"
    assert rtf_escape("😀") == "\\u-10179?\\u-8704?"
    png = _png()
    doc = {"title": "Tower — Report", "subtitle": "s", "meta": [["Units",
                                                              "kN-m"]],
           "blocks": [{"type": "heading", "text": "1. Model", "level": 1},
                      {"type": "paragraph", "text": "note", "style": "note"},
                      {"type": "table", "columns": ["Story", "P"],
                       "rows": [["L1", 1.5], ["L2", None]],
                       "caption": "Forces"},
                      {"type": "image", "png_b64":
                       base64.b64encode(png).decode(), "width_px": 200},
                      {"type": "page_break"}]}
    out = build_rtf(doc).decode("ascii")
    assert out.startswith("{\\rtf1\\ansi") and out.endswith("}")
    assert "Tower \\u8212? Report" in out
    assert "\\trowd" in out and "\\trhdr" in out and "\\qr 1.5\\cell" in out
    assert "\\pngblip\\picw2\\pich3\\picwgoal3000\\pichgoal4500" in out
    assert png.hex()[:16] in out
    assert "\\s1\\outlinelevel0" in out and "TOC" in out and "\\page" in out
    for bad in ({"paper": "A3"}, {"blocks": [{"type": "video"}]},
                {"blocks": [{"type": "image", "png_b64": "AAAA"}]},
                {"blocks": [{"type": "heading", "level": 7}]}):
        with pytest.raises(ValueError):
            build_rtf(bad)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path))
    from skyframe.api.server import create_app
    app = create_app()
    app.testing = True
    return app.test_client()


def test_http_endpoints(client):
    cat = client.get("/api/model/templates").get_json()
    assert [c["id"] for c in cat] == list(TEMPLATES)
    r = client.post("/api/model/template", json={
        "template": "two_way_beams", "params": {"stories": 1, "bays_x": 1,
                                                "bays_y": 1}})
    assert r.status_code == 200
    body = r.get_json()
    assert body["summary"]["template"] == "two_way_beams"
    assert body["model"]["frame_axis_convention"] == "etabs"
    assert client.get("/api/model").get_json()["name"] == \
        "Two-Way Slab with Beams"
    assert client.post("/api/model/template", json={
        "template": "x"}).status_code == 400
    r = client.post("/api/edit/shell_divide", json={
        "selection": {"shells": ["F1-A1"]},
        "params": {"mode": "n", "n1": 2, "n2": 2}})
    assert r.status_code == 200 and len(
        r.get_json()["summary"]["divided"]["F1-A1"]) == 4
    r = client.post("/api/grid", json={
        "name": "GG", "kind": "general",
        "lines": [{"label": "A", "p1": [0, 0], "p2": [5, 5]}]})
    assert r.status_code == 200
    assert r.get_json()["grid_systems"][-1]["lines"][0]["p2"] == [5.0, 5.0]
    assert client.post("/api/grid", json={"name": "GG", "kind": "general",
                                          "lines": []}).status_code == 400
    r = client.post("/api/report/rtf", json={"title": "T", "blocks": []})
    assert r.status_code == 200 and r.get_json()["mime"] == "application/rtf"
    rtf = base64.b64decode(r.get_json()["content_b64"])
    assert rtf.startswith(b"{\\rtf1")
    assert client.post("/api/report/rtf", json={
        "blocks": [{"type": "x"}]}).status_code == 400
