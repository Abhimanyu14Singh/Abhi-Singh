"""Story shear from ALL lateral load types (CONTRACT "Story shear from all
lateral load types").

Story shear at a level = the sum of the lateral forces applied at and above
that level (ETABS), equivalently the horizontal cut force just below it.
Every expected number is a closed-form hand result or the independent cut-
force check :func:`skyframe.engine.story_shear.cut_shear` (equilibrium of
the free body above a plane just below the level, from the element end
forces of the solved domain).  Units: kN, m, kPa.
"""

import math
import warnings

import pytest

from skyframe.core import openwind as ow
from skyframe.core.loads_ext import GroundDisplacement
from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection,
                                 Material, MemberLoad, NodalLoad,
                                 ShellSection, StoryForce, ThermalLoad)
from skyframe.engine import story_shear as ssx
from skyframe.engine.opensees_engine import OpenSeesEngine, _local_axes

E = 2.0e8
H = 3.0                     # story height
NS = 3                      # stories
HT = H * NS


def _model(name="ss"):
    m = BuildingModel(name=name)
    m.diaphragm = "none"
    m.add_material(Material("STL", E=E, nu=0.3, unit_weight=0.0))
    m.add_section(FrameSection("COL", "STL", A=1e-2, I33=2e-4, I22=1e-4,
                               J=1e-4))
    m.add_section(FrameSection("BR", "STL", A=5e-3, I33=5e-5, I22=5e-5,
                               J=5e-5))
    m.set_stories([H] * NS)
    return m


def _run(m, case="C"):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m.validate()
        eng = OpenSeesEngine(m)
        res = eng.run_static(case)
    return eng, res


def _shear(res, story):
    e = res.story[story]
    return e["shear_x"], e["shear_y"]


def _names(m):
    return [s.name for s in m.stories]


def _assert_cut(eng, res, m, rel=1e-7, absol=1e-7):
    """story shear == cut force just below each level (equilibrium)."""
    for s in m.stories:
        cx, cy = ssx.cut_shear(eng, eng._asm, s.elevation)
        vx, vy = _shear(res, s.name)
        assert vx == pytest.approx(cx, rel=rel, abs=absol), s.name
        assert vy == pytest.approx(cy, rel=rel, abs=absol), s.name


def _cantilever(stacked=False):
    m = _model()
    if stacked:
        for k in range(NS):
            m.add_member("column", "COL", (0, 0, k * H), (0, 0, (k + 1) * H),
                         story=m.stories[k].name, uid=f"C{k}")
    else:
        m.add_member("column", "COL", (0, 0, 0), (0, 0, HT),
                     story=m.stories[-1].name, uid="C")
    return m


# --------------------------------------------------------------------------- #
# 1-3. cantilever column with lateral member loads (exact integration)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("stacked", [False, True])
def test_cantilever_lateral_udl_exact(stacked):
    """UDL w along +X on a 9 m cantilever: shear at level e = w (HT - e)."""
    w = 2.5
    m = _cantilever(stacked)
    p = m.pattern("W", "wind")
    for mem in m.members:
        p.member_loads.append(MemberLoad(mem.uid, "udl", w,
                                         direction="global_x"))
    m.add_case("C", {"W": 1.0})
    eng, res = _run(m)
    for s in m.stories:
        vx, vy = _shear(res, s.name)
        assert vx == pytest.approx(w * (HT - s.elevation), abs=1e-9)
        assert vy == 0.0
    assert res.base["FX"] == pytest.approx(-w * HT, rel=1e-9)
    _assert_cut(eng, res, m)


def test_cantilever_partial_trapezoid_and_point_global_y():
    """Single 9 m column (no node at the 3 m / 6 m levels): trapezoid
    4 -> 10 kN/m over z in [1.8, 7.2] plus a 6 kN point at z = 4.5 and a
    5 kN point at z = 6.0 (exactly at a level: counted there)."""
    m = _cantilever()
    p = m.pattern("W", "wind")
    za, zb, wa, wb = 1.8, 7.2, 4.0, 10.0
    p.member_loads.append(MemberLoad("C", "trapezoid", wa, wb, za / HT,
                                     zb / HT, "global_y"))
    p.member_loads.append(MemberLoad("C", "point", 6.0, 0.0, 4.5 / HT, 1.0,
                                     "global_y"))
    p.member_loads.append(MemberLoad("C", "point", 5.0, 0.0, 6.0 / HT, 1.0,
                                     "global_y"))
    m.add_case("C", {"W": 1.0})
    eng, res = _run(m)

    def w(z):
        return wa + (wb - wa) * (z - za) / (zb - za)

    def above(e):
        lo = max(e, za)
        trap = 0.5 * (w(lo) + w(zb)) * (zb - lo) if lo < zb else 0.0
        return trap + (6.0 if 4.5 >= e else 0.0) + (5.0 if 6.0 >= e else 0.0)

    for s in m.stories:
        vx, vy = _shear(res, s.name)
        assert vx == 0.0
        assert vy == pytest.approx(above(s.elevation), rel=1e-12, abs=1e-12)
    assert res.base["FY"] == pytest.approx(-above(0.0), rel=1e-9)
    _assert_cut(eng, res, m)


def test_local_y_load_on_column_resolves_through_axes():
    """local_y UDL on the column: global horizontal = w * (local y axis)."""
    w = 3.0
    m = _cantilever()
    m.pattern("W", "wind").member_loads.append(
        MemberLoad("C", "udl", w, direction="local_y"))
    m.add_case("C", {"W": 1.0})
    eng, res = _run(m)
    yax = _local_axes(m.members[0])[1]
    for s in m.stories:
        vx, vy = _shear(res, s.name)
        assert vx == pytest.approx(w * yax[0] * (HT - s.elevation), abs=1e-9)
        assert vy == pytest.approx(w * yax[1] * (HT - s.elevation), abs=1e-9)
    assert abs(yax[0]) + abs(yax[1]) == pytest.approx(1.0)
    _assert_cut(eng, res, m)


# --------------------------------------------------------------------------- #
# 4. inclined member with a global_x load straddling a level
# --------------------------------------------------------------------------- #
def test_inclined_member_global_x_split_at_level():
    """Brace (0,0,0)->(6,0,6) plus a column for stability: global_x UDL w
    per unit length; the 3 m level cuts the brace at mid-length, so the
    shear at 3 m is w L / 2 (L = 6 sqrt 2); a point load at a = 0.25
    (z = 1.5) is below the level, one at a = 0.75 above it."""
    m = _model()
    m.set_stories([3.0, 3.0])
    m.add_member("column", "COL", (6, 0, 0), (6, 0, 6), uid="C")
    m.add_member("brace", "BR", (0, 0, 0), (6, 0, 6), uid="B")
    L = 6.0 * math.sqrt(2.0)
    w = 1.5
    p = m.pattern("W", "wind")
    p.member_loads.append(MemberLoad("B", "udl", w, direction="global_x"))
    p.member_loads.append(MemberLoad("B", "point", 7.0, 0.0, 0.25, 1.0,
                                     "global_x"))
    p.member_loads.append(MemberLoad("B", "point", 11.0, 0.0, 0.75, 1.0,
                                     "global_x"))
    m.add_case("C", {"W": 1.0})
    eng, res = _run(m)
    s1, s2 = _names(m)
    assert _shear(res, s1)[0] == pytest.approx(w * L / 2 + 11.0, rel=1e-12)
    # top level: only the node force share is AT the level -> nothing above
    assert _shear(res, s2)[0] == pytest.approx(0.0, abs=1e-12)
    assert res.base["FX"] == pytest.approx(-(w * L + 18.0), rel=1e-9)
    _assert_cut(eng, res, m)


# --------------------------------------------------------------------------- #
# 5. braced frame with wind member loads on the windward columns
# --------------------------------------------------------------------------- #
def _braced_frame():
    m = _model("braced")
    for k in range(NS):
        z0, z1 = k * H, (k + 1) * H
        sn = m.stories[k].name
        for x in (0.0, 6.0):
            m.add_member("column", "COL", (x, 0, z0), (x, 0, z1), story=sn,
                         uid=f"C{k}{int(x)}")
        m.add_member("beam", "COL", (0, 0, z1), (6, 0, z1), story=sn,
                     uid=f"G{k}")
        m.add_member("brace", "BR", (0, 0, z0), (6, 0, z1), story=sn,
                     uid=f"D{k}")
    return m


def test_braced_frame_wind_member_loads():
    m = _braced_frame()
    p = m.pattern("W", "wind")
    w = 4.0
    for k in range(NS):
        p.member_loads.append(MemberLoad(f"C{k}0", "udl", w,
                                         direction="global_x"))
        # leeward suction as a trapezoid on the other column line
        p.member_loads.append(MemberLoad(f"C{k}6", "trapezoid", 1.0, 2.0,
                                         0.0, 1.0, "global_x"))
    m.add_case("C", {"W": 1.0})
    eng, res = _run(m)
    for s in m.stories:
        n_above = NS - round(s.elevation / H)
        assert _shear(res, s.name)[0] == pytest.approx(
            n_above * H * (w + 1.5), rel=1e-12)
    _assert_cut(eng, res, m)
    # legacy definition read zero here
    assert all(v == (0.0, 0.0) for v in
               eng._story_shears(m.cases["C"]).values())


# --------------------------------------------------------------------------- #
# 6. lattice tower with an open-structure wind pattern
# --------------------------------------------------------------------------- #
def _tower():
    m = _model("tower")
    m.add_section(FrameSection.rectangular("LEG", "STL", 0.2, 0.3))
    m.add_section(FrameSection.rectangular("BRR", "STL", 0.1, 0.15))
    m.set_stories([10.0, 10.0, 10.0])
    pts = [(0, 0), (4, 0), (4, 4), (0, 4)]
    for k in range(3):
        z0, z1 = 10.0 * k, 10.0 * (k + 1)
        sn = m.stories[k].name
        for i, (x, y) in enumerate(pts):
            m.add_member("column", "LEG", (x, y, z0), (x, y, z1), story=sn,
                         uid=f"L{k}{i}")
        for i in range(4):
            a, b = pts[i], pts[(i + 1) % 4]
            m.add_member("beam", "BRR", (*a, z1), (*b, z1), story=sn,
                         uid=f"S{k}{i}")
            m.add_member("brace", "BRR", (*a, z0), (*b, z1), story=sn,
                         uid=f"D{k}{i}")
    ow.assign(m, [mm.uid for mm in m.members], {})
    return m


def test_open_structure_wind_lattice():
    m = _tower()
    ow.generate(m, "OW", add_case=False, V=40.0, angle=30.0)
    pat = "OW"
    assert m.patterns[pat].member_loads
    m.add_case("C", {pat: 1.0})
    eng, res = _run(m)
    # hand total: every member load integrated over the part above e
    def hand(e):
        vx = vy = 0.0
        for ml in m.patterns[pat].member_loads:
            mem = next(x for x in m.members if x.uid == ml.member_uid)
            zi, zj = mem.pi[2], mem.pj[2]
            L = mem.length
            n = 2000
            for k in range(n):
                f = (k + 0.5) / n
                if not (ml.a <= f <= ml.b):
                    continue
                if zi + (zj - zi) * f < e:
                    continue
                d = {"global_x": (1, 0), "global_y": (0, 1)}[ml.direction]
                ww = ml.w if ml.kind == "udl" else (
                    ml.w + (ml.w2 - ml.w) * (f - ml.a) / (ml.b - ml.a))
                vx += ww * d[0] * L / n
                vy += ww * d[1] * L / n
        return vx, vy
    for s in m.stories:
        vx, vy = _shear(res, s.name)
        hx, hy = hand(s.elevation)
        assert vx == pytest.approx(hx, rel=2e-3, abs=1e-6)
        assert vy == pytest.approx(hy, rel=2e-3, abs=1e-6)
    assert abs(_shear(res, m.stories[0].name)[0]) > 1.0
    _assert_cut(eng, res, m)


# --------------------------------------------------------------------------- #
# 7. shell wall with hydrostatic lateral pressure (joint pattern)
# --------------------------------------------------------------------------- #
def test_shell_wall_hydrostatic_pressure():
    """Wall in the YZ plane (B = 4 m, 9 m tall, 1 m mesh); global_x
    pressure q(z) = g (HT - z).  Consistent bilinear nodal forces: the
    nodes AT level e carry the top-node share of the element row below,
    B h (q(e - h)/6 + q(e)/3), so
    V(e) = B g (HT - e)^2 / 2 + B h (q(e - h)/6 + q(e)/3)."""
    g, B, h = 10.0, 4.0, 1.0
    m = _model("wall")
    m.add_shell_section(ShellSection("W", "STL", 0.2))
    m.add_shell("wall", "shell", "W", [(0, 0, 0), (0, B, 0), (0, B, HT),
                                       (0, 0, HT)], mesh_size=h, uid="WA")
    m.pattern("HP", "other").area_loads.append(AreaLoad(
        "WA", g, direction="global_x",
        joint_pattern={"type": "linear", "a": 0.0, "b": 0.0, "c": -1.0,
                       "d": HT}))
    m.add_case("C", {"HP": 1.0})
    eng, res = _run(m)

    def q(z):
        return g * (HT - z)

    for s in m.stories:
        e = s.elevation
        exp = B * g * (HT - e) ** 2 / 2 + B * h * (q(e - h) / 6 + q(e) / 3)
        assert _shear(res, s.name)[0] == pytest.approx(exp, rel=1e-9)
    assert res.base["FX"] == pytest.approx(-B * g * HT ** 2 / 2, rel=1e-9)
    _assert_cut(eng, res, m, rel=1e-6, absol=1e-6)


def test_slab_directional_area_load_and_load_set():
    """Horizontal slab at 6 m with a global_y area load (load set) and a
    global_x area load: the whole resultant counts at levels <= 6 m."""
    m = _model("slab")
    m.add_shell_section(ShellSection("S", "STL", 0.2))
    for x, y in ((0, 0), (4, 0), (4, 4), (0, 4)):
        for k in range(NS):
            m.add_member("column", "COL", (x, y, k * H), (x, y, (k + 1) * H),
                         story=m.stories[k].name)
    r = m.add_shell("slab", "shell", "S", [(0, 0, 6), (4, 0, 6), (4, 4, 6),
                                           (0, 4, 6)], mesh_size=1.0,
                    uid="SL")
    m.pattern("A", "wind").area_loads.append(
        AreaLoad("SL", 2.0, direction="global_x"))
    m.shell_load_sets = {"LS": {"A": {"q": 1.5, "direction": "global_y"}}}
    r.load_set = "LS"
    m.add_case("C", {"A": 1.0})
    eng, res = _run(m)
    s1, s2, s3 = _names(m)
    for s in (s1, s2):
        vx, vy = _shear(res, s)
        assert vx == pytest.approx(2.0 * 16.0, rel=1e-12)
        assert vy == pytest.approx(1.5 * 16.0, rel=1e-12)
    assert _shear(res, s3) == (0.0, 0.0)


# --------------------------------------------------------------------------- #
# 8. tendon with horizontal kink forces (self-equilibrated)
# --------------------------------------------------------------------------- #
def test_tendon_in_column_horizontal_kink_forces():
    """Parabolic tendon (sag s along +X) in a 9 m column, P = sigma A, no
    losses.  Small-slope equivalent loads: anchor horizontal components
    4 P s / L each, curvature load 8 P s / L^2 (opposite) -> net zero
    (base shear 0) but V(e) = +/-4 P s (2 e - L) / L^2 at the levels."""
    m = _cantilever()
    m.pattern("PT", "other")
    sigma, A, s_ = 1.0e6, 1.0e-3, 0.1
    m.tendons = [{"uid": "T1", "points": [[0, 0, 0], [0, 0, HT]],
                  "sags": [s_], "drape_dir": [1, 0, 0], "area": A,
                  "jacking_stress": sigma, "host": ["C"], "pattern": "PT",
                  "n_sub": 36}]
    m.add_case("C", {"PT": 1.0})
    eng, res = _run(m)
    P = sigma * A
    vals = []
    for s in m.stories:
        e = s.elevation
        vx, vy = _shear(res, s.name)
        assert abs(vx) == pytest.approx(4 * P * s_ * abs(2 * e - HT) / HT ** 2,
                                        rel=1e-9)
        vals.append((vx, 2 * e - HT))
        assert vy == pytest.approx(0.0, abs=1e-9)
    # one consistent sign for (2e - L): linear in e
    sg = [math.copysign(1.0, v) * math.copysign(1.0, k) for v, k in vals]
    assert len(set(sg)) == 1
    assert res.base["FX"] == pytest.approx(0.0, abs=1e-6 * P)
    _assert_cut(eng, res, m, rel=1e-6, absol=1e-6)


# --------------------------------------------------------------------------- #
# 9. thermal / ground displacement: no story shear
# --------------------------------------------------------------------------- #
def test_thermal_and_ground_displacement_contribute_zero():
    m = _braced_frame()
    p = m.pattern("T", "other")
    for k in range(NS):
        p.thermal_loads.append(ThermalLoad(f"D{k}", 40.0))
        p.thermal_loads.append(ThermalLoad(f"G{k}", -25.0))
    p.ground_displacements.append(GroundDisplacement((6.0, 0.0, 0.0),
                                                     ux=0.01))
    m.add_case("C", {"T": 1.0})
    eng, res = _run(m)
    for s in m.stories:
        assert _shear(res, s.name) == (0.0, 0.0)
    assert any(abs(res.story[s.name]["ux"]) > 1e-6 for s in m.stories)


# --------------------------------------------------------------------------- #
# 10. combos linear in cases; P-Delta / corotational capture
# --------------------------------------------------------------------------- #
def test_combo_story_shear_is_linear_in_cases():
    m = _braced_frame()
    pw = m.pattern("W", "wind")
    for k in range(NS):
        pw.member_loads.append(MemberLoad(f"C{k}0", "udl", 3.0,
                                          direction="global_x"))
    m.pattern("E", "quake").story_forces.append(
        StoryForce(m.stories[1].name, fx=20.0, fy=0.0))
    m.pattern("N", "other").nodal_loads.append(
        NodalLoad((6.0, 0.0, HT), fx=-7.0))
    for n in ("W", "E", "N"):
        m.add_case(n, {n: 1.0})
    m.add_case("WEN", {"W": 1.2, "E": -0.5, "N": 2.0})
    m.add_combo("CB", {"W": 1.2, "E": -0.5, "N": 2.0})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        out = OpenSeesEngine(m).run().to_dict()
    cases, combos = out["cases"], out["combos"]
    for s in m.stories:
        for k in ("shear_x", "shear_y"):
            lin = (1.2 * cases["W"]["story"][s.name][k]
                   - 0.5 * cases["E"]["story"][s.name][k]
                   + 2.0 * cases["N"]["story"][s.name][k])
            assert combos["CB"]["story"][s.name][k] == pytest.approx(
                lin, rel=1e-12, abs=1e-12)
            assert cases["WEN"]["story"][s.name][k] == pytest.approx(
                lin, rel=1e-12, abs=1e-12)
    assert cases["W"]["story"][m.stories[0].name]["shear_x"] == \
        pytest.approx(3.0 * 6.0)


@pytest.mark.parametrize("geom", ["pdelta", "corotational"])
def test_geometric_cases_count_member_loads(geom):
    m = _braced_frame()
    p = m.pattern("W", "wind")
    for k in range(NS):
        p.member_loads.append(MemberLoad(f"C{k}0", "udl", 2.0,
                                         direction="global_x"))
    m.pattern("G", "dead").member_loads.extend(
        MemberLoad(f"G{k}", "udl", 10.0) for k in range(NS))
    m.add_case("C", {"W": 1.0}, geometric=geom,
               pdelta_gravity={"G": 1.0})
    eng, res = _run(m)
    for s in m.stories:
        n_above = NS - round(s.elevation / H)
        assert _shear(res, s.name)[0] == pytest.approx(2.0 * H * n_above,
                                                       rel=1e-12)


def test_replay_equals_capture():
    """Without a capture (e.g. a nonlinear static final state) the pattern
    replay gives the same extras and leaves the domain/bookkeeping alone."""
    m = _braced_frame()
    p = m.pattern("W", "wind")
    p.member_loads.append(MemberLoad("D1", "trapezoid", 1.0, 3.0, 0.2, 0.9,
                                     "global_y"))
    p.member_loads.append(MemberLoad("C20", "point", 5.0, 0.0, 0.3, 1.0,
                                     "global_x"))
    m.add_case("C", {"W": 1.0})
    eng, res = _run(m)
    span_before = {k: list(v) for k, v in eng._seg_span_loads.items()}
    rep = ssx.extra_shears(eng, eng._asm, {"W": 1.0})
    assert {k: list(v) for k, v in eng._seg_span_loads.items()} == \
        span_before
    for s in m.stories:
        vx, vy = rep.get(s.name, (0.0, 0.0))
        assert (vx, vy) == pytest.approx(_shear(res, s.name), abs=1e-12)
    _assert_cut(eng, res, m)


# --------------------------------------------------------------------------- #
# 11. story stiffness = shear / drift (diagnostics + tables)
# --------------------------------------------------------------------------- #
def test_story_stiffness_uses_full_shear():
    m = _braced_frame()
    p = m.pattern("W", "wind")
    for k in range(NS):
        p.member_loads.append(MemberLoad(f"C{k}0", "udl", 5.0,
                                         direction="global_x"))
    m.add_case("C", {"W": 1.0})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        eng = OpenSeesEngine(m)
        out = eng.run().to_dict()
    st = out["cases"]["C"]["story"]
    sk = out["story_stiffness"]["C"]
    prev = 0.0
    for s in m.stories:
        dx = st[s.name]["ux"] - prev
        prev = st[s.name]["ux"]
        if st[s.name]["shear_x"] == 0.0:
            assert sk[s.name]["kx"] == 0.0
            continue
        assert sk[s.name]["kx"] == pytest.approx(
            abs(st[s.name]["shear_x"] / dx), rel=1e-12)
        assert sk[s.name]["kx"] > 0.0
    from skyframe.core import tables
    rows = tables.compute_table("story_stiffness", out, m)
    rows = rows.get("rows", rows) if isinstance(rows, dict) else rows
    r1 = [r for r in rows if r["case"] == "C"
          and r["story"] == m.stories[0].name][0]
    assert r1["shear_x"] == pytest.approx(5.0 * 6.0)
    assert r1["stiff_x"] == pytest.approx(sk[m.stories[0].name]["kx"])


# --------------------------------------------------------------------------- #
# 12. cut-force equality on a 3-story frame + wall model, mixed loads
# --------------------------------------------------------------------------- #
def test_cut_force_equality_frame_wall_mixed():
    m = _braced_frame()
    m.add_shell_section(ShellSection("W", "STL", 0.2))
    m.add_shell("wall", "shell", "W", [(6, 0, 0), (6, 4, 0), (6, 4, HT),
                                       (6, 0, HT)], mesh_size=1.0, uid="WA")
    for k in range(NS):
        m.add_member("column", "COL", (6, 4, k * H), (6, 4, (k + 1) * H),
                     story=m.stories[k].name, uid=f"CW{k}")
        m.add_member("beam", "COL", (0, 0, (k + 1) * H),
                     (6, 4, (k + 1) * H), story=m.stories[k].name,
                     uid=f"GW{k}")
    p = m.pattern("MIX", "other")
    p.member_loads.append(MemberLoad("C10", "trapezoid", 2.0, 6.0, 0.1, 0.8,
                                     "global_x"))
    p.member_loads.append(MemberLoad("D2", "udl", 1.5, 0.0, 0.2, 0.7,
                                     "global_y"))
    p.member_loads.append(MemberLoad("GW1", "point", 9.0, 0.0, 0.5, 1.0,
                                     "global_y"))
    p.member_loads.append(MemberLoad("G0", "udl", 12.0))          # gravity
    p.area_loads.append(AreaLoad("WA", 3.0, direction="global_x",
                                 joint_pattern={"type": "linear", "a": 0.0,
                                                "b": 0.0, "c": -0.1,
                                                "d": 1.0}))
    p.nodal_loads.append(NodalLoad((0.0, 0.0, 6.0), fx=4.0, fy=-2.0))
    p.story_forces.append(StoryForce(m.stories[2].name, fx=10.0, fy=3.0))
    p.self_weight_factor = 1.0
    m.add_case("C", {"MIX": 1.0})
    eng, res = _run(m)
    _assert_cut(eng, res, m, rel=1e-6, absol=1e-6)
    s1 = m.stories[0].name
    assert abs(_shear(res, s1)[0]) > 10.0 and abs(_shear(res, s1)[1]) > 1.0
    # the legacy sum (story force + nodal load only) differs
    leg = eng._story_shears(m.cases["C"])[s1]
    assert abs(leg[0] - _shear(res, s1)[0]) > 1.0


# --------------------------------------------------------------------------- #
# 13. legacy loads unchanged (bit-identical)
# --------------------------------------------------------------------------- #
def test_story_force_and_nodal_only_model_is_bit_identical_to_legacy():
    m = _braced_frame()
    p = m.pattern("E", "quake")
    p.story_forces.append(StoryForce(m.stories[0].name, fx=11.1, fy=2.2))
    p.story_forces.append(StoryForce(m.stories[2].name, fx=33.3, fy=-4.4))
    p.nodal_loads.append(NodalLoad((6.0, 0.0, 6.0), fx=0.7, fy=0.3, fz=-9))
    p.member_loads.append(MemberLoad("G1", "udl", 8.0))           # gravity
    m.add_case("C", {"E": 1.3})
    eng, res = _run(m)
    legacy = eng._story_shears(m.cases["C"])
    for s in m.stories:
        assert _shear(res, s.name) == legacy[s.name]      # exact equality


def test_gravity_on_inclined_and_rotated_members_adds_nothing():
    """Gravity loads on skewed / rotated members: the horizontal rounding
    noise of the axis transform is dropped (exactly zero extras)."""
    m = _braced_frame()
    m.add_member("beam", "COL", (0, 0, 3), (6, 0, 6), uid="SK", angle=17.0)
    p = m.pattern("G", "dead")
    p.member_loads.append(MemberLoad("SK", "trapezoid", 3.0, 5.0, 0.1, 0.9))
    p.member_loads.append(MemberLoad("SK", "point", 4.0, 0.0, 0.4, 1.0,
                                     "global_z"))
    p.self_weight_factor = 1.0
    m.materials["STL"].unit_weight = 77.0
    m.add_case("C", {"G": 1.0})
    eng, res = _run(m)
    for s in m.stories:
        assert _shear(res, s.name) == (0.0, 0.0)
