"""ETABS Assign > Joint Loads / Shell Loads parity.

* joint moments (NodalLoad.mx/my/mz),
* ground displacement / support settlement (LoadPattern.ground_displacements),
* shell load directions, projected loads and linear joint patterns
  (AreaLoad.direction / projected / joint_pattern),
* frame concentrated moments (MemberLoad kind="moment").

Every expected number is a closed-form hand result written in the test.
Units: kN, m, kPa.
"""

import json
import math
import warnings

import pytest

from skyframe.core.loads_ext import GroundDisplacement, shell_local_axes
from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection,
                                 LoadPattern, Material, MemberLoad, NodalLoad,
                                 PointSupport, ShellSection)
from skyframe.engine.opensees_engine import OpenSeesEngine

E = 2.0e8                     # kPa
I33 = 2.0e-4                  # m^4 (bends about local z = vertical plane)
I22 = 5.0e-5                  # m^4
A_SEC = 1.0e-2
J_SEC = 1.0e-4
FIX = (1, 1, 1, 1, 1, 1)


def _base_model(name="t"):
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("STL", E=E, nu=0.3, unit_weight=0.0))
    mdl.add_section(FrameSection("B", "STL", A=A_SEC, I33=I33, I22=I22,
                                 J=J_SEC))
    return mdl


def _solve(mdl, case="C"):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mdl.validate()
        eng = OpenSeesEngine(mdl)
        res = eng.run_static(case)
    return eng, res


def _tag(eng, pt):
    return eng._find_node(eng._asm, pt)


def _cantilever(L=4.0):
    mdl = _base_model()
    mdl.add_member("beam", "B", (0, 0, 0), (L, 0, 0), uid="M1")
    mdl.supports.append(PointSupport((0.0, 0.0, 0.0), FIX))
    return mdl


# --------------------------------------------------------------------------- #
# 1. joint moments
# --------------------------------------------------------------------------- #
def test_cantilever_tip_moment_my_rotation_and_deflection():
    """Tip couple My = M about global +Y on a cantilever along +X bends it in
    the vertical plane (I33): ry = M L / E I33, uz = -M L^2 / (2 E I33)."""
    L, M = 4.0, 30.0
    mdl = _cantilever(L)
    mdl.pattern("P").nodal_loads.append(NodalLoad((L, 0, 0), my=M))
    mdl.add_case("C", {"P": 1.0})
    eng, res = _solve(mdl)
    d = res.node_disp[_tag(eng, (L, 0, 0))]
    assert d[4] == pytest.approx(M * L / (E * I33), rel=1e-9)
    assert d[2] == pytest.approx(-M * L ** 2 / (2 * E * I33), rel=1e-9)
    assert res.base["MY"] == pytest.approx(-M, rel=1e-9)


def test_cantilever_tip_moment_mz_uses_minor_axis():
    """Mz about global Z bends in the horizontal plane (I22):
    rz = M L / E I22, uy = +M L^2 / (2 E I22)."""
    L, M = 4.0, 12.0
    mdl = _cantilever(L)
    mdl.pattern("P").nodal_loads.append(NodalLoad((L, 0, 0), mz=M))
    mdl.add_case("C", {"P": 1.0})
    eng, res = _solve(mdl)
    d = res.node_disp[_tag(eng, (L, 0, 0))]
    assert d[5] == pytest.approx(M * L / (E * I22), rel=1e-9)
    assert d[1] == pytest.approx(M * L ** 2 / (2 * E * I22), rel=1e-9)


def test_torsion_joint_moment_mx():
    """mx: twist = T L / (G J), G = E / (2 (1 + nu))."""
    L, T = 4.0, 5.0
    mdl = _cantilever(L)
    mdl.pattern("P").nodal_loads.append(NodalLoad((L, 0, 0), mx=T))
    mdl.add_case("C", {"P": 1.0})
    eng, res = _solve(mdl)
    G = E / (2 * 1.3)
    assert res.node_disp[_tag(eng, (L, 0, 0))][3] == pytest.approx(
        T * L / (G * J_SEC), rel=1e-9)


def test_joint_moment_case_scale():
    L, M = 4.0, 30.0
    mdl = _cantilever(L)
    mdl.pattern("P").nodal_loads.append(NodalLoad((L, 0, 0), my=M))
    mdl.add_case("C", {"P": 2.5})
    eng, res = _solve(mdl)
    assert res.node_disp[_tag(eng, (L, 0, 0))][4] == pytest.approx(
        2.5 * M * L / (E * I33), rel=1e-9)


# --------------------------------------------------------------------------- #
# 2. frame concentrated moments
# --------------------------------------------------------------------------- #
def test_member_moment_cantilever_midspan():
    """Couple M about local z at a = L/2 on a cantilever (local z = -Y for a
    beam along +X): tip rotation magnitude M a / EI33 and tip deflection
    M a (L - a/2) / EI33; M3 constant |M| over [0, a), zero beyond."""
    L, M = 4.0, 20.0
    a = L / 2
    mdl = _cantilever(L)
    mdl.pattern("P").member_loads.append(
        MemberLoad("M1", kind="moment", w=M, a=0.5, direction="local_z"))
    mdl.add_case("C", {"P": 1.0})
    eng, res = _solve(mdl)
    d = res.node_disp[_tag(eng, (L, 0, 0))]
    assert abs(d[4]) == pytest.approx(M * a / (E * I33), rel=1e-9)
    assert abs(d[2]) == pytest.approx(M * a * (L - a / 2) / (E * I33),
                                      rel=1e-9)
    # equals the same couple as a joint moment at a split node (-Y)
    mdl2 = _base_model()
    mdl2.add_member("beam", "B", (0, 0, 0), (a, 0, 0), uid="M1")
    mdl2.add_member("beam", "B", (a, 0, 0), (L, 0, 0), uid="M2")
    mdl2.supports.append(PointSupport((0.0, 0.0, 0.0), FIX))
    mdl2.pattern("P").nodal_loads.append(NodalLoad((a, 0, 0), my=-M))
    mdl2.add_case("C", {"P": 1.0})
    eng2, res2 = _solve(mdl2)
    d2 = res2.node_disp[_tag(eng2, (L, 0, 0))]
    assert d[2] == pytest.approx(d2[2], rel=1e-9)
    assert d[4] == pytest.approx(d2[4], rel=1e-9)
    st = res.member_stations["M1"]
    for x, m3 in zip(st["x"], st["M3"]):
        if x < a - 1e-6:
            assert abs(m3) == pytest.approx(M, rel=1e-9)
        elif x > a + 1e-6:
            assert abs(m3) == pytest.approx(0.0, abs=1e-9)
    # v0.16 closed-form deflection stations honor the couple: tip local-y
    # deflection equals the nodal uz (local y = +Z for this beam)
    dl = res.member_deflections["M1"]
    assert dl["dy"][-1] == pytest.approx(d[2], rel=1e-9)
    # midspan: theta-free cantilever part, v(a) = -/+ M a^2 / (2 EI)
    assert abs(dl["dy"][5]) == pytest.approx(M * a ** 2 / (2 * E * I33),
                                             rel=1e-9)


def test_member_moment_fixed_fixed_fef():
    """Fixed-fixed beam, couple M at midspan: R = 6 M a b / L^3 = 1.5 M / L,
    end moments M b (2a - b) / L^2 = M / 4 (exact fixed-end forces)."""
    L, M = 6.0, 24.0
    mdl = _base_model()
    mdl.add_member("beam", "B", (0, 0, 0), (L, 0, 0), uid="M1")
    mdl.supports.append(PointSupport((0.0, 0.0, 0.0), FIX))
    mdl.supports.append(PointSupport((L, 0.0, 0.0), FIX))
    mdl.pattern("P").member_loads.append(
        MemberLoad("M1", kind="moment", w=M, a=0.5, direction="global_y"))
    mdl.add_case("C", {"P": 1.0})
    eng, res = _solve(mdl)
    ra = res.reactions[_tag(eng, (0, 0, 0))]
    rb = res.reactions[_tag(eng, (L, 0, 0))]
    assert abs(ra[2]) == pytest.approx(1.5 * M / L, rel=1e-9)
    assert ra[2] == pytest.approx(-rb[2], rel=1e-9)
    assert abs(ra[4]) == pytest.approx(M / 4, rel=1e-9)
    assert abs(rb[4]) == pytest.approx(M / 4, rel=1e-9)
    assert res.base["MY"] + M == pytest.approx(0.0, abs=1e-8)


def test_member_moment_with_release_propped():
    """Fixed at A, end j released (Mj) onto a fixed node: propped cantilever
    with couple M at a = L/2: prop force R = 3 M a (L - a/2) / L^3 = 9M/(8L)
    (condensed fixed-end path)."""
    L, M = 6.0, 16.0
    mdl = _base_model()
    mdl.add_member("beam", "B", (0, 0, 0), (L, 0, 0), uid="M1",
                   releases="Mj")
    mdl.supports.append(PointSupport((0.0, 0.0, 0.0), FIX))
    mdl.supports.append(PointSupport((L, 0.0, 0.0), FIX))
    mdl.pattern("P").member_loads.append(
        MemberLoad("M1", kind="moment", w=M, a=0.5, direction="local_3"))
    mdl.add_case("C", {"P": 1.0})
    eng, res = _solve(mdl)
    rb = res.reactions[_tag(eng, (L, 0, 0))]
    assert abs(rb[2]) == pytest.approx(9 * M / (8 * L), rel=1e-9)
    assert abs(rb[4]) == pytest.approx(0.0, abs=1e-9)


def test_member_moment_validation():
    mdl = _cantilever()
    mdl.pattern("P").member_loads.append(
        MemberLoad("M1", kind="moment", w=1.0, a=0.5, direction="gravity"))
    with pytest.raises(ValueError, match="moment load direction"):
        mdl.validate()


# --------------------------------------------------------------------------- #
# 3. ground displacement
# --------------------------------------------------------------------------- #
def _two_span(L=5.0, middle_spring_kz=None):
    mdl = _base_model()
    mdl.add_member("beam", "B", (0, 0, 0), (L, 0, 0), uid="M1")
    mdl.add_member("beam", "B", (L, 0, 0), (2 * L, 0, 0), uid="M2")
    pin = (1, 1, 1, 1, 0, 0)
    roll = (0, 1, 1, 0, 0, 0)
    mdl.supports.append(PointSupport((0.0, 0.0, 0.0), pin))
    if middle_spring_kz is None:
        mdl.supports.append(PointSupport((L, 0.0, 0.0), roll))
    else:
        mdl.add_spring_support((L, 0.0, 0.0),
                               [0, 0, middle_spring_kz, 0, 0, 0])
    mdl.supports.append(PointSupport((2 * L, 0.0, 0.0), roll))
    return mdl


def test_settlement_middle_support_two_span():
    """Middle support of a 2 x L continuous beam settles by D: exact
    R_B = -6 E I D / L^3 (pulls down), R_A = R_C = 3 E I D / L^3,
    M_B = 3 E I D / L^2."""
    L, D = 5.0, 0.01
    mdl = _two_span(L)
    mdl.pattern("S").ground_displacements.append(
        GroundDisplacement((L, 0.0, 0.0), uz=-D))
    mdl.add_case("C", {"S": 1.0})
    eng, res = _solve(mdl)
    EI = E * I33
    assert res.node_disp[_tag(eng, (L, 0, 0))][2] == pytest.approx(-D,
                                                                    rel=1e-9)
    assert res.reactions[_tag(eng, (L, 0, 0))][2] == pytest.approx(
        -6 * EI * D / L ** 3, rel=1e-8)
    for x in (0.0, 2 * L):
        assert res.reactions[_tag(eng, (x, 0, 0))][2] == pytest.approx(
            3 * EI * D / L ** 3, rel=1e-8)
    mb = res.member_forces["M1"]          # end j bending moment
    assert abs(mb[11]) == pytest.approx(3 * EI * D / L ** 2, rel=1e-8)
    assert res.base["FZ"] == pytest.approx(0.0, abs=1e-6)


def test_settlement_end_support_two_span():
    """End support A settles by D.  Flexibility method on the released beam
    (2L simply supported on A, C): d_BB = L^3/(6EI); the settlement of A
    lowers the chord at B by D/2, so the redundant must push B up by D/2:
    X_B = (D/2)/d_BB = 3 E I D / L^3 (upward), R_A = R_C = -X_B/2."""
    L, D = 5.0, 0.008
    mdl = _two_span(L)
    mdl.pattern("S").ground_displacements.append(
        GroundDisplacement((0.0, 0.0, 0.0), uz=-D))
    mdl.add_case("C", {"S": 1.0})
    eng, res = _solve(mdl)
    EI = E * I33
    xb = 3 * EI * D / L ** 3
    assert res.reactions[_tag(eng, (L, 0, 0))][2] == pytest.approx(xb,
                                                                    rel=1e-8)
    assert res.reactions[_tag(eng, (0, 0, 0))][2] == pytest.approx(-xb / 2,
                                                                    rel=1e-8)


def test_settlement_through_spring_ground_end():
    """Spring support at B (kz): the ground end moves -D.  Spring in series
    with the beam flexibility d_BB = L^3/(6 EI):
    R = D / (d_BB + 1/k), reaction at B = -R; reported via the spring
    deformation (u - u_ground)."""
    L, D, k = 5.0, 0.01, 2.0e4
    mdl = _two_span(L, middle_spring_kz=k)
    mdl.pattern("S").ground_displacements.append(
        GroundDisplacement((L, 0.0, 0.0), uz=-D))
    mdl.add_case("C", {"S": 1.0})
    eng, res = _solve(mdl)
    EI = E * I33
    R = D / (L ** 3 / (6 * EI) + 1.0 / k)
    assert res.reactions[_tag(eng, (L, 0, 0))][2] == pytest.approx(-R,
                                                                    rel=1e-8)
    assert res.base["FZ"] == pytest.approx(0.0, abs=1e-6)
    uB = res.node_disp[_tag(eng, (L, 0, 0))][2]
    assert uB == pytest.approx(-D + R / k, rel=1e-8)


def test_settlement_rotation_fixed_end():
    """Fixed-fixed beam, end A rotates by th (ry): end moments
    4EI th / L (A) and 2EI th / L (B), shear 6 EI th / L^2."""
    L, th = 4.0, 0.002
    mdl = _base_model()
    mdl.add_member("beam", "B", (0, 0, 0), (L, 0, 0), uid="M1")
    mdl.add_member("beam", "B", (L, 0, 0), (2 * L, 0, 0), uid="M2")
    mdl.supports.append(PointSupport((0.0, 0.0, 0.0), FIX))
    mdl.supports.append(PointSupport((2 * L, 0.0, 0.0), FIX))
    mdl.pattern("S").ground_displacements.append(
        GroundDisplacement((0.0, 0.0, 0.0), ry=th))
    mdl.add_case("C", {"S": 1.0})
    eng, res = _solve(mdl)
    EI, Lt = E * I33, 2 * L
    ra = res.reactions[_tag(eng, (0, 0, 0))]
    rb = res.reactions[_tag(eng, (2 * L, 0, 0))]
    assert abs(ra[4]) == pytest.approx(4 * EI * th / Lt, rel=1e-8)
    assert abs(rb[4]) == pytest.approx(2 * EI * th / Lt, rel=1e-8)
    assert abs(ra[2]) == pytest.approx(6 * EI * th / Lt ** 2, rel=1e-8)


def test_ground_displacement_validation_and_free_dof():
    L = 5.0
    mdl = _two_span(L)
    mdl.pattern("S").ground_displacements.append(
        GroundDisplacement((2.5, 0.0, 0.0), uz=-0.01))
    mdl.add_case("C", {"S": 1.0})
    with pytest.raises(ValueError, match="not at a support"):
        mdl.validate()
    # a value on a FREE dof of a real support is ignored with a warning
    mdl2 = _two_span(L)
    mdl2.pattern("S").ground_displacements.append(
        GroundDisplacement((L, 0.0, 0.0), ux=0.01))
    mdl2.add_case("C", {"S": 1.0})
    mdl2.validate()
    with pytest.warns(UserWarning, match="free dof"):
        res = OpenSeesEngine(mdl2).run_static("C")
    assert max(abs(v) for r in res.reactions.values() for v in r) < 1e-9


# --------------------------------------------------------------------------- #
# 4. shell loads
# --------------------------------------------------------------------------- #
def _shell_model(corners, mesh=0.5, kind="wall", supports=None):
    mdl = _base_model()
    mdl.add_shell_section(ShellSection("SH", "STL", thickness=0.2))
    mdl.add_shell(kind, "shell", "SH", corners, mesh_size=mesh, uid="W1")
    for p in supports or []:
        mdl.supports.append(PointSupport(tuple(map(float, p)), FIX))
    return mdl


def test_hydrostatic_wall_resultant_and_location():
    """Vertical wall b x h, base fixed (auto base fixity), pressure
    p = gamma (h - z) along +Y via joint pattern (c = -1, d = h):
    resultant gamma h^2 b / 2, acting at h/3 above the base."""
    b, h, gamma = 2.0, 3.0, 10.0
    mdl = _shell_model([(0, 0, 0), (b, 0, 0), (b, 0, h), (0, 0, h)])
    mdl.pattern("H").area_loads.append(AreaLoad(
        "W1", gamma, direction="global_y",
        joint_pattern={"type": "linear", "c": -1.0, "d": h}))
    mdl.add_case("C", {"H": 1.0})
    eng, res = _solve(mdl)
    R = gamma * h ** 2 * b / 2
    assert res.base["FY"] == pytest.approx(-R, rel=1e-10)
    # reactions' moment about the base line (x axis) = R * h/3
    assert res.base["MX"] / R == pytest.approx(h / 3, rel=1e-10)


def test_hydrostatic_zero_negative_clips_above_water():
    """Wall H = 4 m, water depth h = 3 m (mesh line at z = 3): the pattern
    gamma (h - z) is clipped to 0 above the free surface -> same gamma h^2 b
    / 2 at h/3."""
    b, H, h, gamma = 2.0, 4.0, 3.0, 9.81
    mdl = _shell_model([(0, 0, 0), (b, 0, 0), (b, 0, H), (0, 0, H)])
    mdl.pattern("H").area_loads.append(AreaLoad(
        "W1", gamma, direction="global_y",
        joint_pattern={"type": "linear", "c": -1.0, "d": h,
                       "zero_negative": True}))
    mdl.add_case("C", {"H": 1.0})
    eng, res = _solve(mdl)
    R = gamma * h ** 2 * b / 2
    assert res.base["FY"] == pytest.approx(-R, rel=1e-10)
    assert res.base["MX"] / R == pytest.approx(h / 3, rel=1e-10)


_ROOF = [(0, 0, 0), (4, 0, 0), (4, 3, 2), (0, 3, 2)]   # inclined plane


def _roof(direction_loads):
    mdl = _shell_model(_ROOF, mesh=1.0, kind="slab", supports=_ROOF)
    pat = mdl.pattern("P")
    for kw in direction_loads:
        pat.area_loads.append(AreaLoad("W1", **kw))
    mdl.add_case("C", {"P": 1.0})
    return mdl


def test_local3_pressure_inclined_equals_global_components():
    """local_3 (normal) pressure q on the inclined roof == the global_y and
    global_z loads q*n_y and q*n_z; reaction sum = -q A n."""
    q = 5.0
    e1, e2, n = shell_local_axes(_roof([]).shells[0])
    eng, res = _solve(_roof([{"q": q, "direction": "local_3"}]))
    eng2, res2 = _solve(_roof([{"q": q * n[1], "direction": "global_y"},
                               {"q": q * n[2], "direction": "global_z"}]))
    A = 4.0 * math.sqrt(3.0 ** 2 + 2.0 ** 2)
    for key, k in (("FX", 0), ("FY", 1), ("FZ", 2)):
        assert res.base[key] == pytest.approx(-q * A * n[k], abs=1e-9)
        assert res.base[key] == pytest.approx(res2.base[key], abs=1e-9)
    for t, d in res.node_disp.items():
        for a, b in zip(d, res2.node_disp[t]):
            assert a == pytest.approx(b, rel=1e-8, abs=1e-14)


def test_local_axes_of_inclined_roof_and_local2_load():
    """e3 = (0, -2, 3)/sqrt(13), e1 = +X (horizontal), e2 = e3 x e1 =
    (0, 3, 2)/sqrt(13) up-slope; a local_2 load sums to -q A e2."""
    e1, e2, e3 = shell_local_axes(_roof([]).shells[0])
    s = math.sqrt(13.0)
    assert e1 == pytest.approx((1.0, 0.0, 0.0))
    assert e2 == pytest.approx((0.0, 3 / s, 2 / s))
    assert e3 == pytest.approx((0.0, -2 / s, 3 / s))
    q, A = 2.0, 4.0 * s
    eng, res = _solve(_roof([{"q": q, "direction": "local_2"}]))
    assert res.base["FY"] == pytest.approx(-q * A * 3 / s, rel=1e-10)
    assert res.base["FZ"] == pytest.approx(-q * A * 2 / s, rel=1e-10)


def test_projected_gravity_on_inclined_roof():
    """Projected gravity: total = q * plan area (4 x 3); unprojected:
    q * true area."""
    q = 3.0
    eng, res = _solve(_roof([{"q": q, "projected": True}]))
    assert res.base["FZ"] == pytest.approx(q * 12.0, rel=1e-10)
    eng2, res2 = _solve(_roof([{"q": q}]))
    assert res2.base["FZ"] == pytest.approx(q * 4.0 * math.sqrt(13.0),
                                            rel=1e-10)
    # takedown's independently summed applied FZ matches the projected load
    fz = eng._applied_gravity_fz(eng._asm, {"P": 1.0})
    assert fz == pytest.approx(q * 12.0, rel=1e-10)


def test_global_x_load_on_slab_reaction_sum():
    """Horizontal 4 x 3 slab, global_x p = 2 kPa: sum FX = -p * area."""
    pts = [(0, 0, 0), (4, 0, 0), (4, 3, 0), (0, 3, 0)]
    mdl = _shell_model(pts, mesh=1.0, kind="slab", supports=pts)
    mdl.pattern("P").area_loads.append(AreaLoad("W1", 2.0,
                                                direction="global_x"))
    mdl.add_case("C", {"P": 1.0})
    eng, res = _solve(mdl)
    assert res.base["FX"] == pytest.approx(-24.0, rel=1e-10)
    assert res.base["FZ"] == pytest.approx(0.0, abs=1e-9)


def test_local3_on_vertical_wall_equals_global_y():
    """Wall in the x-z plane with corners CCW: e3 = -Y, so local_3 q equals
    global_y with -q (node-by-node identical response)."""
    pts = [(0, 0, 0), (2, 0, 0), (2, 0, 3), (0, 0, 3)]
    m1 = _shell_model(pts)
    m1.pattern("P").area_loads.append(AreaLoad("W1", 4.0,
                                               direction="local_3"))
    m1.add_case("C", {"P": 1.0})
    m2 = _shell_model(pts)
    m2.pattern("P").area_loads.append(AreaLoad("W1", -4.0,
                                               direction="global_y"))
    m2.add_case("C", {"P": 1.0})
    _, r1 = _solve(m1)
    _, r2 = _solve(m2)
    assert r1.base["FY"] == pytest.approx(4.0 * 6.0, rel=1e-10)
    for t, d in r1.node_disp.items():
        assert d == pytest.approx(r2.node_disp[t], rel=1e-10, abs=1e-15)


def test_uniform_joint_pattern_equals_default_gravity():
    """Consistent integration with p = 1 (d = 1) on a rectangular mesh is the
    quarter-area tributary distribution: results equal the default load."""
    pts = [(0, 0, 0), (4, 0, 0), (4, 3, 0), (0, 3, 0)]
    m1 = _shell_model(pts, mesh=1.0, kind="slab", supports=pts)
    m1.pattern("P").area_loads.append(AreaLoad("W1", 5.0))
    m1.add_case("C", {"P": 1.0})
    m2 = _shell_model(pts, mesh=1.0, kind="slab", supports=pts)
    m2.pattern("P").area_loads.append(AreaLoad(
        "W1", 5.0, joint_pattern={"type": "linear", "d": 1.0}))
    m2.add_case("C", {"P": 1.0})
    _, r1 = _solve(m1)
    _, r2 = _solve(m2)
    for t, d in r1.node_disp.items():
        assert d == pytest.approx(r2.node_disp[t], rel=1e-10, abs=1e-15)


def test_area_load_validation():
    pts = [(0, 0, 0), (4, 0, 0), (4, 3, 0), (0, 3, 0)]
    mdl = _shell_model(pts, mesh=1.0, kind="slab", supports=pts)
    mdl.pattern("P").area_loads.append(AreaLoad("W1", 1.0,
                                                direction="sideways"))
    with pytest.raises(ValueError, match="area load direction"):
        mdl.validate()
    mdl.patterns["P"].area_loads[0] = AreaLoad(
        "W1", 1.0, direction="local_3", projected=True)
    with pytest.raises(ValueError, match="projected"):
        mdl.validate()
    mdl.patterns["P"].area_loads[0] = AreaLoad(
        "W1", 1.0, joint_pattern={"type": "quadratic"})
    with pytest.raises(ValueError, match="joint_pattern"):
        mdl.validate()


def test_membrane_rejects_directional_load():
    mdl = _base_model()
    mdl.add_shell_section(ShellSection("SH", "STL", thickness=0.2))
    pts = [(0, 0, 0), (4, 0, 0), (4, 3, 0), (0, 3, 0)]
    for a, b in zip(pts, pts[1:] + pts[:1]):
        mdl.add_member("beam", "B", a, b)
    mdl.add_shell("slab", "membrane", "SH", pts, uid="S1")
    mdl.pattern("P").area_loads.append(AreaLoad("S1", 1.0,
                                                direction="global_x"))
    with pytest.raises(ValueError, match="membrane"):
        mdl.validate()


# --------------------------------------------------------------------------- #
# 5. serialization
# --------------------------------------------------------------------------- #
def test_defaults_serialize_byte_identical():
    """Default loads serialize exactly as before the feature (no new keys)."""
    pat = LoadPattern("P")
    pat.nodal_loads.append(NodalLoad((1.0, 2.0, 3.0), fx=1.0, fz=-2.0))
    pat.area_loads.append(AreaLoad("S1", 4.0))
    d = pat.to_dict()
    assert d["nodal_loads"] == [{"point": (1.0, 2.0, 3.0), "fx": 1.0,
                                 "fy": 0.0, "fz": -2.0}]
    assert d["area_loads"] == [{"region_uid": "S1", "q": 4.0}]
    assert "ground_displacements" not in d
    assert list(d) == ["name", "kind", "member_udls", "nodal_loads",
                       "story_forces", "member_loads", "area_loads",
                       "thermal_loads", "accidental_torsion", "ecc",
                       "self_weight_factor"]


def test_model_round_trip_with_new_loads():
    L = 5.0
    mdl = _two_span(L)
    pat = mdl.pattern("S")
    pat.ground_displacements.append(GroundDisplacement((L, 0.0, 0.0), uz=-0.01))
    pat.nodal_loads.append(NodalLoad((10.0, 0.0, 0.0), fz=-3.0, my=4.0))
    pat.member_loads.append(MemberLoad("M2", kind="moment", w=7.0, a=0.3,
                                       direction="local_z"))
    mdl.add_shell_section(ShellSection("SH", "STL", thickness=0.2))
    mdl.add_shell("wall", "shell", "SH",
                  [(0, 2, 0), (2, 2, 0), (2, 2, 2), (0, 2, 2)], uid="W9")
    for x in (0.0, 2.0):
        mdl.supports.append(PointSupport((x, 2.0, 0.0), FIX))
    pat.area_loads.append(AreaLoad(
        "W9", 3.0, direction="local_3", joint_pattern={
            "type": "linear", "c": -1.0, "d": 2.0, "zero_negative": True}))
    pat.area_loads.append(AreaLoad("W9", 1.0, projected=True))
    d = mdl.to_dict()
    txt = json.dumps(d)
    mdl2 = BuildingModel.from_dict(json.loads(txt))
    assert json.dumps(mdl2.to_dict()) == txt
    p2 = mdl2.patterns["S"]
    assert p2.ground_displacements[0].uz == -0.01
    assert p2.nodal_loads[0].my == 4.0
    assert p2.area_loads[0].direction == "local_3"
    assert p2.area_loads[0].joint_pattern["zero_negative"] is True
    assert p2.area_loads[1].projected is True
    assert p2.member_loads[0].kind == "moment"
    # the round-tripped model solves identically
    mdl.add_case("C", {"S": 1.0})
    mdl2.add_case("C", {"S": 1.0})
    _, r1 = _solve(mdl)
    _, r2 = _solve(mdl2)
    for t, d in r1.node_disp.items():
        assert d == r2.node_disp[t]


def test_settlement_auto_base_rigid_body():
    """No explicit supports: the auto-fixed base of a single column may be
    given a ground displacement; a statically determinate cantilever just
    translates (top uz = -D, zero reactions)."""
    D = 0.02
    mdl = _base_model()
    mdl.add_member("column", "B", (0, 0, 0), (0, 0, 3.0), uid="C1")
    mdl.pattern("S").ground_displacements.append(
        GroundDisplacement((0.0, 0.0, 0.0), uz=-D))
    mdl.add_case("C", {"S": 1.0})
    eng, res = _solve(mdl)
    assert res.node_disp[_tag(eng, (0, 0, 3.0))][2] == pytest.approx(
        -D, rel=1e-12)
    assert max(abs(v) for r in res.reactions.values() for v in r) < 1e-9
