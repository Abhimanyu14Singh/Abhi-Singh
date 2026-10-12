"""Wave-B link types, frequency-dependent links and composite damping.

CONTRACT "Link types: Plastic (Wen), BRB, bilinear damper, T/C friction
isolator", "Frequency-dependent link properties" and
"Material-proportional (composite) modal damping".

Every expected value is closed-form / hand-derived here:

* Plastic (Wen): exact RK4 integration of the Wen ODE
  (``core.links_ext.wen_history``); large-deformation force
  ``ratio*k*d + (1-ratio)*Fy``; near-bilinear loop area
  ``4 (1-ratio) Fy (D - dy)`` for a large yielding exponent.
* BRB: ``F(+d_bm) = omega*Fy``, ``F(-d_bm) = -beta*omega*Fy`` and the
  bilinear-asymmetric backbone between.
* Bilinear damper: terminal velocity of a mass pushed by a constant force
  through the damper alone: ``P = F(v)``.
* T/C isolator: ``-kv*d`` in compression, exactly 0 (axial AND shear) in
  uplift, ``kt (d - gap_t)`` with a hold-down.
* Frequency-dependent link: SDOF ``u = F / (K(f) - w^2 m + i w C(f) +
  2 i zeta w0 w m)`` and base ``|K + i w C| |u|``.
* Composite damping: stacked cantilever, strain energies
  ``E1 = P^2 (H^3 - L2^3) / (6 E1 I)``, ``E2 = P^2 L2^3 / (6 E2 I)``.

Units: kN, m, tonne, s.
"""

import copy
import json
import math

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine
import openseespy.opensees as ops  # noqa: E402

from skyframe.core import links_ext as lx  # noqa: E402
from skyframe.core import composite_damping as cdm  # noqa: E402
from skyframe.core.frequency_cases import (FrequencyLoad,  # noqa: E402
                                           add_frequency_function,
                                           add_psd_case,
                                           add_steady_state_case)
from skyframe.core.model import (LINK_TYPES, TH_DAMPING_MODELS,  # noqa: E402
                                 BuildingModel, FrameSection, LinkMember,
                                 Material, NodalLoad, NodalMass,
                                 PointSupport)
from skyframe.engine import composite_damping as ecd  # noqa: E402
from skyframe.engine import frequency as fq  # noqa: E402
from skyframe.engine import links_ext as lb  # noqa: E402


class _Asm:
    def __init__(self, coords):
        self.node_coords = coords
        self.link_ele = {}


class _Eng:
    _rl_ctx = None


def _harness(ltype, prm, pj=(0.0, 0.0, 0.0), free=(1, 0, 0)):
    """Fixed node 1 at the origin, node 2 at pj with only ``free`` global
    translations released; the link built by the engine builder."""
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0.0, 0.0, 0.0)
    ops.node(2, *pj)
    ops.fix(1, 1, 1, 1, 1, 1, 1)
    ops.fix(2, *[0 if f else 1 for f in free], 1, 1, 1)
    asm = _Asm({1: (0.0, 0.0, 0.0), 2: tuple(pj)})
    lk = LinkMember("L1", (0.0, 0.0, 0.0), tuple(pj), [0.0] * 6, ltype, prm)
    lb.build(_Eng(), asm, lk, ltype, prm, 1, 2, 0, 0, 0)
    ops.constraints("Plain")
    ops.numberer("Plain")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1e-10, 100)
    ops.algorithm("Newton")
    return asm


def _drive(dof, path, n=200):
    """Displacement-controlled path on node 2 ``dof``; [(u, F_link)] with
    F_link = the force the link carries (= -reaction at node 1)."""
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


# =========================================================================
# 1. Plastic (Wen)
# =========================================================================
WEN = {"k": 1.0e4, "Fy": 100.0, "ratio": 0.05, "exp": 2.0}


@pytest.mark.parametrize("pj, dof", [((0.0, 0.0, 0.0), 1),     # shear U2
                                     ((2.0, 0.0, 0.0), 1)])    # axial U1
def test_wen_follows_the_exact_wen_law(pj, dof):
    D = 0.05
    _harness("plastic_wen", WEN, pj)
    h = _drive(dof, [D, -D, D], n=400)
    ops.wipe()
    ref = lx.wen_history(WEN, [D, -D, D], n=400)
    F = np.array([f for _, f in h])
    Fr = np.array([f for _, f in ref])
    # backward-Euler z update at du = uy/80: <= 0.5 % of Fy anywhere
    assert np.max(np.abs(F - Fr)) < 5e-3 * WEN["Fy"]
    # yield force: large deformation -> ratio*k*d + (1-ratio)*Fy
    k, Fy, r = WEN["k"], WEN["Fy"], WEN["ratio"]
    assert h[400][1] == pytest.approx(r * k * D + (1 - r) * Fy, rel=1e-3)
    assert h[800][1] == pytest.approx(-(r * k * D + (1 - r) * Fy), rel=1e-3)
    # loop area = exact integral of the Wen loop
    assert lx.loop_area(h[400:]) == pytest.approx(lx.loop_area(ref[400:]),
                                                  rel=5e-3)


def test_wen_loop_area_tends_to_bilinear():
    prm = dict(WEN, exp=25.0)
    D = 0.05
    _harness("plastic_wen", prm)
    h = _drive(1, [D, -D, D], n=800)
    ops.wipe()
    dy = prm["Fy"] / prm["k"]
    hand = 4.0 * (1 - prm["ratio"]) * prm["Fy"] * (D - dy)
    assert lx.loop_area(h[800:]) == pytest.approx(hand, rel=0.01)
    # yield force read off the loop at the yield deformation (sharp knee)
    f_at = {round(u, 9): f for u, f in h[:801]}
    assert f_at[round(dy, 9)] == pytest.approx(prm["Fy"], rel=0.04)


def test_wen_vertical_layout_and_kv():
    _harness("plastic_wen", dict(WEN, kv=5.0e5), (0.0, 0.0, 0.5),
             free=(0, 0, 1))
    h = _drive(3, [-0.001], n=10)
    ops.wipe()
    assert h[-1][1] == pytest.approx(-500.0, rel=1e-9)        # kv * d


# =========================================================================
# 2. Buckling-restrained brace
# =========================================================================
BRB = {"k": 1.0e5, "Fy": 500.0, "d_bm": 0.05, "beta": 1.15, "omega": 1.4}


def test_brb_asymmetric_backbone_hand_values():
    k, Fy, dbm = BRB["k"], BRB["Fy"], BRB["d_bm"]
    dy = Fy / k
    bt = (1.4 - 1.0) * Fy / (k * (dbm - dy))
    bc = (1.15 * 1.4 - 1.0) * Fy / (k * (dbm - dy))
    assert lx.brb_hardening(BRB) == pytest.approx((bt, bc), rel=1e-12)
    for sign in (1.0, -1.0):
        _harness("brb", BRB, (3.0, 0.0, 0.0))
        h = _drive(1, [sign * dbm], n=200)
        ops.wipe()
        f_at = {round(u, 9): f for u, f in h}
        for frac in (0.3, 0.5, 0.75, 1.0):
            d = sign * frac * dbm
            hand = (Fy + bt * k * (d - dy) if sign > 0
                    else -(Fy + bc * k * (-d - dy)))
            assert f_at[round(d, 9)] == pytest.approx(hand, rel=1e-6)
            assert lx.brb_backbone(BRB, d) == pytest.approx(hand, rel=1e-12)
    # the end values are the AISC adjusted strengths
    assert lx.brb_backbone(BRB, dbm) == pytest.approx(1.4 * Fy)
    assert lx.brb_backbone(BRB, -dbm) == pytest.approx(-1.15 * 1.4 * Fy)


def test_brb_elastic_slope_and_cycle_bounded():
    _harness("brb", BRB, (3.0, 0.0, 0.0))
    h = _drive(1, [0.002, BRB["d_bm"], -BRB["d_bm"]], n=100)
    ops.wipe()
    assert h[100][1] == pytest.approx(BRB["k"] * 0.002, rel=1e-6)
    assert max(f for _, f in h) <= 1.4 * BRB["Fy"] * (1 + 1e-9)
    assert lx.loop_area(h) > 0.0


# =========================================================================
# 3. Bilinear damper
# =========================================================================
BD = {"cd": 200.0, "Fr": 50.0, "ratio": 0.1, "k": 1.0e6}


def _terminal_velocity(P, prm=BD, m=1.0, T=1.0, dt=5e-4):
    _harness("damper_bilinear", prm, (1.0, 0.0, 0.0))
    ops.mass(2, m, 0, 0, 0, 0, 0)
    ops.timeSeries("Constant", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(2, P, 0, 0, 0, 0, 0)
    ops.integrator("Newmark", 0.5, 0.25)
    ops.analysis("Transient")
    for _ in range(int(T / dt)):
        assert ops.analyze(1, dt) == 0
    v = ops.nodeVel(2, 1)
    ops.wipe()
    return v


@pytest.mark.parametrize("P", [10.0, 40.0, 55.0, 65.0, -60.0])
def test_bilinear_damper_force_velocity_points(P):
    c, Fr, p = BD["cd"], BD["Fr"], BD["ratio"]
    v = _terminal_velocity(P)
    vr = Fr / c
    hand_v = (P / c if abs(P) <= Fr
              else math.copysign(vr + (abs(P) - Fr) / (p * c), P))
    assert v == pytest.approx(hand_v, rel=1e-5)
    assert lx.bilinear_damper_force(BD, hand_v) == pytest.approx(P,
                                                                 rel=1e-12)


def test_bilinear_damper_carries_no_static_force():
    def model(with_damper):
        m = BuildingModel(name="bd")
        m.rigid_diaphragms = False
        m.add_material(Material("S", E=2e8, nu=0.3))
        m.add_section(FrameSection.rectangular("C", "S", 0.3, 0.3))
        m.set_stories([3.0])
        m.add_member("column", "C", (0, 0, 0), (0, 0, 3), story="Story1",
                     uid="C1")
        m.supports.append(PointSupport((0, 0, 0), (1,) * 6))
        if with_damper:
            m.supports.append(PointSupport((4, 0, 3), (1,) * 6))
            m.add_link((4, 0, 3), (0, 0, 3), link_type="damper_bilinear",
                       params=dict(BD))
        m.pattern("P", "other").nodal_loads.append(NodalLoad((0, 0, 3),
                                                             fx=10.0))
        m.add_case("P", {"P": 1.0})
        return m
    u = [OpenSeesEngine(model(w)).run_static("P").node_disp for w in (0, 1)]
    e0 = OpenSeesEngine(model(0))
    t = e0._find_node(e0._build(), (0, 0, 3))
    e1 = OpenSeesEngine(model(1))
    t1 = e1._find_node(e1._build(), (0, 0, 3))
    assert u[1][t1][0] == pytest.approx(u[0][t][0], rel=1e-12)


# =========================================================================
# 4. T/C friction isolator
# =========================================================================
TC = {"R": 2.0, "mu": 0.1, "kv": 1.0e6}


def test_tc_isolator_zero_force_under_uplift():
    _harness("tc_fp_isolator", TC, (0.0, 0.0, 0.0), free=(0, 0, 1))
    h = _drive(3, [-0.001, 0.002, -0.0005], n=20)
    for u, f in h:
        assert f == pytest.approx(lx.tc_axial_force(TC, u), abs=1e-9)
    assert h[20][1] == pytest.approx(-1000.0, rel=1e-9)        # -kv d
    assert all(f == 0.0 for u, f in h if u > 0.0)               # uplift
    ops.wipe()


def test_tc_isolator_shear_released_in_uplift():
    """Imposed (ux, uz) path: compress, slide (mu N + N d / R), then lift
    off while displaced — shear AND axial force drop to exactly 0."""
    _harness("tc_fp_isolator", TC, (0.0, 0.0, 0.0), free=(1, 0, 1))
    ops.node(3, 0.0, 0.0, 0.0)                 # one free dummy equation
    ops.fix(3, 0, 1, 1, 1, 1, 1)
    ops.uniaxialMaterial("Elastic", 900, 1.0)
    ops.element("zeroLength", 900, 1, 3, "-mat", 900, "-dir", 1)
    ops.constraints("Transformation")
    t = [0.0, 1.0, 2.0, 3.0]
    ops.timeSeries("Path", 5, "-time", *t, "-values", 0.0, 0.0, 0.02, 0.02)
    ops.timeSeries("Path", 6, "-time", *t, "-values", 0.0, -0.001, -0.001,
                   0.002)
    ops.pattern("Plain", 5, 5)
    ops.sp(2, 1, 1.0)
    ops.pattern("Plain", 6, 6)
    ops.sp(2, 3, 1.0)
    ops.integrator("LoadControl", 0.05)
    ops.analysis("Static")
    N = 1.0e6 * 0.001
    hist = []
    for _ in range(60):
        assert ops.analyze(1) == 0
        hist.append((ops.getTime(), list(ops.basicForce(1))))
    ops.wipe()
    f = dict((round(tt, 6), q) for tt, q in hist)
    assert f[1.0][0] == pytest.approx(-N, rel=1e-9)                # -kv d
    assert abs(f[2.0][1]) == pytest.approx(0.1 * N + N * 0.02 / 2.0,
                                           rel=0.02)    # mu N + N d / R
    for tt, q in hist:
        if tt > 2.0 + 1.0 / 3.0 + 1e-9:                # uz > 0: uplift
            assert q[0] == 0.0 and q[1] == 0.0 and q[2] == 0.0


def test_tc_isolator_hold_down_after_gap():
    prm = dict(TC, kt=2.0e5, gap_t=0.001)
    _harness("tc_fp_isolator", prm, (0.0, 0.0, 0.0), free=(0, 0, 1))
    h = _drive(3, [0.003, -0.001], n=30)
    ops.wipe()
    for u, f in h:
        assert f == pytest.approx(lx.tc_axial_force(prm, u), abs=1e-6)
    assert dict((round(u, 9), f) for u, f in h)[0.003] == pytest.approx(
        2.0e5 * 0.002, rel=1e-9)


# =========================================================================
# 5. links in the engine, validation, round trip
# =========================================================================
def _frame_with(ltype, prm, pi, pj):
    m = BuildingModel(name="lk")
    m.rigid_diaphragms = False
    m.add_material(Material("S", E=2e6, nu=0.3))       # soft frame
    m.add_section(FrameSection.rectangular("C", "S", 0.2, 0.2))
    m.set_stories([3.0])
    for x in (0.0, 4.0):
        m.add_member("column", "C", (x, 0, 0), (x, 0, 3), story="Story1",
                     uid=f"C{int(x)}")
        m.supports.append(PointSupport((x, 0, 0), (1,) * 6))
    m.add_member("beam", "C", (0, 0, 3), (4, 0, 3), story="Story1",
                 uid="B1")
    m.add_link(pi, pj, link_type=ltype, params=dict(prm))
    return m


def test_wen_brace_static_in_engine_matches_wen_law():
    """Inclined Plastic (Wen) brace from a support to the roof corner of
    a soft frame, pushed in a 40-step nonlinear static case (the Wen law
    is path dependent — a one-step linear-static case integrates z in a
    single implicit step): the brace force (support reaction) lands on
    the Wen monotonic curve at the brace elongation."""
    from skyframe.core.nonlinear_static import NonlinearStaticCase
    from skyframe.engine import nonlinear_static as nls
    prm = {"k": 2.0e4, "Fy": 60.0, "ratio": 0.1, "exp": 1.0}
    m = _frame_with("plastic_wen", prm, (8.0, 0, 0), (4.0, 0, 3.0))
    m.supports.append(PointSupport((8.0, 0, 0), (1,) * 6))
    m.pattern("P", "other").nodal_loads.append(NodalLoad((4, 0, 3),
                                                         fx=-120.0))
    m.nonlinear_static_cases["NLS"] = NonlinearStaticCase(
        "NLS", loads=[{"pattern": "P", "scale": 1.0}], steps=40)
    eng = OpenSeesEngine(m)
    assert eng._nonlinear_static_links_present()
    r = nls.run_case(eng, "NLS").case
    asm = eng._build()                        # same (stable) node tags
    t = eng._find_node(asm, (4, 0, 3))
    u = np.array(r.node_disp[t][:3])
    e = np.array([4.0 - 8.0, 0.0, 3.0]) / 5.0
    d = float(u @ e)                                  # elongation
    ref = lx.wen_history(prm, [d], n=2000)[-1][1]
    assert d > prm["Fy"] / prm["k"]                    # past yield
    tb = eng._find_node(asm, (8.0, 0, 0))
    R = np.array(r.reactions[tb][:3])
    assert float(-R @ e) == pytest.approx(ref, rel=2e-3)


@pytest.mark.parametrize("ltype, prm, pi, pj, msg", [
    ("plastic_wen", {"k": 1e4}, (0, 0, 0), (1, 0, 0), "missing required"),
    ("plastic_wen", {"k": 1e4, "Fy": 1, "ratio": 1.0}, (0, 0, 0), (1, 0, 0),
     "ratio"),
    ("plastic_wen", {"k": 1e4, "Fy": 1, "kv": 1e6}, (0, 0, 0), (1, 0, 0),
     "kv applies"),
    ("brb", {"k": 1e5, "Fy": 500, "d_bm": 0.004}, (0, 0, 0), (1, 0, 1),
     "d_bm"),
    ("brb", {"k": 1e5, "Fy": 500, "d_bm": 0.05, "omega": 0.9}, (0, 0, 0),
     (1, 0, 1), "omega"),
    ("brb", {"k": 1e5, "Fy": 500, "d_bm": 0.05, "beta": 0.5}, (0, 0, 0),
     (1, 0, 1), "beta"),
    ("brb", {"k": 1e5, "Fy": 500, "d_bm": 0.05}, (0, 0, 0), (0, 0, 0),
     "zero length"),
    ("damper_bilinear", {"cd": 100, "Fr": -1}, (0, 0, 0), (1, 0, 0), "Fr"),
    ("damper_bilinear", {"cd": 100, "Fr": 10, "ratio": 2}, (0, 0, 0),
     (1, 0, 0), "ratio"),
    ("tc_fp_isolator", {"R": 2, "mu": 0.1}, (0, 0, 0), (1, 0, 1),
     "vertical"),
    ("tc_fp_isolator", {"R": 2, "mu": 0.1, "gap_t": 0.01}, (0, 0, 0),
     (0, 0, 1), "kt"),
])
def test_validation(ltype, prm, pi, pj, msg):
    m = BuildingModel(name="v")
    with pytest.raises(ValueError, match=msg):
        m.add_link(pi, pj, link_type=ltype, params=prm)


def test_types_registered_and_round_trip():
    assert set(lx.EXT_LINK_TYPES) <= set(LINK_TYPES)
    assert "composite" in TH_DAMPING_MODELS
    m = BuildingModel(name="rt")
    m.add_link((0, 0, 0), (2, 0, 1), link_type="plastic_wen", params=WEN)
    m.add_link((0, 0, 0), (2, 0, 2), link_type="brb", params=BRB)
    m.add_link((0, 0, 0), (3, 0, 0), link_type="damper_bilinear", params=BD)
    m.add_link((5, 0, 0), (5, 0, 0.3), link_type="tc_fp_isolator",
               params=dict(TC, kt=1e4))
    d = json.loads(json.dumps(m.to_dict()))
    m2 = BuildingModel.from_dict(d)
    assert [lk.to_dict() for lk in m2.links] == [lk.to_dict()
                                                 for lk in m.links]
    assert "link_freq_props" not in d and "material_damping" not in d


def test_fna_plastic_wen_matches_direct_integration():
    """Stiff ring on 4 vertical Plastic (Wen) bearings (the FNA breadth
    block): FNA (Wen pseudo-force, coupled 2-row) and direct integration
    (uncoupled BoucWen per shear DOF) agree for a unidirectional pulse."""
    from test_fna_breadth import _block, _pulse
    acc, dt = _pulse()
    prm = {"k": 4000.0, "Fy": 22.0, "ratio": 0.1, "exp": 2.0}
    mdl = _block("plastic_wen", prm)
    mdl.add_th_case("TH", "X", acc, dt, damping=1e-9)
    eng = OpenSeesEngine(mdl)
    u_th = np.array(eng.run_time_history("TH").story_ux["Story1"])
    rf = OpenSeesEngine(mdl).run_fna("TH")
    u_f = np.array(rf.story_ux["Story1"])
    pk = np.max(np.abs(u_th))
    assert pk > prm["Fy"] / prm["k"]                      # yielded
    assert abs(np.max(np.abs(u_f)) - pk) <= 0.02 * pk
    assert np.max(np.abs(u_th - u_f)) <= 0.03 * pk
    assert rf.extra["fna"]["devices"] == {"WenComponent": 4}


# =========================================================================
# 6. frequency-dependent link properties
# =========================================================================
M_SD = 10.0
K0 = 4.0e3
ZETA = 0.05


def _link_sdof():
    m = BuildingModel(name="lfd")
    m.rigid_diaphragms = False
    m.num_modes = 1
    m.set_stories([1.0])
    m.supports.append(PointSupport((0, 0, 0), (1,) * 6))
    m.add_link((0, 0, 0), (0, 0, 1.0), stiffness=[K0, 0, 0, 0, 0, 0],
               uid="LK")
    m.nodal_masses.append(NodalMass((0, 0, 1.0), mx=M_SD))
    m.pattern("PX", "other").nodal_loads.append(NodalLoad((0, 0, 1.0),
                                                          fx=1.0))
    add_frequency_function(m, "ONE", "steady_state", [[0.0, 1.0],
                                                      [50.0, 1.0]])
    add_steady_state_case(m, "SS", loads=[FrequencyLoad("PX", 1.0, "ONE")],
                          freq_start_hz=0.5, freq_end_hz=6.0, n_freq=12,
                          modal_refine=False, damping=ZETA)
    return m


TABLE = [[0.0, 3.0e3, 5.0], [2.0, 5.0e3, 20.0], [6.0, 6.0e3, 40.0]]


def _hand(f, table=TABLE):
    w = 2 * math.pi * f
    K = np.interp(f, [r[0] for r in table], [r[1] for r in table])
    C = np.interp(f, [r[0] for r in table], [r[2] for r in table])
    w0 = math.sqrt(K0 / M_SD)
    u = 1.0 / (K - w * w * M_SD + 1j * w * C + 2j * ZETA * w0 * w * M_SD)
    return u, abs(K + 1j * w * C) * abs(u)


def test_freq_dependent_link_steady_state_equals_hand():
    m = _link_sdof()
    m.link_freq_props = {"FD": {"links": ["LK"], "dofs": {"UX": TABLE}}}
    m.validate()
    eng = OpenSeesEngine(m)
    res = fq.run_steady_state(eng, "SS")
    t = str(eng._find_node(eng._asm, (0, 0, 1.0)))
    amp = np.array(res["node_disp"][t]["amp"])[:, 0]
    ph = np.radians(np.array(res["node_disp"][t]["phase_deg"])[:, 0])
    base = np.array(res["base"]["FX"]["amp"])
    for k, f in enumerate(res["frequencies_hz"]):
        u, R = _hand(f)
        z = amp[k] * np.exp(1j * ph[k])
        assert abs(z - u) <= 1e-9 * abs(u)
        assert base[k] == pytest.approx(R, rel=1e-9)
    assert any("frequency-dependent" in w for w in res["warnings"])


def test_freq_dependent_constant_table_reproduces_plain_link():
    m0 = _link_sdof()
    r0 = fq.run_steady_state(OpenSeesEngine(m0), "SS")
    m1 = _link_sdof()
    m1.link_freq_props = {"FD": {"links": ["LK"],
                                 "dofs": {"UX": [[0.0, K0, 0.0]]}}}
    r1 = fq.run_steady_state(OpenSeesEngine(m1), "SS")
    for key in ("node_disp", "base"):
        assert np.allclose(np.array(_flat(r0[key])), np.array(_flat(r1[key])),
                           rtol=1e-10, atol=1e-14)


def _js(o):
    return json.dumps(o, sort_keys=True)


def _flat(o):
    if isinstance(o, dict):
        return [v for k in sorted(o) for v in _flat(o[k])]
    if isinstance(o, list):
        return [v for x in o for v in _flat(x)]
    return [float(o)]


def test_freq_dependent_damper_axial_and_psd_run():
    m = _link_sdof()
    m.add_link((0, 0, 1.0), (2.0, 0, 1.0), link_type="damper",
               params={"cd": 1.0}, uid="DMP")
    m.supports.append(PointSupport((2.0, 0, 1.0), (1,) * 6))
    m.link_freq_props = {"FD": {"links": ["DMP"],
                                "dofs": {"AXIAL": [[0.0, 0.0, 30.0]]}}}
    m.validate()
    eng = OpenSeesEngine(m)
    res = fq.run_steady_state(eng, "SS")
    t = str(eng._find_node(eng._asm, (0, 0, 1.0)))
    amp = np.array(res["node_disp"][t]["amp"])[:, 0]
    w0 = math.sqrt(K0 / M_SD)
    for k, f in enumerate(res["frequencies_hz"]):
        w = 2 * math.pi * f
        u = 1 / (K0 - w * w * M_SD + 1j * w * 30.0 + 2j * ZETA * w0 * w * M_SD)
        assert amp[k] == pytest.approx(abs(u), rel=1e-9)
    add_frequency_function(m, "W", "psd", [[0.0, 1.0], [10.0, 1.0]])
    add_psd_case(m, "PSD", loads=[FrequencyLoad("PX", 1.0, "W")],
                 freq_end_hz=6.0, n_freq=7, modal_refine=False)
    r = fq.run_psd(OpenSeesEngine(m), "PSD")
    assert r["rms"]["base"]["FX"] > 0.0


@pytest.mark.parametrize("props, msg", [
    ({"P": {"links": ["NOPE"], "dofs": {"UX": [[0, 1, 0]]}}}, "unknown"),
    ({"P": {"links": ["LK"], "dofs": {"AXIAL": [[0, 1, 0]]}}}, "AXIAL"),
    ({"P": {"links": ["LK"], "dofs": {"UX": [[1, 1, 0], [0, 1, 0]]}}},
     "increasing"),
    ({"P": {"links": ["LK"], "dofs": {"UX": [[0, 1, -1]]}}}, "C must"),
    ({"P": {"links": ["LK"], "dofs": {"UQ": [[0, 1, 0]]}}}, "DOF"),
    ({"P": {"links": ["LK"], "dofs": {"UX": [[0, 1, 0]]}},
      "Q": {"links": ["LK"], "dofs": {"UY": [[0, 1, 0]]}}}, "already"),
])
def test_freq_dependent_validation(props, msg):
    m = _link_sdof()
    m.link_freq_props = props
    with pytest.raises(ValueError, match=msg):
        m.validate()


def test_freq_dependent_round_trip_prunes_deleted_links():
    m = _link_sdof()
    m.link_freq_props = {"FD": {"links": ["LK"], "dofs": {"UX": TABLE}}}
    d = json.loads(json.dumps(m.to_dict()))
    d2 = BuildingModel.from_dict(d).to_dict()
    assert d2["link_freq_props"] == d["link_freq_props"]
    assert _js(BuildingModel.from_dict(d2).to_dict()) == _js(d2)
    d["links"] = []
    assert BuildingModel.from_dict(d).link_freq_props["FD"]["links"] == []


# =========================================================================
# 7. composite (material-proportional) modal damping
# =========================================================================
L1, L2, SZ = 3.0, 2.0, 0.3
E_A, E_B = 30.0e6, 3.0e6
Z_A, Z_B = 0.05, 0.02


def _stacked():
    m = BuildingModel(name="cmp")
    m.rigid_diaphragms = False
    m.num_modes = 1
    m.add_material(Material("A", E=E_A, nu=0.2))
    m.add_material(Material("B", E=E_B, nu=0.3))
    m.add_section(FrameSection.rectangular("SA", "A", SZ, SZ))
    m.add_section(FrameSection.rectangular("SB", "B", SZ, SZ))
    m.set_stories([L1, L2])
    m.add_member("column", "SA", (0, 0, 0), (0, 0, L1), story="Story1",
                 uid="C1")
    m.add_member("column", "SB", (0, 0, L1), (0, 0, L1 + L2),
                 story="Story2", uid="C2")
    m.supports.append(PointSupport((0, 0, 0), (1,) * 6))
    m.nodal_masses.append(NodalMass((0, 0, L1 + L2), mx=20.0))
    m.material_damping = {"A": Z_A, "B": Z_B}
    return m


def _hand_zeta():
    inertia = SZ ** 4 / 12.0
    H = L1 + L2
    e1 = (H ** 3 - L2 ** 3) / (6.0 * E_A * inertia)
    e2 = L2 ** 3 / (6.0 * E_B * inertia)
    return (Z_A * e1 + Z_B * e2) / (e1 + e2), e1 / (e1 + e2)


def test_composite_zeta_is_strain_energy_weighted():
    z, fa = _hand_zeta()
    info = ecd.composite(_stacked(), 0.05)
    mode = info["modes"][0]
    assert mode["zeta"] == pytest.approx(z, rel=1e-9)
    assert mode["fractions"]["A"] == pytest.approx(fa, rel=1e-9)
    assert mode["energy_check"] == pytest.approx(1.0, rel=1e-9)
    assert cdm.composite_zeta({"A": 3.0, "B": 1.0}, {"A": 0.04, "B": 0.08},
                              0.05) == pytest.approx(0.05)


def _with_th(m, model_kind, zeta=None):
    acc = [2.0 * math.sin(0.4 * i) for i in range(150)]
    m.add_th_case("TH", "X", accel=acc, dt=0.01, damping=0.05,
                  damping_model=model_kind, modal_zeta=zeta)
    return m


def test_composite_th_equals_modal_with_hand_zeta():
    z, _ = _hand_zeta()
    rc = OpenSeesEngine(_with_th(_stacked(), "composite")) \
        .run_time_history("TH")
    rm = OpenSeesEngine(_with_th(_stacked(), "modal", [z])) \
        .run_time_history("TH")
    a, b = np.array(rc.story_ux["Story2"]), np.array(rm.story_ux["Story2"])
    assert np.allclose(a, b, rtol=1e-9, atol=1e-14)
    assert rc.extra["composite_damping"]["modes"][0]["zeta"] == \
        pytest.approx(z, rel=1e-9)
    # FNA path too
    fc = OpenSeesEngine(_with_th(_stacked(), "composite")).run_fna("TH")
    fm = OpenSeesEngine(_with_th(_stacked(), "modal", [z])).run_fna("TH")
    assert np.allclose(fc.story_ux["Story2"], fm.story_ux["Story2"],
                       rtol=1e-9, atol=1e-14)
    assert "composite_damping" in fc.to_dict()


def test_composite_does_not_mutate_the_model_and_round_trips():
    m = _with_th(_stacked(), "composite")
    before = json.dumps(m.to_dict(), sort_keys=True)
    eng = OpenSeesEngine(m)
    eng.run_time_history("TH")
    eng.run_fna("TH")
    assert json.dumps(m.to_dict(), sort_keys=True) == before
    d = json.loads(before)
    assert d["material_damping"] == {"A": Z_A, "B": Z_B}
    assert d["th_cases"]["TH"]["damping_model"] == "composite"
    d2 = BuildingModel.from_dict(d).to_dict()
    assert d2["material_damping"] == d["material_damping"]
    assert d2["th_cases"] == d["th_cases"]


@pytest.mark.parametrize("md, msg", [({"NOPE": 0.05}, "unknown material"),
                                     ({"A": 1.5}, "damping ratio")])
def test_material_damping_validation(md, msg):
    m = _stacked()
    m.material_damping = md
    with pytest.raises(ValueError, match=msg):
        m.validate()


def test_empty_material_damping_is_constant_modal():
    m = _stacked()
    m.material_damping = {}
    info = ecd.composite(m, 0.03)
    assert info["modes"][0]["zeta"] == pytest.approx(0.03)
    assert copy.deepcopy(m).to_dict().get("material_damping") is None


# =========================================================================
# 8. HTTP: composite preview + model round trip through POST /api/model
# =========================================================================
@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.testing = True
    with app.test_client() as client:
        yield client


def test_api_composite_preview_and_round_trip(api_client):
    m = _with_th(_stacked(), "composite")
    m.add_link((0, 0, 5.0), (1, 0, 5.0), link_type="brb",
               params=dict(BRB))
    m.supports.append(PointSupport((1, 0, 5.0), (1,) * 6))
    d = json.loads(json.dumps(m.to_dict()))
    assert api_client.post("/api/model", json=d).status_code == 200
    got = api_client.get("/api/model").get_json()
    for key in ("material_damping", "links", "th_cases"):
        assert got[key] == d[key]
    r = api_client.post("/api/damping/composite",
                        json={"model": _stacked().to_dict()}).get_json()
    z, _ = _hand_zeta()
    assert r["modes"][0]["zeta"] == pytest.approx(z, rel=1e-9)
    r2 = api_client.post("/api/damping/composite",
                         json={"model": _stacked().to_dict(),
                               "material_damping": {"A": 0.1, "B": 0.1}}
                         ).get_json()
    assert r2["modes"][0]["zeta"] == pytest.approx(0.1, rel=1e-12)
    bad = api_client.post("/api/damping/composite",
                          json={"model": _stacked().to_dict(),
                                "material_damping": {"X": 0.1}})
    assert bad.status_code == 400


# =========================================================================
# 9. Interactive Database Editing tables
# =========================================================================
def test_db_edit_tables_round_trip_and_edit():
    from skyframe.core import modeltables as mt
    m = _link_sdof()
    m.link_freq_props = {"FD": {"links": ["LK"], "dofs": {"UX": TABLE}}}
    m.add_material(Material("A", E=3e7, nu=0.2))
    m.material_damping = {"A": 0.04}
    d = json.loads(json.dumps(m.to_dict()))
    keys = ("material_damping", "link_freq_props", "link_freq_points",
            "link_freq_links")
    tabs = {k: mt.get_table(d, k)["rows"] for k in keys}
    assert tabs["material_damping"] == [{"_id": ["A"], "material": "A",
                                         "zeta": 0.04}]
    assert [r["f"] for r in tabs["link_freq_points"]] == [0.0, 2.0, 6.0]
    assert tabs["link_freq_links"][0]["link"] == "LK"
    model, errs, _ = mt.apply_tables(d, tabs)          # unchanged rows
    assert not errs
    assert _js(model.to_dict()) == _js(BuildingModel.from_dict(d).to_dict())
    rows = [dict(r) for r in tabs["link_freq_points"]]
    rows[1]["K"] = 4.5e3
    rows.append({"property": "FD", "dof": "UY", "f": 0.0, "K": 1e3,
                 "C": 0.0})
    md = [{"material": "A", "zeta": 0.07}]
    model, errs, _ = mt.apply_tables(d, {"link_freq_points": rows,
                                         "material_damping": md})
    assert not errs, errs
    assert model.link_freq_props["FD"]["dofs"]["UX"][1] == [2.0, 4.5e3, 20.0]
    assert model.link_freq_props["FD"]["dofs"]["UY"] == [[0.0, 1e3, 0.0]]
    assert model.material_damping == {"A": 0.07}
    model, errs, _ = mt.apply_tables(d, {"material_damping": []})
    assert not errs and "material_damping" not in model.to_dict()
