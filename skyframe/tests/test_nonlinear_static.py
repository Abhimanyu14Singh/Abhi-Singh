"""Nonlinear Static load cases and case chaining (ETABS parity).

Hand / cross-check references (units kN, m, tonne, s; E in kPa):

* Elastic model: a nonlinear static case under load control converges to
  the linear static solution of the same loads (exact up to round-off),
  for any number of steps; with ``geometric="p_delta"`` it equals the
  per-case P-Delta static case (``LoadCase.geometric="pdelta"``) and with
  ``"large_displacement"`` the per-case corotational one.
* Elastic-perfectly-plastic cantilever (height L, base hinge My,
  hardening 0, rotational hinge spring k_t = 10*6EI/L in series with the
  column): yield shear V_y = My/L, yield tip displacement
  d_y = V_y (L^3/3EI + L^2/k_t); past yield the column stays at V_y and
  the extra tip displacement is the rigid-body mechanism
  theta_p = (d - d_y)/L, so the hinge rotation is My/k_t + theta_p.
* Tension-only X-brace frame (pinned columns, released beam): the
  compression diagonal goes slack (force ~ AXIAL_ONLY_RATIO), the tension
  diagonal carries N = H / cos(theta).
* SDOF P-Delta column: k = 3EI/L^3 - P/L, so a MODAL run on the end state
  of a P-Delta gravity case gives T = T0 / sqrt(1 - P/Pcr_s),
  Pcr_s = 3EI/L^2 — identical to the model-wide ``iterative_loads``
  P-Delta option.
"""

import json
import math

import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core.model import (G_ACCEL, BuildingModel,  # noqa: E402
                                 FrameSection, Material, NodalLoad,
                                 NodalMass, PointSupport, StoryForce)
from skyframe.core.nonlinear_static import chain_of  # noqa: E402

E_CONC = 25_000_000.0
SIZE = 0.3
EI = E_CONC * SIZE ** 4 / 12.0


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
def _column(L=3.0, P=0.0, H=1.0, m=10.0, pdelta_method="none"):
    """One-element cantilever: pattern G = tip axial -P, H = tip +X."""
    mdl = BuildingModel(name="col")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.set_stories([L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", SIZE, SIZE))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    if m:
        mdl.nodal_masses.append(NodalMass((0, 0, L), mx=m, my=m))
    mdl.pattern("G", "dead").nodal_loads.append(NodalLoad((0, 0, L), fz=-P))
    mdl.pattern("H", "other").nodal_loads.append(NodalLoad((0, 0, L), fx=H))
    mdl.num_modes = 1
    if pdelta_method != "none":
        mdl.pdelta_options = {"method": pdelta_method,
                              "load_factors": {"G": 1.0},
                              "max_iterations": 20, "tolerance": 1e-12}
    mdl.validate()
    return mdl


def _frame(heights=(3.0, 3.0), masses=(20.0, 20.0), bay=6.0):
    """1x1-bay rigid-diaphragm frame; G = column-top axials (+ a beam UDL
    so the member-station recovery is exercised), EX = unit story forces."""
    mdl = BuildingModel(name="frame")
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.set_stories(list(heights))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))
    mdl.add_section(FrameSection.rectangular("BM", "CONC", 0.3, 0.6))
    corners = [(0, 0), (bay, 0), (bay, bay), (0, bay)]
    z0 = 0.0
    for k, s in enumerate(mdl.stories):
        z1 = s.elevation
        for j, (x, y) in enumerate(corners):
            mdl.add_member("column", "COL", (x, y, z0), (x, y, z1),
                           story=s.name, uid=f"C{k}{j}")
        for j in range(4):
            (xa, ya), (xb, yb) = corners[j], corners[(j + 1) % 4]
            mdl.add_member("beam", "BM", (xa, ya, z1), (xb, yb, z1),
                           story=s.name, uid=f"B{k}{j}")
        z0 = z1
    mdl.story_masses = {s.name: float(mm)
                        for s, mm in zip(mdl.stories, masses)}
    g = mdl.pattern("G", "dead")
    for s, mm in zip(mdl.stories, masses):
        for (x, y) in corners:
            g.nodal_loads.append(NodalLoad((x, y, s.elevation),
                                           fz=-mm * G_ACCEL / 4.0))
    from skyframe.core.model import MemberUDL
    g.member_udls.append(MemberUDL("B00", -12.0))
    ex = mdl.pattern("EX", "other")
    for s in mdl.stories:
        ex.story_forces.append(StoryForce(s.name, fx=1.0))
    mdl.add_case("G", {"G": 1.0})
    mdl.add_case("EX", {"EX": 1.0})
    mdl.add_case("GEX", {"G": 1.0, "EX": 25.0})
    mdl.num_modes = 6
    mdl.validate()
    return mdl


def _brace_frame(H=10.0, w=4.0, h=3.0):
    """Planar (XZ) pinned frame + tension-only X braces."""
    mdl = BuildingModel(name="brace")
    mdl.rigid_diaphragms = False
    mdl.active_dof = ["UX", "UZ", "RY"]
    mdl.add_material(Material("STL", E=200_000_000.0, nu=0.3))
    mdl.set_stories([h])
    mdl.add_section(FrameSection.rectangular("COL", "STL", 0.2, 0.2))
    mdl.add_section(FrameSection.rectangular("BR", "STL", 0.05, 0.05))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, h), "Story1", "C1")
    mdl.add_member("column", "COL", (w, 0, 0), (w, 0, h), "Story1", "C2")
    mdl.add_member("beam", "COL", (0, 0, h), (w, 0, h), "Story1", "B1",
                   releases="Mi,Mj")
    mdl.add_member("brace", "BR", (0, 0, 0), (w, 0, h), "Story1", "DT",
                   axial_limit="tension")
    mdl.add_member("brace", "BR", (w, 0, 0), (0, 0, h), "Story1", "DC",
                   axial_limit="tension")
    for x in (0.0, w):
        mdl.supports.append(PointSupport((x, 0, 0), (1, 1, 1, 0, 0, 0)))
    mdl.pattern("H", "other").nodal_loads.append(NodalLoad((0, 0, h),
                                                           fx=H))
    mdl.add_case("H", {"H": 1.0})
    mdl.validate()
    return mdl


def _maxdiff(a, b):
    return max(abs(u - v) for t in a for u, v in zip(a[t], b[t]))


def _maxabs(a):
    return max(abs(u) for v in a.values() for u in v)


def _same_case(lin, nl, rel=1e-9):
    """Static-case shape equality (node_disp/reactions/forces/stations)."""
    assert set(lin.node_disp) == set(nl.node_disp)
    assert _maxdiff(lin.node_disp, nl.node_disp) <= rel * _maxabs(
        lin.node_disp)
    assert set(lin.reactions) == set(nl.reactions)
    assert _maxdiff(lin.reactions, nl.reactions) <= rel * _maxabs(
        lin.reactions)
    for k in lin.base:
        assert nl.base[k] == pytest.approx(lin.base[k], rel=rel, abs=1e-8)
    assert _maxdiff(lin.member_forces, nl.member_forces) <= rel * _maxabs(
        lin.member_forces)
    for uid, st in lin.member_stations.items():
        for key, vals in st.items():
            for u, v in zip(vals, nl.member_stations[uid][key]):
                assert v == pytest.approx(u, rel=1e-7, abs=1e-7)
    for s, row in lin.story.items():
        for k, v in row.items():
            assert nl.story[s][k] == pytest.approx(v, rel=1e-7, abs=1e-12)


# --------------------------------------------------------------------------- #
# elastic equivalences
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("steps", [1, 7])
def test_load_control_equals_linear_static(steps):
    m = _frame()
    m.add_nonlinear_static_case("NL", {"G": 1.0, "EX": 25.0}, steps=steps)
    eng = OpenSeesEngine(m)
    _same_case(eng.run_static("GEX"), eng.run_nonlinear_static("NL").case)


def test_pdelta_equals_per_case_pdelta():
    L = 3.0
    P = 0.4 * 3.0 * EI / L ** 2
    m = _column(L=L, P=P)
    m.add_case("GH_PD", {"G": 1.0, "H": 1.0}, geometric="pdelta")
    m.add_nonlinear_static_case("NL", {"G": 1.0, "H": 1.0}, steps=5,
                                geometric="p_delta")
    eng = OpenSeesEngine(m)
    lin = eng.run_static("GH_PD")
    nl = eng.run_nonlinear_static("NL").case
    _same_case(lin, nl, rel=1e-7)
    # hand: lateral amplification 1 / (1 - P/Pcr_s)
    tip = max(nl.node_disp, key=lambda t: abs(nl.node_disp[t][0]))
    k0 = 3.0 * EI / L ** 3
    assert nl.node_disp[tip][0] == pytest.approx(1.0 / (k0 - P / L),
                                                 rel=1e-6)


def test_large_displacement_equals_per_case_corotational():
    m = _column(L=3.0, P=50.0, H=5.0)
    m.add_case("GH_CR", {"G": 1.0, "H": 1.0}, geometric="corotational")
    m.add_nonlinear_static_case("NL", {"G": 1.0, "H": 1.0}, steps=10,
                                geometric="large_displacement")
    eng = OpenSeesEngine(m)
    _same_case(eng.run_static("GH_CR"),
               eng.run_nonlinear_static("NL").case, rel=1e-6)


def test_history_elastic_linear_in_load_factor():
    m = _frame()
    m.add_nonlinear_static_case("NL", {"EX": 25.0}, steps=4,
                                control_story="Story2")
    res = OpenSeesEngine(m).run_nonlinear_static("NL")
    h = res.history
    assert h["step"] == [0, 1, 2, 3, 4]
    assert h["load_factor"] == pytest.approx([0, .25, .5, .75, 1.0])
    d_end = h["disp"][-1]
    for lam, d, fx in zip(h["load_factor"], h["disp"], h["base"]["FX"]):
        assert d == pytest.approx(lam * d_end, rel=1e-9, abs=1e-15)
        assert fx == pytest.approx(-50.0 * lam, rel=1e-9, abs=1e-9)
    assert h["monitored"]["dof"] == "UX"
    assert res.converged and res.final_load_factor == pytest.approx(1.0)


def test_displacement_control_elastic_hits_target():
    m = _frame()
    m.add_nonlinear_static_case("EXu", {"EX": 1.0}, steps=1)
    m.add_nonlinear_static_case(
        "DC", {"EX": 1.0}, load_application="displacement_control",
        steps=5, control_story="Story2", target_disp=0.01)
    eng = OpenSeesEngine(m)
    unit = eng.run_nonlinear_static("EXu").history["disp"][-1]
    res = eng.run_nonlinear_static("DC")
    assert res.history["disp"][-1] == pytest.approx(0.01, rel=1e-9)
    assert res.final_load_factor == pytest.approx(0.01 / unit, rel=1e-7)
    # final-state forces = the unit case scaled by lambda (incl. stations)
    lin = eng.run_static("EX")
    lam = res.final_load_factor
    for uid, f in lin.member_forces.items():
        for u, v in zip(f, res.case.member_forces[uid]):
            assert v == pytest.approx(lam * u, rel=1e-6, abs=1e-9)
    assert res.case.base["FX"] == pytest.approx(-2.0 * lam, rel=1e-9)
    assert res.case.story["Story1"]["shear_x"] == pytest.approx(
        2.0 * lam, rel=1e-9)


# --------------------------------------------------------------------------- #
# material nonlinearity (hand)
# --------------------------------------------------------------------------- #
def test_epp_cantilever_hinge_mechanism():
    L, My, target = 3.0, 30.0, 0.03
    m = _column(L=L, m=0.0)
    m.add_nonlinear_static_case(
        "PUSH", {"H": 1.0}, load_application="displacement_control",
        steps=30, control_point=[0, 0, L], target_disp=target,
        hinges="column_base", default_My=My, hardening=0.0)
    res = OpenSeesEngine(m).run_nonlinear_static("PUSH")
    assert res.converged
    k_t = 10.0 * 6.0 * EI / L
    Vy = My / L
    d_y = Vy * (L ** 3 / (3.0 * EI) + L ** 2 / k_t)
    h = res.history
    # elastic branch: V = d / flex until d_y, then capped at Vy
    for d, fx in zip(h["disp"], h["base"]["FX"]):
        V = -fx
        if d <= d_y * (1 - 1e-9):
            assert V == pytest.approx(d / d_y * Vy, rel=1e-6)
        assert V <= Vy * (1 + 1e-6)
    assert -h["base"]["FX"][-1] == pytest.approx(Vy, rel=1e-6)
    assert res.final_load_factor == pytest.approx(Vy, rel=1e-6)
    # base moment caps at My (reactions + member end force)
    assert abs(res.case.base["MY"]) == pytest.approx(My, rel=1e-6)
    assert abs(res.case.member_forces["C1"][4]) == pytest.approx(My,
                                                                 rel=1e-6)
    # mechanism: hinge rotation = elastic My/k_t + plastic (d - d_y)/L
    theta = My / k_t + (target - d_y) / L
    assert res.hinge_rotations["C1"] == pytest.approx(theta, rel=1e-5)
    assert res.yielded == ["C1"]


def test_tension_only_brace_compression_slack():
    H, w, hgt = 10.0, 4.0, 3.0
    m = _brace_frame(H, w, hgt)
    m.add_nonlinear_static_case("NL", {"H": 1.0}, steps=3)
    eng = OpenSeesEngine(m)
    res = eng.run_nonlinear_static("NL").case
    cos_t = w / math.hypot(w, hgt)
    N_t = res.member_stations["DT"]["N"][0]
    N_c = res.member_stations["DC"]["N"][0]
    assert N_t == pytest.approx(H / cos_t, rel=1e-4)
    assert abs(N_c) < 1e-3 * abs(N_t)
    # the static (Newton) path agrees
    _same_case(eng.run_static("H"), res, rel=1e-6)


# --------------------------------------------------------------------------- #
# chaining
# --------------------------------------------------------------------------- #
def test_chain_gravity_then_lateral_equals_combined():
    m = _frame()
    m.add_nonlinear_static_case("NG", {"G": 1.0}, steps=3)
    m.add_nonlinear_static_case("NEX", {"EX": 25.0}, steps=4,
                                start_from="NG")
    m.add_nonlinear_static_case("NALL", {"G": 1.0, "EX": 25.0}, steps=2)
    eng = OpenSeesEngine(m)
    chained = eng.run_nonlinear_static("NEX")
    assert chained.chain == ["NG", "NEX"]
    _same_case(eng.run_nonlinear_static("NALL").case, chained.case)
    _same_case(eng.run_static("GEX"), chained.case)
    # history starts from the gravity end state
    g = eng.run_nonlinear_static("NG").case
    assert chained.history["base"]["FZ"][0] == pytest.approx(g.base["FZ"])


def test_three_level_chain_and_pdelta_chain():
    m = _frame()
    m.add_nonlinear_static_case("A", {"G": 0.5}, steps=2,
                                geometric="p_delta")
    m.add_nonlinear_static_case("B", {"G": 0.5}, steps=2, start_from="A",
                                geometric="p_delta")
    m.add_nonlinear_static_case("C", {"EX": 25.0}, steps=3,
                                start_from="B", geometric="p_delta")
    m.add_nonlinear_static_case("ALL", {"G": 1.0, "EX": 25.0}, steps=4,
                                geometric="p_delta")
    assert chain_of(m, "C") == ["A", "B", "C"]
    eng = OpenSeesEngine(m)
    _same_case(eng.run_nonlinear_static("ALL").case,
               eng.run_nonlinear_static("C").case, rel=1e-7)


def test_cycle_detection():
    m = _frame()
    m.add_nonlinear_static_case("A", {"G": 1.0})
    m.add_nonlinear_static_case("B", {"G": 1.0}, start_from="A")
    with pytest.raises(ValueError, match="circular"):
        m.add_nonlinear_static_case("A", {"G": 1.0}, start_from="B")
    assert m.nonlinear_static_cases["A"].start_from is None   # rolled back
    with pytest.raises(ValueError, match="itself"):
        m.add_nonlinear_static_case("S", {"G": 1.0}, start_from="S")
    d = m.to_dict()
    d["nonlinear_static_cases"]["A"]["start_from"] = "B"
    with pytest.raises(ValueError, match="circular"):
        BuildingModel.from_dict(d)


def test_pushover_starts_from_nonlinear_static_case():
    def model():
        m = _frame()
        m.add_nonlinear_static_case("NG", {"G": 1.0}, steps=1)
        return m
    m1 = model()
    m1.add_pushover_case("PO", "X", gravity={"G": 1.0}, target_drift=0.005,
                         steps=5, default_My=200.0)
    m2 = model()
    m2.add_pushover_case("PO", "X", start_from="NG", target_drift=0.005,
                         steps=5, default_My=200.0)
    a = OpenSeesEngine(m1).run_pushover("PO")
    b = OpenSeesEngine(m2).run_pushover("PO")
    assert b.control["start_from"] == "NG"
    assert b.base_shear == pytest.approx(a.base_shear, rel=1e-6)
    assert b.roof_disp == pytest.approx(a.roof_disp, rel=1e-6)


def test_pushover_start_from_geometric_mismatch_rejected():
    m = _frame()
    m.add_nonlinear_static_case("NGP", {"G": 1.0}, geometric="p_delta")
    with pytest.raises(ValueError, match="matching geometric"):
        m.add_pushover_case("PO", "X", start_from="NGP")
        m.validate()
    m.pushover_cases.clear()
    m.add_pushover_case("PO", "X", start_from="NGP", geometric="pdelta")
    m.validate()


# --------------------------------------------------------------------------- #
# modal from the end of a nonlinear case
# --------------------------------------------------------------------------- #
def test_modal_from_pdelta_gravity_matches_iterative_pdelta():
    L, mass = 3.0, 10.0
    Pcr_s = 3.0 * EI / L ** 2
    P = 0.6 * Pcr_s
    m = _column(L=L, P=P, m=mass)
    m.add_nonlinear_static_case("GPD", {"G": 1.0}, steps=4,
                                geometric="p_delta")
    m.modal_from_case = "GPD"
    m.validate()
    T = OpenSeesEngine(m).run_modal().periods[0]
    T_it = OpenSeesEngine(_column(L=L, P=P, m=mass,
                                  pdelta_method="iterative_loads")
                          ).run_modal().periods[0]
    T0 = OpenSeesEngine(_column(L=L, P=P, m=mass)).run_modal().periods[0]
    k0 = 3.0 * EI / L ** 3
    assert T0 == pytest.approx(2 * math.pi * math.sqrt(mass / k0),
                               rel=1e-9)
    assert T > T0
    assert T == pytest.approx(T0 / math.sqrt(1.0 - P / Pcr_s), rel=1e-6)
    assert T == pytest.approx(T_it, rel=1e-6)


def test_modal_from_frame_pdelta_matches_iterative():
    m = _frame()
    m.add_nonlinear_static_case("GPD", {"G": 1.0}, steps=2,
                                geometric="p_delta")
    m.modal_from_case = "GPD"
    m2 = _frame()
    m2.patterns["G"].member_udls.clear()       # keep the comparison exact
    m.patterns["G"].member_udls.clear()
    T = OpenSeesEngine(m).run_modal().periods
    m2.pdelta_options = {"method": "iterative_loads",
                         "load_factors": {"G": 1.0},
                         "max_iterations": 20, "tolerance": 1e-12}
    T_it = OpenSeesEngine(m2).run_modal().periods
    T0 = OpenSeesEngine(_frame()).run_modal().periods
    assert T[0] > T0[0]
    for a, b in zip(T[:2], T_it[:2]):          # translational modes
        assert a == pytest.approx(b, rel=1e-4)


def test_modal_from_yielded_state_softer():
    L = 3.0
    m = _column(L=L, m=10.0)
    m.add_nonlinear_static_case(
        "Y", {"H": 1.0}, load_application="displacement_control", steps=20,
        control_point=[0, 0, L], target_disp=0.03, hinges="column_base",
        default_My=30.0, hardening=0.05)
    T0 = OpenSeesEngine(m).run_modal().periods[0]
    m.modal_from_case = "Y"
    T = OpenSeesEngine(m).run_modal().periods[0]
    # hinge tangent b*k_t in series with the column: hand SDOF period
    k_t = 10.0 * 6.0 * EI / L
    b = 0.05 / (10.0 + 1.0 - 0.05 * 10.0)
    flex = L ** 3 / (3.0 * EI) + L ** 2 / (b * k_t)
    assert T > 2.0 * T0
    assert T == pytest.approx(2 * math.pi * math.sqrt(10.0 * flex),
                              rel=1e-3)


# --------------------------------------------------------------------------- #
# run control / combos / serialization / validation
# --------------------------------------------------------------------------- #
def test_run_control_dependencies():
    m = _frame()
    m.add_nonlinear_static_case("NG", {"G": 1.0}, steps=1)
    m.add_nonlinear_static_case("NEX", {"EX": 25.0}, start_from="NG")
    m.add_nonlinear_static_case("NG2", {"G": 1.0}, geometric="p_delta")
    m.add_nonlinear_static_case("NLX", {"EX": 1.0}, steps=1)
    m.add_pushover_case("PO", "X", start_from="NLX", target_drift=0.002,
                        steps=2)
    m.modal_from_case = "NG2"
    m.cases_not_run = ["NG", "NG2", "NLX"]
    m.validate()
    r = OpenSeesEngine(m).run()
    st = r.case_status
    assert st["NG"] == st["NG2"] == st["NLX"] == "run_as_dependency"
    assert st["NEX"] == "finished" and st["MODAL"] == "finished"
    assert set(r.nonlinear_static) == {"NG", "NEX", "NG2", "NLX"}
    assert "PO" in r.pushover
    # nothing needs them -> not run
    m.cases_not_run = ["NEX", "PO", "NLX"]
    m.modal_from_case = None
    r = OpenSeesEngine(m).run()
    assert r.case_status["NLX"] == "not_run"
    assert "NLX" not in r.nonlinear_static


def test_combo_rejects_nonlinear_static_case():
    m = _frame()
    m.add_nonlinear_static_case("NG", {"G": 1.0})
    with pytest.raises(ValueError, match="not superposable"):
        m.add_combo("C", {"NG": 1.0, "EX": 1.0})


def test_round_trip_and_results_json():
    m = _frame()
    m.add_nonlinear_static_case(
        "NG", [{"pattern": "G", "scale": 1.0}], steps=2, hinges="all_ends",
        My={"C00": 150.0}, hardening=0.03)
    m.add_nonlinear_static_case(
        "DC", {"EX": 1.0}, load_application="displacement_control",
        target_disp=0.002, control_point=[0, 0, 6.0], control_dof="UX",
        start_from="NG", steps=3)
    m.modal_from_case = "NG"
    d = m.to_dict()
    back = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert back.to_dict() == d
    assert back.nonlinear_static_cases["DC"].to_dict() == \
        m.nonlinear_static_cases["DC"].to_dict()
    assert back.modal_from_case == "NG"
    out = json.loads(json.dumps(OpenSeesEngine(back).run().to_dict()))
    dc = out["nonlinear_static"]["DC"]
    for key in ("node_disp", "reactions", "base", "member_forces",
                "member_stations", "story", "nonlinear"):
        assert key in dc
    nl = dc["nonlinear"]
    assert nl["chain"] == ["NG", "DC"]
    assert set(nl["history"]) == {"step", "load_factor", "disp", "base",
                                  "monitored"}
    assert len(nl["history"]["disp"]) == 4
    assert nl["history"]["disp"][-1] == pytest.approx(0.002, rel=1e-9)


@pytest.mark.parametrize("kw,msg", [
    ({"loads": {"NOPE": 1.0}}, "unknown load pattern"),
    ({"loads": {}}, "at least one load"),
    ({"load_application": "arc"}, "load_application"),
    ({"load_application": "displacement_control"}, "target_disp"),
    ({"geometric": "pdelta"}, "geometric"),
    ({"control_dof": "UQ"}, "control_dof"),
    ({"steps": 0}, "steps"),
    ({"control_story": "Nope"}, "control_story"),
    ({"control_point": [0, 0]}, "control_point"),
    ({"hinges": "bad"}, "hinges"),
    ({"My": {"ZZ": 1.0}}, "unknown member"),
    ({"hardening": 1.5}, "hardening"),
    ({"start_from": "missing"}, "unknown nonlinear static case"),
])
def test_validation_errors(kw, msg):
    m = _frame()
    loads = kw.pop("loads", {"G": 1.0})
    with pytest.raises(ValueError, match=msg):
        m.add_nonlinear_static_case("X", loads, **kw)
    assert "X" not in m.nonlinear_static_cases


def test_validation_chain_geometric_name_and_modal_from():
    m = _frame()
    m.add_nonlinear_static_case("A", {"G": 1.0}, geometric="p_delta")
    with pytest.raises(ValueError, match="same geometric"):
        m.add_nonlinear_static_case("B", {"EX": 1.0}, start_from="A")
    with pytest.raises(ValueError, match="already used"):
        m.add_nonlinear_static_case("G", {"G": 1.0})
    m.modal_from_case = "nope"
    with pytest.raises(ValueError, match="modal_from_case"):
        m.validate()
    with pytest.raises(TypeError):
        m.add_nonlinear_static_case("Z", {"G": 1.0}, bogus=1)


def test_defaults_byte_identical():
    m = _frame()
    d = m.to_dict()
    assert "nonlinear_static_cases" not in d
    assert "modal_from_case" not in d
    assert "nonlinear_static" not in m.case_kinds().values()
    base = OpenSeesEngine(m).run().to_dict()
    assert "nonlinear_static" not in base
    # adding a nonlinear static case leaves every other result untouched
    m2 = _frame()
    m2.add_nonlinear_static_case("NG", {"G": 1.0}, hinges="all_ends",
                                 default_My=100.0)
    other = OpenSeesEngine(m2).run().to_dict()
    other.pop("nonlinear_static")
    other["case_status"].pop("NG")
    assert json.dumps(other, sort_keys=True) == json.dumps(base,
                                                           sort_keys=True)


def test_failed_stage_marks_case_failed():
    """A load-controlled case past the mechanism load fails to converge:
    as the TARGET it returns the last converged step with a warning; as a
    chain START state it fails the dependent case (run() keeps going)."""
    L, My = 3.0, 30.0
    m = _column(L=L, m=0.0, H=2.0 * My / L)
    m.add_nonlinear_static_case("OVER", {"H": 1.0}, steps=4,
                                hinges="column_base", default_My=My,
                                hardening=0.0)
    m.add_nonlinear_static_case("NEXT", {"G": 1.0}, start_from="OVER",
                                hinges="column_base", default_My=My,
                                hardening=0.0)
    r = OpenSeesEngine(m).run()
    over = r.nonlinear_static["OVER"]
    assert not over.converged and over.warnings
    assert over.final_load_factor <= 0.5 + 1e-9
    assert r.case_status["NEXT"] == "failed"
