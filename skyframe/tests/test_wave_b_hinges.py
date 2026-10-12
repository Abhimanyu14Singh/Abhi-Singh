"""Wave B hinges: code-table auto hinges (ASCE 41-17/23, FEMA 356, EC8-3),
V3 / T user hinges, parametric interacting P-M2-M3 / M2-M3 hinges and
wall fiber hinges.  Every value is pinned to a hand computation of the
transcribed code formula (clause cited per test) or a closed form.

Units kN, m, kPa, rad.
"""

import json
import math

import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine
ops = pytest.importorskip("openseespy.opensees")

from skyframe.core import interaction_hinges as cih  # noqa: E402
from skyframe.core import user_hinges as uh  # noqa: E402
from skyframe.core import wall_hinges as cwh  # noqa: E402
from skyframe.core.model import (BuildingModel, FrameSection,  # noqa: E402
                                 Material, NodalLoad, PointSupport,
                                 ShellSection)
from skyframe.design import hinge_tables as ht  # noqa: E402
from skyframe.design.steel import design_properties  # noqa: E402

ES = 199_947_980.0
FY = 345_000.0
L = 3.5


# =========================================================================
# fixtures
# =========================================================================
def _steel_col(P=0.0, section="W14x90", kind="column", L=L, fy=FY,
               Ry=1.1):
    m = BuildingModel(name="sc")
    m.rigid_diaphragms = False
    m.add_material(Material("S", ES, 0.3, 0.0, material_type="steel",
                            fy=fy, Ry=Ry))
    m.set_stories([L])
    if section.startswith("R"):
        b = float(section[1:])
        m.add_section(FrameSection.rectangular(section, "S", b, b))
    else:
        m.add_section(FrameSection.from_library(section, "S"))
    m.add_member(kind, section, (0, 0, 0), (0, 0, L), story="Story1",
                 uid="C1")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    pd = m.pattern("D", "dead")
    pd.nodal_loads.append(NodalLoad((0, 0, L), fz=-P))
    m.pattern("PZ", "other").nodal_loads.append(NodalLoad((0, 0, L),
                                                          fz=-1.0))
    m.pattern("MZ", "other").nodal_loads.append(NodalLoad((0, 0, L),
                                                          mz=1.0))
    return m


def _rc_col(P=0.0, b=0.4, h=0.5, L=3.0, fc=30_000.0):
    m = BuildingModel(name="rc")
    m.rigid_diaphragms = False
    m.add_material(Material("C", 25_000_000.0, 0.2, 0.0, fc=fc))
    m.set_stories([L])
    m.add_section(FrameSection.rectangular("R", "C", b, h))
    m.add_member("column", "R", (0, 0, 0), (0, 0, L), story="Story1",
                 uid="C1")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.pattern("D", "dead").nodal_loads.append(NodalLoad((0, 0, L),
                                                        fz=-P))
    return m


def _auto(code, comp, dof, **params):
    return {"type": "auto", "auto": {"code": code, "component": comp,
                                     "dof": dof, "params": params}}


def _gen(model, auto_prop, P_G=0.0):
    p = uh.normalize_property("A", auto_prop)
    specs, why = ht.generate(model, model.members[0], p["auto"], P_G)
    return specs, why


def _interp(pts, x):
    if x <= pts[0][0]:
        return pts[0][1] * x / pts[0][0]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


# =========================================================================
# 1. table pins (ASCE 41-17 / FEMA 356 / EC8-3)
# =========================================================================
def test_asce41_steel_beam_matches_legacy_auto_m3():
    """Table 9-7.1 beam rows: the generator equals the v0.19 auto_m3
    backbone (same ladder, Eq. 9-1 thy, My = Z Fye)."""
    from skyframe.design.hinges import auto_backbone
    m = _steel_col(section="W18x50", kind="beam")
    specs, _ = _gen(m, _auto("ASCE41-17", "steel_beam", "M3"))
    bb = auto_backbone(m, m.members[0])
    s = specs[0]
    assert s["pos"]["F_B"] == pytest.approx(bb["My"], rel=1e-12)
    for k in ("a", "b"):
        assert s["pos"][k] == pytest.approx(bb[k], rel=1e-12)
    for k in ("IO", "LS", "CP"):
        assert s["acc"][k] == pytest.approx(bb[k], rel=1e-12)
    assert s["meta"]["preliminary"] is False          # verified table


def test_asce41_steel_column_equations():
    """ASCE 41-17 Table 9-7.1 columns (W14x90, L = 3.5 m) at P_G/Pye:
    a = 0.8(1-r)^2.2/(0.1 L/ry + 0.8 h/tw) - 0.0035, b = 7.4(1-r)^2.3/
    (0.5 L/ry + 2.9 h/tw) - 0.006, c = 0.9 - 0.9 r; M_pCE = Z Fye (1 -
    r/2) (r < 0.2) / 9/8 Z Fye (1 - r) (r >= 0.2); IO = 0.5a, LS = 0.75b,
    CP = b; r > 0.6 -> force-controlled."""
    m = _steel_col()
    pr = design_properties("W14x90")
    Fye = 1.1 * FY
    Pye = pr["A"] * Fye
    htw = (pr["d"] - 2 * pr["tf"]) / pr["tw"]
    Lry = L / pr["ry"]
    for r in (0.0, 0.1, 0.3):
        specs, _ = _gen(m, _auto("ASCE41-17", "steel_column", "M3"),
                        r * Pye)
        s = specs[0]
        a = 0.8 * (1 - r) ** 2.2 / (0.1 * Lry + 0.8 * htw) - 0.0035
        b = 7.4 * (1 - r) ** 2.3 / (0.5 * Lry + 2.9 * htw) - 0.006
        My = (pr["Zx"] * Fye * (1 - r / 2) if r < 0.2
              else 9 / 8 * pr["Zx"] * Fye * (1 - r))
        assert s["pos"]["a"] == pytest.approx(a, rel=1e-12)
        assert s["pos"]["b"] == pytest.approx(b, rel=1e-12)
        assert s["pos"]["c"] == pytest.approx(0.9 - 0.9 * r, rel=1e-12)
        assert s["pos"]["F_B"] == pytest.approx(My, rel=1e-12)
        assert s["acc"] == pytest.approx({"IO": 0.5 * a, "LS": 0.75 * b,
                                          "CP": b}, rel=1e-12)
        assert s["meta"]["preliminary"] is True        # unverified rows
    specs, why = _gen(m, _auto("ASCE41-17", "steel_column", "M3"),
                      0.61 * Pye)
    assert specs == [] and "force-controlled" in why
    # PMM = an M2 and an M3 hinge at the same station
    specs, _ = _gen(m, _auto("ASCE41-17", "steel_column", "PMM"), 0.0)
    assert [s["type"] for s in specs] == ["M2", "M3"]
    assert specs[0]["pos"]["F_B"] == pytest.approx(pr["Zy"] * Fye)


def test_asce41_brace_compression_and_tension():
    """AISC 360-16 E3 with Fye (inelastic branch, KL/r = 69) and the
    Table 9-7.1 brace rows: tension W row 10/13/0.6 x Delta_T; compression
    interpolated between stocky (2.1 rt) and slender (4.2 rt)."""
    m = _steel_col(section="R0.1", kind="brace", L=2.0)
    specs, _ = _gen(m, _auto("ASCE41-17", "steel_brace", "P",
                             shape="W"))
    s = specs[0]
    A = 0.01
    r = 0.1 / math.sqrt(12.0)
    Fye = 1.1 * FY
    lam = 2.0 / r
    Fe = math.pi ** 2 * ES / lam ** 2
    assert lam <= 4.71 * math.sqrt(ES / Fye)
    Pce = 0.658 ** (Fye / Fe) * Fye * A
    assert s["neg"]["F_B"] == pytest.approx(Pce, rel=1e-12)
    assert s["pos"]["F_B"] == pytest.approx(A * Fye, rel=1e-12)
    k = ES * A / 2.0
    dT, dc = A * Fye / k, Pce / k
    assert s["pos"]["a"] == pytest.approx(10 * dT, rel=1e-12)
    assert s["pos"]["b"] == pytest.approx(13 * dT, rel=1e-12)
    assert s["acc"]["CP"] == pytest.approx(13 * dT, rel=1e-12)
    rt = math.sqrt(ES / Fye)
    t = (lam - 2.1 * rt) / (2.1 * rt)
    assert 0 < t < 1
    assert s["neg"]["a"] == pytest.approx((1 + t * (0.5 - 1)) * dc,
                                          rel=1e-12)
    assert s["neg"]["b"] == pytest.approx((8 + t * (10 - 8)) * dc,
                                          rel=1e-12)
    assert s["neg"]["c"] == pytest.approx(0.5 + t * (0.3 - 0.5), rel=1e-12)
    assert s["acc_neg"]["LS"] == pytest.approx((7 + t) * dc, rel=1e-12)
    assert ht.brace_shape("HSS6X6X1/2") == "HSS"
    assert ht.brace_shape("HSS6.000X0.500") == "pipe"
    assert ht.brace_shape("L4X4X1/2") == "angle"
    assert ht.brace_shape("2L4X4X1/2") == "2L_in"


def test_rc_section_moment_closed_form():
    """P = 0, tension steel only: Mn = As fy (d - a/2) about the bars ->
    about mid-depth: Cc (h/2 - a/2) + T (d - h/2); a = As fy/(0.85 fc b)."""
    b, h, fc, fy = 0.3, 0.5, 30_000.0, 420_000.0
    d = 0.45
    As = 0.01 * b * d
    Mn = ht.rc_section_Mn(b, h, fc, fy, As, 0.0, d, 0.05, 0.0)
    a = As * fy / (0.85 * fc * b)
    assert Mn == pytest.approx(As * fy * (d - a / 2), rel=1e-9)
    # axial capacity bounds -> None (force-controlled)
    Pmax = 0.85 * fc * (b * h - 2 * As) + fy * 2 * As
    assert ht.rc_section_Mn(b, h, fc, fy, As, As, d, 0.05,
                            1.001 * Pmax) is None


def test_asce41_rc_column_table_10_8():
    """Table 10-8: a = 0.042 - 0.043 n + 0.63 rho_t - 0.023 VyE/VColOE,
    b = 0.5/(5 + n/0.8 / rho_t * fc/fyt) - 0.01, c = 0.24 - 0.4 n,
    IO = min(0.15a, 0.005), LS = 0.5b, CP = 0.7b; V_ColOE Eq. 10-3."""
    m = _rc_col()
    P = 1200.0
    specs, _ = _gen(m, _auto("ASCE41-17", "rc_column", "M3", rho_t=0.004,
                             rho=0.012), P)
    s = specs[0]
    b, h, fc, fy, L_ = 0.4, 0.5, 30_000.0, 420_000.0, 3.0
    d = 0.9 * h
    Mn = ht.rc_section_Mn(b, h, fc, fy, 0.012 * b * d, 0.012 * b * d, d,
                          0.1 * h, P)
    n = P / (b * h * fc)
    # Eq. 10-3 in MPa: Vs = rho_t b fyt (0.8h); Vc = 0.5 sqrt(fc)/(M/Vd)
    #   sqrt(1 + N/(0.5 sqrt(fc) Ag)) 0.8 Ag; M/Vd = L/(2*0.8h) = 3.75
    sq = math.sqrt(30.0) * 1000.0
    Vc = 0.5 * sq / 3.75 * math.sqrt(1 + P / (0.5 * sq * b * h)) * 0.8 \
        * b * h
    Vs = 0.004 * b * fy * 0.8 * h
    vr = (2 * Mn / L_) / (Vs + Vc)
    a = 0.042 - 0.043 * n + 0.63 * 0.004 - 0.023 * vr
    bb = 0.5 / (5 + n / 0.8 / 0.004 * fc / fy) - 0.01
    assert s["meta"]["inputs"]["VColOE"] == pytest.approx(Vs + Vc,
                                                          rel=1e-12)
    assert s["pos"]["F_B"] == pytest.approx(Mn, rel=1e-12)
    assert s["pos"]["a"] == pytest.approx(a, rel=1e-12)
    assert s["pos"]["b"] == pytest.approx(max(a, bb), rel=1e-12)
    assert s["pos"]["c"] == pytest.approx(0.24 - 0.4 * n, rel=1e-12)
    assert s["acc"]["IO"] == pytest.approx(min(0.15 * a, 0.005))
    assert s["acc"]["LS"] == pytest.approx(0.5 * max(a, bb))
    assert s["acc"]["CP"] == pytest.approx(0.7 * max(a, bb))
    assert s["meta"]["preliminary"] is False


def test_asce41_rc_shear_hinge_eq_10_3():
    m = _rc_col()
    specs, _ = _gen(m, _auto("ASCE41-17", "rc_column_shear", "V2",
                             rho_t=0.003, s_over_d=0.9), 500.0)
    s = specs[0]
    b, h = 0.4, 0.5
    sq = math.sqrt(30.0) * 1000.0
    alpha = (1 - 0.9) / 0.25                       # s/d in (0.75, 1)
    Vs = alpha * 0.003 * b * 420_000.0 * 0.8 * h
    Vc = 0.5 * sq / 3.75 * math.sqrt(1 + 500 / (0.5 * sq * b * h)) \
        * 0.8 * b * h
    assert s["pos"]["F_B"] == pytest.approx(Vs + Vc, rel=1e-12)
    assert s["acc"] == {"IO": 0.0, "LS": 0.0, "CP": 0.0}
    assert s["type"] == "V2" and s["meta"]["inputs"]["force_controlled"]


def test_fema356_steel_rows():
    """FEMA 356 Table 5-6 compact beam: IO 1, LS 6, CP 8 thy (primary);
    column P/PCL < 0.2: Eq. 5-4 M_CE = min(1.18 Z Fye (1-P/Pye), Z Fye),
    Eq. 5-2 thy = Z Fye L/(6EI)(1 - P/Pye)."""
    m = _steel_col(section="W18x50", kind="beam")
    s = _gen(m, _auto("FEMA356", "steel_beam", "M3"))[0][0]
    thy = s["thy"]
    pr = design_properties("W18x50")
    Fye = 1.1 * FY
    I = m.sections["W18x50"].I33
    assert thy == pytest.approx(pr["Zx"] * Fye * L / (6 * ES * I))
    assert s["acc"]["LS"] == pytest.approx(6 * thy)
    assert s["acc"]["CP"] == pytest.approx(8 * thy)
    mc = _steel_col()
    pr = design_properties("W14x90")
    Pye = pr["A"] * Fye
    P = 0.1 * Pye
    s = _gen(mc, _auto("FEMA356", "steel_column", "M3"), P)[0][0]
    assert s["pos"]["F_B"] == pytest.approx(pr["Zx"] * Fye)   # capped
    s = _gen(mc, _auto("FEMA356", "steel_column", "M3"), 0.19 * Pye)[0][0]
    assert s["pos"]["F_B"] == pytest.approx(1.18 * pr["Zx"] * Fye * 0.81)
    I = mc.sections["W14x90"].I33
    assert s["thy"] == pytest.approx(pr["Zx"] * Fye * L / (6 * ES * I)
                                     * 0.81)


def test_ec8_3_chord_rotation():
    """EN 1998-3 Annex A: theta_y (A.10b) with phi_y from (A.10)-(A.12)
    and theta_um (A.1); limit states DL/SD/NC -> IO/LS/CP plastic."""
    m = _rc_col(b=0.3, h=0.5, L=3.0)
    N = 600.0
    s = _gen(m, _auto("EC8-3", "rc_column", "M3", rho=0.01, db=0.02),
             N)[0][0]
    b, h, d, d2 = 300.0, 500.0, 450.0, 50.0
    fc, fy, Ec, Es = 30.0, 420.0, 25_000.0, 200_000.0
    rho = rp = 0.01
    Nn = N * 1e3
    alpha = Es / Ec
    A = rho + rp + Nn / (b * d * fy)
    B = rho + rp * d2 / d + Nn / (b * d * fy)
    xi = math.sqrt(alpha ** 2 * A ** 2 + 2 * alpha * B) - alpha * A
    phi = fy / (Es * (1 - xi) * d)
    Lv = 1500.0
    th_y = (phi * (Lv + 0.9 * d) / 3 + 0.0013 * (1 + 1.5 * h / Lv)
            + 0.13 * phi * 20.0 * fy / math.sqrt(fc))
    nu = Nn / (b * h * fc)
    th_um = (1 / 1.5) * 0.016 * 0.3 ** nu * (fc) ** 0.225 \
        * (Lv / h) ** 0.35
    assert s["meta"]["inputs"]["theta_y"] == pytest.approx(th_y, rel=1e-12)
    assert s["meta"]["inputs"]["theta_um"] == pytest.approx(th_um,
                                                            rel=1e-12)
    assert s["acc"]["CP"] == pytest.approx(th_um - th_y, rel=1e-12)
    assert s["acc"]["LS"] == pytest.approx(0.75 * th_um - th_y, rel=1e-12)
    assert s["acc"]["IO"] == 0.0


def test_asce41_23_is_flagged_alias():
    m = _steel_col(section="W18x50", kind="beam")
    a = _gen(m, _auto("ASCE41-17", "steel_beam", "M3"))[0][0]
    b = _gen(m, _auto("ASCE41-23", "steel_beam", "M3"))[0][0]
    assert a["pos"] == b["pos"] and a["acc"] == b["acc"]
    assert b["meta"]["preliminary"] is True
    assert "41-23" in b["meta"]["clause"]


# =========================================================================
# 2. cantilever pushover with an auto P-M hinge (bilinear backbone)
# =========================================================================
def test_auto_pm_column_pushover_follows_backbone():
    """W14x90 cantilever, P_G = 500 kN from the gravity case, auto ASCE
    41-17 P-M3 hinge at the base, Y push.  P_G is read from the linear
    gravity solve; every step satisfies V L = envelope(theta) with
    theta = (d - V L^3/3EI)/L (rigid-plastic hinge in series)."""
    P = 500.0
    m = _steel_col(P=P)
    m.add_hinge_property("AC", _auto("ASCE41-17", "steel_column", "PMM"))
    m.members[0].hinges = [{"property": "AC", "relative_distance": 0.0}]
    m.add_pushover_case("PO", "Y", gravity={"D": 1.0}, target_drift=0.04,
                        steps=40)
    m.validate()
    r = OpenSeesEngine(m).run_pushover("PO")
    hs = [h for h in r.hinge_detail if h.get("auto")]
    assert {h["type"] for h in hs} == {"M2", "M3"}
    h3 = next(h for h in hs if h["type"] == "M3")
    assert h3["auto"]["P_G"] == pytest.approx(P, rel=1e-9)
    pr = design_properties("W14x90")
    Fye = 1.1 * FY
    ratio = P / (pr["A"] * Fye)
    assert h3["My"] == pytest.approx(pr["Zx"] * Fye * (1 - ratio / 2),
                                     rel=1e-12)
    EI = ES * m.sections["W14x90"].I33
    env = h3["envelope"]["positive"]
    n_yield = 0
    for d, V in zip(r.roof_disp, r.base_shear):
        th = (d - V * L ** 3 / (3 * EI)) / L
        M = V * L
        assert M == pytest.approx(_interp(env, th)
                                  + uh.UH_PARALLEL_RATIO * 6 * EI / L * th,
                                  rel=1e-6, abs=1e-6)
        if M > h3["My"] * (1 + 1e-9):
            n_yield += 1
    assert n_yield > 5                           # the hinge yielded
    # first yield bracketed by V L crossing My
    first = next(k for k, V in enumerate(r.base_shear)
                 if V * L > h3["My"])
    assert r.base_shear[first - 1] * L <= h3["My"]
    states = h3["hinge_state"]
    assert states[0] == "A-B" and "B-IO" in states


# =========================================================================
# 3. parametric interacting hinges
# =========================================================================
PY, M3_0, M2_0 = 3000.0, 60.0, 40.0


def _diamond():
    return {"curves": [
        {"angle": a, "points": [[-PY, 0.0], [0.0, M3_0 if a % 180 else
                                             M2_0], [PY, 0.0]]}
        for a in (0.0, 90.0, 180.0, 270.0)]}


def _rc_square():
    m = BuildingModel(name="pm")
    m.rigid_diaphragms = False
    m.add_material(Material("C", 25_000_000.0, 0.2, 0.0))
    m.set_stories([3.0])
    m.add_section(FrameSection.rectangular("R", "C", 0.3, 0.3))
    m.add_member("column", "R", (0, 0, 0), (0, 0, 3.0), story="Story1",
                 uid="C1")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.pattern("D", "dead").nodal_loads.append(NodalLoad((0, 0, 3.0),
                                                        fz=-900.0))
    return m


@pytest.mark.parametrize("direction,axis", [("Y", "M3"), ("X", "M2")])
def test_pmm_surface_yields_at_closed_form(direction, axis):
    """Diamond surface M = M0 (1 - |P|/Py) at P = -900 kN (gravity):
    M3y = 0.7 * 60, M2y = 0.7 * 40 -> yield shear My/L; elastic slope
    1/(L^3/3EI + L^2/k_e); post-yield hinge tangent k_e H/(k_e + H) with
    H = 0.1 My / 0.03 (backbone C = 1.1 at 0.03)."""
    m = _rc_square()
    m.add_hinge_property("PM", {"type": "PMM", "surface": _diamond(),
                                "backbone": [[0, 0], [0, 1.0],
                                             [0.03, 1.1]]})
    m.members[0].hinges = [{"property": "PM", "relative_distance": 0.0}]
    m.add_pushover_case("PO", direction, gravity={"D": 1.0},
                        target_drift=0.03, steps=30)
    m.validate()
    r = OpenSeesEngine(m).run_pushover("PO")
    h = [x for x in r.hinge_detail if x["type"] == "PMM"][0]
    My = 0.7 * (M3_0 if axis == "M3" else M2_0)
    assert h["P"] == pytest.approx(-900.0, rel=1e-9)
    assert h[f"My{axis[1]}"] == pytest.approx(My, rel=1e-12)
    EI = 25e6 * 0.3 ** 4 / 12
    Lc = 3.0
    ke = uh.UH_RIGID_FACTOR * 6 * EI / Lc
    flex = Lc ** 3 / (3 * EI)
    V, d = r.base_shear, r.roof_disp
    assert V[0] == pytest.approx(d[0] / (flex + Lc ** 2 / ke), rel=1e-6)
    H = 0.1 * My / 0.03
    kt = ke * H / (ke + H)
    slope = (V[-1] - V[-2]) / (d[-1] - d[-2])
    assert slope == pytest.approx(1 / (flex + Lc ** 2 / kt), rel=1e-6)
    # yield moment: extrapolate the post-yield line back to the elastic one
    k0 = 1 / (flex + Lc ** 2 / ke)
    dy = (V[-1] - slope * d[-1]) / (k0 - slope)
    assert k0 * dy * Lc == pytest.approx(My, rel=1e-6)
    assert h["hinge_state"][0] == "A-B" and h["hinge_state"][-1] != "A-B"


def test_pmm_axial_value_and_ellipse_fit():
    s = cih.normalize_surface("PMM", _diamond(), "x")
    e = cih.ellipse_at(s, -1500.0)
    assert e["M2y"] == pytest.approx(20.0) and e["M3y"] == pytest.approx(30.0)
    # surface with a 45-degree curve: reported against the ellipse
    s2 = dict(s)
    s2["curves"] = s["curves"] + [{"angle": 45.0, "points": [[-PY, 0.0],
                                                            [0.0, 50.0],
                                                            [PY, 0.0]]}]
    e = cih.ellipse_at(s2, 0.0)
    f45 = next(f for f in e["fit"] if f["angle"] == 45.0)
    c = math.cos(math.radians(45))
    assert f45["M_ellipse"] == pytest.approx(
        1 / math.sqrt((c / 40.0) ** 2 + (c / 60.0) ** 2), rel=1e-12)
    assert cih.curve_moment(s["curves"][0], 4000.0) == 0.0   # outside
    m = _rc_square()
    m.add_hinge_property("PM", {"type": "PMM", "surface": _diamond(),
                                "backbone": [[0, 0], [0, 1.0], [0.03, 1.1]],
                                "axial": {"source": "value",
                                          "value": -1500.0}})
    m.members[0].hinges = [{"property": "PM", "relative_distance": 0.0}]
    m.add_pushover_case("PO", "Y", target_drift=0.02, steps=10)
    m.validate()
    h = [x for x in OpenSeesEngine(m).run_pushover("PO").hinge_detail
         if x["type"] == "PMM"][0]
    assert h["My3"] == pytest.approx(30.0) and h["axial_source"] == "value"


def test_m2m3_elliptical_biaxial_yield():
    """M2M3 hinge at the material level: a 45-degree rotation path yields
    ON the ellipse (M2/M2y)^2 + (M3/M3y)^2 = 1 (exact plasticity)."""
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0, 0, 0)
    ops.node(2, 0, 0, 0)
    ops.fix(1, 1, 1, 1, 1, 1, 1)
    ops.fix(2, 1, 1, 1, 1, 0, 0)
    ops.section("Elliptical", 1, 1000.0, 2000.0, 10.0, 20.0, 0.0, 1e-6,
                1e-6, "My", "Mz")
    ops.element("zeroLengthSection", 1, 1, 2, 1, "-orient", 1, 0, 0, 0, 1,
                0)
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.sp(2, 5, 0.05)
    ops.sp(2, 6, 0.05)
    ops.constraints("Penalty", 1e12, 1e12)
    ops.numberer("Plain")
    ops.system("FullGeneral")
    ops.test("NormDispIncr", 1e-10, 50)
    ops.algorithm("Newton")
    ops.integrator("LoadControl", 0.01)
    ops.analysis("Static")
    for _ in range(100):
        assert ops.analyze(1) == 0
    m2, m3 = ops.eleResponse(1, "basicForce")
    assert (m2 / 10.0) ** 2 + (m3 / 20.0) ** 2 == pytest.approx(1.0,
                                                                abs=1e-6)
    ops.wipe()


def test_interaction_validation_errors():
    bad = [
        {"type": "PMM", "surface": {"curves": [{"angle": 0, "points":
                                                [[0, 1], [1, 0]]}]},
         "backbone": [[0, 0], [0, 1]]},                       # 1 curve
        {"type": "PMM", "surface": _diamond(),
         "backbone": [[0, 0], [0, 1]], "hysteresis": "takeda"},
        # (drop_strength "drops" is accepted since Wave C — strength drop
        # past C by capacity updates, tests/test_hinges_wavec.py)
        {"type": "PMM", "surface": _diamond(),
         "backbone": [[0, 0], [0, 1]], "drop_strength": "bogus"},
        {"type": "M2M3", "surface": _diamond(),
         "backbone": [[0, 0], [0, 1]]},                       # P points
        {"type": "M2M3", "surface": {"curves": [
            {"angle": 0, "points": [[0, 10]]},
            {"angle": 90, "points": [[0, 20]]}]},
         "backbone": [[0, 0], [0, 1]], "axial": {"source": "value",
                                                 "value": 0}},
        {"type": "auto", "auto": {"code": "EC8-3",
                                  "component": "steel_beam"}},
        {"type": "auto", "auto": {"code": "ASCE41-17",
                                  "component": "steel_brace",
                                  "dof": "M3"}},
        {"type": "auto", "auto": {"code": "ASCE41-17",
                                  "component": "rc_column",
                                  "params": {"rho": 0.2}}},
        {"type": "auto", "auto": {"code": "ASCE41-17",
                                  "component": "rc_column"},
         "backbone": [[0, 0], [0, 1]]},
    ]
    for p in bad:
        with pytest.raises(ValueError):
            uh.normalize_property("X", p)
    ok = uh.normalize_property("M", {
        "type": "M2M3", "surface": {"curves": [
            {"angle": 0, "points": [[0, 10]]},
            {"angle": 90, "points": [[0, 20]]}]},
        "backbone": [[0, 0], [0, 1]]})
    assert ok["axial"] is None and ok["drop_strength"] == "holds"


# =========================================================================
# 4. brace hinge buckles at P_CE; V3 / T user hinges
# =========================================================================
@pytest.mark.parametrize("dist", [0.5, 1.0])
def test_brace_hinge_buckles_at_aisc_capacity(dist):
    """A brace with an auto Table 9-7.1 axial hinge (mid-length and end j
    — the end-j hinge swaps sides so compression stays compression) caps
    the compressive force at P_CE (AISC E3 with Fye) and the elastic
    branch is EA/L in series with the hinge."""
    m = _steel_col(section="R0.1", kind="brace", L=3.0)
    m.add_hinge_property("BR", _auto("ASCE41-17", "steel_brace", "P",
                                     shape="W"))
    m.members[0].hinges = [{"property": "BR", "relative_distance": dist}]
    m.add_nonlinear_static_case(
        "AX", {"PZ": 1.0}, load_application="displacement_control",
        steps=40, control_point=[0, 0, 3.0], control_dof="UZ",
        target_disp=-0.02)
    m.validate()
    res = OpenSeesEngine(m).run_nonlinear_static("AX")
    assert res.converged
    s = _gen(m, _auto("ASCE41-17", "steel_brace", "P", shape="W"))[0][0]
    Pce = s["neg"]["F_B"]
    N = res.history["base"]["FZ"]
    assert max(N) == pytest.approx(Pce, rel=2e-6)
    hd = [h for h in res.hinge_detail if h.get("auto")][0]
    assert hd["type"] == "P"
    assert any(st != "A-B" for st in hd["hinge_state"])
    EA = ES * 0.01
    ke = uh.UH_RIGID_FACTOR * EA / 3.0
    d0 = -res.history["disp"][0]
    assert N[0] == pytest.approx(d0 / (3.0 / EA + 1 / ke), rel=1e-6)


def test_v3_and_t_user_hinges():
    m = _steel_col(section="R0.3", L=3.0)
    Vy, Ty = 20.0, 5.0
    m.add_hinge_property("HV3", {"type": "V3", "backbone": [[0, 0], [0, 1.0],
                                                            [0.1, 1.0]],
                                 "scale": {"yield_value": Vy},
                                 "drop_strength": "holds"})
    m.members[0].hinges = [{"property": "HV3", "relative_distance": 0.0}]
    m.add_pushover_case("PO", "X", target_drift=0.004, steps=40)
    m.validate()
    r = OpenSeesEngine(m).run_pushover("PO")
    EI = ES * 0.3 ** 4 / 12
    ke = uh.UH_RIGID_FACTOR * 12 * EI / 27.0
    assert r.base_shear[0] == pytest.approx(
        r.roof_disp[0] / (27.0 / (3 * EI) + 1 / ke), rel=1e-6)
    assert r.base_shear[-1] == pytest.approx(Vy, rel=1e-6)
    hd = [h for h in r.hinge_detail if h.get("user")][0]
    assert hd["type"] == "V3"
    # torsion hinge: torque capped at Ty, elastic GJ/L + k_e in series
    m2 = _steel_col(section="R0.3", L=3.0)
    m2.add_hinge_property("HT", {"type": "T", "backbone": [[0, 0], [0, 1.0],
                                                           [0.1, 1.0]],
                                 "scale": {"yield_value": Ty},
                                 "drop_strength": "holds"})
    m2.members[0].hinges = [{"property": "HT", "relative_distance": 0.0}]
    m2.add_nonlinear_static_case(
        "TOR", {"MZ": 1.0}, load_application="displacement_control",
        steps=20, control_point=[0, 0, 3.0], control_dof="RZ",
        target_disp=0.01)
    m2.validate()
    res = OpenSeesEngine(m2).run_nonlinear_static("TOR")
    assert res.converged
    hd = [h for h in res.hinge_detail if h.get("user")][0]
    assert hd["type"] == "T"
    assert abs(hd["moment"][-1]) == pytest.approx(Ty, rel=1e-6)
    sec = m2.sections["R0.3"]
    GJ = m2.materials["S"].G * sec.J
    keT = uh.UH_RIGID_FACTOR * GJ / 3.0
    th0 = res.history["disp"][0]
    assert abs(hd["moment"][0]) == pytest.approx(
        th0 / (3.0 / GJ + 1 / keT), rel=1e-6)


# =========================================================================
# 5. wall fiber hinge vs hand moment-curvature
# =========================================================================
WALL = {"rho_v": 0.005, "rho_b": 0.02, "boundary_ratio": 0.1,
        "fy": 420_000.0, "fc": None, "n_fibers": 40, "lp_ratio": 0.5,
        "confined": False, "stories": None}


def _ops_section_mphi(lw, t, fc, Ec, wd, P, kappas):
    """Moment of the ENGINE's wall fiber section (zeroLengthSection,
    axial load P held, curvature imposed)."""
    from skyframe.engine.wall_hinges import _fiber_section
    ops.wipe()
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0, 0, 0)
    ops.node(2, 0, 0, 0)
    ops.fix(1, 1, 1, 1, 1, 1, 1)
    ops.fix(2, 0, 1, 1, 1, 1, 0)
    _fiber_section(1, lw, t, fc, Ec, Ec / 2.4, wd, 10)
    ops.element("zeroLengthSection", 1, 1, 2, 1)
    ops.timeSeries("Constant", 1)
    ops.pattern("Plain", 1, 1)
    ops.load(2, -P, 0, 0, 0, 0, 0)
    ops.constraints("Plain")
    ops.numberer("Plain")
    ops.system("FullGeneral")
    ops.test("NormDispIncr", 1e-12, 100)
    ops.algorithm("Newton")
    ops.integrator("LoadControl", 0.0)
    ops.analysis("Static")
    assert ops.analyze(1) == 0
    ops.timeSeries("Linear", 2)
    ops.pattern("Plain", 2, 2)
    ops.load(2, 0, 0, 0, 0, 0, 1.0)
    out = []
    k_prev = 0.0
    for k in kappas:
        ops.integrator("DisplacementControl", 2, 6, k - k_prev)
        ops.analysis("Static")
        assert ops.analyze(1) == 0
        k_prev = k
        out.append(ops.eleResponse(1, "basicForce")[1])
    ops.wipe()
    return out


def test_wall_fiber_section_matches_hand_moment_curvature():
    """Rectangular wall lw = 3, t = 0.25, fc = 30 MPa: the engine fiber
    section (zeroLengthSection, axial held, curvature imposed) reproduces
    the hand strain-compatibility M-phi (Concrete01 envelope, Steel01
    bars, axial by bisection) within 0.5 % (n = 40 fibers).  At P = 0
    every fiber loads monotonically; at P = 1500 kN the compression
    pre-strain UNLOADS the fibers on the tension side once curvature is
    imposed (Concrete01 unloads on its degraded secant, the monotonic hand
    envelope does not), so the comparison starts at 0.25 phi_y where that
    path effect is < 0.01 % (measured 1.2 % at 0.01 phi_y).  The hand
    routine itself is pinned to the closed form of the Hognestad parabola
    in the fully compressed range: with eps = e_a - k y,
    M = (2 fc/e0) k I_c (1 + e_a/e0) + Es k sum(A y^2)."""
    lw, t, fc, Ec = 3.0, 0.25, 30_000.0, 25_000_000.0
    bars = cwh.section_layout(lw, t, WALL)
    ky = cwh.yield_curvature(WALL["fy"], lw)
    for P, ks in ((0.0, [0.05, 0.25, 0.5, 1, 2, 4]),
                  (1500.0, [0.25, 0.5, 1, 2, 4])):
        kappas = [f * ky for f in ks]
        hand = cwh.moment_curvature(lw, t, fc, Ec, WALL["fy"], bars, P,
                                    kappas, strips=4000)
        eng = _ops_section_mphi(lw, t, fc, Ec, WALL, P, kappas)
        for h, e in zip(hand, eng):
            assert abs(e) == pytest.approx(abs(h), rel=5e-3)
    # closed form (hand routine), fully compressed + elastic bars
    P = 1500.0
    e0 = 2 * fc / Ec
    k = 0.01 * ky
    As = sum(A for _, A in bars)
    lo, hi = -e0, 0.0
    for _ in range(200):
        ea = 0.5 * (lo + hi)
        r = -ea / e0
        N = -(t * lw * fc * (2 * r - r * r - (k * lw / e0) ** 2 / 12)) \
            + 200e6 * ea * As
        if N > -P:
            hi = ea
        else:
            lo = ea
    Ic = t * lw ** 3 / 12
    Mc = 2 * fc / e0 * k * Ic * (1 + ea / e0) \
        + 200e6 * k * sum(A * y * y for y, A in bars)
    assert (k * lw / 2) < -ea                      # fully compressed
    hand = cwh.moment_curvature(lw, t, fc, Ec, WALL["fy"], bars, P, [k],
                                strips=4000)[0]
    assert abs(hand) == pytest.approx(Mc, rel=1e-5)


def _wall_model(wh=None):
    m = BuildingModel(name="w")
    m.num_modes = 0
    m.add_material(Material("C", E=25_000_000.0, nu=0.2, unit_weight=0.0,
                            fc=30_000.0))
    m.add_shell_section(ShellSection("W", "C", 0.25))
    m.set_stories([3.5])
    r = m.add_shell("wall", "shell", "W", [(0, 0, 0), (3, 0, 0),
                                           (3, 0, 3.5), (0, 0, 3.5)],
                    mesh_size=0.5, story="Story1", uid="W1")
    r.pier = "P1"
    pd = m.pattern("D", "dead")
    for i in range(7):
        pd.nodal_loads.append(NodalLoad((0.5 * i, 0, 3.5), fz=-1500 / 7))
    if wh is not None:
        m.wall_hinges = {"P1": wh}
    m.add_pushover_case("PO", "X", gravity={"D": 1.0}, target_drift=0.006,
                        steps=12)
    return m


def test_wall_hinge_pushover_statics_and_states():
    m = _wall_model(dict(WALL, n_fibers=20))
    m.validate()
    d = m.to_dict()
    assert d["wall_hinges"]["P1"]["n_fibers"] == 20
    back = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert back.to_dict() == d
    r = OpenSeesEngine(m).run_pushover("PO")
    w = [h for h in r.hinge_detail if h.get("kind") == "wall"]
    assert len(w) == 1 and w[0]["story"] == "Story1"
    h = w[0]
    assert h["axial"][0] == pytest.approx(1500.0, rel=1e-3)
    for M, V in zip(h["moment"], r.base_shear):
        # the wide column carries the base moment (ghost shell ~1e-4)
        assert abs(M) == pytest.approx(V * 3.5, rel=5e-3)
    assert h["hinge_state"][0] == "A-B" and h["hinge_state"][-1] != "A-B"
    assert h["IO"] < h["LS"] < h["CP"]
    # linear analyses never see the wall hinge
    m2 = _wall_model(None)
    m2.add_case("D", {"D": 1.0})
    m.add_case("D", {"D": 1.0})
    a = OpenSeesEngine(m).run_static("D").to_dict()
    b = OpenSeesEngine(m2).run_static("D").to_dict()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_wall_hinge_validation():
    m = _wall_model({"rho_v": -0.1})
    with pytest.raises(ValueError):
        m.validate()
    m = _wall_model({"stories": ["Nope"]})
    with pytest.raises(ValueError):
        m.validate()
    m = _wall_model({"bogus": 1})
    with pytest.raises(ValueError):
        m.validate()


# =========================================================================
# 6. round trip / defaults byte-identical / API
# =========================================================================
def test_auto_property_roundtrip_and_unused_byte_identical():
    def build(with_props):
        m = _steel_col(P=300.0)
        if with_props:
            m.add_hinge_property("AC", _auto("ASCE41-17", "steel_column",
                                             "PMM", rho_t=0.003))
            m.add_hinge_property("PM", {"type": "PMM",
                                        "surface": _diamond(),
                                        "backbone": [[0, 0], [0, 1.0]]})
        m.add_pushover_case("PO", "Y", gravity={"D": 1.0},
                            target_drift=0.02, steps=8,
                            hinges="column_base", default_My=500.0)
        m.validate()
        return m
    a, b = build(False), build(True)
    db = b.to_dict()
    hp = db["hinge_properties"]
    assert hp["AC"]["auto"]["dof"] == "PMM"
    assert hp["PM"]["axial"] == {"source": "gravity", "value": None,
                                 "gravity": None}
    assert BuildingModel.from_dict(json.loads(json.dumps(db))).to_dict() \
        == db
    db.pop("hinge_properties")
    assert json.dumps(a.to_dict(), sort_keys=True) == json.dumps(
        db, sort_keys=True)
    ra = OpenSeesEngine(a).run_pushover("PO").to_dict()
    rb = OpenSeesEngine(b).run_pushover("PO").to_dict()
    assert json.dumps(ra, sort_keys=True) == json.dumps(rb, sort_keys=True)
    assert "wall_hinges" not in a.to_dict()


def test_hinges_api_endpoints():
    from skyframe.api.server import create_app
    app = create_app()
    c = app.test_client()
    cat = c.get("/api/hinges/catalog").get_json()
    assert "ASCE41-17" in cat["codes"] and "EC8-3" in cat["codes"]
    assert cat["codes"]["ASCE41-17"]["steel_brace"]["dofs"] == ["P"]
    m = _steel_col()
    rv = c.post("/api/hinges/auto-preview", json={
        "model": m.to_dict(), "members": ["C1"], "P_G": 0.0,
        "property": _auto("ASCE41-17", "steel_column", "M3")})
    assert rv.status_code == 200
    pv = rv.get_json()["previews"][0]
    assert pv["hinges"][0]["type"] == "M3"
    assert c.post("/api/hinges/auto-preview", json={
        "model": m.to_dict(), "members": ["NOPE"],
        "property": _auto("ASCE41-17", "steel_column", "M3")}
    ).status_code == 400
    sv = c.post("/api/hinges/surface-preview", json={
        "property": {"type": "PMM", "surface": _diamond(),
                     "backbone": [[0, 0], [0, 1]]}, "P": -1500.0})
    assert sv.get_json()["M3y"] == pytest.approx(30.0)


def test_db_edit_tables_round_trip_and_edit():
    from skyframe.core import modeltables as MT
    m = _steel_col(P=100.0)
    m.add_hinge_property("AC", _auto("ASCE41-17", "steel_column", "PMM",
                                     rho_t=0.003))
    m.add_hinge_property("PM", {"type": "PMM", "surface": _diamond(),
                                "backbone": [[0, 0], [0, 1.0], [0.03, 1.1]]})
    m.wall_hinges = {"P1": dict(WALL, stories=["Story1"])}
    m.validate()
    d = json.loads(json.dumps(m.to_dict()))
    keys = [t.key for t in MT.catalogue() if t.editable]
    assert {"hinge_surfaces", "wall_hinges"} <= set(keys)
    rows = {k: json.loads(json.dumps(MT.get_table(d, k)["rows"]))
            for k in keys}
    mm, errs, _ = MT.apply_tables(d, rows)
    assert errs == []
    out = mm.to_dict()
    for k in ("hinge_properties", "wall_hinges", "members"):
        assert json.dumps(out[k], sort_keys=True) == json.dumps(
            d[k], sort_keys=True)
    # edit: one surface point, a wall-hinge ratio and the auto dof
    srows = rows["hinge_surfaces"]
    r0 = next(r for r in srows if r["hinge"] == "PM" and r["angle"] == 90.0
              and r["P"] == 0.0)
    r0["M"] = 66.0
    wrows = rows["wall_hinges"]
    wrows[0]["rho_v"] = 0.004
    hrows = rows["hinge_properties"]
    next(r for r in hrows if r["name"] == "AC")["auto_dof"] = "M3"
    mm, errs, _ = MT.apply_tables(d, {"hinge_surfaces": srows,
                                      "wall_hinges": wrows,
                                      "hinge_properties": hrows})
    assert errs == []
    hp = mm.hinge_properties
    c90 = next(c for c in hp["PM"]["surface"]["curves"]
               if c["angle"] == 90.0)
    assert c90["points"][1] == [0.0, 66.0]
    assert mm.wall_hinges["P1"]["rho_v"] == 0.004
    assert hp["AC"]["auto"]["dof"] == "M3"
