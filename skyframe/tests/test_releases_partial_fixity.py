"""Frame end releases (P V2 V3 T M2 M3), partial fixity and joint local
axes (CONTRACT "Frame end releases, partial fixity and joint local axes").

Every number is pinned to a hand / closed-form value."""

import json
import math
import warnings

import pytest

from skyframe.core import releases as rel
from skyframe.core.checks import check_model
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 MemberLoad, NodalLoad, PointSupport,
                                 SpringSupport, ThermalLoad)
from skyframe.engine.opensees_engine import OpenSeesEngine

E, NU = 200e6, 0.3
A_, I_, J_ = 0.01, 1e-4, 2e-4
FIX = (1, 1, 1, 1, 1, 1)
W = 10.0
# spring-path accuracy: the stiff ties (releases_eng.RELEASE_TIE_FACTOR =
# 1e6 x the member's own DOF stiffness) soften a released end by ~1e-6
TOL = 2e-5


def _base(name="rel"):
    m = BuildingModel(name)
    m.add_material(Material("S", E, NU, 0.0, material_type="steel"))
    m.add_section(FrameSection("B", "S", A_, I_, I_, J_))
    m.set_stories([3.0])
    m.rigid_diaphragms = False
    return m


def _run(m, case="W"):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        eng = OpenSeesEngine(m)
        res = eng.run()
    return eng, res.cases[case]


def _R(eng, cr, p):
    return cr.reactions[eng._find_node(eng._asm, p)]


def _fixed_beam(L=6.0, er=None, releases="", udl=W, sec="B"):
    m = _base()
    m.add_member("beam", sec, (0, 0, 0), (L, 0, 0), uid="B1",
                 releases=releases)
    m.members[0].end_releases = er
    m.supports.append(PointSupport((0, 0, 0), FIX))
    m.supports.append(PointSupport((L, 0, 0), FIX))
    p = m.pattern("W", "dead")
    p.member_loads.append(MemberLoad("B1", "udl", udl, 0, 0, 1, "gravity"))
    m.add_case("W", {"W": 1.0})
    m.validate()
    return m


# --------------------------------------------------------------------------
# model side
# --------------------------------------------------------------------------
def test_mi_mj_are_the_canonical_subset():
    eff = {"i": {"M2": 0.0, "M3": 0.0}, "j": {"M3": 0.0, "M2": 0.0}}
    assert rel.canonical(eff) == ("Mi,Mj", None)
    toks, er = rel.canonical({"i": {"M3": 0.0}, "j": {"M2": 0.0,
                                                     "M3": 0.0}})
    assert (toks, er) == ("Mj", {"i": {"M3": 0.0}})
    toks, er = rel.canonical({"i": {"M2": 0.0, "M3": 500.0}})
    assert (toks, er) == ("", {"i": {"M2": 0.0, "M3": 500.0}})
    m = _base()
    mm = m.add_member("beam", "B", (0, 0, 0), (5, 0, 0), uid="X",
                      releases="Mi")
    assert rel.effective(mm) == {"i": {"M2": 0.0, "M3": 0.0}, "j": {}}
    # a legacy member serialises without the new key (byte-identical)
    assert "end_releases" not in mm.to_dict()
    assert "joint_local_axes" not in m.to_dict()


def test_roundtrip_and_validation():
    m = _fixed_beam(er={"i": {"T": 0, "M3": 1500.0}, "j": ["V2"]})
    m.joint_local_axes = [{"point": [6, 0, 0], "angles": [10, 30, 0]}]
    d = m.to_dict()
    assert d["members"][0]["end_releases"] == {
        "i": {"T": 0.0, "M3": 1500.0}, "j": {"V2": 0.0}}
    m2 = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert m2.to_dict() == d
    for bad, msg in (({"k": {}}, "unknown end"),
                     ({"i": {"M4": 0}}, "unknown DOF"),
                     ({"i": {"P": -1.0}}, ">= 0")):
        with pytest.raises(ValueError, match=msg):
            _fixed_beam(er=bad)
    m = _fixed_beam()
    m.joint_local_axes = [{"point": [0, 0], "angles": [0, 0, 0]}]
    with pytest.raises(ValueError, match="3 finite"):
        m.validate()


def test_stability_rules_match_etabs():
    def mk(er):
        m = _base()
        mm = m.add_member("beam", "B", (0, 0, 0), (5, 0, 0), uid="X")
        mm.end_releases = er
        return mm
    assert rel.unstable(mk({"i": {"T": 0}, "j": {"T": 0}}))
    assert rel.unstable(mk({"i": {"P": 0}, "j": {"P": 0}}))
    assert rel.unstable(mk({"i": {"M3": 0, "V2": 0}, "j": {"M3": 0}}))
    assert rel.unstable(mk({"i": {"M2": 0}, "j": {"M2": 0, "V3": 0}}))
    assert not rel.unstable(mk({"i": {"M3": 0, "M2": 0},
                                "j": {"M3": 0, "M2": 0}}))   # pin-pin
    assert not rel.unstable(mk({"i": {"T": 0}, "j": {"T": 10.0}}))  # spring
    assert not rel.unstable(mk({"i": {"V2": 0}, "j": {"M3": 0}}))
    # Check Model reports it as an ETABS-style warning
    m = _fixed_beam(er={"i": {"T": 0.0}, "j": {"T": 0.0}})
    issues = check_model(m)["issues"]
    assert any(i["code"] == "RELEASE_UNSTABLE" and i["severity"] == "warning"
               for i in issues)
    # ... and the engine warns but still solves (residual stiffness)
    with pytest.warns(UserWarning, match="unstable end releases"):
        OpenSeesEngine(m).run()


# --------------------------------------------------------------------------
# closed-form validations
# --------------------------------------------------------------------------
def test_propped_cantilever_with_shear_release_at_midspan():
    """Fixed at A, roller at C, V2 released at B (mid-span), UDL w: the
    release removes the one redundant -> R_A = R_C = w*a, M_A = 0,
    M_B = w a^2 / 2 (sagging), zero shear just left of B."""
    a = 4.0
    m = _base()
    m.add_member("beam", "B", (0, 0, 0), (a, 0, 0), uid="AB")
    m.add_member("beam", "B", (a, 0, 0), (2 * a, 0, 0), uid="BC")
    m.members[0].end_releases = {"j": {"V2": 0.0}}
    m.supports.append(PointSupport((0, 0, 0), FIX))
    m.supports.append(PointSupport((2 * a, 0, 0), (0, 1, 1, 0, 0, 0)))
    p = m.pattern("W", "dead")
    for u in ("AB", "BC"):
        p.member_loads.append(MemberLoad(u, "udl", W, 0, 0, 1, "gravity"))
    m.add_case("W", {"W": 1.0})
    m.validate()
    eng, cr = _run(m)
    assert _R(eng, cr, (0, 0, 0))[2] == pytest.approx(W * a, rel=1e-7)
    assert _R(eng, cr, (2 * a, 0, 0))[2] == pytest.approx(W * a, rel=1e-7)
    f = cr.member_forces["AB"]
    assert abs(f[5]) < 1e-6 * W * a * a                     # M_A = 0
    assert abs(f[7]) < 1e-6 * W * a                        # V just left of B
    assert f[11] == pytest.approx(W * a * a / 2, rel=1e-7)  # M_B
    st = cr.member_stations["AB"]
    assert st["V2"][-1] == pytest.approx(0.0, abs=1e-6)
    # closed-form cantilever tip deflection: v(a) = (40a^3/6 - 5a^4/12)/EI
    dy = cr.member_deflections["AB"]["dy"][-1]
    assert dy == pytest.approx((W * a * a ** 3 / 6 - W / 2 * a ** 4 / 12)
                               / (E * I_), rel=1e-6)


@pytest.mark.parametrize("k_fac", [0.0, 0.25, 1.0, 10.0])
def test_partial_fixity_end_moment_closed_form(k_fac):
    """Fixed-fixed beam, rotational spring k (M3) at end i: slope-
    deflection gives M_i = (wL^2/12) * k / (k + 4EI/L) and
    M_j = wL^2/12 + (2EI/L) * theta_i."""
    L = 6.0
    k = k_fac * 4 * E * I_ / L
    m = _fixed_beam(L, er={"i": {"M3": k}})
    _, cr = _run(m)
    f = cr.member_forces["B1"]
    fem = W * L * L / 12
    th = fem / (k + 4 * E * I_ / L)
    # spring-path ties soften the end by ~1e-6 of its own stiffness
    assert f[5] == pytest.approx(fem * k / (k + 4 * E * I_ / L),
                                 abs=TOL * fem)
    assert -f[11] == pytest.approx(fem + 2 * E * I_ / L * th, rel=TOL)


def test_partial_fixity_on_timoshenko_member():
    """Shear-flexible member: k_ii = (4+phi)EI/((1+phi)L), FEM = wL^2/12
    unchanged, so M_i = FEM * k / (k + k_ii) -- the release spring keeps
    the ElasticTimoshenkoBeam (no Euler fallback)."""
    L, Av = 3.0, 0.002
    m = _fixed_beam(L)
    sec = m.sections["B"]
    sec.As2 = Av
    sec.As3 = Av
    k = 5000.0
    m.members[0].end_releases = {"i": {"M3": k}}
    eng, cr = _run(m)
    assert "B1" in eng._asm.timo
    phi = 12 * E * I_ / (E / (2 * (1 + NU)) * Av * L * L)
    kii = (4 + phi) * E * I_ / ((1 + phi) * L)
    fem = W * L * L / 12
    assert cr.member_forces["B1"][5] == pytest.approx(fem * k / (k + kii),
                                                      rel=TOL)


@pytest.mark.parametrize("k", [0.0, 1.0e5, 1.0e6])
def test_axial_release_under_thermal_load(k):
    """Fixed-fixed bar heated dT: N = EA alpha dT * k/(k + EA/L); a full
    P release (k = 0) gives zero axial force."""
    L, dT = 6.0, 30.0
    m = _fixed_beam(L, er={"i": {"P": k}}, udl=0.0)
    m.patterns["W"].thermal_loads.append(ThermalLoad("B1", dT))
    _, cr = _run(m)
    N0 = E * A_ * m.thermal_alpha * dT
    assert cr.member_forces["B1"][0] == pytest.approx(
        N0 * k / (k + E * A_ / L), abs=TOL * N0)


def test_torsion_release():
    """Collinear AB (4 m) + BC (6 m) fixed at A and C, torque T0 at B:
    continuous -> split by GJ/L (T_AB = 0.6 T0); T released at the B end
    of BC -> AB carries all of it."""
    def run(er):
        m = _base()
        m.add_member("beam", "B", (0, 0, 0), (4, 0, 0), uid="AB")
        m.add_member("beam", "B", (4, 0, 0), (10, 0, 0), uid="BC")
        m.members[1].end_releases = er
        m.supports.append(PointSupport((0, 0, 0), FIX))
        m.supports.append(PointSupport((10, 0, 0), FIX))
        p = m.pattern("W", "dead")
        p.nodal_loads.append(NodalLoad((4, 0, 0), mx=12.0))
        m.add_case("W", {"W": 1.0})
        m.validate()
        return _run(m)[1].member_forces
    f0 = run(None)
    assert -f0["AB"][3] == pytest.approx(12.0 * 0.6, rel=1e-9)
    f1 = run({"i": {"T": 0.0}})
    assert -f1["AB"][3] == pytest.approx(12.0, rel=1e-7)
    assert abs(f1["BC"][3]) < 1e-6


def test_native_single_axis_release_and_mixed_tokens():
    """M3 only at i: strong-axis propped cantilever (M_j = wL^2/8) while
    a weak-axis (global Y) load still sees the fixed-fixed wL^2/12."""
    L = 6.0
    m = _fixed_beam(L, er={"i": {"M3": 0.0}})
    m.patterns["W"].member_loads.append(
        MemberLoad("B1", "udl", W, 0, 0, 1, "global_y"))
    eng, cr = _run(m)
    f = cr.member_forces["B1"]
    assert abs(f[5]) < 1e-9
    assert -f[11] == pytest.approx(W * L * L / 8, rel=1e-9)
    assert abs(f[4]) == pytest.approx(W * L * L / 12, rel=1e-9)
    assert not eng._asm.release_dups            # exact element release


def test_end_releases_pin_equals_legacy_token_exactly():
    a = _run(_fixed_beam(releases="Mi,Mj"))[1]
    b = _run(_fixed_beam(er={"i": {"M2": 0, "M3": 0},
                             "j": {"M3": 0, "M2": 0}}))[1]
    assert a.member_forces == b.member_forces
    assert a.reactions == b.reactions


# --------------------------------------------------------------------------
# robustness across element paths: spring path == native pin
# --------------------------------------------------------------------------
PIN_SPRING = {"M2": 0.0, "M3": 0.0}     # + FORCE_SPRING_PATH (fixture)


@pytest.fixture
def spring_path(monkeypatch):
    """Build every released end on the zeroLength spring path."""
    from skyframe.engine import releases_eng

    def on(flag=True):
        monkeypatch.setattr(releases_eng, "FORCE_SPRING_PATH", flag)
    return on


def _close(fa, fb, scale, tol=TOL):
    assert len(fa) == len(fb)
    for x, y in zip(fa, fb):
        assert x == pytest.approx(y, abs=tol * scale)


def test_spring_path_matches_native_on_automesh(spring_path):
    def run(er, toks):
        m = _fixed_beam(8.0, er=er, releases=toks)
        m.add_member("beam", "B", (4, 0, 0), (4, 5, 0), uid="S")
        m.supports.append(PointSupport((4, 5, 0), FIX))
        m.members[0].auto_mesh = {"at_intermediate_joints": True}
        m.patterns["W"].member_loads.append(
            MemberLoad("B1", "point", 20.0, 0, 0.3, 1, "gravity"))
        m.validate()
        return _run(m)[1]
    a = run(None, "Mi,Mj")
    spring_path()
    b = run({"i": PIN_SPRING, "j": PIN_SPRING}, "")
    _close(a.member_forces["B1"], b.member_forces["B1"], 100.0)
    _close(a.member_stations["B1"]["M3"], b.member_stations["B1"]["M3"],
           100.0)
    _close(a.member_deflections["B1"]["dy"], b.member_deflections["B1"]["dy"],
           1e-3)


def test_spring_path_matches_native_on_curved_arch(spring_path):
    R = 10.0

    def run(er, toks):
        m = _base("arch")
        mm = m.add_member("beam", "B", (-R, 0, 0), (R, 0, 0), uid="ARC",
                          releases=toks)
        mm.end_releases = er
        mm.curve = {"type": "arc", "via": [0, 0, R], "segments": 12}
        m.supports.append(PointSupport((-R, 0, 0), FIX))
        m.supports.append(PointSupport((R, 0, 0), FIX))
        p = m.pattern("W", "dead")
        p.member_loads.append(MemberLoad("ARC", "udl", 5.0, 0, 0, 1,
                                         "gravity", projected=True))
        m.add_case("W", {"W": 1.0})
        m.validate()
        eng, cr = _run(m)
        return _R(eng, cr, (-R, 0, 0))
    a = run(None, "Mi,Mj")
    spring_path()
    b = run({"i": PIN_SPRING, "j": PIN_SPRING}, "")
    _close(a, b, 100.0)
    # two-hinged parabolic-load arch thrust is finite, horizontal
    assert abs(b[0]) > 1.0


def test_spring_path_matches_native_on_nonprismatic(spring_path):
    from skyframe.core import nonprismatic as npx

    def run(er, toks):
        m = _fixed_beam(6.0)
        m.add_section(FrameSection("D", "S", 0.02, 4e-4, 4e-4, 4e-4))
        npx.add_nonprismatic_section(m, "NP", [{"start_section": "D",
                                                "end_section": "B",
                                                "EI33": "parabolic"}])
        m.members[0].section = "NP"
        m.members[0].releases = toks
        m.members[0].end_releases = er
        m.validate()
        return _run(m)[1]
    a = run(None, "Mi")
    spring_path()
    b = run({"i": PIN_SPRING}, "")
    _close(a.member_forces["B1"], b.member_forces["B1"], 100.0)


def test_spring_path_matches_native_under_pdelta(spring_path):
    def run(er, toks):
        m = _base()
        m.add_member("column", "B", (0, 0, 0), (0, 0, 4), uid="C",
                     releases=toks)
        m.members[0].end_releases = er
        m.add_member("column", "B", (3, 0, 0), (3, 0, 4), uid="C2")
        m.add_member("beam", "B", (0, 0, 4), (3, 0, 4), uid="BM")
        m.supports.append(PointSupport((0, 0, 0), FIX))
        m.supports.append(PointSupport((3, 0, 0), FIX))
        g = m.pattern("G", "dead")
        g.nodal_loads.append(NodalLoad((0, 0, 4), fz=-500.0))
        g.nodal_loads.append(NodalLoad((3, 0, 4), fz=-500.0))
        h = m.pattern("H", "other")
        h.nodal_loads.append(NodalLoad((0, 0, 4), fx=10.0))
        m.add_case("W", {"H": 1.0}, pdelta=True, pdelta_gravity={"G": 1.0})
        m.validate()
        return _run(m)[1]
    a = run(None, "Mi")
    spring_path()
    b = run({"i": PIN_SPRING}, "")
    _close(a.member_forces["C2"], b.member_forces["C2"], 50.0)


# --------------------------------------------------------------------------
# joint local axes
# --------------------------------------------------------------------------
def test_skewed_roller_reaction_normal_to_incline():
    """Simply supported beam, roller at B on a 30-degree incline (joint
    local axes b = 30 deg, restraint U3 local): R_B is normal to the
    incline, R_B = (P/2)(tan30, 0, 1); A takes -P/2 tan30 horizontally."""
    P, L = 10.0, 6.0
    m = _base()
    m.add_member("beam", "B", (0, 0, 0), (L, 0, 0), uid="B1")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 0, 0)))
    m.supports.append(PointSupport((L, 0, 0), (0, 1, 1, 0, 0, 0)))
    m.joint_local_axes = [{"point": [L, 0, 0], "angles": [0, 30, 0]}]
    p = m.pattern("W", "dead")
    p.member_loads.append(MemberLoad("B1", "point", P, 0, 0.5, 1, "gravity"))
    m.add_case("W", {"W": 1.0})
    m.validate()
    eng, cr = _run(m)
    t30 = math.tan(math.radians(30))
    rb = _R(eng, cr, (L, 0, 0))
    ra = _R(eng, cr, (0, 0, 0))
    assert rb[0] == pytest.approx(P / 2 * t30, rel=1e-6)
    assert rb[2] == pytest.approx(P / 2, rel=1e-6)
    assert ra[0] == pytest.approx(-P / 2 * t30, rel=1e-6)
    # normal to the incline: no component along local 1
    loc = rel.global_to_joint_local(m, (L, 0, 0), rb)
    assert abs(loc[0]) < 1e-6 * P
    assert loc[2] == pytest.approx(P / (2 * math.cos(math.radians(30))),
                                   rel=1e-6)
    # the incline pushes B outward: tension P/2 tan30 (end-i force is
    # compression-positive)
    assert cr.member_forces["B1"][0] == pytest.approx(-P / 2 * t30,
                                                      rel=1e-6)


def test_joint_axes_with_uniform_groups_is_exact_fix():
    """All-or-nothing restraint groups are axis-independent: identical to
    the global-axes model (no penalty spring)."""
    a = _fixed_beam()
    b = _fixed_beam()
    b.joint_local_axes = [{"point": [6, 0, 0], "angles": [20, 30, 40]}]
    ca, cb = _run(a)[1], _run(b)[1]
    assert ca.member_forces == cb.member_forces


def test_spring_in_joint_local_axes():
    """Axial spring kz' along a joint axis tilted 90 deg about Y (local 3
    = global +X): a horizontal tip load on a cantilever goes into it."""
    L, k = 4.0, 2.0e4
    m = _base()
    m.add_member("column", "B", (0, 0, 0), (0, 0, L), uid="C")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.spring_supports.append(SpringSupport((0, 0, L),
                                           [0, 0, k, 0, 0, 0]))
    m.joint_local_axes = [{"point": [0, 0, L], "angles": [0, 90, 0]}]
    p = m.pattern("W", "dead")
    p.nodal_loads.append(NodalLoad((0, 0, L), fx=10.0))
    m.add_case("W", {"W": 1.0})
    m.validate()
    eng, cr = _run(m)
    kb = 3 * E * I_ / L ** 3                    # cantilever tip stiffness
    ux = 10.0 / (k + kb)
    assert cr.node_disp[eng._find_node(eng._asm, (0, 0, L))][0] == \
        pytest.approx(ux, rel=1e-6)
    assert _R(eng, cr, (0, 0, L))[0] == pytest.approx(-k * ux, rel=1e-6)


def test_joint_load_local_to_global():
    m = _base()
    m.joint_local_axes = [{"point": [1, 2, 3], "angles": [90, 0, 0]}]
    g = rel.joint_load_to_global(m, (1, 2, 3), [10, 0, 0, 0, 0, 5])
    assert g == pytest.approx([0, 10, 0, 0, 0, 5], abs=1e-12)
    assert rel.global_to_joint_local(m, (1, 2, 3), g) == \
        pytest.approx([10, 0, 0, 0, 0, 5], abs=1e-12)
    assert rel.joint_load_to_global(m, (0, 0, 0), [1, 2, 3, 4, 5, 6]) == \
        [1, 2, 3, 4, 5, 6]


# --------------------------------------------------------------------------
# DB-edit tables
# --------------------------------------------------------------------------
def test_db_tables_roundtrip_and_canonical_form():
    from skyframe.core import modeltables as MT
    m = _fixed_beam(releases="Mi")
    d = m.to_dict()
    t = MT.table("frame_releases")
    rows = t.rows(d)
    assert rows[0]["m2i"] and rows[0]["m3i"] and not rows[0]["ti"]
    rows[0]["ti"] = True
    rows[0]["m3j"] = True
    rows[0]["k_m3j"] = 1234.0
    errs = []
    t.apply(d, rows, errs)
    assert not errs
    mm = d["members"][0]
    assert mm["releases"] == ""
    assert mm["end_releases"] == {"i": {"T": 0.0, "M2": 0.0, "M3": 0.0},
                                  "j": {"M3": 1234.0}}
    # clearing back to the pin restores the legacy token, no new key
    rows = t.rows(d)
    rows[0]["ti"] = False
    rows[0]["m3j"] = False
    errs = []
    t.apply(d, rows, errs)
    assert not errs and d["members"][0]["releases"] == "Mi"
    assert "end_releases" not in d["members"][0]
    BuildingModel.from_dict(d)
    ja = MT.table("joint_local_axes")
    errs = []
    ja.apply(d, [{"x": 6.0, "y": 0.0, "z": 0.0, "angle_b": 30.0}], errs)
    assert not errs
    assert d["joint_local_axes"] == [{"point": [6.0, 0.0, 0.0],
                                      "angles": [0.0, 30.0, 0.0]}]
    BuildingModel.from_dict(d)


def test_joint_reached_only_through_spring_released_ends(spring_path):
    """A - B - C, fixed at A and C, both members pinned at B on the spring
    path: B's free rotations are auto-restrained (as for legacy pins) and
    each half is a cantilever carrying P/2: delta_B = (P/2) a^3 / (3EI)."""
    a, P = 3.0, 12.0

    def run(spring):
        m = _base()
        m.add_member("beam", "B", (0, 0, 0), (a, 0, 0), uid="AB",
                     releases="" if spring else "Mj")
        m.add_member("beam", "B", (a, 0, 0), (2 * a, 0, 0), uid="BC",
                     releases="" if spring else "Mi")
        if spring:
            m.members[0].end_releases = {"j": dict(PIN_SPRING)}
            m.members[1].end_releases = {"i": dict(PIN_SPRING)}
        m.supports.append(PointSupport((0, 0, 0), FIX))
        m.supports.append(PointSupport((2 * a, 0, 0), FIX))
        p = m.pattern("W", "dead")
        p.nodal_loads.append(NodalLoad((a, 0, 0), fz=-P))
        m.add_case("W", {"W": 1.0})
        m.validate()
        eng, cr = _run(m)
        return cr.node_disp[eng._find_node(eng._asm, (a, 0, 0))][2]
    expect = -(P / 2) * a ** 3 / (3 * E * I_)
    assert run(False) == pytest.approx(expect, rel=1e-9)
    spring_path()
    assert run(True) == pytest.approx(expect, rel=TOL)


def test_modal_periods_spring_path_equal_native(spring_path):
    from skyframe.core.builder import quick_building

    def periods(spring):
        q = quick_building(bays_x=2, bays_y=1, stories=2)
        for mm in q.members:
            if mm.kind == "beam":
                if spring:
                    mm.end_releases = {"i": dict(PIN_SPRING),
                                       "j": dict(PIN_SPRING)}
                else:
                    mm.releases = "Mi,Mj"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return OpenSeesEngine(q).run_modal(3).periods
    a = periods(False)
    spring_path()
    b = periods(True)
    assert a == pytest.approx(b, rel=1e-6)


def test_spring_path_matches_native_with_rigid_offsets(spring_path):
    """The release sits at the clear-span element end (inboard of the
    rigid arm) on both paths."""
    def run():
        m = _fixed_beam(6.0, releases="Mi")
        m.members[0].rigid_i = 0.4
        m.members[0].rigid_j = 0.3
        m.validate()
        return _run(m)[1]
    a = run()
    spring_path()
    b = run()
    _close(a.member_forces["B1"], b.member_forces["B1"], 100.0)


def test_corotational_case_keeps_released_spring_members():
    """Spring-path releases ride a Corotational build (no PDelta
    fallback warning, unlike the element release codes)."""
    m = _base()
    m.add_member("column", "B", (0, 0, 0), (0, 0, 4), uid="C")
    m.members[0].end_releases = {"j": {"M3": 2.0e4, "M2": 2.0e4}}
    m.supports.append(PointSupport((0, 0, 0), FIX))
    p = m.pattern("W", "dead")
    p.nodal_loads.append(NodalLoad((0, 0, 4), fx=5.0, fz=-50.0))
    m.add_case("W", {"W": 1.0}, geometric="corotational")
    m.validate()
    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        res = OpenSeesEngine(m).run()
    assert not any("released elements are not supported" in str(w.message)
                   for w in rec)
    # free cantilever top: the base moment carries the tip load + P-delta
    f = res.cases["W"].member_forces["C"]
    assert abs(f[4]) + abs(f[5]) > 5.0 * 4.0 * 0.999


def test_divide_and_join_frames_carry_end_releases():
    """Edit > Divide Frames keeps end i on the first piece and end j on the
    last; Join Frames restores the member (refusing a release at the shared
    joint)."""
    from skyframe.core.edit import apply_edit_model
    sel = {"members": ["B1"], "shells": [], "links": [], "points": []}
    m = _fixed_beam(er={"i": {"T": 0.0, "M3": 900.0}, "j": {"V2": 0.0}})
    new, sm = apply_edit_model(m, "divide", sel, {"mode": "n", "n": 3})
    pieces = sm["divided"]["B1"]
    ms = [next(x for x in new.members if x.uid == u) for u in pieces]
    assert [x.end_releases for x in ms] == [
        {"i": {"T": 0.0, "M3": 900.0}}, None, {"j": {"V2": 0.0}}]
    back, sj = apply_edit_model(new, "join", dict(sel, members=pieces), {})
    assert len(sj["joined"]) == 2
    assert back.members[0].end_releases == m.members[0].end_releases
    ms[0].end_releases = {"i": {"T": 0.0}, "j": {"P": 0.0}}
    _, sj = apply_edit_model(new, "join", dict(sel, members=pieces[:2]), {})
    assert not sj["joined"]
    assert "end release at the shared joint" in {
        s["reason"] for s in sj["skipped"]}
