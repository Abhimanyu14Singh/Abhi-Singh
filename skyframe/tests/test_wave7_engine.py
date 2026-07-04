"""Wave 7 ENGINE tests (v0.6): staged construction + nonlinear time history.

Same rigor as test_phase5/test_wave6: every expected number is derived in
the test from first principles.

Staged construction (rebuild-and-accumulate, CONTRACT v0.6):
  * 2-story single-column stack (loads P1 at level 1, P2 at level 2): the
    per-stage flexibility problems are assembled IN THE TEST with numpy
    (stage 1: k = EA/L single DOF; stage 2: the 2x2 axial stiffness) and
    the engine's accumulated displacements must match at 1e-9.  Axials are
    statically determinate, so the staged lower-column axial equals the
    one-shot P1 + P2; but the staged TOP displacement differs from the
    one-shot by EXACTLY P1*L/EA (the "slab built level" effect: node 2 is
    born at stage 2 and never feels stage 1's shortening).
  * staged base FZ = sum of applied gravity at 1e-9;
  * a 2-story 2-column frame with stiff beams (statically indeterminate):
    staged vs one-shot beam end moments DIFFER (joint rotational restraint
    changes between the 1-story and 2-story configurations), while every
    accumulated member force set satisfies exact per-member equilibrium
    (sum of end forces + span load = 0, moments included);
  * include_live is applied at the END, unstaged, on the full structure:
    closed-form axial-stack check.

Nonlinear time history (v0.5 pushover hinge machinery reused):
  (a) nonlinear=True with My far above demand reproduces the LINEAR TH
      trace at 1e-6 of the peak: the portal's base restraints leave only
      ry free, so the base hinge spring carries identically zero moment
      and the hinged model is elastically EXACT (same-model-path sanity);
  (b) elastoplastic SDOF benchmark: cantilever column + tip mass + base
      hinge with low My under a half-sine pulse sized to yield.  Reference:
      an independent numpy elastoplastic Newmark integrator (bilinear
      KINEMATIC restoring force with the wave-6 closed-form hinge-series
      parameters k_el = 1/(L^3/3EI + L^2/k_theta), F_y = My/L,
      k_pl = 1/(L^3/3EI + L^2/(b*k_theta)); same Rayleigh damping: a0/a1
      fitted at the INITIAL elastic mode, applied as
      c = a0*m + a1*k_committed-tangent, mirroring the engine's betaKcomm).
      Peak and residual displacement within 2%; 'yielded' contains the
      member; hinge rotation far beyond My/k_theta.
  (c) energy sanity on the same run: no NaNs, response bounded, and the
      velocity at the end of the 16 s free-decay tail is ~0.

Units per CONTRACT.md: kN, m, tonne, s; E in kPa.
"""

import math

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine
HINGE_N = engine_mod.HINGE_STIFFNESS_FACTOR
TH_STEP_CAP = engine_mod.TH_STEP_CAP

from skyframe.core.model import (
    BuildingModel,
    FrameSection,
    Material,
    MemberLoad,
    NodalLoad,
    NodalMass,
    PointSupport,
)

E_CONC = 25_000_000.0  # kPa


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _run(model) -> dict:
    return OpenSeesEngine(model).run().to_dict()


def _find_node(results: dict, pt, tol=1e-6):
    for tag, xyz in results["nodes"].items():
        if all(abs(a - b) < tol for a, b in zip(xyz, pt)):
            return tag
    raise AssertionError(f"no FE node found at {pt}")


def _new_model(name: str, story_heights) -> BuildingModel:
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.set_stories(list(story_heights))
    return mdl


# --------------------------------------------------------------------------- #
# 1. staged construction — closed forms
# --------------------------------------------------------------------------- #
def _column_stack(P1, P2, L=3.0, size=0.3):
    """Two-story single-column stack: P1 at level 1, P2 at level 2."""
    mdl = _new_model("stack", [L, L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L),
                   story="Story1", uid="C1")
    mdl.add_member("column", "COL", (0, 0, L), (0, 0, 2 * L),
                   story="Story2", uid="C2")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    pat = mdl.pattern("DEAD", "dead")
    pat.nodal_loads.append(NodalLoad((0, 0, L), fz=-P1))
    pat.nodal_loads.append(NodalLoad((0, 0, 2 * L), fz=-P2))
    mdl.num_modes = 0
    return mdl


def test_staged_column_stack_matches_hand_accumulation():
    """Engine accumulation == numpy hand accumulation at 1e-9; staged top
    displacement differs from one-shot by exactly P1*L/EA; axials (and the
    reported comparison pct) are one-shot identical; base FZ = P1 + P2."""
    P1, P2, L, size = 100.0, 60.0, 3.0, 0.3
    A = size * size
    EA = E_CONC * A
    k = EA / L

    mdl = _column_stack(P1, P2, L, size)
    mdl.add_staged_case("STG", pattern="DEAD")
    eng = OpenSeesEngine(mdl)
    sr = eng.run_staged("STG")
    d = eng.run().to_dict()
    st = d["staged"]["STG"]
    n1 = _find_node(d, (0, 0, L))
    n2 = _find_node(d, (0, 0, 2 * L))

    # hand accumulation, assembled per stage with numpy:
    # stage 1: story-1 column only, load P1 at node 1
    u1_stage1 = np.linalg.solve(np.array([[k]]), np.array([-P1]))[0]
    assert u1_stage1 == pytest.approx(-P1 * L / EA, rel=1e-12)
    # stage 2: both stories, ONLY P2 applied; node 2 born undisplaced
    K2 = k * np.array([[2.0, -1.0], [-1.0, 1.0]])
    d2 = np.linalg.solve(K2, np.array([0.0, -P2]))
    u1_hand = u1_stage1 + d2[0]
    u2_hand = d2[1]
    assert u1_hand == pytest.approx(-(P1 + P2) * L / EA, rel=1e-12)
    assert u2_hand == pytest.approx(-2.0 * P2 * L / EA, rel=1e-12)

    assert st["node_disp"][n1][2] == pytest.approx(u1_hand, rel=1e-9)
    assert st["node_disp"][n2][2] == pytest.approx(u2_hand, rel=1e-9)

    # one-shot top displacement and the exact "slab built level" gap
    one = st["comparison"]["oneshot_case"]
    u2_one = -(P1 + 2.0 * P2) * L / EA
    assert one["node_disp"][n2][2] == pytest.approx(u2_one, rel=1e-9)
    assert (st["node_disp"][n2][2] - one["node_disp"][n2][2]
            ) == pytest.approx(P1 * L / EA, rel=1e-9)

    # axials are statically determinate: staged == one-shot everywhere
    for res in (st, one):
        assert res["member_stations"]["C1"]["N"] == pytest.approx(
            [-(P1 + P2)] * 11, rel=1e-9)
        assert res["member_stations"]["C2"]["N"] == pytest.approx(
            [-P2] * 11, rel=1e-9)
    assert st["comparison"]["column_axial_max_diff_pct"] == pytest.approx(
        0.0, abs=1e-9)

    # equilibrium: staged base FZ = sum of applied gravity
    assert st["base"]["FZ"] == pytest.approx(P1 + P2, rel=1e-9)
    assert one["base"]["FZ"] == pytest.approx(P1 + P2, rel=1e-9)

    # run_staged is cached and returns the same object through run()
    assert eng.run_staged("STG") is sr


def test_staged_frame_equilibrium_and_indeterminate_moments():
    """2-story 2-column frame with stiff beams: staged base FZ exact,
    accumulated forces satisfy per-member equilibrium at 1e-9, and the
    level-1 beam end moments genuinely DIFFER from the one-shot solve
    (the upper columns restrain the joints only in the one-shot model)."""
    L, B = 3.0, 6.0
    w1, w2 = 20.0, 15.0
    mdl = _new_model("frame2", [L, L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.3, 0.3))
    mdl.add_section(FrameSection.rectangular("BEAM", "CONC", 0.4, 1.0))
    for i, x in enumerate((0.0, B)):
        mdl.add_member("column", "COL", (x, 0, 0), (x, 0, L),
                       story="Story1", uid=f"C{i + 1}A")
        mdl.add_member("column", "COL", (x, 0, L), (x, 0, 2 * L),
                       story="Story2", uid=f"C{i + 1}B")
    mdl.add_member("beam", "BEAM", (0, 0, L), (B, 0, L),
                   story="Story1", uid="B1")
    mdl.add_member("beam", "BEAM", (0, 0, 2 * L), (B, 0, 2 * L),
                   story="Story2", uid="B2")
    pat = mdl.pattern("DEAD", "dead")
    pat.member_loads.append(MemberLoad("B1", kind="udl", w=w1))
    pat.member_loads.append(MemberLoad("B2", kind="udl", w=w2))
    mdl.add_staged_case("STG", pattern="DEAD")
    mdl.num_modes = 0

    sr = OpenSeesEngine(mdl).run_staged("STG")
    st, one = sr.case, sr.oneshot

    # global equilibrium of the accumulated state
    assert st.base["FZ"] == pytest.approx((w1 + w2) * B, rel=1e-9)

    # per-member equilibrium of the ACCUMULATED forces (local end-force
    # vectors are forces ON the element; gravity UDL w acts along -local y
    # for a horizontal beam, so sum(Vy) = wL and the moment balance about
    # end i closes with the wL^2/2 span-load moment)
    for uid, w in (("B1", w1), ("B2", w2)):
        f = st.member_forces[uid]
        ref = w * B * B  # kN*m scale
        assert abs(f[0] + f[6]) <= 1e-9 * w * B            # axial
        assert abs(f[1] + f[7] - w * B) <= 1e-9 * w * B    # shear
        assert abs(f[5] + f[11] + B * f[7] - w * B * B / 2.0) <= 1e-9 * ref
    for uid in ("C1A", "C2A", "C1B", "C2B"):
        f = st.member_forces[uid]
        assert abs(f[0] + f[6]) <= 1e-9 * (w1 + w2) * B    # no span load

    # statically indeterminate: staged and one-shot beam END MOMENTS differ
    dM1 = abs(st.member_forces["B1"][5] - one.member_forces["B1"][5])
    assert dM1 > 0.10 * abs(one.member_forces["B1"][5])    # clearly nonzero
    # level-2 beam differs too: in the staged history it never feels w1
    dM2 = abs(st.member_forces["B2"][5] - one.member_forces["B2"][5])
    assert dM2 > 0.10 * abs(one.member_forces["B2"][5])
    # ...while the (determinate, symmetric) column axials still agree
    assert sr.column_axial_max_diff_pct < 1e-6


def test_staged_include_live_applied_unstaged_at_end():
    """include_live loads land on the FULL structure in one increment:
    closed-form axial stack with LIVE = Q at the top, factor 0.5."""
    P1, P2, Q, L, size = 100.0, 60.0, 40.0, 3.0, 0.3
    EA = E_CONC * size * size
    mdl = _column_stack(P1, P2, L, size)
    mdl.pattern("LIVE", "live").nodal_loads.append(
        NodalLoad((0, 0, 2 * L), fz=-Q))
    mdl.add_staged_case("STG", pattern="DEAD", include_live={"LIVE": 0.5})
    d = _run(mdl)
    st = d["staged"]["STG"]
    n1 = _find_node(d, (0, 0, L))
    n2 = _find_node(d, (0, 0, 2 * L))

    # staged dead accumulation + 0.5*Q one-shot on the full stack
    u1 = -(P1 + P2) * L / EA - 0.5 * Q * L / EA
    u2 = -2.0 * P2 * L / EA - 0.5 * Q * 2.0 * L / EA
    assert st["node_disp"][n1][2] == pytest.approx(u1, rel=1e-9)
    assert st["node_disp"][n2][2] == pytest.approx(u2, rel=1e-9)
    assert st["base"]["FZ"] == pytest.approx(P1 + P2 + 0.5 * Q, rel=1e-9)

    # the one-shot comparison carries the same TOTAL loads
    one = st["comparison"]["oneshot_case"]
    assert one["base"]["FZ"] == pytest.approx(P1 + P2 + 0.5 * Q, rel=1e-9)
    u2_one = -((P1 + P2 + 0.5 * Q) + (P2 + 0.5 * Q)) * L / EA
    assert one["node_disp"][n2][2] == pytest.approx(u2_one, rel=1e-9)
    # axials stay determinate-identical with live included
    assert st["comparison"]["column_axial_max_diff_pct"] < 1e-9


def test_staged_validation_and_roundtrip():
    mdl = _column_stack(10.0, 10.0)
    mdl.pattern("LIVE", "live")
    with pytest.raises(ValueError, match="unknown pattern"):
        mdl.add_staged_case("B1", pattern="NOPE")
    with pytest.raises(ValueError, match="stages"):
        mdl.add_staged_case("B2", pattern="DEAD", stages="all_at_once")
    with pytest.raises(ValueError, match="include_live"):
        mdl.add_staged_case("B3", pattern="DEAD",
                            include_live={"NOPE": 1.0})
    sc = mdl.add_staged_case("STG", pattern="DEAD",
                             include_live={"LIVE": 0.25})
    assert (sc.name, sc.pattern, sc.stages) == ("STG", "DEAD", "per_story")

    # staged cases cannot enter load combos
    mdl.add_case("D", {"DEAD": 1.0})
    with pytest.raises(ValueError, match="staged case"):
        mdl.add_combo("BAD", {"STG": 1.0})

    # serialisation round-trip preserves every field
    d1 = mdl.to_dict()
    back = BuildingModel.from_dict(d1)
    sc2 = back.staged_cases["STG"]
    assert (sc2.pattern, sc2.stages, sc2.include_live) == \
        ("DEAD", "per_story", {"LIVE": 0.25})
    assert back.to_dict() == d1
    # pre-v0.6 dicts have no staged_cases key
    d1.pop("staged_cases")
    assert BuildingModel.from_dict(d1).staged_cases == {}

    with pytest.raises(ValueError, match="Unknown staged"):
        OpenSeesEngine(mdl).run_staged("NOPE")

    # run() carries the staged block with the documented comparison shape
    d = _run(mdl)
    st = d["staged"]["STG"]
    for key in ("node_disp", "reactions", "base", "member_forces",
                "story", "member_stations", "comparison"):
        assert key in st
    comp = st["comparison"]
    assert set(comp) == {"column_axial_max_diff_pct", "oneshot_case"}
    assert "base" in comp["oneshot_case"]


# --------------------------------------------------------------------------- #
# 2. nonlinear time history
# --------------------------------------------------------------------------- #
def test_nlth_elastic_high_My_reproduces_linear_trace():
    """(a) nonlinear=True with My far above demand == linear TH at 1e-6 of
    the peak.  Base restraints (1,1,1,1,0,1) leave only ry free, so the
    column-base hinge spring carries identically zero moment: the hinged
    model is elastically EXACT and the whole transient pipeline (Newmark,
    Rayleigh, ground motion, recording) must line up."""
    L, B, dt = 3.0, 6.0, 0.01
    accel = [2.0 * math.sin(2 * math.pi * k * dt / 0.5)
             for k in range(100)] + [0.0] * 100

    def portal():
        mdl = BuildingModel(name="portal")
        mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
        mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.3, 0.3))
        mdl.add_section(FrameSection.rectangular("BEAM", "CONC", 0.3, 0.5))
        mdl.set_stories([L])
        mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L),
                       story="Story1", uid="C1")
        mdl.add_member("column", "COL", (B, 0, 0), (B, 0, L),
                       story="Story1", uid="C2")
        mdl.add_member("beam", "BEAM", (0, 0, L), (B, 0, L),
                       story="Story1", uid="B1")
        for x in (0.0, B):
            mdl.supports.append(
                PointSupport((x, 0, 0), (1, 1, 1, 1, 0, 1)))
        mdl.story_masses = {"Story1": 20.0}
        mdl.num_modes = 3
        return mdl

    m_lin = portal()
    m_lin.add_th_case("THL", "X", accel, dt, damping=0.05)
    r_lin = OpenSeesEngine(m_lin).run_time_history("THL")

    m_nl = portal()
    m_nl.add_th_case("THN", "X", accel, dt, damping=0.05, nonlinear=True,
                     hinges="column_base", My={"C1": 1e9, "C2": 1e9})
    r_nl = OpenSeesEngine(m_nl).run_time_history("THN")

    ul = np.array(r_lin.story_ux["Story1"])
    un = np.array(r_nl.story_ux["Story1"])
    pk = np.max(np.abs(ul))
    assert pk > 1e-4                                   # a real response
    assert np.max(np.abs(ul - un)) <= 1e-6 * pk
    bl = np.array(r_lin.base_FX)
    bn = np.array(r_nl.base_FX)
    assert np.max(np.abs(bl - bn)) <= 1e-6 * np.max(np.abs(bl))
    # nothing yielded; both hinges report (zero) rotations
    assert r_nl.yielded == []
    assert set(r_nl.hinge_rotations) == {"C1", "C2"}
    assert max(abs(v) for v in r_nl.hinge_rotations.values()) <= 1e-12
    # nonlinear results carry the v0.6 keys, linear results do not
    dn, dl = r_nl.to_dict(), r_lin.to_dict()
    assert "yielded" in dn and "hinge_rotations" in dn
    assert "yielded" not in dl and "hinge_rotations" not in dl


def _numpy_elastoplastic_newmark(accel, dt, m, k_el, k_pl, Fy, a0, a1):
    """Independent SDOF bilinear-kinematic Newmark (gamma=1/2, beta=1/4).

    Restoring force: bilinear with elastic k_el, yield Fy, post-yield k_pl
    (kinematic hardening — the exact quasi-static tip force-displacement
    law of the column + hinge series).  Damping mirrors the engine's
    betaKcomm Rayleigh: c = a0*m + a1*k_tangent(committed state).
    Returns the displacement history sampled like the engine (entry k =
    state at t=(k+1)*dt).
    """
    n = len(accel)
    ag = list(accel) + [0.0]
    H = k_el * k_pl / (k_el - k_pl)     # kinematic hardening modulus
    u = v = a = 0.0
    up = 0.0                            # plastic displacement
    alpha = 0.0                         # back force
    g, b = 0.5, 0.25
    u_hist = np.zeros(n)
    for kstep in range(n):
        p1 = -m * ag[kstep + 1]
        f_c = k_el * (u - up)           # committed force state
        kt_c = (k_el if abs(f_c - alpha) < Fy * (1.0 - 1e-12)
                else k_el * H / (k_el + H))
        c = a0 * m + a1 * kt_c
        u1 = u
        for _ in range(80):             # Newton on u_{n+1}
            a1_ = (u1 - u - dt * v
                   - dt * dt * (0.5 - b) * a) / (b * dt * dt)
            v1 = v + dt * ((1.0 - g) * a + g * a1_)
            f_tr = k_el * (u1 - up)
            xi = f_tr - alpha
            if abs(xi) > Fy:
                dg = (abs(xi) - Fy) / (k_el + H)
                f1 = f_tr - k_el * dg * math.copysign(1.0, xi)
                kt = k_el * H / (k_el + H)
            else:
                f1 = f_tr
                kt = k_el
            R = m * a1_ + c * v1 + f1 - p1
            if abs(R) < 1e-12:
                break
            u1 -= R / (m / (b * dt * dt) + c * g / (b * dt) + kt)
        a_new = (u1 - u - dt * v
                 - dt * dt * (0.5 - b) * a) / (b * dt * dt)
        v = v + dt * ((1.0 - g) * a + g * a_new)
        a = a_new
        f_tr = k_el * (u1 - up)
        xi = f_tr - alpha
        if abs(xi) > Fy:                # commit the plastic state
            dg = (abs(xi) - Fy) / (k_el + H)
            up += dg * math.copysign(1.0, xi)
            alpha += H * dg * math.copysign(1.0, xi)
        u = u1
        u_hist[kstep] = u
    return u_hist


@pytest.fixture(scope="module")
def sdof_benchmark():
    """Elastoplastic SDOF: column + tip mass + low-My base hinge under a
    half-sine pulse sized well past yield, then a 16 s free-decay tail."""
    L, size, m_t, My, h, zeta = 3.0, 0.3, 10.0, 40.0, 0.02, 0.02
    I = size ** 4 / 12.0
    EI = E_CONC * I
    kth = HINGE_N * 6.0 * EI / L
    b = h / (HINGE_N + 1.0 - h * HINGE_N)
    k_el = 1.0 / (L ** 3 / (3.0 * EI) + L ** 2 / kth)
    k_pl = 1.0 / (L ** 3 / (3.0 * EI) + L ** 2 / (b * kth))
    Fy = My / L

    # Rayleigh fit at the INITIAL ELASTIC mode (no hinge), like the engine
    w_ray = math.sqrt(3.0 * EI / L ** 3 / m_t)
    a0, a1 = zeta * w_ray, zeta / w_ray

    dt, td, A = 0.002, 0.25, 4.0            # m*A = 40 kN >> Fy = 13.3 kN
    n_pulse = int(round(td / dt))
    accel = [A * math.sin(math.pi * k * dt / td)
             for k in range(n_pulse + 1)] + [0.0] * 8000

    mdl = _new_model("sdof", [L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L),
                   story="Story1", uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=m_t))
    mdl.pattern("G", "other").nodal_loads.append(
        NodalLoad((0, 0, L), fz=-50.0))      # exercises the gravity stage
    mdl.add_th_case("THN", "X", accel, dt, damping=zeta, nonlinear=True,
                    gravity={"G": 1.0}, hinges="column_base",
                    My={"C1": My}, hardening=h)
    mdl.num_modes = 1

    res = OpenSeesEngine(mdl).run_time_history("THN")
    u_engine = np.array(res.story_ux["Story1"])
    u_numpy = _numpy_elastoplastic_newmark(accel, dt, m_t, k_el, k_pl, Fy,
                                           a0, a1)
    return {"res": res, "u_engine": u_engine, "u_numpy": u_numpy,
            "dt": dt, "My": My, "kth": kth, "Fy": Fy, "k_el": k_el}


def test_nlth_sdof_elastoplastic_benchmark(sdof_benchmark):
    """(b) peak + residual displacement vs the independent numpy
    elastoplastic integrator within 2%; the member yields."""
    bm = sdof_benchmark
    u_e, u_n = bm["u_engine"], bm["u_numpy"]
    res = bm["res"]
    pk_e, pk_n = np.max(np.abs(u_e)), np.max(np.abs(u_n))
    u_y = bm["Fy"] / bm["k_el"]

    assert pk_n > 5.0 * u_y                  # the benchmark really yields
    assert abs(pk_e - pk_n) <= 0.02 * pk_n                 # peak, 2%
    assert abs(u_e[-1]) > 5.0 * u_y                        # real residual
    assert abs(u_e[-1] - u_n[-1]) <= 0.02 * pk_n           # residual, 2%

    # yielding is reported: uid listed, hinge rotation far past yield
    assert res.yielded == ["C1"]
    rot_yield = bm["My"] / bm["kth"]
    assert res.hinge_rotations["C1"] > 10.0 * rot_yield
    d = res.to_dict()
    assert d["yielded"] == ["C1"]
    assert d["hinge_rotations"]["C1"] == pytest.approx(
        res.hinge_rotations["C1"])


def test_nlth_energy_sanity_free_decay(sdof_benchmark):
    """(c) no NaNs, bounded response, velocity ~0 after the decay tail."""
    bm = sdof_benchmark
    u_e, dt = bm["u_engine"], bm["dt"]
    res = bm["res"]

    assert np.all(np.isfinite(u_e))
    assert all(math.isfinite(v) for v in res.base_FX)
    assert np.max(np.abs(u_e)) < 2.0 * np.max(np.abs(bm["u_numpy"]))

    vel = np.diff(u_e) / dt
    v_pk = np.max(np.abs(vel))
    assert v_pk > 0.0
    assert abs(vel[-1]) < 0.01 * v_pk        # final velocity ~ 0
    # settled on the residual displacement
    assert abs(u_e[-1] - u_e[-500]) < 0.01 * np.max(np.abs(u_e))


def test_nlth_validation_roundtrip_and_run_cap():
    L = 3.0
    mdl = _new_model("val", [L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.3, 0.3))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L),
                   story="Story1", uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=5.0))
    mdl.pattern("G", "other").nodal_loads.append(
        NodalLoad((0, 0, L), fz=-10.0))
    mdl.num_modes = 1

    # validation of the v0.6 fields (mirrors the pushover rules)
    with pytest.raises(ValueError, match="hinges"):
        mdl.add_th_case("B1", "X", [0.1], 0.01, nonlinear=True,
                        hinges="bases", My={"C1": 10.0})
    with pytest.raises(ValueError, match="hardening"):
        mdl.add_th_case("B2", "X", [0.1], 0.01, nonlinear=True,
                        My={"C1": 10.0}, hardening=1.0)
    with pytest.raises(ValueError, match="unknown member"):
        mdl.add_th_case("B3", "X", [0.1], 0.01, nonlinear=True,
                        My={"NOPE": 10.0})
    with pytest.raises(ValueError, match="must be a finite value > 0"):
        mdl.add_th_case("B4", "X", [0.1], 0.01, nonlinear=True,
                        My={"C1": -5.0})
    with pytest.raises(ValueError, match="default_My"):
        mdl.add_th_case("B5", "X", [0.1], 0.01, nonlinear=True,
                        default_My=0.0)
    with pytest.raises(ValueError, match="unknown pattern"):
        mdl.add_th_case("B6", "X", [0.1], 0.01, nonlinear=True,
                        gravity={"NOPE": 1.0}, My={"C1": 10.0})

    # round-trip preserves every v0.6 field
    mdl.add_th_case("THN", "Y", [0.0, 0.5, -0.5], 0.02, damping=0.03,
                    scale=1.5, nonlinear=True, gravity={"G": 1.2},
                    hinges="all_ends", My={"C1": 77.0}, default_My=55.0,
                    hardening=0.05)
    back = BuildingModel.from_dict(mdl.to_dict())
    th = back.th_cases["THN"]
    assert (th.nonlinear, th.hinges, th.default_My, th.hardening) == \
        (True, "all_ends", 55.0, 0.05)
    assert th.gravity == {"G": 1.2} and th.My == {"C1": 77.0}
    assert back.to_dict() == mdl.to_dict()
    # pre-v0.6 dicts (no nonlinear keys) load as plain linear cases
    d = mdl.to_dict()
    for key in ("nonlinear", "gravity", "hinges", "My", "default_My",
                "hardening"):
        d["th_cases"]["THN"].pop(key)
    th_old = BuildingModel.from_dict(d).th_cases["THN"]
    assert th_old.nonlinear is False and th_old.My == {}
    assert th_old.gravity == {} and th_old.default_My is None

    # run() caps COMBINED (linear + nonlinear) TH steps exactly like v0.4
    mdl2 = _new_model("cap", [L])
    mdl2.add_section(FrameSection.rectangular("COL", "CONC", 0.3, 0.3))
    mdl2.add_member("column", "COL", (0, 0, 0), (0, 0, L),
                    story="Story1", uid="C1")
    mdl2.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl2.nodal_masses.append(NodalMass((0, 0, L), mx=5.0))
    mdl2.num_modes = 1
    mdl2.add_th_case("LIN", "X", [0.0] * (TH_STEP_CAP // 2 + 1), 0.01)
    mdl2.add_th_case("NL", "X", [0.0] * (TH_STEP_CAP // 2 + 1), 0.01,
                     nonlinear=True, My={"C1": 100.0})
    d2 = _run(mdl2)
    assert d2["th_cases"] == {}
    assert "warning" in d2 and str(TH_STEP_CAP) in d2["warning"]
