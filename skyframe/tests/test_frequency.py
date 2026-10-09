"""Frequency-domain analysis: Steady-State and Power Spectral Density.

Every expected number is derived here from first principles:

* SDOF cantilever (tip mass on ux only; k = 3EI/L^3, exact for the Euler
  elasticBeamColumn): steady-state amplitude
  ``(F/k) / sqrt((1 - r^2)^2 + (2 zeta r)^2)``, phase lag
  ``atan2(2 zeta r, 1 - r^2)``, resonance peak at ``r = sqrt(1 - 2 zeta^2)``
  of height ``(F/k) / (2 zeta sqrt(1 - zeta^2))``; hysteretic damping
  ``(F/k) / sqrt((1 - r^2)^2 + (2 zeta)^2)``.
* 2-mass cantilever: independent closed-form flexibility matrix
  ``f(a, b) = a^2 (3b - a) / (6 EI)``, K = F^-1, hand modal superposition
  and a numpy complex direct solve.
* PSD: Crandall's white-noise result.  For a one-sided PSD per rad/s G0,
  ``sigma_u^2 = pi G0 / (4 zeta w^3)``; the engine takes a one-sided PSD
  per Hz W0 (G0 = W0 / 2 pi), i.e. ``sigma_u^2 = W0 / (8 zeta w^3)``.

Units: kN, m, tonne, s; frequencies in Hz.
"""

import json
import math

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core.builder import quick_building
from skyframe.core.frequency_cases import (FrequencyLoad, PSDCase,
                                           SteadyStateCase,
                                           add_frequency_function,
                                           add_psd_case,
                                           add_steady_state_case)
from skyframe.core.model import (G_ACCEL, BuildingModel, FrameSection,
                                 Material, NodalLoad, NodalMass,
                                 PointSupport)
from skyframe.engine.frequency import frequency_grid

E_CONC = 25_000_000.0      # kPa
ZETA = 0.05


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _new_model(name, heights) -> BuildingModel:
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.set_stories(list(heights))
    return mdl


def _sdof(m=10.0, L=3.0, size=0.3):
    """Cantilever column, tip mass on ux only.  Returns (model, k, wn)."""
    mdl = _new_model("SDOF", [L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=m))
    mdl.pattern("PX", "other").nodal_loads.append(NodalLoad((0, 0, L),
                                                            fx=1.0))
    mdl.pattern("PY", "other").nodal_loads.append(NodalLoad((0, 0, L),
                                                            fy=1.0))
    mdl.num_modes = 1
    k = 3.0 * E_CONC * (size ** 4 / 12.0) / L ** 3
    return mdl, k, math.sqrt(k / m)


def _two_mass(L=6.0, size=0.3, m1=8.0, m2=5.0):
    """Cantilever with masses m1 at L/2 and m2 at L (ux).  Returns
    (model, K 2x2, M 2x2) from the closed-form flexibility matrix."""
    mdl = _new_model("2DOF", [L / 2, L / 2])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L / 2),
                   story="Story1", uid="C1")
    mdl.add_member("column", "COL", (0, 0, L / 2), (0, 0, L),
                   story="Story2", uid="C2")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, L / 2), mx=m1))
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=m2))
    mdl.pattern("PX", "other").nodal_loads.append(NodalLoad((0, 0, L),
                                                            fx=1.0))
    mdl.num_modes = 2
    EI = E_CONC * size ** 4 / 12.0
    a, b = L / 2.0, L
    f = lambda x, y: x * x * (3.0 * y - x) / (6.0 * EI)  # noqa: E731
    F = np.array([[f(a, a), f(a, b)], [f(a, b), f(b, b)]])
    return mdl, np.linalg.inv(F), np.diag([m1, m2])


def _hand_modes(K, M):
    """Mass-normalised modes (columns) and circular frequencies."""
    Mih = np.diag(1.0 / np.sqrt(np.diag(M)))
    lam, psi = np.linalg.eigh(Mih @ K @ Mih)
    return np.sqrt(lam), Mih @ psi


def _trapz(y, x):
    """Trapezoid rule (numpy 2 removed np.trapz)."""
    y = np.asarray(y, dtype=float)
    x = np.asarray(x, dtype=float)
    return float(np.sum(0.5 * (y[1:] + y[:-1]) * np.diff(x)))


def _tag(eng, pt):
    asm = eng._asm if eng._asm is not None else eng._build()
    return str(eng._find_node(asm, pt))


def _cplx(entry, dof=None):
    amp = np.array(entry["amp"], dtype=float)
    ph = np.radians(np.array(entry["phase_deg"], dtype=float))
    z = amp * np.exp(1j * ph)
    return z if dof is None else z[:, dof]


# --------------------------------------------------------------------------- #
# 1. SDOF steady state
# --------------------------------------------------------------------------- #
def test_sdof_steady_state_amplitude_sweep_exact():
    mdl, k, wn = _sdof()
    fn = wn / (2 * math.pi)
    F = 2.5
    add_steady_state_case(mdl, "SS", [{"pattern": "PX", "scale": F}],
                          freq_start_hz=0.0, freq_end_hz=3 * fn, n_freq=61,
                          damping=ZETA)
    eng = OpenSeesEngine(mdl)
    r = eng.run_steady_state("SS")
    f = np.array(r["frequencies_hz"])
    amp = np.array(r["node_disp"][_tag(eng, (0, 0, 3.0))]["amp"])[:, 0]
    rr = f / fn
    exact = (F / k) / np.sqrt((1 - rr ** 2) ** 2 + (2 * ZETA * rr) ** 2)
    assert np.allclose(amp, exact, rtol=1e-9, atol=0)
    assert r["modes_used"] == 1
    assert r["modal_frequencies_hz"][0] == pytest.approx(fn, rel=1e-9)


def test_sdof_steady_state_phase_sweep_exact():
    mdl, k, wn = _sdof()
    fn = wn / (2 * math.pi)
    add_steady_state_case(mdl, "SS", [{"pattern": "PX"}], freq_start_hz=0.0,
                          freq_end_hz=4 * fn, n_freq=81, damping=ZETA)
    eng = OpenSeesEngine(mdl)
    r = eng.run_steady_state("SS")
    rr = np.array(r["frequencies_hz"]) / fn
    ph = np.array(r["node_disp"][_tag(eng, (0, 0, 3.0))]["phase_deg"])[:, 0]
    lag = np.degrees(np.arctan2(2 * ZETA * rr, 1 - rr ** 2))
    assert np.allclose(-ph, lag, atol=1e-9)
    # quadrature at resonance, in phase at f = 0, ~opposed far above
    i_res = int(np.argmin(np.abs(rr - 1.0)))
    assert rr[i_res] == pytest.approx(1.0, abs=1e-12)    # modal refinement
    assert -ph[i_res] == pytest.approx(90.0, abs=1e-9)
    assert ph[0] == pytest.approx(0.0, abs=1e-12)
    assert -ph[-1] > 170.0


def test_sdof_resonance_peak_location_and_height():
    zeta = 0.10
    mdl, k, wn = _sdof()
    fn = wn / (2 * math.pi)
    add_steady_state_case(mdl, "SS", [{"pattern": "PX"}],
                          freq_start_hz=0.8 * fn, freq_end_hz=1.1 * fn,
                          n_freq=6001, damping=zeta, modal_refine=False)
    eng = OpenSeesEngine(mdl)
    r = eng.run_steady_state("SS")
    tip = _tag(eng, (0, 0, 3.0))
    f_peak = r["peaks"]["node_disp_freq_hz"][tip][0]
    r_peak = math.sqrt(1 - 2 * zeta ** 2)
    assert f_peak / fn == pytest.approx(r_peak, abs=1e-4)
    peak = r["peaks"]["node_disp"][tip][0]
    assert peak == pytest.approx((1 / k) / (2 * zeta * math.sqrt(1 - zeta ** 2)),
                                 rel=1e-7)


def test_sdof_hysteretic_damping_complex_stiffness():
    mdl, k, wn = _sdof()
    fn = wn / (2 * math.pi)
    add_steady_state_case(mdl, "SS", [{"pattern": "PX"}], freq_start_hz=0.0,
                          freq_end_hz=3 * fn, n_freq=31, damping=ZETA,
                          damping_type="hysteretic")
    eng = OpenSeesEngine(mdl)
    r = eng.run_steady_state("SS")
    rr = np.array(r["frequencies_hz"]) / fn
    z = _cplx(r["node_disp"][_tag(eng, (0, 0, 3.0))], 0)
    exact = 1.0 / (k * ((1 - rr ** 2) + 2j * ZETA))        # u = F/(K(1+i eta)-w^2 m)
    assert np.allclose(z, exact, rtol=1e-9, atol=1e-18)
    # hysteretic: response lags even at f = 0 (loss angle atan(2 zeta))
    assert -r["node_disp"][_tag(eng, (0, 0, 3.0))]["phase_deg"][0][0] == \
        pytest.approx(math.degrees(math.atan(2 * ZETA)), abs=1e-9)


def test_sdof_base_reaction_and_story_outputs():
    mdl, k, wn = _sdof()
    fn = wn / (2 * math.pi)
    add_steady_state_case(mdl, "SS", [{"pattern": "PX", "scale": 3.0}],
                          freq_start_hz=0.1, freq_end_hz=2 * fn, n_freq=21,
                          damping=ZETA)
    eng = OpenSeesEngine(mdl)
    r = eng.run_steady_state("SS")
    u = _cplx(r["node_disp"][_tag(eng, (0, 0, 3.0))], 0)
    fx = _cplx(r["base"]["FX"])
    my = _cplx(r["base"]["MY"])
    # elastic reaction = -k u (support pushes back), base moment = FX * L
    assert np.allclose(fx, -k * u, rtol=1e-8, atol=1e-12)
    assert np.allclose(np.abs(my), 3.0 * np.abs(fx), rtol=1e-8)
    st = r["story"]["Story1"]
    assert np.allclose(_cplx(st["ux"]), u, rtol=1e-12, atol=1e-18)
    assert np.allclose(_cplx(st["drift_x"]), u / 3.0, rtol=1e-12, atol=1e-18)
    assert r["peaks"]["base"]["FX"] == pytest.approx(np.abs(fx).max(),
                                                     rel=1e-12)
    sup = r["peaks"]["reactions"]
    assert len(sup) == 1 and next(iter(sup.values()))[0] == pytest.approx(
        np.abs(fx).max(), rel=1e-12)


def test_sdof_ground_acceleration_relative_response():
    mdl, k, wn = _sdof()
    fn = wn / (2 * math.pi)
    ag = 0.3 * G_ACCEL
    add_steady_state_case(mdl, "SA", [{"pattern": "accel", "direction": "UX",
                                       "scale": ag}],
                          freq_start_hz=0.0, freq_end_hz=3 * fn, n_freq=31,
                          damping=ZETA)
    eng = OpenSeesEngine(mdl)
    r = eng.run_steady_state("SA")
    rr = np.array(r["frequencies_hz"]) / fn
    u = _cplx(r["node_disp"][_tag(eng, (0, 0, 3.0))], 0)
    # m u'' + c u' + k u = -m ag  ->  u = -(ag / wn^2) / (1 - r^2 + 2 i zeta r)
    exact = -(ag / wn ** 2) / (1 - rr ** 2 + 2j * ZETA * rr)
    assert np.allclose(u, exact, rtol=1e-9, atol=1e-18)
    # an orthogonal (UY) acceleration excites nothing (no y mass)
    add_steady_state_case(mdl, "SY", [{"pattern": "accel", "direction": "UY",
                                       "scale": ag}], n_freq=5,
                          freq_end_hz=fn, damping=ZETA)
    r2 = OpenSeesEngine(mdl).run_steady_state("SY")
    assert max(max(v) for v in r2["peaks"]["node_disp"].values()) == 0.0


# --------------------------------------------------------------------------- #
# 2. 2-DOF: hand modal superposition, direct solve, truncation
# --------------------------------------------------------------------------- #
def _two_mass_engine(method="modal", damping_type="modal", num_modes=0,
                     freqs=(0.0, 0.7, 1.9, 3.3, 6.0, 12.0)):
    mdl, K, M = _two_mass()
    add_steady_state_case(mdl, "SS", [{"pattern": "PX", "scale": 1.0}],
                          freq_start_hz=0.0, freq_end_hz=20.0, n_freq=0,
                          frequencies=list(freqs), modal_refine=False,
                          damping=ZETA, method=method,
                          damping_type=damping_type, num_modes=num_modes)
    eng = OpenSeesEngine(mdl)
    r = eng.run_steady_state("SS")
    z1 = _cplx(r["node_disp"][_tag(eng, (0, 0, 3.0))], 0)
    z2 = _cplx(r["node_disp"][_tag(eng, (0, 0, 6.0))], 0)
    return r, np.vstack([z1, z2]).T, K, M


def test_two_dof_frf_matches_hand_modal_superposition():
    r, Z, K, M = _two_mass_engine()
    om, phi = _hand_modes(K, M)
    F = np.array([0.0, 1.0])
    w = 2 * np.pi * np.array(r["frequencies_hz"])
    for k_, wk in enumerate(w):
        H = 1.0 / (om ** 2 - wk ** 2 + 2j * ZETA * om * wk)
        u = phi @ (H * (phi.T @ F))
        assert np.allclose(Z[k_], u, rtol=1e-6, atol=1e-14)
    assert np.allclose(r["modal_frequencies_hz"], om / (2 * np.pi),
                       rtol=1e-8)


def test_two_dof_direct_equals_numpy_complex_solve():
    r, Z, K, M = _two_mass_engine(method="direct")
    om, phi = _hand_modes(K, M)
    C = M @ phi @ np.diag(2 * ZETA * om) @ phi.T @ M     # modal damping
    F = np.array([0.0, 1.0])
    for k_, f in enumerate(r["frequencies_hz"]):
        w = 2 * np.pi * f
        u = np.linalg.solve(K - w * w * M + 1j * w * C, F)
        assert np.allclose(Z[k_], u, rtol=1e-6, atol=1e-14)
    assert r["method"] == "direct" and r["modes_used"] == 2


def test_two_dof_hysteretic_direct_equals_numpy_complex_stiffness():
    r, Z, K, M = _two_mass_engine(method="direct", damping_type="hysteretic")
    F = np.array([0.0, 1.0])
    for k_, f in enumerate(r["frequencies_hz"]):
        w = 2 * np.pi * f
        u = np.linalg.solve(K * (1 + 2j * ZETA) - w * w * M, F)
        assert np.allclose(Z[k_], u, rtol=1e-6, atol=1e-14)


def test_two_dof_modal_truncation_single_mode():
    r, Z, K, M = _two_mass_engine(num_modes=1)
    assert r["modes_used"] == 1
    om, phi = _hand_modes(K, M)
    F = np.array([0.0, 1.0])
    for k_, f in enumerate(r["frequencies_hz"]):
        w = 2 * np.pi * f
        H1 = 1.0 / (om[0] ** 2 - w * w + 2j * ZETA * om[0] * w)
        u = phi[:, 0] * H1 * (phi[:, 0] @ F)
        assert np.allclose(Z[k_], u, rtol=1e-6, atol=1e-14)


def test_direct_method_static_residual_on_massless_dof():
    """A y load at the tip excites no mode (no y mass): modal superposition
    gives zero, the direct method gives the exact static F/k_y."""
    mdl, k, wn = _sdof()
    for method in ("modal", "direct"):
        add_steady_state_case(mdl, f"S_{method}", [{"pattern": "PY"}],
                              freq_start_hz=0.0, freq_end_hz=5.0, n_freq=6,
                              damping=ZETA, method=method)
    eng = OpenSeesEngine(mdl)
    tip = None
    rm = eng.run_steady_state("S_modal")
    rd = eng.run_steady_state("S_direct")
    tip = _tag(eng, (0, 0, 3.0))
    assert np.max(np.abs(rm["node_disp"][tip]["amp"])) == 0.0
    uy = np.array(rd["node_disp"][tip]["amp"])[:, 1]
    assert np.allclose(uy, 1.0 / k, rtol=1e-9)          # square section


def test_direct_at_zero_frequency_equals_static_case_rigid_diaphragms():
    """Rigid-diaphragm building: the direct method at f = 0 reproduces the
    linear static solution of the same pattern at EVERY joint — lateral
    (massed) and gravity (massless, pure residual) patterns alike."""
    mdl = quick_building(bays_x=1, bays_y=1, stories=2)
    for pat in ("EQX", "DEAD"):
        add_steady_state_case(mdl, f"SS_{pat}", [{"pattern": pat}],
                              freq_start_hz=0.0, freq_end_hz=0.0, n_freq=1,
                              modal_refine=False, damping=ZETA,
                              method="direct")
    eng = OpenSeesEngine(mdl)
    for pat in ("EQX", "DEAD"):
        st = eng.run_static(pat)
        r = eng.run_steady_state(f"SS_{pat}")
        scale = max(abs(v) for d in st.node_disp.values() for v in d)
        for t, d in st.node_disp.items():
            z = np.array(r["node_disp"][str(t)]["amp"][0]) * np.cos(
                np.radians(r["node_disp"][str(t)]["phase_deg"][0]))
            assert np.allclose(z, d, atol=1e-8 * scale)
        assert r["base"]["FX"]["amp"][0] == pytest.approx(
            abs(st.base["FX"]), rel=1e-7, abs=1e-6)
        assert r["base"]["FZ"]["amp"][0] == pytest.approx(
            abs(st.base["FZ"]), rel=1e-7, abs=1e-6)


# --------------------------------------------------------------------------- #
# 3. PSD
# --------------------------------------------------------------------------- #
def _crandall_case(mdl, name, W0, n_freq, f_end, modal_refine,
                   fn_name="WN", scale=1.0):
    if fn_name not in mdl.frequency_functions:
        add_frequency_function(mdl, fn_name, "psd", [[0.0, W0], [1e3, W0]])
    add_psd_case(mdl, name, [{"pattern": "accel", "direction": "UX",
                              "function": fn_name, "scale": scale}],
                 freq_start_hz=0.0, freq_end_hz=f_end, n_freq=n_freq,
                 damping=ZETA, modal_refine=modal_refine)


def test_psd_white_noise_crandall_fine_grid():
    mdl, k, wn = _sdof()
    W0 = 0.02                                  # (m/s^2)^2 / Hz, one-sided
    _crandall_case(mdl, "P", W0, n_freq=8001, f_end=80.0, modal_refine=False)
    eng = OpenSeesEngine(mdl)
    r = eng.run_psd("P")
    s2 = r["rms"]["node_disp"][_tag(eng, (0, 0, 3.0))][0] ** 2
    G0 = W0 / (2 * math.pi)                    # one-sided per rad/s
    crandall = math.pi * G0 / (4 * ZETA * wn ** 3)
    assert s2 == pytest.approx(crandall, rel=0.01)
    # base shear RMS = k * sigma_u (elastic reaction of the SDOF)
    assert r["rms"]["base"]["FX"] == pytest.approx(k * math.sqrt(s2),
                                                   rel=1e-9)
    assert r["correlation"] == "full"


def test_psd_modal_refinement_makes_coarse_grid_accurate():
    mdl, k, wn = _sdof()
    W0 = 0.02
    _crandall_case(mdl, "COARSE", W0, n_freq=81, f_end=80.0,
                   modal_refine=False)
    _crandall_case(mdl, "REFINED", W0, n_freq=81, f_end=80.0,
                   modal_refine=True)
    eng = OpenSeesEngine(mdl)
    tip = _tag(eng, (0, 0, 3.0))
    crandall = W0 / (8 * ZETA * wn ** 3)
    s2_ref = eng.run_psd("REFINED")["rms"]["node_disp"][tip][0] ** 2
    s2_crs = eng.run_psd("COARSE")["rms"]["node_disp"][tip][0] ** 2
    assert s2_ref == pytest.approx(crandall, rel=0.02)
    assert abs(s2_ref / crandall - 1) < abs(s2_crs / crandall - 1)


def test_psd_force_white_noise_and_response_curve():
    """White-noise FORCE on the SDOF: sigma_u^2 = W0 / (8 zeta wn^3 m^2);
    the reported response PSD at the output joint is exactly |H|^2 W0."""
    m = 10.0
    mdl, k, wn = _sdof(m=m)
    W0 = 4.0                                   # kN^2/Hz
    add_frequency_function(mdl, "WF", "psd", [[0.0, W0], [500.0, W0]])
    add_psd_case(mdl, "PF", [{"pattern": "PX", "function": "WF"}],
                 freq_start_hz=0.0, freq_end_hz=80.0, n_freq=8001,
                 damping=ZETA, modal_refine=False,
                 output_points=[[0.0, 0.0, 3.0]])
    eng = OpenSeesEngine(mdl)
    r = eng.run_psd("PF")
    tip = _tag(eng, (0, 0, 3.0))
    s2 = r["rms"]["node_disp"][tip][0] ** 2
    assert s2 == pytest.approx(W0 / (8 * ZETA * wn ** 3 * m * m), rel=0.01)
    f = np.array(r["frequencies_hz"])
    w = 2 * np.pi * f
    H2 = 1.0 / np.abs(k - m * w * w + 2j * ZETA * m * wn * w) ** 2
    curve = np.array(r["psd"]["node_disp"][tip])[:, 0]
    assert np.allclose(curve, W0 * H2, rtol=1e-9, atol=0)
    # RMS is exactly the trapezoid integral of the reported curve
    assert s2 == pytest.approx(_trapz(curve, f), rel=1e-12)
    # story/base response-PSD curves are always reported
    assert len(r["psd"]["story"]["Story1"]["ux"]) == f.size
    assert len(r["psd"]["base"]["FX"]) == f.size


def test_psd_units_g2_per_hz_with_scale_equals_si_function():
    mdl, k, wn = _sdof()
    S_g = 1e-4                                 # g^2/Hz
    _crandall_case(mdl, "PG", S_g, n_freq=401, f_end=20.0, modal_refine=True,
                   fn_name="G2", scale=G_ACCEL)
    _crandall_case(mdl, "PSI", S_g * G_ACCEL ** 2, n_freq=401, f_end=20.0,
                   modal_refine=True, fn_name="SI", scale=1.0)
    eng = OpenSeesEngine(mdl)
    tip = _tag(eng, (0, 0, 3.0))
    a = eng.run_psd("PG")["rms"]["node_disp"][tip][0]
    b = eng.run_psd("PSI")["rms"]["node_disp"][tip][0]
    assert a == pytest.approx(b, rel=1e-12)
    assert a > 0.0


def test_psd_two_dof_full_correlation_cross_terms():
    """2-mass cantilever, band-limited force PSD: the engine RMS equals the
    trapezoid integral of |sum_i phi_i H_i phi_i^T F|^2 (all modal cross
    terms) computed by hand, and differs from the SRSS-of-modes value."""
    mdl, K, M = _two_mass()
    om, phi = _hand_modes(K, M)
    add_frequency_function(mdl, "BL", "psd", [[0.0, 1.0], [15.0, 1.0]])
    add_psd_case(mdl, "P2", [{"pattern": "PX", "function": "BL"}],
                 freq_start_hz=0.0, freq_end_hz=15.0, n_freq=3001,
                 damping=0.2, modal_refine=False)
    eng = OpenSeesEngine(mdl)
    r = eng.run_psd("P2")
    f = np.array(r["frequencies_hz"])
    w = 2 * np.pi * f
    F = np.array([0.0, 1.0])
    H = 1.0 / (om[None, :] ** 2 - w[:, None] ** 2
               + 2j * 0.2 * om[None, :] * w[:, None])
    modal = H * (phi.T @ F)[None, :]                     # (nf, 2)
    u1 = modal @ phi[0]
    full = math.sqrt(_trapz(np.abs(u1) ** 2, f))
    srss = math.sqrt(sum(_trapz(np.abs(modal[:, i] * phi[0, i]) ** 2, f)
                         for i in range(2)))
    got = r["rms"]["node_disp"][_tag(eng, (0, 0, 3.0))][0]
    assert got == pytest.approx(full, rel=1e-6)
    assert abs(full - srss) / full > 1e-3              # cross terms matter


# --------------------------------------------------------------------------- #
# 4. linearity, phases, zero loads, functions
# --------------------------------------------------------------------------- #
def test_linearity_scale_phase_and_coherent_loads():
    mdl, k, wn = _sdof()
    kw = dict(freq_start_hz=0.0, freq_end_hz=5.0, n_freq=11, damping=ZETA)
    add_steady_state_case(mdl, "ONE", [{"pattern": "PX"}], **kw)
    add_steady_state_case(mdl, "TWO", [{"pattern": "PX"},
                                       {"pattern": "PX"}], **kw)
    add_steady_state_case(mdl, "CANCEL", [{"pattern": "PX"},
                                          {"pattern": "PX",
                                           "phase_deg": 180.0}], **kw)
    add_steady_state_case(mdl, "SHIFT", [{"pattern": "PX",
                                          "phase_deg": 30.0}], **kw)
    eng = OpenSeesEngine(mdl)
    tip = _tag(eng, (0, 0, 3.0))
    one = _cplx(eng.run_steady_state("ONE")["node_disp"][tip], 0)
    two = _cplx(eng.run_steady_state("TWO")["node_disp"][tip], 0)
    canc = np.array(eng.run_steady_state("CANCEL")["node_disp"][tip]["amp"])
    shift = _cplx(eng.run_steady_state("SHIFT")["node_disp"][tip], 0)
    assert np.allclose(two, 2 * one, rtol=1e-12)
    assert np.max(canc) <= 1e-12 * np.max(np.abs(one))
    assert np.allclose(shift, one * np.exp(1j * math.radians(30.0)),
                       rtol=1e-9)


def test_zero_load_cases_give_zeros():
    mdl, k, wn = _sdof()
    add_frequency_function(mdl, "WN", "psd", [[0.0, 1.0], [50.0, 1.0]])
    add_steady_state_case(mdl, "S0", [{"pattern": "PX", "scale": 0.0}],
                          n_freq=5, freq_end_hz=5.0)
    add_steady_state_case(mdl, "SE", [], n_freq=5, freq_end_hz=5.0)
    add_psd_case(mdl, "P0", [{"pattern": "accel", "direction": "UX",
                              "function": "WN", "scale": 0.0}],
                 n_freq=5, freq_end_hz=5.0)
    eng = OpenSeesEngine(mdl)
    for name in ("S0", "SE"):
        r = eng.run_steady_state(name)
        assert all(v == 0.0 for d in r["peaks"]["node_disp"].values()
                   for v in d)
        assert all(v == 0.0 for v in r["peaks"]["base"].values())
    p = eng.run_psd("P0")
    assert all(v == 0.0 for d in p["rms"]["node_disp"].values() for v in d)
    assert all(v == 0.0 for v in p["rms"]["base"].values())


def test_steady_state_function_interpolation_and_zero_outside():
    mdl, k, wn = _sdof()
    add_frequency_function(mdl, "RAMP", "steady_state",
                           [[1.0, 0.0], [3.0, 4.0]])
    add_steady_state_case(mdl, "SF", [{"pattern": "PX", "function": "RAMP"}],
                          freq_start_hz=0.0, freq_end_hz=4.0, n_freq=0,
                          frequencies=[0.5, 2.0, 3.5], modal_refine=False,
                          damping=ZETA)
    add_steady_state_case(mdl, "S1", [{"pattern": "PX"}],
                          freq_start_hz=0.0, freq_end_hz=4.0, n_freq=0,
                          frequencies=[0.5, 2.0, 3.5], modal_refine=False,
                          damping=ZETA)
    eng = OpenSeesEngine(mdl)
    tip = _tag(eng, (0, 0, 3.0))
    a = np.array(eng.run_steady_state("SF")["node_disp"][tip]["amp"])[:, 0]
    b = np.array(eng.run_steady_state("S1")["node_disp"][tip]["amp"])[:, 0]
    assert a[0] == 0.0 and a[2] == 0.0          # outside [1, 3] Hz
    assert a[1] == pytest.approx(2.0 * b[1], rel=1e-12)   # value 2 at 2 Hz


def test_frequency_grid_union_refine_and_dedupe():
    case = SteadyStateCase("G", freq_start_hz=0.0, freq_end_hz=10.0,
                           n_freq=11, frequencies=[2.5, 3.0, 3.0],
                           damping=0.05, modal_refine=True)
    g = frequency_grid(case, [4.0, 25.0])
    assert g[0] == 0.0 and g[-1] == 10.0
    assert np.all(np.diff(g) > 0)
    assert 2.5 in g and 4.0 in g
    assert np.any(np.isclose(g, 4.0 * (1 + 0.1 * 0.05)))
    assert not np.any(g > 10.0)                 # 25 Hz mode out of range
    case.modal_refine = False
    assert frequency_grid(case, [4.0]).size == 12


# --------------------------------------------------------------------------- #
# 5. model data: round trip, back-compat, validation
# --------------------------------------------------------------------------- #
def _model_with_cases():
    mdl, k, wn = _sdof()
    add_frequency_function(mdl, "WN", "psd", [[0.0, 0.01], [40.0, 0.01]])
    add_frequency_function(mdl, "AMP", "steady_state",
                           [[0.0, 1.0], [40.0, 2.0]])
    add_steady_state_case(mdl, "SS", [{"pattern": "PX", "scale": 2.0,
                                       "function": "AMP", "phase_deg": 10.0},
                                      {"pattern": "accel", "direction": "UX",
                                       "scale": 0.5}],
                          freq_start_hz=0.5, freq_end_hz=8.0, n_freq=16,
                          frequencies=[2.2], method="direct",
                          damping=0.03, damping_type="hysteretic",
                          output_points=[[0.0, 0.0, 3.0]])
    add_psd_case(mdl, "PSD", [{"pattern": "accel", "direction": "UX",
                               "function": "WN", "scale": G_ACCEL}],
                 freq_start_hz=0.0, freq_end_hz=30.0, n_freq=301,
                 num_modes=1)
    return mdl


def test_model_round_trip_of_frequency_fields():
    mdl = _model_with_cases()
    d = mdl.to_dict()
    json.dumps(d)
    assert set(d["frequency_functions"]) == {"WN", "AMP"}
    m2 = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert m2.to_dict() == d
    ss = m2.steady_state_cases["SS"]
    assert isinstance(ss, SteadyStateCase)
    assert isinstance(ss.loads[0], FrequencyLoad)
    assert ss.loads[1].is_accel and ss.loads[1].direction == "UX"
    assert ss.method == "direct" and ss.damping_type == "hysteretic"
    assert isinstance(m2.psd_cases["PSD"], PSDCase)
    assert m2.psd_cases["PSD"].num_modes == 1


def test_old_models_unchanged_and_results_byte_identical():
    """No frequency fields -> no new keys anywhere; adding frequency cases
    leaves every pre-existing result byte-identical."""
    base = quick_building(bays_x=1, bays_y=1, stories=2)
    d0 = base.to_dict()
    for key in ("frequency_functions", "steady_state_cases", "psd_cases"):
        assert key not in d0
    m_old = BuildingModel.from_dict(d0)
    assert m_old.steady_state_cases == {} and m_old.psd_cases == {}
    assert m_old.frequency_functions == {}
    assert json.dumps(m_old.to_dict(), sort_keys=True) == \
        json.dumps(d0, sort_keys=True)
    r0 = OpenSeesEngine(m_old).run().to_dict()
    assert "steady_state" not in r0 and "psd" not in r0

    m_new = BuildingModel.from_dict(d0)
    add_frequency_function(m_new, "WN", "psd", [[0.0, 1e-3], [20.0, 1e-3]])
    add_steady_state_case(m_new, "SS", [{"pattern": "EQX"}], n_freq=11,
                          freq_end_hz=5.0)
    add_psd_case(m_new, "PSD", [{"pattern": "accel", "direction": "UY",
                                 "function": "WN"}], n_freq=21,
                 freq_end_hz=10.0)
    r1 = OpenSeesEngine(m_new).run().to_dict()
    assert set(r1["steady_state"]) == {"SS"} and set(r1["psd"]) == {"PSD"}
    r1.pop("steady_state")
    r1.pop("psd")
    # v1.13 run control reports the new cases' status; nothing else changes
    assert r1["case_status"].pop("SS") == "finished"
    assert r1["case_status"].pop("PSD") == "finished"
    assert json.dumps(r1, sort_keys=True) == json.dumps(r0, sort_keys=True)


@pytest.mark.parametrize("mutate, match", [
    (lambda m: m.steady_state_cases["SS"].loads.append(
        FrequencyLoad("NOPE")), "unknown load pattern"),
    (lambda m: m.steady_state_cases["SS"].loads.append(
        FrequencyLoad("accel", direction="RX")), "accel load direction"),
    (lambda m: m.psd_cases["PSD"].loads.append(
        FrequencyLoad("PX")), "must name a PSD function"),
    (lambda m: m.psd_cases["PSD"].loads.append(
        FrequencyLoad("PX", function="AMP")), "is a 'steady_state'"),
    (lambda m: setattr(m.psd_cases["PSD"], "damping", 0.0), "damping"),
    (lambda m: setattr(m.steady_state_cases["SS"], "method", "x"), "method"),
    (lambda m: setattr(m.steady_state_cases["SS"], "freq_end_hz", 0.1),
     "freq_start_hz"),
    (lambda m: setattr(m.frequency_functions["WN"], "points",
                       [[1.0, 1.0], [0.5, 1.0]]), "strictly increasing"),
    (lambda m: setattr(m.frequency_functions["WN"], "points",
                       [[0.0, -1.0], [1.0, 1.0]]), "PSD values"),
])
def test_validation_errors(mutate, match):
    mdl = _model_with_cases()
    mdl.validate()
    mutate(mdl)
    with pytest.raises(ValueError, match=match):
        mdl.validate()
    d = _model_with_cases().to_dict()
    BuildingModel.from_dict(d)                  # the unmutated dict is fine


def test_unknown_case_and_no_mass_errors():
    mdl, k, wn = _sdof()
    eng = OpenSeesEngine(mdl)
    with pytest.raises(ValueError, match="Unknown steady-state case"):
        eng.run_steady_state("NOPE")
    with pytest.raises(ValueError, match="Unknown PSD case"):
        eng.run_psd("NOPE")
    mdl.nodal_masses = []
    add_steady_state_case(mdl, "S", [{"pattern": "PX"}], n_freq=3)
    with pytest.raises(RuntimeError, match="no dynamic modes"):
        OpenSeesEngine(mdl).run_steady_state("S")


# --------------------------------------------------------------------------- #
# 6. API
# --------------------------------------------------------------------------- #
@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_api_frequency_endpoints(api_client):
    mdl = _model_with_cases()
    resp = api_client.post("/api/model", json=mdl.to_dict())
    assert resp.status_code == 200
    assert set(resp.get_json()["steady_state_cases"]) == {"SS"}

    ss = api_client.post("/api/analyze/steady_state", json={"case": "SS"})
    assert ss.status_code == 200
    body = ss.get_json()
    assert body["case"] == "SS" and body["type"] == "steady_state"
    assert body["method"] == "direct"
    assert len(body["node_disp"]) == 1                  # output_points
    nf = len(body["frequencies_hz"])
    entry = next(iter(body["node_disp"].values()))
    assert len(entry["amp"]) == nf and len(entry["amp"][0]) == 6

    ps = api_client.post("/api/analyze/psd", json={"case": "PSD"})
    assert ps.status_code == 200
    pb = ps.get_json()
    assert pb["type"] == "psd" and pb["modes_used"] == 1
    assert set(pb["rms"]) == {"node_disp", "reactions", "base", "story"}

    assert api_client.post("/api/analyze/psd",
                           json={"case": "NOPE"}).status_code == 400
    assert api_client.post("/api/analyze/steady_state",
                           json={}).status_code == 400

    full = api_client.post("/api/analyze").get_json()
    assert set(full["steady_state"]) == {"SS"}
    assert set(full["psd"]) == {"PSD"}
    assert full["steady_state"]["SS"] == body
