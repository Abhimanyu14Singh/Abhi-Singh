"""ETABS analysis-results tables (skyframe.core.tables + /api/tables).

Every table is pure post-processing of a solved results dict (no re-solve).
Expected numbers come from closed forms:

* cantilever column joint drift = (P L^3 / 3EI) / L;
* guided two-story shear building: story stiffness k_i = 12 E I_i / h_i^3;
* single-story rigid diaphragm on four cantilever columns (3EI/h^3 springs +
  GJ/h torsion): CR, torsional stiffness and the max/avg displacement ratio
  by hand — 1.0 for the symmetric model, the analytic value when eccentric;
  the pure torsional period 2*pi*sqrt(I_theta / K_theta);
* the base-reaction resultant of a single point load passes through it;
* load-pattern / load-case equilibrium errors ~ 0 %;
* modal direction factors sum to 1 and flag the torsional mode; mass ratios
  and participation factors reproduce the engine's ``modal.participation``.

Units per CONTRACT.md: kN, m, tonne, s; E in kPa.
"""

import json
import math

import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core import tables
from skyframe.core.builder import quick_building
from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection, Material,
                                 MemberLoad, MemberUDL, NodalLoad,
                                 PointSupport, ShellRegion, ShellSection,
                                 StoryForce)

E = 25_000_000.0
NU = 0.2


def _solve(model):
    eng = OpenSeesEngine(model)
    d = eng.run().to_dict()
    return d, tables.engine_context(eng)


def _rows(key, d, model, ctx=None, **kw):
    return tables.compute_table(key, d, model, ctx, **kw)["rows"]


def _one(rows, **match):
    hits = [r for r in rows if all(r[k] == v for k, v in match.items())]
    assert len(hits) == 1, (match, hits)
    return hits[0]


# --------------------------------------------------------------------------- #
# model builders
# --------------------------------------------------------------------------- #
def _cantilever(L=4.0, s=0.4, H=10.0, P=0.0):
    mdl = BuildingModel(name="cant")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("C", E=E, nu=NU))
    mdl.set_stories([L])
    mdl.num_modes = 0
    mdl.add_section(FrameSection.rectangular("COL", "C", s, s))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.pattern("H", "quake").nodal_loads.append(
        NodalLoad((0, 0, L), fx=H, fz=-P))
    mdl.add_case("H", {"H": 1.0})
    return mdl


def _shear_building(h1=4.0, h2=3.0, s1=0.5, s2=0.4, F1=30.0, F2=50.0):
    """Two stacked fixed-guided columns: an exact 2-DOF shear building."""
    mdl = BuildingModel(name="shear2")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("C", E=E, nu=NU))
    mdl.set_stories([h1, h2])
    mdl.num_modes = 0
    mdl.add_section(FrameSection.rectangular("C1", "C", s1, s1))
    mdl.add_section(FrameSection.rectangular("C2", "C", s2, s2))
    mdl.add_member("column", "C1", (0, 0, 0), (0, 0, h1), story="Story1",
                   uid="K1")
    mdl.add_member("column", "C2", (0, 0, h1), (0, 0, h1 + h2),
                   story="Story2", uid="K2")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    for z in (h1, h1 + h2):
        mdl.supports.append(PointSupport((0, 0, z), (0, 1, 1, 1, 1, 0)))
    p = mdl.pattern("EQX", "quake")
    p.nodal_loads.append(NodalLoad((0, 0, h1), fx=F1))
    p.nodal_loads.append(NodalLoad((0, 0, h1 + h2), fx=F2))
    mdl.add_case("EQX", {"EQX": 1.0})
    mdl.pattern("DEAD", "dead").nodal_loads.append(
        NodalLoad((0, 0, h1 + h2), fz=-5.0))
    mdl.add_case("DEAD", {"DEAD": 1.0})
    return mdl


def _diaphragm(s_south=0.4, s_north=0.4, a=3.0, b=4.0, H=4.0, F=100.0,
               mass=50.0, num_modes=3, cm_load=None):
    """Single rigid-diaphragm story on four cantilever corner columns (no
    beams): the columns at y=-b are s_south square, at y=+b s_north."""
    mdl = BuildingModel(name="dia")
    mdl.add_material(Material("C", E=E, nu=NU))
    mdl.set_stories([H])
    mdl.add_section(FrameSection.rectangular("CS", "C", s_south, s_south))
    mdl.add_section(FrameSection.rectangular("CN", "C", s_north, s_north))
    i = 0
    for x in (-a, a):
        for y in (-b, b):
            mdl.add_member("column", "CS" if y < 0 else "CN", (x, y, 0),
                           (x, y, H), story="Story1", uid=f"C{i}")
            mdl.supports.append(PointSupport((x, y, 0), (1, 1, 1, 1, 1, 1)))
            i += 1
    mdl.pattern("EQX", "quake").story_forces.append(StoryForce("Story1", fx=F))
    mdl.add_case("EQX", {"EQX": 1.0})
    mdl.pattern("EQY", "quake").story_forces.append(StoryForce("Story1", fy=F))
    mdl.add_case("EQY", {"EQY": 1.0})
    if cm_load is not None:      # a single gravity point load -> CM there
        mdl.pattern("DEAD", "dead").nodal_loads.append(
            NodalLoad((cm_load[0], cm_load[1], H), fz=-100.0))
        mdl.add_case("DEAD", {"DEAD": 1.0})
        mdl.mass_source = {"DEAD": 1.0}
    else:
        mdl.story_masses = {"Story1": mass}
    mdl.num_modes = num_modes
    return mdl


def _diaphragm_hand(mdl, a=3.0, b=4.0, H=4.0, F=100.0):
    """Closed-form CR, K_theta and the Fx edge displacements."""
    G = E / (2 * (1 + NU))
    ks, kt = {}, 0.0
    for m in mdl.members:
        sec = mdl.sections[m.section]
        ks[(m.pi[0], m.pi[1])] = 3 * E * sec.I22 / H ** 3
        kt += G * sec.J / H
    Kx = sum(ks.values())
    ycr = sum(k * y for (x, y), k in ks.items()) / Kx
    Kth = sum(k * (y - ycr) ** 2 for (x, y), k in ks.items()) + \
        sum(k * x ** 2 for (x, y), k in ks.items()) + kt
    theta = F * ycr / Kth
    u_cr = F / Kx
    d = [u_cr - theta * (y - ycr) for y in (-b, b)]
    ratio = max(abs(v) for v in d) / (0.5 * (abs(d[0]) + abs(d[1])))
    return {"Kx": Kx, "ycr": ycr, "Kth": Kth, "theta": theta, "d": d,
            "ratio": ratio}


# --------------------------------------------------------------------------- #
# 1. catalogue
# --------------------------------------------------------------------------- #
def test_catalogue_shape_and_quantities():
    cat = tables.list_tables()
    keys = [t["key"] for t in cat]
    assert len(keys) == len(set(keys)) >= 16
    for want in ("joint_drifts", "diaphragm_cm_displacements",
                 "diaphragm_max_avg_drifts", "story_stiffness",
                 "story_forces", "base_reactions", "load_pattern_summary",
                 "modal_periods", "modal_mass_ratios",
                 "modal_participation_factors", "modal_direction_factors"):
        assert want in keys
    for t in cat:
        assert t["group"].startswith("Analysis Results > ")
        assert t["title"]
        ck = [c["key"] for c in t["columns"]]
        assert len(ck) == len(set(ck))
        for c in t["columns"]:
            assert c["quantity"] in tables.QUANTITIES
            assert c["label"]
    with pytest.raises(KeyError):
        tables.compute_table("nope", {}, BuildingModel())


def test_rows_carry_exactly_the_catalogue_columns():
    mdl = quick_building(stories=2)
    d, ctx = _solve(mdl)
    for t in tables.list_tables():
        out = tables.compute_table(t["key"], d, mdl, ctx)
        assert out["columns"] == t["columns"]
        assert out["rows"], t["key"]
        want = {c["key"] for c in t["columns"]}
        for r in out["rows"]:
            assert set(r) == want, t["key"]
        json.dumps(out)                                   # JSON-safe


# --------------------------------------------------------------------------- #
# 2. joint drifts
# --------------------------------------------------------------------------- #
def test_cantilever_joint_drift_closed_form():
    L, s, H = 4.0, 0.4, 10.0
    mdl = _cantilever(L, s, H)
    d, ctx = _solve(mdl)
    I = s ** 4 / 12.0
    tip = H * L ** 3 / (3 * E * I)
    r = _one(_rows("joint_drifts", d, mdl, ctx), case="H")
    assert r["story"] == "Story1" and r["height"] == pytest.approx(L)
    assert r["disp_x"] == pytest.approx(tip, rel=1e-9)
    assert r["drift_x"] == pytest.approx(tip / L, rel=1e-9)
    assert abs(r["drift_y"]) < 1e-15


def test_joint_drift_relative_to_joint_below():
    mdl = _shear_building()
    d, ctx = _solve(mdl)
    rows = [r for r in _rows("joint_drifts", d, mdl, ctx) if r["case"] == "EQX"]
    r1, r2 = (_one(rows, story="Story1"), _one(rows, story="Story2"))
    assert r2["joint_below"] == r1["joint"]
    st = d["cases"]["EQX"]["story"]
    assert r1["drift_x"] == pytest.approx(st["Story1"]["drift_x"], rel=1e-12)
    assert r2["drift_x"] == pytest.approx(st["Story2"]["drift_x"], rel=1e-12)
    assert r2["drift_x"] == pytest.approx(
        (r2["disp_x"] - r1["disp_x"]) / 3.0, rel=1e-12)


# --------------------------------------------------------------------------- #
# 3. story stiffness + story forces
# --------------------------------------------------------------------------- #
def test_two_story_shear_building_story_stiffness_analytic():
    h1, h2, s1, s2, F1, F2 = 4.0, 3.0, 0.5, 0.4, 30.0, 50.0
    mdl = _shear_building(h1, h2, s1, s2, F1, F2)
    d, ctx = _solve(mdl)
    rows = _rows("story_stiffness", d, mdl, ctx)
    assert {r["case"] for r in rows} == {"EQX"}        # lateral cases only
    k1 = 12 * E * s1 ** 4 / 12.0 / h1 ** 3
    k2 = 12 * E * s2 ** 4 / 12.0 / h2 ** 3
    r1, r2 = _one(rows, story="Story1"), _one(rows, story="Story2")
    assert r1["shear_x"] == pytest.approx(F1 + F2)
    assert r2["shear_x"] == pytest.approx(F2)
    assert r1["stiff_x"] == pytest.approx(k1, rel=1e-6)
    assert r2["stiff_x"] == pytest.approx(k2, rel=1e-6)
    assert r1["drift_x"] == pytest.approx((F1 + F2) / k1, rel=1e-6)
    assert [r["story"] for r in rows] == ["Story2", "Story1"]   # top first


def test_story_stiffness_matches_engine_block():
    mdl = quick_building(stories=3)
    d, ctx = _solve(mdl)
    rows = _rows("story_stiffness", d, mdl, ctx)
    assert {r["case"] for r in rows} == set(d["story_stiffness"])
    for r in rows:
        ref = d["story_stiffness"][r["case"]][r["story"]]
        assert r["stiff_x"] == ref["kx"] and r["stiff_y"] == ref["ky"]


def test_story_forces_cantilever_shear_and_overturning():
    L, H, P = 4.0, 10.0, 25.0
    mdl = _cantilever(L, 0.4, H, P)
    d, ctx = _solve(mdl)
    rows = _rows("story_forces", d, mdl, ctx)
    bot = _one(rows, location="Bottom")
    top = _one(rows, location="Top")
    for r in (bot, top):
        assert r["P"] == pytest.approx(-P, rel=1e-9)
        assert r["VX"] == pytest.approx(H, rel=1e-9)
        assert abs(r["VY"]) < 1e-9 and abs(r["T"]) < 1e-9
        assert abs(r["MX"]) < 1e-6
        assert r["n_members"] == 1 and r["n_shells"] == 0
    assert bot["MY"] == pytest.approx(H * L, rel=1e-5)
    assert abs(top["MY"]) < 1e-4 * H * L


def test_story_forces_base_cut_equals_minus_base_reaction():
    mdl = quick_building(stories=3)
    d, ctx = _solve(mdl)
    rows = _rows("story_forces", d, mdl, ctx)
    for case in ("DEAD", "EQX", "EQY"):
        r = _one(rows, case=case, story="Story1", location="Bottom")
        base = d["cases"][case]["base"]
        assert r["P"] == pytest.approx(-base["FZ"], rel=1e-6, abs=1e-6)
        assert r["VX"] == pytest.approx(-base["FX"], rel=1e-6, abs=1e-6)
        assert r["VY"] == pytest.approx(-base["FY"], rel=1e-6, abs=1e-6)
        # story shear at the bottom of each story == cumulative applied
        for s in mdl.stories:
            rb = _one(rows, case=case, story=s.name, location="Bottom")
            st = d["cases"][case]["story"][s.name]
            assert rb["VX"] == pytest.approx(st["shear_x"], abs=1e-6)


# --------------------------------------------------------------------------- #
# 4. diaphragm tables
# --------------------------------------------------------------------------- #
def test_symmetric_diaphragm_torsional_ratio_is_one():
    mdl = _diaphragm(num_modes=0)
    d, ctx = _solve(mdl)
    rows = _rows("diaphragm_max_avg_drifts", d, mdl, ctx)
    r = _one(rows, case="EQX", direction="X")
    assert r["disp_ratio"] == pytest.approx(1.0, abs=1e-9)
    assert r["drift_ratio"] == pytest.approx(1.0, abs=1e-9)
    assert not [x for x in rows if x["case"] == "EQX" and
                x["direction"] == "Y"]                      # no Y response


def test_eccentric_diaphragm_torsional_ratio_analytic():
    mdl = _diaphragm(s_south=0.4, s_north=0.6, num_modes=0)
    d, ctx = _solve(mdl)
    hand = _diaphragm_hand(mdl)
    assert hand["ratio"] > 1.1                      # genuinely eccentric
    r = _one(_rows("diaphragm_max_avg_drifts", d, mdl, ctx),
             case="EQX", direction="X")
    assert r["disp_ratio"] == pytest.approx(hand["ratio"], rel=1e-6)
    # single story on a fixed base: drift ratio == displacement ratio
    assert r["drift_ratio"] == pytest.approx(hand["ratio"], rel=1e-6)
    assert r["max_disp"] == pytest.approx(max(map(abs, hand["d"])), rel=1e-6)
    assert r["max_drift"] == pytest.approx(r["max_disp"] / 4.0, rel=1e-9)
    # identical to the engine's ASCE 7 irregularity ratio
    assert r["disp_ratio"] == pytest.approx(
        d["irregularity"]["EQX"]["Story1"]["tors_ratio_x"], rel=1e-9)
    # CR from the engine's unit-load method agrees with the hand CR
    assert d["story_props"]["Story1"]["cr_y"] == pytest.approx(
        hand["ycr"], abs=1e-6)


def test_diaphragm_cm_displacement_rigid_body_at_cm():
    a, b = 3.0, 4.0
    mdl = _diaphragm(s_south=0.4, s_north=0.6, cm_load=(a, b), num_modes=0)
    d, ctx = _solve(mdl)
    sp = d["story_props"]["Story1"]
    assert (sp["cm_x"], sp["cm_y"]) == pytest.approx((a, b))
    for case in ("EQX", "EQY"):
        r = _one(_rows("diaphragm_cm_displacements", d, mdl, ctx), case=case)
        assert (r["x"], r["y"], r["z"]) == pytest.approx((a, b, 4.0))
        # the CM sits on the (a, b) column top: a diaphragm slave
        tag = next(t for t, c in d["nodes"].items()
                   if c == pytest.approx([a, b, 4.0]) and
                   int(t) not in ctx["masters"].values())
        nd = d["cases"][case]["node_disp"][tag]
        assert r["ux"] == pytest.approx(nd[0], rel=1e-9, abs=1e-15)
        assert r["uy"] == pytest.approx(nd[1], rel=1e-9, abs=1e-15)
        assert r["rz"] == pytest.approx(nd[5], rel=1e-9, abs=1e-15)
        if case == "EQX":                  # y-eccentric CR: EQX twists
            assert abs(r["rz"]) > 1e-8
            assert r["ux"] != pytest.approx(
                d["cases"][case]["story"]["Story1"]["ux"], rel=1e-3)


def test_centers_of_mass_and_rigidity_table():
    mdl = quick_building(stories=2)
    d, ctx = _solve(mdl)
    rows = _rows("centers_mass_rigidity", d, mdl, ctx)
    masses = mdl.compute_story_masses()
    for r in rows:
        assert r["mass"] == pytest.approx(masses[r["story"]])
        assert r["cm_x"] == d["story_props"][r["story"]]["cm_x"]


# --------------------------------------------------------------------------- #
# 5. base reactions + equilibrium
# --------------------------------------------------------------------------- #
def _portal():
    h = 3.5
    mdl = BuildingModel(name="portal")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("C", E=E, nu=NU, unit_weight=24.0))
    mdl.set_stories([h])
    mdl.num_modes = 0
    mdl.add_section(FrameSection.rectangular("COL", "C", 0.4, 0.4))
    mdl.add_section(FrameSection.rectangular("BM", "C", 0.3, 0.5))
    pts = [(0, 0), (6, 0), (6, 4), (0, 4)]
    for i, (x, y) in enumerate(pts):
        mdl.add_member("column", "COL", (x, y, 0), (x, y, h), story="Story1",
                       uid=f"C{i}")
        mdl.supports.append(PointSupport((x, y, 0), (1, 1, 1, 1, 1, 1)))
    for i in range(4):
        (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % 4]
        mdl.add_member("beam", "BM", (x1, y1, h), (x2, y2, h),
                       story="Story1", uid=f"B{i}")
    return mdl, h


def test_base_reaction_centroid_of_single_point_load():
    mdl, h = _portal()
    mdl.pattern("PV").member_loads.append(
        MemberLoad("B0", kind="point", w=50.0, a=0.3))        # x=1.8, y=0
    mdl.add_case("PV", {"PV": 1.0})
    mdl.pattern("PH").nodal_loads.append(NodalLoad((6, 4, h), fx=20.0))
    mdl.add_case("PH", {"PH": 1.0})
    d, ctx = _solve(mdl)
    rows = _rows("base_reactions", d, mdl, ctx)
    v = _one(rows, case="PV")
    assert v["FZ"] == pytest.approx(50.0, rel=1e-9)
    assert (v["X"], v["Y"], v["Z"]) == pytest.approx((1.8, 0.0, 0.0),
                                                     abs=1e-6)
    hz = _one(rows, case="PH")
    assert hz["FX"] == pytest.approx(-20.0, rel=1e-9)
    # horizontal resultant: Y and Z locate its line of action
    assert (hz["Y"], hz["Z"]) == pytest.approx((4.0, h), abs=1e-6)
    for k in ("FX", "FY", "FZ", "MX", "MY", "MZ"):
        assert v[k] == d["cases"]["PV"]["base"][k]


def test_resultant_location_helper():
    assert tables.resultant_location([0, 0, 0], [1, 2, 3], 0.0) == \
        (None, None, None)
    # vertical P at (2, -3): reaction (0,0,P), moment (P*y, -P*x, 0)
    P = 7.0
    X, Y, Z = tables.resultant_location([0, 0, P], [P * -3, -P * 2, 0], 1.5)
    assert (X, Y, Z) == pytest.approx((2.0, -3.0, 1.5))


def _equilibrium_model():
    mdl, h = _portal()
    mdl.add_shell_section(ShellSection("SL", "C", 0.2))
    mdl.add_shell_section(ShellSection("WL", "C", 0.25))
    mdl.shells.append(ShellRegion("S1", "slab", "membrane", "SL",
                                  [(0, 0, h), (6, 0, h), (6, 4, h), (0, 4, h)],
                                  story="Story1"))
    mdl.shells.append(ShellRegion("W1", "wall", "shell", "WL",
                                  [(6, 0, 0), (6, 4, 0), (6, 4, h), (6, 0, h)],
                                  mesh_size=1.0, story="Story1"))
    sw = mdl.pattern("SW", "dead")
    sw.self_weight_factor = 1.0
    mdl.add_case("SW", {"SW": 1.0})
    dl = mdl.pattern("DL", "dead")
    dl.area_loads.append(AreaLoad("S1", 3.0))
    dl.member_udls.append(MemberUDL("B1", 4.0))
    dl.member_loads.append(MemberLoad("B2", kind="trapezoid", w=2.0, w2=5.0,
                                      a=0.1, b=0.9))
    mdl.add_case("DL", {"DL": 1.0})
    lat = mdl.pattern("LAT", "wind")
    lat.member_loads.append(MemberLoad("C0", kind="udl", w=3.0,
                                       direction="global_x"))
    lat.member_loads.append(MemberLoad("C3", kind="point", w=7.0, a=0.5,
                                       direction="global_y"))
    lat.member_loads.append(MemberLoad("B0", kind="udl", w=2.0,
                                       direction="local_y"))
    lat.nodal_loads.append(NodalLoad((0, 4, h), fx=-4.0, fy=9.0, fz=2.0))
    lat.story_forces.append(StoryForce("Story1", fx=11.0, fy=-6.0))
    mdl.add_case("LAT", {"LAT": 2.0})
    mdl.add_combo("ULS", {"SW": 1.2, "DL": 1.2, "LAT": 1.0})
    mdl.add_combo("ENV", {"SW": 1.0, "LAT": 1.0}, combo_type="envelope")
    return mdl


def test_load_pattern_equilibrium_error_zero():
    mdl = _equilibrium_model()
    d, ctx = _solve(mdl)
    rows = _rows("load_pattern_summary", d, mdl, ctx)
    assert {r["pattern"] for r in rows} == {"SW", "DL", "LAT"}
    sw = _one(rows, pattern="SW")
    # self-weight: every member A*gamma*L + slab t*gamma*A + wall t*gamma*A
    w = sum(mdl.sections[m.section].A * 24.0 * m.length for m in mdl.members)
    w += 0.2 * 24.0 * 24.0 + 0.25 * 24.0 * (4.0 * 3.5)
    assert sw["FZ"] == pytest.approx(-w, rel=1e-9)
    for r in rows:
        assert r["case"] == r["pattern"]
        assert r["error_pct"] < 1e-6, r
    lat = _one(rows, pattern="LAT")
    assert lat["FX"] == pytest.approx(3.0 * 3.5 - 4.0 + 11.0)
    assert lat["react_FX"] == pytest.approx(-lat["FX"], rel=1e-9)  # /factor


def test_load_case_equilibrium_cases_and_additive_combos():
    mdl = _equilibrium_model()
    d, ctx = _solve(mdl)
    rows = _rows("load_case_equilibrium", d, mdl, ctx)
    names = [r["case"] for r in rows]
    assert names == ["SW", "DL", "LAT", "ULS"]          # envelope skipped
    for r in rows:
        assert r["error_pct"] < 1e-6
    qb = quick_building()
    qd, qctx = _solve(qb)
    q = _one(_rows("load_case_equilibrium", qd, qb, qctx), case="1.2D + 1.6L")
    assert q["error_pct"] < 1e-6 and q["case_type"] == "Combination"


# --------------------------------------------------------------------------- #
# 6. modal tables
# --------------------------------------------------------------------------- #
def test_modal_periods_table():
    mdl = quick_building(stories=2)
    d, ctx = _solve(mdl)
    rows = _rows("modal_periods", d, mdl, ctx)
    assert [r["period"] for r in rows] == d["modal"]["periods"]
    for r, f in zip(rows, d["modal"]["frequencies"]):
        assert r["frequency"] == pytest.approx(f, rel=1e-12)
        assert r["circ_freq"] == pytest.approx(2 * math.pi * f, rel=1e-12)
        assert r["eigenvalue"] == pytest.approx(r["circ_freq"] ** 2,
                                                rel=1e-12)


def test_mass_ratios_and_factors_match_engine_participation():
    mdl = quick_building(stories=3)
    d, ctx = _solve(mdl)
    mr = _rows("modal_mass_ratios", d, mdl, ctx)
    pf = _rows("modal_participation_factors", d, mdl, ctx)
    part = d["modal"]["participation"]
    assert len(mr) == len(pf) == len(part)
    for r, f, p in zip(mr, pf, part):
        assert r["UX"] == pytest.approx(p["ux"], rel=1e-9, abs=1e-15)
        assert r["UY"] == pytest.approx(p["uy"], rel=1e-9, abs=1e-15)
        assert r["RZ"] == pytest.approx(p["rz"], rel=1e-9, abs=1e-15)
        assert f["UX"] == pytest.approx(p["gamma_x"], rel=1e-9, abs=1e-12)
        assert f["UY"] == pytest.approx(p["gamma_y"], rel=1e-9, abs=1e-12)
        w2 = (2 * math.pi / p["T"]) ** 2
        assert f["modal_stiffness"] == pytest.approx(w2 * f["modal_mass"],
                                                     rel=1e-12)
    # cumulative sums: monotone, and all 12 modes of the 4x3-dof diaphragm
    # model capture the whole mass
    for k in ("UX", "UY", "RZ"):
        cum = [r["Sum" + k] for r in mr]
        assert all(b >= a - 1e-15 for a, b in zip(cum, cum[1:]))
        assert cum[-1] == pytest.approx(sum(p[k.lower()] for p in part))
    # UZ: no vertical mass anywhere -> 0
    assert all(r["UZ"] == 0.0 for r in mr)


def test_mass_ratios_full_mode_set_sum_to_one():
    mdl = quick_building(stories=2, bays_x=1, bays_y=1)
    mdl.num_modes = 6                                  # == massed dofs
    d, ctx = _solve(mdl)
    last = _rows("modal_mass_ratios", d, mdl, ctx)[-1]
    for k in ("SumUX", "SumUY", "SumRZ", "SumRX", "SumRY"):
        # eigensolver precision: the engine's own ux/uy sums carry the
        # same ~1e-6 residual
        assert last[k] == pytest.approx(1.0, rel=1e-5), k


def test_direction_factors_sum_to_one():
    mdl = quick_building(stories=3)
    d, ctx = _solve(mdl)
    rows = _rows("modal_direction_factors", d, mdl, ctx)
    assert rows
    for r in rows:
        assert r["UX"] + r["UY"] + r["UZ"] + r["RZ"] == pytest.approx(1.0)
        assert all(0.0 <= r[k] <= 1.0 + 1e-12 for k in ("UX", "UY", "UZ",
                                                          "RZ"))


def test_symmetric_torsional_mode_identified_with_analytic_period():
    m = 50.0
    mdl = _diaphragm(num_modes=3, mass=m)
    d, ctx = _solve(mdl)
    rows = _rows("modal_direction_factors", d, mdl, ctx)
    tors = [r for r in rows if r["dominant"] == "RZ"]
    assert len(tors) == 1
    assert tors[0]["RZ"] == pytest.approx(1.0, abs=1e-9)
    hand = _diaphragm_hand(mdl)
    I_th = m * (6.0 ** 2 + 8.0 ** 2) / 12.0
    T_hand = 2 * math.pi * math.sqrt(I_th / hand["Kth"])
    assert tors[0]["period"] == pytest.approx(T_hand, rel=1e-6)
    for r in rows:
        if r is not tors[0]:
            assert r["RZ"] < 1e-9


def test_eccentric_model_torsional_mode_coupled():
    mdl = _diaphragm(s_south=0.4, s_north=0.6, num_modes=3)
    d, ctx = _solve(mdl)
    rows = _rows("modal_direction_factors", d, mdl, ctx)
    tors = max(rows, key=lambda r: r["RZ"])
    assert tors["dominant"] == "RZ" and tors["RZ"] > 0.5
    # eccentricity along y couples torsion with X translation, not Y
    assert tors["UX"] > 1e-3
    assert tors["UY"] < 1e-9
    ymode = max(rows, key=lambda r: r["UY"])
    assert ymode["UY"] == pytest.approx(1.0, abs=1e-9)
    mr = _rows("modal_mass_ratios", d, mdl, ctx)
    assert _one(mr, mode=tors["mode"])["RZ"] == pytest.approx(
        d["modal"]["participation"][tors["mode"] - 1]["rz"], rel=1e-9)


# --------------------------------------------------------------------------- #
# 7. context fallback, case filters, byte-identity, API
# --------------------------------------------------------------------------- #
def test_fallback_context_reproduces_engine_context():
    mdl = quick_building(stories=2)
    d, ctx = _solve(mdl)
    fb = tables.context_from_results(mdl, d)
    assert fb["masters"] == ctx["masters"]
    got = {(t, k): v for t, k, v in fb["mass"]}
    want = {(t, k): v for t, k, v in ctx["mass"]}
    assert got.keys() == want.keys()
    for key, v in want.items():
        assert got[key] == pytest.approx(v, rel=1e-12)
    for key in ("modal_mass_ratios", "diaphragm_max_avg_drifts",
                "joint_drifts", "story_forces"):
        assert _rows(key, d, mdl, None) == _rows(key, d, mdl, ctx)


def test_case_filter_restricts_rows():
    mdl = quick_building(stories=2)
    d, ctx = _solve(mdl)
    rows = _rows("joint_drifts", d, mdl, ctx, cases=["EQX"])
    assert rows and {r["case"] for r in rows} == {"EQX"}
    assert _rows("base_reactions", d, mdl, ctx, cases=["nope"]) == []


def test_results_untouched_by_table_computation():
    mdl = quick_building(stories=2)
    d, ctx = _solve(mdl)
    before = json.dumps(d, sort_keys=True)
    for t in tables.list_tables():
        tables.compute_table(t["key"], d, mdl, ctx)
    assert json.dumps(d, sort_keys=True) == before


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api import server
    app = server.create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client, server


def test_api_tables_list_and_store_and_compute(api_client, monkeypatch):
    client, server = api_client
    mdl = _shear_building()
    assert client.post("/api/model", json=mdl.to_dict()).status_code == 200
    for method in (client.get, client.post):
        cat = method("/api/tables/list").get_json()["tables"]
        assert [t["key"] for t in cat] == [t["key"] for t in
                                           tables.list_tables()]
    resp = client.post("/api/analyze")
    assert resp.status_code == 200
    analyzed = resp.get_json()
    # byte-identical analyze payload vs a direct engine run
    direct = OpenSeesEngine(BuildingModel.from_dict(mdl.to_dict())).run()
    assert json.dumps(analyzed, sort_keys=True) == \
        json.dumps(json.loads(json.dumps(direct.to_dict())), sort_keys=True)

    # from now on the solver must not run: tables come from the store
    def boom(self):
        raise RuntimeError("solver re-run")
    monkeypatch.setattr(server.OpenSeesEngine, "run", boom)
    out = client.post("/api/tables/story_stiffness", json={}).get_json()
    assert out["key"] == "story_stiffness"
    assert {c["key"] for c in out["columns"]} >= {"stiff_x", "shear_x"}
    k1 = 12 * E * 0.5 ** 4 / 12.0 / 4.0 ** 3
    assert _one(out["rows"], story="Story1")["stiff_x"] == pytest.approx(
        k1, rel=1e-6)
    out = client.post("/api/tables/joint_drifts",
                      json={"cases": ["DEAD"]}).get_json()
    assert {r["case"] for r in out["rows"]} == {"DEAD"}
    # explicit results in the body are used as-is
    out = client.post("/api/tables/base_reactions",
                      json={"results": analyzed}).get_json()
    assert {r["case"] for r in out["rows"]} == {"EQX", "DEAD"}
    # errors
    assert client.post("/api/tables/nope").status_code == 404
    assert client.post("/api/tables/base_reactions",
                       json={"cases": "EQX"}).status_code == 400
    # a changed model invalidates the store -> needs a (here failing) solve
    mdl.pattern("EQX").nodal_loads[0].fx = 99.0
    client.post("/api/model", json=mdl.to_dict())
    r = client.post("/api/tables/base_reactions")
    assert r.status_code == 400 and "solver re-run" in r.get_json()["error"]


def test_api_tables_analyze_on_demand(api_client):
    client, server = api_client
    mdl = _cantilever()
    client.post("/api/model", json=mdl.to_dict())
    server._tables_store.clear()
    out = client.post("/api/tables/joint_drifts").get_json()
    tip = 10.0 * 4.0 ** 3 / (3 * E * 0.4 ** 4 / 12.0)
    assert _one(out["rows"], case="H")["disp_x"] == pytest.approx(tip,
                                                                  rel=1e-9)
    assert server._tables_store.get("results") is not None
