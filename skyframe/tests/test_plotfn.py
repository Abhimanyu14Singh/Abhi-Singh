"""Plot functions + floor response spectra (gap audit E1/E3).

Expected numbers are derived in the test from first principles:

* ``output_requests`` round-trips byte-identically; absent = the TH
  result dict is byte-identical (no ``plot_functions`` key) and asking for
  outputs never perturbs the legacy keys;
* every recorded series has len(t) + 1 samples (t = 0 included);
* a recorded joint at the SDOF tip == the story displacement; a joint at
  the fixed base has zero relative motion and absolute acceleration ==
  the ground record; ground velocity / displacement of a constant
  acceleration are a*t and a*t^2/2 exactly;
* undamped SDOF: base shear == -m * absolute tip acceleration
  (equilibrium of the mass);
* link hysteresis: the trapezoidal area under the recorded basic
  force-deformation (minus the stored F^2/2k0) == the energy tracker's
  hysteretic energy; the elastic branch slope == k1;
* Nigam-Jennings: step / ramp inputs reproduce their closed-form SDOF
  responses to round-off (the recurrence is exact for piecewise-linear
  input); a sampled harmonic converges O(dt^2) to the closed-form
  transient + steady-state solution; the spectrum of the ground motion
  at T_sdof equals the SkyFrame SDOF engine's peak displacement;
* floor spectrum of the base joint of a fixed-base model == the ground
  spectrum (bit-identical: identical input series).

Units: kN, m, tonne, s.
"""

import json
import math

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core import plotfn as pfn  # noqa: E402
from skyframe.core.model import (  # noqa: E402
    ISOLATOR_DEFAULT_KV,
    BuildingModel,
    FrameSection,
    Material,
    NodalLoad,
    NodalMass,
    PointSupport,
)

E_CONC = 25_000_000.0
M_SDOF = 10.0
L_COL = 3.0
NO_DAMP = {"type": "rayleigh_coefficients", "mass_coeff": 0.0,
           "stiffness_coeff": 0.0}


def _pulse(dt, t_pulse=0.25, amp=2.0, t_total=2.0):
    n_p = int(round(t_pulse / dt))
    n = int(round(t_total / dt))
    acc = [0.0] + [amp * math.sin(math.pi * k / n_p) for k in range(1, n_p)]
    return acc + [0.0] * (n - len(acc))


def _sdof(dt=0.005, accel=None, **th_kw):
    mdl = BuildingModel(name="sdof")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.set_stories([L_COL])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.3, 0.3))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L_COL),
                   story="Story1", uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.supports.append(PointSupport((0, 0, L_COL), (0, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, L_COL), mx=M_SDOF))
    mdl.num_modes = 1
    mdl.add_th_case("TH", "X", accel if accel is not None else _pulse(dt),
                    dt, **th_kw)
    return mdl


K1, K2, FY_ISO = 2000.0, 200.0, 20.0
H_ISO, B_BLK, M_BLK = 0.3, 4.0, 50.0


def _isolated(**th_kw):
    """Stiff beam ring on 4 bilinear isolators (twoNodeLink, vertical)."""
    mdl = BuildingModel(name="iso")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 2
    mdl.add_material(Material("STIFF", E=2e11, nu=0.3))
    mdl.add_section(FrameSection.rectangular("BM", "STIFF", 0.5, 0.5))
    mdl.set_stories([H_ISO])
    pts = [(0, 0), (B_BLK, 0), (B_BLK, B_BLK), (0, B_BLK)]
    for i in range(4):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % 4]
        mdl.add_member("beam", "BM", (x1, y1, H_ISO), (x2, y2, H_ISO),
                       story="Story1", uid=f"B{i + 1}")
    for i, (x, y) in enumerate(pts):
        mdl.add_link((x, y, 0.0), (x, y, H_ISO), link_type="isolator",
                     params={"k1": K1, "k2": K2, "Fy": FY_ISO},
                     uid=f"I{i + 1}")
        mdl.nodal_masses.append(NodalMass((x, y, H_ISO), mx=M_BLK / 4,
                                          my=M_BLK / 4))
    dt = 0.01
    acc = [3.0 * math.sin(2 * math.pi * k * dt / 1.0)
           for k in range(300)] + [0.0] * 300
    mdl.add_th_case("TH", "X", acc, dt, damping=0.02, **th_kw)
    return mdl


ALL_REQ = {"joints": [[0, 0, L_COL], [0, 0, 0]], "frames": ["C1"],
           "hinges": True}


@pytest.fixture(scope="module")
def sdof_runs():
    base = OpenSeesEngine(_sdof()).run_time_history("TH")
    req = OpenSeesEngine(_sdof(output_requests=ALL_REQ)
                         ).run_time_history("TH")
    return base, req


# --------------------------------------------------------------------------- #
# 1. model layer
# --------------------------------------------------------------------------- #
def test_output_requests_round_trip_and_canonical_form():
    mdl = _isolated(output_requests={"joints": [[0, 0, 0.3]],
                                     "links": ["I1"], "frames": ["B1"],
                                     "hinges": False})
    d = mdl.to_dict()
    assert d["th_cases"]["TH"]["output_requests"] == {
        "joints": [[0.0, 0.0, 0.3]], "links": ["I1"], "frames": ["B1"],
        "hinges": False}
    d2 = BuildingModel.from_dict(json.loads(json.dumps(d))).to_dict()
    assert d2["th_cases"]["TH"] == d["th_cases"]["TH"]
    d3 = BuildingModel.from_dict(json.loads(json.dumps(d2))).to_dict()
    assert json.dumps(d3, sort_keys=True) == json.dumps(d2, sort_keys=True)


def test_default_absent_is_byte_identical_model_dict():
    mdl = _sdof()
    td = mdl.to_dict()["th_cases"]["TH"]
    assert "output_requests" not in td
    assert mdl.th_cases["TH"].output_requests is None
    once = BuildingModel.from_dict(mdl.to_dict()).to_dict()
    assert "output_requests" not in once["th_cases"]["TH"]
    again = BuildingModel.from_dict(once).to_dict()
    assert json.dumps(again, sort_keys=True) == json.dumps(
        once, sort_keys=True)


@pytest.mark.parametrize("req, msg", [
    ({"joints": [[0, 0]]}, "x, y, z"),
    ({"links": ["NOPE"]}, "unknown link"),
    ({"frames": ["NOPE"]}, "unknown member"),
    ({"hinges": 1}, "true/false"),
    ({"bogus": []}, "unknown key"),
    ([1, 2], "object"),
])
def test_output_requests_validation(req, msg):
    with pytest.raises(ValueError, match=msg):
        _isolated(output_requests=req)


def test_joint_not_at_a_node_is_an_engine_error():
    mdl = _sdof(output_requests={"joints": [[1.0, 1.0, 1.0]]})
    with pytest.raises(ValueError, match="no FE node"):
        OpenSeesEngine(mdl).run_time_history("TH")


def test_joint_snaps_to_node_within_display_roundoff(sdof_runs):
    """A point typed in kip-in carries ~1e-6 m round-off: it snaps to the
    node within 1 mm and records the identical series."""
    _base, exact = sdof_runs
    off = [[0.0, 0.0, L_COL + 6.4e-6]]
    res = OpenSeesEngine(_sdof(output_requests={"joints": off})
                         ).run_time_history("TH")
    j = res.extra["plot_functions"]["joints"][0]
    assert j["point"] == off[0]
    assert j["disp"] == exact.extra["plot_functions"]["joints"][0]["disp"]
    far = _sdof(output_requests={"joints": [[0.0, 0.0, L_COL + 2e-3]]})
    with pytest.raises(ValueError, match="within 1 mm"):
        OpenSeesEngine(far).run_time_history("TH")


# --------------------------------------------------------------------------- #
# 2. recording
# --------------------------------------------------------------------------- #
def test_defaults_byte_identical_results(sdof_runs):
    base, req = sdof_runs
    db, dr = base.to_dict(), req.to_dict()
    assert "plot_functions" not in db
    pf = dr.pop("plot_functions")
    assert pf
    # recording never perturbs the legacy result keys
    assert json.dumps(dr, sort_keys=True) == json.dumps(db, sort_keys=True)


def test_series_lengths_and_time_axis(sdof_runs):
    _base, res = sdof_runs
    pf = res.extra["plot_functions"]
    n = len(res.t) + 1
    dt = res.t[0]
    assert len(pf["t"]) == n
    assert pf["t"][0] == 0.0
    assert pf["t"][-1] == pytest.approx(res.t[-1], abs=1e-12)
    assert np.allclose(np.diff(pf["t"]), dt, atol=1e-12)
    j = pf["joints"][0]
    for grp in ("disp", "vel", "acc"):
        assert set(j[grp]) == {"UX", "UY", "UZ", "RX", "RY", "RZ"}
        assert all(len(v) == n for v in j[grp].values())
    for grp in ("disp_abs", "vel_abs", "acc_abs"):
        assert all(len(v) == n for v in j[grp].values())
    for grp in ("acc", "vel", "disp"):
        assert all(len(v) == n for v in pf["ground"][grp].values())
    fr = pf["frames"]["C1"]
    assert len(fr) == 12 and all(len(v) == n for v in fr.values())
    assert pf["hinges"] == {}         # elastic case: no hinge springs


def test_joint_relative_disp_equals_story_and_base_joint(sdof_runs):
    _base, res = sdof_runs
    pf = res.extra["plot_functions"]
    tip, foot = pf["joints"]
    assert tip["disp"]["UX"][0] == 0.0
    assert np.allclose(tip["disp"]["UX"][1:], res.story_ux["Story1"],
                       rtol=0, atol=1e-15)
    # fixed base: zero relative motion, absolute == ground
    assert max(abs(v) for v in foot["disp"]["UX"]) == 0.0
    assert foot["acc_abs"]["UX"] == pf["ground"]["acc"]["UX"]
    assert foot["disp_abs"]["UX"] == pf["ground"]["disp"]["UX"]
    acc = _pulse(0.005)
    assert pf["ground"]["acc"]["UX"][:len(acc)] == [float(a) for a in acc]
    assert pf["ground"]["acc"]["UY"] == [0.0] * len(pf["t"])


def test_ground_kinematics_exact_for_constant_accel():
    a0, dt = 1.5, 0.01
    v, u = pfn.integrate_linear_accel([a0] * 101, dt)
    t = np.arange(101) * dt
    assert np.allclose(v, a0 * t, rtol=0, atol=1e-13)
    assert np.allclose(u, 0.5 * a0 * t * t, rtol=0, atol=1e-13)


def _col_shear(fr, end="i"):
    """The column end shear series carrying the global-X response."""
    a, b = np.array(fr[f"V2_{end}"]), np.array(fr[f"V3_{end}"])
    return a if np.max(np.abs(a)) >= np.max(np.abs(b)) else b


def test_undamped_sdof_equilibrium_shear_vs_abs_accel():
    mdl = _sdof(di_damping=NO_DAMP,
                output_requests={"joints": [[0, 0, L_COL]],
                                 "frames": ["C1"]})
    res = OpenSeesEngine(mdl).run_time_history("TH")
    pf = res.extra["plot_functions"]
    a_abs = np.array(pf["joints"][0]["acc_abs"]["UX"])
    v_rel = np.array(pf["joints"][0]["vel"]["UX"])
    V = _col_shear(pf["frames"]["C1"], "j")
    # mass equilibrium (no damping): m a_abs + V = 0 at every sample
    assert np.max(np.abs(a_abs)) > 1.0
    assert np.allclose(np.abs(V), M_SDOF * np.abs(a_abs),
                       rtol=0, atol=1e-7 * np.max(np.abs(V)))
    # absolute velocity = relative + ground
    vg = np.array(pf["ground"]["vel"]["UX"])
    assert np.allclose(pf["joints"][0]["vel_abs"]["UX"], v_rel + vg,
                       atol=1e-15)


def test_frame_end_forces_match_stiffness(sdof_runs):
    """Fixed-guided column: V = k u (k = m w^2 from the engine's own
    modal period = 12EI/L^3) and M_base = V L / 2 at every sample."""
    _base, res = sdof_runs
    pf = res.extra["plot_functions"]
    T = OpenSeesEngine(_sdof()).run_modal().periods[0]
    k = M_SDOF * (2 * math.pi / T) ** 2
    u = np.abs(np.array(pf["joints"][0]["disp"]["UX"]))
    V = np.abs(_col_shear(pf["frames"]["C1"], "i"))
    assert u.max() > 1e-3
    assert np.allclose(V, k * u, rtol=0, atol=1e-6 * V.max())
    fr = pf["frames"]["C1"]
    mom = [np.abs(np.array(fr[c])) for c in ("M2_i", "M3_i")]
    assert any(np.allclose(m, V * L_COL / 2.0, rtol=0,
                           atol=1e-6 * V.max() * L_COL) for m in mom)


def test_link_hysteresis_area_equals_energy_and_elastic_slope():
    mdl = _isolated(energy=True,
                    output_requests={"links": ["I1", "I2", "I3", "I4"],
                                     "joints": [[0, 0, H_ISO]]})
    res = OpenSeesEngine(mdl).run_time_history("TH")
    d = res.to_dict()
    pf = d["plot_functions"]
    e_h = 0.0
    for uid, lk in pf["links"].items():
        assert lk["type"] == "isolator"
        assert lk["components"] == ["P", "V2", "V3"]
        k0 = {"P": ISOLATOR_DEFAULT_KV, "V2": K1, "V3": K1}
        stored = sum(0.5 * lk["force"][c][-1] ** 2 / k0[c]
                     for c in lk["components"])
        # independent trapezoidal loop area from the recorded series
        area = 0.0
        F = np.array([lk["force"][c] for c in lk["components"]]).T
        D = np.array([lk["deformation"][c] for c in lk["components"]]).T
        for i in range(1, len(F)):
            area += float(np.dot(0.5 * (F[i] + F[i - 1]), D[i] - D[i - 1]))
        assert lk["work"][-1] == pytest.approx(area, rel=1e-12)
        e_h += area - stored
        assert len(lk["work"]) == len(pf["t"])
    assert e_h > 1.0                                  # really yielded
    assert e_h == pytest.approx(d["energy"]["hysteretic"][-1], rel=1e-9)
    # elastic branch: the first (sub-yield) samples follow k1
    lk = pf["links"]["I1"]
    shear = [c for c in ("V2", "V3")
             if max(abs(x) for x in lk["force"][c]) > 1.0][0]
    F, D = lk["force"][shear], lk["deformation"][shear]
    i = next(k for k in range(1, len(F)) if abs(F[k]) > 0.2 * FY_ISO)
    assert F[i] / D[i] == pytest.approx(K1, rel=1e-6)
    # the max force exceeds Fy only along the post-yield branch
    assert max(abs(x) for x in F) > FY_ISO


def test_hinge_series_peak_matches_hinge_rotations():
    dt, td, A = 0.002, 0.25, 4.0
    n_p = int(round(td / dt))
    acc = [A * math.sin(math.pi * k * dt / td)
           for k in range(n_p + 1)] + [0.0] * 400
    mdl = BuildingModel(name="ep")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.set_stories([L_COL])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.3, 0.3))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L_COL),
                   story="Story1", uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, L_COL), mx=10.0))
    mdl.pattern("G", "other").nodal_loads.append(
        NodalLoad((0, 0, L_COL), fz=-50.0))
    mdl.num_modes = 1
    mdl.add_th_case("TH", "X", acc, dt, damping=0.02, nonlinear=True,
                    gravity={"G": 1.0}, My={"C1": 40.0},
                    output_requests={"hinges": True, "frames": ["C1"]})
    res = OpenSeesEngine(mdl).run_time_history("TH")
    hz = res.extra["plot_functions"]["hinges"]
    assert list(hz) == ["C1:i"]
    rot = hz["C1:i"]["rotation"]
    peak = max(max(abs(v) for v in rot["R2"]), max(abs(v) for v in rot["R3"]))
    assert peak == pytest.approx(res.hinge_rotations["C1"], rel=1e-12)
    mom = hz["C1:i"]["moment"]
    mmax = max(max(abs(v) for v in mom["M2"]), max(abs(v) for v in mom["M3"]))
    assert mmax > 40.0                        # yielded (hardening branch)
    # frame end forces include the held gravity axial force (TOTAL values)
    P = res.extra["plot_functions"]["frames"]["C1"]["P_i"]
    assert abs(P[0]) == pytest.approx(50.0, rel=1e-6)


# --------------------------------------------------------------------------- #
# 3. Nigam-Jennings response spectra
# --------------------------------------------------------------------------- #
def test_nj_step_input_matches_closed_form():
    T, z, a0, dt = 0.5, 0.05, 2.0, 0.01
    w = 2 * math.pi / T
    wd = w * math.sqrt(1 - z * z)
    acc = [a0] * 301
    u, v = pfn.sdof_history(acc, dt, T, z)
    t = np.arange(301) * dt
    ex = -(a0 / w ** 2) * (1 - np.exp(-z * w * t) * (
        np.cos(wd * t) + z / math.sqrt(1 - z * z) * np.sin(wd * t)))
    exv = -(a0 / w ** 2) * np.exp(-z * w * t) * (
        w * w / wd) * np.sin(wd * t)
    assert np.allclose(u, ex, rtol=0, atol=1e-13)
    assert np.allclose(v, exv, rtol=0, atol=1e-12)
    # undamped step: Sd = 2 a0 / w^2 (peak at t = T/2 = 25 dt, a sample)
    sp = pfn.response_spectrum(acc, dt, [T], [0.0])["spectra"][0]
    assert sp["Sd"][0] == pytest.approx(2 * a0 / w ** 2, rel=1e-12)
    assert sp["PSa"][0] == pytest.approx(2 * a0, rel=1e-12)
    assert sp["Sa"][0] == pytest.approx(2 * a0, rel=1e-12)


def test_nj_ramp_input_matches_closed_form():
    T, c, dt = 0.7, 3.0, 0.013
    w = 2 * math.pi / T
    n = 200
    t = np.arange(n) * dt
    u, v = pfn.sdof_history(c * t, dt, T, 0.0)
    ex = -(c / w ** 2) * (t - np.sin(w * t) / w)
    exv = -(c / w ** 2) * (1 - np.cos(w * t))
    assert np.allclose(u, ex, rtol=0, atol=1e-13)
    assert np.allclose(v, exv, rtol=0, atol=1e-13)


def _harmonic_exact(t, A, Om, T, z):
    """u'' + 2 z w u' + w^2 u = -A sin(Om t), u(0) = v(0) = 0."""
    w = 2 * math.pi / T
    wd = w * math.sqrt(1 - z * z)
    P0 = -A
    den = (w * w - Om * Om) ** 2 + (2 * z * w * Om) ** 2
    C = P0 * (w * w - Om * Om) / den
    D = -P0 * 2 * z * w * Om / den
    E1 = -D
    E2 = (z * w * E1 - C * Om) / wd
    return (np.exp(-z * w * t) * (E1 * np.cos(wd * t) + E2 * np.sin(wd * t))
            + C * np.sin(Om * t) + D * np.cos(Om * t))


@pytest.mark.parametrize("z", [0.0, 0.05, 0.2])
def test_nj_harmonic_converges_to_closed_form(z):
    A, Om, T, dur = 2.5, 2 * math.pi / 0.8, 0.5, 6.0
    errs = []
    for dt in (0.01, 0.005):
        t = np.arange(int(round(dur / dt)) + 1) * dt
        u, _v = pfn.sdof_history(A * np.sin(Om * t), dt, T, z)
        ex = _harmonic_exact(t, A, Om, T, z)
        errs.append(np.max(np.abs(u - ex)) / np.max(np.abs(ex)))
        sd = pfn.response_spectrum(list(A * np.sin(Om * t)), dt, [T],
                                   [z])["spectra"][0]["Sd"][0]
        assert sd == pytest.approx(np.max(np.abs(ex)), rel=5e-3)
    assert errs[1] < 1e-3
    assert errs[0] / errs[1] == pytest.approx(4.0, rel=0.1)   # O(dt^2)


def test_spectrum_shape_t0_and_pseudo_relations():
    acc = _pulse(0.01, t_pulse=0.3, amp=3.0, t_total=4.0)
    out = pfn.response_spectrum(acc, 0.01, damping=[0.02, 0.05])
    assert out["periods"][0] == 0.0 and len(out["periods"]) == 100
    assert out["pga"] == pytest.approx(max(abs(a) for a in acc))
    s2, s5 = out["spectra"]
    for s in (s2, s5):
        assert s["Sa"][0] == s["PSa"][0] == out["pga"]
        assert s["Sd"][0] == 0.0
        for T, sd, psa, psv in zip(out["periods"][1:], s["Sd"][1:],
                                   s["PSa"][1:], s["PSv"][1:]):
            w = 2 * math.pi / T
            assert psa == pytest.approx(w * w * sd, rel=1e-12)
            assert psv == pytest.approx(w * sd, rel=1e-12)
    # more damping -> smaller spectral peak overall
    assert max(s2["Sa"][1:]) > max(s5["Sa"][1:])
    with pytest.raises(ValueError):
        pfn.response_spectrum(acc, 0.01, damping=[1.2])
    with pytest.raises(ValueError):
        pfn.response_spectrum(acc, -1.0)


def test_spectrum_vs_sdof_engine_peak_displacement():
    """The SkyFrame SDOF (Newmark average acceleration, Rayleigh zeta at
    its single mode) peak displacement == the NJ spectral Sd at T."""
    dt, z = 0.002, 0.05
    acc = _pulse(dt, t_pulse=0.3, amp=2.0, t_total=3.0)
    eng = OpenSeesEngine(_sdof(dt=dt, accel=acc, damping=z))
    T = eng.run_modal().periods[0]
    res = eng.run_time_history("TH")
    peak = max(abs(u) for u in res.story_ux["Story1"])
    sd = pfn.response_spectrum(acc, dt, [T], [z])["spectra"][0]["Sd"][0]
    # Newmark period elongation (pi^2/12)(dt/T)^2 < 1e-4 here
    assert sd == pytest.approx(peak, rel=2e-3)


def test_floor_spectrum_at_fixed_base_equals_ground_spectrum(sdof_runs):
    _base, res = sdof_runs
    pf = res.extra["plot_functions"]
    fl = pfn.spectrum_dict_from_results(pf, [0, 0, 0], "X", [0.05])
    gr = pfn.response_spectrum(_pulse(0.005) + [0.0], 0.005, None, [0.05])
    assert fl["spectra"][0]["Sa"] == gr["spectra"][0]["Sa"]
    assert fl["spectra"][0]["Sd"] == gr["spectra"][0]["Sd"]
    gr2 = pfn.spectrum_dict_from_results(pf, "ground", "X", [0.05])
    assert gr2["spectra"] == fl["spectra"]
    # the roof floor spectrum amplifies near the structure's period
    roof = pfn.spectrum_dict_from_results(pf, [0, 0, L_COL], "X", [0.05])
    assert max(roof["spectra"][0]["Sa"]) > max(gr["spectra"][0]["Sa"])


# --------------------------------------------------------------------------- #
# 4. API
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client():
    from skyframe.api import server as srv
    app = srv.create_app()
    app.config["TESTING"] = True
    srv._state["model"] = srv.quick_building()
    with app.test_client() as c:
        yield c
    srv._state["model"] = srv.quick_building()


def test_api_round_trip_analyze_and_spectrum(client):
    mdl = _sdof(output_requests={"joints": [[0, 0, L_COL]]})
    d = mdl.to_dict()
    r = client.post("/api/model", json=d)
    assert r.status_code == 200
    assert r.get_json()["th_cases"]["TH"]["output_requests"] == \
        d["th_cases"]["TH"]["output_requests"]
    r = client.post("/api/analyze")
    assert r.status_code == 200
    pf = r.get_json()["th_cases"]["TH"]["plot_functions"]
    assert len(pf["joints"]) == 1
    body = {"case": "TH", "point": [0, 0, L_COL], "damping": [0.02, 0.05],
            "periods": [0.0, 0.1, 0.5, 1.0]}
    r = client.post("/api/plotfn/spectrum", json=body)
    assert r.status_code == 200
    j = r.get_json()
    assert j["source"] == "recorded" and j["direction"] == "X"
    assert [s["damping"] for s in j["spectra"]] == [0.02, 0.05]
    assert len(j["spectra"][0]["Sa"]) == 4
    # a joint not recorded -> transparent re-run, model untouched
    r2 = client.post("/api/plotfn/spectrum",
                     json={"case": "TH", "point": [0, 0, 0],
                           "damping": 0.05})
    j2 = r2.get_json()
    assert r2.status_code == 200 and j2["source"] == "rerun"
    g = client.post("/api/plotfn/spectrum",
                    json={"case": "TH", "point": "ground", "damping": 0.05})
    assert g.get_json()["spectra"][0]["Sa"] == j2["spectra"][0]["Sa"]
    m_after = client.get("/api/model").get_json()
    assert m_after["th_cases"]["TH"]["output_requests"] == {
        "joints": [[0.0, 0.0, L_COL]]}
    # raw series mode
    s = client.post("/api/plotfn/spectrum",
                    json={"accel": [0.0, 1.0, 0.0], "dt": 0.01,
                          "periods": [0.0]})
    assert s.status_code == 200 and s.get_json()["pga"] == 1.0
    for bad in ({"case": "NOPE"}, {"case": "TH", "point": [0, 0]},
                {"case": "TH", "damping": 2.0},
                {"case": "TH", "direction": "Q"}, {"accel": [1.0]}):
        assert client.post("/api/plotfn/spectrum",
                           json=bad).status_code == 400
