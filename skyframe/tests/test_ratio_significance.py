"""Ratio-based story checks: negligible-drift significance gate
(skyframe.core.ratio_significance; CONTRACT "Ratio-based story checks:
negligible-drift significance").

In a direction a case does not load, the ASCE 7 torsional ratio max/avg (and
V/Delta, Diaphragm Max Over Avg ratios) divides round-off by round-off.  Such
ratios are now ``None`` with ``na_reason[key] = "not applicable (negligible
drift)"``; loaded directions keep the legacy numbers.

Closed forms:
* single rigid-diaphragm story on four cantilever corner columns
  (k = 3EI/h^3 springs + GJ/h torsion): CR, K_theta and the edge
  displacements under Fx by hand -> the ASCE 7 ratio delta_max/delta_avg;
* under Fy the plan is symmetric about x, so the diaphragm does not rotate
  and the X direction is pure round-off -> n/a.
"""
import json
import math

import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core import ratio_significance as R
from skyframe.core import tables
from skyframe.core.builder import quick_building
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 PointSupport, StoryForce)

E = 25_000_000.0
NU = 0.2
NA = "not applicable (negligible drift)"


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
def _box(s_south=0.4, s_north=0.4, a=3.0, b=4.0, H=4.0, F=100.0):
    """One rigid-diaphragm story (plan 2a x 2b) on four cantilever corner
    columns, no beams: the y=-b pair is s_south square, the y=+b pair
    s_north.  EQX = Fx, EQY = Fy at the story."""
    m = BuildingModel(name="box")
    m.add_material(Material("C", E=E, nu=NU))
    m.set_stories([H])
    m.add_section(FrameSection.rectangular("CS", "C", s_south, s_south))
    m.add_section(FrameSection.rectangular("CN", "C", s_north, s_north))
    i = 0
    for x in (-a, a):
        for y in (-b, b):
            m.add_member("column", "CS" if y < 0 else "CN", (x, y, 0),
                         (x, y, H), story="Story1", uid=f"C{i}")
            m.supports.append(PointSupport((x, y, 0), (1, 1, 1, 1, 1, 1)))
            i += 1
    m.pattern("EQX", "quake").story_forces.append(StoryForce("Story1", fx=F))
    m.add_case("EQX", {"EQX": 1.0})
    m.pattern("EQY", "quake").story_forces.append(StoryForce("Story1", fy=F))
    m.add_case("EQY", {"EQY": 1.0})
    m.story_masses = {"Story1": 50.0}
    m.num_modes = 0
    return m


def _hand_fx(m, a=3.0, b=4.0, H=4.0, F=100.0):
    """Closed form under Fx at the plan centre (master): column x-springs
    k_i = 3 E I / H^3, torsion springs G J / H; y_CR = sum k y / sum k;
    K_theta = sum k (y - y_CR)^2 + sum k x^2 + sum GJ/H; theta = F y_CR /
    K_theta; edge displacements d(y) = F/K - theta (y - y_CR) at y = +/- b."""
    G = E / (2 * (1 + NU))
    ks, kt = {}, 0.0
    for mem in m.members:
        sec = m.sections[mem.section]
        ks[(mem.pi[0], mem.pi[1])] = 3 * E * sec.I22 / H ** 3
        kt += G * sec.J / H
    K = sum(ks.values())
    ycr = sum(k * y for (x, y), k in ks.items()) / K
    Kth = (sum(k * (y - ycr) ** 2 for (x, y), k in ks.items())
           + sum(k * x ** 2 for (x, y), k in ks.items()) + kt)
    theta = F * ycr / Kth
    d = [F / K - theta * (y - ycr) for y in (-b, b)]
    ratio = max(map(abs, d)) / (0.5 * (abs(d[0]) + abs(d[1])))
    return ratio, theta, d


def _run(m):
    return OpenSeesEngine(m).run().to_dict()


# --------------------------------------------------------------------------- #
# 1. the gate itself (pure functions)
# --------------------------------------------------------------------------- #
def test_threshold_formula():
    assert R.threshold(0.0) == 1e-9                  # absolute floor (m)
    assert R.threshold(1e-4) == 1e-9                 # 1e-6 * 1e-4 < 1e-9
    assert R.threshold(0.05) == pytest.approx(5e-8, rel=1e-15)
    assert R.threshold(-0.05) == R.threshold(0.05)
    story = {"S1": {"ux": 0.01, "uy": -0.03}, "S2": {"ux": 0.02, "uy": 1e-17}}
    assert R.case_reference(story) == 0.03
    assert R.case_threshold(story) == pytest.approx(3e-8, rel=1e-15)
    assert R.case_reference({}) == 0.0


def test_tors_ratio_gate_and_legacy_expression():
    legacy = engine_mod.OpenSeesEngine._tors_ratio
    thr = 1e-8
    # loaded: identical (same float) to the legacy expression
    for u, rz, B in ((0.01, 1e-4, 8.0), (-0.02, 3e-3, 6.0), (1e-7, 0, 5.0)):
        assert R.tors_ratio(u, rz, B, thr, legacy) == legacy(u, rz, B)
    # hand value: d = 0.01 +/- 1e-4*4 = 0.0104 / 0.0096 -> 0.0104 / 0.01
    assert R.tors_ratio(0.01, 1e-4, 8.0, thr, legacy) == \
        pytest.approx(1.04, rel=1e-12)
    # round-off in both terms -> n/a (legacy gave a random number)
    assert legacy(3e-16, -1e-16, 8.0) > 1.4
    assert R.tors_ratio(3e-16, -1e-16, 8.0, thr, legacy) is None
    # pure rotation, no translation: d1 = d2 -> 1.0 (genuine, not n/a)
    assert R.tors_ratio(0.0, 1e-4, 8.0, thr, legacy) == 1.0


def test_tors_flag_ignores_na():
    assert R.tors_flag((1.0, None)) == "none"
    assert R.tors_flag((None, 1.25)) == "torsional"
    assert R.tors_flag((1.41, 1.0)) == "extreme"
    assert R.tors_flag((None, None)) == "none"
    assert R.tors_flag((1.2, 1.0)) == "torsional"          # boundaries
    assert R.tors_flag((1.4, 1.0)) == "extreme"


def test_mark_and_clear_na():
    e = {"tors_ratio_x": 1.0, "tors_ratio_y": None}
    R.mark_na(e, "tors_ratio_y")
    assert e["na_reason"] == {"tors_ratio_y": NA}
    R.clear_na(e, "tors_ratio_y")
    assert "na_reason" not in e
    R.clear_na(e, "tors_ratio_x")                    # no-op without reason
    assert "na_reason" not in e


def test_story_stiffness_gate():
    thr = 1e-8
    assert R.story_stiffness(100.0, 0.002, thr) == 50000.0
    assert R.story_stiffness(100.0, -0.002, thr) == 50000.0
    assert R.story_stiffness(100.0, 5e-12, thr) == 0.0   # legacy 2e13
    assert R.story_stiffness(0.0, 0.002, thr) == 0.0


def _legacy_soft(idx, order, kp):
    """The pre-gate engine rule, verbatim."""
    entry = {}
    k_here = kp.get(order[idx], 0.0)
    k_above = kp.get(order[idx + 1]) if idx + 1 < len(order) else None
    above3 = [kp.get(n, 0.0) for n in order[idx + 1:idx + 4]]
    avg3 = (sum(above3) / len(above3)) if above3 else None
    soft_flag = ""
    if k_above and k_above > 1e-15:
        entry["stiff_ratio"] = k_here / k_above
        r = k_here / k_above
        ra = (k_here / avg3) if avg3 and avg3 > 1e-15 else None
        if r < 0.6 or (ra is not None and ra < 0.7):
            soft_flag = "extreme_soft"
        elif r < 0.7 or (ra is not None and ra < 0.8):
            soft_flag = "soft"
    else:
        entry["stiff_ratio"] = None
    entry["soft_flag"] = soft_flag
    return entry


@pytest.mark.parametrize("ks", [
    (100.0, 160.0, 150.0, 140.0, 130.0),            # soft (r = 0.625)
    (50.0, 100.0, 100.0, 100.0),                    # extreme soft
    (100.0, 100.0, 100.0),                          # regular
    (100.0, 0.0, 120.0),                            # zero-shear story above
    (100.0,),                                       # single story
])
def test_soft_story_identical_to_legacy_without_negligible(ks):
    order = [f"S{i}" for i in range(len(ks))]
    kp = dict(zip(order, ks))
    for idx in range(len(order)):
        e = {}
        R.soft_story(e, idx, order, kp, set())
        assert e == _legacy_soft(idx, order, kp)
        assert list(e) == ["stiff_ratio", "soft_flag"]


def test_soft_story_negligible_story_is_na():
    order = ["S1", "S2", "S3", "S4"]
    kp = {"S1": 0.0, "S2": 100.0, "S3": 100.0, "S4": 100.0}
    # legacy: zero stiffness from a round-off drift -> ratio 0 -> extreme
    assert _legacy_soft(0, order, kp)["soft_flag"] == "extreme_soft"
    e = {}
    R.soft_story(e, 0, order, kp, {"S1"})
    assert e == {"stiff_ratio": None, "soft_flag": "",
                 "na_reason": {"stiff_ratio": NA}}
    # the story BELOW a negligible one is n/a too (no story-above stiffness)
    kp2 = {"S1": 100.0, "S2": 0.0, "S3": 100.0, "S4": 100.0}
    e = {}
    R.soft_story(e, 0, order, kp2, {"S2"})
    assert e["stiff_ratio"] is None and e["na_reason"] == {"stiff_ratio": NA}
    # a negligible story drops out of the 3-story average only:
    # S1 vs (S2, S4) average: 65 / 100 -> ra 0.65 < 0.7 -> extreme soft
    kp3 = {"S1": 65.0, "S2": 100.0, "S3": 1e12, "S4": 100.0}
    e = {}
    R.soft_story(e, 0, order, kp3, {"S3"})
    assert e["stiff_ratio"] == pytest.approx(0.65)
    assert e["soft_flag"] == "extreme_soft" and "na_reason" not in e


# --------------------------------------------------------------------------- #
# 2. doubly symmetric plan under X: no Y flag, deterministically
# --------------------------------------------------------------------------- #
def _sym():
    return quick_building(stories=3)            # 3 x 2 bays: doubly symmetric


def _diag(d):
    return json.dumps({"irregularity": d["irregularity"],
                       "story_stiffness": d["story_stiffness"]},
                      sort_keys=True)


@pytest.fixture(scope="module")
def sym_runs():
    out = {}
    for solver in ("auto", "band", "umfpack"):
        for rep in range(2):
            m = _sym()
            if solver != "auto":
                m.analysis_options = {"solver": solver}
                m.validate()
            out[(solver, rep)] = _run(m)
    return out


def test_symmetric_x_load_has_no_y_irregularity(sym_runs):
    for key, d in sym_runs.items():
        for case, other in (("EQX", "y"), ("EQY", "x")):
            loaded = "x" if other == "y" else "y"
            for s, e in d["irregularity"][case].items():
                assert e[f"tors_ratio_{other}"] is None, (key, case, s)
                assert e["na_reason"][f"tors_ratio_{other}"] == NA
                # loaded direction: symmetric -> no rotation -> 1.0
                assert e[f"tors_ratio_{loaded}"] == pytest.approx(1.0,
                                                                  abs=1e-9)
                assert e["flag"] == "none", (key, case, s)
                k = d["story_stiffness"][case][s]
                assert k[f"k{other}"] == 0.0
                assert k["na_reason"] == {f"k{other}": NA}
                assert k[f"k{loaded}"] > 0.0
        # seismic combos: the Y (resp. X) direction is round-off too
        for c, by in d["irregularity"].items():
            for s, e in by.items():
                assert e["flag"] == "none", (key, c, s)


def _skeleton(d):
    """Diagnostics with every float replaced by a marker: n/a slots
    (None), na_reason strings and flags must match EXACTLY."""
    def rec(o):
        if isinstance(o, dict):
            return {k: rec(v) for k, v in o.items()}
        if isinstance(o, float):
            return "<float>"
        return o
    return rec({"irregularity": d["irregularity"],
                "story_stiffness": d["story_stiffness"]})


def _floats(d):
    out = []

    def rec(o, p):
        if isinstance(o, dict):
            for k in sorted(o):
                rec(o[k], p + (k,))
        elif isinstance(o, float):
            out.append((p, o))
    rec({"i": d["irregularity"], "k": d["story_stiffness"]}, ())
    return out


def test_symmetric_diagnostics_identical_across_runs_and_solvers(sym_runs):
    # repeated runs of one solver: byte-identical
    for solver in ("auto", "band", "umfpack"):
        assert _diag(sym_runs[(solver, 0)]) == _diag(sym_runs[(solver, 1)])
    # across solvers: identical n/a slots / reasons / flags; the loaded
    # numbers agree to solver round-off
    ref = sym_runs[("auto", 0)]
    for key, d in sym_runs.items():
        assert _skeleton(d) == _skeleton(ref), key
        for (pa, a), (pb, b) in zip(_floats(d), _floats(ref)):
            assert pa == pb
            assert a == pytest.approx(b, rel=1e-9, abs=1e-12), (key, pa)


def test_symmetric_loaded_soft_story_unchanged(sym_runs):
    """Loaded direction keeps the legacy soft-story numbers: k_i / k_{i+1}."""
    d = sym_runs[("auto", 0)]
    k = d["story_stiffness"]["EQX"]
    e = d["irregularity"]["EQX"]
    assert e["Story1"]["stiff_ratio"] == k["Story1"]["kx"] / k["Story2"]["kx"]
    assert e["Story3"]["stiff_ratio"] is None             # top: no story above
    assert "na_reason" not in e["Story3"] or \
        "stiff_ratio" not in e["Story3"]["na_reason"]


# --------------------------------------------------------------------------- #
# 3. genuinely torsional asymmetric plan: still flagged, hand ratio
# --------------------------------------------------------------------------- #
def test_eccentric_box_still_flagged_with_hand_ratio():
    m = _box(s_south=0.4, s_north=0.6)
    ratio, theta, dd = _hand_fx(m)
    assert 1.2 <= ratio < 1.4                        # Type 1a by hand
    d = _run(m)
    e = d["irregularity"]["EQX"]["Story1"]
    assert e["tors_ratio_x"] == pytest.approx(ratio, rel=1e-6)
    assert e["flag"] == "torsional"
    # the diaphragm genuinely rotates, so the Y ends move |theta| * a each
    # way: delta_max = delta_avg -> 1.0, applicable (not n/a)
    assert e["tors_ratio_y"] == pytest.approx(1.0, abs=1e-12)
    assert "na_reason" not in e
    # EQY: the plan is symmetric about x -> no rotation -> X is round-off
    ey = d["irregularity"]["EQY"]["Story1"]
    assert ey["tors_ratio_x"] is None
    assert ey["na_reason"] == {"tors_ratio_x": NA}
    assert ey["tors_ratio_y"] == pytest.approx(1.0, abs=1e-9)
    assert ey["flag"] == "none"


def test_eccentric_box_extreme_by_hand():
    """A stiffer north flank pushes the hand ratio past 1.4 -> Type 1b."""
    m = _box(s_south=0.3, s_north=0.7)
    ratio, _, _ = _hand_fx(m)
    assert ratio >= 1.4
    e = _run(m)["irregularity"]["EQX"]["Story1"]
    assert e["tors_ratio_x"] == pytest.approx(ratio, rel=1e-6)
    assert e["flag"] == "extreme"


# --------------------------------------------------------------------------- #
# 4. tables: Diaphragm Max Over Avg Drifts / Story Stiffness
# --------------------------------------------------------------------------- #
def test_table_ratio_na_on_round_off_row():
    """Inject a 1e-13 m round-off Y translation into the EQX master result:
    the Y row (it passes the legacy 1e-14 row filter) shows n/a ratios and
    the table warns; the loaded X row is unchanged."""
    m = _box(s_south=0.4, s_north=0.4)
    eng = OpenSeesEngine(m)
    d = eng.run().to_dict()
    ctx = tables.engine_context(eng)
    base = tables.compute_table("diaphragm_max_avg_drifts", d, m, ctx)
    assert not [r for r in base["rows"] if r["case"] == "EQX"
                and r["direction"] == "Y"]
    assert base["warnings"] == []
    mt = str(ctx["masters"]["Story1"])
    nd = d["cases"]["EQX"]["node_disp"][mt]
    nd[1] = 1e-13
    t = tables.compute_table("diaphragm_max_avg_drifts", d, m, ctx)
    y = [r for r in t["rows"] if r["case"] == "EQX" and r["direction"] == "Y"]
    assert len(y) == 1
    assert y[0]["disp_ratio"] is None and y[0]["drift_ratio"] is None
    assert y[0]["max_disp"] > 0.0                    # the raw values stay
    x = [r for r in t["rows"] if r["case"] == "EQX" and r["direction"] == "X"]
    x0 = [r for r in base["rows"] if r["case"] == "EQX"
          and r["direction"] == "X"]
    assert x[0]["disp_ratio"] == x0[0]["disp_ratio"]
    assert len(t["warnings"]) == 1 and NA in t["warnings"][0]
    assert set(y[0]) == {c["key"] for c in t["columns"]}


def test_story_stiffness_table_matches_engine_gate():
    m = _sym()
    eng = OpenSeesEngine(m)
    d = eng.run().to_dict()
    ctx = tables.engine_context(eng)
    rows = tables.compute_table("story_stiffness", d, m, ctx)["rows"]
    for r in rows:
        if r["case"] not in d["story_stiffness"]:
            continue
        k = d["story_stiffness"][r["case"]][r["story"]]
        assert r["stiff_x"] == pytest.approx(k["kx"], rel=1e-12)
        assert r["stiff_y"] == pytest.approx(k["ky"], rel=1e-12)


# --------------------------------------------------------------------------- #
# 5. named diaphragms (cases + RS rows) and towers
# --------------------------------------------------------------------------- #
def test_named_diaphragms_rows_and_story_level_na():
    from test_diaphragms_mass import _eq, _two
    from skyframe.core import codes
    m = _two(named=True, n_stories=2)
    _eq(m, fx=100.0)
    codes.make_rs_case_from_code(m, "RSX", "X", Ss=1.0, S1=0.4)
    eng = OpenSeesEngine(m)
    res = eng.run()
    d = res.to_dict()
    dia = d["diaphragms"]
    for sn, by in dia["cases"]["EQX"].items():
        for dn, r in by.items():
            assert r["tors_ratio_y"] is None, (sn, dn)
            assert r["na_reason"] == {"tors_ratio_y": NA}
            assert r["tors_ratio_x"] is not None
    # RS rows obey the same gate (case reference = the RS story peaks);
    # whatever is applicable is a genuine ~1.0 here, never a false flag
    thr = R.case_threshold(res.rs_cases["RSX"].story)
    for sn, by in dia["rs_cases"]["RSX"].items():
        for dn, r in by.items():
            gate = R.tors_avg(r["uy"], r["rz"],
                              dia["stories"][sn][dn]["extent_x"]) < thr
            assert (r["tors_ratio_y"] is None) == gate, (sn, dn)
            assert r["tors_ratio_y"] is None or r["tors_ratio_y"] < 1.2
            assert r["tors_ratio_x"] == pytest.approx(1.0, abs=1e-9)
    for sn, e in d["irregularity"]["EQX"].items():
        assert e["tors_ratio_y"] is None
        assert e["na_reason"] == {"tors_ratio_y": NA}
        assert e["flag"] == "none"                 # legacy: S1 "extreme"
        assert e["tors_ratio_x"] == max(
            r["tors_ratio_x"] for r in dia["cases"]["EQX"][sn].values())


def test_towers_x_only_load_y_na_per_tower():
    from test_towers import _twin
    m = _twin()
    for sf in m.patterns["EQX"].story_forces:
        sf.fy = 0.0
    d = _run(m)
    irr = d["irregularity"]["EQX"]
    assert set(irr) == {"A1", "A2", "A3", "B1", "B2", "B3"}
    for s, e in irr.items():
        assert e["tors_ratio_y"] is None, s
        assert e["na_reason"]["tors_ratio_y"] == NA
        assert e["tors_ratio_x"] == pytest.approx(1.0, abs=1e-9)
        assert d["story_stiffness"]["EQX"][s]["ky"] == 0.0
    # twin identical towers: identical per-tower entries
    for i in (1, 2, 3):
        assert irr[f"A{i}"] == irr[f"B{i}"] or all(
            (irr[f"A{i}"][k] == irr[f"B{i}"][k]) or
            (isinstance(irr[f"A{i}"][k], float) and math.isclose(
                irr[f"A{i}"][k], irr[f"B{i}"][k], rel_tol=1e-8))
            for k in irr[f"A{i}"])


def test_loaded_twin_towers_have_no_na():
    """Both directions loaded (fy = 0.3 fx): no ratio is gated."""
    from test_towers import _twin
    d = _run(_twin())
    for c, by in d["irregularity"].items():
        for s, e in by.items():
            assert "na_reason" not in e, (c, s)
            assert e["tors_ratio_x"] is not None
            assert e["tors_ratio_y"] is not None
