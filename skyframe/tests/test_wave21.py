"""Wave 21 (v0.20) — composite beams, slab strip design, walking vibration.

Every assertion is pinned by a hand derivation typed into the test:
AISC I3 plastic-distribution cases are re-derived longhand from the
published W18x50 imperial dimensions, the stud/effective-width/inertia
formulas are evaluated arithmetically from their definitions, the strip
layout follows ACI min(L1,L2)/4 by hand, the Whitney inversion is checked
by an exact round trip through ``beam_flexure``, the DG11 chain is typed
directly, and the engine-driven checks use statics-exact demands
(simply supported UDL: M = wL^2/8, delta = 5wL^4/384EI, W = wL).

Units per CONTRACT.md: kN, m, kPa (Fy = 345 MPa = 345_000 kPa).
"""

import math
import warnings

import pytest

from skyframe.core.mesh import mesh_model
from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection,
                                 Material, MemberLoad, PointSupport,
                                 ShellRegion, ShellSection)
from skyframe.design.composite import (check_composite_beams,
                                       composite_flexure, default_live_case,
                                       equivalent_inertia, stud_strength,
                                       summarize_composite,
                                       transformed_inertia)
from skyframe.design.concrete import beam_flexure
from skyframe.design.slab import (check_slab_strips, required_steel,
                                  strip_layout)
from skyframe.design.vibration import (check_vibration, natural_frequency,
                                       walking_acceleration)
from skyframe.engine.opensees_engine import OpenSeesEngine

FIX = (1, 1, 1, 1, 1, 1)
E_STEEL = 200e6          # kPa
FY = 345_000.0           # kPa (A992)

# W18x50 published imperial dimensions (AISC Manual Table 1-1), converted
# with the exact 1 in = 0.0254 m — the same numbers the design table holds:
_IN = 0.0254
AS_W18 = 14.7 * _IN ** 2            # 9.483852e-3 m^2
D_W18 = 18.0 * _IN                  # 0.4572 m
BF_W18 = 7.50 * _IN                 # 0.1905 m
TF_W18 = 0.570 * _IN                # 0.014478 m
TW_W18 = 0.355 * _IN                # 0.0090170 m
ZX_W18 = 101.0 * _IN ** 3           # 1.6548e-3 m^3
I_W18 = 800.0 * _IN ** 4            # 3.329851e-4 m^4 (library I33)


# --------------------------------------------------------------------------- #
# 1. stud strength — AISC I8.2a, both governing branches
# --------------------------------------------------------------------------- #
def test_stud_strength_both_branches():
    """19 mm stud, Fu = 450 MPa: Asa = pi*0.019^2/4 = 2.835287e-4 m^2;
    steel branch Rg*Rp*Asa*Fu = 1.0*0.75*2.835287e-4*450000 = 95.6909 kN.

    fc' = 20 MPa: Ec = 4700*sqrt(20) = 21019.039 MPa; concrete branch
    0.5*Asa*sqrt(20000*21019039) kPa*m^2 = 91.9154 kN < 95.69 -> CONCRETE.
    fc' = 30 MPa: Ec = 4700*sqrt(30) = 25742.960 MPa; concrete branch
    0.5*Asa*sqrt(30000*25742960) = 124.582 kN > 95.69 -> STEEL."""
    Asa = math.pi * 0.019 ** 2 / 4.0
    st20 = stud_strength(0.019, 20_000.0, 450_000.0)
    assert st20["Asa"] == pytest.approx(Asa, rel=1e-12)
    Ec20 = 4700.0 * math.sqrt(20.0) * 1000.0
    assert st20["Ec"] == pytest.approx(Ec20, rel=1e-12)
    q_conc = 0.5 * Asa * math.sqrt(20_000.0 * Ec20)
    q_steel = 0.75 * Asa * 450_000.0
    assert st20["Qn_concrete"] == pytest.approx(q_conc, rel=1e-12)
    assert st20["Qn_steel"] == pytest.approx(q_steel, rel=1e-12)
    assert st20["governs"] == "concrete"
    assert st20["Qn"] == pytest.approx(q_conc, rel=1e-12)

    st30 = stud_strength(0.019, 30_000.0, 450_000.0)
    Ec30 = 4700.0 * math.sqrt(30.0) * 1000.0
    assert st30["Qn_concrete"] == pytest.approx(
        0.5 * Asa * math.sqrt(30_000.0 * Ec30), rel=1e-12)
    assert st30["governs"] == "steel"
    assert st30["Qn"] == pytest.approx(q_steel, rel=1e-12)
    with pytest.raises(ValueError):
        stud_strength(0.0, 30_000.0, 450_000.0)


# --------------------------------------------------------------------------- #
# 2. full-composite plastic distribution — all three PNA cases
# --------------------------------------------------------------------------- #
def test_composite_flexure_pna_in_slab():
    """Classic textbook case: W18x50, fc' = 30 MPa, beff = 2.25 m,
    t_slab = 0.19 m, hr = 0.075 -> tc = 0.115 m.

    As*Fy = 9.483852e-3*345000 = 3271.929 kN;
    0.85*fc'*beff*tc = 0.85*30000*2.25*0.115 = 6598.125 kN > As*Fy
    -> steel governs, PNA in the slab.
    a = 3271.929/(0.85*30000*2.25) = 0.0570226 m (<= tc);
    Mn = As*Fy*(d/2 + t_slab - a/2)
       = 3271.929*(0.2286 + 0.19 - 0.0285113) = 1276.335 kN*m."""
    r = composite_flexure(AS_W18, FY, D_W18, BF_W18, TF_W18, TW_W18,
                          0.19, 0.075, 30_000.0, 2.25)
    AsFy = AS_W18 * FY
    a = AsFy / (0.85 * 30_000.0 * 2.25)
    assert r["pna"] == "slab"
    assert r["Cf"] == pytest.approx(AsFy, rel=1e-12)
    assert r["Cc_max"] == pytest.approx(6598.125, rel=1e-12)
    assert r["a"] == pytest.approx(a, rel=1e-12)
    assert r["Mn"] == pytest.approx(
        AsFy * (D_W18 / 2.0 + 0.19 - a / 2.0), rel=1e-12)


def test_composite_flexure_pna_in_flange():
    """Contrived weak slab forces the PNA into the flange: fc' = 20 MPa,
    beff = 1.5 m, t_slab = 0.135, hr = 0.075 -> tc = 0.06.

    Cc = 0.85*20000*1.5*0.06 = 1530 kN < As*Fy = 3271.929 kN;
    A_c = (3271.929 - 1530)/(2*345000) = 2.52454e-3 m^2
        <= bf*tf = 0.1905*0.014478 = 2.75806e-3 -> FLANGE;
    x = A_c/bf = 0.0132522 m; a = tc (slab fully crushed);
    Mn = Cc*(hr + tc/2) + Fy*As*d/2 - Fy*bf*x^2
       = 1530*0.105 + 345000*9.483852e-3*0.2286 - 345000*0.1905*x^2
       = 897.071 kN*m  (force sum about the top of steel)."""
    r = composite_flexure(AS_W18, FY, D_W18, BF_W18, TF_W18, TW_W18,
                          0.135, 0.075, 20_000.0, 1.5)
    Cc = 0.85 * 20_000.0 * 1.5 * 0.06
    A_c = (AS_W18 * FY - Cc) / (2.0 * FY)
    x = A_c / BF_W18
    assert A_c <= BF_W18 * TF_W18                      # hand screen
    assert r["pna"] == "flange"
    assert r["C"] == pytest.approx(Cc, rel=1e-12)
    assert r["a"] == pytest.approx(0.06, rel=1e-12)    # a = tc exactly
    assert r["x_pna"] == pytest.approx(x, rel=1e-12)
    Mn_hand = (Cc * (0.075 + 0.03) + FY * AS_W18 * D_W18 / 2.0
               - FY * BF_W18 * x ** 2)
    assert r["Mn"] == pytest.approx(Mn_hand, rel=1e-12)


def test_composite_flexure_pna_in_web():
    """Weaker still: fc' = 20 MPa, beff = 1.0, t_slab = 0.105, hr = 0.075
    -> tc = 0.03; Cc = 0.85*20000*1.0*0.03 = 510 kN.

    A_c = (3271.929 - 510)/690000 = 4.00280e-3 > bf*tf -> WEB;
    A_w = A_c - bf*tf = 1.24474e-3; x = tf + A_w/tw = 0.152521 m;
    compression-steel centroid depth (moments of flange + web block):
      y_c = -(bf*tf*tf/2 + A_w*(tf + A_w/(2*tw)))/A_c;
    Mn = Cc*(hr + tc/2) + Fy*As*d/2 + 2*Fy*A_c*y_c = 708.371 kN*m."""
    r = composite_flexure(AS_W18, FY, D_W18, BF_W18, TF_W18, TW_W18,
                          0.105, 0.075, 20_000.0, 1.0)
    Cc = 0.85 * 20_000.0 * 1.0 * 0.03
    A_c = (AS_W18 * FY - Cc) / (2.0 * FY)
    A_w = A_c - BF_W18 * TF_W18
    assert A_w > 0.0                                   # hand screen
    x = TF_W18 + A_w / TW_W18
    y_c = -(BF_W18 * TF_W18 * TF_W18 / 2.0
            + A_w * (TF_W18 + A_w / (2.0 * TW_W18))) / A_c
    assert r["pna"] == "web"
    assert r["x_pna"] == pytest.approx(x, rel=1e-12)
    Mn_hand = (Cc * (0.075 + 0.015) + FY * AS_W18 * D_W18 / 2.0
               + 2.0 * FY * A_c * y_c)
    assert r["Mn"] == pytest.approx(Mn_hand, rel=1e-12)


def test_partial_composite_studs_govern():
    """Big slab (the PNA-in-slab geometry) but sumQn = 0.5*As*Fy: the stud
    transfer C = 1635.964 kN governs all three candidates.

    a = C/(0.85*30000*2.25) = 0.0285113 m;
    A_c = (As*Fy - 0.5*As*Fy)/(2*Fy) = As/4 = 2.370963e-3 m^2
        <= bf*tf = 2.75806e-3 -> PNA in the FLANGE despite the big slab;
    x = A_c/bf; Mn = C*(0.19 - a/2) + Fy*As*d/2 - Fy*bf*x^2."""
    AsFy = AS_W18 * FY
    r = composite_flexure(AS_W18, FY, D_W18, BF_W18, TF_W18, TW_W18,
                          0.19, 0.075, 30_000.0, 2.25, sumQn=0.5 * AsFy)
    C = 0.5 * AsFy
    a = C / (0.85 * 30_000.0 * 2.25)
    A_c = AS_W18 / 4.0
    x = A_c / BF_W18
    assert r["C"] == pytest.approx(C, rel=1e-12)
    assert r["pna"] == "flange"
    Mn_hand = (C * (0.19 - a / 2.0) + FY * AS_W18 * D_W18 / 2.0
               - FY * BF_W18 * x ** 2)
    assert r["Mn"] == pytest.approx(Mn_hand, rel=1e-12)
    # full composite of the same geometry is strictly stronger
    full = composite_flexure(AS_W18, FY, D_W18, BF_W18, TF_W18, TW_W18,
                             0.19, 0.075, 30_000.0, 2.25)
    assert full["Mn"] > r["Mn"]


def test_transformed_and_equivalent_inertia():
    """I_tr by hand parallel-axis: beff = 1.1, t_slab = 0.15, hr = 0.075
    -> tc = 0.075; n = 8: Ac = 1.1*0.075/8 = 0.0103125 m^2;
    y_slab = d/2 + hr + tc/2 = 0.2286 + 0.075 + 0.0375 = 0.3411 m;
    ybar = Ac*y_slab/(As + Ac); I_tr = Is + As*ybar^2 + 1.1*0.075^3/(12*8)
    + Ac*(y_slab - ybar)^2.  C-I3-4 endpoints: sumQn = Cf -> I_tr,
    sumQn = 0 -> Is; quarter ratio -> Is + 0.5*(I_tr - Is) (sqrt)."""
    Ac = 1.1 * 0.075 / 8.0
    y_slab = D_W18 / 2.0 + 0.075 + 0.0375
    ybar = Ac * y_slab / (AS_W18 + Ac)
    I_hand = (I_W18 + AS_W18 * ybar ** 2 + 1.1 * 0.075 ** 3 / (12.0 * 8.0)
              + Ac * (y_slab - ybar) ** 2)
    I_tr = transformed_inertia(AS_W18, I_W18, D_W18, 1.1, 0.15, 0.075, 8.0)
    assert I_tr == pytest.approx(I_hand, rel=1e-12)
    assert equivalent_inertia(I_W18, I_tr, 1000.0, 1000.0) \
        == pytest.approx(I_tr, rel=1e-12)
    assert equivalent_inertia(I_W18, I_tr, 0.0, 1000.0) \
        == pytest.approx(I_W18, rel=1e-12)
    assert equivalent_inertia(I_W18, I_tr, 250.0, 1000.0) \
        == pytest.approx(I_W18 + 0.5 * (I_tr - I_W18), rel=1e-12)
    # over-studded floors clamp at full composite
    assert equivalent_inertia(I_W18, I_tr, 2000.0, 1000.0) \
        == pytest.approx(I_tr, rel=1e-12)


# --------------------------------------------------------------------------- #
# 3. engine-driven composite checks
# --------------------------------------------------------------------------- #
def _composite_model(neighbors=True):
    """Simply supported W18x50 (released ends) under a 6 x 3 m one-quad
    shell slab (mesh_size 10 -> nodes only at the slab corners, so the
    slab never splits the beams and carries no beam load — the beam
    demands stay statics-exact).  DEAD w = 10, LIVE w = 5 kN/m on B1;
    U = 1.2D + 1.6L -> w_u = 20 kN/m, Mu = 20*6^2/8 = 90 kN*m."""
    m = BuildingModel("comp")
    m.rigid_diaphragms = False
    m.num_modes = 0
    m.add_material(Material("steel", E_STEEL, 0.3, 77.0))
    m.add_material(Material("conc", 25e6, 0.2, 24.0))
    m.add_section(FrameSection.from_library("W18x50", "steel"))
    m.shell_sections["SL"] = ShellSection("SL", "conc", 0.15)
    m.set_stories([3.0])
    m.add_member("beam", "W18x50", (0, 1.5, 3), (6, 1.5, 3),
                 story="S1", releases="Mi,Mj", uid="B1")
    ys = [1.5]
    if neighbors:
        m.add_member("beam", "W18x50", (0, 0.5, 3), (6, 0.5, 3),
                     story="S1", uid="N1")
        m.add_member("beam", "W18x50", (0, 2.7, 3), (6, 2.7, 3),
                     story="S1", uid="N2")
        ys += [0.5, 2.7]
    for x in (0.0, 6.0):
        for y in ys:
            m.supports.append(PointSupport((x, y, 3), FIX))
    for x, y in ((0, 0), (6, 0), (6, 3), (0, 3)):
        m.supports.append(PointSupport((x, y, 3), FIX))
    m.shells.append(ShellRegion("S1", "slab", "shell", "SL",
                                [(0, 0, 3), (6, 0, 3), (6, 3, 3),
                                 (0, 3, 3)],
                                mesh_size=10.0, story="S1"))
    dead = m.pattern("DEAD", "dead")
    dead.member_loads.append(MemberLoad("B1", "udl", 10.0))
    live = m.pattern("LIVE", "live")
    live.member_loads.append(MemberLoad("B1", "udl", 5.0))
    m.add_case("DEAD", {"DEAD": 1.0})
    m.add_case("LIVE", {"LIVE": 1.0})
    m.add_combo("U", {"DEAD": 1.2, "LIVE": 1.6})
    m.validate()
    return m


def _run(model):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return OpenSeesEngine(model).run()


def test_effective_width_neighbor_vs_edge_vs_span():
    """Each I3.1a side term governs somewhere in the one model.

    span/8 = 0.75 m.  B1 (y = 1.5): neighbors at 0.5/2.7 -> half
    distances 0.5 and 0.6 (both < 0.75 and < 1.5 edge) -> beff = 1.1.
    N1 (y = 0.5): edge below at 0.5, neighbor above at 1.5 (half 0.5)
    -> 0.5 + 0.5 = 1.0.  N2 (y = 2.7): edge above at 0.3, neighbor below
    half 0.6 -> 0.9.  Without neighbors (edge distances 1.5 both sides)
    B1's span/8 = 0.75 governs each side -> beff = 1.5."""
    mdl = _composite_model()
    res = _run(mdl)
    chk = {c.uid: c for c in check_composite_beams(mdl, res,
                                                   fc_prime=30_000.0)}
    assert chk["B1"].beff == pytest.approx(1.1, rel=1e-9)
    assert chk["N1"].beff == pytest.approx(1.0, rel=1e-9)
    assert chk["N2"].beff == pytest.approx(0.9, rel=1e-9)
    mdl2 = _composite_model(neighbors=False)
    res2 = _run(mdl2)
    chk2 = check_composite_beams(mdl2, res2, fc_prime=30_000.0)
    assert chk2[0].uid == "B1"
    assert chk2[0].beff == pytest.approx(1.5, rel=1e-9)


def test_composite_engine_full_hand_chain():
    """Every reported number for B1 re-derived longhand.

    Mu = 90 (statics); tc = 0.15 - 0.075 = 0.075; beff = 1.1;
    Cf = min(3271.929, 0.85*30000*1.1*0.075 = 2103.75) = 2103.75 kN;
    n_studs = floor(3/0.3) = 10, Qn = 95.6909 (steel branch, fc 30) ->
    sumQn = 956.909 kN, ratio_composite = 956.909/2103.75 = 0.454859;
    C = 956.909; a = C/(0.85*30000*1.1) = 0.0341144 m;
    A_c = (3271.929 - 956.909)/690000 = 3.35510e-3 > bf*tf -> WEB;
    A_w = A_c - bf*tf; x = tf + A_w/tw;
    y_c = -(bf*tf*tf/2 + A_w*(tf + A_w/(2tw)))/A_c;
    Mn = C*(0.15 - a/2) + Fy*As*d/2 + 2*Fy*A_c*y_c; phiMn = 0.9*Mn;
    pre-composite: 1.4*M_dead = 1.4*45 = 63 kN*m vs 0.9*Zx*Fy (Lb = 0);
    I_tr per test 2's formula at n = Es/Ec = 2e8/(4700*sqrt(30)*1000);
    I_equiv = Is + sqrt(0.454859)*(I_tr - Is);
    defl_LL = (5*5*6^4/(384*2e8*Is)) * Is/I_equiv <= 6/360."""
    mdl = _composite_model()
    res = _run(mdl)
    chk = {c.uid: c for c in check_composite_beams(mdl, res,
                                                   fc_prime=30_000.0)}
    b = chk["B1"]
    assert b.applicable and b.reason == ""
    assert b.governing_combo == "U"
    assert b.Mu == pytest.approx(90.0, rel=1e-6)
    assert b.tc == pytest.approx(0.075, rel=1e-12)
    AsFy = AS_W18 * FY
    Cf = 0.85 * 30_000.0 * 1.1 * 0.075                    # 2103.75, governs
    assert Cf < AsFy
    assert b.C_full == pytest.approx(Cf, rel=1e-9)
    Qn = 0.75 * (math.pi * 0.019 ** 2 / 4.0) * 450_000.0  # steel branch
    assert b.n_studs == 10
    assert b.Qn == pytest.approx(Qn, rel=1e-12)
    sumQn = 10.0 * Qn
    assert b.sumQn == pytest.approx(sumQn, rel=1e-12)
    assert b.ratio_composite == pytest.approx(sumQn / Cf, rel=1e-9)
    # partial-composite Mn longhand (PNA in the web)
    C = sumQn
    a = C / (0.85 * 30_000.0 * 1.1)
    A_c = (AsFy - C) / (2.0 * FY)
    A_w = A_c - BF_W18 * TF_W18
    assert A_w > 0.0
    y_c = -(BF_W18 * TF_W18 * TF_W18 / 2.0
            + A_w * (TF_W18 + A_w / (2.0 * TW_W18))) / A_c
    Mn = C * (0.15 - a / 2.0) + FY * AS_W18 * D_W18 / 2.0 \
        + 2.0 * FY * A_c * y_c
    assert b.pna == "web"
    assert b.phiMn_partial == pytest.approx(0.9 * Mn, rel=1e-9)
    assert b.ratio == pytest.approx(90.0 / (0.9 * Mn), rel=1e-6)
    # pre-composite (unshored): 1.4*Dead on the bare Lb = 0 F2 capacity
    assert b.precomp_Mu == pytest.approx(63.0, rel=1e-6)
    assert b.precomp_ratio == pytest.approx(
        63.0 / (0.9 * ZX_W18 * FY), rel=1e-6)
    # deflection: exact bare-steel recovery scaled by Is/I_equiv
    n = E_STEEL / (4700.0 * math.sqrt(30.0) * 1000.0)
    Ac = 1.1 * 0.075 / n
    y_slab = D_W18 / 2.0 + 0.075 + 0.0375
    ybar = Ac * y_slab / (AS_W18 + Ac)
    I_tr = (I_W18 + AS_W18 * ybar ** 2 + 1.1 * 0.075 ** 3 / (12.0 * n)
            + Ac * (y_slab - ybar) ** 2)
    I_eq = I_W18 + math.sqrt(sumQn / Cf) * (I_tr - I_W18)
    assert b.I_tr == pytest.approx(I_tr, rel=1e-9)
    assert b.I_equiv == pytest.approx(I_eq, rel=1e-9)
    defl = 5.0 * 5.0 * 6.0 ** 4 / (384.0 * E_STEEL * I_W18) * I_W18 / I_eq
    assert b.defl_LL == pytest.approx(defl, rel=1e-6)
    assert b.defl_limit == pytest.approx(6.0 / 360.0, rel=1e-12)
    assert b.defl_limit_ok is True
    assert b.status == "OK"
    d = b.to_dict()
    for key in ("uid", "story", "applicable", "reason", "beff", "tc",
                "C_full", "PNA_case", "phiMn_full", "n_studs", "sumQn",
                "ratio_composite", "phiMn_partial", "Mu", "ratio",
                "precomp_ratio", "I_equiv", "defl_LL", "defl_limit_ok",
                "status"):
        assert key in d
    s = summarize_composite(list(chk.values()))
    assert s["n"] == 3 and s["governing"] == "B1" and s["preliminary"]


def test_composite_applicability_screen_and_params():
    """Non-W-shape and off-slab beams report n/a with a reason; shored
    skips the pre-composite check; bad parameters raise."""
    mdl = _composite_model()
    mdl.add_section(FrameSection.rectangular("R", "conc", 0.3, 0.6))
    mdl.add_member("beam", "R", (0, 1.0, 3), (6, 1.0, 3), story="S1",
                   uid="RC")                       # inside the slab
    mdl.add_member("beam", "W18x50", (0, 10, 3), (6, 10, 3), story="S1",
                   uid="OFF")                      # outside the slab plan
    for x in (0.0, 6.0):
        for y in (1.0, 10.0):
            mdl.supports.append(PointSupport((x, y, 3), FIX))
    mdl.validate()
    res = _run(mdl)
    chk = {c.uid: c for c in check_composite_beams(mdl, res,
                                                   fc_prime=30_000.0,
                                                   shored=True)}
    assert not chk["RC"].applicable
    assert "W-shape" in chk["RC"].reason
    assert not chk["OFF"].applicable
    assert "slab" in chk["OFF"].reason
    assert chk["B1"].applicable
    assert chk["B1"].precomp_ratio is None         # shored: check skipped
    with pytest.raises(ValueError, match="stud_d"):
        check_composite_beams(mdl, res, stud_d=0.0)
    with pytest.raises(ValueError, match="hr"):
        check_composite_beams(mdl, res, hr=-0.01)
    with pytest.raises(ValueError, match="rib_spacing"):
        check_composite_beams(mdl, res, rib_spacing=0.0)
    with pytest.raises(KeyError):
        check_composite_beams(mdl, res, combos=["NOPE"])
    # a model with no beam members short-circuits to []
    empty = BuildingModel("nobeams")
    assert check_composite_beams(empty, res) == []
    assert default_live_case(mdl) == "LIVE"
    assert default_live_case(empty) is None


def test_composite_combo_default_and_static_case_names():
    """combos default to every ADDITIVE combo (here: U, so the governing
    combo is U with Mu = 90); explicit STATIC case names are accepted
    (case DEAD alone: Mu = 10*36/8 = 45); a model without additive
    combos raises when combos are omitted."""
    mdl = _composite_model()
    res = _run(mdl)
    dflt = {c.uid: c for c in check_composite_beams(mdl, res,
                                                    fc_prime=30_000.0)}
    assert dflt["B1"].governing_combo == "U"
    assert dflt["B1"].Mu == pytest.approx(90.0, rel=1e-6)
    dead = {c.uid: c for c in check_composite_beams(
        mdl, res, ["DEAD"], fc_prime=30_000.0)}
    assert dead["B1"].governing_combo == "DEAD"
    assert dead["B1"].Mu == pytest.approx(45.0, rel=1e-6)
    mdl.combos.clear()
    with pytest.raises(ValueError, match="no additive combos"):
        check_composite_beams(mdl, res, fc_prime=30_000.0)


# --------------------------------------------------------------------------- #
# 4. slab strips — Whitney inversion
# --------------------------------------------------------------------------- #
def test_required_steel_roundtrip_exact():
    """The quadratic inversion must round-trip beam_flexure EXACTLY.

    Mu = 100 kN*m/m, b = 1, d = 0.17, fc' = 30 MPa, fy = 420 MPa:
    R = 0.85*30000 = 25500; R*d = 4335;
    disc = 4335^2 - 2*25500*100/0.9 = 18792225 - 5666666.67 = 13125558.3;
    T = 4335 - sqrt(disc) = 712.263 kN; As = T/420000 = 1.69587e-3 m^2.
    Plugging As back: phiMn == 100 to 1e-9 (identity of the inversion)."""
    sol = required_steel(100.0, 0.17, 30_000.0, 420_000.0)
    R = 0.85 * 30_000.0
    T = R * 0.17 - math.sqrt((R * 0.17) ** 2 - 2.0 * R * 100.0 / 0.9)
    assert sol["As"] == pytest.approx(T / 420_000.0, rel=1e-12)
    assert sol["phi"] == pytest.approx(0.9, rel=1e-12)
    assert sol["tension_controlled"]
    fx = beam_flexure(1.0, 0.17, sol["As"], 30_000.0, 420_000.0)
    assert fx["phiMn"] == pytest.approx(100.0, rel=1e-9)
    assert fx["phiMn"] == pytest.approx(100.0, abs=1e-9)


def test_required_steel_zero_and_unsolvable():
    """Mu <= 0 needs no steel; a moment beyond the section's reach (the
    discriminant of the quadratic goes negative) returns None.  The
    tension-controlled ceiling at phi = 0.9 is R*d^2/2 (T = R*d):
    25500*0.17^2/2/0.9... i.e. any Mu > 0.9*R*d^2/2 = 331.6 is a clean
    NG."""
    z = required_steel(0.0, 0.17, 30_000.0, 420_000.0)
    assert z["As"] == 0.0
    R = 0.85 * 30_000.0
    ceiling = 0.9 * R * 0.17 ** 2 / 2.0
    assert required_steel(ceiling * 1.01, 0.17, 30_000.0,
                          420_000.0) is None
    with pytest.raises(ValueError):
        required_steel(10.0, -0.1, 30_000.0, 420_000.0)


def test_strip_layout_hand():
    """ACI min(L1,L2)/4 by hand on [0, 4] with lines {0, 4}, L1 = 4:
    line 0 has no lower neighbour (edge, L2 = 2*0 = 0 below) and L2 = 4
    above -> band (0, 1); line 4 mirrors -> (3, 4); middle = (1, 3).
    Three interior-ish lines on [0, 8] (L1 = 6, spacing 4): widths
    min(6,4)/4 = 1 each side.  No lines -> one full middle strip."""
    bands = strip_layout(0.0, 4.0, [0.0, 4.0], 4.0)
    assert [(b["strip"], b["line"], b["band"]) for b in bands] == [
        ("column", 0.0, (0.0, 1.0)),
        ("middle", None, (1.0, 3.0)),
        ("column", 4.0, (3.0, 4.0)),
    ]
    bands = strip_layout(0.0, 8.0, [0.0, 4.0, 8.0], 6.0)
    assert [(b["strip"], b["band"]) for b in bands] == [
        ("column", (0.0, 1.0)), ("middle", (1.0, 3.0)),
        ("column", (3.0, 5.0)), ("middle", (5.0, 7.0)),
        ("column", (7.0, 8.0)),
    ]
    bands = strip_layout(0.0, 4.0, [], 4.0)
    assert bands == [{"strip": "middle", "line": None, "band": (0.0, 4.0)}]


def _strip_model():
    """4 x 4 m slab on 4 corner columns, meshed 2 x 2 (quad centroids at
    (1,1), (3,1), (1,3), (3,3)) — the synthetic-moment carrier."""
    m = BuildingModel("slab")
    m.rigid_diaphragms = False
    m.num_modes = 0
    m.add_material(Material("conc", 25e6, 0.2, 24.0))
    m.add_section(FrameSection.rectangular("COL", "conc", 0.4, 0.4))
    m.shell_sections["SL"] = ShellSection("SL", "conc", 0.2)
    m.set_stories([4.0])
    for i, (x, y) in enumerate([(0, 0), (4, 0), (4, 4), (0, 4)]):
        m.add_member("column", "COL", (x, y, 0), (x, y, 4), story="S1",
                     uid=f"C{i + 1}")
        m.supports.append(PointSupport((x, y, 0), FIX))
    m.shells.append(ShellRegion("S1", "slab", "shell", "SL",
                                [(0, 0, 4), (4, 0, 4), (4, 4, 4),
                                 (0, 4, 4)],
                                mesh_size=2.0, story="S1"))
    m.pattern("D", "dead")
    m.add_case("G", {"D": 1.0})
    m.validate()
    return m


def _synthetic_results(model, Mxx, Myy):
    """Hand-built results dict: uniform per-quad moments over the real
    mesh (no engine — the strip integration is pure post-processing)."""
    mm = mesh_model(model)
    return {
        "nodes": {i + 1: list(p) for i, p in enumerate(mm.points)},
        "shell_quads": [{"region": q.region,
                         "nodes": [n + 1 for n in q.nodes]}
                        for q in mm.quads],
        "cases": {"G": {"shell_forces": {
            qi: [0.0, 0.0, 0.0, Mxx, Myy, 0.0, 0.0, 0.0]
            for qi in range(len(mm.quads))}}},
    }


def test_slab_strip_uniform_moment_integration():
    """UNIFORM Mxx = 12, Myy = -7 kN*m/m over the 2x2 mesh: every section
    moment must equal (moment) x (strip width) — the area-weighted hand
    sum.  Layout per test_strip_layout_hand: column strips 1 m wide at
    y = 0 and 4, middle strip 2 m; sections at x = 0, 2, 4 (span ends +
    midspan of the single 4 m bay).  x-direction reinforces Mxx (the
    mesher's local x runs along corner0 -> corner1 = global X here)."""
    mdl = _strip_model()
    res = _synthetic_results(mdl, 12.0, -7.0)
    regions = check_slab_strips(mdl, res, "G", fc_prime=30_000.0)
    assert len(regions) == 1
    reg = regions[0]
    assert reg["uid"] == "S1" and reg["case"] == "G"
    assert reg["t"] == pytest.approx(0.2) and reg["d"] == pytest.approx(0.17)
    for direction, m0 in (("x", 12.0), ("y", -7.0)):
        strips = reg["directions"][direction]
        assert [(s["strip"], s["width"]) for s in strips] == [
            ("column", pytest.approx(1.0)),
            ("middle", pytest.approx(2.0)),
            ("column", pytest.approx(1.0)),
        ]
        for s in strips:
            xs = [sec["x"] for sec in s["sections"]]
            assert xs == [pytest.approx(0.0), pytest.approx(2.0),
                          pytest.approx(4.0)]
            for sec in s["sections"]:
                assert sec["mu"] == pytest.approx(m0, rel=1e-12)
                assert sec["Mu"] == pytest.approx(m0 * s["width"],
                                                  rel=1e-12)


def test_slab_strip_rebar_min_governs_and_moment_governs():
    """Low moment: |mu| = 7 -> As_req = T/fy with
    T = 4335 - sqrt(4335^2 - 2*25500*7/0.9) = 45.9957 kN ->
    As_req = 109.513 mm^2/m < As_min = 0.0018*1*0.2 = 360 mm^2/m ->
    minimum governs; 16 mm bar spacing Ab/As_min = 201.06e-6/360e-6
    = 0.5585 m, CAPPED at min(3h, 0.45) = 0.45 m.
    High moment |mu| = 120: As_req = 2064.36 mm^2/m > As_min ->
    spacing = 201.06e-6/As_req = 0.0974 m, and the inversion round-trips
    beam_flexure exactly."""
    mdl = _strip_model()
    low = check_slab_strips(mdl, _synthetic_results(mdl, 1.0, -7.0),
                            "G", fc_prime=30_000.0)[0]
    sec = low["directions"]["y"][0]["sections"][0]
    R = 0.85 * 30_000.0
    T = R * 0.17 - math.sqrt((R * 0.17) ** 2 - 2.0 * R * 7.0 / 0.9)
    as_req = T / 420_000.0                         # m^2/m
    assert sec["As_req"] == pytest.approx(as_req * 1e6, rel=1e-9)
    assert sec["As_min"] == pytest.approx(360.0, rel=1e-12)
    assert sec["min_governs"] is True
    assert sec["spacing"] == pytest.approx(0.45, rel=1e-12)   # 3h/450 cap
    high = check_slab_strips(mdl, _synthetic_results(mdl, 120.0, -7.0),
                             "G", fc_prime=30_000.0)[0]
    sec = high["directions"]["x"][0]["sections"][0]
    T = R * 0.17 - math.sqrt((R * 0.17) ** 2 - 2.0 * R * 120.0 / 0.9)
    as_req = T / 420_000.0
    assert sec["As_req"] == pytest.approx(as_req * 1e6, rel=1e-9)
    assert sec["min_governs"] is False
    Ab = math.pi * 0.016 ** 2 / 4.0
    assert sec["spacing"] == pytest.approx(Ab / as_req, rel=1e-9)
    fx = beam_flexure(1.0, 0.17, sec["As_req"] / 1e6, 30_000.0, 420_000.0)
    assert fx["phiMn"] == pytest.approx(120.0, rel=1e-9)
    # a moment beyond the section's reach reports NG
    ng = check_slab_strips(mdl, _synthetic_results(mdl, 400.0, 0.0),
                           "G", fc_prime=30_000.0)[0]
    sec = ng["directions"]["x"][0]["sections"][0]
    assert sec["status"] == "NG" and sec["As_req"] is None


def test_slab_strip_defaults_and_errors():
    """Default case = first DEAD-classified; unknown case raises KeyError;
    cover outside (0, 1) m raises; a slab-free model returns []."""
    mdl = _strip_model()
    res = _synthetic_results(mdl, 5.0, 5.0)
    dflt = check_slab_strips(mdl, res, fc_prime=30_000.0)   # case = None
    assert dflt[0]["case"] == "G"
    with pytest.raises(KeyError):
        check_slab_strips(mdl, res, "NOPE")
    with pytest.raises(ValueError, match="cover"):
        check_slab_strips(mdl, res, "G", cover=30.0)        # millimetres!
    with pytest.raises(ValueError, match="bar_d"):
        check_slab_strips(mdl, res, "G", bar_d=0.0)
    noslab = BuildingModel("noslab")
    assert check_slab_strips(noslab, res, "G") == []


# --------------------------------------------------------------------------- #
# 5. walking vibration — DG11 chain
# --------------------------------------------------------------------------- #
def test_dg11_hand_chain():
    """fn = 0.18*sqrt(9.81/0.005) = 0.18*44.2945 = 7.97301 Hz;
    ap/g = 0.29*exp(-0.35*7.97301)/(0.03*100) = 0.29*0.0614152/3
         = 5.93680e-3 — typed to 1e-12."""
    fn = natural_frequency(0.005)
    assert fn == pytest.approx(0.18 * math.sqrt(9.81 / 0.005), rel=1e-12)
    ap = walking_acceleration(fn, 100.0)
    assert ap == pytest.approx(
        0.29 * math.exp(-0.35 * fn) / (0.03 * 100.0), rel=1e-12)
    with pytest.raises(ValueError):
        natural_frequency(0.0)
    with pytest.raises(ValueError):
        walking_acceleration(5.0, 0.0)


def test_vibration_engine_hand_chain_and_status_flip():
    """B1's chain re-derived from statics (the v0.16 deflection recovery
    is EXACT for the released-end UDL beam):

    w_sus = 10 + 0.11*5 = 10.55 kN/m;
    delta = 5*w*L^4/(384*E*I) = 5*10.55*1296/(384*2e8*3.329851e-4)
          = 2.673261e-3 m;
    W_beam = w*L = 63.3 kN; trib = 0.5 + 0.6 = 1.1 = beff (span/8 = 0.75
    does not govern) -> W_eff = 63.3*1.1/1.1 = 63.3 kN;
    fn = 0.18*sqrt(9.81/delta) = 10.9040 Hz;
    ap/g = 0.29*exp(-0.35*fn)/(0.03*63.3) = 3.3607e-3 < 0.005 -> OK,
    and NG once ap_limit is tightened below it (status flip)."""
    mdl = _composite_model()
    res = _run(mdl)
    checks = {c.uid: c for c in check_vibration(mdl, res)}
    b = checks["B1"]
    assert b.applicable
    w = 10.0 + 0.11 * 5.0
    delta = 5.0 * w * 6.0 ** 4 / (384.0 * E_STEEL * I_W18)
    assert b.delta_mid == pytest.approx(delta, rel=1e-9)
    assert b.W_eff == pytest.approx(w * 6.0, rel=1e-9)
    fn = 0.18 * math.sqrt(9.81 / delta)
    assert b.fn == pytest.approx(fn, rel=1e-9)
    ap = 0.29 * math.exp(-0.35 * fn) / (0.03 * (w * 6.0))
    assert b.ap_over_g == pytest.approx(ap, rel=1e-9)
    assert b.limit == 0.005 and b.status == "OK"
    # unloaded neighbours have no gravity deflection -> not applicable
    assert not checks["N1"].applicable and not checks["N2"].applicable
    d = checks["B1"].to_dict()
    for key in ("uid", "fn", "delta_mid", "W_eff", "ap_over_g", "limit",
                "status"):
        assert key in d


def test_vibration_status_flip_and_params():
    """B1's ap/g ~ 3.361e-3 sits between limits 0.003 and 0.005, so the
    status must flip when the limit is tightened; beta scales ap/g
    exactly inversely (Eq. 4-1: ap/g ~ 1/beta); parameter and case-name
    errors raise cleanly."""
    mdl = _composite_model()
    res = _run(mdl)
    base = {c.uid: c for c in check_vibration(mdl, res)}
    ap = base["B1"].ap_over_g
    assert 0.003 < ap < 0.005 and base["B1"].status == "OK"
    tight = {c.uid: c for c in check_vibration(mdl, res, ap_limit=0.003)}
    assert tight["B1"].status == "NG"
    assert tight["B1"].ap_over_g == pytest.approx(ap, rel=1e-12)
    half = {c.uid: c for c in check_vibration(mdl, res, beta=0.06)}
    assert half["B1"].ap_over_g == pytest.approx(ap / 2.0, rel=1e-9)
    with pytest.raises(ValueError, match="beta"):
        check_vibration(mdl, res, beta=0.0)
    with pytest.raises(ValueError, match="live_factor"):
        check_vibration(mdl, res, live_factor=-0.1)
    with pytest.raises(KeyError):
        check_vibration(mdl, res, "NOPE")
    with pytest.raises(KeyError):
        check_vibration(mdl, res, live_case="NOPE")


# --------------------------------------------------------------------------- #
# 6. API layer
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client():
    from skyframe.api.server import _state, create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c, _state


def test_api_composite_roundtrip_and_errors(client):
    """Endpoint output equals check_composite_beams() run directly on the
    same model (consistency); 400 on unknown combos / bad hr; a model
    with no beam members returns beams: [] (not an error)."""
    c, state = client
    mdl = _composite_model()
    state["model"] = mdl
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = c.post("/api/design/composite", json={"fc_prime": 30_000.0})
    assert r.status_code == 200
    d = r.get_json()
    assert d["preliminary"] is True
    # resolved parameter echo: request values over the documented defaults
    assert d["params"] == {"fc_prime": 30_000.0, "t_slab": None,
                           "hr": 0.075, "stud_d": 0.019,
                           "stud_Fu": 450_000.0, "rib_spacing": 0.3,
                           "shored": False}
    res = _run(mdl)
    hand = {h.uid: h for h in check_composite_beams(mdl, res,
                                                    fc_prime=30_000.0)}
    by_uid = {b["uid"]: b for b in d["beams"]}
    assert set(by_uid) == {"B1", "N1", "N2"}
    b1 = by_uid["B1"]
    assert b1["applicable"] is True
    assert b1["ratio"] == pytest.approx(hand["B1"].ratio, rel=1e-9)
    assert b1["phiMn_partial"] == pytest.approx(
        hand["B1"].phiMn_partial, rel=1e-9)
    assert b1["PNA_case"] == "web"
    assert d["summary"]["governing"] == "B1"
    r = c.post("/api/design/composite", json={"combos": ["NOPE"]})
    assert r.status_code == 400
    r = c.post("/api/design/composite", json={"hr": -1.0})
    assert r.status_code == 400
    r = c.post("/api/design/composite", json={"shored": "yes"})
    assert r.status_code == 400
    state["model"] = BuildingModel("nobeams")
    r = c.post("/api/design/composite", json={})
    assert r.status_code == 200 and r.get_json()["beams"] == []


def test_api_slab_roundtrip_and_errors(client):
    """Real engine run on the 4-column plate: the endpoint returns the
    3-strip layout in both directions with populated sections; 400 on an
    unknown case / bad cover; a slab-free model returns regions: []."""
    c, state = client
    state["model"] = _strip_model()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = c.post("/api/design/slab", json={"fc_prime": 30_000.0})
    assert r.status_code == 200
    d = r.get_json()
    assert d["preliminary"] is True and d["case"] == "G"
    assert len(d["regions"]) == 1
    reg = d["regions"][0]
    for direction in ("x", "y"):
        strips = reg["directions"][direction]
        assert [s["strip"] for s in strips] == ["column", "middle",
                                                "column"]
        for s in strips:
            assert len(s["sections"]) == 3
            for sec in s["sections"]:
                assert {"x", "Mu", "As_req", "As_min", "spacing",
                        "status"} <= set(sec)
    r = c.post("/api/design/slab", json={"case": "NOPE"})
    assert r.status_code == 400
    r = c.post("/api/design/slab", json={"cover": 30.0})
    assert r.status_code == 400
    state["model"] = BuildingModel("noslab")
    r = c.post("/api/design/slab", json={})
    assert r.status_code == 200 and r.get_json()["regions"] == []


def test_api_vibration_roundtrip_and_errors(client):
    """Endpoint output equals check_vibration() run directly; 400 on an
    unknown case / live_case / bad beta."""
    c, state = client
    mdl = _composite_model()
    state["model"] = mdl
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = c.post("/api/results/vibration", json={})
    assert r.status_code == 200
    d = r.get_json()
    assert d["preliminary"] is True
    assert d["case"] is None and d["live_case"] is None  # server defaults
    res = _run(mdl)
    hand = {h.uid: h for h in check_vibration(mdl, res)}
    by_uid = {b["uid"]: b for b in d["beams"]}
    assert by_uid["B1"]["fn"] == pytest.approx(hand["B1"].fn, rel=1e-9)
    assert by_uid["B1"]["ap_over_g"] == pytest.approx(
        hand["B1"].ap_over_g, rel=1e-9)
    assert by_uid["B1"]["status"] == "OK"
    assert not by_uid["N1"]["applicable"]
    r = c.post("/api/results/vibration", json={"case": "NOPE"})
    assert r.status_code == 400
    r = c.post("/api/results/vibration", json={"live_case": "NOPE"})
    assert r.status_code == 400
    r = c.post("/api/results/vibration", json={"beta": 0.0})
    assert r.status_code == 400
