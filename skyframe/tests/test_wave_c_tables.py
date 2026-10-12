"""Wave C: Show Tables results tables + Interactive Database Editing
coverage (core/tables_wave_c.py, core/modeltables_wave_c.py,
core/gendisp.py).

Pinned:
  * every new DB-edit table round-trips byte-identically one by one, all
    together, through CSV and through the zip workbook (rich model, a
    two-tower model, quick_building and an empty model); edits write only
    the edited value, options return to "absent" at their defaults, a
    built-up / Section Designer edit re-derives the mirrored section like
    add_builtup_section / add_designer_section;
  * joint velocities / accelerations == the undamped SDOF closed form
    (harmonic ground motion from rest: transient + steady state);
  * assembled joint masses sum to the mass-source totals;
  * material take-off weights by hand;
  * story max / avg displacement ratio by hand (rigid floor, eccentric
    load, cantilever columns incl. their St Venant torsion);
  * modal load participation (static / dynamic, acceleration and a load
    pattern) == hand values of the 2-DOF cantilever with one mode;
  * a generalized displacement (inter-story drift combination) by hand;
  * nonlinear link rows: static gap force == anchor reaction and the
    closed-form contact force; TH rows == the recorded link series;
  * the ?mock=1 port (js/mock_dbedit_wc.js) produces identical rows and
    round-trips (node, skipped when unavailable).
"""

import copy
import io
import json
import math
import os
import shutil
import subprocess
import zipfile

import numpy as np
import pytest

from skyframe.core import modeltables as MT
from skyframe.core import modeltables_wave_c as WC
from skyframe.core import tables as T
from skyframe.core import gendisp as GD
from skyframe.core import builtup as B
from skyframe.core.builder import quick_building
from skyframe.core.loads_ext import GroundDisplacement
from skyframe.core.model import (BuildingModel, FrameSection, LoadPattern,
                                 Material, NodalLoad, NodalMass,
                                 PointSupport, SectionCut, ShellSection,
                                 ThermalLoad)
from skyframe.core.sections_designer import DesignerSection
from skyframe.core.thermal_ext import ShellThermalLoad, JointTemperature
from skyframe.core import nonprismatic as npx

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

J = json.dumps
NO_DAMP = {"type": "rayleigh_coefficients", "mass_coeff": 0.0,
           "stiffness_coeff": 0.0}
NEW = [t.key for t in MT.catalogue()][MT.table_keys().index(
    "staged_stages"):]


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
def rich_model() -> BuildingModel:
    m = quick_building(bays_x=2, bays_y=1, stories=2)
    m.add_shell_section(ShellSection("SLAB20", "CONC", 0.2))
    z = m.stories[0].elevation
    m.add_shell("slab", "shell", "SLAB20",
                [(0, 0, z), (6, 0, z), (6, 6, z), (0, 6, z)],
                mesh_size=3.0, story=m.stories[0].name, uid="S1")
    m.shells[-1].floor_mesh = {"mode": "cookie_cut", "max_size": 1.5}
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.section_cuts.append(SectionCut("CUT1", "z", 1.5))
    m.section_cuts.append(SectionCut("CUT2", "x", 3.0, y_range=[0, 6]))
    pat = m.patterns["DEAD"]
    pat.thermal_loads.append(ThermalLoad(m.members[0].uid, 20.0, grad3=5.0))
    pat.shell_thermal_loads.append(ShellThermalLoad("S1", 15.0, 2.0))
    pat.joint_temperatures.append(JointTemperature((0, 0, z), 7.5))
    pat.ground_displacements.append(GroundDisplacement((0, 0, 0), uz=-0.01))
    m.pdelta_options = {"method": "iterative_loads",
                        "load_factors": {"DEAD": 1.0, "LIVE": 0.25},
                        "max_iterations": 3, "tolerance": 0.001,
                        "include_in": "all_linear"}
    m.analysis_options = {"solver": "band", "convergence": {"mode": "robust"}}
    m.damping_options = {"rayleigh_on_links": "all",
                         "rayleigh_stiffness_basis": "committed"}
    m.patterns["PT"] = LoadPattern("PT")
    b = next(mm for mm in m.members if mm.kind == "beam")
    m.tendons.append({"uid": "T1", "area": 0.001, "jacking_stress": 1.0e6,
                      "pattern": "PT", "host": [b.uid],
                      "points": [list(b.pi), list(b.pj)]})
    m.add_case("PT", {"PT": 1.0})
    m.hyperstatic_cases["HYP"] = {"case": "PT"}
    m.panel_zones = "rigid"
    m.joint_panel_zones = [{"point": [0, 0, z], "property": "spring",
                            "k": 1e5, "connectivity": "beams_to_panel"}]
    m.add_section(FrameSection.rectangular("D", "CONC", 0.3, 0.8))
    m.add_section(FrameSection.rectangular("S", "CONC", 0.3, 0.5))
    npx.add_nonprismatic_section(m, "NP", [
        {"start_section": "D", "end_section": "S", "EI33": "cubic"}])
    m.add_material(Material("STL", E=2e8, nu=0.3, unit_weight=77.0))
    m.add_builtup_section(B.template_section("double_angle", {},
                                             name="2L", material="STL"))
    m.add_designer_section(DesignerSection(
        "DS", "CONC", polygons=[{"vertices": [[-0.15, -0.3], [0.15, -0.3],
                                              [0.15, 0.3], [-0.15, 0.3]]}],
        rebar=[{"y": 0.1, "z": 0.25, "area": 4.9e-4, "material": "CONC"}]))
    m.joint_patterns["JP"] = {"type": "linear", "a": 0.0, "b": 0.0,
                              "c": 1.0, "d": 0.0}
    m.members[0].hinges = "auto_m3"
    m.add_group("G1", members=[m.members[0].uid])
    m.add_group("G2", members=[m.members[1].uid])
    m.add_staged_case("STG", "DEAD")
    m.add_staged_case("USR", "DEAD")
    m.staged_cases["USR"].stages = [
        {"name": "S1", "duration_days": 7.0, "operations": [
            {"op": "add", "group": "G1", "age_days": 14.0},
            {"op": "load", "group": "G1", "pattern": "DEAD", "scale": 1.0}]},
        {"name": "S2", "duration_days": 0.0, "operations": [
            {"op": "add", "group": "G2"},
            {"op": "remove", "group": "G1"}]}]
    m.staged_cases["USR"].include_live = {"LIVE": 1.0}
    m.staged_cases["STG"].time_dependent = {"days_per_story": 7.0,
                                            "creep_coeff": 2.0,
                                            "aging": True}
    m.members[1].hinge_overwrites = {"auto_subdivide": True,
                                     "relative_length": 0.1}
    z1 = m.stories[1].elevation
    m.generalized_displacements = {"DRIFT": {"type": "translation",
        "terms": [{"point": [0.0, 0.0, z1], "dof": "UX", "sf": 1.0},
                  {"point": [0.0, 0.0, z], "dof": "UX", "sf": -1.0}]}}
    m.validate()
    return BuildingModel.from_dict(m.to_dict())


def tower_model() -> BuildingModel:
    from test_towers import _twin
    m = _twin()
    m.joint_towers = [{"point": [0.0, 0.0, 3.0], "tower": "T1"}]
    m.validate()
    return BuildingModel.from_dict(m.to_dict())


@pytest.fixture(scope="module")
def base():
    return rich_model().to_dict()


@pytest.fixture
def d(base):
    return copy.deepcopy(base)


def rows_of(d, key):
    return json.loads(J(MT.get_table(d, key)["rows"]))


def apply(d, **tables):
    before = J(d)
    m, errs, summary = MT.apply_tables(d, tables)
    assert J(d) == before
    return m, errs, summary


# --------------------------------------------------------------------------- #
# DB-edit: catalogue + round trips
# --------------------------------------------------------------------------- #
def test_catalogue_has_every_wave_c_table(base):
    want = {"staged_stages", "staged_operations", "staged_live",
            "hyperstatic_cases", "section_cuts", "frame_temperature_loads",
            "shell_temperature_loads", "joint_temperatures",
            "ground_displacements", "joint_patterns", "pdelta_options",
            "pdelta_load_factors", "active_dof", "analysis_options",
            "analysis_convergence_tests", "damping_options",
            "panel_zone_option", "joint_panel_zones", "towers",
            "joint_towers", "frame_hinge_assignments",
            "frame_hinge_overwrites", "floor_mesh", "nonprismatic_segments",
            "nonprismatic_subdivisions", "builtup_sections",
            "builtup_components", "designer_sections", "designer_polygons",
            "designer_rebar", "generalized_displacements",
            "generalized_displacement_terms"}
    assert want == set(NEW)
    for k in NEW:
        t = MT.get_table(base, k)
        assert t["group"].startswith("Model Definition")
        for c in t["columns"]:
            assert c["quantity"] in MT.QUANTITIES and c["type"] in \
                MT.COL_TYPES
    st = [c["key"] for c in MT.get_table(base, "staged_cases")["columns"]]
    assert st[:2] == ["name", "pattern"] and "td_days_per_story" in st
    for k in NEW:
        assert MT.get_table(base, k)["rows"] or k in (
            "towers", "joint_towers"), k


@pytest.mark.parametrize("key", NEW + ["staged_cases"])
def test_each_table_round_trips_byte_identically(d, key):
    m, errs, _ = apply(d, **{key: rows_of(d, key)})
    assert errs == []
    assert J(m.to_dict()) == J(d)


@pytest.mark.parametrize("key", NEW + ["staged_cases"])
def test_each_table_csv_round_trip(d, key):
    text = MT.table_to_csv(d, key)
    rows = MT.csv_to_rows(d, key, text)
    m, errs, _ = apply(d, **{key: rows})
    assert errs == []
    assert J(m.to_dict()) == J(d)
    assert MT.table_to_csv(m.to_dict(), key) == text


def test_all_tables_at_once_and_zip(d):
    ed = [t.key for t in MT.catalogue() if t.editable]
    m, errs, _ = apply(d, **{k: rows_of(d, k) for k in ed})
    assert errs == [] and J(m.to_dict()) == J(d)
    m, errs, _ = MT.import_zip(d, MT.export_zip(d))
    assert errs == [] and J(m.to_dict()) == J(d)
    with zipfile.ZipFile(io.BytesIO(MT.export_zip(d))) as z:
        assert {f"{k}.csv" for k in NEW} <= set(z.namelist())


@pytest.mark.parametrize("mk", [tower_model, quick_building, BuildingModel])
def test_round_trip_other_models(mk):
    dd = mk().to_dict()
    ed = [t.key for t in MT.catalogue() if t.editable]
    for k in NEW:
        m, errs, _ = MT.apply_tables(dd, {k: rows_of(dd, k)})
        assert errs == [] and J(m.to_dict()) == J(dd), k
    m, errs, _ = MT.apply_tables(dd, {k: rows_of(dd, k) for k in ed})
    assert errs == [] and J(m.to_dict()) == J(dd)
    m, errs, _ = MT.import_zip(dd, MT.export_zip(dd))
    assert errs == [] and J(m.to_dict()) == J(dd)


def test_tower_tables_rows_and_rename_cascade():
    dd = tower_model().to_dict()
    rows = rows_of(dd, "towers")
    assert [r["name"] for r in rows] == ["T1", "T2"]
    assert rows[0]["grid_system"] == "GA" and rows[0]["stories"] == \
        "A1, A2, A3"
    rows[1]["name"] = "TB"
    m, errs, s = apply(dd, towers=rows)
    assert errs == [] and s["towers"]["renamed"] == {"T2": "TB"}
    md = m.to_dict()
    assert {st.get("tower") for st in md["stories"]} >= {"TB"}
    jt = rows_of(dd, "joint_towers")
    assert jt == [{"_id": 0, "x": 0.0, "y": 0.0, "z": 3.0, "tower": "T1"}]


# --------------------------------------------------------------------------- #
# DB-edit: edits
# --------------------------------------------------------------------------- #
def test_options_tables_edit_and_return_to_default(d):
    rows = rows_of(d, "analysis_options")
    rows[0].update(solver="auto", conv_mode="legacy")
    m, errs, _ = apply(d, analysis_options=rows)
    assert errs == [] and "analysis_options" not in m.to_dict()
    rows = rows_of(d, "pdelta_options")
    rows[0]["max_iterations"] = 7
    m, errs, _ = apply(d, pdelta_options=rows,
                       pdelta_load_factors=[{"pattern": "DEAD",
                                             "factor": 1.2}])
    po = m.to_dict()["pdelta_options"]
    assert po["max_iterations"] == 7 and po["load_factors"] == {"DEAD": 1.2}
    rows = rows_of(d, "active_dof")
    rows[0].update(UZ=False, RX=False, RY=False)
    m, errs, _ = apply(d, active_dof=rows)
    assert m.active_dof == ["UX", "UY", "RZ"]
    rows = rows_of(d, "damping_options")
    rows[0]["rayleigh_on_links"] = "none"
    m, errs, _ = apply(d, damping_options=rows)
    assert errs == [] and "damping_options" not in m.to_dict()
    tests = rows_of(d, "analysis_convergence_tests")
    tests[1]["tolerance"] = 1e-8
    m, errs, _ = apply(d, analysis_convergence_tests=tests)
    conv = m.to_dict()["analysis_options"]["convergence"]
    assert conv["tests"][1] == {"type": "EnergyIncr", "tolerance": 1e-8,
                                "max_iterations": 100}
    assert conv["mode"] == "robust"
    bad = rows_of(d, "analysis_options")
    bad[0]["parallel"] = "nine"
    m, errs, _ = apply(d, analysis_options=bad)
    assert m is None and errs[0]["table"] == "analysis_options"


def test_staged_operations_full_edit(d):
    stages = rows_of(d, "staged_stages")
    stages.append({"case": "USR", "stage": "S3", "duration_days": 30.0})
    ops = rows_of(d, "staged_operations")
    ops[0]["age_days"] = 21.0
    ops.append({"case": "USR", "stage": "S3", "op": "load", "group": "G2",
                "pattern": "LIVE", "scale": 0.5})
    live = [{"case": "USR", "pattern": "LIVE", "factor": 0.3}]
    m, errs, _ = apply(d, staged_stages=stages, staged_operations=ops,
                       staged_live=live)
    assert errs == []
    sc = m.to_dict()["staged_cases"]["USR"]
    assert sc["stages"][0]["operations"][0] == {"op": "add", "group": "G1",
                                                "age_days": 21.0}
    assert sc["stages"][1]["operations"][0] == {"op": "add", "group": "G2"}
    assert sc["stages"][2] == {"name": "S3", "duration_days": 30.0,
                               "operations": [{"op": "load", "group": "G2",
                                               "pattern": "LIVE",
                                               "scale": 0.5}]}
    assert sc["include_live"] == {"LIVE": 0.3}
    # deleting every stage returns the case to per_story
    m, errs, _ = apply(d, staged_stages=[], staged_operations=[])
    assert errs == [] and m.staged_cases["USR"].stages == "per_story"
    # a load operation without a pattern is a per-cell error
    bad = rows_of(d, "staged_operations")
    bad[1]["pattern"] = ""
    m, errs, _ = apply(d, staged_operations=bad)
    assert m is None and (errs[0]["row"], errs[0]["col"]) == (1, "pattern")
    # time-dependent columns
    rows = rows_of(d, "staged_cases")
    i = next(k for k, r in enumerate(rows) if r["name"] == "STG")
    rows[i].update(td_shrinkage=2e-4, td_aging="", td_materials="CONC")
    m, errs, _ = apply(d, staged_cases=rows)
    assert m.staged_cases["STG"].time_dependent == {
        "days_per_story": 7.0, "creep_coeff": 2.0, "shrinkage": 2e-4,
        "materials": ["CONC"]}


def test_load_tables_add_and_delete(d):
    gd = rows_of(d, "ground_displacements")
    gd.append({"pattern": "LIVE", "x": 0, "y": 0, "z": 0, "rz": 0.001})
    ft = rows_of(d, "frame_temperature_loads")
    ft[0]["grad3"] = 0.0
    m, errs, _ = apply(d, ground_displacements=gd,
                       frame_temperature_loads=ft, joint_temperatures=[])
    assert errs == []
    md = m.to_dict()
    assert md["patterns"]["LIVE"]["ground_displacements"] == [
        {"point": [0.0, 0.0, 0.0], "ux": 0.0, "uy": 0.0, "uz": 0.0,
         "rx": 0.0, "ry": 0.0, "rz": 0.001}]
    assert md["patterns"]["DEAD"]["thermal_loads"] == [
        {"member_uid": d["members"][0]["uid"], "dT": 20.0}]
    assert "joint_temperatures" not in md["patterns"]["DEAD"]


def test_section_cut_hinge_floor_mesh_and_gd_edits(d):
    cuts = rows_of(d, "section_cuts")
    cuts[0].update(x_min=0.0, x_max=6.0, group="G1")
    hin = rows_of(d, "frame_hinge_assignments")
    uid1 = d["members"][1]["uid"]
    m, errs, _ = apply(d, section_cuts=cuts, floor_mesh=[],
                       frame_hinge_assignments=hin + [
                           {"frame": uid1, "hinge": "fiber_pmm"}],
                       generalized_displacement_terms=rows_of(
                           d, "generalized_displacement_terms")[:1])
    assert errs == []
    md = m.to_dict()
    assert md["section_cuts"][0]["x_range"] == [0.0, 6.0]
    assert md["section_cuts"][0]["group"] == "G1"
    assert "floor_mesh" not in md["shells"][-1]
    assert md["members"][1]["hinges"] == "fiber_pmm"
    assert len(md["generalized_displacements"]["DRIFT"]["terms"]) == 1


def test_builtup_and_designer_edits_remirror_sections(d):
    comps = rows_of(d, "builtup_components")
    comps[0]["dims"] = "d=0.2; b=0.1016; t=0.0127"
    m, errs, _ = apply(d, builtup_components=comps)
    assert errs == []
    ref = BuildingModel.from_dict(copy.deepcopy(d))
    bs = B.BuiltupSection.from_dict(m.to_dict()["builtup_sections"]["2L"])
    ref.add_builtup_section(bs)
    assert m.sections["2L"].to_dict() == ref.sections["2L"].to_dict()
    assert m.sections["2L"].A != BuildingModel.from_dict(d).sections["2L"].A
    polys = rows_of(d, "designer_polygons")
    polys[0]["vertices"] = "-0.2,-0.3; 0.2,-0.3; 0.2,0.3; -0.2,0.3"
    m, errs, _ = apply(d, designer_polygons=polys)
    assert errs == []
    rb = d["designer_sections"]["DS"]["rebar"][0]
    n = 1.0                       # rebar material = CONC (n = 1)
    assert m.sections["DS"].A == pytest.approx(
        0.4 * 0.6 + (n - 1.0) * rb["area"], rel=1e-12)
    # rename + delete of a mirrored section
    rows = rows_of(d, "designer_sections")
    rows[0]["name"] = "DS2"
    m, errs, _ = apply(d, designer_sections=rows)
    assert errs == [] and "DS2" in m.sections and "DS" not in m.sections
    m, errs, _ = apply(d, designer_sections=[], designer_polygons=[],
                       designer_rebar=[])
    assert errs == [] and "DS" not in m.sections
    used = copy.deepcopy(d)
    used["members"][2]["section"] = "2L"
    m, errs, _ = MT.apply_tables(used, {"builtup_sections": []})
    assert m is None and "2L" in errs[0]["message"]


def test_nonprismatic_segment_edit(d):
    segs = rows_of(d, "nonprismatic_segments")
    segs[0]["EI33"] = "linear"
    sub = rows_of(d, "nonprismatic_subdivisions")
    sub[0]["subdivisions"] = 8
    m, errs, _ = apply(d, nonprismatic_segments=segs,
                       nonprismatic_subdivisions=sub)
    assert errs == []
    s = m.to_dict()["sections"]["NP"]
    assert s["segments"][0]["EI33"] == "linear" and s["subdivisions"] == 8


def test_jsnum_matches_javascript_number_to_string():
    cases = {0.15239999999999998: "0.15239999999999998", 1e-05: "0.00001",
             1.5e-07: "1.5e-7", 2.0: "2", -0.3: "-0.3", 1e21: "1e+21",
             1.5e22: "1.5e+22", 123.456: "123.456", 1e-7: "1e-7",
             -2.5e-6: "-0.0000025", 0.0: "0"}
    for v, s in cases.items():
        assert WC.jsnum(v) == s, v


# --------------------------------------------------------------------------- #
# generalized displacements: model field
# --------------------------------------------------------------------------- #
def test_gendisp_model_field_round_trip_and_validation():
    m = quick_building()
    assert "generalized_displacements" not in m.to_dict()
    m.generalized_displacements = {"G": {"type": "rotation", "terms": [
        {"point": [0, 0, 3.2], "dof": "RZ", "sf": 2.0}]}}
    m.validate()
    d2 = BuildingModel.from_dict(m.to_dict()).to_dict()
    assert d2["generalized_displacements"] == m.to_dict()[
        "generalized_displacements"]
    for bad in ({"type": "x", "terms": [{"point": [0, 0, 0], "dof": "UX"}]},
                {"terms": []},
                {"terms": [{"point": [0, 0], "dof": "UX"}]},
                {"terms": [{"point": [0, 0, 0], "dof": "U9"}]},
                {"terms": [{"point": [0, 0, 0], "dof": "UX",
                            "sf": float("nan")}]},
                {"terms": [{"point": [0, 0, 0], "dof": "UX"}], "x": 1}):
        m.generalized_displacements = {"G": bad}
        with pytest.raises(ValueError):
            m.validate()


# --------------------------------------------------------------------------- #
# results tables
# --------------------------------------------------------------------------- #
def _run(m):
    m.validate()
    eng = OpenSeesEngine(m)
    r = eng.run().to_dict()
    return r, T.engine_context(eng)


def _sdof(acc, dt, L=3.0, E=25e6, M=10.0):
    mdl = BuildingModel(name="sdof")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E, nu=0.2, unit_weight=0.0))
    mdl.set_stories([L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.3, 0.3))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.supports.append(PointSupport((0, 0, L), (0, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=M))
    mdl.num_modes = 1
    mdl.add_th_case("TH", "X", acc, dt, di_damping=NO_DAMP)
    mdl.th_cases["TH"].output_requests = {"joints": [[0, 0, L]]}
    return mdl


def test_joint_velocity_acceleration_sdof_closed_form():
    """Undamped fixed-guided SDOF, a_g = A sin(W t) from rest:
    u = -A/(w^2-W^2) (sin Wt - W/w sin wt); v = u'; a_rel = u'';
    a_abs = a_rel + a_g = -w^2 u (equilibrium)."""
    L, E, M = 3.0, 25e6, 10.0
    k = 12 * E * (0.3 ** 4 / 12) / L ** 3
    w = math.sqrt(k / M)
    W, A, dt, n = 0.5 * w, 1.0, 0.001, 2000
    acc = [A * math.sin(W * i * dt) for i in range(n)]
    mdl = _sdof(acc, dt, L, E, M)
    r, ctx = _run(mdl)
    assert r["modal"]["periods"][0] == pytest.approx(2 * math.pi / w,
                                                     rel=1e-9)
    c = -A / (w * w - W * W)
    u = lambda t: c * (math.sin(W * t) - W / w * math.sin(w * t))  # noqa
    v = lambda t: c * W * (math.cos(W * t) - math.cos(w * t))      # noqa
    a = lambda t: c * (-W * W * math.sin(W * t)                     # noqa
                       + W * w * math.sin(w * t))
    tabs = {k_: T.compute_table(k_, r, mdl, ctx)
        for k_ in ("joint_velocities", "joint_velocities_absolute",
                   "joint_accelerations", "joint_accelerations_absolute")}
    st = {k_: [x for x in t["rows"] if x["step_type"] == "Step"][:n]
          for k_, t in tabs.items()}
    assert len(st["joint_velocities"]) == n
    ts = [x["time"] for x in st["joint_velocities"]]
    assert ts[:3] == [0.0, 0.001, 0.002]
    vmax = max(abs(v(t)) for t in ts)
    amax = max(abs(a(t)) for t in ts)
    for x in st["joint_velocities"]:
        assert x["UX"] == pytest.approx(v(x["time"]), abs=0.01 * vmax)
    for x in st["joint_accelerations"]:
        assert x["UX"] == pytest.approx(a(x["time"]), abs=0.01 * amax)
    pf = r["th_cases"]["TH"]["plot_functions"]["joints"][0]
    for i, x in enumerate(st["joint_accelerations_absolute"]):
        ag = A * math.sin(W * x["time"])
        assert x["UX"] == pytest.approx(
            st["joint_accelerations"][i]["UX"] + ag, abs=1e-12)
        assert x["UX"] == pytest.approx(-w * w * pf["disp"]["UX"][i],
                                        abs=1e-9)
    # absolute velocity = relative + exact ground velocity (A/W)(1-cos Wt)
    for i in (500, 1500):
        x = st["joint_velocities_absolute"][i]
        vg = A / W * (1 - math.cos(W * x["time"]))
        assert x["UX"] == pytest.approx(st["joint_velocities"][i]["UX"] + vg,
                                        abs=1e-4 * A / W)
    mx = [x for x in tabs["joint_velocities"]["rows"]
          if x["step_type"] == "Max"][0]
    assert mx["UX"] == max(pf["vel"]["UX"]) and mx["story"] == "Story1"


def test_joint_th_tables_warn_without_recorded_joints():
    m = _sdof([0.0, 1.0, 0.0], 0.01)
    m.th_cases["TH"].output_requests = None
    r, ctx = _run(m)
    t = T.compute_table("joint_velocities", r, m, ctx)
    assert t["rows"] == [] and "Output Requests" in t["warnings"][0]


def test_assembled_masses_sum_to_mass_source_totals():
    m = quick_building(bays_x=2, bays_y=2, stories=3)
    r, ctx = _run(m)
    t = T.compute_table("assembled_joint_masses", r, m, ctx)
    tot = sum(m.compute_story_masses().values())
    assert t["rows"][-1]["label"] == "Sum"
    assert t["rows"][-1]["UX"] == pytest.approx(tot, rel=1e-12)
    assert sum(x["UX"] for x in t["rows"][:-1]) == pytest.approx(tot,
                                                                rel=1e-12)
    ms = T.compute_table("mass_summary_by_story", r, m, ctx)["rows"]
    sm = m.compute_story_masses()
    assert {x["story"]: x["UX"] for x in ms} == pytest.approx(sm, rel=1e-12)
    md = T.compute_table("mass_summary_by_diaphragm", r, m, ctx)["rows"]
    assert {x["story"]: x["mass_x"] for x in md} == pytest.approx(sm)
    # distributed (not lumped) lateral + vertical mass, plus a joint mass
    m2 = quick_building(bays_x=2, bays_y=1, stories=2)
    m2.mass_options = {"self_mass": True, "patterns": True,
                       "include_lateral": True, "include_vertical": True,
                       "lump_at_stories": False}
    m2.nodal_masses.append(NodalMass((0, 0, m2.stories[0].elevation),
                                     mx=3.0, my=2.0, mz=1.0))
    m2.add_group("COLS", members=[x.uid for x in m2.members
                                  if x.kind == "column"])
    r2, ctx2 = _run(m2)
    t2 = T.compute_table("assembled_joint_masses", r2, m2, ctx2)["rows"]
    tot2 = sum(m2.compute_story_masses().values())
    assert t2[-1]["UX"] == pytest.approx(tot2 + 3.0, rel=1e-9)
    assert t2[-1]["UZ"] == pytest.approx(tot2 + 1.0, rel=1e-9)
    assert len(t2) > 3
    g = T.compute_table("mass_summary_by_group", r2, m2, ctx2)["rows"]
    want = sum(x["UZ"] for x in t2[:-1]
               if x["label"] != "Diaphragm master")
    assert g[0]["group"] == "COLS" and g[0]["UZ"] == pytest.approx(
        want, rel=1e-12)


def test_material_takeoff_by_hand():
    m = BuildingModel(name="mto")
    m.add_material(Material("CONC", E=25e6, nu=0.2, unit_weight=24.0))
    m.add_material(Material("STL", E=2e8, nu=0.3, unit_weight=77.0))
    m.set_stories([3.0, 3.0])
    m.add_section(FrameSection.rectangular("C50", "CONC", 0.5, 0.5))
    m.add_section(FrameSection("W", "STL", A=0.01, I33=1e-4, I22=1e-5,
                               J=1e-6))
    m.sections["W"].mod_weight = 1.1
    m.add_shell_section(ShellSection("SL", "CONC", 0.2))
    for x in (0.0, 6.0):
        for s, (z0, z1) in enumerate(((0, 3), (3, 6))):
            m.add_member("column", "C50", (x, 0, z0), (x, 0, z1),
                         story=f"Story{s + 1}")
    m.add_member("beam", "W", (0, 0, 3), (6, 0, 3), story="Story1")
    m.add_member("beam", "W", (0, 0, 6), (6, 0, 6), story="Story2")
    m.add_shell("slab", "shell", "SL", [(0, 0, 6), (6, 0, 6), (6, 4, 6),
                                       (0, 4, 6)], story="Story2", uid="F1")
    m.validate()
    r = {"nodes": {}}
    bs = {(x["section"], x["object_type"]): x for x in T.compute_table(
        "material_list_by_section", r, m)["rows"]}
    col = bs[("C50", "Column", )] if ("C50", "Column") in bs else None
    assert col["pieces"] == 4 and col["length"] == pytest.approx(12.0)
    assert col["volume"] == pytest.approx(0.25 * 12.0)
    assert col["weight"] == pytest.approx(0.25 * 12.0 * 24.0)
    w = bs[("W", "Beam")]
    assert w["weight"] == pytest.approx(0.01 * 12.0 * 77.0 * 1.1)
    fl = bs[("SL", "Floor")]
    assert fl["area"] == pytest.approx(24.0)
    assert fl["weight"] == pytest.approx(24.0 * 0.2 * 24.0)
    tot = bs[("Total", "")]
    assert tot["weight"] == pytest.approx(72.0 + 10.164 + 115.2)
    st = {(x["story"], x["object_type"]): x for x in T.compute_table(
        "material_list_by_story", r, m)["rows"]}
    assert st[("Story2", "Floor")]["weight"] == pytest.approx(115.2)
    assert st[("Story1", "Column")]["weight"] == pytest.approx(36.0)
    mt = {(x["material"], x["object_type"]): x for x in T.compute_table(
        "material_list_by_material", r, m)["rows"]}
    assert mt[("CONC", "Column")]["volume"] == pytest.approx(3.0)
    assert mt[("STL", "Beam")]["weight"] == pytest.approx(10.164)


def _four_column_story(P=100.0, a=6.0, h=3.0, b=0.4):
    m = BuildingModel(name="tors")
    m.add_material(Material("C", E=25e6, nu=0.2, unit_weight=0.0))
    m.set_stories([h])
    m.add_section(FrameSection.rectangular("COL", "C", b, b))
    for x in (0.0, a):
        for y in (0.0, a):
            m.add_member("column", "COL", (x, y, 0), (x, y, h),
                         story="Story1")
    m.pattern("EX", "quake").nodal_loads.append(NodalLoad((0, 0, h),
                                                          fx=P))
    m.add_case("EX", {"EX": 1.0})
    m.num_modes = 0
    return m


def test_story_max_avg_displacement_ratio_by_hand():
    """Rigid floor on 4 cantilever columns, P at corner (0, 0):
    u0 = P / (4k), theta = (P a/2) / (2 k a^2 + 4 GJ/h) with k = 3EI/h^3;
    max = u0 + theta a/2, min = u0 - theta a/2, ratio = max / u0."""
    P, a, h, b = 100.0, 6.0, 3.0, 0.4
    m = _four_column_story(P, a, h, b)
    r, ctx = _run(m)
    sec = m.sections["COL"]
    E, G = 25e6, 25e6 / 2.4
    k = 3 * E * sec.I33 / h ** 3
    th = (P * a / 2) / (2 * k * a * a + 4 * G * sec.J / h)
    u0 = P / (4 * k)
    rows = T.compute_table("story_max_avg_displacements", r, m, ctx)["rows"]
    x = next(q for q in rows if q["direction"] == "X")
    assert x["max_disp"] == pytest.approx(u0 + th * a / 2, rel=1e-6)
    assert x["min_disp"] == pytest.approx(u0 - th * a / 2, rel=1e-6)
    assert x["avg_disp"] == pytest.approx(u0, rel=1e-6)
    assert x["ratio"] == pytest.approx(1 + th * a / (2 * u0), rel=1e-6)
    y = next(q for q in rows if q["direction"] == "Y")
    assert y["max_disp"] == pytest.approx(th * a / 2, rel=1e-6)
    assert y["ratio"] == pytest.approx(1.0, rel=1e-9)


def test_modal_load_participation_two_dof_hand_values():
    E, size, L, m1, m2 = 25e6, 0.3, 6.0, 8.0, 5.0
    mdl = BuildingModel(name="2DOF")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E, nu=0.2))
    mdl.set_stories([L / 2, L / 2])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L / 2),
                   story="Story1", uid="C1")
    mdl.add_member("column", "COL", (0, 0, L / 2), (0, 0, L),
                   story="Story2", uid="C2")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, L / 2), mx=m1))
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=m2))
    mdl.pattern("PX", "other").nodal_loads.append(
        NodalLoad((0, 0, L), fx=1.0))
    mdl.add_case("PX", {"PX": 1.0})
    mdl.num_modes = 1
    r, ctx = _run(mdl)
    rows = {(x["item_type"], x["item"]): x for x in T.compute_table(
        "modal_load_participation", r, mdl, ctx)["rows"]}
    EI = E * size ** 4 / 12.0
    f = lambda x, y: x * x * (3 * y - x) / (6 * EI)  # noqa: E731
    F = np.array([[f(L / 2, L / 2), f(L / 2, L)], [f(L / 2, L), f(L, L)]])
    K = np.linalg.inv(F)
    Mm = np.diag([m1, m2])
    lam, vec = np.linalg.eig(np.linalg.solve(Mm, K))
    i = int(np.argmin(lam))
    phi = vec[:, i]
    mi = phi @ Mm @ phi
    ra = Mm @ np.ones(2)
    acc = rows[("Acceleration", "UX")]
    assert acc["static"] == pytest.approx(
        100 * (phi @ ra) ** 2 / (lam[i] * mi) / (ra @ F @ ra), rel=1e-6)
    assert acc["dynamic"] == pytest.approx(
        100 * (phi @ ra) ** 2 / mi / (ra @ np.linalg.solve(Mm, ra)),
        rel=1e-6)
    rp = np.array([0.0, 1.0])
    px = rows[("Load Pattern", "PX")]
    assert px["static"] == pytest.approx(
        100 * (phi @ rp) ** 2 / (lam[i] * mi) / (rp @ F @ rp), rel=1e-6)
    assert px["dynamic"] == pytest.approx(
        100 * (phi @ rp) ** 2 / mi / (rp @ np.linalg.solve(Mm, rp)),
        rel=1e-6)
    assert rows[("Acceleration", "UZ")]["dynamic"] == 0.0
    # the stored results block is used when present (no re-solve)
    r["modal"]["load_participation"] = {"acceleration": {"UX": {
        "static": 1.0, "dynamic": 2.0}}, "patterns": {}}
    t = T.compute_table("modal_load_participation", r, mdl, ctx)["rows"]
    assert t == [{"case": "Modal", "item_type": "Acceleration", "item": "UX",
                  "static": 1.0, "dynamic": 2.0}]


def test_generalized_displacement_drift_by_hand():
    m = quick_building(bays_x=1, bays_y=1, stories=2)
    z1, z2 = m.stories[0].elevation, m.stories[1].elevation
    h = z2 - z1
    m.generalized_displacements = {
        "DRIFT2": {"type": "translation", "terms": [
            {"point": [0, 0, z2], "dof": "UX", "sf": 1.0 / h},
            {"point": [0, 0, z1], "dof": "UX", "sf": -1.0 / h}]},
        "TWIST": {"type": "rotation", "terms": [
            {"point": [0, 0, z2], "dof": "RZ", "sf": 1.0}]}}
    r, ctx = _run(m)
    rows = T.compute_table("generalized_displacements", r, m, ctx)["rows"]
    tag = {tuple(round(c, 6) for c in xyz): t
           for t, xyz in r["nodes"].items()}
    u = r["cases"]["EQX"]["node_disp"]
    hand = (u[tag[(0.0, 0.0, z2)]][0] - u[tag[(0.0, 0.0, z1)]][0]) / h
    row = next(x for x in rows if x["name"] == "DRIFT2" and
               x["case"] == "EQX")
    assert row["translation"] == pytest.approx(hand, rel=1e-12, abs=0)
    assert row["rotation"] is None and row["type"] == "Translation"
    st = r["cases"]["EQX"]["story"]["Story2"]["drift_x"]
    assert row["translation"] == pytest.approx(st, rel=1e-9)
    tw = next(x for x in rows if x["name"] == "TWIST" and x["case"] == "EQX")
    assert tw["rotation"] == pytest.approx(u[tag[(0.0, 0.0, z2)]][5],
                                           abs=1e-15)
    # combos are linear: DEAD + EQX
    cb = [x for x in rows if x["case_type"] == "Combination"]
    for x in cb:
        f = m.combos[x["case"]].cases
        want = sum(fac * next(y["translation"] for y in rows
                              if y["name"] == x["name"] and y["case"] == cn)
                   for cn, fac in f.items()) if x["name"] == "DRIFT2" \
            else x["rotation"]
        if x["name"] == "DRIFT2":
            assert x["translation"] == pytest.approx(want, rel=1e-9,
                                                     abs=1e-15)


def _gap_model(gap=0.001, kg=1e6, P=50.0, Lb=4.0):
    m = BuildingModel(name="gap")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E=25e6, nu=0.2, unit_weight=0.0))
    m.set_stories([3.0])
    m.add_section(FrameSection.rectangular("B", "C", 0.3, 0.5))
    m.add_member("beam", "B", (0, 0, 3), (Lb, 0, 3), story="Story1",
                 uid="B1")
    m.supports.append(PointSupport((0, 0, 3), (1, 1, 1, 1, 1, 1)))
    m.add_link((Lb, 0, 3), (Lb, 0, 2.5), uid="G1", link_type="gap",
               params={"k": kg, "gap": gap})
    m.pattern("P", "live").nodal_loads.append(NodalLoad((Lb, 0, 3),
                                                        fz=-P))
    m.add_case("P", {"P": 1.0})
    m.num_modes = 0
    return m


def test_nonlinear_link_rows_static_gap_closed_form():
    gap, kg, P, Lb = 0.001, 1e6, 50.0, 4.0
    m = _gap_model(gap, kg, P, Lb)
    r, ctx = _run(m)
    t = T.compute_table("element_forces_links", r, m, ctx)
    rows = [x for x in t["rows"] if x["link"] == "G1" and x["case"] == "P"]
    assert len(rows) == 2 and not any("device links" in w
                                      for w in t["warnings"])
    kb = 3 * 25e6 * (0.3 * 0.5 ** 3 / 12) / Lb ** 3
    delta = (P + kg * gap) / (kb + kg)          # tip deflection (closed)
    F = kg * (delta - gap)                      # contact force
    assert rows[0]["P"] == pytest.approx(-F, rel=1e-6)
    anchor = next(tg for tg, xyz in r["nodes"].items()
                  if np.allclose(xyz, (Lb, 0, 2.5)))
    assert rows[0]["P"] == pytest.approx(
        -r["cases"]["P"]["reactions"][anchor][2], rel=1e-9)
    assert rows[0]["V2"] == 0.0 and t["columns"] == T.list_tables()[
        [x["key"] for x in T.list_tables()].index(
            "element_forces_links")]["columns"]


def test_nonlinear_link_rows_from_recorded_time_history():
    m = _gap_model()
    m.nodal_masses.append(NodalMass((4.0, 0, 3), mx=2.0, mz=2.0))
    m.num_modes = 1
    m.add_th_case("TH", "X", [0.0, 5.0, -5.0, 2.0, 0.0], 0.01,
                  di_damping=NO_DAMP, gravity={"P": 1.0})
    m.th_cases["TH"].output_requests = {"links": ["G1"]}
    r, ctx = _run(m)
    t = T.compute_table("element_forces_links", r, m, ctx)
    assert [c["key"] for c in t["columns"]][-3:] == ["step_type", "step",
                                                    "time"]
    th = [x for x in t["rows"] if x["case"] == "TH"]
    pf = r["th_cases"]["TH"]["plot_functions"]["links"]["G1"]
    lab = pf["components"][0]
    ser = pf["force"][lab]
    steps = [x for x in th if x["step_type"] == "Step"]
    assert [x["P"] for x in steps] == ser
    assert min(ser) < 0.0                  # the gap is closed under gravity
    assert th[0]["step_type"] == "Max" and th[0]["P"] == max(ser)


def test_new_tables_registered_in_catalogue():
    keys = [t["key"] for t in T.list_tables()]
    for k in ("joint_velocities", "joint_velocities_absolute",
              "joint_accelerations", "joint_accelerations_absolute",
              "assembled_joint_masses", "mass_summary_by_story",
              "mass_summary_by_diaphragm", "mass_summary_by_group",
              "material_list_by_section", "material_list_by_story",
              "material_list_by_material", "story_max_avg_displacements",
              "modal_load_participation", "generalized_displacements"):
        assert k in keys and k in T.REGISTERED
    assert keys.index("story_max_avg_displacements") == \
        keys.index("story_stiffness") + 1


# --------------------------------------------------------------------------- #
# ?mock=1 parity (node)
# --------------------------------------------------------------------------- #
WEB = os.path.join(os.path.dirname(__file__), "..", "skyframe", "api",
                   "web", "js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not found")
def test_mock_db_tables_identical_rows_and_round_trip(tmp_path, base):
    dd = copy.deepcopy(base)
    tw = tower_model().to_dict()
    py = {"rich": {k: MT.get_table(dd, k) for k in NEW + ["staged_cases"]},
          "tower": {k: MT.get_table(tw, k) for k in NEW + ["staged_cases"]}}
    (tmp_path / "models.json").write_text(J({"rich": dd, "tower": tw}))
    script = tmp_path / "parity.mjs"
    url = "file://" + os.path.abspath(os.path.join(WEB, "mock_dbedit.js"))
    script.write_text(f"""
import fs from "node:fs";
const M = await import({J(url)});
const models = JSON.parse(fs.readFileSync({J(str(tmp_path / "models.json"))}));
const keys = {J(NEW + ["staged_cases"])};
const out = {{}};
for (const [name, m] of Object.entries(models)) {{
  out[name] = {{}};
  for (const k of keys) {{
    const t = await M.mockDbEdit(m, "/api/modeltables/" + k, {{model: m}});
    out[name][k] = {{rows: t.rows, columns: t.columns}};
    const r = await M.mockDbEdit(m, "/api/modeltables/" + k + "/apply", {{model: m, rows: JSON.parse(JSON.stringify(t.rows))}});
    out[name][k].rt = r.ok === true && JSON.stringify(r.model) === JSON.stringify(m);
    const csv = await M.mockDbEdit(m, "/api/modeltables/" + k + "/csv", {{model: m}});
    const parsed = await M.mockDbEdit(m, "/api/modeltables/" + k + "/csv_import", {{model: m, csv: csv.csv, apply: true}});
    out[name][k].csv = parsed.ok === true && JSON.stringify(parsed.model) === JSON.stringify(m);
  }}
}}
console.log(JSON.stringify(out));
""")
    res = subprocess.run(["node", str(script)], capture_output=True,
                         text=True, timeout=120)
    assert res.returncode == 0, res.stderr[-3000:]
    js = json.loads(res.stdout.strip().splitlines()[-1])
    for name in ("rich", "tower"):
        for k in NEW + ["staged_cases"]:
            got = js[name][k]
            want = py[name][k]
            assert got["rows"] == json.loads(J(want["rows"])), (name, k)
            assert [c["key"] for c in got["columns"]] == [
                c["key"] for c in want["columns"]], (name, k)
            assert [(c["unit"], c["type"], c.get("enum"))
                    for c in got["columns"]] == [
                (c["unit"], c["type"], c.get("enum"))
                for c in json.loads(J(want["columns"]))], (name, k)
            assert got["rt"], (name, k, "apply round trip")
            assert got["csv"], (name, k, "csv round trip")
