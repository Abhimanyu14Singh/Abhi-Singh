"""Analysis performance, solver policy and convergence strategy.

CONTRACT "Analysis performance, solver policy and convergence strategy";
docs/PERFORMANCE.md has the profiles and the before/after table.

Pinned here:

* ``analysis_options`` data model: defaults serialize byte-identically
  (no key), round trip, validation.
* solver policy: the size threshold (BandGeneral below — the exact legacy
  path, bit-identical results — UmfPack above), forced solvers, the
  graceful band-memory fallback (closed-form storage estimate).
* fast path == old path: on models ABOVE the threshold the full results
  of the default run (sparse solver + factor-once reuse + condensed modal
  + scaled unit modal responses for RS + CR memo) equal the forced legacy
  run (``solver="band"``, ``eigen_solver="legacy"``) to 1e-9 relative
  (tolerance comparator, modal sign-invariant); BELOW it the default run
  is bit-identical to the forced legacy run.
* factor-once: bit-identical to a fresh factorization per case.
* condensed modal: a 2-DOF cantilever closed form (flexibility matrix by
  hand) to 1e-10, mass normalization, deterministic sign.
* convergence ladder: legacy mode fails on a softening-hardening spring
  (load through a snap) exactly as before; robust mode converges to the
  closed-form point of the curve and reports the fallback; a step-halving
  rescue (Newton only, iteration cap) on a 40-segment concave curve;
  robust mode is identical to legacy whenever no step needs a fallback.
* model-size warnings (thresholds, opt-out, results block only when
  fired) and a generous performance regression guard.
"""

import copy
import json
import math
import time
from types import SimpleNamespace

import pytest

ops = pytest.importorskip("openseespy.opensees")

from skyframe.core import analysis_options as aopt
from skyframe.core.builder import quick_building
from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection,
                                 Material, NodalLoad, NodalMass,
                                 PointSupport, ShellSection)
from skyframe.core.results_compare import align_modal, compare
from skyframe.engine import convergence as cvg
from skyframe.engine import model_size as msz
from skyframe.engine import solver_policy as slv
from skyframe.engine.opensees_engine import OpenSeesEngine

SPEC = [[0.0, 0.4], [0.5, 1.0], [1.0, 0.6], [2.0, 0.3], [4.0, 0.15]]
LEGACY = {"solver": "band", "eigen_solver": "legacy"}


def _run(mdl, opts=None):
    m = copy.deepcopy(mdl)
    if opts is not None:
        m.analysis_options = copy.deepcopy(opts)
    eng = OpenSeesEngine(m)
    d = eng.run().to_dict()
    return eng, json.loads(json.dumps(d, sort_keys=True, default=str))


def _medium(stories=6):
    """Torsionally ECCENTRIC frame above the sparse threshold (stiff
    column lines A and 1 -> every lateral quantity is physical, no
    round-off ratios), with RS (CQC + SRSS) cases."""
    m = quick_building(stories=stories, bays_x=4, bays_y=3, bay_width_x=6.0,
                       bay_width_y=7.5)
    m.add_section(FrameSection.rectangular("COLB", "CONC", 0.9, 0.9))
    for mem in m.members:
        if mem.kind == "column" and (abs(mem.pi[0]) < 1e-9
                                     or abs(mem.pi[1]) < 1e-9):
            mem.section = "COLB"
    m.add_rs_case("RSX", "X", spectrum=SPEC)
    m.add_rs_case("RSY", "Y", spectrum=SPEC, combo_method="SRSS")
    return m


def _meshed():
    """2-story frame + eccentric meshed walls + slabs (shell elements)."""
    m = quick_building(stories=2, bays_x=2, bays_y=2, bay_width_y=7.0)
    m.add_shell_section(ShellSection("SL", "CONC", 0.2))
    sdl = m.pattern("SDL", "dead")
    for st in m.stories:
        z, zb = st.elevation, st.elevation - st.height
        r = m.add_shell("slab", "shell", "SL",
                        [(0, 0, z), (12, 0, z), (12, 14, z), (0, 14, z)],
                        mesh_size=2.0, story=st.name)
        sdl.area_loads.append(AreaLoad(r.uid, 2.0))
        m.add_shell("wall", "shell", "SL",
                    [(0, 0, zb), (6, 0, zb), (6, 0, z), (0, 0, z)],
                    mesh_size=1.6, story=st.name)
    m.add_case("SDL", {"SDL": 1.0})
    m.add_rs_case("RSX", "X", spectrum=SPEC)
    return m


# =========================================================================
# 1. analysis_options data model
# =========================================================================
def test_options_default_serializes_byte_identically():
    m = quick_building()
    assert "analysis_options" not in m.to_dict()
    m.analysis_options = {"solver": "auto"}            # == defaults
    assert "analysis_options" not in m.to_dict()
    m.analysis_options = {"convergence": {"mode": "robust"},
                          "solver": "umfpack", "size_warnings": False}
    d = json.loads(json.dumps(m.to_dict()))
    assert d["analysis_options"]["convergence"] == {"mode": "robust"}
    m2 = BuildingModel.from_dict(d)
    assert m2.analysis_options == m.analysis_options
    assert m2.to_dict() == m.to_dict()


@pytest.mark.parametrize("bad", [
    "x", {"solver": "sparsesym"}, {"nope": 1}, {"size_warnings": 1},
    {"eigen_solver": "arpack"}, {"convergence": {"mode": "fast"}},
    {"convergence": {"algorithms": ["Newton", "Broyden"]}},
    {"convergence": {"algorithms": []}},
    {"convergence": {"tests": [{"type": "RelativeEnergyIncr"}]}},
    {"convergence": {"tests": [{"type": "EnergyIncr", "tolerance": 0}]}},
    {"convergence": {"tests": [{"type": "EnergyIncr",
                                "max_iterations": 0}]}},
    {"convergence": {"max_halvings": 13}},
    {"convergence": {"max_halvings": True}},
])
def test_options_validation(bad):
    m = quick_building()
    m.analysis_options = bad
    with pytest.raises(ValueError):
        m.validate()


def test_convergence_defaults_reproduce_legacy_shape():
    c = aopt.convergence(quick_building())
    assert c["mode"] == "legacy"
    assert c["algorithms"] == ["Newton", "ModifiedNewton", "KrylovNewton",
                               "NewtonLineSearch", "BFGS"]
    assert c["max_halvings"] == 4


# =========================================================================
# 2. solver policy
# =========================================================================
def test_policy_threshold_and_forced_solvers():
    small = OpenSeesEngine(quick_building())
    small._build()
    assert slv.estimate_equations(small._asm) < slv.SPARSE_EQ_THRESHOLD
    assert small._asm.linear_system == "BandGeneral"
    big = OpenSeesEngine(_medium())
    big._build()
    neq = slv.estimate_equations(big._asm)
    assert neq >= slv.SPARSE_EQ_THRESHOLD
    assert big._asm.linear_system == "UmfPack"
    for name, sysname in (("band", "BandGeneral"), ("umfpack", "UmfPack"),
                          ("sparse_general", "SparseGeneral")):
        m = quick_building()
        m.analysis_options = {"solver": name}
        e = OpenSeesEngine(m)
        e._build()
        assert e._asm.linear_system == sysname


def test_equation_estimate_closed_form():
    """quick_building(): 4 stories x 12 grid nodes, fixed base, rigid
    diaphragms: 48 free nodes x 6 dofs - 48 slaves x 3 (ux, uy, rz go to
    the master) + 4 masters x 3 free dofs (ux, uy, rz)."""
    e = OpenSeesEngine(quick_building())
    e._build()
    assert slv.estimate_equations(e._asm) == 48 * 6 - 48 * 3 + 4 * 3


def test_band_memory_fallback_closed_form(monkeypatch):
    """A forced band solver on a model whose band storage would exceed the
    cap falls back to UmfPack with a warning instead of crashing."""
    asm = SimpleNamespace(
        node_coords={t: (0, 0, 0) for t in range(1, 1001)},
        node_restraints={}, masters={"S1": 1001},
        story_nodes={"S1": list(range(1, 1001))})
    neq = slv.estimate_equations(asm)
    assert neq == 1000 * 6 - 3 * 1000
    assert msz.band_bytes(asm) == pytest.approx(
        neq * (3.0 * min(6 * 1000, neq) + 1.0) * 8.0)
    monkeypatch.setattr(msz, "BAND_MEMORY_CAP", 1.0e3)
    m = SimpleNamespace(analysis_options={"solver": "band"})
    system, note = slv.choose(m, asm)
    assert system == "UmfPack" and "band" in note
    monkeypatch.setattr(msz, "BAND_MEMORY_CAP", 1.0e12)
    assert slv.choose(m, asm) == ("BandGeneral", "")


def test_below_threshold_default_is_bit_identical_to_legacy():
    m = quick_building(stories=3)
    m.add_rs_case("RSX", "X", spectrum=SPEC)
    e1, d1 = _run(m)
    e2, d2 = _run(m, LEGACY)
    assert e1._asm.linear_system == "BandGeneral"
    assert json.dumps(d1, sort_keys=True) == json.dumps(d2, sort_keys=True)


# =========================================================================
# 3. fast path == old path above the threshold
# =========================================================================
@pytest.mark.parametrize("factory", [_medium, _meshed])
def test_fast_path_equals_legacy_path(factory):
    e1, fast = _run(factory())
    e2, slow = _run(factory(), LEGACY)
    assert e1._asm.linear_system == "UmfPack"
    assert e2._asm.linear_system == "BandGeneral"
    ok, bad, worst = compare(slow, fast, rtol=1e-9)
    assert ok, bad
    assert worst < 1e-9
    # the condensed modal path really ran (mass-normalized, signed)
    assert getattr(e1, "_mcd_units", None) is not None
    assert e1._mcd_rz_memo


def test_comparator_catches_real_change_and_modal_sign():
    _, d = _run(quick_building(stories=2))
    d2 = copy.deepcopy(d)
    nd = d2["cases"]["EQX"]["node_disp"]
    tag = max(nd, key=lambda t: abs(nd[t][0]))
    d2["cases"]["EQX"]["node_disp"][tag][0] *= 1.0 + 1e-6
    ok, bad, _ = compare(d, d2)
    assert not ok and "node_disp" in bad[0]
    d3 = copy.deepcopy(d)                    # a flipped mode is the same
    mode = "1"
    d3["modal"]["shapes"][mode] = {
        t: [-v for v in vals] for t, vals in d3["modal"]["shapes"][mode].items()}
    for g in ("gamma_x", "gamma_y"):
        d3["modal"]["participation"][0][g] *= -1.0
    assert compare(d, d3)[0]
    assert not compare(d, d3, modal_invariant=False)[0]
    a, b = align_modal(d, d3)
    assert a["modal"]["shapes"][mode] == b["modal"]["shapes"][mode]
    assert a["modal"]["participation"][0] == b["modal"]["participation"][0]


def test_factor_once_bit_identical_to_fresh_factorization(monkeypatch):
    m = _medium(stories=5)
    m.rs_cases.clear()
    m.analysis_options = {"solver": "umfpack"}
    fast = OpenSeesEngine(copy.deepcopy(m))
    r_fast = {n: fast.run_static(n) for n in m.cases}
    monkeypatch.setenv("SKYFRAME_SLOW_PATH", "1")
    slow = OpenSeesEngine(copy.deepcopy(m))
    r_slow = {n: slow.run_static(n) for n in m.cases}
    for n in m.cases:
        assert r_fast[n].node_disp == r_slow[n].node_disp
        assert r_fast[n].member_forces == r_slow[n].member_forces
        assert r_fast[n].reactions == r_slow[n].reactions


# =========================================================================
# 4. condensed modal analysis
# =========================================================================
def test_condensed_modal_two_dof_cantilever_closed_form():
    """Fixed-base cantilever column, lumped X masses m1 at h and m2 at 2h.
    Euler flexibility by hand: f11 = h^3/3EI, f22 = 8h^3/3EI,
    f12 = 5h^3/6EI.  The eigenvalues mu of M^1/2 F M^1/2 are 1/omega^2."""
    h, E, b = 3.0, 25.0e6, 0.4
    I = b ** 4 / 12.0
    m1, m2 = 20.0, 10.0
    mdl = BuildingModel(name="stick")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("C", E=E, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "C", b, b))
    mdl.set_stories([h, h])
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, h), story="Story1")
    mdl.add_member("column", "COL", (0, 0, h), (0, 0, 2 * h),
                   story="Story2")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, h), mx=m1))
    mdl.nodal_masses.append(NodalMass((0, 0, 2 * h), mx=m2))
    mdl.num_modes = 2
    mdl.mass_source = {}
    mdl.analysis_options = {"solver": "umfpack"}
    eng = OpenSeesEngine(mdl)
    modal = eng.run_modal()
    EI = E * I
    f11, f22, f12 = h ** 3 / (3 * EI), 8 * h ** 3 / (3 * EI), \
        5 * h ** 3 / (6 * EI)
    a, d, c = m1 * f11, m2 * f22, math.sqrt(m1 * m2) * f12
    tr, det = a + d, a * d - c * c
    mus = [(tr + math.sqrt(tr * tr - 4 * det)) / 2,
           (tr - math.sqrt(tr * tr - 4 * det)) / 2]
    T_hand = [2 * math.pi * math.sqrt(mu) for mu in mus]
    assert modal.periods == pytest.approx(T_hand, rel=1e-10)
    assert getattr(eng, "_mcd_rz_memo", None) == {}   # path taken, no masters
    asm = eng._asm
    t1 = next(t for t, p in asm.node_coords.items() if p == (0.0, 0.0, h))
    t2 = next(t for t, p in asm.node_coords.items()
              if p == (0.0, 0.0, 2 * h))
    for k in (1, 2):
        phi = modal.shapes[k]
        # mass normalization phi^T M phi = 1 and the deterministic sign
        assert (m1 * phi[t1][0] ** 2 + m2 * phi[t2][0] ** 2
                == pytest.approx(1.0, rel=1e-12))
        big = max((phi[t1][0] * math.sqrt(m1), phi[t2][0] * math.sqrt(m2)),
                  key=abs)
        assert big > 0.0
    # orthogonality
    p1, p2 = modal.shapes[1], modal.shapes[2]
    assert abs(m1 * p1[t1][0] * p2[t1][0] + m2 * p1[t2][0] * p2[t2][0]) \
        < 1e-12


def test_condensed_modal_matches_eigen_and_can_be_disabled():
    m = _medium(stories=5)
    m.rs_cases.clear()
    fast = OpenSeesEngine(copy.deepcopy(m)).run_modal()
    m.analysis_options = {"eigen_solver": "legacy"}
    leg_eng = OpenSeesEngine(copy.deepcopy(m))
    legacy = leg_eng.run_modal()
    assert getattr(leg_eng, "_mcd_rz_memo", None) is None
    assert fast.periods == pytest.approx(legacy.periods, rel=1e-10)
    for k in fast.shapes:
        tags = list(fast.shapes[k])
        dot = sum(a * b for t in tags
                  for a, b in zip(fast.shapes[k][t], legacy.shapes[k][t]))
        s = 1.0 if dot > 0 else -1.0
        scale = max(abs(v) for t in tags for v in legacy.shapes[k][t])
        err = max(abs(a - s * b) for t in tags
                  for a, b in zip(fast.shapes[k][t], legacy.shapes[k][t]))
        assert err <= 1e-9 * scale


# =========================================================================
# 5. convergence strategy
# =========================================================================
# softening-hardening spring: 0 -> (0.01, 100) -> (0.04, 10) -> (0.05, 210)
# then the last slope k3 = 20000 continues: a load beyond the 100 kN peak
# must snap through to the hardening branch.
SNAP = [[0.01, 100.0], [0.04, 10.0], [0.05, 210.0]]


def _snap_model(H, conv=None):
    mdl = BuildingModel(name="snap")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("M", E=25e6, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "M", 0.5, 0.5))
    mdl.set_stories([4.0])
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, 4.0), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (0, 1, 1, 1, 1, 1)))
    mdl.add_spring_property("SN", {"kind": "multilinear",
                                   "curves": {"U1": SNAP}})
    mdl.add_spring_support((0, 0, 0), property="SN")
    mdl.pattern("H", "other").nodal_loads.append(
        NodalLoad((0, 0, 4.0), fx=H))
    mdl.add_case("H", {"H": 1.0})
    if conv is not None:
        mdl.analysis_options = {"convergence": conv}
    return mdl


def _snap_u(H):
    """Closed form on the final hardening branch (H > 210 extrapolates the
    last segment; 10 < H <= 210 lies on it too)."""
    return 0.04 + (H - 10.0) / 20000.0


@pytest.mark.parametrize("H", [150.0, 250.0, 300.0])
def test_ladder_converges_where_legacy_newton_fails(H):
    eng = OpenSeesEngine(_snap_model(H))
    with pytest.raises(RuntimeError, match="Static analysis failed"):
        eng.run_static("H")
    eng = OpenSeesEngine(_snap_model(H, {"mode": "robust"}))
    r = eng.run_static("H")
    base = next(t for t, c in eng._asm.node_coords.items()
                if c == (0.0, 0.0, 0.0))
    assert r.node_disp[base][0] == pytest.approx(_snap_u(H), rel=1e-9)
    rep = cvg.reports(eng)["H"]
    assert rep["mode"] == "robust" and rep["fallback_steps"] == 1
    assert rep["failed_steps"] == 0
    step = rep["log"][0]
    assert step["algorithm"] in ("ModifiedNewton", "KrylovNewton",
                                 "NewtonLineSearch", "BFGS")
    assert step["halvings"] == 0


def test_robust_report_in_results_and_absent_in_legacy():
    m = _snap_model(300.0, {"mode": "robust"})
    res = OpenSeesEngine(m).run().to_dict()
    assert res["case_status"]["H"] == "finished"
    assert res["convergence"]["H"]["by_algorithm"]
    m = _snap_model(300.0)
    res = OpenSeesEngine(m).run().to_dict()
    assert res["case_status"]["H"] == "failed"
    assert "convergence" not in res


def _concave_problem(iters):
    """1-dof zeroLength on a 40-segment concave MultiLinear curve
    F = 100 (1 - exp(-u)) sampled every 0.1; P = 95 in ONE load step with
    an iteration cap: Newton walks ~one segment per iteration."""
    pts = []
    for i in range(1, 41):
        u = 0.1 * i
        pts += [u, 100.0 * (1.0 - math.exp(-u))]
    ops.wipe()
    ops.model("basic", "-ndm", 1, "-ndf", 1)
    ops.node(1, 0.0)
    ops.node(2, 0.0)
    ops.fix(1, 1)
    ops.uniaxialMaterial("MultiLinear", 1, *pts)
    ops.element("zeroLength", 1, 1, 2, "-mat", 1, "-dir", 1)
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(2, 95.0)
    ops.constraints("Plain")
    ops.numberer("RCM")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1e-10, iters)
    ops.algorithm("Newton")
    ops.integrator("LoadControl", 1.0)
    ops.analysis("Static")
    # exact answer: the segment [2.9, 3.0] holds F = 95
    f0, f1 = 100 * (1 - math.exp(-2.9)), 100 * (1 - math.exp(-3.0))
    return 2.9 + 0.1 * (95.0 - f0) / (f1 - f0)


def test_step_halving_rescues_newton_only():
    model = SimpleNamespace(analysis_options={"convergence": {
        "mode": "legacy"}})
    _concave_problem(4)
    st = cvg.Strategy(model, base_test=("NormDispIncr", 1e-10, 4))
    assert st.static(("LoadControl", 1.0)) != 0          # legacy fails
    model.analysis_options = {"convergence": {
        "mode": "robust", "algorithms": ["Newton"],
        "tests": [{"type": "NormDispIncr"}], "max_halvings": 8}}
    u_exact = _concave_problem(4)
    st = cvg.Strategy(model, base_test=("NormDispIncr", 1e-10, 4))
    assert st.static(("LoadControl", 1.0)) == 0
    assert ops.nodeDisp(2, 1) == pytest.approx(u_exact, rel=1e-9)
    assert ops.getLoadFactor(1) == pytest.approx(1.0, abs=1e-12)
    (entry,) = st.log
    assert entry["algorithm"] == "Newton" and entry["halvings"] >= 1
    rep = st.report()
    assert rep["halved_steps"] == 1 and rep["failed_steps"] == 0
    # halving disabled -> the same Newton-only ladder fails
    model.analysis_options["convergence"]["max_halvings"] = 0
    _concave_problem(4)
    st = cvg.Strategy(model, base_test=("NormDispIncr", 1e-10, 4))
    assert st.static(("LoadControl", 1.0)) != 0
    assert st.log == [{"step": 1, "failed": True}]


def test_robust_equals_legacy_when_no_fallback_needed():
    """The first attempt of every step is the unchanged legacy attempt, so
    a run that never needs a fallback is identical in robust mode."""
    m = quick_building(stories=3)
    m.add_pushover_case("PO", "X", gravity={"DEAD": 1.0},
                        target_drift=0.01, steps=10, default_My=400.0)
    acc = [0.2 * math.sin(2 * math.pi * 1.5 * k * 0.02) for k in range(60)]
    m.add_th_case("TH", "X", accel=acc, dt=0.02, nonlinear=True,
                  gravity={"DEAD": 1.0}, default_My=400.0)
    _, legacy = _run(m)
    eng, robust = _run(m, {"convergence": {"mode": "robust"}})
    conv = robust.pop("convergence")
    assert legacy == robust
    assert conv["PO"]["fallback_steps"] == 0 and conv["PO"]["steps"] > 10
    assert conv["TH"]["fallback_steps"] == 0


# =========================================================================
# 6. model-size warnings
# =========================================================================
def test_size_warning_thresholds_and_optout(monkeypatch):
    rep = {"equations_estimate": 10, "shell_elements": 0,
           "rs_modal_statics": 0, "th_steps": 0,
           "result_values_estimate": 0}
    assert msz.warnings_for(rep) == []
    rep2 = dict(rep, equations_estimate=msz.SLOW_EQUATIONS)
    assert "large model" in msz.warnings_for(rep2)[0]
    rep3 = dict(rep, equations_estimate=msz.VERY_SLOW_EQUATIONS)
    assert "very large model" in msz.warnings_for(rep3)[0]
    rep4 = dict(rep, shell_elements=msz.SLOW_SHELLS,
                rs_modal_statics=msz.SLOW_MODAL_STATICS,
                th_steps=10 ** 9, equations_estimate=1,
                result_values_estimate=msz.SLOW_RESULT_VALUES)
    assert len(msz.warnings_for(rep4)) == 4
    # a normal run carries no block; lowered thresholds fire a WARNING
    # (never a failure) and the block appears; the opt-out silences it
    m = quick_building(stories=2)
    _, d = _run(m)
    assert "model_size" not in d and "warning" not in d
    monkeypatch.setattr(msz, "SLOW_EQUATIONS", 10)
    _, d = _run(m)
    assert d["model_size"]["equations_estimate"] == \
        slv.estimate_equations(OpenSeesEngine(m)._build())
    assert "large model" in d["warning"]
    assert d["case_status"]["DEAD"] == "finished"
    _, d = _run(m, {"size_warnings": False})
    assert "model_size" not in d and "warning" not in d


# =========================================================================
# 7. performance regression guard (generous bound)
# =========================================================================
def test_performance_regression_guard():
    """A 10-story 5x5-bay frame with two RS cases took ~30 s on the legacy
    path (BandGeneral + per-solve rebuilds, docs/PERFORMANCE.md) and ~5 s
    now; the guard fails only on a gross regression: solver policy off,
    rebuilds per solve, or > 60 s wall."""
    m = quick_building(stories=10, bays_x=5, bays_y=5)
    m.add_rs_case("RSX", "X", spectrum=SPEC)
    m.add_rs_case("RSY", "Y", spectrum=SPEC)
    eng = OpenSeesEngine(m)
    builds = {"n": 0}
    orig = eng._build

    def counting(*a, **k):
        builds["n"] += 1
        return orig(*a, **k)

    eng._build = counting
    t0 = time.perf_counter()
    res = eng.run()
    wall = time.perf_counter() - t0
    assert eng._asm.linear_system == "UmfPack"
    assert builds["n"] <= 6
    assert len(res.rs_cases) == 2 and len(res.modal.periods) == 12
    assert wall < 60.0


# =========================================================================
# 8. cross-run mesh cache
# =========================================================================
def test_mesh_cache_hit_is_bit_identical_and_keyed_on_geometry():
    from skyframe.core import mesh_cache
    from skyframe.core.mesh import mesh_model
    mesh_cache.clear()
    m = _meshed()
    fresh = mesh_model(m)
    a = mesh_cache.cached_mesh(m)
    m.add_case("EXTRA", {"DEAD": 2.0})           # case defs: same key
    b = mesh_cache.cached_mesh(m)
    assert mesh_cache.stats == {"hits": 1, "misses": 1}
    assert b is not a and b.model is m
    for mesh in (a, b):
        assert mesh.points == fresh.points and mesh.quads == fresh.quads
        assert mesh.segments == fresh.segments
        assert mesh.region_trib == fresh.region_trib
    m.shells[0].mesh_size = 1.0                  # geometry change: miss
    c = mesh_cache.cached_mesh(m)
    assert mesh_cache.stats["misses"] == 2
    assert len(c.points) != len(fresh.points)
    # a run served from the cache equals the first (cold) run exactly
    mesh_cache.clear()
    _, d1 = _run(_meshed())
    _, d2 = _run(_meshed())
    assert mesh_cache.stats["hits"] >= 1
    assert d1 == d2
    mesh_cache.clear()
