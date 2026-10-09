"""Direct-integration TH options + energy output (ETABS parity).

Every expected number is derived in the test from first principles:

* SDOF = cantilever column whose top node is restrained in every dof but
  ux (so the structure IS one dof: k = 12EI/L^3, read back from the
  engine's own modal period) + tip mass.  Each integrator is checked
  against an independent numpy implementation of the same scheme
  (Newmark / HHT-alpha / Wilson-theta / central difference) AND against
  the scheme's exact spectral properties (eigenvalues of its
  amplification matrix): Newmark average acceleration has NO amplitude
  decay and the period elongation ``w_bar dt = 2 atan(w dt / 2)``; HHT
  alpha = -0.1 has the numerical damping of its amplification matrix.
* Rayleigh-by-periods coefficients reproduce xi1/xi2 exactly at T1/T2,
  and the engine's free-vibration log decrement equals xi at T1.
* Energy: input == kinetic + strain at every step for an undamped
  elastic SDOF (Newmark average acceleration is exactly energy
  conserving); damping energy == integral of c v^2 dt; modal damping
  energy too; nonlinear hinge -> hysteretic energy and balance.

Units per CONTRACT.md: kN, m, tonne, s; E in kPa.
"""

import json
import math

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core import di_options as dio  # noqa: E402
from skyframe.core.model import (  # noqa: E402
    BuildingModel,
    FrameSection,
    Material,
    NodalLoad,
    NodalMass,
    PointSupport,
)

E_CONC = 25_000_000.0
M_SDOF = 10.0
NO_DAMP = {"type": "rayleigh_coefficients", "mass_coeff": 0.0,
           "stiffness_coeff": 0.0}


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
def _pulse(dt, t_pulse=0.05, amp=2.0, t_total=3.0):
    n_p = int(round(t_pulse / dt))
    n = int(round(t_total / dt))
    acc = [0.0] + [amp * math.sin(math.pi * k / n_p) for k in range(1, n_p)]
    return acc + [0.0] * (n - len(acc))


def _sdof(dt=0.01, accel=None, **th_kw):
    L = 3.0
    mdl = BuildingModel(name="sdof")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.set_stories([L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.3, 0.3))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L),
                   story="Story1", uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.supports.append(PointSupport((0, 0, L), (0, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=M_SDOF))
    mdl.num_modes = 1
    mdl.add_th_case("TH", "X", accel if accel is not None else _pulse(dt),
                    dt, **th_kw)
    return mdl


def _frame(**th_kw):
    """2-story 1x1-bay 3D frame, rigid diaphragms (Transformation)."""
    L, B = 3.0, 6.0
    mdl = BuildingModel(name="frame")
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))
    mdl.add_section(FrameSection.rectangular("BEAM", "CONC", 0.3, 0.5))
    mdl.set_stories([L, L])
    for x in (0.0, B):
        for y in (0.0, B):
            for s in range(2):
                mdl.add_member("column", "COL", (x, y, s * L),
                               (x, y, (s + 1) * L), story=f"Story{s + 1}")
            mdl.supports.append(PointSupport((x, y, 0), (1,) * 6))
    for s in (1, 2):
        z = s * L
        for a, b in (((0, 0), (B, 0)), ((0, B), (B, B)),
                     ((0, 0), (0, B)), ((B, 0), (B, B))):
            mdl.add_member("beam", "BEAM", (*a, z), (*b, z),
                           story=f"Story{s}")
    mdl.story_masses = {"Story1": 30.0, "Story2": 20.0}
    mdl.num_modes = 6
    dt = 0.01
    acc = [0.0] + [3.0 * math.sin(2 * math.pi * k * dt / 0.4)
                   for k in range(1, 80)] + [0.0] * 220
    mdl.add_th_case("TH", "X", acc, dt, **th_kw)
    return mdl


def _elastoplastic(**th_kw):
    """Column + tip mass + low-My base hinge (yields), with gravity."""
    L, m_t = 3.0, 10.0
    dt, td, A = 0.002, 0.25, 4.0
    n_p = int(round(td / dt))
    acc = [A * math.sin(math.pi * k * dt / td)
           for k in range(n_p + 1)] + [0.0] * 1000
    mdl = BuildingModel(name="ep")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.set_stories([L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.3, 0.3))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L),
                   story="Story1", uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=m_t))
    mdl.pattern("G", "other").nodal_loads.append(
        NodalLoad((0, 0, L), fz=-50.0))
    th_kw.setdefault("My", {"C1": 40.0})
    mdl.add_th_case("TH", "X", acc, dt, damping=0.02, nonlinear=True,
                    gravity={"G": 1.0}, hinges="column_base",
                    hardening=0.02, **th_kw)
    mdl.num_modes = 1
    return mdl


def _run(mdl, name="TH"):
    return OpenSeesEngine(mdl).run_time_history(name)


def _ux(res):
    return np.array(res.story_ux["Story1"])


@pytest.fixture(scope="module")
def sdof_k():
    """SDOF stiffness from the engine's own modal period: k = m w^2."""
    T = OpenSeesEngine(_sdof()).run_modal().periods[0]
    w = 2.0 * math.pi / T
    return {"T": T, "w": w, "k": M_SDOF * w * w}


# --------------------------------------------------------------------------- #
# independent numpy SDOF integrators (zero initial conditions, ag(0) = 0)
# --------------------------------------------------------------------------- #
def _path(acc, dt, t):
    n = len(acc)
    x = t / dt
    k = int(math.floor(x + 1e-9))
    if k >= n - 1:
        return acc[n - 1] if abs(x - (n - 1)) < 1e-9 else 0.0
    r = x - k
    return acc[k] + r * (acc[k + 1] - acc[k])


def _ref(method, acc, dt, m, k, c, **p):
    """Displacement/velocity at t = (n+1) dt (engine sampling)."""
    n = len(acc)
    P = lambda t: -m * _path(acc, dt, t)   # noqa: E731
    u = v = a = 0.0
    us, vs = np.zeros(n), np.zeros(n)
    if method == "central_difference":
        u_prev = 0.0
        for i in range(n):
            t = i * dt
            lhs = m / dt ** 2 + c / (2 * dt)
            rhs = (P(t) - k * u + m / dt ** 2 * (2 * u - u_prev)
                   + c / (2 * dt) * u_prev)
            u_new = rhs / lhs
            vs[i] = (u_new - u) / dt
            u_prev, u = u, u_new
            us[i] = u
        return us, vs
    if method == "wilson":
        th = p.get("theta", 1.4)
        tau = th * dt
        for i in range(n):
            pt = P(i * dt + tau)
            # linear acceleration over tau: a_t = A u_t + B, v_t = C u_t + D
            A_, B_ = 6 / tau ** 2, -6 / tau ** 2 * u - 6 / tau * v - 2 * a
            C_, D_ = 3 / tau, -3 / tau * u - 2 * v - tau / 2 * a
            ut = (pt - m * B_ - c * D_) / (m * A_ + c * C_ + k)
            at = A_ * ut + B_
            a1 = a + (at - a) / th
            v1 = v + dt / 2 * (a + a1)
            u1 = u + dt * v + dt ** 2 / 6 * (2 * a + a1)
            u, v, a = u1, v1, a1
            us[i], vs[i] = u, v
        return us, vs
    # Newmark family / HHT-alpha (alpha = 0 -> Newmark)
    al = p.get("alpha", 0.0)
    g, b = p["gamma"], p["beta"]
    for i in range(n):
        t1 = (i + 1) * dt
        p_al = P(t1 + al * dt)              # == (1+al) p_n+1 - al p_n
        A_, B_ = 1 / (b * dt ** 2), -(u + dt * v + dt ** 2 * (0.5 - b) * a) \
            / (b * dt ** 2)
        C_, D_ = g * dt * A_, v + dt * ((1 - g) * a + g * B_)
        lhs = m * A_ + (1 + al) * (c * C_ + k)
        rhs = p_al - m * B_ - (1 + al) * c * D_ + al * (c * v + k * u)
        u1 = rhs / lhs
        a1, v1 = A_ * u1 + B_, C_ * u1 + D_
        u, v, a = u1, v1, a1
        us[i], vs[i] = u, v
    return us, vs


def _amplification_eigs(method, w, dt, **p):
    """Exact eigenvalues of the free-vibration amplification matrix
    (unit mass, no damping), built by stepping the numpy scheme on the
    unit states (u, v, a)."""
    m, k = 1.0, w * w
    if method == "wilson":
        th = p.get("theta", 1.4)
        tau = th * dt

        def step(u, v, a):
            A_, B_ = 6 / tau ** 2, -6 / tau ** 2 * u - 6 / tau * v - 2 * a
            ut = -m * B_ / (m * A_ + k)
            at = A_ * ut + B_
            a1 = a + (at - a) / th
            return (u + dt * v + dt ** 2 / 6 * (2 * a + a1),
                    v + dt / 2 * (a + a1), a1)
    else:
        al = p.get("alpha", 0.0)
        g, b = p["gamma"], p["beta"]

        def step(u, v, a):
            A_ = 1 / (b * dt ** 2)
            B_ = -(u + dt * v + dt ** 2 * (0.5 - b) * a) / (b * dt ** 2)
            C_, D_ = g * dt * A_, v + dt * ((1 - g) * a + g * B_)
            u1 = (-m * B_ + al * k * u) / (m * A_ + (1 + al) * k)
            return u1, C_ * u1 + D_, A_ * u1 + B_
    Amat = np.array([step(*e) for e in np.eye(3)]).T
    lam = np.linalg.eigvals(Amat)
    return lam[np.argmax(np.abs(lam.imag))]      # the oscillatory root


def _fit_recurrence(u):
    """Least-squares u_{n+2} = p1 u_{n+1} - p2 u_n -> (|lambda|, arg)."""
    X = np.column_stack([u[1:-1], -u[:-2]])
    p1, p2 = np.linalg.lstsq(X, u[2:], rcond=None)[0]
    r = math.sqrt(p2)
    return r, math.acos(p1 / (2.0 * r))


# --------------------------------------------------------------------------- #
# 1. defaults: legacy path, bit-identical
# --------------------------------------------------------------------------- #
def test_defaults_leave_model_dict_unchanged():
    th = _sdof().th_cases["TH"]
    d = th.to_dict()
    for key in ("integration", "di_damping", "solver", "energy"):
        assert key not in d
    assert th.integration is None and th.di_damping is None
    assert th.solver is None and th.energy is False
    assert not dio.has_options(th)


def test_defaults_results_bit_identical_to_explicit_defaults():
    """The options path with the DEFAULT values (Newmark 1/2, 1/4; solver
    defaults) reproduces the legacy run exactly — every float equal."""
    r_legacy = _run(_sdof(dt=0.005, damping=0.05))
    r_opts = _run(_sdof(dt=0.005, damping=0.05,
                        integration={"method": "newmark", "gamma": 0.5,
                                     "beta": 0.25},
                        solver={"max_iterations": 25, "tolerance": 1e-8}))
    d0, d1 = r_legacy.to_dict(), r_opts.to_dict()
    assert "direct_integration" not in d0 and "energy" not in d0
    d1.pop("direct_integration")
    assert json.dumps(d0, sort_keys=True) == json.dumps(d1, sort_keys=True)


def test_energy_flag_does_not_change_response_linear_and_nonlinear():
    for build in (_frame, _elastoplastic):
        r0, r1 = _run(build()), _run(build(energy=True))
        d0, d1 = r0.to_dict(), r1.to_dict()
        assert "energy" in d1 and "energy" not in d0
        for k in ("direct_integration", "energy"):
            d1.pop(k)
        assert json.dumps(d0, sort_keys=True) == json.dumps(d1,
                                                           sort_keys=True)


def test_model_roundtrip_with_options():
    opts = dict(integration={"method": "hht", "alpha": -0.1},
                di_damping={"type": "rayleigh_by_periods", "T1": 1.0,
                            "xi1": 0.05, "T2": 0.2, "xi2": 0.04,
                            "stiffness": "initial"},
                solver={"max_iterations": 10, "tolerance": 1e-7,
                        "max_halvings": 3},
                energy=True)
    mdl = _sdof(**opts)
    d = mdl.to_dict()
    td = d["th_cases"]["TH"]
    for k, v in opts.items():
        assert td[k] == v
    d2 = BuildingModel.from_dict(json.loads(json.dumps(d))).to_dict()
    assert d2 == d
    # a pre-options file (keys absent) loads with the legacy defaults
    for k in opts:
        td.pop(k)
    th = BuildingModel.from_dict(d).th_cases["TH"]
    assert not dio.has_options(th)


@pytest.mark.parametrize("kw, msg", [
    ({"integration": {"method": "rk4"}}, "integration method"),
    ({"integration": {"method": "hht", "alpha": -0.5}}, "alpha"),
    ({"integration": {"method": "hht", "alpha": 0.1}}, "alpha"),
    ({"integration": {"method": "wilson", "theta": 2.5}}, "theta"),
    ({"integration": {"method": "newmark", "beta": 0.0}}, "beta"),
    ({"integration": {"method": "newmark", "gamma": 0.4}}, "gamma"),
    ({"di_damping": {"type": "rayleigh_by_periods", "T1": 1.0, "xi1": 0.05,
                     "T2": 1.0, "xi2": 0.05}}, "differ"),
    ({"di_damping": {"type": "rayleigh_by_periods", "T1": 1.0, "xi1": 0.01,
                     "T2": 0.1, "xi2": 0.5}}, "negative"),
    ({"di_damping": {"type": "rayleigh_coefficients", "mass_coeff": -1.0}},
     ">= 0"),
    ({"di_damping": {"type": "rayleigh_coefficients",
                     "stiffness": "tangent"}}, "stiffness"),
    ({"di_damping": {"type": "constant"}}, "di_damping type"),
    ({"di_damping": NO_DAMP, "damping_model": "modal"}, "modal"),
    ({"solver": {"max_iterations": 0}}, "max_iterations"),
    ({"solver": {"tolerance": -1.0}}, "tolerance"),
    ({"solver": {"max_halvings": 99}}, "max_halvings"),
    ({"solver": {"bogus": 1}}, "unknown"),
    ({"energy": "yes"}, "energy"),
])
def test_validation_rejects_bad_options(kw, msg):
    with pytest.raises(ValueError, match=msg):
        _sdof(**kw)


# --------------------------------------------------------------------------- #
# 2. integrators vs exact discrete solutions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("integ, ref_kw", [
    ({"method": "newmark"}, {"gamma": 0.5, "beta": 0.25}),
    ({"method": "newmark", "gamma": 0.5, "beta": 1 / 6},
     {"gamma": 0.5, "beta": 1 / 6}),
    ({"method": "hht", "alpha": -0.1},
     {"alpha": -0.1, "gamma": 0.6, "beta": 1.1 ** 2 / 4}),
    ({"method": "hht", "alpha": -1 / 3},
     {"alpha": -1 / 3, "gamma": 0.5 + 1 / 3, "beta": (4 / 3) ** 2 / 4}),
    ({"method": "wilson", "theta": 1.4}, {"theta": 1.4}),
    ({"method": "central_difference"}, {}),
])
def test_integrator_matches_numpy_reference(sdof_k, integ, ref_kw):
    """Engine trace == independent numpy implementation of the SAME
    scheme (undamped SDOF, pulse + free vibration) to 1e-7 of the peak."""
    dt = 0.01
    acc = _pulse(dt)
    res = _run(_sdof(dt=dt, accel=acc, integration=integ,
                     di_damping=NO_DAMP))
    method = "hht" if integ["method"] == "hht" else integ["method"]
    if method == "hht":
        method = "newmark"
    u_ref, _ = _ref(method, acc, dt, M_SDOF, sdof_k["k"], 0.0, **ref_kw)
    u = _ux(res)
    pk = np.max(np.abs(u_ref))
    assert pk > 1e-5
    assert np.max(np.abs(u - u_ref)) <= 1e-7 * pk


def test_newmark_average_acceleration_no_decay_and_period_elongation(sdof_k):
    """Free vibration: |lambda| == 1 (no amplitude decay) and the
    discrete frequency w_bar dt = 2 atan(w dt / 2) (period elongation)."""
    dt = 0.01
    res = _run(_sdof(dt=dt, integration={"method": "newmark"},
                     di_damping=NO_DAMP))
    u = _ux(res)[10:]                        # free vibration after the pulse
    r, arg = _fit_recurrence(u)
    w = sdof_k["w"]
    assert abs(r - 1.0) < 1e-9
    assert arg == pytest.approx(2.0 * math.atan(w * dt / 2.0), rel=1e-7)
    T_bar = 2 * math.pi * dt / arg
    elong = T_bar / sdof_k["T"] - 1.0
    assert elong == pytest.approx((w * dt / 2) / math.atan(w * dt / 2) - 1,
                                  rel=1e-5)
    assert elong > 0.005                      # measurable at this dt
    # peak amplitude of every free-vibration cycle identical (no decay)
    seg = len(u) // 3
    a1, a3 = np.max(np.abs(u[:seg])), np.max(np.abs(u[-seg:]))
    assert abs(a3 / a1 - 1.0) < 2e-3


def test_hht_numerical_damping_matches_amplification_matrix(sdof_k):
    """HHT alpha = -0.1: measured spectral radius / frequency of the free
    vibration == eigenvalues of the HHT amplification matrix; the
    numerical damping ratio is > 0 and well below 1 %."""
    dt = 0.01
    res = _run(_sdof(dt=dt, integration={"method": "hht", "alpha": -0.1},
                     di_damping=NO_DAMP))
    u = _ux(res)[40:]                        # spurious root died out
    r, arg = _fit_recurrence(u)
    lam = _amplification_eigs("hht", sdof_k["w"], dt, alpha=-0.1,
                              gamma=0.6, beta=1.1 ** 2 / 4)
    assert r == pytest.approx(abs(lam), rel=1e-6)
    assert arg == pytest.approx(abs(np.angle(lam)), rel=1e-6)
    xi_num = -math.log(r) / arg
    assert 1e-4 < xi_num < 0.01
    # the exact amplitude decay over N steps: |lambda|^N
    lam_nm = _amplification_eigs("newmark", sdof_k["w"], dt, gamma=0.5,
                                 beta=0.25)
    assert abs(abs(lam_nm) - 1.0) < 1e-12        # Newmark: no decay
    assert abs(lam) < 1.0                         # HHT: decays


def test_wilson_free_vibration_matches_amplification_matrix(sdof_k):
    dt = 0.01
    res = _run(_sdof(dt=dt, integration={"method": "wilson", "theta": 1.4},
                     di_damping=NO_DAMP))
    r, arg = _fit_recurrence(_ux(res)[40:])
    lam = _amplification_eigs("wilson", sdof_k["w"], dt, theta=1.4)
    assert r == pytest.approx(abs(lam), rel=1e-6)
    assert arg == pytest.approx(abs(np.angle(lam)), rel=1e-6)
    assert r < 1.0


def test_hht_alpha_zero_equals_newmark():
    r_h = _run(_sdof(integration={"method": "hht", "alpha": 0.0}))
    r_n = _run(_sdof())
    assert np.max(np.abs(_ux(r_h) - _ux(r_n))) <= 1e-12 * np.max(
        np.abs(_ux(r_n)))


def test_central_difference_stability_limit(sdof_k):
    """dt >= T_min/pi raises with the computed limit; just below runs."""
    lim = sdof_k["T"] / math.pi
    assert dio.stability_limit({"method": "central_difference",
                                "gamma": 0.5, "beta": 0.0},
                               sdof_k["T"]) == pytest.approx(lim)
    with pytest.raises(ValueError, match=r"T_min/pi = ") as ei:
        _run(_sdof(dt=1.01 * lim, integration={"method":
                                               "central_difference"}))
    assert f"{lim:.6g}" in str(ei.value)
    res = _run(_sdof(dt=0.99 * lim, accel=[0.0, 1.0, 0.0] + [0.0] * 200,
                     integration={"method": "central_difference"}))
    assert res.extra["direct_integration"]["dt_limit"] == pytest.approx(
        lim, rel=1e-9)
    assert res.extra["direct_integration"]["T_min"] == pytest.approx(
        sdof_k["T"], rel=1e-9)
    assert np.all(np.isfinite(_ux(res)))


def test_conditionally_stable_newmark_limit(sdof_k):
    """Linear acceleration (gamma 1/2, beta 1/6): Omega_crit = sqrt(12),
    dt_crit = T sqrt(12) / (2 pi)."""
    lim = sdof_k["T"] * math.sqrt(12.0) / (2 * math.pi)
    with pytest.raises(ValueError, match="conditionally stable"):
        _run(_sdof(dt=1.02 * lim,
                   integration={"method": "newmark", "gamma": 0.5,
                                "beta": 1 / 6}))


def test_central_difference_massless_dofs_rejected():
    with pytest.raises(ValueError, match="massless"):
        _run(_frame(integration={"method": "central_difference"}))


# --------------------------------------------------------------------------- #
# 3. damping options
# --------------------------------------------------------------------------- #
def test_rayleigh_by_periods_exact_ratios():
    for (T1, x1, T2, x2) in ((1.0, 0.05, 0.2, 0.05), (2.0, 0.02, 0.3, 0.05),
                             (0.5, 0.03, 0.05, 0.08)):
        a0, a1 = dio.rayleigh_from_periods(T1, x1, T2, x2)
        assert dio.rayleigh_ratio(a0, a1, T1) == pytest.approx(x1, rel=1e-12)
        assert dio.rayleigh_ratio(a0, a1, T2) == pytest.approx(x2, rel=1e-12)
    # equal ratios -> the classic a0 = 2 xi w1 w2/(w1+w2), a1 = 2 xi/(w1+w2)
    w1, w2 = 2 * math.pi, 2 * math.pi / 0.2
    a0, a1 = dio.rayleigh_from_periods(1.0, 0.05, 0.2, 0.05)
    assert a0 == pytest.approx(2 * 0.05 * w1 * w2 / (w1 + w2), rel=1e-12)
    assert a1 == pytest.approx(2 * 0.05 / (w1 + w2), rel=1e-12)


def test_rayleigh_by_periods_engine_decay(sdof_k):
    """Fit xi1 = 4 % at the SDOF period: the engine reports ratio 0.04 at
    the mode and the free-vibration log decrement gives xi = 4 %."""
    T = sdof_k["T"]
    res = _run(_sdof(dt=0.002, accel=_pulse(0.002, t_total=1.5),
                     di_damping={"type": "rayleigh_by_periods", "T1": T,
                                 "xi1": 0.04, "T2": T / 5, "xi2": 0.06}))
    dd = res.extra["direct_integration"]["damping"]
    assert dd["ratio_at_modes"][0] == pytest.approx(0.04, rel=1e-9)
    r, arg = _fit_recurrence(_ux(res)[30:])
    xi = -math.log(r) / arg
    assert xi == pytest.approx(0.04, rel=0.01)      # dt/T ~ 1 %


def test_rayleigh_coefficients_and_stiffness_bases(sdof_k):
    """Linear model: initial == current == committed stiffness basis, and
    the trace equals the numpy reference with c = a0 m + a1 k."""
    a0, a1 = 0.8, 0.002
    dt = 0.005
    acc = _pulse(dt, t_total=1.0)
    traces = []
    for basis in dio.STIFFNESS_BASES:
        res = _run(_sdof(dt=dt, accel=acc, di_damping={
            "type": "rayleigh_coefficients", "mass_coeff": a0,
            "stiffness_coeff": a1, "stiffness": basis}))
        traces.append(_ux(res))
    u_ref, _ = _ref("newmark", acc, dt, M_SDOF, sdof_k["k"],
                    a0 * M_SDOF + a1 * sdof_k["k"], gamma=0.5, beta=0.25)
    pk = np.max(np.abs(u_ref))
    for u in traces:
        assert np.max(np.abs(u - u_ref)) <= 1e-8 * pk
    assert dio.rayleigh_ops_args(a0, a1, "initial") == (a0, 0.0, a1, 0.0)
    assert dio.rayleigh_ops_args(a0, a1, "committed") == (a0, 0.0, 0.0, a1)


# --------------------------------------------------------------------------- #
# 4. solver controls / substepping
# --------------------------------------------------------------------------- #
def test_substep_fallback_recovers_and_is_recorded():
    """A step that cannot converge in 2 Newton iterations fails without
    substepping and succeeds with dt halving; the substepped steps are
    reported and the response matches the default solve."""
    tight = {"max_iterations": 2, "tolerance": 1e-8}
    with pytest.raises(RuntimeError, match="failed at step"):
        _run(_elastoplastic(solver=dict(tight, max_halvings=0)))
    res = _run(_elastoplastic(solver=dict(tight, max_halvings=8),
                              energy=True))
    ss = res.extra["direct_integration"]["substepped_steps"]
    assert ss and all(1 <= s["halvings"] <= 8 for s in ss)
    assert all(s["t"] == pytest.approx(s["step"] * 0.002) for s in ss)
    ref = _ux(_run(_elastoplastic()))
    assert np.max(np.abs(_ux(res) - ref)) <= 1e-3 * np.max(np.abs(ref))
    # energy stays balanced across the substeps
    assert res.extra["energy"]["max_abs_error_normalized"] < 1e-8


# --------------------------------------------------------------------------- #
# 5. energy
# --------------------------------------------------------------------------- #
def test_energy_undamped_pulse_input_equals_kinetic_plus_strain(sdof_k):
    """Elastic undamped SDOF under a pulse: E_I = E_K + E_S at EVERY step
    (error < 1 %, in fact round-off), E_K/E_S equal the closed forms."""
    dt = 0.005
    acc = _pulse(dt, t_total=1.0)
    res = _run(_sdof(dt=dt, accel=acc, di_damping=NO_DAMP, energy=True))
    en = res.to_dict()["energy"]
    n = len(res.t)
    for key in ("t", "input", "kinetic", "strain", "damping", "hysteretic",
                "error", "error_normalized"):
        assert len(en[key]) == n
    EI, EK, ES = (np.array(en[k]) for k in ("input", "kinetic", "strain"))
    assert np.max(np.abs(EI - EK - ES)) <= 0.01 * np.max(EI)
    assert en["max_abs_error_normalized"] < 1e-10
    assert np.max(np.abs(en["damping"])) < 1e-12 * np.max(EI)
    u, v = _ref("newmark", acc, dt, M_SDOF, sdof_k["k"], 0.0, gamma=0.5,
                beta=0.25)
    assert np.allclose(EK, 0.5 * M_SDOF * v ** 2, rtol=1e-6,
                       atol=1e-9 * np.max(EI))
    assert np.allclose(ES, 0.5 * sdof_k["k"] * u ** 2, rtol=1e-6,
                       atol=1e-9 * np.max(EI))
    # after the pulse no more input: E_I constant
    n_p = int(round(0.05 / dt))
    assert np.ptp(EI[n_p + 1:]) <= 1e-10 * np.max(EI)


def test_energy_damping_equals_integral_c_v2(sdof_k):
    """Rayleigh-damped SDOF: E_D == integral of c v^2 dt (trapezoid on
    the reference velocities) within 1 %, and the balance closes."""
    dt = 0.002
    acc = _pulse(dt, t_total=2.0)
    a0, a1 = 1.2, 0.001
    c = a0 * M_SDOF + a1 * sdof_k["k"]
    res = _run(_sdof(dt=dt, accel=acc, energy=True, di_damping={
        "type": "rayleigh_coefficients", "mass_coeff": a0,
        "stiffness_coeff": a1}))
    en = res.extra["energy"]
    _, v = _ref("newmark", acc, dt, M_SDOF, sdof_k["k"], c, gamma=0.5,
                beta=0.25)
    vv = np.concatenate([[0.0], v])
    ed_ref = np.concatenate([[0.0], np.cumsum(
        0.5 * dt * c * (vv[1:] ** 2 + vv[:-1] ** 2))])[1:]
    ED = np.array(en["damping"])
    assert ED[-1] == pytest.approx(ed_ref[-1], rel=0.01)
    assert np.max(np.abs(ED - ed_ref)) <= 0.01 * ed_ref[-1]
    assert en["max_abs_error_normalized"] < 1e-10
    # most of the input is dissipated by the end
    assert ED[-1] > 0.9 * en["input"][-1]


def test_energy_modal_damping_equals_integral_c_v2(sdof_k):
    """Modal damping (residual method): SDOF with zeta -> c = 2 zeta w m."""
    dt = 0.002
    acc = _pulse(dt, t_total=2.0)
    zeta = 0.05
    res = _run(_sdof(dt=dt, accel=acc, damping=zeta, damping_model="modal",
                     energy=True))
    c = 2 * zeta * sdof_k["w"] * M_SDOF
    u_ref, v = _ref("newmark", acc, dt, M_SDOF, sdof_k["k"], c, gamma=0.5,
                    beta=0.25)
    assert np.max(np.abs(_ux(res) - u_ref)) <= 1e-6 * np.max(np.abs(u_ref))
    vv = np.concatenate([[0.0], v])
    ed_ref = np.sum(0.5 * dt * c * (vv[1:] ** 2 + vv[:-1] ** 2))
    assert res.extra["energy"]["damping"][-1] == pytest.approx(ed_ref,
                                                               rel=0.01)


def test_energy_nonlinear_hysteretic():
    """Yielding hinge: hysteretic energy > 0 and dominant, balance closes;
    the same case with a huge My has zero hysteretic energy."""
    res = _run(_elastoplastic(energy=True))
    en = res.extra["energy"]
    assert en["max_abs_error_normalized"] < 1e-8
    EH = np.array(en["hysteretic"])
    assert EH[-1] > 0.5 * en["input"][-1]
    assert np.min(np.diff(EH)) > -1e-6 * EH[-1]      # dissipation only grows
    el = _run(_elastoplastic(energy=True, My={"C1": 1e9}))
    assert max(abs(x) for x in el.extra["energy"]["hysteretic"]) < 1e-9
    assert el.extra["energy"]["max_abs_error_normalized"] < 1e-8


def test_energy_frame_rigid_diaphragms_all_integrators():
    """MDOF frame with rigid diaphragms (Transformation constraints):
    Newmark balance is exact (constraint forces do no work); HHT/Wilson
    show only their (small, positive) numerical dissipation."""
    r = _run(_frame(energy=True))
    assert r.extra["energy"]["max_abs_error_normalized"] < 1e-9
    for integ, tol in (({"method": "hht", "alpha": -0.05}, 0.01),
                       ({"method": "wilson", "theta": 1.4}, 0.05)):
        en = _run(_frame(energy=True, integration=integ)).extra["energy"]
        assert en["max_abs_error_normalized"] < tol
        assert en["error"][-1] > 0.0               # energy lost, not gained


def test_energy_central_difference_exact_discrete_balance():
    res = _run(_sdof(dt=0.005, integration={"method": "central_difference"},
                     di_damping={"type": "rayleigh_coefficients",
                                 "mass_coeff": 0.5,
                                 "stiffness_coeff": 0.0005},
                     energy=True))
    en = res.extra["energy"]
    assert en["max_abs_error_normalized"] < 1e-10
    assert en["t"][0] == 0.0 and en["t"][1] == pytest.approx(0.005)
    assert en["damping"][-1] > 0.0


def test_api_roundtrip_and_analyze_returns_energy():
    from skyframe.api.server import create_app
    client = create_app().test_client()
    mdl = _sdof(energy=True, integration={"method": "hht", "alpha": -0.05})
    rv = client.post("/api/model", json=mdl.to_dict())
    assert rv.status_code == 200
    td = rv.get_json()["th_cases"]["TH"]
    assert td["integration"] == {"method": "hht", "alpha": -0.05}
    assert td["energy"] is True
    rv = client.post("/api/analyze")
    assert rv.status_code == 200
    th = rv.get_json()["th_cases"]["TH"]
    assert th["direct_integration"]["integration"]["method"] == "hht"
    assert len(th["energy"]["input"]) == len(th["t"])
