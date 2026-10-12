"""Wave C: live-load reduction for beams, static seismic eccentricity
overrides per story / diaphragm, ASCE 7 §12.10 diaphragm design forces
Fpx (CONTRACT "Live-load reduction for beams", "Static seismic
eccentricity overrides", "Diaphragm design forces (ASCE 7 12.10)").

Every expected number is hand-calculated in the test.  Units kN, m."""

import copy
import math
import warnings

import pytest

from skyframe import quick_building
from skyframe.core import fpx as FPX
from skyframe.core import llr_codes as llr
from skyframe.core import static_ecc as SE
from skyframe.core import tables
from skyframe.core.model import BuildingModel, LoadPattern, StoryForce
from skyframe.engine.opensees_engine import OpenSeesEngine

A = pytest.approx


# --------------------------------------------------------------------------- #
# live-load reduction for beams
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def frame():
    m = quick_building(stories=2, bays_x=2, bays_y=2, bay_width_x=6,
                       bay_width_y=6)
    m.patterns["LIVE"].kind = "reducible_live"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        d = OpenSeesEngine(m).run().to_dict()
    return m, d


def test_beam_tributary_half_spacing(frame):
    """6 m x 6 m bays: interior beam At = 6 x (3 + 3) = 36 m^2, edge beam
    6 x 3 = 18 m^2."""
    m, _ = frame
    f = llr.element_factors(m, {"apply_to": ["beams"]})
    assert "columns" in f and f["columns"] == {}
    b = f["beams"]
    assert b["BX1-A2"]["At"] == A(36.0) and b["BX1-A1"]["At"] == A(18.0)
    assert b["BY1-B1"]["At"] == A(36.0) and b["BY1-A1"]["At"] == A(18.0)
    # ASCE 7-16, KLL = 2: interior 0.25 + 4.57/sqrt(72) = 0.788580...
    assert b["BX1-A2"]["R"] == A(0.25 + 4.57 / math.sqrt(72.0))
    assert b["BX1-A2"]["R"] == A(0.788580, rel=1e-6)
    # edge: KLL At = 36 < 37.16 -> no reduction
    assert b["BX1-A1"]["R"] == 1.0
    # KLL = 1 ("all other members"): 0.25 + 4.57/6 = 1.0117 -> 1.0 ;
    # beam_areas override 200 m^2 with KLL 2 -> max(0.5, 0.25 + 4.57/20)
    f2 = llr.element_factors(m, {"apply_to": ["beams"],
                                 "beam_params": {"KLL": 1.0},
                                 "beam_areas": {"BX1-A1": 200.0}})
    assert f2["beams"]["BX1-A2"]["R"] == 1.0
    assert f2["beams"]["BX1-A1"]["R"] == A(0.25 + 4.57 / math.sqrt(200.0))
    f3 = llr.element_factors(m, {"apply_to": ["beams"],
                                 "beam_areas": {"BX1-A1": 2000.0}})
    assert f3["beams"]["BX1-A1"]["R"] == 0.5          # one-floor floor


@pytest.mark.parametrize("code, params, want", [
    ("nbcc2020", {}, 0.3 + math.sqrt(9.8 / 36.0)),          # B = 36 > 20
    ("ec1", {}, 5.0 / 7.0 * 0.7 + 10.0 / 36.0),             # alpha_A
    ("is875", {}, 1.0),                                      # < 50 m^2
    ("user", {"basis": "area", "curve": [[0, 1], [72, 0.6]]}, 0.8),
])
def test_beam_factor_codes(frame, code, params, want):
    m, _ = frame
    f = llr.element_factors(m, {"code": code, "params": params,
                                "apply_to": ["beams"]})
    assert f["beams"]["BX1-A2"]["R"] == A(want)


def test_is875_beam_rule():
    from skyframe.core.llr_beams import beam_factor
    assert beam_factor("is875", {}, {}, 49.0) == 1.0
    assert beam_factor("is875", {}, {}, 120.0) == A(0.9)    # 2 x 50 m^2
    assert beam_factor("is875", {}, {}, 900.0) == A(0.75)   # capped 25 %


def test_beams_reduced_in_results_and_tables(frame):
    m, d = frame
    st = {"code": "asce7_16", "apply_to": ["columns", "beams"]}
    red = llr.apply_to_results(d, m, st)
    R = red["factors"]["beams"]["BX1-A2"]["R"]
    lv = d["cases"]["LIVE"]["member_forces"]["BX1-A2"]
    assert red["cases"]["LIVE"]["member_forces"]["BX1-A2"] == A(
        [R * v for v in lv])
    l3 = d["cases"]["LIVE"]["member_stations"]["BX1-A2"]["M3"]
    assert red["cases"]["LIVE"]["member_stations"]["BX1-A2"]["M3"] == A(
        [R * v for v in l3])
    # edge beam R = 1 -> not listed
    assert "BX1-A1" not in red["cases"]["LIVE"]["member_forces"]
    m.llr_settings = st
    try:
        t = tables.compute_table("live_load_reduction_factors", d, m)
        row = next(r for r in t["rows"] if r["name"] == "BX1-A2")
        assert row["element"] == "Beam" and row["rll"] == A(R)
        assert row["at"] == A(36.0) and row["n"] == 1
        f = tables.compute_table("live_reduced_forces", d, m,
                                 cases=["LIVE"])
        rows = [r for r in f["rows"] if r["name"] == "BX1-A2"]
        assert len(rows) == len(l3)                # every station
        assert rows[5]["M3"] == A(R * l3[5])
    finally:
        m.llr_settings = None


def test_beams_default_off_legacy_shape(frame):
    m, d = frame
    f = llr.element_factors(m, None)
    assert set(f) == {"code", "columns", "piers", "warnings"}
    red = llr.apply_to_results(d, m, None)
    assert set(red["factors"]) == {"columns", "piers"}


# --------------------------------------------------------------------------- #
# static eccentricity overrides
# --------------------------------------------------------------------------- #
def _eq_model(rigid=True):
    m = quick_building(stories=2, bays_x=2, bays_y=1, bay_width_x=6,
                       bay_width_y=5)
    m.rigid_diaphragms = rigid
    p = LoadPattern("EX", "quake")
    p.story_forces = [StoryForce(m.stories[0].name, fx=100.0),
                      StoryForce(m.stories[1].name, fx=200.0)]
    m.patterns["EX"] = p
    m.add_case("EX", {"EX": 1.0})
    return m


def _mz(m):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return OpenSeesEngine(m).run_static("EX").base["MZ"]


def test_story_ratio_and_length_overrides_rigid():
    """Plan 12 x 5 m.  Torques Mz = F ecc Ly: ecc 0.05 -> 300 x 0.25 =
    75; override S1 ratio 0.10 (100 x 0.5 = 50) and S2 length 1.2 m
    (200 x 1.2 = 240) -> 290; the "- ecc" variant flips both."""
    m0 = _eq_model()
    lx, ly = m0.plan_extents()
    assert (lx, ly) == (12.0, 5.0)
    base = _mz(m0)
    m1 = _eq_model()
    m1.patterns["EX"].accidental_torsion = True
    assert _mz(m1) - base == A(-(100 + 200) * 0.05 * 5.0, rel=1e-9)
    s1, s2 = m1.stories[0].name, m1.stories[1].name
    m1.patterns["EX"].ecc_overrides = {s1: 0.10, s2: {"length": 1.2}}
    m1.validate()
    assert _mz(m1) - base == A(-(100 * 0.10 * 5.0 + 200 * 1.2), rel=1e-9)
    m1.patterns["EX"].ecc = -0.05
    assert _mz(m1) - base == A(+(100 * 0.10 * 5.0 + 200 * 1.2), rel=1e-9)
    rows = SE.story_torque_table(m1, m1.patterns["EX"])
    assert rows[1]["mz_x"] == A(-240.0)


def test_override_needs_accidental_torsion_and_identity():
    m0 = _eq_model()
    base = _mz(m0)
    m1 = _eq_model()
    m1.patterns["EX"].ecc_overrides = {m1.stories[0].name: 0.2}
    assert _mz(m1) == A(base, rel=1e-12)          # torsion off: no effect
    m2 = _eq_model()
    m2.patterns["EX"].accidental_torsion = True
    ref = _mz(m2)
    m2.patterns["EX"].ecc_overrides = {s.name: 0.05 for s in m2.stories}
    assert _mz(m2) == A(ref, rel=1e-12)           # same ratio: identity


def test_semi_rigid_slab_override():
    """Semi-rigid (no rigid diaphragm) story with a meshed slab: the
    antisymmetric force-couple field realizes exactly Mz = F e."""
    from skyframe.core.templates import build_template
    m = build_template("two_way_beams", {"stories": 1})[0]
    m.rigid_diaphragms = False
    st = m.stories[0].name
    m.patterns["EX"] = LoadPattern("EX", "quake")
    m.patterns["EX"].story_forces = [StoryForce(st, fx=100.0)]
    m.add_case("EX", {"EX": 1.0})
    base = _mz(m)
    m.patterns["EX"].accidental_torsion = True
    m.patterns["EX"].ecc_overrides = {st: {"length": 2.0}}
    assert _mz(m) - base == A(-200.0, rel=1e-7)


def test_named_diaphragm_override():
    """Two named rigid diaphragms on one story: only DA's torque changes;
    the split weights recovered from the measured torques equal the
    diaphragm masses (lateral-mass split of the story force)."""
    from tests.test_diaphragms_mass import _two
    m = _two()
    m.patterns["EX"] = LoadPattern("EX", "quake")
    m.patterns["EX"].story_forces = [StoryForce("S1", fx=100.0)]
    m.add_case("EX", {"EX": 1.0})
    base = _mz(m)
    m.patterns["EX"].accidental_torsion = True
    d1 = _mz(m) - base
    m.patterns["EX"].ecc_overrides = {"S1/DA": {"length": 1.5}}
    m.validate()
    d2 = _mz(m) - base
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        dia = OpenSeesEngine(m).run().diaphragms["stories"]["S1"]
    mA, mB = dia["DA"]["mass"], dia["DB"]["mass"]
    gxA = 100.0 * mA / (mA + mB)
    LyA, LyB = dia["DA"]["extent_y"], dia["DB"]["extent_y"]
    gxB = 100.0 - gxA
    assert d1 == A(-(gxA * 0.05 * LyA + gxB * 0.05 * LyB), rel=1e-7)
    assert d2 == A(-(gxA * 1.5 + gxB * 0.05 * LyB), rel=1e-7)


@pytest.mark.parametrize("ov, msg", [
    ({"NOPE": 0.1}, "unknown story"),
    ({"S1/ZZ": 0.1}, "unknown story"),
    ({"S1": 0.9}, r"\[0, 0.5\]"),
    ({"S1": {"length": -1}}, ">= 0"),
    ({"S1": "x"}, "ratio or"),
])
def test_override_validation(ov, msg):
    m = _eq_model()
    ov = {k.replace("S1", m.stories[0].name): v for k, v in ov.items()}
    m.patterns["EX"].ecc_overrides = ov
    with pytest.raises(ValueError, match=msg):
        m.validate()


def test_override_json_round_trip():
    m = _eq_model()
    d0 = m.to_dict()
    assert "ecc_overrides" not in d0["patterns"]["EX"]
    m.patterns["EX"].ecc_overrides = {m.stories[0].name: {"length": 1.0}}
    d = m.to_dict()
    back = BuildingModel.from_dict(copy.deepcopy(d))
    assert back.to_dict() == d


def test_ecc_overrides_db_table():
    from skyframe.core import modeltables as MT
    m = _eq_model()
    s1 = m.stories[0].name
    m.patterns["EX"].ecc_overrides = {s1: 0.1, f"{s1}/D1": {"length": 2}}
    d = m.to_dict()
    t = MT.table("load_pattern_ecc_overrides")
    rows = t.rows(d)
    assert len(rows) == 2 and rows[1]["diaphragm"] == "D1"
    assert rows[1]["length"] == 2 and rows[1]["ratio"] is None
    d2 = copy.deepcopy(d)
    errs = []
    t.apply(d2, rows, errs)
    assert not errs and d2 == d
    t.apply(d2, rows[:1], errs)
    assert d2["patterns"]["EX"]["ecc_overrides"] == {s1: 0.1}
    t.apply(d2, [], errs)
    assert "ecc_overrides" not in d2["patterns"]["EX"]
    bad = [{"pattern": "EX", "story": s1, "ratio": 0.1, "length": 1.0}]
    errs = []
    t.apply(d2, bad, errs)
    assert errs


# --------------------------------------------------------------------------- #
# ASCE 7 12.10 Fpx
# --------------------------------------------------------------------------- #
def _fpx_model():
    m = BuildingModel(name="fpx")
    m.set_stories([3.0, 3.0, 3.0], ["L1", "L2", "L3"])
    p = LoadPattern("EQX", "quake")
    p.story_forces = [StoryForce("L1", fx=50.0), StoryForce("L2", fx=100.0),
                      StoryForce("L3", fx=200.0)]
    m.patterns["EQX"] = p
    return m


W = {"L1": 1000.0, "L2": 1000.0, "L3": 800.0}


def test_fpx_hand_example_12_10_1():
    """SDS = 0.5, Ie = 1 -> limits 0.1 w / 0.2 w.
    L3: 200/800 x 800 = 200 > 0.2 x 800 = 160  -> 160 (max)
    L2: 300/1800 x 1000 = 166.667               -> 166.667 (12.10-1)
    L1: 350/2800 x 1000 = 125                   -> 125 (12.10-1)"""
    m = _fpx_model()
    r = FPX.compute(m, "EQX", SDS=0.5, Ie=1.0, weights=W)
    rows = {s["story"]: s for s in r["stories"]}
    assert [s["story"] for s in r["stories"]] == ["L3", "L2", "L1"]
    assert rows["L3"]["Fpx_eq"] == A(200.0)
    assert rows["L3"]["Fpx"] == A(160.0) and rows["L3"]["governs"] == "max"
    assert rows["L2"]["Fpx"] == A(300.0 / 1800.0 * 1000.0)
    assert rows["L2"]["governs"] == "12.10-1"
    assert rows["L1"]["Fpx"] == A(125.0)
    assert rows["L1"]["Fpx_min"] == A(100.0)
    # SDS = 1.0 -> minimum 0.2 w governs L1 (125 < 200), L2 (166.7 < 200)
    r2 = FPX.compute(m, "EQX", SDS=1.0, Ie=1.0, weights=W)
    rows2 = {s["story"]: s for s in r2["stories"]}
    assert rows2["L1"]["Fpx"] == A(200.0) and rows2["L1"]["governs"] == "min"
    assert rows2["L3"]["Fpx"] == A(200.0)          # = eq (min 160, max 320)
    # wpx override (e.g. a partial diaphragm): L2 wpx 600 -> 300/1800*600
    r3 = FPX.compute(m, "EQX", SDS=0.5, Ie=1.0, weights=W,
                     wpx={"L2": 600.0})
    assert {s["story"]: s for s in r3["stories"]}["L2"]["Fpx"] == A(100.0)


def test_fpx_with_elf_pattern_weights():
    """With the ELF generator: weights from the story masses and F_i from
    the pattern; the top level Fpx_eq = F_n / w_n x w_n = F_n."""
    from skyframe.core.codes import asce7_elf
    m = quick_building(stories=3, bays_x=2, bays_y=1)
    asce7_elf(m, SDS=1.0, SD1=0.4, R=8.0, Ie=1.0, name="ELFX")
    r = FPX.compute(m, "ELFX", SDS=1.0, Ie=1.0)
    top = r["stories"][0]
    assert top["Fpx_eq"] == A(top["F"], rel=1e-12)
    w = {s.name: m.compute_story_masses()[s.name] * 9.80665
         for s in m.stories}
    assert top["w"] == A(w[top["story"]], rel=1e-9)


def test_fpx_alternative_12_10_3():
    """N = 3, zs = 0.7, Cs = 0.1, Omega0 = 3, SDS = 1, SD1 = 0.6, Ie = 1,
    Rs = 2:  Gm1 = 1 + 0.35 (2/3) = 1.23333, Gm2 = 0.9 x 0.7 x 4/9 = 0.28,
    Cs2 = min(0.7, 1.0, 10) = 0.7, Cp0 = 0.4, Cpi = max(0.4, 0.333) = 0.4,
    Cpn = sqrt(0.37^2 + 0.196^2) = 0.418708; top Fpx = 0.418708/2 x 800 =
    167.483 (>= 0.2 x 800 = 160); L1/L2 (z <= 0.8 hn): 0.4/2 x 1000 = 200."""
    m = _fpx_model()
    r = FPX.compute(m, "EQX", SDS=1.0, Ie=1.0, weights=W, method="12.10.3",
                    Rs=2.0, Cs=0.1, Omega0=3.0, zs=0.7, SD1=0.6)
    a = r["alternative"]
    assert a["Gm1"] == A(1.0 + 0.35 * 2.0 / 3.0)
    assert a["Gm2"] == A(0.28)
    assert a["Cs2"] == A(0.7)
    assert a["Cpn"] == A(math.sqrt(0.37 ** 2 + 0.196 ** 2))
    rows = {s["story"]: s for s in r["stories"]}
    assert rows["L3"]["Fpx"] == A(math.sqrt(0.37 ** 2 + 0.196 ** 2) / 2
                                  * 800.0)
    assert rows["L2"]["Fpx"] == A(200.0) and rows["L1"]["Fpx"] == A(200.0)


def test_fpx_generate_patterns_and_table():
    m = _fpx_model()
    m.patterns["EQX"].accidental_torsion = True
    names = FPX.generate_patterns(m, "EQX", 0.5, 1.0, weights=W)
    assert names == ["EQX_Fpx_L3", "EQX_Fpx_L2", "EQX_Fpx_L1"]
    p = m.patterns["EQX_Fpx_L2"]
    assert p.kind == "quake" and p.accidental_torsion is True
    assert [(s.story, s.fx, s.fy) for s in p.story_forces] == [
        ("L2", A(300.0 / 1.8), 0.0)]
    m.fpx_settings = {"SDS": 0.5, "Ie": 1.0, "patterns": ["EQX"],
                      "weights": W}
    m.validate()
    t = tables.compute_table("diaphragm_design_forces", {}, m)
    assert [r["story"] for r in t["rows"]] == ["L3", "L2", "L1"]
    assert t["rows"][0]["Fpx"] == A(160.0)
    d = m.to_dict()
    assert d["fpx_settings"]["SDS"] == 0.5
    assert BuildingModel.from_dict(d).to_dict() == d


def test_fpx_validation():
    m = _fpx_model()
    with pytest.raises(ValueError, match="SDS"):
        FPX.compute(m, "EQX", SDS=0.0)
    m.fpx_settings = {"SDS": 0.5, "patterns": ["NOPE"]}
    with pytest.raises(ValueError, match="unknown load pattern"):
        m.validate()
    m.fpx_settings = {"SDS": 0.5, "bogus": 1}
    with pytest.raises(ValueError, match="unknown key"):
        m.validate()
