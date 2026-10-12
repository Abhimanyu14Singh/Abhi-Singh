"""Wave C: regional seismic / wind generators, code spectra, spectrum file
import and the USGS lookup (CONTRACT "Wave C: regional seismic / wind
generators, code spectra, spectrum file import, USGS lookup and regional
material / steel libraries").

Every expected value is worked here from the code formula (clause in the
comment) and the key numbers are pinned as hand-computed decimals.

Building A (test_loads_v116._bldg): elevations 4 / 7 / 10 m, W = 1000 /
1000 / 800 kN (sum 2800, sum w h = 19 000), plan 10 x 6 m (wind X loads the
6 m facade, depth 10 m), tributary facade heights 3.5 / 3.0 / 1.5 m.
Building T: 6 stories x 6 m (z = 6 .. 36 m), 100 t per story.
"""
import json
import math
import urllib.error

import pytest

from skyframe.core import autolateral as al
from skyframe.core import seismic_codes_wavec as sxc
from skyframe.core import spectra_codes as sc
from skyframe.core import spectra_codes_wavec as scw
from skyframe.core import usgs_lookup as usgs
from skyframe.core import wind_codes_wavec as wxc
from skyframe.core.model import BuildingModel, FrameSection, Material
from test_loads_v116 import _bldg

A = pytest.approx
H = {"S1": 4.0, "S2": 7.0, "S3": 10.0}
W = {"S1": 1000.0, "S2": 1000.0, "S3": 800.0}
TRIB = {"S1": 3.5, "S2": 3.0, "S3": 1.5}
G0 = 9.80665


def _F(s):
    return {r["story"]: r["F"] for r in s["stories"]}


def _wh(V, n):
    return V * W[n] * H[n] / 19000.0


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


def test_registration():
    for c in ("ec8_na", "nbcc2020", "ibc2006", "ibc2009", "ibc2012", "ubc97",
              "ntc2018", "bs6399_wind", "api4f_wind", "ntc2018_wind"):
        assert c in al.CODES
    assert {"bs6399_wind", "api4f_wind", "ntc2018_wind"} <= al.WIND_CODES
    assert "ec8_na" not in al.WIND_CODES


# ================================================================ EC8 + NA
def test_ec8_na_france_type2():
    """FR NA Type 2 ground C: S 1.5, TB 0.06, TC 0.40.  agR 0.1 g, gamma_I
    1.2 -> ag 0.12; q 3; T1 0.3 s (plateau): Sd = 0.12 x 1.5 x 2.5/3 =
    0.15 (EN 1998-1 Eq. 3.14); lambda 0.85 (T1 <= 2 TC, > 2 storeys, §4.3.3.2.2);
    Fb = 0.15 x 2800 x 0.85 = 357.0 kN; F_i = Fb z m / sum z m (Eq. 4.11)."""
    s = al.compute(_bldg(), "ec8_na", annex="FR", ag=0.1, q=3.0,
                   ground_type="C", gamma_I=1.2, T1=0.3)
    assert s["code"] == "ec8_na" and s["spectrum_type"] == 2
    assert (s["S"], s["TB"], s["TC"], s["TD"]) == (1.5, 0.06, 0.40, 2.0)
    assert s["Sd"] == A(0.15) and s["lambda_"] == 0.85
    assert s["V"] == A(357.0)
    for n, f in _F(s).items():
        assert f == A(_wh(357.0, n))


def test_ec8_na_portugal_soil_factor_and_germany():
    """PT Type 1 ground D: Smax 2.0, ag = 0.2 g = 1.96133 m/s^2 -> S = 2 -
    (2 - 1)(1.96133 - 1)/3 = 1.679557; T1 0.5 (plateau, TC 0.8), q 2.5 ->
    Sd = 0.2 x 1.679557 = 0.335911, Fb = 0.335911 x 2800 x 0.85 = 799.47.
    DE C-S: S 0.75, TC 0.5; T1 = 1.0 s: Sd = 0.1 x 0.75 x 2.5/1.5 x 0.5 =
    0.0625 (> beta ag = 0.02), lambda 0.85 (T1 = 2 TC) -> 148.75 kN."""
    s = al.compute(_bldg(), "ec8_na", annex="PT", ag=0.2, q=2.5,
                   ground_type="D", T1=0.5)
    assert s["S"] == A(2.0 - (0.2 * G0 - 1.0) / 3.0) == A(1.679557, rel=1e-6)
    assert s["V"] == A(799.469, rel=1e-6)
    s = al.compute(_bldg(), "ec8_na", annex="DE", ag=0.1, q=1.5,
                   ground_type="C-S", T1=1.0)
    assert s["Sd"] == A(0.0625) and s["V"] == A(148.75)
    with pytest.raises(ValueError, match="ground_type"):
        al.compute(_bldg(), "ec8_na", annex="DE", ag=0.1, q=1.5,
                   ground_type="B")
    with pytest.raises(ValueError, match="annex"):
        al.compute(_bldg(), "ec8_na", annex="XX", ag=0.1, q=1.5)
    # an explicit override wins over the preset
    s = al.compute(_bldg(), "ec8_na", annex="UK", ag=0.1, q=1.5,
                   ground_type="B", T1=0.3, S=1.0)
    assert s["S"] == 1.0
    cat = sxc.ec8_na_catalogue()
    assert [c["annex"] for c in cat] == ["UK", "IT", "DE", "FR", "ES", "PT",
                                         "GR", "RO"]
    assert all(c["from_memory"] for c in cat)
    json.dumps(cat)


# ================================================================ NBCC 2020
SA = dict(Sa02=0.848, Sa05=0.751, Sa10=0.425, Sa20=0.257, Sa50=0.080,
          Sa100=0.029)


def test_nbcc_table_interpolation():
    """Table 4.1.8.11, walls / wall-frame, S(0.2)/S(5.0) = 30 (midway 20 -
    40, Note (1)): Mv(1.0) = (1 + 1.25)/2 = 1.125, Mv(2.0) = (1.18 + 1.85)/2
    = 1.515, J(1.0) = 0.695, J(2.0) = 0.51.  Ta = 1.5 s with linear S(T)
    (Note (2)): S(Ta)Mv = (0.425 x 1.125 + 0.257 x 1.515)/2 = 0.43374;
    Mv = 1.32, J = 0.6025 (Note (3)).  Ta = 4.5 s > 4 s (Notes (7), (8)):
    the 4.0 s values between 2 and 5 s: Mv(5) = 3.30, S(5) = 0.08 ->
    0.389355 + (0.264 - 0.389355) 2/3 = 0.305785; J = 0.51 - 0.22 x 2/3."""
    Sa = {0.2: 0.848, 0.5: 0.751, 1.0: 0.425, 2.0: 0.257, 5.0: 0.080,
          10.0: 0.029}

    def S(t):
        return sxc.nbcc_design_s(t, Sa)
    r = sxc.nbcc_mv_j("walls", 30.0, 1.5, S)
    assert r["SMv"] == A(0.43374) and r["Mv"] == A(1.32)
    assert r["J"] == A(0.6025)
    r = sxc.nbcc_mv_j("walls", 30.0, 4.5, S)
    assert r["SMv"] == A(0.389355 + (0.264 - 0.389355) * 2.0 / 3.0)
    assert r["SMv"] == A(0.305785, rel=1e-6)
    assert r["J"] == A(0.51 - 0.22 * 2.0 / 3.0)
    # ratio < 5: linear from 1 at 0 (walls Mv(5 s) = 1.30 at ratio 5)
    assert sxc.nbcc_row("walls", 2.5)[3] == A(1.15)
    # ratio > 70 -> the 70 row; MRF has no 5 s column
    assert sxc.nbcc_row("walls", 120.0) == sxc.nbcc_row("walls", 70.0)
    assert sxc.nbcc_row("mrf", 40.0)[3] is None
    # MRF beyond 2 s uses the 2.0 s values (Note (5))
    r2 = sxc.nbcc_mv_j("braced", 70.0, 3.0, S)
    assert r2["Mv"] == A(1.19) and r2["J"] == A(0.61)
    assert r2["SMv"] == A(0.257 * 1.19)
    # log-log S(T): Mv interpolated linearly times S(Ta)
    r3 = sxc.nbcc_mv_j("walls", 30.0, 1.5, lambda t: sxc.nbcc_design_s(
        t, Sa, "loglog"), loglog=True)
    f = math.log(1.5) / math.log(2.0)
    s15 = math.exp(math.log(0.425) + f * math.log(0.257 / 0.425))
    assert r3["SMv"] == A(s15 * 1.32)


def test_nbcc_design_spectrum():
    """Table 4.1.8.4.-C: S(T <= 0.2) = max(S(0.2), S(0.5)); linear between
    the six ordinates; S(10) held beyond."""
    Sa = {0.2: 0.5, 0.5: 0.6, 1.0: 0.4, 2.0: 0.2, 5.0: 0.1, 10.0: 0.05}
    assert sxc.nbcc_design_s(0.1, Sa) == 0.6
    assert sxc.nbcc_design_s(0.35, Sa) == A(0.6)
    assert sxc.nbcc_design_s(1.5, Sa) == A(0.3)
    assert sxc.nbcc_design_s(20.0, Sa) == 0.05


def test_nbcc2020_cap_governs():
    """Concrete MF (4.1.8.11.(3)(a)): Ta = 0.075 x 10^0.75 = 0.421756 s;
    S(Ta) = 0.848 - 0.097 (0.221756/0.3) = 0.776299; Mv = 1 (Ta <= 0.5);
    V = 0.776299 x 2800/6.8 = 319.65 > cap max(2/3 x 0.848, 0.751) x
    2800/6.8 = 309.235 kN (4.1.8.11.(2)(c), Rd = 4 >= 1.5); floor S(2.0)
    W/RdRo = 105.82; Ft = 0 (Ta <= 0.7 s)."""
    s = al.compute(_bldg(), "nbcc2020", RdRo=6.8, Rd=4.0,
                   system="concrete_mf", **SA)
    Ta = 0.075 * 10.0 ** 0.75
    assert s["T"] == A(Ta) == A(0.421756, rel=1e-6)
    assert s["S_Ta"] == A(0.848 - 0.097 * (Ta - 0.2) / 0.3)
    assert s["Mv"] == 1.0 and s["J"] == 1.0
    assert s["V_max"] == A(309.235, rel=1e-6) and s["V"] == A(s["V_max"])
    assert s["V_min"] == A(0.257 * 2800 / 6.8)
    assert s["Ft"] == 0.0
    for n, f in _F(s).items():
        assert f == A(_wh(s["V"], n))
    # without the cap (Rd < 1.5) the formula value stands
    s2 = al.compute(_bldg(), "nbcc2020", RdRo=6.8, Rd=1.0,
                    system="concrete_mf", **SA)
    assert s2["V"] == A(0.776299 * 2800 / 6.8, rel=1e-6)


def test_nbcc2020_braced_with_top_force_and_overturning():
    """Braced frame (Clause (3)(b)): Ta = 0.025 x 36 = 0.9 s; ratio =
    0.848/0.08 = 10.6.  Table, braced: Mv = 1; J(1.0) = 0.98 - 0.07 x
    5.6/15 = 0.953867, J(0.9) = 1 - 0.046133 x 0.8 = 0.963093.  S(0.9) =
    0.751 - 0.326 x 0.8 = 0.4902; W = 6 x 980.665 = 5883.99 kN; RdRo = 3.9:
    V = 0.4902 x 5883.99/3.9 = 739.574 kN; Ft = 0.07 x 0.9 V = 46.593
    (Sentence (7)); M0 = J sum F h; level x below 0.6 hn: J_x = J + (1 - J)
    h_x/(0.6 hn) (Sentence (8))."""
    m = _tall()
    s = al.compute(m, "nbcc2020", RdRo=3.9, system="braced", **SA)
    W_ = 6 * 100.0 * G0
    assert s["T"] == A(0.9) and s["ratio"] == A(10.6)
    J = 1.0 - (1.0 - (0.98 - 0.07 * 5.6 / 15.0)) * 0.8
    assert s["J"] == A(J) == A(0.963093, rel=1e-6)
    assert s["V"] == A(0.4902 * W_ / 3.9) == A(739.574, rel=1e-5)
    Ft = 0.07 * 0.9 * s["V"]
    assert s["Ft"] == A(Ft) == A(46.593, rel=1e-4)
    F = _F(s)
    hs = {f"L{i + 1}": 6.0 * (i + 1) for i in range(6)}
    for n, f in F.items():
        exp = (s["V"] - Ft) * hs[n] / sum(hs.values())
        assert f == A(exp + (Ft if n == "L6" else 0.0))
    assert s["M0"] == A(J * sum(F[n] * hs[n] for n in F))
    rows = {r["story"]: r for r in s["stories"]}
    # L2 at 12 m < 0.6 x 36 = 21.6 m
    Jx = J + (1 - J) * 12.0 / 21.6
    assert rows["L2"]["J"] == A(Jx)
    assert rows["L2"]["M"] == A(Jx * sum(F[n] * (hs[n] - 12.0)
                                         for n in F if hs[n] > 12.0))
    assert rows["L5"]["J"] == 1.0
    # given period capped at 2 x Ta for braced frames (Clause (3)(d)(ii))
    s2 = al.compute(m, "nbcc2020", RdRo=3.9, system="braced", T=5.0, **SA)
    assert s2["T"] == A(1.8)


# ============================================================ legacy IBC
def test_ibc2012_and_site_coefficients():
    """ASCE 7-10 Table 11.4-1/2 site D: Fa(1.5) = 1.0, Fv(0.6) = 1.5 ->
    SDS = 1.0, SD1 = 0.6 (Eqs. 11.4-3/4); Ta = 0.0466 x 10^0.9 = 0.370157
    (Table 12.8-2, concrete MRF); Cs = SDS/(R/Ie) = 0.125 (Eq. 12.8-2) <
    SD1/(T R/Ie) = 0.20262; V = 0.125 x 2800 = 350 kN; k = 1."""
    s = al.compute(_bldg(), "ibc2012", Ss=1.5, S1=0.6, R=8.0)
    assert (s["Fa"], s["Fv"]) == (1.0, 1.5)
    assert s["SDS"] == A(1.0) and s["SD1"] == A(0.6)
    assert s["Ta"] == A(0.0466 * 10 ** 0.9) == A(0.370157, rel=1e-5)
    assert s["Cs"] == A(0.125) and s["V"] == A(350.0) and s["k"] == 1.0
    for n, f in _F(s).items():
        assert f == A(_wh(350.0, n))
    assert sxc.ibc_site_coefficients(0.625, 0.25, "E") == (
        A(1.7 + (1.2 - 1.7) * 0.5), A(3.2 + (2.8 - 3.2) * 0.5))


def test_ibc2006_vs_2009_minimum():
    """Tall frame, Ss 1.5, S1 0.3, D: Fa 1.0, Fv 1.8 -> SDS 1.0, SD1 0.36;
    Ta = 0.0466 x 36^0.9; Cs = SD1/(Ta R) = 0.038384 governs over SDS/R.
    IBC 2006 (ASCE 7-05 Eq. 12.8-5 as published): Cs >= 0.01 -> 0.038384;
    IBC 2009 / 2012 (Supplement 2 / 7-10): Cs >= 0.044 SDS Ie = 0.044."""
    m = _tall()
    Ta = 0.0466 * 36.0 ** 0.9
    c06 = al.compute(m, "ibc2006", Ss=1.5, S1=0.3, R=8.0)["Cs"]
    c09 = al.compute(m, "ibc2009", Ss=1.5, S1=0.3, R=8.0)["Cs"]
    assert c06 == A(0.36 / (Ta * 8.0)) == A(0.038384, rel=1e-5)
    assert c09 == A(0.044)
    # S1 >= 0.6: Cs >= 0.5 S1/(R/Ie) (Eq. 12.8-6)
    s = al.compute(m, "ibc2012", Ss=1.5, S1=0.75, R=8.0, TL=4.0)
    assert s["Cs_min"] == A(max(0.044 * s["SDS"], 0.5 * 0.75 / 8.0))


# ================================================================ UBC 97
def test_ubc97_zone4_cap_and_zone3_top_force():
    """Zone 4 (Z 0.4), SD: Ca = 0.44, Cv = 0.64 (Tables 16-Q/R); T =
    0.0731 x 10^0.75 = 0.411071 (Method A); V = Cv I W/(R T) = 512.86 >
    2.5 Ca I W/R = 362.353 kN (Eq. 30-5) -> 362.353; Ft = 0 (T <= 0.7).
    Zone 3 (0.3), SC: Ca 0.33, Cv 0.45; tall frame T = 0.0731 x 36^0.75 =
    1.07434; V = 0.45 W/(8.5 T) = 289.95; Ft = 0.07 T V = 21.80 kN."""
    s = al.compute(_bldg(), "ubc97", R=8.5)
    assert (s["Ca"], s["Cv"]) == (0.44, 0.64)
    assert s["T"] == A(0.0731 * 10 ** 0.75)
    assert s["V"] == A(2.5 * 0.44 * 2800 / 8.5) == A(362.353, rel=1e-6)
    assert s["Ft"] == 0.0
    m = _tall()
    W_ = 6 * 100.0 * G0
    s = al.compute(m, "ubc97", R=8.5, Z=0.3, soil="SC")
    T = 0.0731 * 36.0 ** 0.75
    assert s["V"] == A(0.45 * W_ / (8.5 * T)) == A(289.95, rel=1e-4)
    assert s["Ft"] == A(0.07 * T * s["V"]) == A(21.80, rel=1e-3)
    assert sum(_F(s).values()) == A(s["V"])
    with pytest.raises(ValueError, match="Z must be"):
        al.compute(_bldg(), "ubc97", R=8.5, Z=0.25)


# ============================================================ NTC 2018
def test_ntc2018_static():
    """NTC 2018 §3.2.3.2: soil B, ag 0.25, F0 2.4, Tc* 0.3: SS = 1.40 - 0.40
    x 2.4 x 0.25 = 1.16; CC = 1.10 x 0.3^-0.2 = 1.399486; TC = 0.419846,
    TB = TC/3, TD = 4 x 0.25 + 1.6 = 2.6.  T1 = 0.075 x 10^0.75 = 0.421756
    (Eq. 7.3.6) > TC: Sd = ag S F0/q TC/T1 = 0.177653 (q 3.9); lambda 0.85;
    Fh = 0.177653 x 2800 x 0.85 = 422.815 kN (Eq. 7.3.7)."""
    s = al.compute(_bldg(), "ntc2018", ag=0.25, F0=2.4, Tc_star=0.3, q=3.9,
                   soil="B", structure="concrete_mrf")
    CC = 1.10 * 0.3 ** -0.2
    TC = CC * 0.3
    T1 = 0.075 * 10 ** 0.75
    Sd = 0.25 * 1.16 * 2.4 / 3.9 * TC / T1
    assert s["SS"] == A(1.16) and s["CC"] == A(1.399486, rel=1e-6)
    assert s["TC"] == A(TC) and s["TB"] == A(TC / 3) and s["TD"] == A(2.6)
    assert s["Sd"] == A(Sd) == A(0.177653, rel=1e-5)
    assert s["lambda_"] == 0.85
    assert s["V"] == A(Sd * 2800 * 0.85) == A(422.815, rel=1e-5)
    # soil D lower bound 0.90 and the 0.2 ag design floor
    p = sxc.ntc18_parameters(0.5, 3.0, 0.4, "D")
    assert p["SS"] == 0.9
    s = al.compute(_bldg(), "ntc2018", ag=0.25, F0=2.4, Tc_star=0.3, q=10.0,
                   T1=2.0)
    assert s["Sd"] == A(0.05)


# ================================================================ wind
def test_bs6399_wind():
    """Vs = 21 m/s, Sb 1.8 -> Ve = 37.8, qs = 0.613 x 37.8^2 = 875.879 Pa
    (Eq. 1); D/H = 10/10 = 1: front +0.8, rear -0.3 (Table 5); H/D = 1 ->
    divergence 0.85; p = 0.85 x 0.875879 x 1.1 = 0.818947 kPa; F = p x trib
    x 6 m."""
    s = al.compute(_bldg(), "bs6399_wind", Vb=21.0, Sb=1.8)
    p = 0.85 * 0.613 * 37.8 ** 2 / 1000.0 * 1.1
    assert p == A(0.818947, rel=1e-6)
    for n, f in _F(s).items():
        assert f == A(p * TRIB[n] * 6.0)
    assert (s["cpe_front"], s["cpe_rear"], s["divergence"]) == (0.8, -0.3,
                                                                 0.85)
    # Sb table (He, Sb): 1.5 at 4 m, 1.8 at 10 m -> 1.65 at 7 m
    s = al.compute(_bldg(), "bs6399_wind", Vb=21.0,
                   Sb=[[4.0, 1.5], [10.0, 1.8]], divergence=1.0)
    q7 = 0.613 * (21.0 * 1.65) ** 2 / 1000.0
    assert _F(s)["S2"] == A(q7 * 1.1 * 3.0 * 6.0)
    assert wxc.bs6399_cpe(4.0) == (A(0.6), A(-0.1))


def test_api4f_wind():
    """F = 0.00338 Ki Vz^2 Cs A (lbf, knots, ft^2) -> K = 0.00338 x
    4.44822/0.3048^2/(1852/3600)^2 = 0.61155 N s^2/m^4; Vz = 30 (z/10)^(1/8)
    with z >= 10 m."""
    K = 0.00338 * 4.4482216152605 / 0.3048 ** 2 / (1852.0 / 3600.0) ** 2
    assert K == A(0.61155, rel=1e-4) and wxc.API4F_K == A(K)
    s = al.compute(_tall(), "api4f_wind", Vref=30.0, Cs=1.5)
    F = _F(s)
    for i in range(6):
        z = 6.0 * (i + 1)
        vz = 30.0 * (max(z, 10.0) / 10.0) ** 0.125
        trib = 6.0 if i < 5 else 3.0
        assert F[f"L{i + 1}"] == A(1.5 * K * vz ** 2 / 1000 * trib * 8.0)


def test_ntc2018_wind():
    """Zone 3: vb0 27 m/s (as <= a0); cr(50) = 0.75 sqrt(1 - 0.2 ln(-ln
    0.98)) = 1.000734; qr = 0.5 x 1.25 vr^2; category III (kr 0.20, z0
    0.10, zmin 5): ce(z) = kr^2 ln(z/z0)(7 + ln(z/z0)), ce(4) = ce(5) =
    1.707523; p = qr cd (0.8 ce(z) + 0.4 ce(h))."""
    s = al.compute(_bldg(), "ntc2018_wind", zone=3, category="III")
    cr = 0.75 * math.sqrt(1 - 0.2 * math.log(-math.log(1 - 1 / 50.0)))
    assert s["cr"] == A(cr) == A(1.000734, rel=1e-6)
    qr = 0.5 * 1.25 * (27.0 * cr) ** 2 / 1000.0

    def ce(z):
        L = math.log(max(z, 5.0) / 0.1)
        return 0.04 * L * (7 + L)
    assert ce(4.0) == A(1.707523, rel=1e-6)
    for n, f in _F(s).items():
        p = qr * (0.8 * ce(H[n]) + 0.4 * ce(10.0))
        assert f == A(p * TRIB[n] * 6.0)
    # altitude factor (Eq. 3.3.1b): zone 1, as = 1200 m > a0 = 1000 m
    assert wxc.ntc18_vb(1, 1200.0) == A(25.0 * (1 + 0.40 * 0.2))


# =============================================================== spectra
def sa(code, T, **p):
    return sc.spectrum_value(code, p, T)


def test_tsc2018_spectrum():
    """SS 1.0, S1 0.3, ZC: FS 1.2, F1 1.5 (Tables 2.1/2.2) -> SDS 1.2, SD1
    0.45, TA 0.075, TB 0.375 (§2.3.4).  Sae(0) = 0.4 SDS = 0.48; plateau
    1.2; Sae(1) = 0.45; Sae(8) = 0.45 x 6/64 (T > TL).  Reduced (R 8, D 3,
    I 1): Ra(0.2) = 3 + 5 x 0.2/0.375 -> SaR = 1.2/5.666667 = 0.211765."""
    p = dict(SS=1.0, S1=0.3, site="ZC")
    assert sa("tsc2018", 0.0, **p) == A(0.48)
    assert sa("tsc2018", 0.075, **p) == A(1.2)
    assert sa("tsc2018", 0.375, **p) == A(1.2)
    assert sa("tsc2018", 1.0, **p) == A(0.45)
    assert sa("tsc2018", 8.0, **p) == A(0.45 * 6 / 64)
    assert sa("tsc2018", 0.2, kind="design", **p) == A(0.211765, rel=1e-5)
    assert sa("tsc2018", 1.0, kind="design", **p) == A(0.45 / 8)


def test_kds_nscp_spectra():
    """KDS: SDS = 0.22 x 2.5 x 2/3 = 0.366667, SD1 = 0.146667, T0 = 0.08,
    Ts = 0.4, TL 5: Sa(0) = 0.4 SDS, plateau, SD1/T, SD1 TL/T^2.  NSCP zone 4
    SD: Ca 0.44, Cv 0.64; Ts = 0.64/(2.5 x 0.44) = 0.581818, T0 = 0.2 Ts:
    Sa(0) = Ca, plateau 2.5 Ca = 1.1, Sa(1) = Cv = 0.64."""
    assert sa("kds41", 0.0) == A(0.4 * 0.366667, rel=1e-5)
    assert sa("kds41", 0.08) == A(0.366667, rel=1e-5)
    assert sa("kds41", 0.4) == A(0.366667, rel=1e-5)
    assert sa("kds41", 1.0) == A(0.146667, rel=1e-5)
    assert sa("kds41", 6.0) == A(0.146667 * 5 / 36, rel=1e-5)
    assert sa("kds41", 1.0, R=5.0, IE=1.5) == A(0.146667 * 0.3, rel=1e-5)
    assert sa("nscp2015", 0.0) == A(0.44)
    assert sa("nscp2015", 0.116364) == A(1.1, rel=1e-5)
    assert sa("nscp2015", 0.5818) == A(1.1)
    assert sa("nscp2015", 1.0) == A(0.64)
    assert sa("nscp2015", 0.05) == A(0.44 * (1 + 1.5 * 0.05 / 0.1163636),
                                     rel=1e-6)


def test_ntc_cdmx_and_ntc2018_spectra():
    """NTC-Sismo (a0 .2, c .6, Ta .4, Tb 1, k 1): a(0.2) = 0.4, plateau 0.6,
    a(2) = 0.6 (1/2)^2 = 0.15; design (Q 2, R0 2): T 0.6 -> 0.6/(2 x 2) =
    0.15, T 0.2 -> Q' 1.5, R = 3 - sqrt(0.5) -> 0.116301.  NTC 2018 B
    (ag .25, F0 2.4, Tc* .3): plateau 0.25 x 1.16 x 2.4 = 0.696, Se(0) =
    0.29, Se(1) = 0.696 TC, Se(3) = 0.696 TC 2.6/9."""
    assert sa("ntc_cdmx", 0.2) == A(0.4)
    assert sa("ntc_cdmx", 0.7) == A(0.6)
    assert sa("ntc_cdmx", 2.0) == A(0.15)
    assert sa("ntc_cdmx", 0.6, kind="design") == A(0.15)
    assert sa("ntc_cdmx", 0.2, kind="design") == A(
        0.4 / (1.5 * (3 - math.sqrt(0.5)))) == A(0.116301, rel=1e-5)
    TC = 1.10 * 0.3 ** -0.2 * 0.3
    assert sa("ntc2018", 0.0) == A(0.29)
    assert sa("ntc2018", 0.3) == A(0.696)
    assert sa("ntc2018", 1.0) == A(0.696 * TC)
    assert sa("ntc2018", 3.0) == A(0.696 * TC * 2.6 / 9)
    assert sa("ntc2018", 3.0, kind="design", q=4.0) == A(0.05)  # 0.2 ag


def test_ec8_na_spectrum():
    """FR Type 2 ground D: S 1.6, TB 0.1, TC 0.6, TD 1.5; agR 0.1 elastic
    5 %: plateau 0.4, Se(0) = 0.16, Se(1) = 0.24, Se(2) = 0.09."""
    p = dict(annex="FR", ground_type="D", agR=0.1)
    assert sa("ec8_na", 0.0, **p) == A(0.16)
    assert sa("ec8_na", 0.3, **p) == A(0.4)
    assert sa("ec8_na", 1.0, **p) == A(0.24)
    assert sa("ec8_na", 2.0, **p) == A(0.09)
    sp = sc.spectrum("ec8_na", dict(p, spectrum_type=1))
    assert sp["summary"]["TC"] == 0.8 and sp["summary"]["S"] == 1.35
    with pytest.raises(ValueError):
        sc.spectrum("ec8_na", dict(annex="DE", ground_type="D"))


def test_new_spectra_create_functions_and_validate():
    m = _bldg()
    for code in scw.WAVEC_CODES:
        fn = sc.make_spectrum_function(m, "F_" + code, code, {})
        assert fn.source["code"] == code and len(fn.points) > 50
    d = m.to_dict()
    m2 = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert m2.to_dict() == d


# =============================================================== file import
def test_spectrum_file_parse_csv_header_and_damping():
    txt = ("# ETABS-style spectrum file\nDamping = 5%\nPeriod,Accel\n"
           "0.0, 0.40\n0.2, 1.0\n1.0 , 0.6  # inline comment\n\n4.0;0.15\n")
    r = scw.parse_spectrum_text(txt)
    assert r["points"] == [[0.0, 0.4], [0.2, 1.0], [1.0, 0.6], [4.0, 0.15]]
    assert r["damping"] == A(0.05) and r["header_lines"] == 2


def test_spectrum_file_units_and_frequency():
    """cm/s^2 -> g: 981 cm/s^2 = 9.81/9.80665 g; Hz -> T = 1/f (sorted)."""
    txt = "5 490.3325\n1 981\n0.5 196.133\n"
    r = scw.parse_spectrum_text(txt, period_units="Hz", value_units="cm/s2")
    assert [p[0] for p in r["points"]] == [0.2, 1.0, 2.0]
    assert r["points"][0][1] == A(0.5) and r["points"][1][1] == A(
        9.81 / G0) and r["points"][2][1] == A(0.2)
    r = scw.parse_spectrum_text("0 1\n1 2\n", value_units="m/s2", scale=2.0)
    assert r["points"][1][1] == A(4.0 / G0)
    r = scw.parse_spectrum_text("a\nb\n0.1 0.2\n0.5 0.3\n", skip_lines=2)
    assert r["n"] == 2 and r["header_lines"] == 2
    r = scw.parse_spectrum_text("damping 0.03\n0 1\n1 2\n")
    assert r["damping"] == A(0.03)
    r = scw.parse_spectrum_text("1.0E-1\t2.5D-1\n")
    assert r["points"] == [[0.1, 0.25]]


@pytest.mark.parametrize("txt,kw,msg", [
    ("", {}, "empty"), ("T Sa\n", {}, "no period"),
    ("0 1\n0 2\n", {}, "duplicate"), ("0 -1\n", {}, ">= 0"),
    ("0 1\nfoo bar\n", {}, "line 2"), ("0 1\n", {"period_units": "Hz"},
                                        "frequency"),
    ("0 1\n", {"value_units": "furlong"}, "value_units"),
    ("0 1\n", {"scale": 0}, "scale")])
def test_spectrum_file_errors(txt, kw, msg):
    with pytest.raises(ValueError, match=msg):
        scw.parse_spectrum_text(txt, **kw)


def test_import_spectrum_function_damping_precedence():
    m = _bldg()
    fn, r = scw.import_spectrum_function(m, "FILE", "damping 2%\n0 1\n1 2\n")
    assert fn.damping == A(0.02) and fn.source is None
    fn, r = scw.import_spectrum_function(m, "FILE", "0 1\n1 2\n",
                                         damping=0.07)
    assert fn.damping == 0.07 and m.spectrum_functions["FILE"] is fn
    fn, r = scw.import_spectrum_function(m, "F2", "0 1\n1 2\n")
    assert fn.damping == 0.05


# ================================================================== USGS
MOCK = {"request": {"parameters": {}},
        "response": {"data": {
            "ss": 1.5, "s1": 0.6, "sms": 1.5, "sm1": 0.9, "sds": 1.0,
            "sd1": 0.6, "tl": 8, "pgam": 0.6, "sdc": "D",
            "multiPeriodDesignSpectrum": {"periods": [0, 0.2, 1.0, 2.0],
                                          "ordinates": [0.4, 1.0, 0.6, 0.3]}},
            "metadata": {}}}


def test_usgs_lookup_parses_mocked_response():
    seen = {}

    def opener(url, timeout):
        seen["url"], seen["timeout"] = url, timeout
        return json.dumps(MOCK).encode()
    r = usgs.lookup(34.05, -118.25, "asce7-22", "C", "III", opener=opener)
    assert seen["url"].startswith(
        "https://earthquake.usgs.gov/ws/designmaps/asce7-22.json?")
    assert "latitude=34.05" in seen["url"] and "siteClass=C" in seen["url"]
    assert "riskCategory=III" in seen["url"]
    v = r["values"]
    assert (v["SS"], v["S1"], v["SDS"], v["SD1"], v["TL"]) == (1.5, 0.6, 1.0,
                                                               0.6, 8.0)
    assert v["SDC"] == "D"
    assert r["mprs_pairs"] == [[0.0, 0.4], [0.2, 1.0], [1.0, 0.6],
                               [2.0, 0.3]]
    # the pairs feed the ASCE 7-22 MPRS spectrum directly
    sp = sc.spectrum("asce7_22_mprs", {"ordinates": r["mprs_pairs"],
                                       "basis": "design"})
    assert sp["points"][0] == [0.0, A(0.4)]


def test_usgs_unreachable_fails_gracefully():
    def down(url, timeout):
        raise urllib.error.URLError("Tunnel connection failed: 403")
    with pytest.raises(usgs.UsgsUnavailable, match="unreachable.*by hand"):
        usgs.lookup(34.0, -118.0, opener=down)

    def bad(url, timeout):
        return b"<html>maintenance</html>"
    with pytest.raises(usgs.UsgsUnavailable, match="not JSON"):
        usgs.lookup(34.0, -118.0, opener=bad)
    with pytest.raises(usgs.UsgsUnavailable, match="no design data"):
        usgs.parse_response({"response": {"message": "x"}})
    with pytest.raises(ValueError, match="latitude"):
        usgs.build_url(95.0, 0.0)
    with pytest.raises(ValueError, match="site_class"):
        usgs.build_url(10.0, 0.0, "asce7-16", "BC")


# =================================================================== API
@pytest.fixture
def client():
    from skyframe.api import server
    app = server.create_app()
    app.config["TESTING"] = True
    old = server._state["model"]
    server._state["model"] = _bldg()
    yield app.test_client()
    server._state["model"] = old


def test_api_endpoints(client, monkeypatch):
    c = client
    r = c.get("/api/codes/ec8-annexes").get_json()
    assert len(r["annexes"]) == 8
    r = c.post("/api/function/spectrum-file/preview",
               json={"text": "0 0.4\n0.5 1\n", "value_units": "g"})
    assert r.status_code == 200 and r.get_json()["n"] == 2
    r = c.post("/api/function/spectrum-file/preview", json={"text": ""})
    assert r.status_code == 400
    r = c.post("/api/function/spectrum-file",
               json={"name": "FROMFILE", "text": "0 0.4\n0.5 1\n2 0.3\n",
                     "damping": 0.05})
    assert r.status_code == 200
    assert r.get_json()["spectrum_functions"]["FROMFILE"]["points"][1] == \
        [0.5, 1.0]
    # USGS: unreachable -> ok false with the manual-entry hint; mocked -> ok
    monkeypatch.setattr(usgs.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            urllib.error.URLError("blocked")))
    r = c.post("/api/usgs/designmaps", json={"latitude": 34, "longitude":
                                             -118})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is False and j["manual"] is True and "hand" in j["error"]
    assert c.post("/api/usgs/designmaps", json={"latitude": 100,
                                                "longitude": 0}
                  ).status_code == 400

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(MOCK).encode()
    monkeypatch.setattr(usgs.urllib.request, "urlopen",
                        lambda *a, **k: _Resp())
    j = c.post("/api/usgs/designmaps", json={"latitude": 34, "longitude":
                                             -118}).get_json()
    assert j["ok"] is True and j["values"]["SDS"] == 1.0
    # new generators through the existing auto-lateral endpoint
    r = c.post("/api/pattern/auto-lateral",
               json={"code": "ubc97", "R": 8.5})
    assert r.status_code == 200
    assert "UBC97" in r.get_json()["patterns"]
    r = c.post("/api/pattern/auto-lateral",
               json={"code": "ibc2012", "Ss": 1.5, "S1": 0.6, "R": 8.0,
                     "name": "IBC"})
    assert r.status_code == 200
    r = c.post("/api/pattern/auto-lateral",
               json={"code": "ibc2012", "Ss": 1.5, "S1": 0.6, "R": 8.0,
                     "bogus": 1})
    assert r.status_code == 400
