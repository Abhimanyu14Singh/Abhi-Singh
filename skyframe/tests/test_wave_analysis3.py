"""v1.13 analysis-control features (CONTRACT.md "v1.13 additions").

1. Set Load Cases to Run   — ``cases_not_run`` + ``case_status`` /
   ``combo_status`` (skips, dependencies run anyway, combos skipped).
2. Set Active DOF          — ``active_dof``: a 2D portal solved in the XZ
   plane equals the full-3D solve and the slope-deflection closed form;
   out-of-plane DOFs are exactly zero; modal returns in-plane modes only.
3. Mass Source options     — ``mass_options``: self/pattern split, vertical
   mass (sqrt(k/m) hand case), tributary vs story lumping (same total).
4. Material stress-strain  — ``Material.stress_strain``: pure-Python
   backbones vs the OpenSees uniaxial materials, Mander / Kent-Park /
   Steel01 / Park / user closed forms, RC fiber section capacity vs the
   Whitney hand calc, engine wiring, ``POST /api/materials/curve``.
5. Display units           — ``display_units`` + ``GET /api/units``.
"""

import math
import warnings

import openseespy.opensees as ops
import pytest

from skyframe.core.builder import quick_building
from skyframe.core.model import (ACTIVE_DOF_PRESETS, DISPLAY_UNITS,
                                 G_ACCEL, MASS_OPTION_DEFAULTS, MODAL_CASE,
                                 BuildingModel, FrameSection, LoadPattern,
                                 Material, MemberUDL, NodalLoad, NodalMass,
                                 PointSupport)
from skyframe.core.stress_strain import (HYSTERESIS_RULES, material_law,
                                         park_steel_stress)
from skyframe.core.units import UNIT_SETS, units_table
from skyframe.engine.opensees_engine import OpenSeesEngine

FIX = (1, 1, 1, 1, 1, 1)
E_S = 2.0e8                     # kPa
H, L = 3.0, 5.0                 # portal height / span (m)
IC, IB = 2.0e-4, 4.0e-4         # column / beam I (m^4)
P_LAT = 10.0                    # kN


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api import server
    saved = server._state["model"]
    app = server.create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client
    server._state["model"] = saved


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _portal(active=None, dia=True, plane="XZ", masses=0.0):
    """Fixed-base portal (2 columns + beam) in the XZ (or YZ) plane with
    huge axial areas (axial deformation negligible) and a lateral P at the
    top-left joint."""
    m = BuildingModel("portal")
    m.rigid_diaphragms = dia
    m.add_material(Material("S", E_S, 0.3, 0.0, material_type="steel"))
    m.add_section(FrameSection("COL", "S", 100.0, IC, IC, 1e-4))
    m.add_section(FrameSection("BM", "S", 100.0, IB, IB, 1e-4))
    m.set_stories([H])

    def p(a, z):
        return (a, 0.0, z) if plane == "XZ" else (0.0, a, z)
    m.add_member("column", "COL", p(0, 0), p(0, H), story="Story1", uid="C1")
    m.add_member("column", "COL", p(L, 0), p(L, H), story="Story1", uid="C2")
    m.add_member("beam", "BM", p(0, H), p(L, H), story="Story1", uid="B1")
    m.supports.append(PointSupport(p(0, 0), FIX))
    m.supports.append(PointSupport(p(L, 0), FIX))
    pat = m.pattern("LAT", "other")
    pat.nodal_loads.append(NodalLoad(
        p(0, H), fx=P_LAT if plane == "XZ" else 0.0,
        fy=P_LAT if plane == "YZ" else 0.0))
    m.add_case("LAT", {"LAT": 1.0})
    if masses:
        for a in (0, L):
            m.nodal_masses.append(NodalMass(p(a, H), mx=masses, my=masses,
                                            mz=0.0))
    if active is not None:
        m.active_dof = list(active)
    m.validate()
    return m


def _portal_sway():
    """Slope-deflection closed form of the fixed-base portal sway
    (antisymmetric joint rotation theta, axially rigid members):
        (4EIc/h + 6EIb/L) theta = (6EIc/h^2) D
        P/2 = (12EIc/h^3) D - (6EIc/h^2) theta"""
    kc = E_S * IC
    c = 6.0 * kc / H ** 2
    kj = 4.0 * kc / H + 6.0 * E_S * IB / L
    return (P_LAT / 2.0) / (12.0 * kc / H ** 3 - c * c / kj)


def _tag_at(eng, pt):
    if eng._asm is None:
        eng._build()
    for t, c in eng._asm.struct_coords.items():
        if all(abs(a - b) < 1e-9 for a, b in zip(c, pt)):
            return t
    raise KeyError(pt)


def _loaded_model():
    """quick building + one of every case kind (small)."""
    m = quick_building(bays_x=1, bays_y=1, stories=2)
    m.add_rs_case("RSX", "X", spectrum=[[0.0, 1.0], [1.0, 2.0], [4.0, 0.5]])
    m.add_rs_case("RSY", "Y", spectrum=[[0.0, 1.0], [1.0, 2.0], [4.0, 0.5]])
    m.add_rs_combo("RSXY", "RSX", "RSY")
    m.add_th_case("THX", "X", accel=[0.0, 1.0, -1.0, 0.5, 0.0] * 4, dt=0.01)
    m.add_pushover_case("PO", "X", gravity={"DEAD": 1.0},
                        target_drift=0.005, steps=5, default_My=500.0)
    m.add_staged_case("SEQ", pattern="DEAD")
    m.add_buckling_case("BK", gravity={"DEAD": 1.0}, num_modes=2,
                        base_case="DEAD")
    m.add_combo("D+L", {"DEAD": 1.2, "LIVE": 1.6})
    m.add_combo("D+EQ", {"DEAD": 1.0, "EQX": 1.0})
    m.validate()
    return m


def _mono(law, strains, sub=100):
    """Drive a fresh OpenSees uniaxial material monotonically from 0."""
    ops.wipe()
    ops.model("basic", "-ndm", 1, "-ndf", 1)
    law.ops_material(ops, 1)
    ops.testUniaxialMaterial(1)
    out, cur = [], 0.0
    for e in strains:
        for k in range(1, sub + 1):
            ops.setStrain(cur + (e - cur) * k / sub)
        cur = e
        out.append(ops.getStress())
    return out


def _conc(ss, fc=30_000.0, E=25e6):
    return Material("C", E, 0.2, 24.0, material_type="concrete", fc=fc,
                    stress_strain=ss)


def _rebar(ss, fy=420_000.0, fu=620_000.0):
    return Material("R", 2e8, 0.3, 77.0, material_type="rebar", fy=fy,
                    fu=fu, stress_strain=ss)


# =========================================================================== #
# 1. Set Load Cases to Run
# =========================================================================== #
def test_cases_not_run_default_roundtrip_and_full_status():
    m = _loaded_model()
    d = m.to_dict()
    assert d["cases_not_run"] == []
    back = BuildingModel.from_dict(d)
    assert back.cases_not_run == []
    r = OpenSeesEngine(m).run()
    kinds = m.case_kinds()
    assert set(r.case_status) == set(kinds)
    assert MODAL_CASE in r.case_status
    assert all(v == "finished" for v in r.case_status.values())
    assert set(r.combo_status) == set(m.combos) | {"RSXY"}
    assert all(v == "finished" for v in r.combo_status.values())
    out = r.to_dict()
    assert out["case_status"] == r.case_status
    assert out["combo_status"] == r.combo_status


def test_cases_not_run_unknown_name_rejected():
    m = quick_building(bays_x=1, bays_y=1, stories=1)
    m.cases_not_run = ["NOPE"]
    with pytest.raises(ValueError, match="unknown case"):
        m.validate()
    d = quick_building(bays_x=1, bays_y=1, stories=1).to_dict()
    d["cases_not_run"] = ["DEAD", "DEAD"]
    with pytest.raises(ValueError, match="duplicate"):
        BuildingModel.from_dict(d)


def test_static_case_not_run_skips_case_and_its_combos():
    full = OpenSeesEngine(_loaded_model()).run()
    m = _loaded_model()
    m.cases_not_run = ["LIVE"]
    m.validate()
    r = OpenSeesEngine(m).run()
    assert "LIVE" not in r.cases
    assert r.case_status["LIVE"] == "not_run"
    assert r.case_status["DEAD"] == "finished"
    assert r.combo_status["D+L"] == "skipped"
    assert "D+L" not in r.combos
    assert r.combo_status["D+EQ"] == "finished"
    assert "D+L" in r.warning and "skipped" in r.warning
    # the cases that did run are bit-identical to the full run
    for name in ("DEAD", "EQX"):
        assert r.cases[name].node_disp == full.cases[name].node_disp
    assert r.combos["D+EQ"].node_disp == full.combos["D+EQ"].node_disp


def test_rs_case_runs_modal_as_dependency():
    m = _loaded_model()
    m.cases_not_run = [MODAL_CASE]
    m.validate()
    r = OpenSeesEngine(m).run()
    assert r.case_status[MODAL_CASE] == "run_as_dependency"
    assert r.modal.periods                    # results reported
    assert "depends on it" in r.warning


def test_modal_not_run_without_dependents_is_empty():
    m = quick_building(bays_x=1, bays_y=1, stories=1)
    m.cases_not_run = [MODAL_CASE]
    m.validate()
    r = OpenSeesEngine(m).run()
    assert r.case_status[MODAL_CASE] == "not_run"
    assert r.modal.periods == []


def test_buckling_base_case_runs_as_dependency_with_results():
    m = _loaded_model()
    m.cases_not_run = ["DEAD"]
    m.validate()
    r = OpenSeesEngine(m).run()
    assert r.case_status["DEAD"] == "run_as_dependency"
    assert "DEAD" in r.cases                  # it ran -> it reports
    assert "BK" in r.buckling
    # combos may use a dependency-run case
    assert r.combo_status["D+L"] == "finished"


def test_nonlinear_and_dynamic_cases_not_run():
    m = _loaded_model()
    m.cases_not_run = ["PO", "SEQ", "THX", "RSY", "BK"]
    m.validate()
    r = OpenSeesEngine(m).run()
    for n in ("PO", "SEQ", "THX", "RSY", "BK"):
        assert r.case_status[n] == "not_run"
    assert r.pushover == {} and r.staged == {} and r.th_cases == {}
    assert r.buckling == {}
    assert "RSY" not in r.rs_cases and "RSX" in r.rs_cases
    assert r.combo_status["RSXY"] == "skipped"      # RS directional combo
    assert "RSXY" not in r.rs_cases


def test_failed_case_is_reported_not_raised():
    """A TH case on a massless model raises inside run_time_history; run()
    reports it "failed" and still finishes the static case."""
    m = _portal()
    m.add_th_case("TH", "X", accel=[0.0, 1.0, 0.0], dt=0.01)
    m.validate()
    r = OpenSeesEngine(m).run()
    assert r.case_status["TH"] == "failed"
    assert r.case_status["LAT"] == "finished"
    assert "TH" in r.warning and "failed" in r.warning
    assert "LAT" in r.cases


def test_api_analyze_reports_case_status(api_client):
    d = quick_building(bays_x=1, bays_y=1, stories=1).to_dict()
    d["cases_not_run"] = ["LIVE"]
    assert api_client.post("/api/model", json=d).status_code == 200
    out = api_client.post("/api/analyze").get_json()
    assert out["case_status"]["LIVE"] == "not_run"
    assert out["case_status"]["DEAD"] == "finished"
    assert "LIVE" not in out["cases"]
    assert all(v in ("finished", "skipped")
               for v in out["combo_status"].values())
    d["cases_not_run"] = ["ZZZ"]
    assert api_client.post("/api/model", json=d).status_code == 400


# =========================================================================== #
# 2. Set Active Degrees of Freedom
# =========================================================================== #
def test_active_dof_validation_roundtrip_and_presets():
    assert ACTIVE_DOF_PRESETS["xz_plane"] == ["UX", "UZ", "RY"]
    assert ACTIVE_DOF_PRESETS["yz_plane"] == ["UY", "UZ", "RX"]
    assert ACTIVE_DOF_PRESETS["xy_plane"] == ["UX", "UY", "RZ"]
    m = _portal(active=["RY", "UX", "UZ"])        # order-independent
    back = BuildingModel.from_dict(m.to_dict())
    assert back.active_dof == ["RY", "UX", "UZ"]
    assert back.active_dof_mask() == (1, 0, 1, 0, 1, 0)
    for bad, frag in (([], "non-empty"), (["UX", "UW"], "unknown DOF"),
                      (["UX", "UX"], "duplicate")):
        m.active_dof = bad
        with pytest.raises(ValueError, match=frag):
            m.validate()
    assert BuildingModel().to_dict()["active_dof"] == list(
        ACTIVE_DOF_PRESETS["full_3d"])


@pytest.mark.parametrize("dia", [True, False])
def test_xz_portal_matches_3d_and_closed_form(dia):
    D = _portal_sway()
    e3 = OpenSeesEngine(_portal(dia=dia))
    e2 = OpenSeesEngine(_portal(["UX", "UZ", "RY"], dia=dia))
    r3, r2 = e3.run_static("LAT"), e2.run_static("LAT")
    t = _tag_at(e2, (0.0, 0.0, H))
    assert r2.node_disp[t][0] == pytest.approx(D, rel=1e-5)
    for tag, v in r2.node_disp.items():
        for i in range(6):
            assert v[i] == pytest.approx(r3.node_disp[tag][i], abs=1e-15)
        assert v[1] == 0.0 and v[3] == 0.0 and v[5] == 0.0   # exactly
    for uid in ("C1", "C2", "B1"):
        for a, b in zip(r2.member_forces[uid], r3.member_forces[uid]):
            assert a == pytest.approx(b, abs=1e-9)


def test_yz_portal_mirrors_xz():
    D = _portal_sway()
    e = OpenSeesEngine(_portal(["UY", "UZ", "RX"], plane="YZ"))
    r = e.run_static("LAT")
    t = _tag_at(e, (0.0, 0.0, H))
    assert r.node_disp[t][1] == pytest.approx(D, rel=1e-5)
    for v in r.node_disp.values():
        assert v[0] == 0.0 and v[4] == 0.0 and v[5] == 0.0


def test_xz_modal_returns_only_in_plane_modes():
    """2 t of UX (and UY) mass at each top joint: in 3D the out-of-plane
    UY sway modes appear; in the XZ plane only the in-plane modes remain
    and the sway period is 2*pi*sqrt(2m/K), K = P/D (closed form)."""
    mm = 2.0
    K = P_LAT / _portal_sway()
    T_hand = 2.0 * math.pi * math.sqrt(2.0 * mm / K)
    m3 = OpenSeesEngine(_portal(dia=False, masses=mm)).run_modal()
    m2 = OpenSeesEngine(_portal(["UX", "UZ", "RY"], dia=False,
                                masses=mm)).run_modal()
    assert len(m3.periods) == 4 and len(m2.periods) == 2
    assert m2.periods[0] == pytest.approx(T_hand, rel=1e-5)
    oop3 = max(abs(v[1]) for sh in m3.shapes.values() for v in sh.values())
    assert oop3 > 0.1                            # 3D has out-of-plane modes
    for sh in m2.shapes.values():
        for v in sh.values():
            assert v[1] == 0.0 and v[3] == 0.0 and v[5] == 0.0
    assert all(p["uy"] == 0.0 for p in m2.participation)


def test_xy_plane_grillage_has_no_vertical_response():
    m = quick_building(bays_x=1, bays_y=1, stories=2)
    m.active_dof = list(ACTIVE_DOF_PRESETS["xy_plane"])
    m.validate()
    r = OpenSeesEngine(m).run_static("EQX")
    for v in r.node_disp.values():
        assert v[2] == 0.0 and v[3] == 0.0 and v[4] == 0.0
    assert max(abs(v[0]) for v in r.node_disp.values()) > 0.0


def test_all_six_dof_any_order_is_bit_identical():
    a = quick_building(bays_x=1, bays_y=1, stories=2)
    b = quick_building(bays_x=1, bays_y=1, stories=2)
    b.active_dof = ["RZ", "RY", "RX", "UZ", "UY", "UX"]
    ra, rb = OpenSeesEngine(a).run().to_dict(), OpenSeesEngine(b).run().to_dict()
    assert ra == rb


def test_active_dof_merges_with_supports_and_diaphragm():
    """Inactive DOFs at supports (already fixed) and at diaphragm slaves
    (constrained) do not raise duplicate-fix / constraint errors: a
    rigid-diaphragm building in the XZ plane solves and its masters carry
    the inactive UY/RZ restraint."""
    m = quick_building(bays_x=2, bays_y=1, stories=2)
    m.active_dof = ["UX", "UZ", "RY"]
    m.validate()
    eng = OpenSeesEngine(m)
    r = eng.run_static("EQX")
    for st, master in eng._asm.masters.items():
        assert eng._asm.node_restraints[master][1] == 1
        assert eng._asm.node_restraints[master][5] == 1
        assert r.node_disp[master][1] == 0.0
    mo = eng.run_modal()
    assert mo.periods and all(p["uy"] == 0.0 for p in mo.participation)


# =========================================================================== #
# 3. Mass Source options
# =========================================================================== #
def _self_weight_portal(**opts):
    """Portal whose ONLY mass source is a self-weight pattern (+ optional
    UDL pattern)."""
    m = _portal(dia=True)
    m.materials["S"].unit_weight = 77.0
    sw = m.pattern("SW", "dead")
    sw.self_weight_factor = 1.0
    m.mass_source = {"SW": 1.0}
    m.mass_options.update(opts)
    m.validate()
    return m


def test_mass_options_defaults_roundtrip_and_validation():
    m = quick_building(bays_x=1, bays_y=1, stories=1)
    d = m.to_dict()
    assert d["mass_options"] == MASS_OPTION_DEFAULTS
    d.pop("mass_options")                         # old file
    assert BuildingModel.from_dict(d).mass_options == MASS_OPTION_DEFAULTS
    d["mass_options"] = {"include_vertical": True}    # partial -> filled
    back = BuildingModel.from_dict(d)
    assert back.to_dict()["mass_options"] == dict(MASS_OPTION_DEFAULTS,
                                                  include_vertical=True)
    for bad, frag in (({"lateral": True}, "unknown keys"),
                      ({"self_mass": "yes"}, "must be a bool"),
                      ({"include_lateral": False}, "include_lateral"),
                      ({"self_mass": False, "patterns": False},
                       "self_mass / patterns")):
        d["mass_options"] = bad
        with pytest.raises(ValueError, match=frag):
            BuildingModel.from_dict(d)
    # explicit nodal mass makes "no derived mass" legal
    m.nodal_masses.append(NodalMass((0, 0, 3.2), mx=1.0))
    m.mass_options = {"self_mass": False, "patterns": False}
    m.validate()


def test_self_mass_off_gives_zero_modal_mass():
    on = OpenSeesEngine(_self_weight_portal())
    assert on.run_modal().periods
    m = _self_weight_portal(self_mass=False, patterns=True)
    assert sum(m.compute_story_masses().values()) == 0.0
    eng = OpenSeesEngine(m)
    assert eng.run_modal().periods == []
    assert eng._asm.mass_map == {}


def test_patterns_off_removes_exactly_the_pattern_mass():
    m = _self_weight_portal()
    sdl = m.pattern("SDL", "dead")
    sdl.member_udls.append(MemberUDL("B1", 6.0))          # 6 kN/m on beam
    m.mass_source = {"SW": 1.0, "SDL": 1.0}
    m.validate()
    full = m.compute_story_masses()["Story1"]
    m.mass_options["patterns"] = False
    self_only = m.compute_story_masses()["Story1"]
    m.mass_options.update(patterns=True, self_mass=False)
    pat_only = m.compute_story_masses()["Story1"]
    # beam self weight only (columns excluded): A*gamma*L / g
    assert self_only == pytest.approx(100.0 * 77.0 * L / G_ACCEL, rel=1e-12)
    assert pat_only == pytest.approx(6.0 * L / G_ACCEL, rel=1e-12)
    assert full == pytest.approx(self_only + pat_only, rel=1e-12)


def _vertical_column(vertical, lateral=True, lump=True):
    m = BuildingModel("col")
    m.add_material(Material("S", E_S, 0.3, 0.0, material_type="steel"))
    m.add_section(FrameSection("COL", "S", 0.01, 2e-4, 1e-4, 1e-4))
    m.set_stories([H])
    m.add_member("column", "COL", (0, 0, 0), (0, 0, H), story="Story1")
    m.supports.append(PointSupport((0, 0, 0), FIX))
    p = m.pattern("DEAD", "dead")
    p.nodal_loads.append(NodalLoad((0, 0, H), fz=-10.0 * G_ACCEL))  # 10 t
    m.mass_source = {"DEAD": 1.0}
    m.mass_options.update(include_vertical=vertical, include_lateral=lateral,
                          lump_at_stories=lump)
    m.validate()
    return m


def test_include_vertical_adds_axial_mode_sqrt_k_over_m():
    """10 t on an axial column (k = EA/H): T_v = 2*pi*sqrt(m H / (E A))."""
    T_v = 2.0 * math.pi * math.sqrt(10.0 * H / (E_S * 0.01))
    lat = OpenSeesEngine(_vertical_column(False)).run_modal().periods
    both = OpenSeesEngine(_vertical_column(True)).run_modal().periods
    vonly = OpenSeesEngine(_vertical_column(True, lateral=False)).run_modal()
    assert len(lat) == 2 and all(abs(t - T_v) > 1e-3 for t in lat)
    assert len(both) == 3
    assert min(both, key=lambda t: abs(t - T_v)) == pytest.approx(T_v,
                                                                 rel=1e-9)
    assert vonly.periods == [pytest.approx(T_v, rel=1e-9)]
    for v in vonly.shapes[1].values():           # a pure axial (UZ) mode
        assert v[0] == pytest.approx(0.0, abs=1e-12)
        assert v[1] == pytest.approx(0.0, abs=1e-12)
    assert max(abs(v[2]) for v in vonly.shapes[1].values()) > 0.0


def test_lump_at_stories_false_same_total_mass_and_first_period():
    res = {}
    for lump in (True, False):
        m = quick_building(bays_x=2, bays_y=2, stories=3)
        m.mass_options["lump_at_stories"] = lump
        m.validate()
        eng = OpenSeesEngine(m)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mo = eng.run_modal()
        tot = sum(v for (t, d), v in eng._asm.mass_map.items() if d == 1)
        res[lump] = (tot, sum(m.compute_story_masses().values()),
                     mo.periods[0])
        if not lump:
            assert all(t not in eng._asm.masters.values()
                       for (t, d) in eng._asm.mass_map)
    assert res[False][0] == pytest.approx(res[True][0], rel=1e-12)
    assert res[False][0] == pytest.approx(res[True][1], rel=1e-12)
    assert res[False][2] == pytest.approx(res[True][2], rel=1e-3)


def test_tributary_lumping_puts_mass_at_member_nodes():
    """lump_at_stories=False, no diaphragm: a 6 kN/m UDL on the 5 m beam
    lands half on each beam end node (exact lever rule)."""
    m = _portal(dia=False)
    p = m.pattern("SDL", "dead")
    p.member_udls.append(MemberUDL("B1", 6.0))
    m.mass_source = {"SDL": 1.0}
    m.mass_options["lump_at_stories"] = False
    m.validate()
    eng = OpenSeesEngine(m)
    eng._build()
    for x in (0.0, L):
        t = _tag_at(eng, (x, 0.0, H))
        assert eng._asm.mass_map[(t, 1)] == pytest.approx(
            3.0 * L / G_ACCEL, rel=1e-12)
        assert (t, 3) not in eng._asm.mass_map


def test_element_self_mass_respects_self_mass_and_lateral():
    m = _portal(dia=False)
    m.mass_source_mode = "element_self_mass"
    m.materials["S"].unit_weight = 77.0
    eng = OpenSeesEngine(m)
    eng._build()
    assert any(d == 1 for (t, d) in eng._asm.mass_map)
    m.mass_options["include_lateral"] = False
    m.mass_options["include_vertical"] = True
    eng = OpenSeesEngine(m)
    eng._build()
    assert eng._asm.mass_map and all(d == 3 for (t, d) in eng._asm.mass_map)
    m.mass_options.update(self_mass=False, include_lateral=True)
    m.nodal_masses.append(NodalMass((0, 0, H), mx=1.0))
    eng = OpenSeesEngine(m)
    eng._build()
    assert list(eng._asm.mass_map.values()) == [1.0]


def test_explicit_story_masses_roundtrip_semantics():
    m = quick_building(bays_x=1, bays_y=1, stories=2)
    d = m.to_dict()
    assert d["explicit_story_masses"] == {}
    back = BuildingModel.from_dict(d)
    assert back.story_masses == {}                 # still derived
    assert back.compute_story_masses() == m.compute_story_masses()
    legacy = dict(d)
    legacy.pop("explicit_story_masses")            # pre-v1.13 file
    assert BuildingModel.from_dict(legacy).story_masses == \
        m.compute_story_masses()


# =========================================================================== #
# 4. Material stress-strain curves
# =========================================================================== #
def test_mander_peak_equals_fc_at_eps_c0():
    law = material_law(_conc({"model": "mander"}))
    assert law.exact(-0.002) == pytest.approx(-30_000.0, rel=1e-12)
    for e in (-0.0019, -0.0021):
        assert abs(law.exact(e)) < 30_000.0
    # Popovics r = Ec/(Ec - fc/eps0)
    assert law.params["r"] == pytest.approx(25e6 / (25e6 - 15e6), rel=1e-12)
    assert _mono(law, [-0.002])[0] == pytest.approx(-30_000.0, rel=1e-9)
    assert law.ops_type == "Concrete04"


def test_kent_park_post_peak_slope_matches_formula():
    fc = 30_000.0
    law = material_law(_conc({"model": "park"}, fc=fc))
    e50u = (3.0 + 0.29 * 30.0) / (145.0 * 30.0 - 1000.0)
    Z = 0.5 / (e50u - 0.002)
    e1, e2 = -0.0025, -0.0030
    slope = (law.exact(e2) - law.exact(e1)) / (e2 - e1)
    assert slope == pytest.approx(-Z * fc, rel=1e-12) or \
        slope == pytest.approx(Z * fc, rel=1e-12)
    # stress at eps_50u is half the peak
    assert law.exact(-e50u) == pytest.approx(-0.5 * fc, rel=1e-12)
    ops_s = _mono(law, [e1, e2])
    assert (ops_s[1] - ops_s[0]) / (e2 - e1) == pytest.approx(
        (law.exact(e2) - law.exact(e1)) / (e2 - e1), rel=1e-9)
    assert law.exact(-0.01) == pytest.approx(-0.2 * fc, rel=1e-12)


def test_steel01_simple_yields_at_fy_over_E():
    law = material_law(_rebar({"model": "simple", "params": {"b": 0.02}}))
    ey = 420_000.0 / 2e8
    assert law.exact(ey) == pytest.approx(420_000.0, rel=1e-12)
    assert law.exact(-ey) == pytest.approx(-420_000.0, rel=1e-12)
    assert law.exact(0.5 * ey) == pytest.approx(210_000.0, rel=1e-12)
    s1, s2 = law.exact(0.01), law.exact(0.02)
    assert (s2 - s1) / 0.01 == pytest.approx(0.02 * 2e8, rel=1e-12)
    o = _mono(law, [ey, 0.01, 0.02])
    assert o[0] == pytest.approx(420_000.0, rel=1e-9)
    assert (o[2] - o[1]) / 0.01 == pytest.approx(0.02 * 2e8, rel=1e-9)
    assert law.ops_type == "Steel01"


def test_park_steel_plateau_and_ultimate():
    law = material_law(_rebar({"model": "park"}))
    p = law.params
    assert park_steel_stress(0.005, p) == 420_000.0           # plateau
    assert park_steel_stress(p["eps_sh"], p) == pytest.approx(420_000.0)
    assert park_steel_stress(p["eps_su"] - 1e-12, p) == pytest.approx(
        620_000.0, rel=1e-9)
    assert law.exact(0.2) == 620_000.0                        # held
    # MultiLinear (kinematic) is exact at its stations
    o = _mono(law, [0.02, 0.05])
    assert o[0] == pytest.approx(law.stress(0.02), rel=1e-9)
    assert law.stress(0.05) == pytest.approx(law.exact(0.05), rel=2e-3)


def test_user_curve_interpolation():
    pts = [[-0.004, -20_000.0], [-0.002, -30_000.0], [0.0, 0.0],
           [0.0001, 2_500.0], [0.001, 0.0]]
    law = material_law(_conc({"model": "user", "hysteresis": "elastic",
                              "points": pts}))
    assert law.exact(-0.003) == pytest.approx(-25_000.0, rel=1e-12)
    assert law.exact(-0.001) == pytest.approx(-15_000.0, rel=1e-12)
    assert law.exact(0.00055) == pytest.approx(1_250.0, rel=1e-12)
    assert law.exact(-0.01) == -20_000.0                      # held flat
    o = _mono(law, [-0.001, -0.003])
    assert o == [pytest.approx(-15_000.0), pytest.approx(-25_000.0)]


_CASES = ([("concrete", mdl, h, prm)
           for mdl in ("simple", "park", "mander")
           for h in HYSTERESIS_RULES
           for prm in ({}, {"ft": 3000.0})]
          + [("rebar", mdl, h, {}) for mdl in ("simple", "park")
             for h in HYSTERESIS_RULES]
          + [("concrete", "user", h, {}) for h in HYSTERESIS_RULES]
          + [("rebar", "user", h, {}) for h in HYSTERESIS_RULES])


@pytest.mark.parametrize("mt,model,hyst,prm", _CASES)
def test_backbone_matches_opensees_material(mt, model, hyst, prm):
    """The pure-Python engine-effective backbone == the OpenSees uniaxial
    material under monotonic compression and tension (fresh material per
    direction)."""
    ss = {"model": model, "hysteresis": hyst, "params": prm}
    if model == "user":
        ss["params"] = {}
        ss["points"] = ([[-0.004, -20e3], [-0.002, -30e3], [-0.001, -20e3],
                         [0, 0], [1e-4, 2.5e3], [1e-3, 0]] if mt == "concrete"
                        else [[-0.05, -5e5], [-0.002, -4e5], [0, 0],
                              [0.002, 4e5], [0.05, 5e5]])
    mat = _conc(ss) if mt == "concrete" else _rebar(ss)
    BuildingModel._validate_material(mat)
    law = material_law(mat)
    lo, hi = law.range
    cs = [lo * i / 25 for i in range(1, 26)]
    ts = [hi * i / 25 for i in range(1, 26)]
    got = _mono(law, cs) + _mono(law, ts)
    peak = max(abs(law.stress(e)) for e in cs + ts)
    for e, g in zip(cs + ts, got):
        assert g == pytest.approx(law.stress(e), abs=1e-9 * peak)
    if law.native:
        for e in cs + ts:
            assert law.stress(e) == law.exact(e)


def test_stress_strain_validation():
    bad = [
        (_rebar({"model": "mander"}), "not applicable"),
        (_conc({"model": "simple", "hysteresis": "bouncy"}), "hysteresis"),
        (_conc({"model": "simple", "params": {"eps_sh": 0.01}}), "not valid"),
        (_conc({"model": "simple", "points": [[0, 0], [1, 1]]}),
         "only valid for model 'user'"),
        (_conc({"model": "simple", "params": {"eps_c0": 0.004}}),
         "eps_cu"),
        (_conc({"model": "mander"}, E=10e6), "mander needs E"),
        (_conc({"model": "user", "points": [[-0.002, -1e4], [0.001, 1]]}),
         "origin"),
        (_conc({"model": "user", "points": [[0, 0], [-0.002, -1e4]]}),
         "increasing"),
        (_conc({"model": "user", "points": [[-0.002, 1e4], [0, 0]]}),
         "wrong sign"),
        (_rebar({"model": "park"}, fu=400_000.0), "fu > fy"),
        (_conc({"model": "simple", "colour": 1}), "unknown keys"),
    ]
    for mat, frag in bad:
        with pytest.raises(ValueError, match=frag):
            BuildingModel._validate_material(mat)


def test_stress_strain_roundtrip_and_null_backcompat():
    m = quick_building(bays_x=1, bays_y=1, stories=1)
    name = next(iter(m.materials))
    d = m.to_dict()
    assert d["materials"][name]["stress_strain"] is None
    del d["materials"][name]["stress_strain"]          # pre-v1.13 file
    assert BuildingModel.from_dict(d).materials[name].stress_strain is None
    m.materials[name].fc = 30_000.0
    m.materials[name].stress_strain = {
        "model": "mander", "hysteresis": "takeda",
        "params": {"eps_c0": 0.0022, "ft": 2500}}
    back = BuildingModel.from_dict(m.to_dict())
    assert back.materials[name].stress_strain == {
        "model": "mander", "hysteresis": "takeda",
        "params": {"eps_c0": 0.0022, "ft": 2500.0}}
    assert back.to_dict() == BuildingModel.from_dict(back.to_dict()).to_dict()


def test_rc_fiber_section_capacity_matches_whitney():
    """0.3 x 0.5 m RC section, 3 phi20 bars at d = 0.45 m, f'c = 30 MPa
    ('simple' Hognestad law), fy = 420 MPa EPP ('simple', b = 0): the
    OpenSees fiber moment at eps_top = -0.0035 matches the Whitney block
    Mn = As fy (d - a/2), a = As fy / (0.85 f'c b), within 2 %."""
    b, h, d = 0.3, 0.5, 0.45
    As = 3 * math.pi * 0.02 ** 2 / 4.0
    fy, fc = 420_000.0, 30_000.0
    lc = material_law(_conc({"model": "simple"}, fc=fc))
    ls = material_law(_rebar({"model": "simple", "params": {"b": 0.0}},
                             fy=fy))
    ops.wipe()
    ops.model("basic", "-ndm", 2, "-ndf", 3)
    lc.ops_material(ops, 1)
    ls.ops_material(ops, 2)
    ops.section("Fiber", 1)
    ops.patch("rect", 1, 200, 1, -h / 2, -b / 2, h / 2, b / 2)
    ops.fiber(-(d - h / 2), 0.0, As, 2)
    ops.node(1, 0.0, 0.0)
    ops.node(2, 0.0, 0.0)
    ops.fix(1, 1, 1, 1)
    ops.fix(2, 0, 1, 0)
    ops.element("zeroLengthSection", 1, 1, 2, 1)
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(2, 0.0, 0.0, 1.0)
    ops.integrator("DisplacementControl", 2, 3, 2e-5)
    ops.system("BandGeneral")
    ops.numberer("Plain")
    ops.constraints("Plain")
    ops.test("NormDispIncr", 1e-10, 50)
    ops.algorithm("Newton")
    ops.analysis("Static")
    m_max = 0.0
    for _ in range(10000):
        assert ops.analyze(1) == 0
        m_max = max(m_max, ops.getLoadFactor(1))
        if ops.nodeDisp(2, 1) - ops.nodeDisp(2, 3) * h / 2 < -0.0035:
            break
    a = As * fy / (0.85 * fc * b)
    Mn = As * fy * (d - a / 2)
    assert m_max == pytest.approx(Mn, rel=0.02)


def _w_cantilever(ss):
    m = BuildingModel("fibw")
    m.add_material(Material("steel", E_S, 0.3, 77.0, material_type="steel",
                            stress_strain=ss))
    m.add_section(FrameSection.from_library("W18x50", "steel"))
    m.set_stories([3.0])
    m.add_member("column", "W18x50", (0, 0, 0), (0, 0, 3.0), story="S1",
                 hinges="fiber_pmm")
    m.supports.append(PointSupport((0, 0, 0), FIX))
    m.patterns["DEAD"] = LoadPattern("DEAD", "dead")
    m.add_pushover_case("PUSH", "Y", gravity={}, target_drift=0.03, steps=15,
                        hinges="asce41")
    m.validate()
    return m


def test_fiber_hinge_honours_stress_strain():
    """W-shape fiber_pmm pushover: 'simple' with b = 0.01 (the legacy
    Steel01 hardening) reproduces the null law exactly; an EPP 'simple'
    (b = 0) caps lower; 'default' == null."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        base = OpenSeesEngine(_w_cantilever(None)).run_pushover("PUSH")
        same = OpenSeesEngine(_w_cantilever(
            {"model": "simple", "params": {"b": 0.01}})).run_pushover("PUSH")
        dflt = OpenSeesEngine(_w_cantilever(
            {"model": "default"})).run_pushover("PUSH")
        epp = OpenSeesEngine(_w_cantilever(
            {"model": "simple", "params": {"b": 0.0}})).run_pushover("PUSH")
    assert same.base_shear == base.base_shear
    assert dflt.base_shear == base.base_shear
    assert epp.base_shear[0] == pytest.approx(base.base_shear[0], rel=1e-9)
    assert epp.base_shear[-1] < 0.95 * base.base_shear[-1]


def test_layered_shell_honours_stress_strain():
    """Layered wall pushover with a Mander (+ tension) concrete law: the
    pre-crack stiffness stays EXACTLY the elastic E*mod wall stiffness
    (ASDConcrete3D points clipped onto the elastic line) and the run
    completes; the peak differs from the legacy law."""
    from test_wave23 import _layered_wall
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        leg = OpenSeesEngine(_layered_wall(True)).run_pushover("PO")
        m = _layered_wall(True)
        m.materials["C"].material_type = "concrete"
        m.materials["C"].stress_strain = {"model": "mander",
                                          "params": {"ft": 3000.0}}
        m.validate()
        r = OpenSeesEngine(m).run_pushover("PO")
    assert len(r.base_shear) == 40 and not r.warnings
    k0 = r.base_shear[0] / r.roof_disp[0]
    assert k0 == pytest.approx(leg.base_shear[0] / leg.roof_disp[0],
                               rel=1e-4)
    assert max(r.base_shear) != pytest.approx(max(leg.base_shear), rel=1e-3)


def test_api_materials_curve(api_client):
    body = {"material": {"name": "C", "E": 25e6, "fc": 30_000.0,
                         "material_type": "concrete",
                         "stress_strain": {"model": "mander",
                                           "params": {"ft": 3000.0}}}}
    out = api_client.post("/api/materials/curve", json=body).get_json()
    assert out["model"] == "mander" and out["opensees"] == "Concrete04"
    assert 75 <= len(out["strain"]) <= 90
    assert len(out["strain"]) == len(out["stress"])
    assert out["strain"] == sorted(out["strain"])
    assert 0.0 in out["strain"]
    assert min(out["stress"]) == pytest.approx(-30_000.0, rel=1e-12)
    assert max(out["stress"]) == pytest.approx(3_000.0, rel=1e-12)
    assert all(s <= 0 for e, s in zip(out["strain"], out["stress"]) if e < 0)
    # same code as the engine
    law = material_law(_conc({"model": "mander", "params": {"ft": 3000.0}}))
    for e, s in zip(out["strain"], out["stress"]):
        assert s == law.stress(e)
    # null -> the legacy fiber law (Concrete01, eps0 = 2 f'c / E)
    body["material"]["stress_strain"] = None
    out = api_client.post("/api/materials/curve", json=body).get_json()
    assert out["model"] == "default" and out["opensees"] == "Concrete01"
    assert -0.0024 in out["strain"]
    # by name from the current model; errors -> 400
    r = api_client.post("/api/materials/curve",
                        json={"name": "nope"})
    assert r.status_code == 400
    body["material"]["stress_strain"] = {"model": "park",
                                         "params": {"b": 0.1}}
    assert api_client.post("/api/materials/curve",
                           json=body).status_code == 400


# =========================================================================== #
# 5. Display units
# =========================================================================== #
def test_display_units_roundtrip_validation_and_engine_ignores():
    m = quick_building(bays_x=1, bays_y=1, stories=1)
    assert m.to_dict()["display_units"] == "kN-m"
    assert set(DISPLAY_UNITS) == set(UNIT_SETS)
    m2 = quick_building(bays_x=1, bays_y=1, stories=1)
    m2.display_units = "kip-in"
    back = BuildingModel.from_dict(m2.to_dict())
    assert back.display_units == "kip-in"
    assert (OpenSeesEngine(m).run_static("DEAD").node_disp
            == OpenSeesEngine(back).run_static("DEAD").node_disp)
    m2.display_units = "furlong"
    with pytest.raises(ValueError, match="display_units"):
        m2.validate()


def test_api_units_table(api_client):
    t = api_client.get("/api/units").get_json()
    assert t == units_table()
    s = t["sets"]
    assert s["kip-ft"]["force"] == ["kip", 1.0 / 4.4482216152605]
    assert s["kip-ft"]["length"] == ["ft", 1.0 / 0.3048]
    assert s["kip-in"]["length"] == ["in", 1.0 / 0.0254]
    assert s["tonf-m"]["force"] == ["tonf", 1.0 / 9.80665]
    assert s["N-mm"]["force"] == ["N", 1000.0]
    assert s["kN-mm"]["length"] == ["mm", 1000.0]
    for name in ("kN-m", "kN-mm", "N-mm", "tonf-m"):
        assert s[name]["temperature"] == "C"
    for name in ("kip-ft", "kip-in"):
        assert s[name]["temperature"] == "F"
    # 1 ksi = 6894.757 kPa; 1 kip*ft = 1.3558179 kN*m
    assert 6894.757 * s["kip-in"]["factors"]["stress"] == pytest.approx(
        1.0, rel=1e-7)
    assert 1.3558179483 * s["kip-ft"]["factors"]["moment"] == pytest.approx(
        1.0, rel=1e-9)
    assert t["quantities"]["moment"] == {"force": 1, "length": 1,
                                         "si": "kN*m"}
    assert s["kip-ft"]["labels"]["stress"] == "kip/ft^2"
    assert t["temperature"]["F"] == {"scale": 1.8, "offset": 32.0}
