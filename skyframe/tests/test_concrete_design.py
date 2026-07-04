"""Tests for skyframe.design.concrete — preliminary ACI 318-19 checks.

Every expected value is derived BY HAND inside the test (the arithmetic
is spelled out in comments and recomputed independently in test code),
so the module's math is checked against an independent recomputation,
not against itself.

Unit system: kN, m, kPa (fc' = 30 MPa = 30 000 kPa, fy = 420 MPa =
420 000 kPa, Es = 200 GPa = 2.0e8 kPa) per CONTRACT.md.
"""

import math

import numpy as np
import pytest

from skyframe.core.model import BuildingModel, FrameSection, Material
from skyframe.design.concrete import (
    ES_REBAR,
    PHI_COMPRESSION,
    RebarLayout,
    beam_flexure,
    beam_shear,
    beta1,
    check_concrete_members,
    column_interaction,
    phi_from_strain,
    rho_min,
    summarize,
)

FC = 30_000.0                    # kPa (fc' = 30 MPa)
FY = 420_000.0                   # kPa (fy = 420 MPa)
EPS_TY = FY / ES_REBAR           # 420 000 / 2.0e8 = 0.0021 exactly
# beta1 for fc' = 30 MPa: 0.85 - 0.05*(30 - 28)/7 = 0.85 - 0.1/7
BETA1_30 = 0.85 - 0.05 * 2.0 / 7.0


# --------------------------------------------------------------------------- #
# helpers: engine-free models + synthetic CONTRACT-shaped results
# --------------------------------------------------------------------------- #
def _beam_model(b=0.3, h=0.6, L=6.0):
    m = BuildingModel(name="conc-beam-stub")
    m.rigid_diaphragms = False
    m.add_material(Material(name="CONC", E=25e6, nu=0.2))
    m.add_section(FrameSection.rectangular("BSEC", "CONC", b, h))
    m.add_member("beam", "BSEC", (0, 0, 0), (L, 0, 0), uid="M1")
    return m


def _column_model(b=0.4, h=0.4, L=3.0):
    m = BuildingModel(name="conc-col-stub")
    m.rigid_diaphragms = False
    m.add_material(Material(name="CONC", E=25e6, nu=0.2))
    m.add_section(FrameSection.rectangular("CSEC", "CONC", b, h))
    m.add_member("column", "CSEC", (0, 0, 0), (0, 0, L), uid="M1")
    return m


def _results(Pu=0.0, M3i=0.0, M3j=0.0, V2i=0.0, V2j=0.0,
             stations=None, case="D"):
    """member_forces = [Ni,Vyi,Vzi,Ti,Myi,Mzi, Nj,Vyj,Vzj,Tj,Myj,Mzj];
    Pu (+compression) goes in Ni.  ``stations`` (optional) is the
    member_stations dict for member M1."""
    mf = [Pu, V2i, 0.0, 0.0, 0.0, M3i, -Pu, V2j, 0.0, 0.0, 0.0, M3j]
    block = {"member_forces": {"M1": mf}}
    if stations is not None:
        block["member_stations"] = {"M1": stations}
    return {"cases": {case: block}}


def _stations(m3, v2=None):
    n = len(m3)
    return {"x": [i for i in range(n)], "N": [0.0] * n, "V2": v2 or [0.0] * n,
            "V3": [0.0] * n, "T": [0.0] * n, "M2": [0.0] * n,
            "M3": list(m3)}


# 3 x 20 mm bottom bars, default cover 0.04 / stirrup 0.010:
LAY_3X20 = RebarLayout(n_top=3, n_bot=3, bar_dia=0.020)


# --------------------------------------------------------------------------- #
# beam flexure — hand-derived singly-reinforced capacity
# --------------------------------------------------------------------------- #
def test_singly_reinforced_mn_hand_calc():
    # b = 0.3, h = 0.6, cover = 0.04, stirrup = 0.010, 3 x 20 mm bars:
    #   As = 3 * pi * 0.02^2 / 4          = 9.42478e-4 m^2
    #   d  = 0.6 - 0.04 - 0.010 - 0.010   = 0.540 m
    #   a  = As*fy / (0.85*fc'*b) = (9.42478e-4 * 420000) / (0.85*30000*0.3)
    #      = 395.841 / 7650 = 0.0517439 m
    #   Mn = As*fy*(d - a/2) = 395.841 * (0.540 - 0.0258719) = 203.52 kN*m
    #   c = a/beta1 = 0.0619158 m; eps_t = 0.003*(0.54 - c)/c = 0.023166
    #     >= eps_ty + 0.003 = 0.0051 -> tension-controlled, phi = 0.9
    As = 3.0 * math.pi * 0.020 ** 2 / 4.0
    d = 0.6 - 0.04 - 0.010 - 0.020 / 2.0
    a = As * FY / (0.85 * FC * 0.3)
    Mn = As * FY * (d - a / 2.0)
    c = a / BETA1_30
    eps_t = 0.003 * (d - c) / c
    assert eps_t >= EPS_TY + 0.003          # genuinely tension-controlled

    fx = beam_flexure(0.3, d, As, FC, FY)
    assert fx["a"] == pytest.approx(a, rel=1e-9)
    assert fx["c"] == pytest.approx(c, rel=1e-9)
    assert fx["beta1"] == pytest.approx(BETA1_30, rel=1e-12)
    assert fx["eps_t"] == pytest.approx(eps_t, rel=1e-9)
    assert fx["phi"] == 0.9
    assert fx["Mn"] == pytest.approx(Mn, rel=1e-9)
    assert fx["phiMn"] == pytest.approx(0.9 * Mn, rel=1e-9)

    # and through the full pipeline (sagging demand -> bottom steel):
    model = _beam_model()
    chk = check_concrete_members(model, _results(M3i=100.0), "D",
                                 {"M1": LAY_3X20})[0]
    assert chk.phiMn_pos == pytest.approx(0.9 * Mn, rel=1e-9)
    assert chk.phiMn_neg == pytest.approx(0.9 * Mn, rel=1e-9)  # symmetric
    assert chk.preliminary is True


def test_sagging_and_hogging_faces_use_signed_station_m3():
    # Station M3 sign convention: positive = sagging (bottom steel),
    # negative = hogging (top steel).  4 top bars vs 2 bottom bars give
    # DIFFERENT capacities, so the face bookkeeping is observable.
    lay = RebarLayout(n_top=4, n_bot=2, bar_dia=0.020)
    model = _beam_model()
    st = _stations([-80.0, -10.0, 40.0, 50.0, 30.0, -60.0])
    chk = check_concrete_members(model, _results(stations=st), "D",
                                 {"M1": lay})[0]
    assert chk.Mu_pos == pytest.approx(50.0)      # max positive M3
    assert chk.Mu_neg == pytest.approx(80.0)      # |max negative M3|
    d = 0.6 - 0.04 - 0.010 - 0.010
    bar = math.pi * 0.020 ** 2 / 4.0
    phiMn_bot = beam_flexure(0.3, d, 2 * bar, FC, FY)["phiMn"]
    phiMn_top = beam_flexure(0.3, d, 4 * bar, FC, FY)["phiMn"]
    assert chk.phiMn_pos == pytest.approx(phiMn_bot, rel=1e-9)
    assert chk.phiMn_neg == pytest.approx(phiMn_top, rel=1e-9)
    # governing ratio is the max of the three sub-checks
    expect = max(50.0 / phiMn_bot, 80.0 / phiMn_top,
                 0.0)                              # shear demand is zero here
    assert chk.ratio == pytest.approx(expect, rel=1e-9)


def test_rho_min_trigger():
    # 1 x 10 mm bottom bar in a 0.3 x 0.6 beam:
    #   As = pi*0.01^2/4 = 7.854e-5; d = 0.6-0.04-0.01-0.005 = 0.545
    #   rho = 7.854e-5/(0.3*0.545) = 4.803e-4
    #   rho_min = max(0.25*sqrt(30)/420, 1.4/420) = max(3.2600e-3, 3.3333e-3)
    #           = 1.4/420 = 3.3333e-3  ->  violated -> NG + note
    assert rho_min(FC, FY) == pytest.approx(1.4 / 420.0, rel=1e-12)
    assert 0.25 * math.sqrt(30.0) / 420.0 < 1.4 / 420.0   # 1.4/fy governs
    lay = RebarLayout(n_top=1, n_bot=1, bar_dia=0.010)
    model = _beam_model()
    chk = check_concrete_members(model, _results(M3i=1.0), "D",
                                 {"M1": lay})[0]
    assert any("rho_min" in n for n in chk.notes)
    assert chk.status == "NG"                 # detailing failure forces NG
    assert chk.ratio is not None and chk.ratio < 1.0   # strength itself OK


def test_phi_interpolation_in_transition_region():
    # 5 x 28 mm bottom bars, b = 0.3, h = 0.6, cover 0.04, stirrup 0.010:
    #   As = 5 * pi*0.028^2/4 = 3.078761e-3 m^2
    #   d  = 0.6 - 0.04 - 0.010 - 0.014 = 0.536 m
    #   a  = 3.078761e-3*420000/7650 = 0.1690312 m ; c = a/beta1 = 0.2022705
    #   eps_t = 0.003*(0.536 - c)/c = 4.9509e-3 -> between eps_ty = 0.0021
    #   and eps_ty + 0.003 = 0.0051 -> TRANSITION:
    #   phi = 0.65 + 0.25*(eps_t - 0.0021)/0.003 = 0.887578
    As = 5.0 * math.pi * 0.028 ** 2 / 4.0
    d = 0.6 - 0.04 - 0.010 - 0.028 / 2.0
    a = As * FY / (0.85 * FC * 0.3)
    c = a / BETA1_30
    eps_t = 0.003 * (d - c) / c
    assert EPS_TY < eps_t < EPS_TY + 0.003
    phi_hand = 0.65 + (0.9 - 0.65) * (eps_t - EPS_TY) / 0.003

    assert phi_from_strain(eps_t, EPS_TY) == pytest.approx(phi_hand,
                                                           rel=1e-12)
    fx = beam_flexure(0.3, d, As, FC, FY)
    assert fx["phi"] == pytest.approx(phi_hand, rel=1e-9)
    assert fx["phiMn"] == pytest.approx(phi_hand * As * FY * (d - a / 2.0),
                                        rel=1e-9)

    lay = RebarLayout(n_top=5, n_bot=5, bar_dia=0.028)
    chk = check_concrete_members(_beam_model(), _results(M3i=100.0), "D",
                                 {"M1": lay})[0]
    assert any("not tension-controlled" in n for n in chk.notes)
    assert chk.phiMn_pos == pytest.approx(fx["phiMn"], rel=1e-9)


def test_phi_interpolation_endpoints():
    assert phi_from_strain(EPS_TY, EPS_TY) == PHI_COMPRESSION
    assert phi_from_strain(EPS_TY - 1e-4, EPS_TY) == PHI_COMPRESSION
    assert phi_from_strain(EPS_TY + 0.003, EPS_TY) == 0.9
    mid = EPS_TY + 0.0015                    # halfway across the transition
    assert phi_from_strain(mid, EPS_TY) == pytest.approx(0.775, rel=1e-12)


def test_beta1_values():
    assert beta1(28_000.0) == pytest.approx(0.85, rel=1e-12)
    assert beta1(20_000.0) == pytest.approx(0.85, rel=1e-12)
    assert beta1(30_000.0) == pytest.approx(BETA1_30, rel=1e-12)
    # 0.85 - 0.05*(56-28)/7 = 0.65; anything stronger stays clamped
    assert beta1(56_000.0) == pytest.approx(0.65, rel=1e-12)
    assert beta1(80_000.0) == pytest.approx(0.65, rel=1e-12)


# --------------------------------------------------------------------------- #
# beam shear — hand-derived Vc + Vs
# --------------------------------------------------------------------------- #
def test_shear_vc_vs_hand_calc():
    # b = 0.3, d = 0.540 (20 mm bars), 2-leg 10 mm stirrups @ 0.15:
    #   Av = 2 * pi*0.01^2/4 = 1.570796e-4 m^2
    #   Vc = 0.17*sqrt(30 MPa) * 1000 * 0.3 * 0.54
    #      = 0.931128 MPa -> 931.128 kPa * 0.162 m^2 = 150.843 kN
    #   Vs = Av*fy*d/s = 1.570796e-4*420000*0.54/0.15 = 237.504 kN
    #   cap = 0.66*sqrt(30)*1000*0.162 = 585.625 kN  (not reached)
    #   phiVn = 0.75*(Vc + Vs) = 291.260 kN
    d = 0.6 - 0.04 - 0.010 - 0.010
    Av = 2.0 * math.pi * 0.010 ** 2 / 4.0
    Vc = 0.17 * math.sqrt(30.0) * 1000.0 * 0.3 * d
    Vs = Av * FY * d / 0.15
    assert Vs < 0.66 * math.sqrt(30.0) * 1000.0 * 0.3 * d

    sh = beam_shear(0.3, d, Av, 0.15, FC, FY)
    assert sh["Vc"] == pytest.approx(Vc, rel=1e-9)
    assert sh["Vs"] == pytest.approx(Vs, rel=1e-9)
    assert sh["Vs_capped"] is False
    assert sh["phiVn"] == pytest.approx(0.75 * (Vc + Vs), rel=1e-9)

    # through the pipeline with station V2 governing (max |V2| = 200):
    st = _stations([0.0, 0.0, 0.0], v2=[-200.0, 0.0, 150.0])
    chk = check_concrete_members(_beam_model(), _results(stations=st), "D",
                                 {"M1": LAY_3X20})[0]
    assert chk.Vu == pytest.approx(200.0)
    assert chk.phiVn == pytest.approx(0.75 * (Vc + Vs), rel=1e-9)
    assert chk.ratio == pytest.approx(200.0 / (0.75 * (Vc + Vs)), rel=1e-9)
    assert chk.equation == "shear"


def test_shear_vs_cap_at_066_sqrt_fc():
    # Very tight stirrups (s = 0.03 m): Vs = 1.570796e-4*420000*0.54/0.03
    # = 1187.52 kN > cap = 0.66*sqrt(30)*1000*0.3*0.54 = 585.625 kN -> cap
    d = 0.6 - 0.04 - 0.010 - 0.010
    Av = 2.0 * math.pi * 0.010 ** 2 / 4.0
    cap = 0.66 * math.sqrt(30.0) * 1000.0 * 0.3 * d
    assert Av * FY * d / 0.03 > cap
    sh = beam_shear(0.3, d, Av, 0.03, FC, FY)
    assert sh["Vs_capped"] is True
    assert sh["Vs"] == pytest.approx(cap, rel=1e-9)
    assert sh["phiVn"] == pytest.approx(0.75 * (sh["Vc"] + cap), rel=1e-9)

    lay = RebarLayout(n_top=3, n_bot=3, bar_dia=0.020, stirrup_spacing=0.03)
    chk = check_concrete_members(_beam_model(), _results(V2i=100.0), "D",
                                 {"M1": lay})[0]
    assert any("capped" in n for n in chk.notes)


# --------------------------------------------------------------------------- #
# column interaction — hand-derived points
# --------------------------------------------------------------------------- #
COL_LAY = RebarLayout(n_top=3, n_bot=3, bar_dia=0.020)   # 3+3 x 20 mm
COL_D = 0.4 - 0.04 - 0.010 - 0.010        # 0.340 m
COL_DP = 0.04 + 0.010 + 0.010             # 0.060 m (cover+stirrup+bar/2)
COL_AS_FACE = 3.0 * math.pi * 0.020 ** 2 / 4.0


def test_column_pure_compression_point_hand_calc():
    # Ag = 0.16 m^2, Ast = 6 * pi*0.02^2/4 = 1.884956e-3 m^2
    # Pn0 = 0.85*30000*(0.16 - Ast) + 420000*Ast
    #     = 25500*0.1581150 + 791.681 = 4031.934 + 791.681 = 4823.615 kN
    # phiPn,max = 0.80 * 0.65 * Pn0 = 2508.28 kN  (tied, ACI 22.4.2)
    Ast = 6.0 * math.pi * 0.020 ** 2 / 4.0
    Pn0 = 0.85 * FC * (0.16 - Ast) + FY * Ast
    pts = column_interaction(0.4, 0.4, COL_D, COL_DP, COL_AS_FACE, FC, FY)
    top = pts[0]
    assert top["label"] == "pure compression"
    assert top["phiPn"] == pytest.approx(0.80 * 0.65 * Pn0, rel=1e-9)
    assert top["phiMn"] == 0.0
    # pure tension end of the diagram
    bot = pts[-1]
    assert bot["label"] == "pure tension"
    assert bot["phiPn"] == pytest.approx(-0.9 * FY * Ast, rel=1e-9)
    assert bot["phiMn"] == 0.0
    # phiPn is monotonically decreasing along the 5 points
    phiPns = [p["phiPn"] for p in pts]
    assert all(a >= b for a, b in zip(phiPns[:-1], phiPns[1:]))


def test_column_balanced_point_strain_compatibility_numpy():
    # Independent numpy recomputation of the balanced point:
    #   c_b = d * 0.003/(0.003 + eps_ty) = 0.340 * 0.003/0.0051 = 0.200 m
    #   a = beta1*c_b = 0.8357143*0.2 = 0.1671429 m
    #   Cc = 0.85*fc'*a*b = 25500*0.1671429*0.4 = 1704.857 kN
    #   compression steel (d' = 0.060): eps = 0.003*(0.2-0.06)/0.2 = 0.0021
    #     = eps_ty exactly -> fs = +fy; tension steel: eps = -0.0021 -> -fy
    #   Pn = Cc + As*fy - As*fy = Cc = 1704.857 kN
    #   Mn = Cc*(0.2 - a/2) + As*fy*(0.2-0.06) + As*fy*(0.340-0.2)
    #      = 1704.857*0.1164286 + 395.841*(0.140 + 0.140) = 309.33 kN*m
    c_b = COL_D * 0.003 / (0.003 + EPS_TY)
    a = BETA1_30 * c_b
    Cc = 0.85 * FC * a * 0.4
    depths = np.array([COL_DP, COL_D])
    eps = 0.003 * (c_b - depths) / c_b
    fs = np.clip(ES_REBAR * eps, -FY, FY)
    areas = np.array([COL_AS_FACE, COL_AS_FACE])
    Pn = Cc + float(np.sum(areas * fs))
    Mn = Cc * (0.2 - a / 2.0) + float(np.sum(areas * fs * (0.2 - depths)))
    # sanity on the hand arithmetic (comment values above):
    assert Pn == pytest.approx(1704.857, rel=1e-3)
    assert Mn == pytest.approx(309.33, rel=1e-3)

    pts = column_interaction(0.4, 0.4, COL_D, COL_DP, COL_AS_FACE, FC, FY)
    bal = next(p for p in pts if p["label"] == "balanced")
    assert bal["c"] == pytest.approx(c_b, rel=1e-12)
    assert bal["Pn"] == pytest.approx(Pn, rel=1e-6)
    assert bal["Mn"] == pytest.approx(Mn, rel=1e-6)
    # balanced point sits exactly at eps_t = eps_ty -> compression-controlled
    assert bal["eps_t"] == pytest.approx(EPS_TY, rel=1e-9)
    assert bal["phi"] == PHI_COMPRESSION


def test_column_pure_bending_point_is_axial_equilibrium():
    pts = column_interaction(0.4, 0.4, COL_D, COL_DP, COL_AS_FACE, FC, FY)
    pb = next(p for p in pts if p["label"] == "pure bending")
    assert abs(pb["Pn"]) < 1e-6              # bisection converged to Pn = 0
    assert pb["Mn"] > 0.0


def test_column_demand_at_balanced_point_rates_exactly_one():
    pts = column_interaction(0.4, 0.4, COL_D, COL_DP, COL_AS_FACE, FC, FY)
    bal = next(p for p in pts if p["label"] == "balanced")
    model = _column_model()
    res = _results(Pu=bal["phiPn"],
                   stations=_stations([bal["phiMn"], 0.0, -0.1]))
    chk = check_concrete_members(model, res, "D", {"M1": COL_LAY})[0]
    assert chk.equation == "P-M"
    assert chk.ratio == pytest.approx(1.0, rel=1e-9)
    assert chk.status == "OK"
    # half the demand -> half the ratio (radial scaling)
    res2 = _results(Pu=0.5 * bal["phiPn"],
                    stations=_stations([0.5 * bal["phiMn"]]))
    chk2 = check_concrete_members(model, res2, "D", {"M1": COL_LAY})[0]
    assert chk2.ratio == pytest.approx(0.5, rel=1e-9)


def test_column_pure_axial_demand_rates_against_phipn_max():
    pts = column_interaction(0.4, 0.4, COL_D, COL_DP, COL_AS_FACE, FC, FY)
    phiPn_max = pts[0]["phiPn"]
    chk = check_concrete_members(_column_model(),
                                 _results(Pu=0.5 * phiPn_max), "D",
                                 {"M1": COL_LAY})[0]
    assert chk.ratio == pytest.approx(0.5, rel=1e-9)
    over = check_concrete_members(_column_model(),
                                  _results(Pu=1.25 * phiPn_max), "D",
                                  {"M1": COL_LAY})[0]
    assert over.ratio == pytest.approx(1.25, rel=1e-9)
    assert over.status == "NG"


def test_column_unsymmetric_layout_is_na_with_note():
    lay = RebarLayout(n_top=4, n_bot=2, bar_dia=0.020)
    chk = check_concrete_members(_column_model(), _results(Pu=100.0), "D",
                                 {"M1": lay})[0]
    assert chk.status == "N/A"
    assert chk.ratio is None
    assert any("unsymmetric" in n for n in chk.notes)


# --------------------------------------------------------------------------- #
# module-level behaviour
# --------------------------------------------------------------------------- #
def test_member_without_rebar_layout_is_na():
    chk = check_concrete_members(_beam_model(), _results(M3i=10.0), "D",
                                 {})[0]
    assert chk.status == "N/A"
    assert any("no rebar layout" in n for n in chk.notes)
    s = summarize([chk])
    assert s["na"] == 1 and s["governing"] is None
    assert s["preliminary"] is True


def test_section_without_rect_dims_is_na():
    m = BuildingModel(name="no-dims")
    m.add_material(Material(name="CONC", E=25e6))
    m.add_section(FrameSection("GEN", "CONC", A=0.18, I33=5.4e-3,
                               I22=1.35e-3, J=3e-3))       # b = h = 0
    m.add_member("beam", "GEN", (0, 0, 0), (6, 0, 0), uid="M1")
    chk = check_concrete_members(m, _results(M3i=10.0), "D",
                                 {"M1": LAY_3X20})[0]
    assert chk.status == "N/A"
    assert any("no rectangular b/h" in n for n in chk.notes)


def test_brace_kind_is_na():
    m = _beam_model()
    m.add_member("brace", "BSEC", (0, 0, 0), (6, 0, 3.0), uid="X1")
    res = _results(M3i=10.0)
    res["cases"]["D"]["member_forces"]["X1"] = [0.0] * 12
    checks = {c.uid: c for c in
              check_concrete_members(m, res, "D",
                                     {"M1": LAY_3X20, "X1": LAY_3X20})}
    assert checks["X1"].status == "N/A"
    assert any("not checked" in n for n in checks["X1"].notes)


def test_unknown_case_raises():
    with pytest.raises(KeyError):
        check_concrete_members(_beam_model(), _results(), "NOPE", {})


def test_to_dict_shape_and_preliminary_flag():
    chk = check_concrete_members(_beam_model(), _results(M3i=50.0, V2i=40.0),
                                 "D", {"M1": LAY_3X20})[0]
    d = chk.to_dict()
    assert d["preliminary"] is True
    for key in ("uid", "section", "kind", "Pu", "Mu_pos", "Mu_neg", "Mu",
                "Vu", "phiMn_pos", "phiMn_neg", "phiVn", "pm_points",
                "ratio", "equation", "status", "notes"):
        assert key in d
    assert chk.status in ("OK", "NG")


def test_beam_axial_note_when_pu_large():
    # 0.10*fc'*Ag = 0.10*30000*0.18 = 540 kN threshold
    chk = check_concrete_members(_beam_model(),
                                 _results(Pu=600.0, M3i=10.0), "D",
                                 {"M1": LAY_3X20})[0]
    assert any("0.10*fc'*Ag" in n for n in chk.notes)


def test_beam_face_without_bars_and_demand_is_ng():
    lay = RebarLayout(n_top=0, n_bot=3, bar_dia=0.020)
    st = _stations([-50.0, 20.0, 30.0])       # hogging demand, no top bars
    chk = check_concrete_members(_beam_model(), _results(stations=st), "D",
                                 {"M1": lay})[0]
    assert chk.status == "NG"
    assert any("no bars on the tension face" in n for n in chk.notes)


# --------------------------------------------------------------------------- #
# end-to-end with the OpenSees engine
# --------------------------------------------------------------------------- #
def test_end_to_end_concrete_frame():
    pytest.importorskip("openseespy.opensees")
    from skyframe.core.model import MemberLoad, NodalLoad
    from skyframe.engine.opensees_engine import OpenSeesEngine

    m = BuildingModel(name="conc-frame")
    m.rigid_diaphragms = False
    m.add_material(Material(name="CONC", E=25e6, nu=0.2))
    m.add_section(FrameSection.rectangular("C40", "CONC", 0.4, 0.4))
    m.add_section(FrameSection.rectangular("B30X60", "CONC", 0.3, 0.6))
    m.set_stories([3.0])
    m.add_member("column", "C40", (0, 0, 0), (0, 0, 3.0),
                 story="Story1", uid="C1")
    m.add_member("column", "C40", (6, 0, 0), (6, 0, 3.0),
                 story="Story1", uid="C2")
    m.add_member("beam", "B30X60", (0, 0, 3.0), (6, 0, 3.0),
                 story="Story1", uid="B1")
    dead = m.pattern("DEAD", "dead")
    dead.member_loads.append(MemberLoad("B1", kind="udl", w=30.0,
                                        direction="gravity"))
    lat = m.pattern("LAT", "quake")
    lat.nodal_loads.append(NodalLoad((0, 0, 3.0), fx=40.0))
    m.add_case("D", {"DEAD": 1.0})
    m.add_case("E", {"LAT": 1.0})

    eng = OpenSeesEngine(m)
    results = {"cases": {"D": eng.run_static("D").to_dict(),
                         "E": eng.run_static("E").to_dict()}}

    rebar = {"C1": RebarLayout(3, 3, 0.020), "C2": RebarLayout(3, 3, 0.020),
             "B1": RebarLayout(3, 3, 0.020)}

    # sign convention lock: the frame beam under gravity sags at midspan
    # (positive station M3) and hogs at the joints (negative M3)
    m3 = results["cases"]["D"]["member_stations"]["B1"]["M3"]
    assert max(m3) > 0.0 > min(m3)
    checks = {c.uid: c for c in
              check_concrete_members(m, results, "D", rebar)}
    assert checks["B1"].Mu_pos == pytest.approx(max(m3), rel=1e-9)
    assert checks["B1"].Mu_neg == pytest.approx(-min(m3), rel=1e-9)
    # each column carries ~half the beam load in compression
    assert checks["C1"].Pu == pytest.approx(90.0, rel=1e-6)

    for case in ("D", "E"):
        for c in check_concrete_members(m, results, case, rebar):
            assert c.status in ("OK", "NG")
            assert c.ratio is not None and math.isfinite(c.ratio)
            assert c.ratio >= 0.0
            assert c.to_dict()["preliminary"] is True
    d_checks = check_concrete_members(m, results, "D", rebar)
    assert max(c.ratio for c in d_checks) > 0.01
    s = summarize(d_checks)
    assert s["n"] == 3 and s["na"] == 0
