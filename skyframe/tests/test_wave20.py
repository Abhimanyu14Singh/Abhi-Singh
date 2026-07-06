"""Wave 20 (v0.19) — ASCE 41 auto hinges, performance point, pattern live
loading, auto construction sequence.

Every assertion is pinned by a hand derivation written out in the test:
table rows are typed inline from ASCE 41-17, spring mechanics are checked
against series-stiffness closed forms, the bilinearization against an
exactly-bilinear curve, and pattern loading against the classic
skip-loading midspan result.
"""

import math
import warnings

import pytest

from skyframe.core.model import (BuildingModel, FrameSection, LoadPattern,
                                 Material, MemberLoad, PointSupport)
from skyframe.core.patterning import (beam_spans, generate_pattern_live,
                                      pattern_live_envelope)
from skyframe.design.concrete import beam_flexure, beta1
from skyframe.design.hinges import (HINGE_N, RY_DEFAULT, auto_backbone,
                                    concrete_hinge_backbone, hinge_state,
                                    steel_hinge_backbone)
from skyframe.design.performance import (bilinearize, c0_factor, design_sa,
                                         target_displacement)
from skyframe.design.steel import design_properties
from skyframe.engine.opensees_engine import (HINGE_HARDENING_RATIO,
                                             HINGE_STIFFNESS_FACTOR,
                                             OpenSeesEngine)

FIX = (1, 1, 1, 1, 1, 1)
E_STEEL = 200e6          # kPa
FY = 345_000.0           # kPa (A992)


# --------------------------------------------------------------------------- #
# 1. steel hinge backbones — ASCE 41-17 Table 9-7.1
# --------------------------------------------------------------------------- #
def test_steel_backbone_compact_row():
    """W18x50, A992: hand compactness check puts it on the COMPACT row.

    Library dims (design table, imperial -> m): bf = 7.50 in,
    tf = 0.570 in, d = 18.0 in, tw = 0.355 in, Zx = 101 in^3.
    sqrt(E/Fy) = sqrt(200000/345) = 24.077 (MPa units, ratio is
    dimensionless so kPa works identically).
      flange: bf/2tf = 7.50/1.14   = 6.579 < 0.30*24.077 = 7.223  OK
      web:    h/tw = (18-1.14)/.355 = 47.49 < 2.45*24.077 = 58.99 OK
    -> a = 9 thy, b = 11 thy, c = 0.6; IO/LS/CP = 1/9/11 thy.
    thy = Zx*Fye*L/(6*E*I): Zx = 101*0.0254^3 = 1.65483e-3 m^3,
    Fye = 1.1*345000 kPa, I = 800*0.0254^4 = 3.329851e-4 m^4, L = 6 m ->
    thy = 1.65483e-3*379500*6/(6*200e6*3.329851e-4) = 9.42902e-3 rad.
    """
    props = design_properties("W18x50")
    I = 800.0 * 0.0254 ** 4
    bb = steel_hinge_backbone(props, FY, E_STEEL, I, 6.0)
    Zx = 101.0 * 0.0254 ** 3
    thy_hand = Zx * 1.1 * FY * 6.0 / (6.0 * E_STEEL * I)
    assert bb["thy"] == pytest.approx(thy_hand, rel=1e-9)
    assert bb["My"] == pytest.approx(Zx * 1.1 * FY, rel=1e-12)
    assert bb["a"] == pytest.approx(9.0 * thy_hand, rel=1e-9)
    assert bb["b"] == pytest.approx(11.0 * thy_hand, rel=1e-9)
    assert bb["c"] == pytest.approx(0.6)
    assert bb["IO"] == pytest.approx(1.0 * thy_hand, rel=1e-9)
    assert bb["LS"] == pytest.approx(9.0 * thy_hand, rel=1e-9)
    assert bb["CP"] == pytest.approx(11.0 * thy_hand, rel=1e-9)
    assert bb["slenderness_t"] == 0.0 and bb["kind"] == "steel"


def test_steel_backbone_slender_and_interpolated_rows():
    """Synthetic sections pin the SLENDER row and the midpoint.

    sqrt(E/Fy) = 24.077.  Flange ratio bf/2tf = 0.38*24.077 = 9.149
    (web kept compact) -> slender row exactly: a = 4 thy, c = 0.2.
    Midpoint: bf/2tf = 0.34*24.077 -> t = (0.34-0.30)/0.08 = 0.5 ->
    a = (9+4)/2 = 6.5 thy, c = 0.4, IO = (1+0.25)/2 = 0.625 thy.
    """
    root = math.sqrt(E_STEEL / FY)
    base = dict(design_properties("W18x50"))
    I, L = 3.3e-4, 6.0
    # slender flange: tf so that bf/2tf = 0.38*root
    sl = dict(base)
    sl["tf"] = sl["bf"] / (2.0 * 0.38 * root)
    sl["tw"] = (sl["d"] - 2.0 * sl["tf"]) / (2.0 * root)   # web compact
    bb = steel_hinge_backbone(sl, FY, E_STEEL, I, L)
    assert bb["a"] == pytest.approx(4.0 * bb["thy"], rel=1e-9)
    assert bb["c"] == pytest.approx(0.2)
    # halfway flange
    mid = dict(base)
    mid["tf"] = mid["bf"] / (2.0 * 0.34 * root)
    mid["tw"] = (mid["d"] - 2.0 * mid["tf"]) / (2.0 * root)
    bb = steel_hinge_backbone(mid, FY, E_STEEL, I, L)
    assert bb["slenderness_t"] == pytest.approx(0.5, rel=1e-9)
    assert bb["a"] == pytest.approx(6.5 * bb["thy"], rel=1e-9)
    assert bb["c"] == pytest.approx(0.4, rel=1e-9)
    assert bb["IO"] == pytest.approx(0.625 * bb["thy"], rel=1e-9)


def test_steel_backbone_governed_by_worse_of_flange_web():
    """Web halfway slender + compact flange -> the web factor governs."""
    root = math.sqrt(E_STEEL / FY)
    p = dict(design_properties("W18x50"))
    # web at the midpoint of its ladder: h/tw = (2.45+3.76)/2 * root
    p["tw"] = (p["d"] - 2.0 * p["tf"]) / (0.5 * (2.45 + 3.76) * root)
    bb = steel_hinge_backbone(p, FY, E_STEEL, 3.3e-4, 6.0)
    assert bb["slenderness_t"] == pytest.approx(0.5, rel=1e-6)


# --------------------------------------------------------------------------- #
# 2. concrete hinge backbones — ASCE 41-17 Table 10-7
# --------------------------------------------------------------------------- #
def test_concrete_backbone_rows_and_interpolation():
    """Row values typed from Table 10-7 (conforming, low shear).

    rho - rho' = 0 -> a = 0.025, b = 0.05, c = 0.2, IO = 0.010.
    rho - rho' = 0.5 rho_bal -> a = 0.020, b = 0.04, IO = 0.005.
    Quarter point (0.25 rho_bal) -> a = 0.0225 (linear).
    My check: b=0.3, h=0.6, fc=30 MPa, rho=0.01 -> d = 0.54,
    As = 0.00162 m^2, Mn = beam_flexure = As*fy*(d - a_blk/2).
    """
    b, h, fc, E, I, L = 0.3, 0.6, 30_000.0, 25e6, 0.3 * 0.6 ** 3 / 12, 6.0
    fy = 420_000.0
    fy_mpa = 420.0
    rho_bal = 0.85 * beta1(fc) * (fc / fy) * 600.0 / (600.0 + fy_mpa)
    bb0 = concrete_hinge_backbone(b, h, fc, E, I, L, rho=0.01,
                                  rho_prime=0.01)     # net = 0
    assert (bb0["a"], bb0["b"], bb0["c"]) == (0.025, 0.05, 0.2)
    assert (bb0["IO"], bb0["LS"], bb0["CP"]) == (0.010, 0.025, 0.05)
    bb5 = concrete_hinge_backbone(b, h, fc, E, I, L,
                                  rho=0.5 * rho_bal, rho_prime=0.0)
    assert bb5["a"] == pytest.approx(0.020, rel=1e-12)
    assert bb5["IO"] == pytest.approx(0.005, rel=1e-12)
    bb25 = concrete_hinge_backbone(b, h, fc, E, I, L,
                                   rho=0.25 * rho_bal, rho_prime=0.0)
    assert bb25["a"] == pytest.approx(0.0225, rel=1e-12)
    # My equals the module's own flexure closed form evaluated longhand
    d_eff = 0.9 * h
    As = 0.01 * b * d_eff
    Mn_hand = beam_flexure(b, d_eff, As, fc, fy)["Mn"]
    bb = concrete_hinge_backbone(b, h, fc, E, I, L, rho=0.01)
    assert bb["My"] == pytest.approx(Mn_hand, rel=1e-12)
    assert bb["thy"] == pytest.approx(Mn_hand * L / (6 * E * I), rel=1e-12)


def test_hinge_backbone_error_paths():
    with pytest.raises(ValueError):
        steel_hinge_backbone(design_properties("W18x50"), -1.0, E_STEEL,
                             1e-4, 6.0)
    with pytest.raises(ValueError):
        concrete_hinge_backbone(0.3, 0.6, 30000.0, 25e6, 5.4e-3, 6.0,
                                rho=0.5)


# --------------------------------------------------------------------------- #
# 3. hinge state classification
# --------------------------------------------------------------------------- #
def test_hinge_state_bands():
    """Bands shift by the spring yield rotation ty = My/k.

    My = 100, k = 10000 -> ty = 0.01; IO/LS/CP = 0.01/0.03/0.05 plastic.
    """
    bb = {"My": 100.0, "IO": 0.01, "LS": 0.03, "CP": 0.05}
    k = 10_000.0
    assert hinge_state(0.009, bb, k) == "elastic"
    assert hinge_state(0.015, bb, k) == "IO"       # plastic 0.005
    assert hinge_state(-0.035, bb, k) == "LS"      # sign-independent
    assert hinge_state(0.055, bb, k) == "CP"
    assert hinge_state(0.0601, bb, k) == "collapse"


# --------------------------------------------------------------------------- #
# 4. asce41 hinged pushover — cantilever closed forms
# --------------------------------------------------------------------------- #
def _cantilever(hinge=True, drift=0.06, steps=120):
    """One vertical W18x50 cantilever column, tip pushed in Y.

    A vertical member's local z axis is global X (engine axes
    convention), so STRONG-axis (M3 / I33) bending resists a Y push —
    the axis the asce41 hinge yields about.  The base hinge spring
    (k = n 6EI/L) is in SERIES with the cantilever: elastic tip
    stiffness 1/K = L^3/3EI + L^2/k.  The tip hinge at the free end
    never sees moment, so only the base hinge acts.
    """
    m = BuildingModel("cant")
    m.add_material(Material("steel", E_STEEL, 0.3, 77.0))
    m.add_section(FrameSection.from_library("W18x50", "steel"))
    m.set_stories([3.0])
    m.add_member("column", "W18x50", (0, 0, 0), (0, 0, 3), story="S1",
                 hinges="auto_m3" if hinge else "none")
    m.supports.append(PointSupport((0, 0, 0), FIX))
    m.patterns["DEAD"] = LoadPattern("DEAD", "dead")
    m.add_pushover_case("PUSH", "Y", gravity={}, target_drift=drift,
                        steps=steps, hinges="asce41")
    m.validate()
    return m


def test_cantilever_initial_stiffness_series_closed_form():
    """Initial curve slope == 1/(L^3/3EI + L^2/k) to solver precision."""
    m = _cantilever()
    sec = m.sections["W18x50"]
    L, E, I = 3.0, E_STEEL, sec.I33
    k = HINGE_STIFFNESS_FACTOR * 6.0 * E * I / L
    K_hand = 1.0 / (L ** 3 / (3.0 * E * I) + L ** 2 / k)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = OpenSeesEngine(m).run_pushover("PUSH")
    K_num = r.base_shear[0] / r.roof_disp[0]
    assert K_num == pytest.approx(K_hand, rel=1e-6)


def test_cantilever_yield_shear_and_hardening_branch():
    """First yield at V = My/L; hardening slope = 3% of 6EI/L on the spring.

    For the cantilever the base spring moment is EXACTLY V*L, so the
    step where V*L first exceeds My must be the elastic->IO transition;
    past yield the recorded spring moment must satisfy
    M = My + 0.03*(6EI/L)*(rot - ty) on the hardening branch (Hysteretic
    envelope, checked at every hardening-branch step).
    """
    m = _cantilever()
    sec = m.sections["W18x50"]
    L, E, I = 3.0, E_STEEL, sec.I33
    props = design_properties("W18x50")
    My = props["Zx"] * RY_DEFAULT * FY
    k = HINGE_STIFFNESS_FACTOR * 6.0 * E * I / L
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = OpenSeesEngine(m).run_pushover("PUSH")
    base = next(h for h in r.hinge_detail if h["end"] == "i")
    assert base["My"] == pytest.approx(My, rel=1e-12)
    ty = My / k
    kh = HINGE_HARDENING_RATIO * 6.0 * E * I / L
    yielded = [i for i, s in enumerate(base["state"]) if s != "elastic"]
    assert yielded, "the 6% drift push must yield the base hinge"
    first = yielded[0]
    # transition bracketed by V*L crossing My (within one displacement step)
    assert r.base_shear[first] * L >= My * (1.0 - 1e-9)
    assert r.base_shear[first - 1] * L < My * (1.0 + 1e-9)
    for i in yielded:
        rot, mom = base["rot"][i], base["moment"][i]
        if rot - ty > 1e-9 and rot - ty < base["a"]:
            m_env = My + kh * (rot - ty)
            assert abs(mom) == pytest.approx(m_env, rel=1e-6)


def test_cantilever_state_progression_matches_rotation_bands():
    """Recorded states equal hinge_state() re-applied to the rotations."""
    m = _cantilever()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = OpenSeesEngine(m).run_pushover("PUSH")
    sec = m.sections["W18x50"]
    k33 = HINGE_STIFFNESS_FACTOR * 6.0 * E_STEEL * sec.I33 / 3.0
    for h in r.hinge_detail:
        for rot, st in zip(h["rot"], h["state"]):
            assert hinge_state(rot, h, k33) == st


def test_asce41_without_flagged_members_is_pure_elastic():
    """hinges="asce41" with no auto_m3 members inserts NO springs: the
    capacity curve equals the elastic member stiffness exactly."""
    m = _cantilever(hinge=False, drift=0.01, steps=10)
    sec = m.sections["W18x50"]
    K_hand = 3.0 * E_STEEL * sec.I33 / 3.0 ** 3
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = OpenSeesEngine(m).run_pushover("PUSH")
    assert not r.hinge_detail
    assert r.base_shear[0] / r.roof_disp[0] == pytest.approx(K_hand,
                                                             rel=1e-9)


def test_diaphragm_support_hinge_base_shear_closed_form():
    """Regression for the v0.19 base-shear fix (load factor, not
    reactions): TWO hinged-base columns under a RIGID diaphragm.

    The diaphragm constrains ux/uy/rz only, so each column top is free to
    rotate — each column is a base-spring cantilever and they act in
    PARALLEL: K = 2 / (L^3/3EI + L^2/k), k = n 6EI/L.  The old
    reaction-sum recording read ~0 kN on exactly this configuration
    (Transformation handler + support hinge duplicates).
    """
    m = BuildingModel("dia")
    m.add_material(Material("steel", E_STEEL, 0.3, 77.0))
    m.add_section(FrameSection.from_library("W18x50", "steel"))
    m.set_stories([3.0])
    for x in (0.0, 6.0):
        m.add_member("column", "W18x50", (x, 0, 0), (x, 0, 3), story="S1",
                     hinges="auto_m3")
        m.supports.append(PointSupport((x, 0, 0), FIX))
    m.stories[0].diaphragm = "rigid"
    m.patterns["DEAD"] = LoadPattern("DEAD", "dead")
    m.add_pushover_case("PUSH", "Y", gravity={}, target_drift=0.002,
                        steps=10, hinges="asce41")
    m.validate()
    sec = m.sections["W18x50"]
    L, E, I = 3.0, E_STEEL, sec.I33
    k = HINGE_STIFFNESS_FACTOR * 6.0 * E * I / L
    K_hand = 2.0 / (L ** 3 / (3.0 * E * I) + L ** 2 / k)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = OpenSeesEngine(m).run_pushover("PUSH")
    assert r.base_shear[0] > 0.0, "reaction-sum bug: zero base shear"
    assert r.base_shear[0] / r.roof_disp[0] == pytest.approx(K_hand,
                                                             rel=1e-6)


def test_member_hinges_roundtrip_and_validation():
    m = _cantilever()
    d = m.to_dict()
    assert d["members"][0]["hinges"] == "auto_m3"
    assert d["pushover_cases"]["PUSH"]["hinges"] == "asce41"
    m2 = BuildingModel.from_dict(d)
    assert m2.members[0].hinges == "auto_m3"
    m2.members[0].hinges = "bogus"
    with pytest.raises(ValueError, match="hinges must be one of"):
        m2.validate()
    with pytest.raises(ValueError, match="hinge_params"):
        m.add_pushover_case("P2", "X", hinges="asce41",
                            hinge_params={"nope": 1.0})
    with pytest.raises(ValueError, match="hinge_params"):
        m.add_pushover_case("P3", "X", hinges="asce41",
                            hinge_params={"rho": -1.0})


# --------------------------------------------------------------------------- #
# 5. performance point — §7.4.3 coefficient method
# --------------------------------------------------------------------------- #
def _bilinear_curve(Ke=50_000.0, Vy=1_000.0, alpha=0.05, du=0.10, n=50):
    dy = Vy / Ke
    disp, shear = [], []
    for i in range(1, n + 1):
        d = du * i / n
        v = Ke * d if d <= dy else Vy + alpha * Ke * (d - dy)
        disp.append(d)
        shear.append(v)
    return disp, shear


def test_bilinearize_exact_on_bilinear_curve():
    """The idealization must recover an exactly-bilinear curve exactly:
    Ke = 50000, Vy = 1000, dy = 0.02 (equal-area + 0.6Vy secant are both
    identities on the true curve)."""
    disp, shear = _bilinear_curve()
    bl = bilinearize(disp, shear)
    assert bl["Ke"] == pytest.approx(50_000.0, rel=1e-9)
    assert bl["Vy"] == pytest.approx(1_000.0, rel=1e-9)
    assert bl["dy"] == pytest.approx(0.02, rel=1e-9)
    assert bl["Ki"] == pytest.approx(50_000.0, rel=1e-12)
    # hand trapezoid area: 0.5*0.02*1000 + 0.08*(1000+1200)/2 = 98
    assert bl["area"] == pytest.approx(98.0, rel=1e-9)


def test_c0_table_interpolation():
    """Table 7-5 'Other buildings': typed rows + midpoint interpolation."""
    assert c0_factor(1) == 1.0
    assert c0_factor(2) == pytest.approx(1.2)
    assert c0_factor(3) == pytest.approx(1.3)
    assert c0_factor(4) == pytest.approx(1.35)      # midpoint of 3->5
    assert c0_factor(5) == pytest.approx(1.4)
    assert c0_factor(7) == pytest.approx(1.44)      # 1.4 + 0.1*2/5
    assert c0_factor(10) == 1.5 and c0_factor(50) == 1.5


def test_design_sa_branches():
    """SDS = 1.0, SD1 = 0.6 -> Ts = 0.6, T0 = 0.12.
    Sa(0.06) = 1.0*(0.4 + 0.6*0.5) = 0.7; plateau = 1.0; Sa(1.2) = 0.5."""
    assert design_sa(0.06, 1.0, 0.6) == pytest.approx(0.7)
    assert design_sa(0.12, 1.0, 0.6) == pytest.approx(1.0)
    assert design_sa(0.60, 1.0, 0.6) == pytest.approx(1.0)
    assert design_sa(1.20, 1.0, 0.6) == pytest.approx(0.5)


def test_target_displacement_hand_formula():
    """Full Eq. 7-28 longhand: Ti = 0.5 s, W = 5000 kN, SDS = 1, SD1 = 0.6,
    site D, 3 stories, on the exact bilinear curve (Ki = Ke -> Te = Ti):
      Sa = 1.0 (plateau), mu = 1.0/(1000/5000) = 5,
      C1 = 1 + 4/(60*0.25) = 1.266667, C2 = 1 + (4/0.5)^2/800 = 1.08,
      C0 = 1.3, delta_t = 1.3*1.266667*1.08*1.0*(0.5/2pi)^2*9.81."""
    disp, shear = _bilinear_curve()
    tp = target_displacement(disp, shear, Ti=0.5, W=5000.0, SDS=1.0,
                             SD1=0.6, site_class="D", num_stories=3)
    hand = (1.3 * (1 + 4 / (60 * 0.25)) * (1 + (4 / 0.5) ** 2 / 800)
            * 1.0 * (0.5 / (2 * math.pi)) ** 2 * 9.81)
    assert tp["Te"] == pytest.approx(0.5, rel=1e-12)
    assert tp["mu"] == pytest.approx(5.0, rel=1e-9)
    assert tp["C1"] == pytest.approx(1.2666666667, rel=1e-9)
    assert tp["C2"] == pytest.approx(1.08, rel=1e-9)
    assert tp["delta_t"] == pytest.approx(hand, rel=1e-9)
    # long-period limbs: Te >= 1 s -> C1 = C2 = 1
    tp2 = target_displacement(disp, shear, Ti=1.5, W=5000.0, SDS=1.0,
                              SD1=0.6, site_class="D", num_stories=3)
    assert tp2["C1"] == 1.0 and tp2["C2"] == 1.0


def test_target_displacement_site_class_a_factor():
    """Site B vs D differ ONLY through a = 130 vs 60 in C1."""
    disp, shear = _bilinear_curve()
    tb = target_displacement(disp, shear, Ti=0.5, W=5000.0, SDS=1.0,
                             SD1=0.6, site_class="B", num_stories=3)
    td = target_displacement(disp, shear, Ti=0.5, W=5000.0, SDS=1.0,
                             SD1=0.6, site_class="D", num_stories=3)
    assert tb["C1"] == pytest.approx(1 + 4 / (130 * 0.25), rel=1e-9)
    assert td["C1"] == pytest.approx(1 + 4 / (60 * 0.25), rel=1e-9)
    assert tb["C2"] == td["C2"]
    with pytest.raises(ValueError, match="site class"):
        target_displacement(disp, shear, Ti=0.5, W=1.0, SDS=1.0, SD1=0.6,
                            site_class="Z")


def test_bilinearize_error_and_elastic_paths():
    with pytest.raises(ValueError):
        bilinearize([0.01], [100.0])
    # purely elastic straight line: degenerates to the elastic idealization
    # Vy = Vu, Ke = Ki (the pushover never yielded), flagged elastic
    bl = bilinearize([0.01, 0.02, 0.03], [100.0, 200.0, 300.0])
    assert bl["elastic"] is True
    assert bl["Vy"] == pytest.approx(300.0)
    assert bl["Ke"] == pytest.approx(10_000.0)
    # the truly bilinear curve must NOT be flagged elastic
    disp, shear = _bilinear_curve()
    assert bilinearize(disp, shear)["elastic"] is False


# --------------------------------------------------------------------------- #
# 6. pattern (skip) live loading
# --------------------------------------------------------------------------- #
def _three_span(w=10.0):
    """3-span continuous beam (0-4-8-12 m) on 4 fixed supports, plus a
    dead pattern; LIVE puts a UDL w on all three spans."""
    m = BuildingModel("spans")
    m.add_material(Material("conc", 25e6, 0.2, 24.0))
    m.add_section(FrameSection.rectangular("B3060", "conc", 0.3, 0.6))
    m.set_stories([3.0])
    uids = []
    for x0 in (0.0, 4.0, 8.0):
        mm = m.add_member("beam", "B3060", (x0, 0, 3), (x0 + 4.0, 0, 3),
                          story="S1")
        uids.append(mm.uid)
    for x in (0.0, 4.0, 8.0, 12.0):
        m.supports.append(PointSupport((x, 0, 3), FIX))
    m.patterns["DEAD"] = LoadPattern("DEAD", "dead")
    live = LoadPattern("LIVE", "live")
    for u in uids:
        live.member_loads.append(MemberLoad(u, "udl", w))
    m.patterns["LIVE"] = live
    m.add_case("DEAD", {"DEAD": 1.0})
    m.add_case("LIVE", {"LIVE": 1.0})
    m.validate()
    return m, uids


def test_beam_spans_indices_and_runs():
    """Chained collinear beams number 0,1,2; a disconnected beam on the
    same line restarts at 0; a beam on another line is its own run."""
    m, uids = _three_span()
    b4 = m.add_member("beam", "B3060", (20.0, 0, 3), (24.0, 0, 3),
                      story="S1")                    # same line, gap
    b5 = m.add_member("beam", "B3060", (0, 5.0, 3), (4.0, 5.0, 3),
                      story="S1")                    # different line
    spans = beam_spans(m)
    assert [spans[u] for u in uids] == [0, 1, 2]
    assert spans[b4.uid] == 0
    assert spans[b5.uid] == 0


def test_generate_pattern_live_split():
    """Spans 0 and 2 land in __ODD (2 loads), span 1 in __EVEN (1 load)."""
    m, uids = _three_span()
    odd, even = generate_pattern_live(m, "LIVE")
    assert odd == "LIVE__ODD" and even == "LIVE__EVEN"
    odd_uids = {ml.member_uid for ml in m.patterns[odd].member_loads}
    even_uids = {ml.member_uid for ml in m.patterns[even].member_loads}
    assert odd_uids == {uids[0], uids[2]}
    assert even_uids == {uids[1]}
    with pytest.raises(ValueError, match="live"):
        generate_pattern_live(m, "DEAD")
    with pytest.raises(ValueError, match="Unknown"):
        generate_pattern_live(m, "NOPE")


def test_pattern_envelope_classic_skip_result():
    """Loading ONLY the middle span maximizes its midspan sagging moment.

    Fixed-fixed 4 m spans decouple at the fixed supports, so each span is
    an independent fixed-fixed beam: loaded midspan M = wL^2/24 (sagging),
    unloaded M = 0.  With w_total = 1.2*0 (massless dead) + 1.6*10 = 16:
    PLL_EVEN (middle loaded) midspan M = 16*16/24 = 10.6667 kN*m — equal
    to PLL_ALL for THIS support layout, and the envelope must return
    exactly the max of the three cases at every station (definition
    check, machine precision).
    """
    m, uids = _three_span()
    pattern_live_envelope(m, "LIVE")
    assert "PATTERN-LL" in m.combos
    assert m.combos["PATTERN-LL"].combo_type == "envelope"
    assert set(m.cases) >= {"PLL_ALL", "PLL_ODD", "PLL_EVEN"}
    # factored case content: PLL_EVEN = 1.2*DEAD + 1.6*LIVE__EVEN
    assert m.cases["PLL_EVEN"].patterns == {"DEAD": 1.2, "LIVE__EVEN": 1.6}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = OpenSeesEngine(m).run()
    mid = uids[1]
    sta = {c: res.cases[c].member_stations[mid]["M3"]
           for c in ("PLL_ALL", "PLL_ODD", "PLL_EVEN")}
    env_max = res.combos["PATTERN-LL"].member_stations[mid]["M3"]
    env_min = res.combos["PATTERN-LL"].minima.member_stations[mid]["M3"]
    n_sta = len(env_max)
    for i in range(n_sta):
        assert env_max[i] == pytest.approx(
            max(sta[c][i] for c in sta), abs=1e-9)
        assert env_min[i] == pytest.approx(
            min(sta[c][i] for c in sta), abs=1e-9)
    # fixed-fixed hand values on the loaded middle span (PLL_EVEN):
    # |M| = wL^2/24 at midspan, wL^2/12 at the ends, w = 1.6*10 kN/m
    w = 1.6 * 10.0
    assert abs(sta["PLL_EVEN"][n_sta // 2]) == pytest.approx(
        w * 16.0 / 24.0, rel=1e-6)
    assert abs(sta["PLL_EVEN"][0]) == pytest.approx(w * 16.0 / 12.0,
                                                    rel=1e-6)
    # the classic skip contrast: the ODD case leaves the middle span
    # unloaded — its middle-span moments vanish (fixed ends decouple)
    assert abs(sta["PLL_ODD"][n_sta // 2]) < 1e-6


# --------------------------------------------------------------------------- #
# 7. API layer
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client():
    from skyframe.api.server import create_app, _state
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c, _state


def test_api_pattern_live_and_auto_sequence(client):
    c, state = client
    m, _uids = _three_span()
    state["model"] = m
    r = c.post("/api/loads/pattern-live", json={})
    assert r.status_code == 200
    d = r.get_json()
    assert "LIVE__ODD" in d["patterns"] and "PATTERN-LL" in d["combos"]
    r = c.post("/api/loads/pattern-live", json={"live_pattern": "NOPE"})
    assert r.status_code == 400
    r = c.post("/api/case/auto-sequence", json={"name": "SEQ1"})
    assert r.status_code == 200
    assert "SEQ1" in r.get_json()["staged_cases"]
    r = c.post("/api/case/auto-sequence",
               json={"name": "SEQ2", "pattern": "NOPE"})
    assert r.status_code == 400


def test_api_performance_point(client):
    """Endpoint output must equal target_displacement() called directly
    with the same curve, Ti and W (consistency), and 400 cleanly."""
    c, state = client
    m = _cantilever()
    # a tip mass so the modal run has something to swing
    from skyframe.core.model import NodalMass
    m.nodal_masses.append(NodalMass((0, 0, 3), 10.0, 10.0, 0.0))
    m.validate()
    state["model"] = m
    r = c.post("/api/results/performance-point",
               json={"case": "PUSH", "SDS": 1.0, "SD1": 0.6})
    assert r.status_code == 200
    d = r.get_json()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        eng = OpenSeesEngine(m)
        po = eng.run_pushover("PUSH")
        # same selection rule as the endpoint: the dominant mode in the
        # push direction (mode 1 is the WEAK-axis X sway of the column)
        Ti = max(eng.run_modal().participation,
                 key=lambda p: p.get("uy", 0.0))["T"]
    W = 10.0 * 9.81
    hand = target_displacement(po.roof_disp, po.base_shear, Ti=Ti, W=W,
                               SDS=1.0, SD1=0.6, num_stories=1)
    assert d["delta_t"] == pytest.approx(hand["delta_t"], rel=1e-9)
    assert d["Vy"] == pytest.approx(hand["Vy"], rel=1e-9)
    assert set(d["hinge_summary"]) == {"elastic", "IO", "LS", "CP",
                                       "collapse"}
    assert sum(d["hinge_summary"].values()) == 2       # both column ends
    assert 0 <= d["step"] < len(po.roof_disp)
    r = c.post("/api/results/performance-point",
               json={"case": "NOPE", "SDS": 1.0, "SD1": 0.6})
    assert r.status_code == 400
    r = c.post("/api/results/performance-point", json={"case": "PUSH"})
    assert r.status_code == 400


def test_api_analyze_carries_hinge_detail(client):
    c, state = client
    m = _cantilever(drift=0.06, steps=30)
    state["model"] = m
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = c.post("/api/analyze")
    assert r.status_code == 200
    po = r.get_json()["pushover"]["PUSH"]
    assert len(po["hinges"]) == 2
    h = po["hinges"][0]
    assert {"uid", "end", "My", "rot", "moment", "state"} <= set(h)
    assert len(h["rot"]) == len(po["roof_disp"])
