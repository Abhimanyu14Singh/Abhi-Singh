"""Wave 19 backend tests (v0.18):

1. Shear wall design (`skyframe.design.wall`): uniform-reinforcing pier
   PMM via strip strain compatibility — pinned against an INDEPENDENT
   closed-form smeared-steel integration (pure compression P0, balanced,
   pure bending, general c); ACI 11.5.4.3 shear (both alpha_c branches,
   the linear interpolation, and the 0.66*sqrt(fc') cap) and the
   18.10.6.3 boundary trigger, all hand-computed; full pier checks on the
   Wave-16 cantilever wall whose P/V/M free-body forces are exact.
2. Punching shear (`skyframe.design.punching`): 4-column flat plate whose
   per-column Vu = q*A/4 follows from statics + symmetry alone; b0, d,
   vu, the min-of-three Table 22.6.5.2 vc and the ratio all hand-derived;
   the 2-story axial STEP (force below minus force above).
3. Virtual-work drift diagrams (engine `run_virtual_work`): cantilever
   column — contribution sum == roof displacement == FL^3/3EI (unit-load
   theorem, machine precision); two tied cantilevers — the 2x-stiffer
   column takes exactly k_i/sum(k) = 2/3 of the total (hand: e_i =
   F_i*v_i*h^3/3EI_i = F*k_i/(sum k)^2 for fixed-free columns sharing a
   top displacement); UDL portal — Simpson over the 11 stations is exact
   for quadratic x linear moment products, so the identity still holds to
   machine precision.
4. API: POST /api/design/wall, /api/design/punching,
   /api/results/virtual-work round-trips + 400 paths.

Every expected number is hand-derived (ACI equations evaluated
longhand, closed-form beam/section mechanics, statics + symmetry),
independent of the modules under test.  Units per CONTRACT.md: kN, m,
kPa (fc' = 30 MPa = 30_000 kPa).
"""

import math

import pytest

from skyframe.core.mesh import mesh_model
from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection,
                                 Material, MemberUDL, NodalLoad,
                                 PointSupport, ShellRegion, ShellSection,
                                 StoryForce)
from skyframe.design.concrete import (EPS_CU, ES_REBAR, _radial_ratio,
                                      beta1)
from skyframe.design.punching import (check_punching, default_gravity_case,
                                      punching_capacity)
from skyframe.design.wall import (N_STRIPS, boundary_element_check,
                                  check_wall_piers, fc_from_E,
                                  summarize_walls, wall_interaction,
                                  wall_section_forces, wall_shear_strength)
from skyframe.engine.opensees_engine import OpenSeesEngine

E_CONC = 25e6           # kPa

# reference wall section for the module-level PMM tests
LW, TH, RHO_V, FC, FY = 3.0, 0.2, 0.0025, 30_000.0, 420_000.0
EPS_TY = FY / ES_REBAR                       # 0.0021
D_T = LW * (1.0 - 0.5 / N_STRIPS)            # extreme-bar depth (strip model)


# --------------------------------------------------------------------------- #
# independent closed-form smeared-steel section (the hand derivation)
# --------------------------------------------------------------------------- #
def _closed_form_PM(c, Lw=LW, t=TH, rho=RHO_V, fc=FC, fy=FY):
    """(Pn, Mn) by EXACT integration of the smeared uniform steel.

    Concrete: Whitney block 0.85*fc' over a = min(beta1*c, Lw), width t.
    Steel: line density as_l = rho*t (m^2/m), stress sigma(x) =
    clamp(Es*0.003*(c - x)/c, +-fy) — piecewise linear with yield
    boundaries x1 = c*(1 - eps_ty/0.003) (compression) and
    x2 = c*(1 + eps_ty/0.003) (tension), each segment integrated in
    closed form (constant / linear polynomials).  Moments about
    mid-length Lw/2.  This is the continuum limit of the strip model
    (midpoint rule is exact on the linear branch; only the two kink
    cells differ, at O(1/n^2)).
    """
    b1 = beta1(fc)
    a = min(b1 * c, Lw)
    Cc = 0.85 * fc * a * t
    P = Cc
    M = Cc * (Lw / 2.0 - a / 2.0)
    as_l = rho * t
    x1 = c * (1.0 - EPS_TY / EPS_CU)
    x2 = c * (1.0 + EPS_TY / EPS_CU)
    # compression-yield plateau [0, x1]
    lo, hi = 0.0, min(max(x1, 0.0), Lw)
    if hi > lo:
        P += as_l * fy * (hi - lo)
        M += as_l * fy * ((Lw / 2.0) * (hi - lo) - (hi ** 2 - lo ** 2) / 2.0)
    # elastic ramp [x1, x2]: sigma = k*(c - x), k = Es*0.003/c
    lo, hi = max(x1, 0.0), min(x2, Lw)
    if hi > lo:
        k = ES_REBAR * EPS_CU / c
        P += as_l * k * (c * (hi - lo) - (hi ** 2 - lo ** 2) / 2.0)

        def F(x):        # int (c - x)(Lw/2 - x) dx
            return (c * Lw / 2.0 * x - (c + Lw / 2.0) * x ** 2 / 2.0
                    + x ** 3 / 3.0)
        M += as_l * k * (F(hi) - F(lo))
    # tension-yield plateau [x2, Lw]
    lo, hi = max(min(x2, Lw), 0.0), Lw
    if hi > lo:
        P -= as_l * fy * (hi - lo)
        M -= as_l * fy * ((Lw / 2.0) * (hi - lo) - (hi ** 2 - lo ** 2) / 2.0)
    return P, M


# --------------------------------------------------------------------------- #
# 1a. wall PMM building blocks
# --------------------------------------------------------------------------- #
def test_wall_pure_compression_and_tension_points():
    """Diagram endpoints against the ACI formulas evaluated longhand:
    Ag = 0.6, Ast = 0.0025*0.6 = 0.0015 m^2;
    P0 = 0.85*30000*(0.6 - 0.0015) + 420000*0.0015 = 15261.75 + 630
       = 15891.75 kN; phiPn_max = 0.80*0.65*P0 = 8263.71 kN;
    pure tension phiPn = -0.9*420000*0.0015 = -567 kN."""
    pts = wall_interaction(LW, TH, RHO_V, FC, FY)
    top, bottom = pts[0], pts[-1]
    assert top["label"] == "pure compression"
    assert top["phiPn"] == pytest.approx(8263.71, rel=1e-9)
    assert top["phiMn"] == 0.0
    assert bottom["label"] == "pure tension"
    assert bottom["phiPn"] == pytest.approx(-567.0, rel=1e-9)
    assert bottom["phiMn"] == 0.0
    # every point respects the tied-column cap
    assert all(p["phiPn"] <= 8263.71 * (1 + 1e-12) for p in pts)
    # polyline P strictly descends from compression to tension
    phiPns = [p["phiPn"] for p in pts]
    assert all(a >= b - 1e-9 for a, b in zip(phiPns, phiPns[1:]))


def test_wall_balanced_point_vs_closed_form():
    """Balanced point: c_b = d_t*0.003/(0.003 + 0.0021) = d_t*(10/17);
    strip (Pn, Mn) vs the independent closed-form smeared integration to
    1e-3 rel (measured ~3e-7 — only the two kink cells differ), and
    phi = 0.65 (compression-controlled: eps_t == eps_ty exactly)."""
    c_b = D_T * EPS_CU / (EPS_CU + EPS_TY)
    pts = wall_interaction(LW, TH, RHO_V, FC, FY)
    bal = next(p for p in pts if p["label"] == "balanced")
    assert bal["c"] == pytest.approx(c_b, rel=1e-12)
    P_hand, M_hand = _closed_form_PM(c_b)
    assert bal["Pn"] == pytest.approx(P_hand, rel=1e-3)
    assert bal["Mn"] == pytest.approx(M_hand, rel=1e-3)
    assert bal["eps_t"] == pytest.approx(EPS_TY, rel=1e-12)
    assert bal["phi"] == pytest.approx(0.65, rel=1e-9)


def test_wall_pure_bending_vs_closed_form():
    """Pure bending: module bisects Pn = 0; the test bisects the
    INDEPENDENT closed form for the same root and compares c and Mn to
    1e-3 rel; the section is tension-controlled there (phi = 0.9)."""
    pts = wall_interaction(LW, TH, RHO_V, FC, FY)
    pb = next(p for p in pts if p["label"] == "pure bending")
    assert abs(pb["Pn"]) < 1e-6 * 15891.75          # a genuine root
    lo, hi = 1e-9, D_T
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        if _closed_form_PM(mid)[0] < 0.0:
            lo = mid
        else:
            hi = mid
    c_hand = 0.5 * (lo + hi)
    M_hand = _closed_form_PM(c_hand)[1]
    assert pb["c"] == pytest.approx(c_hand, rel=1e-3)
    assert pb["Mn"] == pytest.approx(M_hand, rel=1e-3)
    assert pb["phi"] == pytest.approx(0.9, rel=1e-9)
    assert pb["eps_t"] > EPS_TY + EPS_CU            # tension-controlled


@pytest.mark.parametrize("c", [0.5, 1.0, 2.0])
def test_wall_strip_forces_vs_closed_form(c):
    """General neutral-axis depths: strip integration vs the closed form
    to 1e-3 rel (measured <= 2e-6), and the extreme-bar strain equals the
    plane-section value 0.003*(d_t - c)/c exactly."""
    Pn, Mn, eps_t = wall_section_forces(c, LW, TH, RHO_V, FC, FY)
    P_hand, M_hand = _closed_form_PM(c)
    assert Pn == pytest.approx(P_hand, rel=1e-3)
    assert Mn == pytest.approx(M_hand, rel=1e-3)
    assert eps_t == pytest.approx(EPS_CU * (D_T - c) / c, rel=1e-12)


def test_wall_phi_transition_matches_aci_interpolation():
    """Every diagram point in the ACI 21.2.2 transition band carries
    phi = 0.65 + 0.25*(eps_t - eps_ty)/0.003 (hand linear interpolation);
    the sweep is dense enough to actually sample the band."""
    pts = wall_interaction(LW, TH, RHO_V, FC, FY)
    band = [p for p in pts
            if EPS_TY < p["eps_t"] < EPS_TY + EPS_CU]
    assert band, "no diagram point in the phi transition band"
    for p in band:
        phi_hand = 0.65 + (0.9 - 0.65) * (p["eps_t"] - EPS_TY) / EPS_CU
        assert p["phi"] == pytest.approx(phi_hand, rel=1e-12)


def test_radial_ratio_on_wall_diagram_pure_bending_ray():
    """A P = 0 demand at HALF the hand pure-bending design moment rates
    0.5: phiMn = 0.9*Mn(closed form) = the boundary on the M axis."""
    lo, hi = 1e-9, D_T
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        if _closed_form_PM(mid)[0] < 0.0:
            lo = mid
        else:
            hi = mid
    phiMn_hand = 0.9 * _closed_form_PM(0.5 * (lo + hi))[1]
    pts = wall_interaction(LW, TH, RHO_V, FC, FY)
    poly = [(p["phiMn"], p["phiPn"]) for p in pts]
    r = _radial_ratio(poly, 0.5 * phiMn_hand, 0.0)
    assert r == pytest.approx(0.5, rel=1e-3)


# --------------------------------------------------------------------------- #
# 1b. wall shear + boundary + fc'
# --------------------------------------------------------------------------- #
def test_wall_shear_squat_branch_hand():
    """hw/lw = 1.0 <= 1.5 -> alpha_c = 3.0 (psi).  Longhand SI:
    vc = 3*0.083*sqrt(30)*1000 = 0.249*5.477226*1000 = 1363.8291... kPa;
    vs = 0.0025*420000 = 1050 kPa; Acv = 3*0.2 = 0.6 m^2;
    Vn = 0.6*(1363.8292 + 1050) = 1448.2975 kN; cap = 0.66*sqrt(30)*1000
    *0.6 = 2168.9813 kN (not governing); phiVn = 0.75*Vn = 1086.2231."""
    sh = wall_shear_strength(LW, TH, 3.0, 0.0025, FC, FY)
    assert sh["alpha_c"] == 3.0
    assert sh["vc"] == pytest.approx(3.0 * 0.083 * math.sqrt(30.0) * 1000.0,
                                     rel=1e-12)
    assert sh["Vn"] == pytest.approx(
        0.6 * (3.0 * 0.083 * math.sqrt(30.0) * 1000.0 + 1050.0), rel=1e-12)
    assert not sh["capped"]
    assert sh["phiVn"] == pytest.approx(0.75 * sh["Vn"], rel=1e-12)
    assert sh["phiVn"] == pytest.approx(1086.2231, rel=1e-6)


def test_wall_shear_slender_branch_hand():
    """hw/lw = 9/3 = 3.0 >= 2.0 -> alpha_c = 2.0:
    Vn = 0.6*(2*0.083*sqrt(30)*1000 + 1050) = 0.6*(909.2194 + 1050)
       = 1175.5317 kN."""
    sh = wall_shear_strength(LW, TH, 9.0, 0.0025, FC, FY)
    assert sh["alpha_c"] == 2.0
    assert sh["Vn"] == pytest.approx(
        0.6 * (2.0 * 0.083 * math.sqrt(30.0) * 1000.0 + 1050.0), rel=1e-12)


def test_wall_shear_alpha_c_linear_interpolation():
    """hw/lw = 5.25/3 = 1.75, halfway across [1.5, 2.0] -> alpha_c = 2.5
    (linear 3.0 -> 2.0); and the branch ends meet the plateaus exactly."""
    assert wall_shear_strength(LW, TH, 5.25, 0.0025, FC,
                               FY)["alpha_c"] == pytest.approx(2.5, rel=1e-12)
    assert wall_shear_strength(LW, TH, 4.5, 0.0025, FC,
                               FY)["alpha_c"] == pytest.approx(3.0)
    assert wall_shear_strength(LW, TH, 6.0, 0.0025, FC,
                               FY)["alpha_c"] == pytest.approx(2.0)


def test_wall_shear_upper_bound_cap():
    """rho_h = 0.02: vs = 8400 kPa; uncapped Vn = 0.6*(1363.83 + 8400)
    = 5858.30 kN > cap = 0.66*sqrt(30)*1000*0.6 = 2168.9813 kN — the
    §18.10.4.4 bound governs."""
    sh = wall_shear_strength(LW, TH, 3.0, 0.02, FC, FY)
    assert sh["capped"]
    assert sh["Vn"] == pytest.approx(0.66 * math.sqrt(30.0) * 1000.0 * 0.6,
                                     rel=1e-12)
    assert sh["phiVn"] == pytest.approx(0.75 * sh["Vn"], rel=1e-12)


def test_boundary_trigger_hand_both_sides():
    """sigma = P/Ag + M*c/Ig with Ag = 0.6, c = 1.5, Ig = 0.2*27/12 =
    0.45; limit = 0.2*30000 = 6000 kPa.  P = 1200, M = 1200:
    sigma = 2000 + 4000 = 6000 exactly (NOT required — strict >);
    M = 1201 -> 6003.33 kPa (required)."""
    be = boundary_element_check(1200.0, 1200.0, LW, TH, FC)
    assert be["sigma"] == pytest.approx(6000.0, rel=1e-12)
    assert be["limit"] == pytest.approx(6000.0, rel=1e-12)
    assert not be["required"]
    be2 = boundary_element_check(1200.0, 1201.0, LW, TH, FC)
    assert be2["sigma"] == pytest.approx(2000.0 + 1201.0 * 1.5 / 0.45,
                                         rel=1e-12)
    assert be2["required"]
    # sign convention: |M| is used
    assert boundary_element_check(1200.0, -1201.0, LW, TH,
                                  FC)["required"]


def test_fc_from_E_hand():
    """ACI 19.2.2.1 inverted: E = 25e6 kPa = 25000 MPa ->
    fc' = (25000/4700)^2 = 28.2933 MPa = 28293.345 kPa."""
    assert fc_from_E(25e6) == pytest.approx((25000.0 / 4700.0) ** 2 * 1000.0,
                                            rel=1e-12)
    assert fc_from_E(25e6) == pytest.approx(28293.345, rel=1e-6)


# --------------------------------------------------------------------------- #
# 1c. full pier checks (engine-driven)
# --------------------------------------------------------------------------- #
def _wall_model(stories, W=3.0, mesh=0.25, pier="P1", V0=50.0, P0=100.0):
    """Cantilever shear wall (the Wave-16 fixture): fixed base, lateral V0
    (quake) + vertical P0 (dead) applied tributary-consistently on the top
    edge — the pier free-body forces are EXACT (V = V0, P = P0,
    M = V0*depth), verified in test_wave16."""
    H = sum(stories)
    mdl = BuildingModel(name="pier")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.shell_sections["SH"] = ShellSection("SH", "CONC", TH)
    mdl.set_stories(list(stories))
    mdl.shells.append(ShellRegion(
        "W1", "wall", "shell", "SH",
        [(0, 0, 0), (W, 0, 0), (W, 0, H), (0, 0, H)],
        mesh_size=mesh, story=mdl.stories[-1].name, pier=pier))
    top = sorted(p for p in mesh_model(mdl).points if abs(p[2] - H) < 1e-9)
    xs = [p[0] for p in top]
    trib = [(xs[min(i + 1, len(xs) - 1)] - xs[max(i - 1, 0)]) / 2.0
            for i in range(len(xs))]
    pv = mdl.pattern("V", "quake")
    pp = mdl.pattern("P", "dead")
    for p, tw in zip(top, trib):
        pv.nodal_loads.append(NodalLoad(p, fx=V0 * tw / W))
        pp.nodal_loads.append(NodalLoad(p, fz=-P0 * tw / W))
    mdl.add_case("V", {"V": 1.0})
    mdl.add_case("P", {"P": 1.0})
    return mdl


def test_check_wall_piers_demands_and_shear_ratio():
    """Combo U = 1.2P + 1.0V on the exact cantilever pier: P = 120,
    V = 50, M = 50*3 = 150 (1e-6); ratio_shear = 50/phiVn with phiVn
    hand-chained (hw/lw = 1 -> alpha_c = 3; fc' from E per 19.2.2.1)."""
    mdl = _wall_model([3.0])
    mdl.add_combo("U", {"P": 1.2, "V": 1.0})
    res = OpenSeesEngine(mdl).run()
    checks = check_wall_piers(mdl, res, combos=["U"])
    assert len(checks) == 1
    c = checks[0]
    assert (c.pier, c.story) == ("P1", "Story1")
    assert (c.Lw, c.t, c.hs, c.hw) == (3.0, TH, 3.0, 3.0)
    assert c.P == pytest.approx(120.0, rel=1e-6)
    assert c.V == pytest.approx(50.0, rel=1e-6)
    assert c.M == pytest.approx(150.0, rel=1e-6)
    fc_hand = (E_CONC / 1000.0 / 4700.0) ** 2 * 1000.0    # 28293.345 kPa
    assert c.fc == pytest.approx(fc_hand, rel=1e-12)
    phiVn_hand = 0.75 * 0.6 * (
        3.0 * 0.083 * math.sqrt(fc_hand / 1000.0) * 1000.0
        + 0.0025 * 420_000.0)
    assert c.alpha_c == 3.0
    assert c.phiVn == pytest.approx(phiVn_hand, rel=1e-12)
    assert c.ratio_shear == pytest.approx(50.0 / phiVn_hand, rel=1e-6)
    assert c.governing_combo == "U"
    assert c.status == "OK" and c.preliminary
    # boundary stress hand: 120/0.6 + 150*1.5/0.45 = 200 + 500 = 700 kPa,
    # far below 0.2*fc' = 5658.67 kPa
    assert c.sigma_max == pytest.approx(700.0, rel=1e-5)
    assert not c.boundary_required
    # serialisation shape (the frontend contract)
    d = c.to_dict()
    for key in ("pier", "story", "P", "V", "M", "ratio_pmm", "ratio_shear",
                "phiVn", "capacity_point", "boundary_required", "sigma_max",
                "status", "governing_combo", "pm_points"):
        assert key in d
    assert d["combo"] == d["governing_combo"]    # documented alias
    s = summarize_walls(checks)
    assert s["n"] == 1 and s["ok"] == 1 and s["preliminary"]


def test_check_wall_piers_pure_axial_pmm_ratio_hand():
    """A pure-axial combo (10 x dead, M ~ 0) rates against the capped
    compression plateau: ratio_pmm = Pu / (0.80*0.65*P0) with
    P0 = 0.85*fc'*(Ag - Ast) + fy*Ast evaluated longhand at
    fc' = 28293.345 kPa: P0 = 0.85*28293.345*0.5985 + 630 = 15021.03 kN,
    phiPn_max = 7810.94 kN -> ratio = 1000/7810.94 = 0.12803."""
    mdl = _wall_model([3.0])
    mdl.add_combo("PONLY", {"P": 10.0})
    res = OpenSeesEngine(mdl).run()
    c = check_wall_piers(mdl, res, combos=["PONLY"])[0]
    fc_hand = (E_CONC / 1000.0 / 4700.0) ** 2 * 1000.0
    Ag, Ast = 0.6, 0.0025 * 0.6
    P0 = 0.85 * fc_hand * (Ag - Ast) + 420_000.0 * Ast
    assert c.P == pytest.approx(1000.0, rel=1e-6)
    assert abs(c.M) < 1e-4                       # symmetric gravity: M ~ 0
    assert c.ratio_pmm == pytest.approx(1000.0 / (0.80 * 0.65 * P0),
                                        rel=1e-6)
    # the capacity point sits on the plateau: phiPn component = the cap
    assert c.capacity_point[1] == pytest.approx(0.80 * 0.65 * P0, rel=1e-6)


def test_check_wall_piers_boundary_envelope_over_combos():
    """sigma envelopes over ALL combos while the ratios take the governing
    one: BIG = 1.2P + 20V gives P = 120, M = 20*150 = 3000, so
    sigma = 120/0.6 + 3000*1.5/0.45 = 200 + 10000 = 10200 kPa >
    0.2*30000 = 6000 (explicit fc') -> boundary_required,
    sigma_combo = 'BIG'."""
    mdl = _wall_model([3.0])
    mdl.add_combo("U", {"P": 1.2, "V": 1.0})
    mdl.add_combo("BIG", {"P": 1.2, "V": 20.0})
    res = OpenSeesEngine(mdl).run()
    c = check_wall_piers(mdl, res, combos=["U", "BIG"], fc_prime=30_000.0)[0]
    assert c.sigma_max == pytest.approx(10200.0, rel=1e-5)
    assert c.sigma_limit == pytest.approx(6000.0, rel=1e-12)
    assert c.boundary_required
    assert c.sigma_combo == "BIG"
    assert c.governing_combo == "BIG"            # 20x shear governs too
    assert c.V == pytest.approx(1000.0, rel=1e-6)


def test_check_wall_piers_two_story_stack():
    """Stories [3, 3]: hw = 6, hw/lw = 2 -> alpha_c = 2.0 at BOTH stories;
    the base story sees M = V0*(h1+h2) = 300, story 2 sees V0*h2 = 150
    (exact free bodies, Wave-16)."""
    mdl = _wall_model([3.0, 3.0])
    mdl.add_combo("U", {"P": 1.0, "V": 1.0})
    res = OpenSeesEngine(mdl).run()
    checks = check_wall_piers(mdl, res, combos=["U"])
    by_story = {c.story: c for c in checks}
    assert set(by_story) == {"Story1", "Story2"}
    for c in checks:
        assert c.hw == pytest.approx(6.0, rel=1e-12)
        assert c.hw_over_lw == pytest.approx(2.0, rel=1e-12)
        assert c.alpha_c == 2.0
        assert c.V == pytest.approx(50.0, rel=1e-6)
    assert by_story["Story1"].M == pytest.approx(300.0, rel=1e-6)
    assert by_story["Story2"].M == pytest.approx(150.0, rel=1e-6)
    # stories are ordered bottom-up like the model
    assert [c.story for c in checks] == ["Story1", "Story2"]


def test_check_wall_piers_errors():
    """Clear errors: no pier labels; an unknown combo name; junk rho."""
    mdl = _wall_model([3.0], pier="")            # unlabeled, auto off
    res = OpenSeesEngine(mdl).run()
    with pytest.raises(ValueError, match="no wall pier forces"):
        check_wall_piers(mdl, res, combos=["V"])
    mdl2 = _wall_model([3.0])
    res2 = OpenSeesEngine(mdl2).run()
    with pytest.raises(ValueError, match="no pier forces for 'NOPE'"):
        check_wall_piers(mdl2, res2, combos=["NOPE"])
    with pytest.raises(ValueError, match="no additive combos"):
        check_wall_piers(mdl2, res2)             # model defines no combos
    with pytest.raises(ValueError, match="rho_v"):
        check_wall_piers(mdl2, res2, combos=["V"], rho_v=-0.001)
    with pytest.raises(ValueError, match="fc_prime"):
        check_wall_piers(mdl2, res2, combos=["V"], fc_prime=0.0)
    # static case names are accepted (the piers block carries them)
    assert check_wall_piers(mdl2, res2, combos=["V"])[0].governing_combo \
        == "V"


# --------------------------------------------------------------------------- #
# 2. punching shear
# --------------------------------------------------------------------------- #
B_PL, Q_PL, COL_PL, T_PL = 6.0, 10.0, 0.4, 0.2


def _flat_plate(stories=1, mesh=1.5):
    """B x B meshed shell slab(s) on 4 corner columns, area load q per
    floor.  Statics + 4-fold symmetry alone fix each column's axial step
    per floor at q*B^2/4 = 10*36/4 = 90 kN — no FE result enters the hand
    demand."""
    mdl = BuildingModel(name="plate")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", COL_PL, COL_PL))
    mdl.shell_sections["SL"] = ShellSection("SL", "CONC", T_PL)
    mdl.set_stories([3.0] * stories)
    pts = [(0, 0), (B_PL, 0), (B_PL, B_PL), (0, B_PL)]
    dead = mdl.pattern("D", "dead")
    for k in range(stories):
        z0, z1 = 3.0 * k, 3.0 * (k + 1)
        for i, (x, y) in enumerate(pts):
            mdl.add_member("column", "COL", (x, y, z0), (x, y, z1),
                           story=f"Story{k + 1}", uid=f"C{k * 4 + i + 1}")
        mdl.shells.append(ShellRegion(
            f"S{k + 1}", "slab", "shell", "SL",
            [(0, 0, z1), (B_PL, 0, z1), (B_PL, B_PL, z1), (0, B_PL, z1)],
            mesh_size=mesh, story=f"Story{k + 1}"))
        dead.area_loads.append(AreaLoad(f"S{k + 1}", Q_PL))
    mdl.add_case("GRAV", {"D": 1.0})
    return mdl


def test_punching_flat_plate_hand_numbers():
    """Full hand chain at fc' = 30 MPa, c1 = c2 = 0.4, t = 0.2:
    d = 0.2 - 0.03 = 0.17 m; b0 = 4*(0.4 + 0.17) = 2.28 m;
    Vu = q*B^2/4 = 90 kN (statics + symmetry);
    vu = 90/(2.28*0.17) = 232.1981 kPa;
    vc1 = 0.33*sqrt(30) = 1.807484 MPa; vc2 = 0.17*(1+2/1)*sqrt(30)
        = 2.793748 MPa; vc3 = 0.083*(2 + 40*0.17/2.28)*sqrt(30)
        = 0.083*4.982456*sqrt(30) = 2.264948 MPa -> min = vc1;
    phi_vc = 0.75*1807.484 = 1355.613 kPa;
    ratio = 232.1981/1355.613 = 0.171287."""
    mdl = _flat_plate()
    res = OpenSeesEngine(mdl).run()
    checks = check_punching(mdl, res, "GRAV", fc_prime=30_000.0)
    assert len(checks) == 4
    for c in checks:
        assert c.case == "GRAV" and c.story == "Story1" and c.slab == "S1"
        assert c.Vu == pytest.approx(90.0, rel=1e-9)
        assert c.d == pytest.approx(0.17, rel=1e-12)
        assert c.b0 == pytest.approx(2.28, rel=1e-12)
        assert c.beta == pytest.approx(1.0, rel=1e-12)
        assert c.vu == pytest.approx(90.0 / (2.28 * 0.17), rel=1e-9)
        vc_hand = 0.33 * math.sqrt(30.0) * 1000.0
        assert c.vc == pytest.approx(vc_hand, rel=1e-12)
        assert c.phi_vc == pytest.approx(0.75 * vc_hand, rel=1e-12)
        assert c.ratio == pytest.approx(
            (90.0 / (2.28 * 0.17)) / (0.75 * vc_hand), rel=1e-9)
        assert c.status == "OK" and c.preliminary
    d = checks[0].to_dict()
    for key in ("uid", "story", "slab", "Vu", "vu", "phi_vc", "b0", "d",
                "ratio", "status", "case"):
        assert key in d


def test_punching_capacity_min_of_three_beta_governs():
    """Elongated 0.8 x 0.2 column, d = 0.17: b0 = 2*(0.97) + 2*(0.37)
    = 2.68 m, beta = 4; at fc' = 30 MPa:
    vc1 = 0.33*sqrt(30) = 1.807484; vc2 = 0.17*(1 + 2/4)*sqrt(30)
        = 0.255*sqrt(30) = 1.396692 MPa  <-- governs;
    vc3 = 0.083*(2 + 40*0.17/2.68)*sqrt(30) = 0.083*4.537313*sqrt(30)
        = 2.062856 MPa."""
    cap = punching_capacity(0.8, 0.2, 0.17, 30_000.0)
    assert cap["b0"] == pytest.approx(2.68, rel=1e-12)
    assert cap["beta"] == pytest.approx(4.0, rel=1e-12)
    assert cap["vc2"] == pytest.approx(0.255 * math.sqrt(30.0) * 1000.0,
                                       rel=1e-12)
    assert cap["vc"] == pytest.approx(cap["vc2"], rel=1e-12)
    assert cap["vc"] < cap["vc1"] and cap["vc"] < cap["vc3"]


def test_punching_two_story_axial_step():
    """2-story stack: the Story1 joint carries C_below = 2*90 = 180 but
    C_above = 90, so Vu = 180 - 90 = 90 — the STEP, not the accumulated
    axial; the roof joint (no column above) sees the full 90."""
    mdl = _flat_plate(stories=2)
    res = OpenSeesEngine(mdl).run()
    checks = check_punching(mdl, res, "GRAV", fc_prime=30_000.0)
    assert len(checks) == 8
    by_story = {}
    for c in checks:
        by_story.setdefault(c.story, []).append(c)
    assert {s: len(v) for s, v in by_story.items()} == {"Story1": 4,
                                                        "Story2": 4}
    for c in checks:
        assert c.Vu == pytest.approx(90.0, rel=1e-9)
    # entries are keyed by the column BELOW the slab
    assert {c.uid for c in by_story["Story1"]} == {"C1", "C2", "C3", "C4"}
    assert {c.uid for c in by_story["Story2"]} == {"C5", "C6", "C7", "C8"}


def test_punching_defaults_and_errors():
    """Default case = the first DEAD-classified case; unknown case raises;
    a slab-free model returns an EMPTY list (not an error)."""
    mdl = _flat_plate()
    assert default_gravity_case(mdl) == "GRAV"
    res = OpenSeesEngine(mdl).run()
    dflt = check_punching(mdl, res, fc_prime=30_000.0)     # case = None
    assert [c.case for c in dflt] == ["GRAV"] * 4
    with pytest.raises(KeyError, match="NOPE"):
        check_punching(mdl, res, "NOPE")
    with pytest.raises(ValueError, match="cover"):
        check_punching(mdl, res, "GRAV", cover=0.0)
    # cover is METRES: a millimetre-looking value fails loudly
    with pytest.raises(ValueError, match="METRES"):
        check_punching(mdl, res, "GRAV", cover=40.0)
    # no slabs -> []
    mdl2 = BuildingModel(name="noslab")
    mdl2.rigid_diaphragms = False
    mdl2.num_modes = 0
    mdl2.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl2.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))
    mdl2.set_stories([3.0])
    mdl2.add_member("column", "COL", (0, 0, 0), (0, 0, 3),
                    story="Story1", uid="C1")
    mdl2.pattern("D", "dead").nodal_loads.append(
        NodalLoad((0, 0, 3.0), fz=-10.0))
    mdl2.add_case("GRAV", {"D": 1.0})
    res2 = OpenSeesEngine(mdl2).run()
    assert check_punching(mdl2, res2, "GRAV") == []


def test_punching_thin_slab_and_nonrect_section_na():
    """d <= 0 (cover deduction exceeds the slab) reports N/A with a note
    instead of failing."""
    mdl = _flat_plate()
    res = OpenSeesEngine(mdl).run()
    checks = check_punching(mdl, res, "GRAV", fc_prime=30_000.0,
                            cover=0.25)          # > t_slab = 0.2
    assert len(checks) == 4
    for c in checks:
        assert c.status == "N/A" and c.ratio is None
        assert any("effective depth" in n for n in c.notes)
        assert c.Vu == pytest.approx(90.0, rel=1e-9)   # demand still reported


# --------------------------------------------------------------------------- #
# 3. virtual-work drift diagrams
# --------------------------------------------------------------------------- #
def _cantilever(size=0.3, L=3.0, F=10.0):
    mdl = BuildingModel(name="vw")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.set_stories([L])
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L),
                   story="Story1", uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.pattern("F", "quake").nodal_loads.append(NodalLoad((0, 0, L), fx=F))
    mdl.add_case("F", {"F": 1.0})
    return mdl


def test_vw_cantilever_unit_load_theorem_exact():
    """Single fixed-base column, tip load F = 10: the unit-load integral
    int M m / EI dx with M = F*(L-x), m = 1*(L-x) equals F*L^3/3EI =
    10*27/(3*25e6*0.3^4/12) = 5.3333e-3 m — and that IS the roof
    displacement.  Both moment fields are linear, so Simpson over the 11
    stations is exact: sum == roof_disp == closed form to machine
    precision (rel 1e-12)."""
    out = OpenSeesEngine(_cantilever()).run_virtual_work("F", "X")
    EI = E_CONC * 0.3 ** 4 / 12.0
    d_hand = 10.0 * 3.0 ** 3 / (3.0 * EI)                 # 5.3333e-3 m
    assert set(out["contributions"]) == {"C1"}
    assert out["roof_disp"] == pytest.approx(d_hand, rel=1e-9)
    assert out["total"] == pytest.approx(out["roof_disp"], rel=1e-12)
    assert out["contributions"]["C1"] == pytest.approx(d_hand, rel=1e-9)
    assert out["case"] == "F" and out["direction"] == "X"


def test_vw_two_tied_cantilevers_stiffness_share():
    """Two fixed-base columns, tops tied by a near-rigid pin-ended strut
    (releases Mi,Mj), I2 = 2*I1 (b2 = 0.3*2^(1/4) so b2^4 = 2*0.3^4).
    Both real load F and the virtual unit load resolve into stiffness
    shares k_i/sum(k) at the common top displacement (k_i = 3EI_i/h^3,
    fixed-free), so e_i = (F*k_i/K)*(k_i/K)*h^3/(3EI_i) = F*k_i/K^2 and

        e_i / total = k_i / (k1 + k2)  ->  1/3 and 2/3;
        total = F/K = roof displacement.

    The strut's own axial term appears in the sum (its share is O(EA^-1)
    ~ 1e-12 of the total at E = 2e12), keeping the identity exact."""
    mdl = BuildingModel(name="vw2")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_material(Material("STIFF", E=2e12, nu=0.3))
    b2 = 0.3 * 2.0 ** 0.25
    mdl.add_section(FrameSection.rectangular("COL1", "CONC", 0.3, 0.3))
    mdl.add_section(FrameSection.rectangular("COL2", "CONC", b2, b2))
    mdl.add_section(FrameSection.rectangular("TIE", "STIFF", 0.5, 0.5))
    mdl.set_stories([3.0])
    mdl.add_member("column", "COL1", (0, 0, 0), (0, 0, 3),
                   story="Story1", uid="C1")
    mdl.add_member("column", "COL2", (2, 0, 0), (2, 0, 3),
                   story="Story1", uid="C2")
    mdl.add_member("beam", "TIE", (0, 0, 3), (2, 0, 3), story="Story1",
                   uid="TIE", releases="Mi,Mj")
    for x in (0.0, 2.0):
        mdl.supports.append(PointSupport((x, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.pattern("F", "quake").nodal_loads.append(
        NodalLoad((0, 0, 3), fx=10.0))
    mdl.add_case("F", {"F": 1.0})

    out = OpenSeesEngine(mdl).run_virtual_work("F", "X")
    I1 = 0.3 ** 4 / 12.0
    k1 = 3.0 * E_CONC * I1 / 27.0
    k2 = 2.0 * k1                                # I2 = 2*I1 exactly
    assert out["contributions"]["C1"] / out["total"] == pytest.approx(
        k1 / (k1 + k2), rel=1e-6)                # = 1/3
    assert out["contributions"]["C2"] / out["total"] == pytest.approx(
        k2 / (k1 + k2), rel=1e-6)                # = 2/3
    # the near-rigid strut's axial share is ~2.5e-9 of the total
    assert abs(out["contributions"]["TIE"]) < 1e-7 * out["total"]
    assert out["total"] == pytest.approx(out["roof_disp"], rel=1e-6)
    assert out["roof_disp"] == pytest.approx(10.0 / (k1 + k2), rel=1e-6)


def test_vw_portal_udl_simpson_exact():
    """Asymmetric portal (0.3 / 0.4 columns) under a full-span beam UDL:
    gravity SWAYS the frame; the real beam moment is QUADRATIC and the
    virtual one linear, so the station products are cubic — composite
    Simpson integrates cubics exactly and the unit-load identity holds to
    machine precision.  The lateral story-force case (all-linear fields)
    is exact too."""
    mdl = BuildingModel(name="vw3")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL1", "CONC", 0.3, 0.3))
    mdl.add_section(FrameSection.rectangular("COL2", "CONC", 0.4, 0.4))
    mdl.add_section(FrameSection.rectangular("BM", "CONC", 0.25, 0.45))
    mdl.set_stories([3.0])
    mdl.add_member("column", "COL1", (0, 0, 0), (0, 0, 3),
                   story="Story1", uid="C1")
    mdl.add_member("column", "COL2", (4, 0, 0), (4, 0, 3),
                   story="Story1", uid="C2")
    mdl.add_member("beam", "BM", (0, 0, 3), (4, 0, 3), story="Story1",
                   uid="B1")
    for x in (0.0, 4.0):
        mdl.supports.append(PointSupport((x, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.pattern("G", "dead").member_udls.append(MemberUDL("B1", 15.0))
    mdl.pattern("Q", "quake").story_forces.append(StoryForce("Story1",
                                                             fx=10.0))
    mdl.add_case("G", {"G": 1.0})
    mdl.add_case("Q", {"Q": 1.0})

    outq = OpenSeesEngine(mdl).run_virtual_work("Q", "X")
    assert outq["total"] == pytest.approx(outq["roof_disp"], rel=1e-12)
    assert set(outq["contributions"]) == {"C1", "C2", "B1"}
    # every element deforms: all three carry a nonzero share
    assert all(abs(e) > 0.0 for e in outq["contributions"].values())

    outg = OpenSeesEngine(mdl).run_virtual_work("G", "X")
    # tiny but genuinely nonzero sway; identity to machine precision
    assert abs(outg["total"] - outg["roof_disp"]) < 1e-15


def test_vw_direction_y_and_errors():
    """Y direction works on a y-loaded cantilever; unknown case, bad
    direction, P-Delta case, and a tension-only member all raise."""
    mdl = _cantilever()
    mdl.pattern("FY", "quake").nodal_loads.append(
        NodalLoad((0, 0, 3.0), fy=10.0))
    mdl.add_case("FY", {"FY": 1.0})
    outy = OpenSeesEngine(mdl).run_virtual_work("FY", "Y")
    assert outy["total"] == pytest.approx(outy["roof_disp"], rel=1e-12)
    assert outy["roof_disp"] == pytest.approx(
        10.0 * 27.0 / (3.0 * E_CONC * 0.3 ** 4 / 12.0), rel=1e-9)

    eng = OpenSeesEngine(_cantilever())
    with pytest.raises(ValueError, match="Unknown load case"):
        eng.run_virtual_work("NOPE", "X")
    with pytest.raises(ValueError, match="direction"):
        eng.run_virtual_work("F", "Z")
    mdl_pd = _cantilever()
    mdl_pd.add_case("FPD", {"F": 1.0}, pdelta=True)
    with pytest.raises(ValueError, match="P-Delta"):
        OpenSeesEngine(mdl_pd).run_virtual_work("FPD", "X")
    mdl_nl = _cantilever()
    mdl_nl.add_member("brace", "COL", (0, 0, 0), (0.0, 2.0, 3.0),
                      story="Story1", uid="BR", axial_limit="tension")
    with pytest.raises(ValueError, match="linear model"):
        OpenSeesEngine(mdl_nl).run_virtual_work("F", "X")


def test_vw_user_model_not_mutated():
    """run_virtual_work solves a COPY: the user's model gains no
    __VW_UNIT__ pattern/case."""
    mdl = _cantilever()
    OpenSeesEngine(mdl).run_virtual_work("F", "X")
    assert "__VW_UNIT__" not in mdl.patterns
    assert "__VW_UNIT__" not in mdl.cases


# --------------------------------------------------------------------------- #
# 4. API round-trips + 400 paths
# --------------------------------------------------------------------------- #
@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_api_design_wall(api_client):
    mdl = _wall_model([3.0])
    mdl.add_combo("U", {"P": 1.2, "V": 1.0})
    assert api_client.post("/api/model", json=mdl.to_dict()).status_code \
        == 200
    r = api_client.post("/api/design/wall", json={"combos": ["U"]})
    assert r.status_code == 200
    d = r.get_json()
    assert d["preliminary"] is True
    assert len(d["piers"]) == 1
    c = d["piers"][0]
    assert c["pier"] == "P1" and c["story"] == "Story1"
    assert c["P"] == pytest.approx(120.0, rel=1e-6)
    assert c["V"] == pytest.approx(50.0, rel=1e-6)
    assert c["status"] == "OK" and c["governing_combo"] == "U"
    assert c["combo"] == "U"                     # web-table alias
    assert d["summary"]["n"] == 1
    # default combos (none on a combo-less model) -> clear 400
    r2 = api_client.post("/api/design/wall", json={"combos": ["NOPE"]})
    assert r2.status_code == 400 and "NOPE" in r2.get_json()["error"]
    r3 = api_client.post("/api/design/wall", json={"rho_v": -1.0,
                                                   "combos": ["U"]})
    assert r3.status_code == 400 and "rho_v" in r3.get_json()["error"]
    r4 = api_client.post("/api/design/wall", json={"combos": "U"})
    assert r4.status_code == 400
    # a model without pier labels -> clear 400
    mdl2 = _wall_model([3.0], pier="")
    mdl2.add_combo("U", {"P": 1.2, "V": 1.0})
    api_client.post("/api/model", json=mdl2.to_dict())
    r5 = api_client.post("/api/design/wall", json={"combos": ["U"]})
    assert r5.status_code == 400
    assert "pier" in r5.get_json()["error"]


def test_api_design_punching(api_client):
    mdl = _flat_plate()
    assert api_client.post("/api/model", json=mdl.to_dict()).status_code \
        == 200
    r = api_client.post("/api/design/punching",
                        json={"fc_prime": 30_000.0})
    assert r.status_code == 200
    d = r.get_json()
    assert d["case"] == "GRAV"                   # DEAD-classified default
    assert len(d["columns"]) == 4
    vc_hand = 0.33 * math.sqrt(30.0) * 1000.0
    for c in d["columns"]:
        assert c["Vu"] == pytest.approx(90.0, rel=1e-9)
        assert c["ratio"] == pytest.approx(
            (90.0 / (2.28 * 0.17)) / (0.75 * vc_hand), rel=1e-9)
    r2 = api_client.post("/api/design/punching", json={"case": "NOPE"})
    assert r2.status_code == 400 and "NOPE" in r2.get_json()["error"]
    # millimetre-looking cover -> loud 400 (cover is METRES)
    r2b = api_client.post("/api/design/punching", json={"cover": 40.0})
    assert r2b.status_code == 400 and "METRES" in r2b.get_json()["error"]
    # slab-free model: empty columns, 200 (not an error)
    api_client.post("/api/model", json=_cantilever().to_dict())
    r3 = api_client.post("/api/design/punching", json={})
    assert r3.status_code == 200 and r3.get_json()["columns"] == []
    # ... but an explicit unknown case still 400s
    r4 = api_client.post("/api/design/punching", json={"case": "NOPE"})
    assert r4.status_code == 400


def test_api_virtual_work(api_client):
    mdl = _cantilever()
    assert api_client.post("/api/model", json=mdl.to_dict()).status_code \
        == 200
    r = api_client.post("/api/results/virtual-work",
                        json={"case": "F", "direction": "X"})
    assert r.status_code == 200
    d = r.get_json()
    EI = E_CONC * 0.3 ** 4 / 12.0
    assert d["roof_disp"] == pytest.approx(10.0 * 27.0 / (3.0 * EI),
                                           rel=1e-9)
    assert d["total"] == pytest.approx(d["roof_disp"], rel=1e-12)
    assert set(d["contributions"]) == {"C1"}
    assert d["case"] == "F" and d["direction"] == "X"
    r2 = api_client.post("/api/results/virtual-work",
                         json={"case": "NOPE", "direction": "X"})
    assert r2.status_code == 400
    r3 = api_client.post("/api/results/virtual-work",
                         json={"case": "F", "direction": "Z"})
    assert r3.status_code == 400 and "direction" in r3.get_json()["error"]
    r4 = api_client.post("/api/results/virtual-work", json={})
    assert r4.status_code == 400
