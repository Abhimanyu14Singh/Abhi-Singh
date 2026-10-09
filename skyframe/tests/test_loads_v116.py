"""v1.16 load generation (gap audit D2 + D4).

* frame temperature GRADIENTS (ThermalLoad.grad2 / grad3) through the exact
  fixed-end-force machinery, joint-pattern temperatures,
* shell temperatures (uniform dT + gradient grad3 through the thickness),
* projected frame loads (MemberLoad.projected),
* auto lateral generators (skyframe.core.autolateral): ASCE 7-22 ELF (two-
  period + multi-period), EC8 lateral force, IS 1893:2016, user coefficient,
  user loads, ASCE 7-22 wind; accidental eccentricity +/-.

Every expected number is a closed-form / hand result written in the test.
Units: kN, m, kPa, deg C.
"""

import json
import math
import warnings

import pytest

from skyframe.core import autolateral as al
from skyframe.core.model import (BuildingModel, FrameSection, LoadPattern,
                                 Material, MemberLoad, PointSupport,
                                 ShellSection, ThermalLoad)
from skyframe.core.thermal_ext import JointTemperature, ShellThermalLoad
from skyframe.engine.opensees_engine import OpenSeesEngine

E = 2.0e8                     # kPa
NU = 0.3
I33 = 2.0e-4
I22 = 5.0e-5
A_SEC = 1.0e-2
ALPHA = 1.2e-5                # model default thermal_alpha
G_GRAD = 50.0                 # deg C / m (e.g. dT = 25 C over h = 0.5 m)
FIX = (1, 1, 1, 1, 1, 1)
G = 9.80665


def _base():
    m = BuildingModel("t")
    m.rigid_diaphragms = False
    m.add_material(Material("S", E=E, nu=NU, unit_weight=0.0))
    m.add_section(FrameSection("B", "S", A=A_SEC, I33=I33, I22=I22,
                               J=1e-4))
    return m


def _solve(m, case="C"):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m.validate()
        eng = OpenSeesEngine(m)
        res = eng.run_static(case)
    return eng, res


def _tag(eng, pt):
    return eng._find_node(eng._asm, pt)


def _beam(L=4.0, fix_j=False, releases=None, grad2=0.0, grad3=0.0, dT=0.0,
          scale=1.0):
    m = _base()
    kw = {"releases": releases} if releases else {}
    m.add_member("beam", "B", (0, 0, 0), (L, 0, 0), uid="M1", **kw)
    m.supports.append(PointSupport((0.0, 0.0, 0.0), FIX))
    if fix_j:
        m.supports.append(PointSupport((L, 0.0, 0.0), FIX))
    m.pattern("P").thermal_loads.append(ThermalLoad("M1", dT, grad2, grad3))
    m.add_case("C", {"P": scale})
    return m


# --------------------------------------------------------------------------- #
# 1. frame temperature gradients
# --------------------------------------------------------------------------- #
def test_free_cantilever_gradient_local2():
    """Beam along +X (local 2 = +Z).  Hot +2 face: free curvature
    v'' = -alpha g -> tip rotation alpha g L = 1.2e-5*50*4 = 2.4e-3 rad and
    tip deflection -alpha g L^2/2 = -4.8e-3 m; no internal force."""
    L = 4.0
    eng, res = _solve(_beam(L, grad2=G_GRAD))
    d = res.node_disp[_tag(eng, (L, 0, 0))]
    assert d[4] == pytest.approx(ALPHA * G_GRAD * L, rel=1e-10)    # 2.4e-3
    assert d[2] == pytest.approx(-ALPHA * G_GRAD * L ** 2 / 2, rel=1e-10)
    assert max(abs(v) for v in res.member_forces["M1"]) < 1e-9
    assert max(abs(v) for v in res.member_stations["M1"]["M3"]) < 1e-9
    # closed-form deflection stations follow the parabola v = -a g x^2 / 2
    dl = res.member_deflections["M1"]
    for x, v in zip(dl["x"], dl["dy"]):
        assert v == pytest.approx(-ALPHA * G_GRAD * x * x / 2, abs=1e-14)


def test_free_cantilever_gradient_local3():
    """Hot +3 face (local 3 = -Y for this beam): w'' = -alpha g -> local-3
    tip deflection -alpha g L^2/2, i.e. global +Y 4.8e-3 m, rotation
    about +Z alpha g L."""
    L = 4.0
    eng, res = _solve(_beam(L, grad3=G_GRAD))
    d = res.node_disp[_tag(eng, (L, 0, 0))]
    assert d[1] == pytest.approx(ALPHA * G_GRAD * L ** 2 / 2, rel=1e-10)
    assert d[5] == pytest.approx(ALPHA * G_GRAD * L, rel=1e-10)
    dl = res.member_deflections["M1"]
    assert dl["dz"][5] == pytest.approx(-ALPHA * G_GRAD * 2.0 ** 2 / 2,
                                        rel=1e-10)
    assert max(abs(v) for v in res.member_stations["M1"]["M2"]) < 1e-9


def test_fixed_fixed_gradient_end_moments():
    """Fully restrained: zero displacement, constant moment
    E I alpha g = 2e8*2e-4*1.2e-5*50 = 24 kN m (I33) and
    2e8*5e-5*1.2e-5*50 = 6 kN m (I22); no shear, no axial force."""
    L = 4.0
    eng, res = _solve(_beam(L, fix_j=True, grad2=G_GRAD))
    for d in res.node_disp.values():
        assert max(abs(v) for v in d) < 1e-15
    f = res.member_forces["M1"]
    M = E * I33 * ALPHA * G_GRAD
    assert M == pytest.approx(24.0)
    assert abs(f[5]) == pytest.approx(M, rel=1e-12)
    assert abs(f[11]) == pytest.approx(M, rel=1e-12)
    assert abs(f[1]) < 1e-9 and abs(f[0]) < 1e-9
    for v in res.member_stations["M1"]["M3"]:
        assert abs(v) == pytest.approx(M, rel=1e-12)
    ra = res.reactions[_tag(eng, (0, 0, 0))]
    assert abs(ra[4]) == pytest.approx(M, rel=1e-12)
    eng2, res2 = _solve(_beam(L, fix_j=True, grad3=G_GRAD))
    M2 = E * I22 * ALPHA * G_GRAD
    assert M2 == pytest.approx(6.0)
    for v in res2.member_stations["M1"]["M2"]:
        assert abs(v) == pytest.approx(M2, rel=1e-12)


def test_propped_cantilever_gradient_release():
    """Fixed at i, Mj released onto a fixed node (propped cantilever):
    prop force R = 3 E I kappa / (2 L), fixed-end moment 1.5 E I kappa
    (kappa = alpha g) -> R = 3*24/(2*4) = 9 kN, M_A = 36 kN m."""
    L = 4.0
    eng, res = _solve(_beam(L, fix_j=True, releases="Mj", grad2=G_GRAD))
    k_EI = E * I33 * ALPHA * G_GRAD
    rb = res.reactions[_tag(eng, (L, 0, 0))]
    ra = res.reactions[_tag(eng, (0, 0, 0))]
    assert abs(rb[2]) == pytest.approx(1.5 * k_EI / L, rel=1e-9)     # 9 kN
    assert abs(ra[4]) == pytest.approx(1.5 * k_EI, rel=1e-9)         # 36
    assert abs(rb[4]) < 1e-9
    m3 = res.member_stations["M1"]["M3"]
    assert abs(m3[0]) == pytest.approx(36.0, rel=1e-9)
    assert abs(m3[-1]) < 1e-9


def test_gradient_on_split_member_and_case_scale():
    """Auto-mesh (min_segments 3) splits the cantilever into three
    segments; interior FEF nodal loads cancel -> same tip answers and the
    exact parabola across segments.  Case scale 2 doubles the gradient
    (v = -2 alpha g x^2 / 2)."""
    L = 4.0
    m = _beam(L, grad2=G_GRAD, scale=2.0)
    m.members[0].auto_mesh = {"min_segments": 3}
    eng, res = _solve(m)
    assert len(eng._asm.mesh.segments["M1"]) == 3
    d = res.node_disp[_tag(eng, (L, 0, 0))]
    assert d[4] == pytest.approx(2 * ALPHA * G_GRAD * L, rel=1e-9)
    assert d[2] == pytest.approx(-2 * ALPHA * G_GRAD * L ** 2 / 2, rel=1e-9)
    assert max(abs(v) for v in res.member_stations["M1"]["M3"]) < 1e-8
    dl = res.member_deflections["M1"]
    for x, v in zip(dl["x"], dl["dy"]):
        assert v == pytest.approx(-ALPHA * G_GRAD * x * x, abs=1e-13)


def test_gradient_plus_uniform_fixed_fixed():
    """dT = 20 and grad2 = 50 together on a fixed-fixed member:
    N = E A alpha dT = 2e8*1e-2*1.2e-5*20 = 480 kN (compression) and
    M = 24 kN m — superposed independently."""
    eng, res = _solve(_beam(4.0, fix_j=True, grad2=G_GRAD, dT=20.0))
    f = res.member_forces["M1"]
    assert abs(f[0]) == pytest.approx(480.0, rel=1e-12)
    assert abs(f[5]) == pytest.approx(24.0, rel=1e-12)
    assert res.member_stations["M1"]["N"][0] == pytest.approx(-480.0,
                                                             rel=1e-12)


def test_joint_temperature_interpolated_to_member():
    """Joint temperatures 10 / 30 C at the ends -> member T_avg = 20 C:
    free member elongation alpha*20*L = 9.6e-4 m (L = 4); fixed-fixed
    axial force E A alpha 20 = 480 kN."""
    L = 4.0
    m = _base()
    m.add_member("beam", "B", (0, 0, 0), (L, 0, 0), uid="M1")
    m.supports.append(PointSupport((0.0, 0.0, 0.0), FIX))
    pat = m.pattern("P")
    pat.joint_temperatures += [JointTemperature((0.0, 0.0, 0.0), 10.0),
                               JointTemperature((L, 0.0, 0.0), 30.0)]
    m.add_case("C", {"P": 1.0})
    eng, res = _solve(m)
    assert res.node_disp[_tag(eng, (L, 0, 0))][0] == pytest.approx(
        ALPHA * 20.0 * L, rel=1e-10)
    m.supports.append(PointSupport((L, 0.0, 0.0), FIX))
    eng, res = _solve(m)
    assert abs(res.member_forces["M1"][0]) == pytest.approx(480.0,
                                                            rel=1e-12)


def test_thermal_gradient_serialization_and_validation():
    pat = LoadPattern("P")
    pat.thermal_loads.append(ThermalLoad("M1", 5.0))
    assert pat.to_dict()["thermal_loads"] == [{"member_uid": "M1",
                                               "dT": 5.0}]
    m = _beam(grad2=10.0, grad3=-3.0)
    m.pattern("P").shell_thermal_loads.append(ShellThermalLoad("X", 1.0))
    with pytest.raises(ValueError, match="unknown shell region"):
        m.validate()
    m.patterns["P"].shell_thermal_loads.clear()
    m.patterns["P"].joint_temperatures.append(
        JointTemperature((0.0, 0.0, 0.0), 7.0))
    d = json.loads(json.dumps(m.to_dict()))
    m2 = BuildingModel.from_dict(d)
    tl = m2.patterns["P"].thermal_loads[0]
    assert (tl.grad2, tl.grad3) == (10.0, -3.0)
    assert m2.patterns["P"].joint_temperatures[0].dT == 7.0
    m2.patterns["P"].thermal_loads[0].grad2 = float("nan")
    with pytest.raises(ValueError, match="grad2"):
        m2.validate()


# --------------------------------------------------------------------------- #
# 2. shell temperatures
# --------------------------------------------------------------------------- #
T_SH = 0.2
A_PL = 2.0


def _plate(supports, dT=0.0, grad3=0.0, corners=None):
    m = _base()
    m.add_shell_section(ShellSection("SH", "S", thickness=T_SH))
    corners = corners or [(0, 0, 0), (A_PL, 0, 0), (A_PL, A_PL, 0),
                          (0, A_PL, 0)]
    m.add_shell("slab", "shell", "SH", corners, mesh_size=0.5, uid="W1")
    m.supports.extend(supports)
    m.pattern("P").shell_thermal_loads.append(
        ShellThermalLoad("W1", dT, grad3))
    m.add_case("C", {"P": 1.0})
    return m


_DETERMINATE = [PointSupport((0.0, 0.0, 0.0), (1, 1, 1, 0, 0, 1)),
                PointSupport((A_PL, 0.0, 0.0), (0, 1, 1, 0, 0, 0)),
                PointSupport((0.0, A_PL, 0.0), (0, 0, 1, 0, 0, 0))]


def test_free_plate_gradient_curvature():
    """Statically determinate plate, hot top (+3 = +Z): free isotropic
    curvature kappa = alpha g; with w = 0 at three corners the surface is
    w = kappa/2 (a x + a y - x^2 - y^2) -> centre w = kappa a^2 / 4 =
    1.2e-5*50*4/4 = 6.0e-4 m, fourth corner 0, corner rotations
    kappa a / 2.  Corrected shell moments vanish (free plate)."""
    eng, res = _solve(_plate(_DETERMINATE, grad3=G_GRAD))
    kap = ALPHA * G_GRAD
    dc = res.node_disp[_tag(eng, (A_PL / 2, A_PL / 2, 0))]
    assert dc[2] == pytest.approx(kap * A_PL ** 2 / 4, rel=1e-9)
    dk = res.node_disp[_tag(eng, (A_PL, A_PL, 0))]
    assert abs(dk[2]) < 1e-12
    assert abs(dk[3]) == pytest.approx(kap * A_PL / 2, rel=1e-9)
    for v in res.shell_forces.values():
        assert max(abs(x) for x in v) < 1e-8


def test_clamped_plate_gradient_moment():
    """All edges clamped: no displacement, restrained moment
    m = E t^3 alpha g / (12 (1 - nu)) = 2e8*0.008*6e-4/(12*0.7) = 114.2857
    kN m / m in both directions, no twist, no membrane force."""
    sup = [PointSupport((x, y, 0.0), FIX)
           for x in (0.0, 0.5, 1.0, 1.5, 2.0) for y in (0.0, 0.5, 1.0, 1.5,
                                                        2.0)
           if x in (0.0, 2.0) or y in (0.0, 2.0)]
    eng, res = _solve(_plate(sup, grad3=G_GRAD))
    for d in res.node_disp.values():
        assert max(abs(v) for v in d) < 1e-14
    m_th = E * T_SH ** 3 * ALPHA * G_GRAD / (12 * (1 - NU))
    assert m_th == pytest.approx(114.2857142857, rel=1e-9)
    for v in res.shell_forces.values():
        assert abs(v[3]) == pytest.approx(m_th, rel=1e-9)
        assert v[4] == pytest.approx(v[3], rel=1e-9)
        assert abs(v[5]) < 1e-8 and abs(v[0]) < 1e-8


def test_plate_uniform_temperature_membrane():
    """Uniform dT = 10: free plate expands alpha dT a = 2.4e-4 m at the far
    corner (both directions), no force; clamped plate carries
    N = -E t alpha dT/(1 - nu) = -2e8*0.2*1.2e-4/0.7 = -6857.14 kN/m."""
    eng, res = _solve(_plate(_DETERMINATE, dT=10.0))
    dk = res.node_disp[_tag(eng, (A_PL, A_PL, 0))]
    assert dk[0] == pytest.approx(ALPHA * 10 * A_PL, rel=1e-9)
    assert dk[1] == pytest.approx(ALPHA * 10 * A_PL, rel=1e-9)
    for v in res.shell_forces.values():
        assert max(abs(x) for x in v) < 1e-6
    sup = [PointSupport((x, y, 0.0), FIX)
           for x in (0.0, 0.5, 1.0, 1.5, 2.0) for y in (0.0, 0.5, 1.0, 1.5,
                                                        2.0)
           if x in (0.0, 2.0) or y in (0.0, 2.0)]
    eng, res = _solve(_plate(sup, dT=10.0))
    N = E * T_SH * ALPHA * 10 / (1 - NU)
    for v in res.shell_forces.values():
        assert v[0] == pytest.approx(-N, rel=1e-9)
        assert v[1] == pytest.approx(-N, rel=1e-9)


def test_plate_gradient_reversed_corner_order():
    """Corner ordering CW (local 3 = -Z): a hot +3 face is now the bottom,
    so the free plate bows the other way (centre w = -kappa a^2/4)."""
    cw = [(0, 0, 0), (0, A_PL, 0), (A_PL, A_PL, 0), (A_PL, 0, 0)]
    eng, res = _solve(_plate(_DETERMINATE, grad3=G_GRAD, corners=cw))
    dc = res.node_disp[_tag(eng, (A_PL / 2, A_PL / 2, 0))]
    assert dc[2] == pytest.approx(-ALPHA * G_GRAD * A_PL ** 2 / 4, rel=1e-9)


def test_shell_thermal_membrane_region_rejected():
    m = _base()
    m.add_shell_section(ShellSection("SH", "S", thickness=0.2))
    pts = [(0, 0, 0), (4, 0, 0), (4, 3, 0), (0, 3, 0)]
    for a, b in zip(pts, pts[1:] + pts[:1]):
        m.add_member("beam", "B", a, b)
    m.add_shell("slab", "membrane", "SH", pts, uid="S1")
    m.pattern("P").shell_thermal_loads.append(ShellThermalLoad("S1", 5.0))
    with pytest.raises(ValueError, match="membrane"):
        m.validate()


# --------------------------------------------------------------------------- #
# 3. projected frame loads
# --------------------------------------------------------------------------- #
def _rafter(projected, kind="udl", w=3.0, w2=0.0):
    """30 deg rafter, L = 4 m true length, pinned-ish (both ends fixed)."""
    L, th = 4.0, math.radians(30.0)
    pj = (L * math.cos(th), 0.0, L * math.sin(th))
    m = _base()
    m.add_member("beam", "B", (0, 0, 0), pj, uid="R1")
    m.supports += [PointSupport((0.0, 0.0, 0.0), FIX),
                   PointSupport(pj, FIX)]
    m.pattern("P").member_loads.append(MemberLoad(
        "R1", kind=kind, w=w, w2=w2, projected=projected))
    m.add_case("C", {"P": 1.0})
    return m


def test_projected_gravity_on_30deg_rafter():
    """Projected: w per HORIZONTAL length -> total w L cos30 =
    3*4*0.866025 = 10.3923 kN; unprojected: w L = 12 kN.  (The reaction
    sums of a fully fixed inclined member carry the engine's usual ~1e-7
    solver residual, so they are compared at 1e-6 and as a ratio.)"""
    eng, res = _solve(_rafter(True))
    total = 3.0 * 4.0 * math.cos(math.radians(30))
    assert total == pytest.approx(10.392304845, rel=1e-9)
    assert res.base["FZ"] == pytest.approx(total, rel=1e-6)
    assert eng._applied_gravity_fz(eng._asm, {"P": 1.0}) == pytest.approx(
        total, rel=1e-12)
    eng2, res2 = _solve(_rafter(False))
    assert res2.base["FZ"] == pytest.approx(12.0, rel=1e-6)
    assert res.base["FZ"] / res2.base["FZ"] == pytest.approx(
        math.cos(math.radians(30)), rel=1e-12)


def test_projected_trapezoid_and_global_x():
    """Trapezoid 2 -> 4 projected: 0.5*(2+4)*L*cos30 = 10.3923 kN; a
    projected global_x load on the 30 deg rafter uses the projection on
    the vertical plane: sin30 -> w L / 2."""
    eng, res = _solve(_rafter(True, kind="trapezoid", w=2.0, w2=4.0))
    assert eng._applied_gravity_fz(eng._asm, {"P": 1.0}) == pytest.approx(
        3.0 * 4.0 * math.cos(math.radians(30)), rel=1e-12)
    assert res.base["FZ"] == pytest.approx(
        3.0 * 4.0 * math.cos(math.radians(30)), rel=1e-6)
    m = _rafter(False)
    m.patterns["P"].member_loads[0] = MemberLoad(
        "R1", w=5.0, direction="global_x", projected=True)
    eng, res = _solve(m)
    assert res.base["FX"] == pytest.approx(-5.0 * 4.0 * 0.5, rel=1e-6)


def test_projected_validation_and_serialization():
    m = _rafter(True)
    d = m.to_dict()
    assert d["patterns"]["P"]["member_loads"][0]["projected"] is True
    assert BuildingModel.from_dict(json.loads(json.dumps(d))).patterns[
        "P"].member_loads[0].projected is True
    pat = LoadPattern("Q")
    pat.member_loads.append(MemberLoad("R1", w=1.0))
    assert "projected" not in pat.to_dict()["member_loads"][0]
    m.patterns["P"].member_loads[0] = MemberLoad("R1", kind="point", w=1.0,
                                                 a=0.5, projected=True)
    with pytest.raises(ValueError, match="distributed"):
        m.validate()
    m.patterns["P"].member_loads[0] = MemberLoad(
        "R1", w=1.0, direction="local_y", projected=True)
    with pytest.raises(ValueError, match="global direction"):
        m.validate()


# --------------------------------------------------------------------------- #
# 4. auto lateral generators — 3-story example
# --------------------------------------------------------------------------- #
# Stories 4 / 3 / 3 m -> elevations 4, 7, 10 m; plan 10 x 6 m; weights
# W1 = 1000, W2 = 1000, W3 = 800 kN (W = 2800 kN).
#   sum w h   = 4000 + 7000 + 8000        = 19 000
#   sum w h^2 = 16 000 + 49 000 + 80 000  = 145 000
WTS = {"S1": 1000.0, "S2": 1000.0, "S3": 800.0}


def _bldg():
    m = BuildingModel("three")
    m.add_material(Material("steel", 2.0e8, 0.3, 77.0))
    m.add_section(FrameSection.from_library("W14x90", "steel"))
    m.set_stories([4.0, 3.0, 3.0], names=["S1", "S2", "S3"])
    z = [0.0, 4.0, 7.0, 10.0]
    for x, y in ((0, 0), (10, 0), (10, 6), (0, 6)):
        for i, s in enumerate(("S1", "S2", "S3")):
            m.add_member("column", "W14x90", (x, y, z[i]), (x, y, z[i + 1]),
                         story=s)
    for i, s in enumerate(("S1", "S2", "S3")):
        zz = z[i + 1]
        for a, b in (((0, 0), (10, 0)), ((10, 0), (10, 6)),
                     ((10, 6), (0, 6)), ((0, 6), (0, 0))):
            m.add_member("beam", "W14x90", (*a, zz), (*b, zz), story=s)
    m.story_masses = {k: v / G for k, v in WTS.items()}
    return m


def _fx(pat):
    return {sf.story: sf.fx for sf in pat.story_forces}


def test_asce7_22_two_period_base_shear_and_distribution():
    """SDS 1.0, SD1 0.6, R 8: Ta = 0.0466*10^0.9 = 0.37016 s <= Ts = 0.6
    -> Sa = SDS, Cs = 1/8 = 0.125, V = 0.125*2800 = 350 kN; k = 1:
    F = 350*{4000, 7000, 8000}/19000 = 73.684 / 128.947 / 147.368 kN."""
    m = _bldg()
    pat = al.asce7_22_elf(m, SDS=1.0, SD1=0.6, R=8.0)
    fx = _fx(pat)
    assert fx["S1"] == pytest.approx(73.68421053, rel=1e-9)
    assert fx["S2"] == pytest.approx(128.94736842, rel=1e-9)
    assert fx["S3"] == pytest.approx(147.36842105, rel=1e-9)
    assert sum(fx.values()) == pytest.approx(350.0, rel=1e-12)
    assert m.patterns["ELF22"].kind == "quake"
    s = al.compute(m, "asce7_22", SDS=1.0, SD1=0.6, R=8.0)
    assert s["T"] == pytest.approx(0.0466 * 10 ** 0.9, rel=1e-12)
    assert s["Cs"] == pytest.approx(0.125) and s["k"] == 1.0


def test_asce7_22_period_cap_k_and_long_period():
    """SD1 = 0.3 -> Cu = 1.4; user T = 1.2 s capped at Cu Ta =
    1.4*0.370160 = 0.518224 s.  Ts = 0.3 -> Sa = 0.3/0.518224 = 0.578900,
    Cs = 0.0723625 (> 0.044), V = 202.615 kN, k = 1 + (0.518224-0.5)/2 =
    1.009112.  With TL = 0.4 (< T): Sa = SD1 TL / T^2 = 0.12/0.268552 =
    0.446841."""
    m = _bldg()
    Ta = 0.0466 * 10 ** 0.9
    T = 1.4 * Ta
    assert T == pytest.approx(0.518224, rel=1e-5)
    s = al.compute(m, "asce7_22", SDS=1.0, SD1=0.3, R=8.0, T=1.2,
                   weights=WTS)
    assert s["T"] == pytest.approx(T, rel=1e-12)
    assert s["Sa"] == pytest.approx(0.578900, rel=1e-5)
    assert s["V"] == pytest.approx(2800 * 0.3 / T / 8, rel=1e-12)
    assert s["V"] == pytest.approx(202.615, rel=1e-5)
    k = 1 + (T - 0.5) / 2
    assert s["k"] == pytest.approx(1.009112, rel=1e-5)
    den = 1000 * 4 ** k + 1000 * 7 ** k + 800 * 10 ** k
    assert s["stories"][2]["F"] == pytest.approx(
        s["V"] * 800 * 10 ** k / den, rel=1e-12)
    s2 = al.compute(m, "asce7_22", SDS=1.0, SD1=0.3, R=8.0, T=1.2, TL=0.4)
    assert s2["Sa"] == pytest.approx(0.3 * 0.4 / T ** 2, rel=1e-12)
    assert s2["Sa"] == pytest.approx(0.446841, rel=1e-5)


def test_asce7_22_minimum_cs_with_large_s1():
    """SDS 0.3, SD1 0.2, R 8, S1 0.8: Sa = SDS (T < Ts = 0.667) -> Cs =
    0.0375, floors 0.044*0.3 = 0.0132, 0.01, 0.5*0.8/8 = 0.05 -> Cs =
    0.05, V = 140 kN."""
    s = al.compute(_bldg(), "asce7_22", SDS=0.3, SD1=0.2, R=8.0, S1=0.8)
    assert s["Cs"] == pytest.approx(0.05, rel=1e-12)
    assert s["V"] == pytest.approx(140.0, rel=1e-12)


def test_asce7_22_multi_period_spectrum():
    """MPRS points (0, 0.4) (0.2, 1.0) (0.5, 1.0) (1.0, 0.6) (2.0, 0.3):
    SDS = 0.9 max Sa = 0.9.  Ta = 0.37016: descending envelope max Sa = 1.0
    -> Sa_ELF = min(0.9, 1.0) = 0.9 -> V = 0.9/8*2800 = 315 kN.  With
    Ct = 0.1: Ta = 0.1*10^0.9 = 0.794328, Sa = 1.0 - 0.4*0.294328/0.5 =
    0.764537 -> Cs = 0.0955672, V = 267.588 kN."""
    pts = [[0, 0.4], [0.2, 1.0], [0.5, 1.0], [1.0, 0.6], [2.0, 0.3]]
    m = _bldg()
    s = al.compute(m, "asce7_22", SD1=0.6, R=8.0, mprs=pts)
    assert s["SDS"] == pytest.approx(0.9, rel=1e-12)
    assert s["V"] == pytest.approx(315.0, rel=1e-12)
    s2 = al.compute(m, "asce7_22", SD1=0.6, R=8.0, mprs=pts, Ct=0.1)
    assert s2["T"] == pytest.approx(0.794328, rel=1e-5)
    assert s2["Sa"] == pytest.approx(0.764537, rel=1e-5)
    assert s2["V"] == pytest.approx(267.588, rel=1e-5)
    assert s2["spectrum"] == "multi_period"


def test_ec8_lateral_force_height_distribution():
    """ag = 0.25 g, q = 4, ground B type 1 (S 1.2, TB 0.15, TC 0.5, TD 2).
    T1 = 0.05*10^0.75 = 0.281171 s (plateau): Sd = 0.25*1.2*2.5/4 =
    0.1875; 3 storeys and T1 <= 2 TC -> lambda = 0.85; Fb = 0.1875*2800*
    0.85 = 446.25 kN; F = Fb z m / sum z m = 93.947 / 164.408 /
    187.895 kN."""
    m = _bldg()
    pat = al.ec8_lateral_force(m, ag=0.25, q=4.0, ground_type="B")
    fx = _fx(pat)
    assert sum(fx.values()) == pytest.approx(446.25, rel=1e-12)
    assert fx["S1"] == pytest.approx(93.94736842, rel=1e-9)
    assert fx["S2"] == pytest.approx(164.40789474, rel=1e-9)
    assert fx["S3"] == pytest.approx(187.89473684, rel=1e-9)
    s = al.compute(m, "ec8", ag=0.25, q=4.0, ground_type="B")
    assert s["T"] == pytest.approx(0.281171, rel=1e-5)
    assert s["lambda_"] == 0.85 and s["Sd"] == pytest.approx(0.1875)


def test_ec8_mode_shape_and_spectrum_branches():
    """Mode shape s = 0.3/0.7/1.0: sum w s = 300+700+800 = 1800 ->
    F = 446.25*{300, 700, 800}/1800 = 74.375 / 173.542 / 198.333.
    T1 = 3 s > TD: 0.1875*0.5*2/9 = 0.020833 < beta ag = 0.05 -> Sd =
    0.05, lambda = 1 (T1 > 2 TC) -> Fb = 140.  T1 = 0.1 < TB: Sd = 0.3
    (2/3 + 0.1/0.15 (2.5/4 - 2/3)) = 0.191667."""
    m = _bldg()
    s = al.compute(m, "ec8", ag=0.25, q=4.0, ground_type="B",
                   distribution="mode",
                   mode_shape={"S1": 0.3, "S2": 0.7, "S3": 1.0})
    F = [r["F"] for r in s["stories"]]
    assert F == pytest.approx([74.375, 173.5416667, 198.3333333], rel=1e-9)
    s2 = al.compute(m, "ec8", ag=0.25, q=4.0, ground_type="B", T1=3.0)
    assert s2["Sd"] == pytest.approx(0.05) and s2["lambda_"] == 1.0
    assert s2["V"] == pytest.approx(140.0, rel=1e-12)
    s3 = al.compute(m, "ec8", ag=0.25, q=4.0, ground_type="B", T1=0.1)
    assert s3["Sd"] == pytest.approx(0.191666667, rel=1e-8)
    with pytest.raises(ValueError):
        al.compute(m, "ec8", ag=0.25, q=4.0, ground_type="Z")


def test_is1893_base_shear_and_parabolic_distribution():
    """Z 0.36, I 1.2, R 5, soil II, RC MRF: T = 0.075*10^0.75 = 0.421756 s
    <= 0.55 -> Sa/g = 2.5; Ah = 0.18*2.5/(5/1.2) = 0.108; Vb = 302.4 kN
    (> rho W = 0.024*2800 = 67.2).  Qi = Vb Wi hi^2 / 145000 =
    33.3683 / 102.1903 / 166.8414 kN."""
    m = _bldg()
    pat = al.is1893_static(m, Z=0.36, R=5.0, I=1.2, soil="II")
    fx = _fx(pat)
    assert sum(fx.values()) == pytest.approx(302.4, rel=1e-12)
    assert fx["S1"] == pytest.approx(33.36827586, rel=1e-9)
    assert fx["S2"] == pytest.approx(102.19034483, rel=1e-9)
    assert fx["S3"] == pytest.approx(166.84137931, rel=1e-9)
    s = al.compute(m, "is1893", Z=0.36, R=5.0, I=1.2, soil="II")
    assert s["T"] == pytest.approx(0.421756, rel=1e-5)
    assert s["Ah"] == pytest.approx(0.108, rel=1e-12)


def test_is1893_minimum_shear_and_period_forms():
    """Soil I, T = 1.0 user: Sa/g = 1.0, Ah = 0.18/4.1667 = 0.0432,
    V = 120.96.  Z 0.10, R 5, T 3 s soil I: Ah = 0.05*(1/3)/5 = 0.003333 <
    rho = 0.7 % -> V = 0.007*2800 = 19.6.  'other' along X: T = 0.09*10/
    sqrt(10) = 0.284605 s."""
    m = _bldg()
    s = al.compute(m, "is1893", Z=0.36, R=5.0, I=1.2, soil="I", T=1.0)
    assert s["V"] == pytest.approx(120.96, rel=1e-12)
    s2 = al.compute(m, "is1893", Z=0.10, R=5.0, soil="I", T=3.0)
    assert s2["V"] == pytest.approx(19.6, rel=1e-12)
    s3 = al.compute(m, "is1893", Z=0.24, R=5.0, structure="other")
    assert s3["T"] == pytest.approx(0.09 * 10 / math.sqrt(10), rel=1e-12)
    assert s3["T"] == pytest.approx(0.284605, rel=1e-5)
    assert al.is1893_rho(0.20) == pytest.approx(0.0135, rel=1e-12)


def test_user_coefficient_and_user_loads():
    """User coefficient C = 0.1, k = 2: V = 280, F3 = 280*80000/145000 =
    154.483 kN.  User loads go through verbatim (X: S1 10, S3 30;
    fy 5 at S3 kept)."""
    m = _bldg()
    pat = al.user_coefficient(m, C=0.1, k=2.0)
    fx = _fx(pat)
    assert sum(fx.values()) == pytest.approx(280.0, rel=1e-12)
    assert fx["S3"] == pytest.approx(154.48275862, rel=1e-9)
    pat2 = al.user_loads(m, [{"story": "S1", "fx": 10.0},
                             {"story": "S3", "fx": 30.0, "fy": 5.0}])
    assert [(s.story, s.fx, s.fy) for s in pat2.story_forces] == [
        ("S1", 10.0, 0.0), ("S3", 30.0, 5.0)]
    with pytest.raises(ValueError, match="story"):
        al.user_loads(m, [{"story": "NOPE", "fx": 1.0}])


def test_asce7_22_wind_ke_and_kz():
    """7-22 Kz at 33 ft (10.058 m), exposure C: 2.41 (33/2460)^(2/9.8) =
    0.99975 ~ 1.00 (the classic reference value).  Ke at ze = 500 m:
    exp(-0.0595) = 0.942236.  Story force = 0.613 Kz Ke V^2 Kd cp trib
    width: top story (trib 1.5 m, width 6 m along X)."""
    assert al.asce22_kz(33 * 0.3048, "C") == pytest.approx(0.99975,
                                                           abs=1e-5)
    m = _bldg()
    s = al.compute(m, "asce7_22_wind", V=50.0, exposure="C", ze=500.0)
    assert s["Ke"] == pytest.approx(0.942236, rel=1e-6)
    top = s["stories"][2]
    kz = 2.41 * (10.0 / (2460 * 0.3048)) ** (2 / 9.8)
    q = 0.613 * kz * 0.942236 * 2500 / 1000
    assert top["F"] == pytest.approx(q * 0.85 * 1.3 * 1.5 * 6.0, rel=1e-6)
    pat = al.asce7_22_wind(m, V=50.0)
    assert pat.kind == "wind"


def test_accidental_eccentricity_plus_minus():
    """ecc = +/-0.05: pattern carries accidental torsion with the signed
    ecc; the base torque of the +/- runs straddles the no-ecc value
    symmetrically (MZ+ + MZ- = 2 MZ0) and differs by 2 F ecc Ly."""
    m = _bldg()
    al.asce7_22_elf(m, 1.0, 0.6, 8.0, name="E0")
    p_pos = al.asce7_22_elf(m, 1.0, 0.6, 8.0, name="EP", ecc=0.05)
    p_neg = al.asce7_22_elf(m, 1.0, 0.6, 8.0, name="EN", ecc=-0.05)
    assert p_pos.accidental_torsion and p_pos.ecc == 0.05
    assert p_neg.accidental_torsion and p_neg.ecc == -0.05
    for n in ("E0", "EP", "EN"):
        m.add_case(n, {n: 1.0})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m.validate()
        eng = OpenSeesEngine(m)
        r0, rp, rn = (eng.run_static(n) for n in ("E0", "EP", "EN"))
    assert rp.base["MZ"] + rn.base["MZ"] == pytest.approx(
        2 * r0.base["MZ"], abs=1e-6)
    assert abs(rp.base["MZ"] - rn.base["MZ"]) == pytest.approx(
        2 * 350.0 * 0.05 * 6.0, rel=1e-6)
    with pytest.raises(ValueError):
        al.asce7_22_elf(m, 1.0, 0.6, 8.0, ecc=0.9)


def test_story_masses_drive_weights():
    """Without the weights override W comes from the story masses (here
    the explicit masses W_i/g): same 350 kN as the hand example."""
    s = al.compute(_bldg(), "asce7_22", SDS=1.0, SD1=0.6, R=8.0)
    assert s["W"] == pytest.approx(2800.0, rel=1e-12)
    assert [r["w"] for r in s["stories"]] == pytest.approx(
        [1000.0, 1000.0, 800.0], rel=1e-12)


# --------------------------------------------------------------------------- #
# 5. API + defaults
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client():
    from skyframe.api import server as srv
    app = srv.create_app()
    app.config["TESTING"] = True
    srv._state["model"] = _bldg()
    with app.test_client() as c:
        yield c, srv
    srv._state["model"] = srv.quick_building()


def test_api_auto_lateral_preview_and_generate(client):
    c, srv = client
    body = {"code": "is1893", "Z": 0.36, "R": 5.0, "I": 1.2, "soil": "II"}
    r = c.post("/api/pattern/auto-lateral/preview", json=body)
    assert r.status_code == 200
    assert r.get_json()["V"] == pytest.approx(302.4, rel=1e-12)
    assert "IS1893" not in srv._state["model"].patterns
    r = c.post("/api/pattern/auto-lateral",
               json={**body, "name": "QX", "ecc": -0.05})
    assert r.status_code == 200
    pd = r.get_json()["patterns"]["QX"]
    assert pd["ecc"] == -0.05 and pd["accidental_torsion"] is True
    assert sum(s["fx"] for s in pd["story_forces"]) == pytest.approx(302.4)
    assert c.post("/api/pattern/auto-lateral",
                  json={"code": "nope"}).status_code == 400
    assert c.post("/api/pattern/auto-lateral",
                  json={"code": "ec8", "ag": 0.2, "q": 3,
                        "bogus": 1}).status_code == 400


def test_api_temperature_endpoints(client):
    c, srv = client
    mdl = srv._state["model"]
    mdl.pattern("T")
    uid = mdl.members[-1].uid
    r = c.post("/api/pattern/thermal",
               json={"pattern": "T", "loads": [{"member_uid": uid,
                                                "grad2": 30.0}]})
    assert r.status_code == 200
    tl = r.get_json()["patterns"]["T"]["thermal_loads"][0]
    assert tl == {"member_uid": uid, "dT": 0.0, "grad2": 30.0}
    r = c.post("/api/pattern/joint-temperature",
               json={"pattern": "T", "loads": [{"point": [0, 0, 4],
                                                "dT": 12.0}]})
    assert r.status_code == 200
    assert r.get_json()["patterns"]["T"]["joint_temperatures"] == [
        {"point": [0.0, 0.0, 4.0], "dT": 12.0}]
    r = c.post("/api/pattern/shell-thermal",
               json={"pattern": "T", "loads": [{"region_uid": "none",
                                                "grad3": 5.0}]})
    assert r.status_code == 400
    assert not mdl.patterns["T"].shell_thermal_loads


def test_defaults_byte_identical_shapes_and_results():
    """Legacy pattern dict keys are unchanged; a legacy model's results are
    bit-identical to the same model with explicit default v1.16 fields."""
    pat = LoadPattern("P")
    pat.member_loads.append(MemberLoad("M1", w=2.0))
    pat.thermal_loads.append(ThermalLoad("M1", 3.0))
    d = pat.to_dict()
    assert list(d) == ["name", "kind", "member_udls", "nodal_loads",
                       "story_forces", "member_loads", "area_loads",
                       "thermal_loads", "accidental_torsion", "ecc",
                       "self_weight_factor"]
    assert d["member_loads"][0] == {"member_uid": "M1", "kind": "udl",
                                    "w": 2.0, "w2": 0.0, "a": 0.0,
                                    "b": 1.0, "direction": "gravity"}

    def run(explicit):
        m = _rafter(False)
        m.patterns["P"].thermal_loads.append(
            ThermalLoad("R1", 15.0, 0.0, 0.0) if explicit
            else ThermalLoad("R1", 15.0))
        if explicit:
            m.patterns["P"].member_loads[0].projected = False
        _, res = _solve(m)
        return json.dumps(res.to_dict(), sort_keys=True, default=str)
    assert run(False) == run(True)
