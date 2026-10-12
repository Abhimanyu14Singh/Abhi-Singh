"""D3 audit: story centre of mass (CM) and centre of rigidity (CR).

CONTRACT "Centers of mass and rigidity (audit D3)".  Every expected value is
a closed-form hand calculation or an independent numpy stiffness solve:

CM (the analysis lateral mass of the story)
  * uniform slab + an eccentric added joint mass        (was: ignored)
  * beam point load at a = 0.25 L                        (was: at midspan)
  * L-shaped polygon slab, slab with an eccentric opening (was: bbox centre)
  * frames + wall + slab self-weight + area load + frame / shell additional
    mass on one story                                    (was: add. mass lost)
  * element self-mass mode: masses at the member end nodes (was: load
    centroid of the mass-source patterns)
  * diaphragm / no-diaphragm stories, multi-diaphragm story, towers, and the
    cumulative XCCM / YCCM table columns.

CR (unit-load method — ETABS: a force applied to THIS story only, the other
stories unloaded and free, produces no rotation of this story)
  * symmetric frame -> geometric centre
  * single story, guided columns k = 12EI/h^3 and cantilever columns
    k = 3EI/h^3 with one stiffer column line: x_CR = sum(k x) / sum(k)
  * 3-story asymmetric column building vs an independent numpy assembly
    of the condensed 3D column stiffness with rigid diaphragms (with and
    without the condensed-modal memo path).

Units per CONTRACT.md: kN, m, tonne, s; E in kPa.
"""
import math

import numpy as np
import pytest

from skyframe.core import tables
from skyframe.core.builder import quick_building
from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection,
                                 GridSystem, Material, MemberLoad, MemberUDL,
                                 NodalMass, Opening, PointSupport,
                                 ShellSection, Story, StoryForce)
from skyframe.engine.opensees_engine import OpenSeesEngine
from skyframe.engine.story_centers import region_centroid

pytestmark = pytest.mark.filterwarnings("ignore")

G = 9.80665
E = 25e6
NU = 0.2
H = 3.0


def _run(m):
    e = OpenSeesEngine(m)
    d = e.run().to_dict()
    return d, tables.engine_context(e)


def _rows(d, m, ctx):
    return tables.compute_table("centers_mass_rigidity", d, m, ctx)["rows"]


# --------------------------------------------------------------------------- #
# CM
# --------------------------------------------------------------------------- #
def _slab_model(corners=None, uw=0.0, swf=0.0, q=5.0, behavior="membrane",
                openings=None):
    """One story, 10 x 8 plan, 4 corner columns, 4 edge beams, one slab."""
    m = BuildingModel(name="cm")
    m.add_material(Material("C", E=E, nu=NU, unit_weight=uw))
    m.set_stories([H])
    m.add_section(FrameSection.rectangular("COL", "C", 0.5, 0.5))
    m.add_section(FrameSection.rectangular("BM", "C", 0.3, 0.6))
    m.add_shell_section(ShellSection("SL", "C", 0.2))
    pts = [(0, 0), (10, 0), (10, 8), (0, 8)]
    for i, (x, y) in enumerate(pts):
        m.add_member("column", "COL", (x, y, 0), (x, y, H), story="Story1",
                     uid=f"C{i}")
        m.supports.append(PointSupport((x, y, 0), (1, 1, 1, 1, 1, 1)))
        x2, y2 = pts[(i + 1) % 4]
        m.add_member("beam", "BM", (x, y, H), (x2, y2, H), story="Story1",
                     uid=f"B{i}")
    corners = corners or [(0, 0, H), (10, 0, H), (10, 8, H), (0, 8, H)]
    m.add_shell("slab", behavior, "SL", corners, uid="S1", mesh_size=2.0,
                openings=openings)
    dead = m.pattern("DEAD", "dead")
    dead.self_weight_factor = swf
    if q:
        dead.area_loads.append(AreaLoad("S1", q))
    m.add_case("DEAD", {"DEAD": 1.0})
    m.mass_source = {"DEAD": 1.0}
    m.num_modes = 0
    return m


def test_cm_uniform_slab_plus_eccentric_point_mass():
    m = _slab_model()
    d, ctx = _run(m)
    sp = d["story_props"]["Story1"]
    assert (sp["cm_x"], sp["cm_y"]) == (5.0, 4.0)
    m = _slab_model()
    m.nodal_masses.append(NodalMass((10.0, 8.0, H), mx=20.0, my=20.0))
    d, ctx = _run(m)
    ms = 5.0 * 80.0 / G
    sp = d["story_props"]["Story1"]
    assert sp["cm_x"] == pytest.approx((ms * 5 + 20 * 10) / (ms + 20), rel=1e-12)
    assert sp["cm_y"] == pytest.approx((ms * 4 + 20 * 8) / (ms + 20), rel=1e-12)
    row = _rows(d, m, ctx)[0]
    assert row["mass"] == pytest.approx(ms + 20.0, rel=1e-12)  # was ms only
    assert (row["cm_x"], row["cm_y"]) == (sp["cm_x"], sp["cm_y"])
    # the CM displacement table follows the corrected CM
    r = tables.compute_table("diaphragm_cm_displacements", d, m, ctx)["rows"]
    assert all((x["x"], x["y"]) == (sp["cm_x"], sp["cm_y"]) for x in r)


def test_cm_beam_point_load_at_quarter_span():
    m = _slab_model()
    m.patterns["DEAD"].member_loads.append(
        MemberLoad("B0", "point", 50.0, a=0.25))
    d, _ = _run(m)
    W = 5.0 * 80.0
    sp = d["story_props"]["Story1"]
    assert sp["cm_x"] == pytest.approx((W * 5 + 50 * 2.5) / (W + 50), rel=1e-12)
    assert sp["cm_y"] == pytest.approx(W * 4 / (W + 50), rel=1e-12)


def test_cm_partial_and_trapezoid_member_loads():
    m = _slab_model(q=0.0)
    p = m.patterns["DEAD"]
    p.member_loads.append(MemberLoad("B0", "udl", 10.0, a=0.0, b=0.4))
    # trapezoid 0 -> 12 kN/m over the whole beam: resultant at 2L/3
    p.member_loads.append(MemberLoad("B2", "trapezoid", 0.0, w2=12.0,
                                     a=0.0, b=1.0))
    d, _ = _run(m)
    w1, x1 = 10.0 * 4.0, 2.0                 # B0: (0,0)->(10,0)
    w2, x2 = 0.5 * 12.0 * 10.0, 10.0 - 10.0 * 2.0 / 3.0   # B2: (10,8)->(0,8)
    sp = d["story_props"]["Story1"]
    assert sp["cm_x"] == pytest.approx((w1 * x1 + w2 * x2) / (w1 + w2))
    assert sp["cm_y"] == pytest.approx(w2 * 8.0 / (w1 + w2))


def test_cm_l_shaped_slab_and_eccentric_opening():
    L = [(0, 0, H), (10, 0, H), (10, 4, H), (5, 4, H), (5, 8, H), (0, 8, H)]
    m = _slab_model(corners=L)
    d, _ = _run(m)
    sp = d["story_props"]["Story1"]
    # 10x4 at (5,2) + 5x4 at (2.5,6)
    assert sp["cm_x"] == pytest.approx((40 * 5 + 20 * 2.5) / 60, rel=1e-12)
    assert sp["cm_y"] == pytest.approx((40 * 2 + 20 * 6) / 60, rel=1e-12)
    # opening u 0.6..0.8, v 0.5..1.0 of the 10x8 slab: hole 2x4 at (7,6)
    m = _slab_model(behavior="shell",
                    openings=[Opening(0.6, 0.5, 0.8, 1.0)])
    cx, cy = region_centroid(m.shells[0])
    assert (cx, cy) == pytest.approx(((80 * 5 - 8 * 7) / 72,
                                      (80 * 4 - 8 * 6) / 72), rel=1e-12)
    d, _ = _run(m)
    sp = d["story_props"]["Story1"]
    assert (sp["cm_x"], sp["cm_y"]) == pytest.approx((cx, cy), rel=1e-12)


def test_cm_frames_wall_slab_and_additional_mass():
    m = _slab_model(uw=24.0, swf=1.0, q=2.0)
    m.add_shell_section(ShellSection("WL", "C", 0.25))
    m.add_shell("wall", "shell", "WL",
                [(10, 0, 0), (10, 8, 0), (10, 8, H), (10, 0, H)],
                uid="W1", mesh_size=1.0)
    m.members[1].additional_mass = 0.0          # (a column: none)
    beam0 = next(b for b in m.members if b.uid == "B0")
    beam0.additional_mass = 0.5                 # t/m along y = 0
    m.shells[0].additional_mass = 0.05          # t/m^2 on the slab
    d, ctx = _run(m)
    wb = 0.3 * 0.6 * 24.0                        # beam self-weight kN/m
    parts = [  # (kN, x, y) derived weight
        (wb * 10, 5, 0), (wb * 8, 10, 4), (wb * 10, 5, 8), (wb * 8, 0, 4),
        (0.25 * 24 * 24.0, 10, 4),               # wall (8 x 3 x 0.25)
        (0.2 * 24 * 80 + 2.0 * 80, 5, 4)]        # slab self + area load
    mp = [(w / G, x, y) for w, x, y in parts]
    mp += [(0.5 * 10, 5, 0), (0.05 * 80, 5, 4)]  # additional masses
    M = sum(p[0] for p in mp)
    hx = sum(p[0] * p[1] for p in mp) / M
    hy = sum(p[0] * p[2] for p in mp) / M
    sp = d["story_props"]["Story1"]
    assert sp["cm_x"] == pytest.approx(hx, rel=1e-9)
    assert sp["cm_y"] == pytest.approx(hy, rel=1e-9)
    assert _rows(d, m, ctx)[0]["mass"] == pytest.approx(M, rel=1e-9)


def test_cm_element_self_mass_mode():
    m = _slab_model(uw=24.0, q=0.0)
    m.shells.clear()
    m.patterns["DEAD"].area_loads.clear()
    m.add_section(FrameSection.rectangular("BH", "C", 0.6, 0.9))
    next(b for b in m.members if b.uid == "B0").section = "BH"
    m.mass_source_mode = "element_self_mass"
    d, ctx = _run(m)
    rho = 24.0 / G
    col = rho * 0.25 * H / 2.0                   # column top halves
    bx, by, bh = rho * 0.18 * 10, rho * 0.18 * 8, rho * 0.54 * 10
    node = {(0, 0): col + bh / 2 + by / 2, (10, 0): col + bh / 2 + by / 2,
            (10, 8): col + bx / 2 + by / 2, (0, 8): col + bx / 2 + by / 2}
    M = sum(node.values())
    sp = d["story_props"]["Story1"]
    assert sp["cm_x"] == pytest.approx(
        sum(v * k[0] for k, v in node.items()) / M, rel=1e-12)
    assert sp["cm_y"] == pytest.approx(
        sum(v * k[1] for k, v in node.items()) / M, rel=1e-12)
    assert _rows(d, m, ctx)[0]["mass"] == pytest.approx(M, rel=1e-12)


def test_cm_cumulative_columns_and_no_diaphragm_story():
    m = quick_building(stories=3)
    m.story_diaphragm = {"Story1": "none"}
    m.nodal_masses.append(NodalMass((18.0, 12.0, 9.6), mx=30.0, my=30.0))
    d, ctx = _run(m)
    assert set(d["story_props"]) == {"Story2", "Story3"}   # no CR w/o dia
    rows = _rows(d, m, ctx)
    assert [r["story"] for r in rows] == ["Story3", "Story2"]
    ms = m.compute_story_masses()
    r3, r2 = rows
    assert r3["mass"] == pytest.approx(ms["Story3"] + 30.0)
    assert r3["cm_x"] == pytest.approx(
        (ms["Story3"] * 9 + 30 * 18) / (ms["Story3"] + 30))
    assert (r3["cum_mass"], r3["ccm_x"]) == (r3["mass"], r3["cm_x"])
    tot = r3["mass"] + r2["mass"]
    assert r2["cum_mass"] == pytest.approx(tot)
    assert r2["ccm_x"] == pytest.approx(
        (r3["mass"] * r3["cm_x"] + r2["mass"] * r2["cm_x"]) / tot)
    assert r2["ccm_y"] == pytest.approx(
        (r3["mass"] * r3["cm_y"] + r2["mass"] * r2["cm_y"]) / tot)
    cols = {c["key"]: c for c in
            tables.compute_table("centers_mass_rigidity", d, m, ctx)[
                "columns"]}
    assert "no rotation" in cols["cr_x"]["tip"]


def test_cm_multi_diaphragm_story():
    m = BuildingModel(name="two")
    m.add_material(Material("C", E=3e7, nu=0.2, unit_weight=25.0))
    m.add_section(FrameSection.rectangular("CA", "C", 0.4, 0.4))
    m.add_section(FrameSection.rectangular("B", "C", 0.3, 0.6))
    m.add_shell_section(ShellSection("SL", "C", 0.2))
    m.set_stories([H], ["S1"])
    m.mass_source = {"DEAD": 1.0}
    m.pattern("DEAD", "dead")
    m.diaphragms = {"DA": {"type": "rigid"}, "DB": {"type": "rigid"}}
    for x0, dn in ((0.0, "DA"), (20.0, "DB")):
        pts = [(x0, 0.0), (x0 + 6, 0.0), (x0 + 6, 6.0), (x0, 6.0)]
        for i, (x, y) in enumerate(pts):
            m.add_member("column", "CA", (x, y, 0), (x, y, H), story="S1",
                         uid=f"{dn}C{i}")
            m.supports.append(PointSupport((x, y, 0), (1,) * 6))
            p2 = pts[(i + 1) % 4]
            m.add_member("beam", "B", (x, y, H), (p2[0], p2[1], H),
                         story="S1", uid=f"{dn}B{i}")
        r = m.add_shell("slab", "membrane", "SL",
                        [(x, y, H) for x, y in pts], story="S1",
                        uid=f"{dn}S")
        r.diaphragm = dn
        m.patterns["DEAD"].area_loads.append(AreaLoad(r.uid, 5.0))
    m.nodal_masses.append(NodalMass((6.0, 6.0, H), mx=10.0, my=10.0))
    m.add_case("DEAD", {"DEAD": 1.0})
    m.num_modes = 0
    d, ctx = _run(m)
    rows = {r["diaphragm"]: r for r in _rows(d, m, ctx)}
    Md = 5.0 * 36 / G
    assert rows["DA"]["mass"] == pytest.approx(Md + 10)
    assert rows["DA"]["cm_x"] == pytest.approx((Md * 3 + 60) / (Md + 10))
    assert rows["DA"]["cm_y"] == pytest.approx((Md * 3 + 60) / (Md + 10))
    assert (rows["DB"]["cm_x"], rows["DB"]["cm_y"]) == pytest.approx((23, 3))
    for dn in ("DA", "DB"):                       # symmetric columns
        assert rows[dn]["cr_x"] == pytest.approx(3 + (20 if dn == "DB"
                                                       else 0), abs=1e-9)
        assert rows[dn]["cum_mass"] == rows[dn]["mass"]


def _twin_towers(extra_mass=0.0):
    m = BuildingModel(name="tw")
    m.add_material(Material("C", E=E, nu=NU, unit_weight=24.0))
    m.add_section(FrameSection.rectangular("COL", "C", 0.4, 0.4))
    m.add_section(FrameSection.rectangular("BM", "C", 0.3, 0.5))
    m.pattern("DEAD", "dead")
    m.mass_source = {"DEAD": 1.0}
    m.num_modes = 0
    for pre, ox, tw in (("A", 0.0, "T1"), ("B", 40.0, "T2")):
        m.grid_systems.append(GridSystem([0.0, 6.0], [0.0, 5.0],
                                         name="G" + pre, origin=(ox, 0.0)))
        for i in range(2):
            s = Story(f"{pre}{i + 1}", H, H * (i + 1))
            s.tower = tw
            m.stories.append(s)
            zt, zb = s.elevation, s.elevation - H
            for x in (0.0, 6.0):
                for y in (0.0, 5.0):
                    m.add_member("column", "COL", (ox + x, y, zb),
                                 (ox + x, y, zt), story=s.name,
                                 uid=f"{pre}C{i}{x}{y}")
                    if i == 0:
                        m.supports.append(PointSupport((ox + x, y, 0),
                                                       (1,) * 6))
            for y in (0.0, 5.0):
                b = m.add_member("beam", "BM", (ox, y, zt), (ox + 6, y, zt),
                                 story=s.name, uid=f"{pre}BX{i}{y}")
                m.patterns["DEAD"].member_udls.append(MemberUDL(b.uid, 20.0))
            for x in (0.0, 6.0):
                b = m.add_member("beam", "BM", (ox + x, 0, zt),
                                 (ox + x, 5.0, zt), story=s.name,
                                 uid=f"{pre}BY{i}{x}")
                m.patterns["DEAD"].member_udls.append(MemberUDL(b.uid, 15.0))
    m.grid = m.grid_systems[0]
    m.towers = [{"name": "T1", "grid_system": "GA", "base_elevation": 0.0},
                {"name": "T2", "grid_system": "GB", "base_elevation": 0.0}]
    if extra_mass:
        m.nodal_masses.append(NodalMass((46.0, 5.0, 2 * H), mx=extra_mass,
                                        my=extra_mass))
    m.add_case("DEAD", {"DEAD": 1.0})
    m.validate()
    return m


def test_cm_towers_point_mass_and_cumulative_per_tower():
    m = _twin_towers(extra_mass=10.0)
    d, ctx = _run(m)
    Md = (2 * 20 * 6 + 2 * 15 * 5) / G           # beam UDL mass per story
    sp = d["story_props"]
    assert (sp["A2"]["cm_x"], sp["A2"]["cm_y"]) == pytest.approx((3.0, 2.5))
    assert sp["B2"]["cm_x"] == pytest.approx((Md * 43 + 10 * 46) / (Md + 10))
    assert sp["B2"]["cm_y"] == pytest.approx((Md * 2.5 + 10 * 5) / (Md + 10))
    assert sp["B1"]["cm_x"] == pytest.approx(43.0)
    rows = {r["story"]: r for r in _rows(d, m, ctx)}
    # cumulative never mixes the two towers
    assert rows["A1"]["cum_mass"] == pytest.approx(2 * Md)
    assert rows["A1"]["ccm_x"] == pytest.approx(3.0)
    assert rows["B1"]["cum_mass"] == pytest.approx(2 * Md + 10)
    assert rows["B1"]["ccm_x"] == pytest.approx(
        (2 * Md * 43 + 10 * 46) / (2 * Md + 10))


# --------------------------------------------------------------------------- #
# CR
# --------------------------------------------------------------------------- #
def test_cr_symmetric_frame_at_geometric_centre():
    d, _ = _run(quick_building(stories=3))
    for e in d["story_props"].values():
        assert (e["cr_x"], e["cr_y"]) == pytest.approx((9.0, 6.0), abs=1e-9)


def _column_story(cols, guided):
    """Single story of square columns (x, y, side) on a rigid diaphragm;
    ``guided`` restrains the column-top rotations (k = 12EI/h^3), else the
    columns are cantilevers (k = 3EI/h^3)."""
    m = BuildingModel(name="cr1")
    m.add_material(Material("C", E=E, nu=NU))
    m.set_stories([H])
    for i, (x, y, s) in enumerate(cols):
        nm = f"S{s}"
        if nm not in m.sections:
            m.add_section(FrameSection.rectangular(nm, "C", s, s))
        m.add_member("column", nm, (x, y, 0), (x, y, H), story="Story1",
                     uid=f"C{i}")
        m.supports.append(PointSupport((x, y, 0), (1,) * 6))
        if guided:
            m.supports.append(PointSupport((x, y, H), (0, 0, 0, 1, 1, 0)))
    m.story_masses = {"Story1": 10.0}
    m.num_modes = 0
    return m


@pytest.mark.parametrize("guided,coef", [(True, 12.0), (False, 3.0)])
def test_cr_single_story_sum_kx_over_sum_k(guided, coef):
    # a stiff column line at x = 0 (0.7 m), a light one at x = 9 (0.4 m),
    # and an off-line column at (4, 7)
    cols = [(0, 0, 0.7), (0, 6, 0.7), (0, 3, 0.7), (9, 0, 0.4), (9, 6, 0.4),
            (4, 7, 0.5)]
    d, _ = _run(_column_story(cols, guided))
    k = [coef * E * s ** 4 / 12.0 / H ** 3 for _, _, s in cols]
    xcr = sum(ki * c[0] for ki, c in zip(k, cols)) / sum(k)
    ycr = sum(ki * c[1] for ki, c in zip(k, cols)) / sum(k)
    sp = d["story_props"]["Story1"]
    assert sp["cr_x"] == pytest.approx(xcr, rel=1e-8)
    assert sp["cr_y"] == pytest.approx(ycr, rel=1e-8)
    assert xcr < 2.5                    # pulled toward the stiff line


def _numpy_cr(cols, heights, xm, ym):
    """Independent CR: assemble the 3D stiffness of continuous vertical
    Euler-Bernoulli columns (bending x / y + St-Venant torsion) tied by a
    rigid diaphragm at every level; per story apply unit Fx, Fy, Mz at
    that level only and read its rotation."""
    n = len(heights)
    nc = len(cols)
    Gm = E / (2 * (1 + NU))
    ndof = 3 * n + 2 * nc * n

    def lv(L):                          # level L (1..n) master dofs
        return 3 * (L - 1)

    def sl(c, L, j):                    # column c slope dof at level L
        return 3 * n + 2 * (nc * (L - 1) + c) + j

    K = np.zeros((ndof, ndof))
    for L in range(1, n + 1):
        h = heights[L - 1]
        for c, (x, y, ss) in enumerate(cols):
            s = ss[L - 1]
            sec = FrameSection.rectangular("t", "C", s, s)
            EI = E * s ** 4 / 12.0
            kb = EI / h ** 3 * np.array(
                [[12, 6 * h, -12, 6 * h], [6 * h, 4 * h * h, -6 * h, 2 * h * h],
                 [-12, -6 * h, 12, -6 * h], [6 * h, 2 * h * h, -6 * h, 4 * h * h]])
            for j, (cu, cth) in enumerate(((1.0, -(y - ym)), (1.0, x - xm))):
                T = np.zeros((4, ndof))     # elem dofs -> global
                for row, LL in ((0, L - 1), (2, L)):
                    if LL >= 1:
                        T[row, lv(LL) + j] = cu
                        T[row, lv(LL) + 2] = cth
                for row, LL in ((1, L - 1), (3, L)):
                    if LL >= 1:
                        T[row, sl(c, LL, j)] = 1.0
                K += T.T @ kb @ T
            kt = Gm * sec.J / h
            T = np.zeros((2, ndof))
            if L - 1 >= 1:
                T[0, lv(L - 1) + 2] = 1.0
            T[1, lv(L) + 2] = 1.0
            K += T.T @ (kt * np.array([[1, -1], [-1, 1]])) @ T
    out = []
    for L in range(1, n + 1):
        th = []
        for j in range(3):
            f = np.zeros(ndof)
            f[lv(L) + j] = 1.0
            th.append(np.linalg.solve(K, f)[lv(L) + 2])
        out.append((xm - th[1] / th[2], ym + th[0] / th[2]))
    return out


@pytest.mark.parametrize("modes", [0, 3])
def test_cr_multistory_matches_numpy_stiffness(modes):
    heights = [4.0, 3.0, 3.0]
    # per-story sizes: the stiffness distribution changes up the height
    cols = [(0, 0, (0.6, 0.6, 0.4)), (8, 0, (0.4, 0.65, 0.4)),
            (0, 6, (0.5, 0.5, 0.5)), (8, 6, (0.4, 0.4, 0.7)),
            (3, 6, (0.7, 0.45, 0.45))]
    m = BuildingModel(name="crn")
    m.add_material(Material("C", E=E, nu=NU))
    m.set_stories(heights)
    for c, (x, y, ss) in enumerate(cols):
        z = 0.0
        for i, h in enumerate(heights):
            nm = f"S{c}_{i}"
            m.add_section(FrameSection.rectangular(nm, "C", ss[i], ss[i]))
            m.add_member("column", nm, (x, y, z), (x, y, z + h),
                         story=f"Story{i + 1}", uid=f"C{c}_{i}")
            z += h
        m.supports.append(PointSupport((x, y, 0), (1,) * 6))
    m.story_masses = {f"Story{i + 1}": 20.0 for i in range(3)}
    m.num_modes = modes
    d, _ = _run(m)
    xm, ym = m.plan_center()
    ref = _numpy_cr(cols, heights, xm, ym)
    for i, (xr, yr) in enumerate(ref):
        sp = d["story_props"][f"Story{i + 1}"]
        assert sp["cr_x"] == pytest.approx(xr, rel=1e-7, abs=1e-9)
        assert sp["cr_y"] == pytest.approx(yr, rel=1e-7, abs=1e-9)
    # coupled stories: the CR is NOT the story's own sum(k x)/sum(k)
    k = [ss[1] ** 4 for _, _, ss in cols]
    naive = sum(ki * c[0] for ki, c in zip(k, cols)) / sum(k)
    assert abs(ref[1][0] - naive) > 1e-2
