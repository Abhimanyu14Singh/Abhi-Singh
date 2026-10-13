"""Benchmark pack (CONTRACT "Benchmark pack: CSI ETABS verification
examples"): every Tier-1, Tier-2 and gap case must sit inside CSI's
acceptance band against its independent reference; plus the ASCE 7-02 /
UBC 97 wind generators (hand-verified), the multi-tower auto-load advisor
warning, the LLR-never-in-analysis guarantee and the report writers.

No tolerance here is a free parameter: the band is CSI's (exact /
<= 5 % global / <= 10 % internal); cases whose modelling needs a choice
document it in the case module docstring ([derived] / [assumed]).
"""

import json
import math

import pytest

from skyframe.benchmarks import GAP, TIER1, run_case
from skyframe.benchmarks._core import Row, num, pct
from skyframe.benchmarks.report import totals, write_html, write_json

pytestmark = pytest.mark.filterwarnings("ignore")

TIER2 = ("EX02", "EX03", "EX05", "EX07")


@pytest.fixture(scope="module")
def results():
    return {cid: run_case(cid) for cid in TIER1 + TIER2 + GAP}


# --------------------------------------------------------------------------- #
# rating rules
# --------------------------------------------------------------------------- #
def test_published_number_precision():
    assert num("1,687.50") == (1687.5, pytest.approx(0.005))
    assert num("0.0201")[1] == pytest.approx(5e-5)
    assert num("2.40000e+02") == (240.0, pytest.approx(5e-4))
    assert num(None) == (None, None)
    assert pct(101.0, 100.0) == pytest.approx(1.0)
    assert pct(1.0, 0.0) is None


@pytest.mark.parametrize("value,cls,rating", [
    (1687.504, "global", "exact"), (1687.51, "global", "acceptable"),
    (1687.5 * 1.049, "global", "acceptable"),
    (1687.5 * 1.051, "global", "unacceptable"),
    (1687.5 * 1.099, "internal", "acceptable"),
    (1687.5 * 0.899, "internal", "unacceptable")])
def test_csi_rating(value, cls, rating):
    r = Row("M", "", "kip-in", "1,687.50", "x", None, value, cls=cls).rate()
    assert r.rating == rating


def test_zero_reference_exact_only_within_precision():
    assert Row("M", "", "", "0.00", "x", None, 0.004).rate().rating == \
        "exact"
    assert Row("M", "", "", "0.00", "x", None, 0.006).rate().rating == \
        "unacceptable"


# --------------------------------------------------------------------------- #
# the pack
# --------------------------------------------------------------------------- #
def _all_rows():
    for cid in TIER1 + TIER2 + GAP:
        yield cid


@pytest.mark.parametrize("cid", list(_all_rows()))
def test_case_within_csi_band(results, cid):
    r = results[cid]
    assert r.status in ("run", "partial"), r.error
    assert r.rows, cid
    bad = [(x.quantity, x.location, x.independent, x.skyframe, x.pct_ind)
           for x in r.rows if x.rating not in ("exact", "acceptable")]
    assert not bad, bad


def test_expected_counts_and_exactness(results):
    # Tier-1 value counts as published (Ex 06 adds 3 supplementary rows)
    n = {cid: len(results[cid].rows) for cid in TIER1}
    assert n == {"EX01": 20, "EX04": 12, "EX06": 6, "EX08": 5, "EX15": 30}
    # closed-form statics / exact eigen problems are exact to the printed
    # digits; the patch tests, CM/CR and Fpx are exact too
    for cid in ("EX01", "EX02", "EX04", "GAP-PATCH", "GAP-DIAPHRAGM",
                "GAP-FPX"):
        assert {x.rating for x in results[cid].rows} == {"exact"}, cid


def test_ex06_mass_model_difference_is_the_reference_gap(results):
    rows = results["EX06"].rows
    etabs_style = [x for x in rows if x.group == "ETABS mass model"]
    # SkyFrame's ETABS-style model reproduces ETABS (< 0.002 %)
    assert all(abs(x.pct_etabs) < 2e-3 for x in etabs_style)
    # the references' joint-mass model reproduces Bathe & Wilson (< 5e-4 %)
    ref_style = [x for x in rows if x.group == "Reference mass model"]
    assert all(abs(x.pct_ind) < 5e-4 for x in ref_style)


def test_ex15_reports_mesh_and_alternatives(results):
    r = results["EX15"]
    assert "10 in" in r.mesh
    assert all(x.note for x in r.rows)
    # everything not exact to the printed digits is inside 5 % (the
    # 3-story E-wall rotation 0.0005 rad is exact at its 1-digit precision)
    worst = max(abs(x.pct_ind) for x in r.rows if x.rating != "exact")
    assert worst < 5.0


def test_ex03_story_shears_close_on_base(results):
    rows = results["EX03"].rows
    for x in rows:
        if x.location == "1st" and "base reaction" in x.note:
            base = float(x.note.split()[-2])
            # equilibrium: support reactions (element forces of a frame with
            # 1e3 "rigid" beams) vs the applied story shear
            assert base == pytest.approx(x.skyframe, rel=1e-6)
    seismic = [x for x in rows if "seismic" in x.quantity]
    assert all(abs(x.pct_etabs) < 0.05 for x in seismic)


def test_blocked_cases_list_missing_items():
    for cid in ("EX09", "EX10", "EX11", "EX12", "EX13", "EX14"):
        r = run_case(cid)
        assert r.status == "blocked" and r.blocked and not r.rows


def test_report_writers(results, tmp_path):
    res = list(results.values()) + [run_case("EX12")]
    sc = write_json(res, tmp_path / "s.json")
    d = json.loads((tmp_path / "s.json").read_text())
    assert d["totals"] == totals(res) == sc["totals"]
    assert d["totals"]["unacceptable"] == 0
    write_html(res, tmp_path / "r.html")
    html = (tmp_path / "r.html").read_text()
    for s in ("Scorecard", "Known differences", "Methodology",
              "Licence note", 'id="EX15"', "prefers-color-scheme",
              "<svg"):
        assert s in html


# --------------------------------------------------------------------------- #
# ASCE 7-02 ELF and UBC 97 wind generators (hand-verified)
# --------------------------------------------------------------------------- #
def _frame(n=3, h=4.0):
    from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                     PointSupport)
    m = BuildingModel(name="g")
    m.add_material(Material("C", E=25e6, nu=0.2))
    m.add_section(FrameSection.rectangular("S", "C", 0.4, 0.4))
    m.set_stories([h] * n)
    for k in range(n):
        for x in (0.0, 6.0):
            m.add_member("column", "S", (x, 0, h * k), (x, 0, h * (k + 1)),
                         story=f"Story{k + 1}")
        m.add_member("beam", "S", (0, 0, h * (k + 1)), (6, 0, h * (k + 1)),
                     story=f"Story{k + 1}")
    for x in (0.0, 6.0):
        m.supports.append(PointSupport((x, 0, 0), (1,) * 6))
    return m


def test_asce7_02_hand_calculation():
    from skyframe.core import autolateral as al
    m = _frame()
    w = {"Story1": 1000.0, "Story2": 1000.0, "Story3": 1000.0}
    s = al.compute(m, "asce7_02", Ss=1.0, S1=0.4, R=8.0, I=1.0, site="C",
                   structure="steel_mrf", T=0.6, weights=w)
    # Table 9.4.1.2.4a/b site C: Fa(1.0) = 1.0, Fv(0.4) = 1.4
    SDS, SD1 = 2 / 3 * 1.0, 2 / 3 * 0.56
    Ta = 0.0724 * 12.0 ** 0.8                   # steel MRF 0.028 ft^0.8
    Cu = 1.3 - (SD1 - 0.3) / 0.1 * 0.1          # Table 9.5.5.3.1, linear
    assert s["Ta"] == pytest.approx(Ta) and s["Cu"] == pytest.approx(Cu)
    assert s["T"] == pytest.approx(0.6)          # < Cu Ta
    Cs = min(SDS / 8.0, SD1 / (0.6 * 8.0))       # SD1/(T R/I) governs
    assert s["Cs"] == pytest.approx(Cs) and Cs == pytest.approx(SD1 / 4.8)
    assert s["V"] == pytest.approx(Cs * 3000.0)
    k = 1.0 + (0.6 - 0.5) / 2.0
    hk = [4.0 ** k, 8.0 ** k, 12.0 ** k]
    for row, x in zip(s["stories"], hk):
        assert row["F"] == pytest.approx(s["V"] * x / sum(hk))
    # period cap and the S1 >= 0.6 minimum
    s2 = al.compute(m, "asce7_02", Ss=1.5, S1=0.75, R=8.0, I=1.0,
                    site="D", T=5.0, structure="steel_mrf", weights=w)
    assert s2["T"] == pytest.approx(s2["Cu"] * s2["Ta"])
    assert s2["Cu"] == pytest.approx(1.2)
    assert s2["Cs_min"] == pytest.approx(0.5 * 0.75 / 8.0)


def test_ubc97_wind_hand_calculation_example3_frame():
    """Example 3 frame (3 x 12 ft, 20 ft wide, 100 mph, exposure B):
    windward Ce 0.62 / 0.71 / 0.808 at 12 / 24 / 36 ft, leeward Ce(36 ft)
    0.808; qs = 25.6 psf; forces in lb by hand."""
    from skyframe.core import autolateral as al
    FT, LB = 0.3048, 4.4482216152605e-3
    m = _frame(3, 12 * FT)
    s = al.compute(m, "ubc97_wind", V=100 * 0.44704, exposure="B",
                   width=20 * FT)
    qs = 25.6
    hand = [(0.8 * 0.62 + 0.5 * 0.808) * qs * 240,
            (0.8 * 0.71 + 0.5 * 0.808) * qs * 240,
            (0.8 * 0.808 + 0.5 * 0.808) * qs * 120]
    for row, f in zip(s["stories"], hand):
        assert row["F"] == pytest.approx(f * LB, rel=1e-6)
    assert s["qs"] == pytest.approx(qs * 47.880259e-3, rel=1e-6)
    pat = al.generate(m, "ubc97_wind", "W", V=44.704, width=6.096)
    assert pat.kind == "wind" and len(pat.story_forces) == 3
    assert al.generate(m, "asce7_02", "E", Ss=1, S1=0.4, R=8).kind == \
        "quake"
    with pytest.raises(ValueError):
        al.compute(m, "ubc97_wind", V=40.0, exposure="A")


def test_new_codes_registered_and_api_params():
    from skyframe.core import autolateral as al
    assert {"asce7_02", "ubc97_wind"} <= set(al.CODES)
    assert "ubc97_wind" in al.WIND_CODES
    code, name, ecc, p = al.params_from_body(
        {"code": "asce7_02", "Ss": 1.0, "S1": 0.4, "R": 8})
    assert code == "asce7_02" and p["R"] == 8
    with pytest.raises(ValueError):
        al.params_from_body({"code": "ubc97_wind", "V": 40, "bogus": 1})


# --------------------------------------------------------------------------- #
# Welcome to ETABS findings: multi-tower advisor, LLR never in analysis
# --------------------------------------------------------------------------- #
def test_advisor_warns_auto_lateral_on_multi_tower():
    from skyframe.core import autolateral as al
    from skyframe.core.accuracy_advisor import accuracy_advice
    m = _frame()
    al.generate(m, "user_coefficient", "EQX", C=0.1,
                weights={"Story1": 1.0, "Story2": 1.0, "Story3": 1.0})
    codes = {a["code"] for a in accuracy_advice(m)["advice"]}
    assert "AUTO_LATERAL_MULTI_TOWER" not in codes
    m.towers = [{"name": "T1", "grid_system": "G1", "base_elevation": 0.0},
                {"name": "T2", "grid_system": "G1", "base_elevation": 0.0}]
    adv = {a["code"]: a for a in accuracy_advice(m)["advice"]}
    a = adv["AUTO_LATERAL_MULTI_TOWER"]
    assert a["severity"] == "warning" and a["objects"] == ["EQX"]
    assert a["refs"] == [{"type": "pattern", "uid": "EQX"}]


def test_raw_analysis_output_never_live_load_reduced():
    """Welcome to ETABS v23 p. 4-6: live loads are never reduced in the
    basic analysis output.  Setting live-load reduction changes nothing in
    the solved results; only the separate LLR copies are reduced."""
    from skyframe.core import llr_codes as llr
    from skyframe.core.builder import quick_building
    from skyframe.engine.opensees_engine import OpenSeesEngine

    def solve(settings):
        m = quick_building(stories=4, bays_x=3, bays_y=3, bay_width_x=8,
                           bay_width_y=8)
        m.patterns["LIVE"].kind = "reducible_live"
        m.llr_settings = settings
        m.num_modes = 0
        return m, OpenSeesEngine(m).run().to_dict()

    m0, d0 = solve(None)
    m1, d1 = solve({"code": "asce7_16"})
    for c in ("LIVE",):
        assert d1["cases"][c]["member_forces"] == \
            d0["cases"][c]["member_forces"]
        assert d1["cases"][c]["reactions"] == d0["cases"][c]["reactions"]
    for cb in d0["combos"]:
        assert d1["combos"][cb]["member_forces"] == \
            d0["combos"][cb]["member_forces"]
    red = llr.apply_to_results(d1, m1, m1.llr_settings)
    R = red["factors"]["columns"]["C1-B2"]["R"]
    assert R < 1.0
    raw = d1["cases"]["LIVE"]["member_forces"]["C1-B2"]
    assert red["cases"]["LIVE"]["member_forces"]["C1-B2"] == \
        pytest.approx([R * v for v in raw])
    assert d1["cases"]["LIVE"]["member_forces"]["C1-B2"] == raw
