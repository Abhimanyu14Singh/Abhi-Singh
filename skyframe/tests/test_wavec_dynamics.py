"""Wave C dynamics: eigen shift / cutoff / tolerance, Linear Modal History
and FNA case types, TH initial conditions (staged / hinged NLS), FNA
Takeda / Pivot / BRB / bilinear damper / T/C isolator, RS / steady-state
modal damping (composite, interpolated, overrides), pushover any-DOF
control, ASCE 41 Cm / TL, FEMA 440 displacement modification and shell
creep / shrinkage.

Every expected value is hand-derived in the test (closed-form chain
eigenvalues, Der Kiureghian CQC, Table 7-4, Eqs. 5-1 / 5-2 / 5-4,
sigma J(t, t0) creep and free shrinkage) or a cross-check against an
independent solution (direct integration, the OpenSees material).
"""

import copy
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ops = pytest.importorskip("openseespy.opensees")

from skyframe import quick_building  # noqa: E402
from skyframe.core import pushover_coeff as PC  # noqa: E402
from skyframe.core import timedep as TD  # noqa: E402
from skyframe.core import wavec_dyn as WC  # noqa: E402
from skyframe.core.fna_devices_ext import (HystereticLaw,  # noqa: E402
                                           MenegottoPintoAsym,
                                           TCFrictionPendulum)
from skyframe.core.model import (BuildingModel, FrameSection,  # noqa: E402
                                 LoadPattern, Material, NodalLoad,
                                 NodalMass, PointSupport, ShellSection)
from skyframe.core.nonlinear_static import add_nonlinear_static_case  # noqa
from skyframe.engine.opensees_engine import OpenSeesEngine  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_fna_breadth import _block, _di_vs_fna, _pulse  # noqa: E402

# --------------------------------------------------------------------------- #
# 1. eigen shift / cutoff / tolerance on a shear chain
# --------------------------------------------------------------------------- #
N_CH, K_CH, M_CH = 12, 1000.0, 2.0


def _chain(num_modes=3, **eo):
    """Fixed-free shear chain: k = 12EI/h^3 = 1000 (rotations inactive)."""
    m = BuildingModel(name="chain")
    m.rigid_diaphragms = False
    m.set_stories([1.0] * N_CH)
    m.active_dof = ["UX"]
    m.add_material(Material("M", E=1000.0, nu=0.2, unit_weight=0.0))
    m.add_section(FrameSection("S", "M", A=1.0, I22=1.0 / 12.0,
                               I33=1.0 / 12.0, J=1.0))
    for i in range(N_CH):
        m.add_member("column", "S", (0, 0, float(i)), (0, 0, float(i + 1)),
                     story=f"Story{i + 1}", uid=f"C{i}")
        m.nodal_masses.append(NodalMass((0, 0, float(i + 1)), mx=M_CH))
    m.supports.append(PointSupport((0, 0, 0), (1,) * 6))
    m.num_modes = num_modes
    m.mass_options = dict(m.mass_options, self_mass=False)
    m.eigen_options = eo
    m.validate()
    return m


def _hand_f():
    lam = sorted((2 * K_CH / M_CH) * (1 - math.cos((2 * j - 1) * math.pi
                                                   / (2 * N_CH + 1)))
                 for j in range(1, N_CH + 1))
    return [math.sqrt(v) / (2 * math.pi) for v in lam]


def test_legacy_chain_matches_hand():
    f = _hand_f()
    r = OpenSeesEngine(_chain()).run_modal()
    assert r.frequencies == pytest.approx(f[:3], rel=1e-9)


def test_shift_returns_modes_nearest_the_shift():
    f = _hand_f()
    fs = 0.5 * (f[6] + f[7]) + 0.01            # nearest: 7, 8, then 6 / 9
    d = sorted(range(N_CH), key=lambda i: abs(f[i] - fs))[:3]
    r = OpenSeesEngine(_chain(shift=fs)).run_modal()
    assert r.frequencies == pytest.approx(sorted(f[i] for i in d), rel=1e-8)
    assert r.eigen["found"] == 3 and r.eigen["converged"]
    assert r.eigen["iterations"] > 1           # a genuine subspace iteration
    out = r.to_dict()
    assert out["eigen"]["shift"] == fs and len(out["periods"]) == 3


def test_cutoff_truncates_and_tolerance_reported():
    f = _hand_f()
    fs = f[5]
    cut = 0.55 * min(f[5] - f[4], f[6] - f[5])  # only mode 6 inside
    r = OpenSeesEngine(_chain(shift=fs, cutoff=cut)).run_modal()
    assert r.frequencies == pytest.approx([f[5]], rel=1e-8)
    r2 = OpenSeesEngine(_chain(shift=fs, cutoff=1.2 * (f[6] - f[5]),
                               tolerance=1e-6)).run_modal()
    assert len(r2.frequencies) >= 2 and r2.eigen["tolerance"] == 1e-6


def test_named_modal_case_eigen_params_and_validation():
    m = _chain()
    m.modal_cases = {"MSH": {"type": "eigen", "num_modes": 2,
                             "shift": _hand_f()[4]}}
    m.validate()
    d = json.loads(json.dumps(m.to_dict()))
    assert d["modal_cases"]["MSH"]["shift"] == _hand_f()[4]
    assert d["eigen_options"] == {}  if "eigen_options" in d else True
    res = OpenSeesEngine(m).run().to_dict()
    assert len(res["modal_cases"]["MSH"]["periods"]) == 2
    assert res["modal_cases"]["MSH"]["eigen"]["found"] == 2
    bad = copy.deepcopy(m)
    bad.eigen_options = {"cutoff": -1.0}
    with pytest.raises(ValueError, match="cutoff"):
        bad.validate()
    bad.eigen_options = {"bogus": 1.0}
    with pytest.raises(ValueError, match="unknown eigen"):
        bad.validate()


# --------------------------------------------------------------------------- #
# 2. Linear Modal History / FNA case types
# --------------------------------------------------------------------------- #
def _bldg():
    m = quick_building(stories=3, bays_x=2, bays_y=1)
    acc = [0.0] + [2.0 * math.sin(2 * math.pi * 1.5 * k * 0.01)
                   for k in range(1, 200)]
    m.add_th_case("DI", "X", accel=acc, dt=0.01, damping=0.03)
    m.add_th_case("MH", "X", accel=acc, dt=0.01, damping=0.03)
    m.th_cases["MH"].th_type = "modal"
    m.add_th_case("FN", "X", accel=acc, dt=0.01, damping=0.03)
    m.th_cases["FN"].th_type = "fna"
    m.num_modes = 9               # every massed DOF (3 stories x 3)
    m.validate()
    return m


def test_modal_history_equals_direct_integration_and_case_kinds():
    m = _bldg()
    kinds = m.case_kinds()
    assert kinds["MH"] == "modal_history" and kinds["FN"] == "fna"
    assert kinds["DI"] == "time_history"
    r = OpenSeesEngine(m).run().to_dict()
    for n in ("DI", "MH", "FN"):
        assert r["case_status"][n] == "finished"
    di = np.array(r["th_cases"]["DI"]["story_ux"]["Story3"])
    mh = np.array(r["th_cases"]["MH"]["story_ux"]["Story3"])
    pk = np.max(np.abs(di))
    # the last two samples differ by the legacy FNA end-of-record
    # convention (ground acceleration after the record = 0)
    assert np.max(np.abs(di - mh)[:-2]) <= 1e-6 * pk     # measured 4e-16
    assert r["th_cases"]["MH"]["th_type"] == "modal"
    # round trip + Set Load Cases to Run
    d = json.loads(json.dumps(m.to_dict()))
    assert d["th_cases"]["MH"]["th_type"] == "modal"
    assert "th_type" not in d["th_cases"]["DI"]
    m2 = BuildingModel.from_dict(d)
    assert m2.th_cases["FN"].th_type == "fna"
    m2.cases_not_run = ["FN"]
    r2 = OpenSeesEngine(m2).run().to_dict()
    assert r2["case_status"]["FN"] == "not_run"


def test_th_type_validation():
    m = _bldg()
    m.th_cases["MH"].nonlinear = True
    with pytest.raises(ValueError, match="direct integration"):
        m.validate()
    m.th_cases["MH"].nonlinear = False
    m.th_cases["MH"].th_type = "bogus"
    with pytest.raises(ValueError, match="th_type"):
        m.validate()


# --------------------------------------------------------------------------- #
# 3. direct-integration initial conditions
# --------------------------------------------------------------------------- #
def _init_model():
    m = quick_building(stories=3, bays_x=2, bays_y=1)
    m.add_staged_case("STG", pattern="DEAD")
    add_nonlinear_static_case(m, "NLH", [{"pattern": "DEAD", "scale": 1.0}],
                              steps=4, default_My=250.0)
    m.add_th_case("T0", "X", accel=[0.0] * 20, dt=0.02, nonlinear=True,
                  default_My=300.0)
    m.th_cases["T0"].initial_case = "STG"
    acc = [0.0] + [1.0 * math.sin(2 * math.pi * 1.5 * k * 0.02)
                   for k in range(1, 50)]
    m.add_th_case("T1", "X", accel=acc, dt=0.02, nonlinear=True)
    m.th_cases["T1"].initial_case = "NLH"
    m.validate()
    return m


def test_nl_th_starts_from_staged_displacements():
    m = _init_model()
    eng = OpenSeesEngine(m)
    r = eng.run_time_history("T0")
    ini = r.extra["initial_state"]
    assert ini["kind"] == "staged" and ini["case"] == "STG"
    st = eng.run_staged("STG")
    umax = max(abs(v[2]) for v in st.case.node_disp.values())
    assert umax > 1e-4
    assert ini["max_disp_mismatch"] <= 1e-9 * umax   # domain AT u_s
    # zero excitation: nothing moves past the staged state
    assert max(abs(v) for s in r.story_ux.values() for v in s) < 1e-12
    assert r.nonlinear


def test_nl_th_continues_from_hinged_nonlinear_static_case():
    m = _init_model()
    r = OpenSeesEngine(m).run_time_history("T1")
    ini = r.extra["initial_state"]
    assert ini == {"case": "NLH", "kind": "nonlinear_static",
                   "hinges_from": "initial_case"}
    assert r.hinge_rotations and r.peaks["base"]["FX"] > 0.0


def test_initial_case_validation_accepts_staged():
    m = _init_model()
    m.th_cases["T0"].initial_case = "NOPE"
    with pytest.raises(ValueError, match="nonlinear static or staged"):
        m.validate()


# --------------------------------------------------------------------------- #
# 4. FNA link support
# --------------------------------------------------------------------------- #
def _ops_path(args, eps):
    ops.wipe()
    ops.model("basic", "-ndm", 1, "-ndf", 1)
    ops.uniaxialMaterial(*args)
    ops.testUniaxialMaterial(1)
    out = []
    for e in eps:
        ops.setStrain(float(e))
        out.append(ops.getStress())
    return np.array(out)


def _cyc(n=300, amp=0.05):
    t = np.linspace(0, 1, n)
    rng = np.random.default_rng(1)
    return (amp * np.sin(2 * np.pi * 3 * t) * (0.3 + t)
            + 0.01 * rng.standard_normal(n).cumsum() / 5)


@pytest.mark.parametrize("prm, px, py, beta", [
    ({"points": [[0.01, 100.0], [0.05, 130.0]]}, 1.0, 1.0, 0.0),
    ({"points": [[0.01, 100.0], [0.03, 120.0], [0.08, 125.0]]}, 1, 1, 0),
    ({"points": [[0.01, 100.0], [0.05, 130.0]],
      "points_neg": [[-0.008, -90.0], [-0.04, -110.0]]}, 0.5, 0.25, 0.0),
    ({"points": [[0.01, 100.0], [0.05, 130.0]]}, 0.4, 0.3, 0.3)])
def test_hysteretic_law_equals_opensees_material(prm, px, py, beta):
    from skyframe.engine.hysteresis import _hysteretic_args
    eps = _cyc()
    ref = _ops_path(["Hysteretic", 1, *_hysteretic_args(prm), px, py, 0.0,
                     0.0, beta], eps)
    law = HystereticLaw(prm["points"], prm.get("points_neg"), px, py, beta)
    got = np.array([law.commit(e) for e in eps])
    assert np.max(np.abs(got - ref)) <= 1e-9 * np.max(np.abs(ref))


def test_brb_law_equals_steel4_on_full_cycles():
    from skyframe.core import links_ext as lx
    prm = {"k": 1e5, "Fy": 500.0, "d_bm": 0.05, "beta": 1.15, "omega": 1.4}
    bt, bc = lx.brb_hardening(prm)
    eps = np.concatenate([np.linspace(0, 0.02, 50),
                          np.linspace(0.02, -0.02, 100),
                          np.linspace(-0.02, 0.03, 120),
                          np.linspace(0.03, -0.01, 80)])
    ref = _ops_path(["Steel4", 1, 500.0, 1e5, "-asym", "-kin", bt, 20.0,
                     0.925, 0.15, bc, 20.0, 0.925, 0.15], eps)
    mp = MenegottoPintoAsym(1e5, 500.0, bt, bc, 20.0)
    got = []
    for e in eps:
        mp.commit([e], [0.0], 0.01)
        got.append(mp.c["F"])
    assert np.max(np.abs(np.array(got) - ref)) <= 1e-9 * 805.0


def test_tc_isolator_device_uplift_and_hold_down():
    dev = TCFrictionPendulum(2.0, 0.05, 1e5, 100.0, 1e4, kt=500.0,
                             gap_t=0.01)
    F = dev.trial([0.001, 0.0, -0.002], None, 0.01)       # compression
    assert F[2] == pytest.approx(1e4 * -0.002)
    up = 100.0 / 1e4                                        # lift-off dz
    F = dev.trial([0.05, 0.02, up + 0.005], None, 0.01)    # uplift
    assert F[0] == 0.0 and F[1] == 0.0
    assert F[2] == pytest.approx(100.0)                     # total 0
    F = dev.trial([0.0, 0.0, up + 0.03], None, 0.01)       # hold-down
    assert F[2] == pytest.approx(100.0 + 500.0 * 0.02)


@pytest.mark.parametrize("lt, prm", [
    ("multilinear_takeda", {"points": [[0.005, 20.0], [0.05, 40.0],
                                       [0.3, 70.0]]}),
    ("multilinear_pivot", {"points": [[0.005, 20.0], [0.05, 40.0],
                                      [0.3, 70.0]], "pinch_x": 0.5,
                           "pinch_y": 0.3})])
def test_fna_takeda_pivot_vs_direct_integration(lt, prm):
    dt = 0.005
    acc = ([3.0 * math.sin(2 * math.pi * k * dt / 0.5) for k in range(301)]
           + [0.0] * 300)
    mdl = _block(lt, prm)
    mdl.add_th_case("TH", "X", acc, dt, damping=1e-9)
    u_th, u_f, rf = _di_vs_fna(mdl)
    pk = np.max(np.abs(u_th))
    assert pk > 0.01                                    # yields, cycles
    assert np.max(np.abs(u_th - u_f)) <= 0.02 * pk      # measured < 1e-5
    assert rf.extra["fna"]["devices"] == {"HystereticDevice": 4}


def _brace_frame(lt, prm):
    mdl = BuildingModel(name="br")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 1
    mdl.add_material(Material("S", E=2.0e8, nu=0.3))
    mdl.add_section(FrameSection.rectangular("C", "S", 0.3, 0.3))
    mdl.set_stories([3.0])
    mdl.add_member("column", "C", (0, 0, 0), (0, 0, 3.0), story="Story1",
                   uid="C1")
    mdl.nodal_masses.append(NodalMass((0, 0, 3.0), mx=10.0))
    T1 = OpenSeesEngine(mdl).run_modal(1).periods[0]
    dt = T1 / 100.0
    acc = [20.0 * math.sin(math.pi * k / 30) for k in range(31)] + [0.0] * 600
    mdl.add_th_case("A", "X", acc, dt, damping=0.02)
    mdl.add_link((0, 0, 3.0), (3.0, 0, 0.0), link_type=lt, params=prm,
                 uid="BR")
    mdl.validate()
    return mdl


@pytest.mark.parametrize("lt, prm, dev", [
    ("brb", {"k": 2.0e4, "Fy": 60.0, "d_bm": 0.02, "beta": 1.1,
             "omega": 1.3}, "MenegottoPintoAsym"),
    ("damper_bilinear", {"cd": 60.0, "Fr": 20.0, "ratio": 0.1},
     "BilinearDamper")])
def test_fna_brb_and_bilinear_damper_vs_direct_integration(lt, prm, dev):
    u_th, u_f, rf = _di_vs_fna(_brace_frame(lt, prm), "A")
    pk = np.max(np.abs(u_th))
    assert np.max(np.abs(u_th - u_f)) <= 0.03 * pk   # measured 0.35 % / 6e-5
    assert rf.extra["fna"]["devices"] == {dev: 1}


def test_fna_tc_isolator_vs_direct_integration():
    acc, dt = _pulse()
    mdl = _block("tc_fp_isolator", {"R": 2.0, "mu": 0.06, "k_init": 1e5})
    mdl.add_th_case("TH", "X", acc, dt, damping=1e-9, gravity={"G": 1.0})
    u_th, u_f, rf = _di_vs_fna(mdl)
    pk = np.max(np.abs(u_th))
    assert pk > 0.1
    assert abs(np.max(np.abs(u_f)) - pk) <= 0.03 * pk  # measured 0.8 %
    assert rf.extra["fna"]["devices"] == {"TCFrictionPendulum": 4}


# --------------------------------------------------------------------------- #
# 5. RS / steady-state modal damping
# --------------------------------------------------------------------------- #
SPEC = [[0.0, 0.4], [0.2, 1.0], [0.6, 1.0], [2.0, 0.3], [4.0, 0.15]]


def _rs_model(md, damping=0.05):
    m = quick_building(stories=3, bays_x=2, bays_y=1)
    m.add_rs_case("RS", "X", spectrum=SPEC, damping=damping)
    m.rs_cases["RS"].modal_damping = md
    m.validate()
    return m


def _hand_rs_base(m, zetas, sa_fac=None):
    """Independent RS: per-mode base shear from modal statics, Der
    Kiureghian variable-damping CQC."""
    m0 = copy.deepcopy(m)
    m0.rs_cases["RS"].modal_damping = None
    eng = OpenSeesEngine(m0)
    modal = eng.run_modal()
    from skyframe.engine.opensees_engine import G_ACCEL, _interp_spectrum
    vals, om = [], []
    asm = eng._asm
    for i, T in enumerate(modal.periods, start=1):
        g = modal.participation[i - 1]["gamma_x"]
        sa = _interp_spectrum(SPEC, T) * G_ACCEL * (sa_fac[i - 1]
                                                    if sa_fac else 1.0)
        V = sum(g * sa * mm * modal.shapes[i][t][d - 1]
                for (t, d), mm in asm.mass_map.items() if d == 1)
        vals.append(V)
        om.append(2 * math.pi / T)
    n = len(om)
    rho = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            zi, zj = zetas[i], zetas[j]
            r = om[j] / om[i]
            rho[i, j] = rho[j, i] = (
                8 * math.sqrt(zi * zj) * (zi + r * zj) * r ** 1.5
                / ((1 - r * r) ** 2 + 4 * zi * zj * r * (1 + r * r)
                   + 4 * (zi * zi + zj * zj) * r * r))
    v = np.array(vals)
    return math.sqrt(v @ rho @ v), modal.periods


def test_rs_interpolated_damping_per_mode_with_override():
    md = {"type": "interpolated_period",
          "points": [[0.05, 0.02], [0.5, 0.06]], "overrides": {"2": 0.10}}
    m = _rs_model(md)
    r = OpenSeesEngine(m).run_response_spectrum("RS")
    zinfo = r.rs_info["modal_damping"]
    _v, periods = _hand_rs_base(m, [0.05] * 12)
    hand_z = [float(np.interp(T, [0.05, 0.5], [0.02, 0.06]))
              for T in periods]
    hand_z[1] = 0.10
    assert zinfo["zetas"] == pytest.approx(hand_z, rel=1e-12)
    V, _ = _hand_rs_base(m, hand_z)
    assert abs(r.base["FX"]) == pytest.approx(V, rel=1e-6)
    # differs from the constant-damping case
    r0 = OpenSeesEngine(_rs_model(None)).run_response_spectrum("RS")
    assert abs(abs(r0.base["FX"]) - V) > 1e-6 * V


def test_rs_ec8_spectral_correction():
    md = {"type": "constant", "overrides": {"1": 0.10},
          "sa_correction": "ec8"}
    m = _rs_model(md)
    r = OpenSeesEngine(m).run_response_spectrum("RS")
    z = r.rs_info["modal_damping"]["zetas"]
    fac = [max(math.sqrt(10 / (5 + 100 * zz)), 0.55) for zz in z]
    assert fac[0] == pytest.approx(math.sqrt(10 / 15))
    V, _ = _hand_rs_base(m, z, fac)
    assert abs(r.base["FX"]) == pytest.approx(V, rel=1e-6)


def _stacked():
    m = BuildingModel(name="stack")
    m.rigid_diaphragms = False
    m.add_material(Material("A", E=30e6, nu=0.2, unit_weight=0.0))
    m.add_material(Material("B", E=3e6, nu=0.2, unit_weight=0.0))
    m.add_section(FrameSection.rectangular("SA", "A", 0.4, 0.4))
    m.add_section(FrameSection.rectangular("SB", "B", 0.4, 0.4))
    m.set_stories([3.0, 3.0])
    m.add_member("column", "SA", (0, 0, 0), (0, 0, 3.0), story="Story1",
                 uid="C1")
    m.add_member("column", "SB", (0, 0, 3.0), (0, 0, 6.0), story="Story2",
                 uid="C2")
    m.supports.append(PointSupport((0, 0, 0), (1,) * 6))
    m.nodal_masses.append(NodalMass((0, 0, 3.0), mx=5.0, my=5.0))
    m.nodal_masses.append(NodalMass((0, 0, 6.0), mx=5.0, my=5.0))
    m.material_damping = {"A": 0.05, "B": 0.02}
    m.num_modes = 2
    return m


def test_composite_rs_damping_by_strain_energy():
    m = _stacked()
    m.add_rs_case("RS", "X", spectrum=SPEC, damping=0.05)
    m.rs_cases["RS"].modal_damping = {"type": "composite"}
    m.validate()
    r = OpenSeesEngine(m).run_response_spectrum("RS")
    info = r.rs_info["modal_damping"]
    # hand: zeta_i = sum zeta_g E_g / sum E_g with the reported energies
    for mode, z in zip(info["composite"]["modes"], info["zetas"]):
        fr = mode["fractions"]
        hand = 0.05 * fr.get("A", 0.0) + 0.02 * fr.get("B", 0.0) \
            + 0.05 * fr.get("other", 0.0)
        assert z == pytest.approx(hand, rel=1e-9)
        assert mode["energy_check"] == pytest.approx(1.0, rel=1e-6)
    assert 0.02 < info["zetas"][0] < 0.05


def test_steady_state_composite_and_interpolated_damping():
    from skyframe.core.frequency_cases import (FrequencyLoad,
                                               add_frequency_function,
                                               add_steady_state_case)
    from skyframe.engine import frequency as fq
    m = _stacked()
    m.pattern("PX", "other").nodal_loads.append(NodalLoad((0, 0, 6.0),
                                                          fx=1.0))
    add_frequency_function(m, "ONE", "steady_state", [[0.0, 1.0],
                                                      [50.0, 1.0]])
    add_steady_state_case(m, "SS", loads=[FrequencyLoad("PX", 1.0, "ONE")],
                          freq_start_hz=0.5, freq_end_hz=8.0, n_freq=9,
                          modal_refine=False, damping=0.05, method="direct")
    m.steady_state_cases["SS"].modal_damping = {"type": "composite"}
    m.validate()
    eng = OpenSeesEngine(m)
    res = fq.run_steady_state(eng, "SS")
    z = res["modal_damping"]["zetas"]
    assert len(z) >= 2 and all(0.02 <= v <= 0.05 for v in z)
    # hand modal receptance at the top node with the per-mode ratios
    modal = eng.run_modal(max(eng._build().free_massed_dofs(), 1))
    t = eng._find_node(eng._asm, (0, 0, 6.0))
    amp = np.array(res["node_disp"][str(t)]["amp"])[:, 0]
    asm = eng._asm
    for k, f in enumerate(res["frequencies_hz"]):
        w = 2 * math.pi * f
        u = 0j
        for i, T in enumerate(modal.periods, start=1):
            wi = 2 * math.pi / T
            sh = modal.shapes[i]
            den = sum(mm * sh[tt][d - 1] ** 2
                      for (tt, d), mm in asm.mass_map.items())
            u += sh[t][0] * sh[t][0] / den / (wi * wi - w * w
                                              + 2j * z[i - 1] * wi * w)
        assert amp[k] == pytest.approx(abs(u), rel=1e-6)
    # round trip of the spec
    d = json.loads(json.dumps(m.to_dict()))
    assert d["steady_state_cases"]["SS"]["modal_damping"] == {
        "type": "composite"}
    assert BuildingModel.from_dict(d).steady_state_cases[
        "SS"].modal_damping == {"type": "composite"}


def test_mass_stiffness_coefficients_and_validation():
    md = {"type": "mass_stiffness", "periods": [1.0, 0.2],
          "ratios": [0.05, 0.05]}
    z = WC.mode_zetas(md, 0.05, [1.0, 0.2, 0.5])
    assert z[0] == pytest.approx(0.05) and z[1] == pytest.approx(0.05)
    assert z[2] < 0.05
    for bad, msg in [({"type": "nope"}, "type"),
                     ({"type": "interpolated_period"}, "points"),
                     ({"type": "constant", "overrides": {"0": 0.1}},
                      "mode numbers"),
                     ({"type": "constant", "sa_correction": "x"},
                      "sa_correction")]:
        with pytest.raises(ValueError, match=msg):
            WC.validate_modal_damping(bad, "rs case 'X'")


# --------------------------------------------------------------------------- #
# 6. pushover: RZ control, Cm / TL, FEMA 440 DM
# --------------------------------------------------------------------------- #
def _asym():
    m = quick_building(stories=1, bays_x=2, bays_y=1)
    m.add_section(FrameSection.rectangular("BIG", "CONC", 0.9, 0.9))
    for mem in m.members:
        if mem.kind == "column" and mem.pi[1] == 0.0:
            mem.section = "BIG"
    m.validate()
    return m


def test_pushover_rz_control_linear_hand():
    m = _asym()
    eng = OpenSeesEngine(m)
    asm = eng._build()
    mt = asm.masters["Story1"]
    # unit X force at the roof master -> rotation per kN (linear static)
    from skyframe.engine import modal_condensed as mcd
    mcd._solve_loads(eng, [((mt, 1), 1.0)])
    rz_unit = ops.nodeDisp(mt, 6)
    assert abs(rz_unit) > 1e-9                      # eccentric
    th = 50.0 * rz_unit
    m.add_pushover_case("PRZ", "X", steps=10, control_dof="RZ",
                        target_disp=th)
    m.validate()
    po = OpenSeesEngine(m).run_pushover("PRZ")
    assert po.control["dof"] == "RZ"
    assert po.roof_disp[-1] == pytest.approx(th, rel=1e-9)
    assert po.base_shear[-1] == pytest.approx(50.0, rel=1e-6)
    m.pushover_cases["PRZ"].target_disp = None
    with pytest.raises(ValueError, match="target_disp"):
        m.validate()


def _bilinear_curve(Ke=1000.0, Vy=100.0, a=0.05, du=0.5, n=50):
    dy = Vy / Ke
    d = list(np.linspace(dy / 5, du, n))
    v = [Ke * x if x <= dy else Vy + a * Ke * (x - dy) for x in d]
    return d, v


def test_asce41_cm_table_and_tl_branch_hand():
    from skyframe.design.performance import target_displacement
    d, v = _bilinear_curve()
    base = target_displacement(d, v, Ti=0.5, W=1000.0, SDS=1.0, SD1=0.6,
                               num_stories=5)
    assert base["Cm"] == 1.0 and "TL" not in base
    out = target_displacement(d, v, Ti=0.5, W=1000.0, SDS=1.0, SD1=0.6,
                              num_stories=5, system="concrete_shear_wall")
    assert out["Cm"] == 0.8
    assert out["mu"] == pytest.approx(base["mu"] * 0.8, rel=1e-12)
    assert PC.cm_factor("steel_moment_frame", 2, 0.5) == 1.0   # 1-2 stories
    assert PC.cm_factor("steel_moment_frame", 3, 1.2) == 1.0   # Te > 1 s
    assert PC.cm_factor("steel_ebf", 6, 0.8) == 0.9
    # TL: Te = 2.0 s > TL = 1.5 s -> Sa = SD1 TL / T^2
    out = target_displacement(d, v, Ti=2.0, W=1000.0, SDS=1.0, SD1=0.6,
                              TL=1.5)
    assert out["Sa"] == pytest.approx(0.6 * 1.5 / out["Te"] ** 2, rel=1e-12)
    assert out["delta_t"] == pytest.approx(
        out["C0"] * out["C1"] * out["C2"] * out["Sa"]
        * (out["Te"] / (2 * math.pi)) ** 2 * 9.81, rel=1e-12)


def test_fema440_dm_c1_c2_hand_values():
    # Te = 0.5 s, site D (a = 60), R = 3:
    #   C1 = 1 + 2/(60 * 0.25) = 1.13333, C2 = 1 + (2/0.5)^2/800 = 1.02
    C1, C2 = PC.c1_c2(3.0, 0.5, "D")
    assert C1 == pytest.approx(1.0 + 2.0 / 15.0, rel=1e-12)
    assert C2 == pytest.approx(1.02, rel=1e-12)
    C1, C2 = PC.c1_c2(3.0, 0.1, "B")          # Te < 0.2: value at 0.2 s
    assert C1 == pytest.approx(1.0 + 2.0 / (130 * 0.04), rel=1e-12)
    assert C2 == pytest.approx(1.0 + (2.0 / 0.1) ** 2 / 800.0)
    assert PC.c1_c2(3.0, 1.2, "D") == (1.0, 1.0)

    class Sp:
        def sa(self, T):
            return 1.0
    d, v = _bilinear_curve()
    # degrading tail -> alpha_2 and R_max (Eq. 5-4)
    d2 = d + [0.6, 0.7]
    v2 = v + [v[-1] - 20.0, v[-1] - 40.0]
    out = PC.fema440_dm(d2, v2, Ti=0.5, W=1000.0, spec=Sp(), num_stories=1)
    R = 1.0 / (out["Vy"] / 1000.0)
    assert out["R"] == pytest.approx(R)
    a2 = ((v2[-1] - max(v2)) / (0.7 - d2[int(np.argmax(v2))])) / out["Ke"]
    assert out["alpha_2"] == pytest.approx(a2)
    ae = 0.8 * a2
    h = 1 + 0.15 * math.log(out["Te"])
    assert out["R_max"] == pytest.approx(out["du"] / out["dy"]
                                         + abs(ae) ** (-h) / 4, rel=1e-12)
    assert out["dynamic_instability"] == (R > out["R_max"])


def test_pushover_methods_fema440_dm_option_and_legacy_defaults():
    from skyframe.core import pushover_methods as pm
    assert pm.PERF_DEFAULTS["methods"] == ["asce41", "atc40", "fema440",
                                           "n2"]
    d, v = _bilinear_curve()
    modal = {"T": 0.5, "gamma": 1.3, "phi_ctrl": 1.0, "pf_phi": 1.3,
             "alpha": 0.8, "mass": 100.0, "W": 981.0, "mode": 1}
    out = pm.evaluate(d, v, modal=modal, options={
        "methods": ["asce41", "fema440_dm"], "cm_system": "steel_cbf"},
        num_stories=4)
    a, f = out["methods"]["asce41"], out["methods"]["fema440_dm"]
    assert a["Cm"] == 0.9 and f["Cm"] == 0.9
    assert f["delta_t"] == pytest.approx(a["delta_t"], rel=1e-12)
    with pytest.raises(ValueError, match="cm_system"):
        pm.normalize_options({"cm_system": "wood"})


# --------------------------------------------------------------------------- #
# 7. shell creep / shrinkage (step-by-step staged)
# --------------------------------------------------------------------------- #
E_C = 30e6
L_S, B_S, T_S = 4.0, 1.0, 0.2


def _slab(props, load=True, shell_creep=True, dur=500.0, steps=3,
          clamp=True):
    m = BuildingModel(name="slab")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E=E_C, nu=0.2, fc=30000.0,
                            unit_weight=0.0))
    m.set_stories([1.0])
    m.add_shell_section(ShellSection("SL", "C", T_S))
    m.add_shell("slab", "shell", "SL", [(0, 0, 1.0), (L_S, 0, 1.0),
                                        (L_S, B_S, 1.0), (0, B_S, 1.0)],
                mesh_size=0.25, story="Story1", uid="S1")
    if clamp:
        for k in range(5):                   # clamped edge x = 0
            m.supports.append(PointSupport((0, 0.25 * k, 1.0), (1,) * 6))
    else:                                    # statically determinate
        m.supports.append(PointSupport((0, 0, 1.0), (1, 1, 1, 1, 1, 1)))
        m.supports.append(PointSupport((L_S, 0, 1.0), (0, 1, 1, 0, 0, 0)))
    pat = LoadPattern("DEAD")
    if load:
        for y in (0.0, 0.5, 1.0):
            pat.nodal_loads.append(NodalLoad((L_S, y, 1.0), 0.0, 0.0,
                                             -2.0 if y == 0.5 else -1.0))
    m.patterns["DEAD"] = pat
    m.add_case("DEAD", {"DEAD": 1.0})
    m.add_group("G", shells=["S1"],
                points=[list(sp.point) for sp in m.supports])
    m.material_time_dependent = {"C": props}
    td = {"method": "step", "steps_per_stage": steps}
    if shell_creep:
        td["shell_creep"] = True
    ops_ = [{"op": "add", "group": "G", "age_days": 28}]
    if load:
        ops_.append({"op": "load", "group": "G", "pattern": "DEAD"})
    m.add_staged_case("ST", stages=[{"name": "S1", "duration_days": dur,
                                     "operations": ops_}],
                      time_dependent=td)
    m.validate()
    return m


def _tip(res, x=L_S, y=0.5):
    eng_nodes = res.case.node_disp
    return eng_nodes


def test_shell_creep_tip_deflection_sigma_j():
    props = {"law": "user", "creep_curve": [[0, 0], [1000, 2.0]],
             "shrinkage": False, "aging": False}
    m = _slab(props)
    eng = OpenSeesEngine(m)
    r = eng.run_staged("ST")
    el = OpenSeesEngine(_slab(props, shell_creep=False)).run_staged("ST")
    uz = min(v[2] for v in r.case.node_disp.values())
    # elastic one-shot (oneshot comparison) and phi(500) = 1.0
    uz_el = min(v[2] for v in r.oneshot.node_disp.values())
    assert uz == pytest.approx(uz_el * 2.0, rel=0.02)
    # shells without the option keep the legacy (ageing-only) result
    assert min(v[2] for v in el.case.node_disp.values()) == pytest.approx(
        uz_el, rel=1e-9)


def test_shell_free_shrinkage_strain():
    props = {"law": "user", "creep_curve": [[0, 0], [1000, 0.0]],
             "shrinkage_curve": [[0, 0], [1000, 4e-4]], "aging": False}
    m = _slab(props, load=False, clamp=False)
    r = OpenSeesEngine(m).run_staged("ST")
    p = TD.normalize_props(props)
    eps = (TD.law_shrinkage(p, 28.0 + 500.0, 30000.0)
           - TD.law_shrinkage(p, 28.0, 30000.0))
    ux = max(abs(v[0]) for v in r.case.node_disp.values())
    assert eps > 0
    assert ux == pytest.approx(eps * L_S, rel=1e-6)   # free x shortening
    uy = max(abs(v[1]) for v in r.case.node_disp.values())
    assert uy == pytest.approx(eps * B_S, rel=1e-6)
    # stress free: no reactions
    assert max(abs(v) for vv in r.case.reactions.values()
               for v in vv) < 1e-6


def test_shell_creep_validation():
    props = {"law": "user", "creep_curve": [[0, 0], [1000, 2.0]],
             "shrinkage": False, "aging": False}
    m = _slab(props)
    m.staged_cases["ST"].time_dependent["shell_creep"] = "yes"
    with pytest.raises(ValueError, match="shell_creep"):
        m.validate()


def test_zetavec_copies_and_full_run_with_rs_damping_and_typed_th():
    z = WC.ZetaVec(0.05, [0.02, 0.03])
    for c in (copy.copy(z), copy.deepcopy(z)):
        assert float(c) == 0.05 and c.per_mode == [0.02, 0.03]
    m = _rs_model({"type": "interpolated_period",
                   "points": [[0.1, 0.03], [1.0, 0.05]]})
    acc = [0.0] + [math.sin(k * 0.1) for k in range(1, 60)]
    m.add_th_case("TM", "X", accel=acc, dt=0.02)
    m.th_cases["TM"].th_type = "modal"
    m.validate()
    r = OpenSeesEngine(m).run().to_dict()
    assert r["case_status"]["RS"] == "finished"
    assert r["case_status"]["TM"] == "finished"
    assert r["rs_cases"]["RS"]["rs"]["modal_damping"]["zetas"]
