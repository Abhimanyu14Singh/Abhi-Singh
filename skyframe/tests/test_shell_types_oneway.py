"""B3: ETABS shell element types, one-way membrane slabs, shell benchmarks.

CONTRACT "Shell element types, one-way slabs and shell benchmarks".

* shell_type plumbing (default byte-identical, serialization, validation,
  element family actually built, membrane / plate stiffness groups);
* classical shell benchmarks (Scordelis-Lo, pinched cylinder, MacNeal-
  Harder twisted beam, hard-SS thin / thick plate, Morley skew plate,
  MacNeal-Harder trapezoidal cantilever) at 4x4 / 8x8 / 16x16 for every
  element family, pinned to the convergence table in the CONTRACT;
* one-way slab distribution, hand-checked (rectangle, parallelogram,
  trapezoid) and end-to-end through the engine with released edge beams.
"""

import math
import warnings

import numpy as np
import pytest

from _v02_utils import (          # noqa: E402
    model_mod, FrameSection, NodalLoad, PointSupport,
    new_model, finish, run, find_node, stations,
)
from skyframe.core import shell_types as st
from skyframe.core import oneway as ow
from skyframe.core.mesh import mesh_model
from skyframe.core.model import BuildingModel, ShellRegion, ShellSection
from skyframe.engine import shell_benchmarks as sb
from skyframe.engine import shell_elements as shel


# --------------------------------------------------------------------------- #
# 1. shell_type plumbing
# --------------------------------------------------------------------------- #
def test_shell_type_default_and_serialization():
    s = ShellSection("S", "CONC", 0.2)
    assert s.shell_type == "shell" == st.DEFAULT_SHELL_TYPE
    assert "shell_type" not in s.to_dict()          # legacy JSON unchanged
    assert st.element_names("shell") == ("ShellMITC4", "ShellDKGT")
    mdl = new_model("t", [3.0])
    mdl.add_shell_section(ShellSection("A", "CONC", 0.2,
                                       shell_type="shell_thick"))
    mdl.add_shell_section(ShellSection("B", "CONC", 0.2))
    d = mdl.to_dict()
    assert d["shell_sections"]["A"]["shell_type"] == "shell_thick"
    assert "shell_type" not in d["shell_sections"]["B"]
    back = BuildingModel.from_dict(d)
    assert back.shell_sections["A"].shell_type == "shell_thick"
    assert back.shell_sections["B"].shell_type == "shell"
    assert back.to_dict()["shell_sections"] == d["shell_sections"]


def test_shell_type_validation():
    mdl = new_model("t", [3.0])
    with pytest.raises(ValueError, match="shell_type"):
        mdl.add_shell_section(ShellSection("X", "CONC", 0.2,
                                           shell_type="thin"))
    lay = {"layers": [{"t": 0.2, "material": "CONC", "kind": "concrete"}]}
    with pytest.raises(ValueError, match="layered"):
        mdl.add_shell_section(ShellSection("L", "CONC", 0.2, layered=lay,
                                           shell_type="membrane"))


def test_type_factor_tables():
    assert st.type_factors("shell") == {}
    assert st.type_factors("shell_thin") == {}
    assert st.type_factors("shell_thick") == {}
    assert st.type_factors("membrane") == {k: 1e-6 for k in
                                           ("m11", "m22", "m12", "v13", "v23")}
    for t in ("plate_thin", "plate_thick"):
        assert st.type_factors(t) == {k: 1e-6 for k in ("f11", "f22", "f12")}
    # the typed view multiplies onto user modifiers; default -> identity
    s = ShellSection("S", "CONC", 0.2, m11=0.5, shell_type="membrane")
    v = shel._typed_view(s)
    assert v.m11 == pytest.approx(0.5e-6) and v.f11 == 1.0
    d = ShellSection("D", "CONC", 0.2)
    assert shel._typed_view(d) is d
    assert st.RECOMMENDED_SHELL_TYPE in st.SHELL_TYPES


def _wall_model(shell_type, load="inplane"):
    """4 x 3 m wall (mesh 1 m) fixed at its base nodes, tip load at (4,0,3).

    ``load == "plate"``: a 4 x 4 slab at z = 0 pinned at its 4 corners,
    gravity point load 10 kN at its centre."""
    mdl = new_model("w", [3.0])
    mdl.add_shell_section(ShellSection("SH", "CONC", 0.2,
                                       shell_type=shell_type))
    if load == "inplane":
        mdl.add_shell("wall", "shell", "SH",
                      [(0, 0, 0), (4, 0, 0), (4, 0, 3), (0, 0, 3)],
                      mesh_size=1.0, story="Story1")
        for x in range(5):
            mdl.supports.append(PointSupport((x, 0, 0), (1,) * 6))
        mdl.pattern("P", "dead").nodal_loads.append(
            NodalLoad((4, 0, 3), fx=100.0))
        pt = (4, 0, 3)
    else:
        mdl.add_shell("slab", "shell", "SH",
                      [(0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0)],
                      mesh_size=1.0, story="Story1")
        for (x, y) in ((0, 0), (4, 0), (4, 4), (0, 4)):
            mdl.supports.append(PointSupport((x, y, 0), (1, 1, 1, 0, 0, 0)))
        mdl.pattern("P", "dead").nodal_loads.append(
            NodalLoad((2, 2, 0), fz=-10.0))
        pt = (2, 2, 0)
    mdl.add_case("P", {"P": 1.0})
    finish(mdl, pt)
    return mdl, pt


def _disp(d, pt, dof):
    return d["cases"]["P"]["node_disp"][find_node(d, pt)][dof]


def test_engine_builds_the_type_element_family(monkeypatch):
    seen = []
    orig = shel.add_element

    def spy(ssec, etag, node_tags, stag):
        name = orig(ssec, etag, node_tags, stag)
        seen.append(name)
        return name
    monkeypatch.setattr(shel, "add_element", spy)
    for t, expect in (("shell", "ShellMITC4"), ("shell_thin", "ShellDKGQ"),
                      ("shell_thick", "ASDShellQ4"),
                      ("membrane", "ASDShellQ4"),
                      ("plate_thin", "ShellDKGQ"),
                      ("plate_thick", "ASDShellQ4")):
        seen.clear()
        run(_wall_model(t)[0])
        assert seen and set(seen) == {expect}, (t, set(seen))


def test_triangles_follow_the_type():
    for t in st.SHELL_TYPES:
        assert shel.element_name(ShellSection("S", "C", 0.1, shell_type=t),
                                 3) == st.ELEMENTS[t][1]


def test_membrane_and_plate_stiffness_groups():
    """In-plane wall: membrane == shell_thick (bending never engaged by a
    flat in-plane problem); plate types lose the in-plane stiffness
    (residual 1e-6 -> deflection x ~1e6).  Corner-supported slab: the
    plate types equal their shell counterparts (membrane uncoupled from
    bending in a flat plate); membrane loses bending (x ~1e6)."""
    ip = {t: _disp(run(_wall_model(t)[0]), (4, 0, 3), 0)
          for t in st.SHELL_TYPES}
    assert ip["membrane"] == pytest.approx(ip["shell_thick"], rel=1e-5)
    # coarse 4 x 3 in-plane cantilever: ASDShellQ4 (enhanced membrane) is
    # ~9 % more flexible than the bilinear ShellMITC4 membrane (which locks
    # in in-plane bending); both remain the same order
    assert ip["shell_thick"] == pytest.approx(ip["shell"], rel=0.15)
    for t in ("plate_thin", "plate_thick"):
        assert ip[t] / ip["shell"] > 1e5
    op = {t: _disp(run(_wall_model(t, "plate")[0]), (2, 2, 0), 2)
          for t in st.SHELL_TYPES}
    assert op["plate_thick"] == pytest.approx(op["shell_thick"], rel=1e-5)
    assert op["plate_thin"] == pytest.approx(op["shell_thin"], rel=1e-5)
    assert op["membrane"] / op["shell_thick"] > 1e5
    # identical flat-plate bending: ASDShellQ4 uses the MITC4 bending field
    assert op["shell_thick"] == pytest.approx(op["shell"], rel=1e-6)


def test_shell_thick_stress_axes_match_legacy():
    """ASDShellQ4 gets -local = the ShellMITC4 x axis: a flat wall under a
    uniform in-plane state reports the same Gauss-averaged resultants."""
    outs = {}
    for t in ("shell", "shell_thick"):
        mdl = new_model("u", [3.0])
        mdl.add_shell_section(ShellSection("SH", "CONC", 0.2, shell_type=t))
        mdl.add_shell("wall", "shell", "SH",
                      [(0, 0, 0), (2, 0, 0), (2, 0, 1), (0, 0, 1)],
                      mesh_size=1.0, story="Story1")
        for z in (0, 1):
            mdl.supports.append(PointSupport((0, 0, z), (1, 1, 1, 1, 1, 1)
                                             if z == 0 else
                                             (1, 1, 0, 1, 1, 1)))
            mdl.supports.append(PointSupport((2, 0, z), (0, 1, 0, 1, 1, 1)))
            mdl.pattern("P", "dead").nodal_loads.append(
                NodalLoad((2, 0, z), fx=5.0))
        mdl.supports.append(PointSupport((1, 0, 0), (0, 1, 0, 1, 1, 1)))
        mdl.supports.append(PointSupport((1, 0, 1), (0, 1, 0, 1, 1, 1)))
        mdl.add_case("P", {"P": 1.0})
        finish(mdl, (2, 0, 1))
        d = run(mdl)
        outs[t] = d["cases"]["P"]["shell_forces"]
    for k in outs["shell"]:
        assert np.allclose(outs["shell"][k], outs["shell_thick"][k],
                           atol=1e-6)
    # uniform tension 10 kN over a 1 m high wall -> N = 10 kN/m
    assert max(abs(v[0]) + abs(v[1]) for v in outs["shell"].values()) == \
        pytest.approx(10.0, rel=1e-6)


# --------------------------------------------------------------------------- #
# 2. classical shell benchmarks (convergence table in the CONTRACT)
# --------------------------------------------------------------------------- #
ELEMENTS = ("ShellMITC4", "ShellDKGQ", "ASDShellQ4", "ShellDKGT",
            "ASDShellT3")

# relative error (%) at 4x4 / 8x8 / 16x16 — the CONTRACT table; pinned to
# 0.05 percentage points (deterministic linear solves)
TABLE = {
    "scordelis_lo": {
        "ShellMITC4": (-8.27, -3.60, -1.34),
        "ShellDKGQ": (+5.00, +0.55, -0.34),
        "ASDShellQ4": (+4.48, +0.48, -0.25),
        "ShellDKGT": (-3.79, -2.35, -1.12),
        "ASDShellT3": (+3.97, +0.72, -0.04)},
    "pinched_cylinder": {
        "ShellMITC4": (-65.01, -27.17, -7.49),
        "ShellDKGQ": (-36.09, -4.96, +1.55),
        "ASDShellQ4": (-61.33, -24.65, -6.81),
        "ShellDKGT": (-39.83, -7.15, +1.60),
        "ASDShellT3": (-48.06, -17.57, -4.38)},
    "twisted_beam_inplane": {
        "ShellMITC4": (-77.84, -49.62, -20.83),
        "ShellDKGQ": (-62.30, -32.41, +25.49),
        "ASDShellQ4": (-0.23, -0.12, -0.06),
        "ShellDKGT": (-1.29, -0.60, -0.49),
        "ASDShellT3": (-0.65, -0.29, -0.20)},
    "twisted_beam_outplane": {
        "ShellMITC4": (-68.56, -43.74, -18.40),
        "ShellDKGQ": (-48.31, -17.46, +79.29),
        "ASDShellQ4": (+0.20, +0.00, -0.01),
        "ShellDKGT": (-4.66, -1.59, -0.61),
        "ASDShellT3": (-0.23, -0.15, -0.12)},
    "plate_thin": {
        "ShellMITC4": (-2.30, -0.51, -0.13),
        "ShellDKGQ": (-0.41, -0.06, -0.01),
        "ASDShellQ4": (-2.30, -0.51, -0.13),
        "ShellDKGT": (-4.61, -1.05, -0.25),
        "ASDShellT3": (-16.17, -4.17, -1.03)},
    "plate_thick": {
        "ShellMITC4": (-1.93, -0.43, -0.11),
        "ShellDKGQ": (-5.32, -4.98, -4.94),
        "ASDShellQ4": (-1.93, -0.43, -0.11),
        "ShellDKGT": (-9.30, -5.92, -5.16),
        "ASDShellT3": (-13.80, -3.24, -0.78)},
    "morley_skew": {
        "ShellMITC4": (-39.61, -27.22, -19.62),
        "ShellDKGQ": (+44.71, -4.98, -13.54),
        "ASDShellQ4": (-39.61, -27.22, -19.62),
        "ShellDKGT": (+31.25, -10.41, -20.05),
        "ASDShellT3": (-26.74, -30.21, -26.07)},
    "cantilever_trapezoid_inplane": {
        "ShellMITC4": (-97.63, -93.31, -79.05),
        "ShellDKGQ": (-19.44, -6.86, -2.02),
        "ASDShellQ4": (-94.88, -89.88, -75.01),
        "ShellDKGT": (-82.25, -53.46, -25.14),
        "ASDShellT3": (-56.53, -35.36, -25.98)},
    "cantilever_trapezoid_outplane": {
        "ShellMITC4": (-3.67, -1.43, -0.58),
        "ShellDKGQ": (-1.27, -0.72, -0.48),
        "ASDShellQ4": (-3.69, -1.44, -0.58),
        "ShellDKGT": (-2.26, -1.26, -0.76),
        "ASDShellT3": (-3.96, -1.73, -0.82)},
}


@pytest.fixture(scope="module")
def table():
    return sb.convergence_table(ELEMENTS)


@pytest.mark.parametrize("problem", sorted(TABLE))
def test_benchmark_table_pinned(table, problem):
    for e in ELEMENTS:
        got = [(x - 1.0) * 100.0 for x in table[problem][e]]
        assert got == pytest.approx(list(TABLE[problem][e]), abs=0.05), \
            (problem, e, got)


def test_benchmark_reference_values():
    # Navier (Timoshenko & Woinowsky-Krieger Table 8): alpha = 0.00406
    wk, wm = sb.navier_plate(1000.0)
    assert wk == pytest.approx(0.0040624, abs=2e-7)
    # Mindlin (Wang): w = w_K + M/(kappa G t); Marcus moment at the centre
    # of a SS square plate = (Mx + My)/(1 + nu) = 2 * 0.0479 / 1.3 = 0.0737
    # -> correction D/(kappa G t a^2) * 0.0737 = 0.0737 t^2 / (5 (1-nu) a^2)
    wk10, wm10 = sb.navier_plate(10.0)
    marcus = (wm10 - wk10) / (0.01 / (6 * 0.7 * 5 / 6))
    assert marcus == pytest.approx(0.0737, abs=2e-4)
    assert wm10 / wk10 == pytest.approx(1.0519, abs=5e-4)
    assert sb.SCORDELIS_REF == 0.3024 and sb.PINCHED_REF == 1.8248e-5


def test_benchmark_shell_thick_beats_legacy_on_quads(table):
    """The recommendation: shell_thick quads (ASDShellQ4) are never worse
    than the legacy ShellMITC4 at 16x16 (by more than 0.01 percentage
    points), identical on flat plates, and far better on warped
    (twisted-beam) geometry."""
    for prob, by in table.items():
        a = abs(by["ASDShellQ4"][-1] - 1.0)
        m = abs(by["ShellMITC4"][-1] - 1.0)
        assert a <= m + 1e-4, prob
    for prob in ("twisted_beam_inplane", "twisted_beam_outplane"):
        for x in table[prob]["ASDShellQ4"]:
            assert abs(x - 1.0) < 0.003
    for prob in ("plate_thin", "plate_thick"):
        assert table[prob]["ASDShellQ4"] == pytest.approx(
            table[prob]["ShellMITC4"], rel=1e-9)


def test_benchmark_kirchhoff_elements_miss_shear_deformation(table):
    """Thick plate (a/t = 10): Kirchhoff elements stall ~5 % low (the
    Mindlin shear term is 5.2 %), Mindlin elements converge."""
    for e in ("ShellDKGQ", "ShellDKGT"):
        assert -5.6 < (table["plate_thick"][e][-1] - 1) * 100 < -4.5
    for e in ("ShellMITC4", "ASDShellQ4", "ASDShellT3"):
        r = table["plate_thick"][e]
        assert abs(r[-1] - 1) < 0.01 and abs(r[-1] - 1) < abs(r[0] - 1)


# --------------------------------------------------------------------------- #
# 3. one-way slab distribution
# --------------------------------------------------------------------------- #
def _region(corners, d):
    return ShellRegion("S1", "slab", "membrane", "", corners,
                       distribution="one_way", one_way_dir=d)


def test_one_way_profiles_rectangle_hand_check():
    """6 x 4 rectangle, edge 0 along X.  dir 1 (span X): edges 1, 3 (the
    Y-direction edges) get the uniform q * Lx / 2 = 3 kN/m per kPa (total
    12 each = A/2); edges 0, 2 nothing.  dir 2 (span Y): edges 0, 2 get
    q * Ly / 2 = 2."""
    R = [(0, 0, 0), (6, 0, 0), (6, 4, 0), (0, 4, 0)]
    pr, pair = ow.edge_profiles(_region(R, 1))
    assert pair == (1, 3)
    assert pr[1] == [(0.0, 3.0), (4.0, 3.0)] == pr[3]
    assert ow._integral(pr[0]) == ow._integral(pr[2]) == 0.0
    pr, pair = ow.edge_profiles(_region(R, 2))
    assert pair == (0, 2)
    assert pr[0] == [(0.0, 2.0), (6.0, 2.0)] == pr[2]


def test_one_way_profiles_parallelogram_and_trapezoid():
    """Parallelogram (0,0)(6,0)(8,4)(2,4):
    dir 1 (span X, every strip 6 long): slanted edges (length sqrt 20)
      carry w = (6/2) * |t.p| = 3 * 4/sqrt(20) = 2.68328 uniform, 12 each.
    dir 2 (span Y): edge 0 (y=0), x in [0,2]: strips end on unsupported
      edge 3 -> whole strip 2x to edge 0; x in [2,6]: half of 4 = 2 ->
      profile (0,0)-(2,4) | step | (2,2)-(6,2); total 4 + 8 = 12 = A/2.
    Trapezoid (0,0)(6,0)(5,4)(1,4), dir 1: strip length 6 - y/2, edge 1
      w = (6 - y/2)/2 * 4/sqrt(17); total (6+4)/2*4/2 = 10 each."""
    P = [(0, 0, 0), (6, 0, 0), (8, 4, 0), (2, 4, 0)]
    pr, pair = ow.edge_profiles(_region(P, 1))
    assert pair == (1, 3)
    for k in (1, 3):
        assert all(w == pytest.approx(12 / math.sqrt(20)) for _, w in pr[k])
        assert ow._integral(pr[k]) == pytest.approx(12.0)
    pr, pair = ow.edge_profiles(_region(P, 2))
    assert pair == (0, 2)
    assert pr[0] == pytest.approx([(0, 0), (2, 4), (2, 2), (6, 2)])
    assert ow._integral(pr[0]) == pytest.approx(12.0)
    assert ow._integral(pr[2]) == pytest.approx(12.0)
    T = [(0, 0, 0), (6, 0, 0), (5, 4, 0), (1, 4, 0)]
    pr, pair = ow.edge_profiles(_region(T, 1))
    tp = 4 / math.sqrt(17)
    assert pr[1][0][1] == pytest.approx(3.0 * tp)
    assert pr[1][-1][1] == pytest.approx(2.0 * tp)
    assert ow._integral(pr[1]) == pytest.approx(10.0)


def test_one_way_validation_and_serialization():
    mdl = new_model("v", [3.0])
    mdl.add_shell_section(ShellSection("SH", "CONC", 0.2))
    r = mdl.add_shell("slab", "membrane", "", [(0, 0, 3), (6, 0, 3),
                                                (6, 4, 3), (0, 4, 3)])
    assert r.distribution == "two_way" and r.one_way_dir == 1
    assert "distribution" not in r.to_dict()
    r.distribution, r.one_way_dir = "one_way", 2
    d = mdl.to_dict()
    assert d["shells"][0]["distribution"] == "one_way"
    assert d["shells"][0]["one_way_dir"] == 2
    back = BuildingModel.from_dict(d)
    assert (back.shells[0].distribution, back.shells[0].one_way_dir) == \
        ("one_way", 2)
    bad = [ShellRegion("X", "slab", "shell", "SH",
                       [(0, 0, 3), (6, 0, 3), (6, 4, 3), (0, 4, 3)],
                       distribution="one_way"),
           ShellRegion("X", "slab", "membrane", "",
                       [(0, 0, 3), (6, 0, 3), (6, 4, 3), (3, 6, 3),
                        (0, 4, 3)], distribution="one_way"),
           ShellRegion("X", "slab", "membrane", "",
                       [(0, 0, 3), (6, 0, 3), (6, 4, 3), (0, 4, 3)],
                       distribution="one_way", one_way_dir=3),
           ShellRegion("X", "slab", "membrane", "",
                       [(0, 0, 3), (6, 0, 3), (6, 4, 3), (0, 4, 3)],
                       distribution="three_way"),
           ShellRegion("X", "slab", "membrane", "",
                       [(0, 0, 3), (6, 0, 3), (2, 1, 3), (0, 4, 3)],
                       distribution="one_way")]
    for b in bad:
        with pytest.raises(ValueError):
            mdl._validate_shell(b)


def _four_beam_slab(ax, ay, zs, distribution, d, release=True):
    mdl = new_model("slab-oneway", [zs])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))
    mdl.add_section(FrameSection.rectangular("BM", "CONC", 0.3, 0.5))
    cpts = [(0, 0), (ax, 0), (ax, ay), (0, ay)]
    for i, (x, y) in enumerate(cpts):
        mdl.add_member("column", "COL", (x, y, 0), (x, y, zs),
                       story="Story1", uid=f"C{i+1}")
        mdl.supports.append(PointSupport((x, y, 0), (1, 1, 1, 1, 1, 1)))
    names = ["XB1", "YB1", "XB2", "YB2"]   # X-running, Y-running, ...
    for i in range(4):
        x1, y1 = cpts[i]
        x2, y2 = cpts[(i + 1) % 4]
        m = mdl.add_member("beam", "BM", (x1, y1, zs), (x2, y2, zs),
                           story="Story1", uid=names[i])
        if release:
            m.releases = "Mi,Mj"
    r = mdl.add_shell("slab", "membrane", "",
                      [(0, 0, zs), (ax, 0, zs), (ax, ay, zs), (0, ay, zs)],
                      story="Story1")
    r.distribution, r.one_way_dir = distribution, d
    mdl._validate_shell(r)
    return mdl, cpts


def test_one_way_mesh_records():
    """8 x 4 slab one-way along X: 100 % of q A goes to YB1 / YB2 (the two
    beams that RUN in Y, i.e. the supports the X-span crosses) as uniform
    w = q Lx / 2 = 4 kN/m per kPa over the full 4 m; XB1/XB2 get nothing.
    Default two_way is untouched (identical records to a fresh model)."""
    mdl, _ = _four_beam_slab(8.0, 4.0, 3.0, "one_way", 1)
    loads = mesh_model(mdl).membrane_loads["S1"]
    by = {}
    for tl in loads:
        by.setdefault(tl.member_uid, []).append(tl)
    assert set(by) == {"YB1", "YB2"}
    for uid in ("YB1", "YB2"):
        tot = sum(0.5 * (t.w1 + t.w2) * (t.b - t.a) * 4.0 for t in by[uid])
        assert tot == pytest.approx(16.0, rel=1e-12)       # A/2 = 16 m^2
        for t in by[uid]:
            assert t.w1 == pytest.approx(4.0) and t.w2 == pytest.approx(4.0)
    mdl2, _ = _four_beam_slab(8.0, 4.0, 3.0, "one_way", 2)
    by2 = {tl.member_uid for tl in mesh_model(mdl2).membrane_loads["S1"]}
    assert by2 == {"XB1", "XB2"}
    m_two, _ = _four_beam_slab(8.0, 4.0, 3.0, "two_way", 1)
    two = mesh_model(m_two).membrane_loads["S1"]
    assert {t.member_uid for t in two} == {"XB1", "XB2", "YB1", "YB2"}


@pytest.mark.parametrize("d", [1, 2])
def test_one_way_slab_engine_hand_check(d):
    """8 x 4 m membrane slab, q = 5 kPa, edge beams released at both ends
    (simply supported between the corner columns), corner columns fixed.

    one_way_dir = 1 (span X): YB1/YB2 (L = 4) carry w = q*8/2 = 20 kN/m:
      V = +-40 kN at the ends, M_mid = w L^2 / 8 = 40 kN*m; XB1/XB2 carry
      NOTHING (V = M = 0).
    one_way_dir = 2 (span Y): XB1/XB2 (L = 8) carry w = q*4/2 = 10 kN/m:
      V = +-40, M_mid = 10*64/8 = 80; YB beams nothing.
    Every corner column: 40 kN (one loaded beam end) -> base FZ 160 = q A.
    """
    q, ax, ay, zs = 5.0, 8.0, 4.0, 3.0
    mdl, cpts = _four_beam_slab(ax, ay, zs, "one_way", d)
    mdl.pattern("Q", "dead").area_loads.append(
        model_mod.AreaLoad(region_uid="S1", q=q))
    mdl.add_case("Q", {"Q": 1.0})
    finish(mdl, (0, 0, zs))
    res = run(mdl)
    case = res["cases"]["Q"]
    assert case["base"]["FZ"] == pytest.approx(q * ax * ay, rel=1e-9)
    for (x, y) in cpts:
        assert case["reactions"][find_node(res, (x, y, 0))][2] == \
            pytest.approx(40.0, rel=1e-6)
    loaded, empty = ((("YB1", "YB2"), ("XB1", "XB2")) if d == 1
                     else (("XB1", "XB2"), ("YB1", "YB2")))
    L, w = (ay, q * ax / 2) if d == 1 else (ax, q * ay / 2)
    for uid in loaded:
        s = stations(res, "Q", uid)
        V = np.asarray(s["V2"], float)
        M = np.asarray(s["M3"], float)
        assert abs(V[0] - V[-1]) == pytest.approx(w * L, rel=1e-6)
        assert abs(M[5]) == pytest.approx(w * L * L / 8, rel=1e-6)
    for uid in empty:
        s = stations(res, "Q", uid)
        assert np.max(np.abs(s["V2"])) < 1e-6 * w * L
        assert np.max(np.abs(s["M3"])) < 1e-6 * w * L * L


def test_one_way_missing_support_beam_goes_to_corners():
    """Remove YB2: its half (q A / 2) lands on the two corner nodes of that
    edge as nodal loads; total conserved."""
    mdl, _ = _four_beam_slab(8.0, 4.0, 3.0, "one_way", 1)
    mdl.members = [m for m in mdl.members if m.uid != "YB2"]
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        mesh = mesh_model(mdl)
    assert any("not fully covered" in str(x.message) for x in w)
    nodal = mesh.membrane_nodal["S1"]
    assert sorted(nodal.values()) == pytest.approx([8.0, 8.0])
    tot = sum(0.5 * (t.w1 + t.w2) * (t.b - t.a) * 4.0
              for t in mesh.membrane_loads["S1"])
    assert tot + sum(nodal.values()) == pytest.approx(32.0, rel=1e-12)


def test_shell_type_section_on_engine_default_byte_identical():
    """shell_type = "shell" set explicitly == default (same calls)."""
    a = run(_wall_model("shell")[0])
    m, _ = _wall_model("shell")
    m.shell_sections["SH"].shell_type = "shell"
    b = run(m)
    assert a["cases"] == b["cases"]


@pytest.mark.parametrize("t", ["membrane", "shell_thick"])
def test_frame_into_membrane_wall_junction(t):
    """A beam framing perpendicular into the top of a wall (4 x 3, fixed
    base) and pinned at its far end (L = 4), point load P = 20 kN at
    midspan.  A membrane wall has no out-of-plane bending, so the junction
    acts as a pin: M at the wall end ~ 0 and the beam is simply supported
    (M_mid = P L / 4 = 20, far reaction P/2 = 10) — and the junction is
    NOT singular (the ASDShellQ4 drilling DOF and the residual bending keep
    every DOF stiff).  A shell_thick wall restrains the end (|M_end| > 0,
    M_mid < P L / 4)."""
    mdl = new_model("j", [3.0])
    mdl.add_section(FrameSection.rectangular("BM", "CONC", 0.3, 0.5))
    mdl.add_shell_section(ShellSection("SH", "CONC", 0.2, shell_type=t))
    mdl.add_shell("wall", "shell", "SH",
                  [(0, 0, 0), (4, 0, 0), (4, 0, 3), (0, 0, 3)],
                  mesh_size=1.0, story="Story1")
    for x in range(5):
        mdl.supports.append(PointSupport((x, 0, 0), (1,) * 6))
    mdl.add_member("beam", "BM", (2, 0, 3), (2, 4, 3), story="Story1",
                   uid="B1")
    mdl.supports.append(PointSupport((2, 4, 3), (1, 1, 1, 0, 0, 0)))
    pat = mdl.pattern("P", "dead")
    pat.member_loads.append(model_mod.MemberLoad("B1", kind="point",
                                                 w=20.0, a=0.5))
    mdl.add_case("P", {"P": 1.0})
    finish(mdl, (2, 4, 3))
    res = run(mdl)
    assert res["case_status"]["P"] == "finished", res.get("warning")
    s = stations(res, "P", "B1")
    M = np.asarray(s["M3"], float)
    far = res["cases"]["P"]["reactions"][find_node(res, (2, 4, 3))][2]
    if t == "membrane":
        assert abs(M[0]) < 1e-3 * 20.0
        assert abs(M[5]) == pytest.approx(20.0, rel=1e-3)
        assert far == pytest.approx(10.0, rel=1e-3)
    else:
        assert abs(M[0]) > 1.0
        assert abs(M[5]) < 20.0 - 0.5


def test_polygon_slab_shell_thick_triangles_report_forces():
    """A pentagon slab (auto-meshed quads + triangles) with shell_thick:
    ASDShellT3 triangles report Gauss-averaged resultants (3 x 8 values)
    alongside the quads, and vertical equilibrium is exact."""
    mdl = new_model("poly", [3.0])
    mdl.add_shell_section(ShellSection("SH", "CONC", 0.2,
                                       shell_type="shell_thick"))
    corners = [(0, 0, 0), (4, 0, 0), (4, 3, 0), (2, 4.5, 0), (0, 3, 0)]
    mdl.add_shell("slab", "shell", "SH", corners, mesh_size=1.0,
                  story="Story1")
    for (x, y, z) in corners:
        mdl.supports.append(PointSupport((x, y, z), (1, 1, 1, 0, 0, 0)))
    mdl.pattern("Q", "dead").area_loads.append(
        model_mod.AreaLoad(region_uid="S1", q=2.0))
    mdl.add_case("Q", {"Q": 1.0})
    finish(mdl, (4, 0, 0))
    res = run(mdl)
    assert res["case_status"]["Q"] == "finished", res.get("warning")
    case = res["cases"]["Q"]
    area = mdl.shells[0].area
    assert case["base"]["FZ"] == pytest.approx(2.0 * area, rel=1e-9)
    n_tri = sum(1 for q in res["shell_quads"] if len(q["nodes"]) == 3)
    assert n_tri > 0
    assert len(case["shell_forces"]) == len(res["shell_quads"])
