"""Extended load combinations (ETABS Define > Load Combinations parity).

CONTRACT "Load combinations: RS/TH/nested members and ABS/SRSS/Range types".

Every expected number is computed in the test from the engine's OWN
per-case outputs (static cases, the RS case, the TH series, the staged
final state) with the documented hand rules:

* 1.2D + 1.0E(RS):  max = 1.2D + |E|,  min = 1.2D - |E|   (per quantity)
* abs:   +/- sum |f_i x_i|
* srss:  +/- sqrt(sum (f_i x_i)^2)
* range: max = sum of positive f_i x_i, min = sum of negative f_i x_i
* TH member: [f*min(series), f*max(series)] added to the other members

plus closed-form anchors (RS base shear of a single-mass cantilever under
a flat spectrum = m * Sa * g; cantilever statics for the dead load).
"""

import copy
import json
import math

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core import combos_ext as cx
from skyframe.core.builder import quick_building
from skyframe.core.checks import check_model
from skyframe.core.model import (
    G_ACCEL,
    BuildingModel,
    FrameSection,
    LoadCombo,
    Material,
    NodalLoad,
    NodalMass,
    PointSupport,
)

M_TIP = 10.0          # tonne at the tip (ux, uy)
SA = 0.4              # flat spectrum, g
H = 3.0               # story height (2 stories)
STRUCT = ("node_disp", "reactions", "member_forces")


# --------------------------------------------------------------------------- #
# model + helpers
# --------------------------------------------------------------------------- #
def _cantilever(th=False, staged=False) -> BuildingModel:
    """2-story cantilever column, single tip mass (exact 1-DOF per axis)."""
    m = BuildingModel(name="combo-ext")
    m.rigid_diaphragms = False
    m.num_modes = 2
    m.add_material(Material("S", E=2.0e8, nu=0.3))
    m.add_section(FrameSection.rectangular("C", "S", 0.3, 0.3))
    m.set_stories([H, H])
    m.add_member("column", "C", (0, 0, 0), (0, 0, H), story="Story1",
                 uid="C1")
    m.add_member("column", "C", (0, 0, H), (0, 0, 2 * H), story="Story2",
                 uid="C2")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.nodal_masses.append(NodalMass((0, 0, 2 * H), mx=M_TIP, my=M_TIP))
    m.pattern("DEAD", "dead").nodal_loads += [
        NodalLoad((0, 0, 2 * H), fz=-100.0, fx=2.0),
        NodalLoad((0, 0, H), fz=-80.0)]
    m.pattern("PX", "other").nodal_loads.append(
        NodalLoad((0, 0, 2 * H), fx=10.0))
    m.pattern("W", "wind").nodal_loads.append(
        NodalLoad((0, 0, H), fx=-7.0, fy=3.0))
    m.add_case("D", {"DEAD": 1.0})
    m.add_case("PX", {"PX": 1.0})
    m.add_case("W", {"W": 1.0})
    m.add_rs_case("RSX", "X", [[0.0, SA], [10.0, SA]], num_modes=2)
    if th:
        acc = [3.0 * math.sin(math.pi * k / 40) for k in range(41)]
        m.add_th_case("THX", "X", acc + [0.0] * 40, 0.01)
    if staged:
        m.add_staged_case("STG", pattern="DEAD")
    return m


def _run(m) -> dict:
    return OpenSeesEngine(m).run().to_dict()


def _flat(block: dict, fields=("node_disp", "reactions", "member_forces",
                                "base", "story", "member_stations")):
    """Flatten a case block into {path: float} (station x excluded)."""
    out = {}
    for f in fields:
        v = block.get(f) or {}
        if f in STRUCT:
            for k, arr in v.items():
                for i, x in enumerate(arr):
                    out[(f, k, i)] = x
        elif f == "base":
            for k, x in v.items():
                out[(f, k)] = x
        elif f == "story":
            for s, row in v.items():
                for k, x in row.items():
                    out[(f, s, k)] = x
        else:
            for u, st in v.items():
                for k, arr in st.items():
                    if k == "x":
                        continue
                    for i, x in enumerate(arr):
                        out[(f, u, k, i)] = x
    return out


def _assert_flat(got: dict, exp: dict, rel=1e-9, abs_=1e-9):
    assert set(got) == set(exp)
    for k in exp:
        assert got[k] == pytest.approx(exp[k], rel=rel, abs=abs_), k


# --------------------------------------------------------------------------- #
# 1. validation
# --------------------------------------------------------------------------- #
def test_combo_types_and_bad_type_message():
    m = _cantilever()
    for t in ("add", "envelope", "abs", "srss", "range"):
        m.add_combo("C_" + t, {"D": 1.0}, combo_type=t)
    with pytest.raises(ValueError, match="combo_type must be "
                       "add|envelope|abs|srss|range"):
        m.add_combo("BAD", {"D": 1.0}, combo_type="sum")
    for t in ("abs", "srss", "range"):
        with pytest.raises(ValueError, match="at least one case"):
            m.add_combo("E_" + t, {}, combo_type=t)


def test_member_kinds_accepted():
    m = _cantilever(th=True, staged=True)
    m.add_rs_case("RSY", "Y", [[0.0, SA], [10.0, SA]], num_modes=2)
    m.add_rs_combo("RSXY", "RSX", "RSY")
    m.add_combo("L", {"D": 1.0})
    m.add_combo("ALL", {"D": 1.0, "RSX": 1.0, "RSXY": 0.5, "THX": 1.0,
                        "STG": 1.0, "L": 1.0})
    kinds = {c: cx.member_kind(m, c) for c in m.combos["ALL"].cases}
    assert kinds == {"D": "static", "RSX": "response_spectrum",
                     "RSXY": "rs_directional", "THX": "time_history",
                     "STG": "staged", "L": "combo"}


def test_pushover_member_rejected():
    m = _cantilever()
    m.add_pushover_case("PO", "X", default_My=50.0)
    with pytest.raises(ValueError, match="pushover case 'PO' cannot enter "
                       "a load combo"):
        m.add_combo("BAD", {"D": 1.0, "PO": 1.0})


def test_buckling_and_modal_members_rejected():
    m = _cantilever()
    m.add_buckling_case("BK", {"DEAD": 1.0})
    with pytest.raises(ValueError, match="buckling case 'BK'"):
        m.add_combo("BAD", {"BK": 1.0})
    with pytest.raises(ValueError, match="modal case 'MODAL'"):
        m.add_combo("BAD2", {"MODAL": 1.0})
    with pytest.raises(ValueError, match="unknown case NOPE"):
        m.add_combo("BAD3", {"NOPE": 1.0})


def test_cycle_detection_error():
    m = _cantilever()
    m.add_combo("A", {"D": 1.0})
    m.add_combo("B", {"A": 1.0, "PX": 1.0})
    m.add_combo("C", {"B": 2.0})
    with pytest.raises(ValueError, match="circular combo reference "
                       "A -> C -> B -> A"):
        m.add_combo("A", {"D": 1.0, "C": 1.0})
    with pytest.raises(ValueError, match="cannot reference itself"):
        m.add_combo("SELF", {"SELF": 1.0})
    # the rejected redefinition left the model unchanged
    assert m.combos["A"].cases == {"D": 1.0}


def test_cycle_detection_on_load():
    m = _cantilever()
    m.add_combo("A", {"D": 1.0})
    m.add_combo("B", {"A": 1.0})
    d = m.to_dict()
    d["combos"]["A"]["cases"] = {"B": 1.0}
    with pytest.raises(ValueError, match="circular combo reference"):
        BuildingModel.from_dict(d)


def test_roundtrip_extended_combos():
    m = _cantilever(th=True, staged=True)
    m.add_combo("E", {"D": 1.2, "RSX": 1.0})
    m.add_combo("R", {"D": 1.0, "PX": -1.0}, combo_type="range")
    m.add_combo("N", {"E": 1.0, "THX": 0.5, "STG": 1.0}, combo_type="srss")
    d = m.to_dict()
    back = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert back.to_dict() == d
    assert back.combos["N"].combo_type == "srss"


def test_check_model_accepts_extended_members():
    m = _cantilever(th=True)
    m.add_combo("E", {"D": 1.2, "RSX": 1.0, "THX": 1.0})
    codes = {i["code"] for i in check_model(m)["issues"]}
    assert "COMBO_CASE_INVALID" not in codes
    # a pushover member slipped in without validation is still flagged
    m.add_pushover_case("PO", "X", default_My=50.0)
    m.combos["E"].cases["PO"] = 1.0
    codes = {i["code"] for i in check_model(m)["issues"]}
    assert "COMBO_CASE_INVALID" in codes


# --------------------------------------------------------------------------- #
# 2. RS members: max/min pairs
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def rs_run():
    m = _cantilever()
    m.add_combo("1.2D+E", {"D": 1.2, "RSX": 1.0})
    m.add_combo("1.2D-E", {"D": 1.2, "RSX": -1.0})
    m.add_combo("0.9D+1.5E", {"D": 0.9, "RSX": 1.5})
    m.add_combo("ENV", {"D": 1.2, "RSX": 1.0}, combo_type="envelope")
    m.add_combo("NEST", {"1.2D+E": 1.0, "PX": 1.0})
    m.add_combo("FLAT", {"D": 1.2, "RSX": 1.0, "PX": 1.0})
    return m, _run(m)


def test_rs_magnitude_hand_value(rs_run):
    """Single-mass cantilever, flat spectrum: base shear = m * Sa * g."""
    _, d = rs_run
    assert d["rs_cases"]["RSX"]["base"]["FX"] == pytest.approx(
        M_TIP * SA * G_ACCEL, rel=1e-9)


def test_rs_add_max_min_hand(rs_run):
    """1.2D + 1.0E(RS): max = 1.2D + E, min = 1.2D - E per quantity."""
    _, d = rs_run
    D = _flat(d["cases"]["D"])
    E = _flat(d["rs_cases"]["RSX"])
    cb = d["combos"]["1.2D+E"]
    assert "min" in cb
    _assert_flat(_flat(cb), {k: 1.2 * D[k] + abs(E[k]) for k in D})
    _assert_flat(_flat(cb["min"]), {k: 1.2 * D[k] - abs(E[k]) for k in D})
    # explicit hand numbers: base FX (D pushes -2 kN), base FZ (180 kN up)
    V = M_TIP * SA * G_ACCEL
    assert cb["base"]["FX"] == pytest.approx(-2.4 + V, rel=1e-9)
    assert cb["min"]["base"]["FX"] == pytest.approx(-2.4 - V, rel=1e-9)
    assert cb["base"]["FZ"] == pytest.approx(1.2 * 180.0, rel=1e-12)
    assert cb["min"]["base"]["FZ"] == pytest.approx(1.2 * 180.0, rel=1e-12)
    # base overturning MY of the RS part = V * 2H (single tip mass)
    assert cb["base"]["MY"] - cb["min"]["base"]["MY"] == pytest.approx(
        2 * V * 2 * H, rel=1e-9)


def test_rs_sign_lost(rs_run):
    """RS results are unsigned: -1.0E gives the same pair as +1.0E."""
    _, d = rs_run
    assert d["combos"]["1.2D-E"]["base"] == d["combos"]["1.2D+E"]["base"]
    assert (d["combos"]["1.2D-E"]["min"]["member_stations"]
            == d["combos"]["1.2D+E"]["min"]["member_stations"])


def test_rs_factor_scales_magnitude(rs_run):
    _, d = rs_run
    D = _flat(d["cases"]["D"])
    E = _flat(d["rs_cases"]["RSX"])
    cb = d["combos"]["0.9D+1.5E"]
    _assert_flat(_flat(cb), {k: 0.9 * D[k] + 1.5 * abs(E[k]) for k in D})
    _assert_flat(_flat(cb["min"]),
                 {k: 0.9 * D[k] - 1.5 * abs(E[k]) for k in D})


def test_rs_envelope_combo(rs_run):
    _, d = rs_run
    D = _flat(d["cases"]["D"])
    E = _flat(d["rs_cases"]["RSX"])
    cb = d["combos"]["ENV"]
    _assert_flat(_flat(cb), {k: max(1.2 * D[k], abs(E[k])) for k in D})
    _assert_flat(_flat(cb["min"]), {k: min(1.2 * D[k], -abs(E[k]))
                                    for k in D})


def test_nested_rs_combo_equals_flattened(rs_run):
    _, d = rs_run
    a, b = d["combos"]["NEST"], d["combos"]["FLAT"]
    _assert_flat(_flat(a), _flat(b), rel=1e-12, abs_=1e-12)
    _assert_flat(_flat(a["min"]), _flat(b["min"]), rel=1e-12, abs_=1e-12)


def test_max_min_shape_and_consumers(rs_run):
    """max/min combos use the envelope-combo shape and are skipped by the
    linear-state consumers (takedown, deflection checks, diagnostics)."""
    _, d = rs_run
    cb = d["combos"]["1.2D+E"]
    assert set(cb["min"]) >= {"node_disp", "reactions", "base",
                              "member_forces", "story", "member_stations"}
    assert "shell_forces" not in cb
    assert cb["member_deflections"] == {}
    for blk in ("takedown", "deflection_checks"):
        assert "1.2D+E" not in d[blk]
        assert "ENV" not in d[blk]
    assert d["combo_status"]["1.2D+E"] == "finished"
    assert list(d["combos"]) == ["1.2D+E", "1.2D-E", "0.9D+1.5E", "ENV",
                                 "NEST", "FLAT"]


def test_rs_directional_member():
    m = _cantilever()
    m.add_rs_case("RSY", "Y", [[0.0, SA], [10.0, SA]], num_modes=2)
    m.add_rs_combo("RSXY", "RSX", "RSY", method="SRSS")
    m.add_combo("C", {"D": 1.0, "RSXY": 1.0})
    d = _run(m)
    D = _flat(d["cases"]["D"])
    E = _flat(d["rs_cases"]["RSXY"])
    _assert_flat(_flat(d["combos"]["C"]), {k: D[k] + abs(E[k]) for k in D})
    _assert_flat(_flat(d["combos"]["C"]["min"]),
                 {k: D[k] - abs(E[k]) for k in D})


# --------------------------------------------------------------------------- #
# 3. ABS / SRSS / Range on signed static cases
# --------------------------------------------------------------------------- #
FAC = {"D": 1.0, "PX": -2.0, "W": 1.5}


@pytest.fixture(scope="module")
def rule_run():
    m = _cantilever()
    for t in ("abs", "srss", "range"):
        m.add_combo(t.upper(), dict(FAC), combo_type=t)
    m.add_combo("ADD", dict(FAC))
    return m, _run(m)


def _parts(d):
    return {c: _flat(d["cases"][c]) for c in FAC}


def test_abs_rule(rule_run):
    _, d = rule_run
    P = _parts(d)
    keys = P["D"].keys()
    exp = {k: sum(abs(f * P[c][k]) for c, f in FAC.items()) for k in keys}
    cb = d["combos"]["ABS"]
    _assert_flat(_flat(cb), exp)
    _assert_flat(_flat(cb["min"]), {k: -v for k, v in exp.items()})
    # hand: base FX = |-2| + |-2*10| + |1.5*7| = 32.5 kN
    assert cb["base"]["FX"] == pytest.approx(2.0 + 20.0 + 10.5, rel=1e-12)


def test_srss_rule(rule_run):
    _, d = rule_run
    P = _parts(d)
    exp = {k: math.sqrt(sum((f * P[c][k]) ** 2 for c, f in FAC.items()))
           for k in P["D"]}
    cb = d["combos"]["SRSS"]
    _assert_flat(_flat(cb), exp)
    _assert_flat(_flat(cb["min"]), {k: -v for k, v in exp.items()})
    assert cb["base"]["FX"] == pytest.approx(
        math.sqrt(2.0 ** 2 + 20.0 ** 2 + 10.5 ** 2), rel=1e-12)


def test_range_rule(rule_run):
    _, d = rule_run
    P = _parts(d)
    hi = {k: sum(max(f * P[c][k], 0.0) for c, f in FAC.items())
          for k in P["D"]}
    lo = {k: sum(min(f * P[c][k], 0.0) for c, f in FAC.items())
          for k in P["D"]}
    cb = d["combos"]["RANGE"]
    _assert_flat(_flat(cb), hi)
    _assert_flat(_flat(cb["min"]), lo)
    # hand: base FX contributions D:-2, PX:-2*(-10)=+20, W:1.5*(+7)=+10.5
    assert cb["base"]["FX"] == pytest.approx(30.5, rel=1e-12)
    assert cb["min"]["base"]["FX"] == pytest.approx(-2.0, rel=1e-12)


def test_rules_bracket_linear_add(rule_run):
    """min <= linear add <= max for range and abs (sanity of the rules)."""
    _, d = rule_run
    add = _flat(d["combos"]["ADD"])
    assert "min" not in d["combos"]["ADD"]
    for t in ("ABS", "RANGE"):
        hi = _flat(d["combos"][t])
        lo = _flat(d["combos"][t]["min"])
        for k, v in add.items():
            assert lo[k] - 1e-9 <= v <= hi[k] + 1e-9


# --------------------------------------------------------------------------- #
# 4. nesting
# --------------------------------------------------------------------------- #
def test_nested_static_combo_bit_identical_to_flat():
    m = _cantilever()
    m.add_combo("INNER", {"D": 1.2, "PX": 1.0})
    m.add_combo("OUTER", {"INNER": 1.0, "W": 1.6})
    m.add_combo("FLAT", {"D": 1.2, "PX": 1.0, "W": 1.6})
    d = _run(m)
    assert d["combos"]["OUTER"] == d["combos"]["FLAT"]
    assert "min" not in d["combos"]["OUTER"]
    # single-valued nested combos feed the linear-state consumers
    assert d["takedown"]["OUTER"] == d["takedown"]["FLAT"]
    assert d["deflection_checks"]["OUTER"] == d["deflection_checks"]["FLAT"]


def test_nested_scaled_duplicate_members():
    m = _cantilever()
    m.add_combo("INNER", {"D": 1.0, "PX": 1.0})
    m.add_combo("OUTER", {"INNER": 1.5, "D": 0.5, "W": -1.0})
    d = _run(m)
    P = {c: _flat(d["cases"][c]) for c in ("D", "PX", "W")}
    exp = {k: 2.0 * P["D"][k] + 1.5 * P["PX"][k] - P["W"][k]
           for k in P["D"]}
    _assert_flat(_flat(d["combos"]["OUTER"]), exp)
    assert cx.leaf_factors(m, "OUTER") == {"D": 2.0, "PX": 1.5, "W": -1.0}


def test_nested_envelope_inside_add():
    m = _cantilever()
    m.add_combo("ENV", {"PX": 1.0, "W": 1.0}, combo_type="envelope")
    m.add_combo("C", {"ENV": -2.0, "D": 1.0})
    d = _run(m)
    D, PX, W = (_flat(d["cases"][c]) for c in ("D", "PX", "W"))
    hi = {k: D[k] + max(-2 * max(PX[k], W[k]), -2 * min(PX[k], W[k]))
          for k in D}
    lo = {k: D[k] + min(-2 * max(PX[k], W[k]), -2 * min(PX[k], W[k]))
          for k in D}
    _assert_flat(_flat(d["combos"]["C"]), hi)
    _assert_flat(_flat(d["combos"]["C"]["min"]), lo)


def test_nested_srss_of_abs():
    m = _cantilever()
    m.add_combo("A", {"D": 1.0, "PX": 1.0}, combo_type="abs")
    m.add_combo("S", {"A": 1.0, "W": 2.0}, combo_type="srss")
    d = _run(m)
    D, PX, W = (_flat(d["cases"][c]) for c in ("D", "PX", "W"))
    exp = {k: math.hypot(abs(D[k]) + abs(PX[k]), 2 * W[k]) for k in D}
    _assert_flat(_flat(d["combos"]["S"]), exp)


# --------------------------------------------------------------------------- #
# 5. time history + staged members
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def th_run():
    m = _cantilever(th=True, staged=True)
    m.add_combo("D+TH", {"D": 1.0, "THX": 1.0})
    m.add_combo("D-0.5TH", {"D": 1.0, "THX": -0.5})
    m.add_combo("STG+PX", {"STG": 1.0, "PX": 1.0})
    m.add_combo("NSTG", {"STG+PX": 2.0, "W": 1.0})
    m.add_combo("STG+E", {"STG": 1.0, "RSX": 1.0})
    eng = OpenSeesEngine(m)
    res = eng.run()
    return m, res.to_dict(), eng


def test_th_envelope_combo(th_run):
    """D + TH: per story/base quantity [D + min(series), D + max(series)]."""
    _, d, _ = th_run
    th = d["th_cases"]["THX"]
    D = d["cases"]["D"]
    cb = d["combos"]["D+TH"]
    for s in ("Story1", "Story2"):
        ux = np.array(th["story_ux"][s])
        assert cb["story"][s]["ux"] == pytest.approx(
            D["story"][s]["ux"] + ux.max(), rel=1e-12)
        assert cb["min"]["story"][s]["ux"] == pytest.approx(
            D["story"][s]["ux"] + ux.min(), rel=1e-12)
        pk = th["peaks"]["story"][s]["shear_x"]
        assert cb["story"][s]["shear_x"] == pytest.approx(
            D["story"][s]["shear_x"] + pk, rel=1e-12)
        assert cb["min"]["story"][s]["shear_x"] == pytest.approx(
            D["story"][s]["shear_x"] - pk, rel=1e-12)
    # drift of Story2 from the signed series
    dr = (np.array(th["story_ux"]["Story2"])
          - np.array(th["story_ux"]["Story1"])) / H
    assert cb["story"]["Story2"]["drift_x"] == pytest.approx(
        D["story"]["Story2"]["drift_x"] + dr.max(), rel=1e-12)
    bfx = np.array(th["base_FX"])
    assert cb["base"]["FX"] == pytest.approx(D["base"]["FX"] + bfx.max(),
                                             rel=1e-12)
    assert cb["min"]["base"]["FX"] == pytest.approx(
        D["base"]["FX"] + bfx.min(), rel=1e-12)
    # peaks are consistent with the max/min envelope
    assert max(bfx.max(), -bfx.min()) == pytest.approx(
        th["peaks"]["base"]["FX"], rel=1e-12)


def test_th_unrecorded_quantities_omitted(th_run):
    _, d, _ = th_run
    cb = d["combos"]["D+TH"]
    for f in STRUCT + ("member_stations",):
        assert cb[f] == {} and cb["min"][f] == {}
    assert set(cb["base"]) == {"FX", "FY"}
    assert "time-history" in cb["warning"]


def test_th_negative_factor(th_run):
    _, d, _ = th_run
    th = np.array(d["th_cases"]["THX"]["base_FX"])
    D = d["cases"]["D"]["base"]["FX"]
    cb = d["combos"]["D-0.5TH"]
    assert cb["base"]["FX"] == pytest.approx(D - 0.5 * th.min(), rel=1e-12)
    assert cb["min"]["base"]["FX"] == pytest.approx(D - 0.5 * th.max(),
                                                    rel=1e-12)


def test_staged_final_state_included(th_run):
    _, d, _ = th_run
    S = _flat(d["staged"]["STG"])
    PX = _flat(d["cases"]["PX"])
    cb = d["combos"]["STG+PX"]
    assert "min" not in cb                       # signed + signed
    _assert_flat(_flat(cb), {k: S[k] + PX[k] for k in S})
    # hand: staged base FZ = total dead = 180 kN
    assert cb["base"]["FZ"] == pytest.approx(180.0, rel=1e-9)
    nst = d["combos"]["NSTG"]
    W = _flat(d["cases"]["W"])
    _assert_flat(_flat(nst), {k: 2 * (S[k] + PX[k]) + W[k] for k in S})
    # staged member: not a pure static-pattern state -> no takedown
    assert "STG+PX" not in d["takedown"]
    assert "STG+PX" in d["section_cuts"] or not d["section_cuts"]


def test_staged_plus_rs(th_run):
    _, d, _ = th_run
    S = _flat(d["staged"]["STG"])
    E = _flat(d["rs_cases"]["RSX"])
    cb = d["combos"]["STG+E"]
    _assert_flat(_flat(cb), {k: S[k] + abs(E[k]) for k in S})
    _assert_flat(_flat(cb["min"]), {k: S[k] - abs(E[k]) for k in S})


# --------------------------------------------------------------------------- #
# 6. run control
# --------------------------------------------------------------------------- #
def test_skipped_when_member_not_run():
    m = _cantilever(th=True)
    m.add_combo("E", {"D": 1.2, "RSX": 1.0})
    m.add_combo("T", {"D": 1.0, "THX": 1.0})
    m.add_combo("P", {"PX": 1.0, "W": 1.0})                 # legacy
    m.add_combo("NP", {"P": 1.0, "RSX": 1.0})               # nested legacy
    m.add_combo("NE", {"E": 1.0}, combo_type="abs")         # nested ext.
    m.add_combo("OK", {"D": 1.0, "W": 1.0}, combo_type="range")
    m.cases_not_run = ["RSX", "PX"]
    d = _run(m)
    st = d["combo_status"]
    assert st == {"E": "skipped", "T": "finished", "P": "skipped",
                  "NP": "skipped", "NE": "skipped", "OK": "finished"}
    assert set(d["combos"]) == {"T", "OK"}
    assert "combo 'E' skipped: member(s) ['RSX'] not run" in d["warning"]
    assert "combo 'NE' skipped: member(s) ['E'] not run" in d["warning"]


def test_skipped_when_th_not_run():
    m = _cantilever(th=True)
    m.add_combo("T", {"D": 1.0, "THX": 1.0})
    m.add_combo("NT", {"T": 1.0, "D": 1.0})
    m.cases_not_run = ["THX"]
    d = _run(m)
    assert d["combo_status"] == {"T": "skipped", "NT": "skipped"}
    assert d["combos"] == {}


# --------------------------------------------------------------------------- #
# 7. byte-identical defaults + downstream consumers
# --------------------------------------------------------------------------- #
def test_legacy_combos_byte_identical():
    """Legacy (static add/envelope) combos still use the original engine
    path and are unaffected by extended combos living alongside them."""
    base = quick_building(bays_x=1, bays_y=1, stories=2)
    base.add_combo("ENV", {"DEAD": 1.0, "EQX": 1.0}, combo_type="envelope")
    d0 = _run(base)
    eng = OpenSeesEngine(base)
    for n, cb in base.combos.items():
        assert cx.is_legacy(base, cb)
        assert eng._combine(n, cb).to_dict() == d0["combos"][n]
    ext = copy.deepcopy(base)
    ext.add_combo("X-ABS", {"DEAD": 1.0, "EQX": 1.0}, combo_type="abs")
    ext.add_combo("X-NEST", {"1.2D + 1.6L": 1.0, "EQY": 1.0})
    d1 = _run(ext)
    for key in d0:
        if key in ("combos", "combo_status", "takedown", "section_cuts",
                   "piers", "deflection_checks", "story_stiffness",
                   "irregularity"):
            for n, v in d0[key].items():
                assert d1[key][n] == v, (key, n)
        else:
            assert d1[key] == d0[key], key
    assert list(d1["combos"]) == list(base.combos) + ["X-ABS", "X-NEST"]


def test_tables_skip_max_min_combos(rs_run):
    from skyframe.core.tables import compute_table, list_tables
    m, d = rs_run
    keys = [t["key"] for t in list_tables()]
    seen = set()
    for key in keys:
        try:
            out = compute_table(key, d, m)
        except (ValueError, KeyError) as exc:      # table needs other data
            assert "1.2D+E" not in str(exc)
            continue
        for row in out.get("rows", []):
            seen.add(row.get("case"))
    assert "D" in seen                       # tables did produce rows
    assert not seen & set(d["combos"])       # every combo here is max/min


def test_tables_with_staged_and_th_combos(th_run):
    """Single-valued staged combos appear in the case tables, max/min (TH,
    RS) combos are skipped, and no table raises because of them."""
    from skyframe.core.tables import compute_table, list_tables
    m, d, _ = th_run
    seen = set()
    for t in list_tables():
        try:
            out = compute_table(t["key"], d, m)
        except (ValueError, KeyError) as exc:
            assert not any(n in str(exc) for n in d["combos"])
            continue
        for row in out.get("rows", []):
            seen.add(row.get("case"))
    assert {"STG+PX", "NSTG"} <= seen
    assert not seen & {"D+TH", "D-0.5TH", "STG+E"}


def test_live_reduction_leaves_max_min_combos():
    from skyframe.core.codes import reduce_live_demands
    m = _cantilever()
    m.pattern("LIVE", "live").nodal_loads.append(
        NodalLoad((0, 0, 2 * H), fz=-40.0))
    m.add_case("LIVE", {"LIVE": 1.0})
    m.add_combo("E", {"D": 1.2, "LIVE": 1.0, "RSX": 1.0})
    m.add_combo("I", {"D": 1.2, "LIVE": 1.6})
    m.add_combo("N", {"I": 1.0, "W": 1.0})
    d = _run(m)
    red = {"C1": {"R": 0.5}, "C2": {"R": 0.5}}
    out = reduce_live_demands(d, m, reduction=red)
    assert out["combos"]["E"] == d["combos"]["E"]           # unchanged
    # nested single-valued combo gets its flattened live factor 1.6
    lv = d["cases"]["LIVE"]["member_forces"]["C1"][0]
    assert out["combos"]["N"]["member_forces"]["C1"][0] == pytest.approx(
        d["combos"]["N"]["member_forces"]["C1"][0] - 0.5 * 1.6 * lv)
    assert out["combos"]["I"]["member_forces"]["C1"][0] == pytest.approx(
        out["combos"]["N"]["member_forces"]["C1"][0]
        - d["combos"]["N"]["member_forces"]["C1"][0]
        + d["combos"]["I"]["member_forces"]["C1"][0])


def test_single_valued_helper():
    m = _cantilever(th=True, staged=True)
    m.add_combo("A", {"D": 1.0})
    m.add_combo("B", {"A": 1.0, "STG": 1.0})
    m.add_combo("C", {"B": 1.0, "RSX": 1.0})
    m.add_combo("E", {"D": 1.0}, combo_type="envelope")
    m.add_combo("T", {"THX": 1.0})
    assert [cx.is_single_valued(m, n) for n in "ABCET"] == \
        [True, True, False, False, False]
    assert cx.static_pattern_factors(m, "A") == {"DEAD": 1.0}
    assert cx.static_pattern_factors(m, "B") is None
    assert set(cx.all_members(m, "C")) == {"RSX", "D", "STG"}
    assert isinstance(m.combos["A"], LoadCombo)
