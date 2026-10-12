"""Multiple named mass sources, multiple modal cases (eigen / Ritz) and
Ritz as a usable basis on the FULL engine assembly.

Pins (hand / independent-path):

* a named mass source with half the pattern mass gives periods scaled by
  EXACTLY sqrt(0.5); an RS / TH case referencing that modal case equals
  the same case run on a model whose DEFAULT mass source is that source;
* a single Ritz vector started from a tip-load pattern reproduces the
  cantilever's static displacement EXACTLY by modal superposition
  (``phi phi^T P / w^2 = K^-1 P``); one eigenmode misses it by ~1%;
* Ritz on the engine assembly includes shells and links: with as many
  vectors as massed DOFs it equals the eigen solution, while the legacy
  frame-only numpy Ritz (walls skipped) is far off;
* RS on a full-span Ritz basis equals RS on the eigen basis;
* legacy models serialise / run byte-identically (no new keys).
"""

import math

import numpy as np
import pytest

from skyframe.core import modal_cases as mcs
from skyframe.core import model as model_mod
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 NodalLoad, NodalMass, PointSupport)
from skyframe.core.ritz import ritz_analysis
from skyframe.engine import modal_cases as mcx
from skyframe.engine.opensees_engine import OpenSeesEngine

ops = pytest.importorskip("openseespy.opensees")


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def _column(name="c3", n=3, h=3.0, masses=True):
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.num_modes = n
    mdl.add_material(Material("S", E=2.0e8, nu=0.3))
    mdl.add_section(FrameSection.rectangular("C", "S", 0.3, 0.3))
    mdl.set_stories([h] * n)
    for i in range(n):
        mdl.add_member("column", "C", (0, 0, h * i), (0, 0, h * (i + 1)),
                       story=f"Story{i + 1}", uid=f"C{i + 1}")
        if masses:
            mdl.nodal_masses.append(NodalMass((0, 0, h * (i + 1)),
                                              mx=10.0, my=10.0))
    return mdl


def _pattern_mass_frame():
    """Story masses come ONLY from the pattern "M" (nodal gravity loads):
    1 t per node per unit factor."""
    mdl = _column("pm", n=2, masses=False)
    mdl.rigid_diaphragms = True
    for i in range(2):
        for x in (0.0, 4.0):
            pass
    pat = mdl.pattern("M", "dead")
    for i in range(2):
        pat.nodal_loads.append(NodalLoad((0, 0, 3.0 * (i + 1)),
                                         fz=-model_mod.G_ACCEL * 10.0))
    mdl.mass_source = {"M": 1.0}
    pat2 = mdl.pattern("EX", "quake")
    pat2.nodal_loads.append(NodalLoad((0, 0, 6.0), fx=5.0))
    mdl.add_case("EX", {"EX": 1.0})
    return mdl


def _portal_wall(with_wall=True):
    H = span = 3.0
    mdl = BuildingModel(name="pw")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=2.5e7, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.35, 0.35))
    mdl.add_section(FrameSection.rectangular("BM", "CONC", 0.3, 0.5))
    mdl.set_stories([H])
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, H), story="Story1",
                   uid="CL")
    mdl.add_member("column", "COL", (span, 0, 0), (span, 0, H),
                   story="Story1", uid="CR")
    mdl.add_member("beam", "BM", (0, 0, H), (span, 0, H), story="Story1",
                   uid="BM")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.supports.append(PointSupport((span, 0, 0), (1, 1, 1, 1, 1, 1)))
    if with_wall:
        mdl.shell_sections["SH"] = model_mod.ShellSection(
            name="SH", material="CONC", thickness=0.15)
        mdl.shells.append(model_mod.ShellRegion(
            uid="W1", kind="wall", behavior="shell", section="SH",
            corners=[(0, 0, 0), (span, 0, 0), (span, 0, H), (0, 0, H)],
            mesh_size=0.5, story="Story1"))
    for x in (0.0, span):
        mdl.nodal_masses.append(NodalMass((x, 0, H), mx=20.0))
    mdl.num_modes = 2
    return mdl


# --------------------------------------------------------------------------- #
# model data
# --------------------------------------------------------------------------- #
def test_legacy_model_has_no_new_keys_and_roundtrip():
    mdl = _pattern_mass_frame()
    d = mdl.to_dict()
    for key in ("mass_sources", "modal_cases"):
        assert key not in d
    mdl.add_rs_case("RS", "X", [[0.0, 0.5], [2.0, 0.5]])
    d = mdl.to_dict()
    assert "modal_case" not in d["rs_cases"]["RS"]
    mdl.mass_sources = {"HALF": {"mass_source": {"M": 0.5}}}
    mdl.modal_cases = {
        "MH": {"type": "eigen", "num_modes": 2, "mass_source": "HALF"},
        "RZ": {"type": "ritz", "num_modes": 2,
               "ritz_starts": [{"type": "pattern", "name": "EX"},
                               {"type": "accel", "dir": "UX"}]}}
    mdl.rs_cases["RS"].modal_case = "MH"
    mdl.add_th_case("TH", "X", [0.0, 1.0, 0.0], 0.01)
    mdl.th_cases["TH"].modal_case = "RZ"
    d = mdl.to_dict()
    m2 = BuildingModel.from_dict(d)
    assert m2.to_dict() == d
    assert d["modal_cases"]["MH"] == {"type": "eigen", "num_modes": 2,
                                      "mass_source": "HALF",
                                      "stiffness_case": None}
    assert d["modal_cases"]["RZ"]["max_cycles"] == 0
    assert d["mass_sources"]["HALF"]["options"]["include_lateral"] is True
    assert d["rs_cases"]["RS"]["modal_case"] == "MH"
    assert d["th_cases"]["TH"]["modal_case"] == "RZ"
    assert m2.case_kinds()["MH"] == "modal"


@pytest.mark.parametrize("mutate,msg", [
    (lambda d: d["modal_cases"]["MH"].update(mass_source="NOPE"),
     "unknown mass source"),
    (lambda d: d["mass_sources"]["HALF"]["mass_source"].update(Q=1.0),
     "unknown load pattern"),
    (lambda d: d["modal_cases"].update(EX={"type": "eigen"}),
     "already used"),
    (lambda d: d["modal_cases"].update(MODAL={"type": "eigen"}),
     "reserved"),
    (lambda d: d["modal_cases"]["MH"].update(type="lanczos"), "type"),
    (lambda d: d["modal_cases"]["MH"].update(num_modes=0), "num_modes"),
    (lambda d: d["modal_cases"].update(R2={
        "type": "ritz", "ritz_starts": [{"type": "accel", "dir": "UQ"}]}),
     "dir"),
    (lambda d: d["modal_cases"].update(R3={
        "type": "ritz", "ritz_starts": []}), "at least one"),
    (lambda d: d["modal_cases"].update(R4={
        "type": "ritz", "stiffness_case": "X"}), "eigen cases only"),
    (lambda d: d["rs_cases"]["RS"].update(modal_case="ZZ"),
     "unknown modal case"),
    (lambda d: d["th_cases"]["TH"].update(initial_case="ZZ"),
     "nonlinear static"),
    (lambda d: d["mass_sources"]["HALF"].update(
        options={"include_lateral": False, "include_vertical": False}),
     "lateral"),
])
def test_validation_errors(mutate, msg):
    mdl = _pattern_mass_frame()
    mdl.add_rs_case("RS", "X", [[0.0, 0.5], [2.0, 0.5]])
    mdl.add_th_case("TH", "X", [0.0, 1.0, 0.0], 0.01)
    mdl.mass_sources = {"HALF": {"mass_source": {"M": 0.5}}}
    mdl.modal_cases = {"MH": {"type": "eigen", "num_modes": 2,
                              "mass_source": "HALF"}}
    d = mdl.to_dict()
    mutate(d)
    with pytest.raises(ValueError, match=msg):
        BuildingModel.from_dict(d)


# --------------------------------------------------------------------------- #
# named mass sources
# --------------------------------------------------------------------------- #
def test_named_mass_source_scales_periods_exactly():
    mdl = _pattern_mass_frame()
    mdl.mass_sources = {"HALF": {"mass_source": {"M": 0.5}}}
    mdl.modal_cases = {"MH": {"type": "eigen", "num_modes": 2,
                              "mass_source": "HALF"}}
    eng = OpenSeesEngine(mdl)
    base = eng.run_modal(2).periods
    half = mcx.run_modal_case(eng, "MH").periods
    for t0, t1 in zip(base, half):
        assert t1 == pytest.approx(t0 * math.sqrt(0.5), rel=1e-9)


def test_rs_and_th_with_modal_case_equal_default_mass_source_run():
    mdl = _pattern_mass_frame()
    mdl.mass_sources = {"HALF": {"mass_source": {"M": 0.5}}}
    mdl.modal_cases = {"MH": {"type": "eigen", "num_modes": 2,
                              "mass_source": "HALF"}}
    mdl.add_rs_case("RS", "X", [[0.0, 0.3], [0.5, 1.0], [3.0, 0.2]],
                    combo_method="SRSS")
    mdl.rs_cases["RS"].modal_case = "MH"
    acc = [2.0 * math.sin(math.pi * k / 20) for k in range(21)] + [0.0] * 80
    mdl.add_th_case("TH", "X", acc, 0.01, damping=0.05)
    mdl.th_cases["TH"].modal_case = "MH"
    ref = _pattern_mass_frame()
    ref.mass_source = {"M": 0.5}
    ref.num_modes = 2
    ref.add_rs_case("RS", "X", [[0.0, 0.3], [0.5, 1.0], [3.0, 0.2]],
                    combo_method="SRSS")
    ref.add_th_case("TH", "X", acc, 0.01, damping=0.05)
    a = OpenSeesEngine(mdl)
    b = OpenSeesEngine(ref)
    _close(a.run_response_spectrum("RS").to_dict(),
           b.run_response_spectrum("RS").to_dict())
    _close(a.run_time_history("TH").to_dict(),
           b.run_time_history("TH").to_dict())


def _close(x, y, tol=1e-12):
    """Recursive equality; floats to ``tol`` absolute (degenerate X/Y
    eigenpairs of the symmetric column leave ~1e-18 noise in the cross
    direction, which is solver-basis dependent)."""
    if isinstance(x, dict):
        assert set(x) == set(y)
        for k in x:
            _close(x[k], y[k], tol)
    elif isinstance(x, (list, tuple)):
        assert len(x) == len(y)
        for u, v in zip(x, y):
            _close(u, v, tol)
    elif isinstance(x, float):
        assert x == pytest.approx(y, rel=1e-9, abs=tol)
    else:
        assert x == y


# --------------------------------------------------------------------------- #
# Ritz on the engine assembly
# --------------------------------------------------------------------------- #
def test_ritz_one_vector_from_tip_load_is_exact_static():
    """Cantilever, 3 lumped masses, tip load pattern: ONE load-dependent
    Ritz vector reproduces the static displacement exactly; one eigenmode
    does not (~1% error)."""
    mdl = _column()
    pat = mdl.pattern("TIP", "other")
    pat.nodal_loads.append(NodalLoad((0, 0, 9.0), fx=10.0))
    mdl.add_case("TIP", {"TIP": 1.0})
    mdl.modal_cases = {
        "RZ1": {"type": "ritz", "num_modes": 1,
                "ritz_starts": [{"type": "pattern", "name": "TIP"}]},
        "EIG1": {"type": "eigen", "num_modes": 1}}
    mdl.validate()
    eng = OpenSeesEngine(mdl)
    st = eng.run_static("TIP").node_disp
    tip = max(abs(v[0]) for v in st.values())
    errs = {}
    for nm in ("RZ1", "EIG1"):
        sub = mcx.sub_engine(eng, nm)
        res = sub.run_modal()
        assert len(res.periods) == 1
        w2 = (2.0 * math.pi / res.periods[0]) ** 2
        phi = res.shapes[1]
        t_tip = next(t for t, c in sub._asm.node_coords.items()
                     if abs(c[2] - 9.0) < 1e-9)
        phiP = phi[t_tip][0] * 10.0
        errs[nm] = max(float(np.max(np.abs(np.array(phi[t]) * phiP / w2
                                           - np.array(d))))
                       for t, d in st.items()) / tip
    assert errs["RZ1"] <= 1e-12
    assert 0.005 < errs["EIG1"] < 0.02


def test_ritz_full_span_equals_eigen_and_rs():
    """As many UX/UY Ritz vectors as massed DOFs span the whole space:
    Ritz values == eigenvalues and RS on the Ritz basis == RS on eigen."""
    mdl = _column()
    mdl.num_modes = 6
    mdl.modal_cases = {"RZ": {"type": "ritz", "num_modes": 6}}
    sp = [[0.0, 0.4], [0.3, 1.0], [2.0, 0.3]]
    mdl.add_rs_case("RS", "X", sp, combo_method="CQC")
    mdl.add_rs_case("RSR", "X", sp, combo_method="CQC")
    mdl.rs_cases["RSR"].modal_case = "RZ"
    eng = OpenSeesEngine(mdl)
    eig = eng.run_modal()
    rz = mcx.run_modal_case(eng, "RZ")
    assert sorted(rz.periods, reverse=True) == pytest.approx(
        sorted(eig.periods, reverse=True), rel=1e-8)
    a = eng.run_response_spectrum("RS")
    b = eng.run_response_spectrum("RSR")
    for t, v in a.node_disp.items():
        assert b.node_disp[t] == pytest.approx(v, rel=1e-6, abs=1e-12)
    assert b.base["FX"] == pytest.approx(a.base["FX"], rel=1e-8)


def test_ritz_includes_shells_unlike_legacy_frame_only():
    mdl = _portal_wall()
    mdl.modal_cases = {"RZ": {"type": "ritz", "num_modes": 2,
                              "ritz_starts": [{"type": "accel",
                                               "dir": "UX"}]}}
    eng = OpenSeesEngine(mdl)
    eig = eng.run_modal()
    rz = mcx.run_modal_case(eng, "RZ")
    # the second X vector (beam-axial mode, ~1e-17 relative M-norm after
    # orthogonalization) is exhausted: one vector, the wall-stiff sway
    assert len(rz.periods) == 1
    assert rz.periods[0] == pytest.approx(eig.periods[0], rel=1e-6)
    legacy = ritz_analysis(mdl, 2, "X")
    assert any("shell" in w for w in legacy.warnings)
    assert legacy.periods[0] > 1.5 * rz.periods[0]   # wall stiffness lost


def test_ritz_includes_links_and_link_start_vectors():
    mdl = _column(n=1)
    mdl.nodal_masses[0] = NodalMass((0, 0, 3.0), mx=10.0)
    mdl.add_link((0, 0, 3.0), (2.0, 0, 3.0), stiffness=[500.0, 0, 0, 0, 0, 0])
    for p in ((2.0, 0, 3.0), (0, 0, 0)):     # explicit supports replace
        mdl.supports.append(PointSupport(p, (1, 1, 1, 1, 1, 1)))  # auto
    mdl.add_link((0, 0, 3.0), (-2.0, 0, 3.0), link_type="damper",
                 params={"cd": 10.0})
    mdl.modal_cases = {
        "RZ": {"type": "ritz", "num_modes": 1,
               "ritz_starts": [{"type": "accel", "dir": "UX"}]},
        "RL": {"type": "ritz", "num_modes": 1,
               "ritz_starts": [{"type": "links"}]}}
    mdl.num_modes = 1
    eng = OpenSeesEngine(mdl)
    eig = eng.run_modal()
    assert mcx.run_modal_case(eng, "RZ").periods == pytest.approx(
        eig.periods, rel=1e-9)
    # the damper's axial unit-pair start vector reaches the same mode
    assert mcx.run_modal_case(eng, "RL").periods == pytest.approx(
        eig.periods, rel=1e-9)
    # hand check: k = column 3EI/L^3 + elastic link
    I = 0.3 ** 4 / 12.0
    k = 3 * 2.0e8 * I / 27.0 + 500.0
    assert eig.periods[0] == pytest.approx(2 * math.pi * math.sqrt(10.0 / k),
                                           rel=2e-3)
    m2 = _column(n=1)
    m2.modal_cases = {"RL": {"type": "ritz", "num_modes": 2,
                             "ritz_starts": [{"type": "links"},
                                             {"type": "accel", "dir": "UX"}]}}
    sub = mcx.sub_engine(OpenSeesEngine(m2), "RL")
    sub.run_modal()
    assert any("no nonlinear link" in w for w in sub._ritz_warnings)


# --------------------------------------------------------------------------- #
# run() integration + Set Load Cases to Run
# --------------------------------------------------------------------------- #
def test_run_reports_modal_cases_and_dependencies():
    mdl = _pattern_mass_frame()
    mdl.mass_sources = {"HALF": {"mass_source": {"M": 0.5}}}
    mdl.modal_cases = {"MH": {"type": "eigen", "num_modes": 2,
                              "mass_source": "HALF"},
                       "RZ": {"type": "ritz", "num_modes": 2}}
    mdl.add_rs_case("RS", "X", [[0.0, 0.5], [2.0, 0.5]])
    mdl.rs_cases["RS"].modal_case = "MH"
    mdl.cases_not_run = ["MODAL", "MH"]
    res = OpenSeesEngine(mdl).run().to_dict()
    assert res["case_status"]["MODAL"] == "not_run"     # RS uses MH only
    assert res["case_status"]["MH"] == "run_as_dependency"
    assert res["case_status"]["RZ"] == "finished"
    assert set(res["modal_cases"]) == {"MH", "RZ"}
    assert res["modal_cases"]["MH"]["mass_source"] == "HALF"
    assert res["modal_cases"]["RZ"]["type"] == "ritz"
    assert "RS" in res["rs_cases"]
    legacy = OpenSeesEngine(_pattern_mass_frame()).run().to_dict()
    assert "modal_cases" not in legacy


# --------------------------------------------------------------------------- #
# API + database tables
# --------------------------------------------------------------------------- #
def test_api_modal_case_and_ritz_routing(api_client):
    mdl = _portal_wall()
    mdl.modal_cases = {"RZ": {"type": "ritz", "num_modes": 2}}
    assert api_client.post("/api/model", json=mdl.to_dict()).status_code \
        == 200
    r = api_client.post("/api/analyze/modal_case", json={"case": "RZ"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["type"] == "ritz" and len(body["periods"]) >= 1
    assert body["warnings"]                  # subspace exhausted (see above)
    assert api_client.post("/api/analyze/modal_case",
                           json={"case": "NOPE"}).status_code == 400
    # a wall model routes /api/analyze/ritz to the engine assembly
    r = api_client.post("/api/analyze/ritz", json={"n": 2, "direction": "X"})
    assert r.status_code == 200 and r.get_json()["assembly"] == "engine"
    assert r.get_json()["periods"][0] == pytest.approx(
        body["periods"][0], rel=1e-12)
    # frame-only legacy request: unchanged numpy path
    fr = _column()
    assert api_client.post("/api/model", json=fr.to_dict()).status_code \
        == 200
    r = api_client.post("/api/analyze/ritz", json={"n": 2, "direction": "X"})
    assert r.get_json() == ritz_analysis(fr, 2, "X").to_dict()
    r = api_client.post("/api/analyze/ritz_full",
                        json={"n": 2, "starts": [{"type": "accel",
                                                  "dir": "UX"}]})
    assert r.status_code == 200 and len(r.get_json()["periods"]) == 2


def test_db_tables_modal_cases_and_mass_sources():
    from skyframe.core import modeltables as mt
    mdl = _pattern_mass_frame()
    mdl.mass_sources = {"HALF": {"mass_source": {"M": 0.5}}}
    mdl.modal_cases = {"MH": {"type": "eigen", "num_modes": 2,
                              "mass_source": "HALF"}}
    mdl.add_rs_case("RS", "X", [[0.0, 0.5], [2.0, 0.5]])
    mdl.rs_cases["RS"].modal_case = "MH"
    d = mdl.to_dict()
    keys = {t["key"] for t in mt.list_tables(d)}
    assert {"mass_sources", "mass_source_patterns", "modal_cases"} <= keys
    rows = mt.get_table(d, "modal_cases")["rows"]
    assert rows[0]["name"] == "MH" and rows[0]["mass_source"] == "HALF"
    # identity apply
    new, errs, _ = mt.apply_tables(d, {"modal_cases": rows})
    assert not errs and new.to_dict() == d
    # rename cascades into the RS case; add a Ritz case by row
    rows2 = [dict(rows[0], name="MH2"),
             {"name": "RZ", "type": "ritz", "num_modes": 3,
              "ritz_starts": "UX; pattern:EX"}]
    new, errs, _ = mt.apply_tables(d, {"modal_cases": rows2})
    assert not errs, errs
    nd = new.to_dict()
    assert nd["rs_cases"]["RS"]["modal_case"] == "MH2"
    assert nd["modal_cases"]["RZ"]["ritz_starts"] == [
        {"type": "accel", "dir": "UX"}, {"type": "pattern", "name": "EX"}]
    # deleting a referenced mass source is refused
    new, errs, _ = mt.apply_tables(d, {"mass_sources": []})
    assert errs and new is None
    pr = mt.get_table(d, "mass_source_patterns")["rows"]
    assert pr == [{"_id": ["HALF", "M"], "source": "HALF", "pattern": "M",
                   "factor": 0.5}]
