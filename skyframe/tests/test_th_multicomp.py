"""Multi-component ground motion + load-pattern time histories (gap A6).

Expected numbers are derived in the test from first principles:

* linearity: two components (or a rotated one) == the vector sum of the
  legacy single-direction runs, for direct integration AND modal (FNA);
* UZ on an axial mass-spring column (k = EA/L, read back from the
  engine's own modal period) == an independent numpy Newmark
  average-acceleration integration of the SDOF;
* harmonic pattern load ``p0 sin(W t)`` on the lateral SDOF == the
  closed-form steady-state + transient solution (and == numpy Newmark
  of the same scheme to round-off);
* FNA with pattern loads == direct integration on a linear frame with
  the full modal basis;
* energy: input energy includes the pattern-load work (== independent
  trapezoidal sum of P du), balance error << 1 %.

Units: kN, m, tonne, s.
"""

import json
import math
import warnings

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core import th_components as thc  # noqa: E402
from skyframe.core.model import (  # noqa: E402
    BuildingModel,
    FrameSection,
    Material,
    MemberLoad,
    NodalLoad,
    NodalMass,
    PointSupport,
    StoryForce,
)

E = 25_000_000.0
DT = 0.01
L = 3.0


def _rec(n=300, dt=DT, T=0.4, amp=3.0, n_on=80):
    return [0.0] + [amp * math.sin(2 * math.pi * k * dt / T)
                    for k in range(1, n_on)] + [0.0] * (n - n_on)


def _rec2(n=300, dt=DT):
    return [0.0] + [1.5 * math.sin(2 * math.pi * k * dt / 0.27)
                    * math.exp(-k * dt) for k in range(1, 120)] \
        + [0.0] * (n - 120)


def _frame(components=None, direction="X", scale=1.0, **kw):
    """2-story 1x1-bay symmetric 3D frame, rigid diaphragms."""
    B = 6.0
    mdl = BuildingModel(name="frame")
    mdl.add_material(Material("CONC", E=E, nu=0.2))
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
    mdl.num_modes = 6                  # = every massed dof (full basis)
    mdl.add_th_function("REC", _rec(), DT)
    mdl.add_th_function("REC2", _rec2(), DT)
    mdl.add_th_function("SIN", [math.sin(2 * math.pi * k * DT / 0.5)
                                for k in range(250)], DT)
    pat = mdl.pattern("BLAST", "other")
    pat.story_forces.append(StoryForce("Story2", fx=40.0, fy=-15.0))
    pat.story_forces.append(StoryForce("Story1", fx=10.0))
    mdl.add_th_case("TH", direction, _rec(), DT, scale=scale,
                    components=components, **kw)
    return mdl


def _sdof(components=None, accel=None, dt=DT, **kw):
    """Lateral SDOF: cantilever column, top free in ux only, tip mass."""
    mdl = BuildingModel(name="sdof")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E, nu=0.2))
    mdl.set_stories([L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.3, 0.3))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L),
                   story="Story1", uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1,) * 6))
    mdl.supports.append(PointSupport((0, 0, L), (0, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=10.0))
    mdl.num_modes = 1
    mdl.pattern("P", "other").nodal_loads.append(
        NodalLoad((0, 0, L), fx=1.0))
    mdl.pattern("W", "other").member_loads.append(
        MemberLoad("C1", "udl", 4.0, direction="global_x"))
    acc = accel if accel is not None else _rec()
    mdl.add_th_case("TH", "X", acc, dt, components=components, **kw)
    return mdl


def _vertical(components, mass_opts=None, **kw):
    """Axial mass-spring column: top carries mz only (UZ = the one
    massed dof; the massless lateral/rotation dofs stay unexcited)."""
    mdl = BuildingModel(name="vert")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E, nu=0.2))
    mdl.set_stories([L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.02, 0.02))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L),
                   story="Story1", uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1,) * 6))
    mdl.nodal_masses.append(NodalMass((0, 0, L), mz=2.0))
    if mass_opts is not None:
        mdl.mass_options = dict(mass_opts)
    mdl.num_modes = 1
    mdl.add_th_function("REC", _rec(T=0.05, amp=2.0), DT)
    mdl.add_th_case("TH", "X", [], 0.0, components=components, **kw)
    return mdl


def _newmark(m, k, c, p, dt):
    """Average-acceleration Newmark; p[k] at t = k*dt; returns u at
    t = (k+1)*dt for k = 0..n-1 (the engine's sampling)."""
    n = len(p)
    u = v = a = 0.0
    out = []
    keff = k + 2.0 * c / dt + 4.0 * m / dt ** 2
    for i in range(n):
        p1 = p[i + 1] if i + 1 < n else 0.0
        rhs = p1 + m * (4 / dt ** 2 * u + 4 / dt * v + a) \
            + c * (2 / dt * u + v)
        u1 = rhs / keff
        v1 = 2 * (u1 - u) / dt - v
        a = 4 * (u1 - u) / dt ** 2 - 4 * v / dt - a
        u, v = u1, v1
        out.append(u)
    return np.array(out)


def _sdof_props(mdl, zeta=0.05):
    T = OpenSeesEngine(mdl).run_modal().periods[0]
    w = 2 * math.pi / T
    return w, 2.0 * zeta * w          # w, c/m


def _arr(d):
    return {k: np.array(v) for k, v in d.items()}


def _close(a, b, rel=1e-9):
    a, b = np.asarray(a, float), np.asarray(b, float)
    scale = max(np.max(np.abs(b)), 1e-30)
    assert np.max(np.abs(a - b)) <= rel * scale, \
        (np.max(np.abs(a - b)), scale)


def _di(mdl):
    return OpenSeesEngine(mdl).run_time_history("TH")


def _fna(mdl):
    return OpenSeesEngine(mdl).run_fna("TH")


RUNNERS = {"direct": _di, "modal": _fna}


@pytest.fixture(scope="module")
def legacy():
    """Legacy single-direction runs of REC (and REC2) in X and Y."""
    out = {}
    for name, run in RUNNERS.items():
        for d in ("X", "Y"):
            out[(name, d)] = run(_frame(direction=d))
            m2 = _frame(direction=d)
            m2.th_cases["TH"].accel = _rec2()
            out[(name, d, "2")] = run(m2)
    return out


# --------------------------------------------------------------------------- #
# defaults / identity
# --------------------------------------------------------------------------- #
def test_default_absent_components_dict_unchanged():
    mdl = _frame()
    td = mdl.th_cases["TH"].to_dict()
    assert "components" not in td
    assert mdl.th_cases["TH"].components is None
    m2 = BuildingModel.from_dict(json.loads(json.dumps(mdl.to_dict())))
    assert m2.th_cases["TH"].components is None
    assert m2.to_dict() == mdl.to_dict()
    res = _di(mdl)
    assert "multi_component" not in res.to_dict()


@pytest.mark.parametrize("runner", ["direct", "modal"])
@pytest.mark.parametrize("d", ["X", "Y"])
def test_single_component_reproduces_legacy_bitwise(runner, d):
    """One unrotated component naming the same record == legacy run,
    exactly (same samples, same factor, same patterns semantics)."""
    run = RUNNERS[runner]
    a = run(_frame(direction=d, scale=1.7))
    b = run(_frame(direction=d, scale=1.7, components=[
        {"direction": "U" + d, "function": "REC"}]))
    da, db = a.to_dict(), b.to_dict()
    db.pop("multi_component")
    if runner == "direct":
        assert da == db
        return
    # modal: identical response series; the cross-direction base series
    # is assembled from L_y (round-off ~1e-14) instead of hard zeros
    assert da["story_ux"] == db["story_ux"]
    assert da["story_uy"] == db["story_uy"]
    peak = max(np.max(np.abs(da["base_FX"])), np.max(np.abs(da["base_FY"])))
    for key in ("base_FX", "base_FY"):
        diff = np.abs(np.array(db[key]) - np.array(da[key]))
        assert np.max(diff) <= 1e-12 * peak


def test_single_component_nonlinear_and_energy_bitwise():
    def mk(comp):
        m = _sdof(components=comp, damping=0.02, energy=True,
                  nonlinear=True, My={"C1": 30.0})
        return m
    a = _di(mk(None))
    b = _di(mk([{"direction": "UX"}]))      # case's own inline record
    da, db = a.to_dict(), b.to_dict()
    db.pop("multi_component")
    assert da == db
    assert a.yielded == ["C1"]


# --------------------------------------------------------------------------- #
# linearity: 0 + 90 deg, rotated components
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("runner", ["direct", "modal"])
def test_two_components_0_and_90_equal_vector_sum(runner, legacy):
    run = RUNNERS[runner]
    res = run(_frame(components=[
        {"direction": "UX", "function": "REC", "angle_deg": 0.0},
        {"direction": "UX", "function": "REC2", "angle_deg": 90.0}]))
    rx, ry = legacy[(runner, "X")], legacy[(runner, "Y", "2")]
    for s in ("Story1", "Story2"):
        _close(res.story_ux[s], np.array(rx.story_ux[s])
               + np.array(ry.story_ux[s]))
        _close(res.story_uy[s], np.array(rx.story_uy[s])
               + np.array(ry.story_uy[s]))
    _close(res.base_FX, np.array(rx.base_FX) + np.array(ry.base_FX))
    _close(res.base_FY, np.array(rx.base_FY) + np.array(ry.base_FY))
    # symmetric model: X and Y runs are uncoupled
    assert np.max(np.abs(rx.story_uy["Story2"])) < 1e-9 * np.max(
        np.abs(rx.story_ux["Story2"]))


@pytest.mark.parametrize("runner", ["direct", "modal"])
@pytest.mark.parametrize("theta", [30.0, 135.0])
def test_rotated_component_equals_cos_x_plus_sin_y(runner, theta, legacy):
    run = RUNNERS[runner]
    res = run(_frame(components=[
        {"direction": "UX", "function": "REC", "angle_deg": theta}]))
    c, s_ = math.cos(math.radians(theta)), math.sin(math.radians(theta))
    rx, ry = legacy[(runner, "X")], legacy[(runner, "Y")]
    for st in ("Story1", "Story2"):
        _close(res.story_ux[st], c * np.array(rx.story_ux[st])
               + s_ * np.array(ry.story_ux[st]))
        _close(res.story_uy[st], c * np.array(rx.story_uy[st])
               + s_ * np.array(ry.story_uy[st]))
    _close(res.base_FY, c * np.array(rx.base_FY)
           + s_ * np.array(ry.base_FY))
    comp = res.extra["multi_component"]["components"][0]
    assert comp["cosines"][:2] == pytest.approx([c, s_], abs=1e-15)


def test_rotated_uy_component_direction(legacy):
    theta = 30.0
    res = _di(_frame(components=[
        {"direction": "UY", "function": "REC", "angle_deg": theta,
         "scale": 2.0}]))
    c, s_ = math.cos(math.radians(theta)), math.sin(math.radians(theta))
    rx, ry = legacy[("direct", "X")], legacy[("direct", "Y")]
    _close(res.story_ux["Story2"], 2 * (-s_ * np.array(rx.story_ux["Story2"])
                                        + c * np.array(ry.story_ux["Story2"])))
    _close(res.story_uy["Story2"], 2 * (-s_ * np.array(rx.story_uy["Story2"])
                                        + c * np.array(ry.story_uy["Story2"])))


def test_time_shift_equals_padded_record():
    shift_n = 7
    res = _di(_frame(components=[
        {"direction": "UX", "function": "REC", "time_shift": shift_n * DT}]))
    m = _frame()
    m.th_cases["TH"].accel = [0.0] * shift_n + _rec()
    ref = _di(m)
    assert len(res.t) == len(ref.t) == 300 + shift_n
    _close(res.story_ux["Story2"], ref.story_ux["Story2"], 1e-12)
    _close(res.base_FX, ref.base_FX, 1e-12)


def test_grid_uses_smallest_dt_and_linear_resampling():
    mdl = _frame()
    mdl.add_th_function("COARSE", [0.0, 1.0, 3.0, 0.0], 0.02)
    th = mdl.th_cases["TH"]
    th.components = [{"direction": "UX", "function": "COARSE",
                      "time_shift": 0.005},
                     {"direction": "UY", "function": "REC"}]
    plan = thc.resolve(mdl, th)
    assert plan.dt == DT and plan.n == 300
    gx = plan.ground_series(1)
    # COARSE shifted by 0.005 s at t = 0, .01, .02, .03, .04, .05, .06, .07
    assert gx[:8] == pytest.approx([0, 0.25, 0.75, 1.5, 2.5, 2.25, 0.75, 0])
    assert gx[8:] == [0.0] * 292


# --------------------------------------------------------------------------- #
# vertical component
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("runner", ["direct", "modal"])
def test_uz_component_matches_sdof_newmark(runner):
    comps = [{"direction": "UZ", "function": "REC", "scale": 0.5}]
    mdl = _vertical(comps, mass_opts={"include_vertical": True})
    w, c_m = _sdof_props(mdl)
    m = 2.0
    k = m * w * w
    assert k == pytest.approx(E * 0.02 * 0.02 / L, rel=1e-9)
    with warnings.catch_warnings():
        warnings.simplefilter("error")      # vertical mass present: silent
        res = RUNNERS[runner](mdl)
    ag = 0.5 * np.array(_rec(T=0.05, amp=2.0))
    u = _newmark(m, k, m * c_m, -m * ag, DT)
    fz = np.array(res.extra["multi_component"]["base_FZ"])
    _close(-fz / k, u, 1e-8)
    assert np.max(np.abs(res.base_FX)) < 1e-9 * np.max(np.abs(fz))


def test_uz_without_vertical_mass_warns_and_has_no_effect():
    with pytest.warns(UserWarning, match="no vertical"):
        res = _di(_frame(components=[{"direction": "UZ",
                                      "function": "REC"}]))
    assert max(abs(v) for v in res.extra["multi_component"]["base_FZ"]) \
        < 1e-9
    assert res.peaks["story"]["Story2"]["ux"] < 1e-12


def test_uz_with_include_vertical_false_warns_explicit_mass_only():
    mdl = _vertical([{"direction": "UZ", "function": "REC"}])
    with pytest.warns(UserWarning, match="include_vertical = false"):
        res = _di(mdl)
    assert max(abs(v) for v in res.extra["multi_component"]["base_FZ"]) > 0


# --------------------------------------------------------------------------- #
# load-pattern components
# --------------------------------------------------------------------------- #
def _harmonic(t, p0, k, w, zeta, W):
    r = W / w
    D = (1 - r * r) ** 2 + (2 * zeta * r) ** 2
    st = p0 / k
    up = st * ((1 - r * r) * np.sin(W * t) - 2 * zeta * r * np.cos(W * t)) / D
    up0 = -st * 2 * zeta * r / D
    vp0 = st * (1 - r * r) * W / D
    wd = w * math.sqrt(1 - zeta * zeta)
    A = -up0
    Bc = (zeta * w * A - vp0) / wd
    return up + np.exp(-zeta * w * t) * (A * np.cos(wd * t)
                                         + Bc * np.sin(wd * t))


@pytest.mark.parametrize("runner", ["direct", "modal"])
def test_harmonic_pattern_load_matches_closed_form(runner):
    dt, n, W, p0 = 0.001, 1500, 18.0, 6.0
    mdl = _sdof(accel=[0.0], dt=dt)
    mdl.add_th_function("SINW", [math.sin(W * k * dt) for k in range(n)],
                        dt)
    mdl.th_cases["TH"].components = [
        {"pattern": "P", "function": "SINW", "scale": p0}]
    w, c_m = _sdof_props(mdl)
    m = 10.0
    k = m * w * w
    res = RUNNERS[runner](mdl)
    u = np.array(res.story_ux["Story1"])
    t = np.array(res.t)
    exact = _harmonic(t, p0, k, w, 0.05, W)
    assert len(u) == n
    # converged scheme vs closed form (steady state + transient)
    assert np.max(np.abs(u - exact)) < 0.01 * np.max(np.abs(exact))
    # and identical to the same Newmark scheme by hand
    p = p0 * np.sin(W * np.arange(n) * dt)
    _close(u, _newmark(m, k, m * c_m, p, dt), 1e-9)


def test_fna_pattern_load_equals_direct_integration():
    comps = [{"pattern": "BLAST", "function": "SIN", "scale": 2.0}]
    a = _di(_frame(components=comps))
    b = _fna(_frame(components=comps))
    for s in ("Story1", "Story2"):
        _close(b.story_ux[s], a.story_ux[s], 1e-7)
        _close(b.story_uy[s], a.story_uy[s], 1e-7)
    _close(b.base_FX, a.base_FX, 1e-7)
    _close(b.base_FY, a.base_FY, 1e-7)
    for s in ("Story1", "Story2"):
        for key in ("shear_x", "shear_y", "drift_x"):
            assert b.peaks["story"][s][key] == pytest.approx(
                a.peaks["story"][s][key], rel=1e-6)


@pytest.mark.parametrize("runner", ["direct", "modal"])
def test_mixed_ground_and_pattern_is_superposition(runner):
    run = RUNNERS[runner]
    g = {"direction": "UX", "function": "REC", "angle_deg": 20.0}
    p = {"pattern": "BLAST", "function": "SIN", "scale": 0.5,
         "time_shift": 0.1}
    both = run(_frame(components=[g, p]))
    # zero-scale partners keep the common run length (300 steps)
    rg = run(_frame(components=[g, dict(p, scale=0.0)]))
    rp = run(_frame(components=[dict(g, scale=0.0), p]))
    for s in ("Story1", "Story2"):
        _close(both.story_ux[s], np.array(rg.story_ux[s])
               + np.array(rp.story_ux[s]))
    _close(both.base_FX, np.array(rg.base_FX) + np.array(rp.base_FX))


def test_pattern_story_shear_includes_applied_loads():
    """Quasi-static slow pattern load: top-story shear -> applied force."""
    dt, n = 0.01, 400
    mdl = _frame()
    mdl.add_th_function("RAMP", [min(1.0, k / 300.0) for k in range(n)],
                        dt)
    mdl.th_cases["TH"].components = [{"pattern": "BLAST",
                                      "function": "RAMP"}]
    res = _di(mdl)
    pk = res.peaks["story"]
    assert pk["Story2"]["shear_x"] == pytest.approx(40.0, rel=0.03)
    assert pk["Story1"]["shear_x"] == pytest.approx(50.0, rel=0.03)
    assert res.peaks["base"]["FX"] == pytest.approx(50.0, rel=0.03)


def test_pattern_with_ground_displacement_rejected():
    mdl = _sdof()
    mdl.add_th_function("F", [0.0, 1.0], DT)
    mdl.th_cases["TH"].components = [{"pattern": "P", "function": "F"}]
    mdl.patterns["P"].ground_displacements = [object()]
    with pytest.raises(ValueError, match="ground displacements"):
        _di(mdl)


# --------------------------------------------------------------------------- #
# energy with pattern loads
# --------------------------------------------------------------------------- #
NO_DAMP = {"type": "rayleigh_coefficients", "mass_coeff": 0.0,
           "stiffness_coeff": 0.0}


def test_energy_input_is_pattern_work_undamped_sdof():
    mdl = _sdof(accel=[0.0], di_damping=NO_DAMP, energy=True)
    mdl.add_th_function("SINW", [math.sin(15.0 * k * DT) for k in range(200)],
                        DT)
    mdl.th_cases["TH"].components = [
        {"pattern": "P", "function": "SINW", "scale": 3.0}]
    res = _di(mdl)
    en = res.extra["energy"]
    u = np.concatenate([[0.0], res.story_ux["Story1"]])
    p = 3.0 * np.array([math.sin(15.0 * k * DT) for k in range(200)]
                       + [0.0])
    work = np.cumsum(0.5 * (p[1:] + p[:-1]) * np.diff(u))
    _close(en["input"], work, 1e-9)
    assert en["max_abs_error_normalized"] < 1e-9
    w, _ = _sdof_props(mdl)
    k = 10.0 * w * w
    _close(en["strain"], 0.5 * k * u[1:] ** 2, 1e-8)


def test_energy_element_load_pattern_counts_as_input():
    """Member UDL (element load) pattern: input == work of the
    equivalent nodal force w L/2, strain == 0.5 k u^2."""
    mdl = _sdof(accel=[0.0], di_damping=NO_DAMP, energy=True)
    mdl.add_th_function("SINW", [math.sin(15.0 * k * DT) for k in range(200)],
                        DT)
    mdl.th_cases["TH"].components = [{"pattern": "W", "function": "SINW"}]
    res = _di(mdl)
    en = res.extra["energy"]
    u = np.concatenate([[0.0], res.story_ux["Story1"]])
    p = 4.0 * L / 2 * np.array([math.sin(15.0 * k * DT) for k in range(200)]
                               + [0.0])
    work = np.cumsum(0.5 * (p[1:] + p[:-1]) * np.diff(u))
    _close(en["input"], work, 1e-8)
    w, _ = _sdof_props(mdl)
    _close(en["strain"], 0.5 * 10.0 * w * w * u[1:] ** 2, 1e-8)
    assert en["max_abs_error_normalized"] < 1e-9


@pytest.mark.parametrize("integ", [None, {"method": "hht", "alpha": -0.05}])
def test_energy_balance_mixed_frame_damped(integ):
    kw = {"energy": True}
    if integ:
        kw["integration"] = integ
    res = _di(_frame(components=[
        {"direction": "UX", "function": "REC", "angle_deg": 30.0},
        {"pattern": "BLAST", "function": "SIN", "scale": 1.5}], **kw))
    en = res.extra["energy"]
    assert en["max_abs_error_normalized"] < 0.01
    if integ is None:
        assert en["max_abs_error_normalized"] < 1e-8
    assert en["damping"][-1] > 0.0
    assert len(en["input"]) == len(res.t)


# --------------------------------------------------------------------------- #
# round-trip + validation
# --------------------------------------------------------------------------- #
COMPS = [{"direction": "UX", "function": "REC", "scale": 1.0,
          "angle_deg": 15.0, "time_shift": 0.0},
         {"direction": "UZ", "function": "REC2", "scale": 0.5},
         {"pattern": "BLAST", "function": "SIN", "scale": 2.0,
          "time_shift": 0.25}]


def test_roundtrip_components():
    mdl = _frame(components=COMPS)
    d = json.loads(json.dumps(mdl.to_dict()))
    assert d["th_cases"]["TH"]["components"] == COMPS
    m2 = BuildingModel.from_dict(d)
    assert m2.th_cases["TH"].components == COMPS
    assert m2.to_dict() == mdl.to_dict()


def test_api_roundtrip_and_analyze():
    from skyframe.api.server import create_app
    client = create_app().test_client()
    mdl = _sdof()
    mdl.add_th_function("F", [0.0, 1.0, 0.0], DT)
    mdl.th_cases["TH"].components = [
        {"direction": "UX"}, {"pattern": "P", "function": "F",
                              "scale": 2.0}]
    rv = client.post("/api/model", json=mdl.to_dict())
    assert rv.status_code == 200
    assert rv.get_json()["th_cases"]["TH"]["components"][1]["pattern"] \
        == "P"
    rv = client.post("/api/analyze")
    assert rv.status_code == 200
    th = rv.get_json()["th_cases"]["TH"]
    mc = th["multi_component"]
    assert mc["n_steps"] == len(th["t"]) == 300
    assert [c.get("pattern") for c in mc["components"]] == [None, "P"]


@pytest.mark.parametrize("comps, msg", [
    ([], "non-empty"),
    (["UX"], "must be an object"),
    ([{"direction": "UW", "function": "REC"}], "UX|UY|UZ"),
    ([{"direction": "UX", "function": "NOPE"}], "not a defined"),
    ([{"pattern": "NOPE", "function": "REC"}], "not a defined load"),
    ([{"pattern": "BLAST"}], "needs a time function"),
    ([{"direction": "UZ", "function": "REC", "angle_deg": 10}],
     "horizontal"),
    ([{"direction": "UX", "function": "REC", "time_shift": -1}], ">= 0"),
    ([{"direction": "UX", "function": "REC", "scale": float("nan")}],
     "finite"),
    ([{"direction": "UX", "function": "REC", "foo": 1}], "unknown keys"),
    ([{"direction": "UX", "pattern": "BLAST", "function": "SIN"}],
     "not both"),
    ([{"pattern": "BLAST", "function": "SIN", "angle_deg": 5}],
     "unknown keys"),
])
def test_validation_rejects_bad_components(comps, msg):
    with pytest.raises(ValueError, match=msg):
        _frame(components=comps)


def test_validation_ground_component_needs_a_record():
    mdl = _frame()
    with pytest.raises(ValueError, match="no record"):
        mdl.add_th_case("T2", "X", [], 0.0,
                        components=[{"direction": "UX"}])
    # pattern-only case without any record of its own is fine
    th = mdl.add_th_case("T3", "X", [], 0.0, components=[
        {"pattern": "BLAST", "function": "SIN"}])
    assert th.components[0]["pattern"] == "BLAST"
