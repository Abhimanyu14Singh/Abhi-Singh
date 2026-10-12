"""Code response-spectrum functions, code design combinations, pattern
types and live-load reduction (CONTRACT "Code spectra, design
combinations, pattern types and live-load reduction").

Every spectrum is pinned at its plateau, its corner periods and one
hand-computed point; every combination generator to the code table (count
and factors) for a canonical model; every live-load reduction formula to a
worked example, and the application to frame / pier output to linear
superposition.
"""

import json
import math
import warnings

import pytest

from skyframe.core import combos_codes as cc
from skyframe.core import llr_codes as llr
from skyframe.core import spectra_codes as sc
from skyframe.core import tables
from skyframe.core.builder import quick_building
from skyframe.core.codes import asce7_spectrum, live_load_reduction
from skyframe.core.model import BuildingModel

A = pytest.approx


def sa(code, T, **p):
    return sc.spectrum_value(code, p, T)


# =========================================================================== #
# 1. spectra
# =========================================================================== #
def test_catalogue_lists_every_code_with_defaults():
    cat = {c["code"]: c for c in sc.catalogue()}
    assert set(cat) == {"asce7_16", "asce7_22_mprs", "asce7_22_two_period",
                        "ec8", "is1893_2016", "nbcc_2020", "nzs1170_5",
                        "as1170_4", "gb50011"}
    for c in cat.values():
        keys = [p["key"] for p in c["params"]]
        assert len(keys) == len(set(keys)) and "T_max" in keys
    json.dumps(sc.catalogue())                       # JSON-safe


def test_asce7_16_matches_existing_helper():
    # Ss 1.5, S1 0.6, D: Fa 1.0, Fv 1.7 -> SDS 1.0, SD1 0.68
    ref = asce7_spectrum(1.5, 0.6, "D", 8.0)
    for T, v in ref:
        assert sa("asce7_16", T, Ss=1.5, S1=0.6, site_class="D") == A(v)
    assert sa("asce7_16", 0.3, Ss=1.5, S1=0.6, R=8, Ie=1.25) == A(1.25 / 8)


def test_asce7_22_two_period():
    p = dict(SDS=1.2, SD1=0.72, TL=6.0)
    # T0 = 0.2*0.6 = 0.12, Ts = 0.6
    assert sa("asce7_22_two_period", 0.0, **p) == A(0.48)
    assert sa("asce7_22_two_period", 0.12, **p) == A(1.2)
    assert sa("asce7_22_two_period", 0.6, **p) == A(1.2)
    assert sa("asce7_22_two_period", 1.0, **p) == A(0.72)
    assert sa("asce7_22_two_period", 10.0, **p) == A(0.72 * 6 / 100)
    s = sc.spectrum("asce7_22_two_period", p)["summary"]
    assert (s["T0"], s["Ts"]) == (A(0.12), A(0.6))


MPRS = [0.5, 0.5, 0.55, 0.6, 0.75, 0.9, 1.05, 1.2, 1.3, 1.3, 1.25, 1.15,
        1.05, 0.85, 0.7, 0.5, 0.38, 0.24, 0.17, 0.13, 0.08, 0.05]


def test_asce7_22_mprs_mcer_ordinates():
    p = dict(ordinates=MPRS, basis="mcer", TL=8.0)
    f = 2.0 / 3.0
    # tabulated points: Sa = 2/3 MCE_R
    assert sa("asce7_22_mprs", 0.2, **p) == A(f * 1.3)
    assert sa("asce7_22_mprs", 1.0, **p) == A(f * 0.7)
    # linear interpolation 0.5 .. 0.75 at 0.6: 1.05 + (0.85-1.05)*0.4
    assert sa("asce7_22_mprs", 0.6, **p) == A(f * 0.97)
    # TL = 8 < Te = 10: Sa(Te)(Te/T)^2 beyond 10 s
    assert sa("asce7_22_mprs", 12.0, **p) == A(f * 0.05 * (10 / 12) ** 2)
    s = sc.spectrum("asce7_22_mprs", p)["summary"]
    assert s["SDS"] == A(0.9 * f * 1.3)               # §21.4: 90 % max Sa
    pts = sc.spectrum("asce7_22_mprs", p)["points"]
    for t in sc.MPRS_PERIODS:                          # every period exact
        assert any(abs(q[0] - t) < 1e-9 for q in pts)
    # design-basis ordinates are used as given; [T, Sa] pairs accepted
    q = dict(ordinates=[[0.0, 0.4], [0.2, 1.0], [1.0, 0.6]],
             basis="design", TL=8.0, T_max=4.0)
    assert sa("asce7_22_mprs", 0.1, **q) == A(0.7)
    assert sa("asce7_22_mprs", 2.0, **q) == A(0.3)     # Sa(Te) Te/T
    with pytest.raises(ValueError):
        sc.spectrum("asce7_22_mprs", dict(ordinates=[0.1, 0.2]))
    with pytest.raises(ValueError):
        sc.spectrum("asce7_22_mprs", {})


def test_ec8_type1_type2_elastic_and_design():
    p = dict(spectrum_type=1, ground_type="B", agR=0.25)
    assert sa("ec8", 0.0, **p) == A(0.3)                # ag S
    assert sa("ec8", 0.3, **p) == A(0.75)               # plateau 2.5 ag S
    assert sa("ec8", 1.0, **p) == A(0.375)              # 0.75 * 0.5 / 1
    assert sa("ec8", 3.0, **p) == A(0.75 * 0.5 * 2 / 9)
    # 10 % damping: eta = sqrt(10/15)
    assert sa("ec8", 0.3, damping=0.10, **p) == A(0.75 * math.sqrt(2 / 3))
    # Type 2 ground C: S 1.5, TB 0.1, TC 0.25, TD 1.2
    t2 = dict(spectrum_type=2, ground_type="C", agR=0.1)
    assert sa("ec8", 0.2, **t2) == A(0.1 * 1.5 * 2.5)
    assert sa("ec8", 0.5, **t2) == A(0.375 * 0.25 / 0.5)
    assert sa("ec8", 2.0, **t2) == A(0.375 * 0.25 * 1.2 / 4)
    # design spectrum q = 3.9 (Eq. 3.13-3.16)
    d = dict(p, kind="design", q=3.9)
    assert sa("ec8", 0.0, **d) == A(0.25 * 1.2 * 2 / 3)
    assert sa("ec8", 0.3, **d) == A(0.25 * 1.2 * 2.5 / 3.9)
    assert sa("ec8", 3.0, **d) == A(0.2 * 0.25)         # beta ag floor
    # gamma_I and national-annex override
    assert sa("ec8", 0.3, gamma_I=1.2, TC=0.6, **p) == A(0.9)
    assert sa("ec8", 0.55, gamma_I=1.2, TC=0.6, **p) == A(0.9)


def test_is1893_response_spectrum():
    p = dict(zone="IV", soil="II", I=1.0, R=5.0)        # Ah factor 0.024
    assert sa("is1893_2016", 0.0, **p) == A(0.024)      # 1 + 15T at 0
    assert sa("is1893_2016", 0.05, **p) == A(0.024 * 1.75)
    assert sa("is1893_2016", 0.3, **p) == A(0.06)       # plateau 2.5
    assert sa("is1893_2016", 1.0, **p) == A(0.03264)    # 1.36 / T
    assert sa("is1893_2016", 5.0, **p) == A(0.024 * 0.34)
    assert sa("is1893_2016", 0.3, soil="I", output="sa_g") == A(2.5)
    assert sa("is1893_2016", 0.6, soil="I", output="sa_g") == A(1 / 0.6)
    assert sa("is1893_2016", 0.6, soil="III", output="sa_g") == A(2.5)
    # Table 3 damping factors (2 % -> 1.4, 3.5 % -> 1.2 interpolated)
    assert sc.is1893_damping_factor(0.02) == A(1.4)
    assert sc.is1893_damping_factor(0.035) == A(1.2)
    assert sa("is1893_2016", 0.3, damping=0.02, output="sa_g") == A(3.5)


def test_nbcc_2020():
    p = {}                                   # defaults 0.848/0.751/0.425/...
    assert sa("nbcc_2020", 0.1, **p) == A(0.848)
    assert sa("nbcc_2020", 0.2, **p) == A(0.848)
    assert sa("nbcc_2020", 0.75, **p) == A(0.588)        # linear 0.5-1.0
    assert sa("nbcc_2020", 3.0, **p) == A(0.257 - 0.177 / 3)
    assert sa("nbcc_2020", 12.0, **p) == A(0.029)        # S(10) beyond
    # class E-like: Sa(0.5) > Sa(0.2) -> plateau max(Sa02, Sa05)
    q = dict(Sa02=0.6, Sa05=0.7, Sa10=0.5, Sa20=0.3)
    assert sa("nbcc_2020", 0.1, Sa50=None, Sa100=None, **q) == A(0.7)
    assert sa("nbcc_2020", 0.35, Sa50=None, Sa100=None, **q) == A(0.7)
    assert sa("nbcc_2020", 4.0, Sa50=None, Sa100=None, **q) == A(0.3)
    assert sa("nbcc_2020", 1.0, RdRo=5.0, Ie=1.5, **p) == A(0.425 * 0.3)


def test_nzs1170_5():
    p = dict(site_class="C", Z=0.4)
    assert sa("nzs1170_5", 0.0, **p) == A(0.4 * 1.33)
    assert sa("nzs1170_5", 0.2, **p) == A(0.4 * 2.93)
    assert sa("nzs1170_5", 1.0, **p) == A(0.4 * 2.0 * 0.5 ** 0.75)
    assert sa("nzs1170_5", 2.0, **p) == A(0.4 * 1.32 / 2)
    assert sa("nzs1170_5", 4.0, **p) == A(0.4 * 3.96 / 16)
    for site, tc in (("A", 0.3), ("D", 0.56), ("E", 1.0)):   # continuity
        assert sc.nzs_ch(tc, site) == A(sc.nzs_ch(tc + 1e-9, site), rel=5e-3)
        assert sc.nzs_ch(1.5, site) == A(sc.nzs_ch(1.5 + 1e-9, site),
                                         rel=5e-3)     # table rounding
    d = dict(p, kind="design", mu=4.0)                  # Sp 0.7, k_mu 4
    assert sa("nzs1170_5", 0.2, **d) == A(0.4 * 2.93 * 0.7 / 4)
    assert sa("nzs1170_5", 5.0, **d) == A(0.04)          # (Z/20+0.02)R
    assert sc.nzs_kmu(4.0, 0.35, "C") == A(2.5)
    assert sc.nzs_sp(1.5) == A(0.85)
    with pytest.raises(ValueError):
        sc.spectrum("nzs1170_5", dict(Z=0.5, R=1.8))     # ZR > 0.7


def test_as1170_4():
    p = dict(site_class="Ce", kp=1.0, Z=0.08)
    assert sa("as1170_4", 0.0, **p) == A(0.08 * 1.3)
    assert sa("as1170_4", 0.2, **p) == A(0.08 * 3.68)     # plateau cap
    assert sa("as1170_4", 1.0, **p) == A(0.08 * 1.25)
    assert sa("as1170_4", 2.0, **p) == A(0.08 * 1.874 / 4)
    assert sa("as1170_4", 0.2, kind="design", Sp=0.77, mu=2.0,
              **p) == A(0.08 * 3.68 * 0.385)
    # kp Z not less than 0.08
    assert sa("as1170_4", 0.2, site_class="Be", kp=0.5, Z=0.08) == \
        A(0.08 * 2.94)
    for s in sc.AS_SITES:                                 # continuity at 1.5
        assert sc.as_ch(1.5, s) == A(sc.as_ch(1.5 + 1e-9, s), rel=2e-3)


def test_gb50011():
    p = dict(intensity="8", level="frequent", group="1", site="II")
    assert sa("gb50011", 0.0, **p) == A(0.45 * 0.16)
    assert sa("gb50011", 0.2, **p) == A(0.16)
    assert sa("gb50011", 1.0, **p) == A(0.16 * (0.35 / 1.0) ** 0.9)
    assert sa("gb50011", 2.0, **p) == A((0.2 ** 0.9 - 0.02 * 0.25) * 0.16)
    s = sc.spectrum("gb50011", dict(p, level="rare"))["summary"]
    assert (s["alpha_max"], s["Tg"]) == (A(0.90), A(0.40))
    g, e1, e2 = sc.gb_coefficients(0.02)
    assert (g, e1, e2) == (A(0.9 + 0.03 / 0.42), A(0.02 + 0.03 / 4.64),
                           A(1 + 0.03 / 0.112))


def test_spectrum_points_include_corners_and_validation():
    sp = sc.spectrum("ec8", dict(spectrum_type=1, ground_type="D"))
    ts = [p[0] for p in sp["points"]]
    for c in (0.0, 0.2, 0.8, 2.0, 4.0):
        assert c in ts
    assert ts == sorted(ts) and len(ts) == len(set(ts))
    with pytest.raises(ValueError):
        sc.spectrum("nope", {})
    with pytest.raises(ValueError):
        sc.spectrum("ec8", {"bogus": 1})
    with pytest.raises(ValueError):
        sc.spectrum("ec8", {"agR": -1})
    with pytest.raises(ValueError):
        sc.spectrum("ec8", {"ground_type": "Z"})


def test_function_from_code_round_trip_and_legacy_unchanged():
    m = quick_building(stories=2)
    legacy = json.dumps(m.to_dict(), sort_keys=True)
    m.add_spectrum_function("USER", [[0, 0.1], [1, 0.2]])
    assert "source" not in m.to_dict()["spectrum_functions"]["USER"]
    fn = sc.make_spectrum_function(m, "IS", "is1893_2016",
                                   {"zone": "V", "soil": "I"})
    d = m.to_dict()["spectrum_functions"]["IS"]
    assert d["source"]["code"] == "is1893_2016"
    assert d["source"]["params"]["zone"] == "V"
    back = BuildingModel.from_dict(m.to_dict())
    assert back.spectrum_functions["IS"].source == fn.source
    assert back.spectrum_functions["IS"].points == fn.points
    bad = m.to_dict()
    bad["spectrum_functions"]["IS"]["source"] = {"code": "xx", "params": {}}
    with pytest.raises(ValueError):
        BuildingModel.from_dict(bad)
    m2 = quick_building(stories=2)
    assert json.dumps(BuildingModel.from_dict(m2.to_dict()).to_dict(),
                      sort_keys=True) == legacy
    # an RS case can use the function
    m.add_rs_case("RSIS", "X", [], function="IS")


# =========================================================================== #
# 2. combinations
# =========================================================================== #
def _combo_model(extra=()):
    m = quick_building(stories=2)
    m.combos.clear()
    for n, k in (("WX", "wind"), ("WY", "wind")) + tuple(extra):
        m.pattern(n, k)
        m.add_case(n, {n: 1.0})
    return m


def _by_factors(out):
    return {tuple(sorted(c["factors"].items())) for c in out}


def _has(out, **f):
    return tuple(sorted(f.items())) in _by_factors(out)


def test_pattern_kinds_and_classification():
    for k in ("superdead", "reducible_live", "roof_live", "snow",
              "temperature"):
        assert k in cc.PATTERN_KINDS
    from skyframe.core.modeltables import PATTERN_KINDS as MT
    assert MT == cc.PATTERN_KINDS
    m = _combo_model((("SD", "superdead"), ("RL", "reducible_live"),
                      ("LR", "roof_live"), ("SN", "snow"),
                      ("TMP", "temperature")))
    cls = cc.classify(m)
    assert cls["G"] == ["DEAD", "SD"] and cls["L"] == ["LIVE", "RL"]
    assert cls["Lr"] == ["LR"] and cls["S"] == ["SN"]
    assert cls["W"] == [("WX", True), ("WY", True)]
    assert cls["E"] == [("EQX", True), ("EQY", True)]
    assert cls["T"] == [("TMP", False)]
    m.add_case("MIX", {"DEAD": 1.0, "LIVE": 1.0})
    assert any("MIX" in w for w in cc.classify(m)["warnings"])


def test_en1990_eq610_table():
    m = _combo_model()
    out = cc.code_combinations(m, "en1990")
    uls = [c for c in out if c["name"].startswith("EN-ULS")]
    seis = [c for c in out if c["name"].startswith("EN-SEIS")]
    # 1 (G) + 4 (L lead x +-WX/WY) + 4 (W lead) + 4 (W lead, G fav.) = 13
    assert len(uls) == 13 and len(seis) == 4 and len(out) == 17
    assert _has(uls, DEAD=1.35)
    assert _has(uls, DEAD=1.35, LIVE=1.5, WX=0.9)
    assert _has(uls, DEAD=1.35, LIVE=1.05, WY=-1.5)
    assert _has(uls, DEAD=1.0, WX=1.5)
    assert _has(seis, DEAD=1.0, LIVE=0.3, EQY=-1.0)        # 6.12b psi2
    assert uls[0]["name"] == "EN-ULS01"
    # national annex: category E (psi0 1.0) and gamma overrides
    ne = cc.code_combinations(m, "en1990", live_category="E",
                              gamma_G_sup=1.2, seismic=False)
    assert len(ne) == 13 and _has(ne, DEAD=1.2, LIVE=1.5, WX=1.5)
    po = cc.code_combinations(m, "en1990", psi={"W": [0.5, 0.2, 0.0]},
                              seismic=False)
    assert _has(po, DEAD=1.35, LIVE=1.5, WX=0.75)


def test_en1990_610ab_sls_and_roof_rule():
    m = _combo_model()
    out = cc.code_combinations(m, "en1990", equation="6.10ab",
                               seismic=False, sls=True)
    uls = [c for c in out if c["name"].startswith("EN-ULS")]
    assert len(uls) == 17
    assert _has(uls, DEAD=1.35, LIVE=1.05, WX=0.9)            # 6.10a
    assert _has(uls, DEAD=1.1475, LIVE=1.5, WX=-0.9)          # 6.10b
    n = {p: len([c for c in out if c["name"].startswith(p)])
         for p in ("EN-SLSC", "EN-SLSF", "EN-SLSQ")}
    assert n == {"EN-SLSC": 9, "EN-SLSF": 6, "EN-SLSQ": 1}
    assert _has(out, DEAD=1.0, LIVE=0.5)                      # frequent
    assert _has(out, DEAD=1.0, LIVE=0.3)                      # quasi-perm.
    # A1.2.1(1): roof imposed load never with snow or wind
    m2 = _combo_model((("LR", "roof_live"), ("SN", "snow")))
    for c in cc.code_combinations(m2, "en1990"):
        f = c["factors"]
        if "LR" in f:
            assert not ({"SN", "WX", "WY"} & set(f)), c


def test_is456_and_is800_tables():
    m = _combo_model()
    o = cc.code_combinations(m, "is456")
    assert len(o) == 1 + 3 * 8
    assert _has(o, DEAD=1.5, LIVE=1.5)
    assert _has(o, DEAD=1.2, LIVE=1.2, WX=-1.2)
    assert _has(o, DEAD=1.5, EQY=1.5)
    assert _has(o, DEAD=0.9, EQX=-1.5)
    s = cc.code_combinations(m, "is456", sls=True)
    assert len(s) == 25 + 1 + 2 * 8
    assert _has(s, DEAD=1.0, LIVE=0.8, WY=0.8)
    o8 = cc.code_combinations(m, "is800")
    assert len(o8) == 1 + 4 * 8
    assert _has(o8, DEAD=1.2, LIVE=1.2, WX=0.6)
    assert _has(o8, DEAD=1.2, LIVE=1.2, EQX=-1.2)
    m2 = _combo_model((("LR", "roof_live"),))               # accompanying
    assert _has(cc.code_combinations(m2, "is800"), DEAD=1.5, LIVE=1.5,
                LR=1.05)
    assert _has(cc.code_combinations(m2, "is800"), DEAD=1.2, LIVE=1.2,
                LR=0.53, WX=1.2)


def test_nbcc2020_table():
    m = _combo_model()
    o = cc.code_combinations(m, "nbcc2020")
    # 1 + 2 x 4 (L + 0.4W) + 0 (no snow) + 2 x 4 (W + 0.5L) + 4 (E) = 21
    assert len(o) == 21
    assert _has(o, DEAD=1.4)
    assert _has(o, DEAD=1.25, LIVE=1.5, WX=0.4)
    assert _has(o, DEAD=0.9, LIVE=1.5, WY=-0.4)
    assert _has(o, DEAD=1.25, LIVE=0.5, WX=1.4)
    assert _has(o, DEAD=1.0, LIVE=0.5, EQX=-1.0)
    m2 = _combo_model((("SN", "snow"),))
    o2 = cc.code_combinations(m2, "nbcc2020", storage=True)
    # 1 + 2x(1+4) + 2x(1+4) + 2x4x2 + 4 = 41
    assert len(o2) == 41
    assert _has(o2, DEAD=1.25, LIVE=1.5, SN=1.0)
    assert _has(o2, DEAD=0.9, SN=1.5, LIVE=1.0)
    assert _has(o2, DEAD=1.25, WX=1.4, LIVE=1.0)               # storage
    assert _has(o2, DEAD=1.25, WX=1.4, SN=0.5)
    assert _has(o2, DEAD=1.0, EQY=1.0, LIVE=1.0, SN=0.25)


def test_asnzs1170_table():
    m = _combo_model((("SN", "snow"),))
    o = cc.code_combinations(m, "asnzs1170")
    # (a) 1 + (b) 1 + (d) 4 + (e) 4 + (f) 4 + (g) 1 = 15
    assert len(o) == 15
    assert _has(o, DEAD=1.35)
    assert _has(o, DEAD=1.2, LIVE=1.5)
    assert _has(o, DEAD=1.2, LIVE=0.4, WX=-1.0)
    assert _has(o, DEAD=0.9, WY=1.0)
    assert _has(o, DEAD=1.0, LIVE=0.3, EQX=1.0)
    assert _has(o, DEAD=1.2, SN=1.0, LIVE=0.4)
    st = cc.code_combinations(m, "asnzs1170", occupancy="storage", sls=True)
    assert _has(st, DEAD=1.0, LIVE=0.6, EQX=1.0)
    assert _has(st, DEAD=1.0, LIVE=1.0) and _has(st, DEAD=1.0, LIVE=0.6)


def test_apply_replace_rs_and_warnings():
    m = _combo_model((("TMP", "temperature"),))
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cc.apply_code_combinations(m, "is456")
    assert any("temperature" in str(x.message) for x in w)
    assert len([n for n in m.combos if n.startswith("IS456-")]) == 25
    m.patterns["EQX"].kind = "other"                     # EQX leaves E
    with pytest.warns(UserWarning):
        cc.apply_code_combinations(m, "is456")
    assert len([n for n in m.combos if n.startswith("IS456-")]) == 19
    assert "IS456-25" not in m.combos                    # stale removed
    m.validate()
    # RS cases join E without sign variants
    m2 = _combo_model()
    m2.add_rs_case("RSX", "X", [[0, 0.2], [1, 0.4]])
    o = cc.code_combinations(m2, "nbcc2020")
    assert _has(o, DEAD=1.0, LIVE=0.5, RSX=1.0)
    assert not _has(o, DEAD=1.0, LIVE=0.5, RSX=-1.0)
    assert len(cc.code_combinations(m2, "nbcc2020", include_rs=False)) == 21
    cc.apply_code_combinations(m2, "nbcc2020")
    m2.validate()
    empty = BuildingModel()
    with pytest.warns(UserWarning):
        assert cc.code_combinations(empty, "en1990") == []
    with pytest.raises(ValueError):
        cc.code_combinations(m, "bogus")
    with pytest.raises(ValueError):
        cc.code_combinations(m, "en1990", gamma_Q=-1)


# =========================================================================== #
# 3. live-load reduction
# =========================================================================== #
def test_llr_formulas_worked_examples():
    R = llr.reduction_factor
    # NBCC 4.1.5.8: B = 100 m^2 -> 0.3 + sqrt(0.098); A = 200 assembly
    assert R("nbcc2020", {}, 100.0, 3) == A(0.3 + math.sqrt(0.098))
    assert R("nbcc2020", {"occupancy": "assembly"}, 200.0, 3) == \
        A(0.5 + math.sqrt(0.1))
    assert R("nbcc2020", {}, 15.0, 1) == 1.0
    # EN 1991-1-1 Eq. 6.2 n = 5, psi0 0.7 -> 0.82; Eq. 6.1 A1 50 -> 0.7
    ec = llr.normalize_settings({"code": "ec1"})["params"]
    assert R("ec1", ec, 250.0, 5) == A(0.82)
    assert R("ec1", ec, 2.0 * 50.0, 2) == 1.0                   # n <= 2
    assert R("ec1", dict(ec, method="alpha_A"), 50.0, 1) == A(0.7)
    assert R("ec1", dict(ec, method="alpha_A", category="C"),
             1000.0, 1) == A(0.6)
    assert R("ec1", dict(ec, method="min"), 250.0, 5) == A(5 / 7 * 0.7 + 0.2)
    # IS 875-2 Table 3
    assert [R("is875", {}, 0, n) for n in (1, 2, 3, 4, 5, 10, 11)] == \
        [1.0, 0.9, 0.8, 0.7, 0.6, 0.6, 0.5]
    # ASCE 7-16: KLL At = 4*36 -> 0.25 + 4.57/12
    assert R("asce7_16", {"KLL": 4.0}, 36.0, 2) == A(0.25 + 4.57 / 12)
    assert R("asce7_16", {"KLL": 4.0}, 1e4, 1) == 0.5
    # user curve over area / floors
    cur = {"basis": "area", "curve": [[0, 1], [100, 0.8], [200, 0.6]]}
    assert R("user", llr.normalize_settings(
        {"code": "user", "params": cur})["params"], 150.0, 1) == A(0.7)
    assert R("user", {"basis": "floors", "curve": [[1, 1], [5, 0.6]]},
             0, 3) == A(0.8)
    for bad in ({"code": "x"}, {"code": "ec1", "params": {"psi0": 2}},
                {"code": "user", "params": {"curve": [[1, 1], [0, 1]]}},
                {"kinds": "live"}, {"apply_to": ["slabs"]}, {"foo": 1}):
        with pytest.raises(ValueError):
            llr.normalize_settings(bad)


def test_llr_column_factors_reuse_tributary_rule():
    m = quick_building(stories=4, bays_x=3, bays_y=3, bay_width_x=8,
                       bay_width_y=8)
    geo = live_load_reduction(m)
    f = llr.element_factors(m, {"code": "asce7_16"})["columns"]
    for uid, g in geo.items():
        assert f[uid]["R"] == A(g["R"]) and f[uid]["At"] == A(g["At"])
    f2 = llr.element_factors(m, {"code": "is875"})["columns"]
    assert f2["C1-B2"]["n"] == 4 and f2["C1-B2"]["R"] == A(0.7)
    assert f2["C4-B2"]["R"] == 1.0


@pytest.fixture(scope="module")
def solved():
    from skyframe.engine.opensees_engine import OpenSeesEngine
    m = quick_building(stories=4, bays_x=3, bays_y=3, bay_width_x=8,
                       bay_width_y=8)
    m.patterns["LIVE"].kind = "reducible_live"
    return m, OpenSeesEngine(m).run().to_dict()


def test_llr_applied_to_frame_output(solved):
    m, d = solved
    red = llr.apply_to_results(d, m, {"code": "nbcc2020"})
    assert red["live_cases"] == ["LIVE"] and not red["warnings"]
    R = red["factors"]["columns"]["C1-B2"]["R"]
    assert R == A(0.3 + math.sqrt(9.8 / 256.0))
    lv = d["cases"]["LIVE"]["member_forces"]["C1-B2"]
    assert red["cases"]["LIVE"]["member_forces"]["C1-B2"] == \
        A([R * v for v in lv])
    cb = "1.2D + 1.6L"
    q = d["combos"][cb]["member_forces"]["C1-B2"]
    want = [a + (R - 1) * 1.6 * b for a, b in zip(q, lv)]
    assert red["combos"][cb]["member_forces"]["C1-B2"] == A(want)
    st = red["combos"][cb]["member_stations"]["C1-B2"]
    q0 = d["combos"][cb]["member_stations"]["C1-B2"]
    l0 = d["cases"]["LIVE"]["member_stations"]["C1-B2"]
    assert st["N"][0] == A(q0["N"][0] + (R - 1) * 1.6 * l0["N"][0])
    assert "DEAD" not in red["cases"] and "EQX" not in red["cases"]
    # results untouched
    assert d["cases"]["LIVE"]["member_forces"]["C1-B2"] == lv
    # kinds given explicitly as ["live"] -> nothing reducible
    assert llr.apply_to_results(d, m, {"kinds": ["live"]})["live_cases"] \
        == []


def test_llr_tables(solved):
    m, d = solved
    keys = [t["key"] for t in tables.list_tables()]
    assert "live_load_reduction_factors" in keys
    assert "live_reduced_forces" in keys
    m.llr_settings = {"code": "is875"}
    try:
        t = tables.compute_table("live_load_reduction_factors", d, m)
        row = next(r for r in t["rows"] if r["name"] == "C1-B2")
        assert row["rll"] == A(0.7) and row["n"] == 4
        f = tables.compute_table("live_reduced_forces", d, m,
                                 cases=["LIVE"])
        r0 = next(r for r in f["rows"] if r["name"] == "C1-B2")
        st = d["cases"]["LIVE"]["member_stations"]["C1-B2"]
        assert r0["P"] == A(0.7 * st["N"][0])
        assert {r["case"] for r in f["rows"]} == {"LIVE"}
    finally:
        m.llr_settings = None


def test_llr_applied_to_piers():
    from skyframe.core.model import Material, ShellRegion, ShellSection
    from skyframe.core.model import NodalLoad
    from skyframe.engine.opensees_engine import OpenSeesEngine
    mdl = BuildingModel(name="pier")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("CONC", E=25e6, nu=0.2))
    mdl.shell_sections["SH"] = ShellSection("SH", "CONC", 0.2)
    mdl.set_stories([3.0, 3.0])
    mdl.shells.append(ShellRegion(
        "W1", "wall", "shell", "SH",
        [(0, 0, 0), (3, 0, 0), (3, 0, 6), (0, 0, 6)], mesh_size=0.5,
        story="Story2", pier="P1"))
    pl = mdl.pattern("L", "reducible_live")
    pd = mdl.pattern("D", "dead")
    for x in (0.0, 1.5, 3.0):
        pl.nodal_loads.append(NodalLoad((x, 0, 6), fz=-30.0))
        pd.nodal_loads.append(NodalLoad((x, 0, 6), fz=-50.0))
    mdl.add_case("L", {"L": 1.0})
    mdl.add_case("D", {"D": 1.0})
    mdl.add_combo("U", {"D": 1.2, "L": 1.6})
    d = OpenSeesEngine(mdl).run().to_dict()
    st = {"code": "nbcc2020", "pier_areas": {"P1": 50.0}}
    red = llr.apply_to_results(d, mdl, st)
    fp = red["factors"]["piers"]["P1"]
    assert fp["Story1"]["n"] == 2 and fp["Story1"]["At"] == A(100.0)
    R1 = 0.3 + math.sqrt(0.098)
    R2 = 0.3 + math.sqrt(9.8 / 50)
    assert (fp["Story1"]["R"], fp["Story2"]["R"]) == (A(R1), A(R2))
    pL = d["piers"]["L"]["P1"]["Story1"]["P"]
    assert pL == A(90.0, rel=1e-3)
    assert red["piers"]["L"]["P1"]["Story1"]["P"] == A(R1 * pL)
    pU = d["piers"]["U"]["P1"]["Story2"]["P"]
    pL2 = d["piers"]["L"]["P1"]["Story2"]["P"]
    assert red["piers"]["U"]["P1"]["Story2"]["P"] == \
        A(pU + (R2 - 1) * 1.6 * pL2)
    t = tables.compute_table("live_reduced_forces", d, mdl)
    assert any(r["element"] == "Pier" for r in t["rows"])


def test_llr_settings_round_trip_and_default_absent():
    m = quick_building(stories=2)
    assert "llr_settings" not in m.to_dict()
    m.llr_settings = {"code": "ec1", "params": {"psi0": 0.6}}
    back = BuildingModel.from_dict(m.to_dict())
    assert back.llr_settings == m.llr_settings
    bad = m.to_dict()
    bad["llr_settings"] = {"code": "nope"}
    with pytest.raises(ValueError):
        BuildingModel.from_dict(bad)


# =========================================================================== #
# 4. API
# =========================================================================== #
@pytest.fixture()
def client():
    from skyframe.api.server import create_app
    app = create_app()
    app.testing = True
    with app.test_client() as c:
        c.post("/api/model/quick", json={"stories": 2})
        yield c


def test_api_spectra_and_combos(client):
    cat = client.get("/api/codes/spectra").get_json()
    assert len(cat["codes"]) == 9 and len(cat["mprs_periods"]) == 22
    pv = client.post("/api/function/spectrum-code/preview",
                     json={"code": "gb50011", "params": {}}).get_json()
    assert pv["summary"]["plateau"] == A(0.16)
    r = client.post("/api/function/spectrum-code",
                    json={"name": "GB", "code": "gb50011",
                          "params": {"intensity": "7"}})
    assert r.status_code == 200
    assert r.get_json()["spectrum_functions"]["GB"]["source"]["params"][
        "intensity"] == "7"
    assert client.post("/api/function/spectrum-code",
                       json={"name": "X", "code": "zz"}).status_code == 400
    cat = client.get("/api/codes/combos").get_json()
    assert "superdead" in cat["pattern_kinds"]
    assert {s["standard"] for s in cat["standards"]} == set(cc.STANDARDS)
    pv = client.post("/api/combos/code/preview",
                     json={"standard": "is456"}).get_json()
    assert len(pv["combos"]) == 1 + 3 * 4            # D, L, +-EQX, +-EQY
    r = client.post("/api/combos/code", json={"standard": "nbcc2020"})
    assert r.status_code == 200
    assert "NBCC-01" in r.get_json()["combos"]
    assert client.post("/api/combos/code",
                       json={"standard": "en1990",
                             "params": {"xi": 2}}).status_code == 400


def test_api_live_reduction(client):
    f = client.post("/api/live-reduction/factors",
                    json={"settings": {"code": "is875"}}).get_json()
    assert f["code"] == "is875" and f["live_cases"] == ["LIVE"]
    assert client.post("/api/live-reduction/factors",
                       json={"settings": {"code": "q"}}).status_code == 400
    r = client.post("/api/results/live-reduced",
                    json={"settings": {"code": "is875"}}).get_json()
    assert r["live_cases"] == ["LIVE"] and "LIVE" in r["cases"]


def test_api_preview_uses_draft_model_without_storing(client):
    m = _combo_model((("SN", "snow"),)).to_dict()
    before = client.get("/api/model").get_json()
    r = client.post("/api/combos/code/preview",
                    json={"standard": "asnzs1170", "model": m}).get_json()
    assert len(r["combos"]) == 15
    assert client.get("/api/model").get_json() == before
    f = client.post("/api/live-reduction/factors",
                    json={"model": m, "settings": None}).get_json()
    assert f["code"] == "asce7_16" and f["columns"]


def test_e2k_export_writes_etabs_pattern_types():
    from skyframe.io.e2k_write import PATTERN_TYPES
    assert PATTERN_TYPES["superdead"] == "Super Dead"
    assert PATTERN_TYPES["reducible_live"] == "Reducible Live"
    assert PATTERN_TYPES["dead"] == "Dead"                  # legacy kept


def test_frontend_mock_catalogue_in_sync():
    """js/mock_loadcodes_data.js is a static copy of the catalogues."""
    import os
    import re
    p = os.path.join(os.path.dirname(__file__), "..", "skyframe", "api",
                     "web", "js", "mock_loadcodes_data.js")
    src = open(p, encoding="utf-8").read()
    spec = re.search(r"LC_SPECTRA_CATALOGUE = (.*?);\n", src).group(1)
    comb = re.search(r"LC_COMBO_CATALOGUE = (.*?);\n", src).group(1)
    assert json.loads(spec) == json.loads(json.dumps(sc.catalogue()))
    assert json.loads(comb) == json.loads(json.dumps(cc.catalogue()))
