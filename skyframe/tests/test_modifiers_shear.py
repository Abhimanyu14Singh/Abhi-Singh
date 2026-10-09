"""Frame shear deformation (Timoshenko) + full ETABS frame modifiers, and
shell membrane / bending / shear modifiers.

Closed-form references (Timoshenko beam theory, shear coefficient folded
into the shear area As):
  * cantilever, tip load P:      d = P L^3/(3EI) + P L/(G As)
  * SS beam, UDL w (midspan):    d = 5 w L^4/(384EI) + w L^2/(8 G As)
  * SS beam, midspan point P:    d = P L^3/(48EI) + P L/(4 G As)
  * cantilever station x:        v = P x^2 (3L - x)/(6EI) + P x/(G As)
Every expected number is derived here from those formulas or from an
independent split model that only uses exact nodal-load paths.
"""

import math
import warnings

import pytest

from skyframe.core.mesh import mesh_model
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 MemberLoad, MemberUDL, NodalLoad, NodalMass,
                                 PointSupport, ShellRegion, ShellSection,
                                 AreaLoad)
from skyframe.core.modifiers import (auto_shear_areas, effective_shear_areas,
                                     resolved_shear_areas)
from skyframe.engine.opensees_engine import OpenSeesEngine

E = 30_000_000.0          # kPa
NU = 0.2
G = E / (2.0 * (1.0 + NU))


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _model(nu=NU, story_h=3.0) -> BuildingModel:
    m = BuildingModel(name="t")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E=E, nu=nu, unit_weight=25.0))
    m.set_stories([story_h])
    return m


def _deep_section(m, name="D", shear=True, **kw):
    sec = FrameSection.rectangular(name, "C", 0.3, 1.0)
    sec.shear_deformation = shear
    for k, v in kw.items():
        setattr(sec, k, v)
    return m.add_section(sec)


def _disp(eng, res, pt):
    return res.node_disp[eng._find_node(eng._asm, pt)]


def _cantilever(L=2.0, P=100.0, shear=True, **kw):
    m = _model()
    _deep_section(m, shear=shear, **kw)
    m.add_member("beam", "D", (0, 0, 0), (L, 0, 0), uid="B1")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.pattern("P").nodal_loads.append(NodalLoad((L, 0, 0), fz=-P))
    m.add_case("P", {"P": 1.0})
    return m


def _ss_beam(L=4.0, shear=True, load=None, **kw):
    m = _model()
    _deep_section(m, shear=shear, **kw)
    m.add_member("beam", "D", (0, 0, 0), (L, 0, 0), uid="B1")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 0, 0)))
    m.supports.append(PointSupport((L, 0, 0), (0, 1, 1, 1, 0, 0)))
    pat = m.pattern("W", "dead")
    load(pat)
    m.add_case("W", {"W": 1.0})
    return m


I33 = 0.3 * 1.0 ** 3 / 12.0
AS_RECT = 5.0 / 6.0 * 0.3


# --------------------------------------------------------------------------- #
# 1. Timoshenko frame members
# --------------------------------------------------------------------------- #
def test_timoshenko_cantilever_tip_deflection():
    L, P = 2.0, 100.0
    m = _cantilever(L, P)
    eng = OpenSeesEngine(m)
    res = eng.run_static("P")
    eb = P * L ** 3 / (3 * E * I33)
    sh = P * L / (G * AS_RECT)
    assert sh / eb > 0.15                        # deep beam: shear matters
    assert _disp(eng, res, (L, 0, 0))[2] == pytest.approx(-(eb + sh),
                                                          rel=1e-9)
    assert "B1" in eng._asm.timo
    # deflection stations recover the Timoshenko elastic line exactly
    md = res.member_deflections["B1"]
    for x, dy in zip(md["x"], md["dy"]):
        v = P * x ** 2 * (3 * L - x) / (6 * E * I33) + P * x / (G * AS_RECT)
        assert dy == pytest.approx(-v, rel=1e-9, abs=1e-15)


def test_euler_default_cantilever_unchanged():
    L, P = 2.0, 100.0
    m = _cantilever(L, P, shear=False)
    eng = OpenSeesEngine(m)
    res = eng.run_static("P")
    assert _disp(eng, res, (L, 0, 0))[2] == pytest.approx(
        -P * L ** 3 / (3 * E * I33), rel=1e-9)
    assert eng._asm.timo == {}


def test_timoshenko_ss_udl_midspan():
    L, w = 4.0, 50.0
    m = _ss_beam(L, load=lambda p: p.member_udls.append(MemberUDL("B1", w)))
    eng = OpenSeesEngine(m)
    res = eng.run_static("W")
    exp = 5 * w * L ** 4 / (384 * E * I33) + w * L ** 2 / (8 * G * AS_RECT)
    md = res.member_deflections["B1"]
    assert md["x"][5] == pytest.approx(L / 2)
    assert md["dy"][5] == pytest.approx(-exp, rel=1e-9)
    # statics unchanged: midspan M = wL^2/8, end shear wL/2
    st = res.member_stations["B1"]
    assert abs(st["M3"][5]) == pytest.approx(w * L ** 2 / 8, rel=1e-9)
    assert abs(st["V2"][0]) == pytest.approx(w * L / 2, rel=1e-9)


def test_timoshenko_ss_udl_split_nodes_match_formula():
    """Same beam as two members: the midspan NODE carries the exact value."""
    L, w = 4.0, 50.0
    m = _model()
    _deep_section(m)
    m.add_member("beam", "D", (0, 0, 0), (L / 2, 0, 0), uid="B1")
    m.add_member("beam", "D", (L / 2, 0, 0), (L, 0, 0), uid="B2")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 0, 0)))
    m.supports.append(PointSupport((L, 0, 0), (0, 1, 1, 1, 0, 0)))
    pat = m.pattern("W", "dead")
    pat.member_udls += [MemberUDL("B1", w), MemberUDL("B2", w)]
    m.add_case("W", {"W": 1.0})
    eng = OpenSeesEngine(m)
    res = eng.run_static("W")
    exp = 5 * w * L ** 4 / (384 * E * I33) + w * L ** 2 / (8 * G * AS_RECT)
    assert _disp(eng, res, (L / 2, 0, 0))[2] == pytest.approx(-exp, rel=1e-9)


def test_timoshenko_point_load_uses_exact_fef():
    """ElasticTimoshenkoBeam rejects beamPoint: span point loads go through
    the exact Timoshenko FEF path — midspan d = PL^3/48EI + PL/(4 G As)."""
    L, P = 4.0, 80.0
    m = _ss_beam(L, load=lambda p: p.member_loads.append(
        MemberLoad("B1", "point", P, a=0.5)))
    eng = OpenSeesEngine(m)
    res = eng.run_static("W")
    exp = P * L ** 3 / (48 * E * I33) + P * L / (4 * G * AS_RECT)
    assert res.member_deflections["B1"]["dy"][5] == pytest.approx(-exp,
                                                                  rel=1e-9)


def _fixed_fixed(L, load, members):
    m = _model()
    _deep_section(m)
    for uid, a, b in members:
        m.add_member("beam", "D", (a, 0, 0), (b, 0, 0), uid=uid)
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.supports.append(PointSupport((L, 0, 0), (1, 1, 1, 1, 1, 1)))
    load(m.pattern("W", "dead"))
    m.add_case("W", {"W": 1.0})
    eng = OpenSeesEngine(m)
    return eng, eng.run_static("W")


def test_timoshenko_offcenter_point_fef_vs_split_model():
    """Fixed-fixed deep beam, P at 0.3L: one member (FEF path) vs two members
    with a NODAL load (exact stiffness path) — reactions and end moments."""
    L, P, a = 4.0, 120.0, 0.3
    e1, r1 = _fixed_fixed(L, lambda p: p.member_loads.append(
        MemberLoad("B1", "point", P, a=a)), [("B1", 0.0, L)])
    e2, r2 = _fixed_fixed(L, lambda p: p.nodal_loads.append(
        NodalLoad((a * L, 0, 0), fz=-P)), [("B1", 0.0, a * L),
                                           ("B2", a * L, L)])
    for pt in ((0, 0, 0), (L, 0, 0)):
        ra = r1.reactions[e1._find_node(e1._asm, pt)]
        rb = r2.reactions[e2._find_node(e2._asm, pt)]
        for x, y in zip(ra, rb):
            assert x == pytest.approx(y, rel=1e-8, abs=1e-8)
    # and the shear-flexible split differs from the Euler one (phi matters)
    eb = P * a * (1 - a) ** 2  # Euler FEM at i = P a b^2 / L^2 * L
    mi = abs(r1.reactions[e1._find_node(e1._asm, (0, 0, 0))][4])
    assert abs(mi - eb * L) / (eb * L) > 0.02


def test_timoshenko_partial_udl_vs_split_model():
    """Partial UDL on [0.25L, 0.75L] (FEF path, Timoshenko shape functions)
    vs a 3-member split with a full-span UDL on the middle member."""
    L, w = 4.0, 30.0
    e1, r1 = _fixed_fixed(L, lambda p: p.member_loads.append(
        MemberLoad("B1", "udl", w, a=0.25, b=0.75)), [("B1", 0.0, L)])
    e2, r2 = _fixed_fixed(L, lambda p: p.member_udls.append(
        MemberUDL("B2", w)), [("B1", 0.0, 1.0), ("B2", 1.0, 3.0),
                              ("B3", 3.0, L)])
    for pt in ((0, 0, 0), (L, 0, 0)):
        ra = r1.reactions[e1._find_node(e1._asm, pt)]
        rb = r2.reactions[e2._find_node(e2._asm, pt)]
        for x, y in zip(ra, rb):
            assert x == pytest.approx(y, rel=1e-8, abs=1e-8)
    mid1 = r1.member_deflections["B1"]["dy"][5]
    mid2 = r2.member_deflections["B2"]["dy"][5]
    assert mid1 == pytest.approx(mid2, rel=1e-8)


def test_mod_As2_scales_shear_term():
    L, P = 2.0, 100.0
    m = _cantilever(L, P, mod_As2=0.5)
    eng = OpenSeesEngine(m)
    res = eng.run_static("P")
    exp = P * L ** 3 / (3 * E * I33) + P * L / (G * 0.5 * AS_RECT)
    assert _disp(eng, res, (L, 0, 0))[2] == pytest.approx(-exp, rel=1e-9)


def test_explicit_As2_enables_and_overrides():
    L, P, As2 = 2.0, 100.0, 0.1
    m = _cantilever(L, P, shear=False, As2=As2)
    eng = OpenSeesEngine(m)
    res = eng.run_static("P")
    exp = P * L ** 3 / (3 * E * I33) + P * L / (G * As2)
    assert _disp(eng, res, (L, 0, 0))[2] == pytest.approx(-exp, rel=1e-9)
    # As3 auto-filled from the rectangle
    assert resolved_shear_areas(m.sections["D"]) == (As2, AS_RECT)


def test_auto_shear_areas_rect_and_w_shape():
    rect = FrameSection.rectangular("R", "C", 0.3, 0.6)
    assert auto_shear_areas(rect) == pytest.approx((0.15, 0.15))
    w = FrameSection.from_library("W12x26", "C")
    a2, a3 = auto_shear_areas(w)
    assert a2 == pytest.approx(12.20 * 0.230 * 0.0254 ** 2)      # d * tw
    assert a3 == pytest.approx(5 / 3 * 6.49 * 0.380 * 0.0254 ** 2)
    assert resolved_shear_areas(w) is None                     # not enabled
    w.shear_deformation = True
    assert effective_shear_areas(w) == pytest.approx((a2, a3))
    bare = FrameSection("X", "C", 0.1, 1e-3, 1e-3, 1e-3)
    bare.shear_deformation = True
    assert effective_shear_areas(bare) is None                 # unresolvable


def test_release_falls_back_to_euler_with_warning():
    L, w = 4.0, 50.0
    m = _ss_beam(L, load=lambda p: p.member_udls.append(MemberUDL("B1", w)))
    m.members[0].releases = "Mi"
    eng = OpenSeesEngine(m)
    with pytest.warns(UserWarning, match="moment releases"):
        res = eng.run_static("W")
    assert eng._asm.timo == {}
    assert res.member_deflections["B1"]["dy"][5] == pytest.approx(
        -5 * w * L ** 4 / (384 * E * I33), rel=1e-9)


def test_pdelta_build_falls_back_with_warning():
    m = _cantilever()
    m.cases["P"].pdelta = True
    eng = OpenSeesEngine(m)
    with pytest.warns(UserWarning, match="PDelta geometric transformation"):
        eng.run_static("P")


def test_timoshenko_modal_period():
    """SDOF: tip mass on a deep cantilever, k = 1/(L^3/3EI + L/(G As))."""
    L, mass = 2.0, 50.0
    m = _cantilever(L)
    m.nodal_masses.append(NodalMass((L, 0, 0), mz=mass))
    m.patterns.clear()
    m.cases.clear()
    k = 1.0 / (L ** 3 / (3 * E * I33) + L / (G * AS_RECT))
    T = OpenSeesEngine(m).run_modal(1).periods[0]
    assert T == pytest.approx(2 * math.pi * math.sqrt(mass / k), rel=1e-6)


def test_virtual_work_includes_shear_term():
    """Unit-load theorem stays exact with V*v/(G As) for Timoshenko members."""
    m = _model(story_h=3.0)
    sec = FrameSection.rectangular("D", "C", 0.5, 1.2)
    sec.shear_deformation = True
    m.add_section(sec)
    m.add_member("column", "D", (0, 0, 0), (0, 0, 3), story="Story1",
                 uid="C1")
    m.add_member("column", "D", (4, 0, 0), (4, 0, 3), story="Story1",
                 uid="C2")
    m.add_member("beam", "D", (0, 0, 3), (4, 0, 3), story="Story1",
                 uid="B1")
    for x in (0, 4):
        m.supports.append(PointSupport((x, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.pattern("L").nodal_loads.append(NodalLoad((0, 0, 3), fx=100.0))
    m.add_case("L", {"L": 1.0})
    vw = OpenSeesEngine(m).run_virtual_work("L", "X")
    assert vw["total"] == pytest.approx(vw["roof_disp"], rel=1e-9)


def test_frame_defaults_byte_identical():
    """Explicit default modifier fields == legacy dict (no new keys)."""
    def build():
        m = _ss_beam(4.0, shear=False, load=lambda p: (
            p.member_udls.append(MemberUDL("B1", 20.0)),
            p.member_loads.append(MemberLoad("B1", "point", 30.0, a=0.3))))
        m.patterns["W"].self_weight_factor = 1.0
        return m
    m1 = build()
    d = m1.to_dict()
    for key in ("As2", "As3", "shear_deformation", "mod_As2", "mod_As3",
                "mod_mass", "mod_weight"):
        d["sections"]["D"].pop(key)
    m2 = BuildingModel.from_dict(d)
    r1 = OpenSeesEngine(m1).run_static("W").to_dict()
    r2 = OpenSeesEngine(m2).run_static("W").to_dict()
    assert r1 == r2


# --------------------------------------------------------------------------- #
# 2. mass / weight modifiers
# --------------------------------------------------------------------------- #
def _portal(mode="element_self_mass", mod_mass=1.0, mod_weight=1.0):
    m = _model()
    sec = FrameSection.rectangular("col", "C", 0.3, 0.4)
    sec.mod_mass, sec.mod_weight = mod_mass, mod_weight
    m.add_section(sec)
    m.add_member("column", "col", (0, 0, 0), (0, 0, 3), story="Story1",
                 uid="C1")
    m.add_member("column", "col", (4, 0, 0), (4, 0, 3), story="Story1",
                 uid="C2")
    m.add_member("beam", "col", (0, 0, 3), (4, 0, 3), story="Story1",
                 uid="B1")
    for x in (0, 4):
        m.supports.append(PointSupport((x, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.mass_source_mode = mode
    m.pattern("DEAD", "dead").self_weight_factor = 1.0
    m.add_case("DEAD", {"DEAD": 1.0})
    m.mass_source = {"DEAD": 1.0}
    return m


def test_mod_mass_halves_mass_period_over_sqrt2():
    t1 = OpenSeesEngine(_portal()).run_modal(1).periods[0]
    eng = OpenSeesEngine(_portal(mod_mass=0.5))
    t2 = eng.run_modal(1).periods[0]
    assert t2 == pytest.approx(t1 / math.sqrt(2.0), rel=1e-9)
    total = sum(v for (t, d), v in eng._asm.mass_map.items() if d == 1)
    vol = 0.3 * 0.4 * (3.0 * 2 + 4.0)
    assert total == pytest.approx(0.5 * vol * 25.0 / 9.80665, rel=1e-12)


def test_mod_mass_warns_in_weight_mode():
    with pytest.warns(UserWarning, match="element_self_mass"):
        OpenSeesEngine(_portal(mode="weight", mod_mass=0.5)).run_modal(1)


def test_mod_weight_scales_self_weight_reaction_and_story_mass():
    r1 = OpenSeesEngine(_portal()).run_static("DEAD")
    r2 = OpenSeesEngine(_portal(mod_weight=0.5)).run_static("DEAD")
    W = 25.0 * 0.3 * 0.4 * 10.0
    assert r1.base["FZ"] == pytest.approx(W, rel=1e-9)
    assert r2.base["FZ"] == pytest.approx(0.5 * W, rel=1e-9)
    # weight-derived story mass follows the (scaled) self-weight load
    s1 = _portal(mode="weight").compute_story_masses()["Story1"]
    s2 = _portal(mode="weight", mod_weight=0.5).compute_story_masses()[
        "Story1"]
    assert s2 == pytest.approx(0.5 * s1, rel=1e-12)
    # element self-mass is NOT affected by the weight modifier
    t1 = OpenSeesEngine(_portal()).run_modal(1).periods[0]
    t2 = OpenSeesEngine(_portal(mod_weight=0.5)).run_modal(1).periods[0]
    assert t2 == pytest.approx(t1, rel=1e-12)


# --------------------------------------------------------------------------- #
# 3. shell modifiers
# --------------------------------------------------------------------------- #
LS, WS, TS = 4.0, 1.0, 0.2


def _strip(load="bend", nu=0.0, **mods):
    """Cantilever plate strip (x along the span), fixed at x = 0."""
    m = _model(nu=nu)
    ssec = ShellSection("SL", "C", TS)
    for k, v in mods.items():
        setattr(ssec, k, v)
    m.add_shell_section(ssec)
    m.shells.append(ShellRegion("S1", "slab", "shell", "SL",
                                [(0, 0, 3), (LS, 0, 3), (LS, WS, 3),
                                 (0, WS, 3)], mesh_size=0.25,
                                story="Story1"))
    pts = mesh_model(m).points
    pat = m.pattern("P")
    tip = sorted((p for p in pts if abs(p[0] - LS) < 1e-9),
                 key=lambda p: p[1])
    for p in pts:
        if abs(p[0]) < 1e-9:
            m.supports.append(PointSupport(p, (1, 1, 1, 1, 1, 1)))
    for i, p in enumerate(tip):      # consistent (tributary) edge loads
        f = (0.5 if i in (0, len(tip) - 1) else 1.0) / (len(tip) - 1)
        pat.nodal_loads.append(NodalLoad(
            p, fz=-f if load == "bend" else 0.0,
            fx=f if load == "axial" else 0.0))
    m.add_case("P", {"P": 1.0})
    return m


def _tip(m, comp):
    eng = OpenSeesEngine(m)
    res = eng.run_static("P")
    return _disp(eng, res, (LS, 0, 3))[comp]


def test_shell_defaults_byte_identical():
    m1 = _strip()
    d = m1.to_dict()
    for k in ("f11", "f22", "f12", "m11", "m22", "m12", "v13", "v23",
              "mass", "weight"):
        d["shell_sections"]["SL"].pop(k)
    m2 = BuildingModel.from_dict(d)
    assert (OpenSeesEngine(m1).run_static("P").to_dict()
            == OpenSeesEngine(m2).run_static("P").to_dict())


def test_shell_bending_modifier_uniform_exact_4x():
    """m11=m22=m12=v13=v23=0.25 (uniform path, Ep_mod): bending 4x exactly,
    membrane stiffness unchanged."""
    mods = dict(m11=0.25, m22=0.25, m12=0.25, v13=0.25, v23=0.25)
    assert _tip(_strip(**mods), 2) == pytest.approx(4 * _tip(_strip(), 2),
                                                    rel=1e-9)
    assert _tip(_strip("axial", **mods), 0) == pytest.approx(
        _tip(_strip("axial"), 0), rel=1e-12)


def test_shell_cracked_slab_bending_only():
    """ETABS cracked slab: m11=m22=m12=0.25, shear untouched (layered path)
    -> ~4x deflection on a thin strip (shear share < 1%), membrane exact."""
    mods = dict(m11=0.25, m22=0.25, m12=0.25)
    ratio = _tip(_strip(**mods), 2) / _tip(_strip(), 2)
    assert 3.95 < ratio < 4.0
    assert _tip(_strip("axial", **mods), 0) == pytest.approx(
        _tip(_strip("axial"), 0), rel=1e-9)


def test_shell_membrane_modifier_converse():
    mods = dict(f11=0.25, f22=0.25, f12=0.25)
    assert _tip(_strip("axial", **mods), 0) == pytest.approx(
        4 * _tip(_strip("axial"), 0), rel=1e-9)
    assert _tip(_strip(**mods), 2) == pytest.approx(_tip(_strip(), 2),
                                                    rel=1e-9)


def test_shell_layered_path_equals_uniform_path(monkeypatch):
    """The orthotropic skin-core LayeredShell reproduces the exact
    ElasticMembranePlateSection response (forced via monkeypatch)."""
    mods = dict(m11=0.4, m22=0.4, m12=0.4, v13=0.4, v23=0.4, f11=0.7,
                f22=0.7, f12=0.7)
    ref_b = _tip(_strip(nu=0.2, **mods), 2)
    ref_a = _tip(_strip("axial", nu=0.2, **mods), 0)
    import skyframe.engine.shell_modifiers as sm
    monkeypatch.setattr(sm, "shell_uniform_factors", lambda s: None)
    assert _tip(_strip(nu=0.2, **mods), 2) == pytest.approx(ref_b, rel=1e-9)
    assert _tip(_strip("axial", nu=0.2, **mods), 0) == pytest.approx(
        ref_a, rel=1e-9)


def test_shell_directional_membrane_f11_vs_f22():
    """nu = 0 strip pulled along local 1 (x): f11 = 0.5 doubles the
    elongation exactly; f22 = 0.5 leaves it unchanged."""
    base = _tip(_strip("axial"), 0)
    assert _tip(_strip("axial", f11=0.5), 0) == pytest.approx(2 * base,
                                                              rel=1e-9)
    assert _tip(_strip("axial", f22=0.5), 0) == pytest.approx(base,
                                                              rel=1e-9)


def test_shell_directional_bending_and_shear():
    base = _tip(_strip(), 2)
    assert _tip(_strip(m22=0.5), 2) == pytest.approx(base, rel=1e-9)
    r11 = _tip(_strip(m11=0.5), 2) / base
    assert 1.97 < r11 < 2.0                      # bending part doubles
    assert _tip(_strip(v13=0.1), 2) / base > 1.01  # x-z shear softened
    assert _tip(_strip(v23=0.1), 2) == pytest.approx(base, rel=1e-6)


def test_shell_weight_and_mass_modifiers():
    def slab(**mods):
        m = _strip(**mods)
        m.patterns["P"].nodal_loads.clear()
        m.patterns["P"].self_weight_factor = 1.0
        m.mass_source_mode = "element_self_mass"
        return m
    W = 25.0 * TS * LS * WS
    assert OpenSeesEngine(slab()).run_static("P").base["FZ"] == \
        pytest.approx(W, rel=1e-9)
    assert OpenSeesEngine(slab(weight=0.3)).run_static("P").base["FZ"] == \
        pytest.approx(0.3 * W, rel=1e-9)
    eng = OpenSeesEngine(slab(mass=0.5))
    eng.run_modal(1)
    total = sum(v for (t, d), v in eng._asm.mass_map.items() if d == 3)
    # only free corner nodes keep their share (2 of 4 corners are supports)
    eng1 = OpenSeesEngine(slab())
    eng1.run_modal(1)
    total1 = sum(v for (t, d), v in eng1._asm.mass_map.items() if d == 3)
    assert total == pytest.approx(0.5 * total1, rel=1e-12)


# --------------------------------------------------------------------------- #
# 4. round trip + validation
# --------------------------------------------------------------------------- #
def test_round_trip_all_new_fields():
    m = _model()
    sec = FrameSection.rectangular("D", "C", 0.3, 1.0)
    sec.As2, sec.As3, sec.shear_deformation = 0.2, None, True
    sec.mod_As2, sec.mod_As3, sec.mod_mass, sec.mod_weight = 0.9, 0.8, 0.7, 0
    m.add_section(sec)
    ss = ShellSection("S", "C", 0.2)
    for i, k in enumerate(("f11", "f22", "f12", "m11", "m22", "m12", "v13",
                           "v23", "mass", "weight")):
        setattr(ss, k, 0.1 * (i + 1))
    m.add_shell_section(ss)
    d = m.to_dict()
    m2 = BuildingModel.from_dict(d)
    assert m2.sections["D"] == sec
    assert m2.shell_sections["S"] == ss
    assert m2.to_dict() == d
    m2.validate()


@pytest.mark.parametrize("field,val", [
    ("mod_As2", 0.0), ("mod_As3", -1.0), ("mod_mass", -0.1),
    ("mod_weight", float("nan")), ("As2", 0.0), ("As3", -2.0),
    ("shear_deformation", "yes")])
def test_frame_validation(field, val):
    m = _model()
    sec = FrameSection.rectangular("D", "C", 0.3, 1.0)
    setattr(sec, field, val)
    with pytest.raises(ValueError, match=field):
        m.add_section(sec)


@pytest.mark.parametrize("field,val", [
    ("f11", 0.0), ("m22", -1.0), ("v13", float("inf")), ("mass", -1.0),
    ("weight", float("nan"))])
def test_shell_validation(field, val):
    m = _model()
    ss = ShellSection("S", "C", 0.2)
    setattr(ss, field, val)
    with pytest.raises(ValueError, match=field):
        m.add_shell_section(ss)


def test_buckling_warns_shear_deformation_ignored():
    from skyframe.core.buckling import assemble_elastic_stiffness
    m = _cantilever()
    *_, warn = assemble_elastic_stiffness(m)
    assert any("shear deformation" in w for w in warn)
