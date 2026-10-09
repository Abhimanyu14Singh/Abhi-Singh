"""Plastic hinges at spring-supported column bases (pushover / nonlinear
static / nonlinear time history).

Bug: a column-base hinge duplicate was tied to the support node with
``equalDOF``; when that node carried a PARTIAL restraint on the tied
translations (ux sprung -> free, uy/uz fixed) openseespy's Transformation
handler held the duplicate's free translation at ZERO, so the member saw a
fixed base and the grounded spring was bypassed (fixed-base stiffness
reported).  Fix (``skyframe.engine.hinge_ties``): such ties use the stiff
zeroLength tie element (k_tie = 1e8 x the member end stiffness).

Hand values (cantilever, height L, EI, base hinge k_th = n 6EI/L, n = 10;
the free top end carries no moment, so only the base hinge flexes):

* translational base spring k_s:
      1/K = 1/k_s + L^3/(3EI) + L^2/k_th
* rotational base spring k_r (about the bending axis):
      1/K = L^2/k_r + L^3/(3EI) + L^2/k_th
* the base moment is V L by statics, so a perfectly plastic hinge
  (hardening 0) caps the base shear at My / L;
* multilinear spring (no yield): u = curve^-1(V) + V (L^3/3EI + L^2/k_th);
* tip-mass free vibration: T = 2 pi sqrt(m / K).

The tie element softens the series by ~1e-8 (pinned at 1e-6).  Models
without a partially restrained hinge node keep the exact equalDOF path:
asserted byte-identical against the legacy tie rule.
"""

import copy
import json
import math
import warnings

import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core.builder import quick_building
from skyframe.core.model import (BuildingModel, FrameSection, LoadPattern,
                                 Material, NodalLoad, NodalMass,
                                 PointSupport)
from skyframe.design.steel import design_properties
from skyframe.engine import hinge_ties

E = 25_000_000.0
L = 4.0
N_H = engine_mod.HINGE_STIFFNESS_FACTOR
BIG_MY = 1.0e9                        # hinge present, never yields


def _col(restr, spring=None, prop=None, mass=0.0):
    m = BuildingModel(name="col")
    m.rigid_diaphragms = False
    m.num_modes = 3 if mass else 0
    m.add_material(Material("M", E=E, nu=0.2))
    m.add_section(FrameSection.rectangular("COL", "M", 0.5, 0.5))
    m.set_stories([L])
    m.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                 uid="C1")
    m.supports.append(PointSupport((0, 0, 0), tuple(restr)))
    if prop is not None:
        m.add_spring_property("SP", prop)
        m.add_spring_support((0, 0, 0), property="SP")
    elif spring is not None:
        m.add_spring_support((0, 0, 0), spring)
    if mass:
        m.nodal_masses.append(NodalMass((0, 0, L), mx=mass, my=mass))
    return m


def _EI(m):
    return E * m.sections["COL"].I22          # square: I22 == I33


def _f_col_hinge(m):
    """Cantilever + base-hinge flexibility L^3/3EI + L^2/k_th."""
    EI = _EI(m)
    return L ** 3 / (3.0 * EI) + L ** 2 / (N_H * 6.0 * EI / L)


def _push(m, **kw):
    kw.setdefault("target_drift", 0.01)
    kw.setdefault("steps", 20)
    m.add_pushover_case("PO", "X", **kw)
    po = OpenSeesEngine(m).run_pushover("PO")
    assert not po.warnings
    return po


# =========================================================================
# 1. pushover: initial stiffness = series hand value
# =========================================================================
def test_translational_spring_with_base_hinge_series_stiffness():
    ks = 10_000.0
    m = _col((0, 1, 1, 1, 1, 1), spring=[ks, 0, 0, 0, 0, 0])
    po = _push(m, default_My=BIG_MY)
    K = 1.0 / (1.0 / ks + _f_col_hinge(m))
    K_fixed = 1.0 / _f_col_hinge(m)
    k0 = po.base_shear[0] / po.roof_disp[0]
    assert k0 == pytest.approx(K, rel=1e-6)
    assert k0 < 0.7 * K_fixed                     # NOT the fixed base
    # elastic hinge: the whole curve is the same straight line
    for u, V in zip(po.roof_disp, po.base_shear):
        assert V == pytest.approx(K * u, rel=1e-6)


def test_rotational_spring_with_base_hinge_series_stiffness():
    kr = 50_000.0
    m = _col((1, 1, 1, 1, 0, 1), spring=[0, 0, 0, 0, kr, 0])
    po = _push(m, default_My=BIG_MY)
    K = 1.0 / (L ** 2 / kr + _f_col_hinge(m))
    assert po.base_shear[0] / po.roof_disp[0] == pytest.approx(K, rel=1e-6)


@pytest.mark.parametrize("which", ["translational", "rotational"])
def test_base_hinge_still_yields_at_My(which):
    """Perfectly plastic hinge (hardening 0): V_max = My / L exactly, and
    the elastic branch is the series stiffness."""
    My = 100.0
    if which == "translational":
        ks = 10_000.0
        m = _col((0, 1, 1, 1, 1, 1), spring=[ks, 0, 0, 0, 0, 0])
        f_spr = 1.0 / ks
    else:
        kr = 50_000.0
        m = _col((1, 1, 1, 1, 0, 1), spring=[0, 0, 0, 0, kr, 0])
        f_spr = L ** 2 / kr
    K = 1.0 / (f_spr + _f_col_hinge(m))
    u_y = (My / L) / K
    po = _push(m, default_My=My, hardening=0.0, target_drift=6 * u_y / L,
               steps=60)
    V_y = My / L
    assert max(po.base_shear) == pytest.approx(V_y, rel=1e-6)
    for u, V in zip(po.roof_disp, po.base_shear):
        if u < 0.99 * u_y:
            assert V == pytest.approx(K * u, rel=1e-6)
        if u > 1.01 * u_y:
            assert V == pytest.approx(V_y, rel=1e-6)     # plateau
    assert po.hinge_rotations["C1"] > 0.0


# =========================================================================
# 2. named spring properties under a base hinge
# =========================================================================
CURVE = [[0.01, 100.0], [0.03, 150.0]]       # k0 = 1e4, k1 = 2500


def _curve_u(F):
    return F / 1.0e4 if F <= 100.0 else 0.01 + (F - 100.0) / 2500.0


def test_named_multilinear_spring_with_base_hinge_follows_curve():
    m = _col((0, 1, 1, 1, 1, 1),
             prop={"kind": "multilinear", "curves": {"U1": CURVE}})
    po = _push(m, default_My=BIG_MY, target_drift=0.03 * 3.0 / L, steps=45)
    f = _f_col_hinge(m)
    for u, V in zip(po.roof_disp, po.base_shear):
        assert u == pytest.approx(_curve_u(V) + V * f, rel=1e-6)
    assert min(po.base_shear) < 100.0 < 150.0 < max(po.base_shear)


def test_named_compression_only_spring_with_base_hinge():
    """c-only on U3 (gravity settles it: uz = -W/kz) + linear U1: the
    push stiffness is the U1 spring in series with column + hinge."""
    ks, kz, W = 8_000.0, 2.0e5, 50.0
    m = _col((0, 1, 0, 1, 1, 1),
             prop={"kind": "compression_only", "k": [ks, 0, kz, 0, 0, 0]})
    m.patterns["DEAD"] = LoadPattern("DEAD", "dead")
    m.patterns["DEAD"].nodal_loads.append(NodalLoad((0, 0, L), fz=-W))
    m.add_case("DEAD", {"DEAD": 1.0})
    po = _push(m, default_My=BIG_MY, gravity={"DEAD": 1.0})
    K = 1.0 / (1.0 / ks + _f_col_hinge(m))
    assert po.base_shear[0] / po.roof_disp[0] == pytest.approx(K, rel=1e-6)
    # the same build under gravity alone (static Newton path): settlement
    eng = OpenSeesEngine(m)
    r = eng.run_static("DEAD")
    base = min(t for t, c in eng._asm.node_coords.items()
               if c == (0.0, 0.0, 0.0))
    assert r.node_disp[base][2] == pytest.approx(-W / kz, rel=1e-6)


# =========================================================================
# 3. nonlinear static (displacement control), nonlinear TH, asce41 / fiber
# =========================================================================
def test_nonlinear_static_displacement_control_with_spring_and_hinge():
    ks, My = 10_000.0, 100.0
    m = _col((0, 1, 1, 1, 1, 1), spring=[ks, 0, 0, 0, 0, 0])
    m.patterns["PX"] = LoadPattern("PX", "other")
    m.patterns["PX"].nodal_loads.append(NodalLoad((0, 0, L), fx=1.0))
    K = 1.0 / (1.0 / ks + _f_col_hinge(m))
    u_y = (My / L) / K
    m.add_nonlinear_static_case(
        "NLS", {"PX": 1.0}, load_application="displacement_control",
        steps=40, control_point=[0.0, 0.0, L], control_dof="UX",
        target_disp=4 * u_y, default_My=My, hardening=0.0)
    res = OpenSeesEngine(m).run_nonlinear_static("NLS")
    h = res.history
    assert res.converged
    lam1, u1 = h["load_factor"][1], h["disp"][1]
    assert lam1 / u1 == pytest.approx(K, rel=1e-6)
    assert max(h["load_factor"]) == pytest.approx(My / L, rel=1e-6)
    # base reaction includes the grounded spring force: FX = -lambda
    for lam, fx in zip(h["load_factor"], h["base"]["FX"]):
        assert fx == pytest.approx(-lam, rel=1e-6, abs=1e-9)
    assert "C1" in res.yielded


def test_nonlinear_time_history_period_sees_spring_and_hinge():
    """Free vibration after a short pulse: T = 2 pi sqrt(m/K_series)."""
    ks, mass = 10_000.0, 10.0
    m = _col((0, 1, 1, 1, 1, 1), spring=[ks, 0, 0, 0, 0, 0], mass=mass)
    K = 1.0 / (1.0 / ks + _f_col_hinge(m))
    T = 2.0 * math.pi * math.sqrt(mass / K)
    dt = T / 400.0
    n = int(4.5 * T / dt)
    m.add_th_case("TH", "X", accel=[1.0] * 4 + [0.0] * n, dt=dt,
                  damping=1e-4, nonlinear=True, default_My=BIG_MY)
    th = OpenSeesEngine(m).run_time_history("TH")
    ux = th.story_ux["Story1"]
    t = th.t
    ups = []
    for k in range(10, len(ux) - 1):
        if ux[k] <= 0.0 < ux[k + 1]:
            ups.append(t[k] + (t[k + 1] - t[k]) * (-ux[k])
                       / (ux[k + 1] - ux[k]))
    assert len(ups) >= 3
    T_num = (ups[-1] - ups[0]) / (len(ups) - 1)
    assert T_num == pytest.approx(T, rel=2e-3)
    T_fixed = 2.0 * math.pi * math.sqrt(mass * _f_col_hinge(m))
    assert abs(T_num - T_fixed) > 0.2 * T_fixed


E_STEEL = 200e6


def _w_cantilever(hinges, ky):
    m = BuildingModel("w")
    m.add_material(Material("steel", E_STEEL, 0.3, 77.0))
    m.add_section(FrameSection.from_library("W18x50", "steel"))
    m.set_stories([3.0])
    m.add_member("column", "W18x50", (0, 0, 0), (0, 0, 3.0), story="S1",
                 hinges=hinges)
    m.supports.append(PointSupport((0, 0, 0), (1, 0, 1, 1, 1, 1)))
    m.add_spring_support((0, 0, 0), [0, ky, 0, 0, 0, 0])
    m.patterns["DEAD"] = LoadPattern("DEAD", "dead")
    m.add_pushover_case("PUSH", "Y", gravity={}, target_drift=1e-4,
                        steps=4, hinges="asce41")
    m.validate()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = OpenSeesEngine(m).run_pushover("PUSH")
    return m, r.base_shear[0] / r.roof_disp[0]


def _radau_K(EI_f, EI_e, Lc, lp):
    """6-point HingeRadau cantilever tip stiffness (see test_wave22)."""
    pts = [(0.0, lp, EI_f), (8 * lp / 3, 3 * lp, EI_e),
           (Lc, lp, EI_f), (Lc - 8 * lp / 3, 3 * lp, EI_e)]
    Li = Lc - 8 * lp
    g = 1.0 / math.sqrt(3.0)
    for xi in (-g, g):
        pts.append((4 * lp + Li * (xi + 1) / 2, Li / 2, EI_e))
    f = sum(w * (x / Lc - 1.0) ** 2 / EI for (x, w, EI) in pts)
    return 1.0 / (Lc * Lc * f)


def test_asce41_auto_m3_and_fiber_pmm_hinges_on_spring_base():
    """auto_m3 (Hysteretic zeroLength at a duplicate node — the fixed
    path) and fiber_pmm (no duplicate node) both act in series with a
    translational base spring (pushed in Y = strong axis)."""
    ky, Lc = 20_000.0, 3.0
    m, k_m3 = _w_cantilever("auto_m3", ky)
    EI33 = E_STEEL * m.sections["W18x50"].I33
    f_m3 = Lc ** 3 / (3 * EI33) + Lc ** 2 / (N_H * 6 * EI33 / Lc)
    assert k_m3 == pytest.approx(1.0 / (1.0 / ky + f_m3), rel=1e-6)
    m, k_fib = _w_cantilever("fiber_pmm", ky)
    p = design_properties("W18x50")
    d_w, bf, tf, tw = p["d"], p["bf"], p["tf"], p["tw"]
    y1, y2 = d_w / 2 - 3 * tf / 4, d_w / 2 - tf / 4
    I_fl = bf * tf / 2.0 * (y1 ** 2 + y2 ** 2)
    hw = d_w - 2 * tf
    I_web = tw * hw ** 3 / 12.0 * (1 - 1.0 / engine_mod.FIBER_HINGE_STRIPS
                                   ** 2)
    K_fib = _radau_K(E_STEEL * (2 * I_fl + I_web), EI33, Lc, 0.5 * d_w)
    assert k_fib == pytest.approx(1.0 / (1.0 / ky + 1.0 / K_fib), rel=1e-6)


# =========================================================================
# 4. same defect via Set Active DOFs; defaults byte-identical
# =========================================================================
def _portal(active=None):
    m = BuildingModel(name="p")
    m.rigid_diaphragms = False
    m.num_modes = 0
    m.add_material(Material("M", E=E, nu=0.2))
    m.add_section(FrameSection.rectangular("S", "M", 0.5, 0.5))
    m.set_stories([L])
    m.add_member("column", "S", (0, 0, 0), (0, 0, L), story="Story1",
                 uid="C1")
    m.add_member("column", "S", (6, 0, 0), (6, 0, L), story="Story1",
                 uid="C2")
    m.add_member("beam", "S", (0, 0, L), (6, 0, L), story="Story1",
                 uid="B1")
    for x in (0, 6):
        m.supports.append(PointSupport((x, 0, 0), (1, 1, 1, 1, 1, 1)))
    if active:
        m.active_dof = active
    return m


def test_planar_active_dof_portal_all_ends_hinges():
    """A dof dropped by Set Active DOFs is the same partial restraint at
    every hinge original: the 2D (UX, UZ, RY) portal must match 3D (it
    read ~0 base shear before the fix)."""
    k = []
    for active in (None, ["UX", "UZ", "RY"]):
        po = _push(_portal(active), hinges="all_ends", default_My=BIG_MY,
                   steps=10)
        k.append(po.base_shear[0] / po.roof_disp[0])
    assert k[1] == pytest.approx(k[0], rel=1e-6)
    assert k[0] > 1000.0


def _legacy(monkeypatch):
    monkeypatch.setattr(hinge_ties, "partial_sp_checker",
                        lambda asm, mask: (lambda orig, dofs: False))


def _no_spring_models():
    qb = quick_building(bays_x=2, bays_y=1, stories=3)
    qb.add_pushover_case("PO", "X", gravity={"DEAD": 1.0},
                         hinges="all_ends", default_My=250.0, steps=15)
    qb.add_nonlinear_static_case(
        "NLS", {"EQX": 1.0}, load_application="displacement_control",
        steps=8, control_story=qb.stories[-1].name, control_dof="UX",
        target_disp=0.05, default_My=250.0)
    col = _col((1, 1, 1, 1, 1, 1))
    col.add_pushover_case("PO", "X", default_My=80.0, steps=15,
                          target_drift=0.03)
    portal = _portal()
    portal.add_pushover_case("PO", "X", hinges="all_ends", default_My=120.0,
                             steps=15)
    return {"qb": qb, "col": col, "portal": portal}


def _digest(models):
    out = {}
    for key, m in models.items():
        eng = OpenSeesEngine(copy.deepcopy(m))
        d = {"po": eng.run_pushover("PO").to_dict()}
        if "NLS" in m.nonlinear_static_cases:
            d["nls"] = eng.run_nonlinear_static("NLS").to_dict()
        out[key] = json.dumps(d, sort_keys=True, default=str)
    return out


def test_models_without_springs_byte_identical(monkeypatch):
    """Fixed / free hinge originals keep the exact equalDOF path: the
    pushover + nonlinear static JSON is identical with the legacy rule."""
    models = _no_spring_models()
    new = _digest(models)
    _legacy(monkeypatch)
    old = _digest(models)
    for key in models:
        assert new[key] == old[key], key


def test_legacy_rule_reproduces_the_bug(monkeypatch):
    """Guard: under the pre-fix tie rule the sprung base reads the FIXED
    base stiffness, i.e. the regression test above is meaningful."""
    _legacy(monkeypatch)
    m = _col((0, 1, 1, 1, 1, 1), spring=[10_000.0, 0, 0, 0, 0, 0])
    po = _push(m, default_My=BIG_MY)
    K_fixed = 1.0 / _f_col_hinge(m)
    assert po.base_shear[0] / po.roof_disp[0] == pytest.approx(K_fixed,
                                                                rel=1e-3)
