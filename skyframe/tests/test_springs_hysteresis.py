"""B9 + B11: named point-spring properties and link hysteresis types.

Every expected number is derived here from first principles:

Named springs
  * rotated linear spring: base displacement = R^T K^-1 R H (plan rotation
    by the support angle / the property's local axes; 1e-9);
  * compression-only foundation springs under overturning: the uplifting
    spring releases and the other carries M/a (hand statics), the linear
    property gives M/(2a);
  * gap spring: no force until it has closed by ``gap``, then
    u = -(gap + P/k) (1e-5);
  * multilinear spring: static Newton and pushover points lie exactly on
    the user curve; modal sees the initial slope (period in series with
    the cantilever, 1e-6);
  * defaults byte-identical: legacy to_dict shape, inline == named linear.

Link hysteresis (single zeroLength driven by DisplacementControl):
  * kinematic: unloading slope = initial, loop energy = 4 (Fy - k2 dy)
    (D - dy) for a bilinear backbone (1e-6);
  * Takeda: unloading at the initial slope, then aiming at the opposite
    (yield / peak) point;
  * pivot: reloading passes through the pinch point
    (d0 + px (d* - d0), py Fmax);
  * Bouc-Wen isolator: force at zero displacement on the loop = qd, large
    displacement force -> qd + alpha1 k u;
  * friction spring: slip force mu N, loop energy 4 mu N (D - mu N / k).
"""

import copy
import json
import math

import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine
ops = pytest.importorskip("openseespy.opensees")

from skyframe.core import springprops as sprp
from skyframe.core.model import (LINK_TYPES, BuildingModel, FrameSection,
                                 LinkMember, Material, NodalLoad, NodalMass,
                                 PointSupport)
from skyframe.engine import hysteresis as hyst

E_CONC = 25_000_000.0
E_STIFF = 2.0e11


def _node(coords, pt, tol=1e-9):
    for t, c in coords.items():
        if all(abs(a - b) < tol for a, b in zip(c, pt)):
            return t
    raise AssertionError(f"no node at {pt}")


def _column(L=4.0, E=E_CONC, size=0.5, restr=(0, 0, 1, 1, 1, 1)):
    mdl = BuildingModel(name="col")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("M", E=E, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "M", size, size))
    mdl.set_stories([L])
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), tuple(restr)))
    return mdl


def _static(mdl, name, **load):
    L = mdl.stories[-1].elevation
    mdl.pattern(name, "other").nodal_loads.append(NodalLoad((0, 0, L),
                                                            **load))
    mdl.add_case(name, {name: 1.0})


# =========================================================================
# 1. named point springs: data model
# =========================================================================
PROPS = {
    "LIN": {"kind": "linear", "k": [1000.0, 2000.0, 0, 0, 0, 0],
            "local_axes": {"angle_deg": 15.0}},
    "ML": {"kind": "multilinear",
           "curves": {"U1": [[0.01, 100.0], [0.03, 150.0]]}},
    "CO": {"kind": "compression_only", "k": [0, 0, 5000.0, 0, 0, 0]},
    "GAP": {"kind": "gap", "k": [0, 0, 5000.0, 0, 0, 0], "gap": 0.01,
            "nonlinear_dof": "U3"},
    "MAT": {"kind": "tension_only", "k": [0, 0, 10.0, 0, 0, 0],
            "local_axes": [[0, 1, 0], [-1, 0, 0], [0, 0, 1]]},
}


def test_round_trip_and_json_shape():
    mdl = _column()
    for n, p in PROPS.items():
        mdl.add_spring_property(n, p)
    mdl.add_spring_support((0, 0, 0), property="LIN", angle_deg=30.0)
    lk = mdl.add_link((5, 0, 0), (5, 0, 0.3), link_type="multilinear_pivot",
                      params={"points": [[0.01, 10], [0.05, 20]],
                              "points_neg": [[-0.01, -12], [-0.05, -25]],
                              "pinch_x": 0.4})
    d = json.loads(json.dumps(mdl.to_dict()))
    assert d["spring_supports"][0] == {"point": [0.0, 0.0, 0.0],
                                       "stiffness": [0.0] * 6,
                                       "property": "LIN", "angle_deg": 30.0}
    assert d["spring_properties"]["GAP"]["gap"] == 0.01
    m2 = BuildingModel.from_dict(d)
    m2.validate()
    assert m2.to_dict() == mdl.to_dict()
    assert m2.links[0].params == lk.params


def test_defaults_byte_identical_shape():
    mdl = _column()
    mdl.add_spring_support((0, 0, 0), [800.0, 0, 0, 0, 0, 0])
    d = mdl.to_dict()
    assert "spring_properties" not in d
    assert d["spring_supports"] == [{"point": [0.0, 0.0, 0.0],
                                     "stiffness": [800.0, 0, 0, 0, 0, 0]}]
    assert LINK_TYPES[:8] == ("elastic", "damper", "gap", "hook", "isolator",
                              "fp_isolator", "triple_fp", "multilinear")


def test_inline_equals_named_linear_exactly():
    """The named path with global axes reproduces the inline spring."""
    res = []
    for named in (False, True):
        mdl = _column()
        if named:
            mdl.add_spring_property("K", {"kind": "linear",
                                          "k": [800.0, 900.0, 0, 0, 0, 0]})
            mdl.add_spring_support((0, 0, 0), property="K")
        else:
            mdl.add_spring_support((0, 0, 0), [800.0, 900.0, 0, 0, 0, 0])
        _static(mdl, "H", fx=30.0, fy=-10.0)
        r = OpenSeesEngine(mdl).run_static("H")
        res.append((r.node_disp, r.reactions, r.base))
    for t in res[0][0]:
        assert res[1][0][t] == pytest.approx(res[0][0][t], rel=1e-12,
                                             abs=1e-15)
    for k in res[0][2]:
        assert res[1][2][k] == pytest.approx(res[0][2][k], abs=1e-9)


@pytest.mark.parametrize("prop, msg", [
    ({"kind": "plastic", "k": [1] * 6}, "kind"),
    ({"kind": "linear", "k": [1, 2]}, "6 finite"),
    ({"kind": "linear", "k": [0] * 6}, "at least one"),
    ({"kind": "multilinear"}, "curves"),
    ({"kind": "multilinear", "curves": {"UX": [[0.1, 1]]}}, "curve DOF"),
    ({"kind": "multilinear", "curves": {"U1": [[-0.1, -1], [0.1, 1]]}},
     "through"),
    ({"kind": "multilinear", "curves": {"U1": [[0.2, 1], [0.1, 2]]}},
     "increasing"),
    ({"kind": "compression_only", "k": [1, 0, 0, 0, 0, 0]}, "nonlinear dof"),
    ({"kind": "gap", "k": [0, 0, 1, 0, 0, 0], "gap": -0.1}, "gap must"),
    ({"kind": "linear", "k": [1] * 6, "gap": 0.1}, "not valid"),
    ({"kind": "linear", "k": [1] * 6,
      "local_axes": [[1, 0, 0], [1, 0, 0], [0, 0, 1]]}, "orthonormal"),
    ({"kind": "linear", "k": [1] * 6,
      "local_axes": [[0, 1, 0], [1, 0, 0], [0, 0, 1]]}, "right-handed"),
    ({"kind": "linear", "k": [1] * 6, "bogus": 1}, "unknown key"),
])
def test_property_validation(prop, msg):
    mdl = _column()
    with pytest.raises(ValueError, match=msg):
        mdl.add_spring_property("P", prop)


def test_support_reference_validation():
    mdl = _column()
    with pytest.raises(ValueError, match="unknown spring property"):
        mdl.add_spring_support((0, 0, 0), property="NOPE")
    mdl.add_spring_property("K", {"kind": "linear", "k": [1] * 6})
    with pytest.raises(ValueError, match="all zero"):
        mdl.add_spring_support((0, 0, 0), [1, 0, 0, 0, 0, 0], property="K")
    with pytest.raises(ValueError, match="angle_deg"):
        mdl.add_spring_support((0, 0, 0), [1, 0, 0, 0, 0, 0],
                               angle_deg=float("nan"))
    d = mdl.to_dict()
    d["spring_supports"] = [{"point": [0, 0, 0], "property": "GHOST"}]
    with pytest.raises(ValueError, match="GHOST"):
        BuildingModel.from_dict(d).validate()


# =========================================================================
# 2. named point springs: engine vs hand
# =========================================================================
@pytest.mark.parametrize("how", ["support_angle", "property_angle",
                                 "property_matrix", "inline_angle"])
def test_rotated_linear_spring_flexibility(how):
    """u_base = R^T diag(1/k1, 1/k2) R H for a plan rotation theta."""
    k1, k2, th, H = 1500.0, 4000.0, 35.0, 60.0
    mdl = _column()
    c, s = math.cos(math.radians(th)), math.sin(math.radians(th))
    k = [k1, k2, 0, 0, 0, 0]
    if how == "support_angle":
        mdl.add_spring_property("K", {"kind": "linear", "k": k})
        mdl.add_spring_support((0, 0, 0), property="K", angle_deg=th)
    elif how == "property_angle":
        mdl.add_spring_property("K", {"kind": "linear", "k": k,
                                      "local_axes": {"angle_deg": th}})
        mdl.add_spring_support((0, 0, 0), property="K")
    elif how == "property_matrix":
        mdl.add_spring_property("K", {"kind": "linear", "k": k,
                                      "local_axes": [[c, s, 0], [-s, c, 0],
                                                     [0, 0, 1]]})
        mdl.add_spring_support((0, 0, 0), property="K")
    else:
        mdl.add_spring_support((0, 0, 0), k, angle_deg=th)
    _static(mdl, "H", fx=H)
    eng = OpenSeesEngine(mdl)
    r = eng.run_static("H")
    base = _node(eng._asm.node_coords, (0, 0, 0))
    # local: u1 = c ux + s uy, u2 = -s ux + c uy;  u = R^T K^-1 R [H, 0]
    f1, f2 = c * H / k1, -s * H / k2
    ux, uy = c * f1 - s * f2, s * f1 + c * f2
    assert r.node_disp[base][0] == pytest.approx(ux, rel=1e-9)
    assert r.node_disp[base][1] == pytest.approx(uy, rel=1e-9)
    assert abs(uy) > 0.1 * abs(ux)                 # coupling is real
    assert r.reactions[base][0] == pytest.approx(-H, rel=1e-9)
    assert r.reactions[base][1] == pytest.approx(0.0, abs=1e-8)
    assert r.base["FX"] == pytest.approx(-H, rel=1e-9)


def _overturning(kind):
    """Base beam (-a..a) on a center pin + two vertical springs at +-a;
    a column at the center pushed by H at height h: M = H h."""
    a, h, k, H = 2.0, 3.0, 1.0e5, 40.0
    mdl = BuildingModel(name="ot")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("M", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("S", "M", 0.5, 0.5))
    mdl.set_stories([h])
    mdl.add_member("beam", "S", (-a, 0, 0), (0, 0, 0), uid="B1")
    mdl.add_member("beam", "S", (0, 0, 0), (a, 0, 0), uid="B2")
    mdl.add_member("column", "S", (0, 0, 0), (0, 0, h), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 0, 1)))
    mdl.add_spring_property("F", {"kind": kind, "k": [0, 0, k, 0, 0, 0]})
    for x in (-a, a):
        mdl.add_spring_support((x, 0, 0), property="F")
    _static(mdl, "H", fx=H)
    eng = OpenSeesEngine(mdl)
    r = eng.run_static("H")
    co = eng._asm.node_coords
    return (r, _node(co, (-a, 0, 0)), _node(co, (a, 0, 0)), H * h / a)


def test_compression_only_spring_releases_under_uplift():
    r, nl, nr, M_over_a = _overturning("compression_only")
    assert r.node_disp[nl][2] > 0.0                       # uplift
    assert abs(r.reactions[nl][2]) < 1e-4 * M_over_a      # released
    assert r.reactions[nr][2] == pytest.approx(M_over_a, rel=1e-5)
    # the linear property shares the moment: +-M/(2a)
    r2, nl2, nr2, _ = _overturning("linear")
    assert r2.reactions[nr2][2] == pytest.approx(M_over_a / 2, rel=1e-6)
    assert r2.reactions[nl2][2] == pytest.approx(-M_over_a / 2, rel=1e-6)
    assert r.base["FZ"] == pytest.approx(0.0, abs=1e-6)   # equilibrium


def test_tension_only_spring_mirror():
    r, nl, nr, M_over_a = _overturning("tension_only")
    assert abs(r.reactions[nr][2]) < 1e-4 * M_over_a      # pushes: off
    assert r.reactions[nl][2] == pytest.approx(-M_over_a, rel=1e-5)


def test_gap_spring_closes_at_the_gap():
    k, g, P = 5000.0, 0.01, 200.0
    mdl = _column(restr=(1, 1, 0, 1, 1, 1))
    mdl.add_spring_property("G", {"kind": "gap", "k": [0, 0, k, 0, 0, 0],
                                  "gap": g})
    mdl.add_spring_support((0, 0, 0), property="G")
    _static(mdl, "P", fz=-P)
    eng = OpenSeesEngine(mdl)
    r = eng.run_static("P")
    base = _node(eng._asm.node_coords, (0, 0, 0))
    uz = r.node_disp[base][2]
    r_ = sprp.AXIAL_ONLY_RATIO
    assert uz == pytest.approx(-(P + k * g) / (k * (1 + r_)), rel=1e-9)
    assert uz == pytest.approx(-(g + P / k), rel=1e-5)
    assert r.reactions[base][2] == pytest.approx(P, rel=1e-9)
    # the force law: zero before closing, k (|u| - g) after
    law = sprp.DofLaw("gap", k=k, gap=g)
    assert law.force(-0.5 * g) == pytest.approx(-0.5 * g * k * r_)
    assert law.force(-3 * g) == pytest.approx(-2 * g * k - 3 * g * k * r_)


CURVE = [[0.01, 100.0], [0.03, 150.0]]       # k0 = 1e4, k1 = 2500


def _curve_u(F):
    """Inverse of the (mirrored) CURVE with end extrapolation."""
    if F <= 100.0:
        return F / 1.0e4
    return 0.01 + (F - 100.0) / 2500.0


@pytest.mark.parametrize("H", [50.0, 120.0, -140.0, 170.0])
def test_multilinear_spring_static_follows_curve(H):
    mdl = _column(restr=(0, 1, 1, 1, 1, 1))
    mdl.add_spring_property("ML", {"kind": "multilinear",
                                   "curves": {"U1": CURVE}})
    mdl.add_spring_support((0, 0, 0), property="ML")
    _static(mdl, "H", fx=H)
    eng = OpenSeesEngine(mdl)
    r = eng.run_static("H")
    base = _node(eng._asm.node_coords, (0, 0, 0))
    u = r.node_disp[base][0]
    assert u == pytest.approx(math.copysign(_curve_u(abs(H)), H), rel=1e-9)
    assert r.reactions[base][0] == pytest.approx(-H, rel=1e-9)


def test_multilinear_spring_pushover_follows_curve():
    """Cantilever on the multilinear U1 spring (no frame hinges): every
    pushover point (roof u, V) satisfies u = curve^-1(V) + V L^3 / 3EI
    (spring and elastic cantilever in series) to solver precision."""
    L = 3.0
    mdl = _column(L=L, restr=(0, 1, 1, 1, 1, 1))
    mdl.add_spring_property("ML", {"kind": "multilinear",
                                   "curves": {"U1": CURVE}})
    mdl.add_spring_support((0, 0, 0), property="ML")
    mdl.add_pushover_case("PO", "X", target_drift=0.03, steps=45)
    po = OpenSeesEngine(mdl).run_pushover("PO")
    assert len(po.roof_disp) == 45 and not po.warnings
    assert po.roof_disp[-1] == pytest.approx(0.09, rel=1e-9)
    f_col = L ** 3 / (3.0 * E_CONC * mdl.sections["COL"].I22)
    for u, V in zip(po.roof_disp, po.base_shear):
        assert V > 0
        assert u == pytest.approx(_curve_u(V) + V * f_col, rel=1e-8)
    # the curve was followed through the knee (100) and past its last
    # point (150, end segment extrapolated)
    assert min(po.base_shear) < 100.0 < 150.0 < max(po.base_shear)


def test_nonlinear_spring_modal_uses_initial_stiffness():
    """Modal (linear) sees k0 = 1e4 of the multilinear spring: X sway of a
    tip mass on a cantilever in series with the spring."""
    L, m = 4.0, 10.0
    mdl = _column(L=L, restr=(0, 1, 1, 1, 1, 1))
    mdl.num_modes = 3
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=m, my=m))
    mdl.add_spring_property("ML", {"kind": "multilinear",
                                   "curves": {"U1": CURVE}})
    mdl.add_spring_support((0, 0, 0), property="ML")
    sec = mdl.sections["COL"]
    flex = 1.0 / 1.0e4 + L ** 3 / (3.0 * E_CONC * sec.I22)
    T = 2.0 * math.pi * math.sqrt(m * flex)
    periods = OpenSeesEngine(mdl).run_modal().periods
    assert min(abs(p - T) / T for p in periods) < 1e-6


# =========================================================================
# 3. link hysteresis: single zeroLength under a displacement history
# =========================================================================
def _drive_link(ltype, prm, path, n=40, N=0.0):
    """Ground node 1 -> node 2 (zero length), the builder's own material /
    element, driven along global X; returns [(u, F)] per step (F = force
    the link exerts back = -reaction)."""
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0.0, 0.0, 0.0)
    ops.node(2, 0.0, 0.0, 0.0)
    ops.fix(1, 1, 1, 1, 1, 1, 1)
    ops.fix(2, 0, 1, 0, 1, 1, 1)
    if ltype in sprp.MATERIAL_LINK_TYPES:
        mats, dirs, _ = hyst.material_link(ltype, prm, 0, True)
        ops.element("zeroLength", 1, 1, 2, "-mat", *mats, "-dir", *dirs)
    else:
        hyst.bearing_link(ltype, prm, 1, 2, 0, 0, 0, True)
    ops.constraints("Plain")
    ops.numberer("Plain")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1e-10, 50)
    ops.algorithm("Newton")
    if N:
        ops.timeSeries("Linear", 1)
        ops.pattern("Plain", 1, 1)
        ops.load(2, 0, 0, -N, 0, 0, 0)
        ops.integrator("LoadControl", 1.0)
        ops.analysis("Static")
        assert ops.analyze(1) == 0
        ops.loadConst("-time", 0.0)
    ops.timeSeries("Linear", 2)
    ops.pattern("Plain", 2, 2)
    ops.load(2, 1, 0, 0, 0, 0, 0)
    out, u = [(0.0, 0.0)], 0.0
    for target in path:
        ops.integrator("DisplacementControl", 2, 1, (target - u) / n)
        ops.analysis("Static")
        for _ in range(n):
            assert ops.analyze(1) == 0
            ops.reactions()
            out.append((ops.nodeDisp(2, 1), -ops.nodeReaction(1, 1)))
        u = target
    ops.wipe()
    return out


def _energy(hist):
    return sum(0.5 * (f0 + f1) * (u1 - u0)
               for (u0, f0), (u1, f1) in zip(hist[:-1], hist[1:]))


BB = [[0.01, 100.0], [0.03, 200.0]]          # k0 = 1e4, k2 = 5000
D = 0.02


def test_kinematic_bilinear_loop():
    h = _drive_link("multilinear_kinematic", {"points": BB}, [D, -D, D])
    # backbone on first loading
    assert h[20] == pytest.approx((0.01, 100.0), rel=1e-9)
    assert h[40] == pytest.approx((0.02, 150.0), rel=1e-9)
    # unloading slope = initial
    (u0, f0), (u1, f1) = h[40], h[41]
    assert (f1 - f0) / (u1 - u0) == pytest.approx(1.0e4, rel=1e-9)
    # kinematic: elastic range 2 Fy, then hardening k2: F(-D) = -150
    assert h[80][1] == pytest.approx(-150.0, rel=1e-9)
    E = _energy(h[40:])                       # closed cycle +D -> -D -> +D
    k2, dy, Fy = 5000.0, 0.01, 100.0
    assert E == pytest.approx(4.0 * (Fy - k2 * dy) * (D - dy), rel=1e-6)
    assert E > 0


def test_takeda_unloading_and_peak_oriented_reloading():
    h = _drive_link("multilinear_takeda", {"points": BB}, [D, -D, D], n=40)
    (u0, f0), (u1, f1) = h[40], h[41]
    assert (f1 - f0) / (u1 - u0) == pytest.approx(1.0e4, rel=1e-9)
    # zero force at d0 = 0.02 - 150/1e4 = 0.005, then aim at the opposite
    # first (yield) point (-0.01, -100): F(0) = -100 * 0.005 / 0.015
    f_at = dict((round(u, 9), f) for u, f in h[40:81])
    assert f_at[0.0] == pytest.approx(-100.0 * 0.005 / 0.015, rel=1e-9)
    assert h[80][1] == pytest.approx(-150.0, rel=1e-9)   # on the backbone
    # reload: zero at -0.005, then aim at the opposite PEAK (0.02, 150)
    f_up = dict((round(u, 9), f) for u, f in h[80:])
    assert f_up[0.0] == pytest.approx(150.0 * 0.005 / 0.025, rel=1e-9)
    assert h[-1][1] == pytest.approx(150.0, rel=1e-9)
    assert _energy(h[40:]) > 0.0
    # Takeda dissipates less than the kinematic loop (smaller loop area)
    hk = _drive_link("multilinear_kinematic", {"points": BB}, [D, -D, D])
    assert _energy(h[40:]) < _energy(hk[40:])


def test_pivot_reloading_through_pinch_point():
    px, py = 0.5, 0.25
    prm = {"points": BB, "pinch_x": px, "pinch_y": py}
    h = _drive_link("multilinear_pivot", prm, [D, -D, D], n=64)
    # third leg starts at (-D, -150); d0 = -D + 150/1e4 = -0.005
    Fmax, d0 = 150.0, -0.005
    d_star = D - (1.0 - py) * Fmax / 1.0e4
    P = (d0 + px * (d_star - d0), py * Fmax)
    f_up = dict((round(u, 9), f) for u, f in h[128:])
    assert f_up[round(P[0], 9)] == pytest.approx(P[1], rel=1e-9)
    # kinks there: slope before = P/(P-d0), after = (Fmax-Fp)/(D-P)
    up = h[128:]
    i = [round(u, 9) for u, _ in up].index(round(P[0], 9))
    s_before = (up[i][1] - up[i - 1][1]) / (up[i][0] - up[i - 1][0])
    s_after = (up[i + 1][1] - up[i][1]) / (up[i + 1][0] - up[i][0])
    assert s_before == pytest.approx(P[1] / (P[0] - d0), rel=1e-6)
    assert s_after == pytest.approx((Fmax - P[1]) / (D - P[0]), rel=1e-6)
    assert h[-1][1] == pytest.approx(Fmax, rel=1e-9)
    ht = _drive_link("multilinear_takeda", {"points": BB}, [D, -D, D], n=64)
    e_p, e_t = _energy(h[64:]), _energy(ht[64:])
    assert 0.0 < e_p < e_t                     # pinching reduces the loop


def test_bouc_wen_isolator_reaches_characteristic_strength():
    k0, qd, a1, Dm = 1.0e4, 100.0, 0.1, 0.2
    prm = {"k_init": k0, "qd": qd, "alpha1": a1}
    h = _drive_link("rubber_isolator_bouc_wen", prm, [Dm, -Dm, Dm], n=80,
                    N=500.0)
    # large displacement: F -> qd + alpha1 k0 u
    assert h[80][1] == pytest.approx(qd + a1 * k0 * Dm, rel=1e-3)
    # on the fully-yielded loop the force at u = 0 is -qd / +qd
    f_dn = dict((round(u, 9), f) for u, f in h[80:161])
    f_up = dict((round(u, 9), f) for u, f in h[160:])
    assert f_dn[0.0] == pytest.approx(-qd, rel=0.01)
    assert f_up[0.0] == pytest.approx(qd, rel=0.01)
    E = _energy(h[80:])
    dy = qd / ((1 - a1) * k0)
    assert E == pytest.approx(4.0 * qd * (Dm - dy), rel=0.03)


def test_friction_spring_coulomb_loop():
    mu, N, k0, Dm = 0.1, 1000.0, 1.0e5, 0.05
    h = _drive_link("friction_spring", {"mu": mu, "k_init": k0},
                    [Dm, -Dm, Dm], n=40, N=N)
    assert h[40][1] == pytest.approx(mu * N, rel=1e-3)
    assert h[80][1] == pytest.approx(-mu * N, rel=1e-3)
    E = _energy(h[40:])
    assert E == pytest.approx(4 * mu * N * (Dm - mu * N / k0), rel=0.02)


# =========================================================================
# 4. links in the engine + validation
# =========================================================================
M_BLK, B_BLK, H_ISO = 40.0, 4.0, 0.3


def _block(ltype, params, gravity=0.0):
    mdl = BuildingModel(name="blk")
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
    for x, y in pts:
        mdl.add_link((x, y, 0.0), (x, y, H_ISO), link_type=ltype,
                      params=dict(params))
        mdl.nodal_masses.append(NodalMass((x, y, H_ISO),
                                          mx=M_BLK / 4, my=M_BLK / 4))
    return mdl, pts


@pytest.mark.parametrize("ltype, params, k0", [
    ("multilinear_kinematic", {"points": BB}, 1.0e4),
    ("multilinear_takeda", {"points": BB}, 1.0e4),
    ("multilinear_pivot", {"points": BB, "pinch_y": 0.3}, 1.0e4),
])
def test_material_links_in_engine(ltype, params, k0):
    mdl, pts = _block(ltype, params)
    pat = mdl.pattern("EL", "other")
    F = 4 * 50.0                                   # half yield per link
    for x, y in pts:
        pat.nodal_loads.append(NodalLoad((x, y, H_ISO), fx=F / 4))
    mdl.add_case("EL", {"EL": 1.0})
    eng = OpenSeesEngine(mdl)
    assert eng._nonlinear_static_links_present()
    T = 2 * math.pi * math.sqrt(M_BLK / (4 * k0))
    assert eng.run_modal().periods[0] == pytest.approx(T, rel=1e-3)
    c = _node(eng._asm.node_coords, (0, 0, H_ISO))
    u = eng.run_static("EL").node_disp[c][0]
    assert u == pytest.approx(F / (4 * k0), rel=1e-4)


@pytest.mark.parametrize("ltype, params", [
    ("friction_spring", {"mu": 0.1, "k_init": 1.0e5}),
    ("rubber_isolator_bouc_wen", {"k_init": 1.0e4, "qd": 50.0,
                                  "alpha1": 0.1}),
])
def test_bearing_links_in_engine(ltype, params):
    mdl, pts = _block(ltype, params)
    pg = mdl.pattern("G", "dead")
    for x, y in pts:
        pg.nodal_loads.append(NodalLoad((x, y, H_ISO), fz=-500.0,
                                        fx=1.0))
    mdl.add_case("G", {"G": 1.0})
    eng = OpenSeesEngine(mdl)
    r = eng.run_static("G")
    c = _node(eng._asm.node_coords, (0, 0, H_ISO))
    ux = r.node_disp[c][0]
    assert 0.0 < ux < 1e-3                         # stick / elastic range
    assert r.base["FZ"] == pytest.approx(2000.0, rel=1e-6)


@pytest.mark.parametrize("ltype, params, msg", [
    ("multilinear_takeda", {"points": [[0.01, 1], [0.02, 2], [0.03, 3],
                                       [0.04, 4]]}, "2..3"),
    ("multilinear_takeda", {"points": [[0.01, 1], [0.02, 2]],
                            "points_neg": [[0.01, -1], [-0.02, -2]]},
     "away from 0"),
    ("multilinear_pivot", {"points": BB, "pinch_x": 1.5}, "pinch_x"),
    ("multilinear_kinematic", {"points": [[0.02, 1], [0.01, 2]]},
     "away from 0"),
    ("friction_spring", {"mu": 0.0}, "mu must"),
    ("friction_spring", {"mu": 0.1, "bogus": 1.0}, "unknown param"),
    ("rubber_isolator_bouc_wen", {"k_init": 1e4}, "missing required"),
    ("rubber_isolator_bouc_wen", {"k_init": 1e4, "qd": 10, "alpha1": 1.0},
     "alpha1"),
])
def test_link_param_validation(ltype, params, msg):
    mdl = BuildingModel(name="v")
    with pytest.raises(ValueError, match=msg):
        mdl.add_link((0, 0, 0), (0, 0, 0.3), link_type=ltype, params=params)


def test_hysteresis_link_axis_must_be_vertical():
    mdl = BuildingModel(name="v")
    with pytest.raises(ValueError, match="vertical"):
        mdl.add_link((0, 0, 0), (1, 0, 0.3), link_type="multilinear_takeda",
                     params={"points": BB})


def test_link_round_trip():
    mdl = BuildingModel(name="rt")
    for i, (lt, prm) in enumerate([
            ("multilinear_kinematic", {"points": BB, "kv": 1e6}),
            ("multilinear_takeda", {"points": BB,
                                    "points_neg": [[-0.02, -90],
                                                   [-0.04, -180]]}),
            ("friction_spring", {"mu": 0.05}),
            ("rubber_isolator_bouc_wen", {"k_init": 1e4, "qd": 30,
                                          "gamma": 0.4})]):
        mdl.add_link((i, 0, 0), (i, 0, 0.3), link_type=lt, params=prm)
    d = json.loads(json.dumps(mdl.to_dict()))
    m2 = BuildingModel.from_dict(d)
    assert [lk.to_dict() for lk in m2.links] == \
        [lk.to_dict() for lk in mdl.links]
    assert isinstance(LinkMember.coerce_params(
        copy.deepcopy(d["links"][1]["params"]))["points_neg"][0], list)


def test_th_with_nonlinear_named_spring_runs_newton():
    """A TH case with a multilinear named spring is routed to Newton
    (device-style) and stays finite."""
    mdl = _column(restr=(0, 1, 1, 1, 1, 1))
    mdl.num_modes = 2
    L = mdl.stories[-1].elevation
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=5.0, my=5.0))
    mdl.add_spring_property("ML", {"kind": "multilinear",
                                   "curves": {"U1": CURVE}})
    mdl.add_spring_support((0, 0, 0), property="ML")
    dt = 0.01
    accel = [3.0 * math.sin(2 * math.pi * k * dt / 0.4) for k in range(80)]
    mdl.add_th_case("TH", "X", accel, dt, damping=0.05)
    res = OpenSeesEngine(mdl).run_time_history("TH")
    ux = res.story_ux["Story1"]
    assert all(math.isfinite(v) for v in ux)
    assert max(abs(v) for v in ux) > 0.0
