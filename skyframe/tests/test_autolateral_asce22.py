"""ASCE 7-22 auto-lateral provisions, verified (CONTRACT "Temperature
gradients, projected loads and auto-lateral generators", research note).

Seismic: §11.4.5.1 multi-period design spectrum (linear interpolation,
long-period extension), §21.4 SDS / SD1 from the multi-period spectrum,
§12.8.1.1 Method 1 (Eq. 12.8-2, maximum-Sa rule below the peak period) and
Method 2 (two-period), §12.8.1.3 short-period cap.
Wind: Table 26.10-1 Kz (2.41, 7-22 Table 26.11-1 alpha / zg), Table 26.9-1
Ke, Kd in the pressure (Eq. 27.3-1), split windward qz / leeward qh.

Building: the 3-story hand example of test_loads_v116 (elevations 4 / 7 /
10 m, W = 1000 / 1000 / 800 kN, sum w h = 19 000, plan 10 x 6 m).
"""
import math

import pytest

from skyframe.core import autolateral as al
from test_loads_v116 import _bldg

FT = 0.3048
TA = 0.0466 * 10 ** 0.9          # 0.370157 s

# USGS-style 22-period multi-period DESIGN spectrum (Sa in g)
MPRS = [[0.0, 0.40], [0.01, 0.40], [0.02, 0.41], [0.03, 0.43],
        [0.05, 0.52], [0.075, 0.65], [0.1, 0.78], [0.15, 0.92],
        [0.2, 1.00], [0.25, 1.04], [0.3, 1.05], [0.4, 1.02], [0.5, 0.96],
        [0.75, 0.80], [1.0, 0.66], [1.5, 0.48], [2.0, 0.37], [3.0, 0.24],
        [4.0, 0.17], [5.0, 0.13], [7.5, 0.07], [10.0, 0.04]]


# ------------------------------------------------------------- seismic
def test_mprs_sds_sd1_section_21_4():
    """SDS = 0.9 max Sa(0.2..5 s) = 0.9 * 1.05 (at 0.3 s) = 0.945.
    SD1, vs30 = 760 m/s (> 1450 ft/s): window 1-2 s.  Tabulated T Sa:
    0.66, 0.72, 0.74; but on the 1.5-2 s segment Sa = 0.81 - 0.22 T, so T Sa
    peaks at T = 0.81/0.44 = 1.840909 s with 0.81^2/0.88 = 0.745568 ->
    SD1 = 0.9 * 0.745568 = 0.671011 (> Sa(1) = 0.66).
    vs30 = 300 m/s: window 1-5 s; the 2-3 s segment (Sa = 0.63 - 0.13 T)
    peaks at 2.423077 s with 0.63^2/0.52 = 0.763269 -> SD1 = 0.686942.
    Floor: a spectrum whose 0.9 max T Sa < Sa(1) returns Sa(1)."""
    assert al.mprs_sds(MPRS) == pytest.approx(0.945, rel=1e-12)
    assert al.mprs_sd1(MPRS, 760.0) == pytest.approx(0.9 * 0.81 ** 2 / 0.88,
                                                     rel=1e-12)
    assert al.mprs_sd1(MPRS, 760.0) == pytest.approx(0.671011, rel=1e-6)
    assert al.mprs_sd1(MPRS, 300.0) == pytest.approx(0.9 * 0.63 ** 2 / 0.52,
                                                     rel=1e-12)
    assert al.mprs_sd1(MPRS, 300.0) == pytest.approx(0.686942, rel=1e-6)
    # threshold 1450 ft/s = 441.96 m/s: exactly at it -> the 1-5 s window
    assert al.mprs_sd1(MPRS, 1450 * FT) == pytest.approx(0.686942, rel=1e-6)
    flat = [[0.0, 0.5], [1.0, 0.5], [2.0, 0.1]]   # T Sa max 0.5 at 1 s
    assert al.mprs_sd1(flat, 760.0) == pytest.approx(0.5, rel=1e-12)


def test_mprs_long_period_extension_11_4_5_1():
    """Beyond 10 s: TL = 8 < 10 -> Sa(10) (10/T)^2: Sa(12) = 0.04*100/144
    = 0.0277778.  TL = 12 >= 10: Sa(11) = 0.04*10/11 = 0.0363636 (T <= TL),
    Sa(16) = 0.04*10*12/256 = 0.01875 (T > TL).  Between points: linear,
    Sa(0.35) = 1.035."""
    assert al._mprs_sa(MPRS, 12.0, 8.0) == pytest.approx(0.04 * 100 / 144,
                                                         rel=1e-12)
    assert al._mprs_sa(MPRS, 11.0, 12.0) == pytest.approx(0.4 / 11,
                                                          rel=1e-12)
    assert al._mprs_sa(MPRS, 16.0, 12.0) == pytest.approx(0.01875,
                                                          rel=1e-12)
    assert al._mprs_sa(MPRS, 0.35, 8.0) == pytest.approx(1.035, rel=1e-12)


def test_method1_sa_at_T_beyond_peak():
    """Ta = 0.370157 s >= T_peak = 0.3 s -> Sa = Sa(T) = 1.05 - 0.3
    (Ta - 0.3) = 1.028953 (NOT capped at SDS = 0.945).  R = 8:
    Cs = 0.128619, V = 2800 Cs = 360.1335 kN; k = 1 ->
    F3 = V*8000/19000.  SDS / SD1 derived (vs30 = 760); SD1 >= 0.4 ->
    Cu = 1.4."""
    s = al.compute(_bldg(), "asce7_22", R=8.0, mprs=MPRS, vs30=760.0)
    Sa = 1.05 - 0.3 * (TA - 0.3)
    assert s["method"] == 1 and s["spectrum"] == "multi_period"
    assert s["T_peak"] == 0.3 and s["Sa_max"] == 1.05
    assert s["Sa"] == pytest.approx(Sa, rel=1e-12)
    assert s["Sa"] == pytest.approx(1.028953, rel=1e-6)
    assert s["SDS"] == pytest.approx(0.945, rel=1e-12)
    assert s["SD1"] == pytest.approx(0.671011, rel=1e-6)
    assert s["Cu"] == 1.4
    assert s["V"] == pytest.approx(2800 * Sa / 8, rel=1e-12)
    assert s["V"] == pytest.approx(360.1335, rel=1e-6)
    assert s["stories"][2]["F"] == pytest.approx(s["V"] * 8000 / 19000,
                                                 rel=1e-12)


def test_method1_max_sa_below_peak_period():
    """Ct = 0.03: Ta = 0.03*10^0.9 = 0.238298 s < T_peak 0.3 s -> the
    maximum Sa = 1.05 is used (Sa(T) itself would be 1.030639) ->
    Cs = 0.13125, V = 367.5 kN."""
    s = al.compute(_bldg(), "asce7_22", R=8.0, mprs=MPRS, SD1=0.6,
                   Ct=0.03)
    assert s["T"] == pytest.approx(0.238298, rel=1e-5)
    assert s["Sa_T"] == pytest.approx(1.0 + 0.8 * (s["T"] - 0.2),
                                      rel=1e-12)
    assert s["Sa_T"] == pytest.approx(1.030639, rel=1e-6)
    assert s["Sa"] == 1.05
    assert s["V"] == pytest.approx(367.5, rel=1e-12)


def test_method1_lower_bounds_and_given_sds():
    """Given SDS / SD1 win over the derived ones (USGS values, as ETABS
    takes them).  Long period T (Ct 0.3: Ta = 2.382985, SD1 = 0.6 ->
    Cu 1.4) on a weak spectrum: Sa(T) = 0.37 - 0.13*0.382985 = 0.320212,
    R = 8, Ie = 1.5 -> Cs = 0.0600398; floor 0.044 SDS Ie with SDS = 1.0
    = 0.066 governs; S1 = 0.8 -> 0.5*0.8/(8/1.5) = 0.075 governs."""
    m = _bldg()
    s = al.compute(m, "asce7_22", R=8.0, Ie=1.5, mprs=MPRS, SDS=1.0,
                   SD1=0.6, Ct=0.3)
    assert "SDS_from_mprs" not in s and s["SDS"] == 1.0
    assert s["Sa"] == pytest.approx(0.37 - 0.13 * (s["T"] - 2.0),
                                    rel=1e-12)
    assert s["Cs"] == pytest.approx(0.066, rel=1e-12)
    s2 = al.compute(m, "asce7_22", R=8.0, Ie=1.5, mprs=MPRS, SDS=1.0,
                    SD1=0.6, Ct=0.3, S1=0.8)
    assert s2["Cs"] == pytest.approx(0.075, rel=1e-12)
    assert s2["V"] == pytest.approx(210.0, rel=1e-12)


def test_method2_from_mprs_and_errors():
    """method = 2 with the spectrum: two-period form with the derived
    SDS = 0.945, SD1 = 0.671011 (Ts = 0.710064 > Ta) -> Sa = SDS,
    V = 0.945/8*2800 = 330.75 kN."""
    m = _bldg()
    s = al.compute(m, "asce7_22", R=8.0, mprs=MPRS, vs30=760.0, method=2)
    assert s["method"] == 2 and s["spectrum"] == "two_period"
    assert s["V"] == pytest.approx(330.75, rel=1e-12)
    with pytest.raises(ValueError, match="SD1 or vs30"):
        al.compute(m, "asce7_22", R=8.0, mprs=MPRS)
    with pytest.raises(ValueError, match="method 1 needs"):
        al.compute(m, "asce7_22", SDS=1.0, SD1=0.6, R=8.0, method=1)
    with pytest.raises(ValueError, match="method must be"):
        al.compute(m, "asce7_22", SDS=1.0, SD1=0.6, R=8.0, method=3)


def test_short_period_cap_12_8_1_3():
    """Method 2, SDS 1.5, SD1 0.9 (Ts 0.6 > Ta): Cs from max(min(1.5, 1.0),
    0.7*1.5) = 1.05 -> V = 1.05/8*2800 = 367.5 kN (uncapped 525 kN).
    SDS 1.2: max(1.0, 0.84) = 1.0 -> V = 350 kN; Cs_min uses the real SDS.
    Method 1 / T > 0.5 s are rejected."""
    m = _bldg()
    s = al.compute(m, "asce7_22", SDS=1.5, SD1=0.9, R=8.0,
                   short_period_cap=True)
    assert s["SDS_cs"] == pytest.approx(1.05, rel=1e-12)
    assert s["V"] == pytest.approx(367.5, rel=1e-12)
    assert al.compute(m, "asce7_22", SDS=1.5, SD1=0.9, R=8.0)["V"] == \
        pytest.approx(525.0, rel=1e-12)
    s2 = al.compute(m, "asce7_22", SDS=1.2, SD1=0.9, R=8.0,
                    short_period_cap=True)
    assert s2["V"] == pytest.approx(350.0, rel=1e-12)
    with pytest.raises(ValueError, match="Method 2"):
        al.compute(m, "asce7_22", R=8.0, mprs=MPRS, SD1=0.6,
                   short_period_cap=True)
    with pytest.raises(ValueError, match="T <= 0.5"):
        al.compute(m, "asce7_22", SDS=1.5, SD1=0.9, R=8.0, Ct=0.1,
                   short_period_cap=True)


def test_api_accepts_new_asce22_parameters():
    body = {"code": "asce7_22", "R": 8.0, "mprs": MPRS, "vs30": 760.0}
    code, name, ecc, params = al.params_from_body(body)
    assert al.compute(_bldg(), code, **params)["V"] == pytest.approx(
        360.1335, rel=1e-6)


# ---------------------------------------------------------------- wind
@pytest.mark.parametrize("exp,z_ft,table", [
    ("B", 15, 0.57), ("C", 15, 0.85), ("C", 30, 0.98), ("C", 60, 1.13),
    ("B", 30, 0.69), ("B", 60, 0.83), ("D", 30, 1.17), ("D", 60, 1.32)])
def test_kz_table_26_10_1(exp, z_ft, table):
    """Kz = 2.41 (z/zg)^(2/alpha) at the Table 26.10-1 heights, rounded
    like the table (0-15 ft row 0.57 / 0.85 and C 30 ft 0.98 match the
    published 7-22 table; the others are the equation values)."""
    assert round(al.asce22_kz(z_ft * FT, exp), 2) == table


def test_kz_floor_cap_and_exact_values():
    """z < 15 ft uses 15 ft; above zg the coefficient stays 2.41."""
    for e in "BCD":
        assert al.asce22_kz(1.0, e) == al.asce22_kz(15 * FT, e)
        alpha, zg = al.ASCE22_WIND_EXPOSURES[e]
        assert al.asce22_kz(zg * 1.5, e) == pytest.approx(2.41, rel=1e-12)
    assert al.asce22_kz(15 * FT, "D") == pytest.approx(
        2.41 * (15 / 1935) ** (2 / 11.5), rel=1e-12)
    assert al.asce22_kz(15 * FT, "D") == pytest.approx(1.035042, rel=1e-6)


@pytest.mark.parametrize("z_ft,table", [
    (0, 1.00), (1000, 0.96), (2000, 0.93), (3000, 0.90), (4000, 0.86),
    (5000, 0.83), (6000, 0.80)])
def test_ke_table_26_9_1(z_ft, table):
    """Table 26.9-1 Ke = exp(-0.0000362 zg[ft]) = exp(-0.000119 zg[m])."""
    assert round(al.asce22_ke(z_ft * FT), 2) == table
    assert al.asce22_ke(z_ft * FT) == pytest.approx(
        math.exp(-0.0000362 * z_ft), rel=5e-4)


def test_wind_split_windward_leeward_and_overrides():
    """Exposure C, V = 50 m/s, Ke = 1 (override), Kd 0.85, G 0.85,
    Cp_w 0.8, Cp_l -0.5: qh = 0.613 Kz(10 m) 2500/1000;
    F_i = Kd G (qz_i 0.8 + qh 0.5) trib_i 6 m; S1 at z = 4 m < 15 ft uses
    Kz(15 ft) = 0.851154."""
    m = _bldg()
    s = al.compute(m, "asce7_22_wind", V=50.0, exposure="C", Ke=1.0,
                   G=0.85, cp_windward=0.8, cp_leeward=-0.5, ze=900.0)
    kz15 = 2.41 * (15 / 2460) ** (2 / 9.8)
    assert kz15 == pytest.approx(0.851154, rel=1e-6)
    kzh = 2.41 * (10.0 / (2460 * FT)) ** (2 / 9.8)
    qh = 0.613 * kzh * 2500 / 1000
    q1 = 0.613 * kz15 * 2500 / 1000
    assert s["Ke"] == 1.0 and s["qh"] == pytest.approx(qh, rel=1e-12)
    assert s["stories"][0]["F"] == pytest.approx(
        0.85 * 0.85 * (q1 * 0.8 + qh * 0.5) * 3.5 * 6.0, rel=1e-12)
    assert s["stories"][2]["F"] == pytest.approx(
        0.85 * 0.85 * qh * 1.3 * 1.5 * 6.0, rel=1e-12)
    # combined form with G: p = qz Kd G cp_total
    s2 = al.compute(m, "asce7_22_wind", V=50.0, G=0.85)
    q3 = 0.613 * kzh * 2500 / 1000
    assert s2["stories"][2]["F"] == pytest.approx(
        q3 * 0.85 * 0.85 * 1.3 * 9.0, rel=1e-12)
    with pytest.raises(ValueError, match="both"):
        al.compute(m, "asce7_22_wind", V=50.0, cp_windward=0.8)
