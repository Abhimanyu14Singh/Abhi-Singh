"""Wave 24 (v0.23) — EC3/EC2 checks, NBCC lateral, AISC 341, Cp wind, camber.

Every assertion is pinned by a hand derivation typed into the test: the
EC3 chi/phi chain and the Eq. (6.2)/(6.61) interactions are evaluated
longhand from the Table 6.1 alphas and the published W-shape imperial
dimensions, the EC2 stress-block / VRd,c / VRd,s formulas arithmetically
from their definitions, the NBCC Ce power laws and the log-T spectrum
interpolation directly, the SCWB / panel-zone joint from the E3-1 and
J10-11 formulas, the Cp pattern resultant from q*Cp*area, and the camber
rule from 0.8*delta with its rounding/thresholds.

Units per CONTRACT.md: kN, m, kPa (fy = 355 MPa = 355_000 kPa).
"""

import math
import warnings

import pytest

from skyframe.core.builder import make_shell_wind_pattern
from skyframe.core.codes import (nbcc_ce, nbcc_seismic_elf,
                                 nbcc_spectrum_value, nbcc_wind_pattern)
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 MemberLoad, PointSupport, ShellRegion,
                                 ShellSection)
from skyframe.design.composite import (camber_recommendation,
                                       check_composite_beams)
from skyframe.design.concrete import RebarLayout
from skyframe.design.concrete_ec2 import (GAMMA_C, GAMMA_S,
                                          beam_flexure_ec2,
                                          check_concrete_members_ec2,
                                          column_interaction_ec2,
                                          shear_vrdc, shear_vrds)
from skyframe.design.seismic341 import (check_seismic341,
                                        panel_zone_capacity)
from skyframe.design.steel_ec3 import (CM_SWAY, E_STEEL_EC3, buckling_chi,
                                       buckling_curve, check_members_ec3,
                                       check_members_ec3_envelope)
from skyframe.engine.opensees_engine import OpenSeesEngine

FIX = (1, 1, 1, 1, 1, 1)
FY_EC3 = 355_000.0            # kPa (S355)
_IN = 0.0254                  # m per inch (exact)

# published imperial dimensions (AISC Manual Table 1-1), converted exactly:
AS_W18 = 14.7 * _IN ** 2      # W18x50 area
D_W18 = 18.0 * _IN
TW_W18 = 0.355 * _IN
ZX_W18 = 101.0 * _IN ** 3
ZY_W18 = 16.6 * _IN ** 3
AS_W14 = 26.5 * _IN ** 2      # W14x90
D_W14 = 14.0 * _IN
BF_W14 = 14.5 * _IN
TW_W14 = 0.440 * _IN
TF_W14 = 0.710 * _IN
ZX_W14 = 157.0 * _IN ** 3


def _run(model):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return OpenSeesEngine(model).run()


# --------------------------------------------------------------------------- #
# 1. EC3 buckling curves and chi
# --------------------------------------------------------------------------- #
def test_ec3_chi_curve_b_lambda_1_longhand():
    """lambda_bar = 1.0, curve b (alpha = 0.34):
    phi = 0.5*(1 + 0.34*(1.0 - 0.2) + 1.0^2) = 0.5*2.272 = 1.136;
    chi = 1/(1.136 + sqrt(1.136^2 - 1)) = 1/(1.136 + 0.5389769...)
        = 0.5970231915935528 (computed honestly)."""
    phi = 0.5 * (1.0 + 0.34 * (1.0 - 0.2) + 1.0)
    chi = 1.0 / (phi + math.sqrt(phi ** 2 - 1.0))
    assert phi == pytest.approx(1.136, rel=1e-12)
    assert buckling_chi(1.0, "b") == pytest.approx(chi, rel=1e-12)
    assert buckling_chi(1.0, "b") == pytest.approx(0.5970231915935528,
                                                   rel=1e-12)
    # curve ordering at the same slenderness: a0 > a > b > c > d
    chis = [buckling_chi(1.0, c) for c in ("a0", "a", "b", "c", "d")]
    assert chis == sorted(chis, reverse=True)
    # plateau: chi = 1 exactly at lambda <= 0.2, and never above 1
    assert buckling_chi(0.2, "d") == 1.0
    assert buckling_chi(0.05, "a") == 1.0
    with pytest.raises(ValueError):
        buckling_chi(1.0, "e")
    with pytest.raises(ValueError):
        buckling_chi(-0.1, "b")


def test_ec3_buckling_curve_selection():
    """Table 6.2 rolled-I rule (tf <= 40 mm): W18x50 h/b = 18/7.5 = 2.4
    > 1.2 -> a (major) / b (minor); W14x90 h/b = 14/14.5 = 0.966 <= 1.2
    -> b / c."""
    assert buckling_curve(18.0, 7.5, "y") == "a"
    assert buckling_curve(18.0, 7.5, "z") == "b"
    assert buckling_curve(14.0, 14.5, "y") == "b"
    assert buckling_curve(14.0, 14.5, "z") == "c"
    with pytest.raises(ValueError):
        buckling_curve(0.4, 0.2, "x")


# --------------------------------------------------------------------------- #
# 2. EC3 member checks over a hand-built results dict
# --------------------------------------------------------------------------- #
def _steel_model():
    m = BuildingModel("ec3")
    m.add_material(Material("steel", 2.0e8, 0.3, 77.0))
    m.add_section(FrameSection.from_library("W18x50", "steel"))
    m.add_section(FrameSection.from_library("W14x90", "steel"))
    m.set_stories([3.0])
    m.add_member("beam", "W18x50", (0, 0, 3), (6, 0, 3), story="S1",
                 uid="B1")
    m.add_member("column", "W14x90", (0, 0, 0), (0, 0, 3), story="S1",
                 uid="C1")
    return m


def test_ec3_npl_mpl_vpl_exact_tension_beam():
    """W18x50 in tension (N = -100), M3 = 200, M2 = 50, V2 = 80:

    Npl,Rd  = A*fy      = 14.7 in^2 * 355 MPa  (gamma_M0 = 1)
    Mpl,y   = Zx*fy     = 101 in^3 * 355 MPa
    Mpl,z   = Zy*fy     = 16.6 in^3 * 355 MPa
    Vpl,Rd  = d*tw*fy/sqrt(3)  (Av = d*tw simplification)
    Eq. 6.2 = 100/Npl + 200/Mpl,y + 50/Mpl,z  (governing over shear);
    a TENSION member gets no 6.61/6.62 rows."""
    m = _steel_model()
    res = {"cases": {"T": {"member_forces": {
        "B1": [-100.0, 80.0, 0, 0, 0, 200.0, 0, -80.0, 0, 0, 50.0, 0.0]}}}}
    chk = {c.uid: c for c in check_members_ec3(m, res, "T")}
    b = chk["B1"]
    Npl = AS_W18 * FY_EC3
    Mply = ZX_W18 * FY_EC3
    Mplz = ZY_W18 * FY_EC3
    Vpl = D_W18 * TW_W18 * FY_EC3 / math.sqrt(3.0)
    assert b.NplRd == pytest.approx(Npl, rel=1e-12)
    assert b.MplRd33 == pytest.approx(Mply, rel=1e-12)
    assert b.MplRd22 == pytest.approx(Mplz, rel=1e-12)
    assert b.VplRd == pytest.approx(Vpl, rel=1e-12)
    r62 = 100.0 / Npl + 200.0 / Mply + 50.0 / Mplz
    assert r62 > 80.0 / Vpl                      # 6.2 governs over shear
    assert b.ratio == pytest.approx(r62, rel=1e-12)
    assert b.equation == "6.2"
    assert b.NbRd is None and b.chi_y is None    # tension: no buckling rows
    assert b.status == ("OK" if r62 <= 1.0 else "NG")
    # columns without demand in this hand case stay N/A
    assert chk["C1"].status == "N/A"


def test_ec3_compression_buckling_and_interaction_longhand():
    """W14x90 column, L = 3 m, N = +1000 kN, My(=M3) = 100 kN*m:

    NRk = A*fy; Ncr = pi^2*E*I/L^2 per axis (E = 210 GPa);
    lambda = sqrt(NRk/Ncr); h/b = 14/14.5 <= 1.2 -> curves b (y) / c (z);
    chi per the 6.3.1.2 formula; Nb,Rd = min(chi)*NRk;
    n_y = N/(chi_y*NRk); kyy = 0.9*(1 + min(lambda_y - 0.2, 0.8)*n_y);
    kzz = 0.9*(1 + min(2*lambda_z - 0.6, 1.4)*n_z); kzy = 0.6*kyy;
    (6.61) n_y + kyy*My/Mpl,y ; (6.62) n_z + kzy*My/Mpl,y — the larger
    governs (Mz = 0 here)."""
    m = _steel_model()
    res = {"cases": {"C": {"member_forces": {
        "C1": [1000.0, 0, 0, 0, 0, 100.0, -1000.0, 0, 0, 0, 0, -100.0]}}}}
    chk = {c.uid: c for c in check_members_ec3(m, res, "C")}
    c = chk["C1"]
    sec = m.sections["W14x90"]
    NRk = sec.A * FY_EC3
    lam = {}
    chi = {}
    for axis, I, curve in (("y", sec.I33, "b"), ("z", sec.I22, "c")):
        Ncr = math.pi ** 2 * E_STEEL_EC3 * I / 3.0 ** 2
        lam[axis] = math.sqrt(NRk / Ncr)
        alpha = {"b": 0.34, "c": 0.49}[curve]
        phi = 0.5 * (1 + alpha * (lam[axis] - 0.2) + lam[axis] ** 2)
        chi[axis] = min(1.0, 1.0 / (phi + math.sqrt(phi ** 2
                                                    - lam[axis] ** 2)))
    assert c.chi_y == pytest.approx(chi["y"], rel=1e-12)
    assert c.chi_z == pytest.approx(chi["z"], rel=1e-12)
    assert c.NbRd == pytest.approx(min(chi.values()) * NRk, rel=1e-12)
    n_y = 1000.0 / (chi["y"] * NRk)
    n_z = 1000.0 / (chi["z"] * NRk)
    kyy = CM_SWAY * (1.0 + min(lam["y"] - 0.2, 0.8) * n_y)
    kzz = CM_SWAY * (1.0 + min(2.0 * lam["z"] - 0.6, 1.4) * n_z)
    kzy = 0.6 * kyy
    my = 100.0 / (ZX_W14 * FY_EC3)
    r61 = n_y + kyy * my
    r62 = n_z + kzy * my
    r62x = 1000.0 / NRk + my                     # Eq. 6.2 (Mz = V = 0)
    gov, eq = max((r62x, "6.2"), (r61, "6.61"), (r62, "6.62"))
    # this stocky 3 m W14x90 has chi ~ 1 and Cm = 0.9 pulls the
    # buckling rows BELOW the plain cross-section sum -> 6.2 governs
    # (computed honestly, all three rows re-derived above)
    assert c.ratio == pytest.approx(gov, rel=1e-12)
    assert c.equation == eq
    assert kzz > 0.0 and kyy > 0.0               # exercised the B.1 rows


def test_ec3_envelope_and_shear_governing():
    """Envelope picks the larger-ratio combo per member (tag included);
    a shear-dominated case reports equation 6.2.6 with ratio V/Vpl."""
    m = _steel_model()
    Vpl = D_W18 * TW_W18 * FY_EC3 / math.sqrt(3.0)
    res = {"combos": {
        "A": {"member_forces": {
            "B1": [0.0, 0.9 * Vpl, 0, 0, 0, 10.0, 0, 0, 0, 0, 0, 0],
            "C1": [100.0] + [0.0] * 11}},
        "B": {"member_forces": {
            "B1": [0.0, 0.1 * Vpl, 0, 0, 0, 5.0, 0, 0, 0, 0, 0, 0],
            "C1": [500.0] + [0.0] * 11}},
    }}
    env = {c.uid: c for c in check_members_ec3_envelope(m, res)}
    b = env["B1"]
    assert b.governing_combo == "A"
    assert b.equation == "6.2.6"
    assert b.ratio == pytest.approx(0.9, rel=1e-9)
    assert env["C1"].governing_combo == "B"


# --------------------------------------------------------------------------- #
# 3. EC2 building blocks
# --------------------------------------------------------------------------- #
def test_ec2_beam_flexure_hand():
    """b = 0.3, d = 0.55, As = 3 D25 = 1.472622e-3 m^2, C30, fyk = 500:

    fcd = 30000/1.5 = 20000 kPa; fyd = 500000/1.15 = 434782.6 kPa;
    x = As*fyd/(0.8*0.3*20000) = 0.13339 m;
    MRd = As*fyd*(0.55 - 0.4*x) = 317.986 kN*m (computed honestly)."""
    As = 3.0 * math.pi * 0.025 ** 2 / 4.0
    fx = beam_flexure_ec2(0.3, 0.55, As, 30_000.0, 500_000.0)
    fyd = 500_000.0 / GAMMA_S
    fcd = 30_000.0 / GAMMA_C
    x = As * fyd / (0.8 * 0.3 * fcd)
    assert fx["fcd"] == pytest.approx(fcd, rel=1e-12)
    assert fx["fyd"] == pytest.approx(fyd, rel=1e-12)
    assert fx["x"] == pytest.approx(x, rel=1e-12)
    assert fx["MRd"] == pytest.approx(As * fyd * (0.55 - 0.4 * x),
                                      rel=1e-12)
    assert fx["ductile"] is (x / 0.55 <= 0.45)
    # As,min = max(0.26*fctm/fyk, 0.0013)*b*d, fctm = 0.3*30^(2/3) MPa
    fctm = 0.30 * 30.0 ** (2.0 / 3.0)
    As_min = max(0.26 * fctm / 500.0, 0.0013) * 0.3 * 0.55
    assert fx["As_min"] == pytest.approx(As_min, rel=1e-12)
    with pytest.raises(ValueError):
        beam_flexure_ec2(0.3, 0.55, As, 60_000.0, 500_000.0)  # > C50


def test_ec2_vrdc_longhand():
    """b = 0.3, d = 0.55 m (550 mm), Asl = 1.472622e-3, C30:

    k = 1 + sqrt(200/550) = 1.603023 (< 2); rho_l = 8.9250e-3 (< 0.02);
    v = 0.12*k*(100*rho_l*30)^(1/3) MPa; vmin = 0.035*k^1.5*sqrt(30);
    VRd,c = max(v, vmin)*1000*b*d kN — the rho branch governs here.
    Thin-member clamps: d = 0.04 m -> k clamps to 2.0 exactly; a huge
    Asl clamps rho_l to 0.02."""
    As = 3.0 * math.pi * 0.025 ** 2 / 4.0
    vc = shear_vrdc(0.3, 0.55, As, 30_000.0)
    k = 1.0 + math.sqrt(200.0 / 550.0)
    rho = As / (0.3 * 0.55)
    v = 0.12 * k * (100.0 * rho * 30.0) ** (1.0 / 3.0)
    vmin = 0.035 * k ** 1.5 * math.sqrt(30.0)
    assert vc["k"] == pytest.approx(k, rel=1e-12)
    assert vc["rho_l"] == pytest.approx(rho, rel=1e-12)
    assert v > vmin
    assert vc["governed_by_vmin"] is False
    assert vc["VRdc"] == pytest.approx(v * 1000.0 * 0.3 * 0.55, rel=1e-12)
    assert shear_vrdc(0.3, 0.04, As, 30_000.0)["k"] == 2.0
    assert shear_vrdc(0.3, 0.55, 1.0, 30_000.0)["rho_l"] == 0.02
    # rho = 0: the vmin floor takes over (v = 0)
    vc0 = shear_vrdc(0.3, 0.55, 0.0, 30_000.0)
    assert vc0["governed_by_vmin"] is True
    assert vc0["VRdc"] == pytest.approx(vmin * 1000.0 * 0.3 * 0.55,
                                        rel=1e-12)


def test_ec2_vrds_longhand():
    """2-leg D10 @ 150, d = 0.55, fywk = 500, C30, cot(theta) = 2.5:

    Asw = 2*pi*0.01^2/4 = 1.570796e-4; z = 0.495; fywd = 434782.6;
    VRd,s = (Asw/0.15)*0.495*fywd*2.5 = 563.44 kN;
    VRd,max = 0.3*0.495*nu1*fcd/(2.5 + 0.4), nu1 = 0.6*(1 - 30/250)
            = 0.528 -> 540.74 kN < VRd,s -> crushing governs."""
    Asw = 2.0 * math.pi * 0.01 ** 2 / 4.0
    vs = shear_vrds(0.3, 0.55, Asw, 0.15, 30_000.0, 500_000.0)
    z = 0.9 * 0.55
    fywd = 500_000.0 / GAMMA_S
    VRds = (Asw / 0.15) * z * fywd * 2.5
    nu1 = 0.6 * (1.0 - 30.0 / 250.0)
    VRdmax = 0.3 * z * nu1 * (30_000.0 / GAMMA_C) / (2.5 + 1.0 / 2.5)
    assert vs["VRds"] == pytest.approx(VRds, rel=1e-12)
    assert vs["VRdmax"] == pytest.approx(VRdmax, rel=1e-12)
    assert VRds > VRdmax and vs["crushing"] is True
    assert vs["VRd"] == pytest.approx(VRdmax, rel=1e-12)
    # light stirrups: no crushing, VRd = VRd,s
    vs2 = shear_vrds(0.3, 0.55, Asw, 0.30, 30_000.0, 500_000.0)
    assert vs2["crushing"] is False
    assert vs2["VRd"] == pytest.approx(VRds / 2.0, rel=1e-12)


def test_ec2_column_interaction_endpoints():
    """0.4 x 0.4 column, 4 D20/face, d = 0.34, d' = 0.06, C30, fyk 500:

    Ast = 8 D20 = 2.513274e-3 m^2 (both faces);
    pure compression NRd0 = fcd*(Ag - Ast) + fyd*Ast (no 0.80 cap, no
    phi); pure tension = -fyd*Ast; balanced c = d*0.0035/(0.0035 + eps_yd)
    with eps_yd = fyd/200e6; pure bending NRd = 0 (bisected)."""
    As_face = 4.0 * math.pi * 0.02 ** 2 / 4.0
    pts = column_interaction_ec2(0.4, 0.4, 0.34, 0.06, As_face,
                                 30_000.0, 500_000.0)
    fcd = 30_000.0 / GAMMA_C
    fyd = 500_000.0 / GAMMA_S
    Ast = 2.0 * As_face
    labels = [p["label"] for p in pts]
    assert labels == ["pure compression", "eps_t = 0", "balanced",
                      "pure bending", "pure tension"]
    assert pts[0]["NRd"] == pytest.approx(
        fcd * (0.16 - Ast) + fyd * Ast, rel=1e-12)
    assert pts[0]["MRd"] == 0.0
    eps_yd = fyd / 200_000_000.0
    assert pts[2]["c"] == pytest.approx(
        0.34 * 0.0035 / (0.0035 + eps_yd), rel=1e-12)
    assert abs(pts[3]["NRd"]) < 1e-6 * pts[0]["NRd"]     # bisected to ~0
    assert pts[4]["NRd"] == pytest.approx(-fyd * Ast, rel=1e-12)
    # NRd is monotonically decreasing from pure compression to tension
    ns = [p["NRd"] for p in pts]
    assert ns == sorted(ns, reverse=True)


def test_ec2_member_checks_over_hand_dict():
    """Beam ratio = MEd/MRd exact; column rated on the EC2 diagram; the
    As,min violation flags NG."""
    m = BuildingModel("ec2")
    m.add_material(Material("conc", 25e6, 0.2, 24.0))
    m.add_section(FrameSection.rectangular("B3060", "conc", 0.3, 0.6))
    m.add_section(FrameSection.rectangular("C40", "conc", 0.4, 0.4))
    m.set_stories([3.0])
    m.add_member("beam", "B3060", (0, 0, 3), (6, 0, 3), story="S1",
                 uid="B1")
    m.add_member("column", "C40", (0, 0, 0), (0, 0, 3), story="S1",
                 uid="C1")
    lay_b = RebarLayout(n_top=2, n_bot=3, bar_dia=0.025, fy=500_000.0)
    lay_c = RebarLayout(n_top=4, n_bot=4, bar_dia=0.020, fy=500_000.0)
    res = {"cases": {"G": {
        "member_forces": {"B1": [0.0] * 12, "C1": [800.0, 0, 0, 0, 0, 60.0,
                                                   -800.0, 0, 0, 0, 0, 0]},
        "member_stations": {"B1": {"M3": [120.0, -80.0],
                                   "V2": [90.0, -90.0],
                                   "M2": [0.0, 0.0]}}}}}
    chks = {c.uid: c for c in check_concrete_members_ec2(
        m, res, "G", {"B1": lay_b, "C1": lay_c}, fck=30_000.0)}
    b = chks["B1"]
    d = 0.6 - 0.04 - 0.010 - 0.025 / 2.0            # RebarLayout.d_eff
    As3 = 3.0 * math.pi * 0.025 ** 2 / 4.0
    fx = beam_flexure_ec2(0.3, d, As3, 30_000.0, 500_000.0)
    assert b.phiMn_pos == pytest.approx(fx["MRd"], rel=1e-12)
    # governing: sagging flexure vs shear vs hogging, computed honestly
    assert b.ratio is not None and b.status in ("OK", "NG")
    assert b.equation in ("flexure(+)", "flexure(-)", "shear")
    c = chks["C1"]
    assert c.equation == "P-M" and c.ratio is not None
    assert c.pm_points[0][1] > 0.0 and c.pm_points[-1][1] < 0.0
    # starving the beam of bottom steel trips the EC2 9.2.1.1 minimum
    lay_thin = RebarLayout(n_top=2, n_bot=1, bar_dia=0.008, fy=500_000.0)
    thin = check_concrete_members_ec2(m, res, "G", {"B1": lay_thin},
                                      fck=30_000.0)[0]
    assert thin.status == "NG"
    assert any("As,min" in n for n in thin.notes)


# --------------------------------------------------------------------------- #
# 4. NBCC lateral
# --------------------------------------------------------------------------- #
def test_nbcc_ce_power_laws():
    """open: Ce = (h/10)^0.2 floored at 0.9 (crosses 1.0 at h = 10);
    rough: Ce = 0.7*(h/12)^0.3 floored at 0.7 (equals 0.7 at h = 12)."""
    assert nbcc_ce(10.0, "open") == pytest.approx(1.0, rel=1e-12)
    assert nbcc_ce(20.0, "open") == pytest.approx(2.0 ** 0.2, rel=1e-12)
    assert nbcc_ce(2.0, "open") == 0.9                  # floor
    assert nbcc_ce(12.0, "rough") == pytest.approx(0.7, rel=1e-12)
    assert nbcc_ce(24.0, "rough") == pytest.approx(0.7 * 2.0 ** 0.3,
                                                   rel=1e-12)
    assert nbcc_ce(1.0, "rough") == 0.7                 # floor
    with pytest.raises(ValueError):
        nbcc_ce(10.0, "C")


def test_nbcc_spectrum_log_interpolation():
    """Sa = (0.9, 0.6, 0.4, 0.2) at T = (0.2, 0.5, 1.0, 2.0):

    T <= 0.2 -> 0.9 (plateau); T = 0.7 -> log-linear between (0.5, 0.6)
    and (1.0, 0.4): f = ln(0.7/0.5)/ln(2) = 0.485427,
    S = 0.6 - 0.2*f = 0.502915; exact at the ordinates; T = 4 ->
    0.2*(2/4) = 0.1 (1/T decay)."""
    args = (0.9, 0.6, 0.4, 0.2)
    assert nbcc_spectrum_value(0.05, *args) == 0.9
    assert nbcc_spectrum_value(0.2, *args) == 0.9
    for T, S in ((0.5, 0.6), (1.0, 0.4), (2.0, 0.2)):
        assert nbcc_spectrum_value(T, *args) == pytest.approx(S, rel=1e-12)
    f = math.log(0.7 / 0.5) / math.log(1.0 / 0.5)
    assert nbcc_spectrum_value(0.7, *args) == pytest.approx(
        0.6 + (0.4 - 0.6) * f, rel=1e-12)
    assert nbcc_spectrum_value(4.0, *args) == pytest.approx(0.1, rel=1e-12)
    with pytest.raises(ValueError):
        nbcc_spectrum_value(-1.0, *args)
    with pytest.raises(ValueError):
        nbcc_spectrum_value(1.0, -0.9, 0.6, 0.4, 0.2)


def _two_story_model():
    """Two stories (3 m each), explicit masses 100/50 t, 10 x 6 m plan."""
    m = BuildingModel("nbcc")
    m.add_material(Material("steel", 2.0e8, 0.3, 77.0))
    m.add_section(FrameSection.from_library("W14x90", "steel"))
    m.set_stories([3.0, 3.0], names=["S1", "S2"])
    for x, y in ((0, 0), (10, 0), (10, 6), (0, 6)):
        m.add_member("column", "W14x90", (x, y, 0), (x, y, 3), story="S1")
        m.add_member("column", "W14x90", (x, y, 3), (x, y, 6), story="S2")
    m.story_masses = {"S1": 100.0, "S2": 50.0}
    return m


def test_nbcc_elf_base_shear_distribution_and_caps():
    """hn = 6 m, system other: Ta = 0.05*6^0.75 = 0.1917 s < 0.2 ->
    S(Ta) = Sa02 (plateau).  W = 150 t * g; RdRo = 3.9, Ie = 1:

    V0 = 0.9*W/3.9;  cap = max(2/3*0.9, 0.6)*W/3.9 = 0.6*W/3.9 GOVERNS
    (2/3*0.9 = 0.6 exactly ties Sa05 = 0.6); floor = 0.2*W/3.9 (inactive).
    Ta < 0.7 -> Ft = 0; distribution w*h: S1 100*3 = 300, S2 50*6 = 300
    -> equal halves."""
    m = _two_story_model()
    G = 9.80665
    W = 150.0 * G
    pat = nbcc_seismic_elf(m, 0.9, 0.6, 0.4, 0.2, 3.9)
    V = sum(sf.fx for sf in pat.story_forces)
    V_cap = max(2.0 / 3.0 * 0.9, 0.6) * W / 3.9
    assert V == pytest.approx(V_cap, rel=1e-12)          # cap governs
    fx = {sf.story: sf.fx for sf in pat.story_forces}
    assert fx["S1"] == pytest.approx(V / 2.0, rel=1e-12)
    assert fx["S2"] == pytest.approx(V / 2.0, rel=1e-12)
    assert m.patterns["NELF"].kind == "quake"
    # floor: hn = 160 m ('other') -> Ta = 0.05*160^0.75 = 2.2493 s > 2
    # -> S(Ta) = 0.2*(2/Ta) = 0.17783 < Sa20 -> lifted to the S(2.0)
    # floor V_min = 0.2*W/3.9 (the cap 0.6*W/3.9 stays inactive).
    m2 = _two_story_model()
    m2.set_stories([80.0, 80.0], names=["S1", "S2"])
    pat2 = nbcc_seismic_elf(m2, 0.9, 0.6, 0.4, 0.2, 3.9, name="N2")
    Ta2 = 0.05 * 160.0 ** 0.75
    assert Ta2 > 2.0
    assert nbcc_spectrum_value(Ta2, 0.9, 0.6, 0.4, 0.2) < 0.2
    V2 = sum(sf.fx for sf in pat2.story_forces)
    assert V2 == pytest.approx(0.2 * W / 3.9, rel=1e-12)
    with pytest.raises(ValueError):
        nbcc_seismic_elf(m, 0.9, 0.6, 0.4, 0.2, 3.9, system="brick")


def test_nbcc_elf_top_force_ft():
    """A tall (hn = 60 m, 10 x 6 m stories via explicit masses) 'other'
    system: Ta = 0.05*60^0.75 = 1.0779 s > 0.7 -> Ft = 0.07*Ta*V
    (< 0.25V); the top story takes Ft + its w*h share of (V - Ft); the
    story forces still sum to V exactly."""
    m = BuildingModel("tall")
    m.add_material(Material("steel", 2.0e8, 0.3, 77.0))
    m.add_section(FrameSection.from_library("W14x90", "steel"))
    m.set_stories([6.0] * 10,
                  names=[f"S{i + 1}" for i in range(10)])   # hn = 60 m
    for x, y in ((0, 0), (10, 0)):
        for i in range(10):
            m.add_member("column", "W14x90", (x, y, 6.0 * i),
                         (x, y, 6.0 * (i + 1)), story=f"S{i + 1}")
    m.story_masses = {f"S{i + 1}": 100.0 for i in range(10)}
    pat = nbcc_seismic_elf(m, 0.9, 0.6, 0.4, 0.2, 3.9, direction="Y")
    Ta = 0.05 * 60.0 ** 0.75
    S = nbcc_spectrum_value(Ta, 0.9, 0.6, 0.4, 0.2)
    G = 9.80665
    W = 1000.0 * G
    V = S * W / 3.9
    V = min(max(V, 0.2 * W / 3.9), max(0.6, 0.6) * W / 3.9)
    Ft = min(0.07 * Ta * V, 0.25 * V)
    assert Ft > 0.0
    fy = {sf.story: sf.fy for sf in pat.story_forces}
    assert sum(fy.values()) == pytest.approx(V, rel=1e-12)
    wh = {f"S{i + 1}": 100.0 * G * 6.0 * (i + 1) for i in range(10)}
    den = sum(wh.values())
    assert fy["S10"] == pytest.approx(Ft + (V - Ft) * wh["S10"] / den,
                                      rel=1e-12)
    assert fy["S1"] == pytest.approx((V - Ft) * wh["S1"] / den, rel=1e-12)
    assert all(sf.fx == 0.0 for sf in pat.story_forces)  # Y direction


def test_nbcc_wind_pattern_story_forces():
    """Two-story 10 x 6 plan, q = 0.5 kPa, open, wind X:

    width (perp. to X) = 6 m; trib heights 3.0 (S1: 1.5 + 1.5) and
    1.5 (S2); p(z) = q*Ce(z)*2.0*1.3 with Ce(3) = 0.9 (floor) and
    Ce(6) = (0.6)^0.2; F = p*trib*width."""
    m = _two_story_model()
    pat = nbcc_wind_pattern(m, 0.5, exposure="open", direction="X")
    fx = {sf.story: sf.fx for sf in pat.story_forces}
    p1 = 0.5 * 0.9 * 2.0 * 1.3
    p2 = 0.5 * (6.0 / 10.0) ** 0.2 * 2.0 * 1.3
    assert fx["S1"] == pytest.approx(p1 * 3.0 * 6.0, rel=1e-12)
    assert fx["S2"] == pytest.approx(p2 * 1.5 * 6.0, rel=1e-12)
    assert m.patterns["NWIND"].kind == "wind"
    with pytest.raises(ValueError):
        nbcc_wind_pattern(m, -1.0)
    with pytest.raises(ValueError):
        nbcc_wind_pattern(m, 0.5, exposure="B")


# --------------------------------------------------------------------------- #
# 5. AISC 341: SCWB + panel zone
# --------------------------------------------------------------------------- #
def _smf_model():
    """One W14x90 column with a W18x50 beam framing in at z = 3."""
    m = BuildingModel("smf")
    m.add_material(Material("steel", 2.0e8, 0.3, 77.0))
    m.add_section(FrameSection.from_library("W14x90", "steel"))
    m.add_section(FrameSection.from_library("W18x50", "steel"))
    m.set_stories([3.0])
    m.add_member("column", "W14x90", (0, 0, 0), (0, 0, 3), story="S1",
                 uid="C1")
    m.add_member("beam", "W18x50", (0, 0, 3), (6, 0, 3), story="S1",
                 uid="B1")
    return m


def test_scwb_ratio_hand_joint():
    """E3-1 at (0, 0, 3), Puc = 500 kN:

    Mpc* = Zc*(Fy - Puc/Ag) = 157 in^3 * (345000 - 500/(26.5 in^2));
    Mpb* = 1.1*1.1*345000*101 in^3; ratio = Mpc*/Mpb* = 1.175773...
    (computed honestly).  Puc = 0 (tension clamps to 0) raises the
    ratio to Zc*Fy/Mpb*."""
    m = _smf_model()
    res = {"cases": {"E": {"member_forces": {
        "C1": [500.0] + [0.0] * 11, "B1": [0.0] * 12}}}}
    chks = check_seismic341(m, res, "E")
    assert len(chks) == 1
    j = chks[0]
    assert tuple(j.point) == (0.0, 0.0, 3.0)
    Mpc = ZX_W14 * (345_000.0 - 500.0 / AS_W14)
    Mpb = 1.1 * 1.1 * 345_000.0 * ZX_W18
    assert j.sum_Mpc == pytest.approx(Mpc, rel=1e-12)
    assert j.sum_Mpb == pytest.approx(Mpb, rel=1e-12)
    assert j.scwb_ratio == pytest.approx(Mpc / Mpb, rel=1e-12)
    assert j.scwb_ratio == pytest.approx(1.1757732622985737, rel=1e-9)
    # tension demand clamps Puc to zero
    res2 = {"cases": {"E": {"member_forces": {
        "C1": [-300.0] + [0.0] * 11, "B1": [0.0] * 12}}}}
    j2 = check_seismic341(m, res2, "E")[0]
    assert j2.sum_Mpc == pytest.approx(ZX_W14 * 345_000.0, rel=1e-12)


def test_panel_zone_capacity_formula_exact():
    """J10-11 longhand for the W14x90 / W18x50 joint (phi = 1):

    Rn = 0.6*Fy*dc*tw*(1 + 3*bcf*tcf^2/(db*dc*tw)) with dc/tw/bcf/tcf
    the column and db the beam depth; the demand is
    Ru = sum(Mpb*)/(db - tf_beam)."""
    db = 18.0 * _IN
    tfb = 0.570 * _IN
    Rn = 0.6 * 345_000.0 * D_W14 * TW_W14 * (
        1.0 + 3.0 * BF_W14 * TF_W14 ** 2 / (db * D_W14 * TW_W14))
    assert panel_zone_capacity(345_000.0, D_W14, TW_W14, BF_W14, TF_W14,
                               db) == pytest.approx(Rn, rel=1e-12)
    m = _smf_model()
    res = {"cases": {"E": {"member_forces": {
        "C1": [500.0] + [0.0] * 11, "B1": [0.0] * 12}}}}
    j = check_seismic341(m, res, "E")[0]
    Mpb = 1.1 * 1.1 * 345_000.0 * ZX_W18
    assert j.pz_demand == pytest.approx(Mpb / (db - tfb), rel=1e-12)
    assert j.pz_capacity == pytest.approx(Rn, rel=1e-12)
    assert j.pz_ratio == pytest.approx(j.pz_demand / Rn, rel=1e-12)
    # this joint passes SCWB but fails the (doubler-free) panel zone
    assert j.scwb_ratio > 1.0 and j.pz_ratio > 1.0
    assert j.status == "NG"
    with pytest.raises(ValueError):
        panel_zone_capacity(345_000.0, 0.0, TW_W14, BF_W14, TF_W14, db)


def test_seismic341_designation_and_na():
    """columns=[uids] restricts the joint set; unknown uids raise;
    a non-W-shape beam turns the joint N/A with a note."""
    m = _smf_model()
    m.add_member("column", "W14x90", (6, 0, 0), (6, 0, 3), story="S1",
                 uid="C2")
    res = {"cases": {"E": {"member_forces": {
        "C1": [500.0] + [0.0] * 11, "C2": [500.0] + [0.0] * 11,
        "B1": [0.0] * 12}}}}
    assert len(check_seismic341(m, res, "E", columns="auto")) == 2
    only = check_seismic341(m, res, "E", columns=["C2"])
    assert len(only) == 1 and tuple(only[0].point) == (6.0, 0.0, 3.0)
    with pytest.raises(ValueError):
        check_seismic341(m, res, "E", columns=["B1"])
    with pytest.raises(ValueError):
        check_seismic341(m, res, "E", columns="all")
    # a rectangular beam section is not checkable -> joint N/A
    m2 = _smf_model()
    m2.add_section(FrameSection.rectangular("RB", "steel", 0.3, 0.6))
    m2.members[1].section = "RB"
    res2 = {"cases": {"E": {"member_forces": {
        "C1": [500.0] + [0.0] * 11, "B1": [0.0] * 12}}}}
    j = check_seismic341(m2, res2, "E")[0]
    assert j.status == "N/A"
    assert any("not a library W-shape" in n for n in j.notes)


# --------------------------------------------------------------------------- #
# 6. Cp wind on shells
# --------------------------------------------------------------------------- #
def _wall_model(cp=0.8):
    """4 x 3 x 0.2 m shell wall in the XZ plane, base fixed; the corner
    ordering (0,0,0)->(4,0,0)->(4,0,3)->(0,0,3) gives the plane normal
    (c1-c0) x (c3-c0) = (4,0,0) x (0,0,3) = (0, -12, 0) -> unit -Y."""
    m = BuildingModel("cpw")
    m.rigid_diaphragms = False
    m.num_modes = 0
    m.add_material(Material("conc", 25e6, 0.2, 24.0))
    m.shell_sections["W20"] = ShellSection("W20", "conc", 0.2)
    m.set_stories([3.0])
    m.shells.append(ShellRegion("W1", "wall", "shell", "W20",
                                [(0, 0, 0), (4, 0, 0), (4, 0, 3),
                                 (0, 0, 3)], mesh_size=1.0, story="S1",
                                wind_cp=cp))
    return m


def test_shell_wind_wall_resultant_exact():
    """q = 0.5, Cp = 0.8, area 12 m^2: the nodal loads sum to EXACTLY
    q*Cp*area = 4.8 kN along the region normal (0, -1, 0) — the mesh
    tributary areas sum to the meshed area exactly."""
    m = _wall_model()
    pat = make_shell_wind_pattern(m, 0.5, name="CPW")
    assert pat.kind == "wind" and "CPW" in m.patterns
    tot = [sum(getattr(nl, k) for nl in pat.nodal_loads)
           for k in ("fx", "fy", "fz")]
    assert tot[0] == pytest.approx(0.0, abs=1e-12)
    assert tot[1] == pytest.approx(-0.5 * 0.8 * 12.0, rel=1e-12)
    assert tot[2] == pytest.approx(0.0, abs=1e-12)
    # 5 x 4 grid of mesh nodes at mesh_size 1.0
    assert len(pat.nodal_loads) == 20
    # a negative Cp flips the direction (suction)
    m2 = _wall_model(cp=-0.5)
    pat2 = make_shell_wind_pattern(m2, 0.5, name="CPS")
    assert sum(nl.fy for nl in pat2.nodal_loads) == pytest.approx(
        +0.5 * 0.5 * 12.0, rel=1e-12)


def test_shell_wind_slab_area_load_and_errors():
    """A slab with wind_cp gets ONE AreaLoad of q*Cp (positive =
    downward); no-cp models and bad q raise; wind_cp validates and
    round-trips."""
    m = _wall_model()
    m.shell_sections["SL"] = ShellSection("SL", "conc", 0.15)
    m.shells.append(ShellRegion("R1", "slab", "shell", "SL",
                                [(0, 0, 3), (4, 0, 3), (4, 3, 3),
                                 (0, 3, 3)], mesh_size=1.0, story="S1",
                                wind_cp=-0.7))
    pat = make_shell_wind_pattern(m, 0.5)
    als = [al for al in pat.area_loads if al.region_uid == "R1"]
    assert len(als) == 1
    assert als[0].q == pytest.approx(0.5 * -0.7, rel=1e-12)
    # round trip
    m2 = BuildingModel.from_dict(m.to_dict())
    assert m2.shells[0].wind_cp == pytest.approx(0.8)
    assert m2.shells[1].wind_cp == pytest.approx(-0.7)
    m2.validate()
    # validation: non-numeric wind_cp
    m2.shells[0].wind_cp = "big"
    with pytest.raises(ValueError):
        m2.validate()
    with pytest.raises(ValueError):
        make_shell_wind_pattern(m, 0.0)
    m3 = BuildingModel("nocp")
    with pytest.raises(ValueError):
        make_shell_wind_pattern(m3, 0.5)


def test_shell_wind_engine_equilibrium():
    """Running the Cp pattern through the engine: the base reactions
    balance the exact resultant (sum FY = +q*Cp*area on the supports
    for the -Y load)."""
    m = _wall_model()
    for x in (0.0, 1.0, 2.0, 3.0, 4.0):
        m.supports.append(PointSupport((x, 0, 0), FIX))
    make_shell_wind_pattern(m, 0.5, name="CPW")
    m.add_case("CPW", {"CPW": 1.0})
    m.validate()
    res = _run(m).to_dict()
    reac = res["cases"]["CPW"]["reactions"]
    fy = sum(r[1] for r in reac.values())
    assert fy == pytest.approx(0.5 * 0.8 * 12.0, rel=1e-9)


# --------------------------------------------------------------------------- #
# 7. composite camber
# --------------------------------------------------------------------------- #
def test_camber_rules_hand():
    """0.8*delta floored to 5 mm steps, zero < 20 mm or span < 7.5 m:

    delta = 43 mm -> 0.8*43 = 34.4 -> 30 mm; span 7.0 -> 0;
    delta = 30 mm -> 24 -> 20 mm (>= threshold);
    delta = 20 mm -> 16 -> 15 < 20 -> 0;
    delta = 25 mm -> 20.0 exactly -> 20 mm (floor keeps the exact step);
    span 7.5 exactly IS cambered (>= threshold)."""
    assert camber_recommendation(0.043, 9.0) == pytest.approx(0.030)
    assert camber_recommendation(0.043, 7.0) == 0.0
    assert camber_recommendation(0.030, 9.0) == pytest.approx(0.020)
    assert camber_recommendation(0.020, 9.0) == 0.0
    assert camber_recommendation(0.025, 9.0) == pytest.approx(0.020)
    assert camber_recommendation(0.043, 7.5) == pytest.approx(0.030)
    assert camber_recommendation(0.0, 9.0) == 0.0
    with pytest.raises(ValueError):
        camber_recommendation(-0.01, 9.0)
    with pytest.raises(ValueError):
        camber_recommendation(0.02, 0.0)


def _camber_model(span, w_dead):
    """Simply supported W18x50 under a one-quad slab (mesh_size large so
    the beams never split — statics-exact demands, like test_wave21)."""
    m = BuildingModel("camb")
    m.rigid_diaphragms = False
    m.num_modes = 0
    m.add_material(Material("steel", 2.0e8, 0.3, 77.0))
    m.add_material(Material("conc", 25e6, 0.2, 24.0))
    m.add_section(FrameSection.from_library("W18x50", "steel"))
    m.shell_sections["SL"] = ShellSection("SL", "conc", 0.15)
    m.set_stories([3.0])
    m.add_member("beam", "W18x50", (0, 1.5, 3), (span, 1.5, 3),
                 story="S1", releases="Mi,Mj", uid="B1")
    for x in (0.0, span):
        m.supports.append(PointSupport((x, 1.5, 3), FIX))
    for x, y in ((0, 0), (span, 0), (span, 3), (0, 3)):
        m.supports.append(PointSupport((x, y, 3), FIX))
    m.shells.append(ShellRegion("S1", "slab", "shell", "SL",
                                [(0, 0, 3), (span, 0, 3), (span, 3, 3),
                                 (0, 3, 3)], mesh_size=100.0, story="S1"))
    dead = m.pattern("DEAD", "dead")
    dead.member_loads.append(MemberLoad("B1", "udl", w_dead))
    live = m.pattern("LIVE", "live")
    live.member_loads.append(MemberLoad("B1", "udl", 5.0))
    m.add_case("DEAD", {"DEAD": 1.0})
    m.add_case("LIVE", {"LIVE": 1.0})
    m.add_combo("U", {"DEAD": 1.2, "LIVE": 1.6})
    m.validate()
    return m


def test_composite_camber_engine_chain():
    """9 m W18x50, dead w = 40 kN/m: the pre-composite deflection is the
    exact simply-supported 5wL^4/(384*E*Is) = 51.31 mm (Is = 800 in^4);
    camber = floor(0.8*51.31 / 5 mm) = 40 mm.  A light 6 m beam (< 7.5 m
    span) reports camber 0.0, and camber is None only when no dead case
    exists."""
    I_W18 = 800.0 * _IN ** 4
    m = _camber_model(9.0, 40.0)
    res = _run(m)
    chk = check_composite_beams(m, res, fc_prime=30_000.0)[0]
    assert chk.applicable
    delta = 5.0 * 40.0 * 9.0 ** 4 / (384.0 * 2.0e8 * I_W18)
    assert chk.defl_DL == pytest.approx(delta, rel=1e-6)
    hand = math.floor(0.8 * delta / 0.005) * 0.005
    assert hand == pytest.approx(0.040)
    assert chk.camber == pytest.approx(hand, rel=1e-12)
    # short span: thresholds zero the recommendation
    m6 = _camber_model(6.0, 10.0)
    chk6 = check_composite_beams(m6, _run(m6), fc_prime=30_000.0)[0]
    assert chk6.camber == 0.0
    assert chk6.to_dict()["camber"] == 0.0
    # no recoverable dead deflection -> camber stays None
    d6 = _run(m6).to_dict()
    del d6["cases"]["DEAD"]["member_deflections"]
    chk_none = check_composite_beams(m6, d6, fc_prime=30_000.0,
                                     shored=True)
    assert chk_none[0].camber is None
    assert chk_none[0].to_dict()["camber"] is None


# --------------------------------------------------------------------------- #
# 8. API routing
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client():
    from skyframe.api import server as srv
    app = srv.create_app()
    app.config["TESTING"] = True
    srv._state["model"] = srv.quick_building()
    with app.test_client() as c:
        yield c
    srv._state["model"] = srv.quick_building()


def test_api_steel_code_routing(client):
    """code='EC3' echoes and returns EC3-shaped checks; a bad code 400s;
    the code-less response is BIT-IDENTICAL to code='AISC360' minus the
    echo key (same request, same engine, same checks)."""
    r = client.post("/api/design/steel", json={"case": "DEAD",
                                               "code": "EC3"})
    assert r.status_code == 200
    j = r.get_json()
    assert j["code"] == "EC3" and j["preliminary"] is True
    assert "NplRd" in j["checks"][0]             # EC3 result shape
    r_bad = client.post("/api/design/steel", json={"case": "DEAD",
                                                   "code": "BS5950"})
    assert r_bad.status_code == 400
    r_none = client.post("/api/design/steel", json={"case": "DEAD"})
    r_aisc = client.post("/api/design/steel", json={"case": "DEAD",
                                                    "code": "AISC360"})
    j_none, j_aisc = r_none.get_json(), r_aisc.get_json()
    assert "code" not in j_none                  # pre-v0.23 shape kept
    assert j_aisc.pop("code") == "AISC360"
    assert j_none == j_aisc                      # bit-identical payload


def test_api_concrete_code_routing(client):
    """Same routing contract for /api/design/concrete (ACI318|EC2)."""
    rb = {"rebar": {}}
    r = client.post("/api/design/concrete",
                    json={"case": "DEAD", "code": "EC2", **rb})
    assert r.status_code == 200
    j = r.get_json()
    assert j["code"] == "EC2"
    r_bad = client.post("/api/design/concrete",
                        json={"case": "DEAD", "code": "BS8110", **rb})
    assert r_bad.status_code == 400
    r_none = client.post("/api/design/concrete",
                         json={"case": "DEAD", **rb})
    r_aci = client.post("/api/design/concrete",
                        json={"case": "DEAD", "code": "ACI318", **rb})
    j_none, j_aci = r_none.get_json(), r_aci.get_json()
    assert "code" not in j_none
    assert j_aci.pop("code") == "ACI318"
    assert j_none == j_aci
    # EC2 rejects fck > 50 MPa (the <= C50 laws)
    r_hot = client.post("/api/design/concrete",
                        json={"case": "DEAD", "code": "EC2", "fc": 60_000.0,
                              **rb})
    assert r_hot.status_code == 400


def test_api_nbcc_endpoints(client):
    """nbcc-wind/nbcc-elf create the patterns; missing/bad params 400."""
    r = client.post("/api/pattern/nbcc-wind", json={"q": 0.5,
                                                    "exposure": "rough",
                                                    "direction": "Y"})
    assert r.status_code == 200
    assert r.get_json()["patterns"]["NWIND"]["kind"] == "wind"
    assert client.post("/api/pattern/nbcc-wind",
                       json={}).status_code == 400
    assert client.post("/api/pattern/nbcc-wind",
                       json={"q": 0.5,
                             "exposure": "C"}).status_code == 400
    r = client.post("/api/pattern/nbcc-elf",
                    json={"Sa02": 0.9, "Sa05": 0.6, "Sa10": 0.4,
                          "Sa20": 0.2, "RdRo": 3.9, "system": "steel_mf"})
    assert r.status_code == 200
    pat = r.get_json()["patterns"]["NELF"]
    assert pat["kind"] == "quake"
    assert sum(sf["fx"] for sf in pat["story_forces"]) > 0.0
    assert client.post("/api/pattern/nbcc-elf",
                       json={"Sa02": 0.9}).status_code == 400
    assert client.post("/api/pattern/nbcc-elf",
                       json={"Sa02": 0.9, "Sa05": 0.6, "Sa10": 0.4,
                             "Sa20": 0.2, "RdRo": 3.9,
                             "system": "brick"}).status_code == 400


def test_api_shell_wind_and_seismic341(client):
    """shell-wind 400s without any wind_cp, works once one is set;
    seismic341 defaults to the first additive combo and 400s on unknown
    combos / bad columns params."""
    assert client.post("/api/pattern/shell-wind",
                       json={"q": 0.5}).status_code == 400
    from skyframe.api import server as srv
    mdl = srv._state["model"]
    mdl.shell_sections["SL"] = ShellSection("SL", "CONC", 0.15)
    mdl.shells.append(ShellRegion("R1", "slab", "shell", "SL",
                                  [(0, 0, 3.2), (6, 0, 3.2), (6, 6, 3.2),
                                   (0, 6, 3.2)], mesh_size=3.0,
                                  story="S1", wind_cp=-0.7))
    r = client.post("/api/pattern/shell-wind", json={"q": 0.5})
    assert r.status_code == 200
    pat = r.get_json()["patterns"]["SWIND"]
    assert pat["kind"] == "wind"
    assert pat["area_loads"][0]["q"] == pytest.approx(-0.35, rel=1e-12)
    assert client.post("/api/pattern/shell-wind",
                       json={}).status_code == 400
    # seismic341: quick building columns are rectangular concrete -> all
    # joints N/A, but the endpoint contract (default combo, shapes) holds
    r = client.post("/api/design/seismic341", json={})
    assert r.status_code == 200
    j = r.get_json()
    assert j["combo"] == "1.2D + 1.6L"           # first additive combo
    assert j["columns"] == "auto"
    assert j["summary"]["n"] == len(j["joints"]) > 0
    assert all(row["status"] == "N/A" for row in j["joints"])
    assert client.post("/api/design/seismic341",
                       json={"combo": "NOPE"}).status_code == 400
    assert client.post("/api/design/seismic341",
                       json={"columns": ["NOPE"]}).status_code == 400
    assert client.post("/api/design/seismic341",
                       json={"columns": "some"}).status_code == 400
