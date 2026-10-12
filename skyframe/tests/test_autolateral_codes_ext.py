"""v1.19 auto lateral generators: international seismic and wind codes and
the ASCE 7 Fig. 27.3-8 wind load cases (CONTRACT "Auto lateral loads:
international seismic and wind codes, ASCE 7 wind load cases").

Every expected value is computed INDEPENDENTLY here from the code formula
(clause cited in the comment), not through the module helpers, and the
key numbers are also pinned as decimals worked by hand.

Building A: the 3-story hand example of test_loads_v116 (elevations 4 / 7
/ 10 m, W = 1000 / 1000 / 800 kN, sum w h = 19 000, plan 10 x 6 m: wind X
loads the 6 m facade, depth 10 m; wind Y the 10 m facade, depth 6 m).
Tributary facade heights: 3.5 / 3.0 / 1.5 m.
Building T: a 6-story tower, 6 m stories (z = 6 .. 36 m), plan 8 x 8 m.
"""
import json
import math

import pytest

from skyframe.core import autolateral as al
from skyframe.core import wind_codes_ext as wx
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 StoryForce)
from skyframe.engine.opensees_engine import OpenSeesEngine
from test_loads_v116 import _bldg

FT = 0.3048
MPH = 0.44704
H = {"S1": 4.0, "S2": 7.0, "S3": 10.0}
W = {"S1": 1000.0, "S2": 1000.0, "S3": 800.0}
SWH = 19000.0
TRIB = {"S1": 3.5, "S2": 3.0, "S3": 1.5}


def _F(s):
    return {r["story"]: r["F"] for r in s["stories"]}


def _wh(V, n):
    """V w_n h_n / sum w h (k = 1)."""
    return V * W[n] * H[n] / SWH


def _tall(n=6, hs=6.0, lx=8.0, ly=8.0):
    m = BuildingModel("tall")
    m.add_material(Material("steel", 2.0e8, 0.3, 77.0))
    m.add_section(FrameSection.from_library("W14x90", "steel"))
    names = [f"L{i + 1}" for i in range(n)]
    m.set_stories([hs] * n, names=names)
    for x, y in ((0, 0), (lx, 0), (lx, ly), (0, ly)):
        for i, s in enumerate(names):
            m.add_member("column", "W14x90", (x, y, i * hs),
                         (x, y, (i + 1) * hs), story=s)
    m.story_masses = {s: 100.0 for s in names}
    return m


# =================================================================== seismic
def test_nzs1170_5_hand_example():
    """NZS 1170.5: Z 0.4, soil C, mu 4, R 1, T1 = 0.6 s.
    §3.1.2 Ch(0.6) = 2.0 (0.5/0.6)^0.75 = 1.744392; C = 0.4 Ch = 0.697757.
    §5.2.1.1 kmu = (4-1) 0.6/0.7 + 1 = 3.571429 (T1 < 0.7, soil C); Sp =
    0.7 (§4.4.2, mu >= 2); Cd = C Sp/kmu = 0.136760 (>= max(0.04, 0.03)).
    V = 2800 Cd = 382.929 kN.  §6.2.1.3: Ft = 0.08 V at the top, 0.92 V
    by w h."""
    s = al.compute(_bldg(), "nzs1170_5", Z=0.4, soil="C", mu=4.0, T=0.6)
    Ch = 2.0 * (0.5 / 0.6) ** 0.75
    kmu = 3.0 * 0.6 / 0.7 + 1.0
    Cd = Ch * 0.4 * 0.7 / kmu
    V = Cd * 2800.0
    assert s["Ch"] == pytest.approx(Ch, rel=1e-12)
    assert Ch == pytest.approx(1.744392, rel=1e-6)
    assert s["kmu"] == pytest.approx(kmu, rel=1e-12)
    assert s["Cd"] == pytest.approx(0.136760, rel=1e-5)
    assert s["V"] == pytest.approx(382.929, rel=1e-5)
    assert s["V"] == pytest.approx(V, rel=1e-12)
    F = _F(s)
    assert F["S1"] == pytest.approx(_wh(0.92 * V, "S1"), rel=1e-12)
    assert F["S2"] == pytest.approx(_wh(0.92 * V, "S2"), rel=1e-12)
    assert F["S3"] == pytest.approx(_wh(0.92 * V, "S3") + 0.08 * V,
                                    rel=1e-12)
    assert sum(F.values()) == pytest.approx(V, rel=1e-12)


def test_nzs1170_5_branches_and_floor():
    """Ch: plateau for T < 0.1 (ESM, bracketed Table 3.1 value), class D
    3/T^... ; kmu soil E (T1 < 1, mu >= 1.5): (mu-1.5) T1 + 1.5 with T1 >=
    0.4 s; Cd floor (Z/20 + 0.02) R (Eq. 5.2(2)); Sp = 1.3 - 0.3 mu for 1
    < mu < 2; ZR <= 0.7."""
    from skyframe.core import seismic_codes_ext as sx
    assert sx.nzs_ch(0.05, "A") == 2.35
    assert sx.nzs_ch(1.0, "D") == pytest.approx(2.4 * 0.75 ** 0.75)
    assert sx.nzs_ch(2.0, "E") == pytest.approx(3.32 / 2.0)
    assert sx.nzs_ch(4.0, "C") == pytest.approx(3.96 / 16.0)
    assert sx.nzs_kmu(0.2, 3.0, "E") == pytest.approx(1.5 * 0.4 + 1.5)
    assert sx.nzs_kmu(0.2, 3.0, "C") == pytest.approx(2.0 * 0.4 / 0.7 + 1)
    assert sx.nzs_sp(1.5) == pytest.approx(0.85)
    # long period + high ductility: C Sp/kmu = 0.3 x 3.15/16 x 0.7/6 =
    # 0.00689 -> floor max(0.3/20 + 0.02, 0.03) = 0.035
    s = al.compute(_bldg(), "nzs1170_5", Z=0.3, soil="A", mu=6.0, T=4.0)
    assert s["Cd"] == pytest.approx(0.3 / 20 + 0.02, rel=1e-12)
    s = al.compute(_bldg(), "nzs1170_5", Z=0.13, soil="A", mu=6.0, T=4.0)
    assert s["Cd"] == pytest.approx(0.03, rel=1e-12)
    # Z R capped at 0.7: Z 0.5, R 1.8 -> 0.7
    s = al.compute(_bldg(), "nzs1170_5", Z=0.5, R=1.8, soil="C", mu=1.0,
                   T=0.2)
    assert s["C"] == pytest.approx(2.93 * 0.7, rel=1e-12)


def test_as1170_4_hand_example():
    """AS 1170.4: Z 0.1, kp 1, site Ce, Sp/mu = 0.77/2.  Eq. 6.2(7): T1 =
    1.25 x 0.075 x 10^0.75 = 0.527195 s; Table 6.4 Ce: Ch = 1.25/T1 =
    2.371039 (< 3.68); coef = 0.1 x 2.371039 x 0.385 = 0.0912850; V =
    255.598 kN.  §6.3 k = 1 + (0.527195 - 0.5)/2 = 1.013597."""
    s = al.compute(_bldg(), "as1170_4", Z=0.1, site="Ce")
    T1 = 1.25 * 0.075 * 10 ** 0.75
    Ch = 1.25 / T1
    V = 0.1 * Ch * 0.77 / 2.0 * 2800.0
    k = 1.0 + (T1 - 0.5) / 2.0
    assert s["T"] == pytest.approx(0.527195, rel=1e-6)
    assert s["Ch"] == pytest.approx(2.371039, rel=1e-6)
    assert s["V"] == pytest.approx(V, rel=1e-12)
    assert s["V"] == pytest.approx(255.598, rel=1e-5)
    den = sum(W[n] * H[n] ** k for n in W)
    for n, f in _F(s).items():
        assert f == pytest.approx(V * W[n] * H[n] ** k / den, rel=1e-12)
    # kp Z >= 0.08 (Table 3.3), Ch De at T = 2 s: 2.97/4, coef >= 0.01
    # (0.08 x 0.7425 x 0.67/6 = 0.00663 -> 0.01)
    s = al.compute(_bldg(), "as1170_4", Z=0.05, site="De", T=2.0, mu=6.0,
                   Sp=0.67)
    assert s["kpZ"] == 0.08
    assert s["Ch"] == pytest.approx(2.97 / 4.0)
    assert s["Cs"] == 0.01
    s = al.compute(_bldg(), "as1170_4", Z=0.05, site="De", T=2.0, mu=3.0,
                   Sp=0.67)
    assert s["Cs"] == pytest.approx(0.08 * 0.7425 * 0.67 / 3, rel=1e-12)


def test_gb50011_hand_example():
    """GB 50011: intensity 8 frequent -> alpha_max 0.16 (Table 5.1.4-1);
    site II group 1 -> Tg 0.35 (Table 5.1.4-2); T1 0.6 s, zeta 0.05 (gamma
    0.9, eta2 1).  §5.1.5 Tg < T <= 5Tg: alpha1 = (0.35/0.6)^0.9 x 0.16 =
    0.0985020.  Geq = 0.85 x 2800 = 2380 kN, FEk = 234.435 kN.  Table
    5.2.1: T1 > 1.4 Tg, Tg <= 0.35: delta_n = 0.08 x 0.6 + 0.07 = 0.118.
    F_i = FEk (1 - 0.118) G H/sum G H; top + 0.118 FEk."""
    s = al.compute(_bldg(), "gb50011", T=0.6, intensity="8", site="II",
                   group=1)
    a1 = (0.35 / 0.6) ** 0.9 * 0.16
    FEk = a1 * 0.85 * 2800.0
    assert s["alpha1"] == pytest.approx(a1, rel=1e-12)
    assert s["alpha1"] == pytest.approx(0.0985020, rel=1e-5)
    assert s["V"] == pytest.approx(FEk, rel=1e-12)
    assert s["delta_n"] == pytest.approx(0.118, rel=1e-12)
    F = _F(s)
    for n in ("S1", "S2"):
        assert F[n] == pytest.approx(_wh(FEk * 0.882, n), rel=1e-12)
    assert F["S3"] == pytest.approx(_wh(FEk * 0.882, "S3") + 0.118 * FEk,
                                    rel=1e-12)


def test_gb50011_curve_branches_and_damping():
    """§5.1.5 curve: T < 0.1: (0.45 + 10 (eta2 - 0.45) T) amax; T > 5 Tg:
    (eta2 0.2^gamma - eta1 (T - 5Tg)) amax.  zeta 0.02: gamma = 0.9 +
    0.03/0.42, eta1 = 0.02 + 0.03/4.64, eta2 = 1 + 0.03/0.112 (Eq.
    5.1.5-1..3).  Rare level: alpha_max 0.90 and Tg + 0.05."""
    from skyframe.core import seismic_codes_ext as sx
    assert sx.gb_alpha(0.05, 0.16, 0.35)["alpha"] == pytest.approx(
        (0.45 + 10 * 0.55 * 0.05) * 0.16)
    assert sx.gb_alpha(2.0, 0.16, 0.35)["alpha"] == pytest.approx(
        (0.2 ** 0.9 - 0.02 * (2.0 - 1.75)) * 0.16)
    c = sx.gb_alpha(0.5, 0.16, 0.35, zeta=0.02)
    g = 0.9 + 0.03 / 0.42
    assert c["gamma"] == pytest.approx(g)
    assert c["eta1"] == pytest.approx(0.02 + 0.03 / 4.64)
    assert c["eta2"] == pytest.approx(1 + 0.03 / 0.112)
    assert c["alpha"] == pytest.approx((0.35 / 0.5) ** g * c["eta2"] * 0.16)
    s = al.compute(_bldg(), "gb50011", T=0.3, level="rare")
    assert s["alpha_max"] == 0.90 and s["Tg"] == pytest.approx(0.40)
    assert s["delta_n"] == 0.0                    # T1 <= 1.4 Tg


def test_tsc2018_hand_example():
    """TBDY 2018: SS 1.0, S1 0.3, ZC -> FS 1.2, F1 1.5 (Tables 2.1/2.2),
    SDS 1.2, SD1 0.45, TA 0.075, TB 0.375 s.  RC frame Ct 0.1: TpA =
    0.1 x 10^0.75 = 0.562341 s > TB: Sae = 0.45/0.562341 = 0.800226; Ra =
    R/I = 8 (Eq. 4.1); V = 2800 x 0.100028 = 280.079 kN (>= 0.04 x 2800 x
    1.2 = 134.4, Eq. 4.19).  dFN = 0.0075 x 3 x V = 6.30178 (Eq. 4.22)."""
    s = al.compute(_bldg(), "tsc2018", SS=1.0, S1=0.3, R=8.0, D=3.0)
    T = 0.1 * 10 ** 0.75
    V = 2800.0 * (0.45 / T) / 8.0
    assert s["FS"] == 1.2 and s["F1"] == 1.5
    assert s["T"] == pytest.approx(0.562341, rel=1e-6)
    assert s["V"] == pytest.approx(V, rel=1e-12)
    assert s["V"] == pytest.approx(280.079, rel=1e-6)
    dF = 0.0075 * 3 * V
    assert s["dFN"] == pytest.approx(dF, rel=1e-12)
    F = _F(s)
    assert F["S1"] == pytest.approx(_wh(V - dF, "S1"), rel=1e-12)
    assert F["S3"] == pytest.approx(_wh(V - dF, "S3") + dF, rel=1e-12)
    # T <= TB: Ra = D + (R/I - D) T/TB; Sae plateau SDS
    s = al.compute(_bldg(), "tsc2018", SS=1.0, S1=0.3, R=8.0, D=3.0,
                   T=0.2)
    Ra = 3.0 + 5.0 * 0.2 / 0.375
    assert s["Ra"] == pytest.approx(Ra)
    assert s["V"] == pytest.approx(2800 * 1.2 / Ra, rel=1e-12)
    # Table 2.1 interpolation ZD, SS 0.6: 1.4 + (1.2 - 1.4) 0.1/0.25
    s = al.compute(_bldg(), "tsc2018", SS=0.6, S1=0.15, R=8.0, D=3.0,
                   site="ZD")
    assert s["FS"] == pytest.approx(1.32)
    assert s["F1"] == pytest.approx(2.3)
    # T capped at 1.4 TpA (§4.7.3.4); minimum 0.04 I SDS W governs
    s = al.compute(_bldg(), "tsc2018", SS=0.5, S1=0.1, R=8.0, D=3.0,
                   T=5.0, I=1.5, site="ZA")
    assert s["T"] == pytest.approx(1.4 * T)
    assert s["V"] == pytest.approx(0.04 * 2800 * 1.5 * 0.4, rel=1e-12)


def test_kds41_hand_example():
    """KDS 41 17 00: S 0.22, Fa 1.3, Fv 1.5 -> SDS = 0.22 x 2.5 x 1.3 x
    2/3 = 0.476667, SD1 = 0.22 x 1.5 x 2/3 = 0.22.  RC MRF Ta = 0.0466 x
    10^0.9 = 0.370157; R 5: Cs = min(0.22/(5 x 0.370157) = 0.118869,
    0.476667/5 = 0.0953333) = 0.0953333; V = 266.933 kN.  Given T 1.0 s ->
    capped at Cu Ta, Cu(0.22) = 1.48 (Table 7.2-1, linear): T = 0.547832,
    Cs = 0.22/(5 x 0.547832) = 0.0803167, k = 1.023916."""
    s = al.compute(_bldg(), "kds41", S=0.22, Fa=1.3, Fv=1.5, R=5.0)
    assert s["SDS"] == pytest.approx(0.22 * 2.5 * 1.3 * 2 / 3)
    assert s["SD1"] == pytest.approx(0.22)
    assert s["Cs"] == pytest.approx(0.476667 / 5, rel=1e-5)
    assert s["V"] == pytest.approx(266.933, rel=1e-5)
    for n, f in _F(s).items():
        assert f == pytest.approx(_wh(s["V"], n), rel=1e-12)
    s = al.compute(_bldg(), "kds41", S=0.22, Fa=1.3, Fv=1.5, R=5.0, T=1.0)
    Ta = 0.0466 * 10 ** 0.9
    assert s["Cu"] == pytest.approx(1.48)
    assert s["T"] == pytest.approx(1.48 * Ta, rel=1e-12)
    assert s["Cs"] == pytest.approx(0.22 / (5 * 1.48 * Ta), rel=1e-12)
    assert s["k"] == pytest.approx(1 + (1.48 * Ta - 0.5) / 2)


def test_nscp2015_hand_example():
    """NSCP 2015 zone 4, soil SD, Na 1.2, Nv 1.4: Ca = 0.44 x 1.2 = 0.528,
    Cv = 0.64 x 1.4 = 0.896 (Tables 208-7/8).  TA = 0.0731 x 10^0.75 =
    0.411072 s.  Eq. 208-8: 0.896 x 2800/(8.5 x 0.411072) = 718.0 kN >
    208-9 cap 2.5 x 0.528 x 2800/8.5 = 434.8235 kN -> V = 434.8235 (>=
    0.11 Ca W = 162.6, >= 0.8 Z Nv W/R = 147.6).  T <= 0.7 s: Ft = 0."""
    s = al.compute(_bldg(), "nscp2015", R=8.5, soil="SD", Na=1.2, Nv=1.4)
    assert s["Ca"] == pytest.approx(0.528) and s["Cv"] == pytest.approx(0.896)
    assert s["V"] == pytest.approx(2.5 * 0.528 * 2800 / 8.5, rel=1e-12)
    assert s["V"] == pytest.approx(434.8235, rel=1e-6)
    assert s["Ft"] == 0.0
    for n, f in _F(s).items():
        assert f == pytest.approx(_wh(s["V"], n), rel=1e-12)


def test_nscp2015_top_force_tall():
    """Building T (h 36 m, 6 x 100 t): TA = 0.0488 x 36^0.75 (other) =
    0.715386 s > 0.7 -> Ft = 0.07 T V (Eq. 208-15).  Ca/Cv overrides 0.4 /
    0.56, I 1, R 5.5: V = 0.56 W/(5.5 T) (between the 208-9/10/11
    bounds)."""
    m = _tall()
    s = al.compute(m, "nscp2015", R=5.5, Ca=0.4, Cv=0.56, structure="other")
    T = 0.0488 * 36 ** 0.75
    Wt = 600 * 9.80665
    V = 0.56 * Wt / (5.5 * T)
    assert s["T"] == pytest.approx(T, rel=1e-12)
    assert s["V"] == pytest.approx(V, rel=1e-12)
    Ft = 0.07 * T * V
    assert s["Ft"] == pytest.approx(Ft, rel=1e-12)
    F = _F(s)
    swh = sum(98.0665 * 6 * (i + 1) for i in range(6))
    assert F["L1"] == pytest.approx((V - Ft) * 98.0665 * 6 / swh, rel=1e-9)
    assert F["L6"] == pytest.approx((V - Ft) * 98.0665 * 36 / swh + Ft,
                                    rel=1e-9)


def test_ntc_cdmx_hand_example():
    """NTC-Sismo 2017: a0 0.2, c 0.6, Ta 0.4, Tb 1.0, k 0.8, Q 3, R0 2.
    T = 0.6 (plateau): a = 0.6, Q' = 1 + 2 sqrt(1/0.8) = 3.236068, R = 2;
    Cs = 0.6/(3.236068 x 2) = 0.0927051; V = 259.574 kN, F ~ w h.
    T = 1.5 > Tb: p = 0.8 + 0.2 (1/1.5)^2 = 0.888889, a = 0.6 p (1/1.5)^2
    = 0.237037, Q' = 1 + 2 sqrt(p/0.8).  T = 0.2 < Ta: a = 0.2 + 0.4 x 0.5
    = 0.4, Q' = 1 + 2 sqrt(1.25) 0.5, R = 2 + 1 - sqrt(0.5)."""
    kw = dict(a0=0.2, c=0.6, Ta=0.4, Tb=1.0, k=0.8, Q=3.0, R0=2.0)
    s = al.compute(_bldg(), "ntc_cdmx", T=0.6, **kw)
    assert s["Qp"] == pytest.approx(1 + 2 * math.sqrt(1.25))
    assert s["Cs"] == pytest.approx(0.0927051, rel=1e-6)
    assert s["V"] == pytest.approx(2800 * 0.6 / (2 * (1 + 2 * 1.25 ** 0.5)))
    for n, f in _F(s).items():
        assert f == pytest.approx(_wh(s["V"], n), rel=1e-12)
    s = al.compute(_bldg(), "ntc_cdmx", T=1.5, **kw)
    p = 0.8 + 0.2 / 2.25
    assert s["p"] == pytest.approx(p)
    assert s["a"] == pytest.approx(0.6 * p / 2.25)
    assert s["Qp"] == pytest.approx(1 + 2 * math.sqrt(p / 0.8))
    s = al.compute(_bldg(), "ntc_cdmx", T=0.2, **kw)
    assert s["a"] == pytest.approx(0.4)
    assert s["Qp"] == pytest.approx(1 + 2 * math.sqrt(1.25) * 0.5)
    assert s["R"] == pytest.approx(3 - math.sqrt(0.5))


@pytest.mark.parametrize("code,kw", [
    ("nzs1170_5", {"Z": 0.4, "T": 0.6}),
    ("as1170_4", {"Z": 0.1}),
    ("gb50011", {"T": 0.6}),
    ("tsc2018", {"SS": 1.0, "S1": 0.3, "R": 8.0, "D": 3.0}),
    ("kds41", {"S": 0.22, "R": 5.0}),
    ("nscp2015", {"R": 8.5}),
    ("ntc_cdmx", {"T": 0.6, "a0": 0.2, "c": 0.6, "Ta": 0.4, "Tb": 1.0}),
])
def test_seismic_generate_writes_quake_pattern(code, kw):
    m = _bldg()
    s = al.compute(m, code, **kw)
    pat = al.generate(m, code, None, -0.05, direction="Y", **kw)
    assert pat.name == al._DEFAULT_NAME[code] and pat.kind == "quake"
    assert pat.ecc == -0.05 and pat.accidental_torsion
    assert pat.ecc_y is None
    assert [sf.fx for sf in pat.story_forces] == [0.0] * 3
    assert sum(sf.fy for sf in pat.story_forces) == pytest.approx(s["V"])
    with pytest.raises(ValueError):
        al.compute(m, code, **{**kw, "direction": "Z"})


def test_validation_errors():
    m = _bldg()
    with pytest.raises(ValueError, match="soil"):
        al.compute(m, "nzs1170_5", Z=0.4, soil="F")
    with pytest.raises(ValueError, match="site"):
        al.compute(m, "as1170_4", Z=0.1, site="X")
    with pytest.raises(ValueError, match="intensity"):
        al.compute(m, "gb50011", T=0.5, intensity="10")
    with pytest.raises(ValueError, match="Z must be"):
        al.compute(m, "nscp2015", R=8.5, Z=0.3)
    with pytest.raises(ValueError, match="Tb"):
        al.compute(m, "ntc_cdmx", T=1, a0=.2, c=.6, Ta=1.0, Tb=0.5)
    with pytest.raises(ValueError, match="load_case"):
        al.compute(m, "asce7_16_wind", V=50, load_case=5)
    with pytest.raises(ValueError, match="terrain"):
        al.compute(m, "en1991_wind", vb0=27, terrain="V")
    with pytest.raises(ValueError, match="unknown parameter"):
        al.params_from_body({"code": "kds41", "S": 0.2, "R": 5, "Q": 1})


# ===================================================================== wind
def test_asce7_16_case1_hand_example():
    """ASCE 7-16, V 50 m/s, exposure C, Kd 0.85, Kzt 1, Ke 1 (ze 0).
    Table 26.10-1 (7-16 alpha 9.5, zg 900 ft): z = 10 m = 32.808 ft ->
    Kz = 2.01 (32.808/900)^(2/9.5) = 1.000933; z = 4 m (13.1 ft < 15 ft)
    -> Kz(15 ft) = 0.8489.  qz = 0.613 Kz Kzt Kd Ke V^2 (Eq. 26.10-1.SI).
    G 0.85 (§26.11.1), Cp_w 0.8, L/B = 10/6 -> Cp_l = -0.5 + 0.2 x 0.667
    = -0.366667 (Fig. 27.3-1).  p = G (qz 0.8 + qh 0.366667) (Eq.
    27.4-1, internal pressure cancels); F = p x trib x 6 m."""
    s = al.compute(_bldg(), "asce7_16_wind", V=50.0)

    def kz(z):
        return 2.01 * (max(z / FT, 15.0) / 900.0) ** (2 / 9.5)

    assert kz(10.0) == pytest.approx(1.000933, rel=1e-5)
    assert kz(4.0) == pytest.approx(0.84893, rel=1e-4)
    q = {n: 0.613 * kz(H[n]) * 0.85 * 2500 / 1000 for n in H}
    cpl = -0.5 + 0.2 * (10 / 6 - 1)
    assert s["cp_leeward"] == pytest.approx(cpl)
    assert s["G"] == 0.85
    F = _F(s)
    for n in H:
        p = 0.85 * (q[n] * 0.8 + q["S3"] * abs(cpl))
        assert F[n] == pytest.approx(p * TRIB[n] * 6.0, rel=1e-12)
    assert s["qh"] == pytest.approx(0.613 * kz(10) * 2500 / 1000)
    assert s["V"] == pytest.approx(58.08, rel=1e-4)


def test_asce7_case2_torsion_moments():
    """Fig. 27.3-8 Case 2: 0.75 PX with MT = 0.75 (PWX + PLX) BX eX, eX =
    +-0.15 BX (BX = 6 m normal to X wind).  Story torque = F_case2 x 0.15
    x 6.  The generated pattern carries accidental torsion ecc = +-0.15,
    which the engine applies as Mz = fx ecc Ly (same value)."""
    m = _bldg()
    c1 = _F(al.compute(m, "asce7_16_wind", V=50.0))
    s = al.compute(m, "asce7_16_wind", V=50.0, load_case=2, ecc_sign=-1)
    assert s["case_ecc"] == -0.15
    for r in s["stories"]:
        assert r["F"] == pytest.approx(0.75 * c1[r["story"]], rel=1e-12)
        assert r["Mz"] == pytest.approx(
            -0.75 * c1[r["story"]] * 0.15 * 6.0, rel=1e-12)
    pat = al.generate(m, "asce7_16_wind", "W2", direction="Y", V=50.0,
                      load_case=2)
    c1y = _F(al.compute(m, "asce7_16_wind", V=50.0, direction="Y"))
    assert pat.kind == "wind" and pat.accidental_torsion
    assert pat.ecc == 0.15 and pat.ecc_y is None
    assert {sf.story: sf.fy for sf in pat.story_forces} == pytest.approx(
        {n: 0.75 * v for n, v in c1y.items()}, rel=1e-12)
    # an explicit user ecc wins over the case eccentricity
    pat = al.generate(m, "asce7_16_wind", "W2b", 0.05, V=50.0, load_case=2)
    assert pat.ecc == 0.05


def test_asce7_case3_case4_combined_factors():
    """Case 3: 0.75 PX + 0.75 PY together (no torsion); Case 4: 0.563 PX
    + 0.563 PY (0.75 x 0.75 = 0.5625) with MT = 0.563 (PX BX + PY BY) e,
    e = 0.15 - both torques ADD (the Y eccentricity follows the sign of
    the Y component through ecc_y)."""
    m = _bldg()
    for code in ("asce7_16_wind", "asce7_22_wind"):
        px = _F(al.compute(m, code, V=50.0, direction="X", G=0.85,
                           cp_windward=0.8, cp_leeward=-0.5))
        py = _F(al.compute(m, code, V=50.0, direction="Y", G=0.85,
                           cp_windward=0.8, cp_leeward=-0.5))
        s3 = al.compute(m, code, V=50.0, load_case=3, transverse_sign=-1,
                        G=0.85, cp_windward=0.8, cp_leeward=-0.5)
        assert "case_ecc" not in s3
        for r in s3["stories"]:
            n = r["story"]
            assert r["fx"] == pytest.approx(0.75 * px[n], rel=1e-12)
            assert r["fy"] == pytest.approx(-0.75 * py[n], rel=1e-12)
        s4 = al.compute(m, code, V=50.0, load_case=4, transverse_sign=-1,
                        G=0.85, cp_windward=0.8, cp_leeward=-0.5)
        assert s4["case_ecc"] == 0.15 and s4["case_ecc_y"] == -0.15
        for r in s4["stories"]:
            n = r["story"]
            assert r["fx"] == pytest.approx(0.5625 * px[n], rel=1e-12)
            assert r["fy"] == pytest.approx(-0.5625 * py[n], rel=1e-12)
            assert r["Mz"] == pytest.approx(
                0.5625 * 0.15 * (px[n] * 6.0 + py[n] * 10.0), rel=1e-12)
        # case 4 driven from Y: X is the transverse component
        s4y = al.compute(m, code, V=50.0, load_case=4, direction="Y",
                         transverse_sign=-1, ecc_sign=-1, G=0.85,
                         cp_windward=0.8, cp_leeward=-0.5)
        assert s4y["case_ecc"] == 0.15 and s4y["case_ecc_y"] == -0.15
        for r in s4y["stories"]:
            assert r["F"] == pytest.approx(0.5625 * py[r["story"]])
            assert r["fx"] == pytest.approx(-0.5625 * px[r["story"]])


def test_asce7_case4_engine_torque():
    """Analysed: the base torque of the case-4 (+X, -Y, +e) pattern minus
    that of the same forces without torsion = -sum MT (pure story torques,
    reaction opposite), MT = 0.5625 x 0.15 (PX 6 + PY 10) per story."""
    m = _bldg()
    pat = al.generate(m, "asce7_16_wind", "W4", V=50.0, load_case=4,
                      transverse_sign=-1)
    assert pat.ecc == 0.15 and pat.ecc_y == -0.15
    m.pattern("W4N", "wind")
    m.patterns["W4N"].story_forces = [StoryForce(sf.story, sf.fx, sf.fy)
                                      for sf in pat.story_forces]
    m.add_case("W4", {"W4": 1.0})
    m.add_case("W4N", {"W4N": 1.0})
    res = OpenSeesEngine(m).run()
    mt = sum(0.15 * (abs(sf.fx) * 6.0 + abs(sf.fy) * 10.0)
             for sf in pat.story_forces)
    d = res.cases["W4"].base["MZ"] - res.cases["W4N"].base["MZ"]
    assert d == pytest.approx(-mt, rel=1e-6)
    # round trip keeps ecc_y; legacy patterns omit it
    d1 = m.to_dict()
    assert d1["patterns"]["W4"]["ecc_y"] == -0.15
    assert "ecc_y" not in d1["patterns"]["W4N"]
    m2 = BuildingModel.from_dict(json.loads(json.dumps(d1)))
    assert m2.to_dict() == d1
    m2.patterns["W4"].ecc_y = 0.9
    with pytest.raises(ValueError, match="ecc_y"):
        m2.validate()


def test_asce7_all_cases_set():
    """generate_asce7_wind_cases writes the 12 Fig. 27.3-8 patterns."""
    m = _bldg()
    pats = wx.generate_asce7_wind_cases(m, "asce7_22_wind", "W", V=45.0)
    names = [p.name for p in pats]
    assert names == ["W-1X", "W-1Y", "W-2X+e", "W-2X-e", "W-2Y+e", "W-2Y-e",
                     "W-3X+Y", "W-3X-Y", "W-4X+Y+e", "W-4X+Y-e", "W-4X-Y+e",
                     "W-4X-Y-e"]
    by = {p.name: p for p in pats}
    assert not by["W-1X"].accidental_torsion
    assert by["W-2Y-e"].ecc == -0.15
    assert by["W-4X-Y-e"].ecc == -0.15 and by["W-4X-Y-e"].ecc_y == 0.15
    fx1 = sum(sf.fx for sf in by["W-1X"].story_forces)
    fx4 = sum(sf.fx for sf in by["W-4X+Y+e"].story_forces)
    assert fx4 == pytest.approx(0.5625 * fx1, rel=1e-12)


def test_asce7_22_ext_defaults_identical_to_v116():
    """Without the v1.19 keywords asce7_22_wind is the v1.16 generator."""
    m = _bldg()
    for kw in ({}, {"G": 0.85, "cp_windward": 0.8, "cp_leeward": -0.5},
               {"exposure": "B", "ze": 500.0, "cp_total": 1.1}):
        assert al.compute(m, "asce7_22_wind", V=50.0, **kw) == \
            al.compute_asce7_22_wind(m, V=50.0, **kw)
        # the generic 7-22 path gives the same numbers (load_case 1)
        g = wx.compute_asce7_wind(m, "7-22", 50.0, **{
            **({"cp_total": 1.3} if "cp_windward" not in kw else {}),
            **{k: v for k, v in kw.items()}})
        old = al.compute_asce7_22_wind(m, V=50.0, **kw)
        for a, b in zip(g["stories"], old["stories"]):
            assert a["F"] == pytest.approx(b["F"], rel=1e-12)


def test_asce7_topographic_factor():
    """Fig. 26.8-1 escarpment, exposure C: H 20 m, Lh 50 m (H/Lh 0.4 >=
    0.2, H >= 15 ft), x 10 m upwind (mu 1.5), gamma 2.5: K1 = 0.85 x 0.4 =
    0.34, K2 = 1 - 10/75 = 0.866667, K3(z) = exp(-2.5 z/50); Kzt = (1 +
    K1 K2 K3)^2 -> at z = 10 m: (1 + 0.34 x 0.866667 x 0.606531)^2 =
    1.389391.  H/Lh 0.8 > 0.5: K1 uses 0.5, K2/K3 use Lh = 2H.  H/Lh <
    0.2: Kzt = 1."""
    k = wx.asce_kzt(10.0, "escarpment", 20.0, 50.0, 10.0, "C", True)
    K3 = math.exp(-0.5)
    assert k["K1"] == pytest.approx(0.34)
    assert k["K2"] == pytest.approx(1 - 10 / 75)
    assert k["Kzt"] == pytest.approx((1 + 0.34 * (1 - 10 / 75) * K3) ** 2)
    assert k["Kzt"] == pytest.approx(1.389391, rel=1e-6)
    k = wx.asce_kzt(5.0, "ridge", 40.0, 50.0, -20.0, "D", False)
    assert k["K1"] == pytest.approx(1.55 * 0.5)
    assert k["K2"] == pytest.approx(1 - 20 / (1.5 * 80))
    assert k["K3"] == pytest.approx(math.exp(-3 * 5 / 80))
    assert wx.asce_kzt(5.0, "hill", 5.0, 50.0, 0.0, "C")["Kzt"] == 1.0
    # exposure B needs H >= 60 ft
    assert wx.asce_kzt(5.0, "hill", 15.0, 50.0, 0.0, "B")["Kzt"] == 1.0
    # story pressures use Kzt(z): case-1 force ratio = Kzt(z) on qz
    m = _bldg()
    base = al.compute(m, "asce7_16_wind", V=50.0, cp_total=1.3, G=1.0)
    topo = al.compute(m, "asce7_16_wind", V=50.0, cp_total=1.3, G=1.0,
                      topo_feature="escarpment", topo_H=20.0, topo_Lh=50.0,
                      topo_x=10.0)
    for a, b in zip(topo["stories"], base["stories"]):
        kzt = (1 + 0.34 * (1 - 10 / 75) * math.exp(-2.5 * a["h"] / 50)) ** 2
        assert a["F"] == pytest.approx(b["F"] * kzt, rel=1e-12)
    assert topo["Kzt_h"] == pytest.approx(1.389391, rel=1e-6)


def _gust_hand(h, B, L, V, exposure, n1=None, beta=0.02):
    """§26.11 by hand, feet / mph (Table 26.11-1)."""
    bb, ab, c, l, eps, zmin = {"B": (0.45, 1 / 4, 0.3, 320, 1 / 3, 30),
                               "C": (0.65, 1 / 6.5, 0.2, 500, 1 / 5, 15),
                               "D": (0.8, 1 / 9, 0.15, 650, 1 / 8, 7)}[
        exposure]
    hf, Bf, Lf = h / FT, B / FT, L / FT
    zb = max(0.6 * hf, zmin)
    Iz = c * (33 / zb) ** (1 / 6)                      # Eq. 26.11-7
    Lz = l * (zb / 33) ** eps                          # Eq. 26.11-9
    Q = math.sqrt(1 / (1 + 0.63 * ((Bf + hf) / Lz) ** 0.63))  # 26.11-8
    if n1 is None:
        return 0.925 * (1 + 1.7 * 3.4 * Iz * Q) / (1 + 1.7 * 3.4 * Iz)
    Vz = bb * (zb / 33) ** ab * (88 / 60) * V / MPH     # Eq. 26.11-16
    N1 = n1 * Lz / Vz                                  # 26.11-14
    Rn = 7.47 * N1 / (1 + 10.3 * N1) ** (5 / 3)        # 26.11-13

    def rl(e):                                         # 26.11-15
        return 1 / e - (1 - math.exp(-2 * e)) / (2 * e * e)
    R = math.sqrt(Rn * rl(4.6 * n1 * hf / Vz) * rl(4.6 * n1 * Bf / Vz)
                  * (0.53 + 0.47 * rl(15.4 * n1 * Lf / Vz)) / beta)
    gR = (math.sqrt(2 * math.log(3600 * n1))
          + 0.577 / math.sqrt(2 * math.log(3600 * n1)))  # 26.11-11
    return 0.925 * (1 + 1.7 * Iz * math.sqrt(3.4 ** 2 * Q ** 2
                                             + gR ** 2 * R ** 2)) \
        / (1 + 1.7 * 3.4 * Iz)                         # Eq. 26.11-10


def test_asce7_gust_factor_rigid_and_flexible():
    """Rigid (§26.11.4): building A, wind X (B 6 m, h 10 m), exposure C:
    zbar = max(0.6 x 32.81, 15) = 19.685 ft, Iz = 0.2 (33/19.685)^(1/6)
    = 0.217984, Lz = 500 (19.685/33)^0.2 = 450.92 ft, Q = sqrt(1/(1 +
    0.63 (52.49/450.92)^0.63)) = 0.92747, G = 0.925 (1 + 5.78 Iz Q)/(1 +
    5.78 Iz) = 0.88760.  Flexible (§26.11.5): building T (h 36 m, B = L = 8 m), n1 =
    0.4 Hz, beta 0.02, V 50 m/s, exposure B."""
    g = wx.asce_gust("C", 10.0, 6.0, 10.0, 50.0)
    G = _gust_hand(10.0, 6.0, 10.0, 50.0, "C")
    assert g["gust"] == "rigid"
    assert g["G"] == pytest.approx(G, rel=1e-12)
    assert g["Iz"] == pytest.approx(0.2 * (33 / (0.6 * 10 / FT)) ** (1 / 6))
    assert G == pytest.approx(0.88760, abs=5e-5)
    assert g["Q"] == pytest.approx(0.92747, abs=5e-5)
    gf = wx.asce_gust("B", 36.0, 8.0, 8.0, 50.0, n1=0.4, beta=0.02)
    Gf = _gust_hand(36.0, 8.0, 8.0, 50.0, "B", n1=0.4)
    assert gf["gust"] == "flexible"
    assert gf["G"] == pytest.approx(Gf, rel=1e-12)
    # generator uses it: p scales with G (split pressure)
    m = _tall()
    s = al.compute(m, "asce7_22_wind", V=50.0, exposure="B",
                   gust="flexible", n1=0.4, cp_windward=0.8,
                   cp_leeward=-0.5)
    assert s["G"] == pytest.approx(Gf, rel=1e-12)
    ref = al.compute_asce7_22_wind(m, V=50.0, exposure="B", G=Gf,
                                   cp_windward=0.8, cp_leeward=-0.5)
    for a, b in zip(s["stories"], ref["stories"]):
        assert a["F"] == pytest.approx(b["F"], rel=1e-12)
    with pytest.raises(ValueError, match="flexible"):
        al.compute(m, "asce7_22_wind", V=50.0, gust="flexible", n1=2.0)


def test_en1991_hand_example():
    """EN 1991-1-4, vb0 27 m/s, terrain III (z0 0.3, zmin 5 m), cdir =
    cseason = co = kI = 1, rho 1.25.  kr = 0.19 (0.3/0.05)^0.07 =
    0.215389 (4.5).  qp(z) = (1 + 7/ln(z/z0)) 0.5 rho (kr ln(z/z0) vb)^2
    (4.8).  Building A, wind X: h 10, b 6, d 10 -> h/d 1: cpe D +0.8, E
    -0.5 (Table 7.1), f_corr 0.85 (§7.2.2(3)).  Fig. 7.4 (b < h <= 2b):
    ze = b = 6 m for z <= 6 m (S1), ze = h above.  qp(10) = 0.778749 kPa.
    F = 0.85 (0.8 qp(ze) + 0.5 qp(h)) trib b."""
    s = al.compute(_bldg(), "en1991_wind", vb0=27.0, terrain="III")
    kr = 0.19 * (0.3 / 0.05) ** 0.07

    def qp(z):
        z = max(z, 5.0)
        return (1 + 7 / math.log(z / 0.3)) * 0.5 * 1.25 * (
            kr * math.log(z / 0.3) * 27) ** 2 / 1000

    assert kr == pytest.approx(0.215389, rel=1e-5)
    assert s["qh"] == pytest.approx(qp(10.0), rel=1e-12)
    assert qp(10.0) == pytest.approx(0.778749, rel=1e-5)
    assert s["cpe_D"] == 0.8 and s["cpe_E"] == -0.5 and s["f_corr"] == 0.85
    F = _F(s)
    ze = {"S1": 6.0, "S2": 10.0, "S3": 10.0}
    for n in H:
        p = 0.85 * (0.8 * qp(ze[n]) + 0.5 * qp(10.0))
        assert F[n] == pytest.approx(p * TRIB[n] * 6.0, rel=1e-12)


def test_en1991_strips_cpe_interp_and_national_params():
    """Building T (h 36, b = d = 8): h > 2b -> strips: ze = b (z <= 8),
    h (z >= h - b = 28), z between (Fig. 7.4).  h/d = 4.5: cpe D 0.8, E
    -0.5 - 0.2 x 3.5/4 = -0.675, f_corr = 0.85 + 0.15 x 3.5/4 =
    0.98125.  cdir 0.9 -> vb = 0.9 vb0.  Explicit cscd multiplies."""
    m = _tall()
    s = al.compute(m, "en1991_wind", vb0=30.0, terrain="II", cdir=0.9,
                   cscd=0.95)
    assert s["vb"] == pytest.approx(27.0)
    assert s["cpe_E"] == pytest.approx(-0.675)
    assert s["f_corr"] == pytest.approx(0.98125)
    kr = 0.19

    def qp(z):
        z = max(z, 2.0)
        return (1 + 7 / math.log(z / 0.05)) * 0.5 * 1.25 * (
            kr * math.log(z / 0.05) * 27) ** 2 / 1000

    F = _F(s)
    ze = {"L1": 8.0, "L2": 12.0, "L3": 18.0, "L4": 24.0, "L5": 36.0,
          "L6": 36.0}
    for i, n in enumerate(ze):
        trib = 3.0 if n == "L6" else 6.0
        p = 0.95 * 0.98125 * (0.8 * qp(ze[n]) + 0.675 * qp(36.0))
        assert F[n] == pytest.approx(p * trib * 8.0, rel=1e-12)


def test_en1991_annex_b_structural_factor():
    """Annex B (procedure 1), building T, terrain II, vb 27, n1 0.8 Hz,
    delta 0.1: zs = 0.6 x 36 = 21.6 m; L(zs) = 300 (21.6/200)^(0.67 +
    0.05 ln 0.05) (B.1); B^2 (B.3); fL, SL (B.2); Rh, Rb (B.7/8); R^2
    (B.6); nu, kp (B.4/5); cs cd = (1 + 2 kp Iv sqrt(B^2 + R^2))/(1 + 7
    Iv) (6.1)."""
    m = _tall()
    s = al.compute(m, "en1991_wind", vb0=27.0, terrain="II", n1=0.8,
                   delta=0.1)
    zs, z0, h, b = 21.6, 0.05, 36.0, 8.0
    L = 300 * (zs / 200) ** (0.67 + 0.05 * math.log(z0))
    vm = 0.19 * math.log(zs / z0) * 27
    Iv = 1 / math.log(zs / z0)
    B2 = 1 / (1 + 0.9 * ((b + h) / L) ** 0.63)
    fL = 0.8 * L / vm
    SL = 6.8 * fL / (1 + 10.2 * fL) ** (5 / 3)

    def rl(e):
        return 1 / e - (1 - math.exp(-2 * e)) / (2 * e * e)
    R2 = math.pi ** 2 / 0.2 * SL * rl(4.6 * h * fL / L) * rl(4.6 * b * fL / L)
    nu = max(0.8 * math.sqrt(R2 / (B2 + R2)), 0.08)
    kp = max(math.sqrt(2 * math.log(600 * nu))
             + 0.6 / math.sqrt(2 * math.log(600 * nu)), 3.0)
    cscd = (1 + 2 * kp * Iv * math.sqrt(B2 + R2)) / (1 + 7 * Iv)
    assert s["cscd"] == pytest.approx(cscd, rel=1e-12)
    sf = s["structural_factor"]
    assert sf["cs"] * sf["cd"] == pytest.approx(cscd, rel=1e-12)
    assert 0.8 < cscd < 1.2


def test_is875_hand_example():
    """IS 875-3: Vb 44 m/s, terrain category 2, k1 = k3 = k4 = 1.  Table 2:
    k2 = 1.00 at <= 10 m (building A) -> Vz = 44, pz = 0.6 x 44^2 =
    1161.6 Pa; pd = Kd Ka Kc pz = 0.9 x 1 x 0.9 x 1.1616 = 0.940896 kPa (>
    0.7 pz).  F = (0.8 + 0.5) pd trib x 6."""
    s = al.compute(_bldg(), "is875_wind", Vb=44.0)
    pd = 0.81 * 0.6 * 44 ** 2 / 1000
    assert s["pd_h"] == pytest.approx(0.940896, rel=1e-12)
    for n, f in _F(s).items():
        assert f == pytest.approx(1.3 * pd * TRIB[n] * 6.0, rel=1e-12)
    # building T, terrain 3, k1 1.08, k4 1.15, Ka 0.8 Kc 0.8 -> pd floor
    # 0.7 pz governs (0.9 x 0.8 x 0.8 = 0.576 < 0.7); k2(18 m) = 0.97 +
    # 0.04 x 3/5 = 0.994, k2(36) = 1.06 + 0.06 x 6/20 = 1.078 (linear)
    m = _tall()
    s = al.compute(m, "is875_wind", Vb=39.0, terrain=3, k1=1.08, k4=1.15,
                   Ka=0.8, Kc=0.8)

    def pd(z, k2):
        return 0.7 * 0.6 * (39 * 1.08 * k2 * 1.15) ** 2 / 1000

    F = _F(s)
    assert F["L3"] == pytest.approx(
        (0.8 * pd(18, 0.994) + 0.5 * pd(36, 1.078)) * 6 * 8, rel=1e-12)
    assert F["L1"] == pytest.approx(
        (0.8 * pd(6, 0.91) + 0.5 * pd(36, 1.078)) * 6 * 8, rel=1e-12)


def test_as1170_2_hand_example():
    """AS/NZS 1170.2: VR 45, TC2, Md = Ms = Mt = 1.  Building A (h 10 <=
    25 m): Mz,cat(10) = 1.00, Vdes = 45 for every level; qh = 0.5 x 1.2 x
    45^2 = 1.215 kPa.  Cp,e windward 0.7, leeward d/b = 10/6 -> -0.5 +
    0.2 x 0.6667 = -0.366667 (Table 5.2(B)); p = (0.7 + 0.366667) qh.
    Building T (h 36 > 25 m): windward at z, Mz(18) = 1.05 + 0.03 x 3/5
    = 1.068 (Table 4.1, linear).  Vdes floor 30 m/s (VR 25)."""
    s = al.compute(_bldg(), "as1170_2_wind", VR=45.0)
    p = (0.7 + 0.5 - 0.2 * (10 / 6 - 1)) * 1.215
    for n, f in _F(s).items():
        assert f == pytest.approx(p * TRIB[n] * 6.0, rel=1e-12)
    m = _tall()
    s = al.compute(m, "as1170_2_wind", VR=45.0, Md=0.95)
    qh = 0.6 * (45 * 0.95 * (1.12 + 0.04 * 6 / 10)) ** 2 / 1000
    q18 = 0.6 * (45 * 0.95 * 1.068) ** 2 / 1000
    assert s["qh"] == pytest.approx(qh, rel=1e-12)
    assert _F(s)["L3"] == pytest.approx((0.7 * q18 + 0.5 * qh) * 48,
                                        rel=1e-12)
    s = al.compute(_bldg(), "as1170_2_wind", VR=25.0)
    assert s["Vdes_h"] == 30.0
    s = al.compute(_bldg(), "as1170_2_wind", VR=25.0, vdes_min=0.0)
    assert s["Vdes_h"] == 25.0


def test_gb50009_hand_example():
    """GB 50009: w0 0.55 kN/m2, terrain C, mu_s 1.3.  mu_z (Table 8.2.1) =
    0.544 (z/10)^0.44 with z >= 15 m: building A -> 0.65 at every level
    (0.544 x 1.5^0.44 = 0.6502).  beta_z = 1 (no T1).  wk = 1.3 x 0.6502 x
    0.55.  Building T with T1 1.2 s, zeta1 0.05: §8.4.3 beta_z at z = 36
    by hand."""
    s = al.compute(_bldg(), "gb50009_wind", w0=0.55, terrain="C")
    muz = 0.544 * 1.5 ** 0.44
    assert muz == pytest.approx(0.6502, rel=1e-4)
    for n, f in _F(s).items():
        assert f == pytest.approx(1.3 * muz * 0.55 * TRIB[n] * 6, rel=1e-12)
    m = _tall()
    s = al.compute(m, "gb50009_wind", w0=0.55, terrain="B", T1=1.2,
                   zeta1=0.05)
    Hh, B = 36.0, 8.0
    x1 = max(30 / 1.2 / math.sqrt(1.0 * 0.55), 5)
    R2 = math.pi / 0.3 * x1 ** 2 / (1 + x1 ** 2) ** (4 / 3)
    rz = 10 * math.sqrt(Hh + 60 * math.exp(-Hh / 60) - 60) / Hh
    rx = 10 * math.sqrt(B + 50 * math.exp(-B / 50) - 50) / B
    muz36 = (36 / 10) ** 0.30
    Bz = 0.670 * Hh ** 0.187 * rx * rz * 1.0 / muz36
    bz = 1 + 2 * 2.5 * 0.14 * Bz * math.sqrt(1 + R2)
    top = [r for r in s["stories"] if r["story"] == "L6"][0]
    assert top["beta_z"] == pytest.approx(bz, rel=1e-12)
    assert top["F"] == pytest.approx(bz * 1.3 * muz36 * 0.55 * 3 * 8,
                                     rel=1e-12)
    assert 1.0 < bz < 2.0


@pytest.mark.parametrize("code,kw", [
    ("asce7_16_wind", {"V": 50.0}), ("en1991_wind", {"vb0": 27.0}),
    ("is875_wind", {"Vb": 44.0}), ("as1170_2_wind", {"VR": 45.0}),
    ("gb50009_wind", {"w0": 0.55}),
])
def test_wind_generate_writes_wind_pattern(code, kw):
    m = _bldg()
    s = al.compute(m, code, direction="Y", **kw)
    pat = al.generate(m, code, None, direction="Y", **kw)
    assert pat.kind == "wind" and pat.name == al._DEFAULT_NAME[code]
    assert not pat.accidental_torsion
    assert sum(sf.fy for sf in pat.story_forces) == pytest.approx(s["V"])
    assert s["stories"][0]["w"] is None and s["W"] is None


# =================================================================== towers
def test_towers_per_tower_seismic_and_wind():
    """Each tower distributes its own base shear / uses its own plan
    width (C9 hook in autolateral.compute)."""
    from test_towers import _elf_model, _hand_w
    m = _elf_model()
    sm = al.compute(m, "as1170_4", Z=0.1, direction="X")
    assert set(sm["towers"]) == {"T1", "T2"}
    for tw in ("T1", "T2"):
        sub = sm["towers"][tw]
        assert sub["W"] == pytest.approx(sum(_hand_w(m, tw).values()))
        assert sub["V"] == pytest.approx(sub["Cs"] * sub["W"], rel=1e-12)
    assert sm["V"] == pytest.approx(sm["towers"]["T1"]["V"]
                                    + sm["towers"]["T2"]["V"])
    # wind: T1 facade 5 m, T2 facade 4 m wide (perpendicular to X)
    ws = al.compute(m, "is875_wind", Vb=44.0, direction="X")
    assert ws["towers"]["T1"]["width"] == 5.0
    assert ws["towers"]["T2"]["width"] == 4.0
    pat = al.generate(m, "asce7_16_wind", "W2", V=40.0, load_case=2)
    assert pat.ecc == 0.15
    assert len(pat.story_forces) == 5


# ====================================================================== API
@pytest.fixture()
def client():
    from skyframe.api import server as srv
    app = srv.create_app()
    app.config["TESTING"] = True
    srv._state["model"] = _bldg()
    with app.test_client() as c:
        yield c, srv
    srv._state["model"] = srv.quick_building()


def test_api_new_codes_preview_generate(client):
    c, srv = client
    r = c.post("/api/pattern/auto-lateral/preview",
               json={"code": "gb50011", "T": 0.6})
    assert r.status_code == 200 and r.get_json()["code"] == "gb50011"
    r = c.post("/api/pattern/auto-lateral",
               json={"code": "asce7_22_wind", "V": 50.0, "load_case": 4,
                     "transverse_sign": -1, "name": "W4"})
    assert r.status_code == 200
    pd = r.get_json()["patterns"]["W4"]
    assert pd["kind"] == "wind" and pd["ecc"] == 0.15
    assert pd["ecc_y"] == -0.15
    assert c.post("/api/pattern/auto-lateral",
                  json={"code": "en1991_wind", "vb0": 27,
                        "bogus": 1}).status_code == 400
