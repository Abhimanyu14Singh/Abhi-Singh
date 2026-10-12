"""Wave C: section-cut / pier / spandrel forces for every case type, TH
shell results, Max/Min table rows and the results comparison
(CONTRACT "Cut, pier and spandrel forces for every case type, TH shell
results and Compare Results").

Closed-form pins:

* RS on a SINGLE-mode model: the only mode's load is Gamma*Sa*g*m*phi =
  Sa*g*m at the one massed DOF (Gamma*phi = 1), so every RS cut / pier /
  spandrel value equals |static value under a unit-direction load F =
  Sa g m| — checked against a static case with the force applied.
* TH: pier shear of the two base piers sums to minus the base reaction
  history (exact nodal free body; reactions and element forces are both
  resisting forces); a frame section cut below the only mass equals the
  base reaction history.
* Envelope combos bracket their (factored) members; an add combo with an
  RS member is +/- the RS value around the static one.
* Nonlinear static (linear material, load control): step i forces are
  i/n of the final state.
* TH shell forces at a step equal a static solve with the step's nodal
  displacements imposed (linear model).
"""

import copy
import json
import math
import warnings

import numpy as np
import openseespy.opensees as ops
import pytest

from skyframe.core import results_diff as RD
from skyframe.core import shell_results as SR
from skyframe.core import tables
from skyframe.core import th_output as THO
from skyframe.core.model import (BuildingModel, FrameSection, LoadCase,
                                 LoadPattern, Material, NodalLoad,
                                 NodalMass, PointSupport, ShellRegion,
                                 ShellSection)
from skyframe.engine import cut_results as CX
from skyframe.engine.opensees_engine import OpenSeesEngine

E = 25e6
G = 9.80665
SA = 0.5
MASS = 10.0
ACC = [math.sin(2 * math.pi * 5 * k * 0.005) for k in range(200)]


def _coupled_wall():
    """Two 2 m piers + a 2 m x 1 m shell spandrel, one 3 m story, one
    lumped mass (UX only) at the top corner -> exactly one dynamic DOF."""
    m = BuildingModel(name="cw")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E, 0.2, 0.0))
    m.add_shell_section(ShellSection("W", "C", 0.3))
    m.set_stories([3.0])
    for uid, x0 in (("P1", 0.0), ("P2", 4.0)):
        m.shells.append(ShellRegion(
            uid, "wall", "shell", "W",
            [(x0, 0, 0), (x0 + 2, 0, 0), (x0 + 2, 0, 3), (x0, 0, 3)],
            mesh_size=0.5, story="Story1", pier=uid))
    m.shells.append(ShellRegion(
        "S1", "wall", "shell", "W", [(2, 0, 2), (4, 0, 2), (4, 0, 3),
                                     (2, 0, 3)],
        mesh_size=0.5, story="Story1", spandrel="SP1"))
    m.patterns["LAT"] = LoadPattern("LAT", "other")
    m.patterns["LAT"].nodal_loads.append(NodalLoad((0, 0, 3), 100.0, 0, 0))
    m.cases["LAT"] = LoadCase("LAT", {"LAT": 1.0})
    m.nodal_masses.append(NodalMass((0, 0, 3), mx=MASS))
    m.num_modes = 1
    m.add_rs_case("RSX", "X", spectrum=[[0, SA], [10, SA]], num_modes=1)
    m.add_th_case("THX", "X", accel=ACC, dt=0.005)
    m.validate()
    return m


def _column():
    """3 m cantilever column, lumped UX mass at the top, horizontal
    section cut at mid-height."""
    m = BuildingModel(name="col")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E, 0.2, 0.0))
    m.add_section(FrameSection.rectangular("C40", "C", 0.4, 0.4))
    m.set_stories([3.0])
    m.add_member("column", "C40", (0, 0, 0), (0, 0, 3), story="Story1",
                 uid="C1")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.patterns["LAT"] = LoadPattern("LAT", "other")
    m.patterns["LAT"].nodal_loads.append(NodalLoad((0, 0, 3), 1.0, 0, 0))
    m.cases["LAT"] = LoadCase("LAT", {"LAT": 1.0})
    m.nodal_masses.append(NodalMass((0, 0, 3), mx=MASS))
    m.num_modes = 1
    m.add_section_cut("MID", "z", 1.5)
    m.add_rs_case("RSX", "X", spectrum=[[0, SA], [10, SA]], num_modes=1)
    m.add_th_case("THX", "X", accel=ACC, dt=0.005)
    m.validate()
    return m


def _run(m):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        eng = OpenSeesEngine(m)
        res = eng.run()
    return eng, res, res.to_dict()


@pytest.fixture(scope="module")
def wall():
    m = _coupled_wall()
    m.th_cases["THX"].th_output = {"shells": True, "steps": [50, 100]}
    m.add_combo("ENV", {"LAT": 1.0, "RSX": 0.5}, combo_type="envelope")
    m.add_combo("ADD", {"LAT": 1.0, "RSX": 1.0})
    m.add_combo("ENVTH", {"LAT": 1.0, "THX": 1.0}, combo_type="envelope")
    m.add_nonlinear_static_case("NLS", [{"pattern": "LAT", "scale": 1.0}],
                                steps=4)
    m.validate()
    return (m,) + _run(m)


@pytest.fixture(scope="module")
def column():
    m = _column()
    return (m,) + _run(m)


# --------------------------------------------------------------------------- #
# response spectrum
# --------------------------------------------------------------------------- #
def test_rs_pier_and_spandrel_single_mode_equal_scaled_static(wall):
    _m, _e, _r, d = wall
    F = SA * G * MASS                       # the single mode's load (kN)
    f = F / 100.0                           # static LAT = 100 kN at the top
    for p in ("P1", "P2"):
        s = d["piers"]["LAT"][p]["Story1"]
        r = d["piers"]["RSX"][p]["Story1"]
        for k in ("P", "V", "M"):
            assert r[k] == pytest.approx(abs(s[k]) * f, rel=1e-9)
        assert "min" not in r
    s = d["spandrels"]["LAT"]["SP1"]["Story1"]
    r = d["spandrels"]["RSX"]["SP1"]["Story1"]
    for side in ("Left", "Right"):
        for k in CX.SPD_KEYS:
            assert r[side][k] == pytest.approx(abs(s[side][k]) * f,
                                               rel=1e-9, abs=1e-9)
    # the pseudo carrier never reaches the results
    assert CX.PSEUDO not in d["rs_cases"]["RSX"]["member_stations"]


def test_rs_section_cut_single_mode(column):
    _m, _e, _r, d = column
    F = SA * G * MASS
    s = d["section_cuts"]["LAT"]["MID"]     # unit static load
    r = d["section_cuts"]["RSX"]["MID"]
    for k in CX.CUT_KEYS:
        assert r[k] == pytest.approx(abs(s[k]) * F, rel=1e-9, abs=1e-12)
    # closed form: |FX| = F, |MY| = F * 1.5 (moment arm above the cut)
    assert r["FX"] == pytest.approx(F, rel=1e-9)
    assert r["MY"] == pytest.approx(1.5 * F, rel=1e-9)
    assert r["n_members"] == 1 and r["warnings"] == []


# --------------------------------------------------------------------------- #
# time history
# --------------------------------------------------------------------------- #
def test_th_pier_shears_sum_to_base_reaction_history(wall):
    _m, _e, _r, d = wall
    th = d["th_cases"]["THX"]
    cf = th["cut_forces"]
    assert cf["t"] == th["t"]
    v = (np.asarray(cf["piers"]["P1"]["Story1"]["V"])
         + np.asarray(cf["piers"]["P2"]["Story1"]["V"]))
    b = np.asarray(th["base_FX"])
    assert np.max(np.abs(b)) > 1.0
    assert np.max(np.abs(v + b)) < 1e-9 * np.max(np.abs(b))
    # peaks in the standard block = max / min of the history
    e = d["piers"]["THX"]["P1"]["Story1"]
    h = np.asarray(cf["piers"]["P1"]["Story1"]["V"])
    assert e["V"] == pytest.approx(h.max()) and \
        e["min"]["V"] == pytest.approx(h.min())


def test_th_section_cut_equals_base_reaction_history(column):
    _m, _e, _r, d = column
    th = d["th_cases"]["THX"]
    fx = np.asarray(th["cut_forces"]["section_cuts"]["MID"]["FX"])
    b = np.asarray(th["base_FX"])
    assert np.max(np.abs(fx - b)) < 1e-9 * np.max(np.abs(b))
    e = d["section_cuts"]["THX"]["MID"]
    assert e["FX"] == pytest.approx(b.max()) and \
        e["min"]["FX"] == pytest.approx(b.min())


# --------------------------------------------------------------------------- #
# envelopes / combos
# --------------------------------------------------------------------------- #
def test_envelope_pier_forces_bracket_members(wall):
    _m, _e, _r, d = wall
    for p in ("P1", "P2"):
        env = d["piers"]["ENV"][p]["Story1"]
        lat = d["piers"]["LAT"][p]["Story1"]
        rs = d["piers"]["RSX"][p]["Story1"]
        for k in ("P", "V", "M"):
            members = [lat[k], 0.5 * rs[k], -0.5 * rs[k]]
            assert env[k] == pytest.approx(max(members))
            assert env["min"][k] == pytest.approx(min(members))
            assert env["min"][k] <= lat[k] <= env[k]
        envth = d["piers"]["ENVTH"][p]["Story1"]
        th = d["piers"]["THX"][p]["Story1"]
        for k in ("P", "V", "M"):
            assert envth[k] == pytest.approx(max(lat[k], th[k]))
            assert envth["min"][k] == pytest.approx(min(lat[k],
                                                        th["min"][k]))
        add = d["piers"]["ADD"][p]["Story1"]
        for k in ("P", "V", "M"):
            assert add[k] == pytest.approx(lat[k] + rs[k])
            assert add["min"][k] == pytest.approx(lat[k] - rs[k])
    sp = d["spandrels"]["ENV"]["SP1"]["Story1"]
    assert sp["Left"]["V2"] >= sp["min"]["Left"]["V2"]


def test_legacy_entries_unchanged_and_static_values_match_kernel(wall):
    m, eng, res, d = wall
    geo = CX.geometry(eng, eng._asm)
    v = geo.values(res.cases["LAT"].member_stations,
                   res.cases["LAT"].shell_nodal)
    c, p, s = geo.blocks(v)
    assert p == d["piers"]["LAT"]
    for lab, by in s.items():
        for st, e in by.items():
            for side in ("Left", "Right"):
                for k in CX.SPD_KEYS:
                    assert e[side][k] == pytest.approx(
                        d["spandrels"]["LAT"][lab][st][side][k], abs=1e-9)


# --------------------------------------------------------------------------- #
# nonlinear static
# --------------------------------------------------------------------------- #
def test_nonlinear_static_per_step_cut_forces(wall):
    _m, _e, _r, d = wall
    hist = d["nonlinear_static"]["NLS"]["nonlinear"]["history"]
    cf = hist["cut_forces"]
    assert cf["step"] == hist["step"] == [0, 1, 2, 3, 4]
    v = cf["piers"]["P1"]["Story1"]["V"]
    final = d["piers"]["LAT"]["P1"]["Story1"]["V"]
    for i, x in enumerate(v):
        assert x == pytest.approx(final * i / 4.0, rel=1e-9, abs=1e-9)
    assert d["piers"]["NLS"]["P1"]["Story1"]["V"] == pytest.approx(final)


# --------------------------------------------------------------------------- #
# TH shell forces
# --------------------------------------------------------------------------- #
def test_th_shell_forces_equal_static_solve_with_step_displacements():
    m = _coupled_wall()
    m.th_cases["THX"].th_output = {"shells": True, "steps": [50]}
    m.validate()
    captured = {}
    orig = CX.THRecorder.record

    def spy(self, k):
        orig(self, k)
        if k == 49:
            captured["u"] = {t: list(ops.nodeDisp(t))
                             for t in self.asm.node_coords}
    CX.THRecorder.record = spy
    try:
        _e, _r, d = _run(m)
    finally:
        CX.THRecorder.record = orig
    snap = d["th_cases"]["THX"]["shell_forces"]["at"]["50"]
    # static solve with the same nodal displacements imposed
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        eng = OpenSeesEngine(m)
        asm = eng._build()
    sup = set(asm.support_tags)
    ops.timeSeries("Constant", 77)
    ops.pattern("Plain", 77, 77)
    for t, u in captured["u"].items():
        if t in sup:
            continue
        for dof in range(1, 7):
            ops.sp(t, dof, float(u[dof - 1]))
    ops.wipeAnalysis()
    ops.constraints("Transformation")
    ops.numberer("RCM")
    ops.system("UmfPack")
    ops.algorithm("Linear")
    ops.integrator("LoadControl", 1.0)
    ops.analysis("Static")
    assert ops.analyze(1) == 0
    ref = OpenSeesEngine._shell_outputs(asm)
    peak = max(abs(x) for v in ref.values() for x in v)
    assert peak > 1e-3
    for qi, v in ref.items():
        assert np.allclose(snap[str(qi)], v, rtol=1e-7, atol=1e-9 * peak)


def test_th_shell_envelope_contours_and_tables(wall):
    m, _e, _r, d = wall
    sf = d["th_cases"]["THX"]["shell_forces"]
    assert sf["steps"] == [50, 100] and sf["t"] == [0.25, 0.5]
    names = [s["name"] for s in SR.list_sources(m, d)]
    assert "THX" in names and "THX @ step 50" in names
    hi = SR.contour(m, d, "THX", "F22", bound="max")
    lo = SR.contour(m, d, "THX", "F22", bound="min")
    st = SR.contour(m, d, "THX @ step 50", "F22")
    assert hi["bounded"] and not st["bounded"]
    for a, b, c in zip(lo["values"], st["values"], hi["values"]):
        assert a - 1e-9 <= b <= c + 1e-9
    rows = tables.compute_table("shell_forces", d, m,
                                cases=["THX"])["rows"]
    assert {r["step_type"] for r in rows} == {"Max", "Min"}
    assert {r["case_type"] for r in rows} == {"LinDirHist"}


def test_th_output_validation_and_roundtrip():
    m = _coupled_wall()
    m.th_cases["THX"].th_output = {"shells": True, "steps": [3, 1, 3],
                                   "every": 10}
    m.validate()
    assert m.th_cases["THX"].th_output == {"shells": True, "steps": [1, 3],
                                           "every": 10}
    assert THO.normalize({"shells": False, "frames": 0}) == {}
    d = m.to_dict()
    m2 = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert m2.to_dict() == d
    for bad in ({"shells": True, "steps": [0]}, {"every": -1},
                {"foo": 1}, {"steps": "x"}):
        with pytest.raises(ValueError):
            THO.normalize(bad)
    # legacy: no key emitted
    assert "th_output" not in _coupled_wall().to_dict()["th_cases"]["THX"]


# --------------------------------------------------------------------------- #
# tables
# --------------------------------------------------------------------------- #
def test_pier_table_max_min_rows(wall):
    m, _e, _r, d = wall
    t = tables.compute_table("pier_forces", d, m, cases=["THX", "RSX"])
    assert [c["key"] for c in t["columns"]][4] == "step_type"
    th = [r for r in t["rows"] if r["case"] == "THX" and r["pier"] == "P1"]
    assert [r["step_type"] for r in th] == ["Max", "Min"]
    e = d["piers"]["THX"]["P1"]["Story1"]
    assert th[0]["P"] == pytest.approx(-e["min"]["P"])   # tension-positive
    assert th[1]["P"] == pytest.approx(-e["P"])
    assert th[0]["V2"] == pytest.approx(e["V"])
    rs = [r for r in t["rows"] if r["case"] == "RSX"]
    assert all(r["case_type"] == "LinRespSpec" and r["P"] >= 0 for r in rs)


def test_legacy_tables_unchanged_without_new_case_types():
    m = _coupled_wall()
    del m.rs_cases["RSX"]
    del m.th_cases["THX"]
    _e, _r, d = _run(m)
    t = tables.compute_table("pier_forces", d, m)
    assert "step_type" not in [c["key"] for c in t["columns"]]
    assert all("step_type" not in r for r in t["rows"])


# --------------------------------------------------------------------------- #
# results comparison
# --------------------------------------------------------------------------- #
def test_compare_results_flags_a_changed_value(wall):
    _m, _e, _r, d = wall
    same = RD.diff(d, copy.deepcopy(d), rtol=1e-9)
    assert same["equal"] and same["n_flagged"] == 0
    assert same["n_compared"] > 1000
    d2 = copy.deepcopy(d)
    d2["piers"]["LAT"]["P1"]["Story1"]["V"] *= 1.01
    out = RD.diff(d, d2, rtol=1e-6)
    assert not out["equal"] and out["n_flagged"] == 1
    row = out["rows"][0]
    assert row["path"] == "piers.LAT.P1.Story1.V"
    assert row["case"] == "LAT" and row["quantity"] == "piers"
    v0 = d["piers"]["LAT"]["P1"]["Story1"]["V"]
    assert row["diff"] == pytest.approx(0.01 * v0, rel=1e-9)
    assert 0 < row["rel"] <= 0.01 / 1.01 + 1e-12   # family-scaled
    # below tolerance -> not flagged
    assert RD.diff(d, d2, rtol=0.05)["equal"]
    # filters
    assert RD.diff(d, d2, case="RSX")["equal"]
    assert RD.diff(d, d2, quantity="node_disp")["equal"]
    assert RD.diff(d, d2, case="LAT", quantity="piers")["n_flagged"] == 1
    # structural difference + wrapper unwrap
    d3 = copy.deepcopy(d)
    del d3["cases"]["LAT"]["base"]
    out = RD.diff({"results": d}, d3)
    assert any(r["status"] == "only_a" and r["path"] == "cases.LAT.base"
               for r in out["rows"])
    with pytest.raises(ValueError):
        RD.unwrap({"foo": 1})


def test_compare_endpoint(wall):
    from skyframe.api.server import create_app
    _m, _e, _r, d = wall
    app = create_app()
    cl = app.test_client()
    d2 = copy.deepcopy(d)
    d2["cases"]["LAT"]["base"]["FX"] += 5.0
    r = cl.post("/api/results/compare", json={"a": d, "b": d2,
                                              "rtol": 1e-6})
    assert r.status_code == 200
    j = r.get_json()
    assert j["n_flagged"] == 1 and j["rows"][0]["path"] == \
        "cases.LAT.base.FX"
    assert "LAT" in j["cases"] and "base" in j["quantities"]
    r = cl.post("/api/results/compare", json={"a": "current", "b": d})
    assert r.status_code == 400
    r = cl.post("/api/results/compare", json={"a": d, "b": d,
                                              "rtol": -1})
    assert r.status_code == 400


def test_th_animation_frames_record_nodal_displacements():
    m = _column()
    m.th_cases["THX"].th_output = {"frames": 4}
    m.validate()
    assert m.to_dict()["th_cases"]["THX"]["th_output"] == {"frames": 4}
    _e, _r, d = _run(m)
    th = d["th_cases"]["THX"]
    fr = th["frames"]
    assert fr["steps"] == [50, 100, 150, 200]
    assert fr["t"] == [th["t"][s - 1] for s in fr["steps"]]
    top = [t for t, p in d["nodes"].items() if abs(p[2] - 3.0) < 1e-9
           and abs(p[0]) < 1e-9 and abs(p[1]) < 1e-9]
    assert len(top) == 1
    for s in fr["steps"]:
        u = fr["node_disp"][str(s)][top[0]]
        assert len(u) == 6
        assert u[0] == pytest.approx(th["story_ux"]["Story1"][s - 1],
                                     rel=1e-12, abs=1e-15)
