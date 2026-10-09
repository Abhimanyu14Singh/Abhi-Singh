"""Nonprismatic frame sections (ETABS Define > Frame Sections >
Nonprismatic) and per-joint panel zones (ETABS Assign > Joint > Panel
Zone) — skyframe.core.nonprismatic / skyframe.core.panelzones.

Hand / independent references:
* haunched cantilever tip deflection = virtual work integral
  int P (L-x)^2 / (E I(x)) dx (fine midpoint quadrature);
* tapered pinned-pinned column: Euler with I(x), ``EI(x) y'' + P y = 0``
  solved by an independent finite-difference eigenproblem;
* weight = unit_weight * int A(x) dx (A piecewise linear);
* scissors spring rotation = M / K.
"""

from __future__ import annotations

import json
import math
import warnings

import numpy as np
import pytest

from skyframe.core import nonprismatic as npx
from skyframe.core import panelzones as pzj
from skyframe.core.buckling import buckling_analysis
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 MemberLoad, NodalLoad, PointSupport,
                                 ThermalLoad)
from skyframe.engine.opensees_engine import (OpenSeesEngine,
                                             compute_panel_zone_springs)

E = 2.5e7          # kPa
GAMMA = 24.0       # kN/m^3
B = 0.3
H0, H1 = 0.8, 0.4  # haunch: deep at the support, shallow at the tip
L = 4.0
Z = 3.0
FIX = (1, 1, 1, 1, 1, 1)


def _I(h, b=B):
    return b * h ** 3 / 12.0


def _base(name="np"):
    m = BuildingModel(name)
    m.add_material(Material("C", E, 0.2, GAMMA))
    m.add_section(FrameSection.rectangular("D", "C", B, H0))
    m.add_section(FrameSection.rectangular("S", "C", B, H1))
    m.set_stories([Z])
    m.rigid_diaphragms = False
    m.num_modes = 0
    return m


def _cantilever(segments=None, n=None, P=10.0, section="NP", **mkw):
    m = _base()
    if section == "NP":
        kw = {} if n is None else {"subdivisions": n}
        npx.add_nonprismatic_section(
            m, "NP", segments or [{"start_section": "D", "end_section": "S",
                                   "EI33": "cubic"}], **kw)
    m.add_member("beam", section, (0, 0, Z), (L, 0, Z), story="Story1",
                 uid="B", **mkw)
    m.supports.append(PointSupport((0, 0, Z), FIX))
    pat = m.pattern("P", "live")
    pat.nodal_loads.append(NodalLoad((L, 0, Z), fz=-P))
    m.add_case("P", {"P": 1.0})
    m.validate()
    return m


def _vw_tip(P=10.0, n=3, x_lo=0.0, N=200000):
    """int_{x_lo}^{L} P (L-x)^2 / (E I(x)) dx, I^(1/n) linear D -> S."""
    I0, I1 = _I(H0), _I(H1)
    x = x_lo + (np.arange(N) + 0.5) * (L - x_lo) / N
    s = x / L
    Ix = (I0 ** (1 / n) + s * (I1 ** (1 / n) - I0 ** (1 / n))) ** n
    return float(np.sum(P * (L - x) ** 2 / (E * Ix)) * (L - x_lo) / N)


def _tip(m, P_case="P"):
    eng = OpenSeesEngine(m)
    r = eng.run_static(P_case)
    t = eng._find_node(eng._asm, (L, 0, Z))
    return eng, r, -r.node_disp[t][2]


# =========================================================================== #
# nonprismatic: geometry / interpolation
# =========================================================================== #
def test_layout_absolute_and_relative_lengths():
    m = _base()
    npx.add_nonprismatic_section(m, "NP", [
        {"start_section": "D", "end_section": "S", "length": 1.0,
         "length_type": "absolute"},
        {"start_section": "S", "end_section": "S", "length": 1.0},
        {"start_section": "S", "end_section": "D", "length": 2.0}])
    b = m.add_member("beam", "NP", (0, 0, Z), (L, 0, Z), uid="B")
    lay = npx.layout(m, b)
    # 1 m absolute, the remaining 3 m shared 1 : 2 by the relative segments
    assert [(s["x0"], s["x1"]) for s in lay] == pytest.approx(
        [(0.0, 1.0), (1.0, 2.0), (2.0, 4.0)])
    # cuts: every boundary + 16 subdivisions of each VARYING segment only
    cuts = npx.sub_boundaries(m, b)
    assert len(cuts) == 15 + 1 + 1 + 15
    assert 1.0 in cuts and 2.0 in cuts
    assert not any(1.0 < x < 2.0 for x in cuts)      # uniform segment
    # representative props = the i-end section
    sec = m.sections["NP"]
    assert (sec.A, sec.I33, sec.h, sec.material) == (
        m.sections["D"].A, m.sections["D"].I33, H0, "C")


def test_variation_exponents_linear_parabolic_cubic():
    m = _base()
    for var in ("linear", "parabolic", "cubic"):
        npx.add_nonprismatic_section(m, f"NP_{var}", [
            {"start_section": "D", "end_section": "S", "EI33": var,
             "EI22": "linear"}])
        m.add_member("beam", f"NP_{var}", (0, 0, Z), (L, 0, Z),
                     uid=f"B_{var}")
    I0, I1 = _I(H0), _I(H1)
    A0, A1 = B * H0, B * H1
    J0, J1 = m.sections["D"].J, m.sections["S"].J
    for var, n in (("linear", 1), ("parabolic", 2), ("cubic", 3)):
        mem = next(x for x in m.members if x.uid == f"B_{var}")
        A, I22, I33, J = npx.props_at(m, mem, 0.25 * L)
        assert A == pytest.approx(A0 + 0.25 * (A1 - A0), rel=1e-14)
        assert J == pytest.approx(J0 + 0.25 * (J1 - J0), rel=1e-14)
        hand = (I0 ** (1 / n) + 0.25 * (I1 ** (1 / n) - I0 ** (1 / n))) ** n
        assert I33 == pytest.approx(hand, rel=1e-13)
        i22_0, i22_1 = m.sections["D"].I22, m.sections["S"].I22
        assert I22 == pytest.approx(i22_0 + 0.25 * (i22_1 - i22_0),
                                    rel=1e-14)
    # cubic on a linear depth taper reproduces b h(x)^3 / 12 exactly
    mem = next(x for x in m.members if x.uid == "B_cubic")
    h = H0 + 0.6 * (H1 - H0)
    assert npx.props_at(m, mem, 0.6 * L)[2] == pytest.approx(_I(h), rel=1e-13)


# =========================================================================== #
# nonprismatic: statics / deflection vs virtual work
# =========================================================================== #
def test_haunched_cantilever_tip_deflection_default_within_half_percent():
    m = _cantilever()
    eng, r, d = _tip(m)
    ref = _vw_tip()
    assert len(eng._mesh.segments["B"]) == npx.DEFAULT_SUBDIVISIONS
    assert abs(d - ref) / ref < 0.005
    # stitched per drawn member: end forces + 11 exact statics stations
    st = r.member_stations["B"]
    assert len(st["x"]) == 11
    for x, M in zip(st["x"], st["M3"]):
        assert abs(M) == pytest.approx(10.0 * (L - x), abs=1e-7)
    assert abs(r.member_forces["B"][5]) == pytest.approx(10.0 * L, rel=1e-9)
    # exact deflection stations agree with the tip node
    assert -r.member_deflections["B"]["dy"][-1] == pytest.approx(d,
                                                                 rel=1e-9) \
        or -r.member_deflections["B"]["dz"][-1] == pytest.approx(d, rel=1e-9)


def test_haunched_cantilever_converges_second_order():
    ref = _vw_tip()
    errs = []
    for n in (2, 4, 8, 16, 32):
        _, _, d = _tip(_cantilever(n=n))
        errs.append((d - ref) / ref)
    assert all(e > 0 for e in errs)                  # midpoint I: stiff side
    for a, b in zip(errs, errs[1:]):
        assert 3.5 < a / b < 4.5                      # O(h^2)
    assert errs[-1] < 5e-4


def test_parabolic_variation_cantilever_matches_virtual_work():
    segs = [{"start_section": "D", "end_section": "S", "EI33": "parabolic"}]
    _, _, d = _tip(_cantilever(segments=segs, n=32))
    assert d == pytest.approx(_vw_tip(n=2), rel=1e-3)


def test_uniform_nonprismatic_equals_prismatic_exactly():
    """start == end section: never divided, byte-identical results."""
    def run(section, segs=None):
        m = _base()
        if section == "NP":
            npx.add_nonprismatic_section(m, "NP", segs)
        m.add_member("beam", section, (0, 0, Z), (L, 0, Z), story="Story1",
                     uid="B", releases="Mj")
        m.add_member("column", section, (L, 0, 0), (L, 0, Z),
                     story="Story1", uid="C")
        m.supports.append(PointSupport((0, 0, Z), FIX))
        m.supports.append(PointSupport((L, 0, 0), FIX))
        pat = m.pattern("SW", "dead")
        pat.self_weight_factor = 1.0
        pat.member_loads.append(MemberLoad("B", "point", 7.0, 0.0, 0.3,
                                           1.0))
        pat.member_loads.append(MemberLoad("B", "trapezoid", 2.0, 5.0, 0.1,
                                           0.8))
        pat.thermal_loads.append(ThermalLoad("C", 15.0))
        m.add_case("SW", {"SW": 1.0})
        m.validate()
        eng = OpenSeesEngine(m)
        return eng, json.dumps(eng.run_static("SW").to_dict(),
                               sort_keys=True)
    _, ref = run("S")
    eng1, one = run("NP", [{"start_section": "S", "end_section": "S"}])
    eng2, two = run("NP", [{"start_section": "S", "end_section": "S",
                            "length": 1.0},
                           {"start_section": "S", "end_section": "S",
                            "length": 3.0, "EI33": "cubic"}])
    assert one == ref
    assert two == ref
    assert len(eng1._mesh.segments["B"]) == 1
    assert len(eng2._mesh.segments["B"]) == 1


# =========================================================================== #
# nonprismatic: weight / mass integrate the varying area
# =========================================================================== #
def _sw_model(mass_mode="weight"):
    m = _base()
    npx.add_nonprismatic_section(m, "NP", [
        {"start_section": "D", "end_section": "S", "length": 1.5,
         "length_type": "absolute", "EI33": "cubic"},
        {"start_section": "S", "end_section": "S", "length": 1.0},
        {"start_section": "S", "end_section": "D", "length": 1.0,
         "EI33": "cubic"}])
    m.add_member("beam", "NP", (0, 0, Z), (L, 0, Z), story="Story1",
                 uid="B")
    m.supports.append(PointSupport((0, 0, Z), FIX))
    pat = m.pattern("SW", "dead")
    pat.self_weight_factor = 1.0
    m.add_case("SW", {"SW": 1.0})
    m.mass_source = {"SW": 1.0}
    m.mass_source_mode = mass_mode
    m.validate()
    return m


def _int_A():
    A0, A1 = B * H0, B * H1
    rem = L - 1.5
    return (0.5 * (A0 + A1) * 1.5 + A1 * rem / 2.0
            + 0.5 * (A1 + A0) * rem / 2.0)


def test_self_weight_equals_integral_of_area():
    m = _sw_model()
    mem = m.members[0]
    assert npx.area_integral(m, mem) == pytest.approx(_int_A(), rel=1e-14)
    r = OpenSeesEngine(m).run_static("SW")
    W = GAMMA * _int_A()
    assert r.base["FZ"] == pytest.approx(W, rel=1e-8)
    # cantilever fixed-end moment = int w(x) x dx (exact trapezoids)
    xs = np.linspace(0.0, L, 400001)
    A0, A1 = B * H0, B * H1

    def A_of(x):
        if x <= 1.5:
            return A0 + (A1 - A0) * x / 1.5
        if x <= 2.75:
            return A1
        return A1 + (A0 - A1) * (x - 2.75) / 1.25
    w = GAMMA * np.array([A_of(x) for x in xs])
    f = w * xs
    M_hand = float(np.sum(0.5 * (f[1:] + f[:-1]) * np.diff(xs)))
    assert abs(r.member_forces["B"][5]) == pytest.approx(M_hand, rel=1e-6)


def test_story_and_element_self_mass_integrate_area():
    m = _sw_model("weight")
    masses = m.compute_story_masses()
    assert masses["Story1"] == pytest.approx(GAMMA * _int_A() / 9.80665,
                                             rel=1e-10)
    m2 = _sw_model("element_self_mass")
    eng = OpenSeesEngine(m2)
    asm = eng._build()
    added = {}

    def add(t, dof, v):
        added[(t, dof)] = added.get((t, dof), 0.0) + v
    eng._add_element_self_mass(asm, add)
    rho = m2.materials["C"].mass_per_volume
    # every node's mass (incl. the fixed support) sums to rho * int A
    for dof in (1, 2, 3):
        tot = sum(v for (t, d), v in added.items() if d == dof)
        assert tot == pytest.approx(rho * _int_A(), rel=1e-12)
    # and follows the area: the free (deep) end node carries half of its
    # last sub-element, rho * A(mid) * l / 2
    nL = eng._find_node(asm, (L, 0, Z))
    l_sub = 1.25 / 16
    A_mid = B * H1 + (B * H0 - B * H1) * (1.0 - 0.5 / 16)
    assert added[(nL, 3)] == pytest.approx(0.5 * rho * A_mid * l_sub,
                                           rel=1e-12)


# =========================================================================== #
# nonprismatic: releases / offsets / thermal at the drawn ends
# =========================================================================== #
def test_releases_act_at_drawn_ends_only():
    """Haunched beam fixed-fixed supports with Mi,Mj released = simply
    supported: M(mid) = wL^2/8 whatever EI(x); interior sub-element joints
    stay continuous."""
    m = _base()
    npx.add_nonprismatic_section(m, "NP", [
        {"start_section": "D", "end_section": "S", "EI33": "cubic"},
        {"start_section": "S", "end_section": "D", "EI33": "cubic"}])
    m.add_member("beam", "NP", (0, 0, Z), (L, 0, Z), story="Story1",
                 uid="B", releases="Mi,Mj")
    for p in ((0, 0, Z), (L, 0, Z)):
        m.supports.append(PointSupport(p, (1, 1, 1, 1, 0, 0)))
    m.pattern("Q", "live").member_loads.append(
        MemberLoad("B", "udl", 5.0, 0.0, 0.0, 1.0))
    m.add_case("Q", {"Q": 1.0})
    m.validate()
    eng = OpenSeesEngine(m)
    r = eng.run_static("Q")
    assert len(eng._mesh.segments["B"]) == 32
    f = r.member_forces["B"]
    assert abs(f[5]) < 1e-8 and abs(f[11]) < 1e-8
    st = r.member_stations["B"]
    assert abs(st["M3"][5]) == pytest.approx(5.0 * L ** 2 / 8.0, rel=1e-9)
    # the symmetric haunch deflects symmetrically
    d = r.member_deflections["B"]
    v = d["dy"] if max(map(abs, d["dy"])) > 0 else d["dz"]
    assert v[2] == pytest.approx(v[8], rel=1e-8)


def test_rigid_end_offset_on_drawn_end_of_nonprismatic_member():
    m = _cantilever(n=32, rigid_i=0.1)
    eng, _, d = _tip(m)
    assert eng._member_offsets(m.members[0]) == (0.1, 0.0)
    assert d < _vw_tip()
    # rigid arm over [0, 0.1]: virtual work over the clear span only
    assert d == pytest.approx(_vw_tip(x_lo=0.1), rel=2e-3)


def test_thermal_free_elongation_and_axial_force():
    m = _cantilever(n=8)
    m.patterns["P"].thermal_loads.append(ThermalLoad("B", 30.0))
    m.patterns["P"].nodal_loads.clear()
    eng = OpenSeesEngine(m)
    r = eng.run_static("P")
    t = eng._find_node(eng._asm, (L, 0, Z))
    alpha = m.thermal_alpha
    assert r.node_disp[t][0] == pytest.approx(alpha * 30.0 * L, rel=1e-9)
    assert max(abs(v) for v in r.member_stations["B"]["N"]) < 1e-6


# =========================================================================== #
# nonprismatic: buckling (Euler with I(x))
# =========================================================================== #
def _fd_tapered_euler(EI_of, Lc, N=4000):
    """Smallest P of EI(x) y'' + P y = 0, y(0) = y(L) = 0 (pinned-pinned)
    by central finite differences: -D2 y = P diag(1/EI) y."""
    h = Lc / N
    x = np.arange(1, N) * h
    main = np.full(N - 1, 2.0 / h ** 2)
    off = np.full(N - 2, -1.0 / h ** 2)
    # symmetric form: S^-1/2 ... use EI^(1/2) scaling
    s = np.sqrt(np.array([EI_of(xi) for xi in x]))
    Kmat = (np.diag(main) + np.diag(off, 1) + np.diag(off, -1))
    Kmat = (s[:, None] * Kmat) * s[None, :]
    return float(np.linalg.eigvalsh(Kmat)[0])


def test_tapered_column_buckling_matches_euler_with_varying_I():
    Hc = 6.0
    m = BuildingModel("col")
    m.add_material(Material("C", E, 0.2, GAMMA))
    # wide b so the strong (I22) axis never governs; depth 0.3 -> 0.6
    m.add_section(FrameSection.rectangular("BOT", "C", 1.0, 0.3))
    m.add_section(FrameSection.rectangular("TOP", "C", 1.0, 0.6))
    npx.add_nonprismatic_section(m, "TAP", [
        {"start_section": "BOT", "end_section": "TOP", "EI33": "cubic",
         "EI22": "linear"}])
    m.set_stories([Hc])
    m.rigid_diaphragms = False
    m.add_member("column", "TAP", (0, 0, 0), (0, 0, Hc), story="Story1",
                 uid="C")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 0, 0, 1)))
    m.supports.append(PointSupport((0, 0, Hc), (1, 1, 0, 0, 0, 0)))
    m.pattern("G", "dead").nodal_loads.append(NodalLoad((0, 0, Hc), fz=-1.0))
    m.add_case("G", {"G": 1.0})
    m.validate()
    res = buckling_analysis(m, {"G": 1.0}, num_modes=1)
    Pcr = res.factors[0]

    def EI(x):
        h = 0.3 + 0.3 * x / Hc
        return E * 1.0 * h ** 3 / 12.0
    ref = _fd_tapered_euler(EI, Hc)
    assert Pcr == pytest.approx(ref, rel=0.005)
    # bounded by the uniform columns of the end sections
    lo = math.pi ** 2 * EI(0.0) / Hc ** 2
    hi = math.pi ** 2 * EI(Hc) / Hc ** 2
    assert lo < Pcr < hi
    # internal sub-element nodes take part in the mode
    assert len(res.modes[1]) == 1 + npx.DEFAULT_SUBDIVISIONS


def test_buckling_uniform_nonprismatic_identical_to_prismatic():
    def run(sec):
        m = BuildingModel("col")
        m.add_material(Material("C", E, 0.2, GAMMA))
        m.add_section(FrameSection.rectangular("S", "C", 0.4, 0.4))
        if sec == "NP":
            npx.add_nonprismatic_section(
                m, "NP", [{"start_section": "S", "end_section": "S"}])
        m.set_stories([3.0])
        m.add_member("column", sec, (0, 0, 0), (0, 0, 3.0), uid="C")
        m.pattern("G", "dead").nodal_loads.append(
            NodalLoad((0, 0, 3.0), fz=-1.0))
        return buckling_analysis(m, {"G": 1.0}, num_modes=2).to_dict()
    assert json.dumps(run("S")) == json.dumps(run("NP"))


# =========================================================================== #
# nonprismatic: JSON / validation
# =========================================================================== #
def test_json_roundtrip_and_prismatic_files_unchanged():
    m = _sw_model()
    d = m.to_dict()
    assert "kind" not in d["sections"]["D"]          # old files unchanged
    assert d["sections"]["NP"]["kind"] == "nonprismatic"
    assert d["sections"]["NP"]["subdivisions"] == 16
    back = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert back.sections["NP"].segments == m.sections["NP"].segments
    assert back.to_dict() == d
    # hand-written JSON may omit the representative fields
    sd = {"kind": "nonprismatic", "subdivisions": 4,
          "segments": [{"start_section": "S", "end_section": "D"}]}
    d2 = json.loads(json.dumps(d))
    d2["sections"] = {"NP": sd, "D": d["sections"]["D"],
                      "S": d["sections"]["S"]}
    back2 = BuildingModel.from_dict(d2)
    assert back2.sections["NP"].material == "C"
    assert back2.sections["NP"].A == pytest.approx(B * H1)
    assert len(npx.sub_boundaries(back2, back2.members[0])) == 3


@pytest.mark.parametrize("segs, match", [
    ([{"start_section": "D", "end_section": "X"}], "unknown section"),
    ([{"start_section": "D", "end_section": "S", "EI33": "quartic"}], "EI33"),
    ([{"start_section": "D", "end_section": "S", "length": -1}], "length"),
    ([{"start_section": "D", "end_section": "S", "bogus": 1}], "unknown key"),
    ([], "non-empty"),
    ([{"start_section": "D", "end_section": "STL"}], "one material"),
])
def test_section_validation_errors(segs, match):
    m = _base()
    m.add_material(Material("STEEL", 2e8, 0.3, 77.0))
    m.add_section(FrameSection.rectangular("STL", "STEEL", 0.1, 0.1))
    with pytest.raises(ValueError, match=match):
        npx.add_nonprismatic_section(m, "NP", segs)
    assert "NP" not in m.sections


def test_member_validation_errors():
    m = _base()
    npx.add_nonprismatic_section(m, "NP", [
        {"start_section": "D", "end_section": "S", "length": 5.0,
         "length_type": "absolute"}])
    m.add_member("beam", "NP", (0, 0, Z), (L, 0, Z), uid="B")
    with pytest.raises(ValueError, match="exceed"):
        m.validate()
    m.sections["NP"].segments[0]["length"] = 3.0
    with pytest.raises(ValueError, match="sum to the member length"):
        m.validate()
    m.sections["NP"].segments[0]["length_type"] = "relative"
    m.validate()
    m.members[0].axial_limit = "tension"
    with pytest.raises(ValueError, match="axial-only"):
        m.validate()
    m.members[0].axial_limit = "both"
    m.sections["NP"].shear_deformation = True
    with pytest.raises(ValueError, match="shear deformation"):
        m.validate()


# =========================================================================== #
# per-joint panel zones
# =========================================================================== #
E_C = 2.5e7
COL_B = COL_H = 0.5
BM_B, BM_H = 0.3, 0.6


def _frame(pz="none", overrides=(), bays=1, cantilever=False, P=10.0):
    m = BuildingModel("pz")
    m.rigid_diaphragms = False
    m.num_modes = 0
    m.add_material(Material("CONC", E=E_C, nu=0.2))
    m.add_section(FrameSection.rectangular("COL", "CONC", COL_B, COL_H))
    m.add_section(FrameSection.rectangular("BM", "CONC", BM_B, BM_H))
    m.set_stories([3.0])
    xs = [6.0 * i for i in range(bays + 1)]
    cols = xs[:1] if cantilever else xs
    for i, x in enumerate(cols):
        m.add_member("column", "COL", (x, 0, 0), (x, 0, 3.0),
                     story="Story1", uid=f"C{i + 1}")
        m.supports.append(PointSupport((x, 0, 0), FIX))
    if cantilever:
        m.add_member("beam", "BM", (0, 0, 3.0), (4.0, 0, 3.0),
                     story="Story1", uid="B1")
        m.pattern("H", "other").nodal_loads.append(
            NodalLoad((4.0, 0, 3.0), fz=-P))
    else:
        for i in range(bays):
            m.add_member("beam", "BM", (xs[i], 0, 3.0), (xs[i + 1], 0, 3.0),
                         story="Story1", uid=f"B{i + 1}")
        m.pattern("H", "other").nodal_loads.append(
            NodalLoad((0, 0, 3.0), fx=100.0))
    m.add_case("H", {"H": 1.0})
    m.panel_zones = pz
    m.joint_panel_zones = [dict(e) for e in overrides]
    m.validate()
    return m


def test_no_overrides_is_exactly_the_model_wide_spec():
    for pz in ("none", "rigid", "scissors"):
        m = _frame(pz, bays=2)
        want = compute_panel_zone_springs(m) if pz == "scissors" else {}
        assert pzj.effective_specs(m, compute_panel_zone_springs) == want
        assert "joint_panel_zones" not in m.to_dict()


def test_single_joint_override_equals_model_wide_scissors_exactly():
    """One beam-column joint: model-wide scissors == override from_column
    at that joint (bitwise)."""
    ref = _frame("scissors", cantilever=True)
    ovr = _frame("none", [{"point": [0, 0, 3.0], "property": "from_column"}],
                 cantilever=True)
    e1, e2 = OpenSeesEngine(ref), OpenSeesEngine(ovr)
    r1 = json.dumps(e1.run_static("H").to_dict(), sort_keys=True)
    r2 = json.dumps(e2.run_static("H").to_dict(), sort_keys=True)
    assert r1 == r2
    assert e1.panel_zone_joints() == e2.panel_zone_joints()


def test_override_applies_at_that_joint_only():
    ovr = _frame("none", [{"point": [6.0, 0, 3.0]}], bays=2)
    wide = _frame("scissors", bays=2)
    e_o, e_w = OpenSeesEngine(ovr), OpenSeesEngine(wide)
    e_o.run_static("H")
    e_w.run_static("H")
    jo = e_o.panel_zone_joints()
    jw = {tuple(j["point"]): j["K"] for j in e_w.panel_zone_joints()}
    assert [j["point"] for j in jo] == [[6.0, 0.0, 3.0]]
    assert len(jw) == 3
    assert jo[0]["K"] == pytest.approx(jw[(6.0, 0.0, 3.0)], rel=1e-15)
    # the other joints stay rigidly connected (centerline): their beam
    # ends were not redirected to a panel duplicate
    asm = e_o._asm
    assert {k[0] for k in asm.pz_load_tag} == {"B1", "B2"}
    assert all(asm.node_coords[t][0] == 6.0 for t in asm.pz_load_tag.values())
    # and the drift lies between the centerline and all-scissors models
    def ux(m):
        e = OpenSeesEngine(m)
        r = e.run_static("H")
        return r.node_disp[e._find_node(e._asm, (0, 0, 3.0))][0]
    u_none = ux(_frame("none", bays=2))
    assert u_none < ux(ovr) < ux(wide)


def test_spring_panel_zone_rotation_equals_M_over_k():
    k = 2.0e4
    P, Lb = 10.0, 4.0
    m = _frame("none", [{"point": [0, 0, 3.0], "property": "spring",
                         "k": k}], cantilever=True, P=P)
    eng = OpenSeesEngine(m)
    r = eng.run_static("H")
    (j,) = eng.panel_zone_joints()
    assert j["K"] == k
    do, dd = r.node_disp[j["orig"]], r.node_disp[j["dup"]]
    for c in range(3):
        assert dd[c] == pytest.approx(do[c], abs=1e-15)
    assert abs(dd[4] - do[4]) == pytest.approx(P * Lb / k, rel=1e-8)
    # tip deflection = column + spring + beam flexibility (hand)
    Ib = BM_B * BM_H ** 3 / 12.0
    Ic = COL_B * COL_H ** 3 / 12.0
    M = P * Lb
    th = M * 3.0 / (E_C * Ic) + M / k               # column top + spring
    tip = th * Lb + P * Lb ** 3 / (3 * E_C * Ib)
    t = eng._find_node(eng._asm, (Lb, 0, 3.0))
    assert -r.node_disp[t][2] == pytest.approx(tip, rel=1e-3)


def test_elastic_property_adds_doubler_plate():
    G = E_C / 2.4
    m0 = _frame("none", [{"point": [0, 0, 3.0], "property": "elastic"}],
                cantilever=True)
    m1 = _frame("none", [{"point": [0, 0, 3.0], "property": "elastic",
                          "doubler_t": 0.02}], cantilever=True)
    (s0,) = pzj.override_specs(m0).values()
    (s1,) = pzj.override_specs(m1).values()
    assert s0["K"] == pytest.approx(G * COL_H * BM_H * COL_B, rel=1e-14)
    assert s1["K"] == pytest.approx(G * COL_H * BM_H * (COL_B + 0.02),
                                    rel=1e-14)
    wide = compute_panel_zone_springs(_frame("scissors", cantilever=True))
    assert s0["K"] == list(wide.values())[0]["K"]


def test_rigid_model_wide_keeps_offsets_except_at_overridden_joint():
    m = _frame("rigid", [{"point": [0, 0, 3.0]}], bays=1)
    eng = OpenSeesEngine(m)
    by = {x.uid: x for x in m.members}
    # left joint overridden: no automatic end zones there
    assert eng._member_offsets(by["C1"]) == (0.0, 0.0)
    assert eng._member_offsets(by["B1"]) == (0.0, COL_H / 2.0)
    assert eng._member_offsets(by["C2"]) == (0.0, BM_H / 2.0)
    eng.run_static("H")
    assert [j["point"] for j in eng.panel_zone_joints()] == [[0.0, 0.0, 3.0]]


def test_braces_connectivity_redirects_brace_ends():
    def build(conn):
        m = _frame("none", cantilever=True)
        m.add_member("brace", "BM", (4.0, 0, 0), (0, 0, 3.0),
                     story="Story1", uid="BR")
        m.supports.append(PointSupport((4.0, 0, 0), (1, 1, 1, 0, 0, 0)))
        m.joint_panel_zones = [{"point": [0, 0, 3.0], "property": "spring",
                                "k": 1e5, "connectivity": conn}]
        m.validate()
        eng = OpenSeesEngine(m)
        eng.run_static("H")
        return {k[0] for k in eng._asm.pz_load_tag}
    assert build("beams_to_panel") == {"B1"}
    assert build("braces_to_panel") == {"BR"}
    assert build("beams_and_braces_to_panel") == {"B1", "BR"}


def test_override_at_support_joint_warns_and_is_skipped():
    m = _frame("none", cantilever=True)
    m.add_member("beam", "BM", (0, 0, 0), (-3.0, 0, 0), story="Story1",
                 uid="BB")
    m.supports.append(PointSupport((-3.0, 0, 0), FIX))
    m.joint_panel_zones = [{"point": [0, 0, 0], "property": "spring",
                            "k": 1e4}]
    m.validate()
    eng = OpenSeesEngine(m)
    with pytest.warns(UserWarning, match="support"):
        eng.run_static("H")
    assert eng.panel_zone_joints() == []


@pytest.mark.parametrize("entry, match", [
    ({"point": [0, 0, 3.0], "property": "bogus"}, "property"),
    ({"point": [0, 0, 3.0], "property": "spring"}, "needs k"),
    ({"point": [0, 0, 3.0], "property": "spring", "k": -1.0}, "k must"),
    ({"point": [0, 0, 3.0], "k": 5.0}, "spring' only"),
    ({"point": [0, 0, 3.0], "connectivity": "all"}, "connectivity"),
    ({"point": [9, 9, 9]}, "at least one member"),
    ({"point": [0, 0, 3.0], "property": "from_column",
      "connectivity": "braces_to_panel"}, "at least one member"),
    ({"point": [0, 0]}, r"\[x, y, z\]"),
])
def test_joint_panel_zone_validation(entry, match):
    m = _frame("none", cantilever=True)
    m.joint_panel_zones = [entry]
    with pytest.raises(ValueError, match=match):
        m.validate()


def test_joint_panel_zones_json_roundtrip_and_duplicates():
    m = _frame("scissors", [{"point": [0, 0, 3.0], "property": "spring",
                             "k": 3e4}], bays=1)
    d = m.to_dict()
    assert d["joint_panel_zones"] == [{"point": [0.0, 0.0, 3.0],
                                       "property": "spring", "k": 3e4,
                                       "connectivity": "beams_to_panel"}]
    back = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert back.joint_panel_zones == m.joint_panel_zones
    # override replaces the model-wide spring at that joint only
    specs = pzj.effective_specs(back, compute_panel_zone_springs)
    assert specs[(0.0, 0.0, 3.0)]["K"] == 3e4
    assert specs[(6.0, 0.0, 3.0)]["K"] == pytest.approx(
        (E_C / 2.4) * COL_H * BM_H * COL_B)
    m.joint_panel_zones = m.joint_panel_zones * 2
    with pytest.raises(ValueError, match="duplicate"):
        m.validate()


def test_staged_submodel_keeps_joint_overrides():
    m = _frame("none", [{"point": [0, 0, 3.0]}], bays=1)
    sub = OpenSeesEngine(m)._stage_submodel(0)
    assert sub.joint_panel_zones == m.joint_panel_zones
    warnings.simplefilter("ignore")
