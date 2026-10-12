"""FNA breadth: friction-pendulum, triple-pendulum, multilinear and Wen
links, nonlinear dampers (alpha != 1), gravity initial conditions from a
nonlinear static predecessor, and the Ritz basis.

Each device law is pinned to a closed form (FP theoretical bilinear loop
and area, Fenz-Constantinou TFP regimes I-IV, Masing unloading, Wen
initial tangent / saturation, Maxwell steady state), then every FNA run
is cross-validated against direct integration with the OpenSees element
(ViscousDamper, singleFPBearing, TripleFrictionPendulum, MultiLinear,
elastomericBearingBoucWen) and, for FP, against an independent hand
SDOF Newmark-Raphson integrator.  Viscous damping is ~0 in the
cross-checks: the FNA (modal) and direct-integration (Rayleigh on the
initial stiffness) damping models differ once a device yields.
"""

import math

import numpy as np
import pytest

from skyframe.core import fna as fna_core
from skyframe.core import fna_devices as fd
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 NodalLoad, NodalMass)
from skyframe.core.nonlinear_static import NonlinearStaticCase
from skyframe.engine import fna as fnx
from skyframe.engine.opensees_engine import OpenSeesEngine

ops = pytest.importorskip("openseespy.opensees")

H, B, M_BLK, GRAV = 0.3, 4.0, 80.0, 9.81
W_EACH = M_BLK / 4 * GRAV                  # 196.2 kN per bearing


def _block(ltype, params, num_modes=8, gravity=True):
    """Stiff ring on 4 vertical bearings; 80 t; pattern G = its weight."""
    mdl = BuildingModel(name="iso")
    mdl.rigid_diaphragms = False
    mdl.num_modes = num_modes
    mdl.add_material(Material("STIFF", E=2e11, nu=0.3))
    mdl.add_section(FrameSection.rectangular("BM", "STIFF", 0.5, 0.5))
    mdl.set_stories([H])
    pts = [(0, 0), (B, 0), (B, B), (0, B)]
    for i in range(4):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % 4]
        mdl.add_member("beam", "BM", (x1, y1, H), (x2, y2, H),
                       story="Story1", uid=f"B{i + 1}")
    pat = mdl.pattern("G", "dead")
    for k, (x, y) in enumerate(pts):
        mdl.add_link((x, y, 0.0), (x, y, H), link_type=ltype,
                     params=dict(params), uid=f"L{k + 1}")
        mdl.nodal_masses.append(NodalMass((x, y, H), mx=M_BLK / 4,
                                          my=M_BLK / 4))
        pat.nodal_loads.append(NodalLoad((x, y, H), fz=-W_EACH))
    return mdl


def _pulse(dt=0.005):
    return ([3.0 * math.sin(math.pi * k * dt / 0.5) for k in range(101)]
            + [0.0] * 600), dt


def _di_vs_fna(mdl, case="TH"):
    eng = OpenSeesEngine(mdl)
    u_th = np.array(eng.run_time_history(case).story_ux["Story1"])
    rf = eng.run_fna(case)
    return u_th, np.array(rf.story_ux["Story1"]), rf


# --------------------------------------------------------------------------- #
# nonlinear damper
# --------------------------------------------------------------------------- #
def test_nonlinear_damper_law_alpha1_and_steady_state():
    m1 = fna_core.MaxwellDamper(5000.0, 40.0)
    m2 = fd.NonlinearDamper(5000.0, 40.0, 1.0)
    dt = 0.002
    for k in range(400):
        v = 0.3 * math.sin(0.05 * k)
        f1 = m1.trial(0.0, v, dt)
        f2 = m2.trial([0.0], [v], dt)[0]
        assert f2 == pytest.approx(f1, rel=1e-12, abs=1e-12)
        m1.commit(0.0, v, dt)
        m2.commit([0.0], [v], dt)
    # alpha = 0.5 at constant velocity: F -> cd v^alpha (series spring
    # relaxed); and a reversal gives the mirror value
    d = fd.NonlinearDamper(1.0e5, 50.0, 0.5)
    for _ in range(4000):
        d.commit([0.0], [0.2], 1e-3)
    assert d._F == pytest.approx(50.0 * 0.2 ** 0.5, rel=1e-6)
    for _ in range(4000):
        d.commit([0.0], [-0.2], 1e-3)
    assert d._F == pytest.approx(-50.0 * 0.2 ** 0.5, rel=1e-6)


def test_fna_damper_alpha_half_matches_direct_integration():
    mdl = BuildingModel(name="d05")
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
    acc = [4.0 * math.sin(math.pi * k / 30) for k in range(31)] + [0.0] * 1500
    mdl.add_th_case("A", "X", acc, dt, damping=0.02)
    free = np.max(np.abs(OpenSeesEngine(mdl).run_time_history("A")
                         .story_ux["Story1"]))
    mdl.add_link((0, 0, 3.0), (1.5, 0, 3.0), link_type="damper",
                 params={"cd": 30.0, "alpha": 0.5})
    u_th, u_f, rf = _di_vs_fna(mdl, "A")
    pk = np.max(np.abs(u_th))
    assert pk < 0.85 * free                         # the damper works
    assert np.max(np.abs(u_th - u_f)) <= 0.01 * pk  # measured 2e-4
    assert rf.extra["fna"]["devices"] == {"NonlinearDamper": 1}


# --------------------------------------------------------------------------- #
# friction pendulum
# --------------------------------------------------------------------------- #
def test_fp_theoretical_bilinear_loop():
    """Imposed u = D sin: F(D) = N D/R + mu N; F(0) = -+mu N after slip;
    loop area 4 mu N (D - mu N / k0) (the pendulum term is conservative);
    biaxial circular orbit: friction magnitude mu N."""
    N, R, mu, k0, D = 500.0, 2.0, 0.06, 2.0e4, 0.1
    dev = fd.FrictionPendulum(R, mu, k0, N, vary_N=False)
    n = 20000
    us = [D * math.sin(2 * math.pi * k / n * 2) for k in range(n + 1)]
    Fs = []
    for u in us:
        F = dev.trial([u, 0.0, 0.0], None, 0.0)[0]
        dev.commit([u, 0.0, 0.0], None, 0.0)
        Fs.append(F)
    us = np.array(us[n // 2:])                     # second cycle
    Fs = np.array(Fs[n // 2:])
    i_max = int(np.argmax(us))
    assert Fs[i_max] == pytest.approx(N * D / R + mu * N, rel=1e-9)
    zc = [i for i in range(1, len(us)) if us[i - 1] > 0 >= us[i]]
    assert Fs[zc[0]] == pytest.approx(-mu * N, rel=1e-3)
    area = abs(float(np.sum(0.5 * (Fs[1:] + Fs[:-1]) * np.diff(us))))
    assert area == pytest.approx(4 * mu * N * (D - mu * N / k0), rel=1e-3)
    # biaxial orbit
    dev = fd.FrictionPendulum(R, mu, k0, N, vary_N=False)
    for k in range(4000):
        a = 2 * math.pi * k / 1000
        u = [D * math.cos(a), D * math.sin(a), 0.0]
        dev.commit(u, None, 0.0)
    F = dev.trial(u, None, 0.0)
    fr = np.array(F[:2]) - N / R * np.array(u[:2])
    assert float(np.linalg.norm(fr)) == pytest.approx(mu * N, rel=1e-9)


def _hand_fp_sdof(acc, dt, m, W, R, mu, k0):
    dev = fd.FrictionPendulum(R, mu, k0, W, vary_N=False)
    ag = list(acc) + [0.0]
    u = v = a = 0.0
    out = []
    for k in range(len(acc)):
        p1 = -m * ag[k + 1]
        u1 = u
        for _ in range(100):
            a1 = (u1 - u - dt * v - 0.25 * dt * dt * a) / (0.25 * dt * dt)
            F = dev.trial([u1, 0, 0], None, dt)[0]
            Fp = dev.trial([u1 + 1e-9, 0, 0], None, dt)[0]
            r = m * a1 + F - p1
            if abs(r) < 1e-10:
                break
            u1 -= r / (m / (0.25 * dt * dt) + (Fp - F) / 1e-9)
        an = (u1 - u - dt * v - 0.25 * dt * dt * a) / (0.25 * dt * dt)
        v += 0.5 * dt * (a + an)
        a = an
        dev.commit([u1, 0, 0], None, dt)
        u = u1
        out.append(u)
    return np.array(out)


def test_fna_fp_isolator_vs_direct_and_hand_with_gravity():
    acc, dt = _pulse()
    mdl = _block("fp_isolator", {"R": 2.0, "mu": 0.06, "k_init": 1e5})
    mdl.add_th_case("TH", "X", acc, dt, damping=1e-9, gravity={"G": 1.0})
    u_th, u_f, rf = _di_vs_fna(mdl)
    pk = np.max(np.abs(u_th))
    u_hand = _hand_fp_sdof(acc, dt, M_BLK, 4 * W_EACH, 2.0, 0.06, 4e5)
    assert pk > 0.1                                 # really slides
    assert abs(np.max(np.abs(u_f)) - pk) <= 0.02 * pk   # measured 0.8%
    assert np.max(np.abs(u_f - u_hand)) <= 1e-5 * pk    # measured 2e-8
    ax = rf.extra["fna"]["bearing_axial"]
    assert set(ax) == {"L1", "L2", "L3", "L4"}
    for v in ax.values():
        assert v == pytest.approx(W_EACH, rel=1e-6)
    assert rf.extra["fna"]["initial_case"] == "gravity"


# --------------------------------------------------------------------------- #
# triple friction pendulum (series model)
# --------------------------------------------------------------------------- #
def test_tfp_series_model_regimes_closed_form():
    L1, L2, L3 = 0.3, 2.0, 2.5
    m1, m2, m3 = 0.02, 0.06, 0.10
    d1, d2, d3 = 0.15, 0.4, 0.6        # surface 2 is the first stop
    W = 1000.0
    dev = fd.TripleFP(L1, L2, L3, m1, m2, m3, d1, d2, d3, W, 1e-6,
                      vary_N=False)
    u1s = 2 * L1 * (m2 - m1)                       # end of regime I
    u2s = u1s + (m3 - m2) * (L1 + L2)              # end of regime II
    f2b = m2 + d2 / L2                             # surface 2 at its stop
    u3s = u2s + (f2b - m3) * (L2 + L3)             # end of regime III

    def F(u):
        return dev.trial([u, 0.0, 0.0], None, 0.0)[0]

    def expected(u):
        if u <= u1s:
            return W * m1 + W * u / (2 * L1)
        if u <= u2s:
            return W * m2 + W * (u - u1s) / (L1 + L2)
        if u <= u3s:
            return W * m3 + W * (u - u2s) / (L2 + L3)
        return W * f2b + W * (u - u3s) / (L1 + L3)
    for u in np.linspace(0.002, u3s * 1.2, 40):
        dev.commit([u, 0.0, 0.0], None, 0.0)       # monotonic history
        assert F(u) == pytest.approx(expected(u), rel=2e-3)
    with pytest.raises(ValueError, match="mu1 < mu2"):
        fd.TripleFP(L1, L2, L3, 0.1, 0.06, 0.1, d1, d2, d3, W, 1e-3)


def test_fna_triple_fp_vs_direct_integration():
    acc, dt = _pulse()
    mdl = _block("triple_fp", {"R1": 0.3, "R2": 2.0, "R3": 2.0, "mu1": 0.02,
                               "mu2": 0.06, "mu3": 0.1, "d1": 0.05,
                               "d2": 0.3, "d3": 0.3, "W": W_EACH})
    mdl.add_th_case("TH", "X", acc, dt, damping=1e-9, gravity={"G": 1.0})
    u_th, u_f, _rf = _di_vs_fna(mdl)
    pk = np.max(np.abs(u_th))
    assert abs(np.max(np.abs(u_f)) - pk) <= 0.02 * pk   # measured 0.5%
    assert np.max(np.abs(u_th - u_f)) <= 0.04 * pk      # measured 1.8%


# --------------------------------------------------------------------------- #
# multilinear (Masing) and Wen
# --------------------------------------------------------------------------- #
def test_multilinear_masing_closed_form():
    pts = [[0.01, 10.0], [0.05, 20.0], [0.2, 30.0]]
    dev = fd.MultilinearMasing(pts, rows=1)
    for d, f in pts:
        assert dev.backbone(d) == pytest.approx(f, rel=1e-12)
    um = 0.08
    for u in np.linspace(0, um, 50):
        dev.commit([u], None, 0.0)
    Fm = dev.trial([um], None, 0.0)[0]
    assert Fm == pytest.approx(dev.backbone(um), rel=1e-12)
    for du in (0.004, 0.03, 0.09):              # Masing: 2 x backbone
        assert dev.trial([um - du], None, 0.0)[0] == pytest.approx(
            Fm - 2.0 * dev.backbone(du / 2.0), rel=1e-12)


def test_fna_multilinear_vs_direct_integration():
    acc, dt = _pulse()
    mdl = _block("multilinear", {"points": [[0.005, 20.0], [0.05, 40.0],
                                            [0.3, 70.0]]})
    mdl.add_th_case("TH", "X", acc, dt, damping=1e-9)
    u_th, u_f, _rf = _di_vs_fna(mdl)
    pk = np.max(np.abs(u_th))
    assert pk > 0.05                                 # past the 2nd knee
    assert np.max(np.abs(u_th - u_f)) <= 1e-4 * pk   # measured 1.6e-6


def test_wen_law_tangent_and_saturation():
    k, Fy, r = 1000.0, 50.0, 0.1
    w = fd.WenComponent(r * k, (1 - r) * Fy, Fy / k, eta=2.0)
    d = 1e-6
    assert w.trial([d], None, 0.0)[0] / d == pytest.approx(k, rel=1e-4)
    u = 1.0                                          # 20 x yield
    for x in np.linspace(0, u, 200):
        w.commit([x], None, 0.0)
    assert w.trial([u], None, 0.0)[0] == pytest.approx(
        r * k * u + (1 - r) * Fy, rel=1e-4)
    # unloading starts on the elastic tangent k
    du = 1e-6
    assert (w.trial([u], None, 0.0)[0] - w.trial([u - du], None, 0.0)[0]
            ) / du == pytest.approx(k, rel=1e-3)


def test_fna_bouc_wen_bearing_vs_direct_integration():
    acc, dt = _pulse()
    mdl = _block("rubber_isolator_bouc_wen",
                 {"k_init": 4000.0, "qd": 20.0, "alpha1": 0.1})
    mdl.add_th_case("TH", "X", acc, dt, damping=1e-9)
    u_th, u_f, _rf = _di_vs_fna(mdl)
    pk = np.max(np.abs(u_th))
    assert abs(np.max(np.abs(u_f)) - pk) <= 0.01 * pk   # measured 0.3%
    assert np.max(np.abs(u_th - u_f)) <= 0.02 * pk      # measured 0.6%


# --------------------------------------------------------------------------- #
# gravity initial conditions through a nonlinear static predecessor
# --------------------------------------------------------------------------- #
def test_initial_case_equals_gravity_stage():
    acc, dt = _pulse()
    prm = {"R": 2.0, "mu": 0.06, "k_init": 1e5}
    a = _block("fp_isolator", prm)
    a.add_th_case("TH", "X", acc, dt, damping=1e-9, gravity={"G": 1.0})
    b = _block("fp_isolator", prm)
    b.nonlinear_static_cases["GRAV"] = NonlinearStaticCase(
        "GRAV", loads=[{"pattern": "G", "scale": 1.0}], steps=10)
    b.add_th_case("TH", "X", acc, dt, damping=1e-9)
    b.th_cases["TH"].initial_case = "GRAV"
    b = BuildingModel.from_dict(b.to_dict())        # round trip
    assert b.to_dict()["th_cases"]["TH"]["initial_case"] == "GRAV"
    ra = OpenSeesEngine(a).run_fna("TH")
    rb = OpenSeesEngine(b).run_fna("TH")
    assert rb.extra["fna"]["initial_case"] == "GRAV"
    assert rb.story_ux["Story1"] == pytest.approx(ra.story_ux["Story1"],
                                                  rel=1e-9, abs=1e-12)
    # direct integration continues from the same loads (gravity stage)
    da = OpenSeesEngine(a).run_time_history("TH")
    db = OpenSeesEngine(b).run_time_history("TH")
    assert db.story_ux["Story1"] == pytest.approx(da.story_ux["Story1"],
                                                  rel=1e-12, abs=1e-15)
    # Set Load Cases to Run: the predecessor runs as a dependency
    b.cases_not_run = ["GRAV"]
    assert OpenSeesEngine(b)._run_plan()["status"]["GRAV"] == \
        "run_as_dependency"


# --------------------------------------------------------------------------- #
# Ritz basis + legacy identity
# --------------------------------------------------------------------------- #
def test_fna_on_ritz_basis_matches_eigen_and_direct():
    acc, dt = _pulse()
    prm = {"k1": 2000.0, "k2": 200.0, "Fy": 20.0}
    mdl = _block("isolator", prm)
    mdl.modal_cases = {"RITZ": {
        "type": "ritz", "num_modes": 6,
        "ritz_starts": [{"type": "accel", "dir": "UX"},
                        {"type": "links"}]}}
    mdl.add_th_case("E", "X", acc, dt, damping=1e-9)
    mdl.add_th_case("R", "X", acc, dt, damping=1e-9)
    mdl.th_cases["R"].modal_case = "RITZ"
    eng = OpenSeesEngine(mdl)
    u_th = np.array(eng.run_time_history("E").story_ux["Story1"])
    u_e = np.array(eng.run_fna("E").story_ux["Story1"])
    rr = eng.run_fna("R")
    u_r = np.array(rr.story_ux["Story1"])
    pk = np.max(np.abs(u_th))
    assert rr.extra["fna"]["basis"] == "ritz"
    assert rr.extra["fna"]["modes"] <= 6
    assert np.max(np.abs(u_r - u_th)) <= 0.01 * pk
    assert np.max(np.abs(u_r - u_e)) <= 0.01 * pk


def test_extended_driver_reproduces_legacy_fna():
    acc, dt = _pulse()
    mdl = _block("isolator", {"k1": 2000.0, "k2": 200.0, "Fy": 20.0})
    mdl.add_link((0, 0, H), (-1.0, 0, H), link_type="damper",
                 params={"cd": 15.0})
    mdl.add_th_case("TH", "X", acc, dt, damping=0.03)
    eng = OpenSeesEngine(mdl)
    assert not fnx.wants(eng, mdl.th_cases["TH"])
    leg = eng.run_fna("TH")                         # legacy v0.24 path
    ext = fnx.run(OpenSeesEngine(mdl), "TH")        # forced extended
    assert "fna" not in leg.extra and "fna" in ext.extra
    for s in ("Story1",):
        assert ext.story_ux[s] == pytest.approx(leg.story_ux[s], rel=1e-10,
                                                abs=1e-14)
    assert ext.base_FX == pytest.approx(leg.base_FX, rel=1e-10, abs=1e-10)
