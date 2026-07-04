"""Tests for skyframe.design.steel — preliminary AISC 360-16 checks.

Every expected value is derived BY HAND in the test from the published
AISC Manual (15th ed.) imperial properties, converted with the exact
factor 1 in = 0.0254 m, so the module's table and math are checked
against an independent recomputation (not against itself).

Unit system: kN, m, kPa (Fy = 345 MPa = 345 000 kPa, E = 200 GPa =
2.0e8 kPa) per CONTRACT.md.
"""

import math

import pytest

from skyframe.core.model import BuildingModel, FrameSection, Material, NodalLoad
from skyframe.design.steel import check_members, summarize

# ---- hand-entered W12x26 properties (AISC Manual 15th ed. Table 1-1) ---- #
IN = 0.0254                      # m per inch (exact)
E = 200_000_000.0                # kPa (module design modulus, 200 GPa)
FY = 345_000.0                   # kPa (345 MPa = 50 ksi nominal)

A_W12X26 = 7.65 * IN ** 2        # A   = 7.65 in^2
D_W12X26 = 12.2 * IN             # d   = 12.2 in
TF_W12X26 = 0.380 * IN           # tf  = 0.380 in
I33_W12X26 = 204.0 * IN ** 4     # Ix  = 204 in^4   (matches sections_library)
I22_W12X26 = 17.3 * IN ** 4      # Iy  = 17.3 in^4
J_W12X26 = 0.300 * IN ** 4       # J   = 0.300 in^4
ZX_W12X26 = 37.2 * IN ** 3       # Zx  = 37.2 in^3
ZY_W12X26 = 8.17 * IN ** 3       # Zy  = 8.17 in^3
RX_W12X26 = 5.17 * IN            # rx  = 5.17 in
RY_W12X26 = 1.51 * IN            # ry  = 1.51 in
RTS_W12X26 = 1.75 * IN           # rts = 1.75 in
BF_W12X26 = 6.49 * IN            # bf  = 6.49 in

SX_W12X26 = I33_W12X26 / (D_W12X26 / 2.0)     # elastic modulus from model I33
SY_W12X26 = I22_W12X26 / (BF_W12X26 / 2.0)
HO_W12X26 = D_W12X26 - TF_W12X26              # distance between flange centroids


# --------------------------------------------------------------------------- #
# helpers: engine-free model + synthetic results
# --------------------------------------------------------------------------- #
def _one_member_model(L, section="W12x26", kind="column", vertical=True):
    m = BuildingModel(name="design-stub")
    m.rigid_diaphragms = False
    m.add_material(Material(name="steel", E=E, nu=0.3))
    if section == "RECT":
        m.add_section(FrameSection.rectangular("RECT", "steel", 0.3, 0.5))
    else:
        m.add_section(FrameSection.from_library(section, "steel"))
    pj = (0.0, 0.0, L) if vertical else (L, 0.0, 0.0)
    m.add_member(kind, section, (0.0, 0.0, 0.0), pj, uid="M1")
    return m


def _results(Pu=0.0, M3i=0.0, M3j=0.0, M2i=0.0, M2j=0.0, case="D"):
    """Synthetic CONTRACT-shaped results: member_forces =
    [Ni,Vyi,Vzi,Ti,Myi,Mzi, Nj,Vyj,Vzj,Tj,Myj,Mzj]; Pu(+comp) goes in Ni."""
    mf = [Pu, 0.0, 0.0, 0.0, M2i, M3i, -Pu, 0.0, 0.0, 0.0, M2j, M3j]
    return {"cases": {case: {"member_forces": {"M1": mf}}}}


def _hand_pn_compression(A, KLr, Fy=FY):
    """AISC E3 by hand: Fe = pi^2 E/(KL/r)^2; inelastic/elastic Fcr."""
    Fe = math.pi ** 2 * E / KLr ** 2
    if KLr <= 4.71 * math.sqrt(E / Fy):
        Fcr = 0.658 ** (Fy / Fe) * Fy
    else:
        Fcr = 0.877 * Fe
    return Fcr * A


def _hand_mn_major(Lb, Fy=FY):
    """AISC F2 by hand for W12x26, Cb = 1.0, c = 1."""
    Mp = Fy * ZX_W12X26
    Lp = 1.76 * RY_W12X26 * math.sqrt(E / Fy)
    if Lb <= Lp:
        return Mp
    jc = J_W12X26 / (SX_W12X26 * HO_W12X26)
    Lr = 1.95 * RTS_W12X26 * E / (0.7 * Fy) * math.sqrt(
        jc + math.sqrt(jc ** 2 + 6.76 * (0.7 * Fy / E) ** 2))
    if Lb <= Lr:
        return min(Mp, Mp - (Mp - 0.7 * Fy * SX_W12X26) * (Lb - Lp) / (Lr - Lp))
    s2 = (Lb / RTS_W12X26) ** 2
    Fcr = math.pi ** 2 * E / s2 * math.sqrt(1.0 + 0.078 * jc * s2)
    return min(Fcr * SX_W12X26, Mp)


# --------------------------------------------------------------------------- #
# compression (AISC E3)
# --------------------------------------------------------------------------- #
def test_compression_inelastic_branch_kl_3m():
    # KL/r = 3.0/(1.51*0.0254) = 78.22 < 4.71*sqrt(E/Fy) = 113.40 -> E3-2
    model = _one_member_model(3.0)
    chk = check_members(model, _results(Pu=100.0), "D")[0]
    KLr = max(3.0 / RX_W12X26, 3.0 / RY_W12X26)
    assert KLr < 4.71 * math.sqrt(E / FY)
    Pn = _hand_pn_compression(A_W12X26, KLr)
    assert chk.phiPn == pytest.approx(0.9 * Pn, rel=1e-9)
    assert chk.status == "OK"


def test_compression_elastic_branch_kl_12m():
    # KL/r = 12.0/(1.51*0.0254) = 312.87 > 113.40 -> E3-3, Fcr = 0.877 Fe
    model = _one_member_model(12.0)
    chk = check_members(model, _results(Pu=10.0), "D")[0]
    KLr = 12.0 / RY_W12X26
    assert KLr > 4.71 * math.sqrt(E / FY)
    Pn = _hand_pn_compression(A_W12X26, KLr)
    assert chk.phiPn == pytest.approx(0.9 * Pn, rel=1e-9)


def test_compression_against_published_aisc_table_4_22():
    # AISC Manual Table 4-22 (available critical stress, LRFD), Fy = 50 ksi,
    # KL/r = 100: phi_c*Fcr ~= 21.7 ksi.  (Hand: Fe = pi^2*29000/100^2
    # = 28.62 ksi, Fcr = 0.658^(50/28.62)*50 = 24.07 ksi, phi*Fcr = 21.66.)
    KSI = 6.894757293168361e3            # kPa per ksi (4448.2216152605 N / 645.16 mm^2, exact)
    Fy_50 = 50.0 * KSI
    L = 100.0 * RY_W12X26                # makes ky*L/ry exactly 100 (governs)
    model = _one_member_model(L)
    chk = check_members(model, _results(Pu=100.0), "D", Fy=Fy_50)[0]
    phi_fcr_ksi = chk.phiPn / A_W12X26 / KSI
    assert phi_fcr_ksi == pytest.approx(21.7, rel=0.01)   # AISC Manual Table 4-22 ~= 21.7 ksi


def test_kx_ky_effective_length_factors():
    # With kx huge, the MAJOR axis governs: KL/r = kx*L/rx.
    model = _one_member_model(3.0)
    chk = check_members(model, _results(Pu=100.0), "D", kx=10.0)[0]
    KLr = 10.0 * 3.0 / RX_W12X26
    Pn = _hand_pn_compression(A_W12X26, KLr)
    assert chk.phiPn == pytest.approx(0.9 * Pn, rel=1e-9)


def test_tension_d2_gross_yielding():
    # Pu < 0 (Ni negative) is tension: phiPn = 0.9 * Fy * A (yielding only).
    model = _one_member_model(3.0)
    chk = check_members(model, _results(Pu=-500.0), "D")[0]
    assert chk.Pu == pytest.approx(-500.0)
    assert chk.phiPn == pytest.approx(0.9 * FY * A_W12X26, rel=1e-12)
    assert any("tension" in n for n in chk.notes)


# --------------------------------------------------------------------------- #
# major-axis flexure (AISC F2) — W12x26: Lp = 1.6253 m, Lr = 4.5336 m
# --------------------------------------------------------------------------- #
def test_flexure_plastic_short_lb():
    # Lb = 1.0 m < Lp = 1.76*ry*sqrt(E/Fy) = 1.6253 m -> Mn = Mp = Fy*Zx
    model = _one_member_model(3.0, kind="beam", vertical=False)
    chk = check_members(model, _results(M3i=50.0, M3j=-40.0), "D", Lb=1.0)[0]
    assert 1.0 < 1.76 * RY_W12X26 * math.sqrt(E / FY)
    assert chk.phiMn33 == pytest.approx(0.9 * FY * ZX_W12X26, rel=1e-9)
    # demands from ends (no stations): max(|M3i|,|M3j|)
    assert chk.Mu33 == pytest.approx(50.0)


def test_flexure_inelastic_ltb_f2_2():
    # Lp = 1.6253 m < Lb = 3.0 m < Lr = 4.5336 m -> F2-2 interpolation
    model = _one_member_model(3.0, kind="beam", vertical=False)
    chk = check_members(model, _results(M3i=50.0), "D")[0]  # Lb defaults to L
    Mn = _hand_mn_major(3.0)
    assert Mn < FY * ZX_W12X26                     # genuinely reduced
    assert chk.phiMn33 == pytest.approx(0.9 * Mn, rel=1e-6)


def test_flexure_elastic_ltb_f2_3():
    # Lb = 8.0 m > Lr = 4.5336 m -> elastic LTB (F2-3 with F2-4 stress)
    model = _one_member_model(8.0, kind="beam", vertical=False)
    chk = check_members(model, _results(M3i=30.0), "D")[0]
    Mn = _hand_mn_major(8.0)
    jc = J_W12X26 / (SX_W12X26 * HO_W12X26)
    Lr = 1.95 * RTS_W12X26 * E / (0.7 * FY) * math.sqrt(
        jc + math.sqrt(jc ** 2 + 6.76 * (0.7 * FY / E) ** 2))
    assert 8.0 > Lr
    assert Mn < 0.7 * FY * SX_W12X26               # well into elastic range
    assert chk.phiMn33 == pytest.approx(0.9 * Mn, rel=1e-6)


def test_flexure_minor_axis_f6():
    # Mn22 = min(Fy*Zy, 1.6*Fy*Sy); for W12x26 Fy*Zy = 46.19 governs
    model = _one_member_model(3.0, kind="beam", vertical=False)
    chk = check_members(model, _results(M2i=10.0), "D")[0]
    Mn22 = min(FY * ZY_W12X26, 1.6 * FY * SY_W12X26)
    assert Mn22 == pytest.approx(FY * ZY_W12X26)   # yielding governs here
    assert chk.phiMn22 == pytest.approx(0.9 * Mn22, rel=1e-9)
    assert chk.Mu22 == pytest.approx(10.0)


# --------------------------------------------------------------------------- #
# interaction (AISC H1) — synthetic demands, no engine
# --------------------------------------------------------------------------- #
def _hand_capacities_3m_column():
    KLr = max(3.0 / RX_W12X26, 3.0 / RY_W12X26)
    phiPn = 0.9 * _hand_pn_compression(A_W12X26, KLr)
    phiMn33 = 0.9 * _hand_mn_major(3.0)            # Lb defaults to L = 3 m
    phiMn22 = 0.9 * min(FY * ZY_W12X26, 1.6 * FY * SY_W12X26)
    return phiPn, phiMn33, phiMn22


def test_interaction_h1_1a_high_axial():
    # Pu = 400 kN -> Pr/Pc = 400/979.5 = 0.408 >= 0.2 -> H1-1a
    model = _one_member_model(3.0)
    chk = check_members(model, _results(Pu=400.0, M3i=50.0, M2i=5.0), "D")[0]
    phiPn, phiMn33, phiMn22 = _hand_capacities_3m_column()
    pr_pc = 400.0 / phiPn
    assert pr_pc >= 0.2
    expected = pr_pc + (8.0 / 9.0) * (50.0 / phiMn33 + 5.0 / phiMn22)
    assert chk.equation == "H1-1a"
    assert chk.ratio == pytest.approx(expected, rel=1e-9)
    assert chk.status == ("OK" if expected <= 1.0 else "NG")


def test_interaction_h1_1b_low_axial():
    # Pu = 100 kN -> Pr/Pc = 0.102 < 0.2 -> H1-1b
    model = _one_member_model(3.0)
    chk = check_members(model, _results(Pu=100.0, M3i=50.0, M2i=5.0), "D")[0]
    phiPn, phiMn33, phiMn22 = _hand_capacities_3m_column()
    pr_pc = 100.0 / phiPn
    assert pr_pc < 0.2
    expected = pr_pc / 2.0 + (50.0 / phiMn33 + 5.0 / phiMn22)
    assert chk.equation == "H1-1b"
    assert chk.ratio == pytest.approx(expected, rel=1e-9)


def test_interaction_overstressed_is_ng():
    model = _one_member_model(3.0)
    chk = check_members(model, _results(Pu=900.0, M3i=150.0), "D")[0]
    assert chk.ratio > 1.0
    assert chk.status == "NG"
    s = summarize([chk])
    assert s["ng"] == 1 and s["governing"] == "M1"
    assert s["max_ratio"] == pytest.approx(chk.ratio)
    assert s["preliminary"] is True


# --------------------------------------------------------------------------- #
# module-level behaviour
# --------------------------------------------------------------------------- #
def test_results_marked_preliminary_and_dict_shape():
    model = _one_member_model(3.0)
    chk = check_members(model, _results(Pu=100.0, M3i=10.0), "D")[0]
    d = chk.to_dict()
    assert d["preliminary"] is True
    for key in ("uid", "section", "kind", "Pu", "Mu33", "Mu22", "phiPn",
                "phiMn33", "phiMn22", "ratio", "equation", "status", "notes"):
        assert key in d
    assert chk.preliminary is True


def test_non_library_section_is_na():
    model = _one_member_model(3.0, section="RECT")
    chk = check_members(model, _results(Pu=100.0), "D")[0]
    assert chk.status == "N/A"
    assert chk.ratio is None and chk.phiPn is None
    assert any("not a library W-shape" in n for n in chk.notes)
    s = summarize([chk])
    assert s["na"] == 1 and s["governing"] is None


def test_impostor_library_name_fails_area_validation():
    # A section NAMED like a library shape but with the wrong area (> 1%)
    # must not be trusted as that W-shape.
    model = BuildingModel(name="impostor")
    model.add_material(Material(name="steel", E=E, nu=0.3))
    model.add_section(FrameSection(name="W12x26", material="steel",
                                   A=2.0 * A_W12X26, I33=I33_W12X26,
                                   I22=I22_W12X26, J=J_W12X26))
    model.add_member("column", "W12x26", (0, 0, 0), (0, 0, 3.0), uid="M1")
    chk = check_members(model, _results(Pu=100.0), "D")[0]
    assert chk.status == "N/A"


def test_unknown_case_raises():
    model = _one_member_model(3.0)
    with pytest.raises(KeyError):
        check_members(model, _results(Pu=1.0), "NOPE")


def test_stations_govern_demands_when_present():
    # member_stations M3 max |.| must beat the end values.
    model = _one_member_model(3.0, kind="beam", vertical=False)
    res = _results(Pu=0.0, M3i=10.0, M3j=-12.0)
    res["cases"]["D"]["member_stations"] = {
        "M1": {"x": [0.0, 1.5, 3.0], "N": [0.0, 0.0, 0.0],
               "V2": [0, 0, 0], "V3": [0, 0, 0], "T": [0, 0, 0],
               "M2": [0.0, -4.0, 1.0], "M3": [10.0, 25.0, -12.0]}}
    chk = check_members(model, res, "D")[0]
    assert chk.Mu33 == pytest.approx(25.0)
    assert chk.Mu22 == pytest.approx(4.0)


# --------------------------------------------------------------------------- #
# end-to-end with the OpenSees engine
# --------------------------------------------------------------------------- #
def test_end_to_end_portal_frame():
    pytest.importorskip("openseespy.opensees")
    from skyframe.core.model import MemberLoad
    from skyframe.engine.opensees_engine import OpenSeesEngine

    m = BuildingModel(name="portal")
    m.rigid_diaphragms = False
    m.add_material(Material(name="steel", E=E, nu=0.3))
    m.add_section(FrameSection.from_library("W12x26", "steel"))
    m.set_stories([3.0])
    m.add_member("column", "W12x26", (0, 0, 0), (0, 0, 3.0),
                 story="Story1", uid="C1")
    m.add_member("column", "W12x26", (6, 0, 0), (6, 0, 3.0),
                 story="Story1", uid="C2")
    m.add_member("beam", "W12x26", (0, 0, 3.0), (6, 0, 3.0),
                 story="Story1", uid="B1")
    dead = m.pattern("DEAD", "dead")
    dead.member_loads.append(MemberLoad("B1", kind="udl", w=20.0,
                                        direction="gravity"))
    lat = m.pattern("LAT", "quake")
    lat.nodal_loads.append(NodalLoad((0, 0, 3.0), fx=50.0))
    m.add_case("D", {"DEAD": 1.0})
    m.add_case("E", {"LAT": 1.0})

    eng = OpenSeesEngine(m)
    results = {"cases": {"D": eng.run_static("D").to_dict(),
                         "E": eng.run_static("E").to_dict()}}

    # ---- SIGN CONVENTION (documented, verified here empirically) ------- #
    # For a gravity case the engine's local end-i axial force N_i is
    # POSITIVE in compression: each column carries w*L/2 = 20*6/2 = 60 kN
    # and must report Pu = +60 (stations "N" carry the opposite,
    # tension-positive internal convention; the module uses N_i).
    checks = {c.uid: c for c in check_members(m, results, "D")}
    assert checks["C1"].Pu == pytest.approx(60.0, rel=1e-6)
    assert checks["C2"].Pu == pytest.approx(60.0, rel=1e-6)
    assert results["cases"]["D"]["member_stations"]["C1"]["N"][0] == \
        pytest.approx(-60.0, rel=1e-6)   # station N: tension-positive

    for case in ("D", "E"):
        for c in check_members(m, results, case):
            d = c.to_dict()
            assert c.status in ("OK", "NG")
            for k in ("Pu", "Mu33", "Mu22", "phiPn", "phiMn33",
                      "phiMn22", "ratio"):
                assert math.isfinite(d[k]), (case, c.uid, k)
            assert d["preliminary"] is True
    d_checks = check_members(m, results, "D")
    assert max(c.ratio for c in d_checks) > 0.01
    s = summarize(d_checks)
    assert s["n"] == 3 and s["na"] == 0
