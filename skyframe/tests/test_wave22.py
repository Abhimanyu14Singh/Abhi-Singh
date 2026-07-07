"""Wave 22 (v0.21) — Section Designer, fiber PMM hinges, device library II.

Every assertion is pinned by a hand derivation typed into the test:
polygon properties against the b*h^3/12 closed forms and a longhand
T-section, clipping against half-area/triangle identities, the designer
PMM against the independent design.concrete column machinery, the fiber
hinge against the 6-point HingeRadau flexibility sum (weights lp / 3lp /
Gauss — CONTRACT v0.21), and the devices against their published laws
(FP: F = mu*W + W*d/R; TFP fully-sliding tangent W/(R2+R3); MultiLinear
exact at backbone points).

Units per CONTRACT.md: kN, m, kPa.
"""

import math
import warnings

import pytest

from skyframe.core.model import (BuildingModel, FrameSection, LoadPattern,
                                 Material, NodalLoad, PointSupport)
from skyframe.core.sections_designer import (DesignerSection, clip_polygon,
                                             make_frame_section,
                                             pmm_surface, polygon_area,
                                             polygon_integrals,
                                             section_properties,
                                             validate_polygon)
from skyframe.design.concrete import column_interaction
from skyframe.design.hinges import (auto_backbone, concrete_hinge_backbone,
                                    hinge_state)
from skyframe.design.steel import design_properties
from skyframe.design.wall import fc_from_E
from skyframe.engine.opensees_engine import (FIBER_HINGE_STRIPS,
                                             HINGE_STIFFNESS_FACTOR,
                                             NONLINEAR_STATIC_LINK_TYPES,
                                             OpenSeesEngine)

FIX = (1, 1, 1, 1, 1, 1)
ROT_FIX = (0, 0, 0, 1, 1, 1)          # rotation-only support (bearing top)
E_STEEL = 200e6                        # kPa
FY = 345_000.0                         # kPa (A992)


def _rect(b, h):
    """CCW rectangle y in [-b/2, b/2], z in [-h/2, h/2]."""
    return [[-b / 2, -h / 2], [b / 2, -h / 2],
            [b / 2, h / 2], [-b / 2, h / 2]]


def _mats(**extra):
    mats = {"conc": Material("conc", 25e6, 0.2, 24.0),
            "bar": Material("bar", 200e6, 0.3, 77.0)}
    mats["conc"].fc = 30_000.0
    mats["bar"].fy = 420_000.0
    for k, v in extra.items():
        mats[k] = v
    return mats


# --------------------------------------------------------------------------- #
# 1. exact polygon properties
# --------------------------------------------------------------------------- #
def test_rectangle_polygon_properties_closed_form():
    """b = 0.3, h = 0.6: A = 0.18, I33 = 0.3*0.6^3/12 = 5.4e-3,
    I22 = 0.6*0.3^3/12 = 1.35e-3, centroid at the origin, bbox b/h, and
    J = A^4/(40*(I33+I22)) — all to 1e-12 (shoelace closed forms)."""
    b, h = 0.3, 0.6
    ds = DesignerSection("D", "conc", polygons=[{"vertices": _rect(b, h)}])
    p = section_properties(ds, _mats())
    assert p["A"] == pytest.approx(b * h, rel=1e-12)
    assert p["Cy"] == pytest.approx(0.0, abs=1e-15)
    assert p["Cz"] == pytest.approx(0.0, abs=1e-15)
    assert p["I33"] == pytest.approx(b * h ** 3 / 12.0, rel=1e-12)
    assert p["I22"] == pytest.approx(h * b ** 3 / 12.0, rel=1e-12)
    assert p["b"] == pytest.approx(b) and p["h"] == pytest.approx(h)
    Ip = b * h ** 3 / 12.0 + h * b ** 3 / 12.0
    assert p["J"] == pytest.approx((b * h) ** 4 / (40.0 * Ip), rel=1e-12)
    # no rebar: transformed == gross
    assert p["A_tr"] == pytest.approx(p["A"], rel=1e-15)
    assert p["I33_tr"] == pytest.approx(p["I33"], rel=1e-15)


def test_rectangle_with_hole_subtracts_exactly():
    """0.3 x 0.6 outer minus a centered 0.1 x 0.2 hole:
    A = 0.18 - 0.02 = 0.16; I33 = (0.3*0.6^3 - 0.1*0.2^3)/12."""
    ds = DesignerSection("D", "conc", polygons=[
        {"vertices": _rect(0.3, 0.6)},
        {"vertices": _rect(0.1, 0.2), "hole": True}])
    p = section_properties(ds, _mats())
    assert p["A"] == pytest.approx(0.18 - 0.02, rel=1e-12)
    assert p["I33"] == pytest.approx(
        (0.3 * 0.6 ** 3 - 0.1 * 0.2 ** 3) / 12.0, rel=1e-12)
    assert p["I22"] == pytest.approx(
        (0.6 * 0.3 ** 3 - 0.2 * 0.1 ** 3) / 12.0, rel=1e-12)


def test_t_section_centroid_and_inertia_longhand():
    """T-section as two touching solid rectangles (exact additive union):
    flange 0.5 x 0.12 (z in [0.38, 0.50]), web 0.14 x 0.38 (z in [0, 0.38]).

    Af = 0.06 @ zf = 0.44; Aw = 0.0532 @ zw = 0.19; A = 0.1132;
    Cz = (0.06*0.44 + 0.0532*0.19)/0.1132 = 0.322544...
    I33 = 0.5*0.12^3/12 + 0.06*(0.44-Cz)^2
        + 0.14*0.38^3/12 + 0.0532*(0.19-Cz)^2."""
    flange = [[-0.25, 0.38], [0.25, 0.38], [0.25, 0.50], [-0.25, 0.50]]
    web = [[-0.07, 0.0], [0.07, 0.0], [0.07, 0.38], [-0.07, 0.38]]
    ds = DesignerSection("T", "conc", polygons=[{"vertices": flange},
                                                {"vertices": web}])
    p = section_properties(ds, _mats())
    Af, zf = 0.5 * 0.12, 0.44
    Aw, zw = 0.14 * 0.38, 0.19
    A = Af + Aw
    Cz = (Af * zf + Aw * zw) / A
    I33 = (0.5 * 0.12 ** 3 / 12.0 + Af * (zf - Cz) ** 2
           + 0.14 * 0.38 ** 3 / 12.0 + Aw * (zw - Cz) ** 2)
    assert p["A"] == pytest.approx(A, rel=1e-12)
    assert p["Cz"] == pytest.approx(Cz, rel=1e-12)
    assert p["Cy"] == pytest.approx(0.0, abs=1e-15)
    assert p["I33"] == pytest.approx(I33, rel=1e-12)
    assert p["b"] == pytest.approx(0.5) and p["h"] == pytest.approx(0.5)


def test_rebar_transformed_contributions_hand_check():
    """Two 1e-3 m^2 bars at z = +-0.25 in a 0.3 x 0.6 rectangle,
    n = 200e6/25e6 = 8: A_tr = 0.18 + 7*2e-3 = 0.194; the symmetric pair
    keeps the centroid, so I33_tr = 5.4e-3 + 7*2e-3*0.25^2 = 6.275e-3."""
    ds = DesignerSection("D", "conc", polygons=[{"vertices": _rect(0.3, 0.6)}],
                         rebar=[{"y": 0.0, "z": 0.25, "area": 1e-3,
                                 "material": "bar"},
                                {"y": 0.0, "z": -0.25, "area": 1e-3,
                                 "material": "bar"}])
    p = section_properties(ds, _mats())
    n = 200e6 / 25e6
    assert p["A_tr"] == pytest.approx(0.18 + (n - 1) * 2e-3, rel=1e-12)
    assert p["Cz_tr"] == pytest.approx(0.0, abs=1e-15)
    assert p["I33_tr"] == pytest.approx(
        0.3 * 0.6 ** 3 / 12.0 + (n - 1) * 2e-3 * 0.25 ** 2, rel=1e-12)
    # one bar only: the transformed centroid shifts by
    # Cz_tr = (n-1)*As*0.25 / A_tr and BOTH terms transfer to it
    ds1 = DesignerSection("D", "conc",
                          polygons=[{"vertices": _rect(0.3, 0.6)}],
                          rebar=[{"y": 0.0, "z": 0.25, "area": 1e-3,
                                  "material": "bar"}])
    p1 = section_properties(ds1, _mats())
    A_tr = 0.18 + (n - 1) * 1e-3
    Cz_tr = (n - 1) * 1e-3 * 0.25 / A_tr
    assert p1["Cz_tr"] == pytest.approx(Cz_tr, rel=1e-12)
    assert p1["I33_tr"] == pytest.approx(
        0.3 * 0.6 ** 3 / 12.0 + 0.18 * Cz_tr ** 2
        + (n - 1) * 1e-3 * (0.25 - Cz_tr) ** 2, rel=1e-12)


def test_polygon_validation_errors():
    with pytest.raises(ValueError, match="at least 3"):
        validate_polygon([[0, 0], [1, 0]])
    with pytest.raises(ValueError, match="zero area"):
        validate_polygon([[0, 0], [1, 0], [2, 0]])
    with pytest.raises(ValueError, match="repeated consecutive"):
        validate_polygon([[0, 0], [0, 0], [1, 0], [0, 1]])
    # bowtie with NON-zero shoelace area (area = 4) -> the edge-crossing
    # test must catch it: edge (4,3)-(2,-1) crosses the base at (2.5, 0)
    with pytest.raises(ValueError, match="self-intersecting"):
        validate_polygon([[0, 0], [4, 0], [4, 3], [2, -1], [0, 3]])
    mats = _mats()
    with pytest.raises(ValueError, match="unknown material"):
        section_properties(DesignerSection(
            "D", "conc", polygons=[{"vertices": _rect(0.3, 0.6),
                                    "material": "nope"}]), mats)
    with pytest.raises(ValueError, match="unknown material"):
        section_properties(DesignerSection(
            "D", "conc", polygons=[{"vertices": _rect(0.3, 0.6)}],
            rebar=[{"y": 0, "z": 0, "area": 1e-4,
                    "material": "nope"}]), mats)
    with pytest.raises(ValueError, match="non-hole"):
        section_properties(DesignerSection(
            "D", "conc", polygons=[{"vertices": _rect(0.3, 0.6),
                                    "hole": True}]), mats)
    with pytest.raises(ValueError, match="net area"):
        section_properties(DesignerSection(
            "D", "conc", polygons=[
                {"vertices": _rect(0.3, 0.6)},
                {"vertices": _rect(0.4, 0.8), "hole": True}]), mats)
    with pytest.raises(ValueError, match="base must be one of"):
        section_properties(DesignerSection(
            "D", "conc", base="wood",
            polygons=[{"vertices": _rect(0.3, 0.6)}]), mats)


# --------------------------------------------------------------------------- #
# 2. exact polygon clipping
# --------------------------------------------------------------------------- #
def test_clip_rectangle_at_mid_height_halves_the_area():
    """0.3 x 0.6 rectangle clipped at z = 0: each part has EXACTLY half
    the area (1e-12), and above/below first moments mirror:
    int z dA (above) = 0.3 * 0.3 * 0.15 = 0.0135."""
    rect = _rect(0.3, 0.6)
    up = clip_polygon(rect, 0.0, "above")
    lo = clip_polygon(rect, 0.0, "below")
    A_up, _sy, Sz_up, _iy, _iz = polygon_integrals(up)
    A_lo, _sy, Sz_lo, _iy, _iz = polygon_integrals(lo)
    assert A_up == pytest.approx(0.09, rel=1e-12)
    assert A_lo == pytest.approx(0.09, rel=1e-12)
    assert Sz_up == pytest.approx(0.3 * 0.3 * 0.15, rel=1e-12)
    assert Sz_lo == pytest.approx(-0.3 * 0.3 * 0.15, rel=1e-12)


def test_clip_triangle_hand_case_and_degenerate():
    """Right triangle (0,0)-(2,0)-(0,2) clipped above z = 1: the apex
    triangle (0,1)-(1,1)-(0,2): A = 0.5, centroid z = (1+1+2)/3 = 4/3,
    so int z dA = 0.5 * 4/3 = 2/3.  Clipping entirely above/below the
    outline returns [] / the whole polygon."""
    tri = [[0.0, 0.0], [2.0, 0.0], [0.0, 2.0]]
    top = clip_polygon(tri, 1.0, "above")
    A, _sy, Sz, _iy, _iz = polygon_integrals(top)
    assert A == pytest.approx(0.5, rel=1e-12)
    assert Sz == pytest.approx(2.0 / 3.0, rel=1e-12)
    assert clip_polygon(tri, 5.0, "above") == []
    whole = clip_polygon(tri, -1.0, "above")
    assert polygon_area(whole) == pytest.approx(2.0, rel=1e-12)
    with pytest.raises(ValueError, match="above|below"):
        clip_polygon(tri, 0.0, "sideways")


# --------------------------------------------------------------------------- #
# 3. round trip + derived FrameSection
# --------------------------------------------------------------------------- #
def _model_with_designer(b=0.3, h=0.6, rebar=()):
    m = BuildingModel("dsn")
    m.add_material(Material("conc", 25e6, 0.2, 24.0))
    m.add_material(Material("bar", 200e6, 0.3, 77.0))
    ds = DesignerSection("D1", "conc",
                         polygons=[{"vertices": _rect(b, h)}],
                         rebar=list(rebar))
    m.add_designer_section(ds)
    return m, ds


def test_designer_roundtrip_and_mirrored_frame_section():
    """to_dict/from_dict is exact; the mirrored FrameSection matches
    FrameSection.rectangular on A/I33/I22/b/h (the plain rectangle), and
    J is the documented A^4/(40*Ip) approximation (NOT the Roark
    rectangle formula)."""
    m, ds = _model_with_designer()
    sec = m.sections["D1"]
    ref = FrameSection.rectangular("R", "conc", 0.3, 0.6)
    assert sec.A == pytest.approx(ref.A, rel=1e-12)
    assert sec.I33 == pytest.approx(ref.I33, rel=1e-12)
    assert sec.I22 == pytest.approx(ref.I22, rel=1e-12)
    assert sec.b == pytest.approx(0.3) and sec.h == pytest.approx(0.6)
    Ip = ref.I33 + ref.I22
    assert sec.J == pytest.approx(0.18 ** 4 / (40.0 * Ip), rel=1e-12)
    assert sec.J != pytest.approx(ref.J, rel=0.01)   # different formulas
    d = m.to_dict()
    m2 = BuildingModel.from_dict(d)
    assert m2.to_dict()["designer_sections"] == d["designer_sections"]
    assert m2.to_dict()["sections"]["D1"] == d["sections"]["D1"]
    # a member can use it; deletion is blocked while it does
    m.add_member("column", "D1", (0, 0, 0), (0, 0, 3))
    with pytest.raises(ValueError, match="still used"):
        m.remove_designer_section("D1")
    m.members.clear()
    m.remove_designer_section("D1")
    assert "D1" not in m.sections and "D1" not in m.designer_sections
    with pytest.raises(KeyError):
        m.remove_designer_section("D1")


def test_make_frame_section_transformed_values():
    """With the symmetric 2 x 1e-3 bar pair at z = +-0.25 (n = 8) the
    frame section carries the TRANSFORMED A/I33 (hand values of test 1)."""
    rb = ({"y": 0.0, "z": 0.25, "area": 1e-3, "material": "bar"},
          {"y": 0.0, "z": -0.25, "area": 1e-3, "material": "bar"})
    m, ds = _model_with_designer(rebar=rb)
    sec = make_frame_section(ds, m.materials)
    assert sec.A == pytest.approx(0.18 + 7 * 2e-3, rel=1e-12)
    assert sec.I33 == pytest.approx(5.4e-3 + 7 * 2e-3 * 0.0625, rel=1e-12)


# --------------------------------------------------------------------------- #
# 4. PMM surfaces
# --------------------------------------------------------------------------- #
def _rc_designer_matching_column_interaction(b=0.3, h=0.6, d=0.54, dp=0.06,
                                             As_face=2e-3):
    """Rectangular RC designer section that mirrors column_interaction's
    two-face symmetric layout: each face row split into 2 bars."""
    mats = _mats()
    rb = []
    for z in (h / 2 - dp, h / 2 - d):
        for y in (-0.05, 0.05):
            rb.append({"y": y, "z": z, "area": As_face / 2,
                       "material": "bar"})
    ds = DesignerSection("PM", "conc",
                         polygons=[{"vertices": _rect(b, h)}], rebar=rb)
    return ds, mats


def test_pmm_concrete_matches_design_concrete_column_interaction():
    """The designer PMM and design.concrete.column_interaction implement
    the SAME assumptions (Whitney block over the exact clip == a*b for
    the rectangle, +-fy clamped bar strains, displaced concrete ignored,
    phi_from_strain, the 0.80*0.65 cap on the closed-form Pn0), so the
    labeled points must agree to machine precision (asserted at 1e-9,
    far inside the 1e-3 spec)."""
    b, h, d, dp, As_face = 0.3, 0.6, 0.54, 0.06, 2e-3
    ds, mats = _rc_designer_matching_column_interaction(b, h, d, dp, As_face)
    out = pmm_surface(ds, mats, "33")
    ref = column_interaction(b, h, d, dp, As_face, 30_000.0, 420_000.0)
    mine = {q["label"]: q for q in out["detail"] if q["label"]}
    for lab in ("pure compression", "eps_t = 0", "balanced",
                "pure bending", "pure tension"):
        rp = next(q for q in ref if q["label"] == lab)
        assert mine[lab]["phiPn"] == pytest.approx(rp["phiPn"], rel=1e-9,
                                                   abs=1e-6)
        assert mine[lab]["phiMn"] == pytest.approx(rp["phiMn"], rel=1e-9,
                                                   abs=1e-6)
    # diagram ordered pure compression -> pure tension
    Ps = [pt[1] for pt in out["points"]]
    assert Ps[0] == max(Ps) and Ps[-1] == min(Ps)
    # concrete PMM without rebar is refused
    bare = DesignerSection("X", "conc",
                           polygons=[{"vertices": _rect(b, h)}])
    with pytest.raises(ValueError, match="rebar"):
        pmm_surface(bare, mats, "33")


def test_pmm_steel_plastic_surface_exact():
    """Steel 0.3 x 0.6 rectangle, Fy = 345 MPa: P = 0 plastic bending
    M = Fy*Zp = 345000*0.3*0.6^2/4 = 9315 kN*m EXACT; full compression
    P = Fy*A = 62100 kN; axis 22 swaps b/h: M = Fy*0.6*0.3^2/4."""
    steel = Material("steel", 200e6, 0.3, 77.0)
    steel.fy = FY
    mats = _mats(steel=steel)
    ds = DesignerSection("SP", "steel", base="steel",
                         polygons=[{"vertices": _rect(0.3, 0.6)}])
    out = pmm_surface(ds, mats, "33")
    pb = next(q for q in out["detail"] if q["label"] == "pure bending")
    assert pb["Pn"] == pytest.approx(0.0, abs=1e-8)
    assert pb["Mn"] == pytest.approx(FY * 0.3 * 0.6 ** 2 / 4.0, rel=1e-12)
    assert out["detail"][0]["Pn"] == pytest.approx(FY * 0.18, rel=1e-12)
    assert out["detail"][-1]["Pn"] == pytest.approx(-FY * 0.18, rel=1e-12)
    out22 = pmm_surface(ds, mats, "22")
    pb22 = next(q for q in out22["detail"] if q["label"] == "pure bending")
    assert pb22["Mn"] == pytest.approx(FY * 0.6 * 0.3 ** 2 / 4.0, rel=1e-12)
    with pytest.raises(ValueError, match="axis"):
        pmm_surface(ds, mats, "44")


# --------------------------------------------------------------------------- #
# 5. fiber PMM hinges
# --------------------------------------------------------------------------- #
E_C, B_C, H_C, L_C, W_C = 25e6, 0.4, 0.4, 3.0, 40.0


def _fiber_cantilever(drift, steps, hinges="fiber_pmm"):
    """0.4 x 0.4 RC cantilever (E = 25e6 kPa, no explicit fc -> the ACI
    E-inversion fc' = fc_from_E), W = 40 kN gravity preload at the tip,
    pushed in Y (strong axis — a vertical member's local z is global X)."""
    m = BuildingModel("fib")
    m.add_material(Material("conc", E_C, 0.2, 24.0))
    m.add_section(FrameSection.rectangular("C40", "conc", B_C, H_C))
    m.set_stories([L_C])
    m.add_member("column", "C40", (0, 0, 0), (0, 0, L_C), story="S1",
                 hinges=hinges)
    m.supports.append(PointSupport((0, 0, 0), FIX))
    dead = LoadPattern("DEAD", "dead")
    dead.nodal_loads.append(NodalLoad((0, 0, L_C), fz=-W_C))
    m.patterns["DEAD"] = dead
    m.add_pushover_case("PUSH", "Y", gravity={"DEAD": 1.0},
                        target_drift=drift, steps=steps, hinges="asce41")
    m.validate()
    return m


def _radau_cantilever_K(EI_f, EI_e, L, lp):
    """Tip stiffness of the HingeRadau cantilever: the 6-point rule
    (fiber pts x = 0, L weight lp; elastic pts 8lp/3, L-8lp/3 weight
    3lp; 2-pt Gauss over [4lp, L-4lp]) in the unit-load flexibility sum
    f = sum w_i (x_i/L - 1)^2 / EI(x_i);  K = 1/(L^2 f)."""
    pts = [(0.0, lp, EI_f), (8 * lp / 3, 3 * lp, EI_e),
           (L, lp, EI_f), (L - 8 * lp / 3, 3 * lp, EI_e)]
    Li = L - 8 * lp
    g = 1.0 / math.sqrt(3.0)
    for xi in (-g, g):
        pts.append((4 * lp + Li * (xi + 1) / 2, Li / 2, EI_e))
    assert sum(w for _x, w, _e in pts) == pytest.approx(L, rel=1e-12)
    f = sum(w * (x / L - 1.0) ** 2 / EI for (x, w, EI) in pts)
    return 1.0 / (L * L * f)


def test_fiber_hinge_initial_stiffness_closed_form():
    """Pre-cracking tip stiffness == the 6-point Radau flexibility sum.

    Hinge-section EI (uncracked at the W = 40 kN preload; the first
    2e-5 m step keeps the bending stress 35 kPa < the 246 kPa axial
    precompression):
      fc' = fc_from_E(25e6) = ((25000/4700)^2)*1000 = 28293.3 kPa,
      eps0 = 2fc'/E -> Concrete01 initial tangent = E (documented);
      the axial preload softens the parabola tangent by
      (1 - eps_g/eps0), eps_g = W/(E*A + Es*As_tot);
      I_disc = b h^3/12 (1 - 1/20^2)  (midpoint strips, exact);
      bars: 8 x rho*b*(0.9h)/3 at depths (+-0.4h x3, 0 x2) ->
      I_bars = As_bar * 6*(0.4h)^2;  EI_f = E*soft*I_disc + Es*I_bars.
    Elastic interior EI_e = E * b h^3/12; lp = 0.5h.
    (Measured agreement 0.02% — pinned at the 1% level, well inside the
    2% spec ceiling; the residual is bending-strain tangent variation.)"""
    m = _fiber_cantilever(2e-5, 3)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = OpenSeesEngine(m).run_pushover("PUSH")
    fc = fc_from_E(E_C)
    eps0 = 2.0 * fc / E_C
    nf = FIBER_HINGE_STRIPS
    bar = 0.01 * B_C * 0.9 * H_C / 3.0
    Es = 2.0e8
    eps_g = W_C / (E_C * B_C * H_C + Es * 8.0 * bar)
    soft = 1.0 - eps_g / eps0
    I_disc = B_C * H_C ** 3 / 12.0 * (1.0 - 1.0 / nf ** 2)
    I_bars = bar * 6.0 * (0.4 * H_C) ** 2
    EI_f = E_C * soft * I_disc + Es * I_bars
    EI_e = E_C * B_C * H_C ** 3 / 12.0
    K_hand = _radau_cantilever_K(EI_f, EI_e, L_C, 0.5 * H_C)
    assert r.base_shear[0] / r.roof_disp[0] == pytest.approx(K_hand,
                                                             rel=1e-2)


def test_fiber_hinge_moment_statics_states_and_criteria():
    """(a) the recorded end-i basic moment == V*L at EVERY step (statics
    on the force-based cantilever, rel 1e-5); (b) recorded states equal
    hinge_state() re-applied to the recorded curvature-based plastic
    rotation with the yield shift removed (k = inf) — the documented
    v0.21 classification theta_pl = max(0, |kappa| - My/EI)*lp against
    the Table plastic bands; (c) kappa_y == My/(E*I33) to 1e-12,
    entries carry fiber: true, lp = 0.5h, and the Table 10-7 concrete
    criteria; (d) the moment-free tip stays elastic at 2% drift while
    the base yields; theta_pl clamps at EXACTLY 0 pre-yield."""
    m = _fiber_cantilever(0.02, 200)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = OpenSeesEngine(m).run_pushover("PUSH")
    assert len(r.hinge_detail) == 2
    base = next(hd for hd in r.hinge_detail if hd["end"] == "i")
    assert base.get("fiber") is True and base["kind"] == "concrete"
    # Table 10-7 with rho = 0.01 (default) interpolates between the
    # <= 0 row (a = 0.025) and the >= 0.5 rho_bal row (a = 0.020)
    assert 0.020 <= base["a"] <= 0.025 and base["c"] == 0.2
    sec = m.sections["C40"]
    assert base["lp"] == pytest.approx(0.5 * H_C, rel=1e-12)
    assert base["kappa_y"] == pytest.approx(
        base["My"] / (E_C * sec.I33), rel=1e-12)
    n = len(base["rot"])
    for i in range(n):
        assert base["moment"][i] == pytest.approx(
            r.base_shear[i] * L_C, rel=1e-5, abs=1e-6)
    for hd in r.hinge_detail:
        for rp, st in zip(hd["rot_plastic"], hd["state"]):
            assert hinge_state(rp, hd, math.inf) == st
    # the free tip never sees moment: its hinge stays elastic even at
    # 2% drift (curvature there is ~0 — the total-rotation measure a
    # spring hinge uses would misread the chord rotation as yielding)
    tip = next(hd for hd in r.hinge_detail if hd["end"] == "j")
    assert set(tip["state"]) == {"elastic"}
    assert max(tip["rot_plastic"]) == 0.0
    # first "yield" = the cracked-section curvature crossing the
    # GROSS-EI yield curvature kappa_y: with EI_cr ~ 0.3-0.5 EI_g the
    # transition moment lands well below My (measured 0.487*My) —
    # documented conservatism of the kappa_y = My/EI_gross normalization
    iy = next(i for i, s in enumerate(base["state"]) if s != "elastic")
    assert all(rp == 0.0 for rp in base["rot_plastic"][:iy])
    assert base["rot_plastic"][iy] > 0.0
    assert 0.3 * base["My"] <= base["moment"][iy] <= 1.0 * base["My"]


def test_fiber_hinge_capacity_bracket_vs_whitney():
    """First-yield / capacity bracket via My_fiber estimated from the
    fiber section: the INDEPENDENT designer-PMM Whitney machinery gives
    the nominal Mn at P = W for the SAME section+bars; the fiber peak
    moment (Concrete01 peak fc' vs Whitney 0.85fc', mid-depth bars
    strain-compatible, 1% steel hardening) must land in [1.0, 1.20] x Mn
    (measured 1.074 — an honest model-difference bracket, documented)."""
    m = _fiber_cantilever(0.02, 200)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = OpenSeesEngine(m).run_pushover("PUSH")
    mats = {"conc": Material("conc", E_C, 0.2, 24.0),
            "bar": Material("bar", 2e8, 0.3, 77.0)}
    mats["bar"].fy = 420_000.0          # fc left to the E-inversion
    bar = 0.01 * B_C * 0.9 * H_C / 3.0
    rb = [{"y": z, "z": y, "area": bar, "material": "bar"}
          for (y, z) in ((0.4 * H_C, -0.4 * B_C), (0.4 * H_C, 0.0),
                         (0.4 * H_C, 0.4 * B_C),
                         (0.0, -0.4 * B_C), (0.0, 0.4 * B_C),
                         (-0.4 * H_C, -0.4 * B_C), (-0.4 * H_C, 0.0),
                         (-0.4 * H_C, 0.4 * B_C))]
    ds = DesignerSection("EQ", "conc",
                         polygons=[{"vertices": _rect(B_C, H_C)}], rebar=rb)
    det = pmm_surface(ds, mats, "33")["detail"]
    sweep = sorted((q["Pn"], q["Mn"]) for q in det
                   if q["label"] != "pure compression")
    (P1, M1), (P2, M2) = next(
        (a, b) for a, b in zip(sweep[:-1], sweep[1:])
        if a[0] <= W_C <= b[0])
    Mn_W = M1 + (M2 - M1) * (W_C - P1) / (P2 - P1)
    Mmax = max(r.base_shear) * L_C
    assert 1.0 <= Mmax / Mn_W <= 1.20
    # and the pushover genuinely yields: the last-quarter secant is far
    # below the initial stiffness
    K0 = r.base_shear[0] / r.roof_disp[0]
    K_end = ((r.base_shear[-1] - r.base_shear[-40])
             / (r.roof_disp[-1] - r.roof_disp[-40]))
    assert K_end < 0.10 * K0


def test_fiber_steel_w_shape_initial_stiffness():
    """W18x50 fiber_pmm cantilever (no gravity): Steel01 is LINEAR below
    Fye, so the tip stiffness equals the Radau sum with the DISCRETIZED
    flange/web inertia exactly (pinned 1e-6):
      flanges: 2 midpoint strips each, y = d/2 - 3tf/4 and d/2 - tf/4,
      area bf*tf/2 -> I_fl = bf*tf/2*(y1^2+y2^2) per flange x2;
      web: tw*hw^3/12*(1 - 1/20^2), hw = d - 2tf;
    elastic interior = the LIBRARY I33 = 800 in^4."""
    m = BuildingModel("fibw")
    m.add_material(Material("steel", E_STEEL, 0.3, 77.0))
    m.add_section(FrameSection.from_library("W18x50", "steel"))
    m.set_stories([3.0])
    m.add_member("column", "W18x50", (0, 0, 0), (0, 0, 3.0), story="S1",
                 hinges="fiber_pmm")
    m.supports.append(PointSupport((0, 0, 0), FIX))
    m.patterns["DEAD"] = LoadPattern("DEAD", "dead")
    m.add_pushover_case("PUSH", "Y", gravity={}, target_drift=1e-4,
                        steps=4, hinges="asce41")
    m.validate()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = OpenSeesEngine(m).run_pushover("PUSH")
    p = design_properties("W18x50")
    d_w, bf, tf, tw = p["d"], p["bf"], p["tf"], p["tw"]
    y1, y2 = d_w / 2 - 3 * tf / 4, d_w / 2 - tf / 4
    I_fl = bf * tf / 2.0 * (y1 ** 2 + y2 ** 2)
    hw = d_w - 2 * tf
    I_web = tw * hw ** 3 / 12.0 * (1 - 1.0 / FIBER_HINGE_STRIPS ** 2)
    EI_f = E_STEEL * (2 * I_fl + I_web)
    EI_e = E_STEEL * m.sections["W18x50"].I33
    K_hand = _radau_cantilever_K(EI_f, EI_e, 3.0, 0.5 * d_w)
    assert r.base_shear[0] / r.roof_disp[0] == pytest.approx(K_hand,
                                                             rel=1e-6)


def test_fiber_designer_section_member_runs_and_records():
    """A member whose section IS a designer section takes the designer
    fiber path: same strip closed form (per-material groups), so the
    initial stiffness matches the Radau sum with EI_e from the
    TRANSFORMED I33 and the section's own bars; M = V*L holds."""
    m = BuildingModel("fibd")
    m.add_material(Material("conc", E_C, 0.2, 24.0))
    m.add_material(Material("bar", 2e8, 0.3, 77.0))
    m.materials["bar"].fy = 420_000.0
    As = 1e-3
    rb = [{"y": 0.0, "z": 0.15, "area": As, "material": "bar"},
          {"y": 0.0, "z": -0.15, "area": As, "material": "bar"}]
    ds = DesignerSection("D1", "conc",
                         polygons=[{"vertices": _rect(B_C, H_C)}], rebar=rb)
    m.add_designer_section(ds)
    m.set_stories([L_C])
    m.add_member("column", "D1", (0, 0, 0), (0, 0, L_C), story="S1",
                 hinges="fiber_pmm")
    m.supports.append(PointSupport((0, 0, 0), FIX))
    dead = LoadPattern("DEAD", "dead")
    dead.nodal_loads.append(NodalLoad((0, 0, L_C), fz=-W_C))
    m.patterns["DEAD"] = dead
    m.add_pushover_case("PUSH", "Y", gravity={"DEAD": 1.0},
                        target_drift=2e-5, steps=3, hinges="asce41")
    m.validate()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = OpenSeesEngine(m).run_pushover("PUSH")
    fc = fc_from_E(E_C)
    eps0 = 2.0 * fc / E_C
    Es = 2.0e8
    eps_g = W_C / (E_C * B_C * H_C + Es * 2 * As)
    soft = 1.0 - eps_g / eps0
    I_disc = B_C * H_C ** 3 / 12.0 * (1 - 1.0 / FIBER_HINGE_STRIPS ** 2)
    EI_f = E_C * soft * I_disc + Es * 2 * As * 0.15 ** 2
    n = Es / E_C
    I_tr = (B_C * H_C ** 3 / 12.0 + (n - 1) * 2 * As * 0.15 ** 2)
    EI_e = E_C * I_tr
    K_hand = _radau_cantilever_K(EI_f, EI_e, L_C, 0.5 * H_C)
    assert r.base_shear[0] / r.roof_disp[0] == pytest.approx(K_hand,
                                                             rel=1e-2)
    base = next(hd for hd in r.hinge_detail if hd["end"] == "i")
    assert base.get("fiber") is True
    assert base["moment"][-1] == pytest.approx(r.base_shear[-1] * L_C,
                                               rel=1e-5)


def test_auto_m3_path_bit_identical_closed_forms():
    """auto_m3 spring hinges are untouched by v0.21: the wave20 closed
    forms re-assert bit-for-bit — initial slope 1/(L^3/3EI + L^2/k),
    k = n*6EI/L (rel 1e-6) — and two fresh engine runs return IDENTICAL
    curves (determinism guard)."""
    m = BuildingModel("cant")
    m.add_material(Material("steel", E_STEEL, 0.3, 77.0))
    m.add_section(FrameSection.from_library("W18x50", "steel"))
    m.set_stories([3.0])
    m.add_member("column", "W18x50", (0, 0, 0), (0, 0, 3), story="S1",
                 hinges="auto_m3")
    m.supports.append(PointSupport((0, 0, 0), FIX))
    m.patterns["DEAD"] = LoadPattern("DEAD", "dead")
    m.add_pushover_case("PUSH", "Y", gravity={}, target_drift=0.06,
                        steps=60, hinges="asce41")
    m.validate()
    sec = m.sections["W18x50"]
    L, E, I = 3.0, E_STEEL, sec.I33
    k = HINGE_STIFFNESS_FACTOR * 6.0 * E * I / L
    K_hand = 1.0 / (L ** 3 / (3.0 * E * I) + L ** 2 / k)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r1 = OpenSeesEngine(m).run_pushover("PUSH")
        r2 = OpenSeesEngine(m).run_pushover("PUSH")
    assert r1.base_shear[0] / r1.roof_disp[0] == pytest.approx(K_hand,
                                                               rel=1e-6)
    assert r1.base_shear == r2.base_shear
    assert r1.roof_disp == r2.roof_disp
    assert all("fiber" not in hd for hd in r1.hinge_detail)


def test_fiber_pmm_roundtrip_validation_and_warnings():
    m = _fiber_cantilever(0.01, 5)
    d = m.to_dict()
    assert d["members"][0]["hinges"] == "fiber_pmm"
    m2 = BuildingModel.from_dict(d)
    assert m2.members[0].hinges == "fiber_pmm"
    m2.members[0].hinges = "bogus"
    with pytest.raises(ValueError, match="fiber_pmm"):
        m2.validate()
    # an axial-only fiber member is skipped with a warning (left elastic)
    m3 = _fiber_cantilever(0.001, 2)
    m3.members[0].axial_limit = "tension"
    with pytest.warns(UserWarning, match="axial-only"):
        r = OpenSeesEngine(m3).run_pushover("PUSH")
    assert not r.hinge_detail


def test_concrete_backbone_fc_inversion_uses_fc_from_E():
    """v0.21 fix: an E-only concrete material's auto_m3/fiber backbone now
    uses fc' = fc_from_E(E) (the old inline formula skipped the kPa->MPa
    conversion — 1e6 too big).  auto_backbone == concrete_hinge_backbone
    evaluated at fc_from_E exactly."""
    m = BuildingModel("bb")
    m.add_material(Material("conc", 25e6, 0.2, 24.0))   # NO fc attribute
    m.add_section(FrameSection.rectangular("C", "conc", 0.3, 0.6))
    mm = m.add_member("column", "C", (0, 0, 0), (0, 0, 3), hinges="auto_m3")
    bb = auto_backbone(m, mm)
    fc = fc_from_E(25e6)
    assert fc == pytest.approx(((25e6 / 1000.0) / 4700.0) ** 2 * 1000.0,
                               rel=1e-12)
    ref = concrete_hinge_backbone(0.3, 0.6, fc, 25e6,
                                  0.3 * 0.6 ** 3 / 12.0, 3.0)
    assert bb["My"] == pytest.approx(ref["My"], rel=1e-12)
    assert bb["a"] == pytest.approx(ref["a"], rel=1e-12)


# --------------------------------------------------------------------------- #
# 6. device library II
# --------------------------------------------------------------------------- #
def _bearing_model(link_type, params, W=1000.0, E_col=2e11):
    """A vertical bearing (0,0,0)->(0,0,0.3) under a very stiff column to
    the roof at z = 3 (column tip flexibility F*2.7^3/(3*E*I) ~ 6e-9 m
    per kN at E = 2e11, I = 1/12 — negligible against the device), a
    rotation-only support at the bearing top (no rocking mechanism), and
    a W tip load held as gravity.  The bearing bottom node is a v0.15
    grounded anchor.  Pushover X returns (model,)."""
    m = BuildingModel("dev")
    m.add_material(Material("rigid", E_col, 0.3, 77.0))
    m.add_section(FrameSection.rectangular("BIG", "rigid", 1.0, 1.0))
    m.set_stories([3.0])
    m.add_member("column", "BIG", (0, 0, 0.3), (0, 0, 3.0), story="S1")
    m.supports.append(PointSupport((0, 0, 0.3), ROT_FIX))
    m.add_link((0, 0, 0), (0, 0, 0.3), link_type=link_type, params=params)
    dead = LoadPattern("DEAD", "dead")
    dead.nodal_loads.append(NodalLoad((0, 0, 3.0), fz=-W))
    m.patterns["DEAD"] = dead
    m.validate()
    return m


def _push(m, target, steps):
    m.add_pushover_case("PUSH", "X", gravity={"DEAD": 1.0},
                        target_drift=target / 3.0, steps=steps,
                        hinges="column_base")
    m.validate()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return OpenSeesEngine(m).run_pushover("PUSH")


def test_fp_isolator_static_law():
    """Single FP under constant W = 1000 kN: F = mu*W + W*d/R
    (post-slip stiffness W/R = 500 kN/m).  The roof displacement is
    corrected by the exact elastic column term F*Lc^3/(3EI) before
    evaluating the law; agreement within 1% (measured 0.03%/0.2% at
    d = 0.001/0.05 — the tiny excess is the element's exact
    large-displacement kinematics)."""
    W, R, mu = 1000.0, 2.0, 0.05
    m = _bearing_model("fp_isolator", {"R": R, "mu": mu, "P0": W}, W=W)
    r = _push(m, 0.05, 50)
    E_col, I_col, Lc = 2e11, 1.0 / 12.0, 2.7
    for i in (0, 24, 49):
        F, d_roof = r.base_shear[i], r.roof_disp[i]
        d_b = d_roof - F * Lc ** 3 / (3.0 * E_col * I_col)
        assert F == pytest.approx(mu * W + W * d_b / R, rel=1e-2)
    # post-slip tangent ~ W/R over the second half of the curve
    k_tan = ((r.base_shear[-1] - r.base_shear[24])
             / (r.roof_disp[-1] - r.roof_disp[24]))
    assert k_tan == pytest.approx(W / R, rel=2e-2)


def test_triple_fp_regimes():
    """TFP (R1 = 0.36 inner, R2 = R3 = 1.25, mu = 0.012/0.052/0.14,
    W = 1000): the FULLY-SLIDING tangent is W/(R2+R3) = 400 kN/m — the
    element reproduces it EXACTLY (asserted 0.5%; measured 400.00); the
    first-step force exceeds the inner breakaway mu1*W = 12 kN and the
    curve is monotonic increasing."""
    W = 1000.0
    prm = dict(R1=0.36, R2=1.25, R3=1.25, mu1=0.012, mu2=0.052, mu3=0.14,
               d1=0.1, d2=0.2, d3=0.2, W=W)
    m = _bearing_model("triple_fp", prm, W=W)
    r = _push(m, 0.30, 300)
    k_end = ((r.base_shear[-1] - r.base_shear[-11])
             / (r.roof_disp[-1] - r.roof_disp[-11]))
    assert k_end == pytest.approx(W / (1.25 + 1.25), rel=5e-3)
    assert r.base_shear[9] > 0.012 * W          # slid past inner breakaway
    assert all(b2 > b1 for b1, b2 in zip(r.base_shear[:-1],
                                         r.base_shear[1:]))


def test_multilinear_backbone_exact_at_points():
    """points = [[0.01, 50], [0.05, 120], [0.2, 150]]: at bearing
    displacements 0.01 / 0.03 / 0.05 the force is 50 / 85 (the exact
    midpoint interpolation (50+120)/2) / 120 kN.  The stiff-column
    correction is < 1e-6 m, so the displacement-controlled steps land on
    the backbone displacements to machine precision (asserted 1e-3)."""
    pts = [[0.01, 50.0], [0.05, 120.0], [0.2, 150.0]]
    m = _bearing_model("multilinear", {"points": pts}, W=100.0)
    r = _push(m, 0.05, 50)     # du = 0.001 -> steps 10/30/50
    assert r.roof_disp[9] == pytest.approx(0.01, rel=1e-9)
    assert r.base_shear[9] == pytest.approx(50.0, rel=1e-3)
    assert r.base_shear[29] == pytest.approx(85.0, rel=1e-3)
    assert r.base_shear[49] == pytest.approx(120.0, rel=1e-3)


def test_new_link_types_roundtrip_and_static_routing():
    m = BuildingModel("lk")
    m.add_link((0, 0, 0), (0, 0, 0.3), link_type="fp_isolator",
               params={"R": 2.0, "mu": 0.05})
    m.add_link((5, 0, 0), (5, 0, 0.3), link_type="triple_fp",
               params=dict(R1=0.36, R2=1.25, R3=1.25, mu1=0.012,
                           mu2=0.052, mu3=0.14, d1=0.1, d2=0.2, d3=0.2,
                           W=1000.0))
    m.add_link((9, 0, 0), (9, 0, 0.3), link_type="multilinear",
               params={"points": [[0.01, 50.0], [0.2, 150.0]]})
    d = m.to_dict()
    m2 = BuildingModel.from_dict(d)
    assert m2.to_dict()["links"] == d["links"]
    assert m2.links[2].params["points"] == [[0.01, 50.0], [0.2, 150.0]]
    # all three route static solves through Newton (v0.15 machinery)
    for t in ("fp_isolator", "triple_fp", "multilinear"):
        assert t in NONLINEAR_STATIC_LINK_TYPES


def test_new_link_validation_errors():
    m = BuildingModel("bad")
    cases = [
        ("fp_isolator", {"R": 2.0}, "missing required param 'mu'"),
        ("fp_isolator", {"R": 2.0, "mu": 0.05, "zap": 1.0},
         "unknown param"),
        ("fp_isolator", {"R": -2.0, "mu": 0.05}, "R must be > 0"),
        ("fp_isolator", {"R": 2.0, "mu": 0.05, "P0": 0.0}, "P0 must"),
        ("triple_fp", {"R1": 0.36}, "missing required"),
        ("triple_fp", dict(R1=0.36, R2=1.25, R3=1.25, mu1=0.012,
                           mu2=0.052, mu3=0.14, d1=0.1, d2=0.2, d3=-0.2,
                           W=1000.0), "d3 must be > 0"),
        ("multilinear", {}, "missing required param 'points'"),
        ("multilinear", {"points": [[0.01, 50.0]]}, "at least 2"),
        ("multilinear", {"points": [[0.05, 50.0], [0.01, 60.0]]},
         "strictly increasing"),
        ("multilinear", {"points": [[0.0, 50.0], [0.01, 60.0]]},
         "first backbone displacement"),
        ("multilinear", {"points": [[0.01, 50.0], [0.05, 120.0]],
                         "kv": -1.0}, "kv must be > 0"),
    ]
    for i, (ltype, prm, msg) in enumerate(cases):
        with pytest.raises(ValueError, match=msg):
            m.add_link((10.0 * i, 0, 0), (10.0 * i, 0, 0.3),
                       link_type=ltype, params=prm)
    # non-vertical axis refused (the v0.15 isolator rule)
    with pytest.raises(ValueError, match="must be vertical"):
        m.add_link((0, 0, 0), (1, 0, 0.3), link_type="fp_isolator",
                   params={"R": 2.0, "mu": 0.05})


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


def test_api_designer_upsert_pmm_delete(client):
    c, state = client
    m = BuildingModel("api")
    m.add_material(Material("conc", 25e6, 0.2, 24.0))
    m.add_material(Material("bar", 200e6, 0.3, 77.0))
    m.materials["conc"].fc = 30_000.0
    m.materials["bar"].fy = 420_000.0
    state["model"] = m
    sec = {"name": "D1", "material": "conc",
           "polygons": [{"vertices": _rect(0.3, 0.6)}],
           "rebar": [{"y": 0.0, "z": 0.24, "area": 1e-3,
                      "material": "bar"},
                     {"y": 0.0, "z": -0.24, "area": 1e-3,
                      "material": "bar"}]}
    r = c.post("/api/sections/designer",
               json={"action": "upsert", "section": sec})
    assert r.status_code == 200
    d = r.get_json()
    assert d["properties"]["A"] == pytest.approx(0.18, rel=1e-12)
    assert "D1" in d["model"]["sections"]
    assert "D1" in d["model"]["designer_sections"]
    # PMM endpoint: the same numbers as pmm_surface called directly
    r = c.post("/api/sections/designer/pmm", json={"name": "D1"})
    assert r.status_code == 200
    dd = r.get_json()
    ref = pmm_surface(state["model"].designer_sections["D1"],
                      state["model"].materials, "33")
    assert dd["base"] == "concrete" and dd["axis"] == "33"
    assert len(dd["points"]) == len(ref["points"])
    for got, want in zip(dd["points"], ref["points"]):
        assert got[0] == pytest.approx(want[0], rel=1e-12, abs=1e-9)
        assert got[1] == pytest.approx(want[1], rel=1e-12, abs=1e-9)
    assert dd["detail"][0]["c"] is None        # inf sentinel nulled
    # error paths
    assert c.post("/api/sections/designer/pmm",
                  json={"name": "NOPE"}).status_code == 400
    assert c.post("/api/sections/designer/pmm",
                  json={"name": "D1", "axis": "44"}).status_code == 400
    assert c.post("/api/sections/designer",
                  json={"action": "zap"}).status_code == 400
    bad = dict(sec, name="B",
               polygons=[{"vertices": [[0, 0], [4, 0], [4, 3], [2, -1],
                                       [0, 3]]}])
    r = c.post("/api/sections/designer",
               json={"action": "upsert", "section": bad})
    assert r.status_code == 400
    assert "self-intersecting" in r.get_json()["error"]
    # delete blocked while a member uses the section, then succeeds
    state["model"].add_member("column", "D1", (0, 0, 0), (0, 0, 3))
    r = c.post("/api/sections/designer",
               json={"action": "delete", "name": "D1"})
    assert r.status_code == 400 and "still used" in r.get_json()["error"]
    state["model"].members.clear()
    r = c.post("/api/sections/designer",
               json={"action": "delete", "name": "D1"})
    assert r.status_code == 200
    assert "D1" not in r.get_json()["model"]["sections"]
    assert c.post("/api/sections/designer",
                  json={"action": "delete", "name": "D1"}).status_code == 400


def test_api_model_roundtrips_new_fields_and_400s(client):
    c, state = client
    m = BuildingModel("rt")
    m.add_material(Material("conc", 25e6, 0.2, 24.0))
    ds = DesignerSection("D1", "conc",
                         polygons=[{"vertices": _rect(0.3, 0.6)}])
    m.add_designer_section(ds)
    m.set_stories([3.0])
    m.add_member("column", "D1", (0, 0, 0), (0, 0, 3), story="S1",
                 hinges="fiber_pmm")
    m.add_link((5, 0, 0), (5, 0, 0.3), link_type="multilinear",
               params={"points": [[0.01, 50.0], [0.2, 150.0]]})
    d = m.to_dict()
    r = c.post("/api/model", json=d)
    assert r.status_code == 200
    echo = r.get_json()
    assert echo["members"][0]["hinges"] == "fiber_pmm"
    assert echo["designer_sections"] == d["designer_sections"]
    assert echo["links"][0]["params"]["points"] == [[0.01, 50.0],
                                                    [0.2, 150.0]]
    # 400s: bad link params / bad hinges / bad designer polygons
    bad = dict(d)
    bad["links"] = [dict(d["links"][0],
                         params={"points": [[0.05, 1.0], [0.01, 2.0]]})]
    assert c.post("/api/model", json=bad).status_code == 400
    bad = dict(d)
    bad["members"] = [dict(d["members"][0], hinges="bogus")]
    assert c.post("/api/model", json=bad).status_code == 400
    bad = dict(d)
    bad["designer_sections"] = {"D1": {
        "name": "D1", "material": "conc",
        "polygons": [{"vertices": [[0, 0], [1, 0]]}], "rebar": []}}
    assert c.post("/api/model", json=bad).status_code == 400
