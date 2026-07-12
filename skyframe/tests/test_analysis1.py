"""ANALYSIS WAVE 1 (v0.24) validation — analysis parity I.

Every assertion is pinned by a hand derivation or cross-validated against
an independent solution path, matching the suite's standing rigor:

1. RITZ VECTORS — a 2-DOF problem solved LONGHAND (explicit K^-1, the
   first WYD vector and both exact eigenvalues by hand algebra); frame
   cross-validation against ``run_modal`` on the SAME models (first Ritz
   period, the total-participation Ritz guarantee, the exact-basis case);
   the rigid-diaphragm constraint transformation validated against
   OpenSees' Transformation handler through the eigen comparison.
2. FNA — a device-free case must equal linear direct integration to
   solver precision (FNA with zero nonlinearity IS modal superposition);
   the wave-16 isolated block against BOTH direct integration and the
   wave-16 hand bilinear Newmark integrator; grounded damper decay vs
   the hand added-damping ratio; gap contact vs direct integration; an
   energy-balance audit of the modal integrator; honest
   NotImplementedError scope.
3. MODAL DAMPING — SDOF log-decrement = zeta to 1%; a 2-mass model with
   different per-mode zetas shows INDEPENDENT modal decay (response
   projected onto the mass-weighted eigenvectors); the default Rayleigh
   path stays bit-identical.
4. EIGEN SOLVER — Arpack and dense-LAPACK eigenvalues agree to 1e-8
   relative; the n == n_massed dense path is quiet on tiny models; the
   fd-redirect helper suppresses native output and restores state.
"""

import math

import numpy as np
import pytest

from skyframe.core.builder import quick_building
from skyframe.core.fna import (BilinearComponent, GapComponent,
                               HookComponent, MaxwellDamper, fna_modal_th)
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 NodalMass, TimeHistoryCase)
from skyframe.core.ritz import RitzResults, ritz_analysis, ritz_vectors
from skyframe.engine.opensees_engine import OpenSeesEngine

ops = pytest.importorskip("openseespy.opensees")


# --------------------------------------------------------------------------- #
# shared model helpers
# --------------------------------------------------------------------------- #
def _cantilever(name, heights=(3.0,), size=0.3, E=2.0e8, num_modes=2):
    """Vertical column through the given story elevations, fixed base."""
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.num_modes = num_modes
    mdl.add_material(Material("S", E=E, nu=0.3))
    mdl.add_section(FrameSection.rectangular("C", "S", size, size))
    mdl.set_stories(list(heights))
    z = 0.0
    for i, h in enumerate(heights):
        mdl.add_member("column", "C", (0, 0, z), (0, 0, z + h),
                       story=f"Story{i + 1}", uid=f"C{i + 1}")
        z += h
    return mdl


H_ISO, B_BLK = 0.3, 4.0
K1, K2, FY_ISO, M_BLK = 2000.0, 200.0, 20.0, 80.0


def _isolated_block(num_modes=8):
    """The wave-16 isolated block: stiff beam ring on 4 bilinear isolators
    (ground ends auto-fix via z_min base fixity), block mass on the ring."""
    mdl = BuildingModel(name="iso")
    mdl.rigid_diaphragms = False
    mdl.num_modes = num_modes
    mdl.add_material(Material("STIFF", E=2e11, nu=0.3))
    mdl.add_section(FrameSection.rectangular("BM", "STIFF", 0.5, 0.5))
    mdl.set_stories([H_ISO])
    pts = [(0, 0), (B_BLK, 0), (B_BLK, B_BLK), (0, B_BLK)]
    for i in range(4):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % 4]
        mdl.add_member("beam", "BM", (x1, y1, H_ISO), (x2, y2, H_ISO),
                       story="Story1", uid=f"B{i + 1}")
    for x, y in pts:
        mdl.add_link((x, y, 0.0), (x, y, H_ISO), link_type="isolator",
                     params={"k1": K1, "k2": K2, "Fy": FY_ISO})
        mdl.nodal_masses.append(NodalMass((x, y, H_ISO),
                                          mx=M_BLK / 4, my=M_BLK / 4))
    return mdl


def _pulse_record(dt=0.005, td=0.5, A=3.0, tail=800):
    n_p = int(td / dt)
    return ([A * math.sin(math.pi * k * dt / td) for k in range(n_p + 1)]
            + [0.0] * tail), dt


def _numpy_bilinear_newmark(accel, dt, m, k_el, k_pl, Fy):
    """The wave-16 independent SDOF bilinear-kinematic Newmark benchmark
    (gamma=1/2, beta=1/4, zero damping) — reproduced verbatim."""
    n = len(accel)
    ag = list(accel) + [0.0]
    H = k_el * k_pl / (k_el - k_pl)
    u = v = a = 0.0
    up = alpha = 0.0
    g_, b_ = 0.5, 0.25
    hist = np.zeros(n)
    for kstep in range(n):
        p1 = -m * ag[kstep + 1]
        u1 = u
        for _ in range(100):
            a1_ = (u1 - u - dt * v - dt * dt * (0.5 - b_) * a) / (b_ * dt * dt)
            f_tr = k_el * (u1 - up)
            xi = f_tr - alpha
            if abs(xi) > Fy:
                dg = (abs(xi) - Fy) / (k_el + H)
                f1 = f_tr - k_el * dg * math.copysign(1.0, xi)
                kt = k_el * H / (k_el + H)
            else:
                f1, kt = f_tr, k_el
            R = m * a1_ + f1 - p1
            if abs(R) < 1e-12:
                break
            u1 -= R / (m / (b_ * dt * dt) + kt)
        a_new = (u1 - u - dt * v - dt * dt * (0.5 - b_) * a) / (b_ * dt * dt)
        v = v + dt * ((1.0 - g_) * a + g_ * a_new)
        a = a_new
        f_tr = k_el * (u1 - up)
        xi = f_tr - alpha
        if abs(xi) > Fy:
            dg = (abs(xi) - Fy) / (k_el + H)
            up += dg * math.copysign(1.0, xi)
            alpha += H * dg * math.copysign(1.0, xi)
        u = u1
        hist[kstep] = u
    return hist


def _log_decrement_zeta(series, start=0):
    """Damping ratio from the mean log decrement of successive positive
    peaks of a free-decay series (small-zeta form delta = 2 pi zeta)."""
    s = np.asarray(series)
    peaks = [s[i] for i in range(max(start, 1), len(s) - 1)
             if s[i] > s[i - 1] and s[i] > s[i + 1] and s[i] > 1e-12]
    assert len(peaks) >= 4, "not enough decay peaks to measure"
    decs = [math.log(peaks[i] / peaks[i + 1]) / (2.0 * math.pi)
            for i in range(min(6, len(peaks) - 1))]
    return float(np.mean(decs))


# =========================================================================== #
# 1. load-dependent Ritz vectors
# =========================================================================== #
def test_ritz_2dof_hand_longhand():
    """2-DOF WYD sequence solved entirely by hand.

    K = [[3000, -1000], [-1000, 1000]], M = diag(4, 2), f = M*1 = [4, 2].

    Longhand: det K = 3000*1000 - 1000^2 = 2e6, so
    K^-1 = [[1000, 1000], [1000, 3000]] / 2e6 and
    x1 = K^-1 f = [(4000 + 2000), (4000 + 6000)] / 2e6 = [0.003, 0.005].
    |x1|_M^2 = 4*0.003^2 + 2*0.005^2 = 8.6e-5 -> u1 = x1 / sqrt(8.6e-5).
    Exact eigenvalues: det(K - lam M) = 0 -> 8 lam^2 - 10000 lam + 2e6
    = 0 -> lam = (1250 +- 750)/2 = {250, 1000}.  Two Ritz vectors span
    the whole 2-space, so the Ritz values ARE the exact eigenvalues.
    """
    K = np.array([[3000.0, -1000.0], [-1000.0, 1000.0]])
    M = np.diag([4.0, 2.0])
    f = M @ np.ones(2)
    theta, Phi, U = ritz_vectors(K, M, f, 2, return_basis=True)

    u1_hand = np.array([0.003, 0.005]) / math.sqrt(8.6e-5)
    assert U[:, 0] == pytest.approx(u1_hand, rel=1e-12)
    assert sorted(theta) == pytest.approx([250.0, 1000.0], rel=1e-12)
    # Ritz vectors are M-orthonormal and K-diagonal (reduced eig exact)
    G = Phi.T @ M @ Phi
    assert G == pytest.approx(np.eye(2), abs=1e-12)
    KP = Phi.T @ K @ Phi
    assert np.diag(KP) == pytest.approx(sorted(theta), rel=1e-12)

    # second hand step: x2 = K^-1 M u1, M-orthogonalized against u1 and
    # normalized — replicate the documented algebra with plain floats
    Kinv = np.array([[1000.0, 1000.0], [1000.0, 3000.0]]) / 2.0e6
    x2 = Kinv @ (M @ u1_hand)
    x2 = x2 - u1_hand * float(u1_hand @ (M @ x2))
    x2 = x2 - u1_hand * float(u1_hand @ (M @ x2))    # second GS pass
    u2_hand = x2 / math.sqrt(float(x2 @ (M @ x2)))
    assert U[:, 1] == pytest.approx(u2_hand, rel=1e-9)


def test_ritz_user_load_vector_entry():
    """ritz_vectors accepts an arbitrary (non mass-proportional) load: a
    unit force on dof 2 only.  One vector = the static shape K^-1 e2
    M-normalized; its Ritz value is the Rayleigh quotient (hand)."""
    K = np.array([[3000.0, -1000.0], [-1000.0, 1000.0]])
    M = np.diag([4.0, 2.0])
    f = np.array([0.0, 1.0])
    theta, Phi = ritz_vectors(K, M, f, 1)
    x = np.linalg.solve(K, f)
    u = x / math.sqrt(float(x @ (M @ x)))
    rq = float(u @ (K @ u))
    assert len(theta) == 1
    assert theta[0] == pytest.approx(rq, rel=1e-12)
    assert np.abs(Phi[:, 0]) == pytest.approx(np.abs(u), rel=1e-12)


def _three_story():
    return quick_building(stories=3, bays_x=1, bays_y=1)


def test_ritz_first_period_matches_eigen_diaphragm():
    """3-story rigid-diaphragm frame: the first X Ritz period equals the
    true fundamental period to far better than the 0.1% spec bound —
    this also pins the numpy rigid-diaphragm T-condensation against
    OpenSees' Transformation constraint handler."""
    mdl = _three_story()
    eng = OpenSeesEngine(mdl)
    modal = eng.run_modal(9)
    res = eng.run_ritz(3, "X")
    assert len(res.periods) == 3
    assert res.periods[0] == pytest.approx(modal.periods[0], rel=1e-3)
    assert res.periods[0] == pytest.approx(modal.periods[0], rel=1e-9)


def test_ritz_participation_guarantee():
    """The Ritz guarantee: n load-dependent vectors capture at least as
    much load-direction mass as n eigenvectors (here they capture ALL of
    it: 3 X-chains on 3 diaphragm-x dofs span the X subspace)."""
    mdl = _three_story()
    eng = OpenSeesEngine(mdl)
    modal = eng.run_modal(9)
    for n in (1, 2, 3):
        ritz = eng.run_ritz(n, "X")
        tot_ritz = sum(p["ux"] for p in ritz.participation)
        tot_eig = sum(p["ux"] for p in modal.participation[:n])
        assert tot_ritz >= tot_eig - 1e-12
        assert tot_ritz <= 1.0 + 1e-9
    # the full 3-vector X basis captures 100% of the X mass
    ritz3 = eng.run_ritz(3, "X")
    assert sum(p["ux"] for p in ritz3.participation) == \
        pytest.approx(1.0, abs=1e-9)


def test_ritz_no_diaphragm_exact_basis_matches_eigen():
    """2-mass cantilever, no diaphragms: 2 X-Ritz vectors span the whole
    massed subspace, so BOTH Ritz periods equal the eigen periods and the
    per-mode participations match."""
    mdl = _cantilever("rz2", heights=(3.0, 3.0), num_modes=2)
    mdl.nodal_masses.append(NodalMass((0, 0, 3.0), mx=8.0))
    mdl.nodal_masses.append(NodalMass((0, 0, 6.0), mx=5.0))
    eng = OpenSeesEngine(mdl)
    modal = eng.run_modal(2)
    res = eng.run_ritz(2, "X")
    assert res.periods == pytest.approx(modal.periods, rel=1e-8)
    for pr, pm in zip(res.participation, modal.participation):
        assert pr["ux"] == pytest.approx(pm["ux"], abs=1e-9)
    # gamma * phi is normalization-invariant: compare |gamma_x| too
    for pr, pm in zip(res.participation, modal.participation):
        assert abs(pr["gamma_x"]) > 0.0
        assert pm["gamma_x"] ** 2 == pytest.approx(pr["gamma_x"] ** 2,
                                                   rel=1e-6)


def test_ritz_direction_y_xy_and_validation():
    mdl = _three_story()
    eng = OpenSeesEngine(mdl)
    ry = eng.run_ritz(3, "Y")
    # the building is symmetric in plan bays but not identical X/Y spans;
    # Y vectors must load the Y direction
    assert sum(p["uy"] for p in ry.participation) == \
        pytest.approx(1.0, abs=1e-9)
    assert sum(p["ux"] for p in ry.participation) < 1e-6
    rxy = eng.run_ritz(6, "XY")
    assert sum(p["ux"] for p in rxy.participation) == \
        pytest.approx(1.0, abs=1e-9)
    assert sum(p["uy"] for p in rxy.participation) == \
        pytest.approx(1.0, abs=1e-9)
    with pytest.raises(ValueError):
        eng.run_ritz(3, "Z")
    with pytest.raises(ValueError):
        ritz_analysis(mdl, 0, "X")


def test_ritz_subspace_exhaustion_warns():
    """Asking for more vectors than the load can reach returns fewer,
    with an explicit warning (2 massed X dofs -> 2 vectors max)."""
    mdl = _cantilever("rzex", heights=(3.0, 3.0), num_modes=2)
    mdl.nodal_masses.append(NodalMass((0, 0, 3.0), mx=8.0))
    mdl.nodal_masses.append(NodalMass((0, 0, 6.0), mx=5.0))
    res = OpenSeesEngine(mdl).run_ritz(5, "X")
    assert len(res.periods) == 2
    assert any("subspace is exhausted" in w for w in res.warnings)


def test_ritz_results_shape_and_link_warning():
    mdl = _three_story()
    mdl.add_link((0, 0, 3.2), (1.5, 0, 3.2), link_type="damper",
                 params={"cd": 10.0}, uid="D1")
    res = OpenSeesEngine(mdl).run_ritz(2, "X")
    assert isinstance(res, RitzResults)
    assert any("link" in w for w in res.warnings)
    d = res.to_dict()
    assert set(d) == {"periods", "frequencies", "participation", "shapes",
                      "direction", "warnings"}
    assert d["direction"] == "X"
    assert len(d["periods"]) == len(d["frequencies"]) == 2
    for entry in d["participation"]:
        assert set(entry) == {"mode", "T", "ux", "gamma_x", "uy",
                              "gamma_y", "rz"}
    assert set(d["shapes"]) == {"1", "2"}
    first = next(iter(d["shapes"]["1"].values()))
    assert len(first) == 6
    for t in d["periods"]:
        assert t > 0.0 and math.isfinite(t)


# =========================================================================== #
# 2. FNA
# =========================================================================== #
def test_fna_linear_equals_direct_th_solver_precision():
    """Device-free 2-mass model, complete 2-mode basis: FNA IS modal
    superposition and must reproduce linear direct integration to solver
    precision — displacements, base shear and inertia story shears."""
    mdl = _cantilever("fnalin", heights=(3.0, 3.0), num_modes=2)
    mdl.nodal_masses.append(NodalMass((0, 0, 3.0), mx=8.0))
    mdl.nodal_masses.append(NodalMass((0, 0, 6.0), mx=5.0))
    accel, dt = _pulse_record(dt=0.01, td=0.5, A=3.0, tail=300)
    mdl.add_th_case("TH", "X", accel, dt, damping=0.05)
    eng = OpenSeesEngine(mdl)
    r_th = eng.run_time_history("TH")
    r_fna = eng.run_fna("TH")
    assert r_fna.t == pytest.approx(r_th.t, abs=1e-12)
    for s in ("Story1", "Story2"):
        u1 = np.array(r_th.story_ux[s])
        u2 = np.array(r_fna.story_ux[s])
        assert np.max(np.abs(u1 - u2)) <= 1e-9 * np.max(np.abs(u1))
    b1 = np.array(r_th.base_FX)
    b2 = np.array(r_fna.base_FX)
    assert np.max(np.abs(b1 - b2)) <= 1e-9 * np.max(np.abs(b1))
    for s in ("Story1", "Story2"):
        assert r_fna.peaks["story"][s]["shear_x"] == \
            pytest.approx(r_th.peaks["story"][s]["shear_x"], rel=1e-9)


def test_fna_isolator_matches_direct_th_and_hand_bilinear():
    """The wave-16 isolated block, half-sine pulse well past yield then
    free decay: FNA vs direct integration AND vs the independent numpy
    bilinear-kinematic Newmark benchmark, both < 2% (spec) — measured
    agreement is ~2e-7 vs direct and the benchmark really yields."""
    accel, dt = _pulse_record()
    mdl = _isolated_block()
    mdl.add_th_case("TH", "X", accel, dt, damping=1e-9)   # hysteretic only
    eng = OpenSeesEngine(mdl)
    u_th = np.array(eng.run_time_history("TH").story_ux["Story1"])
    u_fna = np.array(eng.run_fna("TH").story_ux["Story1"])
    u_hand = _numpy_bilinear_newmark(accel, dt, M_BLK,
                                     4.0 * K1, 4.0 * K2, 4.0 * FY_ISO)
    pk = np.max(np.abs(u_th))
    assert np.max(np.abs(u_hand)) > 5.0 * (FY_ISO / K1)   # really yields
    assert abs(np.max(np.abs(u_fna)) - pk) <= 0.02 * pk
    assert abs(u_fna[-1] - u_th[-1]) <= 0.02 * pk         # residual drift
    assert abs(np.max(np.abs(u_fna)) - np.max(np.abs(u_hand))) <= 0.02 * pk
    # measured margin: direct-integration agreement is ~2e-7 relative
    assert abs(np.max(np.abs(u_fna)) - pk) <= 1e-4 * pk
    assert np.all(np.isfinite(u_fna))
    # base shear peak agrees with the reaction-based direct value
    b_th = eng.run_time_history("TH").peaks["base"]["FX"]
    b_fna = eng.run_fna("TH").peaks["base"]["FX"]
    assert b_fna == pytest.approx(b_th, rel=1e-3)


def test_fna_damper_free_decay_added_damping():
    """Grounded Maxwell damper on an SDOF column (wave-16 layout):
    FNA matches direct integration < 2% at the peak, and the FNA decay's
    log decrement gives the hand added ratio zeta = cd/(2 m wn) to 5%."""
    m_t = 10.0
    mdl = _cantilever("fnadmp")
    mdl.nodal_masses.append(NodalMass((0, 0, 3.0), mx=m_t))
    T1 = OpenSeesEngine(mdl).run_modal(1).periods[0]
    wn = 2.0 * math.pi / T1
    zeta = 0.08
    cd = 2.0 * zeta * m_t * wn
    mdl.add_link((0, 0, 3.0), (1.5, 0, 3.0), link_type="damper",
                 params={"cd": cd})
    dt = T1 / 100.0
    accel = [3.0] * 10 + [0.0] * 3000       # velocity pulse, free decay
    mdl.add_th_case("FV", "X", accel, dt, damping=1e-9)
    eng = OpenSeesEngine(mdl)
    u_th = np.array(eng.run_time_history("FV").story_ux["Story1"])
    u_fna = np.array(eng.run_fna("FV").story_ux["Story1"])
    pk = np.max(np.abs(u_th))
    assert abs(np.max(np.abs(u_fna)) - pk) <= 0.02 * pk
    z_est = _log_decrement_zeta(u_fna, start=20)
    assert z_est == pytest.approx(zeta, rel=0.05)


def test_fna_gap_contact_matches_direct_th():
    """Mass on a column with a compression-only gap stop to a grounded
    anchor: the FNA fixed point resolves the contact to the same history
    as the Newton direct integration (both Newmark avg-acc)."""
    m_t = 10.0
    mdl = _cantilever("fnagap")
    mdl.nodal_masses.append(NodalMass((0, 0, 3.0), mx=m_t))
    kg, gp = 800.0, 0.002
    mdl.add_link((0, 0, 3.0), (1.0, 0, 3.0), link_type="gap",
                 params={"k": kg, "gap": gp})
    dt = 0.004
    accel = ([4.0 * math.sin(math.pi * k / 50.0) for k in range(51)]
             + [0.0] * 1500)
    mdl.add_th_case("G", "X", accel, dt, damping=0.02)
    eng = OpenSeesEngine(mdl)
    u_th = np.array(eng.run_time_history("G").story_ux["Story1"])
    u_fna = np.array(eng.run_fna("G").story_ux["Story1"])
    pk = np.max(np.abs(u_th))
    assert u_th.min() < -(gp + 1e-4)               # the gap really engages
    assert np.max(np.abs(u_th - u_fna)) <= 1e-6 * pk


def test_fna_energy_balance_bilinear_sdof():
    """Modal-integrator energy audit on the SDOF bilinear system of the
    isolated-block benchmark: external work = kinetic + elastic strain +
    correction-force work (hysteretic) + viscous dissipation, closing to
    < 0.1% of the peak input (trapezoidal work quadrature — the Newmark
    average-acceleration rule conserves the discrete energy exactly for
    the linear terms)."""
    m = M_BLK
    k1t, k2t, fyt = 4.0 * K1, 4.0 * K2, 4.0 * FY_ISO
    w = math.sqrt(k1t / m)
    zeta = 0.02
    accel, dt = _pulse_record()
    phi = 1.0 / math.sqrt(m)
    gamma = math.sqrt(m)                       # L = m*phi, unit modal mass
    q, qd, qdd, fdev = fna_modal_th(
        [w], [zeta], [gamma], accel, dt,
        comps=[BilinearComponent(k1t, k2t, fyt)],
        B=np.array([[phi]]), k_lin=[k1t])
    q = q[:, 0]
    qd = qd[:, 0]
    F = fdev[:, 0]
    d = phi * q                                # component deformation
    n = len(q)
    ag_end = np.array([accel[k + 1] if k + 1 < len(accel) else 0.0
                       for k in range(n)])
    # step-wise trapezoidal work sums (state k is the step END)
    q_full = np.concatenate([[0.0], q])
    qd_full = np.concatenate([[0.0], qd])
    d_full = np.concatenate([[0.0], d])
    p_full = np.concatenate([[0.0], -gamma * ag_end])
    Fc_full = np.concatenate([[0.0], F - k1t * d])   # correction force
    dq = np.diff(q_full)
    dd = np.diff(d_full)
    W_ext = np.cumsum(0.5 * (p_full[1:] + p_full[:-1]) * dq)
    W_dev = np.cumsum(0.5 * (Fc_full[1:] + Fc_full[:-1]) * dd)
    E_visc = np.cumsum(2.0 * zeta * w
                       * 0.5 * (qd_full[1:] ** 2 + qd_full[:-1] ** 2) * dt)
    T_kin = 0.5 * qd ** 2
    V_el = 0.5 * w * w * q ** 2
    residual = np.abs(T_kin + V_el + E_visc + W_dev - W_ext)
    scale = np.max(np.abs(W_ext))
    assert scale > 0.0
    assert np.max(residual) <= 1e-3 * scale
    # the hysteretic work really dissipated energy (the system yielded)
    assert W_dev[-1] > 0.05 * scale


def test_fna_device_laws_hand_values():
    """The pure-numpy device laws pinned by hand.

    * gap (k=100, gap=0.01): open/undertravel -> 0; d = -0.02 ->
      F = 100*(-0.02 + 0.01) = -1.0 (compression);
    * hook (k=100, slack=0.01): mirror, d = +0.02 -> +1.0;
    * bilinear (k1=100, k2=10, Fy=1): d = 0.005 -> 0.5 (elastic);
      d = 0.02 -> Fy + k2*(d - Fy/k1) = 1 + 10*0.01 = 1.1; elastic
      UNLOADING from there to d = 0.01 -> 1.1 - 100*0.01 = 0.1;
    * Maxwell damper (k, cd): under CONSTANT velocity the force relaxes
      to the dashpot value cd*v (dF/dt = k(v - F/cd) = 0 at F = cd*v).
    """
    g = GapComponent(100.0, 0.01)
    assert g.trial(-0.005, 0.0, 0.01) == 0.0
    assert g.trial(+0.02, 0.0, 0.01) == 0.0
    assert g.trial(-0.02, 0.0, 0.01) == pytest.approx(-1.0)
    h = HookComponent(100.0, 0.01)
    assert h.trial(+0.005, 0.0, 0.01) == 0.0
    assert h.trial(-0.02, 0.0, 0.01) == 0.0
    assert h.trial(+0.02, 0.0, 0.01) == pytest.approx(+1.0)
    b = BilinearComponent(100.0, 10.0, 1.0)
    assert b.trial(0.005, 0.0, 0.01) == pytest.approx(0.5)
    assert b.trial(0.02, 0.0, 0.01) == pytest.approx(1.1)
    b.commit(0.02, 0.0, 0.01)
    assert b.trial(0.01, 0.0, 0.01) == pytest.approx(0.1)   # elastic unload
    d = MaxwellDamper(k=1000.0, cd=5.0)
    v = 0.3
    dt = 0.001
    for _ in range(200):                       # >> relaxation time cd/k
        d.commit(0.0, v, dt)
    assert d.trial(0.0, v, dt) == pytest.approx(5.0 * 0.3, rel=1e-9)


def test_fna_honest_scope_not_implemented():
    accel, dt = _pulse_record(tail=50)
    # (a) hinged nonlinear TH case
    mdl = _cantilever("fnah")
    mdl.nodal_masses.append(NodalMass((0, 0, 3.0), mx=10.0))
    mdl.add_th_case("NL", "X", accel, dt, nonlinear=True, default_My=50.0)
    with pytest.raises(NotImplementedError, match="run_time_history"):
        OpenSeesEngine(mdl).run_fna("NL")
    # (b) gravity stage
    mdl = _cantilever("fnag")
    mdl.nodal_masses.append(NodalMass((0, 0, 3.0), mx=10.0))
    pat = mdl.pattern("D", "dead")
    mdl.add_th_case("GR", "X", accel, dt, gravity={"D": 1.0})
    with pytest.raises(NotImplementedError, match="run_time_history"):
        OpenSeesEngine(mdl).run_fna("GR")
    # (c) damper alpha != 1
    mdl = _cantilever("fnaa")
    mdl.nodal_masses.append(NodalMass((0, 0, 3.0), mx=10.0))
    mdl.add_link((0, 0, 3.0), (1.5, 0, 3.0), link_type="damper",
                 params={"cd": 20.0, "alpha": 0.5})
    mdl.add_th_case("AL", "X", accel, dt)
    with pytest.raises(NotImplementedError, match="alpha"):
        OpenSeesEngine(mdl).run_fna("AL")
    # (d) friction-pendulum device
    mdl = _cantilever("fnafp")
    mdl.nodal_masses.append(NodalMass((0, 0, 3.0), mx=10.0))
    mdl.add_link((0, 0, 0.0), (0, 0, 0.3), link_type="fp_isolator",
                 params={"R": 2.0, "mu": 0.05})
    mdl.add_th_case("FP", "X", accel, dt)
    with pytest.raises(NotImplementedError, match="run_time_history"):
        OpenSeesEngine(mdl).run_fna("FP")
    # (e) unknown case name
    mdl = _cantilever("fnau")
    with pytest.raises(ValueError, match="Unknown time-history case"):
        OpenSeesEngine(mdl).run_fna("NOPE")


def test_fna_results_shape_and_cache():
    """run_fna returns the exact THResults linear serialised shape and
    caches per engine instance."""
    mdl = _cantilever("fnashape")
    mdl.nodal_masses.append(NodalMass((0, 0, 3.0), mx=10.0))
    accel, dt = _pulse_record(tail=100)
    mdl.add_th_case("TH", "X", accel, dt, damping=0.05)
    eng = OpenSeesEngine(mdl)
    r1 = eng.run_fna("TH")
    assert eng.run_fna("TH") is r1                       # cached
    d_fna = r1.to_dict()
    d_th = eng.run_time_history("TH").to_dict()
    assert set(d_fna) == set(d_th)                       # same JSON shape
    assert set(d_fna) == {"t", "story_ux", "story_uy", "base_FX",
                          "base_FY", "peaks"}
    assert set(d_fna["peaks"]["story"]["Story1"]) == {
        "ux", "uy", "drift_x", "drift_y", "shear_x", "shear_y"}
    assert len(d_fna["t"]) == len(accel)


# =========================================================================== #
# 3. modal damping
# =========================================================================== #
def test_modal_damping_sdof_log_decrement():
    """SDOF free decay under damping_model='modal', zeta = 0.05: the
    log decrement recovers 0.05 to 1% (the flat ``damping`` field is
    deliberately set elsewhere to prove modal_zeta wins)."""
    m_t = 10.0
    mdl = _cantilever("mdsdof")
    mdl.nodal_masses.append(NodalMass((0, 0, 3.0), mx=m_t))
    T1 = OpenSeesEngine(mdl).run_modal(1).periods[0]
    dt = T1 / 100.0
    mdl.add_th_case("MD", "X", [3.0] * 10 + [0.0] * 4000, dt,
                    damping=0.30, damping_model="modal", modal_zeta=[0.05])
    res = OpenSeesEngine(mdl).run_time_history("MD")
    z_est = _log_decrement_zeta(res.story_ux["Story1"], start=20)
    assert abs(z_est - 0.05) <= 0.01 * 0.05


def test_modal_damping_two_modes_decay_independently():
    """2-mass column with modal_zeta=[0.02, 0.10]: projecting the free
    decay onto the mass-weighted eigenvectors isolates q1(t) and q2(t);
    each mode's log decrement matches ITS OWN zeta — impossible under any
    single Rayleigh pair fitted to both frequencies."""
    m1, m2 = 8.0, 5.0
    mdl = _cantilever("md2", heights=(3.0, 3.0), num_modes=2, size=0.35)
    mdl.nodal_masses.append(NodalMass((0, 0, 3.0), mx=m1))
    mdl.nodal_masses.append(NodalMass((0, 0, 6.0), mx=m2))
    eng0 = OpenSeesEngine(mdl)
    modal = eng0.run_modal(2)
    T2 = modal.periods[1]
    dt = T2 / 40.0
    zetas = [0.02, 0.10]
    mdl.add_th_case("MD2", "X", [4.0] * 6 + [0.0] * 6000, dt,
                    damping=0.30, damping_model="modal", modal_zeta=zetas)
    eng = OpenSeesEngine(mdl)
    res = eng.run_time_history("MD2")
    u1 = np.array(res.story_ux["Story1"])    # node at z = 3 (single node)
    u2 = np.array(res.story_ux["Story2"])    # node at z = 6

    # mass-weighted modal projection q_i = phi_i^T M u / (phi_i^T M phi_i)
    n1 = eng._find_node(eng._asm, (0, 0, 3.0))
    n2 = eng._find_node(eng._asm, (0, 0, 6.0))
    qs = []
    for i in (1, 2):
        p1, p2 = modal.shapes[i][n1][0], modal.shapes[i][n2][0]
        den = m1 * p1 * p1 + m2 * p2 * p2
        qs.append((m1 * p1 * u1 + m2 * p2 * u2) / den)
    # signed series: positive peaks only (|q| would halve the decrement)
    z1 = _log_decrement_zeta(qs[0], start=20)
    z2 = _log_decrement_zeta(qs[1], start=5)
    assert z1 == pytest.approx(zetas[0], rel=0.10)
    assert z2 == pytest.approx(zetas[1], rel=0.10)
    assert z1 < 0.04 < 0.07 < z2             # unmistakably independent


def test_fna_modal_damping_matches_direct_modal_damping():
    """FNA consumes modal_zeta natively; on a linear SDOF it reproduces
    the ops.modalDamping direct-integration run to solver precision."""
    mdl = _cantilever("fnamd")
    mdl.nodal_masses.append(NodalMass((0, 0, 3.0), mx=10.0))
    accel, dt = _pulse_record(dt=0.01, tail=400)
    mdl.add_th_case("MD", "X", accel, dt, damping=0.30,
                    damping_model="modal", modal_zeta=[0.05])
    eng = OpenSeesEngine(mdl)
    u_th = np.array(eng.run_time_history("MD").story_ux["Story1"])
    u_fna = np.array(eng.run_fna("MD").story_ux["Story1"])
    assert np.max(np.abs(u_th - u_fna)) <= 1e-9 * np.max(np.abs(u_th))


def test_rayleigh_default_path_bit_identical():
    """A case built without the v0.24 fields and one built with the
    explicit defaults produce BIT-IDENTICAL time histories."""
    accel, dt = _pulse_record(dt=0.01, tail=200)

    def build(**kw):
        mdl = _cantilever("ray")
        mdl.nodal_masses.append(NodalMass((0, 0, 3.0), mx=10.0))
        mdl.add_th_case("TH", "X", accel, dt, damping=0.05, **kw)
        return OpenSeesEngine(mdl).run_time_history("TH")

    r_old = build()
    r_new = build(damping_model="rayleigh", modal_zeta=None)
    assert r_old.story_ux["Story1"] == r_new.story_ux["Story1"]
    assert r_old.base_FX == r_new.base_FX


def test_th_case_modal_fields_validation_and_roundtrip():
    mdl = _cantilever("val")
    accel = [0.1, 0.2]
    th = mdl.add_th_case("A", "X", accel, 0.01, damping_model="modal",
                         modal_zeta=[0.02, 0.05])
    d = th.to_dict()
    assert d["damping_model"] == "modal"
    assert d["modal_zeta"] == [0.02, 0.05]
    # default case serialises the new fields with defaults
    th2 = mdl.add_th_case("B", "X", accel, 0.01)
    assert th2.to_dict()["damping_model"] == "rayleigh"
    assert th2.to_dict()["modal_zeta"] is None
    # model-level round trip
    mdl2 = BuildingModel.from_dict(mdl.to_dict())
    assert mdl2.th_cases["A"].damping_model == "modal"
    assert mdl2.th_cases["A"].modal_zeta == [0.02, 0.05]
    assert mdl2.th_cases["B"].damping_model == "rayleigh"
    assert mdl2.th_cases["B"].modal_zeta is None
    # pre-v0.24 file (keys absent) loads with the Rayleigh defaults
    legacy = mdl.to_dict()
    for td in legacy["th_cases"].values():
        td.pop("damping_model", None)
        td.pop("modal_zeta", None)
    mdl3 = BuildingModel.from_dict(legacy)
    assert mdl3.th_cases["A"].damping_model == "rayleigh"
    assert mdl3.th_cases["A"].modal_zeta is None
    # validation errors
    with pytest.raises(ValueError, match="damping_model"):
        mdl.add_th_case("C", "X", accel, 0.01, damping_model="caughey")
    with pytest.raises(ValueError, match="modal_zeta"):
        mdl.add_th_case("D", "X", accel, 0.01, damping_model="modal",
                        modal_zeta=[1.5])
    with pytest.raises(ValueError, match="modal_zeta"):
        mdl.add_th_case("E", "X", accel, 0.01, modal_zeta=[0.05, 0.0])


def test_padded_zetas_rule():
    th = TimeHistoryCase("T", "X", [0.1], 0.01, damping=0.04,
                         modal_zeta=[0.02, 0.05])
    assert OpenSeesEngine._padded_zetas(th, 4) == [0.02, 0.05, 0.05, 0.05]
    assert OpenSeesEngine._padded_zetas(th, 1) == [0.02]
    th2 = TimeHistoryCase("T", "X", [0.1], 0.01, damping=0.04)
    assert OpenSeesEngine._padded_zetas(th2, 3) == [0.04, 0.04, 0.04]


# =========================================================================== #
# 4. eigen solver policy
# =========================================================================== #
def test_eigen_arpack_matches_dense_lapack():
    """n < n_massed (Arpack path): the engine's eigenvalues equal the
    dense -fullGenLapack values to 1e-8 relative on the same domain."""
    mdl = quick_building(stories=6, bays_x=1, bays_y=1)   # 18 massed dofs
    eng = OpenSeesEngine(mdl)
    modal = eng.run_modal(12)
    lam_engine = [(2.0 * math.pi / T) ** 2 for T in modal.periods]
    asm = eng._build()
    eng._setup_analysis(asm)
    lam_dense = ops.eigen("-fullGenLapack", 12)
    for le, ld in zip(lam_engine, lam_dense):
        assert le == pytest.approx(ld, rel=1e-8)


def test_eigen_n_equals_massed_is_dense_and_quiet(capfd):
    """n == n_massed is an ARPACK rank bound (Arnoldi space = rank(M)),
    so the dense solver is REQUIRED — and on a tiny model its native
    'VERY SLOW' console warning is suppressed.  Results are the usual
    positive ascending eigenvalues."""
    mdl = quick_building(stories=4)          # num_modes 12 == 4 x 3 dofs
    eng = OpenSeesEngine(mdl)
    capfd.readouterr()                       # clear
    modal = eng.run_modal()
    out = capfd.readouterr()
    assert "VERY SLOW" not in out.out + out.err
    assert len(modal.periods) == 12
    assert all(t > 0 for t in modal.periods)
    assert modal.periods == sorted(modal.periods, reverse=True)


def test_eigen_dense_values_unchanged_by_policy():
    """The quiet dense path returns the same eigenvalues as a plain
    -fullGenLapack call on the same freshly built domain."""
    mdl = quick_building(stories=4)
    eng = OpenSeesEngine(mdl)
    modal = eng.run_modal()                  # quiet dense path (n == massed)
    lam_engine = [(2.0 * math.pi / T) ** 2 for T in modal.periods]
    asm = eng._build()
    eng._setup_analysis(asm)
    lam_dense = ops.eigen("-fullGenLapack", 12)
    for le, ld in zip(lam_engine, lam_dense):
        assert le == pytest.approx(ld, rel=1e-10)


def test_quiet_native_output_suppresses_and_restores():
    import os as _os
    from skyframe.engine.opensees_engine import _quiet_native_output
    with _quiet_native_output():
        _os.write(1, b"SHOULD-NOT-APPEAR-OUT\n")
        _os.write(2, b"SHOULD-NOT-APPEAR-ERR\n")
    # fds restored: writing works again (would raise on a closed fd)
    assert _os.write(1, b"") == 0
    assert _os.write(2, b"") == 0
    # exceptions propagate and still restore the fds
    with pytest.raises(RuntimeError, match="boom"):
        with _quiet_native_output():
            raise RuntimeError("boom")
    assert _os.write(1, b"") == 0


def test_quiet_output_not_captured(capfd):
    from skyframe.engine.opensees_engine import _quiet_native_output
    capfd.readouterr()
    with _quiet_native_output():
        import os as _os
        _os.write(1, b"HIDDEN-1")
        _os.write(2, b"HIDDEN-2")
    out = capfd.readouterr()
    assert "HIDDEN-1" not in out.out + out.err
    assert "HIDDEN-2" not in out.out + out.err


# =========================================================================== #
# 5. API round trips
# =========================================================================== #
@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_api_ritz_endpoint(api_client):
    mdl = _three_story()
    assert api_client.post("/api/model", json=mdl.to_dict()).status_code \
        == 200
    resp = api_client.post("/api/analyze/ritz",
                           json={"n": 3, "direction": "X"})
    assert resp.status_code == 200
    d = resp.get_json()
    assert len(d["periods"]) == 3
    assert d["direction"] == "X"
    assert d["participation"][0]["ux"] > 0.5
    # bad parameters -> 400
    assert api_client.post("/api/analyze/ritz",
                           json={"direction": "Z"}).status_code == 400
    assert api_client.post("/api/analyze/ritz",
                           json={"n": 0}).status_code == 400


def test_api_fna_endpoint_and_modal_damping_roundtrip(api_client):
    mdl = _cantilever("apifna")
    mdl.nodal_masses.append(NodalMass((0, 0, 3.0), mx=10.0))
    accel, dt = _pulse_record(dt=0.01, tail=100)
    mdl.add_th_case("TH", "X", accel, dt, damping=0.05,
                    damping_model="modal", modal_zeta=[0.05])
    resp = api_client.post("/api/model", json=mdl.to_dict())
    assert resp.status_code == 200
    echoed = resp.get_json()["th_cases"]["TH"]
    assert echoed["damping_model"] == "modal"
    assert echoed["modal_zeta"] == [0.05]

    resp = api_client.post("/api/analyze/fna", json={"case": "TH"})
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["case"] == "TH" and d["method"] == "FNA"
    assert set(d) >= {"t", "story_ux", "story_uy", "base_FX", "base_FY",
                      "peaks"}
    assert len(d["t"]) == len(accel)

    # unknown case and missing body -> 400
    assert api_client.post("/api/analyze/fna",
                           json={"case": "NOPE"}).status_code == 400
    assert api_client.post("/api/analyze/fna", json={}).status_code == 400

    # unsupported feature -> 400 naming direct integration
    bad = _cantilever("apibad")
    bad.nodal_masses.append(NodalMass((0, 0, 3.0), mx=10.0))
    bad.add_th_case("NL", "X", accel, dt, nonlinear=True, default_My=50.0)
    assert api_client.post("/api/model", json=bad.to_dict()).status_code \
        == 200
    resp = api_client.post("/api/analyze/fna", json={"case": "NL"})
    assert resp.status_code == 400
    assert "run_time_history" in resp.get_json()["error"]

    # bad damping_model rejected at the model layer -> 400
    broken = mdl.to_dict()
    broken["th_cases"]["TH"]["damping_model"] = "caughey"
    assert api_client.post("/api/model", json=broken).status_code == 400
