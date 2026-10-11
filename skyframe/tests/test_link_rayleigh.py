"""Rayleigh damping on springs, links and hinges (``damping_options``).

OpenSees zeroLength / twoNodeLink elements carry no Rayleigh term unless
built with ``-doRayleigh``; ``BuildingModel.damping_options`` decides
whether SkyFrame's springs / links / hinges take part
(:mod:`skyframe.core.damping_options`, :mod:`skyframe.engine.link_rayleigh`).

Every reference is independent of the engine:

* a column on a grounded spring (or on an elastic link) whose top is
  restrained in every dof but ux is two Kelvin elements in SERIES with the
  same ``a1`` + a massless base dof, i.e. EXACTLY the SDOF
  ``m u'' + (a0 m + a1 k) u' + k u = -m ag`` with ``k = 1/(1/kc + 1/ks)``
  (the condensation holds step by step under Newmark for zero initial
  conditions); the numpy Newmark integrator below reproduces it, and the
  free-vibration log decrement gives the case's ``xi``;
* the EPP hinged cantilever with the hinge damped on its COMMITTED tangent
  is the classical Rayleigh-damped SDOF with the same series EPP spring,
  ``c = a0 m + a1 k_t`` (``k_t`` = last converged tangent of the spring:
  ``k`` elastic, 0 yielding — in the elastic range ``c = 2 xi w m`` at the
  fitted ``w``);
* the energy balance (``E_I = E_K + E_S + E_D + E_H``) is exact to
  round-off/Newton tolerance with the springs / links / hinges damped.

Units per CONTRACT.md: kN, m, tonne, s; E in kPa.
"""

import hashlib
import json
import math

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core import damping_options as dmo  # noqa: E402
from skyframe.core.model import (  # noqa: E402
    BuildingModel,
    FrameSection,
    Material,
    NodalLoad,
    NodalMass,
    PointSupport,
)
from skyframe.engine import link_rayleigh as lrl  # noqa: E402
from skyframe.engine import thoptions as tho  # noqa: E402

E_CONC = 25_000_000.0
M_TOP = 10.0
ALL = {"rayleigh_on_links": "all", "rayleigh_stiffness_basis": "committed"}


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
def _pulse(dt, t_pulse=0.05, amp=2.0, t_total=3.0):
    n_p = int(round(t_pulse / dt))
    n = int(round(t_total / dt))
    acc = [0.0] + [amp * math.sin(math.pi * k / n_p) for k in range(1, n_p)]
    return acc + [0.0] * (n - len(acc))


def _base(name, base="spring", ks=8.0e3, opts=None, dt=0.002, accel=None,
          **th_kw):
    """Column (top: ux only, mass) on a grounded spring / elastic link /
    fixed base; zeta = 5 % Rayleigh fitted by the engine to the mode."""
    L = 3.0
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.set_stories([L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.3, 0.3))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L),
                   story="Story1", uid="C1")
    if base == "spring":
        mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
        mdl.add_spring_support((0, 0, 0), [ks, 0, 0, 0, 0, 0])
    elif base == "named_ml":                     # nonlinear-elastic curve
        mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
        mdl.add_spring_property("ML", {
            "kind": "multilinear", "k": [0, 0, 0, 0, 0, 0],
            "curves": {"U1": [[-1.0, -2.0 * ks], [-0.004, -ks * 0.004],
                              [0.0, 0.0], [0.004, ks * 0.004],
                              [1.0, 2.0 * ks]]}})
        mdl.add_spring_support((0, 0, 0), property="ML")
    elif base == "link":
        mdl.supports.append(PointSupport((0, 0, -1.0), (1, 1, 1, 1, 1, 1)))
        mdl.supports.append(PointSupport((0, 0, 0), (0, 1, 1, 1, 1, 1)))
        mdl.add_link((0, 0, -1.0), (0, 0, 0), [ks, 0, 0, 0, 0, 0],
                     uid="LK")
    else:
        mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.supports.append(PointSupport((0, 0, L), (0, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=M_TOP))
    mdl.num_modes = 1
    if opts is not None:
        mdl.damping_options = dict(opts)
    th_kw.setdefault("damping", 0.05)
    mdl.add_th_case("TH", "X", accel if accel is not None
                    else _pulse(dt), dt, **th_kw)
    return mdl


def _epp(My, opts=None, energy=False, t_end=6.0, hardening=0.0):
    """Base-hinged EPP cantilever + top mass (verification n03b layout)
    under a 2 Hz sine burst then free vibration."""
    L, E, I, mass = 3.0, 2.0e8, 1.0e-4, 20.0
    mdl = BuildingModel(name="epp")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("MAT", E=E, nu=0.3, unit_weight=0.0))
    mdl.set_stories([L])
    mdl.add_section(FrameSection("S", "MAT", 1e-2, I, I, 2 * I))
    mdl.add_member("column", "S", (0, 0, 0), (0, 0, L), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.active_dof = ["UX", "UZ", "RY"]
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=mass))
    mdl.num_modes = 1
    if opts is not None:
        mdl.damping_options = dict(opts)
    dt = 0.002
    t = np.arange(0, t_end, dt)
    acc = np.where(t < 1.5, 4.0 * np.sin(2 * math.pi * t / 0.5), 0.0)
    kw = {"energy": True} if energy else {}
    mdl.add_th_case("TH", "X", [float(a) for a in acc], dt, damping=0.02,
                    nonlinear=True, hinges="column_base", default_My=My,
                    hardening=hardening, **kw)
    kc = 3 * E * I / L ** 3
    kh = 10 * 6 * E * I / L / L ** 2
    return mdl, dict(acc=list(acc) + [0.0], dt=dt, m=mass, kc=kc, kh=kh,
                     ks=1.0 / (1.0 / kc + 1.0 / kh), Fy=My / L,
                     w0=math.sqrt(kc / mass), zeta=0.02)


def _isolated(opts=None, energy=True):
    """Mass on a vertical isolator link (bilinear Steel01, yields)."""
    mdl = BuildingModel(name="iso")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.set_stories([3.0])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.3, 0.3))
    mdl.add_member("column", "COL", (0, 0, 0.5), (0, 0, 3.0),
                   story="Story1", uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.supports.append(PointSupport((0, 0, 0.5), (0, 1, 1, 1, 1, 1)))
    mdl.supports.append(PointSupport((0, 0, 3.0), (0, 1, 1, 1, 1, 1)))
    mdl.add_link((0, 0, 0), (0, 0, 0.5), link_type="isolator",
                 params={"k1": 2.0e3, "k2": 2.0e2, "Fy": 10.0}, uid="ISO")
    mdl.nodal_masses.append(NodalMass((0, 0, 0.5), mx=5.0))
    mdl.nodal_masses.append(NodalMass((0, 0, 3.0), mx=M_TOP))
    mdl.num_modes = 2
    if opts is not None:
        mdl.damping_options = dict(opts)
    dt = 0.002
    acc = [3.0 * math.sin(2 * math.pi * k * dt / 0.8) for k in range(600)]
    acc += [0.0] * 900
    mdl.add_th_case("TH", "X", acc, dt, damping=0.05, energy=energy)
    return mdl


def _elastoplastic(opts=None, **th_kw):
    """test_th_options' yielding hinge model (gravity + hardening 0.02)."""
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
    if opts is not None:
        mdl.damping_options = dict(opts)
    mdl.add_th_case("TH", "X", acc, dt, damping=0.02, nonlinear=True,
                    gravity={"G": 1.0}, hinges="column_base",
                    hardening=0.02, My={"C1": 40.0}, **th_kw)
    mdl.num_modes = 1
    return mdl


def _run(mdl):
    return OpenSeesEngine(mdl).run_time_history("TH")


def _ux(res):
    return np.array(res.story_ux["Story1"])


def _hash(res):
    return hashlib.sha256(json.dumps(res.to_dict(), sort_keys=True)
                          .encode()).hexdigest()


# --------------------------------------------------------------------------- #
# independent integrators
# --------------------------------------------------------------------------- #
def _newmark_linear(acc, dt, m, k, c):
    """u at t = (n+1) dt (sample n of acc acts at n dt; ag(0) = acc[0])."""
    g, b = 0.5, 0.25
    u = v = a = 0.0
    out = []
    keff = k + g / (b * dt) * c + m / (b * dt * dt)
    for i in range(len(acc)):
        p1 = -m * (acc[i + 1] if i + 1 < len(acc) else 0.0)
        rhs = (p1 + m * (u / (b * dt * dt) + v / (b * dt)
                         + (0.5 / b - 1) * a)
               + c * (g / (b * dt) * u + (g / b - 1) * v
                      + dt * (g / (2 * b) - 1) * a))
        u1 = rhs / keff
        v1 = g / (b * dt) * (u1 - u) + (1 - g / b) * v \
            + dt * (1 - g / (2 * b)) * a
        a = (u1 - u) / (b * dt * dt) - v / (b * dt) - (0.5 / b - 1) * a
        u, v = u1, v1
        out.append(u)
    return np.array(out)


def _sdof_epp_rayleigh(acc, dt, m, k, Fy, a0, a1):
    """Classical Rayleigh-damped EPP SDOF: Newmark average acceleration,
    Newton, ``c = a0 m + a1 k_t`` on the last COMMITTED tangent."""
    g, b = 0.5, 0.25
    u = v = a = up = 0.0
    kt_c = k
    out = []
    for s in range(len(acc) - 1):
        c = a0 * m + a1 * kt_c
        p = -m * acc[s + 1]
        u1 = u
        for _ in range(100):
            an = (u1 - u - dt * v - dt * dt * (0.5 - b) * a) / (b * dt * dt)
            vn = v + dt * ((1 - g) * a + g * an)
            f, kt = k * (u1 - up), k
            if abs(f) > Fy:
                f, kt = math.copysign(Fy, f), 0.0
            R = m * an + c * vn + f - p
            if abs(R) < 1e-11:
                break
            u1 -= R / (m / (b * dt * dt) + c * g / (b * dt) + kt)
        an = (u1 - u - dt * v - dt * dt * (0.5 - b) * a) / (b * dt * dt)
        v = v + dt * ((1 - g) * a + g * an)
        a = an
        f, kt_c = k * (u1 - up), k
        if abs(f) > Fy:
            up = u1 - math.copysign(Fy, f) / k
            kt_c = 0.0
        u = u1
        out.append(u)
    return np.array(out)


def _fit_recurrence(u):
    """Least-squares u_{n+2} = p1 u_{n+1} - p2 u_n -> (|lambda|, arg)."""
    X = np.column_stack([u[1:-1], -u[:-2]])
    p1, p2 = np.linalg.lstsq(X, u[2:], rcond=None)[0]
    r = math.sqrt(p2)
    return r, math.acos(p1 / (2.0 * r))


def _rayleigh_fit(mdl, zeta):
    """The engine's single-mode fit a0 = zeta w, a1 = zeta / w."""
    T = OpenSeesEngine(mdl).run_modal().periods[0]
    w = 2 * math.pi / T
    return w, zeta * w, zeta / w


# --------------------------------------------------------------------------- #
# 1. option plumbing / defaults
# --------------------------------------------------------------------------- #
def test_defaults_serialize_unchanged_and_roundtrip():
    mdl = _base("d")
    assert mdl.damping_options == dmo.damping_defaults()
    assert "damping_options" not in mdl.to_dict()
    mdl.damping_options = dict(dmo.RECOMMENDED_DAMPING_OPTIONS)
    d = mdl.to_dict()
    assert d["damping_options"] == {"rayleigh_on_links": "all",
                                    "rayleigh_stiffness_basis": "committed"}
    back = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert back.damping_options == mdl.damping_options
    assert BuildingModel.from_dict(_base("d").to_dict()).damping_options \
        == dmo.damping_defaults()


@pytest.mark.parametrize("opts, msg", [
    ({"rayleigh_on_links": "some"}, "rayleigh_on_links"),
    ({"rayleigh_stiffness_basis": "tangent"}, "rayleigh_stiffness_basis"),
    ({"bogus": 1}, "unknown keys"),
    ("all", "must be an object"),
])
def test_validation_rejects_bad_options(opts, msg):
    mdl = _base("v")
    mdl.damping_options = opts
    with pytest.raises(ValueError, match=msg):
        mdl.validate()


def test_default_results_byte_identical():
    """``none`` (default, or explicit with any basis) leaves every element
    call untouched: hinged nonlinear TH, sprung / linked linear TH and a
    yielding isolator with energy hash identically; no extra key."""
    builders = [lambda o: _elastoplastic(o, energy=True),
                lambda o: _base("s", "spring", opts=o),
                lambda o: _base("l", "link", opts=o),
                lambda o: _isolated(o)]
    for build in builders:
        ref = _run(build(None))
        assert "link_rayleigh" not in ref.extra
        h = _hash(ref)
        for basis in dmo.RAYLEIGH_STIFFNESS_BASES:
            o = {"rayleigh_on_links": "none",
                 "rayleigh_stiffness_basis": basis}
            assert _hash(_run(build(o))) == h


def test_participation_per_kind():
    """elastic_only damps linear springs / links only; all adds hinges and
    nonlinear springs / material links (summary in extra)."""
    eo = {"rayleigh_on_links": "elastic_only"}
    assert _run(_base("s", "spring", opts=eo)).extra["link_rayleigh"][
        "elements"] == {"spring": 1}
    assert _run(_base("l", "link", opts=eo)).extra["link_rayleigh"][
        "elements"] == {"elastic_link": 1}
    assert "link_rayleigh" not in _run(_elastoplastic(eo)).extra
    assert "link_rayleigh" not in _run(_base("n", "named_ml", opts=eo)).extra
    assert _run(_elastoplastic(ALL)).extra["link_rayleigh"]["elements"] \
        == {"hinge": 1}
    assert _run(_base("n", "named_ml", opts=ALL)).extra["link_rayleigh"][
        "elements"] == {"named_spring_nl": 1}
    info = _run(_isolated(ALL)).extra["link_rayleigh"]
    assert info["elements"] == {"isolator_link": 1}
    assert info["rayleigh_stiffness_basis"] == "committed"
    for k in ("damper_link", "bearing", "hinge_tie", "dummy"):
        assert not lrl.participates("all", k)


def test_modal_damping_unaffected():
    """modalDamping already damps the sprung modes: the option is inert."""
    a = _run(_base("m", "spring", damping_model="modal"))
    b = _run(_base("m", "spring", opts=ALL, damping_model="modal"))
    assert np.array_equal(_ux(a), _ux(b))


# --------------------------------------------------------------------------- #
# 2. linear TH: grounded spring / elastic link decay at the specified xi
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("base", ["spring", "link"])
def test_linear_sprung_column_matches_classical_rayleigh(base):
    """Series Kelvin elements with the same a1 == the SDOF with
    c = a0 m + a1 k (k = series stiffness, the engine's own mode)."""
    dt = 0.002
    mdl = _base("c", base, opts={"rayleigh_on_links": "elastic_only"})
    w, a0, a1 = _rayleigh_fit(mdl, 0.05)
    res = _run(mdl)
    u = _ux(res)
    acc = _pulse(dt)
    k = M_TOP * w * w
    ref = _newmark_linear(acc, dt, M_TOP, k, a0 * M_TOP + a1 * k)
    pk = np.max(np.abs(ref))
    assert np.max(np.abs(u - ref)) <= 1e-7 * pk
    # legacy (springs undamped) is visibly under-damped vs that reference
    leg = _ux(_run(_base("c", base)))
    assert np.max(np.abs(leg - ref)) > 1e-2 * pk


@pytest.mark.parametrize("base", ["spring", "link"])
def test_free_vibration_log_decrement_equals_xi(base):
    """Free vibration after the pulse: the log decrement gives xi = 5 %
    with the springs / link damped (legacy: clearly below 5 %).  The
    stiff column makes the spring 2/3 of the flexibility."""
    def xi_of(opts):
        res = _run(_base("x", base, opts=opts,
                         accel=_pulse(0.002, t_total=1.5)))
        r, arg = _fit_recurrence(_ux(res)[40:])
        return -math.log(r) / arg
    xi_on = xi_of({"rayleigh_on_links": "elastic_only"})
    xi_off = xi_of(None)
    assert xi_on == pytest.approx(0.05, rel=0.01)    # Newmark dt/T ~ 1 %
    assert xi_off < 0.045


def test_stiffness_bases_identical_for_linear_springs():
    """For linear-elastic springs / links K == Kinit == Kc: the three bases
    give the same response (and the same energy balance)."""
    for base in ("spring", "link"):
        u = [_ux(_run(_base("b", base, opts={
            "rayleigh_on_links": "all", "rayleigh_stiffness_basis": b})))
            for b in dmo.RAYLEIGH_STIFFNESS_BASES]
        pk = np.max(np.abs(u[0]))
        for x in u[1:]:
            assert np.max(np.abs(x - u[0])) <= 1e-10 * pk


def test_fixed_base_unchanged_by_option():
    """No spring / link / hinge -> the option changes nothing."""
    a = _run(_base("f", "fixed"))
    b = _run(_base("f", "fixed", opts=ALL))
    assert np.array_equal(_ux(a), _ux(b))
    assert "link_rayleigh" not in b.extra


# --------------------------------------------------------------------------- #
# 3. nonlinear TH: EPP hinged SDOF vs classical Rayleigh SDOF
# --------------------------------------------------------------------------- #
def test_epp_sdof_matches_classically_damped_sdof():
    """EPP hinge (My = 150, yields during the burst, then free vibration):
    with the hinge damped on its committed tangent the response is the
    classical Rayleigh SDOF with the same series EPP spring to < 0.1 %
    (peak, end of record, whole history); the legacy undamped hinge is
    off by more."""
    mdl, p = _epp(150.0, opts=ALL)
    res = _run(mdl)
    assert res.yielded == ["C1"]
    u = _ux(res)
    ref = _sdof_epp_rayleigh(p["acc"], p["dt"], p["m"], p["ks"], p["Fy"],
                             p["zeta"] * p["w0"], p["zeta"] / p["w0"])
    pk = np.max(np.abs(ref))
    assert abs(np.max(np.abs(u)) / pk - 1) < 1e-3
    assert abs(u[-1] / ref[-1] - 1) < 1e-3
    assert np.max(np.abs(u - ref)) < 1e-3 * pk
    leg = _ux(_run(_epp(150.0)[0]))
    assert abs(leg[-1] / ref[-1] - 1) > 3e-3


def test_epp_sdof_elastic_range_exact():
    """Below yield the damped hinge + damped column are series Kelvin
    elements with the same a1: exactly the linear SDOF
    c = a0 m + a1 k_series (independent Newmark)."""
    mdl, p = _epp(1.0e9, opts=ALL, t_end=3.0)
    u = _ux(_run(mdl))
    a0, a1 = p["zeta"] * p["w0"], p["zeta"] / p["w0"]
    ref = _newmark_linear(p["acc"][:-1], p["dt"], p["m"], p["ks"],
                          a0 * p["m"] + a1 * p["ks"])
    assert np.max(np.abs(u - ref)) <= 1e-6 * np.max(np.abs(ref))


# --------------------------------------------------------------------------- #
# 4. energy balance with the option on
# --------------------------------------------------------------------------- #
def _err(res):
    return res.extra["energy"]["max_abs_error_normalized"]


@pytest.mark.parametrize("basis", ["committed", "initial"])
def test_energy_balance_hinges(basis):
    res = _run(_elastoplastic({"rayleigh_on_links": "all",
                               "rayleigh_stiffness_basis": basis},
                              energy=True))
    en = res.extra["energy"]
    assert res.yielded == ["C1"]
    assert _err(res) < 1e-6
    assert en["hysteretic"][-1] > 0.3 * en["input"][-1]
    # the hinge damping does work: more damping energy than legacy
    leg = _run(_elastoplastic(None, energy=True)).extra["energy"]
    assert en["damping"][-1] > leg["damping"][-1]


def test_energy_balance_needs_committed_tangent_correction(monkeypatch):
    """Without the Kc(n) vs Kc(n+1) correction the hinge model's balance
    breaks at the yield events (the failure the verification suite hit);
    with it the balance is exact."""
    ok = _err(_run(_elastoplastic(ALL, energy=True)))
    monkeypatch.setattr(lrl, "tracker_elements", lambda asm: [])
    bad = _err(_run(_elastoplastic(ALL, energy=True)))
    assert ok < 1e-6 < bad


def test_energy_balance_springs():
    """Linear grounded spring (elastic_only) and nonlinear-elastic named
    multilinear spring (all, tangent changes every slope change)."""
    for base, opts in (("spring", {"rayleigh_on_links": "elastic_only"}),
                       ("named_ml", ALL)):
        res = _run(_base("e", base, opts=opts, energy=True,
                         accel=_pulse(0.002, amp=20.0)))
        assert _err(res) < 1e-6
        ed = res.extra["energy"]["damping"][-1]
        leg = _run(_base("e", base, energy=True,
                         accel=_pulse(0.002, amp=20.0)))
        assert ed > leg.extra["energy"]["damping"][-1] > 0


def test_energy_balance_links():
    """Elastic link (zeroLength) and yielding isolator (vertical
    twoNodeLink: shear-distance terms in A) with the option on."""
    res = _run(_base("e", "link", opts=ALL, energy=True))
    assert _err(res) < 1e-6
    iso = _run(_isolated(ALL))
    en = iso.extra["energy"]
    assert _err(iso) < 1e-6
    assert en["hysteretic"][-1] > 0.05 * en["input"][-1]   # it yields


def test_basic_matrix_reproduces_basic_deformation(monkeypatch):
    """A of the zeroLength hinge and of the vertical twoNodeLink isolator
    maps the final nodal displacements to the element's own
    ``basicDeformation`` (the tracker correction relies on it)."""
    import openseespy.opensees as ops
    real = lrl.tracker_elements
    seen = []

    def capture(asm):
        out = real(asm)
        seen[:] = out
        return out
    monkeypatch.setattr(lrl, "tracker_elements", capture)
    for mdl in (_elastoplastic(ALL, energy=True), _isolated(ALL)):
        _run(mdl)
        assert seen
        for e in seen:
            ni, nj = e["nodes"]
            u = np.concatenate([ops.nodeDisp(ni)[:6], ops.nodeDisp(nj)[:6]])
            D = np.array(ops.eleResponse(e["ele"], "basicDeformation"))
            assert np.allclose(e["A"] @ u, D, rtol=1e-9,
                               atol=1e-12 * max(1.0, np.max(np.abs(D))))


def test_energy_tracker_docstring_mentions_correction():
    assert "Kc(n+1)" in tho.__doc__
