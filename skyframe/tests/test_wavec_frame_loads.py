"""Wave C: distributed frame MOMENTS and local-1 / local-3 distributed
forces (CONTRACT "Distributed frame moments, local 1 / 3 frame loads").

Every expected number is a closed-form hand result written in the test.
Beam along +X: local 1 = +X, local 2 = +Z, local 3 = -Y.  Units kN, m.
"""

import json
import math
import warnings

import pytest

from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 MemberLoad, PointSupport)
from skyframe.engine.opensees_engine import OpenSeesEngine, _consistent_load

E = 2.0e8
NU = 0.3
G = E / (2 * (1 + NU))
I33 = 2.0e-4
I22 = 5.0e-5
A = 1.0e-2
J = 1.0e-4
FIX = (1, 1, 1, 1, 1, 1)


def _model(As2=None):
    mdl = BuildingModel(name="wc")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("STL", E=E, nu=NU, unit_weight=0.0))
    sec = FrameSection("B", "STL", A=A, I33=I33, I22=I22, J=J)
    if As2 is not None:
        sec.As2 = As2
    mdl.add_section(sec)
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


def _cantilever(L, auto_mesh=None):
    mdl = _model()
    m = mdl.add_member("beam", "B", (0, 0, 0), (L, 0, 0), uid="M1")
    if auto_mesh:
        m.auto_mesh = {"min_segments": auto_mesh}
    mdl.supports.append(PointSupport((0.0, 0.0, 0.0), FIX))
    return mdl


def _dm(w, direction, w2=None, a=0.0, b=1.0):
    return MemberLoad("M1", kind="moment", w=w,
                      w2=w if w2 is None else w2, a=a, b=b,
                      direction=direction,
                      moment_dist="uniform" if w2 is None else "trapezoid")


# --------------------------------------------------------------------------- #
# distributed torque
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mesh", [None, 4])
@pytest.mark.parametrize("direction", ["local_1", "local_x", "global_x"])
def test_uniform_torque_cantilever(mesh, direction):
    """T(x) = t (L - x); tip twist t L^2 / (2 G J)."""
    L, t = 4.0, 3.0
    mdl = _cantilever(L, mesh)
    mdl.pattern("P").member_loads.append(_dm(t, direction))
    mdl.add_case("C", {"P": 1.0})
    eng, res = _solve(mdl)
    rx = res.node_disp[_tag(eng, (L, 0, 0))][3]
    assert rx == pytest.approx(t * L ** 2 / (2 * G * J), rel=1e-9)
    st = res.member_stations["M1"]
    for x, T in zip(st["x"], st["T"]):
        assert T == pytest.approx(t * (L - x), abs=1e-9)
    assert res.base["MX"] == pytest.approx(-t * L, rel=1e-9)
    # nothing else moves
    for k in ("N", "V2", "V3", "M2", "M3"):
        assert max(abs(v) for v in st[k]) < 1e-8


def test_partial_trapezoid_torque_resultant():
    """t from 2 (a = 0.25) to 6 (b = 0.75): reaction = (2 + 6)/2 * L/2;
    T at x = 0 equals the resultant, zero beyond b."""
    L = 4.0
    mdl = _cantilever(L)
    mdl.pattern("P").member_loads.append(
        _dm(2.0, "local_1", w2=6.0, a=0.25, b=0.75))
    mdl.add_case("C", {"P": 1.0})
    eng, res = _solve(mdl)
    R = 0.5 * (2.0 + 6.0) * (0.5 * L)
    st = res.member_stations["M1"]
    assert st["T"][0] == pytest.approx(R, rel=1e-9)
    assert st["T"][-1] == pytest.approx(0.0, abs=1e-9)
    # twist at the tip = int_0^L T(x)/GJ; T(x) piecewise: for x in [a,b]
    # T = int_x^b t(s) ds.  Closed form with t(s) = 2 + 4 (s - 1)/2 on
    # [1, 3]: int_1^3 (s - 1) t(s) ds + 1 * R  (x < a contributes R*a)
    inner = sum(  # exact: int_1^3 (s-1)(2 + 2(s-1)) ds = 4 + 16/3
        [4.0, 16.0 / 3.0])
    twist = (R * 1.0 + inner) / (G * J)
    assert res.node_disp[_tag(eng, (L, 0, 0))][3] == pytest.approx(
        twist, rel=1e-9)


# --------------------------------------------------------------------------- #
# distributed bending moments
# --------------------------------------------------------------------------- #
def test_uniform_moment_local3_fixed_fixed():
    """Fixed-fixed beam, uniform couple m about local 3: shear V = m along
    the whole span, end moments 0 (M3 == 0 everywhere), reactions +/- m."""
    L, m = 6.0, 5.0
    mdl = _model()
    mdl.add_member("beam", "B", (0, 0, 0), (L, 0, 0), uid="M1")
    mdl.supports.append(PointSupport((0.0, 0.0, 0.0), FIX))
    mdl.supports.append(PointSupport((L, 0.0, 0.0), FIX))
    mdl.pattern("P").member_loads.append(_dm(m, "local_3"))
    mdl.add_case("C", {"P": 1.0})
    eng, res = _solve(mdl)
    st = res.member_stations["M1"]
    for v2, m3 in zip(st["V2"], st["M3"]):
        assert abs(v2) == pytest.approx(m, rel=1e-9)
        assert m3 == pytest.approx(0.0, abs=1e-9)
    ra = res.reactions[_tag(eng, (0, 0, 0))]
    rb = res.reactions[_tag(eng, (L, 0, 0))]
    assert abs(ra[2]) == pytest.approx(m, rel=1e-9)
    assert ra[2] == pytest.approx(-rb[2], rel=1e-9)
    assert abs(ra[4]) == pytest.approx(0.0, abs=1e-9)
    assert abs(rb[4]) == pytest.approx(0.0, abs=1e-9)
    # the consistent-load kernel itself: (Fy_i, Mz_i, Fy_j, Mz_j) = (-m, 0,
    # +m, 0) for a local-z couple m (records are member-local)
    f = _consistent_load([("mtrap", (0, 0, m), (0, 0, m), 0.0, L)], L)
    assert f[1] == pytest.approx(-m)
    assert f[7] == pytest.approx(m)
    assert f[5] == pytest.approx(0.0, abs=1e-12)
    assert f[11] == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("mesh", [None, 3])
def test_uniform_moment_cantilever(mesh):
    """Cantilever, uniform m about local 3: V = 0, |M3(x)| = m (L - x),
    tip rotation m L^2 / (2 EI), tip deflection m L^3 / (3 EI)."""
    L, m = 4.0, 2.5
    mdl = _cantilever(L, mesh)
    mdl.pattern("P").member_loads.append(_dm(m, "local_3"))
    mdl.add_case("C", {"P": 1.0})
    eng, res = _solve(mdl)
    d = res.node_disp[_tag(eng, (L, 0, 0))]
    assert abs(d[4]) == pytest.approx(m * L ** 2 / (2 * E * I33), rel=1e-9)
    assert abs(d[2]) == pytest.approx(m * L ** 3 / (3 * E * I33), rel=1e-9)
    st = res.member_stations["M1"]
    for x, v2, m3 in zip(st["x"], st["V2"], st["M3"]):
        assert abs(v2) < 1e-8
        assert abs(m3) == pytest.approx(m * (L - x), abs=1e-9)
    dl = res.member_deflections["M1"]
    for x, dy in zip(dl["x"], dl["dy"]):
        # v(x) = m/EI (L x^2/2 - x^3/6)
        assert abs(dy) == pytest.approx(
            m / (E * I33) * (L * x * x / 2 - x ** 3 / 6), abs=1e-12)
    assert dl["dy"][-1] == pytest.approx(d[2], rel=1e-9)


def test_global_moment_equals_negative_local3():
    """For a beam along +X, local 3 = -Y: m about global Y == -m about
    local 3 (identical results)."""
    L, m = 4.0, 2.0
    out = []
    for direction, val in (("local_3", m), ("global_y", -m)):
        mdl = _cantilever(L)
        mdl.pattern("P").member_loads.append(_dm(val, direction))
        mdl.add_case("C", {"P": 1.0})
        eng, res = _solve(mdl)
        out.append(res.node_disp[_tag(eng, (L, 0, 0))])
    assert out[0] == pytest.approx(out[1], rel=1e-12)


def test_trapezoid_moment_simply_supported():
    """Pin-roller, m rising 0 -> m0: reactions m0/2,
    |M3(x)| = m0 x / 2 - m0 x^2 / (2L) (zero at both ends)."""
    L, m0 = 6.0, 4.0
    mdl = _model()
    mdl.add_member("beam", "B", (0, 0, 0), (L, 0, 0), uid="M1")
    mdl.supports.append(PointSupport((0.0, 0.0, 0.0), (1, 1, 1, 1, 0, 0)))
    mdl.supports.append(PointSupport((L, 0.0, 0.0), (0, 1, 1, 0, 0, 0)))
    mdl.pattern("P").member_loads.append(_dm(0.0, "local_3", w2=m0))
    mdl.add_case("C", {"P": 1.0})
    eng, res = _solve(mdl)
    ra = res.reactions[_tag(eng, (0, 0, 0))]
    assert abs(ra[2]) == pytest.approx(m0 / 2, rel=1e-9)
    st = res.member_stations["M1"]
    for x, m3 in zip(st["x"], st["M3"]):
        assert abs(m3) == pytest.approx(m0 * x / 2 - m0 * x * x / (2 * L),
                                        abs=1e-9)


def test_moment_with_end_release_propped():
    """Fixed at i, end j released (Mj) onto a fixed node, uniform m about
    local 3: prop reaction = m, fixed-end moment 0, M3 == 0 (condensed
    FEF through core.releases)."""
    L, m = 5.0, 3.0
    mdl = _model()
    mdl.add_member("beam", "B", (0, 0, 0), (L, 0, 0), uid="M1",
                   releases="Mj")
    mdl.supports.append(PointSupport((0.0, 0.0, 0.0), FIX))
    mdl.supports.append(PointSupport((L, 0.0, 0.0), FIX))
    mdl.pattern("P").member_loads.append(_dm(m, "local_3"))
    mdl.add_case("C", {"P": 1.0})
    eng, res = _solve(mdl)
    rb = res.reactions[_tag(eng, (L, 0, 0))]
    ra = res.reactions[_tag(eng, (0, 0, 0))]
    assert abs(rb[2]) == pytest.approx(m, rel=1e-9)
    assert abs(rb[4]) == pytest.approx(0.0, abs=1e-9)
    assert abs(ra[4]) == pytest.approx(0.0, abs=1e-9)
    assert max(abs(v) for v in res.member_stations["M1"]["M3"]) < 1e-8


def test_moment_partial_release_i_end():
    """Released Mi on a fixed node, fixed at j, couple m about local 3
    uniform on [0, L]: by symmetry with the propped case reactions are
    +/- m and M3 == 0."""
    L, m = 5.0, 3.0
    mdl = _model()
    mdl.add_member("beam", "B", (0, 0, 0), (L, 0, 0), uid="M1",
                   releases="Mi")
    mdl.supports.append(PointSupport((0.0, 0.0, 0.0), FIX))
    mdl.supports.append(PointSupport((L, 0.0, 0.0), FIX))
    mdl.pattern("P").member_loads.append(_dm(m, "local_3"))
    mdl.add_case("C", {"P": 1.0})
    eng, res = _solve(mdl)
    ra = res.reactions[_tag(eng, (0, 0, 0))]
    assert abs(ra[2]) == pytest.approx(m, rel=1e-9)
    assert max(abs(v) for v in res.member_stations["M1"]["M3"]) < 1e-8


def test_timoshenko_fixed_fixed_uniform_moment():
    """Shear-flexible fixed-fixed beam, uniform m about local 3: the simply
    supported solution rotates both ends by m/(G Av); restoring them needs
    equal end moments m L phi / (2 (1 + phi)), phi = 12 EI/(G Av L^2)."""
    L, m, Av = 3.0, 4.0, 2.0e-4
    mdl = _model(As2=Av)
    mdl.add_member("beam", "B", (0, 0, 0), (L, 0, 0), uid="M1")
    mdl.supports.append(PointSupport((0.0, 0.0, 0.0), FIX))
    mdl.supports.append(PointSupport((L, 0.0, 0.0), FIX))
    mdl.pattern("P").member_loads.append(_dm(m, "local_3"))
    mdl.add_case("C", {"P": 1.0})
    eng, res = _solve(mdl)
    assert "M1" in eng._asm.timo
    phi = 12 * E * I33 / (G * Av * L * L)
    Mend = m * L * phi / (2 * (1 + phi))
    ra = res.reactions[_tag(eng, (0, 0, 0))]
    rb = res.reactions[_tag(eng, (L, 0, 0))]
    assert abs(ra[4]) == pytest.approx(Mend, rel=1e-7)
    assert abs(rb[4]) == pytest.approx(Mend, rel=1e-7)
    # global moment balance about Y at A: m L (about -Y) = R_b L + Ma + Mb
    assert res.base["MY"] == pytest.approx(m * L, rel=1e-7)


# --------------------------------------------------------------------------- #
# local 1 / local 3 distributed forces
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mesh", [None, 4])
def test_axial_distributed_load_local1(mesh):
    """Cantilever, uniform w along local 1 (+X): N(x) = w (L - x) tension,
    tip ux = w L^2 / (2 E A)."""
    L, w = 4.0, 6.0
    mdl = _cantilever(L, mesh)
    mdl.pattern("P").member_loads.append(
        MemberLoad("M1", kind="udl", w=w, direction="local_1"))
    mdl.add_case("C", {"P": 1.0})
    eng, res = _solve(mdl)
    assert res.node_disp[_tag(eng, (L, 0, 0))][0] == pytest.approx(
        w * L ** 2 / (2 * E * A), rel=1e-9)
    st = res.member_stations["M1"]
    for x, n in zip(st["x"], st["N"]):
        assert n == pytest.approx(w * (L - x), abs=1e-9)
    assert res.base["FX"] == pytest.approx(-w * L, rel=1e-9)


def test_axial_trapezoid_local1_partial():
    """w from 2 to 4 over [0.5, 1] (exact condensed FEF path):
    N(0) = (2 + 4)/2 * L/2."""
    L = 4.0
    mdl = _cantilever(L)
    mdl.pattern("P").member_loads.append(
        MemberLoad("M1", kind="trapezoid", w=2.0, w2=4.0, a=0.5, b=1.0,
                   direction="local_1"))
    mdl.add_case("C", {"P": 1.0})
    eng, res = _solve(mdl)
    st = res.member_stations["M1"]
    assert st["N"][0] == pytest.approx(3.0 * 2.0, rel=1e-9)
    assert st["N"][-1] == pytest.approx(0.0, abs=1e-9)


def test_local3_udl_cantilever():
    """Uniform w along local 3 (-Y): tip |uy| = w L^4 / (8 E I22), base FY
    = +w L (reaction opposes the -Y load)."""
    L, w = 3.0, 2.0
    mdl = _cantilever(L)
    mdl.pattern("P").member_loads.append(
        MemberLoad("M1", kind="udl", w=w, direction="local_3"))
    mdl.add_case("C", {"P": 1.0})
    eng, res = _solve(mdl)
    uy = res.node_disp[_tag(eng, (L, 0, 0))][1]
    assert uy == pytest.approx(-w * L ** 4 / (8 * E * I22), rel=1e-9)
    assert res.base["FY"] == pytest.approx(w * L, rel=1e-9)
    from skyframe.core.tables import applied_pattern_totals
    F = applied_pattern_totals(mdl, "P")
    assert F[1] == pytest.approx(-w * L, rel=1e-12)
    F1 = None
    mdl.pattern("Q").member_loads.append(
        MemberLoad("M1", kind="udl", w=w, direction="local_1"))
    F1 = applied_pattern_totals(mdl, "Q")
    assert F1[0] == pytest.approx(w * L, rel=1e-12)


def test_local1_on_column_reaction_balance():
    """A vertical column with a downward local-1 load (-w along i -> j going
    up) — the engine's gravity balance check counts it (FZ = w H)."""
    H, w = 3.0, 5.0
    mdl = _model()
    mdl.add_member("column", "B", (0, 0, 0), (0, 0, H), uid="M1")
    mdl.supports.append(PointSupport((0.0, 0.0, 0.0), FIX))
    mdl.pattern("P").member_loads.append(
        MemberLoad("M1", kind="udl", w=-w, direction="local_1"))
    mdl.add_case("C", {"P": 1.0})
    eng, res = _solve(mdl)
    assert res.base["FZ"] == pytest.approx(w * H, rel=1e-9)
    assert eng._applied_gravity_fz(eng._asm, {"P": 1.0}) == pytest.approx(
        w * H, rel=1e-12)


# --------------------------------------------------------------------------- #
# model / JSON / tables
# --------------------------------------------------------------------------- #
def test_json_round_trip_and_legacy_shape():
    mdl = _cantilever(4.0)
    mdl.pattern("P").member_loads.append(
        MemberLoad("M1", kind="moment", w=3.0, a=0.5, direction="local_z"))
    d = mdl.to_dict()["patterns"]["P"]["member_loads"][0]
    assert "moment_dist" not in d                 # legacy shape kept
    mdl.pattern("P").member_loads.append(_dm(2.0, "local_2", w2=1.0,
                                             a=0.1, b=0.9))
    d = mdl.to_dict()
    assert d["patterns"]["P"]["member_loads"][1]["moment_dist"] == \
        "trapezoid"
    back = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert back.to_dict() == d
    assert back.patterns["P"].member_loads[1].moment_dist == "trapezoid"


@pytest.mark.parametrize("bad, msg", [
    (dict(kind="moment", moment_dist="weird"), "moment_dist"),
    (dict(kind="udl", moment_dist="uniform"), "kind 'moment'"),
    (dict(kind="moment", moment_dist="uniform", a=0.8, b=0.2), "0 <= a"),
    (dict(kind="moment", moment_dist="uniform", direction="gravity"),
     "moment load direction"),
])
def test_validation(bad, msg):
    mdl = _cantilever(4.0)
    kw = dict(member_uid="M1", w=1.0, direction="local_z")
    kw.update(bad)
    mdl.pattern("P").member_loads.append(MemberLoad(**kw))
    with pytest.raises(ValueError, match=msg):
        mdl.validate()


def test_db_tables_identity_and_edit():
    from skyframe.core import modeltables as MT
    mdl = _cantilever(4.0)
    mdl.pattern("P").member_loads.append(
        MemberLoad("M1", kind="moment", w=3.0, a=0.5, direction="local_z"))
    mdl.pattern("P").member_loads.append(_dm(2.0, "local_1"))
    d = mdl.to_dict()
    keys = [t.key for t in MT.catalogue()]
    assert "frame_moment_loads" in keys
    assert "frame_distributed_moments" in keys
    tc = MT.table("frame_moment_loads")
    td = MT.table("frame_distributed_moments")
    rc, rd = tc.rows(d), td.rows(d)
    assert len(rc) == 1 and len(rd) == 1
    assert rd[0]["moment_dist"] == "uniform"
    import copy
    d2 = copy.deepcopy(d)
    errs = []
    td.apply(d2, rd, errs)
    tc.apply(d2, rc, errs)
    assert not errs and d2 == d
    rd[0]["w"] = 5.0
    td.apply(d2, rd, errs)
    assert not errs
    ml = [x for x in d2["patterns"]["P"]["member_loads"]
          if x.get("moment_dist")][0]
    assert ml["w"] == 5.0 and ml["kind"] == "moment"


def test_curved_member_chords_keep_total():
    """A uniform distributed moment on a curved member is split per chord
    (arc-length ratio) with the same resultant couple density."""
    from skyframe.core.curved import _chord_loads
    ml = _dm(2.0, "local_3")
    pieces = _chord_loads(ml, 4, 1.0, ["c1", "c2", "c3", "c4"])
    assert len(pieces) == 4
    assert all(p.moment_dist == "trapezoid" and p.w == 2.0 and p.w2 == 2.0
               for p in pieces)
    assert [(p.a, p.b) for p in pieces] == [(0.0, 1.0)] * 4


def test_mtrap_kernel_gauss_exact():
    """Consistent load of a linear m with Hermite rotation shapes (cubic
    integrand): fixed-end moments of m = m0 x/L are m0 L/12 (opposite
    signs) and end shears m0/2."""
    L, m0 = 5.0, 3.0
    f = _consistent_load([("mtrap", (0, 0, 0), (0, 0, m0), 0.0, L)], L)
    assert f[1] == pytest.approx(-m0 / 2)
    assert f[5] == pytest.approx(-m0 * L / 12)
    assert f[11] == pytest.approx(m0 * L / 12)
    assert math.isclose(f[7], m0 / 2)
