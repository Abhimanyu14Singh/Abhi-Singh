"""Wave B loose ends (CONTRACT "Irregularity checks, grouped ASCE 7
combinations, E2K releases / joint axes, joint-local reactions, RS shell
forces in combos, TS 498 / KDS / CFE wind, NTC long-period distribution").

Every expected number is worked by hand in the test docstring / comments.
"""
import math
import warnings

import numpy as np
import pytest

from skyframe.core import autolateral as al
from skyframe.core import irregularity_ext as irr
from skyframe.core import joint_local_results as jlr
from skyframe.core import tables
from skyframe.core.codes import asce7_combinations
from skyframe.core.combos_asce7 import asce7_grouped, grouped_applies
from skyframe.core.model import (BuildingModel, FrameSection, LoadPattern,
                                 Material, MemberLoad, NodalLoad, NodalMass,
                                 Opening,
                                 PointSupport, ShellSection)
from skyframe.engine.opensees_engine import OpenSeesEngine
from skyframe.io.e2k_read import read_e2k
from skyframe.io.e2k_write import export_e2k
from test_loads_v116 import _bldg


@pytest.fixture(autouse=True)
def _quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def _rows(rep, code=None, item=None, story=None, case=None, direction=None):
    out = []
    for r in rep["checks"]:
        if code and r["code"] != code:
            continue
        if item and r["item"] != item:
            continue
        if story is not None and r["story"] != story:
            continue
        if case is not None and r["case"] != case:
            continue
        if direction is not None and r["direction"] != direction:
            continue
        out.append(r)
    return out


def _one(rep, **kw):
    rr = _rows(rep, **kw)
    assert len(rr) == 1, rr
    return rr[0]


# =========================================================================
# 1. irregularity checks
# =========================================================================
def _frame(xs_by_story, ys=(0.0, 6.0), heights=(3.0, 3.0, 3.0),
           fc=30000.0):
    """Concrete column frame; story i has columns at xs_by_story[i] x ys
    (0.5 x 0.5 m columns, f'c = 30 MPa)."""
    m = BuildingModel("irr")
    m.add_material(Material("C", 25e6, 0.2, 24.0, fc=fc))
    m.add_section(FrameSection.rectangular("COL", "C", 0.5, 0.5))
    names = [f"S{i + 1}" for i in range(len(heights))]
    m.set_stories(list(heights), names=names)
    z = 0.0
    for i, s in enumerate(m.stories):
        for x in xs_by_story[i]:
            for y in ys:
                m.add_member("column", "COL", (x, y, z), (x, y, s.elevation),
                             story=s.name)
        z = s.elevation
    return m


def test_weight_irregularity_asce_is_ec8():
    """Masses 100 / 200 / 100 t: S2/S1 = 2.0 > 1.5 (ASCE V-2, IS 6(ii));
    S3 is the roof and lighter than S2 -> S2 vs the roof not considered,
    S1/S2 = 0.5.  EC8 4.2.3.3(3) mass upper/lower: S2 2.0 > 1.25
    irregular; S3 0.5 < 0.75 but roof -> ok."""
    m = _frame([[0, 10]] * 3)
    m.story_masses = {"S1": 100.0, "S2": 200.0, "S3": 100.0}
    rep = irr.compute(m)
    r = _one(rep, code="ASCE 7", item="Table 12.3-2 Type 2", story="S2")
    assert r["value"] == pytest.approx(2.0) and r["status"] == "irregular"
    r = _one(rep, code="ASCE 7", item="Table 12.3-2 Type 2", story="S1")
    assert r["value"] == pytest.approx(0.5) and r["status"] == "ok"
    r = _one(rep, code="ASCE 7", item="Table 12.3-2 Type 2", story="S3")
    assert r["value"] == pytest.approx(0.5) and r["status"] == "ok"
    r = _one(rep, code="IS 1893", item="Table 6 (ii)", story="S2")
    assert r["value"] == pytest.approx(2.0) and r["status"] == "irregular"
    r = _one(rep, code="EN 1998-1", item="4.2.3.3(3)", story="S3")
    assert r["value"] == pytest.approx(0.5) and r["status"] == "ok"
    assert rep["summary"]["ASCE 7"]["Table 12.3-2 Type 2"] == "irregular"
    # 140 / 100: 1.4 <= 1.5 -> ok (ASCE), but > 1.25 for EC8
    m.story_masses = {"S1": 100.0, "S2": 140.0, "S3": 100.0}
    rep = irr.compute(m)
    assert _one(rep, code="ASCE 7", item="Table 12.3-2 Type 2",
                story="S2")["status"] == "ok"
    assert _one(rep, code="EN 1998-1", item="4.2.3.3(3)",
                story="S2")["status"] == "irregular"


def test_zero_mass_ratio_is_gated():
    m = _frame([[0, 10]] * 3)
    m.story_masses = {"S1": 0.0, "S2": 50.0, "S3": 50.0}
    rep = irr.compute(m)
    r = _one(rep, code="IS 1893", item="Table 6 (ii)", story="S2")
    assert r["value"] is None and r["status"] == "n/a"
    assert r["note"] == irr.NA_ZERO


def test_vertical_geometric_and_setbacks():
    """Columns span x = 0..20 in S1, S2 and 0..10 in S3 (unsymmetric
    setback).  D(S2)/D(S3) = 2.0 > 1.3 (ASCE V-3) and > 1.25 (IS 6(iii));
    Y unchanged (1.0).  EC8 Fig. 4.1: unsymmetric step 0.5 > 0.10 ->
    irregular; total (20 - 10)/20 = 0.5."""
    m = _frame([[0, 10, 20], [0, 10, 20], [0, 10]])
    m.story_masses = {s.name: 50.0 for s in m.stories}
    rep = irr.compute(m)
    for code, item in (("ASCE 7", "Table 12.3-2 Type 3"),
                       ("IS 1893", "Table 6 (iii)")):
        r = _one(rep, code=code, item=item, story="S2", direction="X")
        assert r["value"] == pytest.approx(2.0)
        assert r["status"] == "irregular"
        assert _one(rep, code=code, item=item, story="S2",
                    direction="Y")["value"] == pytest.approx(1.0)
    r = _one(rep, code="EN 1998-1", item="4.2.3.3(5)", direction="X")
    assert r["value"] == pytest.approx(0.5) and r["status"] == "irregular"
    # symmetric setback 0..20 -> 4..16 (each side 4 m): 0.4 > 0.20
    m2 = _frame([[0, 10, 20], [0, 10, 20], [4, 10, 16]])
    m2.story_masses = m.story_masses
    r = _one(irr.compute(m2), code="EN 1998-1", item="4.2.3.3(5)",
             direction="X")
    assert r["value"] == pytest.approx(0.4) and r["status"] == "irregular"
    assert "sym" in r["note"]
    # a 10 % symmetric setback is regular (0.1 <= 0.2)
    m3 = _frame([[0, 10, 20], [0, 10, 20], [1, 10, 19]])
    m3.story_masses = m.story_masses
    assert _one(irr.compute(m3), code="EN 1998-1", item="4.2.3.3(5)",
                direction="X")["status"] == "ok"


def test_in_plane_discontinuity_and_floating_column():
    """S2 column line x = 0, 10; S3 adds a column at x = 5 standing on the
    S2 beam (same frame lines y = 0 / 6): in-plane offset 5 m from the
    nearest column below.  A column at (5, 3) is off every frame line ->
    out-of-plane."""
    m = _frame([[0, 10], [0, 10], [0, 5, 10]])
    m.story_masses = {s.name: 50.0 for s in m.stories}
    rep = irr.compute(m)
    disc = rep["discontinuities"]
    assert len(disc) == 2
    assert {d["kind"] for d in disc} == {"in_plane"}
    assert all(d["offset"] == pytest.approx(5.0) for d in disc)
    r = _one(rep, code="ASCE 7", item="Table 12.3-2 Type 4", story="S3")
    assert r["status"] == "irregular" and r["value"] == 2
    assert _one(rep, code="IS 1893", item="Table 6 (vi)",
                story="S3")["status"] == "irregular"
    assert _one(rep, code="EN 1998-1", item="4.2.3.3(2)",
                story="S3")["status"] == "irregular"
    assert _one(rep, code="ASCE 7", item="Table 12.3-2 Type 4",
                story="S2")["status"] == "ok"
    m.add_member("column", "COL", (5, 3, 6.0), (5, 3, 9.0), story="S3")
    rep = irr.compute(m)
    out = [d for d in rep["discontinuities"] if d["kind"] == "out_of_plane"]
    assert len(out) == 1 and out[0]["offset"] == pytest.approx(
        math.hypot(5.0, 3.0))
    assert _one(rep, code="ASCE 7", item="Table 12.3-1 Type 4",
                story="S3")["status"] == "irregular"


def test_wall_in_plane_offset_ratio():
    """Wall 0..6 m in S1, 2..8 m in S2 on the same line y = 0: the S2 end
    x = 8 overhangs 2 m -> in-plane, offset 2, ratio 2/6 = 0.333 > 0.20
    (IS 6(iv))."""
    m = _frame([[0, 10], [0, 10]], heights=(3.0, 3.0))
    m.add_shell_section(ShellSection("W", "C", 0.2))
    m.add_shell("wall", "shell", "W", [(0, 0, 0), (6, 0, 0), (6, 0, 3),
                                       (0, 0, 3)], story="S1")
    m.add_shell("wall", "shell", "W", [(2, 0, 3), (8, 0, 3), (8, 0, 6),
                                       (2, 0, 6)], story="S2")
    m.story_masses = {"S1": 50.0, "S2": 50.0}
    rep = irr.compute(m)
    (d,) = [x for x in rep["discontinuities"] if x["type"] == "wall"]
    assert d["kind"] == "in_plane"
    assert d["offset"] == pytest.approx(2.0)
    assert d["ratio"] == pytest.approx(2.0 / 6.0)
    r = _one(rep, code="IS 1893", item="Table 6 (iv)", story="S2")
    assert r["value"] == pytest.approx(2.0 / 6.0)
    assert r["status"] == "irregular"
    assert _one(rep, code="ASCE 7", item="Table 12.3-2 Type 4",
                story="S2")["status"] == "irregular"


def test_weak_story_user_and_estimated_strength():
    """User strengths X 700 / 1000 / 1000 kN: 0.70 < 0.80 -> irregular
    (ASCE V-5a), IS 6(v) 0.7 < 1.0; 600 -> 0.60 < 0.65 extreme (5b).
    Estimated: four 0.5 x 0.5 concrete columns, f'c 30 MPa:
    Vn = 4 x 0.17 sqrt(30) 0.8 x 0.25 x 1000 = 744.94 kN per story."""
    m = _frame([[0, 10]] * 3)
    m.story_masses = {s.name: 50.0 for s in m.stories}
    sd = irr.story_data(m)
    vn = 4 * 0.17 * math.sqrt(30.0) * 0.8 * 0.25 * 1000.0
    assert sd[0]["Vn_x"] == pytest.approx(vn, rel=1e-12)
    assert sd[0]["strength_source"] == "estimated"
    rep = irr.compute(m)
    assert _one(rep, code="ASCE 7", item="Table 12.3-2 Type 5", story="S1",
                direction="X")["value"] == pytest.approx(1.0)
    opts = {"story_strength": {"S1": {"x": 700.0, "y": 1000.0},
                               "S2": {"x": 1000.0, "y": 1000.0},
                               "S3": {"x": 1000.0, "y": 1000.0}}}
    rep = irr.compute(m, options=opts)
    r = _one(rep, code="ASCE 7", item="Table 12.3-2 Type 5", story="S1",
             direction="X")
    assert r["value"] == pytest.approx(0.7) and r["status"] == "irregular"
    assert _one(rep, code="IS 1893", item="Table 6 (v)", story="S1",
                direction="X")["status"] == "irregular"
    opts["story_strength"]["S1"]["x"] = 600.0
    assert _one(irr.compute(m, options=opts), code="ASCE 7",
                item="Table 12.3-2 Type 5", story="S1",
                direction="X")["status"] == "extreme"
    # no f'c -> n/a with the reason
    m2 = _frame([[0, 10]] * 3, fc=None)
    m2.story_masses = m.story_masses
    r = _one(irr.compute(m2), code="ASCE 7", item="Table 12.3-2 Type 5",
             story="S1", direction="X")
    assert r["status"] == "n/a" and r["note"] == irr.NA_STRENGTH


def _slab_model(polys, openings=None):
    m = _frame([[0, 10]], heights=(3.0,))
    m.add_shell_section(ShellSection("SL", "C", 0.2))
    for k, p in enumerate(polys):
        r = m.add_shell("slab", "shell", "SL", [(x, y, 3.0) for x, y in p],
                        story="S1")
        if openings and k in openings:
            r.openings.append(openings[k])
    m.story_masses = {"S1": 50.0}
    return m


def test_floor_reentrant_compactness_openings():
    """L floor = 10 x 4 strip + 4 x 6 leg: notch 6 x 6 -> both
    projections 0.6 > 0.15 (ASCE P-2, IS 5(ii)).  Outline 64 m^2, convex
    hull 100 - 6 x 6 / 2 = 82 m^2 -> EC8 recess (82 - 64)/64 = 0.28125 >
    0.05.  Slenderness 10/10 = 1."""
    m = _slab_model([[(0, 0), (10, 0), (10, 4), (0, 4)],
                     [(0, 4), (4, 4), (4, 10), (0, 10)]])
    rep = irr.compute(m)
    fl = rep["stories"][0]["floor"]
    assert fl["area_outline"] == pytest.approx(64.0)
    assert fl["area_hull"] == pytest.approx(82.0)
    assert fl["notch_x"] == pytest.approx(6.0)
    assert fl["notch_y"] == pytest.approx(6.0)
    for code, item in (("ASCE 7", "Table 12.3-1 Type 2"),
                       ("IS 1893", "Table 5 (ii)")):
        r = _one(rep, code=code, item=item, story="S1")
        assert r["value"] == pytest.approx(0.6)
        assert r["status"] == "irregular"
    r = _one(rep, code="EN 1998-1", item="4.2.3.2(4)", story="S1")
    assert r["value"] == pytest.approx(18.0 / 64.0)
    assert r["status"] == "irregular"
    assert _one(rep, code="EN 1998-1", item="4.2.3.2(5)",
                story="S1")["value"] == pytest.approx(1.0)
    # 10 x 10 slab with an 8 x 8 opening: 64 % > 50 % (P-3 / IS 5(iii));
    # a 6 x 6 opening: 36 % -> ok
    for frac, ratio, status in ((0.8, 0.64, "irregular"),
                                (0.6, 0.36, "ok")):
        a = (1.0 - frac) / 2.0
        m = _slab_model([[(0, 0), (10, 0), (10, 10), (0, 10)]],
                        {0: Opening(a, a, 1.0 - a, 1.0 - a)})
        rep = irr.compute(m)
        for code, item in (("ASCE 7", "Table 12.3-1 Type 3"),
                           ("IS 1893", "Table 5 (iii)")):
            r = _one(rep, code=code, item=item, story="S1")
            assert r["value"] == pytest.approx(ratio)
            assert r["status"] == status
        # a rectangle with a hole is compact (the hole is inside)
        assert _one(rep, code="EN 1998-1", item="4.2.3.2(4)",
                    story="S1")["value"] == pytest.approx(0.0)


def test_slenderness_and_nonparallel():
    m = _slab_model([[(0, 0), (25, 0), (25, 5), (0, 5)]])
    r = _one(irr.compute(m), code="EN 1998-1", item="4.2.3.2(5)",
             story="S1")
    assert r["value"] == pytest.approx(5.0) and r["status"] == "irregular"
    m = _frame([[0, 10]] * 2, heights=(3.0, 3.0))
    m.add_shell_section(ShellSection("W", "C", 0.2))
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    m.add_shell("wall", "shell", "W", [(0, 0, 0), (4 * c, 4 * s, 0),
                                       (4 * c, 4 * s, 3), (0, 0, 3)],
                story="S1")
    m.story_masses = {"S1": 50.0, "S2": 50.0}
    r = _one(irr.compute(m), code="IS 1893", item="Table 5 (v)")
    assert r["status"] == "irregular" and r["value"] == 1


def _synthetic_results():
    return {
        "irregularity": {"EQX": {
            "S1": {"tors_ratio_x": 1.25, "tors_ratio_y": None,
                   "flag": "torsional", "stiff_ratio": 0.65,
                   "soft_flag": "soft",
                   "na_reason": {"tors_ratio_y": "not applicable "
                                                 "(negligible drift)"}},
            "S2": {"tors_ratio_x": 1.05, "tors_ratio_y": None,
                   "flag": "none", "stiff_ratio": 1.2, "soft_flag": "",
                   "na_reason": {"tors_ratio_y": "x"}}},
            "DEAD": {"S1": {"tors_ratio_x": 1.9, "tors_ratio_y": 1.9,
                            "flag": "extreme", "stiff_ratio": 0.1,
                            "soft_flag": "extreme_soft"}}},
        "story_stiffness": {"EQX": {"S1": {"kx": 800.0, "ky": 0.0},
                                    "S2": {"kx": 1000.0, "ky": 0.0},
                                    "S3": {"kx": 1000.0, "ky": 0.0}}},
        "modal": {"participation": [
            {"mode": 1, "T": 1.00, "ux": 0.70, "uy": 0.0, "rz": 0.0},
            {"mode": 2, "T": 0.95, "ux": 0.0, "uy": 0.70, "rz": 0.0},
            {"mode": 3, "T": 1.20, "ux": 0.0, "uy": 0.0, "rz": 0.8}]},
    }


def test_response_based_checks_hand_values():
    """Torsion: r = Dmax/Davg = 1.25 -> Dmax/Dmin = r/(2 - r) = 1.6667 >
    1.5 (IS 5(i)); ASCE P-1a (>= 1.2).  Soft story: engine flag "soft".
    IS 6(i): k1/k2 = 800/1000 = 0.8 < 1; EC8 stiffness upper/lower k2/k1 =
    1.25 within [0.75, 1.25] -> ok.  Gravity case DEAD is ignored.
    Modes: Tx 1.00, Ty 0.95 -> within 10 % -> irregular modes; torsional
    mode 1.20 s longer than both -> IS 5(i)(b)."""
    m = _frame([[0, 10]] * 3)
    m.story_masses = {s.name: 50.0 for s in m.stories}
    m.pattern("EQX", "quake")
    m.add_case("EQX", {"EQX": 1.0})
    m.pattern("DEAD", "dead")
    m.add_case("DEAD", {"DEAD": 1.0})
    rep = irr.compute(m, _synthetic_results())
    r = _one(rep, code="IS 1893", item="Table 5 (i)", story="S1",
             direction="X")
    assert r["value"] == pytest.approx(1.25 / 0.75)
    assert r["status"] == "irregular"
    assert _one(rep, code="IS 1893", item="Table 5 (i)", story="S1",
                direction="Y")["status"] == "n/a"
    assert _one(rep, code="ASCE 7", item="Table 12.3-1 Type 1", story="S1",
                direction="X")["status"] == "irregular"
    assert _one(rep, code="ASCE 7", item="Table 12.3-2 Type 1",
                story="S1")["status"] == "irregular"
    r = _one(rep, code="IS 1893", item="Table 6 (i)", story="S1",
             direction="X")
    assert r["value"] == pytest.approx(0.8) and r["status"] == "irregular"
    r = _one(rep, code="IS 1893", item="Table 6 (i)", story="S1",
             direction="Y")
    assert r["status"] == "n/a"
    r = _one(rep, code="EN 1998-1", item="4.2.3.3(3)", story="S2",
             case="EQX", direction="X")
    assert r["value"] == pytest.approx(1.25) and r["status"] == "ok"
    assert not _rows(rep, case="DEAD")
    assert _one(rep, code="IS 1893",
                item="Table 6 (vii)")["status"] == "irregular"
    assert _one(rep, code="IS 1893",
                item="Table 5 (i)(b)")["status"] == "irregular"


def test_engine_results_flow_and_table():
    """Real run: a symmetric frame under EQX -> torsion ratio ~1 (ok), the
    Y direction gated n/a; the Irregularity Checks table carries the rows
    and the results JSON has no new key."""
    m = _bldg()
    m.pattern("EQX", "quake").nodal_loads.append(
        NodalLoad((0, 0, 10.0), fx=10.0))
    m.add_case("EQX", {"EQX": 1.0})
    d = OpenSeesEngine(m).run().to_dict()
    rep = irr.compute(m, d)
    assert _rows(rep, code="IS 1893", item="Table 6 (i)", case="EQX")
    t = tables.compute_table("irregularity_checks", d, m)
    assert t["rows"] and {c["key"] for c in t["columns"]} >= {
        "code", "item", "status", "value"}
    assert "irregularity_checks" in {x["key"] for x in tables.list_tables()}


def test_irregularity_api(tmp_path, monkeypatch):
    from skyframe.api import server as srv
    app = srv.create_app()
    app.config["TESTING"] = True
    m = _frame([[0, 10]] * 3)
    m.story_masses = {"S1": 100.0, "S2": 200.0, "S3": 100.0}
    srv._state["model"] = m
    with app.test_client() as c:
        r = c.post("/api/irregularity", json={"options": {"ec8_tol": 0.3}})
        assert r.status_code == 200
        j = r.get_json()
        assert j["has_results"] is False
        assert j["summary"]["ASCE 7"]["Table 12.3-2 Type 2"] == "irregular"
        assert j["options"]["ec8_tol"] == 0.3
        assert c.post("/api/irregularity",
                      json={"options": {"ec8_tol": 2}}).status_code == 400
    srv._state["model"] = srv.quick_building()


# =========================================================================
# 2. ASCE 7 combinations with grouped pattern types
# =========================================================================
def _combo_model(extra=()):
    m = _bldg()
    for name, kind in (("DEAD", "dead"), ("LIVE", "live"), ("EQX", "quake"),
                       ("WX", "wind")) + tuple(extra):
        m.pattern(name, kind)
        m.add_case(name, {name: 1.0})
    return m


@pytest.mark.parametrize("std", ["LRFD", "ASD"])
@pytest.mark.parametrize("sds", [None, 1.0])
def test_grouped_equals_legacy_on_legacy_kinds(std, sds):
    """The grouped generator reproduces the legacy combos on a model with
    only dead / live / quake / wind cases (same names and factors), and
    such a model never switches to it."""
    m = _combo_model()
    assert not grouped_applies(m)
    legacy = asce7_combinations(m, std, sds)
    grouped = asce7_grouped(m, std, 0.0 if sds is None else sds)
    assert grouped == legacy


def test_grouped_lrfd_hand_factors():
    """D = DEAD + SDEAD, L = LIVE + RLIVE, Lr = ROOF:
    2: 1.2D + 1.6L + 0.5Lr; 3: 1.2D + 1.6Lr + 1.0L, 1.2D + 1.6Lr +- 0.5W;
    4: 1.2D + 1.0L + 0.5Lr +- 1.0W; 6: (1.2 + 0.2 SDS) D + L +- E."""
    m = _combo_model((("SDEAD", "superdead"), ("RLIVE", "reducible_live"),
                      ("ROOF", "roof_live")))
    assert grouped_applies(m)
    c = asce7_combinations(m, "LRFD", 0.5)
    assert c["1.4D"] == {"DEAD": 1.4, "SDEAD": 1.4}
    assert c["1.2D+1.6L+0.5Lr"] == {"DEAD": 1.2, "SDEAD": 1.2, "LIVE": 1.6,
                                    "RLIVE": 1.6, "ROOF": 0.5}
    assert c["1.2D+1.6Lr+1.0L"] == {"DEAD": 1.2, "SDEAD": 1.2, "ROOF": 1.6,
                                    "LIVE": 1.0, "RLIVE": 1.0}
    assert c["1.2D+1.6Lr-0.5WX"] == {"DEAD": 1.2, "SDEAD": 1.2,
                                     "ROOF": 1.6, "WX": -0.5}
    assert c["1.2D+1.0L+0.5Lr+1.0WX"] == {
        "DEAD": 1.2, "SDEAD": 1.2, "LIVE": 1.0, "RLIVE": 1.0, "ROOF": 0.5,
        "WX": 1.0}
    assert c["1.3D+1.0L+1.0EQX"] == {"DEAD": 1.3, "SDEAD": 1.3, "LIVE": 1.0,
                                     "RLIVE": 1.0, "EQX": 1.0}
    assert c["0.8D-1.0EQX"] == {"DEAD": 0.8, "SDEAD": 0.8, "EQX": -1.0}
    assert "1.2D+1.6L" not in c


def test_grouped_asd_with_snow():
    """ASD with S: D + S, D + 0.75L + 0.75S, D + 0.75L + 0.75S +- 0.525E,
    D + 0.75L + 0.75S +- 0.45W."""
    m = _combo_model((("SNOW", "snow"),))
    c = asce7_combinations(m, "ASD")
    assert c["D+S"] == {"DEAD": 1.0, "SNOW": 1.0}
    assert c["D+0.75L+0.75S"] == {"DEAD": 1.0, "LIVE": 0.75, "SNOW": 0.75}
    assert c["D+0.75L+0.75S+0.525EQX"] == {"DEAD": 1.0, "LIVE": 0.75,
                                           "SNOW": 0.75, "EQX": 0.525}
    assert c["D+0.75L+0.75S-0.45WX"] == {"DEAD": 1.0, "LIVE": 0.75,
                                         "SNOW": 0.75, "WX": -0.45}
    assert c["0.6D+0.6WX"] == {"DEAD": 0.6, "WX": 0.6}


# =========================================================================
# 3 / 8. E2K: pattern types, releases + partial fixity, joint axes, RS
# =========================================================================
def _rel_model():
    """Portal: two columns + beam; beam end i V2 + partial M3 (1500
    kN m/rad), end j T released; base joint 1 local axes a = 30 deg; a
    superdead pattern; a two-direction RS case (angle 30, ecc 0.05, ABS
    with dir_sf 0.3)."""
    m = BuildingModel("rel")
    m.add_material(Material("S", 2.0e8, 0.3, 77.0, material_type="steel",
                            fy=345000.0))
    m.add_section(FrameSection("B", "S", 0.01, 2e-4, 1e-4, 1e-6))
    m.set_stories([3.0])
    m.add_member("column", "B", (0, 0, 0), (0, 0, 3), uid="C1",
                 story="Story1")
    m.add_member("column", "B", (6, 0, 0), (6, 0, 3), uid="C2",
                 story="Story1")
    m.add_member("beam", "B", (0, 0, 3), (6, 0, 3), uid="B1",
                 story="Story1")
    m.members[2].end_releases = {"i": {"M3": 1500.0}, "j": {"T": 0.0}}
    m.supports.append(PointSupport((0, 0, 0), (1,) * 6))
    m.supports.append(PointSupport((6, 0, 0), (1,) * 6))
    m.joint_local_axes = [{"point": [0.0, 0.0, 0.0],
                           "angles": [30.0, 0.0, 0.0]}]
    p = m.pattern("SD", "superdead")
    p.member_loads.append(MemberLoad("B1", "udl", 5.0, 0, 0, 1, "gravity"))
    m.pattern("RL", "roof_live")
    m.add_case("SD", {"SD": 1.0})
    for x in (0.0, 6.0):                    # e2k-expressible joint masses
        m.nodal_masses.append(NodalMass((x, 0.0, 3.0), mx=5.0, my=5.0,
                                        mz=5.0))
    m.num_modes = 3
    m.add_rs_case("RS", "X", [[0.0, 0.5], [10.0, 0.5]])
    rs = m.rs_cases["RS"]
    rs.angle = 30.0
    rs.ecc = 0.05
    rs.loads = [{"dir": "X", "scale": 1.0, "function": ""},
                {"dir": "Y", "scale": 0.3, "function": ""}]
    rs.dir_combo = "ABS"
    rs.dir_sf = 0.3
    m.validate()
    return m


@pytest.mark.parametrize("units", [("KN", "M"), ("KIP", "FT"),
                                   ("N", "MM")])
def test_e2k_native_releases_axes_types_rs(units):
    m = _rel_model()
    text, _rep = export_e2k(m, units=units, extensions=False)
    assert 'RELEASE "M3I TJ"' in text
    assert "PARTIALFIXITY" in text
    assert "ANGLEA 30  ANGLEB 0  ANGLEC 0" in text
    assert 'TYPE  "Super Dead"' in text and 'TYPE  "Roof Live"' in text
    assert "ECCRATIO 0.05" in text and 'DIRCOMBO "ABS"  DIRSF 0.3' in text
    assert text.count('LOADCASE "RS"  ACCEL') == 2 and "ANG  30" in text
    m2, warns = read_e2k(text, units=units)
    b = {x.uid: x for x in m2.members}["B1"]
    assert b.end_releases["j"] == {"T": 0.0}
    assert b.end_releases["i"]["M3"] == pytest.approx(1500.0, rel=1e-12)
    assert m2.joint_local_axes[0]["angles"] == pytest.approx([30, 0, 0])
    assert m2.patterns["SD"].kind == "superdead"
    assert m2.patterns["RL"].kind == "roof_live"
    rs = m2.rs_cases["RS"]
    assert rs.angle == 30.0 and rs.ecc == 0.05
    assert rs.dir_combo == "ABS" and rs.dir_sf == pytest.approx(0.3)
    assert [ld["dir"] for ld in rs.loads] == ["X", "Y"]
    assert rs.loads[1]["scale"] * rs.scale == pytest.approx(0.3)


def test_e2k_native_round_trip_same_results():
    """Native records alone (no $SF lines) reproduce the analysis: member
    forces of the SD case and the RS envelope match to 1e-9."""
    m = _rel_model()
    text, _r = export_e2k(m, extensions=False)
    m2, _w = read_e2k(text)
    m2.validate()
    a = OpenSeesEngine(m).run().to_dict()
    b = OpenSeesEngine(m2).run().to_dict()
    for blk, name in (("cases", "SD"), ("rs_cases", "RS")):
        fa = a[blk][name]["member_forces"]
        fb = b[blk][name]["member_forces"]
        for uid in fa:
            assert fb[uid] == pytest.approx(fa[uid], rel=1e-9, abs=1e-9)


def test_e2k_extensions_exact_and_legacy_text():
    m = _rel_model()
    text, _r = export_e2k(m)
    m2, _w = read_e2k(text)
    assert m2.to_dict() == m.to_dict()
    # a legacy pin keeps the legacy text, no new records appear
    m3 = _rel_model()
    m3.members[2].end_releases = None
    m3.members[2].releases = "Mi"
    m3.joint_local_axes = []
    rs = m3.rs_cases["RS"]
    rs.angle, rs.ecc, rs.loads, rs.dir_combo, rs.dir_sf = (
        0.0, 0.0, [], "SRSS", 1.0)
    t3, _r = export_e2k(m3, extensions=False)
    assert 'RELEASE "M2I M3I"' in t3
    for tok in ("PARTIALFIXITY", "ANGLEA", "ECCRATIO", "DIRCOMBO", "ANG "):
        assert tok not in t3
    assert t3.count('LOADCASE "RS"  ACCEL') == 1


def test_e2k_etabs_type_names_and_partial_fixity_text():
    """ETABS spellings read to the kept kinds; a hand-written
    PARTIALFIXITY in kip-in converts to kN m/rad (1 kip in = 0.112985
    kN m)."""
    from test_exchange import ETABS_CANTILEVER  # noqa: F401
    base = ETABS_CANTILEVER
    extra = ('\n  LOADPATTERN "SDL"  TYPE  "SUPER DEAD"  SELFWEIGHT  0'
             '\n  LOADPATTERN "RLL"  TYPE  "Reducible Live"  SELFWEIGHT  0'
             '\n  LOADPATTERN "SN"  TYPE  "Snow"  SELFWEIGHT  0\n')
    m, _w = read_e2k(base + extra)
    assert m.patterns["SDL"].kind == "superdead"
    assert m.patterns["RLL"].kind == "reducible_live"
    assert m.patterns["SN"].kind == "snow"
    from skyframe.io.e2k_ext import parse_releases
    from skyframe.io.e2k import _tokenize, _kv
    from skyframe.io.exchange_common import Units
    u = Units("KIP", "IN")
    kv = _kv(_tokenize('X 1 RELEASE "PI M3J" PARTIALFIXITY "M3J 1000"'), 2)
    tok, er = parse_releases(kv, lambda v, q: u.to_si(float(v), q))
    assert tok == ""
    assert er == {"i": {"P": 0.0},
                  "j": {"M3": pytest.approx(1000 * 4.4482216152605
                                            * 0.0254)}}


# =========================================================================
# 4. joint-local reactions
# =========================================================================
def test_joint_local_reactions_skewed_roller():
    """Roller on a 30 deg incline (joint axes b = 30, U3 restrained in the
    local axes): R_B normal to the incline -> F1 = 0, F3 = (P/2)/cos30.
    A joint without axes reports F1..M3 = FX..MZ."""
    P, L = 10.0, 6.0
    m = BuildingModel("roll")
    m.add_material(Material("S", 2e8, 0.3, 0.0, material_type="steel"))
    m.add_section(FrameSection("B", "S", 0.01, 1e-4, 1e-4, 2e-4))
    m.set_stories([3.0])
    m.rigid_diaphragms = False
    m.add_member("beam", "B", (0, 0, 0), (L, 0, 0), uid="B1")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 0, 0)))
    m.supports.append(PointSupport((L, 0, 0), (0, 1, 1, 0, 0, 0)))
    m.joint_local_axes = [{"point": [L, 0, 0], "angles": [0, 30, 0]}]
    p = m.pattern("W", "dead")
    p.member_loads.append(MemberLoad("B1", "point", P, 0, 0.5, 1, "gravity"))
    m.add_case("W", {"W": 1.0})
    m.validate()
    d = OpenSeesEngine(m).run().to_dict()
    t = tables.compute_table("joint_reactions", d, m)
    keys = [c["key"] for c in t["columns"]]
    assert keys[-7:] == ["local_axes", "F1", "F2", "F3", "M1", "M2", "M3"]
    rb = [r for r in t["rows"] if abs(r["x"] - L) < 1e-9][0]
    ra = [r for r in t["rows"] if abs(r["x"]) < 1e-9][0]
    assert rb["local_axes"] == "yes" and ra["local_axes"] == ""
    assert abs(rb["F1"]) < 1e-6 * P
    assert rb["F3"] == pytest.approx(P / 2 / math.cos(math.radians(30)),
                                     rel=1e-6)
    assert [ra[k] for k in jlr.LOCAL_KEYS] == [
        ra[k] for k in ("FX", "FY", "FZ", "MX", "MY", "MZ")]
    lr = jlr.local_reactions(m, d)
    (tag, e), = lr["W"].items()
    assert e["local"][2] == pytest.approx(rb["F3"])
    # no joint axes -> legacy columns only
    m.joint_local_axes = []
    t2 = tables.compute_table("joint_reactions", d, m)
    assert "F1" not in [c["key"] for c in t2["columns"]]


# =========================================================================
# 5. shell forces in combos with RS members
# =========================================================================
def _rs_wall():
    from _v02_utils import finish, new_model
    mdl = new_model("rsw", [3.0], nu=0.0)
    mdl.add_shell_section(ShellSection("SH", "CONC", 0.2))
    mdl.add_shell("wall", "shell", "SH", [(0, 0, 0), (2, 0, 0), (2, 0, 3),
                                          (0, 0, 3)],
                  mesh_size=1.0, story="Story1")
    for x in (0.0, 1.0, 2.0):
        mdl.supports.append(PointSupport((x, 0, 0), (1,) * 6))
    mdl.pattern("D", "dead").nodal_loads.append(
        NodalLoad((2, 0, 3), fz=-10.0, fx=2.0))
    mdl.add_case("D", {"D": 1.0})
    finish(mdl, (2, 0, 3))
    mdl.nodal_masses[0].mx = 5.0
    mdl.num_modes = 1
    mdl.add_rs_case("RSX", "X", [[0.0, 0.4], [10.0, 0.4]], num_modes=1)
    mdl.add_combo("1.2D+E", {"D": 1.2, "RSX": 1.0})
    mdl.add_combo("ENV", {"D": 1.0, "RSX": 1.0}, combo_type="envelope")
    mdl.add_combo("SRSS", {"D": 1.0, "RSX": 1.0}, combo_type="srss")
    return mdl


def test_shell_forces_in_rs_combos_closed_form():
    """add: hi/lo = 1.2 s +- |r|; envelope: max(s, |r|) / min(s, -|r|);
    srss: +- sqrt(s^2 + r^2) per resultant (s = static D, r = RS peak).
    The shell contours of the combo then equal the engine box."""
    from skyframe.core import shell_results as sr
    m = _rs_wall()
    d = OpenSeesEngine(m).run().to_dict()
    n = len(d["shell_quads"])
    s = np.array([d["cases"]["D"]["shell_forces"][str(i)]
                  for i in range(n)])
    r = np.abs(np.array([d["rs_cases"]["RSX"]["shell_forces"][str(i)]
                         for i in range(n)]))
    assert np.abs(r).max() > 0.0

    def arr(cd):
        return np.array([cd["shell_forces"][str(i)] for i in range(n)])
    c = d["combos"]["1.2D+E"]
    np.testing.assert_allclose(arr(c), 1.2 * s + r, rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(arr(c["min"]), 1.2 * s - r, rtol=1e-12,
                               atol=1e-12)
    c = d["combos"]["ENV"]
    np.testing.assert_allclose(arr(c), np.maximum(s, r), rtol=1e-12)
    np.testing.assert_allclose(arr(c["min"]), np.minimum(s, -r),
                               rtol=1e-12)
    c = d["combos"]["SRSS"]
    v = np.sqrt(s ** 2 + r ** 2)
    np.testing.assert_allclose(arr(c), v, rtol=1e-12)
    np.testing.assert_allclose(arr(c["min"]), -v, rtol=1e-12, atol=1e-15)
    states, info = sr.source_states(m, d, "1.2D+E")
    (lo, hi), = states
    np.testing.assert_allclose(hi, 1.2 * s + r, rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(lo, 1.2 * s - r, rtol=1e-12, atol=1e-12)
    assert info["bounded"]
    names = {x["name"] for x in sr.list_sources(m, d)}
    assert {"1.2D+E", "ENV", "SRSS"} <= names


# =========================================================================
# 6. wind: TS 498, KDS 41, CFE
# =========================================================================
TRIB = {"S1": 3.5, "S2": 3.0, "S3": 1.5}       # _bldg facade heights


def _F(s):
    return {r["story"]: r["F"] for r in s["stories"]}


def test_ts498_hand_example():
    """z = 4, 7 m -> q 0.5 kPa; z = 10 m -> 0.8 kPa (Table 4).  Wind X
    loads the 6 m facade, cp 0.8 + 0.4 = 1.2: F = 1.2 q trib 6 ->
    12.6 / 10.8 / 8.64 kN."""
    s = al.compute(_bldg(), "ts498_wind")
    F = _F(s)
    assert F["S1"] == pytest.approx(12.6)
    assert F["S2"] == pytest.approx(10.8)
    assert F["S3"] == pytest.approx(8.64)
    assert s["V"] == pytest.approx(32.04)
    from skyframe.core.wind_codes_ext import ts498_q
    assert [ts498_q(z) for z in (5.0, 8.0, 15.0, 60.0, 150.0)] == [
        0.5, 0.5, 0.8, 1.1, 1.3]


def test_kds41_hand_example():
    """V0 30 m/s, roughness C: Kzr = 1.0 below Zb = 10 m -> q = 0.5 x
    1.225 x 900 = 551.25 Pa everywhere on this 10 m building; p = GD (0.8
    + 0.5) q = 2.0 x 1.3 x 0.55125 = 1.43325 kPa; F(S1) = p 3.5 x 6 =
    30.098 kN.  Tall C building z = 36 m: Kzr = (36/10)^0.15."""
    s = al.compute(_bldg(), "kds41_wind", V0=30.0)
    assert _F(s)["S1"] == pytest.approx(1.43325 * 3.5 * 6, rel=1e-12)
    assert s["qh"] == pytest.approx(0.55125, rel=1e-12)
    from skyframe.core.wind_codes_ext import kds_kzr
    assert kds_kzr(36.0, "C") == pytest.approx(3.6 ** 0.15)
    assert kds_kzr(10.0, "B") == pytest.approx(0.81)
    assert kds_kzr(30.0, "B") == pytest.approx(0.81 * 2.0 ** 0.22)
    assert kds_kzr(1000.0, "D") == pytest.approx(1.13 * 50.0 ** 0.10)


def test_cfe_hand_example():
    """VR 40 m/s, category 2: F_rz = 1 for z <= 10 m -> q = 0.613 x 1600
    = 980.8 Pa; wind X: d/b = 10/6 -> leeward -0.5 + 0.2 x (2/3) =
    -0.36667; p = (0.8 + 0.36667) q; F(S1) = p 3.5 x 6.  Category 3 at
    36 m: F_rz = 0.881 x 3.6^0.156; NTC form drops c."""
    s = al.compute(_bldg(), "cfe_wind", VR=40.0)
    cpl = -0.5 + 0.2 * (10.0 / 6.0 - 1.0)
    assert s["cpe_leeward"] == pytest.approx(cpl)
    assert _F(s)["S1"] == pytest.approx((0.8 - cpl) * 0.9808 * 3.5 * 6,
                                        rel=1e-12)
    from skyframe.core.wind_codes_ext import cfe_frz
    assert cfe_frz(36.0, 3) == pytest.approx(0.881 * 3.6 ** 0.156)
    assert cfe_frz(36.0, 3, "ntc") == pytest.approx(3.6 ** 0.156)
    assert cfe_frz(1000.0, 4) == pytest.approx(0.815 * 45.5 ** 0.170)
    with pytest.raises(ValueError):
        al.compute(_bldg(), "cfe_wind", VR=40.0, category=5)


@pytest.mark.parametrize("code,kw", [("ts498_wind", {}),
                                     ("kds41_wind", {"V0": 30.0}),
                                     ("cfe_wind", {"VR": 40.0})])
def test_new_wind_codes_registered_and_generate(code, kw):
    assert code in al.CODES and code in al.WIND_CODES
    m = _bldg()
    s = al.compute(m, code, direction="Y", **kw)
    pat = al.generate(m, code, None, direction="Y", **kw)
    assert pat.kind == "wind" and pat.name == al._DEFAULT_NAME[code]
    assert sum(sf.fy for sf in pat.story_forces) == pytest.approx(s["V"])


# =========================================================================
# 7. NTC-Sismo long-period distribution
# =========================================================================
def test_ntc_long_period_two_term_distribution():
    """W 1000 / 1000 / 800 kN at h 4 / 7 / 10 m: sum W = 2800, sum W h =
    19 000, sum W h^2 = 145 000.  T 1.5 > Tb 1.0, r = 2: q = (1/1.5)^2 =
    0.444444; kh1 = (1 - 0.5 x 2 x 0.555556) 2800/19000 = 0.0654971;
    kh2 = 0.75 x 2 x 0.555556 x 2800/145000 = 0.0160920;
    F_i = W_i Cs (kh1 h + kh2 h^2); V = Cs W (1 + 0.25 r (1 - q))."""
    kw = dict(a0=0.2, c=0.6, Ta=0.4, Tb=1.0, k=0.8, Q=3.0, R0=2.0)
    s = al.compute(_bldg(), "ntc_cdmx", T=1.5, **kw)
    q = (1 / 1.5) ** 2
    kh1 = (1 - 0.5 * 2 * (1 - q)) * 2800 / 19000
    kh2 = 0.75 * 2 * (1 - q) * 2800 / 145000
    assert s["q"] == pytest.approx(q) and s["kh1"] == pytest.approx(kh1)
    assert s["kh2"] == pytest.approx(kh2)
    assert kh1 == pytest.approx(0.0654971, rel=1e-6)
    assert kh2 == pytest.approx(0.0160920, rel=1e-5)
    Cs = s["Cs"]
    for n, w, h in (("S1", 1000, 4), ("S2", 1000, 7), ("S3", 800, 10)):
        assert _F(s)[n] == pytest.approx(w * Cs * (kh1 * h + kh2 * h * h),
                                         rel=1e-12)
    assert s["V"] == pytest.approx(Cs * 2800 * (1 + 0.25 * 2 * (1 - q)),
                                   rel=1e-12)
    # r = 1 (NTC-2004 zone III): q = 2/3
    s1 = al.compute(_bldg(), "ntc_cdmx", T=1.5, r=1.0, **kw)
    assert s1["V"] == pytest.approx(s1["Cs"] * 2800 * (1 + 0.25 / 3),
                                    rel=1e-12)
    # linear keeps the pre-Wave-B w h distribution of V = Cs W
    sl = al.compute(_bldg(), "ntc_cdmx", T=1.5, long_period="linear", **kw)
    assert sl["V"] == pytest.approx(Cs * 2800)
    assert _F(sl)["S1"] == pytest.approx(Cs * 2800 * 4000 / 19000)
    # T <= Tb is unchanged (no reduced keys)
    s06 = al.compute(_bldg(), "ntc_cdmx", T=0.6, **kw)
    assert "kh1" not in s06
    with pytest.raises(ValueError):
        al.compute(_bldg(), "ntc_cdmx", T=1.5, long_period="x", **kw)
