"""Wave-C links: coupled 6x6, multilinear elastic per DOF, multilinear
plastic hysteresis options, HDR, velocity-dependent friction, T/C
friction per direction / in tension, effective (linear) stiffness and
damping, FNA support.

CONTRACT "Wave C links".  Every expected value is closed-form / hand-
derived here (units kN, m, tonne, s):

* coupled link: support reactions = ``K_g d`` with ``K_g = T^T K T`` and
  j's motion carried to node i (``d = u_j + theta_j x (X_i - X_j) - u_i``);
* multilinear elastic: per-DOF curve forces;
* Takeda / pivot / degrading: backbone, unloading zero-crossing
  ``u0 = u_peak - F_peak / k_u`` (``k_u = k0 (u_peak/d1)^-beta``), the
  reload target (opposite peak, else the opposite first point) and the
  pivot pinch point ``(u0 + px (d* - u0), py F_t)``, ``d* = u_t - (1 -
  py) F_t / k0``;
* isotropic: a reversal from F_max yields at -F_max;
* HDR: Grant elastic component ``a1 u + a2 u^3 + a3 u^5``;
* friction: ``mu(v) = mu_fast - (mu_fast - mu) exp(-rate |v|)`` and
  ``F = mu N + N u / R``;
* effective stiffness: ``T = 2 pi sqrt(M / sum k_eff)``.
"""

import json
import math

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine
import openseespy.opensees as ops  # noqa: E402

from skyframe.core import fna_devices_c as fdc  # noqa: E402
from skyframe.core import links_c as lc  # noqa: E402
from skyframe.core.loads_ext import GroundDisplacement  # noqa: E402
from skyframe.core.model import (LINK_TYPES, BuildingModel,  # noqa: E402
                                 FrameSection, LinkMember, LoadCase,
                                 Material, NodalLoad, NodalMass,
                                 PointSupport)
from skyframe.engine import links_c as lcb  # noqa: E402


# =========================================================================
# helpers
# =========================================================================
class _Asm:
    def __init__(self, coords):
        self.node_coords = coords
        self.link_ele = {}


class _Eng:
    _rl_ctx = None


def _harness(ltype, prm, pj=(0.0, 0.0, 0.0), free=(1, 0, 0),
             nonlinear=True):
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0.0, 0.0, 0.0)
    ops.node(2, *pj)
    ops.fix(1, 1, 1, 1, 1, 1, 1)
    ops.fix(2, *[0 if f else 1 for f in free], 1, 1, 1)
    asm = _Asm({1: (0.0, 0.0, 0.0), 2: tuple(pj)})
    lk = LinkMember("L1", (0.0, 0.0, 0.0), tuple(pj), [0.0] * 6, ltype, prm)
    lcb.build(_Eng(), asm, lk, ltype, prm, 1, 2, 0, 0, 0, 100,
              object() if nonlinear else None)
    ops.constraints("Transformation")
    ops.numberer("RCM")
    ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-10, 100)
    ops.algorithm("Newton")
    return asm


def _drive(dof, path, n=100):
    ops.timeSeries("Linear", 2)
    ops.pattern("Plain", 2, 2)
    vec = [0.0] * 6
    vec[dof - 1] = 1.0
    ops.load(2, *vec)
    out, u = [(0.0, 0.0)], 0.0
    for tgt in path:
        ops.integrator("DisplacementControl", 2, dof, (tgt - u) / n)
        ops.analysis("Static")
        for _ in range(n):
            assert ops.analyze(1) == 0
            ops.reactions()
            out.append((ops.nodeDisp(2, dof), -ops.nodeReaction(1, dof)))
        u = tgt
    return out


def _at(hist, u, tol=1e-9):
    for d, f in hist:
        if abs(d - u) <= tol:
            return f
    raise AssertionError(f"no sample at u = {u}")


K6 = np.diag([1e5, 2e4, 3e4, 500.0, 800.0, 900.0])
K6[1, 5] = K6[5, 1] = 2e3          # U2-R3 (shear-rocking)
K6[2, 4] = K6[4, 2] = -1.5e3       # U3-R2
K6[0, 4] = K6[4, 0] = 1e3          # U1-R2
K6[1, 2] = K6[2, 1] = 5e3          # U2-U3
K6[3, 5] = K6[5, 3] = 100.0        # R1-R3


# =========================================================================
# 1. coupled 6x6 linear link
# =========================================================================
@pytest.mark.parametrize("pj", [(0.5, 0.2, 1.0), (0.0, 0.0, 2.0),
                                (3.0, 1.0, 0.0)])
def test_coupled_link_gives_K_d_exactly(pj):
    m = BuildingModel(name="c")
    m.rigid_diaphragms = False
    m.supports.append(PointSupport((0, 0, 0), (1,) * 6))
    m.supports.append(PointSupport(pj, (1,) * 6))
    m.add_link((0, 0, 0), pj, link_type="linear_coupled",
               params={"K": K6.tolist()}, uid="LC")
    d = np.array([1e-3, -2e-3, 0.5e-3, 1e-3, 2e-3, -3e-3])
    m.pattern("G", "other").ground_displacements.append(
        GroundDisplacement(pj, *d))
    m.cases["G"] = LoadCase("G", {"G": 1.0})
    eng = OpenSeesEngine(m)
    r = eng.run_static("G")
    nj, ni = eng._find_node(eng._asm, pj), eng._find_node(eng._asm, (0, 0, 0))
    Kg = lc.global_K(K6, (0, 0, 0), pj)
    Xi, Xj = np.zeros(3), np.array(pj, float)
    dd = np.concatenate([d[:3] + np.cross(d[3:], Xi - Xj), d[3:]])
    f = Kg @ dd                                  # force on j, about i
    Fj = np.concatenate([f[:3], f[3:] + np.cross(Xi - Xj, f[:3])])
    assert np.allclose(r.reactions[nj], Fj, rtol=1e-9, atol=1e-9)
    assert np.allclose(r.reactions[ni], -f, rtol=1e-9, atol=1e-9)


def test_coupled_local_axes_and_decomposition():
    R = lc.local_axes((0, 0, 0), (0, 0, 3))          # vertical
    assert np.allclose(R, [[0, 0, 1], [1, 0, 0], [0, 1, 0]])
    R = lc.local_axes((0, 0, 0), (4, 0, 3))          # inclined
    assert np.allclose(R[0], [0.8, 0, 0.6])
    assert np.allclose(R[1], [-0.6, 0, 0.8])         # up, vertical plane
    rng = np.random.default_rng(7)
    for _ in range(50):
        A = rng.normal(size=(6, 6))
        K = A @ A.T
        t = np.trace(K[:3, 3:]) / 3.0
        K[:3, 3:] -= t * np.eye(3)
        K[3:, :3] = K[:3, 3:].T
        w = np.linalg.eigvalsh(K)[0]
        if w < 0:
            K += (0.1 - w) * np.eye(6)
        parts = lc.decompose(K)
        assert np.allclose(lc.realised(parts), K, rtol=1e-9,
                           atol=1e-9 * np.max(np.abs(K)))
        for k, e, r in parts["lines"]:
            assert k > 0.0 and abs(float(e @ r)) < 1e-9 * (1 + np.linalg.norm(r))


@pytest.mark.parametrize("K, msg", [
    ((np.eye(6) + np.diag([0.3, 0, 0], 3) + np.diag([0.3, 0, 0], -3)),
     "screw"),
    (-np.eye(6), "positive semi-definite"),
    (np.triu(np.ones((6, 6))), "symmetric"),
    (np.zeros((6, 6)), "all zero"),
])
def test_coupled_validation(K, msg):
    m = BuildingModel(name="v")
    with pytest.raises(ValueError, match=msg):
        m.add_link((0, 0, 0), (0, 0, 1), link_type="linear_coupled",
                   params={"K": np.asarray(K).tolist()})


# =========================================================================
# 2. multilinear elastic, each DOF separately
# =========================================================================
def test_multilinear_elastic_each_dof():
    curves = {"U1": [[0.001, 50.0], [0.01, 80.0]],           # mirrored
              "U2": [[-0.02, -30.0], [0.0, 0.0], [0.01, 40.0],
                     [0.03, 50.0]]}
    prm = {"curves": curves, "k_lin": [0, 0, 7.0e3, 0, 0, 0]}
    m = BuildingModel(name="mle")
    m.rigid_diaphragms = False
    pj = (0.0, 0.0, 0.5)                    # vertical: U1=Z, U2=X, U3=Y
    m.supports.append(PointSupport((0, 0, 0), (1,) * 6))
    m.supports.append(PointSupport(pj, (1,) * 6))
    m.add_link((0, 0, 0), pj, link_type="multilinear_elastic", params=prm)
    for name, d, fexp in [
            ("A", (0.02, 0.0, -0.005, 0, 0, 0),
             (45.0, 0.0, -(50.0 + 30.0 * 4.0 / 9.0))),
            ("B", (-0.01, 0.002, 0.004, 0, 0, 0),
             (-15.0, 14.0, 50.0 + 30.0 * 3.0 / 9.0))]:
        m.pattern(name, "other").ground_displacements.append(
            GroundDisplacement(pj, *d))
        m.cases[name] = LoadCase(name, {name: 1.0})
    eng = OpenSeesEngine(m)
    nj = None
    for name, fexp in (("A", (45.0, 0.0, -(50.0 + 30.0 * 4.0 / 9.0))),
                       ("B", (-15.0, 14.0, 50.0 + 30.0 * 3.0 / 9.0))):
        r = eng.run_static(name)
        nj = eng._find_node(eng._asm, pj)
        assert np.allclose(r.reactions[nj][:3], fexp, rtol=1e-9, atol=1e-9)


# =========================================================================
# 3. multilinear plastic hysteresis (Takeda, Pivot, degrading, concrete,
#    isotropic, BRB)
# =========================================================================
BB = [[0.01, 100.0], [0.05, 140.0]]          # k0 = 1e4, post slope 1000


def _cycle(ltype, prm, path, n=100):
    _harness(ltype, prm, (0.0, 0.0, 0.0), free=(1, 0, 0))
    h = _drive(1, path, n=n)
    ops.wipe()
    return h


def _b11(ltype, prm, path, n=100):
    """B11 Takeda / pivot materials (engine.hysteresis) in the same rig."""
    from skyframe.engine import hysteresis as hy
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0.0, 0.0, 0.0)
    ops.node(2, 0.0, 0.0, 0.0)
    ops.fix(1, 1, 1, 1, 1, 1, 1)
    ops.fix(2, 0, 1, 1, 1, 1, 1)
    mats, dirs, _ = hy.material_link(ltype, prm, 0, True)
    ops.element("zeroLength", 1, 1, 2, "-mat", *mats, "-dir", *dirs)
    ops.constraints("Plain")
    ops.numberer("Plain")
    ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-10, 100)
    ops.algorithm("Newton")
    h = _drive(1, path, n=n)
    ops.wipe()
    return h


def test_takeda_loop_hand_points():
    """+0.03 -> -0.02 -> +0.04 on the symmetric 100 / 140 kN backbone."""
    path = [0.03, -0.02, 0.04]
    h = _b11("multilinear_takeda", {"points": BB}, path, n=200)
    assert _at(h, 0.03) == pytest.approx(120.0, rel=1e-9)        # envelope
    u0 = 0.03 - 120.0 / 1.0e4                                    # 0.018
    # reload aims at the NEGATIVE first point (-0.01, -100): never yielded
    assert _at(h, 0.004) == pytest.approx(
        -100.0 * (u0 - 0.004) / (u0 + 0.01), rel=1e-9)
    assert _at(h, -0.02) == pytest.approx(-110.0, rel=1e-9)
    # unload from (-0.02, -110) to u0' = -0.009, then aim at (0.03, 120)
    u0b = -0.02 + 110.0 / 1.0e4
    assert _at(h[400:], 0.01) == pytest.approx(
        120.0 * (0.01 - u0b) / (0.03 - u0b), rel=1e-9)
    assert _at(h, 0.04) == pytest.approx(130.0, rel=1e-9)
    # the FNA law is the same rule
    dev = fdc.PeakOriented(BB)
    us = [u for u, _ in h[1:]]
    assert np.allclose(dev.path(us), [f for _, f in h[1:]], atol=1e-9)


def test_pivot_loop_hand_pinch_point():
    px, py = 0.5, 0.25
    prm = {"points": BB, "pinch_x": px, "pinch_y": py}
    h = _b11("multilinear_pivot", prm, [0.03, -0.02], n=200)
    u0 = 0.03 - 120.0 / 1.0e4                    # zero crossing 0.018
    ds = -0.01 - (1.0 - py) * (-100.0) / 1.0e4   # d* = -0.0025
    uch = u0 + px * (ds - u0)                    # 0.00775
    F = np.interp(uch, [u for u, _ in reversed(h[200:])],
                  [f for _, f in reversed(h[200:])])
    assert F == pytest.approx(py * -100.0, rel=1e-6)
    assert _at(h, -0.02) == pytest.approx(-110.0, rel=1e-9)
    dev = fdc.PeakOriented(BB, pinch_x=px, pinch_y=py)
    assert np.allclose(dev.path([u for u, _ in h[1:]]),
                       [f for _, f in h[1:]], atol=1e-9)


def test_degrading_unloading_stiffness():
    """multilinear_degrading: unloading at k0 (u_max/d1)^-0.5."""
    h = _cycle("multilinear_degrading", {"points": BB}, [0.03, 0.0], n=300)
    ku = 1.0e4 / math.sqrt(3.0)
    assert _at(h, 0.03) == pytest.approx(120.0, rel=1e-9)
    assert _at(h[300:], 0.02) == pytest.approx(120.0 - ku * 0.01,
                                              rel=1e-9)
    dev = fdc.PeakOriented(BB, beta=0.5)
    assert np.allclose(dev.path([u for u, _ in h[1:]]),
                       [f for _, f in h[1:]], atol=1e-9)


def test_concrete_option_pinches():
    h = _cycle("multilinear_concrete", {"points": BB}, [0.03, -0.02],
               n=300)
    dev = fdc.PeakOriented(BB, pinch_x=0.8, pinch_y=0.2)
    assert np.allclose(dev.path([u for u, _ in h[1:]]),
                       [f for _, f in h[1:]], atol=1e-9)
    tk = fdc.PeakOriented(BB).path([u for u, _ in h[1:]])
    assert min(f for _, f in h[1:]) == pytest.approx(-110.0, rel=1e-9)
    assert any(abs(a - b) > 5.0 for a, (_, b) in zip(tk, h[1:]))  # pinched


def test_isotropic_reversal_yields_at_minus_fmax():
    """Reversal from the backbone's last point (0.05, 140) stays elastic
    over the whole 2 F_max range and yields at -F_max (ETABS isotropic)."""
    prm = {"points": BB}
    D, Fmax = 0.05, 140.0
    u_rev = D - 2.0 * Fmax / 1.0e4                   # 0.022
    h = _cycle("multilinear_isotropic", prm, [D, u_rev, u_rev - 0.01],
               n=200)
    assert _at(h, D) == pytest.approx(Fmax, rel=1e-9)
    assert _at(h[200:], u_rev) == pytest.approx(-Fmax, rel=1e-6)
    # past -F_max the reverse hardening slope b k0 continues
    assert h[-1][1] == pytest.approx(-Fmax - 0.1 * 1.0e4 * 0.01, rel=1e-6)
    # kinematic would have yielded at F_max - 2 F_y = -60
    target, steel01 = lc.isotropic_shift(prm, D)
    assert steel01 == pytest.approx(target, rel=1e-12)
    t2, s2 = lc.isotropic_shift(prm, 0.03)           # other amplitude
    assert s2 == pytest.approx(t2, rel=0.15)        # 0.8-power law (~9 %)


def test_brb_multilinear_backbone():
    prm = {"points": [[0.002, 400.0], [0.03, 540.0]],
           "points_neg": [[-0.002, -400.0], [-0.03, -610.0]]}
    p = lc.brb_params(prm)
    assert p["bt"] == pytest.approx(140.0 / 0.028 / 2.0e5)
    assert p["bc"] == pytest.approx(210.0 / 0.028 / 2.0e5)
    h = _cycle("multilinear_brb", prm, [0.03, -0.03], n=400)
    assert _at(h, 0.03) == pytest.approx(540.0, rel=0.01)
    assert _at(h, -0.03) == pytest.approx(-610.0, rel=0.01)
    m = BuildingModel(name="brbv")
    with pytest.raises(ValueError, match="same point"):
        m.add_link((0, 0, 0), (2, 0, 2), link_type="multilinear_brb",
                   params={"points": prm["points"],
                           "points_neg": [[-0.003, -400.0], [-0.03, -600]]})


def test_plastic_axial_layout_on_inclined_link():
    """An inclined multilinear_degrading link acts along its axis."""
    _harness("multilinear_degrading", {"points": BB}, (2.0, 0.0, 0.0),
             free=(1, 0, 0))
    h = _drive(1, [0.03])
    ops.wipe()
    assert h[-1][1] == pytest.approx(120.0, rel=1e-9)


# =========================================================================
# 4. HDR isolator
# =========================================================================
HDR = {"Gr": 400.0, "Kbulk": 2.0e6, "D1": 0.1, "D2": 0.9, "ts": 0.003,
       "tr": 0.008, "n": 20.0, "a1": 800.0, "a2": 2000.0, "a3": 5000.0,
       "b1": 0.0, "b2": 0.0, "b3": 0.0, "c1": 0.0, "c2": 0.0, "c3": 0.0,
       "c4": 0.0}


def _hdr_cycle(prm, path, N=1000.0, n=100):
    _harness("hdr_isolator", prm, (0.0, 0.0, 0.0), free=(1, 0, 1))
    ops.timeSeries("Constant", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(2, 0.0, 0.0, -N, 0.0, 0.0, 0.0)
    ops.integrator("LoadControl", 1.0)
    ops.analysis("Static")
    assert ops.analyze(1) == 0
    ops.loadConst("-time", 0.0)
    h = _drive(1, path, n=n)
    ops.wipe()
    return h


def test_hdr_elastic_grant_component():
    """b = c = 0: the Grant model reduces to its elastic part
    F = a1 u + a2 u^3 + a3 u^5 (documented HDR response)."""
    h = _hdr_cycle(HDR, [0.2, -0.2])
    for u, f in h[::20]:
        assert f == pytest.approx(lc.hdr_elastic_force(HDR, u), rel=1e-6,
                                  abs=1e-9)


def test_hdr_cycle_dissipates_and_is_odd():
    prm = dict(HDR, b1=10.0, b2=5.0, b3=2.0)
    h = _hdr_cycle(prm, [0.15, -0.15, 0.15], n=150)
    u = np.array([p[0] for p in h])
    F = np.array([p[1] for p in h])
    area = float(np.sum(0.5 * (F[1:] + F[:-1]) * np.diff(u)))
    assert area > 0.0                                   # hysteretic loop
    # the first loading branch exceeds the elastic Grant part
    assert F[150] > lc.hdr_elastic_force(prm, u[150])


# =========================================================================
# 5. friction: velocity dependence, per direction, tension
# =========================================================================
def _slide(prm, v, N=1000.0, ltype="fp_isolator"):
    """Bearing under axial N, its top driven at constant velocity v
    (MultipleSupport imposed motion carries the velocity)."""
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0.0, 0.0, 0.0)
    ops.node(2, 0.0, 0.0, 0.0)
    ops.fix(1, 1, 1, 1, 1, 1, 1)
    ops.fix(2, 0, 1, 0, 1, 1, 1)
    asm = _Asm({1: (0.0, 0.0, 0.0), 2: (0.0, 0.0, 0.0)})
    lk = LinkMember("F", (0, 0, 0), (0, 0, 0), [0.0] * 6, ltype, prm)
    lcb.build(_Eng(), asm, lk, ltype, prm, 1, 2, 0, 0, 0, 100, object())
    ops.timeSeries("Constant", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(2, 0.0, 0.0, -N, 0.0, 0.0, 0.0)
    ops.timeSeries("Path", 2, "-time", 0.0, 100.0, "-values", 0.0,
                   v * 100.0)
    ops.timeSeries("Constant", 3, "-factor", v)
    ops.pattern("MultipleSupport", 2)
    ops.groundMotion(1, "Plain", "-disp", 2, "-vel", 3)
    ops.imposedMotion(2, 1, 1)
    ops.constraints("Transformation")
    ops.numberer("Plain")
    ops.system("UmfPack")
    ops.test("NormDispIncr", 1e-10, 50)
    ops.algorithm("Newton")
    ops.integrator("Newmark", 0.5, 0.25)
    ops.analysis("Transient")
    T = 0.05 / v                         # slide 50 mm at velocity v
    for _ in range(50):
        assert ops.analyze(1, T / 50.0) == 0
    f = ops.eleResponse(asm.link_ele["F"], "basicForce")
    u = ops.nodeDisp(2, 1)
    ops.wipe()
    return float(f[1]), -float(f[0]), u


@pytest.mark.parametrize("v", [0.001, 0.05, 0.2, 1.0])
def test_velocity_dependent_friction_mu_of_v(v):
    prm = {"R": 2.0, "mu": 0.03, "mu_fast": 0.10, "rate": 20.0}
    F, N, u = _slide(prm, v)
    mu_hand = 0.10 - 0.07 * math.exp(-20.0 * v)
    assert lc.friction_mu(prm, v) == pytest.approx(mu_hand, rel=1e-12)
    # flat-slider form isolates the friction term exactly
    Ff, Nf, _ = _slide({"mu": 0.03, "mu_fast": 0.10, "rate": 20.0}, v,
                       ltype="friction_spring")
    assert Ff / Nf == pytest.approx(mu_hand, rel=1e-6)
    # pendulum: mu N + N u / R (small-displacement form, 1 % at u/R)
    assert F == pytest.approx(mu_hand * N + N * u / 2.0, rel=0.01)


def test_normal_force_dependent_friction():
    prm = {"mu": 0.03, "mu_fast": 0.10, "rate": 20.0, "n_slow": 0.9,
           "n_fast": 0.9}
    F, N, _ = _slide(prm, 0.2, ltype="friction_spring")
    mu_hand = lc.friction_mu(prm, 0.2, N)
    ms, mf = 0.03 * N ** -0.1, 0.10 * N ** -0.1
    assert mu_hand == pytest.approx(mf - (mf - ms) * math.exp(-4.0))
    assert F / N == pytest.approx(mu_hand, rel=1e-6)


TC = {"R": 2.0, "mu": 0.05, "k_init": 1.0e5, "kv": 1.0e7}


def _tc_push(prm, uz, ux, dof=1, free=(1, 1, 1)):
    """Axial deformation uz (+ = uplift) then a slide ux along dof."""
    _harness("tc_fp_isolator", prm, (0.0, 0.0, 0.0), free=free)
    ops.system("SparseGeneral")          # re-setup after the 2nd sp
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.sp(2, 3, uz)
    ops.integrator("LoadControl", 0.05)
    ops.analysis("Static")
    for _ in range(20):
        assert ops.analyze(1) == 0
    ops.loadConst("-time", 0.0)
    ops.timeSeries("Linear", 2)
    ops.pattern("Plain", 2, 2)
    ops.sp(2, dof, ux)
    for _ in range(20):
        assert ops.analyze(1) == 0
    ops.reactions()
    out = [-ops.nodeReaction(1, d) for d in (1, 2, 3)]
    ops.wipe()
    return out


def test_tc_per_direction_friction_and_radius():
    """FNA law (uncoupled per direction): F_x = mu N + N u/R,
    F_y = mu3 N + N u/R3; direct integration rejects it clearly."""
    prm = dict(TC, mu3=0.10, R3=4.0)
    from skyframe.engine import fna_c
    N = 1000.0
    dz = -N / TC["kv"]
    dev = fdc.FrictionBearing(2.0, 4.0, fna_c._mu_fn(prm), 1e5, 1e7,
                              per_dir=True, mu3_fn=fna_c._mu_fn(prm, "mu3"),
                              tc=True)
    for u in np.linspace(0.0, 0.05, 51):
        dev.commit([u, u, dz], [0, 0, 0], 0.01)
    F = dev.trial([0.05, 0.05, dz], [0, 0, 0], 0.01)
    assert F[0] == pytest.approx(0.05 * N + N * 0.05 / 2.0, rel=1e-9)
    assert F[1] == pytest.approx(0.10 * N + N * 0.05 / 4.0, rel=1e-9)
    assert F[2] == pytest.approx(-N, rel=1e-12)
    with pytest.raises(NotImplementedError, match="FNA"):
        _harness("tc_fp_isolator", prm, (0.0, 0.0, 0.0), free=(1, 1, 1))
    ops.wipe()


def test_tc_friction_in_tension():
    prm = dict(TC, kt=1.0e5, gap_t=0.002, mu_t=0.1, R_t=2.0)
    Fu = _tc_push(prm, 0.004, 0.05, free=(1, 0, 1))    # uplift past gap
    Nt = 1.0e5 * 0.002
    assert Fu[2] == pytest.approx(Nt, rel=1e-6)
    assert Fu[0] == pytest.approx(0.1 * Nt + Nt * 0.05 / 2.0, rel=0.01)
    Fg = _tc_push(prm, 0.001, 0.05, free=(1, 0, 1))    # inside the gap
    assert Fg == pytest.approx([0.0, 0.0, 0.0], abs=1e-6)
    Fc = _tc_push(prm, -1.0e-4, 0.05, free=(1, 0, 1))  # compression
    assert Fc[0] == pytest.approx(0.05 * 1000.0 + 1000.0 * 0.05 / 2.0,
                                  rel=0.01)


# =========================================================================
# 6. effective (linear) stiffness and damping
# =========================================================================
H, B, M_BLK = 0.3, 4.0, 80.0
KE = 2.5e3                                   # kN/m per bearing


def _block(ltype, prm, uid_prefix="L"):
    mdl = BuildingModel(name="iso")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 6
    mdl.add_material(Material("STIFF", E=2e11, nu=0.3, unit_weight=0.0))
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
                     params=dict(prm), uid=f"{uid_prefix}{k + 1}")
        mdl.nodal_masses.append(NodalMass((x, y, H), mx=M_BLK / 4,
                                          my=M_BLK / 4))
        pat.nodal_loads.append(NodalLoad((x, y, H), fz=-196.2))
    return mdl


ISO = {"k1": 2.0e4, "k2": 1.0e3, "Fy": 50.0}
ISO_EFF = dict(ISO, k_eff=[1.0e7, KE, KE, 0.0, 0.0, 0.0])


def test_modal_uses_effective_stiffness():
    T_hand = 2.0 * math.pi * math.sqrt(M_BLK / (4.0 * KE))
    T = OpenSeesEngine(_block("isolator", ISO_EFF)).run_modal().periods
    assert T[0] == pytest.approx(T_hand, rel=1e-6)
    # without k_eff the INITIAL stiffness k1 governs (legacy)
    T0 = OpenSeesEngine(_block("isolator", ISO)).run_modal().periods
    assert T0[0] == pytest.approx(2.0 * math.pi * math.sqrt(
        M_BLK / (4.0 * ISO["k1"])), rel=1e-4)


@pytest.mark.parametrize("ltype, prm", [
    ("fp_isolator", {"R": 2.0, "mu": 0.05}),
    ("rubber_isolator_bouc_wen", {"k_init": 2.0e4, "qd": 40.0}),
    ("multilinear_takeda", {"points": [[0.002, 40.0], [0.1, 140.0]]}),
    ("hdr_isolator", HDR),
])
def test_effective_stiffness_any_nonlinear_type(ltype, prm):
    prm = dict(prm, k_eff=[1.0e7, KE, 2.0 * KE, 0.0, 0.0, 0.0])
    eng = OpenSeesEngine(_block(ltype, prm))
    T = eng.run_modal().periods
    # X (U2 = KE), Y (U3 = 2 KE) and the torsion of the rigid ring
    # (k_t = 4 (KE + 2 KE) (B/2)^2, J = M (B/sqrt 2)^2)
    hand = [2 * math.pi * math.sqrt(M_BLK / (4 * KE)),
            2 * math.pi * math.sqrt(M_BLK / (8 * KE)),
            2 * math.pi * math.sqrt(M_BLK * B ** 2 / 2.0
                                    / (12 * KE * (B / 2) ** 2))]
    for th in hand:
        assert min(abs(t - th) for t in T) <= 1e-5 * th


def test_linear_static_uses_k_eff_and_nonlinear_static_does_not():
    from skyframe.core.nonlinear_static import NonlinearStaticCase
    from skyframe.engine import nonlinear_static as nls
    mdl = _block("isolator", ISO_EFF)
    pat = mdl.pattern("PX", "other")
    for x, y in [(0, 0), (B, 0), (B, B), (0, B)]:
        pat.nodal_loads.append(NodalLoad((x, y, H), fx=100.0))
    mdl.cases["PX"] = LoadCase("PX", {"PX": 1.0})
    mdl.nonlinear_static_cases["NPX"] = NonlinearStaticCase(
        "NPX", loads=[{"pattern": "PX", "scale": 1.0}], steps=20)
    eng = OpenSeesEngine(mdl)
    r = eng.run_static("PX")
    t = eng._find_node(eng._asm, (0, 0, H))
    assert r.node_disp[t][0] == pytest.approx(100.0 / KE, rel=1e-5)
    rn = nls.run_case(OpenSeesEngine(mdl), "NPX").case
    u_nl = rn.node_disp[t][0]
    u_hand = 0.05 / 2.0e4 * 0 + 50.0 / 2.0e4 + 50.0 / 1.0e3  # bilinear
    assert u_nl == pytest.approx(u_hand, rel=1e-3)


def test_linear_th_effective_damping():
    """Linear TH: k_eff + c_eff -> SDOF free vibration decays with the
    hand damping ratio zeta = sum c / (2 sqrt(K M))."""
    c = 2.0 * 0.10 * math.sqrt(4 * KE * M_BLK) / 4.0     # zeta = 10 %
    prm = dict(ISO, k_eff=[1.0e7, KE, KE, 0, 0, 0],
               c_eff=[0.0, c, c, 0, 0, 0])
    mdl = _block("isolator", prm)
    acc = [5.0] * 4 + [0.0] * 1200
    mdl.add_th_case("TH", "X", acc, 0.005, damping=1e-9)
    u = np.array(OpenSeesEngine(mdl).run_time_history("TH")
                 .story_ux["Story1"])
    peaks = [u[i] for i in range(1, len(u) - 1)
             if u[i] > u[i - 1] and u[i] >= u[i + 1] and u[i] > 0]
    delta = math.log(peaks[1] / peaks[2])
    zeta = delta / math.sqrt(4 * math.pi ** 2 + delta ** 2)
    assert zeta == pytest.approx(0.10, rel=0.03)


def test_nonlinear_th_uses_the_nonlinear_property():
    acc = [3.0 * math.sin(math.pi * k * 0.005 / 0.5) for k in range(101)] \
        + [0.0] * 300
    a = _block("isolator", ISO_EFF)
    a.add_th_case("TH", "X", acc, 0.005, damping=0.02)
    a.th_cases["TH"].nonlinear = True
    b = _block("isolator", ISO)
    b.add_th_case("TH", "X", acc, 0.005, damping=0.02)
    b.th_cases["TH"].nonlinear = True
    ua = OpenSeesEngine(a).run_time_history("TH").story_ux["Story1"]
    ub = OpenSeesEngine(b).run_time_history("TH").story_ux["Story1"]
    # same nonlinear devices; only the Rayleigh fit (initial modes) uses
    # the effective periods -> the response is close, and yields
    assert max(abs(x) for x in ub) > ISO["Fy"] / ISO["k1"]
    assert max(abs(x) for x in ua) == pytest.approx(
        max(abs(x) for x in ub), rel=0.1)


# =========================================================================
# 7. FNA
# =========================================================================
def _pulse(dt=0.005, amp=3.0):
    return ([amp * math.sin(math.pi * k * dt / 0.5) for k in range(101)]
            + [0.0] * 500), dt


def _di_fna(mdl):
    acc, dt = _pulse()
    mdl.add_th_case("TH", "X", acc, dt, damping=1e-9, gravity={"G": 1.0})
    u_th = np.array(OpenSeesEngine(mdl).run_time_history("TH")
                    .story_ux["Story1"])
    rf = OpenSeesEngine(mdl).run_fna("TH")
    return u_th, np.array(rf.story_ux["Story1"]), rf


@pytest.mark.parametrize("ltype, prm, dev", [
    ("multilinear_takeda", {"points": [[0.004, 60.0], [0.1, 120.0]]},
     "PeakOriented"),
    ("multilinear_degrading", {"points": [[0.004, 60.0], [0.1, 120.0]]},
     "PeakOriented"),
    ("multilinear_pivot", {"points": [[0.004, 60.0], [0.1, 120.0]]},
     "PeakOriented"),
])
def test_fna_peak_oriented_matches_direct_integration(ltype, prm, dev):
    u_th, u_f, rf = _di_fna(_block(ltype, prm))
    pk = np.max(np.abs(u_th))
    assert pk > 0.004                                     # yielded
    assert abs(np.max(np.abs(u_f)) - pk) <= 0.03 * pk
    assert sum(rf.extra["fna"]["devices"].values()) == 4   # C5 or C6 hysteretic law (both validated)


def test_fna_velocity_friction_and_tc_match_direct_integration():
    prm = {"R": 2.0, "mu": 0.03, "mu_fast": 0.08, "rate": 30.0,
           "k_init": 5.0e4}
    u_th, u_f, rf = _di_fna(_block("fp_isolator", prm))
    pk = np.max(np.abs(u_th))
    assert abs(np.max(np.abs(u_f)) - pk) <= 0.05 * pk
    assert rf.extra["fna"]["devices"] == {"FrictionBearing": 4}
    tc = {"R": 2.0, "mu": 0.04, "k_init": 5.0e4, "kt": 1e4}
    u_th, u_f, rf = _di_fna(_block("tc_fp_isolator", tc))
    pk = np.max(np.abs(u_th))
    assert abs(np.max(np.abs(u_f)) - pk) <= 0.05 * pk


def test_fna_brb_and_bilinear_damper_laws():
    k0, fy, bt, bc = 1e4, 100.0, 0.05, 0.08
    dev = fdc.AsymKinematic(k0, fy, bt, bc)
    F = []
    for u in np.linspace(0, 0.03, 61):
        dev.commit([u], [0], 0.01)
        F.append(dev.trial([u], [0], 0.01)[0])
    assert F[-1] == pytest.approx(fy + bt * k0 * (0.03 - 0.01), rel=1e-9)
    for u in np.linspace(0.03, -0.03, 121):
        dev.commit([u], [0], 0.01)
    # reversal: elastic range 2 Fy, then the compression hardening slope
    f_end = dev.trial([-0.03], [0], 0.01)[0]
    assert f_end < -fy
    d = fdc.BilinearDamper(1e9, 100.0, 50.0, 0.1)
    for _ in range(400):
        d.commit([0], [1.0], 1e-3)
    # steady state (stiff spring): F = Fr + ratio cd (v - vr)
    assert d.trial([0], [1.0], 1e-3)[0] == pytest.approx(
        50.0 + 0.1 * 100.0 * (1.0 - 0.5), rel=1e-4)


def test_fna_uses_k_eff_in_the_basis():
    mdl = _block("isolator", ISO_EFF)
    acc, dt = _pulse()
    mdl.add_th_case("TH", "X", acc, dt, damping=0.02)
    rf = OpenSeesEngine(mdl).run_fna("TH")
    T = max(rf.extra["fna"]["periods"])
    assert T == pytest.approx(2 * math.pi * math.sqrt(M_BLK / (4 * KE)),
                              rel=1e-5)


def test_fna_rejects_hdr_and_isotropic():
    for lt, prm in (("hdr_isolator", HDR),
                    ("multilinear_isotropic", {"points": BB})):
        mdl = _block(lt, prm)
        acc, dt = _pulse()
        mdl.add_th_case("TH", "X", acc, dt, damping=0.02)
        with pytest.raises(NotImplementedError, match="direct integration"):
            OpenSeesEngine(mdl).run_fna("TH")


# =========================================================================
# 8. registration, validation, round trip, legacy identity
# =========================================================================
def test_types_registered_and_nested_round_trip():
    assert set(lc.C_LINK_TYPES) <= set(LINK_TYPES)
    m = BuildingModel(name="rt")
    m.add_link((0, 0, 0), (0, 0, 1), link_type="linear_coupled",
               params={"K": K6.tolist()})
    m.add_link((1, 0, 0), (1, 0, 1), link_type="multilinear_elastic",
               params={"curves": {"U2": [[0.01, 5.0], [0.02, 6.0]]},
                       "k_lin": [1e5, 0, 0, 0, 0, 0]})
    m.add_link((2, 0, 0), (2, 0, 0), link_type="isolator",
               params=dict(ISO, k_eff=[1e7, 1e3, 1e3, 0, 0, 0],
                           c_eff=[0, 5, 5, 0, 0, 0]))
    m.add_link((3, 0, 0), (3, 0, 0), link_type="tc_fp_isolator",
               params=dict(TC, mu3=0.1, R3=3.0, mu_fast=0.1, rate=20.0,
                           kt=1e4, mu_t=0.05))
    m.add_link((4, 0, 0), (4, 0, 0.2), link_type="hdr_isolator",
               params=HDR)
    d = json.loads(json.dumps(m.to_dict()))
    m2 = BuildingModel.from_dict(d)
    assert [lk.to_dict() for lk in m2.links] == [lk.to_dict()
                                                 for lk in m.links]


@pytest.mark.parametrize("ltype, prm, pj, msg", [
    ("isolator", dict(ISO, k_eff=[1, 2, 3]), (0, 0, 0), "6 finite"),
    ("isolator", dict(ISO, k_eff=[0] * 6), (0, 0, 0), "at least one"),
    ("isolator", dict(ISO, c_eff=[0, 1, 1, 0, 0, 0]), (0, 0, 0), "needs"),
    ("fp_isolator", {"R": 2, "mu": 0.05, "mu_fast": 0.1}, (0, 0, 0),
     "both"),
    ("fp_isolator", {"R": 2, "mu": 0.05, "n_slow": 0.9}, (0, 0, 0),
     "both"),
    ("tc_fp_isolator", dict(TC, mu_t=0.1), (0, 0, 0), "kt"),
    ("multilinear_elastic", {"curves": {"U7": [[0.1, 1.0]]}}, (0, 0, 1),
     "dof"),
    ("multilinear_elastic", {"curves": {"U1": [[0.1, 1.0]]},
                             "k_lin": [5, 0, 0, 0, 0, 0]}, (0, 0, 1),
     "both a curve"),
    ("multilinear_isotropic", {"points": [[0.01, 1], [0.02, 2],
                                          [0.03, 3]]}, (0, 0, 1),
     "2..2"),
    ("multilinear_degrading", {"points": BB, "kv": 1e6}, (2, 0, 1),
     "kv applies"),
    ("hdr_isolator", dict(HDR, D1=1.0), (0, 0, 0), "D1"),
    ("hdr_isolator", HDR, (1, 0, 1), "vertical"),
])
def test_validation(ltype, prm, pj, msg):
    m = BuildingModel(name="v")
    with pytest.raises(ValueError, match=msg):
        m.add_link((0, 0, 0), pj, link_type=ltype,
                   params=prm if ltype != "elastic" else prm)
