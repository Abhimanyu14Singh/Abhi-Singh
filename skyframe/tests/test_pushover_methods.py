"""Pushover performance methods: ADRS, ATC-40, FEMA 440 EL, EN 1998 N2.

Every number is pinned to a closed form or a hand calculation written out
below (independently of :mod:`skyframe.core.pushover_methods`).
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from skyframe.core import pushover_methods as pm

G = 9.81
PI2 = 4.0 * math.pi ** 2


# ----------------------------------------------------------------------
# helpers: an exactly bilinear capacity spectrum (Sd m, Sa g)
# ----------------------------------------------------------------------
def _bilinear_curve(K, dy, alpha, d_end, n=400):
    sd = [d_end * i / n for i in range(1, n + 1)]
    # make dy an exact vertex so the equal-area bilinear is exact
    sd = sorted(set(sd + [dy]))
    sa = [K * d if d <= dy else K * dy + alpha * K * (d - dy) for d in sd]
    return sd, sa


T0 = 0.5
K = PI2 / (T0 ** 2 * G)          # g/m, initial slope of T0 = 0.5 s
DY = 0.02
ALPHA = 0.05


# ----------------------------------------------------------------------
# FEMA 440 effective period / damping (Eqs. 6-5 .. 6-10), B, M
# ----------------------------------------------------------------------
@pytest.mark.parametrize("mu,beta,ratio", [
    # mu < 4: 4.9 m^2 - 1.1 m^3 + 5 ; 0.20 m^2 - 0.038 m^3 + 1  (m = 2)
    (3.0, 4.9 * 4 - 1.1 * 8 + 5.0, 0.20 * 4 - 0.038 * 8 + 1.0),
    # 4 <= mu <= 6.5: 14 + 0.32 m + 5 ; 0.28 + 0.13 m + 1  (m = 4)
    (5.0, 14.0 + 0.32 * 4 + 5.0, 0.28 + 0.13 * 4 + 1.0),
])
def test_fema440_coefficients_low_mid(mu, beta, ratio):
    el = pm.fema440_el(mu, 0.0, 0.8)
    assert el["beta_eff"] == pytest.approx(beta, abs=1e-12)
    assert el["Teff"] == pytest.approx(ratio * 0.8, abs=1e-12)
    # mu = 3: 15.8 %, Teff = 1.496 T0 ; mu = 5: 20.28 %, 1.80 T0
    assert el["beta_eff"] == pytest.approx({3.0: 15.8, 5.0: 20.28}[mu])
    assert el["Teff"] / 0.8 == pytest.approx({3.0: 1.496, 5.0: 1.80}[mu])


def test_fema440_coefficients_high_ductility():
    mu = 8.0
    m = mu - 1
    r = 0.89 * (math.sqrt(m / (1 + 0.05 * (mu - 2))) - 1) + 1
    # sqrt(7 / 1.3) = 2.3204774 -> 0.89 * 1.3204774 + 1 = 2.1752249
    assert r == pytest.approx(2.1752249, abs=1e-6)
    q = 0.64 * m
    beta = 19 * ((q - 1) / q ** 2) * r ** 2 + 5
    assert beta == pytest.approx(20.588, abs=2e-3)
    el = pm.fema440_el(mu, 0.1, 1.0)
    assert el["Teff"] == pytest.approx(r)
    assert el["beta_eff"] == pytest.approx(beta)
    # B = 4 / (5.6 - ln beta) ; M = (Teff/T0)^2 (1 + a(mu-1))/mu
    assert el["B"] == pytest.approx(4 / (5.6 - math.log(beta)))
    assert el["M"] == pytest.approx(r * r * (1 + 0.1 * 7) / 8)
    assert el["Tsec"] == pytest.approx(1.0 / math.sqrt((1 + 0.1 * 7) / 8))


def test_fema440_elastic_limit():
    el = pm.fema440_el(1.0, 0.0, 0.7)
    assert el["beta_eff"] == 5.0 and el["Teff"] == 0.7 and el["M"] == 1.0


# ----------------------------------------------------------------------
# ATC-40 kappa (Table 8-1), beta0 (Eq. 8-6), SRA / SRV (Eqs. 8-9, 8-10)
# ----------------------------------------------------------------------
def test_atc40_kappa_by_type():
    assert pm.atc40_kappa("A", 10.0, 10 / 63.7) == 1.0
    assert pm.atc40_kappa("A", 30.0, 0.4) == pytest.approx(1.13 - 0.51 * 0.4)
    assert pm.atc40_kappa("B", 20.0, 20 / 63.7) == 0.67
    assert pm.atc40_kappa("B", 40.0, 0.5) == pytest.approx(0.845 - 0.446 * 0.5)
    assert pm.atc40_kappa("C", 40.0, 0.5) == 0.33
    assert pm.atc40_kappa("C", 1.0, 0.01) == 0.33
    # the published branches are continuous at their limits
    assert pm.atc40_kappa("A", 16.2501, 16.25 / 63.7) == pytest.approx(1.0, abs=2e-4)
    assert pm.atc40_kappa("B", 25.0001, 25 / 63.7) == pytest.approx(0.67, abs=2e-4)
    with pytest.raises(ValueError):
        pm.atc40_kappa("D", 1.0, 0.1)


def test_atc40_damping_hand():
    # bilinear ay = 0.3219 g at dy = 0.02 ; trial point dpi = 0.08
    ay = K * DY
    api = ay + ALPHA * K * (0.08 - DY)
    bl = {"ay": ay, "dy": DY, "api": api, "dpi": 0.08}
    x = (ay * 0.08 - DY * api) / (api * 0.08)
    beta0 = 63.7 * x
    for typ in ("A", "B", "C"):
        dm = pm.atc40_damping(bl, typ)
        kap = pm.atc40_kappa(typ, beta0, x)
        beff = kap * beta0 + 5
        assert dm["beta0"] == pytest.approx(beta0)
        assert dm["beta_eff"] == pytest.approx(beff)
        sra = max((3.21 - 0.68 * math.log(beff)) / 2.12, pm.SR_MIN[typ][0])
        srv = max((2.31 - 0.41 * math.log(beff)) / 1.65, pm.SR_MIN[typ][1])
        assert dm["SRA"] == pytest.approx(sra)
        assert dm["SRV"] == pytest.approx(srv)
    # type A, beta0 = 63.7 * 0.6726... > 16.25 -> kappa from the formula
    assert beta0 > 16.25
    assert pm.atc40_damping(bl, "A")["kappa"] == pytest.approx(1.13 - 0.51 * x)


def test_atc40_sr_minimums():
    # a near-EPP curve with huge ductility hits the Table 8-2 floors
    bl = {"ay": 1.0, "dy": 0.01, "api": 1.0, "dpi": 1.0}
    for typ, (smin, vmin) in pm.SR_MIN.items():
        dm = pm.atc40_damping(bl, typ)
        if typ == "A":
            assert dm["SRA"] == pytest.approx(max(
                (3.21 - 0.68 * math.log(dm["beta_eff"])) / 2.12, smin))
        assert dm["SRA"] >= smin and dm["SRV"] >= vmin
    dmc = pm.atc40_damping(bl, "C")
    assert dmc["beta_eff"] == pytest.approx(0.33 * 63.7 * 0.99 + 5)


# ----------------------------------------------------------------------
# bilinear representation (equal area, initial stiffness)
# ----------------------------------------------------------------------
def test_bilinear_exact_on_bilinear_curve():
    sd, sa = _bilinear_curve(K, DY, ALPHA, 0.3)
    cap = pm.Capacity(sd, sa)
    for dpi in (0.05, 0.1, 0.25):
        bl = cap.bilinear(dpi)
        assert bl["dy"] == pytest.approx(DY, rel=1e-9)
        assert bl["alpha"] == pytest.approx(ALPHA, rel=1e-8)
        assert bl["mu"] == pytest.approx(dpi / DY, rel=1e-9)
        assert bl["T0"] == pytest.approx(T0, rel=1e-12)
    # inside the elastic branch: dy = dpi, mu = 1
    assert cap.bilinear(0.01)["mu"] == pytest.approx(1.0)


def test_bilinear_equal_area_on_curved_capacity():
    sd = [0.01 * i for i in range(1, 31)]
    sa = [0.6 * (1 - math.exp(-d / 0.05)) for d in sd]
    cap = pm.Capacity(sd, sa)
    bl = cap.bilinear(0.2)
    # areas: bilinear == curve up to dpi
    A_bil = bl["ay"] * bl["dy"] / 2 + (bl["dpi"] - bl["dy"]) * (bl["ay"] + bl["api"]) / 2
    assert A_bil == pytest.approx(cap.area(0.2), rel=1e-12)
    assert bl["ay"] == pytest.approx(cap.K * bl["dy"])


# ----------------------------------------------------------------------
# FEMA 440 iteration converges to the hand intersection
# ----------------------------------------------------------------------
def _hand_fema440_dp(SDS, SD1):
    """Fixed point dp = Sa(Teff)/B Teff^2 g/4pi^2 (the Teff radial line
    meets the reduced spectrum at dp) — bisection written out here."""
    def sa(T):
        Ts = SD1 / SDS
        T0_ = 0.2 * Ts
        if T < T0_:
            return SDS * (0.4 + 0.6 * T / T0_)
        return SDS if T <= Ts else SD1 / T

    def g(dp):
        mu = dp / DY
        m = mu - 1
        if mu < 4:
            beta = 4.9 * m ** 2 - 1.1 * m ** 3 + 5
            r = 0.20 * m ** 2 - 0.038 * m ** 3 + 1
        elif mu <= 6.5:
            beta = 14 + 0.32 * m + 5
            r = 0.28 + 0.13 * m + 1
        else:
            r = 0.89 * (math.sqrt(m / (1 + 0.05 * (mu - 2))) - 1) + 1
            q = 0.64 * m
            beta = 19 * ((q - 1) / q ** 2) * r ** 2 + 5
        Te = r * T0
        B = 4 / (5.6 - math.log(beta))
        return sa(Te) / B * Te ** 2 * G / PI2 - dp
    lo, hi = DY * 1.0001, 0.3
    assert g(lo) > 0 > g(hi)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if g(mid) > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


@pytest.mark.parametrize("SDS,SD1", [(1.0, 0.6), (0.8, 0.3), (1.5, 1.0)])
def test_fema440_iteration_hits_hand_intersection(SDS, SD1):
    sd, sa = _bilinear_curve(K, DY, ALPHA, 0.3)
    cap = pm.Capacity(sd, sa)
    spec = pm.Spectrum({"code": "asce7", "SDS": SDS, "SD1": SD1})
    res = pm.fema440(cap, spec)
    hand = _hand_fema440_dp(SDS, SD1)
    assert res["converged"]
    assert res["performance_point"]["Sd"] == pytest.approx(hand, rel=1e-5)
    # the MADRS point lies on the capacity, the radial check agrees
    assert res["d_max_radial"] == pytest.approx(hand, rel=1e-5)
    assert res["mu"] == pytest.approx(hand / DY, rel=1e-5)
    assert len(res["iterations"]) >= 2


# ----------------------------------------------------------------------
# ATC-40 procedures A and B on a bilinear capacity: the hand fixed point
# ----------------------------------------------------------------------
def _hand_atc40_dp(SDS, SD1, typ):
    def red_sa_at_sd(d, sra, srv):
        # velocity-region branch Sa = (SRV SD1)^2 g / (4 pi^2 d) capped by
        # the reduced plateau SRA SDS
        return min(sra * SDS, (srv * SD1) ** 2 * G / (PI2 * d))

    def g(d):
        ay = K * DY
        api = ay + ALPHA * K * (d - DY)
        x = (ay * d - DY * api) / (api * d)
        b0 = 63.7 * x
        kap = pm.atc40_kappa(typ, b0, x)
        be = kap * b0 + 5
        sra = max((3.21 - 0.68 * math.log(be)) / 2.12, pm.SR_MIN[typ][0])
        srv = max((2.31 - 0.41 * math.log(be)) / 1.65, pm.SR_MIN[typ][1])
        return red_sa_at_sd(d, sra, srv) - api
    lo, hi = DY * 1.001, 0.3
    assert g(lo) > 0 > g(hi)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if g(mid) > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


@pytest.mark.parametrize("typ", ["A", "B", "C"])
def test_atc40_procedures_hit_hand_intersection(typ):
    SDS, SD1 = 1.0, 0.6
    sd, sa = _bilinear_curve(K, DY, ALPHA, 0.3)
    cap = pm.Capacity(sd, sa)
    spec = pm.Spectrum({"code": "asce7", "SDS": SDS, "SD1": SD1})
    hand = _hand_atc40_dp(SDS, SD1, typ)
    a = pm.atc40(cap, spec, typ, "A")
    b = pm.atc40(cap, spec, typ, "B")
    assert a["converged"] and b["converged"]
    assert a["performance_point"]["Sd"] == pytest.approx(hand, rel=1e-5)
    assert b["performance_point"]["Sd"] == pytest.approx(hand, rel=1e-5)
    # the intersection is in the reduced velocity region (T > TC')
    assert a["Teff"] > SD1 / SDS
    # stiffer damping reduction (type A) -> smaller displacement
    if typ == "C":
        hA = _hand_atc40_dp(SDS, SD1, "A")
        assert hand > hA


# ----------------------------------------------------------------------
# EN 1998-1 Annex B (N2)
# ----------------------------------------------------------------------
def _ec8(ag, gt="C", st=1):
    return pm.Spectrum({"code": "ec8", "ag": ag, "ground_type": gt,
                        "spectrum_type": st})


def test_ec8_spectrum_table_and_branches():
    sp = _ec8(0.3)
    assert (sp.S, sp.TB, sp.TC, sp.TD) == (1.15, 0.2, 0.6, 2.0)
    a = 0.3 * 1.15
    assert sp.sa(0.0) == pytest.approx(a)
    assert sp.sa(0.4) == pytest.approx(2.5 * a)
    assert sp.sa(1.2) == pytest.approx(2.5 * a * 0.6 / 1.2)
    assert sp.sa(3.0) == pytest.approx(2.5 * a * 0.6 * 2.0 / 9.0)
    sp2 = _ec8(0.2, "D", 2)
    assert (sp2.S, sp2.TB, sp2.TC, sp2.TD) == (1.8, 0.1, 0.3, 1.2)


def _n2_curve(gamma, m_star, Fy, dy, dm, n=50):
    """MDOF curve whose SDOF image is EXACTLY EPP (F*y, d*y, d*m)."""
    ds = [dm * i / n for i in range(1, n + 1)]
    ds = sorted(set(ds + [dy]))
    fs = [Fy * min(d / dy, 1.0) for d in ds]
    return [gamma * d for d in ds], [gamma * f for f in fs]


def test_n2_short_period_hand():
    """Gamma 1.25, m* 100 t, F*y 500 kN, d*y 0.02 m, d*m 0.10 m.

    E*m = 500 (0.10 - 0.01) = 45 -> d*y = 2 (0.10 - 45/500) = 0.02
    T* = 2 pi sqrt(100 * 0.02 / 500) = 0.39738 s < TC = 0.6 s
    Se = 2.5 * 0.3 * 1.15 = 0.8625 g ; d*et = Se g (T*/2pi)^2
          = 8.461125 * 0.004 = 0.0338445 m
    F*y/m* = 5.0 < Se g = 8.461125 -> qu = 1.692225
    d*t = d*et/qu (1 + (qu - 1) TC/T*) = 0.02 (1 + 0.692225 * 1.509875)
        = 0.0409035 m ;  dt = 1.25 d*t = 0.0511294 m
    """
    disp, shear = _n2_curve(1.25, 100.0, 500.0, 0.02, 0.10)
    r = pm.n2(disp, shear, gamma=1.25, m_star=100.0, spec=_ec8(0.3))
    assert r["E_star_m"] == pytest.approx(45.0)
    assert r["d_star_y"] == pytest.approx(0.02)
    T = 2 * math.pi * math.sqrt(0.004)
    assert r["T_star"] == pytest.approx(T)
    assert r["T_star"] == pytest.approx(0.397384, abs=1e-6)
    assert r["Se"] == pytest.approx(0.8625)
    assert r["d_star_et"] == pytest.approx(0.8625 * 9.81 * 0.004)
    assert r["qu"] == pytest.approx(0.8625 * 9.81 / 5.0)
    assert r["rule"] == "short_period"
    qu = 0.8625 * 9.81 / 5.0
    dst = 0.02 * (1 + (qu - 1) * 0.6 / T)
    assert r["d_star_t"] == pytest.approx(dst)
    assert r["d_star_t"] == pytest.approx(0.0409035, abs=1e-6)
    assert r["dt"] == pytest.approx(1.25 * dst)


def test_n2_equal_displacement_and_elastic():
    # long period: m* 500 t, F*y 500, d*y 0.08 -> T* = 2pi sqrt(0.08)
    # = 1.7772 s in TC..TD: Se = 2.5 ag S TC/T*, d*t = d*et
    disp, shear = _n2_curve(1.3, 500.0, 500.0, 0.08, 0.4)
    r = pm.n2(disp, shear, gamma=1.3, m_star=500.0, spec=_ec8(0.3))
    T = 2 * math.pi * math.sqrt(0.08)
    assert r["T_star"] == pytest.approx(T)
    assert r["rule"] == "equal_displacement"
    se = 2.5 * 0.345 * 0.6 / T
    assert r["d_star_t"] == pytest.approx(se * 9.81 * 0.08)
    assert r["dt"] == pytest.approx(1.3 * se * 9.81 * 0.08)
    # short period but strong enough: elastic response, d*t = d*et
    disp, shear = _n2_curve(1.0, 10.0, 500.0, 0.004, 0.02)
    r = pm.n2(disp, shear, gamma=1.0, m_star=10.0, spec=_ec8(0.3))
    assert r["T_star"] < 0.6 and r["rule"] == "elastic"
    assert r["d_star_t"] == pytest.approx(r["d_star_et"])


def test_n2_with_asce_spectrum_uses_ts_as_tc():
    disp, shear = _n2_curve(1.25, 100.0, 500.0, 0.02, 0.10)
    sp = pm.Spectrum({"code": "asce7", "SDS": 1.0, "SD1": 0.6})
    r = pm.n2(disp, shear, gamma=1.25, m_star=100.0, spec=sp)
    assert r["TC"] == pytest.approx(0.6)
    assert r["Se"] == pytest.approx(1.0)


# ----------------------------------------------------------------------
# ADRS conversion + evaluate() on a known curve
# ----------------------------------------------------------------------
def test_adrs_conversion_hand():
    sd, sa = pm.adrs([0.05, 0.1], [300.0, 450.0], W=2000.0, alpha=0.8,
                     pf_phi=1.25)
    assert sd == [0.04, 0.08]
    assert sa == pytest.approx([300 / 1600, 450 / 1600])
    with pytest.raises(ValueError):
        pm.adrs([0.1], [1.0], W=0.0, alpha=0.8, pf_phi=1.0)


def test_evaluate_all_methods_consistent():
    # MDOF curve from an exactly bilinear capacity spectrum
    pf, alpha, M = 1.3, 0.8, 200.0
    W = M * G
    sd, sa = _bilinear_curve(K, DY, ALPHA, 0.3)
    disp = [pf * d for d in sd]
    shear = [alpha * W * a for a in sa]
    modal = {"T": T0, "gamma": 1.0, "phi_ctrl": pf, "pf_phi": pf,
             "alpha": alpha, "mass": M, "W": W, "mode": 1}
    out = pm.evaluate(disp, shear, modal=modal, options={
        "spectrum": {"code": "asce7", "SDS": 1.0, "SD1": 0.6}})
    rows = {r["method"]: r for r in out["summary"]}
    assert list(rows) == list(pm.METHODS)
    # FEMA 440 / ATC-40 rows equal the stand-alone solutions
    hand = _hand_fema440_dp(1.0, 0.6)
    assert rows["fema440"]["Sd"] == pytest.approx(hand, rel=1e-5)
    assert rows["fema440"]["roof_disp"] == pytest.approx(pf * hand, rel=1e-5)
    assert rows["atc40"]["Sd"] == pytest.approx(_hand_atc40_dp(1.0, 0.6, "A"),
                                                rel=1e-5)
    # Sa and V on the row follow the ADRS conversion
    r = rows["fema440"]
    assert r["Sa"] == pytest.approx(r["base_shear"] / (alpha * W))
    # N2: m* = alpha M / Gamma
    n2 = out["methods"]["n2"]
    assert n2["m_star"] == pytest.approx(alpha * M / pf)
    assert rows["n2"]["roof_disp"] == pytest.approx(n2["dt"])
    # ASCE 41 is the existing coefficient method
    from skyframe.design.performance import target_displacement
    td = target_displacement(disp, shear, Ti=T0, W=W, SDS=1.0, SD1=0.6)
    assert rows["asce41"]["roof_disp"] == pytest.approx(td["delta_t"])
    json.dumps(out)                                     # JSON-safe


def test_asce41_general_spectrum_matches_existing():
    sd, sa = _bilinear_curve(K, DY, ALPHA, 0.3, n=50)
    disp = [1.2 * d for d in sd]
    shear = [1500.0 * a for a in sa]
    from skyframe.design.performance import target_displacement
    td = target_displacement(disp, shear, Ti=0.5, W=2000.0, SDS=1.0,
                             SD1=0.6, site_class="C", num_stories=3)
    # same spectrum through the general path (TL beyond every period)
    sp = pm.Spectrum({"code": "asce7", "SDS": 1.0, "SD1": 0.6, "TL": 50.0})
    g = pm.asce41(disp, shear, Ti=0.5, W=2000.0, spec=sp, site_class="C",
                  num_stories=3)
    for k in ("Te", "Sa", "mu", "C0", "C1", "C2", "delta_t"):
        assert g[k] == pytest.approx(td[k], rel=1e-12)


def test_demand_exceeds_capacity_reports_none():
    sd, sa = _bilinear_curve(K, DY, 0.0, 0.03, n=30)      # short, weak
    modal = {"T": T0, "gamma": 1, "phi_ctrl": 1, "pf_phi": 1.0,
             "alpha": 1.0, "mass": 100.0, "W": 981.0, "mode": 1}
    out = pm.evaluate([d for d in sd], [981.0 * a for a in sa], modal=modal,
                      options={"methods": ["fema440", "atc40"],
                               "spectrum": {"code": "asce7", "SDS": 2.0,
                                            "SD1": 1.5}})
    rows = {r["method"]: r for r in out["summary"]}
    assert rows["fema440"]["roof_disp"] is None
    assert rows["atc40"]["roof_disp"] is None
    assert any("exceeds the capacity" in w for w in out["warnings"])


# ----------------------------------------------------------------------
# options validation
# ----------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    {"methods": []}, {"methods": ["asce41", "asce41"]}, {"methods": ["x"]},
    {"behavior": "D"}, {"atc40_procedure": "C"}, {"site_class": "Z"},
    {"spectrum": {"code": "asce7", "SDS": 0}},
    {"spectrum": {"code": "ec8", "ag": -1}},
    {"spectrum": {"code": "ec8", "ag": 0.2, "ground_type": "Q"}},
    {"spectrum": {"code": "ec8", "ag": 0.2, "TB": 0.9, "TC": 0.5}},
    {"spectrum": {"code": "nz"}}, {"foo": 1},
])
def test_options_rejected(bad):
    with pytest.raises(ValueError):
        pm.normalize_options(bad)


def test_options_defaults():
    o = pm.normalize_options(None)
    assert o == {"methods": ["asce41", "atc40", "fema440", "n2"],
                 "spectrum": {"code": "asce7", "SDS": 1.0, "SD1": 0.6},
                 "site_class": "D", "behavior": "A",
                 "atc40_procedure": "A"}
