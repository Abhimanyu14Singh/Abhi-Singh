"""Wave 18 backend tests (v0.17):

1. Vertical seismic component (ASCE 7-16 §12.4.2.2 / §12.4.2.3):
   ``asce7_combinations(..., SDS=...)`` folds Ev = 0.2*SDS*D into the
   SEISMIC combos' DEAD factor — hand-written expected dicts for SDS = 1.0
   ((1.2 + 0.2) = 1.4 and (0.9 - 0.2) = 0.7 exactly) and SDS = 0.5
   (1.3 / 0.8); ASD analogues ((1.0 + 0.14*SDS), (1.0 + 0.105*SDS),
   (0.6 - 0.14*SDS)); the no-SDS output is DICT-EQUAL to the pre-change
   combos; wind combos never change; bad SDS raises.
2. Panel zones (beam-column joint flexibility, model-level option):
   * "rigid": automatic rigid end zones — the exposed helper computes
     column offsets = max_beam_h/2 and beam offsets = col_h/2 (hand
     values); portal drift DECREASES vs "none" and matches a hand-built
     model with the equivalent EXPLICIT rigid_i/rigid_j offsets to 1e-9
     (same v0.9 machinery); the user's model is never mutated; user-set
     offsets win per member end.
   * "scissors": elastic panel spring K_theta = G*d_c*d_b*t_p — portal
     drift INCREASES vs "rigid" AND vs "none"; on an isolated joint
     (column fixed base + cantilever beam, tip load P) the spring rotation
     equals M_joint/K_theta = P*L_b/K to 1e-6; equilibrium holds with
     rigid diaphragms (base shear = applied story shear); support joints
     are skipped with a warning.
   * "none" default: zero change (the full pre-existing suite stays
     green); round-trip + validation of the enum.
3. API: POST /api/combos/asce7 accepts optional SDS (400 on bad);
   POST /api/model round-trips panel_zones (400 on a bad value);
   /api/analyze runs a panel-zone model.

Every expected number is hand-derived (ASCE 7-16 §12.4.2.3 combination
forms, K_theta = G*d_c*d_b*t_p, h/2 offsets), independent of the modules
under test.  Units per CONTRACT.md: kN, m, tonne, s; E in kPa.
"""

import math

import pytest

from skyframe.core.builder import quick_building
from skyframe.core.codes import apply_asce7_combinations, asce7_combinations
from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 NodalLoad, PointSupport)
from skyframe.engine.opensees_engine import (OpenSeesEngine,
                                             compute_panel_zone_offsets,
                                             compute_panel_zone_springs)

E_CONC = 25e6                                  # kPa
COL_B = COL_H = 0.5                            # column 0.5 x 0.5
BM_B, BM_H = 0.3, 0.6                          # beam 0.3 x 0.6
FIX = (1, 1, 1, 1, 1, 1)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _seismic_model(with_wind=False):
    """DEAD/LIVE/EQX/EQY single-pattern cases (matched by pattern kind)."""
    mdl = BuildingModel(name="combo-src")
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", COL_B, COL_H))
    mdl.set_stories([3.0])
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, 3.0),
                   story="Story1", uid="C1")
    for name, kind in (("DEAD", "dead"), ("LIVE", "live"),
                       ("EQX", "quake"), ("EQY", "quake")):
        mdl.pattern(name, kind)
        mdl.add_case(name, {name: 1.0})
    if with_wind:
        mdl.pattern("WX", "wind")
        mdl.add_case("WX", {"WX": 1.0})
    return mdl


def _expected_lrfd_no_sds():
    out = {"1.4D": {"DEAD": 1.4},
           "1.2D+1.6L": {"DEAD": 1.2, "LIVE": 1.6}}
    for q in ("EQX", "EQY"):
        for sgn, sl in ((1.0, "+"), (-1.0, "-")):
            out[f"1.2D+1.0L{sl}1.0{q}"] = {"DEAD": 1.2, "LIVE": 1.0,
                                           q: sgn}
            out[f"0.9D{sl}1.0{q}"] = {"DEAD": 0.9, q: sgn}
    return out


def _expected_asd_no_sds():
    out = {"D": {"DEAD": 1.0},
           "D+L": {"DEAD": 1.0, "LIVE": 1.0}}
    for q in ("EQX", "EQY"):
        for sgn, sl in ((1.0, "+"), (-1.0, "-")):
            out[f"D{sl}0.7{q}"] = {"DEAD": 1.0, q: sgn * 0.7}
            out[f"D+0.75L{sl}0.525{q}"] = {"DEAD": 1.0, "LIVE": 0.75,
                                           q: sgn * 0.525}
            out[f"0.6D{sl}0.7{q}"] = {"DEAD": 0.6, q: sgn * 0.7}
    return out


def _assert_combos_close(got, expected, tol=1e-12):
    """Same combo names, same case sets, factors equal to ``tol``."""
    assert set(got) == set(expected)
    for name, cases in expected.items():
        assert set(got[name]) == set(cases), name
        for case, f in cases.items():
            assert got[name][case] == pytest.approx(f, abs=tol), \
                (name, case)


# --------------------------------------------------------------------------- #
# 1. vertical seismic component — ASCE 7-16 §12.4.2.2 / §12.4.2.3
# --------------------------------------------------------------------------- #
def test_lrfd_no_sds_regression_dict_equal():
    """SDS omitted / None reproduces the pre-v0.17 output EXACTLY."""
    mdl = _seismic_model()
    assert asce7_combinations(mdl) == _expected_lrfd_no_sds()
    assert asce7_combinations(mdl, "LRFD", SDS=None) == \
        _expected_lrfd_no_sds()


def test_asd_no_sds_regression_dict_equal():
    mdl = _seismic_model()
    assert asce7_combinations(mdl, "ASD") == _expected_asd_no_sds()
    assert asce7_combinations(mdl, "ASD", SDS=None) == \
        _expected_asd_no_sds()


def test_lrfd_sds_one_hand_dict():
    """SDS = 1.0: (1.2 + 0.2*1.0) = 1.4 and (0.9 - 0.2*1.0) = 0.7 exactly
    (§12.4.2.3 combos 6/7, rho = 1)."""
    expected = {"1.4D": {"DEAD": 1.4},
                "1.2D+1.6L": {"DEAD": 1.2, "LIVE": 1.6}}
    for q in ("EQX", "EQY"):
        for sgn, sl in ((1.0, "+"), (-1.0, "-")):
            expected[f"1.4D+1.0L{sl}1.0{q}"] = {"DEAD": 1.4, "LIVE": 1.0,
                                                q: sgn}
            expected[f"0.7D{sl}1.0{q}"] = {"DEAD": 0.7, q: sgn}
    got = asce7_combinations(_seismic_model(), "LRFD", SDS=1.0)
    _assert_combos_close(got, expected)


def test_lrfd_sds_half_hand_dict():
    """SDS = 0.5: dead factors 1.2 + 0.1 = 1.3 and 0.9 - 0.1 = 0.8."""
    expected = {"1.4D": {"DEAD": 1.4},
                "1.2D+1.6L": {"DEAD": 1.2, "LIVE": 1.6}}
    for q in ("EQX", "EQY"):
        for sgn, sl in ((1.0, "+"), (-1.0, "-")):
            expected[f"1.3D+1.0L{sl}1.0{q}"] = {"DEAD": 1.3, "LIVE": 1.0,
                                                q: sgn}
            expected[f"0.8D{sl}1.0{q}"] = {"DEAD": 0.8, q: sgn}
    got = asce7_combinations(_seismic_model(), "LRFD", SDS=0.5)
    _assert_combos_close(got, expected)


def test_asd_sds_one_hand_dict():
    """SDS = 1.0 ASD (§2.4.5 / §12.4.2.3 combos 8/9/10):
    (1 + 0.14) = 1.14, (1 + 0.105) = 1.105, (0.6 - 0.14) = 0.46."""
    expected = {"D": {"DEAD": 1.0},
                "D+L": {"DEAD": 1.0, "LIVE": 1.0}}
    for q in ("EQX", "EQY"):
        for sgn, sl in ((1.0, "+"), (-1.0, "-")):
            expected[f"1.14D{sl}0.7{q}"] = {"DEAD": 1.14, q: sgn * 0.7}
            expected[f"1.105D+0.75L{sl}0.525{q}"] = {
                "DEAD": 1.105, "LIVE": 0.75, q: sgn * 0.525}
            expected[f"0.46D{sl}0.7{q}"] = {"DEAD": 0.46, q: sgn * 0.7}
    got = asce7_combinations(_seismic_model(), "ASD", SDS=1.0)
    _assert_combos_close(got, expected)


def test_asd_sds_half_hand_factors():
    """SDS = 0.5: 1.07 / 1.0525 / 0.53 on the seismic dead factors."""
    got = asce7_combinations(_seismic_model(), "ASD", SDS=0.5)
    assert got["1.07D+0.7EQX"]["DEAD"] == pytest.approx(1.07, abs=1e-12)
    assert got["1.0525D+0.75L+0.525EQX"]["DEAD"] == \
        pytest.approx(1.0525, abs=1e-12)
    assert got["0.53D-0.7EQX"]["DEAD"] == pytest.approx(0.53, abs=1e-12)
    assert got["0.53D-0.7EQX"]["EQX"] == pytest.approx(-0.7, abs=1e-12)


def test_sds_never_touches_wind_or_gravity_combos():
    """Ev is a SEISMIC vertical component: wind and gravity-only combos are
    identical with and without SDS (both standards)."""
    for std in ("LRFD", "ASD"):
        base = asce7_combinations(_seismic_model(with_wind=True), std)
        sds = asce7_combinations(_seismic_model(with_wind=True), std,
                                 SDS=1.0)
        for name, cases in base.items():
            if "EQ" in name:
                continue                     # seismic combos change by design
            assert sds[name] == cases, (std, name)
        # exactly the seismic combos were renamed/refactored, none dropped
        assert len(sds) == len(base)


def test_sds_zero_equals_no_sds():
    mdl = _seismic_model()
    assert asce7_combinations(mdl, "LRFD", SDS=0.0) == \
        asce7_combinations(mdl, "LRFD")
    assert asce7_combinations(mdl, "ASD", SDS=0.0) == \
        asce7_combinations(mdl, "ASD")


def test_bad_sds_raises():
    mdl = _seismic_model()
    for bad in (-0.1, float("nan"), float("inf"), True, "1.0"):
        with pytest.raises(ValueError, match="SDS"):
            asce7_combinations(mdl, "LRFD", SDS=bad)


def test_apply_asce7_combinations_with_sds_adds_to_model():
    mdl = _seismic_model()
    combos = apply_asce7_combinations(mdl, "LRFD", SDS=1.0)
    assert "1.4D+1.0L+1.0EQX" in mdl.combos
    assert mdl.combos["0.7D-1.0EQX"].cases["DEAD"] == \
        pytest.approx(0.7, abs=1e-12)
    assert set(combos) <= set(mdl.combos)


# --------------------------------------------------------------------------- #
# 2. panel zones
# --------------------------------------------------------------------------- #
def _portal(pz="none", explicit_offsets=False):
    """One-bay portal: 0.5x0.5 columns, 0.3x0.6 beam, 100 kN lateral."""
    mdl = BuildingModel(name="portal")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", COL_B, COL_H))
    mdl.add_section(FrameSection.rectangular("BM", "CONC", BM_B, BM_H))
    mdl.set_stories([3.0])
    ckw = dict(rigid_j=BM_H / 2.0) if explicit_offsets else {}
    for i, x in enumerate((0.0, 6.0)):
        mdl.add_member("column", "COL", (x, 0, 0), (x, 0, 3.0),
                       story="Story1", uid=f"C{i + 1}", **ckw)
        mdl.supports.append(PointSupport((x, 0, 0), FIX))
    bkw = (dict(rigid_i=COL_H / 2.0, rigid_j=COL_H / 2.0)
           if explicit_offsets else {})
    mdl.add_member("beam", "BM", (0, 0, 3.0), (6.0, 0, 3.0),
                   story="Story1", uid="B1", **bkw)
    mdl.pattern("H", "other").nodal_loads.append(
        NodalLoad((0, 0, 3.0), fx=100.0))
    mdl.add_case("H", {"H": 1.0})
    mdl.panel_zones = pz
    return mdl


def test_panel_zone_offset_helper_hand_values():
    """Columns get max_beam_h/2 = 0.3 at the joint (top) end; the beam gets
    col_h/2 = 0.25 at both ends; base ends stay 0 (no beam there)."""
    off = compute_panel_zone_offsets(_portal())
    assert off["C1"] == (0.0, BM_H / 2.0)          # (0, 0.3)
    assert off["C2"] == (0.0, BM_H / 2.0)
    assert off["B1"] == (COL_H / 2.0, COL_H / 2.0)  # (0.25, 0.25)
    assert off["C1"][1] == pytest.approx(0.3, abs=1e-15)
    assert off["B1"][0] == pytest.approx(0.25, abs=1e-15)


def test_panel_zone_offset_user_precedence_per_end():
    """A user-set explicit offset wins at ITS end only."""
    mdl = _portal()
    mdl.members[0].rigid_j = 0.1                   # C1 top: user says 0.1
    off = compute_panel_zone_offsets(mdl)
    assert off["C1"] == (0.0, 0.1)                 # user value kept
    assert off["C2"] == (0.0, BM_H / 2.0)          # computed elsewhere
    assert off["B1"] == (COL_H / 2.0, COL_H / 2.0)


def test_panel_zone_rigid_matches_explicit_offsets_exactly():
    """"rigid" == a hand-built model with equivalent explicit rigid_i/j —
    same v0.9 machinery, so displacements match to 1e-9 (measured: exact)."""
    eng_auto = OpenSeesEngine(_portal("rigid"))
    eng_hand = OpenSeesEngine(_portal("none", explicit_offsets=True))
    r_auto = eng_auto.run_static("H")
    r_hand = eng_hand.run_static("H")
    assert r_auto.story["Story1"]["ux"] == pytest.approx(
        r_hand.story["Story1"]["ux"], abs=1e-9)
    for tag, d in r_hand.node_disp.items():
        for k in range(6):
            assert r_auto.node_disp[tag][k] == pytest.approx(d[k],
                                                             abs=1e-9)
    for uid, f in r_hand.member_forces.items():
        for k in range(12):
            assert r_auto.member_forces[uid][k] == pytest.approx(f[k],
                                                                 abs=1e-9)


def test_panel_zone_rigid_drift_decreases_and_model_not_mutated():
    mdl_rigid = _portal("rigid")
    d_none = OpenSeesEngine(_portal("none")).run_static("H") \
        .story["Story1"]["ux"]
    d_rigid = OpenSeesEngine(mdl_rigid).run_static("H") \
        .story["Story1"]["ux"]
    assert d_rigid < d_none                        # stiffer joints
    assert d_rigid > 0.5 * d_none                  # sane magnitude
    # the engine NEVER mutates the user's model
    for m in mdl_rigid.members:
        assert m.rigid_i == 0.0 and m.rigid_j == 0.0


def test_scissors_drift_increases_vs_rigid_and_none():
    d_none = OpenSeesEngine(_portal("none")).run_static("H") \
        .story["Story1"]["ux"]
    d_rigid = OpenSeesEngine(_portal("rigid")).run_static("H") \
        .story["Story1"]["ux"]
    d_sci = OpenSeesEngine(_portal("scissors")).run_static("H") \
        .story["Story1"]["ux"]
    assert d_sci > d_rigid                # panel flexibility adds drift
    assert d_sci > d_none                 # softer than even centerline
    assert d_rigid < d_none


def test_scissors_spring_stiffness_helper_hand_value():
    """K_theta = G * d_c * d_b * t_p with G = E/(2(1+nu)), d_c = 0.5,
    d_b = 0.6, t_p = 0.5 (column b)."""
    springs = compute_panel_zone_springs(_portal())
    assert len(springs) == 2                       # both beam-column joints
    G = E_CONC / (2.0 * 1.2)
    K_hand = G * COL_H * BM_H * COL_B
    for spec in springs.values():
        assert spec["K"] == pytest.approx(K_hand, rel=1e-12)
        assert spec["d_c"] == COL_H and spec["d_b"] == BM_H
        assert spec["t_p"] == COL_B
        assert spec["G"] == pytest.approx(G, rel=1e-12)


def test_scissors_isolated_joint_rotation_equals_M_over_K():
    """Column fixed at base + cantilever beam, tip load P: the FULL beam
    moment M = P*L_b crosses the panel spring, so the orig<->dup relative
    rotation is exactly M/K_theta (1e-6)."""
    P, L_b = 10.0, 4.0
    mdl = BuildingModel(name="joint")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", COL_B, COL_H))
    mdl.add_section(FrameSection.rectangular("BM", "CONC", BM_B, BM_H))
    mdl.set_stories([3.0])
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, 3.0),
                   story="Story1", uid="C1")
    mdl.add_member("beam", "BM", (0, 0, 3.0), (L_b, 0, 3.0),
                   story="Story1", uid="B1")
    mdl.supports.append(PointSupport((0, 0, 0), FIX))
    mdl.pattern("P", "other").nodal_loads.append(
        NodalLoad((L_b, 0, 3.0), fz=-P))
    mdl.add_case("P", {"P": 1.0})
    mdl.panel_zones = "scissors"

    eng = OpenSeesEngine(mdl)
    res = eng.run_static("P")
    joints = eng.panel_zone_joints()
    assert len(joints) == 1
    j = joints[0]
    assert j["point"] == [0.0, 0.0, 3.0]
    K_hand = (E_CONC / 2.4) * COL_H * BM_H * COL_B
    assert j["K"] == pytest.approx(K_hand, rel=1e-12)

    d_orig = res.node_disp[j["orig"]]
    d_dup = res.node_disp[j["dup"]]
    # translations are tied rigid (equalDOF)
    for k in range(3):
        assert d_dup[k] == pytest.approx(d_orig[k], abs=1e-15)
    # spring rotation about global Y (the beam's bending plane) = M/K
    M_joint = P * L_b                              # 40 kN*m
    d_rot = abs(d_dup[4] - d_orig[4])
    assert d_rot == pytest.approx(M_joint / K_hand, rel=1e-6)
    # the beam end moment really is P*L_b (major-axis M3 at end i)
    assert abs(res.member_forces["B1"][5]) == pytest.approx(M_joint,
                                                            rel=1e-9)


def test_scissors_quick_building_equilibrium_and_drift():
    """Scissors + rigid diaphragms: base shear balances the applied story
    forces exactly and the roof drift exceeds the centerline model."""
    def run(pz):
        qb = quick_building(bays_x=1, bays_y=1, stories=2)
        qb.panel_zones = pz
        r = OpenSeesEngine(qb).run_static("EQX")
        return qb, r
    qb, r_sci = run("scissors")
    _, r_none = run("none")
    applied = sum(sf.fx for sf in qb.patterns["EQX"].story_forces)
    assert applied > 0.0
    assert -r_sci.base["FX"] == pytest.approx(applied, rel=1e-9)
    assert r_sci.story["Story2"]["ux"] > r_none.story["Story2"]["ux"]


def test_scissors_support_joint_skipped_with_warning():
    """A beam-column joint AT a support node is skipped (the equalDOF tie
    would hide the beam shear from the support reaction)."""
    mdl = BuildingModel(name="base-joint")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", COL_B, COL_H))
    mdl.add_section(FrameSection.rectangular("BM", "CONC", BM_B, BM_H))
    mdl.set_stories([3.0])
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, 3.0),
                   story="Story1", uid="C1")
    mdl.add_member("beam", "BM", (0, 0, 0), (4.0, 0, 0),
                   story="Story1", uid="B1")
    mdl.supports.append(PointSupport((0, 0, 0), FIX))
    mdl.supports.append(PointSupport((4.0, 0, 0), FIX))
    mdl.pattern("H", "other").nodal_loads.append(
        NodalLoad((0, 0, 3.0), fx=10.0))
    mdl.add_case("H", {"H": 1.0})
    mdl.panel_zones = "scissors"
    eng = OpenSeesEngine(mdl)
    with pytest.warns(UserWarning, match="support"):
        res = eng.run_static("H")
    assert eng.panel_zone_joints() == []
    assert -res.base["FX"] == pytest.approx(10.0, rel=1e-9)


def test_panel_zones_roundtrip_and_validation():
    mdl = _portal("scissors")
    back = BuildingModel.from_dict(mdl.to_dict())
    assert back.panel_zones == "scissors"
    d = mdl.to_dict()
    d["panel_zones"] = "rigid"
    assert BuildingModel.from_dict(d).panel_zones == "rigid"
    # pre-v0.17 file (no key) keeps the centerline default
    d.pop("panel_zones")
    assert BuildingModel.from_dict(d).panel_zones == "none"
    d["panel_zones"] = "krawinkler"
    with pytest.raises(ValueError, match="panel_zones"):
        BuildingModel.from_dict(d)
    assert BuildingModel().panel_zones == "none"   # default


def test_panel_zone_rigid_full_run_and_modal():
    """engine.run() (cases + combo + modal + diagnostics) works under
    "rigid" panel zones and reports a shorter fundamental period than the
    centerline model (stiffer joints)."""
    def run(pz):
        qb = quick_building(bays_x=1, bays_y=1, stories=1)
        qb.panel_zones = pz
        return OpenSeesEngine(qb).run()
    r_rigid = run("rigid")
    r_none = run("none")
    assert r_rigid.modal.periods[0] < r_none.modal.periods[0]
    assert r_rigid.cases["EQX"].story["Story1"]["ux"] < \
        r_none.cases["EQX"].story["Story1"]["ux"]
    # combos still superpose and balance
    assert "1.2D + 1.6L" in r_rigid.combos


# --------------------------------------------------------------------------- #
# 3. API
# --------------------------------------------------------------------------- #
@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_api_combos_asce7_accepts_sds(api_client):
    r = api_client.post("/api/model/quick",
                        json={"bays_x": 1, "bays_y": 1, "stories": 1})
    assert r.status_code == 200
    resp = api_client.post("/api/combos/asce7",
                           json={"standard": "LRFD", "SDS": 1.0})
    assert resp.status_code == 200
    combos = resp.get_json()["combos"]
    assert "1.4D+1.0L+1.0EQX" in combos
    assert combos["1.4D+1.0L+1.0EQX"]["cases"]["DEAD"] == \
        pytest.approx(1.4, abs=1e-12)
    assert combos["0.7D-1.0EQX"]["cases"]["DEAD"] == \
        pytest.approx(0.7, abs=1e-12)
    # no SDS: classic combos (regression through the endpoint)
    r2 = api_client.post("/api/model/quick",
                         json={"bays_x": 1, "bays_y": 1, "stories": 1})
    assert r2.status_code == 200
    resp2 = api_client.post("/api/combos/asce7", json={})
    assert resp2.status_code == 200
    assert "1.2D+1.0L+1.0EQX" in resp2.get_json()["combos"]


def test_api_combos_asce7_bad_sds_400(api_client):
    for bad in ("abc", -1.0, True):
        resp = api_client.post("/api/combos/asce7",
                               json={"standard": "LRFD", "SDS": bad})
        assert resp.status_code == 400
        assert "SDS" in resp.get_json()["error"]


def test_api_model_roundtrips_panel_zones(api_client):
    mdl = _portal("scissors")
    resp = api_client.post("/api/model", json=mdl.to_dict())
    assert resp.status_code == 200
    assert resp.get_json()["panel_zones"] == "scissors"
    assert api_client.get("/api/model").get_json()["panel_zones"] == \
        "scissors"
    bad = mdl.to_dict()
    bad["panel_zones"] = "bogus"
    resp2 = api_client.post("/api/model", json=bad)
    assert resp2.status_code == 400
    assert "panel_zones" in resp2.get_json()["error"]


def test_api_analyze_runs_panel_zone_model(api_client):
    mdl = _portal("rigid")
    assert api_client.post("/api/model",
                           json=mdl.to_dict()).status_code == 200
    resp = api_client.post("/api/analyze")
    assert resp.status_code == 200
    d = resp.get_json()
    assert "H" in d["cases"]
    assert d["cases"]["H"]["story"]["Story1"]["ux"] > 0.0
